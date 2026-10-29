"""意图桶防退化断言：`bucket` 不是判定门（R10 T3 改判后的诚实最小范围）。

`docs/下一步规划.md` §14.5 那句「桶不是出口」原先只是叙述，本模块把它
变成断言：把 `bucket` 当意图桶来读的 `app/` 源码文件集合，等于且只等于
`app/agent/candidates.py`（候选截断排序与 L2 序列化两处）。词边界扫描下
`app/` 的命中一共 7 个文件，其余 6 个全是同名异物——令牌桶 1 个（限流）与
SQL 分组 5 个——按文件登记在下面这份显式白名单里。白名单条目必须是真实扫描
命中：空登记（例如只出现 `TokenBucket` / `buckets` 而没有独立单词 `bucket`
的文件）不会被扫描命中，留着只会静默扩大盲区。

将来若有人把意图桶读进任何判定/出口路径（`candidates.py` 之外），本测试
必须红，并把「意外的读取文件」列出来。阈值族不扩、未知 kind 不回落两条
已由 `tests/test_policy_engine.py`（:194、:196、:203）钉住，此处不重复。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

# 意图桶（build_candidates 算出的 bucket）唯一允许的生产读取文件。
_INTENT_BUCKET_READERS = {"app/agent/candidates.py"}

# 与意图桶无关的同名命中，按文件显式登记（白名单）。
_NON_INTENT_BUCKET_FILES = {
    "app/core/rate_limit.py",  # 令牌桶：限流存储与桶实现
    "app/sql/builder.py",  # SQL 分组：按桶聚合查询
    "app/sql/narrative.py",  # SQL 分组：叙述层复用分组桶
    "app/sql/prefetch.py",  # SQL 分组：预取按桶归并
    "app/sql/service.py",  # SQL 分组：服务层透传分组桶
    "app/sql/snapshot.py",  # SQL 分组：快照按桶聚合
}


def _bucket_reader_files() -> set[str]:
    """返回 app/ 下正文里出现独立单词 `bucket`（词边界、大小写敏感）的源文件。"""
    pattern = re.compile(r"\bbucket\b")
    readers: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            readers.add(path.relative_to(ROOT).as_posix())
    return readers


def test_bucket_is_read_as_intent_bucket_only_in_candidates() -> None:
    readers = _bucket_reader_files() - _NON_INTENT_BUCKET_FILES
    unexpected = sorted(readers - _INTENT_BUCKET_READERS)
    missing = sorted(_INTENT_BUCKET_READERS - readers)
    assert not unexpected, f"意外的读取文件（意图桶不得在这些文件被读）：{unexpected}"
    assert not missing, f"意图桶读取点消失，请复核断言是否过期：{missing}"
