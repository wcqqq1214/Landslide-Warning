# 藕塘滑坡阶段性结果与后续决策包

> 更新日期：2026-08-13
> 用途：汇总导师要求下已跑通的藕塘工程案例，形成后续撰写、审查和更换数据集时的统一入口
> 证据等级：**工程原型／内部可复算，不是确认性预测或正式预警**
> 方法依据：以[`导师修改意见整理与后续执行计划`](advisor_review_action_plan.md)和指定 Word 论文为主；用户本人的藕塘毕业论文仅作参考
> Vajont：本轮仅按用户要求完成现有文件的只读内容盘点；未启动数据适配、模型或实验，也未用于阈值选择或结果生成

> 工程口径（2026-08-13）：v4 加速度扩展是唯一可执行预警原型。默认入口为 `features → convlstm → ootang-operational-v4`，入口 manifest 使用 schema 3；旧 30 日 V0、旧融合及 v1/v2/v3 运行入口仅留在 Git 历史。本次未启动正式 NGBoost 或 Vajont。

## 1. 阶段结论

藕塘案例已经达到导师要求的“先跑通”目标。`features → convlstm → ootang-operational-v4` 三阶段可重复执行，8 个测点均进入高程感知 ConvLSTM、逐测点区间/运动学/加速度三族判断和滑坡体级双轴空间融合（实现沿自早期 v3 规则，但当前仅由 v4 调用），运行清单、逐时刻结果和图件均已生成。当前不需要因为拿不到原始 GNSS 而停止这条原型路线。

数据限制影响的是**结论强度**，不是“能否运行”。现有输入是公开包中的物化日序列，原始 GNSS 锚点及日值生成链不可取得；因此本案例可用于验证代码链、输出结构和规则可审计性，但不能证明模型在独立原始 GNSS 上具有确认性预测能力，也不能把当前阈值和颜色写成可直接部署的工程预警标准。

## 2. 本阶段完成范围

| 模块 | 已完成内容 | 当前边界 | 主要证据 |
| --- | --- | --- | --- |
| 数据与空间输入 | 8 个测点完成位移列、平面坐标和高程映射；`elev_m` 作为 7 通道模型中的一个静态输入通道 | 高程是地形先验，不是新增位移观测或力学约束 | [`station_coords.csv`](../data/station_coords.csv)、[`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json) |
| 位移概率预测 | 7 日回看、1 日预测；输出 P10/P50/P90 和逐点误差；已完成 fixed-120 三个滚动折 × 五个预设种子及全部逐日预测 | 属于物化日序列内部探索性诊断；早停与容量敏感性尚未重跑 | [`7 通道 fixed-120 审查`](ootang_convlstm_elevation_fixed120_review.md)、[`five-seed manifest`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json) |
| 模型解释分工 | 导师确认由 ConvLSTM 负责 P10/P50/P90 与覆盖评价，独立 NGBoost 回归+SHAP 负责候选模型依赖 | 沿用用户毕业论文中 LightGBM+SHAP 与 LSTM 分离的角色先例；当前不是 ConvLSTM-SHAP，NGBoost 目标也不是正式五级融合，也不能单独证明物理因果主控 | [`回归 SHAP provenance`](../figures/shap/ngboost_regression_shap_provenance.json)、[`ngboost_shap_protocol.md`](ngboost_shap_protocol.md) |
| v4 三族逐点判断 | 区间、运动学（速度/切线角）和严格逐点加速度进入全部 4,112 条测点—时刻记录；raw `ΔV` 保留审计 | V0 是项目特有比较器；导师确认加速度沿用指定 Word 速度 `V0` 的相对结构，v4 以加速度自身 A0 量纲一致转置，不伪称 Word 有严格加速度表 | [`ootang_operational_station_timeline.csv`](../figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv)、[`ootang_operational_thresholds.csv`](../figures/warning_operational_draft_v4/ootang_operational_thresholds.csv) |
| 多测点空间融合 | v4 使用双轴空间融合，分别输出滑坡体确认等级和局部最高候选；全局有效点与 O1/O2/O3 覆盖门禁适用于所有颜色 | 空间支撑数及融合规则是项目原型规则，不是指定 Word 的逻辑回归复现 | [`ootang_operational_site_timeline.csv`](../figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv)、[`v4 配置`](../config/ootang_operational_run.v4.draft.json) |
| 代表日审计 | v4 代表日显示 interval/velocity/acceleration/tangent/fused 证据、双轴等级和跨区支撑 | 属于观测后规则说明，不用于评价提前量或预警性能 | [`代表日诊断图`](../figures/warning_operational_draft_v4/ootang_v4_typical_days.svg)、[`图件清单`](../figures/warning_operational_draft_v4/ootang_v4_typical_days_manifest.json) |
| 全时刻等级展示 | 覆盖 514 日 × 8 点候选等级，并同时显示滑坡体整体确认与局部最高双轴 | 400 个 `NC` 是空间佐证不足而非缺测；属于观测后状态审计 | [`完整时间线`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg)、[`图件清单`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline_manifest.json) |
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

滚动 seed 0 和预设种子 `0,1,2,3,4` 的 15 个折—种子拟合均已完成，保存了 34,440 条 `seed × fold × date × station` 逐日预测；rolling seed 0 与 five-seed 中的 seed 0 逐值一致。运行对应提交为 `1e06629119e08b33ded2540a435e726c2d2da97a`。当前 7 通道尚未运行早停和容量敏感性；加入高程前的 6 通道历史诊断仍只作探索性版本对照，不能替代 7 通道复验或用于高程因果归因。预警 v4 继续使用其已冻结的运行输入，未用五种子结果重新选择阈值或规则。

R3 的模型分工已由导师确认，并沿用毕业论文中“LightGBM+SHAP 负责特征解释，LSTM 负责概率预测”的分离式角色先例。当前项目对应为：ConvLSTM 单独输出 P10/P50/P90 并评价覆盖；独立 NGBoost 回归+SHAP 分析候选模型依赖。这不是 ConvLSTM-SHAP，不能称为已确定物理主控因素；该独立回归目标也不是正式五级融合。毕业论文以多次 LSTM 独立训练形成分布，当前项目采用分位数 ConvLSTM，二者不是同一不确定性算法；该先例只支持模型角色分工，不覆盖指定 Word 论文对预警指标、阈值和融合的主依据地位。

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

| fold | 模型 RMSE，均值 ± SD (mm) | 基线 RMSE (mm) | RMSE skill；正值种子 | MAE skill；正值种子 | 增量相关 | 增量标准差比 | coverage / 目标 | 区间宽度 (mm) | mean pinball | 80% interval score |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.970 ± 0.418 | 0.245 | -7.036；0/5 | -8.270；0/5 | 0.202 | 6.933 | 0.387 / 0.800 | 2.959 | 0.534 | 8.463 |
| 2 | 0.356 ± 0.085 | 0.120 | -1.970；0/5 | -1.363；0/5 | 0.187 | 3.668 | 0.956 / 0.800 | 1.320 | 0.086 | 1.475 |
| 3 | 0.328 ± 0.008 | 0.340 | 0.036；5/5 | 0.066；5/5 | -0.041 | 0.156 | 0.754 / 0.800 | 0.476 | 0.065 | 1.102 |

fold 1/2 对所有种子均明显劣于基线，并分别过度放大增量波动；fold 3 虽对所有种子略优，但预测增量标准差约收缩 84%，相关性接近零，其增益更符合平均漂移修正和强平滑，不能解释为稳定跟踪逐日触发过程。80% 区间在三折分别明显欠覆盖、过覆盖和轻度欠覆盖，说明校准不能稳定跨时期迁移。

历史 6 通道与当前 7 通道的 15 运行平均值显示，7 通道 RMSE/MAE 分别低约 14.3%/15.7%，但逐种子仅 8/15 个 RMSE 和 9/15 个 MAE 更低，fold 3 的平均 RMSE/MAE 反而高约 1.6%/2.6%，总体正 skill 数仍同为 5/15。该差异由早期高误差折主导，且历史工件缺少当前完整输入血缘，因此只能视为版本表现变化，不能证明高程带来因果增益。由于三个外层测试折均已查看，后续不得据此选择高程尺度、网络规模、轮数、阈值或最佳种子。

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

这些日期是在观测后按当前版本化冻结的语义规则自动选择出的可用整体颜色代表日（blue、yellow、orange、red，另保留 localized blue）；它们不是独立事件样本，不能用于计算召回率、误报率、提前量或工程预警效果。

## 6. 高程能够增加什么，不能增加什么

高程能够增加的是**空间结构信息**。对于 ConvLSTM，静态高程网格使不同坡位在卷积邻域中具有可区分的地形背景，因此比只用平面坐标和位移场更符合坡体空间异质性的建模直觉。

高程不能自动增加的是**观测证据等级**。它不能替代原始 GNSS，不能恢复日序列生成链，也不能证明位移变化由高程所致。当前三折 × 五种子结果显示版本差异随折次和种子改变，且没有改变“前两折失败、第三折仅小幅正 skill”的主要模式。因此论文中可写“引入静态地形先验并完成内部探索性诊断”，不应写“高程显著提高预测精度或预警可信性”。

## 7. V0、稳定段和阈值状态

指定 Word 论文要求以 MVIF 趋势项的初始稳定斜率确定逐测点基准速度，并按 `V0=MAX(1.5V, V+2σ)` 建立速度框架。当前输入上的严格 MVIF 拟合无法稳定识别有限 `t_f`，因此项目没有伪造一个“论文同款 MVIF V0”。

为先跑通藕塘，v4 使用的是明确标记为 `raw_velocity_kmeans_comparator` 的项目特有比较器，并在配置和结果中保留这一来源。它可以支持工程流程演示，但不能在论文中不加限定地称为指定 Word 方法的严格复现。若最终数据集具备可解释稳定段，应重新冻结逐点稳定段、V、σ、V0、blue 边界、加速度基线和切线角参数，再进行确认性验证。

## 8. 当前可以与不可以写入论文的结论

### 可以写入阶段性方法或内部结果

- 已建立包含静态高程先验的 7 通道 ConvLSTM，并完成 8 测点 fixed-120 三折 × 五种子内部诊断；
- 已建立区间、速度、加速度、改进切线角的透明逐点规则输出；
- 已建立局部候选与滑坡体整体确认分离的 v4 双轴空间融合；
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

## 9. 后续技术决策

当前推荐冻结 v4 和 7 通道 fixed-120 结果，不再根据已经查看的外层测试折调模型或规则。7 通道早停与容量敏感性尚未完成；若后续确需开展，应先冻结只使用训练内部时序切分的选择协议，且不得覆盖本轮 fixed-120 结果或使用外层测试折选参。正式 NGBoost 的前提是独立五级结局标签，不能用 v4 规则输出自训练。下一步重点是决定最终论文的数据角色：

1. **藕塘保留为原型案例**：使用本文件的谨慎口径，重点展示方法链、空间双轴输出和局限性；
2. **选择可追溯的新主数据集**：先审计原始观测、坐标、时间生成链和事件标签，再重新冻结切分、稳定段、V0、阈值和验证协议；
3. **若最终仍以藕塘承担确认性主案例**：需要获得能够解除证据门禁的新增来源材料，否则必须收窄论文主张；
4. **Vajont 保持独立 P2**：本轮只读内容盘点不构成案例启动授权；只有用户另行明确允许后，才建立数据适配、运行质量审计和独立结果目录；不得用其结果反调藕塘阈值或模型。

## 10. 复核入口

| 目的 | 文件 |
| --- | --- |
| 导师意见、来源优先级和执行边界 | [`advisor_review_action_plan.md`](advisor_review_action_plan.md) |
| 数据来源与确认性证据门禁 | [`ootang_data_lineage_expert_review.md`](ootang_data_lineage_expert_review.md) |
| 高程可信性、400 日成因和空间规则审查 | [`ootang_elevation_warning_expert_review.md`](ootang_elevation_warning_expert_review.md) |
| 当前 v4 运行与字段说明 | [`ootang_operational_run.md`](ootang_operational_run.md) |
| ConvLSTM 运行来源、切分和输出哈希 | [`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json) |
| v4 规则、结果计数和输入哈希 | [`ootang_operational_run_manifest.json`](../figures/warning_operational_draft_v4/ootang_operational_run_manifest.json) |
| schema 3 最小链路运行记录 | 原 `figures/pipeline/latest_run.json` 为 2026-08-01 的 v3 残留记录，已于 2026-08-15 删除；当前 HEAD 无端到端运行清单，需按 Git 历史提交 `7d2e38b` 查阅旧记录或重新完整运行生成 |
| 代码库审查与工程门禁 | [`codebase_review_2026-08-05.md`](codebase_review_2026-08-05.md) |
| 代表日规则图 | [`ootang_v4_typical_days.svg`](../figures/warning_operational_draft_v4/ootang_v4_typical_days.svg) |
| 514 日完整预警状态图 | [`ootang_v4_full_warning_timeline.svg`](../figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg) |
| 8 点位移—四指标—最终等级联合图 | [`ootang_v4_all_station_combined_diagnostic.svg`](../figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg) |
| 7 通道 fixed-120 协议与科学审查 | [`ootang_convlstm_elevation_fixed120_review.md`](ootang_convlstm_elevation_fixed120_review.md) |
| 7 通道 rolling seed 0 产物与血缘 | [`rolling manifest`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json) |
| 7 通道三折 × 五种子产物与血缘 | [`five-seed manifest`](../figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json) |
| 7 通道 fixed-120 管线运行记录 | [`convlstm_elevation_fixed120_v1_run.json`](../figures/pipeline/convlstm_elevation_fixed120_v1_run.json) |
