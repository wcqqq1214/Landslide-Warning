# 藕塘 epoch drain v2 首阻塞项观察 R2b-2b-1 工程说明

## 1. 结论与范围

R2b-2b-1 是一个显式、非默认、仅机器运行的 **v2 sibling foundation**。它解决的第一个
问题是：在旧 epoch 尚有未完成事务时，把 v1 full-clean gate 返回的**首个阻塞项**记录为
确定性、内容寻址、绑定候选与旧 ledger tip 的 observation。它尚未枚举完整 closed workset，
也没有阻止新旧工作进入；因此不修改 R2b/R2b-2a v1 bytes，不把 observation 写成恢复完成、old epoch
drained 或新 epoch active。

只读状态机审计发现，原先文档中的线性顺序
`R2b v1 -> R2b-2a -> R2b-2b` 对 non-clean start 不可达：R2b v1 只有在入口已经
full-clean 时才发布 `epoch_drain_started`，R2b-2a 又只观察该唯一 v1 authority。因此
non-clean foundation 不能被写成 R2b-2a 后继 lifecycle transaction：

```text
R2b-v1 clean start ---------------------> DRAINING
R2b-v2 fresh non-clean first-blocker observation -> OBSERVED (非 DRAINING authority)
```

发现任何 durable v1 drain transaction 或 authority 时，v2 不得迁移、收编、加字段或重放
为 v2；v1 取得优先级，既有 v2 observation 立即 inert。由于冻结 v1 并不读取 v2 namespace，
本切片明确 `v1_v2_mutual_exclusion_implemented=false`，不得把单向检测写成互斥。clean start
返回 `not_applicable_clean_use_v1`，不创建 v2 event。

## 2. 本切片实现与未实现

本切片实现：

- 新 v2 profile、schema 和独立 `drain_v2/` namespace；
- 在固定六锁顺序下执行 v1/R2a authority 复验与 clean/pending 分类；
- 把当前首个明确 pending family 映射为稳定自然键；内部 synthetic hook 验证固定排序、去重
  和 item/bytes 上限，但 production inspector 本切片最多发布一个 blocker；
- create-only 发布 content-addressed first-blocker observation 与 singleton event；observation
  绑定 R1/R2a event、candidate/slot、old live epoch 与 ledger tip；
- same-state repoll byte-idempotent；unknown、ambiguous、event/object tamper 和版本共存均
  fail closed 或 machine waiting；
- event 重放必须 exact dereference observation 的 path/hash/size/schema/context/items；即使
  随后出现 v1 witness，缺失或篡改 observation 也先阻断；
- 任一锁 busy 时不写 event、observation、head 或 status；
- head/status 仅为可修复 cache，不具 lifecycle/transition authority；
- CLI 只接受冻结的 `--config`，无人工日期、冻结、批准、cleanup、force 或 backdate。

本切片尚未实现：

- guard completion、trusted-time receipt、outcome/revision、live settlement、shadow reconcile
  的精确 action adapter；
- 非空 canonical route 的 durable producer cut、跨 TSA 网络调用的解锁/重锁提交协议；
- workset quiescence assessor、route exchange 和 v2 `epoch_drain_started` lifecycle event；
- `SEALED(old)+ACTIVE(new)` 原子 transition、cycle v4、scheduler authorization；
- trusted anchor、E2 evidence、real activation 或 formal warning。

因此能力字段只允许 `first_blocker_observation_implemented=true`、
`observation_binds_r1_r2a_authority=true` 与
`observation_binds_old_epoch_context=true`，同时固定
`bounded_workset_reservation_implemented=false`、
`complete_workset_enumeration=false`、`old_work_admission_fence_implemented=false`、
`bounded_workset_recovery_implemented=false`。observation 不表示闭合集合、producer cut 或
恢复许可。

## 3. 版本与 authority 隔离

v2 精确绑定已提交的冻结上游：

- R2b v1 profile：`1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105`；
- R2b v1 implementation：`c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602`；
- R2b-2a profile：`2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a`；
- R2b-2a implementation：`46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc`。

v2 不得改写或重解释 v1 fence-prepare、intent-prefix、capsule、intent、exchange-attempt、
armed marker、boundary、drain event、eligibility observation/event。v2 first-blocker object、event、
head/status 使用新的 schema/suffix；孤立 object 与 cache 没有 authority。event 只有“冻结
clean gate 在绑定 context 下报告过这个首 blocker”的 observation authority，
`lifecycle_authority=false` 且 `transition_authority=false`。head/status 都是可由 durable event
重建的 cache，不作为输入、authority 或 anti-rollback witness；同时删除 event 与 observation
的 rollback 不能靠 mutable cache 检出，故 `anti_rollback_authority_implemented=false`。

## 4. Workset 闭包

本版本只识别以下六个 pending family，但每个 durable observation **恰好一个 item**。自然键
优先使用冻结 gate reason 中的 ISO 日期；无日期时显式绑定 family 与冻结 status/reason 的
canonical digest。它们是后续完整枚举器的闭包候选，不是本切片已经捕获的 closed workset：

1. `issue_route_replay`：既存 issue object、producer receipt、replay receipt 的精确关系；
2. `live_outstanding`：既存 outstanding live issue；
3. `outcome_revision`：既存 outcome/revision natural key；
4. `guard`：已有 guard intent 所授权的 completion；不得为 direct-v1 issue 补造 intent；
5. `trusted_time`：已有 request 的相同 nonce/DER 后续；不得生成新 nonce；
6. `shadow`：以冻结 live upper tip 为上界的 shadow transaction/classification。

缺少 durable upstream reservation、receipt-without-request、completion-without-intent、branch、unknown family
或无法构成唯一自然键时必须阻断，不能猜测、合成 outcome、删除旧文件或扩大 selector。

## 5. 锁与执行边界

首 blocker 观察与 event 提交顺序固定为：

```text
manager -> cycle -> deploy -> runner -> replay -> shadow
```

逆序释放。本切片只在锁内观察并提交 observation，不调用会再次获取这些锁或动态发现新工作的
public poll。未来 action adapter 也必须以 manifest 精确键运行：普通本地 action 在合适的
under-lock primitive 中完成；TSA 网络特例只能锁内确认/复用已有 request，解锁发送同一 DER，
再重获全部锁做 exact-CAS link/receipt。每 item 每 poll 至多尝试一次，外部依赖等待不得 spin。

## 6. 否定性声明

所有 v2 profile、event 与 status 必须保持：

```text
bounded_workset_reservation_implemented = false
complete_workset_enumeration = false
old_work_admission_fence_implemented = false
bounded_workset_recovery_implemented = false
epoch_drain_started_implemented = false
canonical_old_issue_route_fence_implemented = false
v1_v2_mutual_exclusion_implemented = false
anti_rollback_authority_implemented = false
lifecycle_authority = false
transition_authority = false
drained_eligibility_current = false
old_epoch_drained = false
old_epoch_drain_implemented = false
activation_candidate_selected = false
active_epoch_switch_implemented = false
automatic_epoch_rotation_implemented = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
real_activation_ready = false
formal_warning_output = false
```

## 7. 后续最短路径

后续 R2b-2b-2a 已先补齐 frozen official writer 的 deploy/runner durable lock-path cut：它不
修改自绑定旧 writer，只用 deny-write regular-file sentinel 与 Darwin atomic swap 封闭正式
入口；该 event 仍明确不是泛化 admission fence 或 closed reservation。当前下一步是**完整**
bounded manifest 枚举，使 first-blocker observation 升级为真正的 closed-workset reservation。其后的 R2b-2b-2
才实现 manifest-keyed action adapters，并以 step receipt 证明每个旧工作向前收口；它不能
重新 selector 或持续追随新 tip。随后再做双 capture quiescence 与 v2 DRAINING lifecycle
event。再后才是独立 assessor 和原子 active transition。
每一阶段继续使用 synthetic 快环，只在里程碑末尾运行一次 gated real non-clean chain，禁止
重复 R2b/full-repository 长测。

## 8. 冻结与验证记录

冻结 SHA-256：

- profile `aa12082e32b9b94fc4ad4b08232ed586b49c8c1bf1d2ccdaf3587b096c047d17`；
- implementation `93a6463f514d73c4809287e1bbc8033984c82f550d5c55ce84c209475d7b604b`；
- tests `d315d394b536eec4b1ea09c7a9683cfcbc0ebadc58a98758268f32d3329ed488`。

定向 v2 synthetic `9/9`，与 main 合并快测 `45/45`；Ruff、format、compile、strict JSON、
默认三阶段及显式 stage dry-run、根/可信时间双 `uv lock --check` 均通过。未修改冻结 v1
实现，且不重复真实 R2b、NGBoost 或全仓长测。97-path aggregate 保持
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，共享 v4
Bai--Perron 源无 diff。
