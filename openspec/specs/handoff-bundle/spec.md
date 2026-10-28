# Handoff Bundle

## Purpose

移交包是坐席接手案件时看到的上下文包。客户档案里的档案摘要是客户**自己**写下的自由文本，
本能力把它带进移交包的只读区，并标明来源与长度上限：正文先按与档案读出口同一套规则脱敏，
再按移交摘要的既有上限截断。

摘要只进移交包——不进客户可见的回复文本、不进模型输入的提示词、不进任务上下文与追踪属性，
也不进队列行摘要。读档案摘要只读一列，不建行、不改更新时间、不写审计事件。客户没有档案行
或摘要是空 / 纯空白时，移交包不出现这三个键：不写 `null`、不写空串、不写 `0`、不占位。
加入档案摘要后，移交包只多出这三个键，既有键的名称与取值逐字不变。

## Requirements

### Requirement: 移交包携带客户档案摘要

当转人工的对话客户本人此前写下过**非空**的档案摘要时，这次转人工产生的移交包 SHALL 携带
三个键：`history_summary`（正文）、`history_summary_source`（来源标注）、
`history_summary_max_chars`（实际生效的截断上限）。

正文 SHALL 来自客户档案的摘要列这**唯一**来源；`history_summary_source` SHALL 恒为
`"customer_profiles.history_summary"`。系统 SHALL NOT 从会话槽位、消息历史、任务记录或
意图轨迹里拼装「这个客户之前说过什么」——那是另一套事实。

当该客户没有档案行，或档案摘要为空 / 纯空白时，移交包 SHALL **不出现**这三个键：
不写 `null`、不写空串、不写 `0`、不写任何占位符。

#### Scenario: 有摘要时三个键都出现
- **WHEN** 客户此前写下过非空的档案摘要，随后发生一次转人工
- **THEN** 移交包里存在 `history_summary`，其值是该客户档案摘要的内容
- **AND** `history_summary_source` 为 `"customer_profiles.history_summary"`
- **AND** `history_summary_max_chars` 是实际生效的截断上限

#### Scenario: 没有档案行时三个键都不出现
- **WHEN** 转人工的客户没有档案行
- **THEN** 移交包里不存在 `history_summary`、`history_summary_source`、
  `history_summary_max_chars` 三个键

#### Scenario: 摘要是纯空白时三个键都不出现
- **WHEN** 客户档案行存在，但档案摘要是空串或纯空白
- **THEN** 移交包里不存在 `history_summary`、`history_summary_source`、
  `history_summary_max_chars` 三个键

### Requirement: 摘要先脱敏、后截断

进入移交包的档案摘要 SHALL 先按**与档案读出口同一套**的脱敏规则处理，再按移交摘要的既有
长度上限截断。顺序 SHALL 固定为「先脱敏、后截断」——反了会把本该被掩码的内容留在包里。

`history_summary_max_chars` SHALL 记录**实际生效的截断上限**（整数），SHALL NOT 记录原文长度；
原文是否被截断由正文末尾的省略号表达。

#### Scenario: 摘要里的个人数据在包内检索不到
- **WHEN** 客户档案摘要里含手机号、邮箱、身份证号或银行卡号
- **THEN** 移交包正文中检索不到这些原文
- **AND** 这些位置呈现为脱敏后的掩码

#### Scenario: 超长摘要被截断到上限
- **WHEN** 档案摘要的长度超过移交摘要的长度上限
- **THEN** 移交包正文的长度等于该上限，且以省略号结尾
- **AND** `history_summary_max_chars` 等于该上限

#### Scenario: 未超长的摘要不被截断
- **WHEN** 档案摘要的长度不超过移交摘要的长度上限
- **THEN** 移交包正文与档案摘要在空白归一化后逐字相同，且不以省略号结尾

### Requirement: 档案摘要只进移交包

档案摘要 SHALL **只**出现在移交包里。系统 SHALL NOT 把它放进客户可见的回复文本、
模型输入的提示词、任务上下文或追踪属性。

移交包的**队列行摘要** SHALL 保持原样（只带本会话要点），档案摘要 SHALL NOT 出现在队列行摘要里。

#### Scenario: 客户回复里检索不到档案摘要
- **WHEN** 客户档案有摘要，随后发生一次转人工
- **THEN** 客户收到的回复文本里检索不到该摘要内容

#### Scenario: 提示词与追踪里检索不到档案摘要
- **WHEN** 客户档案有摘要，一次转人工的会话产生了回复与追踪
- **THEN** 模型输入的提示词与该次会话的追踪属性里检索不到该摘要内容

#### Scenario: 队列行摘要不带档案摘要
- **WHEN** 坐席读取转人工队列的一行
- **THEN** 该行只带本会话要点，不带档案摘要正文

### Requirement: 读取档案摘要只读、不留痕、不建行

读取档案摘要 SHALL 只读**一列**：按租户与用户逐值相等取档案摘要列，SHALL NOT 创建档案行、
SHALL NOT 改动档案的更新时间、SHALL NOT 写审计事件。移交包自身的落库已有既存审计覆盖。

读到的档案 SHALL 是该会话调用方**本人**的档案。坐席通过移交包读客户档案，
SHALL NOT 通过客户档案的读接口读别人的档案。

#### Scenario: 转人工之后没有新增的档案类审计
- **WHEN** 客户档案已有摘要，一次转人工发生
- **THEN** 审计里的档案类事件没有新增行

#### Scenario: 档案的更新时间没有被读取改动
- **WHEN** 客户档案已有摘要，一次转人工发生
- **THEN** 档案行的更新时间与转人工之前相同

#### Scenario: 同一个租户里的别的用户读不到这份摘要
- **WHEN** 同一租户里的另一个用户发生转人工
- **THEN** 该用户的移交包里不存在 `history_summary` 键

#### Scenario: 换一个租户读不到这份摘要
- **WHEN** 另一个租户里的同名用户发生转人工
- **THEN** 该用户的移交包里不存在 `history_summary` 键

### Requirement: 移交包的既有键逐字不变

加入档案摘要后，移交包 SHALL 只多出上述三个键，且这三个键**只在有摘要时**出现；
既有键的名称与取值 SHALL 逐字不变。

装配移交包的既有入口 SHALL 保持纯函数：不接数据库会话、不查库；既有调用点与既有产出键不变。

#### Scenario: 没有档案时键集合与本轮之前一致
- **WHEN** 一个没有档案的客户被转人工
- **THEN** 移交包的键集合等于本轮之前的键集合，不多不少

#### Scenario: 有档案时只多出三个键
- **WHEN** 一个有档案摘要的客户被转人工
- **THEN** 移交包的键集合等于本轮之前的键集合加上 `history_summary`、
  `history_summary_source`、`history_summary_max_chars`
