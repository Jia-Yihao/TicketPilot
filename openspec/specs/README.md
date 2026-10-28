# 基线能力规格

这里存放**已经实现**的能力规格，是 reviewer 判断"行为是否被改坏"的对照面。

## 现状

基线尚未一次性建立。不要凭空补写 12 个意图的规格——那会制造第二套事实源。

按以下规则逐步补齐：

- 某次变更归档时，把它涉及的 capability 增量合并进来。
- 新增 capability 时，在 `specs/<capability>/spec.md` 里写当前真实行为，测试名与 Scenario 一一对应。
- 规格与代码冲突时，以 `docs/spec/项目规格.md` 和代码为事实源，先修规格再继续。

## 格式

```markdown
# <Capability>

## Purpose
[这个能力对外承诺什么，两句话以内。]

## Requirements

### Requirement: <行为名>
<系统 SHALL ...>

#### Scenario: <场景名>
- **WHEN** <触发条件>
- **THEN** <可观察结果>
```

`capability` 用 kebab-case，按业务能力命名（`policy-retrieval`、`refund-approval`、`handoff-routing`），不按代码目录命名。
