# 交付：阶段 B（审计编号 F-9）——写意图判定迁到意图目录

按用户裁定，本轮**不执行 `git commit`**：这里给出五个可分离提交的**文件清单、提交信息与本轮归因改动**，由人工按序落盘。
读顺序建议：先 §0（基线为什么不可用 `git diff HEAD` 直接作证），再 §1–§5，最后 §6（A1–A5）与 §7（回滚）、§8（如实登记）。

## 0. 基线说明（先读这一节，否则 diff 会被读错）

- 本轮开工时的 `HEAD` 是 `8dba4ea`（docs: 补齐守卫轮的三处同步缺口），它**早于** B-1 / B-3 / P8 / P11 等**尚未提交**的轮次：
  `git diff HEAD --stat` → **141 个文件、9058 插入 / 10718 删除**。
  证据（逐条实测）：`git show HEAD:app/agent/routing.py | grep -c refund_reason` → `0`；
  `git show HEAD:app/agent/intents.yaml | sed -n '31p'` → `version: "2026-10-24"`（现值 `2026-10-27`）。
- 所以 `git diff HEAD -- <path>` 得到的是**跨轮累计**，不是本轮 diff。本轮归因改用**标识符集合**作证：
  `_WRITE_INTENT_IDS` / `_WRITE_INTENT_EXEMPT` / `_match_write_intent` / `match_request_only` / `F-9` / `阶段 B` / `ADR-037`
  这些串只出现在下面清单里的文件；`app/agent/support_agent.py`、`app/services/**`、`evals/**` 均**不含**其中任何一个，
  即本轮未触碰它们（`support_agent.py` 的 mtime `2026-10-27 15:17` 属上一轮，本轮所有写入都在 `17:17` 之后）。
- 为便于人工核对，四条累计 diff 已导出到 `.tmp/`（该目录被 gitignore，**不入库**）：
  `.tmp/f9_c2_intents.yaml.patch`（92 行）、`.tmp/f9_c4_routing.patch`（175 行）、
  `.tmp/f9_c4_tests.patch`（217 行）、`.tmp/f9_c5_docs.patch`（4434 行，绝大部分是前几轮的文档工作）。
- 工作树自上一轮起就是脏的，因此本轮所有「未改动」类结论一律以**实读行号 / 实读内容**作证，不以 `git diff` 为空作证（`tasks.md` 3.3 已按同一纪律记录）。

---

## 1. 提交 1 —— 摸底（`tasks.md` 第 1 组，只读）

**文件清单**：无产品文件改动。产物是两份基线（`.tmp/`，不入库）：`.tmp/intent_before.json`（A1 对照物）、
`.tmp/equiv_pre.json`（端到端 346 条 `SupportPlan.intent` 序列），以及 `tasks.md` 第 1 组的勾选与读数。

**提交信息**：

```text
docs(F-9): 摸底写意图判定的现状并固定等价性基线

改了什么：只读复核 routing.py 的写意图 if-elif（:297-314，含 :298 的 blocks_write_action）
与 support_agent.classify_support() 的三步顺序（:158，①:179-180 ②:181-183 ③:184-187），
把两份基线冻结下来——346 条语料的 classify() 读数与 346 条 SupportPlan.intent 序列。
为什么：本轮是等价重构，没有改造前的逐条读数就没有「等价」的判据；行号也已逐条实读，
确认 plan.md 第 1 节没有漂移，不需要按符号重定位。
读数：plan.md 第 3 节 T1–T6 全部实测——T2 语料分歧 0 条、T3 差出 11 条但端到端已等于
catalog 读数、T6 目录写行数 = 3（DELIVERY_ISSUE 不在 Intent 枚举）。无行为变化，无文件写入。
```

## 2. 提交 2 —— 影子模式（`tasks.md` 第 2 组）

**文件清单**：`app/agent/intents.yaml`（两行写意图的 `cues` + `match_request_only`，此步无人读取＝零行为变化）、
`openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py`（新增，过程层比对脚本）、
`.../diff-list.md`（新增，14 行差异清单）、`tasks.md` 第 2 组勾选。

**本轮归因改动（YAML 两行，cue 逐字来自 `routing.py:41` / `:53` 的常量，未加词）**：

```yaml
  - intent_id: REFUND_REQUEST
    ...
    match_request_only: true          # 新增：问句不进写行（T1）
    cues:                             # 新增 6 条，逐字 = _REFUND_ACTION
      - "申请退款"
      - "我要退款"
      - "帮我退款"
      - "退钱"
      - "退款申请"
      - "refund"

  - intent_id: TICKET_CREATE
    ...
    match_request_only: true          # 新增
    cues:                             # 新增 5 条，逐字 = _TICKET
      - "工单"
      - "投诉"
      - "转人工"
      - "人工客服"
      - "ticket"
```

**提交信息**：

```text
feat(F-9): 影子模式——把两组写意图词表逐字搬进 intents.yaml，但不接判定

改了什么：REFUND_REQUEST 加 6 条 cues、TICKET_CREATE 加 5 条 cues（逐字复制 routing.py:41
的 _REFUND_ACTION 与 :53 的 _TICKET），两行各加 match_request_only: true；新增
compare_write_intents.py 在 346 条语料上比对 classify() 与 catalog.match() 的过程层读数。
为什么：清单没出来之前不许动判定（dispatch C10）。这一步刻意「加了没人读」，
先把差异量化——改造前classify 侧与 catalog 侧差 14 条（软字段 catalog_write/catalog_match），
全部逐条登记在 diff-list.md §1.2 的「原因 / 处理」两列。
读数：compare_write_intents.py → cases=346 过程层写意图差异=0（改造前 14 条，此步归零）；
全量 1029 passed, 6 skipped / 收集 1035 / test_py_files 72 / 三条守卫 24 passed /
ruff All checks passed! / mypy Success: no issues found in 148 source files；
端到端判定漂移(硬)=0、A1 逐条差异 0。此步无任何生产判定变化（YAML 新字段无人读）。
```

## 3. 提交 3 —— 消除过程层差异（`tasks.md` 第 3 组）

**文件清单**：`openspec/changes/add-intent-routing-data-driven-2026-10/known-divergences.md`（新增，12 组 / 228 条读路径既有分歧）、
`.../diff-list.md`（补「原因 → 处理」结论）、`tasks.md` 第 3 组勾选。**无产品文件改动**。

**提交信息**：

```text
docs(F-9): 过程层差异归零的结论与读路径既有分歧的档案（不改判定）

改了什么：T2（POLICY_QA ↔ LOGISTICS_QUERY 顺序）实测 346 条语料上分歧 0 条，因此
不改任何 priority——按语料决定，不凭直觉；T3（_REFUND_ELIGIBILITY 命中却赋 POLICY_QA 的
降级）维持现状：11 条分歧的 SupportPlan 已等于 catalog 的 REFUND_ELIGIBILITY，降级分支
端到端惰性，不用 YAML 表达它也不新增读行；T4 复核 classify_support() 第 ① 步顺序未动。
读路径的 12 组 / 228 条分歧逐条转入 known-divergences.md，注明为什么 SupportPlan 不受影响
（读意图取的是第 ③ 步的 catalog 行），并登记两处残留影响面：model_gate 的 gap_readable
与候选集的 rule: 证据。
为什么：dispatch C2 要求端到端与过程两层门禁；把不影响 SupportPlan 的分歧留档而不是「顺手修好」，
才能保住等价性基线。
读数：过程层写意图差异=0 / 判定漂移(硬)=0 / A1 逐条差异 0（.tmp/equiv_pre.json ↔
.tmp/equiv_step1.json、.tmp/intent_before.json ↔ .tmp/intent_step1.json）。
```

## 4. 提交 4 —— 切换判定来源（`tasks.md` 第 4 组，行为等价的关键提交）

**文件清单**：`app/agent/routing.py`（写意图判定改走 catalog + 白名单/豁免 + 延迟导入；
`_REFUND_ACTION` / `_TICKET` 两个常量与其 `if-elif` 分支在本提交内已不被判定引用，常量本体留到提交 5 删）、
`tests/test_intent_catalog.py`、`tests/test_intent_rules.py`、`tests/test_intent_candidates.py`
（**全部并入既有用例体内**，未新增测试函数 / 参数项 / 测试文件）、`tasks.md` 第 4 组勾选。

**本轮归因改动（`routing.py` 的写分支，改前 → 改后）**：

```python
# 改前（HEAD 形状，本轮开工时仍在）
blocked = blocks_write_action(text)
if any(word in text for word in _REFUND_ACTION) and not blocked:
    intent = Intent.REFUND_REQUEST
elif any(word in text for word in _TICKET) and not blocked:
    intent = Intent.TICKET_CREATE
elif any(word in text for word in _REFUND_ELIGIBILITY):
    ...

# 改后（当前 app/agent/routing.py:331-334）
blocked = blocks_write_action(text)
write_spec = None if blocked else _match_write_intent(text)
if write_spec is not None:
    intent = Intent(write_spec.intent_id)
elif any(word in text for word in _REFUND_ELIGIBILITY):
    ...
```

新增的三个判定面（当前行号，步骤 5 复算）：`_WRITE_INTENT_IDS` `:269`、`_WRITE_INTENT_EXEMPT` `:273`、
`_match_write_intent()` `:276`（首行**函数内延迟导入** `get_catalog`，类型注解走 `if TYPE_CHECKING` `:13-14`）；
`DELIVERY_ISSUE` 在循环里**显式点名跳过**（`:289-290`），不是裸 `continue`（C5）。

**提交信息**：

```text
feat(F-9): 写意图判定改由 intents.yaml 的目录 cues 判，判定结果一条都没改

改了什么：classify() 的写意图分支不再读 Python 词表，改为遍历目录里 write_path: true 的
行（_match_write_intent()）；加 _WRITE_INTENT_IDS 白名单与 _WRITE_INTENT_EXEMPT 豁免集，
并把「write_path 行 ⊆ 白名单 ∪ 豁免集」写成断言（并入既有用例
test_intent_catalog.py::test_every_enum_label_has_a_catalog_row）。问句门槛两处都在：
routing.py:331-332 的 None if blocked else …，以及 support_agent.py:181-183 的
and not (blocks_write_action(text))——后者一字未动。
为什么：判定器要唯一（目录是意图事实源，ADR-009 的承诺此前只对读意图成立）。循环导入
用函数内延迟导入解决，因为 intent_catalog 模块级 import 本模块的 blocks_write_action；
未采用「下沉到 support_agent 再传值进 classify()」——classify() 有 4 个生产调用点，
加参数会把判定面被动扩到本轮范围之外。architecture-guard.yaml 未改，依赖方向不变。
读数：compare_write_intents.py → 过程层写意图差异=0（classify_write 与 catalog_write 346 条
逐条相同）；端到端判定漂移(硬)=0、A1 逐条差异 0；catalog 软字段读数变化 14 条（与提交 2
同一批，留档在 diff-list.md）；全量 1029 passed, 6 skipped, 1 warning in 183.41s、收集
1035 tests collected、tests/*.py = 72、三条守卫 24 passed、ruff All checks passed!、
mypy Success: no issues found in 148 source files。反向验证（手工、不留用例）：临时加一行
NEW_WRITE_ROW（write_path: true、不在两集合）→ test_every_enum_label_has_a_catalog_row
1 failed，消息点名「未登记的写路径行 ['NEW_WRITE_ROW']」，随后从
.tmp/intents_backup_4_8.yaml 恢复并 diff -q 确认 IDENTICAL。
```

## 5. 提交 5 —— 收尾（`tasks.md` 第 5 + 6 组）

**文件清单**：
- `app/agent/routing.py`：删除 `_REFUND_ACTION` / `_TICKET` 两个常量（删除前全仓 `grep` 零命中；`docs/code-anchors.yaml` 无锚点，退出码 `1`）
- `app/agent/intents.yaml`：头部第 8–9 行改为「加**读**能力＝加一行 + 一个 action 实现；写行见文末脚注」，文末追加脚注（点名 `_WRITE_INTENT_IDS` / `_WRITE_INTENT_EXEMPT` / `DELIVERY_ISSUE` 豁免 / 会红的用例名）——**净零行**（保住 `:31`/`:49-55`/`:51`/`:80`/`:84`/`:93`/`:96` 等外部引用位置）
- `docs/adr/ADR-037-写意图判定迁到意图目录.md`（**新增**）
- `docs/项目现状.md`、`docs/下一步规划.md`、`docs/优化规划.md`（仅状态词与根因句）
- ADR 篇数 35 → 36：`README.md:13`、`README.md:445`、`CONTRIBUTING.md:171`（含范围改到 `ADR-037`）、`docs/下一步规划.md:28`、`docs/spec/项目规格.md:3703`、`docs/guide/证据索引.md:673`、`docs/records/智能体平台设想.md:178`、`docs/项目现状.md:25`
- `docs/records/智能体平台设想.md`：§9.4 ADR 索引新增 ADR-037 行（`:1078`）、编号说明补一句、文件计数 `共 35 个文件` → `共 36 个文件`（`:1098`）
- `docs/spec/TicketPilot项目说明书.docx` + `docs/spec/manual-build.json`（`scripts/build_manual.py` 重生成）
- `tasks.md` 第 5、6 组勾选与读数

**提交信息**：

```text
docs(F-9): 删掉两组死词表，写意图的 YAML 承诺改成真话，落 ADR-037 并同步篇数

改了什么：① 删除 _REFUND_ACTION / _TICKET 常量（判定已在提交 4 换源，此处只清尸体）；
② intents.yaml 头部那句「加一行即可，不改路由代码」按现实收窄为「加读能力＝一行 + 一个
action 实现，写行见文末脚注」，脚注写明唯一被校验的写路径规则（白名单 / 豁免集 / 会红的用例名）；
③ 新增 ADR-037 记录决策、6 条被否决方案（含「全面委托 catalog」）与 DELIVERY_ISSUE 豁免；
④ 三份状态文档标收口（项目现状 D1 已收口 / D2 部分收口、下一步规划 F-9 已收口、优化规划
阶段 B 已完成）；⑤ ADR 篇数 35 → 36 逐处同步并重生成说明书。
为什么：头注释原来是假的，留着会让「加一行即生效」第二次失效；YAML 说明拆头/尾是为净零行——
把整段放头部会把 version 从 :31 推走，连带打漂本轮不可写文件（.qoder/rules/refund-reason-
taxonomy.md、docs/spec/项目规格.md:4150、docs/guide/证据索引.md:108）的引用。CONTRIBUTING.md
:171-174 本来就要求 §9.4 索引、编号说明与文件计数同步，那三处不是越界。
读数：删除前 grep 全仓零命中；删除后 pytest tests/test_intent_catalog.py
tests/test_intent_rules.py → 55 passed in 0.61s；build_manual.py → bytes=291807
paragraphs=4131 tables=91 fffd=0 parts=13 + MANUAL OK；grep -rn "35 篇\|35 个文件"
README.md CONTRIBUTING.md docs/ .github/ → 零命中；A1 复跑 346 条 drift=0、
invariants/model_gap 相同；全量 1029 passed, 6 skipped in 191.43s、收集 1035、
三条守卫 24 passed in 4.10s、ruff All checks passed!、mypy 148 files 零错误。
```

---

## 6. A1–A5 逐条结论

| 编号 | 判据 | 结论 | 本轮读数 |
|---|---|---|---|
| **A1** | 346 条语料判定逐条一致（硬门禁） | **通过** | `.tmp/intent_before.json` ↔ `.tmp/intent_step5.json` → `cases= 346 drift= 0`；复跑 `.tmp/intent_step5b.json` → `rerun drift= 0`；`invariants` / `model_gap` / `version` / `label_rule` / `dataset` 逐字段相同；端到端第二层 `.tmp/equiv_pre.json` ↔ 步骤 4 → `判定漂移(硬)=0` |
| **A2** | 全量用例全绿、收集数不减少、0 failed | **通过** | `1029 passed, 6 skipped, 1 warning in 191.43s`；`pytest --collect-only` → `1035 tests collected`；`ls tests/*.py | wc -l` = `72`（C13 的两个数都未漂移） |
| **A3** | 「加一行即生效」为真 | **通过（两段证明）** | ① `intent_catalog._load(tmp_path/…)` 注入 `FAKE_WRITE`（`write_path: true` + cue）→ `patched.match("假写的动作词")` 返回 `FAKE_WRITE`，生产目录对同一句返回 `None`；② 临时 YAML 给 `REFUND_REQUEST` 加一条 cue「那笔钱给我退回来」+ `monkeypatch` 换 `intent_catalog.get_catalog` → `classify()` 立即判 `REFUND_REQUEST`，`undo()` 后回到 `UNKNOWN`。与 plan §4.3 的一处不一致已按代码现实落地并报告：`_load` 拒绝重复 `intent_id`，而 `Intent` 六个成员在目录里都有行，故「新增一行且用既有成员」在数学上不可能 |
| **A4** | 写动作门槛未丢 | **通过** | 两处调用点都在：`routing.py:331-332`（`None if blocked else …`）与 `support_agent.py:181-183`（`and not (blocks_write_action(text))`），另有 `intent_catalog.py:223` 的 `match_request_only`；「什么时候可以转人工」实测 `classify → POLICY_QA`、`classify_support → POLICY_QA`，而 `_match_write_intent(...)` **非 None**（cue 确实命中 `TICKET_CREATE`）——是门槛在挡，不是词表没命中。既有 `blocks_write_action` 用例全过（`55 passed`） |
| **A5** | 文档守卫不红 + 说明书重生成 | **通过** | 三条守卫 `24 passed in 4.10s`；`ruff check app tests scripts evals alembic` → `All checks passed!`；`mypy app` → `Success: no issues found in 148 source files`；`scripts/build_manual.py` → `MANUAL OK`（因动了被 `in_manual: true` 绑定的 `docs/spec/项目规格.md:3703`） |

## 7. 回滚方法（按步骤粒度）

本轮**不涉及数据库、迁移、配置项、环境变量**，回滚只需 revert 提交，无需数据操作。

| 想退到哪 | 操作 | 退完之后是什么状态 | 副作用 |
|---|---|---|---|
| 判定源仍是 Python 词表 | revert 提交 4 | `classify()` 回到两组 `if-elif`；提交 2 加的 `cues` / `match_request_only` **仍在 YAML 里但无人读**（影子状态） | 无。`cues` 字段自提交 2 起就不改变判定，退 4 不会打红目录校验（`write_path` / `match_request_only` 是 `_ROW_FIELDS` 既有合法键） |
| 完全回到改造前 | 依次 revert 提交 4 → 3 → 2 | YAML 两行回到无 `cues`；`_REFUND_ACTION` / `_TICKET` 随提交 4 的 revert 回来（提交 5 的删除也一并回来，注意 revert 顺序：先 5 后 4 才能把常量恢复） | 需注意 revert 顺序：**先 revert 提交 5（文档/删常量/YAML 注释），再 revert 提交 4**，否则提交 4 的 revert 会把常量放回一个已经不引用它的文件里，形成「回来了但没人读」的中间态 |
| 只退文档 | revert 提交 5 的文档部分 | 代码保持切换后的形状，`docs/` 与 ADR 回到改造前叙述 | **不要单独这么做**：ADR 篇数（`adr_count` 事实绑 `CONTRIBUTING.md:171` 与 `docs/records/智能体平台设想.md:178`）与 `docs/adr/` 实际文件数会被打红，且说明书与 `docs/spec/项目规格.md:3703` 脱节 |
| 全部 | revert 整个 change（5 个提交） | 与开工前逐字一致 | 无数据迁移、无表结构变化；`RULESET_FINGERPRINT` 随 `routing.py` 字节回滚而变，模型决策缓存按既有设计作废一次（无需用例硬编码该值，测试导入常量） |

## 8. 如实登记（读了才不算越界，本轮**未**自行处置）

1. **`cue_overlap.with_gold_cue` 61 → 74**（`by_intent` 新增 `REFUND_REQUEST: 3`、`TICKET_CREATE: 10`，`rate 0.3035 → 0.3682`）。
   这是 A1 输出里的**元数据字段**，不是任何质量门槛，也不参与判定；变化原因是写意图的 cue 表第一次进了目录，
   评测脚本按目录 cue 统计「金标句子里有 cue」的比例，分母口径没变、分子自然变大。逐条 `predicted` 漂移仍为 0。
2. **`layers.*.rule_latency_ms` 变化未归因**：`external` 层基线 `45.421` → 步骤 4 `10.35` → 步骤 5 两次 `9.586 / 9.823`。
   本轮之后没有再回到 45 ms，因此既不能归因于冷启动，也不能归因于本轮改动。**不下加速结论**，只登记为未归因的时点计时读数。
3. **`RULESET_FINGERPRINT` 的覆盖面比它的说法窄一格**：`routing.py:18-35` 按源码字节做摘要，
   哈希的是 `routing.py` + `app/sql/planner.py` + `app/core/money.py`，**不含 `intents.yaml`**。
   本轮把写意图判定移进 YAML 之后，「改一行 cues 会改变模型读数、但不会作废任何在途决策记录」成了事实。
   本轮**未实现**修复（属新增机制，超出等价重构范围），已写进 ADR-037 第 9 节登记为缺口。
4. **`app/agent/candidates.py:110` 的 `_evidence()` 不读 `match_request_only`**：问句只要含 cue 就会被记
   `cue:` 证据。离线 A/B（HEAD 目录 vs 当前目录，同 346 条）实测 **consult=99 条、候选集变化=0**，
   因为本轮进入目录的 cue 与原 Python 词表逐字相同、而问句门槛在闸门处已挡住 consult。
   **同构是本轮条件的产物，不是机制保证**——将来给写行加新 cue 就会漂。已写进 ADR-037 第 9 节。
5. **本轮不可写文件里的陈旧引用**（按 dispatch §3 未改，只登记）：
   - `docs/guide/证据索引.md:31` 写 `app/agent/routing.py:208` 的 `classify()` 与 `support_agent.py:210` 的调用点——
     现值分别是 `routing.py:298` 与多处调用点（`:679`/`:1024` 等）。锚点守卫只登记符号名不登记行号，所以**不会打红**。
   - `.qoder/rules/refund-reason-taxonomy.md:469` 仍写 `intents.yaml` 的 `version: "2026-10-26"`，现值 `2026-10-27`。
   - `docs/guide/证据索引.md:108` → `intents.yaml:67` 的 `真人` cue 引用，自 B-1 的槽位块插入后已漂。
6. **`docs/下一步规划.md` §10.1 总表没有 F-9 行、§10.2 也没有对应小节**，而 `docs/优化规划.md:92` 曾承诺
   「三条已按 §0.2 格式登记」。F-9 实际只登记在 §0.2 总表里。§10.1 / §10.2 不在 dispatch §3 的可写清单内，
   本轮**未补**，作为缺口留给下一轮（与本文件 §5 的登记句同一条）。
7. **未跑的门禁（如实列出，不冒充全绿）**：
   - `evals/run.py`、`evals/support_run.py`、`retrieval_metrics.py`、`replay.py --self-check`、`alembic check`、`npm run build`
     ——本轮按用户裁定「评测指标由用户自己优化」不跑模型侧门禁；`agents.md` 只在涉及检索/评测时要求前两项，本轮未触碰检索。
   - `scripts/llm_gap_probe.py` 的 11 句探针（`优化规划.md` 阶段 B 验收项之一）**本轮未跑**：需要真实模型调用。
   - 本轮跑了的只有 `evals/intent_metrics.py`（A1 等价性回归，`AGENT_MODE` 走规则，不碰模型）。
8. **变更目录里有 `__pycache__`**（`openspec/changes/add-intent-routing-data-driven-2026-10/__pycache__/`）：
   落盘提交 2 / 3 时**不要 `git add` 整个目录**，只加 `compare_write_intents.py`、`diff-list.md`、`known-divergences.md`、`tasks.md` 与本文件。
9. **ADR 篇数实际同步面大于 C12 列的六处**：本轮共改 **8 处**数字（多出 `README.md:445`、`docs/项目现状.md:25`）
   外加 §9.4 索引行与编号说明两句。多出的部分由 `CONTRIBUTING.md:171-174` 的既有约定授权
   （那段明写「§9.4 ADR 索引、编号说明与文件计数要同步改」），不是自行扩面。

---

## §9 复审返工（第 2 轮）

本轮**只交 diff，不产生任何提交**（用户裁决：`git commit` / `git add` / `git stash` /
`git checkout` / `git reset` 一律不执行）。改的只有注释、文档与既有用例体内的断言，
**不改任何判定行为**：`classify()` / `classify_support()` 的输出逐条不变，收集数保持 **1035**。

| 文件 | 位置（返工后行号） | 性质 |
|---|---|---|
| `app/agent/routing.py` | `:19-28`（`ruleset_fingerprint()` docstring）、`:273-281`（`_WRITE_INTENT_EXEMPT` 注释块 + 常量行） | 注释 |
| `docs/adr/ADR-037-写意图判定迁到意图目录.md` | 新增 `### 10`（`:180-194`） | 文档 |
| `tests/test_intent_rules.py` | `:62-66`（既有用例 `test_explicit_requests_still_create_write_actions` 体内） | 断言（不加参数行、不加用例） |
| 本文件 | 本小节（§9） | 记录 |

### 9.1 reviewer 5 条的逐条处置

| # | 级别 | 退回项 | 处置 | 证据 |
|---|---|---|---|---|
| 1 | 严重 | `ruleset_fingerprint()` 的 docstring 写「A digest of the files that decide what the rules read」，但摘要只哈希 `routing.py` + `app/sql/planner.py` + `app/core/money.py`，而**写意图的 cue 判定数据本轮已迁到 `app/agent/intents.yaml`**——那句话与实现不符 | 只改 docstring，**不动那个路径元组**：把 `intents.yaml` 加进摘要会改模型决策缓存的失效语义，超出「等价重构」范围，ADR-037 第 9 节已裁定登记为缺口交后续轮次。新 docstring 明写摘要只覆盖这三个代码文件、**不覆盖** `intents.yaml`、并指向 ADR-037 §9 | `app/agent/routing.py:19-28`（函数名 `:19`、函数体 `:29-42`、模块级常量 `RULESET_FINGERPRINT` `:43` 均未动；该常量返工前在 `:40`，docstring +3 行后随之下移） |
| 2 | 一般 | `tests/test_architecture_guard.py` 用例级 docstring 的「反向与同层耦合必须登记在 `exceptions`」与该用例体不一致：循环里 `if source == target: continue` 把**同名层**的边整段跳过，`kind` 只在**同 tier 跨层**时才取「同层跨层耦合」 | **不改那个测试文件**（按裁决）：在 ADR-037 新增 `### 10` 记决定——规则 2 的「同层耦合」读作「同 tier 跨层耦合」，同名层内的边不参与判定、也不进 `exceptions`；docstring 的措辞订正归架构守卫自身轮次（ADR-025），本轮不顺手改 | `docs/adr/ADR-037-写意图判定迁到意图目录.md:180-194` |
| 3 | 一般 | `_WRITE_INTENT_EXEMPT` 的注释没说它同时是**登记面**：`tests/test_intent_catalog.py` 的子集断言把它与 `_WRITE_INTENT_IDS` 并起来比，所以「往这里加一个 id」本身就能让断言变绿 | 在既有三行**之后**追加四行英文注释（原三行一字不改），点明这层「加 id 即变绿」的副作用，因此每条必须写明理由，并复述 `DELIVERY_ISSUE` 的理由就是「没有 `Intent` 成员」 | `app/agent/routing.py:273-280`（注释块），常量行 `:281` 未动 |
| 4 | 一般 | 「退款优先于工单」现在只由 `intents.yaml` 两个整数承载（`REFUND_REQUEST` 10 < `TICKET_CREATE` 11），**没有任何用例钉**；调成 11/11 或 12/11 会让「退款 + 投诉」同句静默变成工单 | 在既有用例 `tests/test_intent_rules.py::test_explicit_requests_still_create_write_actions` 的**函数体内**加 2 条断言（该用例带 `@pytest.mark.parametrize`，只加体内断言、不加参数行、不新增用例）：`classify("我要申请退款，顺便投诉一下").intent` 与 `_support(同句)` 都必须是 `REFUND_REQUEST` | `tests/test_intent_rules.py:62-66`；收集数仍为 1035（见 9.2 第 1 条） |
| 5 | 一般 | 复审者声明**未核完**文档同步面：`docs/spec/项目规格.md`、`docs/records/智能体平台设想.md`（守卫绑定的「规模事实」行）、`docs/guide/证据索引.md` 三处各命中一次「36 篇」，但它没读上下文，因此「`docs/spec/项目规格.md` 本轮仅授权 ADR 篇数同步」这一条它不给通过证据 | **由编排方补证据，不改代码**：三行原文已逐行读出（见 9.5），每行里与 F-9 相关的只有 ADR 篇数一项，其余读数（148 文件 / 72 测试文件 / 收集 1035 / 26 表 / 48 路径 / 51 操作）与本轮之前同值，未夹带口径改动 | 见 9.5 三行原文摘录 |

### 9.2 本轮实跑读数

> 时点读数：2026-10-27，同一台机器，**脏工作树**。`git status` 无法区分本轮改动与更早轮次累积的
> 改动（`openspec/agents.md` §8），因此下面这些读数**只是本轮自述**，不冒充「唯一由本轮产生」。

1. `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest --collect-only -q -o addopts=` →
   `1035 tests collected in 0.75s`（收集数与返工前同数）。
2. `.venv/Scripts/python.exe -m pytest tests/test_intent_rules.py tests/test_intent_catalog.py -q -o addopts=`
   → `55 passed in 0.57s`，退出码 0。
3. `.venv/Scripts/python.exe -m pytest tests/test_docs_consistency.py tests/test_doc_code_anchors.py tests/test_architecture_guard.py`
   → `24 passed in 4.55s`，退出码 0。
4. `.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic` →
   `warning: The following rules have been removed and ignoring them has no effect: - UP038` +
   `All checks passed!`，退出码 0（`UP038` 是既有配置告警，不是错误）。
5. `.venv/Scripts/python.exe -m mypy app` → `Success: no issues found in 148 source files`，退出码 0。
6. 等价性回归：
   `.venv/Scripts/python.exe openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py --out .tmp/equiv_rework.json --diff .tmp/equiv_pre.json`
   → 头部两行 `过程层写意图差异=0` / `判定漂移(硬)=0  catalog 读数变化(留档)=14`，退出码 0。
   `14` 那条是软字段（`catalog_write` / `catalog_match`）的施工面留档，与 §3 及 ADR-037 §3 登记的是同一批。
7. `find . -maxdepth 3 -iname "*review*" -not -path "./.git/*" -not -path "./node_modules/*"` →
   24 条命中全部在 `.tmp/` 与 `.pytest_tmp/` 下，均为历史轮次的评测/用例产物；没有本变更的退回通知文件。

### 9.3 未跑项（如实列出）

- **全量 `pytest`**（`1029 passed / 6 skipped` 那条）本轮**未跑**：按用户裁定只跑最小范围（上面第 2、3 条）。
- `evals/**`（`run.py` / `support_run.py` / `retrieval_metrics.py` / `replay.py --self-check` /
  `intent_metrics.py`）本轮**未跑**；`scripts/llm_gap_probe.py` 的 11 句探针（需真实模型调用）
  **未跑**。等价性改由第 6 条那条离线端到端比对承担。
- `alembic check`、`npm run build`：本轮不涉及迁移与前端，未跑。

### 9.4 与分派简报的三处行号差异（读到的不符，按实际落地）

1. `_WRITE_INTENT_EXEMPT` 的注释块返工前实际在 `:270-272`（常量行 `:273`），简报写的是 `:271-273`。
2. 第 1 条改动让该 docstring 正文从 5 行变 7 行（含收尾引号共 6 行 → 9 行），故
   `:19` 之后的行号 **+3**；第 3 条又在豁免集注释块里加了 5 行，于是该常量之后的行号
   **再 +5**（合计 +8）。实测：`RULESET_FINGERPRINT` `:40 → :43`、
   `_WRITE_INTENT_IDS` `:269 → :272`、`_WRITE_INTENT_EXEMPT` `:273 → :281`、
   `_match_write_intent` `:276 → :284`、`classify()` `:298 → :306`、
   写路径门槛 `:331-332 → :339-340`；文件总行数 360 → 368。
3. `tests/test_architecture_guard.py` 的三处引用逐行核对无误：用例 docstring `:239`、
   同层跳过分行 `:245-246`、`kind` 判定行 `:255`、模块级 docstring `:11-12`（文件共 396 行）。
### 9.5 第 5 条的三行原文摘录（编排方实地读，非复审者自述）

| 位置 | 原文（截断到与 F-9 相关的部分） | 判定 |
|---|---|---|
| `docs/spec/项目规格.md:3703` | 「**本轮收尾读数**：… mypy strict **148** 个文件零错误；`tests/*.py` **72** 个文件、收集 **1035** 条；**26** 张表；**15** 条迁移（head `d9e4c7a15b82`）；**36 篇 ADR**（ADR-001…ADR-011、ADR-013…ADR-037；ADR-037 随 F-9 阶段 B 于同日落地）」 | 与 F-9 相关的只有 ADR 篇数与 ADR-037 的落地说明；148 / 72 / 1035 / 26 / 15 均为本轮之前同值，未改 |
| `docs/records/智能体平台设想.md:178` | 「**规模事实**：Python 代码 `app/` 148 个文件；72 个测试文件（收集 1035 条…）；26 张表；48 个 API 路径 / 51 个操作…；**36 篇 ADR**（缺 ADR-012）；10 个控制台页面；…」 | 同上；这是 `adr_count` 事实绑定的两处之一（`docs/fact-baseline.yaml:177-185`），守卫已绿 |
| `docs/guide/证据索引.md:673` | 「…pytest **1029 passed / 6 skipped / 0 failed**（收集 **1035**、72 个测试文件…）、**36 篇 ADR**（ADR-001…ADR-011、ADR-013…ADR-037）、mypy strict **148** 个文件零错误、ruff 零告警」 | 同上；只有篇数与编号区间被改 |

三行的共同形状：**改的是 `36 篇` 这个数与该数附带的编号区间说明，同一行里的其它读数一律未动**。
没有出现第二处与 F-9 相关的改动，也没有新增段落。
### 9.6 第 2 轮复审结论（独立 reviewer）

结论：**通过**。5 条逐条判「闭合」，越界检查无问题（本轮返工只落在
`app/agent/routing.py` 的注释 / `tests/test_intent_rules.py` 的既有用例体 /
`ADR-037` 的散文 / 本文件），三份守卫清单里查不到任何 F-9 标识符（`grep -c` 均为 0），
`git log --oneline -3` 头部仍是 `8dba4ea`——**本轮没有 `git commit`**。

复审另登记一条非阻断项（照抄结论）：本文件 9.5 的「未改」列在三份文档均未被 git 跟踪
（`??`）的前提下无历史可比，属自述；后续轮次若改这三份文件，须按 `openspec/agents.md` §8.5
补时点标注。

复审还指出 9.1 第 4 条里一句推演不准：两行 priority 都调成 11 时按 `intent_id` 定序仍得
`REFUND_REQUEST`，不会红。已就地订正 `tests/test_intent_rules.py:62-65` 的那句注释
（改为「工单行调到 <=10 即红；退款行调到 12（工单仍 11）同样红；两行都 11 时按 `intent_id`
定序，仍得退款」），并复跑 `ruff check tests/test_intent_rules.py` → `All checks passed!`、
`pytest --collect-only` → `1035`、`pytest tests/test_intent_rules.py` 全通过。
