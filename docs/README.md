# 项目文档导航

本页只维护入口，不重复记录各版结果或下一步。此前神经实验限定 ConvLSTM/PINN；293 日复核已完成，
v2.3 已完成开发段训练但未达标，已停止；其实现、完整数组与复算记录均保留。
后续冻结 3×2 交叉评分已完成并在同一结果报告第 5 节收尾，不再作为待核验或待送审任务。
此前方法筛选已完成，当时没有推荐启动的候选；短期滚动任务仅作为讨论建议。
用户随后指定联合均值/尺度概率 ConvLSTM，v2.4 已完成一次配对实验及保存结果复算，未达标并停止；
两窗平均 RMSE、CRPS 均未优于 P0，保留 B+。原评分读取异常和只读修复记录一并保留，没有重启训练。
尾段局部偏差可先不管，不再要求先指定日期。v2.1 及力探针继续暂缓。
v2.0 按原标准未达标并已停止。
旧计划的预算、运行命令和后续建议均不构成新的执行指令。

用户随后批准的GP v1与加性GP v2均已完成并停止。GP v1平均指标改善但逐点概率失败；
最新ADD均值高于B+和TIME_ONLY，概率局部改善不足以通过整体条件，保留B+。

## 当前入口

**v4.0 短期对比已完成：[完整结果](ootang_short_horizon_comparison_results.v4.0.md)／[四页精简报告](../output/pdf/ootang_short_horizon_brief.v4.1.pdf)／[四点全部七步长图件](../figures/ootang_short_horizon_v4/20260913_short_horizon/README.md)。** 用户明确的七个位移端点、四类模型与残差配对已经执行并核验；共同推荐 1 天为 RR_DIRECT、2—7 天为在线回归＋反馈，均按开发锁定。保留 PINN 物理失败和物理增量不稳定结果，不把公开已处理日序列上的低误差等同于真实预警效果；停止新增实验。

2026-09-14 仅将展示压缩为四页（简报 v4.1），六组原图、七步长推荐和科学结论均保持；[五页原版](../output/pdf/ootang_short_horizon_comparison_report.v4.0.pdf)保留。[源码、来源与核验入口](../paper/README.md)说明编译及 96 个数字单元核对，不构成新实验版本。

依据 [原计划](ootang_short_horizon_comparison_plan.v4.0.md)、[执行合同](ootang_short_horizon_execution.v4.0.md)与 [配置](../config/ootang_short_horizon_comparison.v4_0.json)，独立窗口为 2026-09-13 13:37:17—17:37:17 UTC。旧计划中“尚未执行”是形成时状态；剩余预算不转用。以下滚动与长窗记录均为历史依据。

**冻结可信度审查已完成：[报告 v1.0](ootang_rolling_frozen_validation_report.v1.0.md)／[执行前计划](ootang_rolling_frozen_validation_plan.v1.0.md)。** 用户随后授权按计划执行，A—D 已完成：五对象来源、669 起点与192边界核对通过，完整指标和结论已收束。保留滚动机器学习的探索性收益；B+ 额外增益未确立，C18 仍是经验方差规则。真实日值 as-of 与新独立评价资格未核实，不阻塞报告，也不据此开启 C19。零新训练、评分或物理调用，分步提交，未 push；原计划中的“未执行”保留为形成时记录。
下述实验收尾中的“未 push”是形成时记录；6pro 报告远端已有该快照，本次远端核对范围见新计划第 7 节，不再由旧记录判断当前缺材料。

**当前：18 个候选、六次三候选复盘完成。C18 信息矩阵方差近似在开发/后期均通过原 27 项数值门槛，独立模型/评分、112 项统计及六图核验完成。** 开发/后期平均 RMSE 0.5740/1.3931、CRPS 0.3508/0.6937 mm、90% 覆盖89.34%/86.27%；均值继承 C16，CRPS 相对 C16 略退步，区间评分改善。保留为探索候选，不称新盲测或完整贝叶斯后验，物理增量仍不稳定；原 C4 迁移失败及293日任务不改判。原总截止05:27:52 UTC保持，停止新增C19。结果 `db351d0`，源码/原件/核验 `4139854`/`ca1992e`/`fc99763`，56项相关合成检查；每步本地提交，未push。

| 文档 | 用途 |
| --- | --- |
| [短期对比结果 v4.0](ootang_short_horizon_comparison_results.v4.0.md) / [四页 PDF](../output/pdf/ootang_short_horizon_brief.v4.1.pdf) | 当前完成结果与精简展示：七端点、四类模型与残差配对；开发锁定与后期评价、统计、全部曲线及核验 |
| [短期模型与残差学习对比计划 v4.0](ootang_short_horizon_comparison_plan.v4.0.md) / [执行合同](ootang_short_horizon_execution.v4.0.md) | 已完成实验的事前冻结依据；不根据其中的运行建议自动重启 |
| [C7 追加概率研究](ootang_rolling_probability_extension.v3.6.md) / [配置](../config/ootang_rolling_probability.v3_6.json) | 已完成并核验；经验分布覆盖失败，同池高斯对照与完整分布保留 |
| [C8 在线均值学习](ootang_rolling_probability_candidate.c8.md) / [C9 逐步长尺度](ootang_rolling_probability_candidate.c9.md) | 两候选已完成并核验；均值小幅收益、概率覆盖仍不足，C9 尺度优化停止 |
| [C10 指数遗忘在线回归](ootang_rolling_probability_candidate.c10.md) / [配置](../config/ootang_rolling_probability.v3_9.json) | 已运行并独立核验；均值与概率均差于 C8，停止遗忘参数搜索 |
| [C11 物理概率参照](ootang_rolling_probability_candidate.c11.md) / [C12 区间评分权重](ootang_rolling_probability_candidate.c12.md) | 均已完成并独立核验；均值复用 C8 DATA，覆盖有限增加但评分退步，后期 25/27，停止固定核心宽分量/评分切换分支 |
| [C13 成熟误差记忆](ootang_rolling_probability_candidate.c13.md) / [C14 固定单位](ootang_rolling_probability_candidate.c14.md) | 均已运行并独立核验；前者大幅退步，后者较稳定但未超过原核心，停止单位/惩罚/滞后阶搜索 |
| [C15 跨点回归](ootang_rolling_probability_candidate.c15.md) | 两臂均退步，主模型开发/后期 26/27、24/27；初始/在线方程、空间输入和评分已独立核验，停止固定配置 |
| [C16 一日误差反馈](ootang_rolling_probability_candidate.c16.md) / [C17 自身误差校准](ootang_rolling_probability_candidate.c17.md) | 均已运行、核验并停止；后期 26/27、25/27，均值收益保留，覆盖仍不足 |
| [C18信息矩阵方差近似](ootang_rolling_probability_candidate.c18.md) | 最后候选；两段27/27、模型/统计/六图核验完成；后期已暴露与物理增量限制保持 |
| [十七轮最终对比与 C16 统计](ootang_rolling_consolidation.v2_2026-09-13.md) / [十五轮归并与 C8 统计](ootang_rolling_consolidation_2026-09-13.md) | 已完成；全部候选/对照、配对成块、非重叠、完整曲线和来源核验，旧主模型不改判 |
| [本轮简报](ootang_rolling_probability_brief_2026-09-13.md) | 方法是否属于机器学习、实际效果、尚未解决的问题与提交索引 |
| [v3.0 滚动结果与路线记录](ootang_rolling_probability_results.v3.0.md) | 四候选与后期完整结果、概率不足、物理消融、配对统计和完整曲线 |
| [C5—C6 概率尺度对照](ootang_rolling_probability_scale_study.v3.4.md) / [C5 方案](ootang_rolling_probability_candidate.c5.md) / [C6 方案](ootang_rolling_probability_candidate.c6.md) | 两个开发候选均已完成并独立核验；合并历史误差失败，最近折收益不稳定，均未运行后期 |
| [v3.0 实施与核验](ootang_rolling_probability_implementation.v3.0.md) | 因果数据流、物理教师、ConvLSTM/正则回归、概率反馈和独立核验 |
| [五次定期复盘与最终收尾](ootang_rolling_route_review_2026-09-13.md) / [后期选择锁](ootang_rolling_transfer_decision.v3.3.md) | 第三/第六/第九/第十二/第十五候选后的纠偏及 C16/C17 收尾，停止连续无收益分支，完整效果及物理额外价值尚未完成 |
| [v3.0 滚动概率预测计划](ootang_rolling_probability_plan.v3.0.md) / [配置](../config/ootang_rolling_probability.v3_0.json) | 原冻结方案；因果更新观测、30 日主步长、公平对照与每三次纠偏，追加安排另见 C7 方案 |
| [AGENTS.md](../AGENTS.md) | 当前范围、导师目标、协作与 Git 规则 |
| [历史预测残差训练核对](ootang_residual_training_review_2026-09-13.md) | v1.5/v1.19/v2.0/v2.4已有尝试与180日范围；本轮核对完成，无新训练或重复送审，数据固定使用 |
| [B+加性GP v2结果](ootang_bplus_additive_gp_results.v2.md) / [冻结计划](ootang_bplus_additive_gp_plan.v2.md) / [配置](../config/ootang_bplus_additive_gp.v2.json) | 一次8组配对已完成并停止；ADD均值较B+及TIME_ONLY变差，整体失败；完整曲线、只读核验修正及评分保留 |
| [B+–GP结果](ootang_bplus_gp_results.v1.md) / [原计划](ootang_bplus_gp_plan.v1.md) / [已确认补充](ootang_bplus_gp_execution_addendum.v1.1.md) / [配置](../config/ootang_bplus_gp.v1.json) | 单次四点开发验证已停止；均值小幅改善、平均概率评分改善，MJ3/MJ1逐点概率保护失败；完整产物与复算保留 |
| [v2.4 结果](ootang_convlstm_joint_results.v2.4.md) / [冻结计划](ootang_convlstm_joint_plan.v2.4.md) / [配置](../config/ootang_convlstm_joint.v2_4.json) | 一次两窗配对实验已停止；模型与评分复算通过，JOINT 效果失败；完整数组、评分异常及只读修复可追溯 |
| [两页阶段简报 PDF](../output/pdf/ootang_bplus_process_report_20260912.pdf) / [源码与编译说明](../paper/README.md) | 供汇报使用的均值、概率结果与下一步讨论；无图，分别展示各预测窗 |
| [方法筛选决策](ootang_method_selection_2026-09-12.md) | 三篇原始论文的适用边界与旧路线对照；本轮不训练，附导师沟通稿 |
| [v2.3 结果](ootang_probability_pinn_results.v2.3.md) / [冻结方案](ootang_probability_pinn_plan.v2.3.md) / [执行配置](../config/ootang_probability_pinn.v2_3.json) | 已完成开发段实验，均值、概率及近似检查失败，已停止；本轮未训练最终模型 |
| [293 日复核与当前方向](ootang_293day_prediction_audit_2026-09-11.md) | 来源、参数、日期、完整图表和指标；用户最新澄清与停止点 |
| [v2.2 需求记录](ootang_tail_scope_and_direction.v2.2.md) | 保留形成时的尾段要求，最新实施顺序以 293 日复核报告为准 |
| [6pro 补审交接记录](ootang_web_route_review_prompt.md) / [文本材料包](review_materials/ootang_6pro_followup_20260911.md) | 补审已返回，处理结果见复核报告；保留当时的原件包，不再作为待发送任务 |
| [v2.1 暂缓候选](ootang_probability_pinn_plan.v2.1.md) / [冻结配置](../config/ootang_probability_pinn.v2_1.json) | 原样保留的概率 PINN 设计，当前不直接进入实现；尚无训练结果 |
| [v2.0 结果](ootang_convlstm_direct_results.v2.0.md) / [冻结方案](ootang_convlstm_direct_plan.v2.0.md) | 5/8 严格均值改善，整体未达标；一次有界实验已停止 |
| [progress.md](progress.md) | 当前状态、最近维护和后续边界 |
| [路线复盘](ootang_route_review_2026-09-11.md) | 八轮学习的效果总表、失败证据和停止决定 |
| [汇总指标](ootang_route_review_2026-09-11_metrics.csv) / [来源记录](ootang_route_review_2026-09-11_sources.json) | 由既有指标计算的跨版本对照及 Git 来源 |

任务范围依当前用户请求与明确修正。某次实验的方法由对应冻结配置、源码和 manifest 决定，
数值以保存的指标与预测产物为准；文档汇总不能覆盖原结果。测试、实现完成与研究效果分别判断。

## 已完成四点实验：按需查证

以下均为历史实验依据，不是当前待办。旧文档内的“当前”“下一步”和预算只描述其形成时的状态。

| 内容 | 证据入口 |
| --- | --- |
| 原三组模型与时间协议 | [v1.1 冻结规格](ootang_bplus_probabilistic_experiment_plan.v1.1.md)、[实施与结果](ootang_bplus_probabilistic_implementation.v1_1.md) |
| B+ 标定、增量与内部选模 | [v1.2](ootang_bplus_diagnostics_results.v1.2.md)、[v1.3](ootang_bplus_increment_results.v1.3.md)、[v1.4](ootang_bplus_optimization_selection_results.v1.4.md) |
| ConvLSTM 样本与同步修正 | [v1.5](ootang_bplus_sample_learning_results.v1.5.md)、[v1.6 教师迁移](ootang_bplus_teacher_transfer_results.v1.6.md)、[v1.7](ootang_bplus_synchronized_correction_results.v1.7.md) |
| 状态 PINN 与共享力学 | [v1.8 方程依据](ootang_bplus_pinn_equation_contract.v1.8.md)、[v1.9](ootang_bplus_state_pinn_results.v1.9.md)、[v1.10](ootang_bplus_pinn_consistency_results.v1.10.md)、[v1.11](ootang_bplus_shared_mechanics_results.v1.11.md)、[v1.12](ootang_bplus_rate_learning_results.v1.12.md) |
| 修正需求、输入与历史来源 | [v1.13](ootang_bplus_rate_diagnostics_results.v1.13.md)、[v1.14](ootang_bplus_temporal_features_results.v1.14.md)、[v1.15](ootang_bplus_temporal_decomposition_results.v1.15.md)、[v1.16](ootang_bplus_history_availability_results.v1.16.md) |
| 历史学习、起点配对与目标平衡 | [v1.17](ootang_bplus_history_learning_results.v1.17.md)、[v1.18](ootang_bplus_history_diagnostics_results.v1.18.md)、[v1.19](ootang_bplus_origin_learning_results.v1.19.md)、[v1.20](ootang_bplus_origin_gradients_results.v1.20.md)、[v1.21](ootang_bplus_balanced_origin_results.v1.21.md) |
| 迁移、教师条件与连续预测 | [v1.22](ootang_bplus_input_transfer_results.v1.22.md)、[v1.23](ootang_bplus_teacher_condition_review.v1.23.md)、[v1.24](ootang_bplus_sequence_interface_results.v1.24.md)、[v1.25](ootang_bplus_sequence_learning_results.v1.25.md) |

每份结果链接到其执行前方案和原产物。已完成实验的计划、配置、源码及结果存在哈希和路径依赖，
继续按原路径保存。前期讨论与 v1 草案也被冻结 v1.1 引用，仅作设计演变证据，不是活动方案。
原始导师 PDF/ZIP 的位置与使用规则见 AGENTS；原件不改写。

## 历史八点预测与代理预警阶段

这一阶段的报告已于 2026-09-05 提交。八点 ConvLSTM–NGBoost–SHAP、H=7 代理标签和预警输出
均不属于当前四点 B+ 任务；保留其负结果和 `formal_warning_output=false` 边界。

| 内容 | 历史文档 |
| --- | --- |
| 已提交报告与结果包 | [报告编译说明](../paper/README.md)、[阶段结果包](ootang_stage_results_package.md)、[当时的方法底稿](ootang_manuscript_methods_results_draft.md) |
| 方法、标签与字段 | [NGBoost 实验记录](ootang_ngboost_auto_state_experiment_plan.md)、[SHAP 协议](ngboost_shap_protocol.md)、[规则基线](ootang_operational_run.md)、[数据字典](ootang_warning_data_dictionary.md) |
| 数据与模型审查 | [数据来源](ootang_data_lineage_expert_review.md)、[ConvLSTM 固定预算](ootang_convlstm_elevation_fixed120_review.md)、[高程与预警](ootang_elevation_warning_expert_review.md)、[区间](ootang_interval_calibration_expert_review.md)、[稳定段](ootang_stable_segment_expert_review.md) |
| 历史方法依据 | [加速度定义](ootang_v4_acceleration_decision.md)、[文献登记](current_method_reference_register.md)、[已退役导师意见快照](advisor_review_action_plan.md) |

这些文档包含独有的方法、引用或失败证据，部分被冻结配置直接引用，因此保留而不冒充当前指令。

## 已移除内容的追溯

- v1.26 未完成、未运行的计划、配置和草稿目录已删除，共九个文件；原字节保存在 Git `7fc5f29`。
- 清理前完整进度流水和旧导航可从 Git `cb08f62` 查看。当前进度只保留当前状态，不复制历史待办。
- 路线汇总的来源 JSON 是当时的来源记录；其中两份已删除的 v1.26 文件按 `source_git_commit:path`
  从 Git 查证，旧哈希不变。其余均值与概率汇总源文件继续保留。

例如只读查看旧进度：`git show cb08f62:docs/progress.md`。追溯不等于重新启动该版本。
