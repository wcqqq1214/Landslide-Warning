# 藕塘 machine-only anchor result ledger adapter 工程说明

## 1. 范围与结论

本增量只消费已经 create-only 持久化并深度验证的 anchor response observation。它不重新联网，
也不读取 bearer token。消费过程在既有 `manager → cycle → replay → shadow` 四重 coordinator
锁内完成；在 ledger mutation 前只对 `external_anchor_dispatch.lock` 做一次非阻塞 durability
fence，按 object→link 顺序 exact re-adopt/fsync，随即释放：

```text
linked response observation
  -> frozen request/result pairing replay
  -> expected-pre-head live-ledger CAS
  -> branch-selected recovery receipt
  -> previous-hash recovery event
```

`candidate_confirmed` 唯一映射到 `anchor_confirmed`；`deterministic_failure` 唯一映射到
`anchor_failed`。两者均为非 terminal transition step，不代表 workset 已收口，也不产生 lifecycle、
drained、active-switch、trusted-anchor、E2 live evidence 或 formal warning authority。

本增量只修改 recovery 控制面。ConvLSTM、v4 默认链、冻结 train/validation/test splits、指标、
阈值、模型参数和科研结论均未修改；没有训练、真实 HTTP/TSA 请求或人工 freeze/cleanup/approval/
force/backdate 路径。

## 2. 锁与网络边界

response object/link 仍由上一阶段在四锁外、独立 dispatch lock 下发布。进入本 adapter 前，机器在
四锁内重新验证 reservation、manifest key、item intent、request action contract、内容寻址 object、
唯一 link、bounded raw response bytes 和 deterministic classification。

只有完整 link 存在且通过 object→link durability fence 时才允许 result CAS：

- 无 object/link：释放四锁后沿原 frozen intent 做 bounded dispatch；
- object-only：释放四锁后先 exact re-adopt/fsync object，再零网络补建唯一 link；
- linked object：四锁内非阻塞探测 dispatch lock；若 publisher 尚持锁则 machine waiting、零
  ledger mutation；取得后 exact fsync object 再 fsync link，释放 dispatch lock 后才消费；
- receipt 已存在：只做历史 replay 或补建缺失的 recovery event，不重做 result action。

因此 DNS、TLS、socket 和 response deadline 仍不占用四锁；dispatch lock 不承载 transport 与
ledger mutation 的重叠，只提供极短的 crash-durability fence；result mutation 也不会脱离四锁与
manifest authority。

## 3. Observation 到 EventSpec 的确定映射

共同字段来自 frozen request contract，而不是 mutable current head：

- request event key/type/sequence/predecessor/entry；
- canonical request body 与 digest；
- expected result pre-head；
- old live epoch、seal、target、issue、input manifest 和 state hash；
- stable idempotency key。

成功分支使用 frozen `live._stored_anchor_payload` 再验证 normalized response，并构造：

```text
event_type = anchor_confirmed
event_key  = <request prefix>:confirmed
payload    = <exact request payload> + <validated response>
```

失败分支只接受 observation schema v1 的受控 stage/code taxonomy，并构造 frozen-writer shape：

```text
event_type  = anchor_failed
event_key   = <request prefix>:failed
reason_code = request_or_receipt_validation_failed
error_type  = AnchorResultProtocolFailure
retry_policy = automatic_next_poll
```

observation 的 `stage`/`code` 保留在 recovery action semantics 中，不塞入 live event payload；
observation 自身的 `record_failure_then_automatic_next_poll` 也不会误写成 frozen live retry policy。
`AnchorResultProtocolFailure` 是 recovery-specific 的稳定分类，不宣称与旧 default client 的内部
exception 类名字逐字节相同。

## 4. Expected-pre-head CAS 与崩溃接管

adapter 调用 recovery-only `append_transaction_at_pre_head_v1`：

- fresh append 只允许 request event 仍是 exact terminal pre-head；
- crash-forward adoption 只接受 result event 位于 request 后的固定位置，字段、payload、predecessor
  和 hash 全部一致；
- exact result 后可以存在完整合法 suffix；
- foreign head、错误位置、错误 event key/content、partial mutation、epoch/schema/chain 漂移均 fail
  closed，且不追加另一条 result。

CAS 的瞬时 `created/adopted` disposition 不进入 receipt。两条路径重建相同 `ActionOutput`，其中
`action_output=null`，semantics 绑定 observation object/link reference、request identity、result
EventSpec digest、result event identity 和选定分支，并明确：

```text
live_ledger_event_recorded = true
network_action_performed = false
external_response_network_action_performed = true
remote_exactly_once = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
```

live ledger 是 confirmation 的 source of truth。本窄增量不把冗余 anchor receipt cache 文件纳入
result action；既有 live runner 的幂等 repair 仍可从 confirmed event 重建它。

## 5. Branch-selected receipt

transition graph 保留两条允许边，但 receipt 必须由已验证 outcome 收窄为一个 next action：

| observation outcome | live event | receipt `next_actions` |
|---|---|---|
| `candidate_confirmed` | `anchor_confirmed` | `["outcome_batch_settled"]` |
| `deterministic_failure` | `anchor_failed` | `["anchor_request_recorded"]` |

receipt loader 使用同一个纯函数重算分支，并交叉验证 semantics 中的 `selected_next_action`。历史
receipt 会重新加载 exact observation、重建 EventSpec 并验证 result 的固定 ledger 位置；不要求
request/result 仍是 current head，也不联网或补写 response link。

## 6. 正常崩溃窗口

- link 后、CAS 前：下一 locked poll 消费；
- object/link 名称已可见但 publisher 尚未完成 fsync：dispatch lock busy 时只 machine waiting；
  后续 poll 在同一锁内按 object→link exact re-adopt 后才允许 CAS；
- CAS 后、recovery receipt 前：下一 poll exact-adopt 同一 result，再创建 receipt/event；
- recovery receipt 后、recovery event 前：下一 poll只补 previous-hash event；
- result receipt 后有合法 suffix：历史 verifier 仍按固定位置重放；
- object-only：锁外先 durable re-adopt object、再零网络补 link，后续 locked poll 才消费。
- 仅有空的 per-step object 目录：视为 mkdir 后的无权威 crash residue；未知文件、orphan link 和
  多 object 仍 fail closed。

永久 observation 现在既可绑定 pending result intent，也可作为已完成 result receipt 的历史证据；
不会再把正常的 receipt-before-event crash 误判为 observation 分支。

## 7. 当前机器边界与下一步

failure 分支现在完全自动推进：前一张 result receipt 必须逐字段绑定 exact `anchor_failed`，retry
request contract 以该 failure event 为 expected pre-head，并只构造 `attempt + 1` 的
`anchor_requested` EventSpec。随后 result intent 又必须绑定这张 request receipt 与 ledger event，
因此历史 replay、CAS adoption 和下一轮 machine dispatch 都不会退回第一次 request，也不需要人工
freeze/cleanup/approval。

confirmed 分支的下一 action `outcome_batch_settled` 尚无 recovery adapter，因此仍保持 machine
waiting。下一窄增量应只实现该 confirmed branch 的 settlement adapter；其他 workset family、full
terminal closure 与 drain assessor 仍不在本轮范围。

## 8. 验证范围

定向测试 `36/36`（约 0.45 s）覆盖：confirmed/failure 两个分支与单一 `next_actions`、failure 后
自动 `attempt + 1`、消费阶段零网络、object→link durability fence/dispatch-lock busy、空 object
目录 crash residue、CAS commit 后 receipt 前 exact adoption、receipt 后 recovery-event forward
adoption、foreign pre-head 零 mutation，以及历史 receipt repoll。相邻 recovery、live ledger/CAS、
workset inventory/manifest、admission cut、drain eligibility、drain v2 和 main 回归 `143/143`
（约 7.67 s）。

未运行模型训练、真实 endpoint、长时间并发/容量矩阵或与本 adapter 无关的科研边界测试。
最终独立只读复审 P0/P1=0；唯一 P2 是永久回归未重复执行 attempt-2 的 response consumption/CAS，
实现端到端探针已通过，故本轮按定界快测策略不扩张重复矩阵。
