# 实施计划与分派单

## 目标

把本轮分诊出的两项已登记欠账收口：

1. **T-A**：为「rerank 默认关闭 + OVER BUDGET 定性未被改写」补一条可执行守卫用例，并把口径文件的
   两句「当前缺口」收口为「**部分补钉**」（口径现写：「部分补钉（2026-11；2026-11-03 独立审查后由「已补钉」收窄）」；
   其中「已补钉」仅作为**被收窄的旧称**被引用，现状态是部分补钉）。
2. **T-B**：把 `analytics.route` 无同源耗时起点这件事，**显式登记为缺口**（零生产代码改动），
   满足口径 R9 的「二选一必须说清」要求。

## 架构 / 技术栈

- 技术栈：Python 3 + pytest（只读断言用例）；Markdown 口径文档；无迁移、无依赖变化。
- 架构影响：**无**。两项都不改产品代码，不新增配置项、trace 键、span、表、事件。
  分层守卫（`docs/architecture-guard.yaml`）读数不变。

## 规格路径

- 本变更**不写** `specs/<capability>/spec.md` 增量（两项均不改变对外行为或运行时契约，理由见 `tasks.md` 首节）。
- 本轮的「规格」落点是两份项目级口径文件的定点收口：
  `.qoder/rules/benchmark-harness.md`（§5.1 末段、§7 第 8 条）、
  `.qoder/rules/observability-span-and-link.md`（§4.8.c、§5 第 1 条）。
- 前置文档：`proposal.md`（口径来源、实测事实、三条编排裁定）。

## 分派形态

两项边界不重叠、无相互依赖，可并行派给两个 executor；每项实现后各自提交 reviewer。
真源见本文件末尾状态表；本变更的任务状态最终要回填 `docs/下一步规划.md`，
但**只能由 reviewer 通过后**回填（`task-order.md` §1）。

任务编号：`### 任务 1` … `### 任务 9`，全局连续，与 `tasks.md` 的勾选项一一对应。

---

## 全局约束（每份分派简报都必须逐字带上）

1. **禁止整文件覆盖**。工作树已有未提交的收尾 re-cite 批次：
   `README.md`、`docs/spec/项目规格.md`、`docs/spec/TicketPilot项目规格说明书.docx`、
   `docs/spec/manual-build.json`、`tests/test_question_coverage_reading.py`。
   需要动这些文件时**只做定位替换（Edit）**；发现落点已被他人改写就先回报，不要强行落笔。
2. `.review/` 与 `.workbuddy/` 是旁支目录（二者均**未被 `.gitignore` 覆盖**，实测 `git check-ignore` 无命中）：
   **不得**扫进任何提交、不得删除。
3. **解释器一律用 venv**：`./.venv/Scripts/python.exe`。裸 `python` 是 Anaconda，读数会假。
4. **不得跑全量 pytest**（全量基线只跑一次，属收尾轮）；**不得跑 `evals/**` 的模型读数**。
   只跑本任务新增/涉及的用例与 `tests/test_trace.py`（若涉及 span 形状）。
5. **不得为了变绿去改**三份守卫清单或文档数字。
6. 交付说明必须附**本轮真实命令输出**；行号一律以符号名/字符串共现为主（口径文件行号引用无机器核对）。
7. 只做「已完成/自检/提交审查」，不得自行宣布通过。
8. 运行时副产物只允许落在已被 `.gitignore` 覆盖的路径（实测 `.gitignore:7` 覆盖 `.pytest_cache/`），
   或仓外临时目录；不得在仓内新建散装证据文件目录。

---

## T-A：补钉「rerank 默认关闭 + OVER BUDGET 定性」守卫

**责任 Agent**：executor　**依赖**：无（与 T-B 并行）
**写文件面**：`tests/` 下新增 1 个文件 + `.qoder/rules/benchmark-harness.md` 两处定点替换
**读文件面**：`app/core/config.py`、`.env.example`、`evals/rerank_latency.py`、`README.md`、
`docs/下一步规划.md`（**只读**）
**非目标（整个 T-A）**：不打开 rerank、不改任何默认值、不改 `evals/rerank_latency.py` 的判定逻辑、
不新增配置项、不跑模型读数、不改文档句子。

### 任务 1：复核五项现状锚点

- **目标**：把五个现状落点的**本轮实测行号**写进交付说明，作为后续任务与 reviewer 对照的底座。
- **受影响范围**：无文件写入，只产出读数。
- **要复核的五项**（口径旧行号一律不得照抄）：
  1. `app/core/config.py` 的 `retrieval_rerank_enabled: bool = False`（本轮实测 `:76`，口径写 `:70` 已漂）。
  2. `.env.example` 的整行 `RETRIEVAL_RERANK_ENABLED=false`（本轮实测 `:59`；主会话先前「grep 未命中」
     系工具默认跳过点文件，属误报）。
  3. `evals/rerank_latency.py` 的 verdict print（本轮实测 `:249`，含 `'OVER BUDGET'` 字面量）。
  4. `README.md` 中 `OVER BUDGET` 与 `默认关闭` 同行共现的行（本轮实测 `:200`、`:428`）。
  5. `docs/下一步规划.md` 中同行共现的行（本轮实测 `:2529`；口径写 `:290` 已漂）。
- **验收标准**：五项逐一给 `文件:行` + 命中该行的命令；结论与 `proposal.md` 实测表一致，不一致则回报差异。
- **证据要求**：`grep -n` 原始输出粘贴进交付说明。
- **验证方式命令**：
  ```
  grep -n "retrieval_rerank_enabled: bool" app/core/config.py
  grep -n "RETRIEVAL_RERANK_ENABLED" .env.example
  grep -n "OVER BUDGET" evals/rerank_latency.py
  grep -nE "OVER BUDGET" README.md docs/下一步规划.md
  ```

### 任务 2：新增守卫用例（四条断言）

- **目标**：新增 `tests/test_rerank_default_off_and_over_budget_qualitative.py`（文件名可自定，语义等价即可），
  覆盖口径 §5.1 禁区 1 的两件事：默认关闭 + 文档定性未被改写。
- **非目标**：不 import、不执行 `evals/rerank_latency.py`；不写死行号；不新增第二个测试文件。
- **实现要求**（全部只读文件/只读配置，零模型调用、零副作用）：
  1. **配置默认值**：`Settings()` 默认实例的 `retrieval_rerank_enabled` 为假（以符号名为准）。
  2. **环境模板**：`.env.example` 中存在**整行** `RETRIEVAL_RERANK_ENABLED=false`（按内容匹配）。
  3. **verdict 串仍在**：`evals/rerank_latency.py` 源码文本里仍能检索到 `OVER BUDGET` 字面量。
  4. **文档定性未被改写**（正向 + 反向两半都要有）：
     - 正向：`README.md` 与 `docs/下一步规划.md` 中**至少各有一行**同时含 `OVER BUDGET` 与 `默认关闭`。
       本轮实测共现行：`README.md:200`、`README.md:428`、`docs/下一步规划.md:2529`——
       断言必须按**字符串共现**写，**不得**写死行号（README 工作树在改，行号已漂过一次）。
     - 反向：被命中的那些行**不得**出现「已优化」「延迟可接受」这两类改写措辞。
- **验收标准**：四条断言逐条对应 1-4；文件为纯只读断言；无 `time.sleep`、无网络/模型调用。
- **证据要求**：A-2 的 pytest 输出 + 用例函数清单。

### 任务 3：负验证（临时改红再原样收回）

- **目标**：证明用例真的会红，不是恒真断言。
- **非目标**：验证痕迹**不得**随交付落盘（口径 §3-3「不扩面」同向）。
- **实现要求**：
  (a) 临时把 `Settings` 默认值改真 → 用例变红；(b) 临时把 README 某行的「默认关闭」删掉 → 用例变红；
  两步做完后**原样收回**。
- **验收标准**：两次红都留原始输出；收回后 `git diff -- app/core/config.py` 恰好为空；`README.md` 与 `docs/下一步规划.md` 的 `git diff` 与本轮开始时**逐字相同**（不得为变绿而回退其它批次的改动，禁止 `git checkout` / `git restore` / `git stash` / `git clean`）。
- **证据要求**：两段失败输出 + `git diff --stat` 收回后的结果。
- **验证方式命令**：
  ```
  ./.venv/Scripts/python.exe -m pytest tests/test_rerank_default_off_and_over_budget_qualitative.py -q -o addopts=""
  git diff -- app/core/config.py README.md
  ```

### 任务 4：`benchmark-harness.md` 两句「当前缺口」收口

- **目标**：把 §5.1 末段与 §7 第 8 条的「当前缺口」改为「**部分补钉**」并登记三条残留缺口（口径现写：「部分补钉（2026-11；2026-11-03 独立审查后由「已补钉」收窄）」，「已补钉」只作旧称引用），并写出真实用例文件名与用例函数名。
- **非目标**：**只改这两句**，不动其他段落、不改条款编号、不改文档里的任何时点读数。
- **实现要求**：
  - §5.1 末段「**当前缺口**：仓库内**没有**一条专门钉……是否补钉由实现轮按最小范围决定。」→ 部分补钉 + 用例名 + 三条残留缺口。
  - §7 第 8 条末尾「——**部分可钉住**：配置默认值可由用例钉；「文档定性未被改写」当前**缺口**。」→
    四条均已由该用例钉住 + 用例文件名；同时保留「时点读数需带日期、不改定性」的原意。
  - 在相应位置登记一句：**口径文件行号引用无机器核对**（锚点守卫只扫 `docs/**/*.md`，§2.5 既有口径）。
- **验收标准**：diff 只含上述两处/三处行级改动；无整文件重写痕迹。
- **证据要求**：`git diff -- .qoder/rules/benchmark-harness.md` 逐字粘贴。

### 任务 5：最小范围门禁 + 级联读数登记

- **目标**：跑通本任务涉及的最小门禁，并把必然变红的两条事实读数**登记而不修**。
- **非目标**：本轮**不得**改文档数字、**不得**改三份守卫清单、**不得**跑全量 pytest、
  **不得**跑 `tests/test_docs_consistency.py` 与三份守卫（它们必然因两条读数变红，属预期）。
- **期望读数（本轮实测，供对照）**：
  - 新增前：`pytest --collect-only -q -o addopts=""` → **1378 tests collected**；`ls tests/*.py | wc -l` → **89**。
  - 新增后（该文件含 N 条用例）：`test_collected` = 1378 + N，`test_py_files` = 90。
- **验收标准**：用例通过、ruff 通过、mypy 通过；交付说明写明两条读数的漂移与「留收尾轮 re-cite」
  （`task-order.md` §4）。
- **证据要求**：三条命令的本轮实际输出（含用例数 N 的实测值）。
- **验证方式命令**：
  ```
  ./.venv/Scripts/python.exe -m pytest tests/test_rerank_default_off_and_over_budget_qualitative.py -q -o addopts=""
  ruff check app tests scripts evals alembic
  ./.venv/Scripts/python.exe -m mypy app
  ```

### T-A 禁止事项

改 `app/core/config.py` 默认值 / 改 `evals/rerank_latency.py` / 改 `.env.example` /
改三份守卫清单 / 改 `docs/spec/项目规格.md` 或 `README.md` 的任何句子 / 新增配置项、迁移、依赖 /
新增 trace 键、span、表、事件 / 跑 evals 模型读数 / 跑全量 pytest / 断言里写死行号。

---

## T-B：把 `analytics.route` 缺耗时起点显式登记为缺口（零生产代码改动）

**责任 Agent**：executor　**依赖**：无（与 T-A 并行；写文件面不相交）
**写文件面**：`docs/下一步规划.md` §14.6 一处追加 + `.qoder/rules/observability-span-and-link.md` 定点补注
+ 交付说明
**读文件面**：`app/sql/service.py`、`app/observability/tracing.py`（**只读，一行不改**）
**非目标（整个 T-B）**：**不改 `app/sql/service.py` 的任何一行**（包括不给 `_trace_route` 加 `started` 形参、
不补 `latency_ms=`）；不改 `app/observability/tracing.py`；不动 §2.1 第 3 条保护的三处 `duration_ms` 算式；
不碰 `docs/spec/项目规格.md` §16.6（在途 re-cite 批次正在改）。

**本任务属 `openspec/AGENTS.md` §8「证据型变更」**：只产出读数与文档登记，不改产品代码，
且运行在脏工作树上，因此自述不得写成已核事实——必须落盘树状态快照与范围声明（见任务 7）。

### 任务 6：同源形状复核（含停止回报条款）

- **目标**：executor 自行复核「四处无法同源」是否成立，成立才继续登记；不成立就停下回报。
- **要复核的事实**（行号为本轮工作树读数，必须自行复核后落笔）：
  1. `analytics.route` 由 `_trace_route` 产出（`app/sql/service.py:354-376`，`trace.add` 在 `:365-375`），
     该 `add` **不传** `latency_ms`，因此 `trace_spans.latency_ms` 取默认 0。
  2. 四个调用点：`:120`、`:138`、`:166`、`:305`。前三处作用域内**没有任何路由段起点**
     （`:202` 的 `started` 在 `:166` 之后且量的是执行段）。
  3. 第四处 `:305` 的起点 `:285` 量的是 prefetch 构建段（`:286-288`），且同一段时长已由
     `analytics.query`（`:326` 的 `latency_ms=duration_ms`）与 `analytics.prefetch`（`:339`）各记一次。
  4. **结论**：四处无法统一成同源形状——若把 `:305` 的窗口拉到路由段起点，会把 prefetch 段并入
     「路由」读数并与另两个 span 重复计量，违反 §2.1 第 4 条与 §1.1「段」的定义；
     若各点各取各的起点，则得到不同源的数，正中 §2.1 第 7 条禁令。故本轮**登记缺口、不硬编读数**。
- **停止条件**：**若复核后认为能同源**（找到一条不混段、不重复计量的形状），停下回报 organizer，
  不要自行改生产代码，本任务改回路线一重新分派。
- **验收标准**：交付说明逐条给 `文件:行`，且与 `proposal.md` 的调用点表一致。
- **证据要求**：`Read` 出的关键行片段（`_trace_route` 的 `trace.add`、四处调用、两处 `started`）。

### 任务 7：grep 判据实测 + 树状态前后快照 + 零改动证明

- **目标**：证明本任务不破坏 §3 第 4 条的一级 grep 判据，并固定「本轮只改了哪些文件」。
- **非目标**：不修改任何生产代码。
- **实现要求**：
  1. 一级 grep 应为 4 处：`tracing.py` 的 `elapsed_ms` 函数体 1 处 + `app/sql/service.py` 三处 `duration_ms`；
     `app/services/runtime.py` 零命中。宽 grep 应为 8 处（一级 4 + 二级浮点 4）。
  2. 按 §8.1 落**树状态快照**：写入前与写入后各一份 `git status --short` + `git diff --stat`，**逐字粘贴**；
     不得只写结论。
  3. 按 §8.1 落**范围声明**：声明本轮是否改过既有文件；脏树下的自述只能标为自述。
  4. 读数产物落**仓外临时目录**，并记文件名、字节数、mtime、生成命令，写明「不入库、归档后不可复看」。
- **验收标准**：`git diff -- app/` 输出为空；两个 grep 命中数与上表一致；前后快照齐全。
- **证据要求**：命令原文 + 输出原文 + 快照原文。
- **验证方式命令**：
  ```
  grep -rn "int((time.perf_counter() - started)" app/
  grep -rn "perf_counter() - started" app/
  git diff -- app/
  git status --short
  git diff --stat
  ```

### 任务 8：`docs/下一步规划.md` §14.6 追加缺口登记行

- **目标**：在 R9 段末尾追加一条「已知缺口（2026-11 显式登记）」的行，内容即任务 6 第 1-4 点的压缩版 + `文件:行`。
- **非目标**：不整文件覆盖；不改 §0.2 执行清单里 R9 行的状态（状态只能由证据给出，`task-order.md` §1）；
  不改文档里的其他事实读数。
- **落点与红线**：
  - 落点 `### 14.6 观测与闭环`（本轮实测 `:2646`），**只做定位插入**。该文件工作树当前干净，仍按最小改动执行。
  - 插入位置必须在已登记锚点行 `:30`「当前规模事实」与 `:119`「| 测试 |」**之后**（§14.6 在 2646，天然满足）。
  - 新增句子**不得复现**「当前规模事实」「| 测试 |」等已登记锚点句（整行唯一命中会被打破）。
  - 措辞必须可判定：写「四个调用点里三处无起点、一处起点与其余不同源，故 `analytics.route` 无耗时读数」
    这类带 `文件:行` 的断言，**不得**写成「后续优化」「待优化」这类模糊话。
- **验收标准**：新行存在且唯一；`git diff` 只含该处插入；锚点行未被触碰。
- **证据要求**：`git diff -- docs/下一步规划.md` 逐字粘贴 + `grep -n "当前规模事实" docs/下一步规划.md`
  与 `grep -n "| 测试 |" docs/下一步规划.md` 仍各为 1 处命中。

### 任务 9：`observability-span-and-link.md` §4.8.c 与 §5 第 1 条定点补注

- **目标**：把「要么……要么登记为缺口」的二选一**收口为「本轮采纳后者」**，写明判定理由与登记落点。
- **非目标**：不新增段落、不改其他条款、不改 §2.1/§3 的判据表述、不改 §2.1 第 7 条本身。
- **实现要求**：两处各一次定点补注；同时登记「口径文件行号引用无机器核对」。
- **验收标准**：diff 只含 §4.8.c 与 §5 第 1 条两处；结论与任务 8 的文档行一致（术语、落点、理由三处对齐）。
- **证据要求**：`git diff -- .qoder/rules/observability-span-and-link.md` 逐字粘贴。

### T-B 禁止事项

改 `app/**` 任何一行 / 给 `_trace_route` 加形参或写 `latency_ms=0` / 改 `evals/**` /
改三份守卫清单 / 整文件覆盖 `docs/下一步规划.md` 或 `docs/spec/项目规格.md` /
改 §16.6 观测面（在途批次在做）/ 把 R9 标成「完成」/ 跑全量 pytest / 跑 evals 模型读数 /
把缺口写成「后续优化」这类模糊措辞。

---

## 并行边界声明

| 文件 | T-A | T-B |
|---|---|---|
| `tests/` 新增文件 | **写** | 不碰 |
| `.qoder/rules/benchmark-harness.md` | **写** | 不碰 |
| `.qoder/rules/observability-span-and-link.md` | 不碰 | **写** |
| `docs/下一步规划.md` | 只读（字符串共现断言） | **写**（§14.6 追加，`:2529` 不受影响） |
| `app/**`、`evals/**`、`.env.example`、三份守卫清单 | 只读 | 只读 |

写面不重叠；T-A 对 `docs/下一步规划.md` 的断言按字符串共现而非行号，
故 T-B 在 §14.6（`:2529` 之后）追加不会与 T-A 的用例相互影响。两项可并行。

## 审查重点（提交 reviewer 时逐条对照）

1. T-A：四条断言是否逐条对应、是否写死行号、是否新增/改写了文档句子、负验证痕迹是否已收回。
2. T-A：`benchmark-harness.md` 是否**只**改了那两句（+一句登记），其余零改动。
3. T-B：`git diff -- app/` 是否真为空；grep 判据是否仍 4/8。
4. T-B：登记句是否可判定（带 `文件:行`、无模糊措辞）；§0.2 R9 状态是否未动；锚点句是否未复现。
5. 两项：级联漂移是否**只登记未修**（`test_py_files` 89→90、`test_collected` 1378→1378+N），
   是否误跑全量基线或改守卫清单。

---

## 状态表（返工跟踪）

| 任务编号 | 责任 Agent | 状态 | 依赖 | 审查轮次 | 阻塞原因 |
|---|---|---|---|---|---|
| T-A（任务 1-5） | executor | 已通过（第 3 轮 reviewer 通过，无阻塞/严重项） | 无 | 3 | 一般项随批登记：①共现行内层逐行断言恒真、不承载判定，升格批须删除或改为可证伪 ②口径 §5.1「部分补钉」段仍描述旧的两份文件+存在性判据，升格批须重写否则升级为严重 ③守卫变红时先区分禁区破坏与合法重排，不得直接放宽 DOC_CO_OCCURRENCE_BASELINE |
| T-B（任务 6-9） | executor | 待审查（2.1–2.4 实现已落盘，见 evidence.md 补件二） | 无 | 0 | 仅待送审，非待派工；2.5 未勾 |

状态取值：`待分派 / 进行中 / 待审查 / 退回返工 / 已通过 / 阻塞 / 取消`。
只有 reviewer 给出「通过」才可改「已通过」；organizer 不得自审代替 reviewer，不得宣布任务完成。
同一任务连续退回两轮即升级（缩小范围 / 补口径 / 转人工），不得无限循环。
