from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ValidationAppError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class ClarificationRequired(AppError):
    """The request was understood, but a slot is missing or malformed."""

    status_code = 422
    code = "CLARIFICATION_REQUIRED"


class ApprovalExpiredError(ConflictError):
    code = "APPROVAL_EXPIRED"


class ApprovalRequiredError(AppError):
    status_code = 409
    code = "APPROVAL_REQUIRED"


class DependencyUnavailableError(AppError):
    status_code = 503
    code = "DEPENDENCY_UNAVAILABLE"
    retryable = True


class ToolTimeoutError(AppError):
    """The upstream did not answer inside the budget the manifest allows.

    Retryable: the call may not have reached the upstream at all, so the
    same request with the same idempotency key is safe to send again.
    """

    status_code = 504
    code = "TOOL_TIMEOUT"
    retryable = True


class UpstreamUnavailableError(AppError):
    """The upstream answered with a failure the caller may send again."""

    status_code = 503
    code = "UPSTREAM_UNAVAILABLE"
    retryable = True


class UnknownOutcomeError(AppError):
    """The call may or may not have taken effect.

    Not retryable and not a business rejection: repeating it could apply a
    side effect twice. The only way out is to reconcile against the
    authoritative record.
    """

    status_code = 503
    code = "OUTCOME_UNKNOWN"
    retryable = False


class CircuitOpenError(AppError):
    """The breaker for this tool is open, so the call was not attempted."""

    status_code = 503
    code = "CIRCUIT_OPEN"
    retryable = True


class RateLimitExceededError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    retryable = True


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"


class SqlRefusedError(AppError):
    """A question could not be turned into a statement that may run.

    The refusal is not a crash: the question was understood, but the
    planner or the guard declined it. The reason code travels in the
    details so a caller can tell "not understood" from "not permitted".
    """

    status_code = 422
    code = "SQL_REFUSED"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(
            detail or "The question could not be answered from your records.",
            details={"reason": reason},
        )
        self.reason = reason


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_envelope(
    *,
    code: str,
    message: str,
    retryable: bool,
    request_id: str = "unknown",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": request_id,
    }
    if details:
        error["details"] = details
    return {"error": error}


_MAX_VALIDATION_ITEMS = 10


def _validation_error_items(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Field pointers and rule names only; the rejected value stays out.

    A body that fails to parse can arrive as raw bytes, which made the default
    error payload unserialisable and turned a 422 into a 500. Dropping ``input``
    also keeps a secret typed into the wrong field from being echoed back.
    """
    items: list[dict[str, Any]] = []
    for error in exc.errors()[:_MAX_VALIDATION_ITEMS]:
        loc = error.get("loc")
        items.append(
            {
                "loc": (
                    [str(part) for part in loc]
                    if isinstance(loc, (list, tuple))
                    else []
                ),
                "type": str(error.get("type", "invalid")),
                "msg": str(error.get("msg", "Invalid value.")),
            }
        )
    return items


def _envelope(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return error_envelope(
        code=code,
        message=message,
        retryable=retryable,
        request_id=_request_id(request),
        details=details,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                request,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                request,
                code="VALIDATION_ERROR",
                message="Request validation failed.",
                retryable=False,
                details={"errors": _validation_error_items(exc)},
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error request_id=%s", _request_id(request))
        return JSONResponse(
            status_code=500,
            content=_envelope(
                request,
                code="INTERNAL_ERROR",
                message="Internal error. Use request_id to correlate logs.",
                retryable=False,
            ),
        )
