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
| `code/convlstm.py` | 8 测点空间网格 ConvLSTM，输出 P10/P50/P90 位移 | `models/convlstm.pt`、`figures/convlstm/*` |
| `code/ngboost_warn.py` | 使用动态 V0 当日四级标签训练 NGBoost 概率分类器 | `models/ngboost.pkl`、`figures/ngboost/*`、`figures/thresholds/v0_thresholds.csv` |
| `code/warning_fusion.py` | V0 主判、关键测点切线角升级复核、NGBoost 概率旁证 | `figures/warning_fusion/warning_fusion.csv` |
| `code/sensitivity_analysis.py` | 重算预先规定的 V0 与切线角参数组合并比较等级、事件和融合原因 | `figures/sensitivity/*` |
| `code/tangent_stage_review.py` | 为 MJ9/MJ1/MJ3 生成累计位移、速率、加速度和 15/30/60 日候选阶段复核图及 CSV 对比表 | `figures/tangent_angle/review/*` |
| `config/tangent_reference_stages.csv` | 人工等速阶段配置接口；当前所有条目均为 `candidate`，没有任何阶段被自动批准 | 由 `tangent_angle.py` 读取 |

## 4. 已锁定的实现选择

### 4.1 特征工程

- 位移速率：1 日一阶差分。
- 位移加速度：位移速率的一阶差分。
- 库水位速率：1 日一阶差分。
- 累计降雨窗口：7、15、30 日。
- 原始切线角：日增量除以 `v_eq` 后取反正切，并按许强等（2009）的严格 `>45`、`>80`、`>85` 阶段边界判定。
- 工程切线角：3 日尾随线性斜率，不使用未来观测；再应用 5 日内至少 3 次命中的持续性确认。
- 自动等速段：仅在前 80% 训练期内选择 30 日候选窗口，属于专家阶段划分前的辅助候选，不是原文方法本身。
- 人工等速阶段：通过 `config/tangent_reference_stages.csv` 配置。系统自动读取 `status=approved` 的行作为人工阶段；同一测点仅允许一个批准阶段，且必须完全位于训练期内。当前无任何批准阶段。

### 4.2 ConvLSTM

- 8 测点通过 IDW 插值到 `4 x 7` 规则网格，属于真实二维卷积循环结构。
- 当前输入窗口：7 日。
- 当前预测步长：1 日。
- 输出：有序 P10/P50/P90 位移增量，再还原为累计位移。
- 损失：分位数 pinball loss。
- 当前校准比例 `CAL_FRAC=0.0`，保形校准尚未启用。

### 4.3 NGBoost

- 主模型：`NGBClassifier` 四分类概率模型。
- 标签：动态 V0 当日四级状态，不是切线角标签。
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
uv run python code/features.py
uv run python code/onset_analysis.py
uv run python code/shap_select.py
uv run python code/convlstm.py
uv run python code/ngboost_warn.py
uv run python code/warning_fusion.py
uv run python code/sensitivity_analysis.py
```

运行测试：

```bash
uv run --with pytest pytest -q
```

`main.py` 不是当前执行入口。各阶段保持独立，是为了允许单独重跑和核对中间结果。

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

- ConvLSTM 的 P10-P90 覆盖率低于名义 80%，尚未完成校准。
- NGBoost 未超过昨日状态持续性基线。
- 测试段无橙色和红色样本，不能评价高等级识别能力。
- 自动等速段尚未由专家阶段复核；15/30/60 日候选窗口会为关键测点选出显著不同的参考速率，并大幅改变融合结果。复核图和 CSV 参数表已生成（`figures/tangent_angle/review/`），人工配置接口已就绪（`config/tangent_reference_stages.csv`），等待专家独立确定等速阶段。
- 当前融合结果尚无完整事件级提前量和误报评价。
- 尚无外部时间或跨滑坡验证。

## 9. 下一阶段实现顺序

1. 获得包含更多独立 onset 的新监测时段或新滑坡数据。
2. 建立按日期的滚动时间验证和事件级评价。
3. 事件数量足以支持内外层评价后，重新调节 NGBoost，不再使用现有测试段选参。
4. 启用 ConvLSTM 校准集并评价校准前后覆盖率和宽度。
5. 根据累计位移曲线和宏观变形资料专家复核等速阶段，再冻结切线角参数。
6. 完成特征组消融和 SHAP 跨折稳定性分析。
7. 获得新时段或新滑坡数据后进行确认性验证。
