# Agent Orchestration

## Purpose

在对话入口与任务台账之间传递模型结论时，既避免重复付费，又不让入口凭一条记录控制台账：
台账独立复核通过才复用，任何一项不一致就重算。

## Requirements

### Requirement: 入口模型结论只能作为待复核记录

系统 SHALL 把对话入口的模型结论表示为不可变的决策记录，包含请求与上下文摘要、
规则指纹、模型名、入口当次的规则读数、模型读数与用量；记录 SHALL NOT 写入
任务请求字段，也 SHALL NOT 被台账直接当作事实。

#### Scenario: 记录与当前请求一致才复用
- **WHEN** 台账收到一条决策记录，其摘要、规则指纹、模型名与当前规则的意图/订单号/金额逐项一致
- **THEN** 台账复用记录中的模型读数，不再次调用模型
- **AND** `task.routed.planning_source` 仍由台账写成 `pydantic_ai`
- **AND** 记录的模型轨迹带 `model_reused=true`

#### Scenario: 任何一项不一致就重算
- **WHEN** 摘要、规则指纹、模型名或规则读数与当前状态不一致
- **THEN** 台账丢弃该记录并按原路径重新规划
- **AND** 重新规划若调用模型，`llm_usage` 如实新增一行

### Requirement: 一次对话只有一次模型调用与一行用量

系统 SHALL 在成功路径上让一段端到端退款对话产生且只产生一次模型调用与一行
`llm_usage`。

#### Scenario: 成功退款只付一次
- **WHEN** 入口在规则缺口上采纳退款意图，随后创建任务
- **THEN** 模型只被调用一次，`llm_usage` 只有 `source=chat` 一行
- **AND** 任务终态与不复用时一致（等待人工审批）

### Requirement: 规则模式不产生调用

系统 SHALL 在 `AGENT_MODE=rules` 下保持零模型调用。

#### Scenario: rules 模式
- **WHEN** 以 `AGENT_MODE=rules` 跑同一段退款对话
- **THEN** 模型调用次数为 0，`llm_usage` 为空
