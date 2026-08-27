# 藕塘 machine-only anchor request recovery 工程说明

## 1. 范围与结论

本切片为 `live_outstanding -> anchor_request_recorded` 提供第一个真正调用
live-ledger expected-pre-head CAS 的 recovery adapter。它只记录一个
`anchor_requested` event；不访问 TSA/HTTP endpoint，不读取或生成外部回执，不设置人工
freeze、cleanup、approval、force 或 backdate 入口。

该 action 是 `live_outstanding` transition chain 的非终态步骤。成功 receipt 只能转向
`anchor_result_recorded`，不能完成该 key，不能解锁依赖它的 item，也不声称已实现
network recovery、完整 workset recovery、drain assessor、epoch switch 或 formal warning。

本切片只扩展旧 epoch recovery 控制面。ConvLSTM、v4 默认链、冻结数据切分、指标、
阈值、训练结论以及旧 live writer/ledger 均不修改。无参数默认链仍为
`features -> convlstm -> ootang-operational-v4`。

## 2. Frozen-prefix action contract

adapter 不从 current ledger head 重算 anchor attempt。它先完整验证当前 live chain，再按
manifest 中的 `old_live_epoch_id`、`live_event_count` 和 `live_terminal_sha256` 切出不可
重解释的 frozen prefix。当前链可以比冻结 prefix 长，但冻结位置上的 sequence/hash 必须
仍精确相同，且 frozen prefix 与 current full chain 都必须能由冻结 live core 完整重放。

从 frozen projection 中必须唯一确认：

- outstanding target、issue id 与 `issue_batch_sealed` event 等于 manifest item authority；
- seal sequence/hash、input-manifest hash 和 state hash 未变；
- 该 seal 尚无 `anchor_confirmed`；
- frozen prefix 内该 seal 的 request 数与 result 数相等；
- `attempt = frozen_requested_count + 1`。

然后调用冻结 live writer 的 canonical EventSpec builder，重建与原 `_attempt_anchor()` 完全相同
的单事件 spec。step intent 在 mutation 前 create-only 绑定一个 versioned action contract，至少
包含 expected pre-head、attempt、完整 EventSpec 及其 canonical SHA-256。因此事件已落盘后
不会因 current head 增长而生成 `attempt + 1`。

原 live writer 另有一个依赖当前进程环境的抑制分支：endpoint 未配置且历史已有
`endpoint_not_configured` failure 时，它会暂不追加下一次 request。manifest 的冻结 authority
没有绑定 endpoint 环境状态，本 adapter 也刻意不读取 endpoint，因此不能复制该瞬时分支；它只
执行 reservation 已冻结的 `anchor_request_recorded` transition。这条记录不能证明 endpoint
可用，也不能授权网络访问；后续 `anchor_result_recorded` 必须以独立冻结的机器 request/result
合同处理成功或失败。若未来需要恢复 writer 的 endpoint-wait 语义，必须新增版本化 authority，
不能在当前 action contract 中临时读取环境变量。

## 3. CAS 与崩溃接管

adapter 只向 recovery-only CAS 传入 intent 中的 expected pre-head 和单事件 EventSpec。

- fresh path 要求 current terminal head 仍精确等于 frozen pre-head；
- crash-forward path 只接管 frozen pre-head 后位置、predecessor 和 caller-controlled fields 完全一致
  的原事件；
- 原事件之后可以有通过完整链重放的合法 suffix；
- absent-key stale head、foreign position、partial/changed retry、错误 epoch 或 chain/schema drift 均阻断。

SQLite `BUSY/LOCKED` 被映射为可重试的 machine busy，不产生成功 receipt；CAS conflict、
validation、schema 或 chain-integrity 错误则映射为 fail-closed integrity failure。机器不会在冲突后
改用新 head 或新 attempt。

两个崩溃窗口的收敛规则为：

1. ledger commit 后、receipt 前崩溃：下一 poll 重放同一 intent contract，CAS 接管原事件，
   不重复追加；
2. receipt 后、recovery event 前崩溃：只读后置验证通过后补 recovery event，不重跑
   mutation adapter。

## 4. Receipt 与历史后置验证

ledger event 不是普通文件输出，因此 ActionOutput 使用
`kind=live_anchor_request_event` 且 `reference=null`。receipt semantics 绑定 event key/type、
sequence、predecessor/entry hash、live epoch、seal、attempt、EventSpec digest，并明确
`live_ledger_event_recorded=true` 与 `network_action_performed=false`。

receipt 不记录 CAS 的瞬时 `created/adopted` 分支：二者对同一持久事件必须产生完全
相同的后置语义，否则 commit-before-receipt 崩溃会改变 authority bytes。

历史 receipt verifier 从原 item intent 取回 action contract，再次重放 manifest frozen prefix，
重建同一 EventSpec，并要求真实 ledger event 恰好位于 `frozen_event_count + 1`。它只验证
不可变的事件事实和 receipt semantics，不重跑“当前尚无 result”等已经过时的前置
条件，因此合法后续 suffix 不会破坏历史 receipt。

## 5. Capability 边界

本切片可以声称：

```text
live_anchor_request_adapter_implemented=true
ledger_mutation_recovery_implemented=true
live_ledger_expected_pre_head_cas_implemented=true
```

`ledger_mutation_recovery_implemented` 只表示这一个受审单事件 adapter 已实现，不表示全部
ledger transition 或全部 reserved keys 已恢复。`live_ledger_mutated` 作为全局静态 false claim
会与成功 receipt 自相矛盾，因此不再用作通用 capability 字段；某一 step 的持久效果
由 `action_semantics.live_ledger_event_recorded` 表达。

由于 intent/receipt/status 的 authority semantics 和 claim 集合发生必要变化，它们升为 v3，
但 runtime namespace 仍是 `workset_recovery_v1`。这不是对已发布 authority 的就地迁移：若该
namespace 已有 v2 immutable intent/receipt/event，新 profile 会因 schema/profile/provenance 不匹配而 fail
closed，不覆写、转换或重解释 v2 bytes。

以下边界继续为 false：全部 reserved item 完成、全 transition 支持、terminal/transitive
closure、derived-future-work reservation、network recovery、shadow mutation、drained/active/rotation、
trusted anchor、E2 evidence、real activation 和 formal warning。

## 6. 验证与下一步

稳定实现的关键 SHA-256 为：

| artifact | SHA-256 |
| --- | --- |
| recovery profile | `beb5ff9c3e34f60451ee933bfd3dbcc3dcb5d398f575a24cfbd0ea811ac4a3f2` |
| recovery coordinator | `3f7ccfd8bcfd85046b91ca4a210b9511ee8718e504eda939c2fcef63a26ce566` |
| live CAS module | `b443da5fd92eb2e48e33918c3e6090be0e53fe2182584f6bb0ccee050dfb327e` |
| recovery test | `6e6bc2ca87da55f4ddf923dacc9239618f469bda4bff992d656564c14898a507` |
| live CAS test | `e040528d021e15d2b4e5dd5f41ce75a1a0027b8b3db244d5fce91106b501931d` |
| transition contract | `c7ecf9e5e553d54b90017f32aeab1546a7ee7f5d42dbc54e263fc0be1cd152d0` |

adapter/CAS 精准测试为 `26/26`（0.178 s）；相邻 CAS/live-ledger/recovery/manifest/
admission-cut/drain-v2/main 回归为 `101/101`（1.098 s）。Ruff check、format、strict JSON
`35/35`、35-stage list、默认 dry-run 与显式 recovery dry-run 均通过。97-path protected
aggregate 仍为 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，
11 个 frozen writer 全部匹配。独立审计无剩余 P0/P1；真实 live fixture 额外验证了
`attempt=2`、只追加一条 `anchor_requested` 且追加后科学链完整重放成功。

下一步是 `anchor_result_recorded`，但它不能简单重用本地单事件 adapter：必须先设计外部
request intent、释放内部锁前后的 generation/head fence、幂等 response object adoption、回执验证与
request/result 配对。在该 adapter 完成前，本 key 应稳定停在 machine waiting，不得转为人工
操作。Shadow CAS 与其他 issue/outcome transition 仍延后。
