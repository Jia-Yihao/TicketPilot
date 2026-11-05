# 贡献指南

感谢有兴趣改进 TicketPilot。这个项目是**工程作品集与教学向演示**，
它的价值在于把“Agent 系统里哪些部分必须是确定性代码”讲清楚，
所以改动前请先读 [docs/guide/范围与边界.md](docs/guide/范围与边界.md) 与 [docs/guide/架构说明.md](docs/guide/架构说明.md)。

## 行为准则

就三条：评论针对代码不针对人；不确定就写“我不确定”，不要编；
文档里的每个数字都要能跑出来。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate          # PowerShell: .venv\Scripts\Activate.ps1；Git Bash: source .venv/Scripts/activate
pip install -e ".[dev]"

cd web && npm ci && cd ..          # 控制台（可选，只做后端改动可跳过）

python scripts/seed.py             # 建表 + 演示数据（默认 SQLite）
uvicorn app.asgi:app --reload --port 8000
```

## 提交前必须通过

```bash
ruff check app tests scripts evals alembic
python -m mypy app                        # strict 模式，零错误（154 个文件）
python -m pytest -q                     # 收集 1382 条、90 个测试文件（2026-10-28 实测 1074 passed / 6 skipped / 0 failed；当前读数见 `docs/spec/项目规格.md` §19.1；跳过的 6 条需要 PostgreSQL 或 Redis）
python evals/run.py                       # 40/40 任务路径 Golden Set
python evals/support_run.py               # 62/62 只读路径（含多轮澄清）
python evals/clarification_candidates.py  # 澄清降级前后对比（同一批 5 段剧本跑旧预算=2 / 新预算=3）
python evals/support_run.py --dataset evals/analytics_dimension_set.json --partial   # 3/3 统计维度拒绝
python scripts/build_intent_set.py --check   # 346 条意图语料可复现
python scripts/manage_intent_gaps.py report --cycle 2026-10-26   # 缺口队列计数；分层对比加 --before / --after
python evals/intent_metrics.py --enforce     # 规则覆盖、误判代价与缺口不变量
python evals/intent_candidates.py            # 意图候选集离线证据（不调模型；--with-model 才问端点）
python evals/retrieval_metrics.py         # 检索/路由评测基线，门槛全过
python evals/retrieval_metrics.py --dataset evals/retrieval_set.json   # 分层 1k 集（200 金 + 800 银）
python scripts/build_retrieval_set.py --check   # 分层集可复现性
python evals/replay.py --self-check       # 回放自检：diff 为空
python evals/metrics_baseline.py --check  # 基线门禁：五个离线评测源的 395 条确定性读数与 evals/baseline/ 逐键相等（默认零容差）
python -m alembic check                   # 模型与迁移无漂移
cd web && npm run build                   # 控制台类型检查 + 构建
```

这份清单覆盖 `.github/workflows/ci.yml` 主 job 的检查项；CI 另有 `alembic upgrade head`
建库步骤，并把检索指标显式限定为 `--channels bm25,hybrid`。分层集指标、分层集复现、
澄清降级剧本对比、指标基线门禁与控制台构建是本地附加检查（CI 的 web job 单独跑构建；剧本自身的前后对比
不进 CI 门禁，但它的形状与读数由 `tests/test_clarification_evidence.py` 的 11 条用例钉住）；
Postgres job 只验迁移链，不跑 pytest。

只有需要真实 PostgreSQL（并发锁与 Outbox 认领竞态）或真实 Redis（跨进程共享配额）的用例才要指过去；指过去之后它们会真的执行，不再是 skip：

```bash
TICKETPILOT_TEST_POSTGRES_URL=postgresql+psycopg://... python -m pytest   # 2026-10-25（519 条时代）：517 passed / 2 skipped（153.64 s）
TICKETPILOT_TEST_REDIS_URL=redis://127.0.0.1:56379/0 python -m pytest tests/test_rate_limit_redis.py   # 2 passed
# 两个容器同时指上：全量一条不跳
TICKETPILOT_TEST_POSTGRES_URL=postgresql+psycopg://... TICKETPILOT_TEST_REDIS_URL=redis://127.0.0.1:56379/0 python -m pytest   # 2026-10-25（519 条时代）：519 passed / 0 skipped（137.23 s）
```

这些用例会临时创建数据库、跑完删除，不能指向共享库。

> **Postgres 覆盖范围（2026-10-25）**：T2 起新建的测试文件与四张新表已经在 PostgreSQL 16.15 容器上跑过：
> 全量 **517 passed / 2 skipped**，跳过的 2 条是 Redis 用例（把 `TICKETPILOT_TEST_REDIS_URL` 也指上后是 **519 passed / 0 skipped**，137.23 s），
> 当时是 13 条迁移（head `b4e8d2f6a1c9`）；第 14 条 `a1c7e9d4b820`（P4）没在那次手工全量里，
> 但已在运行中的 PostgreSQL 16 容器上单独验过 `upgrade head` + `check`（见 `docs/guide/证据索引.md` 的 P4 一节）与 CI 的 `postgres-migration` 作业。
> 当前迁移链 **16 条**（head `ea6c2b48d701`），本地 `alembic check` → `No new upgrade operations detected.`；
> 默认 `pytest` 当前收集 1382 条（2026-10-28 实测：1074 passed / 6 skipped / 0 failed；当前读数见 `docs/spec/项目规格.md` §19.1），它与 PG 历史读数不要混写；
> **查询改写、分层检索集、意图语料评测、意图缺口闭环、P2/P3/P6、预取式统计、P4 规则治理、P11 澄清候选降级与 P8 意图候选化新增的 347 条，加渠道可用性轮 19 条，再加 B-1/B-3 落地与后续各轮（收尾返工轮、R1/R3 端到端能力、R4 指标基线、R5 答案层、R7 采纳面收口）新增的 194 条（未逐轮拆分归因），共 560 条目前只有 SQLite 证据**，尚未在 PG 上复跑。
> **检索指标脚本本身也在 PG 上跑过**（`retrieval_metrics.py --channels bm25,dense,hybrid,gated,rerank`
> 在 `tp_eval` 库上跑出五行读数，与 SQLite 逐位一致）。
> 这次能一致，前提是修掉了一个可复现性缺陷：`PolicySearchService.visible_chunks()` 原本没有 `ORDER BY`，
> 行序随数据库而变 → BM25 同分行的次序变 → 重排候选集变 → 同一个语料在两个库上排出不同名次。
> 现在它按块 ID 排序，`app/retrieval/search.py` 的 `visible_chunks()` 是这轮唯一的行为改动。

CI 会跑 ruff、mypy、后端测试、Golden Set、Alembic 迁移与 `alembic check`、以及控制台构建，
见 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

### 门禁口径对照（命令 × 出现在哪几处）

口径的权威定义是本文件「提交前必须跑」三条；本地 Makefile 与 CI 只向它对齐
「说法」，不合并命令。三处之间的差异全部点名：**本地必跑 = 三条 / CI 额外 = evals 家族 /
显式命令 = `lint-bench`、双库 diff**——不存在第四套说法。

| 命令 | 本地 Makefile | CI（`.github/workflows/ci.yml` 主 job） | 文档（本文件） | 差异与一句话理由 |
|---|---|---|---|---|
| `python -m pytest -q` | `make test` | `- run: python -m pytest -q` | 「提交前必须通过」清单 | 本次把 CI 与本文件原先的裸 `python -m pytest` 对齐到权威串：`-q` 只改输出详略，不改通过/失败判定，也不改收集/passed 读数 |
| `ruff check app tests scripts evals alembic` | `make lint` | 同串步骤 | 同串清单行 | 三处原本就逐字相同、保持不动；`bench` 面的 lint 走下方显式命令 `lint-bench`，不改这串主命令行（`evals/`） |
| `python -m mypy app` | `make mypy`（本次补上的等价目标） | 同串步骤 | 同串清单行 | 原先三处缺 Makefile 一环，本次补目标使三处逐字相同；范围仍只覆盖 `app`、不扩面（同规则 §2.3） |
| evals 家族：`python evals/run.py`、`python evals/support_run.py`（含 analytics/memory 两个 `--dataset … --partial` 变体）、`python evals/retrieval_metrics.py --channels bm25,hybrid`、`python scripts/build_intent_set.py --check`、`python evals/intent_metrics.py --enforce`、`python evals/replay.py --self-check`、`python evals/answer_metrics.py --enforce` | 拆在 `eval`、`eval-support`、`eval-dimension`、`eval-memory`、`eval-intent`、`eval-retrieval`、`eval-replay`、`eval-answer` 各目标，按需单跑 | 主 job 在三条之外额外执行这几步 | 上方门禁清单内逐条列出（本地附加检查） | **CI 额外**：权威三条不含 evals；CI 多跑这几步是额外门禁，本次既不并入三条、也不从 CI 删除 |
| `lint-bench`（`ruff check bench`） | **缺目标——缺口登记**，本次不自行新增 | 不跑 | 不列 | **显式命令**：`bench/` 建立时按 `evals/` 走独立 Makefile 目标 + CI 独立步骤；三处目前都没有这条命令的说法，属已登记待落地项 |
| 双库 diff 与回放自检：`python evals/retrieval_metrics.py --database-url <PG>` 与默认 SQLite 各跑一遍对照同读数；`python evals/replay.py --self-check`（通过读数 `REPLAY DIFF EMPTY`） | `make eval-replay` 覆盖回放自检；双库对照无目标（手工显式命令） | 主 job 只跑 `python evals/replay.py --self-check`，不跑双库对照 | 上方清单含 `python evals/replay.py --self-check` 一行 | **显式命令**：这是 `evals/` 点名的两条回归判据，不是提交前必跑的第四条常驻命令，故不并入三条 |
| 涉及迁移时：`python -m alembic check` | `make migrate`（只 `alembic upgrade head`，不含 `check`） | 主 job 收尾两步 `upgrade head` + `check`（SQLite），另有 `postgres-migration` job 只验 PG 迁移 | 清单含 `python -m alembic check` 一行 | 权威口径写的是「涉及迁移时加」，属条件性附加而非三条常驻，差异点名保留、不强行拉平 |

> 说明：表内「本次」指门禁口径归一这一次；除标注「本次补/对齐」的两行外，各命令串此前已逐字一致。

## 两个守卫：架构与文档

上面这份清单里已经包含两个守卫——它们是 `pytest` 里的普通用例，所以 CI 的主 job 会自动跑到。
想单独跑一遍（改完分层或改完文档数字时用）：

```bash
make guard
# 没装 make（Windows 上常见）就直接跑：
python -m pytest tests/test_architecture_guard.py tests/test_docs_consistency.py
```

- **架构依赖守卫**：`tests/test_architecture_guard.py`，规则源是 `docs/architecture-guard.yaml`。
  改了 `app/` 的分层依赖方向（新增一层目录、让下层反过来依赖上层、引入循环导入），
  **先改 `docs/architecture-guard.yaml` 并走 ADR**，再改代码；直接改代码会被守卫挡下。
  每条例外都必须写 `why`，守卫会检查「登记过的例外是否还在用」——删了违规代码却不清理例外，同样会红。
- **文档一致性守卫**：`tests/test_docs_consistency.py`。
  它核对两件事：OpenAPI 的每个 `(方法, 路径)` 在 `docs/guide/接口说明.md` 里都有落点；
  `.env.example` 的每个赋值都能映射到一个 `Settings` 字段。
  加了端点不写文档、或加了配置项不登记，守卫直接红。

失败信息都带 `文件:行`，直接改那一行。

守卫自己也有反例：`scripts/guard_negative_check.py` 在临时副本上造六处破坏（未声明的层、
反向依赖、`forbidden` 边、模块级环、登记腐化、配置项无对应字段），逐处断言对应的用例会红，
还原后断言会绿——它不碰仓库里的任何文件。改完守卫本身（规则或用例）之后跑一遍：

```bash
make guard-negative
```

## 目录约定

```text
app/api/v1/        HTTP 层：只做校验、依赖注入、错误信封，不写业务规则
app/schemas/       请求与响应模型（pydantic）
app/agent/         意图路由与有界语言模型节点；不得触碰权限、金额与状态机
app/services/      业务规则与状态机（资格判断、审批、台账、对账、消息队列、记忆与主动规则）
app/tools/         工具声明 + Tool Gateway + 执行器（超时/重试/熔断/降级/回执）
app/channels/      渠道抽象：入站适配器、渲染能力矩阵、出站投递（ProactiveSender）
app/sql/           受控 Text-to-SQL：计划 → AST → 守卫 → 只读执行
app/core/          配置、策略引擎、身份与错误信封、输入/输出护栏
app/models/        SQLAlchemy 模型
app/db/            会话与引擎装配
app/repositories/  数据访问，所有查询必须带 tenant_id
app/retrieval/     政策摄取、切块、BM25 检索与引用
app/observability/ 链路追踪与脱敏
app/resources/     随包分发的演示数据（政策 md、商品 catalog.json）
web/src/           React 控制台；设计令牌集中在 styles.css 的 :root
docs/guide/        使用与实现：架构、接口、边界、评测怎么跑
docs/spec/         契约：项目规格
docs/文档地图.md    文档入口：每份文档读法、唯一事实源表
docs/adr/          架构决策记录
docs/architecture-guard.yaml  架构依赖守卫的规则源
tests/             单元 / 集成 / 端到端 / 安全用例
evals/             Golden Set、只读评测集、检索指标与回放脚本
```

### 代码注释与 docstring

模块级 docstring 与公开函数的 docstring 写**简体中文**，讲「这个模块负责什么、边界在哪、谁不该依赖它」；
标识符、日志文案、span 名、事件类型、错误码一律保持英文原样。行内注释只在解释「为什么」时写，
不复述代码在做什么。

存量还有一部分英文模块 docstring，不强求一次性改完：随触点（改到那个文件时）转译，避免制造大批噪声 diff。

## 加一条演示数据

- **商品**：编辑 `app/resources/catalog/catalog.json`。每个商品必须给
  `sections`（`id` / `title` / `body`），`id` 会直接作为按需加载接口的路径参数，
  改 `id` 等于改公开契约。
- **政策**：在 `app/resources/policies/` 新增 Markdown，文件名带版本；
  `scripts/seed.py` 会摄取并按 `effective_to` 过滤，保留一份已过期旧版用于验证过滤。
- 两类数据都只写演示值，不要放任何真实客户、订单或密钥。

## 加一个接口

1. 先在 `docs/guide/接口说明.md` 写契约：方法、路径、身份要求、错误码、是否跨租户可读。
2. 在 `app/schemas/` 定义响应模型，在 `app/api/v1/` 装配；业务规则放 `app/services/`。
3. 跨租户访问一律 `404`，不要 `403`。
4. 覆盖三类测试：正常路径、越权/跨租户、并发或重复提交（若涉及写）。
5. 前端要用的话，同步更新 `web/src/types.ts` 与 `web/src/api.ts`。

## 变更需要 ADR 的情况

改动状态机、审批语义、幂等/对账策略、身份模型或对外 API 契约时，
请在 `docs/adr/` 追加一条 ADR（编号递增，含背景/决策/后果/被否决方案），
并在 PR 描述里链接它。纯文案、样式与测试补充不需要。

编号规则：`docs/adr/` 当前 **39 篇**（ADR-001…ADR-011、ADR-013…ADR-040），
**ADR-012 是预留号位**（策略外置与阈值灰度），**不回收、不重排**。
即使中间号位空着，新 ADR 也要接在当前最大号之后。

## 截图与文档

控制台视觉有变化时请重拍受影响截图（`docs/assets/screenshots/`），
README 只引用该目录下的图片。文档里的测试数量、端点数量必须与实际命令输出一致。

改动**会影响回复文本的生成路径**（个性化效果、输出护栏、模板措辞）时，除了
`docs/guide/接口说明.md` 的响应示例，还要同步 `README.md` 的对照段、`docs/spec/项目规格.md` §6.7
与前端 `web/src/pages/ConversationPage.tsx` 里的原因码标签表；并在 `docs/guide/评测说明.md`
确认回放口径：新增一种会让回复变化的效果，等于让已有档案客户的历史 trace 不再逐字可回放。

改动**会打开或改变主动规则的写面**（`PUT /api/v1/memory/rules/{event_type}`）时，
这条同步清单更长，漏一条就会出现「文档说只能改种子数据，代码却能改」的漂移：

1. `docs/guide/接口说明.md`——提交（202）与批准（200）两段契约、每个错误码的形状、`details` 回显内容。
2. `docs/guide/架构说明.md`——「主动规则的治理面」一节：提交 → 复核 → 生效的写路径、版本快照、回滚、诚实边界。
3. `docs/guide/安全说明.md`——**可写字段与审批的信任边界**：`requires_confirmation` 不可写、
   事件类型由代码定、发起人不能批准自己的提案、审计只存摘要、改动不回溯。
4. `docs/spec/项目规格.md`——§12.9 的写面块、§9.1 的接口表与 §19.1/§19.2 的实测读数、漂移表。
5. `docs/guide/范围与边界.md` 的「不做」侧与 `docs/guide/评测说明.md` 的「诚实边界」侧——写路径进不了评测集，
   要写清楚原因，不要让读数看起来覆盖了它。
6. `README.md` 与本文件的门禁读数。
7. 前端 `web/src/pages/ProactiveRules.tsx` + `web/src/api.ts` + `web/src/types.ts`，
   以及 `web/src/App.tsx` 的路由与角色门禁（`/rules` 只对 `support` / `admin` 可见；
   批准 / 驳回按钮只对 `approver` 且非提交人显示）。
8. 规则文案现在**需要另一位审批人**（四眼）、有不可变版本表与一键回滚；仍然**没有**
   删除规则、按租户灰度、定时生效或对已生成通知的回溯改写——这些是 ADR-031 明确不做的，
   任何文档都不能把它们写成已具备。

## 提交与 PR

- 分支名 `feat/…`、`fix/…`、`docs/…`。
- Commit message 用祈使句说明“为什么”，一次提交只做一件事。
- PR 模板会要求填写：验证方式（跑了哪些命令、看了哪个页面）与未验证边界。
  **不清楚是否真的可行的写法，请写进“未验证”，不要删。**
- 不要提交 `.env`、数据库文件、`web/dist`、`.tmp/`。
