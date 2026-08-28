# Ootang cross-freeze step dependency reservation v1

## 1. 本增量解决的精确缺口

冻结 workset 时，未确认的 `live_outstanding` key 只会预留
`anchor_result_recorded`，不会静态依赖同日 outcome key。若 recovery 在 freeze 后写入
`anchor_confirmed`，该 key 的下一步会变成 `outcome_batch_settled`，但 immutable manifest DAG
仍没有 outcome dependency。即使 frozen manifest 中对应的 outcome sibling 已经物化、消费并取得
terminal receipt，v6 dispatcher 也必须稳定等待，不能临时改写 manifest。

本增量新增独立 sidecar：

- `code/monitoring/ootang_epoch_step_dependency_reservation.py`
- `config/ootang_epoch_step_dependency_reservation.v1.json`
- runtime namespace：`workset_recovery_v1/step_dependencies_v1`

它只为以下单一规则发布 versioned authority：

```text
post-freeze anchor_result_recorded(candidate_confirmed)
  -> outcome_batch_settled
  depends on
unique terminal outcome_or_revision_consumed manifest sibling
```

它不新增 natural key，不运行 settlement，不把任何 nonterminal recovery receipt 改成 terminal，
也不声明完整 derived-future-work 或 lifecycle closure。

## 2. 为什么使用独立 sidecar

现有 recovery v6 global intent、item intent 与 receipt 会绑定 recovery profile SHA-256、transition
plan 以及 `ootang_epoch_workset_recovery.py` provenance。直接修改原模块/profile 并在相同 namespace
解释既有字节，会使已持久化 v6 authority 无法按原合同重放。

因此本轮 profile 固定以下既有上游：

```text
recovery profile:        5c50d168d389c286d0940a00884369ae8f65fc399f8726f0f64300999dd2de01
recovery implementation: b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51
```

sidecar 在相同 `manager -> cycle -> replay -> shadow` 四锁下只读深验完整 v6
manifest/global-intent/item-intent/receipt/event/anchor-observation authority；存在 pending intent、receipt
缺 event 或基础 authority 不完整时只等待。原 v6 manifest、global intent、item intent、receipt 与
event 均不改字节。

## 3. 唯一候选与依赖边界

source 必须是 frozen manifest 中的 `live_outstanding` key，最新深验 receipt 必须满足：

- `action=anchor_result_recorded`；
- `result_outcome=candidate_confirmed`；
- `next_actions=[outcome_batch_settled]` 且 `terminal_for_key=false`；
- receipt semantics 与 frozen old epoch、target、issue、seal 完全一致；
- confirmation event 的 sequence、event key、entry/predecessor hash 已被 v6 receipt/event 链绑定。

dependency 必须是同一 frozen manifest 中不同的 `outcome_revision` key，最新深验 receipt 必须满足：

- `action=outcome_or_revision_consumed` 且 `terminal_for_key=true`；
- output schema 为 canonical outcome-consumption output；
- writer branch 仅允许 `outstanding_settlement` 或
  `preexisting_consumed_adoption`；
- old epoch、target、issue、seal 与 source 精确相同；
- source revision、exact outcome SHA-256、outcome source id 与 terminal ledger event 完整存在。

零个 terminal sibling 时 sidecar 等待；多于一个精确匹配 sibling 时 fail closed。若 frozen manifest
已经静态预留 outcome dependency，则 sidecar不重复发布动态 edge。

## 4. 持久化合同与崩溃恢复

每个 dependency slot 由根 manifest SHA-256、source key、source receipt SHA-256 和目标 settlement
step id 确定。reservation object 绑定：

- recovery global intent、root manifest、manifest reservation event；
- source/dependency key、natural key、namespace digest；
- source previous receipt/event、目标 step index/id/transition plan；
- dependency terminal receipt/event 与 outcome/ledger identity；
- sidecar implementation provenance；
- narrow capability 与所有 broad false claims。

对象按 canonical JSON SHA-256 发布到 `reservations/sha256/<sha>.json`。随后 append-only event
记录 sequence、previous entry、slot、source/dependency key 及 object reference。每 poll 最多发布或
forward-adopt 一个 slot：

```text
deep replay v6 -> replay sidecar objects/events
  -> one orphan object: append its missing event only
  -> otherwise select one unique ready pair
  -> publish create-only object -> append event -> cache status
```

若进程在 object 后、event 前崩溃，下一 poll 不重新选择、不改 recovery ledger，只补 exact event。
同 slot 出现多个对象、多个 orphan、event 无 object、event chain 分叉或任一已引用字节漂移都会阻断。

## 5. 能力声明

本 sidecar 只声明：

```text
cross_freeze_manifest_sibling_step_dependency_reservation_implemented=true
```

至少以下声明继续为 false：

```text
bounded_workset_recovery_implemented
all_reserved_items_settled
all_reserved_successors_supported
terminal_transition_closure_implemented
derived_future_work_reservation_implemented
all_transition_branches_supported
lifecycle_authority
transition_authority
old_epoch_drained
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
trusted_anchor_receipt_verified
e2_live_evidence_eligible
real_activation_ready
formal_warning_output
```

原因是 sidecar authority 尚未被显式 overlay dispatcher 消费；`source_snapshot_ingested` 后新增
outcome natural keys、issue-route derived live work 与 shadow derived work 也仍未实现。

## 6. 有界验证

新增 `3/3` 快测约 `0.024 s`，覆盖唯一 terminal sibling 的 content-addressed reservation/event、
object-before-event crash-forward adoption、nonterminal waiting、多 terminal sibling ambiguity
fail-closed、broad claims 保持 false，以及所有基础 v6 artifact 逐字不变。

sidecar + recovery + manifest 定向回归为 `67/67`（`5.913 s`）。Ruff check/format、Python
compile、strict JSON/profile load 与 `git diff --check` 均通过；相邻
sidecar/recovery/materializer/live-ledger/CAS/epoch-gates/prequential/main 回归为 `224/224`
（`15.025 s`）。两路独立只读复审均为
P0=0、P1=0；正常第二 poll 的 event-chain tamper 扩展被列为非阻塞后续覆盖。未运行训练、真实网络、长并发/容量
矩阵或无关科研边界测试，也未修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数与实验
结论。

当前文件指纹：

```text
sidecar implementation: c385b7c8783d86831561d5c1179b05efc78e3f5ef99a912b44382143625e5190
sidecar profile:        8b10a9642610b76911813d80ee8e31205c0e3c490aa05a13f0ba8287d457670e
sidecar tests:          250f82cdbf802d92193afd7d090f048de869f0af7658c5b1ae925a9249000796
protected main.py:      02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898
```

## 7. 下一窄增量

下一步实现显式 versioned overlay dispatcher：只消费本 sidecar 的深验 reservation，把 exact
dependency authority 绑定进新的 settlement step intent/contract，并让 terminal outcome receipt
通过该 edge 解锁 live settlement adoption。该 overlay 不能重解释既有 v6 字节。

完成该闭环后，再单独实现 `source_snapshot_ingested -> derived_outcome_items_required` 的新-key
reservation；在这些 end-to-end closure 证据完成前不运行 drained assessor 或 active transition。
