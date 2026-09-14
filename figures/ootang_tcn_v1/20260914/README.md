# TCN完整结果图件

最终版本为 `v3/`，共16张，每张同时保存PNG（300dpi）和文字可编辑SVG。`v1/`、`v2/`是版面/审计修订前记录，不作最终交付入口。

[七步长总览](v3/overview.png)／[逐点残差输出差值](v3/residual_pair.png)

|步长|开发四点完整曲线|后期探索四点完整曲线|
|---|---|---|
|1天|[PNG](v3/development_h1.png) / [SVG](v3/development_h1.svg)|[PNG](v3/later_exploratory_h1.png) / [SVG](v3/later_exploratory_h1.svg)|
|2天|[PNG](v3/development_h2.png) / [SVG](v3/development_h2.svg)|[PNG](v3/later_exploratory_h2.png) / [SVG](v3/later_exploratory_h2.svg)|
|3天|[PNG](v3/development_h3.png) / [SVG](v3/development_h3.svg)|[PNG](v3/later_exploratory_h3.png) / [SVG](v3/later_exploratory_h3.svg)|
|4天|[PNG](v3/development_h4.png) / [SVG](v3/development_h4.svg)|[PNG](v3/later_exploratory_h4.png) / [SVG](v3/later_exploratory_h4.svg)|
|5天|[PNG](v3/development_h5.png) / [SVG](v3/development_h5.svg)|[PNG](v3/later_exploratory_h5.png) / [SVG](v3/later_exploratory_h5.svg)|
|6天|[PNG](v3/development_h6.png) / [SVG](v3/development_h6.svg)|[PNG](v3/later_exploratory_h6.png) / [SVG](v3/later_exploratory_h6.svg)|
|7天|[PNG](v3/development_h7.png) / [SVG](v3/development_h7.svg)|[PNG](v3/later_exploratory_h7.png) / [SVG](v3/later_exploratory_h7.svg)|

曲线为固定h的每日新起点预测，横轴为目标日期，四点顺序ATU1/ATU5/MJ3/MJ1。全部合法日期保留；开发每点n=376…370，后期n=293…287。蓝/橙阴影为两版TCN的90%边际经验高斯预测区间，非种子误差棒；B+、DRIFT1、RR_DIRECT的完整概率区间在明细CSV中。短步长曲线重叠是保存预测本身接近，不是删去某个模型。
总览为三种子等权集成的实际四点平均误差，纵轴明确使用对数；不是先算各种子误差再平均。配对图为BRES−DIRECT，负值表示残差输出更好，正值表示更差；逐点异质性及后期区间评分退步保留。
来源：[完整曲线CSV](../../../results/ootang_tcn_v1/20260914/analysis/forecasts_long.csv)；[汇总CSV](../../../results/ootang_tcn_v1/20260914/analysis/summary_by_horizon.csv)。
QA：16份最终alignment JSON为PASS，1.5pt容差无豁免；最终Matplotlib文字框不重叠、不出画布，配置字号至少6.5pt，SVG可编辑文字和实际PNG尺寸/DPI另核对。已逐图检查64个面板，见[目视回执](v3/visual_review.json)，该回执完成生成时记录中的待目视步骤；细微均值差需要结合评分总览阅读。
通用源码审计仍报告缺PDF导出/字体设置：用户明确要求不制作PDF，故这两项不适用，不声称PDF审计通过。PNG替代TIFF、300dpi替代600dpi符合本轮PNG/SVG交付合同；对数轴有代码逐组有限正值断言。原v1/v2误报的刻度碰撞来自未绘制的范围外Text对象，v3按当前Matplotlib Axis绘制规则排除；零数据变动。
