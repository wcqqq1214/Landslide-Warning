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

@docs/ootang_route_review_2026-09-11.md
@docs/progress.md
@docs/ootang_bplus_probabilistic_experiment_plan.v1.1.md
@docs/ootang_bplus_sequence_learning_results.v1.25.md

- **当前有效状态：按用户 2026-09-11“先修正”要求，暂停自动扩展实验与诊断。** 当前工作是汇总既有模型效果、保留失败证据、纠正推进策略；旧持续目标及各版预算不能覆盖本次暂停决定。
- 范围保持：**藕塘一个现有案例、MJ3/MJ1/ATU5/ATU1 四点位移概率预测，暂不做预警、不换新案例**。导师目标仍是均值误差低于改进 B+，同时改善概率预测。实现、测试、提交与训练损失下降都不能代替效果达标。
- v1.1 的三组为 M0 改进 B+、M1 ConvLSTM、M2 方程内修正混合模型；M2 不冒充经典 PINN。v1.9 状态 PINN 的主输出仍是 R，诊断 P 不得替代主输出。原模型、切分、阈值、选模记录、负结果和限制保持。
- 已完成八轮主要学习、16 个变体的同窗汇总：没有一个同时降低两窗平均 RMSE 或同时降低两窗平均 CRPS，严格四项改善最高 3/8。最新 v1.25 四组均 2/8，IN_CARRY 两窗 RMSE 59.2967/25.4071 mm，高于 P0 的 58.0122/22.2448。**当前保留 B+ 基线，整体精度/概率目标未完成。**
- v1.26 只完成过计划，五个未完成源码草稿已在 `archive/paused/ootang_sequence_state_v1_26/` 封存，未完成验证、未运行。不要导入、补完或执行该草稿；历史计划/配置保留，局部预算不代表运行授权。
- 暂停期间可读取已保存证据、作必要数字汇总、修正文档和准备一个候选方案；不自行启动新训练、拟合、模型推断、梯度诊断或新诊断版本。旧 `--verify` 入口可能仍进行模型计算，不因名称为“只读复核”而自动运行。
- 后续若提出一次实验，先交付一个具体、可审阅的候选：证据、区别于既有失败的改法、完整均值/概率判据、总时间上限与失败退出条件。路线复盘建议整个候选工作最多 120 分钟（含准备、实现、检查、计算、解释和提交），这是待确定的预算建议，**不是执行授权**；用户确定具体方案后再执行。
- 不用“局部问题尚可继续诊断”自动延长路线。任一候选失败或总时限耗尽，记录并停止；不得换版本重置预算、拼点/种子、事后选轮数或缩短预测窗来制造改善。出现正证据也不自动开启下一轮。
- 开始或恢复工作先读本入口、路线复盘、最新 progress 及相关冻结计划/结果。v1.1–v1.25 全部旧结果保留在版本文档与产物中；旧文档里的“下一步”仅作历史背景，由本次决定取代。研究目标不等于已有结果；计划不冒充用户或导师已接受。
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

# Git Rules

- Commit a backup after each completed, verifiable step. Split distinct implementation, experiment-result, and documentation steps into separate commits; keep related tests with their feature/fix and exclude unrelated user files. A commit request does not by itself request a push.
- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
