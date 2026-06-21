# Figures 产物说明

本目录保存可由 `code/*.py` 重建的结果快照。PNG 是展示图，CSV 是支撑图表、复核数值和追踪逐日结果的审计表；它们都不是原始监测数据，也不应手工修改。

| 文件 | 作用 | 类型 | 论文用途 |
| --- | --- | --- | --- |
| `pipeline/latest_run.json` | 保存统一入口最近一次实际运行的提交哈希、源码指纹、Python 版本、阶段契约状态、退出码、耗时及输出 SHA-256 | 工程验收清单 | 证明管线执行范围、产物完整性和失败点，不作为模型性能证据 |
| `convlstm/forecast_interval.png` | 展示 8 个测点的一日位移 P10/P50/P90 预测区间 | 最终图 | 位移预测主图 |
| `convlstm/forecast_metrics.csv` | 保存各测点校准前后的 RMSE、MAE、R2/NSE、持久性基线、pinball loss、覆盖率、宽度和 80% interval score | 最终评估表 | 生成位移预测结果表；用 `interval_variant` 区分原始和校准区间，R2/NSE 仅作补充 |
| `convlstm/forecast_period_metrics.csv` | 将 287 日测试段按日期连续分为三个块并保存校准前后同组指标 | 时间稳定性审计表 | 检查总体均值是否掩盖后期性能退化，不代替滚动时间验证 |
| `convlstm/forecast_calibration_metrics.csv` | 保存拟合/校准/测试日期边界、测点独立 `qhat` 及校准前后覆盖率、宽度、pinball 和 interval score | 校准审计表 | 证明校准期早于测试期并量化宽度-覆盖率代价；不提供时间序列下的严格覆盖保证 |
| `convlstm/forecast_bootstrap_ci.csv` | 保存总体和各测点在 7/14/30 日连续块下的点估计、95% 百分位区间、配对差值及完整重采样参数 | 不确定性审计表 | 14 日为主分析，7/30 日为敏感性；模型和 `qhat` 固定，不能解释为训练或未来漂移不确定性 |
| `convlstm/rolling_validation_folds.csv` | 保存三个扩展窗口折的拟合/校准/测试边界、模型配置、随机种子和逐测点 `qhat` | 验证协议审计表 | 证明每折时间隔离和测试长度来源；明确属于内部探索性验证 |
| `convlstm/rolling_validation_metrics.csv` | 保存每折总体及 8 测点的原始/校准区间指标、持久性基线和增量偏差 | 滚动评估表 | 必须逐折报告，不以平均值掩盖前两折失败 |
| `convlstm/rolling_validation_predictions.csv` | 保存三个测试折逐日逐测点的真实值、持久性、P10/P50/P90、校准端点和 `qhat` | 逐日审计表 | 复算滚动指标和诊断系统性高估/低估，不作独立外部证据 |
| `convlstm/seed_stability_runs.csv` | 保存种子 0-4 各折时间边界、固定参数、`qhat` 和增量尺度 | 多种子协议审计表 | 证明没有增删种子、改参或选择最佳种子 |
| `convlstm/seed_stability_metrics.csv` | 保存逐种子、逐折、总体/测点的点误差、增量响应和区间指标 | 多种子明细表 | 逐次报告初始化波动，不以跨种子均值替代失败运行 |
| `convlstm/seed_stability_summary.csv` | 保存五种子均值、样本标准差、范围、正 skill 数量和符号一致性 | 稳定性汇总表 | 判断结果方向是否依赖初始化；仍属已查看测试折上的探索性诊断 |
| `convlstm/seed_stability_training.csv` | 保存 15 次训练每个 epoch 的全批量 pinball loss 与梯度 L2 范数 | 优化轨迹表 | 检查数值稳定和损失趋势，不把训练损失当作泛化证据 |
| `ngboost/confusion_matrix.png` | 展示动态 V0 当日四级状态的混淆矩阵 | 最终图 | 状态识别结果图；测试段无橙/红样本 |
| `ngboost/warning_metrics.csv` | 保存 accuracy、F1、Brier、各等级支持数和召回率 | 最终评估表 | 支撑状态识别结果；无支持等级应写“不可评价” |
| `ngboost/warning_probabilities.csv` | 保存测试段逐日真实等级、预测等级和四级概率 | 逐日审计表 | 供概率校准、误差复核和融合旁证使用 |
| `shap/shap_reg_summary.png` | 展示位移增量回归的 SHAP 分布 | 最终图 | 解释模型依赖的候选指标 |
| `shap/shap_cls_summary.png` | 展示动态 V0 当日状态分类的 SHAP 分布 | 最终图 | 解释模型依赖，不作因果结论 |
| `shap/shap_reg_importance.csv` | 保存回归 mean absolute SHAP 排序 | 支撑表 | 生成变量重要性表和跨折稳定性分析 |
| `shap/shap_cls_importance.csv` | 保存分类 mean absolute SHAP 排序 | 支撑表 | 生成变量重要性表和跨折稳定性分析 |
| `shap/shap_model_metrics.csv` | 保存单次时间留出的回归/分类指标和样本信息 | 最终评估表 | 说明当前探索性性能和类别不平衡 |
| `shap/shap_binary_cv_metrics.csv` | 保存 5 折扩展窗口分类结果及持续性基线 | 交叉验证审计表 | 逐折报告；单类别折不能汇总 AUC |
| `tangent_angle/uniform_rates.csv` | 保存各测点自动等速候选段、参考速率和稳定性统计 | 参数审计表 | 专家复核 `v_eq`，不能直接当作已验证参数 |
| `tangent_angle/review/MJ9_stage_review.png` | MJ9 累计位移、速率、加速度和 15/30/60 日候选阶段复核图 | 专家复核图 | 供专家结合宏观变形资料独立确定等速阶段，不标注"最佳阶段" |
| `tangent_angle/review/MJ1_stage_review.png` | MJ1 累计位移、速率、加速度和 15/30/60 日候选阶段复核图 | 专家复核图 | 同上 |
| `tangent_angle/review/MJ3_stage_review.png` | MJ3 累计位移、速率、加速度和 15/30/60 日候选阶段复核图 | 专家复核图 | 同上 |
| `tangent_angle/review/candidate_stage_comparison.csv` | 三个关键测点在 15/30/60 日窗口下的参数来源、速率统计、切线角等级、相对 30 日一致率及融合影响 | 综合审计表 | 供专家核对参数影响，不得按一致率或报警天数自动选优 |
| `thresholds/v0_thresholds.csv` | 保存 8 个测点共享的动态 V0、5V0、10V0、公式参数和方法来源 | 参数审计表 | 区分导师指定一级公式与 Chen（2024）高等级倍数来源 |
| `sensitivity/v0_sensitivity.csv` | 汇总 15/30/60 日窗口与 0.85/0.90/0.95 截断分位数组合的等级、事件和默认一致率 | 敏感性摘要表 | 说明 V0 结论对预设参数的依赖范围，不用于选优 |
| `sensitivity/v0_parameters.csv` | 保存 9 组配置下每个测点的 V0、5V0、10V0 和估计样本数 | 参数审计表 | 追溯 V0 敏感性结果到测点参数 |
| `sensitivity/tangent_sensitivity.csv` | 汇总 27 组候选窗口、平滑和持续性规则的最终等级、融合原因与一致率 | 敏感性摘要表 | 区分等速候选窗口与工程平滑规则的影响 |
| `sensitivity/tangent_parameters.csv` | 保存 15/30/60 日候选窗口选出的等速段、`v_eq` 和稳定性统计 | 参数审计表 | 供专家对照累计位移曲线复核等速阶段 |
| `warning_onset/onset_events.csv` | 保存连续黄色及以上事件的起止、持续时间和可预测性 | 事件审计表 | 说明独立事件数量 |
| `warning_onset/onset_targets.csv` | 保存逐日 at-risk 状态及未来 1/3/7 日 onset 标签 | 派生标签表 | 后续未来预警模型的目标表 |
| `warning_onset/onset_inventory.csv` | 汇总各窗口正负日期和可预测事件数量 | 摘要表 | 判断是否具备可靠建模和置信区间条件 |
| `warning_fusion/warning_fusion.csv` | 保存 V0、切线角、NGBoost 旁证、最终等级和融合原因 | 最终融合表 | 逐日审计规则是否升级以及为何升级 |

## 保留原则

- 论文图表和 `docs/results_report.md` 引用的结果快照保留在 Git 中，以便数值可追溯。
- 三个阶段原先各自保存的 `v0_thresholds.csv` 内容完全相同，现合并为 `thresholds/v0_thresholds.csv`。
- 其余 CSV 承担不同任务，不是重复文件。需要清理空间时可以整体删除并按 README 的运行顺序重建，但不要只删除某一张支撑表后继续引用旧结果。
- 如果未来 SHAP、NGBoost 和 onset 使用不同的 V0 参数，必须按分析范围分别命名输出，不能继续覆盖公共阈值表。
