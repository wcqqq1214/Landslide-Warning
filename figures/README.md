# Figures 产物索引

`figures/` 保存由 `code/` 脚本生成的图、表和审计快照，不是原始监测数据。CSV、JSON 和模型
文件不得手工改写；PNG、PDF、SVG 应由绘图代码生成。若只调整版式，应直接复用版本化 CSV，
不重训模型，并同步更新 manifest 中的图件哈希或 figure-refresh 记录。

当前全部结果均为藕塘科研试跑，`formal_warning_output=false`。ConvLSTM 输出 8 个测点的 P10/P50/P90 位移预测；ECDF 自动生成未来状态代理标签；五分类 site NGBoost 进行概率预警；NGBoost SHAP 解释模型依赖；v4 只作透明规则基线。

## 当前权威入口

| 环节 | 入口 | 说明 |
| --- | --- | --- |
| 全流程 | [`pipeline/ootang_advisor_demo_run.json`](pipeline/ootang_advisor_demo_run.json) | 2026-08-31 五阶段运行快照；保留当次输入输出指纹 |
| ConvLSTM | [`convlstm/forecast_run_manifest.json`](convlstm/forecast_run_manifest.json) | 8 点 P10/P50/P90、fit/calibration/test 与区间评价 |
| ConvLSTM 汇总图 | [`convlstm/forecast_all_stations.png`](convlstm/forecast_all_stations.png) | 管线与证据包使用的 8 点汇总图 |
| 报告分页图 | [`../paper/README.md`](../paper/README.md) | 当前报告按测点分页展示累计位移、日增量、残差和预测区间 |
| 滚动与种子诊断 | [`convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json`](convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json) / [`seed_stability_0_4/manifest.json`](convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json) | 当前 7 通道 fixed120 内部诊断 |
| ECDF 自动标签 | [`ngboost_auto_state_ecdf_v2/manifest.json`](ngboost_auto_state_ecdf_v2/manifest.json) | `H=7` 未来多点变形代理状态及开发折边界 |
| NGBoost 五分类 | [`ngboost_auto_state_classifier_v1/manifest.json`](ngboost_auto_state_classifier_v1/manifest.json) | site 概率、基线比较及测点诊断 |
| NGBoost SHAP | [`site_shap_summary.png`](ngboost_auto_state_classifier_v1/site_shap_summary.png) / [`site_shap_importance.csv`](ngboost_auto_state_classifier_v1/site_shap_importance.csv) | 8 点 × 4 指标全量依赖；解释期望顺序等级，不表示因果 |
| NGBoost 逐时图 | [`warning_timeline.pdf`](ngboost_auto_state_classifier_v1/warning_timeline.pdf) / [`site_predictions.csv`](ngboost_auto_state_classifier_v1/site_predictions.csv) / [`station_predictions.csv`](ngboost_auto_state_classifier_v1/station_predictions.csv) | `10×861` 图：H=7 标签、site NGBoost 和 8 点；840 日成熟、21 日未成熟 |
| v4 透明基线 | [`warning_operational_draft_v4/ootang_operational_run_manifest.json`](warning_operational_draft_v4/ootang_operational_run_manifest.json) | 四指标测点结果和多点规则融合，不替代 NGBoost |
| 证据包 | [`advisor_ootang_v1/manifest.json`](advisor_ootang_v1/manifest.json) / [`advisor_summary.md`](advisor_ootang_v1/advisor_summary.md) | 2026-08-31 机械打包快照，不是当前报告 |

当前 SHAP 与逐时图在 2026-09-01 由冻结 CSV 免训练重绘；最新图件哈希以 classifier
[`manifest.json`](ngboost_auto_state_classifier_v1/manifest.json) 的 `figures` 和 `figure_refresh`
为准。pipeline 与 advisor manifest 保留各自运行时的旧图哈希，不反写为当前版式。

## 时间口径

- `1461`：2016-07-01—2020-06-30 的完整运动学日期数；8 点长表为 `1461×8=11688` 行。
- `1425`：2016-08-06—2020-06-30 的 ConvLSTM 一步预测日期；fit/calibration/test 分别为
  `911/227/287` 日，预测长表为 `1425×8=11400` 行。
- `861`：2018-02-21—2020-06-30 的 NGBoost 模型输出日期；fold 1 为拟合段、fold 2 为开发段、
  fold 3 为历史回顾段，不能把 861 日全部称为 OOF。测点诊断共 `861×8=6888` 行。
- `514`：2019-02-03—2020-06-30 的 v4 规则基线日期；测点表为 `514×8=4112` 行。

四种时间范围用途不同，不能混称“全历史”。861 日中 840 日已有成熟 H=7 标签，每折末端
合计 21 日尚未成熟；这些日期仍有模型预测。暖启动或缺失状态必须显式保留，不得静默补值。

## 解释与历史边界

- 当前 SHAP 指定解释对象是五分类 site NGBoost，不是 ConvLSTM；排名只能称为候选重要因素，不能写成物理因果证明。
- SHAP“全量”是完整展示 32 个输入特征，不是解释全部 861 日；背景样本固定为 fold 1 的
  12 日，解释样本固定为 fold 2 的 25 日，逐特征值表共 `25×32=800` 行。
- 旧独立位移增量回归 SHAP 的执行链与完整结果已退役；`shap/stability/` 中保留的两张 PNG
  仅作历史快照，当前 `process_report` 不引用，也不得替代分类 SHAP。
- `convlstm/` 根目录保留的旧 6 通道 rolling/seed 文件属于历史快照；未启用的 inner-validation/capacity 实现与产物已退役，当前 fixed120 诊断只认上表版本化 manifests。
- `auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.png` 与
  `v5_candidate_display_ootang_v1/candidate_display.png` 同样只是历史快照，当前 `process_report`
  不引用，也不是当前运行入口。
- Vajont 已暂停：不得读取、适配、训练或生成结果，直到藕塘流程收口且用户再次明确启动。

删除或归档历史产物时，以 manifest 依赖为边界；不要只删除支撑表后继续引用旧图或旧结论。历史内容可从 Git 恢复，无需在当前索引重复维护。
