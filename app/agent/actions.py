"""Action handlers — the executable half of the intent catalog.

Every capability the agent can perform is an *action*. The catalog row names
the action; this module implements it. Adding a capability therefore means
adding a row to ``intents.yaml`` and one function here — the routing and
dispatch code in ``support_agent.py`` never changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.escalation import EscalationSignal, get_escalation_catalog
from app.agent.turns import Draft, SupportPlan, Turn
from app.core.errors import NotFoundError
from app.core.money import format_cents
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.observability.tracing import TraceRecorder
from app.services.catalog import CatalogService
from app.services.refund_policy import EligibilityResult, RefundPolicy
from app.services.runtime import TaskRuntime
from app.services.support import SupportContextService, refund_reason_label
from app.sql.errors import SqlRejected
from app.sql.schema import DATASETS, status_label
from app.sql.service import SqlAnalyticsService, SqlAnswer


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """Everything an action needs to answer one turn."""

    session: Session
    identity: Identity
    contexts: SupportContextService
    catalog: CatalogService
    policy: PolicyEngine
    runtime: TaskRuntime
    analytics: SqlAnalyticsService
    plan: SupportPlan
    state: Turn
    message: str
    request_text: str
    context: dict[str, Any]
    trace: TraceRecorder
    signal: EscalationSignal | None = None


ActionFn = Callable[[ActionRequest], Draft]

REGISTRY: dict[str, ActionFn] = {}


def action(name: str) -> Callable[[ActionFn], ActionFn]:
    """Register a handler under the action name the catalog refers to."""

    def register(fn: ActionFn) -> ActionFn:
        REGISTRY[name] = fn
        return fn

    return register


# --------------------------------------------------------------- shared bits
def order_card(
    contexts: SupportContextService, identity: Identity, plan: SupportPlan
) -> dict[str, Any] | None:
    if not plan.order_id:
        return None
    try:
        return contexts.order_card(identity, plan.order_id)
    except NotFoundError:
        return None


def order_not_found(plan: SupportPlan) -> Draft:
    return Draft(
        intent=plan.intent,
        reply="没有找到这个订单，或当前身份无权查看。请核对订单号后再试。",
        suggestions=["查询订单", "退款条件是什么？", "转人工"],
        resolved=False,
    )


def eligibility_sentence(eligibility: EligibilityResult, policy: PolicyEngine) -> str:
    currency = eligibility.order.currency
    if eligibility.eligible:
        return (
            f"按政策 {eligibility.policy_version} 可退 "
            f"{format_cents(eligibility.amount_cents)} {currency}，"
            "提交后仍需人工审批。"
        )
    reasons = "、".join(
        refund_reason_label(reason, policy) for reason in eligibility.reasons
    )
    return f"当前不符合退款条件（{reasons}）。"


def delivery_verdict(
    contexts: SupportContextService, identity: Identity, order_id: str
) -> str:
    logistics = contexts.orders.get_logistics(identity, order_id)
    if logistics is None:
        return "该订单没有物流记录，需要人工核实。"
    if logistics.status.value == "EXCEPTION":
        return "物流状态是 EXCEPTION，属于异常件。"
    now = utcnow()
    if logistics.estimated_delivery is not None and logistics.estimated_delivery < now:
        return (
            f"已超过预计送达时间 {logistics.estimated_delivery.isoformat()}，"
            "按规则属于严重延迟。"
        )
    if "停留" in logistics.last_event or "48" in logistics.last_event:
        return (
            f"最新物流节点是「{logistics.last_event}」，"
            "轨迹长期停留在同一节点，应先向承运商核实，不能直接判定丢件。"
        )
    return f"物流仍在推进：{logistics.last_event}。"


# ------------------------------------------------------------------ handlers
@action("catalog.search")
def catalog_search(request: ActionRequest) -> Draft:
    product = request.catalog.resolve(request.message) or (
        request.catalog.for_order(request.plan.order_id)
        if request.plan.order_id
        else None
    )
    if product is not None:
        cards = [request.contexts.product_card(product)]
        reply = (
            f"{product.name}（{product.sku}）售价 "
            f"{format_cents(product.price_cents)} {product.currency}。"
            f"{product.summary}"
            " 规格、质保和使用说明已折叠在卡片里，展开对应章节即可查看。"
        )
    else:
        matches = request.catalog.search(request.message)
        if not matches:
            return Draft(
                intent=request.plan.intent,
                reply="没有匹配到商品记录。请补充商品名、SKU 或订单号，我再查一次。",
                suggestions=["AeroDesk 的参数", "TrailLite 的质保", "查询订单物流"],
                resolved=False,
            )
        cards = [request.contexts.product_card(item) for item in matches[:2]]
        names = "、".join(str(card["title"]) for card in cards)
        reply = (
            f"找到 {len(cards)} 款相关商品：{names}。"
            "先给出摘要卡片；补充 SKU 或具体型号后可以继续缩小范围。"
        )
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=cards,
        suggestions=["这款商品的质保范围", "查询订单物流", "退款条件是什么？"],
    )


@action("order.get")
def order_get(request: ActionRequest) -> Draft:
    card = order_card(request.contexts, request.identity, request.plan)
    if card is None:
        return order_not_found(request.plan)
    eligibility = RefundPolicy(request.session, request.policy).check(
        request.identity, request.plan.order_id or ""
    )
    reply = (
        f"{card['summary']} "
        f"{eligibility_sentence(eligibility, request.policy)}"
        " 订单字段与物流记录已折叠到卡片中，按需展开。"
    )
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=[card],
        suggestions=["这件商品怎么保养", "申请退款", "转人工"],
    )


@action("order.logistics")
def order_logistics(request: ActionRequest) -> Draft:
    card = order_card(request.contexts, request.identity, request.plan)
    if card is None:
        return order_not_found(request.plan)
    logistics = card["sections"][1]["preview"]
    reply = (
        f"{card['summary']} 物流摘要：{logistics} 展开「物流记录」可查看完整节点。"
    )
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=[card],
        suggestions=["这件商品怎么保养", "申请退款", "转人工"],
    )


@action("order.delivery_review")
def order_delivery_review(request: ActionRequest) -> Draft:
    card = order_card(request.contexts, request.identity, request.plan)
    if card is None:
        return order_not_found(request.plan)
    verdict = delivery_verdict(
        request.contexts, request.identity, request.plan.order_id or ""
    )
    policy_cards = request.contexts.policy_cards(
        request.identity, "严重延迟 延迟判定 处理方式 承运商 核实", limit=1
    )
    reply = (
        f"订单 {request.plan.order_id} 的物流判断：{verdict}"
        " 按《延迟配送处理规则》，严重延迟可以创建高优先级工单由人工联系承运商；"
        "延迟本身不自动产生退款资格。"
    )
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=[card, *policy_cards],
        suggestions=["创建高优先级工单", "查询物流轨迹", "申请退款"],
    )


@action("refund.eligibility")
def refund_eligibility(request: ActionRequest) -> Draft:
    card = order_card(request.contexts, request.identity, request.plan)
    if card is None:
        return order_not_found(request.plan)
    eligibility = RefundPolicy(request.session, request.policy).check(
        request.identity, request.plan.order_id or ""
    )
    reply = (
        f"订单 {request.plan.order_id} 的退款资格："
        f"{eligibility_sentence(eligibility, request.policy)}"
        " 这是按当前政策与订单字段算出来的结论；"
        "点开「退款资格」章节可以看到每一条判定依据。"
    )
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=[card],
        suggestions=["申请退款", "退款条件是什么？", "转人工"],
    )


@action("refund.status")
def refund_status(request: ActionRequest) -> Draft:
    card = order_card(request.contexts, request.identity, request.plan)
    if card is None:
        return order_not_found(request.plan)
    records = request.contexts.refund_records(
        request.identity, request.plan.order_id or ""
    )
    if not records:
        reply = (
            f"订单 {request.plan.order_id} 目前没有退款记录，可退余额 "
            f"{format_cents(card['fields']['refundable_cents'])} "
            f"{card['fields']['currency']}。"
            "如果已经在别处提交过退款申请，把申请编号或工单号发我核对。"
        )
        return Draft(
            intent=request.plan.intent,
            reply=reply,
            cards=[card],
            suggestions=["申请退款", "查询订单", "转人工"],
        )
    lines = "；".join(
        f"{row['amount']} 元（{row['status_label']}，状态 {row['status']}，"
        f"发起于 {row['created_at']}）"
        for row in records
    )
    reply = f"订单 {request.plan.order_id} 的退款记录：{lines}。"
    if any(row["status"] == "UNKNOWN" for row in records):
        reply += (
            "退款状态尚未确认，必须先与渠道对账；"
            "在权威记录确认前我不会说这笔退款已经到账。"
        )
    elif all(row["status"] == "SUCCEEDED" for row in records):
        reply += "这笔退款已经确认成功。"
    else:
        reply += "退款还在处理链路里，可以继续追问进度。"
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=[card],
        suggestions=["查询订单", "退款条件是什么？", "转人工"],
    )


@action("retrieval.hybrid")
def policy_question(request: ActionRequest) -> Draft:
    cards, evidence_score = request.contexts.policy_evidence(
        request.identity, request.message, limit=2
    )
    request.trace.add(
        "retrieve",
        "policy.search",
        attributes={
            "query": request.message,
            "hits": len(cards),
            "evidence_score": evidence_score,
            "citations": [
                {
                    "document_id": card["card_id"],
                    "version": card["fields"].get("version"),
                    "section": card["fields"].get("matched_section"),
                }
                for card in cards
            ],
        },
    )
    if not cards:
        return Draft(
            intent=request.plan.intent,
            reply="未检索到当前租户下仍在有效期内的适用政策。建议转人工核实。",
            suggestions=["查询订单", "转人工"],
            evidence_score=None,
            resolved=False,
        )
    top = cards[0]
    version = top["fields"].get("version", "")
    section = top["fields"].get("matched_section")
    where = f"「{section}」" if section else ""
    reply = (
        f"根据《{top['title']}》{version}{where}：{top['summary']} "
        "完整章节仍在卡片中按需展开，引用会随章节一起返回。"
    )
    threshold = request.policy.escalation.no_evidence_score
    grounded = evidence_score is not None and evidence_score >= threshold
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=cards,
        suggestions=["查询订单", "申请退款", "转人工"],
        evidence_score=evidence_score,
        resolved=grounded,
    )


@action("refund.request")
def refund_request(request: ActionRequest) -> Draft:
    return _create_task(request)


@action("ticket.create")
def ticket_create(request: ActionRequest) -> Draft:
    return _create_task(request)


def _create_task(request: ActionRequest) -> Draft:
    task = request.runtime.create_task(
        request.identity, request.request_text, request.context or None
    )
    result = task.get("result") or {}
    summary = result.get("summary") or "任务已创建。"
    if task["status"] == "WAITING_APPROVAL":
        reply = (
            "已把这条请求交给确定性工作流。退款已通过资格校验，"
            f"正在等待人工审批：{summary} 审批通过前不会调用退款工具。"
        )
    else:
        reply = f"已把这条请求交给确定性工作流：{summary}"
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        suggestions=["查询订单状态", "查看售后条款", "转人工"],
        task=task,
        resolved=task["status"] != "FAILED",
    )


@action("analytics.query")
def analytics_query(request: ActionRequest) -> Draft:
    """Answer a question about the caller's own records.

    The question never becomes SQL here. The planner writes down what was
    asked, the builder turns that into a statement, and the guard decides
    whether the statement may run. A question the planner does not
    understand is refused rather than guessed at.
    """
    try:
        answer = request.analytics.answer(
            request.message,
            request.identity,
            request.session,
            trace=request.trace,
        )
    except SqlRejected as exc:
        request.trace.add(
            "tool",
            "analytics.query",
            attributes={"status": "REJECTED", "reason": exc.reason},
        )
        return Draft(
            intent=request.plan.intent,
            reply="这个问题我没法从记录里直接回答，已转人工同事帮你核对。",
            suggestions=["查询订单", "退款状态", "转人工"],
            evidence_score=0.0,
            resolved=False,
        )
    summary, card = _analytics_summary(answer)
    return Draft(
        intent=request.plan.intent,
        reply=summary,
        cards=[card],
        suggestions=["查询订单", "退款状态", "转人工"],
        evidence_score=1.0,
        resolved=True,
    )


def _analytics_summary(answer: SqlAnswer) -> tuple[str, dict[str, Any]]:
    """The sentence the customer reads, and the card that carries the numbers."""
    dataset = DATASETS[answer.plan.dataset]
    span = answer.plan.time_range.label if answer.plan.time_range else "全部时间"
    rows = answer.rows
    if answer.plan.group_by is None:
        record = rows[0] if rows else {}
        count = _as_int(record.get("metric"))
        total = _as_int(record.get("metric_total"))
        if answer.plan.metric == "sum":
            sentence = f"{span}你的{dataset.label}金额合计 {format_cents(total)} 元。"
        elif answer.plan.metric == "count_and_sum":
            sentence = (
                f"{span}你有 {count} {dataset.unit}{dataset.label}记录，"
                f"金额合计 {format_cents(total)} 元。"
            )
        else:
            sentence = f"{span}你有 {count} {dataset.unit}{dataset.label}记录。"
        fields: dict[str, Any] = {"range": span, "currency": "CNY"}
        if answer.plan.metric in {"count", "count_and_sum"}:
            fields["records"] = count
        if answer.plan.metric in {"sum", "count_and_sum"}:
            fields["amount_cents"] = total
        return sentence, _analytics_card(
            answer, dataset.label, span, summary=sentence, fields=fields
        )

    parts: list[str] = []
    fields = {"range": span}
    for row in rows:
        bucket = str(row.get("bucket", ""))
        count = _as_int(row.get("metric"))
        total = _as_int(row.get("metric_total"))
        label = (
            status_label(dataset.name, bucket)
            if answer.plan.group_by == "status"
            else bucket
        )
        key = label or "unknown"
        if answer.plan.metric == "sum":
            parts.append(f"{label} {format_cents(total)} 元")
        elif answer.plan.metric == "count_and_sum":
            parts.append(f"{label} {count} {dataset.unit}（{format_cents(total)} 元）")
        else:
            parts.append(f"{label} {count} {dataset.unit}")
        if answer.plan.metric in {"count", "count_and_sum"}:
            fields[key] = f"{count} {dataset.unit}"
        if answer.plan.metric in {"sum", "count_and_sum"}:
            fields[f"{key}_amount_cents"] = total
    sentence = (
        f"{span}你的{dataset.label}分布：" + "、".join(parts) + "。"
        if parts
        else f"{span}没有{dataset.label}记录。"
    )
    return sentence, _analytics_card(answer, dataset.label, span, summary=sentence, fields=fields)


def _analytics_card(
    answer: SqlAnswer,
    label: str,
    span: str,
    *,
    summary: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "analytics",
        "card_id": answer.query_id,
        "title": f"{label}统计",
        "subtitle": span,
        "summary": summary,
        "badges": [],
        "fields": fields,
        "sections": [],
        "source": f"ledger://{answer.plan.dataset}",
        "total_chars": len(summary),
    }


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


@action("handoff.escalate")
def handoff_escalate(request: ActionRequest) -> Draft:
    """The turn goes to a human; the wording comes from the trigger table.

    The table owns the reply, so the same handover is phrased the same way
    whether it was triggered by an explicit request or by a cue.
    """
    signal = request.signal
    if signal is None:
        candidates = get_escalation_catalog().intent_signals(
            request.plan.intent.value
        )
        signal = candidates[0] if candidates else None
    if signal is None:
        return Draft(
            intent=request.plan.intent,
            reply="已为你转接人工客服，你不用重复描述。",
        )
    return Draft(intent=request.plan.intent, reply=signal.reply)


@action("none")
def unknown(request: ActionRequest) -> Draft:
    return Draft(
        intent=request.plan.intent,
        reply=(
            "我可以帮你查商品信息、售后条款、订单与物流，"
            "也可以按你的订单判断退款资格，"
            "再把退款或工单请求交给确定性工作流。"
            "请补充商品名、SKU 或订单号。"
        ),
        suggestions=["商品参数", "退款条件是什么？", "查询订单", "转人工"],
    )
