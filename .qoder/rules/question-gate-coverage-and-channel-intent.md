# 问句门覆盖率读数、分列报告与渠道意图直取（R10 项目级规则）

## 性质声明

本文件只声明口径，**不改任何产品代码/测试/迁移/配置/既有文档**。文中「当前事实」均给出 `文件:行` 证据（本轮实读）；「待实现」条目落地属实现轮，不在本轮范围。本文件不重开 F-1（只读问询通路 `QuestionProbe`/`probe_question`，口径 `.qoder/rules/clarify-counter-question.md`）已定的裁决；「`model_gate` 的 `question` 出口带模型读数」这一半已由 F-1 交付，本文件只补另一半：独立覆盖率读数、可跑的分列报告、渠道意图直取接线口径。

R10 来自 `docs/下一步规划.md` §0.2:73，验收原文：「`model_gate` 的 `question` 出口要么带模型读数、要么有独立覆盖率读数；「问句 / 陈述」分列的报告可跑」；R10 正文另见 :2612 与 §14.5（:2622 起）。

---

## 1. 术语表（含消歧小节）

| 术语 | 定义 | 证据（本轮实读） |
|---|---|---|
| `model_gate` | 三级路由判定函数，输出三个字符串出口之一：`rules_hit`（规则已读到，不问模型）、`question`（规则失明且消息是求信息而非求动作）、`consult`（规则失明且消息是动作形态，唯一可**采纳**模型读数的出口） | `app/agent/support_agent.py:118-136`；出口返回点 :133 / :135 / :136 |
| `question` 出口 | `blocks_write_action(message.lower())` 为真时 `model_gate` 返回字符串 `"question"`（support_agent.py:134-135）。**它本身不返回 gap**；gap 记录由下游 `_consult_model`（support_agent.py:1576 定义；上一轮简报所称 `_apply_model_gate` 即此函数，仓内无该名）在各分支返回 | support_agent.py:134-135、:1644 |
| `_QUESTION_GAP` | question 分支的默认 gap 记录字典，形状含 `source: "rules"`、`intent: "UNKNOWN"`、`adopted: False` 等键；未成反问时逐字返回 | support_agent.py:176-185（字典体含 `gate`:180、`redirect`:182 等读数所需键）；return 点 :1662、:1666、:1673、:1681、:1696，复制点 `dict(_QUESTION_GAP)` :1697 |
| `QuestionProbe` / `probe_question` | F-1 落地的只读探针：复用候选核与 `parse` 入口，但**从不写 ledger 判定行、从不跑采纳面**；`reason` 取 `QUESTION_PROBE_REASONS` 之一表示无读数，读数成功时 `reason` 为 `None` | `app/agent/pydantic_agent.py:458-472`、:686-714（禁写理由见 :693-703 docstring） |
| `QUESTION_PROBE_REASONS` | 探针无读数时的原因值域，共 **7** 个成员，逐字为：`rules_mode`、`no_candidates`、`model_failed`、`model_none`、`model_unknown`、`model_out_of_candidates`、`matches_rule` | `app/agent/pydantic_agent.py:447-455`（注释 :443-446 明示其只骑在 `QuestionProbe.reason` 上，不是新 `ModelTrace` 键） |
| `model_gap` | 既有 trace/上下文键（非新增），question 分支各出口返回的第二元素即其值；规则侧形状另见 `support_agent.py:879-882` | support_agent.py:879、:1644-1696 |
| `clarify_reason` 值域 | 既有 trace 键上新增**值**的先例：`COUNTER_QUESTION_REASON = "COUNTER_QUESTION_INTENT_AMBIGUOUS"`（值新、键旧） | support_agent.py:163-166，消费 :232 |
| 覆盖率读数 | 本口径定义的离线比值（见第 2 节），**只寄生上表既有键，零新增** | 见第 2 节 |
| 渠道意图直取 | 渠道 payload 的 `intent` 字符串经白名单校验后被路由直接采纳为**非写**意图（见第 4 节） | `RenderedMessage.intent`（类 `app/channels/base.py:94-95`，property :110-112 从 payload 读 `intent`；渠道消息模型**无**此字段，见第 4 节接线前事实） |
| 写意图 | `_WRITE_INTENT_IDS = frozenset({Intent.REFUND_REQUEST, Intent.TICKET_CREATE})`；另有注册面 `_WRITE_INTENT_EXEMPT = frozenset({"DELIVERY_ISSUE"})` | `app/agent/routing.py:272`、:281；受控循环 :294-302，两处 `continue` 守门在 :298、:300（判据条件在 :297、:299） |
| 问句 / 陈述 | 请求文本的语言形态二分；分列报告按形态分列统计各出口（见第 3 节） | 见第 3 节 |

### 消歧

- 「覆盖率读数」≠「模型采纳率」。覆盖率回答「question 出口里有多少真拿到了模型结构化读数」；采纳面（R7 四键 `model_adopted`/`model_redirect`/`model_redirect_rejected`/`model_redirect_kind`，`ADOPTABLE_INTENTS` 见 support_agent.py:42、消费 :1757）回答「模型读数是否被据此改路由」。键空间不同，本报告只读前者。
- 「问句」≠「需要反问的请求」。`model_gate` 的 `question` 出口判的是「规则失明 + 动作形态被否」；分列报告按文本形态分列，不是意图分列。门只管「有没有结构化意图可用」，语义上的问句交给 L3、域外交给转人工（`docs/下一步规划.md` §14.5 基一 :2632：「这批请求既没有模型读数，也没有单独的覆盖率读数，等于从评测里消失（R10）」）。
- 「直取」≠「授权」。写意图即使被白名单命中也必须走既有资格校验/审批（见第 4 节 R-1 甲）。
- `bucket`（`high`/`medium`/`low`）是同分候选间的排序依据，未校准前**不参与任何判定**（`.qoder/rules/intent-candidates.md` §2.4 :104-114、术语表 :14），不是「不确定就下沉」的判据（§14.5 基三 :2636）。

## 2. 覆盖率读数契约（R-2：不解锁新键）

**裁决 R-2 = 不解锁**：覆盖率读数只寄生既有键，**零新增 trace 键 / span / 表 / 事件 / 配置项**。

依据：F-1 口径「探针键不是新 `ModelTrace` 键」（pydantic_agent.py:443-446 注释原文）；R7「默认值不落盘、新值走旧键」（support_agent.py:163-166 即先例）；阈值单一入口先例 `EscalationThresholds.no_evidence_threshold(kind)`（`app/core/policy.py:88`，未知 kind 报 `KeyError` 不静默回落 :93-94 语义）。

读数键来源（逐条，均为现状，非新增）：

1. `_QUESTION_GAP` 形状：定义 support_agent.py:176，五个逐字返回点 :1662 / :1666 / :1673 / :1681 / :1696，成反问时的唯一两处改动点是 `source`（真读数来源，写入点 :1701）与成本记录随行（:1702 `model` 键；:1698-1700 注释「The only two changes §2.2 allows」）。
2. `model_gap` 变量/键：support_agent.py:879-882（规则侧形状）、:1644-1696（question 分支出口值）。
3. `clarify_reason` 值域：含 `COUNTER_QUESTION_INTENT_AMBIGUOUS`（support_agent.py:166 定义、:232 消费）。
4. `QuestionProbe.reason`：值域 `QUESTION_PROBE_REASONS` 七成员（pydantic_agent.py:447-455，本轮逐字实读，成员数以代码为准）。

读数定义（口径，实现轮落地）：

- **分子**：`question` 出口中，**模型确实产出结构化意图读数并被客户看见（形成反问）**的条数。判据**只有一条**：gap 的 `source` 已非 `"rules"`——全分支唯一写入点 support_agent.py:1701（`question_gap["source"] = probe.source`），只有成反问出口 :1703-1715 可达，且同处 :1702 随行 `model` 键。**「模型被调用但没读数」不计入分子**：:1677-1681 出口（`not probe.asks`，探针已付费、`reason` 取无读数七值之一、gap 逐字返回 `source` 仍 `"rules"`）**留在分母、明确排除出分子**——理由：把「付过钱」计入分子会让任何进模型通路的轮次都算命中，覆盖率恒等于 100%，与分子自身的定义矛盾，读数失去意义。
- **缺口登记（不臆造判据，R-2 不解锁新键）**：:1691-1696 出口——探针**确曾产出合法读数**（`probe.intent` 非 `None`，pydantic_agent.py:770），却因标签表凑不出完整问句而未成反问（support_agent.py:1691 `draft is None`）——**在既有键下与 :1681 已付费无读数不可区分**：两处 gap 均逐字返回 `_QUESTION_GAP`（`source` 仍 `"rules"`、无 `model` 键），`QuestionProbe.reason` 不落 span，探针的 `ModelTrace` 原样返回不改写（带内搬运唯一写入点 pydantic_agent.py:661-682 只跑 `plan` 路径，`ModelTrace` 既有字段 :160-180 不含读数值）。实现轮以 gap `source` 读数为唯一兜底：此类轮次按「无读数」计，覆盖率**偏低**，报告须写明这一已知低估，不得加键补齐。
- **分母**：**只计入实际进入模型通路的 `question` 出口**。`AGENT_MODE=rules` **不计入分母**。理由（本轮实读）：规则模式在两处门提前返回——`_consult_model` 在 support_agent.py:1647-1648 直接返回（第一元素为传入 `plan`，其后四元全 `None`，连 gap 都不产），探针 `probe_question` 在 pydantic_agent.py:713-714 返回 `reason="rules_mode"` 且零调用（§9 零调用要求见 :711-712 注释）；把「从不进入模型的模式」计入分母会把模式不可达误读为覆盖率损失。（如实说明：简报所引「support_agent.py:1491、pydantic_agent.py:547-548」两处门经本轮实读**不是**规则门——前者是 `CandidateOrder` 构造、后者是 ledger 复用说明；真实门位即上述 :1647 与 :713，本文件按代码现实落笔。）
- **T4 前置裁定（编排方 2026-10-29，派单前实地核验）：覆盖率的分母由「会话级恒等式」算出，不落任何新键。** 判据链条（逐环实读，行号为工作树读数）：
  - `source="chat"` 的用量台账行在 `app/` **只有一个写入点**：`app/agent/support_agent.py:911`（`if model_trace is not None:`，落库调用在 `:915`），且**没有**复用闸——与任务侧 `app/services/runtime.py:433`（`if not model_trace.reused:`，注释原文「A reused reading was already paid for on the chat turn, so writing another row would double-count it.」）形成对照。
  - 问句出口里 `model_trace` 为 `None` 的返回点共五处：`support_agent.py:1646`（`rules_hit`）、`:1648`（规则模式）、`:1662`（上限已用）、`:1666`（开关关闭）、`:1673`（入口守卫）——这五处**不付钱、不落行**。已付费的三处：`:1681`（`not probe.asks`）、`:1696`（成句失败）、`:1703-1715`（成反问）——`probe_question` 的失败路径同样带 trace（`app/agent/pydantic_agent.py:723-725`、`:728-734`），因此「付费」与「有 chat 行」在问句通路上等价。
  - consult 出口写 chat 行的条件与它写 `gap["model"]` 的条件**逐字相同**（`support_agent.py:1734-1735`：`if model_trace is not None:` / `gap["model"] = model_trace.as_dict()`），所以带 `model` 键的 consult gap 条数等于该会话 consult 侧的 chat 行数。
  - **恒等式**：同一会话的 `chat` 行数 = 成反问数 + 付费未成句数 + consult 带 `model` 键数 ⟹ **分母 = chat 行数 − consult 带 `model` 键数**；**分子 = 带 `model` 键（`source` 已非 `"rules"`）的 `question` gap 数**；两者相减恰等于「已付费无读数」两类轮次。分类依据既有 `gate` 键，**不新增键**。复用自抵消：`app/agent/pydantic_agent.py:600`（`ModelTrace.from_dict(decision.trace, reused=True)`）、`reused` 字段 `:171`、既有序列化键 `model_reused`（`:246`）都可读出。`detail_followup` 分支（`support_agent.py:879-889`）与 `self._consult_model(...)`（`:901-910`）是互斥的 if/else，且 `:890` 显式 `model_trace = None`，因此**分子分母两侧都不计**，不会污染恒等式。
  - **三条边界**：① 关联粒度只能到**会话**——`app/models/llm_usage.py` 有 `source` / `conversation_id` / `task_id`，**没有** `trace_id`；② 排除 `source="analytics"`（`app/agent/actions.py:657`）与 `source="task"`（`app/services/runtime.py:438`、`:442`）；③ 若 `分母 − 分子` 算出负数，说明恒等式被破坏，报告必须**显式失败**，不得用 `max(0, …)` 掩盖。
  - **`QuestionProbe.reason` 不获任何消费者**：`app/` 内零读取点（`app/agent/pydantic_agent.py:447-455` 只是词表定义与方括号；`:752-753` 注释原文「探针侧不做任何枚举转换——是否把读数转成 `Intent` 是 T4 消费端的决定，不属本方法。」只把决定权交给消费端，不构成落盘授权）。R10 不为它落盘、不加键（R-2 与本节「不得加键补齐」）。台账把它写成「T4 覆盖率读数」的首个消费者，属错前提，随本轮在台账定点更正。
  - **附带一般项（独立发现，不属 R10 范围，处置归 R12）**：对话侧 `support_agent.py:911` 对 `reused` trace 无闸，任务侧 `runtime.py:433` 明确跳过——同一份复用读数在对话侧会**重复计一次成本**。改动会动既有读数、需另立口径，本轮只登记不处置。
- 阈值单一入口——**T3 改判（编排方 2026-10-29，取代本条原先「实现轮加一个 kind」的表述）**：R10 **不**在 `EscalationThresholds`（`app/core/policy.py:88` 的 `no_evidence_threshold` 同族）新增 kind。三条理由：① 覆盖率是**离线报告读数**，不是运行时判定线——§0.2 R10 行的验收原文只要求「`question` 出口要么带模型读数、要么有独立覆盖率读数」与「分列报告可跑」，没有任何一处要求运行时按阈值处置；② 加一个无消费者的 kind 就是死代码——派单前 grep 复核 `threshold(` 在 `app/` 的调用点只有 `app/services/escalation.py:126`（`clarification_rounds`）、`:287`（`signal.threshold`）、`:306`（族名）三处，`app/agent/escalation.yaml` 登记的阈值名只有 `frustration_rounds` / `after_failed_attempts` / `no_evidence_score` / `clarification_rounds` / `session_idle_minutes` 五个，覆盖率不属于其中任何一族；③ R-2 定死**零新增配置项**，而运行时阈值线的默认值要么落 `Settings`（直接撞 R-2）、要么落常量（伪装成可调）。**将来若确需给覆盖率加运行时判读线，必须先改本文件并写明落点，实现轮不得自行择一。**
- **T3 的诚实最小范围（随上条改判定死）**：把 `docs/下一步规划.md` §14.5「桶不是出口」从叙述变成**断言**。意图桶在 `app/` 的生产读取点只有 `app/agent/candidates.py` 的候选截断排序（`:175` 的 `_BUCKET_RANK[candidate.bucket]`）与 L2 序列化（`:211` 的 `"bucket"` 键）；`bucket` 在 `app/` 的其余命中是 6 个文件——令牌桶 1 个（`app/core/rate_limit.py`）与 SQL 分组 5 个（`app/sql/` 下 `builder.py` / `narrative.py` / `prefetch.py` / `service.py` / `snapshot.py`），与意图桶无关；`app/api/middleware.py` 与 `app/sql/planner.py` 只有 `TokenBucket` / `buckets` 形态、没有独立单词 `bucket`，2026-10-29 实测后**不**进白名单（空登记会静默扩大盲区）；`app/agent/pydantic_agent.py` 只 import `build_candidates` / `CandidateSet`，不读桶。「阈值族不因 R10 扩大」与「未知 kind 不静默回落」已由既有 `tests/test_policy_engine.py`（`:194`、`:196`、`:203`）钉住，**不重复写**。T3 净产出 = 一条源扫描防退化断言（`tests/` 目录下的 `test_bucket_is_not_a_decision_gate.py`），**零生产代码改动、零新增配置项、零迁移**。
- 另计 `counter_question_enabled` 关闭分支（support_agent.py:1663-1666）：开关关闭连探针都不付钱，与「上限已用」分支逐字同形（:1659-1662 注释），报告须能与「模型被问但无读数」区分——区分依据即分母只含实际进入模型通路者。

## 3. 分列报告契约（T5：入口在 `evals/`）

**裁决 T5**：报告脚本放 `evals/`，不放 `scripts/`。理由（`.qoder/rules/benchmark-harness.md` §1.0 :60 原文）：「`evals/` 回答『答得对不对、过不过门槛』」，而 `scripts/` 是「一次性/运维脚本」（同表 :62）。同族先例（本轮 `ls` 实读）：`evals/intent_candidates.py`、`evals/model_distribution.py`、`evals/intent_metrics.py` 均在 `evals/` 目录。

**定名（本轮锁定）**：`evals/question_coverage.py`（该名当前不存在，与同族命名风格一致）。

三条硬约束：

1. 纯离线读已存 trace/ledger，**零模型调用**、零写库。
2. **不触碰** `evals/intent_metrics.py` 与 `evals/intent/**`（该目录下 `authored_gap.jsonl`、`external_*.jsonl` 等为既有评测资产）。
3. **不加 Makefile target**，入口一行：`python evals/question_coverage.py`。解释器口径为**本文件自声明**（本轮 grep 复核 `.qoder/rules/` 全目录，`task-order.md` 并无此条）：一律用 `./.venv/Scripts/python.exe`（裸 `python` 是 Anaconda，读数会假）。

输出：「问句 / 陈述」分列 ×（`rules_hit` / `question` / `consult`）出口的计数与占比表，附第 2 节覆盖率读数。形态判定输入只能是请求文本本身；报告只落计数，不落客户原文、不落订单号（留痕纪律：A count, never the order numbers）。`evals/` 既有门禁口径不变——本脚本不进 `Makefile:24-44` 的 `eval` 系列目标。

## 4. 渠道意图直取契约（R-1：甲）

**裁决 R-1 = 甲**：渠道 `intent` 字符串直取**仅限非写意图**。`REFUND_REQUEST` / `TICKET_CREATE` 即使被白名单命中，仍必须走既有资格校验/审批——**直取不授权建单**。

依据：项目不变量「模型可以读、不可以授权」（§14.5 :2640 同源）；R7 §3.6；`_WRITE_INTENT_IDS`（routing.py:272）与受控断言语义（:294-302，`_WRITE_INTENT_EXEMPT` 注册面注释 :276-280）不变；`classify()` 能认领的写意图集合不因本口径扩大。

接线前事实（本轮实读 + 全仓 grep 复核）：携带渠道 payload `intent` 字符串的 property 是 **`RenderedMessage.intent`**（类定义 `app/channels/base.py:94-95`，property :110-112，透传、非 `str` 即 `None`）——**不是任何渠道消息模型上的 intent 字段**：`app/models/channel_message.py` 根本没有 `intent` 字段（本轮全文 grep 复核，零命中）。该 payload 字符串在 `app/` 的消费方**仅一处**：`app/api/v1/channels.py:74`（webhook 响应回显）。此前所引 `app/services/handoff.py:122`、:157 读的是会话表字段 `ConversationMessage.intent`（`app/models/conversation.py:44`），其写入源是会话记录（用户行 support_agent.py:518、:1184 落 `plan.intent.value`，assistant 行 :736 落 `draft.intent.value`），**与渠道 payload 无关**。**路由侧零消费，结论不变。** 台账冲突如实登记：`docs/下一步规划.md:2634`（§14.5 基二）把该 property 误归到不存在的渠道消息模型字段、并把消费方表述为 handoff（「全仓只有 `app/services/handoff.py` 拿它写意图历史」），与代码归属不符；§14.1 表行 :2584（注意：属 §14.1 七问裁定表，非 §0.2）「渠道 payload 的 `intent` 只进转人工包、不进路由」中「不进路由」与实读一致、「只进转人工包」同样不准（转人工包的 intent 来自会话表）。两处均以本文件实读为准。

白名单口径：

- 白名单**必须是 `intents.yaml` 枚举中非写行的子集**；单一来源，实现轮不得另立枚举源（候选核同款约束：`.qoder/rules/intent-candidates.md` §2.1 :26「必须是 intents.yaml 里存在的行」）。
- 外部字符串进枚举前必须过白名单，**读不出落 `UNKNOWN`，不得当作可信输入直接落库**（§14.5 基二 :2634 原文）。
- `bucket` 不参与直取判定，也不作下沉判据（:2636；intent-candidates.md :14、§2.4）。

红线：`tests/test_llm_gap.py::test_the_task_api_cannot_reroute_a_read_request_into_a_write`（:457 定义）必须保持绿，且写入 DoD；探针/直取都不得绕开 `decide_adoption` 单点（pydantic_agent.py:580、:637）。

以下为 **T6 口径轮补充（2026-10-29，编排方派单前实地核验）**，把上面「白名单口径」三条与红线扩成可实现、可判定的完整契约。全部为**待实现口径**：本文件只声明，不改任何产品代码；下列裁定（T6-1…T6-12）与其判据（§5 DoD 第 7-13 条）在实现轮落地前，仓库内没有任何直取行为存在（入站渠道 intent 今天既没有载体、也没有消费者，见上「接线前事实」与本轮补测事实 A/B）。

### 4.1 载体（T6-2）

`MessageEnvelope`（`app/channels/base.py:52-63`，frozen dataclass，现无 intent 字段）新增专用字段：

```python
declared_intent: str | None = None
```

- **明确不进 `metadata`、不进 `envelope_digest` 的 `material`**（`app/channels/base.py:261-287` 构造的是显式字面字典：channel / tenant_id / user_id / conversation_id / text / locale / metadata / attachments，再 `json.dumps(..., sort_keys=True)` 取 sha256）。因此给 dataclass 加这个新字段**不会**改变任何既有摘要，去重行为不变。
- **禁止把 intent 塞进 `metadata`**：`metadata` 在 `material` 里（`base.py:270`），塞进去会让 `envelope_digest` 变值、打穿去重（判据：§5 DoD 第 10 条「`envelope_digest` 逐字节不变」）。
- **代价登记（必须保留）**：`declared_intent` 不进摘要，于是**同一文本、不同 `declared_intent` 的两条重复消息会被判成同一封**，第二条返回第一次的响应（`gateway.py:144-149` 的 `claim` 去重分支）。本轮接受这个代价，理由：摘要覆盖的是「这封消息的意思」的去重键，为一个只有声明、没有真实发送方的可选字段扩大指纹面，换来的是既有去重全部重算。

### 4.2 适配器填充（T6-3）

三处 `MessageEnvelope` 构造点（本轮实测全集：`app/channels/im.py:59`、`app/channels/email.py:62`、`app/channels/web.py:47`）只在入站 payload 的 `intent` 值满足 `isinstance(value, str)` 时填入 `declared_intent`；非字符串（数字、`null`、对象、缺失）一律留 `None`，**不得 `str()` 强转**。形态判据与 `RenderedMessage.intent` property（`base.py:109-112`，非 `str` 即 `None`）逐字同形。现状注：`im.py:52-67` 的 `normalize` 今天连 `metadata` 都不传，入站 `intent` 键被直接丢弃——这正是「今天发送方为 0」的证据之一（T6-10）。

### 4.3 白名单来源与值域（T6-4，定死）

- **唯一来源是 `IntentCatalog` 新增的一个方法**（如 `non_write_declared_intents()`，实现轮定名）：值 = `ordered_specs()`（`app/agent/intent_catalog.py:194`，返回全部目录行）中 `write_path` 为假（`IntentSpec.write_path`，`intent_catalog.py:123`，加载处 `:291`）的行**去掉 `UNKNOWN`**。**不需要给 `intents.yaml` 加任何新键**——`write_path` 已在 `_INTENT_FIELDS`（`intent_catalog.py:62-82`，`:74`）。
- **本轮实测（venv 解释器现算，命令见收尾复验）**：`ordered_specs()` 共 12 行；`write_path` 为真恰好 3 行——`REFUND_REQUEST`（`intents.yaml:92`）、`TICKET_CREATE`（`:115`）、`DELIVERY_ISSUE`（`:235`）；「非写减 `UNKNOWN`」的白名单恰为 **8 个**，按目录序：`HUMAN_HANDOFF`、`SELF_ANALYTICS`、`REFUND_STATUS`、`REFUND_ELIGIBILITY`、`POLICY_QA`、`LOGISTICS_QUERY`、`PRODUCT_QUERY`、`ORDER_QUERY`。
- **排除** `REFUND_REQUEST`、`TICKET_CREATE`、`DELIVERY_ISSUE` 三行。第三行虽在规则侧被 `_WRITE_INTENT_EXEMPT`（`app/agent/routing.py:281`）豁免，但它 `write_path: true`，属目录已登记的写行，**不进直取白名单**——白名单判据只认 `write_path`，不读 `_WRITE_INTENT_IDS` / `_WRITE_INTENT_EXEMPT`（那两个集合是规则侧的受检面，与 `routing.py:294-302` 受控循环的两道 `continue` 守门绑定，本口径一字不动）。
- `intents.yaml` 行增减**自动**改白名单；**不得**另立枚举源、**不得**在 Python 里抄一份字面量集合。若实现确需一份缓存抄本（如 frozenset 常量），它必须 **fail-closed**：与目录推导结果不一致时拒绝放行任何值，且由一条用例逐字钉住两者相等（§5 DoD 第 7 条）。
- **两条决定性事实（本轮 venv 现测，写死进口径）**：
  - 事实 A：上述 8 个里有 **2 个不是 `SupportIntent` 的声明成员**——`HUMAN_HANDOFF`、`SELF_ANALYTICS`。`SupportIntent` 声明成员只有 10 个（`app/agent/turns.py:24-33`），这两个经 `SupportIntent(...)` 时走 `_missing_` 凭空造成员那条路（`turns.py:36-48`）。
  - 事实 B：**今天同一条路已经在跑**——`get_catalog().match("转人工"…)`、`match("找个真人"…)`、`match("人工客服"…)` 都返回 `HUMAN_HANDOFF` 行，`classify_support` 因此今天就会产出这个非声明成员（`app/agent/support_agent.py:281-282` 的 collaboration 短路直接 `SupportIntent(spec.intent_id)`）。
  - **结论（不得写成别的）**：「白名单包含这两个」不是新扩大枚举面，而是与规则路径**同源**；本口径**不得**把白名单改写成「必须等于 `SupportIntent` 声明成员集」——那会把规则今天就在产出的 `HUMAN_HANDOFF` 排除掉，制造白名单与规则路径的人为分叉。

### 4.4 解析函数与 fail-closed 次序（T6-5）

新增纯函数，与 `classify_support` 同处（`app/agent/support_agent.py`）：

```python
def resolve_declared_intent(value: object) -> SupportIntent | None: ...
```

- **白名单校验必须在 `SupportIntent(...)` 构造之前**。理由（代码现实，本轮实读）：`_missing_`（`app/agent/turns.py:36-48`）对**任意非空字符串**用 `str.__new__(cls, value)` 凭空造成员并塞进 `_value2member_map_`/`_member_map_`——先构造后校验等于把校验拆掉，还会把任意垃圾字符串永久注册进枚举。
- 返回值域封闭：合法白名单成员 → 对应 `SupportIntent`；**非法值（白名单外任意字符串）、写意图（含 `DELIVERY_ISSUE`）、`UNKNOWN`、`None`、非字符串、小写形态（如 `"policy_qa"`）一律返回 `None`**。大小写敏感是刻意的：目录行 id 是大写蛇形，直取不做 normalize。

### 4.5 采纳落点（T6-6，定死）

直取值生效**只在** `classify_support` 现有的 `if spec is None:` 那**一个**分支内（`app/agent/support_agent.py:287-288`，函数体有且只有这一个可作落点的分支），且额外要求 `route.intent is Intent.UNKNOWN`（规则 `classify()` 也没读出来）时，才把 `SupportPlan(SupportIntent.UNKNOWN, …)` 换成 `SupportPlan(resolve_declared_intent(declared_intent), …)`（解析为 `None` 时行为与今天逐字相同，仍落 `UNKNOWN`）。

- **绝不覆盖**：collaboration 短路（`:281-282`）、写意图分支（`:283-286`）、以及任何 `spec is not None` 的规则命中。直取填的是「规则与目录都失明」那一个洞，不是给外部字符串开优先权。
- `_silent` 的 `classify_support` 调用点（`app/agent/support_agent.py:1174-1179`）**不传**直取值（沿用关键字参数默认 `None`），行为逐字不变。
- `handle` → `_stage_route_and_plan`（调用点 `:833-839`）→ `classify_support` 之间的形参穿透由实现轮以 keyword-only、默认 `None` 的参数落地；两处既有调用点之外不得新增第三个 `classify_support` 调用点来旁路 `_stage_route_and_plan` 的既有装配。

### 4.6 接线（T6-7）

- `gateway.accept_webhook` 把 `declared_intent=envelope.declared_intent` 传进 `self.agent.handle(...)`（现盘调用点 `app/channels/gateway.py:152-157`，今天没有 intent 实参）。
- `SupportAgent.handle` 新增关键字参数 `declared_intent: str | None = None`（签名现盘 `app/agent/support_agent.py:377-385`）。docstring「``channel`` is provenance only… never a decision branch」（`:386-387`）不为 `declared_intent` 修改，也不被它削弱：`declared_intent` **是**决策输入，但它只在 T6-6 那一个规则失明分支被消费，且与 `channel` 是两个参数、两条纪律。
- `accept_web`（`gateway.py:65-102`，两处 `self.agent.handle(...)` 调用 `:68-74`、`:96-102`）**不改**，登记为边界：网页入口不接收外部声明。

### 4.7 不留痕（T6-8）

直取值是一次外部声明，采纳后**只活在本回合的路由读数里**：不进消息 payload、不进 `task.context`（明确**不与** `support_agent.py:828` 的 `context["channel"]` 并列——channel 是 provenance，声明值不是）、不进 span 属性、不进审计、不落库；**不新增任何 trace 键**（与第 2 节 R-2「零新增键」同向）。可观察性只能是间接的：只有最终路由读数变了才可见。这一代价如实登记；排查「这条路由是谁给的」靠渠道侧发送日志，不靠本服务留痕。

### 4.8 边界与不动项

- **`classify()` 不得读 `context["intent"]`（T6-9）**：任务 API 侧不能因为一个声明字符串被改路由。这条是红线用例（§5 第 4 条）的支撑判据——`/api/v1/tasks` 的 `context` 是调用方可控字典，任何把 `context["intent"]` 喂进规则或直取通路的写法都属越界。
- **不加配置开关（T6-10）**：今天发送方为 0（没有任何适配器携带 intent 入站——`im.py:52-67` 连 `metadata` 都不传；全仓 `app/` 唯一消费点是 `api/v1/channels.py:74` 的响应回显），落地即行为中性；且采纳面不是可调参数（R7 §2.1 同向）。回退方式 = **版本回退**，不留运行时旋钮。
- **`HUMAN_HANDOFF` 保留在白名单（T6-12），登记代价**：外部字符串可以触发转人工。这不新增能力面：客户自己打「转人工」「找个真人」今天同样触发（事实 B），且直取轮不动钱、HMAC 与身份校验（`base.py:258` 的 `verify` 链、`gateway.py:134`）一行不改。
- **F-1 交互代价（T6-11，登记）**：被直取填上意图的回合 `plan.intent` 不再是 `UNKNOWN`，因而**不再形成反问草稿**——F-1 口径 `.qoder/rules/clarify-counter-question.md` §3.3 的形成侧守卫要求 `plan.intent is SupportIntent.UNKNOWN` 才消费反问草稿。这是预期代价（外部已声明意图的回合没有「请确认意图」的信息增量），实现轮必须有用例钉住（§5 DoD 第 13 条），不得当成缺陷修掉。

### 4.9 冲突登记与补充裁定（T6-1，含原红线的重读）

- **直取不是模型读数**：渠道声明的 intent 是**调用方给的一个值**，不经模型。因此它**不得**经 `decide_adoption`（`app/agent/pydantic_agent.py:90-112`）、**不得**产出 `ModelDecision` 行、R7 四计数键（`model_adopted`/`model_redirect`/`model_redirect_rejected`/`model_redirect_kind`）对它**保持 `None`（不落盘）**——与 `.qoder/rules/adoption-surface.md` §2.3「没有记录过判定 ≠ 判过且否」同义，与 `.qoder/rules/clarify-counter-question.md` §2.2 对非采纳通路的处理同形。
- **原文内部张力（显式登记，不静默改写）**：本节上方原红线句「探针/直取都不得绕开 `decide_adoption` 单点」与「直取仅限非写意图」**不能同时按字面成立**——`ADOPTABLE_INTENTS` 唯一成员是 `REFUND_REQUEST`（写意图），任何非写读数在 `decide_adoption` 那里**都不可能**被采纳；「直取非写值」按构造就是「不经过（也无法经过）采纳判定」，若强行让它走 `decide_adoption`，得到的只会是 `adopted=False`，路由照旧，直取落空。
- **重读（补充裁定）**：该红线句在本口径下读作「**不得为直取开第二个模型采纳口**」——即禁止出现第二处把「外部读数」写进路由的采纳逻辑（`decide_adoption` 或与其等价的判断）。直取通路恰恰因为**不是**模型读数、**不进**采纳判定，才与这条红线相容；它改路由的授权来源是 T6-4…T6-6 的白名单与落点约束，不是采纳面。红线句原文（含 `test_the_task_api_cannot_reroute_a_read_request_into_a_write` 保持绿的判据）**一字不动**，本段只登记其读法。
- 与上方「白名单口径」三条的关系：第 1 条（子集约束）被 §4.3 具体化为目录推导的封闭八值；第 2 条「读不出落 `UNKNOWN`、不得当作可信输入直接落库」被 §4.4 的返回 `None`（等价于今天不落直取值时的行为）与 §4.7 的不落库承接，不冲突；第 3 条（`bucket` 不参与）不变。


## 5. 验收标准（DoD）

实现轮须逐条给证据：

1. `question` 出口覆盖率读数可由第 2 节既有键离线算出；零新增 trace 键/span/表/事件/配置项（R-2）。
2. `python evals/question_coverage.py`（venv 解释器）可跑，输出「问句 / 陈述」分列 × 三出口报告 + 覆盖率读数；纯离线、不调模型；不改 `evals/intent_metrics.py` 与 `evals/intent/**`；无 Makefile target（T5）。
3. 渠道 `intent` 直取仅对非写意图生效；`REFUND_REQUEST`/`TICKET_CREATE` 仍走资格校验/审批；白名单外字符串落 `UNKNOWN` 不落库（R-1 甲）。
4. `tests/test_llm_gap.py::test_the_task_api_cannot_reroute_a_read_request_into_a_write` 保持绿。
5. `_WRITE_INTENT_IDS`、`_WRITE_INTENT_EXEMPT` 与受控断言语义不变；采纳面 `ADOPTABLE_INTENTS` 不扩；守卫三清单（fact-baseline / code-anchors / architecture-guard）本轮与实现轮均不触碰，除非编排方另行批准。
6. 留痕纪律：报告与读数不落客户原文、不落订单号，只落计数。

以下为 **T6-b 新增条目（口径轮 2026-10-29 追加；第 3、4 条原文保留不变，新条目指向它们）**，逐条对应 §4.1-§4.9 的裁定，实现轮须逐条给证据：

7. **白名单精确集合（fail-closed，T6-4）**：`IntentCatalog` 新增方法推导出的白名单**逐字等于** §4.3 那 8 个 id（`HUMAN_HANDOFF`、`SELF_ANALYTICS`、`REFUND_STATUS`、`REFUND_ELIGIBILITY`、`POLICY_QA`、`LOGISTICS_QUERY`、`PRODUCT_QUERY`、`ORDER_QUERY`）；用例钉住「方法结果 == 由 `ordered_specs()` 现算的『非 `write_path` 且非 `UNKNOWN`』集合」，抄本与目录不一致时拒绝放行任何值；`intents.yaml` 增删行时该集合随目录自动变化（用例以目录现算为唯一基准，不写死第二份字面量断言之外的判定）。
8. **解析函数判定（T6-5）**：`resolve_declared_intent` 对「白名单外任意字符串、`REFUND_REQUEST`、`TICKET_CREATE`、`DELIVERY_ISSUE`、`UNKNOWN`、`None`、非字符串（数字/对象/`null`）、小写形态（`"policy_qa"`）」逐一返回 `None`；且白名单校验发生在 `SupportIntent(...)` 构造之前——用例断言非法值被拒后**没有**凭空长进 `SupportIntent._value2member_map_`。
9. **采纳落点（T6-6）**：直取只在 `classify_support` 的 `spec is None` 且 `route.intent is Intent.UNKNOWN` 的回合生效；collaboration 短路、写意图分支、任何规则命中均**不被**直取覆盖（各一条用例）；`_silent` 调用点（`support_agent.py:1174`）行为逐字不变；`accept_web` 两调用点不变（T6-7 边界）。
10. **载体与适配器（T6-2/T6-3）**：三处构造点（`im.py:59`、`email.py:62`、`web.py:47`）各一条用例——payload `intent` 为 `str` 时 `envelope.declared_intent` 携带该值，非字符串（含缺失）时为 `None` 且**未经过 `str()` 强转**；`envelope_digest`（`base.py:261-287`）对加了 `declared_intent` 字段前后的同一信封**逐字节不变**。
11. **零留痕扫描（T6-8/T6-1）**：一次被直取采纳的回合之后，消息 payload、`task.context`、span 属性、审计 detail 中检索不到该声明值；R7 四计数键保持 `None`（不落盘）、台账无该回合产生的 `ModelDecision` 行；且 `classify()` 不读 `context["intent"]`（T6-9：任务 API 传 `context={"intent": "POLICY_QA"}` 路由读数不变）。
12. **红线保持绿（指向第 4 条）**：`tests/test_llm_gap.py::test_the_task_api_cannot_reroute_a_read_request_into_a_write` 不改动断言地通过；第 3 条判据（非写意图之外的声明不放行、写意图走既有资格校验/审批）由本批第 8、9 条用例补足可判定形式。
13. **F-1 交互代价钉测（T6-11）与转人工代价（T6-12）**：被直取填上意图的回合 `plan.intent` 非 `UNKNOWN`，因而**不形成反问草稿**（F-1 口径 §3.3 形成侧守卫，用例钉住）；`HUMAN_HANDOFF` 直取轮只产生转人工既有形态——不动钱、HMAC 与身份校验路径一行不改（用例断言业务表行数增量与该回合转人工记录的既有形状一致）。


## 不做（R10 明确不覆盖，§14.5 四条「不做」逐条落为禁区）

1. **L2 轻量语义暂不补**（:2638）：解锁条件是 R4 基线后「某几类覆盖率长期偏低且澄清率偏高」，语料复用 R10 与 F-1 标注，不新标。
2. **工具调用当路由：不做**（:2640）：「模型可以读、不可以授权」要求模型拿不到工具 schema。
3. **分类器不许编造槽位参数**（:2642）：缺槽位就澄清，不猜；分类、槽位、路由是三件事。
4. **边执行边修正：暂不采纳**（:2644）：前提是工具幂等或可回滚，退款与建单都不是。

## 与来源的对齐

- `docs/下一步规划.md`：§0.2:73（R10 行与验收原文）、:2612（R10 正文）、§14.5 :2622-:2644（四条基据 :2632/:2634/:2636/:2638 与四条不做）。
- F-1（`.qoder/rules/clarify-counter-question.md`）：只读问询通路已交付「带模型读数」一半，本文件不重开其裁决。
- R7（`.qoder/rules/adoption-surface.md`）：采纳面四键与「默认值不落盘」先例；本文件不扩采纳面。
- R8：`no_evidence_threshold(kind)` 阈值单一入口先例（app/core/policy.py:88）。
- `.qoder/rules/benchmark-harness.md` §1.0：`evals/` 与 `bench/`、`scripts/` 三分。
- `.qoder/rules/intent-candidates.md` §2.4：`bucket` 判据与「不参与任何判定」约束。
