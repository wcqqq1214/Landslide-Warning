# Collaboration

- Challenge assumptions and point out flaws; suggest better alternatives when warranted.
- Follow the user's current task and explicit corrections. Complete authorized work through a reviewable deliverable; review-only and proposal-only requests remain read-only unless edits are requested.
- Consult existing context and decisions before asking. Resolve routine, reversible choices autonomously and state material assumptions; ask only when unresolved information materially affects correctness or scope. Reuse valid approvals; never invent one or treat silence, elapsed time, or progress notifications as approval.
- Honor explicit approval requirements. Explain the action and applicable instruction, finish authorized preparation first, and continue independent work while a dependent step waits.
- Apply relevant Skills without expanding task scope or turning optional guidance into approval gates. Do not edit installed Skills during ordinary project work.

# Research and Verification

- Use `academic-research-suite` for advisory research and writing support; it must not change frozen experimental design or conclusions.
- Check literature claims against original sources; check project numerical and effectiveness claims against versioned data and reproducible artifacts. Do not invent research facts, results, or credentials.
- Preserve frozen splits, thresholds, metrics, model records, conclusions, negative findings, and limitations. Document authorized design changes in a separate version; never conceal failures through retrospective tuning or relabeling.
- Distinguish hypotheses from verified findings, attempted fixes from verified fixes, model dependence from causality, proxy outcomes from observed truth, and exploratory from confirmatory results. Execution success alone does not establish effectiveness.
- Report implementation, validation, and user acceptance separately. Pending acceptance does not block preparation; local blockers affect only dependent work. Complete unaffected parts and state remaining work without claiming overall completion.
- Match verification to the change and research risk: relevant reproducible runs, numerical/leakage checks, and figure/data consistency. Do not retrain or run unrelated exhaustive tests for documentation changes.
- After substantive research, when the task authorizes edits, update `docs/progress.md` or the relevant versioned method/results document with inputs, methods/splits, outputs, conclusions, limitations, and next steps. For read-only reviews, report findings without writing files.

# Current Stage Plan

- **最新完成：Transformer残差幅度正则化单假设实验已完整验证并交付。** 先读`docs/ootang_transformer_regularization_results.v1.0.md`、核验页及`results/ootang_transformer_regularization_v1/20260914/final_receipt.json`。仅λ=1输出幅度平方惩罚；9拟合3600更新45检查点、完整开发376/最终293日、训练异常0，旧模型拟合/物理调用0。新REG1开发/最终RMSE44.413553/8.907333，原版48.186617/9.279082，B+41.368372/9.124173 mm；最终平均MAE6.539368较B+6.901689降低5.25%，RMSE降低2.38%。相对原版两阶段均值/概率配对通过，均值种子3/3及2/3，概率均3/3；但开发四点落后B+、最终MJ3失败、仅1/3种子RMSE超过B+、主概率未优于B+，完整工作条件两阶段均失败。18交叉组合只诊断尺度转移，不进入选模或替代主输出。779来源、696320数值、2图8面板/23408个SVG值及图文核验通过。只在`codex/transformer-residual-regularization`本地分步提交，不push/PDF、不追加λ/结构/更新；14:48:30—16:48:30 UTC独立窗口余额不转用。下方事前“尚未训练”保留形成时状态，原版/旧实验结论不改。

- **最新授权：Transformer残差幅度正则化单假设验证。** 用户要求进一步验证，依据`docs/ootang_transformer_regularization_plan.v1.0.md`和配置。仅新增λ=1输出幅度平方惩罚，原结构/三种子/400更新不变；9拟合3600更新，完整开发376日及最终293日，效果差不停止。原版/B+/DRIFT1/RR和物理缓存复用，主概率规则不变，固定3×3均值/尺度仅诊断。新分支`codex/transformer-residual-regularization`，自限14:48:30—16:48:30 UTC含准备；本地分步commit，不push/PDF，旧预算不恢复。当前先冻结实现核验，新训练0；下方完成记录不阻止这次明确新授权。

- **最新完成：Transformer / CNN-Mamba 按导师条件的全部实验与交付已完成。** 先读 `docs/ootang_sequence_conditional_results.v1.0.md`、`docs/ootang_sequence_conditional_validation.v1.0.md` 和 `results/ootang_sequence_conditional_v1/20260914/final_receipt.json`。两个固定结构各DIRECT/BRES三种子，内部均选400次；36次拟合/14400次更新、180检查点，完整376日开发和293日最终评价，训练异常0。最终平均RMSE B+9.124173、DRIFT1 12.977735、RR12.338214、旧TCN43.253357/12.862059、Transformer36.416858/9.279082、CNN-Mamba39.610119/10.375614 mm。新残差版均值改善旧TCN，Transformer残差MAE略低于B+但RMSE及概率未同时改善；四臂两阶段完整条件均0/4。最终残差均值各3/3种子，开发不稳定；概率配对两阶段均0/3。2220800数值、完整发出顺序、五图20面板及报告核对通过；旧NumPy矩阵警告保留，原结果和宽区间不改。此为重复历史日期的探索性条件预测；CPU Mamba-1适配不冒称旧8点CUDA分支同任务。分支 `codex/transformer-mamba-conditional`，分步本地提交，不push/PDF，不自动追加训练；独立12:33:39—14:33:39 UTC剩余窗口不转用，实际用时见回执。下方授权的“尚未训练”是形成时记录。

- **最新授权：轻量 Transformer 与 CNN-Mamba 条件训练完整实验。** 用户要求两个模型同样跑完；依据 `docs/ootang_sequence_conditional_plan.v1.0.md` 和配置。分支 `codex/transformer-mamba-conditional`，独立 2026-09-14 12:33:39—14:33:39 UTC；两个固定结构各DIRECT/BRES三种子，沿用导师条件驱动、无位移反馈，完整开发376日和最终293日，效果失败不停止。旧同协议TCN/B+/DRIFT1/RR只复用，最多新增36次拟合/14400次更新；不扫描结构、不push/PDF。当前先冻结与实现核验，尚未开始新训练；下方TCN完成状态保持，不覆盖这次明确新授权。

- **最新完成：按导师条件从头训练 TCN，所有阶段与交付已跑通并结束。** 见 `docs/ootang_tcn_conditional_training_results.v1.0.md`、`docs/ootang_tcn_conditional_validation.v1.0.md` 与 `results/ootang_tcn_conditional_v1/20260914/final_receipt.json`。6996参数、两臂三种子，18次拟合/3600次更新，内部共选100次；完整376日开发与293日最终评价，未因效果差中止。最终B+/DRIFT1/RR/DIRECT/BRES平均RMSE9.124173/12.977735/12.338214/43.253357/12.862059 mm，两臂整体失败，保留负结果。BRES较DIRECT均值在两阶段和3/3种子改善，概率收益有阶段/测点限制；其四点主要评分仍差于B+。66检查点独立前向、509586数值、完整发出顺序及三图12面板/38612个SVG值核对通过。首次图件打包键冲突修复为v2，原件保留，训练未重跑。来源冻结`d68cecc`、实现`c880721`、训练结果`9ade9a2`；只在`codex/tcn-conditional-training`本地提交，不push/PDF，不自动追加候选或更新，独立120分钟余额不转用。下方本轮授权及仅计划文字保留形成时状态。

- **最新执行授权：用户要求无论效果多差都跑通 TCN 新实验。** 见 `docs/ootang_tcn_conditional_execution.v1.0.md` 与配置；分支 `codex/tcn-conditional-training`，独立 2026-09-14 11:44:16—13:44:16 UTC。执行整段条件训练，两臂/三种子和完整开发/最终评价；效果门不阻塞后续，代码问题修复后继续，保留负结果。旧参数/数据/切分不变，不加候选、轮数或恢复旧预算，只本地分步 commit，不 push/PDF。此项覆盖下方“先计划、尚未授权执行”的形成时记录。

- **当前用户要求：先写按导师方案重新训练 TCN 的 plan，尚未授权本轮立即执行。** 见 `docs/ootang_tcn_conditional_training_plan.v1.0.md`。旧七天滚动实验和 293 日固定权重递推均已完成；新计划对齐原 PDF/ZIP 的给定未来降雨/水位、连续原 B+ 和无位移反馈 8:2 条件预测，从头训练整段 DIRECT/BRES 轨迹。当前仅文档及来源核对，正式配置/实现/训练未启动；拟议独立 120 分钟窗口未计时，不恢复旧额度，不自动加入 Transformer/Mamba，不 push/PDF。后续先读新计划与最新 progress，再按需核读下方已完成结果；旧负结果、冻结计划和最终回执不改写。

- **最新：8:2无位移反馈TCN新实验及导师图件已完成并停止。** 见 `docs/ootang_tcn_independent_results.v1.0.md`、`docs/ootang_tcn_independent_validation.v1.0.md` 及 `results/ootang_tcn_independent_v1/20260914/final_receipt.json`。前1168日、后293日一次发出；复用固定400次/三种子短期TCN，以自身预测每7天递推，未来降雨/水位起点固定；新增拟合/更新0。完整293日四点平均RMSE：B+10.169685、DRIFT1 12.977735、RR_DIRECT 137.895650、DIRECT 90.731455、BRES 211.827864 mm；两版TCN在四点均值和主要概率评分均落后B+/DRIFT1，残差版更差。118项来源、382校准起点、294个最终块、240评分及71197项交付数值核对通过；两张中文四点PNG/SVG已目视，保留各点/种子/日期/失败。核验实现 `afc252d`、评分图件 `6a93058`。探索性固定权重迁移，不是新293日直接输出训练、不推论整个TCN家族；旧滚动结果保持。分支 `codex/tcn-independent-forecast` 只本地提交，不push/PDF，不自动追加训练；实际用时见最终回执，自限窗口10:20—12:20 UTC不转用。下方本轮事前及历史阶段记录不覆盖此完成状态。

- **新授权：参照导师图开展8:2无位移反馈独立递推实验。** 用户已明确选择独立预测方式的新实验；分支 `codex/tcn-independent-forecast`，依据 `docs/ootang_tcn_independent_plan.v1.0.md`。复用已有前80%训练检查点，两版TCN/三种子按7天块递推完整293天；不接收预测段位移或驱动。前1168日内的382个历史起点独立回放形成每距离最近90条成熟误差的固定区间。新增拟合/更新0；不能将本轮称为新训练的293日直接输出模型。自限窗口10:20—12:20 UTC含前置读图与澄清，只本地分步提交。先冻结和核验，再执行及出中文四点PNG/SVG；保留旧滚动结果，不按新结果换结构/轮数或删日期，未授权PDF/push。

- **最新状态：TCN 完整实验、独立核验及 Markdown/CSV/图件交付已完成，停止追加实验。** 见 `docs/ootang_tcn_results.v1.0.md` 与 `results/ootang_tcn_v1/20260914/final_receipt.json`。两臂内部共同选择400次，三个种子、三阶段共18次拟合/7200次更新；72份权重重载，36份评价检查点完整前向复算，预测及尺度最大差0，评分差4.97e-14 mm；117600行预测明细、七步长/逐点表和16张最终SVG/PNG已核对。开发和探索性后期七步长平均 MAE/RMSE/CRPS/90%区间评分均优于 B_ANCHOR，但均落后 DRIFT1 与 RR_DIRECT，两版 TCN 完整工作条件均0/7。开发均值/概率优胜者七步长均为RR_DIRECT，共同推荐仅1—6天，第7天空缺保持。后期残差版仅第3—7天集成MAE/RMSE同时改善，七步长区间评分全部退步；种子1的RMSE改善、种子0/2均退步，未建立稳定残差收益。7天RMSE：DIRECT 0.856186、BRES 0.771781、DRIFT1 0.278587、RR_DIRECT 0.037318、B_ANCHOR 1.784162 mm；BRES的90%覆盖76.8293%。执行核验通过与效果未达标分别保留，不声称用户/导师验收。此次后续核验与交付只作本地提交，不自动追加模型/轮数，不制作PDF；独立120分钟窗口的剩余额度不转用。

- **本次入口维护及推送授权。** 2026-09-14 用户明确要求更新 `README.md`、`AGENTS.md` 并 commit and push。新增提交仅包含这两个入口文件，按 Git Rules 推送到 `main`；该明确请求覆盖本次推送相关的历史“不push”文字。当前 `codex/tcn-short-horizon` 已提交历史随正常快进推送保留，未提交的核验器与产物保持本地，不擅自纳入提交，也不切换或重置主任务工作分支。本条不授权新实验或扩展预算。

- **当前读取顺序。** 先读本入口、`README.md`、最新 `docs/progress.md`、独立预测的 `docs/ootang_tcn_independent_results.v1.0.md` / `docs/ootang_tcn_independent_plan.v1.0.md` / `config/ootang_tcn_independent.v1_0.json` 及其最终回执；需要短期比较时再读`docs/ootang_tcn_plan.v1.0.md`、`config/ootang_tcn.v1_0.json`、本轮 `internal_selection.json`/`selection.json` 及开发/后期 CSV，再按需追溯 v4.0 与旧结果。下方旧“开始或恢复工作”的长列表作为历史来源索引，不要求为文档维护重跑旧实验；“尚未训练／当前结束／不push”等均按形成时间及后续明确授权解释。

下方入口维护推送授权仅对应当次明确范围，不自动扩展到后续核验与图文交付。以下保留事前授权与历史阶段记录，不覆盖上述实际完成状态。

- **新独立授权：TCN 七步长配对实验。** 2026-09-14 用户要求根据新方案逐步验证，覆盖旧停止限制中的本轮特定实验。新分支 `codex/tcn-short-horizon`，独立120分钟窗口09:24:37—11:24:37 UTC，含准备和核验；见 `docs/ootang_tcn_plan.v1.0.md`。只新增 TCN_DIRECT/TCN_BRES，同种子/输入/结构与共同内部训练次数，开发锁定后完整后期（即使效果差），C16仅历史补充。旧训练不恢复，不追加结构/轮数，不制作PDF，只本地分步commit，不push。下方旧“当前结束”记录不阻止此项新授权。

- **最新收尾修订：用户授权提交全部未提交改动（含 Vajont），随后取消短期对比 PDF 报告。** 删除 `output/pdf/ootang_short_horizon_brief.v4.1.pdf`，其余报告源码、图件和形成时记录保留作历史备份，移除当前导航中的 PDF 交付入口；不再生成或核验该报告。Vajont 仅按原文件归档，不启动新案例或实验。本次新增提交只在本地 `test`，没有新的 push 请求；此前 `6093dc7` 已合并推送至 `main`，仓库已为 private。

- **当前任务已由用户明确结束。** 2026-09-14，用户转述导师认为本轮 ConvLSTM／PINN 效果较差、正在另换模型，并要求“到此为止”。本仓库保留全部已完成实验、负结果、物理核验、探索性收益与限制；不据此断言整个模型家族无效，不再启动候选、追加训练或恢复旧预算。当前仅完成仓库收尾：用户已确认将 `test` 合并到现有默认分支 `main` 并 push，并将仓库设为 private；此授权覆盖下方历史“不 push”限制。未提交的 PDF 修改及无关 Excel 留在本地，不纳入本次合并内容。下方“当前／最新／待办”均保留为形成时记录，不构成继续实验的授权。

- **最新：初态耦合续评已完成内部、开发、完整后期、独立核验及 PDF 同步。** 见 `docs/ootang_neural_initial_state_results.v1.1.md`。本次 21:05:18—22:05:18 UTC 独立 60 分钟窗口内复用内部检查点选 e200，开发/后期各三种子200次，共新增六次拟合1200次更新。用户在开发失败后明确要求完整后期，单独 v1.2 补充已记录；开发失败不改判。4707 条轨迹通过按原 x/beta 换算数值物理门，4167 条训练后轨迹也过原严格子步诊断，三条 e0 同一起点严格诊断保留。后期平均 MAE/RMSE 七天均改善、概率门3—7天通过，但逐点均值门未过，联合0/7；7天后期RMSE1.554626、CRPS0.802606 mm，落后CORE0.025580/0.009978。完整流程已跑通，不再训练、换候选或预算续期；保留旧软PINN失败，只在test本地分步commit，不push。

下方续评事前授权和 v1.0 停止记录保留形成时状态，不覆盖以上最新结果。

- **最新授权：用户批准独立 60 分钟初态模型续评及 PDF 同步。** 2026-09-13 21:05:18—22:05:18 UTC，依据 `docs/ootang_neural_initial_state_continuation.v1.1.md` 与新配置。原 x 容差按正 beta 换算塑性子步数值门，原严格门保留诊断；仅复用内部模型并补原选模／开发／条件后期，最多新增 6 次拟合、2400 次更新。原模型、C、数据、效果门与 v1.0 失败记录不改。当前处于事前冻结，未开始续评；该授权覆盖旧停止限制中的本次特定续评，不恢复旧预算或其他候选。只在 `test` 本地提交，不 push。

- **最新独立神经初态实验已停止并完成保存核验。** 用户批准的 120 分钟窗口为 2026-09-13 18:17:21—20:17:21 UTC；仅四维有界 z 初态修正、原 B+ 严格递推，原 54 参数及其他记忆保持。内部三种子各 400 次更新后，完整 e0 对照触发新增塑性子步门：原 x 容差与 delta_p=x/beta 的单位未提前对齐。D 确认训练后 12 份检查点共 2160 条轨迹全部过物理门，失败只在三份相同 e0 的同一起点；不得误称神经轨迹或预测效果失败。未选模／评分，未进开发或后期，七步长预测增益未评价。结果见 `docs/ootang_neural_initial_state_results.v1.0.md`，冻结计划／配置保持；不改门槛、补评分、重训或自动开新候选。只在 `test` 本地分步备份，不 push。下方 v4.0 及其他“当前授权”均为历史，不恢复其预算。

@docs/ootang_neural_initial_state_results.v1.0.md
@docs/ootang_neural_initial_state_plan.v1.0.md

- **v4.0 P1—P5 已完成，当前停止追加实验。** 36 次固定神经拟合、7800 次更新，七步长开发选择锁定后完整运行后期；独立模型、数组、尺度、评分、选择、统计与图件核验完成。共同推荐 h=1 为 RR_DIRECT，h=2—7 为 C16_CORE_RULES，后期七组均通过本轮工作条件；均值／CRPS 最小身份七步长均为 CORE。7 天 CORE 后期平均 RMSE 0.025580、CRPS 0.009978 mm，90% 覆盖 85.8885%；B_ANCHOR 同窗 RMSE 1.784162 mm。PINN 完成训练但物理验收失败，B+ 额外增量未确立；低误差仅支持当前公开日序列的探索性位移预测，不代表真实预警有效。结果与五页图文简报见下方新入口；原四小时截止不变，剩余时间不转用，分步本地 commit，未 push。


@docs/ootang_short_horizon_comparison_plan.v4.0.md
@docs/ootang_short_horizon_execution.v4.0.md
@docs/ootang_short_horizon_comparison_results.v4.0.md

以下 v4.0 授权与 P0 文字保留形成时记录；其中“尚未训练／尚未启用”不覆盖已完成状态。

- **最新执行授权：用户已要求“根据 plan，一步步进行”。v4.0 独立执行窗口已于 2026-09-13 13:37:17 UTC 开始，17:37:17 UTC 截止。** P1 固定短窗状态 PINN 的完整记忆／初始 z 校正与日映射约束，原 54 参数不重估；共同校准与七步长开发选择保持。此授权支持 P1—P5 的实现、固定实验、核验、报告及分步 commit，不需再逐命令询问。仍未请求 push。

- **当前请求：用户已明确改为分别预测 1、2、3、4、5、6、7 天后的位移，先写短期比较 plan，最终交付参考 process_report 的简要图文报告。** 采用 B+、ConvLSTM、PINN、岭回归与固定残差配对；逐 h 在开发段选模，后期完整评价，不按点／种子／尾段拼接。v4.0 当前为设计文档，尚未训练；PINN 数学接口须在实施准备中明确，未就绪只影响该臂。未来 4 小时为拟议独立执行上限，尚未启用；原八小时及冻结审查均已结束。旧 30 日与 293 日结论保留为历史，不再决定本次短期主模型。当前只做文档、来源核对与本地 commit；未请求 push。

以下入口与执行预算描述此前阶段；其“当前”不覆盖上述最新请求。

@docs/ootang_rolling_probability_plan.v3.0.md
@docs/ootang_rolling_consolidation.v2_2026-09-13.md
@docs/ootang_rolling_probability_brief_2026-09-13.md
@docs/ootang_rolling_probability_candidate.c18.md

@docs/ootang_route_review_2026-09-11.md
@docs/progress.md
@docs/ootang_convlstm_direct_results.v2.0.md
@docs/ootang_tail_scope_and_direction.v2.2.md
@docs/ootang_293day_prediction_audit_2026-09-11.md
@docs/ootang_probability_pinn_plan.v2.3.md
@docs/ootang_probability_pinn_results.v2.3.md
@docs/ootang_method_selection_2026-09-12.md
@docs/ootang_convlstm_joint_plan.v2.4.md
@docs/ootang_convlstm_joint_results.v2.4.md
@docs/ootang_bplus_gp_results.v1.md
@docs/ootang_bplus_additive_gp_plan.v2.md
@docs/ootang_bplus_additive_gp_results.v2.md
@docs/ootang_residual_training_review_2026-09-13.md

历史规格与结果按 `docs/README.md` 查证；不把其中形成时的“当前计划”自动加载为当前任务。

- **当前新授权：用户已批准按 B+ 引导短期滚动概率预测方向自主实验约 8 小时。** 见 v3.0 计划与配置；总时间 2026-09-12 21:27:52—2026-09-13 05:27:52 UTC，包含准备和核验。原数据和四点固定，每日释放过去位移、未来 1—30 日条件预测，30 日为主步长；这是一项新任务，不改判原 293 日长窗。原 v3.0 安排六候选，C7 追加方案安排累计九候选；九轮现已完成并按每三轮复盘。每完成一步 docs 和 commit。允许在总预算内实现、训练、按证据修正及核验，不需逐命令重复确认；旧版本停止状态仍有效但不是本轮执行禁令。找到经核验且满足事前标准的方法可提前停止；未要求 push。无关 Vajont Excel 保持。

- **当前：18 个候选、六次三候选复盘完成。C18 信息矩阵方差近似在开发/后期均通过原 27 项数值门槛，独立模型/评分、112 项统计及六图核验完成。** 开发/后期平均 RMSE 0.5740/1.3931、CRPS 0.3508/0.6937 mm、90% 覆盖89.34%/86.27%；均值继承 C16，CRPS 相对 C16 略退步，区间评分改善。保留为探索候选，不称新盲测或完整贝叶斯后验，物理增量仍不稳定；原 C4 迁移失败及293日任务不改判。原总截止05:27:52 UTC保持，停止新增C19。结果 `db351d0`，源码/原件/核验 `4139854`/`ca1992e`/`fc99763`，56项相关合成检查；每步本地提交，未push。

- **C1—C6 历史结论保持，第二次复盘的自定六候选停止安排已由第三次复盘更新。** C4 开发 27/27，唯一后期 24/27，均值和概率评分改善但覆盖不足；C5 合并历史误差尺度失败，C6 最近折开发小幅改善但内部退步。C5/C6 未运行后期，其禁止迁移入口不变。C4 配对统计、非重叠统计与六张完整图保持；C7—C9 独立核验不构成这些新候选的统计区间。DATA 消融仍优于 FULL，物理额外价值未证实；原 293 日长窗及所有旧候选停止状态保持。


- **最新核对已完成：历史预测残差学习不是新方案，不据此再训练或重复送6pro。** 见 `docs/ootang_residual_training_review_2026-09-13.md`。v1.5纯物理OOF、v1.19起点历史、v2.0直接多步、v2.4联合概率都已有对应尝试及负结果；本次核对63项配置来源、8张训练查询、4组教师参数/训练前缀及输出锁，零神经求值/训练/物理调用，位移只解析前612日。OOF指B+教师拟合边界，当前h预处理和神经训练样本不冒充独立在线预测；旧优化器未收敛标志保持。293/376日训练查询未覆盖是未验证差异，不构成收益依据。用户明确现有论文数据固定且为真实值，不要求更换/补采，不把原始材料恢复设为当前前置条件；按Wang等公开藕塘数据使用，历史来源限制不回写。当前推荐新增实验0个，保留B+，不自动调权、拉长窗口或组合旧模块开新版本；只有具体新假设且本地无法裁定才考虑定向论证。

- **最新有效状态：用户授权的加性GP v2已完成并停止，ADD效果失败，保留B+。** 见 `docs/ootang_bplus_additive_gp_results.v2.md`。762/376日配对，8次拟合、360次目标调用、270次迭代、13.1950秒，来源49项及完整输出锁定通过；未读最终293日标签，物理调用/旧重训/重试均0。预测平均RMSE ADD 41.7343、TIME_ONLY 40.7863、B+ 41.3684 mm；ADD平均MAE 33.2878也高于两参照。平均CRPS 25.2531、90%区间评分229.6315优于e0，但均值失败、对TIME_ONLY的CRPS增益不足1%，14项聚合仅6项通过、逐点保护0/4。停止ADD，不把TIME_ONLY换成主候选。独立预算2026-09-12 18:37:17—20:37:17 UTC，正式运行18:49:42—18:49:56，剩余时间不转用。原log参数元数据核验相差1.11e-16，已按原log/exp表示重建核精确核对；只改只读核验器和回归检查，训练核/配置/预测/数值容差未改。12项合成检查、8模型重载、独立矩阵、40/20/22760/512行评分复算通过；原异常及矩阵警告保留，不声称修复底层库。冻结配置/计划、原结果和旧GP v1正负证据保留，未push。不自动换核、调门槛、扩边界、重训最终窗或重复送审。

- **此前有效状态：用户明确选择GP并确认执行补充后，B+–GP单次开发验证已完成并停止，整体未达标。** 依据 `docs/ootang_bplus_gp_results.v1.md`；四点各拟合一次，共138次目标调用、108次迭代，4.3896秒，物理调用0。完整376日平均RMSE 41.3684→41.1976 mm、MAE 33.2651→32.8855、CRPS 28.1156→25.1766、90%区间评分388.4387→263.5318；六项平均改善均通过、四点均值均不退步，但MJ3区间评分/覆盖及MJ1 CRPS/区间评分/覆盖退步，只有ATU1/ATU5通过逐点保护。保留B+，不换核、扩边界、补跑、拼点或进入最终293日。12项合成检查、重载、独立矩阵、历史对照和保存评分复算通过；矩阵乘法警告已记录，有限值与逐项求和一致，未确定警告底层成因。预算2026-09-12 17:32:19—19:32:19 UTC，运行已于17:53:41结束；剩余额度不转为下一实验。最新授权允许本轮GP机器学习候选，不冒称深度学习/PINN，不改变既有v2.3/v2.4停止状态。

- **此前有效状态：用户授权的 v2.4 配对实验与复算已完成，JOINT 未达标并停止，保留 B+。** 两个完整 180 日窗的四点平均 RMSE 为 58.1709/43.5275 mm（P0 58.0122/22.2448），CRPS 42.1278/31.7638 mm（P0 42.0113/25.1140）；完整拟合也变差，旧四项严格均值改善 0/8。DETACHED 均值与原 DIRECT 同种子最大差 0；JOINT 仅第一窗优于 DETACHED，第二窗退步，不把对照替换成成功候选。12 组 e100 固定模型，共 1200 次协调迭代、2400 次优化器更新；一次训练 61.12 秒。训练后评分错误地要求 P0 有三分量而退出失败，原异常与锁定产物 `157c467` 保留；修复 `ba1e645` 只适配单分量读取/评分，训练和判定核心 AST 不变。保存结果复算 `73aad6f` 核验 12 组重载、64 行指标和 21504 行点日表，差均为 0，零新训练和物理调用。结果以 v2.4 结果页及 `results/ootang_convlstm_v2_4/20260912_joint/scoring/` 为准。总时限仍为 2026-09-12 15:51:38—16:51:38 UTC，不重启、不改权重或加轮数，不进入最终 293 日；剩余时间不转用下一轮。旧 PINN 停止状态和全部负结果保持。

- 2026-09-12 用户明确批准新实验，本轮采用此前提出的 60 分钟总时限（08:24:03—09:24:03 UTC）。实验现已完成并停止，三种子各 300 次、共 900 次更新，仅运行 792/376 开发阶段；不把剩余时间移用于下一轮。授权、执行与累计用时见 progress。
- **此前状态保持：v2.3 水文蠕变概率 PINN 开发段实验失败并已停止，保留 B+，未进入最终 293 日训练。** 376 日开发四点平均 MAE 为 177.5373 mm（B+ 33.2651），平均 RMSE 190.1728 mm（B+ 41.3684），CRPS 166.6350 mm（v1.1-e0 28.1156），90% 覆盖率 0%。九项候选检查均未过，神经/同背景 C 解相差很大；实现与复算通过不等于效果通过。原 v2.1 和四参数力探针继续暂缓。已完成的最终 293 日复核仍为 e0 无均值增益，平均/合并 RMSE 9.1242/9.4506 mm；不与本次开发窗混排。
- **此前收尾已完成：用户授权的冻结 3×2 均值/尺度交叉评分已核验，本次决定不再送 6pro 重复审核。** 固定 B+ 均值使用保存神经尺度时开发平均 CRPS 28.1156→26.9647、90% 覆盖率 36.24%→40.23%，但区间变宽、MJ3 CRPS 变差、均值无增益；只是交叉诊断，不替换主输出或给 M0 增补概率成绩。三种子/完整日期保留，零新训练和物理调用。初始化拟合 MAE 47.6222→最终 34.1867，不是从完整 B+ 解出发；旧 M1/M2 已尝试 B+ 残差/方程内修正，泛称“保留 B+ 再学修正”不是新方案依据。见 v2.3 结果第 5 节；补表不是待办，不据此启动 v2.4。只有可能改变去留且本地无法裁定的新证据才考虑定向补审。
- **此前方法筛选已完成：三篇原始论文的相关方法/实验核读后，本轮推荐进入实验的候选为 0 个。** 依据与阅读边界见方法决策页；不将贝叶斯推断、残差修正或论文的短期预测成绩当作当前长窗增益证据。建议与导师讨论同四点、获得新位移观测后的短期滚动概率任务，且 B+ 须使用相同信息条件；这只是讨论建议，未改变 293 日协议、步长或导师目标，不是新训练授权。本轮不追加论文、诊断或重复送审；不据有限检索断言全部 PINN 无效。
- 用户进一步明确：导师只是圈出尾段，表示局部预测不准可先不管，未指定日期。**不再把获取尾段日期作为当前报告或其他独立工作的前置条件。** 全窗及逐日误差保留；没有分段评分或尾段贡献。v2.3 提出完整窗 MAE、CRPS 和区间评分的候选门槛，属于新方案建议，不是导师已确认的标准，不追溯改判旧结果。若未来将分段用于选模/主要指标，须事前说明依据，不能按胜负截尾。新澄清不能让零修正变成神经增益。
- v2.3 根据 B+ 保存状态提出有界水文蠕变修正，保留全历史反力与运动递推；原 54 参数不重估，背景演化关系明确改变。该结构已在固定开发协议下失败，不再作为待启动候选。同背景 C 重放只作诊断，其开发平均 MAE 41.1096 mm 也不优于 B+，不替换神经主输出；不把模拟分量或共同拟合的关系当成真实机制证明。
- 范围保持：**藕塘一个现有案例、MJ3/MJ1/ATU5/ATU1 四点位移概率预测，暂不做预警、不换新案例**。导师目标仍是均值误差低于改进 B+，同时改善概率预测。实现、测试、提交与训练损失下降都不能代替效果达标。
- v1.1 的三组为 M0 改进 B+、M1 ConvLSTM、M2 方程内修正混合模型；M2 不冒充经典 PINN。v1.9 状态 PINN 的主输出仍是 R，诊断 P 不得替代主输出。原模型、切分、阈值、选模记录、负结果和限制保持。
- 截至 v1.25 的八轮主要学习、16 个变体同窗汇总：没有一个同时降低两窗平均 RMSE 或同时降低两窗平均 CRPS，严格四项改善最高 3/8。v1.25 四组均 2/8，IN_CARRY 两窗 RMSE 59.2967/25.4071 mm，高于 P0 的 58.0122/22.2448。**当前保留 B+ 基线，整体精度/概率目标未完成。**
- v1.26 未完成、未运行，已按用户清理要求从工作树删除其计划、配置及草稿目录，共九个文件；原件可从 Git `7fc5f29` 恢复，仅供追溯。没有活动 v1.26 入口或运行授权。
- v2.0 DIRECT 严格均值改善 5/8，但预测平均 RMSE 为 64.4117/22.1677 mm（P0 58.0122/22.2448），CRPS 为 47.2449/26.9716 mm（P0 42.0113/25.1140），完整均值与概率判据失败。局部改善数量更多不代表稳定四点提升。
- v2.0 方案、实现与实验产物分别提交 e49ac92、b3ba6c7、453762f；一次运行 34.20 秒，900 次均值/600 次尺度更新，7 项相关检查通过，全部 15 个模型重载一致。全流程处于原 120 分钟窗口内（07:33:17–09:33:17 UTC），剩余预算不转用于新实验。未重新拟合 B+，未重训旧变体。
- v2.1 方案/配置提交 `1ebb98a` 原样保留，尚未实现或训练；尾段容忍不放宽物理检查。旧 `Day.backward` 的梯度接口限制不是原 v2.1 张量残差对力断梯度的证据；施力历史与随机状态充分性仍属待验证问题。本次没有历史冲突反例或可辨识性验证。v2.3 单独给出新候选的依据、历史状态表达、神经位移近似验收及拟议效果标准，不直接恢复旧计划。
- v2.2 修订提交 `e78490d` 保留形成时的需求记录；原图现已定位为 PDF 第 13 页 ATU1/MJ3 的 293 日预测窗，与早期两窗不同。用户最新尾段澄清以 293 日复核报告为准，v2.3 的执行结果与停止状态以其结果报告为准；不再把精确截点当成普遍前置条件。原 8/8 四项门槛不自动等同导师要求，旧负结果不改判。
- v2.3 数据接口、完整历史递推、原 C 同背景重放及运行流程均已实现；八项相关测试通过，三模型重载一致；只读复算核验 388 行指标、18688 行点日表和九项判据。结果与来源见 v2.3 结果文件及 `results/ootang_probability_pinn_v2_3/20260912_development/`。核心实现 `003156d`，复算工具 `7f0ed6d`，运行产物 `4aad0c9`。
- 不用“局部问题尚可继续诊断”自动延长路线，不换版本重置预算，不拼点/种子、不事后选轮数或按胜负删日期。本轮已经停止，剩余额度不自动移用于新实验；旧准备额度与本次新授权分别记在 progress。出现局部正证据也不自动开启下一轮。
- 开始或恢复工作先读本入口、最新 progress、v3.0滚动计划、历史预测残差训练核对、加性GP v2结果、B+–GP结果、v2.4 结果、方法决策、v2.3 结果和 293 日复核报告；按需追溯形成时方案。v2.3 已停止，不根据旧配置再次运行。v1.1–v1.25、v2.0 原结果/配置/源码及 v2.1/v2.2 记录保留，旧文档的“下一步”仅为历史背景。研究目标不等于已有结果或导师验收。
- 原始日值 as-of 未知、当前 h 预处理、已知未来驱动及历史窗口反复暴露的限制保持；本次汇总不构成新盲测或方法有效性证明。

## 导师资料（当前阶段原始依据）

以下 `@` 路径均相对于仓库根目录；保留原件，不覆盖或修改导师交付文件。

@manuscript.pdf
@藕塘滑坡_二维模型B加_优化预测报告.pdf
@section2d_v4.zip

- 两份 PDF 内容相同，优先阅读 `manuscript.pdf`，不作两份独立证据。ZIP 完整交付根目录为 `section2d_v4/work/delivery_final/outang_repro/`；内部文件定位与平台复现见 v1.1 第 3.1 节。
- `@` 仅定位资料，不代表已读取内容。涉及公式、参数、数据或数值结论时，实际查阅对应 PDF 页或 ZIP 文件并记录来源，不依靠对话摘要猜测。

## 导师目标（用户于 2026-09-10 转述，轻微润色）

导师说明：资料包含提出的研究想法和借助 ChatGPT 生成的模型，现有物理预测模型精度尚可。承谦本阶段的工作为：

1. 提出基于物理引导的智能概率预测模型，开展时空概率预测。可以沿用现有 ConvLSTM，也可以采用物理信息神经网络（PINN，导师推荐）。
2. 采用 PDF 中的**改进 B+ 模型**提供物理引导，也可参考其中的训练和预测思路。
3. 仅分析藕塘同一剖面上的 **MJ3、MJ1、ATU5、ATU1** 四个测点。
4. 针对物理模型难以充分考虑不确定性因素的问题，通过智能概率模型提高预测精度、量化不确定性，并借助物理引导增强模型可信度。
5. 训练段和预测段的预测（或拟合）均值应接近实测位移，误差应小于改进 B+；概率预测区间应尽可能覆盖实测数据，允许存在偏差。
6. 对物理引导智能概率预测方法不清楚时，先查阅相关文献，并核对原文依据。

## 导师补充要求（用户于 2026-09-11 转述）

“像尾部的这个地方是很难预测的，可以不管。”来源为本次对话和圈出末端变化的附图。
采用的方向解释：允许图示尾段预测偏差，优先主要预测时段的均值与概率质量；范围依下述后续查证。
后续补审准备已对上 PDF 第 13 页图 5：红圈为 ATU1/MJ3，原图预测期 2019-09-12 至 2020-06-30；
用户随后明确只表达尾段预测偏差可先不管，没有指定日期；当前不要求补齐截点。
补审结论、完整 293 日复核和最新解释见 [复核报告](docs/ootang_293day_prediction_audit_2026-09-11.md)，不改写 v2.2 形成时记录。
完整窗口和困难尾段的误差继续保留，不能据此删数据或宣称旧模型已经达标。
原始补充来源见 [v2.2 修订](docs/ootang_tail_scope_and_direction.v2.2.md)，最新解释及实施顺序见上述复核报告；原始导师文件不改写。

# Git Rules

- Commit a backup after each completed, verifiable step. Split distinct implementation, experiment-result, and documentation steps into separate commits; keep related tests with their feature/fix and exclude unrelated user files. A commit request does not by itself request a push.
- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
