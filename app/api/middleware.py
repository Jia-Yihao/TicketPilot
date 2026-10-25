"""Transport-edge guards: token buckets and body caps.

Both middlewares sit outside the request-id and exception middleware so a
rejected request never reaches a route. The rate limiter keeps its buckets in
this process only: with several workers each one has its own budget, which is
a real boundary of the current design, not a hidden one.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import (
    PayloadTooLargeError,
    RateLimitExceededError,
    error_envelope,
)
from app.core.guard import SecurityAction, SecurityCategory, SecuritySeverity
from app.core.rate_limit import RateLimitStore, TokenBucket
from app.services.security_events import SecurityEventService

logger = logging.getLogger(__name__)

__all__ = [
    "BodySizeLimitMiddleware",
    "RateLimitMiddleware",
    "SecurityEventWriter",
]


class SecurityEventWriter:
    """Best-effort event persistence for requests that never reach a route."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def record(
        self,
        *,
        tenant_id: str,
        category: SecurityCategory,
        action: SecurityAction,
        severity: SecuritySeverity = SecuritySeverity.MEDIUM,
        user_id: str | None = None,
        rule_id: str = "",
        source: str = "transport",
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            await run_in_threadpool(
                self._write,
                tenant_id,
                category,
                action,
                severity,
                user_id,
                rule_id,
                source,
                detail or {},
            )
        except Exception:  # pragma: no cover - logging must not reject traffic
            logger.exception("could not persist security event")

    def _write(
        self,
        tenant_id: str,
        category: SecurityCategory,
        action: SecurityAction,
        severity: SecuritySeverity,
        user_id: str | None,
        rule_id: str,
        source: str,
        detail: dict[str, Any],
    ) -> None:
        with self.session_factory() as session:
            SecurityEventService(session).record(
                tenant_id=tenant_id,
                category=category,
                action=action,
                severity=severity,
                user_id=user_id,
                rule_id=rule_id,
                source=source,
                detail=detail,
            )
            session.commit()


def _request_id(scope: Scope) -> str:
    incoming = Headers(scope=scope).get("x-request-id", "").strip()
    return incoming or f"req-{uuid.uuid4().hex}"


async def _reject(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> None:
    response = JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"X-Request-ID": _request_id(scope), **(headers or {})},
    )
    await response(scope, receive, send)


class RateLimitMiddleware:
    """Tenant and IP budgets; either store can reject the request.

    The stores are injected so the transport does not decide where a budget
    lives. The default is process-local; ``app.main`` passes Redis-backed
    stores when configured, which makes the limit survive across workers.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        tenant_per_minute: int,
        ip_per_minute: int,
        enabled: bool = True,
        event_writer: SecurityEventWriter | None = None,
        event_dedupe_seconds: float = 60.0,
        tenant_limiter: RateLimitStore | None = None,
        ip_limiter: RateLimitStore | None = None,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.tenant_limiter = tenant_limiter or TokenBucket(tenant_per_minute)
        self.ip_limiter = ip_limiter or TokenBucket(ip_per_minute)
        self.event_writer = event_writer
        self.event_dedupe_seconds = event_dedupe_seconds
        self._last_event: dict[tuple[str, str], float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not self.enabled
            or not scope.get("path", "").startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        tenant_id = headers.get("x-tenant-id", "").strip() or "anonymous"
        user_id = headers.get("x-user-id", "").strip() or None
        client = scope.get("client")
        ip = str(client[0]) if client else "anonymous"

        tenant_ok, tenant_retry = self.tenant_limiter.consume(f"tenant:{tenant_id}")
        ip_ok, ip_retry = self.ip_limiter.consume(f"ip:{ip}")
        if tenant_ok and ip_ok:
            await self.app(scope, receive, send)
            return

        limit = "tenant" if not tenant_ok else "ip"
        retry_after = max(tenant_retry, ip_retry)
        error = RateLimitExceededError(
            "Request rate limit exceeded.",
            details={
                "limit": limit,
                "retry_after_seconds": retry_after,
                "tenant_limit_per_minute": self.tenant_limiter.per_minute,
                "ip_limit_per_minute": self.ip_limiter.per_minute,
            },
        )
        await self._record(tenant_id, user_id, limit, retry_after)
        await _reject(
            scope,
            receive,
            send,
            status_code=error.status_code,
            payload=error_envelope(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                request_id=_request_id(scope),
                details=error.details,
            ),
            headers={"Retry-After": str(retry_after)},
        )

    async def _record(
        self, tenant_id: str, user_id: str | None, limit: str, retry_after: int
    ) -> None:
        if self.event_writer is None:
            return
        now = time.monotonic()
        key = (tenant_id, limit)
        last = self._last_event.get(key)
        if last is not None and now - last < self.event_dedupe_seconds:
            return
        self._last_event[key] = now
        await self.event_writer.record(
            tenant_id=tenant_id,
            user_id=user_id,
            category=SecurityCategory.RATE_LIMIT,
            action=SecurityAction.BLOCKED,
            severity=SecuritySeverity.LOW,
            rule_id=f"rate_limit.{limit}",
            detail={"limit": limit, "retry_after_seconds": retry_after},
        )


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class BodySizeLimitMiddleware:
    """Reject oversized JSON bodies and a stricter cap for multipart uploads."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        attachment_max_bytes: int,
        event_writer: SecurityEventWriter | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.attachment_max_bytes = attachment_max_bytes
        self.event_writer = event_writer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Bodyless requests (notably the SSE endpoint) must keep the original
        # receive: StreamingResponse uses it to watch for client disconnects.
        if scope.get("method", "").upper() not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_type = headers.get("content-type", "").lower()
        is_multipart = content_type.startswith("multipart/form-data")
        limit = self.attachment_max_bytes if is_multipart else self.max_bytes
        if limit <= 0:
            await self.app(scope, receive, send)
            return

        declared: int | None = None
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = None
        if declared is not None and declared > limit:
            await self._block(
                scope,
                receive,
                send,
                received=declared,
                limit=limit,
                is_multipart=is_multipart,
            )
            return
        if declared == 0 and not headers.get("transfer-encoding", "").lower():
            await self.app(scope, receive, send)
            return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] != "http.request":
                disconnected = True
                break
            body.extend(message.get("body", b""))
            if len(body) > limit:
                await self._block(
                    scope,
                    receive,
                    send,
                    received=len(body),
                    limit=limit,
                    is_multipart=is_multipart,
                )
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": False,
                }
            if disconnected:
                return {"type": "http.disconnect"}
            # Hand later polls back to the server so StreamingResponse can
            # still observe a real client disconnect.
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _block(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        received: int,
        limit: int,
        is_multipart: bool,
    ) -> None:
        error = PayloadTooLargeError(
            "Request body exceeds the configured limit.",
            details={
                "limit_bytes": limit,
                "received_bytes": received,
                "kind": "attachment" if is_multipart else "body",
            },
        )
        await self._record(scope, received, limit, is_multipart)
        await _reject(
            scope,
            receive,
            send,
            status_code=error.status_code,
            payload=error_envelope(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                request_id=_request_id(scope),
                details=error.details,
            ),
        )

    async def _record(
        self, scope: Scope, received: int, limit: int, is_multipart: bool
    ) -> None:
        if self.event_writer is None:
            return
        headers = Headers(scope=scope)
        tenant_id = headers.get("x-tenant-id", "").strip() or "anonymous"
        user_id = headers.get("x-user-id", "").strip() or None
        await self.event_writer.record(
            tenant_id=tenant_id,
            user_id=user_id,
            category=SecurityCategory.PAYLOAD_TOO_LARGE,
            action=SecurityAction.BLOCKED,
            severity=SecuritySeverity.MEDIUM,
            rule_id="body.multipart" if is_multipart else "body.request",
            detail={
                "limit_bytes": limit,
                "received_bytes": received,
                "kind": "attachment" if is_multipart else "body",
            },
        )
