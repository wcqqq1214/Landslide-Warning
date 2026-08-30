# Ootang atomic epoch transition 与 authorized cycle v4

## 实现边界

本增量把已经加固的 V2 scoped bounded-drain completion 接到可执行的机器生命周期边界，
同时不改变默认科研管线。`ootang_epoch_active_transition.py` 没有 profile，也没有新增
proof、head、intent、WAL 或 pointer；它只在
`runtime/ootang_epoch_registry_v1/active_transition_v1/` 发布一个 create-only immutable event
和一个 `cache_authority=false` status。

生命周期状态不是由两个可分离文件表达。唯一事件原子记录：

```text
old_official_scheduler_state = SEALED
new_official_scheduler_state = ACTIVE
```

`ACTIVE` 的精确定义是“已授权官方机器 scheduler 初始化新 epoch genesis”，不是“genesis
已经初始化”。因此事件同时固定 `new_epoch_genesis_initialized=false`；它不向旧 SQLite
ledger 追加 lifecycle row，也不移动已经位于最终 stable slot 的 candidate。

## 首次提交与历史回放

首次提交先持有旧 epoch 既有 `manager -> cycle -> replay -> shadow` 四锁，再从 completion 的
slot hash 派生并持有候选 `cycle -> deploy -> runner -> replay -> shadow` 五个 writer locks。只有在
这些 non-blocking locks 全部归 transition 所有后才重新验证：

1. strengthened V2 completion 的 immutable event、fresh live-ledger prefix 与零 actionable
   六族 capture；
2. exact current-empty R1 candidate 与 R2a prepared authority；
3. candidate identity、stable slot、new live epoch id，以及 same-slot shadow root 除已持有的 regular
   lock file 外无数据；
4. cycle-v4 scheduler adapter 已存在且 bytes 可固定。

事件绑定 completion event 的 path/hash/size，exact R1/R2a sequence/hash，old live fresh
count/terminal，candidate/slot/new epoch，live 与 same-slot shadow roots，frozen executable tree，
tree 内 exact cycle-v3 script/config，以及 cycle-v4 adapter SHA-256。create-only publication 和
同 bytes repoll 是幂等的，同路径异 bytes 或任一 historical reference 漂移都会 fail closed。候选
writer 已占锁时本 poll 返回 busy；writer 先完成并留下 ledger/receipt/shadow 数据时，锁内的第二次
current-empty 验证会拒绝 transition，因而不存在检查后抢写再提交 ACTIVE 的窗口。

事件发布后，repoll 与 scheduler lease 不再调用 candidate 的 current-empty gate。它们从事件固定
的 selectors 深回放 historical R1/R2a chain、candidate object、executable capsule/tree、smoke
receipt 与 completion bytes；因此 cycle-v4 合法初始化新 ledger/genesis 后，不会让历史 transition
失效。这一分离避免把“提交前必须为空”错误地持续应用到已经 ACTIVE 的 epoch。

## 官方 scheduler 入口

`ootang_prequential_cycle_v4.py` 是公开无参数的 scoped official scheduler adapter。它先取得
transition 的 manager authorization lease，在 lease 整个存续期内重放事件和历史 authority、
验证自身 SHA-256，然后只执行事件固定的 frozen cycle-v3 script/config，并只传事件固定的 live
与 shadow roots。公开 CLI 没有 runtime-root override。

没有 transition event 是正常机器等待：写入独立、可替换、非权威 status 后以 0 退出。child
返回 0 表示本 poll 完成，3 保留 busy 语义，其他非零返回或 adapter/tree/script/config 漂移均
fail closed。cycle-v4 status 只是诊断 cache，不加入 lifecycle event chain，也不授予任何 authority。

这使机器 scheduler 可以按 `settlement -> active transition -> authorized cycle v4` 重复调用，
不需要人工冻结、批准、清理、force 或 backdate；但它还不是一个跨多代自动选候选并连续轮换的
常驻控制器。

## Claims 与科研边界

唯一 event 的正向声明严格限定在 `official_machine_scheduler_lifecycle`：machine-only、scoped
lifecycle/transition authority、candidate selected、official scheduler 的 old `SEALED` 与 new
`ACTIVE`、active switch 与 scheduler entrypoint authorization，以及它所消费的
`bounded_official_workset_drained=true`。

以下声明仍明确为 false：不加限定的 `old_epoch_drained`、generic old-work/canonical-route/
direct-filesystem fences、old direct live-v1 entrypoint disabled、anti-rollback、trusted anchor、
E2 eligibility、real activation ready、formal warning、automatic calibration promotion、continuous
automatic rotation、cross-ledger single-database atomicity，以及 new genesis initialized。这里的
原子性只指一个 immutable lifecycle event 同时表达 scoped `SEALED(old)+ACTIVE(new)`；不能扩写
为任意 writer 或两个 ledger 的数据库事务。

当前 transition 属于本机 trusted-writer 模型，不绑定 RFC 3161，所以
`anti_rollback_authority_implemented=false` 与 `trusted_anchor_receipt_verified=false` 是有意保留的
真实边界。
若要获得外部或 root-resistant 资格，后续必须另行引入独立 TSA/KMS/透明日志证据，不能把本地
SHA-256 event 解释为已具备该能力。

ConvLSTM、operational v4、冻结 splits/metrics/thresholds、模型参数、预测产物和科研结论均未
改动。本增量不运行训练、网络、全量科研管线或历史慢速 fault matrix。

## 验证与后续

completion、transition、cycle-v4 与 main 聚焦测试合计 `53/53`，执行 4.337 秒。Ruff check/format、
Python compile、默认三阶段与显式 `settlement -> transition -> cycle-v4` dry-run、diff check 均通过；
97-path protected aggregate 保持
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。最终独立审查发现并关闭
两个实质竞态：candidate writer 曾可在 empty check 与 publish 之间抢写，以及 candidate release
异常曾可跳过 old coverage/manager release；修复后复审无剩余 P0/P1/P2。

Transition、cycle-v4、对应 focused tests、`main.py` 与其 test SHA-256 分别为
`443cba9c5faec370f0d87e167623ff306b8cfd6b35e5679a291fbc2a9258a484`、
`3a65d31fed29c8bf16f9c9db4959260bc0332287101982b5fdc92ad6a2799600`、
`ce587c2070e15b82ed55bfae714ecb72e1f9c237ca9ea2c463f61dea75fb7680`、
`abe878d0011c89f0e33af57bc2656c9b3bf9fb6d4d1ebea25a01cd9fd14d0a55`、
`83af0111181c4635056dfad10a8346eaebe5cd893071544b0face8a896458001` 与
`45c2e3f8f12573903102d65dc9c6ba095073b497a3d743592e07c2128d44b4a4`。

下一步应先让机器 scheduler 在真实但隔离的 runtime 上完成一次
`settlement -> transition -> cycle-v4/genesis` 可观测运行，并核对 scoped status 与 ledger genesis；
随后再分别处理外部 trusted-time/anti-rollback 资格和多代 continuous rotation controller。两项都
不能通过扩大本事件 claim 或人工 waiver 代替。
