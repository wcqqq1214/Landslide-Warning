# 藕塘旧 epoch manifest-keyed recovery R2b-2b-2c 工程说明

## 1. 范围与结论

R2b-2b-2c 是显式、非默认、仅机器运行的局部恢复切片。它只接受 R2b-2b-2b 已发布的
singleton reservation event 及其 content-addressed manifest，并且每次 poll 最多推进其中一个
exact natural key。该阶段不重新枚举 workset，不追加新 key，也不根据当前运行态改写 manifest
冻结的 successor。

首版只覆盖三类可由本地既有证据唯一重建的 deterministic adapter：

1. `anchor_receipt_repaired`：由已经进入 live logical ledger 的唯一 `anchor_confirmed` event 和
   对应 seal 重建相同 stored anchor receipt；
2. `trusted_time_request_der_repaired`：由 manifest 绑定的 request、原 nonce、message imprint、
   policy OID 与 evidence envelope 重建相同 RFC 3161 DER，不创建新 nonce；
3. `superseded_by_backfill`：当 guard intent 没有 live open/seal 边界，且冻结 ledger 中已有唯一
   backfill/settlement 持久证据时，记录该 key 的 recovery disposition；单纯机器时间越过目标日
   起点不构成 backfill 证据。

这不是完整六族恢复器。它不执行网络请求，不写 live/shadow logical ledger，不物化 outcome，
不推进 issue route，不生成 guard completion，也不关闭或切换 epoch。

## 2. Authority chain 与锁

每次 poll 必须先按固定顺序获取仍存活的四锁：

```text
manager → cycle → replay → shadow
```

在同一锁域内，coordinator 完整重放 admission-cut authority，确认 deploy/runner canonical lock
path 仍是 `both_cut` 的 deny-write sentinel；随后重放 workset reservation event 及其 manifest。
恢复 authority 至少绑定：

- admission-cut event、terminal attempt 与 frozen authority context；
- reservation event 的 `entry_sha256`；
- manifest 的 path、SHA-256、size 与 `profile_sha256`；
- `workset_keyset_sha256`、item count 和每个 item 的 `namespace_digest`；
- recovery profile、coordinator implementation 与 adapter implementation provenance。

coordinator 的实现 SHA-256 会进入 global intent，intent 一旦存在，后续代码漂移即 fail closed。
首次发布前的实现审查根仍是版本化 Git checkout；本切片没有额外的外部签名/硬件 trust anchor，
因此 `external_implementation_trust_anchor_implemented=false` 与
`anti_rollback_authority_implemented=false`。运行时 provenance 不能被解释为这两项能力。

历史 manifest 重放只验证 immutable bytes、内部 digest 和引用关系，不要求所有 mutable
predecessor 仍保持 reservation 时的 bytes。当前 predecessor 的 exact-CAS 只在选择某一个 key
之后、执行该 key 之前发生。否则第一个合法修复会使后续 poll 无法重放旧 manifest。

恢复器不获取 canonical deploy/runner lock，也不在四锁内调用会再次获取相同锁的 public poll。
adapter 必须使用明确的 “caller already owns locks” 窄接口或无锁内部原语，避免自锁及绕过
admission-cut sentinel。任一锁 busy、cut 不再成立、event/manifest 分支或引用漂移时均 fail
closed，且不得写 recovery authority。

## 3. Global intent、item intent、receipt 与 event

恢复 namespace 使用 create-only、canonical JSON 和 durable publish。全局 intent 必须先于任何
predecessor mutation 发布，并固定 reservation identity、完整有序 keyset、依赖图摘要、支持的
adapter 集合和单 key poll 策略。natural key 不直接作为文件名；路径键由 manifest SHA-256、
natural key 与 namespace digest 的 canonical payload 计算。

一个 key 的 crash-forward 顺序为：

```text
replay global intent
  → verify dependency receipts
  → publish/adopt item intent
  → exact predecessor CAS
  → probe pending/already-applied/conflict
  → apply at most one deterministic local action
  → verify exact successor evidence
  → publish/adopt item receipt
  → append one recovery event
```

item intent 固定 family、natural key、namespace digest、manifest 中声明的 successor、dependency
receipt refs、predecessor/read set、允许的 write set 和 adapter provenance。receipt 绑定 item
intent hash、实际 successor evidence、before/after semantic digest，并区分 `applied` 与
`adopted_already_applied`。

receipt 缺失不能被解释为 mutation 尚未发生。若进程在 mutation 后、receipt 前崩溃，下一次
poll 必须先通过 adapter probe 识别 exact successor；完全一致时只补同一 receipt，不重复产生
副作用，部分状态或异义状态则阻断。recovery event 是 receipt 的 append-only 索引，不替代
receipt 本身；`status.json` 仅是可修复 cache，`cache_authority=false`。

本切片每 poll 最多选择一个 dependency-ready key。选择顺序由 manifest family order 与 natural
key 稳定排序决定。dependency 缺失、环、重复 key，或未被依赖边定序的 read/write 冲突都必须
在 action 前拒绝。部分完成后的 predecessor 期望态由 manifest baseline 加已深度验证的依赖
receipts 推导，不能对所有 item 反复盲比原始 manifest bytes。

## 4. 三类 deterministic local adapter

### 4.1 Live anchor receipt repair

该 adapter 只接受 successor 为 `anchor_receipt_repaired` 的 manifest item。它重放 frozen live
logical chain，要求唯一 `issue_batch_sealed` 与唯一 `anchor_confirmed` 对应同一 seal，并使用
live core 的 stored-anchor payload builder 生成预期 canonical bytes。写入目标只能是 manifest
绑定的缺失 receipt 路径。

若 receipt 已存在且 bytes 完全相同，则采用 `adopted_already_applied`；同路径异 bytes、orphan
receipt、seal/confirmation 不唯一或 current source prerequisite CAS 不匹配时阻断。该修复只恢复
本地 receipt 文件，不把时间锚提升为 trusted anchor，也不改变 live ledger。

### 4.2 Trusted-time request DER repair

该 adapter 只接受 request record 已存在、DER 缺失且没有 response link/receipt downstream chain
的 reserved key。它验证 request record 的 profile、target、envelope、nonce、policy、imprint 与
manifest authority，调用同一 DER builder 重建 exact bytes，并以 request 内预存的 path、SHA-256
和 size 做 create-only 发布。

DER-without-request、缺 DER 但已有 downstream response chain、DER 同路径异 bytes、nonce 或
envelope 漂移均为 integrity failure。该 adapter 不访问 TSA 网络，不创建新 request，不替换
nonce，也不宣称 response 或 receipt 已验证。

### 4.3 Guard superseded disposition

该 adapter 只接受 manifest 已冻结为 `superseded_by_backfill` 的 key。执行时必须再次重放 guard
intent、pre-head 与 frozen live ledger evidence，确认不存在 open/seal 边界，并确认唯一既有
backfill/settlement 持久事件仍精确支持 supersede。若 inventory 只是因为机器时间越过目标日而
选择了该 successor，adapter 必须保持 waiting，不能按名称过度声明 backfill。

当前 guard core 没有与 `superseded_by_backfill` 等价的 durable guard completion/event schema。
因此首版只能在 recovery namespace 发布绑定既有持久证据的 disposition receipt 和 recovery
event；不得伪造 `guard_completion_recorded`，不得向 guard completion namespace 写入自创格式，
也不得把 receipt 解释成 live ledger terminal event。后续 drained assessor 若需要 guard-native
持久证据，必须先新增并独立审查 guard core schema 与 validator。

## 5. 明确不支持的 mutation

以下 action 即使出现在 reservation 中，也只能报告 `unsupported_reserved_successor` 或 waiting，
不得跳过、重分类或人工清理：

- TSA HTTP 请求、response object/link 获取及 cryptographic receipt 验证；
- live、shadow 或 issue-replay ledger append；
- anchor request/result 创建、outcome settle/materialize/consume、source snapshot ingest；
- issue replay receipt 生成或 route consume；
- guard completion 写入；
- shadow settlement、classification 或 cursor 推进；
- 任意未在 frozen manifest 中出现的 key 或 successor。

网络 action 具有释放锁、外部副作用和响应非确定性，不能套用本地 create-only adapter。ledger
mutation 还需要 sequence/previous-hash transaction、SQLite durability 与 mutation-before-receipt
恢复协议。它们必须在后续切片分别实现，不能由 generic 文件写 adapter 代替。

## 6. Capability 边界

本阶段即使三类 adapter 均可运行，也只能声称 manifest-keyed deterministic local recovery 的
工程能力。至少以下 capability 继续为 false：

```text
bounded_workset_recovery_implemented
all_reserved_successors_supported
network_recovery_implemented
ledger_mutation_recovery_implemented
old_work_admission_fence_implemented
direct_filesystem_writer_fence_implemented
v1_v2_mutual_exclusion_implemented
anti_rollback_authority_implemented
external_implementation_trust_anchor_implemented
lifecycle_authority
transition_authority
drained_eligibility_current
old_epoch_drained
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
trusted_anchor_receipt_verified
e2_live_evidence_eligible
real_activation_ready
formal_warning_output
```

waiting、unsupported、busy 或 blocked status 不得把 profile 中的实现能力误报为当前 key 已恢复。
只有 item receipt 深度复验成功时，该 key 的当前完成标志才可为 true；只有 manifest 全 keyset
均有合法 receipt 时，未来 terminal recovery event 才可宣称完整恢复。

## 7. 故障恢复与测试边界

定向测试应覆盖：immutable event/manifest replay；依赖排序与每 poll 单 key；predecessor CAS
漂移；global/item intent create-only adoption；三个 adapter 的 pending、already-applied 和 conflict；
intent 后崩溃、mutation 后 receipt 前崩溃、receipt 后 event 前崩溃；orphan/branch/unknown entry；
waiting/busy/unsupported/blocked status false claims；四锁逆序释放；guard receipt 不越权为 guard
completion；TSA 网络函数与 ledger writer 在本切片不可达。

测试不需要穷举所有容量边界、所有 symlink/fsync 排列，也不运行模型训练、NGBoost、SHAP、
ConvLSTM 或真实 TSA 网络。最终定向 recovery/manifest/admission-cut/drain-v2/main 快测为
76/76（0.998 s）；Ruff、format、compile、strict JSON、stage list、默认/显式 dry-run、97 个
protected path 与 11 个 frozen writer 检查均通过。profile、实现与测试 SHA-256 依次为：

```text
c958a407cd5903c4fdff5e1e22e79af3c6669194506b136b0e44948a88a4bb3e
7ac9e8c63d38b80a193b3a7c10bf10204c11987511fe120e369ee25f928e8652
9c20a4ecc029b7d0ba4c58f9e4801313100b4748a1cf03c6c559956e1ddc6c4d
```

本阶段未运行模型训练、真实 TSA 网络、真实 runtime mutation、全仓测试、大容量测试或穷举
filesystem/crash 矩阵。

## 8. 下一阶段

下一阶段应优先定义 ledger-native、可重复探测的 action transaction：明确 read/write set、序列号与
previous-hash CAS、mutation 后 receipt 前的 adoption，以及与 admission-cut sentinel 共存的锁
接口。网络 trusted-time action 必须另设外部请求 intent、释放锁前后的 fence generation CAS 和
response object adoption。

当且仅当所有 manifest key 都由各自受审 adapter 收口，并由独立 assessor 深度重放 terminal
recovery event，才可进入 post-recovery drain eligibility。该后续观察仍不能自动推出
`SEALED(old)+ACTIVE(new)`、epoch switch 或 formal warning authority。
