# 藕塘路线审查：网页版材料与提示词

日期：2026-09-11。用途：交给网页版模型进行独立的路线评估与启发。
这份材料索引不改变实验方案、预算、切分、阈值或历史结论，不启动训练。
来源快照固定为 Git `d48c454c18a3daac49177d3f19c262a0d47d24d8`：当时 v2.0 已失败并停止，v2.1 仅完成方案。
后续仓库变化不自动进入此次审查；旧文档中的“当前”“下一步”只描述其形成时的状态。

## 固定版本材料

以下为公开 GitHub 的原文件链接。先读必读材料，再按问题读取补充依据。
打开失败时报告具体文件；不要声称已读取无法访问的内容，也不要据此推断文件不存在。

### 必读

1. [导师原稿 manuscript.pdf](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/manuscript.pdf)：导师给出的 B+ 物理模型资料。
2. [路线复盘](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_route_review_2026-09-11.md)：截至 v1.25 的历史尝试和负结果；其暂停状态不覆盖后来的 v2.0/v2.1 记录。
3. [v2.0 ConvLSTM 结果](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_convlstm_direct_results.v2.0.md)：该快照最新完成的实验。
4. [v2.1 概率 PINN 方案](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_probability_pinn_plan.v2.1.md)：待质疑的候选，尚未实现或训练。

### 按需核查

| 材料 | 用途 |
| --- | --- |
| [导师原始 ZIP](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/section2d_v4.zip) | 原始代码交付，约 47.8 MB；模型与求解器位于 `section2d_v4/work/delivery_final/outang_repro/section2d_v4/physical_model.py` 和 `physical_solver.c` |
| [v1.8 方程说明](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_bplus_pinn_equation_contract.v1.8.md) | 项目对原方程的整理，有原 ZIP 成员哈希；不替代原始代码核查 |
| [v1.9 状态 PINN 结果](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_bplus_state_pinn_results.v1.9.md) | 原状态 P 与主输出 R 的区别及失败证据 |
| [v1.12 共享力学学习结果](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_bplus_rate_learning_results.v1.12.md) | 统一路径后的结果，不能把接口问题当作唯一失败原因 |
| [历史汇总指标](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/docs/ootang_route_review_2026-09-11_metrics.csv) | 历史跨版本数值，未包含 v2.0 |
| [v2.0 逐点指标](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/results/ootang_convlstm_v2_0/20260911_direct/metrics.csv) | 拟合/预测误差、CRPS、覆盖率、宽度和区间评分 |
| [v2.1 冻结配置](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/config/ootang_probability_pinn.v2_1.json) | 候选参数、预算与 21 项来源哈希 |
| [物理残差实现](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/code/physics_guided_pinn/equations.py) / [数值审查实现](https://raw.githubusercontent.com/wcqqq1214/Landslide-Warning/d48c454c18a3daac49177d3f19c262a0d47d24d8/code/physics_guided_pinn/substep_audit.py) | 核查软约束损失与原数值容差的定义 |

原 PDF 的另一个中文文件名版本内容相同，不作为第二份独立证据。
Markdown 内的相对链接按此固定提交解析；可用下列仓库树查找原文件：
[固定版本仓库](https://github.com/wcqqq1214/Landslide-Warning/tree/d48c454c18a3daac49177d3f19c262a0d47d24d8)。

## 审查任务

请从物理引导机器学习、时间序列概率预测和实验设计三个角度，独立审查我的研究路线，帮助我决定下一次最值得做的实验。

这是路线评估与启发任务，暂时不写代码、不运行实验。请直接挑战现有方案，包括另一个 AI 提出的 v2.1；不要因为方案详细或已经冻结，就默认它合理。

### 一、导师目标与硬约束

研究藕塘滑坡同一剖面的四个测点：MJ3、MJ1、ATU5、ATU1。

以导师提供的改进 B+ 物理模型为引导，使用 ConvLSTM 或 PINN，做到：

1. 训练段拟合及预测段均值接近实测，误差低于 B+。
2. 同时改善概率预测，兼顾覆盖率、区间宽度和概率评分。
3. 当前只做这个案例的四点位移预测，不扩展到预警或新案例。
4. 主模型限定在 ConvLSTM/PINN，不能直接改成另一种非神经模型。

请区分导师的实际要求与项目后来自行加入的具体门槛、预算和实现选择；后者可以批评，但不能追溯修改既有实验标准或把负结果改判为成功。

### 二、已有结果与问题

后续实验主要使用两个预测起点：第 432、612 日，各预测完整 180 日。下列数值均为先计算逐点指标，再对四点取算术平均，单位 mm，来源见 v2.0 结果和逐点指标：

| 指标 | B+ / P0 | v2.0 ConvLSTM |
| --- | --- | --- |
| 两窗预测 RMSE | 58.0122 / 22.2448 | 64.4117 / 22.1677 |
| 两窗预测 CRPS | 42.0113 / 25.1140 | 47.2449 / 26.9716 |

P0 是 B+ 均值加既定滚动尺度的概率参照，不是导师原 B+ 天生具有概率输出。

v2.0 有 5/8 个“点位×窗口”同时改善拟合/预测 RMSE、MAE，但整体目标仍未完成。此前也尝试过状态 PINN，存在训练状态与最终求解器输出分离的问题；后续统一路径仍未稳定超过 B+。

大量工作花在接口、梯度、数值核验上，却没有形成稳定预测收益。我希望改变推进策略。

注意：

- 旧窗口已经反复查看，不是新的独立盲测。
- 未来降雨、库水位作为给定条件，不代表实时已知。
- 不能把四点、三个种子或相邻日期当成独立现场重复。
- 候选失败不能直接推断整个 ConvLSTM/PINN 家族无效。

### 三、请重点回答

1. **现有负结果分别支持什么判断？**
   请区分：数据和可预测信息不足、修正位置不合适、时间协议或泛化问题、优化未充分完成、概率建模不合适，以及评价设计问题。
   每个判断标明“已有证据”“待检验假设”或“无法判断”，不要根据汇总误差臆测唯一原因。

2. **神经网络相较 B+ 的改善空间究竟来自哪里？**
   请具体说明它能利用什么额外信息或学习什么模型偏差。若物理参数、驱动、初值和方程均固定，网络为什么可能得到更好的预测？哪些修改仍属于可信的物理引导？

3. **独立审查 v2.1 概率 PINN。**
   判断有限幅度的广义力修正是否有依据、四点观测能否识别它，以及状态网络和力偏差是否存在相互补偿。
   说明末层贝叶斯近似及联合学习的残差尺度能够表达哪些不确定性、遗漏哪些；审查预测域无标签物理配点与此前协议的差异如何影响公平比较。
   重点核查：软约束 PINN 固定训练 200 步后，要求全部抽样满足原求解器约 1e−8 mm 级容差，是否是合理的科学筛选条件？请区分求解器数值正确性、神经近似精度和物理可信度，给出依据，不预设答案。
   首模型未通过这个门槛时，实际能排除什么，不能排除什么？

4. **在 ConvLSTM/PINN 范围内，最多提出三条有实质区别的候选路线。**
   每条说明核心可检验假设、与已失败方案的真正区别、为何可能改善跨期均值与概率、所需信息能否由现有数据提供、最可能的失败原因，以及用于去留决策的最小实验。
   不要堆模型名称、模块或论文热点。允许结论是“当前证据不足以支持任何新训练”。

5. **最终只推荐一个下一步行动。**
   给出最小实验或必要的信息核查、对照、主要指标、成功条件、停止条件和投入估算。
   现有 v2.1 留给实现、运行、核验与收尾的预算最多 90 分钟，请判断这个预算能回答什么问题。
   如果认为预算或门槛不合理，请明确提出修改理由，作为待讨论的新建议；不要自动追加预算，也不要用不足的预算宣判整条路线无效。

### 四、证据与输出要求

请检索并核对与你的核心建议直接相关的原始论文，优先少量高度相关来源。给出论文链接、对应方法或实验位置，以及其数据量、预测任务和物理系统与本项目的差异。不要把其他任务上的成功当作本项目有效的证据。

先列出实际读到的材料及缺失项。若未读取原始数据或代码，请明确说明，不能声称完成代码审计或复现。缺失信息先列最关键的三项，同时完成不依赖这些信息的分析。

输出顺序：

1. 不超过 300 字的直接结论；
2. 关键瓶颈及其证据；
3. 对 v2.1 的保留、质疑和否决理由；
4. 最多三条候选路线的比较；
5. 唯一推荐的下一步及退出条件；
6. 原始文献依据与尚未解决的不确定性。

请把“值得检验的假设”与“已经证明的结论”始终分开。
