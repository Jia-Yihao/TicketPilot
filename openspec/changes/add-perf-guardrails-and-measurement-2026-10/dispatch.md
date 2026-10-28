# 分派单（organizer 出品；状态板随轮次更新）

## 0. 在途与排队（不可重叠的上游）

- T-B1-R3（B-1 返工第 3 轮，在途 executor）独占文件集：
  `app/agent/support_agent.py`、`tests/test_decision_reuse.py`、
  `docs/adr/ADR-030-决策复用.md`、`.qoder/rules/refund-reason-taxonomy.md`。
- R0（上轮收尾轮，排在所有在途审查通过之后）：ADR 终稿、`docs/spec/项目规格.md`、
  说明书 docx、全量门禁十项；会动 `docs/fact-baseline.yaml` 的数字行，且其「ADR 终稿」
  产出落 `docs/adr/*.md`（与 P4 可写集交叠）。**开工前置（反向闸）：P4 已过审**——
  organizer 在 R0 开工时必须登记 P4 的过审证据。
- 本波收尾轮 = 计划任务 12。

## 1. 分派表

| 任务 | 内容 | 责任 | 可写文件集 | 依赖/阻塞 | 状态 |
|---|---|---|---|---|---|
| P1 | bench 骨架（计划任务 1） | E1 | `bench/**`（新目录） | 口径文件 `.qoder/rules/benchmark-harness.md` **已落地**（编排方 2026-10-27 裁定，无「批准草案」环节）；前置=用户确认「bench 可先于收尾轮开工」 | 待分派 |
| P2 | 负验证+bench lint 进 CI（任务 2，含 `Makefile` `lint-bench` 目标——本包内 `Makefile` 唯一可写归属） | E2 | `.github/workflows/ci.yml`、`Makefile`（只允许新增独立目标 `lint-bench: ruff check bench`，既有主 `lint` 行逐字不动） | P1 通过后 | 待分派 |
| P3 | 删 `pending_count()`（任务 3） | E2 | `app/services/outbox.py` | 无 | 待分派 |
| P4 | 文档数字守卫扩面（任务 4） | E3 | `tests/test_docs_consistency.py`、`docs/fact-baseline.yaml`、`docs/guide/评测说明.md`、`docs/guide/架构说明.md`、`docs/guide/接口说明.md`、`docs/guide/证据索引.md`（仅本波新增/被改小节补「生成命令+日期」注记，历史小节不动）、`docs/adr/*.md`（除 ADR-030） | 必须**完成并过审于 R0 开始与结束之前**（排序理由：先扩守卫，收尾轮 R0 在更严守卫下一次同步数字）；**完成判据（两档，本守卫扩面轮按一般档）**：P4 这一轮做的是守卫扩面，不是数字刷新轮，**不得**以「该守卫当日全绿」为闸——判据为**当日失败项集合必须是本轮改前基线的子集**：本轮不得新增失败项、不得新增锚点歧义，附可复现命令 `PYTHONIOENCODING=utf-8 python -m pytest tests/test_docs_consistency.py tests/test_doc_code_anchors.py -q -o addopts=`；改前基线以 `1 failed, 15 passed` 及该唯一失败项 `test_documented_numbers_match_the_code` 的既存文档数字漂移分布作**文字判据**登记（`docs/spec/项目规格.md:3451` 五处、`docs/spec/项目规格.md:3517` 一处、`docs/records/智能体平台设想.md:164` 一处、`docs/下一步规划.md:83` 一处，共 8 项，均**先于本波任何轮次存在**，待上一轮收尾轮 R0 做数字刷新后方转绿）；此 8 项既存漂移及其数值**不得**写成会被机器核对的锚点句（守卫 `tests/test_docs_consistency.py` 断言 `docs/fact-baseline.yaml` 的 claim 锚点串不含数字，且每条锚点须在被引文件里整行唯一命中）。当日 `pytest --collect-only -q -o addopts=` 收集数仍须与改前一致（计划任务 4 验收）。**升档条款**：若 P4 这一轮实际承担的就是数字刷新（改写 `docs/fact-baseline.yaml` 数字行 / `docs/adr/*.md` 成稿），判据即**升为该守卫当日全绿**，且必须同轮兑现 `docs/spec/项目规格.md` ↔ `docs/spec/TicketPilot项目说明书.docx` 成对重生成。**时序恒为**：P4 用一般档（子集判据）过审 → R0 才开工做数字刷新（R0 才是全绿 + docx 成对重生成的数字同步轮，见 §3 判据 2）；反向闸「R0 开工前置 = P4 已过审」（见 §0、§2 相交证明）不因此削弱。 | 待分派 |
| P5 | 收窄 A/B 读数（任务 5） | E1 | `bench/model_ab/**` | P1 | 待分派 |
| P6 | M 扫描（任务 6） | E1 | `bench/retrieval_m/**` | P1 | 待分派 |
| P7 | 预取校准（任务 7） | E1 | `bench/prefetch_calibration/**` | P1 | 待分派 |
| P8 | 审批队列 baseline（任务 8） | E1 | `bench/approval_queue/**` | P1 | 待分派 |
| P9 | 预算刹车（任务 9） | E4 | `app/core/config.py`、`app/core/llm_budget.py`(新)、`app/agent/pydantic_agent.py`、`app/agent/support_agent.py`(仅接线)、`.env.example`、`tests/test_llm_budget.py`(新)、数字同步行(`docs/fact-baseline.yaml`/README/PROJECT_SPEC) | **阻塞：T-B1-R3 过审 + R0 完成**；与 P10/P11 串行 | 阻塞 |
| P10 | 事件有界化（任务 10） | E4 | `app/repositories/tasks.py`、`app/services/runtime.py`、事件测试(新/既有)、数字同步行 | 同上；P9 之后 | 阻塞 |
| P11 | seed 去重（任务 11） | E5 | `tests/conftest.py`、直调 seed 的测试模块（定位替换） | 同上；P10 之后 | 阻塞 |
| P12 | 本波收尾（任务 12） | E3 | 见计划任务 12 | P1–P11 全过审 | 阻塞 |

## 2. 可立即开工组的文件集不相交证明

集合（P1/P2/P3/P4 之间两两求交；P5–P8 各自限定在 `bench/` 的互斥子目录）：

- `bench/**` ∩ `ci.yml`/`Makefile` = ∅；∩ `app/services/outbox.py` = ∅；∩ P4 七文件 = ∅。
- `ci.yml`/`Makefile` ∩ `app/services/outbox.py` = ∅；∩ P4 = ∅；`Makefile` ∩ R0 = ∅
  （R0 清单不含 `Makefile`；主 `lint` 行按口径 §2.2 逐字不动，不触锚点同步）。
- P4 集 ∩ 在途 T-B1-R3 集 = ∅（唯一近邻 `docs/adr/*`：P4 显式排除 ADR-030）。
- P4 ∩ R0 = `docs/fact-baseline.yaml` 数字行，**以及 `docs/adr/*.md`**（R0 的「ADR 终稿」
  产出落该目录，与 P4 可写集交叠——前一版证明称「唯一非物理不相交项」系漏计，本轮更正为
  **两项例外**）——均**以排序消解**：P4 必须先于 R0 开始与结束完成并过审，不并行；R0 侧有
  对应「开工前置：P4 已过审」反向闸（见 §0）。风险与判据已写明。
- P9/P10/P11 之间在 `docs/fact-baseline.yaml`/README/PROJECT_SPEC 的数字行重叠，
  故三者**串行**（P9→P10→P11），不做并行分派。

## 3. 阻塞解除判据（P9–P11）

同时满足才解锁，由 organizer 在状态板登记证据：

1. reviewer 对 T-B1-R3 给出书面通过结论；
2. R0 完成（ADR-030 终稿 + PROJECT_SPEC/docx + 全量门禁十项当日输出）；**R0 自身的开工
   前置是 P4 已过审（§0 反向闸）——organizer 登记「R0 完成」证据时须同时登记 P4 过审证据**；
3. 开工当日重跑并记录：`pytest --collect-only -q -o addopts=`（收集数本包不写具体值，
   以当日现算读数为口径；`148/72/15` 文件数/迁移数引用仅作对照）、`git status --short` 快照。

## 4. 每任务通用验收（写进各派单简报）

- 目标/非目标/受影响范围/验收标准/证据要求/依赖，六项齐备才可开工。
- 先写失败测试再实现（P5–P8 为读数任务，判定=每模块 `run.py` 非零退出码断言本模块
  必需键 + `bench/validate.py` 按 per-module 期望表校验；期望表由各测量任务自行登记）。
- 证据必须附本轮命令与真实输出；引用历史读数须注明时点且不得当本轮结论。
- 禁区以 `plan.md`「全局硬约束 1–11」逐条对照；reviewer 未通过不得标记完成。
- 同一任务连续两轮被退回 → 升级：缩范围/补口径/转人工，由 organizer 发起。

## 5. 返工状态板模板

| 任务 | 责任 | 状态 | 依赖 | 审查轮次 | 阻塞原因 |
|---|---|---|---|---|---|
| P1–P8 | E1/E2/E3 | 待分派/待批准 | 见 §1 | 0 | P1 等用户确认 bench 开工时机（口径 `.qoder/rules/benchmark-harness.md` 已落地） |
| P9–P12 | E4/E5/E3 | 阻塞 | T-B1-R3 + R0 | 0 | 见 §3 判据 |

## 6. 审查一般项登记（不阻断，须跟踪）

本规划包（`plan.md` / `tasks.md` / `dispatch.md`）三轮独立审查的结论与遗留登记如下。
本包所在的 `openspec/**` 不在两个文档守卫（`tests/test_docs_consistency.py` /
`tests/test_doc_code_anchors.py`）的扫描范围内，故本轮消歧**无机器核对**，仅有 grep 自查
与 reviewer 读文。本节不写任何基线读数（收集数、文件数、迁移数、head），需要指代时
以当日现算读数为准。

- `rev-tp-plan`（第 1 轮，判**不通过**：2 严重 + 4 一般）→ 一般项已在第 2、3 轮处置。
- 第 2 轮返工后的复审（判**不通过**：2 严重 + 4 一般）→ 一般项 3/4/6 与严重项 1/2 在
  第 3 轮处置，另 2 条在第 2 轮已闭环。
- `rev-tp-plan-r3`（2026-10-27，判**通过**，附 C1/C2 两条一般项）→ 本轮处置，审查原文
  逐字登记如下（原文中的行号为审查时的改前定位）：
  - **C1 审查原文（逐字）**：「**C1 判「一般」**。plan.md:274/:281/:98/:125 与 tasks.md:3
    的裸名「收尾轮」：口径 1 的实质危害（把 R0 义务错归任务 12）已在所有判据句消除；:274
    处于任务 12 自身节内、:7/:97 是引号内固定串（与 dispatch:18 逐字同）、:125/:286 沿用
    benchmark-harness §2.2 自身的泛称。读者按 dispatch.md:12 映射不会误读成 R0。登记建议：
    裸用处补「（本波）」限定。」
    **处置**：C1 的裸名「收尾轮」五处（`plan.md` :98/:125/:274/:281/:286）已加「（本波）」/
    点名归属；`tasks.md:3` 已改「两个收尾轮（R0、本波任务 12）」并列写法。`plan.md:7`/:97
    与 `tasks.md:3` 引号内固定串「bench 可先于收尾轮开工」逐字保留、未改动。
  - **C2 审查原文（逐字）**：「**C2 判「一般」**。tasks.md:8「先于上轮收尾轮」有「上轮」
    限定，与 plan.md:161「上一轮收尾轮 R0」、dispatch §0 定义一一对应，不足以歧义；建议
    统一加 R0 记号。」
    **处置**：`tasks.md:8`/:17 已改用 `R0` 记号（:17 写作「R0（上一轮收尾）」）。
  - **一致性说明**：「合并进主命令行归本波任务 12」这一指认的出处为口径
    `.qoder/rules/benchmark-harness.md` §2.2（原文未点名轮次）+ `docs/下一步规划.md` §12
    的轮次消歧记录（2026-10-27 现测：该记录在 §12.4，行号会漂，引用只到小节号）。
