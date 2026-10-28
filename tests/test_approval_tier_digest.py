"""B-3 第一轮（B3-i）：档位快照进摘要、档位变化使旧审批失效。

口径来源：`.qoder/rules/approval-tiering.md`
- §5.1 新增第九键 ``approval_tier``，值是**整个档位快照**
  ``{"tier_id","max_cents","roles","required_approvals"}``。
- §5.2 摘要键清单由八项修订为九项；存量审批不 retro-fit。
- §5.3 执行前显式重校验：载荷含 ``approval_tier`` 且当前表解析结果与存量快照
  逐字段不一致 → 失效，原因码 ``APPROVAL_TIER_CHANGED``，退款不执行，转人工复核；
  载荷不含该键（存量审批）→ 跳过这条重校验。
- §2.3 校验 5 ``roles`` 装载期按字典序规范化：同一组角色换书写顺序，摘要不变。
- §2.4 金额大于所有档位上限 → 落**最后一档**（不是最低档），trace 记 ``tier_fallback``。
- §4.1 审批人除既有 ``has_role("approver")`` 外，还须持有该档 ``roles`` 中至少一个；
  默认档位（roles == ["approver"]）下与今天等价。

本轮**不做**阈值生效与自动执行路径（B3-ii）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.approval_tiers import get_approval_tiers, load_approval_tiers
from app.core.security import Identity
from app.models.approval import Approval
from app.models.audit import AuditEvent
from app.models.commerce import Order, Refund
from app.models.enums import RefundReason, RefundType
from app.models.observability import TraceSpan
from app.services.approvals import ApprovalService, canonical_digest, refund_payload
from tests.conftest import ALICE, REVIEWER

AMOUNT_CENTS = 12900

# 只改末档 max_cents（解析 T1 的结果不变）用的两张表。
_TABLE_T2_100000 = """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 10000
    roles:
      - approver
    required_approvals: 1
  - tier_id: T2
    max_cents: 100000
    roles:
      - approver
    required_approvals: 1
"""

_TABLE_T2_200000 = """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 10000
    roles:
      - approver
    required_approvals: 1
  - tier_id: T2
    max_cents: 200000
    roles:
      - approver
    required_approvals: 1
"""

# 12900 分在新表里落到 T2：与存量 T1 快照不一致，触发 APPROVAL_TIER_CHANGED。
_TABLE_SHIFTS_12900_TO_T2 = """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 10000
    roles:
      - approver
    required_approvals: 1
  - tier_id: T2
    max_cents: 10000000
    roles:
      - approver
    required_approvals: 1
"""

# 金额 12900 大于所有档位上限：兜底落到**最后一档** T2。
_TABLE_ABOVE_ALL = """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 5000
    roles:
      - approver
    required_approvals: 1
  - tier_id: T2
    max_cents: 9000
    roles:
      - approver
    required_approvals: 1
"""

# 仅 T1 的 max_cents 变了（6000000）：对不含 approval_tier 键的存量审批不构成误伤理由，
# 但足以让「若读该键则必判变」的任何实现现形。
_TABLE_SINGLE_TIER_6000000 = """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 6000000
    roles:
      - approver
    required_approvals: 1
"""


def _roles_ordered_table(first: str, second: str) -> str:
    return (
        """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 5000000
    roles:
      - """
        + first
        + "\n      - "
        + second
        + "\n    required_approvals: 1\n"
    )


def _write(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "approval_tiers.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _payload_order() -> Order:
    return Order(
        id="ORD-DEMO-1001",
        tenant_id="demo",
        user_id="alice",
        total_cents=AMOUNT_CENTS,
        refundable_cents=AMOUNT_CENTS,
        currency="CNY",
        paid_at=datetime(2026, 1, 1),
        version=1,
    )


def _digest_for(snapshot: dict[str, Any]) -> str:
    return canonical_digest(
        refund_payload(
            _payload_order(),
            AMOUNT_CENTS,
            "POLICY-2026-10",
            refund_reason=RefundReason.QUALITY,
            refund_type=RefundType.FULL,
            approval_tier=snapshot,
        )
    )


def _request_refund(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(
    client: TestClient, task_id: str, headers: dict[str, str] = REVIEWER
) -> Any:
    return client.post(
        f"/api/v1/tasks/{task_id}/approve",
        headers=headers,
        json={"decision": "APPROVE"},
    )


def _approval_payload(app: FastAPI, approval_id: str) -> dict[str, Any]:
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        return dict(approval.action_payload or {})


def _refund_rows(app: FastAPI) -> list[Refund]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(Refund)))


def _trace_spans(app: FastAPI, trace_id: str) -> list[TraceSpan]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(select(TraceSpan).where(TraceSpan.trace_id == trace_id))
        )


def _audit_rows(app: FastAPI, action: str) -> list[AuditEvent]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == action))
        )


def _strip_tier_key(app: FastAPI, approval_id: str) -> None:
    """把存量审批改造成「载荷里没有 approval_tier 键」的在途审批，并同步摘要。

    verify_binding 对存量载荷重算（口径 §5.2），所以去掉键必须同时重算
    action_digest，才是在途审批的真实形状。
    """
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        legacy = {
            key: value
            for key, value in dict(approval.action_payload or {}).items()
            if key != "approval_tier"
        }
        approval.action_payload = legacy
        approval.action_digest = canonical_digest(legacy)
        session.commit()


# --------------------------------------------------------------------------
# §5.1 / §5.2 第九键进载荷与摘要（对应「测试必须钉住」第 1 条）
# --------------------------------------------------------------------------


def test_the_default_payload_carries_the_tier_snapshot_and_keeps_the_eight_keys() -> None:
    """默认配置下 refund_payload 含 approval_tier，值等于 T1 快照；既有八键逐字不变。"""

    payload = refund_payload(
        _payload_order(),
        AMOUNT_CENTS,
        "POLICY-2026-10",
        refund_reason=RefundReason.QUALITY,
        refund_type=RefundType.FULL,
        approval_tier=get_approval_tiers().tier_for(AMOUNT_CENTS).snapshot(),
    )

    assert payload["approval_tier"] == {
        "tier_id": "T1",
        "max_cents": 5_000_000,
        "roles": ["approver"],
        "required_approvals": 1,
    }
    assert set(payload) == {
        "action",
        "order_id",
        "amount_cents",
        "currency",
        "resource_version",
        "policy_version",
        "reason",
        "refund_type",
        "approval_tier",
    }
    assert payload["action"] == "REFUND"
    assert payload["order_id"] == "ORD-DEMO-1001"
    assert payload["amount_cents"] == AMOUNT_CENTS
    assert payload["currency"] == "CNY"
    assert payload["resource_version"] == 1
    assert payload["policy_version"] == "POLICY-2026-10"
    assert payload["reason"] == "QUALITY"
    assert payload["refund_type"] == "FULL"


def test_a_new_approval_from_the_runtime_carries_the_default_tier_snapshot(
    client: TestClient, app: FastAPI
) -> None:
    """默认档位表下运行时建的审批：载荷带 T1 快照，且摘要覆盖含新键的整份载荷。"""

    created = _request_refund(client)
    assert created["status"] == "WAITING_APPROVAL"

    approval_id = created["pending_approval"]["approval_id"]
    payload = _approval_payload(app, approval_id)
    assert payload["approval_tier"] == get_approval_tiers().tier_for(
        AMOUNT_CENTS
    ).snapshot()

    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        assert canonical_digest(dict(approval.action_payload or {})) == (
            approval.action_digest
        )


# --------------------------------------------------------------------------
# §8 验收 4 摘要敏感性（对应「测试必须钉住」第 2 / 3 / 4 条）
# --------------------------------------------------------------------------


def test_changing_roles_or_required_approvals_changes_the_digest() -> None:
    """tier_id 不变，只改角色集合的内容或审批份数，摘要必须变（快照进摘要的意义）。"""

    base = get_approval_tiers().tier_for(AMOUNT_CENTS).snapshot()
    more_roles = {**base, "roles": ["approver", "reviewer"]}
    two_approvals = {**base, "required_approvals": 2}

    assert _digest_for(base) != _digest_for(more_roles)
    assert _digest_for(base) != _digest_for(two_approvals)


def test_changing_only_another_tiers_max_cents_keeps_the_digest(
    tmp_path: Path,
) -> None:
    """只改 max_cents 而档位解析结果不变（1000 分始终落 T1）：摘要必须不变。

    快照取的是解析结果，不是原始表行；上界变化若不落在解析命中的档上，
    规格一个字没动。
    """

    before = load_approval_tiers(_write(tmp_path / "before", _TABLE_T2_100000))
    after = load_approval_tiers(_write(tmp_path / "after", _TABLE_T2_200000))

    snapshot_before = before.tier_for(1000).snapshot()
    snapshot_after = after.tier_for(1000).snapshot()
    assert snapshot_before == snapshot_after
    assert _digest_for(snapshot_before) == _digest_for(snapshot_after)


def test_the_same_role_set_written_in_another_order_keeps_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YAML 里同一组角色换书写顺序：装载期按字典序规范化，摘要必须不变（§2.3 校验 5）。

    本轮加载期只允许 approver（§2.3 校验 5），单元素集合无「书写顺序」可言；
    规范化机制用两元素集合钉住：临时放开允许角色，两份表只差角色顺序。
    """

    import app.core.approval_tiers as tiers_module

    monkeypatch.setattr(
        tiers_module, "_ALLOWED_ROLES", frozenset({"approver", "reviewer"})
    )

    forwards = load_approval_tiers(
        _write(tmp_path / "forwards", _roles_ordered_table("approver", "reviewer"))
    )
    backwards = load_approval_tiers(
        _write(tmp_path / "backwards", _roles_ordered_table("reviewer", "approver"))
    )

    snapshot_forwards = forwards.tier_for(1000).snapshot()
    snapshot_backwards = backwards.tier_for(1000).snapshot()
    assert snapshot_forwards["roles"] == ["approver", "reviewer"]
    assert snapshot_forwards == snapshot_backwards
    assert _digest_for(snapshot_forwards) == _digest_for(snapshot_backwards)


# --------------------------------------------------------------------------
# §5.3 执行前重校验（对应「测试必须钉住」第 5 / 6 条）
# --------------------------------------------------------------------------


def test_a_stored_approval_without_the_tier_key_is_not_invalidated(
    client: TestClient,
    app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """载荷不含 approval_tier 的存量审批：改档位表后执行也不被 APPROVAL_TIER_CHANGED 误伤。"""

    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]
    _strip_tier_key(app, approval_id)

    changed = load_approval_tiers(_write(tmp_path, _TABLE_SINGLE_TIER_6000000))
    monkeypatch.setattr("app.services.refunds.get_approval_tiers", lambda: changed)

    approved = _approve(client, created["task_id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert len(_refund_rows(app)) == 1


def test_a_stored_approval_with_a_changed_tier_is_invalidated_before_execution(
    client: TestClient,
    app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """载荷含 approval_tier 且当前表解析结果不同：执行前失效，退款未执行，转人工复核。

    处理形状照 verify_binding 失败分支：审批 INVALID、台账 CANCELLED +
    error_code APPROVAL_TIER_CHANGED、审计 refund.invalidated、回执 NEEDS_HUMAN_REVIEW。
    """

    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]
    stored = _approval_payload(app, approval_id)
    assert stored["approval_tier"]["tier_id"] == "T1"

    changed = load_approval_tiers(_write(tmp_path, _TABLE_SHIFTS_12900_TO_T2))
    monkeypatch.setattr("app.services.refunds.get_approval_tiers", lambda: changed)

    approved = _approve(client, created["task_id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "FAILED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert body["result"]["reason_code"] == "APPROVAL_TIER_CHANGED"
    assert body["latest_approval"]["status"] == "INVALID"
    assert body["tool_execution"]["status"] == "CANCELLED"
    assert _refund_rows(app) == []

    invalidated = _audit_rows(app, "refund.invalidated")
    assert any(
        row.detail.get("reason") == "APPROVAL_TIER_CHANGED" for row in invalidated
    )


# --------------------------------------------------------------------------
# §2.4 兜底（对应「测试必须钉住」第 7 条）
# --------------------------------------------------------------------------


def test_an_amount_above_every_tier_falls_back_to_the_last_tier_and_records_it(
    app_factory: Callable[..., FastAPI],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """金额大于所有档位上限：落到**最后一档**（不是最低档），trace 记 tier_fallback。"""

    app = app_factory()
    client = TestClient(app)

    table = load_approval_tiers(_write(tmp_path, _TABLE_ABOVE_ALL))
    monkeypatch.setattr("app.services.runtime.get_approval_tiers", lambda: table)

    created = _request_refund(client)
    assert created["status"] == "WAITING_APPROVAL"

    payload = _approval_payload(app, created["pending_approval"]["approval_id"])
    # 兜底到最后一档 T2（上界 9000），不是最低档 T1；档位键不进摘要之外的形状不变。
    assert payload["approval_tier"] == {
        "tier_id": "T2",
        "max_cents": 9000,
        "roles": ["approver"],
        "required_approvals": 1,
    }

    fallback_spans = [
        span for span in _trace_spans(app, created["trace_id"])
        if "tier_fallback" in span.name or "tier_fallback" in span.attributes
    ]
    assert fallback_spans, "trace 必须能观察到 tier_fallback"
    assert fallback_spans[0].attributes.get("tier_fallback") is True


# --------------------------------------------------------------------------
# §4.1 审批角色校验与今天等价（对应「测试必须钉住」第 8 条）
# --------------------------------------------------------------------------


def test_default_tier_keeps_the_approver_gate_equivalent_to_today(
    client: TestClient,
) -> None:
    """默认档位 roles == ["approver"]：角色判定与本轮之前逐字等价。"""

    created = _request_refund(client)
    denied = _approve(
        client, created["task_id"], headers={**ALICE, "X-Roles": "agent"}
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    approved = _approve(client, created["task_id"], headers=REVIEWER)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "SUCCEEDED"


def test_admin_still_passes_the_gate_under_the_default_tier(
    app_factory: Callable[..., FastAPI],
) -> None:
    """has_role 视 admin 兼容 approver（既有语义）：默认档位下 admin 仍能批。"""

    app = app_factory()
    client = TestClient(app)

    created = _request_refund(client)
    approved = _approve(
        client,
        created["task_id"],
        headers={"X-Tenant-Id": "demo", "X-User-Id": "admin", "X-Roles": "admin"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "SUCCEEDED"


def test_decide_rejects_a_reviewer_outside_the_tier_roles(
    client: TestClient, app: FastAPI
) -> None:
    """载荷档位 roles 为 ["approver"] 时，decide 处的档位校验与 has_role 判定等价：
    approver 放行，无角色者拒绝。"""

    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]

    with app.state.session_factory() as session:
        service = ApprovalService(session)
        from app.core.errors import ForbiddenError

        with pytest.raises(ForbiddenError):
            service.decide(
                task_id=created["task_id"],
                approval_id=approval_id,
                decision="APPROVE",
                reviewer=Identity("demo", "nobody", frozenset()),
            )
