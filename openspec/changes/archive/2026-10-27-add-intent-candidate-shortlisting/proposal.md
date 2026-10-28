# P8 意图识别候选化

## 为什么

模型现在直接给结论：`IntentOutput.intent` 是 6 个字面量，而意图目录有 12 行
（`HUMAN_HANDOFF` / `SELF_ANALYTICS` / `REFUND_STATUS` / `DELIVERY_ISSUE` /
`PRODUCT_QUERY` 等规则读得到的行，模型根本没机会选）；反过来，模型选中字面量里的
某一个时，服务端**没有任何证据**说明它为什么这么选。`confidence` 是一个死字段——
写进 trace 却没有人消费，这就是 NEXT_STEPS 的 F-3「读了但没证据」的中间态。

更糟的是采纳面与输出面是脱节的：模型可以说任何 6 个之一，服务端只认退款读数，其余
丢弃。丢弃是对的（写动作不能让模型开门），但「能说什么」和「能触发什么」之间缺一层
服务端算出来的**候选集**，于是既没法判断模型是否越界，也没法解释某一次采纳为什么可信。

## 改什么

1. 新增确定性候选内核 `app/agent/candidates.py`：`build_candidates(message, context, route,
   catalog=…, order_state=…)` 用**封闭的五类证据**（目录 cue / 规则读数 / 真实存在的槽位 /
   会话上下文 / 订单状态）算出候选集；`HUMAN_HANDOFF` 与 `UNKNOWN` 无条件在集合里；
   只要有任何一行拿到内容证据，没有内容证据的普通意图行就不得进候选。**一行依据都没有时
   不收窄**：把整份目录的行名（不含行定义）交给模型，并如实标 `narrowed=false`——窄到只剩
   两行兜底等于没给模型选择余地，而这类句子正是模型补位的场合。
2. 每个候选带一个**服务端信度桶**（`high` / `medium` / `low`），按证据的**种类数**判；
   模型自报的 `confidence` 不参与桶计算，也不参与任何判定。
3. 模型面收窄：prompt 只注入 L1（候选 `intent_id` + 证据串，约 100 token），完整 12 行目录
   永不注入；`IntentOutput.intent` 改为 `str`，`NONE` 是合法回答。
4. 越界固定处理：`intent ∉ 候选 ∧ intent ≠ NONE` → **不重试**、回退规则读数，并在成本记录里
   带 `model_reason="model_out_of_candidates"`（`NONE` 记 `model_none`）。
5. 新配置 `INTENT_CANDIDATES_ENABLED`（默认 `true`）与 `INTENT_CANDIDATES_MAX`（默认 6），
   关掉后模型面回到本轮之前的行为。

## 不改什么

- **不扩大采纳面**：对话入口仍只采纳 `REFUND_REQUEST` 读数，其余丢弃；`TICKET_CREATE` 等
  不由模型触发；写路径仍由规则门 + 必填槽位 + 审批决定。
- 不动 `AGENT_MODE=rules`：规则模式在算候选之前就返回，零调用、零新增 trace 字段，
  回复与本轮之前逐字一致（回放自检必须继续 `REPLAY DIFF EMPTY`）。
- 不把 `confidence` 变成决策输入，也不把模型输出当 gold。
- 不引入 embedding / 相似度做候选召回，不用 LLM 生成候选。
- 不改幂等键、`ModelDecision` 的 `digest` 口径与 P2 的复用条件。
- 本轮不把 L2 定义注入到答案路径（只提供 `describe_candidates()`，不调用即不进 prompt）。

## 验收

- 同一条消息 + 同一份上下文两次算出的候选集逐字一致（含顺序）。
- 假模型给出候选外的意图：回退规则读数、只发一次请求、`model_reason` 是
  `model_out_of_candidates`；给 `NONE`：不采纳、不建任务。
- 假模型自报 `confidence=0.99` 但证据只有一种槽位：桶仍是 `low`——证明桶不读模型说的数。
- `AGENT_MODE=rules` 下候选内核不被调用、模型桩件一次不响。
- `INTENT_CANDIDATES_ENABLED=false`：不注入候选、不产生 `model_reason`，合法字面量的读数与来源
  标记与本轮之前一致；「字面量之外的回答」变成「答了但不采纳」（路由结果仍为规则读数），这一
  差异如实写进 ADR，不宣称逐字一致。
- P1 语料（346 条）上报候选覆盖率、平均候选数与桶分布，`external` / `authored_gap` 分层；
  11 句探针与 62 条支持集不退化。

## 边界（写进文档，不许含糊）

- 桶不是概率，未校准前**不设阈值**；`medium` / `low` 的实际含义本轮不下结论。
- 越界率需要真实模型 A/B 读数；没有读数时报告必须写「未测」，不得用假模型模拟。
- 候选化不解决「规则读不到」本身，它解决的是「模型说了但服务端不知道为什么」。
