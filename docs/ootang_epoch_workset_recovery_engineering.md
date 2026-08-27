# 藕塘旧 epoch manifest-keyed recovery R2b-2b-2c 工程说明

## 1. 范围与当前结论

R2b-2b-2c 是显式、非默认、仅机器运行的局部恢复切片。它只接受 R2b-2b-2b 发布的 singleton
reservation event 及其 content-addressed manifest，每次 poll 最多推进一个 dependency-ready natural
key 的一个 transition step。该阶段不重新枚举 workset，不追加未冻结 key，也不允许人工冻结、人工
补单或人工清理来绕过协议。

冻结 manifest 的准确语义是：它完整记录 reservation 时观察到的六个 family，并为每个已观察 key
提供 transition seed。它不是每个 key 的 terminal/transitive closure；首个 action 可能产生 content-
dependent、reservation 时尚不存在的后续 work。因此，恢复器现在为每个 key 固定版本化 transition
plan，并使用 step intent/step receipt 链推进，不能再把“首个 successor 已写入”误报为“该 key 已
终结”。

当前可执行 adapter 仍只有三类既有本地确定性 action：

1. `anchor_receipt_repaired`：由已进入 live logical ledger 的唯一 `anchor_confirmed` event 与对应
   seal 重建相同 stored anchor receipt；
2. `trusted_time_request_der_repaired`：由 manifest 绑定的 request、原 nonce、message imprint、
   policy OID 与 evidence envelope 重建相同 RFC 3161 DER，不创建新 nonce；
3. `superseded_by_backfill`：当 guard intent 没有 live open/seal 边界，且冻结 ledger 中已有唯一
   backfill/settlement 持久证据时，记录该 key 的 recovery disposition。

其中 `trusted_time_request_der_repaired` 明确是非 terminal step：它的 receipt 只允许下一步
`trusted_time_response_link_recorded`，不会完成 trusted-time key，也不会解锁依赖该 key 的其他
item。当前切片不执行网络请求，不写 live/shadow/issue-replay logical ledger，不物化 outcome，不生成
guard completion，也不关闭、创建或切换 epoch。

这些更改只涉及旧 epoch 恢复控制面，不修改数据划分、指标、阈值、训练结论或 ConvLSTM 主模型与
默认预测链。

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
- recovery profile、coordinator implementation、adapter implementation 与 transition contract
  provenance。

coordinator 的实现 SHA-256 会进入 global intent，intent 一旦存在，后续代码漂移即 fail closed。
首次发布前的实现审查根仍是版本化 Git checkout；本切片没有额外外部签名或硬件 trust anchor，
因此 `external_implementation_trust_anchor_implemented=false` 与
`anti_rollback_authority_implemented=false`。运行时 provenance 不能被解释为这两项能力。

历史 manifest 重放只验证 immutable bytes、内部 digest 和引用关系，不要求所有 mutable
predecessor 永久保持 reservation 时的 bytes。某一步的 current predecessor CAS 必须在该 step
执行前核对；合法 step 完成后，下一次 poll 从已经深度验证的 step receipt chain 推导期望态，不能
反复盲比最初 manifest bytes。

恢复器不获取 canonical deploy/runner lock，也不在四锁内调用会再次获取相同锁的 public poll。
adapter 必须使用明确的“caller already owns locks”窄接口或无锁内部原语。任一锁 busy、cut 不再
成立、event/manifest 分支或引用漂移时均 fail closed，且不得写 recovery authority。

## 3. Versioned transition plan 与 step receipt chain

恢复 namespace 使用 create-only、canonical JSON 和 durable publish。global intent 必须先于任何
predecessor mutation 发布，并固定 reservation identity、有序 keyset、依赖图摘要、transition
contract hash、每个 key 的 transition plan、initial-step adapter coverage 和单 step poll 策略；
该 coverage 不表示整条 key transition chain 已有终态 adapter。

每个 item transition plan 至少绑定：

- `initial_action`、有向 `edges`、允许的 `terminal_actions`；
- `closure_resolved` 与可审计的 `unresolved_reason`；
- family mutation lanes、read-set namespace digest 与 write-set policy；
- canonical plan SHA-256。

一个 key 的每一步使用独立、内容绑定的 step id。其 crash-forward 顺序为：

```text
replay global intent and verified prior step-receipt chains
  → require terminal receipts for all manifest dependencies
  → publish/adopt step intent
  → exact predecessor or expected-pre-head CAS
  → probe pending/already-applied/conflict
  → apply at most one supported deterministic action
  → verify exact successor evidence
  → publish/adopt immutable step receipt
  → append one step-indexed recovery event
```

step intent 固定 key、step index、action、transition-plan hash、previous-step-receipt reference、
dependency terminal receipt refs 与 adapter provenance；read/write policy 通过 transition-plan hash
间接绑定。step receipt 再绑定 intent hash、action output kind/reference/semantics、`next_actions` 与
`terminal_for_key`。是否新写入或采用 already-present exact bytes 由 action-specific create-only
adapter 的可重复后置条件决定，不依赖 status cache 自报。

receipt chain 必须从 step 0 连续增长；每个 action 必须是 plan 初始 action 或上一 receipt 的唯一合法
后继，且 previous receipt reference、plan hash 与 step index 都必须吻合。terminal receipt 后不得追加
新 step。若 plan 尚有未解析的 derived future work，即使当前 action 的局部 mutation 成功，
`terminal_for_key` 仍必须为 false。

关键依赖规则是：只有 `terminal_for_key=true` 的最新 step receipt 才表示 key 完成，才可满足其他
item 的 dependency。非 terminal receipt 只证明某一步已持久完成。例如 DER repair receipt 会把该
key 留在下一步 `trusted_time_response_link_recorded`；当前没有对应网络 adapter 时，机器应稳定报告
waiting/unsupported，而不能提前推进 dependents。

receipt 缺失不能解释为 mutation 未发生。若进程在 mutation 后、receipt 前崩溃，下一次 poll 必须
通过 action-specific create-only adapter 识别/adopt exact successor；完全一致时只补同一 step
receipt，部分状态或异义状态则阻断。已有历史 receipt 只运行 action-specific immutable
postcondition verifier，不重跑已经失效的 predecessor 条件或 mutation adapter。若进程在 receipt
后、event 前崩溃，则只补 event，不重复 action。recovery event 是 receipt 的 append-only 索引，
不替代 receipt；`status.json` 仅是可修复 cache，
`cache_authority=false`。

## 4. 六族 transition contract

当前版本化 contract 明确列出已知 transition graph，用于阻止首步 receipt 的错误闭包解释；它不是
对所有未来 derived key 的预留或实现承诺。

| family | 已知主链 | terminal 条件/边界 |
| --- | --- | --- |
| `issue_route_replay` | receipt verified → route consumed | 只有已解析闭包中的 route consumed 可 terminal；无 seal 时会派生未预留 live outstanding，保持非 terminal |
| `live_outstanding` | anchor request → anchor result → request retry 或 outcome settled | repaired anchor receipt 或 resolved outcome settled 可 terminal；分支需受审 evidence adapter |
| `outcome_revision` | source ingest → derived items；materialize → consume | source ingest 可能生成多个 content-dependent obligations，只有已解析 consume 可 terminal |
| `guard` | guard completion 或 superseded disposition | 两者均可作为各自受审路径的 terminal；当前只支持后者 |
| `trusted_time` | DER repair → response link → receipt verified | 只有 cryptographically verified receipt 可 terminal |
| `shadow` | close → genesis → classify；derived outstanding → settle；cursor | genesis/rotation/unclassified issue 可派生未预留 work，必须保持非 terminal；仅已解析 classify/settle/cursor 可 terminal |

任何 branch selection 都需要独立审查的 evidence adapter；任何 unresolved derived work 都不得被 terminal
receipt、完成状态或 dependency gate 吞掉。`terminal_transition_closure_implemented=false`、
`derived_future_work_reservation_implemented=false` 和 `all_transition_branches_supported=false` 是当前
设计边界，不是待人工确认的开关。

## 5. 三类 deterministic local adapter

### 5.1 Live anchor receipt repair

该 adapter 只接受 action 为 `anchor_receipt_repaired` 的 step。它重放 frozen live logical chain，
要求唯一 `issue_batch_sealed` 与唯一 `anchor_confirmed` 对应同一 seal，并使用 live core 的 stored-
anchor payload builder 生成预期 canonical bytes。写入目标只能是 manifest 绑定的缺失 receipt 路径。

若 receipt 已存在且 bytes 完全相同，则采用 `adopted_already_applied`；同路径异 bytes、orphan
receipt、seal/confirmation 不唯一或 current source prerequisite CAS 不匹配时阻断。该修复只恢复
本地 anchor receipt 文件，不把时间锚提升为 trusted anchor，也不改变 live ledger。

### 5.2 Trusted-time request DER repair

该 adapter 只接受 request record 已存在、DER 缺失且没有 response link/receipt downstream chain
的 step。它验证 request record 的 profile、target、envelope、nonce、policy、imprint 与 manifest
authority，调用同一 DER builder 重建 exact bytes，并以 request 内预存的 path、SHA-256 和 size
做 create-only 发布。

DER-without-request、缺 DER 但已有 downstream response chain、DER 同路径异 bytes、nonce 或
envelope 漂移均为 integrity failure。该 adapter 不访问 TSA 网络，不创建新 request，不替换 nonce，
也不宣称 response 或 receipt 已验证。其 step receipt 必须为非 terminal。

### 5.3 Guard superseded disposition

该 adapter 只接受 manifest 初始 action 为 `superseded_by_backfill` 的 key。执行时必须再次重放
guard intent、pre-head 与 frozen live ledger evidence，确认不存在 open/seal 边界，并确认唯一既有
backfill/settlement 持久事件仍精确支持 supersede。机器时间越过目标日起点本身不构成 backfill
evidence。

当前 guard core 没有与 `superseded_by_backfill` 等价的 durable guard completion/event schema。
因此本切片只能在 recovery namespace 发布绑定既有持久证据的 disposition step receipt 和 recovery
event；不得伪造 `guard_completion_recorded`，不得向 guard completion namespace 写入自创格式，
也不得把该 receipt 解释成 live ledger terminal event。

## 6. 明确不支持的 mutation 与 capability

以下 action 即使出现在 transition plan 中，也只能由机器报告 waiting/unsupported/blocked，不得
跳过、重分类或要求人工操作：

- TSA HTTP 请求、response object/link 获取及 cryptographic receipt 验证；
- live、shadow 或 issue-replay ledger append；
- anchor request/result、outcome settle/materialize/consume、source snapshot ingest；
- issue replay receipt 生成或 route consume；
- guard completion 写入；
- shadow settlement、classification、rotation 或 cursor 推进；
- derived future work 的自动 reservation，以及任意未在冻结 manifest 中出现的新 key。

网络 action 具有释放锁、外部副作用和响应非确定性，不能套用本地 create-only adapter。ledger
mutation 必须先具备 transaction 内 expected-pre-head CAS、序列号/previous-hash 校验、SQLite
durability 与 mutation-before-receipt adoption，不能由 generic 文件写 adapter 代替。

当前可以声明 `transition_plan_binding_implemented`、`step_receipt_chain_implemented` 和
`terminal_receipt_dependency_gate_implemented`；但至少以下能力继续为 false：

```text
bounded_workset_recovery_implemented
all_reserved_items_settled
all_reserved_successors_supported
terminal_transition_closure_implemented
derived_future_work_reservation_implemented
all_transition_branches_supported
network_recovery_implemented
ledger_mutation_recovery_implemented
old_work_admission_fence_implemented
direct_filesystem_writer_fence_implemented
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

waiting、unsupported、busy 或 blocked status 不得把实现能力误报为当前 key 已恢复。只有最新 step
receipt 经深度复验且 `terminal_for_key=true` 时，该 key 才算完成；只有冻结 keyset 中每个 key 都
有合法 terminal receipt，未来 terminal recovery event 才可能宣称完整恢复。

## 7. 故障恢复与验证边界

定向快测应覆盖：immutable event/manifest replay；transition plan hash 绑定；step index、previous
receipt 与合法 edge；terminal-only dependency gate；DER repair 非 terminal；unresolved derived work
非 terminal；predecessor/pre-head CAS 漂移；global/step intent create-only adoption；三个 adapter 的
pending、already-applied 和 conflict；intent 后崩溃、mutation 后 receipt 前崩溃、receipt 后 event
前崩溃；orphan/branch/unknown entry；waiting/busy/unsupported/blocked false claims；四锁逆序释放；
TSA 网络函数与 ledger writer 在本切片不可达。

本阶段不需要穷举所有容量边界、所有 symlink/fsync 排列，也不运行模型训练、NGBoost、SHAP、
ConvLSTM、真实 TSA 网络、真实 runtime ledger mutation、全仓长测或穷举 filesystem/crash 矩阵。
最终测试计数和 profile/implementation/test SHA-256 应在实现稳定并完成定向验证后写入交接文档；
本说明不固化开发中间态 hash 或已过期计数。

## 8. 下一阶段

下一步先为 live/shadow append primitive 增加 transaction 内 expected-pre-head CAS，使 stale reader
不能在 head 漂移后提交 ledger event，并验证 mutation 后 receipt 前的机器自动 adoption。完成该
基础原语后，首个窄 ledger adapter 应选择 `anchor_request_recorded`：只追加一个确定性 event，不
访问网络；它必须绑定 action-specific read/write set、sequence/previous-hash、step intent 与
step receipt，且不得调用会重入现有四锁的 public poll。

TSA 网络 action 应另设外部请求 intent、释放锁前后的 fence-generation CAS、幂等 response object
adoption 和加密验证。只有所有冻结 key 都由各自受审 transition chain 收口，并由独立 assessor
深度重放 terminal recovery event，才可进入 post-recovery drain eligibility；这仍不能自动推出
`SEALED(old)+ACTIVE(new)`、epoch switch 或 formal warning authority。
