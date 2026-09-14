# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15（本地时间），起点条件化小型Transformer三臂小试全部完成，未建立稳定收益，最终窗口明显差于B+。**

[最新完整图文报告](docs/ootang_transformer_origin_results.v1.0.md) · [三张PNG/SVG](figures/ootang_transformer_origin_v1/20260914/README.md) · [全部27组CSV](results/ootang_transformer_origin_v1/20260914/analysis/phase_summary.csv)

每个评分窗口完整293日；下表为四点RMSE的平均值，单位mm。模型固定200次、三种子，无选模；全部均值/区间锁定后释放完整标签评分。

| 起点（已观测天数） | B+ | 完整历史注意力 | 去显式历史位移 | 历史均匀池化 |
| --- | ---: | ---: | ---: | --- |
| 792 | 27.313325 | 42.081238 | 42.866189 | 42.369016 |
| 972 | 67.167389 | 46.468504 | 46.556051 | 45.874273 |
| 1168，最终探索 | 9.124173 | 24.702585 | 24.565083 | 24.929915 |

三臂均只过972历史窗的均值门，概率门三窗均未过；完整历史和注意力的配对收益不稳定。36新拟合7200更新、144检查点、全部预测及负结果已保存并独立复算。旧固定半残差最终8.867132mm的局部收益保持，不自动追加训练或RL。

当前协议给定未来逐日降雨/库水位，每条预测路径不接收实测位移反馈。窗口部分重叠、历史日期已经暴露，属于探索性条件预测；972沿用已可用的792日B+参数，不能推广为每个起点都重新拟合物理参数。它与逐日更新的1—7日滚动任务、固定驱动长期递推分开解释。

[上一轮跨起点α/λ报告](docs/ootang_transformer_temporal_results.v1.0.md)、[半残差/区间校准报告](docs/ootang_transformer_calibration_results.v1.0.md)和全部旧结果保持原样。

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
| [code/transformer_origin/](code/transformer_origin/) | 起点条件化三臂训练、独立复算和图文交付 |
| [code/transformer_temporal/](code/transformer_temporal/) | 跨起点选参、有限λ训练与完整核验 |
| [code/transformer_calibration/](code/transformer_calibration/) | 上一轮保存预测半残差与离线区间校准 |
| [code/transformer_regularization/](code/transformer_regularization/) | 上一轮 Transformer 残差正则化实现 |
| [code/sequence_conditional/](code/sequence_conditional/) | 同协议 Transformer / CNN-Mamba 对照 |
| [code/tcn_conditional_trajectory/](code/tcn_conditional_trajectory/) | 同协议 TCN 与基线接口 |
| [config/](config/) · [results/](results/) · [figures/](figures/) | 按实验版本保存的配置、预测、评分与图件 |
| [data/](data/) · [docs/](docs/) | 数据和可追溯研究文档 |

历史文档从默认阅读入口退役，原路径、内容和实验产物保留。复查优先使用保存结果；旧计划、命令及剩余预算不构成新训练授权。
