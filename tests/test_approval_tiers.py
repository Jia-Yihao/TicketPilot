"""审批档位表与 loader 的加载、校验与解析测试。

口径来源：`app/core/approval_tiers.yaml`
- §2.1 表的位置与形状（独立 YAML，不并入 `config.py`）
- §2.2 默认语义（单层表：一份 `approver` 审批）
- §2.3 加载期校验 7 条（违反即报错，不静默跳过、不降级）
- §2.4 解析规则与兜底（首个 `max_cents` 不小于金额的档；超出落到**最后一档**）
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

VALID_TABLE = """
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 5000000
    roles:
      - approver
    required_approvals: 1
"""

TABLE_WITHOUT_TIER_ID = """
version: "2026-10-26"
tiers:
  - max_cents: 5000000
    roles:
      - approver
    required_approvals: 1
"""

TWO_TIER_TABLE = """
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


def _write(directory: Path, body: str, name: str = "approval_tiers.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 默认表
# --------------------------------------------------------------------------


def test_the_default_table_has_one_tier_approved_by_one_approver() -> None:
    """默认表只有一档：该档允许的角色是 approver，所需审批数为 1。"""
    from app.core.approval_tiers import get_approval_tiers

    table = get_approval_tiers()
    assert len(table.tiers) == 1
    tier = table.tiers[0]
    assert tier.tier_id == "T1"
    assert tier.roles == ("approver",)
    assert tier.required_approvals == 1
    # 默认上界取策略金额上限 `refund_max_amount_cents` 的默认值（5_000_000 分）。
    assert tier.max_cents == 5_000_000


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def test_resolution_is_deterministic_and_takes_the_first_tier_that_covers_the_amount(
    tmp_path: Path,
) -> None:
    """解析是纯函数：同一金额两次解析逐字相同；取首个 max_cents 不小于该金额的档。"""
    from app.core.approval_tiers import load_approval_tiers

    table = load_approval_tiers(_write(tmp_path, TWO_TIER_TABLE))

    first = table.tier_for(1000)
    second = table.tier_for(1000)
    assert first.snapshot() == second.snapshot()
    assert first.tier.tier_id == "T1"

    # 上界是含的：恰好等于 max_cents 命中该档。
    assert table.tier_for(10000).tier.tier_id == "T1"
    # 超过第一档上界才落到下一档。
    assert table.tier_for(10001).tier.tier_id == "T2"
    assert table.tier_for(100000).tier.tier_id == "T2"


def test_an_amount_above_every_tier_falls_back_to_the_highest_tier(tmp_path: Path) -> None:
    """金额大于所有档位上界 → 取最后一档（最高档），不是最低档，并给出兜底信号。"""
    from app.core.approval_tiers import load_approval_tiers

    table = load_approval_tiers(_write(tmp_path, TWO_TIER_TABLE))

    resolution = table.tier_for(100001)
    assert resolution.tier.tier_id == "T2"
    assert resolution.tier is table.tiers[-1]
    assert resolution.fell_back is True

    # 命中表内档位时不带兜底信号。
    assert table.tier_for(100).fell_back is False


# --------------------------------------------------------------------------
# 加载期校验（逐条）
# --------------------------------------------------------------------------


def test_an_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    path = _write(tmp_path, VALID_TABLE + "extra: 1\n")
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_an_unknown_tier_key_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    body = VALID_TABLE.replace(
        "    required_approvals: 1",
        '    required_approvals: 1\n    note: "typo"',
    )
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_tier_without_an_id_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    path = _write(tmp_path, TABLE_WITHOUT_TIER_ID)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_repeated_tier_id_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    body = TWO_TIER_TABLE.replace("tier_id: T2", "tier_id: T1")
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_tier_id_that_is_not_upper_snake_case_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    for broken in ("t1", "T-1", "T1 LARGE", "T1__LARGE"):
        path = _write(tmp_path / broken.replace(" ", "_"), VALID_TABLE.replace("T1", broken))
        with pytest.raises(ValueError):
            load_approval_tiers(path)


def test_a_tier_id_with_a_trailing_newline_is_rejected(tmp_path: Path) -> None:
    """A ``$`` anchor also matches just before a trailing newline."""
    from app.core.approval_tiers import load_approval_tiers

    body = VALID_TABLE.replace("tier_id: T1", 'tier_id: "T1\\n"')
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_max_cents_that_is_not_an_integer_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    for broken in ('"5000000"', "5000000.5", "true"):
        path = _write(tmp_path / broken.strip('"'), VALID_TABLE.replace("5000000", broken))
        with pytest.raises(ValueError):
            load_approval_tiers(path)


def test_a_negative_max_cents_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    path = _write(tmp_path, VALID_TABLE.replace("5000000", "-1"))
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_repeated_max_cents_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    body = TWO_TIER_TABLE.replace("max_cents: 100000", "max_cents: 10000")
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_decreasing_max_cents_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    body = TWO_TIER_TABLE.replace("max_cents: 100000", "max_cents: 5000")
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_tier_without_roles_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    roles_block = "    roles:\n      - approver\n"
    empty = _write(tmp_path / "empty", VALID_TABLE.replace(roles_block, "    roles: []\n"))
    with pytest.raises(ValueError):
        load_approval_tiers(empty)

    missing = _write(tmp_path / "missing", VALID_TABLE.replace(roles_block, ""))
    with pytest.raises(ValueError):
        load_approval_tiers(missing)


def test_a_role_outside_approver_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    path = _write(tmp_path, VALID_TABLE.replace("      - approver", "      - reviewer"))
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_repeated_role_inside_one_tier_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    body = VALID_TABLE.replace("      - approver", "      - approver\n      - approver")
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_approval_tiers(path)


def test_a_required_approvals_other_than_one_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    for broken in ("0", "2"):
        body = VALID_TABLE.replace("required_approvals: 1", f"required_approvals: {broken}")
        path = _write(tmp_path / broken, body)
        with pytest.raises(ValueError):
            load_approval_tiers(path)


def test_an_empty_tier_table_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    empty = _write(tmp_path / "empty", 'version: "2026-10-26"\ntiers: []\n')
    with pytest.raises(ValueError):
        load_approval_tiers(empty)

    missing = _write(tmp_path / "missing", 'version: "2026-10-26"\n')
    with pytest.raises(ValueError):
        load_approval_tiers(missing)


def test_a_table_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    from app.core.approval_tiers import load_approval_tiers

    path = _write(tmp_path, "- tier_id: T1\n")
    with pytest.raises(ValueError):
        load_approval_tiers(path)


# --------------------------------------------------------------------------
# 角色列表的规范化
# --------------------------------------------------------------------------


def test_roles_are_normalised_to_lexicographic_order() -> None:
    """装载后每档的 roles 按字典序排序；快照里的 roles 序列等于排序结果。"""
    from app.core.approval_tiers import get_approval_tiers

    table = get_approval_tiers()
    for tier in table.tiers:
        assert tier.roles == tuple(sorted(tier.roles))
        snapshot = tier.snapshot()
        assert snapshot["roles"] == sorted(snapshot["roles"])
        assert snapshot["roles"] == list(tier.roles)


def test_the_same_role_set_written_in_another_order_yields_the_same_snapshot() -> None:
    """同一组角色换一种书写顺序，快照逐字相同（角色列表的顺序会逐字进摘要）。"""
    from app.core.approval_tiers import ApprovalTier

    forwards = ApprovalTier(
        tier_id="T1",
        max_cents=100,
        roles=("approver", "reviewer"),
        required_approvals=1,
    )
    backwards = ApprovalTier(
        tier_id="T1",
        max_cents=100,
        roles=("reviewer", "approver"),
        required_approvals=1,
    )
    assert forwards.roles == backwards.roles == ("approver", "reviewer")
    assert forwards.snapshot() == backwards.snapshot()


def test_the_snapshot_carries_exactly_the_four_tier_fields() -> None:
    """档位快照是四元组：tier_id / max_cents / roles / required_approvals。"""
    from app.core.approval_tiers import get_approval_tiers

    table = get_approval_tiers()
    snapshot = table.tier_for(100).snapshot()
    assert set(snapshot) == {"tier_id", "max_cents", "roles", "required_approvals"}
    assert snapshot["roles"] == ["approver"]


# --------------------------------------------------------------------------
# loader 的纯度
# --------------------------------------------------------------------------


def test_the_loader_reads_neither_clock_nor_random_nor_model_nor_database() -> None:
    from app.core import approval_tiers

    source = inspect.getsource(approval_tiers)
    for forbidden in (
        "import time",
        "import random",
        "import datetime",
        "import sqlalchemy",
        "import httpx",
        "import openai",
        "session_factory",
        "get_settings",
    ):
        assert forbidden not in source, f"loader 不得依赖 {forbidden}"
