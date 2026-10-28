"""退款原因关键词规则表的加载与匹配测试。

口径来源：`.qoder/rules/refund-reason-taxonomy.md` §5.6.1（表结构与加载期校验）、
§5.6.2（命中即填槽、未命中不填）。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

VALID_TABLE = """
version: "2026-10-26"
slot: refund_reason
enum: RefundReason
other_cues: ["其他", "说不清"]
rules:
  - refund_reason: DAMAGED
    cues: ["破损", "摔坏"]
  - refund_reason: QUALITY
    cues: ["质量", "故障"]
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "refund_reason_rules.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_default_table_loads_and_every_target_is_a_refund_reason_member() -> None:
    from app.agent.refund_reason_rules import get_refund_reason_rules
    from app.models.enums import RefundReason

    table = get_refund_reason_rules()
    assert table.slot == "refund_reason"
    assert table.enum == "RefundReason"
    assert table.rules
    for rule in table.rules:
        assert isinstance(rule.refund_reason, RefundReason)
    assert all(isinstance(cue, str) and cue for rule in table.rules for cue in rule.cues)


def test_cues_are_lowercase_substrings_and_matching_lowercases_the_text() -> None:
    from app.agent.refund_reason_rules import get_refund_reason_rules, load_refund_reason_rules
    from app.models.enums import RefundReason

    table = get_refund_reason_rules()
    for cue in table.other_cues:
        assert cue == cue.lower(), "other_cues 必须是小写子串"
        assert cue.strip() == cue and cue, "other_cues 必须是非空子串"
    for rule in table.rules:
        for cue in rule.cues:
            assert cue == cue.lower(), "cues 必须是小写子串"
            assert cue.strip() == cue and cue, "cues 必须是非空子串"
    assert table.match("商品破损了") is RefundReason.DAMAGED
    # 两次独立加载同一张表、同一段入站文本：两次读出的结果必须一致。
    # （旧写法 `table.match(x) is table.match(x)` 是同一个表达式比同一个表达式，恒真。）
    repo_table = Path(__file__).resolve().parents[1] / "app" / "agent" / "refund_reason_rules.yaml"
    first = load_refund_reason_rules(repo_table)
    second = load_refund_reason_rules(repo_table)
    assert first is not second, "两次加载必须是各自独立的表对象"
    # 一段同时命中多行的文本：行序就是优先级（口径 §5.6.1），
    # 两次加载必须给出同一个「先命中的那一行」。
    multi_row_text = f"{table.rules[0].cues[0]} {table.rules[-1].cues[0]}"
    assert first.match(multi_row_text) is table.rules[0].refund_reason
    assert second.match(multi_row_text) is table.rules[0].refund_reason
    assert first.match(multi_row_text) is second.match(multi_row_text)
    assert first.match("商品破损了") is second.match("商品破损了") is RefundReason.DAMAGED


def test_the_inbound_text_is_lowercased_before_the_cues_are_compared(tmp_path: Path) -> None:
    """入站文本先转小写再比 cue：表里的 cue 是小写，入站可能是大写。

    口径 §5.6.1：cue 是小写子串，匹配前把入站文本转小写。
    这条用一张**临时表**（ASCII 小写 cue）来钉住这一步——仓库里的 cue 全是中文，
    删掉 ``.lower()`` 时中文表看不出差别。
    """
    from app.agent.refund_reason_rules import load_refund_reason_rules
    from app.models.enums import RefundReason

    body = """
version: "2026-10-26"
slot: refund_reason
enum: RefundReason
other_cues: ["other"]
rules:
  - refund_reason: QUALITY
    cues: ["quality"]
"""
    path = _write(tmp_path, body)
    table = load_refund_reason_rules(path)

    assert table.match("Quality") is RefundReason.QUALITY
    assert table.match("QUALITY") is RefundReason.QUALITY
    assert table.match("a Quality problem") is RefundReason.QUALITY


def test_the_first_matching_row_decides() -> None:
    from app.agent.refund_reason_rules import get_refund_reason_rules

    table = get_refund_reason_rules()
    first, second = table.rules[0], table.rules[1]
    assert first.refund_reason is not second.refund_reason
    text = f"{first.cues[0]} {second.cues[0]}"
    assert table.match(text) is first.refund_reason


def test_an_explicit_other_wins_over_the_rule_rows() -> None:
    from app.agent.refund_reason_rules import get_refund_reason_rules
    from app.models.enums import RefundReason

    table = get_refund_reason_rules()
    cue = table.other_cues[0]
    text = f"{cue} {table.rules[0].cues[0]}"
    assert table.match(text) is RefundReason.OTHER
    assert table.match(cue) is RefundReason.OTHER


def test_a_miss_produces_no_value_at_all() -> None:
    from app.agent.refund_reason_rules import get_refund_reason_rules
    from app.models.enums import RefundReason

    table = get_refund_reason_rules()
    assert table.match("我要退款，订单 ORD-DEMO-1001，金额 129 元") is None
    assert table.match("") is None
    assert table.match("随便一句话") is not RefundReason.OTHER


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE + "extra: 1\n")
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_unknown_rule_key_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    body = VALID_TABLE.replace(
        'cues: ["破损", "摔坏"]',
        'cues: ["破损", "摔坏"]\n    reason: "DAMAGED"',
    )
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_a_foreign_slot_name_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE.replace("slot: refund_reason", "slot: refund_type"))
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_a_foreign_enum_name_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE.replace("enum: RefundReason", "enum: RefundType"))
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_a_target_outside_the_enum_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE.replace("refund_reason: DAMAGED", "refund_reason: BROKEN"))
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_other_cannot_be_a_rule_target(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE.replace("refund_reason: DAMAGED", "refund_reason: OTHER"))
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_a_rule_without_cues_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE.replace('cues: ["破损", "摔坏"]', "cues: []"))
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_an_empty_table_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    body = VALID_TABLE.split("rules:")[0] + "rules: []\n"
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_an_empty_other_cues_table_is_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    path = _write(tmp_path, VALID_TABLE.replace('other_cues: ["其他", "说不清"]', "other_cues: []"))
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_duplicated_other_cues_are_rejected(tmp_path: Path) -> None:
    from app.agent.refund_reason_rules import load_refund_reason_rules

    body = VALID_TABLE.replace(
        'other_cues: ["其他", "说不清"]', 'other_cues: ["其他", "其他"]'
    )
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_refund_reason_rules(path)


def test_the_loader_reads_neither_clock_nor_random_nor_model_nor_database() -> None:
    from app.agent import refund_reason_rules as loader

    source = inspect.getsource(loader)
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
