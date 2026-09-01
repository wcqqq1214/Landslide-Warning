# 藕塘 NGBoost 五分类 SHAP 协议

> 当前权威口径：SHAP 解释的是滑坡体级五分类 NGBoost 主模型，不是 ConvLSTM。旧的独立
> NGBoost 位移增量回归 SHAP 执行链已经退役，不能代替本协议。

## 目的与解释对象

当前分析回答：在收到时刻 `t` 的全部 8 个测点观测后，五分类 site NGBoost 对未来
`H=7` 自动代理状态的期望等级

\[
E[L\mid X_t]=\sum_{k=0}^{4} kP(Y_{auto,t}=k\mid X_t)
\]

主要依赖哪些“测点 × 预警指标”输入。被解释模型为
[`models/ootang_ngboost_auto_state_site_v1.pkl`](../models/ootang_ngboost_auto_state_site_v1.pkl)，
模型与产物血缘由
[`manifest.json`](../figures/ngboost_auto_state_classifier_v1/manifest.json)固定。

这一定义与用户对导师意见的当前解释一致：ConvLSTM 产生 P10/P50/P90 位移预测及 80% **预测区间**，
区间经验覆盖率单独以 PICP 评价；NGBoost 融合四项指标并输出五级概率；SHAP 解释该
NGBoost 分类模型。SHAP 不计算 PICP，也不是 ConvLSTM 内部归因。

## 模型、输入与样本

- 模型：五分类 `NGBClassifier`，类别为 green、blue、yellow、orange、red；site 模型是主
  概率分类器，测点共享模型仅作逐点诊断。
- 目标：由未来 7 日多测点位移率与正速度 Q90 自动生成的 `Y_auto(t)`；它是科研代理结局，
  不是现场灾害真值。
- 输入：固定 32 维
  `8 × [interval_z, velocity_mm_per_day, acceleration_mm_per_day_squared, tangent_angle_degree]`；
  不包含未来目标或标签字段。
- 背景样本：fold 1 拟合期内等距抽取最多 12 个日期。
- 解释样本：fold 2 开发期内等距抽取最多 25 个日期。
- 方法：permutation SHAP；每个样本最多 65 次评估，输出目标为上述期望顺序等级。
- 输出规模：`25 日期 × 32 特征 = 800` 个 SHAP 值。

核心产物为
[`site_shap_values.csv`](../figures/ngboost_auto_state_classifier_v1/site_shap_values.csv)、
[`site_shap_importance.csv`](../figures/ngboost_auto_state_classifier_v1/site_shap_importance.csv)和
[`site_shap_summary.pdf`](../figures/ngboost_auto_state_classifier_v1/site_shap_summary.pdf)。
汇总图按 8 个测点 $\times$ 4 项指标展示全部 32 项平均绝对 SHAP，不截取 Top-$k$。
实现位于
[`ootang_ngboost_auto_state_classifier.py`](../code/warning/ootang_ngboost_auto_state_classifier.py)。

## 解释边界

- SHAP 数值只描述这个固定 NGBoost 在指定背景和解释样本上的模型依赖。
- mean absolute SHAP 只表示贡献幅度，不表示影响方向；相关的速度与切线角等特征可能分摊贡献。
- “主控因素”只能写成“模型支持的候选主控因素”或“关键贡献因素”，不能写成物理因果证明。
- 当前 NGBoost 没有超过严格 lag-7 persistence；因此 SHAP 更适合解释负结果模型如何决策，
  不能为模型有效性或现场预警能力背书。
- 不得依据 SHAP 排名回调四指标阈值、自动标签边界、时间折或模型参数。

## 旧回归 SHAP 的历史地位

旧阶段描述的是独立 NGBoost 回归器对相邻观测位移增量的解释。该模型不输出五级预警概率，
也不是当前预警主模型；其源码、入口、测试、模型及完整结果已经从当前工作树退役，可从 Git
基线 `b13eb8b` 恢复。`paper/process_report.tex` 直接嵌入的两张 stability PNG 仅为保证旧报告
可构建而保留，不是当前 manifest 证据。正文、结果图和当前证据链只引用上述 site 五分类
NGBoost SHAP，不得把两类模型的目标、样本或重要性排名合并。

Vajont 当前暂停，未进入本协议的输入、背景样本、解释样本或结果。
