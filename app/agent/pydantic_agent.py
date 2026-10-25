from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field

from app.agent.routing import RoutePlan, classify
from app.models.enums import Intent

if TYPE_CHECKING:
    from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

IntentLiteral = Literal[
    "POLICY_QA",
    "ORDER_QUERY",
    "LOGISTICS_QUERY",
    "TICKET_CREATE",
    "REFUND_REQUEST",
    "UNKNOWN",
]

# An order id is never "invented": it is copied from the request or context.
# The shape check runs inside the agent contract so a malformed id is retried
# by the model instead of leaking into the routing layer.
_ORDER_ID = re.compile(r"ORD-[A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12})*")


class IntentOutput(BaseModel):
    intent: IntentLiteral
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

    @property
    def failed(self) -> bool:
        return self.status != "OK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_requests": self.requests,
            "model_retries": self.retries,
            "model_input_tokens": self.input_tokens,
            "model_output_tokens": self.output_tokens,
            "model_latency_ms": round(self.latency_ms, 1),
            "model_status": self.status,
            "model_instrumented": self.instrumented,
        }


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
        self, request: str, context: dict[str, Any] | None = None
    ) -> tuple[IntentOutput, ModelTrace]:
        prompt = json.dumps(
            {"request": request, "context": context or {}}, ensure_ascii=False
        )
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


class IntentBrain:
    """Deterministic routing first; PydanticAI only fills gaps or resolves UNKNOWN."""

    def __init__(self, *, mode: str = "rules", agent: IntentAgent | None = None) -> None:
        self.mode = mode
        self.agent = agent

    @property
    def model_name(self) -> str | None:
        """The model label for the usage ledger, or ``None`` when rules-only."""
        return self.agent.model_name if self.agent is not None else None

    def plan(
        self, request: str, context: dict[str, Any] | None = None
    ) -> tuple[RoutePlan, str, ModelTrace | None]:
        """Route with rules first; the third value is the model's cost record.

        ``None`` means the model was never asked (rules mode, or a rule hit
        outside the gap path). A ``rules_fallback`` still carries a trace:
        the call was made and failed, which is exactly the case a silent
        fallback would hide.
        """
        rule_plan = classify(request, context)
        if self.mode != "pydantic_ai" or self.agent is None:
            return rule_plan, "rules", None

        try:
            output, trace = self.agent.parse(request, context)
        except ModelCallError as exc:
            logger.warning("pydantic_ai intent extraction failed; falling back to rules")
            return rule_plan, "rules_fallback", exc.trace
        except Exception:
            logger.warning("pydantic_ai intent extraction failed; falling back to rules")
            return rule_plan, "rules_fallback", ModelTrace(status="FAILED")

        intent = rule_plan.intent
        if intent is Intent.UNKNOWN and output.intent != "UNKNOWN":
            intent = Intent(output.intent)

        order_id = rule_plan.order_id or output.order_id
        amount_cents = rule_plan.amount_cents
        if amount_cents is None:
            amount_cents = output.amount_cents
        amount_invalid = rule_plan.amount_invalid or (
            amount_cents is not None and amount_cents <= 0
        )
        return (
            RoutePlan(
                intent=intent,
                order_id=order_id,
                amount_cents=amount_cents,
                amount_invalid=amount_invalid,
            ),
            "pydantic_ai",
            trace,
        )
