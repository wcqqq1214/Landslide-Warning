# 项目文档导航

当前入口以 **2026-09-16本地时间已完成的TiDE分开编码与受限H融合**为准（实验日期为2026-09-15 UTC）。结果、实现核验和效果判断分别阅读；历史计划中的“尚未训练”保留形成时含义。

## 最新研究：TiDE分开编码与受限H融合

[研究记录](ootang_tide_fusion_results.v1.0.md) · [完整33组CSV](../results/ootang_tide_fusion_v1/20260915/analysis/phase_summary.csv) · [1800配对指标差](../results/ootang_tide_fusion_v1/20260915/analysis/paired_metric_effects.csv) · [128行分量](../results/ootang_tide_fusion_v1/20260915/analysis/component_summary.csv) · [四张研究图](../figures/ootang_tide_fusion_v1/20260915/README.md)

[冻结计划](ootang_tide_fusion_plan.v1.0.md) · [配置](../config/ootang_tide_fusion.v1_0.json) · [1272来源](ootang_tide_fusion_sources.v1.0.json) · [成熟支持](../results/ootang_tide_fusion_v1/20260915/mature_support.json) · [实现](ootang_tide_fusion_implementation.v1.0.md) · [核验](ootang_tide_fusion_validation.v1.0.md) · [图件合同](ootang_tide_fusion_figure_contract.v1.0.md) · [回执](../results/ootang_tide_fusion_v1/20260915/final_receipt.json)

新增SPLIT/BOUND共24拟合9600更新120检查点，精确复用KIN/PHYS24拟合120检查点，全部四条293日路径完成。SPLIT三主窗RMSE52.630851/15.600443/9.230440，BOUND25.340337/7.703676/9.038071mm；两新版三窗平均MAE/RMSE仍高于旧KIN。BOUND相对SPLIT均值门1/3、同种子同时改善3/2/1个，三种主要配对的稳定判定均false；四组B+联合均0/3。最终BOUND的MJ3/MJ1覆盖41.64%/56.31%，幅度限制生效不代表效果达标。1272来源、188863268数值、四图16面板/247192图形值核验完成。新旧容量不同、两支共同训练、已暴露重叠窗口等限制保持；本轮不追加cap/结构/轮数/RL。仅研究记录/CSV/PNG/SVG。

## 历史研究：TiDE物理特征分组消融

[研究记录](ootang_tide_features_results.v1.0.md) · [完整27组CSV](../results/ootang_tide_features_v1/20260915/analysis/phase_summary.csv) · [2520行因素效应](../results/ootang_tide_features_v1/20260915/analysis/factorial_effects.csv) · [四张研究图](../figures/ootang_tide_features_v1/20260915/README.md)

[冻结计划](ootang_tide_features_plan.v1.0.md) · [配置](../config/ootang_tide_features.v1_0.json) · [751来源](ootang_tide_features_sources.v1.0.json) · [成熟支持](../results/ootang_tide_features_v1/20260915/mature_support.json) · [实现](ootang_tide_features_implementation.v1.0.md) · [核验](ootang_tide_features_validation.v1.0.md) · [图件合同](ootang_tide_features_figure_contract.v1.0.md) · [回执](../results/ootang_tide_features_v1/20260915/final_receipt.json)

同一TiDE以K（B+位移/日增量）与H（B+水文状态）作2×2消融。新增KIN/HYD各12次拟合，精确复用DATA/PHYS；共24新拟合9600更新、240新旧检查点和四条293日发报完成。KIN三主窗RMSE22.907124/5.534082/7.540029，HYD29.660967/25.069860/8.476753mm；完整PHYS14.859012/12.114521/8.921779。H在K存在时第一窗有益、后两窗负收益；两新组最终MJ3均值仍差于B+，KIN三点覆盖不足80%，四组B+联合均0/3。187837220独立数值、四图16面板核验通过。下一研究假设是分组编码与受限融合，不属于本轮追加；不按最终成绩拼点或搜索RL。只保存研究记录/CSV/PNG/SVG。

## 上一轮研究：小型TiDE直接预测配对

[研究记录](ootang_tide_direct_results.v1.0.md) · [完整21组CSV](../results/ootang_tide_direct_v1/20260915/analysis/phase_summary.csv) · [三张研究图](../figures/ootang_tide_direct_v1/20260915/README.md)

[冻结计划](ootang_tide_direct_plan.v1.0.md) · [配置](../config/ootang_tide_direct.v1_0.json) · [261来源](ootang_tide_direct_sources.v1.0.json) · [成熟支持](../results/ootang_tide_direct_v1/20260915/mature_support.csv) · [实现](ootang_tide_direct_implementation.v1.0.md) · [核验](ootang_tide_direct_validation.v1.0.md) · [图件合同](ootang_tide_direct_figure_contract.v1.0.md) · [回执](../results/ootang_tide_direct_v1/20260915/final_receipt.json)

TiDE_DATA三主窗RMSE50.874904/33.672205/7.278984，TiDE_PHYS14.859012/12.114521/8.921779mm；最终B+9.124173。加物理版平均误差低于既有GRU，但物理配对收益随窗口/点改变、B+联合均0/3。24拟合9600更新/120检查点完整完成，93991564独立数值、三图12面板通过；保留负结果，继续研究的优先问题是物理特征分组，本轮未追加该实验。仅研究记录/CSV/PNG/SVG，无PDF汇报。

## 上一轮交付：历史教师更新配对

| 文档/产物 | 用途 |
| --- | --- |
| [完整比较报告](ootang_teacher_refresh_results.v1.0.md) · [三张PNG/SVG](../figures/ootang_teacher_refresh_v1/20260915/README.md) | 三窗教师政策配对、两组四点导师式图与负结果 |
| [18组完整CSV](../results/ootang_teacher_refresh_v1/20260915/analysis/phase_summary.csv) · [逐点结果](../results/ootang_teacher_refresh_v1/20260915/analysis/metrics_by_point.csv) · [教师拟合](../results/ootang_teacher_refresh_v1/20260915/audit/teacher_fit_points.csv) | 四点/种子/完整日期、拟合与预测分开解释 |
| [计划](ootang_teacher_refresh_plan.v1.0.md) · [配置](../config/ootang_teacher_refresh.v1_0.json) · [203来源](ootang_teacher_refresh_sources.v1.0.json) · [成熟支持](ootang_teacher_refresh_support.v1.0.csv) | 同网格/结构/样本，固定10次物理更新、24次GRU拟合 |
| [实现](ootang_teacher_refresh_implementation.v1.0.md) · [核验](ootang_teacher_refresh_validation.v1.0.md) · [图件合同](ootang_teacher_refresh_figure_contract.v1.0.md) · [最终回执](../results/ootang_teacher_refresh_v1/20260915/final_receipt.json) | 96检查点/5588338数值，三图12面板与原来源归档 |

更新组三个主要窗集成MAE/RMSE均高于旧教师组，最终RMSE11.068245对10.600134mm，B+9.124173；两组联合门均0/3。8/10个历史前缀拟合RMSE改善未转化为预测增益；10次物理拟合均未收敛，有限预算限制保留。本轮完整完成并收束，不自动追加训练/教师密度/λ/RL或换模型。

## 上一轮交付：训练充分性与固定预算对照

| 文档/产物 | 用途 |
| --- | --- |
| [完整结果](ootang_training_sufficiency_results.v1.0.md) · [四张PNG/SVG](../figures/ootang_training_sufficiency_v1/20260915/README.md) | 训练改善与外推不稳定、两骨干最终四点曲线 |
| [完整24组CSV](../results/ootang_training_sufficiency_v1/20260915/extension/analysis/phase_summary.csv) · [逐点结果](../results/ootang_training_sufficiency_v1/20260915/extension/analysis/metrics_by_point.csv) | 三个完整293日窗、原200及新400结果，全部种子保留 |
| [计划](ootang_training_sufficiency_plan.v1.0.md) · [配置](../config/ootang_training_sufficiency.v1_0.json) · [1329来源](ootang_training_sufficiency_sources.v1.0.json) · [零训练诊断](ootang_training_sufficiency_diagnostic.v1.0.md) | 先固定触发，只有训练与成熟历史共同改善才追加 |
| [实现](ootang_training_sufficiency_implementation.v1.0.md) · [核验](ootang_training_sufficiency_validation.v1.0.md) · [图件约定](ootang_training_sufficiency_figure_contract.v1.0.md) · [回执](../results/ootang_training_sufficiency_v1/20260915/final_receipt.json) | 96旧/72新检查点，原200步精确重放，24新拟合9600实际更新 |

最终GRU400/TF400 RMSE11.339123/13.653310mm均比原200次更高，B+9.124173保持；两个400版本最终概率门通过，但三个窗口联合仍0/3。本轮已收束，不继续加训练或结构。

## 上一轮交付：统一接口骨干×起点表达

| 文档/产物 | 用途 |
| --- | --- |
| [完整比较报告](ootang_backbone_anchor_results.v1.0.md) · [九张PNG/SVG](../figures/ootang_backbone_anchor_v1/20260915/README.md) | 三窗骨干/表达配对、四组导师式及完整范围图 |
| [36组汇总](../results/ootang_backbone_anchor_v1/20260915/analysis/phase_summary.csv) · [逐点/种子因子效应](../results/ootang_backbone_anchor_v1/20260915/analysis/factorial_seed_points.csv) | 12方法、全部293日/四点/种子，保留旧半残差与G11 |
| [计划](ootang_backbone_anchor_plan.v1.0.md) · [配置](../config/ootang_backbone_anchor.v1_0.json) · [1106来源](ootang_backbone_anchor_sources.v1.0.json) | 同输入/解码/样本、固定两骨干及24新拟合 |
| [实现](ootang_backbone_anchor_implementation.v1.0.md) · [核验](ootang_backbone_anchor_validation.v1.0.md) · [图件约定](ootang_backbone_anchor_figure_contract.v1.0.md) · [回执](../results/ootang_backbone_anchor_v1/20260915/final_receipt.json) | 192新旧检查点、3813856数值、九图36面板及结论边界 |

TF原残差/起点表达最终RMSE15.981473/13.290363mm，GRU起点版11.141437、B+9.124173；A的平均收益跨骨干出现，不能推广每点/种子或网络家族。四组B+联合门仍0/3；24新拟合4800更新已完成，旧GRU复用，不自动追加实验。

## 上一轮交付：GRU两因素消融

| 文档/产物 | 用途 |
| --- | --- |
| [结果报告](ootang_gru_ablation_results.v1.0.md) · [五张PNG/SVG](../figures/ootang_gru_ablation_v1/20260915/README.md) | 两因素配对、三个完整293日窗、四组四点曲线及负结果 |
| [24组汇总](../results/ootang_gru_ablation_v1/20260915/analysis/phase_summary.csv) · [逐点/种子/因子效应](../results/ootang_gru_ablation_v1/20260915/analysis/factorial_seed_points.csv) | 所有候选、对照、点和种子保持 |
| [冻结计划](ootang_gru_ablation_plan.v1.0.md) · [配置](../config/ootang_gru_ablation.v1_0.json) · [708来源](ootang_gru_ablation_sources.v1.0.json) | 固定四组、合法教师/采样、独立预算和原诊断归档 |
| [核验](ootang_gru_ablation_validation.v1.0.md) · [图件约定](ootang_gru_ablation_figure_contract.v1.0.md) · [回执](../results/ootang_gru_ablation_v1/20260915/final_receipt.json) | 48拟合/192检查点复算、20面板图文核验和限制 |

G11最终RMSE10.785141mm，优于G00的14.257499，仍高于B+9.124173；首日跳偏明显缩小，ATU1/MJ3后续均值与ATU1覆盖仍不足。四组B+联合门均0/3。全部预定训练完成，本地提交，未push，不追加模型/训练。

## 上一轮交付：夜间固定小图小试

| 文档/产物 | 用途 |
| --- | --- |
| [简要报告](ootang_overnight_graph_results.v1.0.md) · [六页PDF](../output/pdf/ootang_overnight_graph_v1/v2/ootang_spatial_pilot_figures.pdf) | 旧起点小试失败、两版GRU、残差诊断及完整区间图 |
| [PNG/SVG与源数组](../figures/ootang_overnight_graph_v1/20260915/README.md) · [33组CSV](../results/ootang_overnight_graph_v1/20260915/analysis/phase_summary.csv) | 完整三窗、全部旧对照、三种子与逐点结果 |
| [冻结计划](ootang_overnight_graph_plan.v1.0.md) · [配置](../config/ootang_overnight_graph.v1_0.json) · [301来源](ootang_overnight_graph_sources.v1.0.json) | 固定小图/GRU、合法教师、唯一条件候选与新截止 |
| [实施说明](ootang_overnight_graph_implementation.v1.0.md) · [核验](ootang_overnight_graph_validation.v1.0.md) · [图件合同](ootang_overnight_graph_figure_contract.v1.0.md) · [回执](../results/ootang_overnight_graph_v1/20260915/final_receipt.json) | 96检查点、11110304数值、70346图形值、完整交付与失败留档 |

24拟合4800更新已完整结束；最终LOCAL/GRAPH平均RMSE14.283633/14.257499mm，B+9.124173。最终概率门通过、均值门未过；图相对本点增量不足。因果残差触发false，双头拟合0，不自动追加实验；北京时间09:00前提前结束，只本地提交。

## 上一轮：起点条件化小试

| 文档/产物 | 用途 |
| --- | --- |
| [完整报告](ootang_transformer_origin_results.v1.0.md) | 完整历史/去显式历史位移/均匀池化三臂，完整三窗与负结果 |
| [三张图PNG/SVG](../figures/ootang_transformer_origin_v1/20260914/README.md) · [27组CSV](../results/ootang_transformer_origin_v1/20260914/analysis/phase_summary.csv) | 跨时段配对、四点导师版及完整区间版 |
| [冻结计划](ootang_transformer_origin_plan.v1.0.md) · [配置](../config/ootang_transformer_origin.v1_0.json) · [253来源](ootang_transformer_origin_sources.v1.0.json) | 固定模型、训练目标/教师/日期、90分钟自限窗口 |
| [实现勘误](ootang_transformer_origin_implementation.v1.0.md) · [核验](ootang_transformer_origin_validation.v1.0.md) · [图件合同](ootang_transformer_origin_figure_contract.v1.0.md) · [回执](../results/ootang_transformer_origin_v1/20260914/final_receipt.json) | 144检查点、9027212数值、35247图形值、异常与完成状态 |

当前结论：36拟合7200更新完整完成；三臂均只过972历史均值门，最终RMSE分别24.702585/24.565083/24.929915mm，B+9.124173mm。历史信息/注意力收益不稳定，最终完整均值/概率门失败；本轮停止，不自动加训练/RL。

## 上一轮：跨起点 α / 有限 λ

| 文档/产物 | 用途 |
| --- | --- |
| [完整结果](ootang_transformer_temporal_results.v1.0.md) | 五点α、四点λ、历史锁定选择、四个完整293日比较与负结果 |
| [五张图及PNG/SVG](../figures/ootang_transformer_temporal_v1/20260914/README.md) · [56组CSV](../results/ootang_transformer_temporal_v1/20260914/analysis/phase_summary.csv) | 跨起点证据、最终四点导师版/完整范围图与原数值 |
| [冻结计划](ootang_transformer_temporal_plan.v1.0.md) · [配置](../config/ootang_transformer_temporal.v1_0.json) · [156来源](ootang_transformer_temporal_sources.v1.0.json) | 事前网格、时间/教师/信息边界、预算 |
| [核验](ootang_transformer_temporal_validation.v1.0.md) · [图件合同](ootang_transformer_temporal_figure_contract.v1.0.md) · [最终回执](../results/ootang_transformer_temporal_v1/20260914/final_receipt.json) | 228检查点/3339568数值/事件与图形复算、异常及实际完成状态 |

该轮结论：三步完整完成，42新拟合16800更新；α/λ未建立稳定四点B+优势。最终选择α=1、λ=0，两流程同为9.279082mm RMSE，高于B+9.124173。固定半残差局部收益仍在，旧结论不改；不自动继续搜索或RL。

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
