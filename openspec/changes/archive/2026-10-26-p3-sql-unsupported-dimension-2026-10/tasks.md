# P3-a 任务清单

## 1. 计划器

- [x] 1.1 识别不支持维度并给出 `UNSUPPORTED_DIMENSION`，不产生计划（计划任务 1）

## 2. 台账与对话层

- [x] 2.1 `NO_PLAN` / `UNSUPPORTED_DIMENSION` 分开记账，新 reason code 入册（计划任务 2）
- [x] 2.2 对话层拒绝并真实转人工，回复无状态桶（计划任务 3）

## 3. 回归

- [x] 3.1 status / month / day 与既有支持集逐条不退化（计划任务 4）

## 4. 评测与收口

- [x] 4.1 三条维度评测集落地，CI 与 `Makefile` 复用同一命令（计划任务 5）
- [x] 4.2 全量门禁通过，四份文档的 19 个 reason code 与说明书同步（计划任务 6）
