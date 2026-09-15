# 原 TiDE_KIN 距离与跨时段诊断图

四图各四面板，全部来自原检查点和既有发报的零训练诊断。PNG为300dpi研究预览，SVG文字可编辑；无PDF交付。所有窗均探索性，612仅启动诊断。

| 图 | 文件 | 读图边界 |
| --- | --- | --- |
| 各距离训练权重 | [PNG](v1/supervision_weights.png) · [SVG](v1/supervision_weights.svg) | 系数不是梯度；全部原三种子显示。 |
| 四点分距离误差 | [PNG](v1/error_by_distance.png) · [SVG](v1/error_by_distance.svg) | 三主窗、原发报集成的逐点RMSE；全部六方法/种子另存CSV。 |
| 原检查点拟合与发报 | [PNG](v1/checkpoint_fit.png) · [SVG](v1/checkpoint_fit.svg) | 训练与发报实线均为种子平均，集成独列；日期集合不同，不重选检查点。 |
| 未来输入可见性 | [PNG](v1/visibility_sensitivity.png) · [SVG](v1/visibility_sensitivity.svg) | 是相对完整输入的预测变化，不是准确性；不同d使用不同共同日期集合。 |

[研究记录](../../../docs/ootang_tide_kin_diagnostic_results.v1.0.md) · [图形契约](../../../docs/ootang_tide_kin_diagnostic_figure_contract.v1.0.md) · [数值与图形核验](../../../docs/ootang_tide_kin_diagnostic_validation.v1.0.md) · [目视记录](visual_review.json)

152条科学曲线、21088个源/图形值核对通过；实际SVG最大差4.998e-7pt。四图字体最小7.2pt，对齐1.5pt门通过，碰撞FAIL/WARN均0。源代码静态扫描4项WARN已逐项在目视记录说明。全部PNG逐图查看，用户/导师尚未验收。
