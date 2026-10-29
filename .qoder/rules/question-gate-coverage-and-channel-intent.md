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

## 5. 验收标准（DoD）

实现轮须逐条给证据：

1. `question` 出口覆盖率读数可由第 2 节既有键离线算出；零新增 trace 键/span/表/事件/配置项（R-2）。
2. `python evals/question_coverage.py`（venv 解释器）可跑，输出「问句 / 陈述」分列 × 三出口报告 + 覆盖率读数；纯离线、不调模型；不改 `evals/intent_metrics.py` 与 `evals/intent/**`；无 Makefile target（T5）。
3. 渠道 `intent` 直取仅对非写意图生效；`REFUND_REQUEST`/`TICKET_CREATE` 仍走资格校验/审批；白名单外字符串落 `UNKNOWN` 不落库（R-1 甲）。
4. `tests/test_llm_gap.py::test_the_task_api_cannot_reroute_a_read_request_into_a_write` 保持绿。
5. `_WRITE_INTENT_IDS`、`_WRITE_INTENT_EXEMPT` 与受控断言语义不变；采纳面 `ADOPTABLE_INTENTS` 不扩；守卫三清单（fact-baseline / code-anchors / architecture-guard）本轮与实现轮均不触碰，除非编排方另行批准。
6. 留痕纪律：报告与读数不落客户原文、不落订单号，只落计数。

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
