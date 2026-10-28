# 渠道入站可用性原因 — 任务清单

口径与取值表：`specs/channel-inbound/spec.md` + `plan.md` 的「谓词映射表」。
步骤与精确文件：`plan.md`。每个实现任务先写会失败的测试并亲眼看到它失败；
只有独立审查（reviewer 读代码与 diff）通过后，才由 organizer 勾选本文件。

## 1. 入站事实与判定内核

- [x] 1.1 `app/channels/registry.py` 声明 `INBOUND_IMPLEMENTED` / `WEBHOOK_INBOUND`，
      `app/channels/base.py` 抽出 `secret_is_configured`，新建
      `app/channels/availability.py` 的四值判定，`gateway._WEBHOOK_CHANNELS` 改为派生；
      `tests/test_channel_availability.py` 的先红测试转绿（计划任务 1）

## 2. 响应契约

- [x] 2.1 `app/schemas/channels.py` 加 `ChannelAvailability` 与必填字段
      `availability`；`ChannelGateway.matrix()` 为 8 个渠道各写一个值；
      默认开发配置下的逐渠道期望值测试通过（计划任务 2）

## 3. 防漂移守卫

- [x] 3.1 三条守卫（全开关适配器集合等价、webhook 集合派生、
      与 `validate_production_safety()` 双向等价）加 503 行为钉全部通过，
      并附破坏性自检的红输出（计划任务 3）

## 4. 不退化

- [x] 4.1 `tests/test_channels.py` 零改动且全绿；能力字段与 `CAPABILITIES`
      逐条一致的断言通过；`test_outbound.py` 与 `test_retrieval_channels.py` 全绿
      （计划任务 4）

## 5. 控制台

- [x] 5.1 `web/src/pages/ChannelMatrix.tsx` 删净 `INBOUND_LABELS`、
      入站列改显示 `availability` 的四值文案并含未知值兜底；
      `web/src/types.ts` 补类型；`npm run typecheck` 与 `npm run build` 通过
      （计划任务 5）

## 6. 文档同步

- [x] 6.1 `docs/guide/接口说明.md` 的 `GET /api/v1/channels` 与 webhook 两节补该字段与取值表；
      `docs/下一步规划.md` F-6 标收口并登记 `disabled_in_prod` 的替换理由；
      确认 `docs/spec/项目规格.md` 与说明书未动、`docs/` 内未新增 `path.py:NN` 行号锚点，
      `tests/test_docs_consistency.py` 与 `tests/test_doc_code_anchors.py` 全绿（计划任务 6）

## 7. 门禁与审查

- [x] 7.1 全量 `pytest` / `ruff check app tests scripts evals alembic` / `mypy app` /
      三个守卫（`test_architecture_guard.py`、`test_docs_consistency.py`、
      `test_doc_code_anchors.py`）/ `npm run build` 全部跑过并附本轮真实输出
      （计划任务 7）
- [x] 7.2 `git diff --stat` 与 `git status --short` 自查未越界
      （`tests/test_channels.py`、`docs/spec/项目规格.md`、`app/api/v1/channels.py`、
      `alembic/`、`docs/architecture-guard.yaml` 改动为零）（计划任务 7）
- [x] 7.3 提交 reviewer 读代码与 diff 验收；按 8 字段退回通知返工直到通过；
      通过后归档并把增量并入 `openspec/specs/channel-inbound/spec.md`
      （计划任务 7）

## 不在本轮范围

- 补齐 `APP` / `WHATSAPP` / `VOICE` / `SMS` 的入站适配器（`docs/下一步规划.md` §8 已列为不做）。
- 修改 webhook 的 404 行为或两处 404 的理由文本；修改 `enabled` 的现有语义。
- 新增第五个取值去覆盖身份映射等其它配置缺陷；新增配置项、依赖或迁移。
- 改出站投递面（`app/channels/outbound.py` 的地址映射与拒绝原因码表）。
- 为 `disabled_in_prod` 写一个今天没有判定来源的分支。
