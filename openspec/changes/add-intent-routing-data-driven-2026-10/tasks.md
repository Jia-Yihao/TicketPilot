# Tasks: add-intent-routing-data-driven-2026-10

> 文档口径：本包写作时引用的 `docs/项目现状.md` 与 `docs/优化规划.md` 已并入
> `docs/下一步规划.md`（§15 裁定与三阶段、§1 现状），两份文件不再单独存在；合并登记见
> `docs/records/历史记录.md`。下文出现这两个路径时按上述位置理解，任务与证据内容未改写。

每步独立提交、独立过门禁。勾选前先贴读数。准则见 `dispatch.md` 的 C1–C14。

## 1. 摸底（只读，不改任何文件）

- [x] 1.1 复核 `app/agent/routing.py:41`、`:53`、`:298-314` 与 `app/agent/support_agent.py:158-187`、`:181-183` 的现状，确认与 `plan.md` 第 1 节一致；行号若已漂移，按符号重新定位
  - 读数（2026-10-27 实读）：`_REFUND_ACTION` 在 `routing.py:41`（6 条）、`_TICKET` 在 `:53`（5 条）逐字与 plan 一致；写意图 `if-elif` 当前在 `:297-314`（`blocked = blocks_write_action(text)` 在 `:298`，plan 的 `:298-314` 命中同一块）；`classify_support()` 在 `support_agent.py:158`，第 ① collaboration `:179-180`、第 ② 写意图 `:181-183`（含 `not (blocks_write_action(text))`）、第 ③ `:184-187`。**无行号漂移。**
- [x] 1.2 跑一次 `evals/intent_metrics.py`，把改造前的输出存成基线文件（**这是 A1 的对照物，丢了就没法验收**）
  - 读数：改造前基线 `.tmp/intent_before.json`；改造后 `.tmp/intent_step1.json`；`346` 条 `predicted` **逐条差异 0**，`invariants` 两侧相同（`diff-list.md` §1.1）
- [x] 1.3 存一份**端到端基线**：346 条的 `SupportPlan.intent` 序列（C2 的第一层门禁靠它）
  - 读数：`.tmp/equiv_pre.json`（改造前）/ `.tmp/equiv_step1.json`（步骤 1 后），每条含 `support_intent`；`判定漂移(硬)=0`
- [x] 1.4 阅读 `plan.md` 第 3 节的 T1–T6，确认理解后再动手
  - T1 问句门槛两处都要保留（实测在 `routing.py:298`、`support_agent.py:181-183`）；T2 优先级倒置（实测：**0 条语料分歧**，见 §3.1）；T3 `REFUND_ELIGIBILITY` 降级（实测 11 条，端到端已等于 catalog 读数，降级不生效）；T4 collaboration 先判（顺序未动）；T5 cues 逐字不放宽（`gap-refundstatus-07` 的既有害处照登）；T6 `DELIVERY_ISSUE` 也是 `write_path: true` 但不在 `Intent` 枚举（实测写行数 = 3）

## 2. 影子模式（不改判定）

- [x] 2.1 `intents.yaml` 给 `REFUND_REQUEST` 加 `cues`（逐字复制 `_REFUND_ACTION` 的 6 条）与 `match_request_only: true`
  - 读数：catalog 写行读数 `None → REFUND_REQUEST`，6 条 cues 与 `routing.py:41` 逐字一致（比对方式见 `diff-list.md` 复算命令）
- [x] 2.2 `intents.yaml` 给 `TICKET_CREATE` 加 `cues`（逐字复制 `_TICKET` 的 5 条）与 `match_request_only: true`
  - 读数：5 条 cues 与 `routing.py:53` 逐字一致；`write_path_rows=('REFUND_REQUEST', 'TICKET_CREATE', 'DELIVERY_ISSUE')`
- [x] 2.3 写比对脚本/测试：346 条语料上 `classify()` 与 `catalog.match()` 的**过程层**差异清单
  - 读数：`compare_write_intents.py` → `cases=346 过程层写意图差异=0`（改造前 14 条，全部登记在 `diff-list.md` §1.2）
- [x] 2.4 门禁：全量用例全绿 + 端到端基线与改造前逐条一致（此步不应有任何行为变化）
  - 读数：`1029 passed, 6 skipped in 178.15s` / 收集 `1035` / `test_py_files` `72` / 三条守卫 `24 passed` / ruff `All checks passed!` / mypy `Success: no issues found in 148 source files`；端到端 `判定漂移(硬)=0`、A1 逐条差异 0

## 3. 消除过程层差异

- [x] 3.1 处理 T2（`POLICY_QA` ↔ `LOGISTICS_QUERY` 顺序），用 346 条语料验证哪种对齐与现状一致，**不凭直觉**
  - 读数：catalog 侧 `POLICY_QA` priority **40** 先于 `LOGISTICS_QUERY` **60**；`classify()` 侧 `_LOGISTICS` 分支在 `_POLICY` 之前（`routing.py:307-310`）。346 条上 **`classify=LOGISTICS_QUERY ∧ catalog=POLICY_QA` 的语料 0 条** → 倒置在本语料不触发，**不改任何 priority**。结论与证据写在 `known-divergences.md` §3
- [x] 3.2 处理 T3（`REFUND_ELIGIBILITY` 被降级为 `POLICY_QA`），维持现状；无法表达则单独报批
  - 读数：降级在 `routing.py:303-304`；346 条上差出 **11 条**（10 + 1），每条 `SupportPlan` **已等于 catalog 的 `REFUND_ELIGIBILITY`** → 降级分支端到端惰性。维持现状，不用 YAML 表达降级，也不新增读行（`known-divergences.md` §4）
- [x] 3.3 处理 T4（collaboration 仍先判），确认 `classify_support()` 第 ① 步顺序未动
  - 读数：`support_agent.py:179-180`（第 ① collaboration）/ `:181-183`（第 ② 写意图，含 `not (blocks_write_action(text))`）/ `:184-187`（第 ③）三段顺序实读未变，本步骤 1 的改动面**不含** `app/agent/support_agent.py`（本轮只改 `intents.yaml` 与三份测试）。注：工作树自上一轮起就是脏的（`git status` 有大批在途改动），因此这条**不以 `git diff` 为空作证**，以实读行号作证。第 11 组 `gap-handoff-10` 由第 ① 步给出 `HUMAN_HANDOFF`，含 `转人工`/`人工客服` 的句子未被写行抢走
- [x] 3.4 每条对齐在差异清单里留「原因 → 处理」一行
  - 读数：`diff-list.md` §1.2 的表头即「原因 / 处理」两列，14 行逐条填写；本轮无需「对齐动作」（过程层已由步骤 1 归零），该表即其全部对象
- [x] 3.5 读路径的既有分歧不修，转入 `known-divergences.md`，逐条注明为什么 `SupportPlan` 不受影响
  - 读数：`known-divergences.md` 已建，12 组 / **228 条**，含统一理由（读意图由 `classify_support()` 第 ③ 步取 catalog 行）、残留影响面两处（`model_gate` 的 `gap_readable`、候选集 `rule:` 证据）与逐条清单
- [x] 3.6 门禁：过程层差异清单为空 + 端到端仍逐条一致
  - 读数：`过程层写意图差异=0`、`判定漂移(硬)=0`、A1 逐条差异 0（`.tmp/equiv_pre.json` ↔ `.tmp/equiv_step1.json`；`.tmp/intent_before.json` ↔ `.tmp/intent_step1.json`）

## 4. 切换

- [x] 4.1 `classify()` 写意图分支改为遍历 catalog 的 `write_path: true` 行（新增私有函数）
  - 读数：新增 `routing.py:278 _match_write_intent()`，`classify()` 的写分支现在是 `:333 blocked = blocks_write_action(text)` + `:334 write_spec = None if blocked else _match_write_intent(text)`；旧的 `_REFUND_ACTION` / `_TICKET` 两条 `if-elif` 已不再被判定引用（常量本体按步骤 5 才删）。`compare_write_intents.py` → **`过程层写意图差异=0`**（`classify_write` 与 `catalog_write` 346 条逐条相同）
- [x] 4.2 加 `_WRITE_INTENT_IDS` 白名单与 `_WRITE_INTENT_EXEMPT` 豁免集（含 `DELIVERY_ISSUE`）
  - 读数：`routing.py:271 _WRITE_INTENT_IDS = frozenset({Intent.REFUND_REQUEST, Intent.TICKET_CREATE})`、`:275 _WRITE_INTENT_EXEMPT = frozenset({"DELIVERY_ISSUE"})`；目录写行仍为 3 行（`write_path_rows=('REFUND_REQUEST', 'TICKET_CREATE', 'DELIVERY_ISSUE')`），`DELIVERY_ISSUE` 在循环里显式点名跳过（`:291`），不是裸 `continue`
- [x] 4.3 加断言：`write_path: true` 的行集合 ⊆ 白名单 ∪ 豁免集（C5，防止静默跳过）
  - 读数：断言并入 `tests/test_intent_catalog.py::test_every_enum_label_has_a_catalog_row`（既有用例体内，未新增函数）；4.8 的实测证明它会红
- [x] 4.4 T1 门槛保留：`blocks_write_action` 在 write 判定处仍生效，两处调用点都要有
  - 读数：`routing.py:333-334`（`None if blocked else …`）与 `support_agent.py:181-183`（`and not (blocks_write_action(text))`）两处都在；`「什么时候可以转人工」` 实测 `classify → POLICY_QA`、`_support → POLICY_QA`，而 `_match_write_intent("什么时候可以转人工")` **非 None**（cue 确实命中 `TICKET_CREATE`）——即门槛在挡，不是词表没命中
- [x] 4.5 若产生循环导入，下沉查询到 `support_agent` 层，**不改** `architecture-guard.yaml` 的依赖方向
  - 读数：`intent_catalog.py` 在模块级 `from app.agent.routing import blocks_write_action, …`，因此 `routing.py` 只能**函数内延迟导入** `get_catalog`（`_match_write_intent` 首行）；类型注解走 `if TYPE_CHECKING`（`:14-15`）。未改 `docs/architecture-guard.yaml`，`tests/test_architecture_guard.py` 通过（见 4.10 的 24 passed），即依赖方向未被打破。未采用「下沉到 support_agent 再传值进 `classify()`」，因为 `classify()` 有 4 个生产调用点（`pydantic_agent.py:397`、`support_agent.py:679`、`:1024`、`runtime.py:141`），加参数会让判定面被动扩到本轮范围之外
- [x] 4.6 问句门槛断言：**并入既有 routing / classify 用例体内**（不新增测试函数、不用 `parametrize`、不新建 `test_*.py`），断言「什么时候可以转人工」不产生 `REFUND_REQUEST` / `TICKET_CREATE`
  - 读数：追加在 `tests/test_intent_rules.py::test_policy_questions_never_trigger_write_actions`（既有参数化用例体内，参数项未增）；三条断言（classify 读数、生产路径 `_support` 读数、`_match_write_intent` 命中但被门槛挡住）全过
- [x] 4.7 假写意图断言：并入 4.6 同一个既有用例体内，用 `intent_catalog._load(tmp_path / "intents.yaml")` 构造目录（**不许走 `get_catalog()`**，它带 `lru_cache(maxsize=1)`），断言加一行 `write_path: true` 即生效
  - 读数：并入 `tests/test_intent_catalog.py::test_the_catalog_row_replaces_the_agent_branch`（同文件既有用例，已带 `tmp_path` / `monkeypatch`），两段：① `_load` 一份含 `FAKE_WRITE`（`write_path: true` + cue）的临时 YAML，`patched.match("假写的动作词")` 返回 `FAKE_WRITE`，生产目录对同一句话返回 `None`；② 把 `REFUND_REQUEST` 的 cue 在临时 YAML 里加一条「那笔钱给我退回来」，`monkeypatch` 换掉 `intent_catalog.get_catalog` 后 `classify()` 立即判成 `REFUND_REQUEST`，`monkeypatch.undo()` 后回到 `UNKNOWN`。**与 plan §4.3 的一处不一致（已按代码现实落地并报告）**：plan 要求「假意图的 `intent_id` 必须是 `Intent` 已有成员」，但 `_load` 对重复 `intent_id` 加载期即报错（`test_a_duplicate_intent_row_is_rejected`），而 `Intent` 六个成员在目录里**都有行**（`test_every_enum_label_has_a_catalog_row`），所以「新增一行且用既有成员」在数学上不可能；本轮拆成 ①（新行 `FAKE_WRITE`，证 catalog 判定源）+ ②（用可认领成员 `REFUND_REQUEST` 证 classify 判定源）。`FAKE_WRITE` 用 `intent_id` 不在白名单，正好由 4.3 的子集断言拦住，不属越界
- [x] 4.8 反向验证失败可见：临时加一行 `write_path: true` 但不在白名单/豁免集，4.3 的断言必须失败（**手工验证，不留用例、不提交**）
  - 读数（2026-10-27 手工，用完即恢复）：向 `app/agent/intents.yaml` 追加 `NEW_WRITE_ROW`（`write_path: true`）后 `pytest tests/test_intent_catalog.py::test_every_enum_label_has_a_catalog_row` → **`1 failed`**，消息 `AssertionError: 未登记的写路径行 ['NEW_WRITE_ROW']：先扩 Intent 与 routing._WRITE_INTENT_IDS，或显式加入 routing._WRITE_INTENT_EXEMPT 并写明理由`；随后从 `.tmp/intents_backup_4_8.yaml` 恢复，`diff -q` 输出空 + `IDENTICAL`。未提交该临时行
- [x] 4.9 收集数不变核对：`pytest --collect-only` 仍为 **1035**，`test_py_files` 仍为 **72**；任一变大说明新增了函数 / 参数项 / 测试文件，回头改写位置
  - 读数：`1035 tests collected in 0.75s`；`ls tests/*.py | wc -l` = `72`
- [x] 4.10 门禁：端到端逐条一致 + 全量全绿（1035）+ 三条守卫
  - 读数：端到端 `判定漂移(硬)=0`（`.tmp/equiv_pre.json` ↔ `.tmp/equiv_step4.json`）、`catalog 读数变化(留档)=14`（与步骤 1 同一批，软字段）、`读路径既有分歧=228`（12 组，与步骤 1 逐组同数）；A1 `.tmp/intent_before.json` ↔ `.tmp/intent_step4.json` **346 条 `predicted` 逐条差异 0**，`layers` 两层的全部质量字段相同（`correct/wrong/false_unknown/false_unknown_write/gold_unknown/unknown_predictions/unknown_matched/unknown_misread/coverage/accuracy/answerable_accuracy` 逐项相等），仅 `rule_latency_ms` 随机器负载浮动（`authored_gap 5.558 → 5.935`、`external 45.421 → 10.35`）；`model_gap` / `version` / `label_rule` / `dataset` 相同。全量 `1029 passed, 6 skipped, 1 warning in 183.41s`（0 failed）；三条守卫 `24 passed in 4.29s`；`ruff check app tests scripts evals alembic` → `All checks passed!`；`mypy app` → `Success: no issues found in 148 source files`。附注：本轮改动 `routing.py` 会轮转 `RULESET_FINGERPRINT`（`routing.py:15-36` 按源码字节做摘要，无任何用例硬编码其值），模型决策缓存因此按既有设计作废一次


## 5. 收尾

- [x] 5.1 删除 `_REFUND_ACTION` / `_TICKET` 常量与对应 `if-elif` 分支（先确认全仓零引用）
  - 读数（删除前，2026-10-27）：`grep -rn "_REFUND_ACTION\|_TICKET\b" app tests scripts evals docs --include=*.py --include=*.yaml --include=*.md` → **零命中**；`grep -n "REFUND_ACTION\|_TICKET" docs/code-anchors.yaml` → 退出码 `1`（无锚点，删除不会打红符号守卫）。删除后写分支只经 `_match_write_intent()`（当时记作 `routing.py:278`；**步骤 5 完成时复算为 `:276`**，因删除两行常量而前移，`classify()` 现为 `:298`、门槛现为 `:331-332`）。`读路径`词表 `_REFUND_ELIGIBILITY`/`_LOGISTICS`/`_POLICY`/`_ORDER` 按本轮范围保留（阶段 B 只迁写意图）。删除后复验：`pytest tests/test_intent_catalog.py tests/test_intent_rules.py -q -o addopts=` → **`55 passed in 0.61s`**
- [x] 5.2 更新 `intents.yaml` 头部注释，使「加一行即可」成立
  - 读数：头部第 8–9 行改为「**read** capability = 加一行 + 一个 action 实现；写行见文末脚注」，写路径的**唯一被校验的规则**（`_WRITE_INTENT_IDS` / `_WRITE_INTENT_EXEMPT` 子集断言 + 会红的用例名）写在**文件末尾脚注**（UNKNOWN 行之后）。之所以拆头/尾：头部原来那一句是假的（写行不改代码判不了），而将整段说明放在头部会把 `version` 从 `:31` 推走、连带打漂 `docs/spec/项目规格.md:4150`（引 `:51`/`:96`）与 `.qoder/rules/refund-reason-taxonomy.md`（引 `:31`/`:49-55`/`:80`/`:84-87`/`:93-96`）等**本轮不可写文件**的引用。改动为**净零行**（头部替换 2 行、脚注追加在文件尾），复验点读：`:31 version: "2026-10-27"`、`:49-55` 槽位块、`:51` 槽位话术、`:80 - intent_id: REFUND_REQUEST`、`:84 required_slots:`、`:93 clarify_prompts:`、`:96 refund_reason:` 全部原位
- [x] 5.3 `docs/项目现状.md` D1 / D2 标收口
  - 读数：§五 D1 改为「**已收口（F-9 阶段 B，2026-10-27）**」，证据指向 `app/agent/routing.py` 的 `_match_write_intent()` 与 `intents.yaml` 两行写意图 `cues`，并写明「换的是判定源，不是判定结果：346 条语料端到端逐条一致（含 `UNKNOWN` 的位置）」；D2 改为「**部分收口**」——写意图两套词表已合一（`_REFUND_ACTION` / `_TICKET` 已删），读路径四张词表 `routing.py:45-58` 与目录 cues 仍各写一份，「加一行即可」现在只对读行成立。同步面：第 3 行的根因句（并标注另一头 D3/D5 仍在）、§4.1 流程图①②两行（引用重新逐行核到 `routing.py:298`/`:276`、`intent_catalog.py:208`、`support_agent.py:113`/`:158`/`:1513`）、第 113 行依赖句追加「这一根因已随 F-9 阶段 B 拔掉」、§六 在途清单插入本变更、未完成项计数 13 → 12、在途变更计数 2 → 3。图内新增 1 行属文档内部漂移：已 `grep` 确认没有任何文件按行号引用 `docs/项目现状.md` / `docs/优化规划.md`，锚点守卫只登记符号名不登记行号，故不会打红。
- [x] 5.4 `docs/下一步规划.md` §0.2 的 F-9 改完成
  - 读数：§0.2 总表 F-9 行现状列改为「**已收口**」并点名 `_match_write_intent()` 已删（验收列原样不动，裁定列补「完成（2026-10-27 阶段 B；等价重构，判定结果零变化，裁定见 ADR-037）」）；F-1 行的现状改为「已裁定，待实施（依赖的 F-9 已于 2026-10-27 收口，可开工）」；流程状态段追加本变更「任务 1–5 已完成、待归档」，并如实写明「本变更未写 `specs/` 增量，归档时按 `openspec/AGENTS.md` 的约定处理」。登记：`docs/优化规划.md:92` 曾承诺为 F-9 在 §10.2 新增一节、§10.1 总表也应有一行，本轮**未补**（§10.1/§10.2 不在 dispatch §3 的可写清单内），作为已登记缺口进最终报告。
- [x] 5.5 `docs/优化规划.md` 阶段 B 标完成
  - 读数：阶段 B 标题加「**已完成（2026-10-27）**」；现状行改判为已收口；做什么行补「**实际落地范围**：判定源迁移 + 「加一行即生效」被用例证明；「合成优先级写进契约文件」这一句**本轮未做**（登记在 ADR-037 的「未做」段）」；验收行逐条区分「有本轮读数」与「未跑」——**11 句探针（`scripts/llm_gap_probe.py`）本轮未跑**，理由：需真实模型调用，属评测侧。第 3 行整体结论与第 52 行的「仍锁在 Python 里」按新现实限定到读路径。`:110` 的变更目录名与 `实施指导.md` 是失效引用，已在可写文档内改为真实目录 `openspec/changes/add-intent-routing-data-driven-2026-10/` 与实际文件清单。
- [x] 5.6 新增 ADR-037（接 ADR-036），记录决策、被否决的「全面委托 catalog」方案、`DELIVERY_ISSUE` 豁免
  - 读数：新增 `docs/adr/ADR-037-写意图判定迁到意图目录.md`（`ls docs/adr/ADR-*.md | wc -l` → **36**）。九节决策覆盖 cues 逐字复制与子串语义等价、三处问句门槛 + `_match_write_intent(...) is not None` 断言、端到端两层门禁（漂移 0、软字段 14 条留档）、函数内延迟导入破循环依赖且不反转依赖方向、白名单 + 豁免 + 子集断言（4.8 实测新写行打红）、「加一行即生效」的两段证明、YAML 头尾注释与净零行的引用面理由、六条明确不做、两处副作用（`RULESET_FINGERPRINT` 不覆盖 `intents.yaml`＝本轮新暴露缺口；`candidates.py:110` 不读 `match_request_only`＝离线 A/B 实测 346 条 consult=99、候选集变化=0）。被否决 6 条含「全面委托 catalog」。
- [x] 5.7 ADR 篇数 35 → 36 同步 6 处：`README.md:13`、`CONTRIBUTING.md:171`（含范围 `ADR-037`）、`docs/下一步规划.md:28`、`docs/spec/项目规格.md:3703`、`docs/guide/证据索引.md:673`、`docs/records/智能体平台设想.md:178`
  - 读数：C12 六处**逐处替换并断言命中数==1**（脚本输出四处 `hits= 1`，README 两处与 CONTRIBUTING 用 Edit 单点替换）。范围串同改为 `ADR-001…ADR-011、ADR-013…ADR-037`（`CONTRIBUTING.md:171`、`docs/spec/项目规格.md:3703`、`docs/guide/证据索引.md:673`）；`docs/spec/项目规格.md:3703` 另加一句「ADR-037 随 F-9 阶段 B 于同日落地」，避免把新增读数冒充上一轮实测。
  - **超出 C12 清单的三处，按 `CONTRIBUTING.md:171-174` 的既有约定补齐**（那段明写「`docs/records/智能体平台设想.md` 的 §9.4 ADR 索引、编号说明与文件计数要同步改」）：① 实测第 7 处篇数 `docs/项目现状.md:25` `| 架构决策记录 | 35 篇 |` → 36；② `docs/records/智能体平台设想.md:1078` ADR 索引表新增 ADR-037 行（与 ADR-035/036 行同风格）；③ 同文件编号说明追加「ADR-037（写意图判定迁到意图目录）随 F-9 阶段 B 落地。」与文件计数行 `共 35 个文件` → `共 36 个文件`。
  - 复验：`grep -rn "35 篇\|35 个文件" README.md CONTRIBUTING.md docs/ .github/` → **零命中**；`openspec/changes/add-perf-guardrails-and-measurement-2026-10/plan.md` 的历史读数**未动**（C12 唯一例外）。
- [x] 5.8 跑 `scripts/build_manual.py` 重生成 docx
  - 读数：`.venv/Scripts/python.exe scripts/build_manual.py` → `source: docs/spec/项目规格.md` / `output: docs/spec/TicketPilot项目说明书.docx` / `bytes=291807 paragraphs=4131 tables=91 fffd=0 parts=13` / **`MANUAL OK`**（因 5.7 动了被 `in_manual: true` 绑定的 `docs/spec/项目规格.md:3703`）。
- [x] 5.9 门禁：全量用例 + 三条守卫 + `ruff` + `mypy` 全绿
  - 读数（2026-10-27，收尾后）：全量 `pytest -q -o addopts=` → **`1029 passed, 6 skipped, 1 warning in 191.43s`**；`pytest --collect-only -q -o addopts=` → **`1035 tests collected`**（与改造前一致，未新增收集项）；三条守卫 `test_docs_consistency.py test_doc_code_anchors.py test_architecture_guard.py` → **`24 passed in 4.10s`**；`ruff check app tests scripts evals alembic` → **`All checks passed!`**（仅 `UP038` 规则已移除的既有告警）；`mypy app` → **`Success: no issues found in 148 source files`**。
  - A1 复跑（步骤 5 之后）：`evals/intent_metrics.py --json .tmp/intent_step5.json` 与改造前基线 `.tmp/intent_before.json` 逐条比对 → **346 条 `drift= 0`**；`invariants` 与 `model_gap` 完全一致；差异只有 `layers.*.rule_latency_ms`（时点计时）与 `cue_overlap`（`with_gold_cue` 61 → 74，`by_intent` 新增 `REFUND_REQUEST: 3` / `TICKET_CREATE: 10`，因写行 cues 首次进入目录 cue 表）。复跑一次 `.tmp/intent_step5b.json` → `rerun drift= 0`，`external` 层 9.586 → 9.823 ms（两次复跑彼此稳定）。注：基线的 `external 45.421` 与步骤 4 的 `10.35` 之间的落差**在本轮之后没有回到过 45 ms**，因此既不能归因于冷启动、也不归因于本轮改动，按**未归因的时点计时读数**如实登记，不据此下任何加速结论。

## 6. 明确不做

- [x] 6.1 不改采纳面（`support_agent.py:1513`）
  - 核验读数：该行现值 `if source != "pydantic_ai" or routed.intent is not Intent.REFUND_REQUEST:` —— 采纳面仍只有 `REFUND_REQUEST`，本轮零改动。
- [x] 6.2 不动 `model_gate()` 的 `question` 出口（属阶段 A）
  - 核验读数：`model_gate()` 仍在 `support_agent.py:113`，`return "question"` 仍在 `:130`，docstring 的 `question` 解释（`:118`/`:120`）逐字未动。阶段 A（F-1「模型读数换成一次反问」）未开工。
- [x] 6.3 不新增意图行（用户裁定：目录暂不扩张）
  - 核验读数：`grep -c '^  - intent_id: '` 现算 `app/agent/intents.yaml` → **12 行**，与改造前一致；本轮只给 `REFUND_REQUEST` / `TICKET_CREATE` 两行填了**逐字复制**的 `cues`，未加词、未合并同义（C6）。4.8 用 `_load()` 造的 `NEW_WRITE_ROW` 假意图写在测试的 tmp 目录里，不进生产 YAML。
- [x] 6.4 不动 `confidence` 字段（F-3 独立决定）
  - 核验读数：`app/agent/pydantic_agent.py` 的 `IntentOutput.confidence: float = Field(ge=0, le=1)` 原样保留；`grep -rn confidence app --include=*.py` 除该声明外只命中 `app/agent/candidates.py:11` 的模块文档注释（「Nothing here reads the model's own claim about itself」），本轮**没有新增任何读取点**。
- [x] 6.5 不新增评测语料、不改 `evals/intent_metrics.py` 的指标口径（评测扩量本轮不做）
  - 核验读数：`git status --short -- evals/intent_metrics.py evals/intent/` → 两者均为 **`??`（未跟踪，属 P1 轮产物）**，本轮没有产生新的改动记录；文件系统 mtime 停在 `2026-10-25 21:13–21:56`，而本轮所有写入均为 `2026-10-27`。该脚本只被用作 A1 等价性回归的读数来源（`.tmp/intent_before.json` vs `.tmp/intent_step5.json` → `drift= 0`），语料 346 条与分层口径未动。
