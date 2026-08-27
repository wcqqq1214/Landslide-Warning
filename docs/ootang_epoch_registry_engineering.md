# 藕塘不可变 epoch registry R1 工程说明

> 日期：2026-08-26--27
> 范围：candidate 预构建与 verified-ready registry；非 active switch、非 E2 激活
> R1 提交：`3d6ce8f feat: add immutable epoch candidate registry`

## 1. 本切片解决什么

R1 为后续机器自动轮换建立一个独立、非默认的不可变 registry。机器从固定的 finalized
source feed 推导稳定 slot，在 slot 的最终路径中运行 source materialization 和五 seed
bundle build，随后把固定代码、配置、依赖锁与信任材料封装成 content-addressed archival
byte capsule，并由 candidate receipt 联合绑定 source/model 血缘和空白 namespace 合同。
提交路径只有在 public source/model loaders 完成、feed lineage 精确绑定且持久化快照复验
通过后，才追加一条 `candidate_ready` registry event。历史 replay 验证 immutable feed、
candidate artifacts、capsule objects、live epoch id 与事件链；它不把已进入后续生命周期的
mutable slot 当成历史权威，也不声称重新运行完整训练或每个 checkpoint 的科学 forward。

这一步刻意不修改 live v1、cycle v3 或其单 epoch ledger。当前 live replay 强制一个
ledger 恰好只有一个 `epoch_genesis`，而 source/model manifest 又记录最终绝对路径；
因此安全轮换必须采用“稳定 slot + 每 epoch 独立 runtime root”，不能把第二个 genesis
塞进旧 SQLite，也不能 build 后移动目录。

R1 只证明“一个候选 epoch 已由机器在最终 namespace 中预构建、其提交时 runtime bytes
已快照且尚未开始签发”。它不证明候选已被激活，更不证明预测性能改善。全部状态固定保持：

```text
automatic_epoch_rotation_implemented = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
real_activation_ready = false
formal_warning_output = false
```

R1 capsule 的 `materialized_executable_tree=false`：它保存的是逐文件 hash 与 create-only
blob，不是可直接启动旧 verifier 的运行树。R2 必须补齐可执行 materialization 与隔离
启动，才能安全处理代码/依赖更新后的旧 outstanding；当前 allowlist 也是显式 provenance
集合，不是自动解析得到的 transitive import closure，本文不提前宣称该能力。

后续 R2a 已在不改写 R1 event/capsule 的前提下补齐这一切片：从 R1 immutable
tip 解析 exact 22-module closure，仅捕获两个审核过的 augmentation，物化同源非可迁移
树，并做根/可信时间双 frozen isolated 环境、prerequisite reload 与五种子 P50 烟测。
详见 `docs/ootang_epoch_preparation_engineering.md`。R1 本身的
`materialized_executable_tree=false` 仍是对历史 R1 capsule 的准确描述，不应被回写成 true。

## 2. 为什么先做 registry，而不是直接 cycle v4

直接把 rotation 插入 cycle v3 会留下三个无法诚实处理的窗口：

1. 旧 epoch 仍有 outstanding issue 时，新 epoch 不得接管该 outcome；
2. 代码或依赖改变后，当前进程不能用新 verifier 重新解释旧 ledger；
3. candidate build 后若移动路径，既有 manifest 中的 artifact path 会失真。

因此实施顺序固定为：

```text
R1: stable slot -> public prebuild/reload -> immutable snapshot verification -> candidate_ready
R2a: exact executable closure -> same-origin materialization -> isolated smoke -> PREPARED
R2b-1: clean-start route fence + epoch_drain_started -> DRAINING
R2b-2a: clean-start eligibility observation + stale detection -> still DRAINING
R2b-2b v2: bounded-workset non-clean recovery -> still DRAINING
R2b-assessor: independently prove bounded work closed -> DRAINED eligibility
R2c: authoritative atomic SEALED/ACTIVE transition
R3: cycle v4 + trusted-time qualification + scheduler authorization
```

R2a 已保存可执行 capsule/tree/smoke 证据，但没有 drain authority。R2b 首切片在
`manager → cycle → deploy → runner → replay → shadow` 全锁下，通过不可降级的 macOS
`renameatx_np(RENAME_SWAP)` 原子撤销 canonical issue route。它先内容寻址固定 full
intent-prefix/capsule，再于 tombstone mkdir 前 create-only 固定历史 R1/R2a/route/ACL policy
的 singleton fence-prepare；每 poll 证明 start→current append-only，最终 boundary 在 event
前完整自重放。manifest/staged-feed 上限分别为 64 MiB / 16 MiB；swap 前 worst-case/actual
capacity 门后先固定 exact pre-swap boundary、追加 append-only `drain_exchange_attempts` 并在
fence 内武装 terminal marker，再 immediate-swap；正常 same-poll state 要求 exact，exchanged
recovery 从 armed terminal 复用旧 boundary，只以 current 合法 extension 作 gate。WAL/marker/
boundary 只有 recovery authority，唯一 DRAINING lifecycle authority 仍是随后追加的
`epoch_drain_started`。首版只接受无 outstanding/pending guard/trusted/shadow 的 clean start；
否则机器等待。old work 可在两次 poll 间继续，合法 settled extension 由后续完整 replay
自动纳入，不做人工 cleanup。R2b-2a observation/stale detection 已建立：它从 historical
R2b binding 完整重放，在同一六锁下发布 deterministic clean observation 与 append-only
event；同 state 幂等、合法 extension 自动刷新，pending/capacity 只机器等待，所有
drained/active claims 仍 false。下一步用新 v2 schema 建立 bounded-workset non-clean
recovery；v2 不得重解释或覆写已发布的 v1 fence-prepare/intent-prefix/capsule/intent/
exchange-attempt/armed-marker/boundary/drain-event/eligibility-observation/event bytes，
历史增长需要的 chunk/Merkle 也只能由该新版本表达。
后续 assessor 证明所有历史工作合法收口后，权威 transition 才可同时 seal old / activate
new。若 outcome 永不到达，机器只能持续等待，不能补 outcome、人工冻结/批准或强制切换。

## 3. 机器状态机

R1 没有 target date、freeze、approve、force、backdate 或人工签名参数：

```text
missing finalized feed
  -> waiting_for_candidate_feed

verified feed
  -> independently validate the complete frozen Ootang feed contract
  -> append/create-only feed observation and refresh its tip witness
  -> reject source-id/export/revision rollback against the registry chain
  -> capture reviewed static bytes into the archival capsule
  -> derive stable slot id
  -> create-only copy into slots/<slot-id>/live/incoming/
  -> materialize source in final slot
  -> build/reload five-seed bundle in final slot
  -> prove source lineage daily_feed_snapshot == registry feed bytes
  -> prove future issue/outcome/ledger/guard/replay/time namespaces are empty
  -> snapshot feed and every declared runtime artifact into registry objects
  -> recapture the reviewed worktree and reject mid-build change
  -> jointly bind capsule + immutable runtime snapshots in a candidate receipt
  -> append candidate_ready event
  -> reconstruct mutable status/head from the immutable registry chain
```

事件名保持 `candidate_ready`；可变 status 刻意写成
`immutable_candidate_record_ready`，表示 R1 只证明已提交的快照记录，不能把它误读为
“当前 mutable slot 仍可直接激活”。R2 激活前必须从 capsule/snapshots materialize 并验证
PREPARED lifecycle。

slot id 只由规范合同和输入 bytes 推导，不含 wall-clock 或临时路径。feed observation 在
长训练之前写入独立 create-only previous-hash 链。单个 observation event 自身携带并校验
精确 feed bytes，因此它是一次 create-only+fsync 的水位 commit；object-store feed 只是
之后可从 event 自动恢复的派生副本，不存在“object 已落盘、event 未落盘却算已观察”的
双文件语义窗口。它的 mutable tip witness 可恢复陈旧值，
但保留的更高序号不能被静默删尾。因此已经严格验证的 feed 即使随后 build 等待、receipt
成为 orphan 或进程崩溃，仍是反回滚最高水位。同一候选重跑必须幂等返回；同一自然身份
出现不同 bytes、序号分叉、previous hash 不连续或已发布对象被替换时 fail closed。构建
在 source、model 或 capsule 完成前中断，只能留下无 candidate 权威事件的
orphan；完整 receipt 若已发布而 event 未发布，下一轮同时复验 receipt 的 immutable
snapshots、当前 artifact bytes 和提交时空 namespace 后补交 event，不会再次训练。同 slot
已成为 registry tip 时只验证历史快照、不重建；feed 回到旧 export/revision/record bytes
会在任何新 receipt/event 发布前作为 rollback 阻断，不能把旧候选重新提升为 tip。

`checked_at_utc` 可使用测试注入 clock，但默认 source ingest、五 seed build 和最终 reload
都显式传 `now=None`，由各生产模块在真实动作边界重新读取机器时钟。长训练跨越 model
publication deadline 会由 bundle 合同看见，不能把轮询起点的一次采样当作人工冻结时间。
feed observation 自身也在 production 路径重新采 UTC，并在写入前 exact-check feed/record
schema、finalized、时间因果与自然日边界、从 2020-07-01 起连续 extension、有限数值、
非负 rainfall、8 站集合及顺序；future/invalid feed 不会污染反回滚水位。

## 4. Registry 与 capsule 证据

每个 registry event 使用严格 UTF-8 finite canonical JSON，至少绑定：

- 精确 `sequence_id = previous + 1`、event type、previous/entry SHA-256；
- 独立 feed-observation sequence/head；candidate feed 必须按 observation 次序出现；
- stable slot/runtime root、candidate id 与重算 live epoch id；
- finalized feed、source lineage 中完全相同的 daily-feed snapshot、activation/source/data
  manifest、watermark 与 outcome source；
- outer model manifest、training manifest 和五个 checkpoint；
- live/deploy/cycle-v3/replay/verified-live/calibration-shadow/trusted-time profiles；
- trust manifest、leaf/root、隔离 trusted-time project/lock；
- 根 `pyproject.toml`、`uv.lock` 和固定 code/config capsule tree；
- candidate 的 ledger、issue/outcome、guard/replay/time namespace 在 candidate commit 时
  尚未初始化；后续合法 activation 不会使历史 replay 失效；
- 所有 E2、trusted、activation、rotation、formal claim 为 false。

mutable status/head 不是权威；删除或污染后只能从完整 event chain 与 immutable objects
重建。每次公开 load 都重新读取 stable regular file、核对 pathname/open inode、mtime/
ctime、大小、哈希、canonical bytes、路径 containment 和引用对象，不信任 status 中自报
的 ready 布尔值。历史 capsule 固定其当时的 registry implementation object；代码升级后
不再用“当前 implementation hash”重新解释旧 capsule，新 candidate 则捕获新实现。

## 5. 文件系统、锁与恢复边界

- registry manager lock 使用 non-blocking `flock`，并拒绝 symlink、非 regular file 和
  获取锁前后的 inode/pathname 替换；
- 所有 runtime 子路径必须严格位于 registry root，slot、object、event 与 status 不能
  alias 或逃逸；
- blob、capsule manifest 和 registry event 均 create-only；同路径同 bytes 幂等，
  同路径异 bytes 阻断；
- create/cache 临时文件只写入同一 registry filesystem 的非权威 `.tmp/`，永不进入
  event/receipt 枚举；hard-link 或 replace 后对权威 parent directory 执行 `fsync`；
- 持有 manager lock 后，机器自动删除仅匹配生成器命名且为 regular file 的 crash temp；
  这也移除 SIGKILL 留下的 authority hard-link alias，并 `fsync` `.tmp`，未知 entry 则
  fail closed；
- candidate 必须直接在最终 stable slot 构建，不允许 staging 后 rename；
- source/bundle producer 的既有 deploy/runner 锁仍由其 public API 管理，registry
  不调用跨模块 private lock helper；
- runtime/prebuilder/source-feed override 只存在于 private isolated-runtime 测试入口；
  production `poll_epoch_registry()` 和 CLI 的公开签名根本不接受这些参数，只能使用 profile
  固定 feed 和默认 public prebuilder；
- R1 不创建 candidate ledger，不签发 issue，不读取 outcome，也不触碰旧 live root。

`live_epoch_id` 使用现有 live-v1 的精确 canonical-digest 公式（canonical JSON 不带文件
末尾 LF），回放时从 activation-source manifest、model manifest、watermark、live profile
和 implementation/environment hashes 重新推导；不能接受一个格式正确但语义任意的 hash。

本地 SHA-256 registry 仍属于 trusted-writer 完整性模型：单机 root 权限可同时删除对象、
事件和本地 anti-rollback 状态。`.tmp` 隔离、file/parent `fsync` 覆盖已建目录内的进程
崩溃恢复，但本切片没有证明所有首次 `mkdir` 在突然断电后的持久性；也没有抵抗恶意本地
writer 在两次目录检查之间替换整个父目录。后续 transition 必须绑定 RFC 3161 回执；若
需要抵抗本机根权限失陷，还需独立 operator/KMS 或远端透明日志，不能把同一主机上的两
把 key 写成虚假的 2-of-2。

## 6. 方法依据

本实现借用而不照搬 TUF：逐版本 `N -> N+1`、previous-root/new-root 连续验证、
consistent snapshot 和 freshness/rollback 语义适用于 registry，但本项目不需要完整
`root/timestamp/snapshot/targets` 网络仓库。

- [TUF v1.0.36：root update 与 rollback/freeze workflow](https://github.com/theupdateframework/specification/blob/v1.0.36/tuf-spec.md#L1290-L1379)
- [TUF v1.0.36：consistent snapshots](https://github.com/theupdateframework/specification/blob/v1.0.36/tuf-spec.md#L1620-L1653)
- [in-toto ResourceDescriptor](https://github.com/in-toto/attestation/blob/2dcd055e9f72e746687c306e35f4e59720ff45be/spec/v1/resource_descriptor.md)
- [Sigstore TrustedRoot：历史实例、validity overlap 与 operator](https://github.com/sigstore/protobuf-specs/blob/0342fe5797edd558c58098033220fb27a2542a28/protos/sigstore_trustroot.proto#L95-L178)
- [Sigstore root-signing 自动化实现](https://github.com/sigstore/root-signing/blob/ebc52304c5c7e47c89a310216e889cf305dc770f/README.md#L43-L89)
- [RFC 3161](https://www.rfc-editor.org/rfc/rfc3161.html)

in-toto 自身明确不能阻止仍未过期旧 layout 的重放，SLSA provenance 的 dependency
completeness 也不能替代本项目的 epoch 全链验证。因此本文不宣称 in-toto/SLSA 等级，
也不把供应链完整性等同于预测有效性。

## 7. 验证记录

最终实现与 profile SHA-256 分别为
`1418b754012b71b296c200539efa846cea63e5a4374e44d216bd656f8b047b9c` 和
`c56004649689ee8aa4beeca6a7c61bbdd5529ec62706880ea8eac65a8ca18edd`。提交前验证为：

- registry 定向测试 `40/40`，registry + pipeline 测试 `72/72`；
- 全仓 `765/765`（344.829 秒，0 failure / 0 error）；
- Ruff 全 `code tests main.py`、四个本增量 Python 文件的 format check、`compileall`、
  strict JSON `27/27`、根/可信时间隔离项目 lock check 和 `git diff --check` 均通过；
- v5 preflight `23/23`，报告仍为 G0 PASS、G1--G4 BLOCKED、G5a 未评估/未授权，
  `formal_warning_output=false`；
- 无参数 dry-run 仍严格为 `features -> convlstm -> ootang-operational-v4`，显式 registry
  dry-run 只运行 `ootang-epoch-registry`；
- 97 个保护路径无 diff，聚合 SHA-256 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`；根
  `pyproject.toml`/`uv.lock` 仍分别为
  `bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0`/
  `f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c`。

两轮独立只读审计最终均为 P0/P1 `0/0`。审计中发现的 event sequence exact-int 与
capsule reference size exact nonnegative-int 边界已修复并加入对抗测试。保留的 P2
工程边界是：本地 trusted-writer/任意并发 writer 不具备外部原子隔离、首次目录创建的
完整掉电持久性未证明、observation 保存全 feed 且累计 replay 为 O(N²)，以及 R2 尚需
materialize transitive executable closure；这些边界未被包装成已经解决。

## 8. R2b 首切片与后续 assessor

R2a 已完成 exact transitive local closure、same-origin tree materialization 与双域/五种子
smoke，但没有因此产生 drain authority。R2b 首切片新增独立 `drain_events/`、
`drain_head.json`、`drain_status.json`、`drain_fence_prepares/`、`drain_intents/`、append-only
`drain_exchange_attempts/` WAL、内容寻址
drain capsule 与 tombstone namespace，并在 shared objects 中发布内容寻址 full
intent-prefix、staged-feed 与 full-clean boundary；它只引用
R1/R2a，不改写既有 registry/preparation event。intent 是 durable transaction
reservation/lower-bound；其中 `candidate_at_intent` 只是恢复绑定，不是 activation selection。
WAL、armed marker 与 boundary 只有 recovery authority；orphan fence-prepare/intent-prefix/
intent/capsule/staged-feed/attempt/boundary object 本身均无 lifecycle authority。唯一 DRAINING
lifecycle authority 是 `epoch_drain_started` event。

机器先要求旧 epoch clean：无 outstanding live issue、无未完成 verified-live guard、
trusted-time request 或 calibration shadow 工作。缺 R1 tip、缺或落后 R2a tip、缺 old-live
prerequisite/ledger 或上述任一 pending 状态都只写 waiting status，不产生 event，也不通过
人工清理跨门。一次 waiting poll 不会冻结旧 epoch；旧 work 可继续，下一 poll 仍 pending
就继续等待，合法 settled extension 会被 replay 并自动纳入新的 full-clean boundary。

clean start 后，coordinator 按固定
`manager → cycle → deploy → runner → replay → shadow` 顺序持有全部锁。capsule 精确引用
full `ootang_epoch_drain_intent_prefix_v1`，保存 live/shadow 全 entry hashes 与
issue/outcome/guard/trusted/source inventories；每次 poll 分字段证明 start→current
append-only/prefix extension。机器捕获 old-route identity 后，在任何 tombstone `mkdir` 前
create-only 发布永久 singleton `ootang_epoch_drain_fence_prepare_v1`，绑定历史 R1/R2a、
capsule/prefix、old-route identity、tombstone path 与 ACL/swap policy。crash 或 current tip
推进后仍恢复该历史 transaction；marker 自身无 lifecycle authority，也不是 activation
selection。

marker 落盘后，机器才在 mode `0755` 的空 tombstone 安装并 exact-readback Darwin extended
ACL `everyone deny write`，且要求真实 add-file 拒写探针通过；随后写入并复验包含 ACL/inode
的绑定 intent，完成下述 capacity/boundary/WAL/armed-marker 序列后再调用
macOS `renameatx_np(RENAME_SWAP)`，把 canonical old `issue_inbox` 与 tombstone 原子交换。
ACL 随 inode 跨父目录交换后立即围栏 canonical route，随后机器原位 chmod exact `0555`
并再次复验 ACL/拒写。若崩溃发生在 swap→chmod，只允许向前加固，绝不交换回旧 route。
平台、ACL 或原语不满足时禁止退化为普通 rename。物理 issue-admission boundary 是 Darwin
swap 成功的瞬间；权威 state-snapshot boundary 是六锁下 full-clean replay 形成的内容寻址
pre-swap 对象，再由唯一 `epoch_drain_started` 精确引用。该对象绑定 live/issue/guard/trusted/
outcome/shadow/source inventories 与 staged next-epoch incoming，后者不是 old source
authority。完整 replay 得出的 lifecycle 仍只是 `DRAINING`；candidate selection、drained、
active/rotation、trusted anchor、E2 与 formal claim 全部为 false。

v1 显式固定 manifest 最大 64 MiB、staged next-epoch feed 最大 16 MiB。feed 作为独立内容
寻址 object 保存，boundary 只引用它，event replay 使用相同 16 MiB 上限，因此合同内的大
feed 不会因较小 control-object 限额而自锁。event append 前机器必须完整自重放 boundary、
fence-prepare、intent、capsule/prefix、全部 inventories/source/staged object，并精确重建拟
提交 clean state。

swap 前机器先以 maximum-size staged CAS reference 计算 worst-case boundary；超过 64 MiB
返回 `waiting_for_drain_boundary_capacity`，canonical route 保持未交换、无 event、无人工
cleanup。通过后严格执行 final fence verify → actual queue stable double-read/CAS → actual
capacity → publish exact pre-swap full boundary → append/replay previous-hash-linked WAL
terminal → 在 fence operand 内写入 `.epoch-drain-armed-attempt.v1.json` 直接绑定
terminal+boundary → immediate Darwin swap；marker 随 inode 原子移动。每个 attempt 精确引用
一个 pre-swap full boundary，只可武装 unique terminal。正常同 poll post-swap logical clean
必须与 pre-swap state 精确相等，post-swap publisher/self-replay 继续复验。prepared retry 只
自动恢复严格单一 marker temp 与 exact/缺失 ACL crash state；already-exchanged recovery 必须
从 armed terminal 读取旧 boundary，current clean 只作合法 append-only extension gate，不能
重建 boundary。suffix rollback、branch、gap、extra、symlink 或不精确 temp/ACL 均 fail closed。

R2b-2a clean-start eligibility observation/stale detection 已实现跨 poll deterministic
observation、stale/extension 识别与 historical source-object 复验，结果仍只可为 DRAINING；
下一步以新 schema 做 R2b-2b v2 bounded-workset non-clean recovery，支持 trusted-time/
guard/shadow 等明确 workset。v2 不得重解释、补字段或覆写 v1 fence-prepare/intent-prefix/
capsule/intent/exchange-attempt/armed-marker/boundary/drain-event/eligibility-observation/event
bytes；超 64 MiB 的历史 chunk/Merkle 设计也属于该未来 v2。再后的独立 assessor
才能证明旧 namespace 已
收口，独立 transition 切片才可追加单个权威 `SEALED(old)+ACTIVE(new)` 事件并从 registry
tip 恢复 active cache。closed epoch 的迟到 revision 需进入独立跨 epoch retrospective
chain，不重开旧 online state。全程无人工日期、冻结、cleanup、批准、force 或 backdate；
结局不到达时只能机器等待。

R2a 的真实默认链试跑已在历史 feed causal gate 正确 fail closed，因此当前没有真实 R1
candidate，也没有 R2b end-to-end PASS。这一结果不能以改时钟、合成日期或测试 override
重写为成功。
