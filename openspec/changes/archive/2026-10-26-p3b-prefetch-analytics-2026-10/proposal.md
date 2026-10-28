## 为什么

P3-a 之后，统计问题只有一条路：问句 → `SqlPlan` → builder → guard → SQL。
数字是真的，但（1）明细 / 解释类追问（「哪三笔」「分别是什么」）只能拿到一个聚合数；
（2）每加一个维度都要改计划器与 builder，不支持的维度只能拒绝；
（3）模型即使参与表达，也拿不到「服务端已经查好、算好」的结构化事实。

P3-b 要证明的是：**LLM 只做语言理解与表达，查询范围、金额、权限、资格、统计一律由服务端确定。**
受控 SQL 不删除，而是与「按身份预取 + Python 预聚合 + 模型只表达」并存，按数据量与问题类型路由。

## 改什么

- 新增确定性 `AnalyticsRouter`：输出 `SQL` / `PREFETCH` / `REFUSED` 与可计数理由；不支持的维度仍拒绝。
- 新增按 `Identity` 预取的 `AnalyticsSnapshot`：count / sum / group_by 用 Python 在已过滤行上计算；超阈值时 L1 用既有受控 SQL 聚合，L2 只取最近 N 行并标明截断。
- 新增分层上下文：L1 聚合 + L2 最近明细（合计硬预算）注入表达路径；L3 全量明细永不进 prompt，只按 `query_id` 展开。
- 新增 `FactualityGuard`：模型回复里的每个阿拉伯数字必须能在事实中找到，否则丢弃句子并回退确定性模板。
- 预取路径在 `AGENT_MODE=pydantic_ai` 且开启表达时调用模型；`rules` / 模型不可用 / 开关关闭时只输出模板，零调用。
- 新增追问路径：同一会话内的「哪几笔 / 明细」按上一轮 `query_id` 返回 L3 明细卡片；快照过期要求重新提问。
- 新增配置 `ANALYTICS_PREFETCH_ENABLED`、`ANALYTICS_PREFETCH_MAX_ROWS`、`ANALYTICS_SNAPSHOT_TTL_SECONDS`、`ANALYTICS_NARRATION_ENABLED`。

## 不改什么

- 不放开自由 SQL，不让模型生成 SQL 或聚合代码；计划器、builder、guard 全部保留。
- 不改 `analytics` 卡片既有字段名；不改 61 条支持集、Golden 40 条与回放的判定口径。
- 不做跨进程共享的快照存储（本轮为进程内，带 TTL，作为已声明边界）。
- 不改 P3-a 的拒绝语义与 4 条既有测试。
