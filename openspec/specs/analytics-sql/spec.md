# Analytics SQL

## Purpose

用受控查询回答调用者自己的结构化统计；分组维度不在支持集合时明确拒绝并转人工，
不把问题答成另一个问题。

## Requirements

### Requirement: 不支持的分组维度必须拒绝

系统 SHALL 只在分组维度属于受支持集合（`status` / `month` / `day`）时生成计划；
识别到「按商品 / 按品类 / 按渠道」等不支持的维度时 SHALL 返回 `None`，
并以 `UNSUPPORTED_DIMENSION` 写入执行台账，不得回落到 `status`。

#### Scenario: 按商品分布
- **WHEN** 用户问「我的退款按商品分布」
- **THEN** 计划器不产生计划，拒绝原因为 `UNSUPPORTED_DIMENSION`
- **AND** 回复不包含「已受理」「已到账」等状态桶
- **AND** 走既有 `no_evidence` 路径建一条转人工移交

#### Scenario: 支持的状态维度不变
- **WHEN** 用户问「按状态统计我的订单笔数」
- **THEN** 计划仍为 `group_by=status`，结果与改动前一致

### Requirement: 拒绝原因可区分

系统 SHALL 把「没认出数据集」与「认出了但不支持这个维度」写成不同
`rejected_reason`。

#### Scenario: 台账可度量
- **WHEN** 分别问「今天天气怎么样」与「我的退款按商品分布」
- **THEN** 台账两行分别为 `NO_PLAN` 与 `UNSUPPORTED_DIMENSION`
