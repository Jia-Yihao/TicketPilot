"""The validation retry, seen from the outside.

The agent rejects a malformed ``order_id`` inside its own contract
(``@agent.output_validator``), which makes the model answer again. Two
observers have to agree on that: the project's own ledger (``model_retries``)
and OpenTelemetry (one ``chat <model>`` span per model request).

The OTel side is checked with a fake tracer provider built on
``opentelemetry-api`` only — the SDK is not a dependency of this project, and
the global tracer provider is a no-op until an operator installs an exporter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import SpanKind
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel

from app.agent.pydantic_agent import IntentAgent


@dataclass
class _FakeSpan:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    ended: bool = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, values: dict[str, Any]) -> None:
        self.attributes.update(values)

    def is_recording(self) -> bool:
        return True

    def end(self) -> None:
        self.ended = True

    def get_span_context(self) -> Any:
        return None

    def record_exception(
        self, exception: BaseException, **kwargs: Any
    ) -> None:
        self.attributes["exception"] = repr(exception)

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        return None

    def update_name(self, name: str) -> None:
        self.name = name

    def add_event(self, *args: Any, **kwargs: Any) -> None:
        return None

    def add_link(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeTracer:
    """The part of the OTel ``Tracer`` protocol the instrumentation uses."""

    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []
        self.calls: list[dict[str, Any]] = []

    def start_span(self, name: str, **kwargs: Any) -> _FakeSpan:
        span = _FakeSpan(name=name)
        span.set_attributes(kwargs.get("attributes") or {})
        self.spans.append(span)
        return span

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Iterator[_FakeSpan]:
        self.calls.append({"name": name, **kwargs})
        span = self.start_span(name, **kwargs)
        try:
            yield span
        finally:
            span.end()


class _FakeTracerProvider:
    """The part of the OTel ``TracerProvider`` protocol pydantic-ai uses."""

    def __init__(self) -> None:
        self.tracer = _FakeTracer()

    def get_tracer(self, *args: Any, **kwargs: Any) -> _FakeTracer:
        return self.tracer


def _scripted(outputs: list[dict[str, Any]]) -> FunctionModel:
    remaining = list(outputs)

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        args = remaining.pop(0) if remaining else outputs[-1]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
        )

    return FunctionModel(respond)


REJECTED = {
    "intent": "REFUND_REQUEST",
    "order_id": "DEMO-1001",
    "amount_cents": 12900,
    "confidence": 0.9,
    "decision_summary": "test",
}
ACCEPTED = {**REJECTED, "order_id": "ORD-DEMO-1001"}


def _instrumented(outputs: list[dict[str, Any]]) -> tuple[IntentAgent, _FakeTracer]:
    provider = _FakeTracerProvider()
    model = InstrumentedModel(
        _scripted(outputs),
        InstrumentationSettings(tracer_provider=provider),  # type: ignore[arg-type]
    )
    return IntentAgent(model=model), provider.tracer


def _model_spans(tracer: _FakeTracer) -> list[_FakeSpan]:
    """The model-request spans; the agent also opens its own run span."""
    return [span for span in tracer.spans if span.name.startswith("chat ")]


def test_a_validation_retry_emits_one_span_per_model_request() -> None:
    agent, tracer = _instrumented([REJECTED, ACCEPTED])

    output, trace = agent.parse("ORD-DEMO-1001 那笔钱给我退回来")

    assert output.order_id == "ORD-DEMO-1001"
    # The ledger says one retry; OpenTelemetry says two model requests.
    assert trace.requests == 2
    assert trace.retries == 1
    assert trace.instrumented is True
    model_spans = _model_spans(tracer)
    assert len(model_spans) == 2
    assert all(span.ended for span in model_spans)
    assert model_spans[0].attributes.get("gen_ai.operation.name") == "chat"


def test_the_agent_wraps_its_model_for_instrumentation() -> None:
    agent = IntentAgent(model=_scripted([ACCEPTED]))

    assert agent.instrumented is True
    assert isinstance(agent.model, InstrumentedModel)


def test_instrumentation_can_be_switched_off() -> None:
    agent = IntentAgent(model=_scripted([ACCEPTED]), instrument=False)

    assert agent.instrumented is False
    assert not isinstance(agent.model, InstrumentedModel)


def test_a_clean_run_emits_one_model_span() -> None:
    agent, tracer = _instrumented([ACCEPTED])

    _output, trace = agent.parse("查订单 ORD-DEMO-1001")

    assert trace.retries == 0
    assert len(_model_spans(tracer)) == 1


def test_the_span_kind_follows_the_otel_convention() -> None:
    # The instrumentation opens a CLIENT span for a model request.
    agent, tracer = _instrumented([ACCEPTED])

    agent.parse("查订单 ORD-DEMO-1001")

    model_calls = [
        call for call in tracer.calls if str(call.get("name", "")).startswith("chat ")
    ]
    assert model_calls and model_calls[0].get("kind") is SpanKind.CLIENT
