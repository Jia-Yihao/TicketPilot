# 实施准则：意图路由数据驱动化（F-9 / 阶段 B）

> 文档口径：本包写作时引用的 `docs/项目现状.md` 与 `docs/优化规划.md` 已并入
> `docs/下一步规划.md`（§15 裁定与三阶段、§1 现状），两份文件不再单独存在；合并登记见
> `docs/records/历史记录.md`。下文出现这两个路径时按上述位置理解，任务与证据内容未改写。

## 0. 怎么用这份文件

开工前通读一遍，每次提交前逐条对一遍。准则编号 C1–C14。**违反任何一条即返工**，不接受「效果更好」作为解释。

技术方案在 `plan.md`，任务清单在 `tasks.md`。这份文件只回答一件事：**做的时候什么允许、什么不允许、什么时候必须停下来问**。

## 1. 任务性质

这是**等价重构**，不是效果改进。改前改后，同一句话必须得到同一个意图，包括 `UNKNOWN` 落在哪些句子上。任何一处判定变化，即使看起来更准，也算失败。

## 2. 准则

### C1 判定只许不变，且以端到端为准

判据对象是 `classify_support()` 输出的 `SupportPlan.intent`，346 条语料逐条一致。不是 `classify()` 的中间值，不是「大致对得上」。

违反后果：返工。

### C2 门禁分两层，不能只卡过程层

| 层 | 判据 |
|---|---|
| 端到端 | `SupportPlan.intent` 346 条逐条不变 |
| 过程 | `classify()` 写意图分支 vs catalog 写行，差异为空 |

只卡过程层会漏掉合成顺序：某句同时命中写意图 cue 与 collaboration cue 时，`classify()` 给 `TICKET_CREATE`、`classify_support()` 给 `HUMAN_HANDOFF`，过程层看不出问题，端到端已经翻转。

### C3 问句门槛两处都不能丢

`blocks_write_action()` 在 `routing.py:298` 与 `support_agent.py:181-183` 各调用一次。迁移词表时两处都要保留。

判据：「什么时候可以转人工」不得产出 `REFUND_REQUEST` / `TICKET_CREATE`。

违反后果：写路径被问句触发，属严重缺陷。

### C4 转人工先于写路径

`classify_support()` 第一步的 collaboration 判断顺序不许动。`_TICKET` 与 `HUMAN_HANDOFF` 的 cue 都含「转人工」「人工客服」，顺序一变就变成建单而不是转人工。

### C5 跳过必须可观测，禁止裸 `continue`

`DELIVERY_ISSUE` 也是 `write_path: true`，但不在 `Intent` 枚举里。按 2.1 节的白名单 + 豁免集处理，并且加一条断言：`write_path: true` 的行必须 ⊆ 白名单 ∪ 豁免集。

新增一行写意图而不在这两个集合里，**测试必须失败**并提示先扩枚举。静默跳过会让「加一行即生效」第二次失效，且失效时没有任何提示。

### C6 写意图 cues 只许逐字复制

`REFUND_REQUEST` 的 6 条、`TICKET_CREATE` 的 5 条，逐字照抄，不加词、不收窄、不合并同义。加词是独立事项，不混在本轮——否则就没有等价性基线可言。

### C7 不扩采纳面，不碰模型闸门

- `support_agent.py:1513` 的采纳判据不动。
- `model_gate()` 的 `question` 出口不动。
- 不实现「模型读数 → 反问」。那是阶段 A（F-1），不是本轮。

### C8 不改守卫清单

`docs/fact-baseline.yaml`、`docs/code-anchors.yaml`、`docs/architecture-guard.yaml` 三份不许改。为了让门禁变绿而改清单，视为作弊，直接返工。

### C9 不动评测语料与指标口径

`evals/intent_metrics.py`、`evals/intent/**` 只用于 A1 回归。不新增语料、不改指标定义、不改分层口径。**评测扩量本轮不做**——等价性回归是门禁，不是评测建设。

### C10 五步五提交，不合并

`tasks.md` 的 1–5 组各一次提交。步骤 1（影子模式 + 差异清单）必须先交，清单没出来之前不许动判定逻辑。合并提交会让 diff 不可归因，等价性结论就失效了。

### C11 用例期望只在结论不变时改

直接调 `model_gate()` / `classify()` 的用例，判定来源变了之后可能要给新期望：

- 允许：来源变了、端到端结论相同。
- 不允许：期望从「不 consult」变成「consult」，或从 `UNKNOWN` 变成某个意图——那是行为变了，停下报批。
- 必须补：一条生产路径用例（走 `classify_support`），证明这句话仍然不进 `consult`。只改单元期望不补端到端用例，视同未验证。

### C12 文档数字要全量同步，不止守卫绑定的

ADR-037 写入后「35 篇 ADR」要改 6 处：`README.md:13`、`CONTRIBUTING.md:171`（含括号范围改到 `ADR-037`）、`docs/下一步规划.md:28`、`docs/spec/项目规格.md:3703`、`docs/guide/证据索引.md:673`、`docs/records/智能体平台设想.md:178`。

守卫只绑其中两处，按「守卫不红就行」去改会留下 4 处错数，其中 README 是门面读数行。改完 `docs/spec/项目规格.md` 必须跑 `scripts/build_manual.py`。

唯一例外：`openspec/changes/add-perf-guardrails-and-measurement-2026-10/plan.md:164` 的 `35 篇` 是另一个变更的历史记录，**不要改**。

### C13 收集数与测试文件数保持不变

本轮所有新断言都**并入既有用例体内**，不改收集数：

- 收集数必须保持 **1035**，`tests/*.py` 必须是 **72** 个。
- 不许新增测试函数——新增一个就是 1036。
- 不许用 `pytest.mark.parametrize`——**每个参数项各算一条收集**，这是最容易漏的一条。
- 不许新建 `test_*.py` 文件——`test_py_files` 事实会跟着变。
- 假意图必须用 `intent_catalog._load(tmp_path / ...)` 构造，**不许走 `get_catalog()`**（`@lru_cache(maxsize=1)`，改不动也清不干净）。

理由：「1035 条全绿」既是本轮的验收基线（A2），也是三处守卫事实（`docs/fact-baseline.yaml` 的 `test_collected` 与 `test_py_files`）。改收集数就要同步 9 份文档里 26 处 `1035`（其中 5 处是历史推导，如「885 → 1035 的 150 条」，改成新数会把历史叙述写错），还要重生成 docx。等价重构不该附带这么大的文档改动。

断言失败时要能自证：消息必须写清楚预期与实测，例如 `假意图未生效：catalog.match() 返回 REFUND_REQUEST，期望 FAKE_WRITE`。

### C14 该停下的时候停下

遇到以下任一情况，停下来报批，不要自行裁决：

1. 346 条里有判定变化，且你说不清原因
2. 必须改 `Intent` 枚举才能继续
3. 必须改守卫清单才能让门禁变绿
4. 发现 `plan.md` 的行号与代码不一致（以代码为准，报告差异）
5. 需要动 `docs/spec/项目规格.md` 的契约条款
6. 实现需要扩大采纳面或改 `model_gate`

## 3. 可写与禁写

**可写**：`app/agent/intents.yaml`、`app/agent/routing.py`（仅写意图分支与两个常量）、`app/agent/support_agent.py`（仅 `classify_support` 的写意图判据）、`tests/`（**只在既有用例体内追加断言**，不新增测试函数 / 参数项 / 测试文件，见 C13）、`docs/adr/`（新增 ADR-037）、`docs/项目现状.md` / `docs/下一步规划.md` / `docs/优化规划.md`（仅状态词）、C12 列出的六处 ADR 篇数。

**禁写**：`app/services/`、`app/sql/`、`app/retrieval/`、`app/tools/`、`app/api/`、三份守卫 `.yaml`、`evals/` 下的语料与指标脚本、`docs/spec/项目规格.md` 的契约条款（除 C12 的篇数）。

## 4. 门禁命令

```bash
# A1 等价性：改造前后各跑一次，逐条 diff 必须为空
.venv/Scripts/python.exe evals/intent_metrics.py --json .tmp/intent_after.json

# A2 全量
.venv/Scripts/python.exe -m pytest -q -o addopts=

# A5 文档与分层守卫
.venv/Scripts/python.exe -m pytest tests/test_docs_consistency.py \
    tests/test_doc_code_anchors.py tests/test_architecture_guard.py -q -o addopts=

# 静态
.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
.venv/Scripts/python.exe -m mypy app
```

判据：A1 逐条一致、全量收集数不减少且 0 failed、守卫 24 passed、ruff 与 mypy 全绿。动了 `docs/spec/项目规格.md` 追加 `scripts/build_manual.py`。

## 5. 必交产物

1. 每一步的提交，信息含「改了什么、为什么、读数」。
2. **过程层差异清单**：语料 id / 原文 / `classify` 判定 / `catalog` 判定 / 原因 / 处理，终态为空。
3. **既有分歧档案** `known-divergences.md`：读路径的既有分歧，逐条说明为什么不影响 `SupportPlan`。
4. 端到端基线（改造前 346 条的 `SupportPlan.intent` 序列）。
5. 最终报告：A1–A5 逐条结论 + 回滚方法。
