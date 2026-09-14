# 历史文档与退役清单

2026-09-14 文档整理。此页登记从默认阅读入口退役的文档；它们继续作为历史记录、复现依据或数据来源使用。退役不表示结果无效、依赖已删除或实验未完成，也不改变原来的探索性/确认性表述。

## 状态与范围

以整理前 Git `1a019d4a88eac3b07717d1401de5011569602e96` 的 `docs/` 为范围，共 158 份受版本管理的文件：

| 状态 | 数量 | 用法 |
| --- | ---: | --- |
| 当前实验文档 | 5 | REG1 计划、结果、核验、来源与图件合同，见[当前导航](../README.md) |
| 同协议支持文档 | 11 | Transformer/CNN-Mamba 和 TCN 条件实验，见[当前导航](../README.md) |
| 导航与进度 | 2 | 文档入口和形成时记录 |
| 从默认入口退役 | 140 | 下方逐项列明，按需查阅 |

只整理导航和协作入口；以下 140 份文件保留原路径和原字节，实验代码、配置、预测、图件、来源清单和回执不因本次维护改动。未纳入该 Git 快照的新文件不在本次退役或提交范围内。

历史文档中的“当前”“新授权”“尚未训练”、运行命令、审阅建议及预算，仅说明形成时状态，不自动构成现行任务。复现某版时仍须按该版冻结计划和来源读取所需历史文件；新研究或修改实验设计需要明确的新任务。

## 常用历史入口

| 主题 | 入口 |
| --- | --- |
| 固定短期 TCN 的长期递推 | [结果](../ootang_tcn_independent_results.v1.0.md) |
| TCN 七步长滚动预测 | [结果](../ootang_tcn_results.v1.0.md) |
| v4.0 七步长比较 | [结果](../ootang_short_horizon_comparison_results.v4.0.md) |
| 神经初态 | [完整续评结果](../ootang_neural_initial_state_results.v1.1.md) |
| 30 日滚动候选 | [v3.0 结果](../ootang_rolling_probability_results.v3.0.md) · [C18](../ootang_rolling_probability_candidate.c18.md) |
| GP | [v1 结果](../ootang_bplus_gp_results.v1.md) · [加性 v2 结果](../ootang_bplus_additive_gp_results.v2.md) |
| ConvLSTM / PINN | [DIRECT v2.0](../ootang_convlstm_direct_results.v2.0.md) · [JOINT v2.4](../ootang_convlstm_joint_results.v2.4.md) · [PINN v2.3](../ootang_probability_pinn_results.v2.3.md) |
| 原 B+ 与四点实现 | [v1.1 方案](../ootang_bplus_probabilistic_experiment_plan.v1.1.md) · [实现](../ootang_bplus_probabilistic_implementation.v1_1.md) |
| 导师尾段要求及完整窗口复核 | [293 日复核](../ootang_293day_prediction_audit_2026-09-11.md) · [v2.2 形成时记录](../ootang_tail_scope_and_direction.v2.2.md) |

旧八点代理预警与当前四点位移任务分开解释，保留 `formal_warning_output=false` 边界；[旧报告编译说明](../../paper/README.md)仅用于追溯。不同信息条件、窗口和指标的结果不跨协议混排。各版负结果、未运行状态及数值/来源限制以原文为准。

## 完整退役目录

每项状态均为“从默认入口退役，保留历史参考”，共 140 项；目录名仅用于定位，不改判模型效果或文件内结论。

<details>
<summary>固定短期权重的 293 日独立递推（5 份）</summary>

- [ootang_tcn_independent_figure_contract.v1.0.md](../ootang_tcn_independent_figure_contract.v1.0.md)
- [ootang_tcn_independent_plan.v1.0.md](../ootang_tcn_independent_plan.v1.0.md)
- [ootang_tcn_independent_results.v1.0.md](../ootang_tcn_independent_results.v1.0.md)
- [ootang_tcn_independent_sources.v1.0.json](../ootang_tcn_independent_sources.v1.0.json)
- [ootang_tcn_independent_validation.v1.0.md](../ootang_tcn_independent_validation.v1.0.md)

</details>

<details>
<summary>1—7 日滚动预测与神经初态（12 份）</summary>

- [ootang_neural_initial_state_continuation.v1.1.md](../ootang_neural_initial_state_continuation.v1.1.md)
- [ootang_neural_initial_state_later_report.v1.2.md](../ootang_neural_initial_state_later_report.v1.2.md)
- [ootang_neural_initial_state_plan.v1.0.md](../ootang_neural_initial_state_plan.v1.0.md)
- [ootang_neural_initial_state_results.v1.0.md](../ootang_neural_initial_state_results.v1.0.md)
- [ootang_neural_initial_state_results.v1.1.md](../ootang_neural_initial_state_results.v1.1.md)
- [ootang_short_horizon_comparison_plan.v4.0.md](../ootang_short_horizon_comparison_plan.v4.0.md)
- [ootang_short_horizon_comparison_results.v4.0.md](../ootang_short_horizon_comparison_results.v4.0.md)
- [ootang_short_horizon_execution.v4.0.md](../ootang_short_horizon_execution.v4.0.md)
- [ootang_tcn_plan.v1.0.md](../ootang_tcn_plan.v1.0.md)
- [ootang_tcn_results.v1.0.md](../ootang_tcn_results.v1.0.md)
- [ootang_tcn_sources.v1.0.json](../ootang_tcn_sources.v1.0.json)
- [ootang_tcn_validation.v1.0.md](../ootang_tcn_validation.v1.0.md)

</details>

<details>
<summary>30 日滚动概率预测与 C1—C18（28 份）</summary>

- [ootang_rolling_consolidation.v2_2026-09-13.md](../ootang_rolling_consolidation.v2_2026-09-13.md)
- [ootang_rolling_consolidation_2026-09-13.md](../ootang_rolling_consolidation_2026-09-13.md)
- [ootang_rolling_frozen_validation_plan.v1.0.md](../ootang_rolling_frozen_validation_plan.v1.0.md)
- [ootang_rolling_frozen_validation_report.v1.0.md](../ootang_rolling_frozen_validation_report.v1.0.md)
- [ootang_rolling_probability_brief_2026-09-13.md](../ootang_rolling_probability_brief_2026-09-13.md)
- [ootang_rolling_probability_candidate.c10.md](../ootang_rolling_probability_candidate.c10.md)
- [ootang_rolling_probability_candidate.c11.md](../ootang_rolling_probability_candidate.c11.md)
- [ootang_rolling_probability_candidate.c12.md](../ootang_rolling_probability_candidate.c12.md)
- [ootang_rolling_probability_candidate.c13.md](../ootang_rolling_probability_candidate.c13.md)
- [ootang_rolling_probability_candidate.c14.md](../ootang_rolling_probability_candidate.c14.md)
- [ootang_rolling_probability_candidate.c15.md](../ootang_rolling_probability_candidate.c15.md)
- [ootang_rolling_probability_candidate.c16.md](../ootang_rolling_probability_candidate.c16.md)
- [ootang_rolling_probability_candidate.c17.md](../ootang_rolling_probability_candidate.c17.md)
- [ootang_rolling_probability_candidate.c18.md](../ootang_rolling_probability_candidate.c18.md)
- [ootang_rolling_probability_candidate.c2.md](../ootang_rolling_probability_candidate.c2.md)
- [ootang_rolling_probability_candidate.c3.md](../ootang_rolling_probability_candidate.c3.md)
- [ootang_rolling_probability_candidate.c4.md](../ootang_rolling_probability_candidate.c4.md)
- [ootang_rolling_probability_candidate.c5.md](../ootang_rolling_probability_candidate.c5.md)
- [ootang_rolling_probability_candidate.c6.md](../ootang_rolling_probability_candidate.c6.md)
- [ootang_rolling_probability_candidate.c8.md](../ootang_rolling_probability_candidate.c8.md)
- [ootang_rolling_probability_candidate.c9.md](../ootang_rolling_probability_candidate.c9.md)
- [ootang_rolling_probability_extension.v3.6.md](../ootang_rolling_probability_extension.v3.6.md)
- [ootang_rolling_probability_implementation.v3.0.md](../ootang_rolling_probability_implementation.v3.0.md)
- [ootang_rolling_probability_plan.v3.0.md](../ootang_rolling_probability_plan.v3.0.md)
- [ootang_rolling_probability_results.v3.0.md](../ootang_rolling_probability_results.v3.0.md)
- [ootang_rolling_probability_scale_study.v3.4.md](../ootang_rolling_probability_scale_study.v3.4.md)
- [ootang_rolling_route_review_2026-09-13.md](../ootang_rolling_route_review_2026-09-13.md)
- [ootang_rolling_transfer_decision.v3.3.md](../ootang_rolling_transfer_decision.v3.3.md)

</details>

<details>
<summary>B+ 与 GP（5 份）</summary>

- [ootang_bplus_additive_gp_plan.v2.md](../ootang_bplus_additive_gp_plan.v2.md)
- [ootang_bplus_additive_gp_results.v2.md](../ootang_bplus_additive_gp_results.v2.md)
- [ootang_bplus_gp_execution_addendum.v1.1.md](../ootang_bplus_gp_execution_addendum.v1.1.md)
- [ootang_bplus_gp_plan.v1.md](../ootang_bplus_gp_plan.v1.md)
- [ootang_bplus_gp_results.v1.md](../ootang_bplus_gp_results.v1.md)

</details>

<details>
<summary>早期四点物理引导、ConvLSTM 与 PINN（67 份）</summary>

- [ootang_bplus_balanced_origin_plan.v1.21.md](../ootang_bplus_balanced_origin_plan.v1.21.md)
- [ootang_bplus_balanced_origin_results.v1.21.md](../ootang_bplus_balanced_origin_results.v1.21.md)
- [ootang_bplus_diagnostics_plan.v1.2.md](../ootang_bplus_diagnostics_plan.v1.2.md)
- [ootang_bplus_diagnostics_results.v1.2.md](../ootang_bplus_diagnostics_results.v1.2.md)
- [ootang_bplus_error_structure_protocol.v1.5.md](../ootang_bplus_error_structure_protocol.v1.5.md)
- [ootang_bplus_error_structure_results.v1.5.md](../ootang_bplus_error_structure_results.v1.5.md)
- [ootang_bplus_history_availability_plan.v1.16.md](../ootang_bplus_history_availability_plan.v1.16.md)
- [ootang_bplus_history_availability_results.v1.16.md](../ootang_bplus_history_availability_results.v1.16.md)
- [ootang_bplus_history_completion_plan.v1.16.1.md](../ootang_bplus_history_completion_plan.v1.16.1.md)
- [ootang_bplus_history_diagnostics_plan.v1.18.md](../ootang_bplus_history_diagnostics_plan.v1.18.md)
- [ootang_bplus_history_diagnostics_results.v1.18.md](../ootang_bplus_history_diagnostics_results.v1.18.md)
- [ootang_bplus_history_learning_plan.v1.17.md](../ootang_bplus_history_learning_plan.v1.17.md)
- [ootang_bplus_history_learning_results.v1.17.md](../ootang_bplus_history_learning_results.v1.17.md)
- [ootang_bplus_increment_plan.v1.3.md](../ootang_bplus_increment_plan.v1.3.md)
- [ootang_bplus_increment_results.v1.3.md](../ootang_bplus_increment_results.v1.3.md)
- [ootang_bplus_input_transfer_plan.v1.22.md](../ootang_bplus_input_transfer_plan.v1.22.md)
- [ootang_bplus_input_transfer_results.v1.22.md](../ootang_bplus_input_transfer_results.v1.22.md)
- [ootang_bplus_model_route_discussion.v1.md](../ootang_bplus_model_route_discussion.v1.md)
- [ootang_bplus_optimization_review.v1.4.md](../ootang_bplus_optimization_review.v1.4.md)
- [ootang_bplus_optimization_selection_plan.v1.4.md](../ootang_bplus_optimization_selection_plan.v1.4.md)
- [ootang_bplus_optimization_selection_results.v1.4.md](../ootang_bplus_optimization_selection_results.v1.4.md)
- [ootang_bplus_origin_gradients_plan.v1.20.md](../ootang_bplus_origin_gradients_plan.v1.20.md)
- [ootang_bplus_origin_gradients_results.v1.20.md](../ootang_bplus_origin_gradients_results.v1.20.md)
- [ootang_bplus_origin_learning_plan.v1.19.md](../ootang_bplus_origin_learning_plan.v1.19.md)
- [ootang_bplus_origin_learning_results.v1.19.md](../ootang_bplus_origin_learning_results.v1.19.md)
- [ootang_bplus_pinn_consistency_plan.v1.10.md](../ootang_bplus_pinn_consistency_plan.v1.10.md)
- [ootang_bplus_pinn_consistency_results.v1.10.md](../ootang_bplus_pinn_consistency_results.v1.10.md)
- [ootang_bplus_pinn_equation_contract.v1.8.md](../ootang_bplus_pinn_equation_contract.v1.8.md)
- [ootang_bplus_pinn_equation_validation.v1.8.md](../ootang_bplus_pinn_equation_validation.v1.8.md)
- [ootang_bplus_pinn_substep_audit_plan.v1.8.md](../ootang_bplus_pinn_substep_audit_plan.v1.8.md)
- [ootang_bplus_pinn_substep_audit_results.v1.8.md](../ootang_bplus_pinn_substep_audit_results.v1.8.md)
- [ootang_bplus_probabilistic_experiment_plan.v1.1.md](../ootang_bplus_probabilistic_experiment_plan.v1.1.md)
- [ootang_bplus_probabilistic_experiment_plan.v1.md](../ootang_bplus_probabilistic_experiment_plan.v1.md)
- [ootang_bplus_probabilistic_implementation.v1_1.md](../ootang_bplus_probabilistic_implementation.v1_1.md)
- [ootang_bplus_rate_diagnostics_plan.v1.13.md](../ootang_bplus_rate_diagnostics_plan.v1.13.md)
- [ootang_bplus_rate_diagnostics_results.v1.13.md](../ootang_bplus_rate_diagnostics_results.v1.13.md)
- [ootang_bplus_rate_learning_plan.v1.12.md](../ootang_bplus_rate_learning_plan.v1.12.md)
- [ootang_bplus_rate_learning_results.v1.12.md](../ootang_bplus_rate_learning_results.v1.12.md)
- [ootang_bplus_sample_learning_plan.v1.5.md](../ootang_bplus_sample_learning_plan.v1.5.md)
- [ootang_bplus_sample_learning_results.v1.5.md](../ootang_bplus_sample_learning_results.v1.5.md)
- [ootang_bplus_sequence_interface_plan.v1.24.md](../ootang_bplus_sequence_interface_plan.v1.24.md)
- [ootang_bplus_sequence_interface_results.v1.24.md](../ootang_bplus_sequence_interface_results.v1.24.md)
- [ootang_bplus_sequence_learning_implementation.v1.25.md](../ootang_bplus_sequence_learning_implementation.v1.25.md)
- [ootang_bplus_sequence_learning_plan.v1.25.md](../ootang_bplus_sequence_learning_plan.v1.25.md)
- [ootang_bplus_sequence_learning_results.v1.25.md](../ootang_bplus_sequence_learning_results.v1.25.md)
- [ootang_bplus_shared_mechanics_plan.v1.11.md](../ootang_bplus_shared_mechanics_plan.v1.11.md)
- [ootang_bplus_shared_mechanics_results.v1.11.md](../ootang_bplus_shared_mechanics_results.v1.11.md)
- [ootang_bplus_state_pinn_implementation.v1.9.md](../ootang_bplus_state_pinn_implementation.v1.9.md)
- [ootang_bplus_state_pinn_plan.v1.9.md](../ootang_bplus_state_pinn_plan.v1.9.md)
- [ootang_bplus_state_pinn_results.v1.9.md](../ootang_bplus_state_pinn_results.v1.9.md)
- [ootang_bplus_synchronized_correction_plan.v1.7.md](../ootang_bplus_synchronized_correction_plan.v1.7.md)
- [ootang_bplus_synchronized_correction_results.v1.7.md](../ootang_bplus_synchronized_correction_results.v1.7.md)
- [ootang_bplus_teacher_condition_review.v1.23.md](../ootang_bplus_teacher_condition_review.v1.23.md)
- [ootang_bplus_teacher_condition_review_plan.v1.23.md](../ootang_bplus_teacher_condition_review_plan.v1.23.md)
- [ootang_bplus_teacher_transfer_plan.v1.6.md](../ootang_bplus_teacher_transfer_plan.v1.6.md)
- [ootang_bplus_teacher_transfer_results.v1.6.md](../ootang_bplus_teacher_transfer_results.v1.6.md)
- [ootang_bplus_temporal_decomposition_plan.v1.15.md](../ootang_bplus_temporal_decomposition_plan.v1.15.md)
- [ootang_bplus_temporal_decomposition_results.v1.15.md](../ootang_bplus_temporal_decomposition_results.v1.15.md)
- [ootang_bplus_temporal_features_plan.v1.14.md](../ootang_bplus_temporal_features_plan.v1.14.md)
- [ootang_bplus_temporal_features_results.v1.14.md](../ootang_bplus_temporal_features_results.v1.14.md)
- [ootang_convlstm_direct_plan.v2.0.md](../ootang_convlstm_direct_plan.v2.0.md)
- [ootang_convlstm_direct_results.v2.0.md](../ootang_convlstm_direct_results.v2.0.md)
- [ootang_convlstm_joint_plan.v2.4.md](../ootang_convlstm_joint_plan.v2.4.md)
- [ootang_convlstm_joint_results.v2.4.md](../ootang_convlstm_joint_results.v2.4.md)
- [ootang_probability_pinn_plan.v2.1.md](../ootang_probability_pinn_plan.v2.1.md)
- [ootang_probability_pinn_plan.v2.3.md](../ootang_probability_pinn_plan.v2.3.md)
- [ootang_probability_pinn_results.v2.3.md](../ootang_probability_pinn_results.v2.3.md)

</details>

<details>
<summary>历史方案讨论、来源与完整窗口复核（7 份）</summary>

- [ootang_293day_prediction_audit_2026-09-11.md](../ootang_293day_prediction_audit_2026-09-11.md)
- [ootang_method_selection_2026-09-12.md](../ootang_method_selection_2026-09-12.md)
- [ootang_residual_training_review_2026-09-13.md](../ootang_residual_training_review_2026-09-13.md)
- [ootang_route_review_2026-09-11.md](../ootang_route_review_2026-09-11.md)
- [ootang_route_review_2026-09-11_metrics.csv](../ootang_route_review_2026-09-11_metrics.csv)
- [ootang_route_review_2026-09-11_sources.json](../ootang_route_review_2026-09-11_sources.json)
- [ootang_tail_scope_and_direction.v2.2.md](../ootang_tail_scope_and_direction.v2.2.md)

</details>

<details>
<summary>旧八点预测、代理预警与论文草稿（14 份）</summary>

- [advisor_review_action_plan.md](../advisor_review_action_plan.md)
- [current_method_reference_register.md](../current_method_reference_register.md)
- [ngboost_shap_protocol.md](../ngboost_shap_protocol.md)
- [ootang_convlstm_elevation_fixed120_review.md](../ootang_convlstm_elevation_fixed120_review.md)
- [ootang_data_lineage_expert_review.md](../ootang_data_lineage_expert_review.md)
- [ootang_elevation_warning_expert_review.md](../ootang_elevation_warning_expert_review.md)
- [ootang_interval_calibration_expert_review.md](../ootang_interval_calibration_expert_review.md)
- [ootang_manuscript_methods_results_draft.md](../ootang_manuscript_methods_results_draft.md)
- [ootang_ngboost_auto_state_experiment_plan.md](../ootang_ngboost_auto_state_experiment_plan.md)
- [ootang_operational_run.md](../ootang_operational_run.md)
- [ootang_stable_segment_expert_review.md](../ootang_stable_segment_expert_review.md)
- [ootang_stage_results_package.md](../ootang_stage_results_package.md)
- [ootang_v4_acceleration_decision.md](../ootang_v4_acceleration_decision.md)
- [ootang_warning_data_dictionary.md](../ootang_warning_data_dictionary.md)

</details>

<details>
<summary>历史外部审阅材料（2 份）</summary>

- [ootang_web_route_review_prompt.md](../ootang_web_route_review_prompt.md)
- [review_materials/ootang_6pro_followup_20260911.md](../review_materials/ootang_6pro_followup_20260911.md)

</details>

## 快照与恢复

整理前 README、AGENTS 和完整导航可从 Git `1a019d4` 查看，无需在当前入口继续堆叠阶段记录。被历史来源清单引用的文件仍按其原路径核验。

[REG1 原交付清单](../../results/ootang_transformer_regularization_v1/20260914/final_manifest.json)冻结的是交付当时的快照，其中也包含 README、AGENTS 和 progress 的哈希。本次获授权的导航维护会改变这三份入口文件；原清单不回写，对应旧字节应从 `1a019d4` 核验，不能把新入口与旧快照的差异误判为实验数组被改动。其余研究产物按原清单核验。

此前已删除的未运行 v1.26 文件不在本次清单中，原件仍可由 Git `7fc5f29` 追溯；本次不恢复或重新启用。旧报告中的已取消交付、旧图件缺失等按形成时记录解释，不据导航整理重跑实验。
