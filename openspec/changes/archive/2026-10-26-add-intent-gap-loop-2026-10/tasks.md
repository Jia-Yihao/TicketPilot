# P5 意图缺口闭环 — 任务

## 1. 测试先行

- [x] 1.1 新增 `tests/test_intent_gaps.py`：规范化/去重、跨租户合并、终态不重开、模型提议不改状态、人工确认门槛、闭包行、分层报告、稳定渲染、构建器合并与跨层重复、CLI 往返。先运行确认失败。
- [x] 1.2 更新 `tests/test_intent_eval.py` 的来源断言，接受 `gap_id` 闭包来源。

## 2. 队列与闭环

- [x] 2.1 新增 `evals/intent_gaps.py`：规范化、哈希、加载/渲染、ingest、propose、close、ignore、closure_rows、build_report；失败不落盘。
- [x] 2.2 新增 `scripts/manage_intent_gaps.py`：`ingest` / `propose` / `close` / `ignore` / `report`，`--confirm-human` 硬门禁，稳定 JSON 输出。

## 3. 语料回灌

- [x] 3.1 `scripts/build_intent_set.py` 合并 `evals/intent/gap_closures.jsonl` 到 `authored_gap`，记录 sha256，跨层重复文本报错。
- [x] 3.2 提交初始 `gap_candidates.jsonl`、`gap_queue.json`、`gap_closures.jsonl`：第 106 行关闭为 `ORDER_QUERY`，第 108/132 行 OPEN（108 带一条模型提议，仍 OPEN）。
- [x] 3.3 重生成 `intent_set.json` 并使 `--check` 通过；语料 345 → 346。

## 4. 周期报告与读数

- [x] 4.1 跑改写前后指标 JSON，生成 `evals/intent/gap_report.json`：队列计数、新增/关闭/忽略、两层 coverage 与 `false_unknown` 变化、门槛是否退化。
- [x] 4.2 全量 `pytest`、`ruff`、`mypy`、`build_intent_set --check`、`intent_metrics --enforce` 通过，保存实际输出作为证据。

## 5. 文档与审查

- [x] 5.1 新增 ADR-032；同步 `docs/guide/评测说明.md`、`docs/spec/项目规格.md`、`docs/guide/范围与边界.md`、`docs/guide/架构说明.md`、`docs/records/智能体平台设想.md`、`docs/下一步规划.md`、README、CONTRIBUTING、`docs/guide/证据索引.md`、`docs/fact-baseline.yaml`，重生成说明书。
- [x] 5.2 提交 reviewer 独立审查；按 8 字段退回通知返工，复验通过后才勾选本文件并归档。
- [x] 5.3 归档到 `openspec/changes/archive/2026-10-26-add-intent-gap-loop-2026-10/`，增量并入 `openspec/specs/intent-evaluation/spec.md`。
