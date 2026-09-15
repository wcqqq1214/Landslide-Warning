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

- 最新完成（2026-09-15）：用户授权的历史B+教师更新政策配对，分支`codex/teacher-refresh-pairing`，见`docs/ootang_teacher_refresh_results.v1.0.md`及最终回执。两臂同GRU起点表达/共同60日网格/三种子/e200，10次历史物理更新、24神经拟合4800更新、96检查点及四条293日发报全部完成。G_CACHED三个主要窗RMSE19.372056/22.660456/10.600134，G_REFRESH24.684374/23.804193/11.068245mm；更新组集成MAE/RMSE三窗均退步，同种子同时改善0/2/1个；两组B+联合门均0/3，最终B+9.124173。历史拟合8/10降低RMSE，但全部物理拟合达到800次上限、未收敛，不能断言所有更新教师方法无效。203来源、5588338独立数值、三图12面板/222772图形值核验与目视通过；18项原运行时依赖已按原字节归档，原资料不改。新独立11:01:47—13:01:47 UTC自限窗口含准备核验，实际用时见回执，旧预算及余额不转用。按冻结计划收束本轮路线，不追加教师密度/轮数/结构/λ/RL，只本地commit/Markdown/CSV/PNG/SVG，不push/PDF。图件封装相对路径错误仅修目录根，图形/预测保持；用户未跟踪导师制图规则原样，用户/导师尚未验收。
- 上一轮完成（2026-09-15）：训练充分性诊断及一次固定200对400预算对照，分支`codex/training-sufficiency-audit`。见`docs/ootang_training_sufficiency_results.v1.0.md`及最终回执。96旧检查点/128×32固定面板、截止1168日完整成熟的612/792路径触发追加；完整24新拟合9600实际更新（4800重放＋4800追加），72新检查点，前200步日志/参数/预测精确一致。最终GRU11.141437→11.339123、TF13.290363→13.653310mm，B+9.124173；训练MSE仍降16.21%—21.87%，但972/最终RMSE退步。两400版本最终概率门对B+通过、均值失败，联合均0/3；不证明充分收敛或网络家族无效。1329来源、401128/1265488诊断/追加数值、四图16面板已核验。独立10:08:15—11:08:15 UTC含准备交付，实际时间见回执；已停止，不追加800次/结构/λ/RL，不转用旧预算。只本地commit/Markdown/CSV/PNG/SVG，不push/PDF。CSV读取审计及图件初稿错误留档，正式拟合无失败重试；导师/用户尚未验收。
- 上一轮完成（2026-09-15）：用户授权统一接口GRU/Transformer×起点表达消融，分支`codex/transformer-anchor-ablation`，见`docs/ootang_backbone_anchor_results.v1.0.md`及最终回执。固定14维输入/图/解码/uniform样本/三种子/e200/λ=1，GRU1241、单层TF1249参数。复用24份GRU拟合，新增24份TF拟合4800更新；全部192新旧检查点、1106来源、3813856数值及九图36面板核验完成。最终G00/G10/T00/T10 RMSE14.257499/11.141437/15.981473/13.290363mm，B+9.124173；A三窗平均收益可迁移，A开时GRU三窗均值/CRPS更低，但四组B+联合门均0/3。TF起点版ATU1/ATU5覆盖63.82%/47.44%，GRU起点版ATU1覆盖76.11%；旧半残差8.867132及G11 10.785141局部结果保留，不推广骨干家族。新09:18:32—11:18:32 UTC自限窗口含准备核验，实际用时见回执；本轮已停止，不追加网络/λ/RL或转用旧预算。Markdown/CSV/PNG/SVG、本地分步commit，不push/PDF。参数计数与锁路径训练前异常留档，正式24拟合无失败重试；用户/导师尚未验收。
- 上一轮完成（2026-09-15）：用户授权的起点残差表达×边界采样2×2消融。分支`codex/gru-boundary-ablation`，见`docs/ootang_gru_ablation_results.v1.0.md`和最终回执。48拟合9600更新/192检查点、四前缀/三种子/e200全部完成；612校准启动，三个主要窗完整293日评分，无B+重拟合/物理前向。708来源/3439904数值独立复算、G00旧模型48检查点复现、5图20面板/406161图形及数据值核验通过。最终G00/G10/G01/G11 RMSE14.257499/11.141437/11.563858/10.785141mm，均高于B+9.124173；G11首日跳偏减小但ATU1/MJ3均值仍退步，ATU1覆盖67.24%。两因素平均效应改善，条件效应不稳定；四组B+联合门均0/3。独立06:30—08:30 UTC包含准备核验，实际结束见回执，剩余预算不转用。Markdown/CSV/PNG/SVG、本地分步commit完成，不push、不追加训练或RL；未交付PDF，仅临时同源图形QA载体。用户/导师尚未验收。下方夜间记录保持历史完成状态。
- 范围：藕塘同一剖面ATU1、ATU5、MJ3、MJ1的位移均值与概率区间；现有固定数据，不自动扩展案例、补采或真实预警。
- 上一轮完成（2026-09-15本地时间）：用户新授权的夜间固定小图与因果残差诊断。GRU_LOCAL/GRU_GRAPH各1241参数，只有固定MJ3—MJ1邻接边不同；全历史GRU8、固定200更新/λ=1/三种子，612校准启动、792/972/1168完整293日评价。24拟合4800更新、96检查点，新增B+拟合/物理前向0。
- 结果：两臂仅过972历史均值门，792/最终未过；最终概率门通过，但均值/概率联合门三窗均未过。最终LOCAL/GRAPH平均RMSE14.283633/14.257499，B+9.124173mm；图相对本点的均值/概率门三窗均未过。前1152日因果EMA30诊断符合点0，未触发GRU_GRAPH_DUAL，双头拟合0；不推广为全部空间/分解方法无效。
- 301来源、96检查点重载/独立NumPy前向、11110304数值/62事件核对通过，最大差3.30e-12；六页PDF/24面板/70346图形值和逐页面视、字体/1.5pt对齐/碰撞通过。初版诊断页注释碰撞与三页预览保留，最终v2只修排版，不改数据/训练。用户/导师验收尚未获得。
- 本轮新授权窗口18:23:23—次日01:00 UTC（北京时间09:00前），已提前结束；新授权明确允许本轮PDF，不追溯覆盖旧无PDF记录。各步本地commit，不push；不自动增加图边/结构/轮数、双头或RL，剩余时间不转用。前一轮起点小试36拟合7200更新的失败和旧半残差8.867132mm局部正结果保持。

# Reading Order

1. 先读[README](README.md)、[文档导航](docs/README.md)和最新[progress](docs/progress.md)。
2. 当前效果先读[历史教师更新配对](docs/ootang_teacher_refresh_results.v1.0.md)、[核验](docs/ootang_teacher_refresh_validation.v1.0.md)、[回执](results/ootang_teacher_refresh_v1/20260915/final_receipt.json)；实施追溯对应计划/配置/203来源/成熟支持与实现。上轮读[训练预算核验](docs/ootang_training_sufficiency_results.v1.0.md)、[核验](docs/ootang_training_sufficiency_validation.v1.0.md)、[回执](results/ootang_training_sufficiency_v1/20260915/final_receipt.json)；按需再读[骨干×起点表达](docs/ootang_backbone_anchor_results.v1.0.md)、[GRU两因素消融](docs/ootang_gru_ablation_results.v1.0.md)及[夜间简报](docs/ootang_overnight_graph_results.v1.0.md)，不恢复旧预算。
3. 需要对照时再读[起点条件化小试](docs/ootang_transformer_origin_results.v1.0.md)、[跨起点α/λ](docs/ootang_transformer_temporal_results.v1.0.md)、[半残差/校准](docs/ootang_transformer_calibration_results.v1.0.md)、[REG1](docs/ootang_transformer_regularization_results.v1.0.md)、[Transformer/CNN-Mamba](docs/ootang_sequence_conditional_results.v1.0.md)、[TCN](docs/ootang_tcn_conditional_training_results.v1.0.md)。
4. 旧协议按[历史导航](docs/history/README.md)定向追溯。历史预算、命令和形成时“尚未训练”不是当前待办。

# Experimental Boundaries

- 本轮共同训练起点432至1152、间隔60日，两组同样重训；G_CACHED不冒充旧G10精确复现。更新教师使用当时合法前缀、最近原缓存初始化、单阶段800次上限；改变物理输入/起点基线/目标整项政策，不等于目标单因素或物理因果。外层B+保持原教师，完整成熟293日监督起点0/2/5/8，最终最晚完整起点852，更近教师只具部分成熟目标；未收敛与场景差异限制保持。
- 当前条件协议给定未来逐日降雨/水位，预测路径中不反馈实测位移，B+完整状态从首日延续。新起点可用此前观测重新训练，但保留旧发出预测。与1—7日滚动/固定驱动递推分开解释。
- 跨起点窗口部分重叠，第三历史窗与最终窗重叠97日；历史日期/400次训练选择曾暴露，仍属探索性。972教师参数拟合于792，只代表该固定参数复用流程，不假称每起点都重估B+。原as-of/预处理/物理优化未收敛限制保留。
- 上轮骨干×起点表达固定e200、λ=1、原uniform样本，无选模，四组全部报告；旧夜间实验唯一双头候选未触发，不属于当前待办。训练伪起点教师≤m，目标在当前拟合前缀内成熟，标准化用当前训练段而非冒充历史独立在线预测。该旧实验612只有180日可训练目标、最终1168教师与episode教师≤792的差异保持。
- 图为固定分区内等权边，不按全序列相关估权；本点神经对照仍有共享权重及B+物理耦合，不等同完全无空间信息。EMA30严格单边，报告协方差；96驱动相关仅描述性，不作因果或显著性宣称。
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
