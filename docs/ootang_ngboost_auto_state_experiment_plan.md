# 藕塘自动未来状态标签与 NGBoost 五分类实验计划

> 当前状态（2026-08-31）：本文件同时保留拟合前计划和已完成执行记录。当前主模型是
> 五分类 site NGBoost，SHAP 解释该主模型的期望顺序等级；v4 仅为透明规则基线，旧独立
> 位移增量回归 SHAP 执行链已退役并仅由 Git 历史追溯。原始发布表有 1,461 日；本文“全时刻”限定为
> 2018-02-21—2020-06-30 的 861 个模型可用 OOF 日期（6,888 条测点诊断）；v4 基线另有
> 2019-02-03—2020-06-30 的 514 日。Vajont 暂停且未用于本计划。当前解释协议见
> [`ngboost_shap_protocol.md`](ngboost_shap_protocol.md)，结果血缘见
> [`classifier manifest`](../figures/ngboost_auto_state_classifier_v1/manifest.json)。

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: plan
- Origin Date: 2026-08-30
- Verification Status: VERIFIED
- Version Label: ecdf_challenger_v2_verified

## Experiment Overview

- **Title**: 藕塘未来 7 日多测点变形状态自动标注与 NGBoost 概率分类
- **Objective**: 在不人工逐时刻标注、不修改 ConvLSTM 主结构的前提下，生成严格按时间隔离的五级未来变形状态代理标签，并判断它是否足以支持导师要求的四指标 NGBoost 分类。
- **Hypothesis**: 由未来位移率和未来正速度 Q90 生成的多点代理状态，比旧“未来 ConvLSTM 区间误差等级”更接近变形过程；严格加速度只作为当前时刻分类输入，不参与目标定义。
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
X_t=[PI_{i,t},v_{i,t},a_{i,t},\alpha_{i,t}]_{i=1}^{8}
\longrightarrow Y_{auto,t}.
\]

其中 `PI` 表示实测位移相对 P10–P90 **预测区间**的位置/偏离程度，不是参数置信区间。
共享测点模型 `X_(i,t) → y_auto(i,t)` 仅用于 8 点诊断面板；site 模型是综合预警主输出，
当前 SHAP 只解释 site 五分类 NGBoost。

## 第一增量：只实现标签诊断

这一增量不训练 NGBoost、不调参，只输出类别支持和时间线。标签器的阻断门禁只保留：

- fold 1 与 fold 2 开发期的测点/site 五级均非空，五个中心严格递增；
- 测点与主任务 site 的各级未来位移增量、未来速度中位数均随颜色递增；
- 标准化、变点、聚类中心和边界不读取 fold 2/3；
- 每个输出标签只读取 `[t+1,t+7]`，无跨 fold 目标。

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
4. 严格加速度只从标签构造中移除，仍完整保留为时刻 `t` 的 NGBoost 输入，与预测区间偏离、
   速度和改进切线角共同预测未来状态；
5. site 为综合预警主任务；测点标签用于八点诊断，不再要求每个单点在每折都独立
   出现五级。若 fold 2 开发期的 site 五级非空、未来位移/速度中位数有序且时间门禁通过，才进入
   NGBoost；否则记录为第二个失败实验并停止标签搜索。

## 2026-08-30 ECDF challenger v2 执行结果

正式入口 `uv run python main.py --stage ootang-ngboost-auto-state-ecdf` 在 1.8 秒内完成，
直接消费并校验 v1 的 6,888 条四指标/H=7 测点 OOF，不重复 34,440 行 seed 管线。固定边界为：

- station：`0.216071/0.400000/0.605357/0.802143`；
- site：`0.331825/0.421925/0.572480/0.710893`。

`label_gate_passed=true`：fold 1 station 五级为 `447/445/450/450/448`，site 五级各
56 日；fold 2 site 五级为 `138/66/39/21/16`。fold 1 与 fold 2 的 site 未来位移速率、
未来速度 Q90 中位数均随颜色严格递增；标签器最大输入日为 `2018-12-04`，fold 2 从
`2018-12-05` 开始，H=7 跨折目标为 0。六项产物外部连续复跑逐字节一致，集合 SHA-256 为
`d80a12776c955b69c2ab23e6fdd2b69177d7a3d0948cfe16bf48660916d2369e`。

非阻断限制保持透明：fold 2 site red 只有 16 日；仅 ATU2、ATU4 在 fold 2 单点层面
同时具备完整五级且结果有序，其余点存在缺级或局部顺序回落。因此后续 NGBoost 以 32 维
site 综合任务为主，测点共享模型与单点颜色只作诊断，不把它们写成稳定的逐点五级性能；
SHAP 仅解释 32 维五分类 site NGBoost 主模型。
所有折均已暴露，v2 通过只表示开发期代理标签可训练，不是现场灾害真值或确认性验证。

## 第二增量：固定 NGBoost 与最小基线

v2 challenger 已通过，可以执行。固定复用旧 pilot 的 NGBoost 参数：五类
`NGBClassifier`、深度 3 的树基学习器、500 estimators、learning rate 0.01、全样本/全列、
`random_state=0`；不做网格搜索、早停选择或概率后校准。site 主模型按固定测点顺序输入
`8 × [interval_z, velocity, strict_acceleration, tangent_angle] = 32` 维。共享测点模型使用
四指标加 station one-hot，仅用于八点诊断；当前 SHAP 解释 32 维 site 主模型。比较三个最小基线：

1. fold 1 的类别先验/多数类；
2. 最后一个已经成熟的 H=7 标签持续到当前，即以 `y_(t-7)` 预测 `y_t`，禁止把尚需
   `[t+1,t+7]` 才能知道的 `y_t` 当输入；
3. 使用完全相同 `X/Y` 的多项 Logistic Regression（对应导师指定论文的概率融合思路，但不声称复现论文系数）。

主要看状态转折时刻的 macro-F1 与 ordinal MAE；同时报告全时刻 log-loss、Brier score、
每级召回和混淆矩阵。训练前按固定标签复算发现，fold 2 仅有 11 个“相邻有效日等级发生
变化”的转折日。该样本量不足以承担硬性模型淘汰或 fold 3 准入门禁，因此撤销原定硬门槛，
把转折指标连同样本数作为小样本描述性结果。模型参数仍固定且不根据 fold 2/3 选择；fold 3
照常给出完整逐时刻预测和历史指标，但只标记为已暴露的 historical description，不能称为
独立测试或确认性证据。此修订发生在任何新模型拟合前，避免事后按性能放宽标准。

### 图件合同（训练前固定）

- **核心结论**：固定 NGBoost 能在收到时刻 `t` 的八点四指标后，为未来 H=7 状态输出
  全时刻五级概率/颜色，并用开发期 SHAP 显示模型最依赖的“测点 × 指标”，但不作因果解释。
- **证据链**：全时刻 site/八点颜色时间线回答“何时发出何种信号”；与三个基线的指标回答
  “模型是否比简单规则更有信息”；fold 2 permutation SHAP 回答“模型概率主要依赖哪些当前输入”。
- **图型与后端**：`quantitative grid`，Python/matplotlib 单一后端；不混用其他绘图后端。
- **导出与完整性**：两张必要图均保存 PNG、可编辑文本 SVG 和 PDF；PDF 字号不低于 5 pt，
  运行源码、文本和碰撞审计。图件使用 2018-02-21—2020-06-30 全部 861 个模型可用
  OOF site 日期和 6,888 个 station-date 预测，
  不为了排版删时刻；标签不可用的每折末 7 日仍显示模型信号并明确真值 unavailable。

## 2026-08-31 固定分类器执行结果

正式入口
`uv run python main.py --stage ootang-ngboost-auto-state-classifier --manifest figures/pipeline/ngboost_auto_state_classifier_v1_run.json`
在 29.8 秒内完成。模型严格只拟合 fold 1 的 280 个有效 site 日期，输出如下：

| fold 2 estimator | n | Accuracy | Macro-F1 | Ordinal MAE | Log-loss | Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NGBoost | 280 | 0.3536 | 0.2871 | 0.7429 | 3.3358 | 0.9960 |
| Multinomial Logistic | 280 | 0.2500 | 0.1964 | 0.8321 | 2.2732 | 1.0771 |
| Fold-1 prior | 280 | 0.4929 | 0.1321 | 0.9679 | 1.6094 | 0.8000 |
| Strict `y_(t-7)` persistence | 273 | 0.8022 | 0.6722 | 0.2234 | N/A | N/A |

NGBoost 的顺序误差和 macro-F1 优于 Logistic/prior，但没有超过严格因果 persistence，且
log-loss 明显劣于无信息先验，说明概率过度自信。本增量因此是可复现的负结果，不是“模型已
改善”。fold 2 仅 11 个相邻日转折，在共同 persistence-available mask 上 NGBoost 的
macro-F1/MAE 为 `0.0800/1.0909`，Logistic 为 `0.1071/0.9091`，persistence 为
`0.0571/1.0909`；继续保持 `small_support_descriptive_only`。fold 3 NGBoost macro-F1
`0.4635` 仅作已暴露历史描述。

site SHAP 使用 fold 1 的 12 个等距背景日期、fold 2 的 25 个等距解释日期和 65 次/样本
permutation 评估，解释输出为 `E[level|X]=Σk·P(k)`。前四项 mean absolute SHAP 是
ATU2 切线角 `0.5440`、ATU1 切线角 `0.4815`、ATU5 速度 `0.2094`、ATU2 速度
`0.1799`。由于自动标签本身由未来位移速率和速度构造，这些结果只能说明模型依赖，不能写成
独立的因果主控因素。

图件使用 Python/matplotlib 单一后端。静态预检为 16 pass、0 fail；PNG 仅作 300 dpi
预览，PDF/SVG 为矢量主件。最终 SHAP/时间线 PDF 的最小字号分别为 7/6 pt，碰撞审计均为
`0 fail, 0 warn`。预检的 TIFF、600 dpi、missing-data/uncertainty 四项 warning 不阻断：
本轮没有栅格投稿需求，标签 unavailable 行数已在 manifest 明示，且图中不声称随机重复不确定性。
SHAP masker 内部另出现 3 条 sklearn 矩阵数值 warning；最终概率、概率和与 800 个 SHAP
值均通过有限性检查，因此保留为运行提示，不增加会掩盖数值结果的兼容代码。

## 第三增量：单一 lag-7 状态记忆 challenger（拟合前固定）

固定分类器没有超过 persistence，下一步不调 NGBoost 参数，只回答一个明确问题：模型若显式
知道 issue 时刻已经成熟的上一状态，能否学习 persistence 之上的修正？

1. 输入为原 32 个 site 白名单特征，加唯一标量
   `lag7_state_level=Y_auto(t-7)`；不得加入 future outcome、severity、target end、当前
   `Y_auto(t)` 或其他标签字段。
2. `Y_auto(t-7)` 的目标窗是 `[t-6,t]`，只在收到 `U_t` 后成熟。按同折日期精确查找；
   必须验证来源行恰为七个日历日前且其 `target_end_date == t`，不能只依赖行位移。每折前
   7 日用 sentinel `-1`，不跨折填充、不增加 availability 列。
3. NGBoost 结构/500 estimators/learning rate 0.01/树深 3/random seed 0 全部不变；fold 1
   的 280 个 valid 日期拟合，fold 2 是唯一评价折；fold 3 只输出逐时预测，不计算指标。
4. 直接校验并消费已提交 v1 `site_predictions.csv`/manifest，不重训 no-memory 模型。
   common mask 是每折 lag-7 可得的 273 日；相邻转折仍为同折 `Y_t != Y_(t-1)`，再与
   common mask 取交集，fold 2 固定为 11 日。
5. fold 2 common 273 日对 memory/no-memory/persistence 报 accuracy、fixed-five
   macro-F1、ordinal MAE；仅两个 NGBoost 报 log-loss/Brier。另报 memory 的 fold 2 全
   280 日指标；不为 hard persistence 构造 epsilon/one-hot 概率分数，也不计算 fold 3 指标。
6. “改进候选”要求 fold 2 common-mask 上，memory 的 macro-F1 严格高于 no-memory 与
   persistence、ordinal MAE 严格低于二者、log-loss 与 Brier 都低于 no-memory，且
   log-loss `<1.6094`；否则拒绝。transition 11 日只描述，不设第二套门槛。

该增量只生成 site prediction、comparison metrics、feature importance、manifest 和单个模型；
不重复八点模型/SHAP/图件，不开始 horizon、消融、校准或超参数搜索。若失败，本轮停止模型
修补，把问题返回自动标签的可预测性或显式时序模型设计。

## 2026-08-31 lag-7 状态记忆 challenger 执行结果

正式运行使用代码提交 `b0dc37a`，完整管线耗时 4.0 秒且产物合同通过。模型输出全部 861
个 site 日期的概率与颜色；指标表共 27 行，仅评价 fold 2。fold 3 严格保持
`prediction_only`，不计算指标，也不得解释为现场验证或确认性测试。

| fold 2 common 273 estimator | Accuracy | Fixed-five macro-F1 | Ordinal MAE | Log-loss | Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Memory NGBoost | 0.336996 | 0.281322 | 0.761905 | 3.434553 | 1.028243 |
| Committed v1 NGBoost | 0.336996 | 0.281322 | 0.761905 | 3.421186 | 1.021504 |
| Strict lag-7 persistence | 0.802198 | 0.672215 | 0.223443 | N/A | N/A |

Memory 模型的 macro-F1 没有严格超过 v1 或 persistence，ordinal MAE 没有严格低于二者，
log-loss/Brier 没有低于 v1，且 log-loss 没有低于 `1.6094`。预注册的 7 项机械门槛因此全部
为 false，最终结论为 `rejected`。fold 2 的 11 个相邻状态转折只保留描述性结果，不建立
额外通过门槛。lag-7 特征的 NGBoost built-in importance 为 `0`、rank 33；该值仅表示此固定
模型没有利用该输入，不是因果主控因素结论。

该负结果没有修改 ConvLSTM 主结构或导师指定的总体框架。实验在此停止直接五分类 lag-feature
修补；下一步只允许先审查结构性 residual/transition 方法的目标、时间因果性和评价合同。
审查结果已由机器写入下节拟合前协议，不设置人工冻结或批准步骤；该方法尚未拟合或运行。

## 第四增量：lag-conditioned residual NGBoost（拟合前固定）

该单一 challenger 不再要求直接五分类器自己发现 persistence 结构，而让 NGBoost 分类预测
机器状态增量 `delta_state=Y_auto(t)-Y_auto(t-7)`，再机械映射回最终五级概率。

1. 仅在 fold 1 同折 lag 已成熟且当前目标 valid 的 273 日拟合。`Y_auto(t-7)` 必须满足来源
   日期恰为 `t-7` 且来源 `target_end_date==t`。fold-1-only 自动类别为
   `[-2,-1,0,+1]`，计数 `4/49/177/43`，固定编码为 `[0,1,2,3]`；不得根据 fold 2 扩类。
2. X 仍为八点 × 四项当前导师指标的 32 维白名单，加 `Y_auto(t-7)` 共 33 维。residual
   只在 lag 可得日运行，所以不使用 sentinel 或 availability 列；不得加入当前状态、未来结果、
   severity、颜色或 target end 字段。
3. 使用 `k_categorical(4)`，其余 NGBoost 500 estimators、learning rate 0.01、树深 3、
   seed 0 均与 v1 相同，不搜索参数、不重采样、不校准。
4. 给定 lag 等级 `l`，将满足 `l+delta` 不在 `[0,4]` 的类别概率置零，对可行概率重新归一，
   再按 `k=l+delta` 汇总为五级概率。禁止 clamp、epsilon、事后混合和看 fold 2 后补类。
   `delta=0` 总是可行，因此归一化分母必须大于零。
5. 每折前 7 日没有成熟 lag，自动逐行复制已提交 v1 NGBoost 的五级概率，标记
   `v1_fallback`；它只保证全部 861 时刻有输出，不进入 main comparison。其余日期标记
   `residual_ngboost`，包括当前 truth unavailable 但 lag 已成熟的每折末 7 日。
6. fold 2 common 273 是唯一评价集合：residual/v1/persistence 报 accuracy、fixed-five
   macro-F1、ordinal MAE，仅 residual/v1 报 log-loss/Brier。fold 2 的 delta support 与 fold 1
   相同，但 `delta=0` 比例由 `64.8%` 升到 `80.2%`；另有 4 个 `(lag=3, delta=-2)`
   条件组合未出现在 fold 1。二者只作为已暴露漂移风险记录，不删除样本或改变协议。
7. 仅当 residual macro-F1 严格高于 persistence、ordinal MAE 严格低于 persistence、
   log-loss 与 Brier 都低于 v1，且 log-loss `<1.6094` 时接受；否则 `rejected` 并停止本轮
   NGBoost 修补。fold 3 只输出信号，不计算指标或参与选择。

输出限制为 site predictions、fold-2 comparison metrics、fold-1 delta class definition、manifest
和一个模型；不增加第二个 residual 变体、测点模型、SHAP、图件、调参或概率校准。该协议由机器
记录，不引入人工日期、人工标签、人工冻结或批准步骤。

## 2026-08-31 residual challenger 执行结果

正式运行使用源码提交 `1e977e4`。完整管线耗时 4.0 秒（stage 3.9 秒）并通过产物合同；输出
861 条逐日预测，其中 840 条为 residual 预测，21 条为逐行 v1 fallback。fallback 每折恰好
7 条，其五级概率与已提交 v1 逐值一致。指标表只有 13 行，严格限于 fold 2 的 273 日共同
集合；fold 3 只输出预测，不计算指标或作评价主张。

| fold 2 common 273 estimator | Accuracy | Fixed-five macro-F1 | Ordinal MAE | Log-loss | Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Residual NGBoost | 0.355311 | 0.330687 | 0.706960 | 3.753783 | 1.023779 |
| Committed v1 NGBoost | 0.336996 | 0.281322 | 0.761905 | 3.421186 | 1.021504 |
| Strict lag-7 persistence | 0.802198 | 0.672215 | 0.223443 | N/A | N/A |

Residual 结构相对 v1 的 accuracy、macro-F1 和 ordinal MAE 有小幅改善，但仍远落后严格
persistence；log-loss 与 Brier 也比 v1 更差。五项预注册检查全部为 false，最终结论为
`rejected`。因此按协议停止本轮 NGBoost 修补，不调参、不建立第二 residual 变体。

该实验没有修改 ConvLSTM 主结构或导师指定的总体框架，也不支持现场有效性、正式预警或
fold 3 评价主张。下一步只做只读方法核对：审计当前自动标签是否真正落实导师意见及参考论文
中的加速度等级与自动未来标签是否保持角色分离；在核对完成前不修改标签或模型。

## 2026-08-31 方法对齐审计结论

指定 Word 第五章没有严格加速度阈值表。表 5-4 是速度 `V0/5V0/10V0` 五级，第四项为
`ΔV`；当前严格加速度五级的 fit-only `A0=max(1.5A,A+2σa)` 是导师“同阈值结构”要求下
的项目操作化，不是论文原阈值。

分类合同保持 X/Y 分离：`y` 是 H=7 未来位移率与未来正速度 Q90 的自动多点代理状态；严格
加速度与 ConvLSTM 区间、速度、改进切线角是当前 `X_t`。同刻四指标融合色不能改作标签，
否则形成 `y=F(X_t)` 循环。参考论文采用同刻 MLR 融合，但未给训练标签或拟合系数；本项目
自动未来状态是可复现的替代口径，不声称复现论文。SHAP 解释当前五分类 site NGBoost，
不是 ConvLSTM，也不是旧独立位移增量回归器。

导师要求的速度、严格加速度、ConvLSTM 八点全时间、四指标、NGBoost 五分类、逐时五色与
多点综合已基本实现；分类器因未胜 persistence 而保持 rejected exploratory。旧 classifier
的 fold 3 历史指标不重跑、不级联改写；后续 fold 3 只生成预测。

## Expected Outputs

### v1 失败诊断（已冻结）

| Output | Path | Format | Success Criterion |
| --- | --- | --- | --- |
| 测点自动标签 | `figures/ngboost_auto_state_v1/station_auto_labels.csv` | CSV | 8 点逐时刻；每行记录未来窗、三项结果量、严重度、等级和边界版本 |
| site 自动标签 | `figures/ngboost_auto_state_v1/site_auto_labels.csv` | CSV | 每日 O1/O2/O3 严重度与 site 五级完整可追溯 |
| 分段与五级中心 | `figures/ngboost_auto_state_v1/label_state_definition.csv` | CSV | 记录拟合段、段长、中心、边界与严格递增检查 |
| 机械门禁摘要 | `figures/ngboost_auto_state_v1/label_gate.json` | JSON | 每项门禁显式 pass/fail；失败不生成 NGBoost 模型 |
| 标签时间线 | `figures/ngboost_auto_state_v1/auto_state_timeline.png` | PNG | 8 点与 site 全时间轴可视检查，不作为人工改标签入口 |
| 运行清单 | `figures/ngboost_auto_state_v1/manifest.json` | JSON | 输入、配置、源码、输出哈希与最大拟合日期齐全 |

### v2 ECDF challenger（当前通过版本）

| Output | Path | Format | Success Criterion |
| --- | --- | --- | --- |
| 测点自动标签 | `figures/ngboost_auto_state_ecdf_v2/station_auto_labels.csv` | CSV | 四指标、ECDF 分量、未来结果、五级和边界版本逐行可追溯 |
| site 自动标签 | `figures/ngboost_auto_state_ecdf_v2/site_auto_labels.csv` | CSV | 每日 O1/O2/O3 两层等权严重度与五级完整 |
| ECDF/边界定义 | `figures/ngboost_auto_state_ecdf_v2/label_state_definition.csv` | CSV | 保存每点两分量全部 unique knots/count/CDF 与 station/site 四条边界 |
| 机械门禁摘要 | `figures/ngboost_auto_state_ecdf_v2/label_gate.json` | JSON | `label_gate_passed=true`，并保留逐点与小样本 advisory |
| 标签时间线 | `figures/ngboost_auto_state_ecdf_v2/auto_state_timeline.png` | PNG | 八点和 site 全 OOF 时间线，无人工编辑入口 |
| 运行清单 | `figures/ngboost_auto_state_ecdf_v2/manifest.json` | JSON | v1 来源、配置、代码和五项非 manifest 产物哈希一致 |

### 固定 NGBoost 分类器 v1（当前负结果）

| Output | Path | Format | Success Criterion |
| --- | --- | --- | --- |
| site 全时刻概率/颜色 | `figures/ngboost_auto_state_classifier_v1/site_predictions.csv` | CSV | 四估计器 × 861 个模型可用 OOF 日期（2018-02-21—2020-06-30）；truth unavailable 与 prediction available 分离 |
| 八点诊断概率/颜色 | `figures/ngboost_auto_state_classifier_v1/station_predictions.csv` | CSV | 8 点 × 861 日 = 6,888 行，明确 diagnostic-only |
| 指标与混淆矩阵 | `figures/ngboost_auto_state_classifier_v1/{metrics,confusion_matrix}.csv` | CSV | 三折角色、全时刻/转折子集、n 与小样本状态明确 |
| site SHAP | `figures/ngboost_auto_state_classifier_v1/site_shap_{values,importance}.csv` | CSV | 800 行期望等级 permutation SHAP，可追溯 station/indicator/date |
| 全时刻图与 SHAP 图 | `figures/ngboost_auto_state_classifier_v1/{warning_timeline,site_shap_summary}.{png,pdf,svg}` | figure | 全 8 点、全部 861 个模型可用 OOF 日期；矢量文本与碰撞审计通过 |
| 模型与运行清单 | `models/ootang_ngboost_auto_state_*_v1.pkl`; `figures/ngboost_auto_state_classifier_v1/manifest.json` | pickle/JSON | 固定模型、输入/输出哈希和限制完整 |

## Monitoring Configuration

- **Timeout**: 标签诊断 5 分钟；NGBoost 第二增量 30 分钟
- **Monitor files**: `figures/ngboost_auto_state_ecdf_v2/label_gate.json`
- **Experiment type override**: analysis（第一增量）
- **Metric file**: `figures/ngboost_auto_state_ecdf_v2/label_gate.json`
- **Metric key**: `label_gate_passed`

## Analysis Plan

- **Primary metric**: 第一增量为五级支持与时间因果门禁；第二增量为 fold 2 开发期转折 macro-F1 和 ordinal MAE
- **Success threshold**: 按上述机械门禁；不以全时刻 accuracy 单独判断成功
- **Comparison**: 类别先验、状态持续、多项 Logistic Regression、v4 透明规则基线；已退役的 interval-proxy 仅可从 Git 基线 `b13eb8b` 作历史核对

## 方法依据与证据边界

- [Killick, Fearnhead & Eckley, 2012](https://arxiv.org/abs/1101.1438)支持以惩罚代价做多个变点的精确优化；本项目的三维代价、`β=3 log(n)` 和最短 7 日是预先固定的项目实现，不是该论文直接给出的藕塘参数。
- [Truong, Oudre & Vayatis, 2020](https://arxiv.org/abs/1801.00718)将离线变点方法拆为代价函数、搜索方法和变点数约束，支持把这三部分分别记录和审计。
- [Cheng et al., 2020](https://arxiv.org/abs/1911.01325)讨论了“先分段、再聚类相似片段”的一般路线；本项目使用欧氏段严重度与一维 KMeans，不复现其 Wasserstein/谱聚类算法。
- [Deng et al., 2021](https://link.springer.com/article/10.1007/s10346-021-01676-8)展示了由实测速度和加速度生成运动标签、再训练机器学习分类器的滑坡实例，说明标签生成可以自动化；其 AE 传感器、8 类规则和随机切分不能直接迁移为本项目的五级时间验证。

这些文献只支持方法组件与自动化方向。未来 7 日定义、v1 五级有序 KMeans、v2 ECDF
百分位严重度、O1/O2/O3 综合和所有数值门禁均是本项目设计，不能写成文献已证明有效。
