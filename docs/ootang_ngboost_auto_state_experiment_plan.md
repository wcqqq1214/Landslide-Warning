# 藕塘自动未来状态标签与 NGBoost 五分类实验计划

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: plan
- Origin Date: 2026-08-30
- Verification Status: VERIFIED
- Version Label: labels_only_v1_failed_gate

## Experiment Overview

- **Title**: 藕塘未来 7 日多测点变形状态自动标注与 NGBoost 概率分类
- **Objective**: 在不人工逐时刻标注、不修改 ConvLSTM 主结构的前提下，生成严格按时间隔离的五级未来变形状态代理标签，并判断它是否足以支持导师要求的四指标 NGBoost 分类。
- **Hypothesis**: 由未来原始位移、速度和严格加速度共同生成的状态，比旧“未来 ConvLSTM 区间误差等级”更接近变形过程，也更可能在开发期形成可学习的五级与状态转移。
- **Type**: analysis / ETL（第一增量）；training（标签机械门禁通过后）

本计划只定义科研原型。自动标签代表数据驱动的未来变形状态，不等同于现场灾害真值；独立事件/专家记录仍是确认性灾害预警效能主张的必要条件。

## Setup

- **Language/Framework**: Python >= 3.10；NumPy、pandas、scikit-learn；第二阶段复用 NGBoost
- **Entry Command**: `uv run python main.py --stage ootang-ngboost-auto-state`；该 explicit-only stage 默认就是 labels-only
- **Working Directory**: `/Users/wcqqq1214/Project/Landslide-Warning`
- **Dependencies**: 只使用现有 `pyproject.toml` 依赖；不为变点检测新增库
- **Environment**: CPU 即可；标签诊断不训练 ConvLSTM

## Inputs

| Input | Path | Description |
| --- | --- | --- |
| 运行配置 | `config/ootang_ngboost_auto_state.v1.json` | 固定 H=7、fold 1 标签器拟合、有序五聚类、非阻断支持度提示与 6 个输出路径 |
| ConvLSTM 时间 OOF | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv` | 5 seeds × 3 folds × 287 日 × 8 点，共 34,440 行；五种子预先固定等权均值后为 6,888 个 OOF 测点—时刻 |
| 逐点运动学 | `data/ootang_kinematics_long.csv` | 原始位移、真实 `Δt` 速度、`ΔV` 与严格加速度；模型只用导师要求的加速度，`ΔV` 仅审计 |
| 空间块与 v4 基线 | `config/ootang_operational_run.v4.draft.json` | 复用固定的 O1/O2/O3 点位拓扑和 v4 基线，不复用其颜色作训练标签 |
| 滚动折边界 | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_runs.csv` | 读取 15 个折—种子的 fit/calibration/test 日期并核验同折一致，不手填新日期 |

## 固定的时间协议

### 2026-08-30 预诊断纠偏

最初计划把 `2016-08-06`—`2018-02-20` 的 pre-OOF 状态中心永久固定。只读复算发现该时期的极端变形尺度明显高于后续 OOF：固定边界应用到三个 OOF 折后，测点和 site 均只剩 green/blue，yellow/orange/red 全为 0，无法训练五分类。该结果否决的是“跨时期固定绝对 taxonomy”，不是自动标签方向。v1 只在训练折 fold 1 学习统一 taxonomy、在 fold 2/3 固定应用；但由于三个折均已参与开发期诊断，后两折都只能作探索性开发评价，不能再称独立验收或确认性留出。

1. 自动标签器的标准化、变点和五级中心只使用 OOF fold 1（`2018-02-21`—`2018-12-04`）内未来 7 日完整的锚点，即最后 7 日不参与。训练目标的类别定义可以由训练折结局学习，但随后固定，不能用 fold 2/3 重聚类或改边界。
2. fold 1 同时提供自动标签器拟合和首轮 NGBoost 训练标签；fold 2（`2018-12-05`—`2019-09-17`）用于开发期评价，fold 3（`2019-09-18`—`2020-06-30`）只作历史描述。二者均已暴露，不能重新称为独立验收或未见确认集。
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

1. 每个测点分别用 fold 1 标签锚点的中位数与 IQR 标准化三维 `z`；IQR 非正则该测点标签器失败。
2. 对每点的标准化三维序列做分段常值的惩罚最小化：段内平方误差 + `β × 变点数`，其中 `β=3 log(n)`、最短段长 `7` 日。实现使用现有依赖可完成的确定性精确动态规划，不增加参数搜索。
3. 每段严重度取三个标准化分量均值；汇总 8 点全部拟合段，按段长加权做一维 `K=5` KMeans，固定 `random_state=0`，中心从小到大对应 green、blue、yellow、orange、red，相邻中心中点为边界。
4. fold 2/3 不重新分段或聚类；当 `t+7` 的观测到齐后，机器计算当日严重度并用 fold 1 固定边界赋值。因此在线更新也不需要人工选择日期。

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

这一增量不训练 NGBoost、不调参，只输出类别支持和时间线。标签器的阻断门禁只保留：

- fold 1 与 fold 2 开发期的测点/site 五级均非空，五个中心严格递增；
- 测点与主任务 site 的各级未来位移增量、未来速度中位数均随颜色递增；
- 标准化、变点、聚类中心和边界不读取 fold 2/3；
- 每个输出标签只读取 `[t+1,t+7]`，无跨 fold 目标；

逐字节复现不由单次运行进程自证：提交前在外部连续运行两次并记录六项产物集合哈希；
后续训练 stage 必须同时读取科研 gate，而不能把 pipeline 的成功退出当作标签通过。

任一类别少于 20 个测点锚点/site 日时记录 `limited_support`，但不阻断科研 pilot；相应分类指标只作描述，不声称稳定类别性能。这一收窄响应项目“避免过严边界测试”的要求，也避免用任意样本数否决自动标签本身。

任何阻断项失败时只报告原因，不为了凑五级修改日期或边界。届时保留为失败 pilot，再评估预先单列的 challenger，而不是继续搜索 NGBoost 参数。

## 2026-08-30 labels-only v1 执行结果

正式入口 `uv run python main.py --stage ootang-ngboost-auto-state` 已在 5.1 秒内完成，
34,440 条五种子预测等权聚合为 6,888 条测点 OOF；其中 6,720 条具备完整 H=7
未来目标，剩余 168 条恰为 `3 folds × 8 stations × 7 terminal days`。连续两次直接运行的
六项产物集合 SHA-256 均为
`03659acdae63a1259af539c992e122cc9429918e1de5942e51b951c317fb4300`，时间隔离、
五种子等权和中心递增通过；外部确定性复跑另行通过。

机械结论为 `label_gate_passed=false`，因此没有训练 NGBoost：

- fold 1 测点五级为 `1518/476/173/59/14`，site 五级为 `104/62/34/64/16`；
- fold 2 测点五级为 `1730/397/80/33/0`，site 五级为 `148/96/17/12/7`，测点
  red 缺失；
- fold 1 的未来位移/速度中位数在 orange→red 回落，说明三分量等权严重度被未来加速度
  主导，不能把这五类解释为有序的未来变形强度；
- site 主任务的 fold 1 中位数也在 blue→yellow 回落：未来位移为
  `0.1434→0.1113`，未来速度为 `0.1499→0.1220`；
- 少于 20 个样本仍只作 `limited_support` 提示，不是新增阻断条件。

这次失败否决的是“三项未来结果等权 + 变点 + KMeans”这一标签器，不否决自动标签、
ConvLSTM 或导师要求的四项 NGBoost 输入。完整结果固定在
`figures/ngboost_auto_state_v1/`，不得通过覆盖产物来改写失败结论。

## 预先登记的单一 challenger

导师指定论文第五章采用位移区间、改进切线角、速度和变形速率增量四项指标，经多项式
逻辑回归输出五级概率；论文没有提供可直接迁移到藕塘的外部监督真值，也没有把严格
加速度单独当作五级真值。因而不能把论文颜色表直接冒充 NGBoost 的实测标签。

下一次只评估一个固定 challenger，不搜索权重或阈值：

1. 对每个测点，用 fold 1 分别建立未来 H=7 位移速率和未来正速度 Q90 的经验 CDF；
2. 两个 fold-1 百分位等权平均为未来变形严重度，fold 1 的 20/40/60/80% 分位固定为
   green/blue/yellow/orange/red 边界，并原样应用到 fold 2/3；
3. site 继续按固定 O1/O2/O3 先块内等权、再三块等权，并仅用 fold 1 固定自己的五级边界；
4. 严格加速度只从标签构造中移除，仍完整保留为时刻 `t` 的 NGBoost 输入，与置信区间、
   速度和改进切线角共同预测未来状态；
5. site 为综合预警主任务；测点标签用于八点诊断和 SHAP，不再要求每个单点在每折都独立
   出现五级。若 fold 2 开发期的 site 五级非空、未来位移/速度中位数有序且时间门禁通过，才进入
   NGBoost；否则记录为第二个失败实验并停止标签搜索。

## 第二增量：固定 NGBoost 与最小基线

只有第一增量通过后才执行。固定复用旧 pilot 的 NGBoost 参数，不做网格搜索；比较三个最小基线：

1. fold 1 的类别先验/多数类；
2. 当前自动状态持续到未来 7 日；
3. 使用完全相同 `X/Y` 的多项 Logistic Regression（对应导师指定论文的概率融合思路，但不声称复现论文系数）。

主要看状态转折时刻的 macro-F1 与 ordinal MAE；同时报告全时刻 log-loss、Brier score、每级召回和混淆矩阵。进入 fold 3 历史描述的最低开发门槛是：fold 2 转折 macro-F1 高于 persistence 与 Logistic、转折 ordinal MAE 至少不劣于两者，且 log-loss 优于类别先验。这里是已暴露数据上的项目 pilot 门禁，不是独立测试或通用工程阈值。

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

- **Primary metric**: 第一增量为五级支持与时间因果门禁；第二增量为 fold 2 开发期转折 macro-F1 和 ordinal MAE
- **Success threshold**: 按上述机械门禁；不以全时刻 accuracy 单独判断成功
- **Comparison**: 旧 interval-proxy 标签分布、类别先验、状态持续、多项 Logistic Regression、v4 透明规则基线

## 方法依据与证据边界

- [Killick, Fearnhead & Eckley, 2012](https://arxiv.org/abs/1101.1438)支持以惩罚代价做多个变点的精确优化；本项目的三维代价、`β=3 log(n)` 和最短 7 日是预先固定的项目实现，不是该论文直接给出的藕塘参数。
- [Truong, Oudre & Vayatis, 2020](https://arxiv.org/abs/1801.00718)将离线变点方法拆为代价函数、搜索方法和变点数约束，支持把这三部分分别记录和审计。
- [Cheng et al., 2020](https://arxiv.org/abs/1911.01325)讨论了“先分段、再聚类相似片段”的一般路线；本项目使用欧氏段严重度与一维 KMeans，不复现其 Wasserstein/谱聚类算法。
- [Deng et al., 2021](https://link.springer.com/article/10.1007/s10346-021-01676-8)展示了由实测速度和加速度生成运动标签、再训练机器学习分类器的滑坡实例，说明标签生成可以自动化；其 AE 传感器、8 类规则和随机切分不能直接迁移为本项目的五级时间验证。

这些文献只支持方法组件与自动化方向。未来 7 日定义、五级有序 KMeans、O1/O2/O3 综合和所有数值门禁均是本项目待实验验证的设计，不能写成文献已证明有效。
