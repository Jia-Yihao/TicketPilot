from __future__ import annotations

from typing import Any

from app.core.errors import ValidationAppError
from app.models.enums import RefundReason, RefundType, RiskLevel, VerificationStatus
from app.retrieval.search import PolicySearch
from app.services.orders import OrderService
from app.services.refund_policy import RefundPolicy
from app.services.refunds import (
    RefundExecutionService,
    refund_reason_label,
    refund_type_label,
)
from app.services.tickets import TicketService
from app.tools.base import (
    DegradationStep,
    RetryPolicy,
    ToolContext,
    ToolManifest,
    ToolResult,
)
from app.tools.registry import ToolRegistry

_ORDER_ID = {
    "type": "string",
    "pattern": "^ORD-[A-Za-z0-9-]+$",
    "minLength": 8,
    "maxLength": 40,
    "hint": "订单号应为 ORD-… 形式，例如 ORD-DEMO-1001",
}
# 退款原因 / 类型：入参键名一律带上下文（口径 §5.6.4），值是枚举的值字符串。
# 两个键都是可选的：类型缺值时由服务端派生，不由调用方追问（口径 §5.6.6）。
_REFUND_REASON = {
    "type": "string",
    "enum": [member.value for member in RefundReason],
    "hint": "退款原因：QUALITY 质量问题 / DAMAGED 破损 / WRONG_ITEM 发错商品 / "
    "NOT_AS_DESCRIBED 与描述不符 / NO_LONGER_NEEDED 不再需要 / OTHER 其他",
}
_REFUND_TYPE = {
    "type": "string",
    "enum": [member.value for member in RefundType],
    "hint": "退款类型：FULL 全额 / PARTIAL 部分 / SHIPPING 运费 / PRICE_PROTECTION 价保",
}


def build_registry(
    *,
    orders: OrderService,
    policies: PolicySearch,
    tickets: TicketService,
    refunds: RefundExecutionService,
    policy: RefundPolicy,
) -> ToolRegistry:
    """装配内建工具：处理器 + 清单，一次装成一个注册表。

    ``orders`` / ``policies`` / ``tickets`` / ``refunds`` 由调用方按当前会话
    现造，因此注册表天然带着这次调用者的可见范围与事务边界；本函数不碰
    数据库，也不缓存任何东西——每次进任务都重新装配一遍。
    """
    registry = ToolRegistry()

    # --- 只读处理器：订单 / 物流 / 政策检索，全部走调用者自己的可见范围 ---
    def get_order(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        order = orders.require_order(ctx.identity, _str_arg(args, "order_id"))
        return ToolResult(
            ok=True,
            data=orders.order_view(order),
            message=orders.order_summary(order),
        )

    def get_logistics(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        order = orders.require_order(ctx.identity, _str_arg(args, "order_id"))
        logistics = orders.get_logistics(ctx.identity, order.id)
        return ToolResult(
            ok=True,
            data=orders.logistics_view(order, logistics),
            message=orders.logistics_summary(order, logistics),
        )

    def search_policy(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        query = _str_arg(args, "query")
        results = policies.search(ctx.identity, query)
        return ToolResult(
            ok=True,
            data={
                "count": len(results),
                "results": [
                    {**item.citation(), "content": item.content} for item in results
                ],
            },
            message=f"检索到 {len(results)} 个相关政策片段。",
        )

    # --- 写处理器：建单（幂等键由上下文给）与退款（唯一动钱的一个）---
    def create_ticket(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        key = ctx.idempotency_key or _str_arg(args, "idempotency_key")
        creation = tickets.create(
            ctx.identity,
            category=_str_arg(args, "category"),
            priority=str(args.get("priority", "NORMAL")),
            subject=_str_arg(args, "subject"),
            description=_str_arg(args, "description"),
            idempotency_key=key,
        )
        ticket = creation.ticket
        return ToolResult(
            ok=True,
            data={
                "ticket_id": ticket.id,
                "ticket_no": ticket.ticket_no,
                "status": ticket.status.value,
                "category": ticket.category,
                "priority": ticket.priority,
                "created": creation.created,
            },
            message=f"已创建工单 {ticket.ticket_no}。",
        )

    def check_refund_eligibility(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        amount = args.get("amount_cents")
        if amount is not None and not isinstance(amount, int):
            raise ValidationAppError("amount_cents must be an integer.")
        result = policy.check(ctx.identity, _str_arg(args, "order_id"), amount)
        data = result.to_dict()
        # 只读回执的尾句必须阈值感知（B-3 口径 §4.3 / §3.1）：数据源只有
        # 资格结果里已有的 requires_approval（PolicyEngine 阈值判定），
        # 不新增阈值判定、档位表不参与「要不要人批」。为真时的输出与
        # 改动前逐字一致（§8.1 / §4.2.1 不变量 5）；为假时不得断言
        # 「实际退款需要人工审批」，也不得出现档位内部口径或阈值金额
        # （§4.3 客户可见文本禁令）。
        data["approval_requirement"] = (
            "实际退款需要人工审批。"
            if data["requires_approval"]
            else "实际退款是否进入人工审批，取决于退款金额与当前审批阈值。"
        )
        return ToolResult(ok=True, data=data)

    def request_refund(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if not ctx.approval_id:
            raise ValidationAppError("approval_id is required for request_refund.")
        outcome = refunds.execute(ctx.task_id, ctx.approval_id)
        # 结果未知不是回执：钱可能已经出去了，也可能没有，只有对账能回答。
        # 用「结果未知」这个失败码表达，执行器就不会重放（`_may_retry` 对
        # unknown 一律拒绝），网关的审计也会记 ERROR 而不是 SUCCESS。
        unconfirmed = outcome.status is VerificationStatus.PENDING_CONFIRMATION
        return ToolResult(
            ok=not unconfirmed and outcome.status is not VerificationStatus.FAILED,
            data={
                "verification_status": outcome.status.value,
                "summary": outcome.summary,
                "refund_id": outcome.refund_id,
                "ledger_id": outcome.ledger_id,
                "amount_cents": outcome.amount_cents,
                "currency": outcome.currency,
                "checks": list(outcome.checks),
                "reason_code": outcome.reason_code,
                # 退款原因 / 退款类型给中文唯一名；缺值是「未提供」（口径 §2、§5.5）。
                # 补充说明（reason_note）不进公开回执（口径 §5.5）。
                "refund_reason_label": refund_reason_label(outcome.refund_reason),
                "refund_type_label": refund_type_label(outcome.refund_type),
            },
            message=outcome.summary,
            error_code="OUTCOME_UNKNOWN" if unconfirmed else outcome.reason_code,
            retryable=unconfirmed,
        )

    # --- 清单：两个 R1 只读工具，超时与重试预算写在清单里，不在处理器里 ---
    registry.register(
        ToolManifest(
            name="get_order",
            version="2",
            description="查询当前用户自己的订单详情（金额、状态、收货信息）。",
            params={
                "type": "object",
                "properties": {"order_id": _ORDER_ID},
                "required": ["order_id"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.R1,
            side_effect=False,
            requires_approval=False,
            idempotency="forbidden",
            timeout_ms=3000,
            retry=RetryPolicy(max_attempts=2, backoff_ms=200, retry_on=("timeout",)),
            degradation=(
                DegradationStep(
                    kind="ask_user",
                    message="订单号格式不对，应为 ORD-… 形式，请再确认一次。",
                    action="clarify_order_id",
                ),
            ),
            result_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "status": {"type": "string"},
                    "total_cents": {"type": "integer"},
                    "refundable_cents": {"type": "integer"},
                    "currency": {"type": "string"},
                },
                "required": ["order_id", "status"],
            },
            receipt=(
                "订单 {order_id} 当前状态为 {status}，"
                "订单金额 {total_cents:yuan} {currency}，"
                "可退余额 {refundable_cents:yuan} {currency}。"
            ),
            scopes=(),
        ),
        get_order,
    )
    registry.register(
        ToolManifest(
            name="get_logistics",
            version="2",
            description="查询当前用户自己订单的物流轨迹。",
            params={
                "type": "object",
                "properties": {"order_id": _ORDER_ID},
                "required": ["order_id"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.R1,
            side_effect=False,
            requires_approval=False,
            idempotency="forbidden",
            timeout_ms=3000,
            retry=RetryPolicy(max_attempts=2, backoff_ms=200, retry_on=("timeout",)),
            degradation=(
                DegradationStep(
                    kind="ask_user",
                    message="订单号格式不对，应为 ORD-… 形式，请再确认一次。",
                    action="clarify_order_id",
                ),
                DegradationStep(
                    kind="handoff",
                    message="物流系统暂时不可用，已为你转接人工。",
                    action="handoff",
                ),
            ),
            result_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "available": {"type": "boolean"},
                    "carrier": {"type": "string"},
                    "tracking_no": {"type": "string"},
                    "status": {"type": "string"},
                    "last_event": {"type": "string"},
                },
                "required": ["order_id"],
            },
            receipt=(
                "订单 {order_id} 由 {carrier} 承运（运单 {tracking_no}），"
                "最新状态 {status}：{last_event}。"
            ),
            scopes=(),
        ),
        get_logistics,
    )
    # --- 清单：政策检索 R0（无副作用，不需要幂等键）---
    registry.register(
        ToolManifest(
            name="search_policy",
            version="2",
            description="检索当前租户生效中的售后政策片段，返回带版本号的引用。",
            params={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 1000,
                        "hint": "请补充你想了解的政策关键词",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.R0,
            side_effect=False,
            requires_approval=False,
            idempotency="forbidden",
            timeout_ms=5000,
            retry=RetryPolicy(max_attempts=2, backoff_ms=300, retry_on=("timeout",)),
            degradation=(
                DegradationStep(
                    kind="ask_user",
                    message="没能理解你想问的政策，请补充关键词。",
                    action="clarify_query",
                ),
            ),
            result_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
            receipt="找到 {count} 条相关政策片段。",
            scopes=(),
        ),
        search_policy,
    )
    # --- 清单：建单 R2（有副作用，幂等键必需，失败先重试再转人工）---
    registry.register(
        ToolManifest(
            name="create_ticket",
            version="2",
            description="为当前用户创建售后工单，重复提交不会产生第二张工单。",
            params={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["REFUND", "LOGISTICS", "PRODUCT", "OTHER"],
                        "hint": "工单分类应为 REFUND / LOGISTICS / PRODUCT / OTHER",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["LOW", "NORMAL", "HIGH", "URGENT"],
                    },
                    "subject": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 80},
                },
                "required": ["category", "subject", "description"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.R2,
            side_effect=True,
            requires_approval=False,
            idempotency="required",
            timeout_ms=5000,
            retry=RetryPolicy(max_attempts=3, backoff_ms=500, retry_on=("timeout",)),
            degradation=(
                DegradationStep(
                    kind="retry_later",
                    message="系统繁忙，工单已记录，稍后自动重试。",
                    action="schedule_retry",
                ),
                DegradationStep(
                    kind="handoff",
                    message="工单暂时无法创建，已为你转接人工。",
                    action="handoff",
                ),
            ),
            result_schema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "ticket_no": {"type": "string"},
                    "status": {"type": "string"},
                    "category": {"type": "string"},
                    "priority": {"type": "string"},
                },
                "required": ["ticket_id", "ticket_no"],
            },
            receipt="已创建工单 {ticket_no}，分类 {category}，优先级 {priority}。",
            scopes=(),
        ),
        create_ticket,
    )
    # --- 清单：退款资格 R1（只读判定，缺金额不补默认值）---
    registry.register(
        ToolManifest(
            name="check_refund_eligibility",
            version="2",
            description="按退款政策规则判断某笔订单是否可以退款，并给出原因。",
            params={
                "type": "object",
                "properties": {
                    "order_id": _ORDER_ID,
                    "amount_cents": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100000000,
                        "hint": "退款金额需为大于 0 的分（1 分 = 0.01 元）",
                    },
                    "refund_reason": _REFUND_REASON,
                    "refund_type": _REFUND_TYPE,
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.R1,
            side_effect=False,
            requires_approval=False,
            idempotency="forbidden",
            timeout_ms=3000,
            retry=RetryPolicy(max_attempts=2, backoff_ms=200, retry_on=("timeout",)),
            degradation=(
                DegradationStep(
                    kind="ask_user",
                    message="退款金额格式不正确，请提供大于 0 且最多两位小数的金额。",
                    action="clarify_amount",
                ),
            ),
            result_schema={
                "type": "object",
                "properties": {
                    "eligible": {"type": "boolean"},
                    "reasons": {"type": "array"},
                    "amount": {"type": "string"},
                    "max_refundable_cents": {"type": "integer"},
                    "currency": {"type": "string"},
                    "requires_approval": {"type": "boolean"},
                    "approval_requirement": {"type": "string"},
                },
                "required": ["eligible", "reasons"],
            },
            receipt=(
                "按当前政策，该订单最多可退 {max_refundable_cents:yuan} {currency}，"
                "{approval_requirement}"
            ),
            scopes=(),
        ),
        check_refund_eligibility,
    )
    # --- 清单：退款执行 R3（有副作用、必须审批；不重试，结果未知就停下等人核）---
    registry.register(
        ToolManifest(
            name="request_refund",
            version="2",
            description="执行已审批通过的退款，并返回可核对的结果。",
            params={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_level=RiskLevel.R3,
            side_effect=True,
            requires_approval=True,
            idempotency="required",
            timeout_ms=10000,
            retry=RetryPolicy(max_attempts=1, backoff_ms=0, retry_on=()),
            degradation=(
                DegradationStep(
                    kind="stop",
                    message="退款处理中，最长 30 分钟出结果，稍后会主动通知你。",
                    action="pending_confirmation",
                ),
                DegradationStep(
                    kind="handoff",
                    message="退款状态需要人工核实，已为你转接并带上完整上下文。",
                    action="handoff",
                ),
            ),
            result_schema={
                "type": "object",
                "properties": {
                    "verification_status": {"type": "string"},
                    "refund_id": {"type": "string"},
                    "ledger_id": {"type": "string"},
                    "amount_cents": {"type": "integer"},
                    "currency": {"type": "string"},
                    "checks": {"type": "array"},
                    "reason_code": {"type": "string"},
                    "refund_reason_label": {"type": "string"},
                    "refund_type_label": {"type": "string"},
                },
                "required": ["verification_status"],
            },
            receipt=(
                "已退款 {amount_cents:yuan} {currency}，"
                "退款原因：{refund_reason_label}，退款类型：{refund_type_label}，"
                "退款单号 {refund_id}，已由权威退款记录确认。"
            ),
            scopes=(),
        ),
        request_refund,
    )
    # --- 装配完毕：注册表交给网关与执行器，运行时不必知道工具从哪来 ---
    return registry


def _str_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationAppError(f"Missing or invalid argument: {key}.")
    return value.strip()
