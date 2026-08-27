# 藕塘 live ledger expected-pre-head CAS v1 工程说明

## 1. 范围与结论

本切片新增 recovery-only 的
`code/monitoring/ootang_live_ledger_cas_v1.py`，为既有 Ootang live SQLite ledger 提供
transaction-internal expected-pre-head compare-and-swap（CAS）。它解决的是一个明确的
TOCTOU 问题：恢复器不能先在连接外检查 ledger head，再调用旧 public append；两步之间 head
可能已经变化。

该模块是加法实现，不修改 byte-frozen 的 `ootang_live_ledger.py` 或
`ootang_prequential_live.py`，因此不改变旧 live epoch 的 implementation digest、epoch identity、
schema 或正式 writer 行为。当前 recovery stage 已通过受 profile SHA 绑定的单事件
`anchor_request_recorded` adapter 调用该原语。因此可声称窄化的
`ledger_mutation_recovery_implemented=true`，但只覆盖这一个受审 transition，不是完整
ledger/workset recovery。通用 occurrence claim `live_ledger_mutated` 不再静态写入所有 authority；
单步持久效果由 receipt action semantics 记录。

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

已接入的 step receipt 绑定 intent action contract、EventSpec digest、event key/sequence/entry hash 与
pre-head，不绑定会因 WAL/checkpoint 改变的 SQLite 文件 SHA。它也不绑定瞬时
`created/adopted` disposition：二者对同一持久事件必须生成相同 authority，否则
commit-before-receipt 崩溃会改变 receipt bytes。SQLite busy/locked 是 transient machine waiting，
不生成成功 receipt，也不得自动换用新 head 重算。

## 5. 定向验证

独立 CAS 快速测试覆盖：

1. exact pre-head 首次提交；
2. stale head 且 key 不存在时零写入；
3. exact retry 保留原始 stored row 并返回 `created=false`；
4. exact retry 后已有完整合法 suffix 时采用原事件；
5. partial batch 与 changed content retry fail closed；
6. wrong epoch/hash/position 拒绝；
7. SQLite connect/transaction busy 被分类为可重试 busy，而不是成功或普通 conflict。

这里不运行耗时并发、fsync 故障矩阵、全仓训练或模型实验。既有 ledger 的 WAL、FULL synchronous、
schema trigger、完整 chain 与并发写锁合同继续由 frozen module 的既有测试负责。

本接入切片稳定验证时的关键 SHA-256 为：

| artifact | SHA-256 |
| --- | --- |
| recovery profile | `beb5ff9c3e34f60451ee933bfd3dbcc3dcb5d398f575a24cfbd0ea811ac4a3f2` |
| live CAS module | `b443da5fd92eb2e48e33918c3e6090be0e53fe2182584f6bb0ccee050dfb327e` |
| transition contract | `c7ecf9e5e553d54b90017f32aeab1546a7ee7f5d42dbc54e263fc0be1cd152d0` |

本 adapter 与 CAS 的精准组合测试为 `26/26`（0.178 s），Ruff check 与 format 均通过。
独立审计无剩余 P0/P1；真实 live fixture 额外贯通验证了 frozen prefix 中已有一次失败
request 时重建 `attempt=2`、只新增一条 `anchor_requested`，且追加后完整科学链重放成功。
本精准计数不覆写已提交 CAS 增量的历史 `93/93` 聚合记录。

## 6. 已接入 anchor request 与下一步

`live_outstanding -> anchor_request_recorded` 已按原设计接入，且不访问 TSA 网络。
实现完整验证 current chain 后，按 manifest 的 event count 和 terminal SHA-256 切出并重放
frozen prefix；从该 prefix 的唯一 seal 及既有 anchor-request 计数确定同一 attempt，并在
step intent 中直接绑定：

- expected pre-head 与 frozen live epoch；
- seal sequence/hash、target、issue id；
- attempt、event key 与完整 canonical `EventSpec` digest；
- protocol/code/environment/model/input-manifest/state hashes；
- adapter、旧 ledger、CAS helper 与旧 live writer 的 provenance。

随后调用本 CAS 原语，验证持久事件，再发布 create-only receipt/event。任何 foreign position、字段
漂移、错误 predecessor 或与 transition plan 不相容的 suffix 都必须阻断，禁止按新 head 自动重算
另一个 request。

下一步为 `anchor_result_recorded`。它需要另设外部 request intent、释放内部锁前后的
generation/head fence、幂等 response-object adoption、回执验证和严格 request/result 配对。
本地单事件 CAS 不能自动授权这类外部副作用；在对应合同完成前，机器稳定
waiting，不转人工操作。

shadow CAS 暂缓：shadow item 尚未逐项冻结 step pre-head，rotation 的 close→genesis 是两个事务，
空库 genesis、prior-step receipt head 传递、transaction digest/position/size 与多种 batch shape 仍需
单独版本化合同。不能因为 live CAS 已存在就宣称 shadow mutation 可恢复。
