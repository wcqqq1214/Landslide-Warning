# 藕塘 published outstanding outcome 自动消费工程说明

## 1. 本增量做什么

本增量实现 manifest-bound
`outcome_revision -> outcome_or_revision_consumed` 的第一个写入分支：只消费 admission 时已经由
outcome materializer 完整发布、但尚未进入 live ledger 的 `selection_kind=outstanding` receipt tip。
机器路径为：

```text
frozen receipt-chain item
  -> immutable tip receipt + exact outcome object + input/source manifest replay
  -> confirmed outstanding live prefix replay
  -> frozen canonical `_append_outcome_batch` EventSpec capture
  -> expected-pre-head CAS append or exact crash-forward adoption
  -> terminal outcome recovery receipt/event
  -> manifest dependency gate
  -> existing live settlement adoption adapter
```

本实现不从 date-named inbox 或 current source 重新选择 actual，不复制或改写评分数学，也不运行
训练。它直接调用冻结的 live writer 生成完整 EventSpecs；ConvLSTM、v4、数据 split、指标、阈值、
模型参数和实验结论均未修改。

## 2. 输入权威与 mutable 边界

首次 action 只接受精确的 `outcome_receipt_chain` authority：tip 已 published、未 ledger-consumed、
item 未 terminal，且 successor/action 均为 `outcome_or_revision_consumed`。manifest 中的每条 receipt、
exact outcome、input/source manifest、active receipt pointer 和 active outcome 都先做 path、size、
SHA-256 CAS。

随后复用 materializer 的完整 receipt-chain replay，要求当前唯一 tip、pointer 和 active bytes 仍与
冻结 tip 一致；tip receipt、exact object、input/source manifest、revision sequence 和 frozen
`ledger_consumed` 标志必须逐项等于 manifest authority。exact outcome 再经 frozen live loader 验证
old epoch 的 outcome source、target、revision、单调时间戳和八站顺序/数值。input manifest 的
`selection_kind` 必须为 `outstanding`；`revision` 和 `backfill` writer 仍未实现。

active pointer/inbox 只属于首次 mutation 的 predecessor CAS。transaction 已完整提交后，无论 recovery
receipt 是否已经写出，重放都改用 manifest-bound immutable tip receipt、exact object 和 source
manifest；合法的后续 materializer revision 可以移动 active pointer/inbox，不会使旧 transaction 或
terminal receipt 失效。

## 3. canonical writer 与 CAS

adapter 以精确 pre-head 重建 live projection，并要求同一 target/issue 仍 outstanding、seal 已有
confirmed anchor authority，且该 revision 尚未进入 projection。内存 collector 只捕获一次
`live._append_outcome_batch` 的 transaction，不写 SQLite。固定形状为：

- 1 条 `outcome_batch_opened`；
- 8 条 `outcome_revealed`；
- 每站 4 条 `score_recorded -> expert_state_updated -> conformal_state_updated ->
  drift_state_updated`，共 32 条；
- 1 条 `site_score_recorded`；
- 1 条 `outcome_batch_settled`。

共 43 条。intent contract 绑定 exact pre-head、epoch/target/issue/seal、tip receipt、exact outcome、
source manifest、source/revision identity、首末 event key、ordered-key digest 和全部 EventSpec 的 canonical
digest。fresh append 只在 current terminal head 等于 intent pre-head 时发生。已有 event key 只能作为
pre-head 后完全连续、逐字段 canonical 相等的 43-event retry 被接管；partial、displaced、foreign 或
内容冲突全部 fail closed。

JSON replay 会把 tuple 归一为 list，因此 EventSpec 与 stored event 的比较使用 canonical JSON bytes，
而不是 Python 容器类型相等；这不放宽任何持久字段。

## 4. 三态 crash-forward 处理

pending intent 按 ledger slice 明确分成三态：

1. 43 条完全不存在：重新验证 current publication 与全部 predecessor artifacts，再执行 CAS；
2. pre-head 后 43 条完整存在：只用 immutable tip 与 exact slice 重建原 contract/output，零写接管并补
   recovery receipt；即使期间 materializer 已发布下一 revision，也不再读取 mutable pointer/inbox；
3. first/terminal key 部分存在、位置变化或内容不符：integrity failure，零追加、零 receipt。

completed receipt 的历史验证永远走第二类纯读 postcondition verifier，不调用 CAS。若 ledger 被回退到
intent pre-head，验证只报告 slice 消失，绝不重新生成 43 条事件。receipt 之后的新 revision suffix
可以存在；验证仍按原 pre-head 的固定 slice 重放。

ActionOutput 不记录瞬时 `created/adopted` 分支，因此 fresh 与 crash adoption 产生同一不可变 receipt。
semantics 绑定 ordered entry digest、first/terminal event、state hashes，并继续保持：

```text
network_action_performed = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
formal_warning_output = false
```

## 5. settlement dependency wiring 与明确未覆盖范围

inventory 现在会把非终态 receipt-chain 的精确 natural key 接到同日、且 admission 时已经处于
`outcome_batch_settled` successor 的 `live_outstanding` item。receipt chain 优先成为唯一 settlement
outcome dependency；同日更晚 source candidate 保留为结算后的 revision，避免一个 live settlement
出现两个 outcome authorities。outcome terminal receipt 深验后，既有 settlement adapter 才能采用同一
43-event transaction 并终态化该 live key。

这一闭环只适用于 manifest 冻结时已经 confirmed、因而已经预留 outcome dependency 的 live item。
若 anchor confirmation 是 manifest 之后由 recovery 才产生，冻结 live item 没有该 dependency；当前
仍保持 machine waiting，不动态修改 immutable DAG。解决它需要未来版本的 step-level/derived-work
reservation，不属于本增量。因此以下声明继续为 false：

```text
bounded_workset_recovery_implemented
all_reserved_successors_supported
derived_future_work_reservation_implemented
terminal_transition_closure_implemented
old_epoch_drained
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
```

`outcome_materialized`、settled-date revision writer、backfill revision writer 和首次 backfill writer 也
仍是独立未实现分支。本实现不会因这些分支不可用而转入人工 freeze、cleanup、approval、force 或
backdate。

## 6. 验证范围

定向测试使用真实 `_LiveFixture`、八站 ledger 和实际 outcome materializer publication，而不是手工
伪造 active outcome。覆盖 fresh 43-event append、CAS 后/receipt 前崩溃接管、intent 后合法 foreign
suffix fail-closed、历史 slice 回退零重写、合法 revision 移动 pointer/inbox 后旧 receipt 继续纯读
有效，以及 post-CAS/pre-receipt 窗口内 pointer/inbox 前进后的 immutable adoption。

最终测试计数、运行时间、独立复审结论和源码/配置指纹在提交前写入 `CODEX_HANDOFF.md` 与
`docs/progress.md`。未运行训练、真实网络、长并发/容量矩阵或无关科研边界测试。

## 7. 下一步

manifest-bound `outcome_materialized` 及其到 outstanding 43-event consumption 的下一轮
step-receipt 桥已实现，详见 `docs/ootang_outcome_materialization_recovery_engineering.md`。下一
窄增量改为 settled-date revision consumption writer，随后分别处理 backfill revision 和
首次 backfill writer。更高层的完整闭环问题仍是版本化 derived-work/step-level
dependency，令 manifest 后才 confirmed 的 live item 也能自动关联随后终态化的
outcome，而不改写原 DAG。
