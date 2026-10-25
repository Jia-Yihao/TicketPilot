from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import (
    ApprovalRequiredError,
    ConflictError,
    ForbiddenError,
    ValidationAppError,
)
from app.models.enums import ApprovalStatus
from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.tools.base import ToolContext, ToolResult
from app.tools.executor import ExecutionOutcome, ToolExecutionError, ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.schema import format_violations, validate_params


class ToolGateway:
    """Single execution entry: validate, authorize, approval check, audit, dispatch.

    The five gates before the call are not the executor's business and stay
    here. What the executor owns is everything from the call onwards: the
    budget, the retries, the breaker and the degradation step that answers
    a failure.
    """

    def __init__(
        self,
        session: Session,
        registry: ToolRegistry,
        *,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.approvals = ApprovalRepository(session)
        self.audit = AuditRepository(session)
        self.executor = executor or ToolExecutor()

    def call(
        self, name: str, ctx: ToolContext, args: dict[str, Any] | None = None
    ) -> ToolResult:
        """Call a tool and hand back what it returned."""
        return self.call_outcome(name, ctx, args).result

    def call_outcome(
        self, name: str, ctx: ToolContext, args: dict[str, Any] | None = None
    ) -> ExecutionOutcome:
        """Call a tool and hand back how it went.

        The outcome carries the attempt count, the failure kind and the
        degradation step, so a caller that keeps a trace can record them.
        A failed call raises ``ToolExecutionError`` instead of returning.
        """
        tool = self.registry.require(name)
        manifest = tool.manifest
        arguments = args or {}

        self._validate(manifest.name, manifest.params, arguments)
        self._authorize(manifest.name, manifest.scopes, ctx)

        if manifest.requires_approval:
            if not ctx.approval_id:
                raise ApprovalRequiredError(f"Tool {name} requires an approved action.")
            approval = self.approvals.require(ctx.approval_id)
            if approval.task_id != ctx.task_id:
                raise ApprovalRequiredError("Approval does not belong to this task.")
            if approval.status is not ApprovalStatus.APPROVED:
                raise ConflictError(f"Approval is {approval.status.value}, not APPROVED.")

        self._require_idempotency(manifest.name, manifest.idempotency, ctx, arguments)

        try:
            outcome = self.executor.execute(manifest, tool.handler, ctx, arguments)
        except ToolExecutionError as exc:
            self._record_failure(manifest.name, ctx, exc)
            raise
        except Exception as exc:
            self._record_failure(manifest.name, ctx, exc)
            raise

        if outcome.failure is not None:
            error = ToolExecutionError(
                outcome.result.message,
                kind=outcome.failure,
                attempts=outcome.attempts,
                degradation=outcome.degradation,
                code=outcome.code,
                cause=outcome.cause,
            )
            self._record_failure(manifest.name, ctx, error)
            raise error

        if manifest.side_effect:
            self.audit.record(
                actor_id=ctx.identity.user_id,
                tenant_id=ctx.identity.tenant_id,
                action=f"tool.{name}",
                resource_type="tool",
                resource_id=name,
                outcome="SUCCESS" if outcome.result.ok else "FAILED",
                detail={"task_id": ctx.task_id, "message": outcome.result.message[:200]},
            )
        return outcome

    def _record_failure(self, name: str, ctx: ToolContext, exc: Exception) -> None:
        """A failed side effect is recorded whether or not the tool has one."""
        manifest = self.registry.require(name).manifest
        if not manifest.side_effect:
            return
        detail: dict[str, Any] = {"task_id": ctx.task_id, "error": type(exc).__name__}
        if isinstance(exc, ToolExecutionError):
            detail["failure_kind"] = exc.kind.value
            detail["attempts"] = exc.attempts
            detail["degradation"] = (
                exc.degradation.kind if exc.degradation is not None else None
            )
        self.audit.record(
            actor_id=ctx.identity.user_id,
            tenant_id=ctx.identity.tenant_id,
            action=f"tool.{name}",
            resource_type="tool",
            resource_id=name,
            outcome="ERROR",
            detail=detail,
        )

    @staticmethod
    def _validate(
        name: str, schema: dict[str, Any], args: dict[str, Any]
    ) -> None:
        violations = validate_params(schema, args)
        if not violations:
            return
        detail = format_violations(violations)
        raise ValidationAppError(
            f"工具 {name} 的参数不合法：{detail}。",
            details={"tool": name, "violations": [list(item) for item in violations]},
        )

    @staticmethod
    def _authorize(name: str, scopes: tuple[str, ...], ctx: ToolContext) -> None:
        missing = [scope for scope in scopes if not ctx.identity.has_role(scope)]
        if missing:
            raise ForbiddenError(f"Tool {name} requires scope: {'、'.join(missing)}.")

    @staticmethod
    def _require_idempotency(
        name: str,
        mode: str,
        ctx: ToolContext,
        args: dict[str, Any],
    ) -> None:
        if mode != "required":
            return
        key = ctx.idempotency_key or args.get("idempotency_key")
        if not isinstance(key, str) or not key.strip():
            raise ValidationAppError(f"Tool {name} requires an idempotency key.")
