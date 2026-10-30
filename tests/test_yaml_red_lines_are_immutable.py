"""R11 T5：红线静态锁定测试——六 YAML 字节不可变与口径自检索条款。

口径来源：`.qoder/rules/online-signals-and-notification.md`
- §6-3 / §9-10：六份 YAML 在 R11 期间**一个字节都不许多改**（`online_signals.yaml`
  的「loader 测试只读」豁免只免运行时写入路径，不免本条字节锁）。
  本文件用 digest 钉死的 fail-closed 锁实现：每份文件记录一份**落地当下从盘上
  现算**的 SHA-256 字面值，断言 `rb` 模式读出的字节摘要逐字相等。
  §9-10 的另外两支（`app/**` 无 YAML 写路径源码扫描、action_suggestion 字节相等）
  已由 T4 落在 `tests/test_monitor_api.py:329` 与 `:516`。
- §9-11 前半：口径文件内「R11 的信号采集与判定零模型调用」必须可检索
  （后半模型桩断言已由 T4 落在 `tests/test_monitor_api.py:399`）。
- §9-12 后半：口径文件内「未经基线校准的占位初值」字样必须可检索（grep 断言；
  同条前半的 YAML 两行 `calibrated: false` 与 note 字样要求已落在
  `tests/test_online_signals.py:541`）。

形状先例：`tests/test_docs_consistency.py:470-487` 的 source_sha256 字面比较。
失败闭口：文件缺失即断言失败，**不得 skip**；parametrize 逐文件一行，
失败信息指名是哪一份 YAML 变了。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# §6-3 / §9-10：六 YAML 字节锁。
# digest 为 2026-10-30 T5 落地时用 ./.venv/Scripts/python.exe 现算的
# hashlib.sha256(<rb 读出的字节>).hexdigest()，不是想象值。
# 改这些字面值 = 改红线，必须先有口径裁定，不是测试落后于实现的顺手修。
# ---------------------------------------------------------------------------

RED_LINE_YAMLS: tuple[tuple[str, str], ...] = (
    ("app/agent/intents.yaml",
     "eae0907baf41c84e9d3ce8abc9f0634e40c823484b9c9a6771386e21512f2435"),
    ("app/agent/escalation.yaml",
     "bf1545aa13a139fcd59ecea8834f5a0c91d311c5f58c0983936bab4f84fc3ba9"),
    ("app/core/approval_tiers.yaml",
     "e68206fa454f729e8dbea41143a33a0eff2b0ff7ac68880c3e331a794fb65510"),
    ("app/agent/counter_question.yaml",
     "b3b819867134ff819359e726fdc1a2f7d6a27f254dcc3d18cbcfba25f96d72c5"),
    ("app/agent/refund_reason_rules.yaml",
     "1e1ba6c5098f05682a2c91fb4074ce8165613e131bb7e0c9a93355fb95b6851b"),
    ("app/core/online_signals.yaml",
     "a1e156572d14bdea3cc26185b32fda146c8cf17e5842d909e8266803b04fec1e"),
)


@pytest.mark.parametrize(("rel_path", "expected_sha256"), RED_LINE_YAMLS)
def test_the_red_line_yaml_bytes_are_immutable(rel_path: str, expected_sha256: str) -> None:
    path = REPO_ROOT / rel_path
    # fail-closed：缺文件是失败不是跳过。
    assert path.is_file(), f"红线 YAML 缺失：{rel_path}（六 YAML 一份都不能少）"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == expected_sha256, (
        f"红线 YAML 被改动：{rel_path}\n"
        f"  钉死的摘要：{expected_sha256}\n"
        f"  当前摘要　：{actual}\n"
        "  R11 期间六 YAML 一字节不许改（口径 §6-3/§9-10）；"
        "若确有口径裁定的改动，走 organizer 裁定后重新钉，不得改测试就放行。"
    )


# ---------------------------------------------------------------------------
# §9-11 前半：口径文件自检索条款
# ---------------------------------------------------------------------------


def test_the_rule_file_carries_the_zero_model_call_clause() -> None:
    """口径必须能在自身文件里检索到「R11 的信号采集与判定零模型调用」。

    只断言可检索（`in`），不断言出现次数——现盘命中 :7、:221、:264 三处，
    次数钉死会把口径的正常措辞修订也变成红。
    """
    rule_path = REPO_ROOT / ".qoder" / "rules" / "online-signals-and-notification.md"
    assert rule_path.is_file(), f"口径文件缺失：{rule_path.as_posix()}"
    text = rule_path.read_text(encoding="utf-8")
    assert "R11 的信号采集与判定零模型调用" in text, (
        "口径文件检索不到「R11 的信号采集与判定零模型调用」——"
        "§9-11 的自检索条款失效"
    )


# ---------------------------------------------------------------------------
# §9-12 后半：口径文件内占位初值字样必须可检索
# ---------------------------------------------------------------------------


def test_the_rule_file_carries_the_uncalibrated_placeholder_wording() -> None:
    """口径必须能在自身文件里检索到「未经基线校准的占位初值」。

    §9-12 后半的 grep 断言：现盘命中 :124 与 :164 两处。
    只断言可检索（`in`），不断言出现次数——次数钉死会把口径的正常
    措辞修订也变成红。
    """
    rule_path = REPO_ROOT / ".qoder" / "rules" / "online-signals-and-notification.md"
    assert rule_path.is_file(), f"口径文件缺失：{rule_path.as_posix()}"
    text = rule_path.read_text(encoding="utf-8")
    assert "未经基线校准的占位初值" in text, (
        "口径 §9-12 要求的字样「未经基线校准的占位初值」在口径文件里检索不到——"
        "§9-12 后半的 grep 断言失效"
    )
