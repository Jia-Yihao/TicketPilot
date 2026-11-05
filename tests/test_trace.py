from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

from app.agent.turns import Draft, SupportIntent
from app.core.security import Identity
from app.models.conversation import Conversation
from app.models.task import Task
from app.observability import tracing
from app.observability.redaction import redact, redact_payload
from app.observability.tracing import SpanBuffer, TraceRecorder
from app.repositories.traces import TraceRepository
from evals.replay import _link_line, _linked_task_trees, _load_source
from tests.conftest import ALICE
from tests.test_refund_taxonomy import _alembic_config


def test_recorder_records_spans_in_order() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    recorder.add("understand", "support.classify", attributes={"intent": "POLICY_QA"})
    recorder.add("retrieve", "policy.search", attributes={"hits": 2})

    assert recorder.kinds() == ["understand", "retrieve"]
    assert recorder.attributes_of("retrieve") == {"hits": 2}
    assert recorder.find("missing") is None


def test_recorder_marks_a_failed_span_as_error() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    try:
        with recorder.span("tool", "get_order") as payload:
            payload["tool"] = "get_order"
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    span = recorder.find("tool")
    assert span is not None
    assert span.status == "ERROR"
    assert span.attributes["error"] is True


def test_nested_spans_point_at_their_parent() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    with recorder.span("understand", "outer"):
        with recorder.span("retrieve", "inner"):
            pass

    inner = recorder.find("retrieve")
    outer = recorder.find("understand")
    assert inner is not None and outer is not None
    assert inner.parent_span_id == outer.id
    assert outer.parent_span_id is None


def test_redaction_masks_personal_data() -> None:
    masked = redact("我的手机号 13800138000，邮箱 alice@example.com")
    assert "13800138000" not in masked
    assert "alice@example.com" not in masked
    assert "***" in masked


def test_redaction_keeps_business_identifiers_readable() -> None:
    masked = redact("订单 ORD-DEMO-1001 已签收")
    assert "ORD-DEMO-1001" in masked


def test_redaction_walks_nested_payloads() -> None:
    payload = redact_payload({"contact": {"phone": "13900001111"}, "items": ["13500002222"]})
    assert payload["contact"]["phone"] == "***"
    assert payload["items"] == ["***"]


def test_trace_attributes_are_redacted_before_storage() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    recorder.add("generate", "support.reply", attributes={"text": "联系 13800138000"})

    assert "13800138000" not in recorder.spans[0].attributes["text"]


def test_support_reply_returns_a_replayable_trace(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    assert response.status_code == 200
    trace_id = response.json()["trace_id"]
    assert trace_id

    trace = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert trace.status_code == 200
    body = trace.json()
    kinds = [span["kind"] for span in body["spans"]]
    # R9-b (口径 §4.8.d): the root span is appended last, indices 0/1 keep
    # their readings; the exact ordered list now includes it.
    assert kinds == ["understand", "retrieve", "generate", "root"]

    understand = body["spans"][0]["attributes"]
    assert understand["intent"] == "POLICY_QA"
    retrieve = body["spans"][1]["attributes"]
    assert retrieve["hits"] >= 1
    assert retrieve["citations"][0]["version"] == "v3"


def test_trace_of_a_refund_task_records_the_tool_and_the_approval(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "WAITING_APPROVAL"
    assert task["trace_id"]

    trace = client.get(
        f"/api/v1/traces/{task['trace_id']}",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
    )
    assert trace.status_code == 200
    kinds = [span["kind"] for span in trace.json()["spans"]]
    assert "understand" in kinds
    assert "tool" in kinds
    assert "approval" in kinds


def test_trace_of_another_tenant_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    trace_id = response.json()["trace_id"]

    other_tenant = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "carol"},
    )
    assert other_tenant.status_code == 404


def test_trace_of_another_user_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    trace_id = response.json()["trace_id"]

    other_user = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "bob"},
    )
    assert other_user.status_code == 404


def test_approving_a_refund_appends_to_the_task_trace(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
            "context": {"refund_reason": "QUALITY"},
        },
    )
    assert created.status_code == 201
    task = created.json()
    trace_id = task["trace_id"]
    approval_id = task["pending_approval"]["approval_id"]

    approved = client.post(
        f"/api/v1/tasks/{task['task_id']}/approve",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
        json={"approval_id": approval_id, "decision": "APPROVE"},
    )
    assert approved.status_code == 200

    trace = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
    )
    assert trace.status_code == 200
    names = [span["name"] for span in trace.json()["spans"]]
    assert "refund.approval_requested" in names
    assert "refund.approval_approved" in names
    assert "refund.execution" in names


def test_trace_metrics_are_tenant_scoped_and_staff_only(client: TestClient) -> None:
    client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )

    response = client.get(
        "/api/v1/traces/metrics",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["spans"] > 0
    assert body["trace_count"] >= 1
    assert body["error_spans"] == 0
    assert body["error_rate"] == 0.0
    assert body["by_kind"]

    forbidden = client.get(
        "/api/v1/traces/metrics",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert forbidden.status_code == 403

    other_tenant = client.get(
        "/api/v1/traces/metrics",
        headers={
            "X-Tenant-Id": "acme",
            "X-User-Id": "agent-9",
            "X-Roles": "support",
        },
    )
    assert other_tenant.status_code == 200
    assert other_tenant.json()["spans"] == 0
    assert other_tenant.json()["error_rate"] == 0.0


# --------------------------------------------------- R9-a: segment latencies
#
# R9 口径 §5 acceptance 2: stub the single entry ``tracing.elapsed_ms`` to one
# fixed non-zero reading and assert every segment span carries it. A real
# elapsed-time reading is not admissible as the criterion — 0 is a legal
# reading and would be indistinguishable from a producer that forgot to pass
# latency_ms at all (§2.2.2). Readings below come from the stub, never from
# wall-clock assertions or manufactured delays.


def _stubbed_spans(
    client: TestClient, headers: dict[str, str], trace_id: str
) -> dict[tuple[str, str], int]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    assert response.status_code == 200
    return {
        (span["kind"], span["name"]): span["latency_ms"]
        for span in response.json()["spans"]
    }


def test_stubbed_clock_reads_seven_on_every_conversation_segment(
    app_factory: Callable[..., FastAPI],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracing, "elapsed_ms", lambda started: 7)
    application = app_factory()
    client = TestClient(application)
    alice = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}

    profiled = client.put(
        "/api/v1/memory", headers=alice, json={"preferred_name": "Alice 女士"}
    )
    assert profiled.status_code == 200

    policy_turn = client.post(
        "/api/v1/support/messages",
        headers=alice,
        json={"message": "退款条件是什么？"},
    )
    assert policy_turn.status_code == 200
    policy_spans = _stubbed_spans(
        client, alice, str(policy_turn.json()["trace_id"])
    )
    assert policy_spans[("understand", "support.classify")] == 7
    assert policy_spans[("retrieve", "policy.search")] == 7
    assert policy_spans[("generate", "memory.personalize")] == 7
    assert policy_spans[("generate", "support.reply")] == 7

    analytics_turn = client.post(
        "/api/v1/support/messages",
        headers=alice,
        json={"message": "我买了多少订单"},
    )
    assert analytics_turn.status_code == 200
    analytics_spans = _stubbed_spans(
        client, alice, str(analytics_turn.json()["trace_id"])
    )
    assert analytics_spans[("generate", "analytics.narrate")] == 7
    assert analytics_spans[("guard", "analytics.faithfulness")] == 7


def test_stubbed_clock_reads_seven_on_the_task_route(
    app_factory: Callable[..., FastAPI],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracing, "elapsed_ms", lambda started: 7)
    application = app_factory()
    client = TestClient(application)
    alice = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}

    created = client.post(
        "/api/v1/tasks",
        headers=alice,
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["trace_id"]

    task_spans = _stubbed_spans(client, alice, str(task["trace_id"]))
    assert task_spans[("understand", "task.route")] == 7


def test_stubbed_clock_reads_seven_when_the_output_guard_changes_the_draft(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guard span only exists when the guard really rewrote the reply
    # (§5.2): the reply below carries a timeline assertion and the draft has
    # no source, so ``_guard_output`` cannot take either early return.
    monkeypatch.setattr(tracing, "elapsed_ms", lambda started: 7)
    agent: Any = app.state.support_agent
    recorder = TraceRecorder(tenant_id="demo")
    draft = Draft(intent=SupportIntent.POLICY_QA, reply="您的退款 3 天内一定到账。")

    with app.state.session_factory() as session:
        agent._guard_output(
            session,
            Identity("demo", "alice", frozenset()),
            recorder,
            "conv-demo",
            draft,
        )

    span = recorder.find("guard")
    assert span is not None
    assert span.name == "output.grounded"
    assert span.latency_ms == 7


def test_analytics_tool_spans_carry_their_duration_in_the_latency_column(
    client: TestClient,
) -> None:
    # §2.1.3: the ledger/attribute duration_ms and the latency column are the
    # same number written into a second carrier — never a second formula.
    answered = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "我买了多少订单"},
    )
    assert answered.status_code == 200
    response = client.get(
        f"/api/v1/traces/{answered.json()['trace_id']}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    tool_spans = [
        span
        for span in response.json()["spans"]
        if span["kind"] == "tool" and "duration_ms" in span["attributes"]
    ]
    assert tool_spans, "expected a tool span carrying duration_ms"
    for span in tool_spans:
        assert span["latency_ms"] == span["attributes"]["duration_ms"]


# ------------------------------------------------------------- R9-b: root span
#
# R9 口径 §2.3 / §5.3–5.5：一棵树（= 一次 flush 写下的那批 span，§1.1）恰好
# 一条根，名字只能是三个封闭值之一，且只能由 ``TraceRecorder.finalize()`` 产出
# ——调用方永远不自己 ``add("root", ...)``。注意「树」不等于 trace_id：
# task.run 树与 task.approve 树共用同一个 task.trace_id（既有用例
# test_approving_a_refund_appends_to_the_task_trace 钉住的追加行为），所以
# 下面的断言按「每棵树、每个根名」判定，而不是「一次 GET 恰好一条根」。

ROOT_NAMES = {"support.turn", "task.run", "task.approve"}


def _span_forest(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verify the parent pointers of every tree and return the roots."""
    ids = {span["span_id"] for span in spans}
    roots = [span for span in spans if span["kind"] == "root"]
    for root in roots:
        assert root["parent_span_id"] is None
        assert root["name"] in ROOT_NAMES
        # §2.3.8：根的属性恒为空字典（仍需走脱敏路径，由 _record 保证）。
        assert root["attributes"] == {}
    for span in spans:
        if span["kind"] == "root":
            continue
        # §5.3：除根外每条 span 的父级非空且指向同一棵树里存在的 span。
        # 顶层 span 被 finalize 重挂到根上，所以这里不可能读到 None。
        assert span["parent_span_id"] is not None, span
        assert span["parent_span_id"] in ids, span
        assert span["parent_span_id"] != span["span_id"]
    return roots


def _get_trace(client: TestClient, headers: dict[str, str], trace_id: str) -> Any:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    assert response.status_code == 200
    return response.json()["spans"]


def test_conversation_tree_carries_exactly_one_support_turn_root(
    client: TestClient,
) -> None:
    # §5.3 树之一：POLICY_QA 对话（support.turn）。
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    assert response.status_code == 200
    spans = _get_trace(
        client,
        {"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        str(response.json()["trace_id"]),
    )

    roots = _span_forest(spans)
    assert len(roots) == 1
    assert roots[0]["name"] == "support.turn"
    # §2.3.7：根排在末尾，spans[0]/spans[1] 的既有读数不被顶掉。
    assert spans[-1]["kind"] == "root"


def test_refund_task_tree_carries_exactly_one_task_run_root(
    client: TestClient,
) -> None:
    # §5.3 树之二：退款任务（task.run），审批前只有一棵树。
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "WAITING_APPROVAL"

    spans = _get_trace(
        client,
        {"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        str(task["trace_id"]),
    )
    roots = _span_forest(spans)
    assert len(roots) == 1
    assert roots[0]["name"] == "task.run"
    assert spans[-1]["kind"] == "root"


def test_approval_appends_a_task_approve_rooted_tree_to_the_task_trace(
    client: TestClient,
) -> None:
    # §5.3 树之三：审批（task.approve）。approve 树与 run 树共用 task.trace_id
    # （既有追加行为），所以这个 trace 上有两棵树、两个根——每个根名恰好一条。
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
            "context": {"refund_reason": "QUALITY"},
        },
    )
    assert created.status_code == 201
    task = created.json()
    approver = {
        "X-Tenant-Id": "demo",
        "X-User-Id": "reviewer",
        "X-Roles": "approver",
    }

    approved = client.post(
        f"/api/v1/tasks/{task['task_id']}/approve",
        headers=approver,
        json={
            "approval_id": task["pending_approval"]["approval_id"],
            "decision": "APPROVE",
        },
    )
    assert approved.status_code == 200

    spans = _get_trace(client, approver, str(task["trace_id"]))
    roots = _span_forest(spans)
    assert sorted(root["name"] for root in roots) == ["task.approve", "task.run"]
    assert spans[-1]["kind"] == "root"
    assert spans[-1]["name"] == "task.approve"


def test_silent_turn_is_a_second_rooted_tree_with_its_own_trace(
    client: TestClient,
) -> None:
    # §4.8.a / §5.3：沉默路径自己构造一棵树，「每轮恰好一个根」是错的验收
    # 写法——正确写法是「每棵树恰好一个根」。同一会话里接管后的沉默回合
    # 产出第二条 trace，两条 trace 各自恰好一条 support.turn 根。
    alice = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}
    first = client.post(
        "/api/v1/support/messages", headers=alice, json={"message": "转人工"}
    ).json()
    second = client.post(
        "/api/v1/support/messages",
        headers=alice,
        json={
            "message": "订单 ORD-DEMO-1001 到哪了",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    assert second["agent_silent"] is True
    assert second["trace_id"] != first["trace_id"]

    for trace_id in (first["trace_id"], second["trace_id"]):
        spans = _get_trace(client, alice, str(trace_id))
        roots = _span_forest(spans)
        assert len(roots) == 1
        assert roots[0]["name"] == "support.turn"


def test_finalize_rehangs_top_level_spans_once_and_stays_idempotent() -> None:
    # §2.3.4 / §5.5：finalize 只重挂顶层，嵌套不动；第二次调用不新增根、
    # 不改父级、len(spans) 不变。§2.3.5：「finalize 之后 _open 为空」的
    # 断言只写在单测里。
    recorder = TraceRecorder(tenant_id="demo", root_name="support.turn")
    recorder.add("understand", "support.classify")
    with recorder.span("retrieve", "outer"):
        recorder.add("generate", "inner-under-outer")
    outer = recorder.find("retrieve")
    inner = recorder.find("generate")
    first_top = recorder.spans[0]
    assert outer is not None and inner is not None
    top_parent_before = first_top.parent_span_id
    assert top_parent_before is None  # 顶层 span 初生无父，等 finalize 重挂。
    inner_parent_before = inner.parent_span_id
    assert inner_parent_before == outer.id

    recorder.finalize()
    roots = [span for span in recorder.spans if span.kind == "root"]
    assert len(roots) == 1
    assert roots[0] is recorder.spans[-1]  # §2.3.7：根排在末尾。
    assert roots[0].parent_span_id is None
    assert recorder.spans[0].parent_span_id == roots[0].id
    assert recorder.spans[0] is first_top
    # 嵌套层不动（§2.3.4：finalize 只处理顶层）。
    assert recorder.find("generate") is inner
    assert inner.parent_span_id == inner_parent_before
    assert outer.parent_span_id == roots[0].id
    assert not recorder._open  # 单测专属断言（§2.3.5）

    spans_before = list(recorder.spans)
    parents_before = [span.parent_span_id for span in recorder.spans]
    recorder.finalize()
    assert recorder.spans == spans_before
    assert [span.parent_span_id for span in recorder.spans] == parents_before
    assert len([span for span in recorder.spans if span.kind == "root"]) == 1


def test_finalize_never_raises_and_nameless_recorders_grow_no_root() -> None:
    # §2.3.5：_open 意外非空时生产路径吞掉、不抛、不为它新增属性；
    # §7.1「默认值留空即不生根」：不带根名的 recorder（仓内既有其它构造点）
    # finalize 后行为不变，长不出根。
    recorder = TraceRecorder(tenant_id="demo", root_name="task.run")
    recorder.add("tool", "get_order")
    recorder._open.append(SpanBuffer(id="spn_loose", kind="tool", name="loose"))
    recorder.finalize()  # must not raise
    assert len(recorder.spans) == 2
    assert recorder.spans[-1].kind == "root"

    plain = TraceRecorder(tenant_id="demo")
    plain.add("tool", "get_order")
    plain.finalize()
    assert plain.kinds() == ["tool"]


# --------------------------------------------------- R9-c: 关联键 conversation_id
#
# R9 口径 §2.4 / §5.6–5.8：`tasks.conversation_id` 是把「对话树」与「任务树」
# 落到同一个对话下的唯一关联键。它只走列（§2.4.4：进 `task.context` 会改
# `request_digest`，把同一幂等键的重试从「返回第一个任务」变成 409），只由
# 对话侧建单写入（§2.4.3：直接 `POST /api/v1/tasks` 一律 NULL，对外不加字段）。
#
# 命中 QUALITY 规则表的一句话：槽位齐了，对话侧会真的建单（`actions.py::_create_task`）。
REFUND_WITH_REASON = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"


def test_a_dialog_created_task_records_the_turn_conversation_id(
    client: TestClient, app: FastAPI
) -> None:
    # §5.6 之一：对话侧建单 → tasks.conversation_id 等于当轮 conversations.id。
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_WITH_REASON},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    task_id = str(body["task"]["task_id"])

    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        conversation = session.get(Conversation, str(body["conversation_id"]))
        assert task is not None, task_id
        assert conversation is not None, body["conversation_id"]
        assert task.conversation_id == conversation.id
    # 键不落进 context（§2.4.4）：写进去会改摘要，重试即 409。
    assert "conversation_id" not in task.context


def test_a_task_created_through_the_api_carries_no_conversation_key(
    client: TestClient, app: FastAPI
) -> None:
    # §5.6 之二 + §2.4.3：直接调 API 建的单一律 NULL，且对外响应不带这个键。
    created = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "帮我查询订单 ORD-DEMO-1001"},
    )
    assert created.status_code == 201, created.text
    assert "conversation_id" not in created.json()

    with app.state.session_factory() as session:
        task = session.get(Task, created.json()["task_id"])
        assert task is not None
        assert task.conversation_id is None


def test_the_dialog_tree_and_the_task_tree_share_one_conversation_key(
    client: TestClient, app: FastAPI
) -> None:
    # §5.7：按 tasks.conversation_id 查 trace_spans，同一个对话下同时读到
    # root/support.turn 与 root/task.run 两棵树，且两棵树的 trace_id 不同。
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_WITH_REASON},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    with app.state.session_factory() as session:
        task = session.get(Task, str(body["task"]["task_id"]))
        assert task is not None and task.conversation_id is not None
        spans = TraceRepository(session).list_for_conversation(task.conversation_id)

    roots = [(span.name, span.trace_id) for span in spans if span.kind == "root"]
    assert sorted(name for name, _ in roots) == ["support.turn", "task.run"]
    trace_ids = {trace_id for _, trace_id in roots}
    assert len(trace_ids) == 2, roots
    # 两棵树各自的 trace_id 与读到的对话树 / 任务记录一致。
    assert str(body["trace_id"]) in trace_ids
    assert str(task.trace_id) in trace_ids
    assert all(span.conversation_id == task.conversation_id for span in spans)


# --- §5.8：迁移双向可行（形状照 tests/test_refund_taxonomy.py 的同名用例）-----

# 新迁移的前一版；历史行必须在这一版上写进去，才谈得上「不得回填」。
LINK_PREVIOUS_HEAD = "d9e4c7a15b82"


def _seed_legacy_task(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks (id, tenant_id, user_id, status, request, context,"
                " version, created_at, updated_at) VALUES ('task_legacy', 'demo',"
                " 'alice', 'SUCCEEDED', '帮我查询订单 ORD-DEMO-1001', '{}', 1,"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    engine.dispose()


def test_migration_adds_a_nullable_conversation_key_without_backfilling(
    tmp_path: Path,
) -> None:
    from alembic import command

    database_url = f"sqlite+pysqlite:///{(tmp_path / 'link.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, LINK_PREVIOUS_HEAD)
    _seed_legacy_task(database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    columns = {
        column["name"]: column for column in sa_inspect(engine).get_columns("tasks")
    }
    assert "conversation_id" in columns, "迁移后 tasks 缺少 conversation_id 列"
    assert columns["conversation_id"]["nullable"] is True
    indexed = {index["name"] for index in sa_inspect(engine).get_indexes("tasks")}
    assert "ix_tasks_conversation_id" in indexed, sorted(indexed)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT conversation_id FROM tasks WHERE id = 'task_legacy'")
        ).one()
    assert row == (None,), "迁移不得回填历史行"
    engine.dispose()


def test_migration_downgrade_removes_the_conversation_key(tmp_path: Path) -> None:
    from alembic import command

    database_url = f"sqlite+pysqlite:///{(tmp_path / 'link-down.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    assert "conversation_id" in {
        column["name"] for column in sa_inspect(engine).get_columns("tasks")
    }

    # 目标写死为父版本，不用相对值 `"-1"`：`"-1"` 把用例钉在
    # 「本条迁移恰好是 head」上，下一条迁移落地即变红（同一棵树里
    # refunds 用例已以真实代价验证）。
    command.downgrade(config, LINK_PREVIOUS_HEAD)

    columns = {column["name"] for column in sa_inspect(engine).get_columns("tasks")}
    assert "conversation_id" not in columns
    indexed = {index["name"] for index in sa_inspect(engine).get_indexes("tasks")}
    assert "ix_tasks_conversation_id" not in indexed
    engine.dispose()


# --- §5.10：回放产出按关联键串起一步（R9-d）------------------------------
#
# 断言对象是 evals/replay.py 的产出，不是 trace_spans 表本身（表由 §5.7 覆盖）。
# 口径只允许「加一行输出」；两个既有打印行与全部比对规则不得动，这里从真实库
# 现算那行输出的两个数，不跑 --self-check（模型读数由用户侧复跑）。


def test_the_replay_links_the_task_tree_under_the_conversation_key(
    client: TestClient, app: FastAPI
) -> None:
    # 复用 §5.7 的样本：一次对话触发建单，_load_source 现在把
    # traces_by_conversation 一并交出来（只搬运，不新增查询）。
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_WITH_REASON},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    conversation_id = str(body["conversation_id"])

    with app.state.session_factory() as session:
        conversations, spans, traces_by_conversation = _load_source(session)

    # 源里确实记录到了这轮对话。
    assert conversation_id in {c.conversation_id for c in conversations}

    # 判据一：该 conversation_id 名下挂到的树数 ≥ 2。
    trace_ids = traces_by_conversation.get(conversation_id, [])
    assert len(trace_ids) >= 2, trace_ids

    # 判据二：root 名同时含对话树 support.turn 与任务树 task.run，且两棵树的 trace_id 不同。
    roots_by_trace = {
        trace_id: [
            span["name"] for span in spans.get(trace_id, []) if span["kind"] == "root"
        ]
        for trace_id in trace_ids
    }
    dialog_traces = {
        trace_id
        for trace_id, names in roots_by_trace.items()
        if "support.turn" in names
    }
    task_traces = {
        trace_id for trace_id, names in roots_by_trace.items() if "task.run" in names
    }
    assert dialog_traces, roots_by_trace
    assert task_traces, roots_by_trace
    assert dialog_traces != task_traces, roots_by_trace

    # 判据三：那行输出里的两个数，从真实库现算——只含该对话的映射得到恰 (1, 1)。
    only_this_conversation = {conversation_id: trace_ids}
    assert _linked_task_trees(only_this_conversation, spans) == (1, 1)


def test_the_link_line_names_the_count_it_printed() -> None:
    assert _link_line(1, 1) == (
        "replay: 1 task trees linked by conversation_id across 1 conversations"
    )
    assert _link_line(0, 0) == (
        "replay: 0 task trees linked by conversation_id across 0 conversations"
    )

    def span(kind: str, name: str) -> dict[str, Any]:
        # 键集与 _load_source 产出的一致：kind / name / status / attributes。
        return {"kind": kind, "name": name, "status": "OK", "attributes": {}}

    spans = {
        "trc_dialog": [span("understand", "support.classify"), span("root", "support.turn")],
        "trc_task": [span("understand", "task.route"), span("root", "task.run")],
        "trc_approve": [span("approval", "approval.decided"), span("root", "task.approve")],
        "trc_noroot": [span("understand", "support.classify")],
    }
    # 只有对话树：链接没有把任何任务树带进来。
    assert _linked_task_trees({"conv_a": ["trc_dialog"]}, spans) == (0, 0)
    # 只有任务树、没有对话树：不算「对话里建出来的单」。
    assert _linked_task_trees({"conv_b": ["trc_task"]}, spans) == (0, 0)
    # 一个对话名下同时有 support.turn + task.run + task.approve → 两棵任务树、一个对话。
    assert (
        _linked_task_trees({"conv_c": ["trc_dialog", "trc_task", "trc_approve"]}, spans)
        == (2, 1)
    )
    # trace 里没有 root span（_root_name 返回 None）→ 不进计数。
    assert _linked_task_trees({"conv_d": ["trc_noroot"]}, spans) == (0, 0)
