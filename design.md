# 滑坡位移预测与预警代码设计

> 本文档描述当前代码实现和模块边界。研究问题、终点和评价规范以 `framework.md` 为准；结果数值以 `docs/results_report.md` 和 `figures/*/*.csv` 为准。

## 1. 数据与约束

- 原始数据：`data/monitoring_data.csv`，1461 个连续日观测，2016-07-01 至 2020-06-30。
- 位移测点：MJ9、MJ1、MJ3、ATU1、ATU2、ATU3、ATU4、ATU5。
- 环境变量：Rainfall、GWT、RWL、aveT、minT、maxT、DP、RH。
- 坐标数据：`data/station_coords.csv`，用于将 8 个测点 IDW 插值为规则网格。
- 所有时间特征仅使用当前及历史观测；阈值、标准化参数和自动等速段只能由训练期估计。

## 2. 当前架构

```text
monitoring_data.csv
        |
        v
features.py ------------------------------+
        |                                  |
        | features.csv                     | tangent parameters
        v                                  v
shap_select.py      convlstm.py      tangent_angle.py
        |                 |                 |
        v                 v                 |
figures/shap       models/convlstm.pt       |
                    figures/convlstm        |
        |                                   |
        +------------> ngboost_warn.py <----+
                            |
                            v
                   models/ngboost.pkl
                   figures/ngboost
                            |
                            v
                   warning_fusion.py
                            |
                            v
              figures/warning_fusion/warning_fusion.csv

monitoring_data.csv -> onset_analysis.py -> warning_events.py
                                         -> figures/warning_onset

monitoring_data.csv -> sensitivity_analysis.py -> figures/sensitivity
```

## 3. 模块职责

| 模块 | 职责 | 主要输出 |
| --- | --- | --- |
| `code/features.py` | 位移速率/加速度、库水位变化率、多窗口降雨和切线角特征 | `data/features.csv`、`figures/tangent_angle/uniform_rates.csv` |
| `code/tangent_angle.py` | 训练期等速段估计、原始/因果平滑切线角和持续性判级；提供人工等速阶段读取接口 | 由 `features.py` 调用；配置文件 `config/tangent_reference_stages.csv` |
| `code/warning_thresholds.py` | 测点专属 V0、30 日位移速率和四级标签 | 由 SHAP、NGBoost 和融合模块调用 |
| `code/warning_events.py` | 连续事件提取、未来 onset 标签和固定阈值事件评价 | 由 onset 分析及后续模型调用 |
| `code/onset_analysis.py` | 生成 1/3/7 日未来标签、事件清单和样本充分性盘点 | `figures/warning_onset/*`、`figures/thresholds/v0_thresholds.csv` |
| `code/shap_select.py` | 构造滞后样本、NGBoost 探索性回归/二分类、SHAP 和时间扩展窗口评价 | `figures/shap/*`、`figures/thresholds/v0_thresholds.csv` |
| `code/grid_interp.py` | 读取测点坐标并建立 IDW 规则网格插值器 | 由 `convlstm.py` 调用 |
| `code/block_bootstrap.py` | 生成非循环重叠日期块索引并计算百分位区间 | 由 `convlstm.py` 调用 |
| `code/convlstm.py` | 8 测点空间网格 ConvLSTM，输出 P10/P50/P90 位移 | `models/convlstm.pt`、`figures/convlstm/*` |
| `code/convlstm_rolling_validation.py` | 固定现有 ConvLSTM 结构，执行三个非重叠测试折的扩展窗口验证 | `figures/convlstm/rolling_validation_*.csv` |
| `code/ngboost_warn.py` | 使用动态 V0 当日四级标签训练 NGBoost 概率分类器 | `models/ngboost.pkl`、`figures/ngboost/*`、`figures/thresholds/v0_thresholds.csv` |
| `code/warning_fusion.py` | V0 主判、关键测点切线角升级复核、NGBoost 概率旁证 | `figures/warning_fusion/warning_fusion.csv` |
| `code/sensitivity_analysis.py` | 重算预先规定的 V0 与切线角参数组合并比较等级、事件和融合原因 | `figures/sensitivity/*` |
| `code/tangent_stage_review.py` | 为 MJ9/MJ1/MJ3 生成候选阶段复核图，并比较参数、切线角等级和融合影响 | `figures/tangent_angle/review/*` |
| `config/tangent_reference_stages.csv` | 人工等速阶段配置接口；当前所有条目均为 `candidate`，没有任何阶段被自动批准 | 由 `features.py` 加载并交给 `tangent_angle.py` 校验 |

## 4. 已锁定的实现选择

### 4.1 特征工程

- 位移速率：1 日一阶差分。
- 位移加速度：位移速率的一阶差分。
- 库水位速率：1 日一阶差分。
- 累计降雨窗口：7、15、30 日。
- 原始切线角：日增量除以 `v_eq` 后取反正切，并按许强等（2009）的严格 `>45`、`>80`、`>85` 阶段边界判定。
- 工程切线角：3 日尾随线性斜率，不使用未来观测；再应用 5 日内至少 3 次命中的持续性确认。
- 自动等速段：仅在前 80% 训练期内选择 30 日候选窗口，属于专家阶段划分前的辅助候选，不是原文方法本身。
- 人工等速阶段：`features.py` 每次正式运行时读取 `config/tangent_reference_stages.csv`，将 `status=approved` 的行交给 `tangent_angle.py`。配置表和直接参数使用同一套训练期、测点名、日期、样本数和正速率校验；同一测点仅允许一个批准阶段。当前无任何批准阶段。

### 4.2 ConvLSTM

- 8 测点通过 IDW 插值到 `4 x 7` 规则网格，属于真实二维卷积循环结构。
- 当前输入窗口：7 日。
- 当前预测步长：1 日。
- 输出：有序 P10/P50/P90 位移增量，再还原为累计位移。
- 损失：分位数 pinball loss。
- 评价：按测点及三个连续测试时段报告点误差、持久性基线、分位数损失、覆盖率、宽度和 80% interval score；R2/NSE 仅作趋势敏感的补充指标。
- 校准：原训练窗口前 80% 用于拟合、后 20% 连续日期用于按测点对称 split-conformal 校准；标准化和增量尺度只拟合于前者。时间自相关使经典覆盖保证不成立，因此结果按探索性校准报告。
- 不确定性：固定模型与校准量，以连续日期块同步重采样所有测点；14 日为预设主块长，7/30 日为敏感性分析，各 1000 次。输出模型-基线及校准-原始的配对差值，不把两个单独区间是否重叠当作差异检验。
- 滚动验证：测试长度沿用现有 287 日留出尺度，三个测试折互不重叠，训练历史逐折扩展；每折重新拟合标准化、增量尺度、模型和 `qhat`。固定同一随机种子以减少初始化差异，但不把固定种子解释为统计稳健性。

### 4.3 NGBoost

- 主模型：`NGBClassifier` 四分类概率模型。
- 标签：动态 V0 当日四级状态，不是切线角标签。
- 一级 `V0` 使用导师指定的稳定月均值-标准差公式；5/10 倍高等级阈值参考 Chen et al.（2024）式（10）的默认 `vd`。当前实现不包含该文的 GPD/POT、VaR 或 CVaR 估计。
- 输入：8 测点位移速率/加速度聚合量、库水位、库水位速率和多窗口累计降雨。
- 当前模型任务属于状态识别；未来 1/3/7 日 onset 标签已实现，但模型验证因独立事件不足而暂停。

### 4.4 预警融合

- V0 是主判规则，融合结果不得低于 V0 等级。
- 切线角仅使用 MJ9、MJ1、MJ3 三个关键测点执行升级复核。
- 单测点切线角异常最高升级为黄色观察状态。
- 多测点或多尺度一致时，才允许进一步升级。
- NGBoost 概率保留为旁证，不直接覆盖规则等级。

## 5. 运行顺序

```bash
uv run python main.py
```

`main.py` 按 `features -> onset -> shap -> convlstm -> convlstm-rolling -> ngboost -> fusion -> sensitivity -> tangent-review` 编排九个独立进程。使用 `--stage` 可选择阶段，`--skip` 可跳过阶段，`--dry-run` 可在不执行脚本时核对命令。阶段选择保持标准顺序，但不自动解析或补跑上游依赖。实际执行会将提交哈希、执行源码 SHA-256 指纹、运行环境、逐阶段状态、退出码和耗时写入 `figures/pipeline/latest_run.json`，失败时同样保留记录。

阶段契约在子进程前检查必需输入，在子进程后检查预期输出存在且本次运行已更新。清单为每个通过检查的输出保存相对路径、文件大小和 SHA-256；缺输入、缺输出或陈旧输出均使管线停止，不能仅凭脚本退出码 0 判定完成。

运行测试：

```bash
uv run --with pytest pytest -q
```

各阶段仍保持独立脚本，以便单独重跑和核对中间结果；统一入口只负责顺序、失败传播和耗时汇总，不改变模型内部实现。

## 6. 数据泄漏防线

1. 所有数据先按日期排序，训练期必须早于验证/测试期。
2. V0 和自动等速段只由训练期估计。
3. 滞后、滚动累计和平滑只允许使用当前及历史数据。
4. 同一日期的 8 个测点必须进入同一个数据分区。
5. 标准化参数只由训练期拟合。
6. 测试结果不能参与特征、阈值和超参数选择。

现有后 20% 数据已经参与多轮分析，因此只能作为探索性留出结果。后续确认性评价必须使用新的时间折、新监测时段或外部滑坡数据。

## 7. 完成标准

“脚本无报错”只说明工程管线可运行，不等于研究假设成立。每次正式实验至少满足：

- 代码和测试通过，输出文件可追溯到 Git 提交。
- 所有阈值和变换遵守训练期边界。
- 同时报告主模型、基线、类别/事件支持数和不确定性。
- 位移预测同时报告误差、区间覆盖率和宽度。
- 预警同时报告样本级、概率校准和事件级结果。
- 结论与证据等级一致，不将状态识别描述为提前预警。

## 8. 当前已知限制

- ConvLSTM 已启用独立时间校准，但 P10-P90 测试覆盖率仍低于名义 80%，最后连续测试块退化明显。
- ConvLSTM 已输出日期块 95% 置信区间，但其局部平稳假设与已观察到的后期漂移冲突；区间不包含训练过程和未来制度变化的不确定性。
- ConvLSTM 滚动验证只在第三折超过持久性基线；前两折均出现系统性高估。该差异可能同时来自训练样本量、优化和时间分布变化，当前尚未分离机制。
- NGBoost 未超过昨日状态持续性基线。
- 测试段无橙色和红色样本，不能评价高等级识别能力。
- 自动等速段尚未由专家阶段复核；15/30/60 日候选窗口会为关键测点选出显著不同的参考速率，并大幅改变融合结果。复核图和 CSV 参数表已生成（`figures/tangent_angle/review/`），人工配置接口已就绪（`config/tangent_reference_stages.csv`），等待专家独立确定等速阶段。
- 当前融合结果尚无完整事件级提前量和误报评价。
- 尚无外部时间或跨滑坡验证。

## 9. 下一阶段实现顺序

1. 获得包含更多独立 onset 的新监测时段或新滑坡数据。
2. 在固定滚动折上诊断 ConvLSTM 的初始化、训练样本量和时间漂移敏感性，不使用测试折选参。
3. 事件数量足以支持内外层评价后，重新调节 NGBoost，不再使用现有测试段选参。
4. 在新增时段上评价 ConvLSTM 静态校准的跨期稳定性，必要时预先设计滚动校准协议。
5. 根据累计位移曲线和宏观变形资料专家复核等速阶段，再冻结切线角参数。
6. 完成特征组消融和 SHAP 跨折稳定性分析。
7. 获得新时段或新滑坡数据后进行确认性验证。
