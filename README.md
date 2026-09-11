# Landslide-Warning

藕塘滑坡位移概率预测科研原型。当前阶段在同一剖面的 ATU1、ATU5、MJ3、MJ1 四点，
比较冻结 B+、物理引导 ConvLSTM 残差模型、方程内蠕变修正模型和状态 PINN 混合方法。

最新完成 [v1.20 固定两目标梯度诊断](docs/ootang_bplus_origin_gradients_results.v1.20.md)：
90 checkpoint、74.144 秒，0 训练/物理拟合。612 日 OOF 最终三个种子的配对块
梯度为拟合块的约 12–17 倍，共同负梯度方向均使拟合块局部上升，支持另版检验
目标尺度平衡；不代表原 Adam 实际方向或预测失败的唯一原因。13 项测试、72 项
差分及独立回放通过，整体目标未实现。原 NumPy 数值警告保留，另做标量复查一致。
[执行前方案](docs/ootang_bplus_origin_gradients_plan.v1.20.md)与全部负结果保持。

此前完成 [v1.19 起点历史 IN/OOF 有限学习](docs/ootang_bplus_origin_learning_results.v1.19.md)：
1,800 更新、506.138 秒，无新物理拟合/积分。两窗预测平均 RMSE：IN 58.9612/25.1138、
OOF 59.6529/33.6124，仍高于 B+ 的 58.0122/22.2448 mm；严格改善为 2/8、1/8。
总训练损失下降，但 612 日 OOF 一个种子的拟合块损失增加 13.7187%。15 项测试及
独立 checkpoint/样本/概率核验通过，整体目标未实现。下一步另版检查两训练块的
梯度大小与方向，再决定是否调整训练；[执行前方案](docs/ootang_bplus_origin_learning_plan.v1.19.md)
与全部旧失败、条件历史回测边界保持。

此前完成 [v1.18 固定历史网络依赖诊断](docs/ootang_bplus_history_diagnostics_results.v1.18.md)：
固定九个 H 权重，科学求值 408 次、12.480 秒，0 训练/物理拟合。历史响应非零，但
保留历史相对同权重屏蔽仅 3/8 点窗预测误差同时下降；432 日 ATU1/ATU5 全窗反向，
ATU5 预测修正需求 RMS 为训练样本的 32.2119 倍，612 日 MJ3 为 11.2766 倍。
13 项测试及独立回放/数值核验通过，整体目标未实现。下一步另版固定带历史和起点
步长的 IN/OOF 样本对照，保留 v1.5 旧 OOF 失败和全部条件历史回测限制。

此前完成 [v1.17 起点历史编码学习对照](docs/ootang_bplus_history_learning_results.v1.17.md)：
固定 1,800 更新、215.982 秒，无新物理拟合/积分。H 加入历史后相对同结构 C 的预测
误差在 7/8 点窗下降，但严格超过 B+ 仅 3/8，预测平均 RMSE 58.9127/24.8298 mm
仍高于 B+ 的 58.0122/22.2448；概率评分也未改善。13 项测试与全部 checkpoint/
时序/数值核验通过，整体目标未实现。[执行前方案](docs/ootang_bplus_history_learning_plan.v1.17.md)
和全部种子、负结果保持，下一步先检查固定模型对历史的依赖及修正需求差异。

此前完成 [v1.16 历史信息审计及补充接口核验](docs/ootang_bplus_history_availability_results.v1.16.md)：
原三来源等值核对失败，导师 CSV 有最大约 5e-12 mm 的末位差异；原门限和失败保留。
公开 CSV/XLSX 前 612 日六列一致，按[补充方案](docs/ootang_bplus_history_completion_plan.v1.16.1.md)
生成四起点各 30 日的历史位移/增量，补充执行 3.440 秒，0 新拟合或预测。
14 项测试及逐格核验通过。接口只读取过去，原始日值当时可用性仍未知；后续有限
学习对照见 v1.17。接口完成不代表整体四点精度/概率目标已实现。

此前完成 [v1.15 冻结修正分组与范围诊断](docs/ootang_bplus_temporal_decomposition_results.v1.15.md)：
762 行、3.237 秒，0 新拟合/神经/力学调用。第三窗 ATU1 的大负修正在数值上主要
对应 s/p 线性项，扣开超范围部分后均值仍反向；不能据此把限幅当成修复。6 项测试
与全部分解/原指标复核通过，整体目标仍未实现。源码核对发现当前四点 M1 的 u/du
来自 B+；后续预测起点历史信息审计见 v1.16，历史编码效果尚未验证。
[执行前方案](docs/ootang_bplus_temporal_decomposition_plan.v1.15.md)与
[分组热图](results/ootang_bplus_v1_15/20260911_temporal_decomposition/group_means.png)保留。

此前完成 [v1.14 训练内时序输入对照](docs/ootang_bplus_temporal_features_results.v1.14.md)：
固定 9 次岭回归，约 8.419 秒，0 新神经/力学调用。增加 30 日历史和 B+ 状态的 HHS
三窗预测平均 RMSE 为 16.3382/24.6583/72.1319 mm，P0 为 18.2392/29.5090/58.0122；
前两窗改善，第三窗明显恶化，不能据此直接扩充原神经模型。11 项测试、九组正规
方程及完整时序/指标复核通过，整体目标未实现。下一步先分解辅助修正和输入跨期变化。
[执行前方案](docs/ootang_bplus_temporal_features_plan.v1.14.md)及
[十二面板对照图](results/ootang_bplus_v1_14/20260911_temporal_features/temporal_corrections.png)保留。

此前完成 [v1.13 修正与尺度诊断](docs/ootang_bplus_rate_diagnostics_results.v1.13.md)：
只读冻结数组，0 新训练/力学调用。第一预测窗 ATU1/ATU5 的修正大多与实际需求反向，
第二窗 MJ3 三种子均整窗反向；第二窗没有倍率接近边界，单维输入越界也仅 7/180 日，
不能据此直接放宽倍率。原尺度跨期同时存在过大与不足，下一步先检查训练内修正信息
与时序关系，再决定有限对照。270 行诊断、9 项测试和全部数值复核通过，整体目标未实现。
[执行前方案](docs/ootang_bplus_rate_diagnostics_plan.v1.13.md)与
[需求/修正对照图](results/ootang_bplus_v1_13/20260911_rate_diagnostics/correction_demand.png)保留。

此前完成 [v1.12 直接力学输出学习](docs/ootang_bplus_rate_learning_results.v1.12.md)：
同一 G 通过原 Day 训练并预测，固定 1,800 次更新约 6.03 分钟，36 checkpoint 与
9 次原算法独立回放均通过核验。全部点窗训练误差下降，但预测平均 RMSE 为
58.8431/24.7192 mm，仍高于 B+ 的 58.0122/22.2448，严格改善仅 2/8。
概率区间仍有过宽和欠覆盖，整体目标未实现；[执行前方案](docs/ootang_bplus_rate_learning_plan.v1.12.md)
及旧负结果保持。后续修正通道与跨期尺度诊断见 v1.13，本版没有追加训练。

此前完成 [v1.11 共享力学路径验证](docs/ootang_bplus_shared_mechanics_results.v1.11.md)：
36 条验证轨迹、4 次完整历史反向约 7.945 秒，0 训练更新；12 组完整输出与旧 B+/R
的四点均值最大差 2.274e-13 mm，6/6 指定小步长导数及未来隔离检查通过。
[v1.11 执行前方案](docs/ootang_bplus_shared_mechanics_plan.v1.11.md)保留，本版验证本身不代表预测有效。

此前完成 [v1.10 冻结 PINN 梯度与轨迹诊断](docs/ootang_bplus_pinn_consistency_results.v1.10.md)：
52 次网络求值、324 次梯度、9 组代数分解，约 8 秒，0 训练更新/力学调用，数值检查通过。
342 日最终物理梯度约为数据梯度的 4,136–6,002 倍；612 日 ATU1/ATU5 的大 P/R 差
主要对应塑性状态差，不能只据此增大运动残差权重。旧 M2 已通过求解器训练但泛化失败。
同一速率网络下共享力学路径的原型验证见 v1.11；有限训练另行登记。
本诊断没有新的预测改善结果，整体目标仍未完成。

此前完成 [v1.9 状态 PINN 实验与补充核验](docs/ootang_bplus_state_pinn_results.v1.9.md)：
1,800 次训练更新、9 次原力学回放，约 7.68 分钟至核验前。原进程因保存前后检查项数
不同而退出 1，失败记录保留；独立补充核验通过，没有追加训练/积分或修改物理门限。
主输出 R 两窗预测平均 RMSE 为 57.7985/45.8646 mm，B+ 为 58.0122/22.2448；
严格改善 2/8，整体目标未实现。第二窗网络状态与力学回放明显分离，后续诊断见 v1.10。
[均值对比图](results/ootang_bplus_v1_9/20260911_state_pinn_summary/forecast_means.png)。

此前完成 [v1.7 同步重拟合与受限修正](docs/ootang_bplus_synchronized_correction_results.v1.7.md)：
18 个模型共 1,800 次更新、19.27 分钟，无新物理拟合/前向。普通/受限修正均只达到
3/8 点窗严格改善；训练拟合明显改善，但预测和概率评分仍未超过 B+。
[v1.8 PINN 真实子步核验](docs/ootang_bplus_pinn_substep_audit_results.v1.8.md)已完成：
三次积分、123,072 子步通过方程/活动集检查，日末四点均值与原保存值差为 0 mm。
[v1.9 核心实现记录](docs/ootang_bplus_state_pinn_implementation.v1.9.md)为训练前历史状态；
[有限方案](docs/ootang_bplus_state_pinn_plan.v1.9.md)和固定主输出定义保持。

此前完成 [v1.6 新旧 B+ 同日对照](docs/ootang_bplus_teacher_transfer_results.v1.6.md)：
固定网络和尺度，4 次原物理前向及 2 条独立力学核验，约 9.21 秒，无新训练。
旧教师下修正也未稳定改善四点；部分点更换教师后 B+ 已改善，网络仍保留相近的正修正，
导致过度修正。后续同步重拟合和受限修正的结果见 v1.7。
[执行前方案](docs/ootang_bplus_teacher_transfer_plan.v1.6.md)及全部负结果保持。

此前完成 [v1.5 ConvLSTM 样本来源对照](docs/ootang_bplus_sample_learning_results.v1.5.md)：
同一网络以拟合误差/过去外推误差训练，各两窗三种子 100 轮，共 1,200 次更新。
严格逐点同时改善分别为 2/8、0/8；外推样本组两窗平均预测 RMSE 58.1161/42.1026 mm，
B+ 为 58.0122/22.2448 mm，仍未稳定改善四点。尺度迁移与概率覆盖局限保留。
[执行前学习方案](docs/ootang_bplus_sample_learning_plan.v1.5.md) 与
[误差诊断结果](docs/ootang_bplus_error_structure_results.v1.5.md) 独立保留；本版按预算结束。
此前完成 [v1.4 优化及内部选模对照](docs/ootang_bplus_optimization_selection_results.v1.4.md)，
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

复核最新固定梯度及副本差分，不训练或写入产物；原 NumPy 警告可能再次出现：

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
