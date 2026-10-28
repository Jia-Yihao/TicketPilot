"""转人工一等公民：8 类升级信号、接管静默、上下文包移交与坐席侧动作。

这些用例断言的是平台的四条不可逆承诺里最容易被悄悄破坏的一条：
结论与移交必须可由 trace 与上下文包还原，接管期间 Agent 必须零输出。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.conversation import ConversationMessage
from tests.conftest import ALICE

SEAT = {"X-Tenant-Id": "demo", "X-User-Id": "agent-1", "X-Roles": "support"}
OTHER_SEAT = {"X-Tenant-Id": "acme", "X-User-Id": "agent-9", "X-Roles": "support"}

BUNDLE_FIELDS = (
    "conversation_id",
    "tenant_id",
    "user_id",
    "channel",
    "reason",
    "signal_id",
    "priority",
    "queue",
    "transcript_digest",
    "transcript_ref",
    "intent_history",
    "slots",
    "attempted_actions",
    "pending_task_id",
    "sentiment",
    "policy_refs",
    "suggested_next_step",
    "created_at",
)


def _message(
    client: TestClient,
    headers: dict[str, str],
    text: str,
    **extra: object,
) -> dict:
    response = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": text, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seat_queue(client: TestClient, headers: dict[str, str] = SEAT) -> dict:
    response = client.get("/api/v1/handoffs", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _seat_case(client: TestClient, event_id: str) -> dict:
    response = client.get(f"/api/v1/handoffs/{event_id}", headers=SEAT)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------- 信号 1
def test_explicit_request_hands_the_context_to_the_seat(client: TestClient) -> None:
    body = _message(client, ALICE, "转人工")

    assert body["intent"] == "HUMAN_HANDOFF"
    assert body["handoff"]["signal_id"] == "user_request"
    assert body["handoff"]["status"] == "HANDOFF_PENDING"
    assert body["handoff"]["takeover"] is True

    queue = _seat_queue(client)
    assert queue["total"] == 1
    item = queue["items"][0]
    assert item["signal_id"] == "user_request"
    assert item["queue"] == "GENERAL"
    assert item["priority"] == "P0"

    case = _seat_case(client, item["event_id"])
    bundle = case["bundle"]
    for field in BUNDLE_FIELDS:
        assert field in bundle, f"bundle is missing {field}"
    assert bundle["conversation_id"] == body["conversation_id"]
    assert bundle["user_id"] == "alice"
    assert bundle["signal_id"] == "user_request"
    assert bundle["reason"] == "USER_REQUEST"
    assert bundle["suggested_next_step"]
    assert bundle["transcript_digest"]


# --------------------------------------------------------------------- 信号 2
def test_complaint_routes_to_the_complaint_queue(client: TestClient) -> None:
    body = _message(client, ALICE, "我要投诉你们的物流")

    assert body["handoff"]["signal_id"] == "complaint"
    assert body["handoff"]["queue"] == "COMPLAINT"
    # 投诉不吞掉正在进行的动作：工单照建，话术照给。
    assert body["task"] is not None
    assert body["task"]["status"] == "SUCCEEDED"
    assert "投诉专席" in body["reply"]

    case = _seat_case(client, body["handoff"]["event_id"])
    assert case["bundle"]["pending_task_id"] == body["task"]["task_id"]
    assert case["bundle"]["reason"] == "COMPLAINT"


# --------------------------------------------------------------------- 信号 3
def test_frustration_cue_hands_over(client: TestClient) -> None:
    body = _message(client, ALICE, "你们这什么破服务太差了")

    assert body["handoff"]["signal_id"] == "frustration"
    assert body["handoff"]["takeover"] is True
    assert "转人工" in body["reply"]

    case = _seat_case(client, body["handoff"]["event_id"])
    assert case["bundle"]["sentiment"]["label"] == "NEGATIVE"
    assert case["bundle"]["sentiment"]["signals"]


# --------------------------------------------------------------------- 信号 4
def test_repeat_failure_hands_over_after_two_failed_turns(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")
    assert first["handoff"] is None
    assert first["reply"]

    second = _message(
        client,
        ALICE,
        "订单 ORD-NOPE-1 到哪了",
        conversation_id=first["conversation_id"],
    )

    assert second["handoff"]["signal_id"] == "repeat_failure"
    assert second["handoff"]["takeover"] is True
    case = _seat_case(client, second["handoff"]["event_id"])
    assert case["bundle"]["slots"]["failed_intents"]["LOGISTICS_QUERY"] >= 2


# --------------------------------------------------------------------- 信号 5
def test_no_evidence_hands_over_when_retrieval_finds_nothing(
    app_factory: Callable[..., FastAPI],
) -> None:
    app = app_factory(policy_min_score=99.0)
    client = TestClient(app)

    body = _message(client, ALICE, "退款条件是什么？")

    assert body["intent"] == "POLICY_QA"
    assert body["context_cards"] == []
    assert body["handoff"]["signal_id"] == "no_evidence"
    assert body["handoff"]["takeover"] is True

    case = _seat_case(client, body["handoff"]["event_id"])
    assert case["bundle"]["reason"] == "NO_EVIDENCE"
    assert case["bundle"]["policy_refs"] == []


# --------------------------------------------------------------------- 信号 6
def test_high_risk_refund_is_flagged_but_the_conversation_stays(
    client: TestClient,
) -> None:
    body = _message(
        client,
        ALICE,
        "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题",
    )

    assert body["handoff"]["signal_id"] == "risk"
    assert body["handoff"]["status"] == "NOTED"
    assert body["handoff"]["takeover"] is False
    # 写路径照常执行：风险只被标记，不被拦下。
    assert body["task"] is not None
    assert "人工审批" in body["reply"]

    queue = _seat_queue(client)
    assert queue["total"] == 1
    assert queue["items"][0]["queue"] == "RISK"


# --------------------------------------------------------------------- 信号 7
def test_repeated_clarification_hands_over(client: TestClient) -> None:
    """追问一次 → 带候选再追问一次 → 第三次仍未补上才移交。"""
    first = _message(client, ALICE, "物流到哪了")
    assert first["clarification"] is not None
    assert first["handoff"] is None

    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )

    assert second["clarification"] is not None
    assert second["handoff"] is None
    assert [card["kind"] for card in second["context_cards"]] == [
        "order_candidate",
        "order_candidate",
    ]
    assert second["slots"]["clarification_count"] == 2

    third = _message(
        client, ALICE, "还是想不起来", conversation_id=first["conversation_id"]
    )

    assert third["handoff"]["signal_id"] == "repeated_clarification"
    assert third["handoff"]["takeover"] is True
    assert third["slots"]["clarification_count"] == 3
    assert "已转人工同事接手" in third["reply"]

    fourth = _message(
        client, ALICE, "还在吗", conversation_id=first["conversation_id"]
    )

    assert fourth["agent_silent"] is True
    assert fourth["slots"]["clarification_count"] == 3


def test_the_clarification_handoff_carries_the_candidates_shown(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "物流到哪了")
    _message(client, ALICE, "我忘了单号", conversation_id=first["conversation_id"])

    third = _message(
        client, ALICE, "还是想不起来", conversation_id=first["conversation_id"]
    )

    case = _seat_case(client, third["handoff"]["event_id"])
    candidates = case["bundle"]["attempted_candidates"]
    assert [item["order_id"] for item in candidates] == [
        card["card_id"] for card in third["context_cards"]
    ]
    assert {item["status"] for item in candidates} == {"SHIPPED", "DELIVERED"}
    assert all(item["product_name"] for item in candidates)


def test_a_handoff_without_candidates_still_lists_the_field(
    app_factory: Callable[..., FastAPI],
) -> None:
    """没有历史订单的客户被转人工时，字段仍然存在，只是空列表。"""
    app = app_factory()
    client = TestClient(app)
    headers = {"X-Tenant-Id": "demo", "X-User-Id": "nobody"}

    first = _message(client, headers, "物流到哪了")
    _message(client, headers, "我忘了单号", conversation_id=first["conversation_id"])
    third = _message(
        client, headers, "还是想不起来", conversation_id=first["conversation_id"]
    )

    assert third["context_cards"] == []
    case = _seat_case(client, third["handoff"]["event_id"])
    assert case["bundle"]["attempted_candidates"] == []


# --------------------------------------------------------------------- 信号 8
def test_idle_session_is_flagged_without_taking_over(
    app_factory: Callable[..., FastAPI],
) -> None:
    app = app_factory(session_idle_minutes=0)
    client = TestClient(app)

    body = _message(client, ALICE, "退款条件是什么？")

    assert body["handoff"]["signal_id"] == "idle_timeout"
    assert body["handoff"]["status"] == "NOTED"
    assert body["handoff"]["takeover"] is False
    assert body["reply"]


# ------------------------------------------------------------------ 接管静默
def test_agent_stays_silent_while_a_seat_owns_the_conversation(
    client: TestClient, app: FastAPI
) -> None:
    from app.models.enums import Outcome

    first = _message(client, ALICE, "转人工")
    assert first["reply"]
    assert first["agent_silent"] is False

    second = _message(
        client, ALICE, "订单 ORD-DEMO-1001 到哪了", conversation_id=first["conversation_id"]
    )

    assert second["reply"] == ""
    assert second["agent_silent"] is True
    assert second["context_cards"] == []
    assert second["task"] is None
    assert second["handoff"]["status"] == "HANDOFF_PENDING"

    # 静默也必须留痕：trace 里能看到 agent.silent 这一段。
    trace = client.get(f"/api/v1/traces/{second['trace_id']}", headers=ALICE)
    assert trace.status_code == 200
    spans = trace.json()["spans"]
    silent = [span for span in spans if span["name"] == "agent.silent"]
    assert silent
    assert silent[0]["attributes"]["reply_chars"] == 0

    # 静默分支同样只经产出点：留痕消息的 payload outcome 必须是枚举里的 AGENT_SILENT。
    with app.state.session_factory() as session:
        silent_message = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == second["conversation_id"],
                ConversationMessage.content == "订单 ORD-DEMO-1001 到哪了",
            )
        ).one()
    assert str(silent_message.payload["outcome"]) == Outcome.AGENT_SILENT.value


def test_agent_answers_again_after_the_seat_releases_the_conversation(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "转人工")
    event_id = first["handoff"]["event_id"]

    claimed = client.post(f"/api/v1/handoffs/{event_id}/claim", headers=SEAT)
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "HUMAN_ACTIVE"

    released = client.post(
        f"/api/v1/handoffs/{event_id}/release",
        headers=SEAT,
        json={"note": "已经答复用户"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "RELEASED"

    again = _message(
        client, ALICE, "订单 ORD-DEMO-1001 到哪了", conversation_id=first["conversation_id"]
    )
    assert again["agent_silent"] is False
    assert again["reply"]
    assert again["handoff"] is None


# ------------------------------------------------------------------ 坐席动作
def test_seat_can_claim_and_close_a_case(client: TestClient) -> None:
    body = _message(client, ALICE, "转人工")
    event_id = body["handoff"]["event_id"]

    claimed = client.post(f"/api/v1/handoffs/{event_id}/claim", headers=SEAT)
    assert claimed.status_code == 200
    detail = claimed.json()
    assert detail["status"] == "HUMAN_ACTIVE"
    assert detail["claimed_by"] == "agent-1"
    assert detail["claimed_at"]

    closed = client.post(
        f"/api/v1/handoffs/{event_id}/close",
        headers=SEAT,
        json={"resolution": "RESOLVED"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["resolution"] == "RESOLVED"
    assert closed.json()["closed_by"] == "agent-1"

    # 关闭之后不在待办队列里。
    assert _seat_queue(client)["total"] == 0


def test_claiming_a_case_twice_is_a_conflict(client: TestClient) -> None:
    body = _message(client, ALICE, "转人工")
    event_id = body["handoff"]["event_id"]

    assert client.post(f"/api/v1/handoffs/{event_id}/claim", headers=SEAT).status_code == 200
    again = client.post(f"/api/v1/handoffs/{event_id}/claim", headers=SEAT)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"


def test_closing_a_case_that_was_never_claimed_is_rejected(
    client: TestClient,
) -> None:
    body = _message(client, ALICE, "转人工")
    event_id = body["handoff"]["event_id"]

    response = client.post(f"/api/v1/handoffs/{event_id}/close", headers=SEAT)
    assert response.status_code == 200
    assert response.json()["status"] == "CLOSED"


# -------------------------------------------------------------------- 权限
def test_customer_cannot_read_the_seat_queue(client: TestClient) -> None:
    _message(client, ALICE, "转人工")

    response = client.get("/api/v1/handoffs", headers=ALICE)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_a_case_of_another_tenant_is_not_visible(client: TestClient) -> None:
    body = _message(client, ALICE, "转人工")
    event_id = body["handoff"]["event_id"]

    response = client.get(f"/api/v1/handoffs/{event_id}", headers=OTHER_SEAT)
    assert response.status_code == 404
    assert "alice" not in response.text

    queue = _seat_queue(client, OTHER_SEAT)
    assert queue["total"] == 0


def test_unknown_status_filter_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/handoffs?status=NOPE", headers=SEAT)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# -------------------------------------------------------------------- 指标
def test_metrics_count_handoffs_and_abandons(client: TestClient) -> None:
    first = _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")
    assert first["handoff"] is None
    # 同一诉求再次出现且上次没有解决：记一次 abandon。
    _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")

    _message(client, ALICE, "转人工")

    response = client.get("/api/v1/handoffs/metrics", headers=SEAT)
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["handoff_conversations"] == 1
    assert metrics["handoff_rate"] > 0
    assert metrics["open_cases"] == 1
    assert metrics["by_signal"]["user_request"] == 1
    assert metrics["by_queue"]["GENERAL"] == 1
    assert metrics["by_reason"]["USER_REQUEST"] == 1
    assert metrics["abandon_events"] == 1
    assert metrics["abandon_by_channel"]["WEB"] == 1
    assert metrics["abandon_by_intent"]["LOGISTICS_QUERY"] == 1
    assert metrics["abandon_by_hour"]
    assert len(metrics["abandon_by_hour"]) == 1


def test_abandon_events_are_listed_for_the_seat(client: TestClient) -> None:
    _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")
    _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")

    response = client.get("/api/v1/handoffs/abandons", headers=SEAT)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    event = body["items"][0]
    assert event["reason"] == "REPEAT_INTENT"
    assert event["intent"] == "LOGISTICS_QUERY"
    assert event["channel"] == "WEB"


def test_abandon_of_another_tenant_is_not_visible(client: TestClient) -> None:
    _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")
    _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")

    response = client.get("/api/v1/handoffs/abandons", headers=OTHER_SEAT)
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_a_follow_up_in_the_same_thread_is_not_an_abandon(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "订单 ORD-NOPE-1 到哪了")
    _message(
        client,
        ALICE,
        "订单 ORD-NOPE-1 到哪了",
        conversation_id=first["conversation_id"],
    )

    response = client.get("/api/v1/handoffs/abandons", headers=SEAT)
    assert response.json()["total"] == 0


# ------------------------------------------------------------ 触发表本身的形状
def test_every_signal_declares_a_phase_and_a_counter() -> None:
    from app.agent.escalation import get_escalation_catalog

    catalog = get_escalation_catalog()
    assert len(catalog.signals) == 8
    for signal in catalog.signals:
        assert signal.phase in {"pre", "post"}
        if signal.trigger == "threshold":
            assert signal.counter
            assert signal.threshold
        if signal.trigger == "cue":
            assert signal.cues


def test_every_named_threshold_resolves_in_the_policy_engine() -> None:
    """触发表引用的阈值名必须真能在政策引擎里取到数。"""
    from app.agent.escalation import get_escalation_catalog
    from app.core.config import Settings
    from app.core.policy import PolicyEngine

    engine = PolicyEngine.from_settings(Settings(environment="test"))

    for signal in get_escalation_catalog().signals:
        if signal.threshold is not None:
            assert engine.escalation.threshold(signal.threshold) >= 0, signal.signal_id


def test_repeated_clarification_has_its_own_budget() -> None:
    """澄清预算不复用挫败度阈值：第二次追问要留在客户手里。"""
    from app.agent.escalation import get_escalation_catalog
    from app.core.config import Settings
    from app.core.policy import PolicyEngine

    engine = PolicyEngine.from_settings(Settings(environment="test"))
    signal = next(
        item
        for item in get_escalation_catalog().signals
        if item.signal_id == "repeated_clarification"
    )

    assert signal.counter == "clarification_count"
    assert signal.threshold == "clarification_rounds"
    assert engine.escalation.threshold("clarification_rounds") == 3
    assert engine.escalation.threshold("frustration_rounds") == 2


@pytest.mark.parametrize(
    "status",
    ["HANDOFF_PENDING", "QUEUED", "HUMAN_ACTIVE", "CLOSED", "RELEASED", "NOTED"],
)
def test_every_handoff_status_is_listable(status: str) -> None:
    from app.models.enums import HandoffStatus

    assert HandoffStatus(status).value == status


def test_metrics_can_be_scoped_to_a_window(client: TestClient) -> None:
    _message(client, ALICE, "转人工")

    response = client.get(
        "/api/v1/handoffs/metrics?window_minutes=60", headers=SEAT
    )
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["window_minutes"] == 60
    assert metrics["handoff_conversations"] == 1
    assert metrics["by_signal"]["user_request"] == 1


# ------------------------------------------------------- R13：outcome 值域（移交侧）
def test_handoff_bundle_outcomes_come_from_the_recorded_turns(
    client: TestClient,
) -> None:
    """移交上下文包里的 outcome：除接管当回合的读取侧兜底外，必须全部 ∈ Outcome。

    盘上事实：bundle 是事件创建时的快照，而接管当回合的 payload 在快照之后
    才写入 outcome（阶段八），所以该行只能是 handoff.py 的兜底值 ``UNKNOWN``；
    ``UNKNOWN`` 是读取侧兜底，不入枚举、不是产出点。
    """
    from app.models.enums import Outcome

    first = _message(client, ALICE, "订单 ORD-DEMO-1001 买的是什么商品？")
    second = _message(client, ALICE, "转人工", conversation_id=first["conversation_id"])
    event_id = second["handoff"]["event_id"]

    claimed = client.post(f"/api/v1/handoffs/{event_id}/claim", headers=SEAT)
    assert claimed.status_code == 200, claimed.text
    case = _seat_case(client, event_id)

    seen = [str(record["outcome"]) for record in case["bundle"]["intent_history"]]
    allowed = {member.value for member in Outcome}
    assert seen[:1] == [Outcome.ANSWERED.value]
    assert set(seen) <= allowed | {"UNKNOWN"}
    assert [value for value in seen if value not in allowed] == ["UNKNOWN"]
    assert Outcome.HANDOFF.value not in seen  # 当回合快照时尚无 outcome，兜底为 UNKNOWN
