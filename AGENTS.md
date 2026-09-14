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
- 最新完成（2026-09-15本地时间）：跨起点α/有限λ三步实验。432起点180日选择/校准，612/792/972/1168各发出完整293日；α五点、λ四点，42次新拟合16800更新、18旧拟合复用、5岭回归、11物理前向，56组分布全部完成。
- 结果：α选择0/0/0/1，λ选择3/3/3/0；历史均值门均0/3。972的λ平均改善但MJ3退步；最终两流程等同原版，RMSE9.279082，高于B+9.124173mm。固定半残差最终8.867132的局部收益保留，新增λ=1/3和3未超过已试λ=1的最终均值误差，不证明所有λ或RL无效。
- 228检查点、3339568数值、141事件与全部评分通过独立复算；五图20面板/88936图形值及目视通过。旧检查点step/updates字段兼容异常及图例修订原件保留，未重训已完成模型。
- 本轮完成并结束。新任务按用户明确授权处理，不自动追加模型、参数、轮数、PDF或push；独立16:31:07—18:31:07 UTC预算及剩余时间不延续。旧实验的停止记录和负结果保持。

# Reading Order

1. 先读[README](README.md)、[文档导航](docs/README.md)和最新[progress](docs/progress.md)。
2. 当前效果读[跨起点结果](docs/ootang_transformer_temporal_results.v1.0.md)、[核验](docs/ootang_transformer_temporal_validation.v1.0.md)、[最终回执](results/ootang_transformer_temporal_v1/20260914/final_receipt.json)；实现时再读对应冻结计划、配置和来源。
3. 需要对照时再读[半残差/校准](docs/ootang_transformer_calibration_results.v1.0.md)、[REG1](docs/ootang_transformer_regularization_results.v1.0.md)、[Transformer/CNN-Mamba](docs/ootang_sequence_conditional_results.v1.0.md)、[TCN](docs/ootang_tcn_conditional_training_results.v1.0.md)。
4. 旧协议按[历史导航](docs/history/README.md)定向追溯。历史预算、命令和形成时“尚未训练”不是当前待办。

# Experimental Boundaries

- 当前条件协议给定未来逐日降雨/水位，预测路径中不反馈实测位移，B+完整状态从首日延续。新起点可用此前观测重新训练，但保留旧发出预测。与1—7日滚动/固定驱动递推分开解释。
- 跨起点窗口部分重叠，第三历史窗与最终窗重叠97日；历史日期/400次训练选择曾暴露，仍属探索性。972教师参数拟合于792，只代表该固定参数复用流程，不假称每起点都重估B+。原as-of/预处理/物理优化未收敛限制保留。
- α、λ都在上一发出窗口前90日按四点平均MAE/RMSE选，后90日校准，路径内固定，禁止按点/种子/本窗口答案替换主输出。最终校准[1062,1152)，16日间隔保持。90个相邻误差不是独立重复；高斯边际区间不保证整条轨迹同时覆盖。
- 完整候选、日期、种子、原逐点/概率保护和失败记录保留；执行通过与效果/导师验收分别报告。两种选择流程最终相同，严格改善0/3表示相同而非额外退步。
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
