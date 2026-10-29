# F-1 单轮反问（意图歧义时问一次，不改路由）

> 本目录是 **F-1 的交付轮（T7）补写的 openspec 产物**：代码已由 T1–T6 六个提交落盘
> （`77cdf0b`→`0fbcc32`，2026-10-29），本变更目录记录的是**已经发生的那次变更**，不是待批的提案。
> 逐任务的证据（哪个文件 + 哪个 commit）、本轮实际跑过的门禁命令与逐字输出、以及未跑的
> 门禁清单在 `plan.md` 的「任务 7」与「交付证据」两节。

## 为什么

**规则读不懂、又是问句的时候，系统只给客户一份通用菜单。**
`model_gate()`（`app/agent/support_agent.py:118-136`）把回合分成三档，其中 `question` 档
落地前的实物是一个**固定字典** `_QUESTION_GAP`（`app/agent/support_agent.py:176-185`）：
`source: rules` / `intent: UNKNOWN` / `adopted: False` / `gate: question`，**一次模型都不问**。
它的理由写在函数自己的 docstring 里（`app/agent/support_agent.py:123-125`）：
「No model reading could be adopted for a read question, so the call would buy nothing.」

这句理由只说对了一半。「不能采纳」是对的（采纳面只有 `REFUND_REQUEST`，
`ADOPTABLE_INTENTS`，`app/agent/pydantic_agent.py:39`），由此推出的「所以不值得问」是错的：
模型读出来的那一行可以**不改路由、只拿去问客户确认一句**。客户问「运费谁出？」
（探针第 10 句，`scripts/llm_gap_probe.py:103`）本该得到「您是想查运费政策、查物流，还是办退款？」，
实际得到的是通用菜单。

**采纳面太窄的另一半后果在 `consult` 档。**
`_consult_model` 的采纳门（`app/agent/support_agent.py:1757`）只放行
`ADOPTABLE_INTENTS`，其余合法读数在「不采纳 → 原样返回规则读数」那一个 return 点
（`app/agent/support_agent.py:1787`）被**无声丢弃**：客户得到了正确路由不该有的兜底答案，
而我们手里明明有一个「服务端有证据、模型认为规则读错了」的行名。

**探针的三句转人工话术是这件事最直接的证据。**
`scripts/llm_gap_probe.py:100-102` 三句（「叫个活人来」「找你们负责人」「找个真人」）
在前两轮的预期标签是 `TICKET_CREATE`；规则词表读不出它们，问句门又不问模型，
于是这三句今天全部停在通用菜单里打转。

**为什么现在做。** 口径 `.qoder/rules/clarify-counter-question.md` 已于 2026-10-29
通过第 7 轮独立复查（提交 `4a4a495`），「口径通过前不派实现」的阻塞已解除
（`docs/下一步规划.md:45` 的 F-1 行如实登记了这一点）；R10「问句 / 陈述分列读数」以本项
形成为前提，晚做要改两次。

## 改什么

按能力列变更点（行为契约见 `specs/counter-question/spec.md`，逐文件步骤与证据见 `plan.md`）：

1. **只读问询通路**：`IntentBrain` 新增 `probe_question()`（`app/agent/pydantic_agent.py:686-770`），
   返回 `QuestionProbe`（:459-481）。它复用与 `plan()` 同一个候选内核与同一个
   `IntentAgent.parse`，**不调 `decide_adoption`、不产 `ModelDecision`、四计数键保持 `None`**。
2. **`question` 档可以换一次反问**：`_consult_model` 的问句支
   （`app/agent/support_agent.py:1658-1715`）在「上限未用 + 开关开 + 规则读数为 `UNKNOWN`」
   三条件成立时才付这次调用；合法新读数（含 `REFUND_REQUEST`）只进选项，**绝不采纳**。
3. **`consult` 档的被拒改道读数可以换一次反问**：触发判据照抄 `decide_adoption` 已算好的
   那一个布尔 `model_trace.redirect_rejected is True`（:1768-1775），对话层不重写等价逻辑；
   数据载体是 `ModelTrace` 新增的两个**带内、永不序列化**字段
   `answer` / `candidate_ids`（`app/agent/pydantic_agent.py:203-204`，路线 γ）。
4. **单轮上限**：`ConversationSlots.counter_question_asked: bool`
   （`app/services/conversations.py:42`，随 `to_dict` :59 / `from_dict` :85 全量往返），
   随回合末**既有的那一次** `save_slots` 写回（`app/agent/support_agent.py:721-731`）。
   **不加锁、不加版本列、不做条件更新。**
5. **反问文案是数据不是代码**：`app/agent/counter_question.yaml` + 独立 loader
   `app/agent/counter_question.py`（顶层键封闭集合 `version` / `prompt_template` /
   `labels` / `max_options`，加载期拒绝未知键，缺显示名整句不反问）。模型文本 0 参与。
6. **反问轮的 Draft 形态**：`clarification.reason` 取既有 trace 键 `clarify_reason` 的
   **新值** `COUNTER_QUESTION_INTENT_AMBIGUOUS`（零新增 trace 键），
   `expected_slot` / `resumable_intent` 定死 `None`（`app/schemas/support.py:57-58` 随之放宽为可空）。
7. **审计**：`counter_question.asked`，`detail` 恰三键
   `{gate, reading_intent, option_count}`，写在**消费点**（反问文本真的成为回复的那一处，
   `app/agent/support_agent.py:564-583`），形成但被拦的轮次一行都不写。
8. **一键回退**：`counter_question_enabled: bool = True`（`app/core/config.py:122`，
   `.env.example:209` 登记 `COUNTER_QUESTION_ENABLED=true`），回退等价范围逐字见口径 §10.2。

## 不改什么

- **不扩采纳面**：`ADOPTABLE_INTENTS` / `WRITE_INTENTS` / `REDIRECT_KINDS` 三个词表
  一行未动（`app/agent/pydantic_agent.py:39,44,53`）；`TICKET_CREATE` / `HUMAN_HANDOFF`
  的模型读数**永不建任务、永不进移交包**。
- **不放开金额**：模型输出的金额永不生效，金额仍走追问与审批。
- **不改写路径**：`blocks_write_action`（`app/agent/routing.py:237-245`）与其三道闸门、
  `intents.yaml` 的写路径行、`routing.py` 的受检 / 豁免集合、`evals/intent_metrics.py` 的
  write 口径全部不动；业务表（`tasks` / `tickets` / `refunds` / `approvals` /
  `handoff_events`）行数增量对每一批消息仍然逐条相等。
- **不引入新机制**：无锁、无版本列、无条件更新、无新表、无新迁移、无新增配置项（开关本身除外）；
  不在 `Draft.cards` 新增 kind、不新增 span / trace 键、不引入多轮澄清历史栈。
- **不改 `AGENT_MODE=rules` 的任何行为**：反问代码全部挂在两道既有 model 闸门之后
  （`app/agent/support_agent.py:1647-1648`、`app/agent/pydantic_agent.py:603-604`），
  rules 模式不可达。
- **不为本项放宽升级信号**：`repeated_clarification` 的覆盖面、阈值、`takeover` 全部不动；
  反问轮只是照常消耗共享的 `clarification_rounds` 预算。
- **不改三份守卫清单**（`docs/fact-baseline.yaml` / `docs/code-anchors.yaml` /
  `docs/architecture-guard.yaml`）、**不改任何文档里的测试数字**、**不改
  `docs/下一步规划.md` §0.2 的状态列**——按 `.qoder/rules/task-order.md` §3（:28-40）
  与 §4（:41-48），这些统一归 R12 收尾轮 re-cite。
- **不在归档前把增量写进 `openspec/specs/` 基线**（`openspec/AGENTS.md` §7）。

## 影响面

| 面 | 落点 |
|---|---|
| 模型通路 | `app/agent/pydantic_agent.py`（新增 `QuestionProbe` / `probe_question`、`ModelTrace` 两个带内字段） |
| 对话侧 | `app/agent/support_agent.py`（`_QUESTION_GAP` / `_counter_question_draft` / `CounterQuestion`、`handle` 消费点、`_consult_model` 两支、槽位写回） |
| 会话状态 | `app/services/conversations.py`（`counter_question_asked` 字段与全量往返；**无迁移**） |
| 数据文件 | `app/agent/counter_question.yaml` + `app/agent/counter_question.py`（新增） |
| 配置 | `app/core/config.py`、`.env.example`（`counter_question_enabled`） |
| 公开契约 | `app/schemas/support.py`（`SupportClarification.expected_slot` / `resumable_intent` 改为可空） |
| 留痕 | 审计动作 `counter_question.asked`；既有 `clarify_reason` 取值域 +1；零新增 span 键 |
| 测试 | `tests/test_counter_question_catalog.py`、`tests/test_question_probe.py`、`tests/test_counter_question_turn.py`、`tests/test_consult_counter_question.py`、`tests/test_counter_question_audit.py`、`tests/test_counter_question_matrix.py`（新建，共 82 条）；`tests/test_llm_gap.py`、`tests/test_security_model_cross.py` 的**成本断言**按口径 §13.3 改写，判据形状不动 |
| 文档 | ADR-039（本轮新增）；`docs/guide/接口说明.md`、`docs/guide/证据索引.md`、`docs/spec/项目规格.md`、`docs/adr/ADR-024`、`docs/下一步规划.md` §0.2 与 ADR 索引**本轮不改**，逐条登记在 `plan.md` 的「待办：需他处同步（本轮不改）」 |

## 依赖与顺序

- **实现顺序（已按此落盘）**：T1 文案数据与 loader → T2 状态位与往返 → T3 只读问询通路 →
  T4 问句档消费与回合行为 → T4b `consult` 档（路线 γ）→ T5 审计与开关回退 → T6 矩阵与门禁 →
  T7 本交付轮（openspec + ADR）。T3 是 T4/T4b 的硬前置（没有只读入口就没有「问了但不采纳」）；
  T5 的审计点必须等 T4/T4b 的消费点成形，否则会把「形成但被拦」的轮次写成反问。
- **上游**：口径 `.qoder/rules/clarify-counter-question.md`（第 7 轮独立复查通过，2026-10-29）；
  候选化底座（ADR-034）与采纳面收口（R7）已在盘上。
- **下游**：R10「问句 / 分类分列读数」以本项形成为前提（`docs/下一步规划.md:73`）；
  口径 §13.2 已为它预埋 `clarify_reason=COUNTER_QUESTION_INTENT_AMBIGUOUS` 的单列口径。
- **本轮的边界**：只补文档产物，不改代码、不改测试、不改守卫清单、不改任何既有文档，
  不归档本目录（归档属 R12 / 收尾轮）。
