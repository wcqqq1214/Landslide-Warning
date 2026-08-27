# 藕塘 epoch drain eligibility R2b-2a 工程说明

> 更新日期：2026-08-27
>
> 基线提交：`2eefceb feat: add atomic epoch drain barrier`

## 1. 目标与边界

`ootang-epoch-drain-eligibility` 是显式、非默认的机器阶段。它在 R2b 已经原子围栏
canonical old-epoch issue route 并追加唯一 `epoch_drain_started` 之后，跨 scheduler poll
持久化“机器在该次发布瞬间重新验证到 clean DRAINING state”的观察。它解决两个问题：

1. 同一完整状态重复 poll 必须字节级幂等，不能因 wall-clock 变化不断制造新证据；
2. old runtime 出现合法 settled extension 后，机器必须自动识别旧观察不再是 current，
   并在完整复验新状态后追加新观察。

该阶段不是 drain completion assessor，也不是 lifecycle 或 active-transition authority。
Observation/event 显式固定 `observation_authority_only=true`、
`lifecycle_authority=false`、`transition_authority=false`；事件仅记录
`drained_eligibility_current_at_publication=true` 这一历史事实。它同时固定
`lifecycle_state=DRAINING`、`old_epoch_drained=false`，以及
activation candidate、active switch、automatic rotation、trusted anchor、E2 evidence、real
activation 和 formal warning 全部 false。未来 transition 必须重新取得相同机器锁并精确
复验 terminal observation 与 machine-current state，不能只读取 event、head 或 status。

## 2. 冻结上游与历史 authority

R2b-2a profile 精确绑定已经提交的 R2b bytes：

- `config/ootang_epoch_drain.v1.json` SHA-256
  `1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105`；
- `code/monitoring/ootang_epoch_drain.py` SHA-256
  `c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602`。

观察器不调用 R2b start/swap/hardening 写路径，也不要求 drain event 绑定的 candidate 仍是
最新 R1/R2a tip。它从已经持久化的唯一 R2b event 恢复历史 R1/R2a selector，再完整重放
event → intent → capsule → exchange-attempt WAL terminal → armed marker → exact pre-swap
boundary，并复验 canonical fence。这样，registry 后续出现新 candidate 不会错误阻断正在
排空的历史 old epoch；同时也不能把新 candidate 偷换到既有 drain transaction。

## 3. 独立 namespace 与 authority

R2b-2a 只新增以下独立 namespace：

```text
runtime/ootang_epoch_registry_v1/
├── drain_eligibility_events/
├── drain_eligibility_head.json
├── drain_eligibility_status.json
└── objects/sha256/<sha>.epoch-drain-eligibility-observation.json
```

Observation 先作为 content-addressed object durable publish，随后 event 才可引用它。孤立
observation 没有 authority。Event 是独立、连续 sequence 和 previous-entry-hash 链，文件名
同时绑定 sequence 与 entry SHA；unknown entry、gap、branch、非法 suffix、symlink、
non-canonical JSON 或 reference/path/hash/size 漂移一律 fail closed。

`drain_eligibility_head.json` 和 `drain_eligibility_status.json` 都是 mutable cache；status
另显式固定 `cache_authority=false`，二者都不是输入 authority。链领先于 cache 代表 event
已落盘但 cache 更新前崩溃，机器按 cache 自身引用的历史 tip 复验时间和 hash 后从完整 event
chain 前推。at/behind replayed chain 的 cache 必须精确匹配真实 entry；同序号伪 tip 或生产上
不可达的 status/lifecycle/count 组合只触发本次 fail-closed，不会被提升为持久 witness。只有
schema/profile/time/负声明完整合法且 strictly-ahead 的 cache 才在 blocked status 中保留，
用于发现 event suffix rollback。若外部同时删除 event suffix 与所有 mutable ahead witness，
则缺少独立外部证据，v1 不能单独证明该 rollback。文档不把这一威胁边界包装成已经解决；
若未来威胁模型要求覆盖，需新增独立 immutable commit-receipt chain，而不能重解释当前 v1
bytes。

## 4. 六锁 machine-only 算法

每次 poll 非阻塞地按固定顺序取得：

```text
manager → cycle → deploy → runner → replay → shadow
```

在全部六锁内，机器执行：

1. 清理 create-only publication 或 cache replace 崩溃遗留的严格 `.tmp/*.create` 与
   `.tmp/*.cache`；unknown、非 regular 或 symlink temp entry 直接 fail closed；
2. 从 persisted R2b event 选择历史 R1/R2a authority；若尚无唯一
   `epoch_drain_started`，返回 machine waiting，不生成 observation/event；
3. 完整重放 R2b event、boundary、WAL terminal、armed marker、intent/capsule 与 route
   fence；
4. 从 intent 恢复 archive route，并先复验 DRAINING ledger prefix、无 fence 后新
   outstanding issue 及 archived issue inventory 精确等于 event boundary；再使用该 route
   完整重放 live ledger、issue/replay receipt、
   outcome registry、guard completion、trusted-time receipt、calibration shadow 与 current
   source authority；历史 eligibility observation replay 还必须逐条重新解引用各自冻结的
   semantic/activation/snapshot source objects，不能只检查 metadata 自洽；
5. 再次以 machine-current clean state 重放 R2b，证明它是 authoritative pre-swap
   boundary 的合法 extension，且 archived issue inventory 精确不变；
6. 比较 terminal eligibility observation 与 current clean state；
7. 若需发布新语义，先 durable publish deterministic observation CAS，再做第二次完整
   capture/replay；只有第二次与拟发布 observation 精确相等时才 append event；
8. 完整 replay eligibility chain 后刷新 head/status cache。

Observation 不含 poll timestamp，也不含 staged next-epoch incoming。时间只记录在首次引用
某个新 observation 的 event；因此同一 clean semantics 的重复 poll 保留首次 bytes 和时间。
Staged incoming 在 R2b 中已经被明确界定为 next-epoch queue，不属于 old-epoch source
authority，其变化不能刷新 old-epoch eligibility observation。

## 5. current、stale、waiting 与 extension

机器状态转换固定为：

| machine-current 状态 | 动作 | event 行为 |
|---|---|---|
| 无唯一 R2b drain event | waiting for epoch draining | 不追加 |
| current clean 与 terminal observation 精确相等 | current idempotent | 不追加，保留首次 bytes |
| current 是 terminal 的合法 settled extension | 发布新 CAS，复验稳定后追加下一 event | 追加一条 |
| 当前存在 outstanding/pending work | waiting，status 将 current 标为 false | 不追加 stale event |
| prospective observation 超过 64 MiB | capacity waiting，留待 v2 | 不追加 CAS/event |
| CAS 后第二次 capture 合法前进 | waiting for stable observation；orphan CAS 无 authority | 不追加 |
| 当前相对历史 observation rollback/branch/改写 | integrity block | 不追加 |
| R2b event/boundary/fence/WAL/marker 复验失败 | integrity block | 不追加 |

合法 settled extension 直接复用冻结 R2b 的递归规则：live/shadow entry SHA 保留完整 prefix；
既有 guard/trusted natural-key records 不变；outcome receipt history 只可追加；source id 与
activation manifest 固定，records/revision history 不截断，watermark 与 snapshot sequence
不下降；old epoch、candidate、drain event 与 boundary origin 固定；archived issue inventory
必须精确不变。任何 pending 状态只令 mutable status 的
`drained_eligibility_current=false`，不会修改旧 event；旧 event 永远只陈述其发布瞬间。

## 6. 持久化与崩溃恢复

Observation/event 使用 R2b 的 durable publication/adoption 合同：create-only link 后 fsync
文件、直接 namespace parents、registry root 及其 parent；重试遇到 identical existing bytes
也重新执行 durable adoption。六锁取得后先机器清理严格识别的 crash temp；unknown temp
不会被忽略。Event append 在 observation durable 之后，head/status 在完整
event replay 之后，所以典型 crash state 自动向前恢复：

- observation 已发布、event 未发布：对象是无 authority orphan；下一 poll 精确复验后可复用；
- event 已发布、head/status 未刷新：event chain 是 authority，下一 poll 前推 cache；
- head/status 丢失：从 chain 重建；
- head 超前、chain gap/branch/suffix mismatch、symlink 或对象/reference 篡改：fail closed。

任何 post-sample integrity failure 都会尽力把 mutable status 改写为
`blocked_integrity`、`drained_eligibility_current=false`，但不会用较短 replay 覆盖完整合法且
strictly-ahead 的 head/status rollback witness；因此删除 suffix 后连续 poll 仍持续阻断，
直到原 tip 精确恢复。同 count 伪 tip 和不可达 status 组合不会被固化，合法的
event-ahead/status-behind crash state 则自动前推 cache。
该 blocked status 仍无 authority。Event `recorded_at_utc` 的首条不得早于所绑定 R2b event，
后续又不得早于 eligibility terminal event；历史 chain replay 执行同一因果时间门。机器时钟
回退不能伪造更早资格观察。
CLI 只接受冻结的 `--config`，没有 date、freeze、approval、force、cleanup、backdate 或
test-epoch 参数。

## 7. 计算边界与后续阶段

Observation v1 上限为 64 MiB，继续使用 full clean-state manifest。prospective object 超限
时机器返回 `waiting_for_drain_eligibility_capacity`、current=false，且不写 CAS/event；历史
已引用 object 超限仍按 integrity block。每次 poll 会完整重放
R1/R2a/R2b、live/shadow 和 receipt inventories；eligibility chain 若逐条验证 full
observation，累计成本可能为 O(K²)，发布前双 capture 还会增加常数成本。这是显式工程债，
不能被描述成已优化。

后续 R2b-2b-1 v2 已实现 context-bound 首 blocker observation，但 complete enumeration、
reservation、admission fence 与 recovery 仍为 false。下一阶段先建立 shared machine
admission cut 与 closed-workset manifest，再按 manifest 精确键机器恢复 outstanding guard、
trusted-time、shadow 等 workset。未来 checkpoint/chunk/Merkle
或 immutable commit receipt 也只能进入新版本；不得重解释、补字段或覆写已经发布的 v1
fence-prepare、intent-prefix、capsule、intent、exchange-attempt、armed marker、boundary、
drain event、eligibility observation/event bytes。之后才是独立 drain assessor，再之后才可能
有权威 `SEALED(old)+ACTIVE(new)` transition、cycle v4 与 scheduler authorization。

## 8. 冻结候选与验证

本增量以已提交的 `2eefceb feat: add atomic epoch drain barrier` 为基线，并作为独立
R2b-2a 里程碑提交。冻结 SHA-256 为：

- profile `2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a`；
- implementation `46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc`；
- tests `4e247497ca78a4449ae00769ad1c383c5b5279195c357edcf84e29af4e5754a4`。

冻结字节验证记录：public eligibility `20/20`；包含真实 R2b authority 构造、首次观察和
幂等复询的 full eligibility `21/21`（runner `144.659` 秒，墙钟 `156.49` 秒）；main
`35/35`；正式 v5 preflight `23/23`，仍为 G0 PASS、G1--G4 BLOCKED、G5a 未评估/未授权、
formal warning false。31-stage list、默认三阶段 dry-run、R2b→R2b-2a 显式 dry-run、strict
JSON `30/30`、根/可信时间双 `uv lock --check`、Ruff 与格式检查均通过。97 条保护路径聚合
保持 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，共享 v4
Bai--Perron 源无 diff。已提交基线 `2eefceb` 的全仓结果仍为 `809/809`。本 scoped 增量无需
重复全仓；一次额外 full discovery 在运行 `4184.67` 秒、进入无关 NGBoost
horizon-sensitivity 用例时被主动中断，中断前无 failure/error。该次不构成完整 suite PASS，
后续 agent 不应再次重复，除非发布范围或代码面发生实质扩张。

两组独立只读规格/模式审计在最终 SHA 上均给出 P0/P1/P2 `0/0/0`。未逐项展开的 coverage
debt 包括五把非 manager 锁、各 durable publication/fsync fault point、source
activation/snapshot receipt 及错误根/symlink/hash/size 的分项测试；这些路径复用已验证的
冻结 R2b helper，但未来仍应补齐 fault matrix。完整 eligibility chain 的累计 O(K²) 重放和
大量私有 R2b helper 耦合也是明确架构债。
