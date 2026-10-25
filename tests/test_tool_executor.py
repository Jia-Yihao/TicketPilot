"""The six failure forms, one test each.

A tool call can end in six ways. Five of them are the ones the manifest
promises to answer: a bad argument, a timeout, a broken upstream, an outcome
nobody knows, a business rejection and a bug. None of them may pass in
silence: each one has a kind, an attempt count and a degradation step, and
the caller can always tell what happened.

The sixth form is not a tool at all: a question the SQL planner refuses.
It is covered in ``test_tool_failures.py``, where it reaches the customer.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ToolTimeoutError,
    UnknownOutcomeError,
    UpstreamUnavailableError,
    ValidationAppError,
)
from app.core.security import Identity
from app.tools.base import (
    DegradationStep,
    RetryPolicy,
    ToolContext,
    ToolManifest,
    ToolResult,
)
from app.tools.executor import (
    CircuitBreaker,
    FailureKind,
    ToolExecutionError,
    ToolExecutor,
    classify_failure,
    receipt_placeholders,
    render_receipt,
)
from app.tools.gateway import ToolGateway
from app.tools.registry import ToolRegistry


def _ctx() -> ToolContext:
    return ToolContext(identity=Identity("demo", "alice", frozenset()), task_id="task-1")


def _manifest(**overrides: object) -> ToolManifest:
    values: dict[str, object] = {
        "name": "read_order",
        "description": "read an order",
        "params": {"type": "object", "properties": {}, "additionalProperties": False},
        "timeout_ms": 3000,
        "retry": RetryPolicy(max_attempts=3, backoff_ms=100, retry_on=("timeout",)),
        "degradation": (
            DegradationStep(kind="ask_user", message="请再确认一次。", action="clarify"),
            DegradationStep(kind="retry_later", message="稍后自动重试。", action="retry"),
            DegradationStep(kind="stop", message="处理中，稍后通知你。", action="pending"),
            DegradationStep(kind="handoff", message="已转人工。", action="handoff"),
        ),
    }
    values.update(overrides)
    return ToolManifest(**values)  # type: ignore[arg-type]


def _raising(exc: BaseException, calls: list[int]):
    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        calls.append(ctx.attempt)
        raise exc

    return handler


# ----------------------------------------------------------------- the six forms


def test_a_bad_argument_becomes_a_question_and_is_not_retried() -> None:
    """Form 1: validation. The caller gets a question, not a failure."""
    calls: list[int] = []
    executor = ToolExecutor()
    manifest = _manifest(
        retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=("timeout",))
    )

    outcome = executor.execute(
        manifest, _raising(ValidationAppError("参数不合法。"), calls), _ctx(), {}
    )

    assert outcome.failure is FailureKind.VALIDATION
    assert outcome.attempts == 1
    assert calls == [1]
    assert outcome.degradation is not None
    assert outcome.degradation.kind == "ask_user"
    assert outcome.result.message == "请再确认一次。"
    assert outcome.result.ok is False


def test_a_timeout_is_retried_and_ends_as_retry_later() -> None:
    """Form 2: timeout. Retryable, so the policy repeats it."""
    calls: list[int] = []
    executor = ToolExecutor()
    manifest = _manifest(
        retry=RetryPolicy(max_attempts=3, backoff_ms=100, retry_on=("timeout",))
    )

    outcome = executor.execute(
        manifest, _raising(ToolTimeoutError("上游超时。"), calls), _ctx(), {}
    )

    assert outcome.failure is FailureKind.TIMEOUT
    assert outcome.attempts == 3
    assert calls == [1, 2, 3]
    assert outcome.degradation is not None
    assert outcome.degradation.kind == "retry_later"
    assert outcome.result.retryable is True


def test_a_broken_upstream_is_retried_and_then_the_breaker_opens() -> None:
    """Form 3: upstream. After the threshold the call is not attempted."""
    calls: list[int] = []
    executor = ToolExecutor(breaker=CircuitBreaker(failure_threshold=2))
    manifest = _manifest(
        retry=RetryPolicy(max_attempts=1, backoff_ms=0, retry_on=("upstream",))
    )
    handler = _raising(UpstreamUnavailableError("上游 503。"), calls)

    first = executor.execute(manifest, handler, _ctx(), {})
    second = executor.execute(manifest, handler, _ctx(), {})
    third = executor.execute(manifest, handler, _ctx(), {})

    assert first.failure is FailureKind.UPSTREAM
    assert second.failure is FailureKind.UPSTREAM
    assert calls == [1, 1]
    assert third.attempts == 0
    assert third.code == "CIRCUIT_OPEN"
    assert third.circuit_state == "open"
    assert third.result.retryable is True


def test_an_unknown_outcome_is_never_repeated() -> None:
    """Form 4: the call may or may not have taken effect. Repeating is unsafe."""
    calls: list[int] = []
    executor = ToolExecutor()
    manifest = _manifest(
        side_effect=True,
        idempotency="required",
        retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=("timeout", "unknown")),
    )

    outcome = executor.execute(
        manifest, _raising(UnknownOutcomeError("结果未知。"), calls), _ctx(), {}
    )

    assert outcome.failure is FailureKind.UNKNOWN
    assert outcome.attempts == 1
    assert calls == [1]
    assert outcome.degradation is not None
    assert outcome.degradation.kind == "stop"
    assert outcome.result.retryable is False


def test_a_business_rejection_is_not_retried() -> None:
    """Form 5: the tool answered, and the answer is no."""
    calls: list[int] = []
    executor = ToolExecutor()
    manifest = _manifest(
        retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=("timeout", "upstream"))
    )

    outcome = executor.execute(
        manifest, _raising(ConflictError("该订单已有退款在处理。"), calls), _ctx(), {}
    )

    assert outcome.failure is FailureKind.BUSINESS
    assert outcome.attempts == 1
    assert calls == [1]
    assert outcome.degradation is not None
    assert outcome.degradation.kind == "stop"


def test_a_bug_is_handed_to_a_human() -> None:
    """The form nobody planned for still gets an answer."""
    calls: list[int] = []
    executor = ToolExecutor()

    outcome = executor.execute(
        _manifest(), _raising(RuntimeError("boom"), calls), _ctx(), {}
    )

    assert outcome.failure is FailureKind.INTERNAL
    assert outcome.degradation is not None
    assert outcome.degradation.kind == "handoff"
    assert outcome.result.retryable is False


# --------------------------------------------------------------- the two rules


def test_a_side_effect_is_not_repeated_unless_the_tool_is_idempotent() -> None:
    calls: list[int] = []
    executor = ToolExecutor()
    manifest = _manifest(
        side_effect=True,
        idempotency="forbidden",
        retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=("timeout",)),
    )

    outcome = executor.execute(
        manifest, _raising(ToolTimeoutError("超时。"), calls), _ctx(), {}
    )

    assert outcome.attempts == 1
    assert calls == [1]


def test_an_idempotent_side_effect_may_be_repeated() -> None:
    calls: list[int] = []
    executor = ToolExecutor()
    manifest = _manifest(
        side_effect=True,
        idempotency="required",
        retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=("timeout",)),
    )

    outcome = executor.execute(
        manifest, _raising(ToolTimeoutError("超时。"), calls), _ctx(), {}
    )

    assert outcome.attempts == 3
    assert calls == [1, 2, 3]


def test_the_backoff_grows_between_attempts() -> None:
    slept: list[float] = []
    executor = ToolExecutor(sleeper=slept.append)
    manifest = _manifest(
        retry=RetryPolicy(max_attempts=3, backoff_ms=200, retry_on=("timeout",))
    )

    executor.execute(manifest, _raising(ToolTimeoutError("超时。"), []), _ctx(), {})

    assert slept == [0.2, 0.4]


# ------------------------------------------------------------------- receipts


def _receipt_manifest(**overrides: object) -> ToolManifest:
    values: dict[str, object] = {
        "name": "read_order",
        "result_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "status": {"type": "string"},
                "total_cents": {"type": "integer"},
                "currency": {"type": "string"},
            },
        },
        "receipt": "订单 {order_id} 状态 {status}，金额 {total_cents:yuan} {currency}。",
    }
    values.update(overrides)
    return ToolManifest(**values)  # type: ignore[arg-type]


def test_a_receipt_is_rendered_from_the_tools_own_return_value() -> None:
    manifest = _receipt_manifest()

    text = render_receipt(
        manifest,
        {
            "order_id": "ORD-DEMO-1001",
            "status": "SHIPPED",
            "total_cents": 12900,
            "currency": "CNY",
        },
    )

    assert text == "订单 ORD-DEMO-1001 状态 SHIPPED，金额 129.00 CNY。"


def test_a_receipt_may_not_quote_a_field_the_result_schema_does_not_declare() -> None:
    manifest = _receipt_manifest(receipt="订单 {order_id} 金额 {secret_cents:yuan}。")

    assert render_receipt(manifest, {"order_id": "ORD-1", "secret_cents": 1}) == ""


def test_a_receipt_with_a_missing_field_is_not_rendered_at_all() -> None:
    manifest = _receipt_manifest()

    assert render_receipt(manifest, {"order_id": "ORD-1", "status": "SHIPPED"}) == ""


def test_the_registry_refuses_a_receipt_that_quotes_an_undeclared_field() -> None:
    registry = ToolRegistry()
    manifest = _receipt_manifest(receipt="订单 {order_id} 金额 {secret_cents:yuan}。")

    with pytest.raises(ValueError) as caught:
        registry.register(manifest, lambda ctx, args: ToolResult(ok=True))

    assert "secret_cents" in str(caught.value)


def test_the_receipt_placeholders_are_readable() -> None:
    assert receipt_placeholders("订单 {order_id} 金额 {total_cents:yuan}。") == [
        "order_id",
        "total_cents",
    ]


def test_a_successful_call_answers_with_the_receipt() -> None:
    executor = ToolExecutor()

    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(
            ok=True,
            data={
                "order_id": "ORD-DEMO-1001",
                "status": "SHIPPED",
                "total_cents": 12900,
                "currency": "CNY",
            },
            message="handler words",
        )

    outcome = executor.execute(_receipt_manifest(), handler, _ctx(), {})

    assert outcome.result.message == "订单 ORD-DEMO-1001 状态 SHIPPED，金额 129.00 CNY。"


def test_the_handler_message_stands_when_the_receipt_cannot_be_filled() -> None:
    executor = ToolExecutor()

    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, data={"order_id": "ORD-DEMO-1001"}, message="handler words")

    outcome = executor.execute(_receipt_manifest(), handler, _ctx(), {})

    assert outcome.result.message == "handler words"


def test_the_builtin_receipts_only_quote_declared_fields(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        manifests = services.gateway.registry.manifests()

    for manifest in manifests:
        declared = manifest.result_schema.get("properties", {})
        for name in receipt_placeholders(manifest.receipt):
            assert name in declared, f"{manifest.name}.receipt quotes {name}"


# -------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (ValidationAppError("x"), FailureKind.VALIDATION),
        (ToolTimeoutError("x"), FailureKind.TIMEOUT),
        (UpstreamUnavailableError("x"), FailureKind.UPSTREAM),
        (DependencyUnavailableError("x"), FailureKind.UPSTREAM),
        (UnknownOutcomeError("x"), FailureKind.UNKNOWN),
        (ConflictError("x"), FailureKind.BUSINESS),
        (RuntimeError("x"), FailureKind.INTERNAL),
    ],
)
def test_an_exception_is_classified_into_one_kind(exc: BaseException, kind: FailureKind) -> None:
    assert classify_failure(exc) is kind


def test_a_failed_call_raises_with_its_kind_and_degradation(app: FastAPI) -> None:
    registry = ToolRegistry()
    manifest = _manifest(retry=RetryPolicy(max_attempts=1, backoff_ms=0, retry_on=()))
    registry.register(
        manifest, lambda ctx, args: (_ for _ in ()).throw(ConflictError("拒绝了。"))
    )
    with app.state.session_factory() as session:
        gateway = ToolGateway(session, registry)

        with pytest.raises(ToolExecutionError) as caught:
            gateway.call_outcome("read_order", _ctx(), {})

    assert caught.value.kind is FailureKind.BUSINESS
    assert caught.value.degradation is not None
    assert caught.value.degradation.kind == "stop"
