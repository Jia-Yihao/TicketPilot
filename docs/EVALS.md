# 客服评测基线

评测分三层，都**离线、可重复、不依赖模型**：

| 层 | 数据 | 入口 | 回答的问题 |
|---|---|---|---|
| 任务路径 | `evals/golden_set.json`（40 条） | `python evals/run.py` | 建单 / 查询 / 退款 / 工单等写路径的终态、工具与回执对不对 |
| 只读路径 | `evals/support_set.json`（61 条） | `python evals/support_run.py` | 12 个意图的正常 / 边界 / 失败形态，多轮澄清能不能续跑 |
| 检索 | 同上数据集的 17 条标注 | `python evals/retrieval_metrics.py` | 换检索器前后 recall / MRR / nDCG 有没有退化；多通道对比 bm25 / hybrid / rerank |

另有两个闭环工具：`evals/replay.py` 读 trace 表重跑并 diff，`scripts/sample_traces.py` 按渠道×意图分层抽样产 bad case 队列。

## 数据集

`evals/support_set.json` 收录 **61 条用例**（34 normal / 20 boundary / 7 failure，12 个意图全覆盖，其中 12 条是多轮）。每条给出：

| 字段 | 含义 |
|---|---|
| `id` | 用例标识 |
| `kind` | `normal` / `boundary` / `failure`，用来保证三态都有样本 |
| `query` | 首轮用户原话（不写关键词堆砌） |
| `intent` | 期望的客服意图（自然语义，不是当前路由器的输出） |
| `policy_id` | 期望命中的政策文档；为空表示该用例只参与意图评测 |
| `sections` | 期望命中的章节，作为相关性标注 |
| `turns` | 逐轮消息与断言：意图、回复关键片段、澄清原因 / 缺失槽位、任务、转人工、必须出现的 span |

覆盖 4 份政策（退款 / 延迟配送 / 退货 / 工单），**17 条带标注的检索用例 + 44 条纯意图 / 多轮用例**。澄清类用例断言 `clarification.reason`（`MISSING_ORDER_ID` / `MISSING_AMOUNT` / `INVALID_AMOUNT`）、`expected_slot` 与 `resumable_intent`，并至少有一条断言续跑后 `support.classify.resumed` 为真。

`evals/golden_set.json` 收录 40 条任务路径用例（16 failure / 12 normal / 12 boundary），覆盖 6 个任务意图。UNKNOWN 没有 normal 形态——无法归类本身就是失败形态。其余 6 个只读意图由 `support_set.json` 覆盖；两个数据集的并集由 `tests/test_eval_datasets.py` 校验必须等于意图目录的 12 条。

## 指标

| 指标 | 定义 |
|---|---|
| `recall@k` | 命中的相关 chunk 占该用例全部相关 chunk 的比例（k = 1 / 3 / 5） |
| `mrr` | 第一个相关 chunk 排名倒数的平均 |
| `ndcg@5` | 前 5 名的排序折扣增益，理想排序为 1 |
| `intent_accuracy` | 意图路由命中期望意图的比例 |
| `stale_hits` | 命中已失效或不在有效期内的政策（必须为 0） |
| `cross_tenant_hits` | 命中其他租户的政策（必须为 0） |

相关性判定：chunk 所属文档的 `policy_id` 与用例一致，且 chunk 章节落在 `sections` 内。

## 执行

```bash
python evals/run.py                              # 40 条任务路径 + 轨迹断言
python evals/support_run.py                      # 61 条只读路径 + 多轮澄清
python evals/retrieval_metrics.py                # 检索指标（17 条标注，默认 bm25 门槛）
python evals/retrieval_metrics.py --channels bm25,hybrid,rerank   # 多通道对比
python evals/replay.py --self-check              # 录制后立即回放，要求 diff 为空
python scripts/sample_traces.py                  # 从已有库抽样 bad case（只读）
make eval / eval-support / eval-retrieval / eval-replay / sample-bad-cases
```

`--channels` 是**逗号分隔的对比列表，门槛只套在第一个渠道上**（JSON 里的 `gate_channel`），其余渠道只展示不参与失败判定；CI 跑的是 `bm25,hybrid`，所以 `GATE PASSED` 说的是 bm25。`rerank` 在没有 `RERANK_URL` 时不进对比表，而是在报告末尾打一行 `skipped: rerank: no RERANK_URL configured`。

`python evals/retrieval_metrics.py --json` 输出机器可读结果；`--database-url postgresql+psycopg://...` 可在 Postgres 上复跑。检索退出码 1 表示门槛渠道有指标低于 `support_set.json` 里的 `thresholds`。`tests/test_retrieval_eval.py` 用同一份数据在 `pytest` 里再跑一次，因此 `pytest` 也会卡住回归。

hybrid 通道要有向量才有意义。稠密通道读的是 `document_chunks.embedding`（JSON 列），本机没有 embedding 端点时用离线哈希替身自建：

```bash
python scripts/embed_policies.py --offline            # 首次写入哈希向量
python scripts/embed_policies.py --offline --rebuild  # 换维度 / 换语料后全量重嵌
```

`--rebuild` 是必需的而不是可选的：库里已有别种宽度的向量时，检索会直接报 `returned mixed dimensions`；同一空间但内容变了则报 `re-embed every chunk with --rebuild`，两种情况都不会拿旧向量凑数。

## 轨迹断言

任务路径不是只看回复文本，而是按 span 校验外部可观测的编排：

| 断言 | 失败信息前缀 | 例子 |
|---|---|---|
| 工具序列 | `trace.tools` | `expected ['get_order'], got []` |
| 工具参数 | `trace.get_order.params.order_id` | `expected 'ORD-DEMO-1001', got None` |
| 降级链 | `trace.degradation` / `trace.degradation.status` | `expected 'fallback', got none` |
| 回执来源 | `trace.receipt_from` | 回执字段必须来自声明的工具，而不是模板拼装 |
| 结论 | `result.outcome` | `expected VERIFIED_SUCCESS, got FAILED` |

失败信息直接指出字段与期望/实际值，不需要再回读 trace JSON。只读路径的 span 断言写在 `turns[].expect.spans`，校验「必须出现哪些 span」，不约束实现内部的调用顺序。

## 离线回放

`evals/replay.py` 读 `trace_spans` 与消息表，把录制的会话在新的种子库上重放，再逐轮比对：

- **结论变化**（`CONCLUSION`）：终态、意图、澄清原因、工具序列、卡片数量、回复摘要；
- **轨迹变化**（`TRAJECTORY`）：每一步检索命中数、文档 id、工具参数、降级状态、回执字段。

为保证 diff 只反映真实变化，比对前会归一化每次运行都会变的生成 id（`conv_` / `task_` / `rfd_` / `tkt_` / `TCK-` / `trc_` / `spn_` / `doc_`）与 ISO 时间戳。回放只覆盖**可复现**的会话：每轮都有 trace、无审批 / 转人工 / 对账 span、有 generate span、工具只允许 `analytics.query`。当前 61 条里 46 条可回放，15 条因转人工跳过，跳过原因会打印出来。

```bash
python evals/replay.py --self-check              # 自录自放，必须 REPLAY DIFF EMPTY
python evals/replay.py --database-url <url>      # 回放已有库
python evals/replay.py --trace-id trc_xxx        # 只回放一条会话
```

退出码：0 无 diff，1 有 diff，2 基础设施问题（数据源不通过、无可回放会话等）。

2026-10-24 两条路径都真跑过：`--self-check` 为 `61 recorded / 46 replayable / 15 skipped(has handoff span)` → `REPLAY DIFF EMPTY`；`--database-url` 指向本机演示库（跑过一轮渠道与记忆冒烟之后）为 `14 recorded / 13 replayable / 1 skipped(has handoff span)` → `REPLAY DIFF EMPTY`。注意 `--trace-id` 只有在**同时**给 `--database-url` 时才是在查已有库，否则会走 `--self-check` 的临时库并回一句 `not found or not replayable`——抽样结果里的 `replay.command` 因此把 URL 写成占位符，需要自己填。

**诚实边界**：回放使用新建的种子库，源库里已不存在的订单 / 政策会以 diff 形式暴露，而不是被静默跳过；转人工期间的坐席动作无法重放。

**记忆个性化对回放的影响（T9，2026-10-24 实测）**：回放目标库是**重新 seed 的库，里面没有客户档案**，而档案现在会改回复（称呼）与轨迹（`generate / memory.personalize` span）。所以"写档案期间录下的会话"重放必然出 diff——这不是回归，是 [ADR-018](adr/ADR-018-memory-in-answer-personalization.md) 明写的代价。实测：把 T9 在线真跑用的库（`.tmp/t9_replay.db`，`demo:alice` 在录制期间有档案）交给 `replay.py --database-url`，得到 `12 recorded / 12 replayable / 0 skipped` → `38 diff lines`，其中 9 段会话出 diff（10 个轮次标题行），全部是同一形状——8 条 `conclusion.reply_sha256`（录制回复多了称呼前缀）+ 20 条 span 位置错位（录制里有 `generate/memory.personalize`，无档案的重放里没有），没有一条落在检索命中、文档 id、工具参数或卡片数量上；没被写档案那几轮污染的 3 段会话仍逐字一致。反过来，`--self-check`（种子库，无人写过档案）仍是 `46 replayable / REPLAY DIFF EMPTY`，评测集里也没有任何用例调 `PUT /memory`，所以 **101 条评测与回放自检的"逐字不变"断言依然成立**。要复核某个真实客户的会话，得先把他的档案在同一份库里重建，否则 diff 只会把称呼与那个 span 报出来。

## 分层抽样

`scripts/sample_traces.py` 直接以只读方式读 span / task / handoff / 消息表，按**信号**打分后按 `渠道 × 意图` 分层配额输出 JSONL：

| 信号 | 触发条件 |
|---|---|
| `span_error` | 任一 span `status != OK` |
| `input_guard` | 输入护栏命中 |
| `clarification` | 本轮要求补槽 |
| `handoff` | 有转人工事件 |
| `no_evidence` | 检索无命中 / 无 citation |
| `unsupported_claim` | 输出护栏替换了无依据断言 |
| `task_failed` / `task_timeout` | 任务终态失败或超时 |
| `degraded` | 工具走了降级链 |
| `slow` | 延迟超过 `--slow-ms` |

每条候选带 `trace_id`、渠道、意图、信号、严重度、分数与回放命令占位符；消息与 span 属性在写出前已脱敏，`--output` 默认 `.tmp/bad_cases.jsonl`。

```bash
python scripts/sample_traces.py --tenant demo --since-minutes 1440 --per-stratum 3 --limit 100
```

渠道取值的优先级是 `_channel()`：先取移交包里的 `bundle["channel"]`，其次取任务 `context.channel`，两者都没有才回落成 `WEB`。2026-10-24 在同一份演示库上真跑了一次（`--since-minutes 60`，14 条 trace → 2 条 bad case）：

```
EMAIL/HUMAN_HANDOFF: 1/1     ← 邮件里说「转人工」，移交包带 channel=EMAIL
WEB/LOGISTICS_QUERY: 1/1     ← 同一条邮件会话（只是没触发转人工），回落成 WEB
```

**诚实边界**：同一条 EMAIL 会话在上面两行里出现了两个渠道——那次跑的是 T7-5 **之前**的库，`context` 里还没有渠道字段，所以没触发转人工的那条只能回落 `WEB`。T7-5 之后 `SupportAgent` 在路由之后把渠道写进任务 `context`，新数据的两条路径同源；**回落只发生在历史行上**，队列里仍没有「这条渠道从哪一级取到」的来源标记。抽样只产队列，不自动回灌数据集，也没有跨天去重。

## 当前基线（2026-10-24 实测）

门槛套在 `gate_channel = bm25` 上，8 项全过；hybrid 只做对比展示。分母是 17 条标注展开出的 68 条查询。

| 指标 | bm25 | 门槛 | hybrid(hashing) |
|---|---|---|---|
| `recall@1` | 0.7941 | ≥ 0.70 | 0.8529 |
| `recall@3` | 0.9118 | ≥ 0.85 | 0.9118 |
| `recall@5` | 0.9706 | ≥ 0.90 | **0.9118** |
| `mrr` | 0.8745 | ≥ 0.80 | 0.9020 |
| `ndcg@5` | 0.8824 | ≥ 0.80 | 0.8890 |
| `intent_accuracy` | 1.0000 | ≥ 0.55 | 1.0000 |
| `stale_hits` | 0 | 0 | 0 |
| `cross_tenant_hits` | 0 | 0 | 0 |

hybrid 把首名命中率抬了约 6 个点（0.7941 → 0.8529）、`mrr` 抬了约 2.7 个点，**但 `recall@5` 从 0.9706 掉到 0.9118**：RRF 融合让哈希替身的排序挤掉了原来能进前 5 的相关章节。报告只有渠道级汇总，没有逐条 diff，所以「哪几条在 top-5 退化」目前没有量化，只能定性承认。门槛仍按 bm25 的实测值设定，没有为了放行 hybrid 而调低，也没有为了刷分去调检索器。

**本节全部数字都是本机 SQLite 的读数。** 上一轮曾在 PostgreSQL 16 上复跑过 `bm25` 一列且与 SQLite 一致，但那是更早的标注规模；本轮收尾没有再起 PostgreSQL 容器（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的），所以**当前这份 17 标注 × 68 查询的基线（含 bm25 列）没有两库一致性证据**。`hybrid(hashing)` 那一列从一开始就只在本机 SQLite 上测过，没在 Postgres 复跑，也没在真实 embedding 端点上测过。要补这一条，用 `python evals/retrieval_metrics.py --channels bm25,hybrid --database-url postgresql+psycopg://...`（见「执行」一节）。

门槛是**当前基线下的回归护栏**，不是目标值。改进检索器或路由表之后应当同步抬高门槛。

## 这份基线暴露的问题

### 1. 排序：4 条用例的正确答案进不了前两名

| 用例 | recall@1 | recall@3 | 现象 |
|---|---|---|---|
| `refund-scope` | 0.00 | 0.00 | 问「哪些订单可以退款」，`退款条件` 排在 `适用范围` 之前，相关章节连 top-3 都进不去 |
| `delay-handling` | 0.00 | 1.00 | 问「怎么处理」，`延迟判定` 压过 `处理方式` |
| `ticket-priority` | 0.00 | 1.00 | 问「最高优先级」，`创建规则` 压过 `优先级定义` |
| `refund-window` | 0.50 | 0.50 | 首个命中正确，但另一个相关章节被排到 top-3 之外 |

BM25 按词面匹配，章节标题与提问用词不一致时，相邻但语义更近的章节会先命中。要拿到更高 `recall@1`，需要更强的语义匹配（稠密向量 / 混合检索）——这条通道已经具备（`app/retrieval/dense.py`、`fusion.py`、`rerank.py`）并且能跑出数字，但**跑分用的是离线哈希替身**（`scripts/embed_policies.py --offline`），不是真语义向量；`rerank` 通道要 `RERANK_URL` 才进对比。所以这一节的结论是「通道通了、方向对了」，不是「四条用例已被解决」：换成真实 embedding 之后 recall@5 的退化有可能反过来，数字要重测。

### 2. 路由：疑问句门控之后不再是短板

早期版本 `intent_accuracy` 只有 0.609：9 条政策类问法被判成 `REFUND_REQUEST` / `TICKET_CREATE` / `DELIVERY_ISSUE`，因为 cue 表只认动作词。加上**疑问句门控 + 政策主题优先**（`app/agent/routing.py`）之后，61 条用例的意图全部命中，`intent_accuracy` 升到 1.000，`routing_gaps` 为空。

这一条同时抬高了 `recall@1`：路由判对之后，取的才是政策检索通道而不是工单/物流通道。

## 控制台 Trace 回放与运营指标口径

控制台的 Trace 回放与运营看板只读已落库的数据，不在查看时重新执行任何工具或 SQL。

- **span 树**：以 `parent_span_id` 建树；父 span 未落库的孤儿 span 提升为根，不会被隐藏。同级按 `started_at` 排序。
- **缩进与耗时条**：缩进只反映真实父子关系；耗时条按 `latency_ms` 与本次 trace 的最大值归一化。
- **脱敏**：attributes 在写入 span 前已做 PII 掩码；回放页不提供「展开原文」。
- **span 指标**：`GET /api/v1/traces/metrics` 按 `started_at` 过滤窗口；`error_rate` 是 **span 级**错误率（`status != "OK"`）；`p95_latency_ms` 用最近秩取整；`trace_count` 是窗口内不同 `trace_id` 的数量。
- **转人工指标**：`window_minutes` 下，会话按 `updated_at`、转人工 / 放弃事件按 `created_at` 落在窗口内统计；`open_cases` 同样只数窗口内案件。空分母的比率显示 `—`，不显示 0。
- **统计卡片**：台账保存 SQL 文本、参数摘要、行数、耗时与状态，不保存结果行；卡片不重放查询。

**T1 验收记录（2026-10-24）**：在临时 SQLite 库上以坐席身份完成接管 → 交还 → 再次接管 → 结案（`FOLLOW_UP`）；运营看板六项指标、时段切换与空态 `—` 正常；Trace 回放验证了缩进、耗时条与 attributes 展开；客户身份访问 `/agent`、`/ops` 均被角色门禁拒绝；390px 下四个页面 `scrollWidth == innerWidth == 390`。`npm run build`、ruff、mypy、pytest（当时 322 passed / 3 skipped）通过；T4/T4b/T5/T6 完成后全量为 382 passed / 3 skipped（收集 385）；T7 完成后（含 17 条出站投递用例）为 402 passed / 3 skipped（收集 405，31 个测试文件）；T8 把两条 web 入口收进同一道幂等闸、再加 6 条跨入口用例后为 **408 passed / 3 skipped（收集 411）**；T9 加 18 条记忆个性化用例后为 **426 passed / 3 skipped（收集 429、32 个测试文件）**；T10 加 21 条规则编辑用例后为 **447 passed / 3 skipped（收集 450、33 个测试文件）**；2026-10-25 先把「别人的 `order_id`」那条作用域事实钉成 1 条用例（448 / 收集 451），同日再把模型边界的三条事实钉成 `tests/test_llm_gap.py` 4 条（452 / 收集 455），同一日「对话入口接通模型」轮把它扩到 7 条（覆盖 `rules` 模式一次不问、问句不花调用、采纳退款后仍等审批且来源为 `pydantic_ai`），现为 **455 passed / 3 skipped（收集 458、34 个测试文件）**。评测集本身不受 T9 / T10 影响：`evals/` 里没有任何用例调 `PUT /memory`，也没有一条碰 `memory` / `proactive` / `rules`（`grep` 可核对，零匹配），种子库不建档案、种子规则文案不变，所以 `40/40` + `61/61` + `REPLAY DIFF EMPTY` 三条读数与 T8 时逐字相同。

## 诚实边界

- 标注由项目自己编写，没有真实用户问法样本，可能低估长尾难度；
- 相关性只到「章节」粒度，同章节内部不再细分；
- 只评政策检索，不评商品检索与订单字段问答；
- 检索语料只有 5 份政策共 17 个 chunk（过期的 v2 占 2 个，不参与检索），BM25 的统计量在真实语料上会更稳，这里的小样本数字不能直接外推；
- `intent_accuracy = 1.000` 是在 61 条自写用例上得到的，**不等于真实用户问法的准确率**；新问法进来应先加进 `support_set.json` 再改路由。
- 轨迹断言只约束「必须出现哪些 span / 工具 / 参数」，不校验内部实现细节；实现可以重构，只要外部可观测行为不变。
- 回放只覆盖无审批、无转人工的只读会话；写路径（退款、建单）靠 Golden 用例而不是回放兜底。
- **T4 渠道、T5 记忆与主动服务、T10 规则编辑没有进评测集**。它们有 `pytest`（渠道 20 条、记忆 21 条、规则编辑 21 条）和真跑冒烟（本机起服务 + 74 / 59 / 52 条断言全过），但 `golden_set.json` / `support_set.json` 里没有「同一句话从邮件进来会怎样」「客户写了偏好之后回答会不会变」「改了退款模板之后通知正文是什么」这类用例——理由分三种：T4 的渠道差异发生在渲染层（认知路径相同，写进评测集只会得到同一个答案的多份副本）；T9 之后答案**确实**会随档案变化，所以这类用例必须先固定一份档案快照才可断言（[ADR-018](adr/ADR-018-memory-in-answer-personalization.md)）；T10 的规则改动**只影响下一条通知**、不回溯已生成的通知，要断言它就得同时断言「旧通知逐字不变」，这属于写路径而不是认知路径，评测集按定义不收（写路径由 Golden 用例与专项 `pytest` 兜底）。所以这三条不是「测不到」，是**口径上不归评测集管**——把未进集的东西说成测过是不诚实的。
- 评测跑的是**规则优先**的判定路径：本机没有模型 API Key，`LLM_MODE` 保持默认，槽位抽取回落到规则表。所以这套数字描述的是「无模型时系统能守住什么」，不是模型加持后的上限。
- `hybrid(hashing)` 的向量来自 `hashing` 替身，维度与语义都是人造的；这一列的绝对值不可与任何真实 embedding 的结果比较，只能与同一次运行里的 bm25 相对比。
