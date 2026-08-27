# 过程验证案例

`noip2018_process_cases.jsonl` 包含 6 题各 12 种场景，共 72 条构造计划：3 个有效过程、题意误读、约束遗漏、错误算法、证明缺口、复杂度超时、边界、溢出、实现不一致和“结果正确但过程无效”。

这些记录当前是 `TEMPLATE_NOT_EXECUTED`，用于冻结覆盖范围，不冒充真实实验。配置 Hy3 后，需要把模板物化为具体 Solver 输出与 C++，经 Docker 判题并由两名标注者盲标，再使用 `scripts/validate_localization.py` 计算定位准确率与误报构成。

