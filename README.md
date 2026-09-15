# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15，起点残差表达×边界采样的2×2消融已完整完成。改动改善了原GRU，但最终窗仍未达到均值和概率同时优于B+的目标。**

[最新消融报告](docs/ootang_gru_ablation_results.v1.0.md) · [五张PNG/SVG](figures/ootang_gru_ablation_v1/20260915/README.md) · [完整24组CSV](results/ootang_gru_ablation_v1/20260915/analysis/phase_summary.csv) · [核验](docs/ootang_gru_ablation_validation.v1.0.md)

四组共用1241参数固定图GRU、三个种子和固定200更新。G00原样，G10起点残差表达，G01边界采样，G11两项合用。每窗完整293日；下表为集成预测的四点平均RMSE，单位mm。

| 起点（已观测天数） | B+ | G00 | G10 | G01 | G11 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 792 | 27.313325 | 30.145471 | 26.778773 | 26.249552 | 20.382465 |
| 972 | 67.167389 | 37.481242 | 20.445223 | 35.158832 | 20.732781 |
| 1168，最终探索 | 9.124173 | 14.257499 | 11.141437 | 11.563858 | 10.785141 |

48拟合9600更新/192检查点、三个完整评价窗已保存并独立复算；G00复现旧固定图GRU。G11最终RMSE较G00下降24.35%，第1天平均绝对误差15.314678→1.229503mm，但ATU1/MJ3均值仍劣于B+，ATU1的90%覆盖仅67.24%。两因素收益存在交互，不能将平均改善推广到每点或全部时期。四组对B+联合门均0/3；本轮图文已完成，不按结果追加训练，只本地提交、未push。

当前协议给定未来逐日降雨/库水位，每条预测路径不接收实测位移反馈。窗口部分重叠、历史日期已经暴露，属于探索性条件预测；972沿用已可用的792日B+参数，不能推广为每个起点都重新拟合物理参数。它与逐日更新的1—7日滚动任务、固定驱动长期递推分开解释。

[上一轮固定小图及其PDF](docs/ootang_overnight_graph_results.v1.0.md)、[起点条件化小试](docs/ootang_transformer_origin_results.v1.0.md)、[跨起点α/λ报告](docs/ootang_transformer_temporal_results.v1.0.md)、[半残差/区间校准报告](docs/ootang_transformer_calibration_results.v1.0.md)和全部旧结果保持原样。旧半残差最终8.867132mm的局部正结果不改，不据此重新选模。

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
