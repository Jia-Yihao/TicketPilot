# P5 意图缺口闭环

## 为什么

`scripts/sample_traces.py` 只是把 bad case 写成 JSONL，人工读完就结束：同一句漏判会在
多个周期重复入队，没人能回答「这一轮补了什么、覆盖率涨了多少」。P1 已经有稳定语料与
指标，缺的是一条从「漏掉的句子」到「人工确认的用例」再到「重跑指标」的可复现路径。

## 改什么

1. 新增缺口队列 `evals/intent/gap_queue.json`：按规范化文本去重，租户只存哈希；
   每条记录带 `OPEN` / `CLOSED` / `IGNORED` 状态、首次/最近出现周期与观测次数。
2. 新增 `evals/intent_gaps.py` + `scripts/manage_intent_gaps.py`：抽样入库、模型提议、
   人工确认关闭 / 忽略、生成周期报告。模型提议只进 `proposals`，不改 `gold`。
3. 人工确认关闭的缺口写入 `evals/intent/gap_closures.jsonl`，由
   `scripts/build_intent_set.py` 合并进同一份 held-out 语料（`authored_gap` 层），
   不另造分母。
4. 报告 `evals/intent/gap_report.json` 固定回答：新增用例数、关闭 / 忽略 / 仍开放数、
   两层覆盖率与 `UNKNOWN` 的变化、旧门槛是否退化。

## 不改什么

- 不让模型自动改词表；`app/agent/intents.yaml` 与 `routing.py` 仍是人工 review 的确定性资产。
- 不把 `UNKNOWN` 降到 0 当目标；有些句子本来就该转人工。
- 不改已有 `support_set.json` / `golden_set.json` 的门槛。

## 验收

- 同一份输入重跑，队列与报告逐字一致。
- 没有 `--confirm-human` 不能产生 `gold` 或写入语料。
- 关闭一条缺口后，`build_intent_set.py --check` 通过，指标脚本读到的用例数 +1。
