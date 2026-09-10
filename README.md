# Landslide-Warning

藕塘滑坡位移概率预测科研原型。当前阶段在同一剖面的 ATU1、ATU5、MJ3、MJ1 四点，
比较冻结 B+、物理引导 ConvLSTM 残差模型和方程内蠕变修正模型。

最新完成 [v1.4 优化及内部选模对照](docs/ootang_bplus_optimization_selection_results.v1.4.md)，
仅使用前 792 日。同前缀续算的训练目标小幅下降，但未达既定梯度容差；内部选模在一个
历史窗口改善预测、另一个窗口变差，逐点严格同时改善为 **0/8**。本次未新增神经网络训练。
[v1.4 检查](docs/ootang_bplus_optimization_review.v1.4.md) 与
[执行前方案](docs/ootang_bplus_optimization_selection_plan.v1.4.md) 独立保留。
[v1.3 结果](docs/ootang_bplus_increment_results.v1.3.md) 保留两个窗口预测误差变差、1/8 的结论。
前版 [v1.2 前缀诊断结果](docs/ootang_bplus_diagnostics_results.v1.2.md) 独立保留。
已完成的三模型规格为[实验计划 v1.1](docs/ootang_bplus_probabilistic_experiment_plan.v1.1.md)，
开发、验证及结果见[实施记录](docs/ootang_bplus_probabilistic_implementation.v1_1.md)。
本阶段不做预警、不换案例。旧 ConvLSTM–NGBoost–SHAP 结果保留为历史阶段证据。
v1.1 有限实验已完成：M1/M2 均选择零轮修正，未改善 B+，当前保留 M0 基线。

## 当前四点 B+ 实验

复算已归档 v1.4 结果，不重新拟合或写入：

```bash
PYTHONPATH=code .venv/bin/python -m physics_guided_optimization_selection.verify results/ootang_bplus_v1_4/20260911_optimization
PYTHONPATH=code .venv/bin/python -m physics_guided_optimization_selection.verify results/ootang_bplus_v1_4/20260911_selection
```

两项实验的来源、固定预算、新拟合入口和结果边界见 v1.4 结果记录；新实验使用独立 run id。
前版诊断的复现方式见各自结果记录。下列命令保留用于独立复现 v1.1：

```bash
PYTHONPATH=code .venv/bin/python -m physics_guided.run \
  --run-id example_new_run --phase all
```

每次实验使用独立 `run_id`。结果写入 `results/ootang_bplus_v1_1/<run_id>/`；分阶段入口、
所需原始 ZIP、标签隔离、历史回测边界及失败处理见实施记录。该入口与旧默认流程独立。

## 历史阶段：八点预测与代理预警流程

```text
多源监测数据与逐点运动学特征
  → ConvLSTM 全部 8 测点概率位移预测
  → P10–P90 预测区间及覆盖率评价
  → 区间偏离、逐点速度、严格逐点加速度、改进切线角
  → H=7 多测点未来状态自动标签
  → site NGBoost 五分类概率预警
  → NGBoost SHAP
  → 测点级与滑坡体级逐时预警
```

速度与加速度使用真实相邻时间差：

```text
v_i = (U_i - U_{i-1}) / (t_i - t_{i-1})
a_i = (v_i - v_{i-1}) / (t_i - t_{i-1})
```

当前 SHAP 解释的是五级 **site NGBoost 分类器**的期望预警等级，只能说明模型依赖，
不能证明物理因果主控因素。当前汇总图展示全部 `8×4=32` 项输入，不再截取 Top-k。
早期独立 NGBoost 回归 SHAP 的执行链已经退役；保留的少量旧图仅作历史快照。

## 当前结果边界

- ConvLSTM 已输出全部 8 个测点的 P10/P50/P90、训练/校准/评价分段和区间指标。
- 四项预警指标均进入 8 测点 × 4 指标的 32 维 site 分类输入，不划分主、副指标。
- H=7 标签由未来多点变形代理状态自动生成，不人工逐时判级，也不以同一时刻规则颜色作为标签。
- 固定 NGBoost 已生成五级概率、逐时颜色和完整 32 项分类 SHAP，但未超过严格 lag-7 persistence；该负结果保留。
- 当前结果是公开物化历史序列上的可复算探索性结果，`formal_warning_output=false`，不代表现场正式预警。

四个常见样本数使用不同时间口径，不能混写：

| 口径 | 数量 | 含义 |
| --- | ---: | --- |
| 原始物化日序列 | 1,461 日 | 2016-07-01 至 2020-06-30；运动学长表为 `1461×8` 行 |
| ConvLSTM 一步预测 | 1,425 日 | 2016-08-06 至 2020-06-30；fit/calibration/test 为 911/227/287 日 |
| NGBoost 模型可用时间 | 861 日 | 三个连续 287 日折；逐时分类任务，SHAP 从固定折内样本解释 |
| v4 透明规则基线 | 514 日 | 满足该基线自身输入和评价窗口的日期；测点表为 `514×8` 行 |

861 日均有模型预测；其中 840 日已有成熟的 H=7 自动代理标签，每折末端合计 21 日因未来
窗口尚未成熟而显式标灰，不能静默删除或当作真实标签。

在现有实验中，v4 是透明诊断基线，NGBoost 是概率分类模型。Vajont 未参与这些结果。
旧阶段的案例启动门禁已撤销；此次文档更新未启动 Vajont 或其他新实验。

## 快速复现藕塘流程

项目使用 `uv` 和 Python `>=3.10`：

```bash
uv sync
uv run python main.py \
  --stage ootang-operational-v4 \
  --stage ootang-ngboost-auto-state \
  --stage ootang-ngboost-auto-state-ecdf \
  --stage ootang-ngboost-auto-state-classifier \
  --stage ootang-advisor-package \
  --manifest figures/pipeline/ootang_advisor_demo_run.json
```

该命令复用已有 ConvLSTM 预测，不重训 ConvLSTM，不运行已拒绝的 memory/residual challenger，
不启动 Vajont，也不根据负结果重新调参。

## 文档与证据入口

- [文档导航与权威顺序](docs/README.md)
- [当前方法与结果初稿](docs/ootang_manuscript_methods_results_draft.md)
- [阶段结果与证据边界](docs/ootang_stage_results_package.md)
- [H=7 自动标签及五分类实验记录](docs/ootang_ngboost_auto_state_experiment_plan.md)
- [当前阶段报告与编译说明](paper/README.md)
- [当前阶段报告源文件](paper/process_report.tex)
- [当前 NGBoost 分类 SHAP 图](figures/ngboost_auto_state_classifier_v1/site_shap_summary.pdf)
- [机器生成的 8 测点 ConvLSTM 汇总图](figures/convlstm/forecast_all_stations.png)
- [逐时五级预警图](figures/ngboost_auto_state_classifier_v1/warning_timeline.pdf)

[`figures/advisor_ootang_v1/advisor_summary.md`](figures/advisor_ootang_v1/advisor_summary.md)
是机械生成的证据索引，不替代当前 `paper/process_report.tex`。

## 代码结构

```text
main.py                         # 统一阶段入口
code/features/                  # 逐点运动学与输入特征
code/convlstm/                  # ConvLSTM 概率位移预测
code/physics_guided/            # 当前四点 B+、ConvLSTM 残差与方程内修正比较
code/physics_guided_diagnostics/ # v1.2 前缀标定与滚动诊断
code/physics_guided_increment/   # v1.3 固定位移/增量目标对照
code/warning/                   # 自动标签、NGBoost 分类和多点输出
code/reporting/                 # 既有证据的机械汇总
config/                         # 版本化实验配置
data/                           # 藕塘输入与派生数据
figures/                        # 当前结果、图表、运行清单及少量历史快照
docs/                           # 当前文档、参考记录与历史审计
paper/                          # 当前阶段报告、可编辑流程图和数据驱动图件
```
