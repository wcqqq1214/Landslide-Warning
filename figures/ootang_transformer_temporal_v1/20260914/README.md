# 跨起点 α / λ 完整验证图件

全部五张最终图已完成数据核对和目视检查。跨起点主图说明历史选参稳定性；最终两流程选中α=1、λ=0，所以两组四点轨迹及概率区间相同。新有限λ搜索未建立稳定四点收益，负结果保留。

| 图件 | PNG（300 dpi） | SVG（可编辑文字） |
| --- | --- | --- |
| 历史选α，导师纵轴 | [PNG](v1/ALPHA_SELECTED_mentor.png) | [SVG](v1/ALPHA_SELECTED_mentor.svg) |
| 历史选α，完整区间范围 | [PNG](v1/ALPHA_SELECTED_full.png) | [SVG](v1/ALPHA_SELECTED_full.svg) |
| 历史选λ，导师纵轴 | [PNG](v1/LAMBDA_SELECTED_mentor.png) | [SVG](v1/LAMBDA_SELECTED_mentor.svg) |
| 历史选λ，完整区间范围 | [PNG](v1/LAMBDA_SELECTED_full.png) | [SVG](v1/LAMBDA_SELECTED_full.svg) |
| 跨起点参数/误差/种子/覆盖比较 | [PNG](v1/temporal_validation.png) | [SVG](v1/temporal_validation.svg) |

四点均为ATU1、ATU5、MJ3、MJ1；单位mm。最终图全1461日、前1168日训练、后293日一次发出；给定未来降雨/水位，预测期间无实测位移反馈。80%/95%为历史90条误差RMS形成的逐日边际预测区间，不是种子置信区间。导师固定纵轴版可能裁切区间显示，完整范围版保留全部数值。

历史窗口有重叠，三个种子不等于三个独立数据集。跨起点图保留三种子配对差，RMSE比值使用已核验正值的对数轴；不提供独立重复的置信区间或显著性判断。

[结果报告](../../../docs/ootang_transformer_temporal_results.v1.0.md) · [数据与模型核验](../../../docs/ootang_transformer_temporal_validation.v1.0.md) · [图件数字](v1/figure_numbers.csv) · [源数组](v1/source_arrays.npz) · [实际SVG/PNG核验](v1/delivery_qa.json) · [目视记录](v1/visual_qa.json)。

固定240×170mm、正文最小7.6pt、横纵网格；面板对齐门1.5pt通过。实际SVG曲线/区间88,936值核对，最大坐标量化差约5.15e-6mm；不制作PDF。静态检查器未识别导入的画布/导出函数，并要求本轮排除的PDF；原未通过状态单列，未冒称其通过。`draft_legend_review/`是图例调整前预览，仅用于追溯，交付使用`v1/`。
