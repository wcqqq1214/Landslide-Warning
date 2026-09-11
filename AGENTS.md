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

@docs/ootang_bplus_probabilistic_experiment_plan.v1.1.md
@docs/ootang_bplus_diagnostics_plan.v1.2.md
@docs/ootang_bplus_increment_plan.v1.3.md
@docs/ootang_bplus_increment_results.v1.3.md
@docs/ootang_bplus_optimization_review.v1.4.md
@docs/ootang_bplus_optimization_selection_plan.v1.4.md
@docs/ootang_bplus_optimization_selection_results.v1.4.md
@docs/ootang_bplus_error_structure_protocol.v1.5.md
@docs/ootang_bplus_error_structure_results.v1.5.md
@docs/ootang_bplus_sample_learning_plan.v1.5.md
@docs/ootang_bplus_sample_learning_results.v1.5.md
@docs/ootang_bplus_teacher_transfer_plan.v1.6.md
@docs/ootang_bplus_teacher_transfer_results.v1.6.md
@docs/ootang_bplus_synchronized_correction_plan.v1.7.md
@docs/ootang_bplus_synchronized_correction_results.v1.7.md
@docs/ootang_bplus_pinn_equation_contract.v1.8.md
@docs/ootang_bplus_pinn_equation_validation.v1.8.md
@docs/ootang_bplus_pinn_substep_audit_plan.v1.8.md
@docs/ootang_bplus_pinn_substep_audit_results.v1.8.md
@docs/ootang_bplus_state_pinn_plan.v1.9.md
@docs/ootang_bplus_state_pinn_implementation.v1.9.md
@docs/ootang_bplus_state_pinn_results.v1.9.md
@docs/ootang_bplus_pinn_consistency_plan.v1.10.md
@docs/ootang_bplus_pinn_consistency_results.v1.10.md
@docs/ootang_bplus_shared_mechanics_plan.v1.11.md
@docs/ootang_bplus_shared_mechanics_results.v1.11.md
@docs/ootang_bplus_rate_learning_plan.v1.12.md
@docs/ootang_bplus_rate_learning_results.v1.12.md
@docs/ootang_bplus_rate_diagnostics_plan.v1.13.md
@docs/ootang_bplus_rate_diagnostics_results.v1.13.md
@docs/ootang_bplus_temporal_features_plan.v1.14.md
@docs/ootang_bplus_temporal_features_results.v1.14.md
@docs/ootang_bplus_temporal_decomposition_plan.v1.15.md
@docs/ootang_bplus_temporal_decomposition_results.v1.15.md
@docs/ootang_bplus_history_availability_plan.v1.16.md
@docs/ootang_bplus_history_completion_plan.v1.16.1.md
@docs/ootang_bplus_history_availability_results.v1.16.md

- 当前范围：**藕塘一个现有案例、四点位移概率预测，暂不做预警、不换新案例**。v1.1/v1.2/v1.3 的有限实验已完成，均未实现稳定四点提升，保留负结果。v1.3 两窗平均预测误差变差、逐点同时改善 1/8，且 612 日旧参数的新目标值优于新拟合最佳值；见对应结果和 progress。后续优化稳定性、训练内时序选模或神经模型变化须另记版本，不自动追加预算或训练。计划默认不冒充导师已确认的决定。
- v1.4 已按用户“继续进行下一步”完成实现、两项有限实验与数值核验：同前缀 J1 续算下降约 1.27%/2.92%，均未达 gtol；固定 J0 的内部选模在 432 日预测改善、612 日预测变差，严格逐点同时改善 0/8。实际新 nfev 12,470/14,800、拟合约 20.1 分钟，无新增神经训练。**v1.4 已结束，未实现稳定四点提升**；后续训练内误差分析、概率修正目标或模型变化须另记版本并依当前用户请求，不自动续算或追加预算。
- v1.5 已按“根据你的建议，一步步推进”完成诊断和同一 ConvLSTM 的 IN/OOF 样本来源对照。均值截止 342/432，随后 90/180 日估计常尺度；实际 1,200/1,200 更新、约 4.15 分钟，无新物理前向/拟合或 M2/PINN。两窗 B+ 预测平均 RMSE 58.0122/22.2448，IN 62.6232/22.9473，OOF 58.1161/42.1026 mm；严格改善 IN 2/8、OOF 0/8，仍未稳定改善四点。15 项测试及 checkpoint/尺度/概率/指标核验通过。**v1.5 按预算结束**；后续同日教师更换诊断已在 v1.6 独立完成，见下条。研究精度目标未实现，不自动训练到达标。
- v1.6 同日教师更换对照已完成：固定全部网络/尺度，4 原前向+2 独立轨迹、约 9.21 秒，无拟合/训练。OLD OOF 两窗预测 RMSE 128.9485/72.4677 mm，仍劣于 NEW B+ 58.0122/22.2448；旧教师下也未稳定四点改善。612 日 ATU1/ATU5 的 B+ 偏差大幅缩小，OOF 正修正却相近，支持当前模型存在修正需求迁移问题，不是唯一根因或物理因果。12 项测试及物理/checkpoint/概率/指标核验通过。**v1.6 诊断结束，整体精度目标未实现**；后续同步学习已在 v1.7 独立完成，见下条。
- v1.7 同步重拟合/受限修正已完成：复用 342A/432B/612B，U/L 两组、三种子、100 轮，1,800/1,800 更新、19.27 分钟，0 新物理拟合/前向。预测平均 RMSE：P0 58.0122/22.2448、U 60.5902/23.2161、L 59.5126/23.6080 mm；严格改善均 3/8，训练误差明显下降但外推及概率评分未改善。20 项相关测试、checkpoint/尺度/指标核验通过。**v1.7 已结束，整体目标未实现**，不按外层成绩追加轮数、改 A/D 或拼接点位。
- v1.8 降阶 PINN 方程接口及真实 64 子步核验已完成：三次原算法记录版积分、123,072 子步，每条 48 项检查通过，日末四点均值与原保存值差为 0 mm；科学进程含回放 3.496 秒，无新拟合/神经更新。10 项合成/梯度/记录/隔离测试及封存后只读复核通过。后续状态网络及有限训练另记 v1.9。不能将经验限幅 L 或旧 M2 改名成已训练的经典 PINN；整体精度和概率目标仍未完成。
- v1.9 固定训练、回放与补充核验已完成：1,800 更新、9 次原算法回放、369,216 子步，460.821 秒至核验前。原进程因保存前后报告 48/49 项字典比较失败而退出 1，失败与原源码保留；新增背景检查误差 0，补充完整核验通过，无补训/新积分/放宽门限。R 主输出两窗预测平均 RMSE 57.7985/45.8646 mm，对照 B+ 58.0122/22.2448，严格改善 2/8；P 诊断不能代替 R。342 日最终总目标恶化，612 日 P/R 在 ATU1/ATU5 的训练及预测均明显分离；具体原因尚待诊断。15 项相关测试、36 checkpoint 和全部时序/概率核验通过。**v1.9 按预算结束，整体目标未实现**。下一版先只读诊断训练目标及 P/R 一致性并核对旧 M2 路径，再独立登记方案；不自动追加训练、改变损失权重或按外层结果选 checkpoint。
- v1.10 冻结梯度/运动分解诊断已完成：52 次网络求值、324 次梯度、9 组代数分解，7.999 秒，0 更新/力学调用，全部核验通过。342 日最终物理/数据梯度范数比 4,136–6,002；612 日 ATU1/ATU5 大 P/R 差主要对应塑性状态差，运动残差差响应约 0.2–0.4 mm，不作物理因果解释。旧 M2 源码核对确认已通过原求解器训练，原开发所有训练 checkpoint 均劣于 e0。**本版诊断结束，整体目标未实现**。下一步先验证相同 G 输入/倍率下训练和最终输出共享力学路径的值与完整历史梯度，再另版登记有限学习对照；不能把这一思路冒称首次尝试或已验证有效，本诊断不自动追加训练预算。
- v1.11 同一 G 的共享力学路径已实现并通过有限验证：3 原参考前向、32 G 求值、36 条递推、4 次完整历史反向，7.945 秒，0 更新。12 组完整输出均值与旧 B+/R 最大差 2.274e-13 mm，6/6 指定小步长导数及两个未来隔离前缀通过；612 日一条较大扰动改变 1 个活动分支，保留记录。11 项相关测试、1,568 保护文件哈希及封存只读核验通过。**原型验证结束，整体目标未实现**；下一步独立固定同一 G 数据损失直接作用于最终力学输出的有限训练对照，保持原切分/种子/倍率与旧负结果，本版没有新训练预算或效果结论。
- v1.12 同一 G 直接力学输出学习已完成：1,800 更新、1,845 G/Day 前向、9 原算法回放，361.521 秒、exitcode=0，0 物理拟合/尺度网络。九个模型目标均下降，8 点窗训练 RMSE/MAE 均改善，但预测平均 RMSE 58.8431/24.7192 mm 仍高于 P0 的 58.0122/22.2448，严格改善 2/8。612 日较旧 R 的大偏差明显缩小仍未超过 B+；区间同时存在过宽与欠覆盖。13 项相关测试、36 checkpoint/九条回放及封存只读复核通过，1,634 保护文件保持。**本版预算结束，整体目标未实现**；下一步建议只读检查新 S 的修正方向/幅度、倍率与输入跨期变化及常尺度迁移，先形成假设再另版固定有限对照。保留原优化器、倍率、切分、标准、负结果与条件历史回测边界，不自动补训、改尺度、拼点或按外层选轮数；当前无下一版训练预算或导师接受记录。
- v1.13 冻结修正/倍率/输入/尺度诊断已完成：270 行、57.569 秒，0 新网络/梯度/力学调用/训练/尺度拟合。432 日 ATU1/ATU5 均值修正反向 177/180、180/180 日，612 日 MJ3 三种子均 180/180 日反向；612 日无倍率接近边界，单维输入越界仅 7/180 日，不能把全部失败归为触边或越界。n612 ATU1/ATU5 的旧 sigma 约 64/151 mm，下一窗 RMS 约 4–11 mm，MJ3 则达旧 sigma 的 5.26–5.38 倍。9 项测试、319,237 数值复核及 1,921 保护文件检查通过。**诊断结束，整体目标未实现**；下一步先固定训练内修正信息与时序检验，再决定有限输入/目标对照；旧 M2 已含状态且泛化失败，不把加状态、放宽倍率或加轮数当作已验证方法。当前无下一版训练/拟合预算，不按外层改模型或区间。
- v1.14 训练内时序输入对照已完成：复用 252B/342A/432B，9 次岭回归、3 常数/3 scaler 估计，8.419 秒，0 新神经/力学调用或概率尺度拟合。HHS 三窗预测平均 RMSE 16.3382/24.6583/72.1319 mm，P0 为 18.2392/29.5090/58.0122；前两窗改善、第三窗恶化，432 日 ATU1 180/180 日反向，RMSE 55.3860→119.5784。11 项测试、九组正规方程、120 指标/36 比较与时序核验通过，1,954 保护文件保持。**本版按预算结束，整体目标未实现**。下一步先对保存系数/输入作分组修正与跨期范围核对，再决定有限输入/目标对照；不能据辅助回归直接选原神经模型，不能据失败断言输入无信息。历史暴露、已知未来驱动及旧 M2 状态输入失败保持，无新增神经训练或外层选模预算。
- v1.15 冻结辅助回归分组/范围诊断已完成：762 行、3.237 秒，0 新拟合/神经/力学调用。432 日 ATU1 的 s/p 项合计 −80.3702 mm，超范围部分 −53.6015；总修正约 −63.1789，分为边界内 −13.4177 和超范围 −49.7613，事后需求 +54.5140。不能把限幅当成已验证修复，也不能把辅助项套用到原 S。HHS 三窗均全窗至少一维越界，极小越界须同时看幅度。6 项测试、九组分解/72 原指标核验通过，2,011 保护文件保持。**本版结束，整体目标未实现**。源码确认当前四点 M1 的 u/du 来自 B+，M2 用自身状态，当前 G 用水文输入；实测通过拟合/损失/初值进入，未显式输入近期实测序列。下一步先核对预测起点的观测来源、插值与真实可用时间，再决定历史观测编码的有限方案；这是待检验方向，无新训练预算，不误称模型完全未用实测或没有记忆，不改旧切分/模型/概率结论。
- v1.16 原三来源等值检查在 1.309 秒后 exitcode=1；公开 CSV/XLSX 前 612 日六列一致，导师 CSV 的 MJ3 有 240 值超过原 1e-12 容差，最大约 5e-12 mm。原源码、门限及失败完整保留。另版 v1.16.1 按先登记方案报告导师差异，并完成公开表的四起点 30 日历史输入；3.440 秒、0 新拟合/神经/力学/预测，14 项测试、1,920 次历史数值检查及来源/日期/旧指纹独立复核通过，2,074 保护文件保持。**历史接口完成，整体目标未实现**。四起点都切在既有月内三次段中间，只能证明下游没有额外读取未来行，原始日值当时可用性仍 unknown；不把数据血缘限制或原始 GNSS 无法取得扩大成原型阻断。下一步另版固定历史位移/增量编码与同一 B+ 的有限对照，整段预测不注入未来实测，保留旧切分、负结果和条件历史回测边界。本轮无新训练预算，不把接口通过写成精度/概率改善或三来源原等值通过。
- 开始或恢复本阶段工作时，先读下列导师目标、对应版本计划与结果，以及 `docs/progress.md` 最新本阶段记录，再实施、验证或报告；研究目标不等于已有结果。
- 旧报告、旧导师要求和旧待办仅为历史背景。阶段结束或替换时更新本入口，保留可追溯记录。

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

# Git Rules

- Commit a backup after each completed, verifiable step. Split distinct implementation, experiment-result, and documentation steps into separate commits; keep related tests with their feature/fix and exclude unrelated user files. A commit request does not by itself request a push.
- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
