# TicketPilot 项目上下文

> 本文件是 OpenSpec 的项目上下文，只写"这个项目是什么、边界在哪、怎么验证"。
> 业务事实以 `docs/spec/项目规格.md` 为准；本文件与它冲突时，先改 `docs/spec/项目规格.md` 并说明理由。

## 定位

TicketPilot 是售后客服 Agent：**确定性工作流骨架 + 有界 LLM 节点**。

- 读路径：意图路由 → 检索/受控 Text-to-SQL → 生成回复，只读、可重放。
- 写路径：全部经过任务运行时（TaskRuntime），带幂等键、账本、四眼审批与对账。
- LLM 不做越权决策；路由、风险等级、写动作标记、转人工条件写在 YAML 与代码里，不在提示词里。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic |
| Agent | 规则路由 + PydanticAI 受限槽位抽取 |
| 数据库 | SQLite（本地/离线评测）、PostgreSQL（生产与 CI） |
| 控制台 | React 19、TypeScript、Vite |
| 质量 | pytest、ruff、mypy（strict）、架构守卫、Golden Set、检索评测 |

## 目录职责

```text
app/api/       HTTP 层：校验、依赖注入、错误信封
app/core/      配置、身份、错误、时间、金额、日志、策略引擎
app/agent/     意图路由、本地 SupportAgent、受限槽位抽取
app/services/  任务运行时、退款、审批、客服上下文、商品目录、订单、转人工、消息队列
app/sql/       受控 Text-to-SQL：planner / builder / guard / executor
app/tools/     ToolSpec / ToolContext / ToolResult 与工具实现
app/models/    ORM 模型与枚举
web/           控制台
tests/         单元、集成、e2e、架构守卫
evals/         Golden Set、检索指标、回放与业务指标
docs/          四卷：guide/（使用与实现）、spec/（契约）、records/（历史记录）、adr/（决策），
               另有 下一步规划.md（唯一执行计划）与三份守卫清单（fact-baseline / code-anchors / architecture-guard）
```

## 验证命令

```bash
python -m pytest -q          # 等价 make test
ruff check app tests scripts evals alembic   # 等价 make lint
python -m mypy app           # strict
python -m pytest tests/test_architecture_guard.py tests/test_docs_consistency.py
python -m alembic check
python evals/run.py
```

提交前至少跑：pytest、ruff、mypy。

## 不变量（不可在变更中悄悄改掉）

1. 写操作必须经过工具清单（ToolSpec）与任务运行时，不允许直连业务写接口。
2. 有副作用的工具必须有幂等键；审批类动作必须留痕。
3. 跨租户数据不可见；任何查询都要带租户边界。
4. 过期政策不得命中；政策状态变更要可追溯。
5. 意图目录（`intents.yaml`）与转人工条件（`escalation.yaml`）是数据，不是提示词。
6. 错误信封、身份模型、时间与金额工具是跨模块契约，改动要先写进项目级口径。

## 变更记录

所有实现类变更走 `openspec/changes/<change-id>/`，`change-id` 用 kebab-case，动词开头，例如 `add-query-rewrite-metrics`。

例外：已存在的 `p2-decision-reuse-2026-10`、`p3-sql-unsupported-dimension-2026-10`、
`p3b-prefetch-analytics-2026-10`、`p6-sse-follow-2026-10` 等以阶段编号开头的目录是
历史命名；按 `AGENTS.md` §7「不改 `change-id` 或目录名」保留，不因本句改名。
新建变更仍须动词开头。
