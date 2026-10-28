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
    route: str = "SQL"
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
    route: str = "SQL"
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


class LlmUsageTotals(BaseModel):
    calls: int
    failed: int
    requests: int
    retries: int
    input_tokens: int
    output_tokens: int
    avg_latency_ms: float


class LlmUsageBySource(LlmUsageTotals):
    source: str


class LlmUsageRow(BaseModel):
    id: str
    source: str
    planning_source: str
    model_name: str | None = None
    conversation_id: str | None = None
    task_id: str | None = None
    requests: int
    retries: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    status: str
    created_at: str


class LlmUsageResponse(BaseModel):
    window_hours: int
    totals: LlmUsageTotals
    by_source: list[LlmUsageBySource] = Field(default_factory=list)
    recent: list[LlmUsageRow] = Field(default_factory=list)
