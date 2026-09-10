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

- 当前范围：**藕塘一个现有案例、四点位移概率预测，暂不做预警、不换新案例**。v1.1/v1.2/v1.3 的有限实验已完成，均未实现稳定四点提升，保留负结果。v1.3 两窗平均预测误差变差、逐点同时改善 1/8，且 612 日旧参数的新目标值优于新拟合最佳值；见对应结果和 progress。后续优化稳定性、训练内时序选模或神经模型变化须另记版本，不自动追加预算或训练。计划默认不冒充导师已确认的决定。
- v1.4 已按用户“继续进行下一步”完成实现、两项有限实验与数值核验：同前缀 J1 续算下降约 1.27%/2.92%，均未达 gtol；固定 J0 的内部选模在 432 日预测改善、612 日预测变差，严格逐点同时改善 0/8。实际新 nfev 12,470/14,800、拟合约 20.1 分钟，无新增神经训练。**v1.4 已结束，未实现稳定四点提升**；后续训练内误差分析、概率修正目标或模型变化须另记版本并依当前用户请求，不自动续算或追加预算。
- v1.5 已按“根据你的建议，一步步推进”完成诊断和同一 ConvLSTM 的 IN/OOF 样本来源对照。均值截止 342/432，随后 90/180 日估计常尺度；实际 1,200/1,200 更新、约 4.15 分钟，无新物理前向/拟合或 M2/PINN。两窗 B+ 预测平均 RMSE 58.0122/22.2448，IN 62.6232/22.9473，OOF 58.1161/42.1026 mm；严格改善 IN 2/8、OOF 0/8，仍未稳定改善四点。15 项测试及 checkpoint/尺度/概率/指标核验通过。**v1.5 按预算结束**；后续同日教师更换诊断已在 v1.6 独立完成，见下条。研究精度目标未实现，不自动训练到达标。
- v1.6 同日教师更换对照已完成：固定全部网络/尺度，4 原前向+2 独立轨迹、约 9.21 秒，无拟合/训练。OLD OOF 两窗预测 RMSE 128.9485/72.4677 mm，仍劣于 NEW B+ 58.0122/22.2448；旧教师下也未稳定四点改善。612 日 ATU1/ATU5 的 B+ 偏差大幅缩小，OOF 正修正却相近，支持当前模型存在修正需求迁移问题，不是唯一根因或物理因果。12 项测试及物理/checkpoint/概率/指标核验通过。**v1.6 诊断结束，整体精度目标未实现**；后续同步学习已在 v1.7 独立完成，见下条。
- v1.7 同步重拟合/受限修正已完成：复用 342A/432B/612B，U/L 两组、三种子、100 轮，1,800/1,800 更新、19.27 分钟，0 新物理拟合/前向。预测平均 RMSE：P0 58.0122/22.2448、U 60.5902/23.2161、L 59.5126/23.6080 mm；严格改善均 3/8，训练误差明显下降但外推及概率评分未改善。20 项相关测试、checkpoint/尺度/指标核验通过。**v1.7 已结束，整体目标未实现**，不按外层成绩追加轮数、改 A/D 或拼接点位。
- v1.8 降阶 PINN 方程接口及真实 64 子步核验已完成：三次原算法记录版积分、123,072 子步，每条 48 项检查通过，日末四点均值与原保存值差为 0 mm；科学进程含回放 3.496 秒，无新拟合/神经更新。10 项合成/梯度/记录/隔离测试及封存后只读复核通过。后续状态网络及有限训练另记 v1.9。不能将经验限幅 L 或旧 M2 改名成已训练的经典 PINN；整体精度和概率目标仍未完成。
- v1.9 固定训练、回放与补充核验已完成：1,800 更新、9 次原算法回放、369,216 子步，460.821 秒至核验前。原进程因保存前后报告 48/49 项字典比较失败而退出 1，失败与原源码保留；新增背景检查误差 0，补充完整核验通过，无补训/新积分/放宽门限。R 主输出两窗预测平均 RMSE 57.7985/45.8646 mm，对照 B+ 58.0122/22.2448，严格改善 2/8；P 诊断不能代替 R。342 日最终总目标恶化，612 日 P/R 在 ATU1/ATU5 的训练及预测均明显分离；具体原因尚待诊断。15 项相关测试、36 checkpoint 和全部时序/概率核验通过。**v1.9 按预算结束，整体目标未实现**。下一版先只读诊断训练目标及 P/R 一致性并核对旧 M2 路径，再独立登记方案；不自动追加训练、改变损失权重或按外层结果选 checkpoint。
- v1.10 冻结梯度/运动分解诊断已完成：52 次网络求值、324 次梯度、9 组代数分解，7.999 秒，0 更新/力学调用，全部核验通过。342 日最终物理/数据梯度范数比 4,136–6,002；612 日 ATU1/ATU5 大 P/R 差主要对应塑性状态差，运动残差差响应约 0.2–0.4 mm，不作物理因果解释。旧 M2 源码核对确认已通过原求解器训练，原开发所有训练 checkpoint 均劣于 e0。**本版诊断结束，整体目标未实现**。下一步先验证相同 G 输入/倍率下训练和最终输出共享力学路径的值与完整历史梯度，再另版登记有限学习对照；不能把这一思路冒称首次尝试或已验证有效，本诊断不自动追加训练预算。
- v1.11 同一 G 的共享力学路径已实现并通过有限验证：3 原参考前向、32 G 求值、36 条递推、4 次完整历史反向，7.945 秒，0 更新。12 组完整输出均值与旧 B+/R 最大差 2.274e-13 mm，6/6 指定小步长导数及两个未来隔离前缀通过；612 日一条较大扰动改变 1 个活动分支，保留记录。11 项相关测试、1,568 保护文件哈希及封存只读核验通过。**原型验证结束，整体目标未实现**；下一步独立固定同一 G 数据损失直接作用于最终力学输出的有限训练对照，保持原切分/种子/倍率与旧负结果，本版没有新训练预算或效果结论。
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
