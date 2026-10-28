"""The one model call allowed to phrase an analytics answer.

It is a narrator, not a planner: the prompt is built from server facts, the
output is one sentence, and the caller checks every number against those
facts before showing it. The call is budgeted at one request; a rules-only
deployment never constructs this class at all.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.sql.narrative import (
    FACTUALITY_CONSTRAINT,
    AnalyticsNarrator,
    NarrationCall,
    NarrationPrompt,
)

if TYPE_CHECKING:
    from pydantic_ai.models import Model

__all__ = ["PydanticAnalyticsNarrator", "build_analytics_narrator"]

logger = logging.getLogger(__name__)


class PydanticAnalyticsNarrator:
    """PydanticAI-backed narrator with a single-request budget."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        model: Model | None = None,
        timeout_seconds: float = 20.0,
        instrument: bool = True,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.model_name: str | None = model_name or type(model).__name__
        self.instrumented = False
        self.model: Any = None
        self._agent: Any = None
        self._usage_limits: Any = None
        try:
            self._agent, self.model, self._usage_limits, self.instrumented = (
                self._build(model_name, model, instrument)
            )
        except Exception as exc:
            # A missing key or an uninstalled provider must not stop the app:
            # the statistics answer is deterministic, only the wording is an
            # enhancement. The template answers and the failure is logged.
            logger.warning(
                "analytics narrator unavailable (%s); the template will answer", exc
            )

    def _build(
        self,
        model_name: str | None,
        model: Model | None,
        instrument: bool,
    ) -> tuple[Any, Any, Any, bool]:
        from pydantic_ai import Agent
        from pydantic_ai.usage import UsageLimits

        if model is None and not model_name:
            raise ValueError("A model name or model instance is required.")
        target: Any = model if model is not None else model_name
        instrumented = False
        if instrument:
            try:
                from pydantic_ai.models.instrumented import InstrumentedModel

                if not isinstance(target, InstrumentedModel):
                    target = InstrumentedModel(target)
                instrumented = True
            except Exception:  # pragma: no cover - telemetry is optional
                logger.warning("model instrumentation unavailable; continuing without it")
        agent = Agent(
            target,
            output_type=str,
            system_prompt=FACTUALITY_CONSTRAINT,
            retries=0,
        )
        # A sentence has no schema to retry against; one request is the whole
        # budget. If it fails, the template answers.
        return agent, target, UsageLimits(request_limit=1), instrumented

    def narrate(
        self, prompt: NarrationPrompt
    ) -> tuple[str | None, NarrationCall]:
        if self._agent is None:
            return None, NarrationCall(status="FAILED")
        started = time.perf_counter()
        try:
            result = self._agent.run_sync(
                prompt.user,
                usage_limits=self._usage_limits,
                timeout=self.timeout_seconds,
            )
        except Exception:
            logger.warning("analytics narration failed; falling back to the template")
            return None, NarrationCall(
                latency_ms=(time.perf_counter() - started) * 1000,
                status="FAILED",
            )
        usage = getattr(result, "usage", None)
        requests = int(getattr(usage, "requests", 0) or 0) if usage is not None else 0
        return str(result.output), NarrationCall(
            requests=requests,
            retries=max(0, requests - 1),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def build_analytics_narrator(settings: Settings) -> AnalyticsNarrator | None:
    """The configured narrator, or ``None`` when the path must stay silent.

    ``None`` is the whole switch: the action then takes the template branch
    without touching a model, so ``AGENT_MODE=rules`` really means zero calls.
    """
    if settings.agent_mode != "pydantic_ai":
        return None
    if not settings.analytics_narration_enabled:
        return None
    return PydanticAnalyticsNarrator(
        model_name=settings.agent_model,
        timeout_seconds=settings.agent_timeout_seconds,
        instrument=settings.agent_otel_instrument,
    )
