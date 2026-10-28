# 分段耗时、根 span 与两树关联键（R9）：项目级口径

> 本文件是 TicketPilot 本轮工作（给四类分段 span 补耗时读数、给每棵树补一个根 span、给对话树
> 与任务树一个共同关联键）的项目级声明，只规定术语、契约、迁移形状、边界与验收口径。流程仍遵循
> 用户级与项目级协作规则；业务事实以 `docs/spec/项目规格.md` 为准，两者冲突时先改本文件并说明
> 理由，再改代码。
>
> 来源：`docs/下一步规划.md` §0.2 第 72 行（R9 行）与 §14.6 第 2648 行（R9 段）。两处原文都只写
> 「四类 span 补耗时、补根 span、两表关联」，**都没有枚举是哪四类**，也没有指定关联键取哪一列、
> 历史数据怎么办。本文件 §1 把枚举钉死，§2 给契约与迁移形状，§4 划边界，§7 登记三处裁定与代价。
>
> 「当前事实」一律给 `文件:行` 或 `文件::函数名` 证据，且是本次接单时逐处读过的。行号按当时工作树
> 读数记录，实现时可能已漂——**以符号名为准，行号只作定位提示**。标注**「待实现」**的项是本轮
> 已裁定、代码尚未落地的口径。

---

## 1. 术语表

### 1.1 唯一定义

| 术语 | 唯一定义 |
|---|---|
| 树（tree） | 一次 `TraceRepository.flush(recorder)` 写下的一批 span，由 `trace_id` 标识。写入侧见 `app/repositories/traces.py::TraceRepository.flush`（`:51-66`），每条 span 带 `recorder.trace_id`（`:55`）。「树」不等于「对话」，也不等于「任务」。 |
| 根 span（root span） | 一棵树里 `kind == "root"` 且 `parent_span_id is None` 的那一条 span。**一棵树恰好一条**。 |
| 段（segment） | 一次请求里**可以独立变慢**、且读者会拿它来定位「慢在哪」的一段工作。段与 span 不是一对一：同一个段在不同入口各有一条 span。 |
| 分段 span（segment span） | `kind` ∈ {`understand`, `retrieve`, `generate`, `guard`} 的 span。这四类就是 R9 原文说的「四类 span」。逐条定义见 §1.2。 |
| 耗时读数（latency reading） | `SpanBuffer.latency_ms`（`app/observability/tracing.py:71`）与其落库列 `trace_spans.latency_ms`（`app/models/observability.py:35`）。整数毫秒，非负。 |
| 关联键（link key） | 本轮新增的 `tasks.conversation_id` 列（**待实现**）。对话树与任务树在库里靠它串起来；值域等于 `conversations.id`（`app/models/conversation.py:22`，`String(40)`）。 |

### 1.2 四类分段（封闭枚举）

| 段 | `kind` | 定义 | 当前产出者（`文件::函数名`，行号） | 耗时现状 |
|---|---|---|---|---|
| 理解段 | `understand` | 把一句话读成意图与槽位的那一段。 | 对话侧 `support.classify`：`app/agent/support_agent.py::SupportAgent.handle` `:334-350`<br>任务侧 `task.route`：`app/services/runtime.py::TaskRuntime._run` `:436` | 两者都是 0（`trace.add` 未传 `latency_ms`，取 `tracing.py:98` 的默认值） |
| 检索段 | `retrieve` | 取证据的那一段。 | `policy.search`：`app/agent/actions.py::policy_question` `:335-353` | 0 |
| 生成段 | `generate` | 产出给人看的那段文字或效果。 | `support.reply`：`support_agent.py::handle` `:604-621`<br>`memory.personalize`：`support_agent.py::_personalize` `:998-1009`<br>`analytics.narrate`：`actions.py::_prefetch_answer` `:475-488` | 三者都是 0 |
| 护栏段 | `guard` | 回答成型前后做的那道检。 | `output.grounded`：`support_agent.py::_guard_output` `:948-956`<br>`analytics.faithfulness`：`actions.py::_prefetch_answer` `:489-498`<br>`tool.degraded.<tool>`：`app/services/runtime.py::TaskRuntime._degrade` `:1416-1427` | 前两个是 0；第三个**已有**读数（`:1426`） |

**排除项（本轮明确不算「四类」）**：

| 被排除的名字 | 现状证据 | 排除理由 |
|---|---|---|
| `tool` | `runtime.py::_call_tool` `:1303-1395`，三处 `latency_ms=int((time.perf_counter() - started) * 1000)` 在 `:1354`、`:1369`、`:1388` | 已经有耗时。R9 现状列说的「分位数只覆盖工具 span」正是这一类的现状。 |
| `approval` | `runtime.py` `:742-752`、`:803-815`、`:867-877`、`:928-933`、`:955-963`、`:972-980` | 事务与记账记录：各自标记一个**已经发生**的事实，起止点没有「这一段做了多少活」的含义。 |
| `handoff` | `support_agent.py::handle` `:503-516`、`support_agent.py::_silent` `:1045-1053` | 同上：转人工状态变化是一条事件，不是可独立变慢的段。 |
| `reconcile` | `runtime.py::approve` `:985-994` | 同上：对账结果记录。 |
| `root` | **待实现** | 是新增的根，不是段。 |

### 1.3 必须消歧的同名概念

1. **`latency_ms` 与 `attributes["duration_ms"]` 不是两套耗时**。`latency_ms` 是列，也是读 API 与
   metrics 唯一读的字段（`app/api/v1/traces.py:134` 的逐 span 输出、`:88-92` 的 p95 计算）。`duration_ms`
   是同一个数**目前只写在 span 属性 JSON 里**的那份拷贝（`app/sql/service.py` 的三个 tool span，`:247`、
   `:318`、`:335`；这三个 span 的 `trace_spans.latency_ms` 是 0，见 §2.1 第 3 条；本轮补写后两者才相等）。
   **本轮落地后**同一个数会有**三个载体**：`trace_spans.latency_ms`、span 属性 `duration_ms`、
   `sql_queries` 台账的 `duration_ms`（`app/sql/service.py::SqlAnalyticsService._record`
   的 `duration_ms` 形参，`:439-472`）。**三者本轮不合并、不互相换算、不相加、不取平均**。
2. **`trace_id` 与 `conversation_id` 不可互相替代**。`trace_id` 标识一棵树（`app/models/observability.py:27`；
   `tasks.trace_id` 在 `app/models/task.py:46`）；`conversation_id` 标识一次多轮对话
   （`trace_spans.conversation_id` 已有，`app/models/observability.py:30`；`tasks.conversation_id` 待实现）。
   一轮对话可以有多棵树（§4.8.a 登记了同一轮的两棵树），所以「按 `trace_id` 串」串不出整轮；
   「按 `conversation_id` 串」会把一轮里的所有树一起串出来。**两个键都留，含义不同**。
3. **`understand` 有两个产出者，不是一段**。对话侧 `support.classify`（`support_agent.py:334`）记录的是
   「规则+模型最终读法」，属性含 `intent`/`route_intent`/`model_gap`；任务侧 `task.route`
   （`runtime.py:436`）记录的是任务提交那一刻的路由。同 kind、不同 name、不同入口，**不得**在报表里
   合并成「理解段跑了一次」。
4. **`root` 是 kind 值，不是 `trace_id`，也不是 `parent_span_id`**。「这棵树的根」只能靠
   `kind == "root"` 找；不得用 `parent_span_id is None` 当唯一判据（`add()` 产出的顶层 span 现在也是
   `parent_span_id is None`，见 `tracing.py:107`）。
5. **「段」与「span」不是一对一**。`generate` 类有三条 span、`guard` 类有三条，「生成段有耗时」要三条
   都有读数才算（§5 第 2 条）。
6. **`decision.reseal` 是 `understand` 类但不是理解段**。它在 `support_agent.py:358-362`，是「重算摘要」的
   记账动作，属性是键名而非读数。它**不计入** §5 的「四段」验收；也不得据此声称理解段已补耗时。

---

## 2. 契约

### 2.1 四类分段补耗时

1. **唯一算法**：`app/observability/tracing.py` 增加模块级函数
   `elapsed_ms(started: float) -> int`（**待实现**），实现就是现有 `TraceRecorder._elapsed_ms`
   （`:189-191`）的函数体：`int((time.perf_counter() - started) * 1000)`。
   `_elapsed_ms` 改为委托它，**不得**留第二份同式。
2. **手写同式必须一起改**，否则「唯一实现」是假的。`app/services/runtime.py` 现在有四处
   `int((time.perf_counter() - started) * 1000)`：`_call_tool` 的 `:1354`、`:1369`、`:1388` 与
   `_degrade` 的 `:1426`。四处都改成调用 `elapsed_ms(started)`。
3. **`app/sql/service.py` 的 `duration_ms` 算式保留原状，span 侧本轮补 `latency_ms=duration_ms`**。
   三处算式（`:216`、`:222`、`:288`）算的是 `sql_queries` 台账与 span 属性共用的那个数，不是
   `latency_ms`；**算式一个字符都不改，也不并入 `tracing.elapsed_ms`**。
   **现状**：三个 tool span（`analytics.query`、`analytics.query` 的 PREFETCH 分支、`analytics.prefetch`）
   目前**只写属性** `duration_ms`（`:247`、`:318`、`:335`），**没有**任何 `latency_ms=` 实参，
   所以 `trace_spans.latency_ms` 列取 `tracing.py:98` 的形参默认值 0
   （`grep -n latency_ms app/sql/service.py` 零命中）。
   **本轮**：这三处 `trace.add("tool", ...)` 各增写一个 `latency_ms=duration_ms` 实参，复用**同一行
   作用域里已算好的那个变量**；`duration_ms` 属性**保留不动**，两者数值必须逐字相等。
   **不得**新增第四种算式、**不得**改 `:216`/`:222`/`:288` 三个算式本身。
   **理由**：`latency_ms` 列是 `GET /api/v1/traces/{trace_id}`（`app/api/v1/traces.py:134`）与 metrics
   （`:88-92` 的 p95）**唯一读**的字段，属性里的 `duration_ms` **不进 p95**；不写则这三个 span 的
   读数对外恒为 0。这属「把已有的数写进唯一被读的载体」，不是第二套算法（§1.3 第 1 条）。
4. **起点必须是该段工作真正开始的那一刻**的 `time.perf_counter()` 读数；终点取在对应的
   `trace.add(...)` 之前。
5. **不得用「recorder 构造时刻」冒充跨了前面几段的耗时**。对话侧的 `TraceRecorder` 在阶段四才构造
   （`support_agent.py:328-333`），而输入护栏（`:289`）、槽位读取（`:307`）、模型咨询（`:309-311`）都在
   它之前；任务侧同样先 `brain.plan(...)`（`runtime.py:399-401`）再构造 recorder（`:402`）。所以
   `support.classify` 与 `task.route` 的起点必须**单独取一次** `perf_counter()` 并把读数带进
   `trace.add`。**本轮不要求**把 recorder 构造点上移（上移会改变 span 归属，并影响 `_silent` 那棵
   第二棵树，见 §4.8.a）。
6. **为了可打桩，调用点必须写成模块属性访问**：`from app.observability import tracing` 之后
   `tracing.elapsed_ms(started)`，或 `import app.observability.tracing as tracing` 同理。**不得**
   `from app.observability.tracing import elapsed_ms` 后直接调名——那样 §5 第 2 条的打桩断言失效
   （打桩的是 `app.observability.tracing.elapsed_ms`，不是各模块里的局部名）。
7. **`app/sql/service.py` 的第四个 tool span** `analytics.route`（`_trace_route` `:351-373`，`add` 在
   `:362-373`，调用点 `:120`、`:138`、`:166`、`:304`）**没有** `duration_ms`，且它的前三个调用点都在
   `started = time.perf_counter()`（`:202`）之前——只有 `:304`（在 `_answer_prefetch` 内，`started` 在
   `:284`）有起点。本轮**不得**给它写 0、写估算值或事后另起一个计时点（那会得到一个与其他三个调用点
   不同源的数）。要么先让四个调用点都拿到真实起点再补，要么在交付说明里登记为**明确缺口**（§5 第 1 条）。
8. 读数要落在读者能看到的地方：`GET /api/v1/traces/{trace_id}` 的逐 span `latency_ms`
   （`app/api/v1/traces.py:127-140`，字段在 `:134`）。本轮**不新增**对外字段。

### 2.2 `latency_ms` 的值域与「不许恒为 0」

1. 类型与默认值三处一致：列 `Integer, default=0, nullable=False`（`app/models/observability.py:35`）、
   buffer `latency_ms: int = 0`（`tracing.py:71`）、`add()` 形参默认 `latency_ms: int = 0`（`tracing.py:98`）。
   本轮**不改**这三处默认值（改默认值会让「没传」和「真的是 0」不可分）。
2. **0 是合法读数**：一段工作在 1 毫秒内完成时 `elapsed_ms` 就返回 0。因此「四段都有耗时」的验收
   **不得**写成 `assert latency_ms > 0`，也不得用 `time.sleep` 去制造非零读数。判据见 §5 第 2 条
   （结构性 + 打桩）。
3. 非负与整数在产出侧保证：`elapsed_ms` 返回 `int(...)`，`time.perf_counter()` 单调（`tracing.py:190`）。
   **不得**加 `max(0, ...)` 或 `round()` 去掩盖一个算错的起点——掩盖之后「段耗时算错」就不再可观测
   （项目级「显式失败」口径）。
4. 读数不进重放比对，所以补读数是**回放安全**的：`evals/replay.py::_load_source` 组 span 字典时只取
   `kind`/`name`/`status`/`attributes`（`:135-143`），根本不读 `latency_ms` 列；即使有人把耗时写进
   **属性**，`_VOLATILE_ATTRS`（`:67-86`）里已有 `latency_ms` 与 `duration_ms`，同样被过滤。两处都
   不需要改 `evals/replay.py` 的比对规则（§4 第 6 条）。

### 2.3 根 span

1. **`SpanKind` 只加一个值**。`tracing.py:49-58` 现在是 8 个（`guard`/`understand`/`retrieve`/`generate`/
   `tool`/`approval`/`handoff`/`reconcile`），改为 9 个，新增 `"root"`。同一批里**不得**再加别的 kind。
2. **产出点只有一个**：`TraceRecorder.finalize()`（**待实现**），由 `TraceRepository.flush`
   （`app/repositories/traces.py:51-66`）在写之前调用。四棵树的构造点各一处——`support_agent.py:330`、
   `support_agent.py:1041`、`runtime.py:402`、`runtime.py:897`；六个 flush 点——
   `support_agent.py:622`、`:1054`、`runtime.py:470`、`:934`、`:964`、`:995`。
   **禁止**在调用方写 `trace.add("root", ...)`：那会让「一棵树一个根」变成调用方的自觉。
3. **`name` 只有三个值**，由 recorder 构造时给出：`support.turn`（对话轮，`support_agent.py:330` 与
   `:1041` 两处都用它）、`task.run`（`runtime.py:402`，`_run`）、`task.approve`（`runtime.py:897`，`approve`）。
   加第四个名字要先改本文件。
4. **父子规则**：`finalize()` 把所有 `parent_span_id is None` 的 span 重挂到根上；根自己的
   `parent_span_id is None`；嵌套仍然按栈顶 `self._open[-1].id`（`tracing.py:107`、`:122`）——finalize
   只处理**顶层**，不改任何中间层。
5. **`_open` 必须为空，但生产路径不得因此抛**。`span()` 的 `finally` 已经保证弹出
   （`tracing.py:148-149`），正常情况下 finalize 时 `self._open` 为空。两层要求**分开写、不得混成一条**：
   - **生产路径**：`finalize()` **不得 raise**——recorder 的既有承诺是「不把异常抛进请求路径」
     （`tracing.py:8-11`）。若 `self._open` 意外非空，按该承诺**吞掉**：既不抛，也不为它新增 span
     属性（根属性恒为 `{}`，见第 8 条），请求照常返回。**不得**把 `assert not self._open` 放进生产路径。
   - **单测**：`not self._open` 的断言只写在**单测**里（或在单测里断言 `finalize()` 之后 `_open` 为空），
     失败即红——那是编程错误的唯一暴露点。
6. **幂等**：同一个 recorder 第二次 `flush` 不新增根、不改已有父级、`len(recorder.spans)` 不变。当前
   调用图里每个 recorder 只 flush 一次（`runtime.py::approve` 的三个 flush 点在
   `if/elif/else` 三条互斥分支上，`:934` 之后 `raise`、`:964` 之后 `return`、`:995` 之后 `return`），但
   幂等仍是硬要求——它让「以后有人加一条重试路径」不会静默多出一条根。
7. **根排在最后**：根进入 `recorder.spans` 的位置是末尾。`list_for_trace` 按 `started_at, id` 排序
   （`traces.py:68-75`），所以它读出来也在最后。这样 `get_trace` 的 `spans[0].conversation_id`
   （`api/v1/traces.py:118`）与既有断言 `spans[0]`/`spans[1]`（`tests/test_trace.py:91`、`:93`）不被顶掉。
   **不得**把根插到 `spans` 头部。
8. **根的属性本轮为空字典 `{}`**。需要「这棵树几条 span」的读者按 trace 数 span，不额外落字段。
   即使为空，属性仍要过 `redact_payload`（`tracing.py:181`，finalize 走同一条 `_record` 路径）。

### 2.4 关联键

1. **键名与类型**：`tasks.conversation_id`，`String(40)`、`nullable=True`、`index=True`（**待实现**）。
   照 `tasks.trace_id`（`app/models/task.py:46`：裸列 + 索引，**无外键**）与 `trace_spans.conversation_id`
   （`app/models/observability.py:30`：同形状）写。`tasks` 上没有任何外键（`app/models/task.py:24-55`）；
   `ForeignKey` 只出现在同文件的 `TaskEvent`（`:64`）。**不加外键**：SQLite 上 `batch_alter_table` 加外键
   要走表重建，收益不成比例，且这两张既有表都没有先例。
2. **三个写入点，缺一不可**：
   1. `app/repositories/tasks.py::TaskRepository.create`（`:38-64`）增加 `conversation_id: str | None = None`
      形参，并写进 `Task(...)`（现有 `trace_id=` 在 `:58`）。
   2. `app/services/runtime.py::TaskRuntime.create_task`（`:293-301`）增加同名关键字参数，透传给
      `services.tasks.create(...)`（调用点在 `:339-348`，`trace_id=` 在 `:345`）。
   3. 对话侧建单的桥：`app/agent/actions.py::_create_task`（`:396-402`）把 `request.conversation_id` 传进去。
      该字段已存在（`ActionRequest.conversation_id`，`actions.py:70`），由
      `support_agent.py::SupportAgent._request` 的 `:1150` 从 `state.conversation_id` 填入。
3. **直接调 API 建的单一律 `NULL`**。`app/api/v1/tasks.py::create_task`（`:24-47`）的请求体
   `TaskCreateRequest`（`app/schemas/tasks.py:8-18`）只有 `request` 与 `context`，**不新增**
   `conversation_id` 字段；`runtime.create_task(...)` 的调用（`api/v1/tasks.py:42-47`）不加该参数。
   加字段等于把「对话轮的键」变成客户端可伪造的输入，与「两棵树在库里自证同源」的目的相反。
   `TaskResponse`（`app/schemas/tasks.py:52-67`）也**不加**该字段——它不在本轮对外口径里。
4. **禁止把该键写进 `task.context`**。`request_digest` 由
   `canonical_digest({"request": request, "context": context or {}})` 现算（`runtime.py:311-315`）；
   把键塞进 `context` 会改摘要，摘要不等即**显式 `ConflictError`**（`runtime.py:328-331`），
   `:332` 才是 `return self._replay(identity, existing_id)`；于是同一个幂等键的重试从「返回第一个
   任务」变成 409。唯一约束 `uq_tasks_idempotency`（`app/models/task.py:30-32`）**只在并发竞态里
   当仲裁者**（`create_task` 的 docstring `:302-309`：`The unique constraint is the arbiter under a
   race; the lookup here only saves the common sequential retry a write.`）。键**只能走列**。
5. **span 侧几乎已就绪**。`trace_spans.conversation_id` 已存在（`observability.py:30`），
   `TraceRepository.flush` 已经把 `recorder.conversation_id` 写进去（`traces.py:61`）。当前只有对话树
   填了它（`support_agent.py:332`、`:1043`），任务树与审批树没填（`runtime.py:402`、`:897` 构造时都没传
   `conversation_id=`）。所以 span 侧本轮只做一件事：这两处传 `conversation_id=task.conversation_id`。
6. **怎么串**：`tasks.conversation_id` → `trace_spans.conversation_id`，读侧已有
   `TraceRepository.list_for_conversation`（`traces.py:77-87`，带 `limit=200`）。两棵树各自有自己的
   `trace_id`，靠这一个键落到同一个对话上。
7. **回放侧已按该键分组**：`evals/replay.py::_load_source` 已经给每个对话收集 trace_id
   （`:145-152`），任务树开始写 `conversation_id` 后同一个对话的列表会多出任务树的 trace_id——
   连带影响见 §4.8.e。

### 2.5 迁移

1. **只改一张表一列一索引**：`tasks` 加 `conversation_id VARCHAR(40) NULL` 与 `ix_tasks_conversation_id`。
   **不回填历史行**（`NULL` 就是「本列上线前建的」）。
2. **形状照抄** `alembic/versions/d5a91b73e204_task_trace_link.py`（`tasks.trace_id` 那条）：两段
   `with op.batch_alter_table('tasks', schema=None) as batch_op:`，先 `add_column`，再
   `create_index(batch_op.f('ix_tasks_conversation_id'), ['conversation_id'], unique=False)`；
   `downgrade()` 先 `drop_index` 再 `drop_column`。**不改** `batch_alter_table` 的写法、不自创
   `ALTER TABLE`：SQLite 上只有 `batch` 模式才做表重建。
3. **链**：`down_revision = 'd9e4c7a15b82'`（当前 head；`alembic/versions/d9e4c7a15b82_refund_reason_columns.py:16-17`，
   且没有任何文件的 `down_revision` 指向它）。迁移条数从 15 变 16
   （`docs/下一步规划.md:30` 的「15 条迁移（head `d9e4c7a15b82`）」与 `README.md:459`、
   `CONTRIBUTING.md:69` 同读数）。
4. **up / down 都要能跑**：`upgrade()` 加列加索引、`downgrade()` 去索引去列，两条方向都要有可执行的
   证据（§5 第 8 条）。
5. **两个方言都覆盖**：SQLite 走本地 `pytest` 的 `command.upgrade` / `command.downgrade`（形状照
   `tests/test_refund_taxonomy.py:85-91` 的 `_alembic_config` 与 `:118-159` 的两条用例）；PostgreSQL 由
   CI 的 `postgres-migration` 作业跑 `upgrade head` + `check`（`.github/workflows/ci.yml:69-76`）。
6. **`alembic check` 必须无漂移**：`app/models/task.py` 的 `Task` 与迁移必须一致（CI 主作业
   `.github/workflows/ci.yml:29-30`、PG 作业 `:73-76` 都跑）。
7. **会红的地方（本轮不许为变绿改守卫清单）**：`docs/fact-baseline.yaml` 的 `alembic_revisions`
   （`:69-91`，6 条 claim）与 `alembic_head`（`:93-108`，4 条 claim），其中 `docs/spec/项目规格.md` 的两条
   是 `in_manual: true`（还要重生成说明书 docx，见 `docs/fact-baseline.yaml:69-91` 与
   `.qoder/rules/task-order.md` §3）。`sql_tables` 不受影响——只加列，表数不变。这些读数按
   `task-order.md` §4「全量基线只跑一次」统一在收尾轮 re-cite，实现轮只登记「哪几处会红」（§7.2）。

---

## 3. 值域与计数纪律

1. **kind 值域只有一个声明点**：`SpanKind`（`tracing.py:49-58`）。但注意 `add()` 的形参是 `str`
   （`tracing.py:92`），字面量**不会**被类型检查拦住——所以本轮靠 §5 的三条断言兜住，靠纪律拦不住。
   新增 kind 先加枚举值，再改调用点。
2. **`"root"` 只允许三处出现**：`SpanKind` 的成员、`finalize()` 里的字面量、以及本文件。
   `grep -rn 'add("root"' app/` 必须无命中（§2.3 第 2 条）。
3. **根 span 的 `name` 只有 `support.turn` / `task.run` / `task.approve` 三个值**（§2.3 第 3 条）。
   报表、控制台、文档都不得自行缩写或加值。
4. **span 耗时算式的唯一入口** `elapsed_ms`。判据分**两级**，两级都要满足：
   - **一级（span 耗时算式唯一）**：`grep -rn "int((time.perf_counter() - started)" app/` 的命中
     只剩 **4 处**——`app/observability/tracing.py` 的 `elapsed_ms` 函数体 1 处（`:191`）+
     `app/sql/service.py` 的三处 `duration_ms`（`:216`、`:222`、`:288`，§2.1 第 3 条明确算式保留）。
     `app/services/runtime.py` **零命中**。
   - **二级（非 span 的既有算式，登记保留）**：另有 4 处同形但**非 span 耗时**的算式，按 `文件:行`
     登记、本轮**不动**：`app/agent/analytics_narrator.py:107`、`:117`（写 `NarrationCall.latency_ms`，
     叙述调用台账）、`app/agent/pydantic_agent.py:397`、`:406`（写 `ModelTrace.latency_ms`／用量台账）。
     这 4 处是**浮点**、不经 `int(...)`，与 span 的整数毫秒是不同载体，**不得**为让「唯一实现」好看而
     把它们也收进 `elapsed_ms`。
   - **读数对照（防止把二级误判成残留）**：更宽的 `grep -rn "perf_counter() - started" app/`
     **当前 12 处**（现行 `int(...)` 式 8 处 + 二级的 4 处浮点），一级落地后应为 **8 处**（一级 4 处 + 二级 4 处）。
     该命令命中数不是 4，属**预期**，不是缺口。
5. **耗时读数不得互相加总**。四段之和**不等于**端到端：对话侧的根 span 窗口从阶段四开始（§4.8.b），
   任务侧的 `task.route` 起点在 recorder 之前，两处都不在同一个窗口里。任何报表不得输出
   「四段相加」的字段。
6. **`tool` 类不并入四段**。`analytics.query`/`analytics.prefetch`/`analytics.route`/`analytics.expand`
   都是 `tool` kind（`app/sql/service.py:238`、`:309`、`:326`、`:362` 与 `actions.py:442`、`:547`、`:564`），
   它们的读数与四段并列，不相加、不平均。

---

## 4. 边界与禁止事项

1. **不改 metrics 的百分位口径**。`GET /api/v1/traces/metrics`（`app/api/v1/traces.py:51-104`）本轮仍然
   **只有一个 p95**：计算在 `:88-92`（`rank = max(0, min(len-1, ceil(0.95*len)-1))`），输出 `p95_latency_ms`
   在 `:100`，`by_kind`（`:101`）只给**条数**。不加按 kind 的分位数、不改 `window_minutes`、不改 p95 的
   取法。本轮只是让 `latency_ms` 有更多非零值，于是 p95 的输入变宽——这是「读数变多」，不是口径变化。
2. **不改前端**。`web/src/pages/TraceViewer.tsx` 的 `buildTree`（`:23-55`）按 `parent_span_id` 分组
   （分组循环 `:26-34`，`children.get(parent)` 在 `:31`），`durationMs`（`:57-63`）优先取非零 `latency_ms`（`:58`），渲染用
   `spanKindLabel(span.kind)`（`:270`），父级显示 `PARENT {span.parent_span_id ?? 'ROOT'}`（`:305`）。
   `web/src/format.ts` 的 `SPAN_KIND_META`（`:315-324`）只有现有 8 个 kind，`spanKindLabel`（`:326-329`）
   落到 `?? kind`（`:328`），所以 `root` 会原样显示成 `root`。**本轮一行前端都不改**。
3. **不给 `approval` / `handoff` / `reconcile` 补耗时**（理由见 §1.2 排除表）。`tool` 类里
   `runtime.py::_call_tool` 的三条**已有**读数（`:1354`、`:1369`、`:1388`）不动；`app/sql/service.py`
   的三条当前**只有属性 `duration_ms`、`latency_ms` 列恒为 0**，本轮按 §2.1 第 3 条**补写
   `latency_ms=duration_ms`**——这是本轮唯一涉及 `tool` 类的改动。
4. **不回填历史行**。`tasks.conversation_id` 对既有行留 `NULL`；不得写数据迁移去猜：`tasks` 现有列是
   `request`/`context`/`result`/`trace_id`/`idempotency_key`/`request_digest`/`version`/时间戳
   （`app/models/task.py:43-55`），没有任何一列能稳定反推到对话，`trace_id` 反推要求先知道那一轮的
   `trace_id`（§7.2）。
5. **任何 span 属性不得出现客户原话**。既有事实里已经有 `retrieve/policy.search` 的
   `"query": request.message`（`actions.py:339`）——本轮**不动它，也不照这个样式扩大**；
   `generate/support.reply` 只记 `reply_chars` 与 `reply_sha256`（`support_agent.py:609-610`）、候选只记
   条数（`support_agent.py:617` 的 `"clarify_candidates"`）。新增字段一律先过这条检查：不能是原话、
   订单号、回复正文、模型提示词；并且仍然过
   `redact_payload`（`tracing.py:181`）。根 span 属性为空（§2.3 第 8 条）。
6. **不改 `evals/replay.py` 的比对规则**。`_VOLATILE_ATTRS`（`:67-86`）、`_BLOCKING_KINDS`（`:90`）、
   `_READ_ONLY_TOOLS`（`:94-101`）、`_trajectory`（`:273-300`）、`_conclusion`（`:249-254`）一个字符都
   不改。唯一允许的改动是**加一行输出**——本轮**必须加**：把「按关联键串起的步数」并进 `_replay`
   的 `print(f"replay: ...")`（现有两处，`:368-371` 与 `:415`）。格式与位置由实现者定，但**必须**
   能在 `--self-check` 的输出里读到「按关联键串起多少步」这个数（判据见 §5 第 10 条）。
   根 span 天然安全：`root` 不在 `_BLOCKING_KINDS` 里（`:90`）、不进 `tools`/`guards` 分组
   （`:282-296` 按 `kind == "tool"`/`"guard"` 过滤）、`spans` 列表（`:276`）只用 `kind/name` 拼字符串。
7. **三份守卫清单一个字节都不动**：`docs/fact-baseline.yaml`、`docs/code-anchors.yaml`、
   `docs/architecture-guard.yaml`。第二份现有 6 条 anchors（`docs/code-anchors.yaml:26-68`），**没有一条**
   登记 `app/observability/**`，本轮也不登记——登记 `tracing.py` 会让「内部实现改名」变成红。第三份
   不需要改：`observability` 是 tier 0（`docs/architecture-guard.yaml:30-42`），`sql`（tier 2）与
   `services`（tier 4）向下引入不新增边，且 `app/sql/service.py:33` 早就
   `from app.observability.tracing import TraceRecorder`。
8. **已登记的实现边界（读到的既有事实，不是裁定，但会限制实现方式）**：
   1. **对话侧一轮可能是两棵树**。`handle` 的 `support.turn`（`support_agent.py:330`）之外，转人工
      沉默路径 `_silent`（`:1013-1072`）自己又构造一个 recorder（`:1041`），唯一 span 是
      `handoff/agent.silent`（`:1045-1053`），flush 在 `:1054`。所以「每轮一个根」是**错**的验收写法；
      正确写法是「**每棵树**恰好一个根」（§5 第 3 条）。沉默路径下同一轮会有两条 `support.turn` 根
      span、两个 `trace_id`。
   2. **`support.turn` 根的窗口不覆盖模型调用**。recorder 在阶段四构造（`support_agent.py:328-333`），
      而模型咨询在 `_stage_consult_model`（`:309-311`，定义在 `:715`）。所以根的读数（构造 → finalize）
      **不得**被当成端到端耗时；移动构造点不在本轮范围（§2.1 第 5 条）。
   3. **`analytics.route` 没有耗时起点**（§2.1 第 7 条）。这是 `app/sql/service.py` 四个 tool span 里
      唯一没有 `duration_ms` 的一个，`_trace_route`（`:351-373`）的形参里也没有 `started`。
   4. **`tests/test_trace.py:89` 是精确有序等值断言**：`assert kinds == ["understand", "retrieve", "generate"]`，
      同一用例在 `:91`、`:93` 用 `spans[0]`/`spans[1]` 取属性。根排在最后（§2.3 第 7 条）只保住索引
      0/1，`:89` 的等值断言**必须**同步改成含 `root` 的读数。这是本轮**唯一**需要动的既有断言；
      实现者要逐条报告改前/改后读数，不得顺手放宽别的断言。
   5. **回放的按序兜底配对可能被 `conversation_id` 影响**。`_load_source` 给每个对话收集 trace_id
      （`evals/replay.py:145-152`），当 assistant 消息上没有 `trace_id` 时按**顺序**兜底配对
      （`:185-188`：`turn.trace_id is None and index < len(known)`）。任务树开始写 `conversation_id` 后
      `known` 会变长，这类旧对话可能按序配到任务树。该兜底只对「`trace_id` 落地之前录下的对话」生效
      （`support_agent.py:595-600` 现在会把 `trace_id` 写进消息 `payload`）。本轮不改这段代码
      （§4 第 6 条），但必须核对 `--self-check` 输出的
      `replay: N recorded conversations, M replayable, K skipped`（`:368-371`，逐 reason 的
      `skip K: reason` 行在 `:372-373`）三个数**全是本轮之前的读数**——读数与判据见 §5 第 9 条。
   6. **`evals/trace_assert.py` 不存在**。`docs/code-anchors.yaml:74-76` 把它登记为「前瞻规划、尚未创建」，
      盘上也没有这个文件。**不得**把它当现状引用，也不得据它设计断言。
9. **本轮明确不做**：不做 trace 采样、不改 `started_at`/`ended_at` 的语义（`observability.py:36-40` 的列默认值
   `utcnow` 保持不变）、不加 `p50`/分 kind 分位数、不改 `conversation_messages.payload` 的键集、不加新表、
   不加新事件、不加新 kind（除 `"root"`）、不改 `TaskResponse` 的字段集。

---

## 5. 验收标准（DoD）

每条都要有可复核的证据；「已实现」不算证据。测试用例的**函数名**由实现者定，本节只规定**哪个文件、
断言什么**。

1. **单一算法（两级判据）**：
   - **一级**：`grep -rn "int((time.perf_counter() - started)" app/` 的命中只剩 **4 处**——
     `app/observability/tracing.py` 的 `elapsed_ms` 函数体 1 处（`:191`）+
     `app/sql/service.py` 的三处 `duration_ms`（`:216`、`:222`、`:288`，§2.1 第 3 条明确算式保留）；
     `app/services/runtime.py` **零命中**。四个分段产出者的 `latency_ms` 都取自
     `tracing.elapsed_ms(...)`（§2.1 第 6 条的模块属性写法）。
   - **二级（登记保留，不得收编）**：`app/agent/analytics_narrator.py:107`、`:117` 与
     `app/agent/pydantic_agent.py:397`、`:406` 这 4 处同形算式**不是 span 耗时**（写叙述台账与模型用量
     台账，且是浮点、不经 `int(...)`），本轮不动。更宽的 `grep -rn "perf_counter() - started" app/`
     **当前 12 处、一级落地后 8 处**（一级 4 + 二级 4），这个数不是 4 属预期（§3 第 4 条）。
   **`analytics.route` 的处理必须显式**：要么四个调用点都拿到真实起点后写读数，要么在交付说明里
   登记为缺口——**不得**留一个 0 又不说明（§2.1 第 7 条）。
2. **四段有耗时（结构性 + 打桩，覆盖全部产出者）**：写在 `tests/test_trace.py`（或与它同源的 trace 用例
   文件），断言形状是——把 `app.observability.tracing.elapsed_ms` 打桩成返回固定非零值（例如 7），
   跑能依次产出下列 span 的样本，断言这些 span 的 `latency_ms` **全部等于 7**：
   - `understand/support.classify`、`retrieve/policy.search`、`generate/support.reply`：
     一条 POLICY_QA 对话即可；
   - `guard/output.grounded`：样本必须让护栏**真的改动过草稿**（`_guard_output` 在
     `draft.clarification is not None` 与 `not result.changed` 两处提前返回，`support_agent.py:924-925`、
     `:928-929`），否则这条 span 根本不产出；
   - `generate/memory.personalize`：带档案且 `memory_personalization_enabled` 打开的样本（`_personalize`
     在开关关闭、无档案、`not result.applied` 三处提前返回，`:984`、`:987`、`:996`）；
   - `generate/analytics.narrate` 与 `guard/analytics.faithfulness`：一条走 PREFETCH 的统计问题样本
     （`actions.py::_prefetch_answer`，`narrate(...)` 在 `:473`）；
   - `understand/task.route`：一条提交到任务的样本（`runtime.py:436`）。
   打桩能抓住「产出者忘了传 `latency_ms`」——那种情况下它是 0，而不是 7。**不得**用真实耗时做这条断言
   （§2.2 第 2 条）。
3. **根 span 结构**：从 `GET /api/v1/traces/{trace_id}` 读回后按 `parent_span_id` 建树，断言：
   - `kind == "root"` 的 span **恰好一条**；
   - 根的 `parent_span_id` 为 `None`；
   - 除根外每条 span 的 `parent_span_id` 非空，且指向同一棵树里存在的 span（不是自己）；
   - 至少要覆盖三种树：一条 POLICY_QA 对话（`support.turn`）、一次退款任务（`task.run`）、一次审批
     （`task.approve`，`runtime.py:897` 那棵树）。
   **不要**写成「每轮恰好一个根」——沉默路径下同一轮有两棵树（§4.8.a）。
4. **根 `name` 只有三个值**：断言读到的 `root` 的 `name` ∈ {`support.turn`, `task.run`, `task.approve`}。
5. **`finalize` 幂等**：对同一个 recorder 连调两次 `finalize()` / `flush()`，第二次后 `len(spans)` 不变、
   根仍只有一条、已有 `parent_span_id` 不变。
6. **关联键三条路径都要断言**：
   - 对话侧建单（退款走 `actions.py::_create_task`）→ `tasks.conversation_id` 等于当轮
     `conversations.id`；
   - 直接 `POST /api/v1/tasks`（`app/api/v1/tasks.py:24-47`）→ `tasks.conversation_id is NULL`；
   - 同一幂等键重试（`POST /api/v1/tasks` 带 `Idempotency-Key`）→ 返回第一个任务、任务表只有一行
     （唯一约束 `uq_tasks_idempotency`，`app/models/task.py:30-32`），且 `request_digest` 与第一次相同
     ——这条同时证明 §2.4 第 4 条（键没进 `context`）。
7. **两棵树共用一个键**：一次对话触发建单后，按 `tasks.conversation_id` 查 `trace_spans`
   （`TraceRepository.list_for_conversation`，`traces.py:77-87`），断言该对话下**同时**有
   `root/support.turn` 与 `root/task.run` 两棵树，且两棵树的 `trace_id` 不同。这条就是 R9 原文
   「对话树与任务树有共同关联键」的落地判据。
8. **迁移双向可行**：在空库上跑通 `upgrade` 与 `downgrade`（形状照 `tests/test_refund_taxonomy.py:118-159`
   的两条用例）。断言：迁移后 `tasks` 有可空的 `conversation_id` 列、`sa_inspect(...).get_indexes("tasks")`
   里有 `ix_tasks_conversation_id`；历史行的 `conversation_id` 仍是 `NULL`（不得回填）；`downgrade("-1")`
   后列与索引都消失。
9. **回放不回退（两组读数逐字相同）**：`evals/replay.py --self-check` 仍打出 `REPLAY DIFF EMPTY`
   （`:443`），且下面两组输出与本轮之前**逐字相同**：
   - `replay: N recorded conversations, M replayable, K skipped`（`:368-371`）——**recorded / replayable /
     skipped 三个数**，以及随后逐 reason 的 `skip K: reason` 行（`:372-373`）。这三个数是 §4.8.e 那条
     核对要求的读数来源。
   - `replay: N conversations, M diff lines`（`:415`）的 N 与 M，M=0。
   若用 `--from` 指向本轮之前录下的 trace 做比对，`_trajectory` 的 `spans` 列表（`:276`）会差一条
   `root/<name>`——这是 §7.3 登记的代价，**不得**靠改 `_trajectory` 或往 `_VOLATILE_ATTRS` 加 `root` 抹平。
10. **回放产出按关联键串起一步（R9-d）**：断言对象是 **`evals/replay.py` 的产出**，不是 `trace_spans`
    表本身（后者由第 7 条覆盖）。复用第 7 条的样本（一次对话触发建单）跑 `--self-check`，断言：
    - 该 `conversation_id` 名下挂到的 trace 数 **≥ 2**；
    - 其中**至少一条来自任务树**（即 `root/task.run` 的那条 `trace_id`），另有一条是对话树
      （`root/support.turn`）——「对话树的步」与「任务树的步」被同一个关联键串到同一个对话下；
    - 串起的步数必须是 `--self-check` 输出里**可直接读到并可复核**的一个数（实现者定格式，但**不得**
      只打印不计数、**不得**靠人工从库里反查）。
    同时断言 `--self-check` 的 recorded / replayable / skipped 三个数与本轮之前**逐字相同**
    （读数位置见第 9 条第一组）。
    **不得**靠改 `_trajectory` / `_VOLATILE_ATTRS` / `_BLOCKING_KINDS` 实现，只允许 §4 第 6 条那
    「加一行输出」。
11. **metrics 口径未变**：`GET /api/v1/traces/metrics` 的响应键集与改前逐字相同
    （`window_minutes`、`trace_count`、`spans`、`error_spans`、`error_rate`、`p95_latency_ms`、`by_kind`、
    `by_status`、`by_hour`，见 `app/api/v1/traces.py:94-104`）；`p95_latency_ms` 的取法仍是 `:91` 那一个
    全量 p95。`by_kind` 里出现 `root` 计数属于读数变多，不是口径变化。
12. **前端零改动且不回归**：`npm run build` 通过；`root` span 在控制台渲染为 `root`
    （`web/src/format.ts:328` 的 `?? kind` 兜底）、父级显示 `ROOT`（`TraceViewer.tsx:305`）。前端源码
    一个字节都不改（§4 第 2 条）。
13. **既有断言变更逐条报告**：`tests/test_trace.py:89` 的等值断言已按 §4.8.d 更新，交付说明里给出
    改前/改后读数；除此之外没有别的既有断言被动过。
14. **不许真耗时断言**：交付里不出现 `assert latency_ms > 0`，也不用 `time.sleep` 制造读数（§2.2 第 2 条）。
15. **门禁与文档读数**：`pytest` 全绿（收集数变化时按 `task-order.md` §3 处理说明书重生成）、
    `ruff check`、`python -m mypy app`、三份守卫全绿。迁移读数 15 → 16 会让
    `docs/fact-baseline.yaml` 的 `alembic_revisions` / `alembic_head` 变红（§2.5 第 7 条）：实现轮**只登记**
    「哪几处会红」，按 `task-order.md` §4 在收尾轮统一 re-cite，**不得**为变绿去改守卫清单。

---

## 6. 与来源的对齐

| 来源 | 原文要点 | 本文件对应 |
|---|---|---|
| `docs/下一步规划.md` §0.2 第 72 行 R9 行「做什么」列 | 四类 span 补耗时、补根 span、两表关联 | §1.2（四段枚举）、§2.1、§2.3、§2.4 |
| 同一行「现状」列 | 分位数只覆盖工具 span，路由与生成段无耗时；对话树与任务树在库里没有关联键 | §1.2 排除表（`tool` 已有时耗，`runtime.py:1303-1395`）、§1.2 现状列（路由/生成段为 0）、§2.4 第 5 条 |
| 同一行「验收」列 | 路由与生成段有 span 耗时、有根 span；两棵树有共同关联键；`evals/replay.py` 的产出能按该键串起一步 | §5 第 2、3、7、9、10 条（「按该键串起一步」单列在第 10 条） |
| `docs/下一步规划.md` §14.6 第 2648 行 R9 段 | 「分位数只覆盖工具 span，路由与生成两段没有读数……补四类 span 的耗时、补一个根 span，并给两棵树一个关联键——现在两棵树在库里串不起来」 | §1.2、§2.1、§2.3、§2.4；「串不起来」的机制解释在 §2.4 第 5 条（span 侧已有列、任务侧缺列） |
| `docs/下一步规划.md:30`「当前规模事实」 | 26 张表，15 条迁移（head `d9e4c7a15b82`） | §2.5 第 3、7 条、§7.2 |
| `README.md:459` / `CONTRIBUTING.md:69` | 迁移 15 条、head `d9e4c7a15b82` | §2.5 第 7 条、§7.2 |
| 组织分派简报的六条裁定 | 四段枚举、根 span 与三个入口、关联键取 `tasks.conversation_id`、耗时整数毫秒且单一实现、metrics 口径不变、前端不变 | §1.2、§2.3、§2.4、§2.1、§4 第 1 条、§4 第 2 条；三处需另行登记的判断见 §7 |

> 「四段取 `understand`/`retrieve`/`generate`/`guard`」「关联键取 `tasks.conversation_id`」「根 span 的代价
> 由历史 trace 承担」这三条是**裁定**，不是 §0.2/§14 原文（原文没有枚举、没有指定列、没有说历史数据
> 怎么办）。逐条见 §7。

---

## 7. 三处裁定与代价登记

> 下面三条**不是** `docs/下一步规划.md` 的原文要求。原文只写「四类 span 补耗时、补根 span、两表关联」。
> 实现时不得把它们读成原文，也不得据此扩大范围。

### 7.1 裁定一：四类 span 取 `understand` / `retrieve` / `generate` / `guard`

- **为什么是这四类**：R9 的验收句只点名「路由与生成段有 span 耗时」。可核的边界是 §0.2 现状列点出的
  两段（路由 = `understand`、生成 = `generate`），再加检索与护栏两类才构成一条可读的请求链。
  只补两段的话，`retrieve` 与 `guard` 仍恒为 0，而同一条链上的 0 会被读成「这两段不耗时」。
- **排除项与理由**：`tool` 已有耗时（`runtime.py::_call_tool` `:1303-1395`）；`approval`/`handoff`/`reconcile`
  是事务与记账记录，不是可独立变慢的段（§1.2 排除表、§4 第 3 条）；`root` 是新加的根本身。
- **影响面（blast radius）**：
  - `SpanKind` 从 8 个值变 9 个，只加 `"root"`（`tracing.py:49-58`）；
  - 新增一个模块级函数 `elapsed_ms`，`_elapsed_ms` 改为委托（`tracing.py:189-191`）；
  - `app/services/runtime.py` 四处手写同式改调用（`:1354`、`:1369`、`:1388`、`:1426`）；
  - `app/sql/service.py` 三个 tool span 各增写一个 `latency_ms=duration_ms` 实参（复用同行已算好的变量，
    `:247`、`:318`、`:335`），`duration_ms` 属性保留不动（§2.1 第 3 条）；
  - 八个分段产出点各取一次起点并传 `latency_ms`（§1.2 现状列的八行，`understand` 两条、`retrieve` 一条、
    `generate` 三条、`guard` 两条非 `tool.degraded` 的）；
  - `TraceRecorder.__init__` 增加一个根名形参（默认值留空即不生根），四个构造点各传一个值：
    `support_agent.py:330`（`support.turn`）、`support_agent.py:1041`（`support.turn`）、
    `runtime.py:402`（`task.run`）、`runtime.py:897`（`task.approve`）；
  - 新增 `TraceRecorder.finalize()`，调用点**只有一处**：`TraceRepository.flush`
    （`app/repositories/traces.py:51-66`）在写库之前调一次，别处不得调（§2.3 第 2 条）；
  - `tests/test_trace.py:89` 的等值断言要改（§4.8.d）；
  - `evals/support_run.py` **不受影响**：数据集里的 `spans` 是**子集**断言（`evals/support_run.py:201-206`
    只查 `expect.spans` 里的 key 在不在、`span_absent` 里有没有出现），加根 span 不会让任何用例失败；
    现有 `span_absent` 只有 `evals/memory_personalization_set.json:69-71` 一处，列的是
    `generate/memory.personalize`。

### 7.2 裁定二：关联键取 `tasks.conversation_id`（新增列 + 一条迁移）

- **为什么不是反查 `tasks.trace_id`**：反查要求「建单那一刻就知道这一轮的 `trace_id`」。今天
  `_create_task`（`actions.py:396-402`）手上只有 `request.conversation_id`；`trace_id` 是 recorder 在
  阶段四才铸的（`support_agent.py:330`），而建单发生在动作层更后面。要用 `trace_id` 当键，就得在
  `tasks` 上再存一遍 trace 并回填，改动面更大，还多出一个真源。
- **为什么禁止进 `task.context`**：`request_digest = canonical_digest({"request": request, "context": context or {}})`
  （`runtime.py:311-315`）。把键塞进 `context` 会改摘要，摘要不等即**显式 `ConflictError`**（`runtime.py:328-331`），
  `:332` 才是 `return self._replay(identity, existing_id)`；于是同一个幂等键的重试从「返回第一个任务」变成 409。
  唯一约束 `uq_tasks_idempotency`（`app/models/task.py:30-32`）**只在并发竞态里当仲裁者**——
  `create_task` 的 docstring（`:302-309`）写得很清楚：`The unique constraint is the arbiter under a race;
  the lookup here only saves the common sequential retry a write.` 键只能走列。
- **代价登记**：
  - 新增一列 + 一条迁移，迁移条数 15 → 16，head 变成新号（`down_revision = 'd9e4c7a15b82'`）；
  - `docs/fact-baseline.yaml` 的 `alembic_revisions`（`:69-91`，6 条 claim）与 `alembic_head`
    （`:93-108`，4 条 claim）会红；其中 `docs/spec/项目规格.md` 的两条是 `in_manual: true`，意味着
    还要重生成说明书 docx（`task-order.md` §3）；
  - `sql_tables` claim 组不受影响（只加列，表数不变）；
  - **不许**为变绿去改这三份守卫清单（同 `adoption-surface.md` §3 第 4 条）；
  - 按 `task-order.md` §4「全量基线只跑一次」，这些读数统一在收尾轮 re-cite，实现轮只登记「哪几处会红」；
  - 历史行留 `NULL`、不回填（§4 第 4 条），所以「两表关联」的覆盖率从本列上线那一刻才开始算，
    任何报表不得把历史行计成「未关联」的缺陷。

### 7.3 裁定三：根 span 的代价由历史 trace 承担（kind/name 序列整体后移一位）

- **新录对新放**：`--self-check` 两侧都是本次现录（`evals/replay.py::_generate_source` 先跑一遍数据集
  再读库，`def` 在 `evals/replay.py:419`），两侧都有根 span，`_trajectory` 的 `spans` 列表（`:276`）逐字相同 →
  `REPLAY DIFF EMPTY`（`:443`）不变，`_conclusion`（`:249-254`）也不受影响（它只读 `support.reply` 的属性）。
- **旧记录对新录**：拿本轮之前录下的 trace 当基线时，recorded 侧少一条 `root/<name>`，`spans` 列表会
  报一条 TRAJECTORY 差异。这是**可接受的**（旧记录本来就不含新字段），并且**不得**靠改 `_trajectory`
  或往 `_VOLATILE_ATTRS` 里加 `root` 来抹平——那会让回放守卫失去意义（§4 第 6 条）。
- **代价上限**：一条差异行（`spans` 列表长度差一位），不是逐 span 的 N 条差异；`tools`/`guards` 分组
  （`:282-296` 按 kind 过滤）不受影响。
- **另一处代价**：`tests/test_trace.py:89` 的精确等值断言必改（§4.8.d）。这是本轮唯一的既有断言变更，
  必须逐条报告改前/改后读数。
- **不承担的代价**：不改 `evals/replay.py` 的比对规则、不改 `--self-check` 的判定、不重录历史 trace、
  不改 `evals/support_run.py` 的断言形状。
