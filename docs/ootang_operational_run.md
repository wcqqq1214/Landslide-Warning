# 藕塘实施版运行说明（导师复核用）

> 配置：[ootang_operational_run.v1.draft.json](../config/ootang_operational_run.v1.draft.json)
>
> 状态：`operational_draft`；用于先完整跑通藕塘案例并便于导师后续替换规则，**不是正式预警结果**。

## 1. 目的与边界

用户于 2026-07-23 授权按当前推荐技术路线先跑通藕塘，导师后续若提出方法调整，再替换相应规则。为避免这项工程授权被误写成论文方法已经完全冻结，本运行独立于正式协议：

- 基础协议仍是 [`ootang-five-level-rule-v1`](../config/ootang_warning_protocol.v1.draft.json) 的 `1.3-draft`；实施版配置锁定其内容 SHA-256 和七项未决项，任一漂移都会拒绝运行；
- 每份 CSV 和 manifest 均写入 `formal_warning_output=false`、`artifact_status=operational_draft_not_formal` 与 `vajont_used=false`；
- 不调用 [`formal_warning.py`](../code/warning/formal_warning.py)，不把原始速度 KMeans 候选称为指定 Word 论文的 MVIF `V0` 实现；
- 参数只由 `split=fit` 的候选稳定段和运动学记录产生；calibration/test 仅执行，不反向选择参数。

因此，它是一份“可跑、可审计、可替换”的实施版，不是对阈值有效性、预警提前量或泛化性能的结论。

## 2. 当前可替换技术路线

1. 先重建七份藕塘草案证据，核对基础协议指纹、输入来源和非正式标识；
2. 区间指标直接使用指定 Word 图 5-1 的 `μ+kσ` 五级区域；本项目近似为 `μ=P50`、`σ=(P90-P10)/(2×1.28155)`，且只在观测到同日位移后作状态识别；
3. 严格 MVIF 仍是指定 Word 路径的诊断基线。当前 8 点均为 `tf_multistart_unstable`，所以实施版显式改用 fit-only 的“原始速度 KMeans 初始低速前缀”作为**项目特有运行基线**；
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

## 3. 运行与产物

```bash
uv run python code/warning/operational_run.py
```

命令先刷新唯一的 `figures/warning_draft/` 输入证据集，再在临时目录写出并核验实施版文件，最后逐文件提升到 `figures/warning_operational_draft/`：

- `ootang_operational_thresholds.csv`：8 点的候选 `V0`、速度/切线角 blue 边界与 `ΔV` 近零容差；
- `ootang_operational_station_timeline.csv`：calibration/test 每日、每点四项输入、状态、等级、测点融合理由与候选基线来源；
- `ootang_operational_site_timeline.csv`：每日的有效点数量、各级计数、贡献点、未获佐证点与滑坡体实施版状态；
- `ootang_operational_run_manifest.json`：基础协议/实施版配置/输入/输出哈希、结果状态计数和明确的非正式边界。

这些文件均可从 manifest 中的路径与 SHA-256 复核。参数表不会因仅改变 test 期预测值而变化；该性质由集成测试覆盖。若发生 Python 可捕获的写入或提升错误，旧实施版快照会恢复；不宣称进程被强制终止或断电时的目录级事务。

## 4. 解读限制

- `operational_draft` 的色彩仅表示当前实施版规则的输出，不能在论文中称为“正式预警等级”；
- 严格 MVIF 失败这一事实没有被删除或放宽；KMeans 候选仍不是指定 Word 的 MVIF 初始稳定斜率；
- 速度与切线角都依赖 `v/V0`，在两项佐证融合中存在相关性，不能解释成两种独立观测证据；
- `uncorroborated`、`insufficient_valid_station_results` 是有意保留的不确定状态，不可在图表或统计时静默并入 green；
- 不输出监督分类性能、概率、F1、Brier、混淆矩阵或严格前瞻预警提前量；Vajont 不参与本运行。

导师若只要求改动当前已支持的范围表、blue 容差、`ΔV` 中心/近零容差或最少支撑数，应先复制并提升本配置版本，再重跑此命令。若要求更换稳定段证据来源、切线角公式或融合语义，则必须新增并审查相应实现，不能仅改描述字符串；基础 `1.3-draft` 协议的未决项与正式门禁仍需单独审查。
