# 藕塘 E2-A 机器预序账本工程说明

> 建立日期：2026-08-26
> 状态：`e2a_engineering_only_not_live_evidence`
> 配置：`config/ootang_prequential_live.v1.json`
> 配置文件 SHA-256：`bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00`
> 运行范围：`future_prequential_motion_engineering`
> 正式预警输出：`false`

## 1. 结论与边界

E2-A 已实现一个按机器时钟单次轮询、可由调度器重复调用的 append-only
prequential runner。激活后，日期发现、8 测点批次签发、封存、外部锚尝试、
结局揭示、评分、在线状态更新、迟到回填、结局修订、恢复和等待均由机器完成，
不需要人工逐日冻结、挑日期、选 seed、选阈值或批准状态转移。

E2-A **永远不产生 E2 live evidence**。它当前不能从已绑定的五个 checkpoint
重放预测，不能验证 issue 所引用 input manifest 的语义，也不能密码学验证一个
固定可信时间服务的回执，更不能在前提变化时自动建立新的 immutable epoch。
因此，即使一个 HTTPS 接口返回格式正确的 JSON，最多只能形成
`engineering_blind_time_order_candidate=true`；以下字段仍固定为：

```json
{
  "trusted_anchor_receipt_verified": false,
  "e2_live_evidence_eligible": false,
  "real_activation_ready": false
}
```

本实现不改变正式 v5 的 G0--G4 门禁，不使用 Vajont，不声称独立灾害结局，
也不输出颜色、灾害概率、event recall、FAR、AUROC 或 AUPRC。E2-A 的价值是
把严格时序和失败恢复做成可执行工程基础，而不是把工程时序候选包装成科学证据。

后续没有改写本 v1 runner 或账本合同，而是增加了 additive
`ootang-issue-replay`、`ootang-verified-live` 和 cycle v3 指定入口。该入口在同一
`runner.lock` 内先独立重放 source 尾七日、activation normalization、五个安全
checkpoint、IDW/ConvLSTM/readout 与 40 个 P50，再按 immutable replay receipt →
seal intent → 本 v1 issue transaction → completion 提交。若 v1 已直接 seal 且没有
预先存在的 intent，wrapper 会 fail closed，绝不事后追认；但旧 v1 CLI 尚未被系统级
权限禁用，因此这里的“独立重放已实现”只适用于指定入口，不是不可绕过的部署授权。
完整合同见 `docs/ootang_checkpoint_input_replay_engineering.md`。

## 2. 架构与运行目录

核心文件如下：

| 组件 | 路径 | 职责 |
|---|---|---|
| 版本化合同 | `config/ootang_prequential_live.v1.json` | 锁定站点、目标、输入、时间、账本、锚和能力边界 |
| 纯在线数学 | `code/monitoring/prequential_core.py` | 不接触文件系统或时钟的 immutable station state、issue/reveal 和 site 聚合 |
| 事件账本 | `code/monitoring/ootang_live_ledger.py` | SQLite WAL、原子追加、自然键幂等和完整 SHA-256 链验证 |
| 机器轮询 | `code/monitoring/ootang_prequential_live.py` | 前提加载、状态机、完整科学重放、锚、修订、状态输出和 fail-closed |
| 管线入口 | `main.py` 中 `ootang-prequential-live` | 显式调用 E2-A；不属于默认 pipeline |

默认运行根目录是 `runtime/ootang_prequential_live_v1/`：

```text
runtime/ootang_prequential_live_v1/
├── source_snapshot/manifest.json
├── model_bundle/manifest.json
├── issue_inbox/YYYY-MM-DD.json
├── outcome_inbox/YYYY-MM-DD.json
├── anchors/
├── ledger.sqlite3
├── runner.lock
└── status.json
```

`runtime/` 是运行态而非论文固化产物，已从 Git 跟踪中排除。账本是科学状态的
唯一事实来源；`status.json` 是每次轮询原子刷新的机器投影，anchor receipt 文件
只是可从已确认账本事件恢复的冗余副本。

数据流为：

```text
机器调度器
  -> 单次 poll + 非阻塞排他锁
  -> 配置 / source snapshot / 五 seed model bundle 完整性检查
  -> 从零哈希 genesis 或完整重放既有 ledger
  -> 先处理已接受日期的 outcome revision
  -> 下一自然日：backfill，或 issue -> seal -> anchor attempt -> reveal
  -> 完整重放并原子更新 status.json
```

runner 本身遵循 `one_machine_poll_then_exit`，所以长期无人值守部署仍需系统级
定时器、作业编排器或数据到达触发器自动调用；它不要求也不包含人工按日按钮。

## 3. 激活前提与 epoch 绑定

若 source snapshot 或五 seed production model bundle 任一不存在，runner：

1. 写入 `waiting_for_production_bundle_or_source_snapshot`；
2. 列出 `missing_prerequisites`；
3. 不创建 ledger，也不伪造 genesis 或日期。

前提同时存在后，机器会严格校验：

- source snapshot 必须声明 8 个固定站点、有限的 latest displacement 和
  `maximum_complete_finalized_date`，且该 watermark 必须严格晚于 2020-06-30；
  E2-A 校验该声明的结构与字节血缘，但尚不从源数据语义上独立证明“共同完整”；
- source snapshot、其 data manifest、model manifest、training manifest 和五个
  checkpoint 的路径、大小与 SHA-256 均匹配；
- checkpoint seed 及顺序必须恰为 `0,1,2,3,4`，不得自动挑选“最佳 seed”；
- model training cutoff 必须等于 epoch genesis watermark；模型创建时间不得早于
  source snapshot，也不得晚于当前机器时间；
- E1 algorithm profile 文件哈希与内容哈希、E1 manifest 和其 issue-chain terminal
  hash 必须与合同一致；E1 只被引用，不与新账本串链；
- Python、实现、平台、`pyproject.toml` 与 `uv.lock` 共同形成环境记录；runner、
  pure core 和 ledger 三个模块共同形成 implementation SHA-256；
- genesis 以前态全零哈希开始，8 个站点全部以 `live_epoch_start` 冷启动，不转移
  E1 terminal state。

epoch id 绑定 profile、source snapshot、model manifest、watermark、实现和环境。
账本创建后，上述前提消失或变化都会触发 fail-closed，而不是由旧 epoch 静默
吸收。E2-A 尚未实现机器自动关闭旧 epoch 并建立新 epoch；这是 E2-B 门禁，
不是要求人工逐日冻结的理由。

## 4. 单次轮询状态机

机器始终只推进 `last_finalized_date + 1 day`，不跨越自然日缺口：

```text
prerequisite missing
  -> waiting_for_production_bundle_or_source_snapshot

prerequisite ready + empty ledger
  -> epoch_genesis
  -> replay and verify

no outstanding issue
  ├── next-day finalized outcome already exists
  │     -> backfill_not_blind -> replay -> inspect following day
  ├── later input exists but next day is missing
  │     -> waiting_for_missing_natural_day
  ├── neither issue nor outcome exists
  │     -> waiting_for_new_data
  ├── next target is on/before local today and has no outcome
  │     -> waiting_for_backfill_outcome
  └── a valid future issue exists
        -> atomic issue batch -> automatic anchor attempt
        -> waiting_for_outcome, or consume a concurrently arrived outcome

outstanding durable issue
  -> repair/retry anchor automatically
  -> waiting_for_outcome, or atomic outcome batch -> replay

contract, schema, time, hash, lifecycle or mathematical mismatch
  -> blocked_integrity + non-zero process exit
```

轮询不追加 heartbeat；没有新数据时 ledger terminal hash 保持不变。另一个进程
持有 `runner.lock` 时，本进程立即以 busy 退出，不并发写账本。

## 5. issue / outcome / 时间隔离

预测目标是 Asia/Shanghai 自然日的下一日累计位移，记录时间统一为 UTC。每个
issue 必须满足：

- 文件名、`target_date` 和机器期望的下一自然日完全一致；
- `source_as_of_at_utc <= generated_at_utc`，两者均严格早于目标自然日开始；
- `generated_at_utc` 不早于 production model 创建时间，也不晚于轮询时间；
- source snapshot 与 model manifest 哈希属于当前 epoch；
- input manifest 引用的文件在字节大小和 SHA-256 上匹配；
- 8 个站点且顺序固定，每点恰有 persistence 与 seed0--4 六个有限预测；
- persistence 必须等于账本中上一自然日的最新累计位移；
- 任意层级出现 `actual`、`actual_mm`、`outcome`、`reveal_actual_mm`、
  `warning_color`、`landslide_probability`、`event_recall` 或 `far` 即拒绝。

issue 以一个 `BEGIN IMMEDIATE` 事务整体追加：

```text
issue_batch_opened
  -> station_issue × 8（固定站点顺序）
  -> issue_batch_sealed
  -> fallback_or_abstain_recorded × 8
```

所有站点 issue 在追加前已完成内存校验。`issue_batch_sealed` 记录整批根哈希，
并明确 `same_date_actual_excluded=true`。事务提交后机器才允许尝试外部锚；在
issue 已 durable 且 anchor attempt 已记录以前，runner 不读取该目标日 outcome。

outcome 必须包含相同 8 个站点、`finalized=true`、严格 UTC 的 observed / available /
finalized 时间、稳定 source id 和 revision id，并验证其 source manifest 字节。
finalized 时间晚于机器轮询时间时拒绝读取。首次 outcome 以另一个原子事务追加：

```text
outcome_batch_opened
  -> outcome_revealed × 8
  -> [score_recorded
      -> expert_state_updated
      -> conformal_state_updated
      -> drift_state_updated] × 8
  -> site_score_recorded
  -> outcome_batch_settled
```

8 个 reveal 必须全部在任一 score 或状态更新前 durable。状态更新只影响后续
target；site 只保留连续 score，不产生 warning color。若 outcome 在任何 durable
issue 以前已经存在，该日期只能追加为 `backfill_not_blind`：它推进已接受日期和
persistence 基准，但不创建追溯 issue、不更新在线统计，也不计盲态指标。

## 6. Append-only 完整性、恢复与重放

SQLite ledger 使用 WAL、`synchronous=FULL`、STRICT table 和单 writer
`BEGIN IMMEDIATE`。数据库内的 trigger 禁止 UPDATE/DELETE；event key 与 entry
hash 均唯一。相同自然键、相同稳定内容的重试幂等，相同键但内容变化或只出现
半个事务时拒绝继续。

每个事件均绑定：

- protocol config、implementation、environment；
- input manifest 与 model manifest；
- station/aggregate state-before 与 state-after；
- canonical UTF-8 JSON payload；
- previous entry SHA-256 与自身 entry SHA-256。

canonical JSON 使用排序 key、紧凑分隔符、禁止 NaN/Infinity，浮点按 Python JSON
binary64 最短 round-trip 规则序列化。每个公开账本读取先校验完整 schema、trigger
和从 genesis 到 terminal 的整条哈希链，而不是只看最后一行。

runner 随后从事件零状态完整重建 scientific projection，复算并检查：

- genesis 前提和 8 个 cold-start station state；
- issue 生命周期、站点顺序、expert 混合、conformal 区间与 fallback；
- reveal、绝对误差、单侧异常、expert loss、ACI、drift reset 和状态哈希；
- O1/O2/O3 连续 site 聚合及最终状态；
- issue/outcome/source/model/input 哈希的跨事件一致性；
- anchor 与 seal 的链接、修订血缘和日期单调性。

进程若在 `anchor_confirmed` 提交后、receipt 文件落盘前崩溃，下次 poll 会从账本
自动重建 receipt；已存在但内容不同的 receipt 会触发完整性阻断。状态文件也以
临时文件、fsync、原子 replace 和目录 fsync 写入。ledger/schema/hash 损坏时会尽力
刷新 `status.json` 为 `blocked_integrity`，并设置 `ledger_validation_failed=true`。

本地 SHA-256 链能发现内容变更，但不能证明写入者身份或可信时间。该限制不能
用“服务器返回过 JSON”代替。

## 7. 在线数学与 outcome revision

`prequential_core.py` 的 station state 是 immutable、无文件系统和无时钟依赖的
纯函数状态。它沿用 E1 v1 合同：persistence + seed0--4 六专家的在线组合、绝对
残差 conformal 区间、ACI、单侧 underprediction anomaly、有限历史窗漂移检测，
以及 O1/O2/O3 连续 site 聚合。每次 issue 只读前态；每次 reveal 才产生下一状态。

同一个 revision id 重放相同字节时幂等，revision id 被复用于不同字节时阻断。
已 settled issue 的修订追加每站 `outcome_revision` 与
`revision_rescore_recorded`；backfill 的修订只追加 revision，因原本没有可重算的
blind issue。修订视图：

- 保留首次 outcome、首次评分和当时的 online station state；
- `live_online_state_rewritten=false`，不把新结局倒灌进历史 expert/ACI/drift；
- revised rescore 标记 `updates_live_state=false`、`blind_metric_eligible=false`；
- 若修订的是当前最新已接受日期且尚无 outstanding issue，更新后续 persistence
  所用的 latest displacement；绝不改写已经签发的 issue。

## 8. 必须原样保留的机器可读能力边界

配置和每份 status 必须包含以下精确键值。任何改变都要求新的已审查版本；当前
loader 对差异 fail closed：

```json
{
  "issue_predictions_source": "external_precomputed_feed_not_replayed",
  "input_manifest_semantics_verified": false,
  "checkpoint_inference_replayed": false,
  "trusted_anchor_receipt_verified": false,
  "automatic_epoch_rotation_implemented": false,
  "prerequisite_change_behavior": "fail_closed_pending_machine_epoch_manager",
  "real_activation_ready": false
}
```

相应科学解释如下：

1. runner 只验证外部 issue feed 的结构、时间、有限数值、文件哈希和 epoch 链接；
   它没有证明这些 seed 预测确由声明的 checkpoint 和 as-of features 计算得到；
2. input manifest 当前只按路径、字节数和哈希验证，没有逐字段证明特征可见时间、
   站点/日期自然键、预处理与模型 input schema 的语义；
3. `anchor_confirmed` 只表示 HTTPS JSON 接口形状与 root 匹配，不表示 provider
   身份、签名、证书链、可信时钟或不可抵赖时间已经验证；
4. 代码、配置、模型、环境或激活前提变化会阻断旧 epoch，尚无机器 epoch manager
   完成关闭、注册、冷启动和原子切换；
5. 所以 E2-A settlement 的 `e2_live_evidence_eligible` 永远为 false，状态中的
   `e2_live_evidence_accumulated` 也不得被 HTTPS 回执提升。

## 9. 机器运行与状态读取

直接执行一次 poll：

```bash
uv run python code/monitoring/ootang_prequential_live.py \
  --config config/ootang_prequential_live.v1.json
```

通过显式 pipeline stage 执行：

```bash
uv run python main.py \
  --stage ootang-prequential-live \
  --manifest /tmp/ootang-prequential-live-v1-run.json
```

该 stage `enabled_by_default=false`，避免在历史建模默认链中误称真实 live 运行。
生产编排器应在输入到达或固定周期自动运行上述命令，并依据 process exit code 与
`status.json` 驱动重试/告警；不得由人工选择某个日期文件后再启动。

主要 `runner_status` 语义：

| 状态 | 机器含义 | 是否追加科学事件 |
|---|---|---|
| `waiting_for_production_bundle_or_source_snapshot` | 激活前提不齐，安全等待 | 否，且不创建 ledger |
| `waiting_for_new_data` | 下一自然日没有 issue/outcome | 否 |
| `waiting_for_missing_natural_day` | 已看到更晚文件但连续下一日缺失 | 否 |
| `waiting_for_backfill_outcome` | 下一目标已成为历史，禁止追溯签发 | 否 |
| `waiting_for_outcome` | issue 已封存并已自动尝试锚，等待 finalized outcome | issue/anchor 已追加 |
| `blocked_integrity` | schema、时间、哈希、前提、数学或生命周期不一致 | 不继续推进；CLI 返回 2 |

锁竞争属于进程级 `busy`（CLI 返回 3），不代表科学状态变化。当前机器真值应直接
读取运行态文件，而不是抄写到论文：

```bash
uv run python -c 'import json; from pathlib import Path; p=Path("runtime/ootang_prequential_live_v1/status.json"); print(json.dumps(json.loads(p.read_text()), ensure_ascii=False, indent=2))'
```

## 10. 验证记录与字节指纹

最终验证中，E2-A 定向测试为 64 个通过：pure core 7、ledger 12、live runner
20、pipeline/main 25；冻结 G0--G4 gate 另有 23 个通过。全仓 discovery 共
414/414 个测试通过，E2 相关文件和全仓的 Ruff、compileall 均通过。覆盖的关键边界
包括：缺前提等待、冷启动、backfill、完整 issue/outcome 生命周期、锚失败与恢复、
幂等重试、并发锁、日期缺口、反向时间、禁止 outcome 字段、单次文件快照读取、
SQLite schema/chain 篡改、完整数学重放及首次/后续修订。

真实单次 poll 在缺少 source/model 前提时自动写出
`waiting_for_production_bundle_or_source_snapshot`，列出两个缺项，保持
`ledger_event_count=0` 且不创建 `ledger.sqlite3`。显式 E2-A 与默认 pipeline
dry-run 均通过；默认链仍只有 `features -> convlstm -> ootang-operational-v4`。

冻结登记表引用的 18 个唯一文件全部匹配登记 SHA-256，G0--G4 gate 23/23 通过。
E1 四个产物和 97 个保护路径也未漂移：

- E1 manifest：`2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`；
- E1 metrics：`9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`；
- E1 site：`d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`；
- E1 station：`805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`；
- 97 路径聚合：`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。

最终检查同时修复了前一提交把两个已登记 Markdown 文件的硬换行从两个空格改成
`<br>` 所造成的字节漂移；`config/ootang_v5_gate_register.v1.json` 未修改。恢复后的
5 行尾空格是登记快照的一部分，因此全路径 `git diff --check` 会只报告这 5 行；
排除 `docs/v5_g1_g4_preflight.md` 与 `docs/v5_v0_numerical_audit.md` 后无其他问题。

最终执行命令为：

```bash
uv run ruff check code tests main.py
uv run python -m compileall -q code main.py tests
uv run python -m unittest \
  tests.test_prequential_core \
  tests.test_ootang_live_ledger \
  tests.test_ootang_prequential_live \
  tests.test_main
uv run python -m unittest discover -s tests -p 'test_*.py'
xargs shasum -a 256 < docs/v5_v0_protected_paths.txt | shasum -a 256
```

2026-08-26 起草时的工作树字节指纹如下。若这些文件在提交前有任何改动，必须
重算并同步更新本表；不能保留一个与提交字节不符的“通过”记录。

| 文件/组合 | SHA-256 |
|---|---|
| `config/ootang_prequential_live.v1.json` | `bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00` |
| `code/monitoring/prequential_core.py` | `9b5cf69192ffba885c136598735db919714828a93945dc23cb2141bf553e041b` |
| `code/monitoring/ootang_live_ledger.py` | `087356f33e43996411d3524ec99256d4b5c390f09dd0dbfca083cc7a63245b14` |
| `code/monitoring/ootang_prequential_live.py` | `95c46165020b16bcf1da743c156f6fd0430e6e56f5a88cafceec7703e4c571ff` |
| runner/core/ledger canonical implementation composite | `3db7a40b6835128bc4de63c7d10f96b4c89515d142760f04c1d053cbba7a3629` |
| `tests/test_prequential_core.py` | `60e7bcd22631c3db773f4f3dc5151a09ec890791785e38d338f5045c317f7650` |
| `tests/test_ootang_live_ledger.py` | `01d09771be2003d2d39a907a0ce8694e21a9b63e1521ebeb52e9dadce8c3b08f` |
| `tests/test_ootang_prequential_live.py` | `d80d7dda765caf9092b21019eb94c074928273fbd9e28fce42b68ef68cbfb9d4` |
| `main.py` | `f81f8bc16800ef9a9ff14c012cb21fd82acfd9f3fd5c7658e1308a2878bb882f` |
| `tests/test_main.py` | `391a4d8606b518dfd3717af997c5d145afe59cdfcb06da8a7c27c35df7365e0f` |

重算命令：

```bash
shasum -a 256 \
  config/ootang_prequential_live.v1.json \
  code/monitoring/prequential_core.py \
  code/monitoring/ootang_live_ledger.py \
  code/monitoring/ootang_prequential_live.py \
  tests/test_prequential_core.py \
  tests/test_ootang_live_ledger.py \
  tests/test_ootang_prequential_live.py \
  main.py tests/test_main.py

uv run python -c 'import sys; sys.path.insert(0,"code"); from monitoring.ootang_prequential_live import _runner_code_sha256; print(_runner_code_sha256())'
```

真正激活时，implementation composite、environment、source/model/input manifests
及状态链哈希会进入 genesis 和每个事件；本节的静态表不能代替运行时 ledger。

## 11. E2-B：机器化部署门禁与当前状态

E2-B 不是人工冻结数据，而是把外部信任边界继续收进机器闭环。前两项已作为
engineering-only 机器路径实现；其余门禁关闭前仍不能讨论真实 E2 证据：

1. **已实现——内容寻址的自动 ingest 与语义验证**：机器从源系统生成不可变 snapshot /
   per-issue input manifest，验证站点、自然键、单位、缺失、finalization、as-of
   可见性、特征预处理和 input schema，而不只验证路径与字节哈希。
2. **指定入口已实现——五 seed deployment bundle 与 checkpoint 推理重放**：按 genesis watermark
   生成固定 seed `0..4` 的不可变 checkpoint、训练 manifest、环境和推理图；issue
   producer 从这些字节实际推理，additive verified-live wrapper 在 seal 前独立重放。
   旧 live-v1 CLI 尚可绕过，仍需 scheduler authorization；禁止使用 OOF CSV 充当
   未来预测，也禁止事后挑 best seed。
3. **不可变发布单元**：把 runner/core/ledger、依赖锁、模型和 schema 发布为
   content-addressed bundle；部署进程实际执行的字节必须与 ledger 绑定的实现一致。
4. **可信时间回执验证器**：预声明 provider allowlist、pinned public key / trust
   root、签名算法、canonical request、最大时钟偏差和失败策略；本地验证签名、
   sealed root 与时间，且在 outcome 可见前 durable，不能信任任意 HTTPS JSON。
5. **自动 epoch manager**：前提变化时机器原子关闭旧 epoch，保存不可变 registry，
   校验候选 bundle，按预声明规则冷启动新 epoch，并保证旧 issue/outcome/revision
   仍能路由到原 epoch；失败只能等待或阻断，不能回退到人工挑选。
6. **自动监督与故障演练**：为 scheduler、单 writer、磁盘满、断电、半写、重复
   投递、乱序文件、锚超时、receipt 丢失和恢复建立机器告警与 fault-injection
   验收；发布门禁应验证真实运行路径而不只调用测试 fixture。
7. **重放性能门禁**：当前 scientific replay 在长期逐日轮询下约为
   `O(D·N)`（每次 poll 对累计 N 个事件完整重放，跨 D 次轮询累计放大）。E2-B
   应增加基准预算，并采用经链绑定的 authenticated checkpoint / materialized
   snapshot 加尾部重放，同时保留周期性全链审计。优化必须证明与从 genesis 全量
   重放得到相同 terminal state，不能以缓存跳过完整性验证。
8. **证据资格验收**：新版本只有在 checkpoint inference、input semantics、可信
   回执和自动 epoch 四项机器字段均由真实执行路径验证为 true 后，才允许对新签发
   日期计算 E2 eligibility；不得追溯提升 E2-A 已有记录。

即使 E2-B 全部通过，系统获得的仍只是未来盲态的运动学预测/校准证据。独立灾害
结局与正式预警效能属于 E3，不能由模型自己的异常分数或自动化程度替代。

## 12. 交接不变量

后续实现和部署必须保持：

- 正常日不需要人工冻结、人工选日期、人工选 seed 或人工判阈值；
- 没有新数据就等待，已有结局但没有先验 issue 就标记 backfill；
- issue 在 outcome 读取前全站封存，更新只作用于未来；
- 原始 outcome、修订、评分和状态只追加不覆盖；
- 未满足 E2-B 门禁时 `e2_live_evidence_eligible=false`；
- 没有独立灾害结局时不报告灾害 recall、FAR 或风险概率；
- 任何完整性或前提漂移都 fail closed，不伪造日期、预测或证据。
