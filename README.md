# Landslide-Warning

藕塘滑坡位移概率预测科研原型。当前范围为同一剖面的 ATU1、ATU5、MJ3、MJ1 四点，
目标是通过改进 B+ 物理引导，降低预测误差并改善概率区间。

**2026-09-11：按用户要求暂停自动追加实验与诊断，先完成路线纠偏。**
[路线复盘与效果总表](docs/ootang_route_review_2026-09-11.md) 汇总八轮主要学习、16 个变体：
没有一个同时降低两个预测窗的平均 RMSE，也没有一个同时降低两窗平均 CRPS；严格四项改善最高 3/8。
当前保留 B+ 基线，整体精度与概率目标未完成。实现核验通过不代表预测有效。

最新 [v1.25 结果](docs/ootang_bplus_sequence_learning_results.v1.25.md)：四组严格改善均 2/8；
IN_CARRY 两窗预测平均 RMSE 为 59.2967/25.4071 mm，高于 B+ 的 58.0122/22.2448 mm，
四组两窗平均 CRPS 均未改善。全部旧模型、种子与负结果保留。

v1.26 未运行的计划、配置与草稿已从工作树删除；历史备份见 Git `7fc5f29`。
后续只有在一个具体候选的依据、总时间上限及失败退出条件确定后才考虑执行；当前没有新实验授权。
详细状态见 [progress](docs/progress.md)，逐窗概率与误差汇总见
[汇总数据](docs/ootang_route_review_2026-09-11_metrics.csv)。

原三组为 M0 改进 B+、M1 ConvLSTM、M2 方程内修正混合模型，后续另有状态 PINN。
M2 不是经典 PINN；早期八点 ConvLSTM–NGBoost–SHAP 属于历史阶段，不纳入当前四点比较。
所有旧方案、原始结果与方法边界保留在版本文档、产物与 Git 历史中。

## 历史四点实验的复现参考

下列命令仅保留为历史复现资料，不是当前待执行步骤。部分 `--verify` 仍会运行模型或梯度计算；
暂停期间不自动批量执行，当前工作范围以 [AGENTS.md](AGENTS.md) 和路线复盘为准。

复核最新同 lead 输入迁移的距离、选择、样本、时序和指标，不训练或写入产物：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python \
  -m physics_guided_input_transfer.run --run-id 20260911_input_transfer --verify
```

复核此前梯度平衡实验的 checkpoint、日志、样本、时序和概率，不训练或写入产物：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python \
  -m physics_guided_balanced_origin.run --run-id 20260911_balanced_origin --verify
```

复核此前固定梯度及副本差分，不训练或写入产物；原 NumPy 警告可能再次出现：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python \
  -m physics_guided_origin_gradients.run --run-id 20260911_origin_gradients --verify
```

仅以标量求和复核保存的梯度指标，无模型求值：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python results/ootang_bplus_v1_20/scalar_geometry_check.py
```

复核此前 IN/OOF 的 checkpoint、样本、时序和概率指标，不训练或写入产物：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python \
  -m physics_guided_origin_learning.run --run-id 20260911_origin_learning --verify
```

复核此前固定历史网络诊断，不训练、改参数或写入产物：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python \
  -m physics_guided_history_diagnostics.run --run-id 20260911_history_diagnostics --verify
```

复核历史学习的 checkpoint、样本、时间边界和概率指标，不更新模型或写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python \
  -m physics_guided_history_learning.run --run-id 20260911_history_learning --verify
```

复核历史接口及来源差异（保留原三来源比较失败），只读且不调用模型。
使用本机已配置的 bundled Python：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code \
/Users/wcqqq1214/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m physics_guided_history_completion.run --run-id 20260911_history_completion --verify
```

复核 v1.15 的有符号分组、范围与原指标，不新增拟合/神经/力学调用或写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_temporal_decomposition.run --run-id 20260911_temporal_decomposition --verify
```

复核 v1.14 的九组回归系数、输入、时序和指标，只代入检查，不重新拟合或写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_temporal_features.verify --run-id 20260911_temporal_features
```

复核 v1.13 保存的修正、倍率、输入和尺度诊断，不调用模型或写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_rate_diagnostics.verify --run-id 20260911_rate_diagnostics
```

复核 v1.12 保存的 checkpoint、力学轨迹、概率指标与时序，不新增网络/力学调用或写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_rate_learning.verify --run-id 20260911_rate_learning
```

复核 v1.11 已保存轨迹与导数证据，不新调用网络/力学求解器，不写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_shared_mechanics.verify --run-id 20260911_shared_mechanics
```

复核 v1.10 保存的梯度、差分和运动分解，不调用网络/力学求解器，不写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_pinn_consistency.verify --run-id 20260911_pinn_consistency
```

复核 v1.9 已保存模型与全部结果，保留原进程失败，不重训、不积分、不写文件：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_state_pinn.postcheck --run-id 20260911_state_pinn
```

复核 v1.8 的已保存子步、原活动集代数与 PINN 残差，不重新积分或训练：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=code .venv/bin/python -m physics_guided_pinn.run_substep_audit --run-id 20260911_substep_audit --verify
```

复核 v1.7 的同步重拟合、checkpoint、尺度和指标，不重训或新增物理前向：

```bash
PYTHONPATH=code .venv/bin/python -m physics_guided_synchronized_correction.verify --run-id 20260911_synchronized_correction
```

复核 v1.6 的同日对照、checkpoint、尺度和指标，不重训或新增物理前向：

```bash
PYTHONPATH=code .venv/bin/python -m physics_guided_teacher_transfer.verify --run-id 20260911_teacher_transfer
```

复核 v1.5 的已存 checkpoint、尺度和指标，不重新训练：

```bash
PYTHONPATH=code .venv/bin/python -m physics_guided_sample_learning.verify --run-id 20260911_sample_learning
```

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
