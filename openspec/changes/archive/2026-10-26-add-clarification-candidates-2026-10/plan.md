# P11 澄清候选降级 — 实施计划

目标：让「缺订单号」的第二次追问变成可自助的候选列表，第三次仍缺才转人工，并把
已展示的候选交给坐席。全程模板 + 确定性查询，零模型调用。

架构：仓储层新增按 `tenant_id + user_id` 的最近订单读路径 →
`SupportContextService` 产出 L1 候选卡片 → `SupportAgent._clarify` 在第二次缺
`order_id` 时挂候选 → `repeated_clarification` 改用新的 `clarification_rounds`
阈值 → 移交上下文包记录 `attempted_candidates`。前端把候选订单号做成点选即发送。

技术栈：FastAPI + SQLAlchemy 2 + Pydantic（后端）；React + TypeScript（`web/`）；
pytest（测试）；OpenSpec 规格增量在
`openspec/changes/add-clarification-candidates-2026-10/specs/support-conversation/spec.md`。

全局约束：

- 候选只能来自调用者自己的订单行；不猜订单号、不做模糊匹配、不跨租户/跨用户。
- 澄清路径不调用模型：`AGENT_MODE=rules` 与 `pydantic_ai` 的候选输出逐字一致。
- 新配置必须有默认值并进 `.env.example`；阈值只从政策引擎读，代码不硬编码数字。
- 候选订单号不进 trace 属性、不进日志；只允许记数量。
- 先红后绿：每个任务先写会失败的测试并亲眼看过失败原因。
- 不动写路径、审批、意图路由与槽位抽取。

审查重点：

1. 阈值语义变化是否被讲清楚（`frustration_rounds` 仍为 2，澄清预算独立为 3）。
2. 候选作用域是否只按 `tenant_id + user_id`，坐席角色也不放宽。
3. 第二次追问是否真的先于转人工出现（否则候选永远看不见）。
4. `attempted_candidates` 是否与卡片字段一致，空候选时是否仍是列表。
5. 隐私边界：trace 只记计数；`support_set.json` 的口径变化是否逐条有据。

## 任务 1 — 澄清阈值进政策引擎

- `app/core/config.py`：新增 `escalation_clarification_rounds: int = 3`。
- `app/core/policy.py`：`EscalationThresholds` 增加 `clarification_rounds`，
  `threshold()` 暴露该名字，`from_settings()` 接线。
- `app/agent/escalation.yaml`：`repeated_clarification.threshold` 由
  `frustration_rounds` 改为 `clarification_rounds`，并把注释里的阈值口径同步。
- `.env.example`：新增 `ESCALATION_CLARIFICATION_ROUNDS=3` 及一行说明。
- 测试：`tests/test_policy_engine.py` 新增「引擎读到独立的澄清阈值」；
  `tests/test_intent_catalog.py`（YAML 装载测试所在处）断言
  `repeated_clarification` 引用的阈值名能被引擎解析，不再借用 `frustration_rounds`。

验证：`.venv/Scripts/python.exe -m pytest tests/test_policy_engine.py tests/test_handoff.py -o addopts= -q`

## 任务 2 — 本人最近订单查询

- `app/repositories/commerce.py`：`list_recent_orders(tenant_id, user_id, limit=3)`，
  按 `paid_at` 倒序、`id` 倒序兜底，只读。
- 测试：`tests/test_support_candidates.py::test_recent_orders_are_scoped_to_tenant_and_user`、
  `::test_recent_orders_come_back_newest_first_and_stop_at_the_limit`。

## 任务 3 — 候选卡片（L1 事实）

- `app/services/support.py`：`ORDER_STATUS_LABELS` + `order_candidates(identity, limit=3)`，
  产出 `kind="order_candidate"` 卡片：`card_id` 为订单号，字段只有 `order_id` /
  `product_name` / `status` / `delivered_at`，`sections` 为空（L2 不预取）。
- 测试：`tests/test_support_candidates.py::test_candidate_cards_carry_only_l1_facts`、
  `::test_candidate_cards_name_the_product_from_the_catalog`、
  `::test_a_caller_without_orders_gets_no_candidates`。

## 任务 4 — 第二次追问带候选

- `app/agent/support_agent.py`：`_clarify` 接收 `clarification_count` 与 session/identity，
  仅在 `slot == "order_id"` 且 `clarification_count >= 1` 时取候选；模板文案列出候选，
  `suggestions` 前置订单号；无候选时文案与今天逐字一致。
- 调用点把 `escalation_state.clarification_count`、`session`、`identity` 传进去。
- 测试：`tests/test_support_conversation.py` 新增
  `::test_the_first_clarification_offers_no_candidates`、
  `::test_the_second_clarification_lists_the_callers_own_orders`、
  `::test_candidates_never_leak_another_users_orders`、
  `::test_a_missing_amount_clarification_still_has_no_candidates`、
  `::test_choosing_a_candidate_resumes_the_interrupted_intent`。

## 任务 5 — 第三次仍缺才转人工，移交带候选

- `tests/test_handoff.py`：把 `test_repeated_clarification_hands_over` 改成三轮，
  断言第二轮是候选、第三轮才是 `repeated_clarification`。
- `app/agent/handoff.py`：新增 `CandidateOrder` 值对象与
  `HandoffBundle.attempted_candidates`（默认空元组，`to_dict` 输出列表）。
- `app/services/handoff.py`：`build_bundle(..., attempted_candidates=...)`。
- `app/agent/support_agent.py`：`_escalate` 从 `draft.cards` 里取
  `kind == "order_candidate"` 的卡片填进 bundle。
- 测试：`::test_the_handoff_bundle_carries_the_candidates_shown`、
  `::test_a_clarification_handoff_without_candidates_still_lists_the_field`。

## 任务 6 — 候选不进 trace

- `app/agent/support_agent.py`：`generate/support.reply` 属性新增
  `clarify_candidates`（计数），不写订单号。
- 测试：`tests/test_support_candidates.py::test_the_trace_counts_candidates_without_ids`、
  `::test_candidate_order_ids_never_appear_in_the_trace_payload`。

## 任务 7 — 回测集口径同步

- `evals/support_set.json`：`refund-3turn` 按新阈值改写第二、三轮期望（第二轮追问金额、
  第三轮补金额后建任务），并新增一条 `clarify-candidates` 三轮用例
  （追问 → 候选 → 点选后自助闭环）。
- 测试：`tests/test_eval_datasets.py` 钉住新用例的形状与标签。

验证：`.venv/Scripts/python.exe evals/support_run.py`（61 → 62 条，逐条通过）

## 任务 8 — 前端点选即发送

- `web/src/types.ts`：`ContextCardKind` 增加 `order_candidate`。
- `web/src/components/ContextCard.tsx`：`KIND_META` 增加「订单候选 / CANDIDATE」，
  `FIELD_LABELS` 增加 `product_name`。
- `web/src/styles.css`：`context-card--order_candidate` 与徽标配色。
- `web/src/pages/ConversationPage.tsx`：建议芯片若与本条回复的候选订单号一致则直接发送，
  其余仍是填入输入框。

验证：`cd web && npm run typecheck && npm run build`

## 任务 9 — 前后对比证据

- 新增 `evals/clarification_candidates.py`：同一段「三次都不给订单号」与一段
  「第三次点选候选」的对话，分别用 `ESCALATION_CLARIFICATION_ROUNDS=2`（旧口径）与
  默认 3（新口径）跑，输出转人工率与自结率对比，并把读数写进 `docs/guide/证据索引.md`。

验证：`.venv/Scripts/python.exe evals/clarification_candidates.py`

## 任务 10 — 文档与说明书

ADR-033（阈值语义变化 + 候选隐私边界）；`docs/spec/项目规格.md`、`docs/guide/架构说明.md`、
`docs/guide/评测说明.md`、`docs/guide/范围与边界.md`、`docs/下一步规划.md`、`docs/records/智能体平台设想.md`、
`docs/guide/证据索引.md`、`docs/fact-baseline.yaml`（如出现新计数）、`README.md`、
`CONTRIBUTING.md`、`.env.example` 与 `docs/spec/TicketPilot项目说明书.docx` 同步。

## 收尾验证

```bash
.venv/Scripts/python.exe -m pytest -o addopts= -q
.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe evals/run.py
.venv/Scripts/python.exe evals/support_run.py
.venv/Scripts/python.exe -m alembic check
cd web && npm run typecheck && npm run build
```

reviewer 通过后勾选 `tasks.md`，归档变更并把增量并入
`openspec/specs/support-conversation/spec.md`。
