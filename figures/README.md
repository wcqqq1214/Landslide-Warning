# Figures 产物说明

本目录保存可由 `code/` 下各分组脚本重建的结果快照。PNG 是展示图，CSV 是支撑图表、复核数值和追踪逐日结果的审计表；它们都不是原始监测数据，也不应手工修改。当前大部分模型产物以 Figshare 发布物化日序列为输入，不是独立原始 GNSS 上的确认性结果。

> `convlstm/` 根目录下的 `rolling_validation_*`、`seed_stability_*`、`inner_validation_*` 与 `capacity_*` 均是加入高程前的 6 输入通道历史快照。当前 7 输入通道的 fixed120 滚动验证与五种子诊断只写入下述版本化 `runs/displacement_elevation_exog_v1/fixed120_v1/` 目录，不得跨目录混用。7 通道早停与容量敏感性尚未运行，Vajont 也未启动。

> **当前代码树（2026-08-15）**：默认入口为 `features → convlstm → ootang-operational-v4`。当前可运行的解释支路仅为独立 NGBoost 回归 + SHAP；旧 30 日 `V0`、旧分类、旧融合及 v1/v2/v3 运行脚本已移出工作树，仅可通过 Git 历史恢复。schema 3 管线清单保存逐阶段输入/输出路径、大小、SHA-256、源码指纹和工作树状态。v4 仍为非正式原型，未读取或启动 Vajont。

> **已退役产物删除（2026-08-15）**：`ngboost/`、`warning_fusion/`、`warning_onset/`、`thresholds/`、`sensitivity/`、`warning_draft/`、`warning_operational_draft/`、`warning_operational_draft_v2/`、`warning_operational_draft_v3/`、`warning_review/` 共 82 个文件，以及 `pipeline/latest_run.json`（v3 阶段残留记录）和 `pipeline/shap_stability_run.json`（已退役 `shap-stability` 阶段）已从工作树删除；恢复请查阅 Git 历史提交 `7d2e38b` 及其之前的快照。删除范围经核验不影响 v4 管线：v4 链只读 `figures/convlstm/`，写 `warning_draft_v4/` 与 `warning_operational_draft_v4/`，删除后 13 个 v4 manifest 路径哈希与 262 项测试全部通过。

## 当前独立 NGBoost 回归 SHAP 产物

下列文件由当前 `ngboost-shap` 阶段生成。目标为相邻观测的位移增量，只用于描述候选模型依赖；不解释 ConvLSTM、不推断物理因果，也不是正式五级预警分类器。跨折稳定性和删组诊断未纳入当前精简原型。

| 文件 | 作用 | 边界 |
| --- | --- | --- |
| `shap/ngboost_regression_metrics.csv` | 单一时序留出下的独立 NGBoost 回归指标 | 仅为模型依赖分析的质量摘要，不等同预警性能 |
| `shap/ngboost_regression_shap_importance.csv` | 留出样本的 mean absolute permutation-SHAP 排序 | 相关特征会分摊贡献，不能解释为唯一主控因素 |
| `shap/ngboost_regression_shap.png` | 独立 NGBoost 回归 SHAP 可视化 | 不是 ConvLSTM-SHAP 图 |
| `shap/ngboost_regression_shap_provenance.json` | 输入、模型和输出指纹 | 用于重建与审计，不解除数据血缘门禁 |

## 7 通道 fixed120 版本化诊断

本次仅运行藕塘 `convlstm-rolling` 和 `convlstm-seeds` 两个内部探索性阶段；高程按“测点间 z-score 后水平 IDW”形成静态第 2 通道。运行固定为 3 个 287 日测试折、`seed=0` 滚动验证以及 `seed=0-4` 五种子诊断，不选择最佳种子。两个阶段 manifest 均保留 `formal_warning_output=false` 和 `confirmatory_external_validation=false`，总管线 manifest 也固定 `formal_warning_output=false`；这些产物不能解释为正式预警、外部验证或高程的因果增益。

| 文件 | 数量 | 作用 |
| --- | ---: | --- |
| `pipeline/convlstm_elevation_fixed120_v1_run.json` | 2 个阶段、10 个阶段产物 | 保存本次 rolling/seed 执行顺序、提交与源码指纹、耗时、阶段契约状态以及每个产物的大小和 SHA-256；不包含早停、容量或 Vajont 阶段 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json` | 3 个输入、4 个项目源码、3 个 CSV 输出 | 固定 7 通道架构、协议全文、运行环境、提交、输入/源码/输出哈希及探索性证据边界 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/rolling_validation_folds.csv` | 3 行 | 保存三个扩展窗口折的 fit/calibration/test 日期、固定 120 轮参数、seed=0 和逐测点 `qhat` |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/rolling_validation_metrics.csv` | 54 行 | 保存 3 折 × 9 个总体/测点范围 × 原始/校准区间的点预测、增量响应、覆盖率和区间指标 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/rolling_validation_predictions.csv` | 6,888 行 | 保存 3 折 × 287 日 × 8 测点的完整 seed=0 逐日预测键集 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json` | 7 个输入、5 个项目源码、5 个 CSV 输出 | 除数据、坐标和冻结协议外，逐项绑定 rolling 的 3 个 CSV 与 manifest；保存五种子结果和全部哈希 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_runs.csv` | 15 行 | 保存 5 个种子 × 3 折的固定协议、时间边界、`qhat` 和增量尺度 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_metrics.csv` | 270 行 | 保存 5 个种子 × 3 折 × 9 个范围 × 2 个区间版本的完整指标，不按结果选择种子 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_summary.csv` | 54 行 | 汇总各折、范围和区间版本的五种子均值、样本标准差、范围及 skill 符号一致性 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_training.csv` | 1,800 行 | 保存 15 次拟合 × 120 轮的 pinball loss 与梯度 L2 范数 |
| `convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv` | 34,440 行 | 保存 5 个种子 × 3 折 × 287 日 × 8 测点的完整逐日预测键集；其中 seed=0 与 rolling 产物逐值复现 |

`runs/displacement_elevation_exog_v1/fixed120_v1/inner_validation_v1/` 和 `capacity_sensitivity_v1/` 当前不存在，表示 7 通道早停与容量敏感性没有运行，而不是结果缺失。Vajont 输入文件也未被本次管线读取或转换为结果。

| 文件 | 作用 | 类型 | 论文用途 |
| --- | --- | --- | --- |
| `data_lineage/ootang_data_lineage_manifest.json` | 固定 Figshare 来源、XLSX/CSV 哈希与一致性、代码/输入指纹、预测键集、自然月结构状态和数据闸门 | 数据血缘总清单 | `prototype_run_gate=allowed`，但 `confirmatory_evidence_gate=blocked`、`formal_warning_output=false`；不推断具体生成算法或已证实未来泄漏 |
| `data_lineage/ootang_monthly_polynomial_fingerprint.csv` | 保存 9 个目标列和 5 个负对照逐自然月四阶差分、三次残差和二次误差 | 数据结构审计表 | 支撑“发布序列具有强自然月分段三次指纹”，不是插值算法识别 |
| `data_lineage/ootang_column_fingerprint_summary.csv` | 汇总各列 48 个月通过数、月内/跨月四阶差分窗口和断点日 | 数据结构摘要 | 区分目标列与环境负对照，不能作为预警阈值 |
| `data_lineage/ootang_split_boundary_audit.csv` | 检查三个模型边界是否切穿同一月内三次段 | 时间边界审计表 | 证明发布序列在边界两侧存在同月代数结构；上游生成独立性与未来信息使用仍未知 |
| `data_lineage/ootang_split_cross_boundary_predictability.csv` | 量化三次段跨边界的代数外推/回代误差 | 代数依赖诊断表 | 固定为 `not_forecast_evaluation`，不得写成模型预测成绩 |
| `data_lineage/ootang_prediction_alignment_summary.csv` | 保存冻结 fit/calibration/test 日期键集、8 测点行数以及 actual/persistence 对齐误差 | 工程对齐审计表 | 证明仓库预测表键集与特征表一致，不证明上游日值未使用未来锚点 |
| `convlstm/forecast_all_stations.png` | 展示 8 个测点的全时间轴位移、fit 诊断、校准段与留出 test/prediction 段的 P10/P50/P90 区间及边界 | 探索性内部结果图 | 物化序列内部位移预测主图；fit 只作诊断，test 也不是原始数据确认性证据 |
| `convlstm/forecast_predictions.csv` | 保存逐日逐测点的 `split`、实际位移、持久性、原始 P10/P50/P90、校准端点、`qhat` 与端点适用状态 | 逐时刻审计表 | 可从 CSV 复画全测点主图；fit 行刻意没有校准端点，test 校准端点只来自先前 calibration 段 |
| `convlstm/forecast_metrics.csv` | 保存各测点测试段校准前后的 RMSE、MAE、R2/NSE、持久性基线、pinball loss、覆盖率、宽度和 80% interval score | 探索性内部评估表 | 仅衡量模型相对于发布物化序列的误差；用 `interval_variant` 区分未校准和校准区间，R2/NSE 仅作补充 |
| `convlstm/forecast_period_metrics.csv` | 将 287 日测试段按日期连续分为三个块并保存校准前后同组指标 | 时间稳定性审计表 | 检查总体均值是否掩盖后期性能退化，不代替滚动时间验证 |
| `convlstm/forecast_calibration_metrics.csv` | 保存拟合/校准/测试日期边界、测点独立 `qhat` 及校准前后覆盖率、宽度、pinball 和 interval score | 校准审计表 | 证明仓库代码采用日历先后切分并量化宽度-覆盖率代价；不证明上游生成独立，也不提供严格覆盖保证 |
| `convlstm/forecast_run_manifest.json` | 记录高程感知初跑的数据/坐标哈希、`elev_m` 标准化与 IDW 方法、7 个输入通道、切分和全部输出哈希 | 原型运行清单 | `prototype_run_gate=allowed`、`confirmatory_evidence_gate=blocked`；证明高程实际进入模型，不证明其带来因果作用或确认性增益 |
| `warning_operational_draft_v4/ootang_operational_run_manifest.json` | 保存 v4 加速度三族融合、v3 双轴契约、v1 基础协议与 v2 加速度扩展的双 SHA-256、实现源码指纹、输入/输出哈希、行数和状态计数 | v4 非正式空间草案清单（默认阶段） | 严格逐点 `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，`A0=max(1.5A,A+2sigma_a)`；固定 `formal_warning_output=false`、`vajont_used=false`，不表示现场验证 |
| `warning_operational_draft_v4/ootang_operational_{thresholds,station_timeline,site_timeline}.csv` | 保存 8 行 fit-only 加速度阈值、4,112 条测点三族等级和 514 行滑坡体双轴结果；raw `delta_v` 仍可审计 | v4 非正式审计表 | 加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`；v3 核心 CSV 不被覆盖 |
| `warning_operational_draft_v4/ootang_v4_{typical_days,full_warning_timeline,all_station_combined_diagnostic}.{svg,png}` | v4 代表日、完整双轴时间线和 8 点 interval/velocity/acceleration/tangent/fused 联合图 | v4 非正式规则解释图 | 每个图件 manifest 均复制核心输出哈希、行数、实现来源和 v1/v2 双协议哈希；canonical bundle 不依赖 PDF，需排版时可用 `OOTANG_V4_EXPORT_PDF=1` 本地生成可选 sidecar |
| `convlstm/forecast_bootstrap_ci.csv` | 保存总体和各测点在 7/14/30 日连续块下的点估计、95% 百分位区间、配对差值及完整重采样参数 | 不确定性审计表 | 14 日为主分析，7/30 日为敏感性；模型和 `qhat` 固定，不能解释为训练或未来漂移不确定性 |
| `convlstm/rolling_validation_folds.csv` | 保存三个扩展窗口折的拟合/校准/测试边界、模型配置、随机种子和逐测点 `qhat` | 历史 6 通道验证协议审计表 | 仅作加入高程前的历史对照；当前 7 通道结果使用上方版本化路径 |
| `convlstm/rolling_validation_metrics.csv` | 保存每折总体及 8 测点的原始/校准区间指标、持久性基线和增量偏差 | 历史 6 通道滚动评估表 | 仅作加入高程前的历史对照，不得与版本化 7 通道指标拼接 |
| `convlstm/rolling_validation_predictions.csv` | 保存三个测试折逐日逐测点的真实值、持久性、P10/P50/P90、校准端点和 `qhat` | 历史 6 通道逐日审计表 | 仅用于复核历史滚动指标，不作当前模型或独立外部证据 |
| `convlstm/seed_stability_runs.csv` | 保存种子 0-4 各折时间边界、固定参数、`qhat` 和增量尺度 | 历史 6 通道多种子协议表 | 仅作加入高程前的历史对照；当前 7 通道使用版本化 seed bundle |
| `convlstm/seed_stability_metrics.csv` | 保存逐种子、逐折、总体/测点的点误差、增量响应和区间指标 | 历史 6 通道多种子明细表 | 不得当作当前高程模型的初始化稳定性证据 |
| `convlstm/seed_stability_summary.csv` | 保存五种子均值、样本标准差、范围、正 skill 数量和符号一致性 | 历史 6 通道稳定性汇总表 | 仅作历史对照，不得替代版本化 7 通道汇总 |
| `convlstm/seed_stability_training.csv` | 保存 15 次训练每个 epoch 的全批量 pinball loss 与梯度 L2 范数 | 历史 6 通道优化轨迹表 | 仅用于历史数值稳定性复核，不代表当前 7 通道训练轨迹 |
| `convlstm/inner_validation_runs.csv` | 保存内层训练/验证、外层校准/测试边界、停止参数、所选 epoch、停止原因、尺度和 `qhat` | 历史 6 通道早停协议表 | 7 通道早停尚未运行；本文件不得作为当前模型的 epoch 依据 |
| `convlstm/inner_validation_selection_history.csv` | 保存 15 次内层选择的逐轮训练/验证 pinball loss、梯度和耐心计数 | 历史 6 通道模型选择轨迹表 | 7 通道早停尚未运行；仅复核历史停止触发 |
| `convlstm/inner_validation_refit_history.csv` | 保存按所选 epoch 在完整拟合期重新初始化训练的逐轮 loss 和梯度 | 历史 6 通道最终重训轨迹表 | 不代表当前 7 通道模型的训练轮数或轨迹 |
| `convlstm/inner_validation_metrics.csv` | 保存早停版本逐种子、逐折和逐测点的外层预测指标 | 历史 6 通道早停评估表 | 7 通道早停尚未运行，不得与当前 fixed120 结果作正式比较 |
| `convlstm/inner_validation_summary.csv` | 保存早停版本五种子均值、样本标准差、范围及 skill 方向 | 历史 6 通道早停汇总表 | 仅作历史对照，不能判断 7 通道 epoch 选择效果 |
| `convlstm/inner_validation_predictions.csv` | 保存早停版本 15 次外层测试的逐日逐测点预测 | 历史 6 通道逐日审计表 | 7 通道早停尚未运行，不得用于当前模型复算 |
| `convlstm/inner_validation_comparison.csv` | 将早停与固定 120 轮按种子、折、测点和区间版本一一配对 | 历史 6 通道训练轮数诊断表 | 不得把历史配对结论外推到当前 7 通道模型 |
| `convlstm/capacity_candidates.csv` | 保存四个预注册配置在五种子三折上的最优内层 loss、epoch 和停止原因 | 历史 6 通道容量候选表 | 7 通道容量敏感性尚未运行；候选结果只作历史对照 |
| `convlstm/capacity_selection_summary.csv` | 保存每折四个配置的内层验证 loss 均值、样本标准差、范围、epoch 和排名 | 历史 6 通道容量选择汇总表 | 不得作为当前 7 通道配置选择依据 |
| `convlstm/capacity_selection_history.csv` | 保存 60 次候选运行的逐轮训练/验证 loss、梯度和耐心计数 | 历史 6 通道候选优化轨迹表 | 7 通道容量敏感性尚未运行；仅复核历史候选 |
| `convlstm/capacity_selected_runs.csv` | 保存每折所选配置、时间边界、种子、epoch、尺度和 `qhat` | 历史 6 通道最终运行协议表 | 不代表当前 7 通道模型的所选配置 |
| `convlstm/capacity_selected_refit_history.csv` | 保存 15 次所选配置完整拟合期重训曲线 | 历史 6 通道最终重训轨迹表 | 不代表当前 7 通道模型的重训轨迹 |
| `convlstm/capacity_selected_metrics.csv` | 保存所选配置逐种子、折和测点的外层指标 | 历史 6 通道容量评估表 | 7 通道容量敏感性尚未运行，不得据此判断当前模型容量 |
| `convlstm/capacity_selected_summary.csv` | 保存所选配置跨种子均值、样本标准差、范围和 skill 方向 | 历史 6 通道容量汇总表 | 仅作历史对照，不得替代当前 7 通道证据 |
| `convlstm/capacity_selected_predictions.csv` | 保存所选配置 15 次外层测试逐日逐测点预测 | 历史 6 通道逐日审计表 | 7 通道容量敏感性尚未运行，不得用于当前模型复算 |
| `convlstm/capacity_selected_comparison.csv` | 将所选配置与当前早停参照按种子、折、测点一一配对 | 历史 6 通道容量/正则化诊断表 | 不得把历史比较结论外推到当前 7 通道模型 |
| `shap/shap_provenance.json` | 固定独立解释模型、两个目标、样本时段、背景样本和解释边界 | 溯源清单 | 明确不是 ConvLSTM-SHAP、因果结论或正式五级预警 |
| `shap/shap_reg_summary.png` | 展示独立 NGBoost 对目标观测位移增量的 SHAP 分布 | 探索性解释图 | 图题写明模型和留出解释样本时段；只解释模型依赖 |
| `shap/shap_cls_summary.png` | 展示独立 NGBoost 对遗留同日 V0 标签 `warning_level >= 1` 的 SHAP 分布 | 探索性解释图 | 不是正式五级预警或未来 onset 预警；不作因果结论 |
| `shap/shap_reg_importance.csv` | 保存回归 mean absolute SHAP 排序及模型/目标/样本字段 | 支撑表 | 生成变量重要性表和跨折稳定性分析 |
| `shap/shap_cls_importance.csv` | 保存遗留分类 mean absolute SHAP 排序及模型/目标/样本字段 | 支撑表 | 必须连同 `target_status` 读取，不得称作正式预警结果 |
| `shap/shap_model_metrics.csv` | 保存单次时间留出的回归/遗留分类指标、目标和样本信息 | 探索性评估表 | 说明当前探索性性能和类别不平衡，不评价正式预警 |
| `shap/shap_binary_cv_metrics.csv` | 保存 5 折扩展窗口遗留分类结果及持续性基线 | 交叉验证审计表 | 逐折报告；单类别折不能汇总 AUC，也不表示未来预警能力 |
| `shap/stability/cross_fold_protocol.csv` | 保存两个任务五折的模型、目标、时间边界、抽样量、参数和主指标 | 协议审计表 | 核对每折仅使用允许的训练历史和固定解释预算 |
| `shap/stability/cross_fold_feature_importance.csv` | 保存 88 个特征逐任务逐折的绝对/归一化 SHAP、排名和方向相关 | 明细解释表 | 支撑特征排名与方向复算；方向仅为模型依赖 |
| `shap/stability/cross_fold_feature_stability.csv` | 汇总每个特征的跨折排名、top10 次数和方向一致性 | 稳定性汇总表 | 识别跨时间折重复依赖，不作因果解释 |
| `shap/stability/cross_fold_rank_stability.csv` | 保存所有折对的特征级和组级 Spearman 排名相关 | 稳定性审计表 | 描述时间折间排序一致性；折间训练历史重叠，不做独立显著性检验 |
| `shap/stability/cross_fold_station_feature_importance.csv` | 保存逐任务、逐折、逐测点的特征 SHAP 指标 | 测点分层明细表 | 描述同一模型在不同测点子样本上的依赖，不是留一测点外推验证 |
| `shap/stability/cross_fold_station_feature_stability.csv` | 汇总逐测点特征的跨时间折排名和方向稳定性 | 测点/时间稳定性表 | `station_*` 为模型标识输入，不得视为地质主控因素 |
| `shap/stability/cross_fold_group_importance.csv` | 保存五个预设特征组逐任务逐折的归一化 SHAP 份额和排名 | 组级解释表 | 与删组消融联合判断模型依赖，不能单独按份额选特征 |
| `shap/stability/group_ablation_fold_metrics.csv` | 保存完整模型及五个删组模型的逐折主/辅助指标 | 消融明细表 | 报告全部折，正退化表示删组后主指标变差 |
| `shap/stability/group_ablation_summary.csv` | 汇总各组退化量的均值、中位数、范围和符号折数 | 消融汇总表 | 判断删组影响方向是否跨折一致，不以均值掩盖单折反转 |
| `shap/stability/shap_group_stability.png` | 展示两个任务五折的组级归一化 SHAP 份额 | 诊断图 | 直观看回归稳定与分类时期异质性；数值以 CSV 为准 |
| `shap/stability/group_ablation.png` | 展示各删组主指标退化量及均值 | 诊断图 | 比较跨折方向；不作为显著性检验或因果证据 |
| `tangent_angle/uniform_rates.csv` | 保存各测点自动等速候选段、参考速率和稳定性统计 | 参数审计表 | 专家复核 `v_eq`，不能直接当作已验证参数 |
| `tangent_angle/review/*_stage_review.png` | 8 个测点的累计位移、速率、加速度和 15/30/60 日候选阶段复核图 | 专家复核图 | 供专家结合宏观变形资料独立确定等速阶段，不标注"最佳阶段" |
| `tangent_angle/review/candidate_stage_comparison.csv` | 8 个测点在 15/30/60 日窗口下的参数来源、速率统计、切线角等级、相对 30 日一致率及融合影响 | 综合审计表 | 供专家核对参数影响，不得按一致率或报警天数自动选优 |

## 保留原则

- 论文图表和 `docs/results_report.md` 引用的结果快照保留在 Git 中，以便数值可追溯。
- 各 CSV 承担不同任务，不是重复文件。需要清理空间时可以整体删除并按 README 的运行顺序重建，但不要只删除某一张支撑表后继续引用旧结果。
- 已退役路线的产物（旧 `V0` 阈值表、敏感性、onset、旧融合、旧分类图、v1/v2/v3 运行快照和专家审查产物）已于 2026-08-15 从工作树删除；需要复现其数值时按 Git 历史恢复对应提交，不要重新在当前目录下生成同名文件。
- 已存档的旧 CSV、PNG 与模型副本仍作为历史快照保留，但其生成脚本、专属测试和旧清单已从当前工作树移除；若需恢复其生成语义或路径对应关系，应查阅 Git 历史，而不是把它们视为当前方法的一部分。
