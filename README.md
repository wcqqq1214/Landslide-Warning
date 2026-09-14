# Landslide-Warning

藕塘滑坡四点位移概率预测科研原型。研究范围为 ATU1、ATU5、MJ3、MJ1，目标是通过
改进 B+ 物理引导，降低均值误差并改善概率区间；当前不开展预警或新案例。

**当前状态（2026-09-14）：小型 TCN 的内部、开发和完整后期比较、独立核验与交付均已完成，实验停止。**
本轮在 `codex/tcn-short-horizon` 比较 TCN_DIRECT 与 TCN_BRES，内部共同选定400次更新，
三个种子、三阶段共18次拟合、7200次更新。预测数组、检查点、逐点/七步长表与开发名单已保存；
72份权重重载，36份评价检查点全量前向复算通过；117600行预测明细、全部结果表和16张最终图已核对。
[完整结论](docs/ootang_tcn_results.v1.0.md)／[CSV汇总](results/ootang_tcn_v1/20260914/analysis/summary_by_horizon.csv)／[图件索引](figures/ootang_tcn_v1/20260914/README.md)／[最终回执](results/ootang_tcn_v1/20260914/final_receipt.json)。
实施与核验通过，整体效果未达标；用户/导师效果验收未提供。

两版 TCN 在开发及后期的七个步长均降低了相对 B_ANCHOR 的平均 MAE、RMSE、CRPS 和90%区间评分，
但这些指标均落后于 DRIFT1 和普通 RR_DIRECT，两版 TCN 均未通过完整工作条件。
开发七步长的均值和概率优胜者均为 RR_DIRECT；共同条件推荐为第1—6天，第7天没有通过者，
不能将“评分最低”写成“全部条件达标”。来源见[开发锁定](results/ootang_tcn_v1/20260914/selection.json)
与[后期固定名单评价](results/ootang_tcn_v1/20260914/later_exploratory/frozen_selection_evaluation.json)。

下表为后期第7天的四点指标平均值；RMSE、CRPS和区间评分单位为 mm，覆盖率为百分比，
每点287个合法起点，目标日期为2019-09-18—2020-06-30。

| 方法 | RMSE | CRPS | 90%区间评分 | 90%覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| B_ANCHOR | 1.784162 | 0.904383 | 9.246043 | 78.6585% |
| DRIFT1 | 0.278587 | 0.126017 | 1.360112 | 75.3484% |
| RR_DIRECT | 0.037318 | 0.021510 | 0.222347 | 86.6725% |
| TCN_DIRECT | 0.856186 | 0.475485 | 3.727784 | 85.6272% |
| TCN_BRES | 0.771781 | 0.424421 | 3.982303 | 76.8293% |

完整来源：[后期七步长表](results/ootang_tcn_v1/20260914/later_exploratory/summary_by_horizon.csv)、
[逐点表](results/ootang_tcn_v1/20260914/later_exploratory/metrics_by_point_horizon.csv)、
[开发七步长表](results/ootang_tcn_v1/20260914/development/summary_by_horizon.csv)。
残差版在开发段的四项平均误差评分均优于直接版；后期第3—7天平均 MAE/RMSE 较低，
但第1—2天 RMSE 较高，七个步长90%区间评分均更差。后期种子1的RMSE改善，种子0/2全部七步长退步；因此只支持部分集成均值收益，未建立稳定整体收益。

本轮每日收到新观测后，用最近30日观测和合法 B+ 物理背景，一次预测四点未来第1—7天，保留已发预测。
两版 TCN 使用相同输入、结构、种子、批次、单位与训练次数，只改变输出基线及监督目标；
这检验 B+ 残差输出方式，不等于有/无物理信息消融，也不是 PINN 或严格物理约束预测。
未来降雨为过去七日均值、库水位保持最后值；概率层为最近90条已兑现误差的经验高斯校准。
C16 仅作历史补充，不参与本轮选模或门槛；ConvLSTM/PINN 不重新训练。

| 入口 | 用途 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 当前范围、导师目标与协作规则 |
| [当前进度](docs/progress.md) | 当前状态、最近维护与后续边界 |
| [文档导航](docs/README.md) | 当前决策、四点实验和历史八点成果的来源 |
| [TCN 完整结果](docs/ootang_tcn_results.v1.0.md) / [核验与解释边界](docs/ootang_tcn_validation.v1.0.md) / [图件](figures/ootang_tcn_v1/20260914/README.md) | 已完成的固定配对实验、全部七步长曲线、负结果和独立核验 |
| [TCN 冻结计划](docs/ootang_tcn_plan.v1.0.md) / [配置](config/ootang_tcn.v1_0.json) / [来源清单](docs/ootang_tcn_sources.v1.0.json) | 本轮固定结构、日期、教师、共同检查点、校准与效果条件 |
| [TCN 实验目录](results/ootang_tcn_v1/20260914/) / [训练登记](results/ootang_tcn_v1/20260914/fit_registry.json) | 三阶段保存结果、各种子权重、日志和实际训练次数 |
| [v4.0 短期比较](docs/ootang_short_horizon_comparison_results.v4.0.md) | 同任务历史对照、在线回归结果、PINN 物理失败及限制 |
| [神经初态完整结果](docs/ootang_neural_initial_state_results.v1.1.md) | 数值物理检查与预测收益分开报告；已停止 |
| [加性 GP v2 结果](docs/ootang_bplus_additive_gp_results.v2.md) / [GP v1 结果](docs/ootang_bplus_gp_results.v1.md) | 已完成的局部收益、完整条件失败和停止记录 |
| [方法筛选决策](docs/ootang_method_selection_2026-09-12.md) | 当时长窗候选为零的历史决定，不覆盖后续独立授权 |
| [v2.3 结果](docs/ootang_probability_pinn_results.v2.3.md) / [冻结方案](docs/ootang_probability_pinn_plan.v2.3.md) | 开发段失败并停止；完整模型、物理对照、概率指标和复算依据 |
| [293 日复核与当前方向](docs/ootang_293day_prediction_audit_2026-09-11.md) | 来源/参数/日期、全窗图表、最新尾段解释及停止点 |
| [v2.2 需求记录](docs/ootang_tail_scope_and_direction.v2.2.md) | 保留形成时的尾段要求；后续澄清以当前复核报告为准 |
| [v2.1 暂缓候选](docs/ootang_probability_pinn_plan.v2.1.md) | 原概率 PINN 方案保留，未实现或训练 |
| [v2.0 结果](docs/ootang_convlstm_direct_results.v2.0.md) | 一次有界 ConvLSTM 实验的效果、完整判据与停止决定 |

历史长窗及旧模型停止结论保持，不由本轮短期结果改判。最初三组为 M0 改进 B+、M1 ConvLSTM、
M2 方程内修正混合模型；M2 不称经典 PINN。旧293日、30日滚动和当前1—7日任务分别解释。
导师允许困难尾部存在偏差，但没有指定删去日期；完整窗口和负结果继续保留。

原始日值 as-of、既有预处理和历史窗口反复暴露的限制保持；当前后期评价明确为探索性，
不是新盲测、现场部署或真实预警验证。当前可用驱动场景与历史已知未来实测驱动对照分别记录。
旧报告源码、图件和形成时记录保留作历史备份；已取消的短期简报 PDF 不再作为当前交付入口。

| 目录/文件 | 用途 |
| --- | --- |
| `code/physics_guided*`、`config/` | 各版四点方法、诊断实现与冻结配置，按对应版本查证 |
| `code/tcn_short_horizon/`、`results/ootang_tcn_v1/` | 独立 TCN 入口、两臂固定实验和保存产物 |
| `code/short_horizon/`、`results/ootang_short_horizon_v4/` | v4.0 数据、物理、校准评分接口及历史同任务对照 |
| `results/ootang_bplus_v1_*/`、`results/ootang_convlstm_v2_0/` | 已完成四点实验的权重、日志、指标、核验与负结果 |
| `results/ootang_probability_pinn_v2_3/` | v2.3 代码快照、权重、状态数组、物理对照、图表和失败证据 |
| `results/ootang_293day_audit/`、`scripts/audit_ootang_293day_predictions.py` | 冻结预测的复核产物与重评分脚本，无模型训练 |
| `docs/` | 当前入口及必须保留的版本化方法、结果和来源记录 |
| `main.py`、`code/convlstm/`、`code/warning/` | 历史八点预测及代理预警流程，不是当前四点实验入口 |
| `figures/`、`models/`、`paper/` | 各阶段图件、权重与历史报告源码，按版本区分 |
| `data/` | 数据来源与派生文件；新出现的用户文件不自动纳入实验 |

复查优先读取已保存数组、CSV与核验记录；运行入口和旧配置不自动构成重训授权。
本轮已按原计划完成核验与 Markdown/CSV/图件收尾；不追加结构、模型或训练轮数，不制作 PDF，剩余额度不转用。
v1.26 未运行的计划、配置与草稿已删除，Git `7fc5f29` 保留原字节。
原始数据、教师参数、冻结方案及历史失败记录保持；研究工作条件不冒充导师或工程验收。
