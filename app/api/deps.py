from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from app.agent.support_agent import SupportAgent
from app.channels.gateway import ChannelGateway
from app.core.config import Settings
from app.core.policy import PolicyEngine
from app.services.runtime import TaskRuntime
from app.sql.service import SqlAnalyticsService


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_policy(request: Request) -> PolicyEngine:
    return cast(PolicyEngine, request.app.state.policy)


def get_runtime(request: Request) -> TaskRuntime:
    return cast(TaskRuntime, request.app.state.runtime)


def get_support_agent(request: Request) -> SupportAgent:
    return cast(SupportAgent, request.app.state.support_agent)


def get_channel_gateway(request: Request) -> ChannelGateway:
    return cast(ChannelGateway, request.app.state.channel_gateway)


def get_analytics(request: Request) -> SqlAnalyticsService:
    return cast(SqlAnalyticsService, request.app.state.analytics)


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
