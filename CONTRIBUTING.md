# 贡献指南

感谢有兴趣改进 TicketPilot。这个项目是**工程作品集与教学向演示**，
它的价值在于把“Agent 系统里哪些部分必须是确定性代码”讲清楚，
所以改动前请先读 [docs/SCOPE.md](docs/SCOPE.md) 与 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

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
python -m mypy app                        # strict 模式，零错误（134 个文件）
python -m pytest                          # 455 passed, 3 skipped（收集 458、34 个测试文件；跳过的 3 条需要 PostgreSQL）
python evals/run.py                       # 40/40 任务路径 Golden Set
python evals/support_run.py               # 61/61 只读路径（含多轮澄清）
python evals/retrieval_metrics.py         # 检索/路由评测基线，门槛全过
python evals/replay.py --self-check       # 回放自检：diff 为空
python -m alembic check                   # 模型与迁移无漂移
cd web && npm run build                   # 控制台类型检查 + 构建
```

这九条与 `.github/workflows/ci.yml` 的主 job 一一对应；CI 的 Postgres job 只验迁移链，不跑 pytest。

只有需要真实 PostgreSQL 的用例（并发锁与 Outbox 认领竞态）才要指过去；指过去之后它们会真的执行，不再是 3 个 skip：

```bash
TICKETPILOT_TEST_POSTGRES_URL=postgresql+psycopg://... python -m pytest   # 2026-10-25：458 passed / 0 skipped（109.92 s）
```

这些用例会临时创建数据库、跑完删除，不能指向共享库。

> **Postgres 覆盖范围（2026-10-25）**：T2 起新建的 10 个测试文件（148 条，含 `test_personalization.py` 18 条、
> `test_proactive_rules.py` 21 条、`test_llm_gap.py` 7 条）与四张新表已经在 PostgreSQL 16.15 容器上跑过：
> 全量 **458 passed / 0 skipped**，同一容器上 Golden **40/40**、支持集 **61/61**、11 条迁移 + `alembic check` 无漂移。
> 默认 `pytest` 的 455 passed / 3 skipped 是 SQLite 基线，两个读数不要混写。
> **仍未在 PG 上跑**的是四通道检索指标脚本（`retrieval_metrics.py` 的 dense / hybrid / rerank 三列），
> PG 侧只有 `test_retrieval_eval.py` 的门槛断言。

CI 会跑 ruff、mypy、后端测试、Golden Set、Alembic 迁移与 `alembic check`、以及控制台构建，
见 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

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
docs/adr/          架构决策记录
tests/             单元 / 集成 / 端到端 / 安全用例
evals/             Golden Set、只读评测集、检索指标与回放脚本
```

## 加一条演示数据

- **商品**：编辑 `app/resources/catalog/catalog.json`。每个商品必须给
  `sections`（`id` / `title` / `body`），`id` 会直接作为按需加载接口的路径参数，
  改 `id` 等于改公开契约。
- **政策**：在 `app/resources/policies/` 新增 Markdown，文件名带版本；
  `scripts/seed.py` 会摄取并按 `effective_to` 过滤，保留一份已过期旧版用于验证过滤。
- 两类数据都只写演示值，不要放任何真实客户、订单或密钥。

## 加一个接口

1. 先在 `docs/API.md` 写契约：方法、路径、身份要求、错误码、是否跨租户可读。
2. 在 `app/schemas/` 定义响应模型，在 `app/api/v1/` 装配；业务规则放 `app/services/`。
3. 跨租户访问一律 `404`，不要 `403`。
4. 覆盖三类测试：正常路径、越权/跨租户、并发或重复提交（若涉及写）。
5. 前端要用的话，同步更新 `web/src/types.ts` 与 `web/src/api.ts`。

## 变更需要 ADR 的情况

改动状态机、审批语义、幂等/对账策略、身份模型或对外 API 契约时，
请在 `docs/adr/` 追加一条 ADR（编号递增，含背景/决策/后果/被否决方案），
并在 PR 描述里链接它。纯文案、样式与测试补充不需要。

编号规则：`docs/adr/` 当前 **19 篇**（ADR-001…ADR-011、ADR-013…ADR-020），
**ADR-012 是预留号位**（策略外置与阈值灰度），**不回收、不重排**。
即使中间号位空着，新 ADR 也要接在当前最大号之后；`docs/AGENT_PLATFORM_PLAN.md` 的
§9.4 ADR 索引、编号说明与文件计数要同步改。

## 截图与文档

控制台视觉有变化时请重拍受影响截图（`docs/assets/screenshots/`），
README 只引用该目录下的图片。文档里的测试数量、端点数量必须与实际命令输出一致。

`docs/TicketPilot项目说明书.docx` 是 `docs/PROJECT_SPEC.md` 的 Pandoc 渲染，**不要手工编辑**。
改完 spec 后跑 `python scripts/build_manual.py`（或 `make manual`）：它渲染到临时文件、校验
`U+FFFD` 计数 0、段落数与表格数非零、体积不低于 50 KB，全部通过才替换旧 docx，
失败时旧文件原样保留并打印是哪一项没过。

改动**会影响回复文本的生成路径**（个性化效果、输出护栏、模板措辞）时，除了
`docs/API.md` 的响应示例，还要同步 `README.md` 的对照段、`docs/PROJECT_SPEC.md` §6.7
与前端 `web/src/pages/ConversationPage.tsx` 里的原因码标签表；并在 `docs/EVALS.md`
确认回放口径：新增一种会让回复变化的效果，等于让已有档案客户的历史 trace 不再逐字可回放。

改动**会打开或改变主动规则的写面**（`PUT /api/v1/memory/rules/{event_type}`）时，
这条同步清单更长，漏一条就会出现「文档说只能改种子数据，代码却能改」的漂移：

1. `docs/API.md`——请求体字段、每个错误码的形状、`details.allowed` 之类回显内容。
2. `docs/ARCHITECTURE.md`——新增「主动规则的可编辑面」一节或更新它：写路径、校验点、诚实边界。
3. `docs/SECURITY.md`——**可写字段的信任边界**：哪些字段不可写（`requires_confirmation`）、
   事件类型为什么由代码定、审计为什么只存 `template_sha256`、改动为什么不回溯。
4. `docs/PROJECT_SPEC.md`——§12.9 的写面块与 §19.1/§19.2 的实测读数、漂移表。
5. `docs/SCOPE.md` 的「不做」侧与 `docs/EVALS.md` 的「诚实边界」侧——写路径进不了评测集，
   要写清楚原因，不要让读数看起来覆盖了它。
6. `README.md`、`docs/AGENT_PLATFORM_PLAN.md`（规模事实、Phase 表、ADR 索引、交付表）
   与本文件的门禁读数。
7. 前端 `web/src/pages/ProactiveRules.tsx` + `web/src/api.ts` + `web/src/types.ts`，
   以及 `web/src/App.tsx` 的路由与角色门禁（`/rules` 只对 `support` / `admin` 可见）。
8. 规则文案**不双人复核**、没有版本表 / 一键回滚 / 按租户灰度——这三件事是
   ADR-019 明确不做的，任何文档都不能把它们写成已具备。

## 提交与 PR

- 分支名 `feat/…`、`fix/…`、`docs/…`。
- Commit message 用祈使句说明“为什么”，一次提交只做一件事。
- PR 模板会要求填写：验证方式（跑了哪些命令、看了哪个页面）与未验证边界。
  **不清楚是否真的可行的写法，请写进“未验证”，不要删。**
- 不要提交 `.env`、数据库文件、`web/dist`、`.tmp/`。
