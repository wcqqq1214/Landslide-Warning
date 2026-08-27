# 项目工作进度

> 更新日期：2026-08-27。本文件记录工程与研究实现进度；正式 v5 门禁以
> `v5_validation_protocol.md` 为准，机器连续预测支路以
> `ootang_autonomous_research_protocol.md` 为准，结果数值以版本化 CSV 和 manifest
> 为准。历史条目保留其原始日期和门禁数字，不与当前工程门禁混读。

## 2026-08-27 official-writer lock-path admission cut R2b-2b-2a

- 基于已提交 `efa45c9 feat: add drain blocker observation`，新增显式、非默认阶段
  `ootang-epoch-admission-cut`。当前共 33 个可选阶段；默认链仍严格为
  `features → convlstm → ootang-operational-v4`，mutable 输出仅为
  `runtime/ootang_epoch_registry_v1/admission_cut_v1/status.json`。
- 旧 live/guard/replay/trusted/shadow/producer 记录会绑定当前实现哈希，因此本切片没有给
  11 个旧 writer/orchestrator 植入新 marker。profile 逐字冻结并在每次执行前复验这些文件，
  避免为了切断入口而让待排空旧 epoch 自身不可重放。
- 机器按 `manager → cycle → deploy → runner → replay → shadow` 取得全锁，create-only 发布
  prepare、exact `0444` + `everyone deny write` regular-file sentinels、inode-bound intent 和
  previous-hash-linked current-context attempts；随后仅用 Darwin
  `renameatx_np(RENAME_SWAP)` 先交换 canonical `deploy_cycle.lock`，再交换 `runner.lock`。
  不存在普通 rename fallback、restore、unfence、cleanup、force、批准、人工日期或 backdate。
- deploy-only crash 后，恢复不会重新 acquire/flock 已封闭 deploy pathname，而是锁定剩余
  runner/replay/shadow，追加绑定最新 old-ledger tip 的第二个 attempt，再只向前完成 runner
  cut；对封闭 pathname 唯一的 `O_RDWR` 是必须失败的 denial probe，成功即 integrity block。
  runner cut 后只需 manager/cycle 即可精确重放并发布 singleton event。首次物理 cut 前
  检出任何 v1 authority 时，v1 precedence，事务 inert 且锁路径保持开放。
- Event 只证明冻结 official writer 的两个共享 lock pathname 已持续拒绝 write-open。
  direct-filesystem/unknown writer 不在证明内；complete enumeration、reservation/recovery、
  泛化 old-work admission fence、v1/v2 mutual exclusion、anti-rollback、DRAINING lifecycle、
  drained/active/trusted/E2/formal 均保持 false，status 仍只是 cache。
- profile/module/test 当前 SHA-256 分别为
  `fe4e91768c8558d887a34465fa6c9f4e8c05f8c1a7cf07e061bc602733136c1c`、
  `95b675b132c5051cbbc4d34041b9686d122a64c6368f04c0bff8d6dddf1effcf`、
  `a26c7ebc476d0931ed0373f2b2fd5e6bc8255ed19ee9c3276a545a2a834ed8b3`。
  真实 Darwin ACL/swap、7 个崩溃点、busy/v1 precedence、context extension 和篡改快测，
  连同 main 合计 `45/45`，墙钟约 1 秒；Ruff/format/compile、strict JSON `32/32`、双 lock
  check、33-stage list 与默认/显式 dry-run 通过。97-path aggregate 保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，11 个 frozen writer
  和共享 v4 Bai--Perron 无 diff；最终独立审计 P0/P1/P2 `0/0/0`。未运行 R2b、NGBoost、
  真实 non-clean chain 或全仓长测。
- 下一步是在两个 official writer lock pathname 已封闭的稳定边界内，完整、定界、内容寻址
  枚举六 family 及传递义务，形成 closed-workset manifest event。只有 manifest 成立后才可
  实现 exact-key action adapters；当前 cut event 不能晋升为 reservation/recovery/drained。
- 详细合同见 `docs/ootang_epoch_admission_cut_engineering.md`。

## 2026-08-27 epoch drain v2 first-blocker observation R2b-2b-1

- 基于已提交 `895844e feat: add drain eligibility observation`，新增显式非默认阶段
  `ootang-epoch-drain-v2-workset`。当前共 32 个可选阶段；默认链仍严格为
  `features → convlstm → ootang-operational-v4`。mutable 输出仅为
  `runtime/ootang_epoch_registry_v1/drain_v2/status.json` liveness cache。
- 状态机审计修正了原 roadmap 的不可达线性假设：v1 drain 只在入口 full-clean 时产生
  authority，而 eligibility 又要求该 v1 authority，所以 non-clean 路径不能是 eligibility
  的普通后继。本切片只在相同六锁下把冻结 v1 clean gate 的首 blocker 写为独立 v2
  content-addressed observation。
- Observation 绑定 R1/R2a event、candidate/slot、old live epoch id、live event count 和 ledger
  terminal；event 重放必须精确解引用 observation 的 path/hash/size/schema/context/items。
  同 context/blocker 重询字节幂等，变化后原 observation 只标 stale/inert。任一锁 busy 时
  event/object/head/status 都不写。
- v2 单向检测全部 v1 drain transaction/object witness，绝不采纳或改写。冻结 v1 不读取 v2，
  因此本切片明确不声称互斥：后生 v1 authority 具有优先级，v2 observation inert。
  head/status 只是 repairable cache，不提供 anti-rollback authority。
- 当前只允许 `first_blocker_observation_implemented=true` 以及 R1/R2a/old-ledger context binding；
  bounded reservation/recovery、complete enumeration、old-work admission fence、v1/v2 mutual
  exclusion、anti-rollback、DRAINING event/route fence、drained/active/trusted/E2/formal 均 false。
- profile/module/test SHA-256 分别为
  `aa12082e32b9b94fc4ad4b08232ed586b49c8c1bf1d2ccdaf3587b096c047d17`、
  `93a6463f514d73c4809287e1bbc8033984c82f550d5c55ce84c209475d7b604b`、
  `d315d394b536eec4b1ea09c7a9683cfcbc0ebadc58a98758268f32d3329ed488`；v2+main
  定向快测 `45/45`，静态/JSON/lock/dry-run 检查通过，97-path aggregate 保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。未重复 R2b、
  真实长链或全仓长测。
- 后续 R2b-2b-2a 已完成 frozen official-writer lock-path cut；当前下一步是完整枚举并内容寻址
  closed workset，之后才写 manifest-keyed action adapters。不能把本观察或 physical cut 直接
  晋升为 reservation/recovery/drained/active。
- 详细合同见 `docs/ootang_epoch_drain_v2_engineering.md`。

## 2026-08-27 epoch drain eligibility R2b-2a

- 本增量基于已提交的 `2eefceb feat: add atomic epoch drain barrier`，新增显式、非默认阶段
  `ootang-epoch-drain-eligibility`；当前共 31 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段位于 `ootang-epoch-drain` 之后、
  operational v4 之前，但不会进入默认链。machine-readable mutable 输出为
  `runtime/ootang_epoch_registry_v1/drain_eligibility_status.json`。
- 当前候选 profile/module/test SHA-256 分别为
  `2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a`、
  `46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc`、
  `4e247497ca78a4449ae00769ad1c383c5b5279195c357edcf84e29af4e5754a4`；若代码或测试继续
  修改，冻结前必须重算。本增量作为独立 R2b-2a 里程碑提交。
- 观察器只从 persisted unique R2b event 恢复历史 R1/R2a selector，不调用 start/swap
  写路径，也不偷换成 current registry tip。它按
  `manager → cycle → deploy → runner → replay → shadow` 非阻塞取得全锁，完整重放
  event/intent/capsule/exact pre-swap boundary/exchange WAL terminal/armed marker、canonical
  fence 和 archived old runtime；拒绝 fence 后 outstanding issue，并要求 archived issue
  inventory 精确不变。每个历史 observation 的 semantic/activation/snapshot source object
  都按冻结 path/hash/size 重新解引用，不能只信 observation 内自洽 metadata。
- clean state 先发布 deterministic 64 MiB content-addressed observation，再追加
  previous-hash-linked event。Observation 不含 poll time 或 staged next-epoch incoming；同
  state repoll 字节幂等，合法 settled extension 经二次 exact capture 后自动追加一条；
  pending 或 prospective capacity 超限只返回 machine waiting/current=false，不写 event。
  首条 event time 不得早于 R2b event，后续不得早于 terminal eligibility event；六锁后只
  自动清理严格识别的 crash temp，unknown temp fail closed。
- head/status 只是 repairable cache，status 明确 `cache_authority=false`。integrity failure
  尽力写 `blocked_integrity/current=false`；只有 schema/profile/time/全部负声明合法且严格
  ahead 的 cache 才保留为 rollback witness，at/behind replayed chain 的 tip 必须精确匹配
  真实 entry。同 count 伪 tip 因此不会被固化成永久机器阻塞。event suffix 与所有 mutable
  ahead witness 同时被外部删除仍是 v1 明示检测边界。
- Observation/event 固定 `observation_authority_only=true`、
  `lifecycle_authority=false`、`transition_authority=false`；old drained、candidate selected、
  active switch/rotation、trusted anchor、E2、real activation、formal warning 全部保持 false。
  future assessor/transition 必须重新取得同一锁集并 exact recheck machine-current state。
- 验证已通过 eligibility public `20/20`、含真实 R2b authority integration 的 full
  eligibility `21/21` 与 main `35/35`。本 scoped 增量不再重复全仓：已提交基线
  `2eefceb` 已有 `809/809`；额外启动的 full discovery 在运行 `4184.67` 秒、进入与本增量
  无关的 NGBoost horizon-sensitivity 用例时被主动中断，中断前没有 failure/error。该次不是
  完整 suite 结果，不得写成 PASS，也不要由后续 agent 再次重复。已知工程债是逐锁/
  逐 fsync fault matrix、冻结 R2b 私有 API
  耦合、eligibility full-chain O(K²) 重放，以及上述 mutable-witness 同删边界。
- 该条目的下一步已由上方 R2b-2b-1 首 blocker observation 开始，但尚未形成 closed
  workset。后续 R2b-2b-2a 已完成 frozen official-writer lock-path cut；当前仍需完整枚举和
  manifest-keyed recovery；不得重解释
  或覆写任何已发布 R2b/R2b-2a v1 bytes。其后才是独立 drain assessor、权威
  `SEALED(old)+ACTIVE(new)` transition、cycle v4 与 scheduler authorization。
- 详细合同见 `docs/ootang_epoch_drain_eligibility_engineering.md`。

## 2026-08-27 epoch drain-start barrier R2b 首切片

- 本增量基于 `b53a238 feat: add executable epoch preparation`，新增显式、非默认阶段
  `ootang-epoch-drain`；该 R2b 基线共 30 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。R2b profile/module/test SHA-256 分别为
  `1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105`、
  `c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602`、
  `b79d132562891a3134d55073a282b351533e525b0661cd47914698e323612d68`。
- R2b 使用独立 `drain_events`、head/status、`drain_fence_prepares`、intent、capsule、
  append-only `drain_exchange_attempts` WAL 与 overlay namespace，并在 shared
  content-addressed objects 中保存完整 intent-prefix、staged feed 和 full-clean boundary；它只引用并复验 R1 registry
  和 R2a preparation tip，不改写两条历史链、candidate capsule 或 smoke receipt。mutable
  head/status 不是 authority。intent 是 durable transaction reservation/lower-bound，其中
  `candidate_at_intent` 只记录创建 intent 时绑定的 candidate，明确不是 activation
  selection。WAL、armed marker 与 boundary 只有 recovery authority；orphan
  fence-prepare/intent-prefix/intent/capsule/staged-feed/attempt/boundary object 本身均无
  lifecycle authority，唯一 DRAINING lifecycle authority 仍是 `epoch_drain_started` event。
- drain-start 临界区严格按 `manager → cycle → deploy → runner → replay → shadow` 取得
  全锁，避免任何已知 issue producer、cycle、live runner、checkpoint replay 或 calibration
  shadow 与 route fencing 并发。任一锁 busy 时不部分推进。
- 首版只接受 clean start：旧 epoch 不得有 outstanding issue，也不得存在 pending
  guard、trusted-time 或 shadow 工作。若任一未收口，机器返回 waiting，不追加 authority
  event、不撤销 route；已有 intent 只保留为 lower-bound，不改写为最终快照。机器也不借助
  人工日期、冻结、批准、force、backdate 或伪造 outcome 清空状态。
  old work 可在两次 scheduler poll 间继续；下一次 poll 仍 pending 就继续机器等待，合法
  append-only settled extension 会被完整重放并自动纳入新的 clean-state，不要求人工 cleanup。
- 通过 clean-start 检查后，机器先捕获 old-route identity，并在任何 tombstone `mkdir` 前
  create-only 发布唯一 `fence_prepare`，绑定历史 R1/R2a、capsule/完整 intent-prefix、
  old-route device/inode/mode、tombstone path 与 ACL/swap policy。crash 或 current tip 推进后
  仍从该永久 marker 机器恢复同一历史 transaction，marker 自身不产生 lifecycle authority。
  marker 落盘后才准备 mode 精确为 `0755` 的空 tombstone，安装并 exact-readback extended
  ACL `everyone deny write`，且实际 add-file 拒写探针通过；随后持久化 intent，并在全锁下
  复验全部绑定。机器完成下述 capacity/boundary/WAL/armed-marker 序列后再调用
  macOS `renameatx_np(RENAME_SWAP)`，跨父目录交换 canonical old `issue_inbox` 与 overlay
  tombstone；ACL 随 inode 交换后立即围栏 canonical route，再原位 chmod 为
  exact `0555` 并复验 ACL/拒写。swap→chmod 崩溃恢复只能向前完成加固，绝不 swap back。
  该原语/ACL 不可用或失败时 fail closed，禁止以普通 rename、move 或复制删除降级。
- R2b 区分两个边界：物理 issue-admission boundary 是 Darwin swap 成功的瞬间；权威
  state-snapshot boundary 是六锁下 full-clean replay 形成的 content-addressed **pre-swap**
  boundary object，再由唯一 `epoch_drain_started` 精确引用。该对象绑定 live/issue/guard/trusted/
  outcome/shadow/source inventories 与 staged next-epoch incoming；staged incoming 不是旧
  source authority，未被 event 引用的 orphan object 也没有 authority。event 当前只进入
  `DRAINING`；`activation_candidate_selected`、drained、`SEALED(old)`、`ACTIVE(new)`、
  automatic rotation、trusted anchor、E2 evidence、real activation 与 formal warning
  声明全部为 false。
- capsule 精确引用内容寻址 full `intent-prefix` manifest；它保存 live/shadow 全部 entry
  hashes 与 issue/outcome/guard/trusted/source inventories。每次 poll 都验证 start→current
  的 append-only/prefix extension，合法 settled extension 自动进入最终 boundary。manifest
  上限显式为 64 MiB，staged next-epoch feed 上限为 16 MiB；feed 独立内容寻址、boundary
  只存 reference，因此合同内的大 feed 不会因 control-object 上限而自锁。event append 前
  还必须完整自重放 boundary 及其全部引用，重建 state 精确一致后才写唯一 event。
- swap 前先以最大 16 MiB staged CAS reference 对完整 boundary 做 worst-case capacity
  preflight。若 canonical boundary 超过 64 MiB，机器返回
  `waiting_for_drain_boundary_capacity`，canonical route 保持未交换且不写 event，也不要求
  人工 cleanup。通过后不可逆尾部固定为 final fence verify → actual staged queue 稳定双读/
  CAS → actual capacity → publish exact pre-swap full boundary → append/replay
  `drain_exchange_attempts` terminal → 在 fence operand 内写入直接绑定 terminal+boundary 的
  `.epoch-drain-armed-attempt.v1.json` → immediate Darwin swap；marker 随 fence inode 原子
  移到 canonical route，post-swap publisher/self-replay 仍复验。
  正常同 poll 的 post-swap logical clean 必须与 pre-swap state 精确相等；只有已经 exchanged
  的 crash recovery 才允许从 immutable start-prefix 到 recovery-current 的合法 append-only
  extension。
- 每个 WAL attempt 都精确引用一个完整 pre-swap boundary，并由连续 sequence/previous hash
  组成 append-only chain；只可武装 unique terminal。prepared retry 可自动收敛严格的单一
  marker temp 与 exact/缺失 ACL crash state，不允许人工 cleanup。exchanged recovery 必须从
  已随 swap 移动的 armed marker 读取 terminal 的**旧 boundary**；current clean 只作其合法
  append-only extension gate，不能重建或替换 boundary。WAL suffix rollback、branch、gap、
  extra entry、symlink，或 marker/ACL/temp 不精确时一律 fail closed。64 MiB 超限仍在 swap
  前返回 machine waiting；为历史增长引入 chunk/Merkle 属后续 R2b-2b v2 版本，不得重解释 v1
  bytes。
- R2a 已记录的真实默认链试跑结论保持不变：正式五种子训练后，R1 因仓库缺少从
  2020-07-01 连续到机器当前日的 finalized feed，在历史 feed causal gate 以
  `Bundle was not durable before the first target natural day` 正确 fail closed；未生成
  R1 candidate，也没有真实 R2a/R2b 端到端 PASS。不得通过改时钟、合成日期、人工
  backdate 或 test override 伪造通过。
- 最终验证：R2b focused 为 `12/12`（runner `1008.486` 秒，墙钟 `1025.68` 秒）；
  R1+R2a+R2b+main 组合为 `116/116`（runner `2361.831` 秒，墙钟 `2393.77` 秒）；
  全仓为 `809/809`（runner `5568.360` 秒，墙钟 `5642.18` 秒），均为 0 failure /
  0 error。main 为 `34/34`，正式 v5 preflight 为 `23/23`，报告仍为 G0 PASS、
  G1--G4 BLOCKED、G5a 未评估/未授权、formal warning false。Ruff check、scoped
  format、compileall、`git diff --check`、strict JSON `30/30`、根/可信时间双
  `uv lock --check`、30-stage list、默认/显式 dry-run 与共享 v4 Bai--Perron diff
  均通过。97 条保护路径聚合 SHA-256 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
  最终独立标准/规格审计为 P0/P1/P2 `0/0/0`；逐对象 publication-fsync 故障矩阵仅作为
  后续 coverage debt，不削弱本轮已验证的通用 durable-adoption 合同。
- 该历史 R2b 基线的下一步 R2b-2a clean-start eligibility observation/stale detection 已在
  本文件顶部增量完成，结果仍只可为 DRAINING。后续 R2b-2b-1 v2 已实现首 blocker
  observation，但不具 reservation/recovery authority。后续 R2b-2b-2a 已完成 official-writer
  lock-path cut；当前下一步是完整 manifest 与 keyed trusted-time/guard/outcome/live/shadow
  recovery；v2 不得重解释或覆写 v1 fence-prepare/intent-prefix/capsule/intent/exchange-attempt/
  armed-marker/boundary/event bytes；历史 chunk/Merkle 也只能由该新版本表达。其后才是独立
  drain assessor 与权威 active transition；cycle v4、scheduler authorization 与长链
  O(N²) 扫描优化仍在更后。
- 详细锁、swap、intent 与非声明边界见 `docs/ootang_epoch_drain_engineering.md`。

## 2026-08-27 epoch 可执行准备 R2a

- R1 已在提交 `3d6ce8f feat: add immutable epoch candidate registry` 固定。在其上
  新增显式、非默认阶段 `ootang-epoch-preparation`；该 R2a 基线共 29 个可选阶段，
  无参数默认链仍严格为 `features → convlstm → ootang-operational-v4`。R2a profile/
  module SHA-256 分别为
  `c6ec0b1f340effd9e3fd5cd1a0ee67ebca9ffa4dc743a9cb36d701850dc875f9`/
  `b03182accc3e8d482683eda29c7c07bdb66f7a316dfa99a41f29e1b99b3fe209`。
- R2a 从 immutable R1 candidate tip 解析静态 local import closure，精确要求 22 个
  `convlstm`/`monitoring` 模块；只允许从 canonical project 捕获已固定 SHA 的
  `code/convlstm/__init__.py` 和 `code/monitoring/__init__.py` 两个 augmentation。
  dynamic/wildcard local import、第三个补件、closure 数量/顺序漂移或自洽删除 root
  module 均 fail closed。
- executable capsule 将 R1 event/candidate/slot/live-epoch、exact closure/resource tree 与
  R2a profile/implementation 精确 bytes 一起存入 content-addressed objects；capsule、smoke
  receipt 与 preparation event 都交叉绑定 path/SHA/size。物化树只能在原 canonical
  project 和 `slots/<slot-id>/live` 上使用，固定 `relocatable=false`、
  `portable_offline_runtime=false`；它不包含 `.venv`、CPython/uv binary、OS 或 wheel cache。
- 生产 smoke 固定 uv `0.12.5` 绝对路径及 SHA-256、CPython `3.10.20` 及
  executable SHA-256、SOABI 与 `macOS-26.5.1-arm64-arm-64bit`。机器分别用根
  43-distribution inventory 和 trusted-time 5-distribution inventory 的
  `uv --no-config run --isolated --frozen ... python -I -B` 环境做双域烟测；任一
  Python/distribution/platform 指纹漂移均阻断。
- 根烟测编译 exact 22 Python files、从物化树 import cycle-v3/trusted-time shadow、
  从 canonical slot 重载 source/model/live prerequisite 并复算 live epoch id。随后对 seeds
  `0..4` 调用 bundle `predict_p50()`，与 training manifest `reload_replay` 按精确
  station 集合/顺序、finite value、`rtol=0, atol=1e-6 mm` 逐项比较；trusted-time
  域另从同一树 import core。
- 已准备 candidate 的每次 current repoll 都重验 R1/capsule/tree/receipt 并重跑双域
  smoke，不仅信任历史 status。同 R1 candidate 遇到 R2a implementation 升级时机器
  重做 closure/materialization/smoke 并追加 `candidate_revalidated`；历史 event 仍依自身
  内容寻址 implementation object 重放。smoke receipt 已提交但 event 未提交的 orphan
  也必须重跑当前 smoke 并比较；环境漂移不得直接补 event。
- event 最终 publish 前再复验 R1 receipt/current artifacts/空 future namespace、R1/R2a
  profile/implementation bindings、capsule、tree 和 smoke receipt；追加后全链 replay 并确认
  唯一 tip。R2a config file 或其 canonical project 父路径为 symlink 时即使 bytes/hash
  相同也 fail closed。公开 API/CLI 不提供 runtime/candidate/date/freeze/approve/force/backdate/
  smoke override；manager lock busy 为 exit 3。进入统一错误归一范围后的完整性冲突为
  exit 2；少数 acquire-lock/profile 前置异常仍可能 traceback/exit 1，但同样 fail closed。
- R2a 明确固定 `old_epoch_drain_implemented=false`、
  `active_epoch_switch_implemented=false`、`automatic_epoch_rotation_implemented=false`、
  `trusted_anchor_receipt_verified=false`、`e2_live_evidence_eligible=false`、
  `real_activation_ready=false`、`formal_warning_output=false`。后续 R2b 首切片现已完成
  clean-start canonical route fence 与 `epoch_drain_started`，但只进入 DRAINING；非
  clean-start trusted-time/guard/shadow 恢复和 drain assessor 仍待完成。全过程禁止人工
  日期、冻结、批准或 force。
- epoch-preparation 独立终审为 `30/30`（590.587 秒），与 pipeline 最终组合为
  `63/63`（597.651 秒），全仓回归为 `796/796`（3047.808 秒），均为 0 failure /
  0 error。Ruff、compileall、R2a scoped format、diff-check、strict JSON `28/28`、
  根/可信时间双 lock check、默认/显式 dry-run 与 29-stage list 均通过。v5 frozen
  preflight 为 `23/23`，仍为 G0 PASS、G1--G4 BLOCKED、G5a 未评估/未授权、formal
  warning false。97 条保护路径无相对 HEAD 漂移，聚合 SHA-256 实测仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 另以真实 R1 default prebuilder 启动正式五种子 × 120 epoch 隔离链，五个 checkpoint
  均完成；R1 在 candidate 发布前因 `Bundle was not durable before the first target natural
  day` 正确 fail closed。当前没有从 2020-07-01 连续到机器当前日的 finalized feed，故
  未生成 R1 candidate、未进入 R2a default smoke。该结果不是 PASS，也不通过改系统时钟、
  合成日期、人工 backdate 或 test-epoch override 绕过；真实 default smoke 继续作为 P2。
- capsule/lifecycle 审计当前无新增 P0/P1。保留的非阻断 P2 包括有限 AST denylist、
  smoke import 覆盖边界、name/version-only distribution inventory、同 UID 非协作 writer/
  路径 TOCTOU/全历史重写、异常后的旧 status cache，以及部分 pre-lock/profile
  `RegistryError` 尚未统一映射 CLI blocked；orphan capsule/tree 从不构成 authority，
  freshness 必须依赖本次 poll 成功退出和 event replay。完整边界见工程文档。
- 详细合同、exact module 列表和恢复边界见
  `docs/ootang_epoch_preparation_engineering.md`。

## 2026-08-26--27 不可变 epoch registry R1

- 本增量基于 `74ef8b9 feat: add cryptographic time shadow gate`，新增显式、非默认
  阶段 `ootang-epoch-registry`；该 R1 基线当时共 28 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段不提供 target-date、freeze、
  approve、force、backdate 或人工签名入口。
- R1 从固定 finalized feed bytes 与冻结合同推导 stable slot id，直接在最终
  `slots/<slot-id>/live` 路径运行 source materialization 和五 seed bundle prebuild；
  已发布 manifest 含绝对 artifact path，因此禁止 build 后移动或以 symlink 切换。
- source/model public loaders 通过后，机器要求 lineage 中的 daily-feed snapshot 精确等于
  registry feed bytes，把固定代码、配置、根/隔离依赖锁和 RFC 3161 trust 保存为
  content-addressed archival byte capsule，并把 feed 与全部声明的 runtime artifacts 另存
  immutable snapshots；随后才向严格 N→N+1、previous-hash、create-only registry 链追加
  `candidate_ready`。历史 replay 只依赖这些快照，不要求以后合法启用的 mutable slot 永久
  空白。capsule 尚未 materialize 为可执行旧 epoch tree，其显式 allowlist 也不是
  transitive import closure；mutable status/head 只是可由权威事件链恢复的 cache。
- feed export 必须递增，source id 固定；修订可以前进，但任何已见 revision id/record bytes
  的回退均在新 receipt/event 发布前 fail closed。完整 feed 合同通过后、长训练前，机器先
  追加独立 create-only feed-observation chain 并刷新 tip witness；observation event 内含
  精确 raw feed bytes，是单文件水位 commit，object 副本可由它恢复。所以 orphan receipt、
  waiting build 或 object publication 前崩溃都不会遗忘更高水位，invalid/future feed 则
  不会污染链。构建前后重新捕获
  worktree capsule；默认 prebuilder 在真实动作边界重新采机器时间，测试
  prebuilder/feed override 只允许 project production runtime tree 之外的 isolated runtime。
  实现/profile SHA-256 分别为
  `1418b754012b71b296c200539efa846cea63e5a4374e44d216bd656f8b047b9c`/
  `c56004649689ee8aa4beeca6a7c61bbdd5529ec62706880ea8eac65a8ca18edd`。
- 权威 event 仍名为 `candidate_ready`；status 使用更窄的
  `immutable_candidate_record_ready`，避免把历史快照完整性误写成当前 mutable slot 的
  activation readiness。production poll/CLI 不暴露 runtime、feed 或 prebuilder override。
- R1 不修改旧 live v1/cycle v3，不创建 candidate ledger，不签发 issue、不读 outcome、
  不切换 active。`automatic_epoch_rotation_implemented`、可信 anchor、E2 evidence、real
  activation 和 formal warning 全部固定为 false。registry 定向 `40/40`、registry +
  pipeline `72/72`、全仓 `765/765`（344.829 秒，0 failure / 0 error）；Ruff、scoped
  format、compileall、strict JSON `27/27`、根/隔离 lock check、diff-check 均通过。
  v5 preflight `23/23`，仍为 G0 PASS、G1--G4 BLOCKED、G5a 未授权；97 个保护路径无
  diff，聚合 SHA-256 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。两轮独立只读
  审计均为 P0/P1 `0/0`；保留的 trusted-writer/掉电持久性、累计 replay O(N²) 与 R2
  executable closure 是已显式记录的 P2 工程边界。
- 后续 R2a 已在不修改 R1 链的前提下完成 exact executable closure、same-origin tree
  materialization 和双域/五种子烟测；R2b 首切片又完成 clean-start canonical route
  原子 fencing 与 `epoch_drain_started`。当前仍只到 DRAINING，非 clean-start
  trusted-time/guard/shadow 恢复、drain assessor 与 active transition 尚未实现。若 outcome
  永不到达，机器只能等待，不能伪造结局、人工冻结/批准或强制轮换。

## 2026-08-26 RFC 3161 可信时间影子门

- 本增量基于 `bace358 feat: add independent issue replay gate`，新增显式、非默认
  阶段 `ootang-trusted-time-shadow`；当前共 27 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段只对 replay-gated verified-live
  completion 对应的 issue seal 建立外部 proof-of-existence，不读 outcome，不提供
  target-date、freeze、approve、force、backdate 或人工签名入口。
- v1 固定 Sigstore production RFC 3161 endpoint、policy OID
  `1.3.6.1.4.1.57264.2`、SHA-256 imprint、256-bit nonce、`certReq=true`、仅
  `PKIStatus=granted`、必须存在且不超过 1 秒的 accuracy，以及冻结的
  `Asia/Shanghai = UTC+08:00` 目标日边界。trust source 固定到
  `sigstore/root-signing@ba3066c420970c13772ba0625f09f1ec97193116`；manifest、leaf
  DER、root DER SHA-256 分别为 `33d22cc6dbdf8bf016b0cb96e291ca4538109ffb5d22a639edb34d2f42c80eef`、
  `85f927bc07ab62cac3b44356c10efc81b2c6883fda7ab9e6d870d9d13acd05b7`、
  `2aca8fea5d3ce48b01cc77076293c280e6c23ffe44034757ee7833ca9f45d633`；这些值也在
  core 中硬固定，不能通过同时替换 config、manifest 和自签证书改变 trust root。
- 根 `pyproject.toml`/`uv.lock` 保持原字节和
  `bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0`/
  `f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c`，避免破坏旧
  replay/shadow 配置绑定。stdlib launcher 改用 `uv 0.12.5 --isolated --frozen`、精确
  CPython `3.10.20`、`python -I` 与清理后的子进程环境启动独立子项目；launcher、
  core、子项目、子锁 SHA-256 分别为
  `92a0a755881f549b272bfbd09d11b2590e06e9ecb06409421f6bd18e261b1f1b`、
  `797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7`、
  `236606b46ed945fbbce46868a1a8ab5aac9a1131f39352f324e95a6001b59625`、
  `aebfc5d498735f694572ee8b53c328da5fa66a84da05d202605a2500e8b78f93`。
- 请求 JSON/TSQ 先 create-only 持久化，原始 TSR 进入 content-addressed object，随后
  建 target link、receipt 和 status；每次公开 load 都从完整 live ledger 与
  verified-live guard history 重建科学 envelope，再复验 canonical bytes、路径、
  nonce、policy、message、单 signer、精确 leaf、leaf→root/root self 签名、TSA name、
  CMS 签名/证书链、`genTime+accuracy` 和目标日前因果条件。锁 inode 被替换、symlink、
  非规范 JSON、协调 trust swap、伪造 envelope、对象 relocation、模块注入及崩溃恢复
  均有对抗测试；联网前若无法读取 signal mask 或发现 `SIGALRM` 被继承屏蔽则立即
  fail closed，408/425/429/5xx 自动等待重试，确定性篡改则 exit 2，锁忙 exit 3。
- 公共 production dummy fixture 的 TSQ/TSR SHA-256 为
  `bdc94a42cd34ba1a947c521b19553edea9a7699c621c8ff66ba4458382acbfa3`/
  `4535d7ddc291159db625a9b68b403d5db14a8544c3be102604377a4a7d317afc`，已真实联网取得并
  在隔离环境离线复验；它不是藕塘科学证据。当前 focused 验证为 core `22/22`、
  launcher+pipeline `39/39`、全仓 `724/724`（345.293 秒，0 failure / 0 error）；Ruff、
  format、compileall、strict JSON `3/3`、diff-check、根/隔离 lock 检查均通过。正式 v5
  preflight 为 `23/23`，仍是 G0 PASS、G1--G4 BLOCKED、G5a 未授权；97 个保护路径保持
  `97/97`，聚合哈希仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。最终独立只读
  审计为 P0/P1/P2 `0/0/1`，唯一 P2 是下述 ESSCertIDv2 显式解析边界。
- 即使密码学回执有效，`trusted_anchor_receipt_verified`、
  `e2_live_evidence_eligible`、`real_activation_ready` 与 `formal_warning_output` 仍全部
  为 false。CMS 证明的是固定 TSA 签署的时间声明，不独立证明其上游 UTC 时源绝对
  正确；当前还没有多运营方 quorum，也未单独解析 RFC 5816 ESSCertIDv2 signed
  attribute。下一道 machine-only 门禁是 immutable epoch registry、bundle prebuild
  与安全自动 rotation；随后把 trusted-time 接入 cycle v4 并实施 scheduler entry
  authorization，不能以人工冻结或批准替代。

## 2026-08-26 指定入口 checkpoint/input replay 与 cycle v3

- 本增量基于 `9a69725 feat: add autonomous calibration shadow`，新增显式非默认
  阶段 `ootang-issue-replay`、`ootang-verified-live` 和
  `ootang-prequential-cycle-v3`；当前共 26 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。没有 target-date、freeze、approve、
  force、backdate、manual-signature 或人工清理入口。
- replay/verified-live/cycle-v3 配置 SHA-256 分别为
  `c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5`、
  `081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b`、
  `6852876db121027e82aedfb2b65c9cb1d9b40106b19c7068ba8764b317e1db24`；旧
  live/cycle v1/v2 配置和 ledger schema 均未改写。
- independent verifier 递归使用共享 source authority 重建 historical base + daily
  feed 的 current/activation canonical dataset，再独立复刻 producer 的五项
  normalization、IDW、7-channel 输入、ConvLSTM cell/head、station readout 与
  P50 反归一化；8 个 persistence 必须 exact，五 seed × 八站的 40 个 P50 固定
  `rtol=0, atol=1e-6 mm`。公开 receipt reload 也重新加载五 checkpoint 做真实
  forward，不信任 receipt 自报 digest。
- verified-live 在原 live-v1 `runner.lock` 内固定执行 replay receipt → pre-seal
  intent → live issue transaction → completion。intent 前、append 前、append 后和
  receipt create 后均重新采样时钟；回退/跨目标日会在不可逆写入前阻断或精确撤销
  本轮 receipt。append 后崩溃先恢复 completion，再允许读取 outcome；无预先 intent
  的旧 v1 seal 永不事后追认。
- cycle v3 以 13 个固定阶段把两次 replay、三次 verified-live、四次 shadow 与
  source/bundle/outcome/issue 屏障组合成有界 fixed point。progress 先严格验证 raw
  链，再仅投影科学语义，排除合法 clock、storage path、raw sequence/entry hash 和
  anchor-only churn；issue/seal 科学 payload 的变化仍改变 token。稳定 token 与
  `work_remaining` 矛盾、振荡、stage crash、锁竞争和 status schema/claim 漂移均
  fail closed。
- 对抗修复覆盖 source/current/activation 自洽篡改、五 normalization bit parity、
  self-consistent forward/digest 篡改、final/intermediate symlink、pathname inode
  replacement、read-once 同 inode 变化、create-only 冲突、崩溃临时文件自动恢复、
  completion 时钟回退不落坏记录、declared source/model/issue/verification 因果时间，
  以及不同 runtime 时钟/路径/anchor retry 下科学 token 一致。最终 replay/
  verified-live/cycle-v3 为 `36/27/16`，三模块 `79/79`，加 pipeline `109/109`；全仓
  `715/715`（727.081 秒，0 failure / 0 error）。Ruff、compileall、JSON 与
  `git diff --check` 全部通过；v5 preflight `23/23`，G0 PASS、G1--G4 BLOCKED、
  G5a 未授权；97 路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。真实空
  runtime 精确执行 13 阶段，一轮 `converged_waiting`，所有 evidence/activation/
  promotion 标志为 false。最终独立审计 P0/P1 `0/0`。
- 本增量不训练、不调参、不选择 seed、不改校准候选/阈值，也不重新估计 accuracy、
  coverage、interval score、FAR 或 recall；它改善的是 issue 来源与提交因果的可验证
  完整性，不是预测精度。旧 live-v1 CLI 仍可绕过指定入口，本地账本仍是
  trusted-writer chain，可信密码学时间、immutable epoch registry/自动 rotation、
  scheduler entry authorization 与 O(N²) 长链优化仍未实现。因此 formal warning、
  E2 live evidence 和 real activation 继续为 false；下一道机器门禁是可信密码学时间。

## 2026-08-26 E2 calibration shadow 与 cycle v2

- 本增量基于 `fd6f919 feat: add prequential calibration bakeoff`，新增显式非默认
  阶段 `ootang-prequential-calibration-shadow` 和 `ootang-prequential-cycle-v2`；
  当前共 23 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。全程没有人工选日、冻结、批准、
  补签或自动 promotion 入口。
- shadow 配置 SHA-256 为
  `28c02510f81e1832220913d4bfde69bc8abe9a2269aa8279aabc297f60113857`，精确绑定
  live/deploy/bakeoff v1、不可变 calibration core、`pyproject.toml` 和 `uv.lock`；
  cycle v2 配置 SHA-256 为
  `875daa95416e58e3c80a4a68f59874047daa1bbb5b395878f6a9265605279242`，精确绑定
  cycle v1 与 shadow v1。
- ACI v1 control、AgACI-EWA variant 和 SPCI-QRF 分别维护 8 个 station-local
  状态。每个目标先在单个事务中写 `opened + 24 candidate issue + sealed`，匹配的
  live settlement 到达后才写 `opened + 24 reveal + 24 state update + settled`。
  激活时已存在的 outstanding issue 固定不计未来支持；漏签 settlement 只记
  backfill-ineligible，不补造 issue 或更新状态；revision 只追加 24 条 rescore，
  state-before/state-after 相同且不进入原始充分统计。live drift 自动同时重置三方法。
- 独立 SQLite STRICT shadow ledger 使用不同 application id、WAL/FULL、canonical
  finite JSON、事务摘要、全局链、issue-only 链、conflicting insert/replace guard
  和 update/delete trigger。每次公开读写都从 genesis 完整验证并数学重放；完整
  同语义重试保留首次事件/时间，部分事务、同键异语义、schema/chain/state 漂移均
  fail closed。上游 epoch 在无 outstanding 时由机器 close+cold-start，有 outstanding
  时拒绝跨 epoch。
- runner 把 issue seal、settlement、backfill 和 revision 按 live source sequence
  统一合并，关闭了长时间离线时较晚 source 推过较早 revision 的游标缺陷。单次最多
  512 个动作，合法积压返回 `work_remaining/exit 0`；确定性 progress token 排除
  poll/status 时间、shadow recorded time、raw SQLite bytes 和无科学变化的 anchor
  churn。cycle v2 在 v1 七步周围插入四次 shadow reconcile，共 11 个固定阶段；
  `work_remaining` 与稳定 token 的矛盾会 fail closed，不能误报收敛。
- 评估合同在首个 shadow outcome 前预声明：共同支持、至少 180 个共同未来目标日和
  每站每方法 180 个样本、30 日 rolling、coverage absolute gap `≤0.05`、challenger
  相对 ACI coverage-gap margin `≤0.02`、interval-score ratio `≤1.0`、availability
  `≥0.95`，并要求逐站通过。它只自动计算 engineering readiness；selection、
  promotion、E2 live evidence、real activation 和 formal warning 始终为 false。
- 最终验证：shadow ledger/runner/cycle v2 为 `14/13/23`，合计 `50/50`；pipeline
  `29/29`；prequential/deploy/shadow 联合回归 `269/269`；全仓 `635/635`
  （330.833 秒，0 failure / 0 error）。Ruff、compileall、JSON 校验和
  `git diff --check` 全部通过。真实空 runtime 精确执行 11 阶段，一轮返回
  `converged_waiting`，shadow 为 `waiting_for_live_prerequisites`，evidence/promotion
  均为 false。独立最终审查为 P0/P1/P2 `0/0/0`。
- 正式 v5 fail-closed preflight 仍为 `23/23`，G0 PASS、G1--G4 BLOCKED、G5a
  未授权；E1 与 97 条保护路径哈希保持不变。在该历史增量结束时，下一道机器门禁是
  runner-independent checkpoint/input replay；它现已由本文顶部的指定入口实现。
  可信密码学时间、immutable epoch registry/自动 rotation、调度入口权限边界和长链
  O(N²) 扫描优化仍待完成。不得用人工冻结或批准替代。

## 2026-08-26 E1 prequential 校准 bakeoff

- 新增显式且非默认阶段 `ootang-prequential-calibration-bakeoff`，当前共 21 个
  可选阶段；无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段只消费受保护 E1 四产物，
  三种方法逐 binary64 复用同一 6,888 行 point forecast，不修改模型、E1、E2
  ledger、v4/v5 或 Vajont。
- 固定并列方法为 E1 exact `aci_v1_control`、七个 gamma 专家的
  `agaci_ewa_variant_v1` 和 signed-residual `spci_qrf_v1`。AgACI 名称明确标注该
  实现不是论文的 BOA+gradient-trick 精确复现；SPCI 使用 lag=10、最新到最旧的
  Eq.13 特征、60 个 QRF pair、180 日窗口和固定浅层 10-tree RF。任何理论保证
  均不从论文直接转移到项目的 clipped/windowed/reset 变体。
- 每个 fold-date 先生成 3×8=24 个 issue 和 outcome-free candidate batch hash，
  再读取同日 actual 并更新下一日状态；fold 与 E1 drift schedule 自动 reset。反事实
  测试确认首日 actual 改动不改变首日 24 issue/hash，但会改变三种方法的次日
  state/hash。ACI alpha、interval 与 warmup 逐项对齐 E1。
- fold 1/2/3 的 ACI coverage 为 `0.791192/0.695061/0.630746`；AgACI-EWA 为
  `0.848140/0.745884/0.657121`，在 fold 2/3 缩小 coverage gap，三折平均宽度和
  interval score 均下降，但 fold 1 从轻微欠覆盖变成过覆盖。SPCI 为
  `0.721186/0.622433/0.498736`，三折均更欠覆盖，不能因区间窄或 score 低而晋级。
- 产物为 timeline/metrics/pairwise/manifest `20,664/108/144/1`；SHA-256 分别为
  `8857e77a96cba8ad2ae011a822759c0c08cc65e0c6fdab684b3e3fc0334df91e`、
  `dfc314041a2982456428b51dd4cd08c7b232706dea77ff77c200e3e76e620168`、
  `4e13a366bfe37b58d9692bdbefd23f4fa686ee731657460b21f04964662eccf8`、
  `229a26f5ec2c5a7082b14d18b8af7d21d44ba42f3b5b5d365b765f6fead4422f`；
  连续两次完整生成逐字节一致。
- 输出合同禁止 winner/rank/selection/promotion，所有正式、独立标签、确认性、
  Vajont 标志为 false。当前只支持把三种固定状态送入未来 E2 shadow 前瞻比较，
  不能从已查看 E1 结果直接晋升或继续搜索后回写 v1。完整方法、论文边界、数值、
  哈希与下一门禁见 `docs/ootang_prequential_calibration_bakeoff.md`。
- 最终独立 P0/P1 审计关闭了两个问题：issue 阶段现以字段白名单结构性排除所有
  reveal 数据，outcome lookup 只在 24 issue/hash 后构造；SPCI 加权分位数现排除
  零权极值并只在正权有限支持上重归一化。修复后的连续两次完整重放仍逐字节一致，
  最新审计无开放 P0/P1。
- 最终 calibration core/runner/pipeline 定向测试为 `13/13`、`4/4`、`28/28`；
  全仓 `584/584`（575.016 秒，0 failure / 0 error），Ruff、compileall、
  `git diff --check` 全部通过。v5 preflight `23/23`，E1 四哈希和 97 路径聚合
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`
  保持不变。

## 2026-08-26 E2-B2 机器 outcome 与 fixed-point cycle

- 新增两个显式且非默认阶段 `ootang-outcome-materializer` 和
  `ootang-prequential-cycle`，当前共 20 个可选阶段；无参数默认链仍严格是
  `features → convlstm → ootang-operational-v4`。cycle 的七步内部顺序固定为
  source ingest → bundle ensure → live reconcile → outcome materialize →
  live reconcile → issue produce → live seal。
- source current pointer 升级为 `ootang_source_current_pointer_v2`。每日 source
  revision 以 predecessor/sequence receipt 链保留，每次全局 snapshot 另写入内容寻址
  `ootang_source_snapshot_receipt_v1` 链；current pointer 必须精确对应唯一 tip。
  缺失或陈旧 pointer 只能从已完整验证的 tip 恢复，r1→r2→r1 回退、分支、
  孤儿、重复 sequence 或 object/receipt 篡改均 fail closed。旧的 pointer v1
  不做隐式迁移；实现、依赖或 epoch 合同变化也必须进入后续自动 rotation 协议。
- outcome materializer 只消费 current source 中已由 producer 递归验证的 finalized
  record，选择顺序固定为 source revision → sealed outstanding issue → 连续
  backfill，不从预测、score、当前日期或人工表格推导真值。每目标 revision
  通过内容寻址 exact object、带 sequence/predecessor 的 immutable receipt 链、唯一
  active receipt pointer 和 inbox 分层提交；在 receipt→pointer→inbox 任一崩溃窗口后
  都只能从已验证 tip/object 恢复，分支、回退、污染或同 revision 异语义拒绝。
  activation watermark 及更早记录/修订不会悄然改写旧 epoch，而是返回
  `waiting_epoch_rotation_required`。outcome 的机器时间由 invocation-scoped 单调
  sampler 贯穿初始读取、多 receipt 恢复、发布和阻断状态；跨链 `10→11→9→10`
  故障注入会撤销本轮所有公开 pointer/inbox，blocked status 保留最后成功观测时间。
- cycle 在外层只长持非阻塞 `cycle_lock`；调用子阶段时不预持
  deploy/runner 子锁，仅在收集 progress snapshot 时按 deploy → runner 短持锁以
  避免 torn snapshot。科学 token 绑定 source pointer、model manifest、verified ledger
  scientific projection、issue/outcome receipt tips、active bindings 与 inbox bytes；不绑定
  poll 时间戳、可变 status bytes、raw ledger head/event count 或持续失败的 anchor
  retry，因而无科学进展时可真正收敛。
- 空输入在一轮返回 `converged_waiting`；zero-byte 或有效 schema 但无 genesis
  的崩溃中间 ledger 作为可恢复 pre-genesis 状态，不会在 live 自动初始化前
  被 token snapshot 阻断；真正的 schema/hash 损坏仍 blocked。单次最多 64 个有科学
  进展的轮次，尚有合法 backlog 时以 `work_remaining/exit 0` 交回调度器；上次
  continuation token history 持久化后，跨调用回到已见 token 才会按振荡阻断。
- runtime 内外部 symlink alias、阶段 status 路径/schema/provenance 与非白名单状态
  均 fail closed；busy 统一为 exit 3，integrity 冲突为 exit 2。生产不允许注入假
  stage/token；仅显式 `OOTANG_E2B_ALLOW_TEST_CYCLE_OVERRIDE=1` 的测试环境可用依赖
  注入。所有 evidence/activation flags 仍固定 false。
- 最终验证：outcome materializer 31/31、cycle 23/23、cycle/main 50/50，E2-B2
  相关联合回归 197/197，全仓 566/566；Ruff、compileall 与 `git diff --check`
  均通过。独立对抗复审最终无 P0/P1。真实空 runtime 七阶段只生成 status/lock，
  一轮返回 `converged_waiting`，未生成 source pointer、activation、model、issue、
  outcome 或 ledger；无参数 dry-run 仍严格显示默认三阶段。
- 正式 v5 fail-closed preflight 23/23，报告仍为 G0 PASS、G1--G4 BLOCKED、
  G5a 未授权。E1 manifest/metrics/site/station SHA-256 仍分别为
  `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`、
  `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`、
  `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`、
  `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`；
  97 条保护路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 当前 cycle/deploy/live 配置 SHA-256 分别为
  `2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef`、
  `60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940` 和
  `bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00`。
- 该历史增量当时仍需 runner-independent checkpoint/input replay；它现已由顶部
  指定入口实现。当前仍需可信密码学时间、immutable automatic epoch
  registry/rotation、scheduler authorization，以及消除 receipt/ledger 链反复全扫的
  O(N²) 瓶颈。这些门禁不得被人工日期、冻结、批准、补签或伪造 backfill 取代。

## 2026-08-26 E2-B1 机器 source、bundle 与 issue producer

- E2-A 已提交为 `a4e7de2 feat: add autonomous prequential live ledger`。在其后新增
  三个显式且非默认阶段：`ootang-live-source`、`ootang-production-bundle`、
  `ootang-issue-producer`；默认链仍严格保持
  `features → convlstm → ootang-operational-v4`。
- source producer 严格接收 2020-06-30 之后的日连续 finalized JSON，只允许原始
  rainfall/RWL/八站 displacement，拒绝 duplicate keys、非有限值、负降雨、缺站、
  时间倒序、迟于下一自然日的 finalization 和日期缺口。在可信代码内重算
  `RWL_rate` 与 7/15/30 日雨量和，并物化 immutable content objects、推进式
  `source_current` 和 one-time no-clobber activation source。
- bundle producer 只从 immutable activation source 做 seeds 0--4 的全 as-of 固定
  120-epoch CPU 拟合，不选 best seed。checkpoint 是 tensor/primitive-only，使用
  `weights_only=True` 重载并递归核验 normalization、elevation、IDW/readout、state、
  source、schema、shape；training manifest 还绑定 deploy/base/producer、
  `pyproject.toml`、`uv.lock`、PyTorch 与 NumPy。保存/重载 tensor 精确一致，完整
  source 推理另显式固定并持久化 `1e-6 mm` reload tolerance。
- issue producer 递归复核 current/activation/model artifacts，只取 watermark 之前最后
  7 行，用五 checkpoint 内部生成 P50，并显式映射 model/live station order。目标
  必须是 E2-A ledger next target 且等于 current watermark + 1；有 outstanding issue、
  pre-genesis source 已越过 activation 或目标已进入当前自然日时均自动等待，不能
  跳日或回填。input manifest 与 issue 均内容寻址/原子发布；同科学语义复跑保留
  首次字节，冲突语义、篡改或 outcome 污染 fail closed。
- ledger 存在时 issue producer 不再信任可变 `status.json`：它在 E2-A runner lock
  内调用只读 verified projection API，完整验证 SQLite schema/hash chain 并科学重放，
  再核对 status 的 count/head/epoch/date/source/model，锁持有到发布结束。首次 issue
  还先写内容寻址 exact-byte object 和 atomic no-replace producer receipt；时间字段、
  receipt、object 或 inbox 任一篡改都拒绝，丢失 inbox 只能恢复首次登记字节。input
  manifest 同时绑定 deploy profile、issue producer、依赖锁与 Python/NumPy/pandas/
  PyTorch 版本，环境或实现变化不会被同目标幂等吞掉。
- source 非预期 I/O/CSV/竞态异常会规范化为 `SourceIntegrityError` 并 best-effort
  刷新 `blocked_integrity`，避免遗留旧 ready；source/bundle/issue 的 deploy lock 与
  E2-A runner lock 均采用非阻塞 busy 语义，bundle 不再无限等待锁。
- 最终对抗审计又收紧三条生产边界：source/bundle 的所有 runtime 子路径做 root 与
  symlink confinement，current pointer 必须精确绑定真实 immutable activation；
  checkpoint 从同一受限 bytes 同时完成 SHA 校验与 `weights_only` 加载，关闭路径替换
  TOCTOU；issue 在预测前要求 current latest displacement 与 ledger/activation 权威
  状态逐站一致，并在发布前后重采时钟。跨目标日起点的 issue 会在 runner lock 内撤出
  inbox，bundle 最终 manifest 的 write/post-check/revoke 窗口也持有 runner lock；
  receipt 崩溃恢复则先恢复已提交首发 bytes、再报告候选冲突。
- 配置只用 `*_implemented` 声明代码能力；状态只在真实操作成功后把
  `source_manifest_semantics_verified_by_producer`、
  `safe_checkpoint_loading_exercised`、
  `producer_checkpoint_inference_replayed` 设为 true。对能力边界的篡改由三个 loader
  分别拒绝。
- source/bundle/issue/E2-A/main 定向 136/136、完整仓库 505/505、正式 v5
  fail-closed preflight 23/23 通过；Ruff、compileall 与 diff-check 通过。显式四阶段
  dry-run 与真实空输入 poll 均通过；真实状态依次为 `waiting_for_daily_finalized_feed`、
  `waiting_for_semantically_validated_source`、`waiting_for_source_or_model` 和
  `waiting_for_production_bundle_or_source_snapshot`，未生成 source pointer、activation、
  model、issue 或 ledger；三项 E2-B 状态均绑定当前 deploy profile SHA
  `60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940`，等待态的
  semantics/loading/replay 实际执行字段均为 false。没有运行不存在前提的真实
  120-epoch bundle。
- E1 manifest/metrics/site/station 四项 SHA 与既有记录完全一致，97 条保护路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`；正式 v5
  仍为 G0 PASS、G1--G4 BLOCKED、G5a 未授权。
- E2-B1 当时固定 `e2_live_evidence_eligible=false`、`real_activation_ready=false`。
  后续 E2-B2 已完成 machine-only outcome materializer/cycle，顶部增量又完成指定入口
  runner 独立 checkpoint/input replay；剩余为可信密码学时间、immutable epoch
  registry/自动轮换、scheduler authorization 和 O(N²) 长链扫描优化；不得退回人工
  日冻结或人工签发。
- 设计、合同、feed 示例、运行命令与边界详见
  `docs/ootang_prequential_deploy_engineering.md`。

## 2026-08-26 E2-A 追加式 live 工程基础

- 新增显式阶段 `ootang-prequential-live`；默认链仍严格保持
  `features → convlstm → ootang-operational-v4`。该阶段单次机器 poll 后退出，
  由调度器重复调用，不需要人工逐日挑日期、冻结样本或批准状态更新。
- 新增纯函数在线数学核心和 SQLite WAL 追加式 ledger。所有写入使用
  `BEGIN IMMEDIATE` 与 `synchronous=FULL`，自然键同内容幂等、异内容拒绝，
  update/delete trigger 与完整 SHA-256 链共同 fail closed；issue 与 outcome 各自
  作为完整事务写入，8 点 issue 全部持久化并 seal 后才允许调用 outcome loader。
- runner 自动处理冷启动 genesis、无数据等待、自然日缺口、历史
  `backfill_not_blind`、外部锚请求/失败/回执、outcome reveal/score/state update、
  outcome revision 与回顾性重算。修订不回写历史事件或在线 StationState；若修订
  在下一次 issue 前到达，则机器使用最新正式位移作为 persistence 基线。
- 恢复不信任 settlement 自带状态：每次从 ledger 重放 genesis、issue、reveal、
  expert/conformal/drift 更新和 O1/O2/O3 site score，并校验事件顺序、站点顺序、
  状态哈希、输入/模型/配置/实现/环境哈希。回执文件丢失时从 ledger 自动重建，
  回执或 SQLite schema/链被改写时状态更新为 `blocked_integrity`。
- E2-A 仍是 `e2a_engineering_only_not_live_evidence`。当前 issue 预测来自外部预计算
  feed，尚未从五个 checkpoint 内部重放；input manifest 仅校验文件哈希，尚未校验
  语义；HTTPS 回执没有 pinned provider/密码学 verifier；epoch 变化仍 fail closed，
  尚无自动 registry/rotation。因此 profile/status 固定
  `input_manifest_semantics_verified=false`、`checkpoint_inference_replayed=false`、
  `trusted_anchor_receipt_verified=false`、`automatic_epoch_rotation_implemented=false`
  和 `real_activation_ready=false`。任何回执最多形成工程时序候选，E2 live evidence
  计数保持 0。
- live/core/ledger/main 定向测试共 64 项通过（live 20、ledger 12、core 7、main 25），
  冻结 G0--G4 gate 23/23 通过，全仓 414/414 通过；Ruff、compileall、显式/默认
  dry-run 和真实缺前提 poll 均通过。真实 poll 自动等待且不创建 ledger。E1 四项
  输出与 97 路径聚合哈希保持不变；完整命令和哈希见
  `docs/ootang_prequential_live_engineering.md`。
- 后续 E2-B1 已完成机器生成的 content-addressed 五种子部署 bundle 与 issue
  producer，E2-B2 又完成 outcome materializer 和 fixed-point cycle。仍缺 runner 独立
  checkpoint/input 重放、安全自动 epoch registry/rotation 和可信密码学时间。
  这些步骤都不得退回人工日冻结。长寿命部署前还需消除 receipt/ledger
  registry 反复全量扫描导致的 O(N²) 增长。

## 2026-08-26 全自动 prequential 机器闭环首版

- 新增显式阶段 `ootang-prequential-monitor`，消费固定 5-seed、3-fold 严格时序
  OOF P50 与 persistence，对 861 个 fold-date、8 个测点自动执行同日先 issue、
  后 reveal 的在线专家组合、双侧 conformal/ACI、单侧残差 anomaly、漂移重置、
  rewarm abstain 与 O1/O2/O3 连续空间聚合。算法不需要逐日人工挑段、选阈值、
  冻结样本或批准更新。
- 输出 `station_timeline/site_timeline/prequential_metrics/manifest` 分别为
  6,888/861/27/1 个记录文件；正常 point forecast 4,041 行、abstain 2,847 行、
  自动 drift 26 次。site 状态为完整 80 日、三个 block 均有可用子集 445 日、
  空间不完整 abstain 336 日。
- 三折 all-population MAE skill 相对 persistence 为
  `0.119925/0.132698/0.119456`，但 RMSE skill 为
  `-0.200329/-0.086410/0.038628`，没有稳定 RMSE 优势。active population 的
  RMSE skill 为 `0.106951/-0.035278/0.036118`，fold 2 仍为负。
- 目标 0.8 的区间覆盖率为 `0.791192/0.695061/0.630746`，后两折明显不足；
  abstain rate 为 `0.426394/0.391551/0.422038`。因此 v1 只作为内部回顾性科研
  监测器，不晋升生产；后续校准 challenger 必须新版本预声明，不能从已查看输出
  反复调参回写 v1。
- E1 使用 run-wide issue-only SHA-256 chain；runner 的 bundle validator 会从全零起点逐批
  重算链、校验 previous 链接、station/site 一致性和同 fold 跨日状态衔接。
  同日 actual 修改不改变当日 issue/hash，未来 actual 修改不改变因果前缀。
  runner 还从源 manifest 锁定的 commit `1e06629119e08b33ded2540a435e726c2d2da97a`
  取回 `data/features.csv` Git blob，核对 6,888 条 actual 和 6,888 条上一自然日
  persistence；staged CSV 以 `%.17g`/round-trip 重读后全量重放站点状态、
  site 聚合与 metrics，通过后才原子提升。
  这仍不是实时 append-only event ledger 或历史盲测；后续 E2-A runner 已实现，
  但不改变本条 E1 结果的证据等级。
- 最终产物 SHA-256：station
  `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`、site
  `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`、metrics
  `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`、manifest
  `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`；连续复跑
  完全一致。
- prequential 模块 19 项、合并定向 91 项、全量 373 项测试通过；`ruff`、
  `compileall`、`git diff --check` 与默认/显式 dry-run 通过。97 个受保护路径聚合
  哈希仍为 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 正式 v5 的 G1--G4 blocked 状态未被修改；机器支路也未生成颜色、灾害概率、
  event recall 或 FAR。其后的 E2-A 已实现 ledger/恢复/修订与不可信锚接口；真实
  激活仍需五种子推理 producer、输入语义、自动 epoch 和可信时间验证，而不是回到
  人工逐日冻结。

## 2026-08-20 G1--G4 证据盘点与机器预检

- 已完成标签、时间暴露、V0 unavailable 覆盖和指标门槛的只读盘点：当前 `G0=PASS`，`G1--G4=BLOCKED`。详细证据、待决策项与建议契约记录在 `docs/v5_g1_g4_preflight.md`。
- G1 无法关闭：仓库中没有独立五级现场真值、事件文件或标签 manifest。区间代理标签、v4/v5 规则等级、历史 onset/SHAP 规则标签以及已查看模型输出的专家复算都不得冒充独立真值；`unknown` 必须保留，不得转为阴性/绿色。
- G2 无法关闭：当前藕塘物化数据仅覆盖 2016-07-01 至 2020-06-30；model fit 为 2016-08-06 至 2019-02-02，自动 V0 selection window 则按实际 runner 合同覆盖 2016-07-01 至 2019-02-02（每站 947 行），二者不得混写。calibration 为 2019-02-03 至 2019-09-17，historical test 为 2019-09-18 至 2020-06-30；滚动验证还已将 2018-02-21 至 2020-06-30 依次暴露为外层 test，因此现有范围无法重切出 unseen 确认块。
- G3 当前覆盖仅 MJ1/MJ3：2/8 站、1,028/4,112 点日，均为 25%；两站均属 O1，当前正式 site 发布为 0/514 日。已记录 fail-closed 底线：unavailable 一律 abstain/not-applicable，禁止绿色化、插补、跨站借值、v4/常数回退、缺失后权重重归一和事后放宽分段门；部署范围、覆盖分母、site 行为和数值门槛仍待批准。
- G4 只冻结了候选指标和区间方案：共同主指标为事件召回与每 100 个阴性发布单位日的 FAR，对比 B0--B5；97.5% 单侧 exact Clopper--Pearson 事件召回下界和配对移动日期块 bootstrap 仅为未批准建议。`R_min`、`FAR_max`、各层 `coverage_min`、fusion `delta_min`、FAR 非劣效界与最低支持量均保持 null。
- 新增 `config/ootang_v5_gate_register.v1.json` 作为 G0--G4 机器状态权威源，`code/warning/ootang_v5_gate_preflight.py` 以严格 schema、路径和 SHA-256 校验 fail closed。v1 是不可就地升格的 blocked snapshot；解除 blocker 需新 schema/version、交叉哈希 manifest、对应 validator 和审查后的默认源切换。`--report` 仅报告，`--require-g0-g4`/默认模式在当前状态下必须拒绝；CLI 和生产 guard 均不允许用 path/root 覆盖默认源。G0--G4 全 PASS 也只是 G5a 必要条件；正式 G5a 还需独立版本化 run contract 及专用 guard，当前均未实现。
- 新预检模块 23 项、合并定向回归 71 项和全量 353 项测试全部通过；`ruff`、`compileall`、`git diff --check`、默认/显式 dry-run 均通过。97 个保护路径及共享 v4 Bai--Perron 源无工作树漂移。
- 本轮没有读取标签 payload，没有启动 NGBoost 训练/推理或融合，没有生成预警颜色，也没有读取或修改 Vajont 工作簿与 `review.md`。

## 2026-08-20 自动 V0 数值审计

- ATU3/MJ9 的 `negative_segment_sse` 已确认为全历史 float64 前缀原始矩相减引起的灾难性消减：失败窗口的闭式 SSE 为负，但直接残差平方和与 60 位 Decimal 参考均非负；日期严格递增且设计矩阵满秩。
- 自动 V0 wrapper 统一改为“每个候选段从自身起点重定时间/位移原点，再计算局部充分统计量”，随后执行相同的最小段长、DP、BIC 和首段选择规则。共享 v4 Bai--Perron 文件保持逐字节不变，未采用放大容差、全局均值中心化或只对失败站点重试的回退方案。
- 八站重新生成后，原六站的分段数、边界和状态不变；ATU3 改为可审计的 `first_break_is_not_accelerating`，MJ9 改为 `nonpositive_initial_segment_slope`。候选仍仅 MJ1/MJ3，V0 仍为 `0.25034204499655494/0.24813126112408163 mm/day`。
- 自动 V0 分段表由 36 行增至 48 行，完整保存八站各 6 个 BIC 选定段；v5 展示仍为 4,112 行，其中 1,028 行可用、3,084 行 not applicable。97 个 v4/ConvLSTM/NGBoost/模型保护文件在显式重生成前后哈希完全一致。
- v5 展示对上游自动 V0 改为完整 fail-closed 验证：profile/method、V0 公式、runner/shared-BIC、kinematics/predictions/forecast/v4 血缘和八站 fit 截止日任一缺失或漂移均拒绝；对应篡改负测已纳入 48 项定向与 330 项全量门禁。
- 根因、最小复现、假设检验和验收证据持续记录在 `docs/v5_v0_numerical_audit.md`；正式 v5 的标签、切分、指标、覆盖率和融合决策门记录在 `docs/v5_validation_protocol.md`。数值门关闭不解除其余正式验证门禁。
- 验证协议将运行前授权与运行后候选验收严格分开：G5b/G6b 不得作为生成自身证据的前提。G0--G4 机器预检也不会自动授权 G5a，避免用一个 register 布尔值跳过未实现的运行契约。

## 2026-08-18 v5 候选展示

- 新增显式阶段 `ootang-v5-candidate-display`，只读取既有自动 V0、藕塘运动学、ConvLSTM 原始预测和 v4 历史参考产物。默认链仍为 `features → convlstm → ootang-operational-v4`；本阶段不训练或保存模型，不调用 v4 融合，不读取 Vajont。
- `candidate_timeline.csv` 完整保留 514 日 × 8 点共 4,112 行。MJ1/MJ3 共 1,028 行标记 `candidate_available`，显示自动 V0 相关速度比和连续切线角；其余 6 点共 3,084 行标记 `not_applicable_v0_unavailable`，不补 V0 或候选字段。所有行仍保留原始速度、`ΔV`、区间状态和明确命名的 v4 历史参考列。
- 新增 `figures/v5_candidate_display_ootang_v1/`：候选时间表、8 点摘要、MJ1/MJ3 五面板图、六点 unavailable 状态表和 manifest。产物固定 `candidate_display_only=true`、`ngboost_inference_output=false`、`v5_fusion_output=false`、`formal_warning_output=false`；因此不构成新的 8 点预警颜色或 v5 综合结果。

## 2026-08-18 自动 V0 候选诊断

- 新增显式阶段 `ootang-auto-v0-direct-bai-perron`，只使用藕塘 fit 累计位移和真实时间轴做自动 BIC 分段线性选择；不人工选段、不回退 KMeans、不使用严格 MVIF 失败结果、不读取 Vajont，不改写 v4/ConvLSTM/NGBoost。
- 8 个测点均输出候选记录；MJ1/MJ3 状态为 `initial_segment_selected`，候选 V0 约 `0.2503/0.2481 mm/day`。本节初跑时 ATU3/MJ9 因数值分段失败而 unavailable；2026-08-20 数值审计修复后，ATU1--ATU5 均因首个断点不满足“后一段更快”而 unavailable，MJ9 因首段斜率非正而 unavailable。没有人工补选。
- 新增 `figures/auto_v0_direct_bai_perron_ootang_v1/`：候选表、分段审计表、8 点诊断图和 manifest。该 V0 仅为 v5 候选，不进入 v4 阈值或 NGBoost 主输入；报告已在现有 `paper/process_report.tex` 中精简更新。

## 2026-08-18 NGBoost 四指标分组消融

- 在已冻结的 h=1/3/7、五级区间代理标签、固定 NGBoost 参数和 fit-only 协议下，新增七组预声明输入：full、interval-only、分别去掉区间/速度/`ΔV`/切线角，以及去掉 station one-hot。共完成 21 次固定拟合，不调参、不排名、不选择特征集，也不保存消融模型。
- 消融输出 85,120 条 calibration/test 预测、4,116 条 NGBoost 指标、2,100 个混淆矩阵单元、5,040 条可靠性数据和 840 条相对 full 的原始差值。full 配置在三个 horizon 上与已提交敏感性逐项一致。
- 去掉 `interval_z` 后，六个 horizon×split 的全时刻 macro-F1 下降约 `0.215–0.699`、ordinal MAE 增加约 `0.343–0.913`，说明当前区间代理任务由区间持续性主导；该结果受标签定义影响，不能写成区间是物理主控因素。
- `interval_only` 的全时刻结果常接近 full，但在六个状态转移集合中 macro-F1 和 ordinal MAE 均差于 full，说明非区间运动学指标主要对状态变化提供增量。去掉 `ΔV` 后，转移行 ordinal MAE 在六个时段全部恶化，macro-F1 在五个时段下降；这是当前最一致的单项增量证据。
- 分别去掉速度、切线角或 station one-hot 的变化较小且方向混合，只能提示信息冗余或弱依赖，不能证明这些变量没有联合价值或模型可跨测点迁移。完整结果见 `docs/ootang_ngboost_interval_proxy_feature_ablation.md`。本轮仍不支持 NGBoost 进入默认主流程。

## 2026-08-17 NGBoost 区间代理 pilot 与提前量敏感性

- 新增显式阶段 `ootang-ngboost-interval-proxy-pilot`，默认链仍严格为 `features → convlstm → ootang-operational-v4`。本阶段只读取既有藕塘 ConvLSTM、逐点运动学和 v4 比较基准，不重训/修改 ConvLSTM，不修改 v4，也未读取或引入其他案例。
- 使用当前 `interval_z`、逐点速度、原始 `ΔV` 和连续切线角四项指标，加 8 个测点 one-hot 控制量，预测下一自然日的五级原始区间偏离代理状态。物理加速度、环境变量、校准后区间和 v4 融合等级均未进入输入。
- 严格同测点、同 split、一日配对得到 fit/calibration/test=`7280/1808/2288`，目标五级支持分别为 `3538/2870/701/83/88`、`531/847/287/143/0`、`584/1039/182/133/350`。calibration 无 red，相关指标明确记为不可定义。
- 固定 `NGBClassifier` 五分类参数，只用 fit 训练，不做搜索、早停、重拟合、类别权重、SMOTE、合成标签或事后概率校准。calibration/test 全时刻 accuracy 为 `0.906/0.947`、macro-F1（支持类）为 `0.905/0.925`；状态持续基线分别为 `0.916/0.952` 与 `0.923/0.936`，NGBoost 未超过简单持续性基线。
- 状态转移行上 NGBoost calibration/test accuracy 仅为 `0.132/0.209`；test 的 macro-F1 `0.229` 和 ordinal MAE `0.791` 优于多数类基线的 `0.083/1.527`，但 calibration 未稳定复现。因此该模型只保留为探索性概率 pilot，不引入主流程，不改动既有模型或论文结论。
- 版本化产物位于 `figures/ngboost_interval_proxy_pilot_ootang_v1/`，模型为 `models/ootang_ngboost_interval_proxy_pilot_v1.pkl`，完整边界与结果见 `docs/ootang_ngboost_interval_proxy_pilot.md`。
- 在模型、四指标、测点控制量、训练策略和类别处理完全相同的条件下，新增显式 h=1/3/7 提前量敏感性。fit/calibration/test 样本分别为 h1 `7280/1808/2288`、h3 `7264/1792/2272`、h7 `7232/1760/2240`；三个 fit 均含五类，三个 calibration 均无 red。
- h1/h3/h7 的 calibration 全时刻 accuracy 为 `0.906/0.781/0.715`，对应持续基线为 `0.916/0.833/0.744`；test 为 `0.947/0.860/0.675`，对应持续基线为 `0.952/0.876/0.773`。三个 horizon 的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过持续基线，且 log loss、Brier、ECE 随提前量增加而整体升高。
- 状态转移行信息随提前量增加而增多，部分转移指标优于简单基线，但没有在 calibration/test 和不同指标间稳定一致。敏感性产物明确 `selection_performed=false`、`ranking_performed=false`，不输出最佳 horizon，不改变当前“不引入主流程”的判断。完整结果见 `docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md`。

## 2026-08-15 已退役产物清理

- 按用户决定删除已退役路线的版本化产物，只保留当前 v4 链所需目录。删除 `figures/` 下 `ngboost/`、`warning_fusion/`、`warning_onset/`、`thresholds/`、`sensitivity/`、`warning_draft/`、`warning_operational_draft/`、`warning_operational_draft_v2/`、`warning_operational_draft_v3/`、`warning_review/` 共 82 个跟踪文件，另删 `pipeline/latest_run.json`（v3 阶段残留记录）与 `pipeline/shap_stability_run.json`（已退役 `shap-stability` 阶段）。
- 删除前已核验：这 10 个目录在 `code/`、`main.py` 和 `tests/` 中引用数均为 0；v4 链只读 `figures/convlstm/`，写 `figures/warning_draft_v4/` 与 `figures/warning_operational_draft_v4/`。删除后 v4 核心 manifest 的 13 个路径 SHA-256 全部匹配，`main.py --dry-run` 仍精确为 `features → convlstm → ootang-operational-v4`。
- `figures/tangent_angle/` 未删：`features` 阶段仍向其写出 `uniform_rates.csv`，删除会打断默认管线。`figures/shap/` 及 `shap/stability/` 未删：仍被 `paper/process_report.tex` 引用。
- 本次清理不改动任何 v4 数值、阈值、模型或协议内容哈希，也未启动 Vajont。删除项一律按 Git 历史（提交 `7d2e38b` 及之前）恢复，不在当前目录重建同名文件。
- 副作用：`ootang_stable_segment_expert_review.md` 与 `ootang_interval_calibration_expert_review.md` 内嵌的审查图和支撑 CSV 链接已失效，两份文档的文字结论仍有效。

## 2026-08-13 当前代码树与解释支路同步

- 当前可执行最小链严格为 `features → convlstm → ootang-operational-v4`；旧 30 日 `V0` 标签、旧预警融合 v1/v2/v3 运行入口和对应测试已从工作树移除，仅保留在 Git 历史；这里不指后续新增的 machine-prequential cycle v1/v2/v3。
- 独立解释支路改为 NGBoost 回归 + SHAP：目标是下一观测位移增量，输出候选模型依赖；它不是 ConvLSTM-SHAP、因果主控因素识别或正式五级预警分类。
- v4 数值产物重跑后仍为 4,112 条测点记录、514 条滑坡体记录和 8 行阈值；加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`。代码清理只更新来源指纹和解释产物，不改这些 v4 数值。
- 正式 NGBoost 未启动：缺少独立五级结局标签。不能把当前四指标规则输出作为标签，再以同一输入训练模型并称为正式验证。
- 本节之后的 v1/v2/v3、旧 SHAP/分类和历史测试数量均为时间戳所示的历史记录，不描述当前入口。

## 2026-08-11 v4 严格逐点加速度扩展收口（阈值来源于 2026-08-13 澄清）

- 导师确认逐点导数方法及“相同阈值”。经课题组内部方法核对后，v4 沿用速度 `V0` 基线形式和 `1×/5×/10×` 相对结构，而非不存在的严格加速度阈值表：以加速度自身 `A0` 量纲一致转置，`a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，真实 `dt`，单位 `mm/day²`，三点 warmup；raw `delta_v` 仍只作审计。
- fit-only 稳定段阈值固定为 `A=mean(a)`、`sigma_a=sample std(ddof=1)`、`A0=max(1.5A,A+2sigma_a)`；`A0<=0` 或非有限时 fail-closed。五级为 green `<A0-sigma_a`、blue `[A0-sigma_a,A0+sigma_a]`、yellow `(A0+sigma_a,5A0)`、orange `[5A0,10A0)`、red `>=10A0`。
- v4 使用 O1/O2/O3 双轴空间规则（实现最初形成于 v3 草案，但当前只由 v4 调用）；速度/切线角仍是同一运动学 family，加速度独立计票。8 点、514 日输出已复算：测点加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`，滑坡体整体 green/blue/yellow/orange/red=`8/48/31/9/18`，`valid=114`、`candidate_not_site_confirmed=400`。
- v4 核心与图件 manifest 均绑定源码指纹、输出哈希与行数、v1 基础协议及 v2 扩展协议双哈希，并保留 `formal_warning_output=false`、`vajont_used=false`。默认入口现为 `features → convlstm → ootang-operational-v4`；v3 数值仅为保留的历史快照，不再有可执行对照入口。NGBoost 正式预警模型仍未完成，Vajont 未启动。
- 本轮不改变既有 v3 核心数值 CSV；共享 runner 源码哈希变化仅刷新 v3 manifest/图件 provenance，未将 v3 数值混入 v4 阈值。

## 2026-08-08 代码库审查与工程收口

- 这是 2026-08-08 的历史工程收口记录：当时 v3 及其余阶段仍为 explicit-only。2026-08-13 后，旧运行入口与专属代码已移至 Git 历史；保留的 MVIF、6 通道和旧预警产物仍不混入当前主结果。
- 44 个测试文件已纳入 Git。当前全量门禁为 `361 passed`、`52 subtests passed`；Ruff、Python 编译检查和 `main.py --dry-run` 均通过。该门禁证明工程快照可复核，不证明藕塘数据具备确认性证据或正式预警有效性。
- 统一入口清单升级为 schema 3，逐阶段保存输入/输出路径、大小、SHA-256、源码指纹和工作树状态。当时的 `latest_run.json` 已于 2026-08-15 作为 v3 残留记录删除；当前 HEAD 尚无端到端运行清单，下次完整运行会重新生成。
- v2/v3 配置锁定的 Wang 论文 PDF 只作为空间拓扑来源证据，不是计算输入；本地副本存在时必须匹配锁定摘要，缺失时允许原型计算并在运行清单记录未核验状态，错误副本会 fail-closed。v2 历史清单未因本次代码审查统一刷新，不应据此声称所有历史字段均已更新。
- 本次没有重新训练模型、改动数值产物或启动 Vajont。Vajont 仍须用户明确授权；后续若获准，必须先冻结其角色并建立独立数据/评价目录。

本节是工程收口记录，不替代 2026-08-04 的 7 通道科学结果，也不解除 `confirmatory_evidence_gate=blocked`、`formal_warning_output=false` 或最终论文门禁。

## 当前阶段

| 项目 | 状态 | 可核对产物 |
| --- | --- | --- |
| 历史十三阶段统一管线 | 已完成（加入高程前的历史快照） | 旧运行记录中的 13/13 阶段与产物哈希；不代表当前高程感知模型已重跑全部历史诊断 |
| 藕塘高程感知最小链路 | 已完成初跑；v4 为当前默认草案 | v2/v3 数值快照保留；v4 产物见 `figures/warning_operational_draft_v4/` |
| 藕塘阶段性结果包 | 已完成 | `docs/ootang_stage_results_package.md` 统一汇总可写/不可写结论、证据门禁和后续数据决策 |
| 代码目录按研究流程分组 | 已完成 | `code/features/`、`code/warning/`、`code/explainability/`、`code/convlstm/`；入口路径已在 `main.py`、`README.md` 和 `docs/design.md` 同步 |
| ConvLSTM 高程静态通道 | 已完成初跑 | `elev_m` 标准化后经水平 IDW 形成静态网格；`figures/convlstm/forecast_run_manifest.json` 记录坐标哈希和处理方法 |
| ConvLSTM 日历后置校准 | 已完成（当前单次初跑） | `figures/convlstm/forecast_calibration_metrics.csv`；不证明上游日值生成独立 |
| ConvLSTM 配对日期块 95% 区间 | 已完成 | `figures/convlstm/forecast_bootstrap_ci.csv` |
| ConvLSTM 7 通道 fixed-120 诊断 | 三折滚动与五种子已完成；早停/容量未运行 | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/`；历史 6 通道根目录产物只作对照，不是 7 通道证据 |
| 高程与空间预警专家审查 | 已完成 | `docs/ootang_elevation_warning_expert_review.md`；400 日成因、典型日、课题组内部方法边界及高程可信性已核对 |
| v2 空间融合覆盖门禁 | 已修复 | `minimum_assessable_station_count=3` 先于全部颜色执行；2 个跨区 yellow 点反例及 v2 兼容语义均有测试 |
| 滑坡体 green/blue 语义 | v3 草案已实现并复算 | 双轴输出整体确认等级与局部最高候选；green `8`、blue `48`，局部 blue 关注 `8` 日 |
| 全时刻预警状态展示 | 已完成（非正式、观测后） | 514 日 × 8 点候选色带及 `site-confirmed/local maximum` 双轴；400 个 NC 明确不是缺测 |
| 位移—四指标—最终等级联合图 | 已完成（非正式、观测后） | 4×2 小多图覆盖 8 点 × 514 日，逐点对齐累计位移和五条状态带 |
| SHAP 跨折稳定性与特征组消融 | 已完成 | `figures/shap/stability/`；固定 5 折、5 个特征组和任务专属主指标 |
| 藕塘数据血缘 | 已审查并拆分门禁 | `source_recovery_status=unavailable_by_project_constraint`；原型初跑允许，确认性证据与正式预警阻断 |
| 新神经调参/机理消融与正式日预测 | 暂停 | 7 通道 fixed-120 结果已查看，不据此优化；早停/容量未运行，自然月分段三次结构仍限制确认性解释 |
| Vajont 案例 | 未启动 | 本轮 fixed-120 未读取、未适配、未运行；此前仅做过只读内容盘点，不构成启动，开始前必须获得用户明确许可 |
| NGBoost 区间代理 pilot | 已完成显式初跑；不进入默认链 | 11,376 条一日配对、五级概率与基线比较；calibration/test 未超过状态持续基线 |
| NGBoost h=1/3/7 提前量敏感性 | 已完成显式、非排名初跑 | 同一模型与输入并列报告；三个 horizon 全时刻 accuracy、macro-F1、ordinal MAE 均未超过持续基线，不选择最佳提前量 |
| NGBoost 四指标分组消融 | 已完成 21 次固定拟合；不保存模型 | 区间主导代理任务；`ΔV` 对状态转移的增量最一致；不排名或选择特征集 |
| 自动 V0 数值审计 | 已关闭 | ATU3/MJ9 不再因负 SSE 提前退出；八站均完成数值分段，候选仍仅 MJ1/MJ3；共享 v4 与 97 个保护文件不变 |
| v5 G0--G4 机器预检 | v1 冻结快照；G0 PASS、G1--G4 BLOCKED | 无独立标签与 unseen 块；V0 覆盖 25%；数值门槛仍 null；不授权 G5a |
| 自动 V0 候选诊断 | 已完成显式初跑；2/8 点可用 | MJ1/MJ3 形成 fit-only 候选，其余 6 点 unavailable；不人工补段、不写入 v4 |
| v5 候选展示 | 已完成显式初跑；不形成融合结果 | 4,112 行保留全部 8 点；MJ1/MJ3 可用、其余 6 点 not applicable；无 NGBoost 推断、颜色或模型输出 |
| NGBoost 未来 onset 正式调参 | 暂停 | 当前仅 3 个互不相连的可预测标签事件，不满足稳定调参与外层评价条件；区间代理 pilot 不解除该门禁 |
| 正式切线角/V0 覆盖 | 待导师或现场资料决定 | 自动 fit-only 候选仅覆盖 MJ1/MJ3；其余 6 点保持 unavailable，尚无可提升为正式 V0 的独立验证 |

## 当前滚动验证协议

1. 保持现有 ConvLSTM 结构、7 日输入和 1 日预测步长，不更换模型。
2. 使用 3 个扩展窗口折，每折测试 287 个连续日，测试段互不重叠。
3. 每折训练段末 20% 作为日历上后置的 calibration 期；标准化、增量尺度和测点 `qhat` 只使用该折允许的表格历史行。
4. 每折报告总体和逐测点误差、持久性基线、区间覆盖率、宽度、pinball loss 和 interval score，不只报告跨折均值。
5. 当前物化序列和留出时段已参与多轮分析，且上游生成独立性未知；滚动结果仅作探索性内部时间验证，不作为外部确认性证据。

> 本协议已于 2026-08-04 用当前 7 通道 fixed-120 完成三折滚动和五种子诊断。早停与容量敏感性没有随本轮运行；2026-06-21/22 的对应记录均为历史 6 通道证据。

## 2026-08-04 7 通道 fixed-120 三折与五种子记录

- 按运行前冻结协议完成 `seed=0` 三折滚动和 `seed=0-4` × 3 折的 15 个拟合；没有挑选最佳种子，也没有让测试折参与选择。
- rolling 与 five-seed 阶段的 `seed=0` 折元数据、54 行指标和 6,888 行预测在 `1e-12` 容差内复现；五种子保留 15 行运行、270 行指标、1,800 行训练记录和 34,440 个唯一完整的 `seed × fold × date × station` 预测键。
- fold 1/2 的 RMSE 和 MAE 对 5/5 种子均劣于持久性基线；平均 RMSE 分别为 `1.970/0.356 mm`，基线为 `0.245/0.120 mm`。
- fold 3 对 5/5 种子的 RMSE/MAE 仅小幅改善：平均 RMSE `0.328 mm`，基线 `0.340 mm`，平均 RMSE skill `0.036`。但预测增量标准差比仅 `0.156`，平均增量相关 `-0.041`，仅 1/5 种子为正；该优势伴随强平滑，不是稳定的逐日动态跟踪证据。
- 校准后 P10–P90 coverage 在三折为 `0.387/0.956/0.754`；fold 1 欠覆盖、fold 2 过覆盖、fold 3 略欠覆盖，不能只报 coverage 而忽略宽度和 interval score。
- 本轮是藕塘公开物化日序列上的内部探索性诊断。历史 6 通道对照只能描述版本变化，不能当作 7 通道证据或高程因果消融。
- 7 通道早停和容量敏感性未运行；Vajont 也未启动，后续开始必须先得到用户明确许可。
- 版本化产物位于 `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/`，管线清单为 `figures/pipeline/convlstm_elevation_fixed120_v1_run.json`，完整审查见 `docs/ootang_convlstm_elevation_fixed120_review.md`。

## 2026-08-04 藕塘原型文档收口记录

- 已同步 `README.md`、方法/设计/框架状态、结果、限制、进度、图件说明及 `main.py` 阶段契约；当前 ConvLSTM 主结果统一为 7 通道 fixed-120 三折 × 五种子，历史 6 通道滚动、早停和容量结果均明确隔离。
- 当前 7 通道最后一折 `seed=0` 的 14 日时间块结果已保留：模型相对持久性基线的 RMSE/MAE 差异 95% 区间均跨 0；三折 × 五种子 bundle 尚未扩展为逐折逐种子的全面 bootstrap。
- 文档已统一报告站点异质性、强平滑、区间失配及物化日序列血缘限制；该同步完成的是内部原型记录，不解除 `confirmatory_evidence_gate=blocked`，也不把结果升级为正式预警证据。
- Vajont 本轮未启动；如用户以后明确允许，须先冻结其外部验证、补充案例或方法演示角色，再建立独立数据与评价协议。
- 提交前全量门禁为 `355 passed`、`45 subtests passed`；2 项失败仍是旧 `V0` 方法名和旧切线角列断言，未出现本轮新增回归。科学证据轴与规范轴独立审查均为 P0=0、P1=0。

## 2026-08-01 v3 空间规则实施记录

- 修复 v2 的 P0 覆盖门禁：少于 3 个可评估测点时，任何 site 颜色都不能返回；v2 的“全分区仅约束 green”历史语义保持不变，当前 v2 四份产物 SHA-256 未改变。
- 新增独立 `ootang-operational-spatial-v3` 配置、融合模块、运行入口和 `figures/warning_operational_draft_v3/`（该目录已于 2026-08-15 删除，仅存于 Git 历史），没有覆盖 v1/v2。
- v3 将 `site_confirmed_level` 与 `local_max_candidate_level` 分轴。所有 site 颜色先要求至少 3 点并覆盖 O1/O2/O3；blue 也要求至少 2 点跨 2 区；未确认 yellow–red 不降级；孤立/单区 blue 记为 site green + `localized_blue_attention`。
- 514 日仍有 `valid=114`、`candidate_not_site_confirmed=400`；整体确认色为 green `8`、blue `48`、yellow `31`、orange `9`、red `18`，另有 400 日不发布整体颜色；局部最高候选为 blue `56`、yellow `196`、orange `111`、red `151`。
- v2/v3 的 4112 条测点时间线新增 `trend_component`、`transition_status`、`evidence_consistency_status` 和 `composite_warning_signal`：`ΔV` 三态现在改变完整信号和理由，但不改变五色候选，也不作为速度/切线角之外的独立投票。候选色和 514 日滑坡体统计保持不变。
- 已将六个冻结语义的代表日诊断纳入同一 v3 阶段，输出可编辑 SVG、PDF、300 dpi PNG 与 provenance manifest；图中未确认 site 显式为 `NC`，并逐日列出确认支撑、局部最高测点和 O1/O2/O3。
- 已加入 514 日完整时间线图：上半图覆盖 8 点全部候选状态，下半图并列整体确认与局部最高；400 个未确认日以灰色 NC 表示且明确为“非缺测”。
- 已加入 8 点联合诊断图：每点显示累计位移，以及 interval、velocity、`ΔV` 三态、tangent angle 和 final candidate；三类 v3 图件共用带源码指纹的公开 provenance/导出支持层。
- 所有 v3 产物继续标记 `operational_draft_not_formal`、`formal_warning_output=false`、`vajont_used=false`。

## 2026-07-30 高程感知初跑记录

- 用户确认原始 GNSS 无法取得，导师要求先使用现有公开藕塘序列和 `data/station_coords.csv` 的高程完成案例跑通；藕塘不一定用于最终论文。
- 不删除原有来源审查，而是拆分为：

  ```text
  source_recovery_status = unavailable_by_project_constraint
  prototype_run_gate = allowed
  confirmatory_evidence_gate = blocked
  formal_warning_output = false
  ```

- 修复了此前 `elev_m` 未进入 ConvLSTM 的实现落差。当前采用“8 点高程 z-score → 按 `x_m/y_m` 水平 IDW → 静态高程通道”，不把高程直接并入三维距离；输入由 6 通道变为 7 通道。
- 最小链路三阶段全部通过，最终复跑耗时约 `40.7 s`。预测表包含 fit `7288`、calibration `1816`、test `2296` 条测点记录，主键无重复。
- 最后 287 日物化 test 段总体 RMSE 为 `0.338 mm`，持久性为 `0.340 mm`，RMSE skill 为 `0.007`；校准后 P10–P90 覆盖率为 `0.770`。流程已通，但没有明显优于简单基线。
- 与提交前的无高程单种子快照相比，总体 RMSE 约由 `0.318 mm` 增至 `0.338 mm`，平均逐点 RMSE skill 由约 `0.082` 降至 `0.019`。这是事后描述，不用于反向调节模型或高程尺度。
- v2 输出包含 `4112` 个测点—时刻和 `514` 个滑坡体时刻；四项输入均无缺失。`114` 个时刻满足当前项目特有空间确认，`400` 个保留为 `candidate_not_site_confirmed`，不得并入 green。
- 所有当前产物继续标记为原型/非正式，Vajont 未读取、未运行，且启动前必须得到用户明确许可。

## 2026-07-30 高程与空间预警专家审查

- 审查报告见[`藕塘高程通道与空间预警结果专家审查`](ootang_elevation_warning_expert_review.md)。
- 高程作为静态地形先验可提高输入结构的物理合理性，但课题组内部方案的“物理引导”实际来自稳定性系数和半经验物理位移，并使用 GCN/T-GCN/ST-GCN；当前高程 ConvLSTM 是项目改造，不是该方法的复现。
- 在相同 `11400` 个预测键、观测和 persistence 下，高程版相对无高程单种子快照的 test RMSE/MAE 分别增加 `0.0196/0.0158 mm`；14 日配对块重采样的差值区间均高于 0。由于 test 已查看且只有单种子，该结果只是否定当前已显示提升，不构成确认性消融。
- 400 个未空间确认日全部为 8/8 测点和 3/3 分区有效，并非缺失：`189` 日不足 2 个 yellow+ 点，`211` 日已经达到至少 2 点但仍全部位于 O1。
- 对应 `755` 条 O1 yellow+ 测点记录的候选等级全部由区间指标决定；当前 orange/red 不能解释为速度或切线角达到同级。
- v2 的 514 日 site 输出没有 green，说明“任一 blue 即 site blue、8 点全 green 才 site green”不适合把绿色作为常态；该审查建议已于 2026-08-01 通过全局门禁修复和独立 v3 双轴草案落实。
- 本轮没有调整阈值、模型或 test，也没有读取或启动 Vajont；Vajont 仍受用户明确许可门禁约束。

## 2026-07-28 数据血缘审查记录

- 仓库 `monitoring_data.xlsx` 与 Wang 等（2025）Figshare 文件 MD5 完全一致；CSV 与工作簿 1461×17 的日期、列和数值等价。
- 8 条位移和 GWT 在 48/48 个自然月内呈三次指纹，5 个环境负对照为 0/48；月内第四差分无断点，断点集中在自然月边界。
- 首个模型目标、fit→calibration、calibration→test 三个边界均切穿同一月内三次段；跨边界恢复只作为代数依赖诊断，不写成预测性能或已证实未来泄漏。
- Figshare 的 11 个公开 notebook 没有生成该结构的代码，也没有公开原始 GNSS/GWT 锚点、日值处理链或 MJ/ATU 映射。
- 原始数据恢复现已确认不作为当前可执行路线；历史事实仍保留。
- 当前 `prototype_run_gate=allowed`、`confirmatory_evidence_gate=blocked`、`formal_warning_output=false`、`vajont_used=false`；计划中的机理性神经消融仍暂停。
- 典型状态日和结果可解释性审查已经完成；v2 门禁与不覆盖 v2 的 v3 green/blue 双轴草案也已完成。下一步等待最终论文数据集选择；若换数据集，重新建立数据契约和确认性验证协议。

## 本轮完成门槛

- 输出逐折计划、逐日预测和逐折/逐测点指标 CSV。
- 测试覆盖时间隔离、测试段不重叠、固定协议和管线产物契约。
- 同步更新 README、设计、研究框架、结果、限制和本进度文档。
- 全量测试、Ruff、编译和完整管线通过；运行清单中的源码及产物哈希可复核。

## 2026-06-21 历史 6 通道滚动验证记录

- 三个测试折均为 287 日且互不重叠，输出已通过固定种子逐字节确定性复跑。
- 模型/持久性 RMSE：折 1 为 2.123/0.245 mm，折 2 为 0.492/0.120 mm，折 3 为 0.318/0.340 mm。
- 逐测点 RMSE 优于基线数量：0/8、0/8、8/8；当前 ConvLSTM 不能表述为跨时期稳定优于持久性基线。
- 校准覆盖率：48.8%、94.9%、75.2%；第二折覆盖率上升伴随区间过宽和 interval score 恶化。
- 全量门禁：135 项测试和 32 个子测试通过；Ruff、编译、CSV 完整性及有限数检查通过。
- 当时的九阶段完整管线通过，运行清单源码指纹与代码一致，36/36 个产物哈希复核通过；最新十一阶段验收见下文。
- 功能提交：`bdf14e5`（`feat: add convlstm rolling validation`）；运行清单及本进度记录随后的维护提交另行保存。

## 2026-06-21 历史 6 通道后续诊断与外部工具筛选

- 基于已冻结的逐日预测结果开展事后诊断，未重新训练或修改参数。三折总体日增量相关系数分别为 0.182、0.148、0.011，逐测点相关系数中位数分别为 0.062、0.055、-0.068。
- 第三折相对持久性基线的 RMSE 优势伴随预测增量方差明显偏小，因此目前只能表述为该折点误差较低，不能表述为已稳定捕捉位移加速和减速过程。
- 种子 `0-4` 的固定三折训练稳定性诊断已经完成，共 15 次训练，未选择最佳种子或修改超参数。
- 已检查 `modelscope/Awesome-Vibe-Research` 及相关候选项目。PaperQA2、RefChecker 和 `nature-figure` 分别可能用于本地文献核对、投稿前参考文献验证和图件审查；Curie/EurekAgent 的实验隔离思想可参考，但其指标驱动自动优化不宜直接用于当前已查看的测试折。
- 当前未向本仓库或本机 Codex 环境接入任何上述外部项目；接入前必须取得用户明确批准。
- 工具用途、风险、采用时机和状态已持久化到 `docs/research_tools.md`。

## 2026-06-21 历史 6 通道五种子诊断记录

- 折 1/2 的 RMSE 和 MAE 均为 0/5 种子超过持久性基线；折 3 均为 5/5，说明初始化影响幅度但不改变跨折方向。
- 折 1/2 的 RMSE 为 2.385 +/- 0.574 和 0.390 +/- 0.143 mm，基线为 0.245 和 0.120 mm；折 3 为 0.323 +/- 0.008 mm，基线为 0.340 mm。
- 折 3 日增量相关性为 -0.048 +/- 0.220，预测/实际增量标准差比为 0.164 +/- 0.022；不能把点误差优势解释为稳定捕捉加速/减速。
- 所有训练 loss 下降且梯度有限，但最后 10 个 epoch 的 loss 仍下降 4.4%-8.3%。下一步应先在拟合期内部锁定时间验证和停止规则，再决定有限调参；现有校准段和测试折不得参与选择。
- 全量门禁：143 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。
- 十阶段完整管线通过，运行清单源码指纹与功能提交 `97c4acf` 一致，40/40 个产物哈希复核通过。
- 四张五种子 CSV 在独立运行和完整管线运行间 SHA-256 完全一致；运行清单及本进度记录随后的维护提交另行保存。

## 2026-06-22 历史 6 通道内层验证实施记录

- 在任何新结果产生前，已将 80%/20% 内层时间切分、300 轮上限、30 轮最少观察、30 轮耐心、0.1% 最小相对改进和验证 pinball loss 选择规则写入 `framework.md`，并以提交 `3c9a616` 单独保存和推送。
- 新阶段保留原固定 120 轮结果，不修改模型结构、学习率、输入窗口、损失函数或特征；每个种子和外层折独立选择 epoch，再在完整拟合期重新初始化训练。
- 15/15 次内层选择均由耐心规则停止；折 1/2/3 的所选 epoch 中位数为 22/7/1，范围为 16-61、3-20、1-98，没有运行达到 300 轮上限。
- 相对固定 120 轮，三折总体 RMSE 分别有 5/5、5/5、4/5 个种子改善；但相对持久性基线，折 1/2 仍为 0/5，折 3 为 5/5。训练轮数影响失败幅度，但没有解决跨时期失效。
- 第三折覆盖率由 75.2% 升至 81.1%，同时宽度由 0.471 增至 0.977 mm、interval score 由 1.037 恶化至 1.248 mm；早停不能概括为所有评价维度均改善。
- 七张结果 CSV 在单阶段运行和完整管线运行间 SHA-256 完全一致。十一阶段完整管线耗时 1172.5 秒，11/11 阶段和 47/47 个产物哈希通过。
- 全量门禁：152 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。功能提交为 `ae41ef9`，运行清单及结果文档随后的维护提交另行保存。

## 2026-06-22 历史 6 通道有限容量/正则化诊断实施记录

- 在任何候选结果产生前，已将隐藏通道 `8/16`、Adam 权重衰减 `0/1e-4`、折内五种子平均验证 loss 排名、并列规则和停止扩搜判据写入 `framework.md`，并以提交 `d13292e` 单独保存和推送。
- 新阶段保留当前 `16/0` 配置作为参照，不改变学习率、输入窗口、卷积核、特征、外层折或校准规则；外层测试不参与配置排名。
- 折 1/2/3 仅按内层五种子均值分别选择 `h16_wd0`、`h08_wd0`、`h16_wd1e4`；三个折没有共同最优配置，第一/二名 loss 差值均远小于种子标准差。
- 最终相对持久性基线的 RMSE/MAE 正 skill 种子数为 0/5、0/5；0/5、0/5；5/5、4/5。只有折 3 达到多数种子双指标正 skill，触发预注册的停止扩搜规则。
- 折 2 内层选择的小模型在外层较当前早停参照平均增加 0.070 mm RMSE 和 0.061 mm MAE；不能把内层微小排名差异解释为稳定泛化增益。
- 首次运行的严格零容差参照检查因最大 `2.22e-16` 的 CSV 浮点尾差停止，未写出结果；随后以 `5 x float64 epsilon` 锁定验证 loss 容差，并用 `1e-12` 配对指标容差避免将数值噪声标记为改善。修复提交为 `e9711cc` 和 `875f832`。
- 十二阶段完整管线耗时 1693.9 秒，12/12 阶段、56/56 个产物哈希和源码指纹均通过。八张不受配对标签修复影响的容量 CSV 与先前单阶段运行 SHA-256 一致。
- 全量门禁：160 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。功能提交为 `7507dac`，最终结果与清单随后的维护提交另行保存。

## 2026-06-23 SHAP 稳定性与组消融记录

- 在结果产生前以提交 `7456598` 锁定五折、每折背景/解释日期、88 个特征、五个特征组、回归 MAE 和分类 Brier 主指标；当前数据已被探索，协议不表述为前瞻性注册。
- 功能提交 `38713fd` 实现跨折 SHAP 排名、方向相关、组级贡献和 drop-one-group 消融，并接入统一入口为第 4 阶段。
- 首次正式运行暴露方向统计的 pandas 索引对齐错误：SHAP 数组使用位置索引，而样本特征保留原索引，导致方向全为空。修复提交 `3c06d38` 将两者显式按位置对齐；绝对 SHAP、排名和消融结果不受影响。
- 修复后正式运行耗时 3291.3 秒，阶段及 9/9 产物契约通过，源码指纹和产物哈希见 `figures/pipeline/shap_stability_run.json`。运行使用 `caffeinate -i`，避免 Mac 熄屏暂停进程；网络断开不影响本地训练。
- 回归组排名折间 Spearman 中位数为 1.000，分类为 0.500；只有位移运动学组在回归 MAE 和分类 Brier 中均为 5/5 折删去后变差。
- 环境组的删组方向不一致，不能解释为环境因素无物理作用；分类运动学贡献又与 30 日位移速率标签存在定义耦合，不能当作独立提前预警发现。
- 全量门禁在修复后为 171 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。
- 十三阶段完整管线耗时 3731.9 秒，13/13 阶段、65/65 个产物哈希和提交 `6cdcc35` 均通过。`shap-stability` 在保持输出数值一致的情况下耗时 2243.9 秒；先前单阶段 3291.3 秒的额外耗时与 Mac 熄屏暂停或系统负载有关，不作为模型性能证据。
