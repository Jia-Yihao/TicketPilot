# F-8 移交包携带客户档案摘要 — 任务清单

口径：`.qoder/rules/handoff-profile-summary.md`（唯一契约来源）。
规格增量：`openspec/changes/add-handoff-profile-summary-2026-10/specs/handoff-bundle/spec.md`。
步骤与文件：`plan.md`。每个实现任务先写会失败的测试并亲眼看过失败（红 → 绿）；
只有独立审查通过后勾选本文件。本轮不提交、不推送、不改 `docs/**`。

## 里程碑 A — 立项与测试骨架

- [x] A1 写 `proposal.md`（为什么 / 改什么 / 不改什么）与 `specs/handoff-bundle/spec.md`
      （行为契约，逐条 Scenario 可映射到一条测试）（计划任务 1）
- [x] A2 写 `plan.md`（目标 / 架构 / 技术栈 / 规格路径 / 全局约束 / 审查重点 / 逐任务文件与测试）
      与 `tasks.md`（里程碑勾选，编号与 `plan.md` 一一对应）（计划任务 1）
- [x] A3 写 `tests/test_handoff_profile_summary.py`（10 条验收逐条钉住）并验证**先失败**
      （失败点必须是「移交包里没有那三个键」，不是导入错或拼写错）（计划任务 1）

## 里程碑 B — 后端读取与整形

- [x] B1 在 `app/services/memory.py` 新增**模块级** `load_history_summary(session, identity)`：
      按租户与用户逐值相等取档案摘要**一列**，返回值已脱敏，无档案行 / 空 / 纯空白 ⇒ `None`；
      不建行、不碰 `updated_at`、不写审计、不 commit（计划任务 2）
- [x] B2 脱敏与截断的用例转绿：直接写库塞手机号 / 邮箱 / 身份证 / 卡号，包内检索不到原文；
      超长按上限截断且以 `…` 结尾；未超长逐字不变（计划任务 2）
- [x] B3 `app/agent/handoff.py`：`HandoffBundle` 加三个字段（默认 `None` / `""` / `0`），
      `to_dict()` **只在 `history_summary` 非空时**写入这三个键；包保持 frozen（计划任务 3）
- [x] B4 `app/services/handoff.py`：`HandoffService.escalate` 在构造事件**之前**用
      `dataclasses.replace()` 补摘要——先脱敏、后截断（上限 = `handoff_digest_max_chars`），
      来源标注恒为 `"customer_profiles.history_summary"`，上限标注记实际生效的上限；
      `build_bundle` 的签名与形状**一字不改**（计划任务 3）

## 里程碑 C — 边界与不退化

- [x] C1 空时不占位：无档案行、或摘要为空 / 纯空白时，包内**没有**那三个键（计划任务 3）
- [x] C2 不进回复 / 不进模型输入 / 不进追踪：客户回复文本、提示词面、追踪属性里检索不到摘要（计划任务 3）
- [x] C3 不留痕、不建行：转人工后审计里的 `memory.%` 没有新增行，档案 `updated_at` 不变（计划任务 3）
- [x] C4 租户边界：换 `user_id`、换租户都读不到别人的摘要（计划任务 3）
- [x] C5 不退化：既有移交包键逐字不变（只多出三个新键）；`tests/test_handoff.py`、
      `tests/test_personalization.py`、`tests/test_memory.py` 全绿（计划任务 3）

## 里程碑 D — 控制台

- [x] D1 `web/src/types.ts`：`HandoffBundle` 接口加三个**可选**字段（计划任务 4）
- [x] D2 `web/src/pages/AgentWorkbench.tsx`：案件详情区**条件渲染**——三个键存在才显示，
      缺失不渲染、不显示占位符；前端不自带事实副本、不拼进任何输入框（计划任务 4）
- [x] D3 `npm run build` 通过（类型与构建无错）（计划任务 4）

## 里程碑 E — 门禁与读数

- [x] E1 定向 pytest（新用例 + `test_handoff` / `test_personalization` / `test_memory`）全绿（计划任务 5）
- [x] E2 `ruff check`（用 `.venv` 里那个 0.17.2）与 `mypy app` 全绿（计划任务 5）
- [x] E3 架构守卫与文档代码锚点守卫全绿（计划任务 5）
- [x] E4 文档一致性守卫**单独跑**：失败项只允许是 `test_collected` 事实漂移；报告本轮真实
      收集用例数（数字同步留待收口轮）（计划任务 5）
- [x] E5 汇报只报**产出**（改了哪些文件、关键代码与行号、每条命令的真实输出、收集用例数、
      守卫失败项判定），**不下质量结论**（计划任务 5）

## 不在本轮范围

改 `docs/**`、`README.md`、`CONTRIBUTING.md`；提交与推送；让档案摘要进模型输入或提示词；
档案摘要的跨会话检索 / 时间线 / 版本历史；内容白名单与敏感词拦截；改档案的写入路径与上限；
改队列行摘要的字段集；新增迁移、配置项或依赖。
