# Figures 产物说明

本目录保存可由 `code/` 下各分组脚本重建的结果快照。PNG 是展示图，CSV 是支撑图表、复核数值和追踪逐日结果的审计表；它们都不是原始监测数据，也不应手工修改。当前大部分模型产物以 Figshare 发布物化日序列为输入，不是独立原始 GNSS 上的确认性结果。

> `convlstm/` 根目录下的 `rolling_validation_*`、`seed_stability_*`、`inner_validation_*` 与 `capacity_*` 均是加入高程前的 6 输入通道历史快照。当前 7 输入通道的 fixed120 滚动验证与五种子诊断只写入下述版本化 `runs/displacement_elevation_exog_v1/fixed120_v1/` 目录，不得跨目录混用。7 通道早停与容量敏感性尚未运行，Vajont 也未启动。

> **工程口径（2026-08-11）**：默认入口运行 `features → convlstm → ootang-operational-v4`；v3 为 explicit-only 数值对照，其余历史复现和诊断阶段必须显式选择。schema 3 管线清单保存逐阶段输入/输出路径、大小、SHA-256、源码指纹和工作树状态。`pipeline/latest_run.json` 可能是历史快照，不能脱离其中的提交和源码指纹解释；v4 仍为非正式原型，未读取或启动 Vajont。

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
| `pipeline/latest_run.json` | 保存统一入口某次实际运行的提交哈希、源码指纹、Python 版本、schema 3 阶段契约状态、输入/输出文件 SHA-256、退出码、耗时及工作树状态 | 工程验收清单（可能为历史快照） | 证明对应运行的执行范围、产物完整性和失败点；使用前核对提交/源码指纹，不作为模型性能证据 |
| `pipeline/shap_stability_run.json` | 保存 2026-06-23 历史 SHAP 稳定性单阶段运行的源码指纹、耗时和 9 个产物哈希 | 历史工程清单 | 仅追溯提交 `3c06d38` 的旧运行；不代表当前 `ΔV` 对齐后的产物 |
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
| `warning_draft/interval_calibration_diagnostics.csv` | 保存 8 个测点、仅 calibration 段的分位数顺序、覆盖率、中点误差和标准化残差原始摘要，以及协议和 calibration 输入切片哈希 | 区间门禁原始诊断表 | 为后续冻结门禁提供可复核证据；不含通过/失败、颜色或预警等级，不能作为正式预警结果 |
| `warning_draft/interval_calibration_diagnostics_manifest.json` | 保存诊断产物的协议状态、未评估项目、calibration 选段范围，以及 calibration 输入/输出哈希 | 草案运行清单 | 明确本次产物只作诊断且 `formal_warning_output=false`；哈希不随 held-out test 行变化，不得据此宣称区间五级映射已通过 |
| `warning_draft/ootang_draft_warning_evidence_manifest.json` | 保存当前七份有效藕塘草案诊断的固定执行顺序、每份组件的协议内容指纹/未决项、输出与 sidecar SHA-256 及排除的退役产物 | 草案证据总清单 | 证明同一版 `draft` 协议下的证据集可整体重建；其 `formal_warning_output=false`，只含项目比较器的候选 `V0`，不含指定 Word/正式 `V0`、速度/切线角等级、融合、正式时间线或 Vajont |
| `warning_operational_draft/ootang_operational_run_manifest.json` | 保存 v1 导师复核实施版的基础草案协议、可替换运行配置、输入/输出哈希、fit-only 参数来源及状态计数 | v1 非正式实施版总清单 | 证明四指标链路可完整重跑；固定为 `operational_draft_not_formal`、`formal_warning_output=false`、`vajont_used=false`，不能作为正式预警或 Word-MVIF `V0` 结论 |
| `warning_operational_draft/ootang_operational_{thresholds,station_timeline,site_timeline}.csv` | 保存 v1 的 8 点运行版基线/容差、calibration/test 四指标逐点结果和逐日多点汇总 | v1 非正式实施版审计表 | `uncorroborated`/`insufficient_valid_station_results` 不能并入 green，参数只来自 fit，不能用 test 期反调 |
| `warning_operational_draft_v2/ootang_operational_run_manifest.json` | 保存 v2 测点证据族、O1/O2/O3 来源、空间规则和结果计数 | v2 非正式空间草案清单 | 速度/切线角只算一个运动学证据族；未确认 yellow--red 不降级，不能解释为正式 `F_site` |
| `warning_operational_draft_v2/ootang_operational_{thresholds,station_timeline,site_timeline}.csv` | 保存 v2 逐点候选、`ΔV` 三态趋势/一致性复合信号、空间覆盖与跨区确认 | v2 非正式审计表 | 全局最少 3 点门禁已修复；`ΔV` 改变完整信号但不凭符号改变五色严重度，不得覆盖为 v3 |
| `warning_operational_draft_v3/ootang_operational_run_manifest.json` | 保存 v3 双轴契约、实现源码指纹、输入/输出哈希及整体/局部颜色计数 | v3 非正式空间草案清单（explicit-only 对照） | 固定 `formal_warning_output=false`、`vajont_used=false`；green 仍是项目规则状态，不是现场安全结论 |
| `warning_operational_draft_v3/ootang_operational_{thresholds,station_timeline,site_timeline}.csv` | 保存与 v2 相同的逐点指标/阈值，以及 `site_confirmed_*`、`local_max_candidate_*`、`local_attention_status` 双轴空间结果 | v3 非正式审计表 | 8 个 green 日仍保留局部 blue 关注；400 个未确认高候选不得并入 green，且 v3 不覆盖 v2 |
| `warning_operational_draft_v3/ootang_v3_typical_days.{svg,pdf,png}` | 六个冻结语义代表日的逐点 interval/kinematic/`ΔV`、整体/局部双轴和 O1/O2/O3 支撑诊断 | v3 非正式规则解释图 | SVG 保留可编辑文字；未确认 site 显式为 `NC`；属于观测后示例，不是性能、提前量或正式预警图 |
| `warning_operational_draft_v3/ootang_v3_typical_days_manifest.json` | 保存代表日规则配置、核心清单/CSV/渲染器指纹、所绘子集指纹及三个导出文件哈希 | v3 图件 provenance 清单 | 固定 `formal_warning_output=false`、`vajont_used=false`；核心实现指纹过期时拒绝绘图 |
| `warning_operational_draft_v3/ootang_v3_full_warning_timeline.{svg,pdf,png}` | 展示 514 日 × 8 测点候选五级状态及滑坡体 `site-confirmed/local maximum` 双轴 | v3 非正式全时序图 | 400 个 `NC` 明确表示未获空间确认而非缺测；属于观测后状态审计，不证明提前量或现场安全 |
| `warning_operational_draft_v3/ootang_v3_full_warning_timeline_manifest.json` | 保存全日期/测点覆盖、400 个 NC 语义、核心输入/渲染器指纹和三个导出文件哈希 | v3 图件 provenance 清单 | 固定 514 日、4,112 条测点记录、`formal_warning_output=false` 和 `vajont_used=false` |
| `warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic.{svg,pdf,png}` | 4×2 小多图对齐 8 点累计位移与 interval、velocity、`ΔV`、tangent、final 五条状态带 | v3 非正式联合诊断图 | 覆盖 514 日 × 8 点；`ΔV` 使用三态而非虚构五级；属于观测后审计，不证明提前量 |
| `warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic_manifest.json` | 保存联合图字段映射、完整键空间、状态计数、配置/输入/渲染器/共享支持层及导出哈希 | v3 图件 provenance 清单 | 固定 `formal_warning_output=false`、`vajont_used=false`，核心 manifest 或实现过期时拒绝绘图 |
| `warning_operational_draft_v4/ootang_operational_run_manifest.json` | 保存 v4 加速度三族融合、v3 双轴契约、v1 基础协议与 v2 加速度扩展的双 SHA-256、实现源码指纹、输入/输出哈希、行数和状态计数 | v4 非正式空间草案清单（默认阶段） | 严格逐点 `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，`A0=max(1.5A,A+2sigma_a)`；固定 `formal_warning_output=false`、`vajont_used=false`，不表示现场验证 |
| `warning_operational_draft_v4/ootang_operational_{thresholds,station_timeline,site_timeline}.csv` | 保存 8 行 fit-only 加速度阈值、4,112 条测点三族等级和 514 行滑坡体双轴结果；raw `delta_v` 仍可审计 | v4 非正式审计表 | 加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`；v3 核心 CSV 不被覆盖 |
| `warning_operational_draft_v4/ootang_v4_{typical_days,full_warning_timeline,all_station_combined_diagnostic}.{svg,png}` | v4 代表日、完整双轴时间线和 8 点 interval/velocity/acceleration/tangent/fused 联合图 | v4 非正式规则解释图 | 每个图件 manifest 均复制核心输出哈希、行数、实现来源和 v1/v2 双协议哈希；PDF 可本地重建但不纳入本次 Git 快照 |
| `warning_draft/stable_segment_candidates.csv` | 保存 8 个测点、拟合截止日及以前历史的两类聚类原始速度初始低速前缀候选、拟合截止日、聚类中心、`V`、`σ`、候选 `V0`、Word 输入状态及输入切片哈希 | 对照性自动选段审计表 | 供复核项目特有的非监督对照程序；其 `candidate_method_role` 明确它不是指定 Word 的 MVIF 初始稳定斜率实现，不含速度等级或预警等级 |
| `warning_draft/stable_segment_candidates_manifest.json` | 保存 fit 截止日期、候选算法状态、Word 输入状态、未评估项目及 fit 预测/截止日前运动学输入切片哈希 | 草案运行清单 | 明确 `draft_candidate_not_formal`、`formal_warning_output=false` 与非 Word-`V0` 对照角色；哈希不随 calibration/test 或 post-fit 运动学记录变化 |
| `warning_draft/delta_v_fit_calibration_diagnostics.csv` | 保存 8 个测点在 fit 截止日前历史与 calibration 精确预测日期中的 `ΔV` 有效数、原始分布摘要和输入切片哈希 | `ΔV` 原始诊断表 | 为后续冻结 `ΔV≈0` 容差提供可复核证据；不含 `delta_v_state`、近零容差或预警等级 |
| `warning_draft/delta_v_fit_calibration_diagnostics_manifest.json` | 保存 fit 截止日、calibration 精确日期选择规则/数量、输入切片哈希、未评估项与草案协议状态 | 草案运行清单 | 明确 `diagnostic_only_no_tolerance_decision` 和 `formal_warning_output=false`；test 行不参与阈值或状态选择 |
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
| `ngboost/confusion_matrix.png` | 展示遗留动态 V0 当日四级状态的混淆矩阵 | 历史/探索性图 | 测试段无橙/红样本；不进入当前四指标融合 |
| `ngboost/warning_metrics.csv` | 保存遗留任务的 accuracy、F1、Brier、各等级支持数和召回率 | 历史/探索性评估表 | 无支持等级应写“不可评价”；不能当作当前五级规则性能 |
| `ngboost/warning_probabilities.csv` | 保存遗留测试段逐日真实等级、预测等级和四级概率 | 历史/探索性逐日表 | 仅供旧任务校准与误差复核；当前 v3 不读取这些概率，也不将其作为融合旁证 |
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
| `thresholds/v0_thresholds.csv` | 保存遗留路径使用的 8 个测点动态 V0、5V0、10V0、公式参数和方法来源 | 历史参数审计表 | 新导出行写入 `warning_path=legacy_exploratory` 与 `formal_warning_output=false`；仅服务旧 V0/onset/NGBoost/SHAP 标签，不是当前正式五级规则的阈值 |
| `sensitivity/v0_sensitivity.csv` | 汇总 15/30/60 日窗口与 0.85/0.90/0.95 截断分位数组合的等级、事件和默认一致率 | 敏感性摘要表 | 说明 V0 结论对预设参数的依赖范围，不用于选优 |
| `sensitivity/v0_parameters.csv` | 保存 9 组配置下每个测点的 V0、5V0、10V0 和估计样本数 | 参数审计表 | 追溯 V0 敏感性结果到测点参数 |
| `sensitivity/tangent_sensitivity.csv` | 汇总 27 组候选窗口、平滑和持续性规则的最终等级、融合原因与一致率 | 敏感性摘要表 | 区分等速候选窗口与工程平滑规则的影响 |
| `sensitivity/tangent_parameters.csv` | 保存 15/30/60 日候选窗口选出的等速段、`v_eq` 和稳定性统计 | 参数审计表 | 供专家对照累计位移曲线复核等速阶段 |
| `warning_onset/onset_events.csv` | 保存连续黄色及以上事件的起止、持续时间和可预测性 | 事件审计表 | 说明独立事件数量 |
| `warning_onset/onset_targets.csv` | 保存逐日 at-risk 状态及未来 1/3/7 日 onset 标签 | 派生标签表 | 后续未来预警模型的目标表 |
| `warning_onset/onset_inventory.csv` | 汇总各窗口正负日期和可预测事件数量 | 摘要表 | 判断是否具备可靠建模和置信区间条件 |
| `warning_fusion/warning_fusion.csv` | 保存历史 V0、切线角、NGBoost 旁证、最终等级和融合原因 | 历史融合表 | 新生成 CSV 写入 `warning_path=legacy_exploratory`、`formal_warning_output=false` 和方法 ID；逐日审计旧规则如何升级，不是本轮正式四指标五级融合输出 |

## 保留原则

- 论文图表和 `docs/results_report.md` 引用的结果快照保留在 Git 中，以便数值可追溯。
- 三个阶段原先各自保存的 `v0_thresholds.csv` 内容完全相同，现合并为 `thresholds/v0_thresholds.csv`。
- 其余 CSV 承担不同任务，不是重复文件。需要清理空间时可以整体删除并按 README 的运行顺序重建，但不要只删除某一张支撑表后继续引用旧结果。
- 如果未来 SHAP、NGBoost 和 onset 使用不同的 V0 参数，必须按分析范围分别命名输出，不能继续覆盖公共阈值表。
- 历史脚本的 figures 输出目录会同时写入 `legacy_warning_manifest.json`，使 PNG 等非表格产物也可追溯为非正式；`models/ngboost.pkl` 配套同目录的 `ngboost_legacy_warning_manifest.json`。已存档 CSV 若早于上述字段或 sidecar，仍作为历史快照保留，其身份以 [`docs/legacy_warning_artifact_inventory.md`](../docs/legacy_warning_artifact_inventory.md) 为准。
