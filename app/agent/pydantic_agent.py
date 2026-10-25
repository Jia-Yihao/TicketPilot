from __future__ import annotations

import json
import logging
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


class IntentOutput(BaseModel):
    intent: IntentLiteral
    order_id: str | None = None
    amount_cents: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)
    decision_summary: str = Field(max_length=300)


INSTRUCTIONS = """你是售后工单系统的受限意图解析器，只做结构化抽取，不执行任何动作。
规则：
1. 只能选择给定的 intent。
2. order_id 只能从用户文本或 context 中逐字复制，不得猜测或编造。
3. 退款金额只提取用户明确给出的金额，转换为整数分；未给出则为 null。
4. 不判断退款资格、不批准退款、不调用工具。
5. decision_summary 只写一句简短依据，不写长篇推理。
"""


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
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "model_requests": self.model_requests,
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
    ) -> None:
        try:
            from pydantic_ai import Agent
            from pydantic_ai.usage import UsageLimits
        except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
            raise RuntimeError("pydantic-ai is not installed.") from exc
        if model is None and not model_name:
            raise ValueError("A model name or model instance is required.")
        self.request_limit = request_limit
        self.timeout_seconds = timeout_seconds
        self.usage = LlmUsage()
        self._usage_limits: Any = UsageLimits(request_limit=request_limit)
        self._agent: Any = Agent(
            model if model is not None else model_name,
            output_type=IntentOutput,
            system_prompt=INSTRUCTIONS,
            retries=2,
        )

    def parse(self, request: str, context: dict[str, Any] | None = None) -> IntentOutput:
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
        except Exception:
            # IntentBrain turns this into a rules fallback, but the attempt
            # still cost time and tokens, so it belongs in the ledger of what
            # the model path cost.
            self.usage.calls += 1
            self.usage.failures += 1
            self.usage.latency_ms += (time.perf_counter() - started) * 1000
            raise
        usage = getattr(result, "usage", None)
        self.usage.calls += 1
        self.usage.latency_ms += (time.perf_counter() - started) * 1000
        if usage is not None:
            self.usage.model_requests += int(getattr(usage, "requests", 0) or 0)
            self.usage.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        return cast("IntentOutput", result.output)


class IntentBrain:
    """Deterministic routing first; PydanticAI only fills gaps or resolves UNKNOWN."""

    def __init__(self, *, mode: str = "rules", agent: IntentAgent | None = None) -> None:
        self.mode = mode
        self.agent = agent

    def plan(
        self, request: str, context: dict[str, Any] | None = None
    ) -> tuple[RoutePlan, str]:
        rule_plan = classify(request, context)
        if self.mode != "pydantic_ai" or self.agent is None:
            return rule_plan, "rules"

        try:
            output = self.agent.parse(request, context)
        except Exception:
            logger.warning("pydantic_ai intent extraction failed; falling back to rules")
            return rule_plan, "rules_fallback"

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
        )
