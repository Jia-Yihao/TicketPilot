# P2 实施计划

## 目标

让对话入口在规则缺口上只问一次模型：入口把模型结论写成可复核的决策记录，
写路径独立复核后复用，不再用同一份请求重算；成功路径保持一次模型调用与一行
`llm_usage`，`AGENT_MODE=rules` 保持零调用。

## 架构

```text
用户消息 → 规则 classify
  └─ 规则缺口 → IntentBrain（入口）问一次模型 → ModelDecision（内存）
        → ActionRequest.model_decision
        → TaskRuntime.create_task(..., decision=)
        → _run → IntentBrain.plan(..., decision=)
              ├─ verifies() 为真 → 复用模型读数，ModelTrace(reused=True)
              └─ 任一项不一致 → 按原路径重算（最坏多问一次）
        → task.routed（planning_source 由台账写、model_reused=true）
        → 复用时不写第二行 llm_usage
```

记录只在进程内传递：不写入 `tasks.request`、`tasks.context`，也不落库。
台账只信自己复核过的事实，入口不能凭一条记录越过资格校验、审批或写路径。

## 技术栈

Python 3.12、FastAPI、SQLAlchemy 2 ORM、PydanticAI（仅入口模型）、pytest。

## 规格路径

`openspec/changes/p2-decision-reuse-2026-10/specs/agent-orchestration/spec.md`

## 全局约束

- `ModelDecision` 不可变（frozen），只在内存传递，不写任务请求字段与上下文。
- 台账必须逐项复核请求摘要、规则指纹、模型名、规则意图/订单号/金额，
  任一项不一致就丢弃记录并重算。
- 复用不得伪造台账：`planning_source` 仍由台账写，记录只增加 `model_reused`。
- 复用不得跳过资格校验、审批与写路径，也不得省略用量行的真实性。
- `AGENT_MODE=rules` 忽略记录且零模型调用、零用量行。
- 两轮澄清按轮计费：每轮各一次调用、一行用量，不做跨轮复用。

## 审查重点

1. 记录字段是否足以独立复核，`verifies()` 是否覆盖全部输入。
2. 篡改摘要 / 规则指纹 / 模型名 / 规则读数 / 换请求是否都触发重算。
3. 复用路径是否真的没有第二次 `parse` 与第二行 `llm_usage`。
4. 记录是否确实不落库，`tasks.request` 是否仍是用户原话。
5. `rules` 模式与模型失败路径是否仍是零伪造、零错误写。
6. 单轮真实读数与 `task.routed.model` 是否同源，A/B 对照是否单独标注。

## 任务

### 任务 1：决策记录与规则指纹

- 文件：`app/agent/decision.py`（新建 `ModelDecision`、`request_digest`）、
  `app/agent/routing.py`（`RULESET_FINGERPRINT`）、`tests/test_decision_reuse.py`
- 测试：同一请求与上下文摘要稳定；规则指纹对 `routing.py` 与 `intents.yaml`
  字节稳定；`verifies()` 对摘要、指纹、模型名、规则三项读数逐项敏感。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_decision_reuse.py -q`

### 任务 2：大脑复核与复用

- 文件：`app/agent/pydantic_agent.py`（`IntentBrain.plan(..., decision=)`、
  `ModelTrace.reused`）、`tests/test_decision_reuse.py`
- 测试：合法记录复用且不再调用模型；五类篡改与换请求各触发一次重算；
  `rules` 模式忽略记录且零调用。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_decision_reuse.py -q`

### 任务 3：入口到台账的传递

- 文件：`app/agent/support_agent.py`（`_consult_model` 构造记录）、
  `app/agent/actions.py`（`ActionRequest.model_decision`、`_create_task`）、
  `app/services/runtime.py`（`create_task(..., decision=)`、`_run`）、
  `app/services/llm_usage.py`、`tests/test_decision_reuse.py`
- 测试：端到端单轮 1 次 `parse`、1 行 `source=chat` 用量、
  `task.routed.model.model_requests=1` 且 `model_reused=true`；
  `tasks.request` 与用户原话逐字相等，`tasks.context` 不含 `decision` /
  `model_decision` 键（记录不落库）。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_decision_reuse.py tests/test_llm_gap.py -q`

### 任务 4：失败与多轮边界

- 文件：`tests/test_decision_reuse.py`
- 测试：模型失败不建任务、不产生错误写，失败调用仍如实记一行 `FAILED`；
  两轮澄清共 2 次调用、2 行用量，第二轮请求摘要不同、不能跨轮复用；
  `rules` 模式端到端 0 次调用、0 行用量。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_decision_reuse.py -q`

### 任务 5：验证、证据与文档

- 文件：`docs/adr/ADR-030-决策复用.md`、`docs/spec/项目规格.md`、
  `docs/guide/证据索引.md`、`docs/下一步规划.md`、`README.md`、
  `docs/spec/TicketPilot项目说明书.docx`（由 `scripts/build_manual.py` 重生成）
- 测试/证据：全量 `pytest`、`ruff`、`mypy app`、`alembic check`、
  Golden 40/40、支持集 61/61；单轮真实读数 476 input / 164 output / 6321 ms
  与 `task.routed.model` 的 6321.4 ms 出自同一 `ModelTrace`；A/B 对照另列，
  不与单轮读数混写。
- 验证：

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe -m alembic check
.venv/Scripts/python.exe evals/run.py
.venv/Scripts/python.exe evals/support_run.py
.venv/Scripts/python.exe scripts/build_manual.py
```
