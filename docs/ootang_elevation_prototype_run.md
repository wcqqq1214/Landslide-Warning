# 藕塘高程感知初步跑通记录

> 历史记录说明（2026-08-13）：本文件记录高程原型初跑时的 v2 运行命令和产物。当前可执行预警入口已收敛为 `ootang-operational-v4`；旧 v2 命令仅能通过 Git 历史复现。

> **产物链接失效说明（2026-08-15）**：本文末尾引用的 `figures/warning_operational_draft_v2/` 四份产物已随已退役产物清理从工作树删除，可按 Git 历史提交 `7d2e38b` 及之前恢复。文中记录的 v2 数值（`valid=114`、`candidate_not_site_confirmed=400`、blue `56`/yellow `31`/orange `9`/red `18`）仍是当时 v2 快照的结果，**不等于**当前 v4 的滑坡体统计（整体确认 green `8`/blue `48`/yellow `31`/orange `9`/red `18`）。当前结果请查阅 `figures/warning_operational_draft_v4/`。

> 运行日期：2026-07-30<br>
> 状态：`prototype_internal_not_confirmatory`<br>
> 正式预警：`false`<br>
> Vajont：未读取、未运行

## 1. 本次目标

导师要求先使用现有藕塘数据和测点高程跑通案例。用户确认原始 GNSS 无法取得，且藕塘不一定用于最终论文。因此本次目标是验证代码链路、数据契约和输出完整性，不是建立确认性预测或正式工程预警。

本次门禁为：

```text
source_recovery_status = unavailable_by_project_constraint
prototype_run_gate = allowed
confirmatory_evidence_gate = blocked
formal_warning_output = false
vajont_used = false
```

## 2. 输入与高程处理

- 时序输入：[`data/monitoring_data.csv`](../data/monitoring_data.csv)，Figshare 发布物化日建模序列；
- 空间输入：[`data/station_coords.csv`](../data/station_coords.csv)，包含 8 个测点的 `station/disp_col/x_m/y_m/elev_m`；
- `station` 与 `disp_col` 一一对应，`x_m/y_m/elev_m` 均为有限米制数值；
- 高程范围为 `190–515 m`，平均值 `368.125 m`，总体标准差 `100.683 m`；
- `x_m/y_m` 仅用于水平 IDW；
- `elev_m` 先在 8 个固定测点间做 z-score，再按水平 IDW 权重映射为 `4×7` 静态高程网格；
- 高程不直接并入三维欧氏距离，避免在没有坡面距离标定时任意混合水平与垂直尺度。

当前 ConvLSTM 输入共 7 个通道：

```text
displacement_idw_grid
elevation_static_idw_grid
RWL
RWL_rate
Rain_cum7
Rain_cum15
Rain_cum30
```

详细输入哈希、归一化和网格范围见[`高程感知预测清单`](../figures/convlstm/forecast_run_manifest.json)。

## 3. 执行链路

```bash
uv run python main.py \
  --stage features \
  --stage convlstm \
  --stage ootang-operational-v2
```

链路为：

```text
monitoring_data.csv
  → features / 逐点速度 / ΔV
  → 7 通道高程感知 ConvLSTM
  → P10/P50/P90 与逐时刻区间状态
  → 速度、ΔV、切线角
  → 测点级非监督证据族
  → O1/O2/O3 滑坡体空间汇总
```

三阶段均通过输入/输出契约检查，基于提交 `a01f061` 的最终复跑总耗时约 `40.7 s`。完整阶段、源码指纹和产物哈希见当时的 `figures/pipeline/latest_run.json`（该文件已于 2026-08-15 删除，按 Git 历史提交 `7d2e38b` 及之前查阅）。

## 4. 预测结果

切分窗口为：

| split | 窗口数 | 日期 |
| --- | ---: | --- |
| fit | 911 | 2016-08-06 至 2019-02-02 |
| calibration | 227 | 2019-02-03 至 2019-09-17 |
| test | 287 | 2019-09-18 至 2020-06-30 |

test 段总体结果：

| 指标 | 高程感知 ConvLSTM | persistence |
| --- | ---: | ---: |
| RMSE | 0.338 mm | 0.340 mm |
| MAE | 0.174 mm | 0.181 mm |
| RMSE skill | 0.007 | — |
| 原始 P10–P90 覆盖率 | 0.750 | — |
| 校准后 P10–P90 覆盖率 | 0.770 | — |
| 原始/校准后平均区间宽度 | 0.518 / 0.542 mm | — |

逐测点校准版本 RMSE：

| 测点 | 模型 RMSE | persistence RMSE | RMSE skill |
| --- | ---: | ---: | ---: |
| MJ9 | 0.120 | 0.125 | 0.037 |
| MJ1 | 0.167 | 0.183 | 0.091 |
| MJ3 | 0.220 | 0.223 | 0.014 |
| ATU1 | 0.477 | 0.477 | 0.001 |
| ATU2 | 0.323 | 0.322 | -0.004 |
| ATU3 | 0.494 | 0.494 | -0.000 |
| ATU4 | 0.312 | 0.307 | -0.016 |
| ATU5 | 0.387 | 0.397 | 0.027 |

结果只能解读为“模型成功运行且总体略优于 persistence”。优势极小，且 ATU2、ATU3、ATU4 没有超过 persistence，不能写成明显或稳定提升。

## 5. 与加入高程前快照的比较

在相同单种子主流程下，加入高程前的历史快照总体 RMSE 约为 `0.318 mm`，加入高程后为 `0.338 mm`；平均逐测点 RMSE skill 约由 `0.082` 降至 `0.019`。部分测点覆盖率有所上升，但点误差整体变差。

这是已查看 test 段上的事后比较，只用于诚实记录当前结果。不得据此继续试验高程权重、三维距离、网络规模或其他参数并选择表现最好者。

## 6. 四指标与空间融合完整性

- 预测表共 `11,400` 条记录：fit `7,288`、calibration `1,816`、test `2,296`；
- 8 个测点全部进入预测，没有 `(date, station, split)` 重复；
- v2 测点时间线共 `4,112` 条，即 `514` 个结果日 × 8 点；
- 区间、速度、`ΔV` 和切线角四项在全部 `4,112` 条记录中均可评估；
- 滑坡体时间线共 `514` 条，日期无重复；
- 当前项目特有空间规则给出 `114` 条 `valid`，另有 `400` 条 `candidate_not_site_confirmed`；
- `valid` 颜色为 blue `56`、yellow `31`、orange `9`、red `18`；
- `candidate_not_site_confirmed` 保留候选证据，不并入 green。

逐点和滑坡体结果见：

- [`测点时间线`](../figures/warning_operational_draft_v2/ootang_operational_station_timeline.csv)
- [`滑坡体时间线`](../figures/warning_operational_draft_v2/ootang_operational_site_timeline.csv)
- [`候选阈值表`](../figures/warning_operational_draft_v2/ootang_operational_thresholds.csv)
- [`v2 运行清单`](../figures/warning_operational_draft_v2/ootang_operational_run_manifest.json)

## 7. 当前结论与下一步

本次已经达到“藕塘初步跑通”的工程目标：高程确实进入模型，8 点预测、四指标和多测点空间汇总均可复算。

尚未达到：

- 高程能够改善预测的证据；
- 当前 7 通道模型的早停和容量稳定性复验；三折滚动与五种子 fixed-120 诊断已于 2026-08-04 完成，但 fold 1/2 稳定不如持久性、fold 3 仅在强平滑下略优，未证明跨时期稳健性；
- 来源清楚的确认性预测；
- 正式 `V0`、正式融合或工程预警；
- 任何 Vajont 结果。

典型状态日、400 条未空间确认状态和高程可信性已经完成专家审查，见[`藕塘高程通道与空间预警结果专家审查`](ootang_elevation_warning_expert_review.md)。审查确认 400 日全部数据完整且候选只位于 O1，同时发现 v2 有效点门禁没有覆盖非绿色分支、green/blue 的滑坡体语义过严。上述门禁已修复，并已形成不覆盖 v2 的 v3 双轴空间规则草案；没有扩大同一 test 段上的模型搜索。若最终论文更换数据集，则以新数据重新建立来源、切分、阈值和验证协议。

后续 fixed-120 诊断及其版本化产物见 [`藕塘 7 通道 ConvLSTM fixed-120 诊断审查`](ootang_convlstm_elevation_fixed120_review.md)。该诊断没有回调本记录中的 operational v2/v3 阈值或颜色。
