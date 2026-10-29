"""在线信号阈值基线 YAML 与 loader 的加载、校验与抄本一致性测试。

口径来源：`.qoder/rules/online-signals-and-notification.md`
- §3 键结构与约束 1–4（加载期封闭键校验，违反即 `ValueError`，不静默跳过）
- §3-7 抄本一致性（现读 `evals/baseline/business.json`，文件缺失 / 键缺失 /
  值不符一律失败）
- §9 第 1、2、3、4、12 条（本文件覆盖 T2 交付；§9-3 生产侧消费在 T3）

**反例硬要求**：一律写临时 YAML 后直接调 `load_online_signals(path)`，
不得走 `@lru_cache(maxsize=1)` 的 `get_online_signals()` 缓存 getter，
否则第二次加载会返回第一次的结果，反例假通过。
先例形态照 `tests/test_approval_tiers.py:52-56` 的 `_write(directory, body, name=...)`
与 `:123-146` 那批反例。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

if TYPE_CHECKING:
    from app.core.online_signals import OnlineSignalTable

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 种子：一份 3 行、契约全满足的最小 YAML，用于反例通过文本替换派生。
# ---------------------------------------------------------------------------

VALID_TABLE = """version: 1
signals:
  - signal_id: no_evidence_rate
    threshold: 0.20
    calibrated: false
    realert_minutes: 15
    baseline:
      source_file: "none"
      reading_key: "none"
      copied_value: null
      copied_at: "2026-11"
      note: "未经基线校准的占位初值"
    action_suggestion: "建议 A"
  - signal_id: handoff_rate
    threshold: 0.55
    calibrated: true
    realert_minutes: 15
    baseline:
      source_file: "evals/baseline/business.json"
      reading_key: "handoff_metrics.handoff_rate"
      copied_value: 0.3654
      copied_at: "2026-11"
      note: "阈值 0.55 高于基线 0.3654，属判断值，不是基线值。"
    action_suggestion: "建议 B"
  - signal_id: tool_failure_rate
    threshold: 0.10
    calibrated: false
    realert_minutes: 15
    baseline:
      source_file: "none"
      reading_key: "none"
      copied_value: null
      copied_at: "2026-11"
      note: "未经基线校准的占位初值"
    action_suggestion: "建议 C"
"""


def _write(directory: Path, body: str, name: str = "online_signals.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def _load(tmp_path: Path, body: str) -> OnlineSignalTable:
    """反例专用：**直接调纯加载函数**，绕过缓存 getter。"""
    from app.core.online_signals import load_online_signals

    return load_online_signals(_write(tmp_path, body))


# ---------------------------------------------------------------------------
# §9-1 正例：纯加载函数能读一份合法 YAML
# ---------------------------------------------------------------------------


def test_a_valid_table_loads_three_signals_in_declared_order(tmp_path: Path) -> None:
    table = _load(tmp_path, VALID_TABLE)

    assert table.version == "1"
    assert len(table.signals) == 3
    assert [s.signal_id for s in table.signals] == [
        "no_evidence_rate",
        "handoff_rate",
        "tool_failure_rate",
    ]


def test_the_cached_getter_and_the_pure_loader_agree(tmp_path: Path) -> None:
    """`get_online_signals()` 与纯加载同形：同一份文件两次结果逐字一致。"""
    from app.core.online_signals import get_online_signals, load_online_signals

    real_path = REPO_ROOT / "app" / "core" / "online_signals.yaml"
    pure = load_online_signals(real_path)
    cached = get_online_signals()
    # 缓存 getter 走的就是同一份文件；两个入口的信号行逐字相等。
    assert [s.signal_id for s in cached.signals] == [s.signal_id for s in pure.signals]
    assert cached.signals == pure.signals


# ---------------------------------------------------------------------------
# §9-1 反例 · 顶层封闭键（§3 约束 1）
# ---------------------------------------------------------------------------


def test_an_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _load(tmp_path, VALID_TABLE + "extra: 1\n")


def test_a_missing_signals_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _load(tmp_path, "version: 1\n")


def test_a_table_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _load(tmp_path, "- signal_id: no_evidence_rate\n")


# ---------------------------------------------------------------------------
# §9-1 反例 · signals 恰三行 + 封闭词表 + 无重复（§3 约束 1）
# ---------------------------------------------------------------------------


def test_too_few_signals_are_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE[: VALID_TABLE.index("  - signal_id: tool_failure_rate")]
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_too_many_signals_are_rejected(tmp_path: Path) -> None:
    # 追加一份结构合法的第四行（用不同的 signal_id 排除重复干扰）。
    extra = (
        "  - signal_id: tool_failure_rate\n"
        "    threshold: 0.30\n"
        "    calibrated: false\n"
        "    realert_minutes: 15\n"
        "    baseline:\n"
        '      source_file: "none"\n'
        '      reading_key: "none"\n'
        "      copied_value: null\n"
        '      copied_at: "2026-12"\n'
        '      note: "未经基线校准的占位初值"\n'
        '    action_suggestion: "建议 D"\n'
    )
    # 直接把第四行的 signal_id 改掉，避免与第三行重复；
    # loader 的 `len != 3` 检查在遍历 signal 之前跑，越界即错。
    body = VALID_TABLE + extra
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_a_duplicate_signal_id_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace("signal_id: handoff_rate", "signal_id: no_evidence_rate")
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_a_signal_id_outside_the_closed_vocabulary_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace("signal_id: no_evidence_rate", "signal_id: unknown_signal")
    with pytest.raises(ValueError):
        _load(tmp_path, body)


# ---------------------------------------------------------------------------
# §9-1 反例 · 信号行封闭键（§3 约束 2）
# ---------------------------------------------------------------------------


def test_an_unknown_signal_row_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '    action_suggestion: "建议 A"',
        '    action_suggestion: "建议 A"\n    severity: P1',
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_a_missing_signal_row_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace("    calibrated: false\n", "", 1)
    with pytest.raises(ValueError):
        _load(tmp_path, body)


# ---------------------------------------------------------------------------
# §9-1 反例 · baseline 块封闭键（§3 约束 2）
# ---------------------------------------------------------------------------


def test_an_unknown_baseline_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      note: "未经基线校准的占位初值"',
        '      note: "未经基线校准的占位初值"\n      extra: "boom"',
        1,
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_a_missing_baseline_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace('      note: "未经基线校准的占位初值"', "", 1)
    with pytest.raises(ValueError):
        _load(tmp_path, body)


# ---------------------------------------------------------------------------
# §9-1 反例 · threshold 值域（§3 约束 3：float ∈ (0, 1]）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("broken", "label"),
    [
        ("0", "zero"),
        ("-0.1", "negative"),
        ("1.5", "above_one"),
        ("true", "boolean_true"),
        ("false", "boolean_false"),
        ('"0.20"', "string"),
    ],
)
def test_a_bad_threshold_is_rejected(tmp_path: Path, broken: str, label: str) -> None:
    body = VALID_TABLE.replace("threshold: 0.20", f"threshold: {broken}")
    path = _write(tmp_path / label, body)
    # 直接调纯加载函数，绕过缓存 getter。
    from app.core.online_signals import load_online_signals

    with pytest.raises(ValueError):
        load_online_signals(path)


def test_threshold_one_is_accepted(tmp_path: Path) -> None:
    """1.0 属于 (0, 1]，边界不越。"""
    body = VALID_TABLE.replace("threshold: 0.20", "threshold: 1.0")
    table = _load(tmp_path, body)
    assert table.by_id("no_evidence_rate").threshold == 1.0


# ---------------------------------------------------------------------------
# §9-1 反例 · realert_minutes 值域（§3 约束 3：int > 0，bool 挡掉）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("broken", "label"),
    [
        ("0", "zero"),
        ("-1", "negative"),
        ("15.5", "float"),
        ("true", "boolean_true"),
        ("false", "boolean_false"),
    ],
)
def test_a_bad_realert_minutes_is_rejected(tmp_path: Path, broken: str, label: str) -> None:
    body = VALID_TABLE.replace("realert_minutes: 15", f"realert_minutes: {broken}")
    path = _write(tmp_path / label, body)
    from app.core.online_signals import load_online_signals

    with pytest.raises(ValueError):
        load_online_signals(path)


# ---------------------------------------------------------------------------
# §9-1 反例 · action_suggestion 非空字符串（§3 约束 3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("replacement", "label"),
    [
        ('action_suggestion: ""', "empty"),
        ("action_suggestion: \"   \"", "whitespace"),
        ("action_suggestion: null", "null"),
        ("action_suggestion: 3", "int"),
    ],
)
def test_a_bad_action_suggestion_is_rejected(
    tmp_path: Path, replacement: str, label: str
) -> None:
    body = VALID_TABLE.replace('action_suggestion: "建议 A"', replacement)
    path = _write(tmp_path / label, body)
    from app.core.online_signals import load_online_signals

    with pytest.raises(ValueError):
        load_online_signals(path)


# ---------------------------------------------------------------------------
# §9-1 反例 · calibrated 必须是 bool（§3 约束 4 前置）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("broken", "label"),
    [
        ('calibrated: "true"', "string"),
        ("calibrated: 1", "int_one"),
        ("calibrated: null", "null"),
    ],
)
def test_a_non_boolean_calibrated_is_rejected(
    tmp_path: Path, broken: str, label: str
) -> None:
    body = VALID_TABLE.replace("calibrated: false", broken, 1)
    path = _write(tmp_path / label, body)
    from app.core.online_signals import load_online_signals

    with pytest.raises(ValueError):
        load_online_signals(path)


# ---------------------------------------------------------------------------
# §9-1 反例 · calibrated ↔ baseline 形状互锁（§3 约束 4，四个方向）
# ---------------------------------------------------------------------------


def test_calibrated_true_with_empty_source_file_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      source_file: "evals/baseline/business.json"',
        '      source_file: ""',
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_calibrated_true_with_empty_reading_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      reading_key: "handoff_metrics.handoff_rate"',
        '      reading_key: "  "',
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_calibrated_true_with_null_copied_value_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace("copied_value: 0.3654", "copied_value: null")
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_calibrated_false_with_non_none_source_file_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      source_file: "none"\n      reading_key: "none"\n      copied_value: null\n'
        '      copied_at: "2026-11"\n      note: "未经基线校准的占位初值"',
        '      source_file: "somewhere.json"\n      reading_key: "none"\n'
        '      copied_value: null\n      copied_at: "2026-11"\n'
        '      note: "未经基线校准的占位初值"',
        1,
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_calibrated_false_with_non_none_reading_key_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      source_file: "none"\n      reading_key: "none"',
        '      source_file: "none"\n      reading_key: "some.key"',
        1,
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_calibrated_false_with_non_null_copied_value_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      source_file: "none"\n      reading_key: "none"\n      copied_value: null',
        '      source_file: "none"\n      reading_key: "none"\n      copied_value: 0.5',
        1,
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


def test_calibrated_false_with_a_note_missing_the_marker_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        '      note: "未经基线校准的占位初值"',
        '      note: "随手写的备注"',
        1,
    )
    with pytest.raises(ValueError):
        _load(tmp_path, body)


# ---------------------------------------------------------------------------
# §9-2 真 YAML 逐键断言（字面对象是 `.qoder/rules/online-signals-and-notification.md` §3）
# ---------------------------------------------------------------------------

_EXPECTED_ACTION_SUGGESTIONS: dict[str, str] = {
    "no_evidence_rate": (
        "查检索与证据判定：核对 policy.search 的空因分布与 no_evidence 判定线，确认知识库版本。"
    ),
    "handoff_rate": (
        "查澄清与门：核对 clarification_rounds 与 escalation 触发分布，确认是否有单信号风暴。"
    ),
    "tool_failure_rate": (
        "查熔断与上游：按 tool_name 拆失败分布，核对 timeout 与上游服务健康。"
    ),
}


def test_the_committed_yaml_carries_the_pinned_rows() -> None:
    """加载 `app/core/online_signals.yaml`，逐键断言三行、三 id、三阈值与 calibrated 位。"""
    from app.core.online_signals import load_online_signals

    table = load_online_signals(REPO_ROOT / "app" / "core" / "online_signals.yaml")

    assert [s.signal_id for s in table.signals] == [
        "no_evidence_rate",
        "handoff_rate",
        "tool_failure_rate",
    ]
    assert table.by_id("no_evidence_rate").threshold == 0.20
    assert table.by_id("handoff_rate").threshold == 0.55
    assert table.by_id("tool_failure_rate").threshold == 0.10

    assert table.by_id("no_evidence_rate").calibrated is False
    assert table.by_id("handoff_rate").calibrated is True
    assert table.by_id("tool_failure_rate").calibrated is False

    for signal_id in ("no_evidence_rate", "handoff_rate", "tool_failure_rate"):
        assert table.by_id(signal_id).realert_minutes == 15


def test_the_committed_yaml_action_suggestions_are_verbatim() -> None:
    """三条 `action_suggestion` 逐字节相等（红线 §6-1：静态字段、不由运行时拼接）。"""
    from app.core.online_signals import load_online_signals

    table = load_online_signals(REPO_ROOT / "app" / "core" / "online_signals.yaml")
    for signal_id, expected in _EXPECTED_ACTION_SUGGESTIONS.items():
        actual = table.by_id(signal_id).action_suggestion
        assert actual == expected, (
            f"{signal_id} action_suggestion drift:\n"
            f"  expected: {expected!r}\n"
            f"  actual:   {actual!r}"
        )


# ---------------------------------------------------------------------------
# §9-4 抄本一致性（fail-closed）：现读 evals/baseline/business.json
# ---------------------------------------------------------------------------


def _load_baseline_file(path: Path) -> dict[str, Any]:
    """读一份基线 JSON；文件缺失即 `AssertionError`，不静默跳过。"""
    if not path.is_file():
        raise AssertionError(f"baseline file missing: {path.as_posix()}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"baseline payload is not a mapping: {payload!r}")
    return payload


def _compare_copied_value(table: OnlineSignalTable, payload: dict[str, Any]) -> None:
    """抄本一致性：`calibrated: true` 行的 `copied_value` 必须逐字等于
    `readings[reading_key]`。键缺失 / 值不符一律失败，不返回默认值。"""
    readings = payload.get("readings")
    if not isinstance(readings, dict):
        raise AssertionError("baseline payload has no `readings` mapping.")
    for signal in table.signals:
        if not signal.calibrated:
            continue
        key = signal.baseline.reading_key
        if key not in readings:
            raise AssertionError(
                f"baseline reading key missing: {key!r} (from {signal.signal_id})."
            )
        actual = readings[key]
        if actual != signal.baseline.copied_value:
            raise AssertionError(
                f"transcript drift on {signal.signal_id}: copied_value="
                f"{signal.baseline.copied_value!r} baseline={actual!r}."
            )


def test_transcript_matches_the_committed_baseline_file() -> None:
    """§9-4：YAML 抄本 == 现读 `evals/baseline/business.json` 的读数。"""
    from app.core.online_signals import load_online_signals

    table = load_online_signals(REPO_ROOT / "app" / "core" / "online_signals.yaml")
    payload = _load_baseline_file(REPO_ROOT / "evals" / "baseline" / "business.json")
    _compare_copied_value(table, payload)
    # 显式钉一下具体数值，防止 helper 无声通过。
    handoff = table.by_id("handoff_rate")
    assert handoff.baseline.copied_value == payload["readings"][handoff.baseline.reading_key]
    assert handoff.baseline.copied_value == 0.3654


def test_transcript_fails_when_the_baseline_file_is_missing(tmp_path: Path) -> None:
    """坏情形①文件缺失：helper 断言失败，不静默跳过。"""
    missing = tmp_path / "evals" / "baseline" / "business.json"
    with pytest.raises(AssertionError):
        _load_baseline_file(missing)


def test_transcript_fails_when_the_reading_key_is_missing(tmp_path: Path) -> None:
    """坏情形②键缺失：inline fixture 照 `tests/test_metrics_baseline.py` 形状。"""
    from app.core.online_signals import load_online_signals

    table = load_online_signals(REPO_ROOT / "app" / "core" / "online_signals.yaml")
    payload: dict[str, Any] = {
        "_meta": {"recorded_at": "2026-10-28T12:53:08", "source": "business"},
        "readings": {"handoff_metrics.conversations": 52},
    }
    with pytest.raises(AssertionError):
        _compare_copied_value(table, payload)


def test_transcript_fails_when_the_reading_value_drifts(tmp_path: Path) -> None:
    """坏情形③值不符：inline fixture 用一个不同的数，helper 必须报错。"""
    from app.core.online_signals import load_online_signals

    table = load_online_signals(REPO_ROOT / "app" / "core" / "online_signals.yaml")
    payload: dict[str, Any] = {
        "_meta": {"recorded_at": "2026-10-28T12:53:08", "source": "business"},
        "readings": {"handoff_metrics.handoff_rate": 0.9999},
    }
    with pytest.raises(AssertionError):
        _compare_copied_value(table, payload)


# ---------------------------------------------------------------------------
# §9-12 A/C 未经校准可检索
# ---------------------------------------------------------------------------


def test_uncalibrated_rows_are_flagged_in_the_yaml() -> None:
    """§9-12：A/C 两行 `calibrated: false` 且 `baseline.note` 含「未经基线校准」。"""
    from app.core.online_signals import load_online_signals

    table = load_online_signals(REPO_ROOT / "app" / "core" / "online_signals.yaml")
    for signal_id in ("no_evidence_rate", "tool_failure_rate"):
        signal = table.by_id(signal_id)
        assert signal.calibrated is False, f"{signal_id} must be calibrated: false"
        assert "未经基线校准" in signal.baseline.note, (
            f"{signal_id} baseline.note must contain 「未经基线校准」; got {signal.baseline.note!r}"
        )


# ---------------------------------------------------------------------------
# §9-3 YAML 侧：escalation.yaml 的 `no_evidence` 块 intents
# 注：T2 只钉 YAML 侧，生产侧消费（信号 A 分子/分母与意图集合的实际关联）在 T3。
# ---------------------------------------------------------------------------


def test_escalation_no_evidence_intents_are_pinned_to_the_two_expected_values() -> None:
    """漂移即 fail：`app/agent/escalation.yaml` 的 `no_evidence` 块 intents 必须
    是 {POLICY_QA, SELF_ANALYTICS}。T2 只钉 YAML 侧。"""
    escalation_path = REPO_ROOT / "app" / "agent" / "escalation.yaml"
    raw = yaml.safe_load(escalation_path.read_text(encoding="utf-8"))
    rows = raw["signals"]
    no_evidence = next(row for row in rows if row.get("signal_id") == "no_evidence")
    assert set(no_evidence["intents"]) == {"POLICY_QA", "SELF_ANALYTICS"}


# ---------------------------------------------------------------------------
# loader 的纯度（照 `test_approval_tiers.py:326` 形态）
# ---------------------------------------------------------------------------


def test_the_loader_reads_neither_clock_nor_random_nor_model_nor_database() -> None:
    import inspect

    from app.core import online_signals

    source = inspect.getsource(online_signals)
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
