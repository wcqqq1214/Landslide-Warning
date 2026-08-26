# 藕塘 RFC 3161 可信时间影子门工程说明

> 日期：2026-08-26
> 协议：`ootang-trusted-time-shadow-v1`
> 范围：密码学时间能力验证，非正式预警、非 E2 证据激活

## 1. 目标与边界

本增量为 replay-gated verified-live issue seal 增加一个**独立、显式、非默认**的
RFC 3161 时间戳影子门。机器自动把已完成的
`replay receipt -> pre-seal intent -> issue seal -> completion` 科学身份编码为规范消息，
向固定 TSA 请求签名时间戳，再从不可变原始响应重做证书链、签名、消息、策略、nonce
与时间上界验证。

它只回答“固定科学语义在某一外部 TSA 声称的时间之前已经存在，并且回执由固定 TSA
密钥签发”这一工程问题。它不改模型、不训练、不调参、不选择校准器、不读取 outcome
inbox，也不提高 accuracy、coverage、interval score、FAR 或 recall。旧 JSON anchor
仍不是密码学时间证据；本影子门也永久保持：

```text
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
real_activation_ready = false
formal_warning_output = false
```

因此，即使 RFC 3161 回执验证成功，也不能自动解锁 E2、正式预警、方法选择或晋升。
后续必须另行实现 immutable epoch registry/自动 rotation 和不可绕过的 scheduler entry
authorization。

## 2. 为什么采用 RFC 3161

[RFC 3161](https://www.rfc-editor.org/rfc/rfc3161.html) 是标准轨的 PKI
Time-Stamp Protocol：响应把消息摘要、hash OID、策略、nonce、`genTime`、可选
`accuracy` 与 TSA 签名绑定；客户端必须检查响应状态、所请求消息、证书身份和签名。
[RFC 5816](https://www.rfc-editor.org/rfc/rfc5816.html) 将签名证书绑定更新为
ESSCertIDv2，避免继续依赖 SHA-1 标识。

本实现选择 Sigstore production TSA，是因为其公开实现明确提供 RFC 3161/5816
服务并发布可版本化的信任材料。Roughtime 仍是 Internet-Draft，Rekor 本身证明的是
透明日志包含关系而不是本项目当前所需的独立 TSA 回执，故本版本不把二者混入同一
验收门。

这里必须保留一个重要证据边界：有效 CMS 签名证明“固定 TSA 对该 `genTime` 和
`accuracy` 作出了签名声明”，并不独立证明其上游 UTC 时源绝对正确。TLS 也只保护
传输；证据可信性来自持久化的 CMS、固定证书和离线可重复验证。若未来要求降低单一
运营方信任，应另建多运营方、预声明 quorum，而不能事后挑选最有利时间。

## 3. 冻结的 provider 与依赖

| 项目 | v1 固定值 |
|---|---|
| endpoint | `https://timestamp.sigstore.dev/api/v1/timestamp` |
| policy OID | `1.3.6.1.4.1.57264.2` |
| message hash | SHA-256，OID `2.16.840.1.101.3.4.2.1` |
| nonce | 每个新 target 由机器生成 256-bit 正整数 |
| cert request | `true` |
| accepted PKI status | 仅 `granted (0)` |
| maximum response | 65,536 bytes |
| maximum monotonic RTT | 10 seconds |
| required accuracy | 必须存在，且保守总量不超过 1 second |
| provider epoch lower bound | `2025-07-04T00:00:00Z` |
| target-day timezone | versioned fixed `Asia/Shanghai = UTC+08:00`（不读取 host tzdb） |
| verifier | 精确 `rfc3161-client==1.0.8` |
| runtime | `uv 0.12.5`、精确 CPython `3.10.20`、`--isolated --frozen`、`python -I` |
| trust source commit | `sigstore/root-signing@ba3066c420970c13772ba0625f09f1ec97193116` |
| leaf DER SHA-256 | `85f927bc07ab62cac3b44356c10efc81b2c6883fda7ab9e6d870d9d13acd05b7` |
| root DER SHA-256 | `2aca8fea5d3ce48b01cc77076293c280e6c23ffe44034757ee7833ca9f45d633` |

公开 trust manifest 还固定上游 JSON、PEM 和 DER 的 SHA-256；manifest 和四个
PEM/DER 指纹同时硬编码在 core，协调替换 config、manifest 与自签 TSA 也会 fail
closed。加载器要求它们是稳定 regular file，拒绝 symlink、路径逃逸与哈希漂移，
并显式验证 leaf 由 root 签发及 root self-signature。

密码学依赖没有加入根项目：旧 `pyproject.toml`/`uv.lock` 精确保持
`bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0`/
`f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c`，从而不破坏
已有 calibration/replay profile 的 hash binding。stdlib launcher 在进入第三方代码前
验证 core、隔离子项目和子锁哈希，清除 `PYTHON*`、`UV_*`、`VIRTUAL_ENV`，再以
`uv --no-config run --isolated --frozen --python 3.10.20 python -I` 启动。每轮使用
临时隔离环境，避免持久 `.venv` 中同版本包被篡改后仅靠 metadata version 混过；
lock 中 wheel/sdist 哈希仍由 uv 验证。

最终 bootstrap SHA-256：

| artifact | SHA-256 |
|---|---|
| launcher | `92a0a755881f549b272bfbd09d11b2590e06e9ecb06409421f6bd18e261b1f1b` |
| core | `797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7` |
| isolated `pyproject.toml` | `236606b46ed945fbbce46868a1a8ab5aac9a1131f39352f324e95a6001b59625` |
| isolated `uv.lock` | `aebfc5d498735f694572ee8b53c328da5fa66a84da05d202605a2500e8b78f93` |
| trusted-time profile | `2d804c887c33e1038f8089de99b08ae65596a02a201eea5aba3ea9034f664b63` |

第三方库的 v1.0.8 请求 builder 只能自行生成约 64-bit nonce，且不能写 `reqPolicy`。
本项目没有伪称它具备更强能力，而是用一个极小的严格 DER encoder 生成
`TimeStampReq`：version 1、SHA-256 AlgorithmIdentifier、32-byte imprint、固定 policy、
256-bit positive nonce 和 `certReq=true`；第三方库仅用于解析与验证响应。静态真实 TSA
fixture 会同时验证这一请求结构和完整离线响应路径。

## 4. 请求绑定与因果条件

只有同一个 runner lock 下的公开 verified-live projection 证明当前 outstanding seal
已存在对应 guard completion，机器才会建立请求。域分离 canonical JSON 至少绑定：

- trusted-time profile/config 与绑定 live/replay/verified-live profile 的 SHA-256；
- live epoch、target date、seal sequence 和原始 seal entry hash；
- guard completion 的确定性科学语义 hash；
- issue/input/model/context 等上游科学身份；
- request schema 与 trust epoch；
- 随机 nonce 不进入被时间戳消息，而由 RFC 3161 request/response 精确回显防重放。

本模块绝不读取 outcome inbox。机器不能用“已经看到 actual”来决定请求哪个 target，
也没有 target-date、freeze、approve、force、backdate 或人工签名参数。

验收时间使用 RFC 3161 的保守上界：

```text
trusted_upper_bound = genTime + accuracy
causality_before_target = trusted_upper_bound < frozen UTC+08:00 target-day start
```

缺失 accuracy 不按零处理。当前本机 wall clock 只作为 advisory 记录；是否早于 target
完全由签名的 `genTime + accuracy` 决定。HTTP 往返只用 monotonic clock 限制，避免系统
wall-clock 回退篡改超时判定。

## 5. 严格响应验证

每次公开 load 都从 request、raw TSR object、target response link 和 receipt 重新读取
并执行完整密码学验证，不信任 receipt 中的派生布尔值。至少要求：

1. DER 可唯一解析，响应大小和媒体类型符合固定合同；
2. PKIStatus 精确为 `granted`，拒绝 `grantedWithMods` 和全部错误/等待状态；
3. TSTInfo v1、SHA-256 imprint、固定 policy 和 256-bit nonce 精确匹配请求；
4. 只有一个 signer，嵌入证书精确等于 pinned leaf，TSTInfo TSA DirectoryName 精确
   等于 leaf subject；
5. leaf/root DER fingerprint、leaf→root/root self-signature、CMS 签名/证书路径和
   `genTime` 时证书有效期全部通过；
6. leaf 的唯一 EKU 是 critical `id-kp-timeStamping`；
7. `genTime` 为 UTC、在 provider epoch 下界之后，accuracy 存在且不超过 1 秒；
8. 从完整 live ledger 与 verified-live guard history 重建 target 的 canonical
   envelope，再重算 message imprint；
9. receipt 的所有派生字段与重算结果逐项一致。

`rfc3161-client` 当前没有公开 ESSCertIDv2 字段验证 API；本版本没有另写一个未经充分
审计的 CMS ASN.1 parser，因此不声称单独验证 RFC 5816 `signingCertificateV2`
attribute。精确 embedded-leaf pin、SignerInfo/CMS 验证和 leaf/root 签名关系限制了
证书替换面，但这是保留的已知 P2，不得在论文中写成“独立完整验证 ESSCertIDv2”。

任何 request、TSQ、TSR、link、receipt、trust material 或配置的篡改均返回
`blocked_integrity/exit 2`。socket timeout 外还有进程内 total transport deadline；
联网前会读取当前线程的信号掩码，若无法读取或发现用于 deadline 的 `SIGALRM` 已被
继承屏蔽，则在发送请求前 fail closed，避免 slow-drip 无限占有共享 runner lock。
网络不可达、timeout 或服务临时错误是
`waiting_for_timestamp_service/exit 0`，由调度器自动重试，不要求人工冻结或清理；锁忙
为 `busy/exit 3`。

## 6. 不可变持久化与崩溃恢复

完整 runtime root/namespace 映射由 core 精确固定，配置不能把状态或锁重定向到另一
目录。运行根与 live v1 共用 runner lock，并在持锁前后校验 regular file 与 pathname/open
inode 身份，拒绝锁替换导致的双持有者。独立 namespace 中 request JSON/TSQ 先
create-only 持久化，TSR 按 bytes SHA-256 写 content-addressed object，再写 target
response link，最后写派生 receipt/status。所有 final artifact 拒绝 symlink 和同名
异字节覆盖。

若进程在 object、link 或 receipt 任一边界崩溃，下一 poll 只从已验证的安全前缀恢复：
已有 link 时重验 raw TSR 并补齐 receipt；只有无害 orphan object 时可重新请求并建立
新的唯一 link。相同 target、相同科学语义和已有有效回执幂等返回；相同 target 出现
不同 request 语义、nonce、link 或 receipt 时 fail closed。

## 7. 自动状态机与使用方式

阶段是显式非默认入口，缺少 activation、outstanding seal 或 guard completion 时返回
自动 waiting，且不访问网络：

```bash
uv run python main.py --stage ootang-trusted-time-shadow
```

成功回执只允许本影子 namespace 报告：

```text
rfc3161_receipt_verified = true
cryptographic_time_shadow_verified = true
```

它仍不能改变旧 `trusted_anchor_receipt_verified` 或任何 E2/formal 标志。当前版本也不
插入 cycle v3；这能避免改变已经 hash-bound 的 13-stage 历史合同。后续若要成为新的
designated entry，必须新增 cycle v4/profile，而不是原地改写 v3。

## 8. 验证与剩余风险

公开 dummy message `ootang-custom-rfc3161-probe-v1` 的真实 production 请求/响应
分别为 105/1,287 bytes，SHA-256 为
`bdc94a42cd34ba1a947c521b19553edea9a7699c621c8ff66ba4458382acbfa3`/
`4535d7ddc291159db625a9b68b403d5db14a8544c3be102604377a4a7d317afc`；签名
`genTime=2026-08-26T12:53:46Z`、accuracy=1 second，并对 2026-08-27 固定
UTC+08:00 边界返回 causality true。fixture 只是工程互操作证据，不是藕塘科学证据，
也不把历史 `genTime` 追认成真实 issue 时间。

当前验证记录：

```text
isolated core contracts: 22/22
launcher + pipeline focused: 39/39
full repository: 724/724 (345.293 seconds; 0 failure / 0 error)
ruff / format / compileall / strict JSON / diff-check: pass (JSON 3/3)
root + isolated uv lock checks: pass
formal-v5 preflight: 23/23; G0 PASS, G1--G4 BLOCKED, G5a unauthorized
protected paths: 97/97; aggregate 6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3
independent audit: P0/P1/P2 = 0/0/1
```

最终独立只读审计确认无开放 P0/P1；唯一审计分级 P2 是未单独解析 ESSCertIDv2。
其余仍未关闭的协议边界包括：单一 TSA 运营方的正确时源假设、
签发后撤销状态的长期归档、
旧 live/cycle CLI 可绕过 designated entry、代码/模型升级缺少自动 epoch rotation，
以及长链 O(N²) 重放成本。这些必须由后续独立机器协议处理。

## 9. 主要依据

- Adams et al., [RFC 3161: Internet X.509 PKI Time-Stamp Protocol](https://www.rfc-editor.org/rfc/rfc3161.html).
- Santesson et al., [RFC 5816: ESSCertIDv2 Update for RFC 3161](https://www.rfc-editor.org/rfc/rfc5816.html).
- Sigstore, [RFC3161 Timestamp Authority](https://github.com/sigstore/timestamp-authority).
- Sigstore, [versioned root-signing trust material](https://github.com/sigstore/root-signing/blob/ba3066c420970c13772ba0625f09f1ec97193116/targets/trusted_root.json).
- Trail of Bits, [rfc3161-client](https://github.com/trailofbits/rfc3161-client).
