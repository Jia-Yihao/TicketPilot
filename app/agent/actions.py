"""动作处理器：意图目录里每个意图的「可执行的那一半」。

目录（``app/agent/intents.yaml``）说这句话是什么意思、该由哪个动作来答；这里
就是那些动作本身。所以加一项能力 = 目录里加一行 + 写一个函数并用 ``@action``
注册，``support_agent.py`` 里的派发代码一行都不用改——派发只认名字。

一条取舍：动作只负责组装 ``Draft``（回执文字、卡片、建议、可信度），不写业务
表。真正动数据的那条路是 ``_create_task`` 把请求交给 ``TaskRuntime``，审批、
幂等与审计都留在运行时里。这样读路径永远不可能在动作层留下半个事务。

边界：动作依赖运行时，运行时、工具实现与仓储都不该反过来依赖动作。入参面只有
``ActionRequest`` 一个，加字段要同时想清楚哪些动作会读到它、哪些动作读不到也
不该假装读到了——``Draft.resolved`` 与 ``Draft.evidence`` 是转人工判定的输入，
凭空填成「已解决」会让信号表看不出失败。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.decision import ModelDecision
from app.agent.escalation import EscalationSignal, get_escalation_catalog
from app.agent.turns import Draft, SupportPlan, Turn
from app.core.errors import NotFoundError
from app.core.money import format_cents
from app.core.policy import EvidenceScore, PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.observability import tracing
from app.observability.tracing import TraceRecorder
from app.services.catalog import CatalogService
from app.services.llm_usage import LlmUsageService
from app.services.refund_policy import EligibilityResult, RefundPolicy
from app.services.runtime import TaskRuntime
from app.services.support import SupportContextService, refund_reason_label
from app.sql.errors import SqlRejected
from app.sql.narrative import AnalyticsNarrator, NarrationResult, narrate, render_template
from app.sql.schema import DATASETS
from app.sql.service import SqlAnalyticsService, SqlAnswer
from app.sql.snapshot import AnalyticsSnapshot


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
    # The chat entry's model reading, offered to the task ledger for review.
    # It is not a verdict: the runtime verifies it before reusing anything.
    model_decision: ModelDecision | None = None
    # The one narrator allowed to phrase prefetched facts. ``None`` in rules
    # mode or when the switch is off, which is also the zero-call guarantee.
    narrator: AnalyticsNarrator | None = None
    conversation_id: str | None = None
    # The snapshot a detail follow-up asked to open; ``None`` for a new turn.
    expand_query_id: str | None = None


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


# Two different situations, two different sentences: the library has no clause
# for this tenant at all (stop and say so), versus the clause exists but this
# phrasing found nothing (rephrase, or hand over). The handover signal replaces
# both for the customer, but the trace and the draft keep them apart.
_EMPTY_POLICY_REPLIES = {
    # Kept verbatim from before: this sentence has always meant the library.
    "empty_query": "未检索到当前租户下仍在有效期内的适用政策。建议转人工核实。",
    "no_visible_chunks": "未检索到当前租户下仍在有效期内的适用政策。建议转人工核实。",
    "no_match": (
        "这次没检索到和这个问题相关的条款：问法可能和条款措辞对不上。"
        "可以换个说法再问一次，或者直接转人工核实。"
    ),
}


@action("retrieval.hybrid")
def policy_question(request: ActionRequest) -> Draft:
    retrieve_started = time.perf_counter()
    evidence = request.contexts.policy_evidence(
        request.identity, request.message, limit=2
    )
    cards = evidence.cards
    request.trace.add(
        "retrieve",
        "policy.search",
        attributes={
            "query": request.message,
            "hits": len(cards),
            "evidence_score": evidence.best.score if evidence.best else None,
            "evidence_kind": evidence.best.kind if evidence.best else None,
            "empty_reason": evidence.empty_reason,
            "citations": [
                {
                    "document_id": card["card_id"],
                    "version": card["fields"].get("version"),
                    "section": card["fields"].get("matched_section"),
                }
                for card in cards
            ],
        },
        latency_ms=tracing.elapsed_ms(retrieve_started),
    )
    if not cards:
        return Draft(
            intent=request.plan.intent,
            reply=_EMPTY_POLICY_REPLIES[evidence.empty_reason or "no_visible_chunks"],
            suggestions=["查询订单", "转人工"],
            evidence=None,
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
    best = evidence.best
    if best is None:
        # A card only ever comes from a scored hit, so a card without a score is
        # a programming error — never a reason to guess a scale for the number.
        raise RuntimeError("Policy cards came back without an evidence score.")
    threshold = request.policy.escalation.no_evidence_threshold(best.kind)
    return Draft(
        intent=request.plan.intent,
        reply=reply,
        cards=cards,
        suggestions=["查询订单", "申请退款", "转人工"],
        evidence=best,
        resolved=best.score >= threshold,
    )


@action("refund.request")
def refund_request(request: ActionRequest) -> Draft:
    return _create_task(request)


@action("ticket.create")
def ticket_create(request: ActionRequest) -> Draft:
    return _create_task(request)


def _create_task(request: ActionRequest) -> Draft:
    task = request.runtime.create_task(
        request.identity,
        request.request_text,
        request.context or None,
        decision=request.model_decision,
        conversation_id=request.conversation_id,
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
    asked and the router picks a deterministic path:

    * the controlled statement path returns a template sentence and makes no
      model call;
    * the prefetch path hands server-computed facts to the narrator, whose
      sentence is checked number by number before it is shown;
    * a question the planner cannot read is refused rather than guessed at.
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
            evidence=EvidenceScore(score=0.0, kind="verdict"),
            resolved=False,
        )
    if answer.route == "PREFETCH" and answer.snapshot is not None:
        return _prefetch_answer(request, answer)
    summary, card = _analytics_summary(answer)
    return Draft(
        intent=request.plan.intent,
        reply=summary,
        cards=[card],
        suggestions=["查询订单", "退款状态", "转人工"],
        evidence=EvidenceScore(score=1.0, kind="verdict"),
        resolved=True,
    )


def _prefetch_answer(request: ActionRequest, answer: SqlAnswer) -> Draft:
    """Phrase prefetched facts, then prove the sentence kept them."""
    snapshot = answer.snapshot
    if snapshot is None:  # pragma: no cover - guarded by the caller
        summary, card = _analytics_summary(answer)
        return Draft(intent=request.plan.intent, reply=summary, cards=[card])
    narrate_started = time.perf_counter()
    result = narrate(request.message, snapshot, request.narrator)
    _record_narration_usage(request, result)
    request.trace.add(
        "generate",
        "analytics.narrate",
        attributes={
            "query_id": snapshot.query_id,
            "source": result.source,
            "faithfulness": result.faithfulness,
            "prompt_chars": result.prompt.chars,
            "facts_chars": result.prompt.facts_chars,
            "estimated_tokens": result.prompt.estimated_tokens,
            "truncated": snapshot.truncated,
            "rows_in_prompt": len(snapshot.recent),
        },
        latency_ms=tracing.elapsed_ms(narrate_started),
    )
    request.trace.add(
        "guard",
        "analytics.faithfulness",
        status="FAILED" if result.faithfulness == "failed" else "OK",
        attributes={
            "status": result.faithfulness,
            "missing_numbers": list(result.missing),
            "source": result.source,
        },
        latency_ms=tracing.elapsed_ms(narrate_started),
    )

    dataset = DATASETS[snapshot.dataset]
    _template, fields = render_template(
        dataset=snapshot.dataset,
        metric=snapshot.metric,
        group_by=snapshot.group_by,
        span=snapshot.time_range,
        rows=snapshot.l1_rows(),
    )
    card = _analytics_card(
        answer, dataset.label, snapshot.time_range, summary=result.reply, fields=fields
    )
    if snapshot.truncated:
        note = f"只展开最近 {len(snapshot.rows)} 条明细，统计数字仍是全量。"
        card["sections"] = [
            {"id": "detail-window", "title": "明细范围", "preview": note, "chars": len(note)}
        ]
    return Draft(
        intent=request.plan.intent,
        reply=result.reply,
        cards=[card],
        suggestions=["查询订单", "退款状态", "转人工"],
        evidence=EvidenceScore(score=1.0, kind="verdict"),
        resolved=True,
        analytics_query_id=snapshot.query_id,
    )


@action("analytics.expand")
def analytics_expand(request: ActionRequest) -> Draft:
    """Open the rows behind the previous statistic, without a model call.

    The snapshot store is the only source: an expired id, an id built by
    another identity, or an id from another worker's process all look the
    same and all ask the customer to re-ask. Falling back to a fresh query
    here would silently answer a different question.
    """
    query_id = request.expand_query_id
    if not query_id:
        return Draft(
            intent=request.plan.intent,
            reply="没有可以展开的统计结果，请先提出一个统计问题。",
            suggestions=["我买了多少订单", "我退了几笔款", "转人工"],
            evidence=EvidenceScore(score=1.0, kind="verdict"),
            resolved=False,
        )
    snapshot = request.analytics.get_snapshot(query_id, request.identity)
    if snapshot is None:
        request.trace.add(
            "tool",
            "analytics.expand",
            attributes={"query_id": query_id, "status": "UNAVAILABLE"},
        )
        return Draft(
            intent=request.plan.intent,
            reply=(
                "上一次统计的明细已经过期或不属于当前身份，"
                "请重新提出统计问题，我再展开明细。"
            ),
            suggestions=["我买了多少订单", "我退了几笔款", "转人工"],
            evidence=EvidenceScore(score=1.0, kind="verdict"),
            resolved=True,
        )

    card = _analytics_detail_card(snapshot)
    request.trace.add(
        "tool",
        "analytics.expand",
        attributes={
            "query_id": snapshot.query_id,
            "status": "OK",
            "rows": len(snapshot.rows),
            "truncated": snapshot.truncated,
            "source": "snapshot",
        },
    )
    sentences = [
        f"{snapshot.time_range}共 {snapshot.count} {snapshot.unit}"
        f"{snapshot.label}记录，下面列出最近 {len(snapshot.rows)} 条明细。"
    ]
    if snapshot.truncated:
        sentences.append("统计数字仍是全量。")
    return Draft(
        intent=request.plan.intent,
        reply=" ".join(sentences),
        cards=[card],
        suggestions=["查询订单", "退款状态", "转人工"],
        evidence=EvidenceScore(score=1.0, kind="verdict"),
        resolved=True,
        analytics_query_id=snapshot.query_id,
    )


def _analytics_detail_card(snapshot: AnalyticsSnapshot) -> dict[str, Any]:
    """The L3 rows, one folded section each, under the statistics card shape."""
    sections: list[dict[str, Any]] = []
    for index, row in enumerate(snapshot.rows, start=1):
        status = row.get("status_label") or row.get("status") or ""
        reference = row.get("reference") or row.get("id") or ""
        at = str(row.get("created_at") or "").replace("T", " ")
        parts = [str(reference)]
        if status:
            parts.append(str(status))
        amount = row.get("amount_cents")
        if isinstance(amount, int):
            parts.append(f"{format_cents(amount)} 元")
        if at:
            parts.append(at)
        preview = " · ".join(item for item in parts if item)
        sections.append(
            {
                "id": f"row-{index}",
                "title": f"{snapshot.label} {index}",
                "preview": preview,
                "chars": len(preview),
            }
        )
    if snapshot.rows:
        summary = (
            f"{snapshot.time_range}共 {snapshot.count} {snapshot.unit}"
            f"{snapshot.label}记录，展示最近 {len(snapshot.rows)} 条。"
        )
    else:
        summary = f"{snapshot.time_range}没有{snapshot.label}记录。"
    fields: dict[str, Any] = {
        "range": snapshot.time_range,
        "records": snapshot.count,
        "shown": len(snapshot.rows),
    }
    if snapshot.metric in {"sum", "count_and_sum"}:
        fields["amount_cents"] = snapshot.total_cents
    return {
        "kind": "analytics_detail",
        "card_id": snapshot.query_id,
        "title": f"{snapshot.label}明细",
        "subtitle": snapshot.time_range,
        "summary": summary,
        "badges": [],
        "fields": fields,
        "sections": sections,
        "source": f"ledger://{snapshot.dataset}",
        "total_chars": len(summary),
    }


def _record_narration_usage(request: ActionRequest, result: NarrationResult) -> None:
    """Write the expression call to the shared usage ledger, source analytics."""
    if result.call is None:
        return
    narrator = request.narrator
    LlmUsageService(request.session).record(
        tenant_id=request.identity.tenant_id,
        user_id=request.identity.user_id,
        conversation_id=request.conversation_id,
        source="analytics",
        planning_source="pydantic_ai",
        model_name=getattr(narrator, "model_name", None),
        requests=result.call.requests,
        retries=result.call.retries,
        input_tokens=result.call.input_tokens,
        output_tokens=result.call.output_tokens,
        latency_ms=result.call.latency_ms,
        status=result.call.status,
    )


def _analytics_summary(answer: SqlAnswer) -> tuple[str, dict[str, Any]]:
    """The template sentence and the card that carries the numbers."""
    dataset = DATASETS[answer.plan.dataset]
    span = answer.plan.time_range.label if answer.plan.time_range else "全部时间"
    sentence, fields = render_template(
        dataset=dataset.name,
        metric=answer.plan.metric,
        group_by=answer.plan.group_by,
        span=span,
        rows=answer.rows,
    )
    return sentence, _analytics_card(
        answer, dataset.label, span, summary=sentence, fields=fields
    )


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
