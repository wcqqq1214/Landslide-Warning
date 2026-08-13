# 藕塘 v4 预警原型数据字典

本字典只覆盖 `figures/warning_operational_draft_v4/` 的当前字段。全部记录都是非正式原型：`formal_warning_output=false`、`vajont_used=false`。

## 核心表

| 文件 | 行数 | 一行含义 |
| --- | ---: | --- |
| `ootang_operational_thresholds.csv` | 8 | 一个测点的 fit-only 速度、切线角和加速度运行基线 |
| `ootang_operational_station_timeline.csv` | 4,112 | 一个结果日 × 一个测点的四指标、三族候选和审计状态 |
| `ootang_operational_site_timeline.csv` | 514 | 一个结果日的整体确认和局部最高候选双轴 |
| `ootang_operational_run_manifest.json` | 1 | 协议、源码、输入和输出 SHA-256 血缘 |

## 测点字段

| 字段组 | 关键字段 | 含义 |
| --- | --- | --- |
| 键与边界 | `case,date,split,station,formal_warning_output,vajont_used` | 藕塘、结果日期、ConvLSTM 结果分区、测点及非正式边界 |
| 区间 | `interval_level,interval_color,interval_status,interval_mu,interval_sigma,interval_z` | 已发布预测区间与随后可见 `U_t` 的观测后偏离；不是原始 GNSS 不确定性或前瞻告警 |
| 速度 | `velocity,velocity_level,velocity_color,velocity_indicator_status` | `v_i=(U_i-U_{i-1})/Δt_i` 的逐点速度及当前运行比较器等级 |
| 原始速度增量 | `delta_v,delta_v_state,delta_v_status` | `v_i-v_{i-1}`，单位 `mm/day`；仅审计，不参与 ordinal 投票 |
| 加速度 | `acceleration,acceleration_level,acceleration_color,acceleration_indicator_status,acceleration_reason` | `a_i=(v_i-v_{i-1})/Δt_i`，单位 `mm/day²`；前两行 warmup；以站点 `A0` 的五级结构分级 |
| 切线角 | `tangent_angle_degree,tangent_angle_level,tangent_angle_color,tangent_angle_indicator_status` | 运行基线转换的改进切线角；不规则时明确不可用 |
| 三族候选 | `kinematic_level,kinematic_color,evidence_families,evidence_family_count,candidate_level,candidate_color` | 速度/切线角属于一个运动学 family，与区间、加速度共同取最大候选。规范族名为 `interval`、`kinematic_velocity_tangent`、`acceleration` |
| 审计 | `fusion_status,fusion_reason,input_statuses,transition_status,evidence_consistency_status` | 输入是否可评估、为什么得到当前候选；不把缺失/暖启动伪装为 green |

`A0=max(1.5A,A+2σ_a)`，其中 `A` 和 `σ_a` 由 fit-only 加速度基线计算。green `<A0-σ_a`，blue 为 `A0±σ_a`，yellow 至 `5A0`，orange 至 `10A0`，red 为 `≥10A0`；精确边界包含关系以 v4 profile 为准。

## 滑坡体字段

| 字段组 | 关键字段 | 含义 |
| --- | --- | --- |
| 整体轴 | `site_confirmed_level,site_confirmed_color,site_fusion_status,site_fusion_reason` | 只有覆盖 O1/O2/O3 且达到两点、两块支撑时才给出整体候选 |
| 局部轴 | `local_max_candidate_level,local_max_candidate_color,local_attention_status` | 任一可评估测点当天的最高候选；即使整体轴无色仍保留 |
| 空间支撑 | `assessable_station_count,assessable_blocks,coverage_complete,contributing_stations,contributing_blocks` | 可评估覆盖和用于整体确认的测点/空间块 |
| 局部来源 | `local_max_candidate_stations,local_max_candidate_blocks` | 达到局部最高候选的测点/空间块 |

`site_level/site_color` 和 `site_candidate_level/site_candidate_color` 只为向后兼容保留。论文和报告应使用显式的 `site_confirmed_*` 与 `local_max_candidate_*` 字段。

## 解释限制

1. green 是当前规则候选，不能写成现场安全状态；
2. 整体无色不等于“无异常”或“数据缺失”，须同时看 local 轴和原因字段；
3. 当前稳定段、速度 V0、切线角容差以及正式 `F/F_site` 均尚未取得独立确认；
4. 本字典不定义正式 NGBoost 五级训练标签。正式模型只能使用独立、可核验的结局标签，而不是本表的同一规则输出。
