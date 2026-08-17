# 藕塘 NGBoost 区间代理提前量敏感性

## 目的与边界

本试验在不改变模型、输入或案例的条件下，并列考察 `h=1/3/7` 日预测提前量。三个提前量均使用：

- 同一个固定 `NGBClassifier` 和同一组超参数；
- 相同四项科学输入：当日 `interval_z`、逐点速度、原始 `ΔV`、连续切线角；
- 相同 8 个测点 one-hot 控制量；
- 相同五级原始区间偏离代理标签；
- fit-only 训练，calibration/test 只评价；
- 无搜索、早停、重拟合、类别权重、SMOTE、合成标签或事后概率校准。

本试验只使用藕塘既有物化数据，不重训或修改 ConvLSTM，不修改 v4，不使用物理加速度和环境变量，也未读取或引入其他案例。所有提前量在运行前即固定；本报告不排名、不选择，也不声明“最佳”提前量。

## 样本支持

| horizon | fit/calibration/test 样本 | fit G/B/Y/O/R | calibration G/B/Y/O/R | test G/B/Y/O/R | 状态转移行 fit/calibration/test |
| --- | --- | --- | --- | --- | --- |
| 1 日 | 7280/1808/2288 | 3538/2870/701/83/88 | 531/847/287/143/0 | 584/1039/182/133/350 | 575/151/110 |
| 3 日 | 7264/1792/2272 | 3528/2864/701/83/88 | 519/845/285/143/0 | 571/1038/180/133/350 | 1343/300/281 |
| 7 日 | 7232/1760/2240 | 3506/2854/701/83/88 | 499/841/278/142/0 | 547/1034/176/133/350 | 2139/450/508 |

三个提前量的 fit 均包含五类，可以训练五分类 NGBoost。三个 calibration 均没有 red，因此 calibration red recall、AUC 和类别校准指标均明确记为不可定义。

## 全部时刻结果

| horizon | split | NGBoost accuracy | 持续基线 accuracy | NGBoost macro-F1 | 持续基线 macro-F1 | NGBoost ordinal MAE | 持续基线 ordinal MAE |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 日 | calibration | 0.906 | 0.916 | 0.905 | 0.923 | 0.094 | 0.084 |
| 1 日 | test | 0.947 | 0.952 | 0.925 | 0.936 | 0.053 | 0.048 |
| 3 日 | calibration | 0.781 | 0.833 | 0.607 | 0.833 | 0.219 | 0.167 |
| 3 日 | test | 0.860 | 0.876 | 0.716 | 0.835 | 0.146 | 0.127 |
| 7 日 | calibration | 0.715 | 0.744 | 0.547 | 0.723 | 0.288 | 0.261 |
| 7 日 | test | 0.675 | 0.773 | 0.459 | 0.681 | 0.463 | 0.247 |

三个提前量的 NGBoost 全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过各自的状态持续基线。提前量增加后持续基线下降，但 NGBoost 的全时刻判别也同步变弱，没有形成稳定的基线增益。

在错误方向上存在取舍：h=7 的 false-escalation rate 在 calibration/test 低于持续基线，但 false-deescalation rate 明显更高，且主要判别与概率指标更差。因此这不能解释为整体改进，也不用于选择 h=7。

## 概率质量

| horizon | split | log loss | multiclass Brier | top-label ECE |
| --- | --- | ---: | ---: | ---: |
| 1 日 | calibration | 0.248 | 0.143 | 0.018 |
| 1 日 | test | 0.137 | 0.075 | 0.021 |
| 3 日 | calibration | 0.620 | 0.315 | 0.045 |
| 3 日 | test | 0.413 | 0.210 | 0.047 |
| 7 日 | calibration | 0.939 | 0.439 | 0.089 |
| 7 日 | test | 1.157 | 0.468 | 0.095 |

随着提前量增加，log loss、Brier 和 ECE 整体增大，说明更远期的五级概率分布更不准确且校准更弱。这是并列敏感性描述，不用于选择提前量。

## 仅状态转移时刻

| horizon | split | NGBoost accuracy | 多数类 accuracy | NGBoost macro-F1 | 多数类 macro-F1 | NGBoost ordinal MAE | 持续基线 ordinal MAE |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 日 | calibration | 0.132 | 0.364 | 0.119 | 0.133 | 0.868 | 1.000 |
| 1 日 | test | 0.209 | 0.264 | 0.229 | 0.083 | 0.791 | 1.000 |
| 3 日 | calibration | 0.230 | 0.330 | 0.206 | 0.124 | 0.770 | 1.000 |
| 3 日 | test | 0.331 | 0.249 | 0.326 | 0.080 | 0.719 | 1.028 |
| 7 日 | calibration | 0.336 | 0.284 | 0.281 | 0.111 | 0.673 | 1.020 |
| 7 日 | test | 0.280 | 0.215 | 0.210 | 0.071 | 0.892 | 1.091 |

更长提前量产生了更多转移样本，NGBoost在部分转移指标上相对简单基线显示出信息，但这种改善没有在 calibration 与 test、accuracy 与 macro-F1、以及不同提前量之间同时保持一致。因此这些结果不能支持自动选择某一个 horizon，也不能授权调参或引入主流程。

## 当前判断

1. 固定 NGBoost 的 h=1/3/7 路径均可复算，h=1 与已提交 pilot 在 4,096 条 calibration/test 记录上保持数值一致；
2. 三个提前量的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过状态持续基线；
3. 状态转移行上存在有限信号，但跨时段和指标不稳定；
4. 概率质量随提前量增加而减弱；
5. 标签仍是区间偏离代理状态，不是独立灾害事件或专家真值；
6. 当前结果只支持保留为非排名敏感性诊断，不支持选择提前量、调整既有模型或进入正式预警流程。

## 产物

- `models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h1.pkl`
- `models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h3.pkl`
- `models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h7.pkl`
- `figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/predictions.csv`
- `figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/metrics.csv`
- `figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/confusion_matrices.csv`
- `figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/reliability.csv`
- `figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/horizon_summary.csv`
- `figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/manifest.json`
