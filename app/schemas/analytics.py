from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyticsQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AnalyticsPlanView(BaseModel):
    """What the question was understood to mean."""

    dataset: str
    metric: str
    group_by: str | None = None
    status: str | None = None
    time_range: str | None = None


class AnalyticsQueryResponse(BaseModel):
    query_id: str
    question: str
    plan: AnalyticsPlanView
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int
    duration_ms: int


class AnalyticsQuerySummary(BaseModel):
    query_id: str
    question: str
    dataset: str
    metric: str
    group_by: str | None = None
    row_status: str | None = None
    time_range: str | None = None
    sql_text: str
    row_count: int
    duration_ms: int
    status: str
    rejected_reason: str | None = None
    trace_id: str | None = None
    created_at: str


class AnalyticsQueryListResponse(BaseModel):
    items: list[AnalyticsQuerySummary] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
