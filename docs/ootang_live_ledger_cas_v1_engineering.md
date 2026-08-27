# 藕塘 live ledger expected-pre-head CAS v1 工程说明

## 1. 范围与结论

本切片新增 recovery-only 的
`code/monitoring/ootang_live_ledger_cas_v1.py`，为既有 Ootang live SQLite ledger 提供
transaction-internal expected-pre-head compare-and-swap（CAS）。它解决的是一个明确的
TOCTOU 问题：恢复器不能先在连接外检查 ledger head，再调用旧 public append；两步之间 head
可能已经变化。

该模块是加法实现，不修改 byte-frozen 的 `ootang_live_ledger.py` 或
`ootang_prequential_live.py`，因此不改变旧 live epoch 的 implementation digest、epoch identity、
schema 或正式 writer 行为。当前 recovery stage 只把 CAS 原语作为受 profile SHA 绑定的可用能力，
尚未调用它写入真实 runtime ledger；`ledger_mutation_recovery_implemented=false` 与
`live_ledger_mutated=false` 继续成立。

本切片不修改 ConvLSTM、v4 默认链、数据划分、指标、阈值、训练结果或实验结论，也不引入人工
freeze、cleanup、approval、force 或 backdate。无参数默认链仍是
`features -> convlstm -> ootang-operational-v4`。

## 2. 版本兼容边界

旧 live ledger 的 schema v1 对表、索引、trigger 与列集合执行 exact self-check，不能增加 CAS 列或
transaction metadata。新模块因此单向导入并复用旧 ledger 已冻结的 canonical primitives：spec
validation、payload encoding、schema/full-chain verification、event construction、row matching 与
insert。recovery profile 分别固定旧 ledger 与新 CAS 模块的 path/SHA-256；旧 writer 不反向导入
CAS 模块。

CAS 的冻结 pre-head 合同为：

```text
epoch_id
event_count >= 1
sequence_id >= 1
event_count == sequence_id
entry_sha256 = lowercase SHA-256
```

同一 write transaction 内必须先完整验证 ledger schema 与从 genesis 开始的 hash chain，再确认：

- genesis 类型是 `epoch_genesis`，payload 的 `live_epoch_id` 等于冻结 epoch；
- `event_count - 1` 位置上的 sequence 与 hash 精确等于 expected pre-head；
- incoming batch 非空、event key 不重复，且每个 `EventSpec` 通过旧 ledger 的 canonical validator。

只比较 terminal hash 不足以排除跨 epoch 或错误 ledger 文件，因此 epoch、count、sequence、位置与
hash 缺一不可。

## 3. 单事务状态机

CAS 在一个连接的 `BEGIN IMMEDIATE` 内完成检查、写入/采用和最终 full-chain verification：

| 状态 | 机器行为 |
| --- | --- |
| 所有 incoming key 均不存在，当前 terminal head 精确等于 expected pre-head | 连续追加，重放完整链，commit，返回 `created=true` |
| 所有 key 均已存在，稳定字段完全相同，且事件是 expected pre-head 后的精确连续 slice | 不重复写入，返回原始 stored event bytes，`created=false` |
| 上述 exact slice 后还有完整有效 suffix | storage-level CAS 仍可采用原 slice；调用 adapter 必须另行验证 suffix 的 transition 语义与授权 |
| 只有部分 key 存在、内容/顺序/位置/predecessor 改变、epoch 错或 fresh append 时 head 已移动 | conflict、rollback、零新增行 |
| schema、canonical payload 或完整 hash chain 不可重放 | integrity failure、rollback |

允许 exact slice 后存在合法 suffix，是为了支持“ledger commit 后、recovery receipt 前崩溃”之后
其他合法进度已经追加的 crash-forward 识别。这个 storage primitive 只证明原事件的内容与链位置，
不证明 suffix 的业务授权；未来 action adapter 必须根据 manifest、transition plan、锁与 admission
authority 决定 suffix 是否仍允许补 receipt，不能把 CAS 成功直接解释为 key terminal。

live schema v1 没有持久 transaction digest/position/size，因此多事件 retry 能证明 exact contiguous
slice，却不能从 schema 单独证明这些行历史上由同一个 SQLite transaction 创建。首个计划接入的
`anchor_request_recorded` 只追加单事件，不受该多事件历史边界影响；后续 multi-event adapter 需在
各自合同中明确是否可接受此限制。

## 4. Crash-forward 语义

调用方必须把完整稳定 `EventSpec` 和 expected pre-head 绑定到 create-only step intent。CAS 返回：

- `created=true`：本次 transaction 新追加；
- `created=false`：目标事件此前已经以相同内容紧邻同一 frozen pre-head 持久存在。

未来 step receipt 应至少绑定 intent hash、EventSpec digest、event key/sequence/entry hash、pre/post
head 与 `created/adopted` disposition，不绑定会因 WAL/checkpoint 改变的 SQLite 文件 SHA。SQLite
busy/locked 应成为 transient machine waiting，不生成成功 receipt，也不得自动换用新 head 重算。

## 5. 定向验证

新增的 6 个快速单元测试覆盖：

1. exact pre-head 首次提交；
2. stale head 且 key 不存在时零写入；
3. exact retry 保留原始 stored row 并返回 `created=false`；
4. exact retry 后已有完整合法 suffix 时采用原事件；
5. partial batch 与 changed content retry fail closed；
6. wrong epoch/hash/position 拒绝。

这里不运行耗时并发、fsync 故障矩阵、全仓训练或模型实验。既有 ledger 的 WAL、FULL synchronous、
schema trigger、完整 chain 与并发写锁合同继续由 frozen module 的既有测试负责。

稳定实现的 SHA-256 为：

| artifact | SHA-256 |
| --- | --- |
| recovery profile | `246dbf18bbc24cdecb7c85d289cdf4edd069fe5e47cbcbbbfb9e27e438a4ee04` |
| live CAS module | `23ca29356ef23483a0846e701376850745400082e607a16e2479c1057b6befa4` |
| live CAS test | `788b0bb17636c60fffdec13f971e9719d58fa8b600a091873875a07b2392fefe` |
| recovery coordinator | `9899fea20203b1e4e097b80ee714a59c7abd93d7eb26dd4fccc5ce541950f346` |
| recovery test | `d7417554b928a8afb51e4db0c21ff40d2f0a2f209c82476cfc905bea0f5490a4` |

CAS、frozen live ledger、recovery、manifest、admission-cut、drain-v2 与 main 的聚焦组合测试为
`93/93`（unittest 1.018 s）。Ruff、format、compile、strict JSON `34/34`、35-stage list、默认与
显式 pipeline dry-run 均通过；97-path aggregate 保持
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，11 个 frozen writer
哈希一致，独立只读实现审计无 P0/P1。

## 6. 下一步：单事件 anchor request adapter

下一切片只接 `live_outstanding -> anchor_request_recorded`，不访问 TSA 网络。一个关键 crash-forward
约束是：不能复用 recovery 当前 `_live_projection()` 的“当前 terminal head 必须仍等于 manifest
tip”门。若 event 已提交但 receipt 尚未发布，该门会在 CAS adoption 前拒绝；若从当前 head 重算，
又会错误生成 `attempt + 1`。

正确做法是完整验证当前链后，按 manifest 的 `event_count` 与 `terminal_entry_sha256` 切出并重放
冻结 prefix；从该 prefix 的唯一 seal 及既有 anchor-request 计数确定同一个 attempt，并在 step
intent 中直接绑定：

- expected pre-head 与 frozen live epoch；
- seal sequence/hash、target、issue id；
- attempt、event key 与完整 canonical `EventSpec` digest；
- protocol/code/environment/model/input-manifest/state hashes；
- adapter、旧 ledger、CAS helper 与旧 live writer 的 provenance。

随后调用本 CAS 原语，验证返回事件，再发布 create-only receipt/event。任何 foreign position、字段
漂移、错误 predecessor 或与 transition plan 不相容的 suffix 都必须阻断，禁止按新 head 自动重算
另一个 request。

shadow CAS 暂缓：shadow item 尚未逐项冻结 step pre-head，rotation 的 close→genesis 是两个事务，
空库 genesis、prior-step receipt head 传递、transaction digest/position/size 与多种 batch shape 仍需
单独版本化合同。不能因为 live CAS 已存在就宣称 shadow mutation 可恢复。
