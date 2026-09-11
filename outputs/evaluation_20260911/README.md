# Hy3-ContestLens 评测证据包

生成时间：2026-09-11T07:15:43.769550+00:00

本目录由 `scripts/build_evaluation_package.py` 从 `runs/contestlens.sqlite3` 与各 run
目录只读生成。不会复制私有测试输入、标准输出、模型隐藏推理或密钥。

## 核心口径

- 运行记录：86 条数据库状态为 `COMPLETED` 且结果 JSON 可解析的记录。
- 答案正确：确定性 Judge 为 `AC`，共 64 条。
- 过程正确：融合诊断 `process_correct=true`，共 61 条。
- 严格成功：编译 OK、Judge AC 且过程正确，共 51 条。
- 题目覆盖：63 道可自动评测题均至少有一次闭环记录；
  53 道至少一次答案 AC，44 道至少一次严格成功。

## 文件说明

- `Hy3-ContestLens_评测结果与人工抽检.xlsx`：汇总、难度分层、运行明细、版本验证和可编辑人审台账。
- `Hy3-ContestLens_评测分析报告.md`：分析报告的 Markdown 版本。
- `../../output/pdf/Hy3-ContestLens_评测分析报告.pdf`：逐页渲染验证后的正式 PDF 报告。
- `complete_run_results.csv`：全部已完成工作流的运行级明细。
- `problem_coverage.csv`：按题目去重后的历史至少一次覆盖情况。
- `revision_validation_data.csv/jsonl`：初始与修复候选的版本级预测数据。
- `validation_sample.csv/jsonl`：10 条“先有问题、后严格成功”的配对验证样本。
- `manual_audit_records.csv`：含 Codex 独立证据预审和待双人人工签字字段的抽检台账。
- `manual_review_queue.jsonl`：便于标注工具导入的待人工复核队列。
- `summary_metrics.json`：报告与工作簿使用的机器可读汇总。

## 人工验证状态

`manual_audit_records.csv` 中的 `assisted_*` 仅是 AI 辅助独立复核，不是人工盲标。
`human_*` 字段故意留空。两名人工评审完成并裁决后，才能据此计算正式定位准确率和误报率。
这一区分用于避免把自动预审或历史模板冒充真实人工验证。
