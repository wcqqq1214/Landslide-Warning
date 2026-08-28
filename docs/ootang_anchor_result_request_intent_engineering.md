# 藕塘 machine-only anchor result request intent 工程说明

## 1. 范围与结论

本切片只为 `anchor_request_recorded -> anchor_result_recorded` 建立外部请求之前的
machine-only authority。它在 recovery coordinator 持有 manager、cycle、replay 和 shadow
四重锁、且 manifest generation 与 live-ledger head 均稳定时，为唯一、紧邻当前 ledger tip
的 canonical `anchor_requested` event 创建 create-only item intent。

本切片**尚未执行** `anchor_result_recorded`：不发出 HTTP/TSA 请求，不观察或持久化网络
response，不验证 provider receipt，不调用 live-ledger result CAS，也不生成 branch-selected
recovery receipt/event。创建 intent 后，系统只进入可重复轮询的 machine waiting；不存在人工
freeze、cleanup、approval、force、backdate 或手工补录入口。

本切片不修改 ConvLSTM、v4 默认链、冻结训练/验证/测试切分、指标、阈值、模型参数或实验
结论。它只扩展旧 epoch recovery 控制面，为后续解锁运输层提供不可重解释的请求合同。

## 2. Canonical request authority

coordinator 完整验证 manifest frozen authority、当前 live chain 和已有 recovery artifacts 后，
只接受一个满足以下条件的 result request 候选：

- event type 必须是冻结 live writer 产生的 canonical `anchor_requested`；
- request 必须属于 manifest item 绑定的 live epoch、target、issue、seal 和 attempt；
- request 必须是当前 ledger terminal event，即 expected result pre-head；
- 对同一 seal 不得存在已配对 result、第二个 unmatched request 或任何合法/非法 suffix；
- event key、sequence、predecessor hash、entry hash 和 canonical payload 必须彼此一致；
- stored item intent 必须能重新验证为同一 event、同一 manifest generation 和同一 ledger head。

因此，本切片不从“当前仍 unmatched 的任意历史 request”猜测工作，也不在 head 变化后把 intent
迁移到新的 attempt。candidate 不唯一、request 不在 tip、chain/schema drift、manifest generation
变化或 authority 不匹配均 fail closed，且不会创建 intent。

## 3. Create-only item intent

result item intent 的 versioned action contract 冻结后续 transport 与 ledger commit 所需的最小
authority，至少包括：

- 完整 request-event identity：event key/type、sequence、predecessor/entry hash、live epoch、
  target、seal 与 attempt；
- expected result pre-head，且必须精确等于该 `anchor_requested` terminal event；
- 经过严格验证和 normalization 的 exact HTTPS endpoint；
- canonical POST body 及其 SHA-256，而不是运行时重新拼装的等价 JSON；
- 从冻结 request identity/body 派生的 stable idempotency key；
- manifest、reservation、step 和 implementation provenance。

intent 只能以 create-only 方式写入。相同 bytes 可以被机器幂等接管；同一路径下任何字段变化、
重算出的 endpoint/body/hash/key 变化或 foreign artifact 都会阻断，不能覆写或静默升级。该规则
使“intent commit 后进程崩溃”收敛为同一个 pending request，而不是生成第二个外部操作。

endpoint 配置是创建该 authority 的必要输入。endpoint 缺失，或无法通过 exact normalized HTTPS
校验时，coordinator 返回 machine waiting 且**不创建** result item intent。系统不会把缺失/非法
endpoint 固化成一次 `anchor_failed`，因为当前切片没有发出外部请求，也没有足够事实声称一次
请求失败。

认证 token 不属于可重放 authority，不读取进 intent，也不得出现在文件、日志、status、hash
输入或异常文本中。后续 transport 只能在真正 dispatch 时从进程环境读取 token。

## 4. Global fence 与零网络语义

一旦 result item intent 存在但尚无 branch-selected receipt，它就是全局 pending fence。后续 poll
只能验证并报告同一 intent 的 machine-wait 状态，不得选择其他 item、推进同一 key 的其他 branch、
追加新的 anchor attempt 或越过该请求修改 live ledger。

当前切片在四重锁内只完成 authority selection 与 create-only intent commit。它不会在锁内调用
socket、DNS、HTTP client 或 provider SDK；已有 pending intent 的轮询同样保持零网络。这样既避免
长网络等待占用四重锁，也防止外部响应到达时内部 generation/head 已悄然变化。

本切片的持久事实只能表述为：机器已经冻结“将要发送什么、发往哪里、以哪个幂等 identity
发送，以及 result 最多能接在哪个 pre-head 后”。它不能表述为请求已发送、provider 已接受、
receipt 已验证或 ledger result 已记录。

## 5. 尚未实现的 unlocked transport

下一切片应在不持有上述四重 recovery 锁时处理 transport，并继续使用本 intent 的 exact endpoint、
canonical body 和 stable idempotency key。安全的后续顺序应为：

1. 在独立的 request fence 下读取并验证 pending intent；
2. dispatch exact HTTPS POST，且重试始终复用同一 idempotency key；
3. 将 response/failure observation 作为 create-only、content-addressed artifact 持久化；
4. 重新取得四重锁，复核 manifest generation、request event 和 expected result pre-head；
5. 根据冻结 live writer 语义构建 `anchor_confirmed` 或 `anchor_failed` EventSpec；
6. 通过 expected-pre-head CAS 创建或精确接管 result event；
7. 最后创建 branch-selected receipt/event，才允许 transition graph 继续推进。

网络 timeout 或连接中断可能发生在“远端已接受、客户端尚未观察 response”之后。除非 provider
明确支持该 stable key 的幂等 POST 或按 key 查询，系统无法证明远端 exactly-once。因此当前以及
下一切片都不得声称 external exactly-once；遇到 ambiguous outcome 时必须保留同一 pending intent，
不能换 key、追加 failure result 或自动生成新 attempt。

response schema/interface validation 也不等于可信时间或加密证明。只有后续实现并验证明确的
provider trust/cryptographic contract 后，才可能提升相关 evidence claim；当前 external anchor
仍是 interface-only boundary。

## 6. Capability 边界与下一步

本切片可以声称：

```text
anchor result request intent is create-only and reproducible
canonical request endpoint/body/hash/idempotency identity is frozen
pending result intent globally fences recovery progress
network action performed = false
```

本切片不能声称：

```text
anchor_result_recorded
external request dispatched or observed
network recovery implemented
response/receipt verified
live-ledger result CAS implemented
remote exactly-once
trusted timestamp or E2 evidence
branch-selected recovery receipt
terminal/transitive recovery closure
```

实现落在 recovery profile v1 的 `1.4.0-anchor-result-request-intent` 版本，相关
intent/item-intent/receipt/status authority 升为 v4。定向 recovery 测试 `23/23`（约
0.17 s），相邻 live-ledger/CAS/manifest/admission-cut/drain/main 七模块 `117/117`（约
1.8 s）；Ruff check/format、compile、strict profile load 与 diff check 均通过。测试只使用
本地 fake/fixture，不执行真实网络、训练或科研实验。

下一步只需实现最小的 unlocked transport 与 create-only response observation，再单独实现重新加锁后的
result CAS/receipt。两步均应使用纯本地 fake transport 和精确 crash-window 测试完成快速验证；无需
训练模型、重跑冻结科研实验或扩大边界测试矩阵。
