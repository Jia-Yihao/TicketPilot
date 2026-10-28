# Proposal: 性能/治理真缺口收敛 + bench 口径落地（TP 波次）

## 为什么

外部性能/治理清单 TP-1…TP-7 经两批只读盘点实测后，一半建议被否掉（审批队列复合索引、
对账提速、Outbox 积压读数、对账离线测量、双库跑、TP-4「模型产 SQL 计划」前提）。剩余
**8 个真缺口**（编号 G1…G8，见下）至今没有闭环：

- G1（TP-1）模型调用前预算刹车未实现：`llm_usage` 是纯事后记账
  （`app/models/llm_usage.py:29-52` 写于 `app/services/llm_usage.py:37-72`；读侧仅
  `GET /api/v1/analytics/llm-usage`）。`RateLimitMiddleware`（`app/api/middleware.py:120`）
  限 HTTP 请求；`agent_request_limit=2`（`app/core/config.py:112` →
  `app/agent/pydantic_agent.py:213`）只管单次 run 内 UsageLimits。仓库内没有任何
  「调用前查预算、超了改走规则读数或追问」的分支。
- G2（TP-1）收窄集 vs 无信号集（`narrowed=false`）的 token/延迟 A/B 读数缺失
  （trace 已记 `model_candidates`，`app/agent/pydantic_agent.py:118-136`，但没有两侧对照）。
- G3（TP-2）候选数 M 扫描未做：`retrieval_candidate_pool` 默认 20
  （`app/core/config.py:72` → `app/retrieval/build.py:112` → `app/retrieval/hybrid.py:88,98,116,189`）；
  `evals/dense_gate_analysis.py:180` 把 pool 固定成 20，扫的是 DenseGate 阈值，不是 M。
- G4（TP-4）预取阈值 `analytics_prefetch_max_rows=200`（`app/core/config.py:38`，消费于
  `app/main.py:114`、`app/agent/support_agent.py:213`，路由判定 `app/sql/router.py:127`，
  行数来自 scoped COUNT `app/sql/prefetch.py:91-103`）接线完整但从未用真实数据量校准过。
- G5（TP-5）事件读取无界：`app/repositories/tasks.py:118-125` 的 `events()` 无 LIMIT，
  整表读全量后在 Python 里 `ids.index(after_id)+1` 切片；`follow_events`
  （`app/services/runtime.py:1162-1220`）每轮 2 次查询、poll 0.5s（`:1168`）。
- G6（TP-6）seed 每个用例重跑：`tests/conftest.py:12,34` 把 `seed_all` 放在 function 级
  `app_factory`（`app/seed.py` 190 行、4 个 seeder），另有 ≥9 个测试模块直接调用；
  CI（`.github/workflows/ci.yml:19`）只有 `python -m pytest`。
- G7（TP-7）负验证未进门禁：`scripts/guard_negative_check.py` CASES 恰 6 条（`:193-230`），
  `Makefile:18-19` 有 `guard-negative`，但 CI 与 pytest 均不跑它；「六红一绿」至今只是
  人工读数（`docs/guide/证据索引.md:661,666`）。
- G8（TP-7）文档数字机器核对覆盖面不足：`tests/test_docs_consistency.py:200-215` 的
  COMPUTERS 有 14 项，`docs/fact-baseline.yaml` 44 条 claims（12 条 `in_manual: true`）
  只覆盖 6 个文件；`docs/guide/评测说明.md`、`docs/guide/架构说明.md`、`docs/guide/接口说明.md`、`docs/adr/*`
  里的**可现算**数字无机器核对（时点读数如 p50/p95 不在可现算之列，口径见 plan）。

同时，所有「读数型」缺口（G2/G3/G4 与 TP-3 残留的查询形态 baseline）都缺一个可复现的
测量口径：仓库没有独立的 `bench/`，读散落在 evals 脚本与人工记录里，且本仓库已吃过
「A/B 产物没记 commit、无法复核产自哪棵树」的亏。

## 改什么

- 新增 capability `benchmark-harness`：`bench/` 目录契约（走原接口、不复制业务逻辑、
  每模块 fixture+run.py+baseline.json+env_fingerprint、门禁归属、契约回归复用既有两守卫）。
  bench 测量的项目级口径**已落地**于 `.qoder/rules/benchmark-harness.md`（编排方 2026-10-27
  裁定改名并纠偏），本包规格与计划一律引用该文件，不再新建第二份口径。
- 新增 capability `project-gates`：负验证进 CI；文档数字核对覆盖面按「可现算」原则扩展。
- `agent-orchestration`：模型调用前预算刹车（flag 化，默认关=现状等价；越预算只降级读，
  永不放宽任何闸）。
- `task-events`：事件读取有界化（SQL 侧 keyset 游标 + 分页上限，语义不变）。
- 测量任务：G2 A/B、G3 M 扫描、G4 预取校准、TP-3 审批队列耗时 baseline（只读数不改形态）。
- 治理小项：G6 seed 重复执行（阻塞组）、`app/services/outbox.py` 的无调用方
  `pending_count()` 死代码处置（**裁定=删除**：全仓零调用方、不带租户边界，API 已有
  带租户边界的 `count_for`；不保留原样，也不新增第三种积压读法）。

## 不改什么

- 不改任何已砍项：审批队列复合索引、对账/复核提速、Outbox 积压新读法、对账离线测量、
  双库跑、「让模型产 SQL 计划」。
- 不动退款写路径五要素（资格校验、四眼审批、`action_digest`、资源版本绑定、幂等键）；
  不改 `visible_chunks()` 的 ORDER BY；不放宽 SQL 守卫 19 个 reason 码
  （`app/sql/errors.py:14-34`）、`NO_PLAN` 转人工（`app/sql/router.py:118-120` →
  `app/agent/actions.py`）、L3 不进 prompt（`app/sql/snapshot.py:117-152`）。
- 不动统一错误信封（`app/core/errors.py:160-176`）、`X-Request-ID`、验签 fail-closed
  （`app/channels/base.py:222-258`）、幂等闸。
- 不动模型侧契约：`IntentOutput`、`ModelDecision.verifies()` 六条件
  （`app/agent/decision.py:45-62`）、「单轮写动作 1 次模型调用」读数；跨 worker 同表口径
  不得改成内存总线。
- 不改重排判定的既有事实表述：`OVER BUDGET` 是 `evals/rerank_latency.py:249` 的离线延迟
  判定；运行时关闭靠配置默认 `retrieval_rerank_enabled=False`（`app/core/config.py:70`）
  + 熔断 fail-open，任何文档/任务不得写成「自动关/已优化」。
- 不在本轮改 `app/agent/support_agent.py`、`tests/conftest.py`、`tests/test_decision_reuse.py`、
  `docs/adr/ADR-030-决策复用.md`、`.qoder/rules/refund-reason-taxonomy.md`
  （在途 T-B1-R3 责任范围），除分派单标注的阻塞任务外。

## 定级理由（normal，不建 design.md）

按 `openspec/AGENTS.md` §2，large 的判定是「新子系统、数据迁移、权限或计费变化」。本包
判 **normal**，理由：`bench/` 只是**读数生产者**——不改产品行为、无迁移、无权限/计费
变化，且其任务（计划任务 1、5–8）须待用户确认「bench 可先于收尾轮开工」后才动工；未获
确认时本包实际产出仅口径引用与规划文档。产品代码改动仅阻塞组任务 9–11，各为 flag 化
默认关或单能力有界小改。技术取舍与代价由 `plan.md`、`dispatch.md` 及收尾轮 ADR 承载，
不另建 `design.md`（`openspec/AGENTS.md` §2「判定不确定时按 normal 走」）。

## 待用户确认（登记，非本包自行改判）

- **时点读数「永不进现算绑定」相对外部清单是范围缩小**：TP-7 外部清单原文要求把靠人工
  同步的部分「**逐步**」绑到脚本输出；本包（计划任务 4 与 `specs/project-gates/spec.md`）
  裁定 p50/p95 类时点读数**永久**不进 COMPUTERS 现算项，改走结构核对（本波新增/被改小节
  必须带生成命令+日期）。此差异属范围缩小，本包不自行定案，登记待用户确认。两个选项的
  代价：**现算绑定**——每次重算须改写历史读数或维护「现算值+历史值」双轨，违反「时点
  读数标时点、历史保留」既有纪律；**结构核对**（本包当前方案）——数字不被机器复核，依赖
  标时点纪律与 reviewer 读 diff。用户若选「逐步现算绑定」，须先改计划任务 4 与
  project-gates 规格再实现，不得实现时自行择一。
