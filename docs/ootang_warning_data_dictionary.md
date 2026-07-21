# 藕塘四指标数据字典（协议草案）

> 协议：[`ootang-five-level-rule-v1`](../config/ootang_warning_protocol.v1.draft.json)
>
> 状态：`draft`；本文件记录已确认的数据契约与未冻结项，**不授权生成正式预警结果**。
>
> 方法依据：[行动计划](advisor_review_action_plan.md)、导师指定[论文](../literature/物理引导的阶跃型水库滑坡变形智能概率预测模型与预警方法研究.docx)第五章，以及[改进切线角原始文献](../literature/一种改进的切线角及对应的滑坡预警判据_许强.pdf)。

## 1. 共用约束

- 范围仅为藕塘 8 个测点；Vajont 不参与任何字段定义、阈值选择或结果生成。
- 五级的唯一顺序为 `green=0`、`blue=1`、`yellow=2`、`orange=3`、`red=4`；它表示总体颜色顺序，不会自动赋予单项指标阈值。
- 所有估计器、容差和融合规则只能在预先声明的 fit/calibration 数据上冻结；test 期只执行，不能反向选择规则。
- 每个指标必须携带可审计状态。`warmup`、`invalid`、`not_applicable` 不能被静默改写为 green，也不能被规则融合忽略。
- 每份 `figures/warning_draft/` 审计 CSV 与 manifest 都记录协议 ID、版本、状态和 `protocol_content_sha256`。该哈希标识生成时的规范化 JSON 内容，不等同于协议已冻结或已产生正式预警结果。
- 当前的历史 `warning_fusion.py`、旧 30 日位移增量、旧四级/主副指标路径均只是溯源材料，不是本字典所定义的正式路径。
- 用户于 2026-07-21 确认：指定 Word 论文优先于藕塘毕业论文；后者的 `30` 日、四级 `V0` 路径只可复核历史产物，不能替代本字典的逐点日速度、指定 Word 式（5-3）或五级规则。
- 指定 Word 对阶跃型滑坡的 `V0` 输入是 MVIF 趋势项位移的初始稳定斜率。用户已授权以 `s(t)=A ln((t_f-Bt)/(t_f-t))+C` 协调其第 3 章的 `s0/C` 符号不一致，其中 `C` 为待估截距；原始模型文献仍未规定该斜率的自动取值时刻或窗口。现有原始逐点速度 KMeans 产物仅标为 `project_specific_comparator_not_specified_word_v0_implementation`，可供对照审计，不能被写作已实现的 Word `V0` 路径。
- 用户已确认：MVIF 的有限 `t_f` 若在 fit 期多起点拟合中不可辨识，必须明确 `failed`，不能人为给 horizon 或由其生成 `V`、`σ`、`V0`、速度/切线角等级或预警。`mvif_fit_candidates.*` 只审计该数值门禁；当前藕塘 8 个 fit 记录均因 `tf_multistart_unstable` 失败，故它们不含任何下游阈值量。该门禁不替代尚未冻结的“初始稳定斜率”自动取值规则。

## 2. 四项指标

| 指标与正式字段 | 值的定义及单位 | 时间窗口 / 可用数据 | 缺失与暖启动 | 阈值来源与当前状态 |
| --- | --- | --- | --- | --- |
| 区间偏离状态：`interval_level` | 已发布预测的 `P10/P50/P90`（mm）与随后观测到的 `U_t`（mm）。采用 `μ_t=P50_t`、`σ_t=(P90_t-P10_t)/(2×1.28155)`、`z_t=(U_t-μ_t)/σ_t` 的项目特有正态近似。 | calibration 质量诊断固定只读 `split=calibration`；逐时刻状态识别只在目标 `U_t` 已观测后进行。fit 行是拟合诊断，固定为 `not_applicable`；calibration/test 的已发布预测可映射，不能称为 `t+h` 前瞻预警。 | 底层接口只在调用者显式提供 `warmup/invalid/not_applicable` 时保留该输入状态；当前 `forecast_predictions.csv` 没有区间上游状态字段，故审计产物仅按 split 派生 fit=`not_applicable`，且非有限值或 `P10≤P50≤P90` / `P90>P10` 不成立时为 `invalid`。有效的已发布预测直接输出五级，并写入 `interval_mapping_basis=specified_thesis_figure_5_1_normal_regions`。 | 指定论文图 5-1 给出 `μ`、`μ+σ`、`μ+2σ`、`μ+3σ` 的五级相对区域；其本身不提供 `P10/P50/P90→μ/σ` 公式。覆盖率、对称性和尾部诊断保留为审计，不虚构通过阈值，也不阻止该论文参考映射；整个协议仍为 draft，不能输出正式综合预警。 |
| 逐点速度：`velocity` / `velocity_level` | `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`，单位 `mm/day`。`velocity_level` 是未来的五级单项等级，不等同于最终测点等级。 | 当前值使用相邻两次有效观测的实际 `Δt`；每个测点独立。Word 路径要求先从 MVIF 趋势项位移自动/人工确定初始稳定斜率；该自动规则尚未冻结。现有仅由 fit 期有效原始速度生成的 KMeans `V0` 为对照性候选。`figures/warning_draft/velocity_tangent_fit_calibration_diagnostics.csv` 因此只保存其对照性的 `v`、`v/V0` 与 `v-V0` 原始描述统计。 | 首个速度为 `warmup`；缺失位移、无效日期或非正 `Δt` 产生明确无效状态，不插值。 | 指定论文式（5-3）为 `V0=max(1.5V,V+2σ)`，其中 `V` 是选定初始位移段的平均速率。表 5-4 的橙色列符号已由同章图 5-4（速率纵轴、`V0/5V0/10V0` 阈值线）核对为 `5V0≤V<10V0`；MVIF 初始稳定段与 `V≈V0` 的 blue 容差仍未冻结：`stable_segment_selection`、`v0_blue_tolerance`。该诊断表不输出速度等级或容差。 |
| 变形速率增量：`delta_v` / `delta_v_state` | `ΔV_i=v_i-v_{i-1}`，是速度增量而非加速度，单位仍为 `mm/day`。状态仅为 `negative`、`near_zero`、`positive`，不是单独虚构的五级阈值。 | 需要连续两个有效速度，涉及 `i-2,i-1,i` 三个观测位置；近零容差只能在预先声明的 fit/calibration 阶段冻结。`figures/warning_draft/delta_v_fit_calibration_diagnostics.csv` 只固化原始摘要：fit 取截止日前历史，calibration 只取精确预测日期，不按起止日期包络扩展。 | 前两行是 `warmup`；当前速度无效则为 `velocity_invalid`，前一速度无效则为 `previous_velocity_invalid`。 | 指定论文只将 `ΔV` 作为辅助判别，且其正文 `[92]` 无法从该 Word 文件的参考文献表追溯；可采用负/近零/正的过程语义，但 `delta_v_near_zero_tolerance` 与其参与 `F` 的规则未冻结。 |
| 改进切线角：`tangent_angle` / `tangent_angle_level` | 原始方法将累计位移坐标变换为时间量纲后计算 `α_i=(180/π)arctan((T_i-T_{i-1})/(t_i-t_{i-1}))`；在当前等间隔日数据中，速率比形式为 `α_i=(180/π)arctan(v_i/V0)`，输出单位为 degree。 | 原始文献要求先识别等速变形阶段并计算其平均速率 `V0`。本项目的自动稳定段仅是 fit-only 草案候选；当前遗留的 3 日因果平滑和持续性规则不可自动升格为正式窗口。`velocity_tangent_fit_calibration_diagnostics.csv` 只保存该原始角度及其相对 45° 的描述统计。 | 原始文献建议不等间隔观测先等间隔化。藕塘当前为逐日数据；出现缺测/非等间隔时的重采样、无效标记或其他处置尚未冻结。 | 指定论文表 5-2 和许强等（2009）给出 `α<45°`、`α≈45°`、`45°<α<80°`、`80°≤α<85°`、`α≥85°` 对应五色。`α≈45°` 没有数值容差或边界归属，因此 `tangent_blue_tolerance`、`nonregular_tangent_handling` 以及稳定段选择仍阻止正式五级；该诊断表不输出切线角等级或容差。 |

## 3. 输出与融合边界

逐测点的未来可复算表至少应含：

```text
case_id, date, split, station,
interval_level, interval_status, interval_color, interval_mapping_basis,
interval_mu, interval_sigma, interval_z, interval_reason,
velocity, velocity_level, velocity_status,
delta_v, delta_v_state, delta_v_status,
tangent_angle, tangent_angle_level, tangent_status,
station_warning_level, station_warning_color,
fusion_rule_version, fusion_reason, validity_flag
```

其中 `station_warning_level` 只能来自已冻结的四指标函数 `F`；滑坡体层还需要独立冻结 `F_site`。当前 `rule_fusion.py` 的“两项佐证”仅为项目特有草案候选，且在任一输入非 `valid` 时保留相应无效状态。它不能解除协议 `draft` 状态，也不能产生监督模型概率、F1、Brier 或混淆矩阵结论。

在 `F_site` 冻结前，[`site_fusion.py`](../code/warning/site_fusion.py) 只提供诊断汇总：仅当测点结果同时满足 `status=valid` 与已有等级时，才统计有效点、异常点、各级数量和有效点中的最高等级；`uncorroborated` 单列，绝不按 green 或 elevated 处理。该汇总固定输出 `integrated_level=null`、`integrated_color=null` 与 `formal_warning_output=false`，所以其中的 `max_station_level` 不是滑坡体级预警，不能写入阶段 5 的正式综合预警表。

## 4. 未冻结项清单

下列决策必须留在版本化协议中，不能由本字典、历史代码、指定论文的其他案例数值或 test 期结果补写：

1. 每测点稳定段选择和速度五级的 blue/橙色边界；
2. `ΔV≈0` 容差以及它在测点级 `F` 中的参与方式；
3. 切线角 `α≈45°` 的 blue 容差与不规则采样处置；
4. 测点级 `F`、滑坡体级 `F_site`、平局、冲突、缺失和暖启动规则。

论文参考的区间单项状态已可复算，但在其余项冻结并完成四指标与滑坡体融合前，任何单项颜色仍不是藕塘的正式预警结果。
