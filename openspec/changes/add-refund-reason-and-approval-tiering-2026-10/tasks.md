# B-1 退款原因与退款类型 ＋ B-3 审批金额分层 — 任务清单

口径：`.qoder/rules/refund-reason-taxonomy.md`（B-1）+ `.qoder/rules/approval-tiering.md`（B-3）。
行为契约：`specs/refund-request/spec.md` + `specs/approval-tiers/spec.md`。
逐文件步骤与精确断言：`plan.md`。

每个实现任务先写会失败的测试并**亲眼看过失败**（红 → 绿 → 重构）；失败必须失败在「行为未实现」，
不许失败在拼写或导入路径。**只有独立审查（reviewer 读代码与 diff）通过后，才由 organizer 勾选本文件。**

编号规则：`<阶段>.<序号>` 只用于阅读分组；每项末尾的 `（计划任务 N）` 与 `plan.md` 的全局
`### 任务 N` **一一对应**。

## 1. 退款原因与退款类型枚举

- [x] 1.1 `app/models/enums.py` 新增 `RefundReason`（`QUALITY` / `DAMAGED` / `WRONG_ITEM` /
      `NOT_AS_DESCRIBED` / `NO_LONGER_NEEDED` / `OTHER`）与 `RefundType`（`FULL` / `PARTIAL` /
      `SHIPPING` / `PRICE_PROTECTION`）两个 `StrEnum`，与既有 `RefundStatus` 同风格、成员值
      等于成员名、非法值构造即抛错；新建 `tests/test_refund_taxonomy.py` 写出会失败的枚举断言
      并验证失败于「枚举不存在」（计划任务 1）

## 2. 退款台账三列与一条迁移

- [x] 2.1 `app/models/commerce.py` 的 `Refund` 新增 `reason` / `refund_type` / `reason_note`
      三列且**均可空**（枚举列与 `RefundStatus` 同构）；新建一条迁移
      `alembic/versions/<新 revision>_refund_reason_columns.py`，`down_revision = 'a1c7e9d4b820'`，
      **只加可空列、不回填历史数据**，SQLite 与 PostgreSQL 双向可升降；往返迁移与「不回填」
      的断言先红后绿（计划任务 2）

## 3. 原因关键词规则表与 loader

- [x] 3.1 新建 `app/agent/refund_reason_rules.yaml`（关键词 → 退款原因映射）与
      `app/agent/refund_reason_rules.py`（loader）；匹配语义为**小写子串命中**、**表序即优先级**、
      **未命中不产生值**（不得兜底 `OTHER`）；同文件另设**独立**的 `other_cues` 表作为「显式其他」
      识别通路，**优先于** `rules`（B-1 口径 §5.6.1.1；`OTHER` 仍**不得**作 `rules` 的目标值）；
      加载期对未知键、非法目标值、目标值为 `OTHER`、空关键词表、`other_cues` 为空或有重复项、
      整表为空**一律报错不降级**；loader 不读时钟、不用随机、不调模型、不查库；
      `tests/test_refund_reason_rules.py` 先红后绿（计划任务 3）

## 4. 意图目录的退款原因槽位

- [x] 4.1 `app/agent/intents.yaml` 新增 `slots.refund_reason`（提示词含「其他 / 说不清」出口）、
      `REFUND_REQUEST.required_slots` 追加 `refund_reason`、`clarify_prompts` 追加同项、`version`
      同步 bump；`catalog.missing_slots("REFUND_REQUEST", …)` 能算出 `refund_reason` 缺失；
      **不得**为落地放宽 `app/agent/intent_catalog.py` 的 `_TOP_FIELDS`（若发现必须放宽即为越界，
      退回口径重新裁定）；本项会让对话语料停在追问上，语料侧的连带处置见 `plan.md` 任务 11
      「语料口径」（计划任务 4）

## 5. 链路载体与对话侧采集

- [x] 5.1 `app/agent/routing.py` 的 `RoutePlan` 新增 `refund_reason` / `refund_type` 并从 `context`
      同形读取；`app/agent/turns.py` 的 `Turn` 新增同名字段；`app/agent/support_agent.py` 组装
      `context` 时写入 `context["refund_reason"]`（上下文已有显式 `refund_type` 时原样透传）、
      缺槽 `present` 集合**只补 `refund_reason`、不补 `refund_type`**；**命中才写、未命中该键
      不出现**；**模型不补位**——假模型给出退款原因 / 类型时路由计划字段仍只由规则命中或上下文
      决定；**显式「其他」可继续**（客户回复「其他」取 `OTHER`，走 `other_cues` 通路且**优先于**
      `rules`；未命中任何关键词**不**兜底成 `OTHER`）；**上下文已有合法 `refund_reason` 时沿用
      该值、不重新匹配**（计划任务 5）

## 6. 写路径：载荷、工具入参、缺值闸门、任务 API 422

- [x] 6.1 `app/services/approvals.py` 的 `refund_payload` 新增**关键字参数**
      `refund_reason: RefundReason` / `refund_type: RefundType`（载荷键为裸名 `reason` /
      `refund_type`），`create_refund_approval` 透传同名关键字参数；`app/tools/builtin.py` 的
      `check_refund_eligibility` 入参加 `refund_reason` / `refund_type`（**带上下文、无裸名
      `reason`**）；`app/services/runtime.py` 的 `_handle_refund` **只补一道** `_clarify` 闸门
      （`plan.refund_reason is None`），**不补** `plan.refund_type is None` 闸门，改为在订单
      加载、资格校验通过后按 B-1 口径 §5.6.6 **派生** `refund_type`（显式值优先；本次金额 ==
      `orders.refundable_cents` → `FULL`，否则 → `PARTIAL`；两个入口同一条派生）；**不**默认
      `FULL`；`app/schemas/tasks.py` + `app/api/v1/tasks.py` **缺 `refund_reason` 返回 422**
      （按本任务定死的判据：判为退款意图**或**上下文已出现任一退款键时，`refund_reason` 必须
      存在且合法），缺 `refund_type` **不**返回 422 且**不**补成 `FULL`，缺 `refund_reason`
      **不得**补成 `OTHER`；**第二条 422 判据**——`reason_note` 只在 `reason=OTHER` 时合法，
      非 `OTHER` 带 `reason_note` ⇒ **422**，该判定**只写一处**（退款写路径共用入口），**不得**
      静默忽略；既有退款用例逐条不退化（计划任务 6）

## 7. 退款落库与审批详情展示

- [x] 7.1 `app/services/refunds.py` 落库写入 `reason` / `refund_type` / `reason_note`
      （`reason_note` 仅 `reason=OTHER` 时可写；非 `OTHER` 带 `reason_note` 时**不建退款任务**，
      该 `reason_note` 既未被静默忽略也未落库）；`app/services/runtime.py::_approval_snapshot`
      透出载荷；`web/src/pages/ApprovalWorkbench.tsx` 展示原因 / 类型的中文唯一名、历史空值显示
      「未提供」（**不**映射成 `OTHER` / `FULL`）、补充说明只在坐席侧可见；`reason_note` 不进
      载荷 / 摘要 / 公开回执（计划任务 7）

## 8. 审批档位表与 loader

- [x] 8.1 新建 `app/core/approval_tiers.yaml`（默认单层：1 档、`roles: [approver]`、
      `required_approvals: 1`）与 `app/core/approval_tiers.py`（`@lru_cache(maxsize=1)` 加载 +
      解析）；解析为纯函数、取首个 `max_cents` 不小于该金额的档、超出所有档位上界取**最后一档**；
      加载期对未知键、`tier_id` 缺失 / 重复 / 非大写蛇形、`max_cents` 非整数 / 负数 / 非递增、
      `roles` 为空 / 含非 `approver` / 档内重复、`required_approvals != 1`、空表**一律报错**；
      装载时把每档 `roles` **按字典序排序**后再进快照（B-3 口径 §2.3 校验 5：角色列表的顺序
      会逐字进摘要，不规范化会产生假漂移；当前唯一合法集合是单元素 `["approver"]`，此时排序是
      恒等变换、既有读数不变）；`tests/test_approval_tiers.py` 先红后绿（计划任务 8）

## 9. 档位快照进摘要与执行前重校验

- [x] 9.1 `app/services/approvals.py` 的退款载荷新增键 `approval_tier`，值为**解析结果**的快照
      `{tier_id, max_cents, roles, required_approvals}`（九键载荷），`create_refund_approval`
      透传；`app/services/refunds.py` 执行前用**当前**档位表重解析并比对，不一致 → 审批失效、
      原因码 `APPROVAL_TIER_CHANGED`、**不执行**退款、按既有失败形状转人工复核；载荷**无**该键的
      存量审批跳过这项重校验且既有 `verify_binding` 行为不变；角色集合的**内容**改动 → 摘要变化，
      同一组角色只换**书写顺序** → 摘要**不变**（角色列表在装载期按字典序规范化）（计划任务 9）

## 10. 阈值生效与自动执行路径

- [x] 10.1 `app/services/runtime.py` 的 `_handle_refund` 按 `eligibility["requires_approval"]`
      分支：阈值之上建审批行进 `WAITING_APPROVAL`（`result.approval_path == "APPROVAL"`），
      阈值之下**不起审批行**、直接到终态（`result.approval_path == "AUTO"`）、写任务事件
      `refund.auto_executed` 与审计；**按 B-3 口径 §4.2.1 重构** `app/services/refunds.py`：
      拆出「权威执行体」与「审批绑定包装器」，两条路径调**同一个**执行体（权威执行体**只有
      一份**；六项权威校验——加锁、并发冲突、资格复核、幂等占位、渠道调用、验证对账——**强度
      相同**；两份绑定校验都保留）；自动路径同样复核资格、同用台账 `request_digest` 重算比对；
      **禁止**在自动路径复制一份执行逻辑、**禁止**给工具加「跳过审批」开关、**禁止**把资格复核
      或绑定比对降级为「可选」；**审批路径的既有行为逐条不变**；两条路径公开回执措辞不同、
      客户可见文本不含档位 / 角色 / 所需审批数 / 阈值金额；`request_refund` 的
      `requires_approval` 仍为 `True`、网关审批校验未变；默认配置（阈值 0）下既有 Golden 与
      退款用例逐条不变（计划任务 10）

## 11. 全量门禁与不退化读数

- [x] 11.1 跑完全量 `pytest` / `ruff check app tests scripts evals alembic` / `mypy app` /
      `alembic check` / `test_architecture_guard.py` + `test_docs_consistency.py` / `evals/run.py` /
      `evals/support_run.py` / `cd web && npm run build`，读数重定向到 `.tmp/*.txt` 并设
      `PYTHONIOENCODING=utf-8` 后再读；要求全绿，且默认配置下 Golden 与退款用例读数与本轮之前
      **逐条一致**（「逐条一致」指**读数**一致，语料侧按 `plan.md` 任务 11「语料口径」补齐输入：
      Golden 每条退款用例补 `context` 的 `refund_reason` / `refund_type`、**请求文本与断言逐字
      不动**；support 集 9 条退款用例补一句原因线索、**期望逐字不动**（改了就逐条登记）；
      business_metrics 的 3 个退款模板在同一轮加线索、读数与上一轮**并排**报告；既有退款测试
      补上原因 / 类型、**断言不动**；**不新增 Golden 用例**）；阈值生效联动的 `risk` 转人工信号
      变化（`app/services/escalation.py:286-290` 读同一阈值）**必须写进 ADR**，不得当作回归悄悄
      改掉（计划任务 11）

## 12. 文档同步与独立审查

- [x] 12.1 新建 `docs/adr/ADR-0XX-refund-reason-and-approval-tiering.md`（含被否决方案：「档位表
      接管阈值判定」「把自动执行标记也放进摘要」「多级会签」「按客户等级分层」「给 `refund_type`
      建关键词规则表」「追问客户『全额还是部分』」「对话侧退款全部转人工」，并登记 B-1 口径
      §5.6.6 的代价：自助链路上产生不了 `SHIPPING` / `PRICE_PROTECTION`）；同步
      `docs/spec/项目规格.md`（`action_digest` 覆盖字段六项改九项、术语表、两条路径）、
      `docs/guide/接口说明.md`（`TaskCreateRequest.context` 两键、**缺 `refund_reason` 时**的 422、
      `refund_type` 缺值由服务端派生的说明、对话侧追问话术、审批详情新字段）、
      `docs/guide/评测说明.md`（**语料处置登记**：逐条登记 `plan.md` 任务 11「语料口径」那张表的改动，
      business_metrics 的本轮与上一轮读数**并排**给出）、`docs/guide/证据索引.md`（本轮实跑读数）、
      `docs/下一步规划.md`（B-1 / B-3 状态与顺序结论）、
      `docs/guide/架构说明.md`、`README.md`、`CONTRIBUTING.md`、`.env.example`、说明书重生成、
      `docs/fact-baseline.yaml` 计数同步；并逐条核对 `plan.md`「待办：需他处同步」表里登记的
      冲突点（`docs/spec/项目规格.md:118`、`web/src/pages/ApprovalWorkbench.tsx:143-146` 等）
      均已收口；最后提交 reviewer 读代码与 diff 验收，按 8 字段退回通知返工直到通过（计划任务 12）
- [x] 12.2 `README.md` 描述阈值边界的小节写着「未达阈值走自动执行，达到阈值走人工审批」，而
      `app/core/policy.py` 的判定是 `amount_cents > approval_threshold_cents`——**等于**阈值时走的
      是自动执行，「达到」在边界上把方向说反了，正确措辞是「不超过阈值 / 超过阈值」；属文档同步轮
      必修项，且该句属「守卫抓不到的散文过度声明」，可作为已登记守卫反例的活样本
      （沿革注：该措辞已于 2026-10-27 由 T-R0-2 落地修正、rev-r0-batch-a 判定**通过**；本条正文
      描述的是修前状态，原文照留不改写）

## 13. 计划外派生轮次（2026-10-26 起，返工与补裁登记）

主计划（任务 1–12）立项后，实现过程中派生了三轮计划外的返工与一轮补裁。本节登记这些事实与
欠账，让后续读计划的人看得出「计划任务 6 / 7 之外还欠什么」；本轮没有任何一条验收通过，
一条都不许勾。本节条目为计划外派生，`plan.md` 无对应的全局任务编号，故不写「（计划任务 N）」
尾注，改为注明各自的口径依据与状态。

- [ ] 13.1 R1b / R2：退款原因的跨轮沿用与建单即清（实现已落地；R2 的独立审查曾判**不通过**，其
      严重项 `ModelDecision.digest` 迟发重封未申报由 R3 承接、已于 2026-10-27 判通过；沿用生命
      周期本身未被判违规，但放弃路径缺规则一事由审查交回、已由编排方补裁，即 13.3）。规则匹配与
      写回只发生在规划意图为 `REFUND_REQUEST` 的回合，退款任务成功建单的回合清除
      `ConversationSlots.refund_reason`，下一笔退款重新采集、重新追问；落点
      `app/agent/support_agent.py` 的 `ConversationSlots` 构造处；口径依据
      `.qoder/rules/refund-reason-taxonomy.md` §5.6.1.1「沿用适用域（生命周期）」。同一回合内
      客户改口不被新说法覆盖，保留为已知取舍，其对 B-4 原因分布的影响待写进 ADR（由任务 12 承接）。
- [ ] 13.2 R3：`ModelDecision.digest` 迟发重封必须留痕（独立审查已判**通过**，2026-10-27）。入口在模型
      回答之后往 `context` 补服务端派生键（当前唯一是 `refund_reason`）时，用**本回合终态上下文**
      重算 `ModelDecision.digest`，并写一条 `kind=understand`、`name=decision.reseal`、属性
      `derived_keys` 只含键名列表的 trace span。这条读法来自用户 2026-10-26 的裁决
      「允许重封，但必须留痕」，已落在 `docs/adr/ADR-030-决策复用.md` 新增的决策 6；
      `verifies()` 比对的字段集未被放宽（`app/agent/decision.py` 零改动）。待办：`docs/guide/评测说明.md` /
      `README.md` 是否需要因这条裁决同步（未判定，登记为待办而不是结论）。
- [ ] 13.3 R4：沿用域补裁 B'——放弃路径上 `refund_reason` 永不清除（**口径已落，实现轮未开始**）。
      缺陷事实（已逐行核实）：客户在退款流程中途转话题时，`pending_intent` 因本轮没有
      `draft.clarification` 被写成 `None`，而 `refund_reason` 因为 `draft.task` 为 `None` 被
      **保留**，于是「流程标记已消失、原因却还在」；下一句不带原因的新退款请求会在 `classify()`
      **之前**被注入这个陈旧值，槽位不再追问。裁定：沿用域从「同一笔退款流程内」收紧为
      **可判定的不变量**——`ConversationSlots.refund_reason` 仅在本回合规划意图为
      `REFUND_REQUEST`、或上一回合留下的 `pending_intent` 仍是 `REFUND_REQUEST` 时保留，否则随
      同一次 `ConversationSlots` 构造清为 `None`；**单一落点**就是那次构造，不新增事件、配置项
      或转人工分支。被否决的备选（逐条保留否决理由，防止实现时回头）：(A) 只在转人工时清除——
      漏掉不转人工的转话题；(C) 读 abandon events——既撞既有边界（`app/agent/support_agent.py:232-233`
      的 `handle()` docstring 明写 handoff bundle 与 abandon events 是「never a decision branch」；
      该句不在 `_detect_abandon` 自身 docstring（`:1215`）里，但 `_detect_abandon` 正是在 `handle()`
      内被调用（调用点 `:480`），所以撞的正是这条边界），又只覆盖闲置超时、覆盖不了转话题；(D) 新增 abandon action——
      引入新机制，超出本轮；(F) 只在 `_resume_pending` 返回 `resumed` 为真的回合沿用——会把
      这一轮建立的 carry 初衷（避免重复追问）当场废掉，因为客户重述退款、`plan.intent` 已是
      `REFUND_REQUEST` 时 `_resume_pending` 返回假。代价登记：客户在流程中途插问一句别的
      （被读成非退款意图）会被清掉原因、下一句重新追问，这是「宁可重问，也不替客户决定第二笔
      退款的原因」的安全方向代价。**前置闸门**：`app/agent/support_agent.py` 正被 R3 的独立审查
      读取，R4 的实现轮必须等 R3 结论出来（退回则并入其返工，通过则单开一轮），不得两轮同时
      改同一文件。口径先行部分已于本轮写进 `.qoder/rules/refund-reason-taxonomy.md` §5.6.1.1
      「沿用适用域（生命周期）」（可判定不变量 + 代价登记 + 被否决备选 A/C/D/F）；实现轮尚未
      开始，上述前置闸门**已解除**（R3 已于 2026-10-27 判通过），改为：实现轮可派，但须与文档
      数字同步轮错开同文件。
      沿革注（2026-10-27，rev-r0-batch-a 返工轮，沿 13.6 ②「原文照留 + 加注」形态）：本条存活条件
      第二项（上方「或上一回合留下的 `pending_intent` 仍是 `REFUND_REQUEST`」）与本条「口径已落，
      实现轮未开始」已由 13.7（`rev-bprime-r4`，2026-10-27 判**通过**）订正并落码，本条原文照留
      不改写；现行读法是「**本回合写回的** `pending_intent`」，旧写法「上一回合留下的」即口径
      §5.6.1.1 被否决备选清单中的 (G)，照本条原文派单会把泄漏改回去；本条所列被否决备选清单现
      为 A/C/D/F/G（口径处另登记有注）；本条引用的 `_detect_abandon` docstring `:1215` 已核准为
      `:1235`（见 13.7 ① 处置）。
- [ ] 13.4 B-3 审查通过时随附的 6 条一般项（已登记、未处置，逐条列出）：① `approval_requirement`
      随资格 dict 落进任务 `result` JSON（`app/services/runtime.py` 不合格分支的
      `data.eligibility`），需确认任务 API 与控制台不原样呈现；② `tests/test_approval_tier_paths.py`
      的可见文本黑名单扫了 `approver` 的值但未扫字面键名 `roles`；③ `docs/guide/接口说明.md` 的新键、新回执
      措辞与阈值联动话术尚未同步；④ 零可退余额的**不合格**回执尾句由断言句变成中性句
      （属已声明的变化）；⑤ `reconcile_task` 对纯自动执行任务显示「未提供」，修法要先裁定
      「对账改读执行台账 `request_digest` 载荷」的口径归属；⑥ 脏工作树下无法用 git 切出单轮
      增量，后续要强范围证据需在轮次边界产生提交锚点。
      处置去向（2026-10-27 AUTO 对账回落轮，登记用，**不据以勾选本条**）：② 已由上一轮处置——
      字面键名 `roles` 已进 `tests/test_approval_tier_paths.py` 两处客户可见文本黑名单
      （现文件 `:330-339` 与 `:577` 的 `for token in` 块）；⑤ 本轮实现——`reconcile_task` 审批路径
      读审批载荷、AUTO 路径回落读执行台账 `request_payload`（`app/services/refunds.py:585-592`），
      配先失败用例 `test_auto_reconcile_reads_reason_from_ledger_payload`；口径归属补写在
      `.qoder/rules/approval-tiering.md` §4.2 第 6 条（对账面归属声明段）。
- [ ] 13.5 rev-b1-r3（R3 重封留痕）独立审查判通过时随附的 6 条一般项（2026-10-27，已登记、
      未处置，逐条列出）：① 文档记录的收集数（现写 1030）实测已变，
      `test_documented_numbers_match_the_code` 按 `docs/fact-baseline.yaml` 必红，收尾轮必须
      兑现刷新（含说明书重生成与锚点唯一性复跑）；② `tests/test_decision_reuse.py:479` 对
      `route.refund_reason is None` 那道守卫无独立钉死力（规则直读回合即使删守卫仍会绿），若
      后续让「模型被问且 carry 原因在场」可达需补一条用例；③ ADR-030 说痕迹「不进客户可见
      文本」，实际口径是「不进回复/回执」，而 trace API 属客户可读自身会话的观测面，需在
      `docs/guide/接口说明.md` 写明这一区分；④ resumed + 迟发命中的回合复用仍会失败、多付一次调用（与
      初始 digest 的既有不对称同源），建议在 ADR-030 决策 5/6 处补一句；⑤ 本轮无 PostgreSQL
      伴随读数（SQLite-only 证据），计入收尾全量门禁；⑥ 在途轮次无提交锚点，R3 增量无法用
      git 与 R1/R2 切分，沿用「轮次边界产生提交锚点」的既有裁决。
- [ ] 13.6 `rev-bprime`（B′ 口径轮，判**通过**、附 3 条一般项，2026-10-27）一般项的登记与处置
      （**口径文档改动，实现轮未开始**；本条只登记处置，不得据以勾选任何实现项）：
      ① 一般项 1 指出的 docstring **归属措辞**易误读（原文把引文写成挂在 `_detect_abandon` 身上）——
      已按建议把归属改为点名持有者：
      `.qoder/rules/refund-reason-taxonomy.md` §5.6.1.1 被否决备选 (C) 与本文件 13.3 两处说法均写成
      `handle()` docstring，并补代码锚 `app/agent/support_agent.py:232-233`；同一处另写明该句不在
      `_detect_abandon` 自身 docstring（`:1215`）里、而 `_detect_abandon` 在 `handle()` 内被调用
      （调用点 `:480`）。否决结论与理由未变。
      ② 一般项「§5.6.1.1『单一落点就是那次构造』未在口径本体命名代码落点」——已补锚：口径本体现写明
      单一落点是 `app/agent/support_agent.py:602` 的 `ConversationSlots(...)` 回合**写回**构造
      （`refund_reason=` 实参在 `:613`）（2026-10-27 核准为 `:620`/`:634`；历史轮次记录原文照留不改写），并写明 `app/services/conversations.py:115` 的
      `ConversationSlots()` 是新会话默认构造、无沿用值可清，**不构成第二落点**；另如实登记
      `ConversationSlots.from_dict`（`app/services/conversations.py:59`，其 `refund_reason=` 实参在
      `:67`）是从存量 JSON 复原的读取路径、不是本回合的写回落点，以免「唯一带 `refund_reason=` 的槽位
      构造」被误读成覆盖 `from_dict`。三条「不新增事件、不新增配置项、不新增转人工分支」原文未动。
      同处范围词订正：`rev-register-3in1` 判「全仓」为**严重项**（该句被
      `tests/test_refund_taxonomy.py:806` 带 `refund_reason=` 的直接构造反驳），本轮把范围限定为
      `app/`，并在同一句内显式交代该用例属测试形状断言、不构成运行期落点候选；结论与不变量本体未变。
      ③ 归属核对（污染路径发现属哪一轮）：**「放弃路径不清除 `refund_reason`」这一缺陷是由
      `rev-b1-r3` 的 K7 判为「声明缺位」交回、经编排方补裁成 B′（口径 §5.6.1.1），不是 `rev-b1-r2`
      的退回理由；`rev-b1-r2` 的「不通过」只针对 `ModelDecision.digest` 迟发重封未申报那一条严重项
      （沿革见 13.1）。** 此前编排方派单简报把这个缺陷归给 r2 属**简报措辞错误**，仓库文档无此说法：
      口径现文 `:301` 与本文件 13.1 均写作 r3，两份被审文档相互一致，故 ③ 无需改仓库文档，只在本条
      登记核对结论。
      以上 ①② 的处置形态是**两份口径/计划文档的定点替换**，未改任何产品代码、测试、迁移、`docs/**`
      或其它 `.qoder/rules/*.md`；B′ 的可判定不变量本身仍待实现轮落地（见 13.3「实现轮未开始」）。

- [x] 13.7 T-BPRIME-R4：B′ 存活条件的**订正 + 落码**（2026-10-27 由独立审查者 `rev-bprime-r4` 判定**通过**，附 3 条一般项登记在本条末尾）。
      ① 口径订正：`.qoder/rules/refund-reason-taxonomy.md` §5.6.1.1 的不变量第二条件由
      「上一回合留下的 `pending_intent`」订正为「**本回合写回的** `pending_intent`」（即与
      `refund_reason` 同一次构造里由 `draft.clarification["resumable_intent"]` 算出的值，不是
      `carried.pending_intent`）；同条内补写「为何是写回值而非上一回合存量」（带三回合真实
      HTTP 读数：turn1 缺金额追问 → turn2 `LOGISTICS_QUERY` 无追问 → turn3 不带原因词的新退款
      未被追问、直接 `WAITING_APPROVAL` 建单）与「代价仍然只有那一半」（插问非退款回合即清，
      不新增代价）；被否决备选清单追加 (G)（读上一回合存量 `carried.pending_intent`——恰在
      泄漏形态里为真，按它写等于不变量不生效）。
      ② 落码（TDD，先 RED 后 GREEN）：RED 为新增用例
      `tests/test_refund_taxonomy.py::test_a_topic_change_turn_clears_the_carried_reason_for_the_next_refund`
      在改 `app/` 前失败于断言 (b)（`assert 'DAMAGED' is None`）；实现只改
      `app/agent/support_agent.py` 的那一次 `ConversationSlots(...)` 写回构造：把
      `pending_intent=` 表达式提为局部变量 `pending_intent_value`（写回值逐字等价），
      `refund_reason` 存活条件补上「本回合退款流程仍开着」（本轮规划/恢复为
      `REFUND_REQUEST`，或本轮写回的 `pending_intent_value == REFUND_REQUEST.value`），
      不成立时连同既有「建单即清」分支一并走 `None`；未新增事件、配置项、迁移、依赖、
      转人工分支或第二处写回。
      ③ 连带影响登记（已裁定：**改用例，不改实现**）：既有用例
      `test_a_carried_reason_is_not_rematched_by_the_next_message` 因本次行为改动失败——其
      turn2「包裹破损了」被读成 DELIVERY_ISSUE（`破损` 是该行的 cue，`app/agent/intents.yaml`
      cues 列表），既非退款回合也无 `REFUND_REQUEST` 写回值，按订正后的不变量必须清掉原因；
      该用例断言的正是订正前的保留行为，与口径 §5.6.1.1 代价句「插问非退款回合即清」冲突。
      裁定落地（2026-10-27 返工轮）：该用例 turn2 已换为「包裹破损了，我要申请退款」，
      并新增 `assert second.json()["slots"]["pending_intent"] == "REFUND_REQUEST"` 钉住
      「本回合退款流程仍开着」；`refund_reason == "QUALITY"` 断言逐字保留。主会话实测两条
      真实回合读数（turn1 恒为「商品有质量问题，我要申请退款」）：turn2=「包裹破损了」→
      `t2_intent=DELIVERY_ISSUE`、`t2_reason=null`；turn2=「包裹破损了，我要申请退款」→
      `t2_intent=REFUND_REQUEST`、`t2_reason=QUALITY`（后者即本用例现形态）。
      ④ `docs/**` 数字未同步（守卫失败集合仍为既有 8 行），归收尾轮 R0。
      rev-bprime-r4（2026-10-27，判**通过**）随附 3 条一般项的登记与处置（沿 13.4/13.5/13.6 格式，
      逐条列出）：
      ① **[一般]** 锚点行号漂移：口径 §5.6.1.1 引用的 `app/agent/support_agent.py:602` /
      `refund_reason=` 实参 `:613`，在当前文件实为构造 `:620`、实参 `:634`（新增注释块导致下移约
      18 行）；构造唯一性不受影响，收尾轮 R0 同步文档时一并核准。**处置：T-R0-1 ① 已核准落地**
      ——口径 §5.6.1.1 两处已于 2026-10-27 改为 `:620`/`:634`，同处被否决备选 (C) 的
      `_detect_abandon` docstring `:1215` 一并核准为 `:1235`；本文件 13.6 ②（`:227-229`）里引用
      这两个行号的那句属历史轮次记录，原文照留、未改写，仅在其后加注
      「（2026-10-27 核准为 `:620`/`:634`；历史轮次记录原文照留不改写）」保留沿革（13.7 正文以函数名指代、未写行号，无需处置）。
      ② **[一般]** 「`pending_intent` 表达式逐字等价提取」在轮次边界无提交锚点（`openspec/` 未跟踪）
      的现状下，只能经逻辑推演与既有用例通过证据间接确认；待「轮次边界产生提交锚点」落地后可直接
      复核。**处置：保留跟踪，不阻塞。**
      ③ **[一般]** 口径 §5.6.1.1 被否决备选清单字母不连续（A/C/D/F/G，无 B/E），系历史轮次沿革所致，
      不构成矛盾；建议收尾轮在清单处加一句说明，防后来者疑心有选项被静默删除。**处置：T-R0-1 ②
      已落地**——2026-10-27 已在该清单 (G) 条后追加一句说明（清单即当前完整清单，被裁撤编号未
      静默删除任何候选），(A)(C)(D)(F)(G) 正文与编号未动。

## 不在本轮范围

- **本轮（立项轮）不写产品代码、不写测试、不改迁移、不改任何既有文档**；本文件描述的是
  后续实现轮的任务，实现轮开始前不得勾选。
- 不扩写模型采纳面：退款原因 / 类型 / 档位都不由模型抽取或采纳。
- 不改退款金额计算逻辑，不引入退货流程，不引入 `refunds.product_id`。
- 不做多级会签（所需审批数恒为 1），不做按客户等级的差异化策略。
- 不放宽工具侧审批闸门，不改四眼原则，不做「退款原因选择器」UI。
- 不热加载配置：档位表与阈值改动在重启进程后生效。
- 不改 `change-id` 与归档目录名；不在未归档时把增量写进基线 `openspec/specs/`。
