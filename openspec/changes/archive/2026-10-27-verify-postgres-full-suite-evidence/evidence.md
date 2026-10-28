# T-4 证据：TicketPilot PostgreSQL 全量测试真实读数

- 任务编号：T-4
- 执行日期：2026-10-26（UTC；本机 +0800）
- 执行环境：Windows，Git Bash，Docker 29.5.3
- 范围声明（**本轮自述，不是已核事实**）：本轮的意图是只补证据，不改产品代码、测试代码、迁移、配置、CI、README、docs 既有文件；**这条声明由执行者自述**。会话开始前工作树已是脏树（当时 `git status` 记为 71 个已修改 + 47 个未跟踪），此后本轮与外部进程**并发写同一棵树**，因此**当前的 `git status` 无法独立复核本轮是否改动过既有文件**（`openspec/AGENTS.md` §8.2 第 3 条：范围声明不得依赖自证）。
  - 可核的部分只有三件：① 仓内**唯一新增路径**是 `.pytest_tmp/`（pytest `--basetemp` 副产物，第 1 节）；② 本变更目录内两个**受控**新增产物是 `evidence.md` 与 `tasks.md`（不含仓外读数文件）；③ 仓外读数产物两份（第 6 节）。本文件因此**不**声称是「唯一新增产物」，只声称是两个**受控**产物之一。
  - 窗口内被外部进程改写的 tracked 文件清单见第 8 节。

## 1. 树状态（自述；运行前后**非逐字一致**，另见第 8 节的外部并发写入）

```
$ git log -1 --oneline
8dba4ea docs: 补齐守卫轮的三处同步缺口
```

### 1.1 运行前的树状态（**只有计数与摘录，不是逐字快照**）

`git status --short`（会话开始时抓取）：**71 个已修改（M）+ 47 个未跟踪（??）**，为脏树。完整清单过长，代表性摘录：

- 已修改：`.env.example`、`.github/workflows/ci.yml`、`Makefile`、`README.md`、`app/**`（actions/escalation/handoff/pydantic_agent/routing/support_agent/turns、api/v1/*、core/*、services/*、sql/*）、`docs/*`（评测说明.md、黄金集说明.md、证据索引.md 等）、`evals/replay.py`、`tests/test_handoff.py` 等 8 个测试文件、`web/src/*`
- 未跟踪：`AGENTS.md`、`openspec/`、`docs/guide/证据索引.md`、`alembic/versions/a1c7e9d4b820_proactive_rule_governance.py`、`app/agent/decision.py`、`app/sql/{narrative,prefetch,router,snapshot}.py`、ADR-026～033、`tests/test_analytics_*` 等 15 个测试文件、`scripts/*` 6 个

**缺陷登记**：运行前只保留了上述计数与摘录，**没有逐字保留完整清单**。按 `openspec/AGENTS.md` §8.1「运行前与运行后各一份 `git status --short` + `git diff --stat`，逐字粘贴」，本轮的**运行前快照不达标**；1.2 的现状快照只能证明「现在是什么样」，**不能证明「运行前是什么样」**，也无法据此断言本轮是否动过既有文件。

### 1.2 现状树状态快照（2026-10-26 19:30 +0800，逐字粘贴）

`$ git status --short`（全文 139 行）：

```
 M .env.example
 M .github/workflows/ci.yml
 M CONTRIBUTING.md
 M Makefile
 M README.md
 M app/agent/actions.py
 M app/agent/escalation.yaml
 M app/agent/handoff.py
 M app/agent/pydantic_agent.py
 M app/agent/routing.py
 M app/agent/support_agent.py
 M app/agent/turns.py
 M app/api/v1/analytics.py
 M app/api/v1/memory.py
 M app/api/v1/tasks.py
 M app/channels/base.py
 M app/channels/gateway.py
 M app/channels/registry.py
 M app/core/config.py
 M app/core/policy.py
 M app/main.py
 M app/models/memory.py
 M app/repositories/commerce.py
 M app/retrieval/build.py
 M app/schemas/analytics.py
 M app/schemas/channels.py
 M app/schemas/memory.py
 M app/seed.py
 M app/services/conversations.py
 M app/services/escalation.py
 M app/services/handoff.py
 M app/services/memory.py
 M app/services/proactive.py
 M app/services/runtime.py
 M app/services/support.py
 M app/sql/errors.py
 M app/sql/planner.py
 M app/sql/service.py
 M docs/records/智能体平台设想.md
 M docs/guide/接口说明.md
 M docs/guide/架构说明.md
 M docs/guide/评测说明.md
 M docs/guide/黄金集说明.md
 M docs/下一步规划.md
 M docs/spec/项目规格.md
 M docs/guide/范围与边界.md
 M docs/guide/安全说明.md
 M "docs/TicketPilot\351\241\271\347\233\256\350\257\264\346\230\216\344\271\246.docx"
 M docs/adr/ADR-010-受控问数.md
 M docs/adr/ADR-019-主动规则可编辑面.md
 M docs/adr/ADR-020-对话入口的模型缺口.md
 M docs/fact-baseline.yaml
 M docs/spec/manual-build.json
 M evals/replay.py
 M evals/retrieval_metrics.py
 M evals/support_run.py
 M evals/support_set.json
 M tests/test_handoff.py
 M tests/test_llm_gap.py
 M tests/test_llm_usage.py
 M tests/test_model_distribution.py
 M tests/test_policy_engine.py
 M tests/test_proactive_rules.py
 M tests/test_pydantic_agent.py
 M tests/test_retrieval_channels.py
 M tests/test_security_model_cross.py
 M tests/test_sql_analytics.py
 M tests/test_sql_guard.py
 M web/src/api.ts
 M web/src/components/ContextCard.tsx
 M web/src/pages/AgentWorkbench.tsx
 M web/src/pages/ChannelMatrix.tsx
 M web/src/pages/ConversationPage.tsx
 M web/src/pages/ProactiveRules.tsx
 M web/src/pages/TaskDetailPage.tsx
 M web/src/styles.css
 M web/src/types.ts
?? .pytest_tmp/
?? .qoder/
?? AGENTS.md
?? alembic/versions/a1c7e9d4b820_proactive_rule_governance.py
?? app/agent/analytics_narrator.py
?? app/agent/candidates.py
?? app/agent/decision.py
?? app/channels/availability.py
?? app/retrieval/rewrite.py
?? app/sql/narrative.py
?? app/sql/prefetch.py
?? app/sql/router.py
?? app/sql/snapshot.py
?? docs/guide/证据索引.md
?? docs/guide/快速开始.md
?? docs/adr/ADR-026-查询改写.md
?? docs/adr/ADR-027-分层检索评测.md
?? docs/adr/ADR-028-意图语料评测.md
?? docs/adr/ADR-029-预取统计.md
?? docs/adr/ADR-030-决策复用.md
?? docs/adr/ADR-031-主动规则治理.md
?? docs/adr/ADR-032-意图缺口闭环.md
?? docs/adr/ADR-033-澄清候选降级.md
?? docs/adr/ADR-034-意图候选收窄.md
?? docs/code-anchors.yaml
?? evals/analytics_dimension_set.json
?? evals/clarification_candidates.py
?? evals/clarification_scenarios.json
?? evals/external/
?? evals/intent/
?? evals/intent_candidates.py
?? evals/intent_gaps.py
?? evals/intent_metrics.py
?? evals/retrieval/
?? evals/retrieval_set.json
?? openspec/
?? scripts/build_intent_set.py
?? scripts/build_retrieval_set.py
?? scripts/manage_intent_gaps.py
?? scripts/sample_external_phrasings.py
?? scripts/sample_intent_phrasings.py
?? scripts/sse_walkthrough.py
?? tests/test_analytics_faithfulness.py
?? tests/test_analytics_followup.py
?? tests/test_analytics_narrative.py
?? tests/test_analytics_prefetch.py
?? tests/test_analytics_router.py
?? tests/test_channel_availability.py
?? tests/test_clarification_evidence.py
?? tests/test_decision_reuse.py
?? tests/test_doc_code_anchors.py
?? tests/test_intent_candidate_eval.py
?? tests/test_intent_candidate_model.py
?? tests/test_intent_candidate_trace.py
?? tests/test_intent_candidates.py
?? tests/test_intent_eval.py
?? tests/test_intent_gaps.py
?? tests/test_query_rewrite.py
?? tests/test_retrieval_set.py
?? tests/test_sql_dimensions.py
?? tests/test_support_candidates.py
?? tests/test_task_event_stream.py
```

`$ git diff --stat`（仅 tracked 文件；Git 的 CRLF 提示走 stderr，未计入下表）：

```
 .env.example                                       |   41 +
 .github/workflows/ci.yml                           |    3 +
 CONTRIBUTING.md                                    |   53 +-
 Makefile                                           |    9 +-
 README.md                                          | 1568 ++++----------------
 app/agent/actions.py                               |  303 +++-
 app/agent/escalation.yaml                          |    9 +-
 app/agent/handoff.py                               |   27 +
 app/agent/pydantic_agent.py                        |  190 ++-
 app/agent/routing.py                               |   26 +
 app/agent/support_agent.py                         |  241 ++-
 app/agent/turns.py                                 |    3 +
 app/api/v1/analytics.py                            |    9 +
 app/api/v1/memory.py                               |  108 +-
 app/api/v1/tasks.py                                |   39 +-
 app/channels/base.py                               |   20 +-
 app/channels/gateway.py                            |   11 +-
 app/channels/registry.py                           |   16 +-
 app/core/config.py                                 |   34 +
 app/core/policy.py                                 |    3 +
 app/main.py                                        |    9 +-
 app/models/memory.py                               |   85 ++
 app/repositories/commerce.py                       |   19 +
 app/retrieval/build.py                             |   63 +-
 app/schemas/analytics.py                           |    2 +
 app/schemas/channels.py                            |   11 +
 app/schemas/memory.py                              |   62 +
 app/seed.py                                        |   26 +-
 app/services/conversations.py                      |    5 +
 app/services/escalation.py                         |   12 +-
 app/services/handoff.py                            |    3 +
 app/services/memory.py                             |    1 +
 app/services/proactive.py                          |  516 ++++++-
 app/services/runtime.py                            |  104 +-
 app/services/support.py                            |   60 +
 app/sql/errors.py                                  |    1 +
 app/sql/planner.py                                 |   95 +-
 app/sql/service.py                                 |  209 ++-
 docs/records/智能体平台设想.md                        |   83 +-
 docs/guide/接口说明.md                                        |  162 +-
 docs/guide/架构说明.md                               |   57 +-
 docs/guide/评测说明.md                                      |  410 ++++-
 docs/guide/黄金集说明.md                                 |   13 +-
 docs/下一步规划.md                                 | 1384 ++++++++++++++++-
 docs/spec/项目规格.md                               |  869 +++++++++--
 docs/guide/范围与边界.md                                      |   75 +-
 docs/guide/安全说明.md                                   |   17 +-
 ...3\256\350\257\264\346\230\216\344\271\246.docx" |  Bin 226384 -> 267608 bytes
 docs/adr/ADR-010-受控问数.md         |    4 +-
 .../adr/ADR-019-主动规则可编辑面.md |    5 +-
 docs/adr/ADR-020-对话入口的模型缺口.md                 |    2 +
 docs/fact-baseline.yaml                            |   50 +-
 docs/spec/manual-build.json                             |    8 +-
 evals/replay.py                                    |   12 +-
 evals/retrieval_metrics.py                         |  337 +++--
 evals/support_run.py                               |   34 +-
 evals/support_set.json                             |   92 +-
 tests/test_handoff.py                              |   98 +-
 tests/test_llm_gap.py                              |   18 +-
 tests/test_llm_usage.py                            |    6 +-
 tests/test_model_distribution.py                   |    5 +-
 tests/test_policy_engine.py                        |   11 +
 tests/test_proactive_rules.py                      |  564 ++++++-
 tests/test_pydantic_agent.py                       |   12 +-
 tests/test_retrieval_channels.py                   |    7 +-
 tests/test_security_model_cross.py                 |    5 +-
 tests/test_sql_analytics.py                        |    4 +-
 tests/test_sql_guard.py                            |    3 +
 web/src/api.ts                                     |  250 +++-
 web/src/components/ContextCard.tsx                 |    6 +-
 web/src/pages/AgentWorkbench.tsx                   |   34 +
 web/src/pages/ChannelMatrix.tsx                    |   26 +-
 web/src/pages/ConversationPage.tsx                 |   52 +-
 web/src/pages/ProactiveRules.tsx                   |  242 ++-
 web/src/pages/TaskDetailPage.tsx                   |   84 +-
 web/src/styles.css                                 |   28 +
 web/src/types.ts                                   |   84 +-
 77 files changed, 7046 insertions(+), 2103 deletions(-)
```

### 1.3 时点声明：145 / 61 是**时点读数**，不是现状

- `app/**/*.py` 计 **145**、`tests/*.py` 计 **61**：**2026-10-26 17:49 +0800 的读数**（会话结束前复核）。比文档基线 144 / 59（`docs/spec/项目规格.md:3451`「上一轮（P11）的读数是 144 / 59 / 818」）多出的是 **1 + 2**（app **+1**、tests **+2**），均由外部进程本轮新增，见第 8 节。
- 同一时点的收集数 **842**、通过数 **831** 见第 6 节。
- **现状已变**（2026-10-26 19:30 +0800 实测）：`app/**/*.py` **146**、`tests/*.py` **65**、收集 **885**、通过 **879**、跳过 **6**（与 `README.md:375`、`docs/spec/项目规格.md:3451` 一致）。按 `openspec/AGENTS.md` §8.2 第 5 条，**历史读数按项目做法标日期保留，不静默改成现状值**；本文件因此在 §6 保留 842 / 145 / 61，并在此处单列现状。
- **并发写入仍在继续**：1.2 的快照是 19:30 抓取的；同一命令在 19:26 与 19:30 两次抓取之间，`git diff --stat` 已经出现差异（如 `README.md` 的改动行数、`app/channels/registry.py`、`web/src/pages/ProactiveRules.tsx`），说明**树在本证据成文期间仍被外部进程持续改写**。这正是本轮不能声称「运行前后一致」的客观依据（见 1.1 与第 8 节）。

### 1.4 仓内唯一新增路径：`.pytest_tmp/`

本轮命令用了 `--basetemp=.pytest_tmp`（第 6 节），在**仓库内**留下了 pytest 的临时目录。当前 `git status --short` 中它是 `?? .pytest_tmp/`，且 `grep -n "pytest_tmp" .gitignore` **零命中**（未进 `.gitignore`）。

- 因此本轮在**仓内**新增的路径**只有** `.pytest_tmp/` 这一个（pytest 副产物目录）；本文件与 `tasks.md` 位于**未跟踪的 `openspec/` 目录**内，同样计入未跟踪项。
- 复核命令（只读）：

```
$ git status --short | grep -n "pytest_tmp"
?? .pytest_tmp/
$ grep -n "pytest_tmp" .gitignore
(无输出，退出码 1)
```

- `.pytest_tmp/` **保留不动**：删掉会破坏「本轮仓内唯一新增路径」这一登记事实的可核对性；`.gitignore` 也**不在本轮改动范围**内（第 10 节登记为待办）。

## 2. 口径核对（以仓库实际声明为准）

| 项 | 仓库声明 | 出处 |
|---|---|---|
| PG 测试环境变量 | `TICKETPILOT_TEST_POSTGRES_URL`（逐字一致） | `tests/test_postgres_concurrency.py:21`、`tests/test_postgres_outbox.py:22` |
| 连接串方言 | `postgresql+psycopg://` | `.env.example:5`、`.github/workflows/ci.yml:69`、`docs/guide/黄金集说明.md:57-58` |
| alembic 调用形式 | 裸 `alembic`，URL 走 `-x db_url=` 或 settings | `Makefile:50`、`alembic/env.py:21`、`ci.yml:69-73`、`黄金集说明.md:57` |
| pytest 调用形式 | `python -m pytest -q` | `Makefile:13`（`README.md:375`「怎么验证这些说法」一节为 `python -m pytest`，**无 `-q`**（该文件正被外部进程并发改写，行号会漂移，以小节标题为准）） |
| Golden Set PG 复跑 | `python evals/run.py --database-url postgresql+psycopg://...`，独立库 | `docs/guide/黄金集说明.md:53-58` |
| PG 专项用例需显式启用 | 用例创建并删除临时数据库，不能指向共享库 | `docs/guide/黄金集说明.md:63`（「该用例会创建并删除临时数据库，需显式启用，避免误连业务库。」）、`docs/spec/项目规格.md:2873`（「需要 `TICKETPILOT_TEST_POSTGRES_URL` / `TICKETPILOT_TEST_REDIS_URL`。这些测试会临时创建数据库、跑完删除，不能指向共享库。」） |

解释器：`D:/Code/TicketPilot/.venv/Scripts/python.exe`（存在，Python 3.13.9），全程使用同一 venv。

## 3. 一次性 PG 容器

机器上已有 `my-agentix-neo4j-local`、`my-agentix-mysql-1` 等在跑，全程未触碰。任务示例端口 **15432 实际已被 `my-agentix-postgres-1` 占用**，改用空闲端口 15443（先经 `netstat` 确认可用）：

```
$ docker run -d --name tp-pg-t4 -p 127.0.0.1:15443:5432 -e POSTGRES_PASSWORD=devpw -e POSTGRES_DB=tp_test postgres:16-alpine
244cd9c536fe...
```

`docker logs tp-pg-t4` 出现 `LOG:  database system is ready to accept connections`（启动后 1 秒）。

版本实测：

```
$ docker exec tp-pg-t4 psql -U postgres -d tp_test -tAc "SELECT version()"
PostgreSQL 16.15 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
```

连接串：`postgresql+psycopg://postgres:devpw@127.0.0.1:15443/tp_test`

## 4. 迁移（alembic upgrade head，真实输出）

```
$ .venv/Scripts/alembic.exe -x db_url="postgresql+psycopg://postgres:devpw@127.0.0.1:15443/tp_test" upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 322b3f652b72, initial schema
INFO  [alembic.runtime.migration] Running upgrade 322b3f652b72 -> 7d3a9c1e5b42, order-level approval guard
INFO  [alembic.runtime.migration] Running upgrade 7d3a9c1e5b42 -> b41d7c9e2f30, conversation state
INFO  [alembic.runtime.migration] Running upgrade b41d7c9e2f30 -> c8e2f5a91d47, trace spans
INFO  [alembic.runtime.migration] Running upgrade c8e2f5a91d47 -> d5a91b73e204, task trace link
INFO  [alembic.runtime.migration] Running upgrade d5a91b73e204 -> e2f6a4d81c57, handoff and abandon events
INFO  [alembic.runtime.migration] Running upgrade e2f6a4d81c57 -> b7c4e19a2f60, sql query ledger
INFO  [alembic.runtime.migration] Running upgrade b7c4e19a2f60 -> c4f1b8d2e306, outbox messages
INFO  [alembic.runtime.migration] Running upgrade c4f1b8d2e306 -> f3a7c9d21b84, security events
INFO  [alembic.runtime.migration] Running upgrade f3a7c9d21b84 -> a9d4c2f7b135, channel messages
INFO  [alembic.runtime.migration] Running upgrade a9d4c2f7b135 -> c3b8e5a71d29, customer memory and proactive notifications
INFO  [alembic.runtime.migration] Running upgrade c3b8e5a71d29 -> d7a1f3c9b2e4, llm usage ledger
INFO  [alembic.runtime.migration] Running upgrade d7a1f3c9b2e4 -> b4e8d2f6a1c9, task idempotency key
INFO  [alembic.runtime.migration] Running upgrade b4e8d2f6a1c9 -> a1c7e9d4b820, proactive rule versions, changes and notification rule version
```

退出码 **0**。空库走完全部 **14 条迁移**，head `a1c7e9d4b820`。

## 5. alembic check（真实输出，原文）

```
$ .venv/Scripts/alembic.exe -x db_url="postgresql+psycopg://postgres:devpw@127.0.0.1:15443/tp_test" check
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.schemas
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.tables
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.types
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.constraints
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.comments
No new upgrade operations detected.
```

退出码 **0**。

## 6. 全量 pytest（带 TICKETPILOT_TEST_POSTGRES_URL）

命令（权威轮，2026-10-26 17:45:58 +0800 开始）：

```
TICKETPILOT_TEST_POSTGRES_URL="postgresql+psycopg://postgres:devpw@127.0.0.1:15443/tp_test" \
  .venv/Scripts/python.exe -m pytest -q -rs --basetemp=.pytest_tmp \
  --junitxml=C:/Users/15237/tp-t4-results.xml
```

### 时点声明：842 / 831 / 145 / 61 是**时点读数**，不是现状

- 本节全部读数取自 **2026-10-26 17:45:58 +0800 启动、17:48:50 结束**的那一轮（JUnit XML `timestamp` 属性 `2026-10-26T17:45:58.486334+08:00`，日志文件 mtime 17:48:52）。收集 **842**、通过 **831**、失败 **9**、跳过 **2**、耗时 **172.486 s**。
- 同一时点的文件计数是 `app/**/*.py` **145**、`tests/*.py` **61**（17:49 复核，见 1.3）。
- **现状已变**（2026-10-26 19:30 +0800 实测）：`app/**/*.py` **146**、`tests/*.py` **65**、收集 **885**、通过 **879**、跳过 **6**（与 `README.md:375`、`docs/spec/项目规格.md:3451` 一致）。按 `openspec/AGENTS.md` §8.2 第 5 条，**历史读数按日期保留，不静默改成现状值**；本节因此保留 842 / 831 / 145 / 61，现状另列于 1.3。

### 统计（权威口径：JUnit XML，本轮真实产物）

```
tests="842" failures="9" errors="0" skipped="2" time="172.486"
timestamp="2026-10-26T17:45:58.486334+08:00" hostname="Yihao"
```

即 **831 passed / 9 failed / 2 skipped**（842 收集，172.486 s，由 842−9−2 得出 passed）。

### 控制台末尾原文（如实登记）

本沙箱内 pytest 控制台输出在末行统计（`=== N passed ... ===`）之前被截断，退出码真实但末行统计不可见。控制台实际看到的末尾为：

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
------------ generated xml file: C:\Users\15237\tp-t4-results.xml -------------
=========================== short test summary info ===========================
SKIPPED [1] tests\test_rate_limit_redis.py:60: set TICKETPILOT_TEST_REDIS_URL to run the shared-budget test
SKIPPED [1] tests\test_rate_limit_redis.py:75: set TICKETPILOT_TEST_REDIS_URL to run the shared-budget test
```

### 仓外落盘产物（**不入库，归档后不可复看**）

本轮读数有两份**仓库外**落盘产物。它们是本轮唯一的原始读数载体，**不随本 change 目录归档**，归档后**不可复看**——本文件第 6 节与第 13 节的所有数字都以它们为唯一来源。

| 产物 | 绝对路径 | 字节数 | mtime | 生成方式 |
|---|---|---|---|---|
| JUnit XML | `C:\Users\15237\tp-t4-results.xml` | **125044** | **2026-10-26 17:48:50.981466700 +0800** | 同一轮 pytest 的 `--junitxml=C:/Users/15237/tp-t4-results.xml` 参数 |
| 完整控制台日志 | `C:\Users\15237\tp-pg-t4-pytest-final.log` | **15323** | **2026-10-26 17:48:52.285551100 +0800** | 同一轮 pytest 的 stdout/stderr 重定向 |

复核命令与输出（只读，2026-10-26 19:2x 复跑，数值与上表一致）：

```
$ stat -c '%n | bytes=%s | mtime=%y' /c/Users/15237/tp-t4-results.xml
/c/Users/15237/tp-t4-results.xml | bytes=125044 | mtime=2026-10-26 17:48:50.981466700 +0800

$ stat -c '%n | bytes=%s | mtime=%y' /c/Users/15237/tp-pg-t4-pytest-final.log
/c/Users/15237/tp-pg-t4-pytest-final.log | bytes=15323 | mtime=2026-10-26 17:48:52.285551100 +0800
```

- **两份产物都在仓库外**（`C:\Users\15237\` 下，不在 `D:\Code\TicketPilot` 内），因此 `git status` **看不到**它们；它们**没有**进 `.gitignore`（不在仓库内，本就无需 ignore），也**没有**被复制进本 change 目录。
- 结论：**本证据的原始读数不可随仓库复现**。若需要长期可复看，须由后续证据任务把产物落进仓内已被 `.gitignore` 覆盖的路径（`openspec/AGENTS.md` §8.1 第 1 行）。本轮**不**补（补它等于新增产物，超出本轮边界）。
- XML 的 `<testsuite>` 头部原文：

```
<testsuite name="pytest" errors="0" failures="9" skipped="2" tests="842" time="172.486" timestamp="2026-10-26T17:45:58.486334+08:00" hostname="Yihao">
```

### 本轮之前的两次运行（如实登记）

- 第 1 轮（17:33 前后）：`python -m pytest -q -rs --basetemp=.pytest_tmp`，退出码 **0**，2 skipped。当时外部进程尚未向仓库新增文件（见第 8 节），全绿。统计末行同样被沙箱截断，**未留下数字读数**。该轮**无落盘产物**（既无 JUnit XML 也无控制台日志留存），因此除退出码与 skip 数外**无可核事实**。
- 第 3 轮（17:39 前后）：同一命令，退出码 **1**，仅 `tests/test_docs_consistency.py::test_documented_numbers_match_the_code` 失败（守卫实时清点发现文档数字 144/59/818 与外部新增文件后的实际 **145/61/842** 不符——该三元组与第 13 节更正后的口径一致，`61` 而非 `60`）。失败原因是外部并发改树，与 PG 路径无关。该轮同样**无落盘产物**，仅据执行者记录。**第 3 轮的时刻为近似值**（分钟级记录），与 §8.1 记录的首次出现时刻可能互相覆盖。

### PG 专项用例真实执行（本任务核心增量证据）

以下 4 条在真实 PostgreSQL 16.15 上**执行并通过**（不再是 skip），逐条取自本轮 XML：

| 用例 | 结果 | 用时 |
|---|---|---|
| `tests/test_postgres_concurrency.py::test_concurrent_approvals_produce_exactly_one_refund` | PASS | 1.668 s |
| `tests/test_postgres_concurrency.py::test_concurrent_task_creation_with_one_key_returns_one_task` | PASS | 0.734 s |
| `tests/test_postgres_outbox.py::test_only_one_worker_claims_a_message` | PASS | 0.788 s |
| `tests/test_postgres_outbox.py::test_the_worker_settles_a_refund_on_postgres` | PASS | 0.848 s |

（注：`docs/下一步规划.md:681` 写"outbox 1 条"，本轮实测 `test_postgres_outbox.py` 收集 2 条，均为 PG 真实执行。文档计数过期，属既有文档债，本轮按禁令不修。）

### 失败清单（9 条，只登记，未修复）

| # | 用例 | 报错摘要 | 疑似原因 |
|---|---|---|---|
| 1 | `tests/test_docs_consistency.py::test_documented_numbers_match_the_code` | AssertionError：文档写 144/59/818，实际清点 145/61/842 | 外部进程本轮向 `app/`、`tests/` 新增 4 个文件（第 8 节），文档数字守卫按设计报警；与 PG 无关 |
| 2 | `tests/test_intent_candidate_model.py::test_an_in_candidate_reading_is_used_exactly_as_before` | AttributeError: 'ModelTrace' object has no attribute 'reason' | 外部进程本轮新建的测试文件（17:42 出现），对应产品代码未实现该属性；TDD 红灯，与 PG 无关 |
| 3 | `...::test_a_candidate_the_adoption_surface_does_not_cover_is_still_not_adopted` | 同上 AttributeError | 同上 |
| 4 | `...::test_an_out_of_candidate_answer_falls_back_without_a_second_request` | assert 'rules_fallback' == 'pydantic_ai' | 同上 |
| 5 | `...::test_none_is_an_answer_not_a_failure` | assert 0 == 1 | 同上 |
| 6 | `...::test_the_server_bucket_never_reads_the_models_own_number` | assert False | 同上 |
| 7 | `...::test_the_prompt_carries_the_candidates_and_nothing_else_from_the_catalog` | AttributeError: 'ModelTrace' … 'reason' | 同上 |
| 8 | `...::test_the_switch_off_offers_no_candidates_and_still_adopts_a_legal_literal` | TypeError: IntentBrain.__init__() got an unexpected keyword argument 'candidates_enabled' | 同上 |
| 9 | `...::test_the_switch_off_still_refuses_an_intent_outside_the_old_literals` | ValidationError: 1 validation error for IntentOutput | 同上 |

**排除外部噪音后，与 PG 相关及存量用例的读数为 831 passed / 0 failed / 2 skipped**。9 条失败的构成经 XML 逐条核对（`<testcase>` + `<failure>` 配对，共 9 组）：

- **1 条**是 `tests/test_docs_consistency.py::test_documented_numbers_match_the_code`——文档数字守卫**按设计**报警（外部进程新增文件使实际清点变成 145/61/842，而文档仍写 144/59/818）。
- **8 条**全部来自 `tests/test_intent_candidate_model.py`（P8 未跟踪的 WIP 测试文件）：这些用例期望的 `ModelTrace.reason`、`IntentBrain(..., candidates_enabled=...)` 等接口**在 `app/agent/candidates.py` 与 `app/agent/pydantic_agent.py` 里尚未落地**，属「测试先行、实现未落地」。
- 归因结论：**9 条失败只由本轮外部新增的 `app/agent/candidates.py` 与两个 P8 测试文件（`tests/test_intent_candidates.py`、`tests/test_intent_candidate_model.py`）引起**，与 PostgreSQL 路径无关。逐条扫描 9 个 `<failure>` 元素：**无一条**含 `psycopg`、`OperationalError`、`ProgrammingError`、`UndefinedColumn`、`IndexError`、`pg_indexes`、`indexdef` 等 PG / psycopg / 索引症状；唯一出现的 `postgres` 子串在第 1 条失败里，属于文档守卫**引用的文档正文**（`docs/下一步规划.md:83` 等行中提到 "Postgres / Redis 专项"），**不是运行时症状**。

### 本轮被跳过的用例（全部 2 条，逐项）

| 用例 | 跳过原因 |
|---|---|
| `tests/test_rate_limit_redis.py::test_two_app_instances_share_one_tenant_budget` | 缺环境变量 `TICKETPILOT_TEST_REDIS_URL`（Redis 专项，不在本任务 PG 范围内；机器上虽运行着 `ticketpilot-redis`(127.0.0.1:56379)，本任务未授权接 Redis，故未设置） |
| `tests/test_rate_limit_redis.py::test_two_stores_share_one_counter` | 同上 |

没有因"标记 integration/e2e"或"缺 PG 环境变量"而跳过的用例——PG 专项 4 条本轮全部真实执行。

## 7. Golden Set 在 PG 上复跑

按 `docs/guide/黄金集说明.md:53-58` 口径，用独立库避免污染：

```
$ docker exec tp-pg-t4 createdb -U postgres tp_eval
$ .venv/Scripts/alembic.exe -x db_url="postgresql+psycopg://postgres:devpw@127.0.0.1:15443/tp_eval" upgrade head   # 14 条走完，退出码 0
$ .venv/Scripts/python.exe evals/run.py --database-url "postgresql+psycopg://postgres:devpw@127.0.0.1:15443/tp_eval"
```

统计行原文（本轮真实输出，退出码 0）：

```
40/40 golden cases passed
```

## 8. 外部并发写入（如实登记，未干预）

**权威跑批窗口**：2026-10-26 **17:45:58 +0800**（JUnit XML `timestamp` 属性）至 **17:48:50**（XML 文件 mtime）/ **17:48:52**（控制台日志 mtime）。

### 8.1 本轮窗口内出现的未跟踪文件（P8 变更 `add-intent-candidate-shortlisting-2026-10`）

executor **未**创建、未修改、未删除下列文件（它们是外部进程写入的未跟踪路径）：

| 路径 | 执行者当时记录的首次出现时刻 | 当前实测 mtime | 说明 |
|---|---|---|---|
| `app/agent/candidates.py` | 17:38 | 2026-10-26 17:48:54 +0800 | P8 候选内核（实现） |
| `tests/test_intent_candidates.py` | 17:41 | 2026-10-26 17:49:30 +0800 | P8 候选内核测试 |
| `tests/test_intent_candidate_model.py` | 17:42 | **2026-10-26 18:34:46 +0800** | P8 模型面测试（9 条失败中的 8 条来自它） |

- **当前 mtime 不能证明「首次出现时刻」**：三者都被外部进程在跑批之后再次改写过（mtime 已被覆盖），因此「17:38 / 17:41 / 17:42」是**执行者记录**，不是可核事实。
- **可核的部分**：JUnit XML 的 `classname` 去重后同时含 `tests.test_intent_candidates` 与 `tests.test_intent_candidate_model`，说明这两个测试文件在**权威轮确实被收集并执行**了；`tests.test_intent_candidate_model` 的 8 条 `<failure>` 即第 6 节失败清单的第 2–9 条。

### 8.2 窗口内被外部进程改写的 **tracked** 文件（清单 + 时刻）

| tracked 文件 | 实测 mtime | 是否落在权威跑批窗口（17:45:58–17:48:50）内 |
|---|---|---|
| `app/core/config.py` | 2026-10-26 17:41:56 +0800 | 否（窗口之前） |
| `.env.example` | 2026-10-26 17:42:18 +0800 | 否（窗口之前） |
| `tests/test_llm_gap.py` | 2026-10-26 17:47:55 +0800 | **是** |
| `tests/test_model_distribution.py` | 2026-10-26 17:47:55 +0800 | **是** |
| `tests/test_security_model_cross.py` | 2026-10-26 17:47:55 +0800 | **是** |
| `app/main.py` | 2026-10-26 17:50:28 +0800 | 否（窗口之后） |
| `tests/test_pydantic_agent.py` | 2026-10-26 17:50:54 +0800 | 否（窗口之后） |

- **读数解释边界（重要）**：`tests/test_llm_gap.py`、`tests/test_model_distribution.py`、`tests/test_security_model_cross.py` 三个文件在**跑批进行中**（17:47:55）被改写。因此本轮读数对这三条文件的解释力**弱于**其余存量用例：pytest 在 17:45:58 收集用例，收集到的是**改写前**的内容，而执行结果可能跨过了改写点。它们的失败与否**不能**单独作为「该文件功能是否正确」的判据。
- 其余四个文件（`app/core/config.py`、`.env.example`、`app/main.py`、`tests/test_pydantic_agent.py`）分别落在窗口**之前**（17:41–17:42）与**之后**（17:50），不影响本轮读数的采集内容。
- 该清单**只能反映「现在这些文件是什么时刻被写过」**：mtime 会被后续写入覆盖，因此它给出的是**最后一次写入时刻**，不是「在窗口内是否被改写过」的直接证据；「三个测试文件在跑批窗口内被改写」这一判断依赖 mtime 落在窗口区间这一事实。

### 8.3 与 9 条失败的关系

第 6 节 9 条失败的构成（经 XML 逐条核对）：

- **1 条** `tests/test_docs_consistency.py::test_documented_numbers_match_the_code`——文档数字守卫按设计报警，**由外部新增文件导致计数变化**（文档写 144/59/818，实际 145/61/842）。
- **8 条**全部来自 `tests/test_intent_candidate_model.py`——P8 的「WIP 测试先行、实现未落地」：用例期望的 `ModelTrace.reason`、`IntentBrain(..., candidates_enabled=...)` 等接口在 `app/agent/candidates.py` 与 `app/agent/pydantic_agent.py` 里尚未实现。
- **归因**：9 条失败**只由 `app/agent/candidates.py` 与两个 P8 测试文件（`tests/test_intent_candidates.py`、`tests/test_intent_candidate_model.py`）引起**，**无一含 PG / psycopg / 索引症状**（第 6 节有逐条扫描结论）。

### 8.4 不属于本轮噪音的文件（原清单更正）

- `evals/clarification_candidates.py` **不是**本轮运行期间新增的文件。它实测 mtime 为 **2026-10-26 15:59:45 +0800**，**早于本轮约 1.5 小时**（本轮窗口 17:33–18:00）；它属于**已归档**的 P11 变更 `openspec/changes/archive/2026-10-26-add-clarification-candidates-2026-10`（归档目录实测存在），**与 P8 的 `add-intent-candidate-shortlisting-2026-10` 无关**。
- 它是 **eval 脚本、不是测试**，**与 9 条失败无关**（9 条失败全部落在 `tests/test_docs_consistency.py` 与 `tests/test_intent_candidate_model.py` 两个测试文件上）。
- 更正说明：mtime 被后续写入覆盖只能解释「**更晚**」的时间戳，**不能**解释一个**更早**的 mtime。因此本条更正不适用「mtime 会被后续写入覆盖」那套解释——原清单把它列为「运行期间新增」是错的，已按实测改述。

### 8.5 结论

这是本轮唯一的环境噪音来源；如需干净全绿读数，应在**无并发写入**的树状态下复跑（复验标准见 `tasks.md`）。本轮读数对**被并发改写过的文件**的解释力见 8.2。

## 9. 覆盖缺口（本轮证据的边界）

按 `tests/conftest.py:28`（「"database_url": f"sqlite+pysqlite:///{tmp_path / f'test-{index}.db'}"」），除 PG 专项 4 条外，其余用例按仓库既有设计跑临时 SQLite（`sqlite+pysqlite:///{tmp_path}/...`）。因此：

- **本轮拿到 PG 真实读数**：4 条 PG 专项并发/outbox 用例 + Golden Set 40 条执行器路径（tp_eval 库）。
- **现状口径是 366，不是 347**：仓库现状口径写在 `README.md:178`——SQLite-only 的新增用例累计 **366** 条，推导式为 `885 − 519 = 366`（885 是当前收集数，519 是 2026-10-25 那轮 PG 全量的通过数）。**347** 是另一个身份：`docs/spec/项目规格.md:26` 列的 9 个来源子计（查询改写 68 + 分层检索集 13 + 意图语料评测 25 + P2/P3/P6 19 + 预取式统计 102 + P4 规则治理 13 + P5 意图缺口闭环 31 + P11 澄清候选降级 28 + P8 意图候选化 48 = **347**），它是 **866 收集时代**的读数（`519 + 347 = 866`）；与现状口径 366 相差 **19** 条，差额来自**渠道可用性轮（F-6，`openspec/changes/add-channel-availability-reason`）**补的 19 条。**无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例**：除上面这 4 条 PG 专项外，本轮新增/变更的用例**仍然**走 `tests/conftest.py:28` 的临时 SQLite，**没有**在 PG 上复跑。不可把本轮读数读成「347 条已在 PG 复跑」，也不可读成「366 条已在 PG 复跑」。
  - **下界限定**：366 是按「519 那轮已覆盖 519 条」算出来的**下界**——2026-10-25 那轮之后 `tests/**` 有过改写（`tests/test_llm_gap.py`、`tests/test_model_distribution.py`、`tests/test_security_model_cross.py`、`tests/test_pydantic_agent.py`，实测 mtime 见本文件 §8.2），这些文件里的用例在 519 那轮是**旧内容**的读数，所以「当前内容已有 PG 证据」的用例数只会**少于** 519，缺口只会**大于** 366。**该清单不是全集**：它只列 §8.2 表里登记的这 4 个（`tests/test_llm_gap.py`、`tests/test_model_distribution.py`、`tests/test_security_model_cross.py` 落在权威跑批窗口内，`tests/test_pydantic_agent.py` 落在窗口之后）；以 2026-10-25 那轮 PG 全量的跑批时刻（约 15:07–15:09；实测 `.tmp/final_pytest_20261025.txt` 的 mtime 为 2026-10-25 15:09:27）为界，另有 **7** 个 tracked 测试文件在那轮之后、本轮窗口之前被改写：`tests/test_retrieval_channels.py`（mtime 2026-10-25 19:30）、`tests/test_llm_usage.py`（2026-10-25 22:28）、`tests/test_sql_analytics.py`（2026-10-25 23:01）、`tests/test_sql_guard.py`（2026-10-26 09:08）、`tests/test_proactive_rules.py`（2026-10-26 13:21）、`tests/test_policy_engine.py`（2026-10-26 15:18）、`tests/test_handoff.py`（2026-10-26 15:19）——它们的用例在 519 那轮同样是**旧内容**的读数，同样计入下界，**缺口只会大于 366，不会被高估**；本轮不逐条统计精确值，登记为待办（§10.1）。**该轮 PG 证据不撤销**。
- **本轮跑批的落库分配**（本轮跑批的事实）：842 条收集里，**4** 条 PG 专项跑在 PostgreSQL（真实 PG 读数），**838** 条跑在临时 SQLite（`tests/conftest.py:28`）。**838 是「本轮跑在 SQLite 上的用例数」**，它**不是**「从无 PG 证据的用例数」——后者见下一个 bullet。
- **无 PG 证据的用例数**：当轮 **842** 收集口径下约 **323** 条（= 842 − 519）；当前 **885** 收集口径下 **366** 条（= 885 − 519）。**519** 来自 2026-10-25 那轮 PG 全量（`docs/spec/项目规格.md:24`、`docs/spec/项目规格.md:3634-3640`：同时指上 PostgreSQL 与 Redis 两个容器，**519 passed / 0 skipped**，137.23 s），那 519 条**是有 PG 证据的**；**该轮证据仍然有效，本文件不撤销它**。其中与 PG 行为直接相关、**本轮未在 PG 上复跑**的是 `app/sql/` 方言分支与部分唯一索引的 `postgresql_where` 变体（`app/models/approval.py:23,32`；其中 `:32` 的 `uq_approvals_open_per_resource` 已被 PG 专项用例用 `pg_indexes` 直查 `indexdef` 断言覆盖，`:23` 的 `uq_approvals_one_pending_per_task` 仅经「重复申请被挡」用例间接行使，尚未有 `indexdef` 直查断言）。**「已被 2026-10-25 那轮 PG 覆盖」这一事实按文件分述**（以那轮跑批时刻 2026-10-25 15:09 为界，实测 mtime）：
  - `app/sql/builder.py:34`（`"postgresql": {"month": "YYYY-MM", "day": "YYYY-MM-DD"}` 日期格式）与 `app/sql/builder.py:39`（`"postgresql": "TO_CHAR"` 函数映射）：实测 mtime **2026-10-25 11:09:58**，**早于**那轮跑批时刻，**当前内容确已被那一轮覆盖**；本轮未在 PG 上复跑，变更后需回归。
  - `app/sql/service.py:400`（`if dialect == "postgresql":`）与 `app/sql/prefetch.py:284`（`if dialect == "postgresql":`）：实测 mtime 分别为 **2026-10-25 23:26:35** 与 **2026-10-25 23:11:28**，**晚于**那轮跑批时刻——**那轮跑批之后被改写，当前内容从未在 PG 上跑过**，需回归。
  - **三个文件均需回归**，但「已被那轮覆盖」只对 `builder.py` 成立，对 `service.py` / `prefetch.py` 不成立。
- `docs/guide/评测说明.md:102` 提到的 `evals/retrieval_metrics.py --database-url` PG 复跑**本轮未执行**（任务步骤未要求），登记为遗留项。

## 10. 待办（本任务不修改，仅登记）

### 10.1 既有待办（沿用）

1. `.pytest_tmp` **不在 `.gitignore`**（`grep -n "pytest_tmp" .gitignore` 零命中）。建议仓库维护者补一行，避免 `--basetemp` 目录污染 `git status`。
2. `docs/下一步规划.md:681` 写「`test_postgres_outbox.py` 1 条」，本轮实测该文件收集 **2** 条且均为 PG 真实执行（计数过期）。
3. 外部 `add-intent-candidate-shortlisting-2026-10` 变更落地后，需复跑文档数字守卫（144/59/818 基线已过期；当前磁盘基线见 `docs/spec/项目规格.md:3451`）。
4. Redis 2 条 skip 的补证需 `TICKETPILOT_TEST_REDIS_URL`，超出本任务授权范围。
5. 本轮的两份读数产物在**仓库外**，归档后不可复看（第 6 节）。若要让证据长期可复看，须由后续证据任务把产物落进仓内已被 `.gitignore` 覆盖的路径。
6. **「当前内容已有 PG 证据」的用例数未逐条统计**（第 9 节的下界限定）：2026-10-25 那轮之后 `tests/**` 有过改写（`tests/test_llm_gap.py`、`tests/test_model_distribution.py`、`tests/test_security_model_cross.py`、`tests/test_pydantic_agent.py`，实测 mtime 见 §8.2），那 519 条只是**上界**，精确值待后续证据轮统计。**该清单不是全集**：它只列 §8.2 表里登记的这 4 个；以 2026-10-25 那轮 PG 全量的跑批时刻（约 15:07–15:09；实测 `.tmp/final_pytest_20261025.txt` 的 mtime 为 2026-10-25 15:09:27）为界，另有 **7** 个 tracked 测试文件在那轮之后、本轮窗口之前被改写：`tests/test_retrieval_channels.py`（mtime 2026-10-25 19:30）、`tests/test_llm_usage.py`（2026-10-25 22:28）、`tests/test_sql_analytics.py`（2026-10-25 23:01）、`tests/test_sql_guard.py`（2026-10-26 09:08）、`tests/test_proactive_rules.py`（2026-10-26 13:21）、`tests/test_policy_engine.py`（2026-10-26 15:18）、`tests/test_handoff.py`（2026-10-26 15:19）——它们同样使 519 只是**上界**，缺口只会**大于** 366，不会被高估。

### 10.2 文档同步待办（本轮 PG 读数已推翻既有文档口径）

本轮证据与下列既有文档**冲突**。本轮红线禁止改 `docs/**`，因此**只登记、点名 `文件:行` 与需要改成的方向，不写具体新数字**（避免二次臆造）：

**两套计数的口径（本表统一使用，不得混用）**：「**落库分配**」指本轮跑批里哪些用例跑在 SQLite 上（842 收集中 4 条 PG 专项跑在 PostgreSQL、**838** 条跑在临时 SQLite，`tests/conftest.py:28`）；「**无 PG 证据的用例数**」指当前内容从未在 PG 上跑过的用例数（当轮 842 收集口径约 **323**、当前 885 收集口径 **366**，**均为下界**）。两套计数的推导式、下界限定与「无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例」的表述见第 9 节。

| 冲突位置（`文件:行`） | 现有口径（要点） | 需要改成的方向 |
|---|---|---|
| `docs/spec/项目规格.md:24-31` | 「最近一次全量复跑（**519 条时代**）… 519 passed / 0 skipped」；「第 14 条 `a1c7e9d4b820` … **没有在那次手工全量里跑过**」；同段还有「新增 347 条只有 SQLite 证据」 | 把「最近一次全量复跑」指向 2026-10-26 那一轮（14 条迁移 head 走完、PG 专项真实执行、Redis 专项仍 skip 的事实分别写清）；把「第 14 条没在那次手工全量里跑过」改为「已在 2026-10-26 的手工全量轮里跑过」；「347 条只有 SQLite 证据」那半句按本轮实际口径改写（见 `docs/spec/项目规格.md:26` 的现文与本文件 §9） |
| `docs/spec/项目规格.md:3634-3640` | 2026-10-25 轮记录：**519 passed / 0 skipped**、**13** 条迁移（head `b4e8d2f6a1c9`） | **保留**该条为历史读数（按项目做法标日期不删不改），**另起一条 2026-10-26 轮**的新记录（迁移条数与 head、PG 专项执行情况、跳过项、Golden 读数、噪音与归因）；不要用新读数覆盖历史条目 |
| `docs/下一步规划.md:83` | 门禁表同一句：「**PostgreSQL 16.15 最近一次全量（519 条时代）**…」「**新增的 347 条只有 SQLite 证据**」 | 门禁表该行改为指向 2026-10-26 那一轮；「347 条只有 SQLite 证据」那半句按本轮实际口径改写（**除 PG 专项外仍走 SQLite**，不可写成「已在 PG 复跑」，见本文件 §9） |
| `docs/下一步规划.md:676-681` | 2026-10-25 的 PG 全量记录：**458 passed**、**11** 条迁移（head `c3b8e5a71d29`）、「`test_postgres_outbox.py` **1** 条」 | **保留**该条为历史记录，另起 2026-10-26 的条目；`outbox` 的「1 条」按本轮实测（该文件收集 2 条、均 PG 真实执行）修正 |
| `docs/records/智能体平台设想.md:164` | 「该读数停在 **519 条时代**，其后新增的 **347** 条尚未在 PG 上复跑」 | 改为：本轮已在 PG 上复跑，但**范围仅 PG 专项与 Golden 路径**，其余存量用例仍只有 SQLite 证据；不要写成「全库已在 PG 上可用」（见本文件 §13 末尾） |
| `docs/guide/黄金集说明.md:61` | 「2026-10-25 实测 SQLite **40/40**、PostgreSQL 16.15 **40/40**（同一轮全量 pytest 519 passed / 0 skipped，两个容器同时指上）」 | 补一条 2026-10-26 的 Golden Set PG 复跑记录（独立库 `tp_eval`、迁移走完、执行器路径 40/40）；**须注明本轮该读数无仓外留存产物**（第 6 节），证据强度与 2026-10-25 条目不同 |
| `docs/guide/证据索引.md:907`（§四「运行读数：一轮流量与端到端延迟」标题行，其下 `:915` 为「2026-10-26 复跑（当前读数）」小节；该文件正被外部进程并发改写，行号会漂移，以小节标题为准） | 证据总账里**没有**本轮条目 | 补一条本轮条目（2026-10-26 PG 全量轮：迁移 head、PG 专项 4 条、跳过项、9 条失败的归因与噪音边界、仓外产物路径与「不入库」声明） |
| `README.md:178-180` | 「SQLite-only 的新增用例已经累计到 **366 条**（PG 那次全量停在 519 条时代，885 − 519 = 366；上一轮记的 347 条是 866 收集时代的读数……）」 | **这一处是现状口径本身，不是待改项**；登记在此是为了点明 T-4 文件采用的 366 与它同源，并给出 T-4 的下界限定（见 §9） |
| `docs/spec/项目规格.md:2875` | 「包含 T2 起新增的文件与四张新表；**其后新增的 347 条**（查询改写、分层检索集、意图语料评测、P2/P3/P6、预取式统计、P4 规则治理、P5 意图缺口闭环、P11 澄清候选降级与 P8 意图候选化）尚未在 PG 上复跑」 | 与 `README.md:178` 的推导对齐，或明确标注为「9 个命名来源的子计」并写明与现状口径的 19 条差额来源 |
| `docs/下一步规划.md:5` | 「879 条通过用例全部在位（收集 885；……该读数停在 519 条时代，**其后新增的 347 条**尚未在 PG 上复跑）」 | 同上 |
| `docs/下一步规划.md:1107` | 「**仍然没有的**：新增 28 条与其时其余 299 条（P8 之后总数 347 条）一样只有 SQLite 证据」 | 同上；注意这句里的 299 / 28 / 347 是**当时**的子计，改写时要保留它自己的历史语境 |
| `docs/guide/范围与边界.md:98` | 同一句「新增 … 347 条目前只有 SQLite 证据，尚未在 PG 上复跑」 | 同上 |
| `docs/records/智能体平台设想.md:856` | 门禁表同一句：「新增的查询改写、分层检索集、意图语料评测、意图缺口闭环、P2/P3/P6、预取式统计、P4 规则治理、P11 澄清候选与 P8 意图候选化用例共 **347** 条只有 SQLite 证据」 | 同上 |
| `CONTRIBUTING.md:70` | 同一句「……与 P8 意图候选化新增的 **347** 条目前只有 SQLite 证据，尚未在 PG 上复跑」 | 同上 |

上表共 **14** 行（原 7 行 + 第 3 轮追加的 7 行），其中含「347」字样的共 **10** 行（`README.md:178-180` 那一行的引文里也含「347」）。**待对齐的文档位置是 9 处**：`docs/spec/项目规格.md:26` / `:2875`、`docs/下一步规划.md:5` / `:83` / `:1107`、`docs/guide/范围与边界.md:98`、`docs/records/智能体平台设想.md:164` / `:856`、`CONTRIBUTING.md:70`——**不含** `README.md:178-179`（README 那一行是**现状口径 366 本身**，「347」只出现在它的引文里，不是待改项）。这 9 处与 `README.md:178-180` 的 **366** 现状口径的对齐，**由文档收口轮执行**（T-4 的红线禁止改 `docs/**`、`README.md`、`CONTRIBUTING.md`），T-4 **本轮只登记、不改**。

### 10.3 归档形态待办（`openspec/AGENTS.md` §1 / §2）

- 本 change 目录 `openspec/changes/verify-postgres-full-suite-evidence/` 目前只有 `evidence.md` 与 `tasks.md`，**缺 `proposal.md` / `plan.md` / `specs/`**。
- 按 `openspec/AGENTS.md` §1 的 change 目录形态，证据型变更属 §2 规模分级里的 **tiny**（单文件、无契约变化、无迁移），tiny 本身「不需要 change 目录」——但本轮已经建了目录并按 normal 的目录形态被审。
- 处置（**本轮不做**，只登记）：归档前二选一——① 补齐 `proposal.md` 与 `plan.md`；② 在归档说明里**显式记「证据轮、归档形态豁免」**，由 organizer/reviewer 裁定。
- 本轮**不**真的去补 `proposal.md` / `plan.md`（会扩大本轮改动范围），也**不**改 `openspec/AGENTS.md`。
- 运行前逐字快照缺失一事已在 §1.1 登记，归档说明中一并写明。

## 11. 自检结论与回滚

- 自检：验收标准逐条见 `tasks.md`；未 git commit；未触碰 `my-agentix-*` 容器；失败只登记未修复；本文件所有读数取自本轮真实命令输出或第 6 节登记的两份仓外产物。
- **「未改任何既有文件」是执行者自述，不是已核事实**：会话开始前工作树已是脏树（71 M + 47 ??），本轮与外部进程并发写同一棵树，**当前 `git status` 无法独立复核本轮是否改动过既有文件**（第 1 节 1.1 的缺陷登记、`openspec/AGENTS.md` §8.2 第 3 条）。
- 已知限制：
  1. 控制台末行统计在沙箱下丢失，以 JUnit XML 为权威；
  2. 外部并发写入造成 9 条与本任务无关的失败，且其中 3 个测试文件在跑批窗口内被改写（第 8 节 8.2），读数对它们的解释力受限；
  3. 两份原始读数产物在**仓库外**，**不入库、归档后不可复看**（第 6 节）；
  4. 运行前的树状态只留了计数与摘录，**没有逐字快照**（第 1 节 1.1）。
- 回滚方式：删除本目录两个新建文件（`evidence.md`、`tasks.md`）+ `docker rm -f tp-pg-t4`（已于第 12 节执行清理）。**仓内另有残留**：`.pytest_tmp/`（pytest `--basetemp` 副产物，第 1 节 1.4）**未删**，它不在 `.gitignore` 里，会出现在 `git status` 中；删除它即完全回滚，但**本轮刻意保留**，以维持「仓内唯一新增路径」这一登记事实的可核对性。

## 12. 容器清理（真实输出）

```
$ docker rm -f tp-pg-t4
tp-pg-t4

$ docker ps -a --format '{{.Names}}' | grep -c "tp-pg-t4"
0

$ docker ps --format '{{.Names}}' | grep my-agentix
my-agentix-neo4j-local
my-agentix-mysql-1
my-agentix-redis-1
my-agentix-postgres-1
my-agentix-reranker-1
```

残留容器 0 个；两个 `my-agentix` 目标容器（neo4j、mysql）及其余既有容器全部仍在运行。

## 13. 验收记录与修订

### 13.1 第 1 轮（reviewer 判定通过，附 4 项一般项）

核对方式：读 `tp-t4-results.xml`、逐条比对失败/跳过用例名与计数、抽查 §9 四处方言指认的 `文件:行`、读 `tests/conftest.py:28` 与 `test_rate_limit_redis.py` 门控、核对 `git status` 与容器状态。未复跑测试。

- 一般项处置：
  1. §6 表 #1 的 `145/60/842` 系誊抄误差，已按 XML 实测改为 `145/61/842`。
     **第 2 轮更正该处置的「理由」**（数字不变）：原写的理由「XML 全文含 `61` 九处、`60` 零处」是**子串计数**的误用——`60` 在 XML 里作为子串实际出现 **22** 处（出现在中文转义串与耗时里，如 `time="0.600"`、`\u5360\u6bd4`），因此该理由**不成立**。现改为**可复算**的方法：XML 的 `<testcase classname=...>` 去重后为 **59** 个测试模块，恰等于 17:49 时 `tests/*.py` 计 **61** 减去 `__init__.py` 与 `conftest.py` 两个非用例文件；`842` 来自 XML 的 `tests="842"` 属性；`145` 是 17:49 的 `app/**/*.py` 文件计数（文件系统读数，非 XML 读数）。
  2. Golden Set `40/40` 无仓库外留存产物：登记为后续证据任务的改进项（eval 控制台输出应落盘），本轮不补。
  3. **第 2 轮更正该处置的口径**：原写「§8 外部文件时间戳与实测 mtime 的轻微矛盾：mtime 会被后续写入覆盖，事实本身已由主会话独立核实，不改写」。该解释**只适用于** mtime 可能被**后写覆盖**的那几个文件，**不适用于** `evals/clarification_candidates.py`——它的实测 mtime（2026-10-26 15:59:45）**早于**本轮窗口约 1.5 小时，后写覆盖只能让 mtime **变晚**，解释不了一个变早的时间戳。原清单把它列为「运行期间新增」是**错的**，已在 §8.4 按实测改述为「早于本轮已存在、属已归档的 P11 变更、与 9 条失败无关」。
  4. §9 关于 `approval.py:23` 覆盖精度的表述已收紧，明确只有 `:32` 有 `pg_indexes` 的 `indexdef` 直查断言，`:23` 属间接行使。

### 13.2 第 2 轮（reviewer 退回，本轮返工）

退回项与本轮落点（逐条对应）：

| 退回项 | 级别 | 本轮落点 | 改后原文（摘） |
|---|---|---|---|
| 范围声明不可独立复核；与仓库现状冲突 | 严重 | 头部「范围声明」条 + §1 标题 + §1.1 | 「**本轮自述，不是已核事实**…**当前的 `git status` 无法独立复核本轮是否改动过既有文件**」；§1 标题改为「树状态（自述；运行前后**非逐字一致**…）」 |
| 「唯一新增产物」字面为假 | 严重 | 头部范围声明 + §1.4 | 改为两个**受控**产物（`evidence.md` / `tasks.md`），并把 `.pytest_tmp/` 登记为**仓内唯一新增路径** |
| §8 未登记 tracked 文件被并发改写 | 严重 | §8.2（新增表） | 列出 7 个 tracked 文件与实测 mtime，并标出 `tests/test_llm_gap.py` / `tests/test_model_distribution.py` / `tests/test_security_model_cross.py`（17:47:55）**落在权威跑批窗口内** |
| 仓外产物未登记校验信息 | 严重 | §6「仓外落盘产物」表 | 路径 + 字节数（125044 / 15323）+ mtime（17:48:50.981466700 / 17:48:52.285551100）+「**不入库，归档后不可复看**」 |
| 既有文档 PG 口径已被推翻但未登记 | 严重 | §10.2（新增表） | 逐条点名 7 处 `文件:行` 与「需要改成的方向」，**不写具体新数字** |
| 「347 条只有 SQLite 证据」口径 | 严重 | §9 | 「本轮**不**改变…**没有**在 PG 上复跑。不可把本轮读数读成「347 条已在 PG 复跑」」（**第 3 轮已更正，见 §13.4**） |
| §2 把 `docs/spec/项目规格.md` 指到错误行 | 一般 | §2 表末行 | 出处改为 `docs/spec/项目规格.md:2873`（并附该行原文） |
| 两处出处与 §9 引用区间不逐字 | 一般 | §2 表「pytest 调用形式」行、§9 | 改为「`Makefile:13`（`README.md:375`「怎么验证这些说法」一节为 `python -m pytest`，**无 `-q`**（该文件正被外部进程并发改写，行号会漂移，以小节标题为准））」；`builder.py` 拆为 `:34`（日期格式）与 `:39`（`"postgresql": "TO_CHAR"`）两个锚点 |
| 数字与算术不自洽 | 一般 | §1.3、§6 第 1/3 轮、§13.1 | 「多出的是 **1 + 2**」；第 1/3 轮标注「**无落盘产物**」并把三元组统一为 `145/61/842` |
| §8 噪音文件清单与实测 mtime 矛盾 | 一般 | §8.4、§13.1 第 3 项 | `evals/clarification_candidates.py` 移出「运行期间新增」清单，改述为「早于本轮已存在、属 P11 归档变更、与 9 条失败无关」；并写清「9 条失败只由 `app/agent/candidates.py` 与两个 P8 测试文件引起」 |
| 缺「时点读数」标注 | 一般 | §1.3、§6「时点声明」 | 两处各加一条：145/61/842/831 是 2026-10-26 17:45–17:49 的读数，现状（19:30 实测 146/65/885/879/6）另列，**历史读数不静默改数** |
| change 目录形态 | 一般 | §10.3（新增） | 登记「归档前补齐 proposal/plan，或显式记『证据轮、归档形态豁免』」，本轮不真的补 |

本轮**只改**了 `openspec/changes/verify-postgres-full-suite-evidence/evidence.md` 与同目录 `tasks.md` 两个文件；**未**重跑 PG 容器、**未**重跑全量 pytest（读数沿用第 6 节登记的两份仓外产物）。

### 13.3 本证据的能力边界

本证据**不能**支撑「全库在 PostgreSQL 上可用」：

- **无 PG 证据的用例数**：当轮 **842** 收集口径约 **323**、当前 **885** 收集口径 **366**（均为**下界**，见第 9 节）；**无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例**。
- **Redis 共享预算 2 条**：本轮因**未指 Redis 容器**而 skip（本轮 skip 的 2 条是 `tests/test_rate_limit_redis.py` 的 `test_two_app_instances_share_one_tenant_budget` 与 `test_two_stores_share_one_counter`）——**不是「无读数」**：2026-10-25 那轮两个容器同时指上是 `519 passed / 0 skipped`，`docs/spec/项目规格.md:24-25` 明写「默认跳过的 4 条 PostgreSQL 专项用例（并发与 Outbox）与 2 条 Redis 共享配额专项**全部执行**」，那 2 条**有 2026-10-25 的读数**；本轮**不撤销**该读数。
- **`evals/retrieval_metrics.py`**：**本轮未执行**——**不是「PG 读数缺失」**：2026-10-25 那轮已在 PG 上跑过五通道并与 SQLite 逐项一致（`docs/spec/项目规格.md:33`、`docs/spec/项目规格.md:4837`），本轮**不撤销**该读数。

### 13.4 第 3 轮（reviewer 退回，本轮返工）

退回项与本轮落点（逐条对应）：

| 退回项 | 级别 | 本轮落点 | 改后原文（摘） |
|---|---|---|---|
| 计数口径混用：把「本轮跑批的落库分配」当成「从无 PG 证据的用例数」（§9 原第 3 个 bullet：「**仍只有 SQLite 读数的存量用例**：约 **838** 条（842 收集 − 4 条 PG 专项）」），并连带在 §13.3 写成「约 838 条仍只有 SQLite 读数」 | 严重 | §9 第 3/4 个 bullet、§10.2 口径说明段、§13.3 | §9：「**本轮跑批的落库分配**（本轮跑批的事实）：842 条收集里，**4** 条 PG 专项跑在 PostgreSQL…**838** 条跑在临时 SQLite…**838 是「本轮跑在 SQLite 上的用例数」**，它**不是**「从无 PG 证据的用例数」」；「**无 PG 证据的用例数**：当轮 **842** 收集口径下约 **323** 条（= 842 − 519）；当前 **885** 收集口径下 **366** 条（= 885 − 519）」；§13.3：「无 PG 证据的用例数：当轮 **842** 收集口径约 **323**、当前 **885** 收集口径 **366**（均为**下界**，见第 9 节）」 |
| 拿 **347** 当「现状口径」并断言「本轮未改变」（§9 原第 2 个 bullet）；同时 §13.3 把有读数的 Redis 2 条写成「无读数」、把五通道检索指标写成「PG 读数缺失」 | 严重 | §9 第 2 个 bullet（含下界限定）、§10.2（新增 7 行 + 归属声明）、§13.3 | 「**现状口径是 366，不是 347**…推导式为 `885 − 519 = 366`…**347** 是 `docs/spec/项目规格.md:26` 的 9 个来源子计…**866 收集时代**的读数（`519 + 347 = 866`），与现状口径 366 相差 **19** 条，差额来自**渠道可用性轮（F-6）**补的 19 条。**无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例**」；§13.3：「Redis…本轮因**未指 Redis 容器**而 skip…**不是「无读数」**…那 2 条**有 2026-10-25 的读数**」「`evals/retrieval_metrics.py`…**本轮未执行**——**不是「PG 读数缺失」**…本轮**不撤销**该读数」 |
| 第 3 轮的时刻与 §8.1 记录的首次出现时刻在分钟级上互相覆盖，读起来矛盾 | 一般 | §6「本轮之前的两次运行」第 3 轮那条 | 「**第 3 轮的时刻为近似值**（分钟级记录），与 §8.1 记录的首次出现时刻可能互相覆盖。」 |
| §10.3 的归档形态待办未提「运行前逐字快照缺失」 | 一般 | §10.3 | 「运行前逐字快照缺失一事已在 §1.1 登记，归档说明中一并写明。」 |

本轮**只改**了 `openspec/changes/verify-postgres-full-suite-evidence/evidence.md` 与同目录 `tasks.md` 两个文件；**未**重跑 PG 容器、**未**重跑全量 pytest、**未**重跑 `evals/run.py`、**未**跑 `ruff` / `mypy` / `alembic check`（读数沿用第 6 节登记的两份仓外产物，**一个读数都没有变**），**未**动 `docs/**`、`README.md`、`CONTRIBUTING.md` 与任何产品 / 测试 / 迁移 / 脚本文件。

### 13.5 第 4 轮（reviewer 判定通过，附 5 项一般项；本轮把这 5 项落笔）

上一轮 reviewer 的结论是**通过**（无阻塞、无严重项），登记了 5 个**非阻塞一般项**。本轮只落笔这 5 项，**读数一个都没变**：evidence.md §6 登记的全部读数（842 收集 / 831 passed / 9 failed / 2 skipped / 172.486 s / 145 / 61 / 4 条 PG 专项耗时 / 14 条迁移 / `alembic check` 原文 / Golden 40/40）与第 6 节登记的两份仓外产物**原样不变**。

一般项处置：

1. **锚点漂移**（§10.2 表 `docs/guide/证据索引.md` 行）：原锚点 `:906` 指向的是**空行**，真实承载口径的是 `:907`（「## 四、运行读数：一轮流量与端到端延迟」）与 `:915`（「### 2026-10-26 复跑（当前读数）」）。漂移原因是并发改写：`docs/guide/证据索引.md` 实测 mtime **2026-10-26 19:31:30**，**晚于** §1.2 快照的 19:30。改后按 §2 对 `README.md` 的既有处理方式补「行号会漂移，以小节标题为准」。
2. **§9 下界限定的展品清单不完整**：原清单只列落在本轮窗口附近的 4 个 `tests/**` 文件；实测另有 **7** 个 tracked 测试文件在 2026-10-25 那轮（约 15:07–15:09）之后、本轮窗口之前被改写，已补进 §9 与 §10.1 第 6 项。**结论方向不变且仍偏保守**：缺口只会**大于** 366，不会被高估；「下界」的措辞**未改**。
3. **`app/sql/` 子句过宽（事实错误）**：原句把 `app/sql/builder.py:34` / `:39`、`app/sql/service.py:400`、`app/sql/prefetch.py:284` 一并说成「已被那一轮 PG 覆盖」。按实测 mtime 拆开：`builder.py`（2026-10-25 11:09:58）**早于**那轮跑批时刻，**当前内容确已被那一轮覆盖**；`service.py`（2026-10-25 23:26:35）与 `prefetch.py`（2026-10-25 23:11:28）**晚于**那轮跑批时刻，**当前内容从未在 PG 上跑过**。**操作结论不变**（三者均需回归），锚点行号（`:34` / `:39` / `:400` / `:284`）一个未删。
4. **§13.2 留痕未加指向**：第 2 轮那条「改后原文（摘）」单元格仍引用旧措辞「本轮**不**改变…」，已补内联指向「**第 3 轮已更正，见 §13.4**」（措辞沿用同目录 `tasks.md` 第 2 轮对照表的既有写法）。
5. **§10.2 计数措辞歧义**：原文「上表（含既有 7 行）共 **9 处**写着「347」的表述」把「表内含「347」的行数」与「待对齐的文档位置数」混在一起。改后写明：表共 **14** 行、含「347」字样的共 **10** 行（README 那一行的引文里也含「347」），而**待对齐的文档位置是 9 处**（**不含** `README.md:178-179`——README 那一行是**现状口径 366 本身**，不是待改项）。**9 未改成 10**。

本轮**只改**了 `openspec/changes/verify-postgres-full-suite-evidence/evidence.md` 与同目录 `tasks.md` 两个文件；**未**重跑 PG 容器、**未**重跑全量 pytest、**未**重跑 `evals/run.py`、**未**跑 `ruff` / `mypy` / `alembic check` / `evals/*`，**未**动 `docs/**`、`README.md`、`CONTRIBUTING.md` 与任何产品 / 测试 / 迁移 / 脚本文件。

---

## 13. 归档说明（2026-10-27）

**归档依据**：独立审查（reviewer，只读代码与文档、不运行测试）判定本文档的事实、范围声明与
缺口声明三部分成立，可以归档。本文没有规格增量，归档只是把变更目录移入
`openspec/changes/archive/`，不涉及 `openspec/specs/` 的任何合并。

**自述与已核的边界**：本文所有「本轮未改动既有文件」的表述都是**执行者自述**，在当前脏工作树上
无法独立复核（`openspec/AGENTS.md` §8.2 第 3 条）。可核的只有两件：仓库内的受控产物是两个文件
（本文与 `tasks.md`）；仓外读数产物不入库。归档不改变这个边界——**归档不等于把自述升格为已核事实**。

**门禁读数的时点（引用前必读）**：文中 §6 的「842 收集 / 831 passed / 9 failed / 2 skipped」
是 **2026-10-26 那一次运行**的读数，不是当前值，也不代表当前树上仍能复现那 9 条失败。
归档时点（2026-10-27）的全量读数是另一组：收集 1035 条、**1029 passed / 6 skipped / 0 failed**。
两者不可混引；引用 §6 的数字时必须同时写明它是 2026-10-26 的读数。
