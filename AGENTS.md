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

- 范围：藕塘同一剖面ATU1、ATU5、MJ3、MJ1的位移均值与概率区间；现有固定数据，不自动扩展案例、补采或真实预警。
- 最新完成（2026-09-15本地时间）：起点条件化小型交叉注意力尝试。完整历史COND_ATTN、去显式历史观测NO_OBS_ATTN、均匀池化POOL_MLP三臂；612校准启动，792/972/1168全部293日评价，固定36拟合7200更新、144检查点，B+重拟合0、物理前向2。
- 结果：三臂均只在972历史窗通过B+均值门，792和最终失败，完整概率门三窗均未过。最终平均RMSE分别24.702585/24.565083/24.929915，B+9.124173mm。显式历史/注意力的配对收益仍不稳定，旧固定半残差8.867132局部收益保持，不推论整个网络家族无效。
- 253来源、144权重精确重载及独立NumPy前向、9027212数值/83事件核对通过；三图12面板/35247图形值及目视通过。432教师覆盖笔误在训练前勘误，绘图整数JSON序列化故障/初版保留，未重训或改评分。
- 本轮完成并结束。新任务按用户明确授权处理，不自动追加模型、参数、轮数、PDF或push；独立17:47:02—19:17:02 UTC自限预算及剩余时间不延续。旧实验的停止记录和负结果保持。

# Reading Order

1. 先读[README](README.md)、[文档导航](docs/README.md)和最新[progress](docs/progress.md)。
2. 当前效果读[起点条件化结果](docs/ootang_transformer_origin_results.v1.0.md)、[核验](docs/ootang_transformer_origin_validation.v1.0.md)、[最终回执](results/ootang_transformer_origin_v1/20260914/final_receipt.json)；实现时再读对应冻结计划、实现勘误、配置和来源。
3. 需要对照时再读[跨起点α/λ](docs/ootang_transformer_temporal_results.v1.0.md)、[半残差/校准](docs/ootang_transformer_calibration_results.v1.0.md)、[REG1](docs/ootang_transformer_regularization_results.v1.0.md)、[Transformer/CNN-Mamba](docs/ootang_sequence_conditional_results.v1.0.md)、[TCN](docs/ootang_tcn_conditional_training_results.v1.0.md)。
4. 旧协议按[历史导航](docs/history/README.md)定向追溯。历史预算、命令和形成时“尚未训练”不是当前待办。

# Experimental Boundaries

- 当前条件协议给定未来逐日降雨/水位，预测路径中不反馈实测位移，B+完整状态从首日延续。新起点可用此前观测重新训练，但保留旧发出预测。与1—7日滚动/固定驱动递推分开解释。
- 跨起点窗口部分重叠，第三历史窗与最终窗重叠97日；历史日期/400次训练选择曾暴露，仍属探索性。972教师参数拟合于792，只代表该固定参数复用流程，不假称每起点都重估B+。原as-of/预处理/物理优化未收敛限制保留。
- 本轮固定e200、λ=1，无选模，三个新臂全部报告；训练伪起点教师≤m，目标在当前拟合前缀内成熟，标准化用当前训练段而非冒充历史独立在线预测。612只有180日可训练目标、最终1168教师与episode教师≤792的差异保持。
- 本轮上一发出路径第91—180日校准，最终[1062,1152)，16日间隔保持。90个相邻误差不是独立重复，距离迁移和高斯边际区间不保证完整293日同时覆盖。旧α/λ选择流程仅为历史，不恢复搜索。
- 完整候选、日期、种子、原逐点/概率保护和失败记录保留；执行通过与效果/导师验收分别报告。历史输入可影响模型的正控通过不等于历史状态有效可迁移；起点条件原型不制造训练段神经拟合曲线。
- 新旧概率误差来源和距离支持不同，跨版本概率变化不能全部归因模型。旧半残差/DIST90的局部收益及限制保持。
- 模型使用同合法物理特征时，残差输出配对不能称有/无物理信息消融；Transformer等不称PINN或严格满足B+。旧模型身份、冻结源码和来源路径保持，复核优先保存数组/检查点。

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
