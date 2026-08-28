# 藕塘 outcome materialization 与自动消费桥工程说明

## 1. 本增量实现的机器链路

本增量实现 manifest-bound `outcome_revision -> outcome_materialized`，并把新的
materialization step receipt 作为下一次 poll 的唯一前驱，接入已有
`outcome_or_revision_consumed` outstanding writer：

```text
frozen inventory authority
  -> exact source/receipt-chain replay
  -> materializer input + exact outcome + immutable receipt publication/repair
  -> nonterminal recovery receipt (0 live-ledger events)
  -> next poll replays only that immutable receipt/exact object/input manifest
  -> canonical outstanding 43-event CAS append or exact adoption
  -> terminal outcome-consumption receipt
```

该链路不需要人工 freeze、cleanup、approval、force 或 backdate，不运行训练或真实
网络请求。ConvLSTM、v4 主方法、冻结 split、metrics、thresholds、模型参数和实验结论
均未修改。

## 2. 两类物化权威

coordinator 只接受两类精确 authority：

1. `outcome_receipt_chain` 且 `tip_published=false`：receipt、exact outcome 和 input
   manifest 已持久化，但 pointer/inbox 发布未完成。系统深验冻结 receipt history，只向
   前补全发布。
2. `machine_selected_source_outcome`：current source snapshot 在 manifest 时已选中，但尚无
   materializer receipt。系统重放冻结 source snapshot/record/seal/selection，并生成精确
   input manifest、outcome object 和 receipt。

两类都绑定 target、old epoch、source/revision、snapshot sequence/receipt、live seal、
input/exact/receipt SHA-256 及 item-artifact digest。机器选择的 revision 额外绑定
`previous_revision_id` 与 `previous_outcome_sha256`；首个 outstanding/backfill 则两字段必须同为
`null`。

materializer 的 deploy/runner locks 在 recovery 路径中是 deny-write sentinels，不能被伪装成
公开 runner 权限。coordinator 只复用 materializer 已有的 caller-owned
`_publish_candidate`/`_reconcile_chain` 原语，并依赖既定 manager→cycle→replay→shadow
锁序列保护调用者边界。

## 3. 零 live-write 物化合同

materialization 在写任何 input artifact 前重做 source finalization 验证，然后由同一 canonical
payload 计算全部 content address。动作结束后必须证明：

- immutable receipt 与 exact outcome 存在且与合同完全相等；
- input manifest 与 source snapshot identity 未变；
- 若该 receipt 仍是 current tip，pointer/inbox 必须已完整发布；
- `live_ledger_event_count=0` 且 `live_ledger_mutation_performed=false`；
- `network_action_performed=false`。

recovery receipt 非终态，且唯一 `next_actions` 为 `outcome_or_revision_consumed`。后者不再从
mutable current source/pointer 重选 actual，而是从前一张 recovery receipt 精确取回
materializer receipt、exact outcome 和 input manifest，深验后才进入已有 canonical 43-event
outstanding writer。

## 4. commit-before-recovery-receipt 崩溃向前恢复

持久化 materializer receipt 后、recovery receipt 前崩溃时，动作按不可变 receipt chain
分类：

1. 预期 receipt 仍为 current tip：重放完整 chain，再调用 `_reconcile_chain` 补齐
   pointer/inbox；
2. 预期 receipt 是合法 historical predecessor：只采用 immutable receipt/exact/input，不
   要求 current pointer 回退，不覆盖已发布的新 revision；
3. 预期 receipt 不存在：只有 `machine_selected_source` 允许执行 fresh publication；
   registered-tip 分支的 receipt 消失则 fail closed。

因此，rev1 在 commit 后来不及写 recovery receipt，期间 materializer 又正常发布 rev2
时，系统会以 rev1 的 immutable history 补齐原 step，不破坏 rev2 current state。

## 5. freeze 前已消费的诚实采用

receipt tip 可能在 manifest freeze 时已经存在于 live ledger，但 publication repair 仍未完成。
该分支不能谎报 fresh CAS。新合同显式记录 `ledger_consumed_at_freeze=true`，使用
`writer_branch=preexisting_consumed_adoption`，仅重放并采用冻结 prefix 中的精确 43-event
transaction，不执行 ledger CAS。

## 6. pending revision 前驱与顺序

inventory 现在为每个日期记录未消费 receipt tip。若 current source 已是同日更新
revision，候选不再被错误冻结为新 `outstanding`/`backfill`，而是统一变为：

```text
selection_kind = revision
previous_revision_id = pending tip revision
previous_outcome_sha256 = pending tip exact outcome
dependency = pending tip natural key
```

这个规则同样覆盖 ledger-known rev1 + pending rev2 + current rev3。依赖先终态化，后续
source selector 才会以相同 predecessor authority 生成 rev2/rev3 materialization intent。真实回归
已覆盖“未消费 rev1 → 43-event 消费 → 同日 rev2 自动物化为 receipt sequence 2”。

## 7. 明确边界与下一步

本轮只完整闭合“新物化的 `selection_kind=outstanding` → 43-event 消费”。
revision、backfill 和 first-backfill 可以按冻结 authority 物化，但它们的 live-ledger
consumption writer 仍未实现。所以上节的 rev2 已安全物化，但不会被错当 outstanding
追加到 ledger。

下一窄增量是 settled-date revision consumption writer，必须复用 immutable predecessor
chain 与 expected-pre-head CAS/crash-forward 框架；之后再分别处理 backfill 与
first-backfill。跨 manifest-freeze 的 derived-work/step-level dependency reservation 仍属更高层
closure 工作。以下声明继续为 false：

```text
bounded_workset_recovery_implemented
all_reserved_successors_supported
derived_future_work_reservation_implemented
terminal_transition_closure_implemented
old_epoch_drained
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
```

有界验证为 inventory+recovery focused `58/58`（3.71 s），含 materializer 及相邻
recovery/live-ledger/CAS/inventory/manifest/admission/eligibility/drain/main 回归 `192/192`（11.06 s）。
独立最终只读复审为 P0/P1=0。未运行训练、真实网络、长并发/容量矩阵或无关边界测试。
