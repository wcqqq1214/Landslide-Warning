# 滑坡位移预测与预警代码设计

> 本文档描述当前代码实现和模块边界。研究问题、终点和评价规范以 `framework.md` 为准；结果数值以 `results_report.md` 和 `../figures/*/*.csv` 为准。
>
> **状态更新（2026-07-16）**：此处描述的是导师修改前的实现边界。当前正式方法以 `advisor_review_action_plan.md` 和导师指定论文为准；本文件中的四级 V0、加速度、V0 主判和 NGBoost 路径均为旧实现，不得用作本轮协议或结果表述。
>
> **入口隔离（2026-07-22）**：`main.py` 是研究与历史/探索性复核入口，运行清单固定为 `formal_warning_output=false`。未来正式执行器只能经 `code/warning/formal_warning.py::run_formal_warning()`，并在任何读写前通过冻结协议门禁；当前草案协议因此不能生成正式预警。历史产物的完整映射见 `legacy_warning_artifact_inventory.md`。
>
> **数据血缘门禁（2026-07-28）**：当前输入是 Figshare 发布的物化日建模序列，不是已验证的独立原始逐日 GNSS。8 条位移和 GWT 具有强自然月分段三次指纹，原始锚点、日值生成算法及未来信息使用状态未恢复，故 `data_gate=blocked`；下述模型评价只属于物化序列内部工程/探索性结果。

## 1. 数据与约束

- 发布物化建模序列：`data/monitoring_data.csv`，1461 个连续日历行，2016-07-01 至 2020-06-30；原始 GNSS 时间戳、观测锚点和日值生成方法尚未提供。
- 位移测点：MJ9、MJ1、MJ3、ATU1、ATU2、ATU3、ATU4、ATU5。
- 环境变量：Rainfall、GWT、RWL、aveT、minT、maxT、DP、RH。
- 坐标数据：`data/station_coords.csv`，用于将 8 个测点 IDW 插值为规则网格；MJ/ATU 与 GPS/FJ 的正式映射及坐标血缘尚未恢复，空间几何暂按工程输入使用。
- 项目代码生成的时间特征只使用当前及历史表格行；上游日序列生成是否使用未来锚点未知。阈值、标准化参数和自动等速段仍只能由训练期估计。

## 2. 当前架构

```text
monitoring_data.csv
  -> features/build_features.py
       -> data/features.csv
       -> data/ootang_kinematics_long.csv
       -> data/ootang_kinematics_summary.csv
       -> figures/tangent_angle/uniform_rates.csv

data/features.csv + data/station_coords.csv
  -> convlstm/model.py -> models/convlstm.pt + figures/convlstm

monitoring_data.csv
  -> explainability/shap_select.py -> figures/shap
  -> warning/onset_analysis.py + warning/warning_events.py -> figures/warning_onset
  -> warning/sensitivity_analysis.py -> figures/sensitivity
  -> features/tangent_stage_review.py -> figures/tangent_angle/review

data/features.csv + monitoring_data.csv
  -> warning/ngboost_warn.py -> models/ngboost.pkl + figures/ngboost (历史/探索)
  -> warning/warning_fusion.py -> figures/warning_fusion/warning_fusion.csv (历史/探索)

future frozen protocol + formal four-indicator executor
  -> warning/formal_warning.py::run_formal_warning(...)
  -> formal warning artifacts (当前未实现，draft 协议会先拒绝)
```

## 3. 模块职责

| 模块 | 职责 | 主要输出 |
| --- | --- | --- |
| `code/features/build_features.py` | 时间感知的逐点位移速度/`ΔV`、库水位变化率、多窗口降雨和切线角特征 | `data/features.csv`、`data/ootang_kinematics_long.csv`、`data/ootang_kinematics_summary.csv`、`figures/tangent_angle/uniform_rates.csv` |
| `code/features/kinematics.py` | 统一计算 `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})` 与 `ΔV_i=v_i-v_{i-1}`，并记录暖启动、缺测和异常时间间隔 | 由 `build_features.py`、切线角和解释模块调用 |
| `code/features/tangent_angle.py` | 使用真实时间间隔估计等速段、原始/因果平滑切线角和持续性判级；支持可选人工等速阶段表 | 由 `build_features.py` 调用 |
| `code/warning/warning_thresholds.py` | 历史 30 日 V0、位移增量和四级标签；导出行显式为非正式 | 由旧 SHAP、NGBoost 和融合模块调用 |
| `code/warning/warning_events.py` | 连续事件提取、未来 onset 标签和固定阈值事件评价 | 由 onset 分析及后续模型调用 |
| `code/warning/onset_analysis.py` | 生成 1/3/7 日未来标签、事件清单和样本充分性盘点 | `figures/warning_onset/*`、`figures/thresholds/v0_thresholds.csv` |
| `code/explainability/shap_select.py` | 构造含逐点速度/`ΔV` 的滞后样本；对独立 NGBoost 做探索性回归、遗留同日 V0 二分类、SHAP 和时间扩展窗口评价 | `figures/shap/*`、`figures/thresholds/v0_thresholds.csv`；不是 ConvLSTM-SHAP 或正式预警 |
| `code/explainability/shap_stability.py` | 按锁定五折协议重训独立解释模型，汇总特征/组排名、方向、测点分层时间稳定性并执行五组删组消融 | `figures/shap/stability/*`；不作因果或留一测点泛化结论 |
| `code/convlstm/grid_interp.py` | 读取测点坐标并建立 IDW 规则网格插值器 | 由 `model.py` 调用 |
| `code/convlstm/block_bootstrap.py` | 生成非循环重叠日期块索引并计算百分位区间 | 由 `model.py` 调用 |
| `code/convlstm/model.py` | 8 测点空间网格 ConvLSTM，输出 P10/P50/P90 位移 | `models/convlstm.pt`、`figures/convlstm/*` |
| `code/convlstm/rolling_validation.py` | 固定现有 ConvLSTM 结构，执行三个非重叠测试折的扩展窗口验证 | `figures/convlstm/rolling_validation_*.csv` |
| `code/convlstm/seed_stability.py` | 固定三折、结构和超参数，执行预设五种子优化稳定性诊断 | `figures/convlstm/seed_stability_*.csv` |
| `code/convlstm/inner_validation.py` | 在每折拟合期内部按时间选择训练轮数，完整拟合期重训后与固定 120 轮结果配对 | `figures/convlstm/inner_validation_*.csv` |
| `code/convlstm/capacity_sensitivity.py` | 执行预注册 2x2 隐藏通道/权重衰减矩阵，仅按内层五种子验证 loss 逐折选择配置 | `figures/convlstm/capacity_*.csv` |
| `code/convlstm/data_lineage_audit.py` | 只读核验 Figshare 工作簿与仓库 CSV、自然月多项式指纹、模型边界代数依赖和预测日期键集 | `figures/data_lineage/*`；只作数据血缘门禁，不修复或生成监测值 |
| `code/warning/ngboost_warn.py` | 使用历史动态 V0 当日四级标签训练 NGBoost 概率分类器 | `models/ngboost.pkl`、`figures/ngboost/*`、`figures/thresholds/v0_thresholds.csv`；历史/探索性 |
| `code/warning/warning_fusion.py` | 历史 V0 主判、8 测点切线角升级复核、NGBoost 旁证；CSV 显式标为非正式 | `figures/warning_fusion/warning_fusion.csv`；历史/探索性 |
| `code/warning/formal_warning.py` | 在冻结协议检查后才调用未来正式四指标执行器 | 当前只有门禁，无正式时间线或结果输出 |
| `code/warning/sensitivity_analysis.py` | 重算预先规定的 V0 与切线角参数组合并比较等级、事件和融合原因 | `figures/sensitivity/*` |
| `code/features/tangent_stage_review.py` | 为 8 个位移测点生成候选阶段复核图，并比较参数、切线角等级和融合影响 | `figures/tangent_angle/review/*` |

## 4. 已锁定的实现选择

### 4.1 特征工程

- 位移速率：`v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`，按表格日期的实际 `Δt` 计算，单位为 mm/d；当前是物化序列导数，不等同于原始 GNSS 速度。
- 速度增量：`ΔV_i=v_i-v_{i-1}`，单位仍为 mm/d；它不是再除以时间的加速度，并可能放大自然月多项式处理结构。
- 库水位速率：按真实 `Δt` 计算，单位为 m/d。
- 累计降雨窗口：7、15、30 日。
- 原始方法切线角：逐点速度除以 `v_eq` 后取反正切，并按许强等（2009）的严格 `>45`、`>80`、`>85` 阶段边界判定；当前数值仍是物化序列导数的变换。
- 工程切线角：3 个观测点的尾随时间线性斜率，不使用未来观测；再应用 5 个观测点内至少 3 次命中的持续性确认。
- 自动等速段：仅在前 80% 训练期内选择 30 日候选窗口；当前发布表为连续日历网格，因此等同于 30 个表格行，不证明原始采样为逐日。它只是专家阶段划分前的辅助候选，不是原文方法本身。
- 人工等速阶段：当前仓库不保留默认配置文件。若后续需要固定人工等速阶段，可临时提供同结构 CSV，并将 `status=approved` 的行交给 `tangent_angle.py` 校验；同一测点仅允许一个批准阶段，日期必须位于训练期内。

> 修订边界：`data/features.csv` 已改用 `*_delta_v`，并新增藕塘运动学长表；但下文的动态 V0、NGBoost 和融合描述仍是修订前的历史路径，不能作为本轮五级正式预警结果。其替换必须等待阶段 0 的阈值、蓝色边界和融合协议冻结。

### 4.2 ConvLSTM

- 8 测点通过 IDW 插值到 `4 x 7` 规则网格，代码结构上属于二维卷积循环网络；物理空间解释仍受坐标血缘与点位别名映射未解决的限制。
- 当前输入窗口：7 日。
- 当前预测步长：1 日。
- 输出：有序 P10/P50/P90 位移增量，再还原为累计位移。
- 损失：分位数 pinball loss。
- 评价：按测点及三个连续测试时段报告相对于发布物化序列的点误差、持久性基线、分位数损失、覆盖率、宽度和 80% interval score；R2/NSE 仅作趋势敏感的补充指标。
- 校准：原训练窗口前 80% 用于拟合、后 20% 连续日期用于按测点对称 split-conformal 校准；标准化和增量尺度只拟合于前者。时间自相关使经典覆盖保证不成立，因此结果按探索性校准报告。
- 不确定性：固定模型与校准量，以连续日期块同步重采样所有测点；14 日为预设主块长，7/30 日为敏感性分析，各 1000 次。输出模型-基线及校准-原始的配对差值，不把两个单独区间是否重叠当作差异检验。
- 滚动验证：测试长度沿用现有 287 日留出尺度，三个测试折互不重叠，训练历史逐折扩展；每折重新拟合标准化、增量尺度、模型和 `qhat`。固定同一随机种子以减少初始化差异，但不把固定种子解释为统计稳健性。
- 多种子诊断：预设种子 0-4，保持滚动折和全部参数不变，保存每轮训练 loss/梯度、逐种子指标和跨种子汇总；不得选择最佳种子或据测试折调整 epoch。
- 内层 epoch 选择：原拟合期按日期切为 80% 内层训练和 20% 内层验证，最多 300 轮并按预注册早停规则选 epoch；同一种子在完整拟合期重训后，原校准段只估计 `qhat`，外层测试段只评价。固定 120 轮结果保留为配对参照。
- 有限容量/正则化诊断：只比较隐藏通道 `8/16` 和 Adam 权重衰减 `0/1e-4` 的四个组合；每折以五种子内层最小验证 loss 均值排名，外层测试不参与配置选择，当前早停版本保留为配对参照。

### 4.3 旧 NGBoost 路径（不作为本轮正式预警）

- 主模型：`NGBClassifier` 四分类概率模型。
- 标签：动态 V0 当日四级状态，不是切线角标签。
- 旧标签的一级 `V0` 沿用既有毕业论文摘要中的匀速变形段位移速率统计公式；5/10 倍高等级阈值可追溯到 Chen et al.（2024）式（10）的默认 `vd`。该旧实现不包含该文的 GPD/POT、VaR 或 CVaR 估计，且已不作为本轮正式预警路径。
- 输入：8 测点位移速率/`ΔV` 聚合量、库水位、库水位速率和多窗口累计降雨。
- 当前模型任务属于状态识别；未来 1/3/7 日 onset 标签已实现，但模型验证因独立事件不足而暂停。

### 4.4 预警融合

- V0 是主判规则，融合结果不得低于 V0 等级。
- 切线角使用 MJ9、MJ1、MJ3、ATU1、ATU2、ATU3、ATU4、ATU5 共 8 个测点执行升级复核。
- 单测点切线角异常最高升级为黄色观察状态。
- 多测点或多尺度一致时，才允许进一步升级。
- NGBoost 概率保留为旁证，不直接覆盖规则等级。

## 5. 运行顺序

```bash
uv run python main.py
```

`main.py` 按 `features -> onset -> shap -> shap-stability -> convlstm -> convlstm-rolling -> convlstm-seeds -> convlstm-inner-validation -> convlstm-capacity -> ngboost -> fusion -> sensitivity -> tangent-review` 编排十三个独立进程。使用 `--stage` 可选择阶段，`--skip` 可跳过阶段，`--dry-run` 可在不执行脚本时核对命令。阶段选择保持标准顺序，但不自动解析或补跑上游依赖。实际执行会将提交哈希、执行源码 SHA-256 指纹、运行环境、逐阶段状态、退出码和耗时写入 `figures/pipeline/latest_run.json`，失败时同样保留记录。

阶段契约在子进程前检查必需输入，在子进程后检查预期输出存在且本次运行已更新。清单为每个通过检查的输出保存相对路径、文件大小和 SHA-256；缺输入、缺输出或陈旧输出均使管线停止，不能仅凭脚本退出码 0 判定完成。

运行测试：

```bash
uv run --with pytest pytest -q
```

各阶段仍保持独立脚本，以便单独重跑和核对中间结果；统一入口只负责顺序、失败传播和耗时汇总，不改变模型内部实现。

## 6. 数据泄漏防线

以下 1–6 项约束仓库内建模流程，不能证明公开日序列的上游生成没有使用未来锚点：

1. 所有表格行先按日期排序，训练期必须早于验证/测试期。
2. V0 和自动等速段只由训练期估计。
3. 滞后、滚动累计和平滑只允许使用当前及历史数据。
4. 同一日期的 8 个测点必须进入同一个数据分区。
5. 标准化参数只由训练期拟合。
6. 测试结果不能参与特征、阈值和超参数选择。

现有后 20% 数据已经参与多轮分析，因此只能作为探索性留出结果。更重要的是，三个关键模型边界都切穿同一自然月三次段。后续确认性评价必须取得血缘清楚的原始锚点，先按时间切分锚点，再在每折内部生成日序列；仅增加同类物化序列的时间折、新时段或外部案例不能自动解除该门禁。

## 7. 完成标准

“脚本无报错”只说明工程管线可运行，不等于研究假设成立。每次正式实验至少满足：

- 代码和测试通过，输出文件可追溯到 Git 提交。
- 原始观测锚点、日值生成链和点位映射可追溯，且数据闸门通过。
- 所有阈值和变换遵守训练期边界。
- 同时报告主模型、基线、类别/事件支持数和不确定性。
- 位移预测同时报告误差、区间覆盖率和宽度。
- 预警同时报告样本级、概率校准和事件级结果。
- 结论与证据等级一致，不将状态识别描述为提前预警。

## 8. 当前已知限制

- 当前发布序列的 8 条位移和 GWT 在全部 48 个自然月内具有强分段三次指纹；原始锚点、生成算法和未来信息使用状态未知，正式日预测、正式 `V0`、导数阈值与融合均被数据门禁阻断。
- ConvLSTM 已启用日历上后置的时间校准，但 P10-P90 测试覆盖率仍低于名义 80%，最后连续测试块退化明显。
- ConvLSTM 已输出日期块 95% 置信区间，但其局部平稳假设与已观察到的后期漂移冲突；区间不包含训练过程和未来制度变化的不确定性。
- ConvLSTM 五种子诊断中，折 1/2 的方向性失败对初始化稳定，折 3 的点误差优势也对种子稳定但伴随明显方差收缩和接近零的平均增量相关性。初始化影响误差幅度，但不能解释跨折方向反转；训练样本量和时间分布作用仍未分离。
- ConvLSTM 内层早停降低了多数固定 120 轮配对误差，但折 1/2 仍未超过持久性基线；第三折覆盖率改善伴随区间宽度和 interval score 恶化，所选 epoch 也存在明显种子差异。
- ConvLSTM 有限容量/正则化诊断在三个折选出不同配置，且折 2 的内层选择未迁移为外层改善；仅折 3 达到多数种子双指标正 skill，当前数据已停止继续扩搜。
- NGBoost 未超过昨日状态持续性基线。
- 五折 SHAP 稳定性分析中，回归组排名稳定而分类组排名随时期变化；只有位移运动学组在两个任务均为 5/5 折删去后主指标恶化。环境组结果不稳定，不能解释为物理无效或因果缺失。
- 测试段无橙色和红色样本，不能评价高等级识别能力。
- 自动等速段尚未由导师或现场资料确认；15/30/60 日候选窗口会为部分测点选出显著不同的参考速率，并大幅改变融合结果。复核图和 CSV 参数表已生成（`figures/tangent_angle/review/`），等待独立确定等速阶段。
- 当前融合结果尚无完整事件级提前量和误报评价。
- 尚无外部时间或跨滑坡验证。

## 9. 下一阶段实现顺序

1. 恢复原始 GNSS/GWT 时间戳和值、日值聚合/QC/插值链、参考基准和 MJ/ATU 点位映射。
2. 先切分原始锚点，再在每个时间折内部重建日序列并复查未来信息隔离。
3. 数据门禁通过后，重跑稳定段、`V0`、ConvLSTM、区间校准和运动学指标。
4. 停止在当前已查看物化序列折上扩大 ConvLSTM 超参数搜索或做机理性神经消融。
5. 获得包含更多互不相连标签事件的新监测时段，事件数量足够后再评价分类与提前量。
6. 根据原始累计位移曲线和宏观变形资料复核等速阶段，确认后再固定切线角参数。
