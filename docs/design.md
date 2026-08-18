# 当前代码设计

本文件只描述当前工作树中可执行的藕塘原型。旧 30 日 `V0` 标签、旧预警融合、旧 v2/v3 运行入口及其测试已于 2026-08-13 从当前树移除；若需复现，使用 Git 历史而不是当前默认或显式管线。

所有藕塘规则产物都是研究原型：`formal_warning_output=false`、`vajont_used=false`。当前发布物化序列无法恢复原始 GNSS 和完整生成血缘，故允许工程初跑（`prototype_run_gate=allowed`），但确认性证据与正式预警仍被阻断（`confirmatory_evidence_gate=blocked`）。

## 当前流程

```text
data/monitoring_data.csv
  └─ features/build_features.py
       ├─ data/features.csv
       ├─ data/ootang_kinematics_long.csv
       └─ figures/tangent_angle/uniform_rates.csv

data/features.csv + data/station_coords.csv
  └─ convlstm/model.py
       ├─ models/convlstm.pt
       └─ figures/convlstm/{forecast_predictions,metrics,calibration,...}

data/monitoring_data.csv
  └─ explainability/ngboost_shap.py
       └─ figures/shap/ngboost_regression_*

ootang_kinematics_long.csv + forecast_predictions.csv
  + v1 base protocol + v2 acceleration extension + v4 profile
  └─ warning/operational_run_v4.py
       └─ figures/warning_operational_draft_v4/*

forecast_predictions.csv + ootang_kinematics_long.csv
  + v4 comparator V0 + isolated NGBoost pilot profile
  └─ warning/ootang_ngboost_interval_proxy_pilot.py
       ├─ models/ootang_ngboost_interval_proxy_pilot_v1.pkl
       └─ figures/ngboost_interval_proxy_pilot_ootang_v1/*

same fixed pilot contract + predeclared horizons [1, 3, 7]
  └─ warning/ootang_ngboost_interval_proxy_horizon_sensitivity.py
       ├─ models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h{1,3,7}.pkl
       └─ figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/*

same fixed horizons/model + seven predeclared feature sets
  └─ warning/ootang_ngboost_interval_proxy_feature_ablation.py
       └─ figures/ngboost_interval_proxy_feature_ablation_ootang_v1/*

future frozen protocol + independent outcome labels
  └─ warning/formal_warning.py
       └─ formal warning artifacts (not implemented; current gate rejects)
```

`main.py` contains eleven independently selectable stages. Its no-argument chain is exactly `features → convlstm → ootang-operational-v4`; independent SHAP, the NGBoost proxy pilot/sensitivity/ablation, and all ConvLSTM diagnostics require `--stage`.

## 模块职责

| 模块 | 当前职责 | 主要输出 / 边界 |
| --- | --- | --- |
| `code/features/kinematics.py` | 计算真实时间间隔下的逐点速度、原始 `ΔV` 和加速度 | `v_i=(U_i-U_{i-1})/Δt_i`；`a_i=(v_i-v_{i-1})/Δt_i`；速度一行、加速度两行 warmup 明示 |
| `code/features/build_features.py` | 生成环境、水文、运动学和切线角特征 | 不构造旧 30 日 `V0` 分类标签 |
| `code/convlstm/model.py` | 以 7 通道输入预测全部八测点位移 P10/P50/P90 | 输出训练/校准/测试分段图、预测 CSV、覆盖率和运行 manifest |
| `code/convlstm/rolling_validation.py`、`seed_stability.py` | 固定 7 通道的滚动和多种子诊断 | 当前显式阶段；不将历史 6 通道结果当作 7 通道证据 |
| `code/explainability/ngboost_shap.py` | 独立 NGBoost 回归及 permutation SHAP | 解释的是 `U_t-U_{t-1}` 模型依赖；当前只运行单一冻结时序留出，不解释 ConvLSTM、不推断物理因果、不输出预警分类。若需跨折稳定性，须另行冻结协议和计算预算 |
| `code/warning/ootang_ngboost_interval_proxy_pilot.py` | 用四项连续指标训练固定 NGBoost 五分类 pilot，预测下一日原始区间偏离状态 | 仅显式运行；标签是代理状态，当前结果未超过持续性基线，不替换 ConvLSTM/v4，也不读取其他案例 |
| `code/warning/ootang_ngboost_interval_proxy_horizon_sensitivity.py` | 在同一模型/输入/训练策略下并列运行 h=1/3/7 | 只报告非排名敏感性；不选择 horizon，所有提前量的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过持续基线 |
| `code/warning/ootang_ngboost_interval_proxy_feature_ablation.py` | 固定模型与 horizon，对七组输入进行 21 次分组消融 | 不保存消融模型、不排名；区间主导代理任务，`ΔV` 对状态转移的增量最一致 |
| `code/warning/draft_evidence.py` | 重建 v4 所需的区间、运动学和稳定段证据 bundle | 只作审计；严格 MVIF 失败不得生成正式 V0 |
| `code/warning/operational_run.py` | 验证 v4 profile、fit-only 参数、输入与来源指纹，写测点和滑坡体时间线 | 只接受 v4 profile，拒绝旧 profile；不输出正式预警 |
| `code/warning/operational_v4_fusion.py` | 区间、运动学和加速度三证据族的测点候选融合 | 速度与切线角为一个 family；`ΔV` 不参与 ordinal 投票 |
| `code/warning/operational_spatial_fusion.py` | v4 当前使用的双轴空间确认实现 | 当前只由 v4 调用；旧 v3 运行入口及其专属实现只保留在 Git 历史 |
| `code/warning/formal_warning.py` | 正式预警的 fail-closed 入口 | 在冻结协议、独立标签和确认性证据到位前明确拒绝运行 |

## v4 四指标与空间输出

测点输入是：观测后区间状态、逐点速度、严格逐点加速度和改进切线角。速度与切线角合并成一个运动学证据族，加速度是独立证据族；原始 `ΔV` 只用于过程审计。候选色为三个族的最高等级。

加速度基线由 fit-only 稳定段候选求得：`A=mean(a)`、`σ_a=std(a,ddof=1)`、`A0=max(1.5A,A+2σ_a)`。导师已确认采用相同的五级相对阈值结构：green `< A0-σ_a`、blue `A0±σ_a`、yellow 至 `5A0`、orange 至 `10A0`、red `≥10A0`。这是当前案例的可复算操作化，不是现场验证的预警性能结论。

滑坡体输出分为两轴：

- `site_confirmed_level`：跨测点、跨空间块确认后的整体候选；
- `local_max_candidate_level`：任何可评估测点的最高局部候选。

因此“整体无色”不等于没有局部异常，也不等于数据缺失。green 仅指当前规则下的候选状态，不能写作现场安全结论。

## NGBoost 区间代理 pilot

该显式阶段使用时刻 `t` 的区间标准化偏离、逐点速度、`ΔV` 和连续切线角，预测 `t+1` 的五级原始区间偏离代理状态。8 个测点 one-hot 仅作为控制变量；物理加速度和环境变量不进入模型。模型只在 fit 训练，calibration/test 只评价，不调参、不重拟合、不做样本合成或事后校准。

pilot 已完整输出 11,376 条一日配对记录和五级概率。NGBoost 在 calibration/test 全部时刻的 accuracy 为 `0.906/0.947`，略低于状态持续基线的 `0.916/0.952`；状态转移行 accuracy 为 `0.132/0.209`。因此它只作为技术可行性和概率诊断支路保留，不进入默认链，也不授权修改 ConvLSTM、v4 或正式论文结论。

提前量敏感性保持相同模型、输入和训练策略，并列运行 h=1/3/7。三个 horizon 在 calibration/test 的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过各自持续基线；随着 horizon 增加，log loss、Brier 和 ECE 整体升高。转移行存在有限信息，但未在时段和指标间稳定一致，因此敏感性只作非排名诊断，不输出“最佳”或“选定”提前量。

七组输入消融进一步表明：去掉 `interval_z` 会使全时刻 macro-F1、ordinal MAE 和 log loss 大幅恶化，而仅区间模型已接近 full，说明当前代理标签任务由区间持续性主导；但仅区间在六个 horizon×split 的转移行 macro-F1 和 ordinal MAE 上均差于 full，非区间指标对状态变化仍有增量。其中去掉 `ΔV` 后转移行 ordinal MAE 在六个时段全部恶化，是最一致的增量证据。分别去掉速度、切线角或 station one-hot 的影响较小且方向混合，不能据此宣称单项必要或跨点可迁移。消融不排名、不选特征集，也不改变不引入主流程的判断。

## 版本化与清理原则

1. 当前代码只运行 v4；历史脚本、旧运行入口和对应单元测试从工作树删除，保留在 Git 历史。
2. 历史 v1/v2/v3 CSV、配置和文档中的结果描述可以作为已发生实验的溯源快照，但不再是当前可执行方法。
3. 当前 v4 的共享空间融合已使用中性模块名；历史 v1/v2/v3 的代码只保留在 Git 历史，不在工作树中提供运行入口。
4. 任何正式 NGBoost 预警模型必须以独立结局标签训练和验证，不能把同一四指标透明规则生成的标签再包装成正式预警验证。
