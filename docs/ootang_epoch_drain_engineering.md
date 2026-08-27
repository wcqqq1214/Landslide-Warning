# 藕塘 epoch drain-start R2b 首切片工程说明

> 日期：2026-08-27
> 基线提交：`b53a238 feat: add executable epoch preparation`
> 阶段：`ootang-epoch-drain`（显式、非默认）
> 配置 SHA-256：`1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105`
> 实现 SHA-256：`c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602`
> 测试 SHA-256：`b79d132562891a3134d55073a282b351533e525b0661cd47914698e323612d68`
> 状态：R2b machine drain-start engineering only；非 drained、非 active switch

## 1. 本切片关闭与不关闭的边界

R1 固定 candidate 与 archival capsule，R2a 固定 exact executable closure、same-origin
materialized tree 和 frozen smoke。二者都没有撤销旧 epoch 的签发 route。R2b 首切片只
建立一个可恢复的机器 drain-start barrier：在全部相关 writer 被锁住且 old epoch 已是
clean start 时，先把六锁下的完整 pre-swap clean-state 边界固定为内容寻址对象、追加
exchange-attempt WAL 并在 fence inode 内武装其 terminal attempt，再原子撤销 canonical
`issue_inbox`，最后向独立 lifecycle 链追加唯一 `epoch_drain_started`。

```text
acquire manager -> cycle -> deploy -> runner -> replay -> shadow
  -> replay immutable R1 candidate tip
  -> replay matching current R2a prepared tip
  -> verify old live prerequisites and clean state
  -> publish content-addressed full intent-prefix manifest and drain capsule
  -> capture the unfenced old-route identity
  -> publish create-only singleton fence_prepare before any tombstone mkdir
  -> mkdir/recover and probe exact deny-write ACL on empty 0755 tombstone
  -> persist and reverify durable transaction intent/lower-bound, including ACL identity
  -> replay every full-clean namespace again under all six locks
  -> preflight worst-case full boundary capacity with a maximum-size staged CAS reference
  -> final exact prepared-fence verification
  -> stable-capture the actual staged queue into content-addressed storage
  -> preflight actual full boundary capacity
  -> publish the exact pre-swap full-clean boundary object
  -> append/replay the drain_exchange_attempts WAL terminal referencing that boundary
  -> arm that terminal in the fence operand as .epoch-drain-armed-attempt.v1.json
  -> immediately atomically swap canonical issue_inbox with the ACL-fenced tombstone
  -> harden canonical fence in place to exact 0555 and re-probe
  -> require same-poll post-swap logical clean state to equal pre-swap state exactly
  -> fully self-replay the same pre-swap boundary and its referenced objects
  -> append epoch_drain_started referencing that exact terminal attempt and boundary
  -> replay drain chain and rebuild head/status cache
  -> DRAINING
```

`DRAINING` 只表示旧 canonical issue route 已由机器建立 drain-start fence。它不表示
旧 epoch 已排空，不 seal old epoch，不 activate candidate，不建立自动 rotation，不把
trusted-time shadow 晋升为可信 anchor，也不授权 E2 evidence 或正式预警。

## 2. 独立 namespace 与 authority

R2b 在 registry shared root 中使用独立命名空间：

- `drain_events/`：create-only previous-hash event chain；
- `drain_head.json`、`drain_status.json`：可由 event replay 恢复的 mutable cache；
- `drain_fence_prepares/`：在任何 tombstone `mkdir` 前 create-only 发布的 singleton
  transaction marker；
- `drain_intents/`：在 route swap 前绑定 old epoch、R1/R2a tip 与预期文件系统状态的
  durable transaction reservation/lower-bound；
- `drain_exchange_attempts/`：append-only、previous-hash-linked WAL；每个 attempt 都精确
  引用一次 pre-swap full-clean boundary，且只有 unique terminal 可被武装；
- `drain_capsules/sha256/`：内容寻址的 drain 绑定证据；
- shared `objects/sha256/`：保存完整 `epoch-drain-intent-prefix`、profile/implementation、
  staged-feed 与最终 epoch-drain boundary 内容寻址对象；
- `drain_tombstones/`：交换前为 `0755 + exact extended ACL deny-write` 的空
  issue-route tombstone，以及交换后原 canonical route 的隔离 overlay。

这些对象引用但不改写 R1 `registry_events` 或 R2a preparation events/capsule/tree/smoke。
R1/R2a head/status 同样只作定位起点，R2b 必须分别 replay 权威链并交叉复验 candidate、
slot、live epoch、profile/implementation 与 current tip。orphan `fence_prepare`、intent、
tombstone 或 overlay 本身不构成 drain authority；孤立 intent-prefix/capsule/staged-feed/
boundary object 即使内容寻址正确也没有 authority。`drain_exchange_attempts` WAL、fence
operand 内的 `.epoch-drain-armed-attempt.v1.json` marker 与它引用的 boundary 只提供
crash-recovery authority；唯一能创建 DRAINING lifecycle authority 的仍是完整 replay 后的
`epoch_drain_started` event。该 event 精确绑定 terminal attempt 及其 pre-swap boundary。

intent 中的 `candidate_at_intent=true` 只表示该 durable transaction 在创建时绑定了哪个
R1/R2a candidate，作为恢复所需的下界；它明确伴随
`activation_candidate_selected=false`，不表示机器已经完成 activation selection，更不表示
candidate 已 ACTIVE。

## 3. 首版 clean-start 门禁

首版刻意不尝试在复杂 pending 状态中开始 drain。以下任一情况都返回机器 waiting，且
不追加 authority event、不改变 canonical route；若已有 durable `fence_prepare`/intent，
只保留其 transaction/lower-bound 语义，不删除、不覆写，也不把它晋升成最终 state
snapshot：

1. 缺 R1 candidate tip；
2. 缺 R2a prepared tip，或它没有追上当前 R1 candidate；
3. 缺 old-live profile/source/model/ledger prerequisite；
4. live ledger 仍有 outstanding issue；
5. verified-live guard 仍有未完成 intent/completion；
6. trusted-time 仍有未完成 request/receipt；
7. calibration shadow 仍有 outstanding/pending transaction。

这不是人工介入请求。scheduler 后续再次 poll 即可。一次 poll 发现 pending 后会释放锁并
保持旧 route，旧 epoch 的合法工作可在两次 poll 之间继续完成；下一次 poll 从全部权威链
重放，仍 pending 就继续机器等待，已经形成的合法 append-only settled extension 则自动纳入
新的 clean-state/boundary，而不是要求人工 cleanup。机器不会挑 target date、伪造 outcome、
删 pending work、人工冻结/批准、backdate 或通过 `--force` 跳过门禁。

capsule 精确引用内容寻址 `ootang_epoch_drain_intent_prefix_v1`。该完整 prefix 保存 live 与
shadow ledger 的全部 entry hashes，而不是只存 terminal；同时保存 issue、outcome、guard、
trusted-time、source authority inventories。后续每个 poll 都从该 start prefix 到 current
state 分字段证明 append-only/prefix extension：任何 ledger rollback、natural-key 改写、
receipt history 截断或 source lineage 回退都 blocked；合法 settled extension 自动进入最终
boundary。

## 4. 全锁序与并发边界

R2b 固定使用一条全局锁序：

```text
manager -> cycle -> deploy -> runner -> replay -> shadow
```

`manager` 把 lifecycle coordinator 与 R1/R2a writer 串行化；其余五锁分别阻止 fixed-point
cycle、source/issue deploy、live transaction、checkpoint replay 和 calibration shadow 在
route 切换中途开始新事务。锁必须按上述顺序取得并逆序释放；任何一级 busy 都不允许
留下部分 intent、swap 或 event。

该锁序只约束遵守同一合同的协作入口，不等于抵抗同 UID 非协作 writer、root 权限或路径
TOCTOU。后续 scheduler authorization 仍必须从系统入口层消除绕过，不能把本地锁写成
外部安全边界。

## 5. 两条不能混同的边界

R2b 明确区分物理 admission boundary 与权威 state-snapshot boundary：

1. **物理 issue-admission boundary** 是 Darwin
   `renameatx_np(..., RENAME_SWAP)` 成功的瞬间。ACL-fenced tombstone inode 在该单一
   filesystem operation 中到达 canonical `issue_inbox`，此后旧 route 不能再接受新 issue；
2. **权威 state-snapshot boundary** 是全部六锁内 full-clean replay 得到的内容寻址
   pre-swap epoch-drain boundary object，再由唯一 `epoch_drain_started` event 精确引用。它绑定
   live ledger、canonical/archived issue 与 producer receipt inventory、verified guard、
   trusted-time、outcome registry、calibration shadow、current source authority，以及已
   staged 的 next-epoch incoming inventory。staged incoming 只作为边界观察，不会被偷换为
   old-epoch source authority。

swap 只解决“从哪个瞬间停止接纳新 issue”，本身不证明当时有哪些科学状态；
`fence_prepare`、intent-prefix/capsule 与 intent 只提供可恢复 transaction 的 durable
lower-bound，也不是最终快照。WAL/armed marker/boundary 只允许恢复已持久化的交换决定，
都不能单独进入 DRAINING；未被 event 引用的 orphan attempt 或 boundary object 没有
lifecycle authority。只有唯一 event 对 terminal attempt/full-clean boundary 交叉复验通过
后，才能重建同一个 DRAINING 起点。

正常同一 poll 的不可逆临界尾部固定为：final prepared-fence identity/ACL/probe 复验 → 对实际
staged queue 做两次稳定读取并 publish-once 到 CAS → 用该真实 reference 计算 actual boundary
capacity → 内容寻址发布该 exact pre-swap full boundary → append/replay WAL attempt → 在 fence
operand 内以 marker 武装其 unique terminal → 立即执行 Darwin swap。actual capacity 与 swap
之间这段有界持久化 arming 序列不再插入 old-runtime assessor、queue recapture 或其他可变
logical-state 采样。swap 后重新 full replay 得到的 old-runtime logical clean state必须与
`final_pre_swap_clean` **精确相等**；正常路径不能把 swap 窗口内的新 extension 静默吸收到
boundary。

## 6. `fence_prepare`、macOS ACL 围栏与 canonical route 原子交换

机器先读取 canonical old route 的 exact device/inode/mode，在任何 tombstone namespace 或
candidate tombstone `mkdir` 前 create-only 发布唯一
`drain_fence_prepares/<candidate_id>.json`。该 `ootang_epoch_drain_fence_prepare_v1` marker
绑定历史 R1/R2a entry、内容寻址 drain capsule（并经它绑定完整 intent-prefix）、old-route
identity、canonical/tombstone path、ACL SHA-256、pre-swap `0755` mode 和不可降级的 Darwin
swap policy。没有匹配 marker 的 tombstone 不能被事后收编为 transaction。

marker 落盘后，R2b 才在 drain overlay 准备一个空、无 symlink/alias 的 tombstone
directory。交换前它的
mode 按冻结合同精确保持 `0755`；写保护由 inode 上的 reviewed extended ACL 提供，
语义为 `everyone deny write`。机器通过 file descriptor 安装 ACL，读取序列化 ACL 并要求
精确相等，再实际尝试 add-file；只有操作系统返回拒绝访问，才算围栏成立。单看 mode 或
自报 ACL 字段都不够。

通过 clean-state 门后，机器持有全部六锁并再次核对 canonical old `issue_inbox`、tombstone、
R1/R2a tip、old live epoch、精确 inode/mode/ACL 与所有 pending projection。唯一允许的
route mutation 是：

```text
renameatx_np(old_issue_inbox, tombstone, RENAME_SWAP)
```

该 macOS 原语在单个 filesystem 操作中跨两个 parent 交换 pathname。extended ACL 随
tombstone inode 移动，所以 canonical `issue_inbox` 在 swap 返回时已经由 ACL 围栏；原
inbox bytes 则完整进入 drain overlay，供恢复与审计。随后机器在 canonical route 的稳定
descriptor 上原位 chmod 为 exact `0555`，fsync，并再次精确读取 ACL、检查空目录和执行
add-file 拒写探针。

swap 前，空 fence operand 中必须只包含 canonical
`.epoch-drain-armed-attempt.v1.json`。该 marker 直接绑定 append-only
`drain_exchange_attempts` 的 unique terminal entry 和该 entry 精确引用的 pre-swap full
boundary。`RENAME_SWAP` 交换 inode 时 marker 随 fence operand 原子移动到 canonical
`issue_inbox`；因此 exchanged recovery 读取的是“实际随交换移动的 terminal/boundary”而
不是依据当前状态猜测一次 attempt。

实现不得退化为两个普通 rename、先 move 后建空目录、copy/delete 或其他具有“旧 route
短暂缺失/重新可写”窗口的模拟。平台/ACL API 不支持、跨 filesystem、ACL exact-readback、
拒写探针、路径或 inode 复验失败时必须 fail closed。

## 7. prepare、intent、boundary、事件与崩溃恢复

drain intent 必须先于 swap 持久化，并继承 `fence_prepare` 固定的历史 R1/R2a entries，
同时绑定 old live identity、canonical route/tombstone identity 和当时的 clean-state
prefix。它是 durable transaction
reservation/lower-bound，不是 activation decision，也不能单独授权 DRAINING。swap 前、
boundary 发布前与 event append 前都重新读取这些绑定并重放完整状态，防止用过期 preflight
发布 drain event；若 waiting poll 之间产生合法 settled extension，机器在尚未 exchanged 时
按新 tip追加新的 full-clean boundary/WAL attempt，而不是删除新记录或把旧 intent 当成冻结点。

`drain_exchange_attempts` 是 append-only WAL。每个 canonical entry 都以连续 sequence、
previous-entry hash 和精确 artifact reference 绑定一个完整 pre-swap boundary；后一个 boundary
只能是前一个 clean prefix 的合法 append-only extension。机器只允许将 WAL unique terminal
写入 fence operand 内的 `.epoch-drain-armed-attempt.v1.json`，marker 同时直引 terminal
attempt 与 boundary。WAL 缺 suffix、rollback、branch、sequence gap、额外 entry、非 canonical
filename/content、symlink 或 marker 指向非 terminal/missing/ambiguous attempt 时全部 fail
closed，不能挑另一个“看起来可用”的边界继续。

`fence_prepare` 是 create-only singleton 且永久保留。若进程在 marker 后、tombstone/intent
完成前崩溃，下一次 poll 必须从 marker 绑定的历史 R1/R2a entries、capsule/intent-prefix 和
old-route/ACL policy 继续同一 transaction。即使 registry/preparation current tip 已推进到
另一个 candidate，也不能把新 tip 偷换进旧 marker，不能删除 marker 后另起 transaction；
机器完成或阻断 marker 绑定的历史 transaction。marker 自身没有 lifecycle authority，
`candidate_at_intent=true` 仍不是 activation selection。

崩溃恢复遵守一个原则：文件系统中间态不能自行成为 lifecycle authority。未 swap 的
prepared retry 只能在全锁下重验 identity，并自动收敛严格的单一 armed-marker temp、`0755`/
`0555` recovery mode 与缺失/精确 ACL crash state；未知 temp、额外目录项、symlink、错误
mode/ACL 或不属于 WAL 的 temp 全部 fail closed。恢复武装 unique terminal 后才允许继续
swap，不需要也不允许人工 cleanup。

若进程崩溃于 swap→chmod 窗口，ACL 已与带 armed marker 的 fence inode 一起到达 canonical
route 并持续拒写；恢复必须识别精确 exchanged inode pair，原位完成 `0555` 加固并复验，
绝不通过第二次 swap 交换回去。已 swap 但 event 未追加时，机器必须从 canonical fence 内
armed marker 选择 WAL terminal，读取它在 **swap 前已经固定** 的原 boundary；当前
recovery-clean 只作为“从该 boundary clean prefix 到 current 的合法 append-only extension”
gate，严禁按 current 重建、替换或追加一个新 boundary。与正常 same-poll exact-equality
规则不同，already-exchanged recovery 可以接受这个严格 extension，随后 event 仍引用原
terminal attempt 与原 pre-swap boundary。event 已追加但 cache 未更新时只从 event、WAL/
marker/boundary 和全部历史链重建 head/status。任何 suffix rollback/branch/gap/extra/symlink、
双 route、ACL/mode/拒写探针不匹配、缺失 original inbox 或不匹配绑定都 blocked，而不是
重新开放 issue route。

event append 还有最后一道自检：机器重新打开内容寻址 boundary，执行 canonical bytes/hash、
`fence_prepare`、intent、capsule/intent-prefix、terminal WAL/armed marker、live/shadow 全链、
所有 inventory、source 与 staged-feed object 的完整语义 replay。正常 same-poll 要求 current
与 boundary clean 精确一致；exchanged recovery 则要求 current 只作该 boundary 的合法
append-only extension gate。只有这次 pre-event self-replay 通过才 create-only 追加 event。

主成功状态区分首次 `epoch_draining` 与重复 poll 的
`already_epoch_draining_idempotent`。两者都必须由相同权威 event replay 得出，不能只相信
旧 `drain_status.json`。

## 8. manifest 与 staged feed 大小合同

v1 配置显式固定 `maximum_manifest_bytes=67108864`（64 MiB）和
`maximum_staged_feed_bytes=16777216`（16 MiB）。前者用于完整 capsule、intent-prefix 与
boundary manifest 的读取/复验；后者与 R1 finalized-feed 合同相同，专用于 staged
next-epoch feed。staged feed 保存为独立内容寻址
`<sha>.epoch-drain-staged-feed.json`，boundary 只保存其精确 reference，event replay 也以同一
16 MiB 上限重读。因此合同内的大 feed 不会因旧 4 MiB control-object 上限或把 raw feed
内嵌进 boundary 而让 drain 永久自锁。

在 swap 前，机器先用“staged queue present 且 CAS reference 的 `size_bytes` 为最大 16 MiB”
这一固定长度 reference 计算 worst-case canonical boundary bytes；真实 feed bytes 不内嵌，
所以它覆盖 queue absent/present 的容量最坏情形。若完整 boundary 超过 64 MiB，返回机器状态
`waiting_for_drain_boundary_capacity`：canonical route 保持原 inode、未交换、无 event，也
不请求人工 cleanup。worst-case 通过后仍按 final fence verify → actual queue stable capture/CAS
→ actual capacity → publish exact full boundary → append WAL attempt → arm terminal marker →
immediate swap 再算一次。post-swap publisher 与 pre-event self-replay 继续执行相同 64 MiB
约束和完整语义复验；超过 staged-feed 16 MiB 或其他显式上限同样 fail closed。v1 不用
chunking、Merkle root 或截断历史规避 64 MiB 合同；任何因历史增长需要 chunk/Merkle 的设计
都属于后续 R2b-2b v2 版本，必须新版本编码，不能重解释已发布 v1 bytes。

## 9. 当前否定性声明

R2b event/status 对以下声明保持 false：

```text
activation_candidate_selected = false
old_epoch_drained = false
old_epoch_drain_implemented = false
active_epoch_switch_implemented = false
automatic_epoch_rotation_implemented = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
real_activation_ready = false
formal_warning_output = false
```

`epoch_drain_started` 不能被改名或解释为 `epoch_drained`、`epoch_rotated`、
`SEALED(old)` 或 `ACTIVE(new)`。首版即便要求 clean start，也仍需由后续独立 assessor
证明 DRAINING 状态和所有历史 namespace 的最终收口。

## 10. 真实默认链的诚实边界

R2a 验收中已有一次不注入 prebuilder、不缩短 epoch、不改机器时钟的真实默认链试跑。
五个正式 checkpoint 均完成，但 R1 在 candidate publication 前以
`Bundle was not durable before the first target natural day` 正确 fail closed。仓库没有
从 2020-07-01 连续到 2026-08-27 的 machine-finalized feed，单记录 target 已是历史日期。

因此当前没有真实 R1 candidate，也没有执行到 R2a default smoke，更不存在真实 R2b
end-to-end PASS。该负结果必须保留；不能通过更改系统时钟、合成/回填日期、人工 backdate、
冻结批准或 test-epoch override 将其改写为通过。

## 11. 后续门禁与验证记录

**R2b-2a clean-start eligibility observation/stale detection** 已在后续增量实现：机器跨
poll 保存并复验 deterministic eligibility observation，识别 stale 观察，合法 settled
extension 经二次 capture 自动纳入新观察；历史 observation 的冻结 source objects 每次重放
均重新解引用，head/status 不取得 transition authority。其输出仍只能是 DRAINING，不能把
eligibility 写成 drained/active。后续 R2b-2b-1 v2 已先实现 context-bound 首 blocker
observation，但明确不构成完整枚举、reservation、admission fence 或 recovery。后续
R2b-2b-2a 已完成 frozen official-writer lock-path cut；当前下一步是 closed-workset manifest，
之后才以精确键恢复 outstanding
guard、trusted-time request、shadow 和其他已存事务。v2 不得重新解释、补字段
或覆写已经发布的 v1 fence-prepare/intent-prefix/capsule/intent/exchange-attempt/armed-marker/
boundary/event bytes；历史 chunk/Merkle 表示也只能在该新版本中定义。

只有独立 machine drain assessor 证明 bounded workset 全部合法收口后，后续权威 transition
才可原子 `SEALED(old)+ACTIVE(new)`。cycle v4、trusted-time qualification、scheduler
authorization 和 receipt/ledger 长链 O(N²) 扫描优化仍是后续门禁。

最终冻结验证如下：

- R2b focused `12/12`，runner `1008.486` 秒、墙钟 `1025.68` 秒；
- R1+R2a+R2b+main `116/116`，runner `2361.831` 秒、墙钟 `2393.77` 秒；
- 全仓 `809/809`，runner `5568.360` 秒、墙钟 `5642.18` 秒；三组均为 0 failure /
  0 error；
- main `34/34`；Ruff check、scoped format、compileall、`git diff --check`、strict JSON
  `30/30`、根/可信时间双 `uv lock --check`、30-stage list 与默认/显式 dry-run 均通过；
- frozen v5 preflight `23/23`，报告保持 G0 PASS、G1--G4 BLOCKED、G5a 未评估/未授权、
  formal warning false；
- 97 条保护路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，共享 v4
  Bai--Perron 源无 diff；
- 最终独立标准/规格审计为 P0/P1/P2 `0/0/0`。逐对象 publication-fsync 故障矩阵保留为
  coverage debt，不代表已知实现缺陷。

这些工程门禁不改变上节真实默认链的负结果，也不构成 drained、active、rotation、E2 或
formal warning 证据。
