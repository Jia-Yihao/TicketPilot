# 性能基准目录 `bench/` 的方法论口径（P-BENCH）：项目级口径

> 本文件是 TicketPilot 本轮工作（下一波「性能与治理优化」开工前，先给尚未存在的
> `bench/` 目录立方法论口径）的项目级声明，只规定术语、门禁归属、读数契约与验收口径。
> 流程仍遵循用户级协作规则；业务事实以 `docs/spec/项目规格.md` 为准，冲突时先改本文件并说明理由，
> 再改代码。
>
> 本文件**只声明口径，不改动任何产品代码、测试、`Makefile`、`pyproject.toml`、迁移、既有文档**。
> 「当前事实」一律给 `文件:行` 证据；标注**「待实现」**的项是本轮已裁定、但代码/配置尚未落地的口径，
> 其落地属实现轮任务。

---

## 0. 本文件的定位与生效时点

`bench/` 目录**当前不存在**（本文件撰写时在仓库根实测 `ls -d bench` → `No such file or directory`）。
因此本文件的全部「当前事实」都是**没有 bench 时的现状**；口径 1 的三条中，第 1、2 条**待落地**，
第 3 条是「`python -m mypy app` 范围不变」的**不扩面裁定**（无需改动任何清单，只需守住不被扩面）。

生效时点：**`bench/` 建立之前**。任何 `bench/` 目录的首次提交必须同时满足：

1. 口径 1 的三条已落地（`bench/` 内无可收集文件、`bench` 由独立 `lint-bench` 目标覆盖、
   `python -m mypy app` 范围不变），或在提交里逐条写明哪一条尚未落地及原因；
2. 每条读数带 §3 的产出身份；
3. 回填文档前先跑 §4 的两个守卫并附本轮真实输出。

背景（这是本口径存在的理由，可核对）：本仓库有一串**会咬人**的门禁——文档数字守卫把
「pytest 收集用例数」绑成了事实基线，且该数字在文档里有**三处**说法、其中一处还要求说明书
docx 同步（`docs/fact-baseline.yaml:163-175`：`智能体平台设想.md` 的「规模事实」行、
`docs/spec/项目规格.md` 的「本轮收尾读数」行（`in_manual: true`）、`docs/下一步规划.md` 的「| 测试 |」行），
而事实的唯一定义是现算的 `pytest --collect-only`（`tests/test_docs_consistency.py:147-174`）。
`bench/` 的归属若不先定死，就会出现两种事故：写了**不受 lint 的代码**，或基线数字被自己刚动的
目录**搞漂移**——后者会一次性产生一批文档欠账（守卫级联）。

---

## 1. 术语表

同一概念在本项目只有一种叫法。下表右侧的英文名是**实现必须逐字使用的标识符**
（目录名、JSON 键名、字段名、命令串）。

| 中文术语 | 唯一英文标识符 | 唯一定义 |
|---|---|---|
| 基准目录 | `bench/` | 仓内**新增的顶层目录**，只承载性能与分布读数的生产者，不承载业务逻辑、不承载 pytest 用例。 |
| 测量模块 | bench module | `bench/` 下一个可独立运行的测量单元，入口是**显式命令**（`python bench/<name>.py`），不是 `pytest`。 |
| 读数 | reading | 一个测量模块一次运行产出的一份结果值。**没有指纹的读数无效**（§3.1）。 |
| 环境指纹 | `env_fingerprint` | 与读数同处存放、说明「这个数在什么环境上产生」的字段集合（§3.1）。 |
| 产出身份 | `produced_by` | `env_fingerprint` 内的**必填子项**：产生该读数的 git commit（或工作树脏的显式声明）、生成命令全串、时刻。 |
| 推理 token 干扰项 | `reasoning_tokens_enabled` | `env_fingerprint` 内的**必填子项**：本机模型服务「reasoning tokens 关不掉」这一事实的登记位与注记（§3.2）。 |
| 事实基线 | `docs/fact-baseline.yaml` | 「哪个文件的哪一行写了哪个数字、用哪个正则抠出来」的清单；**不写数字本身**（`tests/test_docs_consistency.py:9-13` 的模块文档）。 |
| 两个守卫 | （指代，非标识符） | §4 点名的**既有**两条契约回归判据：SQLite/PostgreSQL 逐位一致、`evals/replay.py --self-check` 的 `REPLAY DIFF EMPTY`。本口径**只复用**它们，不新增第三条。 |
| 守卫级联 | （概念，非标识符） | 改动一个受事实基线绑定的数字后，必须同步该事实的全部文档说法 + 重生成说明书，否则守卫红的连锁后果。 |
| flag 化 | （概念，非标识符） | 行为改动必须由配置开关承载，默认值即改动前的行为（§5.1）。 |
| 默认等价 | （概念，非标识符） | flag 关闭时，既有 Golden / support / 回放读数**逐字不变**；「等价」的判据是读数，不是「大概没变」。 |

### 1.0 必须消歧的同名概念

| 已有标识符 / 目录 | 含义 | 证据 | 与 `bench/` 的关系 |
|---|---|---|---|
| `evals/` | **质量**读数（评测集、指标门槛、回放、延迟判定脚本） | `Makefile:24-44` 的 `eval` / `eval-support` / `eval-retrieval` / `eval-replay` 目标全部指向 `evals/`；`evals/retrieval_metrics.py:690-744` | 不同职责。`evals/` 回答「答得对不对、过不过门槛」，`bench/` 回答「多快、多贵、分布如何」。**禁止**把基准脚本塞进 `evals/`（`evals/` 的既有入口逐条被 `Makefile:24-44` 的 `eval` 系列目标指向，往里加读数生产者就是改那批门禁的口径），也禁止把质量门槛塞进 `bench/`。 |
| `tests/` | pytest 用例所在地，`testpaths` 唯一入口 | `pyproject.toml:51-52`（`[tool.pytest.ini_options]` / `testpaths = ["tests"]`） | `bench/` **不得**放进 `tests/`（§2.4）。 |
| `scripts/` | 一次性/运维脚本 | `Makefile:9-10`（`seed`）、`:18-19`（`guard-negative`）、`:46-47`（`sample-bad-cases`） | 不同职责：`scripts/` 产出的不是可复用的性能 baseline。 |
| `ruff check` | lint 命令 | `Makefile:22`、`CONTRIBUTING.md:28` | 逐字用 `ruff check`，**不得**写作「lint 清单」「代码检查列表」等别名去指代那条命令行。 |
| `mypy` | 类型检查命令 | `CONTRIBUTING.md:29`、`docs/records/智能体平台设想.md:855` | 逐字用 `mypy`；口径一律写全命令 `python -m mypy app`，范围不变——`bench` 不进 mypy 覆盖（§2.3）。 |
| `REPLAY DIFF EMPTY` | 回放自检的**通过读数** | 打印点 `evals/replay.py:441-446`（字符串在 `:443`）；命令定义 `evals/replay.py:453-457`（`--self-check`） | 它是**断言读数**，与同脚本 `:415` 打印的信息行 `replay: N conversations, M diff lines` **不是同一个东西**；引用时必须写全大写原串，不得意译成「回放无差异」。 |
| `pytest` 收集数 vs `passed` 数 | 事实基线绑的是**收集数** | `docs/fact-baseline.yaml:163-175`（`test_collected`，现算 `pytest --collect-only -q`） | 「447 passed」那类是**运行读数**，会随跳过与否浮动；本口径谈的漂移风险只针对**收集数**。 |
| `OVER BUDGET` | 重排的**离线延迟判定**读数 | 产生点 `evals/rerank_latency.py:249`；读数与判定 `docs/spec/项目规格.md:3647`（mean 2300 / p50 2281 / p95 2602 / max 2689 对 300 ms 预算） | 不是「待修复缺陷」，也不是 `bench/` 可以自行改写的定性（§5.1）。 |

---

## 2. 口径 1：`bench/` 的门禁归属（三条定死）

三条中第 1、2 条**待实现**（`bench/` 尚不存在，现有清单里也没有它），第 3 条为**不扩面裁定**——
守住既有串不被改动即是落地。下表是本文件撰写时的现状读数，
逐条给证据；实现轮必须把「待落地要求」那一列变成真实配置。

| # | 定死的口径 | 当前事实（已核对） | 待落地要求 |
|---|---|---|---|
| 1 | `bench/` **不被 pytest 收集** | `testpaths = ["tests"]`（`pyproject.toml:51-52`），`addopts = "-q"`（`:53`） | 保持 `testpaths` 不变；`bench/` 内**不得**出现 `test_*.py` 或 `*_test.py` |
| 2 | `bench/` **必须被 lint 覆盖（独立 `lint-bench` 目标，不改主命令行）** | `ruff check app tests scripts evals alembic`（`Makefile:22`；同一串见 `CONTRIBUTING.md:28`、`docs/records/智能体平台设想.md:854`）——**不含 `bench`**，且本轮**不改这条主命令行** | 新增 Makefile 目标 `lint-bench: ruff check bench` + CI 独立步骤 `ruff check bench`（**待实现**）；合并进主命令行属口径变更，须与文档同步一起由收尾轮做 |
| 3 | `bench/` **不进类型检查** | `python -m mypy app`（`CONTRIBUTING.md:29`、`docs/records/智能体平台设想.md:855`、`docs/spec/项目规格.md:4697` 明写「只覆盖 `app`」）——**不含 `bench`** | **不扩面**：`python -m mypy app` 范围保持不变（编排方 2026-10-27 裁定，理由见 §2.3）；既有门禁串与「只覆盖 `app`」类范围表述**逐字不动** |

### 2.1 第 1 条：不被收集，测量脚本一律是显式命令入口

- **禁止形态**：`bench/` 目录内出现 `test_*.py` 或 `*_test.py`。
- **允许形态**：`python bench/<name>.py` 这类显式命令入口；模块的「自检」用**退出码**表达
  （非零即失败），失败信息必须可观察，不得静默返回 0。
- **理由（写成链条，逐环可核对）**：一旦被 pytest 收集，就会改变「`pytest` 收集用例数」这条
  **已进事实基线**的读数。链条是：

  1. 收集数的唯一定义是现算的 `pytest --collect-only`（`tests/test_docs_consistency.py:147-174`），
     登记为事实 `test_collected`（`docs/fact-baseline.yaml:163-175`）；
  2. 该事实有**三处**文档说法，其中 `docs/spec/项目规格.md` 那处标了 `in_manual: true`
     （`docs/fact-baseline.yaml:169-172`），于是还追加一条「说明书 docx 必须重生成」的欠账
     （同步守卫 `tests/test_docs_consistency.py:448-467` 的 `test_manual_contains_the_claimed_lines`、
     `:470-488` 的源文件 sha256 比对）；
  3. 每个事实**至少**要在两处被写出来（`tests/test_docs_consistency.py:437-445`），
     且文档锚点必须**整行唯一命中**（`tests/test_docs_consistency.py:269-278`），
     追加文档内容时不得复现已登记锚点的那句话。

  净效果：**一次性能测量附带一批文档欠账**，且失败模式是「守卫红一片、没人说得清哪个数才是对的」。
  把 `bench/` 挡在收集之外，买的就是这条基线不动。
- **诚实标注**：`testpaths = ["tests"]` 已使默认 `python -m pytest` 扫不到 `bench/`，但这条纪律
  **不是多余**的——显式 `pytest bench/` 会绕开 `testpaths`；而实现轮若为了让 bench 进 CI 去改
  `testpaths`，纪律立刻失效。因此**目录内不得出现可收集文件名**是主口径，`testpaths` 是兜底。

### 2.2 第 2 条：`bench/` 必须被 lint 覆盖，但不改被文档锚定的主命令行

- 口径：**新增** Makefile 目标 `lint-bench: ruff check bench`，并在 CI 加独立步骤
  `ruff check bench`；既有主命令行 `ruff check app tests scripts evals alembic` **本轮不改**
  （编排方 2026-10-27 裁定）。合并进主命令行属口径变更，须与文档同步一起由收尾轮做。
  理由：主命令行串被 `CONTRIBUTING.md:28`、`docs/records/智能体平台设想.md:854` 等多处文档
  **逐字锚定**，立刻把 `bench` 塞进路径参数会即时打爆这些已登记的锚点句。
- 当前事实：现清单是 `app tests scripts evals alembic` 五个目录，**不含 `bench`**
  （`Makefile:22`，同一串抄在 `CONTRIBUTING.md:28`、`docs/records/智能体平台设想.md:854`）。
  本文件**不改**这两处——本轮的落地形态是新增 `lint-bench` 目标，不是改这串。
- 为什么是「必须」而不是「可选」：不收 bench 等于**允许往仓库里写不受 lint 的代码**，
  与本仓库「说法必须变成断言」的既有取向（`tests/test_docs_consistency.py:1-19` 的模块文档）直接冲突。
- **同步欠账登记（只在收尾轮决定合并主命令行时触发，本文件不代做）**：该命令行在文档里是**被写死多次**的事实串——
  `CONTRIBUTING.md:28`、`docs/records/智能体平台设想.md:854`（那行还专门注了一句「范围是这五个目录，
  不是整仓 `.`」）、以及 `docs/下一步规划.md:842` / `:886` / `:944` / `:1011` 与
  `docs/spec/项目规格.md:4767` / `:4782` / `:4958` / `:4971` 那批**历史读数**行。
  本轮口径（独立 `lint-bench` 目标）**不动主命令行**，这批行逐字不变，不产生同步欠账；
  若收尾轮把 `bench` 合并进主命令行：口径行（前两处 + `Makefile`）必须逐字改到新串；
  历史读数行**不得静默改写**（时点读数按项目做法标日期保留）。
  注：`ruff check` 这串**不在** `docs/fact-baseline.yaml` 的清单里（该文件只有 `mypy_files`
  一条与 mypy 相关，`docs/fact-baseline.yaml:138-147`），所以守卫**不会**替你抓这句的漂移，
  必须人工同步。

### 2.3 第 3 条：`bench/` 不进类型检查（`python -m mypy app` 范围不变）

- 口径：**mypy 不覆盖 bench**。既有口径 `python -m mypy app`（生产代码 strict）**范围不变**，
  不新增 `python -m mypy app bench` 之类的命令串，也不要求实现轮扩面。
  理由（编排方 2026-10-27 裁定）：扩面会把 `mypy_files` 这条事实基线读数与门禁时长一起抬高，
  收益不成比例；bench 是**读数生产者**，其类型问题污染的是读数不是产品——读数的可信度由
  §3 的环境指纹与校验器兜底，不由 mypy 扩面兜底。
- 当前事实（三条证据，逐条不含 bench；按本条口径它们**继续为真、不得改动**）：`CONTRIBUTING.md:29` 写 `python -m mypy app`
  并注「strict 模式，零错误（148 个文件）」；`docs/records/智能体平台设想.md:855` 同串；
  `docs/spec/项目规格.md:4697` 的检查项明写「`python -m mypy app` 通过（`strict = true`，只覆盖 `app`）」，
  `docs/spec/项目规格.md:3023` 也写「CI 跑 `python -m mypy app`」。配置侧 `[tool.mypy]` 为
  `strict = true`（`pyproject.toml:72-75`）。
- **数字与措辞两侧的可核对依据（保留原核实结论）**：
  - 数字侧：事实 `mypy_files` 现算的是 **`app/` 下的 Python 文件数**
    （`tests/test_docs_consistency.py:134-140`，`_fact_app_py_files` 与 `_fact_mypy_files`
    都指向 `ROOT / "app"`）。因此「mypy 不覆盖 bench」不会打红数字守卫；反过来，
    一旦扩面，「零错误（148 个文件）」这类**登记在事实基线上的说法**立刻与读数脱节，
    触发守卫级联——这正是裁定不扩面的直接代价依据。
  - 措辞侧：**「只覆盖 `app`」这类范围表述**（`docs/spec/项目规格.md:4697`、
    `CONTRIBUTING.md:29` 的注释、`docs/records/智能体平台设想.md:855` 的括号说明）在本口径下
    **保持为真，实现轮不得改**；只有将来正式裁定扩面时才随扩面同步改写。
- **代价（登记，作为「不扩面」的理由保留）**：`[tool.mypy]` 是 `strict = true`（`pyproject.toml:72-74`），
  bench 若进去就要按 strict 写全注解；门禁时长与一次性返工量都会上升。基准脚本写的数字
  将来会被引用，类型问题污染的确实是读数本身——但对本条的兜底是 §3 指纹 + §7 校验器读数，
  不是 mypy 扩面。若后来者要重新论证扩面，**必须先改本文件并说明理由**。
- **与 OpenSpec 草案的关系（如实记录，冲突已闭环）**：
  编排方 2026-10-27 裁定采纳「`mypy` 不扩面、`ruff` 走独立 `lint-bench` 目标」，
  与本文件 §2.2 / §2.3 一致；`openspec/changes/add-perf-guardrails-and-measurement-2026-10/benchmark-harness-draft.md`
  §2 的立场与之**一致**，且该草案**已被本文件取代**——引用 bench 测量口径一律以本文件为准，
  不得再回引草案作为实现依据。

### 2.4 第 1 条的补充边界（不得绕道）

1. **不得为了让 bench「不被收集」而把它放进 `tests/` 里面**（例如 `tests/bench/`）。
   那会同时污染测试目录的口径——`_fact_test_py_files` 现算 `tests/` 下的 `.py` 文件数
   （`tests/test_docs_consistency.py:143-144`，登记为 `test_py_files`，
   `docs/fact-baseline.yaml:149-161`），放进来就直接把这条基线打漂移；而「测性能」与「测正确性」
   混在一个目录树里，后来者无法判断该看哪个入口。
2. **不得靠 `testpaths` / `norecursedirs` 之外的花招绕过**：允许的机制只有
   `pyproject.toml` 的 `[tool.pytest.ini_options]` 这一处既有配置面
   （`pyproject.toml:51-57`）。**禁止**用 `conftest.py` 钩子里动态删 item、
   软链接、改名规避、或给 `bench/` 单开 `pytest.ini` 之类的写法——它们都让「哪些用例真的在跑」
   变成要靠读隐藏逻辑才知道的事。
3. **不得把 `bench/` 的自检伪装成 pytest 用例再声称「没被收集」**。自检只有两条正当形态：
   显式命令 + 非零退出码（§2.1）。

### 2.5 已登记的缺口（诚实标注，不在本轮补）

- 两个守卫的扫描清单**都不含 `bench`**：符号/路径锚点守卫只认顶层目录
  `("app", "evals", "scripts", "tests", "web", "alembic")`
  （`tests/test_doc_code_anchors.py:39`，扫描范围 `docs/**/*.md` 见 `:172`）；
  数字守卫的现算表 `COMPUTERS` 也没有任何 bench 相关事实
  （`tests/test_docs_consistency.py:202-217`）。
  后果：`bench/` 里写的 `文件:行` 与「bench 已进 `lint-bench`」这类说法**当前没有机器核对**，
  只能靠实现轮的显式命令与 reviewer 读 diff。**这就是缺口**，本口径如实登记，不假装已被守卫兜住。
- 同理，`.qoder/rules/*.md` 不在锚点守卫扫描范围内（只扫 `docs/`，
  `tests/test_doc_code_anchors.py:172`），**本文件自身的 `文件:行` 不被机器核对**。
  因此本文件每处引用在撰写时都是**逐行读到过**的；实现轮若发现某处行号漂了，改本文件并说明，
  不得反过来以「守卫没红」为准。
- 行号漂移同步登记（R2，2026-10-29）：本轮为把「源文件摘要」归一为单一定义，在
  `tests/test_docs_consistency.py:41` 引入 `from scripts.build_manual import source_digest`，使该文件原
  :41 之后的行号整体 +2；本文件对它的 8 个区间引用已按此同步。同批另有 **8 行**同类引用落在
  `openspec/changes/add-perf-guardrails-and-measurement-2026-10/`（共 **13 处引用点、5 个区间族**，
  2026-10-29 实测，收尾轮可用
  `grep -rn "145-172\|446-465\|468-486\|435-443\|267-276\|132-138\|141-142\|200-215\|393-395\|380-388" openspec/changes/add-perf-guardrails-and-measurement-2026-10/`
  现核）：`plan.md:62` 引 `:267-276`、`plan.md:63` 引 `:393-395`、`plan.md:65` 引 `:435-443` 与
  `:380-388`、`plan.md:159` 引 `:267-276` 与 `:393-395`、`plan.md:160` 引 `:380-388`、
  `proposal.md:32` 引 `:200-215`、`specs/project-gates/spec.md:22` 引 `:200-215` 与 `:435-443`、
  `specs/project-gates/spec.md:23` 引 `:267-276`、`:393-395` 与 `:380-388`。
  本轮不改，登记留待口径/收尾轮处置。

---

## 3. 口径 2：环境指纹必须多记两项

### 3.1 每条读数必须记录产出身份

`env_fingerprint` 必含下列各项，**缺任一项该读数即无效**（无效 = 不得进文档、不得引用、不得当基线）：

| 字段 | 内容 | 为什么必含 |
|---|---|---|
| `produced_by.commit` | 产生该读数的 git commit；工作树脏时**显式声明**「脏」并附脏状态摘要，不得只写一个干净的 commit 冒充 | 本仓库已吃过一次「A/B 产物没记 commit，导致无法复核它产自哪棵树」的亏；那条教训的形状与 `openspec/agents.md` §8「证据型变更在脏工作树上运行，`git status` 无法区分本轮改的与更早累积的，因此自述不得写成已核事实」一致 |
| `produced_by.command` | 生成命令全串（可复制粘贴重跑） | 「怎么跑出来的」必须是读数的一部分，否则不可复现 |
| `produced_by.moment` | 时刻（标时点，不冒充现状值） | 时点读数必须标时点（项目既有纪律） |
| `model_endpoint` | 模型服务端点 URL | 本机端点与托管端点的延迟/cost 完全不是一回事；端点事实见 `docs/spec/项目规格.md:1360`（`OPENAI_BASE_URL=http://127.0.0.1:1234/v1`）、`.env.example:41` |
| `model_id` / `model_snapshot` | 模型标识与快照名 | 同一个端点换模型后读数不可比；本仓库引用过的写法是 `openai:qwen/qwen3-8b`（`docs/guide/证据索引.md:85`） |
| `python_version` / `deps` | Python 版本与依赖版本（至少 pydantic-ai、sqlalchemy、fastapi、pytest） | 版本漂移会改并发、序列化与计时行为 |
| `machine_os` | 机器与 OS 指纹（OS 与版本、CPU/内存规模、是否容器内） | 本项目所有既有延迟读数都在**本机 CPU** 上产生（`docs/spec/项目规格.md:3647` 那条判定就是「在这台纯 CPU 机器上」），换个机器就不能外推 |
| `reasoning_tokens_enabled` | 见 §3.2 | 见 §3.2 |
| `repeat_count` / `spread` | 见 §3.3 | 见 §3.3 |

- 存储形状由实现轮定，但**必须与读数同处可查**（写在 `baseline.json` 内或与之并列并可由
  显式校验器判定缺项即 FAIL）。**禁止**「指纹在另一个文件、读数在另一个文件、靠人记住它们的对应关系」。

### 3.2 上游不可控干扰项：LM Studio 的 reasoning tokens（这是引用约束，不是待修复缺陷）

**定死登记**：本机 LM Studio（端点 `127.0.0.1:1234/v1`）的 **reasoning tokens 关不掉**
——温度设 0 也压不住，单次调用约 **8–14 秒**。因此：

1. 本仓库的**费用 / token 类读数必然包含推理 token**；引用时**必须**如实带上这一句，
   **不得**把这类读数写成「纯答案 token」，也不得写成「答案长度」。
2. 含模型调用的延迟读数必须带 `reasoning_tokens_enabled` 与口径注记：
   「该延迟含推理 token 生成时间，不代表推理引擎能力上限」。
   **不同 reasoning 设置下的读数不得互相引用**（换了设置就是换了分母）。
3. 这一条写成**引用约束**：它不是可修的缺陷——上游没有开关，我们能做的只有如实标注。
   把它写成「待修复」会诱导后来者「修好之后再引用旧数」，而旧数根本没变。
4. 事实的可核对来源：既有实测已显出这笔额外 token 并被登记为差异而非静默覆盖——
   `docs/下一步规划.md:2377` 记录了同一探针复跑时行内容为
   `model cost: calls=11 failures=0 tokens=7434+2449 wall=90382 ms`，其中 **2449 是另计的
   reasoning tokens**，而旧行只记 7303（`docs/下一步规划.md:2304` 同样注明「本轮复跑多计 2449
   reasoning tokens」）。这两行是本口径引用约束的直接依据：token 读数**从来就不是**「纯答案 token」。

### 3.3 重复性要求：不得只跑一次就落文档

- 同一读数**至少重复 5 次**，并记录离散程度。
- 报数时必须同时给出：次数、集中趋势（median）、离散项（min/max，或 p50/p95 视指标而定）。
  只报一个均值、或只报一次单次运行的读数，**不得落进任何文档**。
- 理由（可核对）：本仓库已有的延迟读数就出现过「同一判定、不同百分位」的复跑差异——
  重排 p95 记录读数 2602 ms、复跑 2497 ms，**判定仍是 `OVER BUDGET` 不动**
  （`docs/spec/项目规格.md:4856`、`docs/guide/评测说明.md:363`、`docs/下一步规划.md:781` 三处都写了这件事）。
  既然百分位会随机器负载浮动，5 次就是「能说出离散有多大」的**最小可判样本**；
  少于 5 次时 min/max 与 median 的关系在算术上几乎不携带信息，写了也挡不住引用者把它当稳定值。
- 该最小次数与既有草案 `benchmark-harness-draft.md:38` 及
  `openspec/changes/.../specs/benchmark-harness/spec.md:22` 的「重复次数 ≥5」同值，不另立新数。

---

## 4. 口径 3：契约回归复用现成的两个守卫，不另写一套

### 4.1 判据只有这两条

`bench/` 的改动（或 bench 驱动的优化）是否破坏行为，判据**只用既有的两个守卫**：

| 守卫 | 命令 | 通过读数 | 产生位置（本文件撰写时已逐行读到） |
|---|---|---|---|
| (a) SQLite / PostgreSQL 逐位一致 | `python evals/retrieval_metrics.py --database-url <PG 库>` 与默认（临时 SQLite）各跑一遍，两侧**同读数** | 五通道各行指标逐位一致 | `--database-url` 参数定义 `evals/retrieval_metrics.py:695-698`，分流逻辑 `:727-744`（不给 URL 时走 `sqlite+pysqlite://` 临时库）；该性质的根因修复在 `app/retrieval/search.py:106` 的 `visible_chunks()` + `:127` 的 `.order_by(DocumentChunk.id)`；后果与解释记录在 `docs/guide/评测说明.md:231`、`README.md:176` |
| (b) 回放自检 | `python evals/replay.py --self-check` | `REPLAY DIFF EMPTY` | 字符串打印点 `evals/replay.py:441-446`（`:443`）；`--self-check` 参数定义 `:453-457`；信息行 `replay: N conversations, M diff lines` 在 `:415`（**不是**通过读数本身） |

### 4.2 不写第三套的理由

新增第三套回归判据 = **又多一条要人工同步的口径**。本仓库的取向是「说法必须变成断言，
断言不得重复」：数字守卫把文档数字绑定到**唯一**的现算实现（`tests/test_docs_consistency.py:9-13`
的模块文档：清单「不写数字本身——数字的唯一定义在代码里」），符号守卫同理只登记一份
（`tests/test_doc_code_anchors.py:1-22` 的模块文档，`:237-252` 的符号存在性判定）。
在同一件事上再开第三个判据，会把「谁说了算」重新变成模糊问题。

### 4.3 回填纪律（bench 结论进文档时）

1. **只允许回填读数与被改的那一句**。不得顺手改口径文件以外的既有事实行，不得「顺便」把相邻段落
   的口径也调整一遍。
2. **回填前必须先跑这两个守卫并给出本轮真实输出**（§4.1 的两条命令，当日运行）。
   引用历史运行结果冒充本轮，按「造假」处理。
3. **时点读数标时点**：历史读数按日期保留，不静默改成现状值；现状值另起一行注明
   「此为 X 日 Y 时读数，现已变化」。这条既有纪律的直接适用面就是性能读数
   （`docs/spec/项目规格.md:4856` 那条「复跑一次、p95 2497、判定不动」就是它的正确形态）。
4. **锚点唯一命中**：新增或改写文档行时，不得复现已登记锚点的那句话
   （`tests/test_docs_consistency.py:269-278` 要求每个锚点在目标文件里**恰好命中一行**）；
   动了被 `in_manual: true` 绑定的行，必须重生成说明书（`tests/test_docs_consistency.py:448-467`、
   `:470-488`）。

---

## 5. 两条禁区（不得省略，不得软化）

### 5.1 优化一律「默认关闭」地实现，或 flag 化

- 任何 bench 驱动的行为改动必须由配置开关承载，**默认值 = 改动前的行为**；
  要么默认关闭，要么默认等价（等价的可判据 = §4.1 两个守卫 + 既有 Golden / support 读数逐字不变）。
- **不得为了让基准好看**而把已判「超预算、默认关闭」的行为（**重排**）悄悄写成「已优化」。
  该读数必须**原样保留**其「**OVER BUDGET 默认关闭**」的定性。可核对的现状事实：
  `app/core/config.py:70` `retrieval_rerank_enabled: bool = False`（`:66-68` 的注释写明
  dense 与 rerank 是 opt-in 且 fail open）；`.env.example:59` `RETRIEVAL_RERANK_ENABLED=false`；
  判定产生点 `evals/rerank_latency.py:249`；判定依据读数 `docs/spec/项目规格.md:3647`
  （mean 2300 / p50 2281 / **p95 2602** / max 2689 对 300 ms 交互预算）；
  既有的定性表述见 `README.md:199`、`README.md:423`、`docs/下一步规划.md:290`
  （「判 **OVER BUDGET**……默认关闭，理由从『没测』变成『测了，超预算』」这一类句子是
  结论本体，不得在回填时被改成「已优化」或「延迟可接受」）。
- **可钉住的形状（给实现轮）**：默认配置下断言 `retrieval_rerank_enabled` 为假，
  且 `evals/rerank_latency.py` 的 `verdict` 仍产出 `OVER BUDGET`；文档里凡出现被本条保护的定性，
  必须能在 `README.md` / `docs/guide/评测说明.md` 检索到同一串。
  **当前缺口**：仓库内**没有**一条专门钉「rerank 默认关闭 + OVER BUDGET 定性未被改写」的守卫
  （`docs/fact-baseline.yaml` 里没有 OVER BUDGET 相关事实，`tests/` 里也没有断言该串的用例）。
  如实登记为缺口，是否补钉由实现轮按最小范围决定。

### 5.2 跨 worker 语义的成立条件不得被改

- 现有实现依赖「**所有进程读同一张表**」这一事实，基准**不得**以性能为理由把它换成
  内存总线、进程内广播或其他进程内机制。可核对的三处：
  1. 模型用量台账：`app/services/llm_usage.py:1`「Cross-worker model accounting」，
     表侧说明 `app/models/llm_usage.py:13-20`——「One model run, written where every worker can read it」，
     进程内计数器「dies with the worker」，行才是台账事实。
  2. 任务事件流：`app/services/runtime.py:1174-1177`——「订阅的是数据库、不是进程内回调」，
     worker A 上的流能看到 worker B 写的事件，「轮询是不引入消息泵的代价」。
  3. 限流预算：`app/core/rate_limit.py:7`（N 个 worker 就各有一份预算）、
     `app/api/middleware.py:125`（配置了共享存储才「survive across workers」）、
     `app/core/config.py:126`（多 worker 需 `RATE_LIMIT_STORE=redis`）——这一处的「跨进程」
     本来就是**显式配置**换来的，不得由 bench 改动偷偷换成进程内默认。
- 允许的动作：把同一张表的读取**变快**（加索引、有界化、缓存 + 正确失效键）。
  禁止的动作：把事实源从表**搬到进程内**，从而让「多 worker」这个部署前提失效。
- **可钉住的形状**：涉及上述三处的 bench 优化，其等价性测试必须在「两个会话/两个引擎实例」
  或既有的 PG 侧用例下仍通过（现有 PG 用例入口：`tests/test_postgres_concurrency.py:21`、
  `tests/test_postgres_outbox.py:22`，均由 `TICKETPILOT_TEST_POSTGRES_URL` 决定是否跳过）。
  **当前缺口**：没有任何守卫断言「台账/事件流的事实源仍是数据库」，只能靠 reviewer 读 diff 判；
  如实登记。

---

## 6. 硬性边界（编号，逐条可判定）

1. 本文件**不改**任何产品代码、测试、`Makefile`、`pyproject.toml`、`.env.example`、`docs/**`。
   口径 1 的第 1、2 条落地属实现轮，第 3 条为不扩面守住项（§2.3）。
2. `bench/` 只调**生产原接口**取数，**不复制业务逻辑**；`app/**` 不得 import `bench/**`
   （方向唯一，bench 依赖 app，反之禁止）。
3. `bench/` 的假模型、假时钟、假端点**只活在 `bench/**` 内**，不得进入任何生产代码路径。
4. `bench/` 内不得出现 `test_*.py` / `*_test.py`（§2.1）。
5. `testpaths` 保持 `["tests"]` 不变；不得用 `conftest.py` 钩子删 item 之类的隐藏机制绕过收集口径（§2.4）。
6. `bench/` 的回归**不进** `python -m pytest` 门禁；它的自检是显式命令 + 非零退出码。
7. 每条读数缺 `env_fingerprint` 任一项即无效（§3.1）；无效读数不得进文档、不得被引用。
8. reasoning token 干扰项必须以「引用约束」形态被带上，不得改写成缺陷或「已修复」（§3.2）。
9. 同一读数重复 <5 次即不得落文档（§3.3）。
10. 契约回归判据**只有** §4.1 那两个守卫；不得新增第三套。
11. 回填只改读数与被改那一句，先跑守卫并附本轮真实输出（§4.3）。
12. 优化默认关闭或默认等价；重排的「OVER BUDGET 默认关闭」定性不得改写（§5.1）。
13. 「所有进程读同一张表」的成立条件不得被换成进程内机制（§5.2）。
14. 不引入新配置项、不新增迁移、不新增依赖（本口径不要求任何新的 `Settings` 字段）。
15. 术语不得另造别名：`ruff check` / `mypy` / `pytest` 收集数 / `REPLAY DIFF EMPTY` /
    `docs/fact-baseline.yaml` / `tests/test_docs_consistency.py` / `tests/test_doc_code_anchors.py`
    一律逐字使用。

---

## 7. 验收标准（DoD）

每条都可判定，并注明当前是「已可钉住 / 待实现 / 缺口」。

1. **`bench/` 内无 pytest 可收集文件**：遍历 `bench/`，不存在 `test_*.py` 与 `*_test.py`；
   `python -m pytest --collect-only -q -o addopts=` 的收集数与本口径落地前**同一数字**
   （该收集数即事实 `test_collected`，`tests/test_docs_consistency.py:147-174`）。
   ——**待实现**（`bench/` 尚不存在，§2 表第 1 行）。
2. **`bench` 由独立 `lint-bench` 目标覆盖**：`Makefile` 新增 `lint-bench: ruff check bench`，
   CI 有独立步骤 `ruff check bench`；既有主命令行 `ruff check app tests scripts evals alembic`
   与 `CONTRIBUTING.md:28`、`docs/records/智能体平台设想.md:854` 的锚定串**逐字不变**，
   历史读数行不被静默改写；合并进主命令行只属收尾轮的口径变更。——**待实现**（现串 `Makefile:22` 不含 `bench`）。
3. **mypy 范围不变（不扩面守住项）**：`python -m mypy app` 与「只覆盖 `app`」类范围表述
   （`docs/spec/项目规格.md:4697`、`CONTRIBUTING.md:29` 注释、`docs/records/智能体平台设想.md:855`）
   **逐字保持**；`mypy_files` 读数仍按 `app/` 现算成立
   （`tests/test_docs_consistency.py:134-140`）。——**已落地即守住**：本轮与实现轮都不得把这些串扩到 `bench`（编排方 2026-10-27 裁定，§2.3）。
4. **每条读数带产出身份**：校验器对缺 `produced_by`（commit / 命令 / 时刻）的 `baseline.json`
   判 FAIL；脏树情形必须有显式声明字段，不得只有一个裸 commit。——**待实现**；
   当前可钉住的替代：无（§2.5 的缺口——两个守卫都不扫 `bench/`）。
5. **推理 token 干扰项在引用处被如实带上**：任何含模型调用的延迟/费用/token 读数，
   其引用文本必须能检索到「含推理 token 生成时间」这一句口径注记；
   不得出现「纯答案 token」这类表述。——**待实现**；
   当前**缺口**：仓库内无守卫扫文档的这一句话，只能靠 reviewer 读回填 diff。
6. **重复性与离散度**：每条读数带 `repeat_count` ≥ 5 与离散项（min/max 或 p50/p95）；
   只报单次或只报均值的读数判无效。——**待实现**（校验器职责，实现轮落地）。
7. **回归判据只有那两个守卫**：回填材料里必须同时出现
   `python evals/retrieval_metrics.py --database-url <PG>` 与默认 SQLite 两侧的同读数、
   以及 `python evals/replay.py --self-check` 的 `REPLAY DIFF EMPTY`，且为**本轮输出**；
   `bench/` 内不得存在第三套契约套件。——**已可钉住**（两条命令与产生位置见 §4.1；
   其结果由 reviewer 阅读证据，不由本文件代为断言）。
8. **禁区 1（默认关闭 / flag 化）**：默认配置下 `retrieval_rerank_enabled` 仍为 `False`
   （`app/core/config.py:70`）、`.env.example:59` 仍为 `false`，
   `evals/rerank_latency.py:249` 的 verdict 仍为 `OVER BUDGET`，
   且 `README.md:199` / `README.md:423` / `docs/下一步规划.md:290` 的该定性未被改写。
   ——**部分可钉住**：配置默认值可由用例钉；「文档定性未被改写」当前**缺口**
   （`docs/fact-baseline.yaml` 无 OVER BUDGET 事实，`tests/` 内无断言该串的用例）。
9. **禁区 2（跨 worker 语义）**：模型用量台账、任务事件流、限流预算三处的事实源仍是数据库/共享存储
   （`app/services/llm_usage.py:1`、`app/models/llm_usage.py:13-20`、
   `app/services/runtime.py:1174-1177`、`app/api/middleware.py:125`）；
   未出现只在 bench 优化分支生效的进程内替代。——当前**缺口**：
   无守卫断言「事实源仍是表」，只能靠 reviewer 读 diff；等价性可用既有 PG 用例
   （`tests/test_postgres_concurrency.py:21`、`tests/test_postgres_outbox.py:22`，
   需 `TICKETPILOT_TEST_POSTGRES_URL`）作最小范围佐证。
10. **未越界**：本轮只新增本文件；`app/`、`tests/`、`bench/`、`docs/`、`Makefile`、
    `pyproject.toml`、`.qoder/rules/` 既有文件**零改动**；两个既有守卫的失败项数量不增长。

---

## 8. 边界（本口径明确不做）

1. **不设计 `bench/` 的目录内部形状**、不定 baseline 文件格式、不列具体测量模块清单
   （属实现轮；`benchmark-harness-draft.md` §1 那份形状可作参考，但它不是本口径的一部分）。
2. **不做性能门禁阈值**（不规定 p95 上限、不规定 CI 阻断条件）；本口径只规定「读数怎么产生、
   怎么算有效、破了行为用什么判」。
3. **不新增第三套回归判据**、不新增事实基线数字、不新增配置项、不新增迁移、不新增依赖。
   baseline 必须钉住的读数按可核对性分两类：**只有可现算的数字**（如 `test_collected`、
   `mypy_files` 这类由现算实现唯一确定的事实）才进守卫；p50/p95 这类**时点读数**不进守卫，
   改走**结构核对**——生成命令与日期必须出现在读数段里（§3.1 `produced_by`、§4.3 第 3 条），
   由校验器判形状缺失即 FAIL，不由数字守卫比对数值。
4. **不改 `testpaths`**、不改 pytest 的收集面。
5. **不修 LM Studio 的 reasoning tokens**（上游无开关，本口径只登记引用约束）。
6. **不把重排从「默认关闭」改判**，也不替它补任何「已优化」结论。
7. **不动 B-1 / B-3 / P8 / F-8 等业务口径文件**；本文件与它们只在「术语不得撞名」上相关。
8. **不在本轮把口径 1 的第 1、2 条落地**。本文件只声明；实现轮按 §7 第 1–3 条交付并同步文档
   （第 3 条的交付形态是「守住既有串不扩面」）。

---

## 9. 与来源的对齐

| 来源 | 本文件对应 |
|---|---|
| 编排方裁定「`bench/` 开工前必须先立方法论口径」 | §0 定位与生效时点 |
| 裁定 1：`bench/` 门禁归属三条 | §2（含 §2.1 守卫级联链条、§2.2/§2.3 待实现证据、§2.4 不得绕道） |
| 裁定 2：环境指纹必须多记两项（产出身份 + 上游干扰项）+ 重复性 | §3（§3.1 `produced_by`、§3.2 `reasoning_tokens_enabled`、§3.3 重复 ≥5） |
| 裁定 3：契约回归复用现成两个守卫，不另写一套 + 回填纪律 | §4 |
| 禁区 1：优化默认关闭 / flag 化，`OVER BUDGET` 定性不得改写 | §5.1 |
| 禁区 2：跨 worker 语义成立条件不得改 | §5.2 |
| 用户级 `AGENTS.md` §8「时点读数必须标时点 / 锚点必须逐字核对」 | §4.3 第 3–4 条 |
| `openspec/project.md` 不变量 6「跨模块契约改动要先写进项目级口径」 | §5.2（跨 worker 台账/事件流/限流正是跨模块契约的读法） |
| `agents.md`（项目级）「冲突时先停下、说明冲突、改口径文件，再改代码」 | §2.3 末段：与草案的冲突已由编排方 2026-10-27 裁定闭环，本文件采纳并取代草案 |

> **两处必须让 reviewer 看见的判断**：
>
> 1. **与在途草案的分歧已闭环（编排方 2026-10-27 裁定）**（§2.2 / §2.3 末段）：
>    采纳立场为「`mypy` 不扩面（`python -m mypy app` 范围不变）；`ruff` 覆盖 bench 走**独立
>    `lint-bench` 目标 + CI 独立步骤**，不改被文档锚定的主命令行，合并属收尾轮口径变更」。
>    OpenSpec 草案 `benchmark-harness-draft.md` §2 与本裁定**一致**；该草案**已被本文件取代**，
>    引用 bench 测量口径一律以本文件为准。实现者不得再回引草案，也不得以「草案里那么写」
>    为理由少做或多做一条。
> 2. **§2.5 / §7 第 4–9 条登记的是缺口，不是已达成**：`bench` 不在两个守卫的扫描清单里
>    （`tests/test_doc_code_anchors.py:39`、`tests/test_docs_consistency.py:202-217`），
>    `.qoder/rules/*.md` 也不在锚点扫描范围（`tests/test_doc_code_anchors.py:172`）。
>    因此「指纹齐全」「定性未被改写」「事实源仍是表」这三类判定**当前没有机器核对**，
>    只能由 reviewer 读 diff 兜底；本口径如实写出，不以「文档已声明」冒充「已被断言」。
