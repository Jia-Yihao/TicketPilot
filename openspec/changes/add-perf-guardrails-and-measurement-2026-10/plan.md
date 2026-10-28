# Plan: 性能/治理真缺口收敛 + bench 口径（T-TP-PLAN 产出）

## 目标

把 TP 清单实测后的 8 个真缺口拆成可独立验收的任务：2 个可立即开工（pending_count 处置、
文档数字核对扩面），2 个 bench 组任务（bench 骨架、负验证进 CI，默认 R0 之后开工，
提前开工需用户确认「bench 可先于收尾轮开工」——口径文件
`.qoder/rules/benchmark-harness.md` 已落地≠开工已解锁，该确认截至本节记录时尚未发生），
4 个测量读数任务（A/B、M 扫描、
预取校准、审批队列 baseline，依赖 bench 骨架），3 个实现任务显式阻塞在途 T-B1-R3 与
上轮收尾轮之后（预算刹车、事件分页、seed 去重），最后本波收尾轮同步本波自身增量数字并
跑全量门禁（既存 8 项漂移的数字刷新由 R0 承担）。

## 非目标

- 已砍项一律不做：审批队列复合索引、对账/复核提速、Outbox 积压新读法、对账离线测量、
  双库跑、「模型产 SQL 计划」。TP-3 仅出耗时 baseline（任务 8），不做查询形态改造。
- 不改产品行为默认值：所有优化 flag 化默认关或默认等价。

## 架构与落点

- 新目录 `bench/`（仓内，pytest 不收——`pyproject.toml` 的
  `[tool.pytest.ini_options]` 中 `testpaths` 保持 `tests`，不改；lint 覆盖走 `Makefile`
  新增独立 `lint-bench` 目标（任务 2 独占该文件改动）+ CI 独立步骤 `ruff check bench`，
  既有主命令行逐字不改；mypy 不覆盖 bench；口径唯一来源
  `.qoder/rules/benchmark-harness.md` §2）。共享件：`bench/_fingerprint.py`
  （env_fingerprint + produced_by）与 `bench/validate.py`（模块形状校验，显式命令运行）。
- 预算刹车落 `app/core/`（新模块）+ 调用侧接线；事件分页落
  `app/repositories/tasks.py` + `app/services/runtime.py`；seed 去重落 `tests/conftest.py`
  （session 级模板库 + 每用例复制，隔离语义不变）。

## 口径来源（reviewer 判定基准）

- 项目级：`agents.md`、`openspec/project.md` 不变量、`.qoder/rules/approval-tiering.md`、
  `.qoder/rules/refund-reason-taxonomy.md`、`.qoder/rules/intent-candidates.md`、
  `.qoder/rules/retrieval-query-rewrite.md`、`.qoder/rules/clarification-candidates.md`、
  `.qoder/rules/handoff-profile-summary.md`。
- bench 口径：`.qoder/rules/benchmark-harness.md`——**该口径文件已落地**（经编排方
  2026-10-27 裁定改名并纠偏），是 bench 测量的**唯一**项目级
  口径，本包引用它、不再新建第二份。其三条已生效立场（pytest 不收 `bench/`、`ruff` 以独立
  `lint-bench` 目标 + CI 独立步骤覆盖 bench 而不改被文档锚定的主命令行、`python -m mypy app`
  不扩面）与本包文字冲突时**以该文件为准**。
  本包 `benchmark-harness-draft.md` 已被该口径文件**取代**（见其 §2.3 末段），仅存档为历史
  草案，不得作为实现依据。

## 全局硬约束（每个任务的红线，reviewer 逐条对照）

1. 重排读数表述：`OVER BUDGET` 是 `evals/rerank_latency.py:249` 的**离线延迟判定**；
   运行时关闭靠配置默认 `retrieval_rerank_enabled=False`（`app/core/config.py:70`）+
   熔断 fail-open。任何产出不得写成「已自动关闭/已优化」。
2. SQLite/PostgreSQL 逐位一致必须保留；`app/retrieval/search.py` 的 `visible_chunks()`
   补的 ORDER BY 不得回退。
3. 退款写路径五要素不动：资格校验、四眼审批、`action_digest`、资源版本绑定、幂等键；
   任务终态与 `result.outcome` 分离的读数必须守住
   （钉在 `tests/test_refunds.py:266-267,294,296`）。
4. SQL：19 个 reason 码（`app/sql/errors.py:14-34`）逐条检查不得绕过；无计划仍
   `NO_PLAN` 转人工（`app/sql/router.py:118-120` 到 `app/agent/actions.py`）；
   L3 明细永不进 prompt（`app/sql/snapshot.py:117-152`）。校准类读数（TP-4 禁区第 4 条）：
   校准读数与其产生时的数据量/分布绑定，换数据量必须重跑，不得由旧读数外推到新数据量。
5. 渠道/API：统一错误信封（`app/core/errors.py:160-176`）、`X-Request-ID`、验签
   fail-closed（`app/channels/base.py:222-258`）、幂等闸 5 处不变。
6. 守卫：失败必须报 `file:line`（`_claim_line`，`tests/test_docs_consistency.py:267-276`，
   锚点整行唯一且**不含数字**，见 `:393-395`）；负验证必须六红一绿
   （`scripts/guard_negative_check.py:193-230` 恰 6 CASES）；每条事实至少 2 claims
   （`:435-443`）；COMPUTERS 与清单双向无多余（`:380-388`）。
7. 模型侧：`IntentOutput` 契约保留；模型不授权任何写动作；`ModelDecision.verifies()`
   六条件不放宽（`app/agent/decision.py:45-62`）；「单轮写动作 1 次模型调用」读数守住。
8. 「跨 worker 靠所有进程读同一张表」成立条件不变，禁止内存总线/进程内计数。
9. 严禁把 mock/缓存带进生产；bench 产物不得被 `app/` import。
10. 本波任何任务不得整文件覆盖既有文件，只做定位替换；`app/agent/support_agent.py`、
    `tests/test_decision_reuse.py`、`docs/adr/ADR-030-决策复用.md`、
    `.qoder/rules/refund-reason-taxonomy.md` 只有阻塞组任务（解锁后）可动。
11. 改动 pytest 收集数（本包不写具体值，以开工当日 `pytest --collect-only -q -o addopts=`
    现算读数为准）、`app/` py 文件数（148）、
    `tests/` py 文件数（72）、迁移数（15，head `d9e4c7a15b82`）中任何一项的任务，
    必须在本任务内定位同步 `docs/fact-baseline.yaml` 已登记 claims 与 README/
    PROJECT_SPEC 对应句（守卫即时红的既有工作法）；既存 8 项漂移的说明书 docx
    成对重生成随 **R0** 的数字刷新同轮兑现（归属见任务 4「排序理由」）；本波任务 12
    仅当本波增量再次触发 `in_manual` 行时另起一次成对重生成。executor 开工当天必须重跑 `pytest --collect-only -q -o addopts=`
    等命令取**当日读数**再动手，引用本计划数字仅作对照。

## 审查重点（reviewer 读代码判定，不跑全量）

- 任务 9：刹车点是否在**调用前**；默认关闭时是否零新增查询、零新增 trace；越预算分支
  是否可能触达写路径；预算是否读 `llm_usage` 同表而非内存态。
- 任务 10：LIMIT 是否真进 SQL；keyset 游标在 SQLite/PG 的比较是否等价；
  `after_id` 缺行语义是否与旧代码一致。
- 任务 11：模板库复制方案是否保持每用例全新数据；seed 时机改变后写用例是否仍隔离。
- 任务 4：新锚点是否整行唯一且不含数字；是否误把时点读数做成现算函数。
- 任务 5-8：baseline.json 是否带 produced_by（commit+脏状态）与 reasoning-tokens
  口径；收窄/无信号两侧分母是否分开报。

---

## 任务清单（编号全局连续；分派细表见 dispatch.md）

### 任务 1（默认 R0 之后开工，提前开工需用户确认「bench 可先于收尾轮开工」——口径文件 `.qoder/rules/benchmark-harness.md` 已落地≠开工已解锁，该确认截至本节记录时尚未发生；「批准草案」环节不存在）
**bench 骨架与共享件**。可写文件集：`bench/**`（全新目录，与一切在途轮次及两个收尾轮（R0、本波任务 12）的文件集不相交）。
- 内容：`bench/README.md`（门禁归属三项决定与后果：pytest 不收、ruff 以 `Makefile` 独立
  `lint-bench` 目标 + CI 独立步骤 `ruff check bench` 覆盖（既有主命令行不改；目标由任务 2
  提供，首次提交若未落地须按下方验收逐条声明）、mypy 不覆盖）；`bench/_fingerprint.py`（Python/SDK/模型快照/
  端点/温度=0/预热/重复次数不低于 5/分位数池化 + `reasoning_tokens_enabled` +
  `produced_by`：commit、`git status --short` 摘要、生成命令、时刻）；
  `bench/validate.py`（模块四件形状校验 + 指纹缺项 FAIL + **per-module 期望表**：支持按
  模块登记「本模块必需键」的期望表，任务 5–8 各自登记本模块必需键，一般 9 的落点；
  显式命令运行，pytest 不收）。
- 自检（先失败）：对故意缺 `produced_by` 的样例 `validate.py` 必须报 FAIL。
- 验证：`./.venv/Scripts/python.exe bench/validate.py > .tmp/bench_validate.txt 2>&1`
  退出 0；`./.venv/Scripts/ruff.exe check bench` 绿。
- 验收：四件形状成立；`bench/` 不 import 进 `app/` 的反向路径为零（判定方式：本任务显式
  命令 + reviewer 读 diff——口径 §2.5 已登记 `bench/` 不在两个文档守卫扫描清单，无机器核对）；
  不改 `pyproject.toml`/`Makefile`/README 数字（收集数、文件数事实全部不动；`Makefile` 的
  `lint-bench` 目标唯一归属任务 2）。`.qoder/rules/benchmark-harness.md` §0「`bench/` 首次
  提交必须同时满足」三条落成逐条可核对声明：(1) `bench/**` 内无 `test_*.py`/`*_test.py`
  （本任务遍历断言）；(2) `python -m mypy app` 范围不扩面（本任务不碰任何 mypy 清单，
  核实既有串逐字不变）；(3) `lint-bench` 目标由任务 2 提供——首次提交时若任务 2 尚未落地，
  必须在 `bench/README.md` 逐条写明「哪一条尚未落地及原因」（口径 §0 允许形态），不得静默缺失。

### 任务 2（依赖任务 1）
**负验证进 CI + bench lint 进 CI（含 `Makefile` `lint-bench` 目标——本包内 `Makefile` 的
唯一可写归属）**。可写：`.github/workflows/ci.yml`、`Makefile`（`Makefile` 只允许**新增**
独立目标 `lint-bench: ruff check bench`，既有 `lint` 主命令行与其余目标逐字不动）。
- 在 test job 追加两个独立步骤：`python scripts/guard_negative_check.py` 与
  `ruff check bench`；不得合并改动既有 `ruff check app tests scripts evals alembic`
  那一行（该行被文档多处锚定，合并进主命令行属口径变更，由本波任务 12 随文档同步一并处理（口径 §2.2 原文未点名轮次；本波指认见 `docs/下一步规划.md` §12 的轮次消歧记录））。
- 验收：读 ci.yml——两步骤存在、非 continue-on-error、位于 test job；读 Makefile——
  `lint-bench: ruff check bench` 目标存在、主 `lint` 行逐字不变；
  本地等价读数：`./.venv/Scripts/python.exe scripts/guard_negative_check.py`（六红一绿、
  退出 0）与 `./.venv/Scripts/ruff.exe check bench` 绿。
- 风险登记（守卫级联 memory）：负验证 CASES 6 本身会因收集数漂移而红——若本任务
  落地时树上已有未同步的数字漂移，CI 失败原因必须先修数字，不得给步骤加
  continue-on-error 绕过。

### 任务 3（可立即开工）
**pending_count 死代码处置（裁定=删除）**。可写：`app/services/outbox.py`（仅此一个）。
- 删除 `:104-109`（全仓零调用方、不带租户边界；API 已有带边界的
  `repo.count_for`，`app/api/v1/outbox.py:63-86`；不新增第三种积压读法）。
- 测试：不新增（删除无行为面）；证据=`./.venv/Scripts/python.exe -m pytest
  tests/test_outbox.py -q -o addopts=` 绿 + 全仓检索 `pending_count` 零命中。
- 事实影响：`app/` 文件数 148 不变（同文件删函数）；收集数不变。

### 任务 4（可立即开工；必须完成并过审于 R0 开始与结束之前）
**文档数字核对覆盖面扩展（仅可现算子集）**。
- 可写：`tests/test_docs_consistency.py`（只扩 COMPUTERS 函数与清单数据，**不新增
  test_ 函数**、不动既有断言，收集数不变）、`docs/fact-baseline.yaml`、`docs/guide/评测说明.md`、
  `docs/guide/架构说明.md`、`docs/guide/接口说明.md`、`docs/guide/证据索引.md`（仅为本波新增/被改读数小节
  补「生成命令+日期」注记与结构核对对象，历史小节不动）、`docs/adr/*.md`（**禁 ADR-030**，在途责任）。
- 禁写：`README.md`、`docs/spec/项目规格.md`、说明书 docx、`.qoder/rules/**`。
- 范围裁定：新事实只收**可由仓库确定性重算**的数字（候选：`docs/guide/接口说明.md` 端点计数→
  与既有 openapi facts 交叉补锚点；`docs/guide/架构说明.md` 的模块/通道计数；
  `docs/adr/*` 的 ADR/守卫结构计数；bench 模块数在任务 1 落地后登记）。
  **时点读数（`docs/guide/证据索引.md` 的 p50/p95、通过率等）显式排除**：不可现算，
  进守卫会强迫改写历史读数，违反「时点读数标时点、历史保留」。替代=结构核对，
  **范围收窄为「本波新增/被改的读数小节」**（2026-10-27 现测：现文件 33 个 `###` 小节，仅四节标题
  带日期，要求历史小节逐段补齐超出本任务边界）。机械判据：同一 `###` 小节内须出现至少
  一个反引号包裹的生成命令串、且须出现至少一个 `YYYY-MM-DD` 日期串，缺一即结构核对失败
  （实现为新增一条既有测试内的数据检查：只扩数据不新增 test_ 函数）；既有历史小节不在该检查
  范围内，「逐段补齐历史小节」不属本任务职责（口径 §4.3 时点读数纪律不变）。
- 每条新事实：1 个现算函数 + 至少 2 处整行唯一、不含数字的锚点（`:267-276`、`:393-395`）；
  追加措辞时**不得复现已登记锚点子串**（级联 memory 教训）；双向闭合（`:380-388`）。
- 验收（判据分两档，适用条件写死）：本任务**不是**数字同步轮（既存 8 项漂移的数字统一刷新由上一轮收尾轮 **R0** 承担，
  见下方「排序理由」与 `dispatch.md` §0），故对 `tests/test_docs_consistency.py` 不作「全绿」判据——
  当前脏树上该守卫的改前基线读数为 `1 failed, 15 passed`，失败项是 8 个先于本波存在的
  文档数字漂移（`docs/spec/项目规格.md:3451` 五处、`:3517` 一处、
  `docs/records/智能体平台设想.md:164`、`docs/下一步规划.md:83`，多在本任务禁写/未覆盖
  范围），须等 R0 做数字刷新后方转绿。
  1. **一般轮次（含本任务，非数字同步轮）**：失败项集合必须是改前的**子集**——本轮
     不得新增失败项、不得新增锚点歧义。复现命令
     `PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest tests/test_docs_consistency.py tests/test_doc_code_anchors.py -q -o addopts=`，
     与改前基线读数（上述 `1 failed, 15 passed` / 8 个既存问题项）对照；该基线只是
     **文字判据**，不作为被机器核对的锚点句。另：`tests/test_architecture_guard.py`
     仍须绿；当日收集数与改前一致；`-m ruff`/mypy 绿。
  2. **数字同步轮（R0，负责刷新 `docs/fact-baseline.yaml` 数字行与各文档数字）**：
     判据才是该守卫**全绿**，且必须同轮兑现「`docs/spec/项目规格.md` 与
     `docs/spec/TicketPilot项目说明书.docx` 成对重生成」这一既有口径。本波任务 12
     只同步本波自身增量数字并跑全量门禁，不承担这 8 项既存漂移。
- 排序理由（写死）：先扩守卫，**R0** 在更严守卫下一次性同步数字，数字不刷两遍；
  故 R0 依赖任务 4，且本任务须完成并过审于 R0 开始与结束之前。

### 任务 5（依赖任务 1）
**收窄 vs 无信号（`narrowed=false`）的 token/延迟 A/B 读数**。可写：`bench/model_ab/**`。
- 走生产 `build_candidates` 与 brain 调用面；两侧分母分别报（口径
  `.qoder/rules/intent-candidates.md` §5：无信号集覆盖率恒 100%，不得混入收窄覆盖率）；
  含模型延迟一律带 `reasoning_tokens_enabled` 注记；真实模型读数与离线读数分开标注。
- 验收：`bench/model_ab/baseline.json` 含两侧样本数/均值/p50/p95 + produced_by；
  `bench/validate.py` 绿；本模块 `run.py` 以非零退出码断言本模块必需键（一般 9：断言
  归属本任务，`validate.py` 按任务 1 的 per-module 期望表校验本模块登记键）；
  不改 `app/**`、不改 `evals/**`。

### 任务 6（依赖任务 1）
**候选数 M 扫描（10/20/40）**。可写：`bench/retrieval_m/**`。
- 经构造参数 `candidate_pool`（`app/retrieval/hybrid.py:88,98`）扫 10/20/40（另记现值
  默认 20，`app/core/config.py:72`；`rerank_top_n` 默认 20，`config.py:88`），报
  MRR/recall@k；对照组必须含「pool 固定 20 的 dense_gate 扫描不是 M 扫描」的口径澄清
  （`evals/dense_gate_analysis.py:180`）；PG 一致性抽检复用
  `evals/retrieval_metrics.py --database-url`，不新写第二套。
- 验收：三档读数 + 预算内最佳 M 的建议表 + produced_by；**不设通过/失败门槛**
  （读数轮）；不改配置默认值；本模块 `run.py` 以非零退出码断言本模块必需键
  （归属本任务，`validate.py` 按 per-module 期望表校验）。

### 任务 7（依赖任务 1）
**预取阈值校准读数**。可写：`bench/prefetch_calibration/**`。
- 以 seed 数据 + 合成分布跑 scoped COUNT（`app/sql/prefetch.py:91-103`），对照
  `analytics_prefetch_max_rows=200`（`app/core/config.py:38`，消费 `app/main.py:114`、
  `app/agent/support_agent.py:213` **只读**）的路由分流
  （`app/sql/router.py:127` `ROW_COUNT_OVER_THRESHOLD`）；只出分布与建议，不改默认、
  不动 `scripts/sql_evidence.py`（它是守卫/NO_PLAN 演示，不承担校准）。
- 红线：**校准读数与其产生时的数据量/分布绑定，换数据量必须重跑、不得外推**
  （TP-4 禁区第 4 条，对应全局硬约束 4）；该注记落成
  `bench/prefetch_calibration/baseline.json` 的字段级必需键（本模块 `run.py` 以非零
  退出码断言之，`validate.py` 按 per-module 期望表校验——归属本任务）。
- 验收：分位数表 + 「200 分流命中率」读数 + produced_by + 上述校准注记键存在。

### 任务 8（依赖任务 1）
**审批队列耗时 baseline（只读数，不改造）**。可写：`bench/approval_queue/**`。
- 测 `list_for_tenant`（`app/repositories/approvals.py` 的 `list_for_tenant` 函数，其
  `stmt = (select(Approval, Task, Order)…)` 块中 `.order_by(…)` 首键是 `case(…)` 表达式——
  本轮已实读函数体复核；以「函数名 + 代码块」为锚，不用行号，普通 btree 服务不了）
  + 列表一次请求的 3 语句形态
  （list / `count_for_tenant` / `counts_for_tenant`）与 join 的耗时分布；
  为「未来是否做**查询形态**改造」提供前置读数。
- 红线：不得产出「加索引」结论（前提已被实测否掉）；不改任何 SQL。
- 验收：baseline.json + produced_by + 一句显式声明「仅读数，未做任何形态改造」；本模块
  `run.py` 以非零退出码断言本模块必需键（归属本任务，`validate.py` 按 per-module 期望表校验）。

### 任务 9（阻塞组：解锁条件见 dispatch.md）
**LLM 预算刹车**。
- 可写：`app/core/config.py`（新增 `llm_budget_enabled: bool = False`、
  `llm_budget_window_minutes: int = 60`、`llm_budget_max_tokens_per_window: int = 0`
  （0=不限）；环境变量 `LLM_BUDGET_*` 三行）、`app/core/llm_budget.py`（新增：读
  `llm_usage` 同表的窗口聚合，租户边界，纯查询——跨 worker 同表口径）、
  `app/agent/pydantic_agent.py`（调用**前**闸：越预算不发起请求，trace 固定原因码
  `budget_skipped`）、`app/agent/support_agent.py`（chat 缺路径接线，若刹车点选在
  brain 之外时才动）、`.env.example`、`tests/test_llm_budget.py`（新增）、
  `docs/fact-baseline.yaml` + README/PROJECT_SPEC 对应句（同 commit 定位同步
  `app_py_files` 148→149 与收集数增量——守卫级联，见 memory）。
- 测试先行（先红后绿）：默认关闭=查询集合与 trace 字段集合逐字等价；越预算=假模型
  驱动断言模型请求 0 次、只读问答回落规则读数、写动作不建任务；预算未越限=现行为。
- 红线：全局约束 7/8/9 全适用；`request_refund` 的 `requires_approval=True`
  （`app/tools/builtin.py` 的 `request_refund` ToolSpec 块，以 spec 名为锚，行号会漂移）与 ToolGateway 审批校验一字不改；刹车不是审批旁路。

### 任务 10（阻塞组）
**事件读取有界化**。
- 可写：`app/repositories/tasks.py`（`events()`（`:118-125`，现无 LIMIT、Python 切片）
  改为 SQL LIMIT + `(created_at, id)` keyset 游标，命中
  `ix_task_events_task_created`）、`app/services/runtime.py`（`follow_events`
  （`:1162-1220`）改分页循环取数；poll 0.5s（`:1168`）、心跳、idle_timeout 默认值不动）、
  既有事件相关测试文件 + 新增 `tests/test_task_events.py`（如尚无同名文件；新增即动
  `test_py_files`/`test_collected` 两事实→同 commit 同步 claims）、
  `docs/fact-baseline.yaml` + README/PROJECT_SPEC 对应句。
- 验收：等价性测试钉住「同一数据集新 SQL 游标输出 == 旧全量切片输出」——限定在**无重复
  时间戳**的数据集（已实测：`TaskEvent.created_at` 无唯一约束，`app/core/time.py` 的
  `utcnow()` 带微秒，同批行可同戳；现 `events()` 仅 `order_by(TaskEvent.created_at)`
  无 `id` 兜底，同戳行的相对次序本就不确定）；`id` 兜底按「对同 `created_at` 行的
  **确定化**」登记，重复戳数据集只断言行集一致、**不断言**逐条一致（与
  `specs/task-events/spec.md` 逐字同判据）；
  `after_id` 缺行返回空（语义不变）；单条 SELECT 带 LIMIT（读代码判定）；
  SQLite/PG 行值比较逐位一致（PG 若需展开为 OR 链，两库同序列）。
- 红线：跨 worker 同表订阅不变；不做 SSE 协议变更。

### 任务 11（阻塞组）
**测试 seed 去重**。
- 可写：`tests/conftest.py`（`:12,34`——app_factory 改「session 级模板 sqlite 文件
  跑一次 `seed_all`，function 级复制该文件再建 app」；对用例接口不变）、
  直接调用 seed 的测试模块（定位替换，仅调整建库方式，不改断言）。
- 禁写：`app/seed.py`（190 行、4 个 seeder，只读）；`app/main.py`。
- 验收：`-m pytest -q -o addopts=` 全量绿；改前/改后**当日**各跑一次全量墙钟，
  两个读数进证据（不许引用历史）；数据隔离类用例（如 `tests/test_refunds.py`
  余额断言 `:269,277,298`）不退化；若新增测试文件则同 commit 同步数字 claims。
- 风险：CI 仅 `python -m pytest`（`.github/workflows/ci.yml:19`），收益直接进 CI 墙钟；
  模板库+复制方案若引发 SQLite 句柄/临时文件问题，退回「session 级 engine +
  事务回滚」备选须在计划变更里申报。

### 任务 12（本波收尾轮；依赖任务 1-11 全部通过 reviewer）
**本波数字与文档统一同步**。
- 可写：`docs/**`（ADR-035+ 新写：预算刹车与联动代价、bench 口径裁决、时点读数
  不进守卫的理由、M 扫描/A/B/校准读数结论）、`README.md`、`CONTRIBUTING.md`、
  `docs/spec/TicketPilot项目说明书.docx`（重生成）、`docs/guide/证据索引.md`（追加本轮读数，
  标时点；本波新增小节须满足任务 4 的结构核对机械判据）、
  `.qoder/rules/benchmark-harness.md`（**口径文件已落地**（编排方 2026-10-27 裁定），
  本项不是「落草案」；本波收尾轮仅在本波读数或裁定需同步注记时对该既有文件做定位替换，
  不再新建第二份）、
  `openspec/changes/<本 change>/tasks.md` 勾选（organizer 执行）。
- 门禁十项（当日输出进证据）：`python -m pytest -q`、`ruff check app tests scripts
  evals alembic`（既有主命令行逐字不动；`bench` 由任务 2 的独立 `lint-bench` 目标与
  CI 独立步骤覆盖，合并进主命令行属口径变更、口径 §2.2 原文未点名轮次、由本波任务 12 另议）、`python -m mypy app`、`evals/run.py`、
  `evals/support_run.py`、`evals/retrieval_metrics.py`、`scripts/build_intent_set.py
  --check`、`evals/intent_metrics.py --enforce`、`evals/replay.py --self-check`、
  `alembic check`；涉前端再加 `npm run build`。
