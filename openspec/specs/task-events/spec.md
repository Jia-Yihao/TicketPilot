# Task Events

## Purpose

把任务事件端点从一次性回放变成可续传的实时订阅：回放断点之后的持久化事件，
空闲发心跳，终态排空后关闭。

## Requirements

### Requirement: 运行中的任务可被持续订阅

系统 SHALL 在任务运行期间保持事件流连接，把 `Last-Event-ID` 之后的新事件逐条推送；
任务进入终态且没有新事件后 SHALL 结束流。

#### Scenario: 新事件到达即推送
- **WHEN** 客户端在任务等待审批时保持连接
- **THEN** 审批产生的新事件按持久化顺序到达，不需要重连或刷新
- **AND** 同一事件只推送一次

#### Scenario: 断点续传
- **WHEN** 客户端带 `Last-Event-ID` 重连
- **THEN** 首个推送事件是它之后的那一条，之前的事件不重复

### Requirement: 不泄漏、不依赖单进程

系统 SHALL 用数据库轮询实现订阅，使多 worker 共享同一份事件表；
结束或断开后 SHALL 不残留订阅状态。

#### Scenario: 终态关闭
- **WHEN** 任务进入 `SUCCEEDED` / `FAILED` / `CANCELLED`
- **THEN** 事件流在送完已提交事件后结束

#### Scenario: 心跳
- **WHEN** 连接空闲达到心跳间隔
- **THEN** 服务端发送 SSE 注释帧，连接保持

### Requirement: 兼容既有回放

系统 SHALL 保持事件接口的帧形状与权限不变，并允许 `follow=false` 回到「回放后关闭」。

#### Scenario: 回放
- **WHEN** 请求 `follow=false`
- **THEN** 行为与改动前一致（取已持久化事件、发完关闭）
