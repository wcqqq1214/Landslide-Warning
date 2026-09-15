# GRU两因素消融图件

最终交付为`v2`：五张PNG/SVG、20面板，240×170mm、300dpi，可编辑SVG文字。`v1`保留排版初稿，不作为最终展示版本。

| 图 | PNG | SVG |
| --- | --- | --- |
| 全部窗口与两因素效应 | [总览](v2/factorial_overview.png) | [矢量](v2/factorial_overview.svg) |
| G00 原样GRU | [四点曲线](v2/g00_raw_uniform_four_points.png) | [矢量](v2/g00_raw_uniform_four_points.svg) |
| G10 起点残差表达 | [四点曲线](v2/g10_anchor_uniform_four_points.png) | [矢量](v2/g10_anchor_uniform_four_points.svg) |
| G01 更新边界采样 | [四点曲线](v2/g01_raw_boundary_four_points.png) | [矢量](v2/g01_raw_boundary_four_points.svg) |
| G11 两项合用 | [四点曲线](v2/g11_anchor_boundary_four_points.png) | [矢量](v2/g11_anchor_boundary_four_points.svg) |

总览四面板分别回答完整窗均值、首日跳偏、概率评分、配对因子效应。连线/大符号为三种子平均预测后的评分，浅色小符号保留全部种子；种子差异不是独立自然重复的置信区间。RMSE主效应/交互均按固定四组差值定义，负值表示误差降低。

四点图保留完整1461日时间轴：蓝底为历史观测与B+拟合，橙底为前1168日训练后一次发出的293日条件预测，给定未来逐日降雨/库水位、无位移反馈。没有补画未计算的神经训练段拟合。各点仅减去初始位移，未平滑、删日期或裁尾；同点四组共用纵轴，包含全部三种子和完整95%边际区间。

80/95%区间来自各自上一条已发路径第91—180日90个成熟误差的逐点RMS，整窗固定；覆盖不代表整条路径同时覆盖概率。全部窗口已暴露、部分重叠，仅作探索性比较。

[源数组](v2/source_arrays.npz) · [实际图形坐标](v2/plot_records.json) · [数值/字体/碰撞核验](../../../results/ootang_gru_ablation_v1/20260915/figure_qa_v4/receipt.json) · [逐图目视记录](v2/visual_review.json) · [图件锁](v2/artifact_lock.json) · [绘图源码](../../../code/gru_ablation/figures.py)。

核验：406161个数据/图形/尺寸值、808项检查，源数据差0，SVG坐标量化差≤7.21e−7pt；20面板全部1.5pt对齐、文字≥7.4pt、自动碰撞失败/警告均0，全部最终PNG已目视。源检查器的3项提示已审阅：本轮按约定交付300dpi PNG/SVG而非TIFF；240mm面向导师展示，脚本解析器把`240/25.4`误读为6096mm，实际SVG宽度已核对为240mm。

第一轮图形核验未展开SVG的隐式闭合顶点，修订核验器后继续；随后自动渲染检查发现总览局部图例与网格线相交。v2移除该面板网格，并将四点指标放到绘图区上方；没有更改数据或区间。所有失败记录保留于本轮results。PDF仅作为临时同源字体/碰撞检查载体，不作为本轮图件交付或提交。
