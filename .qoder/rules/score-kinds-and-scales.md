# 分数种类与分尺度阈值（R8）：项目级口径

> 本文件是 TicketPilot 本轮工作（给检索分数加种类标记、把「无依据」判定改成按尺度分别比较、
> 把「检索为空」拆成「库里没有」与「我没检索到」）的项目级声明，只规定术语、契约、配置项与
> 验收口径。流程仍遵循用户级协作规则；业务事实以 `docs/spec/项目规格.md` 为准，冲突时先改
> 本文件并说明理由，再改代码。
>
> 「当前事实」一律给 `文件:行` 证据；标注**「待实现」**的项是本轮已裁定、代码尚未落地的口径。

---

## 0. 本条要修的是什么（**修复前**的现状，2026-10-28 之前）

> 本节的描述与行号都是**改动前**的读数，保留下来是为了说明「为什么改」；当前事实见 §2 与 `docs/adr/ADR-038-分数种类与分尺度阈值.md`。

1. **分数与名次可以相反**。混合检索把两路通道的**原始分**留在 `score` 里：
   `app/retrieval/hybrid.py:141` 的 `by_id.setdefault(hit.chunk.id, hit)` 让**先入的那个通道**
   （通常是稀疏路）的分对象胜出，而返回顺序来自 `:145` 的 `rrf_fuse(channels, k=self.rrf_k)`
   与 `:148` 的重排。于是 `RetrievedChunk.score`（`app/retrieval/search.py:35`、写入点
   `:70-82` 的 `to_retrieved()`）描述的是某一路的原始分，名次描述的是融合/重排结果——
   两者可以完全相反，下游没有任何字段能看出这个分数属于哪把尺子。
2. **同一把尺子量三种分**。转人工的「无依据」判定只有一个阈值
   `escalation_no_evidence_score`（`app/core/config.py:59`，默认 `0.35`；`.env.example:122`
   同值），比较发生在 `app/services/escalation.py:292-305` 的 `_below_evidence`（`score < limit`，
   调用点 `:254`）。而 `score` 可能是 BM25 原始分（本语料上强命中是十几分）、余弦相似度
   （0..1）、RRF 融合分（单通道首位 `1/61 ≈ 0.0164`）或重排分（0..1）。`_below_evidence` 的
   docstring 自己写着「阈值是稀疏 BM25 尺度的」，代码却不按尺度分支——**文档与代码互相矛盾**。
   同一个误用在回答侧还有一处：`app/agent/actions.py:352-353` 的
   `grounded = evidence_score >= threshold` 决定 `Draft.resolved`。
3. **两句话被合并成一句**。一条都没检索到时说的是「未检索到当前租户下仍在有效期内的适用政策」
   （`app/agent/actions.py:341`），把「库里没有这条政策」与「这次没检索到」压成同一句。
   这两句话对客服的含义不同：前者该结束对话，后者该改写重试或转人工。

---

## 1. 术语表

同一概念只有一种叫法。右侧英文名是**实现必须逐字使用的标识符**。

| 中文术语 | 唯一英文标识符 | 唯一定义 |
|---|---|---|
| 分数种类 | `score_kind` | 一个分数「属于哪把尺子」的封闭标记，取值见 §1.1。 |
| 稀疏分 | `sparse` | BM25 原始分（`PolicySearchService.ranked` 的输出，`app/retrieval/search.py:160`）。 |
| 稠密分 | `dense` | 余弦相似度；`app/retrieval/hybrid.py` 的 `_dense_ranked` 只保留 `> 0` 且 `>= dense_min_score` 的命中。 |
| 融合分 | `fusion` | RRF 分 `Σ 1/(k + rank)`（`app/retrieval/fusion.py::rrf_fuse`，`k` 默认 60 ⇒ 单通道首位 `1/61`）。 |
| 重排分 | `rerank` | 重排端点返回的 `relevance_score`（`app/retrieval/rerank.py`，Cohere 风格，0..1）。 |
| 证据分 | `evidence_score` / `EvidenceScore` | 交给「有没有依据」判定的那一个分数，连同它的种类。 |
| 判定分 | `verdict` | **不是检索分**：动作层自己写下的 0 或 1（`app/agent/actions.py` 的自有记录查询路径），表示「这次动作算不算给了答案」。它只为让这些既有读数在按种类比较后**逐条不变**而存在（§2.3 第 4 条末行）。 |
| 无依据阈值 | `no_evidence_score` | 「证据太弱」的判定线；**本轮的改动就是让它按种类取不同值**（§2.3）。 |
| 空检索原因 | `empty_reason` | 一条都没检索到时的原因分类，取值见 §2.4。 |
| 可见 chunk | visible chunks | `PolicySearchService.visible_chunks()`（`app/retrieval/search.py:135`）给出、满足租户边界与有效期边界的那批文档块。 |

### 1.1 封闭词表

```python
# app/retrieval/search.py —— 检索产出的分数种类（T8-1 已落地）
SCORE_KINDS = ("sparse", "dense", "fusion", "rerank")
# app/core/policy.py —— 交给「有没有依据」判定的分数种类
EVIDENCE_KINDS = (*SCORE_KINDS, "verdict")
EMPTY_REASONS = ("empty_query", "no_visible_chunks", "no_match")
```

`SCORE_KINDS` 定义在 `app/retrieval/search.py`（与 `RetrievedChunk` 同文件），`app/` 内不得另立第二份。
`EVIDENCE_KINDS` 是**判定面**的词表：它比 `SCORE_KINDS` 多一个 `verdict`（§1 术语表），
因为「算不算有依据」也要能表达「这次动作自己判了自己」，而那是动作层的读数、不是检索分。
两份词表的**唯一**约束是前者是后者的前缀；**不得**为了少写一行把 `verdict` 塞进 `SCORE_KINDS`
（那会让 `RetrievedChunk.score_kind` 允许一个检索永不产出的值）。
出现词表外的值属于**编程错误**，不是「未知种类按稀疏处理」。

### 1.2 必须消歧的同名概念

裸名 `score` 在本仓库被多处占用。下表把它们与「分数种类」分开；**实现不得混用**。

| 已有标识符 | 含义 | 证据 | 与本条的关系 |
|---|---|---|---|
| `DenseGate.min_top1_score`（`5.0`） | **稠密门控**的 BM25 边界：低于它才去调稠密通道 | `app/retrieval/hybrid.py:40`、`:52`、判据 `:60` | 不同用途。它是「要不要跑稠密通道」，不是「有没有依据」。本条**复用**它作为稀疏阈值的**校准依据**（同一个 17 条标注集上校准出来的 BM25 边界），但**不共用**这个字段。 |
| `DenseGate.min_coverage`（`0.35`） | 稠密门控的查询词覆盖率边界 | `app/retrieval/hybrid.py:53`、判据 `:62` | 不同用途。它是覆盖率，不是分数。**注意**：`0.35` 与本条稠密阈值同值纯属巧合，不得据此互相引用。 |
| `dense_min_score` | 稠密通道的**入选**下限（构造函数参数，默认 `0.0`） | `app/retrieval/hybrid.py` 构造函数形参 | 不同用途：入选门槛，不是无依据阈值。 |
| `PolicySearchService.min_score` | 稀疏通道的**入选**下限（默认 `0.01`） | `app/retrieval/search.py:130` | 同上。 |
| `zscore_fuse` | 另一套融合函数，**生产未用** | 定义 `app/retrieval/fusion.py:55`；`app/` 内除 `__all__` 外零调用 | 与本条无关；不得顺手接上。 |
| `RetrievedChunk.citation()["score"]` | 引用里回填的分数 | `app/retrieval/search.py:74-86` | 它跟随 `RetrievedChunk.score`，键集**不变**（§4.1）。 |

---

## 2. 契约

### 2.1 字段

```python
@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: DocumentChunk
    document: Document
    score: float
    score_kind: str = "sparse"        # 新增，默认值保证既有构造点不改

@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    ...
    score: float
    score_kind: str = "sparse"        # 新增，默认值同上
```

1. **默认 `"sparse"`**：`PolicySearchService` 是唯一的纯稀疏实现，它的既有产出本来就该标 `sparse`；
   默认值让既有构造点（含 `tests/` 里的直接构造）一行不改。
2. `to_retrieved()` **照抄** `ScoredChunk.score_kind`，不得改写。
3. **不进 `citation()`**：引用键集（`app/retrieval/search.py:74-86`）是已承诺的对外契约，
   本条**不加** `score_kind` 键。种类只走内存与 trace（§2.5）。

### 2.2 分数与顺序必须同尺度

1. **写回融合分**：混合检索返回的 `RetrievedChunk.score` 必须是**融合分**（`fusion`）或
   **重排分**（`rerank`），不得把某一路通道的原始分留在里面。**修复前**违反此条（见 §0 第 1 点）；
   **修复后**已满足——`_scored_order()` 只产出这两种种类（`app/retrieval/hybrid.py:173` 无重排时
   全部 `fusion`、`:180` 重排头、`:183` 端点未回答的尾块），`search()` 照抄写回
   （`app/retrieval/hybrid.py:152`）。
2. **单调性**：任何 `search()` 返回的列表，`score` 必须沿返回顺序**单调不增**。这是
   「分数与名次不矛盾」的可判定形式，用断言钉住（§5 第 2 条）。
3. **只有一路有结果时也标融合**：仍用融合分与 `fusion` 种类，理由——下游比较的是
   「这条证据有多强」，不是「哪一路给的」。
4. 重排生效时：`score_kind = "rerank"`，`score` 取重排分；未进入重排结果的尾部队列
   （`app/retrieval/hybrid.py` 的 `_rerank` 把未重排的 id 追加在后）**也必须有分数**，
   其值取该块的融合分并按「低于所有重排分」排序——不得留 `0.0` 之类会破坏单调性的占位。
   **corner case 的读法（2026-10-28 按 T8-1 独立复查结论补登记）**：若某尾块的融合分
   **高于**本轮最小重排分（端点的 `relevance_score` 某条低于 RRF 分），则取
   `min(该块融合分, 本轮最小重排分)`——两条要求（取融合分 / 低于所有重排分）在此处张力，
   取后者，因为第 2 条的单调不增是硬要求、第 4 条末句也明禁破坏单调性的占位。
   正常情形（融合分低于所有重排分，`rrf_k=60` 时单通道首位即 `1/61`）该结果就是融合分本身。
5. **重排头部的顺序**：按重排分降序，同分保持端点返回顺序。旧实现原样保留端点顺序——
   Cohere 风格端点的 `results` 本就按相关系降序，两者无差异；端点乱序时以分数为准，
   否则分数与名次再次相反。**登记为已知行为变化**（进 R8 的 ADR）。
6. **`"dense"` 只在进程内可见**：`_dense_ranked()` 产出的 `ScoredChunk` 是 `by_id` 的内部值，
   **混合检索的** `search()` 输出一律重置为 `fusion` / `rerank`；纯稀疏的
   `PolicySearchService.search()`（`app/retrieval/search.py:218`）不经混合器，产出是 `sparse`。
   因此 `"dense"` 当前没有可观察断言——
   这是第 1 条「不得把某一路通道的原始分留在里面」的必然结果（第 3 条只覆盖「只有一路有结果」
   的情形），不是缺口；只能靠读代码确认。

### 2.3 证据分与无依据阈值按种类比较

```python
# app/core/policy.py
@dataclass(frozen=True, slots=True)
class EvidenceScore:
    score: float
    kind: str          # EVIDENCE_KINDS 之一

# app/services/support.py
@dataclass(frozen=True, slots=True)
class PolicyEvidence:
    cards: list[dict[str, Any]]
    best: EvidenceScore | None          # None = 一条都没检索到
    empty_reason: str | None            # 仅当 best is None 时非空，取值 ∈ EMPTY_REASONS
```

1. `SupportContextService.policy_evidence()`（`app/services/support.py:206-241`，`policy_cards()` 在 `:201-204`）的返回改为
   上面那个 `PolicyEvidence`（**不是**三元组）：`best=None` 等价于今天的 `score is None`；
   `empty_reason` 就是 §2.4 的三分结果，与 `best` 一起从**同一处**返回，调用方不必再知道
   「怎么判断空检索」。`policy_cards()` 同步改（它只是取 `.cards`）。
   **为什么是 dataclass 而不是三元组**：三个字段里有两个只在空检索时才有意义，
   三元组会让每个调用点都要记住位置；dataclass 让「空检索」这件事有一个名字。
2. 判定只有一处：`EscalationThresholds.no_evidence_threshold(kind: str) -> float`
   （`app/core/policy.py`，与既有 `threshold(name)` 并存）。取不到 `kind` 的键 → `KeyError`，
   **不得**回落到稀疏阈值。
   **键只能用字面量，不能在 `app/core/policy.py` 里 import `SCORE_KINDS`**：`core` 是
   tier 0、`retrieval` 是 tier 2（`docs/architecture-guard.yaml` 的 `tiers`），
   `core -> retrieval` 是**向上依赖**，守卫会判违规。因此 `EVIDENCE_KINDS` 在
   `app/core/policy.py` 里逐字写出，词表本身仍以 `app/retrieval/search.py` 的 `SCORE_KINDS`
   为准；两份的关系由一条用例钉住（§5 第 5 条），不靠人工同步。
3. **两个消费点读同一个函数**：`app/services/escalation.py::_below_evidence` 与
   `app/agent/actions.py` 的 `grounded` 判定。任一处自写比较即违规。
   `_below_evidence` 仍先看 `signal.threshold`（YAML 里是 `no_evidence_score`）：它不是取值来源，
   只是「这个信号属于哪一族阈值」的自我声明——写错了照样 `KeyError`，
   与旧实现「阈值名打错即报错」的强度相同。
4. 阈值表（`EscalationThresholds` 新增三个字段，`Settings` 同名三个）：

   | `score_kind` | 配置项（环境变量） | 字段 | 默认 | 依据 |
   |---|---|---|---|---|
   | `sparse` | `ESCALATION_NO_EVIDENCE_SCORE`（**沿用旧名**） | `escalation_no_evidence_score` | **`5.0`** | 与 `DenseGate.min_top1_score` 同一批校准的 BM25 边界（`app/retrieval/hybrid.py:52`）。旧默认 `0.35` 是余弦尺度数字误用在 BM25 上。 |
   | `dense` | `ESCALATION_NO_EVIDENCE_SCORE_DENSE` | `escalation_no_evidence_score_dense` | `0.35` | 余弦相似度 0..1，与旧值同量级。 |
   | `fusion` | `ESCALATION_NO_EVIDENCE_SCORE_FUSION` | `escalation_no_evidence_score_fusion` | `0.0164` | ≈ `1/61`，即 `rrf_k=60` 下单通道首位的 RRF 分；低于它说明两条通道都没把它排到前面。 |
   | `rerank` | `ESCALATION_NO_EVIDENCE_SCORE_RERANK` | `escalation_no_evidence_score_rerank` | `0.35` | 端点 `relevance_score` 是 0..1。 |
   | `verdict` | **无配置项** | — | 常量 `0.5` | 动作层自写的 0 / 1 判定分。**不设配置项**的理由：没有人会去调「0 和 1 的分界线」，它只在 0.5 这一个位置有意义；给它开个旋钮等于邀请误配。 |

   **`verdict` 为什么必须存在（本轮的一处硬裁定，实现不得绕开）**：
   `app/agent/actions.py` 的 `analytics.query` / `analytics.expand` 用
   `EvidenceScore(score=0.0, kind="verdict")`（`:451`，SQL 被拒）与
   `EvidenceScore(score=1.0, kind="verdict")`（`:462`、`:521`、`:542`、`:559`、`:586`）当哨兵，
   而 `escalation.yaml` 的 `no_evidence` 信号 `intents` 明确包含 `SELF_ANALYTICS`
   （`app/agent/escalation.yaml:141-143`，信号 `takeover: true`、`reply_mode: replace`）。
   若这六个哨兵改成「没有种类」，按第 5 条它们会一律被判「无依据」——
   **每一次统计问答都会被转人工**。`verdict` 让 `0.0 < 0.5 <= 1.0` 与旧的 `0.0 < 0.35 <= 1.0`
   结论逐条相同，是本轮唯一不动这些读数的写法。
5. `kind` 缺省（`EvidenceScore` 整个为 `None`）与 `score` 为 `None` 一样按「无依据」处理；
   **不得**拿稀疏阈值去比一个不知道尺度的数。`EvidenceScore` 一旦存在，
   `kind` 必然是 `EVIDENCE_KINDS` 成员——「有分数没有尺子」这个状态在本轮**不可表示**（第 7 条）。
6. **行为变化登记（必须进 ADR 与本文件的实现记录）**：默认配置下，稀疏首位分 `< 5.0` 的检索
   现在会触发 `no_evidence` 转人工；旧实现里这个信号在稀疏路径上**永不触发**（任何命中都 ≥ 0.35）。
   这是本条的目的，不是副作用。既有用例 `tests/test_handoff.py::test_no_evidence_hands_over_when_retrieval_finds_nothing`
   走的是「一条都没检索到」（`None`）分支，读数不变。
7. `Draft`（`app/agent/turns.py:58-73`）的 `evidence_score: float | None` **换成**
   `evidence: EvidenceScore | None = None`，与第 1 条的 `best` 同一个类型：
   `evaluate_post(..., evidence=draft.evidence)`。**为什么换而不是加一个 `evidence_kind`
   在旁边**：一对平行字段允许「有分数、没种类」这个中间态存在，而那正是本条要消灭的误用；
   换成一个带种类的值，误用在类型层面就写不出来。`Citation.score`
   （`app/agent/handoff.py:101`，`float | None`）照旧取 `draft.evidence.score`——
   对外契约的**类型与键集都不变**，只是它承载的分数在混合检索下已经先是融合分（T8-1）。

### 2.4 空检索原因

```python
def classify_empty(query: str, visible: int) -> str: ...   # 纯函数，放 app/retrieval/search.py
```

| 值 | 判据 | 对客户的含义 |
|---|---|---|
| `empty_query` | 查询去空白后为空 | 输入问题（沿用今天的早退行为） |
| `no_visible_chunks` | 该租户在该时刻可见 chunk 数为 0 | **库里没有这份政策**：该结束对话或说明没有此类政策 |
| `no_match` | 有可见 chunk，但本轮一条没命中 | **我没检索到**：该改写重试或转人工 |

1. 落点只有两处：`retrieve/policy.search` span 的属性 `empty_reason`（既有 span，不新增），
   以及 `app/agent/actions.py` 那句「一条都没检索到」的回复——**按原因分两句**，
   不得再合并成一句。
   **哪句归谁（定死，防止实现时二选一）**：今天那句「未检索到当前租户下仍在有效期内的
   适用政策。建议转人工核实。」字面说的是**库里没有可用条款**，归 `no_visible_chunks`；
   `no_match` 另写一句**明确说「这次没检索到」**并给出可执行的下一步（改写问法或转人工），
   不得沿用前一句。`empty_query` 沿用今天的早退行为（早退 = 不查库、沿用今天那句），
   不新增一句「检索失败」的客户措辞。
   **客户可见面的现实（2026-10-28 补裁定，登记代价）**：`no_evidence` 信号是
   `reply_mode: replace`（`app/agent/escalation.yaml` 的 `no_evidence` 段，`:147` 附近），
   而零命中的 POLICY_QA 必然触发它（`tests/test_handoff.py::test_no_evidence_hands_over_when_retrieval_finds_nothing`
   已钉住）。因此这两句**今天都到不了客户眼前**——`SupportAgent._shape`
   （`app/agent/support_agent.py:1223-1230`）会用信号文案整句替换。本条**不改** `reply_mode`：
   改成 `append` 会同时改掉 `SELF_ANALYTICS` 的客户可见文案（同一信号、同一 `intents`），
   超出 R8 范围且会打进 support 集读数。于是「可分」的落点是两处**可判定**的地方：
   动作层的 `Draft.reply`（两句不同，用例直测）与 `retrieve/policy.search` 的 `empty_reason`
   属性；客户可见文本维持既有声明不变。这是本条**明确接受的代价**，实现与文档都不得写成
   「客户会看到两句不同的话」。
2. `no_visible_chunks` 的判定用 `visible_chunks()`（`app/retrieval/search.py:135`），
   它已经带租户与有效期边界；**不得**另写一条绕过边界的计数查询（跨租户计数是越权）。
3. 追加成本：一次 `visible_chunks()` 调用只在「本轮零命中」时发生，正常路径不增加查询。
   **空查询不查库**：查询去空白后为空时直接得 `empty_query`，不得为了数可见 chunk 多打一次库。
4. **计数的来源是 `PolicySearchService` 本身，不是注入进去的那个 `policy_search`**：
   在 `SupportContextService` 内用 `PolicySearchService(self.session).visible_chunks(identity)`
   数一次（构造函数参数只影响 `search()` 的条数与入选分，不影响可见性边界）。
   理由：`self.policy_search` 可能是 `RewritingPolicySearch` / `HybridPolicySearch`，
   它们没有统一的可见性方法（`RewritingPolicySearch` 靠 `__getattr__` 转发、`HybridPolicySearch`
   **没有**这个方法），按鸭子类型去取会在实现之间分叉。
   **代价（登记）**：给 `SupportContextService` 注入假 `policy_search` 的用例，
   若库里的可见 chunk 数不为 0，空命中会得到 `no_match` 而不是 `no_visible_chunks`——
   这正是**正确**读数（可见性问的是库，不是那个假检索器）。

### 2.5 trace 与对外面

- `score_kind` / `evidence_kind` / `empty_reason` **只进 trace 与内存**，不进客户可见文本、
  不进 `citation()`、不进 OpenAPI 契约。
- trace 落点：`retrieve/policy.search` span（`empty_reason` 与本轮命中数与种类），
  对话侧沿用 `Draft` → span 的既有搬运方式。**不新增表、不新增事件**。
  属性名定死：既有 `query` / `hits` / `citations` 一字不改，`evidence_score` 仍是**数值**
  （`float | None`，便于人读与既有工具），另加 `evidence_kind`（`str | None`）与
  `empty_reason`（`str | None`）。**不得**把 `EvidenceScore` 对象直接塞进属性值——
  trace 属性是 JSON。

---

## 3. 配置项与生效方式

| 项 | 位置 | 默认 |
|---|---|---|
| `escalation_no_evidence_score` | `app/core/config.py`（既有，`:59`）；`.env.example`（既有，`:126`） | `5.0`（**由 `0.35` 改**，§2.3 第 4 条） |
| `escalation_no_evidence_score_dense` | `app/core/config.py`（新增）；`.env.example`（新增） | `0.35` |
| `escalation_no_evidence_score_fusion` | 同上 | `0.0164` |
| `escalation_no_evidence_score_rerank` | 同上 | `0.35` |

- 全部为纯本地标量，无外部端点依赖；改完**重启进程**生效，不承诺热加载。
- `verdict` 的 `0.5` **不是配置项**（§2.3 第 4 条末行），因此本表保持四项。
- 阈值名仍在 `app/agent/escalation.yaml` 的 `no_evidence` 信号里以 `threshold: no_evidence_score`
  出现（`app/agent/escalation.yaml:140` 附近）——**键名不改**，解析改为按种类取值。

---

## 4. 边界与禁止事项

1. **不改 `citation()` 键集**、不改任何对外 schema、不改 OpenAPI 契约。
2. **不改 `escalation.yaml` 的 `threshold:` 键名**，不新增信号，不改 `no_evidence` 信号的
   `intents` / `queue` / 文案之外的触发机制。
3. **不改融合算法**：`rrf_fuse` 公式、`rrf_k` 默认值、`zscore_fuse`（生产未用）都不动。
4. **不改稠密门控**：`DenseGate.min_top1_score` / `min_coverage` / `reason()` 一行不改。
5. **不把三种尺度归一化成一个分数**。归一化会把「三把尺子」重新压成「一个没有单位的数」，
   正是本条要消灭的东西。
6. **不把「库里没有」当成「没有依据」**：`no_visible_chunks` 与 `no_match` 的回复措辞不同，
   但两者都按「无依据」走转人工（前者还建议结束对话）。**不得**把 `no_visible_chunks`
   悄悄当成「检索失败」重试。
7. **不动** `evals/intent_metrics.py` 与 `evals/intent/**` 的语料与指标口径。
8. **不改三份守卫清单**：`docs/fact-baseline.yaml`、`docs/code-anchors.yaml`、
   `docs/architecture-guard.yaml`。为了让门禁变绿去改清单，视为作弊。
9. **不扩大模型采纳面**，不碰 `.qoder/rules/adoption-surface.md` 的任何一条。
10. **评测读数由用户自己跑**：本轮只跑 pytest / ruff / mypy / 三份守卫；`evals/**` 的模型读数
    与 `evals/support_run.py` 的 62 条由用户侧复跑，交付物里如实登记「未跑」。

---

## 5. 验收标准（DoD）

1. **种类字段**：`RetrievedChunk.score_kind` 与 `ScoredChunk.score_kind` 存在、默认 `"sparse"`、
   取值 ∈ `SCORE_KINDS`；纯稀疏检索产出 `sparse`，混合检索产出 `fusion`，重排生效产出 `rerank`。
2. **顺序一致**：`tests/test_retrieval_channels.py` 新增断言——混合检索结果按 `score` 单调不增，
   且返回值等于 `rrf_fuse` 独立算出的融合分（不是任一路的原始分）。
3. **分尺度比较**：同一分数在不同种类下结论不同——稀疏 `4.9 < 5.0` 判无依据、稠密 `0.4 ≥ 0.35`
   判有依据、融合 `0.01 < 0.0164` 判无依据。三类各一条用例。
4. **单点判定**：`_below_evidence` 与 `actions.py` 的 `grounded` 都调 `no_evidence_threshold(kind)`；
   代码里不存在第二份「按种类取阈值」的映射（`grep` 可判）。
5. **词表同步不靠人工**：一条用例断言 `no_evidence_threshold` 接受的键集
   **等于** `set(SCORE_KINDS) | {"verdict"}`。检索侧将来加一种分数而阈值表没跟上，
   这条用例就红——这是 `app/core/policy.py` 只能写字面量（§2.3 第 2 条）的补偿。
6. **未知种类不静默**：`no_evidence_threshold("unknown")` 抛 `KeyError`（或有等价断言）。
7. **`verdict` 保行为**：`SELF_ANALYTICS` 的六个哨兵改成 `EvidenceScore(0.0 / 1.0, "verdict")`
   之后，`Draft.resolved` 与「是否转人工」的结论与本轮之前**逐条相同**——
   至少一条用例钉住「统计问答的正常答案不会因为新增种类判定而转人工」，
   一条钉住「SQL 被拒仍然转人工」。
8. **空检索三分**：`empty_query` / `no_visible_chunks` / `no_match` 各一条用例；
   `no_visible_chunks` 与 `no_match` 的**动作层回复**（`Draft.reply`）不同，且 `empty_reason`
   出现在 trace 属性里。**不得**把这条写成「客户会看到两句不同的话」——`no_evidence` 信号
   的 `reply_mode: replace` 会替换掉动作层那句（§2.4 第 1 条的代价登记）。
9. **旧读数不退**：`tests/test_handoff.py`、`tests/test_retrieval_channels.py` 既有用例、
   `tests/test_support.py`、`tests/test_support_conversation.py`、`tests/test_support_candidates.py`、
   `tests/test_policy_engine.py` 等全绿；`escalation.yaml` 与 `citation()` 键集逐字不变。
   **若有既有用例的读数真的变了**（例如某条 POLICY_QA 样本的首位 BM25 分 `< 5.0`），
   实现者**必须逐条报告**（用例名 + 改前读数 + 改后读数 + 是否属 §2.3 第 6 条登记的行为变化），
   不得默默改断言。
10. **T8-1 的加固行**：`tests/test_retrieval_channels.py` 里
    `assert hits[0].chunk_id in sparse_raw`（把「返回值确实是稀疏路的块」从注释变成断言）。
11. **门禁**：`pytest` 全绿（收集数与文档一致）、`ruff check`、`python -m mypy app`、
    三份守卫 24 passed；改了 `docs/spec/项目规格.md` 必须同批 `python scripts/build_manual.py`
    重生成说明书（源 sha256 必须相等）。
12. **文档同步**：`docs/spec/项目规格.md`（阈值表与 0.35 的旧说法）、`docs/guide/接口说明.md`
    （trace 字段与两个空检索原因）、`docs/guide/评测说明.md`（若引用旧阈值）、`README.md`（若引用）、
    `docs/records/智能体平台设想.md:698` 的「阈值清单」行——本轮由 13 项改到 **17 项**
    （13 → 16 是三个新旋钮；16 → 17 是补上一直漏列的 `escalation_clarification_rounds`）、
    `docs/下一步规划.md` §0.2 的 R8 行；ADR 登记 §2.3 第 6 条的行为变化、
    §2.2 第 5 条的重排头部顺序变化、以及 `verdict` 这第五种种类。

---

## 6. 与来源的对齐

| 来源 | 本文件对应 |
|---|---|
| `docs/下一步规划.md` §14.3「R8 分数种类」（分数与名次可相反、0.35 去比 BM25 十几分、稠密余弦另一套尺度） | §0、§2.2、§2.3 |
| §14.3 末句「检索为空时说『未检索到相关内容』，把『库里没有』和『我没检索到』合并了」 | §2.4 |
| §0.2 的 R8 行验收列（`RetrievedChunk` 带分数种类；`tests/test_retrieval_channels.py` 断言分数与名次不矛盾；`escalation_no_evidence_score` 按尺度比较；检索为空与库中无此条款可分） | §2.1、§2.2、§2.3、§2.4、§5 |
| `docs/guide/TicketPilot优化实施方案.docx` 的 R8 论证 | §0 的三条现状与 §2.3 的默认值依据（该 docx 是 2026-10-27 的时点论证，逐条结论已按代码核过，见 §1.2） |

> **本文件的三处判断不是 §14.3 原文要求**，实现时不得读成原文：① 稀疏默认值取 `5.0`
> （原文只说「按尺度分别比较」，没说具体值；`5.0` 取自 `DenseGate.min_top1_score` 的既有校准）；
> ② `fusion` / `rerank` 也各给一个阈值（原文只点了稀疏与稠密两把尺子）；
> ③ `no_visible_chunks` 也走转人工（原文只说两句含义不同，没说各自的处置）。
