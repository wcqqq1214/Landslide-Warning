# Figures 产物索引

`figures/` 保存由 `code/` 脚本生成的图、表和审计快照，不是原始监测数据。不要手工修改 CSV、JSON、PNG、PDF 或 SVG；需要更新时应重跑对应脚本，并以同目录 manifest 的输入、输出和 SHA-256 为准。

当前全部结果均为藕塘科研试跑，`formal_warning_output=false`。ConvLSTM 输出 8 个测点的 P10/P50/P90 位移预测；ECDF 自动生成未来状态代理标签；五分类 site NGBoost 进行概率预警；NGBoost SHAP 解释模型依赖；v4 只作透明规则基线。

## 当前权威入口

| 环节 | 入口 | 说明 |
| --- | --- | --- |
| 全流程 | [`pipeline/ootang_advisor_demo_run.json`](pipeline/ootang_advisor_demo_run.json) | 五阶段执行、输入输出指纹与契约状态 |
| ConvLSTM | [`convlstm/forecast_run_manifest.json`](convlstm/forecast_run_manifest.json) | 8 点 P10/P50/P90、fit/calibration/test 与区间评价 |
| ConvLSTM 图 | [`convlstm/forecast_all_stations.png`](convlstm/forecast_all_stations.png) | 全部 8 个测点的完整时间轴 |
| 滚动与种子诊断 | [`convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json`](convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json) / [`seed_stability_0_4/manifest.json`](convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json) | 当前 7 通道 fixed120 内部诊断 |
| ECDF 自动标签 | [`ngboost_auto_state_ecdf_v2/manifest.json`](ngboost_auto_state_ecdf_v2/manifest.json) | `H=7` 未来多点变形代理状态及开发折边界 |
| NGBoost 五分类 | [`ngboost_auto_state_classifier_v1/manifest.json`](ngboost_auto_state_classifier_v1/manifest.json) | site 概率、基线比较及测点诊断 |
| NGBoost SHAP | [`site_shap_summary.png`](ngboost_auto_state_classifier_v1/site_shap_summary.png) / [`site_shap_importance.csv`](ngboost_auto_state_classifier_v1/site_shap_importance.csv) | 解释期望顺序等级，仅表示模型依赖 |
| v4 透明基线 | [`warning_operational_draft_v4/ootang_operational_run_manifest.json`](warning_operational_draft_v4/ootang_operational_run_manifest.json) | 四指标测点结果和多点规则融合，不替代 NGBoost |
| 证据包 | [`advisor_ootang_v1/manifest.json`](advisor_ootang_v1/manifest.json) / [`advisor_summary.md`](advisor_ootang_v1/advisor_summary.md) | 当前图表与汇总表入口，不是导师报告 |

## 时间口径

- `1461`：2016-07-01—2020-06-30 的发布日序列总行数。
- `861`：2018-02-21—2020-06-30 的 NGBoost 模型可用 OOF 日期；测点诊断共 `861×8=6888` 行。
- `514`：2019-02-03—2020-06-30 的 v4 规则基线日期。

三种时间范围用途不同，不能混称“全历史”。暖启动或尚无成熟未来真值的日期应读取状态字段，不得静默补值。

## 解释与历史边界

- 当前 SHAP 指定解释对象是五分类 site NGBoost，不是 ConvLSTM；排名只能称为候选重要因素，不能写成物理因果证明。
- `shap/ngboost_regression_*` 是旧独立位移增量回归 SHAP，仅用于方法演进溯源，不得替代当前分类 SHAP。
- `convlstm/` 根目录的旧 6 通道滚动、早停和容量文件属于历史快照；当前 fixed120 诊断只认上表版本化 manifests。
- Vajont 已暂停：不得读取、适配、训练或生成结果，直到藕塘流程收口且用户再次明确启动。

删除或归档历史产物时，以 manifest 依赖为边界；不要只删除支撑表后继续引用旧图或旧结论。历史内容可从 Git 恢复，无需在当前索引重复维护。
