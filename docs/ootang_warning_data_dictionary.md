# 藕塘四指标数据字典（协议草案）

> 基础协议：[`ootang-five-level-rule-v1`](../config/ootang_warning_protocol.v1.draft.json)；v4 加速度扩展：[`ootang_warning_protocol.v2.draft.json`](../config/ootang_warning_protocol.v2.draft.json)
>
> 状态：`draft`；本文件记录已确认的数据契约与未冻结项，**不授权生成正式预警结果**。
>
> 方法依据：[行动计划](advisor_review_action_plan.md)、导师指定[论文](../literature/物理引导的阶跃型水库滑坡变形智能概率预测模型与预警方法研究.docx)第五章，以及[改进切线角原始文献](../literature/一种改进的切线角及对应的滑坡预警判据_许强.pdf)。

> 工程口径（2026-08-11）：本字典仍是 `draft` 数据契约，不授权正式预警。默认入口切换为 `features → convlstm → ootang-operational-v4`；v3 数值快照保留为对照。v4 的导师确认范围是加速度计算方法，阈值相对带宽是用户批准的项目操作假设；二者不写成指定 Word 论文已给出的藕塘阈值。schema 3 管线清单记录阶段输入/输出指纹，Vajont 仍未启动。

## 1. 共用约束

- 所有累计位移、速度、`ΔV`、切线角和区间状态当前都基于 Figshare 发布的物化日建模序列；原始观测锚点、日值生成算法、未来信息使用状态以及 MJ/ATU→GPS/FJ 映射均未解决。2026-07-30 起采用分层门禁：`prototype_run_gate=allowed` 允许内部初跑，`confirmatory_evidence_gate=blocked` 禁止把 fit/calibration 草案升级为正式参数或预警。详见[`数据血缘专家审查`](ootang_data_lineage_expert_review.md)。
- 空间输入来自 `data/station_coords.csv`：`station/disp_col` 必须覆盖 8 点且一一对应，`x_m/y_m/elev_m` 单位均为米并须为有限值。当前 `elev_m` 在 8 点间标准化后，经 `x_m/y_m` 水平 IDW 形成静态高程通道；它不改变逐点四指标公式，也不作为第三个距离维度。
- 范围仅为藕塘 8 个测点；Vajont 不参与任何字段定义、阈值选择或结果生成。
- 五级的唯一顺序为 `green=0`、`blue=1`、`yellow=2`、`orange=3`、`red=4`；它表示总体颜色顺序，不会自动赋予单项指标阈值。
- 所有估计器、容差和融合规则只能在预先声明的 fit/calibration 数据上冻结；test 期只执行，不能反向选择规则。
- 每个指标必须携带可审计状态。`warmup`、`invalid`、`not_applicable` 不能被静默改写为 green，也不能被规则融合忽略。
- 每份 `figures/warning_draft/` 审计 CSV 与 manifest 都记录协议 ID、版本、状态和 `protocol_content_sha256`。该哈希标识生成时的规范化 JSON 内容，不等同于协议已冻结或已产生正式预警结果。
- [`code/warning/draft_evidence.py`](../code/warning/draft_evidence.py) 的 `write_draft_warning_evidence_bundle()` 是当前七份有效藕塘草案诊断的统一重建入口：它只接受 `case=ootang`、`status=draft` 的协议，核验每份组件的协议内容指纹、未决项、来源路径、输出 SHA-256 和 `formal_warning_output=false`，并写入 `figures/warning_draft/ootang_draft_warning_evidence_manifest.json`。它在临时目录完成组件生成和核验、成功后才逐文件原子替换相应快照；若出现 Python 可捕获的写入、核验或提升错误，会恢复旧快照，但不宣称进程被强制终止或断电时的 bundle 级目录事务。排除已退役的 MVIF profile 候选，不调用正式执行器，不将原始速度 KMeans 草案候选写作或用于指定 Word 的正式 `V0`，也不计算单项等级、融合或时间线，更不使用 Vajont。
- 为满足“先跑通藕塘”的实施需求，保留 [`ootang_operational_run.v1.draft.json`](../config/ootang_operational_run.v1.draft.json) 作为双计票对照，并新增 [`ootang_operational_run.v2.draft.json`](../config/ootang_operational_run.v2.draft.json) 与 [`operational_v2_fusion.py`](../code/warning/operational_v2_fusion.py)。两版都仅从 fit 期产生 KMeans 对照、`V0±σ`、`5V0/10V0`、切线角和选段 `ΔV` MAD 参数，在 calibration/test 执行。v2 把速度/切线角合并为一个运动学证据族，`ΔV` 仅标记加速性，单一证据族异常保留为可评估候选，并按 O1=`MJ9/MJ1/MJ3`、O2=`ATU4/ATU5/ATU3`、O3=`ATU2/ATU1` 做跨区汇总；该空间拓扑只参考 Wang 等（2025，DOI `10.1029/2025JH000592`）PDF 第 7 页图 4(a,d) 和 5.2 节，配置锁定已审查源文件指纹，**按当前代码重建的** manifest 记录本地副本核验状态；已有 v2 历史快照可能尚未包含该字段。该来源不扩展成跨区支撑数或颜色规则的论文结论。blue 仅在它是最高候选时可见；未获 yellow--red 跨区确认的更高候选不得降为 blue。配置均锁定基础协议 `1.3-draft` 的内容指纹与完整七项未决项，所有 CSV/manifest 输出 `operational_draft_not_formal`、`formal_warning_output=false`、`vajont_used=false`。这不修改本字典的正式字段定义，也不将 KMeans 对照、严格 MVIF 失败或任何运行颜色升级为 Word 论文的正式方法或预警结论；详见 [`ootang_operational_run.md`](ootang_operational_run.md)。
- 2026-08-01 新增独立 [`ootang_operational_run.v3.draft.json`](../config/ootang_operational_run.v3.draft.json) 与 [`operational_v3_fusion.py`](../code/warning/operational_v3_fusion.py)。v3 完全复用 v2 的逐点指标、阈值、测点证据族和分区，只把滑坡体输出拆为 `site_confirmed_level` 与 `local_max_candidate_level`：任何 site 色先要求至少 3 点及三个分区覆盖，blue 也要求 2 点/2 区，未确认 yellow--red 不降级，孤立/单区 blue 记为 site green + `localized_blue_attention`。这是非正式运行字段，不冻结正式 `F_site`，也不改变数据与确认性证据门禁。
- 2026-08-11 新增 [`ootang_operational_run.v4.draft.json`](../config/ootang_operational_run.v4.draft.json) 与 [`operational_v4_fusion.py`](../code/warning/operational_v4_fusion.py)。v4 逐测点计算 `v_i=(U_i-U_{i-1})/Δt_i`、`a_i=(v_i-v_{i-1})/Δt_i`，单位分别为 `mm/day`、`mm/day²`，并在 fit-only 候选稳定段上计算 `A`、`sigma_a`、`A0`。加速度是独立五级 evidence family；速度与切线角仍合并为一个运动学 family；原始 `delta_v` 仅作审计字段。v4 同时锁定 v1 基础协议和 v2 扩展协议的 SHA-256，核心与三类图件 manifest 还复制实现源码指纹、输出行数/哈希和双协议声明；输出目录为 `figures/warning_operational_draft_v4/`，不覆盖 v3 数值快照。导师确认的是计算方法，`A0` 相对带宽为用户批准假设；加速度阈值尚未现场独立验证。
- 当前的历史 `warning_fusion.py`、旧 30 日位移增量、旧四级/主副指标路径均只是溯源材料，不是本字典所定义的正式路径。
- 正式执行器未来只能通过 [`formal_warning.py`](../code/warning/formal_warning.py) 的 `run_formal_warning()` 进入；它先调用 `require_frozen_protocol()`，且在正式四指标执行器尚未实现前会明确拒绝，而不会接受旧融合函数。当前 `main.py` 运行清单和历史预警阶段均固定标为非正式；旧融合、阈值及可单独运行的历史脚本的新表格携带 `warning_path=legacy_exploratory` 与 `formal_warning_output=false`，输出目录另有 `legacy_warning_manifest.json`，`models/ngboost.pkl` 则配套同目录的 `ngboost_legacy_warning_manifest.json`。完整映射见 [`legacy_warning_artifact_inventory.md`](legacy_warning_artifact_inventory.md)。
- 用户于 2026-07-21 确认：指定 Word 论文优先于藕塘毕业论文；后者的 `30` 日、四级 `V0` 路径只可复核历史产物，不能替代本字典的逐点日速度、指定 Word 式（5-3）或五级规则。
- 指定 Word 对阶跃型滑坡的 `V0` 输入是 MVIF 趋势项位移的初始稳定斜率。用户已授权以 `s(t)=A ln((t_f-Bt)/(t_f-t))+C` 协调其第 3 章的 `s0/C` 符号不一致，其中 `C` 为待估截距；原始模型文献仍未规定该斜率的自动取值时刻或窗口。现有“发布序列未作项目内平滑的相邻差分速度”KMeans 产物仅标为 `project_specific_comparator_not_specified_word_v0_implementation`，可供对照审计，不能被写作已实现的 Word `V0` 路径；这里过去使用的“原始速度”不表示原始 GNSS 测量速度。
- 用户已确认：MVIF 的有限 `t_f` 若在 fit 期多起点拟合中不可辨识，必须明确 `failed`，不能人为给 horizon 或由其生成正式 `V`、`σ`、`V0`、速度/切线角等级或预警。`mvif_fit_candidates.*` 只审计这条严格基线；当前藕塘 8 个 fit 记录均因 `tf_multistart_unstable` 失败，故它们不含任何下游阈值量。用户在审阅该结果后确认：这些失败不授权事后放宽多起点、Jacobian 秩或 `t_f` 一致性条件，也不将原始速度 KMeans 对照升级为指定 Word 路径。此前王/安（2023）`5 d`、`L` 匀速段改写路线及 `mvif_initial_slope_candidates.*` 已退役为历史记录，当前协议拒绝继续写出它。新的 `bai_perron_mvif_initial_slope_candidates.*` 只有在严格 MVIF 已接受时，才对该拟合趋势做 Bai--Perron 分段 OLS/BIC 审计；仅当首段为正且紧随段增速时才输出 `candidate_v_mm_per_day`。整段、非增速首断点、数据不足或严格 MVIF 失败均不产生候选。该路线不计算 `σ` 或 `V0`，不输出速度/切线角等级或预警，且未估计断点不确定性；这些均继续受 `stable_segment_selection` 等未冻结项约束。

## 2. 四项指标

| 指标与正式字段 | 值的定义及单位 | 时间窗口 / 可用数据 | 缺失与暖启动 | 阈值来源与当前状态 |
| --- | --- | --- | --- | --- |
| 区间偏离状态：`interval_level` | 已发布预测的 `P10/P50/P90`（mm）与随后物化到表中的 `U_t`（mm）。采用 `μ_t=P50_t`、`σ_t=(P90_t-P10_t)/(2×1.28155)`、`z_t=(U_t-μ_t)/σ_t` 的项目特有正态近似。它表示模型与物化目标值之间的观测后偏离，不是原始 GNSS 测量不确定性。 | calibration 质量诊断固定只读 `split=calibration`；逐时刻状态识别只在目标 `U_t` 已可见后进行。fit 行是拟合诊断，固定为 `not_applicable`；calibration/test 的已发布预测可映射，不能称为 `t+h` 确认性前瞻预警。 | 底层接口只在调用者显式提供 `warmup/invalid/not_applicable` 时保留该输入状态；当前 `forecast_predictions.csv` 没有区间上游状态字段，故审计产物仅按 split 派生 fit=`not_applicable`，且非有限值或 `P10≤P50≤P90` / `P90>P10` 不成立时为 `invalid`。有效的已发布预测直接输出五级，并写入 `interval_mapping_basis=specified_thesis_figure_5_1_normal_regions`。 | 指定论文图 5-1 给出 `μ`、`μ+σ`、`μ+2σ`、`μ+3σ` 的五级相对区域；其本身不提供 `P10/P50/P90→μ/σ` 公式。覆盖率、对称性和尾部诊断保留为审计，不虚构通过阈值，也不阻止该论文参考映射；整个协议仍为 draft，不能输出正式综合预警。 |
| 逐点速度：`velocity` / `velocity_level` | `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`，单位 `mm/day`。当前值是物化序列导数，可能携带自然月多项式结构；`velocity_level` 是未来的五级单项等级，不等同于最终测点等级。 | 当前值使用相邻两个有效物化日历值的实际 `Δt`；每个测点独立。Word 路径要求先从 MVIF 趋势项位移自动/人工确定初始稳定斜率。当前有两条隔离审计路线：fit 期发布序列相邻差分速度 KMeans `V0` 仍只是对照；新的 MVIF 候选先要求严格有限 `t_f` 拟合通过，再在其趋势上以 Bai--Perron 分段 OLS/BIC 识别首个正斜率、后续增速段，只输出 `candidate_v_mm_per_day`。它不接入 `velocity_tangent_fit_calibration_diagnostics.csv`，避免与 KMeans 对照混淆。 | 首个速度为 `warmup`；缺失位移、无效日期或非正 `Δt` 产生明确无效状态，不插值。Bai--Perron 草案不需要逐日重采样；严格 MVIF 拟合失败、无结构断点或首断点不增速时均明确失败。 | 指定论文式（5-3）为 `V0=max(1.5V,V+2σ)`，其中 `V` 是选定初始位移段的平均速率。表 5-4 的橙色列符号已由同章图 5-4（速率纵轴、`V0/5V0/10V0` 阈值线）核对为 `5V0≤V<10V0`；Bai--Perron 首段规则、断点不确定性、`σ` 的操作定义与 `V≈V0` 的 blue 容差仍未冻结：`stable_segment_selection`、`v0_blue_tolerance`。两个审计表都不输出速度等级或容差。 |
| 变形速率增量：`delta_v` / `delta_v_state` | `ΔV_i=v_i-v_{i-1}`，是速度增量而非加速度，单位仍为 `mm/day`。它是物化序列的二阶差分性质派生量，状态仅为 `negative`、`near_zero`、`positive`，不是单独虚构的五级阈值。 | 需要连续两个有效速度，涉及 `i-2,i-1,i` 三个物化日历值；近零容差只能在预先声明的 fit/calibration 阶段审计。`figures/warning_draft/delta_v_fit_calibration_diagnostics.csv` 只固化数值摘要：fit 取截止日前历史，calibration 只取精确预测日期，不按起止日期包络扩展。 | 前两行是 `warmup`；当前速度无效则为 `velocity_invalid`，前一速度无效则为 `previous_velocity_invalid`。 | 指定论文只将 `ΔV` 称为辅助判别，正文 `[92]` 无法从该 Word 文件的参考文献表追溯；其融合公式的解释还只列区间、切线角和速率三类输出，未能恢复 `ΔV` 的精确特征角色。可采用负/近零/正的过程语义，但 `delta_v_near_zero_tolerance` 与其参与 `F` 的规则未冻结；数据门禁通过前也不冻结为正式量。 |
| 改进切线角：`tangent_angle` / `tangent_angle_level` | 原始方法将累计位移坐标变换为时间量纲后计算 `α_i=(180/π)arctan((T_i-T_{i-1})/(t_i-t_{i-1}))`；在当前发布表的等间隔日历网格中，速率比形式为 `α_i=(180/π)arctan(v_i/V0)`，输出单位为 degree。它仍是物化序列导数的变换。 | 原始文献要求先识别等速变形阶段并计算其平均速率 `V0`。本项目的自动稳定段仅是 fit-only 草案候选；当前遗留的 3 日因果平滑和持续性规则不可自动升格为正式窗口。`velocity_tangent_fit_calibration_diagnostics.csv` 只保存该原始角度及其相对 45° 的描述统计。 | 原始文献建议不等间隔观测先等间隔化。藕塘发布表为逐日日历网格，但不能据此推断原始采样频率；出现原始缺测/非等间隔时的重采样、无效标记或其他处置尚未恢复。 | 指定论文表 5-2 和许强等（2009）给出 `α<45°`、`α≈45°`、`45°<α<80°`、`80°≤α<85°`、`α≥85°` 对应五色。`α≈45°` 没有数值容差或边界归属，因此 `tangent_blue_tolerance`、`nonregular_tangent_handling`、稳定段选择和数据血缘仍阻止正式五级；该诊断表不输出切线角等级或容差。 |

### 2.1 v4 加速度扩展

v4 新增 `acceleration`、`acceleration_level`、`acceleration_indicator_status` 和 `acceleration_reason`。其定义与状态如下：

| 字段 | 定义 | fit 基线与五级映射 | 语义边界 |
| --- | --- | --- | --- |
| `acceleration` | `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，单位 `mm/day²`，使用当前速度对应的真实 `Δt_i` | `A=mean(a)`、`sigma_a=std(a,ddof=1)`、`A0=max(1.5A,A+2sigma_a)`；green `<A0-sigma_a`，blue `A0-sigma_a…A0+sigma_a`，yellow `A0+sigma_a…5A0`，orange `5A0…10A0`，red `≥10A0`，边界归属以 v2 JSON 为准 | 计算方法来自导师确认；相对带宽由用户授权沿用速度规则，不是 Word 论文已经给出的藕塘加速度阈值。`A0` 非有限/非正时 fail-closed。 |

三点暖启动规则为：首个速度行和前两个加速度行不分级；缺失位移、无效日期或非正 `Δt` 保留明确状态。负加速度自然落入 green 的相对范围，但不以 `delta_v` 符号替代加速度分级。`delta_v` 仍保留为 `mm/day` 的原始审计字段，v4 不将其作为独立 ordinal vote。

## 3. 输出与融合边界

逐测点的未来可复算表至少应含：

```text
case_id, date, split, station,
interval_level, interval_status, interval_color, interval_mapping_basis,
interval_mu, interval_sigma, interval_z, interval_reason,
velocity, velocity_level, velocity_status,
delta_v, delta_v_state, delta_v_status,
acceleration, acceleration_level, acceleration_indicator_status,
acceleration_reason,
tangent_angle, tangent_angle_level, tangent_status,
station_warning_level, station_warning_color,
fusion_rule_version, fusion_reason, validity_flag
```

其中 `station_warning_level` 只能来自已冻结的四指标函数 `F`；滑坡体层还需要独立冻结 `F_site`。当前 `rule_fusion.py` 的“两项佐证”仅为项目特有草案候选，且在任一输入非 `valid` 时保留相应无效状态。它不能解除协议 `draft` 状态，也不能产生监督模型概率、F1、Brier 或混淆矩阵结论。

在 `F_site` 冻结前，[`site_fusion.py`](../code/warning/site_fusion.py) 只提供诊断汇总：仅当测点结果同时满足 `status=valid` 与已有等级时，才统计有效点、异常点、各级数量和有效点中的最高等级；`uncorroborated` 单列，绝不按 green 或 elevated 处理。该汇总固定输出 `integrated_level=null`、`integrated_color=null` 与 `formal_warning_output=false`，所以其中的 `max_station_level` 不是滑坡体级预警，不能写入阶段 5 的正式综合预警表。

v2 实施版的 `station_assessment_status=valid` 只表示四项输入可评估；它与 v1 的“两项佐证有效”语义不同。`candidate_level` 保留测点的最高偏离候选，`kinematic_level` 是速度/切线角这一单一证据族的等级，`acceleration_status` 仅区分 `accelerating/not_accelerating`，不改候选颜色。为兼容遗留读取而保留的 `final_level/final_color` 是 `candidate_level/candidate_color` 的别名，不能解释为测点已确认或最终综合结果。滑坡体表的 `assessable_station_count`、`assessable_blocks` 和 `coverage_complete` 只描述数据覆盖；`cross_block_confirmation_minimum_level/color` 记录 yellow 起的跨区确认门槛；`site_candidate_level` 与 `candidate_not_site_confirmed` 保留未获跨区确认的异常。它们均是 v2 的非正式运行审计字段，不是冻结后的 `F` 或 `F_site`。

v3 滑坡体表以 `site_confirmed_level/color`、`local_max_candidate_level/color` 和 `local_attention_status` 为规范字段；`site_level/color`、`site_candidate_level/color` 只是兼容别名。`contributing_stations/blocks` 是达到整体确认等级的跨区支撑点，`local_max_candidate_stations/blocks` 是恰好达到局部最高等级的点，两组不能混用。覆盖不足时整体等级为空但局部候选仍保留。v3 的 green 只表示当前项目规则下覆盖完整且没有跨区 blue+ 或未确认 yellow+，不是已验证安全状态。

## 4. 未冻结项清单

下列决策必须留在版本化协议中，不能由本字典、历史代码、指定论文的其他案例数值或 test 期结果补写：

1. 原始观测锚点、日值生成算法、未来锚点使用状态以及 MJ/ATU 与 GPS/FJ/坐标映射；
2. 每测点 Bai--Perron 初始段候选的正式接受（包括断点不确定性、最少段长/首段增速判断及 `σ` 的约定）和速度五级的 blue/橙色边界；
3. `ΔV≈0` 容差以及它在测点级 `F` 中的参与方式；
4. 切线角 `α≈45°` 的 blue 容差与不规则采样处置；
5. 测点级 `F`、滑坡体级 `F_site`、平局、冲突、缺失和暖启动规则。

论文参考的区间单项状态已可复算，但在其余项冻结并完成四指标与滑坡体融合前，任何单项颜色仍不是藕塘的正式预警结果。
