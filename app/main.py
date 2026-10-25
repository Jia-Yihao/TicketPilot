from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

import app.models  # noqa: F401  (register SQLAlchemy metadata)
from app.agent.pydantic_agent import IntentAgent, IntentBrain
from app.agent.support_agent import SupportAgent
from app.api.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    SecurityEventWriter,
)
from app.api.v1 import (
    analytics,
    approvals,
    channels,
    handoffs,
    health,
    memory,
    outbox,
    security,
    support,
    tasks,
    tickets,
    traces,
)
from app.channels.gateway import ChannelGateway
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, install_request_id
from app.core.policy import PolicyEngine
from app.db.session import build_engine, build_session_factory, create_all
from app.services.runtime import TaskRuntime
from app.sql.service import SqlAnalyticsService


class ConsoleStaticFiles(StaticFiles):
    """Serve the built console and fall back to index.html for SPA routes."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise


def find_console_dir() -> Path | None:
    for base in (Path(__file__).resolve().parent.parent, Path.cwd()):
        candidate = base / "web" / "dist"
        if (candidate / "index.html").is_file():
            return candidate
    return None


def build_brain(settings: Settings) -> IntentBrain:
    if settings.agent_mode == "pydantic_ai":
        return IntentBrain(
            mode="pydantic_ai",
            agent=IntentAgent(
                model_name=settings.agent_model,
                request_limit=settings.agent_request_limit,
                timeout_seconds=settings.agent_timeout_seconds,
            ),
        )
    return IntentBrain(mode="rules")


def create_app(
    settings: Settings | None = None, *, brain: IntentBrain | None = None
) -> FastAPI:
    settings = settings or get_settings()
    if settings.identity_adapter != "dev_headers":
        raise RuntimeError(
            "IDENTITY_ADAPTER=oidc is configured but no verified OIDC adapter is implemented; "
            "refusing to start."
        )
    if settings.refund_provider != "mock":
        raise RuntimeError(
            "REFUND_PROVIDER=http is configured but no real refund provider is implemented; "
            "refusing to start."
        )
    configure_logging()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    if settings.auto_create_tables:
        create_all(engine)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Deterministic after-sales workflow with approvals, idempotency "
            "and reconciliation."
        ),
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    runtime = TaskRuntime(session_factory, settings, brain=brain or build_brain(settings))
    policy = PolicyEngine.from_settings(settings)
    sql_analytics = SqlAnalyticsService(max_limit=settings.analytics_max_limit)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.runtime = runtime
    app.state.policy = policy
    app.state.analytics = sql_analytics
    support_agent = SupportAgent(
        session_factory, settings, runtime, policy=policy, analytics=sql_analytics
    )
    app.state.support_agent = support_agent
    app.state.channel_gateway = ChannelGateway(
        session_factory, settings, support_agent
    )

    install_request_id(app)
    install_error_handlers(app)
    edge_events = (
        SecurityEventWriter(session_factory)
        if settings.security_events_enabled
        else None
    )
    # Added bottom-up: the rate limiter becomes the outermost guard, then the
    # body cap, then the request-id middleware installed above.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.request_max_bytes,
        attachment_max_bytes=settings.attachment_max_bytes,
        event_writer=edge_events,
    )
    app.add_middleware(
        RateLimitMiddleware,
        tenant_per_minute=settings.rate_limit_tenant_per_minute,
        ip_per_minute=settings.rate_limit_ip_per_minute,
        enabled=settings.rate_limit_enabled,
        event_writer=edge_events,
    )
    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(approvals.router)
    app.include_router(tickets.router)
    app.include_router(support.router)
    app.include_router(channels.router)
    app.include_router(memory.router)
    app.include_router(traces.router)
    app.include_router(security.router)
    app.include_router(handoffs.router)
    app.include_router(analytics.router)
    app.include_router(outbox.router)

    console_dir = find_console_dir()
    if console_dir is not None:
        app.mount(
            "/console",
            ConsoleStaticFiles(directory=console_dir, html=True),
            name="console",
        )

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        payload = {"service": settings.app_name, "health": "/health/ready"}
        if settings.docs_enabled:
            payload["docs"] = "/docs"
        if console_dir is not None:
            payload["console"] = "/console/"
        return payload

    return app
