# 藕塘固定 NGBoost 四指标分组消融

## 目的与边界

本试验用于回答：固定 NGBoost 的预测结果主要依赖哪类输入，以及速度、`ΔV`、切线角和测点身份是否在当前区间代理任务中提供增量信息。

所有消融严格保持：

- 同一个 `NGBClassifier`、超参数、五级目标和 h=1/3/7 提前量；
- 同一 fit/calibration/test 和逐时刻配对；
- fit-only 训练，calibration/test 只评价；
- 无调参、排名、选择、类别权重、SMOTE、重采样、重拟合或事后校准；
- 仅使用藕塘既有数据，不修改 ConvLSTM、v4 或 SHAP，不读取其他案例；
- 不保存 21 个消融模型，仅保存可复算的预测、指标和来源清单。

标签仍是未来原始区间偏离代理状态，不是专家标注或现场灾害真值。因此，区间特征的优势部分来自目标定义本身，不能解释成它具有独立的物理主控地位。

## 固定消融配置

1. `full`：区间 + 速度 + `ΔV` + 切线角 + 测点控制量；
2. `interval_only`：仅区间 + 测点控制量；
3. `without_interval`：去掉区间；
4. `without_velocity`：去掉速度；
5. `without_delta_v`：去掉 `ΔV`；
6. `without_tangent`：去掉切线角；
7. `without_station_controls`：去掉测点 one-hot。

七组配置在 h=1/3/7 共完成 21 次独立固定拟合。`full` 在三个 horizon 的 12,160 条 calibration/test 记录、概率、指标、混淆矩阵和可靠性数据均与已提交提前量敏感性一致。

## 主要结果

### 区间是当前代理任务的主要信息来源

去掉 `interval_z` 后，六个 `horizon × split` 的全时刻表现均显著恶化：

- macro-F1 相对 full 下降约 `0.215–0.699`；
- ordinal MAE 增加约 `0.343–0.913`；
- log loss 增加约 `0.421–1.917`。

这说明模型预测的核心来自当前区间偏离状态。但由于目标就是未来区间偏离状态，这一结果是任务定义下的预期关联，不是独立因果证据。

### 仅区间已能接近 full，但转移识别持续较弱

`interval_only` 在全时刻上的变化通常小于“去掉区间”，部分时段的概率指标甚至优于 full；但在全部六个 `horizon × split` 的状态转移行中：

- macro-F1 均低于 full，差值约 `-0.001` 至 `-0.127`；
- ordinal MAE 均高于 full，增加约 `0.020–0.206`。

因此，速度、`ΔV`、切线角的组合没有明显改善多数稳定时刻，却对状态变化时刻提供了一定增量信息。

### `ΔV` 对状态转移的增量最一致

去掉 `ΔV` 后，状态转移行的 ordinal MAE 在全部六个 `horizon × split` 中均变差，增加约 `0.013–0.144`；macro-F1 除 h=1 calibration 的极小正差外，其余五个时段均下降，h=7 calibration/test 分别下降约 `0.088/0.099`。

全时刻结果并非始终同方向，例如 h=3 calibration 在去掉 `ΔV` 后部分指标略有改善，而 h=3/h=7 test 的概率或顺序指标恶化。因此更谨慎的结论是：

> `ΔV` 在当前任务中主要为状态转移识别提供增量信息，但并未稳定改善所有时刻和所有概率指标。

### 速度与切线角的单独增量较小且方向不稳定

分别去掉速度或切线角后，大部分变化明显小于去掉区间：

- 去掉速度的全时刻 macro-F1 变化约 `-0.005` 至 `+0.013`；
- 去掉切线角的全时刻 macro-F1 变化约 `-0.007` 至 `+0.004`；
- 转移行的变化也随 horizon 和 split 改变方向。

这与速度和当前切线角由同一 `velocity/V0` 关系构造、信息高度相关相符。但本试验只分别删除其中一个，不能证明二者的联合运动学信息没有价值；若要回答联合贡献，需要另行预注册“同时去掉速度和切线角”的实验，本轮不追加。

### 测点身份控制量不是主要决定因素

去掉 station one-hot 后，六个全时刻 macro-F1 差值约 `-0.005` 至 `+0.017`，ordinal MAE 变化约 `-0.012` 至 `+0.003`；转移行同样呈小幅混合变化。当前结果不支持模型主要依靠测点身份记忆标签，但也不能将这种小差异解释为跨测点可迁移性证明。

## 非排名结论

本试验没有选定特征集，也没有据 test 指标修改模型。可以形成的受限判断为：

1. 当前区间代理任务由 `interval_z` 主导，这与目标定义直接相关；
2. 非区间运动学指标的主要价值集中在状态转移行，而不是多数稳定时刻；
3. 其中 `ΔV` 的转移增量最为一致，支持继续保留为候选预警指标；
4. 速度和切线角的单独增量小且不稳定，提示二者存在信息冗余，但本轮不能判断其联合贡献；
5. 测点 one-hot 的影响较小，没有显示明显的身份依赖；
6. 这些结果仍不足以支持 NGBoost 进入主流程，因为 full 模型在提前量敏感性中没有超过状态持续基线，且标签不是独立事件真值。

## 产物

- `figures/ngboost_interval_proxy_feature_ablation_ootang_v1/predictions.csv`
- `figures/ngboost_interval_proxy_feature_ablation_ootang_v1/metrics.csv`
- `figures/ngboost_interval_proxy_feature_ablation_ootang_v1/confusion_matrices.csv`
- `figures/ngboost_interval_proxy_feature_ablation_ootang_v1/reliability.csv`
- `figures/ngboost_interval_proxy_feature_ablation_ootang_v1/ablation_summary.csv`
- `figures/ngboost_interval_proxy_feature_ablation_ootang_v1/manifest.json`

所有产物都明确 `selection_performed=false`、`ranking_performed=false`、`models_persisted=false`、`formal_warning_output=false` 和 `vajont_used=false`。
