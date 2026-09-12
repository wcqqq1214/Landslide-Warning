# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型。研究范围为 ATU1、ATU5、MJ3、MJ1，目标是通过
改进 B+ 物理引导，降低均值误差并改善概率区间；当前不开展预警或新案例。

**当前状态：v2.3 概率 PINN 已完成开发段实验，均值、概率和神经近似均未达标，已停止。**
完整 376 日开发段平均 MAE 为 177.5373 mm（B+ 33.2651），CRPS 为 166.6350 mm（原 e0 28.1156），
90% 覆盖率为 0%；未进入最终 293 日训练。代码与复算通过，效果失败，保留 B+。
来源、完整曲线、判据与限制见[v2.3 结果](docs/ootang_probability_pinn_results.v2.3.md)。原 v2.1 与力探针继续暂缓。

此前导师最终 293 日的已有预测复核保持：
按冻结 `e48d3e1` 对齐 1168 日拟合参数与 2019-09-12—2020-06-30 全窗：M1/M2 均为零修正，
三组四点平均 RMSE 9.1242 mm、合并 RMSE 9.4506 mm，没有神经均值增益。M0 概率指标不适用。
导师允许圈出末端的局部偏差，用户已明确没有指定日期；不再把获取截点作为当前工作的前置条件。
全窗曲线、逐日误差、已有概率评分和补审处理见[293 日复核报告](docs/ootang_293day_prediction_audit_2026-09-11.md)。
方法仍只限定 ConvLSTM/PINN；v2.0 按原完整窗标准未达标，已停止。
此前的[v2.0 结果](docs/ootang_convlstm_direct_results.v2.0.md)严格均值改善 5/8，但第一窗误差和两窗 CRPS 变差。
八轮主要学习的 16 个变体中，没有一个同时降低两个预测窗的平均 RMSE，严格四项改善最高 3/8。
完整误差、概率评分与失败证据见[路线复盘](docs/ootang_route_review_2026-09-11.md)。

| 入口 | 用途 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 当前范围、导师目标与协作规则 |
| [当前进度](docs/progress.md) | 当前状态、最近维护与后续边界 |
| [文档导航](docs/README.md) | 当前决策、四点实验和历史八点成果的来源 |
| [v2.3 结果](docs/ootang_probability_pinn_results.v2.3.md) / [冻结方案](docs/ootang_probability_pinn_plan.v2.3.md) | 开发段失败并停止；完整模型、物理对照、概率指标和复算依据 |
| [293 日复核与当前方向](docs/ootang_293day_prediction_audit_2026-09-11.md) | 来源/参数/日期、全窗图表、最新尾段解释及停止点 |
| [v2.2 需求记录](docs/ootang_tail_scope_and_direction.v2.2.md) | 保留形成时的尾段要求；后续澄清以当前复核报告为准 |
| [v2.1 暂缓候选](docs/ootang_probability_pinn_plan.v2.1.md) | 原概率 PINN 方案保留，未实现或训练 |
| [v2.0 结果](docs/ootang_convlstm_direct_results.v2.0.md) | 一次有界 ConvLSTM 实验的效果、完整判据与停止决定 |

最初三组为 M0 改进 B+、M1 ConvLSTM、M2 方程内修正混合模型；后续另有状态 PINN。
M2 不是经典 PINN。原始日值当时的可用性仍未知，未来降雨/库水位作为给定驱动，
这些反复使用的历史窗口不能称新盲测或现场验证。

| 目录/文件 | 用途 |
| --- | --- |
| `code/physics_guided*`、`config/` | 各版四点方法、诊断实现与冻结配置，按对应版本查证 |
| `results/ootang_bplus_v1_*/`、`results/ootang_convlstm_v2_0/` | 已完成四点实验的权重、日志、指标、核验与负结果 |
| `results/ootang_probability_pinn_v2_3/` | v2.3 代码快照、权重、状态数组、物理对照、图表和失败证据 |
| `results/ootang_293day_audit/`、`scripts/audit_ootang_293day_predictions.py` | 冻结预测的复核产物与重评分脚本，无模型训练 |
| `docs/` | 当前入口及必须保留的版本化方法、结果和来源记录 |
| `main.py`、`code/convlstm/`、`code/warning/` | 历史八点预测及代理预警流程，不是当前四点实验入口 |
| `figures/`、`models/`、`paper/` | 历史八点阶段产物、权重与已提交报告 |
| `data/` | 数据来源与派生文件；新出现的用户文件不自动纳入实验 |

复现某次实验时查阅对应版本的实现说明与配置；本页不提供会覆盖旧输出或重启训练的快捷命令。
v1.26 未运行的计划、配置与草稿已删除，Git `7fc5f29` 保留原字节。
一次复核已完成，没有分段评分；不按模型成绩挑截点。
v2.3 的 MAE 与概率门槛是新候选建议，不是导师已确认的验收，也不用于改判旧结果或重置预算。
