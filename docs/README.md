# 项目文档导航

本页只维护入口，不重复记录各版结果或下一步。用户限定 ConvLSTM/PINN；导师 293 日已有预测
复核已完成，没有神经均值增益；尾段局部偏差可先不管，不再要求先指定日期。v2.1 及力探针继续暂缓。
v2.0 按原标准未达标并已停止。
旧计划的预算、运行命令和后续建议均不构成新的执行指令。

## 当前入口

| 文档 | 用途 |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | 当前范围、导师目标、协作与 Git 规则 |
| [293 日复核与当前方向](ootang_293day_prediction_audit_2026-09-11.md) | 来源、参数、日期、完整图表和指标；用户最新澄清与停止点 |
| [v2.2 需求记录](ootang_tail_scope_and_direction.v2.2.md) | 保留形成时的尾段要求，最新实施顺序以 293 日复核报告为准 |
| [6pro 补审交接记录](ootang_web_route_review_prompt.md) / [文本材料包](review_materials/ootang_6pro_followup_20260911.md) | 补审已返回，处理结果见复核报告；保留当时的原件包，不再作为待发送任务 |
| [v2.1 暂缓候选](ootang_probability_pinn_plan.v2.1.md) / [冻结配置](../config/ootang_probability_pinn.v2_1.json) | 原样保留的概率 PINN 设计，当前不直接进入实现；尚无训练结果 |
| [v2.0 结果](ootang_convlstm_direct_results.v2.0.md) / [冻结方案](ootang_convlstm_direct_plan.v2.0.md) | 5/8 严格均值改善，整体未达标；一次有界实验已停止 |
| [progress.md](progress.md) | 当前状态、最近维护和后续边界 |
| [路线复盘](ootang_route_review_2026-09-11.md) | 八轮学习的效果总表、失败证据和停止决定 |
| [汇总指标](ootang_route_review_2026-09-11_metrics.csv) / [来源记录](ootang_route_review_2026-09-11_sources.json) | 由既有指标计算的跨版本对照及 Git 来源 |

任务范围依当前用户请求与明确修正。某次实验的方法由对应冻结配置、源码和 manifest 决定，
数值以保存的指标与预测产物为准；文档汇总不能覆盖原结果。测试、实现完成与研究效果分别判断。

## 已完成四点实验：按需查证

以下均为历史实验依据，不是当前待办。旧文档内的“当前”“下一步”和预算只描述其形成时的状态。

| 内容 | 证据入口 |
| --- | --- |
| 原三组模型与时间协议 | [v1.1 冻结规格](ootang_bplus_probabilistic_experiment_plan.v1.1.md)、[实施与结果](ootang_bplus_probabilistic_implementation.v1_1.md) |
| B+ 标定、增量与内部选模 | [v1.2](ootang_bplus_diagnostics_results.v1.2.md)、[v1.3](ootang_bplus_increment_results.v1.3.md)、[v1.4](ootang_bplus_optimization_selection_results.v1.4.md) |
| ConvLSTM 样本与同步修正 | [v1.5](ootang_bplus_sample_learning_results.v1.5.md)、[v1.6 教师迁移](ootang_bplus_teacher_transfer_results.v1.6.md)、[v1.7](ootang_bplus_synchronized_correction_results.v1.7.md) |
| 状态 PINN 与共享力学 | [v1.8 方程依据](ootang_bplus_pinn_equation_contract.v1.8.md)、[v1.9](ootang_bplus_state_pinn_results.v1.9.md)、[v1.10](ootang_bplus_pinn_consistency_results.v1.10.md)、[v1.11](ootang_bplus_shared_mechanics_results.v1.11.md)、[v1.12](ootang_bplus_rate_learning_results.v1.12.md) |
| 修正需求、输入与历史来源 | [v1.13](ootang_bplus_rate_diagnostics_results.v1.13.md)、[v1.14](ootang_bplus_temporal_features_results.v1.14.md)、[v1.15](ootang_bplus_temporal_decomposition_results.v1.15.md)、[v1.16](ootang_bplus_history_availability_results.v1.16.md) |
| 历史学习、起点配对与目标平衡 | [v1.17](ootang_bplus_history_learning_results.v1.17.md)、[v1.18](ootang_bplus_history_diagnostics_results.v1.18.md)、[v1.19](ootang_bplus_origin_learning_results.v1.19.md)、[v1.20](ootang_bplus_origin_gradients_results.v1.20.md)、[v1.21](ootang_bplus_balanced_origin_results.v1.21.md) |
| 迁移、教师条件与连续预测 | [v1.22](ootang_bplus_input_transfer_results.v1.22.md)、[v1.23](ootang_bplus_teacher_condition_review.v1.23.md)、[v1.24](ootang_bplus_sequence_interface_results.v1.24.md)、[v1.25](ootang_bplus_sequence_learning_results.v1.25.md) |

每份结果链接到其执行前方案和原产物。已完成实验的计划、配置、源码及结果存在哈希和路径依赖，
继续按原路径保存。前期讨论与 v1 草案也被冻结 v1.1 引用，仅作设计演变证据，不是活动方案。
原始导师 PDF/ZIP 的位置与使用规则见 AGENTS；原件不改写。

## 历史八点预测与代理预警阶段

这一阶段的报告已于 2026-09-05 提交。八点 ConvLSTM–NGBoost–SHAP、H=7 代理标签和预警输出
均不属于当前四点 B+ 任务；保留其负结果和 `formal_warning_output=false` 边界。

| 内容 | 历史文档 |
| --- | --- |
| 已提交报告与结果包 | [报告编译说明](../paper/README.md)、[阶段结果包](ootang_stage_results_package.md)、[当时的方法底稿](ootang_manuscript_methods_results_draft.md) |
| 方法、标签与字段 | [NGBoost 实验记录](ootang_ngboost_auto_state_experiment_plan.md)、[SHAP 协议](ngboost_shap_protocol.md)、[规则基线](ootang_operational_run.md)、[数据字典](ootang_warning_data_dictionary.md) |
| 数据与模型审查 | [数据来源](ootang_data_lineage_expert_review.md)、[ConvLSTM 固定预算](ootang_convlstm_elevation_fixed120_review.md)、[高程与预警](ootang_elevation_warning_expert_review.md)、[区间](ootang_interval_calibration_expert_review.md)、[稳定段](ootang_stable_segment_expert_review.md) |
| 历史方法依据 | [加速度定义](ootang_v4_acceleration_decision.md)、[文献登记](current_method_reference_register.md)、[已退役导师意见快照](advisor_review_action_plan.md) |

这些文档包含独有的方法、引用或失败证据，部分被冻结配置直接引用，因此保留而不冒充当前指令。

## 已移除内容的追溯

- v1.26 未完成、未运行的计划、配置和草稿目录已删除，共九个文件；原字节保存在 Git `7fc5f29`。
- 清理前完整进度流水和旧导航可从 Git `cb08f62` 查看。当前进度只保留当前状态，不复制历史待办。
- 路线汇总的来源 JSON 是当时的来源记录；其中两份已删除的 v1.26 文件按 `source_git_commit:path`
  从 Git 查证，旧哈希不变。其余均值与概率汇总源文件继续保留。

例如只读查看旧进度：`git show cb08f62:docs/progress.md`。追溯不等于重新启动该版本。
