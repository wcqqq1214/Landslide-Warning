# 藕塘旧 epoch 官方 writer lock-path cut R2b-2b-2a 工程说明

## 1. 结论与范围

本切片新增显式、非默认、仅机器运行的 `ootang-epoch-admission-cut`。它不修改任何已在
live ledger、guard intent、issue replay receipt 或 calibration shadow ledger 中自绑定哈希的
旧 writer，而是在六锁临界区内把活动 runtime 的两个共享科学 writer lock pathname：

```text
deploy_cycle.lock
runner.lock
```

依次与同文件系统上的 deny-write regular-file sentinel 做 Darwin
`renameatx_np(RENAME_SWAP)` 原子交换。交换后，旧 writer 仍按冻结代码以 write-open 获取锁，
但 canonical pathname 已指向 ACL + `0444` sentinel，机器因此拒绝新的普通 source、issue、
outcome、live/guard、replay、trusted-time 与 shadow writer 进入。

它只建立“冻结官方 writer 入口”的物理前提，尚未枚举 closed workset，也没有授权任何恢复
action。因此 `old_work_admission_fence_implemented`、complete enumeration、reservation、
recovery、DRAINING lifecycle、drained、active switch、rotation、trusted/E2/formal 等泛化声明
继续为 false。

## 2. 为什么不直接修改旧 writer

旧 runtime 不是普通的无状态脚本集合，多个 durable record 会绑定当前实现字节：

- live ledger 的事件上下文与 epoch id 包含 `ootang_prequential_live.py` 的实现 SHA；
- verified-live intent 要求记录的 guard implementation SHA 等于当前文件；
- issue replay receipt 递归复验 verifier/live 实现；
- calibration shadow event 要求当前 runner+ledger implementation digest 与历史一致；
- issue/outcome producer receipt 也保存实现 provenance。

因此给这些文件直接添加“读取新 marker”的 import 会让待排空的旧 epoch 本身立即不可重放。
本切片在 profile 中逐字冻结 11 个 writer/orchestrator 文件，并在执行前复验其 SHA-256；任何
漂移都 fail closed。这个边界比表面上的共享 marker 更重要。

## 3. 覆盖的官方 writer 边界

冻结 writer 的锁入口形成以下覆盖：

| family | 普通写入入口 | 首个共享写锁 |
|---|---|---|
| source authority | source ingest | deploy |
| issue object/receipt/route | issue producer | deploy（随后 runner） |
| outcome/revision | outcome materializer | deploy（随后 runner） |
| live outstanding / guard | legacy live、verified live | runner |
| independent replay | issue replay | runner（随后 replay） |
| trusted time | trusted-time shadow | runner |
| calibration shadow | shadow reconcile | active runner（随后 shadow） |

cycle v1/v2/v3 只提供外层 cycle serialization；它们最终仍必须进入上表的 deploy/runner
writer，所以不需要改其自绑定代码。profile 仍将三个 cycle 文件冻结并复验，避免入口假设在
未升级协议的情况下漂移。

该覆盖只针对仓库中冻结的正式机器入口。能够绕过入口直接改 active runtime 文件的本地敌手、
外部进程或未登记 writer 不在当前证明内，故
`direct_filesystem_writer_fence_implemented=false`，也不能把本切片写成完整 old-work
admission fence。

## 4. 事务与崩溃恢复

首次 cut 采用固定顺序：

```text
manager -> cycle -> deploy -> runner -> replay -> shadow
prepare -> sentinels -> intent -> attempt -> deploy swap -> runner swap -> event
```

- `prepare` 先绑定已复验的 v2 first-blocker observation、R1/R2a/candidate/old epoch 与交换路径；
- 两个 sentinel 是 create/recover-only regular file，内容绑定 prepare，安装固定 deny-write ACL，
  mode 精确为 `0444`；
- `intent` 在任何 swap 前绑定旧 lock inode 与 sentinel inode，成为 forward-only 物理事务依据；
- previous-hash-linked `attempt` 绑定每次实际 cut 前的 machine-current old live context；固定
  R1/R2a/candidate/slot/old epoch 不得改变，live count 只能单调增加，同 count 的 terminal
  必须精确相同，所有 durable 时间也不得倒退；
- deploy 必须先交换。它一旦 cut，source/issue/outcome admission 已关闭；如果此处崩溃，恢复
  不再 acquire/flock 已封闭 deploy pathname，而是获取仍开放的 runner/replay/shadow 后继续；
  对封闭 pathname 唯一的 `O_RDWR` 尝试是预期失败的 denial probe，成功反而立即 integrity block；
- runner 交换后所有正式科学 writer 均被阻断。后续恢复跳过已封闭 lock 的 acquisition，使用
  single-fd bytes/ACL/mode/inode 复验与 denial probe，并只向前发布唯一 event；
- 不存在 restore、unfence、cleanup、force、approval、target date 或 backdate 路径。

若 prepare 后、首次物理 cut 前机器崩溃，普通 writer 可能合法推进旧 runtime；恢复必须在六锁
下追加新的 context-bound attempt，不能把旧 attempt 冒充当前边界。若 v1 authority 在首次
物理 cut 前取得，v1 优先，本 transaction inert；deploy cut 后 v1 也无法再 write-open其所需
锁，但本切片仍保守地保持 `v1_v2_mutual_exclusion_implemented=false`。

## 5. Authority 与否定性声明

唯一 event 只证明：冻结的官方 writer lock pathname 已按 intent/attempt 交换并持续拒绝
write-open。mutable status 只是 cache。prepare、sentinel、intent 或 attempt 单独都不是
lifecycle/transition authority，event 也不表示 workset 已闭包或完成。

本版本仅允许以下特定能力为 true：

```text
machine_only
frozen_writer_bytes_preserved
official_writer_lock_path_cut_implemented
deploy_lock_path_cut_implemented
runner_lock_path_cut_implemented
physical_cut_crash_resume_implemented
```

以下能力必须保持 false：

```text
complete_workset_enumeration
bounded_workset_reservation_implemented
bounded_workset_recovery_implemented
old_work_admission_fence_implemented
canonical_old_issue_route_fence_implemented
direct_filesystem_writer_fence_implemented
v1_v2_mutual_exclusion_implemented
scheduler_entrypoint_authorization_implemented
anti_rollback_authority_implemented
epoch_drain_started_implemented
lifecycle_authority
transition_authority
drained_eligibility_current
old_epoch_drained
old_epoch_drain_implemented
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
activation_candidate_selected
trusted_anchor_receipt_verified
e2_live_evidence_eligible
real_activation_ready
formal_warning_output
```

同时删除全部 durable prepare/intent/attempt/event 和物理 sentinel 的对手级回滚不由 mutable
status 检出，因此 anti-rollback authority 仍未实现。

## 6. 下一步

下一步是在两个 lock pathname 已物理封闭、官方旧 writer 不再推进的稳定边界内，一次性完整
枚举六 family 及传递义务：issue route/producer/replay、live outstanding、source+outcome
revision、guard、trusted-time request/DER/link/receipt、shadow cursor 到冻结 live upper tip。
manifest 必须逐路径绑定 hash/size、namespace digest、自然键、依赖与允许的 successor state；
超过上限、unknown/orphan/branch 或无法构成唯一键时 fail closed，不能截断。

只有 closed manifest event 成立后，才实现 manifest-keyed v2 action adapters。TSA 仅允许复用
manifest 中已有 request 的相同 nonce/DER，网络阶段解锁后必须重获机器 recovery lock 并对同一
fence generation + item 做 exact-CAS；不能重新运行会获取已封闭旧锁的 public poll。

## 7. 冻结与验证记录

冻结候选 SHA-256：

- profile `fe4e91768c8558d887a34465fa6c9f4e8c05f8c1a7cf07e061bc602733136c1c`；
- implementation `95b675b132c5051cbbc4d34041b9686d122a64c6368f04c0bff8d6dddf1effcf`；
- tests `a26c7ebc476d0931ed0373f2b2fd5e6bc8255ed19ee9c3276a545a2a834ed8b3`。

临时目录 synthetic 快环在真实 Darwin regular-file ACL 与
`renameatx_np(RENAME_SWAP)` 上覆盖正常 cut/byte-idempotence、prepare/sentinels/intent/
attempt/deploy/runner/event 七个崩溃点、deploy-only 后 current-context extension、manager/
runner busy 零事务写、v1 precedence、prepare/sentinel mode+ACL/intent/attempt/event 篡改以及
CLI 无人工控制与 durable child pre-publish 时钟回拨。admission-cut `8/8`，连同 main stage
contract 合计 `45/45`，墙钟约 1 秒；
Ruff、format、compile、strict JSON `32/32`、根/可信时间双 `uv lock --check`、33-stage list、
默认三阶段与显式 v2→cut dry-run 均通过。97-path protected aggregate 保持
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，共享 v4
Bai--Perron 与 11 个 frozen writer/orchestrator 对 HEAD 均无 diff。未重复运行 R2b、真实
non-clean 长链、NGBoost 或全仓长测。最终独立 authority/standards 审计为 P0/P1/P2
`0/0/0`。
