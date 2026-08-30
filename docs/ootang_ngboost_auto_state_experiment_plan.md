# 藕塘自动未来状态标签与 NGBoost 五分类实验计划

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: plan
- Origin Date: 2026-08-30
- Verification Status: UNVERIFIED
- Version Label: code_plan_v1

## Experiment Overview

- **Title**: 藕塘未来 7 日多测点变形状态自动标注与 NGBoost 概率分类
- **Objective**: 在不人工逐时刻标注、不修改 ConvLSTM 主结构的前提下，生成严格按时间隔离的五级未来变形状态代理标签，并判断它是否足以支持导师要求的四指标 NGBoost 分类。
- **Hypothesis**: 由未来原始位移、速度和严格加速度共同生成的状态，比旧“未来 ConvLSTM 区间误差等级”更接近变形过程，也更可能在开发期形成可学习的五级与状态转移。
- **Type**: analysis / ETL（第一增量）；training（标签机械门禁通过后）

本计划只定义科研原型。自动标签代表数据驱动的未来变形状态，不等同于现场灾害真值；独立事件/专家记录仍是确认性灾害预警效能主张的必要条件。

## Setup

- **Language/Framework**: Python >= 3.10；NumPy、pandas、scikit-learn；第二阶段复用 NGBoost
- **Entry Command**: 计划新增 `uv run python main.py --stage ootang-ngboost-auto-state --labels-only`；当前尚未实现
- **Working Directory**: `/Users/wcqqq1214/Project/Landslide-Warning`
- **Dependencies**: 只使用现有 `pyproject.toml` 依赖；不为变点检测新增库
- **Environment**: CPU 即可；标签诊断不训练 ConvLSTM

## Inputs

| Input | Path | Description |
| --- | --- | --- |
| ConvLSTM 时间 OOF | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv` | 5 seeds × 3 folds × 287 日 × 8 点，共 34,440 行；五种子预先固定等权均值后为 6,888 个 OOF 测点—时刻 |
| 逐点运动学 | `data/ootang_kinematics_long.csv` | 原始位移、真实 `Δt` 速度、`ΔV` 与严格加速度；模型只用导师要求的加速度，`ΔV` 仅审计 |
| 空间块与 v4 基线 | `config/ootang_operational_run.v4.draft.json` | 复用固定的 O1/O2/O3 点位拓扑和 v4 基线，不复用其颜色作训练标签 |
| 滚动折边界 | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_runs.csv` | 读取 15 个折—种子的 fit/calibration/test 日期并核验同折一致，不手填新日期 |

## 固定的时间协议

1. 自动标签器的标准化、变点和五级中心只使用首个 OOF 日之前的历史：`2016-08-06` 至 `2018-02-20`。因目标窗口为未来 7 日，最后一个可用于拟合标签器的锚点为 `2018-02-13`。
2. OOF fold 1（`2018-02-21`—`2018-12-04`）用于首轮 NGBoost 拟合；fold 2（`2018-12-05`—`2019-09-17`）只用于方法验收。方案锁定后以 fold 1+2 重训，fold 3（`2019-09-18`—`2020-06-30`）只评价一次。
3. 标签器、NGBoost 和输出均按整天组织全部 8 点；禁止同一天不同测点跨训练/评价集合。
4. 每个 fold 末 7 日不生成目标，禁止跨 fold 借未来观测。
5. 输入 `interval_z_t` 需要当天实测 `U_t`，因此系统语义固定为“收到时刻 `t` 的观测后，预测未来 7 日状态”，不能写成观测 `U_t` 前已经发报。
6. 五种子固定等权聚合，不按 fold 3 结果挑 seed。

## 四指标 OOF 表

每个测点—时刻只保留导师要求的四项连续输入：

```text
interval_z_t
velocity_mm_per_day_t
acceleration_mm_per_day_squared_t
tangent_angle_degree_t = atan(velocity_t / V0_i,fold)
```

- `interval_z` 复用现有 `classify_observed_interval_states` 对五种子等权聚合后的 calibrated P10/P90 与 P50 计算。
- `V0_i,fold` 用现有 KMeans 稳定段比较器按每折 fit 截止日自动重算；只作为改进切线角的可复现比较基准。只读诊断已覆盖 3 折 × 8/8 点，但它不是导师指定论文的正式 `V0` 现场验证结论。
- 当前藕塘日序列上 `delta_v` 与严格加速度数值相等，但量纲语义不同；不得把二者同时作为模型输入。

## 自动未来状态标签

固定预测窗 `H=7` 日。对测点 `i`、锚点 `t`，只用未来原始观测构造结果向量：

\[
z_{i,t}=\left[
\frac{(U_{i,t+7}-U_{i,t})_+}{7},
Q_{0.9}\{(v_{i,t+r})_+\}_{r=1}^{7},
Q_{0.9}\{(a_{i,t+r})_+\}_{r=1}^{7}
\right].
\]

其中正部只表达本数据位移正方向上的加速变形。标签器拟合过程固定为：

1. 每个测点分别用标签器拟合期的中位数与 IQR 标准化三维 `z`；IQR 非正则该测点标签器失败。
2. 对每点的标准化三维序列做分段常值的惩罚最小化：段内平方误差 + `β × 变点数`，其中 `β=3 log(n)`、最短段长 `7` 日。实现使用现有依赖可完成的确定性精确动态规划，不增加参数搜索。
3. 每段严重度取三个标准化分量均值；汇总 8 点全部拟合段，按段长加权做一维 `K=5` KMeans，固定 `random_state=0`，中心从小到大对应 green、blue、yellow、orange、red，相邻中心中点为边界。
4. OOF 日期不重新分段或聚类；当 `t+7` 的观测到齐后，机器计算当日严重度并用已经固定的边界赋值。因此在线更新也不需要人工选择日期。

测点标签记为 `y_auto(i,t)`。滑坡体标签不取单点最大值：先按固定拓扑计算各空间块内测点严重度等权均值：

```text
O1 = [MJ9, MJ1, MJ3]
O2 = [ATU4, ATU5, ATU3]
O3 = [ATU2, ATU1]
```

再对标签器拟合期的三维 `[B_O1, B_O2, B_O3]` 使用相同的“变点 → 段严重度 → 有序五聚类”流程，得到固定 site 边界与 `Y_auto(t)`。后续 NGBoost 的主任务为：

\[
X_t=[CI_{i,t},v_{i,t},a_{i,t},\alpha_{i,t}]_{i=1}^{8}
\longrightarrow Y_{auto,t}.
\]

共享测点模型 `X_(i,t) → y_auto(i,t)` 用于 8 点面板与测点级 SHAP；site 模型是综合预警主输出。

## 第一增量：只实现标签诊断

这一增量不训练 NGBoost、不调参，只输出类别支持和时间线。标签器必须同时满足：

- 拟合期五级均非空，五个中心严格递增；
- 各级未来位移增量与未来速度的中位数整体随颜色递增；
- site 的 yellow/orange/red 各不少于 20 日，且各自不只来自一个孤立短片段；
- 标准化、变点、聚类中心和边界的最大输入日期早于首个 OOF 日期；
- 每个输出标签只读取 `[t+1,t+7]`，无跨 fold 目标；
- 连续运行两次输出逐字节一致。

任何一项失败时只报告原因，不为了凑五级查看 fold 3 后修改窗口、日期或边界。届时保留为失败 pilot，再评估预先单列的 challenger，而不是继续搜索 NGBoost 参数。

## 第二增量：固定 NGBoost 与最小基线

只有第一增量通过后才执行。固定复用旧 pilot 的 NGBoost 参数，不做网格搜索；比较三个最小基线：

1. fold 1 的类别先验/多数类；
2. 当前自动状态持续到未来 7 日；
3. 使用完全相同 `X/Y` 的多项 Logistic Regression（对应导师指定论文的概率融合思路，但不声称复现论文系数）。

主要看状态转折时刻的 macro-F1 与 ordinal MAE；同时报告全时刻 log-loss、Brier score、每级召回和混淆矩阵。NGBoost 进入最终历史留出评价的最低条件是：fold 2 转折 macro-F1 高于 persistence 与 Logistic、转折 ordinal MAE 至少不劣于两者，且 log-loss 优于类别先验。这里是项目 pilot 门禁，不是通用工程阈值。

## Expected Outputs

| Output | Path | Format | Success Criterion |
| --- | --- | --- | --- |
| 测点自动标签 | `figures/ngboost_auto_state_v1/station_auto_labels.csv` | CSV | 8 点逐时刻；每行记录未来窗、三项结果量、严重度、等级和边界版本 |
| site 自动标签 | `figures/ngboost_auto_state_v1/site_auto_labels.csv` | CSV | 每日 O1/O2/O3 严重度与 site 五级完整可追溯 |
| 分段与五级中心 | `figures/ngboost_auto_state_v1/label_state_definition.csv` | CSV | 记录拟合段、段长、中心、边界与严格递增检查 |
| 机械门禁摘要 | `figures/ngboost_auto_state_v1/label_gate.json` | JSON | 每项门禁显式 pass/fail；失败不生成 NGBoost 模型 |
| 标签时间线 | `figures/ngboost_auto_state_v1/auto_state_timeline.png` | PNG | 8 点与 site 全时间轴可视检查，不作为人工改标签入口 |
| 运行清单 | `figures/ngboost_auto_state_v1/manifest.json` | JSON | 输入、配置、源码、输出哈希与最大拟合日期齐全 |

## Monitoring Configuration

- **Timeout**: 标签诊断 5 分钟；NGBoost 第二增量 30 分钟
- **Monitor files**: `figures/ngboost_auto_state_v1/label_gate.json`
- **Experiment type override**: analysis（第一增量）
- **Metric file**: `figures/ngboost_auto_state_v1/label_gate.json`
- **Metric key**: `label_gate_passed`

## Analysis Plan

- **Primary metric**: 第一增量为五级支持与时间因果门禁；第二增量为 fold 2 转折 macro-F1 和 ordinal MAE
- **Success threshold**: 按上述机械门禁；不以全时刻 accuracy 单独判断成功
- **Comparison**: 旧 interval-proxy 标签分布、类别先验、状态持续、多项 Logistic Regression、v4 透明规则基线

## 方法依据与证据边界

- [Killick, Fearnhead & Eckley, 2012](https://arxiv.org/abs/1101.1438)支持以惩罚代价做多个变点的精确优化；本项目的三维代价、`β=3 log(n)` 和最短 7 日是预先固定的项目实现，不是该论文直接给出的藕塘参数。
- [Truong, Oudre & Vayatis, 2020](https://arxiv.org/abs/1801.00718)将离线变点方法拆为代价函数、搜索方法和变点数约束，支持把这三部分分别记录和审计。
- [Cheng et al., 2020](https://arxiv.org/abs/1911.01325)讨论了“先分段、再聚类相似片段”的一般路线；本项目使用欧氏段严重度与一维 KMeans，不复现其 Wasserstein/谱聚类算法。
- [Deng et al., 2021](https://link.springer.com/article/10.1007/s10346-021-01676-8)展示了由实测速度和加速度生成运动标签、再训练机器学习分类器的滑坡实例，说明标签生成可以自动化；其 AE 传感器、8 类规则和随机切分不能直接迁移为本项目的五级时间验证。

这些文献只支持方法组件与自动化方向。未来 7 日定义、五级有序 KMeans、O1/O2/O3 综合和所有数值门禁均是本项目待实验验证的设计，不能写成文献已证明有效。
