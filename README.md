# Landslide-Warning

基于机器学习方法的水库滑坡位移预测与预警研究代码仓库。当前以三峡库区藕塘滑坡日尺度监测数据为例，完成从特征工程、位移预测、状态分类、SHAP 解释、动态阈值到多指标融合预警的端到端流程。

> 当前代码已跑通完整研究框架；现有结果属于同一份数据上的探索性内部时间验证，不应直接表述为最终确认性泛化证据。

## 当前状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 统一管线 | 已完成 | `main.py` 运行 13 个阶段，最近一次完整运行 13/13 通过 |
| ConvLSTM 位移预测 | 已完成初步建模与诊断 | 已输出单次留出、滚动验证、五种子、早停和容量敏感性结果 |
| NGBoost 状态分类 | 已完成初步模型 | 当前任务为动态 V0 当日状态识别，不是未来 onset 预警 |
| SHAP 解释 | 已完成 | 已输出单次 SHAP、五折稳定性和特征组消融 |
| V0/切线角融合 | 已跑通 | 8 个测点均进入切线角融合；参考等速阶段仍需导师或现场资料确认 |
| 未来 onset | 已生成标签和事件清单 | 当前仅 3 个可预测独立事件，暂不做正式调参宣称 |

## 快速运行

项目使用 `uv` 管理依赖，Python 版本为 3.10。

```bash
uv sync
uv run python main.py
```

完整运行会把提交哈希、源码指纹、各阶段状态、耗时和产物 SHA-256 写入 `figures/pipeline/latest_run.json`。

## 代码结构

```text
.
├── main.py                  # 统一管线入口
├── code/                    # 按流程分组的特征、预警、解释和 ConvLSTM 脚本
│   ├── features/            # 特征工程、切线角和等速阶段复核
│   ├── warning/             # V0 阈值、事件、NGBoost、融合和敏感性分析
│   ├── explainability/      # SHAP 分析和稳定性验证
│   └── convlstm/            # ConvLSTM 预测模型及滚动/稳定性/容量诊断
├── data/                    # 原始数据、测点坐标和派生特征
├── models/                  # 可再生成的模型文件
├── figures/                 # 可再生成的图表、指标和审计表
└── docs/                    # 研究框架、代码设计、结果报告和限制说明
```

核心脚本：

| 脚本 | 作用 |
| --- | --- |
| `code/features/build_features.py` | 生成位移速率、加速度、降雨/库水位特征和切线角特征 |
| `code/warning/onset_analysis.py` | 生成未来 1/3/7 日 onset 标签和事件盘点 |
| `code/explainability/shap_select.py` | 训练 NGBoost 解释模型并输出 SHAP 图和重要性 |
| `code/explainability/shap_stability.py` | 执行五折 SHAP 稳定性和特征组消融 |
| `code/convlstm/model.py` | 训练 ConvLSTM 位移 P10/P50/P90 区间预测模型 |
| `code/convlstm/rolling_validation.py` | 执行三个扩展窗口滚动时间验证 |
| `code/convlstm/seed_stability.py` | 执行固定协议五种子稳定性诊断 |
| `code/convlstm/inner_validation.py` | 执行内层时间验证和早停诊断 |
| `code/convlstm/capacity_sensitivity.py` | 执行有限容量和正则化敏感性诊断 |
| `code/warning/ngboost_warn.py` | 训练 NGBoost 当日四级状态概率模型 |
| `code/warning/warning_fusion.py` | 融合 V0、切线角和 NGBoost 概率旁证 |
| `code/warning/sensitivity_analysis.py` | 执行 V0 与切线角预设参数敏感性分析 |
| `code/features/tangent_stage_review.py` | 生成 8 个测点等速阶段复核材料 |

## 管线阶段

`main.py` 的默认顺序为：

```text
features -> onset -> shap -> shap-stability -> convlstm
-> convlstm-rolling -> convlstm-seeds -> convlstm-inner-validation
-> convlstm-capacity -> ngboost -> fusion -> sensitivity -> tangent-review
```

各阶段声明输入和输出。管线会在执行前检查输入是否存在，并在执行后检查预期产物是否更新；阶段失败时立即停止。

## 主要结果入口

| 文件 | 内容 |
| --- | --- |
| `docs/framework.md` | 研究框架、验证规则和报告边界 |
| `docs/design.md` | 代码架构和模块边界 |
| `docs/results_report.md` | 当前完整探索性结果和科研表述边界 |
| `docs/framework_status.md` | 研究框架覆盖、已实现指标和缺口 |
| `docs/progress.md` | 阶段进度和运行记录 |
| `docs/warning-limitations.md` | 预警、阈值、SHAP 和验证限制 |
| `figures/README.md` | 每个 PNG/CSV 的用途和保留原则 |

必要图表：

| 图 | 用途 |
| --- | --- |
| `figures/convlstm/forecast_interval.png` | ConvLSTM 位移预测区间 |
| `figures/ngboost/confusion_matrix.png` | NGBoost 当日四级状态混淆矩阵 |
| `figures/shap/shap_reg_summary.png` | 位移增量回归 SHAP 摘要 |
| `figures/shap/shap_cls_summary.png` | 当日状态分类 SHAP 摘要 |
| `figures/shap/stability/shap_group_stability.png` | SHAP 特征组跨折稳定性 |
| `figures/shap/stability/group_ablation.png` | 特征组消融结果 |
| `figures/tangent_angle/review/*_stage_review.png` | 8 个测点等速阶段复核图 |

## 当前结论边界

- ConvLSTM 在最后 287 日留出段略优于持久性基线，但三个滚动时间折中前两折未优于基线，因此不能声称跨时期稳定优于基线。
- NGBoost 当前识别的是当日动态 V0 状态；留出段没有 orange/red 样本，不能评价高等级预警召回。
- SHAP 结果描述模型依赖关系，不代表致灾因果关系。
- V0 和切线角规则已跑通，但切线角参考等速阶段尚未由导师或现场资料确认，不能写成确认性切线角结论。
- 当前数据已被多轮探索使用；最终投稿需要新增时段、外部滑坡或其他确认性验证支持。
