# 藕塘 machine-only anchor result response observation 工程说明

> 历史阶段说明：本文记录 response-observation 增量的冻结边界。其后续 result CAS adapter 已实现，
> 当前合同见 `docs/ootang_anchor_result_ledger_adapter_engineering.md`；本文中的“尚未实现/下一步”只描述
> `507b5a5` 提交时点，不再代表仓库当前状态。

## 1. 范围与当前结论

本增量为已冻结的 `anchor_result_recorded` item intent 增加四重 coordinator 锁外的 bounded
HTTPS dispatch，以及 create-only、content-addressed response observation。它只回答以下问题：

- 是否已经使用 intent 中冻结的 endpoint、canonical body 和 idempotency key 发起一次 bounded
  request；
- 是否观察到一个可完整持久化的确定性 HTTP response；
- 该 response 是符合 frozen live interface 的 `candidate_confirmed`，还是可重放的
  `deterministic_failure`；
- crash 或并发 repoll 后，机器能否在不再次联网的情况下接管已经落盘的 observation。

本增量**尚未执行** `anchor_result_recorded` ledger transition。它不追加 `anchor_confirmed` 或
`anchor_failed` event，不调用 expected-pre-head CAS，不创建 recovery step receipt/event，也不选择
`anchor_result_recorded` 之后的 transition branch。因此，response observation 只是后续 result
adapter 的输入证据，不是 live-ledger result、可信时间证明、生命周期 authority 或 terminal recovery。

实现对应 recovery profile
`1.5.0-anchor-result-response-observation`。intent、item-intent、receipt 和 status authority 升为
v5；response observation/link 分别使用：

```text
ootang_epoch_workset_anchor_result_response_observation_v1
ootang_epoch_workset_anchor_result_response_link_v1
```

本增量不修改 ConvLSTM、v4 默认科研链、训练/验证/测试切分、指标、阈值、模型参数或实验结论。

## 2. 两阶段机器状态机与锁边界

一个 poll 被拆为内部 authority selection 和外部 dispatch 两阶段：

```text
四重锁内
  deep-verify reservation / manifest / item intent / exact live head
  -> 创建或接管唯一 anchor-result item intent
  -> 返回 AnchorResultDispatchPlan
  -> 释放 manager -> cycle -> replay -> shadow 四重锁

四重锁外
  获取 external_anchor_dispatch.lock
  -> 重新读取并精确验证 item-intent snapshot 与 action contract
  -> 优先加载或接管既有 response object/link
  -> 无 observation 时读取 bearer token
  -> bounded HTTPS POST
  -> durable create-only response object
  -> durable create-only response link
  -> 释放 dispatch lock
```

dispatch lock 与四重 coordinator 锁不重叠。DNS、TLS、socket write、response read 和 deadline
等待不会占用 manager/cycle/replay/shadow 锁。当前只有一个 pending recovery step intent 被允许，
独立 dispatch lock 又把本机 dispatch、object publication 和 link publication 串行化，从而避免两个
本地进程同时为同一个 step 产生网络操作。

当前可见状态包括：

| 状态 | 含义 | 本 poll 是否联网 |
|---|---|---:|
| `waiting_for_external_anchor_token` | token 缺失或不满足严格内存校验；intent 保持 pending | 否 |
| `waiting_for_external_anchor_retry` | transport retryable 或 delivery ambiguous；没有 observation | 是 |
| `external_anchor_response_observed` | 一个确定性 response observation 和 link 已 durable 发布 | 是 |
| `external_anchor_response_forward_adopted` | crash 遗留 object 被验证并补建 link | 否 |
| `waiting_for_locked_anchor_result_consumption` | exact link/object 已存在，等待下一次四锁内消费 | 否 |
| `blocked_integrity` | intent、namespace、object、link、凭证反射或 artifact replay 发生不可接受漂移 | 视失败点而定 |

`network_action_performed` 是单次返回值和 status cache 中的运行事实，不是静态全局 capability。
存在 response observation 也不等于实现了端到端 network recovery。

## 3. Frozen request 与 HTTPS transport contract

transport 不重新解释 endpoint 或 request payload。它只使用 immutable item intent 的 action
contract：

- exact normalized HTTPS endpoint；
- canonical JSON request body 及 SHA-256；
- `timeout_seconds`；
- `maximum_response_bytes = 1 MiB`；
- 由 frozen request-event identity 派生的 64 位小写 SHA-256 idempotency key；
- bearer-token environment-variable 名称。

默认 HTTP request 固定为 `POST`，headers 为：

```text
Authorization: Bearer <dispatch-time token>
Content-Type: application/json
Accept: application/json
Accept-Encoding: identity
Idempotency-Key: <frozen 64-hex key>
User-Agent: ootang-workset-recovery/1
```

client 使用 no-redirect opener。总 transport deadline 由 frozen timeout 驱动，并覆盖 open 与
slow response read；SIGALRM 被继承为 blocked、已有 process alarm 或无法安全安装/恢复 deadline
时均 fail closed。socket timeout 仍被传入 opener，但不作为 slow-drip total deadline 的替代。

response 最多读取 `maximum_response_bytes + 1` bytes，以便确定性地区分合法上限与 overflow。
成为 `candidate_confirmed` 必须同时满足：

- status 恰为 `200`；
- final URL 与 frozen normalized endpoint 逐字相同；
- media type 恰为 `application/json`；
- charset 为缺失或 `utf-8`；
- content encoding 为缺失或 `identity`；
- 完整 body 不超过 1 MiB；
- body 能由项目 strict JSON decoder 解码并保持 canonicalizable；
- 顶层 response 满足 frozen `live._anchor_response` interface：仅含
  `provider`、`receipt_id`、`anchored_at_utc`、`root_sha256`、`receipt`；
- `root_sha256` 精确覆盖 request 绑定的 sealed ledger entry。

`live._anchor_response` 的归一化输出被存入 `validated_response`，字段为
`provider`、`receipt_id`、`anchored_at_utc`、`sealed_entry_sha256` 和 `receipt`。这只是 interface
validation；当前 frozen profile 明确仍为
`interface_only_no_cryptographic_verifier_e2a`。

## 4. Retryable、ambiguous 与 deterministic failure

机器只在存在完整、确定性 response 事实时创建 observation。以下情况归为 retryable 或 delivery
ambiguous：

- DNS、TLS、connect、socket、reset、timeout 等 `URLError` / `OSError` 类 transport failure；
- total deadline 到期；
- HTTP `408`、`425`、`429`；
- HTTP `5xx`。

这些情况返回 `waiting_for_external_anchor_retry`，不创建 response object/link，不追加 ledger
failure，不更换 request body 或 idempotency key。远端可能已经接受 request、而本地未观察到完整
response；因此下一次机器 poll 只能用同一 key 重试。

已完整观察到的下列情况归为 `deterministic_failure`，并作为 observation 持久化：

| stage | code | 条件 |
|---|---|---|
| `response_body` | `response_too_large` | body 超过 1 MiB |
| `http_status` | `redirect_rejected` | HTTP `3xx`，且未跟随 redirect |
| `http_status` | `request_rejected` | 除 retryable status 外的 HTTP `4xx` |
| `http_status` | `unexpected_http_status` | 其他非 `200` status |
| `transport` | `final_url_changed` | final URL 不等于 frozen endpoint |
| `transport` | `unexpected_content_encoding` | encoding 不是缺失或 `identity` |
| `transport` | `unexpected_media_type` | media type 不是 `application/json` |
| `transport` | `unexpected_charset` | charset 不是缺失或 `utf-8` |
| `response_json` | `invalid_strict_json` | UTF-8/JSON/duplicate-key/non-finite/canonicalability 校验失败 |
| `response_contract` | `invalid_anchor_response` | frozen anchor response schema、time、root 或 receipt 校验失败 |

failure object 固定记录：

```json
{
  "stage": "<bounded enum>",
  "code": "<bounded enum>",
  "error_type": "AnchorResultProtocolFailure",
  "retry_policy": "record_failure_then_automatic_next_poll"
}
```

这里的 `retry_policy` 描述后续 result adapter 应如何把该 observation 映射为 frozen
`anchor_failed`，本增量自身不会写入 failure event。

## 5. Artifact 路径与 exact schema

所有 artifact 位于：

```text
runtime/ootang_epoch_registry_v1/workset_recovery_v1/
  item_intents/<step_id>.json
  external_anchor_dispatch.lock
  external_anchor_response_objects/<step_id>/<observation_sha256>.json
  external_anchor_response_links/<step_id>.json
  status.json
```

### 5.1 Content-addressed response observation

object filename 必须等于整个 canonical observation payload 的 SHA-256。payload exact keys 为：

```text
schema_version
profile_id
profile_sha256
step_id
key_id
item_intent
request_identity
outcome
transport_response
validated_response
failure
network_action_performed
remote_exactly_once
trusted_anchor_receipt_verified
e2_live_evidence_eligible
live_ledger_result_recorded
recovery_receipt_created
```

`item_intent` 使用 `{path, sha256, size_bytes}` reference；`request_identity` exact keys 为：

```text
request_event_entry_sha256
request_body_sha256
endpoint_sha256
idempotency_key
```

`transport_response` exact keys 为：

```text
status_code
media_type
charset
content_encoding
final_url
body_complete
observed_body_size_bytes
observed_body_sha256
observed_body_base64
```

body 以 base64 保留 observed bytes，并由 size 和 SHA-256 双重绑定。verification 会反解 bytes、
重建 transport record、重新执行 deterministic classification 和 frozen response validation，再比较
完整 canonical payload。observation 不写入本地 received time 或 RTT，避免这些非语义字段使 crash
replay 产生新的 authority bytes。

对于 `candidate_confirmed`：`validated_response` 为 frozen live normalization，`failure = null`。
对于 `deterministic_failure`：`validated_response = null`，`failure` 为上一节的 bounded record。
两种 outcome 均固定：

```text
network_action_performed = true
remote_exactly_once = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
live_ledger_result_recorded = false
recovery_receipt_created = false
```

### 5.2 Create-only response link

link path 由 step ID 唯一确定。exact keys 为：

```text
schema_version
profile_id
profile_sha256
step_id
key_id
item_intent
request_identity
response_observation
outcome
network_action_performed
remote_exactly_once
trusted_anchor_receipt_verified
e2_live_evidence_eligible
live_ledger_result_recorded
recovery_receipt_created
```

`response_observation` 是 object 的 `{path, sha256, size_bytes}` reference。link 不复制 body 或
validated response；每次消费必须通过 reference 深验 object，不能只相信 link 中的 outcome。

## 6. Crash recovery、并发与 namespace integrity

publication 顺序是 object 后 link，因此明确覆盖以下 crash windows：

1. **intent 后、network 前 crash**：没有 observation；下一 poll 使用同一 intent/key。
2. **request 发出后、完整 response 落盘前 crash**：delivery ambiguous；下一 poll 使用同一 key，
   不伪造 failure 或新 attempt。
3. **object durable、link 前 crash**：step-scoped object directory 中存在唯一 object；下一 poll 深验
   object 并 create-only 补建 link，返回 `external_anchor_response_forward_adopted`，零网络。
4. **link durable 后 crash**：下一 poll 深验 link/object，返回
   `waiting_for_anchor_result_adapter`，零网络。

同一 step object directory 只允许零个或一个 content-addressed JSON object；两个不同 object 会被
判为 branched observation 并阻断。存在 link 但没有 object 是 orphaned link；link bytes、object
bytes、filename digest、item-intent reference、request identity 或 profile binding 任一变化均阻断。
全局 namespace 还要求 response object/link 的 step IDs 必须属于现有 item intents，且所有 link
必须有对应 object set。

这一设计只能提供本机 dispatch 串行化和 durable crash-forward adoption。除非 provider 明确证明
它遵守 `Idempotency-Key` 的幂等 POST 或 query-by-key contract，request 发出后、本地落盘前的
remote exactly-once 仍不可证明。

## 7. Credential 与隐私边界

token 只在 dispatch lock 内、真正联网之前，从 item intent 冻结的 environment-variable 名称读取。
合法 token 必须非空、trimmed，且每个字符处于可打印 ASCII `0x21..0x7e`；否则机器等待且不联网。

token value 不进入 item intent、request identity、idempotency digest、response object、link、status、
exception text 或 CLI JSON。默认 transport 只在内存中构造 `Authorization` header。response body
若包含 token 的 exact ASCII bytes，系统在持久化前进入 generic integrity block，既不 redaction
也不落盘，从而避免把 credential reflection 固化进 recovery artifacts。

该边界不把 bearer token、HTTPS channel 或 provider JSON 声称为外部实现 trust anchor；它也不
验证 receipt 的密码学真实性。

## 8. Capability 与 false claims

本增量可以声称：

```text
live_anchor_result_response_observation_implemented = true
bounded HTTPS dispatch occurs outside the four coordinator locks
response objects and links are create-only and crash-forward adoptable
retryable/ambiguous attempts preserve the frozen idempotency key
network_action_performed is reported per poll
```

本增量仍明确不能声称：

```text
network_recovery_implemented
live_anchor_result_adapter_implemented
live_ledger_result_recorded
recovery_receipt_created
remote exactly-once
trusted_anchor_receipt_verified
e2_live_evidence_eligible
external implementation trust anchor
bounded workset recovery or terminal transition closure
lifecycle / transition / drain / activation authority
real activation readiness or formal warning output
```

## 9. 快速验证结果

本增量按风险只执行秒级 fake-transport/fixture 验证：

- focused recovery suite `30/30`，约 `0.18 s`。新增路径覆盖 success observation、token 不落盘、
  wrong-root deterministic failure、existing link 零网络、object-before-link forward adoption、
  retryable transport 零 artifact 和 missing token 零网络；
- recovery、live-ledger/CAS、workset inventory/manifest、admission-cut、eligibility、drain-v2 与 main
  相邻 suite `137/137`，约 `7.50 s`；
- Ruff check/format、Python compile、strict profile load/config SHA binding 和 `git diff --check` 通过。

测试没有访问真实 endpoint，没有模型训练、科研实验重跑、slow-socket deadline 实网试验、长时间并发
压力或大规模边界矩阵。no-redirect、fixed headers、total deadline 与 response `max+1` read 由默认
transport 的固定实现契约提供；本轮不把它们夸大为 provider 行为或 remote exactly-once 证据。
独立只读复核的初始 2 个 P1、2 个 P2（locked deep replay、HTTPError body timeout、transport
type boundary、alarm construction window）均已修正；最终复核为 P0/P1=0。

## 10. 历史下一步：result CAS adapter（现已实现）

下一增量应只消费已深验的 response link/object，不再次联网：

1. 重新取得 manager、cycle、replay、shadow 四重锁；
2. 复核 reservation generation、pending item intent、request event 和 exact expected result pre-head；
3. 对 `candidate_confirmed`，从 frozen request payload 与 `validated_response` 构造 exact
   `anchor_confirmed` EventSpec；
4. 对 `deterministic_failure`，构造 frozen `anchor_failed` EventSpec，固定
   `reason_code=request_or_receipt_validation_failed`、受控 `error_type` 和
   `retry_policy=automatic_next_poll`；
5. 通过 expected-pre-head CAS 创建 result event，或精确接管 crash 后已存在的同字节 event；
6. event durable 后才创建 branch-selected recovery step receipt 和 recovery event；
7. 如果 CAS 前 ledger head、manifest generation 或 request binding 已漂移，则保留 observation 并
   fail closed，不重发 network、不改接其他 attempt。

`anchor_result_recorded` 的 transition graph 后仍存在
`anchor_request_recorded` / `outcome_batch_settled` 两个允许 next actions。当前 result adapter 已按
deep-verified outcome 收窄为唯一分支；自动 failure retry 与剩余 confirmed-settlement 边界见
`docs/ootang_anchor_result_ledger_adapter_engineering.md`。
