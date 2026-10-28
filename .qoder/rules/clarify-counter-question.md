# 问句与丢弃读数的单轮反问（F-1）：项目级口径

本文件是 TicketPilot 对 `docs/下一步规划.md` §0.2 条目 F-1「意图采纳策略太窄」（`docs/下一步规划.md:45`，展开见 §15.5.2 `docs/下一步规划.md:2774-2782`，裁定见 §15.7 `docs/下一步规划.md:2805-2814`）的项目级声明，只规定术语、契约、配置项与验收口径，不含实现。流程仍遵循用户级协作规则。凡本文中「当前事实」均以 `文件:行` 给出实地读过的证据；行号为**工作树读数**（含现盘未提交改动，可能与 git HEAD 的行号不同——`app/agent/support_agent.py`、`app/agent/routing.py`、`app/agent/candidates.py`、`app/agent/pydantic_agent.py`、`app/services/conversations.py`、`app/services/escalation.py`、`app/core/config.py` 的工作树与 HEAD 是否一致**未经机器核对**（守卫只扫 `docs/**/*.md`，`.qoder/rules/*.md` 的行号引用无机器核对，靠每次人工对照）；引用仍以符号名为主，行号只作定位提示，行号漂移债登记为**随 R12 统一 re-cite**（级联纪律见 `.qoder/rules/task-order.md` §3/§4），相对旧台账（如 §10.2 的 `:1002`/`:1066-1074`）的漂移登记见 §14.1；`docs/下一步规划.md` 台账本轮仍在被编辑、行号会随修订漂移——凡引台账，以章节号 §15.5.2/§15.7/§10.2/§0.2 加引文原文短句为主，行号只作定位提示，本文给出的台账行号已按现盘读数重对）。「待实现」标记的条目是本口径生效前必须完成的工作。

来源冲突的裁定顺序：§15.5.2 与 §15.7（定稿）> §10.2（旧建议，其「按代价分级」已被取代，见 §14.1 登记）> §0.2 行。

## 1. 术语表

| 术语 | 唯一定义 |
|---|---|
| 反问（counter-question） | 服务端把一次合法的模型读数**呈现为问句与选项**、请求客户确认意图的行为。反问**不改变路由、不执行任何写动作**；它不是采纳（采纳的定义见 `.qoder/rules/adoption-surface.md` §2.1）。 |
| 单轮上限（one-round cap） | 同一会话内反问**至多发生一次**；判定域是**顺序回合**——前一个满足 §3 触发的回合已完成槽位写回（§5.1）后，后续回合不得再反问。并发最坏表现见 §5.1，登记为代价（§13.4），不构成对上限的放宽（§15.7「反问上限：一轮」`docs/下一步规划.md:2811`）。载体是 `ConversationSlots` 新增状态位（§5）。 |
| 问句门（question gate） | `model_gate()` 的三出口之一：`blocks_write_action()` 为真时返回 `"question"`（`app/agent/support_agent.py:116-134`，`model_gate` 定义 :116；门函数 `app/agent/routing.py:237-245`）。现状：该出口**不问模型**，直接返回固定 gap（`app/agent/support_agent.py:1493-1508`）。F-1 把它改为「可以问，但只能换来一次反问」（§15.5.2「做什么」原文，`docs/下一步规划.md:2779`）。 |
| 三出口（`model_gate` 三级） | `rules_hit` / `question` / `consult`（`app/agent/support_agent.py:116-134`；ADR-024，`docs/spec/项目规格.md`）。`rules_hit` 出口不产生模型读数，**不在 F-1 范围内，逐字不变**。 |
| 候选选项（option） | 反问句里呈现的意图候选。来源是当前轮 `CandidateSet`（`app/agent/candidates.py:60-87`），形状与上限见 §4。 |
| 续跑（resume） | 客户回答澄清后接着处理原意图的既有机制：`Draft.clarification["resumable_intent"]` → `ConversationSlots.pending_intent` → `_resume_pending`（`app/agent/support_agent.py:1170-1213`、`:551-555`、`:1594-1611`）。F-1 的反问**不使用**该机制（反问轮没有「已确认的待办意图」，见 §6）。 |
| 采纳面（adoption surface） | 唯一载体 `ADOPTABLE_INTENTS = frozenset({Intent.REFUND_REQUEST})`（`app/agent/pydantic_agent.py:39`）。F-1 不触碰它（§2.1）。 |
| 四计数键 | `model_adopted` / `model_redirect` / `model_redirect_rejected` / `model_redirect_kind`（`app/agent/pydantic_agent.py`，`ModelTrace` 字段 :186-189；默认 `None` 不落盘 :196-219, :242；口径见 `.qoder/rules/adoption-surface.md` §2.3）。 |
| 丢弃读数（discarded reading） | §15.5.2 现状「模型读出的非退款意图全部丢弃」（`docs/下一步规划.md:2778`）：consult 出口上合法非退款读数经 `decide_adoption` 判为不采纳后路由不变、回落通用菜单（`app/agent/pydantic_agent.py:581`；采纳门 `app/agent/support_agent.py:1557`）。F-1 把「丢弃后沉默」改为「丢弃后换来一次反问」。 |

## 2. 与采纳面的关系（读数通路契约）

### 2.1 三个不动（定死）

1. **不改 `ADOPTABLE_INTENTS`**（`app/agent/pydantic_agent.py:39`）。F-1 落地后它仍等于 `frozenset({Intent.REFUND_REQUEST})`。理由：§15.5.2「做什么」原话是「非退款读数**不改变路由**」（`docs/下一步规划.md:2779`），放开任何意图的采纳都会改变路由。
2. **不新增采纳入口**。采纳判定唯一入口仍是 `decide_adoption()`（`app/agent/pydantic_agent.py:90-112`），且只有现有两处消费它（`IntentBrain.plan` 内 :524、:581 与 trace 搬运 :1534-1541 不算消费——搬运不改算，见 `app/agent/support_agent.py:1536-1541` 注释「搬运而不是重算」）。
3. **反问分支不读 `WRITE_INTENTS`**（`app/agent/pydantic_agent.py:44-46`）。`.qoder/rules/adoption-surface.md` §2.1 已定死 WRITE_INTENTS「只用于分类，不是采纳面，不得拿它当采纳判据」；反问分支同样不得拿它当「哪些意图可以反问」的判据——反问选项来自候选集（§4），不来自写意图表。

### 2.2 四计数键与 `ModelDecision`（定死，不留「实现时再定」）

**问句门新增的模型读取通路**（§3.2）上：

- **不调用 `decide_adoption`，不产出 `ModelDecision` 台账行**。理由：`ModelDecision` 会被任务侧复用——`IntentBrain.plan` 的复用分支（`app/agent/pydantic_agent.py:507-545`，:511 `decision.verifies(...)`）在 digest/ruleset 匹配时直接取用记录里的 `model_intent`；问句轮读数一旦入账，后续任务轮可能复用其中的 `REFUND_REQUEST` 并过采纳门，等于**变相放开了问句采纳**，违反「写路径零变化」（§7）。复用分支的注释（:520 附近「against today's ADOPTABLE_INTENTS, never replayed as…」）说明台账复用的前提是可采纳面重判；反问通路既然不允许重判，就不许入账。
- **四计数键全部保持 `None`（不落盘）**。与问句出口现状 gap（`app/agent/support_agent.py:1493-1508`：`adopted/redirect/redirect_rejected` 为 `False`、`redirect_kind` 为 `"none"`）的区别是：那份 gap 是「不进模型、所以没有任何判定发生过」的**占位读数**，F-1 落地后问句门会进模型，但**判定（采纳/改道分类）没有发生**，把「没判过」写成 `False`/`"none"` 会污染 R7 统计语义——`.qoder/rules/adoption-surface.md` §2.3 定死了「没有记录过判定」(None) ≠ 「判过且否」(False)。「未执行采纳判定」这一事实**不记入四计数键**，只经审计事件呈现（§8）。
- 问句门读取通路的 trace 记录沿用 gap 的 `gate` 键值 `"question"`（既有），**不新增 trace 键**；若 gap 需要反映「这次真的问了模型」，唯一允许的变化是 `source` 由 `"rules"` 变为实际来源且**仅在反问真的发生时**；未触发反问（NONE/越界/上限已用/开关关）时 gap 键集与 `app/agent/support_agent.py:1493-1508` 逐字相同。此条登记为补充裁定（§14.2）。

**consult 出口**（现有陈述句通路）：四计数键**照 R7 现状记，F-1 不改任何写值与语义**。合法非退款读数「从回落通用菜单改为触发一次反问」只是给同一个读数换了消费端；`decide_adoption` 判 `adopted=False`、`redirect_rejected` 照记（`app/agent/pydantic_agent.py:581`、trace 写回 :602-614）。

**反问之后的确认轮**：是一次普通新轮次，按 R7 既有规则记键，无特殊待遇。

### 2.3 关键事实：`IntentBrain.plan` 没有只读入口，F-1 必须新增只读问询通路（定死）

- **当前事实**：`IntentBrain.plan`（`app/agent/pydantic_agent.py:476`）是 IntentBrain 唯一的模型通路，且它的两条模型分支都必然走 `decide_adoption()`——复用分支 :524（`intent = decide_adoption(rule_plan.intent, decision.model_intent).intent`）、新路径 :581（`adoption = decide_adoption(rule_plan.intent, answer)`），新路径还把判定计数写进 trace（`replace(trace, …)` 的 `adopted/redirect/redirect_rejected/redirect_kind` 赋值在 :609-612）。**不存在「只问模型、不做采纳判定」的现成入口**。
- **裁定（定死，可判定）**：问句门的「问模型但不改路由」必须落成**新增的只读问询通路**：复用底层 `IntentAgent.parse`（`app/agent/pydantic_agent.py:370-425`，llm_usage 记账与 `ModelTrace` 生成都在 parse 内部完成）与同一候选内核（`build_candidates`），上层**不调用 `decide_adoption`、不产 `ModelDecision`、四计数保持 `None`**（与 §2.2 一致）。判据即 §12.5：问句门轮四键为 `None`（不落盘）、台账无问句门产生的 `ModelDecision` 行。
- **被否决备选 (a)**：复用 `plan()` 后在反问侧「丢弃读数」——不成立。`plan()` 新路径必然执行 `decide_adoption` 并把非 None 的判定计数写入 trace（:609-612），直接违反 §2.2「四键 None」；且 `app/agent/support_agent.py:1542` 条件（`source == "pydantic_ai" and model_trace is not None`）下会产出 `ModelDecision` 入账（:1545 起构造），按 §2.2 会被任务侧复用，等于变相放开问句采纳。「丢弃读数」丢不掉「已经判过」这一事实。
- **被否决备选 (b)**：对话侧绕过 IntentBrain 另建第二套模型调用——会产生第二套模型路径、用量账本与 trace 形状，违反 §8.1（`llm_usage` 是唯一台账）与单通路纪律，否决。

## 3. 触发条件（定死）

### 3.1 总则

反问只在同时满足以下全部条件时产生，否则行为与本文件落地前逐字相同：

1. `AGENT_MODE=pydantic_ai`（两道闸门：`_consult_model` 内 `if self.settings.agent_mode != "pydantic_ai"`（`app/agent/support_agent.py:1491`）；`IntentBrain.plan` 内 rules 早退（`app/agent/pydantic_agent.py:547-548`））；
2. `counter_question_enabled` 为真（§10）；
3. `ConversationSlots` 的单轮上限状态位未置位（§5）；
4. 该轮模型返回**合法候选读数**，定义：`CandidateSet.allows(answer)` 为真（`app/agent/candidates.py:78-80`）且读数不是 `UNKNOWN` 且不是 `NONE`（`NONE_ANSWER`，`app/agent/pydantic_agent.py:29`）。

### 3.2 分出口裁定

| 出口 / 读数 | 后果（定死） |
|---|---|
| `rules_hit` | 不触发反问，逐字不变。 |
| `question` 出口，合法非 `UNKNOWN` 非 `NONE` 读数（含 `REFUND_REQUEST`） | 产生一次反问（§4）。**`REFUND_REQUEST` 读数同样只进选项，绝不采纳**——问句不是显式请求（`blocks_write_action` 的语义，`app/agent/routing.py:237-245`，docstring 原文「only an explicit request may create a refund or a ticket」），采纳它会违反「写路径零变化」。此为补充裁定（§14.2），依据是 §15.5.2 原文「非退款读数不改变路由」与 §15.7 副作用条。 |
| `question` 出口，读数 `NONE` | **不反问**。还原为今天的问句出口返回（`app/agent/support_agent.py:1493-1508` 的 gap 键集逐字不变）。理由：`NONE` 的口径是「候选里没有一个是对的」（`.qoder/rules/adoption-surface.md` §1），拿它当反问依据等于凭空造选项。 |
| `question` 出口，读数越界（不在候选集） | **不反问**，同上还原。与 consult 出口的 `model_out_of_candidates` 处理同形（`app/agent/pydantic_agent.py:568-572`：越界读数被还原为规则读数）。 |
| `question` 出口，模型调用失败 / 解析抛错 | **不反问**，行为与 consult 出口现有失败兜底同形（`app/agent/pydantic_agent.py:553-558`）。失败必须可从 `llm_usage`（`app/models/llm_usage.py`；计数写回 `app/agent/pydantic_agent.py:401-402`）与既有 trace 观察到，不得静默——但**不为此新增 trace 键**（覆盖读数留给 R10，`docs/下一步规划.md:73`）。 |
| `consult` 出口，`decide_adoption` 判不采纳的合法非退款读数 | 产生一次反问（§4）；四计数键照 §2.2 记。读数 ∈ `ADOPTABLE_INTENTS`（即 `REFUND_REQUEST`）时走今天既有采纳路径，**不反问**。 |
| 任一出口，读数与规则读数相同（无改道） | **不反问**。反问的信息增量来自「规则读不出、模型读出了别的」；规则已命中的轮次（含 `UNKNOWN`↔`UNKNOWN`）没有可确认的分歧。此条为补充裁定（§14.2）。 |

### 3.3 触发之后

反问产生时：置位状态位（§5）、记一条审计事件（§8）、产生本轮 gap/trace（§2.2、§8.3）、`clarification_count` 递增（§5.2）。反问**不阻塞**同一轮里既有的只读回答（规则命中只读意图时的正常答复照常；反问只替代「UNKNOWN 兜底菜单」这一种落点）。

## 4. 选项来源与形状（定死）

1. **来源层级**：当前轮已构造的 `CandidateSet`（`build_candidates()`，`app/agent/candidates.py:125-181`）——与 consult 通路同一调用、同一纯函数内核，**不新建候选集构造器**、不为反问扩大候选（`narrowed` 与否都照用当轮真实候选，理由：候选集是「服务端有证据的行」，反问选项的证据边界必须与之一致）。
2. **排除项**：`UNKNOWN` 不进选项（它的客户语言是「都不是」，由问句兜底句承担）；`HUMAN_HANDOFF` 是候选（`_UNCONDITIONAL`，`app/agent/candidates.py:40`），出现时按正常选项呈现，同时兜底句仍在（§4.5，两条通道并存不算重复：兜底句是客户主动表达路径，选项是呈现路径）。
3. **数量上限**：`max_options = 3`（数据值，见 §4.6）。候选多于 3 项时取 `CandidateSet.candidates` 的前 3 项，**按返回顺序，不重排**（顺序即证据序：`_ordered`，`app/agent/candidates.py:184-193`，catalog `priority` 升序、同优先级按 `intent_id`；`build_candidates` 的 narrowed 截断规则 docstring :137-139 与实现 :170-179（按桶再按 priority 截断）不变）。不放开到候选集上限 6（`_DEFAULT_MAX_CANDIDATES`，:47）——那是给模型封闭答案空间的，给客户看的选项要更窄。此数值为补充裁定（§14.2）。
4. **文案生成方**：**服务端**。问句主干与每个选项的显示名都来自 §4.6 的 YAML 数据；模型只贡献意图读数，**不生成任何面向客户的文案**。依据：项目不变量「意图与转人工条件是 YAML 数据，不是提示词」（`.qoder/rules/intent-candidates.md` 同族纪律）；且现状 `_clarify` 的选项文案同样出自服务端拼接（`app/agent/support_agent.py:1186-1195`）。
5. **形状**：问句 = 主干模板 + 选项显示名列表 + 既有兜底句「你也可以直接说「转人工」，我会转给人工同事接手。」（复用 `_clarify` 尾句原文，`app/agent/support_agent.py:1195`）。选项落进 `Draft.suggestions`（顺序同呈现序），**不落进 `Draft.cards`**（cards 的 `kind` 词表保持封闭，现状只有 `order_candidate` 计入 `clarify_candidates`，`app/agent/support_agent.py:149-158`；新增 card kind 会扩大留痕面，不做）。
6. **裁定数据落点：新独立 YAML `app/agent/counter_question.yaml` + 独立 loader（定死）**。顶层键封闭集合：`version`、`prompt_template`（问句主干）、`labels`（`intent_id → 中文显示名` 映射，必须覆盖 `app/agent/intents.yaml` 当前 12 行——意图行现状**没有任何显示名字段**：`_INTENT_FIELDS`（`app/agent/intent_catalog.py:62-82`）无 label/display 键，`intents.yaml` 各行（:58 起）只有 `cues` 等匹配字段，`SupportIntent` 枚举值是英文 ID（`app/agent/turns.py:24-33`），因此标签数据必须新写）、`max_options`。loader 以 `_TOP_FIELDS` 式封闭校验拒绝未知键——**沿用 `intent_catalog._reject_unknown` 的形态**（`app/agent/intent_catalog.py:252`，调用点 :264/:267/:281；独立文件+独立 loader 的先例：`escalation.yaml` 与 `refund_reason_rules.yaml`）。**不给 `intents.yaml` 加键、不放宽 `_TOP_FIELDS`（:94）与 `_INTENT_FIELDS`（:62-82）**：放宽会把「拼写键静默改变判定」的守卫拆掉一个角，而反问数据与意图行是两种生命周期（§15.7「意图目录：暂不扩张，先接稳已有行」`docs/下一步规划.md:2812`）。
7. 读数意图或**本次要呈现的任一选项**在 `labels` 中无对应显示名时：**不反问**并让该跳过可从审计缺席观察到（不静默吞——记日志级别事实由验收用例断言，见 §12.6）。理由：半截反问（选项缺一块）比不反问更误导。

## 5. 单轮上限状态位（定死）

### 5.1 字段与读写

- `ConversationSlots`（`app/services/conversations.py:16-81`）新增字段 **`counter_question_asked: bool`，默认 `False`**。不用计数器（上限是 1，bool 足够且无法被误读成「还可以调 N 次」）。不引入历史栈（§15.5.2「不做」原文「不引入多轮澄清的历史栈」，`docs/下一步规划.md:2781`）。
- `to_dict`/`from_dict`（`app/services/conversations.py`，全量键往返）**同步扩展**；`from_dict` 对缺键取默认 `False`，旧会话行兼容，因此**不需要任何 alembic 迁移**（槽位是 JSON dict 存储；此为定死裁定）。
- **置位**：反问产生的那一轮，在既有回合写回阶段随其他槽位一并写回（handle 回合写回 `app/agent/support_agent.py:564-602`，`clarification_count` 的写回在 :594，同处新增 `counter_question_asked` 的写回）。置位时机 = 反问文本已生成，不是模型读数成功——读数成功但未成反问（被开关/上限拦掉）不置位。
- **判定**：§3.1 条件 3。**如实登记当前事实：仓库不存在任何会话级互斥**——`grep -rn "lock" app/services/conversations.py app/api/v1/support.py` 均 0 命中；槽位是整列覆盖写（`save_slots`，`app/services/conversations.py:141`），无版本列、无 SELECT FOR UPDATE、无队列；`app/agent/support_agent.py:422-424` 的注释反而明确禁止跨调用持写锁（原文 "Holding a write lock across that call would deadlock on SQLite and block on PostgreSQL"，:423-424）。因此本状态位是**尽力（best-effort）语义**，不承诺原子互斥。
- **裁定（采纳 (b)；(a) 为被否决备选，防止实现时回头）**：「检查—置位」不做独立的原子操作，**置位并入回合既有的那一次槽位保存**——新字段随回合末的 `ConversationSlots` 构造写入（`app/agent/support_agent.py:564-602`，`clarification_count` 写回在 :594，同处新增），由既有的 `conversations.save_slots(turn.conversation, slots)`（:603）同一次落库。**理由**：不新增锁、不新增表、不新增事件、不复用既有写路径之外的任何机制，满足简报「裁定 (b) 不得引入新机制」；且置位与反问文本同次写入，天然不产生「置了位但没问出口」或反过来的中间态。**被否决备选 (a)**（纯尽力、允许置位与保存分开的独立 UPDATE）：会产生状态位与本轮可见回复不一致的窗口，破坏 §10.2 回退等价范围的可判定性，否决。
- **残余竞态窗口与最坏表现（登记为代价，不掩盖；本文件并发情形的唯一完整真实现）**：真原子的「检查—置位」需要行锁或条件更新，属新机制、本裁定禁止；残余窗口 = 「回合开始读到未置位的 carried 槽位 → 回合末才保存」的时间差。并发情形的机制事实：`resolve` 在回合开始裸读槽位 JSON（`app/services/conversations.py:105-115`），`to_dict` 全量键覆盖（`app/services/conversations.py:41-56`），`save_slots` 是整列覆盖写、后写者胜、无版本列、无锁（`app/services/conversations.py:141-145`），置位随其他槽位在回合末构造并经既有的那一次保存落库（`app/agent/support_agent.py:596-603`），且 `app/agent/support_agent.py:422-424` 注释明确禁止跨模型调用持写锁。据此：反问回合 T1 开始时读到 `False`、回合末写回 `True`；与它并发、不触发反问的回合 T2 同样在开始时读到 `False`，其回合末的整列覆盖会把已置位的 `True` **抹回 `False`**；于是在所有并发都结束之后的回合 T3 仍读到 `False`，可以再反问。因此**并发情形不承诺任何上界，也不承诺收敛时点**——此处此前登记的「最坏封顶恰 2 次」与「反问次数 ≤ 同一竞态窗口内的并发回合数（随首个写回后收敛，不随时域累积）」均不成立（整列覆盖可把置位抹回，连「并发回合数」这个天花板也不成立），显式更正作废。代价维持登记、不予量化、不再主张有界——不触及写路径（§7 判据不受影响）、不放宽顺序回合上限（§1、本节「可判定语义」条）、不因此引入新机制（无锁、无版本列、无条件更新、无新增配置项）。若实现发现该代价不可接受，回报 organizer 补裁定，不得自行加锁扩面。
- **可判定语义**：同一会话第二次满足触发条件时**必然不反问**（前一满足触发的回合已完成槽位写回后，即顺序回合；上限已置位），行为逐字回落到 F-1 落地前。并发情形不设上界（见上文竞态段），登记为代价（§13.4），不构成对本条的放宽。这是 §12.3 反用例的锚点。

### 5.2 与既有澄清预算的关系（定死）

- 反问轮复用 `Draft.clarification` 键集（§6.1），因此 `clarified=True` → `EscalationEngine.count_outcome` 把 `clarification_count` 递增 1、封顶于 `clarification_rounds` 预算（`app/services/escalation.py:109-133`；预算默认 3，`app/core/config.py:57`）。
- 与 `repeated_clarification` 信号（`app/agent/escalation.yaml:171-190`，`counter: clarification_count`、`takeover: true`）的关系：**共享预算，不新设计数器，不为 F-1 放宽或收紧该信号**。注意当前事实：该信号 `intents` 列表（:178-184）不含 `UNKNOWN`，问句轮规则读数即 `UNKNOWN`，故反问轮的递增会照常、信号是否命中仍由既有 intent 匹配决定（`app/services/escalation.py:222-223` `if intent in signal.intents`）——F-1 **不改**信号覆盖面；「不死锁」的保证来自单轮上限本身（反问在**顺序回合**上至多一次，第二次起行为与今天逐字相同，判定域见 §1、§5.1；并发情形不设上界（§5.1 竞态段、§13.4 代价登记），不改变本条结论——而今天不死锁：通用菜单 + 客户随时可说转人工），不来自这个计数器。此解读为补充裁定（§14.2）。

## 6. 客户回答之后（定死）

### 6.1 反问轮的 Draft 形态

反问轮产出 `Draft`（`app/agent/turns.py:58-81`）：`intent` = 当轮规则读数（问句门即 `UNKNOWN`）、`reply` = 问句文本、`suggestions` 含选项显示名（§4.5）、`resolved=False`、`clarification` 四键集（与 `_clarify` 同形，`app/agent/support_agent.py:1206-1211`）但取值为：

- `reason = "COUNTER_QUESTION_INTENT_AMBIGUOUS"`（新枚举值，落在既有 trace 键 `clarify_reason` 的取值域，`app/agent/support_agent.py:622`——**零新增 trace 键**）；
- `prompt` = 问句主干；
- `expected_slot = None`（反问缺的是意图不是槽位）；
- `resumable_intent = None`（**定死不写**）。理由：`resumable_intent` 的消费点把它写进 `pending_intent`（`app/agent/support_agent.py:551-555` → `ConversationSlots.pending_intent`，`app/services/conversations.py`；`_resume_pending` :1594-1611），而 pending 机制的语义是「槽位补齐后继续原意图」；反问轮意图**尚未确认**，写 pending 等于把未经确认的模型读数塞进续跑栈。

下游既有行为自动成立，作为验收锚点：`clarified = draft.clarification is not None`（:477）→ §5.2 递增；`_guard_output` 对澄清轮跳过输出守卫（:934）→ 反问轮天然不走写路径守卫之外的分支。

### 6.2 回答的映射与续跑

- 客户的下一轮是**全新轮次**，不做特殊解析：说了某选项的显示名片段或该意图的 `cues`（`app/agent/intents.yaml` 各行 cues，如 `HUMAN_HANDOFF` 的「真人」:75）→ 既有规则分类（`classify_support`，`app/agent/support_agent.py:161-190`）照常命中即走该意图的正常流程（含写路径的照常追问槽位——反问不跳过追问，§11）。
- 规则仍读不出且再次满足 §3：**不反问**（上限已置位），回落今天——即「用户选了才继续；没选/选不出就照旧」。反问不产生任何「等待映射」的挂起状态。

### 6.3 答非所问 / 拒绝 / 沉默（不死锁、不凭空建任务）

- 答非所问：按 6.2 当作普通新轮，上限已用，不反问，落今天的兜底（通用菜单/澄清）。
- 拒绝（如「都不是」）：同上；客户说「转人工」则走既有 cue→`HUMAN_HANDOFF`。
- 沉默：客户不再发言 = 会话停在今天也会停的位置；F-1 不引入超时机制、不凭空建任务（反问轮根本没有到写路径入口，§7）。

## 7. 写路径零变化（可判定形式）

1. **结构论证**（口径，实现必须保持）：反问的产生点在 §3 表列的两个出口的「读数消费」处，位于任务/工单/退款创建（`Draft` 的 action 执行）之前，且反问分支返回 `resolved=False` 的澄清 Draft（§6.1），永不进入动作执行。
2. **可判定判据**：对 `AGENT_MODE=pydantic_ai` 与 `rules` 两组，任一批次消息在 F-1 前后 `tasks` / `tickets` / `refunds` / `approvals` / `handoff_events` 表的行数增量**逐条相等**（差值 0）。
3. **既有用例/读数锚点**（实现轮必须保持通过、不得修改其断言）：`tests/test_llm_gap.py::test_model_gate_names_the_three_levels`（`tests/test_llm_gap.py:234-253`，锚定三出口）；`intents.yaml` 写路径行现状事实（第 3 轮返工按现盘重对）：`write_path: true` 共**三行**——`app/agent/intents.yaml:92 REFUND_REQUEST`（行起 :80）、`:115 TICKET_CREATE`（行起 :106）、`:235 DELIVERY_ISSUE`（行起 :223）；写路径判定的受检成员为**两个**：`app/agent/routing.py:272 _WRITE_INTENT_IDS = frozenset({Intent.REFUND_REQUEST, Intent.TICKET_CREATE})`，`DELIVERY_ISSUE` 经 `app/agent/routing.py:273-281 _WRITE_INTENT_EXEMPT` 显式豁免（理由注释 :273-275：目录行存在但无 Intent 成员，由 `classify_support()` 按目录单独决定）；`app/agent/pydantic_agent.py:44-46` `WRITE_INTENTS` 同为两成员。footer 注释「the one checked rule on the write path」（`app/agent/intents.yaml:348-358`，:348）与两受检成员的出入登记见 §14.1。**实现轮不得改这些行（intents.yaml 写路径行、routing 受检/豁免集合），`tests/test_llm_gap.py:234` 断言不动，`evals/intent_metrics.py` 的 write 口径只读、不改**（§11）。另登记 `tests/test_security_model_cross.py::test_question_with_a_guard_hit_moves_no_money`（原 `test_question_with_a_guard_hit_costs_no_model_call`，编辑后行号 `tests/test_security_model_cross.py:81-96`）：**写路面断言必须原样通过、不得修改**——`injection-question` 的 `"PROMPT_INJECTION" in categories`（:90）与 `task_status is None`（:92）、`clean-question` 的 `categories == []`（:95）、以及全表「护栏命中不动钱」面（`test_no_security_conversation_moves_money` 等其余用例）逐字不动；该用例的**成本断言** `model_calls`（:91、:96）是 §13.3 登记的预期可变项，本轮按实测读数更新。
4. `TICKET_CREATE` / `HUMAN_HANDOFF` 的模型读数**不建任务、不进移交**——`handoff.escalate` 仍只由规则分类或 cue 触发（`app/services/handoff.py:348-351` 审计先例的消费路径不变）。

## 8. 副作用面与审计（定死）

### 8.1 「除 audit_events 外无副作用」的解读（登记为裁定性解读，§14.2）

原文：§15.5.2「验收」行「反问轮除 `audit_events` 外无副作用」（`docs/下一步规划.md:2780`）与 §15.7「A 的反问上限」行同款句（`docs/下一步规划.md:2811`，行号仅作定位提示）。本口径裁定其范围为**业务副作用**：`tasks`/`tickets`/`refunds`/`approvals`/`handoff_events` 等业务表行数增量为 0。回合常规的**只读台账**照常发生且不被 F-1 扩面：消息与会话行（`ConversationService.record`，`app/services/conversations.py:147`）、槽位写回、trace/span、`llm_usage`（问句门新增一次模型调用，见 §13.3）。

### 8.2 审计动作（定死）

- 动作名：**`counter_question.asked`**（≤64 字符列宽约束见 `app/models/audit.py` `action` String(64)；命名形态先例 `handoff.escalate`，`app/services/handoff.py:351`）。
- 写入经 `AuditRepository.record`，实参与其真实签名逐字一致（`app/repositories/audit.py:15-25`：全 keyword-only，**`actor_id`（:18）与 `tenant_id`（:19）为必填实参，样例不得省略**）：

  ```python
  AuditRepository(session).record(
      actor_id=identity.user_id,      # 先例 app/services/handoff.py:349
      tenant_id=identity.tenant_id,   # 先例 app/services/handoff.py:350
      action="counter_question.asked",
      resource_type="conversation",
      resource_id=<会话ID>,
      outcome="SUCCESS",              # 大写形态与仓库既有字面量一致（handoff.py:354、tickets.py:78 等）
      detail={"gate": …, "reading_intent": …, "option_count": …},  # 恰 §8.2 三键
  )
  ```
- `detail` **允许键集定死为三项**：`gate`（`"question"`/`"consult"`）、`reading_intent`（模型读数的 intent_id）、`option_count`（本次呈现的选项数）。**禁止**出现：客户原话、订单号、选项原文/候选证据、模型原始输出。理由与留痕纪律同形：「A count, never the order numbers」（`app/agent/support_agent.py:624-626` 注释原文）；`detail` 是 JSON 列且台账他人可读（`app/models/audit.py`）。
- 每产生一次反问恰一条；被拦（开关/上限/NONE/越界/失败/缺标签）时**不产生**审计事件。

### 8.3 trace 与留痕键集

- 零新增 span 键：反问轮经 §6.1 自动落既有键 `clarify_reason`（值为 `COUNTER_QUESTION_INTENT_AMBIGUOUS`）与 `clarified=True`（`app/agent/support_agent.py:613-626`）；`clarify_slot` 为 None、`clarify_candidates` 为 0（选项不是 order card，:149-158 只数 `order_candidate`，语义不破坏）。
- `understand` span 的 `model_gap`（`app/agent/support_agent.py:347`）按 §2.2 的键集规则变化，除 §2.2 允许的两处（consult 消费端行为、question 成功反问时 `source`）外逐字不变。

### 8.4 反问轮模型调用成本的落点（定死：ModelTrace / 用量台账 / trace 属性）

- **用量台账**：问句门问询复用 `IntentAgent.parse`（§2.3），`llm_usage` 记账在 parse 内部自动完成——失败路径 `usage.calls/failures/latency_ms` 累加并抛携带 trace 的 `ModelCallError`（`app/agent/pydantic_agent.py:401-404`），成功路径六项记账（requests/retries/input_tokens/output_tokens/calls/latency_ms，:419-424）。反问分支**不另建第二本账、不新增用量键**，反问轮多出的那次调用天然反映在既有读数里（§13.3 的成本登记以此为准）。
- **ModelTrace 落点**：问句门通路的 trace 走**对话侧既有落点**——挂 `understand` span 的 `model_gap`（`app/agent/support_agent.py:347`），形状为在 gap 里以 `gap["model"] = model_trace.as_dict()` 搬运（与 consult 出口同形，`app/agent/support_agent.py:1535`）。任务侧的 `task.route` span 落点与反问通路**无关**（反问不产生任务路由结论，§7），不得借道写入。
- **`model_gap` 写什么**：键集 = 既有问句 gap 键集（`app/agent/support_agent.py:1493-1508`）加上 §2.2 允许的两处变化（`source` 为实际来源、反问真的发生时新增 `model` 子对象）；`gate` 仍为 `"question"`。**未触发反问的轮次（NONE/越界/失败/开关关/上限已用）`model_gap` 与 :1493-1508 逐字相同**（§3.2、§10.2 回退等价范围）。
- **四计数键照 R7 口径，不为反问另立**：反问通路上 `adopted/redirect/redirect_rejected/redirect_kind` 恒为 `None`（§2.2），`ModelTrace.as_dict` 对 None 不写键（键清单与条件写入 `app/agent/pydantic_agent.py:239-242`），因此 `model_gap["model"]` 自然不含四键——**不新增第五个计数键、不改写四键既有语义**（consult 出口四键写值路径 :609-612 不动，§2.2）。

## 9. `AGENT_MODE=rules` 逐字不变（定死 + 对照方式）

- 反问全部代码挂在两道既有 model 闸门之后：`app/agent/support_agent.py:1491-1492`（`agent_mode != "pydantic_ai"` 即 `return plan, None, None, None`：rules 模式下 `model_gap` 就是 `None`，与落地前逐字一致；`:1493-1508` 那份固定 gap 键集只在 `pydantic_ai` 模式走问句支时才产生，rules 模式**不可达**，不得据此要求 rules 模式凭空长出一个 gap）与 `app/agent/pydantic_agent.py:547-548`（plan 的 rules 早退）。状态位读、YAML 标签读、审计写都在这两道闸之后，rules 模式**不可达**。
- 定死三不变：rules 模式下 ① 模型调用 0 次（`llm_usage` 行数不变）；② 无 `counter_question.asked` 审计行；③ span 键集与取值与落地前逐字一致（含 `model_gap` 与 `support.reply`）。
- **对照方式**（验收用）：同一批探针语句（`scripts/llm_gap_probe.py` 的 11 句，:86-105）分别在 `AGENT_MODE=rules` 下跑，导出 trace/审计/业务表行数与落地前基线 diff=0；`tests/test_llm_gap.py` 中 §7.3 逐条登记的受保护锚点（`test_model_gate_names_the_three_levels` 等）断言不改地通过；`test_questions_never_reach_the_model_and_refunds_still_do` 的 `assert len(rows) == len(refunds)` 按 §13.3 登记的代价改为 `len(refunds) + 2`（问句门新增两次模型调用落账），除此之外该文件的既有断言逐字不变。`tests/test_security_model_cross.py::test_question_with_a_guard_hit_moves_no_money`（原 `test_question_with_a_guard_hit_costs_no_model_call`）的 `model_calls` 成本断言按 §13.3 登记改为本轮实测读数——属 F-1 预告的预期变化，允许改成本断言、不得改判据形状（该用例的写路径/护栏断言逐字不变，见 §7.3）。（本口径文件作者未运行测试——另一 agent 在同一工作树执行测试中，运行证据由实现轮复核。）

## 10. 配置与开关（定死）

1. 新增 `Settings` 字段 **`counter_question_enabled: bool = True`**（`app/core/config.py` 开关同族先例：`intent_candidates_enabled: bool = True` :113）。`.env.example` 登记 `COUNTER_QUESTION_ENABLED=true` 并附注释，段落形态照 `INTENT_CANDIDATES` 附近既有风格（`.env.example` 带注释开关段，如 :24-30 prefetch 段的「Set ENABLED=false to go back…」写法）。
2. **默认开启**，代价与回退登记：代价 = §13（问句门新增模型调用、多一轮对话进升级预算）；回退方式 = 置 `false`；**回退等价范围（定死）** = 行为与本文件落地前逐字一致——问句门不问模型（gap 即 :1493-1508 现状）、consult 出口非退款读数回落通用菜单、零 `counter_question.asked`、零状态位写回（字段仍存在但恒 `False`）、`model_gap` 与 `support.reply` 键集不变。回退用例为 §12.9。
3. 加开关而不是不加的理由：F-1 改变**客户可感知的对话形状**（多一次问句）；一个可一键回到落地前形态的闸是上生产的最低要求，且回退等价范围可被 §9/§12 的对照法验证。不加「按意图灰度」等二级开关——面越多口径越难定死。
4. 单轮上限、选项上限 3、`labels` 覆盖要求是**口径不是配置**：上限是 §15.7 裁定硬值不进 env；`max_options`/`prompt_template`/`labels` 属 §4.6 的 YAML 数据。

## 11. 硬性边界（不做清单，逐条定死）

1. 不放开金额采纳：模型输出的金额永不生效，金额仍走追问与审批（采纳面契约不变，`.qoder/rules/adoption-surface.md`；§10.2 边界原文「不放开金额」`docs/下一步规划.md:1945`）。
2. 不跳过追问：反问后客户确认了意图，槽位澄清照常（`_clarify` 主路径不改）。
3. 不把「模型说退款」变成审批：审批门（`validation: eligibility_and_approval`，`app/agent/intents.yaml:88`）不动。
4. 不采纳 `HUMAN_HANDOFF`/`TICKET_CREATE` 模型读数（§10.2 的分级建议已被 §15.7 取代，登记见 §14.1；本口径下它们最多进反问选项）。
5. 不引入多轮澄清历史栈（§15.5.2「不做」原文）。
6. 不为反问扩意图目录：`intents.yaml` 行、`SupportIntent` 枚举成员不动（§15.7「意图目录：暂不扩张，先接稳已有行」`docs/下一步规划.md:2812`）。
7. 不使用 embedding/相似度判定选项或匹配客户回答（离线确定性优先，同 `candidates.py` 的纯函数内核纪律：`build_candidates` docstring :136-137「the kernel never touches the database」；`:63-68` 是 `CandidateSet` docstring 的 `narrowed` 如实报告纪律，不是内核不查库那条）。
8. `confidence` 不参与任何反问判定（§15.7「F-3（`confidence`）：不并入本方案，独立决定」，`docs/下一步规划.md:2813`）。
9. 不新增 alembic 迁移（§5.1 裁定；`audit_events`、会话 JSON 均复用）。
10. 不改三份守卫清单（任务边界原文）。
11. 不动 `evals/intent_metrics.py` 与 `evals/intent/**`（只读基准）。
12. 不改 `ADOPTABLE_INTENTS`、`WRITE_INTENTS`、`REDIRECT_KINDS` 词表（`app/agent/pydantic_agent.py:39,44,53`）。
13. 不在 `Draft.cards` 新增 kind、不新增 span/trace 键（§4.5、§8.3）。
14. 不因反问放宽任何升级阈值或信号覆盖面（§5.2）。

## 12. 验收标准（DoD，逐条可判定；实现轮执行，本口径轮未执行）

覆盖 §15.5.2「验收」原文四条（`docs/下一步规划.md:2780`）与 §0.2 F-1 行验收原文（`docs/下一步规划.md:45`），外加简报要求的两条补充判据：

| # | 判据 | 验证方式 |
|---|---|---|
| 1 | 探针 11 句（`scripts/llm_gap_probe.py:86-105`）中转人工三句（:100-102「叫个活人来」「找你们负责人」「找个真人」）在 `AGENT_MODE=pydantic_ai` 下**至少一种**变成反问（回复为问句且 `suggestions` 含选项、伴随恰一条 `counter_question.asked`） | 探针重跑输出 + 新回归用例断言（实现轮以新回归用例满足，探针实跑读数归用户侧——本仓库评测指标由用户自行复跑；实现轮交付物中探针侧为「期望」而非「实测」） |
| 2 | 写路径结果零变化：§7.2 的行数增量差 0 判据；§7.3 登记的受保护锚点断言不改地通过；`assert len(rows) == len(refunds)` 按 §13.3 改为 `len(refunds) + 2` | 对照运行 |
| 3 | 反问上限一轮：**正用例**第一次满足触发即反问；**反用例**同一会话**顺序执行**的两次满足触发（后一回合在前一反问回合完成槽位写回 §5.1 之后开始）时第二次**不反问**且行为与落地前逐字相同（trace/审计/回复 diff）；并发情形不设上界（见 §5.1 竞态段、§13.4 代价登记），不入本条验收 | 新增两条正反用例 |
| 4 | `AGENT_MODE=rules` 逐字不变：§9 三不变 | §9 对照法 |
| 5 | 反问轮不产生第二个计数键污染：问句门反问成功的轮次，四计数键 `None`（不落盘）；consult 出口轮次四键写值与落地前一致；全库无问句门轮产生的 `ModelDecision` 行 | trace/台账表断言 |
| 6 | 选项契约：≤3+兜底句、顺序 = `CandidateSet` 返回序、文案 = YAML `labels`（模型输出文本 0 参与）；`labels` 缺键时不反问且该轮无审计事件 | 用例断言选项来源与缺键行为 |
| 7 | 审计契约：动作名、`resource_type/outcome`、detail 恰为 `{gate, reading_intent, option_count}`；断言 detail 序列化文本不含客户原话片段、`ORD-` 形态订单号 | 用例断言 |
| 8 | `NONE` / 越界 / 模型失败三分支均不反问，gap 键集与 `app/agent/support_agent.py:1493-1508` 逐字相等 | 用例断言 |
| 9 | `COUNTER_QUESTION_ENABLED=false` 时 §10.2 回退等价范围成立（对照 diff=0） | 开关对照用例 |
| 10 | 答非所问 / 拒绝 / 沉默后续轮不死锁、不建任务（§6.3 三场景用例；`clarification_count` 与 `repeated_clarification` 交互照 §5.2） | 新增用例 |
| 11 | 门禁与文档同步：实现轮必须运行并通过 pytest 全量、ruff、mypy 与三份守卫检查（`docs/fact-baseline.yaml` / `docs/code-anchors.yaml` / `docs/architecture-guard.yaml`）；`--collect-only` 收集数若变化，按 `.qoder/rules/task-order.md` §3 级联（同步三处文档并重生成说明书 docx）；`evals/**` 模型读数（探针 11 句、62 条支持集、replay）由用户侧本机模型复跑，实现轮不运行且在交付物中如实登记「未跑」（§13.5） | 可判定判据：四类门禁命令的通过记录在案；交付物中出现收集数数值或级联登记、出现「evals 读数未跑」字样；缺任一项即不满足 |

## 13. 代价与取舍登记

1. **升级预算消耗**：每次反问 `clarification_count` +1（§5.2），离 `clarification_rounds`（默认 3）更近；「转人工率」读数（`repeated_clarification` 命中数、takeover 计数）在 F-1 后可能上升——这是把「菜单打转」换成「一次确认」的预期代价，不是缺陷。会浮动的读数：`repeated_clarification` 命中数、转人工率、平均每会话轮数。
2. **自结率**：反问成功→下一轮意图确定、自结路径更快；反问失败→更近升级、自结率降。报告口径须用 `clarify_reason=COUNTER_QUESTION_INTENT_AMBIGUOUS` 把反问轮单列（为 R10 的「问句/陈述分列」预埋，`docs/下一步规划.md:73`；R10 依赖 F-1，属 R10 范围，本文件不裁定 R10 怎么做）。§10.2 边界原文「二次确认是多一轮对话，要计入转人工率与自结率的口径说明」（`docs/下一步规划.md:1946`）在本节登记履行。
3. **问句门新增模型调用**：现状 question 出口 0 次调用（`app/agent/support_agent.py:1493-1508` 直接返回），F-1 后每个满足触发条件的问句轮 1 次调用；`llm_usage` 行数与 token 成本上升，读数即成本证据（§9 要求 rules 模式此项不变）。**受影响既有用例（实现轮登记）**：`tests/test_security_model_cross.py::test_question_with_a_guard_hit_costs_no_model_call`（重命名后 `test_question_with_a_guard_hit_moves_no_money`，编辑后行号 `tests/test_security_model_cross.py:81-96`）——其两条 `model_calls == 0` 成本断言（编辑后 `injection-question` :91、`clean-question` :96）按本轮实测读数更新为 `== 1`；同一用例的「护栏命中不动钱」断言（`task_status is None` 等写路径面）**不改**，受保护登记见 §7.3，判据形状不变登记见 §9。
4. **上限换耐心**：单轮上限放弃了「客户补充细节后第二次反问」的机会；补偿是 consult 出口的常规采纳/改道读数仍然照常生长（§2.2），信息没有丢，只是不再问第二遍。另登记：上限的判定域是顺序回合（§1、§5.1），并发情形**不设上界**、不承诺收敛时点（§5.1 竞态段、§14.2）——维持为已登记的代价，不进 §12.3 验收判据。
5. **默认开启值与 P8 不退化门槛的关系裁定（§14.2 登记为补充裁定）**：
   - **先区分两种「不退化」**：`.qoder/rules/intent-candidates.md` §5 不退化门槛（:169-170）约束的是**意图读数**（11 句探针与 62 条支持集的 `intent_accuracy`）；F-1 在 `pydantic_ai` 模式下改变的是**问句轮可见回复文本**（兜底菜单→反问）。两者不是一个维度：反问轮的意图读数永不采纳、不进路由（§2.1、§3.2），分类语义不变。
   - **默认值裁定 = 维持 `true`（§10.1），且读数实际不退化**：仓库事实——`Settings.agent_mode` 默认 `"rules"`（`app/core/config.py:116`），而 `evals/support_run.py` 构造 Settings 只传 `environment/database_url/auto_create_tables`（:734-738）、`evals/replay.py` 的 `_new_app` 同样不传 `agent_mode`（:304-309）→ 62 条支持集与回放都在 **rules 模式**运行，F-1 在两道闸（`app/agent/support_agent.py:1491`、`app/agent/pydantic_agent.py:547-548`）下**不可达** → `intent_accuracy` 读数逐字不变。
   - **`REPLAY DIFF EMPTY` 前提（实读比对字段清单，非凭记忆）**：`evals/replay.py` `_conclusion`（:249）按 `_CONCLUSION_KEYS`（:56-64：`intent`/`clarified`/`clarify_reason`/`clarify_slot`/`outcome`/`action`/`reply_sha256`）比对，`_trajectory`（:273）比对 spans 序列。清单含 `reply_sha256` 意味着**回复文本的变化原则上会被回放捕捉**；但 `--self-check` 两侧均本轮现录且都在 rules 模式（:304-309），F-1 不触发、文本不变，`REPLAY DIFF EMPTY` 读数逐字不变。只有显式在 `AGENT_MODE=pydantic_ai` 下重录且批次含问句轮时才可能非空——那是预期行为变化，不是回归。
   - **处置（采纳「随实现轮同步文档读数」；「默认关闭留给评测轮」登记为被否决备选）**：评测模型读数由用户侧本机模型复跑（项目惯例），实现轮不跑 `evals/**` 模型读数，交付物如实登记「未跑」；11 句探针在 pydantic_ai 下的期望（转人工三句→反问）本就由 §12.1 作为验收判据随实现轮同步。**代价**：用户侧复跑前，文档中的 pydantic_ai 侧探针读数是「期望」而非「实测」。**被否决备选**：默认关闭、留给评测轮再开——与 §10.1 已定死的默认值裁定冲突，且 F-1 主体行为在实现轮不可验收（§12.1/§12.3 正用例全被闸死）。

## 14. 与来源的对齐表

### 14.1 冲突登记（显式，不静默）

- **§10.2「按代价分级」建议已被 §15.7 定稿取代**（§10.2 `docs/下一步规划.md:1933-1935` vs §15.7 `:2805-2814`，行号仅作定位提示）：§10.2 建议「`HUMAN_HANDOFF` 的模型结论可采纳」「`TICKET_CREATE` 采纳意图但先二次确认」；定稿的 F-1 展开（§15.5.2）是「非退款读数**不改变路由**，产生一次单轮反问」，且采纳面维持 `REFUND_REQUEST` 单成员。本文件按定稿写死（§2.1、§11.4），§10.2 的分级建议**不是**本口径的依据。
- **`intents.yaml` footer 措辞与受检成员数的出入（登记，不修订）**：footer「the one checked rule on the write path」（登记纪律的旧措辞，写于阶段 B（F-9，§15.5.1，已交付）落地时，`app/agent/intents.yaml:348`）与 `_WRITE_INTENT_IDS` 现为两受检成员（`app/agent/routing.py:272`）不吻合——footer 的约束意图是「写路径行必须逐一显式登记、不得静默跳过」（:348-358 全文即此），本轮不改 `intents.yaml` 也不改代码，仅在 §7.3 按现盘事实引用。
- §10.2 与 §0.2 引用的行号是旧读数（§10.2「现状」条 `docs/下一步规划.md:1923` 引 `support_agent.py:1002/:1066-1074`；§0.2 行 :45 引 `:1542`），工作树读数为 `_consult_model` :1434、采纳门 :1557。引用以符号名为主（本文开头纪律）。
- 简报要求的读法优先级「§15.5.2/§15.7 > §10.2 > §0.2」已在本文件开头落实。

### 14.2 原文要求 vs 补充裁定

| 口径条目 | 来源 |
|---|---|
| 反问不改路由、不执行写动作、单轮上限一轮、选项来自候选集、`question` 支「可以问，只能换一次反问」、不引入历史栈、`AGENT_MODE=rules` 逐字不变、除 `audit_events` 外无副作用 | **原文**（§15.5.2 `docs/下一步规划.md:2778-2781`「现状/做什么/验收/不做」行、§15.7「A 的反问上限」行 :2811；行号仅作定位提示） |
| 验收四条（探针三说法、写路径零变化、上限一轮、rules 不变） | **原文**（§0.2 :45、§15.5.2「验收」行 :2780） |
| 不放开金额、不跳过追问、模型说退款≠审批 | **原文**（§10.2 边界 :1945-1946，与定稿无冲突故沿用） |
| 四计数键「保持 None / consult 出口照记不改」、不写 `ModelDecision` | **补充裁定**，依据 adoption-surface §2.3 与 plan 复用分支（`app/agent/pydantic_agent.py:507-545`）；原文未及 |
| 问句门读到 `REFUND_REQUEST` 也不采纳 | **补充裁定**，依据 `blocks_write_action` 语义（`app/agent/routing.py:237-245`）+ 写路径零变化 |
| 触发条件表（NONE/越界/失败不反问；读数=规则读数不反问） | **补充裁定**（NONE/越界处理与 :563-572 现状同形；「无改道不反问」是本口径推论） |
| 选项 ≤3、`Draft.suggestions`、不新增 card kind、独立 YAML `counter_question.yaml` 及顶层键集 | **补充裁定**（原文只说「选项来自候选集」；数据落点是简报要求「定死」的项） |
| 状态位命名 `counter_question_asked`、bool、无迁移 | **补充裁定**（原文只给「上限一轮」） |
| `clarification` 复用 + `resumable_intent=None` + `reason=COUNTER_QUESTION_INTENT_AMBIGUOUS` | **补充裁定**（依据 §6.1 消费点证据） |
| 「无副作用 = 业务表」解读、审计动作名与 detail 三键、零新增 trace 键 | **补充裁定**（原文只给「除 audit_events 外」） |
| 开关 `counter_question_enabled` 默认开 + 回退等价范围 | **补充裁定**（原文无开关要求；简报允许「加与不加都要写理由」） |
| 状态位尽力（best-effort）语义如实登记、置位并入回合末槽位的同一次保存（裁定 (b)，(a) 否决）、并发情形不设上界（唯一完整真实现见 §5.1 竞态段） | **补充裁定**（第 2 轮返工：best-effort 语义与裁定 (b)/(a) 否决成立至今；第 3 轮：明确判定域为顺序回合（§1、§5.1、§12.3、§13.4）；第 6 轮：曾把「最坏封顶恰 2 次」更正为「反问次数 ≤ 同一竞态窗口内的并发回合数、随首个写回后收敛、不随时域累积」；第 7 轮：**撤回全部并发量化上界与收敛时点登记**（含第 6 轮该措辞）——理由：`save_slots` 整列覆盖写可把并发非反问回合读到的旧 `False` 写回、将已置位的 `True` 抹回，其后的回合仍会再反问，故第 6 轮的「≤ 并发回合数」天花板与「收敛」断言均被证伪；撤回后只维持三件事实：上限判定域为顺序回合、并发不设上界也不承诺收敛、代价维持登记且不引入新机制且不影响 §7 写路径判据。本轮缩小范围依据用户级规则「同一任务连续两轮被退回必须升级：缩小范围」。依据：仓库无会话级互斥、`save_slots` 整列覆盖写 `app/services/conversations.py:141-145`、`to_dict` 全量键覆盖 `app/services/conversations.py:41-56`、`resolve` 裸读槽位 JSON `app/services/conversations.py:105-115`、回合末构造 `ConversationSlots` 后经既有的单次 `save_slots` 落库 `app/agent/support_agent.py:596-603`、注释禁止跨模型调用持写锁 `app/agent/support_agent.py:422-424`） |
| `IntentBrain.plan` 无只读入口的事实、新增只读问询通路（复用 `IntentAgent.parse`、上层不 `decide_adoption` 不入账） | **补充裁定**（第 2 轮返工；依据 `app/agent/pydantic_agent.py:476, :524, :581, :609-612`，§2.3） |
| 反问轮模型调用成本落点（`llm_usage` 由 parse 自动记账、ModelTrace 挂 `understand` span 的 `model_gap`、四键 None 不落盘） | **补充裁定**（第 2 轮返工；依据 `app/agent/pydantic_agent.py:401-404, :419-424`，`app/agent/support_agent.py:347, :1535`，§8.4） |
| 默认开启与 intent-candidates §5 不退化门槛相容的裁定（评测装配 rules 模式下 F-1 不可达、读数不变）、处置=随实现轮同步、评测轮再开为否决备选 | **补充裁定**（第 2 轮返工；依据 `app/core/config.py:116`、`evals/support_run.py:734-738`、`evals/replay.py:56-64, :304-309`，§13.5） |
| DoD 第 11 行：门禁与文档同步的可判定判据 | **补充裁定**（第 2 轮返工；级联依据 `.qoder/rules/task-order.md` §3） |
| 审计样例补 `actor_id`/`tenant_id` 实参、`outcome` 取大写形态 | **修正对齐**（第 2 轮返工；非新裁定——样例改为与真实签名 `app/repositories/audit.py:15-25` 与先例 `app/services/handoff.py:349-354` 逐字一致） |
| §4.7 缺 label 不反问的适用范围：从「读数意图在 `labels` 中无对应显示名」扩到「读数意图或**本次要呈现的任一选项**在 `labels` 中无对应显示名」 | **补充裁定（T4 实现轮）**（理由同一句：半截反问比不反问更误导——任一呈现项缺名都会发出缺块的问句） |

### 14.3 与既有口径文件的交叉（只登记，不动手修订）

| 既有文件 | 交叉点 | 建议的最小修订（登记，不由本文件执行） |
|---|---|---|
| `.qoder/rules/adoption-surface.md` | §2.2 规定问句门通不过 `decide_adoption`、四键 `None`、不产 `ModelDecision`——是该口径的自然延申但 adoption-surface 现文未覆盖「不进采纳判定的模型读取通路」 | 在「留痕键集」节补一行：非采纳判定的读取通路四键保持 None，不得入账 `ModelDecision` |
| `.qoder/rules/clarification-candidates.md` | 该文件 :12 以裸名 `EscalationEngine.count_outcome` 引用引擎而未带路径；`escalation.yaml`（策略数据）在 `app/agent/`、`count_outcome`（引擎方法）在 `app/services/escalation.py:109`，分属两目录，读者无从区分。（本轮实测：该文件内**不存在**字符串 `app/agent/escalation`，此前登记的「出现目录名歧义」属不实前提，已按实际状况改写。） | 修订 :12 为双路径写明「策略数据 YAML 在 `app/agent/`、引擎方法在 `app/services/escalation.py`」 |
| `.qoder/rules/intent-candidates.md` | 反问选项上限 3（本文件 §4.3）小于候选集上限 6；两者是「呈现层」与「答案空间」两个概念 | 可加注「候选集上限约束模型答案空间，不约束客户可见选项数（见 clarify-counter-question.md §4）」 |
| `.qoder/rules/intent-candidates.md` | §5 不退化门槛（11 句探针与 62 条支持集 `intent_accuracy`，:169-170）与本文件默认开启的关系：评测装配都在 `agent_mode` 默认 `"rules"` 下构造（`app/core/config.py:116`；`evals/support_run.py:734-738`、`evals/replay.py:304-309` 均不传 `agent_mode`），F-1 不可达，读数逐字不变；变的只是 pydantic_ai 模式问句轮的可见回复文本（意图读数不退化）。完整裁定见本文件 §13.5 | 可在不退化门槛条目加注「rules 模式下 clarify-counter-question.md F-1 不可达，此门槛读数不受其影响；pydantic_ai 侧探针读数由用户侧复跑（见该文件 §13.5）」（登记，不由本文件执行修订） |
| `.qoder/rules/observability-span-and-link.md` | `clarify_reason` 取值域新增 `COUNTER_QUESTION_INTENT_AMBIGUOUS`（键不变、值扩展） | 若该文件枚举了 `clarify_reason` 取值，追加新值 |
| `.qoder/rules/task-order.md` | F-1 是 R10 前置（§0.2 执行清单的 R10 行，引句「依赖 F-1」）；本轮实测 `.qoder/rules/task-order.md` 的工作树与 git HEAD **无差异**，已按 HEAD 文本核对依赖关系：其 §3「文档事实数的级联」（`task-order.md:28-39`）与 §4「全量基线只跑一次」（`task-order.md:41-48`）是 F-1 实现轮的文档同步约束 | 无修订需求，仅登记依赖关系 |
