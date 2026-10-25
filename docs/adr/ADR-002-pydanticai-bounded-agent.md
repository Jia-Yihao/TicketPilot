# ADR-002：使用 PydanticAI 替代 LangGraph 作为受限抽取节点

- 状态：Accepted
- 日期：2026-10-23
- 关联：ADR-001

## 背景

上一项目已深度使用 LangGraph；本项目希望避免重复同一技术栈，同时保留结构化输出、可测试性和 Python 类型约束。候选包括 Claude Agent SDK、Pi Agent、PydanticAI。

## 决策

选择 **PydanticAI**，但只用于一个受限节点：

- 输入：用户文本 + 可选 context
- 输出：`IntentOutput`（intent、order_id、amount_cents、confidence、decision_summary）
- 无工具、无自主循环、无记忆
- 规则先跑；模型输出**只用于**补齐缺失槽位或解决 `UNKNOWN`（规则命中的意图不会被模型覆盖）
- 但 `AGENT_MODE=pydantic_ai` 下模型**每个任务请求都会调用一次**，不是“只在规则失败时才调用”；按需调用是需要额外实现的优化
- 模型失败、超时、输出不合法 → 回退规则（`task.routed` 事件的 `planning_source` 为 `rules_fallback`）
- 预算：`UsageLimits(request_limit=2)` + 超时（默认 20s）

控制流仍由 `TaskRuntime` 掌握，**没有迁移到 Agent 图**。

## 为什么不是 LangGraph

- 本项目控制流是确定性状态机，不需要图编排
- 避免为 6 个固定节点引入图状态、checkpoint、节点编排依赖
- 作品集需要展示“会选型”，而不是“所有项目都用同一框架”

## 为什么不是 Claude Agent SDK / Pi Agent

- 两者更偏向带工具的自主 Agent 循环，与本项目“无工具、只抽取”的边界不匹配
- PydanticAI 的 `output_type` 与 Pydantic v2 模型直接对齐，测试可用 `TestModel` 离线执行
- 模型可通过字符串切换，不绑定单一供应商；默认示例为 Anthropic，但也可换其他 provider

## 后果

优点：

- 依赖少，边界清晰，易测
- 模型不可用时系统仍能运行（规则回退）
- 真实模型调用可完全关闭

代价：

- 若未来需要多步自主推理，需要重新评估框架
- 当前 PydanticAI 仅用于抽取，框架能力未被充分利用

## 验证

- `tests/test_pydantic_agent.py` 使用 `TestModel` 验证结构化输出、规则优先、UNKNOWN 补齐、失败回退
- 真实模型调用尚未验证（无 API Key），README 已明确声明
