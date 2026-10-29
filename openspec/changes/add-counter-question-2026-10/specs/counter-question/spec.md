# 单轮反问（counter-question）

能力：规则读不出意图、而模型给出一个合法候选读数时，服务端只问客户一次「您想咨询的是哪一类」。
本增量全部落在既有能力 `support-conversation` 与 `intent-candidates` 的接缝上，不改动两者的既有契约。

唯一判定口径：`.qoder/rules/clarify-counter-question.md`（本文件的每条 Requirement 都标出对应小节）。

## ADDED Requirements

### Requirement: 反问的产生条件（口径 §3.1、§3.3）
服务端 SHALL **同时**满足以下四条才产生一次反问：本回合模型档位为 `pydantic_ai`；
`counter_question_enabled` 为真；本会话的反问上限状态位未置位；模型给出**合法候选读数**
（在当轮候选集内、且不是 `UNKNOWN`、不是 `NONE`）。任一不成立时，本回合的客户可见回复、
`model_gap` 键集与审计 SHALL 与本能力落地前逐字相同。反问 SHALL NOT 改变路由意图，
SHALL NOT 产生 `Draft.action`，SHALL NOT 进入写路径。
反问只在**当轮规则读数为 `UNKNOWN`** 时展示给客户；规则已读出具体意图的轮次 SHALL NOT 被反问替代。

#### Scenario: 规则读不出 + 合法非退款读数 → 恰一次反问
- **WHEN** 客户消息被规则读成"读不出"，模型给出候选集内的合法非退款读数
- **THEN** 回复 SHALL 是一句反问，`clarification.reason = "COUNTER_QUESTION_INTENT_AMBIGUOUS"`
- **AND** 路由意图 SHALL 仍是规则读数
- **AND** `Draft.action` SHALL 为 `None`

#### Scenario: 反问只替代"读不出"的那一种回答
- **WHEN** 同一回合的规则读数不是"读不出"
- **THEN** 该回合 SHALL NOT 出现反问文本
- **AND** 它 SHALL 拿到规则自己那一档的回答

#### Scenario: 四条前置缺一即不反问
- **WHEN** 四条前置中任一不成立
- **THEN** 回复 SHALL 逐字是本能力落地前的形态
- **AND** `model_gap` 键集 SHALL 与其落地前逐字相等

#### Scenario: 读数是「都不是」时不反问
- **WHEN** 模型答「都不是」（候选里没有一条是对的）
- **THEN** SHALL NOT 反问、SHALL NOT 重试第二次请求
- **AND** 这次已发生的调用成本 SHALL 仍可从既有用量台账读出，不静默吞

### Requirement: 单轮上限（口径 §5.1、§12.3）
每个会话的 SHALL 至多产生一次反问。上限状态位 SHALL 随本回合**既有的那一次**槽位写回落库，
SHALL NOT 引入锁、版本列、条件更新或独立 UPDATE。置位时机 SHALL 是「反问文本真的成为本回合的回复」，
形成但被拦截的轮次 SHALL NOT 置位、SHALL NOT 清除已置的位。
**如实登记（代价，不得写成承诺）**：该状态位是尽力（best-effort）语义——判定域是**顺序回合**
（前一反问回合已完成槽位写回）。槽位保存是整列覆盖写、后写者胜，与反问回合并发、且自身不触发反问的
回合会把它开始时读到的旧值写回，从而把已置位的标记**抹回未置位**。因此并发情形 **不承诺任何上界、
也不承诺收敛时点**；本规格 SHALL NOT 写任何并发次数的量化结论。

#### Scenario: 顺序回合的第二次触发不反问
- **WHEN** 同一会话**顺序执行**的第二个回合再次满足全部触发条件（前一反问回合已写回槽位）
- **THEN** SHALL NOT 反问
- **AND** 回复、`model_gap` 键集与审计 SHALL 与本能力落地前 diff=0

#### Scenario: 并发不承诺上界
- **WHEN** 一个不触发反问的回合与一个反问回合并发，且并发回合在反问回合写回之前开始
- **THEN** 该并发回合 SHALL NOT 被本规格承诺任何互斥
- **AND** 其回合末的整列覆盖写 SHALL 可能把上限标记写回未置位，使后续回合仍可再反问
- **AND** 本场景 SHALL NOT 进入验收判据（口径 §12.3 明文排除并发情形）

### Requirement: 反问选项来自候选集且全部由服务端渲染（口径 §4）
反问选项 SHALL 只来自当轮已构造的候选集：呈现顺序 = 候选返回顺序 = 模型实际收到的顺序，
SHALL NOT 重排；SHALL NOT 超过 `max_options`（3）；SHALL NOT 含「读不出」这一行；
`labels` 数据里缺显示名（读数或任一待呈现选项）时 SHALL 整句不反问。
模型输出文本 SHALL NOT 参与任何面向客户的文案。

#### Scenario: 呈现序等于候选序
- **WHEN** 一次反问成形
- **THEN** `Draft.suggestions` 的 intent_id 序 SHALL 逐字等于同一次调用传给模型的候选 id 序
- **AND** 受 `max_options` 截断与「读不出」跳过

#### Scenario: 缺一个显示名就整句不问
- **WHEN** 任一待呈现选项在 `labels` 里没有中文显示名
- **THEN** SHALL NOT 反问（不发半截问句）
- **AND** 该轮 SHALL 无 `counter_question.asked` 审计行
- **AND** 已付的调用成本 SHALL 仍可从既有用量台账读出

### Requirement: 只读问询通路不得进入采纳面（口径 §2.1、§2.2、§2.3）
`question` 档的模型问询 SHALL 是一条只读通路：SHALL NOT 调用 `decide_adoption`、
SHALL NOT 产生 `ModelDecision` 台账行、SHALL NOT 写四计数键（`adopted` / `redirect` /
`redirect_rejected` / `redirect_kind` 保持未判定状态，序列化时不落键）。
`consult` 档的既有采纳与四计数搬运 SHALL 不因反问改变。
`ModelTrace` 为反问携带的两个字段（读数、候选 id 序）SHALL NOT 出现在 `as_dict()` 的键、
SHALL NOT 被 `from_dict()` 读取，因此 SHALL NOT 进 `model_gap`、SHALL NOT 落库、
SHALL NOT 进 span、SHALL NOT 进审计 detail、SHALL NOT 进客户文本。
**分层事实（如实登记）**：这三条拒绝理由（「都不是」/ 越界 / 调用失败）在**生产者侧**
逐条钉住「不产生反问」；生产消费侧只存在**一道闸**（只判「这次问不问得起」这一个事实，
不按拒绝原因分叉），因此本规格 SHALL NOT 写成「三条生产分支各自端到端钉住」。

#### Scenario: 问句档读到退款也只进选项
- **WHEN** `question` 档探针返回 `REFUND_REQUEST`
- **THEN** 该读数 SHALL 只作为选项呈现
- **AND** 路由 SHALL NOT 变为退款
- **AND** 该轮 SHALL NOT 产生 `ModelDecision` 行

#### Scenario: 探针轮的四计数键不落盘
- **WHEN** 一次探针成功返回合法读数
- **THEN** 其 `ModelTrace.as_dict()` 键集 SHALL 与采纳面落地前逐字相等
- **AND** SHALL NOT 出现四个采纳计数键

#### Scenario: 带内字段跨不出序列化边界
- **WHEN** 一次 consult 反问成形
- **THEN** 该回合的 trace 序列化结果 SHALL NOT 含携带的读数或候选 id 序
- **AND** 从空字典还原的 trace 两字段 SHALL 为 `None`

#### Scenario: 消费侧不按拒绝原因分叉（钉的是消费面）
- **WHEN** 探针以「都不是」/ 越界 / 调用失败中**任一**原因拒绝
- **THEN** 消费侧 SHALL 走同一条回落分支
- **AND** 三者的 gap 键集 SHALL 逐字相等
- **AND** 「每种原因各自为什么被拒」 SHALL 只在生产者侧与探针自己的拒绝原因词表里钉，
  不依赖消费侧分支

### Requirement: `AGENT_MODE=rules` 逐字不变（口径 §9）
rules 模式下本能力 SHALL 完全不可达：SHALL NOT 调用模型、SHALL NOT 写反问审计行、
SHALL NOT 写状态位，span 与回复键集及取值 SHALL 与落地前逐字一致。

#### Scenario: rules 模式三不变
- **WHEN** `AGENT_MODE=rules` 下跑探针的同一批语句
- **THEN** 用量台账行数 SHALL 不变
- **AND** SHALL 无 `counter_question.asked` 行
- **AND** 每个 span 的键集与取值 SHALL 与落地前 diff=0

### Requirement: 开关回退等价范围（口径 §10.2）
`counter_question_enabled = false` 时行为 SHALL 与本能力落地前逐字一致，
且问句档 SHALL 连探针的调用成本都不付。`consult` 档的模型调用本身 SHALL NOT 被该开关关闭。

#### Scenario: 开关关闭时问句档零调用零 gap 变化
- **WHEN** 开关关闭且本回合档位是 `question`
- **THEN** SHALL NOT 调用模型
- **AND** `model_gap` SHALL 逐字是落地前那一份

#### Scenario: 开关关闭不影响 consult 档的既有调用
- **WHEN** 开关关闭且本回合档位是 `consult`
- **THEN** 既有 `plan` 调用与 `model_gap` 取值 SHALL 与本能力落地前逐字一致
- **AND** SHALL NOT 产生反问草稿

### Requirement: 反问轮消耗的是既有澄清预算（口径 §5.2）
反问轮 SHALL 走既有澄清计数（`clarified` → `clarification_count` +1，封顶
`escalation_clarification_rounds`，默认 3）；SHALL NOT 新增计数器、历史栈或专用升级信号。
SHALL NOT 为反问放宽或收紧任何既有信号。

#### Scenario: 反问轮的计数增量照常
- **WHEN** 一次反问成形
- **THEN** `clarification_count` SHALL +1
- **AND** 既有升级信号 SHALL 按其自身原有覆盖面判定，不因反问改变

#### Scenario: 答非所问 / 拒绝 / 沉默都不死锁
- **WHEN** 客户在反问之后答非所问、说「都不是」、或不再发言
- **THEN** 后续 SHALL NOT 再反问（上限已置位）
- **AND** SHALL 逐字落回本能力落地前的兜底
- **AND** SHALL NOT 凭空创建任务

### Requirement: 反问文本在升级叠加下仍然可见（口径 §8.1 的副作用面 + 消费点时序）
审计行与上限置位都发生在升级叠加**之前**，因此反问轮与升级信号同回合时 SHALL NOT 假定客户看不到反问文本。
追加型（`reply_mode` 为 `append`）信号——含「投诉」这一 `takeover: true` + `continue_task: true`
的组合——SHALL 让客户看到「反问 + 追加句」两段文本。替换型信号与非续跑的人工接管在消费点之前
即分流，SHALL NOT 走到反问消费处，因此 SHALL NOT 产生反问审计行。
本能力 SHALL NOT 使用「反问后客户必看不到反问」这类措辞。

#### Scenario: 追加型升级与反问同回合
- **WHEN** 一次反问成形，且同回合命中的是追加型升级信号
- **THEN** 回复 SHALL 是「反问文本 + 追加句」
- **AND** 该轮 SHALL 有一条 `counter_question.asked` 审计行
- **AND** 上限位 SHALL 已置位

#### Scenario: 人工接管替换在消费点之前分流
- **WHEN** 同回合判定为「人工接管且非续跑」
- **THEN** 该分支 SHALL 在反问消费点之前分流
- **AND** 该轮 SHALL NOT 有 `counter_question.asked` 行

### Requirement: 留痕与审计（口径 §8.2、§8.3）
反问轮 SHALL 恰写一条 `counter_question.asked`（`resource_type=conversation`），
`detail` SHALL 恰为 `{gate, reading_intent, option_count}`；SHALL NOT 出现客户原话、
订单号、选项原文或模型原始输出。形成但被拦（开关 / 上限 / 「都不是」/ 越界 / 失败 / 缺标签）
的轮次 SHALL NOT 写任何 `counter_question.asked` 行。trace SHALL 零新增键：
`clarify_reason` 只扩展取值域。

#### Scenario: 每次反问恰一条审计行且 detail 只有三键
- **WHEN** 一次反问成形
- **THEN** SHALL 有恰一条 `counter_question.asked`
- **AND** `detail` 键集 SHALL 等于 `{gate, reading_intent, option_count}`
- **AND** 序列化文本 SHALL NOT 含 `ORD-` 形态订单号或客户原话片段

#### Scenario: 被拦轮次零审计行但成本照常可观察
- **WHEN** 草稿形成但被拦下
- **THEN** SHALL NOT 有 `counter_question.asked` 行
- **AND** 已发生的调用 SHALL 仍反映在既有用量台账里

### Requirement: 选项数据是封闭键集的配置数据（口径 §4.6）
`app/agent/counter_question.yaml` 的顶层键 SHALL 恰为 `version` / `prompt_template` /
`labels` / `max_options`；未知键 SHALL 在加载期直接报错，SHALL NOT 静默忽略。
`labels` SHALL 覆盖意图目录当前全部行。

#### Scenario: 加载期拒绝未知键
- **WHEN** YAML 出现键集之外的顶层键
- **THEN** 加载 SHALL 报错而非忽略

### Requirement: 未执行的验收门（口径 §12.11 / §13.5）
本能力的验收 SHALL 由口径 §12 的十四条逐条判定。下列门禁**不属于**交付轮已通过的证据：
`evals/**` 全部模型读数（`scripts/llm_gap_probe.py` 的 11 句、`evals/support_set.json` 的 62 条、
`evals/replay.py --self-check`）由使用者侧本机模型复跑，实现轮与交付轮均未运行；
需要 `TICKETPILOT_TEST_POSTGRES_URL` 的 PG / Redis 侧用例未运行；`npm run build` 未运行。
§12.1 的「转人工三句至少一种变反问」在交付物中的身份是**期望**而非实测。

#### Scenario: 未跑门禁必须显式登记
- **WHEN** 交付物声称本能力验收完成
- **THEN** 它 SHALL 同时列出未运行的评测门禁
- **AND** SHALL NOT 把「期望」写成实测读数
