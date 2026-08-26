# 藕塘 epoch 可执行准备 R2a 工程说明

> 日期：2026-08-27
> R1 基线提交：`3d6ce8f feat: add immutable epoch candidate registry`
> 阶段：`ootang-epoch-preparation`（显式、非默认）
> 配置 SHA-256：`c6ec0b1f340effd9e3fd5cd1a0ee67ebca9ffa4dc743a9cb36d701850dc875f9`
> 实现 SHA-256：`b03182accc3e8d482683eda29c7c07bdb66f7a316dfa99a41f29e1b99b3fe209`
> 状态：`immutable_epoch_executable_preparation_engineering_only_not_live_evidence`

## 1. R2a 关闭的问题

R1 只保存稳定 slot 候选、runtime artifact 快照和 archival byte capsule；它的
显式 allowlist 不是递归本地 import closure，也没有将快照物化为可由旧 verifier
重载的运行树。R2a 消费 R1 不可变 candidate chain，完成以下机器步骤：

```text
verified R1 candidate tip
  -> resolve exact reviewed local import closure
  -> capture missing reviewed package files
  -> publish content-addressed executable capsule and objects
  -> materialize exact executable tree
  -> run two frozen isolated smoke domains
  -> reload candidate prerequisites and replay five seed P50
  -> reverify R1/capsule/tree/smoke immediately before append
  -> append candidate_prepared or candidate_revalidated
  -> rebuild head/status from the immutable preparation chain
```

这一切仍只是 candidate 准备。R2a 不停止旧 epoch 签发，不评估 drain 是否完成，
不切换 active epoch，不将 trusted-time shadow 晋升为可信 anchor，也不授权 E2 证据或
正式预警。

## 2. 精确 22-module closure 与两个 augmentation

closure seed 是 `monitoring.ootang_prequential_cycle_v3`、
`monitoring.ootang_trusted_time_shadow` 和
`monitoring.ootang_trusted_time_shadow_core`。机器从 R1 捕获的 immutable Python bytes
解析静态 import，禁止 dynamic local import、wildcard local import 和未审核的本地补件。
精确闭包必须按规范顺序等于以下 22 个模块：

1. `convlstm`
2. `convlstm.block_bootstrap`
3. `convlstm.grid_interp`
4. `convlstm.model`
5. `convlstm.ootang_production_bundle`
6. `monitoring`
7. `monitoring.calibration_challengers`
8. `monitoring.ootang_calibration_shadow_ledger`
9. `monitoring.ootang_issue_producer`
10. `monitoring.ootang_issue_replay`
11. `monitoring.ootang_live_ledger`
12. `monitoring.ootang_live_source`
13. `monitoring.ootang_outcome_materializer`
14. `monitoring.ootang_prequential_calibration_shadow`
15. `monitoring.ootang_prequential_cycle`
16. `monitoring.ootang_prequential_cycle_v2`
17. `monitoring.ootang_prequential_cycle_v3`
18. `monitoring.ootang_prequential_live`
19. `monitoring.ootang_trusted_time_shadow`
20. `monitoring.ootang_trusted_time_shadow_core`
21. `monitoring.ootang_verified_live`
22. `monitoring.prequential_core`

R1 capsule 唯一缺失而允许 R2a 从当前项目捕获的两个 Python 文件是：

- `code/convlstm/__init__.py`，SHA-256
  `4b51a22e572727ec42d888387891d94bf8ffa0ef17245be9b453b9b56967f127`；
- `code/monitoring/__init__.py`，SHA-256
  `215064d50f1aa515312aeee43318de1231bcfb0ee6614811d1f3b2f83b5ff82a`。

它们在 manifest 中标记为 `r2_closure_capture`。若闭包不再是 22 个模块、需要第三个
augmentation、其中任一 bytes 变化，或自洽 manifest 删掉 root module，都在事件
发布前 fail closed。`ootang_epoch_registry.py`、回顾性 monitor 与 v4 draft evidence 不会
被错当成 activation closure 成员。

## 3. capsule、tree 与 R2a 自身 provenance

每个 executable capsule 精确绑定 R1 profile/event/candidate/slot/live-epoch identity、
R1 capsule tree、closure module 列表、每个逻辑文件的 SHA-256/size/source/role、资源文件
与整棵 materialized tree digest。内容寻址 object 与 capsule 都 create-only，物化树不允许
额外文件、空目录、symlink、非 regular file、路径 alias 或 object/tree bytes 分歧。

R2a profile 和当前 implementation 不只以 hash 字段自报；二者的精确 bytes 都进入
registry content-addressed object store，capsule、smoke receipt 与 preparation event 通过
path/SHA-256/size 交叉绑定这两份 provenance。当前审核值是：

- profile：`c6ec0b1f340effd9e3fd5cd1a0ee67ebca9ffa4dc743a9cb36d701850dc875f9`；
- implementation：`b03182accc3e8d482683eda29c7c07bdb66f7a316dfa99a41f29e1b99b3fe209`。

历史 preparation event 依自身捕获的 implementation object 重放，不被新 coordinator
实现哈希自锁；新实现处理同一 R1 candidate 时，机器重新解析 closure、物化、烟测，
再追加 `candidate_revalidated`，不覆盖旧 `candidate_prepared`。

## 4. same-origin 而非 portable offline runtime

R1 candidate 已在最终 `slots/<slot-id>/live` 路径生成带绝对路径的 source/model
manifest。R2a 因此要求原 canonical project root 和原 canonical slot root，并在 capsule
中保留精确 `slot_live_root`。树不能移到另一项目、slot 或主机后声称等价：

```text
canonical_project_root_required = true
canonical_slot_root_required = true
relocatable = false
portable_offline_runtime = false
```

`materialized_executable_tree=true` 只证明当前同源主机上的逻辑文件树完整可验。树中
不包含 `.venv`，也没有封存 CPython binary、uv binary、wheel cache、OS 或可移植信任时钟；
不得把它写成 air-gapped bundle、container image 或可跨机器恢复的 runtime。

## 5. 双 frozen isolated 环境

生产 smoke runner 先从绝对路径读取并校验已审核 uv binary：

- uv `0.12.5`：`/opt/homebrew/Cellar/uv/0.12.5/bin/uv`；
- uv SHA-256：`debc68c21b3bb1086e20d9889b53ff5ccf9ef343fda9a57dc2022212e3511125`；
- CPython：`3.10.20`；
- Python executable SHA-256：
  `694bcacb03f978975c57396caaec10a42d3fec62a789f82f8661197c9dd17a2e`；
- SOABI：`cpython-310-darwin`；
- platform：`macOS-26.5.1-arm64-arm-64bit`。

然后用 `uv --no-config run --isolated --frozen --python 3.10.20` 分别启动两个
`python -I -B` 进程：

1. 根项目环境：43 个 distribution，inventory SHA-256
   `007dc4fbca73360ff0ca20b744509d2a3b0fbd44711234e64c35715032ebc34e`；
2. `tools/ootang_trusted_time_runtime` 环境：5 个 distribution，inventory SHA-256
   `622737b4a53f420c3e895e4d74205b456fd7efa15b0e4a9250e760c7c24264c5`。

两域都必须匹配同一 Python executable hash、SOABI 与 platform，同时必须分别与根
`pyproject.toml`/`uv.lock` 及 trusted-time 子项目的 frozen lock 一致。这是对当前
same-origin 环境的强指纹，也是 `portable_offline_runtime=false` 的原因。真实 frozen uv
双域运行已经确认精确命中配置：root/trusted 分别为 43/5 个 distribution，且 inventory、
Python executable hash、SOABI 与 platform 全部一致。

## 6. import/compile/prerequisite 与五种子烟测

根环境逐一重读并 compile 22 个 Python 文件，从 materialized tree import cycle-v3
和 trusted-time shadow root modules，并要求它们的 `__file__` 仍位于该树。随后通过
旧 epoch public loader 从 canonical slot 重载 live profile/source/model prerequisites，重算
`candidate_live_epoch_id`。trusted-time 独立环境只从同一物化树 import
`ootang_trusted_time_shadow_core`。

数值烟测不是只看 manifest 字段。它调用重载 bundle 的 `predict_p50()`，要求：

- seed 集合和顺序精确为 `0,1,2,3,4`；
- 每个 seed 的 station key/顺序精确等于部署 profile 的 model station order；
- 所有 `predict_p50` 与 training manifest `reload_replay.reloaded_p50_mm` 值均为 finite；
- 逐 seed、逐站 `rtol=0`，绝对差不超过 `1e-6 mm`。

缺 seed、缺站、站点顺序变化、NaN/Infinity、epoch id 变化、依赖 inventory 漂移或
trusted-time core 不能从物化树 import，都不得生成 preparation event。

## 7. current repoll、升级与 orphan 恢复

已有同 candidate/同 R2a implementation 的 event 时，poll 不仅重放历史 receipt。它每次都
重新验证 current R1 receipt/artifacts/空 future namespaces、capsule 与 materialized tree，在两个
frozen isolated 环境再跑一次 smoke，并与 immutable smoke receipt 比较。五种子差值可在
`0..1e-6 mm` 内有界抖动，其他环境、树、epoch、import 和 prerequisite 语义必须精确一致。

当 R2a implementation 升级而 R1 candidate 未变时，历史 event 继续由当时捕获的
implementation object 验证；当前代码必须重做 closure/materialization/smoke，追加
`candidate_revalidated`。实现回退到已见语义、同 R1 sequence 改 candidate identity 或 R1 tip
回退都 fail closed。

若进程在 smoke receipt 已 create-only 提交但 preparation event 尚未追加时崩溃，下次 poll
可找到 orphan receipt，但不会直接补 event。机器仍重跑当前双环境 smoke，只有当前结果与
orphan 的 immutable 语义一致时才恢复；distribution/Python/platform/树/数值任一漂移
都阻断 orphan 提升。

## 8. 最后发布屏障、锁与状态

R1 与 R2a 共用非阻塞 `manager.lock`，锁竞争返回 busy/exit 3。R2a production API
只接受 reviewed config；CLI 只有 `--config`，没有 runtime、candidate、date、freeze、approve、
force、backdate 或 smoke override。私有 runtime/smoke 注入只允许 project production runtime 之外的
isolated test root。进入 R2a 统一错误归一范围后的合同、候选、树、环境或链冲突返回
blocked/exit 2；少数 acquire-lock/profile 之前的异常仍受第 10 节第 7 项边界约束，可能
以 traceback/exit 1 安全失败。
即使 `--config` 最终解析到同一 bytes，config file 本身或从 canonical project root
到该文件的任一已存父路径是 symlink 也会 fail closed；不允许用 alias 绕过审核路径身份。

在最后 event publish 前，机器再次完成：

1. 重读 R2a profile/implementation 与 R1 profile/implementation 绑定；
2. 复验 R1 candidate receipt、current artifact bytes 与空 future namespace；
3. 复验 executable capsule 及其 content-addressed profile/implementation provenance；
4. 逐文件复验 exact materialized tree；
5. 复验 smoke receipt 与当前烟测语义。

追加后还必须全链 replay，确认新 event 成为唯一 tip，再恢复 head/status cache。缺 R1
candidate 是成功等待 `waiting_for_immutable_candidate`；正常完成和 current repoll 均是
`executable_candidate_prepared`。不得仅根据可变 status 判断 candidate 权威。

## 9. 未实现声明与 R2b

R2a event/status/capsule/smoke receipt 一致固定：

```text
old_epoch_drain_implemented = false
active_epoch_switch_implemented = false
automatic_epoch_rotation_implemented = false
trusted_anchor_receipt_verified = false
e2_live_evidence_eligible = false
real_activation_ready = false
formal_warning_output = false
```

下一切片 R2b 首先实现 machine `epoch_drain_started` barrier/assessor，不直接跳到
`SEALED(old)+ACTIVE(new)`。在 assessor 能宣布 drain authority 之前，必须先关闭：

1. 所有可能创建旧 epoch issue 的入口，而不只是 cycle-v3 中的指定路径；
2. trusted-time 历史 request 的机器恢复，使已存在请求不因新 drain 边界丢失；
3. orphan guard intent 的恢复/裁决语义，避免崩溃窗口被误解为可切换；
4. 旧 activation-prefix route 的识别与 fencing，不让旧 namespace 绕过 epoch 状态；
5. scheduler dispatch fencing，使 drain 后任何旧 entrypoint 都不能被调度。

上述全部是机器状态与可恢复屏障；不添加人工日期、冻结、批准或 `--force`。
只有 R2b assessor 证明旧 epoch 不再能新增 issue，并将已存 issue/guard/trusted-time/
outcome/revision/shadow 全部收口后，后续切片才能追加权威 active transition。

## 10. 已知非阻断 P2 与证据边界

当前 capsule 与 lifecycle 独立审计均没有新增 P0/P1；以下 P2 不改变 R2a 的 machine-only
准备状态，但限制了证据能够支持的结论，并应在后续 hardening 中关闭：

1. 静态 import 检查目前使用有限 AST denylist。当前审核的精确 22-module bytes 已通过，
   但未来 module 版本应改为更强的语法/调用 allowlist，不能把本次通过外推到任意新代码。
2. orphan capsule 或 materialized tree 从来不是 authority；只有成功追加并完整 replay 的
   preparation event chain 才能授权当前 tip。orphan 只能在重烟测、比对和重新发布屏障后
   被机器恢复。
3. root smoke 会 compile 全部 22 个模块，但运行时只 import closure 的 root modules；
   trusted domain 只 import core。它尚未覆盖 launcher import 或独立 crypto self-test，因而
   不能声称执行了每个模块的全部运行路径。
4. `manager.lock` 与 hash chain 约束协作 writer 和可检测历史，但不能完全防御同 UID
   非协作 writer、路径检查与使用之间的 TOCTOU，或拥有全部存储写权时的全历史重写。
   因此 `trusted_anchor_receipt_verified=false`，本地链不等于外部不可篡改锚。
5. distribution inventory 只内容寻址规范化 name/version，没有哈希 wheel、扩展 `.so` 或
   系统动态库 bytes；这是 `portable_offline_runtime=false` 的另一明确原因。
6. 异常发生时，旧的 mutable status cache 可能保留。freshness 必须由本次 poll 成功退出并
   replay event chain 共同证明，不能只读取缓存中的旧成功状态。
7. 部分发生在 acquire-lock/profile 前置阶段的 `RegistryError` 尚未全部归一为 R2a CLI
   blocked/exit 2。这是可观察错误分类的 hardening 缺口，不改变 event-chain authority，
   也不能被解释为 fail-open。

## 11. 验证记录

- epoch-preparation 定向终审：`30/30`（590.587 秒，0 failure / 0 error）；
- epoch-preparation + pipeline 最终组合：`63/63`（597.651 秒，0 failure / 0 error）；
- 全仓回归：`796/796`（3047.808 秒，0 failure / 0 error）；
- `ruff check`、`compileall`、R2a scoped `ruff format --check` 与 `git diff --check`
  均通过；全仓历史格式基线未被批量改写；
- strict JSON 为 `28/28`，根项目与 trusted-time 子项目 `uv lock --check` 均通过；
- 默认 dry-run 仍严格为 `features -> convlstm -> ootang-operational-v4`，R2a 显式
  dry-run 只选择自身，`--list` 为 `29/29`；
- v5 frozen preflight 为 `23/23`，报告仍为 G0 PASS、G1--G4 BLOCKED、G5a 未评估且
  未授权，formal warning 为 false；
- 当前 profile/module/test SHA-256 已与本文页首、源文件常量和测试文件交叉核对；
- R1 已由提交 `3d6ce8f` 固定；97 条保护路径无相对 HEAD 漂移，聚合 SHA-256 实测仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。

另执行了一次不注入 prebuilder、不缩短 epoch、不改机器时钟的隔离真实链：R1 从
2020-07-01 单条 finalized feed 启动正式五种子 × 120 epoch 训练，五个 checkpoint 均按
create-only 内容寻址落盘；随后 R1 在发布 candidate 前按设计阻断，错误为
`Bundle was not durable before the first target natural day`。原因是当前仓库没有从
2020-07-01 连续到 2026-08-27 的机器 finalized feed，首个预测目标早已成为历史日期。
因此本次没有生成 R1 candidate，也没有执行到 R2a default 双域 smoke；不得把它写成真实
端到端 PASS。正确的机器行为是等待真实连续 feed，而不是改系统时钟、回填合成日期、
人工 backdate 或用 test-epoch override 做绿。真实 default R2a smoke 仍保留为 P2 验收缺口，
已通过的是前述 contract tests、真实 frozen 环境指纹核对和两轮无 P0/P1 的独立只读审计。
