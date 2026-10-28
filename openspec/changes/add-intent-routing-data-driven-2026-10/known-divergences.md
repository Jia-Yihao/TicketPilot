# 读路径的既有分歧（本轮不修，留档）— 阶段 B / F-9

这是 `plan.md` §4.5 与 `tasks.md` 3.5 要求的留档物：`classify()` 的**读意图**与 `catalog.match()` 在改造前就不一致，本轮是等价重构，**一条都不修**；但每一组必须写清「为什么 `SupportPlan` 不受影响」。

与 `diff-list.md` 的分工：那份清单记的是**过程层写意图**差异（14 条，步骤 1 已归零）；本文件记的是**读路径**差异（228 条，原样保留）。

复算方式（只读）：

```bash
.venv/Scripts/python.exe openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py \
    --out .tmp/equiv_step1.json
# 读数行：读路径既有分歧=228；分组明细即本文件
```

---

## 1. 总表：12 组、228 条，以及为什么端到端不变

端到端判定发生在 `classify_support()`（`app/agent/support_agent.py:158-187`），三步顺序是：① catalog 命中 `collaboration` 行 → 直接用该行；② `classify()` 读出**写意图**且未被问句门槛挡住 → 用 `classify()` 的读数；③ 其余情况用 catalog 命中的行，catalog 没命中才是 `UNKNOWN`。

因此「读意图」这一面上 **catalog 本来就是赢家**：`classify()` 的读数只在第 ② 步（写意图）参与端到端判定。下面 12 组全是读意图，没有一组落在第 ② 步的适用域里——这就是「`SupportPlan` 不受影响」的统一理由，逐组只是它的不同形态。

| # | `classify` 读判定 → catalog 判定 | 条数 | 实际 `SupportPlan` | 由哪一步决定 | 为什么端到端不受影响 |
|---|---|---|---|---|---|
| 1 | `UNKNOWN` → 无行 | 164 | `UNKNOWN` | 第 ③ 步的 `spec is None` 分支 | 名义差异，不是判定差异：两侧都是「没读出来」，`classify` 给 `Intent.UNKNOWN`、catalog 给不出行，第 ③ 步照样回落 `SupportIntent.UNKNOWN` |
| 2 | `UNKNOWN` → `PRODUCT_QUERY` | 22 | `PRODUCT_QUERY` | 第 ③ 步 | `classify` 的词表不认商品问法，catalog 认；端到端一直取 catalog 行 |
| 3 | `POLICY_QA` → `REFUND_ELIGIBILITY` | 10 | `REFUND_ELIGIBILITY` | 第 ③ 步 | 即 T3：`classify` 把资格问句降级成 `POLICY_QA`，但降级结果从未被采纳 |
| 4 | `LOGISTICS_QUERY` → `DELIVERY_ISSUE` | 9 | `DELIVERY_ISSUE` | 第 ③ 步 | catalog 的 `DELIVERY_ISSUE`（priority 50）先于 `LOGISTICS_QUERY`（60）命中 |
| 5 | `UNKNOWN` → `SELF_ANALYTICS` | 6 | `SELF_ANALYTICS` | 第 ③ 步 | `classify` 无统计意图分支，catalog 行独享 |
| 6 | `UNKNOWN` → `DELIVERY_ISSUE` | 5 | `DELIVERY_ISSUE` | 第 ③ 步 | 同上，`classify` 完全没读出来 |
| 7 | `ORDER_QUERY` → `SELF_ANALYTICS` | 4 | `SELF_ANALYTICS` | 第 ③ 步 | 「我的订单一共几个」被 `classify` 读成查订单、catalog 读成统计 |
| 8 | `UNKNOWN` → `REFUND_STATUS` | 3 | `REFUND_STATUS` | 第 ③ 步 | catalog 的 15 条 `REFUND_STATUS` cues 覆盖 `classify` 未覆盖的说法 |
| 9 | `POLICY_QA` → `REFUND_STATUS` | 2 | `REFUND_STATUS` | 第 ③ 步 | 含退款字样但问的是到账，`classify` 停在政策问答 |
| 10 | `UNKNOWN` → `REFUND_ELIGIBILITY` | 1 | `REFUND_ELIGIBILITY` | 第 ③ 步 | T3 的另一形态（`classify` 什么都没读出来） |
| 11 | `UNKNOWN` → `HUMAN_HANDOFF` | 1 | `HUMAN_HANDOFF` | **第 ① 步** | collaboration 行先判（T4 要求的顺序），与 `classify` 无关 |
| 12 | `POLICY_QA` → `PRODUCT_QUERY` | 1 | `PRODUCT_QUERY` | 第 ③ 步 | 「保修多久」：`classify` 归政策，catalog 归商品 |

**合计 228 条，端到端 `SupportPlan.intent` 与 catalog 读数一致者占全部**（第 1 组两侧同义、第 11 组由第 ① 步决定、其余 10 组由第 ③ 步取 catalog 行）。

## 2. 残留影响面（诚实登记，不属于本轮）

`classify()` 的**读**意图虽然不改 `SupportPlan.intent`，但不是完全没有下游影响，两处必须写清，免得后来者以为「读意图随便改都行」：

1. **是否问模型**：`model_gate()`（`support_agent.py:113-131`）的 `gap_readable` 含一条 `route.intent is Intent.UNKNOWN and plan.intent is SupportIntent.ORDER_QUERY`。它读的是 `classify` 的读数；改 `classify` 的读分支会改变**调用次数**，不改变 `SupportPlan`。本轮不动 `model_gate()`（用户约束：`question` 出口属阶段 A）。
2. **候选集的 `rule:` 证据**：`app/agent/candidates.py` 把 `classify()` 的非 `UNKNOWN` 读数作为一条证据串。改读意图会改候选名单与桶，属可见副作用；本轮 cues 只补在写行上，读行的证据集合未变（步骤 1 的 `cue_overlap` 变化见 `diff-list.md` §1.1）。

采纳面（`support_agent.py:1511-1513`）仍只认 `Intent.REFUND_REQUEST`，本轮未动。

## 3. T2 的实测结论：优先级倒置在本语料上产生 0 条分歧

`plan.md` §3 的 T2 担心的是顺序倒置：

* `catalog`：`POLICY_QA` priority **40** 先于 `LOGISTICS_QUERY` priority **60**；
* `classify()`：`elif` 链把 `_LOGISTICS`（→ `LOGISTICS_QUERY`）放在 `_POLICY`（→ `POLICY_QA`）**之前**。

若照 catalog 的顺序把写意图迁过去就动到了读行顺序，346 条里会出现分歧。实测：346 条上 **`classify=LOGISTICS_QUERY` 且 `catalog=POLICY_QA` 的语料为 0 条**（第 4 组是 `LOGISTICS_QUERY → DELIVERY_ISSUE`，方向不同，与 T2 无关）。

**处理**：不为了对齐而改任何 `priority`，也不新增读行顺序规则——「哪种对齐与现状一致」的答案是**都不需要动**（零分歧即证据）。写意图迁移只依赖 `write_path: true` 行的先命中顺序，两行 priority 10 / 11 本就都在所有读行之前，天然与 `classify` 的 `if-elif` 首位一致。

## 4. T3 的实测结论：降级分支在端到端已是惰性的，维持现状

`classify()` 用 `_REFUND_ELIGIBILITY` 把资格问句读成 `POLICY_QA`（`routing.py:303-304`），而 catalog 有独立的 `REFUND_ELIGIBILITY` 行（priority 30，11 条 cues）。两者在 346 条上差出 11 条（第 3 组 10 条 + 第 10 组 1 条），但每一条的 `SupportPlan` **已经等于 catalog 的 `REFUND_ELIGIBILITY`**——降级结果从未被采纳。

**处理**：维持现状，不改 `_REFUND_ELIGIBILITY`、不试图用 YAML 表达「降级为 POLICY_QA」。这条降级要单独收口的话，属意图目录扩张或读意图重排，均超出本轮（用户约束：不新增意图行；plan §6.3）。

## 5. T4 的实测结论：collaboration 先判的顺序未动

第 11 组的 `gap-handoff-10`（「客服从哪里接入，我要真人对话」）由第 ① 步给出 `HUMAN_HANDOFF`——这正是「转人工先于写路径」的形态。步骤 1 与步骤 4 的改动面都不触碰 `classify_support()` 的三步顺序（`support_agent.py:179-187` 三段一行未改），并且 `TICKET_CREATE` 的 cues 里含 `转人工`/`人工客服`（逐字复制 `_TICKET`），与 `HUMAN_HANDOFF` 行的 cues 重叠：这类句子仍先被第 ① 步截走，写行没有抢到任何端到端判定（语料上第 11 组仅此 1 条，且它走的是 catalog 行而非 `classify` 的写分支）。

## 6. 逐条清单

以下按组重列每条语料原文与两侧读数。第 1 组（164 条）两侧同为「没读出来」，只点 id 不重列原文。

### 6.1 UNKNOWN → 无行｜SupportPlan=UNKNOWN｜164 条

两侧都是「没读出来」，逐条原文不重列，只点语料 id：`ext-delivery-01`, `ext-delivery-05`, `ext-delivery-07`, `ext-delivery-08`, `ext-delivery-10`, `ext-delivery-11`, `ext-delivery-15`, `ext-delivery-16`, `ext-delivery-17`, `ext-delivery-18`, `ext-delivery-22`, `ext-delivery-24`, `ext-delivery-26`, `ext-delivery-27`, `ext-handoff-01`, `ext-handoff-02`, `ext-handoff-03`, `ext-handoff-04`, `ext-handoff-06`, `ext-handoff-07`, `ext-handoff-08`, `ext-handoff-09`, `ext-handoff-10`, `ext-logistics-06`, `ext-logistics-07`, `ext-logistics-20`, `ext-logistics-21`, `ext-logistics-22`, `ext-logistics-23`, `ext-order-01`, `ext-order-02`, `ext-order-06`, `ext-order-10`, `ext-order-11`, `ext-order-12`, `ext-order-14`, `ext-order-15`, `ext-order-16`, `ext-policy-02`, `ext-policy-03`, `ext-policy-05`, `ext-policy-06`, `ext-policy-07`, `ext-policy-13`, `ext-policy-17`, `ext-policy-18`, `ext-product-01`, `ext-product-02`, `ext-product-04`, `ext-product-06`, `ext-product-07`, `ext-product-11`, `ext-product-12`, `ext-product-15`, `ext-product-16`, `ext-product-21`, `ext-product-25`, `ext-product-26`, `ext-product-27`, `ext-product-28`, `ext-refundreq-02`, `ext-refundreq-03`, `ext-refundreq-04`, `ext-refundreq-05`, `ext-refundreq-10`, `ext-refundreq-11`, `ext-refundreq-12`, `ext-refundreq-13`, `ext-refundreq-15`, `ext-refundstatus-03`, `ext-refundstatus-05`, `ext-refundstatus-12`, `ext-refundstatus-15`, `ext-unknown-01`, `ext-unknown-02`, `ext-unknown-03`, `ext-unknown-04`, `ext-unknown-05`, `ext-unknown-06`, `ext-unknown-07`, `ext-unknown-08`, `ext-unknown-09`, `ext-unknown-10`, `ext-unknown-11`, `ext-unknown-12`, `ext-unknown-13`, `ext-unknown-14`, `ext-unknown-15`, `ext-unknown-16`, `ext-unknown-17`, `ext-unknown-18`, `gap-delivery-01`, `gap-delivery-02`, `gap-delivery-03`, `gap-delivery-04`, `gap-delivery-06`, `gap-delivery-07`, `gap-delivery-10`, `gap-delivery-12`, `gap-eligibility-04`, `gap-eligibility-07`, `gap-eligibility-11`, `gap-eligibility-12`, `gap-fd54b3b92cad`, `gap-handoff-01`, `gap-handoff-02`, `gap-handoff-03`, `gap-handoff-04`, `gap-handoff-05`, `gap-handoff-06`, `gap-handoff-08`, `gap-handoff-09`, `gap-handoff-11`, `gap-handoff-12`, `gap-logistics-03`, `gap-logistics-04`, `gap-logistics-06`, `gap-logistics-07`, `gap-logistics-08`, `gap-logistics-09`, `gap-logistics-10`, `gap-order-01`, `gap-policy-02`, `gap-policy-04`, `gap-policy-07`, `gap-policy-11`, `gap-policy-12`, `gap-product-09`, `gap-product-10`, `gap-product-11`, `gap-refundreq-01`, `gap-refundreq-02`, `gap-refundreq-03`, `gap-refundreq-04`, `gap-refundreq-06`, `gap-refundreq-07`, `gap-refundreq-08`, `gap-refundreq-09`, `gap-refundreq-10`, `gap-refundreq-12`, `gap-refundstatus-02`, `gap-refundstatus-04`, `gap-refundstatus-09`, `gap-refundstatus-10`, `gap-refundstatus-11`, `gap-refundstatus-12`, `gap-ticket-01`, `gap-ticket-03`, `gap-ticket-06`, `gap-ticket-09`, `gap-ticket-11`, `gap-ticket-12`, `gap-unknown-01`, `gap-unknown-02`, `gap-unknown-03`, `gap-unknown-04`, `gap-unknown-05`, `gap-unknown-06`, `gap-unknown-07`, `gap-unknown-08`, `gap-unknown-09`, `gap-unknown-10`, `gap-unknown-11`, `gap-unknown-12`

### 6.2 UNKNOWN → PRODUCT_QUERY｜SupportPlan=PRODUCT_QUERY｜22 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| ext-product-03 | 多大尺寸 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-05 | 因为在家喂奶不太穿怎么用 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-08 | 单张尺寸是多大 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-09 | 12包装的尺寸是不是更大 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-10 | 材质不一样吗 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-13 | 哪款尺寸大些 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-14 | 跟这个尺寸一样吗 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-17 | 这个怎么用 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-18 | 无纺布是什么材质为啥叫无纺布 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-19 | 那纯棉是什么材质的呀直接棉花做的布 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| ext-product-20 | 这个尺寸是多大的 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-delivery-11 | 送来的不是我要的型号 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-eligibility-02 | 买错颜色了，还可以退换吗 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-eligibility-05 | 赠品要一起退吗，主商品可以退不 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-01 | 这款的承重是多少 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-02 | 有别的颜色可选吗 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-03 | 这个材质会不会过敏 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-04 | 机器怎么安装 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-05 | 尺寸 120 和 150 差多少 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-07 | 配件清单里有什么 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-product-08 | 这个型号支持蓝牙吗 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |
| gap-ticket-08 | 我要立案，商品和描述完全不符 | UNKNOWN | PRODUCT_QUERY | PRODUCT_QUERY |

### 6.3 POLICY_QA → REFUND_ELIGIBILITY｜SupportPlan=REFUND_ELIGIBILITY｜10 条（T3）

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| ext-eligibility-03 | 坏的多可以退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| ext-eligibility-04 | 拆开了一包可以退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| ext-eligibility-05 | 这个棉柔巾我收到了拆开一包试了试湿水效果不好可以退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| ext-eligibility-06 | 那咋办能退吗地址给我但是我打开了一包 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| ext-eligibility-07 | 我点错了能退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| ext-eligibility-08 | 东西买多了可以退吗没有用过 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| ext-eligibility-09 | 我小孩用了还过敏能退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| gap-eligibility-03 | 拆封后发现问题，能退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| gap-eligibility-09 | 包装丢了还能退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |
| gap-eligibility-10 | 定制款做错了，可以退吗 | POLICY_QA | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |

### 6.4 LOGISTICS_QUERY → DELIVERY_ISSUE｜SupportPlan=DELIVERY_ISSUE｜9 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| ext-delivery-03 | 这个没收到然后不是说说口水巾吗怎么都没有 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-06 | 其实我也不会那么无聊为了贪两包湿纸巾故意说没收到的诚信比较重要 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-09 | 我还没收到宝贝怎么写我的宝贝已收到周二01:39:51到达上海 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-12 | 我没收到 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-13 | 不是货没收到 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-19 | 我年前买的核桃为啥没收到 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-21 | 你好我的宝贝怎么这么长时间还没到货但是物流信息却显示早就派送了 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-refundstatus-09 | 我这边还没收到钱呀 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |
| gap-ticket-04 | 我要把快递丢件这件事报上去 | LOGISTICS_QUERY | DELIVERY_ISSUE | DELIVERY_ISSUE |

### 6.5 UNKNOWN → SELF_ANALYTICS｜SupportPlan=SELF_ANALYTICS｜6 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| gap-analytics-01 | 我上个月一共退了几笔 | UNKNOWN | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-02 | 我今年在你们店花了多少钱 | UNKNOWN | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-05 | 我一共买过多少件 | UNKNOWN | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-06 | 帮我统计一下我的退款金额合计 | UNKNOWN | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-08 | 这个账号累计消费了多少元 | UNKNOWN | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-11 | 我买过几次 | UNKNOWN | SELF_ANALYTICS | SELF_ANALYTICS |

### 6.6 UNKNOWN → DELIVERY_ISSUE｜SupportPlan=DELIVERY_ISSUE｜5 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| ext-delivery-02 | 对了那个外包装没有坏里面漏了瓶子也没有坏算破损吗 | UNKNOWN | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-04 | 还有那个盒子非常破损没有开很破反正要怎么处理呢亲 | UNKNOWN | DELIVERY_ISSUE | DELIVERY_ISSUE |
| ext-delivery-14 | 剛查到是假簽收淘寶海外倉查不到說他們未收到 | UNKNOWN | DELIVERY_ISSUE | DELIVERY_ISSUE |
| gap-ticket-05 | 帮我登记一下，我要追究这次延误 | UNKNOWN | DELIVERY_ISSUE | DELIVERY_ISSUE |
| gap-ticket-07 | 请把这次破损记成需要处理的案件 | UNKNOWN | DELIVERY_ISSUE | DELIVERY_ISSUE |

### 6.7 ORDER_QUERY → SELF_ANALYTICS｜SupportPlan=SELF_ANALYTICS｜4 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| gap-analytics-03 | 我的订单一共有几个 | ORDER_QUERY | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-09 | 我最近一周下了几个订单 | ORDER_QUERY | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-10 | 我所有订单加起来多少钱 | ORDER_QUERY | SELF_ANALYTICS | SELF_ANALYTICS |
| gap-analytics-12 | 我这几年的订单总额是多少 | ORDER_QUERY | SELF_ANALYTICS | SELF_ANALYTICS |

### 6.8 UNKNOWN → REFUND_STATUS｜SupportPlan=REFUND_STATUS｜3 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| ext-refundstatus-10 | 我另外个还没退呢现在去寄了 | UNKNOWN | REFUND_STATUS | REFUND_STATUS |
| ext-refundstatus-11 | 还没退吗 | UNKNOWN | REFUND_STATUS | REFUND_STATUS |
| ext-refundstatus-13 | 亲货发了款还没退呢 | UNKNOWN | REFUND_STATUS | REFUND_STATUS |

### 6.9 POLICY_QA → REFUND_STATUS｜SupportPlan=REFUND_STATUS｜2 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| ext-refundstatus-08 | 我的退款什么时候才能给我处理了 | POLICY_QA | REFUND_STATUS | REFUND_STATUS |
| gap-refundstatus-06 | 退款到账要多久，我已经等三天了 | POLICY_QA | REFUND_STATUS | REFUND_STATUS |

### 6.10 UNKNOWN → REFUND_ELIGIBILITY｜SupportPlan=REFUND_ELIGIBILITY｜1 条（T3 另一形态）

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| gap-eligibility-08 | 收到货十天了，还能不能退 | UNKNOWN | REFUND_ELIGIBILITY | REFUND_ELIGIBILITY |

### 6.11 UNKNOWN → HUMAN_HANDOFF｜SupportPlan=HUMAN_HANDOFF｜1 条（T4）

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| gap-handoff-10 | 客服从哪里接入，我要真人对话 | UNKNOWN | HUMAN_HANDOFF | HUMAN_HANDOFF |

这条由 `classify_support()` 第 ① 步（collaboration 行先判）决定，不是第 ③ 步。

### 6.12 POLICY_QA → PRODUCT_QUERY｜SupportPlan=PRODUCT_QUERY｜1 条

| 语料 id | 原文 | classify 读判定 | catalog `match()` 判定 | 实际生效的 `SupportPlan.intent` |
|---|---|---|---|---|
| gap-product-06 | 保修多久 | POLICY_QA | PRODUCT_QUERY | PRODUCT_QUERY |
