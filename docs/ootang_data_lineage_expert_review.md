# 藕塘滑坡日序列数据血缘专家审查

## Material Passport

- Origin Skill: `academic-research-suite / experiment-agent`
- Origin Mode: `validate`
- Origin Date: `2026-07-28`
- Verification Status: `ANALYZED`
- Version Label: `ootang_data_lineage_expert_review_v1`
- Source Commit: `c74a8a3`
- Review Scope: 藕塘公开日序列来源、时间结构、点位映射及其对预测与预警证据的影响
- Excluded Scope: 不读取、不分析、不启动 Vajont；不改阈值、模型结构或既有实验结论
- Formal Warning Output: `false`

## 1. 专家结论

当前仓库的 `data/monitoring_data.xlsx` 已确认与 Wang 等（2025）公开在 Figshare 的 `monitoring data.xlsx` 为同一文件，仓库 CSV 也是该工作簿的数值等价转换。问题不在仓库的 Excel-to-CSV 转换，而在公开文件之前的上游日值形成过程没有披露。

8 条累计位移和地下水位 GWT 共 9 列在全部 48 个自然月内都呈现强烈且一致的三次多项式数值指纹；降雨、库水位和温度 5 个负对照列在 48 个月中均不满足同一指纹。三个模型边界都切穿一个完整的月内三次段，其中 calibration/test 边界后的值可由边界前同月值以三次多项式近乎代数地恢复。这是强数据结构证据，但仅凭它仍不能确定作者使用了何种插值/平滑算法，也不能断言一定使用了未来观测。

建议冻结以下状态：

```text
released_series_role = materialized_daily_modeling_series
numeric_fingerprint = strong_natural_month_piecewise_cubic
mapping_to_GPS_FJ = unresolved
exact_generation_algorithm = unresolved
original_observation_anchors = not_available_in_audited_release
future_information_usage = unknown
independent_raw_daily_gnss_claim = not_supported
data_gate = blocked
formal_warning_output = false
vajont_used = false
```

因此：

1. 当前 ConvLSTM 的日期对齐、fit-only 标准化和未来一天目标构造仍可作为工程实现事实；
2. 当前性能、区间、稳定段、速度、`ΔV` 和切线角结果只能描述这份**已物化日序列**；
3. 在原始观测锚点及日值生成方法查清前，不应把跨月内边界结果升级为独立原始 GNSS 日预测证据，也不应运行新的神经网络消融并将其解释成 MJ3/MJ9 的地质偏差成因；
4. 正式 `V0`、正式四指标融合和正式预警继续保持阻断；
5. Vajont 的用户授权门禁不变。

## 2. 来源文件已经确认到什么程度

### 2.1 Figshare 原文件

公开来源为 [Wang 等（2025）](../literature/Journal%20of%20Geophysical%20Research%20%20Machine%20Learning%20and%20Computation%20-%202025%20-%20Wang%20-%20Enhancing%20Landslide%20Displacement.pdf)（[DOI](https://doi.org/10.1029/2025JH000592)）配套的 [Figshare 数据](https://doi.org/10.6084/m9.figshare.28171343.v1)：

| 字段 | 值 |
|---|---|
| Figshare article | `28171343` |
| DOI | `10.6084/m9.figshare.28171343.v1` |
| file id | `54029702` |
| file name | `monitoring data.xlsx` |
| 发布文件 MD5 | `372d1608f46d7fcdb9805568d1c0782a` |
| 仓库 XLSX MD5 | `372d1608f46d7fcdb9805568d1c0782a` |
| 仓库 XLSX SHA-256 | `a28a6a09ea660132eafe9508848dcb2958ba892f425489f63c81a7c31b2e4a3d` |
| 仓库 CSV SHA-256 | `ee63480ad9b8065dea359d49873182b1554013f910bec1c6988c0b152bede118` |

工作簿只有 1 个可见工作表，无隐藏表、无公式；CSV 与 XLSX 均为 1461 行 × 17 列，日期和列顺序完全一致，数值最大绝对差为 `1.14e-13`。所以：

```text
repository_conversion_created_monthly_structure = false
repository_csv_matches_published_workbook = true
```

### 2.2 当前仍缺失的上游材料

公开数据和代码没有提供：

- GNSS 原始解算时刻与原始位移值；
- 批次观测如何聚合为日值；
- 异常值、缺失值、仪器改正和参考基准如何处理；
- 是否按月插值、平滑或曲线拟合，以及锚点和边界条件；
- GWT 日序列的形成方法；
- MJ/ATU 与既有 GPS/FJ 监测点编号的对应表；
- 2016-07-01 至 2016-08-05 日序列的来源。

这些缺失项意味着“公开建模表可追溯”不等于“原始测量链可追溯”。

## 3. 自然月分段三次指纹

### 3.1 诊断方法

对每个自然月、每个变量分别计算：

1. 四阶有限差分 `Δ⁴x`；等间隔三次多项式的四阶差分应为 0；
2. 月内三次最小二乘拟合残差；
3. 月内二次拟合 RMSE，排除“任何平滑曲线都能低残差”的泛化解释；
4. 跨月与月内四阶差分窗口的位置；
5. 降雨、库水位及温度负对照。

高精度列使用 `1e-9` 的四阶差分和残差数值容差；五位小数列分别使用 `8e-5` 与 `1e-5`，只用于容纳文件舍入误差。这些容差是数值指纹诊断规则，不是统计显著性阈值，更不是预警阈值。

### 3.2 结果

| 列组 | 列 | 通过月份 | 月内四阶差分超限 | 月内三次最大残差 | 月内二次 RMSE 中位数 |
|---|---|---:|---:|---:|---:|
| 高精度 | MJ1、MJ3、ATU5、ATU1、GWT | 各 48/48 | 各 0/1269 | `3.98e-13`–`2.27e-12` | `0.016`–`0.042`，GWT `0.019` |
| 五位小数 | MJ9、ATU4、ATU3、ATU2 | 各 48/48 | 各 0/1269 | `6.05e-6`–`6.57e-6` | `0.007`–`0.016` |
| 负对照 | Rainfall、RWL、aveT、minT、maxT | 各 0/48 | 大量存在 | 不满足容差 | 不适用 |

全部目标列的四阶差分异常都只出现在跨自然月窗口；高精度 5 列各有 141 个跨月超限，其窗口终点日固定为每月的 2、3、4 日。该空间一致性、变量选择性和自然月边界定位共同构成 `strong_natural_month_piecewise_cubic`，明显强于“序列比较平滑”的一般描述。

这个结果可以支持：

> 公开日序列在数值上由逐自然月三次段构成或与这种构造在文件精度内等价。

它不能单独支持：

> 已经识别出具体的三次样条、三次插值、锚点位置、拟合软件或是否使用未来观测。

## 4. 模型边界与代数依赖

三个现有边界均位于自然月中间：

| 边界 | 日期 | 同月边界前行数 | 同月边界当日及之后行数 | 同一三次段跨边界 |
|---|---|---:|---:|---|
| 首个模型目标 | 2016-08-06 | 5 | 26 | 9/9 列 |
| fit → calibration | 2019-02-03 | 2 | 26 | 9/9 列 |
| calibration → test | 2019-09-18 | 17 | 13 | 9/9 列 |

为量化结构而非评价模型，使用边界一侧同月数据拟合三次式，再计算另一侧误差：

| 边界 | 方向 | 高精度列最大误差 | 五位小数列最大误差 |
|---|---|---:|---:|
| 2016-08-06 | 前 5 日外推后 26 日 | `6.88e-10` | `2.43e-2` |
| 2019-02-03 | 后 26 日回代前 2 日 | `1.36e-12` | `7.48e-6` |
| 2019-09-18 | 前 17 日外推后 13 日 | `7.96e-12` | `1.01e-4` |

2019-09-18 的 calibration/test 边界尤其关键：高精度列的 test 月内后半段几乎由 calibration 月内前半段代数确定，五位小数列误差也保持在文件舍入量级附近。它说明已发布序列层面在该边界两侧存在同月三次段的代数依赖；上游是否独立生成仍然未知。

本审查将这项结果固定标为：

```text
algebraic_dependence_diagnostic_not_forecast_evaluation
```

不能把它写成“用 17 天成功预测后 13 天”，也不能仅据此断言未来信息泄漏；是否泄漏取决于未公开的上游锚点及其在时间切分时是否可用。

## 5. 公开代码包能否解释该结构

本节来自 2026-07-28 对当时从 Figshare 取得的 11 个 notebook 与 `result.xlsx` 的人工外部发布包复核。下载文件位于临时目录，没有作为原始发布包副本提交，本仓库审计脚本也不复算本节扫描。

```text
public_package_review_role = manual_external_release_review
repository_reproducibility = not_available_for_public_package_scan
```

在该次人工复核中：

- 11 个 notebook 均只有代码单元，没有方法说明单元；
- 未发现 `polyfit`、polynomial、spline、cubic、resample 或按月生成日值的代码；
- 唯一明确的时间插值为先对输入序列执行线性插值，再做 `period=365` 的 STL 分解；
- notebook 读取未公开的本机 `D:\data.xlsx` 或 `E:\dataname.xlsx`，并只注释数据已经过“初步筛选”；
- `result.xlsx` 只有模型真值、预测、区间和多次运行结果，不含原始锚点或数据处理表；
- 公开包无法从原始 GNSS 端到端复现 `monitoring data.xlsx`。

因此最稳妥的来源结论是：

```text
monthly_structure_origin = upstream_of_reviewed_public_notebooks_or_unreleased
public_generation_code = not_found_in_manually_reviewed_release_files
end_to_end_raw_data_reproduction = unavailable
```

### 5.1 历史公开模型代码与当前仓库必须分开评价

公开 notebook 中可确认的历史问题包括：

- 在 80/20 切分前对全序列拟合 MinMaxScaler；
- 用全序列位移相关性构图；
- 训练期间逐 epoch 查看测试损失；
- 部分 ST-GNN 任务使用同期位移作为输入和目标，属于重建而非前瞻预测；
- MC-dropout 区间没有覆盖率校准，且标准差的 MinMax 尺度逆变换方向错误。

这些问题使公开包不能作为当前结果的可复现实验基准，但不能自动归因给当前仓库。当前 ConvLSTM 已做到：

- 标准化和增量尺度只由 fit 段估计；
- 训练目标为下一日相对最后输入日的位移增量；
- epoch 只由 fit 内部时间验证选择；
- IDW 只使用固定空间坐标，不以全序列相关性构图；
- calibration 与 test 日期、actual 和 persistence 行对齐误差均为 0。

所以当前判断是：

> 已识别的历史模型代码级泄漏未自动继承到当前 ConvLSTM；仍直接影响当前仓库的是同一份已处理日序列的上游血缘风险。

## 6. 点位映射与监测频率

### 6.1 可以确认

Wang 等（2025）支持当前空间分区：

```text
O1 = MJ9 / MJ1 / MJ3
O2 = ATU4 / ATU5 / ATU3
O3 = ATU2 / ATU1
```

Yang 等（2023）的 [Remote Sensing 论文](https://doi.org/10.3390/rs15204971)和 Yang 等（2024）的 [JRMGE 论文](https://doi.org/10.1016/j.jrmge.2023.09.030)中，GPS01–GPS12 与 FJ01–FJ12 的中断点、三分区和累计位移排序逐项一致，可视为同一监测网络的跨论文等价标签，但两文没有正式改名记录，这也不构成 MJ/ATU 的正式重命名表。

Yang 等（2023）第 2.2 节说明：2013 年安装 12 个 GNSS 点后每月采集 3–6 批，2016-08-06 起才可提供日数据；Yang 等（2024）进一步记录 2013-01-07 至 2016-08-07 约每 5–10 日一次、随后逐日。公开日序列却从 2016-07-01 开始，比前者所述日监测条件提前 36 天。两文所述单频原始数据每 20 秒上传并联合解算，也不能直接等同于每天一个独立统计观测。

### 6.2 仍不能确认

```text
MJ_or_ATU_to_GPS_or_FJ_mapping = unresolved
2016_07_01_to_2016_08_05_origin = unresolved
daily_value_aggregation_rule = unresolved
```

因此：

- 不用 GPS/FJ 坐标替换 MJ/ATU 坐标；
- 不把 1461 行连续日期写成 1461 个独立原始 GNSS 日观测；
- MJ 起始值非零而 ATU 起始值为零可提示参考基准或起算时刻不同，但不能在没有元数据时给出确定解释。

## 7. 对现有专家审查的影响

### 7.1 初始稳定段与 `V0`

此前“8/8 KMeans 候选不适合作为正式初始稳定段”和“严格 MVIF 8/8 不可辨识”的程序结论仍成立，因为它们准确描述当前输入序列及当前门禁。

但地质解释需要收窄：

- 不能把当前日差分速度当作未经处理的原始 GNSS 速度；
- 月内三次构造会直接影响速度、`σ`、阶跃边缘和拟合可辨识性；
- `no_stable_baseline_identified` 只表示**在当前已物化序列和当前方法中**没有识别出可接受基线；
- 它既不证明地质上不存在稳定段，也不证明原始观测中不存在可用于 `V0` 的稳定段。

正式 `V0` 仍为 `NA`，且数据血缘成为独立于 MVIF 失败之外的第二道门禁。

### 7.2 速度、`ΔV` 与改进切线角

这三项都由累计位移的一阶或二阶差分派生，最容易放大插值/拟合结构。当前计算可以用于检查代码、单位、暖启动和规则路径，但不能在血缘未解决时把逐日细节解释成独立地质加速信号。

特别是：

```text
velocity_role = derivative_of_materialized_series
delta_v_role = second_difference_of_materialized_series
tangent_angle_role = transformed_derivative_of_materialized_series
```

因此不再从当前日序列强制校准正式 `ΔV≈0` 容差、正式 blue 带或正式融合规则。

### 7.3 区间校准

此前 calibration-only 审查对当前模型残差的复算仍可重复，MJ/ATU 的空间异质性仍是当前模型—当前序列关系的事实。但它现在只能说明：

> 模型相对已物化日序列的概率误差与区间失配。

它不能说明：

> 相对独立原始 GNSS 日观测的测量不确定性或真实日尺度预测覆盖。

所以 `no_fixed_calibration_candidate_promoted` 保持不变，计划中的神经网络单变量消融暂停；在上游处理未知时，消融无法区分模型漏项、空间异质性与目标序列构造效应。

## 8. 数据闸门

### 8.1 当前阻断

```text
data_gate = blocked
```

阻断：

- `confirmatory_daily_forecast_claims`
- `new_neural_ablation_as_bias_explanation`
- `formal_v0_and_derivative_thresholds`
- `formal_four_indicator_fusion`
- `formal_warning_output`

不阻断：

- 只读数据血缘审计；
- 对已物化序列的工程一致性、日期、单位和缺失处理检查；
- 明确标为 `operational_draft_not_formal` 的代码链路演示；
- 获取原始材料、建立 raw-anchor-first 重算管线；
- 修改文档，收窄过度表述。

### 8.2 解除闸门所需最小材料

至少取得：

1. 原始 GNSS/GWT 观测日期、时刻与数值；
2. 日值聚合、QC、异常剔除、缺失填补和参考基准说明；
3. 若存在插值/平滑：算法、锚点、边界条件及是否使用月末/未来观测；
4. MJ/ATU 与现场监测点编号、坐标和分区的正式映射；
5. 2016-07-01 至 2016-08-05 数据来源；
6. 可从原始锚点端到端重建公开日表的脚本或处理记录。

取得材料后必须先按时间切分**原始锚点**，再在每个 fit/calibration 折内部拟合变换，禁止先用完整月份生成日曲线再切分。

### 8.3 如果无法取得原始材料

论文可以继续使用现有文件，但研究主张必须改为：

- “公开的已处理/已物化日尺度建模序列”；
- “对该序列的内部重建或外推”；
- “探索性运行规则演示”。

同时删除或避免：

- “1461 个独立原始日观测”；
- “确认性日尺度 GNSS 预测”；
- “日差分直接代表原始地质速度/加速度”；
- “已经证明未来信息泄漏”；
- “正式预警有效性已经验证”。

## 9. 可复现产物

审计入口：

```bash
uv run python code/convlstm/data_lineage_audit.py
```

回归测试：

```bash
uv run --with pytest pytest -q tests/test_convlstm_data_lineage.py
```

产物：

- [`数据血缘 manifest`](../figures/data_lineage/ootang_data_lineage_manifest.json)
- [`逐月多项式指纹`](../figures/data_lineage/ootang_monthly_polynomial_fingerprint.csv)
- [`逐列汇总`](../figures/data_lineage/ootang_column_fingerprint_summary.csv)
- [`模型边界审计`](../figures/data_lineage/ootang_split_boundary_audit.csv)
- [`跨边界代数依赖诊断`](../figures/data_lineage/ootang_split_cross_boundary_predictability.csv)
- [`预测日期对齐摘要`](../figures/data_lineage/ootang_prediction_alignment_summary.csv)

最终代码连续重跑两次，6 个产物的 SHA-256 逐项完全一致；manifest 固定审计代码和输入哈希，不写入 HEAD、时间戳或工作树状态。最终 manifest SHA-256 为 `df9606673fe2cba096369f384524ab24328f64d007131bb0b8db87ca9736e378`。

全部固定为：

```text
audit_scope = ootang_only
formal_warning_output = false
vajont_used = false
declared_test_rows_used_for_model_selection = 0
model_selection_usage_status = protocol_statement_not_verified_by_this_audit
```

## 10. 下一步

当前不运行计划中的 fit-only 神经网络单变量消融。下一步按以下顺序执行：

1. 将本数据闸门同步到行动计划、数据字典、结果与限制文档；
2. 向数据作者或导师索取第 8.2 节的最小原始材料；
3. 若取得原始锚点，先实现 raw-anchor-first 的按折重建与泄漏检查，再重跑稳定段、`V0`、ConvLSTM 和区间校准；
4. 若无法取得，按第 8.3 节收窄论文问题和结论，当前 operational 结果只保留为演示；
5. Vajont 继续等待用户明确授权，不作为绕过藕塘数据闸门的替代数据。
