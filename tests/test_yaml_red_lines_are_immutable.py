"""红线静态锁定测试——六份 YAML 的字节不可变。

这些 YAML 承载的是「退款能退多少、什么时候转人工、审批分几档」这类业务口径，
改动它们等于改产品承诺，因此钉成字节锁：改了就要人看见，而不是混进一次普通提交。
- 六份 YAML **一个字节都不许多改**（`online_signals.yaml` 的「loader 测试只读」
  豁免只免运行时写入路径，不免本条字节锁）。
  本文件用 digest 钉死的 fail-closed 锁实现：每份文件记录一份**落地当下从盘上
  现算**的 SHA-256 字面值，断言读出的字节在**把 CRLF 归一成 LF 之后**摘要相等。
  归一化是必须的：同一份内容在 Windows 检出是 CRLF、在 Linux 检出是 LF，
  直接对 `rb` 字节取摘要会让守卫在两个平台上给出不同结论——那测的是换行符，
  不是口径。归一之后「一个字节都不许多改」仍然成立（除换行符以外的字节全在摘要里）。
  另两支同样的锁在 `tests/test_monitor_api.py`（`app/**` 无 YAML 写路径源码扫描、
  action_suggestion 字节相等）与 `tests/test_online_signals.py`（`calibrated: false`
  与 note 字样）。

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
# digest 为 ./.venv/Scripts/python.exe 现算的
# hashlib.sha256(bytes.replace(b"\r\n", b"\n")).hexdigest()，不是想象值。
# 取值时点：五份 LF 文件的摘要沿用 2026-10-30 落地值；escalation.yaml 原地是
# CRLF 检出，按归一化口径重钉（内容未改，只是把换行符从摘要里排除）。
# 改这些字面值 = 改红线，必须先有口径裁定，不是测试落后于实现的顺手修。
# ---------------------------------------------------------------------------

RED_LINE_YAMLS: tuple[tuple[str, str], ...] = (
    ("app/agent/intents.yaml",
     "eae0907baf41c84e9d3ce8abc9f0634e40c823484b9c9a6771386e21512f2435"),
    ("app/agent/escalation.yaml",
     "ef50d0afd8c926e9da43befd5897b073b9e46990dce53e8896b32c46f8186754"),
    ("app/core/approval_tiers.yaml",
     "e68206fa454f729e8dbea41143a33a0eff2b0ff7ac68880c3e331a794fb65510"),
    ("app/agent/counter_question.yaml",
     "3b33be3a54a6343d00b742bd02aec3cc034c019b162b4e822b382c319cb6d5e8"),
    ("app/agent/refund_reason_rules.yaml",
     "1de0083da148fa258002e645f4eeca8f3f6ea36709a198b03d61488eb7d8c1c7"),
    ("app/core/online_signals.yaml",
     "a1e156572d14bdea3cc26185b32fda146c8cf17e5842d909e8266803b04fec1e"),
)


@pytest.mark.parametrize(("rel_path", "expected_sha256"), RED_LINE_YAMLS)
def test_the_red_line_yaml_bytes_are_immutable(rel_path: str, expected_sha256: str) -> None:
    path = REPO_ROOT / rel_path
    # fail-closed：缺文件是失败不是跳过。
    assert path.is_file(), f"红线 YAML 缺失：{rel_path}（六 YAML 一份都不能少）"
    actual = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
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


