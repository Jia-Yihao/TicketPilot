"""Running a tool under the contract its manifest declares.

A manifest already says how a tool may fail: how long it may take, how often
it may be repeated, and what the user is told when it does not deliver. Until
now that contract was only a comment. This module is where it becomes
behaviour.

Three pieces, in the order they matter:

* **classification** — a raised exception becomes a *failure kind*, so the
  retry policy and the degradation chain can both reason about it without
  knowing the transport;
* **the breaker** — a per-tool circuit that stops a broken upstream from
  being hammered, and lets a single probe through once it recovers;
* **the executor** — it runs the handler, repeats it while the retry policy
  allows, and picks the degradation step that matches the failure.

Two rules keep this safe:

* a **side effect is never repeated unless the tool is idempotent**. The
  manifest says so and the executor reads it, so a new tool cannot get this
  wrong by accident;
* an **unknown outcome is never repeated**. Repeating it could apply a side
  effect twice; only reconciling against the authoritative record can say
  what happened.

Two things this module deliberately does not do:

* it does **not** interrupt a handler that has already started. A handler may
  be halfway through a write, and killing it mid-write is worse than waiting.
  ``timeout_ms`` is the budget we are willing to accept, not a kill switch;
  the upstream owns its own deadline;
* it does **not** swallow the failure. Every failed call comes back with its
  kind, its attempt count and the degradation step that answers it, so no
  failure can be silent.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.errors import (
    AppError,
    ApprovalRequiredError,
    CircuitOpenError,
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    NotFoundError,
    ToolTimeoutError,
    UnknownOutcomeError,
    UpstreamUnavailableError,
    ValidationAppError,
)
from app.core.money import format_cents
from app.tools.base import DegradationStep, ToolContext, ToolManifest, ToolResult

__all__ = [
    "CircuitBreaker",
    "ExecutionOutcome",
    "FailureKind",
    "ToolExecutionError",
    "ToolExecutor",
    "classify_failure",
    "receipt_placeholders",
    "render_receipt",
]


class FailureKind(StrEnum):
    """Why a call did not deliver. The kind decides what happens next."""

    VALIDATION = "validation"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    UNKNOWN = "unknown"
    BUSINESS = "business"
    INTERNAL = "internal"


_FIELD = re.compile(r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<spec>[a-z]+))?\}")


# The degradation step that answers each kind. The manifest's chain is
# searched for the first step of this kind; a chain that does not offer one
# falls through to ``handoff`` and then to no answer at all.
_KIND_TO_STEP: dict[FailureKind, str] = {
    FailureKind.VALIDATION: "ask_user",
    FailureKind.TIMEOUT: "retry_later",
    FailureKind.UPSTREAM: "retry_later",
    FailureKind.UNKNOWN: "stop",
    FailureKind.BUSINESS: "stop",
    FailureKind.INTERNAL: "handoff",
}


def classify_failure(exc: BaseException) -> FailureKind:
    """Turn a raised exception into the kind of failure it is."""
    if isinstance(exc, UnknownOutcomeError):
        return FailureKind.UNKNOWN
    if isinstance(exc, (ToolTimeoutError, TimeoutError)):
        return FailureKind.TIMEOUT
    if isinstance(exc, (UpstreamUnavailableError, DependencyUnavailableError)):
        return FailureKind.UPSTREAM
    if isinstance(exc, ValidationAppError):
        return FailureKind.VALIDATION
    if isinstance(exc, (NotFoundError, ForbiddenError, ConflictError, ApprovalRequiredError)):
        return FailureKind.BUSINESS
    return FailureKind.INTERNAL


def pick_degradation(
    steps: tuple[DegradationStep, ...], kind: FailureKind
) -> DegradationStep | None:
    """The step that answers this kind of failure, if the manifest has one."""
    wanted = _KIND_TO_STEP[kind]
    for step in steps:
        if step.kind == wanted:
            return step
    for step in steps:
        if step.kind == "handoff":
            return step
    return None


def render_receipt(manifest: ToolManifest, data: dict[str, Any]) -> str:
    """Fill the manifest's receipt from the tool's own return value.

    Only the fields the result schema declares may be used, and only the
    value the tool returned may fill them: a receipt cannot quote a number
    the tool never produced. A receipt with a missing field is not rendered
    at all — half a sentence is worse than the handler's own words.
    """
    template = manifest.receipt
    if not template:
        return ""
    allowed = manifest.result_schema.get("properties", {})
    values: dict[str, str] = {}
    for name, spec in _receipt_fields(template):
        if name not in allowed:
            return ""
        value = data.get(name)
        if value is None:
            return ""
        values[name] = _format(value, spec)

    def _fill(match: re.Match[str]) -> str:
        name = match.group("name")
        return values.get(name, "")

    return _FIELD.sub(_fill, template)


def receipt_placeholders(template: str) -> list[str]:
    """The field names a receipt template quotes."""
    return [name for name, _spec in _receipt_fields(template)]


def _receipt_fields(template: str) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for match in _FIELD.finditer(template):
        fields.append((match.group("name"), match.group("spec") or ""))
    return fields


def _format(value: Any, spec: str) -> str:
    if spec == "yuan" and isinstance(value, int) and not isinstance(value, bool):
        return format_cents(value)
    if spec == "list" and isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False


class CircuitBreaker:
    """A per-tool breaker: closed until the tool fails too often.

    The state is in memory and per process. A deployment with several
    instances gets one breaker each, which is why the state is not treated
    as a correctness mechanism — it is a load-shedding device.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_timeout_ms: int = 30_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout_s = max(0.0, reset_timeout_ms / 1000)
        self._clock = clock
        self._states: dict[str, _BreakerState] = {}

    def state(self, name: str) -> str:
        state = self._states.get(name)
        if state is None or state.opened_at is None:
            return "closed"
        if self._clock() - state.opened_at >= self.reset_timeout_s:
            return "half_open"
        return "open"

    def acquire(self, name: str) -> bool:
        """Whether a call may be attempted now.

        A half-open breaker lets exactly one probe through, so a recovering
        upstream is not asked twice at the same moment.
        """
        state = self._states.setdefault(name, _BreakerState())
        if state.opened_at is None:
            return True
        if self._clock() - state.opened_at < self.reset_timeout_s:
            return False
        if state.probing:
            return False
        state.probing = True
        return True

    def record_success(self, name: str) -> None:
        state = self._states.setdefault(name, _BreakerState())
        state.failures = 0
        state.opened_at = None
        state.probing = False

    def record_failure(self, name: str) -> None:
        state = self._states.setdefault(name, _BreakerState())
        state.failures += 1
        state.probing = False
        if state.opened_at is not None:
            state.opened_at = self._clock()
        elif state.failures >= self.failure_threshold:
            state.opened_at = self._clock()

    def reset(self, name: str | None = None) -> None:
        if name is None:
            self._states.clear()
        else:
            self._states.pop(name, None)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What one call cost and where it ended."""

    result: ToolResult
    attempts: int
    failure: FailureKind | None
    code: str | None
    degradation: DegradationStep | None
    duration_ms: int
    over_budget: bool
    circuit_state: str
    cause: BaseException | None = field(default=None, compare=False)


class ToolExecutionError(AppError):
    """A tool did not deliver, after every attempt its policy allowed."""

    status_code = 503
    code = "TOOL_EXECUTION_FAILED"

    def __init__(
        self,
        message: str,
        *,
        kind: FailureKind,
        attempts: int,
        degradation: DegradationStep | None = None,
        code: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code or "TOOL_EXECUTION_FAILED",
            details={"kind": kind.value, "attempts": attempts},
            retryable=kind in (FailureKind.TIMEOUT, FailureKind.UPSTREAM),
        )
        self.kind = kind
        self.attempts = attempts
        self.degradation = degradation
        self.cause = cause


class ToolExecutor:
    """Runs a tool the way its manifest says it may be run."""

    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleeper
        self._clock = clock

    def execute(
        self,
        manifest: ToolManifest,
        handler: Callable[[ToolContext, dict[str, Any]], ToolResult],
        ctx: ToolContext,
        args: dict[str, Any],
    ) -> ExecutionOutcome:
        """Run the tool and report where it ended."""
        started = self._clock()

        if not self.breaker.acquire(manifest.name):
            return self._refused(manifest, started)

        attempt = 0
        while True:
            attempt += 1
            attempt_ctx = ToolContext(
                identity=ctx.identity,
                task_id=ctx.task_id,
                approval_id=ctx.approval_id,
                idempotency_key=ctx.idempotency_key,
                attempt=attempt,
            )
            try:
                result = handler(attempt_ctx, args)
            except AppError as exc:
                kind = classify_failure(exc)
                self.breaker.record_failure(manifest.name)
                if self._may_retry(manifest, kind, attempt):
                    self._sleep(self._backoff_s(manifest, attempt))
                    continue
                return self._failed(manifest, kind, exc, attempt, started)
            except Exception as exc:  # noqa: BLE001 - classified, never swallowed
                self.breaker.record_failure(manifest.name)
                return self._failed(manifest, FailureKind.INTERNAL, exc, attempt, started)

            elapsed_ms = int((self._clock() - started) * 1000)
            if not result.ok and result.retryable:
                kind = _kind_of_result(result)
                self.breaker.record_failure(manifest.name)
                if self._may_retry(manifest, kind, attempt):
                    self._sleep(self._backoff_s(manifest, attempt))
                    continue
                return self._failed(
                    manifest,
                    kind,
                    _result_error(result),
                    attempt,
                    started,
                    message=result.message,
                )

            self.breaker.record_success(manifest.name)
            receipt = render_receipt(manifest, result.data)
            if receipt:
                result = ToolResult(
                    ok=result.ok,
                    data=result.data,
                    message=receipt,
                    error_code=result.error_code,
                    retryable=result.retryable,
                )
            return ExecutionOutcome(
                result=result,
                attempts=attempt,
                failure=None,
                code=result.error_code,
                degradation=None,
                duration_ms=elapsed_ms,
                over_budget=elapsed_ms > manifest.timeout_ms,
                circuit_state=self.breaker.state(manifest.name),
            )

    # ------------------------------------------------------------- internals
    def _may_retry(self, manifest: ToolManifest, kind: FailureKind, attempt: int) -> bool:
        if attempt >= manifest.retry.max_attempts:
            return False
        if kind.value not in manifest.retry.retry_on:
            return False
        if kind is FailureKind.UNKNOWN:
            # The call may already have taken effect. Repeating it would ask
            # for the same effect twice, and only a reconciliation against the
            # authoritative record can say what actually happened.
            return False
        if manifest.side_effect and manifest.idempotency != "required":
            return False
        return True

    def _backoff_s(self, manifest: ToolManifest, attempt: int) -> float:
        base = max(0, manifest.retry.backoff_ms)
        return float(base * (1 << (attempt - 1))) / 1000

    def _failed(
        self,
        manifest: ToolManifest,
        kind: FailureKind,
        cause: BaseException,
        attempt: int,
        started: float,
        *,
        message: str = "",
    ) -> ExecutionOutcome:
        step = pick_degradation(manifest.degradation, kind)
        text = message or str(cause) or "The tool did not deliver."
        code = getattr(cause, "code", None) or "TOOL_EXECUTION_FAILED"
        return ExecutionOutcome(
            result=ToolResult(
                ok=False,
                data={"failure_kind": kind.value, "attempts": attempt},
                message=step.message if step is not None else text,
                error_code=code,
                retryable=kind in (FailureKind.TIMEOUT, FailureKind.UPSTREAM),
            ),
            attempts=attempt,
            failure=kind,
            code=code,
            degradation=step,
            duration_ms=int((self._clock() - started) * 1000),
            over_budget=False,
            circuit_state=self.breaker.state(manifest.name),
            cause=cause,
        )

    def _refused(self, manifest: ToolManifest, started: float) -> ExecutionOutcome:
        kind = FailureKind.UPSTREAM
        step = pick_degradation(manifest.degradation, kind)
        return ExecutionOutcome(
            result=ToolResult(
                ok=False,
                data={"failure_kind": kind.value, "attempts": 0},
                message=step.message if step is not None else "The tool is not accepting calls.",
                error_code=CircuitOpenError.code,
                retryable=True,
            ),
            attempts=0,
            failure=kind,
            code=CircuitOpenError.code,
            degradation=step,
            duration_ms=int((self._clock() - started) * 1000),
            over_budget=False,
            circuit_state=self.breaker.state(manifest.name),
        )


def _kind_of_result(result: ToolResult) -> FailureKind:
    code = (result.error_code or "").upper()
    if code == "TOOL_TIMEOUT":
        return FailureKind.TIMEOUT
    if code in {"OUTCOME_UNKNOWN", "TOOL_UNKNOWN"}:
        return FailureKind.UNKNOWN
    if code in {"UPSTREAM_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"}:
        return FailureKind.UPSTREAM
    if code in {"VALIDATION_ERROR", "CLARIFICATION_REQUIRED"}:
        return FailureKind.VALIDATION
    return FailureKind.BUSINESS


def _result_error(result: ToolResult) -> AppError:
    return AppError(
        result.message or "The tool reported a failure.",
        code=result.error_code or "TOOL_EXECUTION_FAILED",
    )
