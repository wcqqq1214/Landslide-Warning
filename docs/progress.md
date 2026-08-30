# 项目工作进度

> 更新日期：2026-08-31。本文件记录工程与研究实现进度；正式 v5 门禁以
> `v5_validation_protocol.md` 为准，机器连续预测支路以
> `ootang_autonomous_research_protocol.md` 为准，结果数值以版本化 CSV 和 manifest
> 为准。历史条目保留其原始日期和门禁数字，不与当前工程门禁混读。

## 2026-08-31 当前权威状态与下一步

- 当前技术链统一称为“ConvLSTM–NGBoost–SHAP”：ConvLSTM 生成全部 8 点的
  `P10/P50/P90` 和预测区间，PICP 等指标评价区间；五分类 site NGBoost 融合 8 点 × 4 项
  预警指标；SHAP 解释该 site NGBoost。直接 ConvLSTM-SHAP 不是当前导师要求。
- 藕塘原始资料包含 `1,461` 个日期；其中 `861 = 3 × 287` 个日期进入当前 OOF 模型可用
  issue 时间线，并全部产生 site NGBoost 五级概率和颜色。每折末 7 日、合计 21 日仍有模型
  预测，但因 H=7 未来窗口尚未成熟而没有回顾性代理真值。“全时刻输出”在当前实验中专指这
  861 个模型可用日期，不等于补造前期窗口不足的 1,461 日全历史预测。
- v4 透明规则基线只覆盖 calibration + test 的 `514 = 227 + 287` 个日期；其测点结果为
  `514 × 8 = 4,112` 行。该 514 日诊断时间线不是 site NGBoost 的 861 日主结果，二者不得混用
  分母、图例或“全时刻”表述。
- 下一步仅做内部文档一致性整理：统一当前/历史入口、方法命名、日期口径、产物链接和证据边界。
  暂不撰写面向导师的报告，不运行模型、不调参、不增加方法，也不读取或启动 Vajont。下文保留
  的旧“下一步”均为当时历史记录；若与本节冲突，以本节为准。

## 2026-08-31 藕塘导师展示流水线收口

- 用户最新优先级是先完整跑通藕塘、允许保留负结果，Vajont 延后到藕塘展示审查完成且用户
  再次明确启动之后。此前对 Vajont 仅有一次只读工作簿检查，临时文件已删除，源文件未修改、
  未提交，也没有生成 Vajont 模型或实验结果。
- 显式五阶段流水线已按
  `ootang-operational-v4 -> ootang-ngboost-auto-state -> ootang-ngboost-auto-state-ecdf ->
  ootang-ngboost-auto-state-classifier -> ootang-advisor-package` 完整运行。运行清单
  [`ootang_advisor_demo_run.json`](../figures/pipeline/ootang_advisor_demo_run.json) 的状态为
  `completed`，总耗时 `35.660 s`，五个阶段的产物合同均为 `passed`，且
  `formal_warning_output=false`。
- 本次只复用既有三折 × 五种子 ConvLSTM 预测，没有重训 ConvLSTM，没有运行已拒绝的
  memory/residual challenger，也没有运行 Vajont。原始 auto-state label gate 仍为 false，
  ECDF gate 为 true；分类器结论仍是 `small_support_descriptive_only`，NGBoost 未超过严格
  lag-7 persistence，作为可复算的阶段性负结果保留。
- 导师展示入口为 [`advisor_summary.md`](../figures/advisor_ootang_v1/advisor_summary.md)，汇总
  5 张核心图和 5 张 CSV 表：ConvLSTM、自动标签、分类器比较、多点综合、加速度阈值分别为
  `27/18/33/23/8` 行；包级血缘与哈希见
  [`manifest.json`](../figures/advisor_ootang_v1/manifest.json)。首轮汇总中的相对路径问题已修复，
  完整重跑已通过。
- 5 张核心图已完成快速目视核对，均可正常打开且内容齐全；v4 全测点诊断图底部说明略拥挤、
  靠边，但不影响本次流程演示，留作后续轻量视觉收口。下一步只做导师展示叙述核对，不继续
  调参、增加新模型或启动 Vajont。

## 2026-08-31 residual challenger 正式结果

- 正式运行使用源码提交 `1e977e4`；完整管线耗时 4.0 秒，其中 stage 为 3.9 秒，产物合同
  通过。共生成 861 条预测，其中 840 条来自 residual 模型，21 条为 v1 fallback；每折恰好
  7 条 fallback，且其概率逐值与已提交 v1 一致。
- 指标表共 13 行，只覆盖 fold 2 的 273 日共同集合；fold 3 仅输出预测，不作评价。fold 2
  residual/v1/persistence 的 accuracy 为 `0.355311/0.336996/0.802198`，fixed-five
  macro-F1 为 `0.330687/0.281322/0.672215`，ordinal MAE 为
  `0.706960/0.761905/0.223443`。residual/v1 的 log-loss 为
  `3.753783/3.421186`，Brier 为 `1.023779/1.021504`。
- 五项预注册门槛全部为 false，机械结论为 `rejected`。结构化增量相对 v1 的硬指标略有
  改善，但仍远落后 persistence，且概率指标更差。
- 按预注册停止 NGBoost 修补：不调参，也不增加第二 residual 变体。该增量未修改 ConvLSTM
  或导师指定的整体框架，不构成现场验证、正式预警或 fold 3 评价主张。
- 下一步只读核对加速度等级与自动未来标签的角色是否正确分离；此时尚不修改自动标签或模型。

## 2026-08-31 方法对齐只读审计

- 指定 Word 第五章没有严格加速度阈值表：表 5-4 给出速度 `V0/5V0/10V0`，第四项是
  `ΔV`。当前严格加速度五级以 fit-only `A0=max(1.5A,A+2σa)` 操作化导师所说的“同阈值
  结构”，不得称为论文原阈值。
- NGBoost 的 `y` 是 H=7 未来位移率与未来正速度 Q90 的自动多点代理状态；严格加速度与
  区间、速度、改进切线角同为 `X_t`。把同刻四指标融合色用作 `y` 会形成 `y=F(X_t)` 循环。
- 导师要求的速度、严格加速度、ConvLSTM 八点全时间、四指标、NGBoost 五分类、逐时五色和
  多点综合已基本实现；但分类器没有胜过 persistence，只能是 rejected exploratory。
- SHAP 解释独立 NGBoost，而非 ConvLSTM 内部。自动未来状态不同于论文同刻 MLR 融合；论文
  缺训练标签和系数，故当前方法是可复现的项目替代口径，不是复现。
- 旧 classifier 的 fold 3 指标仅留作历史产物，不重跑或级联改写；后续 fold 3 统一为
  prediction-only。本审计不设置人工批准或冻结，也不修改标签或模型。

## 2026-08-31 当前结果报告收口

- 已同步 `ootang_stage_results_package.md` 与 `results_report.md`：当前主任务统一为 H=7 ECDF
  自动五级标签、32 维四指标 site NGBoost、八点/全时刻输出和分类 SHAP；旧四级 V0 分类及旧
  回归/二分类 SHAP 明确降为历史对照。
- 报告保留固定分类器、lag-memory 与 residual 均未超过 lag-7 persistence 的负结论，并停止
  NGBoost patching；没有重跑或修改模型、标签、ConvLSTM、v4、产物或评价切分，也没有新增
  fold 3 评价。
- 下一步只基于现有版本化产物整理论文用图表索引与方法/结果叙述，不增加模型变体、调参或额外
  边界测试。
- 已在 `ootang_stage_results_package.md` 第 11 节完成最小图表索引：5 张现有核心图对应 8 点
  ConvLSTM、H=7 全时刻颜色、分类 SHAP、v4 四指标和多点综合；5 组表格来源对应全站预测、
  自动标签、分类公平比较、多点输出及加速度阈值。图件已只读目视核对，未重新计算；除
  ConvLSTM 现有 2100×3000 PNG 外均已有 PDF/SVG 主件。
- 下一步直接据此撰写论文用方法与结果文本，保留分类负结果和证据边界。
- 已新增 `ootang_manuscript_methods_results_draft.md`，形成可直接修改的中文方法、结果、讨论和
  当前结论初稿。内容覆盖 ConvLSTM、四指标、机器 H=7 标签、site NGBoost/基线、分类 SHAP、
  全时刻输出与 v4 多点综合；全部数字经只读复核与版本化产物一致，并补全 fold-1-only site
  q20/q40/q60/q80 离散步骤。没有重跑实验或图件。
- 下一步从既有 CSV/JSON 机械生成 4 张正文表和 1 张加速度补充表，不再选择模型、时间折或
  参数。

## 2026-08-31 residual NGBoost challenger 预注册（拟合前）

- 下一步只运行一个结构性 challenger：在 fold 1 的 273 个 lag 已成熟日，用原八点四指标
  32 维加 `Y_auto(t-7)`，让固定 NGBoost 分类 `delta=Y_auto(t)-Y_auto(t-7)`。fold-1-only
  类别/计数为 `-2/-1/0/+1 = 4/49/177/43`，不根据 fold 2 改类；其余模型参数不变。
- 对越界 delta 概率置零并归一，再精确映射成五级概率；禁止 clamp、epsilon、事后混合。
  每折前 7 日自动复制已提交 v1 概率并标记 fallback，只用于 861 时刻覆盖，不进入主评价。
- 唯一评价为 fold 2 common 273：相对 persistence 必须严格提高 macro-F1、降低 ordinal MAE，
  同时相对 v1 降低 log-loss/Brier 且 log-loss `<1.6094`；任一失败即拒绝并停止 NGBoost
  修补。fold 3 只输出、不算指标；不做第二变体、调参、校准、SHAP、测点模型或新图。
- fold 2 的 delta support 与 fold 1 相同，但 no-change 比例从 `64.8%` 升至 `80.2%`，是已知
  开发折漂移风险。流程只采用机器记录的拟合前协议，不含人工标签、日期、冻结或批准步骤。

## 2026-08-31 lag-7 状态记忆 challenger 正式结果

- 运行代码提交为 `b0dc37a`；完整管线耗时 4.0 秒，产物合同通过。共输出 861 条逐日预测和
  27 行仅限 fold 2 的指标；fold 3 只输出预测，不计算或报告指标。
- fold 2 的 273 日共同掩码上，memory/v1/persistence 的 accuracy 分别为
  `0.336996/0.336996/0.802198`，fixed-five macro-F1 为
  `0.281322/0.281322/0.672215`，ordinal MAE 为
  `0.761905/0.761905/0.223443`。memory/v1 的 log-loss 为
  `3.434553/3.421186`，Brier 为 `1.028243/1.021504`；hard persistence 不报告概率指标。
- 预注册的 7 项改善门槛全部为 false，机械结论为 `rejected`。fold 2 的 11 个相邻状态转折
  仅作小样本描述，不承担第二套通过/失败结论。
- lag-7 特征的 NGBoost 内置重要性为 `0`、排名 33；它只能解释当前模型依赖，不能解释为
  因果主控因素。此次增量未修改 ConvLSTM 或导师指定的整体方法框架，也不构成现场验证或
  正式预警证据。
- 至此停止继续给直接五分类器追加 lag 特征；随后仅运行上方已记录的结构性
  residual/transition 方法，该方法同样被拒绝，且没有引入人工冻结或批准步骤。

## 2026-08-30 科研主线恢复（当前优先级）

- 当前工作恢复到导师指定的科研主线：保持 ConvLSTM 大框架不变，以置信区间、逐点速度、
  严格逐点加速度、改进切线角为四项输入，使用机器自动生成的未来变形状态标签训练
  NGBoost 五分类，并输出 8 个测点逐时刻概率/颜色及多测点综合结果。v4 透明规则只作为
  基线，不再替代导师要求的 R5，也不作为 NGBoost 训练标签。
- “NGBoost 需要 `y`”不等于“必须人工逐时刻标注”。本阶段允许用仅在开发历史上拟合的
  自动分段/状态发现方法生成代理标签；独立现场事件真值只限制确认性灾害预警主张，
  formal-v5 的 G1--G4 门禁不阻塞这一自动化历史原型。
- 现有五种子三折 ConvLSTM OOF 产物可直接复用：34,440 行预测等权聚合后为 6,888 个
  测点—时刻，覆盖 3 折 × 287 日 × 8 点，无需重跑 ConvLSTM。下一步先建立严格按折因果的
  四指标 OOF 表，并在 fold 1--2 上审计自动未来状态标签的五级支持、时序泄漏和转移稳定性。
  三折均已参与此前或本轮开发期诊断，不再把 fold 2/3 称为独立验收或未见留出。
- 旧 interval-proxy 不能直接沿用：其 OOF fold 1 只有 green/blue，fold 2 只有
  green/blue/yellow，开发期没有 orange/red，继续调 NGBoost 参数不能解决目标定义问题。
- Figshare/live feed、epoch/ledger、trusted-time 和连续部署控制器转为延期部署支线；当前科研
  原型不等待传感器接口。过度工程化清理也按用户要求延后，不占用本轮实验时间。

## 2026-08-30 自动未来状态标签 v1 诊断（本增量）

- 新增 explicit-only `ootang-ngboost-auto-state` labels-only stage；它不重训或替换 ConvLSTM，
  只聚合既有 5 seeds × 3 folds 的 OOF，接入八测点逐点速度、严格加速度和 fold-fit-only
  改进切线角比较器，并构造完整 H=7 未来目标。正式 stage 在 5.1 秒完成：34,440 行 seed
  预测聚合为 6,888 行测点 OOF，6,720 行有目标，168 行恰为每折每点末 7 日无标签；无跨折
  未来窗口。
- 标签器 v1 采用 fold 1 的每点中位数/IQR、确定性多元变点和长度加权一维 KMeans 五级；
  fold 2/3 只应用固定边界。六项产物连续复跑逐字节一致，集合 SHA-256 为
  `03659acdae63a1259af539c992e122cc9429918e1de5942e51b951c317fb4300`。
- 门禁结论为 `label_gate_passed=false`，所以没有训练 NGBoost。fold 2 测点级 red 为 0；
  fold 1 red 的未来位移/速度中位数反而低于 orange，说明未来加速度在三分量等权严重度中
  主导了颜色顺序。主任务 site 也在 blue→yellow 出现未来位移 `0.1434→0.1113`、未来速度
  `0.1499→0.1220` 的回落；门禁现已显式记录该失败。小类少于 20 仍只作 advisory，没有用
  额外边界测试拖延任务。
- 已冻结失败产物，不通过重写日期、权重或 KMeans 参数“救”结果。下一步只实现一个
  预先登记的 challenger：fold 1 经验 CDF 归一的未来位移速率与未来速度 Q90 等权严重度，
  以 fold 1 的 20/40/60/80% 分位固定五级；site 按 O1/O2/O3 综合作为 NGBoost 主任务，
  单站降为诊断。严格加速度仍是 NGBoost 输入，不再参与标签严重度以避免循环主导。
- 复核导师指定论文后确认：其第五章用位移区间、切线角、速度、变形速率增量经多项式
  逻辑回归融合，但没有提供可直接迁移的外部监督标签；因此本项目不能把论文阈值输出
  冒充现场真值。自动代理标签仍须明确标为 exploratory，独立事件真值决定最终灾害预警
  主张，而不阻塞当前历史科研原型。
- 该 labels-only stage 成功退出表示诊断产物完整生成，不表示科研门禁通过；任何后续训练
  stage 都必须读取 `label_gate_passed`。逐字节复现由 stage 外连续复跑核验并记录集合哈希，
  不让单次进程自证第二次运行。

## 2026-08-30 fold-1 ECDF 自动标签 challenger v2（本增量）

- 新增 explicit-only `ootang-ngboost-auto-state-ecdf`，直接校验并消费 v1 的测点 OOF 与
  manifest，不重复 seed 聚合。每站仅用 fold 1 对未来 H=7 位移速率和正速度 Q90 建右连续
  经验 CDF，两者百分位等权；station/site 均用 fold 1 的 q20/q40/q60/q80 固定五级，site
  保持 O1/O2/O3 先块内等权再三块等权。严格加速度不再进入目标，但仍是时刻 `t` 的模型输入。
- 正式 stage 1.8 秒完成并得到 `label_gate_passed=true`。fold 1 station 为
  `447/445/450/450/448`，site 每级 56 日；fold 2 site 为 `138/66/39/21/16`。fold 1/2
  的 site 未来位移速率与未来速度 Q90 中位数均按色严格递增，H=7 跨折数为 0，标签器
  最大输入日 `2018-12-04` 早于 fold 2 起点 `2018-12-05`。
- 六项 v2 产物外部复跑逐字节一致，集合 SHA-256 为
  `d80a12776c955b69c2ab23e6fdd2b69177d7a3d0948cfe16bf48660916d2369e`。
  fold 2 site red 仅 16 日、逐站缺级/局部不单调只作非阻断提示；site 是后续 NGBoost 主任务，
  测点共享模型只作八点诊断与 SHAP。所有折均已暴露，结果仍是 exploratory proxy。
- 下一步已解除 labels-only 阻断：固定复用旧 pilot 的 NGBoost 参数，不做调参；以 8 点 ×
  4 指标构成 32 维 site 输入，并比较类别先验、严格因果的 `y_(t-7)` persistence 与多项
  Logistic。任何训练 stage 必须显式读取 v2 `label_gate_passed=true`。新模型拟合前确认
  fold 2 的相邻日状态转折只有 11 天，故撤销以该小样本作 fold 3 准入的硬门禁；仍重点报告
  转折指标和样本数，但 fold 2/3 都只作开发期/历史描述，且不参与参数选择。

## 2026-08-31 固定 NGBoost 五级概率分类器（本增量）

- 新增 explicit-only `ootang-ngboost-auto-state-classifier`。stage 读取并校验 v2
  `label_gate_passed=true`，site 主模型只使用固定站序的 `8 点 × 4 个时刻 t 指标 = 32` 维
  白名单，fold 1 的 280 日训练；共享测点模型使用四指标和 station one-hot，只作诊断。
  未读取 future outcome、severity、auto label 或 target end date 作为 X，未改 ConvLSTM。
- 固定比较 NGBoost、fold-1-only 标准化 multinomial Logistic、fold 1 prior 和同折严格
  `y_(t-7)` persistence，不搜索参数、不校准概率、不重采样。输出 3,444 条 site 预测与
  6,888 条八点诊断预测；NGBoost/Logistic/prior 覆盖全部 3×287 日，末 7 日仍发模型信号，
  但 retrospective truth 明确 unavailable。persistence 每折首 7 日 unavailable。
- fold 2 NGBoost 的 accuracy/macro-F1/ordinal MAE/log-loss/Brier 为
  `0.3536/0.2871/0.7429/3.3358/0.9960`；persistence 在 273 日为
  `0.8022/0.6722/0.2234`，prior log-loss 为 `1.6094`。因此本模型没有证明改善，尤其概率
  过度自信且未超过简单状态持续性。fold 2 的 11 个相邻转折日只作描述：NGBoost
  macro-F1/MAE=`0.0800/1.0909`，Logistic=`0.1071/0.9091`，persistence=`0.0571/1.0909`。
  fold 3 NGBoost macro-F1=`0.4635`，仅为已暴露历史描述，不能反向选择模型。
- site permutation SHAP 用 fold 1 的 12 个等距背景日解释 fold 2 的 25 个等距日期，输出
  `25×32=800` 行，标量为期望等级 `Σk·P(k)`。前四项依赖为 ATU2 切线角 `0.5440`、ATU1
  切线角 `0.4815`、ATU5 速度 `0.2094`、ATU2 速度 `0.1799`；只表示模型依赖，不是因果
  主控因素，也不是 ConvLSTM 内部 SHAP。
- 正式 pipeline stage 用时 29.8 秒。时间线同时展示 site 自动标签、site NGBoost 与全部
  8 点的 861 日颜色；SHAP/时间线均导出 PNG/PDF/SVG。源码图件预检无 fail，PDF 最小字号
  分别 7/6 pt，两个最终 PDF 的碰撞审计均为 `0 fail, 0 warn`。300 dpi PNG 是预览，PDF/SVG
  是矢量主件；未额外生成无必要 TIFF。SHAP masker 运行时由 sklearn 发出 3 条矩阵数值
  warning，但所有最终概率、SHAP 值及概率和均通过 finite/归一检查，未为消除提示增加兼容层。
- 下一步只预注册一个因果状态记忆 challenger：把 issue 时刻已成熟的 `y_(t-7)` 作为一项
  机器可得状态记忆，与原 32 个导师指标联合训练 NGBoost，判断能否在保持 persistence 的
  同时改善转折；不启动 horizon、消融、概率校准或参数网格。

## 2026-08-31 lag-7 状态记忆 challenger 预注册（拟合前）

- 只增加一个 site 特征 `lag7_state_level=y_(t-7)`。该标签的 H=7 未来窗在 issue 日 `t`
  结束，因此在“收到 `U_t` 后发报”语义下机器可得；每折前 7 日固定用 sentinel `-1`，不跨折
  填充，也不增加第二个 availability 特征。来源必须恰为七个日历日前，且其
  `target_end_date == t`；其余 32 维仍是八点四指标白名单。
- 固定复用 v1 NGBoost 参数，在 fold 1 全部 280 个 valid 日拟合；不训练测点模型、不做
  SHAP/新图、不搜索 horizon/参数、不校准概率。输出仅含 861 日 site 概率、共同指标、内置
  importance、manifest 和一个模型。
- fold 2 的主要比较使用 lag-7 可得的共同 273 日：memory NGBoost 对提交的 no-memory
  NGBoost 和 hard persistence 报 accuracy/macro-F1/ordinal MAE；两个 NGBoost 另报
  log-loss/Brier，persistence 不伪造概率指标。相邻日 transition 仍取共同 mask，11 日只描述。
- 只有当 fold 2 common-mask 上，memory 的 macro-F1 严格高于 no-memory 与 persistence、
  ordinal MAE 严格低于二者、log-loss 与 Brier 都低于 no-memory，且 log-loss 低于
  fold-1 prior `1.6094`，才称为“改进候选”；否则拒绝。fold 3 仍输出全时刻信号，但不计算
  指标、不参与选择。失败则停止本轮 NGBoost 修补，回到标签/时序方法层讨论。

## 2026-08-30 藕塘 live feed 真实来源审计（本增量）

- 已完成 Figshare 官方 API、论文数据声明与仓库数据血缘的定向核验。当前唯一核实的公开行级
  藕塘源仍是 version 1、file `54029702`（MD5
  `372d1608f46d7fcdb9805568d1c0782a`），共 1461 日且止于 `2020-06-30`；Figshare versions
  API 当前仅列 v1。它可被机器观察是否出现新 release，但不是逐日传感器/finalized feed。
- 本次有边界的公开源检索没有找到从 `2020-07-01` 起、与当前 MJ/ATU 八点及
  Rainfall+RWL 连续兼容的机器可读数据。后续论文描述了藕塘观测，但未提供兼容行级下载/API，
  不能从图表造数或把不同 JW 点位直接拼接为当前源。
- 因缺少 endpoint/auth/owner、权威点位与参考 epoch、日值 QC、真实时间/修订语义和连续历史，
  本轮没有实现伪 producer、通用 HTTP adapter 或无效 Figshare ingest。既有 exact-file drop
  boundary、R1 validator、source receipts 与自动后续链已经够用；真实单 writer 到位后再实现
  source-specific 最薄 adapter。
- 新发现一个 P1 source-contract 可执行性问题：v1 把 Rainfall 定义为完整自然日量，同时要求
  record 在下一日界前 finalized，且 watermark+1 的 bundle/issue 也须在该日界前生成。真实源必须
  明确 accumulation cutoff；若不能满足，应另建版本化 source/target 协议，不静默修改 v1 或用
  已查看 test 选择方案。
- 当前真实机器状态保持 `waiting_for_candidate_feed`；ConvLSTM、operational-v4、冻结
  splits/metrics/thresholds、参数、产物与科研结论未改动。完整证据、接入必需字段和机器恢复
  顺序见 `docs/ootang_live_feed_source_audit.md`。

## 2026-08-30 真实机器 readiness poll 与首门短路（本增量）

- 在 production runtime 上只执行一次
  `uv run python main.py --stage ootang-epoch-registry --manifest runtime/ootang_epoch_registry_v1/machine_readiness_poll_v1/run.json`。
  总耗时 0.088 秒，R1 registry 完整性检查与空链重放后的新鲜诊断为
  `registry_status=waiting_for_candidate_feed`、`reason=candidate finalized feed is absent`；
  feed observation/event/candidate/slot 数分别为 `0/0/0/0`。
- 本轮在第一个真实未满足 gate 立即停止，没有运行 R2a、recovery、settlement、transition、
  cycle-v4、训练或网络，也没有生成下游级联 waiting cache。status 只作为“本次 exit 0 调用后的
  短路提示”，不替代下游各自对 immutable event/receipt 的 authority replay。
- R1 status 与 pipeline manifest SHA-256 分别为
  `c3c65bb1cc453b9d098bea7bf118c0e4aa0f8168f71910bdfd310e812f681318` 和
  `3191e5212d7b9672cea2c8302fa3489433e44e350462f0159a0f9f0c67d4e5c1`；二者位于 ignored
  runtime，仅作本机运行证据，不提交为科研 authority。
- 审计否决了立即增加“线性全生命周期 wrapper”：R2b-v1 clean-start 与 V2 non-clean 是互斥
  sibling，admission-cut 会改变 deploy/runner lock path，R2a repoll 还会重复五种子 smoke；而
  `main.py` 把正常 `waiting_*` 的 exit 0 当阶段完成，盲目串联既浪费时间又会误判语义。
  已有 ACTIVE epoch 的 cycle-v4 调度与下一候选轮换也必须分责，不能因下一 R1 feed 缺失阻断
  当前 ACTIVE scheduler。
- 下一真实机器输入只能是未来、合法、finalized 的
  `runtime/ootang_prequential_live_v1/incoming/daily_finalized_feed.json`。不得从历史藕塘表伪造、
  backdate 或人工补文件；feed 未到时，机器重复 poll 只能保持等待。ConvLSTM、operational-v4、
  冻结 splits/metrics/thresholds、参数、产物和科研结论均未改动；97-path aggregate 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。

## 2026-08-30 atomic `SEALED(old) + ACTIVE(new)` 与 authorized cycle v4（本增量）

- 新增 profile-free `ootang_epoch_active_transition.py`。唯一 create-only immutable event 在同一
  commit 中原子记录 scoped official scheduler 的 old `SEALED` 与 new `ACTIVE`；没有拆成两个
  lifecycle 文件，没有移动 final stable slot，也没有向旧/new ledger 写 lifecycle row。`ACTIVE`
  只授权机器 scheduler 初始化新 genesis，事件明确保留
  `new_epoch_genesis_initialized=false`。
- 首次发布先持有旧 `manager -> cycle -> replay -> shadow` 四锁，再持候选
  `cycle -> deploy -> runner -> replay -> shadow` 五个 writer locks，随后重验 strengthened V2
  completion、exact current-empty R1/R2a authority、candidate/new epoch binding 与只含 held lock 的
  same-slot shadow；这关闭了 empty check 与 event publish 之间的候选写入竞态。事件
  固定 completion path/hash/size、R1/R2a sequence/hash、old fresh tip、candidate/slot/new id、live/
  shadow roots、frozen executable tree、exact cycle-v3 script/config 及 cycle-v4 adapter SHA-256。
  发布后的 repoll/lease 改用 historical R1/R2a replay，不再要求 candidate 当前 namespace 为空，
  因此后续合法 genesis 不会反向破坏既有 transition authority。
- 新增公开无参数 `ootang_prequential_cycle_v4.py`。它持有 manager authorization lease 覆盖完整
  frozen cycle-v3 child，且只使用事件授权的 script/config/live/shadow roots；公开 CLI 无 root
  override。缺 transition 是正常 `waiting_for_active_transition` 且退出 0；child 0/3 分别表示
  complete/busy，其他非零与任何 binding 漂移均 fail closed。cycle-v4 只写独立
  `cache_authority=false` status，不加入 lifecycle authority chain。
- positive claim 严格限定为 `official_machine_scheduler_lifecycle` 与 official scheduler entrypoint；
  unqualified `old_epoch_drained`、generic/canonical/direct fences、old direct entry disabled、trusted/
  anti-rollback、E2、formal warning、automatic calibration、continuous automatic rotation、cross-ledger
  DB atomicity 与 new genesis initialized 均为 false。当前是 local trusted-writer transition，不绑定
  RFC 3161；外部/root-resistant 资格留待独立 TSA/KMS/透明日志证据。
- ConvLSTM、operational v4、冻结 splits/metrics/thresholds、参数、产物与科研结论未改动；没有
  训练、网络、全量科研管线或历史慢速 fault matrix。completion/transition/cycle-v4/main 聚焦测试
  `53/53` 在 4.337 秒通过；Ruff check/format、Python compile、默认/显式 dry-run、diff check 与
  97-path protected aggregate
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3` 均通过。最终独立审查发现并
  修复 candidate-write TOCTOU 与 candidate release 异常跳过旧锁释放两个 P1，复审无剩余
  P0/P1/P2。transition、cycle-v4、对应 focused tests、`main.py` 与其 test SHA-256 分别为
  `443cba9c5faec370f0d87e167623ff306b8cfd6b35e5679a291fbc2a9258a484`、
  `3a65d31fed29c8bf16f9c9db4959260bc0332287101982b5fdc92ad6a2799600`、
  `ce587c2070e15b82ed55bfae714ecb72e1f9c237ca9ea2c463f61dea75fb7680`、
  `abe878d0011c89f0e33af57bc2656c9b3bf9fb6d4d1ebea25a01cd9fd14d0a55`、
  `83af0111181c4635056dfad10a8346eaebe5cd893071544b0face8a896458001` 与
  `45c2e3f8f12573903102d65dc9c6ba095073b497a3d743592e07c2128d44b4a4`。详细边界见
  `docs/ootang_epoch_active_transition_engineering.md`。
- 该条历史 next-step 已由上方真实 readiness poll 修正：当前 runtime 连 R1 candidate 都不存在，
  直接执行 `settlement -> transition -> cycle-v4` 只会产生级联等待。应在合法 finalized feed 到达后
  从 R1 自动恢复；external trusted-time/anti-rollback 与多代 controller 仍保持独立，不用人工
  waiver 扩大当前 claim。

## 2026-08-30 V2 live-ledger prefix attestation correction（本增量）

- transition 前的 correctness audit 发现：既有 completion 虽会拒绝 live count 回退和同 count
  换 head，但在 count 增加时还未证明 frozen admission-cut terminal 是 current ledger 的真实
  prefix。测试也曾用任意新 head 表示 count+5，不能支持文档中的 append-only successor 声明。
- 修正复用 fresh 六族 inventory 已独立重放的 `frozen_live_logical_chain`，要求 ordered entry
  hashes 的长度/current terminal 与 current context 精确一致，并要求 frozen terminal 出现在 frozen
  count 的 exact index；更长但分叉的合法自洽 ledger 现在 fail closed。没有增加新的 I/O、profile、
  proof/event/head/WAL 或额外 ledger replay。
- completion focused `4/4` 与 cycle/main `42/42` 合计 `46/46`，测试执行 1.533 秒；Ruff、格式、
  Python compile 与 diff check 通过。该修正是 atomic transition 消费 scoped event 前的必要证据
  加固，不修改 ConvLSTM/v4、冻结 splits/metrics/thresholds、参数、产物或科研结论。

## 2026-08-30 V2 scoped bounded-drain completion v1（本增量）

- Reachability audit 纠正了上一轮 handoff 的关键假设：V1 clean-start
  `epoch_drain_started`/canonical-route fence 与 V2 non-clean
  admission-cut/manifest/bounded-closure 是互斥 sibling。任何 durable V1 witness 都会令 V2 与
  admission cut inert，故不能用 synthetic fixture 拼出“V1 fence + V2 closure”的成功路径。
- 本轮只实现当前可达且释放锁后仍稳定的 V2 分支。新增 profile-free
  `ootang_epoch_bounded_drain_completion.py`；它在现有
  `manager -> cycle -> replay -> shadow` 四锁内深回放 bounded closure，从 manifest reservation
  取得 exact candidate/slot/old epoch 与 V2 `both_cut` binding，再做一次 fresh 六族只读 inventory。
  当前 live tip 必须是 frozen admission-cut tip 的 append-only successor；只有 actionable item 为零
  时才发布一个 immutable singleton event；waiting 仅写
  `cache_authority=false` status。没有新增 proof、head、WAL 或 all-settled/all-successor 中间层。
- positive claim 严格限定为
  `authority_scope=official_machine_reserved_workset` 与
  `bounded_official_workset_drained=true`。事件明确保留 `old_epoch_drained=false`、canonical old issue
  route fence=false、direct-filesystem writer fence=false、lifecycle/transition=false 与 active switch=false；
  因此不会把 reviewed official-writer threat model 偷换成全局文件系统定理。V1 仍只保留 machine-current
  eligibility；在没有 writer cut 时，其 standalone drained event 会在六锁释放后重新变 stale。
- lean settlement cycle 把该 assessor 作为 bounded closure 后第 17 个静态 coordinator，同一次机器
  poll 自动推进；cycle status 同时保留 closure 与 scoped completion 两个结果。默认科研链仍是
  `features -> convlstm -> ootang-operational-v4`，本轮未修改 ConvLSTM/v4、冻结
  splits/metrics/thresholds、模型参数、预测产物或实验结论。
- focused completion `3/3` 与 cycle/main integration `42/42` 合计 `45/45`，测试执行 1.287 秒；
  Ruff check/format、Python compile 与 diff check 均通过。没有运行训练、完整科研管线、历史
  multi-minute drain fixture、真实网络、人工冻结/批准/清理、force 或 backdate。
- 该条 next-step 已由本轮后续增量实现：没有增加 drain-ready singleton，而是用一个 immutable
  event 原子提交 scoped `SEALED(old) + ACTIVE(new)` 并绑定 scheduler authorization。V1
  completion 仍应与其 writer cut 或独立定义的原子 transition 耦合，不能提前持久化一个可过期的
  drained Boolean。

## 2026-08-30 epoch scope audit 与 machine settlement cycle v1（本增量）

- 目标复核结论：默认链仍严格为 `features -> convlstm -> ootang-operational-v4`，ConvLSTM、
  v4、冻结 splits/metrics/thresholds、模型参数与科研结论均未改动；但 epoch 工程出现局部优先级
  偏移。审计基线有 57,380 行 epoch 生产码与 17,907 行测试，最近四个 aggregate/coverage
  模块无 CLI，bounded closure 在本增量前只有测试调用、没有生产 consumer，属于“方向相关但尚未
  接入机器运行路径”。
- 确认存在局部过度防御：对既有 ready-state fixture 的一次成功 bounded-closure poll 做诊断
  插桩时，0.623 秒内触发 418 次 profile loader、3,535 次 regular-file read，101 个 unique path
  累计约 246 MB；同一 recovery config/implementation 分别被读取 359/243 次。外部文件、锁、
  append-only/CAS、网络/TSA 与 crash-forward 边界继续严格防御；同一 poll 内对 immutable typed
  cut 的递归重复重验和“一个布尔值一个 proof/event/status”从本增量起停止扩张。
- `ootang_epoch_settlement_cycle.py` 与 explicit-only
  `ootang-epoch-settlement-cycle` 主入口让一次 scheduler job 先执行既有 workset recovery 一次，
  再按静态拓扑表各调用其后 16 个既有公开 coordinator 一次，推进到 bounded terminal closure。
  adapter 不复制上游 profile/proof/event 校验，只用 `tempfile + os.replace` 发布 replaceable
  `cache_authority=false` status；后续由机器 scheduler 重复 poll，不需要人工冻结、批准、清理、
  force 或 backdate。
- 针对用户提出的过度工程化复核，删除了零可配置性的 21 行 settlement profile、约 50 行
  loader/精确字段校验、无人消费且不绑定 artifact bytes 的 result/progress SHA 链、8 项 profile
  capability 声明的重复传播、字符串动态 registry、三层异常和无 consumer 的 Result 字段；status
  仅保留一个 `cache_authority=false` 边界。`main.py` 删除
  已被全局 `source_fingerprint()` 覆盖的 18 个 Python input，仅保留 transitive recovery profile
  与 16 个 coordinator profile。实现从 414 减至 230 行，focused test 从 107 减至 89 行，
  `main.py` 减少 22 行并删除 21 行配置，上述四文件净删 245 行；计入 main integration test 的
  1 行合同调整后，code/config/tests/main 合计净删 244 行。ordered single-pass、原子 cache 和上游
  durable authority 均保留。
- 本 adapter 仍不产生 drained/lifecycle/active/E2/formal authority；这些边界由 owning stage 与文档
  表达，不再复制成无人读取的状态字段。测试预算收敛为 cycle 两个行为测试和 `main.py` 一个 stage
  contract；cycle/main 合计 `42/42` 在 0.164 秒通过。Ruff format/check、Python compile、
  static callable import、main dry-run、residue 与 diff checks 均通过；没有运行训练、全科研管线、
  历史 fault matrix、真实网络或 live epoch mutation。
- 当前 implementation/test、工程审计文档、`main.py` 与未改动 ConvLSTM model artifact SHA-256
  分别为 `ce720a287f64c1a4de75ed2a11c64bca40ed0d82d6737874e7a887aedac647ec`、
  `44e8bda5151e71643a0cf2dc57f3e4de67b054ec695dccc0f87c8fef716e7460`、
  `5ef9b986a16dac8d8dfbe7a533eaefea98f1f4e7fc69db8e801cd87b73d0b46b`、
  `4d38467077ae54e2bbeae8e04c491c1cbfc89762712eb49ca43f7bb6adcbf084` 与
  `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`；已删除的 settlement
  profile 不再有 SHA。
- 该条历史 next-step 假设已由后续 reachability audit 纠正：V1 drain-start/route-fence 与 V2
  bounded-closure 是互斥 sibling，不能合法合并。实际 downstream boundary 仅消费 V2 admission
  cut、既有 closure 与 fresh four-lock inventory，并只发布 scoped official-machine decision。

## 2026-08-30 source-derived bounded terminal closure v1（本增量）

- 新增独立 assessor `ootang_epoch_source_derived_bounded_terminal_closure.py` 与严格
  hash-pinned profile，namespace 为
  `workset_recovery_v1/source_derived_bounded_terminal_closure_v1/`。它在既有机器锁序下深验同一
  immutable source edge 的 frozen manifest/reservation、cross completion、完整 D/R/I reservation、
  overlay 与已发布 current-effective terminal coverage；自身只写 content-addressed proof、singleton
  event 和 `cache_authority=false` status。
- identity 仍固定为完整 `key_id + natural_key + namespace_digest`，发布前同时证明
  `B = {P} ⊎ Qretained ⊎ Rold ⊎ I` 与
  `E = {P} ⊎ Qretained ⊎ D ⊎ Rnew`。derived reservation 中 D/R 行在 overlay 前没有最终
  `key_id`，本 assessor 使用直接 hash-pin 的 overlay normalizer 与 frozen manifest SHA-256 重建
  final identity；不允许 natural-key-only 比较。D/R/I 内部唯一、按 natural key 两两不交、count 与
  keyset digest 均重新计算。
- 每个 frozen item 都有显式 resolution row：P/retained 绑定 current terminal row，Rold 绑定 exact
  terminal Rnew，I 只记录同一 source edge 的 exact invalidation supersession；每个 D/R/I successor
  也逐项绑定 terminal 或 supersession 解析。只有 matching singleton event 发布窄 claim
  `current_source_derived_bounded_terminal_closure=true`；generic bounded recovery、all-reserved、
  all-successor、terminal/transitive closure、drained/lifecycle/activation 等仍为 false。后续
  reachability audit 已确认它不能绑定互斥的 V1 drain-start/route-fence；合法下游改为绑定 V2
  physical cut 与 fresh four-lock capture。
- focused `3/3` 通过，用时 16.637 秒；bounded-closure/current-effective/source-parent/
  retained-base/current-`D/R` 窄链 `18/18` 通过，用时 59.004 秒。覆盖完整两等式与幂等 status
  重建、缺 current-effective event 时不发布、proof-only crash 自动续接。Ruff E7/E9/F、Python
  compile、strict profile load、protected model 与 diff checks 通过；未运行训练、全科研管线、真实
  网络或任何人工冻结/批准/清理。
- 当前 implementation/profile/test、工程文档、受保护 `main.py` 与 ConvLSTM model SHA-256
  分别为
  `9784bebbf9fa560851c1a7184cb9f8bf98a97ae848b8575bff090ff77d62a81f`、
  `6f149b0d4aea0906de3e9df27a6159d16bafc57d5fa531602849ae6126a52261`、
  `d84f372c788485a4c240bd083c1f2e1cf07dc5b86c37d7ea8dfffca8b3882615`、
  `554c262dc842dae121ffdfd2d5ce0fdb7c74803fdf520c32394356c813c053a9`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898` 与
  `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。该条历史
  next-step 先被后续互斥性审计修正：V2 completion 只绑定 matching manifest/candidate、V2
  admission cut、bounded closure 与 fresh inventory；再由当前增量的独立 one-event
  SEALED/ACTIVE transition 与 no-argument authorized cycle v4 消费，不回写本历史 closure claim。

## 2026-08-30 source-derived current-effective workset terminal coverage v1（本增量）

- 新增独立 assessor
  `ootang_epoch_source_derived_current_effective_workset_terminal_coverage.py` 与严格
  hash-pinned profile，namespace 为
  `workset_recovery_v1/source_derived_current_effective_workset_terminal_coverage_v1/`。
  coordinator 在既有机器锁序下只读深验同一 current overlay object/event、已发布 source-parent
  terminal aggregate、retained-base terminal coverage 与 current `D/R` terminal coverage；自身仅写
  content-addressed proof、singleton event 和 `cache_authority=false` 的可替换 status。
- identity 固定为完整 `key_id + natural_key + namespace_digest`。发布前重新验证精确集合等式
  `E = {P} ⊎ Qretained ⊎ Qdri`：三类内部唯一、两两不相交、并集严格等于 current effective
  identity set。随后按 current overlay 拓扑顺序逐项绑定所属 leaf proof/event SHA-256 与可用的
  per-item coverage-row SHA-256，再绑定 current keyset、identity-set 与 dependency-graph digest；
  count-only、status、proof-only、旧 `R` 或 `I` 均不能替代精确集合关系。
- 只有 matching singleton event 发布窄 claim
  `current_effective_workset_terminal_coverage=true`。它只描述一个 current source-derived overlay，
  不等于跨 generation 的 `all_effective_items_terminal`，也不证明 recovery-v6/transitive closure、
  all-reserved/all-successor、bounded recovery、drain/lifecycle/activation、trusted/E2、network 或
  formal warning。缺一家 leaf 时机器只 waiting；自身 proof-only crash 的下一 poll 只补 matching
  event；已经发布后任一精确 leaf 丢失或漂移会 fail closed。
- focused `3/3` 通过，用时 14.731 秒；current-effective/source-parent/retained-base/current-`D/R`
  窄 family chain `15/15` 通过，用时 45.205 秒。覆盖完整三类精确并集与幂等 status 重建、缺
  source-parent 时不发布、proof-only crash 自动续接。Ruff E7/E9/F、Python compile、strict
  profile load 与 diff checks 通过；未运行训练、全科研管线、真实网络或任何人工冻结/批准/清理。
- 当前 implementation/profile/test、工程文档、受保护 `main.py` 与 ConvLSTM model SHA-256
  分别为
  `80f84db4e88dc5fdcc8e768eda2ea11f93e89c89c12726522ed9efb015361ac3`、
  `f82269a8d2c60d9c41bf0d48dab626444084aae2df481f58c19a12d056bfc1b7`、
  `53b33573a0dceeaa775375a9edbf1dc1fac8c3e4f0f16c9d5147c0a7d28bab87`、
  `05eefbedd4ba3940e247d335e9121b9f52e46749b7b7a66a1f8f0771339a238f`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898` 与
  `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。下一窄
  增量应建立 separately versioned bounded terminal-closure assessor：在一个 immutable cut 下证明
  frozen reservation inventory、current effective identity union 与 source-derived successor inventory
  完整后，才允许后续 drain/lifecycle assessor 消费；zero-`D/R` 精确空分支也应由机器显式处理，
  不能用人工例外。

## 2026-08-30 source-derived retained-base terminal coverage v1（本增量）

- 新增独立 leaf
  `ootang_epoch_source_derived_retained_base_terminal_coverage.py` 与严格 hash-pinned
  profile，namespace 为
  `workset_recovery_v1/source_derived_retained_base_terminal_coverage_v1/`。authority
  只读深回放 current source-derived overlay、recovery-v6 per-item receipt/event 与可选的
  published source-terminal aggregate per-item evidence；自身只写 content-addressed proof、
  singleton event 与 `cache_authority=false` 的可替换 status。
- 分母严格定义为 frozen base 中在 current overlay 后完整
  `key_id + natural_key + namespace_digest` 未改变的行，再排除唯一 current
  `source_snapshot_ingested` parent。实现同时验证
  `E = P ⊎ Qretained ⊎ D ⊎ Rnew`：旧 `R` identity 必须消失、`I` 必须被删除、
  `D/Rnew` 不得混入 retained denominator；任何未分类 base identity 消失都会 fail closed。
  空 `Qretained` 是合法的精确机器结果，可发布零项证明，无需人工冻结或例外。
- 每个非空 retained target 只能由 exact recovery-v6 terminal receipt + published event，或
  exact source-terminal aggregate proof + event 覆盖，且两种 provenance 必须不相交。whole
  frozen-manifest boolean、status、receipt-only、aggregate proof-only、旧 `R`、`I` 均不能计数。
  可选 source-terminal authority 只绑定实际被选中的 per-item rows，避免后来无关 aggregate
  事件增长使既有 proof 漂移。proof-only crash 的下一 poll 只补 matching singleton event。
- focused `6/6` 通过，用时 7.243 秒；本 authority、current overlay、frozen-manifest terminal
  assessment 与 step-dependency authority 直接链 `20/20` 通过，用时 12.641 秒。覆盖空子集、
  非空 recovery terminal evidence、published source-terminal per-item evidence、缺证据等待、
  `R/I` 排除、status 重建与 proof-only crash adoption。Ruff format/E7/E9/F、Python compile、
  strict profile load 与 diff checks 通过；未运行训练、全科研管线或真实网络。
- 当前 implementation/profile/test、工程文档、受保护 `main.py` 与 ConvLSTM model SHA-256
  分别为
  `2db6b5bf204990aa3879cf90d8492a57c04e098e5ee3d8ede824af973e1308c8`、
  `8db880133d09d785784aec029f7b84e4410b042af39a50c2eda7635e74d11456`、
  `0b8485a7c1043d4d62cd051bf0f06276aeb9c8fd0224dbede1116d3afccabd14`、
  `bb375b895dcc7c42ec90c22a0875c7c877f205f8c95cdcc34c9a33f6b7e5457f`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898` 与
  `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。下一窄
  增量可构建 current-effective-workset terminal coverage assessor：只合并本 retained-base
  event、已发布 source-parent aggregate 与 current `D/R` coverage，并继续把 generic
  recovery/transitive closure、drain/lifecycle 留在独立边界。

## 2026-08-29 source-derived source-parent terminal aggregate v1（本增量）

- 下一层不能直接声称 whole effective workset terminal：source-ingest parent 的 pinned recovery
  plan 明确为 `source_snapshot_ingested -> derived_outcome_items_required -> []`，且因 content-
  dependent obligations 未知而保持 `closure_resolved=false`、`terminal_actions=[]`；cross-freeze
  receipt/event 也显式保持 `terminal_for_recovery_v6_key=false`。因此本增量先新增独立 leaf
  `ootang_epoch_source_derived_source_parent_terminal_aggregate.py` 与严格 hash-pinned profile，
  namespace 为
  `workset_recovery_v1/source_derived_source_parent_terminal_aggregate_v1/`。
- authority 在既有锁序下深验同一 current overlay 的完整证据链：frozen/cross/current effective
  source parent 必须是唯一且完全相同的 `key_id + natural_key + namespace_digest`，不得落入
  `D/R/I`；matching cross completion 必须证明 source snapshot 已 ingest、derived batch 已完整
  classified；derived reservation/event 与 overlay event 必须绑定同一 source edge 和完整 D/R/I；
  最后必须存在 matching current `D/R` terminal coverage proof + singleton event。D/R count 与
  ordered-row digest 直接取该深验 proof，I 只作为 overlay supersession audit，不冒充 terminal。
- content-addressed proof 记录原 unresolved recovery plan，并以 pinned `recovery._step_id` 绑定
  step 0 `source_snapshot_ingested` 与 step 1 `derived_outcome_items_required`。只有 matching
  singleton event 发布后，窄 claim `current_source_ingest_parent_terminal=true`；广义
  `source_parent_terminal`、recovery-v6 terminal、whole effective、transitive/transition closure、
  all-lanes、drain/lifecycle/activation、trusted/E2/formal 等均保持 false。upstream coverage
  proof-only 时只 waiting；自身 proof-only crash 只补 matching event。status 明确
  `cache_authority=false`，删除后可由 proof/event 自动重建。
- focused `3/3` 通过；本 aggregate、effective D/R coverage、dependent/source-only
  consumption/dispatch 与 overlay 的七模块相邻链 `30/30` 通过，用时 81.570 秒。审查关闭了
  synthetic D/R count、canonical step-id、过宽 status 命名、broad false 集合与 status cache
  标识问题；最终 integrity 与 scope 两路只读复审均为 P0=0/P1=0/P2=0。Ruff format/E7/E9/F、
  Python compile、strict profile load 与 `git diff --check` 通过；未运行训练、全科研管线或真实网络。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `43d541e0eb17144eddc127fdfb7b9c826c5038877d9ae4a9cad08cea5c92447d`、
  `6c9f89bb0f2089f19df71fa9a0bd47a54db8df4638e596ddf98f4c513c2f0b4e`、
  `0a4ebd9feb6784402d4d2c3dbe2fe6c1c93b662856fb1761fb730429887d939a`、
  `6dfc283f0ba5e60b3568a39b3de623ad77d9afbe5f8add6001cc7adce8ef367a`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。下一窄增量
  应实现 retained-base exact-subset terminal authority：逐项深验 current overlay 中完整 identity
  未改变的 base rows，不把 whole frozen-manifest boolean 当 blanket，也绝不复用旧 `R` 或已删除
  `I` identity；之后才可汇总 current effective workset coverage。

## 2026-08-29 source-derived effective outcome terminal coverage v1（本增量）

- 新增独立 leaf `ootang_epoch_source_derived_effective_outcome_terminal_coverage.py` 与严格
  hash-pinned profile，namespace 为
  `workset_recovery_v1/source_derived_effective_outcome_terminal_coverage_v1/`。authority 对上游
  只读，深回放 current effective overlay、source-only consumption、dependent dispatch/consumption
  与 recovery transition contract；自身仅写 content-addressed proof、singleton event 与非权威
  status cache。
- 分母 `Q` 固定为当前 overlay 的完整 normalized `D/R` 集，禁止按 ready candidates 缩小。
  `Qsrc` 只含 dependency list 恰为 source key 的行；其他行均在 `Qdep`。只有包含 source edge 且
  其余依赖全部属于当前 `D/R` 的行进入 supported-dependent 诊断类；retained-base、no-source 或
  其他 unsupported 行仍在分母中并保持 missing。覆盖只接受 deeply replayed terminal event 的
  exact `key_id + natural_key + namespace_digest`；intent、receipt-only、status、materialization、
  live-ledger bytes、source gate、旧 rebound identity 与 `I` 均不计数。
- 仅当非空 `Q` 与 source/dependent terminal evidence 构成 exact disjoint bijection，且三条上游
  durable frontier 均完整时，proof/event 才声明
  `all_current_effective_d_or_r_terminal=true`。proof-only crash 的下一 poll 只补 matching event；
  orphan/branch/changed overlay 或已发布覆盖失效均 fail closed。whole-effective、source parent、
  recovery-v6、transition closure、drain/lifecycle/activation、trusted/E2 与 formal-warning claims
  继续为 false。
- focused `3/3` 通过；六模块相邻链（本 authority、dependent consumption/dispatch、source-only
  consumption/dispatch、overlay）`27/27` 通过，用时 56.632 秒。审查发现并关闭了 reused drain
  publication kernel 的直接 pin 缺口，以及 read-only/依赖分类两项诊断口径问题；最终 integrity
  与 scope 两路只读复审均为 P0=0/P1=0/P2=0。Ruff E7/E9/F、Python compile、strict profile
  load 与 `git diff --check` 通过；未运行训练、全科研管线或真实网络。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `2cf201b5f2b9e439b37e7b5880b39776f442ed189093b6d866c3276f7f6e4bf6`、
  `32eca2b5de227f77d42de79fb0be62b7a0a824712ee9bf19489675b8716ede5c`、
  `ec019a7db449273d8b6147b6748172b3574f7efa480383c066a5e96ae8f25903`、
  `1e45ccec7ba6750e3d7fe61aa4416e598c52d2fd9e786162d9daafc9f7891520`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。下一窄增量
  应构建 versioned current-effective-workset terminal assessor：在新 `D/R` coverage 之外分别核对
  current source parent 与 retained-base families，且不得复用已被 `R/I` 替换或删除的旧 identity，
  也不得把 family coverage 扩大成 recovery/transitive closure 或 lifecycle authority。

## 2026-08-29 source-derived dependent outcome consumption v1（本增量）

- 新增叶子 sibling `ootang_epoch_source_derived_dependent_outcome_consumption.py` 与严格
  hash-pinned profile，namespace 为
  `workset_recovery_v1/source_derived_dependent_outcome_consumption_v1/`。coordinator 深回放
  current effective overlay、source dependency authority 与 dependent dispatcher durable state；
  只有同时拥有 exact intent/receipt/append-only event 且仍匹配当前
  `key_id + natural_key + namespace_digest` 的 dependent `D/R` 才能进入消费候选。
- D2 的 `step_index=3`；step id 绑定 overlay event SHA、dependent-dispatch event SHA、
  dependency-proof SHA、exact key 与 action。CAS 前 create-only intent 完整绑定 parent
  intent/receipt/event refs、dependency proof、effective row/transition、nonterminal previous-step
  adapter、live-ledger exact expected-pre-head、完整有序 canonical EventSpecs 与 recovery contract。
  dependent receipt-without-event、status、source-only event、旧 `R` identity 与 `I` 均不授权。
- 唯一 ledger writer 仍是 pinned recovery expected-pre-head CAS。fresh 只在 intent 固定 head 写入；
  post-CAS 只采用 exact positioned contiguous slice，receipt-only 只补 hash-chained terminal event；
  foreign/partial/displaced/different suffix 禁止 rebase。terminal 仅属于 exact D2 effective key，
  recovery-v6/source parent/D1 dependency parent、whole-workset closure、drain/lifecycle/activation、
  trusted/E2/formal warning 均保持 false/unproved。
- focused `5/5` 与 dependent-consumption/dispatch、source-only consumption/dispatch、overlay 直接链
  `24/24` 通过。首轮测试发现的 nested overlay path P1 已在 CAS 前关闭；独立完整性审查发现的
  upstream Busy 误分类 P1 与 committed-slice Busy P2 均改为保留机器可重试 Busy 语义，并加入
  prerequisite Busy 回归。最终 integrity 与 scope 两路只读复审均为 P0=0/P1=0/P2=0。
  Ruff format/check、Python compile、strict profile load 与 diff checks 通过；未运行训练、全科研
  管线或真实网络。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `83c32fd80e07bed0cf2ec95d173f3152dd8d0c8d1e0aa3283803481495af860a`、
  `b5169b116116c27cf755913f4a52a3c2ecf68b46213ffb77cc6894013c92eb57`、
  `b645dd36ef0a6d990a795be08f1c2bb0ec3c965f4e925ec3e79e866410955703`、
  `9312ae87d31ec06abaa0a259f38be9db330992db86b722501546be704878326f`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。下一窄增量
  优先实现 current-overlay effective `D/R` terminal coverage aggregate：合并 source-only 与
  dependent consumption exact terminal events，但不得把 source gate、retained-base/unsupported
  dependency 或覆盖证明扩大为 recovery/lifecycle closure。

## 2026-08-29 source-derived dependent outcome dispatch v1（本增量）

- 新增独立 `ootang_epoch_source_derived_dependent_outcome_dispatch.py` 与严格 hash-pinned
  profile，位于 sibling namespace
  `workset_recovery_v1/source_derived_dependent_outcome_dispatch_v1/`，不反向修改或 pin 已冻结的
  source-only dispatcher/consumption。coordinator 先深回放 effective overlay、historical
  dispatcher 与 effective-key consumption；候选必须是当前 overlay 中非 source-only 的 exact
  `D/R` row，并按稳定拓扑顺序每 poll 最多推进一个。
- 当前 reviewed graph 为 `source -> D1 -> D2`，且 `D2` 同时直接依赖 source。source edge 只由
  exact cross-freeze gate 满足；其余 `D/R` edge 必须由匹配当前 `key_id + natural_key +
  namespace_digest` 的 terminal consumption receipt/event 满足。D1 materialization-only、consumption
  receipt-without-event、旧 `R` identity、`I`、status 与 retained-base/unsupported dependency 均不能
  解锁 D2。
- readiness 成立后直接复用 pinned historical planner/action 和 canonical materializer 写入 D2，
  不发布空泛 readiness proof，也不调用 current-source selector 或 public materializer。create-only
  intent 绑定 overlay/source gate、完整 dependency proof、effective row/transition、exact historical
  materialization contract 与实现哈希；durable 顺序为 intent -> canonical commit/adoption ->
  nonterminal receipt -> append-only nonterminal event。post-materializer 与 receipt-only crash 均只做
  exact forward adoption/healing，不重复 outcome。
- focused `4/4` 与 dependent-dispatch/consumption/source-only-dispatch/overlay 相邻回归 `19/19`
  通过；Ruff format/check、Python compile、strict profile load、`git diff --check` 通过。authority
  scope 与 durable integrity 两路独立只读复审均为 P0=0/P1=0。未运行训练、全科研管线或真实网络。
  当前 event 仍保持 `next_action=outcome_or_revision_consumed`，effective/recovery-v6/source parent
  terminal、full closure、drain/lifecycle/activation、trusted/E2 与 formal warning 均为 false/unproved。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `c172c08ef48e0153b139542998bb6cf0cd382f5e1940593107ff7424713a2c59`、
  `d374df70debab962143b584c0167661d005b9d7ee36495be44db84bbcb6beb3b`、
  `abfbbb4345bb79a2a43a1a95ecef159c55998a3e2417698fc214dc2c6356e7f2`、
  `d979acdad960643b68fb033185ee5c58360c998e41d49663021aac58278bff57`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。下一窄增量是
  versioned dependent consumption bridge：只接受本 namespace 的 exact live expected-pre-head 与
  ordered canonical EventSpecs，terminalize D2 后才允许继续解锁后继 dependent item。

## 2026-08-29 source-derived effective outcome consumption v1（本增量）

- 新增独立 `ootang_epoch_source_derived_outcome_consumption.py` 与严格 hash-pinned profile。
  coordinator 只接受已深验的 effective overlay 与 matching historical materialization
  dispatcher intent/receipt/event；只把 evented `D/R` 纳入候选，`I`、status、intent-only 与
  receipt-without-event 均不授权 live-ledger mutation。dispatcher receipt 仅转换为内存
  recovery previous-step adapter，不写入或伪造 recovery-v6 receipt。
- 使用真实 base `Reservation` 作为 frozen-cut/runtime context，直接复用 pinned recovery
  consumption planner/action 的四个 canonical 分支：outstanding 43 events、settled revision
  16 events、backfill revision 8 events、first backfill 1 event。create-only intent 完整绑定
  exact expected-pre-head、recovery contract 与 ordered canonical EventSpecs；唯一 ledger writer
  是既有 `append_transaction_at_pre_head_v1` CAS，不调用 current-source selector、public
  materializer 或网络。
- durable 顺序固定为 create-only intent -> canonical CAS commit/adoption -> create-only terminal
  receipt -> append-only terminal event，每 poll 最多推进一个 key。post-CAS crash 只采用 exact
  positioned complete slice，receipt-only crash 只补 control event；intent 后先出现 foreign suffix、
  partial/displaced/different slice 均禁止 rebase 并 fail closed。terminal scope 仅为当前 overlay 的
  exact effective key；recovery-v6 key、source parent、其他 effective items 与 full closure 仍为 false。
- focused `4/4` 真实 SQLite ledger 测试通过，覆盖 first-backfill fresh CAS/幂等、post-CAS
  不重复采用、receipt-only 补 event、dispatcher receipt 无 event 不授权；consumption/dispatcher/
  overlay 相邻回归 `15/15`、recovery 回归 `57/57` 通过。Ruff format/check、Python compile、
  strict profile（含 dispatcher 与 recovery 的传递 pins）及 `git diff --check` 通过。未运行训练、
  全科研管线或真实网络。durable integrity、authority scope 与 test realism 三路独立只读复审
  均为 P0=0/P1=0；非阻断备注仅为本桥端到端 fixture 动态覆盖 1-event first-backfill，
  43/16/8-event 分支继续由 pinned recovery regressions 直接覆盖。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `fb03dfa502d7402b824cec16b36698d0de6349e419feac8a34981ff9e05b0789`、
  `efd33c6c3e9cb386d64cd1e44720b4019c3468a774d695ddc3f77cca3efa863b`、
  `1304ec0c4f57f315b50a7a691ab3109a4cd4447c68d47c77e4cf71b273064777`、
  `a0c01b931a204f37aa4e90825fff2eefa01f34115020be2ef2a1a509a2016ba6`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论，也不声明
  all-effective terminal、transition closure、drain/lifecycle/activation、trusted/E2 或 formal
  warning。下一步是 versioned dependent-readiness dispatcher：只用本 namespace 的 exact terminal
  event 解锁 D/R dependency，并继续用 cross-freeze gate 处理 source parent，避免与 v1
  materialization dispatcher 形成反向 hash-pin 环。

## 2026-08-29 source-derived historical outcome dispatch v1（本增量）

- 新增独立 `ootang_epoch_source_derived_outcome_dispatch.py` 与严格 hash-pinned profile。
  coordinator 只消费已深验并发布的 effective overlay object/event，每 poll 最多推进一个
  source-only-ready 的 effective `D/R` outcome；`I` 永不 dispatch。matching cross-freeze
  receipt/event 与 overlay event 仅构成 source expansion gate，不伪造 source parent 或
  recovery-v6 terminal receipt。
- materialization planner 不调用 current-source selector 或 public `materialize_outcome()`；它从
  immutable derived authority 重建 exact historical `N+1` successor source，直接构造 selection、
  input manifest、outcome 与 receipt contract。真实 base `Reservation` 只提供 frozen cut 与 runtime
  paths，不构造或传入 synthetic effective manifest，不调用 recovery coordinator。
- 历史 `current_source_pointer` obligation 的 logical path 绑定 public pointer path，但 SHA/size
  独立绑定 `N+1` snapshot receipt 中的 immutable content object；不会读取该 public path 作为历史
  CAS，也不会把已推进到 `N+2+` 的 pointer/inbox 回滚。其余 activation/semantic/revision-head/
  snapshot artifacts 与 dataset 均精确绑定 historical source。
- durable 顺序固定为 create-only effective-key intent -> pinned canonical materializer commit/adoption
  -> create-only dispatcher receipt -> append-only event。intent-only 与 materializer-commit crash 均
  可 exact forward-adopt；receipt-only 只补 event。已有 receipt 必须逐字节等于实际 immutable chain
  member；fresh 仅允许空链 genesis 或 declared predecessor 为 unique tip。receipt/event 保持
  `next_action=outcome_or_revision_consumed`、effective/recovery-v6 均非 terminal。
- focused `5/5` 通过；dispatcher/overlay/cross/derived `28/28` 与
  recovery/materializer/live-source `120/120` 通过。Ruff format/check、Python compile、strict profile
  与 6 个 direct upstream SHA pins、`git diff --check` 均通过。独立审查复现的 event replay P1
  （用空 timestamp 重算 entry hash，导致成功后的下一 poll fail）已改为使用 persisted UTC time
  exact 重建完整 event，并由第二次 poll current/单例 intent-receipt-event 回归关闭；另一处
  historical interior adoption P1 已增加当前 chain pointer/inbox 的只读合法性验证，同时禁止历史
  reconcile/回拨。两路最终独立复审均为 P0=0、P1=0。未运行训练、全科研管线或真实网络。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `5d51174c16bdff0dabc88af357bf54ba0e1281aa5b69e06d198b54d4a5941fab`、
  `5d9d85174620cd29b3b216fdd3cec9a9b61d05176cb8a16697f5be3af112a995`、
  `774a5ae566e813238028c754c7128a3cd932d646ba82bf4f64e05fa66ac38ad1`、
  `9678bf442a983d771dbc55a794baf5d34fd0996307000fee93ae1d9bb0d9b5f8`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量不修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论，也不声明
  consumption、effective/source/recovery-v6 terminal、full closure、drain/lifecycle/activation、
  trusted/E2 或 formal warning。下一窄增量是独立 effective consumption bridge：以本 receipt 为
  previous-step authority，绑定 live ledger exact expected-pre-head/EventSpecs 后执行或采用 CAS，
  只有其 terminal receipt/event 才能解锁后继 D/R。

## 2026-08-29 source-derived effective-workset overlay v1（本增量）

- 新增独立 `ootang_epoch_source_derived_workset_overlay.py` 与严格 hash-pinned profile。
  authority 在存续的 manager/cycle/replay/shadow 四锁下，先只读证明 public source pointer
  等于 receipt registry tip，再深验 frozen manifest reservation/event、source-derived
  reservation/event 以及 matching cross-freeze completion receipt/event。pointer 缺失、陈旧或
  分支时直接 fail closed；本层不会调用 pointer repair，也不执行 source ingest。
- effective 变换固定为 `Meff = (M0 - I)`、按 natural key 用完整 `R` row 替换、再加入 `D`。
  `D/R/I` 必须两两不交；D 必须是新 key，R 必须命中旧 key 且 namespace/key-id 改变，I 必须与
  frozen old row 完全一致。除 machine-selected source outcome 外的 row 不允许被 D/R/I 改写。
- 所有 effective item 重新验证 namespace，并以 frozen manifest SHA 重算 key-id；同时发布诚实的
  natural-key-set digest、包含 natural/namespace/key-id 的 identity-set digest、transition-plan-set
  digest 与稳定拓扑 dependency-graph digest。保留项若仍依赖 I、出现 unknown/self/duplicate edge
  或 cycle，均在写入前 fail closed，不机器猜测或删除依赖。
- durable 顺序为 compact content-addressed create-only overlay object -> singleton append-only event；
  完整 effective rows 从 immutable base+D/R/I refs 重建，object 明确
  `effective_rows_embedded=false`。object-only crash 只补 matching event；status 仅为 cache。
- focused `6/6`、overlay/cross/derived/inventory/manifest/recovery 核心 `93/93`、live-source
  `32/32` 通过；Ruff、Python compile、strict profile 与 10 个 direct upstream SHA pins、
  `git diff --check` 均通过。独立审查复现的 P1（只读 gate 与可写 cross loader 间 pointer-loss
  TOCTOU）已改为 overlay-owned pure-read cross replay，并由 gate 后删除 pointer 的回归证明
  recovery call=0、无 object/event；历史 N+2 场景仍保持 N+2 且正常发布。未运行训练、全管线或
  真实网络；两路最终独立只读复审均为 P0=0、P1=0。未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `54ea77652bc5f020146b777d98cd34e1ec26895f363d35a4cb5933b93ef11be7`、
  `d80504e58393d58f284665ed471f19e51b09e847153df7f4143ab99bdf70e8d3`、
  `836da67f06c7560900a570c1fc729d0868f2842cee947f4b3923ed8473f98a1b`、
  `183a2bb62da5b7e75cea9117bec9c628bbb4aac7d09b38db0ba3eab435a92561`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量只实现 exact source edge 的 derived future-work reservation/effective DAG；仍保持
  `all_content_dependent_lanes_reserved=false`、source parent/effective items 非 terminal，且不创建
  recovery-v6 receipt、materialization/consumption、full closure、drain/lifecycle/activation 或
  formal warning。下一步是独立 historical-N+1 effective dispatcher，一次调度一个 effective
  outcome item，不能把 overlay 伪装为 recovery-v6 manifest reservation。

## 2026-08-29 cross-freeze source-ingest writer/adoption v1（本增量）

- 新增独立 `ootang_epoch_source_ingest_cross_freeze.py` 与 profile
  `1.0.0-machine-writer-adoption`。adapter 在 admission cut 后只使用仍存续的
  manager/cycle/replay/shadow 四锁；不会打开、替换、删除或重建 cut legacy deploy/runner
  sentinel，也不会调用 public `ingest_source()`。它对 reviewed source profile 做内存深拷贝，唯一
  runtime 变化是把 `incoming_feed` 指向 manifest-bound、content-addressed、create-only prepared
  feed，再调用 pinned private source kernel；原 source snapshot receipt 继续作为 commit point。
- durable source 前置顺序为 immutable feed copy -> deterministic prepare -> deterministic intent。
  mutable inbox 在 intent 后变化不会改变 source edge。只有 exact slot 的 prepare/intent 已深验后才
  允许 receipt-before-pointer recovery；且恢复前先重放完整 registry，要求 head 是 frozen
  predecessor 的 exact N+1 child，并重建 semantic lineage/feed/revision diff，feed raw
  SHA/size/bytes 必须与 prepared intent 完全相同。无 intent 或 wrong-feed N+1 均在 pointer mutation
  前 fail closed。
- fresh N->N+1 执行一次 writer；current 已为 N+1 或 N+2+ 时只采用 immutable historical N+1，
  不回退 pointer、不重写 source。source commit/adoption 后先释放四锁，机器驱动既有
  source-ingest derived-reservation coordinator，再重新取锁并重放 prepare/intent；只有 matching
  published derived event 才能发布本 namespace 的 create-only completion receipt 与 append-only
  event。receipt-only crash 只补 event，不重复 source writer 或 derived ensure。
- completion 只声明 `source_snapshot_ingested=true` 与 `derived_batch_classified=true`，并显式保持
  `terminal_for_recovery_v6_key=false`。recovery-v6 receipt/event、D materialization/consumption、R/I
  overlay、all-lane/full-workset/closure/drain/lifecycle/activation/trusted/E2/formal claims 均未创建。
- focused tests `9/9`（5.508 s）；cross-freeze/derived/inventory/manifest/recovery 核心回归
  `87/87`（13.910 s）；live-source 回归 `32/32`（2.330 s）。Ruff format/check、Python compile、
  strict profile 与 11 个 direct upstream pins load 通过。独立审查复现的 P1（合法 intent 下错误
  N+1 receipt 可能先推进 pointer）已用 pre-mutation exact-child/feed 深验与回归关闭；最终两路
  只读复审均为 P0=0、P1=0。未运行训练或真实网络，未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `14b975d4198716d0699ae80925ed907f431454243e2d47899a3e6fa9896e25f4`、
  `d5ebf200bacae7debfc0a20d3b431f108e75e54617e4d421d12bf998e18a420e`、
  `18604d6aa9bdfa6c192c9ba885b747ff51901e65fcd59269b82fdd7eb96a5aa5`、
  `12ebc3c33ba1556febf033a4cf9c497c8c26aa7913cfaac08b6290a35c65bcc2`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 下一窄增量是 source-derived effective-workset overlay：基于 published D/R/I batch 增加 D、显式
  替换 R、显式 supersede I，并重建 effective DAG；不得重写 frozen manifest/recovery-v6 bytes，
  也不把分类误报为 materialization 或 terminal closure。详细合同见
  `docs/ootang_source_ingest_cross_freeze_engineering.md`。

## 2026-08-29 source-ingest 派生 outcome key reservation v1（本增量）

- 新增独立 `ootang_epoch_source_ingest_derived_reservation.py` 与 profile
  `1.0.0-post-ingest-adoption`。authority 在 manager/cycle/replay/shadow 四锁下只读采用 frozen
  manifest 中唯一 `source_snapshot_ingested` parent 对应的 immutable `N -> N+1` source edge，
  仅写 `workset_recovery_v1/source_ingest_derived_reservation_v1`；不执行 source ingest、outcome
  materialization、live ledger action 或 recovery-v6 terminal receipt。
- 完整重放 source receipt registry 后，从唯一链内定位 predecessor 的 exact child；current tip
  可已推进到 `N+2+`，但公共 pointer 必须仍绑定唯一 current tip。采用的 `N+1` 则完全从 immutable
  receipt/pointer/semantic manifest/dataset/revision heads/feed objects 重建，所以 sidecar 在后续
  source 推进后仍可 byte-identical replay；mutable incoming feed 与 status 不构成 authority。
- derived selector 精确复用 frozen live projection 的 `revision_ids`、`outstanding_target_date`、
  `last_finalized_date` 与完整 frozen-ledger seal。令 `P1` 为 successor snapshot 的完整 prospective
  machine-selected 集、`K0` 为 frozen machine-selected 集，显式记录
  `D=P1-K0`、同 natural key 但 namespace/snapshot binding 改变的 `R`，以及不再 prospective 的
  `I=K0-P1`。初始 prior 只取 pending outcome receipt-chain tip，D/R 被禁止依赖 I；同 family 的
  `anchor_confirmation` repair item 不会被误当 outstanding selector。
- durable 顺序为 canonical content-addressed create-only `reservation -> append-only event`。
  object-only crash 只补 matching event；event/object branch、重复 child、partial diff、超 4096 个
  D/R/I rows 或超 4 MiB control bytes 均在 durable write 前 fail closed。status 仍只是 replaceable
  observation cache。
- focused tests `8/8`（2.575 s）；derived/inventory/manifest/recovery 核心回归 `78/78`
  （8.730 s）；live-source 回归 `32/32`（2.547 s）。Ruff format/check、Python compile、strict
  profile/upstream pin load 通过；两路独立最终只读复审均为 P0=0、P1=0。未运行训练或真实网络，
  未修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数或实验结论。
- 当前 implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `1d620fbe20d936a27f55d1e03204d87be3ff8d94a892b2f9c966c061e2fa59d2`、
  `916b72d8cf2726afcde289f58b5b8f9729c382bb93ee03bad3b6f8d34caadb18`、
  `72476bbe33c7fdd9ad252a5d7cfc78c0a07baa2d0ddb6aecd0c60883d80e4d0a`、
  `686576cd300bfa211750cc38c7e95d57f68c00e519416f0d069f0654450d8252`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量只证明一个已提交 source edge 的完整 outcome-key reservation。下一窄增量是独立
  cross-freeze source-ingest writer/adoption adapter：机器创建/采用 frozen parent 唯一允许的 source
  objects，并以 matching derived reservation event 作为 parent transition 后续解析条件；仍不得恢复
  legacy writer 或加入人工 freeze/approval。详细合同见
  `docs/ootang_source_ingest_derived_reservation_engineering.md`。

## 2026-08-29 frozen-manifest terminal coverage v1（本增量）

- 新增独立 `ootang_epoch_manifest_terminal_coverage.py` 与 profile
  `1.0.0-exact-frozen-key-bijection`。profile 直接固定未改动的 manifest、recovery v6、step
  dependency sidecar、settlement overlay 与 source-terminal aggregate 共十个
  implementation/profile SHA-256；assessor 在 manager/cycle/replay/shadow 四锁下只读深验，
  仅写 `workset_recovery_v1/manifest_terminal_coverage_v1`。
- 精确公式为 `K = T6 ⊎ TA`。`K` 从 durable manifest bytes 独立重建 canonical key id 与
  deterministic topological order；`T6` 只取 current recovery-v6 terminal receipt tip 及 matching
  recovery event；`TA` 只取具有 matching proof 的 published aggregate event。completed overlay
  slot、orphan/pending proof 不计，两个集合必须互斥且逐 key exact union 等于 `K`，不能只比数量。
- manifest/global/receipt/event/proof snapshot 全部从 durable bytes 重读；recovered ordered items
  必须精确等于 durable manifest 重建结果，aggregate state 也从 proof/event 目录独立重放。独立
  复审发现的两个 P1——内存 receipt 伪造 terminal、内存 `ordered_items` 缩小 key set——均已修复
  并增加回归；两路最终独立只读复审均为 P0=0、P1=0。
- 只有完整覆盖且上游无 pending/incomplete publication boundary 时才执行 deterministic
  content-addressed `proof -> singleton event`。event 前
  `frozen_manifest_key_coverage=false`；proof 后崩溃只复验并补 event，不发布替代 proof、不调用
  上游 action。合法缺 key 只写非权威 waiting status，不生成 partial proof；branch/orphan/multiple
  authority fail closed。
- 快测 `5/5`（0.168 s）；coverage/aggregate/overlay/sidecar/recovery/manifest 核心回归
  `78/78`（6.433 s）；相邻 materializer/live-ledger/CAS/epoch-gates/prequential/main 回归
  `235/235`（14.505 s）。Ruff format/check、Python compile、strict JSON/profile load、受保护上游
  diff 与 diff check 通过。未运行训练或真实网络，未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。
- 当前 coverage implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `5cb928ab7ee8d3ab15b6da8be29d9b597598b200744ce30d0ae01a8568fa15b2`、
  `4edfc6a9386452393197af827d325f0f877e60a32b6c7bd5ed9e37af250deecb`、
  `d304c4bbf9cca11fcd86b6e5b56f6c7a287d2c9c434c76cfbc07bee07317b4d4`、
  `f286481abbd54cb433356a181f8006b0a83b24f40f4a35b1873963947092e84f`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量只证明 frozen manifest key coverage；full/bounded workset、all-item settlement、
  all-successor、terminal/transitive closure、derived new-key、drained/lifecycle/activation 与
  trusted/E2/formal claims 继续为 false。下一窄增量是 source ingestion 产生的
  content-dependent keys 的 versioned create-only reservation authority；issue-route 与 shadow
  derived work 仍是后续独立边界。详细合同见
  `docs/ootang_manifest_terminal_coverage_engineering.md`。

## 2026-08-29 overlay-backed source-key terminal aggregate v1（本增量）

- 新增独立 `ootang_epoch_source_terminal_aggregate.py` 与 profile
  `1.0.0-overlay-backed-source-terminal`。profile 直接固定未改动的 recovery v6、step
  dependency sidecar v1 和 settlement overlay v1 implementation/profile SHA-256；assessor 在
  manager/cycle/replay/shadow 四锁下深验完整 authority chain，只写
  `workset_recovery_v1/source_terminal_aggregate_v1`。
- 只有已发布 completed overlay event 才能进入 aggregate。source 必须仍是 recovery 当前
  `anchor_result_recorded(candidate_confirmed)` tip，且
  `next_actions=[outcome_batch_settled]`、`terminal_for_key=false`；dependency 必须仍是当前
  terminal recovery tip。两端 receipt/event、transition plan、canonical step id/index、sidecar、
  overlay、effective dependency edge 及真实 43-event settlement action semantics 均须精确一致。
- aggregate 采用 `proof -> event` 独立 durable 协议。proof 是无时间字段的 canonical
  content-addressed JSON，只有 matching aggregate event 落盘后才是已发布终态 authority。
  proof 后崩溃时下一 poll 只深验并补 event，不重新选择 candidate、不调用 ensure，也不重跑
  settlement action。event-without-proof、non-prefix/skip/branch、多个 orphan proof 或同一 source
  多个 completed overlay slot 均 fail closed；`status.json` 不是 authority。
- proof 只在 `source_terminal_aggregate_v1` 范围声明
  `terminal_for_source_key=true`。原 recovery v6 receipt 继续明确
  `terminal_for_key=false`，且 recovery/sidecar/overlay/manifest/live ledger 均未修改。本增量不
  声称 full/bounded workset、all-item/all-successor、terminal/transitive closure、derived new-key、
  drained/lifecycle/activation、trusted/E2 或 formal warning。
- 快测 `3/3`（0.070 s），相邻 aggregate/overlay/sidecar/recovery/materializer/live-ledger/CAS/
  epoch-gates/prequential/main 回归 `230/230`（14.089 s）。Ruff format/check、Python compile、
  strict JSON/profile load、受保护上游 diff 与临时路径检查通过；两路独立只读复审均为
  P0=0、P1=0。未运行训练或真实网络，未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。
- 当前 aggregate implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `8d7b03a7480f3bf647f75694631031b3d11200462f4a4887979a982ba1e1a296`、
  `80779ecdb582d2dde53576668037597ac29bf56f486ac926a9754f103eb6604c`、
  `8441967ffb5451e39555c8cd47e6c36d968376144b3c2686a0c5b737562f0374`、
  `e94011778d922b64ed6fc45b2a54fcb07c3fa1155b1869da10a96fd577633ae5`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本历史条目规划的 manifest-coverage assessor 已由上方 frozen-manifest terminal coverage v1
  实现；source-ingest 产生的真正 content-dependent new key reservation 现在是下一独立
  authority。详细合同见
  `docs/ootang_source_terminal_aggregate_engineering.md`。

## 2026-08-29 cross-freeze settlement overlay dispatcher v1（本增量）

- 新增独立 `ootang_epoch_step_dependency_overlay.py` 与 profile
  `1.0.0-sidecar-authorized-settlement`。profile 直接固定未改动的 recovery v6 与 step
  dependency sidecar v1 implementation/profile SHA-256；dispatcher 在同一 manager/cycle/
  replay/shadow 四锁下深验两条既有 authority，只写
  `workset_recovery_v1/step_dependency_overlay_v1`。
- overlay 只消费具有完整 sidecar event 的 reservation，且已完成 overlay events 必须精确构成
  sidecar event chain 的前缀。orphan sidecar object 继续等待，不能跳槽、重排或重新选 candidate；
  每次 poll 最多处理一个 ready slot。
- 机器仅在内存复制 source manifest item，并添加 reservation 固定的唯一 outcome sibling
  natural key。随后复用 recovery v6 的只读 settlement adoption verifier，重放既有 43-event
  transaction；target/issue/seal/source revision/exact outcome/source id/terminal event 与 sidecar
  精确交叉校验。frozen manifest、recovery v6、sidecar 与 live ledger 均不写入。
- 独立 durable 顺序为 `intent -> receipt -> event`。intent 后崩溃会精确复用原 intent 并重验；
  receipt 后崩溃只做 receipt/event forward-adoption，不重复 settlement action。orphan、branch、
  non-prefix 或多个 pending 状态 fail closed。receipt 仅声明
  `terminal_for_overlay_slot=true`，不含 `terminal_for_key`，因此原 recovery v6 source key 仍非终态。
- 新增快测 `3/3`（0.048 s）；overlay + sidecar + recovery + manifest 定向回归 `70/70`
  （6.105 s），相邻 overlay/sidecar/recovery/materializer/live-ledger/CAS/epoch-gates/
  prequential/main 回归 `227/227`（13.853 s）。Ruff、format、compile、strict JSON/profile load、
  diff check 通过。两路独立只读复审最终均为 P0=0、P1=0；其中协议复审先发现 recovery v6
  两类 record type 的 optional source identity 二选一兼容问题，修复后已用真实形态复核通过。
  未运行训练或真实网络，未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。
- 当前 overlay implementation/profile/test、工程文档与受保护 `main.py` SHA-256 分别为
  `53932a5ebd095d98f08fe68aaa3891ba9569b51950da34bfbf662882d97fff53`、
  `4da333ef6233059aedb6ff1cd52bb6de31bae18aa14f1e571e97ee3018f52496`、
  `3a98046df5bf482dfd11506fbab2b527b8c587f71595029c71eec8f716add45c`、
  `be9df3a74d8b8308919a257f937c90d7b839aeb37487c53f8beeffcb21bcd59a`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量只证明一个有序 cross-freeze settlement dependency 的机器采用。full-workset、
  all-successor、原 recovery key terminality、terminal/transitive closure、derived new-key、
  drained/lifecycle/activation 与 trusted/E2/formal claims 继续为 false。本历史条目规划的
  aggregate assessor 已由上方 source-terminal aggregate v1 实现；source-ingest 新 key
  reservation 仍需单独实现。详细合同见
  `docs/ootang_step_dependency_overlay_engineering.md`。

## 2026-08-29 cross-freeze step dependency sidecar v1（本增量）

- 新增独立 `ootang_epoch_step_dependency_reservation.py` 与 profile
  `1.0.0-cross-freeze-manifest-sibling`。sidecar 固定并只读深验现有 recovery v6
  profile/implementation，在同一四锁下工作，不修改 frozen manifest、global intent、item
  intent、receipt 或 recovery event。
- 当前唯一规则是：freeze 后 `anchor_result_recorded(candidate_confirmed)` 选择
  `outcome_batch_settled` 时，若 frozen manifest 中存在同 old epoch/target/issue/seal、且已取得
  canonical outstanding-consumption terminal receipt 的唯一 outcome sibling，则机器发布一个
  versioned step dependency reservation。
- reservation 是 canonical JSON content-addressed create-only object；独立 append-only event 绑定
  slot、source/dependency key 与 object reference。object 已落盘但 event 未落盘时，下一 poll 只补
  event，不重新选择或改写 ledger。nonterminal sibling 等待，多 terminal sibling 歧义 fail closed。
- 本增量只建立 dependency authority，尚未让 recovery dispatcher 消费它。因此
  `derived_future_work_reservation_implemented`、`terminal_transition_closure_implemented`、full
  workset、drained/active/rotation/lifecycle 与 trusted/E2/formal claims 全部保持 false。
- 新增快测 `3/3`（0.024 s）；sidecar + recovery + manifest 定向回归 `67/67`
  （5.913 s），相邻 sidecar/recovery/materializer/live-ledger/CAS/epoch-gates/prequential/main
  回归 `224/224`（15.025 s）。Ruff、compile、strict JSON/profile load、diff check 通过；两路独立只读复审均为
  P0=0、P1=0。未运行训练/真实网络，未修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、
  模型参数或实验结论。
- 当前 implementation/profile/test/main.py SHA-256 分别为
  `c385b7c8783d86831561d5c1179b05efc78e3f5ef99a912b44382143625e5190`、
  `8b10a9642610b76911813d80ee8e31205c0e3c490aa05a13f0ba8287d457670e`、
  `250f82cdbf802d92193afd7d090f048de869f0af7658c5b1ae925a9249000796`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 下一窄增量是 versioned overlay dispatcher：消费本 sidecar authority 并把 terminal dependency
  receipt 绑定进 settlement step intent/contract；再后才处理 source ingest 派生的新 outcome keys。
  详细合同见 `docs/ootang_step_dependency_reservation_engineering.md`。

## 2026-08-29 first-backfill outcome 自动消费（本增量）

- recovery coordinator 新增独立
  `ootang_live_first_backfill_consumption_action_contract_v1`。分支只接受
  `selection_kind=backfill` 的 root outcome，要求 predecessor revision/outcome 均为空、target
  精确为 `last_finalized_date + 1 day`，且 current exact prefix 没有 outstanding target、issue、
  seal 或该 target 的既有 backfill/settled/revision/latest-actual authority。
- writer 复用 canonical `_append_backfill`，只追加一个 aggregate-state
  `backfill_not_blind` event。online states、settled mapping、anchored seals 与 blind counts 不变；
  `last_finalized_date` 推进到 target，backfill registry/count、revision registry、latest actual 与
  下一日 persistence baseline 由机器精确更新。
- fresh path 仅在 exact expected pre-head 上 CAS。CAS 已提交但 recovery receipt 未落盘时，
  下一 poll 从 persisted contract 重建单个 EventSpec，exact 匹配固定位置后仅补 receipt、不再
  调用 CAS；displaced、partial-authority 或 mismatched transaction fail closed。
- outstanding v2、settled revision v1 与 backfill revision v1 三类既有 schema 保持兼容。
  到此 materialized outcome 的 outstanding、settled revision、backfill revision 与 first
  backfill 四条 writer 已闭合，但 full workset、derived future reservation、terminal/transitive
  closure 与 lifecycle authority 仍保持 false。
- 定向 `2/2`（1.400 s）、完整 recovery `57/57`（5.606 s）、相邻
  recovery/materializer/live-ledger/CAS/epoch-gates/main `221/221`（13.756 s）均通过；
  Ruff format/check、Python compile、strict profile load 与 diff check 通过。两路独立只读
  复审均为 P0=0、P1=0；临时运行时另验证同一 frozen manifest 的 day1/day2 串行、
  consumed-at-freeze repair 后零 CAS adoption，以及合法 suffix/mutable pointer 无关的历史只读
  复验。未运行训练或真实网络，也未修改 ConvLSTM、v4、冻结 splits/metrics/thresholds、
  模型参数或实验结论。
- recovery module/profile/test、本增量工程文档与受保护 `main.py` SHA-256 分别为
  `b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51`、
  `5c50d168d389c286d0940a00884369ae8f65fc399f8726f0f64300999dd2de01`、
  `4227941e51697f21f5897da5f51c218ca4f1819e489cb852760178fe16036076`、
  `6679837dd26b6974789532c3423f65743e52d6e79210fa528120ef1893f03083`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 下一窄增量转向 cross-freeze derived work/step-level dependency reservation，再基于该
  versioned authority 推进 closure；在端到端证据完成前不声明 full workset、drained 或
  lifecycle。详细合同见 `docs/ootang_first_backfill_consumption_engineering.md`。

## 2026-08-29 backfill outcome revision 自动消费（本增量）

- recovery coordinator 现在对 `selection_kind=revision` 重放 current projection，并要求
  目标日期排他地属于 settled 或 backfill original。backfill 分支使用独立
  `ootang_live_backfill_revision_consumption_action_contract_v1`，不会改写既有
  outstanding v2 或 settled revision v1 persisted contract/receipt。
- writer 复用 canonical `_append_backfill_revision`，按八站固定顺序追加
  `outcome_revision x 8`，不生成 rescore。后置深验证明原 backfill、online states、
  last-finalized/outstanding、issue/seal 及 blind-settled/engineering-candidate/backfill counts
  不变；revision registry 与目标日期 latest actual 精确更新。
- 当目标正是最后 finalized 日期且当前无 outstanding 时，live projection 自动把
  `latest_displacement_mm` 更新为 revised actuals，作为下一日 persistence baseline；其他情况
  baseline 保持不变，不需要人工修正。
- fresh path 只在 exact expected pre-head 上 CAS。CAS 已提交但 recovery receipt 未落盘时，
  下一 poll 从 persisted contract 重建 8 个 EventSpecs，匹配 exact contiguous slice 后仅补
  receipt、不再调用 CAS；partial/displaced/mismatched slice fail closed。
- 定向 `2/2`（0.836 s）、完整 recovery `55/55`（4.715 s）、相邻
  recovery/materializer/live-ledger/CAS/epoch-gates/main `219/219`（12.984 s）均通过；
  Ruff format/check、Python compile、strict profile load 与 diff check 通过。两路独立只读
  复审均为 P0=0、P1=0；临时 consumed-at-freeze fixture 还验证了 pointer/inbox 从 rev1
  自动修复到 ledger 已消费的 rev2，并以零 CAS 精确采用。
- recovery module/profile/test、本增量工程文档与受保护 `main.py` SHA-256 分别为
  `e4b949664a7cb8cbb07936fa047bc157d6a648a2108adf19de93280a843afceb`、
  `9c9a1dc4404a51b5a5a13721729cdc3306395d2bd69564e13e2c39c5f125f116`、
  `ae558660ee058822d23573327f3139d7f673fbdf7234676d4d8aa24580466f7e`、
  `15b1352743c808dd472dfce7465f44a6e82ffdf8e675f3ea7c5e6d5a8ec46242`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量未运行训练或真实网络，也未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。first-backfill writer 仍未实现，下一窄增量
  即为 first-backfill；详细合同见
  `docs/ootang_backfill_revision_consumption_engineering.md`。

## 2026-08-29 settled-date outcome revision 自动消费（本增量）

- recovery coordinator 新增 `selection_kind=revision` 的 live-ledger 消费路径。它从
  terminal materialization receipt 深验 immutable materializer receipt、exact outcome 与
  source input manifest，再把 `previous_revision_id`/`previous_outcome_sha256` 精确绑定到
  ledger 该日期的 latest registered revision，不重选 mutable pointer/current source。
- writer 复用 live core canonical `_append_revision`，按八站点固定顺序追加
  `(outcome_revision, revision_rescore_recorded) x 8 = 16` 个 events。合同及后置复验
  证明原 settlement、online states、`last_finalized_date` 和
  `outstanding_target_date` 不变；rescore 只属于 revised retrospective view，
  `updates_live_state=false` 且 `blind_metric_eligible=false`。
- fresh path 仅在 exact expected pre-head 上执行 CAS。CAS 已成功但 recovery receipt 未落盘
  时，下一 poll 从 persisted contract 重建 16 个 EventSpecs，匹配 exact contiguous slice
  后只补 receipt、不再调用 CAS；partial/displaced/mismatched slice fail closed。终态
  revision receipt 在合法 live suffix 后仍按 immutable history 只读复验。
- revision 使用独立
  `ootang_live_settled_revision_consumption_action_contract_v1`；旧 outstanding
  `ootang_live_outcome_consumption_action_contract_v2` persisted contract/receipt 保持兼容。
  recovery profile 升为 `2.1.0-settled-revision-consumption`，仅新增已实现的
  `live_settled_revision_consumption_adapter_implemented=true`。
- 定向验证覆盖 rev1 outstanding 消费 → rev2 materialization → canonical 16-event
  revision 消费、post-CAS receipt adoption 与合法 live suffix 后的历史只读路径。
  定向 `2/2`（1.901 s）、完整 recovery `53/53`（4.734 s）、相邻
  recovery/materializer/live-ledger/CAS/epoch-gates/main `217/217`（12.694 s）均通过；
  Ruff format/check、Python compile、strict profile load 与 diff check 通过。两路独立
  只读复审均为 P0=0、P1=0，并额外验证 consumed-at-freeze revision 零事件采用、
  backfill fail-wait 与 outstanding v2 AST/contract 兼容。
- recovery module/profile/test、本增量工程文档与受保护 `main.py` SHA-256 分别为
  `99884ad28de5ab851da4e2401e67ec90d81258f014971c54711c1ba7376e492c`、
  `ef601148860ef5d4781e934f5cafb046eb82e0bac447c3d3f41ae05c19613c37`、
  `688b395b07f11b7892e2f67918cfda3e3a253532797c479de80cb44ce8c71da6`、
  `7e40c68270c7a78dc50bc18ad66532e7dc8c068418aefae4eb286197df293d76`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 本增量未运行训练或真实网络，也未修改 ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数或实验结论。backfill revision 与
  first-backfill writer 仍未实现；下一窄增量是 backfill revision consumption，
  详细合同见 `docs/ootang_settled_revision_consumption_engineering.md`。

## 2026-08-29 outcome materialization 与 outstanding 自动消费桥（本增量）

- recovery coordinator 现在支持两类 manifest-bound `outcome_materialized`：未完整发布的
  `outcome_receipt_chain` tip 做发布修复，`machine_selected_source_outcome` 做新的
  canonical materialization。输入绑定 source snapshot/record、old epoch/seal、selection 及
  predecessor authority；物化动作不追加 live-ledger event、不调用网络。
- 物化 step receipt 现在可以在下一次 poll 作为唯一 immutable 前驱，从其精确
  materializer receipt、exact outcome 和 input manifest 进入已有 outstanding 43-event canonical
  writer。真实回归已验证“machine-selected outstanding 物化 → 下一 poll 消费”，不依赖
  mutable pointer/current source 重选 actual。
- commit-before-recovery-receipt 窗口已按 immutable chain 分类：预期 current tip 才允许
  reconcile pointer/inbox；若合法新 revision 已把它变成 historical predecessor，则只采用原
  receipt/exact/input，不回退 current publication。冻结前已消费的 tip 显式使用
  `preexisting_consumed_adoption`，采用既有 43-event slice 且 CAS 零新增。
- inventory 和 recovery 同时绑定 `previous_revision_id`/
  `previous_outcome_sha256`。同日存在 pending rev1 tip 时，current source rev2 会被正确冻结
  为依赖 rev1 的 `revision`，不再被误分为 outstanding/backfill。真实链路测试已通过
  “rev1 43-event 消费 → rev2 物化为 receipt sequence 2”。
- manifest profile 升为 `1.3.0-revision-predecessor-authority`，recovery profile 升为
  `2.0.0-outcome-materialization-chain`。本轮不声称 revision/backfill/first-backfill live writer、
  full workset、all successors、derived future reservation、terminal closure、drained/active/rotation/
  lifecycle/trusted/E2/formal authority。
- inventory+recovery focused `58/58`（3.71 s）通过；含 outcome materializer 及既定
  recovery、live-ledger/CAS、inventory/manifest、admission、eligibility、drain 与 main 相邻回归
  `192/192`（11.06 s）通过。Ruff check/format、compile、strict profile load 和 diff check
  均通过，独立最终只读复审 P0/P1=0。未运行训练、真实网络、长并发/容量或
  无关边界矩阵；ConvLSTM、v4、冻结 splits/metrics/thresholds、模型参数和实验结论均未修改。
- inventory/manifest/recovery module、manifest/recovery profile、两组直接测试、本增量工程文档
  与 `main.py` SHA-256 分别为
  `2b56de3f36da3a08d34bf3a9509b0492c5d989cd1f391491c0574bed2723bd52`、
  `0ca331c6827cd896b4c3261792eed5f933e104d4c40c4fbf3b7e416e9b1fd424`、
  `6b7cf96e376293ee6b06501f363a3b9e84844e0671084e2a8269bb6c5307bc7a`、
  `857ae1ff031289d51c0a2947beeb2e47ceb9d48a3769db707c8f7f75750d48dc`、
  `243d43b2b9444d0daaa217eac2695d3754686249777eaa0dadd11dd31ea735a2`、
  `cf542fa0ca5901f9eb93448fc5581b983bd057750b371d6126bfc20e72f3e844`、
  `2db4f4eef07fe5234c843faac7a0fb7c56790b0dae98b3b775b0c8d3b2d2ab9d`、
  `8a53cf5eb33c669d5a427f9e7fdb73bebcbcadc1382dda54ca40e911a6bb5855`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。详细合同见
  `docs/ootang_outcome_materialization_recovery_engineering.md`。
- 该历史条目规划的 settled-date revision consumption 已在上述后续增量实现。
  当前下一窄增量是 backfill revision consumption，随后单独实现
  first-backfill writer。跨 freeze 的 derived-work/step-level dependency reservation 继续作为
  更高层 closure 问题处理。

## 2026-08-28 published outstanding outcome 自动消费（本增量）

- recovery coordinator 新增 manifest-bound
  `outcome_revision -> outcome_or_revision_consumed` fresh writer，目前只支持
  `selection_kind=outstanding`。它从 immutable materializer receipt、exact outcome object、source
  manifest 与 frozen item authority 重建输入，复用冻结 live canonical writer 生成完整 43-event
  EventSpecs；不人工 freeze/cleanup/approval/force/backdate，也不改动模型或科研门禁。
- fresh path 先验证 frozen receipt chain 与 current publication 仍指向同一 predecessor，再以
  expected-pre-head CAS 追加 exact `1 opened + 8 revealed + 32 score/expert/conformal/drift + 1 site +
  1 settled` transaction。CAS 已提交但 receipt 未落盘时，机器只按持久化 contract 和 immutable
  EventSpecs 采用 exact slice；不重新读取 mutable current pointer/inbox，也不再次调用 ledger CAS。
- pending transaction 被分为 absent、exact-complete、partial/displaced 三态：absent 才允许 fresh
  CAS，exact-complete 只补终态 receipt，partial/displaced fail closed。completed receipt 的历史深验是
  纯 immutable/read-only 路径，因此后续合法 revision 移动 current publication 或追加 ledger suffix
  不会令旧 receipt 失效，也不会重写 43 个事件。
- inventory 补上真实 dependency edge：frozen manifest 中已经 confirmed 的 live settlement item 会
  唯一依赖同日期、同 source revision 的 outcome item，并优先采用 receipt-chain 提供的精确依赖。
  但 manifest freeze 之后才确认的 live item 无法倒改 immutable DAG；该分支继续机器等待，不虚报
  terminal/transitive closure。未来需要 versioned derived-work/step-level dependency reservation。
- recovery profile 升为 `1.8.0-outstanding-outcome-consumption`，manifest profile 升为
  `1.2.0-outcome-settlement-dependency`。`revision`、`backfill`、first-backfill 与
  `outcome_materialized` 仍未实现；full workset、all successors、derived future reservation、terminal
  closure、drained/active/rotation/lifecycle/trusted/E2/formal claims 均保持 false。
- focused recovery 测试 `46/46`（2.03 s）；既定 recovery、live-ledger/CAS、inventory/manifest、
  admission-cut、eligibility、drain-v2 与 main 相邻回归 `154/154`（8.52 s）通过。独立最终复审
  P0/P1=0。未运行训练、真实网络、长并发/容量或无关边界矩阵；ConvLSTM、v4、冻结
  splits/metrics/thresholds、模型参数和实验结论均未修改。详细合同见
  `docs/ootang_outstanding_outcome_consumption_engineering.md`。
- inventory/manifest/recovery module、manifest/recovery profile 与两组测试 SHA-256 分别为
  `64e7feaf295689e444803fd20eda2d3a754b2ec75932e85987fcba603281bb01`、
  `5284018ade9160a80b78487470d3457a059490fb9062d965fc59048c7acb9c2d`、
  `ee31538f5f089b7c49a62e342c32243ba66fdb7bc3eec5efe0dc108795a84340`、
  `338b8e4c90bf1bf241a255148c4352b3a9fa197658dd08d1dd745ada604dd39e`、
  `3983d790b23d8d4730bddde96070cac16717c29abe35e1e85abe4b5828893629`、
  `397483a942517d5af2579cdd590ba1293b207a96b8761fbd9f66f964a3fc2f18`、
  `69751bfcfa17d892c0087d0aa70a7485b829661a40f2c8f5b36b5b049a9cc6c4`。本增量未触及
  protected ML paths；`main.py` 保持
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`。
- 下一窄增量先实现 `outcome_materialized -> outcome_or_revision_consumed`，继续复用同一 immutable
  authority/CAS/三态 crash-forward 框架；随后再分别处理 revision/backfill writer。跨 freeze 的
  derived dependency reservation 作为更高层 closure 工作单独推进。

## 2026-08-28 manifest-reserved outcome settlement 终态采用（本增量）

- 新增 `live_outstanding -> outcome_batch_settled` 的 receipt-only machine adapter。它不读取
  mutable outcome inbox/current source，不生成 actual，不追加 live event；只在 append-only ledger
  已存在完整 transaction 时终态化该 live key。transaction 缺失时在 settlement item intent 前
  machine waiting，ledger/receipt/event 零新增，无人工 freeze/cleanup/approval/force/backdate。
- live item 必须显式依赖同一 frozen manifest 中 target/old epoch/source revision 匹配的唯一
  `outcome_revision` sibling；既有 terminal dependency gate 必须先深验该 sibling 的
  `outcome_or_revision_consumed` receipt。manifest 后才 confirmed 且未预留 outcome dependency 的
  分支不动态改 DAG，只保持 `waiting_for_supported_ready_key`。
- confirmation 可来自 frozen prefix 内的 exact `anchor_confirmed_event`，或前一张
  candidate-confirmed result receipt。它只提供 anchor authority，不被假定为 settlement pre-head。
  adapter 从 matching settlement 反向取得 43-event batch 的真实前驱，因此允许 canonical poll 在
  confirmation 后先追加较早日期的合法 revision。
- 完整 transaction 必须连续满足 `1 opened + 8 revealed + 8×4 score/expert/conformal/drift +
  1 site + 1 settled = 43`。batch 前 projection 与 settled projection 均由 frozen live core 重放；
  target/issue/seal、outcome/source/revision/input-manifest、state hashes、ordered entry digest 与
  first/terminal event 全部进入 create-only contract/receipt。partial/foreign/mismatched suffix fail
  closed。trusted anchor、E2、network 与 formal warning 保持 false。
- profile 升为 `1.7.0-outcome-settlement-adoption`，仅新增真实 capability
  `live_outcome_settlement_adoption_implemented=true`。full workset、all successors、derived future
  reservation、terminal closure、drained/active/rotation/lifecycle/trusted/E2/formal claims 仍为 false。
- focused recovery 测试 `41/41`（约 0.76 s）；既定 recovery、live-ledger/CAS、inventory/manifest、
  admission-cut、eligibility、drain-v2 与 main 相邻回归 `148/148`（约 8.13 s）通过。真实八站测试
  覆盖 confirmation 与 43-event batch 之间 8 条 canonical outcome revision。未运行训练、真实
  网络、长并发/容量或无关边界矩阵；ConvLSTM、v4、冻结 splits/metrics/thresholds 与科研结论未改。
  初次独立复审的 2 个 P1（缺 reserved outcome authority、错误强制 confirmation 为 pre-head）均已
  修复；最终 P0/P1=0。详细合同见
  `docs/ootang_outcome_settlement_adoption_engineering.md`。
- recovery module/profile/test、`main.py` 与本增量工程文档 SHA-256 分别为
  `89ada0819ed1d81cafe2e854cc86c54f3c57bbe2ef3a36a170327d5fcc7291d5`、
  `b114f8bdc646961a97808ce370db38727ef30f572a0a7262d06b31ed718343be`、
  `2dc8200396310995d9011d881bd35ab2a662fec3d8361f951e27184a8db6c499`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`、
  `a1ec3784e3d3d10acec03c76bc03ae51b4d87d3adc8617c1d362404508d8b269`；97-path
  protected aggregate 保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 下一窄增量是 manifest-bound `outcome_revision -> outcome_or_revision_consumed` fresh writer：只
  消费 immutable materializer receipt/exact outcome/source manifest，复用 frozen canonical writer
  构造完整 EventSpecs，并用 expected-pre-head CAS append/adopt。`outcome_materialized` 与未来版本
  derived-work reservation 继续分开处理。

## 2026-08-28 anchor-result result CAS、持久化 fence 与自动 retry（本增量）

- 基于已提交的 `507b5a5 feat: observe anchor responses`，本增量只消费 linked response
  observation。消费重新取得 `manager → cycle → replay → shadow` 四锁，不读取 token、不调用
  transport；在 ledger mutation 前非阻塞取得 dispatch lock，按 object→link exact re-adopt/fsync 后
  立即释放。publisher busy 时机器等待且 ledger/receipt/event 零新增；空 per-step object 目录作为
  mkdir crash residue 忽略；object-only 接管也先 durable re-adopt object 再补 link，避免二次断电形成
  orphan link。既有 orphan/branched/unknown artifacts 仍 fail closed。
- 机器从 frozen item intent 重放 exact request event/body、seal 与 expected result pre-head。
  `candidate_confirmed` 唯一构造 `anchor_confirmed`，`deterministic_failure` 唯一构造 frozen-shape
  `anchor_failed`；后者只把受控 `AnchorResultProtocolFailure` 写入 `error_type`，stage/code 留在
  recovery semantics。两类 EventSpec 均通过 recovery-only expected-pre-head CAS append/adopt。
- fresh CAS 只接受 request 仍为 terminal head；commit-before-receipt crash 只采用 request 后固定位置
  的 exact result，并允许其后合法 suffix。foreign head/position/content/chain fail closed 且零新增。
  receipt-before-recovery-event crash 只补 event；永久 observation 可同时作为历史 result receipt
  evidence，不再被误判为 pending 分支。
- result `ActionOutput` 不绑定瞬时 created/adopted，`action_output=null`；semantics 绑定 exact
  observation object/link、request/result identity 与 EventSpec digest。receipt 按已验证 outcome
  收窄为 confirmed→`["outcome_batch_settled"]`、failure→`["anchor_request_recorded"]`，历史 loader
  用同一纯函数重算。`network_action_performed=false` 属于本次消费；remote exactly-once、trusted/E2
  仍为 false。
- failure branch 已机器自动闭合到下一次 result intent：前一张 result receipt 必须绑定 exact
  `anchor_failed` event，retry request 以该 event 为 expected pre-head，只生成 `attempt + 1`；随后
  result contract 又逐字段绑定 request receipt/event。合法历史 suffix 可重放，但不能丢失前驱，且
  不会回退采用第一次 request。全程无人工 freeze/cleanup/approval/force/backdate。
- profile 升为 `1.6.0-anchor-result-ledger-adapter`，intent/item-intent/receipt/status authority 升为
  v6，可声明 `live_anchor_result_adapter_implemented=true`；full workset/network recovery、all branch/
  terminal closure、drained/active/trusted/E2/formal claims 继续为 false。
- focused recovery 测试 `36/36`（约 0.45 s）；recovery、live-ledger/CAS、inventory/manifest、
  admission-cut、eligibility、drain-v2 与 main 相邻回归 `143/143`（约 7.67 s）通过。未运行训练、
  真实网络、长并发/容量或无关边界矩阵；ConvLSTM、v4、冻结 splits/metrics/thresholds 和实验结论
  未改。下一窄增量是 confirmed→`outcome_batch_settled` adapter；详细合同见
  `docs/ootang_anchor_result_ledger_adapter_engineering.md`。
- recovery module/profile/test 与 `main.py` SHA-256 分别为
  `51aa8c0eda8b4561a5873fceb3a36570c8e79e6eda3d33761b947153f00bb008`、
  `3157abe52b5357b565366e2a3026a53b087e40a19f01615ff274b9e3e68074da`、
  `407e10e9aa5a951305dd35a6074ddc463a80f10c54f5f08a1885b29921df92fc`、
  `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`；97-path protected
  aggregate 保持 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。最终独立只读复审
  P0/P1=0；唯一 P2 是永久测试止于 attempt-2 result plan，未重复已覆盖的第二轮 response CAS，按
  本轮“避免无必要边界矩阵”的范围保留。

## 2026-08-28 四锁外 anchor-result response observation（本增量）

- 在已提交的 `e9120d9 feat: prepare anchor result requests` 上继续窄化
  `anchor_result_recorded`。四重 coordinator 锁内现在只选择/复验唯一 pending intent 并返回
  `AnchorResultDispatchPlan`；Python `finally` 释放
  `manager → cycle → replay → shadow` 后，public coordinator 才取得独立
  `external_anchor_dispatch.lock` 并执行 bounded HTTPS。DNS/TLS/socket/read deadline 不占用四锁，
  也没有人工 freeze、cleanup、approval、force 或 backdate 分支。
- transport 严格复用 intent 冻结的 normalized endpoint、canonical POST body、1 MiB response
  上限、timeout 与 stable `Idempotency-Key`；固定 no-redirect、JSON/identity headers 和总
  `SIGALRM` deadline。token 仅在 dispatch 时从冻结的环境变量名读取到内存，缺失/非法则机器
  waiting 且零网络；token value 不进入 intent、hash、status、object、link 或 CLI 输出。
- `408/425/429/5xx` 与 URL/timeout/OSError 属于 retryable/ambiguous：不落 response artifact，
  下次仍用同一 key。完整确定性 response 则分类为 `candidate_confirmed` 或受控
  `deterministic_failure`，并依次 create-only 发布
  `external_anchor_response_objects/<step>/<sha256>.json` 与
  `external_anchor_response_links/<step>.json`。object-before-link crash 会由后续 poll 深验并零网络
  补 link；既有 exact link 也零网络等待 result adapter。
- observation 深度绑定 profile、item intent、request event/body/endpoint/idempotency identity、原始
  bounded response bytes 与归一化 candidate/failure；明确记录
  `remote_exactly_once=false`、`trusted_anchor_receipt_verified=false`、
  `live_ledger_result_recorded=false`、`recovery_receipt_created=false`。本增量不追加
  `anchor_failed`/`anchor_confirmed`，不创建 recovery receipt/event，不修改 live ledger。
- profile/schema authority 升为 v5，新增真实 capability
  `live_anchor_result_response_observation_implemented=true`。`network_action_performed` 改为每次 poll
  的 occurrence 字段，不再作为静态 capability；完整 network recovery、result adapter、terminal/
  derived-work closure、drained/active/trusted/E2/formal claims 仍为 false。
- focused fake-transport recovery 测试 `30/30`（约 0.18 s）；recovery、live-ledger/CAS、inventory、
  manifest、admission-cut、eligibility、drain-v2 与 main 相邻回归 `137/137`（约 7.50 s）通过。
  没有真实网络、训练、长时间并发/容量或科研边界重跑；ConvLSTM、v4 默认链、冻结 splits、metrics、
  thresholds、模型参数与实验结论均未修改。
- recovery module/profile/test 与 `main.py` SHA-256 分别为
  `e7fe4011c2f00bbf09d22c3e451db66e3aa62ab1333ebb8b92b8ccd7ba1f634b`、
  `2fd37e48a5b3eeb8a321b559f9a4e162f0abb9de32f5e930bff9956b7e488177`、
  `dc68b54ab8b99813704903a3d81ae39d7bb33f692ce87a66895c09d2a66d31a0`、
  `4da6b9f69069c6ef980e927961d557951ab4fd4fae3e0992e0d59c8e238f9a4d`；97-path protected
  aggregate 仍为 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
  独立只读审查发现的 2 个 P1/2 个 P2 已全部修正，最终 P0/P1=0。
- 下一步只消费既有 response observation：重新取得四锁，复验 expected result pre-head，构造唯一
  `anchor_confirmed`/`anchor_failed` EventSpec，通过 recovery-only CAS append/adopt，再发布
  branch-selected recovery receipt/event。provider 未证明相同 key 的幂等 POST/query 前，继续不声称
  remote exactly-once。详细合同见
  `docs/ootang_anchor_result_response_observation_engineering.md`。

## 2026-08-28 anchor-result request intent 与全局 pending fence（已提交 `e9120d9`）

- 在已提交的 `51696db feat: recover anchor request events` 上继续窄化
  `anchor_request_recorded -> anchor_result_recorded`。本增量只在四重锁内创建
  create-only result item intent；不发 HTTP/TSA 请求，不写 response observation，不追加
  `anchor_failed`/`anchor_confirmed`，也不创建 result receipt/event。
- result contract 不再依赖 inventory 的 aggregate request/result 计数猜测配对。它只接受
  manifest frozen tip 本身，或前一步 recovery request 紧邻产生的唯一 canonical
  `anchor_requested` terminal event；冻结 event key/type/sequence/predecessor/entry、完整 POST
  body 与 digest、expected result pre-head、normalized exact HTTPS endpoint、timeout/response
  上限及由 request identity 派生的 stable idempotency key。
- endpoint 缺失、非法或超出冻结 allowlist 时返回
  `waiting_for_external_anchor_endpoint`，且不创建 immutable intent。token value 永不进入
  intent/status/hash/log；后续只允许 transport 在 dispatch 时读取环境变量。
- 已创建但无 receipt 的 result intent 是全局排他 fence。后续 poll 只深度复验并返回
  `waiting_for_external_anchor_dispatch`，零网络、零 ledger mutation、零其他 key 推进，避免
  在当前 manager→cycle→replay→shadow 四锁内执行长网络 I/O。
- profile/schema authority 升为 v4；新增真实 capability
  `live_anchor_result_request_intent_implemented=true`，同时保持
  `live_anchor_result_adapter_implemented=false`、`network_recovery_implemented=false`、
  `network_action_performed=false`。full/terminal/derived-work/drained/active/trusted/E2/formal
  claims 仍为 false。
- ConvLSTM、v4 默认链、冻结 splits、metrics、thresholds、模型参数和实验结论均未修改；
  本增量只触及 recovery 控制面。定向 recovery 测试 `23/23`（约 0.17 s），相邻 7 模块
  `117/117`（约 1.8 s），Ruff check/format、compile、strict profile load 与 diff check 通过。
- 下一步是四锁外的 bounded HTTPS transport 和 create-only、content-addressed response
  observation；其后才重新取得四锁做 expected-pre-head result CAS/adoption 与 branch-selected
  receipt（failed→新 request，confirmed→outcome settlement）。provider 未证明支持相同 key
  幂等 POST/query 前，不声称 external exactly-once。详细合同见
  `docs/ootang_anchor_result_request_intent_engineering.md`。

## 2026-08-28 single-event anchor request recovery（已提交 `51696db`）

- 基于已提交并 push 的 `232d4ca feat: add live ledger head cas`，
  `ootang-epoch-workset-recovery` 新增唯一一个 ledger mutation adapter：
  `live_outstanding -> anchor_request_recorded`。它只追加单个 `anchor_requested` event，
  不访问 TSA/HTTP endpoint，不生成 response/receipt，不增加人工 freeze、cleanup、
  approval、force 或 backdate 路径。
- 机器完整验证 current live chain 后，按 manifest 的 old epoch、event count 和 terminal
  hash 切出 frozen prefix；current chain 可有合法 suffix，但 frozen tip 在原位置的
  sequence/hash 必须不变。seal、attempt、event key 和完整 canonical EventSpec 只从该
  prefix 重建，禁止按 current head 重算 `attempt + 1`。
- create-only step intent 在 mutation 前绑定 versioned action contract：expected pre-head、
  attempt、完整 EventSpec 与 digest。CAS fresh path 只接受 exact frozen head；崩溃接管
  只采用 `frozen_event_count + 1` 位置的同一 event，并允许其后有可重放 suffix。
  SQLite busy/locked 是 transient machine busy；head/position/content/schema/chain conflict 则 fail
  closed，不自动换 head 或 attempt。
- ledger event 的 ActionOutput 使用 `reference=null`；receipt semantics 绑定 event identity、
  pre/entry hash、seal、attempt 和 EventSpec digest。它不绑定 CAS 瞬时 `created/adopted`，因此
  commit-before-receipt 后的 exact adoption 产生同一 authority。历史 receipt verifier 只重放
  frozen contract 和持久 event，不重跑已过时的“尚无 result”前置条件。
- 当前可声称 `live_anchor_request_adapter_implemented=true`、
  `ledger_mutation_recovery_implemented=true` 和
  `live_ledger_expected_pre_head_cas_implemented=true`。这只是一个受审单事件 adapter
  的实现范围，不代表全 ledger transition 或全 reserved key 已恢复。静态 occurrence
  claim `live_ledger_mutated=false` 已移除，真实持久效果由
  `action_semantics.live_ledger_event_recorded=true` 表达。
- intent/receipt/status authority 因必要语义变更升为 v3，但 runtime namespace 仍为
  `workset_recovery_v1`。若该 namespace 已有 v2 immutable authority，新 profile 必须 fail
  closed；不就地迁移、覆写或重解释 v2 bytes。
- 该 receipt 为非 terminal，只能转向尚未实现的 `anchor_result_recorded`，不解锁
  dependent key。full recovery、all-transition/derived-work closure、network/shadow mutation、
  drained/active/rotation/trusted/E2/formal 仍为 false。下一步是为
  `anchor_result_recorded` 设计外部 request intent、释锁 fence、幂等 response adoption 与
  cryptographic/result validation；在此之前机器稳定 waiting，不转人工操作。
- 本增量未修改 ConvLSTM、v4、冻结 splits、metrics、thresholds、训练结论或
  frozen live writer/ledger bytes；默认链仍为
  `features -> convlstm -> ootang-operational-v4`。recovery profile/module/test SHA-256 为
  `beb5ff9c3e34f60451ee933bfd3dbcc3dcb5d398f575a24cfbd0ea811ac4a3f2`、
  `3f7ccfd8bcfd85046b91ca4a210b9511ee8718e504eda939c2fcef63a26ce566`、
  `6e6bc2ca87da55f4ddf923dacc9239618f469bda4bff992d656564c14898a507`；CAS module/test 为
  `b443da5fd92eb2e48e33918c3e6090be0e53fe2182584f6bb0ccee050dfb327e`、
  `e040528d021e15d2b4e5dd5f41ce75a1a0027b8b3db244d5fce91106b501931d`。精准测试
  `26/26`（0.178 s），相邻回归 `101/101`（1.098 s）；Ruff/format、strict JSON
  `35/35`、35-stage list、默认/显式 recovery dry-run 通过；97-path aggregate 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，frozen writer
  `11/11`，独立审计 P0/P1=0。真实 live fixture 额外验证 `attempt=2`、只新增一条
  `anchor_requested` 且科学链重放成功。详细合同见
  `docs/ootang_anchor_request_recovery_engineering.md`。

## 2026-08-28 live-ledger expected-pre-head CAS v1（已提交 `232d4ca` 的历史记录）

- 当前 committed baseline 是 `7b728a1 fix: gate recovery on terminal steps`。新增 recovery-only
  `code/monitoring/ootang_live_ledger_cas_v1.py`；旧 `ootang_live_ledger.py`、live writer、ledger
  schema/epoch identity 与 frozen implementation bytes 均未修改。
- CAS 在同一 SQLite `BEGIN IMMEDIATE` 内验证 schema、genesis epoch、完整 hash chain 和 frozen
  `event_count/sequence/hash` 位置。fresh path 只允许 current terminal head 精确等于 expected
  pre-head 时连续 append；crash-forward path 只采用 expected pre-head 后内容、顺序、位置和
  predecessor 全部一致的 exact contiguous event slice。partial/changed/stale/wrong-epoch 或 chain
  drift 均 rollback、零新增行。
- exact slice 之后允许存在完整合法 suffix，但这只是 storage-level event identity/position
  adoption。未来 adapter 必须另行重放 manifest、transition plan、锁和 suffix transition
  authority；CAS 成功不能直接生成 terminal receipt，也不能重算到 current head。
- 在该已提交 CAS 增量中，recovery profile 只新增
  `live_ledger_expected_pre_head_cas_implemented=true`；在该历史时点真实 adapter 尚未调用该原语，
  `ledger_mutation_recovery_implemented=false`、`live_ledger_mutated=false`，full recovery、
  all-transition、drained/active/lifecycle/trusted/E2/formal 仍为 false。无网络、真实 runtime
  mutation、人工 freeze/cleanup/approval/force/backdate；shadow CAS 暂缓。
- CAS module SHA-256 为
  `23ca29356ef23483a0846e701376850745400082e607a16e2479c1057b6befa4`，recovery profile SHA-256
  为 `246dbf18bbc24cdecb7c85d289cdf4edd069fe5e47cbcbbbfb9e27e438a4ee04`。CAS 独立测试
  6/6；CAS + frozen live ledger + recovery + manifest + admission-cut + drain-v2 + main 聚焦测试
  93/93（unittest 1.018 s）；Ruff/format/compile、strict JSON 34/34、35-stage list 与默认/显式
  dry-run 均通过；97-path aggregate 保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，11 个 frozen writer
  哈希一致，独立 CAS 审计 P0/P1 为 0。历史 transition-chain 75/75 结果保留在下节，不由本增量
  覆盖。详细合同见 `docs/ootang_live_ledger_cas_v1_engineering.md`。
- 本节当时规划的单事件 machine-only `anchor_request_recorded` adapter 已由上节实现；
  本节其余 capability 与测试数字仅记录已提交 CAS 增量的历史边界。

## 2026-08-28 transition-plan step-chain correction R2b-2b-2c（已提交 `7b728a1`）

- 当前 committed baseline 是 `8f6a9f7 feat: add keyed local recovery`。本增量只修正其
  completion 语义：R2b-2b-2b manifest 完整覆盖单次 frozen observation 下可见的六 family，
  并保存每个 natural key 的 transition seed；它不枚举 terminal/transitive closure，也不预留
  transition 之后才派生的 future work。
- `ootang-epoch-workset-recovery` 的 global intent 现绑定 item-specific transition plan，包括
  initial action、allowed edges、terminal actions、mutation lane、冻结 read-set digest 与
  derived-work closure 状态。每一步单独使用 create-only step intent/receipt/hash-linked event，
  并绑定前一步 receipt；只有 `terminal_for_key=true` 的 receipt 能满足其他 key 的 dependency。
  非终态 receipt 只能推进同一 key，不能把“一次 successor 完成”误报成“整 key 完成”。
- 三个既有本地 adapter 不变：同 nonce/imprint 的 RFC 3161 DER repair、由唯一 frozen
  seal+confirmation 重建 anchor receipt、以及只有 durable superseding evidence 时才成立的 guard
  disposition。DER repair 明确是非终态，后续必须等待机器
  `trusted_time_response_link_recorded` adapter，再继续到
  `trusted_time_receipt_verified`；纯时钟越界仍不能冒充 backfill。
- 会派生未预留 live/outcome/shadow work 的 transition 均设置为 closure unresolved，不能产生
  terminal receipt，也不能被 assessor 计为 complete。full recovery、terminal closure、
  derived-future-work reservation、all-transition support、network recovery、ledger mutation、
  lifecycle、drained/active/trusted/E2/formal 继续为 false；无人工冻结、cleanup、批准、force、
  backdate 或网络动作。
- 本增量未改 ConvLSTM、v4、冻结 splits、metrics、thresholds 或 11 个旧
  writer/orchestrator。manifest profile/module/test SHA-256 为
  `ca3f24c91492d4cbd415331c1abf1e90bd2b971aac8016e69183652e3fb850dc`、
  `8868075715188effe708fe353bf18cbefdee9060abd90f92ede8120cf5313ef2`、
  `6155d557055d6492287b89d269680ad6910f1f4afe4a4cf927be3e6f23320442`；recovery 三值为
  `2b956d96d3aa3049a7901e21a250743aaa90701e41a814fe98e5c91936fdcf5a`、
  `d2492750062ce0151f05856fbff4af9ca53b357279a3141679e90cc75af18911`、
  `caa2b61943227046e0359a11a8cd6fb9d54c59bab69c5103a4af675db34e30cb`。
  recovery/manifest/admission-cut/drain-v2/main 定向测试 75/75（1.015 s）；
  Ruff/format/compile、strict JSON、diff、stage list 与默认/显式 dry-run 均通过；97-path
  aggregate 仍为 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，
  11 个 frozen writer 哈希一致，独立复审无剩余 P0/P1。`8f6a9f7` 的 76/76 只属于
  committed baseline。详细合同见
  `docs/ootang_epoch_workset_recovery_engineering.md` 与
  `docs/ootang_epoch_workset_manifest_engineering.md`。
- 本节当时规划的 expected-pre-head CAS 与后续 `anchor_request_recorded` 接入现均已完成；
  当前下一步是 `anchor_result_recorded`，仍不并行扩张到 issue/outcome/shadow adapter、
  完整网络恢复或 drain assessor。

## 2026-08-27 closed-workset manifest reservation R2b-2b-2b

- 基于已提交 `9d0c350 feat: add writer lock admission cut`，新增显式、非默认阶段
  `ootang-epoch-workset-manifest`。当前共 34 个可选阶段；默认链仍严格为
  `features → convlstm → ootang-operational-v4`，mutable 输出仅为
  `runtime/ootang_epoch_registry_v1/workset_manifest_v1/status.json`。
- 机器完整重放 admission-cut 的 prepare/intent/attempt/event 与两个 physical sentinel，从
  terminal attempt 恢复 R1/R2a、candidate/slot、old epoch 和 frozen live upper tip。由于
  deploy/runner official lock pathname 已封闭，本阶段不调用其旧 public poll，只按
  `manager → cycle → replay → shadow` 获取仍开放的四锁。
- 内容寻址 manifest 固定包含 issue/replay、live outstanding、source+outcome revision、guard、
  trusted-time 与 shadow 六族 descriptor；每项保存 exact natural key、allowed successor、
  dependency keys、contained path/hash/size refs 和 namespace digest。unknown/orphan/duplicate/
  branch/overflow、依赖不闭合或引用不一致时整体 fail closed，不发布部分清单。
- inventory 只读重放 terminal+pending namespace、live/shadow logical chain、shared-object/TSR
  reachability、anchor 与逐 live-event shadow coverage。request-only crash 会保留相同 nonce/imprint
  的 DER repair；target 已到或 outcome 已到而尚未 open/seal 的 guard intent 自动进入
  `superseded_by_backfill`，不需要人工 cleanup。
- create-only singleton event 精确绑定 manifest、cut event、terminal attempt 与 frozen context，
  并记录 publisher path/hash/size；同状态 repoll 字节幂等。历史 authority replay 不重新读取
  mutable predecessor，未来 adapter 按 exact key 单独做 predecessor CAS，因此第一项 recovery
  不会使后续 reservation 失效。waiting/blocked status 的当前完成标志为 false，mutable status
  不是 authority。
- 本切片不执行 recovery，不生成 outcome/TSA request，不声明泛化 admission fence、lifecycle、
  drained、active、trusted、E2 或 formal warning。下一步是 manifest-keyed recovery；全部 item
  收口后仍须独立 drain assessor，不能直接切换 active。
- profile/inventory/publisher/inventory-test/manifest-test SHA-256 依次为
  `19ddf6091ecf348bc609796a672734b67b91d1e4d8b6275604a03c7fba2a56b7`、
  `cb4ce5c3e734aca6da1fccf8c07fca6e313cece177a096232626fea15011c982`、
  `5e1e170473189d03d951b43a0e3258f4cf7d3cdff33c3dcb807206d1612d5e37`、
  `37538d26f9db5f78ed40ae839dca7c9546d8340df0a5817eb8df9aa39e184e5a`、
  `bee588aa54d881da1a97a0fbf4329770a1e0cc8ef44156b496979ed3c1b14a3b`。
  新模块与相邻 admission-cut/drain-v2/main 快测 `66/66`；未跑全仓、训练、TSA 网络、大容量或
  穷举 filesystem/crash 矩阵。Ruff/format/compile、JSON、dry-run 与 protected/frozen-writer
  检查通过；最终短审计无剩余 P0/P1。
- 详细合同见 `docs/ootang_epoch_workset_manifest_engineering.md`。

## 2026-08-27 official-writer lock-path admission cut R2b-2b-2a

- 基于已提交 `efa45c9 feat: add drain blocker observation`，新增显式、非默认阶段
  `ootang-epoch-admission-cut`。当前共 33 个可选阶段；默认链仍严格为
  `features → convlstm → ootang-operational-v4`，mutable 输出仅为
  `runtime/ootang_epoch_registry_v1/admission_cut_v1/status.json`。
- 旧 live/guard/replay/trusted/shadow/producer 记录会绑定当前实现哈希，因此本切片没有给
  11 个旧 writer/orchestrator 植入新 marker。profile 逐字冻结并在每次执行前复验这些文件，
  避免为了切断入口而让待排空旧 epoch 自身不可重放。
- 机器按 `manager → cycle → deploy → runner → replay → shadow` 取得全锁，create-only 发布
  prepare、exact `0444` + `everyone deny write` regular-file sentinels、inode-bound intent 和
  previous-hash-linked current-context attempts；随后仅用 Darwin
  `renameatx_np(RENAME_SWAP)` 先交换 canonical `deploy_cycle.lock`，再交换 `runner.lock`。
  不存在普通 rename fallback、restore、unfence、cleanup、force、批准、人工日期或 backdate。
- deploy-only crash 后，恢复不会重新 acquire/flock 已封闭 deploy pathname，而是锁定剩余
  runner/replay/shadow，追加绑定最新 old-ledger tip 的第二个 attempt，再只向前完成 runner
  cut；对封闭 pathname 唯一的 `O_RDWR` 是必须失败的 denial probe，成功即 integrity block。
  runner cut 后只需 manager/cycle 即可精确重放并发布 singleton event。首次物理 cut 前
  检出任何 v1 authority 时，v1 precedence，事务 inert 且锁路径保持开放。
- Event 只证明冻结 official writer 的两个共享 lock pathname 已持续拒绝 write-open。
  direct-filesystem/unknown writer 不在证明内；complete enumeration、reservation/recovery、
  泛化 old-work admission fence、v1/v2 mutual exclusion、anti-rollback、DRAINING lifecycle、
  drained/active/trusted/E2/formal 均保持 false，status 仍只是 cache。
- profile/module/test 当前 SHA-256 分别为
  `fe4e91768c8558d887a34465fa6c9f4e8c05f8c1a7cf07e061bc602733136c1c`、
  `95b675b132c5051cbbc4d34041b9686d122a64c6368f04c0bff8d6dddf1effcf`、
  `a26c7ebc476d0931ed0373f2b2fd5e6bc8255ed19ee9c3276a545a2a834ed8b3`。
  真实 Darwin ACL/swap、7 个崩溃点、busy/v1 precedence、context extension 和篡改快测，
  连同 main 合计 `45/45`，墙钟约 1 秒；Ruff/format/compile、strict JSON `32/32`、双 lock
  check、33-stage list 与默认/显式 dry-run 通过。97-path aggregate 保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`，11 个 frozen writer
  和共享 v4 Bai--Perron 无 diff；最终独立审计 P0/P1/P2 `0/0/0`。未运行 R2b、NGBoost、
  真实 non-clean chain 或全仓长测。
- 下一步是在两个 official writer lock pathname 已封闭的稳定边界内，完整、定界、内容寻址
  枚举六 family 及传递义务，形成 closed-workset manifest event。只有 manifest 成立后才可
  实现 exact-key action adapters；当前 cut event 不能晋升为 reservation/recovery/drained。
- 详细合同见 `docs/ootang_epoch_admission_cut_engineering.md`。

## 2026-08-27 epoch drain v2 first-blocker observation R2b-2b-1

- 基于已提交 `895844e feat: add drain eligibility observation`，新增显式非默认阶段
  `ootang-epoch-drain-v2-workset`。当前共 32 个可选阶段；默认链仍严格为
  `features → convlstm → ootang-operational-v4`。mutable 输出仅为
  `runtime/ootang_epoch_registry_v1/drain_v2/status.json` liveness cache。
- 状态机审计修正了原 roadmap 的不可达线性假设：v1 drain 只在入口 full-clean 时产生
  authority，而 eligibility 又要求该 v1 authority，所以 non-clean 路径不能是 eligibility
  的普通后继。本切片只在相同六锁下把冻结 v1 clean gate 的首 blocker 写为独立 v2
  content-addressed observation。
- Observation 绑定 R1/R2a event、candidate/slot、old live epoch id、live event count 和 ledger
  terminal；event 重放必须精确解引用 observation 的 path/hash/size/schema/context/items。
  同 context/blocker 重询字节幂等，变化后原 observation 只标 stale/inert。任一锁 busy 时
  event/object/head/status 都不写。
- v2 单向检测全部 v1 drain transaction/object witness，绝不采纳或改写。冻结 v1 不读取 v2，
  因此本切片明确不声称互斥：后生 v1 authority 具有优先级，v2 observation inert。
  head/status 只是 repairable cache，不提供 anti-rollback authority。
- 当前只允许 `first_blocker_observation_implemented=true` 以及 R1/R2a/old-ledger context binding；
  bounded reservation/recovery、complete enumeration、old-work admission fence、v1/v2 mutual
  exclusion、anti-rollback、DRAINING event/route fence、drained/active/trusted/E2/formal 均 false。
- profile/module/test SHA-256 分别为
  `aa12082e32b9b94fc4ad4b08232ed586b49c8c1bf1d2ccdaf3587b096c047d17`、
  `93a6463f514d73c4809287e1bbc8033984c82f550d5c55ce84c209475d7b604b`、
  `d315d394b536eec4b1ea09c7a9683cfcbc0ebadc58a98758268f32d3329ed488`；v2+main
  定向快测 `45/45`，静态/JSON/lock/dry-run 检查通过，97-path aggregate 保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。未重复 R2b、
  真实长链或全仓长测。
- 后续 R2b-2b-2a 已完成 frozen official-writer lock-path cut，R2b-2b-2b/R2b-2b-2c 又推进到
  frozen-observation reservation、item transition plan、首批本地 adapter 与 recovery-only
  expected-pre-head CAS 与单事件 `anchor_request_recorded` adapter；当前下一步是
  `anchor_result_recorded`。不能把本
  历史观察、physical cut 或非终态 receipt 直接晋升为 recovery/drained/active。
- 详细合同见 `docs/ootang_epoch_drain_v2_engineering.md`。

## 2026-08-27 epoch drain eligibility R2b-2a

- 本增量基于已提交的 `2eefceb feat: add atomic epoch drain barrier`，新增显式、非默认阶段
  `ootang-epoch-drain-eligibility`；当前共 31 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段位于 `ootang-epoch-drain` 之后、
  operational v4 之前，但不会进入默认链。machine-readable mutable 输出为
  `runtime/ootang_epoch_registry_v1/drain_eligibility_status.json`。
- 当前候选 profile/module/test SHA-256 分别为
  `2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a`、
  `46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc`、
  `4e247497ca78a4449ae00769ad1c383c5b5279195c357edcf84e29af4e5754a4`；若代码或测试继续
  修改，冻结前必须重算。本增量作为独立 R2b-2a 里程碑提交。
- 观察器只从 persisted unique R2b event 恢复历史 R1/R2a selector，不调用 start/swap
  写路径，也不偷换成 current registry tip。它按
  `manager → cycle → deploy → runner → replay → shadow` 非阻塞取得全锁，完整重放
  event/intent/capsule/exact pre-swap boundary/exchange WAL terminal/armed marker、canonical
  fence 和 archived old runtime；拒绝 fence 后 outstanding issue，并要求 archived issue
  inventory 精确不变。每个历史 observation 的 semantic/activation/snapshot source object
  都按冻结 path/hash/size 重新解引用，不能只信 observation 内自洽 metadata。
- clean state 先发布 deterministic 64 MiB content-addressed observation，再追加
  previous-hash-linked event。Observation 不含 poll time 或 staged next-epoch incoming；同
  state repoll 字节幂等，合法 settled extension 经二次 exact capture 后自动追加一条；
  pending 或 prospective capacity 超限只返回 machine waiting/current=false，不写 event。
  首条 event time 不得早于 R2b event，后续不得早于 terminal eligibility event；六锁后只
  自动清理严格识别的 crash temp，unknown temp fail closed。
- head/status 只是 repairable cache，status 明确 `cache_authority=false`。integrity failure
  尽力写 `blocked_integrity/current=false`；只有 schema/profile/time/全部负声明合法且严格
  ahead 的 cache 才保留为 rollback witness，at/behind replayed chain 的 tip 必须精确匹配
  真实 entry。同 count 伪 tip 因此不会被固化成永久机器阻塞。event suffix 与所有 mutable
  ahead witness 同时被外部删除仍是 v1 明示检测边界。
- Observation/event 固定 `observation_authority_only=true`、
  `lifecycle_authority=false`、`transition_authority=false`；old drained、candidate selected、
  active switch/rotation、trusted anchor、E2、real activation、formal warning 全部保持 false。
  future assessor/transition 必须重新取得同一锁集并 exact recheck machine-current state。
- 验证已通过 eligibility public `20/20`、含真实 R2b authority integration 的 full
  eligibility `21/21` 与 main `35/35`。本 scoped 增量不再重复全仓：已提交基线
  `2eefceb` 已有 `809/809`；额外启动的 full discovery 在运行 `4184.67` 秒、进入与本增量
  无关的 NGBoost horizon-sensitivity 用例时被主动中断，中断前没有 failure/error。该次不是
  完整 suite 结果，不得写成 PASS，也不要由后续 agent 再次重复。已知工程债是逐锁/
  逐 fsync fault matrix、冻结 R2b 私有 API
  耦合、eligibility full-chain O(K²) 重放，以及上述 mutable-witness 同删边界。
- 该条目的下一步已由上方 R2b-2b-1 首 blocker observation 开始；后续 R2b-2b-2a 完成
  frozen official-writer lock-path cut，R2b-2b-2b/R2b-2b-2c 又推进到 frozen-observation
  reservation、item transition plan、首批本地 adapter 与 recovery-only expected-pre-head CAS。
  单事件 `anchor_request_recorded` adapter 现已完成；当前下一步是
  `anchor_result_recorded`，且不得重解释或覆写任何已发布
  R2b/R2b-2a v1 bytes。terminal/derived-work closure 解决后才是独立 drain assessor、权威
  `SEALED(old)+ACTIVE(new)` transition、cycle v4 与 scheduler authorization。
- 详细合同见 `docs/ootang_epoch_drain_eligibility_engineering.md`。

## 2026-08-27 epoch drain-start barrier R2b 首切片

- 本增量基于 `b53a238 feat: add executable epoch preparation`，新增显式、非默认阶段
  `ootang-epoch-drain`；该 R2b 基线共 30 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。R2b profile/module/test SHA-256 分别为
  `1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105`、
  `c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602`、
  `b79d132562891a3134d55073a282b351533e525b0661cd47914698e323612d68`。
- R2b 使用独立 `drain_events`、head/status、`drain_fence_prepares`、intent、capsule、
  append-only `drain_exchange_attempts` WAL 与 overlay namespace，并在 shared
  content-addressed objects 中保存完整 intent-prefix、staged feed 和 full-clean boundary；它只引用并复验 R1 registry
  和 R2a preparation tip，不改写两条历史链、candidate capsule 或 smoke receipt。mutable
  head/status 不是 authority。intent 是 durable transaction reservation/lower-bound，其中
  `candidate_at_intent` 只记录创建 intent 时绑定的 candidate，明确不是 activation
  selection。WAL、armed marker 与 boundary 只有 recovery authority；orphan
  fence-prepare/intent-prefix/intent/capsule/staged-feed/attempt/boundary object 本身均无
  lifecycle authority，唯一 DRAINING lifecycle authority 仍是 `epoch_drain_started` event。
- drain-start 临界区严格按 `manager → cycle → deploy → runner → replay → shadow` 取得
  全锁，避免任何已知 issue producer、cycle、live runner、checkpoint replay 或 calibration
  shadow 与 route fencing 并发。任一锁 busy 时不部分推进。
- 首版只接受 clean start：旧 epoch 不得有 outstanding issue，也不得存在 pending
  guard、trusted-time 或 shadow 工作。若任一未收口，机器返回 waiting，不追加 authority
  event、不撤销 route；已有 intent 只保留为 lower-bound，不改写为最终快照。机器也不借助
  人工日期、冻结、批准、force、backdate 或伪造 outcome 清空状态。
  old work 可在两次 scheduler poll 间继续；下一次 poll 仍 pending 就继续机器等待，合法
  append-only settled extension 会被完整重放并自动纳入新的 clean-state，不要求人工 cleanup。
- 通过 clean-start 检查后，机器先捕获 old-route identity，并在任何 tombstone `mkdir` 前
  create-only 发布唯一 `fence_prepare`，绑定历史 R1/R2a、capsule/完整 intent-prefix、
  old-route device/inode/mode、tombstone path 与 ACL/swap policy。crash 或 current tip 推进后
  仍从该永久 marker 机器恢复同一历史 transaction，marker 自身不产生 lifecycle authority。
  marker 落盘后才准备 mode 精确为 `0755` 的空 tombstone，安装并 exact-readback extended
  ACL `everyone deny write`，且实际 add-file 拒写探针通过；随后持久化 intent，并在全锁下
  复验全部绑定。机器完成下述 capacity/boundary/WAL/armed-marker 序列后再调用
  macOS `renameatx_np(RENAME_SWAP)`，跨父目录交换 canonical old `issue_inbox` 与 overlay
  tombstone；ACL 随 inode 交换后立即围栏 canonical route，再原位 chmod 为
  exact `0555` 并复验 ACL/拒写。swap→chmod 崩溃恢复只能向前完成加固，绝不 swap back。
  该原语/ACL 不可用或失败时 fail closed，禁止以普通 rename、move 或复制删除降级。
- R2b 区分两个边界：物理 issue-admission boundary 是 Darwin swap 成功的瞬间；权威
  state-snapshot boundary 是六锁下 full-clean replay 形成的 content-addressed **pre-swap**
  boundary object，再由唯一 `epoch_drain_started` 精确引用。该对象绑定 live/issue/guard/trusted/
  outcome/shadow/source inventories 与 staged next-epoch incoming；staged incoming 不是旧
  source authority，未被 event 引用的 orphan object 也没有 authority。event 当前只进入
  `DRAINING`；`activation_candidate_selected`、drained、`SEALED(old)`、`ACTIVE(new)`、
  automatic rotation、trusted anchor、E2 evidence、real activation 与 formal warning
  声明全部为 false。
- capsule 精确引用内容寻址 full `intent-prefix` manifest；它保存 live/shadow 全部 entry
  hashes 与 issue/outcome/guard/trusted/source inventories。每次 poll 都验证 start→current
  的 append-only/prefix extension，合法 settled extension 自动进入最终 boundary。manifest
  上限显式为 64 MiB，staged next-epoch feed 上限为 16 MiB；feed 独立内容寻址、boundary
  只存 reference，因此合同内的大 feed 不会因 control-object 上限而自锁。event append 前
  还必须完整自重放 boundary 及其全部引用，重建 state 精确一致后才写唯一 event。
- swap 前先以最大 16 MiB staged CAS reference 对完整 boundary 做 worst-case capacity
  preflight。若 canonical boundary 超过 64 MiB，机器返回
  `waiting_for_drain_boundary_capacity`，canonical route 保持未交换且不写 event，也不要求
  人工 cleanup。通过后不可逆尾部固定为 final fence verify → actual staged queue 稳定双读/
  CAS → actual capacity → publish exact pre-swap full boundary → append/replay
  `drain_exchange_attempts` terminal → 在 fence operand 内写入直接绑定 terminal+boundary 的
  `.epoch-drain-armed-attempt.v1.json` → immediate Darwin swap；marker 随 fence inode 原子
  移到 canonical route，post-swap publisher/self-replay 仍复验。
  正常同 poll 的 post-swap logical clean 必须与 pre-swap state 精确相等；只有已经 exchanged
  的 crash recovery 才允许从 immutable start-prefix 到 recovery-current 的合法 append-only
  extension。
- 每个 WAL attempt 都精确引用一个完整 pre-swap boundary，并由连续 sequence/previous hash
  组成 append-only chain；只可武装 unique terminal。prepared retry 可自动收敛严格的单一
  marker temp 与 exact/缺失 ACL crash state，不允许人工 cleanup。exchanged recovery 必须从
  已随 swap 移动的 armed marker 读取 terminal 的**旧 boundary**；current clean 只作其合法
  append-only extension gate，不能重建或替换 boundary。WAL suffix rollback、branch、gap、
  extra entry、symlink，或 marker/ACL/temp 不精确时一律 fail closed。64 MiB 超限仍在 swap
  前返回 machine waiting；为历史增长引入 chunk/Merkle 属后续 R2b-2b v2 版本，不得重解释 v1
  bytes。
- R2a 已记录的真实默认链试跑结论保持不变：正式五种子训练后，R1 因仓库缺少从
  2020-07-01 连续到机器当前日的 finalized feed，在历史 feed causal gate 以
  `Bundle was not durable before the first target natural day` 正确 fail closed；未生成
  R1 candidate，也没有真实 R2a/R2b 端到端 PASS。不得通过改时钟、合成日期、人工
  backdate 或 test override 伪造通过。
- 最终验证：R2b focused 为 `12/12`（runner `1008.486` 秒，墙钟 `1025.68` 秒）；
  R1+R2a+R2b+main 组合为 `116/116`（runner `2361.831` 秒，墙钟 `2393.77` 秒）；
  全仓为 `809/809`（runner `5568.360` 秒，墙钟 `5642.18` 秒），均为 0 failure /
  0 error。main 为 `34/34`，正式 v5 preflight 为 `23/23`，报告仍为 G0 PASS、
  G1--G4 BLOCKED、G5a 未评估/未授权、formal warning false。Ruff check、scoped
  format、compileall、`git diff --check`、strict JSON `30/30`、根/可信时间双
  `uv lock --check`、30-stage list、默认/显式 dry-run 与共享 v4 Bai--Perron diff
  均通过。97 条保护路径聚合 SHA-256 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
  最终独立标准/规格审计为 P0/P1/P2 `0/0/0`；逐对象 publication-fsync 故障矩阵仅作为
  后续 coverage debt，不削弱本轮已验证的通用 durable-adoption 合同。
- 该历史 R2b 基线的下一步 R2b-2a clean-start eligibility observation/stale detection 已在
  本文件顶部增量完成，结果仍只可为 DRAINING。后续 R2b-2b-1 v2 已实现首 blocker
  observation，但不具 reservation/recovery authority。后续 R2b-2b-2a 已完成 official-writer
  lock-path cut；后续 R2b-2b-2b 已完成 frozen-observation 六族 manifest/transition seed
  reservation，R2b-2b-2c 已增加 item transition plan、逐 step authority chain、首批本地
  adapter 与 recovery-only expected-pre-head CAS。当前下一步是窄化的 machine-only
  `anchor_request_recorded` adapter；v2 不得重解释或覆写 v1
  fence-prepare/intent-prefix/capsule/intent/exchange-attempt/
  armed-marker/boundary/event bytes；历史 chunk/Merkle 也只能由该新版本表达。其后才是独立
  drain assessor 与权威 active transition；cycle v4、scheduler authorization 与长链
  O(N²) 扫描优化仍在更后。
- 详细锁、swap、intent 与非声明边界见 `docs/ootang_epoch_drain_engineering.md`。

## 2026-08-27 epoch 可执行准备 R2a

- R1 已在提交 `3d6ce8f feat: add immutable epoch candidate registry` 固定。在其上
  新增显式、非默认阶段 `ootang-epoch-preparation`；该 R2a 基线共 29 个可选阶段，
  无参数默认链仍严格为 `features → convlstm → ootang-operational-v4`。R2a profile/
  module SHA-256 分别为
  `c6ec0b1f340effd9e3fd5cd1a0ee67ebca9ffa4dc743a9cb36d701850dc875f9`/
  `b03182accc3e8d482683eda29c7c07bdb66f7a316dfa99a41f29e1b99b3fe209`。
- R2a 从 immutable R1 candidate tip 解析静态 local import closure，精确要求 22 个
  `convlstm`/`monitoring` 模块；只允许从 canonical project 捕获已固定 SHA 的
  `code/convlstm/__init__.py` 和 `code/monitoring/__init__.py` 两个 augmentation。
  dynamic/wildcard local import、第三个补件、closure 数量/顺序漂移或自洽删除 root
  module 均 fail closed。
- executable capsule 将 R1 event/candidate/slot/live-epoch、exact closure/resource tree 与
  R2a profile/implementation 精确 bytes 一起存入 content-addressed objects；capsule、smoke
  receipt 与 preparation event 都交叉绑定 path/SHA/size。物化树只能在原 canonical
  project 和 `slots/<slot-id>/live` 上使用，固定 `relocatable=false`、
  `portable_offline_runtime=false`；它不包含 `.venv`、CPython/uv binary、OS 或 wheel cache。
- 生产 smoke 固定 uv `0.12.5` 绝对路径及 SHA-256、CPython `3.10.20` 及
  executable SHA-256、SOABI 与 `macOS-26.5.1-arm64-arm-64bit`。机器分别用根
  43-distribution inventory 和 trusted-time 5-distribution inventory 的
  `uv --no-config run --isolated --frozen ... python -I -B` 环境做双域烟测；任一
  Python/distribution/platform 指纹漂移均阻断。
- 根烟测编译 exact 22 Python files、从物化树 import cycle-v3/trusted-time shadow、
  从 canonical slot 重载 source/model/live prerequisite 并复算 live epoch id。随后对 seeds
  `0..4` 调用 bundle `predict_p50()`，与 training manifest `reload_replay` 按精确
  station 集合/顺序、finite value、`rtol=0, atol=1e-6 mm` 逐项比较；trusted-time
  域另从同一树 import core。
- 已准备 candidate 的每次 current repoll 都重验 R1/capsule/tree/receipt 并重跑双域
  smoke，不仅信任历史 status。同 R1 candidate 遇到 R2a implementation 升级时机器
  重做 closure/materialization/smoke 并追加 `candidate_revalidated`；历史 event 仍依自身
  内容寻址 implementation object 重放。smoke receipt 已提交但 event 未提交的 orphan
  也必须重跑当前 smoke 并比较；环境漂移不得直接补 event。
- event 最终 publish 前再复验 R1 receipt/current artifacts/空 future namespace、R1/R2a
  profile/implementation bindings、capsule、tree 和 smoke receipt；追加后全链 replay 并确认
  唯一 tip。R2a config file 或其 canonical project 父路径为 symlink 时即使 bytes/hash
  相同也 fail closed。公开 API/CLI 不提供 runtime/candidate/date/freeze/approve/force/backdate/
  smoke override；manager lock busy 为 exit 3。进入统一错误归一范围后的完整性冲突为
  exit 2；少数 acquire-lock/profile 前置异常仍可能 traceback/exit 1，但同样 fail closed。
- R2a 明确固定 `old_epoch_drain_implemented=false`、
  `active_epoch_switch_implemented=false`、`automatic_epoch_rotation_implemented=false`、
  `trusted_anchor_receipt_verified=false`、`e2_live_evidence_eligible=false`、
  `real_activation_ready=false`、`formal_warning_output=false`。后续 R2b 首切片现已完成
  clean-start canonical route fence 与 `epoch_drain_started`，但只进入 DRAINING；非
  clean-start trusted-time/guard/shadow 恢复和 drain assessor 仍待完成。全过程禁止人工
  日期、冻结、批准或 force。
- epoch-preparation 独立终审为 `30/30`（590.587 秒），与 pipeline 最终组合为
  `63/63`（597.651 秒），全仓回归为 `796/796`（3047.808 秒），均为 0 failure /
  0 error。Ruff、compileall、R2a scoped format、diff-check、strict JSON `28/28`、
  根/可信时间双 lock check、默认/显式 dry-run 与 29-stage list 均通过。v5 frozen
  preflight 为 `23/23`，仍为 G0 PASS、G1--G4 BLOCKED、G5a 未评估/未授权、formal
  warning false。97 条保护路径无相对 HEAD 漂移，聚合 SHA-256 实测仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 另以真实 R1 default prebuilder 启动正式五种子 × 120 epoch 隔离链，五个 checkpoint
  均完成；R1 在 candidate 发布前因 `Bundle was not durable before the first target natural
  day` 正确 fail closed。当前没有从 2020-07-01 连续到机器当前日的 finalized feed，故
  未生成 R1 candidate、未进入 R2a default smoke。该结果不是 PASS，也不通过改系统时钟、
  合成日期、人工 backdate 或 test-epoch override 绕过；真实 default smoke 继续作为 P2。
- capsule/lifecycle 审计当前无新增 P0/P1。保留的非阻断 P2 包括有限 AST denylist、
  smoke import 覆盖边界、name/version-only distribution inventory、同 UID 非协作 writer/
  路径 TOCTOU/全历史重写、异常后的旧 status cache，以及部分 pre-lock/profile
  `RegistryError` 尚未统一映射 CLI blocked；orphan capsule/tree 从不构成 authority，
  freshness 必须依赖本次 poll 成功退出和 event replay。完整边界见工程文档。
- 详细合同、exact module 列表和恢复边界见
  `docs/ootang_epoch_preparation_engineering.md`。

## 2026-08-26--27 不可变 epoch registry R1

- 本增量基于 `74ef8b9 feat: add cryptographic time shadow gate`，新增显式、非默认
  阶段 `ootang-epoch-registry`；该 R1 基线当时共 28 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段不提供 target-date、freeze、
  approve、force、backdate 或人工签名入口。
- R1 从固定 finalized feed bytes 与冻结合同推导 stable slot id，直接在最终
  `slots/<slot-id>/live` 路径运行 source materialization 和五 seed bundle prebuild；
  已发布 manifest 含绝对 artifact path，因此禁止 build 后移动或以 symlink 切换。
- source/model public loaders 通过后，机器要求 lineage 中的 daily-feed snapshot 精确等于
  registry feed bytes，把固定代码、配置、根/隔离依赖锁和 RFC 3161 trust 保存为
  content-addressed archival byte capsule，并把 feed 与全部声明的 runtime artifacts 另存
  immutable snapshots；随后才向严格 N→N+1、previous-hash、create-only registry 链追加
  `candidate_ready`。历史 replay 只依赖这些快照，不要求以后合法启用的 mutable slot 永久
  空白。capsule 尚未 materialize 为可执行旧 epoch tree，其显式 allowlist 也不是
  transitive import closure；mutable status/head 只是可由权威事件链恢复的 cache。
- feed export 必须递增，source id 固定；修订可以前进，但任何已见 revision id/record bytes
  的回退均在新 receipt/event 发布前 fail closed。完整 feed 合同通过后、长训练前，机器先
  追加独立 create-only feed-observation chain 并刷新 tip witness；observation event 内含
  精确 raw feed bytes，是单文件水位 commit，object 副本可由它恢复。所以 orphan receipt、
  waiting build 或 object publication 前崩溃都不会遗忘更高水位，invalid/future feed 则
  不会污染链。构建前后重新捕获
  worktree capsule；默认 prebuilder 在真实动作边界重新采机器时间，测试
  prebuilder/feed override 只允许 project production runtime tree 之外的 isolated runtime。
  实现/profile SHA-256 分别为
  `1418b754012b71b296c200539efa846cea63e5a4374e44d216bd656f8b047b9c`/
  `c56004649689ee8aa4beeca6a7c61bbdd5529ec62706880ea8eac65a8ca18edd`。
- 权威 event 仍名为 `candidate_ready`；status 使用更窄的
  `immutable_candidate_record_ready`，避免把历史快照完整性误写成当前 mutable slot 的
  activation readiness。production poll/CLI 不暴露 runtime、feed 或 prebuilder override。
- R1 不修改旧 live v1/cycle v3，不创建 candidate ledger，不签发 issue、不读 outcome、
  不切换 active。`automatic_epoch_rotation_implemented`、可信 anchor、E2 evidence、real
  activation 和 formal warning 全部固定为 false。registry 定向 `40/40`、registry +
  pipeline `72/72`、全仓 `765/765`（344.829 秒，0 failure / 0 error）；Ruff、scoped
  format、compileall、strict JSON `27/27`、根/隔离 lock check、diff-check 均通过。
  v5 preflight `23/23`，仍为 G0 PASS、G1--G4 BLOCKED、G5a 未授权；97 个保护路径无
  diff，聚合 SHA-256 仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。两轮独立只读
  审计均为 P0/P1 `0/0`；保留的 trusted-writer/掉电持久性、累计 replay O(N²) 与 R2
  executable closure 是已显式记录的 P2 工程边界。
- 后续 R2a 已在不修改 R1 链的前提下完成 exact executable closure、same-origin tree
  materialization 和双域/五种子烟测；R2b 首切片又完成 clean-start canonical route
  原子 fencing 与 `epoch_drain_started`。当前仍只到 DRAINING，非 clean-start
  trusted-time/guard/shadow 恢复、drain assessor 与 active transition 尚未实现。若 outcome
  永不到达，机器只能等待，不能伪造结局、人工冻结/批准或强制轮换。

## 2026-08-26 RFC 3161 可信时间影子门

- 本增量基于 `bace358 feat: add independent issue replay gate`，新增显式、非默认
  阶段 `ootang-trusted-time-shadow`；当前共 27 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段只对 replay-gated verified-live
  completion 对应的 issue seal 建立外部 proof-of-existence，不读 outcome，不提供
  target-date、freeze、approve、force、backdate 或人工签名入口。
- v1 固定 Sigstore production RFC 3161 endpoint、policy OID
  `1.3.6.1.4.1.57264.2`、SHA-256 imprint、256-bit nonce、`certReq=true`、仅
  `PKIStatus=granted`、必须存在且不超过 1 秒的 accuracy，以及冻结的
  `Asia/Shanghai = UTC+08:00` 目标日边界。trust source 固定到
  `sigstore/root-signing@ba3066c420970c13772ba0625f09f1ec97193116`；manifest、leaf
  DER、root DER SHA-256 分别为 `33d22cc6dbdf8bf016b0cb96e291ca4538109ffb5d22a639edb34d2f42c80eef`、
  `85f927bc07ab62cac3b44356c10efc81b2c6883fda7ab9e6d870d9d13acd05b7`、
  `2aca8fea5d3ce48b01cc77076293c280e6c23ffe44034757ee7833ca9f45d633`；这些值也在
  core 中硬固定，不能通过同时替换 config、manifest 和自签证书改变 trust root。
- 根 `pyproject.toml`/`uv.lock` 保持原字节和
  `bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0`/
  `f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c`，避免破坏旧
  replay/shadow 配置绑定。stdlib launcher 改用 `uv 0.12.5 --isolated --frozen`、精确
  CPython `3.10.20`、`python -I` 与清理后的子进程环境启动独立子项目；launcher、
  core、子项目、子锁 SHA-256 分别为
  `92a0a755881f549b272bfbd09d11b2590e06e9ecb06409421f6bd18e261b1f1b`、
  `797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7`、
  `236606b46ed945fbbce46868a1a8ab5aac9a1131f39352f324e95a6001b59625`、
  `aebfc5d498735f694572ee8b53c328da5fa66a84da05d202605a2500e8b78f93`。
- 请求 JSON/TSQ 先 create-only 持久化，原始 TSR 进入 content-addressed object，随后
  建 target link、receipt 和 status；每次公开 load 都从完整 live ledger 与
  verified-live guard history 重建科学 envelope，再复验 canonical bytes、路径、
  nonce、policy、message、单 signer、精确 leaf、leaf→root/root self 签名、TSA name、
  CMS 签名/证书链、`genTime+accuracy` 和目标日前因果条件。锁 inode 被替换、symlink、
  非规范 JSON、协调 trust swap、伪造 envelope、对象 relocation、模块注入及崩溃恢复
  均有对抗测试；联网前若无法读取 signal mask 或发现 `SIGALRM` 被继承屏蔽则立即
  fail closed，408/425/429/5xx 自动等待重试，确定性篡改则 exit 2，锁忙 exit 3。
- 公共 production dummy fixture 的 TSQ/TSR SHA-256 为
  `bdc94a42cd34ba1a947c521b19553edea9a7699c621c8ff66ba4458382acbfa3`/
  `4535d7ddc291159db625a9b68b403d5db14a8544c3be102604377a4a7d317afc`，已真实联网取得并
  在隔离环境离线复验；它不是藕塘科学证据。当前 focused 验证为 core `22/22`、
  launcher+pipeline `39/39`、全仓 `724/724`（345.293 秒，0 failure / 0 error）；Ruff、
  format、compileall、strict JSON `3/3`、diff-check、根/隔离 lock 检查均通过。正式 v5
  preflight 为 `23/23`，仍是 G0 PASS、G1--G4 BLOCKED、G5a 未授权；97 个保护路径保持
  `97/97`，聚合哈希仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。最终独立只读
  审计为 P0/P1/P2 `0/0/1`，唯一 P2 是下述 ESSCertIDv2 显式解析边界。
- 即使密码学回执有效，`trusted_anchor_receipt_verified`、
  `e2_live_evidence_eligible`、`real_activation_ready` 与 `formal_warning_output` 仍全部
  为 false。CMS 证明的是固定 TSA 签署的时间声明，不独立证明其上游 UTC 时源绝对
  正确；当前还没有多运营方 quorum，也未单独解析 RFC 5816 ESSCertIDv2 signed
  attribute。下一道 machine-only 门禁是 immutable epoch registry、bundle prebuild
  与安全自动 rotation；随后把 trusted-time 接入 cycle v4 并实施 scheduler entry
  authorization，不能以人工冻结或批准替代。

## 2026-08-26 指定入口 checkpoint/input replay 与 cycle v3

- 本增量基于 `9a69725 feat: add autonomous calibration shadow`，新增显式非默认
  阶段 `ootang-issue-replay`、`ootang-verified-live` 和
  `ootang-prequential-cycle-v3`；当前共 26 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。没有 target-date、freeze、approve、
  force、backdate、manual-signature 或人工清理入口。
- replay/verified-live/cycle-v3 配置 SHA-256 分别为
  `c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5`、
  `081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b`、
  `6852876db121027e82aedfb2b65c9cb1d9b40106b19c7068ba8764b317e1db24`；旧
  live/cycle v1/v2 配置和 ledger schema 均未改写。
- independent verifier 递归使用共享 source authority 重建 historical base + daily
  feed 的 current/activation canonical dataset，再独立复刻 producer 的五项
  normalization、IDW、7-channel 输入、ConvLSTM cell/head、station readout 与
  P50 反归一化；8 个 persistence 必须 exact，五 seed × 八站的 40 个 P50 固定
  `rtol=0, atol=1e-6 mm`。公开 receipt reload 也重新加载五 checkpoint 做真实
  forward，不信任 receipt 自报 digest。
- verified-live 在原 live-v1 `runner.lock` 内固定执行 replay receipt → pre-seal
  intent → live issue transaction → completion。intent 前、append 前、append 后和
  receipt create 后均重新采样时钟；回退/跨目标日会在不可逆写入前阻断或精确撤销
  本轮 receipt。append 后崩溃先恢复 completion，再允许读取 outcome；无预先 intent
  的旧 v1 seal 永不事后追认。
- cycle v3 以 13 个固定阶段把两次 replay、三次 verified-live、四次 shadow 与
  source/bundle/outcome/issue 屏障组合成有界 fixed point。progress 先严格验证 raw
  链，再仅投影科学语义，排除合法 clock、storage path、raw sequence/entry hash 和
  anchor-only churn；issue/seal 科学 payload 的变化仍改变 token。稳定 token 与
  `work_remaining` 矛盾、振荡、stage crash、锁竞争和 status schema/claim 漂移均
  fail closed。
- 对抗修复覆盖 source/current/activation 自洽篡改、五 normalization bit parity、
  self-consistent forward/digest 篡改、final/intermediate symlink、pathname inode
  replacement、read-once 同 inode 变化、create-only 冲突、崩溃临时文件自动恢复、
  completion 时钟回退不落坏记录、declared source/model/issue/verification 因果时间，
  以及不同 runtime 时钟/路径/anchor retry 下科学 token 一致。最终 replay/
  verified-live/cycle-v3 为 `36/27/16`，三模块 `79/79`，加 pipeline `109/109`；全仓
  `715/715`（727.081 秒，0 failure / 0 error）。Ruff、compileall、JSON 与
  `git diff --check` 全部通过；v5 preflight `23/23`，G0 PASS、G1--G4 BLOCKED、
  G5a 未授权；97 路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。真实空
  runtime 精确执行 13 阶段，一轮 `converged_waiting`，所有 evidence/activation/
  promotion 标志为 false。最终独立审计 P0/P1 `0/0`。
- 本增量不训练、不调参、不选择 seed、不改校准候选/阈值，也不重新估计 accuracy、
  coverage、interval score、FAR 或 recall；它改善的是 issue 来源与提交因果的可验证
  完整性，不是预测精度。旧 live-v1 CLI 仍可绕过指定入口，本地账本仍是
  trusted-writer chain，可信密码学时间、immutable epoch registry/自动 rotation、
  scheduler entry authorization 与 O(N²) 长链优化仍未实现。因此 formal warning、
  E2 live evidence 和 real activation 继续为 false；下一道机器门禁是可信密码学时间。

## 2026-08-26 E2 calibration shadow 与 cycle v2

- 本增量基于 `fd6f919 feat: add prequential calibration bakeoff`，新增显式非默认
  阶段 `ootang-prequential-calibration-shadow` 和 `ootang-prequential-cycle-v2`；
  当前共 23 个可选阶段，无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。全程没有人工选日、冻结、批准、
  补签或自动 promotion 入口。
- shadow 配置 SHA-256 为
  `28c02510f81e1832220913d4bfde69bc8abe9a2269aa8279aabc297f60113857`，精确绑定
  live/deploy/bakeoff v1、不可变 calibration core、`pyproject.toml` 和 `uv.lock`；
  cycle v2 配置 SHA-256 为
  `875daa95416e58e3c80a4a68f59874047daa1bbb5b395878f6a9265605279242`，精确绑定
  cycle v1 与 shadow v1。
- ACI v1 control、AgACI-EWA variant 和 SPCI-QRF 分别维护 8 个 station-local
  状态。每个目标先在单个事务中写 `opened + 24 candidate issue + sealed`，匹配的
  live settlement 到达后才写 `opened + 24 reveal + 24 state update + settled`。
  激活时已存在的 outstanding issue 固定不计未来支持；漏签 settlement 只记
  backfill-ineligible，不补造 issue 或更新状态；revision 只追加 24 条 rescore，
  state-before/state-after 相同且不进入原始充分统计。live drift 自动同时重置三方法。
- 独立 SQLite STRICT shadow ledger 使用不同 application id、WAL/FULL、canonical
  finite JSON、事务摘要、全局链、issue-only 链、conflicting insert/replace guard
  和 update/delete trigger。每次公开读写都从 genesis 完整验证并数学重放；完整
  同语义重试保留首次事件/时间，部分事务、同键异语义、schema/chain/state 漂移均
  fail closed。上游 epoch 在无 outstanding 时由机器 close+cold-start，有 outstanding
  时拒绝跨 epoch。
- runner 把 issue seal、settlement、backfill 和 revision 按 live source sequence
  统一合并，关闭了长时间离线时较晚 source 推过较早 revision 的游标缺陷。单次最多
  512 个动作，合法积压返回 `work_remaining/exit 0`；确定性 progress token 排除
  poll/status 时间、shadow recorded time、raw SQLite bytes 和无科学变化的 anchor
  churn。cycle v2 在 v1 七步周围插入四次 shadow reconcile，共 11 个固定阶段；
  `work_remaining` 与稳定 token 的矛盾会 fail closed，不能误报收敛。
- 评估合同在首个 shadow outcome 前预声明：共同支持、至少 180 个共同未来目标日和
  每站每方法 180 个样本、30 日 rolling、coverage absolute gap `≤0.05`、challenger
  相对 ACI coverage-gap margin `≤0.02`、interval-score ratio `≤1.0`、availability
  `≥0.95`，并要求逐站通过。它只自动计算 engineering readiness；selection、
  promotion、E2 live evidence、real activation 和 formal warning 始终为 false。
- 最终验证：shadow ledger/runner/cycle v2 为 `14/13/23`，合计 `50/50`；pipeline
  `29/29`；prequential/deploy/shadow 联合回归 `269/269`；全仓 `635/635`
  （330.833 秒，0 failure / 0 error）。Ruff、compileall、JSON 校验和
  `git diff --check` 全部通过。真实空 runtime 精确执行 11 阶段，一轮返回
  `converged_waiting`，shadow 为 `waiting_for_live_prerequisites`，evidence/promotion
  均为 false。独立最终审查为 P0/P1/P2 `0/0/0`。
- 正式 v5 fail-closed preflight 仍为 `23/23`，G0 PASS、G1--G4 BLOCKED、G5a
  未授权；E1 与 97 条保护路径哈希保持不变。在该历史增量结束时，下一道机器门禁是
  runner-independent checkpoint/input replay；它现已由本文顶部的指定入口实现。
  可信密码学时间、immutable epoch registry/自动 rotation、调度入口权限边界和长链
  O(N²) 扫描优化仍待完成。不得用人工冻结或批准替代。

## 2026-08-26 E1 prequential 校准 bakeoff

- 新增显式且非默认阶段 `ootang-prequential-calibration-bakeoff`，当前共 21 个
  可选阶段；无参数默认链仍严格为
  `features → convlstm → ootang-operational-v4`。阶段只消费受保护 E1 四产物，
  三种方法逐 binary64 复用同一 6,888 行 point forecast，不修改模型、E1、E2
  ledger、v4/v5 或 Vajont。
- 固定并列方法为 E1 exact `aci_v1_control`、七个 gamma 专家的
  `agaci_ewa_variant_v1` 和 signed-residual `spci_qrf_v1`。AgACI 名称明确标注该
  实现不是论文的 BOA+gradient-trick 精确复现；SPCI 使用 lag=10、最新到最旧的
  Eq.13 特征、60 个 QRF pair、180 日窗口和固定浅层 10-tree RF。任何理论保证
  均不从论文直接转移到项目的 clipped/windowed/reset 变体。
- 每个 fold-date 先生成 3×8=24 个 issue 和 outcome-free candidate batch hash，
  再读取同日 actual 并更新下一日状态；fold 与 E1 drift schedule 自动 reset。反事实
  测试确认首日 actual 改动不改变首日 24 issue/hash，但会改变三种方法的次日
  state/hash。ACI alpha、interval 与 warmup 逐项对齐 E1。
- fold 1/2/3 的 ACI coverage 为 `0.791192/0.695061/0.630746`；AgACI-EWA 为
  `0.848140/0.745884/0.657121`，在 fold 2/3 缩小 coverage gap，三折平均宽度和
  interval score 均下降，但 fold 1 从轻微欠覆盖变成过覆盖。SPCI 为
  `0.721186/0.622433/0.498736`，三折均更欠覆盖，不能因区间窄或 score 低而晋级。
- 产物为 timeline/metrics/pairwise/manifest `20,664/108/144/1`；SHA-256 分别为
  `8857e77a96cba8ad2ae011a822759c0c08cc65e0c6fdab684b3e3fc0334df91e`、
  `dfc314041a2982456428b51dd4cd08c7b232706dea77ff77c200e3e76e620168`、
  `4e13a366bfe37b58d9692bdbefd23f4fa686ee731657460b21f04964662eccf8`、
  `229a26f5ec2c5a7082b14d18b8af7d21d44ba42f3b5b5d365b765f6fead4422f`；
  连续两次完整生成逐字节一致。
- 输出合同禁止 winner/rank/selection/promotion，所有正式、独立标签、确认性、
  Vajont 标志为 false。当前只支持把三种固定状态送入未来 E2 shadow 前瞻比较，
  不能从已查看 E1 结果直接晋升或继续搜索后回写 v1。完整方法、论文边界、数值、
  哈希与下一门禁见 `docs/ootang_prequential_calibration_bakeoff.md`。
- 最终独立 P0/P1 审计关闭了两个问题：issue 阶段现以字段白名单结构性排除所有
  reveal 数据，outcome lookup 只在 24 issue/hash 后构造；SPCI 加权分位数现排除
  零权极值并只在正权有限支持上重归一化。修复后的连续两次完整重放仍逐字节一致，
  最新审计无开放 P0/P1。
- 最终 calibration core/runner/pipeline 定向测试为 `13/13`、`4/4`、`28/28`；
  全仓 `584/584`（575.016 秒，0 failure / 0 error），Ruff、compileall、
  `git diff --check` 全部通过。v5 preflight `23/23`，E1 四哈希和 97 路径聚合
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`
  保持不变。

## 2026-08-26 E2-B2 机器 outcome 与 fixed-point cycle

- 新增两个显式且非默认阶段 `ootang-outcome-materializer` 和
  `ootang-prequential-cycle`，当前共 20 个可选阶段；无参数默认链仍严格是
  `features → convlstm → ootang-operational-v4`。cycle 的七步内部顺序固定为
  source ingest → bundle ensure → live reconcile → outcome materialize →
  live reconcile → issue produce → live seal。
- source current pointer 升级为 `ootang_source_current_pointer_v2`。每日 source
  revision 以 predecessor/sequence receipt 链保留，每次全局 snapshot 另写入内容寻址
  `ootang_source_snapshot_receipt_v1` 链；current pointer 必须精确对应唯一 tip。
  缺失或陈旧 pointer 只能从已完整验证的 tip 恢复，r1→r2→r1 回退、分支、
  孤儿、重复 sequence 或 object/receipt 篡改均 fail closed。旧的 pointer v1
  不做隐式迁移；实现、依赖或 epoch 合同变化也必须进入后续自动 rotation 协议。
- outcome materializer 只消费 current source 中已由 producer 递归验证的 finalized
  record，选择顺序固定为 source revision → sealed outstanding issue → 连续
  backfill，不从预测、score、当前日期或人工表格推导真值。每目标 revision
  通过内容寻址 exact object、带 sequence/predecessor 的 immutable receipt 链、唯一
  active receipt pointer 和 inbox 分层提交；在 receipt→pointer→inbox 任一崩溃窗口后
  都只能从已验证 tip/object 恢复，分支、回退、污染或同 revision 异语义拒绝。
  activation watermark 及更早记录/修订不会悄然改写旧 epoch，而是返回
  `waiting_epoch_rotation_required`。outcome 的机器时间由 invocation-scoped 单调
  sampler 贯穿初始读取、多 receipt 恢复、发布和阻断状态；跨链 `10→11→9→10`
  故障注入会撤销本轮所有公开 pointer/inbox，blocked status 保留最后成功观测时间。
- cycle 在外层只长持非阻塞 `cycle_lock`；调用子阶段时不预持
  deploy/runner 子锁，仅在收集 progress snapshot 时按 deploy → runner 短持锁以
  避免 torn snapshot。科学 token 绑定 source pointer、model manifest、verified ledger
  scientific projection、issue/outcome receipt tips、active bindings 与 inbox bytes；不绑定
  poll 时间戳、可变 status bytes、raw ledger head/event count 或持续失败的 anchor
  retry，因而无科学进展时可真正收敛。
- 空输入在一轮返回 `converged_waiting`；zero-byte 或有效 schema 但无 genesis
  的崩溃中间 ledger 作为可恢复 pre-genesis 状态，不会在 live 自动初始化前
  被 token snapshot 阻断；真正的 schema/hash 损坏仍 blocked。单次最多 64 个有科学
  进展的轮次，尚有合法 backlog 时以 `work_remaining/exit 0` 交回调度器；上次
  continuation token history 持久化后，跨调用回到已见 token 才会按振荡阻断。
- runtime 内外部 symlink alias、阶段 status 路径/schema/provenance 与非白名单状态
  均 fail closed；busy 统一为 exit 3，integrity 冲突为 exit 2。生产不允许注入假
  stage/token；仅显式 `OOTANG_E2B_ALLOW_TEST_CYCLE_OVERRIDE=1` 的测试环境可用依赖
  注入。所有 evidence/activation flags 仍固定 false。
- 最终验证：outcome materializer 31/31、cycle 23/23、cycle/main 50/50，E2-B2
  相关联合回归 197/197，全仓 566/566；Ruff、compileall 与 `git diff --check`
  均通过。独立对抗复审最终无 P0/P1。真实空 runtime 七阶段只生成 status/lock，
  一轮返回 `converged_waiting`，未生成 source pointer、activation、model、issue、
  outcome 或 ledger；无参数 dry-run 仍严格显示默认三阶段。
- 正式 v5 fail-closed preflight 23/23，报告仍为 G0 PASS、G1--G4 BLOCKED、
  G5a 未授权。E1 manifest/metrics/site/station SHA-256 仍分别为
  `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`、
  `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`、
  `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`、
  `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`；
  97 条保护路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 当前 cycle/deploy/live 配置 SHA-256 分别为
  `2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef`、
  `60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940` 和
  `bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00`。
- 该历史增量当时仍需 runner-independent checkpoint/input replay；它现已由顶部
  指定入口实现。当前仍需可信密码学时间、immutable automatic epoch
  registry/rotation、scheduler authorization，以及消除 receipt/ledger 链反复全扫的
  O(N²) 瓶颈。这些门禁不得被人工日期、冻结、批准、补签或伪造 backfill 取代。

## 2026-08-26 E2-B1 机器 source、bundle 与 issue producer

- E2-A 已提交为 `a4e7de2 feat: add autonomous prequential live ledger`。在其后新增
  三个显式且非默认阶段：`ootang-live-source`、`ootang-production-bundle`、
  `ootang-issue-producer`；默认链仍严格保持
  `features → convlstm → ootang-operational-v4`。
- source producer 严格接收 2020-06-30 之后的日连续 finalized JSON，只允许原始
  rainfall/RWL/八站 displacement，拒绝 duplicate keys、非有限值、负降雨、缺站、
  时间倒序、迟于下一自然日的 finalization 和日期缺口。在可信代码内重算
  `RWL_rate` 与 7/15/30 日雨量和，并物化 immutable content objects、推进式
  `source_current` 和 one-time no-clobber activation source。
- bundle producer 只从 immutable activation source 做 seeds 0--4 的全 as-of 固定
  120-epoch CPU 拟合，不选 best seed。checkpoint 是 tensor/primitive-only，使用
  `weights_only=True` 重载并递归核验 normalization、elevation、IDW/readout、state、
  source、schema、shape；training manifest 还绑定 deploy/base/producer、
  `pyproject.toml`、`uv.lock`、PyTorch 与 NumPy。保存/重载 tensor 精确一致，完整
  source 推理另显式固定并持久化 `1e-6 mm` reload tolerance。
- issue producer 递归复核 current/activation/model artifacts，只取 watermark 之前最后
  7 行，用五 checkpoint 内部生成 P50，并显式映射 model/live station order。目标
  必须是 E2-A ledger next target 且等于 current watermark + 1；有 outstanding issue、
  pre-genesis source 已越过 activation 或目标已进入当前自然日时均自动等待，不能
  跳日或回填。input manifest 与 issue 均内容寻址/原子发布；同科学语义复跑保留
  首次字节，冲突语义、篡改或 outcome 污染 fail closed。
- ledger 存在时 issue producer 不再信任可变 `status.json`：它在 E2-A runner lock
  内调用只读 verified projection API，完整验证 SQLite schema/hash chain 并科学重放，
  再核对 status 的 count/head/epoch/date/source/model，锁持有到发布结束。首次 issue
  还先写内容寻址 exact-byte object 和 atomic no-replace producer receipt；时间字段、
  receipt、object 或 inbox 任一篡改都拒绝，丢失 inbox 只能恢复首次登记字节。input
  manifest 同时绑定 deploy profile、issue producer、依赖锁与 Python/NumPy/pandas/
  PyTorch 版本，环境或实现变化不会被同目标幂等吞掉。
- source 非预期 I/O/CSV/竞态异常会规范化为 `SourceIntegrityError` 并 best-effort
  刷新 `blocked_integrity`，避免遗留旧 ready；source/bundle/issue 的 deploy lock 与
  E2-A runner lock 均采用非阻塞 busy 语义，bundle 不再无限等待锁。
- 最终对抗审计又收紧三条生产边界：source/bundle 的所有 runtime 子路径做 root 与
  symlink confinement，current pointer 必须精确绑定真实 immutable activation；
  checkpoint 从同一受限 bytes 同时完成 SHA 校验与 `weights_only` 加载，关闭路径替换
  TOCTOU；issue 在预测前要求 current latest displacement 与 ledger/activation 权威
  状态逐站一致，并在发布前后重采时钟。跨目标日起点的 issue 会在 runner lock 内撤出
  inbox，bundle 最终 manifest 的 write/post-check/revoke 窗口也持有 runner lock；
  receipt 崩溃恢复则先恢复已提交首发 bytes、再报告候选冲突。
- 配置只用 `*_implemented` 声明代码能力；状态只在真实操作成功后把
  `source_manifest_semantics_verified_by_producer`、
  `safe_checkpoint_loading_exercised`、
  `producer_checkpoint_inference_replayed` 设为 true。对能力边界的篡改由三个 loader
  分别拒绝。
- source/bundle/issue/E2-A/main 定向 136/136、完整仓库 505/505、正式 v5
  fail-closed preflight 23/23 通过；Ruff、compileall 与 diff-check 通过。显式四阶段
  dry-run 与真实空输入 poll 均通过；真实状态依次为 `waiting_for_daily_finalized_feed`、
  `waiting_for_semantically_validated_source`、`waiting_for_source_or_model` 和
  `waiting_for_production_bundle_or_source_snapshot`，未生成 source pointer、activation、
  model、issue 或 ledger；三项 E2-B 状态均绑定当前 deploy profile SHA
  `60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940`，等待态的
  semantics/loading/replay 实际执行字段均为 false。没有运行不存在前提的真实
  120-epoch bundle。
- E1 manifest/metrics/site/station 四项 SHA 与既有记录完全一致，97 条保护路径聚合仍为
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`；正式 v5
  仍为 G0 PASS、G1--G4 BLOCKED、G5a 未授权。
- E2-B1 当时固定 `e2_live_evidence_eligible=false`、`real_activation_ready=false`。
  后续 E2-B2 已完成 machine-only outcome materializer/cycle，顶部增量又完成指定入口
  runner 独立 checkpoint/input replay；剩余为可信密码学时间、immutable epoch
  registry/自动轮换、scheduler authorization 和 O(N²) 长链扫描优化；不得退回人工
  日冻结或人工签发。
- 设计、合同、feed 示例、运行命令与边界详见
  `docs/ootang_prequential_deploy_engineering.md`。

## 2026-08-26 E2-A 追加式 live 工程基础

- 新增显式阶段 `ootang-prequential-live`；默认链仍严格保持
  `features → convlstm → ootang-operational-v4`。该阶段单次机器 poll 后退出，
  由调度器重复调用，不需要人工逐日挑日期、冻结样本或批准状态更新。
- 新增纯函数在线数学核心和 SQLite WAL 追加式 ledger。所有写入使用
  `BEGIN IMMEDIATE` 与 `synchronous=FULL`，自然键同内容幂等、异内容拒绝，
  update/delete trigger 与完整 SHA-256 链共同 fail closed；issue 与 outcome 各自
  作为完整事务写入，8 点 issue 全部持久化并 seal 后才允许调用 outcome loader。
- runner 自动处理冷启动 genesis、无数据等待、自然日缺口、历史
  `backfill_not_blind`、外部锚请求/失败/回执、outcome reveal/score/state update、
  outcome revision 与回顾性重算。修订不回写历史事件或在线 StationState；若修订
  在下一次 issue 前到达，则机器使用最新正式位移作为 persistence 基线。
- 恢复不信任 settlement 自带状态：每次从 ledger 重放 genesis、issue、reveal、
  expert/conformal/drift 更新和 O1/O2/O3 site score，并校验事件顺序、站点顺序、
  状态哈希、输入/模型/配置/实现/环境哈希。回执文件丢失时从 ledger 自动重建，
  回执或 SQLite schema/链被改写时状态更新为 `blocked_integrity`。
- E2-A 仍是 `e2a_engineering_only_not_live_evidence`。当前 issue 预测来自外部预计算
  feed，尚未从五个 checkpoint 内部重放；input manifest 仅校验文件哈希，尚未校验
  语义；HTTPS 回执没有 pinned provider/密码学 verifier；epoch 变化仍 fail closed，
  尚无自动 registry/rotation。因此 profile/status 固定
  `input_manifest_semantics_verified=false`、`checkpoint_inference_replayed=false`、
  `trusted_anchor_receipt_verified=false`、`automatic_epoch_rotation_implemented=false`
  和 `real_activation_ready=false`。任何回执最多形成工程时序候选，E2 live evidence
  计数保持 0。
- live/core/ledger/main 定向测试共 64 项通过（live 20、ledger 12、core 7、main 25），
  冻结 G0--G4 gate 23/23 通过，全仓 414/414 通过；Ruff、compileall、显式/默认
  dry-run 和真实缺前提 poll 均通过。真实 poll 自动等待且不创建 ledger。E1 四项
  输出与 97 路径聚合哈希保持不变；完整命令和哈希见
  `docs/ootang_prequential_live_engineering.md`。
- 后续 E2-B1 已完成机器生成的 content-addressed 五种子部署 bundle 与 issue
  producer，E2-B2 又完成 outcome materializer 和 fixed-point cycle。仍缺 runner 独立
  checkpoint/input 重放、安全自动 epoch registry/rotation 和可信密码学时间。
  这些步骤都不得退回人工日冻结。长寿命部署前还需消除 receipt/ledger
  registry 反复全量扫描导致的 O(N²) 增长。

## 2026-08-26 全自动 prequential 机器闭环首版

- 新增显式阶段 `ootang-prequential-monitor`，消费固定 5-seed、3-fold 严格时序
  OOF P50 与 persistence，对 861 个 fold-date、8 个测点自动执行同日先 issue、
  后 reveal 的在线专家组合、双侧 conformal/ACI、单侧残差 anomaly、漂移重置、
  rewarm abstain 与 O1/O2/O3 连续空间聚合。算法不需要逐日人工挑段、选阈值、
  冻结样本或批准更新。
- 输出 `station_timeline/site_timeline/prequential_metrics/manifest` 分别为
  6,888/861/27/1 个记录文件；正常 point forecast 4,041 行、abstain 2,847 行、
  自动 drift 26 次。site 状态为完整 80 日、三个 block 均有可用子集 445 日、
  空间不完整 abstain 336 日。
- 三折 all-population MAE skill 相对 persistence 为
  `0.119925/0.132698/0.119456`，但 RMSE skill 为
  `-0.200329/-0.086410/0.038628`，没有稳定 RMSE 优势。active population 的
  RMSE skill 为 `0.106951/-0.035278/0.036118`，fold 2 仍为负。
- 目标 0.8 的区间覆盖率为 `0.791192/0.695061/0.630746`，后两折明显不足；
  abstain rate 为 `0.426394/0.391551/0.422038`。因此 v1 只作为内部回顾性科研
  监测器，不晋升生产；后续校准 challenger 必须新版本预声明，不能从已查看输出
  反复调参回写 v1。
- E1 使用 run-wide issue-only SHA-256 chain；runner 的 bundle validator 会从全零起点逐批
  重算链、校验 previous 链接、station/site 一致性和同 fold 跨日状态衔接。
  同日 actual 修改不改变当日 issue/hash，未来 actual 修改不改变因果前缀。
  runner 还从源 manifest 锁定的 commit `1e06629119e08b33ded2540a435e726c2d2da97a`
  取回 `data/features.csv` Git blob，核对 6,888 条 actual 和 6,888 条上一自然日
  persistence；staged CSV 以 `%.17g`/round-trip 重读后全量重放站点状态、
  site 聚合与 metrics，通过后才原子提升。
  这仍不是实时 append-only event ledger 或历史盲测；后续 E2-A runner 已实现，
  但不改变本条 E1 结果的证据等级。
- 最终产物 SHA-256：station
  `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`、site
  `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`、metrics
  `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`、manifest
  `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`；连续复跑
  完全一致。
- prequential 模块 19 项、合并定向 91 项、全量 373 项测试通过；`ruff`、
  `compileall`、`git diff --check` 与默认/显式 dry-run 通过。97 个受保护路径聚合
  哈希仍为 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
- 正式 v5 的 G1--G4 blocked 状态未被修改；机器支路也未生成颜色、灾害概率、
  event recall 或 FAR。其后的 E2-A 已实现 ledger/恢复/修订与不可信锚接口；真实
  激活仍需五种子推理 producer、输入语义、自动 epoch 和可信时间验证，而不是回到
  人工逐日冻结。

## 2026-08-20 G1--G4 证据盘点与机器预检（仅 formal-v5 确认路径）

- 已完成标签、时间暴露、V0 unavailable 覆盖和指标门槛的只读盘点：当前 `G0=PASS`，`G1--G4=BLOCKED`。详细证据、待决策项与建议契约记录在 `docs/v5_g1_g4_preflight.md`。
- G1 无法关闭：仓库中没有独立五级现场真值、事件文件或标签 manifest。区间代理标签、v4/v5 规则等级、历史 onset/SHAP 规则标签以及已查看模型输出的专家复算都不得冒充独立真值；`unknown` 必须保留，不得转为阴性/绿色。
- G2 无法关闭：当前藕塘物化数据仅覆盖 2016-07-01 至 2020-06-30；model fit 为 2016-08-06 至 2019-02-02，自动 V0 selection window 则按实际 runner 合同覆盖 2016-07-01 至 2019-02-02（每站 947 行），二者不得混写。calibration 为 2019-02-03 至 2019-09-17，historical test 为 2019-09-18 至 2020-06-30；滚动验证还已将 2018-02-21 至 2020-06-30 依次暴露为外层 test，因此现有范围无法重切出 unseen 确认块。
- G3 当前覆盖仅 MJ1/MJ3：2/8 站、1,028/4,112 点日，均为 25%；两站均属 O1，当前正式 site 发布为 0/514 日。已记录 fail-closed 底线：unavailable 一律 abstain/not-applicable，禁止绿色化、插补、跨站借值、v4/常数回退、缺失后权重重归一和事后放宽分段门；部署范围、覆盖分母、site 行为和数值门槛仍待批准。
- G4 只冻结了候选指标和区间方案：共同主指标为事件召回与每 100 个阴性发布单位日的 FAR，对比 B0--B5；97.5% 单侧 exact Clopper--Pearson 事件召回下界和配对移动日期块 bootstrap 仅为未批准建议。`R_min`、`FAR_max`、各层 `coverage_min`、fusion `delta_min`、FAR 非劣效界与最低支持量均保持 null。
- 新增 `config/ootang_v5_gate_register.v1.json` 作为 G0--G4 机器状态权威源，`code/warning/ootang_v5_gate_preflight.py` 以严格 schema、路径和 SHA-256 校验 fail closed。v1 是不可就地升格的 blocked snapshot；解除 blocker 需新 schema/version、交叉哈希 manifest、对应 validator 和审查后的默认源切换。`--report` 仅报告，`--require-g0-g4`/默认模式在当前状态下必须拒绝；CLI 和生产 guard 均不允许用 path/root 覆盖默认源。G0--G4 全 PASS 也只是 G5a 必要条件；正式 G5a 还需独立版本化 run contract 及专用 guard，当前均未实现。
- 新预检模块 23 项、合并定向回归 71 项和全量 353 项测试全部通过；`ruff`、`compileall`、`git diff --check`、默认/显式 dry-run 均通过。97 个保护路径及共享 v4 Bai--Perron 源无工作树漂移。
- 本轮没有读取标签 payload，没有启动 NGBoost 训练/推理或融合，没有生成预警颜色，也没有读取或修改 Vajont 工作簿与 `review.md`。

## 2026-08-20 自动 V0 数值审计

- ATU3/MJ9 的 `negative_segment_sse` 已确认为全历史 float64 前缀原始矩相减引起的灾难性消减：失败窗口的闭式 SSE 为负，但直接残差平方和与 60 位 Decimal 参考均非负；日期严格递增且设计矩阵满秩。
- 自动 V0 wrapper 统一改为“每个候选段从自身起点重定时间/位移原点，再计算局部充分统计量”，随后执行相同的最小段长、DP、BIC 和首段选择规则。共享 v4 Bai--Perron 文件保持逐字节不变，未采用放大容差、全局均值中心化或只对失败站点重试的回退方案。
- 八站重新生成后，原六站的分段数、边界和状态不变；ATU3 改为可审计的 `first_break_is_not_accelerating`，MJ9 改为 `nonpositive_initial_segment_slope`。候选仍仅 MJ1/MJ3，V0 仍为 `0.25034204499655494/0.24813126112408163 mm/day`。
- 自动 V0 分段表由 36 行增至 48 行，完整保存八站各 6 个 BIC 选定段；v5 展示仍为 4,112 行，其中 1,028 行可用、3,084 行 not applicable。97 个 v4/ConvLSTM/NGBoost/模型保护文件在显式重生成前后哈希完全一致。
- v5 展示对上游自动 V0 改为完整 fail-closed 验证：profile/method、V0 公式、runner/shared-BIC、kinematics/predictions/forecast/v4 血缘和八站 fit 截止日任一缺失或漂移均拒绝；对应篡改负测已纳入 48 项定向与 330 项全量门禁。
- 根因、最小复现、假设检验和验收证据持续记录在 `docs/v5_v0_numerical_audit.md`；正式 v5 的标签、切分、指标、覆盖率和融合决策门记录在 `docs/v5_validation_protocol.md`。数值门关闭不解除其余正式验证门禁。
- 验证协议将运行前授权与运行后候选验收严格分开：G5b/G6b 不得作为生成自身证据的前提。G0--G4 机器预检也不会自动授权 G5a，避免用一个 register 布尔值跳过未实现的运行契约。

## 2026-08-18 v5 候选展示

- 新增显式阶段 `ootang-v5-candidate-display`，只读取既有自动 V0、藕塘运动学、ConvLSTM 原始预测和 v4 历史参考产物。默认链仍为 `features → convlstm → ootang-operational-v4`；本阶段不训练或保存模型，不调用 v4 融合，不读取 Vajont。
- `candidate_timeline.csv` 完整保留 514 日 × 8 点共 4,112 行。MJ1/MJ3 共 1,028 行标记 `candidate_available`，显示自动 V0 相关速度比和连续切线角；其余 6 点共 3,084 行标记 `not_applicable_v0_unavailable`，不补 V0 或候选字段。所有行仍保留原始速度、`ΔV`、区间状态和明确命名的 v4 历史参考列。
- 新增 `figures/v5_candidate_display_ootang_v1/`：候选时间表、8 点摘要、MJ1/MJ3 五面板图、六点 unavailable 状态表和 manifest。产物固定 `candidate_display_only=true`、`ngboost_inference_output=false`、`v5_fusion_output=false`、`formal_warning_output=false`；因此不构成新的 8 点预警颜色或 v5 综合结果。

## 2026-08-18 自动 V0 候选诊断

- 新增显式阶段 `ootang-auto-v0-direct-bai-perron`，只使用藕塘 fit 累计位移和真实时间轴做自动 BIC 分段线性选择；不人工选段、不回退 KMeans、不使用严格 MVIF 失败结果、不读取 Vajont，不改写 v4/ConvLSTM/NGBoost。
- 8 个测点均输出候选记录；MJ1/MJ3 状态为 `initial_segment_selected`，候选 V0 约 `0.2503/0.2481 mm/day`。本节初跑时 ATU3/MJ9 因数值分段失败而 unavailable；2026-08-20 数值审计修复后，ATU1--ATU5 均因首个断点不满足“后一段更快”而 unavailable，MJ9 因首段斜率非正而 unavailable。没有人工补选。
- 新增 `figures/auto_v0_direct_bai_perron_ootang_v1/`：候选表、分段审计表、8 点诊断图和 manifest。该 V0 仅为 v5 候选，不进入 v4 阈值或 NGBoost 主输入；报告已在现有 `paper/process_report.tex` 中精简更新。

## 2026-08-18 NGBoost 四指标分组消融

- 在已冻结的 h=1/3/7、五级区间代理标签、固定 NGBoost 参数和 fit-only 协议下，新增七组预声明输入：full、interval-only、分别去掉区间/速度/`ΔV`/切线角，以及去掉 station one-hot。共完成 21 次固定拟合，不调参、不排名、不选择特征集，也不保存消融模型。
- 消融输出 85,120 条 calibration/test 预测、4,116 条 NGBoost 指标、2,100 个混淆矩阵单元、5,040 条可靠性数据和 840 条相对 full 的原始差值。full 配置在三个 horizon 上与已提交敏感性逐项一致。
- 去掉 `interval_z` 后，六个 horizon×split 的全时刻 macro-F1 下降约 `0.215–0.699`、ordinal MAE 增加约 `0.343–0.913`，说明当前区间代理任务由区间持续性主导；该结果受标签定义影响，不能写成区间是物理主控因素。
- `interval_only` 的全时刻结果常接近 full，但在六个状态转移集合中 macro-F1 和 ordinal MAE 均差于 full，说明非区间运动学指标主要对状态变化提供增量。去掉 `ΔV` 后，转移行 ordinal MAE 在六个时段全部恶化，macro-F1 在五个时段下降；这是当前最一致的单项增量证据。
- 分别去掉速度、切线角或 station one-hot 的变化较小且方向混合，只能提示信息冗余或弱依赖，不能证明这些变量没有联合价值或模型可跨测点迁移。完整结果见 `docs/ootang_ngboost_interval_proxy_feature_ablation.md`。本轮仍不支持 NGBoost 进入默认主流程。

## 2026-08-17 NGBoost 区间代理 pilot 与提前量敏感性

- 新增显式阶段 `ootang-ngboost-interval-proxy-pilot`，默认链仍严格为 `features → convlstm → ootang-operational-v4`。本阶段只读取既有藕塘 ConvLSTM、逐点运动学和 v4 比较基准，不重训/修改 ConvLSTM，不修改 v4，也未读取或引入其他案例。
- 使用当前 `interval_z`、逐点速度、原始 `ΔV` 和连续切线角四项指标，加 8 个测点 one-hot 控制量，预测下一自然日的五级原始区间偏离代理状态。物理加速度、环境变量、校准后区间和 v4 融合等级均未进入输入。
- 严格同测点、同 split、一日配对得到 fit/calibration/test=`7280/1808/2288`，目标五级支持分别为 `3538/2870/701/83/88`、`531/847/287/143/0`、`584/1039/182/133/350`。calibration 无 red，相关指标明确记为不可定义。
- 固定 `NGBClassifier` 五分类参数，只用 fit 训练，不做搜索、早停、重拟合、类别权重、SMOTE、合成标签或事后概率校准。calibration/test 全时刻 accuracy 为 `0.906/0.947`、macro-F1（支持类）为 `0.905/0.925`；状态持续基线分别为 `0.916/0.952` 与 `0.923/0.936`，NGBoost 未超过简单持续性基线。
- 状态转移行上 NGBoost calibration/test accuracy 仅为 `0.132/0.209`；test 的 macro-F1 `0.229` 和 ordinal MAE `0.791` 优于多数类基线的 `0.083/1.527`，但 calibration 未稳定复现。因此该模型只保留为探索性概率 pilot，不引入主流程，不改动既有模型或论文结论。
- 版本化产物位于 `figures/ngboost_interval_proxy_pilot_ootang_v1/`，模型为 `models/ootang_ngboost_interval_proxy_pilot_v1.pkl`，完整边界与结果见 `docs/ootang_ngboost_interval_proxy_pilot.md`。
- 在模型、四指标、测点控制量、训练策略和类别处理完全相同的条件下，新增显式 h=1/3/7 提前量敏感性。fit/calibration/test 样本分别为 h1 `7280/1808/2288`、h3 `7264/1792/2272`、h7 `7232/1760/2240`；三个 fit 均含五类，三个 calibration 均无 red。
- h1/h3/h7 的 calibration 全时刻 accuracy 为 `0.906/0.781/0.715`，对应持续基线为 `0.916/0.833/0.744`；test 为 `0.947/0.860/0.675`，对应持续基线为 `0.952/0.876/0.773`。三个 horizon 的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过持续基线，且 log loss、Brier、ECE 随提前量增加而整体升高。
- 状态转移行信息随提前量增加而增多，部分转移指标优于简单基线，但没有在 calibration/test 和不同指标间稳定一致。敏感性产物明确 `selection_performed=false`、`ranking_performed=false`，不输出最佳 horizon，不改变当前“不引入主流程”的判断。完整结果见 `docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md`。

## 2026-08-15 已退役产物清理

- 按用户决定删除已退役路线的版本化产物，只保留当前 v4 链所需目录。删除 `figures/` 下 `ngboost/`、`warning_fusion/`、`warning_onset/`、`thresholds/`、`sensitivity/`、`warning_draft/`、`warning_operational_draft/`、`warning_operational_draft_v2/`、`warning_operational_draft_v3/`、`warning_review/` 共 82 个跟踪文件，另删 `pipeline/latest_run.json`（v3 阶段残留记录）与 `pipeline/shap_stability_run.json`（已退役 `shap-stability` 阶段）。
- 删除前已核验：这 10 个目录在 `code/`、`main.py` 和 `tests/` 中引用数均为 0；v4 链只读 `figures/convlstm/`，写 `figures/warning_draft_v4/` 与 `figures/warning_operational_draft_v4/`。删除后 v4 核心 manifest 的 13 个路径 SHA-256 全部匹配，`main.py --dry-run` 仍精确为 `features → convlstm → ootang-operational-v4`。
- `figures/tangent_angle/` 未删：`features` 阶段仍向其写出 `uniform_rates.csv`，删除会打断默认管线。`figures/shap/` 及 `shap/stability/` 未删：仍被 `paper/process_report.tex` 引用。
- 本次清理不改动任何 v4 数值、阈值、模型或协议内容哈希，也未启动 Vajont。删除项一律按 Git 历史（提交 `7d2e38b` 及之前）恢复，不在当前目录重建同名文件。
- 副作用：`ootang_stable_segment_expert_review.md` 与 `ootang_interval_calibration_expert_review.md` 内嵌的审查图和支撑 CSV 链接已失效，两份文档的文字结论仍有效。

## 2026-08-13 当前代码树与解释支路同步

- 当前可执行最小链严格为 `features → convlstm → ootang-operational-v4`；旧 30 日 `V0` 标签、旧预警融合 v1/v2/v3 运行入口和对应测试已从工作树移除，仅保留在 Git 历史；这里不指后续新增的 machine-prequential cycle v1/v2/v3。
- 独立解释支路改为 NGBoost 回归 + SHAP：目标是下一观测位移增量，输出候选模型依赖；它不是 ConvLSTM-SHAP、因果主控因素识别或正式五级预警分类。
- v4 数值产物重跑后仍为 4,112 条测点记录、514 条滑坡体记录和 8 行阈值；加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`。代码清理只更新来源指纹和解释产物，不改这些 v4 数值。
- 当时未启动正式 NGBoost：缺少独立五级现场结局，且不能把当前四指标规则输出作为标签再以同一输入训练。2026-08-30 已将这一限制收窄到确认性灾害效能；使用开发期自动未来变形状态标签的科研原型继续推进。
- 本节之后的 v1/v2/v3、旧 SHAP/分类和历史测试数量均为时间戳所示的历史记录，不描述当前入口。

## 2026-08-11 v4 严格逐点加速度扩展收口（阈值来源于 2026-08-13 澄清）

- 导师确认逐点导数方法及“相同阈值”。经课题组内部方法核对后，v4 沿用速度 `V0` 基线形式和 `1×/5×/10×` 相对结构，而非不存在的严格加速度阈值表：以加速度自身 `A0` 量纲一致转置，`a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，真实 `dt`，单位 `mm/day²`，三点 warmup；raw `delta_v` 仍只作审计。
- fit-only 稳定段阈值固定为 `A=mean(a)`、`sigma_a=sample std(ddof=1)`、`A0=max(1.5A,A+2sigma_a)`；`A0<=0` 或非有限时 fail-closed。五级为 green `<A0-sigma_a`、blue `[A0-sigma_a,A0+sigma_a]`、yellow `(A0+sigma_a,5A0)`、orange `[5A0,10A0)`、red `>=10A0`。
- v4 使用 O1/O2/O3 双轴空间规则（实现最初形成于 v3 草案，但当前只由 v4 调用）；速度/切线角仍是同一运动学 family，加速度独立计票。8 点、514 日输出已复算：测点加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`，滑坡体整体 green/blue/yellow/orange/red=`8/48/31/9/18`，`valid=114`、`candidate_not_site_confirmed=400`。
- v4 核心与图件 manifest 均绑定源码指纹、输出哈希与行数、v1 基础协议及 v2 扩展协议双哈希，并保留 `formal_warning_output=false`、`vajont_used=false`。默认入口现为 `features → convlstm → ootang-operational-v4`；v3 数值仅为保留的历史快照，不再有可执行对照入口。NGBoost 正式预警模型仍未完成，Vajont 未启动。
- 本轮不改变既有 v3 核心数值 CSV；共享 runner 源码哈希变化仅刷新 v3 manifest/图件 provenance，未将 v3 数值混入 v4 阈值。

## 2026-08-08 代码库审查与工程收口

- 这是 2026-08-08 的历史工程收口记录：当时 v3 及其余阶段仍为 explicit-only。2026-08-13 后，旧运行入口与专属代码已移至 Git 历史；保留的 MVIF、6 通道和旧预警产物仍不混入当前主结果。
- 44 个测试文件已纳入 Git。当前全量门禁为 `361 passed`、`52 subtests passed`；Ruff、Python 编译检查和 `main.py --dry-run` 均通过。该门禁证明工程快照可复核，不证明藕塘数据具备确认性证据或正式预警有效性。
- 统一入口清单升级为 schema 3，逐阶段保存输入/输出路径、大小、SHA-256、源码指纹和工作树状态。当时的 `latest_run.json` 已于 2026-08-15 作为 v3 残留记录删除；当前 HEAD 尚无端到端运行清单，下次完整运行会重新生成。
- v2/v3 配置锁定的 Wang 论文 PDF 只作为空间拓扑来源证据，不是计算输入；本地副本存在时必须匹配锁定摘要，缺失时允许原型计算并在运行清单记录未核验状态，错误副本会 fail-closed。v2 历史清单未因本次代码审查统一刷新，不应据此声称所有历史字段均已更新。
- 本次没有重新训练模型、改动数值产物或启动 Vajont。Vajont 仍须用户明确授权；后续若获准，必须先冻结其角色并建立独立数据/评价目录。

本节是工程收口记录，不替代 2026-08-04 的 7 通道科学结果，也不解除 `confirmatory_evidence_gate=blocked`、`formal_warning_output=false` 或最终论文门禁。

## 当前阶段

| 项目 | 状态 | 可核对产物 |
| --- | --- | --- |
| 历史十三阶段统一管线 | 已完成（加入高程前的历史快照） | 旧运行记录中的 13/13 阶段与产物哈希；不代表当前高程感知模型已重跑全部历史诊断 |
| 藕塘高程感知最小链路 | 已完成初跑；v4 为当前默认草案 | v2/v3 数值快照保留；v4 产物见 `figures/warning_operational_draft_v4/` |
| 藕塘阶段性结果包 | 已完成 | `docs/ootang_stage_results_package.md` 统一汇总可写/不可写结论、证据门禁和后续数据决策 |
| 代码目录按研究流程分组 | 已完成 | `code/features/`、`code/warning/`、`code/explainability/`、`code/convlstm/`；入口路径已在 `main.py`、`README.md` 和 `docs/design.md` 同步 |
| ConvLSTM 高程静态通道 | 已完成初跑 | `elev_m` 标准化后经水平 IDW 形成静态网格；`figures/convlstm/forecast_run_manifest.json` 记录坐标哈希和处理方法 |
| ConvLSTM 日历后置校准 | 已完成（当前单次初跑） | `figures/convlstm/forecast_calibration_metrics.csv`；不证明上游日值生成独立 |
| ConvLSTM 配对日期块 95% 区间 | 已完成 | `figures/convlstm/forecast_bootstrap_ci.csv` |
| ConvLSTM 7 通道 fixed-120 诊断 | 三折滚动与五种子已完成；早停/容量未运行 | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/`；历史 6 通道根目录产物只作对照，不是 7 通道证据 |
| 高程与空间预警专家审查 | 已完成 | `docs/ootang_elevation_warning_expert_review.md`；400 日成因、典型日、课题组内部方法边界及高程可信性已核对 |
| v2 空间融合覆盖门禁 | 已修复 | `minimum_assessable_station_count=3` 先于全部颜色执行；2 个跨区 yellow 点反例及 v2 兼容语义均有测试 |
| 滑坡体 green/blue 语义 | v3 草案已实现并复算 | 双轴输出整体确认等级与局部最高候选；green `8`、blue `48`，局部 blue 关注 `8` 日 |
| 全时刻预警状态展示 | 已完成（非正式、观测后） | 514 日 × 8 点候选色带及 `site-confirmed/local maximum` 双轴；400 个 NC 明确不是缺测 |
| 位移—四指标—最终等级联合图 | 已完成（非正式、观测后） | 4×2 小多图覆盖 8 点 × 514 日，逐点对齐累计位移和五条状态带 |
| SHAP 跨折稳定性与特征组消融 | 已完成 | `figures/shap/stability/`；固定 5 折、5 个特征组和任务专属主指标 |
| 藕塘数据血缘 | 已审查并拆分门禁 | `source_recovery_status=unavailable_by_project_constraint`；原型初跑允许，确认性证据与正式预警阻断 |
| 新神经调参/机理消融与正式日预测 | 暂停 | 7 通道 fixed-120 结果已查看，不据此优化；早停/容量未运行，自然月分段三次结构仍限制确认性解释 |
| Vajont 案例 | 未启动 | 本轮 fixed-120 未读取、未适配、未运行；此前仅做过只读内容盘点，不构成启动，开始前必须获得用户明确许可 |
| NGBoost 区间代理 pilot | 已完成显式初跑；不进入默认链 | 11,376 条一日配对、五级概率与基线比较；calibration/test 未超过状态持续基线 |
| NGBoost h=1/3/7 提前量敏感性 | 已完成显式、非排名初跑 | 同一模型与输入并列报告；三个 horizon 全时刻 accuracy、macro-F1、ordinal MAE 均未超过持续基线，不选择最佳提前量 |
| NGBoost 四指标分组消融 | 已完成 21 次固定拟合；不保存模型 | 区间主导代理任务；`ΔV` 对状态转移的增量最一致；不排名或选择特征集 |
| 自动 V0 数值审计 | 已关闭 | ATU3/MJ9 不再因负 SSE 提前退出；八站均完成数值分段，候选仍仅 MJ1/MJ3；共享 v4 与 97 个保护文件不变 |
| v5 G0--G4 机器预检 | v1 冻结快照；G0 PASS、G1--G4 BLOCKED | 无独立标签与 unseen 块；V0 覆盖 25%；数值门槛仍 null；不授权 G5a |
| 自动 V0 候选诊断 | 已完成显式初跑；2/8 点可用 | MJ1/MJ3 形成 fit-only 候选，其余 6 点 unavailable；不人工补段、不写入 v4 |
| v5 候选展示 | 已完成显式初跑；不形成融合结果 | 4,112 行保留全部 8 点；MJ1/MJ3 可用、其余 6 点 not applicable；无 NGBoost 推断、颜色或模型输出 |
| NGBoost 未来 onset 正式调参 | 暂停（旧 onset 目标） | 当前仅 3 个互不相连的旧 onset 标签事件；该结论不阻塞 2026-08-30 新增的“未来 7 日多变量变形状态”自动标签实验 |
| 正式切线角/V0 覆盖 | 待导师或现场资料决定 | 自动 fit-only 候选仅覆盖 MJ1/MJ3；其余 6 点保持 unavailable，尚无可提升为正式 V0 的独立验证 |

## 当前滚动验证协议

1. 保持现有 ConvLSTM 结构、7 日输入和 1 日预测步长，不更换模型。
2. 使用 3 个扩展窗口折，每折测试 287 个连续日，测试段互不重叠。
3. 每折训练段末 20% 作为日历上后置的 calibration 期；标准化、增量尺度和测点 `qhat` 只使用该折允许的表格历史行。
4. 每折报告总体和逐测点误差、持久性基线、区间覆盖率、宽度、pinball loss 和 interval score，不只报告跨折均值。
5. 当前物化序列和留出时段已参与多轮分析，且上游生成独立性未知；滚动结果仅作探索性内部时间验证，不作为外部确认性证据。

> 本协议已于 2026-08-04 用当前 7 通道 fixed-120 完成三折滚动和五种子诊断。早停与容量敏感性没有随本轮运行；2026-06-21/22 的对应记录均为历史 6 通道证据。

## 2026-08-04 7 通道 fixed-120 三折与五种子记录

- 按运行前冻结协议完成 `seed=0` 三折滚动和 `seed=0-4` × 3 折的 15 个拟合；没有挑选最佳种子，也没有让测试折参与选择。
- rolling 与 five-seed 阶段的 `seed=0` 折元数据、54 行指标和 6,888 行预测在 `1e-12` 容差内复现；五种子保留 15 行运行、270 行指标、1,800 行训练记录和 34,440 个唯一完整的 `seed × fold × date × station` 预测键。
- fold 1/2 的 RMSE 和 MAE 对 5/5 种子均劣于持久性基线；平均 RMSE 分别为 `1.970/0.356 mm`，基线为 `0.245/0.120 mm`。
- fold 3 对 5/5 种子的 RMSE/MAE 仅小幅改善：平均 RMSE `0.328 mm`，基线 `0.340 mm`，平均 RMSE skill `0.036`。但预测增量标准差比仅 `0.156`，平均增量相关 `-0.041`，仅 1/5 种子为正；该优势伴随强平滑，不是稳定的逐日动态跟踪证据。
- 校准后 P10–P90 coverage 在三折为 `0.387/0.956/0.754`；fold 1 欠覆盖、fold 2 过覆盖、fold 3 略欠覆盖，不能只报 coverage 而忽略宽度和 interval score。
- 本轮是藕塘公开物化日序列上的内部探索性诊断。历史 6 通道对照只能描述版本变化，不能当作 7 通道证据或高程因果消融。
- 7 通道早停和容量敏感性未运行；Vajont 也未启动，后续开始必须先得到用户明确许可。
- 版本化产物位于 `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/`，管线清单为 `figures/pipeline/convlstm_elevation_fixed120_v1_run.json`，完整审查见 `docs/ootang_convlstm_elevation_fixed120_review.md`。

## 2026-08-04 藕塘原型文档收口记录

- 已同步 `README.md`、方法/设计/框架状态、结果、限制、进度、图件说明及 `main.py` 阶段契约；当前 ConvLSTM 主结果统一为 7 通道 fixed-120 三折 × 五种子，历史 6 通道滚动、早停和容量结果均明确隔离。
- 当前 7 通道最后一折 `seed=0` 的 14 日时间块结果已保留：模型相对持久性基线的 RMSE/MAE 差异 95% 区间均跨 0；三折 × 五种子 bundle 尚未扩展为逐折逐种子的全面 bootstrap。
- 文档已统一报告站点异质性、强平滑、区间失配及物化日序列血缘限制；该同步完成的是内部原型记录，不解除 `confirmatory_evidence_gate=blocked`，也不把结果升级为正式预警证据。
- Vajont 本轮未启动；如用户以后明确允许，须先冻结其外部验证、补充案例或方法演示角色，再建立独立数据与评价协议。
- 提交前全量门禁为 `355 passed`、`45 subtests passed`；2 项失败仍是旧 `V0` 方法名和旧切线角列断言，未出现本轮新增回归。科学证据轴与规范轴独立审查均为 P0=0、P1=0。

## 2026-08-01 v3 空间规则实施记录

- 修复 v2 的 P0 覆盖门禁：少于 3 个可评估测点时，任何 site 颜色都不能返回；v2 的“全分区仅约束 green”历史语义保持不变，当前 v2 四份产物 SHA-256 未改变。
- 新增独立 `ootang-operational-spatial-v3` 配置、融合模块、运行入口和 `figures/warning_operational_draft_v3/`（该目录已于 2026-08-15 删除，仅存于 Git 历史），没有覆盖 v1/v2。
- v3 将 `site_confirmed_level` 与 `local_max_candidate_level` 分轴。所有 site 颜色先要求至少 3 点并覆盖 O1/O2/O3；blue 也要求至少 2 点跨 2 区；未确认 yellow–red 不降级；孤立/单区 blue 记为 site green + `localized_blue_attention`。
- 514 日仍有 `valid=114`、`candidate_not_site_confirmed=400`；整体确认色为 green `8`、blue `48`、yellow `31`、orange `9`、red `18`，另有 400 日不发布整体颜色；局部最高候选为 blue `56`、yellow `196`、orange `111`、red `151`。
- v2/v3 的 4112 条测点时间线新增 `trend_component`、`transition_status`、`evidence_consistency_status` 和 `composite_warning_signal`：`ΔV` 三态现在改变完整信号和理由，但不改变五色候选，也不作为速度/切线角之外的独立投票。候选色和 514 日滑坡体统计保持不变。
- 已将六个冻结语义的代表日诊断纳入同一 v3 阶段，输出可编辑 SVG、PDF、300 dpi PNG 与 provenance manifest；图中未确认 site 显式为 `NC`，并逐日列出确认支撑、局部最高测点和 O1/O2/O3。
- 已加入 514 日完整时间线图：上半图覆盖 8 点全部候选状态，下半图并列整体确认与局部最高；400 个未确认日以灰色 NC 表示且明确为“非缺测”。
- 已加入 8 点联合诊断图：每点显示累计位移，以及 interval、velocity、`ΔV` 三态、tangent angle 和 final candidate；三类 v3 图件共用带源码指纹的公开 provenance/导出支持层。
- 所有 v3 产物继续标记 `operational_draft_not_formal`、`formal_warning_output=false`、`vajont_used=false`。

## 2026-07-30 高程感知初跑记录

- 用户确认原始 GNSS 无法取得，导师要求先使用现有公开藕塘序列和 `data/station_coords.csv` 的高程完成案例跑通；藕塘不一定用于最终论文。
- 不删除原有来源审查，而是拆分为：

  ```text
  source_recovery_status = unavailable_by_project_constraint
  prototype_run_gate = allowed
  confirmatory_evidence_gate = blocked
  formal_warning_output = false
  ```

- 修复了此前 `elev_m` 未进入 ConvLSTM 的实现落差。当前采用“8 点高程 z-score → 按 `x_m/y_m` 水平 IDW → 静态高程通道”，不把高程直接并入三维距离；输入由 6 通道变为 7 通道。
- 最小链路三阶段全部通过，最终复跑耗时约 `40.7 s`。预测表包含 fit `7288`、calibration `1816`、test `2296` 条测点记录，主键无重复。
- 最后 287 日物化 test 段总体 RMSE 为 `0.338 mm`，持久性为 `0.340 mm`，RMSE skill 为 `0.007`；校准后 P10–P90 覆盖率为 `0.770`。流程已通，但没有明显优于简单基线。
- 与提交前的无高程单种子快照相比，总体 RMSE 约由 `0.318 mm` 增至 `0.338 mm`，平均逐点 RMSE skill 由约 `0.082` 降至 `0.019`。这是事后描述，不用于反向调节模型或高程尺度。
- v2 输出包含 `4112` 个测点—时刻和 `514` 个滑坡体时刻；四项输入均无缺失。`114` 个时刻满足当前项目特有空间确认，`400` 个保留为 `candidate_not_site_confirmed`，不得并入 green。
- 所有当前产物继续标记为原型/非正式，Vajont 未读取、未运行，且启动前必须得到用户明确许可。

## 2026-07-30 高程与空间预警专家审查

- 审查报告见[`藕塘高程通道与空间预警结果专家审查`](ootang_elevation_warning_expert_review.md)。
- 高程作为静态地形先验可提高输入结构的物理合理性，但课题组内部方案的“物理引导”实际来自稳定性系数和半经验物理位移，并使用 GCN/T-GCN/ST-GCN；当前高程 ConvLSTM 是项目改造，不是该方法的复现。
- 在相同 `11400` 个预测键、观测和 persistence 下，高程版相对无高程单种子快照的 test RMSE/MAE 分别增加 `0.0196/0.0158 mm`；14 日配对块重采样的差值区间均高于 0。由于 test 已查看且只有单种子，该结果只是否定当前已显示提升，不构成确认性消融。
- 400 个未空间确认日全部为 8/8 测点和 3/3 分区有效，并非缺失：`189` 日不足 2 个 yellow+ 点，`211` 日已经达到至少 2 点但仍全部位于 O1。
- 对应 `755` 条 O1 yellow+ 测点记录的候选等级全部由区间指标决定；当前 orange/red 不能解释为速度或切线角达到同级。
- v2 的 514 日 site 输出没有 green，说明“任一 blue 即 site blue、8 点全 green 才 site green”不适合把绿色作为常态；该审查建议已于 2026-08-01 通过全局门禁修复和独立 v3 双轴草案落实。
- 本轮没有调整阈值、模型或 test，也没有读取或启动 Vajont；Vajont 仍受用户明确许可门禁约束。

## 2026-07-28 数据血缘审查记录

- 仓库 `monitoring_data.xlsx` 与 Wang 等（2025）Figshare 文件 MD5 完全一致；CSV 与工作簿 1461×17 的日期、列和数值等价。
- 8 条位移和 GWT 在 48/48 个自然月内呈三次指纹，5 个环境负对照为 0/48；月内第四差分无断点，断点集中在自然月边界。
- 首个模型目标、fit→calibration、calibration→test 三个边界均切穿同一月内三次段；跨边界恢复只作为代数依赖诊断，不写成预测性能或已证实未来泄漏。
- Figshare 的 11 个公开 notebook 没有生成该结构的代码，也没有公开原始 GNSS/GWT 锚点、日值处理链或 MJ/ATU 映射。
- 原始数据恢复现已确认不作为当前可执行路线；历史事实仍保留。
- 当前 `prototype_run_gate=allowed`、`confirmatory_evidence_gate=blocked`、`formal_warning_output=false`、`vajont_used=false`；计划中的机理性神经消融仍暂停。
- 典型状态日和结果可解释性审查已经完成；v2 门禁与不覆盖 v2 的 v3 green/blue 双轴草案也已完成。下一步等待最终论文数据集选择；若换数据集，重新建立数据契约和确认性验证协议。

## 本轮完成门槛

- 输出逐折计划、逐日预测和逐折/逐测点指标 CSV。
- 测试覆盖时间隔离、测试段不重叠、固定协议和管线产物契约。
- 同步更新 README、设计、研究框架、结果、限制和本进度文档。
- 全量测试、Ruff、编译和完整管线通过；运行清单中的源码及产物哈希可复核。

## 2026-06-21 历史 6 通道滚动验证记录

- 三个测试折均为 287 日且互不重叠，输出已通过固定种子逐字节确定性复跑。
- 模型/持久性 RMSE：折 1 为 2.123/0.245 mm，折 2 为 0.492/0.120 mm，折 3 为 0.318/0.340 mm。
- 逐测点 RMSE 优于基线数量：0/8、0/8、8/8；当前 ConvLSTM 不能表述为跨时期稳定优于持久性基线。
- 校准覆盖率：48.8%、94.9%、75.2%；第二折覆盖率上升伴随区间过宽和 interval score 恶化。
- 全量门禁：135 项测试和 32 个子测试通过；Ruff、编译、CSV 完整性及有限数检查通过。
- 当时的九阶段完整管线通过，运行清单源码指纹与代码一致，36/36 个产物哈希复核通过；最新十一阶段验收见下文。
- 功能提交：`bdf14e5`（`feat: add convlstm rolling validation`）；运行清单及本进度记录随后的维护提交另行保存。

## 2026-06-21 历史 6 通道后续诊断与外部工具筛选

- 基于已冻结的逐日预测结果开展事后诊断，未重新训练或修改参数。三折总体日增量相关系数分别为 0.182、0.148、0.011，逐测点相关系数中位数分别为 0.062、0.055、-0.068。
- 第三折相对持久性基线的 RMSE 优势伴随预测增量方差明显偏小，因此目前只能表述为该折点误差较低，不能表述为已稳定捕捉位移加速和减速过程。
- 种子 `0-4` 的固定三折训练稳定性诊断已经完成，共 15 次训练，未选择最佳种子或修改超参数。
- 已检查 `modelscope/Awesome-Vibe-Research` 及相关候选项目。PaperQA2、RefChecker 和 `nature-figure` 分别可能用于本地文献核对、投稿前参考文献验证和图件审查；Curie/EurekAgent 的实验隔离思想可参考，但其指标驱动自动优化不宜直接用于当前已查看的测试折。
- 当前未向本仓库或本机 Codex 环境接入任何上述外部项目；接入前必须取得用户明确批准。
- 工具用途、风险、采用时机和状态已持久化到 `docs/research_tools.md`。

## 2026-06-21 历史 6 通道五种子诊断记录

- 折 1/2 的 RMSE 和 MAE 均为 0/5 种子超过持久性基线；折 3 均为 5/5，说明初始化影响幅度但不改变跨折方向。
- 折 1/2 的 RMSE 为 2.385 +/- 0.574 和 0.390 +/- 0.143 mm，基线为 0.245 和 0.120 mm；折 3 为 0.323 +/- 0.008 mm，基线为 0.340 mm。
- 折 3 日增量相关性为 -0.048 +/- 0.220，预测/实际增量标准差比为 0.164 +/- 0.022；不能把点误差优势解释为稳定捕捉加速/减速。
- 所有训练 loss 下降且梯度有限，但最后 10 个 epoch 的 loss 仍下降 4.4%-8.3%。下一步应先在拟合期内部锁定时间验证和停止规则，再决定有限调参；现有校准段和测试折不得参与选择。
- 全量门禁：143 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。
- 十阶段完整管线通过，运行清单源码指纹与功能提交 `97c4acf` 一致，40/40 个产物哈希复核通过。
- 四张五种子 CSV 在独立运行和完整管线运行间 SHA-256 完全一致；运行清单及本进度记录随后的维护提交另行保存。

## 2026-06-22 历史 6 通道内层验证实施记录

- 在任何新结果产生前，已将 80%/20% 内层时间切分、300 轮上限、30 轮最少观察、30 轮耐心、0.1% 最小相对改进和验证 pinball loss 选择规则写入 `framework.md`，并以提交 `3c9a616` 单独保存和推送。
- 新阶段保留原固定 120 轮结果，不修改模型结构、学习率、输入窗口、损失函数或特征；每个种子和外层折独立选择 epoch，再在完整拟合期重新初始化训练。
- 15/15 次内层选择均由耐心规则停止；折 1/2/3 的所选 epoch 中位数为 22/7/1，范围为 16-61、3-20、1-98，没有运行达到 300 轮上限。
- 相对固定 120 轮，三折总体 RMSE 分别有 5/5、5/5、4/5 个种子改善；但相对持久性基线，折 1/2 仍为 0/5，折 3 为 5/5。训练轮数影响失败幅度，但没有解决跨时期失效。
- 第三折覆盖率由 75.2% 升至 81.1%，同时宽度由 0.471 增至 0.977 mm、interval score 由 1.037 恶化至 1.248 mm；早停不能概括为所有评价维度均改善。
- 七张结果 CSV 在单阶段运行和完整管线运行间 SHA-256 完全一致。十一阶段完整管线耗时 1172.5 秒，11/11 阶段和 47/47 个产物哈希通过。
- 全量门禁：152 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。功能提交为 `ae41ef9`，运行清单及结果文档随后的维护提交另行保存。

## 2026-06-22 历史 6 通道有限容量/正则化诊断实施记录

- 在任何候选结果产生前，已将隐藏通道 `8/16`、Adam 权重衰减 `0/1e-4`、折内五种子平均验证 loss 排名、并列规则和停止扩搜判据写入 `framework.md`，并以提交 `d13292e` 单独保存和推送。
- 新阶段保留当前 `16/0` 配置作为参照，不改变学习率、输入窗口、卷积核、特征、外层折或校准规则；外层测试不参与配置排名。
- 折 1/2/3 仅按内层五种子均值分别选择 `h16_wd0`、`h08_wd0`、`h16_wd1e4`；三个折没有共同最优配置，第一/二名 loss 差值均远小于种子标准差。
- 最终相对持久性基线的 RMSE/MAE 正 skill 种子数为 0/5、0/5；0/5、0/5；5/5、4/5。只有折 3 达到多数种子双指标正 skill，触发预注册的停止扩搜规则。
- 折 2 内层选择的小模型在外层较当前早停参照平均增加 0.070 mm RMSE 和 0.061 mm MAE；不能把内层微小排名差异解释为稳定泛化增益。
- 首次运行的严格零容差参照检查因最大 `2.22e-16` 的 CSV 浮点尾差停止，未写出结果；随后以 `5 x float64 epsilon` 锁定验证 loss 容差，并用 `1e-12` 配对指标容差避免将数值噪声标记为改善。修复提交为 `e9711cc` 和 `875f832`。
- 十二阶段完整管线耗时 1693.9 秒，12/12 阶段、56/56 个产物哈希和源码指纹均通过。八张不受配对标签修复影响的容量 CSV 与先前单阶段运行 SHA-256 一致。
- 全量门禁：160 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。功能提交为 `7507dac`，最终结果与清单随后的维护提交另行保存。

## 2026-06-23 SHAP 稳定性与组消融记录

- 在结果产生前以提交 `7456598` 锁定五折、每折背景/解释日期、88 个特征、五个特征组、回归 MAE 和分类 Brier 主指标；当前数据已被探索，协议不表述为前瞻性注册。
- 功能提交 `38713fd` 实现跨折 SHAP 排名、方向相关、组级贡献和 drop-one-group 消融，并接入统一入口为第 4 阶段。
- 首次正式运行暴露方向统计的 pandas 索引对齐错误：SHAP 数组使用位置索引，而样本特征保留原索引，导致方向全为空。修复提交 `3c06d38` 将两者显式按位置对齐；绝对 SHAP、排名和消融结果不受影响。
- 修复后正式运行耗时 3291.3 秒，阶段及 9/9 产物契约通过，源码指纹和产物哈希见 `figures/pipeline/shap_stability_run.json`。运行使用 `caffeinate -i`，避免 Mac 熄屏暂停进程；网络断开不影响本地训练。
- 回归组排名折间 Spearman 中位数为 1.000，分类为 0.500；只有位移运动学组在回归 MAE 和分类 Brier 中均为 5/5 折删去后变差。
- 环境组的删组方向不一致，不能解释为环境因素无物理作用；分类运动学贡献又与 30 日位移速率标签存在定义耦合，不能当作独立提前预警发现。
- 全量门禁在修复后为 171 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。
- 十三阶段完整管线耗时 3731.9 秒，13/13 阶段、65/65 个产物哈希和提交 `6cdcc35` 均通过。`shap-stability` 在保持输出数值一致的情况下耗时 2243.9 秒；先前单阶段 3291.3 秒的额外耗时与 Mac 熄屏暂停或系统负载有关，不作为模型性能证据。
