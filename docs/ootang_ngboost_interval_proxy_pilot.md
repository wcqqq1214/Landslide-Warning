# 藕塘 NGBoost 下一日区间风险代理试验

## 目的与边界

该试验只回答一个问题：在不改变既有 ConvLSTM 和 v4 规则模型的前提下，当前四项指标能否帮助 NGBoost 预测下一日的五级区间偏离状态。

试验具有以下固定边界：

- 仅使用藕塘 8 个测点；未读取、适配或运行其他案例；
- 使用固定的 `NGBClassifier` 五分类模型，不进行模型替换或超参数搜索；
- 不重训、不修改 ConvLSTM；只读取其已物化的原始 P10/P50/P90 预测；
- 不修改 v4 阈值、融合和产物；只读取现有每测点比较基准 `V0` 计算连续切线角；
- 不使用物理加速度、降雨、水位、地下水位、气象、高程、空间块或 v4 融合等级；
- fit 时段的 ConvLSTM 区间是已有拟合诊断，不是 out-of-fold/cross-fitted 预测；因此 fit 指标只作训练描述，泛化判断只看后续时段；
- 输出是探索性代理标签试验，`formal_warning_output=false`、`vajont_used=false`。

## 目标与输入

目标为下一自然日的原始区间偏离等级：

```text
X(t) -> interval_proxy_level(t+1)
```

等级由现有 `classify_observed_interval_states` 对下一日实际位移相对下一日原始 P10/P50/P90 的位置进行五级划分。它是未来区间风险代理状态，不是专家标注、现场灾害真值或正式预警等级。

模型的四项科学输入均来自时刻 `t`：

1. `interval_z`：当日观测相对当日原始预测分布的标准化偏离；
2. `velocity_mm_per_day`：真实时间间隔下的逐点速度；
3. `delta_v_mm_per_day`：相邻逐点速度之差；
4. `tangent_angle_degree`：`degrees(arctan(velocity/V0))`。

另外使用 8 个测点 one-hot 作为控制变量。它们不是预警指标。所有样本要求同测点、同 split、相邻一日；不允许跨 fit/calibration/test 边界。

## 数据与训练协议

| split | 样本数 | green | blue | yellow | orange | red | 状态转移行 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fit | 7,280 | 3,538 | 2,870 | 701 | 83 | 88 | 575 |
| calibration | 1,808 | 531 | 847 | 287 | 143 | 0 | 151 |
| test | 2,288 | 584 | 1,039 | 182 | 133 | 350 | 110 |

固定训练策略为：

- 只用 fit 训练；
- `NGBClassifier`、5 类 categorical 分布、500 棵深度 3 的树、学习率 0.01、随机种子 0；
- calibration 和 test 仅评价，不传入 `fit()`，也不在之后重拟合；
- 不使用 SMOTE、合成标签、重采样、类别权重、提前停止或事后概率校准；
- calibration 没有 red，因此其 red recall、AUC 和类别校准指标明确记为不可定义。

比较基线为 fit 多数类/先验概率和“明日状态等于今日状态”的持续性基线。

## 结果

### 全部时刻

| split | 方法 | accuracy | macro-F1（支持类） | balanced accuracy | ordinal MAE | quadratic kappa |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| calibration | NGBoost | 0.906 | 0.905 | 0.895 | 0.094 | 0.939 |
| calibration | 状态持续 | **0.916** | **0.923** | **0.923** | **0.084** | **0.946** |
| test | NGBoost | 0.947 | 0.925 | 0.915 | 0.053 | 0.985 |
| test | 状态持续 | **0.952** | **0.936** | **0.935** | **0.048** | **0.986** |

NGBoost 概率指标如下：

| split | log loss | multiclass Brier | top-label ECE |
| --- | ---: | ---: | ---: |
| calibration | 0.248 | 0.143 | 0.018 |
| test | 0.137 | 0.075 | 0.021 |

测试期 NGBoost 各等级 recall 为 green `0.943`、blue `0.962`、yellow `0.940`、orange `0.737`、red `0.994`。这些高值主要受状态持续性支配，不能脱离转移行评价。

### 仅状态转移时刻

| split | 方法 | accuracy | macro-F1（支持类） | ordinal MAE | quadratic kappa |
| --- | --- | ---: | ---: | ---: | ---: |
| calibration | NGBoost | 0.132 | 0.119 | 0.868 | 0.322 |
| calibration | fit 多数类 | **0.364** | **0.133** | 0.868 | 0.000 |
| calibration | 状态持续 | 0.000 | 0.000 | 1.000 | 0.277 |
| test | NGBoost | 0.209 | **0.229** | **0.791** | **0.786** |
| test | fit 多数类 | **0.264** | 0.083 | 1.527 | 0.000 |
| test | 状态持续 | 0.000 | 0.000 | 1.000 | 0.703 |

NGBoost 在 test 转移行上比多数类基线具有更好的类别均衡性和顺序误差，但绝对 accuracy 只有 `0.209`；在 calibration 转移行上连多数类基线也没有稳定超过。其全时刻指标也略低于简单持续性基线。

## 当前判断

该试验已经证明技术路径能够完整运行，并能输出有限、归一化的五级概率。但现有结果**不支持把 NGBoost 引入或替换现有主流程**：

1. 全部时刻表现未超过状态持续基线；
2. 真正关键的状态转移时刻识别仍弱且跨时段不稳定；
3. 标签是由既有预测区间构造的代理状态，不是独立事件真值；
4. calibration 缺少 red，且当前 test 已在项目中被多轮查看，只能作内部探索。

因此 NGBoost 保持为显式、隔离的 pilot。是否开展类别权重、时序窗口、其他标签或正式模型比较，需在单独决策后进行；本次结果不会自动改动 ConvLSTM、v4 或论文主结论。

## 产物

- `models/ootang_ngboost_interval_proxy_pilot_v1.pkl`
- `figures/ngboost_interval_proxy_pilot_ootang_v1/predictions.csv`
- `figures/ngboost_interval_proxy_pilot_ootang_v1/metrics.csv`
- `figures/ngboost_interval_proxy_pilot_ootang_v1/confusion_matrices.csv`
- `figures/ngboost_interval_proxy_pilot_ootang_v1/reliability.csv`
- `figures/ngboost_interval_proxy_pilot_ootang_v1/manifest.json`
