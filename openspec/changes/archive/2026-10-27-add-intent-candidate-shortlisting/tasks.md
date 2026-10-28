# P8 任务清单

口径：`.qoder/rules/intent-candidates.md`。步骤与文件：`plan.md`。
每个实现任务先写会失败的测试并亲眼看过失败；只有独立审查通过后勾选本文件。

## 里程碑 A — 候选内核

- [x] A1 写 `tests/test_intent_candidates.py`（10 组断言）并验证失败于「模块不存在」
- [x] A2 实现 `app/agent/candidates.py` 让 A1 全绿（含 `describe_candidates()` L2，不被 prompt 调用）
- [x] A3 `app/core/config.py` + `.env.example` 新增 `INTENT_CANDIDATES_ENABLED` / `INTENT_CANDIDATES_MAX`，补默认值与 `.env.example` 存在性断言

## 里程碑 B — 模型面收窄

- [x] B1 写 `tests/test_intent_candidate_model.py`（假模型，7 组断言）并验证失败
- [x] B2 改 `app/agent/pydantic_agent.py`：`IntentOutput.intent: str`、`parse(..., candidates=…)`、
      `ModelTrace.reason`（序列化键 `model_reason`）、INSTRUCTIONS 收窄、越界不重试
- [x] B3 留痕：`support_agent._consult_model` gap 记候选数量与越界/`NONE` 布尔（不记证据串）；
      `runtime._run` 的 `route_attributes` 与 `task.routed` 带 `model_reason`
- [x] B4 写 `tests/test_intent_candidate_trace.py`（2–3 条）并验证通过

## 里程碑 C — 评测与不退化

- [x] C1 `evals/intent_candidates.py`：346 条 P1 语料，分层报覆盖率 / 平均候选数 / 桶分布 / 无条件候选占比
- [x] C2 `tests/test_intent_candidate_eval.py`：钉住报告形状、分层不合并、不设通过/失败判定、空语料拒绝出报告
- [x] C3 跑不退化门禁：11 句探针、62 条支持集、40 条任务集、回放自检、检索门禁
- [x] C4 越界率真实模型 A/B（`--with-model`）；跑不动就在文档里写「未测」，不得用假模型充数

## 里程碑 D — 文档与验收

- [x] D1 ADR-034（含被否决方案：输出全 12 行 + confidence 卡阈值；越界时 `ModelRetry` 再问一次）
- [x] D2 文档同步：`PROJECT_SPEC`、`NEXT_STEPS`、`AGENT_PLATFORM_PLAN`、`ARCHITECTURE`、`API`、
      `EVIDENCE`、`EVALS`、`SCOPE`、`README`、`CONTRIBUTING`、`.env.example`
- [x] D3 `docs/fact-baseline.yaml` 计数同步 + `scripts/build_manual.py` 重生成说明书
- [x] D4 全量门禁（`pytest` / `ruff` / `mypy app` / `alembic check` / `npm run build` / 两个守卫）
- [ ] D5 提交独立审查（reviewer 只读），按 8 字段退回通知返工直到通过；通过后归档并合并进 `openspec/specs/`

## 不在本轮范围

- 扩写意图采纳面；把桶当阈值；L2 定义注入答案路径；embedding 召回；改幂等键或 `ModelDecision.digest`。
