# 提案：补钉 rerank 默认关闭守卫（A）与显式登记 analytics.route 耗时缺口（B）

## 变更 id

`close-rerank-guard-and-route-latency-debt-2026-11`

## 背景

用户在主会话的指令：「你看那些欠账有什么能解决就先解决，解决不了的就记在文档里即可」。
本轮从登记在册的欠账里分诊出**两处可修**，其余只记不修（不在本变更范围内）。

两项互不依赖、写文件面不相交，可并行。

## 口径来源（executor 必须逐字对照）

| 项 | 口径文件与条款 |
|---|---|
| A | `.qoder/rules/benchmark-harness.md` §5.1 末段「当前缺口」、§7 第 8 条 |
| B | `.qoder/rules/observability-span-and-link.md` §2.1 第 7 条、§3 第 4 条、§4.8.c、§5 第 1 条 |
| 通用 | `agents.md`（提交前三条命令、通过须附本轮实际输出）、`.qoder/rules/task-order.md` §1/§3/§4 |
| 流程 | 用户级 `c:/users/15237/.qoder-cn/agents.md`（organizer→executor→reviewer、退回通知 8 字段、DoD） |

## 本轮实测事实（2026-11，工作树读数；行号只作定位提示）

### A 项

1. `app/core/config.py:76` 现值 `retrieval_rerank_enabled: bool = False`（口径写的 `:70` 已漂）。
2. `.env.example:59` **确实**写着 `RETRIEVAL_RERANK_ENABLED=false`（主会话先前「未命中」系工具默认跳过点文件所致，口径 §7-8 的现状描述成立）。
3. `evals/rerank_latency.py:249` 现串 `print(f"verdict             {'WITHIN BUDGET' if within_budget else 'OVER BUDGET'}")`。
4. `grep -rnE "retrieval_rerank_enabled|OVER BUDGET" tests/` 命中 **0** → 守卫缺口为真。
5. 定性锚点行（含 `OVER BUDGET` 且含 `默认关闭`，同行共现）：`README.md:200`、`README.md:428`、
   `docs/下一步规划.md:2529`。口径 §7-8 写的 `README.md:199` / `:423` / `docs/下一步规划.md:290` **均已漂移**，
   实现与测试一律按字符串共现定位，不得写死行号。

### B 项（`app/sql/service.py` 逐行实读）

四个 `_trace_route` 调用点及其作用域内的起点现状：

| 调用点 | 位置 | 该处「路由段」是否有真实起点 | 说明 |
|---|---|---|---|
| 1 | `:120`（拒答分支） | 无 | 前一行 `:117-119` 才调 `self.router.route(...)`，作用域内无 `perf_counter()` |
| 2 | `:138`（UNKNOWN_TABLE） | 无 | `decision` 在 `:137` 就地构造，**根本没有路由工作** |
| 3 | `:166`（SQL 路径） | 无 | `decision` 取自 `:155`；`:202` 的 `started` 在其**之后**且量的是执行段 |
| 4 | `:305`（`_answer_prefetch` 内） | 有，但**不同段** | `:285` 的 `started` 量的是 `:286-288` 的 `prefetch.build(...)`；同一段时长已被 `analytics.query`(`:326`) 与 `analytics.prefetch`(`:339`) 两个 span 各记一次 |

## 编排裁定

### 裁定 1：A 项走「补钉一条 tests/ 用例」

口径 §5.1 已把决定权交给实现轮（「是否补钉由实现轮按最小范围决定」）。缺口性质是「文档定性 + 配置默认值
无人断言」，用**一条只读断言用例**即可钉住，代价最小、零生产代码改动 → 补。

### 裁定 2：B 项**改判走路线二（显式登记缺口），不走「补真实起点」**

派单前实地核验推翻了推荐路线的可行性，理由逐条：

1. 推荐路线要求「四个调用点都拿到同源的真实起点」。调用点 1/3 可以给（在 `router.route()` 前取一次
   `perf_counter()`、终点取在 `trace.add` 之前）；但调用点 2 没有任何路由工作可量，硬写只会得到一个
   恒等于 0 的装饰性读数。
2. **决定性障碍在调用点 4**：它的 `trace.add` 发生在 prefetch 构建**之后**。若把起点放在路由段，
   则窗口必然包含整段 prefetch，等于把「路由段」与「预派段」混成一个读数，并与 `analytics.query` /
   `analytics.prefetch` 两个 span 的 `latency_ms` 重复计量同一段工作 —— 违反口径 §1.1「段」的定义
   （可独立变慢、用于定位慢在哪）与 §2.1 第 4 条（起点必须是该段工作真正开始的那一刻）。
   若把起点放在 `:285`（prefetch 起点），则与 1/3 的语义不同源，正是 §2.1 第 7 条禁止的
   「事后另起一个计时点得到与其他调用点不同源的数」。
3. 因此四处**无法**统一成同源形状。派单简报自己写明：「如果两条路的起点本质上不同源，就改判走第二条路
   （显式登记缺口），不要为了凑读数硬编」——本裁定即采纳该句。
4. 路线二**零生产代码改动**，且 §2.1 第 7 条与 §5 第 1 条本来就把「在交付说明里登记为明确缺口」列为
   合法出口之一；§4.8.c 也已把它登记为「已登记的实现边界」。合规且代价最小。

### 裁定 3：文档落点避开在途改动

- **不碰** `docs/spec/项目规格.md`、`README.md`（工作树已有未提交的收尾 re-cite 批次，含 §16.6 观测面改写）。
- B 项缺口登记落 `docs/下一步规划.md` §14.6（该文件工作树**干净**，登记行追加在 §14.6 内，
  位于已登记锚点行 `:30` 与 `:119` **之后**，不影响它们）。
- 事实基线的锚点是「**字符串 anchor + 正则**」两件套（`docs/fact-baseline.yaml` 的 claim 结构 +
  `tests/test_docs_consistency.py` 的整行唯一命中校验），**不是**硬行号；因此插入行不会打红守卫，
  但**新增句子不得复现已登记锚点句**（如「当前规模事实」「| 测试 |」），否则唯一命中被破坏。

## 非目标（本变更一律不做）

- 不改任何生产代码默认值、不改 `app/sql/service.py`、不改 `evals/**`、不改 `.env.example`。
- 不改三份守卫清单（`docs/fact-baseline.yaml` / `docs/code-anchors.yaml` / `docs/architecture-guard.yaml`）。
- 不在本轮改文档里的测试读数（`test_py_files` / `test_collected` 会因新增用例文件漂移，
  按 `task-order.md` §4「全量基线只跑一次」留待收尾轮统一 re-cite）。
- 不跑 `evals/**` 的模型读数（项目惯例：由用户本机模型复跑）。
- 不重开 R9/F-1/R7/R10 已定的任何其他裁决。
