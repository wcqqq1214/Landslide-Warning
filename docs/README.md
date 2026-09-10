# 项目文档导航

本页用于区分当前权威文档、辅助参考、历史记录和机器生成证据。它不是导师报告，也不改变任何
实验配置、数据划分、阈值、模型或结论。

## 权威顺序

发生冲突时按以下规则判断：

1. 当前用户请求及其明确修正决定任务范围；[`AGENTS.md`](../AGENTS.md) 规定项目协作、
   授权、验证和记录规则。旧导师要求已于 2026-09-05 退役；当前四点阶段依据为 2026-09-10
   更新的导师目标和 v1.1，不把旧预警任务带入新阶段。
2. 版本化 `config/`、对应源码以及运行 `manifest.json` 决定某次实验实际执行的方法；CSV/JSON
   结果决定可报告的数值，不以叙述性文档覆盖机器产物。
3. [`progress.md`](progress.md) 记录最近完成状态、当前限制和下一步。
4. 下列 canonical 文档负责把方法、结果与局限串成论文证据链。
5. reference 文档只能用于查证背景与决策过程，不得覆盖以上来源。

## 当前阶段：四点 B+ 概率位移预测

已完成 [v1.4 同前缀续算与训练内选模](ootang_bplus_optimization_selection_results.v1.4.md)。
训练目标续算小幅下降但未达既定梯度容差；内部选模的两个窗口预测一好一坏，严格逐点同时
改善 0/8，仍未稳定改善四点。实际新 nfev 12,470/14,800，没有新增神经训练。
[执行前检查](ootang_bplus_optimization_review.v1.4.md) 与
[固定方案](ootang_bplus_optimization_selection_plan.v1.4.md) 保留为版本依据；
代码为 `code/physics_guided_optimization_selection/`，两项结果分别存于 `results/ootang_bplus_v1_4/`。

前版 [v1.3 位移与增量目标对照](ootang_bplus_increment_plan.v1.3.md) 已完成，
[结果记录](ootang_bplus_increment_results.v1.3.md) 保留两个窗口平均预测误差变差、逐点同时改善
1/8 的结论，以及新拟合未优于已知可行参数的新目标值这一优化限制。
代码为 `code/physics_guided_increment/`，结果另存 `results/ootang_bplus_v1_3/`，没有新增神经网络训练。

[v1.2 前缀标定与滚动外推诊断](ootang_bplus_diagnostics_plan.v1.2.md) 已完成，
[结果记录](ootang_bplus_diagnostics_results.v1.2.md) 保留未稳定改善外推的结论。
代码为 `code/physics_guided_diagnostics/`，结果另存 `results/ootang_bplus_v1_2/`。
已完成的[实验计划 v1.1](ootang_bplus_probabilistic_experiment_plan.v1.1.md)
规定 M0/M1/M2 三组比较、数据与递推接口、时间验证、训练/选模、
数值验收和输出；独立代码位于 `code/physics_guided/`，实际运行与验证状态见
[实施记录](ootang_bplus_probabilistic_implementation.v1_1.md)，结果位于
[`results/ootang_bplus_v1_1/`](../results/ootang_bplus_v1_1/)。
[v1](ootang_bplus_probabilistic_experiment_plan.v1.md) 和
[前期讨论](ootang_bplus_model_route_discussion.v1.md) 保留为历史设计依据。
下文的 ConvLSTM–NGBoost–SHAP 描述属于已完成阶段，不是当前阶段的新增预警任务。

## 已实现的方法和时间口径

```text
ConvLSTM 多测点概率位移预测
  → 预测区间及覆盖率
  → 四项指标（区间、速度、严格加速度、改进切线角）
  → H=7 自动未来状态标签
  → 五分类 site NGBoost
  → NGBoost SHAP
  → 测点级与滑坡体级逐时预警
```

当前 SHAP 是 **NGBoost 分类 SHAP**，解释 site 分类器的期望有序等级。它不是 ConvLSTM SHAP，
也不是因果证明；当前定义见 [`ngboost_shap_protocol.md`](ngboost_shap_protocol.md)。
当前汇总图展示全部 8 点 × 4 指标的 32 项依赖；早期独立回归 SHAP 的执行链已经退役，
保留图件仅作历史快照。

| 时间分母 | 数量 | 使用位置 |
| --- | ---: | --- |
| 原始物化序列 | 1,461 日 | 完整藕塘输入历史；运动学长表为 `1461×8` 行 |
| ConvLSTM 一步预测 | 1,425 日 | 2016-08-06 至 2020-06-30；fit/calibration/test 为 911/227/287 日 |
| NGBoost 模型可用时间 | 861 日 | 三折分类与逐时概率；SHAP 从固定折内样本解释 |
| v4 透明规则基线 | 514 日 | 基线自己的有效输入/结果窗口；测点表为 `514×8` 行 |

这些口径不能互相替代。“全部时刻”必须写清是全部 861 个模型可用时刻，还是 v4 的 514 个
基线时刻。861 日中有 840 日成熟 H=7 标签；每折末端合计 21 日标签尚未成熟，但仍保留模型输出。

用户已确认阶段报告提交。下列方法与结果文档记录已完成阶段；旧导师意见和历史待办不构成
当前指令。Vajont 未参与现有结果；后续是否开展新案例由当前任务决定，本次未启动新实验。

## Canonical：当前科研文档

| 文档 | 用途 |
| --- | --- |
| [`progress.md`](progress.md) | 当前进度、最近运行、负结果和下一步 |
| [`ootang_manuscript_methods_results_draft.md`](ootang_manuscript_methods_results_draft.md) | 当前整合的方法、结果与讨论文字底稿 |
| [`ootang_stage_results_package.md`](ootang_stage_results_package.md) | 藕塘阶段结果、证据强度及可写/不可写边界 |
| [`ootang_ngboost_auto_state_experiment_plan.md`](ootang_ngboost_auto_state_experiment_plan.md) | H=7 自动标签、五分类 NGBoost、基线和分类 SHAP 的版本化记录 |
| [`ngboost_shap_protocol.md`](ngboost_shap_protocol.md) | 当前五分类 site NGBoost 的 SHAP 对象、样本和解释边界 |
| [`ootang_operational_run.md`](ootang_operational_run.md) | 四指标计算与 v4 透明规则基线；不替代 NGBoost |
| [`ootang_warning_data_dictionary.md`](ootang_warning_data_dictionary.md) | 指标、状态、单位和输出字段定义 |
| [`../paper/process_report.tex`](../paper/process_report.tex) / [`编译说明`](../paper/README.md) | 当前以图为主的阶段报告；数值仍以配置、manifest 和 CSV 为准 |

## Reference：辅助核对材料

| 文档 | 用途 |
| --- | --- |
| [`ootang_convlstm_elevation_fixed120_review.md`](ootang_convlstm_elevation_fixed120_review.md) | ConvLSTM 三折 × 五种子诊断 |
| [`ootang_data_lineage_expert_review.md`](ootang_data_lineage_expert_review.md) | 数据来源、物化序列限制和证据门禁 |
| [`ootang_elevation_warning_expert_review.md`](ootang_elevation_warning_expert_review.md) | 高程输入、空间融合和结果解释审查 |
| [`ootang_interval_calibration_expert_review.md`](ootang_interval_calibration_expert_review.md) | 预测区间校准及覆盖率审查 |
| [`ootang_stable_segment_expert_review.md`](ootang_stable_segment_expert_review.md) | 稳定段与 V0 比较器边界审查 |
| [`ootang_v4_acceleration_decision.md`](ootang_v4_acceleration_decision.md) | 严格加速度阈值的项目操作化依据 |
| [`current_method_reference_register.md`](current_method_reference_register.md) | 当前方法所用文献、合法来源和引用边界 |
| [`advisor_review_action_plan.md`](advisor_review_action_plan.md) | 被 v1/v2 协议配置引用的历史导师意见快照；已退役，不再作为当前约束 |

## Historical：历史路线与工程记录

完整旧文档见 Git 基线 `6f499cd`。2026-08-31 退役的独立回归 SHAP、interval-proxy、
auto-V0/V5 gate 与未启用 ConvLSTM inner/capacity 支路可从基线 `b13eb8b` 恢复。它们只用于
必要时追溯历史，不属于当前科研主线，也不再由当前文档索引维护。

## Generated：机器生成证据

| 入口 | 内容 |
| --- | --- |
| [`figures/pipeline/ootang_advisor_demo_run.json`](../figures/pipeline/ootang_advisor_demo_run.json) | 藕塘五阶段运行清单与产物合同 |
| [`figures/convlstm/`](../figures/convlstm/) | 全 8 测点预测、区间、覆盖率和滚动评价 |
| [`figures/ngboost_auto_state_ecdf_v2/`](../figures/ngboost_auto_state_ecdf_v2/) | H=7 自动标签、边界及时间线 |
| [`figures/ngboost_auto_state_classifier_v1/`](../figures/ngboost_auto_state_classifier_v1/) | 五级概率、基线指标、完整 32 项分类 SHAP 及 861 日逐时预警 |
| [`figures/warning_operational_draft_v4/`](../figures/warning_operational_draft_v4/) | 514 日透明规则基线和多点诊断 |
| [`figures/advisor_ootang_v1/advisor_summary.md`](../figures/advisor_ootang_v1/advisor_summary.md) | 已生成核心图表和表格的索引 |

`advisor_summary.md` 只是机械生成的证据索引，不替代当前 `paper/process_report.tex`。解释任何
结果时，应同时核对对应配置、manifest、CSV 指标和当前局限说明。
