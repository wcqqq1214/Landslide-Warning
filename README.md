# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-16本地时间，TiDE分开编码与受限H融合已完整完成：限幅减轻无约束版退步，但未获得相对旧KIN/PHYS的稳定收益。** 实验目录沿用2026-09-15 UTC日期。

[研究记录](docs/ootang_tide_fusion_results.v1.0.md) · [四张研究图](figures/ootang_tide_fusion_v1/20260915/README.md) · [完整33组CSV](results/ootang_tide_fusion_v1/20260915/analysis/phase_summary.csv) · [分量CSV](results/ootang_tide_fusion_v1/20260915/analysis/component_summary.csv) · [核验](docs/ootang_tide_fusion_validation.v1.0.md)

SPLIT将K主支与H支分别编码后相加，BOUND用训练段30日位移变化RMS限定H贡献；两版均123571参数、180日历史、三种子、固定400更新、从头联合训练。新增24拟合9600更新120检查点，精确复用旧KIN/PHYS24拟合120检查点；四条293日条件预测全部完成，无位移观测反馈，无B+重拟合/物理前向。表为三种子集成预测的四点平均RMSE，单位mm。

| 起点 | B+ | TiDE_KIN | TiDE_PHYS | TiDE_SPLIT | TiDE_BOUND |
| --- | ---: | ---: | ---: | ---: | ---: |
| 792 | 27.313325 | 22.907124 | 14.859012 | 52.630851 | 25.340337 |
| 972 | 67.167389 | 5.534082 | 12.114521 | 15.600443 | 7.703676 |
| 1168 | 9.124173 | 7.540029 | 8.921779 | 9.230440 | 9.038071 |

BOUND相对SPLIT的平均MAE/RMSE/CRPS三窗均降低，但严格均值门只过第一窗，同种子均值同时改善3/2/1个。两新组平均MAE/RMSE三窗均高于旧KIN；BOUND最终比B+的平均RMSE仅改善0.94%，MJ3/MJ1逐点保护失败，90%覆盖仅41.64%/56.31%。四组B+联合均0/3，不将平均改善改判为整体达标。

1272来源、240检查点、188863268独立数值及四图16面板/247192图形值核验通过。幅度限制工作正常不等于预测有效；两支联合训练，新版比旧版增加约12%参数，不能视为容量匹配消融或物理因果。窗口重叠且已暴露，全部探索性。本轮已完成，不追加cap搜索/训练/RL；仅研究记录/CSV/PNG/SVG、本地提交，无push/PR/PDF汇报，用户/导师尚未验收。

[上一轮K/H输入消融](docs/ootang_tide_features_results.v1.0.md)、[原TiDE配对](docs/ootang_tide_direct_results.v1.0.md)、[历史教师更新](docs/ootang_teacher_refresh_results.v1.0.md)、[200/400训练对照](docs/ootang_training_sufficiency_results.v1.0.md)、[GRU/Transformer起点表达](docs/ootang_backbone_anchor_results.v1.0.md)及[旧半残差](docs/ootang_transformer_temporal_results.v1.0.md)的正负结果均保留。

## 阅读入口

| 入口 | 用途 |
| --- | --- |
| [文档导航](docs/README.md) | 当前计划、配置、核验、交付及同协议对照 |
| [协作规则](AGENTS.md) | 当前范围、资料使用、验证和 Git 规则 |
| [进度记录](docs/progress.md) | 最近维护及按形成时间保留的历史记录 |
| [历史文档与退役清单](docs/history/README.md) | 旧方法、旧协议及八点预警材料，按需追溯 |

## 代码与产物

| 目录 | 用途 |
| --- | --- |
| [code/tide_fusion/](code/tide_fusion/) | TiDE独立H支、固定限幅配对、完整训练及分量/图文核验 |
| [code/tide_features/](code/tide_features/) | TiDE物理特征分组2×2消融、旧两端复用与独立核验 |
| [code/tide_direct/](code/tide_direct/) | 小型TiDE直接位移配对、全293日预测及独立核验 |
| [code/teacher_refresh/](code/teacher_refresh/) | 历史B+教师政策配对、固定GRU训练、独立数值与图文核验 |
| [code/training_sufficiency/](code/training_sufficiency/) | 固定检查点诊断、一次条件性预算对照与图文核验 |
| [code/backbone_anchor/](code/backbone_anchor/) | 统一骨干×起点表达、GRU复用、固定TF训练及图文核验 |
| [code/gru_ablation/](code/gru_ablation/) | 上轮GRU起点表达×边界采样2×2消融 |
| [code/overnight_graph/](code/overnight_graph/) | 固定GRU空间配对、因果残差触发、完整核验与六页PDF |
| [code/transformer_origin/](code/transformer_origin/) | 起点条件化三臂训练、独立复算和图文交付 |
| [code/transformer_temporal/](code/transformer_temporal/) | 跨起点选参、有限λ训练与完整核验 |
| [code/transformer_calibration/](code/transformer_calibration/) | 上一轮保存预测半残差与离线区间校准 |
| [code/transformer_regularization/](code/transformer_regularization/) | 上一轮 Transformer 残差正则化实现 |
| [code/sequence_conditional/](code/sequence_conditional/) | 同协议 Transformer / CNN-Mamba 对照 |
| [code/tcn_conditional_trajectory/](code/tcn_conditional_trajectory/) | 同协议 TCN 与基线接口 |
| [config/](config/) · [results/](results/) · [figures/](figures/) | 按实验版本保存的配置、预测、评分与图件 |
| [data/](data/) · [docs/](docs/) | 数据和可追溯研究文档 |

历史文档从默认阅读入口退役，原路径、内容和实验产物保留。复查优先使用保存结果；旧计划、命令及剩余预算不构成新训练授权。
