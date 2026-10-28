# 检索查询改写与评测集分层：项目级口径

本文件是 TicketPilot 的项目级声明，只规定本轮工作（查询侧改写 + 评测集扩充）的
术语、契约、配置项与验收口径。流程仍遵循用户级协作规则；业务事实仍以
`docs/spec/项目规格.md` 为准。两者冲突时，先改本文件并说明理由，再改代码。

## 1. 术语表

| 术语 | 唯一定义 |
|---|---|
| 查询改写（query rewrite） | 在检索器收到查询之前，对**检索用文本**做确定性变换；不改用户原话、不改回复、不改意图路由与槽位抽取。 |
| 改写结果（RewriteResult） | `QueryRewriter.rewrite()` 的返回值，含原始查询、检索用文本、追加词、命中的 5W1H 面向、章节提示与规则痕迹。 |
| 章节提示（section hint） | 从**当前 ACTIVE 政策**的章节标题中选出的 1–2 个词串，只追加到检索用文本，不写回消息或回复。 |
| 5W1H 面向 | 固定六个英文枚举值：`WHO` / `WHAT` / `WHEN` / `WHERE` / `WHY` / `HOW`。中文解释分别对应「谁 / 什么 / 何时 / 何地 / 为何 / 如何」。 |
| 中文数字归一化 | 把中文数字与时段说法追加成阿拉伯数字形式（如 `三十天` → 追加 `30天`），**保留原文**而不是替换。 |
| 同义词扩展 | 命中领域词表后追加同组词；只追加，不删除、不替换原词。 |
| 金标（gold） | 由项目逐条对照政策原文写定、章节标签唯一的用例。 |
| 银标（silver） | 由模板按构造生成、标签由构造决定的用例；只能用于覆盖/压力与分层报告，不能单独当作质量结论。 |
| 外部问法（external phrasing） | 公开客服语料中的用户原话，只作为**措辞来源**；外部数据自带的问答/意图标签一律不采用。 |
| 旧金标（legacy gold） | `evals/support_set.json` 中现有 17 条带 `policy_id` 的检索用例；它们是回归门槛，不得被新数据集替换或稀释。 |

## 2. 查询改写契约

### 2.1 接口

```python
@dataclass(frozen=True)
class RewriteResult:
    original: str
    search_text: str          # 只给检索器用
    added_terms: tuple[str, ...]
    facets: tuple[str, ...]   # WHO/WHAT/WHEN/WHERE/WHY/HOW 的子集
    section_hints: tuple[str, ...]
    numbers: tuple[str, ...]
    synonyms: tuple[str, ...]

class QueryRewriter:
    def rewrite(self, query: str) -> RewriteResult: ...
```

`RewritingPolicySearch` 实现现有 `PolicySearch` 协议，只把 `search_text` 传给内层检索器；
内层可以是 `PolicySearchService` 或 `HybridPolicySearch`。稠密门控的覆盖率也按
`search_text` 计算，保证「改写后的查询」与「判门控的查询」是同一句话。

### 2.2 硬性边界

1. **确定性、离线**：不调用模型/网络，不读时钟，不依赖随机数；同一输入必须逐字产出同一结果。
2. **原话不动**：`messages.content`、`tasks.request`、回复文本、意图路由、金额与订单号抽取
   仍使用用户原话。改写只发生在检索入口。
3. **可回退**：`RETRIEVAL_QUERY_REWRITE_ENABLED=false` 时，检索结果必须与未引入改写前逐位一致。
4. **不许编造**：章节提示只能取当前 ACTIVE 政策的章节标题；不得新增政策里不存在的章节名。
5. **有上限**：章节提示最多 2 个，追加词最多 8 个；原文必须完整保留在 `search_text` 里。
6. **保留原文数字**：中文数字只追加阿拉伯形式，不删除中文形式，避免 `七天无理由` 的章节标题失配。
7. **不碰订单标识**：`ORD-DEMO-1001`、`DE20010003` 这类标识原样保留；归一化只处理
   数值 + 中文量词（元/块/天/日/周/星期/个月/小时/分钟/分）。

### 2.3 5W1H 的用法

5W1H 不是装饰标签，必须同时驱动两件事：

- **判定章节提示**：例如 `HOW`（怎么处理）优先提示「处理方式」，`WHEN`（多久/期限）
  优先提示「退款条件」「七天无理由退货」，`WHO`（谁审批）优先提示「审批要求」。
- **组织评测集**：每条金标/银标都带 `facets`，扩量时必须覆盖六个面向，不允许全部集中在
  `WHAT`。某章节天然没有某个面向时可以不覆盖，但要在生成报告里列出缺口。

## 3. 配置项

新增配置必须有默认值，并写进 `.env.example` 与 `Settings`：

| 环境变量 | 字段 | 默认 | 含义 |
|---|---|---|---|
| `RETRIEVAL_QUERY_REWRITE_ENABLED` | `retrieval_query_rewrite_enabled` | `true` | 总开关；关掉后逐位回到旧行为。 |
| `RETRIEVAL_QUERY_REWRITE_NUMBERS` | `retrieval_query_rewrite_numbers` | `true` | 中文数字/时段归一化。 |
| `RETRIEVAL_QUERY_REWRITE_SYNONYMS` | `retrieval_query_rewrite_synonyms` | `true` | 同义词扩展。 |
| `RETRIEVAL_QUERY_REWRITE_SECTIONS` | `retrieval_query_rewrite_sections` | `true` | 章节标题注入。 |
| `RETRIEVAL_QUERY_REWRITE_MAX_SECTION_HINTS` | `retrieval_query_rewrite_max_section_hints` | `1` | 章节提示上限（口径上限 2）。 |
| `RETRIEVAL_QUERY_REWRITE_MAX_EXPANSIONS` | `retrieval_query_rewrite_max_expansions` | `8` | 追加词上限（不含原文）。 |

生产环境不因该功能拒绝启动：它是纯本地、可回退的检索变换，没有外部端点依赖。

## 4. 评测集契约

### 4.1 分层

| 层 | 文件 | 规模 | 用途 |
|---|---|---|---|
| 旧金标 | `evals/support_set.json` | 17 条带标注 | 历史回归与跨版本可比；门槛仍按这 17 条报一项。 |
| 新金标 | `evals/retrieval_set.json`（`label_quality=gold`） | 200 条 | 主质量结论；按 `dev` / `test` 拆分。 |
| 银标 | 同上（`label_quality=silver`） | 800 条 | 覆盖与压力；不得与金标合并成一个数字。 |
| 外部问法 | `evals/external/` | 采样若干 | 只证明措辞来源；不参与指标分母。 |

总计约 1000 条新用例。`support_set.json` 的只读路径（该轮 61 条；P11 加 1 条候选用例后是
62 条，见 ADR-033）与 `golden_set.json` 的 40 条任务路径不收编进新数据集，保持原样。

### 4.2 字段

`evals/retrieval_set.json` 顶层：`version`、`description`、`k_values`、`gold_counts`、
`silver_counts`、`cases`。

每条 case：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 全局唯一、稳定。 |
| `query` | 是 | 用户问法；金标不得逐字抄政策句子。 |
| `policy_id` | 是 | `POLICY-REFUND` / `POLICY-DELAY` / `POLICY-RETURN` / `POLICY-TICKET`。 |
| `sections` | 是 | 期望命中的章节标题，至少 1 个。 |
| `facets` | 是 | 5W1H 子集，至少 1 个。 |
| `label_quality` | 是 | `gold` / `silver`。 |
| `source` | 是 | `authored` / `template` / `external_phrasing`。 |
| `split` | 是 | `dev` / `test`。 |
| `template_id` | 银标必填 | 模板标识，用于按模板族切分与去重。 |
| `provenance` | 外部问法必填 | 数据集名、文件、行号/哈希；不写外部数据的模型标签。 |

### 4.3 指标口径

- 质量结论必须**分层报告**：`legacy_gold`（17）、`gold_dev`、`gold_test`、`silver_test`。
- `silver` 的数字只能叫「覆盖指标」；金标数字才能叫「检索质量」。
- `stale_hits` 与 `cross_tenant_hits` 在**所有层**都必须为 0。
- 改写前后必须给出同层对比；不能只报改写后的数字。
- 门槛（`thresholds`）只套在旧金标与 `gold_test` 上；银标不设通过/失败判定。

### 4.4 可复现

- 金标源文件与银标模板进仓库；`scripts/build_retrieval_set.py --check` 必须能重算出
  与 `evals/retrieval_set.json` 逐字一致的结果（允许 JSON 稳定排序）。
- 外部问法采样文件进仓库，附来源 URL、许可证与 SHA256；下载脚本不得成为 CI 依赖。
- 不允许把外部数据集的标签、模型生成的标签写成 `gold`。

## 5. 验收标准（DoD）

1. `pytest` 全量通过；新增用例覆盖数字、同义词、章节提示、5W1H、总开关回退。
2. 旧金标 17 条在改写开启后**不退化**：`recall@1/@3/@5`、`mrr`、`nDCG@5` 逐项不低于
   改写前；若某项确实下降，必须在 ADR 与 `docs/guide/评测说明.md` 写明原因与幅度，不能静默接受。
3. 新金标 `gold_test` 报出改写前后对比；银标只报覆盖。
4. `stale_hits = 0`、`cross_tenant_hits = 0`。
5. `scripts/build_retrieval_set.py --check` 通过；数据集形状、标签、拆分由测试钉住。
6. 文档同步：ADR、`docs/spec/项目规格.md`、`docs/guide/评测说明.md`、`docs/guide/架构说明.md`、
   `README.md`、`.env.example`、`CONTRIBUTING.md` 与说明书重生成。
