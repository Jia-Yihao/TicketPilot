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
ruff check app scripts evals alembic
python -m mypy app                        # strict 模式，零错误
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
澄清降级剧本对比、指标基线门禁与控制台构建是本地附加检查（CI 的 web job 单独跑构建）；
Postgres job 只验迁移链。

检索指标的双库一致性由 `PolicySearchService.visible_chunks()` 按块 ID 排序保证（`app/retrieval/search.py`）。

CI 会跑 ruff、mypy、Golden Set、Alembic 迁移与 `alembic check`、以及控制台构建，
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
docs/guide/        使用与实现：架构、接口、边界
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
4. 自测三类路径：正常、越权/跨租户、并发或重复提交（若涉及写）。
5. 前端要用的话，同步更新 `web/src/types.ts` 与 `web/src/api.ts`。

## 截图与文档

控制台视觉有变化时请重拍受影响截图（`docs/assets/screenshots/`），
README 只引用该目录下的图片。文档里的端点数量必须与实际命令输出一致。

改动**会影响回复文本的生成路径**（个性化效果、输出护栏、模板措辞）时，除了
`docs/guide/接口说明.md` 的响应示例，还要同步 `README.md` 的对照段
与前端 `web/src/pages/ConversationPage.tsx` 里的原因码标签表：新增一种会让回复变化的效果，
等于让已有档案客户的历史 trace 不再逐字可回放。

改动**会打开或改变主动规则的写面**（`PUT /api/v1/memory/rules/{event_type}`）时，
这条同步清单更长，漏一条就会出现「文档说只能改种子数据，代码却能改」的漂移：

1. `docs/guide/接口说明.md`——提交（202）与批准（200）两段契约、每个错误码的形状、`details` 回显内容。
2. `docs/guide/架构说明.md`——「主动规则的治理面」一节：提交 → 复核 → 生效的写路径、版本快照、回滚、诚实边界。
3. `docs/guide/安全说明.md`——**可写字段与审批的信任边界**：`requires_confirmation` 不可写、
   事件类型由代码定、发起人不能批准自己的提案、审计只存摘要、改动不回溯。
4. `docs/guide/范围与边界.md` 的「不做」侧——写路径进不了评测集，要写清楚原因。
5. `README.md` 与本文件的门禁命令清单。
6. 前端 `web/src/pages/ProactiveRules.tsx` + `web/src/api.ts` + `web/src/types.ts`，
   以及 `web/src/App.tsx` 的路由与角色门禁（`/rules` 只对 `support` / `admin` 可见；
   批准 / 驳回按钮只对 `approver` 且非提交人显示）。
7. 规则文案现在**需要另一位审批人**（四眼）、有不可变版本表与一键回滚；仍然**没有**
   删除规则、按租户灰度、定时生效或对已生成通知的回溯改写——这些是刻意不做的，
   任何文档都不能把它们写成已具备。

## 提交与 PR

- 分支名 `feat/…`、`fix/…`、`docs/…`。
- Commit message 用祈使句说明“为什么”，一次提交只做一件事。
- PR 模板会要求填写：验证方式（跑了哪些命令、看了哪个页面）与未验证边界。
  **不清楚是否真的可行的写法，请写进“未验证”，不要删。**
- 不要提交 `.env`、数据库文件、`web/dist`、`.tmp/`。
