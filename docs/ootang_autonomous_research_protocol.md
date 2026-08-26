# 藕塘机器闭环科研协议：预序预测、连续异常与证据递进

> 建立日期：2026-08-26<br>
> 状态：`machine_closed_loop_research_protocol_v1`<br>
> 适用对象：藕塘 8 个位移监测点的机器自主科研监测轨<br>
> 首版实现合同：`config/ootang_prequential_monitor.v1.json`<br>
> 输出边界：`retrospective_prequential_self_supervised_not_confirmatory`<br>
> 当前证据状态：`E0(replay/ledger 核心)=已实现`、`E1=已实现`、
> `E2-A=工程 runner 已实现但真实激活门禁未满足`、`E2-B=待实现`、`E3=BLOCKED`

## 1. 决策摘要

本协议建立一条不依赖逐次人工冻结、人工挑段、人工选阈值或人工按日操作的
机器闭环科研路线。系统的直接任务是：

1. 对 8 个测点的下一日累计位移作预序（prequential）预测；
2. 在真实位移随后可见时，计算“实际位移高于先前预测”的单侧连续残差异常；
3. 用已经揭示的历史结局自动更新预测专家权重、conformal 区间、ACI
   状态与漂移状态；
4. 在机器证据不足、输入不完整、状态重热或实现合同失配时自动回退或
   `abstain`；
5. 当前 E1 将每批签发写入 run-wide issue-only 审计链，并绑定逐点状态前后哈希；
   E2-A 已把签发、揭示、修订和状态更新写入 append-only 事件账本；E2-B 再把
   checkpoint 推理、输入语义、可信时间证明和自动 epoch 接入同一机器闭环。

这条路线与现有正式 v5 门禁并行，不绕过也不改写 G1--G4。它解决的是
“机器能否在严格时序下持续预测、量化不确定性并发现运动学新颖性”，不是
“机器是否已经证明了灾害风险”。没有独立灾害结局时，自动异常分数仍然只是
模型残差的新颖性度量，不能被循环用作自己的灾害真值。

首版坚持简单、可审计的组合：5 个固定 seed 的严格时序 OOF 预测加
persistence 专家、连续单侧残差异常、O1/O2/O3 连续空间聚合。输出不生成
五级颜色、行动阈值、正式报警或灾害概率。

## 2. 与“人工冻结”的区别

### 2.1 不需要逐日人工操作

以下是 E2 live runner 的目标正常运行行为。E1 runner 已自动完成历史日期
prequential replay；E2-A runner 已实现新数据发现、跨进程等待/恢复、真实文件
级 issue/outcome 隔离和完整 append-only 事件账本。由于当前尚未在 runner 内部
重放 checkpoint 推理、验证 input-manifest 语义、验证可信密码学时间回执或自动
轮换 immutable epoch，E2-A 仍不能产生 E2 live evidence；这些是 E2-B 门禁。

激活一个协议版本后，正常运行中的下列动作全部由机器完成：

- 校验输入 schema、配置、代码、模型和上次状态哈希；
- 发现新日期，按日期推进状态机；
- 同日一次性签发 8 个测点的预测；
- 封存 issue batch 并追加哈希链；
- 在结局可见后统一揭示、评分和更新；
- 自动重训练或更新合同已经允许的模型状态；
- 自动更新 expert 权重、conformal/ACI 和漂移检测器；
- 自动选择已预定义的 fallback，或拒绝输出；
- 在没有新数据时保持 `waiting_for_new_data`，不伪造日期、不补写结果。

因此，版本化配置不是“每天等待某个人批准后才能运行”的开关。它是一个
**复现契约**：机器可据此判断今天的计算是否仍与昨天属于同一个科学实验。

### 2.2 哪些变化仍必须增加版本

下列变化改变了研究问题或评价口径，必须生成新版本并开始新的 ledger epoch：

- 预测目标、时间跨度、as-of 规则或发布单位改变；
- 测点/空间拓扑、特征定义、训练窗口或模型族改变；
- seed 集、expert 更新公式、conformal 目标覆盖率或漂移算法改变；
- fallback/abstain 规则、连续空间聚合公式或评分指标改变；
- 引入新的独立灾害结局源或改变结局定义。

这不是逐次人工冻结，而是禁止同一个实验标识在运行中悄悄改变含义。合同内
预先声明的滚动更新、自动重训、专家再加权、ACI 和漂移重置属于正常机器状态
转移，不需要新版本，也不需要人工逐日确认。若未来允许机器自动生成 challenger
并晋升，它只能在当前版本已经锁定的候选空间、时序评分和晋升规则内进行；机器
不能根据未来结局反向改写这些元规则。

### 2.3 首版配置至少锁定的字段

机器合同至少包含：

- 8 个测点、O1/O2/O3 映射和预测目标；
- 数据路径、必需列、日期与站点自然键、允许的迟到/修订规则；
- 严格 as-of 特征规则、预测跨度和 fold 边界；
- 模型代码、输入、训练产物、配置和依赖环境的 SHA-256；
- seed=`0,1,2,3,4`、persistence 专家和聚合损失；
- expert 学习率、最小/最大历史长度与失败处理；
- 绝对残差 conformal nonconformity、单侧正残差 p-value 与连续 anomaly score；
- conformal quantile、ACI 参数、漂移检测参数；
- warm-up、fallback、abstain、缺失和哈希失败策略；
- ledger 规范化、batch 顺序、原子写入和恢复规则；
- 产物状态及明确禁止的正式预警字段。

配置内容哈希、实现哈希和输入 manifest 哈希必须共同进入每个 epoch 的 genesis
记录。配置文件被原地修改时，运行必须 fail closed，而不是把变化自动吸收到旧
实验中。当前 E1 将这些绑定写入 bundle manifest，并以全零 previous hash 开始
issue chain；独立的 `epoch_genesis` 事件属于 E2 完整 ledger。

## 3. 证据阶梯 E0--E3

| 层级 | 机器要证明什么 | 当前可做性 | 允许的表述 | 不能推出什么 |
|---|---|---|---|---|
| E0 工程完整性 | 输入、代码、配置、模型、状态、输出和执行顺序可验证；同一自然键不被静默覆盖；失败可恢复 | E1 replay 与 E2-A ledger/恢复/修订重放已实现；checkpoint inference、manifest 语义、可信时间与自动 epoch 待 E2-B | “已实现子项完整、可复算”，并逐项列出未实现项 | 预测有效、异常真实、灾害风险 |
| E1 历史 prequential 回放 | 对已有 OOF 预测按日期模拟先 issue、后 reveal；任何更新只消费较早结局 | 现在可做 | “内部回顾性预序评价” | 历史盲测、确认性效果、未来泛化 |
| E2 未来 append-only 盲态运动学证据 | 对激活水位线之后自然到达的新数据，在目标结局可见前真实签发并封存预测，随后自动评价 | E2-A 工程状态机已实现但证据资格固定为 false；E2-B 四项激活门禁待实现 | 只有可信部署后真实积累，才可写“前瞻盲态运动学预测/校准证据” | 独立灾害结局、灾害风险或正式报警能力 |
| E3 独立灾害结局 | 使用与本模型输出相互独立、带信息可见时间的现场事件/处置/失稳结局评价 | `BLOCKED` | 只有数据与协议齐备后才可按新版本表述 | 当前不得声称 event recall、FAR 或灾害效能 |

### 3.1 E0：工程完整性

E0 的通过条件至少包括：

- 配置 JSON schema、输入 schema、自然键和数值有限性校验通过；
- 5 个 seed 对同一 `fold/date/station` 的 `actual`、`persistence` 和输入血缘一致；
- E1 从源 manifest 锁定的 Git commit 取回 `data/features.csv` blob，
  逐字节核对 SHA-256/大小，并逐行验证 `actual` 与上一自然日
  persistence，不把预测 CSV 中自报的 persistence 当作因果证据；
- 运行只读取已列入 manifest 且哈希匹配的输入；
- 同一 fold 内日期严格递增，同一日期恰有预期的 8 个测点；
- issue batch 在 reveal 前完成并封存；
- 每个更新后的在线状态都有前态哈希、触发事件和后态哈希；
- staged CSV 必须以预声明的 binary64 round-trip 规则重读，并在原子
  提升前从落盘数据重算 issue chain、在线状态数学、site 聚合与 metrics；
- 重跑相同 epoch 时要么字节级复现，要么明确拒绝重复自然键；
- 临时目录、原子提升和中断恢复不会把半成品伪装成完成产物。

E0 是必要条件，不是模型表现门。即使 E0 全部通过，E1--E3 仍须分别报告。

### 3.2 E1：历史预序回放

E1 使用现有 5-seed 时序 OOF 预测，并把每一折视为独立的 **online-state
epoch**。折开始时重置在线 expert、conformal/ACI 和 drift 状态，禁止让前一折
的 test 结局成为下一折的隐性校准数据。当前三个 fold 的 issue batch 同时串入
一条 run-wide retrospective audit chain；该链跨 fold 延续不代表在线状态跨折
延续。每个历史日期都必须执行第 5 节的“同日 8 点两阶段 batch”。

这里的 OOF 约束只说明模型预测本身来自相应训练窗之外；整段历史已经被项目
反复查看，且上游物化日序列的数据血缘仍有未关闭问题。因此 E1 issue chain 是对
算法顺序的可复算模拟，不是当年真实产生并经外部时间戳封存的盲态 ledger。
任何结果固定标注 `retrospective_prequential_self_supervised_not_confirmatory`。

### 3.3 E2：未来追加式盲态运动学证据（E2-A 已实现，E2-B 尚未部署）

E2 的 genesis 记录机器激活时的数据水位线；按当前仓库证据，现有藕塘序列上限
为 2020-06-30。只有水位线之后自然到达、且在 issue 时目标值尚不可见的日期，
才可计入 E2。系统没有新数据时自动保持 `waiting_for_new_data`；这不是失败，
也不允许通过复制、插值或回填历史日期制造“新样本”。

若系统一次收到一批已经包含其结局的迟到历史数据，只能把这些记录归入
`backfill_not_blind`，不能事后生成 E2 issue。系统可以用这批数据更新到最新
as-of 状态，再对尚未可见的下一目标日签发真正的 E2 预测。

E2 ledger 必须 append-only：原 issue、首次接受的 outcome、后续修订和重算
结果均以新事件追加，禁止覆盖。仅有本地 SHA-256 链可证明内部一致性，但不能
单独证明原始签发时间；若要作较强的盲态证据，机器应把每日 batch root 自动
锚定到独立时间戳或 WORM/对象锁存储。该锚定仍是自动过程，不要求人工按日
操作。

E2 只评价位移预测、区间覆盖、残差新颖性、漂移和拒绝行为。它仍然不是 E3。

当前 E2-A 的可执行边界是：严格加载结构化 source/model/issue/outcome 文件、
追加和完整重放账本、自动等待/回填/修订及外部锚接口。它不解析 issue 引用的
input manifest 语义，也不从五个 checkpoint 重放预测；HTTPS 回执没有 pinned
provider 或密码学 verifier；模型/源码/环境变化会 fail closed，尚未由机器自动
建立新的 immutable epoch。因此所有 E2-A settlement 固定
`e2_live_evidence_eligible=false`，即使时间顺序可形成工程候选也不得升格。

### 3.4 E3：独立灾害结局

E3 至少需要一个与本系统分离的、机器可读且带信息可见时间的结局源，例如独立
现场事件系统、经锁定规则生成的工程处置记录或独立仪器确认的宏观失稳记录。
若项目坚持不使用人工标注，可以接入独立、自动记录的结局源；但不能让待评价
模型的预测、残差、颜色、V0、聚类或阈值反过来生成其自己的“真实事件”。

在结局定义、`positive/negative/unknown`、事件窗口、迟到规则、来源哈希和评价
协议均未进入新版本前，E3 保持 `BLOCKED`。机器自动化不能从无标签运动学中
创造独立灾害事实。

## 4. 预测对象与严格 as-of 规则

首版主单位为 `station × target_date`，目标是下一日累计位移：

\[
y_{i,t}=D_{i,t}, \qquad
\widehat y_{i,t\mid t-1}=f_i(\mathcal F_{t-1}),
\]

其中 \(\mathcal F_{t-1}\) 只包含目标日之前已经可见并通过 schema 校验的数据。
persistence 专家为上一可见日累计位移 \(D_{i,t-1}\)。同时报告的预测日增量为
\(\widehat y_{i,t\mid t-1}-D_{i,t-1}\)，但首版残差直接在相同累计位移尺度上
计算，避免目标口径混用。

任何目标日的位移、由目标日位移计算的速度/加速度、目标日后才观测到的降雨或
库水位，都不得进入该目标日 issue。只有在 issue 时已经真实可获得的外生变量
预测或调度值才可进入，并必须另记其来源与 as-of 时间；不能把事后观测值伪装
成预报输入。

迟到或修订数据遵循：

1. issue 只使用当时 ledger 中已接受的最新版本；
2. outcome 首次满足合同的 finalization 条件后才 reveal；
3. 后续修订追加 `outcome_revision`，保留原值、原评分和原在线状态；
4. 可另行追加修订口径的离线重算，但不得把重算结果静默写回当时在线状态；
5. 日期缺口、重复自然键、站点集合不完整或 as-of 冲突触发自动等待或 abstain。

## 5. 每日闭环与“同日 8 点两阶段 batch”

本节中的“8 点”指 8 个监测点，不是每天 08:00。具体调度时钟可以由部署环境
配置，但科学顺序固定不变。

### 5.1 单个 issue 的跨日生命周期

每个预测记录按以下不可逆顺序推进：

```text
issue
  -> seal same-date issue batch (E1 audit chain / E2 append-only ledger)
  -> outcome reveal
  -> score
  -> online expert update
  -> conformal / ACI update
  -> drift update
  -> fallback or abstain state for the next issue
```

更新后的权重、区间和漂移状态只能影响后续日期，永远不能回写已经签发的预测。

### 5.2 阶段 I：先签发同日全部 8 点

对目标日 \(t\)，机器从日期 \(<t\) 的已封存状态构造只读 issue snapshot，然后：

1. 同时准备 ATU1、ATU2、ATU3、ATU4、ATU5、MJ1、MJ3、MJ9 的预测；
2. 对每点写入 expert 预测、issue-time 权重、聚合点预测、conformal 区间、可用性、
   fallback/abstain 原因和全部输入/状态哈希；
3. 在 issue 生成路径解引用任一同日 `actual` 字段前，完成 8 条 issue 记录；
4. 按固定站点顺序生成 canonical issue batch，计算 `issue_batch_hash`；
5. E1 把 batch root 串入 run-wide audit chain 并随整包原子提升；E2 把它追加到
   live ledger 并实时封存。

E1 的源 CSV 会在回放开始前整体载入并校验；因而它不能声称目标值在进程层面
物理不可见。它保证的是：同日 `actual` 不进入 issue 计算、issue-time 状态或
`issue_batch_hash`，并且不会先签发一个站、查看其同日实际值并更新空间或共享
状态，再签发其余站。只有 E2 才要求 outcome 在真实 issue 时尚不可见或由独立
存储隔离。

### 5.3 阶段 II：再统一揭示、评分和更新

只有阶段 I 成功封存后，机器才可以统一读取同日 8 点 outcome，并按固定顺序：

1. 将 outcome 在逻辑上链接到原 issue；E1 写入同一回放行，E2 追加独立 reveal 事件；
2. 计算每个 expert 和聚合预测的损失；
3. 用该日损失更新在线 expert 权重；
4. 计算单侧 residual nonconformity、经验 p-value 和连续 anomaly score；
5. 评价先前签发的双侧对称区间是否覆盖 outcome，并更新 conformal/ACI；
6. 更新 station-specific drift detector；
7. 计算 O1/O2/O3 和跨区连续分数；
8. 记录新状态快照及其哈希，供 \(t+1\) issue 使用；E2 再把该状态作为独立事件
   追加到 ledger。

E2 中若 reveal 阶段失败，已经封存的 issue 仍然保留；机器下一次从未结算 issue
恢复，而不是重发更有利的预测。若 issue 阶段未完整封存，则该 batch 不进入评分，
机器记录失败并按自然键幂等恢复。当前 E1 依靠整包临时构建和原子提升避免半成品，
自身没有逐事件恢复；E2-A 已以 SQLite 原子事务和 ledger 全重放实现这一能力。

## 6. 审计链与目标 append-only ledger

当前 E1 物化的是确定性的 **issue-only run-wide SHA-256 chain**：每个日期的
8 条 issue 在不包含同日 `actual` 的 canonical 载荷上形成 batch hash，链首为
全零 previous hash；station 状态另有 issue 前、reveal 更新后和漂移生效后的
状态哈希。E1 输出以临时目录构建后原子提升，但它不是逐事件追加的实时账本，
也不包含可信时间戳、outcome revision 或恢复事件。

E1 v1 的输出合同还固定 CSV 浮点写出为 `%.17g`、重读为
`float_precision="round_trip"`。runner 必须重读 staged station/site/metrics，
再完整重放站点在线状态、ACI、异常分数、漂移重置、空间聚合与
metrics；任一落盘篡改都必须在 promotion 前 fail closed。

以下 6.1--6.2 已由 E2-A SQLite ledger 实现并通过数学全重放；E1 issue-only
chain 仍不得与它混写。可信签发时间和 checkpoint 推理来源属于账本之外仍待
E2-B 关闭的 provenance 门禁。

### 6.1 事件与自然键

ledger 至少包含以下事件：

- `epoch_genesis`
- `issue_batch_opened`
- `station_issue`
- `issue_batch_sealed`
- `outcome_revealed`
- `score_recorded`
- `expert_state_updated`
- `conformal_state_updated`
- `drift_state_updated`
- `fallback_or_abstain_recorded`
- `outcome_revision`
- `epoch_closed`

`station_issue` 的自然键至少为
`protocol_version/fold_or_live_epoch/target_date/station`；重复键只能返回已有的同哈希
记录，内容不同则 fail closed。

### 6.2 哈希链字段

每条 canonical 事件至少记录：

- `sequence_id`、`event_type`、`event_time`、`target_date`、`station`；
- `issue_id` 或其父 batch/事件 ID；
- `protocol_config_sha256`、`code_sha256`、`environment_sha256`；
- `input_manifest_sha256`、`model_manifest_sha256`；
- `state_before_sha256`、`state_after_sha256`；
- 必要的预测、区间、异常、可用性和原因字段；
- `previous_entry_sha256` 与当前 `entry_sha256`。

`entry_sha256` 由确定性 canonical serialization 计算；键顺序、时间格式、浮点
序列化、缺失值和文本编码都必须写入合同。SHA-256 提供内容完整性，不提供作者
身份或可信时间，因此 E2 的强盲态证据仍需自动外部时间锚。

### 6.3 E1 与 E2 分账

历史 replay 与未来 live 运行必须使用不同 ledger namespace 和 genesis：

- E1 issue-only hash chain 证明“当前回放的签发批次按声明顺序生成并可复算”；
- E2 hash chain 加自动外部时间锚后，才证明“预测在结局可见前已经存在”。

两者不得拼接后统称为一个 blind record。

## 7. 首版预测专家：5-seed OOF + persistence

### 7.1 六个专家

首版只使用以下 6 个专家：

- `seed0_p50`
- `seed1_p50`
- `seed2_p50`
- `seed3_p50`
- `seed4_p50`
- `persistence`

5 个模型专家必须来自同一算法、相同特征合同与相同 fold 的 seed-stability OOF
产物，不能挑选历史表现最好的单一 seed。persistence 既是始终保留的朴素基线，
也是模型不可用时的机器 fallback 候选。

### 7.2 只用过去结局的在线聚合

对站点 \(i\)、已揭示日期 \(t\) 和专家 \(j\)，首版损失为：

\[
\ell_{i,t,j}=
\frac{|y_{i,t}-\widehat y_{i,t,j}|}
{\max_k |y_{i,t}-\widehat y_{i,t,k}|},
\]

若所有专家误差均为零，则当日归一化损失统一记零。首版固定 \(K=6\)，第
\(n\) 个 issue 的学习率为

\[
\eta_n=\sqrt{\frac{8\log K}{n}},
\]

第 \(n\) 个 issue 的权重只由前 \(n-1\) 个已揭示损失生成：

\[
w_{i,n,j}\propto
\exp\{-\eta_n\sum_{s<n}\ell_{i,s,j}\}.
\]

第 \(n\) 个 outcome reveal 后把 \(\ell_{i,n,j}\) 加入累计损失；第 \(n+1\) 个
issue 再用 \(\eta_{n+1}\) 重新计算权重。

issue 点预测是 6 个专家预测的加权平均。权重是站点特异的；当天 outcome
只有在 8 点 issue batch 封存后才加入累计损失。这个在线更新只优化下一日位移
预测损失，不代表学到了灾害风险。

首版最小历史为 60 个已结算日；conformal、单侧 anomaly 和 drift 的滚动历史
最多保留 180 日，expert 权重则按配置使用本 fold 内累计过去损失。具体数值以
配置哈希为准。v1 要求 6 个 expert 输入全部有限且血缘一致；任一 expert 缺失时
不能补零或悄悄改变 \(K\)，而应 fail closed。persistence 保留为可审计 shadow
fallback/基线，不在 v1 中把输入不完整伪装成有效 ensemble。

### 7.3 fold 与自动状态更新

E1 每个新 fold 的首个 issue 前重置所有 station online state；重置后的 60 日为
rewarm。E2 不按日人工重置，只按照配置中的自动漂移或版本切换规则建立新
regime/epoch。任何自动重训只能消费 issue 时已经揭示的数据，并把训练窗口、
随机 seed、输入与模型哈希追加到 ledger。

## 8. 单侧连续残差异常与双侧绝对残差 conformal/ACI

### 8.1 连续单侧 anomaly score

本项目关注模型低估后出现的正向位移偏离，故 outcome reveal 后定义：

\[
a_{i,t}=\max(y_{i,t}-\widehat y_{i,t},0).
\]

只使用 issue 前已经结算的同站历史 nonconformity 集合 \(A_{i,t}^{past}\)，计算：

\[
p_{i,t}=\frac{1+\#\{a\in A_{i,t}^{past}:a\ge a_{i,t}\}}
{1+|A_{i,t}^{past}|},
\qquad
s_{i,t}=-\log_{10}p_{i,t}.
\]

`s` 是连续 residual surprise，不做二值切割，也不映射为颜色。它不是
`P(landslide)`，不同历史窗长度下的最大可达分数也不同，所以报告时必须同时给出
历史样本数、原始 residual 和 p-value。这里的“经验 p-value”来自自适应滚动、
时序相关的残差秩；当前协议没有证明经典假设检验所需的超均匀性或 I 类错误控制，
不能把它解释成显著性水平、误报概率或 FAR。

### 8.2 绝对残差 conformal 区间

conformal 与单侧 anomaly 使用两个不同的 nonconformity 序列。前者使用
\(e_{i,t}=|y_{i,t}-\widehat y_{i,t}|\)，issue 时从同站过去绝对残差的有限样本
higher quantile 得到 \(q_{i,t}\)，签发对称区间：

\[
[L_{i,t},U_{i,t}]
=[\widehat y_{i,t}-q_{i,t},\widehat y_{i,t}+q_{i,t}].
\]

首版目标覆盖率为 0.8，初始 nominal miscoverage 为 0.2。该区间只回答“下一日
累计位移在当前预序误差机制下是否落入自适应误差带”，不回答灾害概率。正向
低估的新颖性仍由第 8.1 节的单侧 \(a_{i,t}\) 单独表达，不能把两者混为一列。

### 8.3 ACI 更新

在 outcome reveal 后令

\[
err_{i,t}=\mathbb 1[y_{i,t}<L_{i,t}\ \text{or}\ y_{i,t}>U_{i,t}],
\]

并对下一日更新：

\[
\alpha_{i,t+1}=
\operatorname{clip}
\{\alpha_{i,t}+\gamma(\alpha_0-err_{i,t}),
\alpha_{min},\alpha_{max}\}.
\]

首版配置为 `alpha0=0.2`、`gamma=1/180`、
`alpha_min=0.01`、`alpha_max=0.5`。ACI 的长期覆盖频率结论不能被写成每一天、
每一测点或任意分布漂移下都有条件覆盖保证。若未来使用 AgACI 或 SPCI，必须以
新配置版本比较区间宽度、覆盖和稳定性，不能只因为方法更复杂而自动替换首版。

### 8.4 warm-up

历史不足 60 个已结算日、漂移重置后尚未重热或 conformal quantile 不可计算时：

- 可以把 issue-time 点预测作为 shadow diagnostic 写入 ledger；
- 正式科研监测状态为 `abstain_rewarm`；
- conformal 区间、对外 p-value 和连续 anomaly score 均 unavailable；绝对残差
  surprise 只在内部用于漂移状态更新，不作为可行动输出；
- 不得用全历史、未来日期或其他站结局临时补足校准窗。

## 9. 漂移、fallback 与 abstain

### 9.1 自动漂移检测

首版在每个站点上监测有界的绝对残差经验 surprise rank，使用
`ADWIN-inspired bounded Hoeffding adaptive window`：`delta=0.002`、每侧最小
子窗 30、最大历史 180。这里明确写作 “ADWIN-inspired”，除非实现逐项满足原始
ADWIN 算法，否则不得声称就是论文中的完整 ADWIN。

漂移只能说明在线损失/残差分布发生了机器可检测的变化，不能自动解释为滑坡
进入加速阶段。检测到漂移后，机器在 reveal 完成后：

1. E1 在 reveal 行记录 drift 标志、cut index 与状态哈希；E2 追加独立 drift
   事件与检测窗口证据；
2. 封存旧 station state；
3. 自动重置该站 expert/conformal 状态并开始新的 regime；
4. 下一目标日进入 `abstain_rewarm`；
5. 只有达到最小过去样本量后才恢复 active forecast action、区间与对外连续
   anomaly；重热期只保留 point/persistence shadow 及内部绝对残差 surprise rank，
   后者仅更新 drift，不输出为 anomaly score。

### 9.2 fallback 优先级

首版按以下顺序机器处理：

1. 5-seed 与 persistence 均满足合同：计算在线加权 ensemble；
2. 历史不足 60 日：保留 ensemble 与 persistence shadow 诊断，但
   `issue_forecast_action=abstain`、区间 unavailable；
3. 漂移重置后：同样进入 `abstain_rewarm`，直到重新积累 60 个过去结局；
4. 任一 expert、最新位移、自然键、as-of、输入哈希或状态哈希不可靠：v1
   fail closed/`abstain`，不得缩小 expert 集合或把 persistence 晋升成未声明输出；
5. 只有未来新版本预先定义并验证 degraded expert-set 规则后，才可允许
   `persistence_only_fallback` 或部分集合重归一；
6. 跨区连续聚合缺少任一空间块时，site action 必须 abstain。

fallback 不是绿色或低风险，abstain 也不是阴性。所有 fallback/abstain 都保留在
计划发布分母中，并报告持续天数与原因。

## 10. O1/O2/O3 连续空间聚合

空间拓扑固定为：

- O1：MJ9、MJ1、MJ3；
- O2：ATU4、ATU5、ATU3；
- O3：ATU2、ATU1。

这一路线不再生成五级颜色。对已经 reveal 的站点连续分数
\(s_{i,t}\)，首版先计算分区局部连续分数：

\[
B_{k,t}=\max_{i\in O_k}s_{i,t},
\]

再计算跨三个分区同时性的连续分数：

\[
S_{site,t}=\min(B_{O1,t},B_{O2,t},B_{O3,t}).
\]

`max within block` 表示保留每个空间块最突出的局部 residual surprise；
`min across blocks` 是连续的跨区“与”聚合，只有三个块都高时才高。它不是概率，
也不是经灾害结局验证的空间风险函数。必须同时输出三个 block score、每块有效
测点数/应有测点数、贡献站点、全体 station score 与 site abstain 状态；不能只
保留一个 site 数值。

v1 的源合同要求同日 8 点完整；任一分区没有有效站点分数时整批 fail closed，
site action 自动 abstain。当前 E1 warm-up/rewarm 期间 station anomaly p-value/
score 与 block/site 连续分数均为 unavailable；只保留 point/persistence shadow
和内部 drift surprise rank，不能把短历史 surprise 解释为可行动输出。未来若
允许分区内部分缺失，block score 只能标注为
“available-subset diagnostic”，不得伪装成完整分区覆盖。
Wang 等（2025）只支持藕塘 O1/O2/O3 拓扑和分区内空间依赖建模，不支持这里的
max/min 聚合、异常阈值或任何预警含义。

## 11. 评价指标与自动报告

### 11.1 E1/E2 可以报告

完整 E2 运行及后续 E1 报告版本应按 fold/live epoch、站点、空间块和总体报告：

- point MAE、RMSE，以及相对 persistence 的配对 skill；
- 5 个 seed、persistence 与在线 ensemble 的逐日损失及权重轨迹；
- 预测日增量的偏差、方差与实际日增量相关性；
- 双侧绝对残差区间经验覆盖率、平均宽度、越界数和 ACI alpha 轨迹；
- residual、p-value、连续 anomaly score 的分布与最大值日期；
- drift 次数、检测日期、窗口长度和重热时长；
- 正常输出、degraded、fallback、warm-up 与 abstain 的计数和覆盖率；
- O1/O2/O3 block score、跨区 site score 和空间可用性；
- 所有输入、配置、代码、模型、ledger root 与输出 SHA-256。

当前 E1 v1 已直接物化 fold/station/overall 的 MAE、RMSE、persistence skill、
active 子集、覆盖率、区间宽度/interval score、abstention、drift 数和 anomaly
分布，并在逐日表保留 expert 预测/权重、ACI、drift、状态哈希与 block/site
连续分数。逐 expert 损失汇总、预测日增量 bias/variance/correlation、越界总数、
drift window/rewarm 时长汇总和 block 级评价表仍是后续报告合同，不能写成当前
已经全部物化。

所有模型比较必须使用相同日期、站点、as-of 信息和缺失政策。不能只挑 ensemble
优于 persistence 的日期，也不能挑选 5 个 seed 中最有利者作为主结果。

### 11.2 当前禁止报告

在 E3 通过前，不得计算或声称：

- 正式灾害风险、失稳概率或行动安全等级；
- event recall、event precision、FAR 或报警提前量；
- 五级颜色准确率、混淆矩阵或“预警成功率”；
- anomaly score 对灾害事件的 AUROC/AUPRC/F1；
- “历史盲测”“blind historical test”或确认性泛化；
- 对其他滑坡、其他设备、其他采样频率或外部场地的泛化；
- 因自动运行、哈希完整或 conformal 覆盖而宣称正式预警可用。

禁止使用 point adjustment、事后扩大事件窗、只要命中异常区间任一点就把整段记
正确、按结果挑选阈值等方式制造高分。没有独立标签时，连续 anomaly score 的
正确评价首先是时序完整性、可复算性、预测残差与稳定性，而不是伪造分类指标。

## 12. 机器状态机与实施顺序

推荐状态机：

```text
validate_contract
  -> replay_e1
  -> verify_e0
  -> initialize_e2_genesis
  -> waiting_for_new_data
  -> issue_8_station_batch
  -> seal_issue_hash
  -> waiting_for_outcome
  -> reveal_8_station_outcomes
  -> score_and_update
  -> active | degraded | abstain_rewarm
  -> waiting_for_new_data
```

遇到配置/实现/输入哈希不一致、自然键冲突或 ledger 链断裂时进入
`blocked_integrity`，机器不得自行忽略。没有新日期时保持 waiting；只有科学目标
或合同本身改变时才开始新版本。工程实施顺序为：

1. E0/E1 核心（已实现）：精确配置 validator、历史 Git blob 因果源
   核对、同日 batch 顺序、issue-only 审计链、状态连续性、落盘全重放、
   原子产物与三折历史 prequential replay；
2. E1 结果（已实现）：固化 manifest、局限声明和 machine-readable metrics；
3. E2-A 工程（已实现）：append-only event ledger、幂等恢复、outcome revision、
   issue/outcome 隔离、自动等待和不可信外部锚接口；
4. E2-B 机器工程（部分门禁已实现）：content-addressed 五种子模型、input-manifest
   语义、机器 outcome/cycle，以及指定入口的 checkpoint inference replay 已实现；
   pinned cryptographic time verifier、immutable epoch registry/自动轮换、scheduler
   entry authorization 与长链性能门仍待实现；
5. E2-B 门禁关闭后初始化独立 live epoch，由机器等待并处理自然到达的新数据；
6. 仅在独立结局源可用时，另开协议版本接入 E3。

这个顺序不要求人为为每个日期选择样本或点击批准，但要求机器在证据不足时诚实
等待或拒绝，而不是把“自动化”理解为“自动产生缺失的真值”。

## 13. 方法依据与适用边界

以下均为 primary-source 链接；引用只支持相应方法动机，不等于已经在藕塘得到
验证。

1. [Dawid（1984），The Prequential Approach](https://rss.onlinelibrary.wiley.com/doi/10.2307/2981683)：提出从连续给出的预测及随后观测到的结局评价统计模型，为“先 issue、后 reveal、只用过去评分更新”提供基本思想；它不自动把回顾性模拟变成真实盲测。
2. [Gibbs 与 Candès（2021），Adaptive Conformal Inference](https://proceedings.neurips.cc/paper/2021/hash/0d441de75945e5acbc865406fc9a2559-Abstract.html)：为未知分布漂移下的在线预测集调整误覆盖参数，并给出长期覆盖频率性质；这不是逐时条件覆盖或灾害安全保证。
3. [Zaffran 等（2022），AgACI](https://proceedings.mlr.press/v162/zaffran22a.html)：分析 ACI 学习率，并以在线 expert aggregation 聚合多个 ACI 学习率；它是后续区间自适应候选，不构成首版必须复杂化的理由。
4. [Xu 与 Xie（2023），SPCI](https://proceedings.mlr.press/v202/xu23r.html)：利用 nonconformity score 的时间依赖预测未来残差条件分位数，并在相应一致性条件下研究渐近条件覆盖；其假设和实现需单独验证后才能引入。
5. [Garg 等（ICLR 2022），Leveraging Unlabeled Data to Predict Performance Under Distribution Shift](https://openreview.net/pdf?id=wcrff7Gh0RR)：证明在不增加分布假设时，未标注目标域的真实准确率一般不可识别；这直接支持“无标签漂移不能替代独立结局”的边界。
6. [Garg 等（2021），多变量时间序列异常检测与诊断评价](https://arxiv.org/abs/2109.11428)：实验显示动态 scoring 可能比复杂底层模型更影响表现，简单逐通道模型配合动态评分也很有竞争力；这支持首版先做可审计 residual scorer 和朴素基线。
7. [Bifet 与 Gavaldà（ADWIN）](https://www.cs.upc.edu/~Gavalda/papers/adwin06.pdf)：用数据自适应窗口在线监测变化，在稳定时扩窗、变化时缩窗；本协议首版只声称 `ADWIN-inspired`，不挪用超出实际实现的理论保证。
8. [El-Yaniv 与 Wiener（2010），Selective Classification](https://jmlr.org/papers/v11/el-yaniv10a.html)：形式化了拒绝选项下的 risk--coverage 权衡；本协议借用“证据不足可 abstain”的原则，但其无噪声分类理论不直接证明本回归监测器的风险。
9. [Wang 等（2025），藕塘时空位移预测](https://doi.org/10.1029/2025JH000592)：用藕塘 8 点和 O1/O2/O3 分块建模时空依赖，并比较 ST-GNN、LSTM 与 GRU；本协议只采用其站点空间拓扑，不采用其结果作为真值或本项目泛化证据。
10. [Nava 等，位移残差揭示滑坡 regime shift](https://doi.org/10.1007/s10346-024-02353-2)：以预测残差识别相对既有动力学的异常偏离，并关注正向低估和多测点群体行为，为单侧 residual monitoring 提供直接领域动机；其站点、频率、模型与阈值不能移植为藕塘结论。
11. [Tang 等（2026），单质心 K-means + EVT 自适应无监督阈值](https://doi.org/10.1016/j.jrmge.2026.01.049)：提出随数据更新的无监督基线和 EVT 概率阈值，用位移、速度、加速度及降雨识别蠕动偏离；它可作为后续 challenger，但不能用自身异常阈值替代独立结局。
12. [Liu 与 Paparrizos（2024），TSB-AD](https://proceedings.neurips.cc/paper_files/paper/2024/hash/c3f3c690b7a99fba16d0efd35cb83b2c-Abstract-Datasets_and_Benchmarks_Track.html)：指出时间序列异常检测常受数据缺陷、偏置指标和不一致 benchmark 影响，并发现简单统计方法/架构常有竞争力；这要求本项目保留 persistence、统一时序评价且不使用有利的事后评分修饰。

其中 Nava 的 residual regime-shift 路线和 Tang 的自适应无监督 EVT 路线都是近年
领域方法，当前不能称为高引共识或通用行业标准；本项目只能把它们作为可检验的
方法依据。TSB-AD 同样提醒：论文中的高 anomaly 分数或 benchmark 排名不能替代
本场地的数据完整性与独立结局验证。

## 14. 本协议的最终边界

在当前资料下，本项目已经可以完全自动地完成 E1 历史 replay，以及 E2-A 的
live 状态机、append-only ledger、等待/恢复/修订和数学全重放。E2-A 仍是工程
基础，不是 live 证据：只有 E2-B 关闭 checkpoint 推理、输入语义、可信时间和
自动 epoch 四项门禁，并真实签发未来日期后，才可能开始积累 append-only 盲态
运动学证据。

E3 仍然 `BLOCKED`。在独立灾害结局不存在时，最科学的机器行为不是生成五级
颜色或给 anomaly score 起一个“风险概率”的名字，而是持续预测、诚实量化不
确定性、记录漂移、在证据不足时 abstain，并让未来数据逐日形成无法事后挑选的
证据链。
