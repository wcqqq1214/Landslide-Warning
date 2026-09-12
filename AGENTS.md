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
@docs/ootang_tail_scope_and_direction.v2.2.md
@docs/ootang_293day_prediction_audit_2026-09-11.md
@docs/ootang_probability_pinn_plan.v2.3.md
@docs/ootang_probability_pinn_results.v2.3.md

历史规格与结果按 `docs/README.md` 查证；不把其中形成时的“当前计划”自动加载为当前任务。

- 2026-09-12 用户明确批准新实验，本轮采用此前提出的 60 分钟总时限（08:24:03—09:24:03 UTC）。实验现已完成并停止，三种子各 300 次、共 900 次更新，仅运行 792/376 开发阶段；不把剩余时间移用于下一轮。授权、执行与累计用时见 progress。
- **当前有效状态：v2.3 水文蠕变概率 PINN 开发段实验失败并已停止，保留 B+，未进入最终 293 日训练。** 376 日开发四点平均 MAE 为 177.5373 mm（B+ 33.2651），平均 RMSE 190.1728 mm（B+ 41.3684），CRPS 166.6350 mm（v1.1-e0 28.1156），90% 覆盖率 0%。九项候选检查均未过，神经/同背景 C 解相差很大；实现与复算通过不等于效果通过。原 v2.1 和四参数力探针继续暂缓。已完成的最终 293 日复核仍为 e0 无均值增益，平均/合并 RMSE 9.1242/9.4506 mm；不与本次开发窗混排。
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
- 开始或恢复工作先读本入口、最新 progress、v2.3 结果和 293 日复核报告；按需追溯形成时方案。v2.3 已停止，不根据旧配置再次运行。v1.1–v1.25、v2.0 原结果/配置/源码及 v2.1/v2.2 记录保留，旧文档的“下一步”仅为历史背景。研究目标不等于已有结果或导师验收。
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
