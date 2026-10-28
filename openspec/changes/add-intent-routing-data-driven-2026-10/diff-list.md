# 差异清单（过程层）— 阶段 B / F-9

本文件是 `plan.md` §4.0 与 `tasks.md` 2.3 要求的留档物：**每一行都是一次「谁判的、判成什么、为什么、怎么处理」的记录**。

复算方式（只读，不改文件）：

```bash
# 改造前基线（步骤 1 之前采集）
.venv/Scripts/python.exe openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py \
    --out .tmp/equiv_pre.json
# 本步之后与基线比对；退出码 0 = 端到端与 classify 判定零漂移
.venv/Scripts/python.exe openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py \
    --out .tmp/equiv_step1.json --diff .tmp/equiv_pre.json
```

口径提醒（`plan.md` §4.0）：

* **端到端**（`classify_support()` 的 `SupportPlan.intent`）是用户可见判定，**必须逐条不变**。
* **过程层**是本轮真正改动的对象：`classify()` 的写意图分支 vs `catalog` 的 `write_path: true` 行。
* 「catalog 读数变化」不是行为变化：切换之后 catalog 就是写意图的判定来源，它当然要和以前不一样。
* 读路径上 `classify()` 与 `catalog.match()` 的**既有分歧**不在本清单，见 `known-divergences.md`。

---

## 步骤 1（影子模式）

### 1.1 门禁读数

```
cases=346 write_path_rows=('REFUND_REQUEST', 'TICKET_CREATE', 'DELIVERY_ISSUE')
过程层写意图差异=0
判定漂移(硬)=0  catalog 读数变化(留档)=14
```

`intent_metrics.py` 的 A1 对照：`.tmp/intent_before.json` vs `.tmp/intent_step1.json` → **346 条 `predicted` 逐条差异 0**；`invariants` 两侧相同；两层 `correct / wrong / false_unknown / false_unknown_write / gold_unknown / unknown_matched / unknown_misread / coverage / accuracy / answerable_accuracy` 全部相同（只有 `rule_latency_ms` 是机器负载噪声，45.421 → 10.246 与 5.558 → 6.002）。

**如实登记一处诊断量变化（不是判定变化）**：`cue_overlap.with_gold_cue` 61 → 74，`by_intent` 多出 `REFUND_REQUEST: 3`、`TICKET_CREATE: 10`。这个字段的定义是「语料里有多少句话天然含有其 gold 意图的 cue」（`evals/intent_metrics.py:427` 的 `_cue_overlap`，模块文档写明「a visibility number, not a score」）。本轮往目录里加了 11 条 cue 字符串，被数到的句子随之变多——它是**catalog 数据的可见副作用**，不进任何门禁判定（`--enforce` 只查 `exact_cue_texts` 与「overlap ≤ 语料条数」，两项均未变、仍成立）。

门禁（2026-10-27 本轮实跑，全部为当日输出）：

```
1029 passed, 6 skipped, 1 warning in 178.15s (0:02:58)     # python -m pytest -q -o addopts=
1035 tests collected in 0.64s                                # python -m pytest --collect-only -q -o addopts=
72                                                           # ls tests/*.py | wc -l（= test_py_files 事实）
24 passed in 4.48s                                           # 三条守卫：architecture_guard + doc_code_anchors + docs_consistency
All checks passed!                                           # ruff check app tests scripts evals alembic
Success: no issues found in 148 source files                 # python -m mypy app
```

收集数 1035 与 `test_py_files` 72 均与改造前逐字相同（本轮只改既有用例体内断言，不新增函数 / 参数项 / 测试文件），事实基线不发生级联。

### 1.2 过程层差异清单：改造前 14 条 → 步骤 1 之后 0 条

改造前，14 条语料的写意图只有代码词表读得出、目录读不出（下表「catalog 写读数」一列的 `None →` 即此）。步骤 1 之后**过程层写意图差异为 0**（脚本读数），因此**步骤 2（消除过程层差异）没有活要干**——本表即其全部对象。

| 语料 id | 原文 | classify 判定 | catalog 判定（改造前 → 步骤 1 后） | 原因 | 处理 |
|---|---|---|---|---|---|
| ext-handoff-05 | 在你们家呆了7天告诉我邮不了电话给我不给我投诉我不想骂你跟你没关系叫你们管事的来 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() None → TICKET_CREATE | 目录的 `TICKET_CREATE` 行没有 cues，`投诉` 只在代码词表 `_TICKET` 里；句子未含 `HUMAN_HANDOFF` 的任何 cue，collaboration 行不抢 | 给该行补 cues（逐字复制 `_TICKET`）+ `match_request_only: true`；端到端 TICKET_CREATE → TICKET_CREATE 不变 |
| ext-refundreq-09 | 好了您同意退款申请吧 | REFUND_REQUEST | 写行 None → REFUND_REQUEST；match() None → REFUND_REQUEST | 同上，`退款申请` 只在 `_REFUND_ACTION` | 同上；端到端不变 |
| ext-refundreq-14 | 我要退款丛新下单还要加一袋在吗亲 | REFUND_REQUEST | 写行 None → REFUND_REQUEST；match() POLICY_QA → REFUND_REQUEST | 改造前 catalog 先命中读行 `POLICY_QA`；端到端此前由 `classify_support()` 第 ② 步（`route.intent`）给出 REFUND_REQUEST，catalog 的 POLICY_QA 从未被采纳 | 补 cues 后 catalog 与 classify 同一读数；端到端不变 |
| ext-refundstatus-02 | 早已申请退款退货 | REFUND_REQUEST | 写行 None → REFUND_REQUEST；match() POLICY_QA → REFUND_REQUEST | 同 ext-refundreq-14 | 同上；端到端不变 |
| ext-ticket-01 | 你们店对于消费者就这么个态度我非投诉你们不可 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() None → TICKET_CREATE | 同 ext-handoff-05 | 同上；端到端不变 |
| ext-ticket-02 | 那我只能投诉 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() None → TICKET_CREATE | 同上 | 同上；端到端不变 |
| ext-ticket-03 | 我前天打了20分钟电话难道还要打吗妳们到底想干嘛不行我不要了我要投诉妳们 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() POLICY_QA → TICKET_CREATE | 句中含疑问词，但 `我要` 是请求框架 → `blocks_write_action` 为假，写意图成立；catalog 改造前落在 POLICY_QA | 同上；端到端不变（问句门槛在 YAML 侧由 `match_request_only` 兑现，与代码侧同一个 `blocks_write_action`） |
| ext-ticket-04 | 我刚和他掛完电话在上海已经转了3天了你们到底想干嘛今天收不到我不要了我要去淘宝投诉你们 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() None → TICKET_CREATE | 同 ext-ticket-03（`我要` 请求框架使门槛不触发） | 同上；端到端不变 |
| ext-ticket-06 | 等着收差评我要投诉你 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() None → TICKET_CREATE | 同 ext-ticket-03 | 同上；端到端不变 |
| ext-ticket-07 | 要补就发顺丰不行就按照程序来快递和你们一起投诉就1这样 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() LOGISTICS_QUERY → TICKET_CREATE | 改造前 catalog 先命中 `LOGISTICS_QUERY`（priority 60）；端到端一直走第 ② 步的 TICKET_CREATE | 补 cues 后 `TICKET_CREATE`（priority 11）先于物流行；端到端不变 |
| ext-ticket-08 | 顺便帮我投诉一下这个快递员态度非常恶劣谢谢了 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() LOGISTICS_QUERY → TICKET_CREATE | 同 ext-ticket-07，且 `帮我` 是请求框架 | 同上；端到端不变 |
| ext-ticket-09 | 我准备投诉贵店 | TICKET_CREATE | 写行 None → TICKET_CREATE；match() None → TICKET_CREATE | 同 ext-ticket-03（`我准备` 是请求框架） | 同上；端到端不变 |
| gap-refundreq-05 | 帮我退款 ORD-DEMO-1001 129 元 | REFUND_REQUEST | 写行 None → REFUND_REQUEST；match() ORDER_QUERY → REFUND_REQUEST | 改造前 catalog 靠 `match_order_id` 命中 `ORDER_QUERY`；端到端走第 ② 步 | 补 cues 后 `REFUND_REQUEST`（priority 10）先于 ORDER_QUERY 行；端到端不变 |
| gap-refundstatus-07 | 退款申请已经提交，129 元还没到账 | REFUND_REQUEST | 写行 None → REFUND_REQUEST；match() None → REFUND_REQUEST | 「退款申请」出现在**陈述已提交**的句子里，代码词表把它读成 REFUND_REQUEST（现状如此，本轮是等价重构，不修） | 逐字复制 cues，因此 catalog 同样读出 REFUND_REQUEST；端到端不变。**这是 T5「cues 不得放宽」的既有代价之一，登记不改** |

### 1.3 步骤 1 的改动面

* `app/agent/intents.yaml`：`REFUND_REQUEST` 加 `match_request_only: true` + 6 条 cues（逐字复制 `_REFUND_ACTION`）；`TICKET_CREATE` 加 `match_request_only: true` + 5 条 cues（逐字复制 `_TICKET`）；`version` 随目录数据 bump 为 `"2026-10-27"`。**判定逻辑一行未改。**
* cues 追加在两行的**行尾**（`clarify_prompts` 之后），因此文档点名的 `intents.yaml:51` / `:67` / `:96` 三处行号未漂移。
* `tests/test_intent_catalog.py` 的版本写死钉子随 bump 更新（在用例体内加一条 `!= "2026-10-26"`，不新增用例）。

---

## 步骤 2 / 3（对齐与归档）

**零代码改动**：过程层差异已由步骤 1 归零，因此步骤 2 没有对齐动作可做，其全部对象是 §1.2 那份表；
步骤 3 的三条 traps 用 346 条语料实测后判定如下。

| 条目 | 实测 | 处理 |
|---|---|---|
| T2 `POLICY_QA`(40) 与 `LOGISTICS_QUERY`(60) 的 priority 倒置 | `classify=LOGISTICS_QUERY ∧ catalog=POLICY_QA` 的语料 **0 条** | **不改任何 priority**（改了就是无依据的行为变更）；结论见 `known-divergences.md` §3 |
| T3 `REFUND_ELIGIBILITY` 被 `classify()` 降级成 `POLICY_QA` | 差出 **11 条**（10 + 1），但每条 `SupportPlan` 已等于 catalog 的 `REFUND_ELIGIBILITY` | 降级分支端到端**惰性**，维持现状、不用 YAML 表达；见 `known-divergences.md` §4 |
| T4 collaboration 先于写路径 | `support_agent.py:179-180 / :181-183 / :184-187` 三段顺序实读未变 | 顺序不动；`gap-handoff-10` 仍由第 ① 步给 `HUMAN_HANDOFF` |

读路径的 228 条既有分歧（12 组）逐组归档在 `known-divergences.md`，本清单不重复列。

---

## 步骤 4（切换判定来源）

### 4.1 门禁读数

```
cases=346 write_path_rows=('REFUND_REQUEST', 'TICKET_CREATE', 'DELIVERY_ISSUE')
过程层写意图差异=0
判定漂移(硬)=0  catalog 读数变化(留档)=14
读路径既有分歧(classify.intent != catalog.match)=228
```

`--diff` 的基线是 `.tmp/equiv_pre.json`（改造前），本次读数存 `.tmp/equiv_step4.json`。
14 条「catalog 读数变化」与步骤 1 完全同一批（同 id、同 `None → REFUND_REQUEST/TICKET_CREATE` 方向），
逐条仍是 **9 条 `TICKET_CREATE` + 5 条 `REFUND_REQUEST`**；`catalog_write` 与 `catalog_match` 是**软字段**，
切换之后 catalog 就是写意图判定来源，它当然要和改造前不一样——这不是行为变化（口径见本文件开头第 4 条）。

A1 对照 `.tmp/intent_before.json` ↔ `.tmp/intent_step4.json`：**346 条 `predicted` 逐条差异 0**；
两层质量字段逐项相等（`correct` 58/91、`wrong` 26/37、`false_unknown` 61/73、`false_unknown_write` 16/9、
`gold_unknown` 12/18、`unknown_predictions` 73/91、`unknown_matched` 12/18、`unknown_misread` 0/0、
`coverage` 0.4966/0.5473、`accuracy` 0.4/0.4527、`answerable_accuracy` 0.3459/0.3989）；
`model_gap` / `version` / `label_rule` / `dataset` 相同；仅 `rule_latency_ms` 随机器负载浮动
（`authored_gap` 5.558 → 5.935、`external` 45.421 → 10.35，改造前那次含首跑冷启动）。

`cue_overlap.with_gold_cue` 仍为 61 → **74**（与步骤 1 同一个原因：目录里多了 11 条 cue 字符串，
这个诊断量数的是「句子里天然含有 gold 意图的 cue」）。它不进任何门禁（`--enforce` 只查
`exact_cue_texts=0` 与「overlap ≤ 语料条数」，两项未变）。**如实登记，不以「更准」解释它。**

门禁（2026-10-27 本轮实跑，全部为当日输出）：

```
1029 passed, 6 skipped, 1 warning in 183.41s (0:03:03)   # python -m pytest -q -o addopts=
1035 tests collected in 0.75s                             # python -m pytest --collect-only -q -o addopts=
72                                                        # ls tests/*.py | wc -l（= test_py_files 事实）
24 passed in 4.29s                                        # 三条守卫
All checks passed!                                        # ruff check app tests scripts evals alembic
Success: no issues found in 148 source files              # mypy app
```

收集数与测试文件数未变 ⇒ 无事实基线级联（`test_collected` / `test_py_files` 两条事实不动）。

### 4.2 「失败可见」的手工验证（4.8，未提交）

向 `app/agent/intents.yaml` 临时追加一行 `NEW_WRITE_ROW`（`write_path: true`，不在白名单也不在豁免集）：

```
E   AssertionError: 未登记的写路径行 ['NEW_WRITE_ROW']：先扩 Intent 与 routing._WRITE_INTENT_IDS，
    或显式加入 routing._WRITE_INTENT_EXEMPT 并写明理由
1 failed in 0.22s
```

随后从 `.tmp/intents_backup_4_8.yaml` 恢复，`diff -q` 为空（输出 `IDENTICAL`）。这条证明 C5 的约束真的在起作用：
新增写行而不登记，测试会红并给出下一步动作，而不是静默跳过。

### 4.3 步骤 4 的改动面

* `app/agent/routing.py`：新增 `_WRITE_INTENT_IDS`（`:271`）、`_WRITE_INTENT_EXEMPT`（`:275`）、
  `_match_write_intent()`（`:278`，**函数内**延迟导入 `get_catalog`；类型走 `if TYPE_CHECKING`），
  `classify()` 的写分支由两条硬编码 `if-elif` 换成 `:333-336` 的「门槛 + catalog 行」。
  `_REFUND_ACTION`（`:45`）与 `_TICKET`（`:57`）**这一步还在**，只是不再被判定引用；删除属步骤 5。
* `app/agent/support_agent.py`：**零改动**（第 ② 步的 `not blocks_write_action` 原样保留，`routing.py` 的门槛也原样保留）。
* `app/agent/intents.yaml`：**零改动**（相对步骤 1）。
* `tests/test_intent_catalog.py`：`test_every_enum_label_has_a_catalog_row` 体内加子集断言（4.3）；
  `test_the_catalog_row_replaces_the_agent_branch` 体内加「假写行 + 加词」两段断言（4.7）。
* `tests/test_intent_rules.py`：`test_policy_questions_never_trigger_write_actions` 体内加三条门槛断言（4.6）。
* 未新增测试函数、未加 `parametrize` 参数项、未新建 `test_*.py`（C13）。

### 4.4 与 plan.md 的两处不一致（以代码为准，已报告不自行放行）

1. **plan §4.3「假意图的 `intent_id` 必须是 `Intent` 已有成员」无法与「新增一行」同时成立**。
   `_load` 对重复 `intent_id` 加载期报错（既有钉子 `test_a_duplicate_intent_row_is_rejected`），
   而 `Intent` 的六个成员在目录里**都已有行**（`test_every_enum_label_has_a_catalog_row` 逐条钉住）。
   落地形态拆成两段：新行 `FAKE_WRITE` 证 catalog 判定源、既有成员 `REFUND_REQUEST` 加 cue 证 `classify()` 判定源。
2. **plan §4.3 的「下沉到 support_agent 层」这条备选未采用**。循环导入确实存在（`intent_catalog.py` 模块级
   import `routing` 的三个判定函数），但 `classify()` 有 4 个生产调用点，加参数会把判定面扩大到本轮范围之外；
   函数内延迟导入既能跑，也不触发 `test_no_module_level_import_cycles`（该用例只读顶层 import 边）。
   未改 `docs/architecture-guard.yaml`（C8）。

