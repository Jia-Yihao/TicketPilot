from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.security import Identity
from app.models.enums import RiskLevel

Idempotency = Literal["required", "optional", "forbidden"]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How often a failing call may be repeated, and with which backoff."""

    max_attempts: int = 1
    backoff_ms: int = 0
    retry_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DegradationStep:
    """One explicit fallback, tried in order when the tool does not deliver.

    The chain is never silent: each step carries the text the user is told,
    so a degraded answer still explains what happened.
    """

    kind: Literal["retry_later", "ask_user", "handoff", "stop"]
    message: str
    action: str = ""


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """The contract of a tool: what it takes, what it returns, how it fails.

    ``params`` is a JSON Schema (type / required / pattern / enum / bounds)
    so the arguments are checked before the handler runs — a wrong order
    number becomes a question, not a failed task.

    ``receipt`` is the sentence the user is shown when the tool succeeds.
    Its placeholders are filled from the tool's own return value, never from
    the request, so an amount or an order number cannot be written by the
    model. The fields it may use are the ones ``result_schema`` declares.
    When the receipt cannot be filled completely it is not rendered at all,
    and the handler's own message stands.
    """

    name: str
    version: str = "1"
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.R0
    side_effect: bool = False
    requires_approval: bool = False
    idempotency: Idempotency = "forbidden"
    timeout_ms: int = 5000
    retry: RetryPolicy = RetryPolicy()
    degradation: tuple[DegradationStep, ...] = ()
    result_schema: dict[str, Any] = field(default_factory=dict)
    scopes: tuple[str, ...] = ()
    receipt: str = ""


@dataclass(frozen=True, slots=True)
class ToolContext:
    identity: Identity
    task_id: str
    approval_id: str | None = None
    idempotency_key: str | None = None
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error_code: str | None = None
    retryable: bool = False


ToolHandler = Callable[[ToolContext, dict[str, Any]], ToolResult]
