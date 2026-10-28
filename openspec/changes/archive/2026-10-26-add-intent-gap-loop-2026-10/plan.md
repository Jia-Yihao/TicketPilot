# P5 意图缺口闭环 — 实施计划

依赖：P1 意图语料（`evals/intent/intent_set.json`，345 条）与指标
（`evals/intent_metrics.py`）。不新增环境变量、不改路由、不让模型写词表。

## 1. 领域模型与去重

新增 `evals/intent_gaps.py`（纯本地、无网络、无时钟）：

- `normalize(text)`：NFKC → casefold → 标点去掉 → 空白折叠为单个空格。
- `dedup_key(text)`：规范化文本的 SHA-256。
- `tenant_hash(tenant_id)`：`sha256("tenant:" + id)`，队列里只存哈希。
- 队列记录字段固定：`id`（`gap-` + key 前 12 位）、`text`、`dedup_key`、
  `tenant_hashes`（排序去重）、`status`（OPEN/CLOSED/IGNORED）、`observations`、
  `first_cycle`、`last_cycle`、`signals`、`predicted`、`label`、`note`、
  `provenance`（只保留 dataset/file/line/selection_id）、`confirmed_by`、
  `confirmation`、`closed_cycle`、`proposals`。
- `ingest(queue, records, cycle=...)`：从 JSONL 记录取 `text` 或最后一条 user
  `messages`；`predicted`/`intent` 不是 `UNKNOWN` 的跳过。同 key 只增
  `observations`、刷新 `last_cycle`、合并租户哈希与信号，**不改状态**。
- 终态（CLOSED/IGNORED）不因再次抽到而回到 OPEN。

## 2. 人工确认与模型提议

- `propose(queue, gap_id, label, note, by, cycle)`：只允许 OPEN；只追加
  `proposals[{"source": "model", ...}]`，`status` 与 `label` 不动。
- `close(...)`：要求 `confirm_human=True`、合法标签、非空 `note` 与非空 `by`；
  置 `CLOSED`、写 `label` / `confirmed_by` / `confirmation="human"` / `closed_cycle`。
- `ignore(...)`：同样要求人工确认与非空说明；置 `IGNORED`，`label` 保持空。
- 所有失败只抛错、不落盘；CLI 捕获后返回 2，队列逐字不变。

## 3. 回灌同一份语料

- `closure_rows(queue)`：从 CLOSED 记录导出 `evals/intent/gap_closures.jsonl`，
  行为 `layer=authored_gap`、`label_source=human`、
  `provenance={"gap_id": ..., "source": "intent-gap-loop", "cycle": ...}`。
- `scripts/build_intent_set.py`：加载 `gap_closures.jsonl` 合并进 `authored_gap`；
  `sources` 增加闭包文件与 sha256；合并后做**跨层**文本去重，重复即失败。
- 不新增数据集、不新增分母；`--check` 仍逐字复算。

## 4. 周期报告

- `scripts/manage_intent_gaps.py` 子命令：`ingest` / `propose` / `close` /
  `ignore` / `report`；写盘用稳定 JSON（indent=2、ensure_ascii=False、末尾换行）。
- `build_report(queue, before=None, after=None, cycle=None)`：
  - 队列计数与 `new_cases`（CLOSED 总数）、`closed_this_cycle`、
    `ignored_this_cycle`、仍开放列表；
  - 按层（external / authored_gap）给 before/after 的 `cases`、`coverage`、
    `false_unknown` 与 delta，**不输出两层相加的单一准确率**；
  - `gate_regression` = after 的 `invariants.ok` 为 false，并列出 failures。
- 输入指标 JSON 直接来自 `python evals/intent_metrics.py --json ...`。

## 5. 初始数据（可复现）

- `evals/intent/gap_candidates.jsonl`：从外部语料第 106/108/132 行取用户原话
  （source_sha256 与 `external_manifest.json` 一致），三条当前分类器均为
  `UNKNOWN`。
- 用 CLI 入库生成 `gap_queue.json`；关闭第 106 行（`ORDER_QUERY` →
  `gap_closures.jsonl`），第 108/132 行保持 OPEN，其中第 108 行带一条模型提议，
  证明提议不关闭缺口。
- 语料从 345 → 346（`authored_gap` 144 → 145）。

## 6. 测试（先失败）

新增 `tests/test_intent_gaps.py`：

1. 规范化忽略大小写、空白与标点；不同文本不同 key。
2. 同周期重复入库只留一条，`observations` 加一，`last_cycle` 刷新。
3. 跨租户同文本合并，`tenant_hashes` 两个值，序列化结果不含原始租户 ID。
4. 终态缺口再次抽到不被重开。
5. 模型提议不改 `status` / `label`，也不增加闭包行。
6. 缺 `confirm_human`、缺说明、缺 `by` 时关闭失败且队列逐字不变。
7. 关闭产出闭包行，带 `gap_id` 来源；忽略不产闭包行。
8. 报告分层给 before/after 与 delta；after 不变量失败时 `gate_regression=true`。
9. 队列与报告渲染逐字稳定（两次结果相同）。
10. 构建器把闭包合并进 `authored_gap`，跨层重复文本被拒绝。
11. CLI 无 `--confirm-human` 返回非零且不写盘；完整 ingest → propose → close →
    report 往返可用。
12. 更新 `tests/test_intent_eval.py` 的来源断言，接受闭包来源。

## 7. 验证命令

```bash
.venv/Scripts/python.exe -m pytest tests/test_intent_gaps.py -o addopts= -q
.venv/Scripts/python.exe -m pytest -o addopts= -q
.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe scripts/build_intent_set.py --check
.venv/Scripts/python.exe evals/intent_metrics.py --enforce
.venv/Scripts/python.exe scripts/manage_intent_gaps.py report \
  --before .tmp/intent_before.json --after .tmp/intent_after.json
```

## 8. 文档与归档

ADR-032；`docs/guide/评测说明.md`、`docs/spec/项目规格.md`、`docs/guide/范围与边界.md`、
`docs/guide/架构说明.md`、`docs/records/智能体平台设想.md`、`docs/下一步规划.md`、
README、CONTRIBUTING、`docs/guide/证据索引.md`、`docs/fact-baseline.yaml` 与说明书重生成。
reviewer 通过后勾选 `tasks.md`、归档并把增量并入
`openspec/specs/intent-evaluation/spec.md`。
