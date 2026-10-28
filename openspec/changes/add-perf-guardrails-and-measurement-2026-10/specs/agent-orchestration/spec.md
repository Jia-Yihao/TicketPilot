# agent-orchestration 规格增量

## ADDED Requirements

### Requirement: 模型调用前预算刹车
系统 SHALL 在发起模型调用**之前**做一次预算检查（读 `llm_usage` 同表的窗口聚合，跨 worker
口径不变——所有进程读同一张表，禁止进程内计数总线）。总开关默认**关**；关闭时行为与
现状逐字等价（零新增查询、零新增 trace 字段）。开关打开且判定越预算时，系统 SHALL
跳过模型调用并降级为确定性读数：只读问答案路径回落规则读数，退款等写动作路径按缺信息
形态追问或转人工——**永不**因刹车放行任何写动作，也永不放宽 `ModelDecision.verifies()`
六条件与 `IntentOutput` 契约。

#### Scenario: 默认关闭等价
- **WHEN** 预算开关保持默认关闭
- **THEN** 每次对话的 SQL 查询集合与 trace 字段集合与本轮之前逐字一致（回归断言）

#### Scenario: 越预算只降级不放行
- **WHEN** 开关打开且当前租户窗口用量超过上限
- **THEN** 本轮不发生模型请求（假模型驱动器断言调用次数为 0），trace 记固定原因码
  `budget_skipped`；若本轮规划意图是写动作，则回复为追问/转人工形态，不建退款任务

#### Scenario: 刹车不削弱既有闸
- **WHEN** 开关打开且预算未越限
- **THEN** 「单轮写动作 1 次模型调用」读数、审批闸门与工具 `requires_approval` 校验
  均与开关关闭时一致（回归断言）

### Requirement: 收窄与非收窄的 token/延迟 A/B 读数
`bench/` SHALL 产出「候选收窄集（`narrowed=true`）」与「无信号集（`narrowed=false`）」
两侧的**输入 token 与延迟**对照读数，两侧分母分别报告、不得合并；含模型延迟的读数
必须带 reasoning-tokens 指纹口径。该任务是测量交付，不改任何生产判定。

#### Scenario: 两个分母分别成表
- **WHEN** 查看 A/B 模块的 `baseline.json`
- **THEN** 收窄集与无信号集各有独立的 token 均值/分位与延迟分位，且各带样本数
