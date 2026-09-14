# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至 2026-09-15（本地时间），Transformer 半残差与区间校准验证已完整完成：均值改善大部分可由固定收缩复现，校准有探索性收益，整体效果条件仍未通过。**

[最新图文报告](docs/ootang_transformer_calibration_results.v1.0.md) · [四点 PNG / SVG](figures/ootang_transformer_calibration_v1/20260914/README.md) · [完整 36 组 CSV](results/ootang_transformer_calibration_v1/20260914/analysis/phase_summary.csv)

下表为四个测点各自 RMSE 的平均值，单位 mm；完整逐点、种子和概率评分见报告。

| 方法 | 开发段 376 日 | 最终探索段 293 日 |
| --- | ---: | ---: |
| 改进 B+ | 41.3684 | 9.1242 |
| DRIFT1 | 6.5413 | 12.9777 |
| 普通岭回归 | 45.8998 | 12.3382 |
| Transformer 残差原版 | 48.1866 | 9.2791 |
| Transformer 残差正则版 REG1 | 44.4136 | 8.9073 |
| Transformer 半残差 HALF | 44.6203 | 8.8671 |

HALF 固定保留原网络一半修正，新增训练为 0；其最终均值与 REG1 接近且略优，开发仍落后 B+。固定 REG1 均值后，按预测距离校准使最终 CRPS 从 17.6571 降至 7.7804，但同规则 B+ 为 7.5602。最终概率局部收益、MJ3 等逐点失败及开发失败共同保留，不据此宣布稳定神经增益或可靠覆盖。开发名单仍为 DRIFT1 / LAST90，搜索触发未通过，不追加 λ 或 RL。

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
| [code/transformer_calibration/](code/transformer_calibration/) | 最新保存预测半残差与离线区间校准验证 |
| [code/transformer_regularization/](code/transformer_regularization/) | 上一轮 Transformer 残差正则化实现 |
| [code/sequence_conditional/](code/sequence_conditional/) | 同协议 Transformer / CNN-Mamba 对照 |
| [code/tcn_conditional_trajectory/](code/tcn_conditional_trajectory/) | 同协议 TCN 与基线接口 |
| [config/](config/) · [results/](results/) · [figures/](figures/) | 按实验版本保存的配置、预测、评分与图件 |
| [data/](data/) · [docs/](docs/) | 数据和可追溯研究文档 |

历史文档从默认阅读入口退役，原路径、内容和实验产物保留。复查优先使用保存结果；旧计划、命令及剩余预算不构成新训练授权。
