# ADR-006：PostgreSQL 为目标，SQLite 用于零依赖开发与测试

- 状态：Accepted
- 日期：2026-10-23

## 背景

生产需要并发、部分唯一索引、JSON 能力和可靠迁移；本地环境不总有 Docker/PostgreSQL。

## 决策

- 目标数据库：PostgreSQL（`psycopg` 3 + SQLAlchemy 2）
- 开发/测试：SQLite 文件库，开启外键约束
- 迁移：Alembic 为唯一生产建表方式
- `ENVIRONMENT=prod` 时强制：非 SQLite、`AUTO_CREATE_TABLES=false`

兼容性措施：

- 金额一律整数分，不用浮点
- 时间统一 naive UTC
- 部分唯一索引同时声明 `sqlite_where` 与 `postgresql_where`
- 枚举使用 `native_enum=False` 字符串存储
- JSON 字段用 SQLAlchemy `JSON`，后续可显式迁移到 `JSONB`

## 验证现状

- 已在 SQLite 实跑 `alembic upgrade head` 与 `alembic check`
- 已用 Alembic `--sql` 渲染 PostgreSQL DDL，确认部分唯一索引语法正确
- **本机未实际启动 PostgreSQL**（Docker daemon 不可用、无 psql）；Compose 与 CI Postgres 作业待在有 Docker 的环境验证

> **修订（2026-10-24）**：上面「本机未实际启动 PostgreSQL」是 2026-10-23 当天的读数。此后本机用 Docker 起过
> PostgreSQL 16.15 一次性容器，迁移链与当时那批用例（320 用例版本）跑通——**323 passed**，默认跳过的三项全部执行。
> 未被这次复跑覆盖的是此后新增的用例与四张新表（见 `docs/PROJECT_SPEC.md` §1.7）。
>
> **修订（2026-10-25）**：上面那句「未被覆盖」现在过时了。同一个容器上带 `TICKETPILOT_TEST_POSTGRES_URL`
> 跑了全量：**458 passed / 0 skipped（109.92 s）**，T2 起的 10 个新文件（148 条，含 `test_llm_gap.py`）
> 与四张新表（`channel_messages`、记忆 / 通知三张）都在内；同一容器上 Golden **40/40**、支持集 **61/61**，
> `alembic upgrade head` 走完 11 条迁移、`alembic check` 无漂移。仍未在 PG 上跑的是四通道检索指标脚本
> （PG 侧只有 `test_retrieval_eval.py` 的门槛断言）。

## 后果

- 本地零依赖可跑
- SQLite 与 PostgreSQL 的并发、锁、类型语义不同，CI 必须保留 Postgres 迁移验证
- 生产不得用 SQLite 兜底
