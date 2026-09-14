# 项目文档导航

当前入口以 **2026-09-15（本地时间）已完成的跨起点 α / 有限 λ 三步验证**为准。结果、实现核验和效果判断分别阅读；历史计划中的“尚未训练”保留形成时含义。

## 最新交付：跨起点 α / 有限 λ

| 文档/产物 | 用途 |
| --- | --- |
| [完整结果](ootang_transformer_temporal_results.v1.0.md) | 五点α、四点λ、历史锁定选择、四个完整293日比较与负结果 |
| [五张图及PNG/SVG](../figures/ootang_transformer_temporal_v1/20260914/README.md) · [56组CSV](../results/ootang_transformer_temporal_v1/20260914/analysis/phase_summary.csv) | 跨起点证据、最终四点导师版/完整范围图与原数值 |
| [冻结计划](ootang_transformer_temporal_plan.v1.0.md) · [配置](../config/ootang_transformer_temporal.v1_0.json) · [156来源](ootang_transformer_temporal_sources.v1.0.json) | 事前网格、时间/教师/信息边界、预算 |
| [核验](ootang_transformer_temporal_validation.v1.0.md) · [图件合同](ootang_transformer_temporal_figure_contract.v1.0.md) · [最终回执](../results/ootang_transformer_temporal_v1/20260914/final_receipt.json) | 228检查点/3339568数值/事件与图形复算、异常及实际完成状态 |

当前结论：三步完整完成，42新拟合16800更新；α/λ未建立稳定四点B+优势。最终选择α=1、λ=0，两流程同为9.279082mm RMSE，高于B+9.124173。固定半残差局部收益仍在，旧结论不改；不自动继续搜索或RL。

## 上一轮：固定半残差与区间校准

[结果](ootang_transformer_calibration_results.v1.0.md) · [计划](ootang_transformer_calibration_plan.v1.0.md) · [配置](../config/ootang_transformer_calibration.v1_0.json) · [来源](ootang_transformer_calibration_sources.v1.0.json) · [核验](ootang_transformer_calibration_validation.v1.0.md) · [七图](../figures/ootang_transformer_calibration_v1/20260914/README.md) · [回执](../results/ootang_transformer_calibration_v1/20260914/final_receipt.json)。原开发376日/最终293日的半残差及DIST90探索性收益、逐点失败和校准限制保持；与本轮窗口/误差池分开解释。

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
