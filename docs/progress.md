# 项目工作进度

> 更新日期：2026-08-17。本文件记录工程与研究实现进度；研究协议以 `advisor_review_action_plan.md` 为准，结果数值以版本化 CSV 和运行清单为准。历史条目保留其原始日期和门禁数字，不与当前工程门禁混读。

## 2026-08-17 NGBoost 区间代理 pilot 与提前量敏感性

- 新增显式阶段 `ootang-ngboost-interval-proxy-pilot`，默认链仍严格为 `features → convlstm → ootang-operational-v4`。本阶段只读取既有藕塘 ConvLSTM、逐点运动学和 v4 比较基准，不重训/修改 ConvLSTM，不修改 v4，也未读取或引入其他案例。
- 使用当前 `interval_z`、逐点速度、原始 `ΔV` 和连续切线角四项指标，加 8 个测点 one-hot 控制量，预测下一自然日的五级原始区间偏离代理状态。物理加速度、环境变量、校准后区间和 v4 融合等级均未进入输入。
- 严格同测点、同 split、一日配对得到 fit/calibration/test=`7280/1808/2288`，目标五级支持分别为 `3538/2870/701/83/88`、`531/847/287/143/0`、`584/1039/182/133/350`。calibration 无 red，相关指标明确记为不可定义。
- 固定 `NGBClassifier` 五分类参数，只用 fit 训练，不做搜索、早停、重拟合、类别权重、SMOTE、合成标签或事后概率校准。calibration/test 全时刻 accuracy 为 `0.906/0.947`、macro-F1（支持类）为 `0.905/0.925`；状态持续基线分别为 `0.916/0.952` 与 `0.923/0.936`，NGBoost 未超过简单持续性基线。
- 状态转移行上 NGBoost calibration/test accuracy 仅为 `0.132/0.209`；test 的 macro-F1 `0.229` 和 ordinal MAE `0.791` 优于多数类基线的 `0.083/1.527`，但 calibration 未稳定复现。因此该模型只保留为探索性概率 pilot，不引入主流程，不改动既有模型或论文结论。
- 版本化产物位于 `figures/ngboost_interval_proxy_pilot_ootang_v1/`，模型为 `models/ootang_ngboost_interval_proxy_pilot_v1.pkl`，完整边界与结果见 `docs/ootang_ngboost_interval_proxy_pilot.md`。
- 在模型、四指标、测点控制量、训练策略和类别处理完全相同的条件下，新增显式 h=1/3/7 提前量敏感性。fit/calibration/test 样本分别为 h1 `7280/1808/2288`、h3 `7264/1792/2272`、h7 `7232/1760/2240`；三个 fit 均含五类，三个 calibration 均无 red。
- h1/h3/h7 的 calibration 全时刻 accuracy 为 `0.906/0.781/0.715`，对应持续基线为 `0.916/0.833/0.744`；test 为 `0.947/0.860/0.675`，对应持续基线为 `0.952/0.876/0.773`。三个 horizon 的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过持续基线，且 log loss、Brier、ECE 随提前量增加而整体升高。
- 状态转移行信息随提前量增加而增多，部分转移指标优于简单基线，但没有在 calibration/test 和不同指标间稳定一致。敏感性产物明确 `selection_performed=false`、`ranking_performed=false`，不输出最佳 horizon，不改变当前“不引入主流程”的判断。完整结果见 `docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md`。

## 2026-08-15 已退役产物清理

- 按用户决定删除已退役路线的版本化产物，只保留当前 v4 链所需目录。删除 `figures/` 下 `ngboost/`、`warning_fusion/`、`warning_onset/`、`thresholds/`、`sensitivity/`、`warning_draft/`、`warning_operational_draft/`、`warning_operational_draft_v2/`、`warning_operational_draft_v3/`、`warning_review/` 共 82 个跟踪文件，另删 `pipeline/latest_run.json`（v3 阶段残留记录）与 `pipeline/shap_stability_run.json`（已退役 `shap-stability` 阶段）。
- 删除前已核验：这 10 个目录在 `code/`、`main.py` 和 `tests/` 中引用数均为 0；v4 链只读 `figures/convlstm/`，写 `figures/warning_draft_v4/` 与 `figures/warning_operational_draft_v4/`。删除后 v4 核心 manifest 的 13 个路径 SHA-256 全部匹配，`main.py --dry-run` 仍精确为 `features → convlstm → ootang-operational-v4`。
- `figures/tangent_angle/` 未删：`features` 阶段仍向其写出 `uniform_rates.csv`，删除会打断默认管线。`figures/shap/` 及 `shap/stability/` 未删：仍被 `paper/process_report.tex` 引用。
- 本次清理不改动任何 v4 数值、阈值、模型或协议内容哈希，也未启动 Vajont。删除项一律按 Git 历史（提交 `7d2e38b` 及之前）恢复，不在当前目录重建同名文件。
- 副作用：`ootang_stable_segment_expert_review.md` 与 `ootang_interval_calibration_expert_review.md` 内嵌的审查图和支撑 CSV 链接已失效，两份文档的文字结论仍有效。

## 2026-08-13 当前代码树与解释支路同步

- 当前可执行最小链严格为 `features → convlstm → ootang-operational-v4`；旧 30 日 `V0` 标签、旧融合以及 v1/v2/v3 运行入口和对应测试已从工作树移除，仅保留在 Git 历史。
- 独立解释支路改为 NGBoost 回归 + SHAP：目标是下一观测位移增量，输出候选模型依赖；它不是 ConvLSTM-SHAP、因果主控因素识别或正式五级预警分类。
- v4 数值产物重跑后仍为 4,112 条测点记录、514 条滑坡体记录和 8 行阈值；加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`。代码清理只更新来源指纹和解释产物，不改这些 v4 数值。
- 正式 NGBoost 未启动：缺少独立五级结局标签。不能把当前四指标规则输出作为标签，再以同一输入训练模型并称为正式验证。
- 本节之后的 v1/v2/v3、旧 SHAP/分类和历史测试数量均为时间戳所示的历史记录，不描述当前入口。

## 2026-08-11 v4 严格逐点加速度扩展收口（阈值来源于 2026-08-13 澄清）

- 导师确认逐点导数方法及“相同阈值”。经课题组内部方法核对后，v4 沿用速度 `V0` 基线形式和 `1×/5×/10×` 相对结构，而非不存在的严格加速度阈值表：以加速度自身 `A0` 量纲一致转置，`a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，真实 `dt`，单位 `mm/day²`，三点 warmup；raw `delta_v` 仍只作审计。
- fit-only 稳定段阈值固定为 `A=mean(a)`、`sigma_a=sample std(ddof=1)`、`A0=max(1.5A,A+2sigma_a)`；`A0<=0` 或非有限时 fail-closed。五级为 green `<A0-sigma_a`、blue `[A0-sigma_a,A0+sigma_a]`、yellow `(A0+sigma_a,5A0)`、orange `[5A0,10A0)`、red `>=10A0`。
- v4 使用 O1/O2/O3 双轴空间规则（实现最初形成于 v3 草案，但当前只由 v4 调用）；速度/切线角仍是同一运动学 family，加速度独立计票。8 点、514 日输出已复算：测点加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`，滑坡体整体 green/blue/yellow/orange/red=`8/48/31/9/18`，`valid=114`、`candidate_not_site_confirmed=400`。
- v4 核心与图件 manifest 均绑定源码指纹、输出哈希与行数、v1 基础协议及 v2 扩展协议双哈希，并保留 `formal_warning_output=false`、`vajont_used=false`。默认入口现为 `features → convlstm → ootang-operational-v4`；v3 数值仅为保留的历史快照，不再有可执行对照入口。NGBoost 正式预警模型仍未完成，Vajont 未启动。
- 本轮不改变既有 v3 核心数值 CSV；共享 runner 源码哈希变化仅刷新 v3 manifest/图件 provenance，未将 v3 数值混入 v4 阈值。

## 2026-08-08 代码库审查与工程收口

- 这是 2026-08-08 的历史工程收口记录：当时 v3 及其余阶段仍为 explicit-only。2026-08-13 后，旧运行入口与专属代码已移至 Git 历史；保留的 MVIF、6 通道和旧预警产物仍不混入当前主结果。
- 44 个测试文件已纳入 Git。当前全量门禁为 `361 passed`、`52 subtests passed`；Ruff、Python 编译检查和 `main.py --dry-run` 均通过。该门禁证明工程快照可复核，不证明藕塘数据具备确认性证据或正式预警有效性。
- 统一入口清单升级为 schema 3，逐阶段保存输入/输出路径、大小、SHA-256、源码指纹和工作树状态。当时的 `latest_run.json` 已于 2026-08-15 作为 v3 残留记录删除；当前 HEAD 尚无端到端运行清单，下次完整运行会重新生成。
- v2/v3 配置锁定的 Wang 论文 PDF 只作为空间拓扑来源证据，不是计算输入；本地副本存在时必须匹配锁定摘要，缺失时允许原型计算并在运行清单记录未核验状态，错误副本会 fail-closed。v2 历史清单未因本次代码审查统一刷新，不应据此声称所有历史字段均已更新。
- 本次没有重新训练模型、改动数值产物或启动 Vajont。Vajont 仍须用户明确授权；后续若获准，必须先冻结其角色并建立独立数据/评价目录。

本节是工程收口记录，不替代 2026-08-04 的 7 通道科学结果，也不解除 `confirmatory_evidence_gate=blocked`、`formal_warning_output=false` 或最终论文门禁。

## 当前阶段

| 项目 | 状态 | 可核对产物 |
| --- | --- | --- |
| 历史十三阶段统一管线 | 已完成（加入高程前的历史快照） | 旧运行记录中的 13/13 阶段与产物哈希；不代表当前高程感知模型已重跑全部历史诊断 |
| 藕塘高程感知最小链路 | 已完成初跑；v4 为当前默认草案 | v2/v3 数值快照保留；v4 产物见 `figures/warning_operational_draft_v4/` |
| 藕塘阶段性结果包 | 已完成 | `docs/ootang_stage_results_package.md` 统一汇总可写/不可写结论、证据门禁和后续数据决策 |
| 代码目录按研究流程分组 | 已完成 | `code/features/`、`code/warning/`、`code/explainability/`、`code/convlstm/`；入口路径已在 `main.py`、`README.md` 和 `docs/design.md` 同步 |
| ConvLSTM 高程静态通道 | 已完成初跑 | `elev_m` 标准化后经水平 IDW 形成静态网格；`figures/convlstm/forecast_run_manifest.json` 记录坐标哈希和处理方法 |
| ConvLSTM 日历后置校准 | 已完成（当前单次初跑） | `figures/convlstm/forecast_calibration_metrics.csv`；不证明上游日值生成独立 |
| ConvLSTM 配对日期块 95% 区间 | 已完成 | `figures/convlstm/forecast_bootstrap_ci.csv` |
| ConvLSTM 7 通道 fixed-120 诊断 | 三折滚动与五种子已完成；早停/容量未运行 | `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/`；历史 6 通道根目录产物只作对照，不是 7 通道证据 |
| 高程与空间预警专家审查 | 已完成 | `docs/ootang_elevation_warning_expert_review.md`；400 日成因、典型日、课题组内部方法边界及高程可信性已核对 |
| v2 空间融合覆盖门禁 | 已修复 | `minimum_assessable_station_count=3` 先于全部颜色执行；2 个跨区 yellow 点反例及 v2 兼容语义均有测试 |
| 滑坡体 green/blue 语义 | v3 草案已实现并复算 | 双轴输出整体确认等级与局部最高候选；green `8`、blue `48`，局部 blue 关注 `8` 日 |
| 全时刻预警状态展示 | 已完成（非正式、观测后） | 514 日 × 8 点候选色带及 `site-confirmed/local maximum` 双轴；400 个 NC 明确不是缺测 |
| 位移—四指标—最终等级联合图 | 已完成（非正式、观测后） | 4×2 小多图覆盖 8 点 × 514 日，逐点对齐累计位移和五条状态带 |
| SHAP 跨折稳定性与特征组消融 | 已完成 | `figures/shap/stability/`；固定 5 折、5 个特征组和任务专属主指标 |
| 藕塘数据血缘 | 已审查并拆分门禁 | `source_recovery_status=unavailable_by_project_constraint`；原型初跑允许，确认性证据与正式预警阻断 |
| 新神经调参/机理消融与正式日预测 | 暂停 | 7 通道 fixed-120 结果已查看，不据此优化；早停/容量未运行，自然月分段三次结构仍限制确认性解释 |
| Vajont 案例 | 未启动 | 本轮 fixed-120 未读取、未适配、未运行；此前仅做过只读内容盘点，不构成启动，开始前必须获得用户明确许可 |
| NGBoost 区间代理 pilot | 已完成显式初跑；不进入默认链 | 11,376 条一日配对、五级概率与基线比较；calibration/test 未超过状态持续基线 |
| NGBoost h=1/3/7 提前量敏感性 | 已完成显式、非排名初跑 | 同一模型与输入并列报告；三个 horizon 全时刻 accuracy、macro-F1、ordinal MAE 均未超过持续基线，不选择最佳提前量 |
| NGBoost 未来 onset 正式调参 | 暂停 | 当前仅 3 个互不相连的可预测标签事件，不满足稳定调参与外层评价条件；区间代理 pilot 不解除该门禁 |
| 切线角等速阶段确认 | 待导师或现场资料决定 | `figures/tangent_angle/review/` 已覆盖 8 个测点；当前无 `approved` 人工阶段 |

## 当前滚动验证协议

1. 保持现有 ConvLSTM 结构、7 日输入和 1 日预测步长，不更换模型。
2. 使用 3 个扩展窗口折，每折测试 287 个连续日，测试段互不重叠。
3. 每折训练段末 20% 作为日历上后置的 calibration 期；标准化、增量尺度和测点 `qhat` 只使用该折允许的表格历史行。
4. 每折报告总体和逐测点误差、持久性基线、区间覆盖率、宽度、pinball loss 和 interval score，不只报告跨折均值。
5. 当前物化序列和留出时段已参与多轮分析，且上游生成独立性未知；滚动结果仅作探索性内部时间验证，不作为外部确认性证据。

> 本协议已于 2026-08-04 用当前 7 通道 fixed-120 完成三折滚动和五种子诊断。早停与容量敏感性没有随本轮运行；2026-06-21/22 的对应记录均为历史 6 通道证据。

## 2026-08-04 7 通道 fixed-120 三折与五种子记录

- 按运行前冻结协议完成 `seed=0` 三折滚动和 `seed=0-4` × 3 折的 15 个拟合；没有挑选最佳种子，也没有让测试折参与选择。
- rolling 与 five-seed 阶段的 `seed=0` 折元数据、54 行指标和 6,888 行预测在 `1e-12` 容差内复现；五种子保留 15 行运行、270 行指标、1,800 行训练记录和 34,440 个唯一完整的 `seed × fold × date × station` 预测键。
- fold 1/2 的 RMSE 和 MAE 对 5/5 种子均劣于持久性基线；平均 RMSE 分别为 `1.970/0.356 mm`，基线为 `0.245/0.120 mm`。
- fold 3 对 5/5 种子的 RMSE/MAE 仅小幅改善：平均 RMSE `0.328 mm`，基线 `0.340 mm`，平均 RMSE skill `0.036`。但预测增量标准差比仅 `0.156`，平均增量相关 `-0.041`，仅 1/5 种子为正；该优势伴随强平滑，不是稳定的逐日动态跟踪证据。
- 校准后 P10–P90 coverage 在三折为 `0.387/0.956/0.754`；fold 1 欠覆盖、fold 2 过覆盖、fold 3 略欠覆盖，不能只报 coverage 而忽略宽度和 interval score。
- 本轮是藕塘公开物化日序列上的内部探索性诊断。历史 6 通道对照只能描述版本变化，不能当作 7 通道证据或高程因果消融。
- 7 通道早停和容量敏感性未运行；Vajont 也未启动，后续开始必须先得到用户明确许可。
- 版本化产物位于 `figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/`，管线清单为 `figures/pipeline/convlstm_elevation_fixed120_v1_run.json`，完整审查见 `docs/ootang_convlstm_elevation_fixed120_review.md`。

## 2026-08-04 藕塘原型文档收口记录

- 已同步 `README.md`、方法/设计/框架状态、结果、限制、进度、图件说明及 `main.py` 阶段契约；当前 ConvLSTM 主结果统一为 7 通道 fixed-120 三折 × 五种子，历史 6 通道滚动、早停和容量结果均明确隔离。
- 当前 7 通道最后一折 `seed=0` 的 14 日时间块结果已保留：模型相对持久性基线的 RMSE/MAE 差异 95% 区间均跨 0；三折 × 五种子 bundle 尚未扩展为逐折逐种子的全面 bootstrap。
- 文档已统一报告站点异质性、强平滑、区间失配及物化日序列血缘限制；该同步完成的是内部原型记录，不解除 `confirmatory_evidence_gate=blocked`，也不把结果升级为正式预警证据。
- Vajont 本轮未启动；如用户以后明确允许，须先冻结其外部验证、补充案例或方法演示角色，再建立独立数据与评价协议。
- 提交前全量门禁为 `355 passed`、`45 subtests passed`；2 项失败仍是旧 `V0` 方法名和旧切线角列断言，未出现本轮新增回归。科学证据轴与规范轴独立审查均为 P0=0、P1=0。

## 2026-08-01 v3 空间规则实施记录

- 修复 v2 的 P0 覆盖门禁：少于 3 个可评估测点时，任何 site 颜色都不能返回；v2 的“全分区仅约束 green”历史语义保持不变，当前 v2 四份产物 SHA-256 未改变。
- 新增独立 `ootang-operational-spatial-v3` 配置、融合模块、运行入口和 `figures/warning_operational_draft_v3/`（该目录已于 2026-08-15 删除，仅存于 Git 历史），没有覆盖 v1/v2。
- v3 将 `site_confirmed_level` 与 `local_max_candidate_level` 分轴。所有 site 颜色先要求至少 3 点并覆盖 O1/O2/O3；blue 也要求至少 2 点跨 2 区；未确认 yellow–red 不降级；孤立/单区 blue 记为 site green + `localized_blue_attention`。
- 514 日仍有 `valid=114`、`candidate_not_site_confirmed=400`；整体确认色为 green `8`、blue `48`、yellow `31`、orange `9`、red `18`，另有 400 日不发布整体颜色；局部最高候选为 blue `56`、yellow `196`、orange `111`、red `151`。
- v2/v3 的 4112 条测点时间线新增 `trend_component`、`transition_status`、`evidence_consistency_status` 和 `composite_warning_signal`：`ΔV` 三态现在改变完整信号和理由，但不改变五色候选，也不作为速度/切线角之外的独立投票。候选色和 514 日滑坡体统计保持不变。
- 已将六个冻结语义的代表日诊断纳入同一 v3 阶段，输出可编辑 SVG、PDF、300 dpi PNG 与 provenance manifest；图中未确认 site 显式为 `NC`，并逐日列出确认支撑、局部最高测点和 O1/O2/O3。
- 已加入 514 日完整时间线图：上半图覆盖 8 点全部候选状态，下半图并列整体确认与局部最高；400 个未确认日以灰色 NC 表示且明确为“非缺测”。
- 已加入 8 点联合诊断图：每点显示累计位移，以及 interval、velocity、`ΔV` 三态、tangent angle 和 final candidate；三类 v3 图件共用带源码指纹的公开 provenance/导出支持层。
- 所有 v3 产物继续标记 `operational_draft_not_formal`、`formal_warning_output=false`、`vajont_used=false`。

## 2026-07-30 高程感知初跑记录

- 用户确认原始 GNSS 无法取得，导师要求先使用现有公开藕塘序列和 `data/station_coords.csv` 的高程完成案例跑通；藕塘不一定用于最终论文。
- 不删除原有来源审查，而是拆分为：

  ```text
  source_recovery_status = unavailable_by_project_constraint
  prototype_run_gate = allowed
  confirmatory_evidence_gate = blocked
  formal_warning_output = false
  ```

- 修复了此前 `elev_m` 未进入 ConvLSTM 的实现落差。当前采用“8 点高程 z-score → 按 `x_m/y_m` 水平 IDW → 静态高程通道”，不把高程直接并入三维距离；输入由 6 通道变为 7 通道。
- 最小链路三阶段全部通过，最终复跑耗时约 `40.7 s`。预测表包含 fit `7288`、calibration `1816`、test `2296` 条测点记录，主键无重复。
- 最后 287 日物化 test 段总体 RMSE 为 `0.338 mm`，持久性为 `0.340 mm`，RMSE skill 为 `0.007`；校准后 P10–P90 覆盖率为 `0.770`。流程已通，但没有明显优于简单基线。
- 与提交前的无高程单种子快照相比，总体 RMSE 约由 `0.318 mm` 增至 `0.338 mm`，平均逐点 RMSE skill 由约 `0.082` 降至 `0.019`。这是事后描述，不用于反向调节模型或高程尺度。
- v2 输出包含 `4112` 个测点—时刻和 `514` 个滑坡体时刻；四项输入均无缺失。`114` 个时刻满足当前项目特有空间确认，`400` 个保留为 `candidate_not_site_confirmed`，不得并入 green。
- 所有当前产物继续标记为原型/非正式，Vajont 未读取、未运行，且启动前必须得到用户明确许可。

## 2026-07-30 高程与空间预警专家审查

- 审查报告见[`藕塘高程通道与空间预警结果专家审查`](ootang_elevation_warning_expert_review.md)。
- 高程作为静态地形先验可提高输入结构的物理合理性，但课题组内部方案的“物理引导”实际来自稳定性系数和半经验物理位移，并使用 GCN/T-GCN/ST-GCN；当前高程 ConvLSTM 是项目改造，不是该方法的复现。
- 在相同 `11400` 个预测键、观测和 persistence 下，高程版相对无高程单种子快照的 test RMSE/MAE 分别增加 `0.0196/0.0158 mm`；14 日配对块重采样的差值区间均高于 0。由于 test 已查看且只有单种子，该结果只是否定当前已显示提升，不构成确认性消融。
- 400 个未空间确认日全部为 8/8 测点和 3/3 分区有效，并非缺失：`189` 日不足 2 个 yellow+ 点，`211` 日已经达到至少 2 点但仍全部位于 O1。
- 对应 `755` 条 O1 yellow+ 测点记录的候选等级全部由区间指标决定；当前 orange/red 不能解释为速度或切线角达到同级。
- v2 的 514 日 site 输出没有 green，说明“任一 blue 即 site blue、8 点全 green 才 site green”不适合把绿色作为常态；该审查建议已于 2026-08-01 通过全局门禁修复和独立 v3 双轴草案落实。
- 本轮没有调整阈值、模型或 test，也没有读取或启动 Vajont；Vajont 仍受用户明确许可门禁约束。

## 2026-07-28 数据血缘审查记录

- 仓库 `monitoring_data.xlsx` 与 Wang 等（2025）Figshare 文件 MD5 完全一致；CSV 与工作簿 1461×17 的日期、列和数值等价。
- 8 条位移和 GWT 在 48/48 个自然月内呈三次指纹，5 个环境负对照为 0/48；月内第四差分无断点，断点集中在自然月边界。
- 首个模型目标、fit→calibration、calibration→test 三个边界均切穿同一月内三次段；跨边界恢复只作为代数依赖诊断，不写成预测性能或已证实未来泄漏。
- Figshare 的 11 个公开 notebook 没有生成该结构的代码，也没有公开原始 GNSS/GWT 锚点、日值处理链或 MJ/ATU 映射。
- 原始数据恢复现已确认不作为当前可执行路线；历史事实仍保留。
- 当前 `prototype_run_gate=allowed`、`confirmatory_evidence_gate=blocked`、`formal_warning_output=false`、`vajont_used=false`；计划中的机理性神经消融仍暂停。
- 典型状态日和结果可解释性审查已经完成；v2 门禁与不覆盖 v2 的 v3 green/blue 双轴草案也已完成。下一步等待最终论文数据集选择；若换数据集，重新建立数据契约和确认性验证协议。

## 本轮完成门槛

- 输出逐折计划、逐日预测和逐折/逐测点指标 CSV。
- 测试覆盖时间隔离、测试段不重叠、固定协议和管线产物契约。
- 同步更新 README、设计、研究框架、结果、限制和本进度文档。
- 全量测试、Ruff、编译和完整管线通过；运行清单中的源码及产物哈希可复核。

## 2026-06-21 历史 6 通道滚动验证记录

- 三个测试折均为 287 日且互不重叠，输出已通过固定种子逐字节确定性复跑。
- 模型/持久性 RMSE：折 1 为 2.123/0.245 mm，折 2 为 0.492/0.120 mm，折 3 为 0.318/0.340 mm。
- 逐测点 RMSE 优于基线数量：0/8、0/8、8/8；当前 ConvLSTM 不能表述为跨时期稳定优于持久性基线。
- 校准覆盖率：48.8%、94.9%、75.2%；第二折覆盖率上升伴随区间过宽和 interval score 恶化。
- 全量门禁：135 项测试和 32 个子测试通过；Ruff、编译、CSV 完整性及有限数检查通过。
- 当时的九阶段完整管线通过，运行清单源码指纹与代码一致，36/36 个产物哈希复核通过；最新十一阶段验收见下文。
- 功能提交：`bdf14e5`（`feat: add convlstm rolling validation`）；运行清单及本进度记录随后的维护提交另行保存。

## 2026-06-21 历史 6 通道后续诊断与外部工具筛选

- 基于已冻结的逐日预测结果开展事后诊断，未重新训练或修改参数。三折总体日增量相关系数分别为 0.182、0.148、0.011，逐测点相关系数中位数分别为 0.062、0.055、-0.068。
- 第三折相对持久性基线的 RMSE 优势伴随预测增量方差明显偏小，因此目前只能表述为该折点误差较低，不能表述为已稳定捕捉位移加速和减速过程。
- 种子 `0-4` 的固定三折训练稳定性诊断已经完成，共 15 次训练，未选择最佳种子或修改超参数。
- 已检查 `modelscope/Awesome-Vibe-Research` 及相关候选项目。PaperQA2、RefChecker 和 `nature-figure` 分别可能用于本地文献核对、投稿前参考文献验证和图件审查；Curie/EurekAgent 的实验隔离思想可参考，但其指标驱动自动优化不宜直接用于当前已查看的测试折。
- 当前未向本仓库或本机 Codex 环境接入任何上述外部项目；接入前必须取得用户明确批准。
- 工具用途、风险、采用时机和状态已持久化到 `docs/research_tools.md`。

## 2026-06-21 历史 6 通道五种子诊断记录

- 折 1/2 的 RMSE 和 MAE 均为 0/5 种子超过持久性基线；折 3 均为 5/5，说明初始化影响幅度但不改变跨折方向。
- 折 1/2 的 RMSE 为 2.385 +/- 0.574 和 0.390 +/- 0.143 mm，基线为 0.245 和 0.120 mm；折 3 为 0.323 +/- 0.008 mm，基线为 0.340 mm。
- 折 3 日增量相关性为 -0.048 +/- 0.220，预测/实际增量标准差比为 0.164 +/- 0.022；不能把点误差优势解释为稳定捕捉加速/减速。
- 所有训练 loss 下降且梯度有限，但最后 10 个 epoch 的 loss 仍下降 4.4%-8.3%。下一步应先在拟合期内部锁定时间验证和停止规则，再决定有限调参；现有校准段和测试折不得参与选择。
- 全量门禁：143 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。
- 十阶段完整管线通过，运行清单源码指纹与功能提交 `97c4acf` 一致，40/40 个产物哈希复核通过。
- 四张五种子 CSV 在独立运行和完整管线运行间 SHA-256 完全一致；运行清单及本进度记录随后的维护提交另行保存。

## 2026-06-22 历史 6 通道内层验证实施记录

- 在任何新结果产生前，已将 80%/20% 内层时间切分、300 轮上限、30 轮最少观察、30 轮耐心、0.1% 最小相对改进和验证 pinball loss 选择规则写入 `framework.md`，并以提交 `3c9a616` 单独保存和推送。
- 新阶段保留原固定 120 轮结果，不修改模型结构、学习率、输入窗口、损失函数或特征；每个种子和外层折独立选择 epoch，再在完整拟合期重新初始化训练。
- 15/15 次内层选择均由耐心规则停止；折 1/2/3 的所选 epoch 中位数为 22/7/1，范围为 16-61、3-20、1-98，没有运行达到 300 轮上限。
- 相对固定 120 轮，三折总体 RMSE 分别有 5/5、5/5、4/5 个种子改善；但相对持久性基线，折 1/2 仍为 0/5，折 3 为 5/5。训练轮数影响失败幅度，但没有解决跨时期失效。
- 第三折覆盖率由 75.2% 升至 81.1%，同时宽度由 0.471 增至 0.977 mm、interval score 由 1.037 恶化至 1.248 mm；早停不能概括为所有评价维度均改善。
- 七张结果 CSV 在单阶段运行和完整管线运行间 SHA-256 完全一致。十一阶段完整管线耗时 1172.5 秒，11/11 阶段和 47/47 个产物哈希通过。
- 全量门禁：152 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。功能提交为 `ae41ef9`，运行清单及结果文档随后的维护提交另行保存。

## 2026-06-22 历史 6 通道有限容量/正则化诊断实施记录

- 在任何候选结果产生前，已将隐藏通道 `8/16`、Adam 权重衰减 `0/1e-4`、折内五种子平均验证 loss 排名、并列规则和停止扩搜判据写入 `framework.md`，并以提交 `d13292e` 单独保存和推送。
- 新阶段保留当前 `16/0` 配置作为参照，不改变学习率、输入窗口、卷积核、特征、外层折或校准规则；外层测试不参与配置排名。
- 折 1/2/3 仅按内层五种子均值分别选择 `h16_wd0`、`h08_wd0`、`h16_wd1e4`；三个折没有共同最优配置，第一/二名 loss 差值均远小于种子标准差。
- 最终相对持久性基线的 RMSE/MAE 正 skill 种子数为 0/5、0/5；0/5、0/5；5/5、4/5。只有折 3 达到多数种子双指标正 skill，触发预注册的停止扩搜规则。
- 折 2 内层选择的小模型在外层较当前早停参照平均增加 0.070 mm RMSE 和 0.061 mm MAE；不能把内层微小排名差异解释为稳定泛化增益。
- 首次运行的严格零容差参照检查因最大 `2.22e-16` 的 CSV 浮点尾差停止，未写出结果；随后以 `5 x float64 epsilon` 锁定验证 loss 容差，并用 `1e-12` 配对指标容差避免将数值噪声标记为改善。修复提交为 `e9711cc` 和 `875f832`。
- 十二阶段完整管线耗时 1693.9 秒，12/12 阶段、56/56 个产物哈希和源码指纹均通过。八张不受配对标签修复影响的容量 CSV 与先前单阶段运行 SHA-256 一致。
- 全量门禁：160 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。功能提交为 `7507dac`，最终结果与清单随后的维护提交另行保存。

## 2026-06-23 SHAP 稳定性与组消融记录

- 在结果产生前以提交 `7456598` 锁定五折、每折背景/解释日期、88 个特征、五个特征组、回归 MAE 和分类 Brier 主指标；当前数据已被探索，协议不表述为前瞻性注册。
- 功能提交 `38713fd` 实现跨折 SHAP 排名、方向相关、组级贡献和 drop-one-group 消融，并接入统一入口为第 4 阶段。
- 首次正式运行暴露方向统计的 pandas 索引对齐错误：SHAP 数组使用位置索引，而样本特征保留原索引，导致方向全为空。修复提交 `3c06d38` 将两者显式按位置对齐；绝对 SHAP、排名和消融结果不受影响。
- 修复后正式运行耗时 3291.3 秒，阶段及 9/9 产物契约通过，源码指纹和产物哈希见 `figures/pipeline/shap_stability_run.json`。运行使用 `caffeinate -i`，避免 Mac 熄屏暂停进程；网络断开不影响本地训练。
- 回归组排名折间 Spearman 中位数为 1.000，分类为 0.500；只有位移运动学组在回归 MAE 和分类 Brier 中均为 5/5 折删去后变差。
- 环境组的删组方向不一致，不能解释为环境因素无物理作用；分类运动学贡献又与 30 日位移速率标签存在定义耦合，不能当作独立提前预警发现。
- 全量门禁在修复后为 171 项测试和 32 个子测试通过；Ruff、编译和差异检查通过。
- 十三阶段完整管线耗时 3731.9 秒，13/13 阶段、65/65 个产物哈希和提交 `6cdcc35` 均通过。`shap-stability` 在保持输出数值一致的情况下耗时 2243.9 秒；先前单阶段 3291.3 秒的额外耗时与 Mac 熄屏暂停或系统负载有关，不作为模型性能证据。
