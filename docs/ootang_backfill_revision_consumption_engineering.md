# 藕塘 backfill outcome revision 自动消费工程说明

## 1. 本增量闭合的机器链路

本增量在 outstanding v2 与 settled revision v1 消费路径之上，新增 backfill
projection 的 revision live-ledger writer：

```text
terminal outcome-materialization receipt
  -> immutable materializer receipt + exact outcome + source input manifest
  -> predecessor revision/outcome pair == live-ledger latest revision authority
  -> current projection exclusive branch check: backfill xor settled
  -> original backfill authority + frozen live prefix replay
  -> canonical _append_backfill_revision emits 8 outcome_revision EventSpecs
  -> expected-pre-head CAS append or exact post-CAS adoption
  -> terminal outcome_or_revision_consumed receipt
```

整条路径由 recovery coordinator 机器执行，不增加人工 freeze、cleanup、approval、
force 或 backdate，也不调用真实网络。ConvLSTM、v4 主方法、冻结数据划分、metrics、
thresholds、模型参数和科学结论均未修改。

## 2. projection 排他判定与持久化合同

materialized revision 仍以 `selection_kind=revision` 进入消费，但 coordinator 不凭
selection 字段猜测其 live lifecycle。它先重放 current projection，并要求目标日期恰好
属于以下一个分支：

- `backfill_events` 中存在且 `settled_events` 中不存在：进入 backfill revision；
- `settled_events` 中存在且 `backfill_events` 中不存在：进入 settled revision；
- 两者同时存在或同时不存在：authority 已失去排他性，fail closed。

backfill 分支使用独立的
`ootang_live_backfill_revision_consumption_action_contract_v1`。合同绑定 exact
pre-head、epoch/target、原 `backfill_not_blind` entry、immutable predecessor
revision/outcome pair、materializer tip/exact outcome/source manifest、online-state digest、
last-finalized/outstanding issue 与 seal、三类统计 count，以及 8 个 EventSpec 的首尾 key、
ordered-key digest 和 full-spec digest。

旧 `ootang_live_outcome_consumption_action_contract_v2` outstanding 合同和
`ootang_live_settled_revision_consumption_action_contract_v1` settled 合同仍按各自既有
schema 分派和深验，不追溯改写已持久化 contract/receipt。

## 3. canonical 8-event transaction

writer 直接复用 live core 的 `_append_backfill_revision`，按 profile 固定的八站顺序
追加：

```text
outcome_revision x 8 = 8 events
```

backfill 原本就是 `backfill_not_blind`，所以该 revision 不产生 settled 分支的
`revision_rescore_recorded`。每个 event 都保持 station state hash 不变，明确记录
`original_classification=backfill_not_blind`、
`live_online_state_rewritten=false` 与
`retrospective_score_available=false`。

transaction 后置复验要求以下 authority 不变：

- 原 backfill entry 及完整 backfill/settled 原始分类映射；
- 全部 online states；
- `last_finalized_date`、`outstanding_target_date`、outstanding issue id 与 issue events；
- 当前 issue-batch seal 与 anchored seal hashes；
- blind-settled、engineering blind candidate 和 backfill counts。

唯一预期的 revision 变化是目标日期的 revision registry 增加新 id，并将该日期的
latest actual 更新为新 outcome。它不把 backfill revision 伪装成 blind observation，
也不补造 retrospective score。

## 4. 下一日 persistence baseline 的机器更新

live projection 对下一日 persistence forecast 使用当前 `latest_displacement_mm`。因此当且
仅当 revision 目标日期等于 `last_finalized_date` 且当前没有 outstanding target 时，
canonical replay 会把这组 baseline 自动更新为 revised actuals；否则保持 revision 前的
baseline。后置验证按该条件计算唯一预期值，不依赖人工修正。

这项变化只修正机器下一周期使用的最新实测基线，不改写 ConvLSTM、模型权重、训练集、
冻结验证划分、指标或阈值。

## 5. fresh CAS 与崩溃向前采用

fresh transaction 只允许在 persisted contract 绑定的 exact expected pre-head 上执行 CAS。
若 8-event CAS 已提交、终态 recovery receipt 尚未落盘，下一 poll 从持久化合同和 immutable
输入重建 canonical EventSpecs，仅在预期位置逐项匹配 exact contiguous slice 后补 receipt，
不再次调用 CAS。partial、displaced 或 bytes 不一致的 transaction 均 fail closed。

合同同时保留 `preexisting_backfill_revision_adoption` 分支，用于 freeze 时 ledger 已含完整
canonical transaction 的零新增采用；它不能被用于容忍不完整或移动过的 slice。

## 6. 明确边界与后续状态

本增量自身只闭合“已有 first backfill 的直接 revision”消费。后继增量现已用独立
`ootang_live_first_backfill_consumption_action_contract_v1` 实现 first-backfill writer：在目标
精确为 `last_finalized_date + 1 day`、不存在 outstanding issue/seal 且没有任何既有 target
authority 时，从 immutable materialized outcome authority 生成 canonical 单事件
`backfill_not_blind` transaction。它没有与本增量的 revision 合同混用。

到此 materialized outcome 的 outstanding、settled revision、backfill revision 与 first
backfill 四条 writer 均已闭合，但这仍不扩大 full workset、all-successor、cross-freeze derived
future work、step-level dependency reservation、terminal/transitive closure、epoch lifecycle、
trusted anchor、E2 或 formal-warning authority。

当前下一窄增量转向 cross-freeze derived work：先建立 versioned step-level dependency
reservation，再据此推进 closure。first-backfill 的 authority、CAS 与后置状态合同见
`docs/ootang_first_backfill_consumption_engineering.md`。

## 7. 当前有界验证记录

当前已完成两条直接路径：canonical 8-event fresh consumption，以及 CAS 已提交但 receipt 未
落盘后的 exact adoption。定向结果为 `2/2`（0.836 s），完整 recovery 为 `55/55`
（4.715 s），相邻 recovery/materializer/live-ledger/CAS/epoch-gates/main 回归为
`219/219`（12.984 s）。Ruff format/check、Python compile、strict profile load 与
`git diff --check` 均通过。

两路独立只读复审均为 P0=0、P1=0。安全复审另用临时运行时验证了
“rev2 已在 freeze 前消费、pointer/inbox 仍落后到 rev1”的自动 repair：随后以
`preexisting_backfill_revision_adoption` 零 CAS 采用，ledger bytes 不变；first-backfill
仍停在 machine wait。contract 复审确认 outstanding v2、settled revision v1 的旧字段、
recorded verifier、transaction classifier 与 output 路径保持兼容。

提交前 SHA-256 为：recovery module
`e4b949664a7cb8cbb07936fa047bc157d6a648a2108adf19de93280a843afceb`、profile
`9c9a1dc4404a51b5a5a13721729cdc3306395d2bd69564e13e2c39c5f125f116`、test
`ae558660ee058822d23573327f3139d7f673fbdf7234676d4d8aa24580466f7e`、受保护
`main.py` `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
