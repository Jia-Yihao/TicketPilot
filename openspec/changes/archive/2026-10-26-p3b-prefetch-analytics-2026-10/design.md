# P3-b 设计取舍

## 并存而不是替换

受控 SQL 继续负责可枚举维度（status / month / day）与超过阈值的统计；预取负责
明细 / 解释类问题和小数据量。两条路径都对同一问题给出相同的 count / sum / group_by，
这既是回归项，也是「聚合放在哪里不影响事实」的证明。

## 行数从哪来

路由里的行数由一次确定性 **scoped COUNT** 得到，不由模型估计，也不由样本行数推断。
同一问句 + 同一 `Identity` + 同一配置 → 同一路由。

## 超阈值时的预取

明细类问题即使超过阈值也走预取：L1 的 count / sum / group_by 用受控 SQL 聚合补齐，
L2 只取最近 `ANALYTICS_PREFETCH_MAX_ROWS` 行，并在上下文与卡片里明确写「只展开最近 N 条」。
纯统计超阈值则整体走受控 SQL，不构造混合快照。

## 快照存储

L3 明细只进进程内 `SnapshotStore`，键为 `query_id`，值绑定 `(tenant_id, user_id)` 与过期时间。
这样做的代价是：多 worker 下追问可能落到没有该快照的进程，此时按「快照过期 / 不存在」
要求重新提问，不返回旧数据。跨进程共享留到生产化批次（P7 边界）。

## 表达与校验

Prompt 固定包含一句约束；数字校验是独立代码，不依赖模型遵守提示词。
表达失败（模型异常 / 数字找不到来源）一律回退模板，事实与卡片不变，只降级措辞。

## 调用预算

预取表达路径只在 `agent_mode=pydantic_ai` 且 `ANALYTICS_NARRATION_ENABLED=true` 时调用一次模型；
`rules` 模式、开关关闭、SQL 模板路径都是零调用。调用成本写入既有 `llm_usage` 台账，source 为
`analytics`。
