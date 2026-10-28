# P6 实施计划

## 目标

让任务事件端点从「取完已存事件就关闭」变成可持续订阅：回放 `Last-Event-ID`
之后的新事件逐条推送，空闲时发心跳，任务终态且事件排空后关闭；同时保留
`follow=false` 的一次性回放行为。

## 架构

```text
客户端 GET /tasks/{id}/events（默认 follow=true）
  → Last-Event-ID → TaskRuntime.follow_events(identity, task_id, after_id)
        循环：events(after_id) 回放/增量
              → get_task(status)
              → 终态：再排空一次 → return
              → 空闲 ≥ heartbeat：yield {"id": None, "event": "ping"}
              → 空闲 ≥ idle_timeout：return
              → sleep(poll_interval)
  → API 层：ping → ": ping\n\n"；其余 → id/event/data 帧
  → 数据库轮询：多个 worker 共享 task_events，无需进程内回调或消息总线
```

## 技术栈

Python 3.12、FastAPI `StreamingResponse`、SQLAlchemy 2 ORM、pytest；
控制台用 `fetch` + `ReadableStream` 读取 SSE。

## 规格路径

`openspec/changes/p6-sse-follow-2026-10/specs/task-events/spec.md`

## 全局约束

- 订阅以数据库轮询实现，不引入 Redis / 消息总线。
- 帧形状、权限与既有回放一致；跨租户仍 404。
- `Last-Event-ID` 之后的事件才推送，已消费的不重复。
- 心跳是 SSE 注释帧，不是任务事件，不占事件 id。
- 终态与最后事件分两条语句读取，终态后必须再排空一次。
- 空闲超过 `idle_timeout` 主动结束，连接不永久占用。
- `follow=false` 回到改动前的回放形状。

## 审查重点

1. 审批产生的新事件是否无需重连即可到达，且不重复。
2. 断点续传是否从锚点之后开始，顺序与持久化一致。
3. 终态提交与最后事件之间的读间隙是否被排空覆盖。
4. 心跳是否按间隔出现且不污染事件序列。
5. `follow=false` 的帧序列是否与 `runtime.events()` 逐帧一致。
6. 关闭 / 断开后是否无残留订阅状态，多个流是否互不影响。
7. 多 worker 共享数据库这一前提是否写清。

## 任务

### 任务 1：运行时订阅

- 文件：`app/services/runtime.py`（`follow_events`）
- 测试：`tests/test_task_event_stream.py` 的推送、续传、终态排空、心跳、
  `follow=false` 五条；终态在最后一次事件提交后仍能送达。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_task_event_stream.py -q`

### 任务 2：事件端点与帧格式

- 文件：`app/api/v1/tasks.py`（`follow` 查询参数、`frame()`、ping 注释帧）
- 测试：默认跟随；`follow=false` 只回放；心跳帧为 `: ping\n\n`；
  既有权限与 404 行为不变。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_task_event_stream.py -q`

### 任务 3：事件流用例

- 文件：`tests/test_task_event_stream.py`
- 测试：等待审批的任务在第二个调用者审批后收到 `approval.approved` 与终态；
  `Last-Event-ID` 续传无重复；终态任务回放后关闭；安静任务发心跳；
  `follow=false` 与 `runtime.events()` 逐帧比较（非空断言）。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_task_event_stream.py -q`

### 任务 4：关闭与断开无残留状态

- 文件：`tests/test_task_event_stream.py`
- 测试：先消费一条再关闭的流不影响后续流；两个并发流各自完整回放；
  全部关闭后新开的流仍从第一条开始。断言不依赖进程内订阅注册表。
- 验证：`.venv/Scripts/python.exe -m pytest tests/test_task_event_stream.py -q`

### 任务 5：走查脚本与文档

- 文件：`scripts/sse_walkthrough.py`、`docs/spec/项目规格.md`（§19.1）、
  `docs/下一步规划.md`、`docs/records/智能体平台设想.md`
- 测试/证据：脚本实跑一次退款任务，输出 live 事件数、断线续传补回条数，
  末尾打印 `P6 walkthrough passed`；文档写清轮询间隔、心跳、`idle_timeout`
  与「多 worker 共享同一张事件表」的前提。
- 验证：`.venv/Scripts/python.exe scripts/sse_walkthrough.py`

### 任务 6：全量门禁与控制台构建

- 文件：`README.md`、`CONTRIBUTING.md`、`docs/guide/范围与边界.md`、
  `docs/spec/TicketPilot项目说明书.docx`（重生成）、`web/src/api.ts`（消费端契约）
- 测试/证据：全量 `pytest`、`ruff`、`mypy app`、`alembic check`、
  Golden 40/40、支持集 61/61、维度集 3/3、`npm run build`。
- 验证：

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe -m alembic check
.venv/Scripts/python.exe evals/run.py
.venv/Scripts/python.exe evals/support_run.py
cd web && npm run build
```
