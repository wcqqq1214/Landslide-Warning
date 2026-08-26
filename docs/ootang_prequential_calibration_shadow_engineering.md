# 藕塘 E2 校准 shadow 与 cycle v2 工程说明

> 建立日期：2026-08-26
> 协议：`ootang-prequential-calibration-shadow-v1`
> 证据层级：未来 E2 流上的机器前瞻顺序工程；当前不是 E2 live evidence
> 选择 / 晋升 / 正式预警：`false / false / false`

## Material Passport

| 字段 | 记录 |
| --- | --- |
| mode | `design + implement + adversarial validate` |
| upstream | verified E2-A append-only live ledger；不信任 status 或 inbox |
| fixed point forecast | 每站已封存 `station_issue.issue.point_forecast_mm` |
| candidates | `aci_v1_control`、`agaci_ewa_variant_v1`、`spci_qrf_v1` |
| state origin | 每个 live epoch 对 24 套状态冷启动，不转移 E1 终态 |
| persistence | 独立 SQLite STRICT append-only ledger、事务摘要、全局链和 issue-only 链 |
| automation | standalone shadow poll + cycle v2；指定入口另有 replay-gated cycle v3，无人工选日/冻结/批准 |
| status | engineering-only；指定入口 replay 已实现，可信时间、系统级入口授权和上游 epoch registry 仍未闭门 |
| protected boundary | live/deploy/cycle v1、校准 core、E1 bundle 和正式 v5 均不改写 |

## 1. 为什么是独立 shadow，而不是修改 live v1

E1 回顾性比较已经显示 AgACI-EWA 有改善信号、固定 SPCI 有欠覆盖，但这些结果已经
被查看，不能再用同一数据选择赢家。本协议把三种固定方法原样送入未来机器数据流，
只比较区间校准，不改变 live v1 的 point forecast、异常分数、状态或任何预警输出。

Shadow 使用新的配置、runner、runtime 和 SQLite application id。它不会向 live v1
ledger 的硬编码事件白名单加入新类型，也不改变 `calibration_challengers.py`；因此
既有 E1 manifest 和 E2-A 事件中的实现身份仍保持原版本。shadow 只消费
`load_verified_ledger_projection()` 完整数学重放后的不可变事件。

后续 cycle v3 没有改变候选、阈值、shadow ledger 或 readiness 结论，只在 cycle v2
外围加入 issue replay 屏障，并把所有 live transition 路由到 verified-live 指定入口。
因此它增强的是签发来源完整性与崩溃可恢复因果链，不会改善或重新估计 ACI、
AgACI-EWA、SPCI-QRF 的覆盖率、区间宽度或预测精度。

## 2. 固定候选与预声明评估合同

三种方法及其全部超参数逐字段绑定 E1 bakeoff v1：ACI 是项目现有控制；
AgACI-EWA 是上下端点分别按过去 pinball loss 做指数加权的项目变体，不是论文中的
BOA + gradient trick 精确复现；SPCI-QRF 使用固定 lag、窗口、beta grid、浅层森林、
单线程和随机种子。任何结果到达后都不能在本版本搜索新 gamma、lag、窗口、树深或
beta。

配置在第一个 shadow outcome 前预声明以下机器评估门槛：目标覆盖率 `0.8`、共同
可用支持、至少 `180` 个共同可用目标日和每站每方法 `180` 个样本、30 日滚动窗、
绝对覆盖差不超过 `0.05`、challenger 相对 ACI 的覆盖差容许边际 `0.02`、interval
score 比值不大于 `1.0`、availability 至少 `0.95`，并要求全部站点通过。revision、
backfill 和非合格签发全部排除。

这些数值是版本化的保守工程预声明，其中 `180` 对应完整最大残差窗口；它们不是
ACI、AgACI 或 SPCI 论文推出的安全保证，也不是滑坡预警效能阈值。本版本只能自动
计算 engineering-readiness gate，事件 schema 没有 winner/selected/promoted 类型，
`automatic_promotion_enabled=false`。即使以后 gate 达标，也只能创建新的协议和
epoch，不能回写本账本或 live v1。

## 3. 因果生命周期

### 3.1 Issue

Runner 固定按 `live runner lock -> shadow lock` 加锁，先完整验证 live 与 shadow 两条
链。只有验证到一个 live `issue_batch_sealed` 且尚无同日 live settlement 时，才从
其前面的八个 `station_issue` 读取固定 point forecast。它先在内存构造全部
`3 methods x 8 stations = 24` 个 issue，再以单个 shadow SQLite 事务追加：

```text
shadow_issue_batch_opened
  -> 24 x shadow_candidate_issued
  -> shadow_issue_batch_sealed
```

Issue payload 绑定 live epoch、live seal entry hash、八个 station issue entry hash、
24 个完整 state-before hash、方法设置、前一条 shadow issue batch hash和当前 batch
hash。其 exact-key schema 禁止 actual、outcome、reveal、coverage、score 等字段；
issue-only 链因此独立于后来结果。

### 3.2 Reveal 与状态更新

Actual 只允许来自绑定同一 live seal 的原始 `outcome_batch_settled.payload`。Runner
必须先证明已有 durable shadow seal，才为全部 24 项计算 reveal，并在一个事务中
追加 opened、candidate reveal、state update 和 settlement。状态只在 reveal 后更新；
同一 live outcome 事务中每站 `drift_state_updated.drift_detected=true` 时，三种方法
均在更新后自动换成下一日冷状态。

若 live settlement 已经存在而 shadow seal 不存在，系统只追加
`shadow_backfill_ineligible`：不补造 issue、不更新校准状态，也不计支持。激活时已经
outstanding 的 live issue 可以生成 shadow issue，但固定标记
`engineering_prospective_ordering_candidate=false`。修订只追加 retrospective rescore，state-before 与
state-after 相同，不改写在线状态或原始指标。

## 4. 账本、重放与崩溃恢复

独立 ledger 使用 WAL、`synchronous=FULL`、STRICT schema、唯一 event key/entry
hash、conflicting-insert/replace guard、no-update/no-delete trigger 和 canonical
finite JSON。guard 在 `INSERT OR REPLACE` 删除旧行之前阻断 sequence、event key
或 entry hash 冲突，不能通过 SQLite 默认的 non-recursive trigger 行为绕过追加约束。
每一事件还记录整个
SQLite append transaction 的摘要、位置和大小，因此完整同语义重试会返回首次
事件与首次时间，而旧事务任意真子集、部分旧键加新键或同键异语义都会 fail closed。

每次公开读取和写入都从 genesis 验证完整 schema、全局 previous-entry chain、事务
边界，并从冷状态数学重放所有 issue/reveal；`status.json` 只是可重建的调度视图。
进程在 live seal 后、shadow transaction 中、shadow commit 后或 status 写前崩溃，
下一次 poll 都按两条不可变链确定性恢复。两库无法形成跨库原子事务，因此恢复以
live sequence + entry hash 为幂等游标；绝不通过回填伪装原子性。

上游 live epoch 改变且 shadow 没有 outstanding issue 时，机器自动 close 旧 shadow
epoch并冷启动新 epoch；若仍有 outstanding issue则 fail closed。这个局部轮换不等于
上游 live v1 已具备完整 immutable epoch registry，后者仍是系统门禁。

## 5. cycle v2 的自动因果屏障

Standalone poll 适合审计和恢复；正常机器调度使用新的 cycle v2。它复用 v1 的
source、bundle、outcome、issue 和 live 子阶段，但在可能跨过 reveal 边界的位置调用
同一个 shadow reconcile API：

```text
shadow reconcile before source / crash recovery
  -> source ingest
  -> bundle ensure
  -> live reconcile before outcome
  -> shadow reconcile before outcome materialization
  -> outcome materialize
  -> live reconcile after outcome
  -> shadow reconcile after settlement
  -> issue produce
  -> live seal issue
  -> shadow reconcile after issue
```

开头的 reconcile 修复“上次 live seal 已提交、shadow 尚未提交”的崩溃窗口；末尾
reconcile 把新 seal 在本轮就写入 shadow。cycle v2 与 v1 复用同一个 outer lock，
避免两个 orchestrator 并发。科学 progress token 组合 v1 token 和 verified shadow
projection，并对 v1 token 做前后双采样；它不绑定 status、lock、poll 时间或 raw
SQLite bytes。

shadow runner 单次最多追加 512 个动作，合法长积压返回 `work_remaining/exit 0`，
由调度器继续调用。cycle v2 只有在组合科学 token 实际变化时接受该状态；若阶段声称
仍有工作而 token 稳定，则按合同矛盾 fail closed，不会误报固定点。

该集成消除了逐日人工冻结、人工选择日期和人工批准，但不能阻止管理员绕过 v2 直接
运行旧 live CLI。因此当前仍是受控机器工程协议；要形成可主张的 E2 evidence，还需
调度入口权限边界和可信时间证明。上游 live v1 ledger 也仍是 trusted-writer hash
chain，而不是经外部密钥认证的任意写入者防篡改存储；shadow 的独立 guard 不会追溯
改变该上游证据边界。

## 6. 状态与证据边界

正常无数据、缺少 prerequisite、等待 issue/outcome、已经追平或合法长积压分别以
waiting / reconciled / work_remaining 状态 `exit 0` 返回。runner 或 shadow lock
竞争是 busy / `exit 3`；schema、
hash、cursor、事务、epoch或数学重放冲突是 blocked / `exit 2`。任何状态都不请求人
指定日期或补签。

shadow-v1/cycle-v2 自身仍不提供 runner-independent checkpoint/input replay；通过
additive cycle-v3 指定入口运行时，这一门由 wrapper 在 live seal 前提供。独立标签、
确认性外部验证、可信 anchor 回执、E2 live evidence、real activation、selection、
promotion 和 formal warning 在两条路径中都固定为 false。本协议解决的是三校准器
未来 issue-before-reveal 的机器执行和审计问题，不证明覆盖保证、滑坡事件识别、
FAR/recall 或安全认证。

## 7. 复现入口

```bash
uv run python main.py --stage ootang-prequential-calibration-shadow
uv run python main.py --stage ootang-prequential-cycle-v2
uv run python -m unittest tests.test_ootang_calibration_shadow_ledger
uv run python -m unittest tests.test_ootang_prequential_calibration_shadow
uv run python -m unittest tests.test_ootang_prequential_cycle_v2
```

固定配置 SHA-256 为：shadow
`28c02510f81e1832220913d4bfde69bc8abe9a2269aa8279aabc297f60113857`，cycle v2
`875daa95416e58e3c80a4a68f59874047daa1bbb5b395878f6a9265605279242`。
shadow-v1/cycle-v2 的历史验收为 ledger/runner/cycle-v2
`14/13/23`（`50/50`），pipeline `29/29`，E2
联合回归 `269/269`，全仓 `635/635`（330.833 秒）；Ruff、compileall、JSON 和
diff-check 通过。空 runtime 精确执行 11 阶段并在一轮返回 `converged_waiting`；
shadow 为 `waiting_for_live_prerequisites`，evidence/promotion 均为 false。独立最终
审查 P0/P1/P2 为 `0/0/0`。保护路径和完整哈希结果同步记录于 `docs/progress.md` 与
`CODEX_HANDOFF.md`。
