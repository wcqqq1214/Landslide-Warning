# 藕塘四点 B+ 概率位移预测 v1.1：实施与复现记录

日期：2026-09-10。执行规格：[实验计划 v1.1](ootang_bplus_probabilistic_experiment_plan.v1.1.md)。
运行目录：[`results/ootang_bplus_v1_1/20260910_implementation/`](../results/ootang_bplus_v1_1/20260910_implementation/)。

## Material Passport

- 工作模式：按用户授权开发并执行固定实验；ARS 用于可复现性与结论边界核对。
- 资料：仓库物化日序列、导师原始 PDF、ZIP 最终交付；没有引入新案例或新观测。
- 资料核对：代理实际读取 PDF 第 3、6–11 页及最终源码；PDF 结构预检通过。不是用户已读声明。
- 方法权限：按冻结 v1.1 实施；不变更日期、起点、种子、模型规模、损失、选模或验收规则。
- 实施、数值验证、效果评价与用户验收分别记录；历史回测不能称作新盲测。

## 独立入口与代码

```bash
# 在仓库根目录；选择新的 run_id，避免覆盖已有实验。
PYTHONPATH=code .venv/bin/python -m physics_guided.run \
  --run-id example_new_run --phase all

# 同一实现的分阶段运行：prepare → calibrate → validate → develop → final → report
PYTHONPATH=code .venv/bin/python -m physics_guided.run \
  --run-id example_new_run --phase prepare
```

`all` 包含两起点各三阶段标定、两路线三个种子开发、轮数锁定、最终重训和统一评价。
`report` 从已保存的数组重建 CSV/图件，不训练模型。部分训练中断的目录会保留，不能自动
覆盖；需要修复的工程错误应记录原因、代码版本及受影响范围，再用新运行目录重算。
源码或配置不同的选模结果不能直接用于最终重训。当前入口不会调用 `main.py` 的旧预警阶段。

| 文件 | 职责 |
| --- | --- |
| `code/physics_guided/reference.py` | 按 ZIP 内最终交付路径解出必要依赖，验证原 ZIP 哈希，编译原 C 求解器 |
| `data.py`、`features.py` | 日期、轴、训练标签与预测驱动分离，训练段固定缩放，20/38 维特征 |
| `calibration.py` | 独立 A/B 起点、三阶段前缀标定、调用计数、终止状态 |
| `mechanics.c`、`mechanics.py` | 每日 64 子步动力学、活动集检查、保留完整历史的日级反向传播 |
| `models.py`、`training.py` | M1、M2、冻结均值后的尺度头、三种子共同 checkpoint 选择 |
| `probability.py`、`reporting.py` | 混合 CDF 分位数、解析 CRPS、统一掩码评分及四点图件 |
| `validation.py`、`tests/test_physics_guided.py` | 零修正、完整梯度、切换连续性、独立展开与概率积分核对 |
| `run.py`、`protocol.py` | 独立命令、配置合同、来源记录、数值门禁与失败记录 |

配置为 [`config/ootang_bplus_probabilistic.v1_1.json`](../config/ootang_bplus_probabilistic.v1_1.json)。
`protocol.specification()` 与配置严格一致才可执行；不能编辑 JSON 后让未实现的新默认悄悄生效。
原 ConvLSTM 模块、权重、图件和既有负结果保持原版本。

## 原始资料、平台适配与数据

PDF 第 3 页核对四坐标与三滑体关系；第 6 页核对首日参考、水头及含水更新；第 8–9 页核对
含水蠕变、Kelvin 初始分量和不可逆内滑移；第 11 页核对式 (22)–(27) 的子步与标定目标。
使用机器精度 `Context.obs`，MJ3/MJ1 是同一 O1 场的不同投影，不把水文域当作测点列直接互换。

ZIP 根 SHA-256 为 `57f3e7c68f004a47875c59ee93d38c384900b5263f111341fdd4aee36fdcc966`。
读取根为 `section2d_v4/work/delivery_final/outang_repro/`，保留几何文件在上一级的路径。
冻结 Python/C 源码原样保留在忽略的 `runtime/` 副本；只另生成动态库后缀和相对 geometry
导入适配的 Python 文件。源码、几何、查表、输入、参数、编译器和动态库哈希见运行目录的
`reference_provenance.json`；不重新拟合导师参数来消除平台差异。

仓库列名的 `/mm`、`/m` 与 ZIP 简化列名显式映射。两份 CSV 的字节哈希不同，但对应四点
最大数值差为 `5.0022e-12 mm`，驱动差为 `1.4211e-14`，来自十进制导出精度；日期完全一致。
雨量不作再次差分，缺日、重复、非有限输入或超出查表的库水位直接失败。

本平台原 NumPy 矩阵乘法曾输出 divide/overflow/invalid 的 RuntimeWarning，结果数组仍有限，
并通过逐值原结果对照；保留原始日志，未对物理结果做裁切、填补或重拟合。NumPy、SciPy、
PyTorch、编译器等实际版本另见运行 manifest。这些警告不被当作模型有效性的证据。

## 时间、输入及训练合同

- 点轴 `ATU1, ATU5, MJ3, MJ1`；物理域轴 `O3, O2, O1_up, O1_down`。
- 内部训练 792 日，开发预测 376 日；最终训练 1168 日，历史预测 293 日。
- 三组均排除首 30 日主评分，训练有效分母分别为 762、1138 日；预测段不追加预热。
- 预测函数只接受 `Drivers(dates, forcing, y0)`、冻结物理和神经参数、冻结 scaler；没有预测期
  实测位移输入。训练器接收截断到允许日期的标签；最终训练 API 拒绝开发/最终评分标签。
- M1 的每个 30 日窗口从零初始化 h/c；分块只累积同一平均损失梯度，每轮仅更新一次。
- M2 每天水文更新后，以自身前日力学状态调用网络一次。4 域修正倍率为 0.5–2；背景变化
  进入子步力学反馈。自定义反向只保存活动集，反向走完每个日内子步与全部历史；没有截断。
- 均值和尺度分别按规定训练，三种子等权混合共同选轮数。最终段不重新选择轮数或区间参数。
- 所有区间为逐日逐点边际分布；不表示空间联合协方差或整条轨迹同时覆盖。

## 数值验证及运行状态

原 B+ 1461 日位移最大复现差 `6.8212e-13 mm`，原预测 CSV 最大差 `5.4570e-12 mm`。
原口径合并 RMSE：1168 日拟合 `8.7467403 mm`，293 日历史预测 `9.4506300 mm`。
完整输入与只提供过去驱动的前缀完全一致。

高斯混合 CRPS 独立 CDF 数值积分绝对差 `3.7792e-13 mm`。M1 分块与整批梯度一致；M2
多日 C 自定义反向与逐子步 PyTorch 展开一致。固定非零神经权重的完整 792 日四方向差分
使用 `1e-4,1e-5,1e-6` 三步长；记录位于 `validation_frozen.json`、`validation_prefix.json`。
切换点另用独立 NNLS 核对 LCP 和各侧导数，不要求切换处有唯一中心导数。

A/B 原始起点和各阶段 theta、目标、nfev/njev、实际求解调用、时长、success 和终止原因均保留。
选中 B 的第三阶段目标为 `53.470942`，A 为 `141.89184`；选择只使用内部训练目标。B 的三阶段
均达到 `max_nfev`，结果不是已收敛解或全局最优解。初次标定在命令入口整合前执行；原阶段记录
保留，后续 manifest 记录整合源码，其格式化不改变标定算法；不伪称存在初次启动时尚未生成的
源码 manifest。

## 固定开发预算与选择结果

两路线各三个种子均完成 200 个均值 epoch、100 个尺度 epoch，合计 1,800 次优化器更新。
保存全部规定 checkpoint；没有删除较差种子、增添起点或延长预算。

| 路线 | e_mu | e_sigma | 开发四点平均 RMSE / mm | 开发混合 CRPS / mm | 均值+尺度参数数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| M0 | 不适用 | 不适用 | 41.3684 | 不适用 | 54 个冻结物理参数 |
| M1 | 0 | 0 | 41.3684 | 28.1156 | 7346 |
| M2 | 0 | 0 | 41.3684 | 28.1156 | 1317 |

两个路线的已训练均值 checkpoint 均比 0 轮差，已训练尺度 checkpoint 的开发 CRPS 也均比
0 轮差。因此共同选择 0 轮，不将其改写为神经修正有效。按计划的平分决胜规则，M2 因参数数
更少成为候选；它不是效果更好的方案，M0 仍是保留基线。

完整选择表见 [`selection.json`](../results/ootang_bplus_v1_1/20260910_implementation/selection.json)。
最终阶段以相同三个种子重新初始化均值/尺度网络，在前 1168 日重新拟合 scaler 和尺度初值，
按锁定的 0/0 轮运行；没有梯度更新，也没有使用最后 293 日标签选轮数。所有最终训练日志
均保留为空的零更新记录，这符合计划规定的 0 轮候选，不是漏跑开发训练。

## 最终历史回测与逐点验收

以下均值误差对 M0、M1、M2 在数值容差内相同；三个种子也没有有效均值分歧。训练分母为
1138 日，预测分母为 293 日；单位 mm。

| 测点 | 训练 RMSE | 训练 MAE | 预测 RMSE | 预测 MAE |
| --- | ---: | ---: | ---: | ---: |
| ATU1 | 9.5351 | 7.4748 | 8.0682 | 5.4318 |
| ATU5 | 11.7879 | 9.2522 | 9.1150 | 8.1080 |
| MJ3 | 5.5144 | 4.5953 | 13.0163 | 8.7791 |
| MJ1 | 7.1812 | 5.5326 | 6.2972 | 5.2879 |

四点 RMSE 算术平均：训练 **8.5046 mm**、预测 **9.1242 mm**；全部点日合并 RMSE：训练
**8.8296 mm**、预测 **9.4506 mm**。不要把这两种汇总口径混称。原 1168 日拟合口径的
8.7467 mm 单独保留在复现表，不与剔除预热后的 8.8296 mm 混用。

相对 M0 的逐点 RMSE/MAE 差值保留在
[`acceptance.json`](../results/ootang_bplus_v1_1/20260910_implementation/acceptance.json)；差异仅为
浮点量级，没有任何点在训练、预测的两项误差上同时改善超过 `1e-6 mm`。
**M1 为 0/4，M2 为 0/4；本次没有完整改善任何一点的均值目标。**

两路线的尺度也停留在初始化，sigma 约 **8.8296 mm**，三分量混合在数值容差内等于同一个
常尺度高斯分布。该尺度来自训练残差，不是学得的时变不确定性，也不是给 M0 另加了概率评分。
M0 的全部概率字段继续为 NA。

| 测点 | 90% 覆盖率 | 90% 平均宽度 / mm | CRPS / mm | 90% 区间评分 / mm |
| --- | ---: | ---: | ---: | ---: |
| ATU1 | 93.86% | 29.0469 | 4.1599 | 42.6575 |
| ATU5 | 100.00% | 29.0469 | 5.3955 | 29.0469 |
| MJ3 | 72.70% | 29.0469 | 7.1005 | 76.1455 |
| MJ1 | 100.00% | 29.0469 | 3.7301 | 29.0469 |

四点平均覆盖为 **91.64%**、CRPS 为 **5.0965 mm**、90% 区间评分为 **44.2242 mm**。
平均覆盖接近名义水平掩盖了点间不均衡：MJ3 仅 72.70%，ATU5 和 MJ1 为 100%。本轮没有
依据这些最终结果放宽区间、改变训练轮数或加入新模型。80%/95% 结果同样完整保留在 CSV。

## 交付与核对

- [`predictions.csv`](../results/ootang_bplus_v1_1/20260910_implementation/predictions.csv)：94,644 行；
  两阶段、全部日期、四点及 M0 的 1 个分量和 M1/M2 各 4 个分量。
- [`metrics.csv`](../results/ootang_bplus_v1_1/20260910_implementation/metrics.csv)：2,196 行；
  逐点、四点等权平均、合并 RMSE、共同日期分母及掩码哈希。
- [`source_snapshot/`](../results/ootang_bplus_v1_1/20260910_implementation/source_snapshot/)：训练所用
  精确源码与配置快照；204 个均值/尺度 checkpoint、三种子优化日志及 M2 状态另存对应阶段目录。
- [`calibration_postcheck.json`](../results/ootang_bplus_v1_1/20260910_implementation/calibration_postcheck.json)：
  两候选逐子步复核、前缀标签扰动目标不变、实际调用口径。目标/Jacobian 调用为 214,374 次；
  加上两次初始活动检查和两次末态候选检查，标定前向调用共 214,378 次，约 752 秒。
  标签扰动核对了传入前缀与目标函数，不冒称另做了一次完整重标定。
- [`physics_package_development.json`](../results/ootang_bplus_v1_1/20260910_implementation/physics_package_development.json)
  与 [`physics_package_final.json`](../results/ootang_bplus_v1_1/20260910_implementation/physics_package_final.json)：
  物理版本、拟合截止日期、参数、完整初态、几何/查表/源码等来源哈希。
- [ATU1](../results/ootang_bplus_v1_1/20260910_implementation/figures/ATU1_forecast.png)、
  [ATU5](../results/ootang_bplus_v1_1/20260910_implementation/figures/ATU5_forecast.png)、
  [MJ3](../results/ootang_bplus_v1_1/20260910_implementation/figures/MJ3_forecast.png)、
  [MJ1](../results/ootang_bplus_v1_1/20260910_implementation/figures/MJ1_forecast.png)：
  全时段、历史预测放大及预测残差；M0/M1/M2 均值曲线重合是零轮结果。

全仓库 268 项测试通过；最后的导出修正及独立产物核对的 18 项针对性测试也通过，四张最新 PNG 逐张目视检查通过。数值验证包括原 B+
复现、原子步约束、完整 792 日四方向梯度、独立 PyTorch 多日展开、LCP 的 NNLS/单侧导数、
CRPS 数值积分及混合 CDF/逐点指标重算。全部原始日志保留。

最终目视检查修正了放大图仍沿用全时段纵轴的问题；同时修复失败路线在验收 JSON 中应使用
null 和 `not_evaluated` 的分支。只改导出层，重新生成图件与验收文件；预测/指标 CSV 的哈希
逐个保持不变，未重新训练或改变选择。具体代码版本关系见
[`export_revision.json`](../results/ootang_bplus_v1_1/20260910_implementation/export_revision.json)。

## 结论、限制与下一步

实施与有限比较已完成；本次结果支持保留 M0，**不支持智能修正提高四点位移预测精度**，也
不能宣称 M2/PINN 优于 ConvLSTM。模型没有获得足以取代零修正的开发成绩，是应保留的负结果。
这不等于已证明所有物理引导概率方法无效，也没有单独识别失败原因。

后 293 日是已暴露的历史回测；单案例、物化日数据、物理参数可辨识性、前缀标定未收敛、
驱动条件已知、三种子不足以代表跨案例泛化、边际区间不覆盖全部参数/未来气象/几何不确定性
等限制继续成立。技术实施完成和数值验证通过不等同于导师/用户验收，验收仍待单独确认。

v1.1 预算到此结束，不自动增添模型、种子、物理起点或修改评价期。下一步可供讨论的是先诊断
开发参考与最终参考的迁移、训练段残差修正为何未外推；若要开展新实验，须另记设计版本及
原因，并保留本次全部选择表和负结果。
