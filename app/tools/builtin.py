from __future__ import annotations

from typing import Any

from app.core.errors import ValidationAppError
from app.models.enums import RiskLevel
from app.retrieval.search import PolicySearch
from app.services.orders import OrderService
from app.services.refund_policy import RefundPolicy
from app.services.refunds import RefundExecutionService
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


def build_registry(
    *,
    orders: OrderService,
    policies: PolicySearch,
    tickets: TicketService,
    refunds: RefundExecutionService,
    policy: RefundPolicy,
) -> ToolRegistry:
    registry = ToolRegistry()

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
        return ToolResult(ok=True, data=result.to_dict())

    def request_refund(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if not ctx.approval_id:
            raise ValidationAppError("approval_id is required for request_refund.")
        outcome = refunds.execute(ctx.task_id, ctx.approval_id)
        return ToolResult(
            ok=outcome.status.value != "FAILED",
            data={
                "verification_status": outcome.status.value,
                "summary": outcome.summary,
                "refund_id": outcome.refund_id,
                "ledger_id": outcome.ledger_id,
                "amount_cents": outcome.amount_cents,
                "currency": outcome.currency,
                "checks": list(outcome.checks),
                "reason_code": outcome.reason_code,
            },
            message=outcome.summary,
            error_code=outcome.reason_code,
        )

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
                },
                "required": ["eligible", "reasons"],
            },
            receipt=(
                "按当前政策，该订单最多可退 {max_refundable_cents:yuan} {currency}，"
                "实际退款需要人工审批。"
            ),
            scopes=(),
        ),
        check_refund_eligibility,
    )
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
                },
                "required": ["verification_status"],
            },
            receipt=(
                "已退款 {amount_cents:yuan} {currency}，"
                "退款单号 {refund_id}，已由权威退款记录确认。"
            ),
            scopes=(),
        ),
        request_refund,
    )
    return registry


def _str_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationAppError(f"Missing or invalid argument: {key}.")
    return value.strip()
