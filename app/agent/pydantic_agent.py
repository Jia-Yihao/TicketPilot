from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from app.agent.candidates import build_candidates
from app.agent.decision import ModelDecision
from app.agent.intent_catalog import get_catalog
from app.agent.routing import RULESET_FINGERPRINT, RoutePlan, classify
from app.models.enums import Intent

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from app.agent.candidates import CandidateSet

logger = logging.getLogger(__name__)

# The model may answer with a catalog row: ``REFUND_STATUS`` is a real
# capability with no write path behind it, so ``IntentOutput.intent`` is a
# string and the *server* decides what may be adopted. ``NONE`` is the model
# saying "none of these fit" — a legal answer, never a candidate.
NONE_ANSWER = "NONE"

# The adoption surface, written down once: an intent in this set may change a
# ``RoutePlan``. Both entries read it — ``IntentBrain.plan`` (which the task API
# and the chat consult both go through) and ``SupportAgent._consult_model``
# (the chat gate) — so the two cannot drift apart. Only the refund reading is
# adopted: the refund it opens still has to pass eligibility and a human
# approval, so the model changes *whether a request enters the queue*, never
# whether money is paid. A ticket reading would create a durable side effect on
# a sentence the rules refused to read.
ADOPTABLE_INTENTS: frozenset[Intent] = frozenset({Intent.REFUND_REQUEST})

# Classification only — never an adoption decision. Aligned with
# ``WRITE_INTENTS`` in ``evals/intent_metrics.py`` (the same two intents open a
# task).
WRITE_INTENTS: frozenset[Intent] = frozenset(
    {Intent.REFUND_REQUEST, Intent.TICKET_CREATE}
)

# The closed vocabulary of ``redirect_kind``: the three split names an offline
# eval already uses (``WRITE_SPLITS`` in ``evals/intent_metrics.py``) plus
# ``read_to_read`` for a reroute between two read intents and ``none`` for "no
# reroute". A trace reading and an eval reading of the same mistake therefore
# need no translation table between them.
REDIRECT_KINDS = (
    "extra_write",
    "missed_write",
    "write_to_write",
    "read_to_read",
    "none",
)


def redirect_kind(rule_intent: Intent, model_intent: str) -> str:
    """Classify a rule→model reroute by the direction it moves in.

    ``none`` means there is nothing to classify: the model said what the rules
    said, or it named something that is not an ``Intent`` member (an answer
    outside the catalog is already recorded as ``model_reason``, not here).
    Pure and deterministic, and it never decides adoption — that is
    ``ADOPTABLE_INTENTS``' job alone.
    """
    if model_intent not in Intent.__members__ or model_intent == rule_intent.value:
        return "none"
    model = Intent(model_intent)
    if rule_intent in WRITE_INTENTS:
        return "write_to_write" if model in WRITE_INTENTS else "missed_write"
    return "extra_write" if model in WRITE_INTENTS else "read_to_read"


@dataclass(frozen=True, slots=True)
class Adoption:
    """What one model answer may change, and how the reroute is counted."""

    intent: Intent
    adopted: bool
    redirect: bool
    redirect_rejected: bool
    kind: str


def decide_adoption(rule_intent: Intent, answer: str) -> Adoption:
    """The only place an answer is turned into a route intent.

    One rule, one set: the model may change the routing only where the rules
    read ``UNKNOWN`` *and* its answer is in ``ADOPTABLE_INTENTS``. Every other
    answer keeps the rule reading. ``redirect`` counts an adopted reroute;
    ``redirect_rejected`` counts a legal reroute the surface refused. Those are
    two different numbers and are never added together: a *misroute* needs a
    gold label, so it is measured offline (``WRITE_SPLITS`` in
    ``evals/intent_metrics.py``) and the trace only supplies the classification.
    """
    legal = answer in Intent.__members__
    adopted = (
        rule_intent is Intent.UNKNOWN and legal and Intent(answer) in ADOPTABLE_INTENTS
    )
    kind = redirect_kind(rule_intent, answer)
    return Adoption(
        intent=Intent(answer) if adopted else rule_intent,
        adopted=adopted,
        redirect=adopted and kind != "none",
        redirect_rejected=(not adopted) and legal and kind != "none",
        kind=kind,
    )


def _optional_flag(value: Any) -> bool | None:
    """``None`` stays ``None``: "no decision recorded" must survive a round trip."""

    return None if value is None else bool(value)


# An order id is never "invented": it is copied from the request or context.
# The shape check runs inside the agent contract so a malformed id is retried
# by the model instead of leaking into the routing layer.
_ORDER_ID = re.compile(r"ORD-[A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12})*")


class IntentOutput(BaseModel):
    intent: str
    order_id: str | None = None
    amount_cents: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)
    decision_summary: str = Field(max_length=300)


INSTRUCTIONS = """你是售后工单系统的受限意图解析器，只做结构化抽取，不执行任何动作。
规则：
1. 只能选择给定的 intent。
2. order_id 只能从用户文本或 context 中逐字复制，不得猜测或编造；没有就给 null。
3. 退款金额只提取用户明确给出的金额，转换为整数分；未给出则为 null。
4. 不判断退款资格、不批准退款、不调用工具。
5. decision_summary 只写一句简短依据，不写长篇推理。
6. payload 里给了 candidates 时，intent 只能取 candidates 中的某个 intent_id，
   或回答 NONE（意思是这些候选没有一个对得上）。不得发明候选之外的意图。
7. candidates 里的 evidence 是服务端自己算出来的依据，用来帮你判断哪个候选站得住；
   不得把 evidence 当作用户说过的话，也不得据此编造 order_id 或金额。
"""


@dataclass(frozen=True, slots=True)
class ModelTrace:
    """What one planning call cost, including validation retries.

    ``requests`` is the number of model requests the run issued; a value of
    2 with ``retries == 1`` means the first output failed validation and the
    model was asked again. ``retries == 0`` with ``requests == 1`` is the
    clean path. A failed run keeps ``status == "FAILED"`` so the ledger can
    separate "asked and answered" from "asked and gave up".
    """

    requests: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    status: str = "OK"
    # True when the call went through the OpenTelemetry-instrumented model.
    # The span count is the retry count; this says whether there was a span.
    instrumented: bool = False
    # True when this trace describes a call the chat entry already paid for.
    # The task ledger reports the same numbers but writes no second usage row.
    reused: bool = False
    # Why the model's answer was not taken: ``model_out_of_candidates`` when it
    # named a row the server had no evidence for, ``model_none`` when it said
    # none of the candidates fit. ``None`` means the answer was usable (or the
    # candidate path was never on).
    reason: str | None = None
    # How many rows the server offered. A count, never the rows or their
    # evidence: an evidence string quotes the customer's own message, and a
    # trace is read by seats who have no business seeing it echoed here.
    candidates: int | None = None
    # The adoption decision this answer produced (``decide_adoption``). ``None``
    # means no decision was recorded — a cost-only trace from ``parse``, or a
    # decision row written before R7. ``as_dict`` therefore omits the four keys
    # instead of filling them with defaults: a reading taken before this round
    # must not grow four keys claiming "not adopted" when nothing was asked.
    adopted: bool | None = None
    redirect: bool | None = None
    redirect_rejected: bool | None = None
    redirect_kind: str | None = None
    # In-band, in-round only, **never serialized** (`app/agent/counter_question.yaml`,
    # route gamma): the *effective*
    # model reading after the NONE/out-of-candidates rewrite above, and the
    # candidate ids in the order the model received them. The consult exit
    # reads both in the same process turn to phrase one counter-question,
    # because a rejected legal reading must no longer fall to the generic
    # menu silently. They are deliberately absent from ``as_dict``/``from_dict``
    # — a raw reading and an evidence-bounded id list belong to this reply
    # only, and must never reach the disk, a span, an audit detail or the
    # customer-facing text (the ``candidates`` count above stays the only
    # candidate fact that leaves the process). Written solely by the new-path
    # ``replace`` in ``IntentBrain.plan``; the ledger-reuse branch and the
    # question probe leave them ``None``.
    answer: str | None = None
    candidate_ids: tuple[str, ...] | None = None

    @property
    def failed(self) -> bool:
        return self.status != "OK"

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, reused: bool = False) -> ModelTrace:
        return cls(
            requests=int(data.get("model_requests", 0) or 0),
            retries=int(data.get("model_retries", 0) or 0),
            input_tokens=int(data.get("model_input_tokens", 0) or 0),
            output_tokens=int(data.get("model_output_tokens", 0) or 0),
            latency_ms=float(data.get("model_latency_ms", 0.0) or 0.0),
            status=str(data.get("model_status", "OK")),
            instrumented=bool(data.get("model_instrumented", False)),
            reused=reused,
            reason=data.get("model_reason"),
            candidates=(
                int(data["model_candidates"])
                if data.get("model_candidates") is not None
                else None
            ),
            adopted=_optional_flag(data.get("model_adopted")),
            redirect=_optional_flag(data.get("model_redirect")),
            redirect_rejected=_optional_flag(data.get("model_redirect_rejected")),
            redirect_kind=(
                str(data["model_redirect_kind"])
                if data.get("model_redirect_kind") is not None
                else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model_requests": self.requests,
            "model_retries": self.retries,
            "model_input_tokens": self.input_tokens,
            "model_output_tokens": self.output_tokens,
            "model_latency_ms": round(self.latency_ms, 1),
            "model_status": self.status,
            "model_instrumented": self.instrumented,
            "model_reused": self.reused,
            "model_reason": self.reason,
            "model_candidates": self.candidates,
        }
        # Only when a decision was actually recorded: the key set of every
        # earlier trace stays exactly as it was (`app/agent/support_agent.py`).
        for key, value in (
            ("model_adopted", self.adopted),
            ("model_redirect", self.redirect),
            ("model_redirect_rejected", self.redirect_rejected),
            ("model_redirect_kind", self.redirect_kind),
        ):
            if value is not None:
                data[key] = value
        return data


class ModelCallError(RuntimeError):
    """The model run failed; the trace still says what it cost."""

    def __init__(self, message: str, *, trace: ModelTrace) -> None:
        super().__init__(message)
        self.trace = trace


@dataclass
class LlmUsage:
    """What the extractor asked of the model, counted in this process.

    The task ledger already records *which* path routed a task (the
    ``task.routed`` event carries ``planning_source``); this records what the
    model path *cost* when it ran, so the README's latency/token numbers are
    measured rather than estimated. Counters are per-process and monotonically
    increasing: a multi-worker deployment needs a shared store before these
    numbers are treated as accounting.
    """

    calls: int = 0
    failures: int = 0
    model_requests: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "model_requests": self.model_requests,
            "retries": self.retries,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "avg_latency_ms": round(self.latency_ms / self.calls, 1)
            if self.calls
            else 0.0,
            "avg_total_tokens": round(
                (self.input_tokens + self.output_tokens) / self.calls, 1
            )
            if self.calls
            else 0.0,
        }


class IntentAgent:
    """PydanticAI-backed extractor; the deterministic rules still have priority."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        model: Model | None = None,
        request_limit: int = 2,
        timeout_seconds: float = 20.0,
        instrument: bool = True,
    ) -> None:
        try:
            from pydantic_ai import Agent, ModelRetry
            from pydantic_ai.usage import UsageLimits
        except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
            raise RuntimeError("pydantic-ai is not installed.") from exc
        if model is None and not model_name:
            raise ValueError("A model name or model instance is required.")
        self.request_limit = request_limit
        self.timeout_seconds = timeout_seconds
        self.usage = LlmUsage()
        # The ledger names the model that produced the call. A test model is
        # labelled by its class so the row is never blank.
        self.model_name = model_name or type(model).__name__
        self._usage_limits: Any = UsageLimits(request_limit=request_limit)
        target: Any = model if model is not None else model_name
        # OpenTelemetry is the export path, not a dependency: `opentelemetry-api`
        # ships with pydantic-ai-slim, and the global tracer provider is a
        # no-op until an operator installs an SDK/exporter. With it on, one
        # `chat <model>` CLIENT span is emitted per model request, so a
        # validation retry shows up as a second span next to the first.
        self.instrumented = False
        if instrument:
            try:
                from pydantic_ai.models.instrumented import InstrumentedModel

                if not isinstance(target, InstrumentedModel):
                    target = InstrumentedModel(target)
                self.instrumented = True
            except Exception:  # pragma: no cover - never fail a request for telemetry
                logger.warning("model instrumentation unavailable; continuing without it")
        # The model that will actually be called: the wrapped one when
        # instrumentation is on. Kept public so a caller (or a test) can see
        # which path the call takes.
        self.model = target
        self._agent: Any = Agent(
            target,
            output_type=IntentOutput,
            system_prompt=INSTRUCTIONS,
            retries=2,
        )

        @self._agent.output_validator
        def _business_rules(output: IntentOutput) -> IntentOutput:
            """Reject shapes the router must never have to defend against.

            Pydantic already checks types and ranges; these are the business
            rules that used to sit outside the agent. Raising ``ModelRetry``
            sends the reason back to the model, which turns a silent
            ``rules_fallback`` into one visible retry.
            """
            if output.order_id is not None and not _ORDER_ID.fullmatch(
                output.order_id
            ):
                raise ModelRetry(
                    "order_id 必须是 ORD- 开头的订单号，且只能逐字复制；"
                    "没有订单号时给 null。"
                )
            if output.amount_cents is not None and output.amount_cents <= 0:
                raise ModelRetry("amount_cents 必须是正整数分；未给金额时给 null。")
            return output

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: CandidateSet | None = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        payload: dict[str, Any] = {"request": request, "context": context or {}}
        # L1 only: the row id and the server's evidence for it. The catalog's
        # own definitions (L2) and the full twelve rows (L3) are never sent.
        if candidates is not None:
            payload["candidates"] = [
                {"intent_id": candidate.intent_id, "evidence": list(candidate.evidence)}
                for candidate in candidates.candidates
            ]
            payload["allow_none"] = candidates.allow_none
        prompt = json.dumps(payload, ensure_ascii=False)
        started = time.perf_counter()
        try:
            result = self._agent.run_sync(
                prompt,
                usage_limits=self._usage_limits,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            # IntentBrain turns this into a rules fallback, but the attempt
            # still cost time and tokens, so it belongs in the ledger of what
            # the model path cost.
            latency_ms = (time.perf_counter() - started) * 1000
            trace = ModelTrace(
                latency_ms=latency_ms, status="FAILED", instrumented=self.instrumented
            )
            self.usage.calls += 1
            self.usage.failures += 1
            self.usage.latency_ms += latency_ms
            raise ModelCallError(str(exc), trace=trace) from exc
        usage = getattr(result, "usage", None)
        latency_ms = (time.perf_counter() - started) * 1000
        requests = int(getattr(usage, "requests", 0) or 0) if usage is not None else 0
        trace = ModelTrace(
            requests=requests,
            # A run that needed two requests means the first output failed
            # validation and the model was asked again. The counter is
            # derived rather than inferred from a model-specific event.
            retries=max(0, requests - 1),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
            latency_ms=latency_ms,
            instrumented=self.instrumented,
        )
        self.usage.calls += 1
        self.usage.latency_ms += latency_ms
        self.usage.model_requests += trace.requests
        self.usage.retries += trace.retries
        self.usage.input_tokens += trace.input_tokens
        self.usage.output_tokens += trace.output_tokens
        return cast("IntentOutput", result.output), trace


# The closed set of internal decision codes for ``IntentBrain.probe_question``
# (`app/agent/counter_question.yaml`). These ride on
# ``QuestionProbe.reason`` only — they are not new ``ModelTrace`` keys and are
# never customer-visible text.
QUESTION_PROBE_REASONS = (
    "rules_mode",
    "no_candidates",
    "model_failed",
    "model_none",
    "model_unknown",
    "model_out_of_candidates",
    "matches_rule",
)


@dataclass(frozen=True, slots=True)
class QuestionProbe:
    """What the read-only question-gate probe learned, and what it cost.

    ``intent`` is a bare catalog row id — a *reading*, never a routing decision:
    this path must not produce a ``ModelDecision`` and must not touch the four
    adoption-count trace keys, so a probe reading can never be replayed by the
    ledger-reuse branch of ``plan`` (`app/agent/counter_question.yaml`). ``trace``
    is the agent's cost record untouched — ``as_dict`` omits
    the four count keys because they stay ``None``, which is exactly why no
    ``replace`` call appears on this path. ``reason`` is one of
    ``QUESTION_PROBE_REASONS`` when no reading is returned, ``None`` when one
    is; ``asks`` is the single question the caller asks.
    """

    intent: str | None
    candidate_set: CandidateSet | None
    trace: ModelTrace | None
    source: str
    reason: str | None

    @property
    def asks(self) -> bool:
        return self.intent is not None


class IntentBrain:
    """Deterministic routing first; PydanticAI only fills gaps or resolves UNKNOWN."""

    def __init__(
        self,
        *,
        mode: str = "rules",
        agent: IntentAgent | None = None,
        candidates_enabled: bool = True,
        max_candidates: int = 6,
    ) -> None:
        self.mode = mode
        self.agent = agent
        self.candidates_enabled = candidates_enabled
        self.max_candidates = max_candidates

    @property
    def model_name(self) -> str | None:
        """The model label for the usage ledger, or ``None`` when rules-only."""
        return getattr(self.agent, "model_name", None) if self.agent is not None else None

    def _candidates(
        self,
        request: str,
        context: dict[str, Any] | None,
        rule_plan: RoutePlan,
        *,
        order_state: str | None = None,
    ) -> CandidateSet | None:
        """The rows the server has evidence for, or ``None`` when the path is off.

        Reached only on the model path: rules mode returns before this, so a
        rules-only deployment never computes a candidate and never grows a
        trace field. ``order_state`` is forwarded exactly as the caller
        supplied it; the kernel never fetches it itself
        (`app/agent/intents.yaml`).
        """
        if not self.candidates_enabled:
            return None
        return build_candidates(
            request,
            context,
            rule_plan,
            catalog=get_catalog(),
            order_state=order_state,
            max_candidates=self.max_candidates,
        )

    def plan(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        decision: ModelDecision | None = None,
        order_state: str | None = None,
    ) -> tuple[RoutePlan, str, ModelTrace | None]:
        """Route with rules first; the third value is the model's cost record.

        ``None`` means the model was never asked (rules mode, or a rule hit
        outside the gap path). A ``rules_fallback`` still carries a trace:
        the call was made and failed, which is exactly the case a silent
        fallback would hide.

        ``decision`` is the chat entry's record of an earlier call. It is
        reused only when the ledger can independently verify that the request,
        context, rule set and model configuration are still the same; a match
        returns the same reading with ``reused=True`` and no model call. A
        mismatch falls through to the ordinary path, which may call the model
        again — the worst case is one extra call, never an unchecked verdict.

        ``order_state`` is the current order status, fetched by the caller
        under its own tenant/user scope: the chat entry passes it so the
        candidate kernel can record ``order:`` evidence. ``None`` means the
        caller could not resolve one — then there is simply no such evidence
        (`app/agent/intents.yaml`). It defaults to ``None``
        so the task-runtime caller, which has no order resolved, keeps
        working unchanged.
        """
        rule_plan = classify(request, context)
        if (
            decision is not None
            and self.mode == "pydantic_ai"
            and self.agent is not None
            and decision.verifies(
                request=request,
                context=context,
                rule_plan=rule_plan,
                ruleset=RULESET_FINGERPRINT,
                model_name=self.model_name,
            )
        ):
            # Same gate as the fresh path: a stored reading is re-checked
            # against today's ``ADOPTABLE_INTENTS``, never replayed as a
            # verdict. The trace keeps the numbers it was written with (the
            # reason and the reroute counters are carried, not recomputed), so
            # a reuse cannot invent an adoption the chat entry never made.
            intent = decide_adoption(rule_plan.intent, decision.model_intent).intent
            order_id = rule_plan.order_id or decision.model_order_id
            amount_cents = rule_plan.amount_cents
            if amount_cents is None:
                amount_cents = decision.model_amount_cents
            amount_invalid = rule_plan.amount_invalid or (
                amount_cents is not None and amount_cents <= 0
            )
            # 退款原因 / 类型只搬运规则读数：模型不抽取、不采纳，也不为它们补位
            # （口径 §5.6.5）。
            return (
                RoutePlan(
                    intent=intent,
                    order_id=order_id,
                    amount_cents=amount_cents,
                    amount_invalid=amount_invalid,
                    refund_reason=rule_plan.refund_reason,
                    refund_type=rule_plan.refund_type,
                ),
                "pydantic_ai",
                ModelTrace.from_dict(decision.trace, reused=True),
            )

        if self.mode != "pydantic_ai" or self.agent is None:
            return rule_plan, "rules", None

        candidate_set = self._candidates(request, context, rule_plan, order_state=order_state)
        try:
            output, trace = self.agent.parse(request, context, candidate_set)
        except ModelCallError as exc:
            logger.warning("pydantic_ai intent extraction failed; falling back to rules")
            return rule_plan, "rules_fallback", exc.trace
        except Exception:
            logger.warning("pydantic_ai intent extraction failed; falling back to rules")
            return rule_plan, "rules_fallback", ModelTrace(status="FAILED")

        reason: str | None = None
        answer = output.intent
        if candidate_set is not None:
            if answer == NONE_ANSWER:
                # The model agrees the rules could not read this either. It is
                # an answer, not a failure: no retry, no task, no adoption.
                reason = "model_none"
                answer = rule_plan.intent.value
            elif not candidate_set.allows(answer):
                # Out of the set means the set was wrong, not that the model
                # misspoke — asking again would pay twice for the same mistake.
                reason = "model_out_of_candidates"
                answer = rule_plan.intent.value

        # One adoption surface (``ADOPTABLE_INTENTS``), one decision function.
        # An answer outside the catalog (``REFUND_STATUS``) or outside the
        # surface (``TICKET_CREATE``, every read intent) may be a legal answer
        # and still keeps the rule reading. ``answer`` is the *effective* one:
        # ``NONE`` and an out-of-candidate row were already rewritten to the
        # rule reading above, so neither is counted as a refused reroute (they
        # have their own ``model_reason``).
        adoption = decide_adoption(rule_plan.intent, answer)
        intent = adoption.intent

        order_id = rule_plan.order_id or output.order_id
        amount_cents = rule_plan.amount_cents
        if amount_cents is None:
            amount_cents = output.amount_cents
        amount_invalid = rule_plan.amount_invalid or (
            amount_cents is not None and amount_cents <= 0
        )
        # 退款原因 / 类型同样只搬运规则读数：模型不抽取、不采纳（口径 §5.6.5）。
        return (
            RoutePlan(
                intent=intent,
                order_id=order_id,
                amount_cents=amount_cents,
                amount_invalid=amount_invalid,
                refund_reason=rule_plan.refund_reason,
                refund_type=rule_plan.refund_type,
            ),
            "pydantic_ai",
            (
                trace
                if trace is None
                else replace(
                    trace,
                    reason=reason,
                    candidates=None if candidate_set is None else len(candidate_set.candidates),
                    adopted=adoption.adopted,
                    redirect=adoption.redirect,
                    redirect_rejected=adoption.redirect_rejected,
                    redirect_kind=adoption.kind,
                    # The two in-band carriers (see ``ModelTrace``): this is
                    # the only write site, and neither key appears in
                    # ``as_dict``, so the counter-question reading and the
                    # candidate id order cannot leave the turn (the chat
                    # entry consumes them in ``_consult_model`` or drops them).
                    answer=answer,
                    candidate_ids=(
                        None
                        if candidate_set is None
                        else tuple(
                            candidate.intent_id for candidate in candidate_set.candidates
                        )
                    ),
                )
            ),
        )

    def probe_question(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        order_state: str | None = None,
    ) -> QuestionProbe:
        """Ask the model what the rules could not read — and record nothing.

        The question gate's read-only probe (`app/agent/counter_question.yaml`):
        it reuses the same candidate
        kernel (``_candidates``) and the same agent entry (``parse``) as
        ``plan``, then stops. This path must never write a ledger decision row
        and never run the adoption surface — otherwise the ledger-reuse branch
        of ``plan`` could replay a probe reading and the probe would have
        opened refund adoption de facto. A legal new reading (including
        ``REFUND_REQUEST``) comes back as a string for the gate to phrase one
        counter-question around; acting on it stays the write-gate's job.

        The ``trace`` is whatever the call cost, never dropped: a failed probe
        still reaches the usage ledger through the existing ``ModelTrace``
        writer. ``order_state`` is forwarded to the candidate kernel exactly as
        ``plan`` forwards it; the kernel never fetches it itself.
        """
        rule_plan = classify(request, context)
        # Same early-return position as ``plan``: rules mode computes no
        # candidates and never reaches the model (§9 的零调用要求).
        if self.mode != "pydantic_ai" or self.agent is None:
            return QuestionProbe(None, None, None, "rules", "rules_mode")

        candidate_set = self._candidates(
            request, context, rule_plan, order_state=order_state
        )
        try:
            output, trace = self.agent.parse(request, context, candidate_set)
        except ModelCallError as exc:
            logger.warning("question probe model call failed; the cost is still recorded")
            return QuestionProbe(
                None, candidate_set, exc.trace, "rules_fallback", "model_failed"
            )
        except Exception:
            logger.warning("question probe model call failed; the cost is still recorded")
            return QuestionProbe(
                None,
                candidate_set,
                ModelTrace(status="FAILED"),
                "rules_fallback",
                "model_failed",
            )

        answer = output.intent
        if answer == NONE_ANSWER:
            # "None of these fit" is an answer, not a failure: no retry (§3.2).
            return QuestionProbe(None, candidate_set, trace, "pydantic_ai", "model_none")
        if candidate_set is None:
            # Without a set the server cannot verify the reading against
            # evidence, and an unverified reading must not drive a question.
            return QuestionProbe(
                None, None, trace, "pydantic_ai", "no_candidates"
            )
        if not candidate_set.allows(answer):
            # Out of the set means the set was wrong, not the model misspoke —
            # 越界不重试 (`app/agent/intents.yaml`). The gate is
            # the candidate set built from catalog rows (``app/agent/intents.yaml``),
            # not the ``Intent`` enum: a legal catalog row that is not an ``Intent``
            # member (e.g. ``REFUND_STATUS``) is a legal reading and this method
            # returns it verbatim. 探针侧不做任何枚举转换——是否把读数转成
            # ``Intent`` 是 T4 消费端的决定，不属本方法。
            return QuestionProbe(
                None, candidate_set, trace, "pydantic_ai", "model_out_of_candidates"
            )
        if answer == Intent.UNKNOWN.value:
            # UNKNOWN 是无条件候选（``app/agent/candidates.py``），所以
            # ``allows()`` 永远为真；口径 §3.1 条件 4 要求合法读数还须
            # ≠UNKNOWN，这一拒绝必须落在方法自身，不能依赖与规则读数的
            # 比较碰巧成立。UNKNOWN↔UNKNOWN 的 reason 也因此从
            # ``matches_rule`` 前移到 ``model_unknown``。
            return QuestionProbe(None, candidate_set, trace, "pydantic_ai", "model_unknown")
        if answer == rule_plan.intent.value:
            # The model said what the rules already said: no new information,
            # so nothing to confirm.
            return QuestionProbe(None, candidate_set, trace, "pydantic_ai", "matches_rule")
        # A legal reading that adds something the rules did not say. Returned
        # as-is — this is where the probe stops; no adoption surface below it.
        return QuestionProbe(answer, candidate_set, trace, "pydantic_ai", None)
