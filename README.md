# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15（本地时间），跨起点验证、α五点对照和λ四点训练/搜索三步全部完成；仍未建立稳定的四点B+优势。**

[最新完整图文报告](docs/ootang_transformer_temporal_results.v1.0.md) · [跨起点及四点PNG/SVG](figures/ootang_transformer_temporal_v1/20260914/README.md) · [全部56组CSV](results/ootang_transformer_temporal_v1/20260914/analysis/phase_summary.csv)

每个窗口完整293日；下表为四点RMSE的平均值，单位mm。α/λ均只用上一起点已成熟的前90日选择，随后90日校准，完整外层标签在所有预测锁定后释放。

| 起点（已观测天数） | B+ | 历史选α | 历史选λ | 选出α / λ |
| --- | ---: | ---: | ---: | --- |
| 612 | 34.949548 | 34.949548 | 34.927836 | 0 / 3 |
| 792 | 27.313325 | 27.313325 | 28.101706 | 0 / 3 |
| 972 | 67.167389 | 67.167389 | 64.541391 | 0 / 3 |
| 1168，最终探索 | 9.124173 | 9.279082 | 9.279082 | 1 / 0 |

固定半残差最终RMSE仍为8.867132mm，但历史选择没有选中它；新加λ=1/3、3在最终均值误差上未超过已有λ=1。972窗口λ有平均改善，但MJ3退步；两流程历史完整均值门均0/3，最终也未过。42次新拟合、16800更新和全部负结果保留，不自动追加训练或RL。

当前协议给定未来逐日降雨/库水位，每条预测路径不接收实测位移反馈。窗口部分重叠、历史日期已经暴露，属于探索性条件预测；972沿用已可用的792日B+参数，不能推广为每个起点都重新拟合物理参数。它与逐日更新的1—7日滚动任务、固定驱动长期递推分开解释。

[上一轮半残差/区间校准报告](docs/ootang_transformer_calibration_results.v1.0.md)和全部旧结果保持原样。

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
| [code/transformer_temporal/](code/transformer_temporal/) | 跨起点选参、有限λ训练与完整核验 |
| [code/transformer_calibration/](code/transformer_calibration/) | 上一轮保存预测半残差与离线区间校准 |
| [code/transformer_regularization/](code/transformer_regularization/) | 上一轮 Transformer 残差正则化实现 |
| [code/sequence_conditional/](code/sequence_conditional/) | 同协议 Transformer / CNN-Mamba 对照 |
| [code/tcn_conditional_trajectory/](code/tcn_conditional_trajectory/) | 同协议 TCN 与基线接口 |
| [config/](config/) · [results/](results/) · [figures/](figures/) | 按实验版本保存的配置、预测、评分与图件 |
| [data/](data/) · [docs/](docs/) | 数据和可追溯研究文档 |

历史文档从默认阅读入口退役，原路径、内容和实验产物保留。复查优先使用保存结果；旧计划、命令及剩余预算不构成新训练授权。
