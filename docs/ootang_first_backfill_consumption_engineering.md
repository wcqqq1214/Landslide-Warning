# 藕塘 first-backfill outcome 自动消费工程说明

## 1. 本增量闭合的机器链路

本增量在 outstanding v2、settled revision v1 与 backfill revision v1 消费路径之上，
补齐 materialized outcome 的首次 backfill live-ledger writer：

```text
terminal outcome-materialization receipt
  -> immutable materializer receipt + exact outcome + source input manifest
  -> selection_kind=backfill and no predecessor revision/outcome
  -> current exact prefix: target == last_finalized + 1 day
  -> no outstanding target, issue lifecycle, seal or existing target authority
  -> canonical _append_backfill emits one aggregate backfill_not_blind EventSpec
  -> expected-pre-head CAS append or exact post-CAS adoption
  -> terminal outcome_or_revision_consumed receipt
```

整条路径由 recovery coordinator 机器执行，不增加人工 freeze、cleanup、approval、
force 或 backdate，也不调用真实网络。ConvLSTM、v4 主方法、冻结数据划分、metrics、
thresholds、模型参数和科学结论均未修改。

## 2. first-backfill root authority

first backfill 是一条独立 root 分支，而不是已有 outcome 的 revision。消费前必须同时满足：

- materialization selection 精确为 `selection_kind=backfill`；
- `previous_revision_id` 与 `previous_outcome_sha256` 都为 `null`；
- target 精确等于 current projection 的 `last_finalized_date + 1 day`；
- current projection 没有 outstanding target、outstanding issue id、issue events 或 seal；
- target 尚未出现在 backfill、settled、revision registry 或 latest-actual authority 中。

任一条件不满足即 fail closed，coordinator 不把已有日期误当成 first backfill，也不绕过
durable issue lifecycle。fresh transaction 的 exact prefix 与 immutable materializer artifacts
一起写入独立
`ootang_live_first_backfill_consumption_action_contract_v1`，后续 retry 不重选 mutable
pointer 或 current source。

该合同还绑定 previous last-finalized、online-state digest、三类 projection count、唯一
event key，以及 ordered-key/full-spec digests。既有
`ootang_live_outcome_consumption_action_contract_v2`、
`ootang_live_settled_revision_consumption_action_contract_v1` 和
`ootang_live_backfill_revision_consumption_action_contract_v1` 的字段、recorded verifier、
transaction classifier 与 output 分派保持兼容，不追溯改写历史 persisted contract/receipt。

## 3. canonical 单事件 transaction

writer 直接复用 live core 的 `_append_backfill`，只追加一个 aggregate-state event：

```text
backfill_not_blind x 1
```

该 event 不属于任一 station 或 issue，固定记录
`classification=backfill_not_blind`、
`reason=outcome_was_available_before_any_durable_issue`、
`online_state_updated=false` 与 `blind_metric_eligible=false`。它的
`state_before_sha256` 与 `state_after_sha256` 相等。

transaction 后置复验要求以下 authority 不变：

- 全部 online states 及其 digest；
- 已有 settled mapping；
- anchored seal hashes；
- blind-settled 与 engineering blind candidate counts；
- 无 outstanding target、issue lifecycle 或 seal 的前后状态。

唯一预期的机器推进是：

- `last_finalized_date` 从前一自然日推进到 target；
- backfill registry 新增该 target，`backfill_count` 精确加一；
- revision registry 为 target 注册当前 source revision 与 exact outcome hash；
- target 的 latest actual 更新为 exact `actual_by_station`；
- `latest_displacement_mm` 更新为这组 actual，成为下一日 persistence baseline。

这不会把 backfill 计入 blind metric，也不会改写 ConvLSTM 或在线学习状态。

## 4. fresh CAS 与崩溃向前采用

fresh writer 只允许在 persisted contract 绑定的 exact expected pre-head 上执行 CAS。若单事件
CAS 已提交、终态 recovery receipt 尚未落盘，下一 poll 从 persisted contract 与 immutable
输入重建同一个 canonical EventSpec，仅在 expected pre-head 后的固定位置 exact 匹配时补写
receipt，不再次调用 CAS。

transaction 缺失时才允许 fresh CAS；event 出现在预期位置则进入 post-CAS adoption；
displaced、partial-authority 或 bytes 不一致状态均 fail closed。合同也区分
`preexisting_first_backfill_adoption`，但它同样只能采用完整 canonical transaction，不能作为
容错开关绕过 root authority。

## 5. writer 覆盖与明确边界

到此，materialized outcome 的四条 live-ledger 消费 writer 已分别闭合：

- outstanding 首次 settlement；
- settled-date revision；
- backfill revision；
- first backfill。

它们仍是相互独立、schema-versioned 的持久化合同。recovery profile 升为
`2.3.0-first-backfill-consumption`，只把
`live_first_backfill_consumption_adapter_implemented` 标记为 true。

这不等于 full workset 或 lifecycle 已完成。all-successor discovery、cross-freeze derived
future work、step-level dependency reservation、terminal/transitive closure、drained/active/
rotation lifecycle、trusted anchor、E2 与 formal-warning authority 仍保持 false。

## 6. 当前有界验证记录

当前已完成两条直接路径：machine-selected first backfill 先 materialize、下一 poll 生成
canonical 单事件 transaction；以及 CAS 已提交但 receipt 未落盘后的 exact 零重复采用。
定向结果为 `2/2`（1.400 s），完整 recovery 为 `57/57`（5.606 s）。

相邻 recovery/materializer/live-ledger/CAS/epoch-gates/main 回归为 `221/221`
（13.756 s）。Ruff format/check、Python compile、strict profile load 与
`git diff --check` 均通过。当前测试未运行训练、真实网络、长并发/容量或无关边界矩阵。

两路独立只读复审均为 P0=0、P1=0。额外临时运行时验证包括：同一 frozen manifest 的
day1/day2 按 dependency 严格串行，day2 contract 使用 day1 后的 current prefix；freeze 前
已消费 root outcome 在 pointer/inbox repair 后以 `preexisting_first_backfill_adoption` 零 CAS
采用；以及合法次日 issue suffix 与无关 mutable pointer 下的历史 receipt 只读复验。

提交前 SHA-256 为：recovery module
`b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51`、profile
`5c50d168d389c286d0940a00884369ae8f65fc399f8726f0f64300999dd2de01`、test
`4227941e51697f21f5897da5f51c218ca4f1819e489cb852760178fe16036076`、受保护
`main.py` `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。

## 7. 下一窄增量

下一步不再增加 outcome writer，而是处理跨 freeze 的 derived work：设计并实现 versioned
derived-work/step-level dependency reservation，使 manifest freeze 后机器新确认的 successor
仍能获得可审计、不可歧义的依赖 authority，再以此推进 terminal/transitive closure。

该增量必须先给出可持久化合同和 fail-closed 边界；在 closure 与 lifecycle 的真实端到端证据
完成前，不能把当前四类 writer 的完成夸大为 full workset、drained 或 activation authority。
