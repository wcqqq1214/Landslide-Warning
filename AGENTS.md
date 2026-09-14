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

# Current Scope and Status

- 范围：藕塘同一剖面 ATU1、ATU5、MJ3、MJ1 的位移均值与概率区间。使用现有固定数据；不自动扩展到新案例、数据补采或真实预警。
- 最新完成（2026-09-14）：Transformer 残差幅度正则化 REG1，λ=1、原结构与三种子/400 更新保持。开发 376 日与最终 293 日均已完整执行并核验；整体效果条件在两阶段均未通过。
- 结论：REG1 相对原版两阶段均值/概率配对改善，最终平均 MAE/RMSE 较 B+ 降低 5.25%/2.38%；开发四点落后 B+，最终 MJ3 退步、仅 1/3 种子 RMSE 优于 B+，主概率未优于 B+。局部收益、完整失败和探索性限制同时保留。
- 本轮实验已结束。后续工作以用户新的明确任务为准；不自动追加模型、超参数、训练轮数、PDF 或 push，旧预算和余额不恢复或转用。历史停止记录不阻止后续明确的新授权，历史授权也不自动延续。

# Reading Order

1. 先读 [README](README.md) 与 [文档导航](docs/README.md)，确定当前任务涉及的版本。
2. 判断现有效果时，读 [REG1 结果](docs/ootang_transformer_regularization_results.v1.0.md)、[核验](docs/ootang_transformer_regularization_validation.v1.0.md) 和 [最终回执](results/ootang_transformer_regularization_v1/20260914/final_receipt.json)；涉及实现时再读对应冻结计划、配置和来源清单。
3. 需要同协议比较时，再读 [Transformer / CNN-Mamba](docs/ootang_sequence_conditional_results.v1.0.md) 与 [TCN](docs/ootang_tcn_conditional_training_results.v1.0.md) 结果。
4. 最近维护见 [progress](docs/progress.md) 顶部；旧协议和方法按 [历史导航](docs/history/README.md) 定向查阅。历史文档中的“当前”“尚未训练”、命令和预算均按形成时间解释，不自动加载为待办或执行指令。

# Experimental Boundaries

- 当前导师条件协议给定未来逐日降雨/库水位，B+ 使用原参数和完整连续状态，预测段无位移观测反馈。它与 1—7 日滚动预测、固定未来驱动的 293 日递推分开解释，不能跨协议混排成绩。
- 重复使用历史日期的后期结果属于探索性评价。公开日序列的来源、预处理和 as-of 限制保留；低误差不等于新盲测或真实预警有效。
- DIRECT / BRES 使用相同合法物理输入时，配对检验的是残差输出方式，不能称有/无物理信息消融；TCN / Transformer / CNN-Mamba 残差模型不称 PINN，不宣称严格满足 B+ 方程。旧模型身份按原版本记录。
- 标准化、教师、选模和误差池遵守对应冻结版本的信息边界；主输出、逐点保护与概率规则不因最终成绩改变。完整日期、所有规定种子和失败结果保留，诊断组合不能替代主输出。
- 复核优先使用保存数组、CSV 和检查点。维护导航时保留被配置、源码或来源锁引用的原路径和原字节；退役只撤下默认入口，旧回执与清单不回写。

# Mentor Sources and Goal

- 原始资料：[manuscript.pdf](manuscript.pdf)、[同内容中文报告](藕塘滑坡_二维模型B加_优化预测报告.pdf)、[section2d_v4.zip](section2d_v4.zip)。两份 PDF 内容相同，优先读 manuscript，不作为两份独立证据；原件不覆盖。
- ZIP 交付根目录为 `section2d_v4/work/delivery_final/outang_repro/`。涉及公式、参数、数据或数值时，实际读取对应 PDF 页或 ZIP 文件并记录来源；路径和摘要不等于已读原文。
- 导师目标是通过改进 B+ 引导的智能时空概率预测，提高四点训练/预测均值精度，使误差小于 B+，并合理覆盖实测、量化不确定性、增强可信度。ConvLSTM/PINN 是早期建议，模型选择以明确的新方案为准；方法论断核对原始文献。
- 导师允许困难尾段存在偏差，未指定可删除日期；不以补齐截点作为前置条件。完整窗口及尾段误差保留，不删数据或改判旧模型达标；需要追溯时见历史导航中的 293 日复核与 v2.2 需求记录。

# Git Rules

- Commit a backup after each completed, verifiable step. Split distinct implementation, experiment-result, and documentation steps into separate commits; keep related tests with their feature/fix and exclude unrelated user files. A commit request does not by itself request a push.
- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
