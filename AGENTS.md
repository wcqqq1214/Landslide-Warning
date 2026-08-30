# Interaction Principles

- You may challenge my views — I'm not always right. Maintain a critical mindset: question my instructions and opinions, point out flaws when you see them, and suggest better alternatives.

# Research Tooling

- `academic-research-suite` is available for literature review, manuscript structure, citation checks, revision, and peer-review simulation.
- Use it as an advisory research-writing tool only; do not let it modify frozen validation splits, metrics, thresholds, model structure, or experimental conclusions.
- All literature claims must be checked against source papers, versioned data, and reproducible project artifacts before being treated as final.

# 导师确定的科研主线（最高优先级）

本节记录导师的最新修改意见及论文主线。除非用户明确改变研究设计，否则它高于历史交接记录、旧实验分支和临时工程方案。后续工作必须围绕科研问题推进，不得用部署、数据托管、人工值守或过度测试替代核心实验。

## 研究目标与固定技术路线

- 研究目标是构建可自动运行的多监测点滑坡智能概率预测与预警系统，而不是人工逐时判级系统。
- 保留导师指定的 ConvLSTM 作为位移预测主模型。未经用户明确同意，不得替换、绕过或弱化这一主体框架。
- 论文主线固定为：多源监测数据与逐点运动学特征 -> ConvLSTM 多测点概率位移预测 -> 预测区间及覆盖率评价 -> 四项预警指标 -> 自动生成的未来状态标签 -> NGBoost 五分类概率预警 -> SHAP 解释 -> 测点级与滑坡体级逐时预警；藕塘完整流程收口后，再开展 Vajont 外部案例验证。
- 按用户对导师意见的当前解释，方法分工固定为：ConvLSTM 输出全部测点的 `P10/P50/P90` 位移预测，并以 PICP 等指标评价预测区间；SHAP 解释五分类 site NGBoost 对四项指标和各测点的依赖，方法角色类似毕业论文中的 LightGBM+SHAP。直接 ConvLSTM-SHAP 不列为当前导师要求或藕塘收口待办。方法总称统一使用“ConvLSTM–NGBoost–SHAP”，不得再用含混的“ConvLSTM+SHAP”。
- 规则融合只能作为透明基线或诊断工具，不能替代导师要求的 NGBoost 预警模型。若 NGBoost 未优于简单因果基线，应如实保留负结果，不得在评价数据上反复调参或更改标签掩盖问题。
- 当前无实时传感器接口，允许先用版本化历史数据试跑；数据接入层应可替换，科研方法不得依赖 Figshare API、特定托管平台或人工操作。
- 当前里程碑是先完成藕塘端到端自动流程及导师展示包。允许模型结果为负；“流程完整、证据可复核、局限写清楚”优先于继续追求指标提升。完成前不得启动 Vajont 数据适配、训练或结果生成。

## 位移、速度与加速度定义

- 禁止继续使用 `V_t = U_t - U_{t-30}` 作为速度。每个测点必须按真实相邻观测时间差计算逐点速度：

  `v_i = (U_i - U_{i-1}) / (t_i - t_{i-1})`。

- 严格逐点加速度定义为：

  `a_i = (v_i - v_{i-1}) / (t_i - t_{i-1})`。

- 不规则采样时必须使用真实 `t_i - t_{i-1}`，不得默认所有间隔均为 1 d、10 d 或 30 d。速度单位与加速度单位必须在表格、图件和正文中明确。
- 速度首行和加速度暖启动时刻没有有效值时，应明确标记为 warmup/不可计算，不得伪造或用未来信息补齐。

## ConvLSTM 概率位移预测与展示

- ConvLSTM 必须对全部监测点输出位移点预测及概率区间；藕塘案例不得只展示 3 条代表性曲线，应展示全部 8 个测点。
- 每个测点的图必须同时覆盖训练段和预测/评价段，并用清晰的边界或样式区分；不得只截取预测段，也不得用训练拟合冒充样本外预测。
- 对未来位移不确定性，学术表述优先使用“预测区间（prediction interval）”。区间质量至少报告经验覆盖率（如 PICP）、区间宽度及与目标覆盖率的偏差；不能仅凭区间很宽或覆盖率很高宣称效果好。
- ConvLSTM 概率预测负责产生 `P10/P50/P90` 位移分位数与预测区间，PICP、区间宽度和覆盖偏差负责评价区间；SHAP 负责解释下游五分类 site NGBoost，并识别模型依赖的候选主控因素。各环节作用必须在方法和结果中分别说明。

## 四项预警指标（全部保留，不分主次）

参考 `literature/物理引导的阶跃型水库滑坡变形智能概率预测模型与预警方法研究.docx`，并按导师最新意见，将以下四项全部作为预警指标：

1. ConvLSTM 位移预测区间及实测值相对区间的位置/偏离程度；
2. 逐点速度；
3. 严格逐点加速度；
4. 改进切线角。

四项指标不得再划分“主指标/副指标”，不得删除其中任何一项，也不得将单一指标直接当作最终滑坡体预警结果。指标在时刻 `t` 的计算只能使用当时已经观测到的信息。

导师指定论文原始第四项是变形速率增量 `Delta V`，并未给出严格逐点加速度阈值表。导师最新意见以“严格逐点加速度”替代该项，因此本项目采用量纲一致的操作化方案：在每个测点的 fit-only 稳定段计算 `A = mean(a)`、样本标准差 `sigma_a` 和

`A0 = max(1.5 A, A + 2 sigma_a)`，

再沿用论文速度指标的 `1x/5x/10x` 相对等级结构：green `< A0-sigma_a`、blue `[A0-sigma_a, A0+sigma_a]`、yellow `(A0+sigma_a, 5A0)`、orange `[5A0, 10A0)`、red `>= 10A0`。其中 `A0 +/- sigma_a` 是本项目对论文定性“约等于”的实现约定，不得写成论文直接给出的加速度阈值，也不得写成已通过现场验证的正式标准。阈值调整须另立版本，仅基于开发数据完成并完整登记，不能依据评价段结果回调。

## 自动标签与 NGBoost 概率预警

- NGBoost 必须对上述预警指标进行五分类概率训练，构建 green、blue、yellow、orange、red 五级预警模型；四个警色之外保留 green 作为正常状态。
- 禁止人工逐时指定标签、人工挑选预警日期、人工冻结运行状态或在运行时等待人工判级。人工只负责研究设计和最终科研审查，不参与每个时刻的决策。
- 在缺少真实灾害等级标签的试跑阶段，沿用已登记的自动标签方案：从时刻 `t` 之后的未来窗口生成多测点变形代理结局，默认使用当前版本化的 `H=7` 方案；标签边界仅在开发折拟合并固定外推，不能使用评价折重新估计。
- 当前时刻的四指标是输入 `X_t`，未来变形代理状态是目标 `Y_auto(t)`。禁止把同一时刻由四指标规则融合得到的颜色直接作为 NGBoost 标签，否则会形成 `y = F(X_t)` 的循环学习，不能证明预测能力。
- NGBoost 至少与类别先验、简单线性分类器和严格因果的滞后状态持续基线比较。评价应同时包含分类正确性、固定五类 macro-F1、顺序等级误差和概率质量；不得只报告 accuracy。
- 自动标签是科研代理结局，不是真实灾害真值。论文必须明确其定义、预测时间窗、拟合数据范围、局限性及与正式现场预警标签的差异。

## SHAP 的解释边界

- SHAP 用于说明指定模型对输入因素的依赖，并据此讨论候选主控因素；不得把 SHAP 排名写成物理因果证明。
- 当前 SHAP 的指定解释对象是五分类 site NGBoost；每张图和每段结论都必须明确其输出目标、背景样本和解释样本，并称为“NGBoost SHAP”。
- 不把直接 ConvLSTM-SHAP 列为当前缺口或导师要求。全文使用“ConvLSTM–NGBoost–SHAP”描述完整技术链，避免“ConvLSTM+SHAP”让读者误以为 SHAP 直接解释 ConvLSTM。
- “主控因素”应表述为模型支持的候选因素，并结合物理机制、时序一致性和独立证据讨论。

## 全时刻、多测点预警输出

- 所有具备有效输入的时刻都必须输出对应预警等级和五级概率，连续展示 green/blue/yellow/orange/red 信号；暖启动、缺失输入或尚无成熟未来真值的时刻必须明确标记状态，不得静默删除。
- 必须同时给出测点级结果和滑坡体级综合结果。综合评判应利用全部监测点及其空间分区/一致性，保留各测点贡献，不能只取一根曲线或让单点极值无条件代表整个滑坡体。
- 结果图表至少覆盖：全部测点的训练段与预测段位移及区间、区间覆盖评价、四项预警指标、SHAP 结果、每个时刻的测点级预警、每个时刻的滑坡体综合预警。
- 正文必须把研究方法、预测结果、区间覆盖、SHAP、自动标签、NGBoost 分类及逐时预警结果串成一条可复核的证据链，区分方法设定、实验结果和科研解释。

## Vajont 外部案例

- Vajont 是导师要求的第二案例，但当前明确延后。只有藕塘端到端流程、全部测点图表、四项指标、NGBoost、SHAP、逐时多点预警和导师展示包均完成后，且用户再次要求启动，才使用 `data/vajont_fig5a_curves_2_3_4_5_58_mm_velocity.xlsx` 中的 `10d` 数据进行验证。
- 在此之前，不得读取 Vajont 进入数据适配器，不得运行其模型或生成实验结果；本地源文件保持不修改、不提交。
- `10d` 是指定数据版本/工作表语境，不等于可以把每个相邻间隔硬编码为 10 天；导数仍按实际时间戳计算。
- Vajont 应用于检验方法的可迁移性，不得用其评价结果反向调整藕塘阈值、时间划分或模型选择。数字化历史数据的来源、单位、时间分辨率和不确定性必须如实记录。
- 数据源未来可以替换为传感器接口或其他文件，但字段语义、逐点导数、时间隔离、全测点输出和多点综合判据应保持一致。

## 执行与文档纪律

- 每完成一个实质研究步骤，必须同步更新 `CODEX_HANDOFF.md`、`docs/progress.md` 或对应的版本化方法/结果文档，记录输入、方法、时间划分、输出、结论、局限和下一步，避免依赖聊天上下文。
- 任何新路线先回答它是否直接服务上述论文证据链。与主线无关的部署账本、平台接入、过度防御代码和低价值边界测试应暂停。
- 验证强度与科研风险相称：优先做最小可复现运行、关键数值核对、时间泄漏检查和图表一致性检查（优先自动化，必要时抽查），不追求无收益的穷举测试。
- 不得因代码已运行就宣称方法有效。所有正面结论必须由预先登记且机器执行的评估协议、简单基线、跨时间评价和可追溯产物共同支持；负结果同样保留并写入论文证据链。

# Commit Guidelines

- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

# Git Rules

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
