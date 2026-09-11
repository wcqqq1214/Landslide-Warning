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
@docs/ootang_convlstm_direct_results.v2.0.md
@docs/ootang_probability_pinn_plan.v2.1.md

历史规格与结果按 `docs/README.md` 查证；不把其中形成时的“当前计划”自动加载为当前任务。

- **当前有效状态：v2.1 概率 PINN 方案已完成，未实现、未训练；最新已完成实验 v2.0 未达标并已停止。** 用户明确只限定 ConvLSTM/PINN；非神经状态空间模型建议已撤回。本次交付为具体方案，不宣称已执行、有效或通过导师验收。
- 范围保持：**藕塘一个现有案例、MJ3/MJ1/ATU5/ATU1 四点位移概率预测，暂不做预警、不换新案例**。导师目标仍是均值误差低于改进 B+，同时改善概率预测。实现、测试、提交与训练损失下降都不能代替效果达标。
- v1.1 的三组为 M0 改进 B+、M1 ConvLSTM、M2 方程内修正混合模型；M2 不冒充经典 PINN。v1.9 状态 PINN 的主输出仍是 R，诊断 P 不得替代主输出。原模型、切分、阈值、选模记录、负结果和限制保持。
- 截至 v1.25 的八轮主要学习、16 个变体同窗汇总：没有一个同时降低两窗平均 RMSE 或同时降低两窗平均 CRPS，严格四项改善最高 3/8。v1.25 四组均 2/8，IN_CARRY 两窗 RMSE 59.2967/25.4071 mm，高于 P0 的 58.0122/22.2448。**当前保留 B+ 基线，整体精度/概率目标未完成。**
- v1.26 未完成、未运行，已按用户清理要求从工作树删除其计划、配置及草稿目录，共九个文件；原件可从 Git `7fc5f29` 恢复，仅供追溯。没有活动 v1.26 入口或运行授权。
- v2.0 DIRECT 严格均值改善 5/8，但预测平均 RMSE 为 64.4117/22.1677 mm（P0 58.0122/22.2448），CRPS 为 47.2449/26.9716 mm（P0 42.0113/25.1140），完整均值与概率判据失败。局部改善数量更多不代表稳定四点提升。
- v2.0 方案、实现与实验产物分别提交 e49ac92、b3ba6c7、453762f；一次运行 34.20 秒，900 次均值/600 次尺度更新，7 项相关检查通过，全部 15 个模型重载一致。全流程处于原 120 分钟窗口内（07:33:17–09:33:17 UTC），剩余预算不转用于新实验。未重新拟合 B+，未重训旧变体。
- v2.1 方案/配置提交 `1ebb98a`：受限广义力偏差的降阶状态 PINN，力偏差末层采用变分贝叶斯近似；同一条件状态用于数据、物理约束和预测。首个固定模型的既定抽样须通过原力学容差，失败即停止整个候选。总投入 120 分钟，方案记账 30 分钟，后续实现、运行及收尾最多 90 分钟；详见冻结方案，不视为自动开跑许可。
- 不用“局部问题尚可继续诊断”自动延长路线。任一候选失败或总时限耗尽，记录并停止；不得换版本重置预算、拼点/种子、事后选轮数或缩短预测窗来制造改善。出现正证据也不自动开启下一轮。
- 开始或恢复工作先读本入口、最新 progress、v2.0 结果及 v2.1 方案。后续执行依用户具体请求，沿用对应冻结方案和停止条件。v1.1–v1.25 与 v2.0 的原结果、配置及源码均保留；旧文档中的“下一步”仅为历史背景，研究目标不等于已有结果或导师验收。
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
