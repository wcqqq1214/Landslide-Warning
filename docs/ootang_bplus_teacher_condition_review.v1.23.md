# 藕塘教师条件与时序表达审查 v1.23

## Material Passport

- 日期：2026-09-11；Origin Skill/Mode：academic-research-suite / experiment-agent / plan。
- 依据：[执行前方案](ootang_bplus_teacher_condition_review_plan.v1.23.md)、
  [v1.22 结果](ootang_bplus_input_transfer_results.v1.22.md)及导师原始交付。
- 来源/样本审查及记录重建通过；本版没有模型效果实验、神经求值/梯度/训练、
  物理积分/拟合、回归/scaler/概率尺度拟合。实现核验与研究有效性分开。
- 审查结论：优先进入**历史与未来时间对齐、编码状态连续传递**的有限接口验证。
  暂不选择直接增加教师编号或 54 维参数的学习支线。候选有效性尚未验证，
  稳定四点均值及概率提升、用户/导师接受均未由本审查完成。

## 1. 输入中已有与缺少的信息

当前网络由 `HistoryM1` 继承原 `M1.forward`，用于 v1.19/v1.21 的两来源对照。
以下依据当前冻结源码，而非根据通道名称猜测数据来源。

| 输入组 | 通道数 | 实际内容及信息路径 |
| --- | ---: | --- |
| B+ 位移 | 2 | `u, du`，来自对应教师的物理位移及日增量 |
| 水文与水力状态 | 14 | 雨量、库水位及变化、7/30 日雨量、库水记忆、四域水头及含水状态 |
| 静态位置 | 4 | 点位坐标及 O3/O2/O1 指示 |
| 近期观测 | 2 | 历史 `实测−B+` 残差及其日差；并非两路原始位移 |
| 预测步长 | 1 | `(目标日−预测起点)/179`，在该查询的 30 个位置重复 |

总计 23 通道。教师参数影响 B+ 位移、水力状态和历史残差，故模型**已有隐式教师
条件信息**；没有显式教师编号、54 维参数向量、拟合 recipe、绝对起点日期通道。
绝对位移和水文轨迹仍可能携带时期信息，不能据缺少日期字段断言“没有阶段信息”。
原 M1 未把完整机械状态作为输入；此前 M2/PINN 则已采用机械状态或方程路径。

当前 h 的网络使用当前 h 的冻结预处理。anchor 及 IN 的教师都在 h 日前缀拟合，
不能把其早期起点样本称为当时可用的全流程 OOF；OOF 的 paired 使用起点教师，
但预处理仍来自当前 h。近期观测输入截止 o−1，预测段不注入新实测。

依据：[物理特征](../code/physics_guided/features.py)、
[历史窗口及网络调用](../code/physics_guided_history_learning/core.py)、
[样本来源](../code/physics_guided_origin_learning/core.py)。准确符号行号和文件哈希
见[源码定位表](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/source_registry.json)。

## 2. 教师与起点支持范围

对原 `pairs_342/432/612.csv` 按独立日期规则逐行枚举，检查两来源的教师、block、
历史截止、lead 和原权重。每个前缀分别有 226、458、831 行，总计 1,515 行；
IN/OOF 共享这些日期行而使用不同教师，不计作两批独立观测。

| h | anchor 行数 | anchor 起点数 | paired 行数 | paired 不重复目标日 | paired 起点数 | OOF paired 教师数 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 342 | 136 | 23 | 90 | 90 | 1 | 1 |
| 432 | 188 | 29 | 270 | 180 | 2 | 2 |
| 612 | 291 | 42 | 540 | 360 | 3 | 3 |

两个 block 各占原权重 1/2。paired 的重复目标日按原规则分摊权重；四点、种子和
重叠日期不能增加独立教师数。h=342 的 paired lead 为 0–89，其他两前缀为 0–179。

OOF paired 中的教师 252、342、432 各只对应自身一个预测起点，教师与起点绑定。
当前 h 教师仅进入 OOF anchor，该块覆盖多个起点；因此不是整个训练集中的每个
教师都只有一个起点。重叠目标日虽能出现两个旧教师，但预测步长和观测历史也
同时改变，尚无“固定起点/历史/lead，仅改变教师”的均衡训练支持。
IN paired 则在 1/2/3 个起点共用一个当前 h 教师；每个 h 的模型单独训练，教师
向量在该模型内恒定。不能把三个独立拟合模型当成一个跨教师学习的大样本模型。

四个教师身份为 252B、342A、432B、612B。每组最终拟合记录、`teachers.json`
以及所选 NPZ 的 theta 严格一致，均为 54 维。原交付参数名含 33 个 `log_` 坐标、
21 个线性坐标；`creep`、`wet_creep` 等不能统一作对数坐标处理。这里只验证身份
和存储含义，未估计参数对误差的回归关系，也未用原 1168 日最终参数替换开发教师。

这些记录支持“增加教师条件的样本支持有限，存在教师/时期混杂风险”的判断。
它们不证明参数输入无效、不证明现有信息不足，也不证明某一遗漏通道是根因。

证据：[按块覆盖](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/coverage_by_block.csv)、
[按教师覆盖](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/coverage_by_teacher.csv)、
[paired 支持](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/paired_support.csv)、
[参数身份](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/teacher_identity.json)、
[参数名](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/parameter_names.csv)。

## 3. 时间结构及与旧方法的差异

令 o 为预测起点，t 为目标日，lead=t−o；lead=0 是第一预测日。原查询将物理
窗口 `[t−29,t]` 与观测残差窗口 `[o−30,o−1]` 沿通道拼接，同一序列位置 k 的日期为
`t−29+k` 与 `o−30+k`，差值为 `lead+1` 日。残差日差还需要左端 o−31 的观测。
两窗并非逐日同步序列；这符合原登记设计，不能直接叫作错位 bug 或未来观测泄漏。

`M1.forward` 在每次查询开始将 hidden/cell 置零，随后处理该查询的 30 个位置。
窗口内部有记忆，但不同目标日之间没有传递该网络的 hidden/cell。整段预测会
复用起点历史，仍不是“从起点状态沿未来日历连续推进”的预测网络。

对两来源的原训练查询以及 v1.22 的 90/180/180 日诊断查询，共检查 117,900 个
窗口位置，日期偏移均符合上述等式；[180 个 lead 的时间表](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/window_offsets.csv)
只是索引运算，没有调用网络或生成新预测。

| 已有路径 | 实际连续性与条件 | 本次候选的区别 |
| --- | --- | --- |
| 原/历史 M1 | 每个目标日重新编码窗口；历史版本含残差与 lead | 历史编码状态传入按日历推进的未来网络 |
| 原 M2 | 前一机械状态和背景位移从首日递推，梯度经过原求解器 | 旧路径已有递推；没有近期观测残差的历史编码器 |
| 状态 PINN | 水文/模板状态、累计背景修正及方程残差；显式时间因子约束初值 | 已用物理状态与时间，不能把候选说成首次引入状态 |
| 同一 G 共享力学学习 | 同一网络控制背景倍率，最终均值由连续力学递推输出 | 保留其失败；新候选改变观测历史与预测网络的衔接 |

源码依据：[原 M1/M2](../code/physics_guided/models.py)、
[状态 PINN](../code/physics_guided_state_pinn/core.py)、
[共享力学](../code/physics_guided_shared_mechanics/core.py)。旧 M2、v1.9、v1.12、
v1.17、v1.19、v1.21 的负结果继续保留；连续性本身不保证泛化。

## 4. 原始材料与方法原文

本次实际复读 `manuscript.pdf` 第 4、11、19、20 页。当前 PDF SHA-256 与已保存
PASS 的结构预检完全一致，PyMuPDF 读到 26 页；复用的是旧结构预检，而非新运行
一次 pypdf 预检。保存解析器版本、页文本摘要哈希和 ZIP 成员校验，见
[原件核对记录](../results/ootang_bplus_v1_23/20260911_teacher_condition_review/mentor_sources.json)。

原稿第 4 页说明预测条件是已知雨量/库水位，部分参数来自拟合反演而非直接实测；
第 11 页说明末态软约束通过全段重新拟合参数实现，物理状态从首日积分而来；
第 19–20 页保留界面/材料参数未唯一识别及重复历史回测的局限。原稿没有要求
将反演参数当成无误差的外生真值，也没有提供学习这些参数与残差关系的大量案例。

ZIP 完整交付根为 `section2d_v4/work/delivery_final/outang_repro/section2d_v4/`。
核对 `physical_model.py`、`predict.py`、最终参数/协议及锁文件；锁中 9 个成员
的哈希均一致。`predict.py` 只读取日期、雨量、库水位，连接历史和未来驱动后
整段前向，再截取预测输出。这支持保留 B+ 连续物理基线，不能作为新神经结构
有效性的证据；原稿关于预测精度的数值未在本版重新计算。

仅使用下列公开方法原文，未上传项目原稿或数据。文献作用是界定结构候选，
没有把其他任务上的性能结论移植成藕塘结果。

| 来源与已读位置 | 支持的设计信息 | 对本项目的限制 |
| --- | --- | --- |
| Shi et al. (2015)，ConvLSTM，§3.2、图 3、式 4，PDF 第 4–5 页 | 编码器末端 hidden/cell 传给预测网络，再展开未来序列 | 原实验为雷达/移动图像，不证明四点位移有效 |
| Salinas, Flunkert & Gasthaus，DeepAR，arXiv v3（2019-02-22），§3、图 2、§3.2 | 历史状态传入预测；未来用模型抽样反馈，协变量须已知 | 训练用真值、预测用样本有差异；大量相关序列的结果不能保证本案例效果 |
| Lim, Arik, Loeff & Pfister，TFT，arXiv v3（2020-09-27），§4.3、§4.5.1 | 区分过去观测、已知未来和静态信息，并通过编码/解码处理可用性差异 | 静态条件通道不能补足缺少的条件组合；没有据此选用完整 TFT |

完整出处：

1. Xingjian Shi, Zhourong Chen, Hao Wang, Dit-Yan Yeung, Wai-kin Wong & Wang-chun Woo.
   *Convolutional LSTM Network: A Machine Learning Approach for Precipitation Nowcasting*.
   NeurIPS (2015)。[出版方原文](https://papers.nips.cc/paper_files/paper/2015/file/07563a3fe3bbe7e3ba84431ad9d055af-Paper.pdf)。
2. David Salinas, Valentin Flunkert & Jan Gasthaus.
   *DeepAR: Probabilistic Forecasting with Autoregressive Recurrent Networks*.
   arXiv:1704.04110v3 (2019)。[本次所读版本](https://arxiv.org/html/1704.04110v3)。
   此处按预印本列三位作者，不与另有作者的 2020 年期刊版混用。
3. Bryan Lim, Sercan O. Arik, Nicolas Loeff & Tomas Pfister.
   *Temporal Fusion Transformers for Interpretable Multi-horizon Time Series Forecasting*.
   arXiv:1912.09363v3 (2020)。[本次所读版本](https://arxiv.org/html/1912.09363v3)。

## 5. 推荐、核验与下一步

推荐下一版先验证一个小型 B+ 残差预测接口：历史端按相同日期编码 B+ 特征和
近期观测残差，末端状态传到未来端；未来端只接收该日可用的 B+ / 已知水文特征，
逐日传递 hidden/cell 并输出四点修正，均值为原 B+ 加修正。未来端不注入预测段
真值。该候选首先验证网络状态递推，尚不引入 DeepAR 式随机轨迹反馈，不能称作
完整 DeepAR、TFT 或新的 PINN 复现。

这里同时涉及日期组织、有效输入历史范围和状态衔接，是明确的时序结构候选。
即使以后优于旧 M1，也不能直接将改善全部归于其中某一个因素；若要隔离状态
传递的作用，应在后续学习方案中明确匹配对照。旧 M2 已有力学递推的事实不变。

下一版实施前须另记接口约定和有限核验预算，至少明确：四点/通道/日期含义，
历史与未来入口分离，旧教师及预处理的使用方式，零读出严格恢复 B+，完整/截断
预测前缀一致，未来观测隔离，以及**非零读出时**历史依赖和梯度可达。
零初始化输出头时，历史梯度为零是预期现象，不能与非零读出检查混淆。
参数量与展开长度应同时计入预算，不能只比较训练轮数。

接口通过后，有限学习对照另版先固定样本、目标、优化器、预算、评价和退出条件。
优先保留当前物理基线及输入来源，集中检验时序结构；不同时增加教师参数学习、
更换概率头或按已暴露窗口重选损失权重。均值出现稳定改善后再单独研究时变区间，
同时保留原概率指标及失败。原切分、四点逐项标准与开发性历史回测边界保持，
原始日值 as-of 仍 unknown；本审查不产生独立盲测证据或下一版训练预算。

本版元数据重建和封存只读回放均 exitcode=0；原样本权重最大差 0，四组参数
严格相等，2,774 个受保护文件通过校验。检查只证明样本/来源/日期描述一致，
不证明数据独立性、根因、物理因果、模型正确性或预测有效性。Ruff 检查通过，
未为文档和元数据审查重训模型或运行无关全库测试。

11 个审查产物已备份，索引计入 38,273 字节，索引 SHA-256 为
`66c625ba94335ca6b549a2abab9eb86c4d1c40a2844d70e1f83fc615c0d32237`。
分步 commit：`28f517b` 方案、`803f845` 审查实现、`2bf8891` 来源与覆盖记录；
本结论与阶段入口另行提交。没有新模型效果成绩，整体目标继续保持未完成。

只读重建与比对命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python code/audit_ootang_teacher_conditions_v1_23.py \
  --verify results/ootang_bplus_v1_23/20260911_teacher_condition_review
```
