"""F-1 单轮反问数据载体的契约测试（口径 §4.6）。

口径来源：``.qoder/rules/clarify-counter-question.md`` §4.5（问句形状）、
§4.6（数据落点：``app/agent/counter_question.yaml`` 顶层键封闭集合
``version`` / ``prompt_template`` / ``labels`` / ``max_options``，
``labels`` 必须覆盖 ``intents.yaml`` 当前 12 行）、§4.3（``max_options``
的口径值是 3，属数据不是配置）。

覆盖关系不在 loader 里校验（loader 不得跨文件耦合）——由本文件用
``app.agent.intent_catalog`` 现算目录的意图行，断言 ``labels`` 键集等于它。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.agent.counter_question import (
    CounterQuestionSpec,
    get_counter_question,
    load_counter_question,
)

_REPO_YAML = Path(__file__).resolve().parents[1] / "app" / "agent" / "counter_question.yaml"

VALID_TABLE = """\
version: "2026-10-29"
prompt_template: "请问您想咨询的是下面哪一类："
labels:
  ORDER_QUERY: "查询订单"
  UNKNOWN: "都不是"
max_options: 3
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "counter_question.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_repo_table_loads_and_exposes_the_three_fields() -> None:
    spec = load_counter_question(_REPO_YAML)
    assert isinstance(spec, CounterQuestionSpec)
    assert isinstance(spec.version, str) and spec.version.strip()
    assert isinstance(spec.prompt_template, str) and spec.prompt_template.strip()
    assert isinstance(spec.max_options, int) and spec.max_options > 0
    assert spec.labels


def test_repo_max_options_is_the_calibrated_three() -> None:
    # 口径 §4.3/§4.6：3 是写进 YAML 的数据值（loader 不断言等于 3，
    # 「必须等于 3」是口径判定，钉在测试侧）。
    assert load_counter_question(_REPO_YAML).max_options == 3


def test_labels_cover_exactly_the_intent_catalog_rows() -> None:
    from app.agent.intent_catalog import get_catalog

    spec = load_counter_question(_REPO_YAML)
    catalog_ids = set(get_catalog().specs)
    assert set(spec.labels) == catalog_ids
    assert len(catalog_ids) == 12, "intents.yaml 当前是 12 行（口径 §4.6 原文）"


def test_labels_are_non_empty_display_names() -> None:
    spec = load_counter_question(_REPO_YAML)
    for intent_id, label in spec.labels.items():
        assert isinstance(intent_id, str) and intent_id.strip()
        assert isinstance(label, str) and label.strip(), f"{intent_id} 的显示名为空"


def test_prompt_template_is_the_trunk_without_the_handoff_tail() -> None:
    # 口径 §4.5：兜底句复用 _clarify 尾句原文，不进 YAML。
    spec = load_counter_question(_REPO_YAML)
    assert "你也可以直接说" not in spec.prompt_template


def test_two_loads_of_the_same_table_are_field_for_field_identical() -> None:
    first = load_counter_question(_REPO_YAML)
    second = load_counter_question(_REPO_YAML)
    assert first is not second
    assert first == second
    assert first.labels == second.labels


def test_the_cached_getter_serves_the_repo_table() -> None:
    fresh = load_counter_question(_REPO_YAML)
    cached = get_counter_question()
    assert cached == fresh
    assert get_counter_question.cache_parameters()["maxsize"] == 1


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_TABLE + "extra: 1\n")
    with pytest.raises(ValueError):
        load_counter_question(path)


@pytest.mark.parametrize(
    "removed",
    [
        'version: "2026-10-29"\n',
        'prompt_template: "请问您想咨询的是下面哪一类："\n',
        'labels:\n  ORDER_QUERY: "查询订单"\n  UNKNOWN: "都不是"\n',
        "max_options: 3\n",
    ],
)
def test_missing_top_level_field_is_rejected(tmp_path: Path, removed: str) -> None:
    body = VALID_TABLE.replace(removed, "")
    assert body != VALID_TABLE, "删除片段必须命中样例文本"
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_counter_question(path)


@pytest.mark.parametrize("value", ["0", "-1", '"3"', "1.5", "true", "null"])
def test_max_options_must_be_a_positive_integer(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, VALID_TABLE.replace("max_options: 3", f"max_options: {value}"))
    with pytest.raises(ValueError):
        load_counter_question(path)


@pytest.mark.parametrize(
    "block",
    [
        "labels: {}",
        "labels: []",
        "labels: 查询订单",
        'labels:\n  ORDER_QUERY: ""',
        'labels:\n  ORDER_QUERY: "  "',
        'labels:\n  ORDER_QUERY: 3',
    ],
)
def test_labels_shape_is_enforced(tmp_path: Path, block: str) -> None:
    body = VALID_TABLE.replace(
        'labels:\n  ORDER_QUERY: "查询订单"\n  UNKNOWN: "都不是"', block
    )
    assert body != VALID_TABLE
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_counter_question(path)


def test_empty_prompt_template_is_rejected(tmp_path: Path) -> None:
    body = VALID_TABLE.replace(
        'prompt_template: "请问您想咨询的是下面哪一类："', 'prompt_template: ""'
    )
    path = _write(tmp_path, body)
    with pytest.raises(ValueError):
        load_counter_question(path)


def test_a_non_mapping_table_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "- 我是列表不是映射\n")
    with pytest.raises(ValueError):
        load_counter_question(path)


def test_the_loader_reads_neither_clock_nor_random_nor_model_nor_database() -> None:
    from app.agent import counter_question as loader_module

    source = inspect.getsource(loader_module)
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
