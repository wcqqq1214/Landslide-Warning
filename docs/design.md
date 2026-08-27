# 当前代码设计

本文件只描述当前工作树中可执行的藕塘原型。旧 30 日 `V0` 标签及旧预警融合
v1/v2/v3 运行入口与测试已于 2026-08-13 从当前树移除；这不指当前
machine-prequential cycle v1/v2/v3。若需复现旧预警，使用 Git 历史而不是当前默认或显式管线。

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

protected E1 point forecasts + E1 fold/drift reset schedule
  + versioned calibration bakeoff contract
  └─ monitoring/ootang_prequential_calibration_bakeoff.py
       ├─ exact ACI v1 control
       ├─ AgACI-inspired EWA endpoint aggregation (explicitly not BOA)
       ├─ signed-residual SPCI-QRF
       └─ figures/prequential_calibration_bakeoff_ootang_v1/*

machine source/model manifests + separated issue/outcome inboxes
  ├─ monitoring/ootang_live_source.py
  ├─ convlstm/ootang_production_bundle.py
  ├─ monitoring/ootang_issue_producer.py
  ├─ monitoring/ootang_outcome_materializer.py
  ├─ monitoring/ootang_prequential_live.py
  ├─ monitoring/ootang_prequential_cycle.py
  ├─ monitoring/prequential_core.py (pure issue/reveal/site mathematics)
  ├─ monitoring/ootang_live_ledger.py (SQLite WAL append-only hash chain)
  └─ runtime/ootang_prequential_live_v1/
       ├─ source_snapshot + source_current v2 + model_bundle
       ├─ objects + source snapshot/revision receipt chains
       ├─ issue/outcome receipts + active pointers + inboxes
       └─ cycle/status + ledger + anchors + locks

verified E2-A live projection + fixed calibration contract
  ├─ monitoring/ootang_calibration_shadow_ledger.py
  ├─ monitoring/ootang_prequential_calibration_shadow.py
  ├─ monitoring/ootang_prequential_cycle_v2.py
  └─ runtime/ootang_prequential_calibration_shadow_v1/
       └─ independent ledger + replayable status + runner lock

current source + exact issue/input + fixed five-seed bundle
  ├─ monitoring/ootang_issue_replay.py
  │    └─ independent strict checkpoint/input replay receipt
  ├─ monitoring/ootang_verified_live.py
  │    └─ runner-lock guarded intent -> live append -> completion link
  ├─ monitoring/ootang_prequential_cycle_v3.py
  │    └─ 13-stage replay-gated calibration-shadow fixed point
  └─ runtime/ootang_prequential_live_v1/
       ├─ issue_replay_receipts + replay status/lock
       └─ verified_live_intents + verified_live_completions + status

verified replay completion + pinned RFC 3161 trust bundle
  ├─ monitoring/ootang_trusted_time_shadow.py
  │    └─ stdlib bootstrap verifier -> isolated/frozen exact Python runtime
  ├─ monitoring/ootang_trusted_time_shadow_core.py
  │    └─ canonical request -> TSA response -> live/guard-bound cryptographic replay
  ├─ tools/ootang_trusted_time_runtime/{pyproject.toml,uv.lock}
  ├─ config/trust/sigstore_tsa_2025_{manifest.v1.json,leaf.pem,root.pem}
  └─ runtime/ootang_prequential_live_v1/
       └─ trusted-time request/response/object/receipt + shadow status

fixed finalized feed + reviewed execution contract
  ├─ monitoring/ootang_epoch_registry.py
  │    └─ stable slot prebuild -> content-addressed archival byte capsule
  │         -> create-only candidate-ready registry chain
  ├─ monitoring/ootang_epoch_preparation.py
  │    └─ exact 22-module closure + two reviewed augmentations
  │         -> same-origin executable tree + two frozen isolated smoke domains
  │         -> candidate-prepared/revalidated chain
  └─ monitoring/ootang_epoch_drain.py
       └─ all-lock clean-start fence -> atomic issue-inbox/tombstone swap
            -> independent epoch-drain-started chain -> DRAINING only

future frozen protocol + independent outcome labels
  └─ warning/formal_warning.py
       └─ formal warning artifacts (not implemented; current gate rejects)
```

`main.py` contains thirty independently selectable stages. Its no-argument chain is exactly
`features → convlstm → ootang-operational-v4`; independent SHAP, ConvLSTM diagnostics,
the prequential monitor/deployment stages, NGBoost proxy experiments, automatic V0, and the v5
candidate display all require `--stage`.

## 模块职责

| 模块 | 当前职责 | 主要输出 / 边界 |
| --- | --- | --- |
| `code/features/kinematics.py` | 计算真实时间间隔下的逐点速度、原始 `ΔV` 和加速度 | `v_i=(U_i-U_{i-1})/Δt_i`；`a_i=(v_i-v_{i-1})/Δt_i`；速度一行、加速度两行 warmup 明示 |
| `code/features/build_features.py` | 生成环境、水文、运动学和切线角特征 | 不构造旧 30 日 `V0` 分类标签 |
| `code/convlstm/model.py` | 以 7 通道输入预测全部八测点位移 P10/P50/P90 | 输出训练/校准/测试分段图、预测 CSV、覆盖率和运行 manifest |
| `code/convlstm/rolling_validation.py`、`seed_stability.py` | 固定 7 通道的滚动和多种子诊断 | 当前显式阶段；不将历史 6 通道结果当作 7 通道证据 |
| `code/monitoring/ootang_prequential_monitor.py` | 对 5-seed 严格时序 OOF + persistence 做同日先 issue 后 reveal 的机器在线组合、校准、漂移和连续空间聚合 | 物化 E1 回顾性 replay；不逐日人工选样本/阈值，不输出颜色、灾害概率或正式预警 |
| `code/monitoring/calibration_challengers.py` | 提供不可变 ACI、AgACI-EWA 与 SPCI-QRF station-local issue/reveal 数学及 canonical state hash | AgACI 是诚实命名的 EWA 变体而非 BOA 复现；SPCI 用 signed residual 与最新到最旧 lag；无文件或时钟依赖 |
| `code/monitoring/ootang_prequential_calibration_bakeoff.py` | 在受保护 E1 point forecast 上先签发全部 24 个 candidate issue、后 reveal，输出 coverage/width/score 与配对比较 | 回顾性描述、禁止 ranking/selection/promotion；不修改 E1、live ledger、模型或正式预警 |
| `code/monitoring/prequential_core.py` | 提供不可变 StationState 及 issue/reveal/site 纯数学 | 与 E1 v1 数学逐字段等价；不访问文件、时钟或网络 |
| `code/monitoring/ootang_live_ledger.py` | 提供 SQLite WAL 追加式事务、自然键幂等、schema trigger 和完整哈希链验证 | SHA-256 证明内部内容一致性，不证明作者身份或可信时间 |
| `code/monitoring/ootang_live_source.py` | 严格 ingest finalized 日 feed，在可信代码内派生特征并物化 content-addressed current/activation source | current pointer v2 绑定每日 revision receipt 链与全局 snapshot receipt 唯一 tip；已验证的缺失/陈旧 pointer 可恢复，回退、分支、孤儿或篡改 fail closed |
| `code/convlstm/ootang_production_bundle.py` | 从 immutable activation source 构建固定 5-seed 全 as-of bundle，并安全重载 checkpoint | 不选 best seed；`weights_only=True`，精确绑定预处理、source、实现与依赖；低 epoch 仅测试路径 |
| `code/monitoring/ootang_issue_producer.py` | 按 verified ledger next-target 从五 checkpoint 内部推理并原子发布下一自然日 issue | runner lock 内只读全链重放；只用 watermark 前最后 7 行；exact-byte object + 首发 receipt；不跳日、不回填过去 issue、同语义幂等、异语义拒绝 |
| `code/monitoring/ootang_outcome_materializer.py` | 从已验证 immutable source provenance 按 revision 优先、sealed outstanding、连续 backfill 的固定规则机器物化 outcome | per-target revision receipt 链、唯一 tip、active pointer 与 inbox 支持崩溃恢复；activation watermark 及更早修订进入 `waiting_epoch_rotation_required`，不人工冻结或回写 epoch |
| `code/monitoring/ootang_prequential_live.py` | 执行 E2-A 单次机器 poll、冷启动、等待、回填、issue/seal、anchor 接口、outcome/update、修订和全重放 | 原 v1 保持不变且自身仍不重放 checkpoint；指定 replay-gated 入口由 additive wrapper 提供，旧 CLI 尚未系统级禁用，E2 evidence 固定 false |
| `code/monitoring/ootang_prequential_cycle.py` | 以固定七步顺序反复调用 source/bundle/live/outcome/issue，直到验证科学状态达到固定点 | 时间戳、raw ledger head 和失败 anchor retry 不影响 progress token；有界 continuation、跨调用振荡检测、非阻塞锁、严格状态复验；无人工日期/冻结/批准字段 |
| `code/monitoring/ootang_calibration_shadow_ledger.py` | 为三种固定校准器提供独立 SQLite STRICT 追加账本、事务摘要、全局链和完整 schema/chain 重放 | 与 live v1 application id 和事件类型隔离；冲突 insert/replace、update/delete 与同键异语义均拒绝；hash chain 不证明作者身份或可信时间 |
| `code/monitoring/ootang_prequential_calibration_shadow.py` | 从 verified live projection 按 source sequence 自动执行 24 项 issue、reveal、状态更新、backfill 排除、revision rescore、drift reset 与 shadow epoch 冷启动 | activation 时已有 issue 不计未来支持；漏签 settlement 不补 issue；revision 不改在线状态；只计算 engineering readiness，不选择或晋升 |
| `code/monitoring/ootang_prequential_cycle_v2.py` | 在 v1 七步闭环周围插入四个 shadow reconcile，形成 11 阶段机器 fixed point | 复用同一 outer lock；live/shadow runtime 分离；`work_remaining` 有界续跑，稳定 token 与未完成工作冲突时 fail closed；无人工日期/冻结/批准字段 |
| `code/monitoring/ootang_issue_replay.py` | 从递归验证的 current source 尾七日和五个 checkpoint 独立重建 IDW、7-channel preprocessing、ConvLSTM forward、readout 与 P50 | persistence 精确核对；40 个 P50 只用 `rtol=0, atol=1e-6 mm`；create-only per-target receipt；不读取同日 outcome，不调用 producer/bundle 核心预测实现 |
| `code/monitoring/ootang_verified_live.py` | 持有 live-v1 runner lock，按 replay receipt → seal intent → live issue transaction → completion link 推进至多一个科学 transition | intent 后/append 前崩溃可安全重试；append 后/completion 前先恢复链接再允许 outcome；无 intent 的直接 v1 seal 永不事后追认；旧 v1 入口仍可绕过，待 scheduler authorization |
| `code/monitoring/ootang_prequential_cycle_v3.py` | 在 shadow cycle 上加入前置/签发后 replay，并将全部 live transition 改走 verified-live，形成 13 阶段机器 fixed point | progress token 绑定 replay/intent/completion 科学身份；保留 64 轮、continuation、振荡检测、busy/blocked 语义；仍无人工日期/冻结/批准字段 |
| `code/monitoring/ootang_trusted_time_shadow.py` + `ootang_trusted_time_shadow_core.py` | stdlib launcher 在精确 CPython/隔离冻结依赖中，为已完成的 replay-gated issue seal 生成固定 policy、256-bit nonce 的 RFC 3161 请求；公开 reload 从 live/guard history 重建 envelope，再从 raw TSR 复验 CMS、固定 leaf/root、消息、nonce、accuracy 与 UTC+08:00 目标日前因果条件 | standalone additive shadow；core 硬固定 Sigstore trust，bootstrap 拒绝代码/锁/环境注入；不读 outcome、不改旧 anchor、不进入 cycle v3，所有 E2/activation/formal 标志为 false；ESSCertIDv2 未单独解析 |
| `code/monitoring/ootang_epoch_registry.py` | 完整验证 finalized feed 后先写独立 observation/head 反回滚链，再派生稳定 slot、预构建 source/model；精确绑定 source-lineage feed，把固定代码/配置/锁/trust 与 runtime artifacts 保存为 content-addressed archival snapshots，最后追加严格 N→N+1 candidate-ready event | standalone R1；waiting/orphan 也保留 feed 水位，历史 replay 验证 immutable snapshots 而非以后可变的 slot；capsule allowlist 不是 executable transitive closure，不移动 slot、不创建 candidate ledger、不切 active、不读 outcome，automatic rotation/E2/activation/formal 均为 false；R2 另做 executable materialization、drain 与原子 switch |
| `code/monitoring/ootang_epoch_preparation.py` | 从 R1 immutable tip 静态解析 exact 22-module closure，只捕获两个固定 augmentation；将 closure、R2a profile/implementation 和绑定资源内容寻址并物化为同源执行树，在根/可信时间双 `uv --isolated --frozen` 环境重载 prerequisite 与五 seed P50 | standalone R2a；只接受 canonical project/slot，`relocatable=false`、`portable_offline_runtime=false`；每次 current repoll 和 orphan recovery 都重烟测，实现升级追加 `candidate_revalidated`；drain/switch/rotation/trusted anchor/E2/activation/formal 全为 false |
| `code/monitoring/ootang_epoch_drain.py` | 六锁下复验 clean old epoch；发布 full intent-prefix/capsule 后，在 tombstone mkdir 前 create-only 写永久 singleton fence-prepare，再建立 ACL-fenced 0755 tombstone；swap 前 worst-case/actual boundary capacity 通过后，发布 exact pre-swap boundary、append/replay `drain_exchange_attempts` WAL，并以 fence 内 armed marker 绑定 terminal+boundary，再 `RENAME_SWAP` 原子交换 route | 超 64 MiB 机器 capacity-waiting、route 不交换；正常 same-poll post-swap state 必须 exact；prepared retry 只恢复严格 temp/ACL state，exchanged recovery 从 armed terminal 复用旧 boundary并以 current extension 作 gate；WAL/marker/boundary 只有 recovery authority，唯一 lifecycle authority 是 event；无人工 cleanup，只进入 DRAINING，其余 claim 全 false |
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

E1 实现是 retrospective replay：源文件会整体载入校验，但同日 `actual`
不进入 issue 载荷、issue-time 状态或 issue hash。三个 fold 的在线状态彼此重置，
issue batch 则形成一条 run-wide 审计链。它不是 E2 实时追加账本，也没有证明
灾害风险。

独立显式 calibration bakeoff 复用 E1 的 6,888 个 point forecast、fold 和自动
drift reset，但为 ACI 控制、AgACI-EWA 变体和 SPCI-QRF 分别维护不可变站点状态。
每个日期必须先完成 3×8 个 issue 及 outcome-free batch hash，才允许读取同日
actual；修改同日 actual 不改变当日 issue，但会改变下一日状态。控制 ACI 的
alpha/区间逐 binary64 对齐 E1，其他方法不得改变 point forecast。产物只给出
planned/common support 上的覆盖、宽度、统一 central-80 score 与稳定性，不含 winner、rank、
selection 或 promotion 字段。当前结果只用于设计新的 E2 shadow 协议。

E2-A 已用独立 namespace 实现 SQLite append-only ledger、真实文件级
issue/outcome 隔离、完整八站事务、自动等待/回填/恢复/修订和时间锚接口。每次
恢复会从 genesis 重算 issue、reveal、score、expert/conformal/drift 状态与 site
聚合；本地回执文件可从账本恢复，链或 schema 漂移会 fail closed。历史 outcome
只能记为 `backfill_not_blind`，目标当日及更早日期禁止事后 issue。

E2-B1 已在 E2-A 前增加严格 finalized source ingest、内容寻址 activation/current
source、固定 5-seed 安全 checkpoint bundle、input-manifest 科学语义和内部 checkpoint
推理 issue producer。producer 在 runner lock 内用公开 read-only API 完整验证/重放
ledger，再核对 status；issue 首发 exact bytes 由内容寻址 object 与不可覆盖 receipt
锁定。runtime path/symlink confinement、activation pointer 交叉绑定、checkpoint
同一 bytes 的校验/加载、ledger latest persistence 门禁和提交前后新鲜 UTC 屏障均
fail closed。能力字段区分“代码已实现”和“本次运行已执行”，所以无 feed 时三个 producer
与 E2-A 只写等待态，不产生 source、model、issue 或 ledger。

E2-B2 已将 source pointer 升级为 v2，以每日 revision receipt 链和全局
snapshot receipt 链拒绝回退/分支，并可从已验证唯一 tip 恢复缺失或陈旧
current pointer；旧 pointer v1 不做静默迁移。machine-only outcome materializer
通过每目标 revision receipt 链、
active pointer 和 inbox 组成崩溃可恢复提交；早于或等于 activation watermark
的修订显式等待机器 epoch rotation。一次调用共享单调机器时钟，跨 receipt 恢复
或发布期间的时钟回退会撤销本轮公开状态并 fail closed。fixed-point cycle 按 source ingest → bundle
ensure → live reconcile → outcome materialize → live reconcile → issue produce →
live seal 固定顺序自动收敛；可恢复的 pre-genesis 空 ledger 不会在子阶段前
被阻断。缺输入是正常 waiting，不会转成人工冻结、日期或批准工作流。

E2 calibration shadow v1 在不修改 live/deploy/cycle v1 和校准 core 的前提下，
把 ACI、AgACI-EWA 与 SPCI-QRF 的 24 套 station-local 状态接到 verified live
projection。独立 ledger 先持久化同目标全部 candidate issue，随后才消费匹配的
settlement；漏签目标只记 backfill-ineligible，revision 只追加重评分且不更新在线
状态。runner 将 issue、settlement、backfill 和 revision 按 live sequence 统一合并，
避免长时间离线追赶时游标越过较早事件。cycle v2 在 source/outcome/issue 边界前后
自动对账，崩溃后从两条完整重放链恢复，不需要人工逐日冻结或批准。

预声明评估固定使用共同支持、至少 180 个未来目标日、逐站 coverage gap、相对 ACI
interval score、availability 和 30 日 rolling gate。状态可自动给出 engineering
readiness，但 schema 和配置固定 `selection_performed=false`、
`promotion_performed=false`、`e2_live_evidence_eligible=false`；达到工程门也不会
自动改写生产校准器。

additive replay/verified-live/cycle-v3 已对指定机器入口独立重放 producer 的
checkpoint/input，并把 pre-seal intent 与 live seal completion 交叉链接；standalone
RFC 3161 shadow 进一步用固定 provider/policy/leaf/root 签名回执验证该完成语义的
外部 proof-of-existence。它不改旧 JSON anchor，也尚未进入 cycle v3。原 E2-A
live-v1 CLI 仍可被直接调用，所以这不是系统级不可绕过授权；实现或模型变化也尚未
自动创建 immutable 新 epoch。receipt/ledger registry 的长链全量验证仍需避免
O(N²) 反复扫描。因此全部能力仍只输出工程时序候选，固定
`e2_live_evidence_eligible=false`。epoch registry R1 关闭 candidate 的稳定 slot、预构建和
verified-ready 记录基础；R2a 再物化 exact 22-module closure，内容寻址捕获自身
profile/implementation，并在精确 uv/Python/依赖指纹下做双 frozen isolated 烟测与
五种子 `predict_p50`/`reload_replay` 比较。该树必须保持原 project/slot，不是
portable offline runtime。R2b 首切片进一步在全锁临界区用不可降级的 macOS 原子 swap
撤销 canonical old-epoch issue route。capsule 引用内容寻址 full intent-prefix，保存
live/shadow 全 entry hashes 与 issue/outcome/guard/trusted/source inventories，每 poll 要求
start→current append-only。任何 tombstone mkdir 前先 create-only 发布永久 singleton
`fence_prepare`，绑定历史 R1/R2a、capsule/prefix、旧 route identity 和 ACL/swap policy；crash
或 current tip 推进后机器继续该历史 transaction，marker 自身没有 lifecycle authority。
intent 也只是 durable transaction reservation/lower-bound；`candidate_at_intent` 不是
activation selection。物理 issue-admission boundary 是 Darwin swap
成功的瞬间；权威 state-snapshot boundary 则是六锁下 full-clean replay 后由唯一
`epoch_drain_started` 引用的内容寻址 pre-swap 对象。后者绑定 live/issue/guard/trusted/outcome/shadow/
source inventories 与 staged next-epoch incoming，orphan object 不产生 authority。

R2b-1 只接受没有 outstanding 或 pending guard/trusted-time/shadow 的 clean start；其他情况
机器跨 poll 等待，旧工作可继续，合法 settled extension 在下一次 replay 中自动纳入，不通过
人工冻结、cleanup、日期、批准、force 或 backdate 清空状态。manifest/staged-feed 显式上限
分别为 64 MiB / 16 MiB；staged feed 独立内容寻址，合同内的大 feed 不会自锁。swap 前用
最大 staged CAS reference 做 worst-case boundary preflight；超 64 MiB 返回
`waiting_for_drain_boundary_capacity`，route 未交换、无 event、无人工 cleanup。通过后顺序
固定 final fence verify → actual queue stable capture/CAS → actual capacity → publish exact
pre-swap boundary → append/replay append-only `drain_exchange_attempts` terminal → 在 fence
operand 内写 `.epoch-drain-armed-attempt.v1.json` 直接绑定 terminal+boundary → immediate swap；
marker 随 inode 原子移动。每个 attempt 精确引用一个完整 pre-swap boundary，只能武装 unique
terminal。正常同 poll post-swap logical clean 必须 exact 等于 pre-swap；prepared retry 只自动
恢复严格单一 temp 与 exact/缺失 ACL crash state。exchanged crash recovery 必须读取 armed
terminal 的旧 boundary，current clean 只作合法 append-only extension gate，不能重建 boundary。
WAL suffix rollback/branch/gap/extra/symlink 一律 fail closed；WAL/marker/boundary 只有 recovery
authority，唯一 DRAINING lifecycle authority 仍是 event。post-swap publisher 与 event append
前仍完整自重放 boundary。R2b-2a 已在同一六锁下加入 deterministic machine-current clean
observation、previous-hash-linked event 与 stale detection：同 state 幂等，合法 settled
extension 二次 capture 后追加，pending/capacity 只 waiting；历史 observation 每次 replay
均重新解引用冻结 source artifacts。head/status 仍是非 authority cache，at/behind chain 的
tip 必须精确匹配，只有完整合法且 strictly-ahead 的 cache 才保留 rollback witness。
任何历史增长的 chunk/Merkle 设计属于未来 R2b-2b v2；当前 event 最多授权 DRAINING；
candidate selection、drained、active switch/rotation、trusted anchor、E2 evidence、activation
readiness 与 formal warning 全部保持 false。下一步以新 v2 schema 实现 R2b-2b
bounded-workset non-clean recovery，且不得重解释 v1 fence-prepare/intent-prefix/capsule/
intent/exchange-attempt/armed-marker/boundary/drain-event/eligibility-observation/event bytes。
之后才是独立 drain
assessor、权威原子 active transition、cycle v4、scheduler authorization 和长链 O(N²) 优化。

## 版本化与清理原则

1. 当前默认预警阶段只运行 v4；历史预警脚本、旧运行入口和对应单元测试从工作树删除，保留在 Git 历史。
2. 历史预警 v1/v2/v3 CSV、配置和文档中的结果描述可以作为已发生实验的溯源快照，但不再是当前可执行预警方法。
3. 当前 v4 的共享空间融合已使用中性模块名；历史预警 v1/v2/v3 的代码只保留在 Git 历史，不在工作树中提供运行入口。当前 machine-prequential cycle v1/v2/v3 不属于这组退役代码。
4. 任何正式 NGBoost 预警模型必须以独立结局标签训练和验证，不能把同一四指标透明规则生成的标签再包装成正式预警验证。
