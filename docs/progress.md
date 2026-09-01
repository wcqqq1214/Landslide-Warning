# 藕塘科研进度

> 更新日期：2026-08-31
>
> 本页只记录当前可执行状态、关键结果、限制与下一步，不是导师报告。完整旧记录可从 Git
> 基线 `6f499cd` 恢复；机器产物、配置和源码始终优先于本页叙述。

## 当前结论

藕塘的 ConvLSTM–NGBoost–SHAP 自动流程已经完整跑通：8 个测点概率位移预测、预测区间
评价、四项逐点预警指标、H=7 自动未来状态标签、五分类 site NGBoost、NGBoost SHAP、
测点级与滑坡体级逐时五色输出均已有版本化产物。ConvLSTM 主体结构未被替换。

流程完整不等于模型有效。当前 ConvLSTM 的跨时期点预测和区间质量不稳定；固定 NGBoost、
memory 与 residual 三种分类方案均未超过严格因果的 lag-7 状态持续基线。因此当前状态是
“可复算的探索性科研原型”，所有预警结果均为 `formal_warning_output=false`。

## 固定科研主线

```text
多源监测数据与逐点运动学特征
  → ConvLSTM 全 8 点 P10/P50/P90 位移预测
  → PICP、区间宽度和覆盖偏差评价
  → 区间、速度、严格加速度、改进切线角四项指标
  → H=7 多测点未来变形代理标签
  → 五分类 site NGBoost
  → NGBoost SHAP
  → 测点级与滑坡体级逐时预警
```

SHAP 解释五分类 site NGBoost 的期望有序等级，作用类似毕业论文中的 LightGBM+SHAP；
它不是 ConvLSTM-SHAP，也不能单独证明物理因果主控因素。

## 三种时间口径

| 口径 | 数量 | 含义 |
| --- | ---: | --- |
| 原始物化序列 | 1,461 日 | 藕塘完整历史输入 |
| NGBoost 模型可用时间 | 861 日 | 2018-02-21—2020-06-30；逐时概率与 SHAP 的模型时间域 |
| v4 透明规则基线 | 514 日 | 2019-02-03—2020-06-30；规则基线自身的有效窗口 |

“所有时刻”必须注明具体分母。NGBoost 另输出 `861×8=6,888` 条测点诊断；v4 输出
`514×8=4,112` 条测点记录。暖启动、缺失输入和未成熟未来真值不能静默删行。

## 已完成的五阶段运行

统一入口的运行记录为 `figures/pipeline/ootang_advisor_demo_run.json`，五阶段合同全部通过，
总耗时约 35.7 秒；该运行未重训 ConvLSTM，也未读取 Vajont。

| 阶段 | 主要输出 | 状态 |
| --- | --- | --- |
| 1. v4 四指标透明基线 | 514 日测点级与 site 双轴时间线 | completed |
| 2. H=7 初始自动标签 | 未来状态标签诊断 | completed，首方案失败结果保留 |
| 3. ECDF challenger | fold-1-only 五级固定边界 | completed，开发期标签门禁通过 |
| 4. 五分类 NGBoost | 概率、颜色、基线比较与分类 SHAP | completed，模型结果为负 |
| 5. 证据包汇总 | 既有图表与 CSV 索引 | completed，不是导师报告 |

相关提交：`a3c956d` 增加藕塘证据包，`6f499cd` 统一当前科研状态与术语。

## 工程精简记录（2026-08-31）

已退役与当前论文证据链无关的 live/prequential/epoch/replay/trusted-time 工程支路，包括其
配置、非默认流水线阶段、专属源码、测试和回顾性 prequential 产物；同时删除四个无生产
消费者的旧 operational 配置。该变更不修改 ConvLSTM、四指标、H=7 自动标签、五分类
NGBoost、分类 SHAP、现有模型或当前实验结果，也不读取 Vajont。旧功能可从 Git 基线
`c9c6917` 恢复。

第一轮精简后统一入口登记 19 个科研阶段，`config/` 保留 17 个版本化文件。第二轮在用户
明确授权且保留 `paper/` 的前提下，进一步退役旧独立回归 SHAP、interval-proxy → auto-V0
→ V5 gate/display、未启用的 ConvLSTM inner-validation/capacity、operational-v2 配置、旧逐日
日期兼容 API 及无消费者产物；统一入口现为 11 个阶段，`config/` 为 10 个版本化文件。

本轮没有修改当前 ConvLSTM 主模型、rolling/seed 诊断、H=7 标签、五分类 NGBoost、分类
SHAP、v4、memory/residual 负结果或 advisor 包，也未读取 Vajont。为保持用户指定保留的
`paper/` 可构建，只留下其直接嵌入的 4 张历史 PNG，并在产物索引中标明它们不是当前证据。
退役执行链可从 Git 基线 `b13eb8b` 恢复；本地另清理约 203 MB 可再生缓存和已删除功能的
孤立虚拟环境。

本轮跟踪差异删除 111 个文件、约 106.5 MiB；大部分行数来自旧预测 CSV，不代表源码规模。
同时修正 v4 PDF 的忽略规则，使 advisor 包实际依赖的 all-station diagnostic 与 full timeline
两份 PDF 能纳入版本控制；其 SHA-256 与既有 advisor manifest 一致。验证包括 256 项全量
`unittest`、Ruff、Python 编译检查、11 个阶段的 80 个现存输出合同，以及保留 `paper/` 的
17 页 XeLaTeX 构建，均通过；未重训模型或重跑科研实验。

清理后复审另修正两项 P3 维护残余：切线角 frame 测试现在只拒绝重复/乱序时间戳，不再把
合法不规则间隔误写成应拒绝；协议辅助模块删除无消费者的 inner/capacity 目录常量。冻结
配置中的两个 `rerun_now=false` 历史决策字段原样保留，50 项相关定点测试与 Ruff 通过。

## 方法实现状态

- 速度按真实相邻时间差计算：`v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`，不再用 30 日位移差。
- 加速度按 `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})` 计算；首个速度和前两个加速度为暖启动。
- ConvLSTM 对 MJ9、MJ1、MJ3、ATU1–ATU5 全部 8 点输出训练、校准、预测段及
  P10/P50/P90；P10–P90 是 80% 预测区间，不称参数置信区间。
- 四项指标全部进入 32 维 site NGBoost 输入，不分主指标和副指标。
- H=7 标签只由未来位移率和未来正速度 Q90 构造；边界只在 fold 1 拟合并固定外推。
- NGBoost 输出 green、blue、yellow、orange、red 五级概率；v4 只作透明诊断基线。
- site 主模型同时使用 8 点；空间拓扑按 O1/O2/O3 分区，不由单点极值代表整个滑坡体。

## 关键结果

### ConvLSTM 三折五种子

| fold | RMSE mean±SD | persistence RMSE | RMSE skill | PICP | width |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.970±0.418 | 0.245 | -7.036 | 0.387 | 2.959 |
| 2 | 0.356±0.085 | 0.120 | -1.970 | 0.956 | 1.320 |
| 3 | 0.328±0.008 | 0.340 | 0.036 | 0.754 | 0.476 |

目标覆盖率为 0.80。fold 1 欠覆盖、fold 2 过覆盖、fold 3 轻度欠覆盖；第三折虽有小幅
正 skill，但日增量明显强平滑，不能据此宣称稳定捕捉变形过程。

### 自动标签与 NGBoost

- fold 1 的 site 五级各 56 日；fold 2 为 `138/66/39/21/16`。
- 固定 NGBoost 在 fold 2 的 accuracy、fixed-five macro-F1、ordinal MAE、log-loss、
  Brier 为 `0.3536/0.2871/0.7429/3.3358/0.9960`。
- 共同 273 日上，lag-7 persistence 为 `0.8022/0.6722/0.2234`；固定 NGBoost、memory
  和 residual 均明显落后，后两者已拒绝，停止在已暴露评价折上继续搜索变体。

### NGBoost SHAP 与多点基线

- 当前 SHAP 前四项依赖为 ATU2 切线角 `0.5440`、ATU1 切线角 `0.4815`、ATU5 速度
  `0.2094`、ATU2 速度 `0.1799`；这是失败模型的依赖描述，不是因果主控结论。
- v4 的 514 日中，114 日形成 site 整体确认，400 日仅保留局部候选。
- v4 加速度等级计数为 `4012/98/2/0/0`；A0 阈值是 fit-only 的项目操作化，尚无现场验证。

## 证据入口

- 方法与结果底稿：`docs/ootang_manuscript_methods_results_draft.md`
- 阶段证据边界：`docs/ootang_stage_results_package.md`
- 自动标签与分类协议：`docs/ootang_ngboost_auto_state_experiment_plan.md`
- SHAP 对象与样本：`docs/ngboost_shap_protocol.md`
- 全 8 点预测：`figures/convlstm/forecast_all_stations.png`
- NGBoost 概率、逐时预警与 SHAP：`figures/ngboost_auto_state_classifier_v1/`
- v4 多点透明基线：`figures/warning_operational_draft_v4/`
- 五阶段机器运行记录：`figures/pipeline/ootang_advisor_demo_run.json`

## 导师阶段汇报重写（2026-08-31）

- 输入：导师当前修改意见、`AGENTS.md` 固定主线、现有藕塘方法与结果底稿，以及已经生成的
  ConvLSTM、NGBoost 和 SHAP 产物。本次没有重训模型，也没有读取或运行 Vajont。
- 方法与时间范围：报告统一为 ConvLSTM–NGBoost–SHAP 路线，明确区分 1,461 日原始序列、
  861 日模型输出、其中 840 日成熟 H=7 标签，以及 514 日多测点规则对照；分类结果仍按
  既定时间折评价，没有根据报告呈现效果重新调参或改标签。
- 输出：重写 `paper/process_report.tex`，同步更新流程图、四指标热图和 `paper/README.md`，
  生成 6 页 `paper/build/process_report.pdf`。报告以图为主，包括 8 个测点全时段概率预测、
  区间评价、五分类比较、逐时结果、NGBoost SHAP 和全部测点四指标结果。
- 表达：正文和流程图只直接陈述当前方法、结果与限制，删除第三人称的汇报说明、请示性措辞
  和后续工作安排。
- 结论：藕塘流程已经接通，但结果不能写成模型已经有效。ConvLSTM 跨时期表现不稳定，
  NGBoost 在当前评价段明显落后于 lag-7 状态持续基线；报告如实保留这两个负结果。
- 局限与下一步：H=7 仍是代理标签，不是真实灾害等级；目前缺少原始 GNSS 复核材料和现场
  事件真值。先请导师确认自动标签定义及四指标、多测点综合方式，再决定是否整理最终藕塘
  展示包并进入 Vajont 外部验证。
- 核对：报告图件脚本可复现；XeLaTeX 构建通过；6 页逐页检查未发现文字裁切、表格越界、
  图片重叠或缺字。
- 版式调整（2026-09-01）：删除首页题目、姓名和日期，取消全部页面的页眉；正文图表与底部
  页码保留，未改动方法、实验结果或科研结论。
- 图件调整（2026-09-01）：流程总览改为 Draw.io 可编辑源文件，并由 Draw.io 导出报告 PNG；
  只调整视觉层级和连线路径，不修改技术路线、实验数字或结论边界。
- 表达收口（2026-09-01）：进一步区分 NGBoost 的 861 日模型输出与透明规则的 514 日诊断
  窗口，补充 32 维四指标输入、273 日共同样本、时间轴前两行含义和 SHAP 解释量定义；
  图内统一使用“滑坡体级 NGBoost”和中文标签。此次只调整报告叙述与图件标签，没有重训、
  改标签、改阈值或改变实验结论；同时删除未引用的 Draw.io SVG 导出物和报告脚本中的无用
  颜色常量，保留可编辑 `.drawio` 源文件与报告使用的 PNG。

## 外部方法文献审计（2026-09-01）

- 输入：当前 `docs/`、`paper/`、`literature/` 与相关 Git 历史；未读取或运行 Vajont。
- 文献范围按用户确认收窄：论文参考文献只保留实际支撑当前路线、方法、公式或评价原则的
  来源，不因某个方案曾经试验过就把其论文写入当前主线。
- Killick et al. (2012)、Truong et al. (2020) 和 Cheng et al. (2020) 只对应已经失败并由
  ECDF v2 取代的 v1“变点 + KMeans”标签路线；Gibbs and Candès (NeurIPS 2021) 只是尚未
  实施的候选方向。它们只留在内部实验史中并标明退役/未实施，不进入当前论文参考文献和
  本轮 PDF 下载范围。
- 区间审查另引用 Gneiting et al. (2007) 与 Gneiting and Raftery (2007) 作为校准、锐度和
  interval score 的评价依据。当前代码确实实施了逐测点 split-conformal 对称扩张，Romano
  et al. (NeurIPS 2019) 可作为相近方法来源，但必须写成项目适配而非完整复现。
- 当前方法来源已补齐并核验，登记见 `docs/current_method_reference_register.md`。本轮仅下载
  7 篇正文 PDF，未下载 SI：ConvLSTM、NGBoost、SHAP、CQR、校准与锐度、proper scoring
  rules，以及一篇分位数回归权威综述；所有 PDF 均通过文件类型、文本、页数与首页抽查，
  并记录 SHA-256。Koenker and Bassett (1978) 与 Shepard (1968) 未找到可核验的合法开放
  正文，保留 DOI、出版社记录和 `metadata_only` 状态，不使用来源不明的镜像。
- Deng et al. (2021) 只有在正文实际用它论证“自动运动状态分类”方向时才保留，不能把它
  写成当前 ECDF、`H=7` 或空间综合规则的直接出处。
- 数据血缘审查引用 Yang et al. (2023, 2024) 核对藕塘监测频率、分区和点位语义；它们不是
  当前模型所借鉴的算法，不纳入方法文献批次。Wang et al. (2025) 本地 PDF 已存在。
- 归档采用双层策略：Git 中记录题名、DOI/arXiv、对当前方法的作用、正文对应位置、合法来源
  URL、本地文件名和校验值；对最终实际引用的方法/评价来源另保留可核查全文。PDF 继续放在
  已忽略的 `literature/`，不提交大文件或可能受版权限制的全文。若没有合法 PDF，则记录可访问
  的出版社/作者全文链接和核验状态，不以未读的元数据代替原文核查。

## 当前限制与下一步

1. 不将自动 H=7 标签写成真实灾害等级，不将 SHAP 写成物理因果证明。
2. 不依据已经暴露的 fold 2/3 继续调参、改标签或筛选模型以掩盖负结果。
3. 导师阶段汇报已经按当前主线重写，详细范围、结果和核对记录见上一节。
4. 模型效能改进应等待新的可追溯独立时段、跨滑坡数据或现场事件真值，再预注册评价。
5. Vajont 继续暂停；只有藕塘内部收口且用户再次明确要求后才可启动，且不得反调藕塘方案。
