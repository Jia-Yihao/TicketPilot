# 实施计划：意图识别的真实语料评测

## 目标

用一份可追溯、人工标注的 held-out 语料替换「61 条按词表写、准确率 1.000」的循环论证，
让规则覆盖、模型缺口补位与误判代价第一次有可复算的数字。

## 架构

```text
.tmp/ext/ecom_dev.jsonl                     外部源（不 vendored，只在本地）
  └─ scripts/sample_intent_phrasings.py     按行号抽取 + 脱敏 + 校验
       └─ evals/intent/external_phrasings.jsonl   （进仓库：文本 + gold + provenance）
            evals/intent/external_manifest.json

evals/intent/authored_gap.jsonl             手写补位层（进仓库）
  └─ scripts/build_intent_set.py            合并 + 稳定排序 + 计数 + SHA256
       └─ evals/intent/intent_set.json       （进仓库：评测唯一输入）

evals/intent_metrics.py                     只调用 app.agent.support_agent.classify_support
  ├─ 规则读数：分层、分意图、UNKNOWN / wrong 分开
  ├─ 代价矩阵：四类互斥 + 槽位推导的澄清/任务/审批/金额暴露
  └─ --model：只在规则 UNKNOWN 子集问模型，报补位、编造、token、延迟
```

## 技术栈

Python 3.13、pytest、pydantic-ai（仅 `--model`）、现有 `app` 模块；不新增依赖。

## 规格路径

`openspec/changes/intent-corpus-eval-2026-10/specs/intent-evaluation/spec.md`

## 全局约束

- 只调用公开入口 `classify_support`、`classify`、`extract_order_id`、`CatalogService.resolve`；
  不读 `IntentCatalog.match` 的内部状态，不复制规则到评测脚本。
- gold 只来自人工标注；外部语料自带的标签、模型输出一律不写入 `gold`。
- 默认路径不依赖网络或模型；`--model` 必须显式开启，且只在规则 `UNKNOWN` 子集调用。
- 两层分开报告；不输出合并准确率。
- 旧 61 条 `support_set.json`、40 条 Golden、检索评测与回放口径不动。
- 所有新增文本用 UTF-8，行尾 LF；JSON 用稳定排序。

## 审查重点

1. 语料是否真的「外部、脱敏、人工标注」，而不是把规则词表换个写法。
2. 指标是否把 `UNKNOWN` 与读错分开，分母是否按层、按意图可见。
3. 代价矩阵四类是否互斥且求和等于总数；金额只算「暴露」不算「已退」。
4. 模型测量是否真的只在缺口子集，是否把标签空间外的用例错算成模型错误。
5. 文档中的每个数字能否由一条命令复现。

## 任务 1：外部语料抽取与人工标注

**文件**

- 新增 `evals/intent/external_selection.json`：行号 + gold + 备注（无文本）。
- 新增 `scripts/sample_intent_phrasings.py`：读源文件，按行号取用户原话，拒绝长数字串、
  URL、邮箱、空白与超长文本；校验每条文本与源文件逐字一致；写
  `evals/intent/external_phrasings.jsonl` 与 `external_manifest.json`（源 SHA256、
  样本 SHA256、来源 URL、许可证、抽取规则）。
- 新增 `evals/intent/external_phrasings.jsonl`（生成物，进仓库）。
- 新增 `evals/intent/external_manifest.json`（生成物，进仓库）。

**测试**：`tests/test_intent_eval.py::test_external_selection_lines_resolve_and_are_deidentified`、
`::test_external_manifest_records_source_and_license`。

**验证**：`python scripts/sample_intent_phrasings.py --source .tmp/ext/ecom_dev.jsonl --check`
输出 `EXTERNAL OK`。

## 任务 2：补位层与数据集生成

**文件**

- 新增 `evals/intent/authored_gap.jsonl`：12 个意图各 12 条，写明 `label_source: human`
  与 `note`（标注依据）；`SELF_ANALYTICS` 与 `HUMAN_HANDOFF` 在外部层缺席/偏少的原因写进
  `intent_set.json` 的 `layer_notes`。
- 新增 `scripts/build_intent_set.py`：合并两层、稳定排序、校验标签与每意图支撑数、
  写 `evals/intent/intent_set.json`；`--check` 重算并与已提交文件逐字比较。
- 新增 `evals/intent/intent_set.json`（生成物，进仓库）。

**测试**：`::test_intent_set_shape_and_per_intent_support`、`::test_no_query_is_a_verbatim_cue`、
`::test_build_intent_set_check_is_stable`。

**验证**：`python scripts/build_intent_set.py --check` 输出 `INTENT SET OK`。

## 任务 3：规则指标与代价矩阵

**文件**

- 新增 `evals/intent_metrics.py`：
  - `_classify(text)`：`classify` → `classify_support`，与生产调用逐参数一致；
  - `_layer_report`：n、coverage、accuracy、unknown、wrong、per-intent 行；
  - `_cost_matrix`：`correct` / `write_intent_error` / `read_intent_error` / `false_unknown`，
    按目录槽位推导 `clarifications` / `tasks` / `approvals` / `amount_exposed_cents`；
  - `_cue_overlap`：外部层包含 gold 意图 cue 的比例；
  - `--json` 输出逐用例行；`--enforce` 在结构不变量失败时退出 1。
- 更新 `Makefile`（若存在对应目标）与 `README.md` 的命令清单。

**测试**：`::test_metrics_separate_unknown_from_wrong`、`::test_cost_matrix_is_exhaustive`、
`::test_metrics_only_call_public_classifier`、`::test_cue_overlap_is_reported_not_scored`。

**验证**：`python evals/intent_metrics.py` 输出两层与代价矩阵；JSON 可逐条对照。

## 任务 4：模型缺口测量

**文件**

- 扩展 `evals/intent_metrics.py` 的 `--model`：
  - 只对规则 `UNKNOWN` 的行调用 `IntentAgent.parse`；
  - 报 `coarse_intent_accuracy`（6 标签可映射的用例）、`out_of_label_space`、
    `slot_fill`、`order_id_fabrications`、`amount_fabrications`、`calls`、`tokens`、`latency_ms`；
  - 模型失败单独计数，不得当作模型答错。
- 新增 `tests/test_intent_eval.py` 的桩件用例：模型只在缺口被调用、非缺口 0 调用、
  编造为 0 的判定、标签空间外不记错。

**验证**：`python evals/intent_metrics.py --model --model-name qwen/qwen3-8b --json .tmp/intent_model.json`
在本机 LM Studio 上跑一次，记录读数；离线测试用桩件。

## 任务 5：测试与门禁

**文件**

- 新增 `tests/test_intent_eval.py`（预计 14–18 条）。
- 更新 `docs/fact-baseline.yaml` 与 `tests/test_docs_consistency.py` 的规模读数
  （测试数、测试文件数）。

**验证**

```bash
python -m pytest -q
ruff check app tests scripts evals alembic
python -m mypy app
python -m pytest tests/test_architecture_guard.py tests/test_docs_consistency.py
python scripts/build_intent_set.py --check
python evals/intent_metrics.py --enforce
```

## 任务 6：证据与文档

**文件**

- 新增 `docs/adr/ADR-028-意图语料评测.md`：为什么做、语料分层与标注规则、
  实测读数、模型标签空间限制、没有做到的事。
- 更新 `docs/guide/评测说明.md`、`docs/spec/项目规格.md`（新增意图评测节与规模读数）、
  `README.md`、`docs/下一步规划.md`（P1 标为已完成并写清剩余边界）、
  `docs/records/智能体平台设想.md`、`docs/guide/范围与边界.md`、`CONTRIBUTING.md`。
- 重生成 `docs/spec/TicketPilot项目说明书.docx`（`make manual` 或等价命令）。

**验证**：文档守卫 17 条通过；说明书 `MANUAL OK`；每个数字能在命令输出中找到。

## 任务 7：独立审查与返工

提交 reviewer 按用户级 8 字段退回规则审查；阻塞/严重项返工到复验通过。
审查重点见「审查重点」五条。
