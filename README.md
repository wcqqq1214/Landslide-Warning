# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15，训练充分性诊断及固定200对400次比较已完成。训练拟合继续改善，追加训练没有带来跨时段稳定的预测收益。**

[最新比较报告](docs/ootang_training_sufficiency_results.v1.0.md) · [四张PNG/SVG](figures/ootang_training_sufficiency_v1/20260915/README.md) · [完整24组CSV](results/ootang_training_sufficiency_v1/20260915/extension/analysis/phase_summary.csv) · [核验](docs/ootang_training_sufficiency_validation.v1.0.md)

固定起点表达、输入/结构/λ/种子，先用96旧检查点和成熟历史窗核验，触发后完成两骨干24次400更新拟合。原200步精确重放，9600次实际更新包含4800重放与4800追加。表为完整293日、三种子集成预测的四点平均RMSE，单位mm。

| 起点 | B+ | GRU200 | GRU400 | TF200 | TF400 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 792历史窗 | 27.313325 | 26.778773 | 21.725008 | 29.102037 | 27.310036 |
| 972历史窗 | 67.167389 | 20.445223 | 24.737119 | 20.640218 | 24.154311 |
| 1168最终探索 | 9.124173 | 11.141437 | 11.339123 | 13.290363 | 13.653310 |

400次的固定训练面板MSE继续下降16.21%—21.87%，但两模型仅792窗降低RMSE，972及最终均退步。最终相对B+的概率门均通过、均值门未过，三个窗口联合仍0/3；不能把训练继续下降或区间变宽后的覆盖改善当作稳定总体收益。

全部原200步日志/参数/预测精确一致；96旧/72新检查点、1329来源、诊断及追加独立数值、四图16面板已核验。未来驱动给定、预测路径不反馈位移，窗口已暴露且部分重叠，结论限于当前探索性条件协议。

[上一轮起点表达消融](docs/ootang_backbone_anchor_results.v1.0.md)的平均收益、[GRU边界采样](docs/ootang_gru_ablation_results.v1.0.md)及[旧Transformer半残差](docs/ootang_transformer_temporal_results.v1.0.md)的局部结果均保留。本轮已停止追加训练/结构/λ/RL；Markdown/CSV/PNG/SVG，本地分步提交，不push或交付PDF。

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
