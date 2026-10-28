# P3-b 实施计划

## 目标

让统计问题按确定性路由在「受控 SQL」与「预取 + 预聚合 + 模型只表达」之间选择；
预取路径的事实由服务端计算，模型只负责措辞，数字经独立 guard 校验，明细按 `query_id` 按需展开。

## 架构

```text
问句 → planner（既有）→ scoped COUNT → AnalyticsRouter
  ├─ REFUSED  → 既有 SqlRejected → 转人工
  ├─ SQL      → 既有 builder/guard/executor → 模板句子（零模型调用）
  └─ PREFETCH → PrefetchService（按 Identity）→ AnalyticsSnapshot
                 → L1 + L2 上下文（L3 留在 SnapshotStore）
                 → AnalyticsNarrator（仅 pydantic_ai）→ FactualityGuard
                 → 成功：模型句子；失败/不可用：模板句子
追问「哪几笔」→ 会话里的 query_id → SnapshotStore.get → analytics_detail 卡片
```

## 技术栈

Python 3.12、SQLAlchemy 2 ORM、PydanticAI（仅表达路径）、pytest、FastAPI。

## 规格路径

`openspec/changes/p3b-prefetch-analytics-2026-10/specs/analytics-prefetch/spec.md`

## 全局约束

- 聚合只在 Python 或既有受控 SQL 里；模型不生成 SQL、不计算。
- 租户与用户只从 `Identity` 取。
- L3 永不进 prompt；trace 不写完整明细与整段回复。
- 任何表达失败都回退模板，事实与卡片不变。
- `analytics` 卡片既有字段名不变。
- 关闭预取开关时逐位回到 P3-a 的受控 SQL 行为。

## 审查重点

1. 路由理由码与 trace 是否一致、可复现。
2. 超阈值与明细类分流的边界。
3. 预取 SQL 是否始终带 `tenant_id` + `user_id`。
4. Python 聚合与受控 SQL 的桶、顺序、金额是否逐位一致。
5. guard 是否能拦住改数、编造、夸大，并正确回退。
6. 快照的身份绑定与过期。
7. `rules` 模式是否真的零调用。
8. 现有支持集 / Golden / 回放 / 卡片字段是否不退化。

## 任务 1：配置与路由

- 文件：`app/core/config.py`、`.env.example`、`app/sql/router.py`、`tests/test_analytics_router.py`
- 测试：四个分支 + 理由码 + 同输入同结果 + 开关关闭。
- 验证：`pytest tests/test_analytics_router.py -q`、`mypy app`。

## 任务 2：预取与预聚合

- 文件：`app/sql/prefetch.py`、`app/sql/snapshot.py`、`tests/test_analytics_prefetch.py`
- 测试：身份隔离、count/sum/group_by、时间范围、两条路径逐位一致、超阈值截断。
- 验证：`pytest tests/test_analytics_prefetch.py tests/test_sql_analytics.py -q`。

## 任务 3：服务接入与账本

- 文件：`app/sql/service.py`、`app/sql/errors.py`、`app/api/v1/analytics.py`、`app/schemas/analytics.py`
- 测试：路由写入 trace、预取账本、API 返回 route、关闭开关回到 SQL。
- 验证：`pytest tests/test_sql_analytics.py tests/test_analytics_router.py -q`。

## 任务 4：分层上下文与表达

- 文件：`app/sql/narrative.py`、`app/agent/pydantic_agent.py`（或独立 narrator）、`app/main.py`
- 测试：预算、L3 不进 prompt、固定约束句、rules 零调用、模型失败回退。
- 验证：`pytest tests/test_analytics_narrative.py -q`。

## 任务 5：数字忠实性

- 文件：`app/sql/narrative.py`、`tests/test_analytics_faithfulness.py`
- 测试：正确数字通过；改数 / 编造 / 夸大被拦截并回退；trace 记录未命中数字。
- 验证：`pytest tests/test_analytics_faithfulness.py -q`。

## 任务 6：渐进式披露

- 文件：`app/sql/snapshot.py`、`app/services/conversations.py`、`app/agent/actions.py`、`app/agent/support_agent.py`、`tests/test_analytics_followup.py`
- 测试：追问同一 query_id、零模型调用、过期/跨身份拒绝、卡片形状。
- 验证：`pytest tests/test_analytics_followup.py -q`。

## 任务 7：全量验证与文档

- 文件：`docs/下一步规划.md`、`docs/spec/项目规格.md`、`docs/guide/架构说明.md`、`docs/guide/评测说明.md`、`README.md`、`CONTRIBUTING.md`、`.env.example`
- 命令：`pytest`、`ruff check`、`mypy app`、`python evals/run.py`、`python evals/support_run.py`、`python scripts/build_manual.py`。
- 证据：改前 / 改后 token 与延迟按路由分开记录；支持集 61/61、Golden 40/40、回放空 diff。
