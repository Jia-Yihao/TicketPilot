"""在线信号阈值与基线抄本表。

数据文件 `online_signals.yaml` 存三个信号（`no_evidence_rate` / `handoff_rate` /
`tool_failure_rate`）的阈值、`baseline` 抄本与动作建议；本模块只做「读 + 校验 +
缓存一份」——不写文件、不读时钟、不用随机、不调模型、不查数据库。

口径来源：`app/core/online_signals.yaml`。

- §3 约束 1：顶层键封闭 `{version, signals}`；`signals` 恰三行，`signal_id`
  取封闭词表 `{no_evidence_rate, handoff_rate, tool_failure_rate}` 且互不重复。
- §3 约束 2：信号行键封闭 `{signal_id, threshold, calibrated, realert_minutes,
  baseline, action_suggestion}`；`baseline` 块键封闭 `{source_file, reading_key,
  copied_value, copied_at, note}`。未知键即 `ValueError`，加载期失败。
- §3 约束 3：`threshold: float ∈ (0, 1]`；`realert_minutes: int > 0`；
  `action_suggestion` 为非空字符串。
- §3 约束 4：`calibrated` 与 `baseline` 形状互锁——`true` 要求抄本四件齐
  （`source_file`/`reading_key` 非空、`copied_value` 不为 `null`）；`false`
  要求 `source_file`/`reading_key` 为字面 `"none"`、`copied_value` 为 `null`、
  `note` 含「未经基线校准」。

**任何违反契约的行都是错误，不是跳过的行**——理由与 `approval_tiers` 一致：
悄悄跳一行会让运行期读到一份没人评审过的表。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "OnlineSignal",
    "OnlineSignalTable",
    "SignalBaseline",
    "get_online_signals",
    "load_online_signals",
]

_SIGNALS_PATH = Path(__file__).with_name("online_signals.yaml")

_TOP_FIELDS = frozenset({"version", "signals"})
_SIGNAL_FIELDS = frozenset(
    {
        "signal_id",
        "threshold",
        "calibrated",
        "realert_minutes",
        "baseline",
        "action_suggestion",
    }
)
_BASELINE_FIELDS = frozenset(
    {"source_file", "reading_key", "copied_value", "copied_at", "note"}
)

# §3 约束 1：封闭词表，恰三行，无重复。
_ALLOWED_SIGNAL_IDS = frozenset(
    {"no_evidence_rate", "handoff_rate", "tool_failure_rate"}
)
_REQUIRED_SIGNAL_ROWS = 3

# §3 约束 4：`calibrated: false` 的字面三件套与 note 关键词。
_UNCALIBRATED_LITERAL = "none"
_UNCALIBRATED_NOTE_MARKER = "未经基线校准"


@dataclass(frozen=True, slots=True)
class SignalBaseline:
    """一行信号的 `baseline` 抄本块。

    `calibrated: true` 时携带实际抄本（`copied_value` 为数值），`false` 时
    `source_file`/`reading_key` 是字面 `"none"`、`copied_value` 为 `None`。
    """

    source_file: str
    reading_key: str
    copied_value: Any
    copied_at: str
    note: str


@dataclass(frozen=True, slots=True)
class OnlineSignal:
    """一行在线信号：id、阈值、是否已基线校准、复警间隔、抄本、动作建议。"""

    signal_id: str
    threshold: float
    calibrated: bool
    realert_minutes: int
    baseline: SignalBaseline
    action_suggestion: str


@dataclass(frozen=True, slots=True)
class OnlineSignalTable:
    """加载后的整表：三行信号 + 版本标识。"""

    version: str
    signals: tuple[OnlineSignal, ...]

    def __post_init__(self) -> None:
        if len(self.signals) != _REQUIRED_SIGNAL_ROWS:
            raise ValueError(
                f"The online signal table must carry exactly "
                f"{_REQUIRED_SIGNAL_ROWS} signals; got {len(self.signals)}."
            )

    def by_id(self, signal_id: str) -> OnlineSignal:
        """按 `signal_id` 取一行；不存在即 `KeyError`，不返回默认值。"""
        for signal in self.signals:
            if signal.signal_id == signal_id:
                return signal
        raise KeyError(signal_id)


def _reject_unknown(allowed: frozenset[str], mapping: dict[str, Any], where: str) -> None:
    """未知键即错——照 `app/core/approval_tiers.py:127` 的白名单在前形。

    本仓另有 `app/agent/intent_catalog.py::_reject_unknown`，参数顺序相反；
    不跨模块 import 私有符号，此处按同层先例复制一份。
    """
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{where} has unknown keys: {', '.join(unknown)}.")


def _read_signal_id(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or raw not in _ALLOWED_SIGNAL_IDS:
        allowed = ", ".join(sorted(_ALLOWED_SIGNAL_IDS))
        raise ValueError(
            f"{where} has a signal_id outside the closed vocabulary: {raw!r}. "
            f"Allowed: {allowed}."
        )
    return raw


def _read_threshold(raw: Any, where: str) -> float:
    # bool 是 int 的子类，必须先挡掉，否则 `threshold: true` 会被当成 1.0 放行。
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{where} must have a numeric threshold in (0, 1].")
    value = float(raw)
    if not (0.0 < value <= 1.0):
        raise ValueError(f"{where} has threshold {value} outside (0, 1].")
    return value


def _read_calibrated(raw: Any, where: str) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"{where} must have a boolean calibrated flag.")
    return raw


def _read_realert_minutes(raw: Any, where: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{where} must have an integer realert_minutes.")
    if raw <= 0:
        raise ValueError(f"{where} must have realert_minutes greater than 0; got {raw}.")
    return int(raw)


def _read_action_suggestion(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{where} must have a non-empty action_suggestion.")
    return raw


def _read_baseline(raw: Any, calibrated: bool, where: str) -> SignalBaseline:
    """按 `calibrated` 校验 `baseline` 块的形状（§3 约束 4，双向互锁）。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must carry a baseline mapping.")
    _reject_unknown(_BASELINE_FIELDS, raw, f"{where} baseline")

    source_file_raw = raw.get("source_file")
    reading_key_raw = raw.get("reading_key")
    copied_value_raw: Any = raw.get("copied_value")
    copied_at_raw = raw.get("copied_at")
    note_raw = raw.get("note")

    if not isinstance(source_file_raw, str):
        raise ValueError(f"{where} baseline.source_file must be a string.")
    if not isinstance(reading_key_raw, str):
        raise ValueError(f"{where} baseline.reading_key must be a string.")
    if not isinstance(copied_at_raw, str) or not copied_at_raw.strip():
        raise ValueError(f"{where} baseline.copied_at must be a non-empty string.")
    if not isinstance(note_raw, str):
        raise ValueError(f"{where} baseline.note must be a string.")

    if calibrated:
        if not source_file_raw.strip():
            raise ValueError(
                f"{where} calibrated: true requires a non-empty baseline.source_file."
            )
        if not reading_key_raw.strip():
            raise ValueError(
                f"{where} calibrated: true requires a non-empty baseline.reading_key."
            )
        if copied_value_raw is None:
            raise ValueError(
                f"{where} calibrated: true requires baseline.copied_value to be set."
            )
    else:
        if source_file_raw != _UNCALIBRATED_LITERAL:
            raise ValueError(
                f"{where} calibrated: false requires baseline.source_file to be "
                f"{_UNCALIBRATED_LITERAL!r}; got {source_file_raw!r}."
            )
        if reading_key_raw != _UNCALIBRATED_LITERAL:
            raise ValueError(
                f"{where} calibrated: false requires baseline.reading_key to be "
                f"{_UNCALIBRATED_LITERAL!r}; got {reading_key_raw!r}."
            )
        if copied_value_raw is not None:
            raise ValueError(
                f"{where} calibrated: false requires baseline.copied_value to be null."
            )
        if _UNCALIBRATED_NOTE_MARKER not in note_raw:
            raise ValueError(
                f"{where} calibrated: false requires baseline.note to contain "
                f"{_UNCALIBRATED_NOTE_MARKER!r}."
            )

    return SignalBaseline(
        source_file=source_file_raw,
        reading_key=reading_key_raw,
        copied_value=copied_value_raw,
        copied_at=copied_at_raw,
        note=note_raw,
    )


def load_online_signals(path: Path) -> OnlineSignalTable:
    """读一份 YAML 并逐条校验契约（§3 约束 1–4）。

    **纯加载**：不读时钟、不用随机、不调模型、不查数据库、不写文件；
    同一份文件两次加载的结果逐字一致。任何违反契约的行都是错误。
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The online signal table must be a mapping.")
    _reject_unknown(_TOP_FIELDS, raw, "The online signal table")

    raw_signals = raw.get("signals")
    if not isinstance(raw_signals, list):
        raise ValueError("The online signal table has no signals list.")
    if len(raw_signals) != _REQUIRED_SIGNAL_ROWS:
        raise ValueError(
            f"The online signal table must carry exactly {_REQUIRED_SIGNAL_ROWS} "
            f"signals; got {len(raw_signals)}."
        )

    signals: list[OnlineSignal] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_signals):
        where = f"Online signal {index + 1}"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be a mapping.")
        _reject_unknown(_SIGNAL_FIELDS, item, where)

        signal_id = _read_signal_id(item.get("signal_id"), where)
        if signal_id in seen_ids:
            raise ValueError(f"{where} repeats the signal_id {signal_id}.")
        seen_ids.add(signal_id)

        threshold = _read_threshold(item.get("threshold"), where)
        calibrated = _read_calibrated(item.get("calibrated"), where)
        realert_minutes = _read_realert_minutes(item.get("realert_minutes"), where)
        action_suggestion = _read_action_suggestion(item.get("action_suggestion"), where)
        baseline = _read_baseline(item.get("baseline"), calibrated, where)

        signals.append(
            OnlineSignal(
                signal_id=signal_id,
                threshold=threshold,
                calibrated=calibrated,
                realert_minutes=realert_minutes,
                baseline=baseline,
                action_suggestion=action_suggestion,
            )
        )

    return OnlineSignalTable(version=str(raw.get("version", "0")), signals=tuple(signals))


@lru_cache(maxsize=1)
def get_online_signals() -> OnlineSignalTable:
    """整进程一份在线信号表；改 YAML 后重启生效，不承诺热加载。

    形状与本仓五处同形先例（`approval_tiers` / `intent_catalog` / `escalation`
    / `counter_question` / `refund_reason_rules`）一致，因此**只能调用
    `_SIGNALS_PATH` 上的 `load_online_signals`**——失败样例必须走纯加载函数、
    不能走这个缓存 getter，否则第二次加载会返回第一次的结果。
    """
    return load_online_signals(_SIGNALS_PATH)
