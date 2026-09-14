# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至 2026-09-14，Transformer 残差幅度正则化实验已完成训练、完整评价和独立核验；整体效果条件未通过。**

[最新图文报告](docs/ootang_transformer_regularization_results.v1.0.md) · [四点 PNG / SVG](figures/ootang_transformer_regularization_v1/20260914/README.md) · [五方法 CSV](results/ootang_transformer_regularization_v1/20260914/analysis/phase_summary.csv)

下表为四个测点各自 RMSE 的平均值，单位 mm；完整逐点、种子和概率评分见报告。

| 方法 | 开发段 376 日 | 最终探索段 293 日 |
| --- | ---: | ---: |
| 改进 B+ | 41.3684 | 9.1242 |
| Transformer 残差原版 | 48.1866 | 9.2791 |
| Transformer 残差正则版 REG1 | 44.4136 | 8.9073 |

REG1 相对原版在两阶段的集成均值和概率配对评价中改善；最终平均 MAE / RMSE 比 B+ 降低 5.25% / 2.38%。但开发四点均落后 B+，最终 MJ3 退步，只有 1/3 种子的最终 RMSE 优于 B+，主概率评分也未超过 B+。保留 B+ 作为物理参照，局部改善不代表整体目标完成。

本轮按导师条件提供未来逐日降雨和库水位，预测段不接收位移观测；最终以前 1168 日训练、后 293 日预测。它与每天接收新观测的 1—7 日滚动预测、固定驱动的长期递推不同。历史日期已反复用于研究，结果属于探索性条件预测。

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
| [code/transformer_regularization/](code/transformer_regularization/) | 最新 Transformer 残差正则化实现 |
| [code/sequence_conditional/](code/sequence_conditional/) | 同协议 Transformer / CNN-Mamba 对照 |
| [code/tcn_conditional_trajectory/](code/tcn_conditional_trajectory/) | 同协议 TCN 与基线接口 |
| [config/](config/) · [results/](results/) · [figures/](figures/) | 按实验版本保存的配置、预测、评分与图件 |
| [data/](data/) · [docs/](docs/) | 数据和可追溯研究文档 |

历史文档从默认阅读入口退役，原路径、内容和实验产物保留。复查优先使用保存结果；旧计划、命令及剩余预算不构成新训练授权。
