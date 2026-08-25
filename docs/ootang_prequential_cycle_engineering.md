# 藕塘 E2-B2 机器 outcome 与闭环编排工程说明

> 建立日期：2026-08-26
> 状态：`e2b2_engineering_only_not_live_evidence`
> 配置：`config/ootang_prequential_cycle.v1.json`
> 配置 SHA-256：`2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef`
> 正式预警输出：`false`

## 1. 目标与不变量

E2-B2 的目标是把 E2-B1 的 finalized source、五种子 bundle、issue producer 与
E2-A ledger 接成可重复调度、无需逐日人工操作的闭环。它不增加人工冻结、人工选日、
人工批准或人工补签接口，也不把缺失数据改写成伪造 outcome。

闭环固定顺序为：

```text
source ingest
  -> bundle ensure
  -> E2-A reconcile/genesis
  -> outcome materialize
  -> E2-A reveal/backfill/revision
  -> issue produce
  -> E2-A seal
  -> repeat until scientific progress token is stable
```

所有现有科学边界保持不变：runner-independent checkpoint/input replay、可信
密码学时间、自动 epoch registry/rotation、E2 evidence 与 real activation 仍为
false；正式 v5 的 G1--G4 也不受本模块影响。cycle 配置还必须精确绑定
deploy/live profile 的版本化 SHA，不允许一部分旧合同与一部分新合同混跑。

## 2. source pointer v2 与 snapshot receipt

source producer 的 current pointer 已升级为
`ootang_source_current_pointer_v2`，发布不再仅依赖一个可变 pointer：

- 每个目标日的 source revision 使用
  `ootang_source_revision_receipt_v1` predecessor/sequence 链保留完整历史；
- 每次全局 source snapshot 写入内容寻址
  `ootang_source_snapshot_receipt_v1` 链，带 `snapshot_sequence_id`、前驱和唯一 tip；
- current pointer 必须递归验证两类 receipt/object，并精确指向全局唯一 tip。

进程在 snapshot receipt 已提交但 current pointer 尚未发布时崩溃，下一 poll 可以
从完整验证后的 tip 恢复缺失或陈旧 pointer。任何 r1→r2→r1 回退、分支、
孤儿、重复 sequence、非 tip 绑定或 receipt/object 篡改都 fail closed；恢复不是对未
验证最新文件的盲信。旧 `ootang_source_current_pointer_v1` 不做静默迁移；需要由
未来的 immutable epoch registry/automatic rotation 明确建立新 epoch。

## 3. outcome 选择规则

materializer 只从 `ootang_live_source.CanonicalSource.records` 中读取已经通过 source
producer 递归验证的逐日原始记录，不从模型预测、ledger score、人工表格或当前日期
推导真值。候选优先级固定为：

1. 已结算/回填日期的当前 source revision 尚未进入 ledger 时，先物化 revision；
2. ledger 有 sealed outstanding issue 且 current source 已含同一目标日 finalized
   record 时，物化该 blind-order outcome；
3. 无 outstanding issue 且 `last_finalized + 1 <= source watermark` 时，只物化下一
   个连续 backfill 日期；
4. 其余情况写机器 waiting 状态，不请求人选择日期。

同一 revision id 复用但内容改变、source 回退到已知旧 revision、日期断裂、未到
`finalized_at_utc`、source/epoch id 不一致或 ledger/status/hash 损坏均 fail closed。
对 activation watermark 及更早记录/修订，materializer 返回持久等待态
`waiting_epoch_rotation_required`；它不在旧 epoch 中暗中重放、回写或要求人工批准。

## 4. outcome 持久化与崩溃恢复

每个 outcome 先生成内容寻址 input manifest，绑定 cycle/deploy profiles、current
source artifacts、精确逐日 record、实现与依赖环境；候选再通过 E2-A
`load_outcome_batch()` 合同预检。每个目标的 canonical outcome bytes 与 input
manifest 都是 content-addressed object；每个 revision 再写入含
`revision_sequence_id`、predecessor 和 exact object binding 的 immutable receipt。
每目标 receipt registry 必须有唯一 tip，独立
`ootang_outcome_active_receipt_pointer_v1` 指向当前 revision，最后才发布
`outcome_inbox/YYYY-MM-DD.json`。

因此 receipt 是 revision commit record，active pointer 是唯一当前绑定，inbox 只是
consumer-facing publication。进程在 object→receipt、receipt→pointer 或
pointer→inbox 任一窗口崩溃时，下一 poll 从完整验证的唯一 tip/object
恢复，而不重新发明另一份科学语义。新 revision 只有在旧 receipt/object 和 active
链仍完整时才能原子推进；旧 revision 保留，分支、非 tip pointer、回退或篡改均拒绝。

权威 source/projection 读取和发布窗口固定按 `deploy -> runner` 锁序执行，并在
写前、写后重采生产机器 UTC；不传入 cycle 入口的旧 `now` 穿过长操作。
一个 invocation-scoped 单调 sampler 覆盖初始读取、多 receipt 恢复、候选发布和
blocked status。若跨链观测 `10→11→9→10`，本轮已经恢复的 pointer/inbox 会逆序
撤销，阻断状态使用最后成功观测的 11，而不会回退到初始时间。异常被规范化为 busy
或 integrity 错误，旧正常 status 不会在失败后继续误导调度器。

## 5. fixed-point cycle

cycle orchestrator 长持独立非阻塞 `cycle_lock`，但调用子阶段时不预持
deploy 或 runner lock，避免子模块重复获锁。只在子阶段之间采集科学
snapshot 时按 deploy → runner 短持两锁，防止独立 producer/runner 并发造成
torn snapshot。每个子阶段返回后，cycle 重新限定 status path、拒绝 symlink
alias，并递归校验 schema、config provenance、evidence flags 和阶段状态白名单。

科学 progress token 只绑定：

- source current pointer 及其 verified receipt tip；
- model bundle manifest；
- ledger 的 verified scientific projection，包括 epoch/state/last/outstanding/seal、已确认
  anchors、revisions 和 latest actuals；
- issue/outcome receipt registries、active bindings 和 inbox exact bytes。

token 明确排除每次 poll 都变化的时间戳、status bytes、raw ledger head/
event count 以及持续失败的 anchor requested/failed 尝试。raw head 只写审计
status，不影响 fixed point。before/after token 相同即返回 `converged` 或
`converged_waiting`。空 feed/activation/model/outcome 是可成功收敛的 waiting，不是人工
介入信号。

单次调用最多执行 64 个不同科学状态的轮次。若每轮都是合法单调进展但 backlog
尚未清空，返回 `work_remaining/exit 0`，交给调度器再次调用；不将正常长 backlog
误报为 blocked。cycle status 持久化 continuation token history，下一次调用会严格验证并
继续重复检测；回到任何已见科学 token，包括跨调用才闭合的 65-state ring，才是
oscillation 并 fail closed。

zero-byte 或已有有效 SQLite schema 但尚无 genesis 的 crash 状态被表示为可恢复
pre-genesis token，以便第一个 live reconcile 自动初始化；损坏 schema/链则仍 blocked。
runtime root 外逃、root 内部指向科学 object 的 mutable symlink alias、树中 symlink 与
非白名单 fatal status 全部 fail closed。生产调用者不能注入假 stage 或 token builder；
仅显式设置 `OOTANG_E2B_ALLOW_TEST_CYCLE_OVERRIDE=1` 的测试进程可使用依赖注入。

锁竞争统一为 busy/exit 3，schema/hash/revision/time/persistence/oscillation 冲突为
blocked/exit 2。无论 waiting、busy 或 blocked，cycle status 中
`e2_live_evidence_eligible`、`real_activation_ready` 及其他 evidence 标志始终为 false。

## 6. 验证记录

当前 focused 验证已通过：

- outcome materializer 31/31；
- cycle 23/23；
- cycle/main 联合 50/50；
- E2-B2 相关联合测试 197/197，全仓 566/566；
- Ruff、compileall 与 `git diff --check`；
- 真实空 runtime 七阶段 poll 只生成 status/lock，一轮返回
  `converged_waiting`，未生成 source/model/issue/outcome/ledger。

正式 v5 fail-closed preflight 23/23，G0 仍 PASS、G1--G4 仍 BLOCKED、G5a 未授权。
E1 manifest/metrics/site/station 四项 SHA 与既有记录一致，97 条保护路径聚合仍为
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`；配置
cycle/deploy/live SHA 也与页首及绑定合同一致。独立对抗复审修复 invocation 级时钟
回退后最终无 P0/P1。本文件仍不声称 E2 live evidence 或真实激活已经成立。

## 7. 剩余门禁

E2-B2 闭环编排完成后仍有四个独立门禁：

1. runner-independent checkpoint/input replay，不仅信任 producer 结果；
2. pinned provider 与可验证签名的可信密码学时间；
3. immutable epoch registry、预构建与安全自动 rotation；
4. 避免 receipt/ledger registry 每次重复全链扫描导致 O(N²) 增长的性能优化。

当前 input manifest 精确绑定实现与依赖，因此代码或环境变化会 fail closed，必须由
第 3 项的新 epoch 协议接管，不能原地假装兼容。所有门禁都不能用人工日期、冻结、
批准、签名或伪造 outcome/backfill 绕过。
