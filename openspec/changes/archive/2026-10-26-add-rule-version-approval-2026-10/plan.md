# P4 实施计划

## 目标

把主动通知规则的写路径从「一个人直接改」升级为「提交 → 另一人批准 → 生效」：
每次生效留下不可变版本，可按版本回滚，审计能回答前后版本与批准人；
已生成的通知不被回溯改写，客户仍看不到未批准的改动。

## 架构

```text
坐席 PUT /rules/{event_type}
  └─ 校验模板 / 启停 / 冷却（复用现有确定性校验）
      └─ ProactiveRuleChange(status=PENDING, base_version=N)
            │            规则行不变，客户不可见
            ▼
审批人 POST /rules/changes/{id}/approve
  ├─ 角色 approver + 提交者不能自批
  ├─ base_version 必须等于规则当前 version，否则 409
  ├─ 规则 version = N+1，写入新值
  ├─ ProactiveRuleVersion(version=N+1, 提交人/批准人/理由/摘要)
  └─ 审计 proactive.rule.approve（前后版本 + 摘要，不含文案）

回滚 POST /rules/{event_type}/rollback {version: M}
  └─ 复制版本 M 的值 → 新的 PENDING 改动（kind=ROLLBACK）
        └─ 同一批准路径 → version = N+1（历史不删不改）
```

通知在生成时记录 `rule_version`；规则只在批准事务内变化，
所以待批改动永远不影响下一条通知以外的任何东西。

## 技术栈

Python 3.12、FastAPI、SQLAlchemy 2 ORM、Alembic、pytest、React + TypeScript。

## 规格路径

`openspec/changes/add-rule-version-approval-2026-10/specs/proactive-rules/spec.md`

## 全局约束

- 提交只创建待批改动，绝不在提交事务里修改规则行；未批准改动对客户不可见。
- 批准必须由具备 `approver` 角色的另一名用户完成；提交者自批返回 403。
- 批准时校验 `base_version`；不一致返回 409，规则与版本均不变。
- 版本只增不改：批准后才写版本快照，历史版本不更新、不删除。
- 回滚复制目标版本的值并重新走审批，不允许直接改库。
- 一个租户 + 事件类型同时只允许一条 `PENDING` 改动。
- 审计只存摘要与版本号，不存模板或理由原文。
- 通知记录生成时的 `rule_version`；批准 / 回滚不回溯已生成通知。
- 所有读写按 `tenant_id` 隔离，跨租户一律 404。
- 模板校验、状态迁移与审计在同一事务；失败后数据库逐字不变。

## 审查重点

1. 提交路径是否真的没有改动规则行，客户侧是否完全看不到待批内容。
2. 自批、缺 `approver` 角色、版本冲突、重复待批是否都被拒绝且无副作用。
3. 版本快照是否不可变，旧版本是否能在多次改动后逐字还原。
4. 回滚是否走审批而不是直接写；回滚后的版本号是否继续递增。
5. 通知的 `rule_version` 是否与生成时实际使用的模板一致。
6. 审计是否能回答「前后版本 + 批准人」，且不含文案原文。
7. 跨租户读取 / 批准 / 回滚是否全部 404。
8. 控制台是否能完成提交、审批、回滚，且不会把待批值当成现行值展示。

## 任务

### 任务 1：模型、迁移与种子基线

- 文件：`app/models/memory.py`（`ProactiveRule.version`、
  `Notification.rule_version`、`ProactiveRuleVersion`、`ProactiveRuleChange`）、
  `app/seed.py`（为种子规则写版本 1 基线）、
  `alembic/versions/<new>_proactive_rule_governance.py`
- 测试：`tests/test_proactive_rules.py` 断言种子规则 `version=1` 且版本列表可读；
  迁移在 SQLite 与 `alembic check` 下无漂移。
- 验证：`python -m alembic upgrade head`、`python -m alembic check`。

### 任务 2：提交、批准、拒绝与冲突

- 文件：`app/services/proactive.py`、`app/models/memory.py`
- 测试：提交返回 `PENDING` 且规则不变；自批 403；无 approver 角色 403；
  另一人批准后生效且 `version+1`；重复待批 409；陈旧 `base_version` 409；
  拒绝后规则不变且状态 `REJECTED`。
- 验证：`python -m pytest tests/test_proactive_rules.py -q`。

### 任务 3：版本、回滚与审计追溯

- 文件：`app/services/proactive.py`、`app/api/v1/memory.py`
- 测试：版本列表包含提交人 / 批准人 / 理由；回滚到旧版走审批且版本继续递增；
  通知记录 `rule_version` 且能关联版本快照；审计含前后版本且不含文案。
- 验证：`python -m pytest tests/test_proactive_rules.py tests/test_audit.py -q`。

### 任务 4：HTTP 契约与租户隔离

- 文件：`app/api/v1/memory.py`、`app/schemas/memory.py`
- 测试：真实 HTTP 走「提交 → 批准 → 生效 → 回滚」；
  跨租户读取改动 / 版本 / 批准 / 回滚全部 404；未批准的改动不出现在客户通知里。
- 验证：`python -m pytest tests/test_proactive_rules.py -q`。

### 任务 5：控制台

- 文件：`web/src/api.ts`、`web/src/types.ts`、`web/src/pages/ProactiveRules.tsx`
- 测试：`npm run build` 通过；待批卡片显示提交人与状态；
  审批人可批准 / 拒绝，提交者看不到批准按钮；现行值只来自规则行。
- 验证：`cd web && npm run build`。

### 任务 6：验证、证据与文档

- 文件：`docs/adr/ADR-031-主动规则治理.md`、
  `docs/spec/项目规格.md`、`docs/guide/接口说明.md`、`docs/guide/架构说明.md`、
  `docs/guide/证据索引.md`、`docs/下一步规划.md`、`README.md`、`docs/guide/范围与边界.md`、
  `docs/records/智能体平台设想.md`、`CONTRIBUTING.md`、
  `docs/spec/TicketPilot项目说明书.docx`（由 `scripts/build_manual.py` 重生成）
- 证据：全量 `pytest`、`ruff`、`mypy app`、`alembic check`、
  Golden 40/40、支持集 61/61、维度 3/3；文档守卫与说明书同步。
- 验证：

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe -m alembic check
.venv/Scripts/python.exe evals/run.py
.venv/Scripts/python.exe evals/support_run.py
.venv/Scripts/python.exe scripts/build_manual.py
cd web && npm run build
```
