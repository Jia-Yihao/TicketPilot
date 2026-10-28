# OpenSpec 使用约定（项目级）

> 本文件规定 TicketPilot 中的变更如何立项、写规格、写计划、验收和归档。
> 流程仍遵循用户级协作规则（organizer → executor → reviewer）；本文件只补"传什么物、放在哪"。
> 目录与字段口径以本文件为准，不得在执行时临时改名。

## 1. 目录结构

```text
openspec/
  project.md                    项目上下文（技术栈、目录职责、验证命令、不变量）
  AGENTS.md                     本文件：变更流程与文件口径
  specs/                        当前已实现的基线能力规格（按 capability 分目录）
  changes/
    <change-id>/                活跃变更
      proposal.md               为什么改、改什么（不含实现细节）
      specs/<capability>/spec.md 规格增量（ADDED / MODIFIED / REMOVED）
      design.md                 可选：有技术取舍时才写
      tasks.md                  里程碑级任务清单（勾选项）
      plan.md                   任务级实施计划（writing-plans 产出）
    archive/
      YYYY-MM-DD-<change-id>/   归档后的完整变更目录
```

## 2. 规模分级

| 规模 | 判定 | 必须产出 |
|---|---|---|
| tiny | 单文件、无契约变化、无迁移 | 不需要 change 目录；仍要 TDD 与验证证据 |
| normal | 跨模块、有新接口/字段/配置/错误码 | `proposal.md` + 规格增量 + `tasks.md` + `plan.md` |
| large | 新子系统、数据迁移、权限或计费变化 | normal 全部 + `design.md` + 分批任务 |

判定不确定时按 normal 走。一次 change 只解决一个可独立验收的问题；跨多个独立子系统就拆。

## 3. 生命周期

```text
propose → spec → plan → implement → verify → archive
```

1. **propose**：用 `brainstorming` 技能问清目标与缘由，写 `proposal.md`。
2. **spec**：只写行为契约，用 `Requirement` + `Scenario`。不写实现步骤。
3. **plan**：规格批准后，用 `writing-plans` 写 `plan.md`；`tasks.md` 只保留里程碑级勾选项，与 `plan.md` 的任务编号对齐。
4. **implement**：用 `test-driven-development` 逐任务执行。每个任务先红后绿。
5. **verify**：用 `verification-before-completion` 跑命令、贴证据；由 `reviewer` 独立验收。
6. **archive**：验收通过后把整个 `<change-id>/` 目录移到 `archive/YYYY-MM-DD-<change-id>/`，并把规格增量合并进 `specs/<capability>/spec.md`。

任何一步没有产物，就不进入下一步。

## 4. 文件口径

### proposal.md

```markdown
## 为什么

[现状、问题、为什么现在做。不写方案。]

## 改什么

[按能力列变更点，一条一句。]

## 不改什么

[显式排除的范围。]
```

### specs/&lt;capability&gt;/spec.md（增量）

```markdown
## ADDED Requirements

### Requirement: <行为名>
<系统 SHALL ...>

#### Scenario: <场景名>
- **WHEN** <触发条件>
- **THEN** <可观察结果>
- **AND** <附加约束>
```

修改已有行为用 `## MODIFIED Requirements`，删除用 `## REMOVED Requirements`。
一条 Requirement 只写一个可验收的行为；Scenario 必须能被一条测试直接映射。

### tasks.md

```markdown
## 1. <阶段名>

- [ ] 1.1 <里程碑>（计划任务 1）
- [ ] 1.2 <里程碑>（计划任务 2）
```

编号规则：`<阶段>.<序号>` 只用于阅读分组，每一项末尾必须写 `（计划任务 N）`，其中 `N` 是 `plan.md` 里全局连续的 `### 任务 N` 编号；两者必须一一对应，无法唯一对应的项不许写进 `tasks.md`。

勾选由 organizer 在 reviewer 通过后执行；reviewer 只判定，不改文件。

### plan.md

格式见用户级 `writing-plans` 技能。必须含：目标、架构、技术栈、规格路径、全局约束、审查重点、逐任务的精确文件与测试。

## 5. 与三个角色的接口

| 角色 | 读什么 | 产出 |
|---|---|---|
| organizer | `proposal.md`、`plan.md`、`tasks.md` | 分派单（任务编号、边界、依赖、验收标准） |
| executor | 分派单 + `plan.md` 对应任务 + 规格增量 | 代码、测试、自检证据（命令与输出） |
| reviewer | `proposal.md`、规格增量、`plan.md`、实际 diff 与源码 | 通过结论，或 8 字段退回通知 |

reviewer 靠**读代码、检查逻辑、对照口径**判定正确性，不运行测试；执行者提交的测试输出是供其阅读的证据。只有必要测试覆盖明显缺失、且该覆盖对正确性至关重要时，reviewer 才在退回通知中要求补**最小范围**测试，不得要求全量测试。

reviewer 判定"通过"后，organizer 勾选 `tasks.md` 对应项并归档。executor 的"已完成"一律只表示"已提交审查"。

## 6. 冲突与优先级

1. 用户当次明确指令
2. `docs/spec/项目规格.md`（业务事实）
3. `.qoder/rules/*.md`（当前工作轮次的项目口径）
4. `openspec/specs/`（已实现能力基线）
5. `openspec/AGENTS.md`（变更流程与文件口径）
6. `openspec/project.md`（项目上下文与不变量）

发现冲突时：先停下，说明冲突点，改口径文件，再改代码。不允许"代码先跑通、文档后补"。

## 7. 禁止事项

- 不许跳过规格直接写代码（tiny 除外）。
- 不许在 `specs/` 里写实现细节、类名、SQL。
- 不许一个 change 里塞多个不相关变更。
- 不许改 `change-id` 或目录名；需要改名就新建 change。
- 不许在未归档的情况下把增量写进基线 `specs/`。
- 不许用"测试通过"代替"规格逐条有对应测试"。

## 8. 证据型变更的产物与自证

「证据型变更」指只产出读数与证据文档、不改产品代码的轮次（tiny 的一种）。它在脏工作树上运行，
`git status` 无法区分「本轮改的」与「更早轮次累积的」，因此**自述不得写成已核事实**。

### 8.1 必须落盘的三类产物

| 产物 | 位置 | 要求 |
|---|---|---|
| 读数产物 | 仓外临时目录，或仓内已被 `.gitignore` 覆盖的路径 | 记文件名、字节数、mtime、生成命令；仓外产物必须写明「不入库、归档后不可复看」 |
| 树状态快照 | 变更目录内（`tree-snapshot.md` 或证据文档的 §1） | 运行**前**与运行**后**各一份 `git status --short` + `git diff --stat`，逐字粘贴；不得只写结论 |
| 范围声明 | 变更目录内 | 声明「本轮是否改过既有文件」；能证则附证据，不能证则**明写「自述、当前树状态无法独立复核」** |

### 8.2 硬性边界

1. **不写「唯一新增产物」**，除非能列出仓内新增路径的完整清单（含 `--basetemp` 之类的副产物目录）。
2. **不写「运行前后一致」**，除非前后两份快照逐字相同；被外部进程改动的 tracked 文件要列名 + 时刻，
   并说明改动是否落在读数窗口内。
3. **范围声明不得依赖自证**。脏树下的自述只能标为自述；可核方法缺失时先由 organizer 补口径再写证据。
4. **读到与既有文档冲突时**，即使本轮红线禁止改 `docs/**`，也必须在「待办」里**逐条点名**冲突的
   `文件:行` 与需要改成的方向（不写具体新数字，避免二次臆造）；只登记，不在同轮改。
5. **时点读数必须标时点**。历史读数按项目做法标日期保留，不静默改成现状值；现状值另起一行并注明
   「此为 X 日 Y 时读数，现已变化」。
6. **锚点必须逐字核对**。`文件:行` 形式的出处，行号要指向承载该口径的那一行，不是相邻行。
