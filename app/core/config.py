from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RefundMode = Literal["success", "fail", "timeout_before", "timeout_after", "unknown"]
OutboundTransportKind = Literal["none", "local_file"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "TicketPilot"
    environment: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "sqlite+pysqlite:///./ticketpilot.db"
    auto_create_tables: bool = True
    docs_enabled: bool = True
    identity_adapter: Literal["dev_headers", "oidc"] = "dev_headers"
    refund_provider: Literal["mock", "http"] = "mock"

    approval_ttl_minutes: int = 30
    policy_max_results: int = 3
    policy_min_score: float = 0.01
    # How many rows one self-service question may pull back.
    analytics_max_limit: int = 200
    # P3-b: controlled SQL and prefetch coexist. The row count comes from a
    # scoped COUNT, never from a model estimate; turning prefetch off restores
    # the P3-a behaviour byte for byte.
    analytics_prefetch_enabled: bool = True
    analytics_prefetch_max_rows: int = 200
    # L3 rows stay in this process only; an expired snapshot asks the
    # customer to re-ask instead of serving stale rows (declared boundary).
    analytics_snapshot_ttl_seconds: int = 900
    # Expression is the only model call on the prefetch path. It stays off in
    # rules mode regardless of this flag.
    analytics_narration_enabled: bool = True

    # Policy thresholds. These are the numbers the agent obeys; nothing in
    # the code may decide a refund window or an escalation trigger on its own.
    refund_policy_version: str = "refund-policy-v3"
    refund_window_days: int = 30
    refund_max_amount_cents: int = 5_000_000
    refund_approval_threshold_cents: int = 0
    escalation_frustration_rounds: int = 2
    # The clarification budget is its own number: the agent asks once, asks again
    # with the customer's own order candidates, and only hands over when the
    # third turn still has no order. Reusing frustration_rounds would hand the
    # conversation over before the customer ever sees a candidate.
    escalation_clarification_rounds: int = 3
    handoff_after_failed_attempts: int = 2
    escalation_no_evidence_score: float = 0.35
    session_idle_minutes: int = 20
    handoff_claim_timeout_seconds: int = 120
    handoff_repeat_window_hours: int = 24
    abandon_idle_minutes: int = 30
    handoff_digest_max_chars: int = 400

    # Retrieval channels. The sparse BM25 channel is always on; the dense and
    # rerank channels are opt-in and fail open, so a broken endpoint degrades
    # the ranking instead of breaking the answer.
    retrieval_dense_enabled: bool = False
    retrieval_rerank_enabled: bool = False
    retrieval_rrf_k: int = 60
    retrieval_candidate_pool: int = 20
    retrieval_dense_min_score: float = 0.0
    # Once dense is on, ask it only where BM25 is blind or poorly covered.
    # Set the flag to false to restore the always-on channel.
    retrieval_dense_gate_enabled: bool = True
    retrieval_dense_gate_min_top1_score: float = 5.0
    retrieval_dense_gate_min_coverage: float = 0.35
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: SecretStr | None = None
    embedding_timeout_seconds: float = 10.0
    embedding_dimensions: int = 256
    rerank_url: str = ""
    rerank_model: str = ""
    rerank_api_key: SecretStr | None = None
    rerank_timeout_seconds: float = 5.0
    rerank_top_n: int = 20
    rerank_failure_threshold: int = 3
    rerank_cooldown_seconds: float = 30.0
    # Query-side rewrite of the *retrieval text* only: the customer's message,
    # the intent route, the slot extraction and the reply keep the original
    # words. The transforms are local string rules (numbers, synonyms, ACTIVE
    # section titles), so the feature is deterministic and reversible; turning
    # the master switch off restores the pre-rewrite ranking.
    retrieval_query_rewrite_enabled: bool = True
    retrieval_query_rewrite_numbers: bool = True
    retrieval_query_rewrite_synonyms: bool = True
    retrieval_query_rewrite_sections: bool = True
    retrieval_query_rewrite_max_section_hints: int = 1
    retrieval_query_rewrite_max_expansions: int = 8

    # P8: the model may only name an intent the server has evidence for. The
    # cap bounds how many evidence-bearing rows are offered; HUMAN_HANDOFF and
    # UNKNOWN are always offered and never take a slot in it. Turning the
    # switch off restores the pre-candidate model face byte for byte.
    intent_candidates_enabled: bool = True
    intent_candidates_max: int = 6

    agent_mode: Literal["rules", "pydantic_ai"] = "rules"
    agent_model: str = "anthropic:claude-sonnet-4-5"
    agent_request_limit: int = 2
    agent_timeout_seconds: float = 20.0
    # Wrap the model in PydanticAI's OpenTelemetry instrumentation: one
    # `chat <model>` span per model request, so a validation retry shows up
    # as a second span. The API ships with pydantic-ai-slim and the global
    # provider is a no-op until an operator installs an exporter, so this
    # costs nothing when nobody is listening.
    agent_otel_instrument: bool = True

    mock_refund_mode: RefundMode = "success"
    mock_refund_delay_seconds: float = 0.0

    # Request-edge guards. The input guard records what it finds and never
    # changes eligibility. The default budget store is process-local; with
    # several workers each one has its own budget. Set RATE_LIMIT_STORE=redis
    # and REDIS_URL to make the tenant/IP limits a shared fact.
    security_events_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_tenant_per_minute: int = 600
    rate_limit_ip_per_minute: int = 300
    rate_limit_store: Literal["memory", "redis"] = "memory"
    redis_url: str = ""
    redis_timeout_seconds: float = 0.5
    # JSON bodies use request_max_bytes; multipart uploads use the stricter
    # attachment cap.
    request_max_bytes: int = 262_144
    attachment_max_bytes: int = 5_242_880

    # Channel adapters. Web is always available because it reuses the dev
    # header identity. Email and IM are opt-in: enabling one in production
    # requires a webhook secret and a sender-to-customer map, otherwise the
    # whole app refuses to start.
    channel_web_enabled: bool = True
    channel_email_enabled: bool = False
    channel_im_enabled: bool = False
    channel_webhook_max_skew_seconds: int = 300
    channel_delivery_claim_timeout_seconds: int = 120
    channel_email_sla_hours: int = 24
    channel_email_secret: SecretStr | None = None
    channel_im_secret: SecretStr | None = None
    channel_email_identity_map: dict[str, str] = Field(default_factory=dict)
    channel_im_identity_map: dict[str, str] = Field(default_factory=dict)

    # Outbound transport for proactive notices. No provider protocol is
    # implemented here, so the only honest choices are to refuse to send
    # ("none", the default) or to hand the rendered message to a local drop
    # file. A drop file is a demonstration sink: "sent" means the message
    # left the application, not that a customer received it.
    channel_outbound_transport: OutboundTransportKind = "none"
    channel_outbound_drop_file: str = ""

    # Whether the answer path reads the customer's own profile at all. The
    # only reader is app/agent/personalization.py, and it applies server-owned
    # templates rather than echoing what the customer wrote. Turning this off
    # returns every reply to the wording from before T9; it does not freeze,
    # hide or change the profile itself.
    memory_personalization_enabled: bool = True

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.environment == "prod":
            if self.database_url.startswith("sqlite"):
                raise ValueError("production requires PostgreSQL, not SQLite")
            if self.auto_create_tables:
                raise ValueError("production must run Alembic; set AUTO_CREATE_TABLES=false")
            if self.identity_adapter != "oidc":
                raise ValueError(
                    "production requires IDENTITY_ADAPTER=oidc; "
                    "dev header identity is for local use only"
                )
            if self.refund_provider != "http":
                raise ValueError(
                    "production requires REFUND_PROVIDER=http; "
                    "the mock provider is for local use only"
                )
            if self.docs_enabled:
                raise ValueError("production must set DOCS_ENABLED=false")
            if self.retrieval_dense_enabled and not (
                self.embedding_base_url and self.embedding_model
            ):
                raise ValueError(
                    "production dense retrieval requires EMBEDDING_BASE_URL and "
                    "EMBEDDING_MODEL; the offline hashing stand-in is not a "
                    "production fallback"
                )
            if self.retrieval_rerank_enabled and not self.rerank_url:
                raise ValueError(
                    "production reranking requires RERANK_URL"
                )
            if self.channel_email_enabled:
                if self.channel_email_secret is None or not (
                    self.channel_email_secret.get_secret_value()
                ):
                    raise ValueError(
                        "production email channel requires CHANNEL_EMAIL_SECRET"
                    )
                if not self.channel_email_identity_map:
                    raise ValueError(
                        "production email channel requires CHANNEL_EMAIL_IDENTITY_MAP"
                    )
            if self.channel_im_enabled:
                if self.channel_im_secret is None or not (
                    self.channel_im_secret.get_secret_value()
                ):
                    raise ValueError(
                        "production IM channel requires CHANNEL_IM_SECRET"
                    )
                if not self.channel_im_identity_map:
                    raise ValueError(
                        "production IM channel requires CHANNEL_IM_IDENTITY_MAP"
                    )
            if self.channel_outbound_transport != "none":
                raise ValueError(
                    "production must keep CHANNEL_OUTBOUND_TRANSPORT=none until a "
                    "real provider protocol is implemented; local_file only "
                    "demonstrates delivery locally"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
