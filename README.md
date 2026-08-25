# Landslide-Warning

藕塘水库滑坡日尺度案例的位移概率预测和多测点预警原型。当前仓库只保留可执行的当前技术路线；已退役的 30 日 `V0` 标签、旧融合与旧运行入口仅在 Git 历史中保留。

## 当前结论边界

- 默认链为 `features → convlstm → ootang-operational-v4`，均为藕塘内部的**非正式原型**。所有 v4 输出均标记 `formal_warning_output=false`、`vajont_used=false`。
- ConvLSTM 独立输出全部 8 个测点的 P10/P50/P90 位移预测，以及训练、校准和测试时段的图表与覆盖率诊断。
- 显式阶段 `ootang-prequential-monitor` 已把 5-seed、3-fold 严格时序 OOF
  预测与 persistence 组成无需逐日人工操作的 E1 机器回放：在线专家加权、双侧
  conformal/ACI 区间、单侧残差 anomaly、自动漂移重置/abstain 和 O1/O2/O3
  连续空间聚合均只使用更早 outcome 更新。三折 MAE skill 均为正，但 RMSE
  优势不稳定，区间覆盖率由 `0.791` 降至 `0.695/0.631`，因此它是内部回顾性
  科研监测器，不是灾害真值、风险概率或正式预警。
- 显式阶段 `ootang-prequential-live` 已实现 E2-A 单次机器 poll、issue/outcome
  隔离、SQLite append-only ledger、等待/回填/恢复/修订、数学全重放与外部锚
  接口。E2-B1 又增加 `ootang-live-source → ootang-production-bundle →
  ootang-issue-producer`：严格 finalized feed、内容寻址 source、固定 5-seed 安全
  checkpoint 及内部推理签发均由机器完成；runtime 路径、activation 指针、checkpoint
  单次字节加载、ledger persistence 和发布前后时间屏障均 fail closed。E2-B2
  再增加机器 outcome materializer 与 fixed-point cycle：source pointer v2 由每日
  revision receipt 和全局 snapshot receipt 链锁定，outcome 以 receipt 链、active
  pointer 和 inbox 分层发布并可自动恢复，cycle 依固定顺序运行直到科学
  状态不再变化。它不提供人工日期、冻结、批准或补签入口。runner 独立
  checkpoint/input 重放、可信密码学时间、自动 epoch registry/rotation 与
  长链扫描优化仍是门禁，因此整条链固定为 engineering-only，不计入 E2
  live evidence，也不输出正式预警。
- 独立 NGBoost 回归 + SHAP 用于识别候选模型依赖；它不是 ConvLSTM 的 SHAP，也不构成因果主控因素或正式预警分类器。
- 显式阶段 `ootang-ngboost-interval-proxy-pilot` 使用四项指标预测下一日五级区间风险代理状态；它不替换 ConvLSTM 或 v4，也未使用其他案例。当前 calibration/test 全时刻表现均略低于状态持续基线，故暂不引入主流程。
- 显式敏感性阶段以完全相同的 NGBoost、输入和训练协议并列运行 h=1/3/7；三个提前量的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过各自持续基线，且概率质量随提前量增加而减弱。本结果不排名或选择 horizon。
- 显式分组消融显示当前区间代理任务由 `interval_z` 主导；非区间指标主要在状态转移行提供增量，其中 `ΔV` 的转移贡献最一致，速度/切线角和测点控制量的单独影响较小且不稳定。消融不排名或选择特征集，也不改变“不引入主流程”的判断。
- 显式阶段 `ootang-auto-v0-direct-bai-perron` 只用 fit 累计位移自动做 BIC 分段，生成每测点 V0 候选；当前 8 点中 2 点可用、6 点 unavailable。段内局部统计已通过负 SSE 数值审计；它不改写 v4，也不使用人工日期范围或 KMeans 回退。
- 显式阶段 `ootang-v5-candidate-display` 保留全部 8 点 × 514 个结果时刻：MJ1/MJ3 显示自动 V0 相关速度比、`ΔV` 和连续切线角，其余 6 点明确 `not_applicable_v0_unavailable`。该阶段不运行新的 NGBoost 推断、不生成候选颜色或融合结果，也不改写 v4。
- v4 按导师确认的逐点方法计算速度和加速度：
  `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`，
  `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`。
  加速度以每测点 fit-only 的 `A0=max(1.5A,A+2σ_a)` 为基准，沿用课题组确认的 `1×/5×/10×` 五级相对结构。
- 当前测点候选融合使用区间、速度/改进切线角（同一运动学证据族）和加速度三族；原始 `ΔV` 仅保留审计，不重复投票。空间层输出“整体确认”和“局部最高候选”两条轴。
- 数据源是发布的物化日序列，原始 GNSS 及完整生成血缘不可得。因此 `prototype_run_gate=allowed`，`confirmatory_evidence_gate=blocked`；不得把本案例表述为已验证的现场正式预警。

截至当前 v4 结果含 4,112 条测点—时刻记录、514 条滑坡体结果和 8 行阈值。加速度单项 green/blue/yellow/orange/red 为 `4012/98/2/0/0`；滑坡体整体确认 green/blue/yellow/orange/red 为 `8/48/31/9/18`，另有 400 日因空间确认条件未满足而不发布整体颜色。

## 快速运行

项目使用 `uv`，Python 3.10：

```bash
uv sync
uv run python main.py
```

无参数只运行 `features → convlstm → ootang-operational-v4`。其余当前诊断需显式选择：

```bash
uv run python main.py --list
uv run python main.py --stage ngboost-shap
uv run python main.py --stage ootang-ngboost-interval-proxy-pilot
uv run python main.py --stage ootang-ngboost-interval-proxy-horizon-sensitivity
uv run python main.py --stage ootang-ngboost-interval-proxy-feature-ablation
uv run python main.py --stage ootang-auto-v0-direct-bai-perron --stage ootang-v5-candidate-display
uv run python main.py --stage ootang-prequential-monitor
uv run python main.py --stage ootang-live-source --stage ootang-production-bundle --stage ootang-issue-producer --stage ootang-prequential-live
uv run python main.py --stage ootang-outcome-materializer
uv run python main.py --stage ootang-prequential-cycle
uv run python main.py --stage convlstm-rolling --stage convlstm-seeds
```

每个阶段声明输入输出，管线在运行前后检查文件新鲜度，并将提交、输入输出 SHA-256、状态与耗时写入 `figures/pipeline/latest_run.json`。该文件当前不存在：原有清单是 2026-08-01 的 v3 阶段残留记录，已于 2026-08-15 删除，下次完整运行会重新生成。解释任何运行清单时须核对其自身提交和源码指纹。

Vajont 尚未启动；读取、适配或运行其数据前必须获得用户明确许可。

## 验证与历史边界

当前测试保护工作树中仍可执行的接口：默认链与显式阶段隔离、prequential
同日 issue/reveal 因果顺序与审计链、fit-only 自动 V0、v5 unavailable 门禁，
以及 v4 的非正式、fail-closed 证据与协议契约。可用以下命令复核：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
uv run ruff check code tests main.py
.venv/bin/python -m compileall -q code main.py tests
```

旧 30 日 `V0`、v1/v2/v3 运行入口和对应历史测试已从当前工作树移除；需要复现历史快照时，必须按提交从 Git 历史恢复，不应把旧产物当作当前 v4 接口或结果。

## 代码结构

```text
main.py                         # 当前管线入口（20 个可选阶段）
code/features/                  # 特征、逐点运动学、切线角
code/convlstm/                  # 概率位移预测与时间验证诊断
code/explainability/            # 独立 NGBoost 回归与 SHAP
code/monitoring/                # 机器 prequential 预测、校准、漂移和连续异常
code/warning/                   # v4 历史规则、自动 V0 与 v5 候选展示门禁
data/                           # 发布物化序列、坐标和派生特征
figures/                        # 版本化预测、规则审计和图件
docs/                           # 当前方法、结果边界和研究计划
```

`code/warning/operational_spatial_fusion.py` 是 v4 当前调用的共享双轴空间融合实现；旧 v3 运行入口及其专属实现只保留在 Git 历史。

## 主要文档和结果

| 文件 | 内容 |
| --- | --- |
| [`docs/design.md`](docs/design.md) | 当前代码架构、输入输出和非正式边界 |
| [`docs/ootang_operational_run.md`](docs/ootang_operational_run.md) | v4 四指标、加速度阈值和多测点双轴规则 |
| [`docs/ootang_stage_results_package.md`](docs/ootang_stage_results_package.md) | 藕塘阶段性结论与可写/不可写边界 |
| [`docs/advisor_review_action_plan.md`](docs/advisor_review_action_plan.md) | 导师意见逐项状态与下一步门禁 |
| [`figures/convlstm/forecast_all_stations.png`](figures/convlstm/forecast_all_stations.png) | 全测点概率位移预测及训练/结果分段 |
| [`figures/shap/ngboost_regression_shap.png`](figures/shap/ngboost_regression_shap.png) | 独立 NGBoost 回归的候选模型依赖 SHAP 图 |
| [`docs/ootang_ngboost_interval_proxy_pilot.md`](docs/ootang_ngboost_interval_proxy_pilot.md) | NGBoost 下一日五级区间代理试验、基线比较和不引入主流程的当前判断 |
| [`docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md`](docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md) | 固定 NGBoost 的 h=1/3/7 非排名提前量敏感性和概率质量诊断 |
| [`docs/ootang_ngboost_interval_proxy_feature_ablation.md`](docs/ootang_ngboost_interval_proxy_feature_ablation.md) | 七组固定输入的非排名消融及四指标增量信息边界 |
| [`docs/v5_v0_numerical_audit.md`](docs/v5_v0_numerical_audit.md) | ATU3/MJ9 负 SSE 的复现、根因、修复和保护性验证记录 |
| [`docs/v5_validation_protocol.md`](docs/v5_validation_protocol.md) | 正式 v5 的标签、切分、指标、V0 unavailable 与融合决策门 |
| [`docs/ootang_autonomous_research_protocol.md`](docs/ootang_autonomous_research_protocol.md) | 不依赖逐日人工操作的 E0--E3 机器闭环协议及科学边界 |
| [`docs/ootang_prequential_monitor_results.md`](docs/ootang_prequential_monitor_results.md) | E1 三折回放结果、确定性/因果校验、产物哈希与当前限制 |
| [`docs/ootang_prequential_live_engineering.md`](docs/ootang_prequential_live_engineering.md) | E2-A ledger/runner 实现、验证、状态语义和 E2-B 激活门禁 |
| [`docs/ootang_prequential_deploy_engineering.md`](docs/ootang_prequential_deploy_engineering.md) | E2-B1 finalized source、五种子安全 bundle、机器 issue producer 与剩余闭环门禁 |
| [`docs/ootang_prequential_cycle_engineering.md`](docs/ootang_prequential_cycle_engineering.md) | E2-B2 source receipt 加固、机器 outcome 物化、固定点 cycle 与恢复边界 |
| [`figures/auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.png`](figures/auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.png) | 8 个测点 fit-only 自动 BIC 分段与 V0 候选状态 |
| [`figures/v5_candidate_display_ootang_v1/candidate_display.png`](figures/v5_candidate_display_ootang_v1/candidate_display.png) | MJ1/MJ3 候选输入与其余 6 点 unavailable 状态；无 NGBoost 推断或 v5 融合 |
| [`figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg`](figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg) | 514 个结果时刻的测点候选与滑坡体双轴状态 |
| [`docs/progress.md`](docs/progress.md) | 当前实现进度、已清理历史代码与未完成门禁 |

## 尚未完成的关键事项

1. E2-B2 已实现 machine-only outcome materializer、receipt/pointer/inbox 崩溃恢复
   和有界 fixed-point cycle。剩余门禁是 runner 独立 checkpoint/input 重放、
   可信密码学时间、immutable epoch registry/自动轮换，以及避免长链
   receipt/ledger 重复扫描的 O(N²) 性能优化。在这些门关闭前保持
   `real_activation_ready=false`。
2. 当前 E1 区间覆盖随 fold 下降，后续应以新版本预声明比较 SPCI/AgACI 等
   challenger，不能在已经查看的回放输出上反复调参并回写 v1。
3. 正式灾害效能仍需与模型输出相互独立、机器可读且带可见时间的结局源。
   自动化可以消除逐日人工操作，但不能从自身残差制造独立灾害真值。
4. NGBoost 代理、自动 V0 和 v5 candidate display 仍是另一条非正式支路；其
   G1--G4 blocked 状态不阻止 machine-only 位移预测研究，也不得被后者绕过。
5. 只有获得用户授权后，才启动 Vajont 的数据适配与外部案例评估。
