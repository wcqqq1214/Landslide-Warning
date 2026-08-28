# 藕塘 outcome settlement 终态采用工程说明

## 1. 范围

本增量为 `live_outstanding -> outcome_batch_settled` 增加机器化终态采用，但不增加新的
outcome writer。它只在 live ledger 已经包含完整、连续且可由 frozen live replay 验证的
settlement transaction 时创建 recovery receipt/event：

```text
confirmed issue authority + terminal manifest-reserved outcome dependency
  -> optional fully replayed intervening revisions
  -> existing contiguous 43-event outcome transaction
  -> full prefix replay
  -> create-only terminal recovery receipt
  -> previous-hash recovery event
```

如果 transaction 尚不存在，机器返回 `waiting_for_durable_outcome_settlement`。该等待发生在
item intent 创建之前，不写 live ledger、recovery receipt 或 recovery event，也不转入人工
freeze、cleanup、approval、force 或 backdate。

本增量不读取 `outcome_inbox`，不从当前 source 猜测 actual，不调用 outcome materializer，也不
运行训练。ConvLSTM、v4、冻结 splits、metrics、thresholds、模型参数和科研结论均未修改。

## 2. 两种 confirmation authority

adapter 只接受两种已版本化的 confirmation 前驱：

1. manifest 冻结时已 confirmed：`authority.anchor_confirmed_event` 必须是 frozen live upper tip，
   且逐字段匹配同一 target、issue 和 seal；
2. recovery 后 confirmed：前一张 `anchor_result_recorded` receipt 必须唯一选择
   `outcome_batch_settled`，其 semantics 必须绑定真实 `anchor_confirmed` ledger row、同一 old epoch
   和 seal，并保持 trusted/E2 为 false。

第二种路径不能使用旧 frozen projection 推断 confirmation；它按 receipt 固定的 sequence/hash 在
current ledger 中定位 event。confirmation 只证明 anchor 分支，不被错误等同为 settlement 的直接
pre-head。manifest 冻结后，canonical poll 可以先消费较早日期的合法 revision；transaction 的
exact pre-head 因此是 43-event batch 的真实前一条 ledger event。

## 3. 完整 transaction 证明

`outcome_batch_settled` 不是单条 marker。frozen live writer 的原始 transaction 固定为 43 条：

- 1 条 `outcome_batch_opened`；
- 8 条按 station order 排列的 `outcome_revealed`；
- 每站依次 4 条 `score_recorded -> expert_state_updated -> conformal_state_updated ->
  drift_state_updated`，共 32 条；
- 1 条 `site_score_recorded`；
- 1 条 `outcome_batch_settled`。

adapter 要求这 43 条自身连续，类型与顺序完全一致，但允许 confirmation 与 batch 之间存在可由
frozen live core 完整重放的历史 revision。它重放 batch 的真实 pre-head，确认仍为同一
outstanding target/issue 且 anchor 覆盖同一 seal；再重放至 settlement，复用 frozen
`live._reconstruct_projection` 验证八站 reveal、score、expert/conformal/drift state、site score、
actual、state hash、时间顺序和 terminal lifecycle。partial suffix、foreign event、错误 target/seal、
错误位置或无法重放的 transaction 均 fail closed。

action contract 和 receipt semantics 绑定 confirmation、first/terminal event、43 个 ordered entry
hash 的聚合 digest、outcome/source/revision/input-manifest identity 以及 settlement state-before/
state-after。它显式保持：

```text
network_action_performed = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
formal_warning_output = false
```

`score_recorded.formal_warning_output` 和 `site_score_recorded.warning_color_output` 也必须为 false。
receipt 证明的是 ledger 中已存在的工程 transaction 和该 workset key 的终态，不把未验证 anchor
或 outcome 提升为科学 live evidence。

## 4. Read-set 与 mutation 边界

`live_outstanding` item 的 manifest artifacts 不包含未来 outcome bytes，因此本 adapter 禁止读取
可替换的 date-named outcome inbox，也禁止跨 item 偷用 current source。live item 必须显式依赖
同一 manifest 中 target/epoch/revision 匹配的 `outcome_revision` sibling；inventory 现会为 admission
时已经处于 settlement successor 的同日 receipt-chain 写入这条唯一依赖，既有 dependency gate 必须
先深验该 sibling 的 terminal `outcome_or_revision_consumed` receipt，live item 才会成为 ready key。
缺少这个预留依赖（包括 manifest 后才 confirmed 的动态分支）时只保持 machine waiting，不事后
改写 DAG。adapter 随后只读取 frozen live prefix、前一张 recovery receipt（若有）和完整
append-only ledger suffix；live ledger 本轮零 mutation。

这一区分避免了两个错误实现：

- 只看到末尾 `outcome_batch_settled` 就发布 terminal receipt；
- 从 mutable inbox 加载一个后来出现的 outcome 并直接更新在线 state。

前者会伪造 43-event lifecycle closure，后者会越过 frozen read set。当前实现两者都不做。

## 5. 崩溃与重放

- manifest-reserved outcome dependency 不存在或尚未 terminal：machine waiting，且无 settlement
  step intent；
- dependency terminal 但 transaction 不存在：machine waiting，且无 settlement step intent；
- 完整 transaction 已存在：创建 settlement adoption intent，再发布 terminal receipt/event；
- intent 后、receipt 前崩溃：下一 poll 重算同一 43-event digest 并补 receipt；
- receipt 后、recovery event 前崩溃：只 forward-adopt 缺失 event；
- confirmation 与 settlement 之间存在合法 revision：以 batch 的真实前驱为 pre-head，仍可采用；
- settlement 后存在合法 suffix：仍按固定 43-event slice 重放；
- partial/foreign suffix：完整 replay 或 exact slice 检查失败，零 terminal receipt。

## 6. 下一步

published outstanding receipt tip 的 fresh outcome consumption 已由下一增量实现：它只消费 manifest
冻结的 immutable materializer receipt、exact outcome object 与 source manifest，复用 frozen
canonical writer 生成完整 EventSpecs，并通过 expected-pre-head CAS append/adopt。本 settlement
adapter 因而可在 admission 时已 confirmed、已预留 dependency 的分支中采用同一 43-event
transaction。详细边界见 `docs/ootang_outstanding_outcome_consumption_engineering.md`。

下一窄增量改为 `outcome_materialized`。manifest 后才 confirmed 的 live item 仍需要未来版本的
derived-work/step-level dependency reservation；settled revision、backfill revision 与首次 backfill
writer 也仍未实现。继续保持 `derived_future_work_reservation_implemented=false`、
`terminal_transition_closure_implemented=false`、`bounded_workset_recovery_implemented=false`、
`old_epoch_drained=false` 和所有 trusted/E2/formal claims 为 false。

## 7. 验证范围

Focused recovery suite `41/41`（约 0.76 s）覆盖 frozen-confirmed、result-receipt-confirmed、缺少
transaction 的 zero-mutation wait、内部缺 event 的 partial slice fail-closed、完整 transaction
receipt-only adoption，以及真实八站链中 confirmation 后先追加 8 条 earlier-date canonical
`outcome_revision` 再 settlement 的路径。

既定相邻 recovery、live ledger/CAS、inventory、manifest、admission cut、drain eligibility、drain
v2 和 main suite `148/148`（约 8.13 s）通过；Ruff、format、compile、strict profile load 和 diff
check 通过。未运行训练、真实 endpoint、长并发/容量或无关科研边界矩阵。

初次独立只读复审发现两个 P1：缺少 manifest-reserved outcome authority，以及错误地强制
confirmation 直接作为 batch pre-head。实现分别增加 matching outcome sibling + terminal
dependency gate，并改为从 matching settlement 反向定位完整 43-event slice 的真实前驱；最终复审
P0/P1=0。
