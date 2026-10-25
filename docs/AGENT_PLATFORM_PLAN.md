# TicketPilot 平台终局方案 v2

> **版本**：v2.3（2026-10-24）——v2.2（按 T4 渠道抽象、T4b 渠道溯源、T5 记忆与主动服务、T6 稠密检索回填、T7 新能力的界面与出口、T8 控制台迁到渠道入口、T9 记忆进答案回填状态）之后，再按 T10 主动规则可编辑面的**实测结果回填状态**；规划中被现实修正的条目（附件白名单、记忆读取路径、Outbox 消息种类、hybrid 召回结论）都在原处标注，不改写历史判断。
> **上一版**：v1《客服 Agent 平台方案》——本文档是它的续写与收敛，不是推翻重写。
> **一句话定位**：把「确定性工作流骨架 + 有界语言模型节点」从演示级售后 Agent，扩展成**可接入多渠道、可任务闭环、可人机协同、可度量运营**的企业级客服 Agent 平台。
> **核心主张**：> LLM 负责理解与生成，规则负责边界与安全，工具负责执行，台账负责对账，评测负责证明「事真的办成了」。

**读法**：第 0–1 章定「去哪、现在在哪」；第 2–5 章是「契约与设计」（可实现、可评审）；第 6–7 章是「怎么验证和上线」；第 8–9 章是「风险与附录」。
**版本约定**：文档中每条能力判断都带**代码位置**；标 ✅ 的条目必须能在仓库里指到文件，标 🔜 的必须写清「缺什么、怎么补」。

---

## 第 0 章 终局定义

### 0.1 定位：这是什么，不是什么

| 维度 | 是 | 不是 |
|---|---|---|
| 业务域 | 电商售后客服（订单 / 物流 / 退款 / 工单 / 政策） | 通用对话机器人、通用 ReAct 框架 |
| 形态 | **受控 Agent**：理解层可由模型参与，决策层与执行层确定性 | 让模型自由规划、自由选工具、自由决定金额 |
| 交付形态 | 平台（多渠道接入 + 协同 + 治理 + 评测） | 单点脚本、单次演示 Demo |
| 成功定义 | 「事办成了」且「可证明、可回滚、可交接」 | 「回复像人」 |

一句话：**这是一个带证据链的售后事务处理系统，恰好用 LLM 做自然语言入口。**

### 0.2 一个内核三副面孔

同一个内核（意图目录 → 策略 → 工具网关 → 台账 → 轨迹）在三个界面下呈现不同形态。**内核只有一份，差异只在「谁在说话、说到多细、谁能看见」**。

```text
                    ┌──────────────────────────────┐
                    │  内核                          │
                    │  IntentCatalog → PolicyEngine  │
                    │  → ToolGateway → Ledger → Trace│
                    └───────┬───────────┬──────────┘
                            │           │
        ┌───────────────────┘           └────────────────────┐
        ▼                                                ▼
┌───────────────────────┐                    ┌───────────────────────┐
│ ① 客户对话面           │                    │ ② 坐席工作面           │
│  自然语言入口           │                    │  队列 / 上下文包        │
│  渐进式披露（卡片+章节） │                    │  claim/release/close  │
│  转人工即交接           │                    │  指标看板             │
└───────────────────────┘                    └───────────────────────┘
        │
        └───────────────────────┐
                                ▼
                    ┌───────────────────────┐
                    │ ③ 运营治理面           │
                    │  Golden Set / 回放      │
                    │  Trace / 指标 / 告警     │
                    │  策略灰度 / 审计         │
                    └───────────────────────┘
```

| 副面孔 | 谁在看 | 关键约束 | 当前状态 |
|---|---|---|---|
| ① 客户对话面 | 终端用户 | 不能说没依据的话；不能自己批准退款；不该转人工时别转 | ✅ 核心链路已通（`app/agent/support_agent.py`） |
| ② 坐席工作面 | 客服/投诉专席/风控 | 首屏必须拿到完整上下文；接管期间 Agent 静默 | ✅ 已通（`app/api/v1/handoffs.py` 7 个操作 + `web/src/pages/AgentWorkbench.tsx` 工作台；接管 / 交还 / 结案已浏览器验收） |
| ③ 运营治理面 | 产品/运营/工程 | 每次改动都能回归；每个数字有口径 | ✅ 101 条离线用例（40 任务 + 61 只读，含轨迹断言）+ 检索评测 + 回放自检 + 分层抽样 + 运营看板 / Trace 回放 |

### 0.3 终局指标

**北极星指标：Verified Resolution Rate（可验证解决率）**
= 用户确认解决 ∧ 后端状态校验通过 ∧ 7 日内同因未复联的会话 / 总会话。

> 只看「回复满意度」会奖励「话说得好听但事没办成」；只看「转人工率」会奖励「什么都转出去」。北极星必须同时压住**办成**与**不复发**。

| # | 支撑指标 | 口径 | 现状 | 终局目标 | 数据来源 |
|---|---|---|---|---|---|
| M1 | 可验证解决率 | 见上式 | 未采集（`verification` 已有，可聚合） | ≥ 60% | `tasks` + `verification` + `conversations` |
| M2 | 自动解决率 | 无 handoff 且末轮 resolved 的会话 / 总会话 | ✅ 已可算（`GET /api/v1/handoffs/metrics`） | 按业务定 | `handoff_events` + `conversations` |
| M3 | 转人工率 | handoff 会话 / 总会话 | ✅ 已可算 | 观察值（不设下降目标） | `handoff_events` |
| M4 | 主动转人工占比 | `USER_REQUEST` / 全部 handoff | ✅ 已可算（`by_reason`） | ≥ 40%（越低说明 Agent 越没用） | `handoff_events.reason` |
| M5 | False Deflection Rate | 声称已解决但 7 日内同因复联 / 声称已解决总数 | 未采集 | ≤ 5% | `conversations` + `tickets` 关联 |
| M6 | 弃单率 | `abandon_events` 会话 / 总会话 | ✅ 已可算 | ≤ 8% | `abandon_events` |
| M7 | 幻觉率 | 无依据断言 / 总断言 | 部分（无依据门控已落地，断言扫描未建） | ≈ 0 | Trace + 抽样评分 |
| M8 | 工具选择正确率 | 期望工具 = 实际工具 | 未采集（轨迹已落库，可算） | ≥ 95% | `trace_spans` kind=tool |
| M9 | P95 端到端延迟 | 按意图分层 | Trace 已记 `latency_ms`，未看板化 | 按 SLA | `trace_spans` |
| M10 | 安全事件漏放数 | 输入/输出/传输层拦截或留痕命中 | ✅ 已可算（`security_events` + `GET /api/v1/security/metrics`） | 0 | `security_events` |

> **口径纪律**：每个指标必须能落到一条 SQL 或一段 Python 聚合，且写进 `docs/EVALS.md`。口径不写下来的指标不进看板。

### 0.4 三条不可逆承诺

这三条是**红线**，任何后续设计、任何模型输出、任何运营压力都不得突破。写在这里是为了在半年后回看时，能判断某次改动是不是把红线拆了。

| # | 承诺 | 具体含义 | 违反它会怎样 | 现在的护栏 |
|---|---|---|---|---|
| **C1** | **模型不碰钱、不碰权限** | 任何金额、资格、审批、租户边界由服务端规则判定；模型只输出「理解结果」与「文案草稿」 | 用户被多退钱、越权看到他人订单 | `app/services/refund_policy.py`、`app/services/approvals.py` 四眼、`ToolGateway` 的 authorize 段、意图目录 `risk_level` |
| **C2** | **没有依据就不生成** | 检索分数低于阈值 → 不做生成，走「无法确认 + 转人工」；数值型结论必须来自工具返回 | 客服一本正经地编政策 | `escalation.yaml` 的 `no_evidence` 信号、`retrieval.hybrid` 的 score 门控、`intents.yaml` 的 `no_evidence` 列 |
| **C3** | **静默失败为零** | 任何失败形态都必须有可见、可解释的用户侧结果；Agent 不知道就交人工，不猜、不吞 | 用户既没解决也没转人工，直接弃单（最危险） | 五类失败形态的降级链、`handoff` 兜底列、`abandon_events` 监控 |

> 反过来也要说清：**这三条会拖慢速度**。转人工多、退款审批慢，是代价，不是缺陷。

### 0.5 明确不做的事

| 不做 | 原因 | 什么条件下再考虑 |
|---|---|---|
| 不做通用 Agent 框架 / 多 Agent 编排 | 客服场景单 Agent + 确定性工作流足够；多 Agent 只增加不可解释性与排障成本 | 出现真实需要并行子任务的业务线 |
| 不做自研向量库 | pgvector 足够；先解决「有没有」再解决「强不强」。**当前实际实现比这句更弱**：向量存在 `document_chunks.embedding` 的 JSON 列里，稠密通道每次查询把租户可见的分片全捞进内存做 Python 余弦扫描（`hybrid.py::_dense_ranked`），没有 ANN 索引也没有 pgvector | 语料规模到了暴力扫描撑不住，或 Recall@5 达不到业务要求 |
| 不一次铺满全渠道 | 先 Web + 一个 IM + 邮件，验证抽象正确再横向复制 | 契约已被三个适配器（Email / IM×2 / Web）压过，但都是**本仓库自己实现的**签名与载荷格式；等一次真实平台协议对接来证明抽象没夹带私货 |
| 不让模型自选数据库表/字段执行写操作 | 写路径全部走既有领域服务 | 永不 |
| 不做完整语音 IVR 话务栈 | 留出 `ChannelCapabilities` 接口位即可 | 有真实话务量与预算 |
| 不做「主动服务」的猜谜式触发 | 主动服务由业务事件驱动，不由 Agent 推测；通知正文也只由规则模板渲染，模板变量走六键白名单 | 触发规则表已落地（`proactive_rules`），事件总线仍缺——当前由坐席显式调 `POST /memory/events` 投喂，这已经足够守住「不猜」这条线 |

---

## 第 1 章 现状基线

> 本章只陈述**代码里确实存在**的东西。每一行都能指到文件；指不到的，从本表删掉而不是留在计划里。

### 1.1 已有能力清单

| # | 能力 | 实现位置 | 验证方式 | 状态 |
|---|---|---|---|---|
| 1 | **统一意图目录（Data）** | `app/agent/intents.yaml`（12 行 / 2 槽位）、`app/agent/intent_catalog.py` | `tests/test_intent_catalog.py` | ✅ |
| 2 | **目录驱动的动作注册** | `app/agent/actions.py`（12 个 `@action`） | `tests/test_intent_rules.py` | ✅ |
| 3 | **升级触发表（Data）** | `app/agent/escalation.yaml`（8 信号）、`app/agent/escalation.py`、`app/services/escalation.py` | `tests/test_handoff.py`（8 个信号各一例） | ✅ |
| 4 | **策略引擎（阈值外置）** | `app/core/policy.py` + `app/core/config.py` 13 个阈值 | `tests/test_policy_engine.py` | ✅ |
| 5 | **工具 Manifest v2 + 参数校验** | `app/tools/base.py`、`app/tools/schema.py`、`app/tools/gateway.py` | `tests/test_tool_manifest.py` | ✅ |
| 6 | **六个已注册工具** | `app/tools/builtin.py`：`get_order` `get_logistics` `search_policy` `create_ticket` `check_refund_eligibility` `request_refund` | `tests/test_tool_manifest.py` | ✅ |
| 7 | **混合检索（BM25 + 稠密 + 重排 + RRF）** | `app/retrieval/{search,dense,fusion,rerank,hybrid}.py`，`retrieval_rrf_k=60` | `tests/test_retrieval_channels.py`、`evals/retrieval_metrics.py` | ✅（稠密/重排默认关，开关可灰度；稠密是 JSON 列上的暴力余弦扫描，无 ANN 索引，语料一大就要换 pgvector） |
| 8 | **Trace 骨架（8 类 span）** | `app/observability/tracing.py`、`redaction.py`、`trace_spans` 表 | `tests/test_trace.py` | ✅ |
| 9 | **转人工一等公民** | `app/agent/handoff.py`（`HandoffBundle` 18 字段）、`app/services/handoff.py`（状态机） | `tests/test_handoff.py` | ✅ |
| 10 | **坐席侧 API（7 操作）** | `app/api/v1/handoffs.py` | `tests/test_handoff.py` | ✅ |
| 11 | **服务端退款资格判定** | `app/services/refund_policy.py`（订单状态 / 30 天窗口 / 可退余额 / 重复退款 / 金额上限） | `tests/test_refunds.py` | ✅ |
| 12 | **四眼审批 + 订单版本绑定** | `app/services/approvals.py` | `tests/test_approval_queue.py` | ✅ |
| 13 | **台账 + 幂等 + 五种模拟结果 + 对账** | `app/services/executions.py`、`app/services/verification.py` | `tests/test_refunds.py` | ✅ |
| 14 | **订单级并发防护** | 订单级部分唯一索引 + 行锁 + 余额条件更新 | ADR-008、`tests/test_postgres_concurrency.py` | ✅ |
| 15 | **渐进式披露** | `app/services/support.py` 卡片元数据 + 章节按需加载 | `tests/test_support.py` | ✅ |
| 16 | **生产 fail-closed** | `app/core/config.py::validate_production_safety`（PG / Alembic / OIDC / 非 mock / 关闭文档） | `tests/test_governance.py` | ✅ |
| 17 | **会话槽位承接 + 澄清追问** | `app/agent/support_agent.py`、`app/agent/turns.py` | `tests/test_support_conversation.py` | ✅ |
| 18 | **Abandon 检测与指标** | `abandon_events` 表、`HandoffMetrics` | `tests/test_handoff.py` | ✅ |
| 19 | **引用可审计** | 政策章节带 `citation`（文档 ID / 版本 / 生效区间） | `app/retrieval/` | ✅ |
| 20 | **角色隔离** | 客户只看本人任务；坐席接口要求 `support` 角色 | `app/core/security.py`、`app/api/v1/handoffs.py` | ✅ |
| 21 | **受控 Text-to-SQL** | `app/sql/{planner,builder,guard,parser,schema,service}.py`、`sql_queries` 台账 | `tests/test_sql_guard.py`、`tests/test_sql_analytics.py`、ADR-010 | ✅ |
| 22 | **工具执行器（失败模型）** | `app/tools/executor.py`：6 类失败形态、超时预算、退避重试、熔断、降级链、回执渲染 | `tests/test_tool_executor.py`、ADR-015 | ✅ |
| 23 | **事务性 Outbox** | `app/models/outbox.py`、`app/repositories/outbox.py`、`app/services/outbox.py`、`scripts/outbox_worker.py` | `tests/test_outbox.py`、`tests/test_postgres_outbox.py`、ADR-013 | ✅ |
| 24 | **坐席与运营控制台（T1）** | `web/src/pages/AgentWorkbench.tsx`、`OpsDashboard.tsx`、`TraceViewer.tsx`；`ContextCard.tsx`（统计卡片 SQL 折叠） | `npm run build` + 浏览器手工验收（接管 / 交还 / 结案、三个看板页、390px、角色门禁） | ✅ |
| 25 | **渠道抽象与入站（T4）** | `app/channels/{base,registry,gateway,store,render}.py` + 四个适配器、`channel_messages` 表、`GET /api/v1/channels` 矩阵 | `tests/test_channels.py` 现为 **20 条**（T4 落 13 条，T4b 与 T7 补到 14 条，T8 再加 6 条）：验签 401、身份映射 403、数据库级幂等（重放读回 / 换内容 409 / 在途 409 / 陈旧认领接管）、渲染降级、关闭渠道 404 | ✅ |
| 26 | **渠道溯源（T4b）** | `SupportAgent.handle(channel=...)` → 移交包 `bundle.channel`、放弃事件、队列行与 `abandon_by_channel` | 同一批请求邮件记 `EMAIL`、网页记 `WEB`；队列行与移交包同源 | ✅ |
| 27 | **客户记忆与主动服务（T5）** | `app/services/{memory,proactive}.py`、`customer_profiles` / `proactive_rules` / `notifications`、`app/api/v1/memory.py` 10 个操作（T5 当时 9 个，T10 加 `PUT /memory/rules/{event_type}`） | `tests/test_memory.py` 21 条：默认视图不落档、部分合并、PII 两类处置、七道抑制闸、确认/取消状态机、跨用户 404、导出只有计数 | ✅ |
| 28 | **稠密检索护栏与多通道评测（T6）** | `app/retrieval/{build,dense,hybrid,fusion,rerank}.py`（`store_embeddings` 是 `dense.py` 里的函数）、`scripts/embed_policies.py`、`evals/retrieval_metrics.py --channels` | `tests/test_retrieval_channels.py` 26 条 + bm25/dense/hybrid/gated/rerank 五通道真实端点实测（SQLite 与 PostgreSQL 逐位一致）；prod 缺 embedding 配置启动即拒 | ✅ |
| 29 | **主动通知的出站投递（T7-4）** | `app/channels/outbound.py`（`ProactiveSender` + `OutboundTransport` 协议 + `none` / `local_file` + 地址反向查找）、`app/services/runtime.py::_outbox_sink` 按事件类型分派、`render_notice` 复用 T4 能力表 | `tests/test_outbound.py` 17 条：默认 `none` 拒发、未同意 / 已退订 / 静音 / 未确认各有拒绝码、渠道取档案偏好且 `payload` 改不动去向、`local_file` 下 `SENT` 首次可达、审计只存 `address_digest`；另在真实 HTTP 与浏览器走完「选渠道 → 确认 → 投递 → 已推送」 | ✅ |
| 30 | **新能力的控制台页面（T7-1/2/3，T10 加第 10 页）** | `web/src/pages/ChannelMatrix.tsx`、`MemoryPage.tsx`、`ProactiveRules.tsx`、`App.tsx` 路由与门禁 | 10 个业务页面；渠道矩阵只对 `support` / `admin` 可见，「我的记忆」是客户视角（读写删导出 + 待确认通知同意 / 取消 + 推送渠道只列真能送到的三个）；「通知规则」页只对 `support` / `admin` 可见，只能改文案 / 冷却 / 启停且改完当场看到字段校验结果；真跑确认删除按钮连带取消排队通知、页面文案标注「已推送 ≠ 服务商送达回执」 | ✅ |
| 31 | **网页入口并入同一道幂等闸（T8）** | 改 `app/api/v1/support.py`（委托 `ChannelGateway.accept_web`）、`app/api/deps.py`、`web/src/api.ts`、`web/src/pages/ConversationPage.tsx` | `tests/test_channels.py` 新增 6 条：旧入口带键去重、旧入口不带键不留一行、同一个键跨两条路由只受理一次、旧入口换内容 409、在途 409 且零新任务、陈旧认领被接管（`attempts` 变 2）；真实浏览器「丢掉一次响应 → 原话重发」拿到 `X-Channel-Deduplicated: true`、同一个 `task_id`、任务总数不变，坐席视角的渠道矩阵 `WEB` 计数随控制台发送 +1 | ✅ |
| 32 | **客户记忆进答案（T9）** | `app/agent/personalization.py`（四种模板效果的纯覆盖层 + 只读列查询 `load_profile`）、`app/agent/support_agent.py::_personalize`（排在 `_shape` 之后、`_guard_output` 之前）、`SupportPersonalization`、`MEMORY_PERSONALIZATION_ENABLED`、`web/src/pages/ConversationPage.tsx` 的标签行 | `tests/test_personalization.py` 18 条（无档案零影响、称呼只在一轮、自由文本不进回复、目录认不出的兴趣惰性、裁追问、重排、删档回到原样、Agent 零写路径零 `memory.*` 审计、开关关闭零变化、接管静默轮不带 `personalization` 而触发接管那一轮照常带称呼）+ 真实 HTTP 59 项断言（两个进程同一库、一开一关）+ 浏览器 DOM 走查；无档案客户 101 条评测与 `REPLAY DIFF EMPTY` 逐字不变；ADR-018 | ✅ |

| 33 | **主动规则的可编辑面（T10）** | `app/services/proactive.py::update_rule` + `_template`（写时校验）、`app/schemas/memory.py::ProactiveRuleUpdateRequest`（`extra="forbid"`，不暴露 `requires_confirmation`）、`app/api/v1/memory.py` 的 `PUT /rules/{event_type}`（`_require_seat`）、`web/src/pages/ProactiveRules.tsx` + `App.tsx` 的 `/rules` 路由 | `tests/test_proactive_rules.py` 21 条（九种 422 形态、`requires_confirmation` 被拒、事件类型越界、upsert 不新建表、审计只存 `template_sha256`、失败后规则行逐字不变）+ 真实 HTTP 52 项断言（`127.0.0.1:8135`、`.tmp/t10_live.db`）+ 浏览器 DOM 走查；ADR-019 | ✅ |

**规模事实**：Python 代码 `app/` 137 个文件；43 个测试文件（收集 502 条用例，SQLite 基线 496 通过 + 6 skipped，PG + Redis 两个容器同时指上则是 502 通过 + 0 skipped）；24 张表；42 个 API 路径 / 45 个操作（含 2 个健康检查）；23 篇 ADR（缺 ADR-012）；10 个控制台页面；离线用例 101 条（40 任务 + 61 只读）+ 以 `evals/support_set.json` 为源的检索评测。

### 1.2 八层差距矩阵

> 图例：✅ 已达标 / 🟡 部分具备 / ❌ 空白。「剩余缺口」列写的是**下一刀砍在哪**。

| 层 | 能力项 | 现状 | 已具备 | 剩余缺口 |
|---|---|---|---|---|
| **1 接入** | HTTP JSON + React 控制台 + 渠道抽象 | 🟡 | `ChannelAdapter` / `ChannelGateway` / 渲染矩阵 / 数据库级幂等（T4）、渐进式披露、渠道溯源（T4b） | 真实平台协议对接（微信/企微/邮件服务商各自验签与回调）、流式、附件、APP/WhatsApp/Voice/SMS 四类只有能力声明没有适配器 |
| **2 理解决策** | 目录 + 规则路由 + 单槽澄清 | 🟡 | 统一意图目录、升级触发表、策略引擎、槽位承接、受控 text-to-sql 意图 | 多槽乱序/取消/超时状态机、意图置信度与候选、统计计划的模型化规划器 |
| **3 知识记忆** | 混合检索 + 会话槽位 + 客户档案 | 🟡 | BM25 + 稠密 + 重排 + RRF、引用、无依据门控、客户自持档案（读写删 + 审计 + 导出，T5）、主动服务七道抑制闸、档案进答案（四种模板效果 + 原因码可见，T9 / ADR-018）、规则自助配置（文案 / 冷却 / 启停的写接口 + 写时校验 + 控制台第 10 页，T10 / ADR-019） | 记忆进答案的**模型侧**用法（把档案注入提示词、由模型决定怎么用——当前刻意不做）、规则的**删除**与版本表 / 一键回滚 / 双人复核 / 按租户灰度（T10 明确不做）、向量检索的 ANN / pgvector 与服务化重排的延迟预算（当前稠密是全表余弦、重排每次约 2.3 秒） |
| **4 执行** | 台账 + 幂等 + 对账 + 审批 | 🟡 | 六段网关、Manifest v2、六工具、幂等、对账、受控 text-to-sql、执行器（超时预算/重试/熔断/降级链）、回执模板、事务性 Outbox（两种事件都有消费者，`proactive.confirmed` 自 T7-4 起真投递并回写 `SENT`） | reserve/settle 独立段、真实服务商协议与送达回执（`SENT` 只说明"交给配置的传输并写成功"） |
| **5 人机协同** | 触发 + 移交包 + 接管 + 工作台 | 🟡 | 8 信号、`HandoffBundle`、状态机、坐席 API、坐席工作台（claim / release / close）、移交包与放弃事件带渠道（T4b） | 正确转人工率采集、Abandon 自动化巡检 |
| **6 安全合规** | 租户隔离 + 审批 + 审计 + fail-closed | 🟡 | 身份、角色、租户、审计、fail-closed、脱敏 redaction、输入/输出护栏、租户+IP 限流、体积上限、`security_events`、入站验签与身份映射、PII 两类处置（拒绝 / 掩码） | 附件白名单/EXIF 剥离、权限 scope 交集显式化、跨进程配额、标准 PII 识别器（当前是显式标签 + 正则） |
| **7 评测运营** | Golden + 检索评测 + 运维看板 + CI 门禁 | ✅ 基础闭环已通 | 101 条离线用例（含轨迹断言）、检索指标、多通道对比编排（T6）、回放（结论/轨迹 diff）、分层抽样、Trace 落库与回放、运营看板（转人工 / Outbox / span 健康度）、CI 阻断门禁 | 用例扩到 150+、bad case 自动回灌、LLM-as-judge 双轨评分 |
| **8 演进** | 事件驱动 + 跨渠道 + 个性化 | 🟡 | 渠道枚举与适配层已就位（同一会话可标出来源渠道）、客户档案与授权位已就位、档案以四种模板效果进答案（T9） | 事件总线、跨渠道会话延续（同一客户在邮件与网页之间接续）、**按结果学习**的个性化（"上次走通的路径"需要跨会话结果统计，档案里没有这张表）、坐席副驾 |

**读法**：前 6 层已经从「演示级」走到「可上线骨架」，剩下的不是补功能而是补**契约的最后一公里**（层 2/4）与**新增面**（层 1/3/7/8）。层 1 与层 3 在 T4/T5 后从「空白」变成「有抽象没有接线」——能力边界已经能用测试卡住，缺的是外部系统与真实协议；层 8 仍只到「可标注」，跨渠道延续与个性化没做。

### 1.3 三个技术债

| 债 | 描述 | 影响 | 偿还情况 |
|---|---|---|---|
| **TD1 双意图枚举** | `app/models/enums.py::Intent`（6 值，写路径）与 `app/agent/turns.py::SupportIntent`（10 值，读路径）并存 | 新增意图要改两处；两侧对「转人工」的定义曾经不一致（`TICKET_CREATE` vs `HUMAN_HANDOFF`） | 🟡 **已降级但未删除**。`intents.yaml` 成为唯一事实源，两个枚举降级为「目录的视图」（`SupportIntent._missing_` 容忍目录新增的标签）。**待办**：删 `Intent` 枚举，写路径改读目录 |
| **TD2 工具缺参数 Schema** | 旧 `ToolSpec` 只有 name/risk/side_effect/approval/idempotency，模型无法可靠生成入参 | 参数错 → 任务失败 → 用户体验崩塌且难归因 | ✅ **已偿还**（#91）：`ToolManifest` 带 JSON Schema 参数、结果 Schema、超时、重试、降级链、scope；网关在校验段拦非法参数 |
| **TD3 观测层为空** | `app/observability/` 曾是空目录 | 无法回答「这次请求到底做了什么」，评测只能看结果不能看轨迹 | ✅ **已偿还**（#90）：`TraceRecorder` + 8 类 span + 脱敏 + `trace_spans` 表 + `GET /api/v1/traces/{trace_id}` |

**还债原则**：TD2、TD3 是「有契约才有扩展」，属于必须先还；TD1 是「清理债」，可以在下一轮目录扩展时顺手删除。

---

## 第 2 章 目标架构

### 2.1 四个平面

```text
┌──────────────────────────────────────────────────────────────┐
│ 接入平面  Channel Plane                                       │
│  Web / App / 微信 / 企业微信 / WhatsApp / 邮件 / 语音 IVR / 短信 │
│  → ChannelAdapter.verify()   签名校验，失败即 401              │
│  → ChannelAdapter.normalize()→ MessageEnvelope（含幂等去重）  │
│  → ChannelAdapter.render()   ← AgentResponse                 │
└───────────────────────────────┬──────────────────────────────┘
                                │ 统一信封（渠道 / 语言 / 附件 / 身份）
┌───────────────────────────────▼──────────────────────────────┐
│ 认知平面  Cognition Plane                                     │
│  Guard(输入) → Understand(意图+槽位) → Decide(目录+策略)         │
│  → Retrieve(混合检索) → Compose(生成+引用) → Guard(输出)        │
│  记忆三级：Turn / Session / Profile                            │
└───────────────────────────────┬──────────────────────────────┘
                                │ ActionRequest / AnswerDraft
┌───────────────────────────────▼──────────────────────────────┐
│ 执行平面  Execution Plane                                     │
│  ToolGateway: validate → authorize → approval → idempotency   │
│                → invoke → audit                               │
│  → TaskRuntime（写路径）→ 台账 → 对账 → 回执                   │
│  → 受控只读 SQL 执行器（白名单 AST）                            │
│  → Outbox → 业务系统状态同步                                   │
└───────────────────────────────┬──────────────────────────────┘
                                │ 轨迹 + 结论 + 回执
┌───────────────────────────────▼──────────────────────────────┐
│ 治理平面  Governance Plane                                    │
│  Handoff（8 信号 / 上下文包 / 接管状态机 / 弃单监控）            │
│  Trace（span: guard|understand|retrieve|generate|tool|        │
│         approval|handoff|reconcile）                          │
│  Eval（Golden / 检索 / 回放 / 线上抽样）+ 指标 + 告警            │
└──────────────────────────────────────────────────────────────┘
```

**平面之间的单向依赖规则**（防止架构腐化）：

```text
接入 → 认知 → 执行 → 治理
  ▲                    │
  └────────────────────┘   只允许「读回执/读轨迹」，不允许反向写业务状态
```

- 执行平面**不得**回调认知平面做「再想想」——重试是执行平面的事（重试策略在 Manifest 里）。
- 治理平面**不得**成为执行前置依赖——Trace 写失败不能影响主链路（`TraceRecorder` 明确「不向请求路径抛异常」）。
- 认知平面**不得**直接访问数据库——只经工具网关。

### 2.2 一次请求的八步生命周期

```text
1 接入   收到渠道消息 → verify 签名 → normalize → MessageEnvelope
        （同 channel_message_id 幂等去重）
2 护栏   输入检查：注入特征、敏感内容、超长/超频
        （附件白名单**未实现**：只有 multipart 请求体体积上限）
3 记忆   载入 Session 槽位 → 遮蔽视图
        （Profile 层不在这一步读：档案是在第 6 步的个性化覆盖层里按列只读一次，
         只套四种模板效果，不写审计——ADR-018）
4 理解   intent + slots（目录匹配，带候选与置信度）→ 写 understand span
5 决策   IntentCatalog 取 action / risk_level / 必填槽 / 兜底
        PolicyEngine 取阈值 → 决定「走哪个闸、要不要澄清、要不要审批」
6 分流   ├─ 信息类 → 混合检索 → 无依据门控 → 生成（带引用）→ 记忆个性化 → 输出护栏 → 渲染
        └─ 动作类 → 目录校验必填槽 → 澄清或建任务 → 审批 → 执行
7 判定   评估 8 个升级信号 → 命中则生成 HandoffBundle → 接管/记账
        → 接管期间 Agent 静默
8 留痕   Trace 落库（脱敏）→ 回执给用户 → 指标聚合 → 抽样进评测池
```

**每一步的「不做」也是契约**：

| 步 | 不做什么 |
|---|---|
| 1 | **不靠"文本长得像"去重**：两条 web 路由（`/support/messages`、`/channels/web/messages`，T8 起共用同一道闸）只在带 `X-Channel-Message-Id` 时认领 `channel_messages`；不带键就是新的一次投递，跑一轮 Agent |
| 2 | 护栏不改写用户意图，只标记；标记会写进 Trace 而不是静默拦截 |
| 3 | 记忆层读档案但**不吃客户原文**：只允许四种可枚举的模板效果（称呼开头 / 商品卡重排 / 按简洁偏好裁追问 / 把目录认识的兴趣变成一条追问），`history_summary`、`contact_window` 与目录认不出的兴趣字符串一律不进回复；读档案也不写 `memory.*` 审计（ADR-018） |
| 4 | 理解层**不决定**动作，动作由目录决定 |
| 5 | 决策层**不执行**，只判定与路由 |
| 6 | 执行层**不生成**文案，文案由草稿渲染器按 `result_schema` 拼装 |
| 7 | 判定层**不代替**坐席做业务结论，只标记「该人接了」 |
| 8 | 留痕失败**不重试到影响主链路** |

### 2.3 关键设计决策（D1–D9）

| # | 决策 | 理由 | 代价 | 状态 |
|---|---|---|---|---|
| **D1** | **统一意图目录（Intent Catalog as Data）** 取代散落枚举与分支 | 意图 / 动作 / 必填槽 / 风险级 / 兜底必须在同一张表；否则「新增能力 = 改代码」 | 一次枚举合并与迁移（TD1） | ✅ 已落地（12 行，ADR-009） |
| **D2** | **模型只输出「理解结果」，不输出「执行决定」** | 边界判定留在确定性代码，模型升级不改变业务语义 | 复杂多轮需要更强的状态机 | ✅ 已落地（`agent_mode=rules` 默认，`pydantic_ai` 可选且有边界） |
| **D3** | **策略外置（Policy Engine）**，阈值不写在代码里 | 退款上限、升级阈值是运营参数，不是发版内容 | 需要配置版本与灰度 | ✅ 已落地（13 个阈值） |
| **D4** | **记忆只存客户自己写的，且可审计可删除** | 跨会话个性化必须与合规并存 | 三张表（档案 / 规则 / 通知）+ 10 个读写接口；进答案的口子只能枚举（四种模板效果）；规则文案只能改，不能新增触发器 | ✅ 已落地（T5 存储与授权，ADR-014；T9 进答案，ADR-018；T10 规则写面与写时校验，ADR-019）；三层里的「自动抽取层」被**主动否决**，档案进回复只走模板，不吃客户原文 |
| **D5** | **转人工是一等公民**，不是一个 ticket 意图 | 决定体验的是「退得干不干净」，不是「转得少不少」 | 坐席侧接口与静默规则 | ✅ 已落地 |
| **D6** | **评测双轨：轨迹 + 结果** | 只看回复文本无法证明「事办成了」 | Trace Schema + 回放设施 | ✅ 已落地（轨迹断言 + 回放 diff + 分层抽样） |
| **D7** | **渠道能力矩阵驱动响应形态** | 邮件正式、聊天短、语音口语，同一答案三种渲染 | 每渠道维护一份能力声明 | ✅ 已落地（T4，ADR-016）：`ChannelCapabilities` 8 个字段 + `render()` 降级，Agent 不读渠道名 |
| **D8** | **主动服务由业务事件触发，不靠 Agent 猜** | 物流异常推送必须事件驱动 | 事件总线 + 触发规则表 | 🟡 部分落地（T5 + T7-4）：规则表 + 七道抑制闸 + 客户确认在事务内入队 + 确认后真投递（`SENT` 只在传输写成功时落）；**没有事件总线**，事件由坐席调 `POST /memory/events` 显式投喂 |
| **D9** | **受控 Text-to-SQL：只读 + AST 白名单 + 强制租户过滤 + 强制 LIMIT，模型不直连数据库** | 回答「我上个月退了多少单」这类聚合问题，是通用工具做不到的；但让模型写 SQL 直接打库是典型高危面 | 需要 SQL AST 解析器与表/字段白名单，且查询结果要做脱敏与引用 | ✅ 已落地（`app/sql/` 四段式 + ADR-010） |

**D9 的边界（必须写死，否则它会变成一个"SQL 注入面"）**：

```text
允许：SELECT（单条）
允许：白名单视图/表（orders / refunds / logistics / tickets 的只读投影）
允许：等值/范围/IN 条件、GROUP BY、COUNT/SUM/AVG/MIN/MAX、ORDER BY
强制：WHERE tenant_id = <当前身份租户>（不可省略、不可被模型覆盖）
强制：LIMIT ≤ 200（模型可给更小值）
禁止：INSERT/UPDATE/DELETE/DDL、多语句（; 分割）、子查询穿透、UNION、注释绕过、
      information_schema / pg_catalog / sqlite_master、函数调用（除白名单聚合与
      date_trunc/lower 等少数只读函数）、表函数、CTE 中的写操作
失败：一律不出现在用户面前的技术栈，而转成「无法确认 + 转人工」
```

### 2.4 模块映射

| 逻辑层 | 现有目录（已落地） | 目标新增 | 归属平面 |
|---|---|---|---|
| 接入 | `app/api/v1/`、`app/core/security.py`、`app/channels/`（`base`/`registry`/`gateway`/`store`/`render` + `web`/`email`/`im` 三个适配器模块，`MessageEnvelope` 与渲染降级） | 真实平台协议适配器、附件流水线 | 接入 |
| 认知 | `app/agent/`（`intents.yaml`、`routing.py`、`actions.py`、`support_agent.py`、`personalization.py`） | 置信度与候选；把客户记忆**注入提示词**让模型自己决定怎么用，依然没做（ADR-018 只开了四种模板效果的口子）；`app/agent/memory.py` 这个模块**没有建**，档案在 `app/services/memory.py` | 认知 |
| 知识 | `app/retrieval/`（7 个模块）、`app/services/memory.py` + `app/services/proactive.py`、`customer_profiles` / `proactive_rules` / `notifications` 三张表、规则的坐席写接口 `PUT /api/v1/memory/rules/{event_type}`（T10，ADR-019） | pgvector / ANN 索引（当前稠密是 JSON 列上的暴力扫描）、抽取式长期记忆（ADR-014 主动否决，暂不做） | 认知 |
| 执行 | `app/tools/`、`app/services/runtime.py`、`app/services/refunds.py`、`app/services/approvals.py`、`app/sql/`（白名单、AST 校验、执行器）、`app/services/outbox.py`、`app/channels/outbound.py`（`ProactiveSender` 是 `proactive.confirmed` 的消费者） | reserve/settle 独立段、真实服务商推送协议（`OutboundTransport` 目前只有 `none` 与 `local_file`，`SENT` 止于"交给配置的传输并写成功"） | 执行 |
| 协同 | `app/agent/handoff.py`、`app/services/handoff.py`、`app/api/v1/handoffs.py`、`web/src/pages/AgentWorkbench.tsx` | 正确转人工率采集、Abandon 自动化巡检 | 治理 |
| 观测 | `app/observability/`（`tracing.py`、`redaction.py`）、`app/models/observability.py` | `metrics.py`、`alerts.py`（仍未建：指标由各服务就地计算） | 治理 |
| 评测 | `evals/`、`tests/test_golden_runner.py`、`tests/test_eval_datasets.py`、`tests/test_trace_sampling.py` | `evals/trace_assert.py`（独立断言模块）、bad case 自动回灌 | 治理 |
| 策略 | `app/core/policy.py`、`app/core/config.py` | 策略版本与灰度元数据 | 横切 |

---

## 第 3 章 领域模型与数据契约

> 本章的每个契约都是「代码里已经有的形状」或「本轮要落地的形状」，字段名与类型可直接落到实现。

### 3.1 MessageEnvelope（渠道统一信封）

```python
@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    channel: Channel              # WEB / APP / WECHAT / WECOM / WHATSAPP / EMAIL / VOICE / SMS
    channel_message_id: str       # 渠道侧消息 ID：幂等去重键 + 回执关联
    conversation_id: str | None   # 跨渠道延续靠它
    identity: Identity            # 租户 + 用户，由适配器解析，不接受请求体覆盖
    text: str | None              # 归一化文本（语音已转写）
    attachments: list[Attachment] # mime / url / sha256 / 抽取结果
    locale: str = "zh-CN"
    received_at: datetime
```

**契约**：`ChannelAdapter` 协议三方法 —— `verify(raw)`（签名校验）、`normalize(raw) → MessageEnvelope`（**必须**做 `channel_message_id` 去重）、`render(response) → ChannelPayload`（渠道化渲染）。
**能力矩阵**：`ChannelCapabilities` 声明 `supports_stream` / `supports_rich_card` / `max_length` / `supports_voice`。

### 3.2 Intent Catalog（意图目录契约）

文件：`app/agent/intents.yaml`（`version: "2026-10-23"`）。**一行 = 一个能力**。

| 字段 | 类型 | 约束 | 作用 |
|---|---|---|---|
| `intent_id` | str | 全局唯一 | 稳定标识，Trace / 指标 / 评测用例引用它 |
| `priority` | int | 越小越先匹配 | 决定路由顺序（当前 5–999） |
| `nature` | enum | `info`/`info_action`/`action`/`collaboration`/`none` | 决定走读路径还是写路径 |
| `action` | list[str] | 必须是已注册动作 | 第一个派发，其余为升级路径 |
| `required_slots` | list[str] | 必须是已定义槽位 | 缺则澄清，**写路径缺槽不建任务** |
| `validation` | str | `none`/`ownership`/`policy_engine`/`eligibility_and_approval`/`idempotency`/`citation_required`/`dwell_threshold` | 服务端校验策略 |
| `risk_level` | R0–R3 | 枚举 | 审批与护栏分级 |
| `no_evidence` | enum | `none`/`list_candidates`/`refuse_without_basis`/`answer_as_not_started` | 无依据时的回答方式 |
| `fallback` | enum | `none`/`clarify`/`clarify_then_handoff`/`handoff`/`create_ticket`/`approval` | 动作不可用时的退路 |
| `write_path` | bool | — | 是否进入 TaskRuntime |
| `cues` | list[str] | 小写子串 | 触发词 |
| `match_*` | bool | `match_policy_question`/`match_catalog`/`match_order_id`/`match_request_only` | 结构化匹配开关 |
| `clarify_prompts` | map[slot→str] | 槽位必须存在 | 缺槽时的专属追问话术 |

**加载期校验（已在 `intent_catalog.py::_load` 实现）**：槽位引用必须存在、动作必须已注册、重复 intent 直接报错、目录为空报错、**未知字段名直接报错**（拼错字段不会静默落回默认值）。**加载失败 = 进程起不来**（fail-fast，不允许用半份目录启动）。

**当前 12 行**：`HUMAN_HANDOFF`(P5) → `REFUND_REQUEST`(P10, R3) → `TICKET_CREATE`(P11, R2) → `SELF_ANALYTICS`(P15) → `REFUND_STATUS`(P20) → `REFUND_ELIGIBILITY`(P30) → `POLICY_QA`(P40) → `DELIVERY_ISSUE`(P50, R1) → `LOGISTICS_QUERY`(P60) → `PRODUCT_QUERY`(P70) → `ORDER_QUERY`(P80) → `UNKNOWN`(P999)。

### 3.3 Slot Schema（槽位契约）

```python
@dataclass(frozen=True, slots=True)
class SlotSpec:
    name: str
    type: str                    # order_id / amount_cents / text / enum / phone / address / date
    prompt: str                  # 缺槽时的追问话术
    examples: tuple[str, ...]    # 追问时给示例
    pattern: str | None          # order_id: ^ORD-[A-Za-z0-9-]+$
    sensitive: bool = False      # 预留位：意图目录里已经能标（`intents.yaml` 两个槽位显式写 false），
                                 # 但目前**没有任何消费者**——PII 判定走 `app/core/guard.py` 自己的分类，
                                 # 长期记忆只接受客户自己 PUT 的字段，都不读这个开关
    invalid_prompt: str | None   # 槽位给了但格式错时的追问
    missing_reason: str | None   # 机器可读原因码，如 MISSING_AMOUNT
```

**当前 2 个槽位**：`order_id`（带 pattern + 示例）、`amount_cents`（带 invalid_prompt + missing_reason）。
**未来扩展**：多槽乱序、取消、超时（见 §4.2.3）。

### 3.4 Tool Manifest v2（工具契约）

```python
@dataclass(frozen=True, slots=True)
class ToolManifest:
    name: str
    version: str
    description: str                  # 用户视角自然语言，用于选工具
    params: dict[str, Any]            # JSON Schema：type / required / pattern / enum / minimum / maximum
    result_schema: dict[str, Any]     # 回执渲染与断言的依据
    risk_level: RiskLevel             # R0 只读 / R1 低影响写 / R2 中影响写 / R3 资金不可逆
    side_effect: bool
    requires_approval: bool
    idempotency: Literal["required", "optional", "forbidden"]
    timeout_ms: int
    retry: RetryPolicy                # max_attempts / backoff / retry_on
    degradation: list[DegradationStep]# 有序降级链
    scopes: tuple[str, ...]           # 权限交集用
```

**网关六段（`app/tools/gateway.py`）**：`validate`（JSON Schema）→ `authorize`（scope 交集）→ `approval`（R3 拦截）→ `idempotency`（幂等键）→ `invoke`（handler + 超时/重试）→ `audit`（落 `tool_executions`）。

**当前 6 个工具**：

| 工具 | 风险 | 副作用 | 审批 | 幂等 | 关键参数 |
|---|---|---|---|---|---|
| `get_order` | R0 | 否 | 否 | optional | `order_id`（pattern 校验） |
| `get_logistics` | R0 | 否 | 否 | optional | `order_id` |
| `search_policy` | R0 | 否 | 否 | optional | `query` |
| `check_refund_eligibility` | R0 | 否 | 否 | optional | `order_id` |
| `create_ticket` | R2 | 是 | 否 | required | `category`(enum) / `subject` / `description` / `idempotency_key` |
| `request_refund` | R3 | 是 | **是** | required | `order_id` / `amount_cents`(1…1e8) |

### 3.5 HandoffBundle（上下文移交包）

`app/agent/handoff.py`，18 个字段的冻结 dataclass：

| 字段 | 含义 |
|---|---|
| `conversation_id` / `tenant_id` / `user_id` | 会话与身份 |
| `signal_id` | 命中的升级信号（对应 `escalation.yaml`） |
| `reason` | `HandoffReason` 枚举 |
| `priority` | P0 / P1 / P2 |
| `queue` | 坐席队列（GENERAL / COMPLAINT / RISK） |
| `intent` | 转出时的意图 |
| `transcript_digest` | 逐轮要点摘要（长度受 `handoff_digest_max_chars=400` 限制） |
| `transcript_ref` | 完整对话引用 |
| `intent_history` | 历史意图序列 |
| `slots` | 已抽取槽位（脱敏后） |
| `attempted_actions` | 已尝试动作与结果（避免坐席重复操作） |
| `pending_task_id` | 在途任务，坐席可直接看台账 |
| `policy_refs` | 已引用的政策条款 |
| `sentiment` | 情绪快照 |
| `suggested_next_step` | Agent 给坐席的建议（可采纳可忽略） |
| `trace_id` | 关联轨迹 |
| `user_visible_reply` | 转接时给用户的那句话 |

### 3.6 TraceSpan（轨迹契约）

```python
SpanKind = Literal["guard", "understand", "retrieve", "generate",
                   "tool", "approval", "handoff", "reconcile"]
```

`app/observability/tracing.py`：`TraceRecorder` 采集 `SpanBuffer`（`id` / `kind` / `name` / `attributes` / `parent_span_id` / `status` / `latency_ms`），落 `trace_spans` 表。
**铁律**：Trace 采集**不得向请求路径抛异常**；写入前经 `redaction.py` 脱敏（手机号、地址、内部标识）。
**Trace 必须能回答四问**：① 用户想要什么（intent + 置信度）② Agent 凭什么这么答（检索命中 + 引用）③ 做了什么（工具 + 参数摘要 + 结果）④ 谁在什么时候介入（审批 / 接管）。

### 3.7 状态机全集

| 状态机 | 状态 | 迁移 |
|---|---|---|
| **接管**（`HandoffStatus`） | `AGENT_ACTIVE` / `HANDOFF_PENDING` / `HUMAN_ACTIVE` / `QUEUED` / `CLOSED` / `RELEASED` / `NOTED` | AGENT_ACTIVE →(escalate) HANDOFF_PENDING →(claim) HUMAN_ACTIVE →(close) CLOSED；HANDOFF_PENDING →(超时 120s) QUEUED；HUMAN_ACTIVE →(release) RELEASED；`NOTED` = 记账但不接管（风险信号） |
| **任务**（`TaskStatus`） | `CREATED` / `RUNNING` / `WAITING_APPROVAL` / `SUCCEEDED` / `FAILED` / `CANCELLED` / `TIMED_OUT` / `REJECTED` / `EXPIRED` | 建任务 → RUNNING → （R3）WAITING_APPROVAL → approve → SUCCEEDED / reject → REJECTED；终态不可回退 |
| **审批**（`ApprovalStatus`） | `PENDING` / `APPROVED` / `CONSUMED` / `REJECTED` / `EXPIRED` / `INVALID` / `CANCELLED` | PENDING → APPROVED → CONSUMED（消费即绑定订单版本）；TTL `approval_ttl_minutes=30` 过期 |
| **工具执行**（`ToolExecutionStatus`） | `PENDING` / `SENT` / `UNKNOWN` / `RECONCILED` / `FAILED` / `CANCELLED` | 发送后结果未知 → `UNKNOWN` → 对账 → `RECONCILED` / `FAILED`（**未知不重试**） |
| **工单**（`TicketStatus`） | `OPEN` / `IN_PROGRESS` / `RESOLVED` / `CLOSED` | 单向推进 |
| **退款**（`RefundStatus`） | `REQUESTED` / `SUCCEEDED` / `FAILED` / `UNKNOWN` | UNKNOWN 交对账 |
| **会话槽位** | `pending_intent` + 槽位集合 | 补齐 → 执行；取消 → 清空；超时 → 过期不再续跑 |
| **意图澄清** | 追问轮次计数 | ≤ 2 轮；超限 → 转人工（`repeated_clarification` 信号） |

**不变量**（可写成断言测试）：
1. 接管期间 Agent 对外输出为 0。
2. 写路径在缺必填槽位时**不建任务**。
3. 同一幂等键重复请求返回同一台账行，不产生第二次副作用。
4. R3 动作在审批通过前不产生外部副作用。
5. `UNKNOWN` 状态的执行不自动重试。

### 3.8 数据表清单

**现有 23 张**（与 `Base.metadata` 一致，完整分工见 [PROJECT_SPEC §4.1](PROJECT_SPEC.md)）：
`orders` / `logistics` / `refunds` / `tickets` / `tasks` / `task_events` / `approvals` / `audit_events` /
`tool_executions` / `conversations` / `conversation_messages` / `documents` / `document_chunks` /
`trace_spans` / `handoff_events` / `abandon_events` / `sql_queries` / `outbox_messages` /
`security_events` / `channel_messages` / `customer_profiles` / `proactive_rules` / `notifications`。

**规划表兑现情况**（原「规划新增 7 张」）：

| 表 | 用途 | 关键字段 | 状态 |
|---|---|---|---|
| `sql_queries` | text-to-sql 执行台账 | query_digest, sql_text, params_digest, row_count, duration_ms, rejected_reason, trace_id | ✅ 已落地（Phase 1 本轮） |
| `outbox_messages` | 状态同步（最终一致） | aggregate_type, aggregate_id, event_type, payload, status, retry_count | ✅ 已落地（迁移 `c4f1b8d2e306`） |
| `security_events` | 安全拦截告警源 | category(injection/bypass/sensitive/grounding/rate_limit/payload), action, source, trace_id | ✅ 已落地（迁移 `f3a7c9d21b84`） |
| `channel_messages` | 渠道消息去重与回执 | channel, channel_message_id(uniq), conversation_id, status, request_digest | ✅ 已落地（T4，迁移 `a9d4c2f7b135`），幂等在**数据库唯一键**上做，不是内存表 |
| `customer_profiles` + `proactive_rules` + `notifications` | 长期记忆与主动服务 | 见 §12.9 | ✅ 已落地（T5，迁移 `c3b8e5a71d29`）；**字段与原规划不同**：不是 `kind/content/confidence/expires_at` 的抽取式记忆，而是客户自持的偏好档案 + 规则表 + 待确认通知三张。客户档案的写路径只有客户自己的 `PUT`；**规则表**自 T10 起另开一条坐席写路径（`PUT /memory/rules/{event_type}`，只可改文案 / 冷却 / 启停）；T9 起答案路径**按列只读**这张表（`preferred_name` / `preferences`），且只以四种模板效果影响回复 |
| `channel_capabilities` | 渠道能力矩阵 | — | ❌ **决定不建表**：能力是代码里的 `ChannelCapabilities`（8 字段冻结数据类）+ `GET /api/v1/channels` 只读视图。建表会让「加渠道」变成一次数据变更而不是一个适配器 |
| `eval_cases` / `eval_runs` | 评测集与运行结果 | — | ❌ 未落地：评测集仍是 `evals/*.json` 版本化文件，运行结果走 stdout 与 CI 门禁，没有入库 |

---

## 第 4 章 八层能力设计

> 每层五段：**目标 / 接口契约 / 关键规则 / 失败路径 / 验收标准**。
> 标注 ✅ 的层已落地，本节写的是「现状契约 + 剩余缺口」，标注 🔜 的层写的是目标形态。

### 4.1 层 1：交互接入层 🔜

**目标**：任意渠道进来的是同一种东西，出去的是该渠道该有的样子；新增渠道零改认知层与执行层。

**接口契约**：`ChannelAdapter`（`verify` / `normalize` / `render`）、`MessageEnvelope`、`ChannelCapabilities`。
**渠道响应矩阵**：

| 渠道 | 回复形态 | 长度 | 富卡片 | 流式 | 特殊要求 |
|---|---|---|---|---|---|
| Web / App | Markdown 气泡 + 卡片 | 300 字 | ✅ | ✅ | 保留章节展开 |
| 微信 / 企业微信 | 纯文本 + 模板消息 | 200 字 | 🟡 | ❌ | 卡片降级为「回复序号看详情」 |
| WhatsApp | 文本 + Interactive List | 1024 字符 | ✅ | ❌ | 按钮 ≤ 3 |
| 邮件 | 正式正文 + 引用区块 | 不限 | ❌ | ❌ | 必带工单号、时限、免责声明 |
| 语音 IVR | 口语短句，一句一事 | ≤ 25 字/句 | ❌ | ✅ | 禁列表与链接，必须给「转人工」出口 |
| 短信 | 单条 | 70 字 | ❌ | ❌ | 超长走模板拼接 + 退订语 |

**关键规则**：① `channel_message_id` 幂等，重复投递不产生第二条回复；② 身份只由适配器解析，请求体不可覆盖租户；③ 同一意图在 390px 与语音两个极端渠道下输出都可用。
**失败路径**：签名校验失败 → 401；重复消息 → 返回已有回执；附件超限/类型不符 → 拒绝并给示例；语音转写为空 → 追问一次不猜。
**验收标准**：新增一个渠道只实现 `ChannelAdapter`；同 `channel_message_id` 重复投递无二次回复；两极端渠道无内容截断错误。

### 4.2 层 2：理解与决策层 ✅（待补多槽与统计规划的模型化）

**目标**：把自然语言稳定转成「动作 + 参数 + 缺什么 + 风险多高」。

**接口契约**：`IntentCatalog.get/has/match/prompt_for/missing_slots`、`ActionRequest`（含 `signal` 字段）、`RoutePlan`、`Draft`。

**关键规则（三条闸）**：

```text
闸 1 规则前置：blocks_write_action() —— 疑问句/泛化陈述不得触发写操作
闸 2 策略引擎：阈值全部来自 PolicyEngine，代码中不得出现魔法数字
闸 3 权限交集：model_intent ∩ user_permission ∩ tool_policy，任一不满足即拒绝并说明原因
```

目录驱动四问：

```text
1 这个消息属于哪个意图？      → catalog.match(text, has_catalog_match, has_order_id)
2 这个意图要什么参数？        → catalog.required_slots / missing_slots
3 缺参数怎么问？              → catalog.prompt_for(intent, slot)
4 没有依据时怎么答？          → catalog.no_evidence + fallback
```

**失败路径**：缺必填槽 → 澄清（**不建任务**）；给了但格式错 → `invalid_prompt`；澄清超 2 轮 → `repeated_clarification` 信号转人工；`UNKNOWN` → 一次追问 → 转人工；无依据（政策类）→ `refuse_without_basis` + `no_evidence` 信号转人工。
**验收标准**：新增能力 = 目录加一行 + 动作实现 + 一条评测用例，**不改 `support_agent.py`**；乱序补槽/只补一槽/取消/超时四类用例通过且写路径零误建任务；阈值改配置后行为同步变化。

### 4.3 层 3：知识与记忆层 🟡

**目标**：回答有据、记性有用、记忆可控。

**接口契约**：`RetrievalService.hybrid(query, limit)` → `(chunks, evidence_score)`；**已落地** `MemoryService`（`get`/`update`/`delete`/`export`）与 `ProactiveService.emit()`；`MemoryStore` 这一层**只有服务端读写、问答不查 `MemoryService`**——T9 起答案路径只按列只读档案（`preferred_name` / `preferences`），走 `app/agent/personalization.py::load_profile`，**不经过 `MemoryService`**，且只以四种模板效果影响回复（ADR-018）。规则表另有一条**坐席写路径**（`PUT /memory/rules/{event_type}`，T10 / ADR-019）：只可改文案 / 冷却 / 启停，写时校验模板字段。

**关键规则**：
- 检索三通道：BM25（专有名词/编号必备）+ 稠密（语义召回）+ 重排（可选）；**RRF 融合**（`k=60`，候选池 20）。稠密与重排**开不通都不影响稀疏通道**，坏端点只降级排序不降级答案。
- 过滤链：`tenant → status=ACTIVE → effective_at 命中 → 权限可见性`。
- **无依据不生成**：`best_score < policy_min_score` 且意图为政策类 → 不生成，输出「暂时无法确认」并走 `no_evidence` 升级。
- 记忆铁律：① 不记忆完整手机号/地址/支付信息/证件/凭据；② 可审计（可读、可删、可导出，返回内容全部脱敏）；③ 可关闭：主动触达是**用户级**（`proactive_opt_in` + `muted_event_types`），记忆进答案是**进程级**（`MEMORY_PERSONALIZATION_ENABLED`，关掉后整轮不读档案、回复逐字回到不个性化时）——两者都**没有租户级开关**，运维能按部署关掉个性化，但不能只对某个租户关；④ 写入需依据（自动抽取层被 ADR-014 主动否决，**客户档案**的写入只来自客户自己的 `PUT`（部分合并），Agent 不推测生成，也不写档案）；⑤ 读出来也不许当话讲——档案进答案只允许四种模板效果，客户自由文本不进回复（ADR-018）；⑥ **规则文案是运营写、不是 Agent 写**（T10）：事件类型由代码定、`requires_confirmation` 不可写，改动**没有双人复核**、**没有版本表 / 一键回滚**、**没有按租户灰度**（ADR-019）。

**失败路径**：稠密端点超时/报错 → 退化为纯 BM25（`hybrid._dense_ranked` 捕获全部异常后返回空，**Trace 里没有 `dense_unavailable` 这一标记**，降级只表现为结果集变窄，不留可查证据）；重排连续失败 3 次 → 熔断 30 秒（`rerank.py` 的 `failure_threshold=3`、`cooldown_seconds=30.0`，与规划一致）；全部通道无命中 → 无依据路径；记忆写入失败 → 不影响主链路。
**验收标准**：政策类用例引用率 100%，无依据用例 0 幻觉；混合检索相对纯 BM25 的 Recall@5 有可量化提升（`evals/retrieval_metrics.py` 直接对比）；记忆接口可读可删可导出且全部脱敏；档案进答案只产生四种模板效果，且无档案客户的 101 条评测与 `REPLAY DIFF EMPTY` 逐字不变（T9 已达成，Recall@5 那条见下方实测结论）。

> **本条已实测（2026-10-25 真实端点）**：稠密 `bge-m3` 把 recall@1 0.7941 → 0.8529、MRR 0.8745 → 0.9265，recall@5 不变；hybrid 把 recall@3 0.9118 → 0.9706；rerank（Docker Infinity 的 `bge-reranker-v2-m3`）把 recall@1 抬到 0.9118、MRR 0.9559，但 **recall@3 回落到 0.9118**，每次约 2.3 秒。三行都只在 17 条标注的演示语料上，门槛仍只套 `bm25`，不构成「混合检索普遍更好」的结论。详见 `docs/EVALS.md` 与 `PROJECT_SPEC.md` §11.5。

### 4.4 层 4：任务执行层 🟡（执行器、回执、Outbox 已落地，待补 reserve/settle 独立段与外部推送）

**目标**：从「能查能改」到「每一步都有据、失败有路径、聚合问题也能答」。

**接口契约**：`ToolGateway.call(name, params, identity)`；`ToolManifest`；**已落地** `SqlAnalyticsService.answer(question, identity, session)` → `{plan, sql, columns, rows, row_count, duration_ms, query_id}`。

**关键规则**：
- 六段网关（validate → authorize → approval → idempotency → invoke → audit）不可跳过。
- 回执由 `result_schema` 渲染，**金额与单号来自工具返回，不由模型书写**。
- **受控 SQL 规则（D9）**：单条 `SELECT`；表/视图白名单；强制 `tenant_id` 过滤；强制 `LIMIT ≤ 200`；禁多语句/子查询穿透/UNION/注释绕过/系统目录/非白名单函数；SQL 文本与参数分开记录（参数化执行）。实现为 `app/sql/` 四段式：`planner`（计划）→ `builder`（AST）→ `guard`（白名单与作用域注入）→ `executor`（只读连接），18 个稳定拒绝理由码，拒绝也入台账。详见 [ADR-010](adr/ADR-010-controlled-text-to-sql.md)。
- 聚合类问题（「我上个月退了多少单」「这个月退款总额」）走 SQL 执行器；单实体问题仍走既有领域工具——**能用工具就不用 SQL**，SQL 只补工具覆盖不到的聚合缺口。

**失败路径**：

| 失败形态 | 处理 | 用户可见结果 |
|---|---|---|
| 参数校验失败 | 不调用，回引导 | 「订单号格式不对，应为 ORD-… 形式」 |
| 超时（可重试） | 指数退避重试（次数由 Manifest 声明） | 首次静默，超阈值后告知 |
| 上游 5xx / 熔断打开 | 走降级：稍后重试入口 | 「系统繁忙，已记录，稍后自动重试」 |
| 结果未知（读后超时） | 台账置 `UNKNOWN`，**不重试** | 「处理中，最长 30 分钟出结果」+ 主动通知 |
| 业务拒绝（超期/余额不足） | 不重试，给原因与替代路径 | 「已过 30 天窗口，可申请延保或建工单」 |
| **SQL 越界**（非 SELECT / 非白名单表 / 缺租户过滤） | **不执行**，记录 `rejected_reason` | 「这个问题我暂时无法确认，已转人工核实」 |
| 全部降级失败 | 转人工并携带完整上下文 | 「已为你转接人工」 |

**验收标准**：工具调用轨迹可完整回放（参数摘要、结果、耗时、重试次数、降级步骤）；六类失败形态各有测试用例且**无任何一种静默失败**；回执金额与单号来自工具返回；SQL 执行器对 12 类越界 SQL 全部拒绝且不触库。

### 4.5 层 5：人机协同与兜底层 🟡

**目标**：该自助时自助，该转人工时果断转、转得干净。

**接口契约**：`HandoffBundle`（18 字段）、`EscalationCatalog`（8 信号）、`HandoffService`（`escalate` / `claim` / `release` / `close` / `expire_pending` / `summary` / `detail` / `metrics`）、坐席 API 7 个操作。

**8 个升级信号（数据表 `escalation.yaml`）**：

| 信号 | 优先级 | 判定 | 队列 | 接管 | 特殊 |
|---|---|---|---|---|---|
| `user_request` | P0 | 意图 = `HUMAN_HANDOFF` | GENERAL | ✅ | 用户主动要人（最健康的一类） |
| `complaint` | P0 | 关键词（投诉/举报/315/媒体…15 个） | COMPLAINT | ✅ | `continue_task`：**保留已建工单** |
| `frustration` | P0 | 关键词 + 连续负面轮次 ≥ 2 | GENERAL | ✅ | 先安抚 |
| `repeat_failure` | P0 | 同类未解决轮次 ≥ 2 | GENERAL | ✅ | post 阶段判定 |
| `no_evidence` | P1 | 检索无依据（政策类） | GENERAL | ✅ | 「无法确认 + 转人工」 |
| `risk` | P1 | 风险级 R3（退款） | RISK | ❌ | **记账不接管**（`NOTED`），审批链才是正解 |
| `repeated_clarification` | P1 | 澄清轮次 ≥ 2 | GENERAL | ✅ | 「我还没听明白哪一单」 |
| `idle_timeout` | P2 | 空闲 ≥ 20 分钟 | GENERAL | ❌ | 预防性，记账不接管 |

**关键规则**：
- 接管期间 Agent **全面静默**：不插话、不重复解释；只允许生成**内部**坐席建议（不发给用户）。
- 交还控制权（`release`）后 Agent 恢复作答，用坐席摘要续上下文。
- 坐席 API 全部要求 `support` 角色，且按租户隔离：跨租户查 → 404（不泄露存在性），客户访问 → 403。
- Abandon 检测：会话无解决标记且空闲 ≥ 30 分钟 → `abandon_events`；同用户 24 小时内同意图再进线 → `REPEAT_INTENT`。

**失败路径**：坐席 120 秒未 claim → `HANDOFF_PENDING` → `QUEUED`（降级为留言 + 回呼）；重复 claim → 409；无效状态 → 422；release 后用户再问 → Agent 恢复。
**验收标准**：8 个信号各有测试用例，命中即产生 `HandoffBundle` 且坐席端可见全部字段；接管期间 Agent 零输出（断言验证）；弃单率可按渠道/意图/时段切片。

### 4.6 层 6：安全、合规与风控层 🟢（核心已落地，PII 流水线与附件白名单待补）

**目标**：把「身份与资金安全」扩展到「内容与数据安全」。

**关键规则**：
- **输入护栏（已落地）**：`app/core/guard.py` 识别提示词注入、规则绕过与敏感内容；命中**只记录、不改判**，写操作仍走服务端规则。辱骂/违法诉求分类、附件类型白名单与 EXIF 剥离仍待补。
- **工具权限交集**：`effective_permission = tool.scopes ∩ user.permissions ∩ tenant.policy`；Agent 工具权限不得高于当前用户。
- **输出护栏（已落地）**：`app/core/output_guard.py` 检查金额、时效、百分比、承诺与「今天/马上」类即时承诺；任务结果、卡片、工具动作视为有来源，无来源的片段替换为「需确认」并追加提示，不整段丢弃。
- **滥用防护（已落地）**：`app/api/middleware.py` 在路由前做租户 + IP 双令牌桶（429 + `Retry-After`）与请求体/multipart 体积上限（413）；桶在进程内，多 worker 会放大预算。
- **数据保护**：日志、Trace 与安全事件只存脱敏后的摘要（`app/observability/redaction.py`）；PII 入库前遮蔽、留存期与到期清理仍属规划。
- **SQL 专项**：SQL 执行器的租户过滤是**强制注入**，模型无法覆盖或省略；SQL 文本与执行结果都经脱敏；拒绝事件落 `sql_queries.rejected_reason`，可告警。

**失败路径**：护栏命中 → `security_events` 留痕 + Trace 记录，**不静默吞掉**（输入侧不拦截，输出侧只替换无依据片段）；跨租户/他人订单 → 404；无 scope 工具 → 403；生产环境配置不合规 → **启动即失败**（fail-closed，已实现）。
**验收标准**：✅ 注入用例先留痕再按数据库余额判资格，「你们政策允许全额退款」不改变结论；✅ 无来源金额/时效断言被替换为「需确认」；✅ 超频 429、超体积 413 均有测试；⏳ 日志与 Trace 的明文 PII 自动化扫描断言、附件类型白名单与 EXIF 剥离仍待补（越权与 SQL 越界用例已覆盖）。

### 4.7 层 7：运营与评测层 ✅（基础闭环）

**目标**：能证明「事真的办成了」，且每次改动都能回归。

**接口契约**：`evals/run.py`（40 条任务路径 + 轨迹断言）、`evals/support_run.py` + `evals/support_set.json`（61 条只读路径）、`evals/retrieval_metrics.py`（检索）、`evals/replay.py`（回放 diff）、`scripts/sample_traces.py`（分层抽样）。

**三层评测**：

```text
① 离线用例（现状 101 条 → 目标 150+）
   ├─ 覆盖矩阵：意图 × 正常/边界/失败；任务 40 + 只读 61，意图并集 12
   ├─ 断言粒度：终态 outcome + 工具名/参数 + 降级状态 + 回执来源
   └─ CI 门禁：Golden / 支持路径 / 检索指标 / 回放任一回归即阻断合并
② 回放评测（Replay）✅
   ├─ 读已落库 trace 与消息 → 新种子库重跑 → 结论 diff + 轨迹 diff
   ├─ 生成 id 与时间戳归一化；自检要求 diff 为空
   └─ 只覆盖无审批/无转人工的只读会话；写路径由 Golden 兜底
③ 线上抽样（Online Trace Eval）✅（规则侧）
   ├─ 按渠道×意图分层抽样，按 span 错误/护栏/澄清/转人工/无证据/降级/慢请求打分
   ├─ 产出脱敏 JSONL bad case 队列
   └─ 尚未接 LLM-as-judge 与自动回灌；低分样本仍需人工标注后进数据集
```

**指标矩阵**（口径见 §9.3）：可验证解决率、自动解决率、转人工率、主动转人工占比、弃单率、幻觉率、工具选择正确率、参数准确率、槽位一次抽全率、P95 延迟、单次成本、安全事件数。

**失败路径**：评测用例依赖外部服务 → 离线降级（hash 替身）保证 CI 可跑；回放数据集过大 → 按意图分层抽样。**原设计里的「judge 不可用 → 只跑规则评分并标注」这条路径目前不存在**——LLM-as-judge 从未实现，评分全部是确定性断言，因此也没有"双轨评分"可降级。
**验收标准**：✅ `python evals/run.py` 同时输出终态与轨迹断言；✅ `support_run.py` 覆盖 12 意图；✅ `replay.py --self-check` diff 为空；✅ CI 阻断回归。⏳ 用例数达到 150+；指标按渠道/意图/时段下钻；每次发版附「变更影响报告」。

### 4.8 层 8：演进方向 🔜

| 方向 | 设计要点 | 依赖 | 现状 |
|---|---|---|---|
| **主动服务** | 业务事件（物流异常、服务到期、支付失败）→ 事件总线 → 触发规则表 → Agent 组织通知 → 用户确认后执行 | Outbox + 事件总线 + 触发规则表 | 🟡 已落地一半，且**刻意偏离原设计**：规则表 + 七道抑制闸 + 客户确认在同事务入队已实现；但通知正文由固定模板渲染，**不让 Agent 组织措辞**（模板变量走六键白名单），事件由坐席调 `POST /memory/events` 显式投喂而不是事件总线 |
| **多模态回复** | 支持操作视频、标注截图、语音回复；按渠道能力矩阵降级 | 资产服务 + `ChannelCapabilities` | 🔜 只做了降级这一半：`ChannelCapabilities` + `render()` 按渠道裁长度、转卡片、追加转人工入口；没有资产服务，`supports_voice` / `supports_rich_card` 目前只影响文本形态 |
| **跨渠道会话延续** | `conversation_id` 全局唯一，渠道只作属性 | Conversation 模型改造 + 渠道身份映射表 | 🟡 数据结构已就位（`conversations` 不带渠道列，渠道只是 `channel_messages` / 移交包的属性），但**接续不会自动发生**：`Conversations.resolve()` 拿不到显式 `conversation_id` 就新建会话；身份映射是「每渠道一张发件人 → `user_id` 表」，同一客户在两个渠道被映射成两个账号时不会合并 |
| **记忆个性化** | 依据 Profile 调整语气、预填参数、优先推荐上次走通的路径 | 层 3 记忆体系 | 🟡 已落一半（T9，ADR-018）：按 `tone` 裁追问、把 `product_interests` 里**目录认识**的商品摆到首位（够不着本轮答案时才变成一条追问）、第一轮用 `preferred_name` 开头——四条效果各有原因码、可断言、可整体关掉。**没落**的是「预填参数」与「优先推荐上次走通的路径」：后者要的是跨会话的结果统计，而档案表里没有这类数据（写入只来自客户自己）。`history_summary` / `contact_window` 至今不参与生成，是有意的 |
| **坐席副驾** | 同一内核以「建议模式」运行，输出内部建议而非用户回复 | 层 5 接管状态机 + 权限隔离 | 🔜 未开始。当前只有「接管后 Agent 静默」这一半，没有第二条建议输出面，也没有 `mode=suggest` 之类的运行形态 |
| **text-to-sql 扩展** | 从只读聚合扩到「带图表的经营视角」；仍受 D9 白名单约束 | `sql/` 模块 + 图表渲染层 | 🟡 只读聚合已落地（`app/sql/` 四段式 + `sql_queries` 台账），**没有图表渲染层**：控制台只有 React Router 一个运行时依赖，结果以表格 + 计划/耗时卡片呈现 |

---

## 第 5 章 横切机制

> 横切机制的特点是「每一层都要用，但只能有一份」。本章的每条机制都给出**唯一实现位置**，避免出现第二套事实源。

### 5.1 配置与策略引擎

| 项 | 约定 |
|---|---|
| 唯一实现 | `app/core/policy.py`（`PolicyEngine`）+ `app/core/config.py`（`Settings`） |
| 阈值清单（13 项） | `refund_window_days=30`、`refund_max_amount_cents=5_000_000`、`refund_approval_threshold_cents=0`、`escalation_frustration_rounds=2`、`handoff_after_failed_attempts=2`、`escalation_no_evidence_score=0.35`、`session_idle_minutes=20`、`handoff_claim_timeout_seconds=120`、`handoff_repeat_window_hours=24`、`abandon_idle_minutes=30`、`handoff_digest_max_chars=400`、`approval_ttl_minutes=30`、`policy_min_score=0.01` |
| 铁律 | 代码中**不得**出现同类魔法数字；改阈值只改配置 |
| 灰度 | 阈值带 `refund_policy_version`（当前 `refund-policy-v3`）；下一版策略与当前版并存，按租户/会话选择 |
| 校验 | 启动期 `validate_production_safety()`：生产禁用 SQLite、禁 `auto_create_tables`、强制 OIDC、强制 http 退款 provider、强制关闭文档、稠密检索需真实 embedding 配置 |

### 5.2 幂等键

| 场景 | 键构造 | 落点 |
|---|---|---|
| 建工单 | `idempotency_key`（请求显式带）+ 租户 + 用户 + 动作 | `tool_executions` 唯一约束 |
| 退款 | 订单级部分唯一索引（ADR-008）+ 行锁 + 余额条件更新 | `refunds` / `tool_executions` |
| 任务创建 | 动作 + 租户 + 用户 + 业务键 | `tasks` |
| 渠道消息 | `channel_message_id` | `channel_messages`（规划） |

**铁律**：幂等命中返回**同一结果**（不报错、不重复副作用）；幂等键与订单版本绑定（审批后订单变了 → 审批 `INVALID`）。

### 5.3 Outbox

```text
业务写（同一事务）→ 写 outbox 行 → 后台 worker 轮询 → 推送外部系统 → 标记 sent
                                   ↘ 失败：retry_count++，退避重试，超过阈值进死信 + 告警
```

**为什么现在做**：外部退款/物流系统抖动时，「本地已改、外部未改」是最难排查的一类问题。Outbox 让「最终一致」可证明。
**当前状态**：✅ Phase 1 已落地。表 `outbox_messages`（迁移 `c4f1b8d2e306`）；`app/models/outbox.py`、`app/repositories/outbox.py`、`app/services/outbox.py`；worker `scripts/outbox_worker.py`；坐席 API `GET /api/v1/outbox`、`POST /api/v1/outbox/deliver`；ADR-013。
**已落地的语义**：消息与业务事实同事务写入（`dedupe_key` 唯一约束保证同一事实只排一条）；worker 先提交认领再推送（at-least-once），推送失败 `retry_count++` + 指数退避，超过 `max_attempts` 进死信（`FAILED`）。状态机为 `PENDING → PROCESSING → SENT / FAILED`，另有 `CANCELLED`（删除档案时把未送达的主动通知一起作废）。**事件类型现有两种，两种都有消费者**：`refund.pending_confirmation`，消费者是**对账**——确认成功 / 失败 / 需人工 ⇒ 投递完成，仍是 `PENDING_CONFIRMATION` ⇒ **不算投递**，消息回到 `PENDING` 等下一轮；`proactive.confirmed`（T5 入队，T7-4 起由 `app/channels/outbound.py` 的 `ProactiveSender` 渲染并投递）。默认 `CHANNEL_OUTBOUND_TRANSPORT=none` 拒绝一切发送，因此没有配置出站通道时 `SENT` 不可能出现；演示用的 `local_file` 把渲染结果追加成一行 JSON，实测 `claimed=1 / sent=1` 且通知落 `SENT`。（T7-4 之前那轮记录的 `claimed=1 / sent=0 / failed=1` 是「没有 sink」时期的读数，现已不成立。）
**待建**：真实服务商协议（SMTP / 微信 / 企业微信）作为 `OutboundTransport` 的第四个实现、送达回执这一独立状态、更多事件类型（物流通知、渠道送达回执）、监控告警接入。状态里没有独立的 `DEAD_LETTER`，死信与单次失败共用 `FAILED`，看板只能靠 `retry_count` 区分。

### 5.4 观测与告警

| 维度 | 内容 |
|---|---|
| 轨迹 | `trace_spans`（8 类 span），`GET /api/v1/traces/{trace_id}` 可完整回放 |
| 脱敏 | `app/observability/redaction.py`，写入前统一处理 |
| 指标 | `HandoffMetrics`（会话/转人工/自动解决/开放案件/队列/原因/信号/时段/弃单），API `GET /api/v1/handoffs/metrics` |
| 告警（规划） | 幻觉率突增、工具连续失败、对账积压、弃单突增、安全拦截突增、**SQL 拒绝率突增**（新增）、转人工队列积压 |
| 采集纪律 | 观测写失败**绝不影响主链路**（`TraceRecorder` 明确不抛） |

### 5.5 降级与熔断

| 机制 | 规则 |
|---|---|
| 重试 | 仅对可重试错误（超时、5xx）；退避 + 次数上限写在 `ToolManifest.retry` |
| 不重试 | 业务拒绝（超期/余额不足）、参数非法、**结果未知**、**SQL 越界** |
| 熔断 | 连续失败达阈值 → 打开 → 冷却 → 半开探测（重排通道已有：`rerank_failure_threshold=3`、`rerank_cooldown_seconds=30`） |
| 降级链 | `ToolManifest.degradation` 有序执行；每一步都在 Trace 留痕 |
| 兜底 | 降级链走完 → 转人工 + 完整上下文（**不允许**「我不知道」式回复） |

### 5.6 成本控制

| 成本源 | 控制手段 |
|---|---|
| LLM 调用 | `agent_mode=rules` 默认（零模型调用）；`agent_request_limit=2` 限次；`agent_timeout_seconds=20` 超时 |
| 模型选型 | `agent_model=anthropic:claude-sonnet-4-5`；升级需评估影响面（D6 双轨评测） |
| 检索 | `policy_max_results=3`、`retrieval_candidate_pool=20`、`policy_min_score` 提前截断 |
| SQL | 强制 `LIMIT ≤ 200`；超限查询直接拒绝，不做「先查后裁剪」 |
| Token | 回执与摘要由模板渲染（`handoff_digest_max_chars=400`），不靠模型复述 |

---

## 第 6 章 落地路线

### 6.1 Phase 总表

| Phase | 主题 | 关键交付 | 出口验收 | 状态 |
|---|---|---|---|---|
| **Phase 0** | 契约统一 | 意图目录、Tool Manifest v2、Trace 骨架、策略引擎 | 新增意图只改 YAML；非法参数在 validate 段被拦；一次请求可查完整 span 树；阈值改配即生效 | ✅ 已完成 |
| **Phase 1** | 执行能力补全 | **受控 Text-to-SQL**、Executor 完整化、重试/熔断/降级链、回执模板、Outbox | 12 类越界 SQL 全拒；SQL 聚合问题可答且带租户隔离；五种失败形态无静默 | 🟡 进行中（受控 Text-to-SQL、`sql_queries` 台账、Executor 失败模型 + 重试熔断降级链、回执模板、事务性 Outbox 已落地，两种事件类型都有消费者且 `SENT` 可回写；待补 reserve/settle 独立段、真实服务商推送协议与送达回执、更多消息类型） |
| **Phase 2** | 人机协同 | 8 信号、`HandoffBundle`、接管状态机、坐席 API、Abandon 监控、坐席 / 运营 / Trace 控制台 | 转人工后坐席首屏即完整上下文；接管期间 Agent 零输出；弃单率可切片；接管 / 交还 / 结案可只靠控制台完成 | ✅ 已完成 |
| **Phase 3** | 接入与安全 | `ChannelAdapter` + Web/邮件/微信、渠道响应矩阵、输入输出护栏、PII 流水线、权限 scope 显式化 | 新渠道零改认知层；注入与越权用例全绿；日志无明文 PII | 🟡 大部分完成（护栏、限流与体积上限、`security_events`、渠道抽象 + 渲染矩阵 + 数据库级幂等 + 渠道溯源已落地；「新渠道零改认知层」由 `tests/test_channels.py` 20 条守住，网页两条入口的幂等闸已由 T8 统一。待做：真实平台协议、标准 PII 识别流水线、权限 scope 交集显式化、APP/WhatsApp/Voice/SMS 的适配器） |
| **Phase 4** | 记忆与评测 | Profile 记忆三层、`/memory` 读写接口、Golden 150+ 与轨迹断言、回放评测、指标看板、bad case 池 | 记忆可读可删；Golden ≥ 150 且含轨迹断言；指标可下钻 | 🟡 评测侧已完成（101 条含轨迹断言、回放 diff、分层抽样、CI 阻断）；记忆侧已落地但**只做两层**：客户自持档案 + 待确认通知（`tests/test_memory.py` 21 条），抽取层按 ADR-014 主动否决；档案进答案已在 T9 落地（四种模板效果 + 原因码可见，ADR-018），所以「记忆没进答案生成路径」这句不再成立；规则的写面已在 T10 落地（坐席改文案 / 冷却 / 启停 + 写时模板校验，ADR-019），但**没有删除 / 版本表 / 双人复核 / 租户灰度**；用例数停在 101，未到 150+ |
| **Phase 5** | 演进 | 事件总线 + 主动服务、跨渠道会话延续、记忆个性化、坐席副驾 | 事件驱动的主动通知可演示且可关停 | 🟡 主动服务已可演示且**默认关**（`proactive_opt_in=False`，七道抑制闸，客户确认才入队，T7-4 起确认后真投递）；规则文案已可由坐席在控制台改（T10，ADR-019）；但触发靠坐席调 `POST /memory/events` 投喂，没有事件总线；`SENT` 只到出站通道边界、不是服务商回执；跨渠道会话延续未做；记忆个性化只做了一半（T9 的四种模板效果可枚举可关停，「按上次走通的路径推荐」没有） |

### 6.2 里程碑演示脚本

> 每个脚本都是**可复现的验收动作**，不是愿景描述。

**M1 · Phase 1 演示：受控 SQL 聚合问答（15 分钟）**

```text
1) 提问「上个月退款的订单有多少笔？」          → Agent 生成白名单 SELECT，注入 tenant_id，返回计数
2) 提问「把订单表删了」                       → 意图不匹配 → 不进 SQL
3) 直接构造越界 SQL「SELECT * FROM users」     → 执行器拒绝，记 rejected_reason，回复「无法确认 + 转人工」
4) 切到另一租户重问                          → 计数只含本租户
5) 查看 GET /api/v1/traces/{trace_id}          → 能看到 sql span：SQL 文本、参数摘要、行数、耗时
```

**M2 · Phase 2 演示：转人工闭环（10 分钟）**

```text
1) 用户说「转人工」                            → 命中 user_request(P0)，产生 HandoffBundle
2) 坐席 GET /api/v1/handoffs                   → 队列里看到该案（默认只列开放案件）
3) 坐席 POST /{id}/claim                        → 状态转 HUMAN_ACTIVE
4) 用户继续说话                                → Agent 静默，零输出
5) 坐席 POST /{id}/release                      → Agent 恢复作答
6) GET /api/v1/handoffs/metrics                → 转人工率、按原因/信号/时段切片
```

**M3 · Phase 3 演示：第二渠道接入（20 分钟）**

```text
1) 打开 CHANNEL_EMAIL_ENABLED，配 CHANNEL_EMAIL_SECRET 与 CHANNEL_EMAIL_IDENTITY_MAP
   （值必须写成 "tenant:user"，写错不会 403 而是 503）
2) 用 HMAC 签一条请求体 POST /api/v1/channels/email/webhook
   （X-Channel-Timestamp + X-Channel-Signature: sha256=<hex>，超出 max_skew 401）
3) 同一句话经 /channels/web/messages 与邮件 webhook 两条路径，回答一致但形态不同
   （邮件更长、卡片转文本、追加 SLA 与转人工入口）
4) 重复投递同一 channel_message_id → 无第二条回复，响应头 X-Channel-Deduplicated: true
```

> **这一版已落地，与最初设想不同的两点**：适配器不是"演示时现写"，`EmailChannelAdapter` 与 IM 适配器已在 `app/channels/`；签名格式是**本项目自定义**的，接真实邮件服务商/微信时必须换成平台方案。幂等面已在 T8（2026-10-24）收口：控制台前端改调 `/api/v1/channels/web/messages` 并自带幂等键，旧路由 `/support/messages` 委托同一个 `ChannelGateway.accept_web`，**不再有「演示只能用新入口」这条限制**；仍然成立的是「去重需要调用方给一个稳定的 `X-Channel-Message-Id`」。

**M4 · Phase 4 演示：可证明的解决率（20 分钟）**

```text
1) 跑一次退款全流程 → tasks + verification + trace_spans 全齐
2) 用 trace_id 还原：意图 → 检索 → 工具 → 审批 → 结论
3) 抽取该会话算作「可验证解决」
```

### 6.3 前置条件

| 类别 | 条件 | 缺失后果 |
|---|---|---|
| 环境 | Python 3.13 + `.venv`（含 ruff 0.17.2 / mypy / pytest）；Node 20+ 与 `web/` 依赖 | 门禁无法复现；**注意：系统 Anaconda 的 ruff 0.12.0 会误报 5 个 UP038** |
| 数据库 | 开发用 SQLite；生产用 PostgreSQL（`alembic upgrade head`，禁止 `auto_create_tables`） | 生产启动直接失败（fail-closed，符合预期） |
| 迁移 | `alembic check` 必须无漂移 | 门禁阻断 |
| 外部依赖 | 稠密检索需 `EMBEDDING_BASE_URL` + `EMBEDDING_MODEL` + key；重排需 `RERANK_URL` | 生产启动校验失败；开发可关闭（离线降级） |
| 身份 | 生产必须 OIDC；开发用 `dev_headers` | 生产启动校验失败 |
| 数据 | `python scripts/seed.py` 造商品/政策/订单/物流样本 | 检索与演示无数据 |
| 评测 | `evals/golden_set.json`、`evals/support_set.json`（或从抽样队列补充） | 门禁无基线 |

---

## 第 7 章 质量与验收

### 7.1 测试金字塔（现状实测）

```text
                        ┌──────────┐
                        │  门禁    │  ruff + mypy(strict) + pytest + Golden + 支持路径
                        │  (9 道)  │  + 检索评测 + 回放自检 + alembic check + npm build
                     ┌──┴──────────┴──┐
                     │  评测集 101 条   │  Golden 40（结果 + 轨迹）+ support 61（路径）+ 检索指标
                  ┌──┴─────────────────┴──┐
                  │    集成测试 19 文件     │  handoff / approvals / refunds / tasks / support
               ┌──┴────────────────────────┴──┐
               │      单元测试（目录/策略/路由） │  intent_catalog / policy_engine / routing /
               │                                │  tool_manifest / trace / retrieval_channels
            ┌──┴────────────────────────────────┴──┐
            │  契约测试：生产 fail-closed、越权、PII │  governance / postgres_concurrency
            └──────────────────────────────────────┘
```

| 层 | 当前 | 目标 |
|---|---|---|
| 单元（目录/策略/路由/工具/轨迹/检索/SQL 守卫/评测集与 runner 自身） | 12 个文件（`intent_catalog`、`intent_rules`、`policy_engine`、`routing`、`tool_manifest`、`trace`、`trace_sampling`、`retrieval_channels`、`retrieval_eval`、`sql_guard`、`eval_datasets`、`golden_runner`） | 覆盖率按模块补齐，目录与策略必须 100% 分支 |
| 集成（TestClient + 临时库：会话/任务/审批/协同/渠道/记忆/执行器/出站） | 16 个文件（`tasks_api`、`tickets`、`refunds`、`approval_queue`、`handoff`、`outbox`、`support`、`support_conversation`、`pydantic_agent`、`channels`、`memory`、`tool_executor`、`tool_failures`、`sql_analytics`、`outbound`、`personalization`）；`personalization` 里 2 条是纯函数单测（现造 `Draft` 直接断言重排），其余走 HTTP | 每条动作、每个信号、每个失败形态各有用例 |
| 契约（生产 fail-closed / 护栏 / Postgres 并发与竞态） | 4 个文件（`governance`、`security_guard`、`postgres_concurrency`、`postgres_outbox`） | 注入、越权、SQL 越界各成组 |
| 评测 | 101 条（40 + 61）+ 检索指标（bm25 与 hybrid 两通道）+ 回放 + 抽样 | 150+ 且保持轨迹断言与回放自检 |

> 上表的三层是**阅读用的归属**，不是仓库里的强制结构：`tests/` 是平铺的 32 个文件，
> pytest marker 虽已声明但没有实际使用，也没有 unit/integration 目录分层（与 §14.1 同一口径）。

### 7.2 CI 门禁（3 个 job，全绿才可合并）

`api` job（`ubuntu-latest` + Python 3.12，与本地 3.13 不同解释器）：

| # | 门禁 | 命令 | 当前状态 |
|---|---|---|---|
| 1 | 静态检查 | `ruff check app tests scripts evals alembic` | ✅（注意范围是这五个目录，不是整仓 `.`） |
| 2 | 类型检查 | `python -m mypy app`（`strict = true`，137 文件） | ✅ |
| 3 | 单元与集成 | `python -m pytest` | ✅（SQLite 基线 496 passed / 6 skipped；跳过的 6 条需要 `TICKETPILOT_TEST_POSTGRES_URL` 或 `TICKETPILOT_TEST_REDIS_URL`，CI **没有注入**，所以 Postgres / Redis 专项只在一次性容器里手工跑。2026-10-25 已在 PostgreSQL 16.15 + Redis 两个容器上补跑全量：**502 passed / 0 skipped**（119.71 s），T4–T10 新增用例（含 `test_personalization.py` 18 条、`test_proactive_rules.py` 21 条、`test_llm_gap.py` 9 条）与 `test_refunds.py` 1 条全部覆盖） |
| 4 | 任务路径 | `python evals/run.py` | ✅（40/40，含轨迹断言） |
| 5 | 只读路径 | `python evals/support_run.py` | ✅（61/61） |
| 6 | 检索评测 | `python evals/retrieval_metrics.py --channels bm25,hybrid` | ✅（GATE PASSED；门槛只套第一个渠道 `bm25`，`hybrid(hashing)` 行是对比编排） |
| 7 | 回放自检 | `python evals/replay.py --self-check` | ✅（REPLAY DIFF EMPTY） |
| 8 | 迁移漂移 | `alembic -x db_url=sqlite:///./ci.db upgrade head` + `alembic check`（全新库） | ✅（No new upgrade operations detected） |

`web` job：`npm ci` + `npm run build`（内含 `tsc --noEmit`）— ✅。

`postgres-migration` job：在 `postgres:16-alpine` 服务上 `alembic upgrade head` + `alembic check` — ✅。
**它只验迁移链，不跑 pytest**，所以「Postgres 上测试全绿」不是 CI 结论；2026-10-25 本地 PostgreSQL 16.15 + Redis 两个一次性容器上的全量是 **502 passed / 0 skipped**（119.71 s；Golden 40/40、支持集 61/61 同轮），但那是一次手工复跑，不是 CI 门禁。

> **纪律**：所有命令用 `.venv/Scripts/...` 执行；CI 与本地用同一套命令，避免「本地过了线上红」。

### 7.3 上线 checklist（8 条，逐条打勾）

- [ ] **① 契约齐全**：新增能力在 `intents.yaml` 有行、在 `actions.py` 有动作、在 `ToolManifest` 有参数 Schema、在 Golden Set 有用例。
- [ ] **② 无静默失败**：本次涉及的失败形态都有用户可见结果；没有「吞异常返回空」。
- [ ] **③ 依据完整**：政策类回答 100% 带引用；无依据用例 0 幻觉。
- [ ] **④ 边界正确**：越权一律 404/403 且不泄露存在性；SQL 一律带 `tenant_id` 且只读。
- [ ] **⑤ 隐私无泄漏**：日志、Trace、评测样本、SQL 结果中无明文手机号/地址/密钥。
- [ ] **⑥ 可追溯**：任一请求可用 `trace_id` 还原「意图 → 检索 → 工具 → 审批 → 结论」。
- [ ] **⑦ 可回滚**：本版本新增的表/列/配置项都有回滚路径；策略阈值改动可单独回退。
- [ ] **⑧ 有影响面报告**：本次改动命中了哪些 Golden 用例、哪些指标可能变化、风险是什么。

### 7.4 上线与回滚

| 阶段 | 动作 | 回滚 |
|---|---|---|
| 发布前 | 7 道门禁全绿 + 影响面报告 + 迁移脚本 dry-run | — |
| 灰度 | 按租户/渠道小流量；策略版本可按租户切换 | 策略切回旧版本 |
| 观察期 | 看：P95 延迟、错误率、幻觉抽样、转人工率、SQL 拒绝率、对账积压 | 触发阈值即回滚 |
| 回滚 | 代码回滚到上一镜像 + `alembic downgrade`（仅当有破坏性迁移） | 本项目迁移均为**新增表/列**，回滚不删数据，安全 |

> **迁移策略**：只做**向后兼容的增量迁移**（加表、加列、可空列）。破坏性变更分两次发版：先加后用，第二次再删。生产禁用 `auto_create_tables`，一律走 Alembic。

---

## 第 8 章 风险清单

### 8.1 技术风险

| # | 风险 | 触发信号 | 影响 | 缓解 | 状态 |
|---|---|---|---|---|---|
| T1 | **SQL 执行面被绕过**（模型直连库 / 白名单被扩太大） | 出现非白名单表、缺租户过滤 | 跨租户数据泄露 | 强制注入租户过滤 + AST 白名单 + 参数化 + `LIMIT` 上限 + 拒绝即告警 + 越界用例 | ✅ 已落地：守卫（`app/sql/guard.py`）+ 数据库第二道（PG `SET TRANSACTION READ ONLY` / SQLite `PRAGMA query_only=ON`）；**残留风险**是自研解析器只覆盖单条 `SELECT`，放开 `WITH`/子查询/`UNION` 时白名单与作用域复核必须同步扩展，且没有租户级配额与 `sql_queries` 保留期清理 |
| T2 | **嵌套事务死锁**（SQLite 锁 / PG 阻塞） | `database is locked`、请求挂起 | 请求失败 | 写路径独立事务；Agent 侧写先提交；`busy_timeout` 兜底 | ✅ 已修（`support_agent.handle` 提交时机 + `PRAGMA busy_timeout=5000`） |
| T3 | **稠密/重排端点不稳定** | 检索超时率上升 | 回答变慢 | 失败开放（fail-open）到 BM25；熔断 + 冷却；生产启动校验禁止用 hash 替身 | ✅ 已设计 |
| T4 | **意图目录与枚举双写漂移**（TD1 未清） | 新意图只在枚举里加 | 路由漏判 | 目录为唯一事实源；`SupportIntent._missing_` 容忍新增；下轮删除 `Intent` 枚举 | 🟡 待清理 |
| T5 | **Trace 写库拖慢主链路** | P99 延迟上升 | 吞吐下降 | 缓冲 + 批量 flush；Trace 异常不抛 | ✅ 已设计 |
| T6 | **枚举/目录扩容引发匹配顺序回归** | Golden 掉用例 | 路由错 | `priority` 显式排序 + Golden 覆盖意图 × 优先级 | ✅ 已设计 |
| T7 | **PII 泄漏到新通道（SQL 结果 / 摘要）** | 抽样出现手机号 | 合规事故 | 统一 `redaction` 出口；SQL 结果脱敏；自动化扫描断言 | 🔜 待建扫描 |
| T8 | **生产配置误配** | 启动校验失败 | 起不来 | fail-closed 启动校验（已实现 6 项） | ✅ 已落地 |

### 8.2 业务风险

| # | 风险 | 触发信号 | 影响 | 缓解 |
|---|---|---|---|---|
| B1 | **过度转人工**（Agent 没用，退化为人肉） | 主动转人工占比下降、队列积压 | 成本上升 | 跟踪 M4「主动转人工占比」，把它当健康度而非成本项看 |
| B2 | **假解决（False Deflection）** | 声称已解决但 7 日内复联 | 客诉、监管风险 | M5 指标 + 对账校验 + 未闭环不报「已解决」 |
| B3 | **静默弃单** | 弃单率上升 | 用户流失 | `abandon_events` 监控 + 空闲与重复意图双检测 + 告警 |
| B4 | **投诉被当作普通工单**（走错队列） | 投诉类案件停留时间长 | 处置超时、舆情 | `complaint` 信号 P0 + `COMPLAINT` 专席 + `continue_task` 保留工单 |
| B5 | **退款越权/越窗** | 审批链缺失、金额异常 | 直接资金损失 | 服务端资格判定 + 四眼审批 + 订单级并发防护 + 风险信号记账 |
| B6 | **用户被静默接管而不自知** | 接管后用户仍收到机器人回复 | 体验崩塌、投诉 | 接管期 Agent 零输出；release 后续上下文；移交话术明确告知 |
| B7 | **SQL 聚合口径错误导致错误经营判断** | 聚合数与台账对不上 | 决策失误 | SQL 结果可下载核对 + 台账交叉校验 + 关键口径写进 `docs/EVALS.md` |
| B8 | **用户重复提交被当成重复问题** | 同一意图 24h 内反复进线 | 误判弃单 | `REPEAT_INTENT` 与「用户主动再问」区分：先看是否已有解决标记 |
| B9 | **主动服务变成骚扰**（T5 新增面） | 同一客户短期内收到多条提案 | 投诉、退订、监管风险 | 默认沉默 + 七道抑制闸（无规则 / 未同意 / 已退订 / 重复 / 冷却 / 当日 `max_notifications_per_day` / 模板缺上下文）+ `requires_confirmation` 先出提案、客户确认才入队。**残留风险**：抑制顺序把入参 422 排在闸门之后，调用方可能只看到 `NO_RULE` 而不知道自己的 `event_key` 非法；且没有跨渠道频控合并 |
| B10 | **渠道入口与旧入口行为不一致**（2026-10-24 由 T8 收口） | 幂等语义只在新入口生效，同一条消息在旧入口可重复受理 | 迁移期已在 `docs/NEXT_STEPS.md` T4 偏差 2 与本文件风险表同时声明差异；T8 让两条 web 入口共用 `ChannelGateway.accept_web`，同一投递键只有一道闸，控制台改调 `/channels/web/messages` 并自带 `dlv_*` 键。**残留边界（不是本风险的复发）**：幂等需要调用方显式给 `X-Channel-Message-Id`，不带键的直接调用仍是一次调用跑一轮——服务端不从「文本长得像」推断重复 |

### 8.3 缓解措施落地状态

| 措施族 | 已落地 | 本轮 | 待建 |
|---|---|---|---|
| 边界（目录/策略/Manifest/Trace） | ✅ 全部 | — | — |
| 协同（信号/移交/接管/坐席 API/工作台） | ✅ 全部 | — | 正确转人工率采集、Abandon 自动化巡检 |
| 执行（网关/台账/幂等/对账） | ✅ 全部 | Outbox、Executor 完整化、SQL 执行器、两种事件类型各有消费者（`proactive.confirmed` 由 `ProactiveSender` 投递） | 真实服务商推送协议与送达回执、更多消息类型、reserve/settle 独立段 |
| 安全（身份/租户/审批/fail-closed/脱敏） | ✅ 核心 | SQL 白名单、输入/输出护栏、限流与体积上限、安全事件看板 | PII 流水线、附件白名单/EXIF 剥离、扫描断言、跨进程配额 |
| 评测（Golden/检索/Trace 落库） | ✅ 基础闭环 | 轨迹断言、回放 diff、分层抽样、CI 阻断 | 150+ 用例、bad case 自动回灌、指标下钻 |
| 渠道（入站抽象/验签/幂等/渲染降级 + 出站投递） | ✅ 抽象 + 4 个入站适配器 + 1 个出站渲染投递面（`WEB` / `EMAIL` / `WECHAT` / `WECOM`；`APP`、`VOICE`、`SMS`、`WHATSAPP` 只声明能力矩阵，入站一律 404、出站没有地址来源） | T4：`Channel` 8 值 + `ChannelCapabilities` 8 字段 + `render()` 降级 + HMAC 验签 + 身份映射 + `channel_messages` 数据库级幂等；T4b：渠道进移交包与 `handoff_events.channel`；T7-4：`ProactiveSender` 用同一张能力表渲染并投递，地址从服务端映射**反查** | 服务商协议（`OutboundTransport` 只有 `none` / `local_file`）、平台原生签名方案、IP 白名单与密钥轮换、渠道级配额 |
| 记忆与主动服务（档案/规则/通知/抑制/投递） | ✅ 服务端闭环 + 客户与坐席界面 + 档案进答案 + 规则写面 | T5：3 张表 + 9 个接口 + 七道抑制闸 + 客户确认同事务入队 + 删除即作废未送达消息；T7-1/2/3：渠道矩阵页与「我的记忆」页（含待确认通知的同意 / 取消 / 推送渠道选择）；T7-4：确认后真投递；T9：档案以四种模板效果进答案，响应与 trace 只报原因码与字段名；T10：`PUT /memory/rules/{event_type}` 开规则写面（文案 / 冷却 / 启停）+ 写时模板校验 + 控制台「通知规则」页 | 规则的**删除**、版本表 / 一键回滚、**双人复核**、按租户灰度，事件总线、服务商侧送达回执、把档案注入提示词的模型侧个性化（刻意不做，ADR-018） |

---

## 第 9 章 附录

### 9.1 术语表

| 术语 | 含义 |
|---|---|
| **意图（Intent）** | 用户诉求的分类，由 `intents.yaml` 一行定义，决定动作、槽位、风险与兜底 |
| **槽位（Slot）** | 执行动作所需的参数，如 `order_id`、`amount_cents`；有类型、格式、追问话术、敏感标记 |
| **动作（Action）** | 可执行的能力实现，注册在 `actions.py`，返回 `Draft`（回复草稿） |
| **工具（Tool）** | 领域侧的副作用封装，注册在 `app/tools/builtin.py`，带 Manifest v2 全部契约 |
| **草稿（Draft）** | 动作返回的结构化回复，含意图、卡片、引用、话术；由渲染器成文 |
| **策略（Policy）** | 阈值与业务规则的集合，来自配置，不写在代码里 |
| **升级信号（Escalation Signal）** | 触发转人工的 8 类判定之一，来自 `escalation.yaml` |
| **移交包（HandoffBundle）** | 转人工时交给坐席的上下文快照，18 字段 |
| **接管（Takeover）** | 人工坐席接手会话；接管期 Agent 静默 |
| **记账（NOTED）** | 只记录不接管（风险信号、空闲超时） |
| **弃单（Abandon）** | 用户既没解决也没转人工就离开 |
| **台账（Ledger）** | 工具执行与副作用的持久记录，幂等与对账的依据 |
| **对账（Verification）** | 用外部事实核对系统状态是否真的成功 |
| **轨迹（Trace）** | 一次请求的 span 树，可完整回放 |
| **门禁（Gate）** | 阻断合并的自动化检查 |
| **无依据（No Evidence）** | 检索没有可用依据的状态，此时不生成 |
| **受控 SQL** | 只读、白名单表、强制租户过滤、强制 LIMIT 的 SQL 执行能力 |
| **假解决（False Deflection）** | 声称已解决但实际未解决或很快复发 |

### 9.2 接口清单

**现状 45 个操作 / 42 条路径**（`app/api/v1/` 43 个 + 健康检查 2 个，按 OpenAPI tag 分组）：

| 分组 | 操作 | 角色 |
|---|---|---|
| 健康（2） | `GET /health/live`、`GET /health/ready` | 公开 |
| 支持（3） | `POST /api/v1/support/messages`、`GET /api/v1/support/cards/{kind}/{card_id}`、`GET /api/v1/support/cards/{kind}/{card_id}/sections/{section_id}` | 客户 |
| 渠道（3） | `GET /api/v1/channels`（能力矩阵）、`POST /api/v1/channels/web/messages`、`POST /api/v1/channels/{channel}/webhook` | 前两个客户；webhook 走验签，不带用户身份头 |
| 任务（6） | `POST /api/v1/tasks`、`GET /api/v1/tasks`、`GET /api/v1/tasks/{id}`、`POST /{id}/approve`、`POST /{id}/cancel`、`GET /{id}/events` | 客户（approve 需审批角色） |
| 工单（2） | `POST /api/v1/tickets`、`GET /api/v1/tickets/{id}` | 客户 |
| 审批（2） | `GET /api/v1/approvals`、`GET /api/v1/approvals/{id}` | 审批角色 |
| 轨迹（2） | `GET /api/v1/traces/metrics`、`GET /api/v1/traces/{trace_id}` | 坐席（`support`）/ 管理员；客户仅本人 |
| 协同（7） | `GET /api/v1/handoffs/metrics`、`GET /api/v1/handoffs/abandons`、`GET /api/v1/handoffs`、`GET /api/v1/handoffs/{id}`、`POST /{id}/claim`、`POST /{id}/release`、`POST /{id}/close` | 坐席（`support`） |
| 台账（4） | `POST /api/v1/analytics/query`、`GET /api/v1/analytics/llm-usage`、`GET /api/v1/analytics/queries`、`GET /api/v1/analytics/queries/{query_id}` | 客服 / 管理员 |
| 消息（2） | `GET /api/v1/outbox`、`POST /api/v1/outbox/deliver` | 坐席（`support`）/ 审批角色 |
| 安全（2） | `GET /api/v1/security/metrics`、`GET /api/v1/security/events` | 坐席（`support`）/ 审批角色 |
| 记忆与主动服务（10） | `GET` / `PUT` / `DELETE /api/v1/memory`、`GET /memory/export`、`GET /memory/rules`、`PUT /memory/rules/{event_type}`、`POST /memory/events`、`GET /memory/notifications`、`POST /memory/notifications/{id}/confirm`、`POST /memory/notifications/{id}/cancel` | 档案与通知只操作**当前身份自己**的；`POST /memory/events`、`GET /memory/rules` 与 `PUT /memory/rules/{event_type}` 额外要求 `support` 角色，其余客户本人即可 |

**原规划清单的兑现情况**（现状 45 个操作，已超出原「目标 30+」）：

| 规划操作 | 用途 | 期次 |
|---|---|---|
| `POST /api/v1/analytics/explain` | 返回生成的 SQL 与白名单判定结果（可解释性） | 待做（当前只在 `sql_queries` 台账与卡片里回显计划与判定，没有独立解释端点） |
| 长期记忆可读可删 | 档案读写与彻底删除 | ✅ 已实现，但**路径与规划不同**：不是 `/memory/{user_id}`，而是 `/api/v1/memory`（只操作当前身份本人），另配 `GET /memory/export`、`GET /memory/rules`、`PUT /memory/rules/{event_type}`、`POST /memory/events`、`GET /memory/notifications` 与 `confirm` / `cancel`，共 10 个操作 |
| `POST /api/v1/channels/{channel}/webhook` | 第二渠道入口 | ✅ 已实现（邮件 / 企业微信 / 微信三类适配器共用这一条路由，先验签再解析；关闭或未识别渠道 404） |
| `GET /api/v1/metrics/summary` | 平台级指标汇总（M1–M10） | 待做（当前按域分散在 `handoffs/metrics`、`traces/metrics`、`security/metrics`、`analytics/queries` 四个接口） |
| `POST /api/v1/evals/replay` | 回放评测入口 | 待做（回放是离线脚本 `evals/replay.py`，没有服务化） |

> 安全域两个操作（`GET /api/v1/security/events`、`GET /api/v1/security/metrics`）已在上面的现状清单里，不再重复。

### 9.3 指标口径（可执行定义）

```sql
-- M1 可验证解决率
-- 分子：会话末轮有 resolved 标记 ∧ 该会话关联的 task.verification_status ∈ (VERIFIED_SUCCESS, PARTIAL_SUCCESS)
--       ∧ 该会话 7 日内无同因复联（同因 = 同 tenant+user+intent 且 7 日内新建）
-- 分母：period 内所有 tenant+user 维度的去重会话数

-- M2 自动解决率
-- 分子：会话无 handoff_events 行 ∧ 末轮 resolved
-- 分母：period 内总会话数

-- M3 转人工率
-- 分子：存在 handoff_events 行的会话数
-- 分母：period 内总会话数

-- M4 主动转人工占比
-- 分子：reason = 'USER_REQUEST' 的 handoff_events 行
-- 分母：全部 handoff_events 行

-- M6 弃单率
-- 分子：abandon_events 中去重 conversation_id 数
-- 分母：period 内总会话数

-- M8 工具选择正确率
-- 分子：trace_spans(kind='tool') 中 name 与期望动作一致的 span 数
-- 分母：全部 kind='tool' 的 span 数
```

> **口径纪律**：口径改动必须同步改 `docs/EVALS.md` 与本节；改口径的 PR 必须说明历史数据是否需要重算。

### 9.4 ADR 索引

| 编号 | 标题 | 状态 |
|---|---|---|
| ADR-001 | Workflow First（工作流优先于自由 Agent） | 已接受 |
| ADR-002 | Bounded Agent with PydanticAI（有界模型节点） | 已接受 |
| ADR-003 | Server-Side Refund Rules（服务端退款规则） | 已接受 |
| ADR-004 | Approval Binding（审批绑定订单版本） | 已接受 |
| ADR-005 | Ledger and Reconciliation（台账与对账） | 已接受 |
| ADR-006 | PostgreSQL Target / SQLite Dev（目标 PG、开发 SQLite） | 已接受 |
| ADR-007 | Verifier as External Truth（对账器以外部事实为准） | 已接受 |
| ADR-008 | Order-Level Refund Guard（订单级退款并发防护） | 已接受 |
| ADR-009 | Intent Catalog as Single Source of Truth（意图目录为唯一事实源） | 已接受 |
| ADR-010 | Controlled Text-to-SQL Execution（受控 SQL 执行：白名单 AST、强制租户过滤、只读） | 已接受 |
| ADR-011 | Handoff as First-Class Citizen（转人工一等公民与接管静默） | 已接受 |
| ADR-013 | Outbox for State Synchronization（状态同步 Outbox） | 已接受 |
| ADR-014 | 长期记忆只存客户自己写的，主动服务必须客户点头（T5 落地时占用预留号位；原标题「记忆三层」已按实际实现改写） | 已接受 |
| ADR-015 | Executor Failure Model（工具执行器失败模型与回执契约） | 已接受 |
| ADR-016 | 渠道抽象——协议差异停在边缘，Agent 只认一种消息（T4 落地） | 已接受 |
| ADR-017 | 主动通知的出站接缝——去向由服务端拼，`SENT` 只到通道边界（T7-4 落地） | 已接受 |
| ADR-018 | 记忆进答案只允许四种模板效果，读路径不走 `MemoryService`（T9 落地，收 ADR-014 自己点名的后果） | 已接受 |
| ADR-019 | 主动规则的开写接口与写时校验（T10 落地，收 ADR-014 自己点名的后果） | 已接受 |
| ADR-020 | 对话入口只在规则读不懂时问模型，而且只买一张待审退款单（「对话入口接通模型」轮落地） | 已接受 |
| ADR-021 | 稠密通道按 BM25 的盲区触发，而不是按分数开关 | 已接受 |
| ADR-022 | 用「分布快照」和「安全×采纳交叉」两把尺量模型，而不是用单条示例 | 已接受 |
| ADR-023 | 三条接缝跨进程——限流、模型用量、任务幂等 | 已接受 |
| ADR-024 | 模型调用三级门控——规则命中不问、问句不问、真缺口才问 | 已接受 |

**待写 ADR（1 篇）**：

| 编号 | 标题 | 触发时机 |
|---|---|---|
| ADR-012 | Policy Engine & Threshold Governance（策略外置与阈值版本化/灰度） | 阈值灰度能力落地前 |

> 编号说明：ADR-013 与 ADR-015 在 Phase 1 落地时分别占用 Outbox 与执行器失败模型两个编号位；
> ADR-012（策略灰度）仍预留，编号不回收；ADR-014（记忆模型）与 ADR-016（渠道抽象）已分别在
> T5、T4 落地时补齐，ADR-017（出站接缝）随 T7-4 落地，ADR-018（记忆进答案）随 T9 落地，
> ADR-019（规则写面）随 T10 落地，ADR-020（对话入口的模型缺口）随「对话入口接通模型」轮落地，
> ADR-021…ADR-024 随「检索门控 + 三级门控 + 跨进程接缝 + 模型评测」轮落地。
> 当前 `docs/adr/` 共 23 个文件（缺 ADR-012）。

---

## 附录 B：本文档的维护规则

1. **每条 ✅ 必须指得到文件**。写「已具备」却指不到文件 → 视为未具备。
2. **每个 Phase 的出口验收 = 可执行的命令或脚本**，不是描述。
3. **决策表只增不删**：决策被推翻时新增一行并标注「已废弃」，保留推理过程。
4. **口径写死在文档里**：指标口径变更必须同步 §9.3 与 `docs/EVALS.md`。
5. **文档版本与代码版本一起走**：本文档随每次能力交付更新，并在 §0.3 更新终局指标的现状列。
