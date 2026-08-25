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

fit cumulative displacement + real elapsed time + point velocity
  └─ warning/auto_v0_direct_bai_perron.py
       └─ figures/auto_v0_direct_bai_perron_ootang_v1/*

automatic V0 candidates + raw kinematics + raw ConvLSTM intervals
  + historical v4 reference artifacts
  └─ warning/ootang_v5_candidate_display.py
       └─ figures/v5_candidate_display_ootang_v1/*

5-seed strict-temporal OOF P50 + persistence
  + versioned prequential monitor contract
  └─ monitoring/ootang_prequential_monitor.py
       ├─ online expert weighting + symmetric conformal/ACI
       ├─ one-sided residual anomaly + drift/reset/abstain
       ├─ O1/O2/O3 continuous spatial aggregation
       └─ figures/prequential_anomaly_ootang_v1/*

future frozen protocol + independent outcome labels
  └─ warning/formal_warning.py
       └─ formal warning artifacts (not implemented; current gate rejects)
```

`main.py` contains fourteen independently selectable stages. Its no-argument chain is exactly
`features → convlstm → ootang-operational-v4`; independent SHAP, ConvLSTM diagnostics,
the prequential monitor, NGBoost proxy experiments, automatic V0, and the v5 candidate display
all require `--stage`.

## 模块职责

| 模块 | 当前职责 | 主要输出 / 边界 |
| --- | --- | --- |
| `code/features/kinematics.py` | 计算真实时间间隔下的逐点速度、原始 `ΔV` 和加速度 | `v_i=(U_i-U_{i-1})/Δt_i`；`a_i=(v_i-v_{i-1})/Δt_i`；速度一行、加速度两行 warmup 明示 |
| `code/features/build_features.py` | 生成环境、水文、运动学和切线角特征 | 不构造旧 30 日 `V0` 分类标签 |
| `code/convlstm/model.py` | 以 7 通道输入预测全部八测点位移 P10/P50/P90 | 输出训练/校准/测试分段图、预测 CSV、覆盖率和运行 manifest |
| `code/convlstm/rolling_validation.py`、`seed_stability.py` | 固定 7 通道的滚动和多种子诊断 | 当前显式阶段；不将历史 6 通道结果当作 7 通道证据 |
| `code/monitoring/ootang_prequential_monitor.py` | 对 5-seed 严格时序 OOF + persistence 做同日先 issue 后 reveal 的机器在线组合、校准、漂移和连续空间聚合 | 当前只物化 E1 回顾性 replay；不逐日人工选样本/阈值，不输出颜色、灾害概率或正式预警；E2 live ingest/ledger runner 待实现 |
| `code/explainability/ngboost_shap.py` | 独立 NGBoost 回归及 permutation SHAP | 解释的是 `U_t-U_{t-1}` 模型依赖；当前只运行单一冻结时序留出，不解释 ConvLSTM、不推断物理因果、不输出预警分类。若需跨折稳定性，须另行冻结协议和计算预算 |
| `code/warning/ootang_ngboost_interval_proxy_pilot.py` | 用四项连续指标训练固定 NGBoost 五分类 pilot，预测下一日原始区间偏离状态 | 仅显式运行；标签是代理状态，当前结果未超过持续性基线，不替换 ConvLSTM/v4，也不读取其他案例 |
| `code/warning/ootang_ngboost_interval_proxy_horizon_sensitivity.py` | 在同一模型/输入/训练策略下并列运行 h=1/3/7 | 只报告非排名敏感性；不选择 horizon，所有提前量的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过持续基线 |
| `code/warning/ootang_ngboost_interval_proxy_feature_ablation.py` | 固定模型与 horizon，对七组输入进行 21 次分组消融 | 不保存消融模型、不排名；区间主导代理任务，`ΔV` 对状态转移的增量最一致 |
| `code/warning/auto_v0_direct_bai_perron.py` | 只用 fit 累计位移、真实时间轴和逐点速度生成自动 V0 候选 | MJ1/MJ3 可用，其余 6 点 unavailable；不人工选段、不回退 KMeans、不改写 v4 |
| `code/warning/ootang_v5_candidate_display.py` | 将自动 V0 可用性、原始速度/`ΔV`、连续切线角、原始区间状态和历史 v4 参考列物化为 8 点 × 514 日候选展示 | 只显式运行；不补 V0、不调用 v4 融合、不输出 NGBoost 概率、候选颜色或正式预警 |
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

## 自动 V0 候选诊断

`warning/auto_v0_direct_bai_perron.py` 的候选数值计算只使用藕塘 fit 累计位移、实际经过时间和逐点速度；运行器还只读 ConvLSTM predictions、forecast manifest、v4 thresholds 与 v4 manifest，以锁定 fit 边界并验证来源血缘，但这些字段不进入分段或 V0 公式。时间感知的 BIC 分段线性算法自动确定候选初始段。每个候选段从自身起点重定时间和位移原点后计算局部 OLS 充分统计量，避免晚期短段从全历史 float64 前缀原始矩相减产生负 SSE；随后仍使用同一最小段长、动态规划、BIC 和首段接受规则。共享 v4 Bai--Perron 实现不变。第一段正斜率且下一段斜率更高时，自动生成候选；若全 fit 只是一段正线性基线，则标记为 `stable_full_fit_baseline`；其他情况为 `unavailable`。本阶段不人工指定日期、不回退 KMeans、不使用 MVIF 失败结果、不写入 v4，也不输出预警等级。

当前 8 个测点中 MJ1/MJ3 形成候选 V0（约 `0.2503/0.2481 mm/day`）；ATU1--ATU5 因首个断点后没有更快而 unavailable，MJ9 因首段斜率非正而 unavailable。八站均保留 BIC 选定分段，数值失败不再与科学门禁混为一谈。这仍是自动流程的候选诊断结果，不应通过放宽门禁或人工补段来“补齐”8 点。结果与拟合段、断点和状态标签保存在 `figures/auto_v0_direct_bai_perron_ootang_v1/`，根因审计见 `docs/v5_v0_numerical_audit.md`，供后续 v5 候选审核。

## v5 候选展示

`warning/ootang_v5_candidate_display.py` 只消费已验证的自动 V0 bundle、藕塘原始运动学、ConvLSTM 原始分位数和当前 v4 历史参考产物。输出保留 calibration/test 的完整 514 日 × 8 点网格：MJ1/MJ3 共 1,028 行标记 `candidate_available`，其余 6 点共 3,084 行标记 `not_applicable_v0_unavailable`。自动 V0 相关速度比与切线角只在前两点计算，任何 unavailable 行都不补值。

该阶段只提供候选输入和可用性审计。manifest 固定 `candidate_display_only=true`、`ngboost_inference_output=false`、`v5_fusion_output=false`、`formal_warning_output=false` 和 `vajont_used=false`；没有模型文件、候选颜色或 8 点综合等级。v4 station/site 列仅以 `v4_reference_*` 命名保留，不参与新计算。

## 机器 prequential 监测支路

`monitoring/ootang_prequential_monitor.py` 消费固定的 5-seed、3-fold OOF P50，
并始终保留 persistence 专家。每个日期先从旧状态生成全部 8 点 issue 和 batch
hash，再统一读取同日 outcome，更新站点特异专家权重、绝对残差双侧
conformal/ACI、正向低估残差 anomaly 与 ADWIN-inspired 漂移状态。fold 切换和
漂移都由合同自动重置，重热期自动 abstain；O1/O2/O3 用 block max 与跨区 min
保留连续分数，不做颜色阈值。

当前实现是 E1 retrospective replay：源文件会整体载入校验，但同日 `actual`
不进入 issue 载荷、issue-time 状态或 issue hash。三个 fold 的在线状态彼此重置，
issue batch 则形成一条 run-wide 审计链。它不是 E2 实时追加账本，也没有证明
灾害风险。E2 需要另行实现自动 live ingest、outcome 隔离、append-only 事件
ledger、恢复/修订和时间锚；这些运行操作仍应由机器完成，而不是逐日人工冻结。

## 版本化与清理原则

1. 当前默认预警阶段只运行 v4；历史脚本、旧运行入口和对应单元测试从工作树删除，保留在 Git 历史。
2. 历史 v1/v2/v3 CSV、配置和文档中的结果描述可以作为已发生实验的溯源快照，但不再是当前可执行方法。
3. 当前 v4 的共享空间融合已使用中性模块名；历史 v1/v2/v3 的代码只保留在 Git 历史，不在工作树中提供运行入口。
4. 任何正式 NGBoost 预警模型必须以独立结局标签训练和验证，不能把同一四指标透明规则生成的标签再包装成正式预警验证。
