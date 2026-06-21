# 项目工作进度

> 更新日期：2026-06-21。本文件记录工程与研究实现进度；研究协议以 `framework.md` 为准，结果数值以 `docs/results_report.md` 和版本化 CSV 为准。

## 当前阶段

| 项目 | 状态 | 可核对产物 |
| --- | --- | --- |
| 九阶段统一管线 | 已完成 | `figures/pipeline/latest_run.json` 中 9/9 阶段成功、36 个产物哈希通过 |
| ConvLSTM 独立时间校准 | 已完成 | `figures/convlstm/forecast_calibration_metrics.csv` |
| ConvLSTM 配对日期块 95% 区间 | 已完成 | `figures/convlstm/forecast_bootstrap_ci.csv` |
| ConvLSTM 扩展窗口滚动验证 | 已完成 | `rolling_validation_folds.csv`、`rolling_validation_metrics.csv`、`rolling_validation_predictions.csv` |
| NGBoost 未来 onset 正式调参 | 暂停 | 当前仅 3 个可预测独立事件，不满足稳定调参与外层评价条件 |
| 切线角等速阶段冻结 | 待专家决定 | `figures/tangent_angle/review/`；当前无 `approved` 人工阶段 |

## 当前滚动验证协议

1. 保持现有 ConvLSTM 结构、7 日输入和 1 日预测步长，不更换模型。
2. 使用 3 个扩展窗口折，每折测试 287 个连续日，测试段互不重叠。
3. 每折训练段末 20% 作为独立时间校准期；标准化、增量尺度和测点 `qhat` 只使用该折允许的历史数据。
4. 每折报告总体和逐测点误差、持久性基线、区间覆盖率、宽度、pinball loss 和 interval score，不只报告跨折均值。
5. 当前数据和留出时段已参与多轮分析，滚动结果仅作探索性内部时间验证，不作为外部确认性证据。

## 本轮完成门槛

- 输出逐折计划、逐日预测和逐折/逐测点指标 CSV。
- 测试覆盖时间隔离、测试段不重叠、固定协议和管线产物契约。
- 同步更新 README、设计、研究框架、结果、限制和本进度文档。
- 全量测试、Ruff、编译和完整管线通过；运行清单中的源码及产物哈希可复核。

## 2026-06-21 滚动验证记录

- 三个测试折均为 287 日且互不重叠，输出已通过固定种子逐字节确定性复跑。
- 模型/持久性 RMSE：折 1 为 2.123/0.245 mm，折 2 为 0.492/0.120 mm，折 3 为 0.318/0.340 mm。
- 逐测点 RMSE 优于基线数量：0/8、0/8、8/8；当前 ConvLSTM 不能表述为跨时期稳定优于持久性基线。
- 校准覆盖率：48.8%、94.9%、75.2%；第二折覆盖率上升伴随区间过宽和 interval score 恶化。
- 全量门禁：135 项测试和 32 个子测试通过；Ruff、编译、CSV 完整性及有限数检查通过。
- 九阶段完整管线通过，运行清单源码指纹与当前代码一致，36/36 个产物哈希复核通过。
- 功能提交：`bdf14e5`（`feat: add convlstm rolling validation`）；运行清单及本进度记录随后的维护提交另行保存。
