# 藕塘 E2-B 机器部署与签发工程说明

> 建立日期：2026-08-26
> 状态：`e2b_engineering_only_not_live_evidence`
> 配置：`config/ootang_prequential_deploy.v1.json`
> 配置 SHA-256：`60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940`
> 正式预警输出：`false`

## 1. 当前完成范围

E2-B 第一增量已经把 E2-A 之前缺少的三项机器能力接到同一显式管线：

1. 校验每日 finalized feed，在可信代码内生成模型特征并物化内容寻址 source；
2. 从 immutable activation source 训练固定 seeds `0..4` 的生产 checkpoint bundle；
3. 从五个 checkpoint 内部重放 P50，自动签发 ledger 要求的下一自然日 issue。

这条路径没有人工挑日期、人工冻结样本、人工选 seed、人工批准模型或人工签发。
每个脚本执行一次 poll 后退出，可由系统调度器或数据到达事件重复调用。缺少输入
是可观测等待态；存在但不符合合同的输入是 fail-closed 错误，系统不会用历史 OOF
行、旧 `models/convlstm.pt`、常数或 persistence-only 结果冒充五种子预测。

E2-B1 仍是工程基础设施，不是 E2 live evidence，也不是正式预警。以下门保持关闭：

```json
{
  "runner_independent_checkpoint_inference_replayed": false,
  "trusted_anchor_receipt_verified": false,
  "automatic_epoch_rotation_implemented": false,
  "e2_live_evidence_eligible": false,
  "real_activation_ready": false
}
```

配置中的 `*_implemented=true` 只表示代码能力存在。运行状态另用
`source_manifest_semantics_verified_by_producer`、
`safe_checkpoint_loading_exercised` 和
`producer_checkpoint_inference_replayed` 表示该次 poll 是否实际执行成功；等待态
不得把“已实现”误写成“已完成”。

后续 E2-B2 已增加 source pointer v2/receipt 链加固、machine-only outcome
materializer 和 fixed-point cycle。它们完成机器物化与调度，但不改变上述
evidence/activation 门禁；详见 `docs/ootang_prequential_cycle_engineering.md`。
其后的 calibration shadow v1/cycle v2 已把三种固定校准器接入独立机器
issue/reveal 账本；同样不改变这些门禁，详见
`docs/ootang_prequential_calibration_shadow_engineering.md`。

再后的 additive replay gate 已为**指定机器入口**关闭 producer 自证问题：独立
verifier 从 immutable activation dataset 重算五组 normalization，从 current source
尾七日重建 7 通道输入，并用独立 checkpoint loader、ConvLSTM forward、IDW 和
readout 核对全部 `8 x 5 = 40` 个 P50；verified-live 再把 receipt、pre-seal intent、
本 v1 live transaction 和 completion 交叉链接。上面的 deploy-v1 配置与状态字段
保留历史语义，不能据此把 deploy-v1 本身误写成 runner-independent。cycle v3 是当前
指定自动入口；旧 live-v1 CLI 仍可绕过，可信时间、自动 epoch rotation 和 E2 evidence
继续为 false。详见 `docs/ootang_checkpoint_input_replay_engineering.md`。

## 2. 机器数据流

```text
incoming/daily_finalized_feed.json
  -> ootang-live-source
  -> immutable content objects
  -> per-day revision receipts + global snapshot receipt chain
  -> source_current v2 + one-time activation source

activation source
  -> ootang-production-bundle
  -> five safe checkpoints + training manifest
  -> model_bundle/manifest.json

source_current + activation source + model bundle + E2-A ledger status
  -> ootang-issue-producer
  -> exact issue object + issue_receipts/YYYY-MM-DD.json
  -> issue_inbox/YYYY-MM-DD.json

issue inbox + outcome inbox
  -> ootang-prequential-live
  -> append-only ledger + online state/status

verified current source + ledger projection
  -> ootang-outcome-materializer
  -> exact outcome object + revision receipt chain
  -> active receipt pointer + outcome inbox

all producers/runner above
  -> ootang-prequential-cycle
  -> bounded fixed-point scheduling + continuation status

exact issue + current/activation source + five checkpoints
  -> ootang-issue-replay
  -> immutable replay receipt
  -> ootang-verified-live
  -> pre-seal intent + live-v1 issue transaction + completion
  -> ootang-prequential-cycle-v3 (13-stage designated machine entrypoint)
```

显式运行命令为：

```bash
uv run python main.py \
  --stage ootang-live-source \
  --stage ootang-production-bundle \
  --stage ootang-issue-producer \
  --stage ootang-prequential-live

uv run python main.py --stage ootang-prequential-cycle

uv run python main.py --stage ootang-issue-replay
uv run python main.py --stage ootang-verified-live
uv run python main.py --stage ootang-prequential-cycle-v3
```

这些机器阶段不属于默认链。默认链仍严格是
`features → convlstm → ootang-operational-v4`。

## 3. finalized feed 合同

feed 是 2020-06-30 之后的严格日连续 JSON 扩展。`records` 必须从 2020-07-01
开始逐日覆盖到本次 watermark，不能只发送最新一天。它只接受原始日降雨、水库
水位和八站累计位移；不接受外部提供的 `RWL_rate` 或雨量窗口。下面是单条记录的
字段示意；生产 feed 必须把相同结构连续填满 `2020-07-01..watermark`：

```json
{
  "schema_version": "ootang_daily_finalized_feed_v1",
  "outcome_source_id": "machine-readable-source-id",
  "exported_at_utc": "2020-07-01T15:00:00Z",
  "records": [
    {
      "schema_version": "ootang_daily_finalized_record_v1",
      "date": "2020-07-01",
      "revision_id": "upstream-immutable-revision-id",
      "observed_at_utc": "2020-07-01T04:00:00Z",
      "available_at_utc": "2020-07-01T05:00:00Z",
      "finalized_at_utc": "2020-07-01T06:00:00Z",
      "finalized": true,
      "rainfall_mm": 0.0,
      "reservoir_water_level_m": 150.0,
      "displacement_mm": {
        "ATU1": 0.0,
        "ATU2": 0.0,
        "ATU3": 0.0,
        "ATU4": 0.0,
        "ATU5": 0.0,
        "MJ1": 0.0,
        "MJ3": 0.0,
        "MJ9": 0.0
      }
    }
  ]
}
```

producer 拒绝 duplicate JSON keys、NaN/Infinity、缺站、额外站、重复或断裂日期、
负降雨、非有限观测、时间倒序、未 finalized 记录和在对应下一自然日开始之后才
finalized 的记录。它把每日 revision 与 observed/available/finalized 时间一并写入
不可变 feed object，并在可信代码内重算：

- `RWL_rate`：按真实相邻日间隔的一阶差分；
- `Rain_cum7/15/30`：包含当日的滚动和；
- 模型位移列：严格按 `MJ9,MJ1,MJ3,ATU1..ATU5` 排列。

历史底座固定为 `data/monitoring_data.csv` 的 1,461 行和 SHA-256；station geometry、
E2-A profile、部署 profile 与派生 schema 同样受哈希或精确字段合同约束。

`source_current.json` 可以随新 finalized 日推进；
`source_snapshot/manifest.json` 是该 epoch 第一次合法 ingest 时的 immutable activation
快照，只允许原子创建一次，绝不被后续 feed 覆盖。语义完全相同而只有 export 时间
变化的重复投递保持原 pointer 和 manifest 字节不变。

E2-B2 将 current pointer 升级为 `ootang_source_current_pointer_v2`。每个目标日的
revision 使用 `ootang_source_revision_receipt_v1` predecessor/sequence 链记录，每个
全局 snapshot 另写入内容寻址 `ootang_source_snapshot_receipt_v1` 链。全局链带
`snapshot_sequence_id`、predecessor 和唯一 tip，current pointer 必须精确绑定该 tip。
进程在 receipt 已提交但 pointer 尚未发布时崩溃，下一 poll 可从递归验证的唯一
tip 恢复缺失/陈旧 pointer；r1→r2→r1 回退、分支、孤儿、重复 sequence、非 tip
绑定或 object/receipt 篡改均 fail closed。

source ingest 遇到配置内错误时写 `blocked_integrity`；未预期的 I/O、pandas/CSV 或
竞态异常会被规范化为 `SourceIntegrityError`，并 best-effort 原子刷新 blocked
状态，避免旧 `ready` 在失败后继续误导机器调度器。

除 `runtime.root` 外的运行路径必须是受 root 约束的相对路径；解析后的目标及其
symlink 父路径都不能逃逸 runtime root。`source_current.json` 中引用的 activation
还必须与配置位置上的不可变 activation 在 path、SHA、size 和完整 source 语义上
一致，不能通过伪造 pointer 注入另一份自洽但错误的 activation。

## 4. 五种子生产 bundle

模型只从 immutable activation source 训练，不从后来推进的 `source_current` 重训。
固定合同为：

- seeds `0,1,2,3,4` 全部保留，不选择 best seed；
- 7 日 lookback、1 日 horizon、7 通道、P10/P50/P90、120 epochs；
- 使用 activation watermark 之前所有可用窗口，不用 holdout 选择 epoch 或 seed；
- CPU、单线程、确定性算法、MKLDNN disabled；
- checkpoint 只含 tensor 和 primitive，加载固定使用
  `torch.load(..., weights_only=True)`；loader 只读取 checkpoint 一次，对同一份受限
  bytes 同时做 size/SHA 校验和 `BytesIO` 反序列化，路径替换不能制造 hash/model
  不一致；
- normalization、elevation、IDW/readout、网络 state、source bindings、schema 和形状
  均递归验证；
- training manifest 绑定 deploy profile、基础模型、bundle producer、`pyproject.toml`、
  `uv.lock`、PyTorch 与 NumPy 版本；
- checkpoint、training manifest 和 outer bundle 都使用内容寻址和原子 no-clobber
  发布；同语义复跑幂等，冲突语义拒绝。

保存前后的 tensor 必须精确一致。由于 CPU 卷积后端在“训练后内存模型”和
“从 checkpoint 重建模型”的完整 source 推理上观察到亚微米级数值路径差异，
manifest 显式保存并验证 `1e-6 mm` 的 reload-inference 绝对容差；这不是预测精度
容差，也不能放宽 tensor、hash 或 schema 的精确校验。

模型创建时间必须不早于 activation capture、不晚于机器当前时间，并严格早于首个
目标自然日开始。若五个 120-epoch 拟合无法在该窗口内完成，当前实现会 fail closed，
不会回填过去目标；解决方式是后续机器 epoch manager 的预构建/原子轮换，不是人工
改时间或手工冻结。outer manifest 提交前和原子持久化后都会重新读取机器 UTC；跨越
首个目标日起点的构建不能宣布成功。

最终 outer manifest 的“提交前时钟 → 原子 link → 提交后时钟 → 必要撤销”短窗口
额外持有 E2-A `runner.lock`；120-epoch 训练本身不占用该锁。这样即使 post-write
时钟判定越界，runner 也不可能在撤销前读到短暂出现的 manifest。锁竞争采用非阻塞
busy，锁顺序固定为 deploy 后 runner。

source、bundle 和 issue 共用非阻塞 `deploy_cycle.lock`。竞争时返回 `busy/exit 3`，
不覆盖已有状态或创建半成品；bundle 不再无限等待另一进程释放锁。

## 5. 自动 issue producer

producer 每次递归重载 current source、activation source、outer model manifest、
training manifest 和五个 checkpoint，再用 current source 的最后 7 行内部推理。
模型站点顺序与 live 顺序的映射显式固定；issue 专家顺序为 persistence 后接
seed0--4 P50。

目标日期不是由人选择。ledger 存在时，producer 先持有 E2-A 的 `runner.lock`，通过
公开只读 `load_verified_ledger_projection()` 以 SQLite `mode=ro/query_only` 完整
验证 schema、哈希链并重放科学状态，再逐字段核对 status 的 event count、terminal
sequence/hash、epoch、last/next/outstanding、source 与 model；锁一直持有到 issue 和
producer status 完成发布。损坏 ledger、伪造/陈旧 status 或并发 runner 都不能通过。

日期规则为：

- 无 ledger 时只能签发 `activation_watermark + 1`，且 current watermark 不能已经
  越过 activation watermark；
- 有 ledger 时只能签发 `status.next_target_date`，它必须严格等于
  `current_source_watermark + 1`；
- current watermark 最后一行八站位移必须逐站精确等于 ledger verified projection
  的 latest displacement；无 ledger 时则必须等于 immutable activation 的 latest。
  同 watermark revision 尚未先进入 ledger 时自动等待，不能生成 consumer 必拒的
  persistence；
- 已有 outstanding issue 时等待，不能跳日或并行签发第二天；
- source as-of、机器生成时间必须早于目标日开始，且 source as-of 不能在机器未来。

签发前生成内容寻址 input manifest，绑定 canonical dataset、source semantic
manifest、activation source、outer/training manifests、五 checkpoint、七行模型输入、
站点映射及预测值；还把 deploy profile、issue producer、`pyproject.toml`、`uv.lock`
以及 Python/NumPy/pandas/PyTorch 版本纳入科学语义。实现或环境变化因此会触发
same-target semantic conflict，而不会静默继承旧签发。首次 canonical issue 字节先写入
`runtime.objects` 内容寻址对象，
再以 atomic no-replace 登记 `issue_receipts/YYYY-MM-DD.json`，最后发布 inbox，并立即
通过 E2-A consumer 合同重读。相同目标且科学语义相同的复跑只能恢复/保留 receipt
绑定的首次精确字节和时间；issue、receipt、exact object、时间字段或嵌套 artifact
任一篡改，以及相同目标科学语义变化或 outcome 污染，均 fail closed。

receipt 是首发 commit point：若进程在 receipt 与 inbox 之间崩溃，下一 poll 会先从
已验证 receipt/object 恢复首发精确 bytes，再单独报告任何新候选冲突。推理完成后、
inbox 原子发布前以及发布后均重新采机器 UTC；若发布跨越目标日起点，producer 在仍
持有 E2-A runner lock 时移除该不可消费 inbox 并写 blocked 状态，不会把早期采样时间
冒充实际持久化时间。

## 6. E2-B1 历史实机等待演练

仓库当前没有配置的未来 daily finalized feed。真实四阶段 poll 的结果为：

- source：`waiting_for_daily_finalized_feed`；
- bundle：`waiting_for_semantically_validated_source`；
- issue：`waiting_for_source_or_model`；
- E2-A：`waiting_for_production_bundle_or_source_snapshot`，ledger events `0`。

演练没有生成 `source_current.json`、activation manifest、model manifest、issue 文件
或 `ledger.sqlite3`。这正是缺输入时的正确机器行为；没有用历史数据伪造“未来运行”。
测试中的 reduced-epoch bundle 只通过私有且显式的测试开关可用，生产 loader 必须
接受配置锁定的 120 epochs。真实 120-epoch bundle 因没有 activation source 而未运行。
source/bundle/issue/E2-A/main 联合定向测试为 136/136，完整仓库回归为 505/505；
Ruff、compileall 与 `git diff --check` 通过。正式 v5 fail-closed preflight 测试
23/23 通过，G0 仍 PASS、G1--G4 仍 BLOCKED、G5a 未授权。E1 manifest/metrics/site/
station SHA-256 分别保持 `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`、
`9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`、
`d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`、
`805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`；
97 条保护路径聚合仍为
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。

## 7. E2-B2 闭环与下一机器门禁

E2-B2 已实现 machine-only outcome materializer 与有界 fixed-point cycle。materializer
只从已验证 immutable per-date source provenance 抽取 finalized 目标观测，按
revision → sealed outstanding issue → contiguous backfill 的固定优先级物化。每目标
revision 用 immutable receipt predecessor/sequence 链、唯一 tip、active receipt pointer、
exact object 和 inbox 分层提交，支持 receipt→pointer→inbox 崩溃恢复并拒绝分支/
回退。activation watermark 及更早修订返回 `waiting_epoch_rotation_required`，
不暗中改写旧 epoch。

cycle 按 source ingest → bundle ensure → live reconcile → outcome materialize →
live reconcile → issue produce → live seal 顺序反复运行，直到验证科学
progress token 不再变化。token 包含 source/model、verified ledger scientific projection、
receipt tips、active bindings 与 inbox bytes，但排除 poll 时间、raw ledger head 与持续失败
anchor retry。空输入是一轮 `converged_waiting`；64 轮合法单调进展后还有 backlog
则以 `work_remaining/exit 0` 交回调度器，跨调用 continuation token history 只在科学状态
回到已见 token 时按振荡阻断。详细锁序、pre-genesis ledger 恢复、symlink/status
复验和 focused 测试见 `docs/ootang_prequential_cycle_engineering.md`。E2-B2 完整仓库
最终回归为相关联合 197/197、全仓 566/566；outcome/cycle focused 分别为 31/31
和 23/23，独立对抗复审最终无 P0/P1。本文第 6 节 136/136 与 505/505 仍只是
E2-B1 历史记录。

后续 calibration shadow v1 已用独立 ledger 前瞻运行 ACI、AgACI-EWA 与 SPCI，
cycle v2 在本节闭环的 source/outcome/issue 边界插入四个因果对账点。该增量不修改
本文件的 source/model/issue 合同；其 `50/50` focused、`269/269` 联合和
`635/635` 全仓验证记录见 shadow 工程文档。

指定入口的 runner-independent checkpoint/input replay 已由 replay/verified-live/
cycle-v3 实现，但旧 live/cycle CLI 尚未被系统权限禁用。standalone RFC 3161
pinned-provider shadow 也已实现并保持 E2/activation false；其离线复验和恢复合同见
`docs/ootang_trusted_time_shadow_engineering.md`。仍需完成：

1. immutable epoch registry、预构建与安全自动 rotation；
2. 把可信时间 shadow 接入新的 designated cycle/epoch 资格门；
3. scheduler entry authorization，禁止绕过指定 replay-gated 入口；
4. 避免 receipt/ledger registry 每次重复全链扫描导致 O(N²) 增长的性能优化。

这些工程门关闭前，E2 证据与真实激活保持 false。正式 v5 的 G1--G4 状态也完全
独立，不得由这条位移预测工程支路绕过。不得为了推进日期而增加人工冻结、
日期选择、批准、签名或补写 outcome/backfill 入口。
