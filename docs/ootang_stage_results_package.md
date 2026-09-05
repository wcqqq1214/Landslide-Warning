# 藕塘滑坡阶段性结果与后续决策包

> 更新日期：2026-08-31
> 用途：汇总导师要求下已跑通的藕塘工程案例，形成后续撰写、审查和更换数据集时的统一入口
> 证据等级：**工程原型／内部可复算，不是确认性预测或正式预警**
> 历史方法依据：当时的导师意见（见 [`历史快照`](advisor_review_action_plan.md)）和指定 Word 论文；用户本人的藕塘毕业论文仅作角色分工参考
> Vajont：未参与本结果包，未用于其阈值选择或结果生成
>
> 阶段状态更新（2026-09-05）：用户已确认阶段报告提交，原导师要求已退役。本文件保留
> 2026-08-31 阶段的方法、结果和决策记录；其中的路线限制、待办和暂停安排不再作为当前指令。
> 后续工作以当前用户任务为准，既有负结果和证据局限保持不变。

> 当前口径（2026-08-31）：v4 是透明、非正式规则基线；H=7 ECDF 五级自动标签与固定
> NGBoost 五分类已完成，全部 `formal_warning_output=false`。Vajont 未参与本结果包。

> **统一对象与时间口径**：原始发布表为 2016-07-01—2020-06-30 的 1,461 个日历日；
> “NGBoost 全时刻”专指 2018-02-21—2020-06-30 的 861 个模型可用 OOF 日期，对应
> 6,888 条测点诊断记录（861 日 × 8 点）；“v4 全时刻”专指 2019-02-03—2020-06-30 的
> 514 个透明规则基线日期。三种口径不得互换。P10–P90 是 80% **预测区间**，其经验覆盖率
> 记为 PICP；它由观测值是否落入区间计算，不由 SHAP 决定。

> **导师展示包已物化（2026-08-31）**：显式五阶段流水线已完成，运行清单
> [`ootang_advisor_demo_run.json`](../figures/pipeline/ootang_advisor_demo_run.json) 状态为
> `completed`、总耗时 `35.660 s`，五阶段产物合同全部通过。该运行复用现有 ConvLSTM 预测，
> 未重训 ConvLSTM、未运行 memory/residual、未运行 Vajont。展示入口为
> [`advisor_summary.md`](../figures/advisor_ootang_v1/advisor_summary.md)，包级血缘见
> [`manifest.json`](../figures/advisor_ootang_v1/manifest.json)。

## 1. 阶段结论

藕塘案例已经达到导师要求的“先跑通”目标。8 个测点均进入高程感知 ConvLSTM 和 v4
透明基线；另以 `8 × [interval_z, velocity, strict_acceleration, tangent_angle]` 的 32 维
site 输入完成 H=7 ECDF 自动五级标签、固定 NGBoost 分类、八点/全时刻信号及分类 SHAP。
但固定分类器、lag-memory 和 residual 三种方案均未胜严格 persistence，因此只构成可复算的
探索性负结果，不证明预警有效。

数据限制影响的是**结论强度**，不是“能否运行”。现有输入是公开包中的物化日序列，原始 GNSS 锚点及日值生成链不可取得；因此本案例可用于验证代码链、输出结构和规则可审计性，但不能证明模型在独立原始 GNSS 上具有确认性预测能力，也不能把当前阈值和颜色写成可直接部署的工程预警标准。

## 2. 本阶段完成范围

| 模块 | 已完成内容 | 当前边界 | 主要证据 |
| --- | --- | --- | --- |
| 数据与空间输入 | 8 个测点完成位移列、平面坐标和高程映射；`elev_m` 作为 7 通道模型中的一个静态输入通道 | 高程是地形先验，不是新增位移观测或力学约束 | [`station_coords.csv`](../data/station_coords.csv)、[`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json) |
| 位移概率预测 | 7 日回看、1 日预测；输出 P10/P50/P90 和逐点误差；已完成 fixed-120 三个滚动折 × 五个预设种子及全部逐日预测 | 属于物化日序列内部探索性诊断；早停与容量敏感性尚未重跑 | [`7 通道 fixed-120 审查`](ootang_convlstm_elevation_fixed120_review.md)、[`five-seed manifest`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json) |
| 模型解释分工 | ConvLSTM 负责 P10/P50/P90 预测区间与 PICP 等区间评价；SHAP 解释五分类 site NGBoost 的期望顺序等级 | 沿用用户毕业论文中“预测模型与其解释模块分工”的思路；当前是 NGBoost SHAP，不是 ConvLSTM-SHAP。SHAP 只能识别模型支持的候选贡献因素，不能单独证明物理因果主控 | [`NGBoost SHAP 协议`](ngboost_shap_protocol.md)、[`SHAP values`](../figures/ngboost_auto_state_classifier_v1/site_shap_values.csv)、[`classifier manifest`](../figures/ngboost_auto_state_classifier_v1/manifest.json) |
| H=7 自动标签 | fold-1 ECDF 固定五级边界，site 按 O1/O2/O3 两层等权综合 | 自动多点代理状态，不是现场灾害真值 | [`ECDF manifest`](../figures/ngboost_auto_state_ecdf_v2/manifest.json)、[`site labels`](../figures/ngboost_auto_state_ecdf_v2/site_auto_labels.csv) |
| 固定 NGBoost 五分类 | 32 维四指标 site 主模型；输出 861 个 site 日期 × 4 个估计器共 3,444 行，以及 6,888 条八点全时刻诊断和分类 SHAP | 未胜 persistence；SHAP 仅为模型依赖、不是 ConvLSTM 内部或因果解释 | [`classifier manifest`](../figures/ngboost_auto_state_classifier_v1/manifest.json)、[`metrics`](../figures/ngboost_auto_state_classifier_v1/metrics.csv)、[`全时刻图`](../figures/ngboost_auto_state_classifier_v1/warning_timeline.pdf)、[`SHAP 图`](../figures/ngboost_auto_state_classifier_v1/site_shap_summary.pdf) |
| v4 三族逐点判断 | 区间、运动学（速度/切线角）和严格逐点加速度进入全部 4,112 条测点—时刻记录；raw `ΔV` 保留审计 | V0 是项目特有比较器；导师确认加速度沿用指定 Word 速度 `V0` 的相对结构，v4 以加速度自身 A0 量纲一致转置，不伪称 Word 有严格加速度表 | [`ootang_operational_station_timeline.csv`](../figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv)、[`ootang_operational_thresholds.csv`](../figures/warning_operational_draft_v4/ootang_operational_thresholds.csv) |
| 多测点空间融合 | v4 使用双轴空间融合，分别输出滑坡体确认等级和局部最高候选；全局有效点与 O1/O2/O3 覆盖门禁适用于所有颜色 | 空间支撑数及融合规则是项目原型规则，不是指定 Word 的逻辑回归复现 | [`ootang_operational_site_timeline.csv`](../figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv)、[`v4 配置`](../config/ootang_operational_run.v4.draft.json) |
| 代表日审计 | v4 代表日显示 interval/velocity/acceleration/tangent/fused 证据、双轴等级和跨区支撑 | 属于观测后规则说明，不用于评价提前量或预警性能 | [`代表日诊断图`](../figures/warning_operational_draft_v4/ootang_v4_typical_days.svg)、[`图件清单`](../figures/warning_operational_draft_v4/ootang_v4_typical_days_manifest.json) |
| v4 基线全时刻等级展示 | 覆盖 v4 的 514 日 × 8 点候选等级，并同时显示滑坡体整体确认与局部最高双轴 | 这是透明规则基线，不是 NGBoost 主概率分类输出；400 个 `NC` 是空间佐证不足而非缺测 | [`完整时间线`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg)、[`图件清单`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline_manifest.json) |
| 位移—指标—等级联合展示 | 4×2 小多图逐点对齐 514 日累计位移、区间/速度/加速度/切线角和最终候选等级 | raw `ΔV` 仍只作审计；属于观测后联合诊断，不证明提前量 | [`联合诊断图`](../figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg)、[`图件清单`](../figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic_manifest.json) |

## 3. 数据条件与证据门禁

### 3.1 已具备的数据

- 2016-07-01 至 2020-06-30 的 1,461 行连续日历序列；包含 8 条累计位移、地下水位及环境变量。
- 8 个测点的 `x_m`、`y_m` 和 `elev_m`；高程范围为 190–515 m。
- 当前模型使用位移 IDW 网格、高程静态 IDW 网格、库水位、库水位变化率和 7/15/30 日累计降雨，共 7 个通道。

### 3.2 缺失但不再阻塞原型运行的数据

- 原始 GNSS 解算时刻与原始位移锚点；
- 公开日序列的完整插值、平滑或重采样生成链；
- 可独立验证预警触发是否正确的事件真值与现场宏观变形记录。

因此项目采用分层门禁：

```text
source_recovery_status = unavailable_by_project_constraint
prototype_run_gate = allowed
confirmatory_evidence_gate = blocked
formal_warning_output = false
```

统一入口还会记录逐阶段输入/输出文件的路径、大小和 SHA-256，以及启动时工作树状态。v4 profile 锁定的 Wang 论文 PDF 仅是拓扑来源证据：本地副本存在时必须匹配锁定摘要，缺失时允许原型计算并记录未核验状态，错误副本则拒绝运行；它不构成模型计算输入。

这一状态表示导师要求的藕塘工程初跑已经完成，但不能把结果升级为独立原始 GNSS 上的确认性日预测、正式阈值或工程预警。详细数据血缘见[`藕塘数据血缘专家审查`](ootang_data_lineage_expert_review.md)。

## 4. 技术路线

### 4.1 高程感知 ConvLSTM

8 个测点高程先按测点总体做 z-score，再仅依据水平坐标进行 IDW，生成固定的 `4×7` 高程网格。高程作为静态通道与随时间变化的位移和环境通道共同进入 ConvLSTM；它不参与距离度量，也没有被解释为三维位移或 GNSS 高程变化。

模型保持 7 日输入、1 日预测和 P10/P50/P90 三分位数。当前 7 通道 fixed-120 协议固定 `hidden_channels=16`、卷积核 3、学习率 `1e-3`，并完成三个 287 日非重叠扩展窗口测试折：2018-02-21—2018-12-04、2018-12-05—2019-09-17、2019-09-18—2020-06-30。每折训练历史末 20% 仅用于区间校准，测试段不参与模型、种子或参数选择。

滚动 seed 0 和预设种子 `0,1,2,3,4` 的 15 个折—种子拟合均已完成，保存了 34,440 条 `seed × fold × date × station` 逐日预测；rolling seed 0 与 five-seed 中的 seed 0 逐值一致。运行对应提交为 `1e06629119e08b33ded2540a435e726c2d2da97a`。当前 7 通道尚未运行早停和容量敏感性；加入高程前的 6 通道历史诊断仍只作探索性版本对照，不能替代 7 通道复验或用于高程因果归因。预警 v4 继续使用其版本化运行输入，未用五种子结果重新选择阈值或规则。

R3 的模型分工按用户对导师意见的当前解释固定，并沿用毕业论文中“解释模型与概率预测模型分离”的角色先例。当前项目中，ConvLSTM 单独输出 P10/P50/P90 并评价 PICP；当前 SHAP 解释五分类 site NGBoost 的期望等级，旧回归 SHAP 仅作历史对照。两者都不是 ConvLSTM 内部 SHAP，也不能称为已确定物理主控因素或正式五级预警的因果解释。毕业论文以多次 LSTM 独立训练形成分布，当前项目采用分位数 ConvLSTM，二者不是同一不确定性算法；该先例只支持模型角色分工，不覆盖指定 Word 论文对预警指标、阈值和融合的主依据地位。

### 4.2 四指标测点规则

每个测点、每个可评估时刻均读取以下四项信息：

1. 观测位移相对预测分布的区间偏离状态；
2. 逐日速度 `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`；
3. 严格逐点加速度 `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`；
4. 基于速度比较器的改进切线角。

速度与切线角属于同一运动学证据族，不能当作两个独立投票；区间与加速度各为独立族，测点候选取三族最高等级。原始 `ΔV_i=v_i-v_{i-1}` 仍记录为过程审计，会进入 `transition_status` 和一致性说明，但不凭符号机械升降颜色。逐点输出同时保留四项状态、贡献指标、融合理由和不可评估原因。

### 4.3 v4 严格逐点加速度扩展

v4 在保留区间、速度/切线角运动学族和原始 `ΔV` 审计字段的同时，新增独立加速度证据族。对相邻速度使用真实时间间隔计算
`a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，单位为 `mm/day²`；首个速度行和前两个加速度行执行三点 warmup。所有加速度阈值只在同一 fit-only 稳定段估计：`A=mean(a)`、`sigma_a=sample std(ddof=1)`、`A0=max(1.5A,A+2sigma_a)`；`A0` 非有限或不大于零时 fail closed。五级边界为 green `<A0-sigma_a`、blue `[A0-sigma_a,A0+sigma_a]`、yellow `(A0+sigma_a,5A0)`、orange `[5A0,10A0)`、red `>=10A0`。速度与切线角仍只计一个运动学族，加速度可独立改变候选等级，raw `ΔV` 不参加 ordinal vote。

导师后续确认加速度阈值也沿用指定 Word 的速度相对结构。因 Word 没有严格加速度阈值表，v4 将 `V/V0` 量纲一致地转为 `a/A0`，保留 `1×/5×/10×` 倍数，且将定性“约等于”操作化为 `A0±sigma_a`。这不等于现场验证；v4 同时显式绑定 v1 基础协议和 v2 加速度扩展协议的内容 SHA-256；核心及三类图件 manifest 均复核源码指纹、输入/输出哈希、行数、双协议哈希、`formal_warning_output=false` 和 `vajont_used=false`。

### 4.4 滑坡体双轴空间规则（当前 v4 使用）

当前实现把“滑坡体整体确认等级”和“局部最高候选等级”分开；该空间逻辑最初形成于 v3 草案，但当前不再存在 v3 运行入口：

- 所有整体颜色首先要求至少 3 个可评估测点，并覆盖 O1、O2、O3；
- blue 及以上整体状态还要求至少 2 个测点、至少 2 个空间分区提供相应支撑；
- 单点或单区 blue 记为整体 green，同时保留 `localized_blue_attention`；
- 未获跨区确认的 yellow、orange、red 只保留在局部候选轴，不静默降为 green 或 blue。

该设计解决了 v2 中 green 过严、局部候选与整体确认混写的问题，同时保持局部高等级信息可见。

## 5. 主要结果

### 5.1 位移预测：fixed-120 三折 × 五种子

每个测试折包含 287 日 × 8 点，共 2,296 条逐点记录；五种子合计 34,440 条预测。以下为总体、校准区间口径，`skill > 0` 表示优于昨日位移持久性基线：

| fold | 模型 RMSE，均值 ± SD (mm) | 基线 RMSE (mm) | RMSE skill；正值种子 | MAE skill；正值种子 | 增量相关 | 增量标准差比 | PICP / 目标 | 区间宽度 (mm) | mean pinball | 80% interval score |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.970 ± 0.418 | 0.245 | -7.036；0/5 | -8.270；0/5 | 0.202 | 6.933 | 0.387 / 0.800 | 2.959 | 0.534 | 8.463 |
| 2 | 0.356 ± 0.085 | 0.120 | -1.970；0/5 | -1.363；0/5 | 0.187 | 3.668 | 0.956 / 0.800 | 1.320 | 0.086 | 1.475 |
| 3 | 0.328 ± 0.008 | 0.340 | 0.036；5/5 | 0.066；5/5 | -0.041 | 0.156 | 0.754 / 0.800 | 0.476 | 0.065 | 1.102 |

fold 1/2 对所有种子均明显劣于基线，并分别过度放大增量波动；fold 3 虽对所有种子略优，但预测增量标准差约收缩 84%，相关性接近零，其增益更符合平均漂移修正和强平滑，不能解释为稳定跟踪逐日触发过程。80% 预测区间的 PICP 在三折分别表现为明显欠覆盖、过覆盖和轻度欠覆盖，说明校准不能稳定跨时期迁移。

历史 6 通道与当前 7 通道的 15 运行平均值显示，7 通道 RMSE/MAE 分别低约 14.3%/15.7%，但逐种子仅 8/15 个 RMSE 和 9/15 个 MAE 更低，fold 3 的平均 RMSE/MAE 反而高约 1.6%/2.6%，总体正 skill 数仍同为 5/15。该差异由早期高误差折主导，且历史工件缺少当前完整输入血缘，因此只能视为版本表现变化，不能证明高程带来因果增益。由于三个外层测试折均已查看，后续不得据此选择高程尺度、网络规模、轮数、阈值或最佳种子。

#### 5.1.1 14 日时间块条件性诊断

当前 7 通道最后一折 `seed=0` 的预设 14 日非循环重叠 moving-block bootstrap 结果为：RMSE 差值（模型−持久性基线）`-0.00249 mm`，95% CI `[-0.00678, 0.00256]`；MAE 差值 `-0.00620 mm`，95% CI `[-0.01395, 0.00217]`；校准后 PICP `0.7696`，95% CI `[0.6755, 0.8742]`；校准后 80% interval score `1.125 mm`，95% CI `[0.648, 1.538]`。完整结果见 [`forecast_bootstrap_ci.csv`](../figures/convlstm/forecast_bootstrap_ci.csv)。两项误差差值区间均跨 0，不支持“稳定优于持久性基线”；该诊断固定已训练模型和 `qhat`，仅量化当前测试样本的条件性抽样不确定性，不覆盖其他折、其他种子、训练不确定性或未来制度变化。

#### 5.1.2 八测点异质性

跨三个折、五个预设种子等权汇总后，测点 RMSE 均值从 MJ9 的 `0.130 mm` 到 ATU3 的 `1.340 mm`，相差超过一个数量级；增量相关均值仅 MJ9 达 `0.324`，MJ3、ATU1 和 ATU2 为负。正 RMSE skill 主要集中于 fold 3；fold 3 的平均测点 PICP 也从 MJ9/MJ3 的约 `0.479/0.537` 到 ATU1–ATU5 的约 `0.862–0.889`。逐折、逐点、逐种子明细见 [`seed_stability_summary.csv`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_summary.csv)。总体均值会掩盖明显空间异质性；缺少独立坡体结构和原始观测资料时，不能把差异归因于高程控制或具体地质机制。

### 5.2 v4 逐点与滑坡体输出

- 测点时间线：4,112 条，区间、速度、加速度和切线角四项输入均可评估；
- 滑坡体时间线：514 日，无重复或静默缺日；
- 空间融合状态：`valid=114`，`candidate_not_site_confirmed=400`；
- 整体确认轴：green 8、blue 48、yellow 31、orange 9、red 18，另有 400 日不发布整体颜色；
- 局部最高候选轴：blue 56、yellow 196、orange 111、red 151；
- 加速度单项等级：green 4,012、blue 98、yellow 2、orange 0、red 0；
- 单区 blue 关注：8 日。

400 个未获整体空间确认的日期均有 8/8 测点、3/3 分区和完整四指标，不是站点缺失造成。其中 189 日不足 2 个 yellow+ 测点，211 日虽达到至少 2 点但全部位于 O1；对应高等级候选主要反映 O1 的局部区间偏离，不能解释为全滑坡体同步进入相同等级。加速度只有 2 条 yellow 测点记录，未产生 orange/red。

### 5.3 五个代表日

| 日期 | 整体确认 | 局部最高 | 规则含义 |
| --- | --- | --- | --- |
| 2019-06-30 | green | blue | 单区 blue，保留局部关注 |
| 2019-07-03 | blue | blue | 两区 blue 支撑，整体确认 blue |
| 2020-03-09 | yellow | red | 整体黄色已跨区确认，局部仍达红色 |
| 2020-06-04 | orange | orange | O1 与 O2 提供橙色支撑 |
| 2020-06-13 | red | red | O2 与 O3 提供红色支撑 |

这些日期是在观测后按当前版本化语义规则自动选择出的可用整体颜色代表日（blue、yellow、orange、red，另保留 localized blue）；它们不是独立事件样本，不能用于计算召回率、误报率、提前量或工程预警效果。

### 5.4 自动标签与 NGBoost 五分类结果

ECDF 标签器在 fold 1 的 site 五级各有 56 日；fold 2 为 green/blue/yellow/orange/red
`138/66/39/21/16`。固定 32 维 site NGBoost 在 fold 2 的
accuracy/macro-F1/ordinal MAE/log-loss/Brier 为
`0.3536/0.2871/0.7429/3.3358/0.9960`；严格 persistence 为
`0.8022/0.6722/0.2234`，无信息均匀概率的 log-loss 为 `1.6094`。

lag-memory 在共同 273 日的硬指标与 v1 相同，概率指标更差，且 lag 特征内置重要性为 0。
residual 在同一共同集为 `0.3553/0.3307/0.7070/3.7538/1.0238`，五项机械门槛全部为
false。结构改造虽使部分硬指标较 v1 小幅上升，但仍远落后 persistence，概率质量也未改善。
三种方案均未证明分类改善，按预注册停止继续修补 NGBoost；ConvLSTM 与 v4 均未因此修改。
本节不报告 fold 3 指标。

## 6. 高程能够增加什么，不能增加什么

高程能够增加的是**空间结构信息**。对于 ConvLSTM，静态高程网格使不同坡位在卷积邻域中具有可区分的地形背景，因此比只用平面坐标和位移场更符合坡体空间异质性的建模直觉。

高程不能自动增加的是**观测证据等级**。它不能替代原始 GNSS，不能恢复日序列生成链，也不能证明位移变化由高程所致。当前三折 × 五种子结果显示版本差异随折次和种子改变，且没有改变“前两折失败、第三折仅小幅正 skill”的主要模式。因此论文中可写“引入静态地形先验并完成内部探索性诊断”，不应写“高程显著提高预测精度或预警可信性”。

## 7. V0、稳定段和阈值状态

指定 Word 论文要求以 MVIF 趋势项的初始稳定斜率确定逐测点基准速度，并按 `V0=MAX(1.5V, V+2σ)` 建立速度框架。当前输入上的严格 MVIF 拟合无法稳定识别有限 `t_f`，因此项目没有伪造一个“论文同款 MVIF V0”。

为先跑通藕塘，v4 使用的是明确标记为 `raw_velocity_kmeans_comparator` 的项目特有比较器，并在配置和结果中保留这一来源。它可以支持工程流程演示，但不能在论文中不加限定地称为指定 Word 方法的严格复现。若最终数据集具备可解释稳定段，应由机器重新建立并版本化逐点稳定段、V、σ、V0、blue 边界、加速度基线和切线角参数，再进行确认性验证。

## 8. 当前可以与不可以写入论文的结论

### 可以写入阶段性方法或内部结果

- 已建立包含静态高程先验的 7 通道 ConvLSTM，并完成 8 测点 fixed-120 三折 × 五种子内部诊断；
- 已建立区间、速度、加速度、改进切线角的透明逐点规则输出；
- 已建立局部候选与滑坡体整体确认分离的 v4 双轴空间融合；
- 已建立 H=7 ECDF 自动多点代理标签、32 维四指标 NGBoost 五分类、八点/全时刻输出和分类 SHAP；
- 固定分类器、lag-memory 与 residual 均未超过 persistence，属于可复现的探索性负结果；
- fixed-120 结果在 fold 1/2 均劣于持久性基线，在 fold 3 仅小幅优于基线，跨时期预测与区间校准均不稳定；
- 与历史 6 通道工件的探索性对照没有形成一致的折次和种子优势，不能据此归因于高程；
- 现有结果适合用于方法跑通、输出设计和局限性讨论。

### 不能写成正式结论

- 不能称当前输入为已验证的独立原始逐日 GNSS；
- 不能称当前 test 为未被探索过的确认性外部测试；
- 不能声称高程造成预测性能改善或具有因果作用；
- 不能把当前比较器写成指定 Word 的严格 MVIF V0；
- 不能把代表日或规则输出写成真实事件召回、误报或提前量；
- 不能把 v4 颜色写成已经通过现场验证的正式预警等级。

## 9. 当时的后续技术决策（历史记录）

当前停止 NGBoost patching，不调参或增加第二变体，也不改 ConvLSTM/v4 历史结果。下一步只做
结果与论文收口，并为未来独立数据建立可复现的数据与评价合同；不设置人工批准或冻结步骤。

- **结果与论文收口**：以本文件的谨慎口径统一方法、结果、图表和限制，不把负结果改写为有效性结论；
- **未来独立数据**：优先选择原始观测、坐标、时间生成链和事件结局可追溯的数据，并建立独立的数据与评价合同；不得用未来结果反调当前藕塘阈值或模型。

## 10. 复核入口

| 目的 | 文件 |
| --- | --- |
| 数据来源与确认性证据门禁 | [`ootang_data_lineage_expert_review.md`](ootang_data_lineage_expert_review.md) |
| 高程可信性、400 日成因和空间规则审查 | [`ootang_elevation_warning_expert_review.md`](ootang_elevation_warning_expert_review.md) |
| 当前 v4 运行与字段说明 | [`ootang_operational_run.md`](ootang_operational_run.md) |
| ConvLSTM 运行来源、切分和输出哈希 | [`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json) |
| v4 规则、结果计数和输入哈希 | [`ootang_operational_run_manifest.json`](../figures/warning_operational_draft_v4/ootang_operational_run_manifest.json) |
| ECDF 自动标签与门禁 | [`manifest.json`](../figures/ngboost_auto_state_ecdf_v2/manifest.json)、[`label_gate.json`](../figures/ngboost_auto_state_ecdf_v2/label_gate.json) |
| 固定分类器指标、全时刻图和 SHAP | [`manifest.json`](../figures/ngboost_auto_state_classifier_v1/manifest.json)、[`metrics.csv`](../figures/ngboost_auto_state_classifier_v1/metrics.csv)、[`warning_timeline.pdf`](../figures/ngboost_auto_state_classifier_v1/warning_timeline.pdf)、[`site_shap_summary.pdf`](../figures/ngboost_auto_state_classifier_v1/site_shap_summary.pdf) |
| lag-memory 结果 | [`manifest.json`](../figures/ngboost_auto_state_memory_v2/manifest.json)、[`comparison_metrics.csv`](../figures/ngboost_auto_state_memory_v2/comparison_metrics.csv) |
| residual 结果 | [`manifest.json`](../figures/ngboost_auto_state_residual_v3/manifest.json)、[`comparison_metrics.csv`](../figures/ngboost_auto_state_residual_v3/comparison_metrics.csv) |
| 当前藕塘五阶段运行记录 | [`ootang_advisor_demo_run.json`](../figures/pipeline/ootang_advisor_demo_run.json)；记录 v4 基线、自动标签、ECDF、NGBoost 分类与证据包五阶段合同，本轮复用既有 ConvLSTM 预测 |
| 代表日规则图 | [`ootang_v4_typical_days.svg`](../figures/warning_operational_draft_v4/ootang_v4_typical_days.svg) |
| 514 日完整预警状态图 | [`ootang_v4_full_warning_timeline.svg`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg) |
| 8 点位移—四指标—最终等级联合图 | [`ootang_v4_all_station_combined_diagnostic.svg`](../figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg) |
| 7 通道 fixed-120 协议与科学审查 | [`ootang_convlstm_elevation_fixed120_review.md`](ootang_convlstm_elevation_fixed120_review.md) |
| 7 通道 rolling seed 0 产物与血缘 | [`rolling manifest`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json) |
| 7 通道三折 × 五种子产物与血缘 | [`five-seed manifest`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json) |
| 7 通道 fixed-120 管线运行记录 | [`convlstm_elevation_fixed120_v1_run.json`](../figures/pipeline/convlstm_elevation_fixed120_v1_run.json) |

## 11. 论文与导师汇报的最小图表清单

以下清单沿用固定配置和版本化输入。本轮没有重跑 ConvLSTM；v4 与固定分类器图件由五阶段
流水线刷新一次，表格再从所列 CSV/JSON 机械汇总，没有选择新模型或查看新的评价折。

### 11.1 核心图件

| 顺序 | 图件与用途 | 使用边界 |
| ---: | --- | --- |
| 1 | [`ConvLSTM 8 点完整时间轴`](../figures/convlstm/forecast_all_stations.png)：展示 fit 诊断、calibration、test 预测及 P10/P50/P90 | 当前 7 通道 seed-0 单切分图，不代表三折 × 五种子总体；fit 线不是独立评价。现有 2100×3000 PNG 可用于汇报，投稿时仅需从既有 CSV 等价导出矢量版。 |
| 2 | [`H=7 site 与 8 点逐时颜色`](../figures/ngboost_auto_state_classifier_v1/warning_timeline.pdf)（[SVG](../figures/ngboost_auto_state_classifier_v1/warning_timeline.svg)）：回答每个时刻输出何种信号 | 自动标签是未来变形 proxy，测点模型只作诊断；固定分类器未胜 lag-7 persistence，不展示 fold 3 分类指标。 |
| 3 | [`当前五级分类 SHAP`](../figures/ngboost_auto_state_classifier_v1/site_shap_summary.pdf)（[SVG](../figures/ngboost_auto_state_classifier_v1/site_shap_summary.svg)）：展示 site 模型依赖的测点 × 指标 | 解释量为 `sum(k*p_k)`；12 个背景日、25 个解释日 × 32 特征。平均绝对 SHAP 无方向，非因果、非 ConvLSTM 内部解释。 |
| 4 | [`v4 全测点四指标诊断`](../figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.pdf)（[SVG](../figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg)）：对齐位移与区间、速度、加速度、切线角和融合等级 | observed-after-forecast 透明规则基线，不是 NGBoost 监督结果或正式预警；正文空间不足时移至补充材料。 |
| 5 | [`v4 多测点综合时间线`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.pdf)（[SVG](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg)）：展示 8 点候选、局部最高和 site-confirmed 双轴 | 未确认日期表示空间支撑不足，不是缺测；不能解释为事件召回、提前量或现场有效性。 |

### 11.2 核心表格来源

| 角色 | 版本化来源 | 正文应回答的问题 |
| --- | --- | --- |
| 主表 1：ConvLSTM 全站概率预测 | [`seed_stability_metrics.csv`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_metrics.csv)、[`seed_stability_summary.csv`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_summary.csv) | 8 点及 overall 的误差、相对 persistence skill、增量响应、PICP、预测区间宽度和 interval score；按五个预设 seed 汇总，不选最佳 seed。 |
| 主表 2：H=7 自动标签定义与支持 | [`label_state_definition.csv`](../figures/ngboost_auto_state_ecdf_v2/label_state_definition.csv)、[`site_auto_labels.csv`](../figures/ngboost_auto_state_ecdf_v2/site_auto_labels.csv)、[`label_gate.json`](../figures/ngboost_auto_state_ecdf_v2/label_gate.json) | `y` 如何由 fold-1 ECDF、两个未来结果量和 O1/O2/O3 形成，以及 fold 1/2 五级支持；不列 fold 3 支持。 |
| 主表 3：分类器与基线公平比较 | [`classifier metrics`](../figures/ngboost_auto_state_classifier_v1/metrics.csv)、[`memory metrics`](../figures/ngboost_auto_state_memory_v2/comparison_metrics.csv)、[`residual metrics`](../figures/ngboost_auto_state_residual_v3/comparison_metrics.csv) | Panel A 报 fold-2 all-valid 280 日的 NGBoost/Logistic/prior；Panel B 报 common 273 日的 v1/memory/residual/persistence，并保留 rejected 决策。 |
| 主表 4：多点逐时输出与空间综合 | [`station timeline`](../figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv)、[`site timeline`](../figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv) | 各点五色候选、加速度等级、site-confirmed 与 local-max 的覆盖和支持差异；完整逐时记录作为附件。 |
| 补充表 S1：8 点加速度阈值 | [`ootang_operational_thresholds.csv`](../figures/warning_operational_draft_v4/ootang_operational_thresholds.csv) | 每点 fit-only `A/σa/A0` 与五级边界；表注明 project operationalization、non-formal，不称为论文原阈值。 |

### 11.3 已物化的导师展示附件

| 附件 | 行数 | 入口 |
| --- | ---: | --- |
| 主表 1：ConvLSTM 全站概率预测 | 27 | [`table1_convlstm.csv`](../figures/advisor_ootang_v1/table1_convlstm.csv) |
| 主表 2：H=7 自动标签与支持 | 18 | [`table2_auto_labels.csv`](../figures/advisor_ootang_v1/table2_auto_labels.csv) |
| 主表 3：分类器与基线比较 | 33 | [`table3_classifier_comparison.csv`](../figures/advisor_ootang_v1/table3_classifier_comparison.csv) |
| 主表 4：多测点综合摘要 | 23 | [`table4_multistation_summary.csv`](../figures/advisor_ootang_v1/table4_multistation_summary.csv) |
| 补充表 S1：8 点加速度阈值 | 8 | [`table_s1_acceleration_thresholds.csv`](../figures/advisor_ootang_v1/table_s1_acceleration_thresholds.csv) |

展示包同时登记上述 5 张核心图。原始 auto-state label gate 为 false，ECDF gate 为 true，
分类器结论为 `small_support_descriptive_only`；NGBoost 未超过严格 persistence。这些负结果是
本阶段应展示的科研证据，而不是继续调参或增加模型的理由。下一步只做导师展示的视觉核对与
研究方法、结果和局限叙述收口；Vajont 继续延后。5 张图已快速确认均能正常打开且内容齐全；
v4 全测点诊断图底部说明略拥挤、靠边，但不影响本次流程演示，后续只需轻量调整。
