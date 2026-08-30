# 导师修改意见与藕塘执行计划

> 当前版本：2026-08-31
>
> 本文件保留固定路径，因为 `config/ootang_warning_protocol.v1.draft.json` 与
> `config/ootang_warning_protocol.v2.draft.json` 引用它。最新研究约束以项目根目录
> `AGENTS.md` 为最高优先级，实际运行以版本化配置、源码、manifest 和结果文件为准。
>
> 本页不是导师报告。此前 939 行执行历史可从 Git 基线 `6f499cd` 查看。

## 1. 当前方法裁决

研究主线固定为：

```text
逐点运动学与多源监测
  → ConvLSTM 全测点概率位移预测
  → 预测区间与覆盖评价
  → 区间、速度、严格加速度、改进切线角
  → H=7 自动未来状态标签
  → 五分类 site NGBoost
  → NGBoost SHAP
  → 测点级与滑坡体级逐时预警
```

按用户对导师意见的最新解释，SHAP 解释五分类 site NGBoost，角色与毕业论文中的
LightGBM+SHAP 一致。ConvLSTM 负责 P10/P50/P90 和预测区间评价；不把直接
ConvLSTM-SHAP 列为当前导师要求。方法名称统一为“ConvLSTM–NGBoost–SHAP”。

规则融合 v4 只作透明基线，不能替代 NGBoost。当前没有传感器接口，允许历史数据自动试跑；
方法不依赖 Figshare API、人工冻结或人工逐时判级。

## 2. 十项导师意见与当前状态

| ID | 导师意见 | 当前执行口径 | 状态 |
| --- | --- | --- | --- |
| R1 | 将 `U_t-U_{t-30}` 改为逐点速度 | 使用真实相邻时间差计算 `v_i`，旧 30 日位移差不再称速度 | 完成 |
| R2 | ConvLSTM 展示全部测点、训练段与预测段 | 8 点均输出 fit/calibration/test、P10/P50/P90 和边界 | 完成 |
| R3 | 用区间覆盖和 SHAP 确定主控因素 | PICP 评价 ConvLSTM 区间；NGBoost SHAP 描述候选关键依赖，不作因果证明 | 完成当前解释 |
| R4 | 区间、速度、加速度、改进切线角全部进入预警 | 四项并列进入 8 点×4 指标的 32 维 site 输入，不设主副指标 | 完成 |
| R5 | 使用 NGBoost 分类构建预警模型 | 五分类 NGBoost 已训练并与三类简单基线比较；负结果保留 | 完成实现，效果为负 |
| R6 | 使用 Vajont `10d` 数据增加案例 | 当前不读取、不适配、不训练；须用户再次明确授权 | 延后 |
| R7 | 参考论文设置加速度预警等级 | 使用严格逐点加速度与 A0 五级项目操作化；不冒充论文原阈值 | 原型完成，待独立验证 |
| R8 | 显示每个时刻的红橙黄蓝信号 | 增加 green 正常级；861 个模型可用日均有五级概率与颜色 | 完成限定时间域 |
| R9 | 讲清方法并展示预测、SHAP、逐时预警 | 方法结果底稿和机器证据齐全；当前按用户要求暂不写导师报告 | 内部材料完成 |
| R10 | 多测点综合评判而非单线 | site NGBoost 同时使用 8 点；v4 另给 O1/O2/O3 空间双轴诊断 | 完成 |

## 3. 逐点运动学定义

每个测点按真实时间戳计算：

\[
v_i=\frac{U_i-U_{i-1}}{t_i-t_{i-1}},\qquad
a_i=\frac{v_i-v_{i-1}}{t_i-t_{i-1}}.
\]

- 位移、速度、加速度单位分别明确为 `mm`、`mm/day`、`mm/day²`。
- 不规则采样必须使用真实 `Δt`，不能硬编码为 1、10 或 30 天。
- 首个速度和前两个加速度为 warmup；无效或非正时间差显式标记，不用未来数据补齐。
- 原始 `ΔV_i=v_i-v_{i-1}` 可用于审计，但不替代严格加速度输入。

## 4. ConvLSTM 与预测区间

- 保留导师指定的 ConvLSTM 主体，使用 8 个藕塘测点及 7 通道输入。
- 输出 P10、P50、P90；P10–P90 称 80% **预测区间**，不称参数置信区间。
- 全测点图同时展示训练、校准和预测/评价段，不用训练拟合冒充样本外预测。
- 区间至少联合报告 PICP、平均宽度、目标覆盖偏差和 interval score。
- 当前三折 PICP 为 `0.387/0.956/0.754`，说明区间校准跨时期不稳定。

## 5. 四项指标与 A0 项目操作化

当前时刻 `X_t` 仅使用当时可见信息，并保留四项连续指标：

1. 实测位移相对 ConvLSTM 预测区间的位置或偏离程度 `interval_z`；
2. 逐点速度 `v_i`；
3. 严格逐点加速度 `a_i`；
4. 逐点速度相对稳定期比较量形成的改进切线角。

指定 Word 论文原始第四项是变形速率增量，并未提供严格逐点加速度阈值表。依据导师最新
意见，本项目仅在每个测点的 fit-only 稳定段计算：

\[
A=\operatorname{mean}(a),\quad
\sigma_a=\operatorname{std}(a),\quad
A_0=\max(1.5A,A+2\sigma_a).
\]

五级操作化为：

```text
green   a < A0 - sigma_a
blue    A0 - sigma_a <= a <= A0 + sigma_a
yellow  A0 + sigma_a < a < 5 A0
orange  5 A0 <= a < 10 A0
red     a >= 10 A0
```

其中 `A0±sigma_a` 是把论文定性“约等于”转换为可执行边界的项目约定，`1×/5×/10×`
沿用论文速度等级的相对结构。它不是论文直接给出的加速度标准，也未通过现场独立验证；
不得依据评价段回调阈值。

## 6. 自动 H=7 标签与 NGBoost

当前输入为 `X_t`，目标为从 `t` 后未来 7 日原始观测生成的 `Y_auto(t)`：

- 每点计算未来 7 日位移率和未来正速度 Q90；
- 只用 fold 1 建立两者的经验 CDF，并等权形成严重度；
- 只用 fold 1 的 q20/q40/q60/q80 固定五级边界；
- 先在 O1/O2/O3 内汇总，再对三分区等权综合形成 site 标签；
- fold 2/3 不重估标签边界，每折末 7 日不借用跨折未来观测。

禁止用同一时刻四指标规则颜色直接给 NGBoost 当标签，否则会形成 `Y=F(X_t)` 的循环学习。
自动标签只表示未来变形代理状态，不是真实灾害等级。

site NGBoost 输入按固定顺序拼接 `8×4=32` 个特征，输出 green、blue、yellow、orange、red
五级概率。比较对象包括 fold-1 类别先验、多项 Logistic 回归和严格因果的 lag-7 状态持续
基线；指标包括 accuracy、固定五类 macro-F1、ordinal MAE、log-loss 和 Brier。

当前 fold-2 NGBoost 为 `accuracy=0.3536`、`macro-F1=0.2871`、`ordinal MAE=0.7429`、
`log-loss=3.3358`、`Brier=0.9960`。共同 273 日的 lag-7 persistence 达到
`0.8022/0.6722/0.2234`，因此 NGBoost 未证明改善；memory 与 residual 也已拒绝。
不得在已暴露折上继续调参或修改标签来掩盖该负结果。

## 7. SHAP、多点和时间口径

NGBoost SHAP 的解释目标是 `Σ_k kP(y=k|X_t)`。背景样本为 fold 1 的 12 个等距日期，
解释样本为 fold 2 的 25 个等距日期；结果仅说明指定模型对“测点×指标”的依赖。
论文中可称“模型支持的候选主控因素”，但必须结合物理机制和独立证据，不能直接写成因果结论。

| 输出范围 | 数量 | 说明 |
| --- | ---: | --- |
| 藕塘原始物化历史 | 1,461 日 | 完整输入序列 |
| NGBoost 模型可用时间 | 861 日 | 3,444 条四估计器 site 输出；6,888 条八点诊断 |
| v4 透明规则窗口 | 514 日 | 4,112 条测点记录；514 条 site 双轴结果 |

NGBoost 的“全部时刻”仅指 861 个模型可用日。未成熟未来真值的日期仍输出模型信号，但不
进入回顾评价。v4 的 514 日不得冒充 NGBoost 的时间分母；v4 只展示 local-max 与跨分区
site-confirmed 两轴，单点极值不能无条件代表整个滑坡体。

## 8. Vajont 门禁

- `data/vajont_fig5a_curves_2_3_4_5_58_mm_velocity.xlsx` 保持本地、未提交、未修改。
- 藕塘内部流程、图表、方法和局限收口前，不读取 Vajont 进入适配器或模型。
- 即使用户随后授权，`10d` 也不能替代真实时间戳差；Vajont 只检验可迁移性。
- Vajont 结果不得反向调整藕塘阈值、时间切分、标签或模型选择。

## 9. 当前证据入口与后续动作

- 方法与结果：`docs/ootang_manuscript_methods_results_draft.md`
- 阶段结果与证据强度：`docs/ootang_stage_results_package.md`
- 自动标签与分类计划：`docs/ootang_ngboost_auto_state_experiment_plan.md`
- SHAP 协议：`docs/ngboost_shap_protocol.md`
- 加速度操作化：`docs/ootang_v4_acceleration_decision.md`
- 全测点预测：`figures/convlstm/forecast_all_stations.png`
- NGBoost 结果：`figures/ngboost_auto_state_classifier_v1/`
- v4 基线：`figures/warning_operational_draft_v4/`
- 五阶段运行：`figures/pipeline/ootang_advisor_demo_run.json`

近期只做内部科研收口：核对图表、单位、时间分母、方法措辞和负结果证据链；按用户要求暂不
写导师报告，不启动新模型搜索，不启动 Vajont。后续效能研究需要新的独立时间段、跨滑坡数据
或现场事件真值，并在查看结果前版本化评价协议。
