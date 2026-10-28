# P3：不支持的维度必须拒绝，不得回落 status

## 为什么改

`_GROUP_CUES` 把「分布」直接映射到 `status`，`_group_by()` 只认
`status` / `month` / `day`。因此「我的退款按商品分布」被规划成
`group_by=status`：SQL 与数字都是真的，答的却是另一个问题。现有的
`NO_PLAN → no_evidence → handoff` 就是「不猜」路径，缺的是让不支持的维度走进去。

## 改什么

1. 计划器先识别「按商品 / 按品类 / 按渠道 / 各品牌 / 型号分布」等**分组维度**；
   维度不在白名单时返回拒绝原因 `UNSUPPORTED_DIMENSION`，不产生计划。
2. 拒绝原因写进 `sql_queries.rejected_reason`，与「认不出数据集」的 `NO_PLAN`
   分开；两者不能埋在同一个桶里。
3. 对话层沿用既有拒绝回复与 `no_evidence` 转人工，真的建一条 handoff；
   回复里不出现状态桶。
4. 支持的状态 / 月 / 日维度逐条不退化。

## 不做

- 不扩 `_GROUP_CUES`、不放开自由 SQL；修复方向是缩小承诺。
- 不在回复文本里假装转人工；移交必须走现有状态机。
