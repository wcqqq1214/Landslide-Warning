# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15，历史B+教师更新配对实验已完整完成。更新教师组在三个完整293日窗口均未改善集成MAE/RMSE，两组均未达到相对B+的均值与概率联合目标。**

[最新比较报告](docs/ootang_teacher_refresh_results.v1.0.md) · [三张PNG/SVG](figures/ootang_teacher_refresh_v1/20260915/README.md) · [完整18组CSV](results/ootang_teacher_refresh_v1/20260915/analysis/phase_summary.csv) · [核验](docs/ootang_teacher_refresh_validation.v1.0.md)

同一小型起点GRU、共同训练网格/样本/单位/种子/200次更新，仅配对历史教师政策：沿用最近合法旧教师，或在历史前缀更新教师。新增10次物理拟合、24次GRU拟合4800更新，完整96份检查点和所有窗口保留；外层B+不重拟合。表为三种子集成预测的四点平均RMSE，单位mm。

| 起点 | B+ | 旧教师GRU | 更新教师GRU |
| --- | ---: | ---: | ---: |
| 792历史窗 | 27.313325 | 19.372056 | 24.684374 |
| 972历史窗 | 67.167389 | 22.660456 | 23.804193 |
| 1168最终探索 | 9.124173 | 10.600134 | 11.068245 |

新教师8/10个历史前缀的拟合RMSE下降、终点偏差缩小，却未转化为预测收益。10次物理拟合全部达到800次函数评价上限且未收敛，保留该限制。最终旧组ATU5和更新组ATU1的90%覆盖分别53.92%和69.62%，均未通过逐点保护；两组B+联合门均0/3。

203来源、10物理教师、96检查点和5588338数值独立复算、三图12面板已核验。未来驱动给定、预测路径不反馈位移；完整成熟长窗监督少且重叠、窗口已暴露，结论限于当前有限预算探索性协议。教师同时改变输入/基线/目标，不能识别唯一失败原因。

[上一轮200/400训练对照](docs/ootang_training_sufficiency_results.v1.0.md)、[起点表达消融](docs/ootang_backbone_anchor_results.v1.0.md)、[GRU边界采样](docs/ootang_gru_ablation_results.v1.0.md)及[旧Transformer半残差](docs/ootang_transformer_temporal_results.v1.0.md)的正负结果保持。旧教师组使用本轮共同网格，不冒充旧G10精确复现。本轮收束已检验的教师更新残差路线，不自动追加模型/轮数/λ/RL；Markdown/CSV/PNG/SVG、本地提交，不push或交付PDF，用户/导师尚未验收。

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
