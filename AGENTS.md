# TicketPilot 项目级协作声明

> 本文件只补项目口径与产物路径；流程仍遵循用户级协作规则（organizer → executor → reviewer）。
> 变更流程、目录与字段口径见 [openspec/AGENTS.md](openspec/AGENTS.md)。

## 事实源优先级

1. 用户当次明确指令
2. `docs/spec/项目规格.md`（业务事实）
3. `.qoder/rules/*.md`（当前工作轮次的项目口径）
4. `openspec/specs/`（已实现能力基线）
5. `openspec/AGENTS.md`（变更流程与文件口径）
6. `openspec/project.md`（项目上下文与不变量）

冲突时先停下、说明冲突、改口径文件，再改代码。

> 任务排序、状态判定与提交边界的口径见 `.qoder/rules/task-order.md`；任务清单与状态的
> 真源仍是 `docs/下一步规划.md` §0.2。不允许"代码先跑通、文档后补"。

## 每次实现类任务的固定动作

| 阶段 | 触发技能 | 产物 |
|---|---|---|
| 立项 / 澄清 | `brainstorming` | `openspec/changes/<change-id>/proposal.md`（tiny 除外） |
| 写规格增量 | — | `changes/<id>/specs/<capability>/spec.md` |
| 写实施计划 | `writing-plans` | `changes/<id>/plan.md` + `tasks.md` |
| 实现 | `test-driven-development` | 代码 + 先失败的测试 + 提交 |
| 收尾 | `verification-before-completion` | 命令与输出证据 |
| 验收 | — | reviewer **读代码与 diff** 判定，不运行测试；给出通过结论或 8 字段退回通知 |
| 归档 | — | 目录移入 `changes/archive/YYYY-MM-DD-<id>/`，增量并入 `specs/` |

`brainstorming` 对以下情况可跳过：用户明确说直接做、纯查询、只读调查、执行已批准的计划。

默认执行方式：本仓库的实现类工作先由 `organizer` 出分派单，再逐任务交 `executor` 实现、`reviewer` 验收；主会话只做编排、简报和对用户汇报。单文件 tiny 改动、纯查询、只读调查可直接在主会话完成。reviewer 不跑测试，但执行者必须提交本轮真实的测试输出作为证据。

## 提交前必须跑

```bash
python -m pytest -q                                   # 等价 make test
ruff check app tests scripts evals alembic            # 等价 make lint
python -m mypy app
```

涉及迁移时加 `python -m alembic check`；涉及检索/评测时加 `python evals/run.py`。

报告"通过"必须附本轮实际输出，不许引用历史运行结果。

## 硬约束

- 写操作必须经过 ToolSpec 与任务运行时，不允许直连业务写接口。
- 有副作用的工具必须有幂等键；审批动作必须留痕。
- 任何查询都带租户边界。
- 过期政策不得命中。
- 意图与转人工条件是 YAML 数据，不是提示词。
- 跨模块契约（错误信封、身份、时间、金额）改动前先写项目级口径。

## 禁止

- 跳过规格直接开写（tiny 除外）。
- 在 `tests/` 之外用临时脚本代替正式测试。
- 修改 `change-id` 或归档目录名。
- 未经验收就勾选 `tasks.md`。
- 用"测试通过"替代"规格逐条有对应测试"。
