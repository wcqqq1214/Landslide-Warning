# 藕塘旧 epoch closed-workset manifest R2b-2b-2b 工程说明

## 1. 结论与范围

本切片新增显式、非默认、仅机器运行的 `ootang-epoch-workset-manifest`。它以已经完成的
R2b-2b-2a official-writer lock-path cut 为唯一前置 authority，在冻结的官方旧 writer 不再
推进后，一次性枚举旧 epoch 六个 work family 及其传递 artifact 义务，并发布内容寻址
manifest 与 create-only singleton reservation event。

该 event 只授权后续 adapter 对 manifest 中的 exact natural key 做机器恢复。它不执行
recovery action，不生成 outcome，不创建或替换 RFC 3161 nonce/DER，不关闭 epoch，也不选择
或切换 candidate。因此 generic admission fence、recovery、DRAINING lifecycle、drained、
`SEALED(old)+ACTIVE(new)`、rotation、trusted anchor、E2 与 formal warning 继续为 false。

## 2. 前置 authority 与锁边界

枚举器必须完整重放 admission-cut 的 prepare、intent、previous-hash-linked attempts、singleton
event 和两个 canonical sentinel 的 inode/mode/ACL/bytes。它从 terminal attempt 恢复固定的：

- R1 registry event 与 R2a preparation event；
- candidate、slot 与 old live epoch；
- 冻结 live event count 和 terminal SHA-256；
- deploy/runner 两个 official writer pathname 已 cut 的物理状态。

由于 deploy/runner canonical lock 已成为 deny-write regular-file sentinel，后继阶段禁止再以
旧 public poll 获取这两个锁。枚举只按 `manager → cycle → replay → shadow` 获取仍开放的四锁。
admission cut 未成立时机器保持 waiting；任一锁 busy 时不得写 manifest/event。该锁边界只覆盖
profile 中冻结的官方 writer，不能扩大解释为阻止同 UID 直接文件写或未知外部 writer。

## 3. 六个 family 与闭包

manifest 固定包含六个 family descriptor，即使某族当前为零 item 也不能省略：

| family | natural key / 传递义务 | 合法 successor |
| --- | --- | --- |
| `issue_route_replay` | old epoch + target + issue id；issue、producer receipt/object、input manifest、replay receipt 与 opened/sealed/settled binding | `issue_replay_receipt_verified` / `issue_route_replay_consumed` |
| `live_outstanding` | old epoch + target + issue/seal；outstanding lifecycle、guard/outcome dependency 与 `anchors/` receipt namespace | anchor receipt/request/result repair 或 `outcome_batch_settled` |
| `outcome_revision` | target + source revision；incoming feed、current/activation/revision chain、outcome receipt/object/active pointer 与 live consume binding | `source_snapshot_ingested` / `outcome_materialized` / `outcome_or_revision_consumed` |
| `guard` | target + pre-head；replay receipt、intent、live open/seal 与 completion | `guard_completion_recorded`；目标日已到或 outcome 已到但未 open/seal 时 `superseded_by_backfill` |
| `trusted_time` | target + issue/seal + nonce/imprint；request、DER、response link、TSR object 与 receipt | 缺 DER 时确定性 `trusted_time_request_der_repaired`，其后 link/receipt |
| `shadow` | shadow/live epoch + 每个 source event/revision；logical ledger、coverage 与 frozen live upper | outstanding settlement、逐事件 classification 或 cursor 收口 |

每个 item 使用确定排序并包含 exact natural key、固定 successor、dependency keys、contained
relative artifact reference（path/SHA-256/size）和 namespace digest。dependency key 必须解析到
同一 manifest 的唯一 item 或明确的 frozen context；不能用自由文本暗示闭包。

以下任一情况整体 fail closed，且不能发布部分 manifest：unknown/hidden/non-regular entry、
orphan、重复 natural key、ledger/receipt branch 或 gap、active pointer 与 chain tip 不一致、依赖
缺失、路径逃逸、hash/size 不符、同键异语义、family/item/byte 上限溢出。

live 与 shadow SQLite 文件本身不是 CAS authority；manifest 保存只读全链重放得到的 schema、
event count、terminal SHA-256 与有序 event refs。共享 `objects/sha256` 完整扫描后，以 durable
source pointer/receipt、issue/replay/outcome records 和 active pointer 为根做传递可达性检查；实际
对象集合必须等于可达集合。孤立 TSR、DER-without-request、anchor/object orphan 与 shadow
cursor 假完成均阻断。request 已存在但 DER 缺失是唯一可由 manifest 保留的 trusted-time
局部修复：DER 由原 nonce/imprint 纯读重建，不创建新 nonce，也不在枚举阶段写文件。

## 4. 内容寻址与 reservation authority

manifest 使用 canonical JSON，按 family 固定次序及 natural key 排序；poll time 不进入 manifest
bytes。文件发布到：

```text
runtime/ootang_epoch_registry_v1/workset_manifest_v1/
  manifests/sha256/<manifest-sha256>.json
  events/00000000000000000001-<entry-sha256>.json
  status.json
```

singleton event 精确引用 manifest 的相对 path、SHA-256 和 size，并绑定 admission-cut event、
terminal attempt 和 authority context。相同稳定状态重复 poll 必须字节幂等；已有 event 与当前
完整枚举不同、manifest/event/reference 被篡改或出现第二个 event 时全部阻断，不能悄悄另开
reservation。`status.json` 只是可修复 cache，不参与 authority。

历史 reservation 重放与 action predecessor 检查被刻意分开：重放只验证 immutable
event/manifest bytes、profile/cut binding、内部 digest 和发布器 provenance，不重新要求 mutable
ledger、route 或 active pointer 仍保持 capture 时的 bytes。否则第一个合法 recovery 会让第二个
item 永久不可达。未来每个 exact-key adapter 必须在执行该 key 前单独做 predecessor exact-CAS，
并由 step receipt 证明 successor。waiting/blocked status 的“当前完成/当前预留”字段保持 false，
不会把 profile 的实现能力误报成当前 authority。

本切片只允许以下新增能力为 true：

```text
machine_only
admission_cut_binding_verified
content_addressed_manifest_implemented
complete_workset_enumeration
bounded_workset_reservation_implemented
```

至少以下声明继续为 false：

```text
bounded_workset_recovery_implemented
old_work_admission_fence_implemented
direct_filesystem_writer_fence_implemented
v1_v2_mutual_exclusion_implemented
anti_rollback_authority_implemented
lifecycle_authority
transition_authority
old_epoch_drained
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
trusted_anchor_receipt_verified
e2_live_evidence_eligible
real_activation_ready
formal_warning_output
```

manifest 与 event 还记录当前 publisher implementation 的 path/SHA-256/size；该 provenance 不
加入 profile 自哈希，避免 publisher↔profile 的不可解循环。inventory helper 则由 profile
冻结 SHA-256，任何未审修改都会在发布前 fail closed。

## 5. 快速验证边界

本切片只执行新 inventory/manifest、相邻 admission-cut/drain-v2 与 `main.py` 定向测试，并做
Ruff、compile、JSON、dry-run、protected-path 与 frozen-writer 检查。不重复全仓测试、模型训练、
NGBoost/SHAP、TSA 网络请求、真实大规模 4096/16384 容量或完整 symlink/fsync/crash 排列；这些
不会提高本次状态机语义的主要证据强度，却会显著拉长任务时间。

## 6. 下一步

下一切片为 manifest-keyed action adapters。每个 adapter 只能接收 reservation 中存在的 exact
key，先重放 manifest/context，再把该 item 向预声明 successor 前推，并发布 step receipt。
trusted-time 网络阶段只能复用 manifest 已绑定的 request/nonce/DER，或按已保留 request
确定性修复同一 DER；释放锁执行外部请求后，
必须重获 recovery lock 并对同一 fence generation 和 item 做 exact-CAS。任何新发现的旧工作
都表示 manifest 不完整，应阻断而不是临时追加或人工清理。

只有所有 reserved item 都由独立 assessor 证明合法收口，后续生命周期切片才可讨论
`SEALED(old)+ACTIVE(new)`。本切片不运行 R2b、NGBoost、ConvLSTM 或全仓长测；真实 non-clean
链留到 manifest + keyed recovery 里程碑末尾只运行一次。
