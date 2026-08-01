# 藕塘滑坡阶段性结果与后续决策包

> 更新日期：2026-08-01
> 用途：汇总导师要求下已跑通的藕塘工程案例，形成后续撰写、审查和更换数据集时的统一入口
> 证据等级：**工程原型／内部可复算，不是确认性预测或正式预警**
> 方法依据：以[`导师修改意见整理与后续执行计划`](advisor_review_action_plan.md)和指定 Word 论文为主；用户本人的藕塘毕业论文仅作参考
> Vajont：本轮仅按用户要求完成现有文件的只读内容盘点；未启动数据适配、模型或实验，也未用于阈值选择或结果生成

## 1. 阶段结论

藕塘案例已经达到导师要求的“先跑通”目标。`features → convlstm → ootang-operational-v3` 三阶段可重复执行，8 个测点均进入高程感知 ConvLSTM、逐测点四指标判断和滑坡体级空间融合，运行清单、逐时刻结果和图件均已生成。当前不需要因为拿不到原始 GNSS 而停止这条原型路线。

数据限制影响的是**结论强度**，不是“能否运行”。现有输入是公开包中的物化日序列，原始 GNSS 锚点及日值生成链不可取得；因此本案例可用于验证代码链、输出结构和规则可审计性，但不能证明模型在独立原始 GNSS 上具有确认性预测能力，也不能把当前阈值和颜色写成可直接部署的工程预警标准。

## 2. 本阶段完成范围

| 模块 | 已完成内容 | 当前边界 | 主要证据 |
| --- | --- | --- | --- |
| 数据与空间输入 | 8 个测点完成位移列、平面坐标和高程映射；`elev_m` 作为 7 通道模型中的一个静态输入通道 | 高程是地形先验，不是新增位移观测或力学约束 | [`station_coords.csv`](../data/station_coords.csv)、[`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json) |
| 位移概率预测 | 7 日回看、1 日预测；输出 P10/P50/P90 和逐点误差；fit/calibration/test 按日期分离 | 属于物化日序列内部的单次原型结果 | [`forecast_predictions.csv`](../figures/convlstm/forecast_predictions.csv)、[`forecast_metrics.csv`](../figures/convlstm/forecast_metrics.csv) |
| 四指标逐点判断 | 区间、速度、`ΔV`、改进切线角进入全部 4,112 条测点—时刻记录 | 当前 V0 是项目特有比较器，不是指定 Word 的严格 MVIF V0 | [`ootang_operational_station_timeline.csv`](../figures/warning_operational_draft_v3/ootang_operational_station_timeline.csv)、[`ootang_operational_thresholds.csv`](../figures/warning_operational_draft_v3/ootang_operational_thresholds.csv) |
| 多测点空间融合 | v3 分别输出滑坡体确认等级和局部最高候选；全局有效点与 O1/O2/O3 覆盖门禁适用于所有颜色 | 空间支撑数及融合规则是项目原型规则，不是指定 Word 的逻辑回归复现 | [`ootang_operational_site_timeline.csv`](../figures/warning_operational_draft_v3/ootang_operational_site_timeline.csv)、[`v3 配置`](../config/ootang_operational_run.v3.draft.json) |
| 代表日审计 | 冻结 6 个语义代表日，显示逐点指标、双轴等级和跨区支撑 | 属于观测后规则说明，不用于评价提前量或预警性能 | [`代表日诊断图`](../figures/warning_operational_draft_v3/ootang_v3_typical_days.svg)、[`图件清单`](../figures/warning_operational_draft_v3/ootang_v3_typical_days_manifest.json) |

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

这一状态表示导师要求的藕塘工程初跑已经完成，但不能把结果升级为独立原始 GNSS 上的确认性日预测、正式阈值或工程预警。详细数据血缘见[`藕塘数据血缘专家审查`](ootang_data_lineage_expert_review.md)。

## 4. 技术路线

### 4.1 高程感知 ConvLSTM

8 个测点高程先按测点总体做 z-score，再仅依据水平坐标进行 IDW，生成固定的 `4×7` 高程网格。高程作为静态通道与随时间变化的位移和环境通道共同进入 ConvLSTM；它不参与距离度量，也没有被解释为三维位移或 GNSS 高程变化。

模型保持 7 日输入、1 日预测、P10/P50/P90 三分位数和固定种子 0。fit 为 2016-08-06 至 2019-02-02，calibration 为 2019-02-03 至 2019-09-17，test 为 2019-09-18 至 2020-06-30。当前高程版本只完成单次最小链路；既有滚动验证、五种子、早停和容量敏感性属于加入高程前的 6 通道历史诊断，不能直接写成当前 7 通道模型的复验结果。

### 4.2 四指标测点规则

每个测点、每个可评估时刻均读取以下四项信息：

1. 观测位移相对预测分布的区间偏离状态；
2. 逐日速度 `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`；
3. 变形速率增量 `ΔV_i=v_i-v_{i-1}` 的负、近零、正状态；
4. 基于速度比较器的改进切线角。

速度与切线角属于同一运动学证据族，不能当作两个独立投票。`ΔV>0` 只作为加速限定信息，不单独把颜色机械提升一级。逐点输出同时保留四项状态、贡献指标、融合理由和不可评估原因。

### 4.3 滑坡体 v3 双轴空间规则

v3 把“滑坡体整体确认等级”和“局部最高候选等级”分开：

- 所有整体颜色首先要求至少 3 个可评估测点，并覆盖 O1、O2、O3；
- blue 及以上整体状态还要求至少 2 个测点、至少 2 个空间分区提供相应支撑；
- 单点或单区 blue 记为整体 green，同时保留 `localized_blue_attention`；
- 未获跨区确认的 yellow、orange、red 只保留在局部候选轴，不静默降为 green 或 blue。

该设计解决了 v2 中 green 过严、局部候选与整体确认混写的问题，同时保持局部高等级信息可见。

## 5. 主要结果

### 5.1 位移预测

当前高程感知 test 段包含 287 日 × 8 点，共 2,296 条逐点记录。总体指标为：

| 指标 | ConvLSTM | 持久性基线 | 解释 |
| --- | ---: | ---: | --- |
| RMSE | 0.338 mm | 0.340 mm | RMSE skill 为 0.007，仅略优于基线 |
| MAE | 0.174 mm | 0.181 mm | 仅描述当前单次物化留出段 |
| 校准后 P10–P90 覆盖率 | 0.770 | 目标 0.800 | 仍有约 0.030 的覆盖缺口 |

相对于加入高程前的同一单种子快照，高程版本 RMSE 约由 0.318 mm 增至 0.338 mm。由于 test 已被查看，后续不得据此选择高程尺度、网络规模、阈值或最佳种子。当前结果支持“流程已跑通”，不支持“高程已经改善预测性能”。

### 5.2 v3 逐点与滑坡体输出

- 测点时间线：4,112 条，全部四项输入可评估；
- 滑坡体时间线：514 日，无重复或静默缺日；
- 空间融合状态：`valid=114`，`candidate_not_site_confirmed=400`；
- 整体确认轴：green 8、blue 48、yellow 31、orange 9、red 18，另有 400 日不发布整体颜色；
- 局部最高候选轴：blue 56、yellow 196、orange 111、red 151；
- 单区 blue 关注：8 日。

400 个未获整体空间确认的日期均有 8/8 测点、3/3 分区和完整四指标，不是站点缺失造成。其中 189 日不足 2 个 yellow+ 测点，211 日虽达到至少 2 点但全部位于 O1；对应高等级候选主要反映 O1 的局部区间偏离，不能解释为全滑坡体同步进入相同等级。

### 5.3 六个代表日

| 日期 | 整体确认 | 局部最高 | 规则含义 |
| --- | --- | --- | --- |
| 2019-02-03 | 未确认 | yellow | 单点、单区黄色候选 |
| 2019-06-30 | green | blue | 单区 blue，保留局部关注 |
| 2020-03-06 | 未确认 | red | O1 局部红色候选未获跨区支撑 |
| 2020-03-09 | yellow | red | 整体黄色已跨区确认，局部仍达红色 |
| 2020-06-04 | orange | orange | O1 与 O2 提供橙色支撑 |
| 2020-06-13 | red | red | O2 与 O3 提供红色支撑 |

这些日期是在观测后按当前版本化冻结的语义规则自动选择出的每类最早日期，适合说明融合逻辑；它们不是独立事件样本，不能用于计算召回率、误报率、提前量或工程预警效果。

## 6. 高程能够增加什么，不能增加什么

高程能够增加的是**空间结构信息**。对于 ConvLSTM，静态高程网格使不同坡位在卷积邻域中具有可区分的地形背景，因此比只用平面坐标和位移场更符合坡体空间异质性的建模直觉。

高程不能自动增加的是**观测证据等级**。它不能替代原始 GNSS，不能恢复日序列生成链，也不能证明位移变化由高程所致。当前单种子结果甚至没有显示误差改善，因此论文中可写“引入静态地形先验并完成原型验证”，不应写“高程显著提高预测精度或预警可信性”。

## 7. V0、稳定段和阈值状态

指定 Word 论文要求以 MVIF 趋势项的初始稳定斜率确定逐测点基准速度，并按 `V0=MAX(1.5V, V+2σ)` 建立速度框架。当前输入上的严格 MVIF 拟合无法稳定识别有限 `t_f`，因此项目没有伪造一个“论文同款 MVIF V0”。

为先跑通藕塘，v3 使用的是明确标记为 `raw_velocity_kmeans_comparator` 的项目特有比较器，并在配置和结果中保留这一来源。它可以支持工程流程演示，但不能在论文中不加限定地称为指定 Word 方法的严格复现。若最终数据集具备可解释稳定段，应重新冻结逐点稳定段、V、σ、V0、blue 边界、`ΔV≈0` 容差和切线角参数，再进行确认性验证。

## 8. 当前可以与不可以写入论文的结论

### 可以写入阶段性方法或内部结果

- 已建立包含静态高程先验的 7 通道 ConvLSTM，并完成 8 测点全链路运行；
- 已建立区间、速度、`ΔV`、改进切线角的透明逐点规则输出；
- 已建立局部候选与滑坡体整体确认分离的 v3 双轴空间融合；
- 当前单次原型只略优于持久性 RMSE 基线，未显示高程带来的性能提升；
- 现有结果适合用于方法跑通、输出设计和局限性讨论。

### 不能写成正式结论

- 不能称当前输入为已验证的独立原始逐日 GNSS；
- 不能称当前 test 为未被探索过的确认性外部测试；
- 不能声称高程造成预测性能改善或具有因果作用；
- 不能把当前比较器写成指定 Word 的严格 MVIF V0；
- 不能把代表日或规则输出写成真实事件召回、误报或提前量；
- 不能把 v3 颜色写成已经通过现场验证的正式预警等级。

## 9. 后续技术决策

当前推荐冻结 v3，不再根据已经查看的 test 调模型或规则。下一步不是继续优化藕塘分数，而是决定最终论文的数据角色：

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
| v1/v2/v3 运行与字段说明 | [`ootang_operational_run.md`](ootang_operational_run.md) |
| ConvLSTM 运行来源、切分和输出哈希 | [`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json) |
| v3 规则、结果计数和输入哈希 | [`ootang_operational_run_manifest.json`](../figures/warning_operational_draft_v3/ootang_operational_run_manifest.json) |
| 完整最小链路运行记录 | [`latest_run.json`](../figures/pipeline/latest_run.json) |
| 六个代表日规则图 | [`ootang_v3_typical_days.svg`](../figures/warning_operational_draft_v3/ootang_v3_typical_days.svg) |
