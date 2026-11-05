# TicketPilot

**售后 Agent 的边界设计：模型可以读，不可以授权；答不了就转人工。**

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Golden Set](https://img.shields.io/badge/Golden%20Set-40%2F40-146b5c)](#怎么验证这些说法)
[![Support Set](https://img.shields.io/badge/Support%20Set-62%2F62-146b5c)](#怎么验证这些说法)
[![License](https://img.shields.io/badge/License-MIT-0e1b2c)](LICENSE)

**40/40 Golden · 62/62 Support · 收集 1382 条用例 · 50 条 API 路径 · 39 篇 ADR · 模型改变写路径路由 0/16**

覆盖商品咨询、政策问答、订单与物流查询、工单创建、退款申请与按金额分层的审批（未达阈值走自动执行，
仍过同一套资格、台账与对账），外加一类受控的 Text-to-SQL 自助统计。主链路在本机就能跑完，不需要任何云 API。

![售后对话控制台：本地客服 Agent、紧凑卡片与按需展开的章节](docs/assets/screenshots/conversation.png)

## 快速开始：3 分钟跑起来

```bash
python -m venv .venv && source .venv/Scripts/activate   # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/seed.py                    # 建表 + 演示数据
uvicorn app.asgi:app --port 8000          # 另开一个终端
python scripts/refund_flow.py             # 退款写路径：资格 → 待审批 → 批准 → 回执
```

`seed.py` 输出一行计数：

```text
seed complete: {'orders': 4, 'policies_created': 5, 'rules': 3}
```

`refund_flow.py` 是最能说明设计主张的一段。客户说退款，服务端先做资格校验，然后**停在人工审批**；
坐席批准之后才执行，回执引用权威退款记录；最后同一句话再问一次，会被数据库里的可退余额拦住——
拦住它的是规则，不是模型记不记得：

```text
> 客户（demo/alice）：我要申请退款，订单 ORD-DEMO-1001，金额 129 元
1. 提交退款请求    109.0 ms  HTTP 200
    intent=REFUND_REQUEST  task=WAITING_APPROVAL
2. 坐席看待审批队列     24.8 ms  HTTP 200
    1 条：apr_fff2fe49115349fc 12900 分
3. 坐席批准     32.0 ms  HTTP 200
    task=SUCCEEDED  outcome=VERIFIED_SUCCESS
    回执：退款 129.00 CNY 已由权威退款记录确认，退款单号 rfd_8e0e93b3e2。
4. 任务事件时间线      4.4 ms  HTTP 200
    task.created -> task.running -> task.routed -> tool.call_started -> tool.call_completed
      -> approval.requested -> approval.approved -> task.succeeded
5. 客户重复申请     32.7 ms  HTTP 200
    task=SUCCEEDED  outcome=FAILED
    客服：…该订单当前不符合退款条件（NO_REFUNDABLE_BALANCE、ALREADY_REFUNDED、AMOUNT_EXCEEDS_REFUNDABLE），未创建退款申请。
```

第 3 步与第 5 步放在一起看，就是整个项目的形状：一次任务可以 `SUCCEEDED` 而结果是 `FAILED`——
拒绝被如实记录，而不是伪装成系统错误。

要看只读问答、渐进式披露、政策引用，以及规则读不懂的句子在对话里得到什么，命令都在
[docs/guide/快速开始.md](docs/guide/快速开始.md) 与 [docs/guide/证据索引.md](docs/guide/证据索引.md)。
全部文档按「叙事 / 使用 / 契约 / 记录」四卷分工，入口见 [docs/文档地图.md](docs/文档地图.md)。

## 它是什么，不是什么

这是一个作品集项目。它想回答的不是"怎么把 Agent 做得更聪明"，而是三个更难的问题：

| 问题 | 我的答案 | 最小证据 |
|---|---|---|
| 模型到底被允许做什么？ | 读语言、抽槽位；资格、金额、审批、幂等、对账全部归服务端 | 16 个写路径轮，模型改变路由 **0** 个；规则读不懂的 11 句，它捞回 6 句，但最多只能把退款送进待审队列 |
| "我很诚实"能被检查吗？ | 把说法变成断言：评测走产品接口，事实由代码现算，守卫自己也要被负验证 | 收集 1382 条、2026-10-28 实测 1074 passed / 6 skipped / 0 failed；18 项事实基线；6 种故意破坏全部被守卫抓到 |
| 什么时候不该回答？ | 没有依据就拒：无计划不查询，读不懂就追问，有风险就转人工 | 拒绝矩阵 10 条；写不出计划即转人工；模拟流量里转人工率 19/52 |

完整读数、反例与代价在 [docs/guide/证据索引.md](docs/guide/证据索引.md)。这份 README 只放论点、最小证据，
以及我如果重来会改的地方。

**是**一个可运行的售后 Agent 演示：确定性工作流骨架，加上被限制在"填空"位置的语言模型。
只读问答不产生副作用；退款与工单必须经过服务端规则、四眼审批和幂等执行；模型可以替换、
可以离线，资金相关的承诺不依赖它的输出。

**不是**一个能直接上线的退款系统：身份是开发 Header，退款渠道是 mock，生产配置会拒绝启动
（刻意的 fail-closed）。也**不是**多 Agent 自主规划——这里没有"让 Agent 自己决定要不要退款"，
只有一个被审得很紧的写入通道。

---

## 一、模型可以读，但不能授权

售后场景里最贵的错误不是答错一句话，而是**在不确定的情况下把钱退出去**。所以这里把两条路径
彻底分开，而不是让一个 Agent 同时负责解释和转账：

| 路径 | 处理方式 | 失败时的代价 |
|---|---|---|
| 只读问答（商品、政策、订单、物流、统计） | 本地 `SupportAgent` + 紧凑上下文卡片 | 答错可以重问，不产生副作用 |
| 写操作（退款、工单） | `TaskRuntime` 确定性工作流：资格 → 审批 → 台账 → 对账 | 结果未知时进入 `PENDING_CONFIRMATION`，由对账收敛，绝不盲目重试 |

模型能碰到写路径的地方只有三道门，每一道单独可测：

| 门 | 位置 | 判什么 | 模型的结论失效时 |
|---|---|---|---|
| 入口门 | `SupportAgent._consult_model` | 只在规则 `UNKNOWN`（或只靠订单号回了 `ORDER_QUERY`）、消息是动作句、`AGENT_MODE=pydantic_ai` 时才问；问之前服务端先算一份候选集（ADR-034），只采纳退款意图与订单号 | 不问，按规则回答或追问 |
| 台账门 | `TaskRuntime._run` → `brain.plan` | 每个任务**重新**规划一次，来源写进 `task.routed`，对话入口的结论不被递进来 | 台账自己算，入口写错最多多追问一轮 |
| 执行门 | 资格 / 审批 / `action_digest` / 订单版本 / 幂等 claim | 钱能不能出去 | 停在审批或 `FAILED`，钱不动 |

模型的输出类型是 `IntentOutput`：`intent` 是 `str`（自 P8 起——答案空间由服务端候选决定，
不再是六个字面量的枚举），两个槽位字段带 pydantic 校验，
`UsageLimits(request_limit=2)` 加 20 秒超时。**它产不出动作，只能产三个字段。**

### 证据

| 想验证 | 怎么测 | 读数 | 代价 |
|---|---|---|---|
| 模型会不会改掉规则已读懂的写路径 | `scripts/llm_boundary_diff.py`，16 个写路径轮 | 改变 **0/16** | 16 次调用 / 9506 token / 95.6 s |
| 规则读不懂时它能不能捞回 | `scripts/llm_gap_probe.py`，11 句词表外说法 | 规则 10/11 `UNKNOWN`；模型对上标签 **6/11**；金额补位 2/2；编造订单号 0 | 11 次调用 / 7303 token / 83.0 s |
| 接进对话入口后呢 | 同 11 句走 `--chat --chat-model` | 意图改变 **5/11**；6 句退款全部进追问；单轮写动作 **0** | 8 次调用 / 4941 token / 48.9 s |
| 补齐槽位后钱会动吗 | `--chat-task` 端到端 | 停在 `WAITING_APPROVAL`，`approval_amount=12900`，`planning_source=pydantic_ai` | 单轮写动作 1 次调用 / 1 行用量；两轮澄清仍 2 次 |
| 模型给错形状时 | `@agent.output_validator` + `ModelRetry`，重试次数落进 `ModelTrace` / span / `llm_usage` | 30 次运行：重试 0、失败 0；金额 9/9、订单号 12/12 | 离线脚本，不进 CI |
| 服务端递给模型的候选集长什么样 | `evals/intent_candidates.py`（离线、确定性，346 条语料） | `external` 收窄 54.73% / 覆盖 67.27%，`authored_gap` 44.83% / 55.38%；**无信号集（规则与 cue 同时失明）占 45.27% / 55.17%，此时不收窄、上限不生效**；上限一次未触发；模型侧 12 条样本越界 **0**、`NONE` 2、改道 3 | 离线 0 次调用；`--with-model` 12 句 × 2 遍 |

这六行的读法比数字本身重要：

- **`0/16` 不能读成"模型没用"**。那 16 句是我按规则词表写的，规则一个空都没留，模型没有机会。
  它只证明"在这份流量上，模型没有改变任何路由"，不证明"换成真实说法也不会"。
- **`6/11` 也不能读成"模型能接住六成"**。那 11 句还是我写的；转人工的三种说法它一句都没接住，
  「运费谁出？」还被判成 `LOGISTICS_QUERY`（错判的代价是答非所问，不是无害）。真实工单语料
  仍然是缺口。
- **接进对话入口后，模型只把请求送到队列门口**。它让 5 句的意图变了，让 6 句退款进入追问，
  但单轮任务仍是 0——因为槽位没满。它改的是"这句话算什么"，不是"钱能不能出去"。
- **台账复核入口的读数，而不是重算**。入口留下的是内存里的 `ModelDecision`（请求摘要 +
  规则集指纹 + 模型名 + 规则读数 + 模型读数 + 用量），台账逐项验证一致才复用，任何一项
  不同就重新调用。单轮写动作因此 **1 次调用 / 1 行 `llm_usage`**；同请求 A/B 从
  **2 次 / 1234 token / 13.63 s** 降到 **1 次 / 620 token / 6.97 s**（A/B 是另一次对照运行；
  单轮块的 640 token 是另一轮，绝对值随生成长度浮动，两者不要求相等）。两轮澄清仍各付一次
  （两轮的请求摘要不同，不能复用），这是刻意保留的边界，见
  [ADR-030](docs/adr/ADR-030-决策复用.md)。
- **没有信度阈值**。`IntentOutput` 里有 `confidence`，但没有一份真实语料能告诉我阈值该是多少，
  拍一个数只是把"我选的"伪装成"算出来的"。P8 之后服务端会算一个**桶**（这行候选背后有几种
  证据），它是唯一被承认的信度口径；但同样**没有阈值**——桶只描述，不判定。
- **候选化的读数是"形状"，不是"效果"**。收窄集里 gold 覆盖率 67.27% 说明服务端确实知道得更多，
  而 12 条模型样本里"越界 0"是靠模型自己答兜底行做到的：gold 已经在候选里的 3 句，它 3 句都
  答了 `UNKNOWN`。所以这一轮能上线的结论是"收窄可测、无信号时不收窄"，不是"模型答得更准"。

代价也摆在这里：本机一次模型调用约 6–8.5 秒（P8 那轮 A/B 实测平均 8487 ms，端点是 LM Studio
的 `qwen3-8b`）。所以 `AGENT_MODE` 默认是 `rules`，模型路径是
可选开关；只读轮根本不进这条路径，52 段模拟流量里只有 12 个任务真的付了钱。

### 写路径新增能力（本轮已落地）

- **退款原因与退款类型（B-1）**：`RefundReason` / `RefundType` 两组受控枚举进入写路径——申请时采集，
  存进 `refund_payload` 并参与动作摘要；确认前改原因或类型会改变摘要，旧审批随之失效。
- **按金额分层的审批与阈值（B-3）**：任务运行时用 `tier_for` 按订单金额定档，台账记录
  `approval_path`（`AUTO` / `APPROVAL`）。不超过阈值走自动执行（`execute_auto`），超过阈值走人工审批；
  两条路径共用同一个权威执行体 `_execute_refund`，资格、幂等 claim、动作摘要、订单版本校验一视同仁，
  **工具门没有因自动化而减弱**。自动执行落 `refund.auto_executed` 事件；档位快照作为第九个摘要键
  `approval_tier` 进审批摘要——档位配置变更（`APPROVAL_TIER_CHANGED`）会作废在途审批。分层配置在
  `app/core/approval_tiers.yaml`，装载期即校验（未知字段、审批人为空、档位重叠或断档等问题直接拒绝
  加载，校验类覆盖见 `tests/test_approval_tiers.py`）。
- **候选集的生产接线（P8）**：问模型之前，服务端先由 `SupportAgent._scoped_order_state` 取订单状态，
  租户与用户双重过滤，查不到返回 `None`——不造假证据；`build_candidates` 据此收窄答案空间。

以上三项的 ADR 正文与规格、说明书同步由收尾轮统一登记，本节只落当前写路径的事实。

---

## 二、"我很诚实"能被检查吗

大多数 README 的诚实是一句形容词。这里把它变成三件可执行的事。

**一，评测走产品自己的接口。** 任务路径的 40 条 Golden、只读路径的 62 条支持集、52 段模拟会话，
全部通过 `POST /api/v1/support/messages`、`POST /api/v1/tasks/{id}/approve` 这些入口驱动，再用
`GET /api/v1/handoffs/metrics`、`GET /api/v1/security/metrics` 和 `task_events` 台账把数字读回来。
脚本不另算一套私有指标——否则"测试通过"和"产品成立"就是两件事。意图评测也一样：
`evals/intent_metrics.py` 只调用公开入口（`classify` / `classify_support` / `get_catalog` /
`extract_order_id` / `extract_amount` / `CatalogService.resolve`），不复制一份 cue 表。

**二，可复现本身也是断言。** 同一份语料在 SQLite 与 PostgreSQL 16.15 上跑五通道检索表，
逐位一致。这不是理所当然：`visible_chunks()` 原本没有 `ORDER BY`，行序随数据库返回顺序变，
BM25 同分行的次序与重排候选集都会跟着变。修法只是按块 ID 排序，但价值在于"这个排序可复现"
从此可以被测试钉住，而不是靠人记得。

**三，事实由代码现算，守卫自己也要被负验证。**

| 守卫 | 钉住什么 | 读数 |
|---|---|---|
| 文档一致性（`tests/test_docs_consistency.py` + `docs/fact-baseline.yaml`） | 14 项事实：OpenAPI 路径与操作数、迁移条数与 head、表数、文件数、用例数、ADR 篇数、评测集规模、控制台页面数；清单只写"去哪核对"，不写数字本身 | 9 条测试；失败时指出文件与行；说明书与规格的同步也在这里守 |
| 架构依赖（`tests/test_architecture_guard.py` + `docs/architecture-guard.yaml`） | `app/` 各层严格向下，越层必须登记理由，禁止边、模块级环、过期例外统统失败 | 8 条测试，报 `file:line` |
| 负验证（`scripts/guard_negative_check.py`） | 守卫会不会失灵：在临时副本上造六处破坏（未声明层、反向依赖、禁止边、模块级环、登记腐化、文档数字漂移） | 六处全红，还原后全绿；约 33 秒 |

### 反例

- **守卫只守它认识的东西。** 文档守卫守 14 项数字与说明书的逐段同步，守不住散文里的过度声明；
  架构守卫只看 `app/` 内部的导入，看不到运行时的依赖方向。
- **数字不是结论。** `hybrid(hashing)` 那行用的是本机哈希替身，只证明多通道编排不破契约，
  不作为召回结论；17 个块的语料证明不了"切块策略优于固定窗口"；SQLite-only 的新增用例已经
  累计到 **561 条**（PG 那次全量停在 519 条时代，2026-10-28 时点收集 1080 − 519 = 561；上一轮记的 347 条是 866 收集时代的
  读数——`项目规格.md` 开头那 9 个来源的合计，519 + 347 = 866，同工作区的渠道可用性轮随后补了
  19 条 = 885，查询改写那轮的 81 条是它的子集；885 → 1080 的 195 条含已落地的 B-1/B-3、返工轮、
  AUTO 对账回落轮、R1/R3 与 R7 新增，未逐轮拆分归因），截至 2026-10-28 该轮仍尚未在 PostgreSQL 复跑。
- **更好的指标也会变差。** 重排把 recall@1 从 0.794 抬到 0.912，却把 recall@3 从 0.971 压到 0.912，
  代价还是 2.3 秒一次、p95 2602 ms，对 300 ms 的交互预算判 OVER BUDGET——所以它默认关闭，
  这两条都留在表里。
- **`0/16` 那次我差点把它写成"模型无用"**。后来改成"这份流量没给模型机会"，因为按规则词表
  写测试再证明规则赢，是在自证。

---

## 三、什么时候不该回答

能回答是能力，不该回答是边界。这里的"拒"分四层，每层有自己的形状：

| 层 | 拒什么 | 长什么样 |
|---|---|---|
| 路由 | 问句不是写请求 | `blocks_write_action` 为真，不进模型、不建任务；「运费谁出？」走政策问答 |
| 槽位 | 必填字段还没拿到，但客户有可能自己补上 | 第一次纯追问；第二次挂**本人**最近订单候选；第三次才移交（P11） |
| 计划 | 写不出受控查询计划 | `NO_PLAN` → 转人工，不猜 SQL、不硬编一个维度 |
| 执行 | 资格、权限、金额不满足 | `FAILED` + 原因码，钱不动；审批缺失或过期同样拒绝 |

受控 Text-to-SQL 是这层边界最清楚的例子。问句先被确定性启发式写成结构化计划，再由构造器
生成 AST，守卫逐条检查（表、列、函数、`LIMIT`、多语句、注释、作用域列），最后在只读连接上执行。
四个文件里没有一次模型调用。

2026-10-25 新增的预取路径与它**并存**，不是替换：`AnalyticsRouter` 用问句形状、计划和一次
按身份过滤的 `COUNT` 决定走 SQL 还是预取；预取在服务端把 count / sum / group_by 算好，
模型只负责措辞，回复里的每个数字还要过一遍独立的事实性守卫，对不上就回退模板。
L3 明细永不进 prompt，只按 `query_id` 在追问时展开。取舍与边界见
[ADR-029](docs/adr/ADR-029-预取统计.md)。

一个真实的拒绝：「我今年退了几笔已发货的单」里有退款数据集和计数词，所以路由进 `SELF_ANALYTICS`；
但「已发货」是订单状态、不属于退款记录，planner 不猜，返回 `NO_PLAN`。客户读到
「这条我暂时无法确认依据，已转人工同事核实后回复你。」，`resolved=false`，台账记
`REJECTED / NO_PLAN`。**没有计划就没有查询，也就没有一个猜出来的数字。**

守卫的拒绝矩阵（`python scripts/sql_evidence.py`，10 条实测）：

| 塞进去的语句 | 结果 |
|---|---|
| `SELECT COUNT(*) AS metric FROM refunds`（无 LIMIT） | `LIMIT_MISSING` |
| `... LIMIT 5; DROP TABLE refunds` | `MULTI_STATEMENT` |
| `SELECT * FROM refunds LIMIT 5` | `STAR_SELECT` |
| 手写 `WHERE tenant_id = 'acme'` | `UNKNOWN_COLUMN`：作用域列不可读，由执行器注入 |
| `load_extension('x')` | `UNKNOWN_FUNCTION` |
| `GROUP BY status HAVING ...` | `SYNTAX`：不允许 `HAVING` |

在转人工之前还有一步容易被漏掉：**客户不是不想自助，是看不见自己该填什么**。
2026-10-26 这轮把「缺 `order_id` 就再说一遍」改成了候选降级——第二次仍缺时，回复不再重复原话，
而是列出**这个人自己**最近的 ≤3 条订单，每条只给四个 L1 事实（订单号 / 商品名 / 状态 / 签收时间），
第三次仍没补上才按 `repeated_clarification` 移交，并把客户**真正看到过**的那几条带进移交包
（`attempted_candidates`，从卡片回读而不是重新查一遍）。纯模板拼接，`AGENT_MODE=rules` 下
一次模型都不问；预算也因此从借用 `frustration_rounds=2` 独立成 `clarification_rounds=3`
（不独立的话第二次追问当场转人工，卡片永远渲染不出来）。同一批 5 段剧本跑两个预算，全部走
`POST /api/v1/support/messages`：

| | 旧预算 =2 | 新预算 =3 |
|---|---|---|
| 转人工率 | 1.0 | **0.4** |
| 自助解决率 | 0.0 | **0.6** |
| 拿到可用候选的比例 | 0.0 | **1.0** |

`before` 那一列的 0 需要解释：旧预算下卡片**也挂上了**，一轮 2 段 × 5 段共 10 张，但挂在
转人工那一轮——卡片还在，客户已经拿它自助不了。所以主指标只数发生在 Agent 仍在服务的轮次上的候选。
分 persona：`cooperative` 3 段全部自助收敛、转人工 0；`stuck` 2 段照样在第三次追问处移交。
取舍与边界见 [ADR-033](docs/adr/ADR-033-澄清候选降级.md)。

转人工不是异常兜底，而是正常出口。52 段模拟流量里 19 段转人工，拆开看没有一段是"系统崩了"
（下表是 2026-10-26 用同一份剧本、同一 `--refund-mode timeout_after` 的复跑）：

| 指标 | 读数 | 口径 |
|---|---|---|
| 转人工率 | 19/52 = **0.3654** | 至少一条 `handoff_events` 的会话 |
| ├ 审批门（RISK） | 12 | 退款必须人工审批，设计上就"转" |
| ├ 客户明说要人工（USER_REQUEST） | 4 | 10-25 那轮这里是「没听懂」（NO_UNDERSTANDING）4 |
| └ 空闲未解决（IDLE_TIMEOUT） | 3 | 超时回填触发 |
| 自结率 | 26/52 = **0.500** | 无转人工且最后一轮 `resolved=true` |
| 放弃率 | 3/52 = **0.0577** | 全部是 `UNKNOWN`；10-25 那轮是 9/52 = 0.1731（退款进度 6 + `UNKNOWN` 3） |

**转人工率与自结率一个字没变，变的是「走哪条出口」和「放不放弃」**——这是本轮最该说清楚的一点。
那 4 段 frustrated 剧本（先问退款进度、再说"你没回答我的问题"、最后明说"转人工"）以前在第 2 轮就被
`repeated_clarification` 判成"没听懂"，同时留下 6 条 `REFUND_STATUS` 放弃事件；预算放宽加上候选之后
会话活到第 3 轮，客户在那里自己说了要人工。所以同一个会话不再被记成"系统没听懂"，也不再是放弃。
我没有把旧数字悄悄改掉：上面的历史读数留在 [docs/guide/证据索引.md](docs/guide/证据索引.md) 的 2026-10-25 那一节。

流量是模拟的（问答∶退款∶失控 ≈ 22∶12∶15，是我对售后队列的猜测），审批者是脚本化的坐席，
所以这些比率换一批真实工单一定会变。它们证明的是"每条出口都有计数"，不是"真实队列长这样"。

### 反例

- **「按商品分布」曾经被答成按状态分布。** P3-a 已修：没有数据基础的维度返回
  `UNSUPPORTED_DIMENSION` → 转人工，不再回落 `group_by=status`；P3-b 的路由把
  `UNSUPPORTED_DIMENSION` 放在最前面，预取路径也不会把它变成「猜一个维度」。
- **预取阈值是标定值，不是质量门槛。** `ANALYTICS_PREFETCH_MAX_ROWS=200` 只在当前演示
  数据上定过路由分布，换数据量要重跑；快照只在进程内，多 worker 下追问可能按
  「过期 / 不存在」要求重新提问。
- **误答比拒答更贵，但我没有单独计量它。** 现在只有转人工率、自结率和放弃率；「答了但答错」
  散落在 bad case 里，没有一个稳定指标。这是下一处该补的口径。
- **候选降级只覆盖 `order_id` 一个槽位。** 缺金额仍是纯追问，没有「候选金额」这种东西；
  5 段剧本是**固定措辞**，不能拿来预测线上转人工率——它能证明的只有「同一批剧本、只动阈值这一个
  开关的条件下，多给一轮加三个候选让配合型客户走通了」。
- **只有 4 个渠道能入站。** 8 个渠道都有能力声明，但 Web / 邮件 / 微信 / 企业微信之外的 webhook
  返回 404——是未实现，不是拒绝。

## 如果重来

**如果重来，我会先收集真实工单，再决定要不要模型。** 现在的顺序是反的：先把机制做出来，再造 11 句
词表外的句子证明机制有效。那 11 句证明的是"缺口存在、模型能补一部分、补上的部分能被安全接住"，
不证明缺口在真实队列里有多宽。**2026-10-25 补上了这个顺序的第一步**：外部 201 条公开客服
原话（人工标注、脱敏、带来源）+ 145 条手写补位（含 1 条人工确认的缺口关闭），规则覆盖率 0.5473、准确率 0.4527，
模型只在规则漏掉的缺口上被测（[ADR-028](docs/adr/ADR-028-意图语料评测.md)）。
但外部层仍只是一个来源，不是 TicketPilot 自己的进线分布；下一步才是接自己的流量。

取舍全文见 [ADR-020](docs/adr/ADR-020-对话入口的模型缺口.md)。

**如果重来，我会先建双库 CI 和负验证，再写功能。** 现在的守卫是补上的，虽然补得值：`ORDER BY` 那种缺陷
如果没有双库对照，会一直藏在"本机跑得通"里。下一步是把 `docs/guide/评测说明.md` 里每个数字绑定到
脚本输出——现在这份文档仍有一部分靠人工同步，事实基线只覆盖了 18 项。

**如果重来，我会先做"该不该回答"的评测，再做"答得好不好"的评测。** 现在 Golden 与 Support 主要测
意图与动作正确，误答率靠人工看 bad case。2026-10-25 补了一层 held-out 意图语料：外部 201 条
真实措辞 + 145 条手写补位（P5 后共 346 条），规则覆盖率 0.5473 / 准确率 0.4527，`UNKNOWN` 与读错、写/读误判的
代价已经分开计量；P5 又给漏判问法加了「去重队列 → 人工确认 → 回灌同一份语料 → 周期报告」的闭环，
但关闭一条缺口不等于修好分类器；它**不是本项目自己的进线分布**，「该答 / 该拒 / 该转人工」仍由人工判据
写在用例里。顺序应该是：真实进线采样 → 标注"该答 / 该拒 / 该转人工" → 再谈模型覆盖。
Text-to-SQL 的维度扩展（商品、订单）仍是正确方向，但它需要先有可读列与计划 schema；
在那之前，P3-a 会选择拒绝而不是猜一个近似维度。
P11 补的是同一类顺序问题的反面教材：我直到 2026-10-26 才去量「澄清失败之后客户怎么样了」，
而在此之前，`repeated_clarification` 借用「客户烦躁」的阈值，第二次追问就当场转人工——
如果一开始就计量放弃率与移交原因，这个借用会当天被发现，而不是一轮功能改造顺手改掉。

---

## 架构：一条写路径的六个不变量

```text
入站 ──► ChannelGateway：验签 → 服务端身份映射 → 消息 ID 幂等认领 → 按渠道渲染
                        │
                        ▼
POST /api/v1/support/messages  ← 两条 web 入口共用同一道幂等闸
        │
        ├─ 只读意图 ──► SupportAgent（本地，无副作用）
        │                   ├─► ContextCard ──► 按需加载章节（政策带引用）
        │                   └─► 自助统计 ──► 计划 → AST → 守卫 → 只读执行 → 台账
        ├─ 转人工 ────► HandoffBundle ──► 坐席队列 ──► claim / release / close
        ├─ 写意图 ────► TaskRuntime
        │                   ├─ 规则路由 / 可选槽位抽取
        │                   ├─ 退款资格（服务端）
        │                   ├─ 审批（四眼 + action_digest + 版本绑定）
        │                   ├─ Tool Gateway ──► 执行器 ──► 台账（幂等 claim）
        │                   └─ Outbox（同事务）──► worker ──► 对账收敛
        └─ 主动服务 ──► 事实 → 规则 → 7 道抑制闸门 → 待确认提案（客户确认才入队）
```

1. **只读不落任务台账**，问答不污染审计记录。
2. **写路径每次外部调用都有台账**；成功或失败后审批置 `CONSUMED`，结果未知时保持 `APPROVED`
   以继续阻塞重复退款。
3. **任务状态与 `result.outcome` 分离**；资格不通过是业务结论，任务仍是 `SUCCEEDED`，展示以
   `outcome` 为准。
4. **重试与回执由 Manifest 声明**：超时、退避、熔断、降级链在执行器里；网关只管校验、授权、
   审批、幂等与审计。副作用与"结果未知"绝不重试。
5. **渠道只改传输与呈现**：`app/agent` 与 `app/services` 没有一处按渠道分支；新增渠道只加适配器。
6. **主动服务默认沉默**：无规则、未同意、已退订、重复、冷却、当日上限、模板缺上下文，任一命中
   就不出声；提案不推送，客户确认才写入队列，且确认与 Outbox 行同事务。

## API

完整契约见 [docs/guide/接口说明.md](docs/guide/接口说明.md)。共 53 个操作（50 条路径，含 2 个健康检查）：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/v1/support/messages` | 客服对话；只读回答或转交确定性工作流 |
| `GET` | `/api/v1/support/cards/{kind}/{card_id}/sections/{section_id}` | 按需加载章节正文（政策含引用） |
| `POST` | `/api/v1/analytics/query` | 自助统计：问句 → 路由 → 受控 SQL 或预取（事实由服务端计算、模型只表达） |
| `POST` | `/api/v1/tasks` | 创建售后任务；带 `Idempotency-Key` 时同键重放返回同一任务 |
| `POST` | `/api/v1/tasks/{task_id}/approve` | 审批通过 / 驳回（四眼，`admin` 也不能自批） |
| `GET` | `/api/v1/handoffs` | 转人工待办队列（坐席） |
| `POST` | `/api/v1/channels/{channel}/webhook` | 邮件 / 微信 / 企业微信入站回调（先验签） |
| `GET` | `/api/v1/approvals` | 审批队列（按状态过滤、分页） |
| `GET` | `/api/v1/monitor/signals` | 三类在线信号阈值读数（只读，含误报率） |
| `POST` | `/api/v1/monitor/reviews` | 复核结论写入，供误报率读数 |

错误统一使用信封结构，所有响应带 `X-Request-ID`；不返回堆栈、连接串、密钥或内部 Prompt：

```json
{
  "error": {
    "code": "APPROVAL_EXPIRED",
    "message": "Approval has expired; request a new one.",
    "retryable": false,
    "request_id": "req-..."
  }
}
```

## 怎么验证这些说法

```bash
python -m pytest                        # 收集 1382 条、90 个测试文件；2026-10-28 本机实测 1074 passed / 6 skipped / 0 failed
ruff check app tests scripts evals alembic   # 零告警（与 CI 同一套目标）
python -m mypy app                      # strict，154 个文件，零错误
python evals/run.py                     # Golden Set 40/40（任务路径 + 轨迹断言）
python evals/support_run.py             # 支持集 62/62（12 意图：正常 34 / 边界 21 / 失败 7）
python evals/clarification_candidates.py  # 澄清降级前后对比：同一批 5 段剧本跑旧预算=2 / 新预算=3
python evals/business_metrics.py --refund-mode timeout_after  # 52 段模拟流量：转人工 19、自结 26、放弃 3
python evals/metrics_baseline.py --check   # 五个离线评测源的 395 条确定性读数与 evals/baseline/ 逐键相等（默认零容差）
python evals/answer_metrics.py --enforce    # 答案层四项判据：引用命中 60/60、编造 0/60、数字溯源 0/60、护栏探针 5/5
python scripts/build_intent_set.py --check   # 346 条意图语料两层可重算、逐字一致
python evals/intent_metrics.py --enforce     # 规则覆盖、误判代价、写方向拆分、模型缺口
python evals/intent_candidates.py            # 候选化离线证据：收窄集 / 无信号集两个分母（0 次调用）
python scripts/manage_intent_gaps.py report --cycle 2026-10-26   # 缺口队列计数；分层对比加 --before / --after
python scripts/build_retrieval_set.py --check   # 分层检索集可重算、逐字一致
python evals/retrieval_metrics.py --rewrite off # 与开启改写的读数对照
python scripts/guard_negative_check.py  # 守卫的反向验证：6 处破坏全被抓到
```

6 条 skip 是 PostgreSQL / Redis 专项；指向真实容器后可以跑满。检索门槛只套第一个渠道
（`gate_channel = bm25`），`hybrid(hashing)` 那行不作为召回结论。

## 已知边界（先说七条）

1. **控制台仍是开发身份**：可切换的 Header 预设；生产身份必须由 OIDC 提供，适配器尚未实现，
   `prod` 配置会拒绝启动。
2. **模型只在本机 LM Studio 上验证过**：托管模型没有验证；346 条意图语料的外部层是公开客服原话、
   补位层仍是手写，模型只在规则 `UNKNOWN` 的缺口子集上被测过。
3. **没有按会话或按用户的模型预算**：`llm_usage` 能记账，不能在下一次调用前刹车。
4. **实时订阅靠数据库轮询**：任务事件端点默认保持连接，每 0.5 秒读一次 `task_events`，
   15 秒心跳、静默 120 秒关闭；跨 worker 成立是因为所有进程读同一张表，不是进程内
   pub/sub。推送延迟上界是轮询间隔，且没有跨任务的事件总线。
5. **主动通知的"送达"只到出站边界**：默认传输拒绝一切发送；演示用 `local_file` 只写本地文件，
   没有 SMTP / 微信 / 短信服务商协议，也没有平台送达回执。
6. **重排超出交互预算**：p95 2602 ms 对 300 ms 预算判 OVER BUDGET，默认关闭，只适合离线批量。
7. **候选降级只做到 `order_id` 一个槽位**：缺金额、缺商品仍是没有候选的纯追问；候选最多 3 条
   本人最近订单、纯模板拼接，移交包是它唯一多带一份的地方（`support.reply` span 只记数量，
   不记订单号），其他推送、导出、日志通道一律不带。

完整清单（渠道验签是自定义方案、限流默认进程内、稠密检索是全表扫描、规则只能申请和批准但
不能删除或按租户灰度、统计台账不保存结果行、回放只覆盖可复现会话……）在
[docs/guide/范围与边界.md](docs/guide/范围与边界.md#已知边界)。
每一条都写了"为什么保留"与"要接真实系统还缺什么"。

## 文档地图

| 想知道 | 读 |
|---|---|
| 怎么装、怎么跑、环境变量、Docker | [docs/guide/快速开始.md](docs/guide/快速开始.md) |
| 每个数字怎么测出来的、反例与代价 | [docs/guide/证据索引.md](docs/guide/证据索引.md) |
| 范围、不做什么、完整已知边界 | [docs/guide/范围与边界.md](docs/guide/范围与边界.md) |
| 系统设计与数据流 | [docs/guide/架构说明.md](docs/guide/架构说明.md) |
| 接口契约与错误码 | [docs/guide/接口说明.md](docs/guide/接口说明.md) |
| 评测集、指标口径与基线 | [docs/guide/评测说明.md](docs/guide/评测说明.md) |
| 威胁模型与 fail-closed 配置 | [docs/guide/安全说明.md](docs/guide/安全说明.md) |
| 还剩什么没做、按什么顺序做 | [docs/下一步规划.md](docs/下一步规划.md) §0.2 |
| 优化阶段的裁定与意图改造落地方案 | [docs/下一步规划.md](docs/下一步规划.md) §15 · [实施包](openspec/changes/add-intent-routing-data-driven-2026-10/proposal.md) |
| 关键取舍（39 篇，含反例） | [docs/adr/](docs/adr/) |

## 项目结构

```text
app/
  agent/            意图目录、规则路由、意图候选集、可选受限模型、升级策略、上下文卡片
  services/         任务运行时、退款、审批、对账、转人工、记忆、主动服务
  channels/         渠道适配器、能力矩阵、验签、入站幂等、出站传输
  retrieval/        BM25 / 稠密 / RRF / 门控 / 重排 / 查询改写
  sql/              确定性 planner、AST 构造、SELECT 子集守卫、只读执行
  api/              FastAPI 路由与依赖
alembic/          16 条迁移，head 为 ea6c2b48d701
evals/             Golden 40、Support 62、分层检索集、澄清降级剧本、意图候选集证据、模型分布与安全交叉、检索指标
scripts/           seed、demo、退款剧本、缺口探针、稠密索引、守卫负验证
tests/             90 个测试文件；含架构依赖守卫与文档一致性守卫
web/               React 19 + Vite 控制台（10 个业务页面）
docs/              规格、ADR、评测口径、证据与说明书
```

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)：本地开发、提交前的质量门禁、目录约定、如何新增商品 /
政策数据与接口，以及截图与文档纪律。改"事实"（代码、接口、模型）要走 ADR；改"去哪里核对"
不用。

## 安全

部署前请读 [docs/guide/安全说明.md](docs/guide/安全说明.md)。项目默认拒绝以 `prod` 模式启动：Header 身份、
mock 退款与公开文档在 `prod` 下都不被接受；未实现的 OIDC / HTTP 适配器会直接让应用起不来。
这是刻意的 fail-closed，不是待办。

## 许可

[MIT](LICENSE) © 2026 TicketPilot contributors
