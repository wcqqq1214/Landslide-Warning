# 藕塘 checkpoint/input runner-independent replay 工程协议

> 预注册日期：2026-08-26
> 状态：已实现；预注册定向验收通过，最终全仓计数见本文第 7 节
> 证据层级：机器前瞻顺序工程；不是 E2 live evidence
> 人工冻结 / 人工选日 / 人工批准：均不允许

## Material Passport

| 字段 | 预注册约束 |
| --- | --- |
| mode | `design + implement + adversarial validate` |
| upstream | 递归验证的 current source、不可变 activation source、固定五种子 bundle、E2-A 数学重放账本 |
| unit of verification | 一个尚未写入 live ledger 的自然日 issue batch |
| visible inputs | issue exact bytes、input manifest、canonical source 尾 7 日、outer/training manifests、seed 0--4 checkpoints |
| hidden / forbidden inputs | 同日 outcome、未来 source rows、人工日期、人工签字、人工冻结、Vajont |
| numerical contract | persistence 逐站精确相等；40 个 seed P50 仅用绝对容差 `1e-6 mm`，`rtol=0` |
| implementation diversity | verifier 独立实现 strict capture、manifest 解析、IDW、7-channel preprocessing、ConvLSTM forward 与 grid readout；不调用 producer/bundle 的核心重放函数 |
| automation | standalone verifier + runner-lock 内 verified-live gate + fixed-point cycle v3 |
| persistence | create-only replay receipt、seal intent、seal completion；全部绑定 exact bytes 与 live ledger 前后 head |
| evidence boundary | formal warning、E2 evidence、real activation、independent label、promotion 均固定为 `false` |
| remaining trust | 旧 live-v1 CLI 仍可绕过指定入口；本地 writer 未做身份认证；可信时间与自动 epoch registry 尚未实现 |

## 1. 问题与假设

当前 producer 会从五个安全 checkpoint 计算 P50，但 live v1 只验证 issue 与
input-manifest 的外层 artifact 引用，并不解析七日输入，也不重算 checkpoint。
因此 producer 自报的 scientific hash 只能证明内部自洽，不能证明预测来自受绑定的
checkpoint。

本增量只检验以下预注册假设：

1. 从递归验证的 canonical source 尾七日和五个受绑定 checkpoint 独立重算时，
   `8 stations x 5 seeds = 40` 个 P50 与 issue 的绝对差均不超过 `1e-6 mm`；
2. 任一 issue、input row、source/model/checkpoint artifact、station/expert order、
   persistence 或预测被改写时，系统在 live append 前 fail closed；
3. 同一输入重试保留第一次 durable receipt bytes，冲突语义不得覆盖；
4. `replay receipt -> seal intent -> live append -> seal completion` 的顺序在
   `runner.lock` 内成立，崩溃只能留下可验证的安全前缀；
5. cycle v3 的所有 live transition 均经 verified-live 指定入口，且 progress token
   包含 replay/intent/completion 的科学身份。

这些是假设与工程门禁，不是模型精度改善假设。该步骤不训练模型、不调整阈值、
不改冻结切分，也不从已查看结果选择 seed 或校准方法。

## 2. 独立性边界

允许共享的是上游 source authority、PyTorch/NumPy 基础算子和冻结 schema/config；
verifier 必须自己实现以下核心：稳定单次文件捕获、strict JSON、artifact 交叉绑定、
尾七日选择与比较、checkpoint tensor/schema 检验、IDW、归一化、ConvLSTM cell/head
forward、station readout、P50 反归一化与 live-order 映射。

禁止把以下调用作为 verifier 的核心证明：producer 的 input scientific hash、row 或
station helpers，以及 production bundle 的 bundle/checkpoint loader、input builder、
checkpoint predictor 或 five-seed predictor。若最终实现偏离此边界，能力字段必须降级，
不得仍声称 runner-independent implementation replay。

## 3. 因果提交顺序

对一个尚未 seal 的目标日，指定入口固定执行：

```text
recursive source + ledger pre-head verification
  -> independent checkpoint/input replay
  -> immutable replay receipt
  -> immutable seal intent (bind pre-head + exact issue + replay receipt)
  -> live-v1 issue append while runner.lock remains held
  -> immutable seal completion (bind resulting live seal entry)
```

Replay 和 intent 均不得读取同日 outcome。intent 后、live append 前崩溃只留下安全
orphan；live append 后、completion 前崩溃只能在证明 intent、exact issue 和实际追加
事件完全一致后恢复 completion，而且恢复 completion 先于任何 outcome 读取。没有
pre-existing intent 的 live-v1 seal 不得被事后追认为 guarded candidate。

## 4. 自动状态与失败语义

- source/model/issue 尚未到齐：机器 waiting，退出成功，等待 scheduler 下次轮询；
- runner 或 replay lock 被占用：busy，退出码 `3`；
- hash、schema、路径、数值、因果链或 receipt 冲突：blocked integrity，退出码 `2`；
- 预测超出固定数值容差：blocked integrity，不回退为 persistence，不重训；
- 不提供 target date、approve、freeze、force、backdate、manual signature 或人工恢复参数。

## 5. 预注册验证矩阵

验收至少覆盖：真实 safe-checkpoint 五种子 parity；duplicate-key/nonfinite/symlink/
path-escape；dataset 尾七日与 manifest 不一致；outer/training/checkpoint 绑定漂移；
persistence 与任一 seed 预测篡改；issue 在重放期间变更；并发锁；同语义重试；冲突
重试；intent 前/后及 live append 后注入崩溃；无 intent 的直接 v1 seal 不追认；
completion 与 live seal hash 交叉验证；不同机器时间下科学 token 不变；空 runtime
固定点等待；全仓测试、Ruff、compileall、JSON 与 diff-check。

## 6. 停止与解释规则

只有全部预注册测试通过，才能把“指定机器入口完成 runner-independent checkpoint/
input replay”记为 implemented。即使通过，也必须同时记录：旧 live-v1 入口仍可绕过，
ledger 仍是 trusted-writer hash chain，可信加密时间、自动 epoch registry/rotation 和
scheduler entry authorization 仍未闭门。因此 E2 live evidence、真实激活和正式预警
继续为 `false`。

## 7. 实现与验收结果

本预注册合同已由以下三个 additive、显式非默认模块实现，旧 live/cycle v1/v2
配置与账本合同未改写：

- `ootang_issue_replay.py`：递归调用共享 source authority 复验 historical base 与
  daily feed 原始 bytes/元数据，并从其 canonical dataset 重建 activation/current
  frame；独立复刻五项 normalization、IDW、七通道输入、ConvLSTM cell/head、station
  readout 与反归一化。公开 receipt loader 也重新加载五 checkpoint 并执行真实
  forward，不能只相信 receipt 内部的比较摘要。
- `ootang_verified_live.py`：长持原 live-v1 `runner.lock`，只按 replay receipt →
  pre-seal intent → live issue transaction → completion 前进；completion 在任何
  outcome/revision 读取前恢复。独立 intent/completion 校验同时复验原始 artifact
  link 与对应 live opened/sealed event。
- `ootang_prequential_cycle_v3.py`：精确绑定 cycle v2、replay v1 和 verified-live v1，
  以固定 13 阶段运行全部 source/bundle/live/outcome/issue/shadow 屏障。旧 live-v1
  CLI 仍可直接调用，因此这里只把 cycle v3 定义为指定机器入口，不宣称系统级不可
  绕过授权。

固定配置 SHA-256 为：

- issue replay v1：`c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5`；
- verified-live v1：`081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b`；
- cycle v3：`6852876db121027e82aedfb2b65c9cb1d9b40106b19c7068ba8764b317e1db24`。

对抗验收覆盖真实 safe-checkpoint 五种子 E2E、五项 normalization 与冻结 producer
逐 bit parity、40 项 forward、self-consistent prediction/digest 篡改、daily-feed
原始值与元数据自洽篡改、source tail/pre-head 漂移、duplicate/nonfinite JSON、artifact
escape/final symlink/pathname swap、同 inode 读中变化、runner/replay/cycle lock、
create-only 幂等、时钟回退/跨目标日、receipt-intent-completion 因果恢复、anchor-only
churn、跨 runtime 时钟/存储路径不变的科学 token，以及空 runtime 的真实 13 阶段
固定点。最终新增时钟与 pathname 对抗收口后，replay/verified-live/cycle-v3 分别为
`36/27/16`，联合 `79/79`；加
pipeline 为 `109/109`。最终全仓为 `715/715`（727.081 秒，0 failure / 0 error）。
Ruff、compileall、三份 JSON、`git diff --check` 全部通过；正式 v5 preflight
`23/23`，仍为 G0 PASS、G1--G4 BLOCKED、G5a 未授权。97 条保护路径聚合仍为
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。真实空
runtime 精确执行 13 阶段，一轮返回 `converged_waiting`，formal warning、E2 evidence、
real activation 与 promotion 均为 false。最终独立审计为 P0/P1 `0/0`。

progress token 保留对原始链的严格验证，但只投影科学语义：去除 receipt/intent/
completion 的观测时间、绝对存储路径、raw sequence/entry hash，并把 live pre-head
归一为过滤 anchor bookkeeping 后的科学前缀摘要。合法时间差、路径差和失败 anchor
重试不会伪造进展；issue/seal 科学 payload 的变化仍会改变 token。所有 runtime 最终
文件与锁均以不跟随 final symlink 的稳定 regular-file snapshot 读取；中间目录被具备
本地写权限的恶意进程并发替换仍属于明确记录的 trusted-local-writer 残余边界。

本增量没有训练、调参、选择 seed、改变校准阈值或重新计算预测性能，所以不会改善
也不能声称改善历史/未来 accuracy、coverage、interval score、FAR 或 recall。它提升
的是 issue 确实来自绑定 source、输入与 checkpoint 的可审计可信度。下一道机器门禁
是 pinned provider 的可信密码学时间；之后是 immutable epoch registry/自动 rotation、
scheduler entry authorization 和长链 O(N²) 扫描优化。

## 8. 最终 P2 边界与下一步

最终只读审计没有开放 P0/P1，但以下 P2 必须由后续机器协议显式接管，不能被当前
测试通过掩盖：

1. 当前按 final component 做 `O_NOFOLLOW` 与 inode/pathname fence；恶意本地 writer
   并发替换中间父目录、以相同 bytes 替换 inode，或落在精确 receipt read→unlink
   窗口，仍超出本增量边界。完全关闭需 dirfd/openat 逐段 walk 并持有目录 fd/inode。
2. 本机时间只有单调/目标日前因果 fence，不是密码学可信时间；SIGKILL 落在 receipt
   link→post-fence 或最终采样→SQLite seal 的极窄窗口，可能留下后续 fail-closed、需
   epoch manager 接管的安全前缀。跨 JSON/SQLite 提交也不是单数据库原子事务。
3. 旧 live-v1/cycle CLI 和外部 scheduler 尚未做不可绕过授权；cycle-v3 是指定安全
   入口，但错误调度仍可能旁路。无 intent 的直接 seal 只会被拒绝，不会被追认。
4. receipt/intent 绑定当前 verifier、依赖与模型；代码/环境/model epoch 升级后历史
   扫描会阻断，必须先有 immutable registry、预构建和自动 rotation。
5. progress 对历史 receipt 做完整递归重放和五 checkpoint forward；长期运行会产生
   O(N²) 级可用性压力。需要链绑定 authenticated snapshot + tail replay，并保留周期
   性 genesis 全审计等价测试。
6. checkpoint archive 原始 bytes 有 64 MiB 上限且 `weights_only=True`，但恶意本地
   artifact writer 仍可能利用解压后的 tensor storage 制造内存峰值；需隔离进程、
   resource limit 或先验安全格式。
7. standalone guard progress 不持 runner lock；cycle 的 base/replay 前后 fence 能覆盖
   正常授权 writer，但旧入口仍可旁路时可能出现一次短暂陈旧 token。scheduler
   authorization 关闭旁路后才能彻底缩小这一窗口。

推荐下一提交只实现可信密码学时间适配层：固定 provider/trust root/签名算法、canonical
request、最大偏差、离线/回滚策略和 immutable receipt；先以 additive shadow 验证，
不得直接把 `trusted_anchor_receipt_verified`、E2 evidence 或 real activation 置为 true。
