# 项目文档导航

当前入口以 **2026-09-14 已完成的 Transformer 残差正则化验证**为准。结果、实现核验和效果判断分别阅读；历史计划中的“尚未训练”保留形成时含义。

## 最新交付：Transformer REG1

| 文档 / 产物 | 用途 |
| --- | --- |
| [结果报告](ootang_transformer_regularization_results.v1.0.md) | 完整开发/最终比较、局部收益、负结果和限制 |
| [四点图件](../figures/ootang_transformer_regularization_v1/20260914/README.md) · [五方法 CSV](../results/ootang_transformer_regularization_v1/20260914/analysis/phase_summary.csv) | 展示与逐项查数 |
| [冻结计划](ootang_transformer_regularization_plan.v1.0.md) · [配置](../config/ootang_transformer_regularization.v1_0.json) | 单一 λ=1 假设、固定训练和评价规则 |
| [来源清单](ootang_transformer_regularization_sources.v1.0.json) | 数据、旧模型和依赖的冻结来源 |
| [独立核验](ootang_transformer_regularization_validation.v1.0.md) · [最终回执](../results/ootang_transformer_regularization_v1/20260914/final_receipt.json) | 重载、数值、信息边界、交付及完成状态 |
| [图件合同](ootang_transformer_regularization_figure_contract.v1.0.md) | 本版已归档图件的显示与核验约定 |

当前结论：REG1 相对原版改善，但两阶段完整工作条件均未通过。最终平均均值误差略优于 B+，开发、逐点稳定性及主概率收益仍有限；本轮已结束。

## 同协议对照

以下两轮采用给定未来驱动、无预测段位移反馈的条件协议，作为当前结果的来源与比较依据。

| 实验 | 文档与产物 |
| --- | --- |
| Transformer / CNN-Mamba | [结果](ootang_sequence_conditional_results.v1.0.md) · [计划](ootang_sequence_conditional_plan.v1.0.md) · [配置](../config/ootang_sequence_conditional.v1_0.json) · [来源](ootang_sequence_conditional_sources.v1.0.json) · [核验](ootang_sequence_conditional_validation.v1.0.md) · [图件合同](ootang_sequence_figure_contract.v1.0.md) · [图件](../figures/ootang_sequence_conditional_v1/20260914/README.md) · [回执](../results/ootang_sequence_conditional_v1/20260914/final_receipt.json) |
| TCN 条件整段训练 | [结果](ootang_tcn_conditional_training_results.v1.0.md) · [计划](ootang_tcn_conditional_training_plan.v1.0.md) · [执行补充](ootang_tcn_conditional_execution.v1.0.md) · [来源](ootang_tcn_conditional_sources.v1.0.json) · [核验](ootang_tcn_conditional_validation.v1.0.md) · [图件合同](ootang_tcn_figure_contract.v1.0.md) · [图件](../figures/ootang_tcn_conditional_v1/20260914/README.md) · [回执](../results/ootang_tcn_conditional_v1/20260914/final_receipt.json) |

## 按需追溯

- [历史文档与退役清单](history/README.md)：140 份旧文档退出默认阅读范围，原文件保留，包含旧滚动/递推任务、ConvLSTM/PINN/GP、八点代理预警和审阅草稿。
- [进度记录](progress.md)：最近维护与历次形成时记录。旧预算、授权和运行命令不自动构成新任务。
- [协作与来源规则](../AGENTS.md)：导师资料、固定研究范围、信息边界及 Git 规则。

本页维护入口，不改写冻结研究文档、历史结论或交付回执。
