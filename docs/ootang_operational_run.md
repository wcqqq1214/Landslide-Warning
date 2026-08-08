# 藕塘实施版运行说明（导师复核用）

> 当前运行配置：[ootang_operational_run.v3.draft.json](../config/ootang_operational_run.v3.draft.json)
>
> 保留对照配置：[v2](../config/ootang_operational_run.v2.draft.json)、[v1](../config/ootang_operational_run.v1.draft.json)
>
> 状态：`operational_draft`；用于先完整跑通藕塘案例并便于导师后续替换规则，**不是正式预警结果**。
>
> 执行决策（2026-07-30）：原始 GNSS 确认无法取得。`prototype_run_gate=allowed`，允许导师要求的高程感知工程初跑；`confirmatory_evidence_gate=blocked`，其颜色、速度、`ΔV`、切线角和区间仍不能升级为独立原始 GNSS 上的确认性证据。

## 1. 目的与边界

用户于 2026-07-23 授权按当前推荐技术路线先跑通藕塘，导师后续若提出方法调整，再替换相应规则。为避免这项工程授权被误写成论文方法已经完全冻结，本运行独立于正式协议：

- 基础协议仍是 [`ootang-five-level-rule-v1`](../config/ootang_warning_protocol.v1.draft.json) 的 `1.3-draft`；实施版配置锁定其内容 SHA-256 和七项未决项，任一漂移都会拒绝运行；
- 每份 CSV 和 manifest 均写入 `formal_warning_output=false`、`artifact_status=operational_draft_not_formal` 与 `vajont_used=false`；
- 不调用 [`formal_warning.py`](../code/warning/formal_warning.py)，不把发布序列相邻差分速度 KMeans 候选称为指定 Word 论文的 MVIF `V0` 实现；
- 参数只由 `split=fit` 的候选稳定段和运动学记录产生；calibration/test 仅执行，不反向选择参数。
- ConvLSTM 必须提供 [`forecast_run_manifest.json`](../figures/convlstm/forecast_run_manifest.json)，证明 `station_coords.csv::elev_m` 已作为静态高程通道进入预测，并使预测 CSV 哈希可与本运行互相核对。

因此，它是一份“可跑、可审计、可替换”的实施版，不是对阈值有效性、预警提前量或泛化性能的结论。

## 2. v1 对照运行（保留，不覆盖）

1. 先重建七份藕塘草案证据，核对基础协议指纹、输入来源和非正式标识；
2. 区间指标直接使用指定 Word 图 5-1 的 `μ+kσ` 五级区域；本项目近似为 `μ=P50`、`σ=(P90-P10)/(2×1.28155)`，且只在观测到同日位移后作状态识别；
3. 严格 MVIF 仍是指定 Word 路径的诊断基线。当前 8 点均为 `tf_multistart_unstable`，所以实施版显式改用 fit-only 的“发布序列相邻差分速度 KMeans 初始低速前缀”作为**项目特有运行基线**；这里不表示原始 GNSS 测量速度；
4. 对每个测点候选段的 `V`、`σ`、`V0=max(1.5V,V+2σ)`，把 `V0±σ` 定义为运行版的 blue 区间：

   ```text
   velocity < V0-σ                -> green
   V0-σ <= velocity <= V0+σ       -> blue
   V0+σ < velocity < 5V0          -> yellow
   5V0 <= velocity < 10V0         -> orange
   velocity >= 10V0               -> red
   ```

5. 切线角采用 `α=atan(v/V0)×180/π`。其 blue 区间由同一速度带映射得到；黄色、橙色、红色继续采用 `<80°`、`[80°,85°)`、`≥85°`；非日尺度/非有效时间行标为 `not_applicable`；
6. 对候选稳定段内的 fit-only `ΔV`，用 `τ=1.4826×median(|ΔV-median(ΔV)|)` 估计近零带（受最小数值下限保护），随后按 `ΔV<-τ`、`|ΔV|≤τ`、`ΔV>τ` 输出 negative/near_zero/positive；
7. 测点级沿用 [`rule_fusion.py`](../code/warning/rule_fusion.py) 的“两项佐证”候选：区间、速度、切线角任一非绿等级可支持该等级，positive `ΔV` 作为定性佐证；孤立异常保留为 `uncorroborated`，不伪装成 green；
8. 滑坡体级要求至少 6 个测点有有效测点结果，再取“至少 2 个有效测点达到或超过同一等级”的最高非绿等级。没有两点支撑的异常保留为 `uncorroborated`。

第 4、6、7、8 步都是项目特有运行约定，故以 JSON 的可执行范围表、`ΔV` 中心/容差和测点数参数表示；例如改 blue 带、`5V0/10V0`、`80°/85°` 或最少支撑数时，必须复制并提升配置版本再重跑，不需要改输入、预测或审计链路。

## 3. v2 空间证据族运行（保留快照）

v2 不改动 v1 目录、KMeans 对照基线、速度/切线角范围表或任何 fit-only 参数；它只替换此前审查明确存在语义问题的融合与滑坡体汇总方式。

1. 速度和切线角合并为一个运动学证据族，取两者等级的最大值；二者不再作为两张独立选票；
2. 测点候选等级为 `max(interval_level, kinematic_level)`；`ΔV=positive` 仅写入 `accelerating`，不单独升级五级颜色；
3. 单一证据族的非绿异常保留为可评估候选，例如 `interval_only_not_accelerating`，不再写成缺失或 `uncorroborated`；
4. 固定空间分区：O1=`MJ9/MJ1/MJ3`，O2=`ATU4/ATU5/ATU3`，O3=`ATU2/ATU1`。该拓扑只参考 Wang 等（2025，[DOI](https://doi.org/10.1029/2025JH000592)）PDF 第 7 页图 4(a,d) 和 5.2 节；配置与 manifest 锁定已审查 PDF 的声明指纹，本地副本存在时还会强制复核，**不**从该论文引申任何本项目的汇总阈值或正式预警规则；
5. 全滑坡体绿色要求至少 3 个可评估测点且三个分区均有覆盖。blue 仅在它是当日最高候选时可见，不要求跨区确认；黄色及以上则要求至少 2 个候选测点、跨至少 2 个分区达到或超过同一级别；
6. 若 yellow--red 最高候选未获相应跨区支撑，输出 `candidate_not_site_confirmed`，保留候选颜色、测点和分区，**不得因其他分区存在 blue 候选而降为 blue**；它不再被误称为“有效测点不足”。

这是一套项目特有、非监督、可替换的 v2 规则，不是指定 Word 的多项 Logistic 回归复现，也不解除正式协议中的任何未决项。

2026-08-01 修复了 v2 的一个未触发缺陷：`minimum_assessable_station_count=3` 现在先于任何颜色返回执行。修复只作用于不足 3 个有效点的情形；v2 原有的“全分区覆盖只约束 green”语义不变。当前 514 天均为 8/8 点有效，因此 v2 四份已提交产物的 SHA-256 在修复前后完全不变。

## 4. v3 双轴空间运行（当前）

v3 复用 v2 的逐点四指标、V0 对照基线、阈值、测点证据族和 O1/O2/O3 拓扑，只替换滑坡体空间决策。它同时回答两个不同问题：

- `site_confirmed_level`：当前证据能否跨测点、跨分区支持滑坡体整体等级；
- `local_max_candidate_level`：当天任一可评估测点的局部最高候选等级。

规则顺序固定为：

1. 任何滑坡体颜色都先要求至少 3 个可评估测点，且 O1/O2/O3 均有覆盖；不足时输出 `insufficient_assessable_coverage`，但仍保留局部候选；
2. 从 red 向 yellow 查找最高的“至少 2 点、至少 2 区”支撑等级；
3. 若存在 yellow--red 局部候选但没有获得上述确认，输出 `candidate_not_site_confirmed`，不得降为 blue 或并入 green；
4. 只有局部最高不超过 blue 时才判断 blue：至少 2 个 blue 点且跨至少 2 区才输出 site blue；
5. 其余覆盖完整日输出 site green；若仍有孤立或单区 blue，则另记 `localized_blue_attention`；
6. 所有分支都保留局部最高候选的等级、测点和分区。

这仍是项目特有的透明非监督草案。green 现在可表达“覆盖完整但没有跨区 blue+，且不存在未确认 yellow+ 候选”，并不等于经现场验证的安全状态。

## 5. 运行与产物

```bash
uv run python code/warning/operational_run.py
```

上面的命令保留并重跑 v1，并只写入 `figures/warning_operational_draft/`。当前 v3 入口为：

```bash
uv run python main.py \
  --stage features \
  --stage convlstm \
  --stage ootang-operational-v3
```

也可以直接运行：

```bash
uv run python code/warning/operational_run_v3.py
```

v2 仍可用 `uv run python code/warning/operational_run_v2.py` 单独重建。三个版本都会先刷新唯一的 `figures/warning_draft/` 输入证据集，再在临时目录写出并核验实施版文件，最后逐文件提升到各自目录：v1、v2、v3 分别拥有 `warning_operational_draft/`、`warning_operational_draft_v2/`、`warning_operational_draft_v3/`。通用运行器会拒绝任一版本写入另一个版本的保留目录。

v2/v3 仍要求本地存在高程感知预测清单。O1/O2/O3 的计算拓扑、Wang et al.（2025）的 DOI、页/图定位和已审查 PDF 的 SHA-256 已直接版本化在 profile 中，因此新克隆不需要分发论文 PDF 才能执行；若本地存在[该 PDF](../literature/Journal%20of%20Geophysical%20Research%20%20Machine%20Learning%20and%20Computation%20-%202025%20-%20Wang%20-%20Enhancing%20Landslide%20Displacement.pdf)，运行器会强制核对指纹，不匹配则拒绝。若本地副本缺失，manifest 会明确记录 `source_file_available_at_run=false` 和仅使用已锁定声明指纹的核验状态，不会从别的论文、网络副本或 Vajont 数据静默替代。

每个版本目录中都有以下同名文件：

- `ootang_operational_thresholds.csv`：8 点的候选 `V0`、速度/切线角 blue 边界与 `ΔV` 近零容差；
- `ootang_operational_station_timeline.csv`：calibration/test 每日、每点四项输入、状态、等级、测点融合理由与候选基线来源；
- `ootang_operational_site_timeline.csv`：每日的有效点数量、各级计数、贡献点、未获佐证点与滑坡体实施版状态；
- `ootang_operational_run_manifest.json`：基础协议/实施版配置/输入/输出哈希、结果状态计数和明确的非正式边界。

v2 的测点表新增 `station_assessment_status`、`candidate_level/color`、`kinematic_level/color`、`evidence_families`、`station_confirmation_status`，以及 `trend_component`、`transition_status`、`evidence_consistency_status`、`composite_warning_signal`。后三类字段把 `ΔV` 的负/近零/正状态实质保留在完整信号和理由中，但不让它凭符号改变五色严重度，也不把速度与切线角重复计票；`acceleration_status` 仅作为向后兼容字段。v3 完全复用这些测点值。v3 滑坡体表以 `site_confirmed_level/color`、`local_max_candidate_level/color`、`local_attention_status` 为规范双轴，同时保留 `site_level/color`、`site_candidate_level/color` 和 `candidate_stations/blocks` 兼容别名。v2/v3 中 `fusion_status=valid` 只表示四项输入可评估，**不再表示两项独立投票已佐证**。

这些文件均可从 manifest 中的路径与 SHA-256 复核。v2/v3 manifest 记录高程感知预测清单、预测哈希匹配状态，以及 Wang 等（2025）空间分区来源的 DOI、页/图定位、预期路径、已审查 SHA-256、本地副本可用性和核验状态。v3 manifest 还锁定运行器、测点融合和 v3 空间融合源码指纹，并汇总双轴等级及局部蓝状态。参数表不会因仅改变 test 期预测值而变化；该性质由集成测试覆盖。若发生 Python 可捕获的写入或提升错误，旧实施版快照会恢复；不宣称进程被强制终止或断电时的目录级事务。

v3 入口还会在核心 CSV/manifest 指纹全部匹配后生成 [`ootang_v3_typical_days.svg`](../figures/warning_operational_draft_v3/ootang_v3_typical_days.svg)、PDF、300 dpi PNG 和独立图件 manifest。六个代表日不是按视觉效果手选，而是按冻结语义规则取最早满足日：未确认 yellow、单区 blue 关注、O1 严重候选簇未确认、site yellow/local red、确认 orange 和确认 red。图件清单锁定规则配置、精确日期、所绘子集、渲染器和三个导出文件的 SHA-256；它明确属于观测后规则解释，不用于评价误报率、召回率或提前量。

同一入口还生成 [`ootang_v3_full_warning_timeline.svg`](../figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline.svg)、PDF、300 dpi PNG 和独立 manifest。上半图覆盖 514 日 × 8 点的测点候选五级状态；下半图同时显示滑坡体整体确认等级与局部最高候选。400 个整体未确认日统一画为灰色 `NC`，并在图题和 manifest 中固定为“空间佐证不足而非缺测”。该图使用目标日观测到达后的状态，因此仍不是严格前瞻预警或提前量证据。

为满足“累计位移、四指标和最终等级同图展示”，入口还生成 [`ootang_v3_all_station_combined_diagnostic.svg`](../figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic.svg)、PDF、300 dpi PNG 和独立 manifest。4×2 小多图逐点对齐 514 日累计位移，以及 interval、velocity、`ΔV` 三态、tangent angle 和 final candidate 五条色带；`ΔV` 不被伪装为五级。三类图件共用公开的输入/provenance/导出支持层，并在各自 manifest 中记录其源码指纹。

核心时间线与三类图件分别以独立 bundle 原子提升，v3 入口按顺序生成四个 bundle；它们不是一次覆盖整个目录的单一事务。若后续图件失败，统一管线会将该阶段标为失败，较早 bundle 可能已更新；每个图件 manifest 都锁定核心 CSV/manifest 哈希，因而旧图件不能通过新核心快照的 provenance 校验，也不能被当作一次完整成功运行发布。

### 5.1 2026-08-01 v3 快照

- `station_coords.csv` 中 8 个 `station/disp_col` 一一对应，`x_m/y_m/elev_m` 均为有限米制数值；
- 高程范围为 `190–515 m`，在 8 个固定测点间 z-score 后按水平 IDW 生成静态网格，不进入三维距离；
- 当前 ConvLSTM 共 7 个通道，fit/calibration/test 窗口为 `911/227/287`；
- 物化 test 段 RMSE 为 `0.338 mm`，持久性为 `0.340 mm`，校准后 P10–P90 覆盖率为 `0.770`；
- v2 生成 `4112` 条测点记录和 `514` 条滑坡体记录，四项输入无缺失；`114` 条为当前规则下的 `valid`，`400` 条保留为 `candidate_not_site_confirmed`；
- v3 的状态数仍为 `valid=114`、`candidate_not_site_confirmed=400`；确认色为 green `8`、blue `48`、yellow `31`、orange `9`、red `18`，另有 `400` 日不发布整体颜色；
- 局部最高候选为 blue `56`、yellow `196`、orange `111`、red `151`；其中 8 个单区 blue 日转为 site green，并明确标记 `localized_blue_attention`；
- v2/v3 测点表均新增 `ΔV` 三态趋势、一致性和复合信号字段；五色候选与滑坡体统计保持不变，两个版本的测点值除 profile ID/version 外一致；
- 六日诊断图完整显示逐点区间、运动学、`ΔV`、整体确认/局部最高双轴及 O1/O2/O3 支撑，未确认 site 使用 `NC`，不会被画成 green；
- 514 日完整图显示全部 4,112 条测点状态及整体/局部双轴；400 个 `NC` 明确不是缺测；
- 8 点联合图在同一日期轴上展示累计位移、四指标状态与最终候选等级，补齐 R9 的联合展示要求；
- 本快照只证明链路完整。高程版本较此前无高程单种子快照的 RMSE 更高，因此不宣称加入高程改善预测，也不据 test 结果继续调参。

## 6. 解读限制

- `operational_draft` 的色彩仅表示当前实施版规则的输出，不能在论文中称为“正式预警等级”；
- 严格 MVIF 失败这一事实没有被删除或放宽；KMeans 候选仍不是指定 Word 的 MVIF 初始稳定斜率；
- v1 中速度与切线角被重复计票的结果只能用于历史对照；v2 将它们合并为一个证据族，仍不能把两个输出解释成独立观测证据；
- v2/v3 的 `candidate_not_site_confirmed`、`insufficient_assessable_coverage` 与 v1 的 `uncorroborated`、`insufficient_valid_station_results` 均不能静默并入 green；v3 只有局部最高不超过 blue 时才允许 site green；
- Wang 等（2025）仅为藕塘 8 个测点的 O1/O2/O3 空间拓扑提供来源；`3` 个可评估点、跨区支撑数、blue/yellow 的处理和任何颜色均是本项目可替换的非监督运行约定；
- 不输出监督分类性能、概率、F1、Brier、混淆矩阵或严格前瞻预警提前量；Vajont 不参与本运行。
- 根目录既有滚动、五种子、早停和容量产物来自加入高程前的 6 通道版本，不能借给当前模型。7 通道 fixed-120 已于 2026-08-04 在独立版本目录完成三折滚动和五种子诊断，但 fold 1/2 仍稳定不如持久性；7 通道早停和容量敏感性没有运行。该诊断不改变本 operational v3 快照的阈值、颜色或非正式证据等级。

导师若只要求改动当前已支持的范围表、blue 容差、`ΔV` 中心/近零容差或最少支撑数，应先复制并提升本配置版本，再重跑此命令。若要求更换稳定段证据来源、切线角公式或融合语义，则必须新增并审查相应实现，不能仅改描述字符串；基础 `1.3-draft` 协议的未决项与正式门禁仍需单独审查。
