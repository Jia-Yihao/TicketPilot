from __future__ import annotations

from typing import Any

from scripts.sample_traces import _channel, sample, severity, signalize


def test_channel_prefers_the_handoff_label_then_the_task_context() -> None:
    task = {"context": {"channel": "email"}}
    assert _channel({"channel": "WECHAT"}, task) == "WECHAT"
    assert _channel(None, task) == "EMAIL"
    assert _channel(None, {"context": {}}) == "WEB"


def _span(
    name: str,
    *,
    kind: str = "tool",
    status: str = "OK",
    attributes: dict[str, Any] | None = None,
    latency_ms: int = 0,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "status": status,
        "attributes": attributes or {},
        "latency_ms": latency_ms,
    }


def _record(
    trace_id: str,
    *,
    channel: str = "WEB",
    intent: str = "ORDER_QUERY",
    score: int = 3,
    started_at: str = "2026-10-24T10:00:00",
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "channel": channel,
        "intent": intent,
        "score": score,
        "severity": "high" if score >= 3 else "low",
        "started_at": started_at,
    }


def test_signalize_names_the_failure() -> None:
    spans = [
        _span(
            "support.classify",
            kind="understand",
            attributes={"guard_categories": ["INJECTION"]},
        ),
        _span("policy.search", kind="retrieve", status="ERROR"),
        _span("support.reply", kind="generate", attributes={"clarified": True}),
        _span("handoff.escalate", kind="handoff", attributes={"reason": "NO_EVIDENCE"}),
        _span("output.grounded", kind="guard", attributes={"replacements": 1}),
    ]
    signals = signalize(spans, None, None, slow_ms=5000)
    assert signals == [
        "span_error",
        "input_guard",
        "clarification",
        "handoff",
        "no_evidence",
        "unsupported_claim",
    ]
    assert severity(signals) == "high"


def test_signalize_reads_task_and_degradation() -> None:
    task = {"status": "FAILED", "result": {"outcome": "NEEDS_HUMAN_REVIEW"}}
    spans = [
        _span("tool.degraded.timeout", kind="guard"),
        _span("analytics.query", latency_ms=6000),
    ]
    signals = signalize(spans, task, None, slow_ms=5000)
    assert "task_failed" in signals
    assert "needs_human_review" in signals
    assert "degraded" in signals
    assert "slow" in signals
    assert severity(signals) == "high"


def test_handoff_event_alone_is_a_signal() -> None:
    handoff = {"reason": "REPEAT_FAILURE"}
    signals = signalize([_span("support.reply", kind="generate")], None, handoff, slow_ms=5000)
    assert signals == ["handoff", "repeated_failure"]
    assert severity(signals) == "high"


def test_sample_caps_each_stratum_and_keeps_the_worst() -> None:
    records = [
        _record("trc_a", score=4, started_at="2026-10-24T10:01:00"),
        _record("trc_b", score=9, started_at="2026-10-24T10:02:00"),
        _record("trc_c", score=5, started_at="2026-10-24T10:03:00"),
        _record("trc_d", intent="POLICY_QA", score=2),
        _record("trc_e", channel="APP", score=2),
    ]
    chosen, report = sample(records, per_stratum=2, limit=None)
    order_query = [
        item["trace_id"]
        for item in chosen
        if item["channel"] == "WEB" and item["intent"] == "ORDER_QUERY"
    ]
    assert order_query == ["trc_b", "trc_c"]
    assert report["WEB/ORDER_QUERY"] == (3, 2)
    assert report["WEB/POLICY_QA"] == (1, 1)
    assert report["APP/ORDER_QUERY"] == (1, 1)
    assert all(item["stratum"] == f"{item['channel']}/{item['intent']}" for item in chosen)


def test_sample_limit_is_global_and_deterministic() -> None:
    records = [_record(f"trc_{index:02d}", score=3 + index % 3) for index in range(10)]
    chosen, _ = sample(records, per_stratum=10, limit=3)
    assert [item["trace_id"] for item in chosen] == ["trc_02", "trc_05", "trc_08"]
    again, _ = sample(list(reversed(records)), per_stratum=10, limit=3)
    assert [item["trace_id"] for item in again] == ["trc_02", "trc_05", "trc_08"]
