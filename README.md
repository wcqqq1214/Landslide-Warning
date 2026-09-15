# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前研究

**截至2026-09-16本地时间，已完成回到原TiDE_KIN的跨时段与预测距离诊断。** 原60检查点全部重放，新训练/优化/B+拟合/物理前向均0，旧e400发报不变。第一窗前30日KIN平均RMSE为7.987134mm，DRIFT1为0.651233mm；失败不能只归为远期监督不足。181—293日期望损失权重在三个主窗为14.259250%/22.361684%/26.677111%，最终MJ3尾段持续低估且区间覆盖不足。未来可见范围影响早期输出，但尚未确认为失败原因。

[诊断研究记录](docs/ootang_tide_kin_diagnostic_results.v1.0.md) · [四张诊断图](figures/ootang_tide_kin_diagnostic_v1/20260915/README.md) · [全量分距离CSV](results/ootang_tide_kin_diagnostic_v1/20260915/diagnostic_v1/issued_by_point.csv) · [核验](docs/ootang_tide_kin_diagnostic_validation.v1.0.md) · [最终回执](results/ootang_tide_kin_diagnostic_v1/20260915/final_receipt.json)

283来源、45766892独立数值、四图16面板/21088图形值和11项解释检查完成。仍是已暴露重叠窗口的探索性分析；执行通过不代表新的效果改善，用户/导师尚未验收。下一候选是按预测距离隔离更晚协变量的TiDE_KIN输入规则，尚未实施；距离均衡损失另作后续配对，不同时改动，不追加H/RL/轮数或重选检查点。仅本地Markdown/CSV/PNG/SVG，无push/PR/PDF汇报。

## 前轮结果（历史完成）

**截至2026-09-16本地时间，冻结TiDE_KIN与历史样本外校正已完整完成：CAL、HCAL三窗集成误差均高于原KIN，当前H附加校正路线按冻结规则收束。** 实验目录沿用2026-09-15 UTC日期。

[研究记录](docs/ootang_tide_correction_results.v1.0.md) · [四张研究图](figures/ootang_tide_correction_v1/20260915/README.md) · [完整39组CSV](results/ootang_tide_correction_v1/20260915/analysis/phase_summary.csv) · [历史监督支持](results/ootang_tide_correction_v1/20260915/mature_support.json) · [核验](docs/ootang_tide_correction_validation.v1.0.md)

原KIN权重与发报精确保留，CAL/HCAL同13179可训练参数，只改变校正器是否读取H。残差来自按当时可用数据重建的历史训练外预测，全部293日条件驱动和标签兑现后才能训练校正器。新增21个历史基模型与18个校正器，共39拟合15600更新；201新/启动检查点加60原KIN检查点全部核验。四起点完整293日条件预测完成，无预测期位移反馈，无B+重拟合/物理前向。以下是三种子集成的四点平均RMSE，单位mm。

| 起点 | B+ | TiDE_KIN | TiDE_CAL | TiDE_HCAL |
| --- | ---: | ---: | ---: | ---: |
| 792 | 27.313325 | 22.907124 | 42.602315 | 42.799103 |
| 972 | 67.167389 | 5.534082 | 17.984216 | 19.006908 |
| 1168 | 9.124173 | 7.540029 | 14.133238 | 10.541766 |

HCAL对CAL只在最终窗集成均值改善，同种子MAE/RMSE同时改善数3/0/0，与集成方向并不一致；三窗均值门均未通过。两新版对KIN三窗MAE/RMSE/CRPS都退步，三组B+联合均0/3。CAL最终对B+概率门通过但均值失败；HCAL的MJ3覆盖仅62.12%。不按点、种子或统计口径挑选赢家。

2216来源、261检查点、89997623独立数值及四图16面板/257388图形值核验通过。完整历史路径数0/20/200/396，第一评价窗只来自同一480前缀弱基模型，路径高度重叠；残差可迁移性未获证明。窗口已暴露且重叠，全部探索性。本轮结束，不追加训练/上界搜索/RL。仅研究记录/CSV/PNG/SVG、本地提交，无push/PR/PDF汇报，用户/导师尚未验收。

[前轮联合融合](docs/ootang_tide_fusion_results.v1.0.md)、[K/H输入消融](docs/ootang_tide_features_results.v1.0.md)、[原TiDE配对](docs/ootang_tide_direct_results.v1.0.md)、[历史教师更新](docs/ootang_teacher_refresh_results.v1.0.md)、[200/400训练对照](docs/ootang_training_sufficiency_results.v1.0.md)、[GRU/Transformer起点表达](docs/ootang_backbone_anchor_results.v1.0.md)及[旧半残差](docs/ootang_transformer_temporal_results.v1.0.md)的正负结果均保留。

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
| [code/tide_kin_diagnostic/](code/tide_kin_diagnostic/) | 原KIN全部检查点重放、成熟监督/误差/可见性诊断及独立核验 |
| [code/tide_correction/](code/tide_correction/) | 原KIN冻结、完整成熟历史训练外残差、小校正配对及独立核验 |
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
