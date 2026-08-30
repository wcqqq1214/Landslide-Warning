# 藕塘导师展示包摘要

**流程已跑通，负结果完整保留，不再依据现有评价数据调参。** 本包仅机械汇总既有版本化结果，不重训模型、不重算预测，也未使用 Vajont；全部内容均为藕塘科研演示，`formal_warning_output=false`。

## 五张核心图

- [convlstm_all_stations](../convlstm/forecast_all_stations.png)：8 点训练/校准/预测段位移及 P10/P50/P90
- [auto_state_timeline](../ngboost_auto_state_classifier_v1/warning_timeline.pdf)：H=7 site 与 8 点逐时五色信号
- [classifier_shap](../ngboost_auto_state_classifier_v1/site_shap_summary.pdf)：固定 site NGBoost 的测点×指标依赖
- [v4_station_diagnostic](../warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.pdf)：v4 全测点四指标透明诊断
- [v4_site_timeline](../warning_operational_draft_v4/ootang_v4_full_warning_timeline.pdf)：v4 site-confirmed 与 local-max 双轴时间线

## ConvLSTM overall 三折（五种子）

| fold | RMSE mean±SD | persistence RMSE | RMSE skill | coverage | width | interval score |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1.970±0.418 | 0.2451 | -7.0364 | 0.3869 | 2.9588 | 8.4626 |
| 2 | 0.356±0.085 | 0.1200 | -1.9704 | 0.9557 | 1.3198 | 1.4753 |
| 3 | 0.328±0.008 | 0.3403 | 0.0358 | 0.7544 | 0.4757 | 1.1021 |

前两折未超过 persistence；第三折的误差结果仍需结合增量强平滑解释。区间 coverage、width 与 interval score 必须联合报告。

## H=7 自动标签支持

| fold | green | blue | yellow | orange | red |
| --- | --- | --- | --- | --- | --- |
| 1 | 56 | 56 | 56 | 56 | 56 |
| 2 | 138 | 66 | 39 | 21 | 16 |

标签是未来位移率与未来正速度 Q90 构成的多点代理结局，不是现场灾害真值。

## 分类器 Panel A：fold-2 all-valid 280 日

| estimator | n | accuracy | macro-F1 | ordinal MAE | log-loss | Brier | status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ngboost | 280 | 0.3536 | 0.2871 | 0.7429 | 3.3358 | 0.9960 | exploratory_negative_result |
| multinomial_logistic | 280 | 0.2500 | 0.1964 | 0.8321 | 2.2732 | 1.0771 | exploratory_negative_result |
| fold1_prior | 280 | 0.4929 | 0.1321 | 0.9679 | 1.6094 | 0.8000 | exploratory_negative_result |

## 分类器 Panel B：fold-2 common 273 日

| estimator | n | accuracy | macro-F1 | ordinal MAE | log-loss | Brier | status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1_ngboost | 273 | 0.3370 | 0.2813 | 0.7619 | 3.4212 | 1.0215 | exploratory_negative_result |
| memory_ngboost | 273 | 0.3370 | 0.2813 | 0.7619 | 3.4346 | 1.0282 | rejected |
| residual_ngboost | 273 | 0.3553 | 0.3307 | 0.7070 | 3.7538 | 1.0238 | rejected |
| lag7_persistence | 273 | 0.8022 | 0.6722 | 0.2234 | N/A | N/A | causal_hard_baseline |

固定 NGBoost 未超过严格 persistence；memory 的共同集硬指标与 v1 相同且概率更差；residual 硬指标略有改善，但仍远落后 persistence，概率指标也更差。memory 与 residual 均为 rejected。不报告 fold 3 分类指标。

## v4 多点双轴计数

| axis | green | blue | yellow | orange | red | not confirmed |
| --- | --- | --- | --- | --- | --- | --- |
| confirmed_color | 8 | 48 | 31 | 9 | 18 | 400 |
| local_max_color | 0 | 56 | 196 | 111 | 151 | N/A |

400 个未确认日表示空间支持不足，不是缺测或 green。v4 是透明、非正式基线。

## 证据边界

三种 NGBoost 方案均未证明改善，不能宣称预警有效。分类 SHAP 仅说明独立 NGBoost 的模型依赖，不是 ConvLSTM 内部解释或因果证据。加速度 `A0` 是 fit-only 的项目操作化阈值，不是参考 Word 论文原阈值或正式现场标准。

## CSV 附件

- [table1_convlstm.csv](table1_convlstm.csv)
- [table2_auto_labels.csv](table2_auto_labels.csv)
- [table3_classifier_comparison.csv](table3_classifier_comparison.csv)
- [table4_multistation_summary.csv](table4_multistation_summary.csv)
- [table_s1_acceleration_thresholds.csv](table_s1_acceleration_thresholds.csv)
