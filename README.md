# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型，研究 ATU1、ATU5、MJ3、MJ1。目标是利用改进 B+ 物理引导，降低位移均值误差并改善概率区间；当前范围不含真实预警或新案例。

## 当前结果

**截至2026-09-15，TiDE物理特征分组消融已完整完成：输入组合的收益随时段改变，尚未达到四点跨窗均值与概率联合目标。**

[研究记录](docs/ootang_tide_features_results.v1.0.md) · [四张研究图](figures/ootang_tide_features_v1/20260915/README.md) · [完整27组CSV](results/ootang_tide_features_v1/20260915/analysis/phase_summary.csv) · [因素效应CSV](results/ootang_tide_features_v1/20260915/analysis/factorial_effects.csv) · [核验](docs/ootang_tide_features_validation.v1.0.md)

K为B+位移/日增量，H为B+水文状态。四组同一TiDE、180日历史、样本、三种子和固定400更新；共同降雨/水位驱动始终保留。新增KIN/HYD共24拟合9600更新、120检查点，精确复用DATA/PHYS的24拟合120检查点；四条293日路径全部完成，无B+重拟合/物理前向。表为三种子集成预测的四点平均RMSE，单位mm。

| 起点 | B+ | 既有GRU | TiDE_DATA | TiDE_KIN | TiDE_HYD | TiDE_PHYS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 792 | 27.313325 | 19.372056 | 50.874904 | 22.907124 | 29.660967 | 14.859012 |
| 972 | 67.167389 | 22.660456 | 33.672205 | 5.534082 | 25.069860 | 12.114521 |
| 1168 | 9.124173 | 10.600134 | 7.278984 | 7.540029 | 8.476753 | 8.921779 |

K组在第二与最终窗平均误差低于完整PHYS，第一窗退步；K存在时加入H的RMSE差为−8.048112、+6.580439、+1.381750mm。两新组最终MJ3仍差于B+，K组ATU1/MJ3/MJ1的90%覆盖不足80%。四组B+联合门均0/3，不将局部平均改善改判为整体达标。最终DATA平均最小也不能抵消前两窗的明显失败。

751来源、240检查点、187837220独立数值及四图16面板核验通过。窗口已暴露且重叠，条件预测给定完整未来驱动，分组效应不等于物理因果。下一研究假设为K/H分开编码与受限融合，本轮未追加结构/训练/λ/RL；不按最终点成绩拼接模型。只保存研究记录/CSV/PNG/SVG、本地提交，无push/PR/PDF汇报，用户/导师尚未验收。

[上一轮TiDE小试](docs/ootang_tide_direct_results.v1.0.md)、[历史教师更新](docs/ootang_teacher_refresh_results.v1.0.md)、[200/400训练对照](docs/ootang_training_sufficiency_results.v1.0.md)、[GRU/Transformer起点表达](docs/ootang_backbone_anchor_results.v1.0.md)及[旧半残差](docs/ootang_transformer_temporal_results.v1.0.md)的正负结果均保留。

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
