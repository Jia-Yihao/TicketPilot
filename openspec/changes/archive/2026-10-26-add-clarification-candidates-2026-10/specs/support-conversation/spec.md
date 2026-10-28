# Support Conversation — 规格增量

## ADDED Requirements

### Requirement: 缺订单号的第二次追问必须给出本人候选

系统 SHALL 在同一个订单相关意图第二次因缺少订单号而追问时，用确定性查询与模板
给出该调用者自己的最近订单候选（最多 3 条，按支付时间倒序），每条候选 SHALL
只携带 L1 事实：订单号、商品名、订单状态、签收时间。系统 SHALL NOT 为生成候选
调用模型，也 SHALL NOT 猜测或模糊匹配订单号。

#### Scenario: 第一次追问只有槽位提问
- **WHEN** 调用者问「帮我查一下订单」而没有给订单号
- **THEN** 回复只有订单号追问，`context_cards` 为空
- **AND** `clarification.expected_slot` 为 `order_id`

#### Scenario: 第二次追问列出候选
- **WHEN** 同一会话里第二次仍然缺少订单号
- **THEN** 回复带 ≤3 张 `kind=order_candidate` 的卡片，字段只有订单号、商品名、
  状态与签收时间
- **AND** `suggestions` 含这些订单号，客户点选即可作为下一句

#### Scenario: 候选只来自调用者本人
- **WHEN** 同一租户下另一个用户、或另一个租户拥有更多订单
- **THEN** 候选里只出现当前 `tenant_id` 与 `user_id` 自己的订单

#### Scenario: 没有历史订单时不编造候选
- **WHEN** 调用者名下没有任何订单
- **THEN** 第二次追问与第一次一样只有槽位提问，不出现任何候选卡片

#### Scenario: 其它槽位的追问不挂候选
- **WHEN** 第二次追问缺的是退款金额而不是订单号
- **THEN** 回复不出现订单候选卡片

### Requirement: 澄清预算由独立阈值决定，第三次仍未补上才转人工

系统 SHALL 用独立的 `clarification_rounds` 阈值（默认 3）判定「反复追问」信号，
使对话的语义为「追问一次 → 带候选追问一次 → 第三次仍未补上才转人工」。表达
挫败度的 `frustration_rounds` 阈值 SHALL 保持原值，SHALL NOT 被澄清预算复用。
澄清计数 SHALL 在阈值处停止增长。

#### Scenario: 带候选的第二次追问不被转人工覆盖
- **WHEN** 会话第二次缺少订单号
- **THEN** 回复是客户能自助的候选追问，`handoff` 为空

#### Scenario: 第三次仍未补上才移交
- **WHEN** 会话第三次仍然缺少订单号
- **THEN** 触发 `repeated_clarification` 转人工，回复被交接文案替换

#### Scenario: 配合的客户不被计数惩罚
- **WHEN** 客户第一次补上订单号、第二次补上金额
- **THEN** 该请求按原意图创建任务，不因两次追问而转人工

### Requirement: 反复追问的移交必须带上已展示的候选

系统 SHALL 在因反复追问而转人工时，把本轮已向客户展示的订单候选写入上下文包的
`attempted_candidates`，字段与候选卡片一致，使坐席不必再问一遍。

#### Scenario: 坐席看到候选
- **WHEN** 因 `repeated_clarification` 生成的移交被坐席读取
- **THEN** `bundle.attempted_candidates` 列出这些订单号与状态
- **AND** 没有候选时该字段为空列表而不是缺失

### Requirement: 候选只在回复里出现，不进日志与其它渠道

系统 SHALL 把订单候选视为个人信息：它只出现在本次回复的卡片、建议与转人工
上下文包中。链路追踪 SHALL NOT 记录候选订单号，只允许记录候选数量。

#### Scenario: trace 里没有订单号
- **WHEN** 一次带候选的追问写入 trace
- **THEN** `support.reply` 的属性里只有候选数量，没有任何订单号字符串
- **AND** 回复文本与卡片仍是候选的唯一出口
