# task-events 规格增量

## ADDED Requirements

### Requirement: 事件读取有界化
`TaskEventRepository.events()` 现实现无 LIMIT、整表按 task 读全量后在 Python 内
`ids.index(after_id)+1` 切片（`app/repositories/tasks.py` 的 `events()`）。系统 SHALL 改为
SQL 侧有界读取：单次查询返回行数不得超过固定页上限（实现常量为口径，不新增环境变量），
游标推进发生在数据库侧（按 `(created_at, id)` keyset，命中既有索引
`ix_task_events_task_created(task_id, created_at)`），SQLite 与 PostgreSQL 逐位一致。
`TaskEvent.created_at` 无唯一约束（`app/models/task.py`，默认值取 `app/core/time.py` 的
`utcnow()`，带微秒，同批行可同戳），现 `order_by(TaskEvent.created_at)` 对同戳行的次序
**不确定**（本轮已实读两处代码确认）；因此新游标的 `id` 兜底 SHALL 按「对同 `created_at`
行的**确定化**措施」登记，SHALL NOT 表述为「保持既有全序不变」。事件的**行集**、
`after_id` 语义、终态判定与 `follow_events` 的可观察行为 SHALL NOT 改变；
「跨 worker 靠所有进程读同一张表」的订阅口径 SHALL NOT 改成内存总线。

#### Scenario: 无界读取消失
- **WHEN** 以带 N 条事件的任务调用 `events()`
- **THEN** 单条 SELECT 带 LIMIT（读代码判定），且在**无重复时间戳**的数据集上返回顺序与
  旧实现全量切片结果逐条一致（等价性测试钉住：构造时间戳互异的数据集，新旧两条路径输出
  相同）；重复时间戳数据集只断言行集一致与 `created_at` 主序一致，**不断言**逐条一致
  （`id` 兜底是确定化，不是还原旧次序）

#### Scenario: 游标越界与缺行语义不变
- **WHEN** `after_id` 不存在于该任务事件序列
- **THEN** 行为与现状一致（返回空列表），不抛错、不返回全量

#### Scenario: SSE 跟读每轮查询有界
- **WHEN** 审查 `follow_events` 主循环（`app/services/runtime.py:1162-1220`）
- **THEN** 每轮取数走有界 `events()`；poll 间隔、心跳、idle_timeout 默认值不变
  （`:1168-1170` 读数回归）
