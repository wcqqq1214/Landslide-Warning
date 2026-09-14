# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15（本地时间），夜间固定小图与因果残差诊断已完整完成，仍未达到均值和概率同时优于B+的目标。**

[最新简报与PDF链接](docs/ootang_overnight_graph_results.v1.0.md) · [六页PDF](output/pdf/ootang_overnight_graph_v1/v2/ootang_spatial_pilot_figures.pdf) · [PNG/SVG](figures/ootang_overnight_graph_v1/20260915/README.md) · [完整33组CSV](results/ootang_overnight_graph_v1/20260915/analysis/phase_summary.csv)

全历史GRU的本点版与固定图版仅改变MJ3—MJ1神经传递边，均为1241参数、固定200更新和三种子。每个窗口完整293日；下表为四点RMSE的平均值，单位mm。

| 起点（已观测天数） | B+ | GRU 本点 | GRU 固定图 |
| --- | ---: | ---: | ---: |
| 792 | 27.313325 | 30.127621 | 30.145471 |
| 972 | 67.167389 | 37.666177 | 37.481242 |
| 1168，最终探索 | 9.124173 | 14.283633 | 14.257499 |

两版只过972历史均值门，最终概率门通过但均值未过；图相对本点的均值/概率门三窗均未过。24新拟合4800更新、96检查点和全部负结果已保存并独立复算。前1152日因果残差诊断未触发双头候选，新增双头拟合0；本轮提前结束，不追加模型、训练或RL。图文和每步本地提交已完成，未push。

当前协议给定未来逐日降雨/库水位，每条预测路径不接收实测位移反馈。窗口部分重叠、历史日期已经暴露，属于探索性条件预测；972沿用已可用的792日B+参数，不能推广为每个起点都重新拟合物理参数。它与逐日更新的1—7日滚动任务、固定驱动长期递推分开解释。

[上一轮起点条件化小试](docs/ootang_transformer_origin_results.v1.0.md)、[跨起点α/λ报告](docs/ootang_transformer_temporal_results.v1.0.md)、[半残差/区间校准报告](docs/ootang_transformer_calibration_results.v1.0.md)和全部旧结果保持原样。旧半残差最终8.867132mm的局部正结果不改，不据此重新选模。

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
