# 下一步优化方案

## 0. 结论

平台的**后端骨架已经成形**：12 条意图的数据化目录、工具 Manifest 与六类失败形态、转人工移交与接管状态机、混合检索、Outbox、span 树、受控 Text-to-SQL、输入/输出护栏与滥用防护、渠道抽象与客户记忆接口（档案已能进答案，见 §3 T9）、可编辑的主动规则（见 §3 T10）、对话入口的受限模型兜底（见本日 2026-10-25「对话入口接通模型」更新），496 条通过用例全部在位（PostgreSQL + Redis 两个容器同时指上是 502 条、零跳过）。

**2026-10-24 更新**：T1（坐席与运营界面）已完成。坐席工作台、运营看板、Trace 查看器与对话页统计卡片均已接入，客户身份看不到入口，直接输 URL 也会被角色门禁拒绝。

**2026-10-24 更新**：T2（护栏与滥用防护）已完成。输入护栏只记录不改判、输出护栏按来源替换无依据断言、租户/IP 双桶限流与请求体/附件上限全部落地，安全事件已进运营看板。

**2026-10-24 更新**：T3（评测扩量与闭环）已完成。任务路径 40 条 + 只读路径 61 条共 101 条用例（含轨迹断言），`evals/replay.py` 回放自检 diff 为空，`scripts/sample_traces.py` 按渠道×意图分层产出 bad case JSONL，CI 已把 Golden、支持路径、检索指标与回放四项纳入阻断门禁。

**2026-10-24 更新**：T4（渠道抽象）、T4b（渠道溯源）、T5（客户记忆与主动服务）、T6（稠密检索与评测编排）已完成。8 个渠道声明 + 邮件/IM 签名入站 + 幂等表落地，`app/agent/` 与 `app/services/` 零改动；客户自持档案与「先确认后落 Outbox」的主动通知可用；`--channels bm25,hybrid` 能出对比数字。全量 **382 passed / 3 skipped**（收集 385，此为 T4–T6 那轮的读数，当前全量见上一段之前的 §0 与下方 T7 段），并以 74 条真实 HTTP 断言对在线服务做过一次跑通验收（`total=74 passed=74 failed=0`）。

**2026-10-24 更新**：T7（新能力的界面与出口）已完成四件事——控制台加「渠道矩阵」（坐席）与「我的记忆」（客户，含待确认通知的同意/取消与推送渠道选择）；`proactive.confirmed` 接上出站投递（`app/channels/outbound.py`，`SENT` 首次可达，地址只从服务端身份映射反查）；任务上下文补上渠道字段（G11）；`.env.example` 写明两个出站开关。全量 **402 passed / 3 skipped**（收集 405），并在真实 HTTP + 真实浏览器上走完「选渠道 → 确认 → 投递 → 已推送」与四种拒绝。**没做到的**：服务商协议（只有 `none` 与 `local_file`）、规则的可配置界面与写接口（G10）、记忆进答案生成路径（G6）。

**2026-10-24 更新**：T8（把控制台迁到渠道入口，收 T4 留下的 B10 入口分裂）已完成。对话页改调 `POST /api/v1/channels/web/messages` 并自带幂等键；旧入口 `/api/v1/support/messages` 不再是「直接调 Agent」的旁路，而是同一道闸的另一个门面——带 `X-Channel-Message-Id` 就写 `channel_messages`，与渠道路由共用同一个 ID 与同一份已存回复。全量 **408 passed / 3 skipped**（收集 411），23 条真实 HTTP 断言 + 真实浏览器实测「响应丢失 → 原话重发 → 读回同一条回复、`X-Channel-Deduplicated: true`、任务数不变」。**没做到的**：不带幂等键的调用仍是一次调用跑一轮（这是设计，不是漏网——见 §3 T8 偏差 1）。

**2026-10-24 更新**：T9（把记忆接进答案生成，收 G6）已完成。新增 `app/agent/personalization.py`——只套模板的纯覆盖层（四种原因码 `ADDRESSED` / `CARDS_REORDERED` / `SUGGESTIONS_TRIMMED` / `INTEREST_SUGGESTION`），`SupportAgent.handle` 在决策塑形之后、输出护栏之前调一次；档案用**只读列查询**加载，不复用 `MemoryService`，所以每轮多一行 `memory.read` 审计的事没有发生；`MEMORY_PERSONALIZATION_ENABLED` 默认开、关掉逐字回到 T8；响应加可选字段 `personalization`（只有原因码与用过的字段名，没有档案值）。全量 **426 passed / 3 skipped**（收集 429，32 个测试文件），18 条 `tests/test_personalization.py` + 59 项真实 HTTP 断言（同一库开 / 关两个进程）+ 浏览器 DOM 走查。**没有做的**：只有四种效果、不吃客户自由文本；档案一旦存在，那之前的历史 trace 不再逐字可回放（实测 12 条会话里 9 条产出 38 行 diff，已写进 `docs/EVALS.md` 与 ADR-018 的后果段）。

**2026-10-24 更新**：T10（把主动规则开成可编辑面，收 G10）已完成。新增 `PUT /api/v1/memory/rules/{event_type}`——按 `(tenant_id, event_type)` upsert，`extra="forbid"` 只收 `template` / `enabled` / `cooldown_minutes` 三个字段；模板在**写入时**校验：白名单外的键、位置参数 `{}`、属性/下标形式、花括号不配对、超 400 字，全部 422 并点名出错的字段，失败后规则行逐字不变（`requires_confirmation` 想写也写不进去，它是 422 不是静默忽略）。只读视图补 `required_context` 与 `updated_at`；审计 `proactive.rule.update` 只存 `template_sha256` 不存文案。控制台加第 10 页「通知规则」（坐席/管理员可改，客户是拒绝面板）。全量 **447 passed / 3 skipped**（收集 450，33 个测试文件），21 条 `tests/test_proactive_rules.py` + 52 项真实 HTTP 断言（`127.0.0.1:8135` + 干净库 `.tmp/t10_live.db`）+ 真实浏览器 DOM 走查（保存生效、三种拒绝回显、客户身份连请求都不发）。**没有做的**：双人复核（审批那层有，规则文案这层没有）、版本表与一键回滚、按租户灰度、删除规则、规则改动进评测集。**顺带更正一条当初写错的事实**：`{}` 的 parse 字段名是**空串**不是 `None`（`None` 是尾随字面量），按 `None` 判会让所有合法文案一律 422——详见 §3 T10 的「前置核实」。

**2026-10-24 更新**：文档口径收尾轮（去掉跨项目对比表述）已完成。四个检索模块的 docstring、`docs/ARCHITECTURE.md` 检索节与 `docs/PROJECT_SPEC.md` §1.8 整节里指向外部项目的表述全部删掉——**只删来源署名，不删技术事实**，§19.2 补了一张漂移表留痕。全量九条门禁按原样复跑全绿（ruff 通过、mypy 134 文件零错误、pytest 447 passed / 3 skipped、Golden 40/40、支持集 61/61、检索门槛 GATE PASSED、回放 REPLAY DIFF EMPTY、alembic 11 条迁移 + 23 张表且 check 无漂移、npm run build 通过），OpenAPI 41 路径 44 操作；说明书 docx 已重生成（`U+FFFD` 计数 0、外部项目名命中 0）。明细见 §7.1。

**2026-10-24 更新**：PostgreSQL 口径修正完成。上一轮口播里的「本机无 Docker/PG 环境」是**错的**——本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的（基线 `323 passed`）。这轮只改口径、不重跑：七处文档里「环境不具备」的理由改成「这一轮没有执行」，**「141 条新用例仍无 PG 证据」这个事实一个字没动**；ADR-006 的 2026-10-23 原文保留并加了一段带日期的修订，`PROJECT_SPEC.md` §19.2 补了一张漂移表留痕。九条门禁在同一个树状态上复跑全绿（pytest 447 passed / 3 skipped，产物 `.tmp/final_t10f.xml`），说明书 docx 已重生成（192,899 字节 / 68 张表，`U+FFFD` 计数 0）。明细见 §7.2。

**2026-10-24 更新**：全树口径复核完成。把前两轮的要求（去掉外部项目名、PG 口径修正）连同其余口径一起过了一遍全树——外部项目名在源码、文档、前端、`.tmp/` 与说明书 docx 里命中 **0**；PG 口径七处文档一致；规模数字（447 passed / 收集 450 / 33 个测试文件 / mypy 134 / 23 张表 / 11 条迁移 / 18 篇 ADR / 评测 40+61 / 41 路径 44 操作 / 10 个控制台页面）逐项对着文件系统与 `app.openapi()` 核过，全部一致。**发现并修掉一处没跟上的**：`README.md` 的「API 概览」首句仍写「共 43 个操作（40 条路径）」——T10 加了 `PUT /api/v1/memory/rules/{event_type}`，这一句漏了，已改成 **44 个操作 / 41 条路径**（那张表 44 行与 OpenAPI 一一对应）。旧措辞（`43 个操作` / `40 条路径` / `116 条` / `没有 sink` / `记忆不影响答案生成` / `没有规则编辑页` / `代码进行中`）逐处核对上下文后确认全部只出现在漂移表、ADR 的当日原文、分轮读数块与本轮这段复核记录本身里，**没有一处被当成当前事实**。本轮只改文档、没动代码，上一轮那组门禁读数仍然成立，没有重跑。说明书已重生成（193,256 字节 / 69 张表 / `U+FFFD` 计数 0）。明细见 §7.3。

**2026-10-25 更新**：「可验证证据」轮（把 README 从功能清单升级成能被面试追问的数字）已完成。新增三个测量脚本与一条用例：`scripts/demo_latency.py`（真实 HTTP、按轮类型分列的单轮延迟）、`scripts/refund_flow.py`（退款写路径五个动作的可重跑演示）、`scripts/llm_boundary_diff.py`（把 16 个写路径轮同时喂给规则与模型，打印字段级 diff 与 token 计数），以及 `tests/test_refunds.py::test_refund_of_another_customers_order_never_reaches_approval`（别人的 `order_id` 落到 `Order not found.`、任务 `FAILED`、不生成审批也不写 tool_execution）。README 新增两节：「端到端数字：延迟、token 与稠密通道的代价」（本机 LM Studio：稠密通道让政策问答从 p50 25.5 ms 变 1059 ms，而这一句答案逐字不变；模型模式让 12/60 轮付 6.0 s 与 636 token，比率与金额逐字段不动）与「模型在哪一层：LLM 只被允许填空」（重放结果 0/16——模型在这份流量上一次也没有改变路由）。门禁复跑：ruff 全绿、mypy 134 文件零错误、全量 **448 passed / 3 skipped（收集 451、33 个测试文件、112.83 s）**；`app/` 只有 `pydantic_agent.py` 的 `LlmUsage` 计数器改动，无新表无迁移；四条评测门禁同轮复跑全绿——Golden **40/40**、支持集 **61/61**、检索 `GATE PASSED`（ndcg@5 0.8824、intent_accuracy 1.0、stale/cross-tenant 均 0）、回放 `REPLAY DIFF EMPTY`（46 段会话、0 diff 行）。**没有做的**：重排通道仍无真实数字（LM Studio 不提供 `/rerank`）；稠密与模型两条新路径都没有进 CI（端点依赖，跑不了）；`evals/business_metrics.py` 的 token 计数器仍是进程内累计、重启清零；新增用例未在 Postgres 上复跑；0/16 这个读数只说明"模型没机会被证明有用"，规则读不懂的真实工单语料仍未收集。

**2026-10-25 更新**：「模型边界」轮（把那个 `0/16` 变成能被检查的事实）已完成，`app/` 一行没动。新增 `scripts/llm_gap_probe.py`：不从"我照着规则词表写的流量"出发，而从**规则读不懂的句子**出发——11 句按词表外手写的说法里，`classify()` 把 **10 句读成 `UNKNOWN`**，本机 `qwen3-8b` 捞回 **6 句**（「一百二十九元」这类中文数字金额补位 2/2 正确；`order_id` 编造 **0** 次，脚本每次跑都验这条，编造就非零退出），转人工的三种说法一句没接住，`运费谁出？` 被我标成 `POLICY_QA` 而它判成 `LOGISTICS_QUERY`（按 `runtime.py:296-302`，这个错判的代价是回一句"请提供订单号"）；代价 11 次调用、6885 token、72.9 秒（均 6.6 秒）。**真正的发现是结构性的**：同样这 11 句走对话入口（`--chat`），模型一次也没被问到——写动作的门在规则手里（`classify_support` 只在规则判成写意图时才交给写路径），11 轮产生 **0 个写动作**；`--live-task` 把同一句话在两个 brain 下的终态并排打出来（`rules` → `SUCCEEDED` / `NEEDS_HUMAN_REVIEW`「无法判断这个请求属于售后范围」；`pydantic_ai` → `WAITING_APPROVAL`、待审金额 12900）。这四条事实钉成了 `tests/test_llm_gap.py`（4 条，桩件离线跑，CI 不依赖模型服务）。README 新增一节「规则读不懂的 11 句：模型能捞回 6 句，但对话入口听不到它」，并把上一节里"真实工单语料我没有"从**结尾的缺口**改成**指向这一节**；`## 测试与评测` 的命令清单与「已知边界」的模型条目同步。门禁全量复跑：ruff 全绿、mypy 134 文件零错误、**452 passed / 3 skipped（收集 455、34 个测试文件、97.66 s）**、Golden **40/40**、支持集 **61/61**、回放 `REPLAY DIFF EMPTY`（46 段会话、0 diff 行）、检索 `GATE PASSED`（`bm25` 读数与上一轮逐字相同：recall@1 0.7941 / @3 0.9118 / @5 0.9706、mrr 0.8745、ndcg@5 0.8824、intent_accuracy 1.0、stale / cross-tenant 均 0）；无新表、无迁移，`alembic check` 同轮复跑为 `No new upgrade operations detected.`，`npm run build` 本轮未重跑（前端一行没动）。当前态规模读数同步了 README / `CONTRIBUTING.md` / `AGENT_PLATFORM_PLAN` / `docs/EVALS.md` / `PROJECT_SPEC` §14.1 五处，带日期的分轮读数块按原样保留。**没有做的**：**没有把对话路径改成"规则读不懂就问模型"**——那是把写动作的进门权交给模型，需要单独一轮（ADR + 台账口径 + 回归面），已列为开头「下一步的五件事」第 5 条；那 11 句仍是我按词表外造的句式，不是真实工单语料，所以"这类说法有多常见"依然没有答案；`--live-task` 跑的是临时库上的可达路径，不是生产流量占比；本轮新增的 4 条与上一轮新增的 1 条都只有 SQLite 证据；重排通道仍无真实数字；**账上也记一笔**：`docs/TicketPilot项目说明书.docx` 仍是 2026-10-24 的 193,256 字节版本，2026-10-25 这两轮加进 README / `docs/EVALS.md` / `PROJECT_SPEC` §14.1 的内容都还没回灌进去——生成脚本没有进仓库，所以重生成是一次手工动作，不是门禁能拦住的事。

**2026-10-25 更新（第二条）**：「对话入口接通模型」轮（收「五件事」第 5 条）已完成，新增 [ADR-020](adr/ADR-020-chat-model-gap.md)。`SupportAgent._consult_model` 是对话路径上唯一能碰模型的地方，只在同时满足三件事时问：规则给 `UNKNOWN`（或规则 `UNKNOWN`、目录只因为消息带了订单号才回 `ORDER_QUERY`）、`AGENT_MODE=pydantic_ai`、`blocks_write_action` 为假。采纳范围只有 `REFUND_REQUEST` 的**意图**（外加模型补的 `order_id`）；金额、读意图、`TICKET_CREATE` 都不采纳。台账不接受入口递进来的结论：`create_task` 进 `_run` 后用同一份请求**重新**规划，`task.routed` 记的是这一次的 `planning_source`，所以一次对话退款问两次模型——这是为了入口写错时最坏只是多追问一轮。决策痕迹写进 `understand` span 的 `model_gap`。实测（本机 LM Studio `qwen3-8b`）：同一批 11 句走对话入口，意图改变 **5/11**，6 句退款全部进入追问流程，单轮写动作仍 **0**（都缺槽位），代价 8 次调用 / 4941 token / 48.9 秒；端到端补齐槽位后任务 `task_d64f417d95db41c0` 落 `WAITING_APPROVAL`、`routed=REFUND_REQUEST source=pydantic_ai`、审批金额 12900、事件停在 `approval.requested`，这段对话 2 次调用 / 918+645 token / 20.3 秒。`AGENT_MODE=rules` 用「被调用就失败」的桩钉住一次不问。`tests/test_llm_gap.py` 从 4 条增到 7 条（全离线）。门禁复跑：ruff 全绿、mypy 134 文件零错误、**455 passed / 3 skipped（收集 458、34 个测试文件、98.45 s）**、Golden **40/40**、支持集 **61/61**、回放 `REPLAY DIFF EMPTY`、检索 `GATE PASSED`；无新表、无迁移；前端未动，`npm run build` 未重跑。**没有做的**：那 11 句仍是按词表外手写的说法，不是真实工单语料；没有信度阈值（`IntentOutput.confidence` 存在但没有语料能定阈值）；没有按会话的模型预算与同句缓存，重复问会重复付费；`TICKET_CREATE` 未采纳（持久副作用 + 通知需要单独设计）；本轮新增 3 条用例只有 SQLite 证据（T2 起新建的 10 个测试文件共 148 条待 PG 复跑）；托管模型仍未验证；`docs/TicketPilot项目说明书.docx` 仍未回灌本轮与上一轮的内容。README 那一节改名为「规则读不懂的 11 句：模型捞回 6 句，对话入口只买一张待审退款单」，`## 测试与评测` 的命令清单、`## 模型在哪一层` 的边界表、`## 已知边界` 的模型条目、演示步骤 ⑤ 同步。

**2026-10-25 更新（第三条）**：「两库复跑 + 重排证据 + 说明书生成脚本」轮已完成，`app/` 一行没动。三件事：**PostgreSQL** 16.15 一次性容器（`ticketpilot-pg`，`127.0.0.1:55432`）上带 `TICKETPILOT_TEST_POSTGRES_URL` 跑全量 **458 passed / 0 skipped（109.92 s）**，3 条 PG-only 用例全部执行；同一容器上 Golden **40/40**、支持集 **61/61**，11 条迁移（head `c3b8e5a71d29`）+ `alembic check` 无漂移。T2 起 10 个文件（148 条）与四张新表第一次有 PG 证据。**重排**：LM Studio 不提供 `/rerank`，改用本机 Docker 的 Infinity（`michaelf34/infinity:latest-cpu`，`127.0.0.1:7998`，缓存挂 `E:/My-Agentix/reranker-cache`）服务 `bge-reranker-v2-m3`；17 条标注 / 68 条查询上 recall@1 **0.794 → 0.912**、MRR **0.875 → 0.956**、nDCG@5 **0.882 → 0.944**，但 recall@3 **0.971 → 0.912**、recall@5 不变；61 条查询 × 17 候选块、0 失败、mean 2277 ms / p50 2267 / max 2563，默认仍关闭。**说明书**：新增 `scripts/build_manual.py`（`make manual`），渲染后校验 `U+FFFD`=0、段落 / 表格 / 体积，失败不替换旧文件；docx 已重生成。门禁同轮复跑：ruff 全绿、mypy 134 文件零错误、**455 passed / 3 skipped（收集 458、97.95 s）**、Golden 40/40、支持集 61/61、检索 `GATE PASSED`、回放 `REPLAY DIFF EMPTY`、`alembic check` 无漂移、`npm run build` 通过。**没有做的**：四通道检索指标脚本未在 PG 上跑；重排只在 CPU 上、无生产部署与延迟预算；docx 只做编码 / 结构校验，不做排版对照；浏览器截图仍未取。

**2026-10-25 更新（第四条）**：「五通道两库 + 重排延迟预算 + 说明书版面实看」轮已完成，**代码只动了一处**。三件事：**① 检索指标脚本在 PostgreSQL 上真跑了**——`evals/retrieval_metrics.py --channels bm25,dense,hybrid,gated,rerank` 在独立的 `tp_eval` 库上跑出**五行读数，与 SQLite 逐位一致**（recall@1 0.794 / 0.853 / 0.794 / 0.794 / 0.912，gated 的 `dense_trigger_rate` 0.2941）。能一致的前提是**修掉了一个真实的可复现性缺陷**：`PolicySearchService.visible_chunks()` 原本没有 `ORDER BY`，行序随数据库而变 → BM25 同分行的次序变 → 重排拿到的候选集变 → 同一份语料在两个库上排出不同名次。现在它按块 ID 排序（`app/retrieval/search.py`，本轮唯一的行为改动），两库逐位一致。**② 重排延迟有了可执行的预算判定**——新增 `evals/rerank_latency.py`，把计时探针插进**生产**的 `HybridPolicySearch`（量的是线上那条融合后的候选集，15 条），实测 60 次计时、0 失败、0 空候选：mean 2300 / p50 2281 / **p95 2602** / min 2081 / max 2689 ms，对 300 ms 的交互预算判 **OVER BUDGET**（`--enforce` 退出码 1）。结论从「没测」变成「测了，超预算」——这台纯 CPU 机器上重排不能进请求路径。**③ 说明书 docx 做了版面目视检查**：docx → PDF（Gotenberg 8 容器内 LibreOffice）→ PNG（容器内 `pdftoppm`）→ 逐页看标题页 / 正文 / 表格 / 复选框；复选框里的空心方块是**真实的 U+2610 球票框**（`pdftotext` 字节级核对），不是豆腐块，U+FFFD 计数 0、无 `w:sym` / 私用区字符。门禁同轮复跑：SQLite 基线 **496 passed / 6 skipped**（收集 502、43 个测试文件、118.54 s），**PostgreSQL + Redis 两个容器同时指上是 502 passed / 0 skipped（119.71 s）**，13 条迁移（head `b4e8d2f6a1c9`）+ `alembic check` 无漂移，Golden 40/40、支持集 61/61、检索 `GATE PASSED`、回放 `REPLAY DIFF EMPTY`、ruff 全绿、mypy strict **137** 文件零错误、`npm run build` 通过。**没有做的**：生产托管 embedding / rerank 的接入与压测；服务商侧投递；浏览器截图仍未取（应用内浏览器当时没有可见视口）。

下一步的价值不在「再加一个后端能力」，而在五件事：
1. **把出站投递接到真实服务商上**——`OutboundTransport` 现在只有拒绝发送的默认实现与写本地文件的演示实现，`SENT` 的含义止于"离开本应用"；真协议还需要送达回执，那是另一个状态，不能复用 `SENT`（G7 余项）；
2. ~~**补最后一条断链**——主动规则只能改 `app/seed.py` 重灌，界面与写接口都没有（G10）~~ **已由 T10 收口（2026-10-24）**：`PUT /memory/rules/{event_type}` + 写时校验模板 + 控制台第 10 页都到位了，「什么时候说什么话」第一次是运营能自改的配置。**但只收口到「可改」**——改完没人复核、没有版本表可以回滚、没有按租户灰度，这三样是有意留下的边界（ADR-019 后果段），不是遗漏；记忆写进去了但答案生成路径不读它（G6）已由 T9 收口，但**只收口到「四种模板效果」**，档案里的自由文本仍然进不了回复，这是刻意的边界而不是漏网（见 §3 T9 偏差）；
3. **把回放与抽样的产物接进日常运营**——bad case 队列仍只产出 JSONL，没有回灌数据集与跨天去重的流程（渠道字段本身已在 T7-5 修好，抽样不再一律回落 `WEB`）。
4. ~~**把 T2 起新增的用例在 PostgreSQL 上复跑**~~ **已由 2026-10-25「两库复跑」轮收口**：PostgreSQL 16.15 一次性容器上带 `TICKETPILOT_TEST_POSTGRES_URL` 跑全量，**458 passed / 0 skipped（109.92 s）**，3 条 PG-only 用例全部执行；同一容器上 Golden **40/40**、支持集 **61/61**，11 条迁移（head `c3b8e5a71d29`）+ `alembic check` 无漂移。T2 起新建的 10 个测试文件（该轮的文件级条数：148 条，`test_security_guard.py` 14 / `test_eval_datasets.py` 4 / `test_trace_sampling.py` 6 / `test_channels.py` 20 / `test_memory.py` 21 / `test_retrieval_channels.py` 20 / `test_outbound.py` 17 / `test_personalization.py` 18 / `test_proactive_rules.py` 21 / `test_llm_gap.py` 7；当前已分别涨到 26 与 9）与四张新表第一次有 PG 证据；`409 RULE_ALREADY_EXISTS` 走的唯一约束也在其中。**检索指标脚本本身也已收口（2026-10-25 第四条更新）**：五通道指标在 `tp_eval` 库上跑出，**与 SQLite 逐位一致**——前提是修掉了 `visible_chunks()` 缺 `ORDER BY` 的行序不确定。**仍然没有的**：CI 的 Postgres job 仍只验迁移、不跑 pytest。
5. ~~**把对话路径的 `UNKNOWN` 接到模型那半边**~~ **已由 2026-10-25「对话入口接通模型」轮收口**：`SupportAgent._consult_model` 在规则 `UNKNOWN`（或只靠订单号回 `ORDER_QUERY`）、`AGENT_MODE=pydantic_ai`、且不是问句时才问模型，只采纳退款意图，台账自己重算并记 `planning_source=pydantic_ai`，端到端仍停在 `WAITING_APPROVAL`。取舍与实测见 [ADR-020](adr/ADR-020-chat-model-gap.md) 与 README「规则读不懂的 11 句：模型捞回 6 句，对话入口只买一张待审退款单」。**仍然没有的**：真实工单语料、信度阈值、按会话的模型预算与同句缓存、`TICKET_CREATE` 的采纳——这些是下一步，不是本轮遗漏。

一句话：**T1–T3 让平台「可运营、可承接、可回归」，T4–T6 让它「可多渠道承接、可记住客户、可换检索器」，T7 让这些能力第一次被人点到、让确认过的通知真的走出去，T8 把入口统一到同一道幂等闸上，T9 让档案以四种可枚举的方式改掉回复，T10 让运营第一次能自己改「我们怎么说」——差的是真实服务商出口、规则改动的复核与回滚、检索的 ANN / 服务化部署，以及真实工单语料。**

---

## 1. 基线（实测）

### 1.1 已经就位

| 维度 | 状态 | 证据 |
|---|---|---|
| 意图 | 12 条，写在 `app/agent/intents.yaml`（`version: 2026-10-24`） | 加意图不改 Python |
| 工具契约 | `ToolManifest`：params / risk / timeout / retry / degradation / receipt / scopes | `app/tools/base.py` |
| 执行器 | 六类失败形态 + 超时预算 + 熔断 + 降级链 | `app/tools/executor.py` |
| 转人工 | 队列 / 详情 / claim / release / close / metrics / abandons | `app/api/v1/handoffs.py` |
| 观测 | span 树 + 脱敏 | `app/observability/tracing.py`、`redaction.py` |
| 检索 | BM25 + 稠密 + RRF 混合 + 重排；dense 默认关，`--channels bm25,hybrid` 已能出对比数字 | `app/retrieval/`（7 个模块）、`scripts/embed_policies.py` |
| 渠道 | `ChannelAdapter` 协议 + 8 个渠道声明 + 邮件/IM 签名入站 + 投递幂等表 + **控制台与旧入口共用同一道幂等闸** | `app/channels/`、`app/api/v1/channels.py`、`app/api/v1/support.py`（走 `gateway.accept_web`） |
| 界面 | 10 个控制台页面：对话 / 审批 / 任务历史 / 任务详情 / 坐席工作台 / 运营看板 / Trace 回放 / 渠道矩阵（坐席）/ 我的记忆（客户）/ 通知规则（坐席，T10） | `web/src/pages/`、`web/src/App.tsx` |
| 记忆 | 客户自持档案（读写删导出）+ 规则渲染的主动通知（客户确认后才落 Outbox）+ **确认后真正投递** + **档案以四种模板效果进答案**（T9） + **规则文案/冷却/启停可由坐席改**（T10） | `app/services/memory.py`、`proactive.py`、`app/channels/outbound.py`、`app/api/v1/memory.py`、`app/agent/personalization.py` |
| 测试 | 496 passed / 6 skipped（502 collected、43 个测试文件；SQLite；Postgres / Redis 专项默认跳过）；**PostgreSQL 16.15 同轮全量 500 passed / 2 skipped（134.77 s）**，同时指上 Redis 容器后是 **502 passed / 0 skipped（119.71 s）**，Golden 40/40、支持集 61/61、13 条迁移 + check 无漂移；CI 的 Postgres job 仍只验迁移不跑 pytest | `tests/` 43 个文件 |
| 一致性 | Outbox + worker + 死信 | `app/models/outbox.py`、`app/api/v1/outbox.py` |
| 统计 | 受控 Text-to-SQL（planner / builder / guard / executor） | `app/sql/` |
| 安全 | 输入/输出护栏、租户+IP 限流、请求体/附件上限、安全事件查询与看板 | `app/core/guard.py`、`output_guard.py`、`app/api/middleware.py`、`app/api/v1/security.py` |
| 评测 | 任务路径 40 条 + 只读路径 61 条（共 101 条，含轨迹断言） | `evals/golden_set.json`、`support_set.json`、`evals/run.py` |
| 闭环 | 回放 diff（结论/轨迹）+ 分层抽样 bad case 队列 | `evals/replay.py`、`scripts/sample_traces.py` |
| 在线验收 | 74 条真实 HTTP 断言全通过（签名 401/403、幂等 409、未知渠道 404、矩阵 `enabled=false`、同意/频率/白名单校验、跨租户隔离、导出与删除） | 本文件 §3 T4/T5 的「真跑记录」；服务真实启动于 `127.0.0.1:8124` |
| T7 在线验收 | 主动通知出口在真实 HTTP + 真实浏览器上走完：默认传输 `sent=0` + `[NO_TRANSPORT]`（通知停 `CONFIRMED`）→ 未选渠道 `[NO_CHANNEL]` → 渠道填 `SMS` `[NO_ADDRESS_SOURCE]` → 配 `local_file` 并选 `EMAIL` 后 `{"claimed": 1, "sent": 1, "failed": 0, "dead_lettered": 0}`、通知 `SENT`、落盘一行含主题与署名的 JSON、审计只有 `address_digest`；浏览器侧「选渠道 → 保存 → 确认 → 投递 → 徽标变已推送」 | 本文件 §3 T7 的「真跑记录」；服务实启于 `127.0.0.1:8128` / `8129`（Alembic 建库 + seed 的隔离 SQLite） |
| T8 在线验收 | 23 条真实 HTTP 断言全通过（`total=23 passed=23 failed=0`）：渠道路由重放只跑一轮、旧入口带 `X-Channel-Message-Id` 同样去重、同一个 ID 换入口仍算同一次投递、处理中 409、陈旧认领被接管；浏览器侧（真实 DOM）用 fetch 拦截器**丢掉一次响应**再原话重发，两次请求同一个幂等键、第二次 `X-Channel-Deduplicated: true` 且任务数不变，坐席视角的渠道矩阵 `WEB` 计数随控制台发送 +1 | 本文件 §3 T8 的「真跑记录」；服务实启于 `127.0.0.1:8130`（Alembic 建库 + seed 的隔离 SQLite `.tmp/t8_live.db`） |
| T10 在线验收 | 52 条真实 HTTP 断言全通过（`52 checks, 0 failures`）：读三条种子规则 → 改退款模板 → `POST /memory/events` 看到新文案逐字生效 → 停用 → `NO_RULE` → 恢复 → 冷却改 0 立即重发 → 审计里只有 sha256 → 客户身份 403 → `acme` 租户 upsert 建出新规则并能 emit；九种拒绝形状各有 422 与错误码（未知事件 / 未知键 / `{}` / 属性 / 下标 / 不配对 / 超长 / `requires_confirmation` / 冷却越界 / 空 body）；浏览器侧（真实 DOM）保存生效、刷新后读回新文案、坏文案把服务端原话回显在输入框下方、客户身份拿到拒绝面板且**没有发出任何规则请求** | 本文件 §3 T10 的「真跑记录」；服务实启于 `127.0.0.1:8135`（Alembic 建库 + seed 的隔离 SQLite `.tmp/t10_live.db`）；截图未取——应用内浏览器当时没有可见视口，验收以 DOM 断言 + 服务端回读为准 |
| 写路径手工验收 | 重建库上跑通退款**成功态**端到端：建任务 `WAITING_APPROVAL` → `X-Roles: approver` 审批 → 任务 `SUCCEEDED`、审批 `CONSUMED`、台账 `RECONCILED`、订单可退余额 12900 → 0；`scripts/demo.py` 三条只读问答 + 两次章节按需加载全通过 | 服务实启于 `127.0.0.1:8126`（`AUTO_CREATE_TABLES=false`）；演示库计数回到文档口径 tasks 1 / approvals 1 / refunds 1 / tool_executions 1 / orders 4 / documents 5；**其余四个验证态（`PARTIAL_SUCCESS` / `PENDING_CONFIRMATION` / `FAILED` / `NEEDS_HUMAN_REVIEW`）未手工走**，一次成功的退款也不产生 Outbox 消息——**退款侧** `SENT` 收敛在那一轮仍无实测（主动通知侧的 `SENT` 已由 T7 实测，见上一行） |

### 1.2 仍未覆盖

| # | 缺口 | 证据 | 影响 |
|---|---|---|---|
| G1 | ~~坐席与运营**无界面**~~ **已覆盖（T1，2026-10-24）** | `web/src/pages/AgentWorkbench.tsx`、`OpsDashboard.tsx`、`TraceViewer.tsx`；handoffs / traces / outbox / analytics 均有入口；客户身份被角色门禁拒绝 | 转人工闭环可只靠控制台完成 |
| G2 | ~~**无渠道抽象**~~ **已覆盖（T4/T4b，2026-10-24）**；入口分裂由 T8 收口 | `app/channels/`（base/registry/gateway/store/render/web/email/im）；`GET /api/v1/channels` 矩阵；`channel_messages` 幂等表；移交包与放弃事件带 `channel`；`app/api/v1/support.py` 与 `app/api/v1/channels.py` 都经 `gateway.accept_web`，控制台前端改调渠道路由并自带幂等键 | 邮件与 IM 可入站；真实服务商协议仍是本仓库自定的 HMAC 契约 |
| G3 | ~~**无滥用防护**~~ **已覆盖（T2，2026-10-24）** | 租户 + IP 令牌桶、请求体与 multipart 上限、429/413 测试；桶在进程内，多 worker 会放大预算 | 可做单实例对外演示；生产需 Redis 配额 |
| G4 | ~~**无内容护栏**~~ **已覆盖（T2，2026-10-24）** | 注入/绕过/敏感内容识别并留痕；无来源金额/时效/承诺断言替换为「需确认」；输入侧仍只记录不拦截 | 合规风险大幅下降；PII 流水线与附件白名单仍待补 |
| G5 | ~~**评测量级不足**~~ **已覆盖（T3，2026-10-24）** | 101 条用例（40 任务 + 61 只读）、轨迹断言、回放、分层抽样；新增测试守住数据集结构与抽样规则 | 回归有网兜底；bad case 回灌仍是人工流程 |
| G6 | ~~无长期记忆~~ ~~记忆只被存与查~~ **已覆盖（T5 接口 + T9 进答案，2026-10-24）** | `customer_profiles` 表 + `/api/v1/memory` 7 个客户侧操作；真跑确认写入/删除/导出与审计留痕。**T9**：`app/agent/personalization.py` 让档案在决策之后、护栏之前改掉回复，18 条用例 + 59 项真实 HTTP 断言 + 浏览器走查 | 只有**四种可枚举的模板效果**（称呼 / 卡片重排 / 裁追问 / 补一条追问）；档案里的自由文本（`history_summary`、`contact_window`）刻意进不了回复，`locale` 没有多语言生成路径所以不接；「按档案预填槽位」没做；档案一改，那之前的历史 trace 不再逐字可回放（ADR-018 后果） |
| G7 | ~~主动服务**只到确认，没到投递**~~ **投递已覆盖（T7-4，2026-10-24）**，服务商侧仍空 | `app/channels/outbound.py` 的 `ProactiveSender` 注册进 `_outbox_sink`；真跑确认 `SENT` 首次可达、地址只从 `CHANNEL_*_IDENTITY_MAP` 反查、四种拒绝码各有用例与实跑。**没有的是** SMTP / 微信 / 企业微信协议与送达回执 | 演示能"发出去"，但 `SENT` 止于"交给配置的传输"；接服务商是 `OutboundTransport` 的第四个实现 |
| G8 | ~~稠密通道**能跑但不是真语义**~~ **已用真实端点验证（2026-10-25）**，工程化仍缺 | `RETRIEVAL_DENSE_ENABLED` + `document_chunks.embedding`（JSON 列）；本机 LM Studio `bge-m3`：`--channels bm25,dense,hybrid,rerank` 实测 recall@1 0.7941 → 0.8529（dense）→ 0.9118（rerank），MRR 0.8745 → 0.9265 → 0.9559 | 检索时逐条算余弦相似度（全表扫描，**无 pgvector / ANN**）；重排每次约 2.3 秒、只在 CPU 上测过；数字只来自 17 条标注的演示语料 |
| G9 | ~~渠道/记忆/主动服务**没有控制台页面**~~ **已覆盖（T7-1/2/3 + T10，2026-10-24）** | `web/src/pages/ChannelMatrix.tsx`（坐席，8 渠道能力与启用状态）、`MemoryPage.tsx`（客户：档案读写删导出 + 待确认通知同意/取消 + 推送渠道选择，只列真能送到的渠道，「已推送」旁标注不等于送达回执）、`ProactiveRules.tsx`（坐席，规则文案/冷却/启停可改）；10 页 | **规则的删除与回滚仍没有**（T10 有意不做，`enabled=false` 表达「不再发」）；出站投递没有界面，只能看队列行与落盘文件 |
| G10 | ~~主动规则**只有读接口**~~ **已覆盖（T10，2026-10-24）** | `PUT /api/v1/memory/rules/{event_type}`（按 `(tenant, event_type)` upsert，`extra="forbid"` 只收三个字段）+ 写时模板校验 + `ProactiveRuleView` 补 `required_context` / `updated_at` + 审计 `proactive.rule.update` 只存 sha256 + 控制台第 10 页；21 条用例 + 52 项真实 HTTP 断言 + 浏览器 DOM 走查 | 写接口让「换话术/换租户」不再需要改代码重灌；**没有的是**：双人复核（审批那层有）、版本表与一键回滚、按租户灰度、删除规则、规则改动进评测集；事件类型仍是代码 enum（`requires_confirmation` 写不进去，422） |
| G11 | ~~抽样渠道仍不可信~~ **已覆盖（T7-5，2026-10-24）** | `TaskRuntime` 的 `context` 带上渠道，抽样不再一律回落 `WEB`；bad case 队列**仍只产出 JSONL**，没有回灌与跨天去重 | 渠道口径可信了，接进日常运营的流程还没接 |

---

## 2. 优化主题与排序

| 主题 | 覆盖缺口 | ROI | 风险 | 建议期次 |
|---|---|---|---|---|
| **T1 坐席与运营界面** | G1 | 极高（后端 100% 就绪，纯前端） | 低 | Sprint 1 ✅ 已完成（2026-10-24） |
| **T2 护栏与滥用防护** | G3、G4 | 高（对外前提） | 中（误拦会伤体验） | Sprint 2 ✅ 已完成（2026-10-24） |
| **T3 评测扩量与闭环** | G5 | 高（后续一切改动的门禁） | 低 | Sprint 2–3 ✅ 已完成（2026-10-24） |
| **T4 渠道抽象与接入** | G2 | 中高（结构性） | 中（动入口） | Sprint 3 ✅ 已完成（2026-10-24） |
| **T4b 渠道溯源** | G2 余项 | 中（分层与移交包可信度） | 低 | Sprint 3 ✅ 已完成（2026-10-24）；抽样渠道仍不完整（G11） |
| **T5 长期记忆与主动服务** | G6、G7 | 中（前瞻） | 中（合规） | Sprint 4 ✅ 接口与治理已完成（2026-10-24）；投递由 T7-4 收口、进答案由 T9 收口 |
| T6 稠密检索实装 | G8 | 中（依赖外部 embedding） | 低 | 有条件再做 ✅ 已完成护栏与评测编排（2026-10-24）；真实 embedding 端点未接 |
| **T7 新能力的界面与出口** | G7、G9、G10、G11 | 高（否则 T4/T5 只能 curl 演示） | 低 | Sprint 5 ✅ 已完成（2026-10-24，G7 投递 / G9 两页 / G11 渠道字段；**G10 规则可配置当时有意留下**，需要写接口 + 审计 + 灰度口径，不适合塞进这一期；**后由 T10 收口**，只有「按租户灰度」那一格仍是有意不做） |
| **T8 控制台迁到渠道入口** | T4 偏差 2（`docs/AGENT_PLATFORM_PLAN.md` 风险表 B10） | 高（幂等面收口，演示不再需要「只用新入口」这句免责） | 中（动入口，但 101 条评测 + 402 条用例做底） | Sprint 5 补充 ✅ 已完成（2026-10-24） |
| **T9 记忆进答案（G6）** | G6 余项（T5 只把档案存下来，答案生成路径不读它） | 高（长期记忆不参与回答等于没有记忆；且这是「模型读不读用户档案」这条信任边界的第一次实装） | 中（动生成路径；档案一改，历史 trace 不再逐字可回放） | Sprint 5 ✅ 已完成（2026-10-24，`app/agent/personalization.py`，18 条用例 + 59 项真实 HTTP 断言 + 浏览器 DOM 走查；**只收口到四种模板效果**——把档案拼进提示词让模型自行决定、按历史预填槽位有意不做，ADR-018） |
| **T10 主动规则的可编辑面** | G10 | 中高（T5 那条链的最后一段：规则文案今天只能改 `app/seed.py` 重灌，界面与写接口都没有；做完后「什么时候说什么话」第一次成为运营可自改的配置） | 中（新增一个写接口 + 一个控制台页；不动生成路径，`evals/` 与回放读数不受影响） | Sprint 6 ✅ 已完成（2026-10-24；写接口 + 写时模板校验 + 第 10 页；事件类型仍是代码 enum、`requires_confirmation` 不做成开关、双人复核与灰度留给 ADR-012，决策全文见 ADR-019） |

排序理由：T1 不动后端、演示价值立现；T2 是「能不能对外」的分界线；T3 必须在 T4 之前，否则改入口没有回归网兜底（事实检验：T4 落地时 `app/agent/`、`app/services/` 零改动，101 条评测结论不变）；T6 放在最后，因为它依赖外部服务，先用离线替身把「换检索器会不会退化」这件事量化，比先接一个真模型更有价值；T7 是 T4/T5 的欠账——能力做完了但只有 API，人用不上就等于没交付；T9 排在 T8 之后，因为它动的是生成路径——入口先收口、回放先存在，才有「无档案客户逐字不变」这条可以拿命去断言的安全网；T10 排在 T9 之后，因为它是**配置面**而不是**生成面**：改的是通知文案的写法，动不到答案路径，所以可以在评测与回放都稳定后单独收口，且它天然需要一个新的 ADR（写时校验与审计口径）而不是塞进 T7 那期。

---

## 3. 任务拆解

### T1 坐席与运营界面（Sprint 1）

**状态：✅ 已完成（2026-10-24）**。5 个前端任务全部落地，后端只补了两个只读能力：`GET /api/v1/traces/metrics` 与 handoff metrics 的 `window_minutes` 窗口过滤。手工验收覆盖接管 / 交还 / 结案三条动作、四个新页面、390px 单列布局与客户角色门禁；`npm run build`、ruff、mypy、pytest 全绿。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T1-1 | 坐席工作台：队列列表 + 移交包详情 + claim/release/close | 新增 `web/src/pages/AgentWorkbench.tsx`；复用 `web/src/api.ts` | ✅ 队列按 `waiting_since` 升序；详情展示移交包全字段；claim 后按钮态变为 release/close |
| T1-2 | 运营看板：转人工率 / 自结率 / Abandon / Outbox 积压 / span 错误率 | 新增 `web/src/pages/OpsDashboard.tsx` | ✅ 六个请求并发拉取（handoff 指标、放弃、三种 Outbox 状态、trace 指标）；空数据显示 `—` 而非 0；24h / 7d / 30d 时段切换 |
| T1-3 | Trace 查看器：按 `trace_id` 渲染 span 树 | 新增 `web/src/pages/TraceViewer.tsx`；入口挂在任务详情页 | ✅ 树形缩进 + 耗时条；点击 span 显示 attributes（已脱敏）；任务详情与坐席移交包均有入口 |
| T1-4 | 导航与角色门禁：坐席/运营页仅 `approver`/`admin` 可见 | 改 `web/src/App.tsx`、`identity.tsx` | ✅ 客户身份看不到入口；直接输 URL 也拒绝（门禁按 `support`/`admin` 判定，见下方偏差说明） |
| T1-5 | 对话页展示统计卡片（analytics 的 plan / SQL / rows） | 改 `ConversationPage.tsx` | ✅ 折叠展示，默认收起；SQL 只读高亮；明确标注「账本不保存结果行」 |

**DoD**：✅ 390px 单列无横向滚动；接管期间 Agent 沉默在 UI 上可见（「Agent 静默中」状态标签）；`npm run build` 通过；四个新页均有浏览器手工验收记录（接管 / 交还 / 结案、指标时段切换、Trace 展开、统计卡片展开）。

**与计划的三处偏差（诚实记录）**：

1. **角色门禁按 `support` 判定，而非仅 `approver` / `admin`**：handoff API 本身要求 `support`，若前端只放行 `approver` / `admin`，坐席角色反而进不去。当前 `canOperate = support || admin`；审批入口仍按 `approver || admin`。
2. **新增 `GET /api/v1/traces/metrics`**：运营看板需要 span 错误率与 P95，原计划只有单条 trace 详情接口。该接口仅对坐席开放，按租户隔离，`window_minutes` 上限 90 天。
3. **analytics 结果行不落账本**：统计卡片只展示 plan / SQL / 行数 / 耗时 / 状态，不重新执行 SQL；「rows」是当时聚合结果的元数据，不是可展开的明细表。

### T2 护栏与滥用防护（Sprint 2）

**状态：✅ 已完成（2026-10-24）**。四项任务全部落地，新增 14 条安全用例；全量测试 **336 passed / 3 skipped**（339 collected），ruff、mypy（115 文件）、`npm run build`、`alembic check` 全绿，既有 Golden 与支持路径结论未变。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T2-1 | 输入护栏：注入模式库 + 敏感内容分类 + 命中记 `security_event` | 新增 `app/core/guard.py`、`app/models/security_event.py`、`app/services/security_events.py`、迁移 `f3a7c9d21b84` | ✅ 「你们政策允许全额退款」先留痕，紧接着的资格问题仍按数据库余额判为「不符合退款条件」；无任务、无退款创建 |
| T2-2 | 输出护栏：金额/时效/承诺类断言必须带 citation 或工具来源 | 新增 `app/core/output_guard.py`；接在 `support_agent` 生成之后 | ✅ 无来源断言替换为「需确认」并追加提示，同段其他内容保留；有 task/card/tool 来源时原文不动 |
| T2-3 | 限流与体积上限：令牌桶（租户 + IP 双维度）、请求体上限、附件大小上限 | 新增 `app/api/middleware.py`；改 `app/main.py`、`app/core/config.py`、`app/core/errors.py` | ✅ 超限 429 + `Retry-After`；JSON 超 256 KiB、multipart 超 5 MiB 返回 413；阈值全部走 Settings |
| T2-4 | 安全事件看板卡 | 复用 T1-2 看板；新增 `GET /api/v1/security/metrics`、`/events` | ✅ 看板新增「安全事件」卡与「安全防线」面板，按类别分列拦截/改写/留痕/高危 |

**关键实现口径**：

- **输入护栏是观测性的**：`scan_input` 只产出 `GuardFinding` 并写 `security_events`，不参与路由、不修改资格判定；敏感信息（手机号/邮箱/证件/银行卡/凭证）只存掩码后的 `excerpt` 与 `digest`，不存原文。
- **输出护栏按来源判定**：任务结果、卡片、工具动作（`order.*` / `refund.*` / `catalog.*` / `analytics.*`）视为有来源；澄清追问不检查，避免把示例金额误替换。
- **体积上限在中间件层**：先看 `Content-Length`，再对 chunked 请求做有上限缓冲并回放；只对 `POST/PUT/PATCH/DELETE` 生效，SSE 等无 body 的 GET 保留原始 `receive`，否则 `StreamingResponse` 会被误判为客户端断开。

**与计划的四处偏差（诚实记录）**：

1. **限流桶是进程内的**：租户与 IP 各一个令牌桶，多 worker 部署时每个进程各有一份预算，实际上限会被放大。跨进程配额需要 Redis，未在本轮引入。
2. **附件上限约束的是 multipart 请求体总大小**，不是解析后的单文件大小；EXIF 剥离与文件类型白名单仍属 T4。
3. **输出护栏是确定性规则，不是语义核验**：它能识别金额、时效、百分比、承诺词与「今天/马上」类即时承诺，但无法证明某句话具体对应哪一条 citation；有来源时整段放行，不做逐句绑定。
4. **传输层拒绝的事件留痕是 best-effort**：安全事件写库失败只记日志、不影响 429/413 响应；限流事件按 `(租户, 维度)` 60 秒去重，避免事件风暴。

**DoD**：✅ 注入、越权、超频、超体积四类用例全绿；所有既有测试与 Golden 用例结论不变；`alembic check` 无漂移（迁移链另在全新 sqlite 上从零验证通过）。

> 环境修复记录：`ticketpilot.db` 是旧版 `create_all` 产物，缺 `d5a91b73e204` / `b7c4e19a2f60` 两批列（`tasks.trace_id`、`sql_queries` 三列）。本轮先备份到 `.tmp/ticketpilot.db.bak`，再补列并执行 `alembic upgrade head`，使本地库与模型一致；这不是 T2 引入的漂移。

### T3 评测扩量与闭环（Sprint 2–3）

**状态：✅ 已完成（2026-10-24）**。六项任务全部落地：任务路径 40 条、只读路径 61 条（共 101 条），轨迹断言覆盖工具名/参数/降级/回执来源，回放自检 `REPLAY DIFF EMPTY`，分层抽样在 85 条 trace 上产出 32 条候选、18 条入队。全量 **345 passed / 3 skipped**（T4/T5/T6 之后为 382 / 385，见 §1.1），CI 新增支持路径、检索指标与回放三道阻断门禁。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T3-1 | 任务路径用例 10 → 40，覆盖 12 意图 × 正常/边界/失败 | 扩 `evals/golden_set.json` | ✅ 40 条覆盖 6 个任务意图 × 三态（UNKNOWN 无 normal），其余 6 个只读意图由 support_set 覆盖，并集 12；每条带 `expected_outcome` 与 `expected_tool` |
| T3-2 | 只读路径用例 23 → 61，补多轮缺槽、乱序补槽、取消、超时 | 扩 `evals/support_set.json` | ✅ 12 意图全覆盖；澄清类断言 `clarification.reason` / `expected_slot` / `resumable_intent`，并有续跑用例断言 `support.classify.resumed` |
| T3-3 | 轨迹断言：工具名 / 参数 / 降级步骤 / 回执字段来源 | 改 `evals/run.py` | ✅ 失败信息按 `trace.tools`、`trace.<tool>.params.<key>`、`trace.degradation.status` 定位到字段 |
| T3-4 | 回放：读 trace 表重跑并 diff | 新增 `evals/replay.py` | ✅ 输出 `CONCLUSION` / `TRAJECTORY` 两类 diff；自检 61 条录制会话中 46 条可回放，15 条因转人工跳过 |
| T3-5 | 线上抽样：分层抽样产 bad case 队列 | 新增 `scripts/sample_traces.py` | ✅ 按渠道×意图分层，信号排序 + 严重度 + 配额上限，产出 JSONL（消息与 span 属性已脱敏） |
| T3-6 | CI 门禁加固 | 改 `.github/workflows/ci.yml`、`Makefile` | ✅ Golden / 支持路径 / 检索指标 / 回放任一回归即阻断；新增 `make eval-support`、`eval-replay`、`sample-bad-cases` |

**关键实现口径**：

- **断言只看外部可观测**：终态、工具名、工具参数、降级状态、回执字段；不读内部函数返回值，避免「测实现」。
- **回放只在可复现的前提下比对**：每轮都有 trace、无审批/转人工/对账 span、有 generate span、工具只允许 `analytics.query`。生成类 id（`conv_` / `task_` / `doc_` 等）与 ISO 时间戳在比对前归一化，否则每次种子都会出现假 diff。
- **回放的诚实边界**：新建库重放，源库里已经不存在的订单/政策会以 diff 形式暴露，而不是被静默跳过。
- **抽样只读**：直接读 span / task / handoff / 消息表，不建表、不写库；读失败只回一行错误（不含 SQL 与会话 ID）。

**与计划的偏差（诚实记录）**：

1. **只读路径实际 61 条，而非 60 条**：多出的一条是取消场景的续跑对照，保留后覆盖更完整。
2. **回放不覆盖转人工会话**：接管期间 Agent 静默，重放无法复现坐席动作，因此 15 条带 handoff span 的会话显式跳过并在输出中列出原因。
3. **抽样没有渠道列可用**：`handoffs` 表本身无 channel，渠道取自 `bundle["channel"]`（当时恒为 `web`）；接 T4 渠道后才会出现真实分层。**（T4b 已让移交包与放弃事件带上真实渠道；T7-5 再把渠道写进任务 `context`，所以带不带转人工的 trace 现在都同源可信，回落 `WEB` 只剩历史行。见 G11 与 §3 T7。）**
4. **bad case 未自动回灌**：本轮只产出队列，人工挑选后加入数据集，未做自动去重与标注。

**DoD**：✅ 用例总数 101（≥100）；`python evals/run.py` 输出含轨迹断言；`python evals/replay.py --self-check` 对同一版本 diff 为空。

### T4 渠道抽象与接入（Sprint 3）

**状态：✅ 已完成（2026-10-24）**。渠道层落在 `app/channels/`（base / registry / gateway / store / render / web / email / im 共 8 个模块），**`app/agent/` 与 `app/services/` 零改动**——`ChannelGateway` 是唯一把传输请求变成 agent turn 的地方，加渠道不加分支。Web / 邮件 / 微信 / 企业微信四个渠道可用，其余四个只有能力声明。全量 **382 passed / 3 skipped**（收集 385，T4 那轮的读数；T7 之后当前全量见 §0 与 §1.1），新增 `tests/test_channels.py` 13 条（T4b 与 T7 陆续补测到 14 条，T8 再加 6 条后现为 **20 条**）。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T4-1 | 渠道协议：`ChannelAdapter` + `ChannelCapabilities` + `MessageEnvelope`（另含 `InboundRequest` / `RenderedMessage` / `Attachment`） | 新增 `app/channels/base.py` | ✅ 协议只有归一化 / 渲染 / 校验，无业务逻辑；`verify_hmac`、`resolve_identity`、`envelope_digest` 是共享工具，不是每个渠道各写一份 |
| T4-2 | Web 渠道：把现有对话包成适配器 | 新增 `app/channels/web.py`、`app/api/v1/channels.py`、`app/channels/gateway.py` | ✅ `test_web_channel_matches_existing_support_route` 断言新路由与 `/support/messages` 同一份响应；不带幂等头时就是透明包装 |
| T4-3 | 消息去重表 | 新增 `app/models/channel_message.py` + 迁移 `a9d4c2f7b135` | ✅ `(channel, channel_message_id)` 唯一；重复投递直接回缓存的回复；同 id 换内容 → 409 `CHANNEL_MESSAGE_ID_REUSED`；仍在处理中 → 409 `CHANNEL_MESSAGE_IN_FLIGHT`（retryable） |
| T4-4 | 邮件渠道：入站 webhook 归一化 + 出站正式模板 | 新增 `app/channels/email.py` | ✅ 出站正文含「受理编号」「`{sla_hours}` 小时内首次回复」「—— TicketPilot 客服团队」与「请勿直接回复」；`max_cards=0` 时参考资料退化为编号列表纯文本 |
| T4-5 | IM 渠道：微信 / 企业微信 webhook（签名校验 + 幂等） | 新增 `app/channels/im.py` | ✅ `max_length=200`；卡片降级为「详情：1.标题 2.标题」，当索引本身超过预算一半再降级为「详情：回复「详情」查看」；`test_im_identity_comes_from_config_not_the_payload` 守住「payload 自称的 tenant 一律无效」 |
| T4-6 | 渠道响应矩阵 | 新增 `app/channels/render.py`；`GET /api/v1/channels` | ✅ 8 个渠道 × 能力字段（stream / rich card / voice / max_length / max_cards / formal / offers_human_handoff）；`enabled` 只取决于是否注册适配器；裁剪只在句边界切并留省略号，`truncated` 显式回传 |

**真跑记录（2026-10-24）**：真实启动服务（`127.0.0.1:8124`，`CHANNEL_EMAIL_ENABLED=true` + 一次性自定密钥 + `AUTO_CREATE_TABLES=false`），74 条 HTTP 断言 `total=74 passed=74 failed=0`。渠道部分逐个确认：缺签名头 401、签名错 401、时间戳漂移 401、未映射发件人 403、未启用 / 未知渠道 404、重复同内容命中缓存且**不重跑对话**、重复换内容 409、矩阵中 6 个渠道 `enabled=false`。

**与计划的偏差（诚实记录）**：

1. **HMAC 契约是本仓库自定的**：真实服务商各有签名与加密方案（SendGrid 用 IP 白名单 + 签名头，企业微信用 AES 消息体）。这里签名的是 `timestamp + '.' + raw_body`，属于可替换占位——接真实渠道时改适配器，不改网关。
2. ~~**幂等只在渠道路由上生效**~~ **已由 T8 收口（2026-10-24）**：当时 `/api/v1/channels/web/messages` 带 `X-Channel-Message-Id` 才写 `channel_messages`，旧路由 `/api/v1/support/messages` 是透明入口、不写幂等表，控制台前端还调旧路由。真跑时在旧路由上等 `X-Channel-Deduplicated` 头、一直等不到，才把这条差异定位出来。现在两个入口都经 `ChannelGateway.accept_web`，同一个键只算一次投递；**残留的差别只剩「调用方没带键」**，见 §3 T8 偏差 1。
3. **`claim()` 发生在 `normalize()` 之后**：签名、字段、身份映射任一失败的投递不会留下 `channel_messages` 行。好处是脏数据不进表；代价是「同一 id 反复失败」没有计数，只能靠日志与限流看。
4. **附件只接不解析**：`Attachment` 进信封也参与 `envelope_digest`，但没有 OCR / 病毒扫描 / 类型白名单；「只有附件没有正文」明确回 422「Attachments are not processed yet; send a text description.」——不过这条分支实际打不到，邮件适配器在 `normalize()` 阶段就先抛「Email webhook has no readable text body.」，网关里的附件文案目前是死代码。
5. **四个渠道只有能力声明没有适配器**：APP / WHATSAPP / VOICE / SMS 能被矩阵描述、能被渲染器塑形（`test_voice_and_sms_shapes`），但入站路由对它们返回 404。语音 IVR 完整电话栈仍在本轮范围外。
6. **`resolve_identity` 的映射值必须是 `tenant:user`**：写成别的格式不会静默降级，而是 503 `DEPENDENCY_UNAVAILABLE`（「The EMAIL identity map is malformed; expected 'tenant:user'.」）。真跑第一次就踩了这个坑，所以单独记下来。

**DoD**：✅ 新增渠道零改动 `app/agent/` 与 `app/services/`；Web / 邮件 / IM 三条端到端路径各有用例并通过；渠道契约在 `docs/API.md` 与 `docs/adr/ADR-016-channel-abstraction.md` 留档。

### T4b 渠道溯源（Sprint 3）

**状态：✅ 已完成（2026-10-24）**，但只覆盖到「有转人工的会话」。`SupportAgent.handle()` 增加 `channel` 关键字参数，写进移交包与放弃事件；`test_handoff_bundle_keeps_the_channel_it_arrived_on` 守住它。真跑证据：EMAIL 入站触发转人工后，`GET /api/v1/handoffs/{event_id}` 的 bundle 里带 `"channel": "EMAIL"`。

**边界**：`channel` 只做溯源，注释里写明「never a decision branch」——路由、资格判定、检索都不读它（这条是刻意的，避免渠道变成隐式权限）。当时任务侧还没有渠道字段，所以没触发转人工的会话在抽样脚本里仍回落 `WEB`；**这一条由 T7-5 补上**（`SupportAgent` 在 `classify()` 之后写 `context["channel"]`，顺序保证它进不了决策输入，见 §3 T7）。

### T5 长期记忆与主动服务（Sprint 4）

**状态：✅ 接口与治理已完成（2026-10-24）**；投递出口与个性化当时未闭环（G6、G7），**现均已收口**——G7 由 T7-4 收口（`app/channels/outbound.py` 出站 + ADR-017），G6 由 T9 收口（档案进答案但只有四种模板效果 + ADR-018）。三张新表（`customer_profiles`、`proactive_rules`、`notifications`，迁移 `c3b8e5a71d29`）+ `/api/v1/memory` 下 9 个操作 + 21 条用例（`tests/test_memory.py`）+ ADR-014。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T5-1 | Profile 表 + 读 / 改 / 删 / 导出 | 新增 `app/models/memory.py`、`app/services/memory.py`、`app/schemas/memory.py`、`app/api/v1/memory.py` | ✅ 档案 8 个字段；`MemoryUpdateRequest` 是 `extra="forbid"`，7 个可写字段之外一律 422；`test_export_reports_counts_without_values` 断言导出不回值、`test_delete_erases_profile_notifications_and_queue` 断言删档连带取消排队中的主动消息 |
| T5-2 | 规则渲染的主动通知 + 客户确认 | 新增 `app/services/proactive.py`；确认 / 取消两个端点 | ✅ 三类事件（退款到账 / 工单进展 / 物流异常）；种子规则 `requires_confirmation=True`，通知先落 `PENDING_CONFIRMATION`，客户 `POST .../confirm` 才写 Outbox；确认幂等、取消后不可再确认、他人不可代操作 |
| T5-3 | 退订与频率控制 | 同上 | ✅ 首次触达因 `NO_CONSENT` 被抑制（默认关闭）；`muted_event_types` 命中回 `UNSUBSCRIBED`；`max_notifications_per_day`（1–20）命中回 `FREQUENCY_CAP`；`event_key` 重复回 `DUPLICATE`；冷却期命中回 `COOLDOWN` |

**关键实现口径**：

- **偏好是白名单，不是自由字典**：只允许 `contact_window`（≤40 字、拒 PII）、`product_interests`（≤10 项）、`tone ∈ {concise, detailed}`；键不认识就 422。真跑踩过两次：`{"carrier": "SF"}` 被拒、`"tone": "简洁"` 被拒。
- **`preferred_name` 拒绝 PII，`history_summary` 掩码后存**：姓名里出现手机号是拒（422），历史摘要里出现手机号是 `redact()` 之后落库——客户合法提到编号，但存的那份不能带原文。
- **`emit()` 的门是有序的**：事件类型 → `event_key` → 规则 → 同意 → 退订 → 重复 → 冷却 → 当日额度 → 上下文白名单 → 模板字段完整性。前八道任一命中就 `SUPPRESSED`，**后面的入参校验根本不会执行**，所以「422 还是抑制」取决于该用户此前有没有触达历史。
- **主动消息只到 Outbox**：确认后写 `proactive.confirmed`，`POST /api/v1/outbox/deliver` 实测 `{claimed:1, sent:0, failed:1, dead_lettered:0}`，`last_error = "MessageNotDelivered: No handler for proactive.confirmed."`——**没有出口 handler，这是设计上的留白而不是漏测**。（这条读数是 T5 那轮的；T7-4 注册 handler 后同一请求实测 `{claimed:1, sent:1, failed:0}`，通知落 `SENT`。见 §3 T7。）

**与计划的偏差（诚实记录）**：

1. **答案生成路径不读记忆**（G6）：客户写下「希望工作日白天收货」之后，回复文本不会因此变化。本轮只交付「客户能查看、修改、导出、删除自己的档案」这层治理，没交付「档案影响回答」这层能力。**→ 已由 T9 收口**（2026-10-24，见本节末 T9），但收口方式是「只允许四种模板效果」，「工作日白天收货」这类自由文本仍然不会改变回复。
2. **规则只有读接口**（G10）：`GET /api/v1/memory/rules`，没有 POST / PATCH / DELETE；三条规则由 `app/seed.py` 落库，改话术要改代码重跑种子。
3. **门序把校验藏在抑制之后**：这是真实顺序，不是 bug，但对接方容易误解「传了非法 context 为什么没报错」——已在本节上面写清。
4. ~~**`SENT` 对主动消息不可达**~~ **已由 T7-4 解决（2026-10-24）**：当时 Outbox worker 重试到 5 次然后进死信，演示里看到的是「确认成功但永远收不到」而不是报错；现在 `proactive.confirmed` 有消费者，`SENT` 只在传输写成功时落。默认的 `none` 传输仍然拒绝一切发送——**"发不出去"依旧是默认行为，只是不再是漏了 handler，而是没配出口**。README 已知边界、`docs/API.md` 的出站章节与 `docs/SECURITY.md` 都写明了这一口径。

**DoD**：✅ 记忆与主动服务 21 条用例全绿；真跑走完「开同意 → 触发事件 → 待确认 → 确认 → Outbox 领取 → 失败留痕 → 导出 → 删除并连带取消」；跨租户与跨用户隔离各有断言。

### T6 稠密检索与评测编排（有条件再做）

**状态：✅ 护栏与评测编排已完成（2026-10-24）；真实端点数字已补（2026-10-25）**。新增 20 条用例（`tests/test_retrieval_channels.py`）与 `evals/retrieval_metrics.py --channels bm25,dense,hybrid,rerank`；稠密用本机 LM Studio 的 `bge-m3`，重排用 Docker Infinity 的 `bge-reranker-v2-m3`。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T6-1 | 稠密通道 + RRF / z-score 融合 | `app/retrieval/`（7 个模块） | ✅ `test_hybrid_surfaces_a_chunk_only_the_dense_channel_found` 证明稠密能补 BM25 漏召；`test_hybrid_falls_back_to_sparse_when_the_dense_channel_fails` 证明稠密挂不影响返回 |
| T6-2 | 向量落库与维度守卫 | `document_chunks.embedding`（JSON 列）、`scripts/embed_policies.py` | ✅ 混维度直接拒（「returned mixed dimensions」）；换 embedding 空间要求 `--rebuild`；`test_hybrid_ignores_a_stale_embedding_space` 守住陈旧向量不参与排序 |
| T6-3 | 租户隔离 | 同上 | ✅ `test_hybrid_never_returns_another_tenants_chunks`——稠密候选仍先在 `sparse.visible_chunks(identity)` 内产生 |
| T6-4 | 重排通道的失败语义 | `app/retrieval/`（rerank） | ✅ 端点不可达时 fail-open，不改变召回集；连续失败后开熔断，不逐个请求都撞一次超时；真实端点 61 次调用 0 失败 |
| T6-5 | 多通道评测与门禁 | `evals/retrieval_metrics.py`、`.github/workflows/ci.yml` | ✅ 同一次运行内并排 bm25 / dense / hybrid / gated / rerank 五列；**门槛只套在第一个渠道上**（JSON 里 `gate_channel`），CI 跑 `--channels bm25,hybrid` 所以 `GATE PASSED` 说的是 bm25 |

**实测数字（61 条用例，其中 17 条带标注；5 份政策 / 17 个 chunk，本机 SQLite + 真实端点，PostgreSQL 逐位一致）**：recall@1 0.7941 → 0.8529（dense）→ 0.9118（rerank）；recall@3 0.9118 → 0.9706（hybrid / gated）→ 0.9118（rerank）；recall@5 四行都是 0.9706；MRR 0.8745 → 0.9265 → 0.9559；nDCG@5 0.8824 → 0.9220 → 0.9438。稠密改的是名次，融合补的是覆盖，门控省调用（embedding 调用率 0.2941）不改读数，重排买头部但把 recall@3 压回去。哈希替身那组旧数字（recall@5 0.9706 → 0.9118）只作为 CI 编排证据保留，完整表在 `docs/EVALS.md` §当前基线。

**与计划的偏差（诚实记录）**：

1. **哈希替身只在 CI**：真实端点跑出的五行是上表；CI 没有端点，`--channels bm25,hybrid` 仍走哈希，那一列不可与真实数字混读。17 条标注的演示语料不足以外推。
2. **稠密是全表扫描**：对候选 chunk 在 Python 里逐条算余弦相似度，没有 pgvector、没有 ANN。小语料没问题，`document_chunks` 上万条要重新设计。
3. **rerank 真跑了，但不免费**：LM Studio 没有 `/rerank`，用 Docker Infinity 服务 `bge-reranker-v2-m3`；头部精度上升、recall@3 反而下降、每次约 2.3 秒（CPU）。延迟预算已由 `evals/rerank_latency.py` 判定为 **OVER BUDGET**（p95 2602 ms vs 300 ms），但仍没有生产部署与容量数据。
4. **默认仍是关**：`RETRIEVAL_DENSE_ENABLED` / `RETRIEVAL_RERANK_ENABLED` 默认 false，不打开就与 T6 之前完全一致。

**DoD**：✅ 换检索器前后能出对比数字并进 CI；离线脚本无网络也能跑；真实 embedding / rerank 端点已在本机验证（不宣称生产可用）。

### T7 新能力的界面与出口（Sprint 5）

**状态：✅ 已完成（2026-10-24）**。四条依赖本轮已就绪后端；G7 / G9 / G11 已覆盖，**G10（规则可配置）当时有意留下**——它要写接口 + 审计 + 冷却口径的灰度，不适合塞进这一期；**G10 已由 T10（见下文）收口**：`PUT /api/v1/memory/rules/{event_type}` + 控制台第 10 页，但**按租户灰度那一格仍是有意不做**（ADR-019、ADR-012 空位不回收）。

| ID | 任务 | 覆盖缺口 | 涉及文件 | 验收 |
|---|---|---|---|---|
| T7-1 | 渠道矩阵页：8 渠道能力 + `enabled` 状态 + 幂等表计数 | G9 | 新增 `web/src/pages/ChannelMatrix.tsx`；改 `App.tsx` | ✅ 页面只读 `GET /api/v1/channels`；未启用渠道灰显且不隐藏；客户身份被门禁拒绝 |
| T7-2 | 客户档案页：查看 / 修改 / 导出 / 删除自己 | G9 | 新增 `web/src/pages/MemoryPage.tsx` | ✅ 「删除我的记忆」一键走完 T5 的删档 + 连带取消排队通知；页面只显示掩码后的摘要，永不显示原文 |
| T7-3 | 待确认通知列表：确认 / 取消 | G9 | 复用 T7-2 页 | ✅ 未同意的用户看不到任何通知入口；`COOLDOWN` / `FREQUENCY_CAP` 抑制只写「暂时不会推送」，不解释内部原因；新增 `contact_channel` 选择器，只列真能送到的渠道（`EMAIL` / `WECHAT` / `WECOM`），存档渠道不在可送集合里时显示「（当前送不到）」而不是假装未设置 |
| T7-4 | 主动消息出口：注册 `proactive.confirmed` 的 handler 并真正发一次 | G7 | 改 `app/services/runtime.py` 的 `_outbox_sink`；新增 `app/channels/outbound.py`；改 `.env.example` | ✅ 出站走 T4 渲染器（邮件带主题与署名、IM 裁到 200 字）；`SENT` 首次可达；发送目标只能是**队列行的租户+用户 → 档案里的渠道 → 服务端身份映射反查出的地址**，`payload` 里的任何指向都改不了去向；四种拒绝码 `[NO_TRANSPORT]` / `[NO_CHANNEL]` / `[NO_ADDRESS_SOURCE]` / `[NO_CONSENT]` 各有用例与实跑证据 |
| T7-5 | 任务上下文带上渠道 | G11 | 改 `app/services/runtime.py` 的 `context`；`scripts/sample_traces.py` 读它 | ✅ 未触发转人工的会话在抽样里也标真实渠道；`_channel()` 的 `WEB` 回落只在历史数据上出现 |

**真跑记录（2026-10-24）**：真实启动服务（`127.0.0.1:8128` 起，端口 8127 被上一轮遗留进程占用，**没有杀那个进程**，换 8129 继续；`AUTO_CREATE_TABLES=false`，库由 `alembic upgrade head` + `scripts/seed.py` 建在 `.tmp/` 下）。逐项确认：

- 默认 `CHANNEL_OUTBOUND_TRANSPORT=none`：`POST /api/v1/outbox/deliver` → `{"claimed": 1, "sent": 0, "failed": 1, "dead_lettered": 0}`，`last_error` 逐字含 `[NO_TRANSPORT]`，通知停在 `CONFIRMED`——**没有配置就不自称送达**；
- 未选渠道 → `[NO_CHANNEL]`；渠道选 `SMS`（有枚举、没有地址来源）→ `[NO_ADDRESS_SOURCE]`；
- 切 `local_file` + 落盘路径 + 渠道 `EMAIL` → `{"claimed": 1, "sent": 1, "failed": 0, "dead_lettered": 0}`，通知 `SENT`，落盘文件多一行 JSON（含 `subject`「TicketPilot 提醒 · 工单进展提醒」、正文、`"truncated": false`）；
- **退避不是坏**：失败后立刻再点投递得到 `{"claimed": 0, ...}`，等过 `available_at`（30 秒、再 60 秒）才被重新认领并成功；
- 审计 `proactive.delivered` 的 `detail` 只有 `channel` / `event_type` / `transport` / `chars` / `truncated` / `address_digest`（SHA-256 前 12 位），**查不到明文地址**；
- 浏览器侧（真实 DOM）：「我的记忆」勾选「邮件」→ 保存（`PUT /api/v1/memory` 200，`preferences.contact_channel = "EMAIL"`）→ 待确认通知点「同意」→ 坐席点投递 → 两条通知徽标同时变「已推送」，并出现「不等于服务商送达回执」的脚注。
  这一轮踩过的坑：`click` 在保存按钮上没有发出 PUT（网络面板只有 GET），改用页面上下文 `b.click()` 才触发——**按钮可点 ≠ 请求发出，必须看网络日志**。

**建议同时补的评测**（避免 T7 之后出现盲区）：T4 的渠道端到端 3 条 + T5 的「记忆不影响答案」反向断言 1 条——后者断言的是「改档案之后回复文本不变」，等个性化真正落地时再删掉。**这条建议已过期**（2026-10-24，T9）：反向断言从未进过评测集，实测 `evals/` 里没有任何用例调 `PUT /memory`，种子库也不建档案；个性化落地后无档案客户的 `40/40` + `61/61` + `REPLAY DIFF EMPTY` 三条读数与 T8 逐字相同，所以既不需要删断言，也没有出现盲区。真正需要记录的是另一件事：**有了档案的客户，其历史 trace 不再逐字可回放**（实测，见 §3 T9 偏差与 `docs/EVALS.md`）。

### T8 把控制台迁到渠道入口（Sprint 5 补充）

**状态：✅ 已完成（2026-10-24）**。收的是 T4 偏差 2 与 `docs/AGENT_PLATFORM_PLAN.md` 风险表 B10：幂等表只由渠道路由写，而对话页调的是旧入口，所以「重复投递读回已存回复」这件事在界面上永远观察不到。改完之后**两个 web 入口共用同一道闸**，`tests/test_channels.py` 从 14 条增至 20 条，全量 **408 passed / 3 skipped**（收集 411）。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T8-1 | 旧入口改走网关：不再直接 `SupportAgent.handle`，改调 `ChannelGateway.accept_web` | 改 `app/api/v1/support.py`、`app/api/deps.py` | ✅ 响应体与状态码逐字不变（仍是 `SupportMessageResponse`）；带 `X-Channel-Message-Id` 才写 `channel_messages`，去重命中时在响应头回 `X-Channel-Deduplicated: true`；不带时**表里一行都不多**（`test_support_entry_without_a_delivery_id_records_nothing`） |
| T8-2 | 一个幂等键 = 一道闸，跨两个入口 | 同上，复用 `app/channels/gateway.py` | ✅ `test_one_delivery_id_is_one_guard_across_both_web_entries`：同一个键先走渠道路由、再走旧入口，第二次读回同一条回复且不产生第二个任务；`test_support_entry_rejects_a_reused_delivery_id_with_other_content` 断换内容仍 409 |
| T8-3 | 把两条只写在文档里的行为变成用例 | `tests/test_channels.py` | ✅ `test_delivery_still_in_flight_conflicts_instead_of_running_twice`（真实 `PROCESSING` 行 → 409 `CHANNEL_MESSAGE_IN_FLIGHT`、`retryable=true`、零新任务）；`test_a_stale_claim_is_taken_over_and_completed`（`channel_delivery_claim_timeout_seconds=0` → 陈旧认领被接管并补完，`attempts` 变 2）——**这个配置项此前从未被任何测试执行过** |
| T8-4 | 控制台前端改调渠道路由并自带幂等键 | 改 `web/src/api.ts`、`web/src/pages/ConversationPage.tsx` | ✅ `sendSupportMessage(identity, message, context, deliveryId)`；每次发送生成一个 `dlv_*`，**同一句话在同一段会话里重发复用同一个键**，成功后作废、失败后保留——用户再点一次是「重试同一次投递」，不是「说第二遍」 |

**关键实现口径**：

- **旧入口的 JSON 契约没变**：`accept_web` 返回的是 Agent 的原始 `response`，不是渠道渲染结果。能力裁剪（IM 200 字、邮件正式署名）只作用在 webhook 回复与出站通知上；`test_web_channel_matches_existing_support_route` 继续守「两条路由响应一致」这条。
- **幂等键只认 Header**：`WebChannelAdapter._message_id` 允许 body 里的 `channel_message_id`，但旧入口的 `SupportMessageRequest` 是 `extra="forbid"`，从 body 收键会变成契约变更，所以旧入口只读 `x-channel-message-id`。
- **失败会留痕**：`agent.handle` 抛异常时 `store.fail(..., error_code="AGENT_ERROR")`，行留在表里而不是被删；一个卡住的键由认领超时接管，不会永久占位。

**真跑记录（2026-10-24）**：真实启动服务（`127.0.0.1:8130`；`AUTO_CREATE_TABLES=false`，库是 `.tmp/t8_live.db`，由 `alembic upgrade head` + `scripts/seed.py` 建），23 条 HTTP 断言 `total=23 passed=23 failed=0`。分组确认：渠道路由重放只跑一轮 / 换内容 409；**旧入口带同一个键同样去重**，不带键时连点两次 `channel_messages` 行数不变；只读回答在两条路由上逐字相同；`/console/` 实际下发的 bundle 里含 `/api/v1/channels/web/messages` 字符串（证明部署产物真的换到了新入口）。浏览器侧（真实 DOM）：装一个 fetch 拦截器**丢掉一次响应的返回**（服务端已经跑完那一轮），输入框恢复原话 → 再点发送 → 两次请求同一个 `dlv_1792832686001_dd2a86`、第二次 `X-Channel-Deduplicated: true`、`task_8a3f7fd64a4a4e8b` 是同一个、alice 的任务总数 10 → 10；随后以坐席身份打开渠道矩阵，`WEB` 行从 `COMPLETED 8 · PROCESSING 1` 变 `COMPLETED 9 · PROCESSING 1`，正好等于刚在控制台发出去的那一条（那条 `PROCESSING` 是验收脚本注入的在途行，属环境产物）。

这一轮踩过的两个坑：① MCP 的 `fill` 只写 DOM 不驱动 React 状态——字数计数停在 0/500、发送按钮 `disabled`、网络日志为空，页面看起来"填好了"但请求根本没发出；改成原生 `HTMLTextAreaElement.prototype.value` setter + `dispatchEvent(new Event('input', {bubbles:true}))` 再在页面上下文 `click()` 才发出请求。这与 T7 记的「按钮可点 ≠ 请求发出，必须看网络日志」是同一类陷阱。② 想用裸 SQL 造一条在途行，撞 `NOT NULL constraint failed: channel_messages.direction`——该列只有 Python 侧默认值，数据库层没有；改走 ORM（`build_engine` + `ChannelMessage(...)`）插入才成立。

**与计划的偏差（诚实记录）**：

1. **不带幂等键仍然是一次调用跑一轮**：旧入口的默认行为刻意保持不变（T4 的「零行为变化」验收还靠它）。收口的是「同一个键在两个入口只算一次投递」，不是「所有调用都自动去重」——去重需要客户端提供一个稳定 ID，服务端无法凭「文本长得一样」判定重复。
2. **幂等键活在前端内存里**：刷新页面或改写那句话都会换新键。真正的「刷新后仍能识别为同一次投递」需要把键落到 URL 参数或 localStorage，本轮没做（控制台是演示定位，且已存在的 `conversation_id` 承接了连续性）。
3. **`PROCESSING` 接管只在单进程内可观测**：认领超时靠 `attempts += 1` 与时间戳，跨进程重复投递仍需数据库唯一约束兜底——**Postgres 侧未复跑**（见 §0 第 4 项）。

**DoD**：✅ 两个 web 入口共用同一道幂等闸且有跨入口用例；控制台真实浏览器上看到 `X-Channel-Deduplicated`；旧入口默认行为逐字不变（101 条评测 + 全量用例回归通过）；文档不再需要「演示只能用新入口」这句免责。

---

### T9 把记忆接进答案生成（G6，Sprint 5 补充）

**状态：✅ 已完成（2026-10-24）**。收的是 [ADR-014](adr/ADR-014-customer-memory-proactive-consent.md) 自己点名的那条后果——「记忆目前只被存与查，没有注入 Agent 回复」——也是 §0 第 2 项列出的两条断链里可离线验证的那一条。

**先说清楚为什么这一步需要重新评测口径**：ADR-014 当年不做注入，不是因为难，而是因为「回复依赖一张客户可随时改写的表」。所以本轮的设计目标不是「让记忆影响回复」，而是**「让记忆只能以少数几种可枚举、可断言、可关掉的方式影响回复」**——影响面收在一个函数里，且默认对没有档案的客户零影响。

#### 已核实的六条前提（读代码确认，非假设）

| # | 前提 | 证据 |
|---|---|---|
| 1 | **种子不建档案** → 没有客户 `PUT` 就没有 profile 行 | `app/seed.py` 全文无 `CustomerProfile`；该模型只被 `app/services/{memory,proactive}.py`、`app/channels/outbound.py` 引用 |
| 2 | `profile_view(None)` 返回默认视图且**不建行** | `app/services/memory.py:48` |
| 3 | `MemoryService.get()` 会写一条 `memory.read` 审计，Agent 每轮都读就会**每轮多一行 `audit_events`** | `app/services/memory.py:80-89` |
| 4 | 「本会话第一轮」已有零成本信号：`ConversationSlots.last_intent` 只在轮末写入，新建会话为 `None` | `app/services/conversations.py:30`、`app/agent/support_agent.py:171` `carried = turn.slots` |
| 5 | 商品卡 `card_id == product.product_id`、`title == product.name` → 档案里的兴趣能用 `CatalogService.resolve()` 换回**服务端所有**的规范名 | `app/services/support.py:103-104`、`app/services/catalog.py:75` |
| 6 | 输出护栏在落库与返回之前，且 `draft.reply` 原地改写 | `app/agent/support_agent.py:514-554`，调用点在其后 |

#### 设计

新增 `app/agent/personalization.py`：一个**纯函数式**的覆盖层，输入 `Draft` + 档案快照 + 目录，输出改动后的 `Draft` 与「用了哪几条效果」的清单。`SupportAgent.handle` 在决策塑形（`_shape`）之后、`_guard_output` 之前调用它一次——顺序保证「先个性化、再过护栏」，护栏永远是最后一道。

- **读档案不复用 `MemoryService`**（前提 3）：用 `select(具体列)` 只取 `preferred_name / preferences / updated_at`，不实例化 ORM 实体，所以既不会建行、也不会被 autoflush 改到，更不写审计。跨租户/跨用户按 `identity` 过滤，读不到就是没有。ADR-014 那句「`app/agent/` 里没有任何一处会调用它」据此改写为「**Agent 只读、且不走 `MemoryService`；写路径仍然只有客户自己的 `PUT`**」。
- **四种效果，各自一个原因码**，全部是模板或形状变化，不吃客户原文：

| 原因码 | 触发条件 | 效果 | 为什么安全 |
|---|---|---|---|
| `ADDRESSED` | 有 `preferred_name` **且**是本会话第一轮（澄清轮同样套模板，`clarification.prompt` 本身不动） | 在回复前加一句服务端模板「你好，{称呼}。」 | 该字段写入时 ≤40 字且命中 PII 直接 `422`；只出现一次，不每轮重复 |
| `CARDS_REORDERED` | `product_interests` 里某项经 `CatalogService.resolve()` 命中目录，且该商品卡已在 `draft.cards` 里但不在首位 | 把那张卡挪到第一位 | **回声的是目录里的规范名，不是客户写的字符串**；命不中就完全不动 |
| `SUGGESTIONS_TRIMMED` | `tone == "concise"` 且建议条数 > 2 | 建议裁到 2 条 | `tone` 是枚举，白名单外写入即 `422` |
| `INTEREST_SUGGESTION` | 命中目录的兴趣**既不是本轮已回答的商品，也没有已经作为商品卡在答子里**，且建议里还没有它 | 在建议首位插入「{目录商品名} 的质保范围」 | 同上，模板 + 目录名；「已经在屏幕上」的两条判据把重复追问挡掉，卡片重排用例与澄清用例各守一条 |

- **明确不是效果的字段**：`history_summary`（掩码存的客户自由文本，永不进回复，有专门用例断言它的词不出现）、`locale`（没有多语言生成路径，接它等于假装有）、`contact_channel` / `contact_window`（前者只归出站投递读，后者是自由文本）、`proactive_opt_in` / `muted_event_types` / `max_notifications_per_day`（属于推送闸门，与答案无关）。
- **可见与可关**：响应新增可选字段 `personalization`，只含**原因码与用过的字段名**，不含任何档案值（与审计同一条纪律）；trace 加一条 `generate / memory.personalize` span，同样只记原因码；`MEMORY_PERSONALIZATION_ENABLED`（默认开）整轮跳过。理由：D3「策略外置」，且运营需要能在不改代码的情况下把这条口子关掉。
- **评测口径的影响面**：种子不建档案 ⇒ Golden 40 / 支持 61 / `REPLAY DIFF EMPTY` 三者逐字不变，这是**可以断言的**（用例直接比「开关打开 vs 关掉」在无档案客户身上的输出）。真正需要记录的限制是：**一旦某客户有了档案，那之前的历史 trace 就不再能逐字回放**——本轮把它写成偏差而不是「无影响」。

| ID | 任务 | 涉及文件 | 验收 |
|---|---|---|---|
| T9-1 | 个性化覆盖层 + 只读档案加载 | 新增 `app/agent/personalization.py` | ✅ `personalize()` 纯函数（现造 `Draft` 直接单测，`test_a_matching_interest_moves_its_own_card_first` / `test_a_card_that_is_already_first_is_not_counted_as_reordered` 就是纯函数用例）；`load_profile()` 用 `select(具体列)` 取列、不实例化实体，`test_the_answer_path_creates_no_profile` 断言问答路径不建行 |
| T9-2 | 接进 `handle` 的决策之后、护栏之前 | 改 `app/agent/support_agent.py` | ✅ `_personalize` 位于 `_shape`（360）与 `_guard_output`（395）之间，护栏仍是最后一个碰文本的环节；澄清轮只改 `reply`、`clarification.prompt` 不动（`test_a_clarification_turn_is_personalized_in_the_reply_only`）；接管静默轮返回 `personalization: null` 且**触发接管那一轮照常带称呼**（`test_a_silent_turn_under_takeover_is_not_personalized`） |
| T9-3 | 开关 + 响应字段 + span | 改 `app/core/config.py`、`app/schemas/support.py` | ✅ `test_the_switch_restores_the_plain_answer` 拿两个进程同一份档案对比，断开关关时的回复**逐字等于**开时回复去掉「你好，Alice 女士。」前缀、建议不变；`test_the_greeting_is_recorded_in_the_trace_before_the_answer_span` 断 span 位置；响应体只有 `effects` + `fields`，没有档案值 |
| T9-4 | 用例 | 新增 `tests/test_personalization.py` | ✅ **18 条全绿**：无档案零影响（`test_a_customer_without_a_profile_is_not_personalized`）、称呼只在一轮、`history_summary` 与 `contact_window` 不进回复、非目录兴趣惰性、目录兴趣变成一条追问、已答商品不再补追问、`concise` 只裁建议、卡片重排（纯函数 + 两卡 HTTP 场景）、已在首卡不动、澄清轮只改回复、个性化不写档案行也不写 `memory.*` 审计、开关关闭零变化、删档回到原样、接管静默轮不个性化 |
| T9-5 | 控制台可见 | 改 `web/src/pages/ConversationPage.tsx`、**`web/src/types.ts`**（计划里写的 `api.ts` 是错的：响应类型在 `types.ts`，`api.ts` 只是请求封装，本轮没动它） | ✅ `PERSONALIZATION_LABELS` 四条中文标签 + 一段 `<p class="support-inherit">本轮按你的档案调整过：…</p>`；未知原因码原样显示（`?? effect`），`fields` 不进界面；真实浏览器 DOM 里只出现这一段，且其中没有档案值 |
| T9-6 | 文档与 ADR | 新增 [ADR-018](adr/ADR-018-memory-in-answer-personalization.md)，改 ADR-014 后果段、README、API、ARCHITECTURE、SECURITY、SCOPE、EVALS、PROJECT_SPEC、AGENT_PLATFORM_PLAN、CONTRIBUTING、本文件 §0/§1.2/§2/§3/§4/§5/§7/§8 | ✅ 全文不再出现「记忆只被存与查 / 答案生成路径不读记忆」作为**当前事实**（T5 段与 §1.2 已就地标注为已收口）；边界与代价如实写，含实测的回放代价 |

**真跑记录（2026-10-24）**：真实起两个进程共用同一个库（`AUTO_CREATE_TABLES=false`，库 `.tmp/t9_live.db` 由 `alembic upgrade head` + `scripts/seed.py` 建；`127.0.0.1:8131` 默认开、`127.0.0.1:8132` 带 `MEMORY_PERSONALIZATION_ENABLED=false`），**59 项 HTTP 断言全过**。分组确认：问答路径不建 profile 行、不写 `memory.*` 审计；档案里的自由文本（`history_summary`、`contact_window`）与注入串不出现在回复；称呼只在本会话第一轮；`concise` 只裁建议；目录认得出的兴趣变成一条追问、认不出的一点效果都不产生；两个进程同一句话的输出差正好是那段模板前缀；渠道入口（`POST /api/v1/channels/web/messages`）同样被个性化，且幂等重投拿回的回复**逐字一致**（个性化写在落库的那条回复上，不是渲染时现算）；`DELETE /api/v1/memory` 后回到无档案基线。浏览器侧（真实 DOM）：`本轮按你的档案调整过：用你的称呼开头` 只出现一条 `<p class="support-inherit">`，页面里没有档案原值。**换干净库复跑**：另建 `.tmp/t9_live2.db`（同样 `alembic upgrade head` + `scripts/seed.py`），以 `127.0.0.1:8133`（开）/ `8134`（关）再跑一遍，仍是 `59 checks, 0 failures`；原地重跑第一次那个库会**假失败 2 条**——脚本里「答案路径不写 `memory.*` 审计」「审计里只有客户自己的 3 次 `PUT`」两条数的是**整张 `audit_events` 表**，上一轮的写入还在表里，所以这条真跑只在 fresh DB 上可复现。

**与计划的偏差（诚实记录）**：

1. **计划里的 `web/src/api.ts` 没被改**——响应类型住在 `web/src/types.ts`（见 T9-5 行）。
2. **「档案一改，历史 trace 不再逐字可回放」不是推测，是实测**：把带档案的 T9 实跑库复制成 `.tmp/t9_replay.db` 后跑 `evals/replay.py --database-url`（该参数同时是读取源与重放目标），得到 `12 recorded conversations, 12 replayable, 0 skipped` → **`38 diff lines`**，明细是 **9 条会话（10 个轮次头）**：8 处 `conclusion.reply_sha256` 不匹配 + 20 行轨迹位移（recorded 里第 N 个 span 是 `generate/memory.personalize`，无档案的重放里没有这一条，`generate/support.reply` 因此前移一位），另外 **3 条无档案会话逐字干净**。反过来，种子库不建档案，所以 `replay.py --self-check` 仍是 `REPLAY DIFF EMPTY`，`40/40` + `61/61` 三条读数与 T8 逐字相同——两个方向都跑过，才把「无档案零影响」和「有档案有代价」同时写成事实。
3. **效果只有四种，且都不吃客户原文**：`locale`（没有多语言生成路径）、`contact_window` / `history_summary`（自由文本）、「按档案预填槽位」都是**有意不做**，不是漏网。
4. **开关是进程级的**：`MEMORY_PERSONALIZATION_ENABLED` 一开全开、一关全关，没有按租户灰度——真跑时用两个进程模拟了开 / 关两侧，但那不等于灰度能力。
5. **18 条新用例未在 PostgreSQL 上复跑**（见 §0 第 4 项：本机有 Docker，缺的是这一轮的执行）。

**DoD（本轮）**：✅ ① 无档案客户的全量行为逐字不变（`40/40` + `61/61` + `REPLAY DIFF EMPTY` 三条读数与 T8 相同，且 `evals/` 里零 `memory` 引用是可核对的）；② 四种效果各有独立用例，「客户自由文本不进回复」有反向断言；③ Agent 侧零写路径、零 `memory.*` 审计行由 `test_personalizing_writes_no_profile_row_and_no_memory_audit` 与 `test_the_answer_path_creates_no_profile` 守住；④ 开关一键回到不个性化（用例 + 真实双进程各验一次）；⑤ 文档写清「档案一改，历史 trace 不再逐字可回放」并给出实测行数；⑥ 九条门禁全绿（426 passed / 收集 429 / ruff / mypy 134 文件 / 三条评测 / 回放自检 / Alembic / 前端构建）。

---

### T10 主动规则的可编辑面（G10，Sprint 6）

**状态：✅ 已完成（2026-10-24）**。收的是 §1.2 的 G10——`proactive_rules` 表、只读 `GET /api/v1/memory/rules` 与三条种子规则都在位（T5 落地、T7-4 让它真的发得出去），但**文案、冷却、启停原来只能改 `app/seed.py` 再重灌**：`app/api/v1/memory.py` 里规则只有一个 `GET`，`web/src/` 全目录 `rules` 零引用（写方案时核对过，9 个页面里没有规则页），所以运营连"看"都得 curl。现在补上了 `PUT /api/v1/memory/rules/{event_type}`（写时校验模板）与控制台第 10 页「通知规则」，**没有加表、没有加迁移**。

**前置核实（写方案前先读的，都带行号；行号已按本轮改动后的文件刷新）**：

- 事件类型是代码所有：`app/models/enums.py:138` 的 `ProactiveEventType` 只有三个值（`:141-143`），`app/services/proactive.py:38` 的 `EVENT_TITLES` 同数，`emit()` 第一步就用 enum 拒绝未知类型（`app/services/proactive.py:262` → `_event()` 在 `:177`）；`app/models/memory.py:60-62` 的 docstring 明写「服务可以抑制一条规则的结果，但不能自己发明消息或触发器」。
- 一条规则只有四个可改字段：`template` / `enabled` / `requires_confirmation` / `cooldown_minutes`（`app/models/memory.py:74-79`），且 `(tenant_id, event_type)` 唯一（`:66-69`）——所以「PUT 到事件类型上」天然是 upsert，不需要新表、不需要迁移。
- 模板渲染发生在 `emit()`：字段集合由 `Formatter().parse(rule.template)` 得出（`app/services/proactive.py:279-283`），缺字段整条抑制 `TEMPLATE_CONTEXT_MISSING`（`:285`），正文 `[:500]` 截断（`:286`）；允许的 context 键是 `_CONTEXT_KEYS` 六个（`:44-48`）。**实测（`.venv` 里直接跑 `Formatter().parse` + `format_map`，脚本在 `.tmp/probe_parse.py`）过这几种文案**：`{amount}` 正常渲染；`{foo}`、`{0}`、`{order_id.upper}`、`{amount[0]}` 的 parse 字段名分别是 `foo` / `0` / `order_id.upper` / `amount[0]`，都不在白名单里，于是**这条规则永远被抑制**，客户收不到任何东西也没有告警；`{amount!r}` 与 `{amount:>10}` 的 parse 字段名仍是 `amount`，渲染正常。
- **本轮更正了方案里两处写错的事实**（都只有真跑才会暴露，正则与静态检查都是绿的）：
  1. **`{}` 的 parse 字段名是空串 `''`，不是 `None`。** `Formatter().parse` 会把字符串**尾随的字面量**报成一个字段名为 `None` 的元组——所以 `None` 的含义是"这里没有占位符"，位置参数的真实标记是空串。方案原文按"`None` = 位置参数"来判，结果是**所有合法文案一律 422**（第一条真跑就被打回来）。修正后的判据：`field is None` → 跳过（尾随字面量）；`not field` → 位置参数，拒。
  2. **同一个 `{}` 在不同调用路径抛不同异常**：`Formatter().format("{}")` 抛 `IndexError: tuple index out of range`，而生产路径 `template.format_map(...)` 抛 `ValueError: Format string contains positional fields`。所以"会炸"是确定的，"炸成什么"取决于调用方式，不能靠捕获单一异常类型兜。
  3. 附带一条：`{order_id.upper}` 在**生产路径**里是被白名单挡掉的（静默抑制），不是渲染出 `<built-in method upper of str object at …>` 那个怪串——怪串只在 `Formatter().format` 直接调用时出现。方案原文把它当成"会渲染出怪东西"是不准确的，但它仍然是坏文案（永远不发），所以照样要拒。
- 权限面现成：`_require_seat`（`app/api/v1/memory.py:35`）要 `support`，admin 因 `Identity.has_role` 的实现自动通过（`app/core/security.py:16-17`）；错误形状 `ValidationAppError` 422 / `ConflictError` 409 / `NotFoundError` 404，`AppError` 支持 `code=` 与 `details=`（`app/core/errors.py:15-35`）。
- 非 `demo` 租户今天永远 `NO_RULE`（`tests/test_memory.py:266` 断言的就是这条）——本轮之后它可以自己建规则了。

**拆解与落地（本轮全部做完）**：

| # | 任务 | 改动面 | 落地结果（实测） |
|---|---|---|---|
| T10-1 | 写接口 | `app/api/v1/memory.py` 新增 `PUT /api/v1/memory/rules/{event_type}`；`app/schemas/memory.py` 新增 `ProactiveRuleUpdateRequest`（`extra="forbid"`，只收 `template` / `enabled` / `cooldown_minutes`） | ✅ 坐席可写、客户 403；未知 `event_type` 422 `UNKNOWN_PROACTIVE_EVENT`（带 `details.allowed`）；body 里出现 `requires_confirmation` 被 `extra="forbid"` 拒（422 `extra_forbidden`，**不是静默忽略**）；按 `(tenant, event_type)` upsert，`acme` 租户建出了 `rule_acme_refund_settled` |
| T10-2 | 写时校验模板 | `app/services/proactive.py::update_rule` / `_template()` | ✅ 引用白名单外的字段 → 422 `RULE_TEMPLATE_UNKNOWN_KEY`（**只回字段名与 `details.allowed`，不回整条文案**——方案里写的 `RULE_TEMPLATE_UNKNOWN_FIELD` 是笔误，落地的码是 `..._UNKNOWN_KEY`）；`{}` 位置参数 / 属性形式 / 下标形式 / 花括号不配对 → 422 `RULE_TEMPLATE_INVALID`；超过 400 字 → 422（渲染截断线是 500，写入时留余量，**不让存进去的文案被静默截尾**）；任何一条失败，规则行逐字不变（`test_rejected_create_persists_nothing` 断言了新建被拒后该租户规则数不变） |
| T10-3 | 只读视图补两字段 | `ProactiveRuleView` + `rules()` | ✅ 加 `required_context`（模板用到的字段名，排序去重）与 `updated_at`；三条种子规则的依赖被用例逐字钉住（`["amount","order_id"]` 等），把「这条规则要 emit 带什么」摆到界面上，而不是等人去撞抑制码 |
| T10-4 | 审计 | `proactive.rule.update` | ✅ detail 只有 `event_type` / 改动的字段名 / `enabled` / `cooldown_minutes` / `template_sha256` / `created` 六个键，**不含模板原文**（用例断言请求体里的标记串不出现在 detail 的序列化结果里） |
| T10-5 | 控制台第 10 页 | 新增 `web/src/pages/ProactiveRules.tsx`，改 `App.tsx`（导航 + `sectionLabel`）、`api.ts`（`listProactiveRules` / `updateProactiveRule`）、`types.ts`、`styles.css`（新增 `.chip--warn`） | ✅ 坐席可见可改；客户进页面得到与 `OpsDashboard` 同款的拒绝面板（**而且一个规则请求都不发**）；保存后写明「下一条通知起生效，已经生成的通知不受影响」；坏文案把服务端原话回显在输入框下方；未知占位符在页面上标成警告 chip |
| T10-6 | 用例 | 新增 `tests/test_proactive_rules.py` | ✅ **21 条**（方案里计划 15 条，实际多写了 6 条：视图字段逐字比对、拒后不落库、跨租户、审计 detail 精确字典、冷却边界、空 body） |
| T10-7 | 文档与 ADR | 新增 **ADR-019（主动规则的可编辑面）**，回头修订 ADR-014 后果段；README / API / ARCHITECTURE §5.1 / SECURITY / SCOPE / EVALS / PROJECT_SPEC / AGENT_PLATFORM_PLAN / CONTRIBUTING / 本文件 | ✅ 不再出现「规则只能改 `seed.py` 重灌」作为当前事实；边界如实写（事件类型固定、无双人复核、无灰度、无版本表、无删除） |

**设计点，先定后做**（已写进 ADR-019 的决策段）：

1. **事件类型不能从界面新增**——enum 归代码所有，`emit()` 的第一道闸就是它；把它做成可配置等于把「什么事实可以触发一条消息」交给配置面，而新增触发能力需要有消费者。界面只能在**三个已知事件**上改文案与阈值，这是「可配置」的边界，不是遗漏。
2. **`requires_confirmation` 不做成开关**——它是 ADR-014「客户先说要不要，消息才会出去」那条承诺的实现位；写接口允许关它，等于允许运营侧把同意闸拆掉。要做这个开关得先有独立的送达与撤回设计（G7 余项），所以本轮在请求体里禁止该字段（422 `extra_forbidden`），界面上渲染成只读徽标。
3. **模板字段必须在 `_CONTEXT_KEYS` 白名单内，且校验发生在写入时**——今天引用一个白名单外的字段不会有任何拒绝：要么这条规则从此**永远被 `TEMPLATE_CONTEXT_MISSING` 抑制**（`{foo}` / `{0}` / `{order_id.upper}` / `{amount[0]}` 都是这种），要么 `emit()` 直接抛 `ValueError` 变成一条 500（`{}`）。配置面的反馈速度必须是"保存时就拒绝"，而且拒绝理由要带上是哪个字段。**判据要按实测写**：`field is None` 是"尾随字面量，没有占位符"，`not field`（空串）才是位置参数——按 `None` 判会把所有合法文案拒掉。
4. **PUT 是 upsert，按 `(tenant_id, event_type)` 唯一**——种子只给 `demo` 灌三条，别的租户永远 `NO_RULE`；upsert 让「新租户开启主动服务」不再需要改代码重灌。并发写同一条由唯一约束兜住，输的一方拿 409 `RULE_ALREADY_EXISTS`。
5. **审计存 `template_sha256` 不存文案**——足够回答「当时生效的是哪一版」，又不会让可能含订单号与金额的通知正文成倍落进 `audit_events`。
6. **改动不回溯，且没有删除**——已生成的通知 `body` 在 emit 时就落库，改模板只影响下一条通知（界面提示里写明）；不提供 DELETE，因为 `notification.rule_id` 会指向已不存在的行、历史断裂，"不再发"由 `enabled=false` 表达（emit 抑制为 `NO_RULE`）。

**用例清单（`tests/test_proactive_rules.py`，21 条，全部通过）**：坐席改文案 200 且视图回显新依赖（`required_context` / `updated_at`）· 只读视图对三条种子规则逐字列出依赖 · 客户 403 · 未知事件类型 422 + 允许列表 · 模板引用白名单外字段 → 422 且规则行逐字不变 · 位置参数与属性形式 → 422 · 花括号不配对 → 422 · 超过渲染预算（>400）→ 422 而 ≤400 通过 · 空模板 → 422 · 请求体带 `requires_confirmation` → 422 且同意闸仍在 · `cooldown_minutes` 越界（−1 / 1441）→ 422 · 改文案后**下一条**通知逐字是新文案、已落库的待发通知**逐字不变** · 停用后 `emit` 抑制 `NO_RULE`、再启用恢复 · 冷却 0 让同一事件立即可再发 · 审计行只带 `template_sha256` 与字段名（断言模板原文不在 detail 序列化结果里）· 种子没覆盖的租户能自己开一条 · 新规则默认启用且能以 `enabled=false` 建 · 新建不带模板 → 422 · **被拒的创建不落库** · 跨租户既看不见也改不到 · 空改动 → 422。

**DoD 与逐条结果**：① 每条拒绝都有用例，且失败后规则逐字不变 ✅（21 条用例含"被拒的创建不落库"）；② 坏文案在写入时就被拒绝 ✅（九种拒绝形状实测，422 且带错误码）——不再有"这条规则从此永远被抑制但没人知道"和"emit 抛 ValueError 变 500"两种后果；③ 「已生成的通知不受影响」有断言 ✅；④ 审计不带正文 ✅（断言请求体标记串不在 detail 序列化结果里）；⑤ 客户角色进不了这一页 ✅（界面：`.gate` 拒绝面板 + **一个规则请求都不发**；API：403）；⑥ 九条门禁全绿 + 一次真实 HTTP 真跑 + 浏览器实测 ✅（见下方「真跑记录」）；⑦ 规模读数按实测改 ✅：OpenAPI `40 路径 / 43 操作` → **`41 / 44`**，测试文件 32 → **33**，用例总数 429 → **450 收集 / 447 通过 / 3 skipped**，mypy 文件数 **134**（本轮不加 `app/` 模块，与预期一致），业务表仍 23 张、迁移仍 **11** 条（无新迁移）。

**真跑记录（2026-10-24）**：

- 环境：`.tmp/t10_live.db` 全新库 → `alembic -x db_url=sqlite+pysqlite:///… upgrade head`（11 条迁移 → head `c3b8e5a71d29`）→ `alembic check` No new upgrade operations detected → seed（`orders 4 / policies_created 5 / rules 3`）→ `uvicorn app.asgi:app --host 127.0.0.1 --port 8135`。
- 脚本 `.tmp/t10_live.py`：**`52 checks, 0 failures`**。覆盖：规格读数（3 条规则、`required_context` 逐字）、坐席可写 / 客户 403、九种拒绝形状各自的状态码与错误码（未知事件 `UNKNOWN_PROACTIVE_EVENT` + 允许列表、未知键 `RULE_TEMPLATE_UNKNOWN_KEY` + `details.allowed`、`{}` 与属性/下标形式 `RULE_TEMPLATE_INVALID`、不配对 `RULE_TEMPLATE_INVALID`、超长 422、`requires_confirmation` 422 `extra_forbidden`、冷却 −1/1441 422、空 body 422）、改文案 → 下一条通知逐字是新文案、**已落库的待发通知逐字不变**、停用 → `NO_RULE` → 恢复、冷却改 0 立即重发、`acme` 租户 upsert 建出 `rule_acme_refund_settled` 并为 carol emit 成功、`audit_events` 里的 detail 恰好是那六个键且**不含模板原文**。
- 浏览器（真实 DOM，控制台 `http://127.0.0.1:5174/console/rules`，代理指向 8135）：坐席身份载入 3 条规则与各自的依赖字段 → 改文案保存 → 提示「已保存：从下一条通知起生效，已经生成的通知不受影响」→ **刷新后读回新文案且 `updated_at` 变了**（证明落库不是本地状态）→ 停用 → 顶部计数从 `生效 3 / 3 条` 变 `生效 2 / 3 条` → 恢复；坏文案三种（未知键 / `{}` / `{order_id.upper}`）分别把**服务端原话**回显在输入框下方（例如 `template cannot use a positional placeholder like {}.（RULE_TEMPLATE_INVALID）`），未知占位符在页面上标成警告 chip；客户身份（`demo/alice`）拿到拒绝面板，**页面一个规则请求都没发**。
- **截图没取**：应用内浏览器当时没有可见视口（`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`），所以这一轮的浏览器证据是 DOM 断言 + 服务端回读，不是图片。文档与说明书里不放这一页的截图。
- 停服：验收后关掉 8135 与 5174（本轮自己起的两个进程）；`127.0.0.1:8127` 与真实 `ticketpilot.db` 未动。

**有意不做（写进 ADR-019 后果段与 §8）**：规则版本表与一键回滚（只有审计里的 sha256，改错了手打回去）；**双人复核**（审批有四眼，规则文案没有——这是当前事实，文档里不许写成"配置有复核"）；按租户灰度与阈值外置（ADR-012 的空位，编号仍不回收）；模板引用客户档案里的任何东西（与 T9 同一条纪律：客户自由文本不进客户收到的内容）；**删除规则**（`notification.rule_id` 会指向已不存在的行，历史断裂；"不再发"由 `enabled=false` 表达）；规则改动进评测集（`evals/` 不建规则也不改规则，同出站投递的口径）。

---

## 4. 节奏与依赖

```text
Sprint 1  T1 界面                                  ✅ 已完成
Sprint 2  T2 护栏 + T3-1/2/3 用例扩量             ✅ 已完成
Sprint 3  T3-4/5/6 回放、抽样与门禁 + T4/T4b 渠道  ✅ 已完成
Sprint 4  T5 记忆与主动服务 + T6 检索通道          ✅ 接口与门禁已完成
Sprint 5  T7 新能力的界面与出口                    ✅ 已完成（G7 投递 / G9 两页 / G11 渠道字段；G10 当时留下，后由 T10 收口）
Sprint 5  T8 控制台迁到渠道入口                      ✅ 已完成（B10 / T4 偏差 2 收口）
Sprint 5  T9 记忆进答案（G6）                        ✅ 已完成（四种模板效果；自由文本进回复有意不做）
Sprint 6  T10 主动规则的可编辑面（G10）              ✅ 已完成（写接口 + 写时校验 + 第 10 页；ADR-019）
```

关键依赖五条，全部已被实际执行验证过：

- T4 必须在 T3 用例扩量之后——T4 落地时 101 条评测结论不变，说明改入口有网兜底；
- T5 必须在 T2 之后——主动推送是「无用户输入的输出」，输出护栏已在位；
- T6 必须在 T3-6 之后——多通道对比要有人认账，门禁先存在再允许「换检索器」；
- T9 必须在 T3-4 回放之后——它的核心安全断言是「无档案客户的输出逐字不变」，没有回放和评测集这句就只是口号；事实检验：T9 落地后 `40/40` + `61/61` + `REPLAY DIFF EMPTY` 三条读数与 T8 逐字相同，反向（有档案的库）也实测出了 38 行 diff；
- T1 与任何后端改动不冲突，可随时插入（T4 / T5 落地时都没动前端，7 页仍是 7 页；T7 才把前端加到 9 页，且这一轮唯一动到的后端是投递面，`app/agent/` 与规则/审批层没改；T9 动了 `app/agent/`，前端只加了一行标签展示）。

---

## 5. 整体验收基线

| # | 标准 | 判定方式 |
|---|---|---|
| 1 | 坐席可只靠控制台完成一次接管与结案 | 手工走查，无 curl |
| 2 | 超频 / 超体积 / 注入 / 违规承诺四类攻击均被拦且留痕 | 安全用例集 |
| 3 | 用例总数 ≥ 100，含轨迹断言，CI 阻断回归 | 101 条（40 + 61），`python evals/run.py` / `support_run.py` / `replay.py --self-check` 均通过 |
| 4 | 新增一个渠道不改 `app/agent/` 与 `app/services/` | ✅ T4 实测：渠道逻辑全在 `app/channels/`，两目录零改动；101 条评测结论不变 |
| 5 | 同一意图在三渠道下输出均可用且无截断歧义 | ✅ Web / 邮件 / IM 各有一条端到端用例；截断只发生在句边界且 `truncated` 显式回传。**注意是渠道级 API 用例，没进 101 条评测集** |
| 6 | 编排变化可解释：任一 `trace_id` 能还原意图→检索→工具→审批→结论 | Trace 查看器 |
| 7 | 502 条用例收集 / 496 通过 + 6 skipped（SQLite）、PG + Redis 两个容器同时指上则 502 通过 + 0 skipped，mypy strict 零错误（137 文件），Alembic 无漂移 | 既有 CI 三 job（Postgres job 仍不跑 pytest） |
| 8 | 未授权 / 伪造 / 重复的入站投递都不能变成 agent turn | ✅ 真跑：401（缺签名 / 错签名 / 漂移）、403（发件人未映射）、404（未知或未启用渠道）、409（同 id 换内容、同 id 在途）；身份只来自配置映射，payload 无法自选租户。**T8 后这条也覆盖网页两个入口** |
| 9 | 客户能查看、修改、导出并删除自己的记忆，且删除能连带取消排队中的推送 | ✅ 21 条用例 + 真跑全程走查；导出不回值、摘要落库前先掩码 |
| 10 | 确认过的主动通知能真的离开本应用，且"发不出去"绝不会被记成已送达 | ✅ T7-4：`SENT` 只在传输写成功时落；默认 `none` 拒绝一切发送（实测 `sent=0` 且通知停 `CONFIRMED`）；地址来自服务端映射反查，`payload` 改不动去向；17 条用例 + 真实 HTTP 与浏览器走查 |
| 11 | 收尾门禁全绿（T4/T4b/T5/T6/T7/T8/T9 文档同步完成后复跑） | ✅ 2026-10-24 实跑，九条门禁命令全部通过，读数见下 |
| 12 | 同一句话在网页任一个入口重发只受理一次，且**在界面上能观察到** | ✅ T8：跨入口同一幂等键有专测；真实浏览器丢掉一次响应再原话重发 → 同 `task_id`、`X-Channel-Deduplicated: true`、任务总数不变 |
| 13 | 客户档案能影响回复，但**只能以可枚举的方式**，且客户自己看得见、运营一键能关 | ✅ T9：四种原因码各有独立用例；档案里的自由文本（`history_summary` / `contact_window`）有反向断言「不出现在回复」；问答路径零建行、零 `memory.*` 审计；`MEMORY_PERSONALIZATION_ENABLED=false` 时回复逐字回到不个性化（用例 + 真实双进程各验一次）；控制台一行标签只说原因，不给档案值；18 条用例 + 59 项真实 HTTP 断言 + 浏览器 DOM 走查。**代价已实测并写明**：有档案的客户，其历史 trace 不再逐字可回放（38 行 diff） |
| 14 | 通知规则能被坐席自己改，且**坏文案在保存时就被拒**，不是发出去才发现 | ✅ T10：`PUT /memory/rules/{event_type}` 坐席 200 / 客户 403；九种拒绝形状各有 422 与错误码且点名出错字段（未知键只回字段名不回文案）；被拒的创建不落库；`required_context` 把「这条规则要 emit 带什么」摆到界面上；审计只有 sha256；改动不回溯已落库的通知；21 条用例 + 52 项真实 HTTP 断言 + 浏览器 DOM 走查。**边界如实写**：事件类型仍是代码 enum、`requires_confirmation` 写不进去、**没有双人复核**、没有版本表与一键回滚、没有按租户灰度、没有删除规则 |

> **2026-10-24（T7 完成后）收尾门禁复跑读数**（那一轮的读数，**已被下方 T8 读数取代**，保留作对照）：
> `ruff check app tests scripts evals alembic` All checks passed · `mypy app` Success: no issues found in **133** source files ·
> `pytest` **402 passed / 3 skipped**（收集 405）in 109.10s · `evals/run.py` **40/40** ·
> `evals/support_run.py` **61/61** · `evals/retrieval_metrics.py --channels bm25,hybrid` GATE PASSED
> （bm25 `recall@1` 0.7941 / `recall@3` 0.9118 / `recall@5` 0.9706 / `mrr` 0.8745 / `ndcg@5` 0.8824 /
> `cross_tenant_hits` 0；hybrid(hashing) 0.8529 / 0.9118 / **0.9118** / 0.9020 / 0.8890——门槛只套 bm25 一列，
> hybrid 的 `recall@5` 比 bm25 低约 6 个点）·
> `replay.py --self-check` 61 recorded / 46 replayable / 15 skipped(has handoff span) → **REPLAY DIFF EMPTY** ·
> 全新库 `alembic upgrade head`（11 条迁移 → head `c3b8e5a71d29`，23 张业务表）+ `alembic check` No new upgrade
> operations detected · `npm run build` 通过。
> **本轮未复跑的项**：PostgreSQL 全量（T4 起累计新增 71 条用例与四张新表未在 PG
> 验证；本机有 Docker，缺的是这一轮的执行，见 §0 第 4 项）；hybrid 列仍只有本机 SQLite 读数，且 embedding 用的是哈希替身而非真实端点；出站投递的服务商侧（只有 `none` 与 `local_file`）。

> **2026-10-24（T8 完成后）收尾门禁复跑读数**（那一轮的读数，**已被下方 T9 读数取代**，保留作对照）：
> `ruff check .` All checks passed · `mypy app` Success: no issues found in **133** source files ·
> `pytest` **408 passed / 3 skipped**（收集 411）in 80.37s，0 failed / 0 error · `evals/run.py` **40/40** ·
> `evals/support_run.py` **61/61**（改入口没动认知层，两份评测结论逐字不变）·
> `evals/retrieval_metrics.py --channels bm25,hybrid` GATE PASSED，六项读数与 T7 段完全一致 ·
> `evals/replay.py --self-check` 46 replayable / 15 skipped(has handoff span) → **REPLAY DIFF EMPTY** ·
> 全新库 `alembic -x db_url=sqlite+pysqlite:///D:/Code/TicketPilot/.tmp/t8_gate.db upgrade head`（→ head `c3b8e5a71d29`，23 张业务表）
> + `alembic check` No new upgrade operations detected · `npm run build` ✓ built in 142ms ·
> 另加一轮 T8 专属验收：23 条真实 HTTP 断言 `total=23 passed=23 failed=0` + 真实浏览器重试走查（§3 T8）。
> **本轮未复跑的项**：PostgreSQL 全量（T4 起累计 71 + T8 的 6 = **77 条新用例**与四张新表仍未在 PG 验证；本机有 Docker，缺的是这一轮的执行，见 §0 第 4 项）；
> OpenAPI 规模 `40 个路径 / 43 个操作` 与 mypy 文件数**不变**（T8 没加接口、没加模块，只改了 `app/api/v1/support.py` 与 `app/api/deps.py`）。

> **2026-10-24（T9 完成后）收尾门禁复跑读数**（T9 代码 + 第 18 条用例 + 文档同步后在**同一个树状态**上一次性复跑九条，取代上一段成为当前读数；产物 `.tmp/final_t9.xml` / `.tmp/final_pytest.txt`）：
> `ruff check app tests scripts evals alembic` All checks passed · `mypy app` Success: no issues found in **134** source files ·
> `pytest` **426 passed / 3 skipped**（收集 429，`errors=0 failures=0`）in 81.06s · `evals/run.py` **40/40** ·
> `evals/support_run.py` **61/61**（无档案客户的认知路径结论逐字不变）·
> `evals/retrieval_metrics.py --channels bm25,hybrid` GATE PASSED，六项读数与 T7/T8 段完全一致（bm25 0.7941 / 0.9118 / 0.9706 / 0.8745 / 0.8824，`stale` 与 `x-tenant` 均 0；hybrid(hashing) 0.85 / 0.91 / **0.91** / 0.90 / 0.89）·
> `evals/replay.py --self-check` 61 recorded / 46 replayable / 15 skipped(has handoff span) → **REPLAY DIFF EMPTY** ·
> 全新库 `alembic -x db_url=sqlite+pysqlite:///D:/Code/TicketPilot/.tmp/t9_final_gate.db upgrade head`（11 条迁移 → head `c3b8e5a71d29`，23 张业务表 + `alembic_version`）
> + `alembic check` No new upgrade operations detected · `npm run build` ✓ built in 153ms ·
> 另加两轮 T9 专属验收：59 项真实 HTTP 断言（同一库、`8131` 开 / `8132` 关两个进程）全过 + 真实浏览器 DOM 走查（§3 T9）；换 `.tmp/t9_live2.db`（`8133` 开 / `8134` 关）**复跑仍是 `59 checks, 0 failures`**，原地重跑旧库会假失败 2 条（那两条数的是整张 `audit_events` 表，脚本不可在同一库重放）；
> 反向对照一次：带档案的库交给 `evals/replay.py --database-url` → `12 recorded / 12 replayable / 0 skipped`、**38 diff lines**（ADR-018 的代价，不是回归）。
> **本轮未复跑的项**：PostgreSQL 全量（T4 起累计 71 + T8 的 6 + T9 的 18 = **95 条新用例**未在 PG 验证，四张新表只在一次性容器里验过迁移链，T9 未建表；本机有 Docker，缺的是这一轮的执行，见 §0 第 4 项）；
> OpenAPI 规模 `40 个路径 / 43 个操作` 用 `create_app().openapi()` 复核**不变**（T9 只在既有响应体里加一个可选字段，没加接口）；mypy 文件数 133 → **134**（新增 `app/agent/personalization.py`）。

> **2026-10-24（T10 完成后）收尾门禁复跑读数**（T10 代码 + 第 21 条用例 + 文档同步后在**同一个树状态**上一次性复跑九条，取代上一段成为当前读数；产物 `.tmp/t10_junit.xml` / `.tmp/t10_pytest.txt`）：
> `ruff check app tests scripts evals alembic` All checks passed · `mypy app` Success: no issues found in **134** source files（本轮不加 `app/` 模块，文件数与 T9 相同）·
> `pytest` **447 passed / 3 skipped**（收集 450，`errors=0 failures=0`）· `evals/run.py` **40/40** ·
> `evals/support_run.py` **61/61**（改的是配置面，认知路径结论逐字不变）·
> `evals/retrieval_metrics.py --channels bm25,hybrid` GATE PASSED，六项读数与 T7/T8/T9 段完全一致（bm25 0.79 / 0.91 / 0.97 / 0.87 / 0.88，`stale` 与 `x-tenant` 均 0；hybrid(hashing) 0.85 / 0.91 / **0.91** / 0.90 / 0.89）·
> `evals/replay.py --self-check` 61 recorded / 46 replayable / 15 skipped(has handoff span) → **REPLAY DIFF EMPTY** ·
> 全新库 `alembic -x db_url=sqlite+pysqlite:///D:/Code/TicketPilot/.tmp/t10_live.db upgrade head`（**11** 条迁移 → head `c3b8e5a71d29`，**23** 张业务表 + `alembic_version`）
> + `alembic check` No new upgrade operations detected · `npm run build` ✓ ·
> OpenAPI 规模 `40 个路径 / 43 个操作` → **`41 个路径 / 44 个操作`**（新增 `PUT /api/v1/memory/rules/{event_type}` 一个路径一个操作；既有 `GET /memory/rules` 的响应体多两个字段，不算新操作）·
> 另加 T10 专属验收：**52 项真实 HTTP 断言全过**（`52 checks, 0 failures`，服务实启于 `127.0.0.1:8135` + 干净库 `.tmp/t10_live.db`）+ 真实浏览器 DOM 走查（保存生效、刷新后回读、三种拒绝回显服务端原话、客户身份零请求，§3 T10 的「真跑记录」）。
> **本轮未复跑的项**：PostgreSQL 全量（T2 起新建的 9 个测试文件当前共 **141 条新用例**未在 PG 验证，四张新表只在一次性容器里验过迁移链，T9/T10 未建表；本机有 Docker，缺的是这一轮的执行，见 §0 第 4 项）；T10 的 `409 RULE_ALREADY_EXISTS` 走的正是唯一约束，这一条尤其需要 PG 侧对照；
> 浏览器截图未取（应用内浏览器没有可见视口），这一轮没有新增任何图片资产。
>
> **2026-10-24（T10-6 文档收口后）最终复跑**：在**文档全部收口之后**的同一个树状态上又复跑了一遍九条，读数与上面这一段**逐字一致**——`ruff` All checks passed · `mypy` 134 文件零错误 · `pytest` **447 passed / 3 skipped**（收集 450，`errors=0 failures=0`，产物 `.tmp/final_t10b.xml` / `.tmp/final_pytest_t10b.txt`）· Golden **40/40** · 支持路径 **61/61** · 检索 `GATE PASSED`（bm25 0.79 / 0.91 / 0.97 / 0.87 / 0.88，hybrid(hashing) 0.85 / 0.91 / 0.91 / 0.90 / 0.89）· `REPLAY DIFF EMPTY`（61 recorded / 46 replayable / 15 skipped）· 全新库 `alembic upgrade head`（**11** 条迁移 → head `c3b8e5a71d29`，**23** 张业务表）+ `alembic check` No new upgrade operations detected · `npm run build` ✓ built in 190ms · OpenAPI **41 个路径 / 44 个操作**。说明书 docx 已按改完的 `PROJECT_SPEC.md` 重生成并校验（`U+FFFD` 计数 0、67 张表、3306 段、纯文本 138,197 字、T10 关键串全部命中；备份 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10c` 与 `-t10d`）。**本轮仍未复跑的项**与上一段相同：PostgreSQL 全量（141 条新用例）。
>
>
> **2026-10-24（收尾核对）第三轮复跑**：把 §1.7 里两条与代码不符的现状改掉之后（见 `docs/PROJECT_SPEC.md` §19.2 新增的两行漂移记录）又复跑了一遍——代码一行未动，读数与上一段**逐字相同**（`ruff` 干净 · `mypy` **134 文件**零错误 · `pytest` **447 passed / 3 skipped**（收集 450，产物 `.tmp/final_t10c.xml`）· Golden 40/40 · 支持 61/61 · 检索 `GATE PASSED` · `REPLAY DIFF EMPTY` · `alembic upgrade head` 11 条迁移到 `c3b8e5a71d29` + `alembic check` 无漂移 · `npm run build` ✓ · OpenAPI 41 路径 / 44 操作）。改的只有三处文档事实：① 「PostgreSQL 缺口 = 116 条」这个逐轮台账**算错了**（漏 T2 的 14 条与 T3 的 10 条，也没算进 T4b/T7 给 `test_channels.py` 加的 1 条），改成**可直接核对**的「T2 起新建的 9 个测试文件当前共 **141 条**」；② §1.7 那句「规则没有增删改接口、控制台没有规则编辑页」在 T10 落地后是错的，改写成写面的真实边界；③ 三处状态标记（T5 段「投递出口与个性化未闭环」、T7 段「G10 有意留下」、T9 段「代码进行中」）与已完成的事实矛盾，各自带一句收口说明。

> **2026-10-25（本轮：五通道两库 + 重排延迟预算 + 说明书版面实看）收尾门禁复跑读数**（取代上面各段的旧读数；代码只改了一处检索排序）：
> `ruff check app tests scripts evals alembic` All checks passed · `mypy app` Success: no issues found in **137** source files ·
> `pytest`（SQLite 基线）**496 passed / 6 skipped**（收集 502、43 个测试文件，118.54 s）·
> **PostgreSQL 16.15 + Redis 两个容器同时指上：502 passed / 0 skipped（119.71 s）** · `evals/run.py` **40/40** ·
> `evals/support_run.py` **61/61** · `evals/retrieval_metrics.py --channels bm25,hybrid` GATE PASSED（bm25 读数与 T8 逐字相同）·
> `evals/replay.py --self-check` → **REPLAY DIFF EMPTY** ·
> 全新库 `alembic upgrade head`（**13** 条迁移 → head `b4e8d2f6a1c9`，**24** 张业务表）+ `alembic check` No new upgrade operations detected ·
> `npm run build` ✓ · OpenAPI **42 个路径 / 45 个操作**。
> 另加三项本轮专项：五通道指标在 `tp_eval` 库上跑出且**与 SQLite 逐位一致**；重排延迟预算判定 **OVER BUDGET**（p95 2602 ms vs 300 ms）；说明书 docx 做了版面目视检查。
> **本轮仍未复跑的项**：生产托管 embedding / rerank 的接入与压测；服务商侧投递；浏览器截图（应用内浏览器没有可见视口）。


---

## 6. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| 前端五个页面工作量被低估 | 中 | 延期 | 先做 T1-1、T1-2 两页，T1-3 可后置 |
| 护栏误拦正常咨询 | 中 | 体验下降 | 先只拦「明确的」注入与承诺句式；灰度 + 命中率看板 |
| 渠道抽象动到入口引发回归 | 中 | 高 | T4-2 先做「零行为变化」的 Web 适配，测试不改一行——已验证：101 条评测结论不变。**T8 真的动了入口**（旧路由改走网关 + 前端换路由），复跑仍是 40/40 与 61/61、`REPLAY DIFF EMPTY`，且响应体由 `test_web_channel_matches_existing_support_route` 逐字守住 |
| 幂等键策略放前端，用户改写一个字就变成新投递 | 中 | 低（多跑一轮，不会算错账） | 前端只在「同一句话 + 同一段会话」复用键，改写即换新键是刻意的：文本变了就是新请求，服务端也会以 `CHANNEL_MESSAGE_ID_REUSED` 拒绝同键换内容的重试 |
| 用例扩量写成「测实现」而非「测行为」 | 中 | 门禁失效 | 断言只写外部可观测：终态、工具名、参数、回执字段 |
| 回放读 trace 表数据量膨胀 | 低 | 存储 | 分层抽样 + TTL |
| **`AUTO_CREATE_TABLES` 与 Alembic 版本戳漂移** | **高（已发生两次）** | 本地库看着能用，`alembic upgrade head` 报「表已存在」，新人按文档重建即失败 | 演示库统一用 `AUTO_CREATE_TABLES=false` + `alembic upgrade head` + seed；`alembic check` 纳入 CI；本轮已把本地库重建一遍并把过程写进下方「环境修复记录」 |
| ~~主动通知「确认了但发不出去」被当成 bug~~ → 新风险：**「已推送」被读成服务商送达回执** | 中 | 演示翻车、信任受损 | T7-4 已让 `SENT` 可达，但它是"交给配置的传输并写成功"。README / API / SECURITY 三处都写明这一口径，控制台「已推送」旁边直接挂脚注；演示时把落盘文件打开给人看，而不是只指徽标 |
| 出站落盘文件里的地址与正文被当成日志随手提交 | 中 | PII 泄露 | `local_file` 只允许非 prod（启动校验拒绝），路径默认注释掉、要求显式配置；审计只存 `address_digest`；`.tmp/` 已在 `.gitignore` 里 |
| 混合检索的 recall@5 退化被读成「hybrid 更好」 | 中 | 决策错误 | 评测表按每个 k 单独列，不合成单一分数；门槛只套在 `bm25`（`gate_channel`），CI 文案已写明 |
| `emit()` 的抑制先于入参校验，对接方误判「校验没生效」 | 中 | 集成期反复试探 | 单测锁住顺序（`test_cooldown_blocks_repeat_after_cancel`）；文档给出完整门序 |
| 限流与幂等状态在进程内 | 中 | 多 worker 放大预算、webhook 重试落到别的进程 | 已在 T2 偏差清单记录；跨进程配额需要 Redis，属独立一期 |
| **客户档案里的自由文本借「个性化」进回复**（T9 新开的口子） | 低（设计已封） | 提示注入的第二条通路：第一条是客户当场说的话，第二条是客户**存下来**的话 | 覆盖层只套服务端模板，四种原因码之外的字段一律不读；兴趣先经 `CatalogService.resolve()` 换成目录规范名，认不出就当没有；`history_summary` / `contact_window` 有专门用例断言其内容不出现在回复里；`_personalize` 排在 `_guard_output` **之前**，护栏永远是最后一个碰文本的环节；`MEMORY_PERSONALIZATION_ENABLED` 一键回到不个性化 |
| **「档案一改历史 trace 不再逐字可回放」被当成回归**（T9） | 中 | 排查浪费、对回放失去信任 | `docs/EVALS.md` 写了 diff 的确切形状（称呼前缀 + `generate/memory.personalize` 造成的 span 位移，实测 38 行 / 9 段会话），并写明复核真实客户会话前要**先在同一份库里重建档案**；`--self-check` 用种子库（无人写过档案），断言仍然逐字成立 |
| **规则文案改了没人复核**（T10 新开的口子） | 中 | 一条错文案直接进所有客户的通知 | 审批那层有四眼（ADR-004），**规则文案这层没有**——这是当前事实，ADR-019 后果段与界面顶部注释都写明了；写时的九条校验只挡"格式坏"与"字段不存在"，不挡"话说错了"；改错了没有一键回滚（审计只有 sha256），只能手打回去。做复核需要先定义"文案改动谁批、批了怎么记"，是独立一期 |
| **规则改动被当成"改了产品行为"**（T10） | 中 | 沟通成本、误判影响面 | 改动只落在通知正文，**不动生成路径**：`evals/` 的 101 条评测与 `REPLAY DIFF EMPTY` 三条读数与 T9 逐字相同；界面提示写明"从下一条通知起生效，已经生成的通知不受影响"；已落库的通知 `body` 不会被回溯改写（有用例断言） |

---

## 7. 配套文档同步

| 文档 | 动作 |
|---|---|
| `docs/AGENT_PLATFORM_PLAN.md` | ✅ 已标注 T1 与 Phase 2 的控制台状态；T2 完成后 §4.6 与阶段表同步为「已落地」并写明进程内限流边界（2026-10-24）；T3 完成后同步评测闭环状态；T4/T5/T6 完成后更新规模事实行（132 个 Python 文件 / 30 个测试文件 / 385 收集 / 23 张表 / 40 路径 43 操作 / 15 篇 ADR 缺 ADR-012 / 7 页），渠道与记忆两层的状态列同步为「已落地（含边界）」；**T7 后再修正为 133 / 31 / 405 收集 / 402 通过 / 9 页**（规模事实行、CI 门禁读数行），§1.1 交付表补第 29、30 行（出站投递 + 新页面），D8 / 八层矩阵第 4 层 / 模块边界表 / §5.3 Outbox 两段 / Phase 1 与 Phase 5 / 分层落地表的执行、渠道、记忆三行——凡是「没有 sink、`SENT` 不回写」的说法一律改写为「两种事件都有消费者，`SENT` 止于出站通道边界」，风险 B10 的指针修正为「控制台迁到渠道入口**不在 T7 清单里**，仍是待办」；**T8 后**：B10 行改为「已收口」，规模事实行与 CI 读数行改到 408 / 411 / 77 条待 PG 复跑，§2.2「每一步的不做」第 1 行、§1.1 交付表新增第 31 行、Phase 3 状态行与渠道层的「演示只能用新入口」免责一起改写为「两个入口共用同一道闸；不带幂等键的调用仍逐次执行」；**T9 后**：文档版本行升到 v2.2 并写明「按 T9 实测结果回填」，§1.1 交付表新增第 32 行（客户记忆进答案，✅），规模事实行改到 426 passed / 收集 429 / 32 个测试文件 / mypy 134，§1.2 八层矩阵与 §2.2 模块边界表的知识·认知两行改为「答案路径按列只读档案、只四种模板效果；注入提示词那套依然没做」，表清单 `customer_profiles` 行补上 T9 的只读口径，记忆铁律第 ③⑤ 条补进程级开关与「读出来也不许当话讲」，分层落地表与 §14 测试分层行（`test_personalization.py` 18 条，其中 2 条纯函数单测）与 CI 门禁第 3 行同步。**T10 后**：版本行升到 **v2.3** 并写明「按 T10 实测结果回填状态」；§1.1 交付表新增第 **33** 行（主动规则的可编辑面，✅），第 27 行（`app/api/v1/memory.py` 9 → **10** 个操作）、第 30 行（控制台 9 → **10** 个业务页面）读数更新；规模事实行改到 447 passed / 收集 450 / 33 个测试文件 / mypy 134 / **18 篇 ADR**（缺 ADR-012）/ 41 路径 44 操作 / 10 个控制台页面；§2.3 的 D4 行补「T10 规则写面与写时校验，ADR-019」；§2.4 模块映射的知识行补规则写接口；§3.8 表清单 `customer_profiles` 行补「规则表自 T10 起另开坐席写路径」；§4.3 的接口契约与记忆铁律第 ④⑥ 条补规则写面与「规则文案没有双人复核」；§6.1 的 Phase 4 / Phase 5 状态列、§8.3 缓解措施表的记忆行（待建列换成「规则的**删除**、版本表 / 一键回滚、**双人复核**、按租户灰度」）、§9.2 接口清单的记忆组（10 个操作 + `PUT /memory/rules/{event_type}`）同步；§9.4 ADR 索引新增 **ADR-019** 行、编号说明补「ADR-019（规则写面）随 T10 落地」、文件计数 17 → **18** |
| `README.md` → 已知边界 | ✅ 已补控制台页面清单、Trace 演示数据为扁平 span、统计台账不存结果行；T2 上线后删除「无速率限制、请求体上限」，改为记录进程内桶与 multipart 口径；T3 上线后补评测徽章与三条评测命令、回放边界。**2026-10-24 收尾已完成**：渠道清单改为「8 个枚举只有 WEB/EMAIL/WECHAT/WECOM 四个能入站，其余 404」、主动通知无出口 handler（实测 `claimed=1 / sent=0 / failed=1`，`SENT` 不可达）、记忆不影响答案生成、稠密是 JSON 列全表扫描；同时修正三条**错的事实**——hybrid 那行指标按实测改为 0.853/0.912/0.912/0.902/0.889、记忆字段改成真实白名单（原来写了不存在的「设备与上下文备注」）、抽样渠道说明改为 `handoff_events.channel` 确实存在（原来写「表里没有 channel 列」）。**T7 再修一遍**：「没有出口 handler / `SENT` 不可达」整段作废，改为「两种事件都有消费者；`SENT` 可达但只表示交给配置的传输，默认 `none` 什么都不发」，并新增出站段（`render_notice` 复用能力表、地址从服务端映射反查、`proactive.delivered` 只存 `address_digest`）；测试段读数改为 402 passed / 收集 405 / 31 个测试文件 / mypy 133；环境变量表补 `CHANNEL_OUTBOUND_TRANSPORT` 与 `CHANNEL_OUTBOUND_DROP_FILE`；「Outbox 有两种事件、两个 sink」替代旧表述，控制台页面清单升到 9 个。**T8 后**：删掉「**旧入口不去重**……控制台前端目前用的正是旧入口」这句已作废的边界，改为「两个 web 入口共用同一道幂等闸，带 `X-Channel-Message-Id` 就记录；不带键的调用仍逐次执行」，读数改到 408 passed / 收集 411。**T9 后**：「记忆不影响答案生成」那条已知边界整条作废，换成「档案确实会改答子，但只有四种模板效果、自由文本不进回复、`MEMORY_PERSONALIZATION_ENABLED` 一键关」；能力清单加一行「记忆进答案只有四种模板效果」；新增一节「记忆怎么进答案：只有四种模板效果」，里面的两组响应（写档案 / 没写档案同一句问话）与四效果表**来自真实抓取**（`.tmp/t9_readme_demo.py` 跑出来的 JSON，不是手写示例）；示例响应补 `personalization` 字段并说明 `null` 的三种成因；环境变量表补 `MEMORY_PERSONALIZATION_ENABLED`；目录结构 `agent/` 行加「记忆个性化覆盖层（只套模板）」；已知边界改口为「也会进答案，但只走四种效果，没有跨会话摘要层、没有由模型自行决定的记忆用法」；测试读数改到 426 passed / 收集 429 / 32 个测试文件 / mypy 134。**T10 后**：能力清单的主动服务那条补「规则文案、冷却与启停可以由坐席在控制台改（写时校验模板字段，不合法当场拒），但事件类型由代码定、改动没有双人复核、已发出的通知不回溯」；控制台页面清单 9 → **10** 个（加「通知规则」）；测试读数改到 **447 passed / 收集 450 / 33 个测试文件**；迁移数写回 **11** 条（head `c3b8e5a71d29`）；`web/` 目录说明补「记忆、渠道、通知规则」。**全树口径复核那轮**：「API 概览」首句的计数从「共 43 个操作（40 条路径，含 2 个健康检查）」改成 **44 个操作 / 41 条路径**（按 `create_app().openapi()` 逐条复核；那张表 44 行与 OpenAPI 一一对应） |
| `docs/API.md` | ✅ 已补 `GET /api/v1/traces/metrics`、handoff metrics 的 `window_minutes`，以及 T2 的 `GET /api/v1/security/metrics`、`GET /api/v1/security/events`。**2026-10-24 收尾已完成**：`/api/v1/channels` 三个操作与 401/403/404/409/503 语义、`X-Channel-Message-Id` 与 `X-Channel-Deduplicated`、身份映射 `tenant:user`（未登记 403 与映射串写错 503 **拆成两行**）；`/api/v1/memory` 9 个操作（偏好白名单与 `tone` 枚举、`extra="forbid"`、完整门序与抑制原因枚举，并写出「校验排在闸门之后」的反直觉现象）；`GET /api/v1/outbox` 信封与**两种事件类型**的投递语义（原来还写「当前唯一事件类型是退款」）。**T7-4 后**：新增整节「主动通知的出站投递」——去向的三个来源（队列行 → 档案偏好 → 服务端映射反查）、两个配置项、11 个拒绝原因码表、真跑读数（`{"claimed":1,"sent":0,"failed":1}` → `[NO_CHANNEL]` → 选 `EMAIL` → `{"claimed":1,"sent":1}` 落 `SENT`）与「失败后立刻再点投递会得到 `claimed: 0`」这个坑；`preferences` 白名单补 `contact_channel`（8 个渠道名之一、**填地址是 422**、只有 `EMAIL`/`WECHAT`/`WECOM` 有地址来源、置 `null` 是删键且不回落到默认渠道）；`GET /api/v1/outbox` 的 `status` 参数补 `CANCELLED`；规则内容的落点从 `scripts/seed.py` 改成 `app/seed.py`。**T8 后**：`POST /api/v1/support/messages` 一节补上可选 `X-Channel-Message-Id` 与 `X-Channel-Deduplicated`，`/api/v1/channels/web/messages` 一节删除「幂等只在这条路由上生效 / 控制台仍调旧入口」整段，改为「两条路由是同一道闸的两个门面」，并写明 `CHANNEL_MESSAGE_IN_FLIGHT` 与认领超时接管现在各有用例。**T9 后**：`POST /api/v1/support/messages` 的示例响应补 `"personalization": null`、响应字段表补一行（三种 `null` 成因），新增小节「`personalization`：档案改了哪几处（四个原因码）」——含一段**真实抓取**的开档案响应、同一句问话无档案时的对照（回复开头、卡片顺序、追问条数）、四条纪律（只报码与字段名 / 兴趣须命中目录 / 护栏在最后 / 静默轮恒为 `null`）与对应 span 的属性清单；`/api/v1/channels/web/messages` 一节写明两条入口返回同一份形状；`/api/v1/memory` 一节把「`GET` 会写一条 `memory.read` 审计」限定到**客户自己读档案**，并注明问答轮次走 `personalization.py` 的只读列查询、不写审计。**T10 后**：新增整节「`PUT /api/v1/memory/rules/{event_type}`（坐席）」——请求体字段表（`template` ≤400 字 / `enabled` / `cooldown_minutes` 0–1440）、**九种 422 形态的错误码表**（`RULE_TEMPLATE_UNKNOWN_KEY` 带 `details.allowed`、`RULE_TEMPLATE_INVALID`、`VALIDATION_ERROR`、`UNKNOWN_PROACTIVE_EVENT`、`extra_forbidden`）、409 `RULE_ALREADY_EXISTS`、审计只存 `template_sha256`、改动不回溯、没有删除接口；`GET /api/v1/memory/rules` 一节补 `required_context` 与 `updated_at` 两字段；角色清单补这一条 PUT |
| `docs/PROJECT_SPEC.md` / `docs/SCOPE.md` | ✅ **2026-10-24 收尾已完成**：未验证清单补入「真实服务商签名与真实 embedding 端点未验证」「Postgres job 不跑 pytest」「hybrid 两列只在本机 SQLite 测过」；口径风险补入「幂等只在渠道路由」「抑制先于校验」「hybrid 的 `recall@5` 退化 0.9706→0.9118」「附件专用文案分支在网关里走不到」；验收口径已按 382/385 与 mypy 132 文件重写；`SCOPE.md` 的「做 / 不做」两侧同步渠道、记忆、主动服务与稠密检索的真实边界；附录 C 手测清单区分「本轮实跑并留痕」与「本轮未执行」。**T7 后**：`PROJECT_SPEC.md` 规模基准与 §14/§19.1 全部改到 402 / 405 / 31 个测试文件 / mypy 133，§19.2 漂移表补三行（T7 规模数字、「没有 sink」五处、规则内容落点），§19.3 的 P1 项从「补出站 sink」改成「只剩服务商侧」，附录 C 增加主动通知出站的手工走查项并记 T7 实跑；未覆盖清单里那条「抽样渠道不可信、`context` 没有渠道字段」**已经过时**（T7-5 落地，`_channel()` 会读 `context.channel`），改为「回落只发生在历史数据上」；`SCOPE.md` 的控制台页面数改 9、"不做"侧把「主动通知不投递」换成「服务商协议与送达回执」，规则内容落点同步为 `app/seed.py`。**T8 后**：`PROJECT_SPEC.md` 规模基准改到 408 / 411、`test_channels.py` 的条目行从 13 改为 20（含 T8 的 6 条）、§19.2 漂移表补一行「全文仍写 402/405 与『控制台调旧入口』」、§19.2 那句「控制台前端仍调旧入口，界面上看不到去重」删除、附录 C 增加「浏览器丢响应 → 原话重发」这条实跑项、附录 D 的渠道行加 `app/api/v1/support.py`；`SCOPE.md` 的「已覆盖」侧补入口收口、Postgres 未复跑清单从 71 改 77。**T9 后**：`PROJECT_SPEC.md` 改动面最大——头部规模块改到 426 / 429 / 32 / mypy 134 并写明「T1–T9 新增用例尚未在 PostgreSQL 上复跑」，§1.7 记忆条目重写，**新增 §6.7.4「记忆个性化覆盖层（T9）」**（位置与开关、为什么按列只读而不复用 `MemoryService`、四原因码表、三条收敛纪律、代价段直接引实测的 38 行 diff），§12.9 的偏好白名单从三键改四键并新增「记忆进答案的部分是受控的」，§13.5 前端行为与 §14.1 测试表加 `test_personalization.py`（18 条）行、§14.3 改 mypy 134，§19.1 已验证清单补 T9 在线真跑、未覆盖清单按轮次枚举而不给总额，口径风险表「客户记忆」行重写并新增「`personalization.effects`」行，§19.2 漂移表新增一轮 6 行（规模数字、「记忆只被存与查」、§12.9 三键→四键、EVALS 回放、ADR-018 初稿的两条伪造链接与一处误引、README/API 示例改为真实抓取），附录 C 加 T9 收尾项、附录 D 加「记忆个性化」同步面；`SCOPE.md` 验收口径改到 426 / 429 / 32 / 134，并新增「记忆进答案（T9）18 条」与「T9 在线真跑（8131 开 / 8132 关、59 项 HTTP 断言、浏览器 DOM）」两条。**T10 后**：`PROJECT_SPEC.md` 头部事实基准块改到 **447 / 450 / 33** 与迁移 **11** 条并写明「T1–T10 新增用例尚未在 PostgreSQL 上复跑」；§12.1 路由表新增 **PUT** 行、表头写「44 个操作 / 41 条路径」；**新增 §12.9「规则的写面（T10，2026-10-24）」**（流程图 + 可写三字段 / `requires_confirmation` 被拒 / 事件类型代码所有 / 写时校验两种静默失败 / PUT 是 upsert / 不回溯 / 审计只存摘要）；§13.1 控制台页面表扩到 10 行；§14.1 新增 `test_proactive_rules.py` 21 条行；§1.7 已验证清单加 T10 一条；§19.1 加 T10 真跑读数（52 项 HTTP 断言 / 九种 422 形态 / 失败后规则行逐字不变 / 9 条审计只带 sha256 / 浏览器 DOM 走查）；§19.2 新增 **6 行** T10 漂移表；附录 B 加两行文件索引、附录 C 加 447 读数与规则写面手测项、附录 D 加「修改主动规则的**写面**」同步面行。`SCOPE.md` 控制台页面数改 10、「不做」侧删掉已作废的「记忆没有进答案生成路径」与「规则没有增删改接口」，换成 T10 的真实边界（事件类型固定 / 无删除 / 无版本表 / 无双人复核 / 无灰度），并加 T10 的 21 条用例与 52 项真跑两条验收行。**去掉跨项目对比表述那轮**：删掉 §1.8 整节（跨项目对照表 + 学习建议）、§0.3 章节地图第 1 行改为「项目概览与最终状态」、§2.3 开头改为直述「TicketPilot 没有单独的 domain 层」；§19.2 新增一张本轮漂移表（**表数净不变**：删一张对照表又补一张漂移表）；附录 C 的 docx 校验块不再写表数——表数与字节数是会随结构无声变化的派生产物读数，写进 docx 的来源文件就会形成自我引用，已全部挪到 §7。**PostgreSQL 口径修正那轮**：`PROJECT_SPEC.md` §1.7 与附录 C 里「本轮收尾也没有可用的 Docker/PostgreSQL 环境」改成「本轮收尾没有再起 PostgreSQL 容器（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的）」；§19.2 新增一张「PostgreSQL 口径修正」漂移表（**2 行**），记下「把没做 PG 复跑写成环境不具备」的错误措辞与改法。`SCOPE.md` 的验收口径没有出现该措辞，**未改**。**全树口径复核那轮**：§19.2 新增一张「全树口径复核」漂移表（**1 行**），记下 `README.md` 那句没跟上的 OpenAPI 计数与改法 |
| `docs/SECURITY.md` | ✅ **T7-4 后重写**：原「主动通知只到确认入账为止：没有出站 sink」一条替换为三条——地址只来自服务端配置映射（四种拒绝码、不回落到默认渠道）、`SENT` **不是送达事实**且 `proactive.delivered` 只存 `address_digest`（但 `local_file` 的落盘文件**含明文地址与正文**，属 PII，必须留在 `.tmp/` 且已被 `.gitignore` 覆盖）、只有 `none` / `local_file` 两种传输且 `prod` 拒绝后者。**T8 后**：把「幂等只在渠道路由与 webhook 上成立：旧入口……仍会跑第二轮 Agent」整条改写为「网页两个入口共用同一道闸；**没有幂等键的调用不去重**，所以滥用面取决于调用方是否带键」。**T9 后**：信任边界表新增一行「客户档案里的自由文本借『个性化』进回复」——这条把档案明确列为**第二个注入面**（第一个是客户当场说的话），对策是只套服务端模板、兴趣必须先经 `CatalogService.resolve()` 换成目录规范名、护栏仍在个性化之后；正文补一段说明答案路径的档案读取是**只读列查询、不写 `audit_events`、不建档案行**，并把「客户自己 `GET` 档案会留 `memory.read`」与它区分开，同时记录 ADR-018 那条代价（有档案的客户历史 trace 不再逐字可回放）。**T10 后**：新增一节「规则文案的信任边界」——模板白名单与写时校验、`requires_confirmation` 不可写、事件类型不可配、审计只存 `template_sha256`、错误只回字段名、改动不回溯、没有删除；已知缺口新增一条「**规则文案没有双人复核**」；并顺手修掉一条**既有错误**：原文写「`proactive.confirmed` 没有注册的 sink」，实际 `app/services/runtime.py:732` 就是 `ProactiveSender(session, self.settings).deliver(row)`，改为「worker 目前有两个消费者：`refund.pending_confirmation` 走对账，`proactive.confirmed` 走 `ProactiveSender`（T7-4 接线，ADR-017）」 |
| `docs/ARCHITECTURE.md` | ✅ **T7-4 后补**：分层职责清单加 `channels/` 与 `services/memory.py` + `proactive.py` 两行；「事务性 Outbox」的图与要点补上第二条分派（`proactive.confirmed → ProactiveSender`）和「`SENT` 止于传输边界」；新增「渠道入站与主动出站」一节（能力矩阵驱动渲染、三道入站闸与四种错误码、去向三段与拒绝码清单）；顺手修掉一处三级状态混读（`timeout_after` 写成「任务 `PENDING_CONFIRMATION`」，实为台账 `UNKNOWN` + 任务 `SUCCEEDED` + 业务结论 `PENDING_CONFIRMATION`）。**T8 后**：「渠道入站与主动出站」一节与第 318 行那条「幂等只在渠道路由上生效，控制台的 `/support/messages` 是透明旧入口」改为「两条 web 路由都经 `ChannelGateway.accept_web`，幂等闸只有一道」；「只读客服路径」的 mermaid 图在入口与 Agent 之间插入 `ChannelGateway.accept_web` 节点，并把入口节点标成两条路由。**T9 后**：分层职责清单加 `agent/personalization.py` 一行（纯函数、只套模板或换形状、四种可枚举原因码、不复用 `MemoryService`）；「只读客服路径」把 `SupportAgent.handle` 内部固定写成三段——**决策塑形 → 记忆个性化 → 输出护栏**，并说明人工接管静默轮不读档案、这条读路径不写审计也不新增档案行，因此「只读路径不写审计」在客户已有档案时依然成立，代价是回复开始取决于档案。**T10 后**：分层职责清单里 `services/memory.py` + `services/proactive.py` 一行补「租户规则的读写与写时模板校验」；**新增「主动规则的可编辑面」一节**（事件类型代码所有 / 写路径 / 写时校验的两种静默失败 / `required_context` + `updated_at` / 诚实边界：无双人复核、无版本表与一键回滚、无按租户灰度、无删除、不回溯）。**去掉跨项目对比表述那轮**：检索节末那条「来自外部项目的 RAG 能力移植」改成自述实现位置（`app/retrieval/dense.py`、`fusion.py`、`rerank.py`、`hybrid.py`），并保留「可选、失败可退化、没有把外部服务变成硬依赖」这三条事实 |
| `CONTRIBUTING.md` | ✅ 门禁块按 `.github/workflows/ci.yml` 重写：ruff 范围为五个目录、mypy 133、pytest 402/3、三条评测门禁、回放自检、`alembic check`、`npm run build` 共九条一一对应，并写明 **Postgres job 只验迁移链、不跑 pytest**；补「上一轮基线 323 passed」避免读者以为 PG 侧已复跑新用例；**T8 后**读数改到 pytest 408/3、并写明「改 `app/api/v1/support.py` 或 `web/src/api.ts` 的入口时必须同时看 `tests/test_channels.py` 的跨入口用例」；**T9 后**读数改到 pytest 426 passed / 收集 429、mypy 134 个文件，并在「截图与文档」加一段改生成路径的同步面：动个性化效果 / 护栏 / 模板措辞时，除 `docs/API.md` 示例外还要同步 README 对照段、`PROJECT_SPEC` §6.7 与前端 `ConversationPage.tsx` 的原因码标签表，同时回 `docs/EVALS.md` 确认回放口径（新增一种会改回复的效果＝让已有档案客户的历史 trace 不再逐字可回放）。**T10 后**：读数改到 **pytest 447 passed / 收集 450 / 33 个测试文件**、mypy 134 个文件；新增「改动**会打开或改变主动规则的写面**」时的 8 条同步清单（API 契约 / ARCHITECTURE 的可编辑面一节 / SECURITY 的信任边界 / PROJECT_SPEC §12.9 与 §19 / SCOPE 与 EVALS 的诚实边界 / README 与 AGENT_PLATFORM_PLAN / 前端三个文件 / 「无双人复核、无版本表、无灰度」这三条不得写成已具备）；ADR 一节补编号规则（当前 **18 篇**，**ADR-012 是预留号位、不回收、不重排**，新 ADR 接在最大号之后并同步 `AGENT_PLATFORM_PLAN` §9.4）；目录约定补齐 `app/` 下实际存在的 `schemas/` / `channels/` / `core/` / `models/` / `db/` / `observability/` 与 `evals/`。**PostgreSQL 口径修正那轮**：`CONTRIBUTING.md` 里那句「本机没有 Docker daemon，只能在 SQLite 上跑」改成「本机装了 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的（基线 `323 passed`），最近一轮收尾时守护进程没有运行、容器没有再起——缺的是这一轮的执行而不是环境」；「141 条没在 PG 复跑」这个事实不变 |
| `docs/adr/` | ✅ ADR-014（客户记忆与主动确认）随 T5 落地；ADR-016（渠道抽象）随 T4 落地；ADR-012（策略外置与阈值灰度）仍未写、编号不回收；T2 护栏口径写在 §4.6 与本文件偏差清单，T3 回放/抽样边界写在 `docs/EVALS.md`；**T7-4 新增 ADR-017（主动通知的出站接缝）**——记录「出站复用能力表、去向三段拼、传输协议默认拒绝、`SENT` 只到通道边界」四条决策与四个被否决方案，并回头修订 ADR-013（「唯一投递的消息类型」）与 ADR-014（「`SENT` 尚未回写」「没有真实出站渠道」）里已过期的后果条目，ADR-016 的「不发送」一条补了指针。**T8 追加一条带日期的修订（不新开编号，仍是 16 篇）**：ADR-016 顶部加「修订：2026-10-24（T8）」、决策 4 末尾那句「不带就是原来的透明转发，所以控制台行为没有变化」就地标注已被修订，文末新增「## 修订（2026-10-24，T8）」——写清入口分裂的成因、现在两行行为表（带键 / 不带键）、**没变的部分**（响应契约、身份来源、渲染、幂等需要显式键）与「为什么保留旧路由而不是废弃」；ADR-009 / ADR-013 无需改动。**T9 新增 ADR-018（客户记忆进答案的受控覆盖层）**——记录「只读列查询而不复用 `MemoryService`、四种模板效果、兴趣必须命中目录、个性化排在护栏之前、进程级开关」五条决策与四个被否决方案（把档案拼进提示词、按历史自动预填槽位、每轮写 `memory.read` 审计、按租户灰度），并回头修订 ADR-014 后果段里「记忆目前只被存与查，没有注入 Agent 回复」那一句（就地标注已被 T9 收口，同时保留当初不做注入的理由）。ADR 总数 16 → **17 篇**（仍缺 ADR-012，编号不回收）。**这一篇的初稿犯过两个错、已改**：引用了两条不存在的 ADR 编号、把一条结论误挂到 ADR-002 上，复核时逐条对着 `docs/adr/` 目录与原文纠正，过程记在 `docs/PROJECT_SPEC.md` §19.2 的漂移表里。**T10 新增 ADR-019（主动规则的可编辑面）**——记录「事件类型代码所有、`requires_confirmation` 不做成开关、模板字段写时校验、PUT 是 upsert、审计存 `template_sha256`、改动不回溯且没有删除」六条决策与五个被否决方案（界面新增事件类型 / `requires_confirmation` 可写 / 只在 `emit()` 时校验 / 存规则版本表 / 删规则接口）；并回头修订 ADR-014 后果段里「规则文案只能改 `seed.py` 重灌」那条过期后果。ADR 总数 17 → **18 篇**（仍缺 ADR-012，编号不回收）。**PostgreSQL 口径修正那轮**：ADR-019 后果段那句「本会话没有可用的 Docker 守护进程」改成「本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出 `323 passed` 基线的；这一轮收尾时守护进程没有运行，没有再起容器——缺的是执行，不是环境」；ADR-006 的「本机未实际启动 PostgreSQL（Docker daemon 不可用）」是 2026-10-23 当天的读数，**原文保留**，就地加一段**带日期的修订**（此后本机用 Docker 起过 PostgreSQL 16.15 一次性容器，320 用例版本跑通 `323 passed`），ADR 总数不变，仍是 18 篇 |
| `docs/EVALS.md` | ✅ 已补「Trace 回放口径」「分层抽样」「轨迹断言」三节，并把用例数从 23/33 更新为 61/101；T6 后补多通道对比表（含 recall@5 退化）、门槛只套第一个渠道的口径、`--offline` 与 `--rebuild` 流程、真跑的回放与抽样数字、以及「渠道与记忆不进评测集」的诚实边界；**T7 后**把 T1 验收段的读数补到 402 / 405 / 31，并说明出站投递由 17 条 `pytest` 用例 + 真实 HTTP 与浏览器走查守，**仍不进 101 条评测集**（评测集测的是认知路径的终态与轨迹，出站是传输面事实）。**T8 后**：T1 验收段的读数追加 T8（408 / 411），渠道用例计数从 13 改 20，并写明「入口收口由 6 条 `pytest` + 真实 HTTP/浏览器走查守，同样不进评测集——它测的是传输面而不是认知面」。**T9 后**：T1 验收段读数追加到 **426 passed / 收集 429 / 32 个测试文件**，并写明个性化由 18 条 `pytest` + 59 项真实 HTTP 断言 + 浏览器 DOM 走查守，**同样不进 101 条评测集**（种子库不建档案，`evals/` 里零 `memory` 引用是核对过的）；「Trace 回放口径」一节**新增一段实测的代价**：`--database-url` 指向带档案的库时 `12 recorded / 12 replayable / 0 skipped` → `38 diff lines`，9 段会话出 diff 的确切形状（8 条 `conclusion.reply_sha256` + 20 条 span 位移）、3 段无档案会话仍逐字干净，以及「复核真实客户会话前先在同一份库里重建档案」这条操作口径。**T10 后**：T1 验收段读数追加 **447 passed / 收集 450 / 33 个测试文件**；「诚实边界」那条改写成**三个不同原因**——T4 的渠道差异在渲染层、T9 让答案真的依赖档案所以需要快照、T10 是写路径按定义就不进只读评测集；并写明 `evals/` 目录里 `memory|proactive|rules` **零引用**（核对过），把未进集的东西说成测过是不诚实的。**PostgreSQL 口径修正那轮**：检索基线那段「本轮收尾没有可用的 Docker/PostgreSQL 环境」改成「本轮收尾没有再起 PostgreSQL 容器（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的）」——「当前这份基线没有两库一致性证据」这个结论不变 |
| `docs/TicketPilot项目说明书.docx` | ✅ 2026-10-24 由 `docs/PROJECT_SPEC.md` 重生成：先备份到 `.tmp/TicketPilot项目说明书.docx.bak-20261024`，再用 `pandoc docs/PROJECT_SPEC.md -o "docs/TicketPilot项目说明书.docx"`（**不带 `--toc`**，正文自己带导航），产出 63 张表；校验方式是从 `word/document.xml` 取纯文本，断言无 `U+FFFD`、无 GBK 型乱码片段，并抽查新增段落（`本轮实跑并留痕` / `MessageNotDelivered` / `四张新表` / `VERIFIED_SUCCESS`）全部命中。**T7-4 后再重生成一次**（`PROJECT_SPEC.md` 这轮改动很大：§19.2 三行、§19.3 第 3、7 项、§24.3、§24.4 整节改写、附录 D 三行）：先备份到 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t7`，同一命令重生成，产物 164,855 字节 / 3119 段 / **64 张表**（比上一版多一张，正是 §24.4 新增的主动通知投递语义表）；校验同样是读 `word/document.xml` 纯文本——`U+FFFD` 计数 0，非 ASCII 且非中日韩的字符只剩 `¥§·×–—""…←→↔≠≤` 与制表框线符号（那 5 个 `×` 是「8 商品 × 5 章节」「渠道×意图」里的乘号，不是乱码）；抽查 `两种消息类型，各有消费者` / `ADR-017`(3) / `proactive.confirmed`(11) / `address_digest`(3) / `OutboundTransport`(3) / `VERIFIED_SUCCESS`(12) 全部命中。注意 `把控制台迁到渠道入口` 在 docx 里查不到是**预期的**——那句话只在 `NEXT_STEPS.md`，没进 `PROJECT_SPEC.md`。**T9 后重生成两次**：18:49 那次（备份 `-t9` = 170,248 字节的 T8 版）产出 180,892 字节，但 `PROJECT_SPEC.md` 在其后又落了 3 处改动（§14.4 两条新盲区、附录 C 与 §19.1 的干净库复跑段），所以 18:54 用同一命令再生成一次，旧产物另存 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t9-1849`；**当前产物 181,493 字节 / 3242 段 / 66 张表 / 纯文本 129,074 字**，校验仍是读 `word/document.xml` 取纯文本：`U+FFFD` 计数 0，非 ASCII 非中日韩字符只剩 `¥§·×–—""…←→↔⇒≠≤≥` 与制表框线符号（3 个 `¥` 是金额、5 个 `×` 是乘号，都不是乱码）；抽查 `6.7.4`(8) / `记忆个性化覆盖层`(2) / `ADDRESSED`(2) / `CARDS_REORDERED`(4) / `MEMORY_PERSONALIZATION_ENABLED`(5) / `memory.personalize`(5) / `personalization.effects` / `load_profile` / `ADR-018`(6) / `test_personalization.py`(2) / `426 passed / 3 skipped`(6) / `收集 429`(5) / `38 diff lines`(3) / `另一个干净库`(2) / `59 checks, 0 failures`(3) 全部命中；残留的 `408 passed`(3) 与 `mypy 133`(3) 逐处核对过上下文，全部是 §19.2 漂移表的「原措辞」列与附录 C 里被下一行标注 `取代上一行的读数` 的 T8 读数块，不是当前事实。**T10 后重生成两次**：20:22 那次（备份 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10` = 181,493 字节的 T9 版）按 T10 改完的 `PROJECT_SPEC.md` 产出 189,130 字节；随后 §19.1 又落了一处「T10-6 文档收口后最终复跑」的读数块，20:25 同一命令再生成一次，旧产物另存 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10b`；**当前产物 190,080 字节 / 3296 段 / 67 张表 / 纯文本 136,894 字**，校验仍是读 `word/document.xml` 取纯文本：`U+FFFD` 计数 0，非 ASCII 非中日韩字符只剩 `¥§·×–—""…←→↔⇒≠≤≥✓` 与制表框线符号（3 个 `¥` 是金额、5 个 `×` 是乘号、1 个 `✓` 是附录 C 的清单勾选，都不是乱码）；抽查 `ADR-019`(5) / `PUT /api/v1/memory/rules/{event_type}`(6) / `RULE_TEMPLATE_UNKNOWN_KEY`(3) / `RULE_TEMPLATE_INVALID`(3) / `UNKNOWN_PROACTIVE_EVENT`(3) / `RULE_ALREADY_EXISTS`(2) / `template_sha256`(9) / `required_context`(4) / `proactive.rule.update`(3) / `test_proactive_rules.py`(2) / `通知规则`(3) / `没有双人复核`(4) / `447 passed / 3 skipped`(8) / `收集 450`(7) / `41 个路径`(3) / `44 个操作`(4) / `10 个控制台页面`(1) / `52 checks, 0 failures`(1) 全部命中；残留的 `426 passed`(4) / `408 passed`(4) / `mypy 133`(4) / `收集 429`(4) 逐处核对过上下文，全部在 §19.2 漂移表的「原措辞」列、附录 C 的历史读数块与「最终复跑」段的**显式否定句**里，不是当前事实。**T10 收尾核对后再生成两次**（备份 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10c` 与 `-t10d`，最终产物 **191,795 字节 / 3306 段 / 67 张表 / 纯文本 138,197 字**，`U+FFFD` 计数 0）：这一轮 `PROJECT_SPEC.md` 改的是 §1.7 的两条现状事实 + §19.2 新增 3 行漂移记录 + 附录 C 的校验记录；重生成后抽查 `141 条`(8) / `9 个测试文件`(5) / `141 条新用例`(2) / `447 passed`(8) / `收集 450`(7) / `41 个路径`(3) / `44 个操作`(4) / `ADR-019`(7) / `RULE_TEMPLATE_UNKNOWN_KEY`(3) / `RULE_TEMPLATE_INVALID`(3) / `UNKNOWN_PROACTIVE_EVENT`(3) / `RULE_ALREADY_EXISTS`(2) / `template_sha256`(11) / `required_context`(4) / `proactive.rule.update`(3) / `test_proactive_rules.py`(3) / `通知规则`(5) / `没有双人复核`(5) / `10 个控制台页面`(1) 全部命中；本轮修正的那两句旧措辞只出现在 §19.2 漂移表的「原措辞」列里，不是当前事实。**一个会再踩的坑**：docx 的校验读数写在 `PROJECT_SPEC.md` 里会形成**自我引用**——改了读数就要再重生成，重生成又改读数。这一轮的做法是：`PROJECT_SPEC.md` 只写**稳定事实**（`U+FFFD` 计数 0、表数、关键串名单）与「逐串命中次数记在 `NEXT_STEPS.md` §7」，把精确字节数 / 段数 / 字数放进 `NEXT_STEPS.md`——那一节不是 docx 的来源，改它不会让 docx 过期。**去掉跨项目对比表述那轮又重生成一次**：先备份到 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10e`（191,795 字节的上一版），同一命令重生成，**当前产物 192,081 字节 / 3298 段 / 67 张表 / 纯文本 140,366 字**（读法：解 `word/document.xml`，按 `<w:p>` 切段、段内 `<w:t>` 顺序拼接、段间用 `\n` 连接）；`U+FFFD` 计数 **0**，外部项目名（大小写两种写法）命中数均为 **0**；`ADR-019`(7) / `RULE_TEMPLATE_UNKNOWN_KEY`(3) / `RULE_TEMPLATE_INVALID`(3) / `UNKNOWN_PROACTIVE_EVENT`(3) / `RULE_ALREADY_EXISTS`(2) / `template_sha256`(11) / `required_context`(4) / `proactive.rule.update`(3) / `447 passed`(8) / `141 条`(8) / `9 个测试文件`(5) 全部命中。表数净不变的原因：删掉 §1.8 的对照表（−1）又补了 §19.2 本轮的漂移表（+1），所以这正好印证了**表数不该写在 `PROJECT_SPEC.md` 里**——它会被下一次结构改动无声改掉，这一轮已把表数与字节数一起挪到本节。**PostgreSQL 口径修正那轮再重生成一次**：备份到 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10f`（192,081 字节的上一版），同一命令重生成，**当前产物 192,899 字节 / 3308 段 / 68 张表 / 718 行 / 纯文本 141,034 字**，`U+FFFD` 计数 **0**；表数 67 → **68** 正是 §19.2 新增的「PostgreSQL 口径修正」漂移表带来的——又一次印证表数不能写进 `PROJECT_SPEC.md`。抽查 `本机装有 Docker`(3) / `缺的是这一轮的执行`(1) / `PostgreSQL 口径修正`(1) / `带日期的修订`(1) 全部命中；旧措辞（`没有可用的 Docker` / `Docker daemon 不可用` / `无可用容器` / `没有 Docker`）在 docx 里只剩 §19.2 漂移表的「原措辞」列与 ADR-006 的历史原文两处，都是有意保留的留痕，不是当前事实。**全树口径复核那轮再重生成一次**：同一命令重生成（`PROJECT_SPEC.md` 这轮只多了 §19.2 的一张漂移表），**当前产物 193,256 字节 / 3315 段 / 69 张表 / 720 行 / 纯文本 139,470 字**（读法：解 `word/document.xml`，按 `<w:p>` 切段、段内 `<w:t>` 顺序拼接、段间用 `\n` 连接，**实体解码后**计数——上一轮记的 141,034 是**未解码**的原始长度，同一个文件两种读法，差在 `&amp;` / `&lt;` 这类实体上），`U+FFFD` 计数 **0**；表数 68 → **69** 正是新增的那张漂移表带来的，又一次印证表数不能写进 `PROJECT_SPEC.md`。抽查 `全树口径复核`(1) / `44 个操作`(5) / `41 条路径`(2) / `43 个操作`(9) / `40 条路径`(2) / `447 passed`(8) / `141 条`(9) 全部命中；其中 `43 个操作` 与 `40 条路径` 的 11 处逐个核过上下文，全部在 §19.2 漂移表与附录 C 的分轮读数块里，不是当前事实。**与 `PROJECT_SPEC.md` 的同步校验**：现场用同一命令重建一份再逐字比对纯文本，`sync_text_identical=True` |

> **环境修复记录（2026-10-24，第二次）**：`ticketpilot.db` 再次漂移——`AUTO_CREATE_TABLES` 为 true 时，应用启动会按模型把 T4/T5 的四张表直接建出来，但**不推进 Alembic 版本戳**，于是 `alembic upgrade head` 在 `a9d4c2f7b135` 处报 `channel_messages already exists`。处置顺序：先备份（`.tmp/ticketpilot.db.bak-20261024-141200`），再把漂移库移出（`.tmp/ticketpilot.db.drifted-20261024`），然后用 `alembic upgrade head` + `alembic check` 重建、以 `AUTO_CREATE_TABLES=false` 跑 seed。这不是 T4/T5 引入的 schema 漂移，模型与迁移是一致的；**踩坑点是本地默认值**。另记：给 Alembic 传库要用 `-x db_url=sqlite+pysqlite:///...`（CI 就是这么做的），`TICKETPILOT_DB_URL` 这类变量名并不存在，写错会静默作用到真实库。

### 7.1 去掉跨项目对比表述（2026-10-24）

项目文档与源码注释里原本有四处指向外部项目的表述。这轮的做法是**只删来源署名、不删技术事实**：

| 位置 | 改前 | 改后 |
| --- | --- | --- |
| `app/retrieval/dense.py` 模块 docstring | 「Dense retrieval channel（ported from …）」 | 「Dense retrieval channel.」+ 向量存 `document_chunks.embedding`、不需要额外服务也不改表 |
| `app/retrieval/fusion.py` 模块 docstring | 「Cross-channel rank fusion（ported from …）」 | 「Cross-channel rank fusion.」+ 纯函数、无三方依赖、融合顺序可复现 |
| `app/retrieval/rerank.py` 模块 docstring | 「Ported from … reranker design: …」 | 直述「重排是检索链的**加法**」与四种失败形态 + 熔断窗口 |
| `app/retrieval/search.py` 的 `tokenize()` | 「Ported from … sparse.py.」 | 直述分词规则：ASCII 词 + CJK 一元与二元组 |
| `docs/ARCHITECTURE.md` 检索节末 | 「稠密、重排与融合来自外部项目的 RAG 能力移植」 | 「实现在 `app/retrieval/dense.py`、`fusion.py`、`rerank.py`、`hybrid.py`，统一保持「可选、失败可退化」的性质，没有把外部服务变成硬依赖」 |
| `docs/PROJECT_SPEC.md` §1.8 整节 | 一整节跨项目对照表 + 学习建议 | **整节删除**；§0.3 章节地图第 1 行改为「项目概览与最终状态」；§2.3 开头改为直述「TicketPilot 没有单独的 domain 层」；§19.2 补一张本轮漂移表留痕 |

**验证**：全仓库大小写不敏感搜索那个外部项目名（含 `app/`、`docs/`、`web/`、根目录 markdown，开 `node_modules` / `.venv` / `.git` / `__pycache__` / `dist`）命中 **0**；说明书 docx 重生成后同名串命中 **0**；本轮改动只碰了四个 docstring 与四份文档，**没有改任何行为**，所以九条门禁按原样复跑全绿——ruff 通过、mypy strict **134 文件**零错误、pytest **447 passed / 3 skipped**（收集 450、0 failures / 0 errors，产物 `.tmp/final_t10e.xml`）、Golden **40/40**、支持集 **61/61**、检索门槛 `GATE PASSED`（`bm25` 0.79/0.91/0.97/0.87/0.88、`hybrid(hashing)` 0.85/0.91/0.91/0.90/0.89）、回放 `REPLAY DIFF EMPTY`（61 recorded / 46 replayable / 15 skipped）、全新库 `alembic upgrade head` **11 条迁移** → head `c3b8e5a71d29` + **23 张业务表** 且 `alembic check` 无漂移、`npm run build` ✓、OpenAPI **41 个路径 / 44 个操作**。

> **一个被顺手去掉的坑**：docx 的**表数**原来写在 `PROJECT_SPEC.md` 附录 C 里，而表数会随文档结构无声变化——这轮删掉 §1.8 的对照表（−1）又补了 §19.2 的漂移表（+1），净数不变纯属巧合。表数与字节数 / 段数 / 字数都是**派生产物读数**，写进 docx 的来源文件就会形成自我引用，现已全部只留在 §7。

### 7.2 PostgreSQL 口径修正（2026-10-24）

上一轮的口播里说成了「本机无 Docker/PG 环境」，**这句话是错的**：本机装了 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的（基线 `323 passed`，默认跳过的三项全部执行）。这轮只改口径、不重跑——把「环境不具备」这个错误理由改成「这一轮没有执行」，**「141 条新用例仍无 PG 证据」这个事实一个字没动**。

| 位置 | 改前 | 改后 |
| --- | --- | --- |
| `CONTRIBUTING.md` Postgres 覆盖范围块 | 「本机没有 Docker daemon，只能在 SQLite 上跑」 | 「本机装了 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的（基线 `323 passed`），最近一轮收尾时守护进程没有运行、容器没有再起——缺的是这一轮的执行而不是环境」 |
| `docs/adr/ADR-019` 后果段 | 「（本会话没有可用的 Docker 守护进程）」 | 「（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出 `323 passed` 基线的；这一轮收尾时守护进程没有运行，没有再起容器——缺的是执行，不是环境）」 |
| `docs/adr/ADR-006` 验证现状 | 「**本机未实际启动 PostgreSQL**（Docker daemon 不可用、无 psql）」 | **原文保留**（ADR 是 2026-10-23 当天的决策记录），就地加一段**带日期的修订**：此后本机用 Docker 起过 PostgreSQL 16.15 一次性容器，320 用例版本跑通 `323 passed`；未被覆盖的是此后新增的用例与四张新表 |
| `docs/EVALS.md` 检索基线段 | 「本轮收尾没有可用的 Docker/PostgreSQL 环境」 | 「本轮收尾没有再起 PostgreSQL 容器（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的）」 |
| `docs/PROJECT_SPEC.md` §1.7 与附录 C | 「本轮收尾也没有可用的 Docker/PostgreSQL 环境来重证这一条」 | 「本轮收尾没有再起 PostgreSQL 容器（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出来的），要重证这一条得把容器起起来再跑一遍」 |
| `docs/PROJECT_SPEC.md` 附录 C 的 T7 读数块 | 「本轮收尾也没有可用的 Docker/PostgreSQL 环境」 | 「本轮收尾没有再起 PostgreSQL 容器，本机装有 Docker」 |
| `docs/NEXT_STEPS.md` §0 第 4 项 + §3 T9 偏差 5 + §5 的 T7/T8/T9/T10 四段读数块 | 「收尾时没有可用的 Docker/PostgreSQL 环境」「无可用容器」「本会话没有可用的 Docker 守护进程」 | 「本机有 Docker，缺的是这一轮的执行，见 §0 第 4 项」；§0 第 4 项另加一段**环境不是障碍**的说明 |
| `docs/PROJECT_SPEC.md` §19.2 | —— | 新增一张「PostgreSQL 口径修正」漂移表（2 行），记下错误措辞与改法 |

**验证**：全仓库大小写不敏感搜索那六种错误措辞，命中只剩两处且都是**有意保留的留痕**——`docs/adr/ADR-006` 的 2026-10-23 原文（旁边就有带日期的修订）与 `docs/PROJECT_SPEC.md` §19.2 漂移表的「原措辞」列；说明书 docx 重生成后同样只剩这两处（`本机装有 Docker` 命中 3、`缺的是这一轮的执行` 命中 1、`PostgreSQL 口径修正` 命中 1、`带日期的修订` 命中 1，`U+FFFD` 计数 0，产物 **192,899 字节 / 3308 段 / 68 张表 / 718 行 / 纯文本 141,034 字**，备份 `.tmp/TicketPilot项目说明书.docx.bak-20261024-t10f`）。本轮**只改文档、没有动任何代码**，九条门禁在同一个树状态上复跑全绿——`ruff check app tests scripts evals alembic` All checks passed · `mypy app` Success: no issues found in **134** source files · `pytest` **447 passed / 3 skipped**（收集 450，`failures=0 errors=0`，产物 `.tmp/final_t10f.xml` / `.tmp/final_pytest_t10f.txt`，99.54s）· `evals/run.py` **40/40** · `evals/support_run.py` **61/61** · `evals/retrieval_metrics.py --channels bm25,hybrid` GATE PASSED（`bm25` 0.79/0.91/0.97/0.87/0.88、`hybrid(hashing)` 0.85/0.91/0.91/0.90/0.89）· `evals/replay.py --self-check` 61 recorded / 46 replayable / 15 skipped → **REPLAY DIFF EMPTY** · 全新库 `alembic upgrade head` **11 条迁移** → head `c3b8e5a71d29` + **23 张业务表**（含 `alembic_version` 共 24 张）且 `alembic check` No new upgrade operations detected · `npm run build` ✓ built in 164ms · OpenAPI **41 个路径 / 44 个操作**。

> **一个要记住的区分**：「PG 复跑没做」是**执行缺口**，「PG 环境不具备」是**能力缺口**——写文档时只能写前者。写成后者会让人以为这台机器跑不了 PG，于是永远不补这条证据；实际上补它只需要把 Docker 起来再跑一遍 `TICKETPILOT_TEST_POSTGRES_URL=... python -m pytest`。

### 7.3 全树口径复核（2026-10-24）

这轮是一次**只读复核**：把前两轮要求的「去掉外部项目名」「PG 口径修正」连同其余口径一起过了一遍全树，核对对象是 37 个 markdown、`app/`、`web/`、`.tmp/` 与说明书 docx 的 `word/document.xml`。

| 口径 | 复核结果 |
| --- | --- |
| 外部项目名 | 全树大小写不敏感搜索命中 **0**（含 `.tmp/`、`web/`、隐藏目录）；docx 内 **0**。`PROJECT_SPEC.md` 里只剩 §19.2 漂移表的「原措辞」列两处，写的是「外部项目 / 跨项目对照表」这类**描述**而不是名字 |
| PostgreSQL | 七处文档措辞一致：本机装有 Docker、上一轮就是用它跑 PG 16.15 容器（`323 passed`）、缺的是这一轮的执行。残留的「Docker daemon 不可用」只在 ADR-006 的 2026-10-23 原文与 §19.2 漂移表里——是留痕，不是当前事实 |
| 规模数字 | `447 passed / 3 skipped`（收集 **450**、**33** 个测试文件）、mypy **134** 个源文件、**10** 个控制台页面、**23** 张表、**11** 条迁移（head `c3b8e5a71d29`）、**18** 篇 ADR（缺 ADR-012）、评测 **40 + 61** 条——逐条对着文件系统与 `create_app().openapi()` 核过，全部一致 |
| OpenAPI 计数 | **发现一处没跟上**：`README.md` 的「API 概览」首句仍写「共 43 个操作（40 条路径）」——T10 加了 `PUT /api/v1/memory/rules/{event_type}`，这一句没跟着改。已改为 **44 个操作 / 41 条路径**（那张表 44 行与 OpenAPI 逐条对得上）；两个旧数全树清零，只在 §19.2 漂移表的「原措辞」列留痕 |
| 能力类旧措辞 | 「没有 sink、`SENT` 不可达」「记忆不影响答案生成」「没有规则编辑页」「没有增删改接口」「代码进行中」「116 条」——逐处核对上下文，命中全部落在 §19.2 漂移表、ADR 的当日原文（旁边有带日期的修订）、附录 C 的分轮读数块、本文件 §3/§5 的分轮验收记录与本节这段复核记录本身，**没有一处被当成当前事实** |

**验证**：全树大小写不敏感搜索那个外部项目名，命中 **0**；说明书 docx 同样 **0**。规模数字逐项对着文件系统核过：`app/` 下 **134** 个 `.py`、`tests/` 下 **33** 个 `test_*.py`、`web/src/pages/` 下 **10** 个 `.tsx`、`docs/adr/` 下 **18** 篇 ADR、`evals/golden_set.json` **40** 条、`evals/support_set.json` **61** 条；`create_app().openapi()` 实测 **41** 条路径 / **44** 个操作（GET 26 / POST 15 / PUT 2 / DELETE 1），与 `README.md` 改后那句和 `PROJECT_SPEC.md` §12.1 表头一致。markdown 链接扫描 **37** 个文件、**0** 条断链。说明书按同一命令重生成（**193,256 字节 / 3315 段 / 69 张表 / 720 行 / 纯文本 139,470 字**，`U+FFFD` 计数 **0**），与 `PROJECT_SPEC.md` 现场重建的纯文本**逐字一致**（`sync_text_identical=True`）。本轮**只改文档、没有动任何代码**，因此上一轮那组门禁读数（pytest `447 passed / 3 skipped`、mypy 134 文件零错误、九条门禁全绿）仍然成立，没有重跑。

> **一个口径写法上的坑**：docx 的「纯文本字数」有两种读法——`<w:t>` 原文拼接（上一轮记的 141,034）与**实体解码后**拼接（本轮的 139,470）。同一个文件、数字差 1,827，因为 `&amp;` / `&lt;` 这些实体在原文里是 5 个字符、解码后是 1 个。以后统一用**解码后**的读法，并把它写进下一份读数里，免得两轮的数字看起来像对不上。

---

### 7.4 两库复跑、重排证据与说明书生成（2026-10-25）

这一轮把三件「代码有、证据没有」的事情收口，`app/` 与 `tests/` 一行没改。

**PostgreSQL 全量（取代 323 passed 基线）**

```bash
docker run -d --name ticketpilot-pg -e POSTGRES_USER=tp -e POSTGRES_PASSWORD=tp \
  -e POSTGRES_DB=ticketpilot -p 127.0.0.1:55432:5432 postgres:16.15
TICKETPILOT_TEST_POSTGRES_URL=postgresql+psycopg://tp:tp@127.0.0.1:55432/ticketpilot \
  python -m pytest
```

结果（产物 `.tmp/pg_full_20261025.txt`）：**458 passed / 0 skipped（109.92 s）**；3 条 PG-only 用例
（`test_postgres_concurrency.py` 2 条、`test_postgres_outbox.py` 1 条）真的执行了，不再是 skip。
同一容器上另建 `eval_pg` 库、`alembic upgrade head`（11 条迁移 → `c3b8e5a71d29`）后跑
Golden **40/40**、支持集 **61/61**，`alembic check` 输出 `No new upgrade operations detected.`。
T2 起新建的 10 个测试文件（148 条）与四张新表全部覆盖；「141 条新用例从未在 PG 执行」的口径作废。
**仍然没有的**：四通道检索指标脚本本身在 PG 上的输出——PG 侧跑的是 `test_retrieval_eval.py`
的门槛断言，不是 `retrieval_metrics.py` 的四行表。

**重排通道（bge-reranker-v2-m3）**

LM Studio 不提供 `/rerank`（`POST /v1/rerank`、`/v1/score` 都返回 `Unexpected endpoint or method`，
2026-10-25 复测），所以用本机 Docker：

```bash
MSYS_NO_PATHCONV=1 docker run -d --name ticketpilot-reranker -p 127.0.0.1:7998:7997 \
  -v E:/My-Agentix/reranker-cache:/app/.cache:ro michaelf34/infinity:latest-cpu v2 \
  --engine torch --model-id /app/.cache/model \
  --served-model-name text-embedding-bge-reranker-v2-m3 --port 7997 --batch-size 4
RETRIEVAL_RERANK_ENABLED=true RERANK_URL=http://127.0.0.1:7998/rerank \
RERANK_MODEL=text-embedding-bge-reranker-v2-m3 \
  python evals/retrieval_metrics.py --channels bm25,dense,hybrid,rerank
```

读数（`.tmp/retrieval_rerank_live.json`）：`recall@1` 0.794 → 0.912、`mrr` 0.875 → 0.956、
`nDCG@5` 0.882 → 0.944，但 **`recall@3` 0.971 → 0.912**、`recall@5` 不变。
延迟（`.tmp/rerank_timing.py`）：61 条查询 × 17 个候选块，0 失败，
mean **2277 ms** / p50 **2267** / min **2074** / max **2563**（CPU）。默认仍关闭。
`MSYS_NO_PATHCONV=1` 不是装饰：Git Bash 会把容器内的 `/app/.cache/model` 改写成
`D:/Git/Git/app/.cache/model`，模型加载直接失败。

**说明书生成脚本**

`scripts/build_manual.py`（`make manual`）把「Pandoc 渲染 → 校验 → 原子替换」做成一条命令：

- 渲染到同目录临时文件，Pandoc 非零退出即失败；
- 校验体积 ≥ 50 KB、XML `<w:p>` 段 ≥ 1000、表格 ≥ 1、所有 XML part 的 `U+FFFD` 计数为 0；
- 全部通过才 `replace()` 旧 docx，失败时旧文件原样保留并打印哪一项没过。

本轮产物 `docs/TicketPilot项目说明书.docx`：**205,485 字节 / 3,371 个 XML 段（含表格单元格）/
1,198 个正文段 / 71 张表 / `U+FFFD` 计数 0 / 13 个 XML part**。上一版是 193,256 字节 / 69 表；
两表的增量来自 §19.2 的新漂移表与 §11.5 的四通道表。抽查关键串命中：
`458 passed / 0 skipped`(5)、`109.92`(4)、`bge-reranker-v2-m3`(4)、`0.9118`(6)、
`build_manual.py`(1)、`REPLAY DIFF EMPTY`(9)。收尾复核时 §19.1 与附录 C 的两处口径又改了两句
（447/450/33 → 455/458/34、哈希替身读数标注为不可引用），按同一命令再重生成一次：
字节数 **205,153 → 205,485**，段数 / 表数 / `U+FFFD` 均不变。

**同步面**：README 的检索四行表与重排节、`docs/PROJECT_SPEC.md` §11.4/§11.5/§14.1/§14.2.1/
§19.1/§19.2/§19.4 与附录 C、`docs/EVALS.md` 基线节与诚实边界、`docs/AGENT_PLATFORM_PLAN.md`、
`docs/GOLDEN_SET.md`、`docs/SCOPE.md`、`docs/SECURITY.md`、`CONTRIBUTING.md`、ADR-006 / ADR-019 /
ADR-020 的 2026-10-25 修订。**没有做的**：四通道指标未在 PG 跑；重排没有生产部署、超时预算与并发压测；
docx 只校验编码与结构，不做版式 / 分页目视检查（浏览器截图仍未取）。

### 7.5 五通道两库、重排延迟预算与说明书版面实看（2026-10-25）

上一轮留下的三个缺口，这一轮全部收口——**其中两个是「从来没测过」，一个是「测了但没人看」**。

**① 检索指标脚本在 PostgreSQL 上真跑了**

```bash
# SQLite
python evals/retrieval_metrics.py --channels bm25,dense,hybrid,gated,rerank
# PostgreSQL（独立库 tp_eval）
python evals/retrieval_metrics.py --channels bm25,dense,hybrid,gated,rerank \
  --database-url postgresql+psycopg://tp:tp@127.0.0.1:55432/tp_eval
```

结果（产物 `.tmp/channels_sqlite.json` / `.tmp/channels_postgres.json`）：**两份 JSON 逐位一致**。

| 通道 | recall@1 | recall@3 | recall@5 | MRR | nDCG@5 | embedding 调用率 |
|---|---|---|---|---|---|---|
| `bm25` | 0.794 | 0.912 | 0.971 | 0.875 | 0.882 | 0 |
| `dense(bge-m3)` | 0.853 | 0.912 | 0.971 | 0.926 | 0.922 | 1.00 |
| `hybrid` | 0.794 | 0.971 | 0.971 | 0.882 | 0.889 | 1.00 |
| `gated`（出厂） | 0.794 | 0.971 | 0.971 | 0.882 | 0.889 | **0.2941** |
| `rerank(bge-reranker-v2-m3)` | 0.912 | 0.912 | 0.971 | 0.956 | 0.944 | 1.00 |

**能一致不是理所当然的——途中发现并修掉了一个真实的可复现性缺陷。**
`PolicySearchService.visible_chunks()` 原本没有 `ORDER BY`，行序随数据库返回顺序而变；
BM25 同分行的次序跟着变 → 重排拿到的候选集变 → 同一份语料在 SQLite 与 PostgreSQL 上
排出不同的名次。修法是给它一条确定的排序（`app/retrieval/search.py`：`visible_chunks()`
按块 ID 排序），修完两库逐位一致。**这是本轮唯一的行为改动**，其余都是文档与新脚本。
这条修复的价值不在数字本身，而在于「这个排序可复现」从此是个可以断言的性质。

**② 重排延迟有了可执行的预算判定**

`evals/rerank_latency.py` 把计时探针插进**生产**的 `HybridPolicySearch`，量的是线上那条
融合后的候选集（15 条），而不是另造一份数据：

```bash
python evals/rerank_latency.py --rerank-url http://127.0.0.1:7997/rerank
python evals/rerank_latency.py --rerank-url http://127.0.0.1:7997/rerank --enforce   # 超预算退出码 1
```

读数（产物 `.tmp/rerank_budget.txt`）：`60 timed, 0 failed, 0 without candidates`、
`candidates min 15 / max 15`、`mean 2300 / p50 2281 / **p95 2602** / min 2081 / max 2689 ms`、
`budget_ms 300` → **`verdict OVER BUDGET`**。
结论从「重排没有可引用数字」变成「**测了，超预算**」：这台纯 CPU 机器上重排不能进请求路径，
默认仍关闭，但理由从猜测换成了数字。诚实的边界：p95 是这台机器上的一次读数，
300 ms 是我们定的决策值，两者的边界是「这台 CPU 机器 + 这份交互目标」，不是通用结论。

**③ 说明书 docx 做了版面目视检查**

上一轮只做了结构与编码校验（`U+FFFD` = 0）。这一轮把 docx 真的渲染成图看：

```bash
# Gotenberg 8 容器内 LibreOffice 转 PDF（v8 的路由是 /forms/libreoffice/convert，
# 不是 v7 的 /forms/libreoffice/convert-to/pdf）
docker run --rm -v /d/Code/TicketPilot/docs:/work gotenberg/gotenberg:8 \
  curl -s -o /work/manual.pdf -F files=@/work/TicketPilot项目说明书.docx \
  http://localhost:3000/forms/libreoffice/convert
# 容器内 pdftoppm 逐页转 PNG，再逐页目视
```

看过的：标题页、正文、表格、复选框。结论：**复选框里的空心方块是真实的 U+2610 球票框**
（`pdftotext` 字节级核对），不是豆腐块；U+FFFD 计数 0；无 `w:sym` / 私用区字符；
本机没有装 LibreOffice / Word（`/c/Program Files/LibreOffice/program` 下只有 `logs`），
所以走容器这条路是必要的，不是图省事。

本轮**文档收口之后**又按 `python scripts/build_manual.py`（`make manual`）重生成了一次说明书：
**219,290 字节 / 3,540 个 XML 段 / 76 张表 / `U+FFFD` 计数 0 / 13 个 XML part**（上一版 205,485 字节 / 71 表）。
抽查本轮关键串全部命中：`OVER BUDGET`(6) · `visible_chunks`(5) · `ORDER BY`(4) · `502 passed / 0 skipped`(6) ·
`119.71`(6) · `tp_eval`(7) · `rerank_latency.py`(4) · `b4e8d2f6a1c9`(5) · `0.2941`(3) · `2602`(3)。
重生成用的是 `docs/PROJECT_SPEC.md` 这一个来源文件，所以文档改完就重跑一次 `make manual` 即可回灌。

**目视检查在最终版 docx 上又做了一遍**（不是上一版的）：219 页的 PDF 里抽了 5 页看——
第 1 页（标题页与事实基线）、第 79 页（§11.5 的两库一致性）、第 108 页（§14.2.1 与一张表）、
第 133 页（§19.4 未验证清单）、第 187 页（收尾门禁记录）。中文正文、粗体、表格边框、行内代码
都渲染正常，没有溢出、没有空白块、没有豆腐块。产物在 `.tmp/pdfpages_final/`。

**同步面**：README 检索基线表与重排节、`docs/PROJECT_SPEC.md` §11.5 / §14.2.1 / §19.1 / §19.2 与附录 C、
`docs/EVALS.md` 基线节与「重排的延迟预算」节、`docs/AGENT_PLATFORM_PLAN.md` 的规模事实与阶段表、
`docs/NEXT_STEPS.md` §0 / §1.1 / §3 T6 / §5 / §7.5、`docs/GOLDEN_SET.md`、`docs/SCOPE.md`、
`CONTRIBUTING.md`、ADR-006 / ADR-019 / ADR-020 的第二轮 2026-10-25 修订。
**没有做的**：生产托管 embedding / rerank 的接入与压测；重排的并发与超时压测；
服务商侧投递；浏览器截图（应用内浏览器当时没有可见视口）。

**收尾时的最终门禁读数**（在**本节全部文档改完之后**的同一个树状态上一次性复跑）：

| 门禁 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check app tests scripts evals alembic` | All checks passed! |
| 类型检查 | `python -m mypy app` | Success: no issues found in **137** source files |
| 单元与集成（SQLite） | `python -m pytest` | **496 passed / 6 skipped**（收集 502、43 个测试文件、118.54 s） |
| 单元与集成（PG + Redis） | 两个环境变量都指上 | **502 passed / 0 skipped**（119.71 s） |
| 任务路径 | `python evals/run.py` | **40/40** |
| 只读路径 | `python evals/support_run.py` | **61/61** |
| 检索评测 | `python evals/retrieval_metrics.py --channels bm25,hybrid` | **GATE PASSED**（bm25 读数与 T8 逐字相同） |
| 回放自检 | `python evals/replay.py --self-check` | **REPLAY DIFF EMPTY**（46 段可回放 / 15 跳过） |
| 迁移漂移 | 全新库 `alembic upgrade head` + `alembic check` | 13 条迁移 → head `b4e8d2f6a1c9`；No new upgrade operations detected. |
| 前端 | `cd web && npm run build` | ✓ built in 214ms |

**耗时读数会随机器负载浮动**：早先一次同读数是 194.54 s（当时同一台机器上还在并发跑五通道 PG 指标与重排延迟基准），
最终这次是 119.71 s；SQLite 侧同理（121.94 s → 118.54 s）。**稳定的是通过 / 跳过条数，不是秒数**，
所以文档里以最后一次复跑为准，并在这里把差异记下来——把耗时当性能指标读会误判。

---

## 8. 不做（T4–T10 这几轮）

- 通用 Agent 框架、多 Agent 编排；
- 真实 OIDC 与真实退款渠道（保持 fail-closed 的演示定位）；
- 语音 IVR 完整电话栈（`ChannelCapabilities` 已为它塑形，但没有适配器、没有媒体面）；
- 生产级 embedding / rerank 服务的部署、压测与超时预算（本机 LM Studio + Docker Infinity 的端点验证已做，见 §3 T6；接入生产托管端点不在本轮）；
- pgvector / ANN（稠密目前是全表扫描）；
- Redis 跨进程配额（限流桶仍在进程内）；
- 主动通知的**服务商协议与送达回执**（T7-4 已交付出站投递、`SENT` 首次可达；但仍只有 `none` 与 `local_file` 两种传输，没有真实邮件/IM 服务，也没有「服务商确认送达」这一独立状态）；
- ~~记忆进入答案生成（个性化仍是 G6，本轮只交付档案治理）~~ —— 本条是 T4–T7 那轮的边界，**已由 T9 收口**（四种模板效果）；T9 这轮有意不做的部分：把档案拼进提示词让模型自己决定怎么用、按档案预填槽位（金额 / 订单号）、「上次走通的路径」式推荐、按租户灰度开关、多语言（`locale`）回复；
- 附件解析、EXIF 剥离与类型白名单；
- 全渠道一次铺满（落 Web + 邮件 + 微信/企业微信，其余四个只有能力声明）；
- **T10 这轮有意不做**（写进 ADR-019 后果段）：
  - **规则版本表与一键回滚**——只有审计里的 `template_sha256`，改错了手打回去；
  - **规则文案的双人复核**——审批有四眼，规则文案**没有**，这是当前事实，任何文档都不许写成「配置有复核」；
  - **按租户灰度与阈值外置**——ADR-012 的空位，编号仍不回收；
  - **模板引用客户档案里的任何东西**——与 T9 同一条纪律：客户自由文本不进客户收到的内容；
  - **删除规则**——`notification.rule_id` 会指向已不存在的行，历史断裂；「不再发」由 `enabled=false` 表达；
  - **规则改动进评测集**——`evals/` 不建规则也不改规则，与出站投递同口径。
