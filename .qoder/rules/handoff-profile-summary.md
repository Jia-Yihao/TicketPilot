# 移交包携带档案摘要（F-8）：项目级口径

> 本文件是 TicketPilot 本轮工作（把长期记忆接一个出口）的项目级声明，只规定术语、
> 契约与验收口径。流程仍遵循用户级协作规则；业务事实以 `docs/spec/项目规格.md` 为准，
> 两者冲突时先改本文件并说明理由，再改代码。
>
> 本文件**只声明口径，不改动任何产品代码、测试、配置、文档**。
> 「当前事实」均以文件:行给出证据。

---

## 1. 术语表

同一概念在本项目只有一种叫法。下表右侧的英文名是**实现必须逐字使用的标识符**
（JSON 键名、字段名、函数名）。

| 中文术语 | 唯一英文标识符 | 唯一定义 |
|---|---|---|
| 档案摘要 | `history_summary` | `customer_profiles.history_summary` 列的内容（`app/models/memory.py:41`）。客户**自己**写入的自由文本，写入时已脱敏（`app/services/memory.py:138`），读取时再脱敏一次（`:64`）。 |
| 移交包 | `HandoffBundle` | 坐席接手时看到的上下文包。定义在 `app/agent/handoff.py:131`，构建于 `app/services/handoff.py:117`，落在 `handoff_events.bundle` 的 JSON 列。 |
| 只读区 | （概念，非标识符） | 移交包里由服务端装配、不来自本轮客户自由文本的部分（`intent_history` / `transcript_digest` / `slots` / `policy_refs` 等）。 |
| 来源标注 | `history_summary_source` | 移交包里的常量字符串，指明这段文字来自哪一列。 |
| 长度上限标注 | `history_summary_max_chars` | 移交包里记录**实际生效**的截断上限的整数（取 `handoff_digest_max_chars`）。 |

### 1.0 必须消歧的同名概念

| 已有标识符 | 含义 | 与「档案摘要」的关系 |
|---|---|---|
| `transcript_digest` | 移交包里的**本会话**逐轮要点摘要（`app/services/handoff.py:168`） | 不同的东西。它是**本次会话**的话轮摘要；`history_summary` 是**跨会话**的档案摘要。两者都受 `handoff_digest_max_chars` 限制，但来源、语义与生命周期都不同，不得互相替代。 |
| `slots` | 移交包里的会话槽位（`app/services/handoff.py:171`） | 不同的东西。槽位是**结构化槽位**，档案摘要是**自由文本**。 |
| `conversation.slots` | 会话记录上的槽位 JSON | 不同的东西，**不得**从里面拼「这个客户之前说过什么」。 |
| `profile_view()["history_summary"]` | 客户**自己**读档案时看到的摘要（`app/services/memory.py:64`） | 同一个来源列，**不同的消费面**：那一条是客户看自己；本轮这条是坐席看客户。两者不得合并成同一出口。 |

---

## 2. 契约

### 2.1 移交包新增三个键

`HandoffBundle`（`app/agent/handoff.py:131`）新增三个字段：

| 字段 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `history_summary` | `str \| None` | `None` | 脱敏并截断后的档案摘要正文。 |
| `history_summary_source` | `str` | `""` | 来源标注，恒为 `"customer_profiles.history_summary"`。 |
| `history_summary_max_chars` | `int` | `0` | 实际生效的截断上限。 |

`HandoffBundle.to_dict()`（`app/agent/handoff.py:156`）**只在 `history_summary` 非空时**
写入这三个键。**为空时三个键都不出现**——不写 `null`、不写空串、不写 `0`、不占位
（NEXT_STEPS F-8「边界」原文：「字段可空，空时不占位」）。

键名**定死**为 `history_summary` / `history_summary_source` / `history_summary_max_chars`，
不得另造别名（`transcript_digest` 是别的概念，见 §1.0）。

### 2.2 内容整形：先脱敏、后截断

整形发生在**移交包落库前**（`app/services/handoff.py` 的 `HandoffService.escalate`，
即 `event.bundle = bundle.to_dict()` 那一处，`app/services/handoff.py:325`），
**不在** `build_bundle` 里：

- `build_bundle`（`app/services/handoff.py:117`）**保持纯函数**：不接 session、不查库，
  签名与形状**不变**。它是「装配包」，不是「取数据」。
- 落库点是**唯一**的补摘要入口：补在这里既不会漏（只有这一条路径写 `bundle`），
  也不会重复（`build_bundle` 只被调用一次，产出同一个包）。

1. **脱敏**：用 `app/observability/redaction.py:29` 的 `redact()`，与档案读出口
   （`app/services/memory.py:64`）**同一套规则**。`redact()` 是幂等的（它的四条正则不匹配
   掩码串 `***`），因此**读取处与构建处各脱敏一次是安全的**，不是重复劳动。
2. **截断**：用 `app/services/handoff.py:99` 的 `_clip()`，上限 = `build_bundle` 已有的
   `digest_max_chars` 参数（= `settings.handoff_digest_max_chars`，`app/core/config.py:64`，
   默认 400），与 `transcript_digest` **同一个上限**、**同一个函数**。
3. **顺序固定：先脱敏、后截断**。反了会把本该被掩码的内容留在包里。

`history_summary_max_chars` 记的是**实际生效的上限**（即 `digest_max_chars`），
**不是**原文长度。原文是否被截断由正文末尾的 `…` 表达（`_clip` 的既有约定）。

### 2.3 唯一来源

- 摘要**只**来自 `customer_profiles.history_summary` 一列。
  `history_summary_source` 恒为字面量 `"customer_profiles.history_summary"`。
- **不得**从别处取：不得从 `conversation.slots`、消息历史、任务记录、`intent_history`
  里拼一段「这个客户之前说过什么」。那是另一套事实，另一条口径。

### 2.4 读取纪律：只读、不留痕、不建行

读取方是 `app/services/memory.py` 新增的**模块级函数**（**不进 `MemoryService`**）：

- 按 `tenant_id` 与 `user_id` **逐值相等**取 `customer_profiles.history_summary` **一列**，
  不实例化 ORM 实体（与 `app/agent/personalization.py:100` `load_profile` 的既有纪律同形：
  「select 具体列，不加载实体」）。
- **不写审计**：与 ADR-014 / ADR-018 的既有纪律同向——`app/agent/personalization.py`
  的只读覆盖层不写 `memory.read` 审计，理由是「让合规事件变成噪音」；本条同样不写。
  移交包本身已被 `handoff.escalate` 审计（`app/services/handoff.py:331`）。
- **不建行、不改 `updated_at`**：只读列选择；没有档案行就是没有摘要。
- 读的是**对话客户本人**的档案（`HandoffService.escalate` 的 `identity` 形参就是该会话的
  调用方），**不是**坐席的档案。
  坐席通过移交包读客户档案，**不得**通过 `GET /api/v1/memory` 读别人的。

**为什么放 `app/services/memory.py` 而不是 `app/agent/personalization.py`**：后者是
ADR-014 刻意收窄的门（「档案进答案的唯一窄口」），它的模块文档写着「`history_summary`
is never read here」。把移交包用的读取放进去会让这扇门重新变宽。放 `memory.py` 则沿用
「档案是记忆服务的事」这一归属，同时不碰那条窄口。

### 2.5 硬性边界

1. **只进移交包**：不进回复文本（`draft.reply`）、不进提示词、不进任务上下文
   （`task.context`）、不进 trace 属性。
2. **不进模型输入**：不得出现在 `app/agent/pydantic_agent.py` 构造的 prompt 里，
   也不得进 `ModelDecision`。可判定方式：在 prompt 与 trace 里**检索不到**该摘要文本。
3. **不改回复路径的覆盖层**：`app/agent/personalization.py` 的 `ProfileSnapshot`
   **不新增** `history_summary` 字段；四种效果（`ADDRESSED` / `CARDS_REORDERED` /
   `SUGGESTIONS_TRIMMED` / `INTEREST_SUGGESTION`）的既有断言逐字不变。
4. **不改写档案**：`customer_profiles.history_summary` 的写入路径
   （`app/services/memory.py:126-138`）不变；本条只加读出口。
5. **不进队列行摘要**：`HandoffService.summary()`（`app/services/handoff.py:214`）
   仍是 triage 面（它只带 `transcript_digest`），档案摘要**只**出现在案件详情
   （`detail()` 返回的 `bundle`）里。
6. **不做「档案摘要进模型」**：即使将来模型面要用，也得另立口径并重新评估
   （与 B-1「模型不抽取、不采纳退款原因」同向，见 `.qoder/rules/refund-reason-taxonomy.md` §5.6.5）。
7. **`build_bundle` 的签名与形状不变**：它仍不接 session、不查库；既有调用点与既有产出键
   逐字不变（只多出三个新键，且只在有摘要时出现）。

### 2.6 控制台

- `web/src/types.ts:270` 的 `HandoffBundle` 接口新增三个**可选**字段。
- `web/src/pages/AgentWorkbench.tsx:294` 起的案件详情区**条件渲染**：三个键存在才显示，
  缺失**不渲染、不显示占位符**（与 `bundle.suggested_next_step` 等既有可选字段同形）。
- 前端**不得**自行维护「这个客户之前说过什么」的事实副本，也不得把摘要拼进任何
  输入框或提示。

---

## 3. 验收标准（DoD）

逐条可判定，每条都要能被测试钉住。

1. **有摘要时进包**：档案有非空摘要时，移交包 `bundle["history_summary"]` 存在。
2. **已脱敏**：包里的内容与档案里的内容按**同一套** `redact()` 规则处理过；
   用例里塞进手机号 / 邮箱 / 身份证 / 卡号，包内检索不到原文（与
   `tests/test_memory.py:111` 同一套断言形状）。
3. **超长截断**：摘要超过 `handoff_digest_max_chars` 时，包内正文长度 == 该上限
   且以 `…` 结尾；`bundle["history_summary_max_chars"]` == 该上限。
4. **空时不占位**：无档案行、或摘要为空 / 纯空白时，包内**没有**
   `history_summary` / `history_summary_source` / `history_summary_max_chars` 三个键。
5. **来源标注**：包内 `history_summary_source` 恒为 `"customer_profiles.history_summary"`。
6. **不进回复**：客户回复文本里检索不到该摘要；`app/agent/personalization.py`
   的四种效果既有断言（`tests/test_personalization.py`）逐字不变。
7. **不进模型输入**：prompt 与 trace 里检索不到该摘要文本。
8. **不留痕**：一次转人工之后，审计里的 `memory.%` 事件**没有新增**行。
9. **租户边界**：换一个 `user_id` 读不到别人的摘要；同一 `user_id` 换租户同样读不到。
10. **不退化**：`tests/test_personalization.py`、`tests/test_handoff.py`、
    `tests/test_memory.py` 全绿；既有 Golden / support 读数不变；默认配置下
    移交包的既有键**逐字不变**（除新增的三个键外无其它差异）。

---

## 4. 代价与取舍登记

1. **档案摘要是客户自己写的自由文本**，即使脱敏后也可能含不适合进案件包的措辞
   （例如情绪化抱怨、第三方姓名）。本轮接受这个代价：来源标注与长度上限给坐席判断依据，
   **不做**内容白名单 / 关键词过滤 / 敏感词拦截（那需要另一套口径，且会引入「谁定义敏感词」
   的新问题）。
2. **跨会话暴露**：`history_summary` 是**跨会话**的，而 `transcript_digest` 是**本会话**的。
   坐席在接手一通新会话时会看到该客户在**别的会话**里自己写下的摘要。
   这是本条的目的（NEXT_STEPS F-8 原话：「给坐席一个『这个客户之前说过什么』的入口」），
   但确实扩大了暴露面，登记在案。
3. **不写审计**：见 §2.4。移交包的落库由 `handoff.escalate` 审计覆盖，但审计里**不含**
   摘要正文（审计只记字段名，不记值，与 ADR-014 同向）。

---

## 5. 边界（本轮明确不做）

1. 不让档案摘要进任何模型输入或提示词。
2. 不做档案摘要的跨会话检索 / 时间线 / 版本历史。
3. 不改档案的写入路径、字段集与 500 字上限（`app/services/memory.py:41` `_HISTORY_LIMIT`）。
4. 不改 `HandoffService.summary()` 的字段集。
5. 不新增迁移、不新增配置项、不新增依赖。
6. 不改 `app/agent/personalization.py` 的 `ProfileSnapshot` 与四种效果。
7. 不做「档案摘要进坐席回复模板」。

---

## 6. 与 NEXT_STEPS 的对齐

| NEXT_STEPS 条目 | 本文件对应 |
|---|---|
| F-8 长期记忆没有出口（`docs/下一步规划.md` 的 **F-8 长期记忆没有出口（低，顺手）** 段） | 第 1 节术语 / 第 2 节契约 / 第 3 节验收 / 第 5 节边界 |
| 「只把**已脱敏的** `history_summary` 加进移交包的只读区，给坐席一个『这个客户之前说过什么』的入口，并在移交包里标明来源与长度上限」 | §2.1（三个键）、§2.2（脱敏 + 截断）、§2.3（来源）、§2.5（只进移交包） |
| 「验收：移交包出现 `history_summary` 时内容已脱敏（与 `redact()` 同一套规则）；超长按 `handoff_digest_max_chars` 截断」 | §2.2、§3 第 2 / 3 条 |
| 「验收：客户回复文本里仍不出现该字段；四种模板效果的现有断言不变」 | §2.5 第 3 条、§3 第 6 条 |
| 「边界：只进移交包，不进回复、不进模型输入；字段可空，空时不占位」 | §2.5 第 1 / 2 条、§2.1（空时不占位）、§3 第 4 / 7 条 |

> 本文件的**取舍登记**（§4 的三条代价、§2.4「不写审计」、§2.5 第 5 条「不进队列行摘要」、
> §2.6 控制台条件渲染）是本口径的补充判断，NEXT_STEPS 原文未规定；
> 实现者不得把它们读成 NEXT_STEPS 的原文要求，也不得在实现时自行择一。
