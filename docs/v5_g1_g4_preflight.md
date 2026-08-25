# 藕塘 v5 G1--G4 预检盘点与待决策登记

> 建立日期：2026-08-20<br>
> 状态：`g0_pass_g1_g4_blocked`<br>
> 适用范围：藕塘 G1--G4 证据盘点；不授权训练、推理、融合或正式预警

## 1. 当前结论

本轮只关闭工程上能够由现有仓库证据回答的问题，不把缺失的现场事实或
导师决策补写成已完成：

- G0 数值正确性：`PASS`；
- G1 独立标签冻结：`BLOCKED`；
- G2 时间与未见确认集冻结：`BLOCKED`；
- G3 unavailable 部署政策：`BLOCKED`；
- G4 指标、数值门槛与置信区间协议：`BLOCKED`；
- 因而 G0--G4 预检未通过；G5a 未评估且未授权。

机器状态源是 `config/ootang_v5_gate_register.v1.json`。文档用于解释证据与
待决策项；不能仅修改本文把任一 Gate 改成 PASS。v1 是不可就地晋升的
blocked snapshot；未来解除阻断必须新建 schema/version、证据 manifest 和
相应 validator，经审查后切换生产默认源。

## 2. 当前数据与时间暴露盘点

### 2.1 物化数据范围

| 项目 | 当前事实 |
|---|---|
| 藕塘运动学范围 | 2016-07-01 至 2020-06-30，共 1,461 日、8 点、11,688 点日 |
| ConvLSTM fit | 2016-08-06 至 2019-02-02，911 日、7,288 点日 |
| 自动 V0 selection window | 2016-07-01 至 2019-02-02，每站 947 行、共 7,576 点日；前 36 日仅用于 fit 截止日内的 V0 自动分段 |
| calibration | 2019-02-03 至 2019-09-17，227 日、1,816 点日 |
| historical test | 2019-09-18 至 2020-06-30，287 日、2,296 点日 |
| v5 candidate-display | calibration + historical test，共 514 日、4,112 点日 |

上述边界来自 `data/ootang_kinematics_long.csv`、
`figures/convlstm/forecast_predictions.csv` 与
`figures/convlstm/forecast_run_manifest.json`。数据血缘审计还记录：当前发布物
是物化日序列，原始观测锚点和精确生成算法不可得，是否使用未来锚点仍为
unknown；因此它本身已经阻断确认性日预测和正式预警。

ConvLSTM/model fit 与自动 V0 selection window 是两个不同边界：前者因预测
可用性从 2016-08-06 开始；后者按 runner 的实际合同读取全部
`date <= 2019-02-02` 的运动学，因此从 2016-07-01 开始。不得把 model-fit
起点误写成自动 V0 起点，也不得把 2016-07-01 至 2016-08-05 用于模型训练。

### 2.2 为什么没有 unseen 时间块

- 2018-02-21 至 2020-06-30 已在三折外层滚动验证中依次作为 test；
- 2019-09-18 至 2020-06-30 的 historical test 已被预测、v4、代理 NGBoost、
  v5 candidate-display 和多份专家审查反复查看；
- 更早日期已进入 fit、calibration、V0 选择或稳定段审查；
- 当前仓库没有 2020-06-30 之后的藕塘数据，也没有封存确认块的 custodian
  manifest。

因此不能从现有范围重新切出一段并称为“未见确认集”。若没有新增、未曝光且
血缘可追溯的数据，本项目只能保持 `retrospective_internal_only`。

## 3. G1 独立标签盘点

当前仓库没有独立五级现场真值、事件 CSV 或标签 manifest。已有“标签状”内容
均不满足正式标签条件：

| 现有内容 | 不可作为正式标签的原因 |
|---|---|
| NGBoost `next_calendar_day_raw_interval_proxy_level` | 由预测区间位置生成，manifest 明示不是 expert/field truth |
| v4 station/site 等级 | 透明规则的 `operational_draft_not_formal` 输出，是待评价比较器而非真值 |
| v5 `interval_level` 与 `v4_reference_*` | 候选展示和历史参考，明确不主张 expert/field truth |
| 历史 SHAP 二元标签 | 由规则阈值派生，不是独立事件结局 |
| 历史 onset 标签 | 由规则 yellow+ 派生，且只有少量回顾规则事件 |
| 现有专家审查文档 | 已看模型/规则输出后的复算与解释，不是盲态双人标注和独立仲裁 |

解除 G1 至少需要以下二者之一：

1. 带信息可见时间的现场事件、工程处置或宏观变形记录；或
2. 不知道 v4、candidate-display、NGBoost 输出的双人标注与独立仲裁。

标签 manifest 必须冻结目标单位、类别语义、`positive/negative/unknown`、
信息可见时间、证据来源、事件/类别/unknown 计数、标注者与仲裁、生成代码和
所有输入哈希。五级证据不足时，应在看模型结果前改为较少级别或二元目标。
`unknown` 必须保留，永不转为阴性或绿色。

## 4. G2 时间协议待决策项

以下项目均不能从历史 test 表现反推：

- 唯一主预测时间窗 `H`；
- 正式发布单位（测点、滑坡体或分别评价二者）；
- 发布时间 `t` 的 as-of/迟到数据规则；
- 新确认块的起止日期、来源和与现有时段不重叠证明；
- `selection_used=false` 的封存声明；
- 解封负责人、一次性运行批准人和失败后的版本升级规则。

新的 confirmation manifest 只能登记元数据和哈希；预检不得读取封存 payload。
Vajont 不在本协议授权范围内，不能自动充当外部确认集。

## 5. G3 unavailable 的工程事实与推荐契约

### 5.1 当前覆盖事实

| 覆盖层 | 当前候选事实 |
|---|---|
| V0 可用站点 | MJ1、MJ3，共 2/8 = 25% |
| V0 可用点日 | 1,028/4,112 = 25% |
| V0 unavailable 点日 | 3,084/4,112 = 75% |
| 空间块 | MJ1/MJ3 均在 O1；当前覆盖 1/3 空间块 |
| 当前正式滑坡体发布 | 0/514；candidate-display 不生成 site color |

这些数值只能说明当前架构覆盖，不能用来倒推一个恰好让方法通过的
`coverage_min`。

### 5.2 可先冻结的 fail-closed 行为

- V0 unavailable 时，V0、速度比、V0 依赖切线角和分支分数保持空；
- unavailable 必须是 abstain/not-applicable，不得编码为 green、negative 或
  零风险；
- 禁止插补、跨站借值、使用 v4 比较器/常数 V0、权重重归一或放宽分段门；
- `both_available` 才能进入双分支候选融合；`ngboost_only`、`v0_only` 只能按
  单分支命名；`neither_available` 无输出；
- 任一正式 site 规则要求的站点/空间块覆盖不足时必须 abstain；
- 若未来允许 mixed-availability 发布，必须作为新方法单独冻结和验证。

仍须用户/导师批准：正式部署范围、complete-case 或 mixed-availability、覆盖率
分母、site-level 行为，以及是否采用“至少 3 个可评估点且 O1/O2/O3 各有
一个”的保守空间门。该空间门来自当前 v4 工程草案，不是已验证的正式规则。

## 6. G4 指标与 CI 的预登记建议

### 6.1 覆盖率应拆分

- `station_day_output_coverage`：有效输出点日 / 预定部署的全部点日；
- `site_day_output_coverage`：有效滑坡体结果日 / 全部计划发布日期；
- `spatial_complete_coverage`：满足获批空间门的日期 / 全部日期；
- `event_window_coverage`：提前窗内至少有一次可评价输出的事件 / 全部事件；
- `class_conditional_coverage[k]`：类别 k 中有输出单位 / 类别 k 全部单位。

unknown 不进入有标签性能分母，但仍保留在部署覆盖分母。系统级事件召回中，
无输出事件应计为 missed；仅在有输出子集上的指标只能作为条件诊断。

### 6.2 主指标、基线与区间

共同主门保持事件召回率与每 100 个阴性发布单位日的误报警率；accuracy 不作
通过指标。B0--B5 使用相同标签、发布时间、H、覆盖分母和配对抽样；B1 只能
持续发布时间真正可见的状态，不能偷用事后真值。

建议预登记但尚未批准的 CI 方案：

- 两个共同主指标各用 97.5% 单侧界；
- 事件召回按独立事件 ID 用 exact Clopper--Pearson 下界；
- FAR、覆盖率、有序指标和模型差值用配对移动日期块 bootstrap；
- 每次抽完整日期块并保留全部测点，块长 `max(H, 7)` 日，10,000 次，固定
  seed；不得把高度相关的 station-day 行当独立样本。

下列数值仍必须由用户/导师在看正式结果前批准并写入新版本 register：`R_min`、
`FAR_max`、各层 `coverage_min`、fusion 增量指标与 `delta_min`、FAR 非劣效界、
最少独立事件数、最少阴性单位日和逐类最低支持量。任一字段为 null 时 G4
保持 BLOCKED。

## 7. 机器预检与后续动作

`code/warning/ootang_v5_gate_preflight.py` 只校验 v1 register 的固定结构、
决策、证据路径和 SHA-256；它不读取缺失的标签、不训练模型、不运行推理、
不生成融合或颜色，也不评估或授权 G5a。当前预期：

```text
G0=PASS
G1=BLOCKED
G2=BLOCKED
G3=BLOCKED
G4=BLOCKED
g0_g4_preflight_passed=false
g5a_authorization_evaluated=false
g5a_authorized=false
```

默认 CLI 和生产 `require_g0_g4_preflight()` 固定读取唯一默认 register，不接受
替代 path/root。未来 G5a runner 必须在读取标签/训练数据或创建任何输出之前
先通过该预检，再通过另一个验证版本化 G5a run contract 的专用 guard；后者
至少要冻结特征、搜索空间、随机种子、calibration/失败规则、输入和实现哈希及
一次性输出 namespace。G0--G4 PASS 只是必要条件，静态 stage 选择不能替代
任一业务门。

## 8. 本轮审计日志

| 日期 | 动作 | 结论 |
|---|---|---|
| 2026-08-20 | 标签与标签状产物盘点 | 没有独立正式标签；G1 BLOCKED |
| 2026-08-20 | 全部藕塘时段和滚动验证暴露盘点 | 当前范围无 unseen 块；G2 BLOCKED |
| 2026-08-20 | 自动 V0/v5 可用性统计 | 2/8 站、25% 点日；site 正式发布仍为 0 |
| 2026-08-20 | G3/G4 工程契约草拟 | 可冻结 fail-closed 行为和指标公式，审批与数值门仍缺失 |

## 9. 实现验证

最终机器报告为 `G0=PASS`、`G1--G4=BLOCKED`、
`g0_g4_preflight_passed=false`、`g5a_authorization_evaluated=false`、
`g5a_authorized=false`。验证结果：

- G0--G4 预检模块：23 项测试通过；
- 自动 V0、候选展示、预检与主流水线定向回归：71 项测试通过；
- 全量 unittest discovery：353 项测试通过；
- `ruff`、`compileall` 与 `git diff --check` 通过；
- 默认 dry-run 仍为 `features -> convlstm -> ootang-operational-v4`；
- 显式 dry-run 仍为 `ootang-auto-v0-direct-bai-perron ->
  ootang-v5-candidate-display`；
- 97 个受保护路径与共享 v4 Bai--Perron 源无工作树漂移。

复跑命令：

```bash
uv run python code/warning/ootang_v5_gate_preflight.py --report
uv run python -m unittest tests.test_ootang_v5_gate_preflight
uv run python -m unittest \
  tests.test_auto_v0_direct_bai_perron \
  tests.test_ootang_v5_candidate_display \
  tests.test_ootang_v5_gate_preflight \
  tests.test_main
uv run python -m unittest discover -s tests
uv run ruff check code tests main.py
uv run python -m compileall -q code main.py tests
git diff --check
uv run python main.py --dry-run
uv run python main.py --dry-run \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display
test "$(wc -l < docs/v5_v0_protected_paths.txt | tr -d ' ')" = 97
xargs git diff --exit-code -- < docs/v5_v0_protected_paths.txt
git diff --exit-code -- code/warning/bai_perron_initial_slope.py
```
