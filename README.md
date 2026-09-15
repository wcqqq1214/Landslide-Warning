# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15，统一接口GRU/Transformer×起点残差表达的小型消融已完整完成。起点表达的平均收益能跨两个骨干出现；本轮GRU起点版三窗均值误差更低，但均未达到B+均值与概率联合目标。**

[最新比较报告](docs/ootang_backbone_anchor_results.v1.0.md) · [九张PNG/SVG](figures/ootang_backbone_anchor_v1/20260915/README.md) · [完整36组CSV](results/ootang_backbone_anchor_v1/20260915/analysis/phase_summary.csv) · [核验](docs/ootang_backbone_anchor_validation.v1.0.md)

统一14维历史、图/解码器、样本、三种子与200更新；GRU1241参数、Transformer1249参数。G00/G10复用并逐检查点验证，T00/T10新训练；全部关闭边界采样。每窗完整293日，表为三种子集成预测的四点平均RMSE，单位mm。

| 起点（已观测天数） | B+ | G00原残差 | G10起点表达 | T00原残差 | T10起点表达 |
| --- | --- | --- | --- | --- | --- |
| 792 | 27.313325 | 30.145471 | 26.778773 | 29.468347 | 29.102037 |
| 972 | 67.167389 | 37.481242 | 20.445223 | 40.587372 | 20.640218 |
| 1168 | 9.124173 | 14.257499 | 11.141437 | 15.981473 | 13.290363 |

新增24次Transformer拟合4800更新，复用24份GRU拟合；192份新旧检查点、1106来源、3813856数值及九图36面板已核验。TF起点版最终RMSE下降16.84%，仍高于GRU起点版和B+，ATU1/ATU5的90%覆盖仅63.82%/47.44%；GRU起点版ATU1覆盖76.11%仍不足。四组B+联合门均0/3，不以训练跑通或平均改善代替效果达标。

本轮给定未来逐日降雨/库水位，每条路径不接收实测位移。历史窗重复暴露且部分重叠，全部为探索性条件预测。它与1—7日滚动及固定驱动递推任务分开解释；972仍用792日教师，不假称每起点重新拟合B+。

[上一轮GRU起点×边界采样](docs/ootang_gru_ablation_results.v1.0.md)的G11最终10.785141mm、[旧Transformer半残差](docs/ootang_transformer_temporal_results.v1.0.md)8.867132mm均保持；不同组/训练组织的历史成绩不纳入本轮纯因素效应，不根据最终窗重选主模型。此次已停止追加，本地分步提交、未push，不交付PDF或启动RL。

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
