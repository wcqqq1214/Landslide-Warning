# 项目文档导航

本页用于区分当前权威文档、辅助参考、历史记录和机器生成证据。它不是导师报告，也不改变任何
实验配置、数据划分、阈值、模型或结论。

## 权威顺序

发生冲突时按以下规则判断：

1. [`AGENTS.md`](../AGENTS.md) 中“导师确定的科研主线”决定研究问题、固定技术路线和禁止事项。
2. 版本化 `config/`、对应源码以及运行 `manifest.json` 决定某次实验实际执行的方法；CSV/JSON
   结果决定可报告的数值，不以叙述性文档覆盖机器产物。
3. [`CODEX_HANDOFF.md`](../CODEX_HANDOFF.md) 与 [`progress.md`](progress.md) 记录最近完成状态、
   当前限制和下一步。
4. 下列 canonical 文档负责把方法、结果与局限串成论文证据链。
5. reference 和 historical 文档只能用于查证背景与决策过程，不得覆盖以上来源。

## 当前主线和时间口径

```text
ConvLSTM 多测点概率位移预测
  → 预测区间及覆盖率
  → 四项指标（区间、速度、严格加速度、改进切线角）
  → H=7 自动未来状态标签
  → 五分类 site NGBoost
  → NGBoost SHAP
  → 测点级与滑坡体级逐时预警
```

当前 SHAP 是 **NGBoost 分类 SHAP**，解释 site 分类器的期望有序等级。它不是 ConvLSTM SHAP，
也不是因果证明；当前定义见 [`ngboost_shap_protocol.md`](ngboost_shap_protocol.md)。
`figures/shap/ngboost_regression_*` 是早期独立回归任务，仅作历史对照。

| 时间分母 | 数量 | 使用位置 |
| --- | ---: | --- |
| 原始物化序列 | 1,461 日 | 完整藕塘输入历史 |
| NGBoost 模型可用时间 | 861 日 | 三折分类与逐时概率；SHAP 从固定折内样本解释 |
| v4 透明规则基线 | 514 日 | 基线自己的有效输入/结果窗口 |

三者不能互相替代。“全部时刻”必须写清是全部 861 个模型可用时刻，还是 v4 的 514 个基线时刻。

Vajont 当前暂停。只有藕塘收口且用户再次明确要求后，才可读取其数据进入适配、训练或结果生成。

## Canonical：当前科研文档

| 文档 | 用途 |
| --- | --- |
| [`progress.md`](progress.md) | 当前进度、最近运行、负结果和下一步 |
| [`ootang_manuscript_methods_results_draft.md`](ootang_manuscript_methods_results_draft.md) | 当前整合的方法、结果与讨论文字底稿 |
| [`ootang_stage_results_package.md`](ootang_stage_results_package.md) | 藕塘阶段结果、证据强度及可写/不可写边界 |
| [`ootang_ngboost_auto_state_experiment_plan.md`](ootang_ngboost_auto_state_experiment_plan.md) | H=7 自动标签、五分类 NGBoost、基线和分类 SHAP 的版本化记录 |
| [`ngboost_shap_protocol.md`](ngboost_shap_protocol.md) | 当前五分类 site NGBoost 的 SHAP 对象、样本和解释边界 |
| [`ootang_operational_run.md`](ootang_operational_run.md) | 四指标计算与 v4 透明规则基线；不替代 NGBoost |
| [`ootang_warning_data_dictionary.md`](ootang_warning_data_dictionary.md) | 指标、状态、单位和输出字段定义 |

## Reference：辅助核对材料

| 文档 | 用途 |
| --- | --- |
| [`advisor_review_action_plan.md`](advisor_review_action_plan.md) | 导师意见与历次执行记录；最新科研约束仍以 `AGENTS.md` 为准 |
| [`ootang_convlstm_elevation_fixed120_review.md`](ootang_convlstm_elevation_fixed120_review.md) | ConvLSTM 三折 × 五种子诊断 |
| [`ootang_data_lineage_expert_review.md`](ootang_data_lineage_expert_review.md) | 数据来源、物化序列限制和证据门禁 |
| [`ootang_v4_acceleration_decision.md`](ootang_v4_acceleration_decision.md) | 严格加速度阈值的项目操作化依据 |
| [`warning-limitations.md`](warning-limitations.md) | 预警结果的使用边界与历史限制汇总 |
| [`results_report.md`](results_report.md) | 详细数值与历次结果记录；当前论文叙述以 canonical 初稿为准 |
| [`design.md`](design.md) | 代码与产物关系参考；若与当前主线冲突，以 canonical 文档和代码为准 |

## Historical：历史路线与工程记录

以下材料保留用于追溯，不是当前科研主线：

- [`framework.md`](framework.md)、[`framework_status.md`](framework_status.md) 中导师纠偏前的框架与状态；
- `figures/shap/ngboost_regression_*` 所对应的早期独立回归 SHAP；
- `ootang_ngboost_interval_proxy_*`、`v5_*`、旧自动 V0 和已拒绝 challenger 的探索记录；
- `ootang_prequential_*`、`ootang_epoch_*`、`ootang_*engineering.md` 等部署、账本、轮换和恢复工程记录；
- 旧 v1/v2/v3 融合、30 日速度定义及已退役产物的描述。

这些文档中的旧“当前”“下一步”只表示其写作时点，不应据此重启部署工程、改写导师主线或
启动额外模型搜索。

## Generated：机器生成证据

| 入口 | 内容 |
| --- | --- |
| [`figures/pipeline/ootang_advisor_demo_run.json`](../figures/pipeline/ootang_advisor_demo_run.json) | 藕塘五阶段运行清单与产物合同 |
| [`figures/convlstm/`](../figures/convlstm/) | 全 8 测点预测、区间、覆盖率和滚动评价 |
| [`figures/ngboost_auto_state_ecdf_v2/`](../figures/ngboost_auto_state_ecdf_v2/) | H=7 自动标签、边界及时间线 |
| [`figures/ngboost_auto_state_classifier_v1/`](../figures/ngboost_auto_state_classifier_v1/) | 五级概率、基线指标、分类 SHAP 及逐时预警 |
| [`figures/warning_operational_draft_v4/`](../figures/warning_operational_draft_v4/) | 514 日透明规则基线和多点诊断 |
| [`figures/advisor_ootang_v1/advisor_summary.md`](../figures/advisor_ootang_v1/advisor_summary.md) | 已生成核心图表和表格的索引 |

`advisor_summary.md` 只是生成证据索引，不是正在撰写或已经定稿的导师报告。解释任何结果时，
应同时核对对应配置、manifest、CSV 指标和当前局限说明。
