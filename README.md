# TicketPilot

**售后退款场景的 AI Agent：模型只负责读懂，授权与资金由服务端确定性代码决定。**

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-0e1b2c)](LICENSE)

TicketPilot 是一个可运行的售后客服 Agent。只读问题（商品、政策、订单、物流、自助统计）由本地 Agent 回答；写操作（退款、工单）交给确定性工作流，依次经过资格校验、按金额分层的人工审批、幂等执行与对账。语言模型只做意图识别和槽位抽取，不产出任何动作。默认 `AGENT_MODE=rules`，不需要任何云 API 就能在本机跑通。

![售后对话控制台](docs/assets/screenshots/conversation.png)

## 功能

- **只读问答**：商品咨询、政策问答、订单与物流查询、受控 Text-to-SQL 自助统计，不产生副作用。
- **确定性写路径**：退款与工单经过资格 → 审批 → 台账 → 对账，模型只能提交意图，不能授权。
- **四眼审批**：按订单金额分层，`action_digest` 绑定审批内容，订单级唯一索引挡住重复申请。
- **受控问数**：自然语言 → 结构化计划 → AST → SELECT 子集校验 → 只读连接执行，全程不调用模型。
- **转人工**：无依据、缺槽位或超权限时主动移交，并带上客户已经看到的候选。
- **多渠道**：Web、邮件、微信、企业微信统一进站，验签、身份映射与消息幂等集中在网关。
- **可评测**：Golden Set、只读支持集、2,200 条分层检索集（600 金 + 1,600 银，test 分片 1,100 条）与意图语料，全部通过产品接口驱动；检索侧可扫 top-k / top-n 曲线，也可用公开客服语料微调 embedding。

## 快速开始

需要 Python 3.12+。

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
pip install -e ".[dev]"

python scripts/seed.py               # 建表 + 演示数据
uvicorn app.asgi:app --port 8000
```

- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：`/health/live`、`/health/ready`
- 控制台：<http://127.0.0.1:8000/console/>（先 `cd web && npm ci && npm run build`）

再开一个终端跑退款全链路：

```bash
python scripts/refund_flow.py
```

## 架构

```text
ChannelGateway（验签 / 身份映射 / 消息幂等）
        │
        ▼
POST /api/v1/support/messages
        ├─ 只读 ──► SupportAgent ──► ContextCard / 受控问数
        ├─ 转人工 ─► HandoffBundle ─► 坐席队列
        └─ 写操作 ─► TaskRuntime ──► 资格 → 审批 → 执行 → 对账
```

渠道差异收敛在 `app/channels/` 的适配器与注册入口，渠道分支不进 `app/agent/` 和 `app/services/`。

## 检索

政策问答先检索、再回答。链路是 **BM25 + 稠密通道 + z-score 融合 + 可选重排**，只有一条链，没有可切换的分支：

| 环节 | 出厂配置 | 为什么是它 |
|---|---|---|
| 召回 | BM25，候选池 20 | 随包语料只有 15 个政策块，20 之后融合结果不再变化 |
| 融合 | z-score，稀疏权重 0.3 | 只看名次的 RRF 会把短语料的头部压平，同一批用例 recall@1 低 4 个点 |
| 稠密 | 微调 `bge-small-zh`，配端点即开 | 训练只用金标问句对，test 分片零重合；没有 `EMBEDDING_BASE_URL` 就只跑 BM25，不拿无语义的占位模型凑数 |
| 重排 | 默认关 | 本机 p95 2.6 s，是 300 ms 交互预算的 8 倍 |
| 答案 | 取前 6 个块 → 2 张政策卡 | 卡片带整篇政策，只有 `matched_section` 指路；k=7/9 与 6 的答案可见结果完全相同 |

分层检索集 2,200 条：600 条手写金标（300 调参 / 300 出结论）+ 1,600 条模板银标，test 分片 1,100 条，门槛只套金标 test。当前读数：纯 BM25 `recall@1` 0.67 / `@3` 0.91，接上微调编码器后 0.73 / 0.91。

```bash
python evals/retrieval_metrics.py --dataset evals/retrieval_set.json
```

融合分只负责排序。z-score 是查询内归一化的，离题问句的融合分照样能进前列，所以「有没有依据」读的是通道原始分（`RetrievedChunk.evidence()`），也是 `no_evidence` 转人工唯一信任的刻度。

稠密通道曾经做过「只在 BM25 盲区才调 embedding」的闸门：调用省下 83%，recall@1 从 0.730 掉到 0.710——盲区是用 BM25 分数判的，判错的那些查询正好是要救的那批，所以闸门被删掉了。编码器可以用公开客服语料自己微调（`scripts/finetune_embedder.py`），留出一整份政策做消融，验证它没在背测试集。

## 配置

从 `.env.example` 复制一份 `.env`：

```bash
cp .env.example .env
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_MODE` | `rules` | `rules` 纯规则；`pydantic_ai` 启用受限模型 |
| `DATABASE_URL` | `sqlite+pysqlite:///./ticketpilot.db` | 生产使用 PostgreSQL |
| `ENVIRONMENT` | `dev` | `prod` 触发 fail-closed 校验 |

完整变量清单见 `app/core/config.py`。

## 项目结构

```text
TicketPilot/
├─ app/                    FastAPI 后端（模块化单体）
│  ├─ asgi.py / main.py    ASGI 入口 / 应用组合根
│  ├─ seed.py              演示数据装载
│  ├─ api/v1/              HTTP 接口与依赖装配
│  ├─ channels/            渠道适配、验签、入站幂等与出站
│  ├─ agent/               意图路由、对话 Agent、受限模型抽取
│  ├─ services/            任务运行时及审批、退款、工单、记忆等业务流程
│  ├─ tools/               工具注册、Gateway 与执行器
│  ├─ models/              SQLAlchemy 数据模型
│  ├─ repositories/        按租户访问数据
│  ├─ db/                  数据库引擎与会话
│  ├─ schemas/             API 请求与响应模型
│  ├─ core/                配置、策略、身份、错误与护栏
│  ├─ retrieval/           政策检索与重排
│  ├─ sql/                 受控 Text-to-SQL
│  ├─ observability/       Trace 与脱敏
│  └─ resources/           随包分发的演示政策与商品目录
├─ alembic/                数据库迁移
├─ evals/                  Golden Set、支持集、检索与意图评测
├─ scripts/                种子数据、演示、Worker 与评测工具
├─ web/                    React 控制台
├─ docs/assets/            界面截图
├─ .github/workflows/      持续集成
└─ pyproject.toml 等       Python 配置、依赖锁定、Docker 与 Make 入口
```

上图按实际目录整理，省略了初始化文件和未逐一列出的子模块，不代表严格的单向分层。

## 开发

```bash
make install     # 安装依赖
make run         # 启动服务
make lint        # ruff
make mypy        # 类型检查
make eval        # Golden Set
```

没有 `make` 时直接运行对应命令，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全

部署前读 [SECURITY.md](SECURITY.md)。项目默认拒绝以 `prod` 模式启动，Header 身份、mock 退款与公开文档都不被接受。未实现的 OIDC 与 HTTP 适配器会让应用直接起不来。

## 许可

[MIT](LICENSE) © 2026 TicketPilot contributors
