# 过程评估方法

Solver 输出固定的步骤 ID、依赖、不变量、依据、复杂度、边界条件、C++ 和代码映射。Algorithm Critic 不看代码评审材料之外的 Code Critic 意见，Code Critic 不看 Algorithm Critic 意见；两者在确定性 Judge 之前并行盲审。

步骤 verdict 为 `SUPPORTED`、`UNSUPPORTED`、`CONTRADICTED`、`NOT_ASSESSABLE`。首错定位优先取拓扑顺序最早的 `CONTRADICTED`，再融合 CE/WA/TLE/MLE/RE/OLE/IO 冲突证据。测试 AC 但存在过程矛盾时标为 `RESULT_CORRECT_PROCESS_INVALID`。

有效性验证以人工盲标为真值：错误样本计算首错步骤完全匹配率；最终答案正确且被系统报过程问题的样本由人工复核，分别报告真实问题比例和误报比例。任何尚未执行的 Hy3 实验必须标注 `NOT_RUN`，不得把合成样本的预期标签冒充真实结果。

