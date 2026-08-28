# 藕塘 settled-date outcome revision 自动消费工程说明

## 1. 本增量闭合的机器链路

本增量在已有 outcome materialization 与 outstanding 消费桥之上，新增
`selection_kind=revision` 的 settled-date live-ledger writer：

```text
terminal outcome-materialization receipt
  -> immutable materializer receipt + exact outcome + source input manifest
  -> predecessor revision/outcome pair == live-ledger latest revision authority
  -> frozen live prefix replay + original settlement authority
  -> canonical _append_revision emits 8 x 2 exact EventSpecs
  -> expected-pre-head CAS append or exact crash-forward adoption
  -> terminal outcome_or_revision_consumed receipt
```

整条路径由 recovery coordinator 在 poll 中机器执行，不增加人工 freeze、
cleanup、approval、force 或 backdate，不调用真实网络。ConvLSTM、v4 主方法、
冻结数据划分、metrics、thresholds、模型参数和科学结论均未修改。

## 2. revision authority 与持久化合同

revision 分支不复用 outstanding 合同的字段形状，而是使用独立的
`ootang_live_settled_revision_consumption_action_contract_v1`。已持久化的
`ootang_live_outcome_consumption_action_contract_v2` 仍由 outstanding 路径按原格式读取，
因此本增量不会追溯改写旧 contract/receipt。

settled revision 合同同时绑定：

- frozen expected pre-head 的 event count、sequence 和 entry SHA-256；
- target date、old live epoch、原 settlement entry、issue id 与 issue-batch seal；
- immutable materializer chain 提供的 `previous_revision_id`/
  `previous_outcome_sha256`，且该 pair 必须精确等于 live-ledger 该日期的
  latest registered revision；
- tip receipt、exact outcome、source input manifest、outcome source/revision identity；
- revision 前的 online-state digest、`last_finalized_date` 与
  `outstanding_target_date`；
- 16 个 EventSpec 的数量、首尾 key、ordered-key digest 和 full-spec digest。

若 predecessor pair 与 ledger latest 不一致、原 settlement 或 seal 改变、revision id 已以
不同 bytes 出现，系统均 fail closed，不重选 mutable current pointer 或 current
source。

## 3. canonical 16-event transaction

writer 直接复用 live core 已有的 `_append_revision`，对八个站点以固定顺序
交错追加：

```text
(outcome_revision, revision_rescore_recorded) x 8 = 16 events
```

这是已结算日期的 retrospective revision，不是第二次 online update。因此后置
验证必须同时证明：

- 原 `outcome_batch_settled` entry 在 revision 前后保持不变；
- 全部 station online states 与合同中的 digest 保持不变；
- `last_finalized_date` 和 `outstanding_target_date` 保持不变；
- revision registry/latest actual 精确指向新 outcome；
- 每个 event 的 state-before/state-after 不变；
- rescore 只属于 `revised_retrospective_view`，
  `updates_live_state=false` 且 `blind_metric_eligible=false`。

因而新 revision 会留下可审计的 retrospective score，却不会泄露未来信息到
已运行的 online state，也不会改写 blind metric authority。

## 4. fresh CAS 与崩溃向前采用

当 16-event slice 尚未存在时，机器只能在合同绑定的 exact pre-head 上执行
fresh CAS。若 ledger CAS 已成功而 recovery receipt 尚未持久化，下一次 poll 从
持久化 contract 重建 EventSpecs，仅在预期位置的 16 个 event 逐字节匹配时
采用该 transaction 并补终态 receipt，不再调用 CAS。

对 freeze 时已完整消费的 revision，合同明确记录
`preexisting_settled_revision_adoption`；它同样只采用已有 exact slice。对 partial、
displaced 或内容不一致的 transaction，系统 fail closed，不尝试修补或追加
第二份。

终态 receipt 的历史验证是 immutable/read-only 路径。测试覆盖 revision 完成后
追加合法 live suffix，再次 poll 不会调用 writer、重写 ledger，也不改动
recovery receipt/event。

## 5. 明确边界

本增量只实现 settled-date revision consumption。若目标日期属于 backfill projection，
coordinator 仍机器等待，不把 backfill revision 误当 settled revision；backfill
revision consumption 和 first-backfill writer 均未实现。full workset、all-successor、
derived future reservation、terminal/transitive closure 与 epoch lifecycle 等更高层声明
继续为 false。

下一窄增量是 backfill revision consumption，仍复用 immutable predecessor
authority、canonical writer 与 expected-pre-head CAS/crash-forward 框架；之后再单独处理
first-backfill writer。

## 6. 有界验证记录

本增量的定向验证覆盖真实 rev1 outstanding 消费、rev2 materialization、
rev2 canonical 16-event 消费，CAS-commit-before-receipt 的 exact adoption，以及合法
live suffix 后的历史只读复验：定向 `2/2`（1.901 s）、完整 recovery `53/53`
（4.734 s）、相邻 recovery/materializer/live-ledger/CAS/epoch-gates/main 回归
`217/217`（12.694 s）。Ruff format/check、Python compile、strict profile load 与
`git diff --check` 均通过。

两路独立只读复审均为 P0=0、P1=0。安全复审另用临时运行时验证了
consumed-at-freeze revision 的 `preexisting_settled_revision_adoption` 零事件采用与
backfill fail-wait；contract 复审确认 outstanding v2 的 exact 字段、recorded verifier、
transaction classifier 和 output 路径保持兼容。

提交前 SHA-256 为：recovery module
`99884ad28de5ab851da4e2401e67ec90d81258f014971c54711c1ba7376e492c`、profile
`ef601148860ef5d4781e934f5cafb046eb82e0bac447c3d3f41ae05c19613c37`、test
`688b395b07f11b7892e2f67918cfda3e3a253532797c479de80cb44ce8c71da6`、受保护
`main.py` `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
