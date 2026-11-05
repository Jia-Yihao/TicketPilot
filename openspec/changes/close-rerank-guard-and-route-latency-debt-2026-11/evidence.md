# 证据补件：读数产物 + 树状态快照 + 范围声明

- 变更：`close-rerank-guard-and-route-latency-debt-2026-11`（对应 plan.md 任务 7，`plan.md:186`）
- 轮次性质：§8 证据型变更（`openspec/AGENTS.md:134`）——只产出读数与证据文档、不改产品代码；
  在脏工作树上运行，`git status` 无法区分本轮改动与更早轮次累积（`openspec/AGENTS.md:136-138`），
  因此本文所有无法用可核方法独立验证的归属/结论一律标注**自述**。
- 本补件文档由 executor 于 2026-11-02 创建（本轮唯一仓内新增文件，见 §3.1 完整清单）。
- 落盘时刻基线：`HEAD = 63c6d306c1dc3a39a1e3c4d2d37ff3e1cccb0848`（分支 master，未推送）。

## 0. 锚点逐字核对记录（§8.2 第 6 条，`openspec/AGENTS.md:156`）

以下 `文件:行` 均已于 2026-11-02 逐字打开核对该行确实承载所述口径：

| 出处 | 承载内容 |
|---|---|
| `openspec/AGENTS.md:134` | `## 8. 证据型变更的产物与自证` |
| `openspec/AGENTS.md:139` | `### 8.1 必须落盘的三类产物` |
| `openspec/AGENTS.md:143` | 读数产物行：仓外临时目录或 gitignore 覆盖路径；记文件名/字节数/mtime/生成命令；仓外须写「不入库、归档后不可复看」 |
| `openspec/AGENTS.md:144` | 树状态快照行：变更目录内、前后各一份 `git status --short` + `git diff --stat` 逐字粘贴 |
| `openspec/AGENTS.md:145` | 范围声明行：能证附证据，不能证明写「自述、当前树状态无法独立复核」 |
| `openspec/AGENTS.md:149-156` | §8.2 六条硬性边界（①唯一新增、②前后一致、③范围不自证、④冲突登记、⑤读数标时点、⑥锚点逐字核对） |
| `plan.md:186` | 任务 7 标题「grep 判据实测 + 树状态前后快照 + 零改动证明」 |
| `plan.md:191-192` | 一级 grep 应 4 处、`app/services/runtime.py` 零命中、宽 grep 应 8 处 |
| `plan.md:193-194` | 快照前后各一份、逐字粘贴、不得只写结论 |
| `plan.md:195` | 范围声明；脏树自述只能标为自述 |
| `plan.md:196` | 读数产物落仓外临时目录并记四项元数据 + 「不入库、归档后不可复看」 |
| `plan.md:197` | 验收：`git diff -- app/` 为空、两 grep 命中数一致、前后快照齐全 |
| `plan.md:199-206` | 验证方式命令块原文 |
| `.gitignore:7` | `.pytest_cache/`（覆盖 pytest 副产物） |
| `.gitignore:8` | `.pytest_tmp/`（覆盖 pytest 副产物） |

## 1. 读数产物（§8.1 第 1 类，`openspec/AGENTS.md:143`）

**位置**：仓外临时目录
`C:/Users/15237/AppData/Local/Temp/ticketpilot-close-rerank-evidence-2026-11/`
（Git Bash 下表示为 `/tmp/ticketpilot-close-rerank-evidence-2026-11/`）。

> **不入库、归档后不可复看。**（§8.1 要求的显式声明）

### 1.1 文件清单（文件名 / 字节数 / mtime / 生成命令）

字节数与 mtime 于 2026-11-02 13:19 前后由 `stat -c '%n %s bytes mtime=%y'` 采集，为时点读数。

| 文件名 | 字节数 | mtime（+0800） | 生成命令（cwd 均为 `D:/Code/TicketPilot`，解释器均为 `./.venv/Scripts/python.exe` 或仓库 Git） |
|---|---|---|---|
| `pytest-rerank-guard.txt` | 276 | 2026-11-02 13:16:47.694 | `./.venv/Scripts/python.exe -m pytest tests/test_rerank_default_off_and_over_budget_qualitative.py -q` |
| `pytest-question-coverage.txt` | 255 | 2026-11-02 13:18:14.517 | `./.venv/Scripts/python.exe -m pytest tests/test_question_coverage_reading.py -q` |
| `grep-narrow.txt` | 525 | 2026-11-02 13:18:19.731 | `grep -rn "int((time.perf_counter() - started)" app/` |
| `grep-wide.txt` | 904 | 2026-11-02 13:18:20.054 | `grep -rn "perf_counter() - started" app/` |
| `gitdiff-app.txt` | 132 | 2026-11-02 13:18:29.425 | `git diff -- app/` |
| `gitdiff-guard.txt` | 203 | 2026-11-02 13:18:29.582 | `git diff -- docs/fact-baseline.yaml docs/code-anchors.yaml docs/architecture-guard.yaml` |

每个文件内部首部均含 `command:` / `cwd:` / `captured-at:` 三行头，输出为命令原始 stdout+stderr。

### 1.2 本轮真实读数原文（2026-11-02，13:16–13:18 窗口）

`pytest-rerank-guard.txt` 输出段原文（captured-at: 2026-11-02 13:16:45 +0800）：

```
....                                                                     [100%]
exit: 0
```

`pytest-question-coverage.txt` 输出段原文（captured-at: 2026-11-02 13:18:07 +0800）：

```
........                                                                 [100%]
exit: 0
```

`grep-narrow.txt` 输出段原文（captured-at: 2026-11-02 13:18:19 +0800，命中计数 4）：

```
app/observability/tracing.py:47:    return int((time.perf_counter() - started) * 1000)
app/sql/service.py:216:                duration_ms=int((time.perf_counter() - started) * 1000),
app/sql/service.py:222:        duration_ms = int((time.perf_counter() - started) * 1000)
app/sql/service.py:289:        duration_ms = int((time.perf_counter() - started) * 1000)
exit: 0
```

`grep-wide.txt` 输出段原文（captured-at: 2026-11-02 13:18:19 +0800，命中计数 8）：

```
app/agent/analytics_narrator.py:107:                latency_ms=(time.perf_counter() - started) * 1000,
app/agent/analytics_narrator.py:117:            latency_ms=(time.perf_counter() - started) * 1000,
app/agent/pydantic_agent.py:412:            latency_ms = (time.perf_counter() - started) * 1000
app/agent/pydantic_agent.py:421:        latency_ms = (time.perf_counter() - started) * 1000
app/observability/tracing.py:47:    return int((time.perf_counter() - started) * 1000)
app/sql/service.py:216:                duration_ms=int((time.perf_counter() - started) * 1000),
app/sql/service.py:222:        duration_ms = int((time.perf_counter() - started) * 1000)
app/sql/service.py:289:        duration_ms = int((time.perf_counter() - started) * 1000)
exit: 0
```

`gitdiff-app.txt` 与 `gitdiff-guard.txt` 的 `---output---` 段均为**空**（输出 0 字节，exit 0）——
即 `git diff -- app/` 为空（`plan.md:197` 验收项）、三份守卫清单
（`docs/fact-baseline.yaml`、`docs/code-anchors.yaml`、`docs/architecture-guard.yaml`）本轮零改动。

`app/services/runtime.py` 零命中：由 `grep-narrow.txt`/`grep-wide.txt` 输出中不含该路径直接可见。

### 1.3 历史时点读数（保留，不静默改成现状值；§8.2 第 5 条，`openspec/AGENTS.md:155`）

- 上游分派单引用历史读数：第一条命令曾读作 `4 passed in 0.03s`；第二条命令曾读作 `........ [100%]`（8 条）。
  该历史读数的具体时点未随分派单给出，**只能标为「历史读数、时点为上游自述」**。
- 现状值另起注明：**此为 2026-11-02 13:16/13:18 读数**（见 §1.2 原文）。本轮 `-q` 实跑输出中
  **没有出现 `4 passed in 0.03s` 汇总行**（只有 4 个点号 + `[100%]` + exit 0）。差异已登记 §4 待办第 1 条，
  本轮不改动任何既有文档数字（全局禁区 5、§8.2 第 4 条）。

## 2. 树状态快照（§8.1 第 2 类，`openspec/AGENTS.md:144`；`plan.md:193-194`）

采集命令（cwd `D:/Code/TicketPilot`，前后两次完全相同、命令本身不修改任何文件）：

```
git status --short
echo "=====DIFFSTAT====="
git diff --stat
```

### 2.1 运行前快照（逐字粘贴，原文由主会话于本 executor 开工前采集）

> 搬运说明：本 executor 只是搬运者，未参与该半份的采集；采集发生在本轮任何写入之前。
> 搬运保真性说明：分派单转写的第 1 行 `M .qoder/rules/benchmark-harness.md` 丢失了行首一个空格
> （`git status --short` 对「未暂存修改」输出为 ` M`，前导一空格）。executor 在动笔写任何文件之前
> 用同一命令序列自行采集过一份，除第 1 行前导空格与 CRLF warning 换行位置外，与下述原文逐行一致。
> 以下按分派单原文逐字粘贴（含该转写缺损），不做修正：

```
M .qoder/rules/benchmark-harness.md
 M .qoder/rules/observability-span-and-link.md
 M README.md
 M "docs/spec/TicketPilot\351\241\271\347\233\256\350\257\264\346\230\216\344\271\246.docx"
 M docs/spec/manual-build.json
 M "docs/spec/\351\241\271\347\233\256\350\257\244\350\210\262.md"
 M "docs/\344\270\200\346\255\245\350\247\204\345\210\222.md"
 M tests/test_question_coverage_reading.py
?? .review/
?? .workbuddy/
?? openspec/changes/close-rerank-guard-and-route-latency-debt-2026-11/
?? tests/test_rerank_default_off_and_over_budget_qualitative.py
=====DIFFSTAT=====
warning: in the working copy of '.qoder/rules/benchmark-harness.md', CRLF will be replaced by LF the next time Git touches it
warning: in the working copy of 'docs/下一步规划.md', CRLF will be replaced by LF the next time Git touches it
 .qoder/rules/benchmark-harness.md                  |  18 +++++++++++++-----
 .qoder/rules/observability-span-and-link.md        |  15 +++++++++++++++
 README.md                                          |  12 ++++++------
 ...3\256\350\257\264\346\230\216\344\271\246.docx" | Bin 304163 -> 304903 bytes
 docs/spec/manual-build.json                        |   4 ++--
 ...241\271\347\233\256\350\257\244\350\210\262.md" |   8 ++++----
 ...270\200\346\255\245\350\247\204\345\210\222.md" |   2 ++
 tests/test_question_coverage_reading.py            |   8 +++++---
 8 files changed, 47 insertions(+), 20 deletions(-)
```

### 2.2 运行后快照（executor 于本文档写入完成后用同一命令序列自行采集，逐字粘贴）

采集时刻：2026-11-02 13:20 +0800（本文档写入完成之后；最终复验命令于同刻 13:20:59 +0800 再次核对一致）。
第 1 行行首空格已用定界符复核：`git status --short | sed -n '1l'` 实测输出为
` M .qoder/rules/benchmark-harness.md$`（之前恰一空格）。

```
 M .qoder/rules/benchmark-harness.md
 M .qoder/rules/observability-span-and-link.md
 M README.md
 M "docs/spec/TicketPilot\351\241\271\347\233\256\350\257\264\346\230\216\344\271\246.docx"
 M docs/spec/manual-build.json
 M "docs/spec/\351\241\271\347\233\256\350\257\244\350\210\262.md"
 M "docs/\344\270\200\346\255\245\350\247\204\345\210\222.md"
 M tests/test_question_coverage_reading.py
?? .review/
?? .workbuddy/
?? openspec/changes/close-rerank-guard-and-route-latency-debt-2026-11/
?? tests/test_rerank_default_off_and_over_budget_qualitative.py
=====DIFFSTAT=====
warning: in the working copy of '.qoder/rules/benchmark-harness.md', CRLF will be replaced by LF the next time Git touches it
warning: in the working copy of 'docs/下一步规划.md', CRLF will be replaced by LF the next time Git touches it
 .qoder/rules/benchmark-harness.md                  |  18 +++++++++++++-----
 .qoder/rules/observability-span-and-link.md        |  15 +++++++++++++++
 README.md                                          |  12 ++++++------
 ...3\256\350\257\264\346\230\216\344\271\246.docx" | Bin 304163 -> 304903 bytes
 docs/spec/manual-build.json                        |   4 ++--
 ...241\271\347\233\256\350\257\244\350\210\262.md" |   8 ++++----
 ...270\200\346\255\245\350\247\204\345\210\222.md" |   2 ++
 tests/test_question_coverage_reading.py            |   8 +++++---
 8 files changed, 47 insertions(+), 20 deletions(-)
```

### 2.3 前后差异说明（不写「运行前后一致」，除非逐字相同——本次不满足，§8.2 第 2 条）

两份快照**不逐字相同**，差异点与原因如下，全部为预期内：

1. 前份第 1 行缺行首空格（`M .qoder/...` vs 后份 ` M .qoder/...`）——系分派单转写缺损（见 §2.1 搬运说明），
   非树状态变化；executor 开工前的自采对照份第 1 行即为 ` M`。
2. tracked 文件集合与 diffstat 数值：后份与前份**逐行相同**（8 个 M 文件、4 个 `??` 条目、
   `8 files changed, 47 insertions(+), 20 deletions(-)`）。本轮 executor 零改 tracked 文件，
   新建的 `evidence.md` 位于未跟踪目录 `openspec/changes/close-rerank-guard-and-route-latency-debt-2026-11/`
   内，`git status --short` 不展开未跟踪目录内部文件清单，故该目录行不变、无新增行。
3. CRLF warning 两行在前后份中均出现且内容相同。
4. 读数窗口内的外部改动：executor 无法证明采集窗口（13:16–13:19）内没有其他进程/会话改动 tracked 文件；
   前份快照由主会话采集、后份由 executor 采集，两者数值一致这一点**只能作为观察，不构成证明**（自述）。

## 3. 范围声明（§8.1 第 3 类，`openspec/AGENTS.md:145`；`plan.md:195`）

### 3.1 本轮新增路径的完整清单（不写「唯一新增产物」前先枚举，§8.2 第 1 条）

仓内新增（全部位于未跟踪的变更目录内）：

1. `openspec/changes/close-rerank-guard-and-route-latency-debt-2026-11/evidence.md`（本文件）

仓外新增（不入库，见 §1.1 清单）：

2. `.../ticketpilot-close-rerank-evidence-2026-11/pytest-rerank-guard.txt`
3. `.../ticketpilot-close-rerank-evidence-2026-11/pytest-question-coverage.txt`
4. `.../ticketpilot-close-rerank-evidence-2026-11/grep-narrow.txt`
5. `.../ticketpilot-close-rerank-evidence-2026-11/grep-wide.txt`
6. `.../ticketpilot-close-rerank-evidence-2026-11/gitdiff-app.txt`
7. `.../ticketpilot-close-rerank-evidence-2026-11/gitdiff-guard.txt`

副产物目录（本轮 pytest 触及、非新增、已被 `.gitignore` 覆盖，故不出现在 `git status --short`）：

8. `D:/Code/TicketPilot/.pytest_cache/` —— `.gitignore:7` 覆盖（`git check-ignore -v` 于 2026-11-02 实测命中）
9. `D:/Code/TicketPilot/.pytest_tmp/` —— `.gitignore:8` 覆盖（同上实测命中）

未发现其他新增路径；本轮未使用 `--basetemp` 等会产生额外目录的参数。

### 3.2 本轮是否改过既有文件

**本轮（本 executor）未修改任何既有文件、未修改任何 tracked 文件**：只做上述新建与一次仓外采集。
可核证据：`git diff -- app/` 输出为空（`gitdiff-app.txt`，0 字节输出）；三份守卫清单
`git diff` 输出为空（`gitdiff-guard.txt`，0 字节输出）；后份快照的 tracked diffstat 与前份逐行相同（§2.3）。

### 3.3 快照中他人改动的 tracked 文件点名（§8.2 第 2 条要求）

以下改动**不属于本轮 executor**，归属依据为上游分派单转述，**属自述、当前树状态无法独立复核**
（`git status` 在脏树上无法区分轮次，`openspec/AGENTS.md:136-138`）；改动时刻未随分派单给出，无法列具体时刻：

| tracked 文件（快照中 M） | 分派单给出的归属（自述） |
|---|---|
| `.qoder/rules/benchmark-harness.md`、`.qoder/rules/observability-span-and-link.md`、`docs/下一步规划.md`、`tests/test_rerank_default_off_and_over_budget_qualitative.py`（`??` 新增） | 本轮早前 T-A / T-B executor 的收口改动 |
| `README.md`、`docs/spec/项目规格.md`、`docs/spec/manual-build.json`、`docs/spec/TicketPilot项目规格说明书.docx`、`tests/test_question_coverage_reading.py` | 在途 R10/R12 re-cite 批次，非本轮作者 |

另点名（非 tracked、非本轮、不得处置）：

- `.review/`、`.workbuddy/`：只读风险审查轨产物。实测 `git check-ignore .review/ .workbuddy/` **零命中**
  （2026-11-02，exit 1，即未被忽略）——**禁止提交、禁止删除**，本轮未触碰。

### 3.4 读数窗口判定

上述他人改动是否落在本轮读数窗口（2026-11-02 13:16–13:19）内：executor 无独立可核方法判定，
**只能写为「无法判定（自述）」**；前/后快照 tracked 部分逐行一致（§2.3 第 2 点）是窗口内 tracked
文件未被进一步改动的唯一旁证，且它本身受 §2.3 第 4 点的自述限制。

## 4. 待办：与既有记录的冲突登记（只登记、不同轮改，§8.2 第 4 条，`openspec/AGENTS.md:153-154`）

1. **pytest `-q` 汇总行差异**
   - 冲突处：上游分派单引用的历史读数「`4 passed in 0.03s`」；本轮原文读数见本文 §1.2
     （`pytest-rerank-guard.txt` 输出段仅有 `....  [100%]` + `exit: 0`，无汇总行）。
   - 需要改成的方向（不写具体新数字）：历史读数记录应注明该汇总行在 `-q` 配置下并非稳定出现，
     或将历史读数改述为「4 条通过（以点号/退出码为凭）」并把汇总行标注为某次非 `-q` 或不同 addopts 时点的读数。
   - 处置：本轮未改任何文档，留待 reviewer/organizer 定夺。
2. **分派单转写快照的首行前导空格缺损**
   - 冲突处：分派单所附 before 快照原文第 1 行 vs `git status --short` 实际输出格式（` M`）。
   - 方向：归档该快照的上游文档若继续引用，应注明「第 1 行前导空格系转写缺损，实际输出含之前空格」。
   - 处置：已在本文 §2.1 以搬运说明形式登记，未改任何既有文件。

---

# 补件二：T-B 取证（2026-11-04）

对应 `tasks.md` 2.1–2.4。T-B 为零生产代码改动，§8.1 的「读数产物」一类缺省（无可执行产物），
证据由代码只读复核、门禁 grep 读数、树状态快照与范围声明构成。

## 5. 同源形状复核（2.1）

复核对象 `app/sql/service.py::_trace_route`：定义在 `:354`，`trace.add(` 自 `:365` 起，
**不传 `latency_ms`**，span 取 `app/observability/tracing.py::elapsed_ms` 之外的默认 0。
四个调用点逐点判定：

| 调用点 | 所处路径 | 作用域内的路由段起点 | 判定 |
|---|---|---|---|
| `:120` | `plan is None` 拒答分支 | 无。拒答路径不做路由工作 | 无起点 |
| `:138` | `dataset is None`（`UNKNOWN_TABLE`）分支 | 无。决策构造后立即落 span | 无起点 |
| `:166` | 正常路径（非 `PREFETCH`） | 无。`:202` 的 `started` 晚于本调用，且量的是执行段 | 无起点 |
| `:305` | `_prefetch_answer` | 有，`:285`，量的是 `prefetch.build`（`:286-288`） | 起点不同源 |

第四处重复计量的核实：`duration_ms`（`:289`）已被 `analytics.query`（`:326` 的
`latency_ms=duration_ms`）与 `analytics.prefetch`（`:339`）各取一次。把 `started` 上提到路由段
起点会把 `prefetch.build` 段并入「路由」读数，与上述两个 span 重复计量（违反口径 §1.1「段」定义、
§2.1 第 4 条）；各点各取起点则不同源（§2.1 第 7 条禁令）。两条路都不可走。

**结论：与 `tasks.md` 2.1 的登记一致——四处无法统一成同源形状。** 2.1 的「若能同源则停下回报」
条件未触发；生产代码零改动。

## 6. 门禁读数（2.2）

一级 `grep -rn "int((time.perf_counter() - started)" app/` → **4 处**：

```
app/observability/tracing.py:47:    return int((time.perf_counter() - started) * 1000)
app/sql/service.py:216:                duration_ms=int((time.perf_counter() - started) * 1000),
app/sql/service.py:222:        duration_ms = int((time.perf_counter() - started) * 1000)
app/sql/service.py:289:        duration_ms = int((time.perf_counter() - started) * 1000)
```

宽 `grep -rn "perf_counter() - started" app/` → **8 处**（一级 4 + 二级 4：
`app/agent/analytics_narrator.py` 2 处、`app/agent/pydantic_agent.py` 2 处，均不在 span 耗时路径上）。

`git diff --stat -- app/` → 空。`git status --short -- app/` → 空。`alembic/` 同样零改动。

## 7. 树状态快照与范围声明（2.2）

**写入后快照**（HEAD `63c6d30`，2026-11-04，`git status --short` 逐字）：

```
 M .github/workflows/ci.yml
 M .qoder/rules/benchmark-harness.md
 M .qoder/rules/observability-span-and-link.md
 M CONTRIBUTING.md
 M Dockerfile
 M Makefile
 M README.md
 M "docs/spec/TicketPilot\351\241\271\347\233\256\350\257\264\346\230\216\344\271\246.docx"
 M docs/spec/manual-build.json
 M "docs/spec/\351\241\271\347\233\256\350\247\204\346\240\274.md"
 M "docs/\344\270\213\344\270\200\346\255\245\350\247\204\345\210\222.md"
 M tests/test_question_coverage_reading.py
?? .review/
?? .workbuddy/
?? openspec/changes/close-rerank-guard-and-route-latency-debt-2026-11/
?? requirements.lock
?? tests/test_rerank_default_off_and_over_budget_qualitative.py
```

**本轮 T-B 的实际写入范围**（`git diff --stat` 逐字）：

```
 .qoder/rules/observability-span-and-link.md               | 15 +++++++++++++++
 ...13\344\270\213\344\270\200\346\255\245\350\247\204\345\210\222.md" |  2 ++
 2 files changed, 17 insertions(+)
```

改动形态可逐行核对：`docs/下一步规划.md` 只在 §14.6 的 R9-d 段与 R11 段之间插入 1 个正文行 +
1 个空行；`.qoder/rules/observability-span-and-link.md` 只在 §4.8.c 第 3 条与 §5 第 1 条各加一段
补注，无删除、无改行。

**范围声明**：快照中除上述两个文件外的 10 个受跟踪文件与 4 个未跟踪路径**不属 T-B**。归属如下
（不写「其余均无关」，先枚举再下结论，§8.2 第 1 条）：

| 路径 | 归属 |
|---|---|
| `tests/test_rerank_default_off_and_over_budget_qualitative.py` | T-A（1.2 的只读守卫用例） |
| `tests/test_question_coverage_reading.py` | T-A（1.5 级联：`test_py_files` 89→90） |
| `.qoder/rules/benchmark-harness.md` | T-A（1.4 的两处「当前缺口」→「部分补钉」） |
| `.github/workflows/ci.yml`、`Dockerfile`、`Makefile`、`README.md`、`CONTRIBUTING.md`、`requirements.lock` | 非本变更，开工前已在树上，点名见 §3.3 |
| `docs/spec/*`（docx、`manual-build.json`、`项目规格.md`） | 非本变更，属 R12 收尾轮的 docx 重生成与摘要更新 |
| `.review/`、`.workbuddy/` | 工具目录，不入库 |

## 8. 2.3 / 2.4 的落盘核对

- **2.3**：`docs/下一步规划.md` §14.6 内已有一行「已知缺口（2026-11 显式登记）：`analytics.route`
  无耗时读数」。逐项核对——带 `文件:行`（`:354-376`、`:120`/`:138`/`:166`/`:305`、`:285`、`:326`、
  `:339`，均已按 §5 复核为工作树实数）；不复现锚点句（只给结论与行位，不抄判定原文）；
  **未改 §0.2 R9 状态**（该段插在 §14.6 内，diff 中无 §0.2 行）。
- **2.4**：`.qoder/rules/observability-span-and-link.md` §4.8.c 第 3 条有「2026-11 收口（§5 第 1 条
  的二选一，本轮采纳后者：显式登记缺口）」段，§5 第 1 条有对应的「（2026-11 显式登记）」补注；
  两处均写明理由（三处无起点、一处不同源）与落点（指向 §14.6 的那一行），满足「不得留一个 0 又
  不说明」。二选一收口在 §5 第 1 条明确写成「本轮采纳后者」。

## 9. 发现的既有行号漂移（只登记，未改）

口径文件 `.qoder/rules/observability-span-and-link.md` 有四处对 `app/observability/tracing.py`
的行号引用已失效——该文件现 244 行，`elapsed_ms` 定义在 `:37`、函数体在 `:47`，而口径写
`:190`（`:130`）、`:191`（`:248`、`:354`）、`:189-191`（`:486`）。另 §5 第 1 条把第三处
`duration_ms` 写作 `:288`，实测为 `:289`。

**未在同一轮修正**，理由：`tasks.md` 2.4 的授权面是「§4.8.c 与 §5 第 1 条定点补注」，改这四处
会扩到 §3.4 与附录（`:486`），属超范围；且口径自身已登记「本口径文件的行号引用无机器核对
（锚点守卫只扫 `docs/**/*.md`，`.qoder/rules/**` 不在扫描面内），核对一律以符号名为准」。
是否扩范围修正，留待 reviewer 定夺。

## 10. 未做项

`tasks.md` 2.5（提交 reviewer 审查）不在执行者范围内——按 `tasks.md` 开头「逐项验收由 reviewer
判定；未经 reviewer 通过不得勾选」，勾选亦不由执行者执行。本轮只交证据。
