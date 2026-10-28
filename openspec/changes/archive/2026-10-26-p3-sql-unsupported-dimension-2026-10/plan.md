# P3-a 实施计划

## 目标

让统计计划器在分组维度不属于受支持集合（`status` / `month` / `day`）时明确拒绝，
不再把「按商品 / 品类 / 渠道」静默映射成 `status`；拒绝原因与「认不出数据集」
分开记账，对话层走既有转人工路径且不出现状态桶。

## 架构

```text
问句 → HeuristicSqlPlanner.decide(question, now)
  ├─ 数据集/状态词可读 + 维度受支持 → SqlPlan(group_by ∈ {status, month, day})
  └─ 识别到分组线索 + 维度不受支持 → PlanRefusal("UNSUPPORTED_DIMENSION")
        → SqlAnalyticsService 抛出 SqlRejected 并写 sql_queries.rejected_reason
        → analytics.query 回复拒绝 + evidence_score=0
        → 既有 no_evidence 转人工（真实 handoff），回复无状态桶
```

## 技术栈

Python 3.12、FastAPI、SQLAlchemy 2 ORM、pytest；评测沿用 `evals/support_run.py`。

## 规格路径

`openspec/changes/p3-sql-unsupported-dimension-2026-10/specs/analytics-sql/spec.md`

## 全局约束

- 只缩小承诺，不扩 `_GROUP_CUES`、不放开自由 SQL。
- 不支持的维度必须返回「无计划 + 原因」，不得回落到 `status`。
- `NO_PLAN` 与 `UNSUPPORTED_DIMENSION` 必须是两个不同的 `rejected_reason`。
- 对话层沿用既有拒绝回复与 `no_evidence` 状态机，移交必须真的建单。
- 支持维度（status / month / day）与既有支持集逐条不退化。
- 不改 `change-id`、目录名与既有错误信封。

## 审查重点

1. 三条不支持维度是否都得到 `UNSUPPORTED_DIMENSION` 且 `plan is None`。
2. 是否还存在任何「分布 → status」的静默回落路径。
3. 台账是否真的把两种拒绝原因分开，而不是共用 `NO_PLAN`。
4. `REASON_CODES` 的四处文档是否与代码同为 19 项。
5. 对话层是否真的建 handoff，回复是否真的不含状态桶。
6. status / month / day 与既有支持集是否不退化。

## 任务

### 任务 1：计划器识别不支持维度

- 文件：`app/sql/planner.py`（`PlanRefusal`、`decide()`、
  `_UNSUPPORTED_DIMENSIONS`）、`tests/test_sql_dimensions.py`
- 测试：三条不支持维度 `plan is None` 且原因为 `UNSUPPORTED_DIMENSION`；
  状态 / 月 / 日三条仍能规划；`plan()` 与原调用签名兼容。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_sql_dimensions.py -q`

### 任务 2：台账原因区分

- 文件：`app/sql/errors.py`（`REASON_CODES` 增加 `UNSUPPORTED_DIMENSION`）、
  `app/sql/service.py`（优先 `decide()` 并写拒绝原因）、
  `tests/test_sql_dimensions.py`、`tests/test_sql_guard.py`
- 测试：`NO_PLAN` 与 `UNSUPPORTED_DIMENSION` 两行分别落账；
  `len(REASON_CODES) == 19` 且列表包含新码，允许追加、不允许丢失或改名。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_sql_dimensions.py tests/test_sql_guard.py -q`

### 任务 3：对话层拒绝与转人工

- 文件：`app/agent/actions.py`（`analytics.query` 捕获 `SqlRejected`）、
  `app/agent/intents.yaml`（`no_evidence: refuse_without_basis`）、
  `tests/test_sql_dimensions.py`
- 测试：三条问句均返回「转人工」、`handoff` 非空、`task` 为空，
  回复不含「已受理 / 已到账 / 状态待确认」。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_sql_dimensions.py -q`

### 任务 4：支持维度与支持集回归

- 文件：`tests/test_sql_dimensions.py`、`tests/test_sql_analytics.py`
- 测试：status / month / day 的计划与结果逐位不变；既有支持集 61/61 仍通过。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_sql_dimensions.py tests/test_sql_analytics.py -q`
  与 `.venv/Scripts/python.exe evals/support_run.py`

### 任务 5：评测集与 CI

- 文件：`evals/analytics_dimension_set.json`（3 条）、
  `.github/workflows/ci.yml`、`Makefile`（`eval-dimension` 目标）
- 测试：三条评测用例以 `--partial` 运行全过；CI 与本地目标命令逐字一致。
- 验证：
  `.venv/Scripts/python.exe evals/support_run.py --dataset evals/analytics_dimension_set.json --partial`

### 任务 6：验证、文档与说明书

- 文件：`docs/spec/项目规格.md`（§19.1、§22、附录 C）、`docs/guide/接口说明.md`、
  `docs/adr/ADR-010-受控问数.md`、`docs/records/智能体平台设想.md`、
  `docs/下一步规划.md`、`docs/guide/范围与边界.md`、`README.md`、`CONTRIBUTING.md`、
  `docs/spec/TicketPilot项目说明书.docx`（重生成）
- 测试/证据：全量 `pytest`、`ruff`、`mypy app`、`alembic check`、
  Golden 40/40、支持集 61/61、维度集 3/3；19 个 reason code 四处一致。
- 验证：全量门禁 + `scripts/build_manual.py` + 文档守卫。
