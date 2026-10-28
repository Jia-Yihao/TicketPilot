# B-1 退款原因与退款类型 ＋ B-3 审批金额分层 — 实施计划

**目标**：给退款补上「退的是什么、为什么退」（原因 / 类型），并让「按金额分层审批」从
死旋钮变成活规则——阈值真正生效，档位进摘要，自动执行与审批通过两条路径都可区分、可审计。

**口径**：
- B-1：`.qoder/rules/refund-reason-taxonomy.md`（已定死，不得改名）
- B-3：`.qoder/rules/approval-tiering.md`（本轮新增）

本文件只规定**怎么落地**；行为契约在 `specs/`，取舍在 `proposal.md`。

**架构**：

```text
采集侧  app/agent/refund_reason_rules.yaml ──loader──▶ app/agent/routing.py::classify
                                                     ├─▶ RoutePlan.refund_reason / .refund_type
                                                     └─▶ app/agent/turns.py::Turn（跨轮载体）
对话侧  app/agent/intents.yaml: slots.refund_reason + REFUND_REQUEST.required_slots
                                                     └─▶ app/agent/support_agent.py（context 组装 / present 集合）

写路径  app/services/runtime.py::_handle_refund
          ├─ 缺值闸门（仅 refund_reason）──▶ self._clarify(...)
          ├─ refund_type 缺省派生（锚：orders.refundable_cents）──▶ FULL / PARTIAL
          ├─ 资格校验 ──▶ eligibility["requires_approval"]
          │     ├─ 真 ─▶ 建审批行（档位快照进载荷与摘要）──▶ WAITING_APPROVAL ─▶ 审批 ─▶ 执行
          │     └─ 假 ─▶ 自动执行（同一台账 / 同一执行器 / 同一资格复核）──▶ 终态
          └─ app/services/refunds.py::RefundExecutionService.execute（两条路径共用）
```

**技术栈**：Python 3.13、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、pytest、ruff、mypy(strict)。

**规格路径**：
- `openspec/changes/add-refund-reason-and-approval-tiering-2026-10/specs/refund-request/spec.md`
- `openspec/changes/add-refund-reason-and-approval-tiering-2026-10/specs/approval-tiers/spec.md`

**全局约束**：

1. **顺序**：先迁移后逻辑，B-1 先于 B-3（理由见 `proposal.md`「依赖与顺序」）。两者都要改同一个
   退款载荷构造函数，**不得交错修改**，否则无法把摘要形状的变化分段归因。
2. **TDD**：每个任务先写会失败的测试并**亲眼看过失败**（红 → 绿 → 重构）。失败必须失败在
   「行为未实现」，不许失败在拼写或导入路径。
3. **命名纪律**：退款原因在链路各处一律 `refund_reason`；只在构造退款载荷与落库两处映射为裸名
   `reason`。**不得**新建承载退款原因值域的裸名 `reason`。档位键固定 `approval_tier`。
4. **模型不碰**：`app/agent/pydantic_agent.py::IntentOutput` 不新增退款原因 / 类型字段；两处采纳位
   不为它们开补位；提示词不新增抽取要求。
5. **工具闸门不放宽**：`request_refund` 的 `requires_approval` 恒为 `True`；
   `app/tools/gateway.py` 的审批校验一行不改。自动执行走服务层复用，不走工具入口。
6. **默认行为不变**：默认阈值 `0` + 默认档位表下，每笔退款仍建审批行、仍进 `WAITING_APPROVAL`，
   既有 Golden 与退款用例读数逐条不变。（「逐条不变」指**读数**不变：语料侧需按任务 11 的
   「语料口径」补齐输入，**不改**请求文本与既有断言。）
7. **不静默降级**：规则表 / 档位表的加载期校验失败一律**报错**，不得跳过该行、不得兜底成默认值。
8. **命令**：`./.venv/Scripts/python.exe`；`pytest` 加 `-o addopts=`（`pyproject.toml:46` 的
   `addopts = "-q"`）；Windows 控制台读数重定向到 `.tmp/*.txt` 并设 `PYTHONIOENCODING=utf-8`。
9. **不碰**：`docs/**` 的文档同步集中在任务 12；任务 1–11 期间不提前改文档。
10. **迁移**：一条新迁移，`down_revision = 'a1c7e9d4b820'`（当前 head），只加**可空**列、
    **不回填**历史数据，SQLite 与 PostgreSQL 双向可升降。

---

## 逐任务计划

以下 12 个任务编号**全局连续**，与 `tasks.md` 每项末尾的 `（计划任务 N）` 一一对应。

### 任务 1 — 退款原因与退款类型枚举（先红）

**文件**：`app/models/enums.py`（新增两个 `StrEnum`）；测试 `tests/test_refund_taxonomy.py`（新建）。

**测试断言**（先失败于「枚举不存在」）：

1. `RefundReason` 的成员恰为 `QUALITY` / `DAMAGED` / `WRONG_ITEM` / `NOT_AS_DESCRIBED` /
   `NO_LONGER_NEEDED` / `OTHER`，且每个成员的值等于成员名；
2. `RefundType` 的成员恰为 `FULL` / `PARTIAL` / `SHIPPING` / `PRICE_PROTECTION`，值等于成员名；
3. 两者是 `StrEnum`、与既有 `RefundStatus`（`app/models/enums.py:81-85`）同风格；
4. 枚举成员可被字符串直接命中（`RefundReason("DAMAGED")` 成立，非法值抛错）。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_refund_taxonomy.py`。

### 任务 2 — 退款台账三列与一条迁移（先红）

**文件**：`app/models/commerce.py`（`Refund` 新增 `reason` / `refund_type` / `reason_note` 三列，
均 `nullable=True`；枚举列用 `SAEnum(..., native_enum=False, length=32, validate_strings=True)`
与 `RefundStatus` 同构）；`alembic/versions/<新 revision>_refund_reason_columns.py`（新建，
`down_revision = 'a1c7e9d4b820'`）；测试 `tests/test_refund_taxonomy.py`（追加）。

**测试断言**：

1. 迁移 `upgrade` 后 `refunds` 表多出三列且**均可空**；
2. `downgrade` 后三列消失（往返可执行）；
3. 迁移**不回填**：对既有行执行 `upgrade` 后三列仍为 `NULL`；
4. 模型层三列的枚举校验生效（写入非法值被拒）。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_refund_taxonomy.py`；
`./.venv/Scripts/python.exe -m alembic check`（PG 上的往返由 CI 跑，本地跑 SQLite）。

### 任务 3 — 原因关键词规则表与 loader（先红）

**文件**：`app/agent/refund_reason_rules.yaml`（新建）；`app/agent/refund_reason_rules.py`
（新建）；测试 `tests/test_refund_reason_rules.py`（新建）。

**表形状（定死，逐字取自口径 §5.6.1）**：顶层键 `version` / `slot` / `enum` / `other_cues` /
`rules`；`slot: refund_reason`、`enum: RefundReason`；`other_cues` 是**非空、不得重复**的小写
子串列表（显式「其他」识别表，**优先于** `rules`，见口径 §5.6.1.1）；`rules` 为
`{refund_reason, cues}` 列表。

**测试断言**：

1. 加载成功，默认表可读；每个规则的 `refund_reason` 都是合法枚举成员；
2. 匹配语义是**小写子串命中**：入站文本先小写化再匹配；`cues` 为小写子串列表；
3. **表序即优先级**：同时命中两行时取表序靠前那行；
4. **未命中不产生值**：返回值为缺省，**不是** `OTHER`、不是空串；
5. 加载期报错逐条钉住：未知顶层键、未知规则键、`slot` 不是 `refund_reason`、`enum` 不是
   `RefundReason`、目标值不在枚举内、**目标值为 `OTHER`**、`cues` 为空、规则表为空、
   **`other_cues` 为空**、**`other_cues` 有重复项**；
6. loader 不读时钟、不用随机、不调模型、不查库；
7. **`other_cues` 优先于 `rules`**：文本同时命中两者时取 `OTHER`；`other_cues` 命中时不再看
   `rules`（口径 §5.6.1.1）。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_refund_reason_rules.py`。

### 任务 4 — 意图目录的退款原因槽位（先红）

**文件**：`app/agent/intents.yaml`（新增 `slots.refund_reason`；`REFUND_REQUEST.required_slots`
追加 `refund_reason`；`REFUND_REQUEST.clarify_prompts` 追加 `refund_reason`；`version` 同步 bump）；
测试 `tests/test_intent_catalog.py`（既有文件追加断言）。

**测试断言**：

1. 目录加载成功，`catalog.slot("refund_reason")` 存在，提示词含「其他 / 说不清」出口；
2. `REFUND_REQUEST` 的必填槽位含 `refund_reason`；
3. 缺失槽位时 `catalog.missing_slots("REFUND_REQUEST", {"order_id", "amount_cents"})`
   返回含 `refund_reason`；
4. `catalog.version` 与文件里的 `version` 一致（bump 生效）；
5. **不需要**改 `app/agent/intent_catalog.py` 的校验器（`slots.refund_reason` 用的全是
   `_SLOT_FIELDS` 现有合法键，`app/agent/intent_catalog.py:83-93`）——若实现时发现必须放宽
   `_TOP_FIELDS`（`:94`）才能落地，即为越界，退回口径重新裁定。

**连带（必须同批落地，否则对话侧语料会退化）**：本任务把 `refund_reason` 加进
`REFUND_REQUEST.required_slots` 之后，`evals/support_run.py` 的退款用例会停在追问上（原文里
没有原因线索）。语料侧的处置配方见**任务 11「语料口径」**，必须与本任务同一批落地。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_intent_catalog.py`；
`./.venv/Scripts/python.exe evals/support_run.py`（按任务 11 的语料口径处置后跑）。

### 任务 5 — 链路载体与对话侧采集（先红）

**文件**：`app/agent/routing.py`（`RoutePlan` 新增 `refund_reason` / `refund_type` 字段；
`classify` 按既有形状从 `context` 读 `refund_reason` / `refund_type`）；`app/agent/turns.py`
（`Turn` 新增同名字段）；`app/agent/support_agent.py`（组装 `context` 时按同形写入
`context["refund_reason"]`；上下文里**已有**显式 `refund_type` 时原样透传；算缺槽的 `present`
集合**只补 `refund_reason`**，**不补 `refund_type`**）；测试 `tests/test_refund_taxonomy.py`（追加）。

**测试断言**：

1. 路由计划在上下文带 `refund_reason` 时带上该值，**不带**时为缺省；
2. 上下文里的 `refund_type` 透传到路由计划；
3. 命中规则表时，对话侧写入 `context["refund_reason"]`；**未命中时该键不出现**；
4. 缺槽集合在对话侧能算出 `refund_reason` 缺失，且**算不出 `refund_type` 缺失**
   （`refund_type` 不进 `REFUND_REQUEST.required_slots`，§5.6.6）；
5. **模型不补位**：假模型给出退款原因时，路由计划的该字段仍只由规则命中 / 上下文决定。
6. **显式「其他」可继续**：客户回复「其他」时 `refund_reason` 取值为 `OTHER`；而**未命中任何
   关键词**的普通文本**不**被兜底成 `OTHER`（§5.6.2）。「其他」的**显式**识别按口径 §5.6.1.1
   走**同文件**的独立 `other_cues` 表（**优先于** `rules`），**不**进 `rules`（§5.6.1 禁止
   `OTHER` 作规则目标值），也**不**在 Python 里硬编码关键词。
7. **上下文已有值时不重新匹配**：上下文里已带合法 `refund_reason` 时沿用该值，本轮文本命中
   `other_cues` / `rules` **不**改写它（口径 §5.6.1.1）。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_refund_taxonomy.py`。

### 任务 6 — 写路径：载荷、工具入参、缺值闸门、类型派生、任务 API 422（先红）

**文件**：`app/services/approvals.py`（`refund_payload` 新增**关键字参数**
`refund_reason: RefundReason`、`refund_type: RefundType`，载荷新增键 `reason` / `refund_type`；
`create_refund_approval` 新增同名关键字参数并透传）；`app/tools/builtin.py`
（`check_refund_eligibility` 的入参加 `refund_reason` / `refund_type`，**键名带上下文**，
`additionalProperties` 保持关闭）；`app/services/runtime.py`（`_handle_refund` 在现有三道闸门后
**只补一道** `_clarify` 闸门：`plan.refund_reason is None`；**不补** `plan.refund_type is None`
闸门，改为在订单加载、资格校验通过后按 §5.6.6 **派生** `refund_type`）；
`app/schemas/tasks.py` + `app/api/v1/tasks.py`（缺 `refund_reason` → 422）；测试
`tests/test_refund_taxonomy.py`、`tests/test_refunds.py`（追加）。

**测试断言**：

1. 载荷键集合为 `{action, order_id, amount_cents, currency, resource_version, policy_version,
   reason, refund_type}`（B-3 落地后为九键，见任务 9）；同一订单两条不同 `reason` → 摘要不同；
2. `create_refund_approval` 的关键字参数名逐字是 `refund_reason` / `refund_type`；
3. 工具入参键是 `refund_reason` / `refund_type`，**没有**裸名 `reason`；
4. 缺退款原因时，`_handle_refund` 走澄清分支，**不建审批行、不执行退款**；**缺退款类型时
   不澄清**，而是派生后继续走；
5. 任务 API 缺 `refund_reason` 返回 **422**（`ValidationAppError`，`app/core/errors.py:58-60`），
   且**不**把缺失补成 `OTHER`；缺 `refund_type` **不**返回 422，也**不**把缺失补成 `FULL`；
6. **派生**（§5.6.6）：本次实退金额 == 订单当前可退余额 `orders.refundable_cents` → 派生
   `FULL`；小于 → 派生 `PARTIAL`；上下文里有显式 `refund_type` 时**采用显式值**，不被派生覆盖；
7. **两个入口同一条派生**：对话侧与任务 API 侧缺 `refund_type` 时得到**同一个**派生结果
   （不存在两套真相）；
8. 既有退款用例**逐条不退化**（默认路径下仍建审批行、仍 `WAITING_APPROVAL`）。
9. **补充说明的 422**：任务 API 提交 `refund_reason` 非 `OTHER` 且带 `reason_note` → **422**，
   不建任务；`reason=OTHER` 且带 `reason_note` → 通过（该条落到任务 7 的落库）。

**任务 API 的 422 判据（本任务定死，实现照此）**：
创建退款任务时，若（a）用 `app/agent/routing.py::classify(request, context)` 判定为退款意图，
或（b）上下文已出现 `refund_reason` / `refund_type` 任一，则 **`refund_reason` 必须存在且取值
合法**，否则 422。`refund_type` **缺失不再**是 422 理由（由服务端派生，§5.6.6）；显式提交了
`refund_type` 但取值非法，仍 422。**不得**为某个入口放宽 `refund_reason` 的必填性；对话侧的
「同一规则、另一种表现」是追问而非 422。

**第二条 422 判据（补充说明，本任务定死，实现照此）**：
`reason_note` **只在 `reason=OTHER` 时**合法。**非 `OTHER` 却提交了 `reason_note` ⇒ 422**
（§2「新记录约束」、口径 §5.6.3）。这条判定**只写一处**（退款写路径的共用入口），任务 API
把它翻译成 422、对话入口把它翻译成「不建退款任务」；**不得**静默忽略，也**不得**先存下
`reason_note` 等 `reason` 定了再决定。对话侧**不采集** `reason_note`（它是坐席侧自由文本，
不进 `REFUND_REQUEST.required_slots`，也不进 `slots` 声明）。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_refund_taxonomy.py tests/test_refunds.py`。

### 任务 7 — 退款落库与审批详情展示（先红）

**文件**：`app/services/refunds.py`（落库时写入 `reason` / `refund_type` / `reason_note`，
`reason_note` 仅 `reason=OTHER` 时可写；**非 `OTHER` 带 `reason_note` ⇒ 拒绝建单**，拒绝判定
在任务 6 的共用入口）；`app/services/approvals.py` 的审批详情快照
（`app/services/runtime.py::_approval_snapshot` 的载荷透出）；`web/src/pages/ApprovalWorkbench.tsx`
（审批详情展示退款原因 / 退款类型的中文唯一名，历史空值显示「未提供」，补充说明只在坐席侧可见）；
测试 `tests/test_refund_taxonomy.py`、`tests/test_refunds.py`（追加）。

**测试断言**：

1. 新建退款的记录上 `reason` / `refund_type` 等于提交值；
2. 历史空值读出为 `None`，展示层渲染为「未提供」，**不**映射成 `OTHER` / `FULL`；
3. 补充说明不进退款载荷 / 摘要 / 公开回执；
4. 公开回执含原因与类型的中文唯一名；
5. **`reason_note` 只在 `OTHER` 时落库**：非 `OTHER` 带 `reason_note` 时**不建退款任务**，
   该 `reason_note` 既未被静默忽略、也未落到 `refunds.reason_note`。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_refund_taxonomy.py tests/test_refunds.py`；
`cd web && npm run build`。

### 任务 8 — 审批档位表与 loader（先红）

**文件**：`app/core/approval_tiers.yaml`（新建，默认单层表）；`app/core/approval_tiers.py`
（新建，`@lru_cache(maxsize=1)` 加载 + 解析）；测试 `tests/test_approval_tiers.py`（新建）。

**测试断言**：

1. 默认表只有一档，角色为 `approver`，所需审批数为 1；
2. 解析是纯函数：同一金额两次解析逐字相同；取首个 `max_cents` 不小于该金额的档；
3. 金额大于所有档位上界 → 取**最后一档**（不是最低档）；
4. 加载期报错逐条钉住：未知顶层键、未知档位键、`tier_id` 缺失 / 重复 / 非大写蛇形、
   `max_cents` 非整数 / 负数 / 重复或倒退、`roles` 为空 / 含非 `approver` / 档内重复、
   `required_approvals != 1`、档位表为空；
5. **角色列表规范化**（§2.3 校验 5）：装载后每档的 `roles` **按字典序排序**；档位快照里的
   `roles` 序列等于该档 `roles` 的字典序排序结果；同一组角色换一种书写顺序，快照逐字相同
   （当前唯一合法角色集合是单元素 `["approver"]`，该断言此时是恒等变换、**既有读数不变**，
   扩角色的那一轮才有区分力）；
6. loader 不读时钟、不用随机、不调模型、不查库。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_approval_tiers.py`。

### 任务 9 — 档位快照进摘要与执行前重校验（先红）

**文件**：`app/services/approvals.py`（退款载荷新增键 `approval_tier`，值为档位快照
`{tier_id, max_cents, roles, required_approvals}`；`create_refund_approval` 透传）；
`app/services/refunds.py`（执行前用**当前**档位表重解析该金额的档位，与载荷快照比对；
不一致 → 审批失效、原因码 `APPROVAL_TIER_CHANGED`、不执行退款、按既有失败形状转人工复核；
载荷**无**该键的存量审批跳过这项重校验）；测试 `tests/test_approval_tiers.py`（追加）。

**测试断言**：

1. 载荷键集合为九键，`approval_tier` 的值是**解析结果**的快照；
2. 只改档位表的 `roles` / `required_approvals`（`tier_id` 不变）→ 摘要**变化**（改的是角色
   集合的**内容**）；
3. 只改不影响解析结果的字段 → 摘要**不变**；
4. 同一组角色只换**书写顺序** → 摘要**不变**（角色列表在装载期按字典序规范化，§2.3 校验 5）；
5. 档位变化后，存量 PENDING 审批在执行前被判失效、退款**未执行**、原因码为
   `APPROVAL_TIER_CHANGED`；
6. 载荷里没有 `approval_tier` 键的存量审批**不被**这条规则误伤（既有 `verify_binding`
   行为不变）；
7. 既有绑定校验（`app/services/approvals.py:127-135`）对**存量载荷**重算，存量审批不因
   新键而失效。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_approval_tiers.py tests/test_refunds.py`。

### 任务 10 — 阈值生效与自动执行路径（先红）

**文件**：`app/services/runtime.py`（`_handle_refund` 按 `eligibility["requires_approval"]` 分支；
自动路径不起审批行、不进 `WAITING_APPROVAL`、写任务事件 `refund.auto_executed`、写审计
`action="refund.auto_executed"`、任务 `result` 带 `approval_path`（`AUTO` / `APPROVAL`）、
回执措辞区分「自动处理」与「审批通过」、客户可见文本不含档位与阈值）；
`app/services/refunds.py`（**按口径 §4.2.1 重构**：拆出「权威执行体」与「审批绑定包装器」，
两条路径调用**同一个**执行体；自动路径改用台账 `request_digest` 重算比对、同样跑一次
`RefundPolicy.check`）；测试 `tests/test_approval_tiers.py`（追加）。

**重构授权与不变量（口径 §4.2.1，实现照此）**：
`RefundExecutionService.execute(task_id, approval_id)`（`app/services/refunds.py:127`）在入口处
硬依赖审批行（`:129` `approvals.require`、`:135-136` 审批-任务归属校验、`:130` 从审批载荷读
金额与订单号），而自动路径**按定义没有审批行**，因此本任务**必须**做结构重构：

- **权威执行体**承载全部权威校验与写操作（订单加锁、`blocking_refund_except` 冲突判定、
  资格复核、幂等占位、渠道调用、验证对账、终态落库与回执）；
- **审批绑定包装器**（现有 `execute`）变成薄包装：取审批行 → 校验归属与状态 → `verify_binding`
  → 取审批载荷 → 调权威执行体；
- **自动执行入口**调**同一个**权威执行体，绕过审批绑定部分，改用台账 `request_digest` 重算比对。

**不变量**：①权威执行体**只有一份**，两条路径调同一个函数，不得出现「自动执行专用」的分支
实现；②六项权威校验（加锁、并发冲突、资格复核、幂等占位、渠道调用、验证对账）**强度相同**，
不得以「没有审批行」为由跳过任一项；③两份绑定校验（审批路径 `verify_binding`、自动路径台账
摘要比对）**都保留**，不是二选一；④资格不过 / 绑定不过 / 并发冲突时两条路径**同样**不执行退款、
同样写台账取消态、同样转人工复核（自动路径无审批行可失效，改为写审计与台账错误码）；
⑤**不得改变审批路径的既有行为**（既有退款用例读数逐条不变）。**禁止**：在自动路径复制一份退款
执行逻辑；给工具入口加「跳过审批」开关；把资格复核或绑定比对降级为「可选」。

**测试断言**：

1. 阈值之上 → 建审批行、任务 `WAITING_APPROVAL`、`result.approval_path == "APPROVAL"`；
2. 阈值之下 → **无审批行**、任务直接到终态、有执行台账行、有 `refund.auto_executed` 事件、
   `result.approval_path == "AUTO"`；
3. 同一笔金额在阈值上下**行为相反**（两条用例成对写）；
4. 自动路径**同样**在执行前复核资格：资格不过 → 不执行、转人工复核；
5. 自动路径用台账 `request_digest` 重算比对，摘要对不上 → 不执行；
6. 两条路径的公开回执措辞**不同**；客户可见文本里检索不到 `tier_id` / `roles` /
   `required_approvals` 与阈值金额；
7. 回归断言：`request_refund` 的 `requires_approval` 仍是 `True`，网关审批校验未变；
8. 默认配置（阈值 0）下既有 Golden 与退款用例**逐条不变**；
9. **结构断言（§4.2.1 不变量 1）**：退款执行的**权威写路径只有一处**——两条路径最终调用的
   是同一个执行体函数；代码里**不存在**只在自动路径生效的执行分支实现（实现方式：由
   「自动路径与审批路径共用同一执行体」这一可观察行为钉住，例如同一笔退款在两条路径下的
   资格不过 / 并发冲突分支产生**同样**的台账状态、审计动作与回执文案）。

**复验**：`./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_approval_tiers.py tests/test_refunds.py`；
`./.venv/Scripts/python.exe -m evals/run.py`（Golden 集不退化）。

### 任务 11 — 全量门禁与不退化读数

```bash
./.venv/Scripts/python.exe -m pytest -o addopts=            # 全量
./.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m alembic check
./.venv/Scripts/python.exe -m pytest tests/test_architecture_guard.py tests/test_docs_consistency.py
./.venv/Scripts/python.exe evals/run.py
./.venv/Scripts/python.exe evals/support_run.py
cd web && npm run build
```

- 读数重定向到 `.tmp/*.txt` 并设 `PYTHONIOENCODING=utf-8` 再读文件。
- 要求：全绿；默认配置下 Golden 与退款用例读数与本轮之前一致（逐条对比）。
- 阈值生效会联动 `risk` 转人工信号（`app/services/escalation.py:286-290` 读同一个阈值）：
  阈值以下的金额不再触发该信号、追加话术随之消失——这是**预期**行为，读数差异要**写进 ADR**，
  不得当作回归悄悄改掉。

**语料口径（本任务同时交付语料处置；下表的规模是 2026-10-26 实测值）**：

「读数逐条不变」指的是**读数**不变，不是「语料一个字都不动」。`refund_reason` 进
`required_slots`（任务 4）与任务 API 的 422 闸门（任务 6）改变的是**输入要求**，因此语料侧
必须补齐输入；补齐的**方式**必须保证既有断言逐字不变。

| 语料 | 实测规模 | 处置 | 读数判据 |
|---|---|---|---|
| `evals/golden_set.json` | 共 **40** 条；`REFUND_REQUEST` **20** 条（其中 **17** 条走到资格工具、**3** 条更早澄清） | 每条退款用例**补一个** `context`：`{"refund_reason": "<RefundReason 枚举值>", "refund_type": "<RefundType 枚举值>"}`；**请求文本与全部 `expect` 断言逐字不动** | **40/40 不变**。`evals/run.py:81-83` 已支持按用例带 `context`；`tool_params` 只比对**列出的**键（`evals/run.py:260-271`），因此新增的工具入参不会顶掉既有断言 |
| `evals/support_set.json` | 共 **62** 条；`REFUND_REQUEST` 意图 **9** 条：`handoff-after-clarify`、`refund-3turn`、`refund-one-clarify`、`refund-invalid-fixed`、`refund-amount-first`、`refund-order-first`、`refund-cancel`、`refund-expire`、`refund-approve`；其中 **8** 条走到退款写路径（除 `handoff-after-clarify` 外全部） | 这 9 条的退款消息**补一句原因线索**（如「商品有质量问题」），使规则表能命中；**期望逐字不动**，除非新规则必然改变某一轮（例如多出一轮澄清）；凡是改了的一律**逐条登记**（改了哪一条、为什么改） | `handoff-after-clarify` **不受影响**：它第 0 轮缺的是 `order_id`，而 `missing_slots` 按 `required_slots` 顺序返回，第 0 位仍是 `order_id`，追问与 `span_attrs` 断言不变。**补线索时不得在触发「恢复」的那一轮里出现「我要退款 / 申请退款」等动作词**，否则该轮不再被识别为「对上一轮澄清的回答」，`support.classify` 的 `resumed` 读数会翻转 |
| `evals/business_metrics.py` | 退款场景 **12** 个，来自 **3** 个模板（`refund-approved-*` 5 / `refund-rejected-*` 3 / `refund-out-of-window-*` 4） | 原因线索**加在同一轮**的话术里（不新起一轮） | 本轮读数与**上一轮并排**报告（不覆盖旧读数），差异逐条说明 |
| `tests/test_refunds.py` 等既有测试 | 现有退款用例 | 建退款时**补上** `refund_reason` / `refund_type` | **断言不动**（逐条不退化） |
| `tests/test_refund_taxonomy.py` | 本轮新建 | 覆盖新的 422 行为（缺 `refund_reason` → 422；缺 `refund_type` → **不** 422 且被派生）与派生规则（`FULL` / `PARTIAL` / 显式优先 / 两入口同源） | 见下方「为什么不新增 Golden 用例」 |

**为什么不新增 Golden 用例**：Golden 集条数（当前 **40**）与 pytest 收集到的用例数（当前
**885**）都被文档一致性守卫**现算现比**（`docs/fact-baseline.yaml` 的 `golden_cases` /
`test_collected` 两条事实，落点是 `docs/records/智能体平台设想.md`、`docs/spec/项目规格.md`、
`docs/下一步规划.md`）。新增一个测试函数会把收集数从 885 变成 886，新增一条 Golden 用例会把
40 变成 41——两者都会让 `tests/test_docs_consistency.py` 变红，并要求同批改这三份文档的数字 +
重生成说明书，那属于**文档同步轮**的事。因此本轮口径定为：**新的 422 行为与派生规则由
`tests/test_refund_taxonomy.py` 覆盖，不新增 Golden 用例**；实现轮若确有必要增删用例，必须
**同批**同步守卫事实与那三份文档的读数。

### 任务 12 — 文档同步与独立审查

**文件**：`docs/adr/ADR-0XX-refund-reason-and-approval-tiering.md`（新建，含被否决方案：
「档位表接管阈值判定」「把自动执行标记也放进摘要」「多级会签」「按客户等级分层」「给
`refund_type` 建关键词规则表」「追问客户『全额还是部分』」「对话侧退款全部转人工」，并登记
B-1 口径 §5.6.6 的代价：自助链路上产生不了 `SHIPPING` / `PRICE_PROTECTION`）；
`docs/spec/项目规格.md`（术语表、`action_digest` 覆盖字段由六项改九项、退款原因 / 类型 / 档位的
业务事实、阈值生效后的两条路径）；`docs/guide/接口说明.md`（`TaskCreateRequest.context` 的
`refund_reason` / `refund_type` 两键、**缺 `refund_reason` 时**的 422、`refund_type` 缺值由
服务端派生的说明、对话侧追问话术、审批详情新字段）；
`docs/guide/评测说明.md`（**语料处置登记**：Golden / support / business_metrics 的改动逐条登记在本轮
**任务 11「语料口径」**那张表上——哪条用例、加了什么输入、期望是否改动及原因；business_metrics
的本轮与上一轮读数**并排**给出）；`docs/guide/证据索引.md`（本轮实跑读数）；`docs/下一步规划.md`
（B-1 / B-3 状态与顺序结论）；`docs/guide/架构说明.md`；`README.md`；`CONTRIBUTING.md`；
`.env.example`；说明书重生成；守卫事实计数同步（`docs/fact-baseline.yaml`）。

**审查重点**：见本文件「审查重点」一节（10 条）。

---

## 审查重点

交给 reviewer 时逐条点名（reviewer 读代码与 diff 判定，不运行测试）：

1. 缺 `refund_reason` 是不是**真不放行**：对话侧追问带「其他」出口、API 侧 422，且两侧读同一个上下文键；缺 `refund_type` 是不是**走同一条服务端派生**（不追问、不默认 `FULL`、两个入口结果一致）；
2. 有没有把「未命中」悄悄兜底成 `OTHER`（这会污染 B-4 想要的原因分布）；
3. 模型有没有被偷偷开了抽取退款原因 / 类型的权限；
4. 裸名 `reason` 有没有出现在三个合法位置之外；
5. 阈值生效后**自动路径**是不是同样复核资格、同样有台账与对账；
6. 自动路径有没有绕开工具侧的审批闸门；
7. 档位快照是不是**解析结果**而不是原始表行（否则改表不改编摘要）；
8. 存量审批有没有被新键或档位重校验误伤；
9. 默认配置下既有读数是否**逐条不变**；
10. 阈值生效带来的 `risk` 转人工信号联动有没有写进 ADR，而不是悄悄消失。

---

## 待办：需他处同步（本轮不改）

本轮只做口径与规格，**不修改任何既有文档**。实现轮必须同步下列位置（本轮已逐条核对）：

| 位置 | 现状 | 需要改成的方向 |
|---|---|---|
| `docs/spec/项目规格.md:118` | `action_digest` 描述为「对动作、订单、金额、币种、资源版本、政策版本做规范化 SHA-256」（六项） | 改为九项，补退款原因、退款类型、审批档位 |
| `docs/guide/接口说明.md` | 未描述 `TaskCreateRequest.context` 的 `refund_reason` / `refund_type` 两键、缺值 422、对话侧新增追问话术 | 按 B-1 口径 §5.6.7 补齐 |
| `docs/下一步规划.md` 的 **B-1 段**与 **B-3 段** | 仍是「未排期、未实现」的待办记录 | 实现轮更新状态与顺序结论 |
| `web/src/pages/ApprovalWorkbench.tsx:143-146` | 绑定校验只读 `order_id` / `amount_cents` / `resource_version` | 补档位快照与原因 / 类型的展示（不做选择器 UI） |
| `docs/fact-baseline.yaml` | 守卫事实计数 | 随新增测试与文件同步 |
| `openspec/specs/` | 归档前不得写入增量 | 验收通过后把本变更的两个增量并入基线 |
