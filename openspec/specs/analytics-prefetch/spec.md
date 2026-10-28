# Analytics Prefetch

## Purpose

数据量小或问题要明细时，服务端先按调用者身份把事实查好、确定性预聚合，模型只负责把它说成人话；
数字必须能在服务端事实里逐条追溯，追问按快照 `query_id` 展开，不重新查库也不重新问模型。

## Requirements

### Requirement: 路由必须是确定性的

系统 SHALL 仅根据问句形状、`SqlPlan`、一次按 `Identity` 过滤的 `COUNT` 与配置选择
`SQL` / `PREFETCH` / `REFUSED`，并给出稳定理由码；同一输入 MUST 得到同一路由与理由码。

#### Scenario: 不支持的维度保持拒绝

- **WHEN** 计划器返回 `UNSUPPORTED_DIMENSION`
- **THEN** 路由为 `REFUSED`，理由码为 `UNSUPPORTED_DIMENSION`，不进入预取路径

#### Scenario: 关闭预取回到 P3-a

- **WHEN** `ANALYTICS_PREFETCH_ENABLED=false`
- **THEN** 所有可计划的问题路由为 `SQL`，理由码为 `PREFETCH_DISABLED`

#### Scenario: 明细类问题走预取

- **WHEN** 问句包含「哪几笔」「明细」「展开」等明细线索
- **THEN** 路由为 `PREFETCH`，理由码为 `DETAIL_REQUEST`

#### Scenario: 可枚举维度与超阈值留在 SQL

- **WHEN** `group_by` 为 `status` / `month` / `day`，或 `row_count` 超过阈值
- **THEN** 路由为 `SQL`，理由码分别为 `ENUMERABLE_DIMENSION` / `ROW_COUNT_OVER_THRESHOLD`

### Requirement: 预取事实只属于当前身份

预取的每条语句 MUST 带 `tenant_id` 与 `user_id`；问句 MUST NOT 决定查询范围。
跨租户或跨用户的行 MUST 查不到。

#### Scenario: 另一个客户的数据不可见

- **WHEN** 同一问句由另一个 `Identity` 执行
- **THEN** 只统计该身份自己的行，跨身份快照也 MUST NOT 被展开

### Requirement: 两条路径的聚合逐位一致

同一问句、同一身份、同一时间窗下，预取路径与受控 SQL 路径的 count / sum / group_by
MUST 逐位一致。

#### Scenario: 分组统计一致

- **WHEN** 同一分组问题分别走 `SQL` 与 `PREFETCH`
- **THEN** 桶、顺序、笔数与金额完全相同

### Requirement: 分层上下文

L1 聚合与 L2 最近摘要 MAY 进入表达 prompt，且共享硬字符预算；L3 完整明细 MUST NOT
进入 prompt。裁剪 MUST 是确定性的。

#### Scenario: 超阈值只展开最近 N 条

- **WHEN** 明细类问题命中行数超过 `ANALYTICS_PREFETCH_MAX_ROWS`
- **THEN** L1 仍是全量聚合，L2 只含最近 N 条，并明确标注截断

### Requirement: 模型只表达，数字必须可追溯

当且仅当 `AGENT_MODE=pydantic_ai` 且 `ANALYTICS_NARRATION_ENABLED=true` 时，预取路径
MAY 调用一次模型；`rules` 模式、开关关闭与 SQL 模板路径 MUST 为零调用。
模型回复中的每个数字 MUST 能在事实集合中找到；否则 MUST 丢弃模型句子并回退模板，
且卡片与事实不变。

#### Scenario: 编造数字被拦截

- **WHEN** 模型回复包含事实中不存在的金额或笔数
- **THEN** 回复使用确定性模板，`analytics.faithfulness` 记录 `FAILED` 与未命中数字

### Requirement: 追问按 query_id 展开

同一会话的明细追问 MUST 使用上一轮 `query_id` 返回 `analytics_detail` 卡片，且 MUST NOT
再次调用模型或重新执行 SQL；快照不存在、过期或跨身份时 MUST 要求重新提问。

#### Scenario: 过期快照

- **WHEN** 追问引用的快照已过期或不存在
- **THEN** 返回要求重新提出统计问题的回复，不返回旧明细
