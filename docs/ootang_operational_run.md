# 藕塘实施版运行说明（导师复核用）

> 当前运行配置：[ootang_operational_run.v2.draft.json](../config/ootang_operational_run.v2.draft.json)
>
> 保留对照配置：[ootang_operational_run.v1.draft.json](../config/ootang_operational_run.v1.draft.json)
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

## 3. v2 空间证据族运行（当前）

v2 不改动 v1 目录、KMeans 对照基线、速度/切线角范围表或任何 fit-only 参数；它只替换此前审查明确存在语义问题的融合与滑坡体汇总方式。

1. 速度和切线角合并为一个运动学证据族，取两者等级的最大值；二者不再作为两张独立选票；
2. 测点候选等级为 `max(interval_level, kinematic_level)`；`ΔV=positive` 仅写入 `accelerating`，不单独升级五级颜色；
3. 单一证据族的非绿异常保留为可评估候选，例如 `interval_only_not_accelerating`，不再写成缺失或 `uncorroborated`；
4. 固定空间分区：O1=`MJ9/MJ1/MJ3`，O2=`ATU4/ATU5/ATU3`，O3=`ATU2/ATU1`。该拓扑只参考 Wang 等（2025，[DOI](https://doi.org/10.1029/2025JH000592)）PDF 第 7 页图 4(a,d) 和 5.2 节；配置与 manifest 锁定本地 PDF 指纹，**不**从该论文引申任何本项目的汇总阈值或正式预警规则；
5. 全滑坡体绿色要求至少 3 个可评估测点且三个分区均有覆盖。blue 仅在它是当日最高候选时可见，不要求跨区确认；黄色及以上则要求至少 2 个候选测点、跨至少 2 个分区达到或超过同一级别；
6. 若 yellow--red 最高候选未获相应跨区支撑，输出 `candidate_not_site_confirmed`，保留候选颜色、测点和分区，**不得因其他分区存在 blue 候选而降为 blue**；它不再被误称为“有效测点不足”。

这是一套项目特有、非监督、可替换的 v2 规则，不是指定 Word 的多项 Logistic 回归复现，也不解除正式协议中的任何未决项。

## 4. 运行与产物

```bash
uv run python code/warning/operational_run.py
```

上面的命令保留并重跑 v1，并只写入 `figures/warning_operational_draft/`。当前 v2 入口为：

```bash
uv run python main.py \
  --stage features \
  --stage convlstm \
  --stage ootang-operational-v2
```

也可以直接运行：

```bash
uv run python code/warning/operational_run_v2.py
```

两版都会先刷新唯一的 `figures/warning_draft/` 输入证据集，再在临时目录写出并核验实施版文件，最后逐文件提升到各自目录。v1 提升到 `figures/warning_operational_draft/`，v2 提升到 `figures/warning_operational_draft_v2/`；通用运行器会拒绝把 v2 写入 v1 目录，反之亦然。

v2 还要求本地存在高程感知预测清单，以及 [`Wang et al. (2025) 的 PDF`](../literature/Journal%20of%20Geophysical%20Research%20%20Machine%20Learning%20and%20Computation%20-%202025%20-%20Wang%20-%20Enhancing%20Landslide%20Displacement.pdf) 且 SHA-256 与配置一致；缺失或指纹不符会明确拒绝运行，不会从别的论文、网络副本或 Vajont 数据静默替代。

每个版本目录中都有以下同名文件：

- `ootang_operational_thresholds.csv`：8 点的候选 `V0`、速度/切线角 blue 边界与 `ΔV` 近零容差；
- `ootang_operational_station_timeline.csv`：calibration/test 每日、每点四项输入、状态、等级、测点融合理由与候选基线来源；
- `ootang_operational_site_timeline.csv`：每日的有效点数量、各级计数、贡献点、未获佐证点与滑坡体实施版状态；
- `ootang_operational_run_manifest.json`：基础协议/实施版配置/输入/输出哈希、结果状态计数和明确的非正式边界。

v2 的测点表新增 `station_assessment_status`、`candidate_level/color`、`kinematic_level/color`、`evidence_families`、`acceleration_status` 与 `station_confirmation_status`；滑坡体表新增 `assessable_station_count`、`assessable_blocks`、`coverage_complete`、`cross_block_confirmation_minimum_level/color`、`site_candidate_level/color`、`candidate_blocks` 与 `contributing_blocks`。v2 中旧列 `fusion_status=valid` 只表示四项输入可评估，**不再表示两项独立投票已佐证**；为兼容旧读者而保留的 `final_level/final_color` 等同于 `candidate_level/candidate_color`，不是测点已确认结果，更不是滑坡体级或正式预警结果。

这些文件均可从 manifest 中的路径与 SHA-256 复核。v2 manifest 还记录高程感知预测清单、预测哈希匹配状态，以及 Wang 等（2025）空间分区源文件的 DOI、页/图定位、路径和 SHA-256。参数表不会因仅改变 test 期预测值而变化；该性质由集成测试覆盖。若发生 Python 可捕获的写入或提升错误，旧实施版快照会恢复；不宣称进程被强制终止或断电时的目录级事务。

### 4.1 2026-07-30 初跑快照

- `station_coords.csv` 中 8 个 `station/disp_col` 一一对应，`x_m/y_m/elev_m` 均为有限米制数值；
- 高程范围为 `190–515 m`，在 8 个固定测点间 z-score 后按水平 IDW 生成静态网格，不进入三维距离；
- 当前 ConvLSTM 共 7 个通道，fit/calibration/test 窗口为 `911/227/287`；
- 物化 test 段 RMSE 为 `0.338 mm`，持久性为 `0.340 mm`，校准后 P10–P90 覆盖率为 `0.770`；
- v2 生成 `4112` 条测点记录和 `514` 条滑坡体记录，四项输入无缺失；`114` 条为当前规则下的 `valid`，`400` 条保留为 `candidate_not_site_confirmed`；
- 本快照只证明链路完整。高程版本较此前无高程单种子快照的 RMSE 更高，因此不宣称加入高程改善预测，也不据 test 结果继续调参。

## 5. 解读限制

- `operational_draft` 的色彩仅表示当前实施版规则的输出，不能在论文中称为“正式预警等级”；
- 严格 MVIF 失败这一事实没有被删除或放宽；KMeans 候选仍不是指定 Word 的 MVIF 初始稳定斜率；
- v1 中速度与切线角被重复计票的结果只能用于历史对照；v2 将它们合并为一个证据族，仍不能把两个输出解释成独立观测证据；
- v2 的 `candidate_not_site_confirmed`、`insufficient_assessable_coverage` 与 v1 的 `uncorroborated`、`insufficient_valid_station_results` 均不能静默并入 green；
- Wang 等（2025）仅为藕塘 8 个测点的 O1/O2/O3 空间拓扑提供来源；`3` 个可评估点、跨区支撑数、blue/yellow 的处理和任何颜色均是本项目可替换的非监督运行约定；
- 不输出监督分类性能、概率、F1、Brier、混淆矩阵或严格前瞻预警提前量；Vajont 不参与本运行。
- 既有滚动、五种子、早停和容量产物来自加入高程前的 6 通道版本；当前 7 通道初跑不能借用那些文件声称已完成同范围稳定性验证。

导师若只要求改动当前已支持的范围表、blue 容差、`ΔV` 中心/近零容差或最少支撑数，应先复制并提升本配置版本，再重跑此命令。若要求更换稳定段证据来源、切线角公式或融合语义，则必须新增并审查相应实现，不能仅改描述字符串；基础 `1.3-draft` 协议的未决项与正式门禁仍需单独审查。
