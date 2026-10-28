# Intent Candidates

## Purpose

服务端在问模型之前先算出这条消息「可能属于哪几个意图」，模型只能在这个集合里选或答 `NONE`；
集合由可枚举证据算出、顺序固定，模型的自报信度不参与任何判定。

## Requirements

### Requirement: 候选集由服务端确定性算出

系统 SHALL 在调用模型**之前**，由服务端算出这条消息的候选意图集合，作为模型唯一被允许
选择的集合。候选内核 SHALL 是纯函数：不读时钟、不使用随机数、不调用模型、不查询数据库；
同一组输入（消息、上下文、规则读数、目录、订单状态）SHALL 逐字产出同一结果，含候选顺序。

候选顺序 SHALL 固定为目录 `priority` 升序、同优先级按 `intent_id`。订单状态 SHALL 由调用方
先取好再传入内核，内核不查库。

#### Scenario: 同一输入两次结果一致
- **WHEN** 用同一条消息与同一份上下文调用两次候选内核
- **THEN** 两次的候选 `intent_id` 序列与每条候选的证据串序列逐字相同

#### Scenario: 未传订单状态时不出现订单证据
- **WHEN** 调用候选内核时 `order_state` 为空
- **THEN** 所有候选的证据串里都不含 `order:` 前缀

### Requirement: 证据来源是封闭集合

每个候选的证据 SHALL 只来自五类已登记的来源：目录 cue 命中（`cue:<词>`）、规则读数
（`rule:<intent>`，非 `UNKNOWN`）、真实存在的槽位（`slot:order_id` / `slot:amount_cents`）、
会话上下文（`context:pending_intent` / `context:last_intent`）、订单状态（`order:<status>`）。
系统 SHALL NOT 接受模型自述、向量相似度或任何未登记来源作为证据。缺槽 SHALL NOT 记为证据。

其中 `rule:` / `cue:` / `context:` 是**内容证据**，SHALL 能单独让一行入选；`slot:` 与 `order:`
是**加强证据**，SHALL 只附着在已经因内容证据入选的行上，SHALL NOT 单独让任何行入选。

两类加强证据的附着面不同：`slot:<槽位名>` SHALL 只附着在**目录行把该槽位声明为必填**的行上；
`order:<状态>` SHALL 只附着在同样声明必填 `order_id` 的行上。附着面由目录行定义决定，
不由证据是否存在决定——消息里出现订单号，不会给一个不要求订单号的意图加上 `slot:` 证据。

#### Scenario: 命中 cue 的行列出该 cue
- **WHEN** 消息里逐字出现某意图行的某个 cue
- **THEN** 该候选的证据含 `cue:<该词>`，且一行最多记 3 个、按目录顺序

#### Scenario: 只有槽位不算依据
- **WHEN** 消息里只有订单号、没有任何 cue 或规则读数
- **THEN** 没有任何候选自称拿到内容证据（每个候选的桶都是 `low`）
- **AND** 候选集不收窄，`narrowed` 为 `false`

#### Scenario: 缺槽不产生证据
- **WHEN** 某意图行要求订单号而本轮与上下文都没有订单号
- **THEN** 该候选（若因其他证据进候选）的证据里不含 `slot:order_id`

### Requirement: 无条件候选与无证据行的排除

`HUMAN_HANDOFF` 与 `UNKNOWN` SHALL 无条件在候选集内（无证据时桶记 `low`，被内容证据命中时
按同一张桶表判定），且 SHALL NOT 被候选上限截断。

只要**至少一行普通意图行**拿到内容证据，没有内容证据的普通意图行 SHALL NOT 进候选——候选集
SHALL NOT 因为「模型可能想选」而包含目录里没有依据的行。

无条件候选（`HUMAN_HANDOFF` / `UNKNOWN`）SHALL NOT 作为收窄的触发者：即使它们被内容证据命中，
也不改变「本轮是否收窄」的判定，因为它们本来就在候选集里，命中与否不改变集合形状。

若**没有任何一行普通意图行**拿到内容证据，候选集 SHALL NOT 收窄：系统 SHALL 把整份目录的
**行名**（只有 `intent_id`，不含行定义）交给模型，并 SHALL 以 `narrowed=false` 如实标记；
此时上限不生效。理由是窄到只剩两行兜底等于没给模型选择的余地，而这类句子正是模型补位的场合；
把它当短名单会虚报覆盖率，因此评测 SHALL 把无信号集单独统计。

`narrowed` SHALL 表示「本轮是否因内容证据收窄」，SHALL NOT 表示「集合大小是否变小」——
一个收窄后仍包含全部内容证据行的集合，`narrowed` 仍为 `true`。

#### Scenario: 空命中不收窄
- **WHEN** 一条消息不命中任何 cue、规则读数为 `UNKNOWN`、上下文为空
- **THEN** 候选集含 `HUMAN_HANDOFF` 与 `UNKNOWN`，桶均为 `low`
- **AND** `narrowed` 为 `false`，整份目录的行名都在，且没有一个候选自称有内容证据

### Requirement: 候选上限与截断

带内容证据的候选数 SHALL 有上限（默认 6，可由 `INTENT_CANDIDATES_MAX` 配置）。超限时 SHALL
先按桶（`high` > `medium` > `low`）、再按目录 `priority` 截断；`HUMAN_HANDOFF` 与 `UNKNOWN`
SHALL NOT 占用该额度。

#### Scenario: 超限按桶截断
- **WHEN** 带内容证据的意图行数超过上限
- **THEN** 留在候选集里的是桶更高、`priority` 更靠前的行
- **AND** 无条件候选仍全部在集合里

### Requirement: 信度桶只由服务端证据决定

每个候选 SHALL 带一个服务端信度桶，取 `high` / `medium` / `low`，判据是证据的**种类数**：
至少 2 种且其中一种为 `rule:` 或 `cue:` 时为 `high`；恰好 1 种且是 `rule:` 或 `cue:` 时为
`medium`；其余（只有 `context:`，或无条件候选无证据）为 `low`。

模型自报的 `confidence` SHALL NOT 参与桶计算，也 SHALL NOT 参与任何路由、采纳或升级判定；
该字段只作为「模型当时说了什么」随 trace 记录一次。桶 SHALL NOT 在未校准前被用作阈值。

#### Scenario: 高自报置信度不改桶
- **WHEN** 模型对某候选返回 `confidence=0.99`，而服务端为该候选只算出一种 `slot:` 证据
- **THEN** 该候选的桶仍是 `low`

### Requirement: 模型输出面收窄到候选集与 NONE

模型被允许说出的意图 SHALL 是候选集内的 `intent_id` 或 `NONE`。注入模型的 SHALL 只有 L1
（候选 `intent_id` 与其证据串）；完整意图目录 SHALL NOT 被注入。`NONE` SHALL 是合法回答，
含义是「候选里没有一个是对的」；`NONE` SHALL NOT 是候选，不进桶、不进目录校验。

#### Scenario: 未被选为候选的行不进 prompt
- **WHEN** 组装本次模型请求的 prompt 负载
- **THEN** 负载里只出现候选的 `intent_id` 与证据串
- **AND** 不出现未被选为候选的目录行文案或整份目录

### Requirement: 越界不重试并回退规则读数

当模型返回的 `intent_id` 不在候选集内且不是 `NONE`，系统 SHALL 判定为越界，SHALL 回退到
规则读数，且 SHALL NOT 为此追加第二次模型请求。成本记录 SHALL 带上
`model_reason = "model_out_of_candidates"`。模型返回 `NONE` 时 SHALL NOT 被当作越界，
SHALL NOT 采纳、SHALL NOT 创建任务，并记 `model_reason = "model_none"`。

#### Scenario: 越界只付一次调用
- **WHEN** 假模型返回一个不在候选集内的意图
- **THEN** 本轮计划等于规则读数
- **AND** `ModelTrace.requests == 1`（没有因越界而重试）
- **AND** `ModelTrace.reason == "model_out_of_candidates"`

#### Scenario: NONE 不触发任务
- **WHEN** 假模型返回 `NONE`
- **THEN** 不创建任务、不采纳模型读数
- **AND** `ModelTrace.reason == "model_none"`

### Requirement: 采纳面保持不变

候选化 SHALL 只改变「模型能说什么」，SHALL NOT 改变「服务端据此做什么」。对话入口 SHALL
仍只采纳 `REFUND_REQUEST` 读数，其余模型读数照旧丢弃；写路径 SHALL 仍由规则门、必填槽位与
审批决定；幂等键与模型决策复用（`ModelDecision`）的校验口径 SHALL NOT 因候选化改变。

#### Scenario: 非退款读数照旧丢弃
- **WHEN** 模型在候选集内选中 `TICKET_CREATE`
- **THEN** 对话入口不因此创建工单，行为与候选化之前一致

#### Scenario: 复用命中时原样带回 reason
- **WHEN** 写路径复用了入口那次模型读数，且那次记录的 `model_reason` 非空
- **THEN** 该 reason 原样进入本次留痕，不重新判定、不额外发请求

### Requirement: 规则模式不进入候选路径

`AGENT_MODE=rules` SHALL NOT 调用候选内核、SHALL NOT 产生任何新增 trace 字段，其回复与
候选化之前 SHALL 逐字一致。模型不可用时的失败路径 SHALL 仍按 `rules_fallback` 记录并带成本。

#### Scenario: 规则模式零调用
- **WHEN** 以 `AGENT_MODE=rules` 处理一条消息
- **THEN** 模型桩件一次未被调用，候选内核未被调用
- **AND** 回放自检仍报告 diff 为空

### Requirement: 开关关闭时不注入候选

`INTENT_CANDIDATES_ENABLED=false` 时，系统 SHALL NOT 计算或注入候选，SHALL NOT 产生
`model_reason`，且合法字面量的读数与来源标记 SHALL 与引入候选化之前一致。
唯一的差异 SHALL 是「字面量之外的回答」的处理方式，且 SHALL 被如实写进 ADR：引入候选化之前由
输出类型校验拒绝并落成 `rules_fallback`，之后是「模型答了但不采纳」，路由结果同为规则读数。
两条路径都回退规则读数，因此 SHALL NOT 改变任何写路径结果。

#### Scenario: 关闭后合法字面量照旧
- **WHEN** 关闭候选开关，模型返回一个字面量意图
- **THEN** 该读数被采纳，来源标记为 `pydantic_ai`，`model_reason` 为空

#### Scenario: 关闭后字面量之外仍不进路由
- **WHEN** 关闭候选开关，模型返回一个不在字面量集合里的意图
- **THEN** 路由结果仍是规则读数
- **AND** 该差异在 ADR 里被记录，而不是被宣称为「逐字一致」

### Requirement: 生产接线把上下文与订单状态真实传入

候选内核 SHALL NOT 自己取上下文或查库；对话入口 SHALL 在调用内核**之前**由服务端取好
会话上下文与订单状态并传入。`context:pending_intent` / `context:last_intent` 与
`order:<状态>` 两类证据 SHALL 在真实入口上可达——只在单元测试里直传参数、生产代码没有接线，
SHALL NOT 被视为满足本条。

#### Scenario: 真实入口把上下文字段交给内核
- **WHEN** 一次对话请求带着上一轮遗留的待确认意图进入入口
- **THEN** 注入模型的候选里，对应该意图的行带 `context:<字段名>` 证据
- **AND** 该证据出现在入口实际调用内核时传入的上下文里，而不是测试里手工构造的参数

#### Scenario: 真实入口把订单状态交给内核
- **WHEN** 一条消息带了本人名下存在的订单号
- **THEN** 需要订单上下文的候选带 `order:<状态>` 证据
- **AND** 订单状态由入口在调用内核前取好，内核自身没有数据库会话

#### Scenario: 不需要证据的路径不去取
- **WHEN** `AGENT_MODE=rules` 或候选开关关闭
- **THEN** 入口既不取上下文也不查订单状态，模型与内核都不被调用

### Requirement: 留痕只记计数与原因，不记原文

span 与任务事件 SHALL 记录候选数量与「是否越界 / 是否 `NONE`」以及 `model_reason`，
SHALL NOT 记录候选证据串——证据串含用户消息内容，写入留痕等于把原文搬进 trace。

#### Scenario: 越界轮次的留痕形状
- **WHEN** 一次越界回退完成
- **THEN** `understand` span 与 `task.routed` 载荷里能看出回退原因是越界
- **AND** 属性里不出现任何候选证据串

### Requirement: 候选质量的评测分层与诚实标注

评测脚本 SHALL 在 P1 两层语料上**分层**报告候选覆盖率（`gold ∈ 候选`）、平均候选数、
桶分布与无条件候选占比，`external` 与 `authored_gap` SHALL NOT 合并成一个数字。
桶分布 SHALL NOT 设通过/失败判定。越界率 SHALL 只在有真实模型 A/B 读数时报告；
没有读数时报告与文档 SHALL 写成「未测」，SHALL NOT 用假模型或规则回退模拟。

#### Scenario: 分层报告
- **WHEN** 运行候选评测脚本
- **THEN** 输出按层给出覆盖率与平均候选数，不输出单一合并数字

#### Scenario: 无真实读数时标注未测
- **WHEN** 本轮未能跑通真实模型的 A/B
- **THEN** 报告与文档中的越界率一项写作「未测」，并说明缺少什么条件
