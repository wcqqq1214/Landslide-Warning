# 项目文档导航

当前入口以 **2026-09-15（本地时间）已完成的 Transformer 半残差与区间校准验证**为准。结果、实现核验和效果判断分别阅读；历史计划中的“尚未训练”保留形成时含义。

## 最新交付：半残差与区间校准

| 文档 / 产物 | 用途 |
| --- | --- |
| [结果报告](ootang_transformer_calibration_results.v1.0.md) | 半残差对照、完整概率比较、逐点失败和下一步判断 |
| [七张四点图件](../figures/ootang_transformer_calibration_v1/20260914/README.md) · [完整 36 组 CSV](../results/ootang_transformer_calibration_v1/20260914/analysis/phase_summary.csv) | 参考纵轴/完整范围图与逐项查数 |
| [冻结计划及图件合同](ootang_transformer_calibration_plan.v1.0.md) · [配置](../config/ootang_transformer_calibration.v1_0.json) | 固定 0.5 半残差、三校准规则、开发锁定和停止条件 |
| [来源清单](ootang_transformer_calibration_sources.v1.0.json) | 1,012 项数据、旧模型与依赖来源 |
| [独立核验](ootang_transformer_calibration_validation.v1.0.md) · [最终回执](../results/ootang_transformer_calibration_v1/20260914/final_receipt.json) | 重载、数值、信息边界、图件与交付状态 |

当前结论：半残差复现大部分均值改善；距离匹配的最终概率收益也适用于 B+，尚无稳定神经增益。开发/逐点保护失败，开发选择不改；新增训练 0，本轮结束，不追加 λ 搜索或 RL。

## 同协议对照

以下两轮采用给定未来驱动、无预测段位移反馈的条件协议，作为当前结果的来源与比较依据。

| 实验 | 文档与产物 |
| --- | --- |
| Transformer REG1 | [结果](ootang_transformer_regularization_results.v1.0.md) · [计划](ootang_transformer_regularization_plan.v1.0.md) · [配置](../config/ootang_transformer_regularization.v1_0.json) · [来源](ootang_transformer_regularization_sources.v1.0.json) · [核验](ootang_transformer_regularization_validation.v1.0.md) · [图件合同](ootang_transformer_regularization_figure_contract.v1.0.md) · [图件](../figures/ootang_transformer_regularization_v1/20260914/README.md) · [回执](../results/ootang_transformer_regularization_v1/20260914/final_receipt.json) |
| Transformer / CNN-Mamba | [结果](ootang_sequence_conditional_results.v1.0.md) · [计划](ootang_sequence_conditional_plan.v1.0.md) · [配置](../config/ootang_sequence_conditional.v1_0.json) · [来源](ootang_sequence_conditional_sources.v1.0.json) · [核验](ootang_sequence_conditional_validation.v1.0.md) · [图件合同](ootang_sequence_figure_contract.v1.0.md) · [图件](../figures/ootang_sequence_conditional_v1/20260914/README.md) · [回执](../results/ootang_sequence_conditional_v1/20260914/final_receipt.json) |
| TCN 条件整段训练 | [结果](ootang_tcn_conditional_training_results.v1.0.md) · [计划](ootang_tcn_conditional_training_plan.v1.0.md) · [执行补充](ootang_tcn_conditional_execution.v1.0.md) · [来源](ootang_tcn_conditional_sources.v1.0.json) · [核验](ootang_tcn_conditional_validation.v1.0.md) · [图件合同](ootang_tcn_figure_contract.v1.0.md) · [图件](../figures/ootang_tcn_conditional_v1/20260914/README.md) · [回执](../results/ootang_tcn_conditional_v1/20260914/final_receipt.json) |

## 按需追溯

- [历史文档与退役清单](history/README.md)：140 份旧文档退出默认阅读范围，原文件保留，包含旧滚动/递推任务、ConvLSTM/PINN/GP、八点代理预警和审阅草稿。
- [进度记录](progress.md)：最近维护与历次形成时记录。旧预算、授权和运行命令不自动构成新任务。
- [协作与来源规则](../AGENTS.md)：导师资料、固定研究范围、信息边界及 Git 规则。

本页维护入口，不改写冻结研究文档、历史结论或交付回执。
