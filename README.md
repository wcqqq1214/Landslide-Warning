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
- 显式阶段 `ootang-prequential-calibration-bakeoff` 在完全相同的 E1 point
  forecast 和自动 reset schedule 上，因果并列比较 ACI 控制、明确标注为非 BOA
  的 AgACI-EWA 变体及 SPCI-QRF。AgACI-EWA 在 fold 2/3 缩小覆盖误差且三折区间
  更窄，但 fold 1 过覆盖；固定 SPCI 配置三折均明显欠覆盖。本结果只支持把固定
  三方法送入未来 E2 shadow 检验，其中 EWA 是改善信号；不执行回顾性排名或晋升，
  也不改写 E1/E2 v1。
- 显式阶段 `ootang-prequential-live` 已实现 E2-A 单次机器 poll、issue/outcome
  隔离、SQLite append-only ledger、等待/回填/恢复/修订、数学全重放与外部锚
  接口。E2-B1 又增加 `ootang-live-source → ootang-production-bundle →
  ootang-issue-producer`：严格 finalized feed、内容寻址 source、固定 5-seed 安全
  checkpoint 及内部推理签发均由机器完成；runtime 路径、activation 指针、checkpoint
  单次字节加载、ledger persistence 和发布前后时间屏障均 fail closed。E2-B2
  再增加机器 outcome materializer 与 fixed-point cycle：source pointer v2 由每日
  revision receipt 和全局 snapshot receipt 链锁定，outcome 以 receipt 链、active
  pointer 和 inbox 分层发布并可自动恢复，cycle 依固定顺序运行直到科学
  状态不再变化。它不提供人工日期、冻结、批准或补签入口。
- 显式阶段 `ootang-prequential-calibration-shadow` 将固定的 ACI、AgACI-EWA
  和 SPCI-QRF 接入独立 E2 shadow ledger；每个目标先原子持久化 24 个候选 issue，
  再允许 reveal、状态更新和预声明 readiness 计算。`ootang-prequential-cycle-v2`
  在 source/outcome/issue 边界前后自动对账，并按 live sequence 合并积压的
  issue、settlement、backfill 与 revision；不补造漏签 issue，也不让 revision
  改写在线状态。该 shadow 只提供未来顺序的工程候选证据，选择、自动晋升、
  E2 live evidence 和正式预警仍固定为 false。
- 新增显式 `ootang-issue-replay`、`ootang-verified-live` 与
  `ootang-prequential-cycle-v3`：独立 runner 从递归验证的 source 尾七日和五个
  checkpoint 重建 IDW、7 通道输入、ConvLSTM forward 与 readout，再核对 40 个
  P50；指定 live 入口在同一 `runner.lock` 临界区按 replay receipt → seal intent →
  live append → completion 提交，并由 13 阶段 fixed-point cycle 自动编排。旧
  live-v1 CLI 尚未由系统级授权禁用，本地账本仍是 trusted-writer chain；可信时间
  shadow 与 immutable epoch registry R1 已作为独立显式阶段实现，但自动 rotation、
  scheduler entry authorization 与长链扫描优化仍是门禁。因此这项能力只关闭指定机器入口的 checkpoint/input 重放门，
  不计入 E2 live evidence，也不输出正式预警。
- `ootang-epoch-registry` 在完整验证 feed 后先写 feed-observation/head 反回滚链，再在
  稳定最终 slot 中预构建并公开重载 source/五 seed bundle；feed、runtime artifacts 与
  显式 archival capsule 都进入内容寻址快照。它只追加 `candidate_ready` 记录，status
  明确为 `immutable_candidate_record_ready`。R1 已在提交 `3d6ce8f` 固定。
- 显式阶段 `ootang-epoch-preparation` 继续完成 R2a：从 R1 immutable tip 解析
  exact 22-module closure，仅允许两个审核过的 package augmentation，将 R2a
  profile/implementation 与候选文件一起内容寻址，在同一 canonical project/slot
  物化非可迁移执行树。机器用两个 `uv --isolated --frozen` 环境重载 prerequisite，
  并按 `atol=1e-6 mm` 复核五 seed `predict_p50` 与 `reload_replay`。每次 current
  repoll、实现升级和 orphan receipt 恢复都会重跑当前烟测。该树固定
  `portable_offline_runtime=false`；drain、active switch、rotation、trusted anchor、E2/
  activation/formal claim 仍全部为 false。
- 显式阶段 `ootang-epoch-drain` 实现 R2b 的首个 machine-only drain-start barrier：
  它在独立 `drain_events/head/status/drain_fence_prepares/intents/capsules/drain_exchange_attempts/overlay` namespace 中
  引用但不改写 R1/R2a。capsule 引用内容寻址 full intent-prefix（live/shadow 全 hashes 与
  issue/outcome/guard/trusted/source inventories）；每 poll 证明 start→current append-only。
  intent 是 durable transaction reservation/lower-bound，`candidate_at_intent` 不是
  activation selection。append-only attempt WAL、armed marker 与 boundary 只有 recovery authority；
  orphan prepare/prefix/intent/capsule/attempt/boundary object 没有 lifecycle
  authority，唯一 DRAINING authority 仍是 `epoch_drain_started` event。
  按 `manager → cycle → deploy → runner → replay → shadow` 取得全锁，并只允许没有
  outstanding issue 或 pending guard/trusted-time/shadow 工作的 clean start。任何 tombstone
  `mkdir` 前，机器先 create-only 发布永久 singleton `fence_prepare`，绑定历史 R1/R2a、
  capsule/prefix、旧 route identity 与 ACL/swap policy；crash 或 tip 推进后恢复同一 transaction，
  marker 自身无 lifecycle authority。随后才为 `0755` 空 tombstone 安装并精确复验 extended
  ACL `everyone deny write`，且真实
  add-file 拒写探针必须通过；随后保存 intent、复验全部绑定，并完成下述 capacity/boundary/
  WAL/armed-marker 序列后再用 macOS
  `renameatx_np(RENAME_SWAP)` 将旧 epoch canonical `issue_inbox` 与 tombstone 原子交换。ACL 随
  inode 跨父目录交换后立即围栏 canonical route，再原位收紧为 exact `0555` 并复验
  ACL/拒写。若崩溃发生在 swap→chmod 窗口，恢复只能向前加固，绝不交换回去；平台
  不支持时也禁止退化为普通 rename。
  物理 issue-admission boundary 是 Darwin swap 成功的瞬间；权威 state-snapshot boundary
  则是六锁下 full-clean pre-swap replay 后由唯一 `epoch_drain_started` 引用的内容寻址对象，绑定
  live/issue/guard/trusted/outcome/shadow/source inventories 与 staged next-epoch incoming。
  pending 时机器跨 poll 等待，合法 settled extension 自动纳入下一边界，不做人工 freeze/
  cleanup/backdate。manifest/staged-feed 上限分别固定 64 MiB / 16 MiB，feed 独立内容寻址，合法
  大 feed 不会自锁。swap 前 worst-case boundary 超过 64 MiB 时返回
  `waiting_for_drain_boundary_capacity`，route 不交换、无 event、无人工 cleanup；通过后顺序
  固定为 final fence verify → actual queue 稳定 capture/CAS → actual capacity → publish exact
  pre-swap full boundary → append/replay `drain_exchange_attempts` terminal → 在 fence operand
  写入直接绑定 terminal+boundary 的 `.epoch-drain-armed-attempt.v1.json` → immediate Darwin
  swap；marker 随 inode 原子移动。正常同 poll post-swap logical clean 必须与 pre-swap 精确
  相等；prepared retry 只自动恢复严格单一 temp/ACL crash state。exchanged recovery 必须从
  armed terminal 读取旧 boundary，current clean 只作合法 append-only extension gate，禁止
  重建 boundary。WAL suffix rollback/branch/gap/extra/symlink 均 fail closed。publisher 与 event
  前 self-replay 继续复验；超 64 MiB 仍在 swap 前机器 waiting，历史 chunk/Merkle 只能进入
  后续 R2b-2b v2 版本，不能重解释 v1 bytes。
  状态只进入 `DRAINING`；candidate selection、drained、active switch、rotation、trusted
  anchor、E2 evidence、activation readiness 与正式预警声明全部为 false。
- 显式阶段 `ootang-epoch-drain-eligibility` 实现 R2b-2a 的 machine-current
  eligibility observation/stale detection。它从 persisted R2b event 恢复历史 R1/R2a
  authority，在同一六锁内完整重放 fence/boundary/WAL/armed marker 与 archived old
  runtime；deterministic 64 MiB CAS 不含 poll time 或 staged incoming。相同 clean state
  字节级幂等，合法 settled extension 经二次 exact capture 后自动追加 previous-hash-linked
  event，pending/capacity 状态只写 current=false 且不追加 event。crash `.tmp` 自动清理，
  integrity failure 尽力写 `blocked_integrity/current=false` 并保留更强 rollback witness。
  Observation/event 明确 `observation_authority_only=true`、lifecycle/transition authority
  false；old drained、active/switch/rotation/trusted/E2/activation/formal 仍全部 false，未来
  transition 必须在六锁下 exact recheck，不能读取 cache 直接晋升。
- 显式阶段 `ootang-epoch-drain-v2-workset` 实现 R2b-2b-1 的 v2 首阻塞项观察。它在同一
  六锁下调用冻结 v1 clean gate，只把首个 pending family 写成绑定 R1/R2a、candidate/slot、
  old live epoch 与 ledger tip 的 content-addressed observation；event 每次重放都精确解引用
  object。它不是完整 workset 枚举、reservation、admission fence 或 recovery；后生 v1
  authority 优先并使 observation inert，head/status 也不提供 anti-rollback authority。所有
  DRAINING/drained/active/trusted/E2/formal 声明仍为 false，且无人工日期、冻结、批准、
  cleanup、force 或 backdate。
- 显式阶段 `ootang-epoch-admission-cut` 实现 R2b-2b-2a 的 machine-only official-writer
  lock-path cut。为保持历史 ledger/intent/receipt 可重放，它逐字冻结而不修改 11 个旧
  writer/orchestrator；在六锁下准备 exact `0444` deny-write regular-file sentinel，并仅以
  Darwin `RENAME_SWAP` 先交换 `deploy_cycle.lock`、再交换 `runner.lock`。deploy-only crash
  会自动跳过已封闭 lock 的 acquisition、重获剩余锁、追加 current-context attempt 后向前完成；无
  restore/unfence/人工控制。event 只证明冻结 official entrypoint 被物理切断，complete
  manifest、reservation/recovery、泛化 admission fence、lifecycle/transition、drained/
  active/trusted/E2/formal 仍全部为 false。
- 显式阶段 `ootang-epoch-workset-manifest` 实现 R2b-2b-2b 的 bounded closed-workset
  reservation。它在 official writer cut 后完整重放 cut event/attempt/context，只取得仍开放的
  `manager → cycle → replay → shadow` 四锁，并把 issue/replay、live outstanding、source+
  outcome revision、guard、trusted-time 与 calibration shadow 六族及其传递义务写成确定排序、
  内容寻址、create-only 的 manifest/event。unknown/orphan/duplicate/branch/overflow 或依赖不闭合
  均整体 fail closed，不发布部分清单。该 event 是 exact-key recovery 的机器 reservation，
  request-only TSA crash 可保留同 nonce 的 DER repair，过期/结果先到的 guard intent 自动转入
  backfill supersede；历史 event 重放不重新要求已合法推进的 predecessor bytes 不变。
  但 recovery、泛化 admission fence、lifecycle、drained/active/trusted/E2/formal 仍全部为 false。
- 显式阶段 `ootang-epoch-workset-recovery` 实现 R2b-2b-2c 的 manifest-keyed 确定性本地
  recovery 基础。机器只重放 immutable reservation，不重新枚举 workset；先提交全局/逐 key
  create-only intent，再按依赖顺序每次最多推进一个 key，最后以 receipt 和 previous-hash event
  向前收口。首批 adapter 只处理同 nonce 的 TSA DER、ledger 可重建的 anchor receipt，以及具有
  durable backfill/settlement 证据的 guard supersession；不执行 TSA 网络、旧 ledger mutation 或
  legacy guard completion。单纯时间越界不能冒充 backfill。完整 workset recovery、drained、
  lifecycle/active/trusted/E2/formal 仍全部为 false。
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
uv run python main.py --stage ootang-prequential-calibration-bakeoff
uv run python main.py --stage ootang-live-source --stage ootang-production-bundle --stage ootang-issue-producer --stage ootang-prequential-live
uv run python main.py --stage ootang-outcome-materializer
uv run python main.py --stage ootang-prequential-cycle
uv run python main.py --stage ootang-prequential-calibration-shadow
uv run python main.py --stage ootang-prequential-cycle-v2
uv run python main.py --stage ootang-issue-replay
uv run python main.py --stage ootang-verified-live
uv run python main.py --stage ootang-prequential-cycle-v3
uv run python main.py --stage ootang-trusted-time-shadow
uv run python main.py --stage ootang-epoch-registry --stage ootang-epoch-preparation --stage ootang-epoch-drain --stage ootang-epoch-drain-eligibility
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

旧 30 日 `V0`、旧预警融合 v1/v2/v3 运行入口和对应历史测试已从当前工作树移除；需要复现历史快照时，必须按提交从 Git 历史恢复，不应把旧产物当作当前 v4 接口或结果。这里不指当前 machine-prequential cycle v1/v2/v3。

## 代码结构

```text
main.py                         # 当前管线入口（31 个可选阶段）
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
| [`docs/ootang_prequential_calibration_bakeoff.md`](docs/ootang_prequential_calibration_bakeoff.md) | 固定 E1 点预测上的 ACI/AgACI-EWA/SPCI 非排名校准比较、论文边界与 E2 shadow 门禁 |
| [`docs/ootang_prequential_live_engineering.md`](docs/ootang_prequential_live_engineering.md) | E2-A ledger/runner 实现、验证、状态语义和 E2-B 激活门禁 |
| [`docs/ootang_prequential_deploy_engineering.md`](docs/ootang_prequential_deploy_engineering.md) | E2-B1 finalized source、五种子安全 bundle、机器 issue producer 与剩余闭环门禁 |
| [`docs/ootang_prequential_cycle_engineering.md`](docs/ootang_prequential_cycle_engineering.md) | E2-B2 source receipt 加固、机器 outcome 物化、固定点 cycle 与恢复边界 |
| [`docs/ootang_prequential_calibration_shadow_engineering.md`](docs/ootang_prequential_calibration_shadow_engineering.md) | E2 三校准器独立 shadow ledger、预声明评估门、cycle v2 因果屏障与非晋升边界 |
| [`docs/ootang_checkpoint_input_replay_engineering.md`](docs/ootang_checkpoint_input_replay_engineering.md) | 五 checkpoint/input 独立重放、verified-live intent/completion 与 cycle v3 指定入口门禁 |
| [`docs/ootang_trusted_time_shadow_engineering.md`](docs/ootang_trusted_time_shadow_engineering.md) | RFC 3161 固定 TSA/策略/证书、隔离冻结运行时的自动可信时间影子请求、live/guard-bound 离线复验、崩溃恢复与非激活边界 |
| [`docs/ootang_epoch_registry_engineering.md`](docs/ootang_epoch_registry_engineering.md) | 不可变 epoch registry R1：稳定 slot、content-addressed archival byte capsule、candidate verified-ready 全链与非轮换边界 |
| [`docs/ootang_epoch_preparation_engineering.md`](docs/ootang_epoch_preparation_engineering.md) | R2a exact executable closure、同源物化树、双冻结隔离环境与五种子重放烟测 |
| [`docs/ootang_epoch_drain_engineering.md`](docs/ootang_epoch_drain_engineering.md) | R2b 首切片：全锁序、canonical route 原子 swap、full-clean boundary/event 与非切换边界 |
| [`docs/ootang_epoch_drain_eligibility_engineering.md`](docs/ootang_epoch_drain_eligibility_engineering.md) | R2b-2a：machine-current eligibility observation、stale detection、capacity/witness fail-safe 与非 transition authority |
| [`docs/ootang_epoch_drain_v2_engineering.md`](docs/ootang_epoch_drain_v2_engineering.md) | R2b-2b-1：context-bound 首阻塞项 observation、v1 precedence、精确对象重放与非 reservation/recovery 边界 |
| [`docs/ootang_epoch_admission_cut_engineering.md`](docs/ootang_epoch_admission_cut_engineering.md) | R2b-2b-2a：冻结 writer 的 deploy/runner regular-file ACL 原子 lock-path cut、forward-only crash recovery 与非 manifest/lifecycle 边界 |
| [`docs/ootang_epoch_workset_manifest_engineering.md`](docs/ootang_epoch_workset_manifest_engineering.md) | R2b-2b-2b：六族 closed-workset 的完整内容寻址枚举、singleton reservation event 与非 recovery/lifecycle 边界 |
| [`docs/ootang_epoch_workset_recovery_engineering.md`](docs/ootang_epoch_workset_recovery_engineering.md) | R2b-2b-2c：manifest-keyed intent/receipt/event、确定性本地 crash-forward adapter 与非完整 recovery/lifecycle 边界 |
| [`figures/auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.png`](figures/auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.png) | 8 个测点 fit-only 自动 BIC 分段与 V0 候选状态 |
| [`figures/v5_candidate_display_ootang_v1/candidate_display.png`](figures/v5_candidate_display_ootang_v1/candidate_display.png) | MJ1/MJ3 候选输入与其余 6 点 unavailable 状态；无 NGBoost 推断或 v5 融合 |
| [`figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg`](figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg) | 514 个结果时刻的测点候选与滑坡体双轴状态 |
| [`docs/progress.md`](docs/progress.md) | 当前实现进度、已清理历史代码与未完成门禁 |

## 尚未完成的关键事项

1. E2-B2、calibration shadow v1、runner-independent checkpoint/input replay、RFC 3161
   shadow、R1 immutable registry、R2a same-origin executable preparation、R2b
   clean-start drain barrier 与 R2b-2a eligibility observation/stale detection 均已有
   machine-only additive 实现。R2b 首切片只原子撤销
   canonical issue route，固定 full-clean boundary 并提交 `epoch_drain_started`；pending
   工作会保持机器 waiting。R2b-2a 跨 poll 保存 publication-time observation、识别
   stale 并自动吸收合法 settled extension，但仍未声明 drained 或切换 active。R2b-2b-1 已用
   不可重解释 v1 bytes 的新 schema 实现 context-bound 首 blocker observation；它尚不是
   closed workset。R2b-2b-2a 已在不修改自绑定旧 writer 的前提下，用 deny-write regular-file
   sentinel 原子封闭 deploy/runner official lock pathname，但明确还不是完整 admission fence。
   R2b-2b-2b 已在该稳定边界内完成 bounded closed-workset manifest 枚举与 reservation；
   R2b-2b-2c 已增加 manifest-keyed dispatcher 和首批确定性本地 crash-forward adapter，但
   网络、ledger mutation 及其余 successor 仍未实现。下一步是补齐 reserved successor adapter 和独立
   drain assessor、权威 active transition、cycle v4、scheduler authorization 和长链
   O(N²) 优化。不得添加人工日期、冻结、cleanup、批准、force 或 backdate；在这些门
   关闭前保持 `real_activation_ready=false`。
2. ACI、AgACI-EWA 与 SPCI 已按预声明合同进入未来 E2 shadow；最少需要 180 个
   共同可用未来目标日并通过逐站 coverage/score/availability/rolling gate，才可
   报告 engineering readiness。当前不会自动选择或晋升，E1 回顾性结果也不得
   用来改写 live v1；任何后续候选变更都必须创建新协议版本与新 epoch。
3. 正式灾害效能仍需与模型输出相互独立、机器可读且带可见时间的结局源。
   自动化可以消除逐日人工操作，但不能从自身残差制造独立灾害真值。
4. NGBoost 代理、自动 V0 和 v5 candidate display 仍是另一条非正式支路；其
   G1--G4 blocked 状态不阻止 machine-only 位移预测研究，也不得被后者绕过。
5. 只有获得用户授权后，才启动 Vajont 的数据适配与外部案例评估。
