# T-4 任务清单：补 PostgreSQL 全量测试真实读数

范围红线：不修改任何产品/测试/迁移/配置/CI/文档既有文件；本目录两个**受控**产物为 `evidence.md` 与 `tasks.md`。

> **范围声明的性质（诚实口径）**：「未修改任何既有文件」是**执行者自述**，不是已核事实——会话开始前工作树已是脏树（71 M + 47 ??），本轮与外部进程并发写同一棵树，**当前 `git status` 无法独立复核本轮是否改动过既有文件**（`openspec/AGENTS.md` §8.2 第 3 条）。可核的部分：仓内**唯一新增路径**是 `.pytest_tmp/`（pytest `--basetemp` 副产物）；仓外读数产物两份（路径 / 字节数 / mtime 见 evidence.md §6，**不入库、归档后不可复看**）；窗口内被外部进程改写的 tracked 文件清单见 evidence.md §8.2。

- [x] 1. 抓取树状态：`git log -1 --oneline`、`git status --short`（71 M + 47 ??，脏树）→ evidence.md §1
- [x] 2. 口径核对：`TICKETPILOT_TEST_POSTGRES_URL` / `postgresql+psycopg://` / `alembic -x db_url=` / `python -m pytest -q` / `evals/run.py --database-url` → evidence.md §2
- [x] 3. 起一次性容器 `tp-pg-t4`（postgres:16-alpine，127.0.0.1:15443；任务示例端口 15432 已被占用，改用空闲端口）→ evidence.md §3
- [x] 4. `alembic upgrade head`：14 条迁移全走完，head `a1c7e9d4b820`，退出码 0 → evidence.md §4
- [x] 5. `alembic check`：`No new upgrade operations detected.`，退出码 0 → evidence.md §5
- [x] 6. 带 PG 环境变量的全量 pytest（`--basetemp=.pytest_tmp`）：842 收集 / 831 passed / 9 failed / 2 skipped / 172.486 s（JUnit XML 权威；控制台末行统计被沙箱截断，已如实登记）→ evidence.md §6
- [x] 7. PG 专项 4 条用例（concurrency×2、outbox×2）在 PostgreSQL 16.15 上真实执行并 PASS → evidence.md §6
- [x] 8. 9 条失败逐条登记（全部归因外部并发写入的 WIP 文件，无一与 PG 相关），未修复 → evidence.md §6、§8
- [x] 9. 跳过清单逐条登记：仅 2 条，均为缺 `TICKETPILOT_TEST_REDIS_URL`（超本任务范围）→ evidence.md §6
- [x] 10. Golden Set PG 复跑：独立库 `tp_eval` 迁移后 `evals/run.py --database-url ...` → `40/40 golden cases passed`，退出码 0 → evidence.md §7
- [x] 11. 覆盖缺口声明：**落库分配**（842 收集里 4 条 PG 专项跑在 PostgreSQL、**838** 条跑在临时 SQLite）与**无 PG 证据的用例数**（当轮 842 收集口径约 **323** = 842 − 519、当前 885 收集口径 **366** = 885 − 519）**分开写**；**无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例**（除 4 条 PG 专项外仍走 `tests/conftest.py:28` 的临时 SQLite）→ evidence.md §9
- [x] 12. 待办登记：`.pytest_tmp` 不在 `.gitignore`（未修改）、`outbox` 计数过期、文档数字守卫待复跑、Redis 补证超范围等 → evidence.md §10.1
- [x] 13. 清理：`docker rm -f tp-pg-t4`；`docker ps -a` 零残留；`my-agentix-neo4j-local`、`my-agentix-mysql-1` 及其余既有容器全部仍在运行 → evidence.md §12

## 第 2 轮返工（第 2 轮退回后）

- [x] 14. 范围声明改为**自述**口径：明写「会话开始前工作树已是脏树，当前 `git status` 无法独立复核本轮是否改动过既有文件」；§1 标题删掉「运行前后一致」→ evidence.md 头部 + §1
- [x] 15. 登记仓内唯一新增路径 `.pytest_tmp/`（`--basetemp` 副产物，未进 `.gitignore`）；「唯一新增产物」改为两个**受控**产物 → evidence.md §1.4
- [x] 16. 树状态快照：现状 `git status --short` + `git diff --stat` **逐字粘贴**；登记「运行前只留计数与摘录、没有逐字快照」这一缺陷 → evidence.md §1.1 / §1.2
- [x] 17. §8 增列窗口内被并发改写的 tracked 文件清单与时刻（7 个文件），并标出 3 个测试文件（17:47:55）落在权威跑批窗口内 → evidence.md §8.2
- [x] 18. 登记两份仓外产物的路径 / 字节数 / mtime / 生成方式 + 「不入库、归档后不可复看」→ evidence.md §6
- [x] 19. §10 增「文档同步待办」：逐条点名 7 处 `文件:行` 与需要改成的方向（不写具体新数字）→ evidence.md §10.2
- [x] 20. §9 补「347 条仍只有 SQLite 证据的口径本轮未改变」；`builder.py` 锚点拆为 `:34` 与 `:39` → evidence.md §9
- [x] 21. §2 两处锚点更正：`docs/spec/项目规格.md:2831` → `:2873`；pytest 调用形式出处改为 `Makefile:13`（README 为 `python -m pytest`，无 `-q`）→ evidence.md §2
- [x] 22. 数字与算术自洽：`2+2` → `1+2`；第 1/3 轮标注「无落盘产物」并统一三元组 `145/61/842`；§13.1 第 1 项的**理由**改为可复算的模块去重法 → evidence.md §1.3 / §6 / §13.1
- [x] 23. `evals/clarification_candidates.py` 移出「运行期间新增」清单，改述为「早于本轮已存在、属 P11 归档变更、与 9 条失败无关」；§13.1 第 3 项口径更正 → evidence.md §8.4 / §13.1
- [x] 24. 时点读数标注：§1.3 与 §6 各加一条「时点读数、现状已变」，145/61/842/831 未被改成 146/65/885/879/6 → evidence.md §1.3 / §6
- [x] 25. §10 增「归档形态待办」（补 proposal/plan 或显式记豁免；本轮不真的补）→ evidence.md §10.3
- [x] 26. 本轮只改本目录 `evidence.md` 与 `tasks.md`；未重跑 PG 容器、未重跑全量 pytest、未动 `docs/**` 与任何产品/测试/迁移/脚本 → 见下「本轮改动清单」

## 验收标准对照

| 标准 | 证据位置 |
|---|---|
| 全量测试在真实 PG 容器上跑过且留下统计读数 | evidence.md §6（XML 属性行 + PG 专项 4 条逐条 PASS） |
| `alembic check` 输出原文 | evidence.md §5 |
| 跳过用例逐条列名与原因 | evidence.md §6 |
| 树状态写入证据 | evidence.md §1（现状快照逐字粘贴 + 运行前快照的缺陷登记） |
| Golden Set PG 读数 | evidence.md §7 |
| 未修改任何既有文件、未 commit | **自述口径**：见文件头「范围声明的性质」——当前 `git status` 无法独立复核；`git log -1` 保持 `8dba4ea` |
| 容器清理且未动既有容器 | evidence.md §12 |

## 第 2 轮验收标准对照（退回项逐条）

| 退回项 | 验收标准 | 证据位置 |
|---|---|---|
| 严重 S1 | §1 不再出现「运行前后一致」；有「自述、脏树、当前 git 状态无法独立复核」的明写 | evidence.md 头部范围声明条 + §1 标题 + §1.1 |
| 严重 S1 | `.pytest_tmp/` 登记为仓内唯一新增路径；不再声称「唯一新增产物」 | evidence.md 头部范围声明条 + §1.4 |
| 严重 S1 | §8 列出 17:41–17:50 被并发改写的 tracked 文件（含时刻），并说明 3 个测试文件落在跑批窗口内 | evidence.md §8.2（7 行表格 + 读数解释边界段） |
| 严重 S1 | §6 有仓外两份产物的路径 + 字节数 + mtime + 「不入库、归档后不可复看」 | evidence.md §6「仓外落盘产物」表 |
| 严重 S2 | §10 出现「文档同步待办」，点名 7 个 `文件:行` | evidence.md §10.2 |
| 严重 S2 | §9 明确「347 条仍只有 SQLite 证据」未被本轮改变（**第 3 轮更正该表述**：§9 已改为引用**现状口径 366**——`885 − 519 = 366`（`README.md:178`）；347 是 `docs/spec/项目规格.md:26` 的 9 个来源子计、**866 收集时代**的读数（`519 + 347 = 866`），与 366 差 **19** 条、差额来自**渠道可用性轮（F-6）**；保留「**无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例**」） | evidence.md §9 第 2 个 bullet |
| 一般 G1 | §2 的 `docs/spec/项目规格.md` 锚点命中「PG 专项需显式启用 / 不能指向共享库」那句 | evidence.md §2 表末行（`:2873`，附原文） |
| 一般 G2 | §2 与 §9 两处锚点与当前文件逐字对应 | evidence.md §2 表「pytest 调用形式」行（`Makefile:13` / `README.md:375`）+ §9（`builder.py:34` 与 `:39`） |
| 一般 G3 | 三处数字互不冲突且与 XML 的 842/145/61 一致 | evidence.md §1.3、§6「第 1/3 轮」、§13.1 第 1 项 |
| 一般 G4 | `evals/clarification_candidates.py` 不再被列为「运行期间新增」 | evidence.md §8.4 + §13.1 第 3 项 |
| 一般 G5 | §1 与 §6 各有「时点读数、现状已变」声明；145/61 未被改成 146/65 | evidence.md §1.3 + §6「时点声明」 |
| 一般 G6 | §10 有归档形态处置待办 | evidence.md §10.3 |

## 复验标准（供 reviewer）

1. 核对 `C:\Users\15237\tp-t4-results.xml` 与 `C:\Users\15237\tp-pg-t4-pytest-final.log` 与 evidence.md §6 数字一致；用 `stat` 复核两份产物的字节数与 mtime。
2. `docker ps -a` 无 `tp-pg-t4`；`my-agentix-*` 容器仍在。
3. 在无外部并发写入的树状态下复跑第 4–7 步，预期 `test_docs_consistency` 与 `test_intent_candidate_model` 相关失败消失。
4. 逐条核对本文件「第 2 轮验收标准对照」表的 12 行落点：按 `文件:行` 打开对应段落，确认表述与断言一致。
5. 逐字复核 evidence.md §1.2 的两份代码块与 `git status --short` / `git diff --stat` 当前输出**逐字一致**（若不一致，说明树在 19:26 之后又被外部改写，属预期，见 §1.3 末条）。
6. 核对 evidence.md §10.2 表中 7 个 `文件:行` 是否命中对应文本（`docs/spec/项目规格.md:24-31` / `:3634-3640`、`docs/下一步规划.md:83` / `:676-681`、`docs/records/智能体平台设想.md:164`、`docs/guide/黄金集说明.md:61`、`docs/guide/证据索引.md:906`）。

## 本轮改动清单（第 2 轮返工）

**只改了两个文件**：

1. `openspec/changes/verify-postgres-full-suite-evidence/evidence.md`
2. `openspec/changes/verify-postgres-full-suite-evidence/tasks.md`

**明确没有改动**：`docs/**`（任何文件）、`app/**`、`tests/**`、`alembic/**`、`scripts/**`、`evals/**`、`web/**`、`openspec/AGENTS.md`、其它任何 `openspec/changes/` 目录、仓库根的 `.pytest_tmp/`（**刻意保留**，见 evidence.md §1.4）、`.gitignore`。

**没有重跑**：未重跑 PG 容器、未重跑全量 pytest、未重跑 `evals/run.py`。读数沿用 evidence.md §6 登记的两份仓外产物。

## 第 3 轮返工（第 3 轮退回后）

> 本轮是**纯证据文档的计数口径修正**：**读数一个都没变**，**未**重跑任何测试。第 3 轮审查已确认核心读数（842 收集 / 831 passed / 9 failed / 2 skipped / 172.486 s 等）逐项独立复核通过；本轮只处理**两个计数口径的严重项**与**两个一般项**。

- [x] 27. §9 第 3 个 bullet 拆成两个：**落库分配**（842 收集里 4 条 PG 专项跑在 PostgreSQL、**838** 条跑在临时 SQLite，`tests/conftest.py:28`）与**无 PG 证据的用例数**（当轮 842 口径约 **323** = 842 − 519、当前 885 口径 **366** = 885 − 519）；「838」只以「本轮跑在 SQLite 上的用例数」的身份出现 → evidence.md §9
- [x] 28. §9 引用**现状口径 366**（`README.md:178`）：写明推导式 `885 − 519 = 366`；写明 **347** 是 `docs/spec/项目规格.md:26` 的 9 个来源子计（68+13+25+19+102+13+31+28+48 = 347）、**866 收集时代**的读数（`519 + 347 = 866`），与 366 差 **19** 条、差额来自**渠道可用性轮（F-6）**补的 19 条；保留并改写为「**无论按 347 还是按 366，本轮都没有在 PG 上复跑这些用例**」→ evidence.md §9
- [x] 29. §9 补**下界限定**：2026-10-25 那轮之后 `tests/**` 有过改写（`tests/test_llm_gap.py`、`tests/test_model_distribution.py`、`tests/test_security_model_cross.py`、`tests/test_pydantic_agent.py`，实测 mtime 见 §8.2），「当前内容已有 PG 证据」的用例数只会**少于** 519、缺口只会**大于** 366；本轮不逐条统计精确值，登记为待办（§10.1）；并写明「**该轮 PG 证据不撤销**」→ evidence.md §9 / §10.1
- [x] 30. §9「复跑价值最高」改为「**本轮未在 PG 上复跑、但 2026-10-25 那轮已被 PG 覆盖、本轮变更后需回归**」；`app/sql/builder.py:34` / `:39`、`app/sql/service.py:400`、`app/sql/prefetch.py:284`、`app/models/approval.py:23,32` 等 `文件:行` 锚点**一个未删** → evidence.md §9
- [x] 31. §13.3 改写：838 → **323 / 366** 双口径；Redis 2 条由「无读数」改为「**本轮因未指 Redis 容器而 skip**」（`tests/test_rate_limit_redis.py` 那两条，2026-10-25 那轮有读数）；`evals/retrieval_metrics.py` 由「PG 读数缺失」改为「**本轮未执行**」（2026-10-25 那轮已在 PG 上跑过五通道并与 SQLite 逐项一致，本轮**不撤销**该读数）→ evidence.md §13.3
- [x] 32. §10.2 增**两套计数的口径说明段** + 追加 7 行新登记项（`README.md:178-180`、`docs/spec/项目规格.md:2875`、`docs/下一步规划.md:5`、`docs/下一步规划.md:1107`、`docs/guide/范围与边界.md:98`、`docs/records/智能体平台设想.md:856`、`CONTRIBUTING.md:70`）+ 表后**归属声明**（9 处「347」与 366 的对齐由文档收口轮执行，T-4 **只登记、不改**）→ evidence.md §10.2
- [x] 33. §6 补「第 3 轮的时刻为**近似值**（分钟级记录），与 §8.1 记录的首次出现时刻可能互相覆盖」的声明 → evidence.md §6
- [x] 34. §10.3 追加「运行前逐字快照缺失一事已在 §1.1 登记，归档说明中一并写明」→ evidence.md §10.3
- [x] 35. 新增 §13.4（第 3 轮退回项与本轮落点对照表，2 严重 + 2 一般逐条映射）→ evidence.md §13.4
- [x] 36. 本轮只改本目录 `evidence.md` 与 `tasks.md`；未重跑任何测试、未动 `docs/**` 与任何产品 / 测试 / 迁移 / 脚本 → 见下「本轮改动清单（第 3 轮返工）」

## 本轮改动清单（第 3 轮返工）

**只改了两个文件**：

1. `openspec/changes/verify-postgres-full-suite-evidence/evidence.md`
2. `openspec/changes/verify-postgres-full-suite-evidence/tasks.md`

**明确没有改动**：`docs/**`（任何文件）、`README.md`、`CONTRIBUTING.md`、`app/**`、`tests/**`、`alembic/**`、`scripts/**`、`evals/**`、`web/**`、`.qoder/**`、`openspec/AGENTS.md`、其它任何 `openspec/changes/` 目录、仓库根的 `.pytest_tmp/`（**刻意保留**，见 evidence.md §1.4）、`.gitignore`。

**没有重跑**：未重跑 PG 容器、未重跑全量 pytest、未重跑 `evals/run.py`、未跑 `ruff` / `mypy` / `alembic check` / `evals/*`。本轮是**纯证据文档的计数口径修正**，evidence.md §6 登记的全部读数（842 收集 / 831 passed / 9 failed / 2 skipped / 172.486 s / 145 / 61 / 59 个测试模块 / 4 条 PG 专项耗时 / 14 条迁移 / `alembic check` 原文 / Golden 40/40）**一个都没有变**。

## 第 4 轮一般项落笔（reviewer 判定通过后）

> 上一轮 reviewer 的结论是**通过**（无阻塞、无严重项），登记了 5 个**非阻塞一般项**。本轮只把这 5 项落笔：**读数一个都没变**，**未**重跑任何测试，evidence.md §6 登记的全部读数与第 6 节登记的两份仓外产物原样不变。

- [x] 37. §10.2 表 `docs/guide/证据索引.md` 行的锚点由 `:906`（空行）改为真实承载口径的 `:907`（「## 四、运行读数：一轮流量与端到端延迟」）与 `:915`（「2026-10-26 复跑（当前读数）」小节），并按 §2 对 `README.md` 的既有处理方式补「行号会漂移，以小节标题为准」→ evidence.md §10.2
- [x] 38. §9「下界限定」与 §10.1 第 6 项各补一句：原清单只列落在本轮窗口附近的 4 个 `tests/**` 文件，另有 **7** 个 tracked 测试文件在 2026-10-25 那轮（约 15:07–15:09）之后、本轮窗口之前被改写（`tests/test_retrieval_channels.py`、`tests/test_llm_usage.py`、`tests/test_sql_analytics.py`、`tests/test_sql_guard.py`、`tests/test_proactive_rules.py`、`tests/test_policy_engine.py`、`tests/test_handoff.py`）；**「下界」的措辞未改**，缺口只会**大于** 366 → evidence.md §9 / §10.1
- [x] 39. §9 的 `app/sql/` 子句**按文件分述**覆盖范围：`app/sql/builder.py:34` / `:39` 早于 2026-10-25 那轮跑批时刻（当前内容确已被那一轮覆盖）；`app/sql/service.py:400`、`app/sql/prefetch.py:284` 晚于那轮跑批时刻（**当前内容从未在 PG 上跑过**）；三者**均需回归**的操作结论不变，锚点行号一个未删 → evidence.md §9
- [x] 40. §13.2 第 2 轮对照表「347 条只有 SQLite 证据」那一行补内联指向「**第 3 轮已更正，见 §13.4**」（措辞沿用本文件第 2 轮对照表的既有写法）→ evidence.md §13.2
- [x] 41. §10.2 表后那句计数措辞去歧义：表共 **14** 行、含「347」字样的共 **10** 行、**待对齐的文档位置是 9 处**（不含 `README.md:178-179`——README 是**现状口径 366 本身**，不是待改项）；**9 未改成 10** → evidence.md §10.2
- [x] 42. 新增 §13.5（第 4 轮 5 项一般项的落点与依据）→ evidence.md §13.5
- [x] 43. 本轮只改本目录 `evidence.md` 与 `tasks.md`；未重跑任何测试、未动 `docs/**` 与任何产品 / 测试 / 迁移 / 脚本 → 见下「本轮改动清单（第 4 轮一般项落笔）」

## 本轮改动清单（第 4 轮一般项落笔）

**只改了两个文件**：

1. `openspec/changes/verify-postgres-full-suite-evidence/evidence.md`
2. `openspec/changes/verify-postgres-full-suite-evidence/tasks.md`

**明确没有改动**：`docs/**`（任何文件）、`README.md`、`CONTRIBUTING.md`、`app/**`、`tests/**`、`alembic/**`、`scripts/**`、`evals/**`、`web/**`、`.qoder/**`、`openspec/AGENTS.md`、其它任何 `openspec/changes/` 目录、仓库根的 `.pytest_tmp/`（**刻意保留**，见 evidence.md §1.4）、`.gitignore`。

**没有重跑**：未重跑 PG 容器、未重跑全量 pytest、未重跑 `evals/run.py`、未跑 `ruff` / `mypy` / `alembic check` / `evals/*`。本轮是**纯证据文档的事实性修正**，evidence.md §6 登记的全部读数（842 收集 / 831 passed / 9 failed / 2 skipped / 172.486 s / 145 / 61 / 59 个测试模块 / 4 条 PG 专项耗时 / 14 条迁移 / `alembic check` 原文 / Golden 40/40）与第 6 节登记的两份仓外产物**一个都没有变**。
