# TCN 条件预测：导师展示图件

当前交付为 **v2**。两臂均从头训练，使用给定未来降雨/库水位，完整 293 日无实测位移反馈。残差版拟合较好、均值优于直接版，但仍未超过连续原 B+；完整负结果见[图文报告](../../../docs/ootang_tcn_conditional_training_results.v1.0.md)。

| 图件 | PNG | 可编辑文字 SVG |
| --- | --- | --- |
| B+ 残差版：四点完整训练拟合与预测 | [PNG](v2/TCN_BRES_COND.png) | [SVG](v2/TCN_BRES_COND.svg) |
| 直接版：四点完整训练拟合与预测 | [PNG](v2/TCN_DIRECT_COND.png) | [SVG](v2/TCN_DIRECT_COND.svg) |
| 五方法：完整 293 日均值对比 | [PNG](v2/all_methods_forecast.png) | [SVG](v2/all_methods_forecast.svg) |

240×170 mm，PNG 300 dpi，原始大小 2834×2007 像素。位移只统一减去原始第一日值；没有在分界处重新贴合实测。预测色带为固定历史误差尺度的 80%/95% 边际区间，不是训练拟合带或整条轨迹同时置信带。

完整源数组、图中 RMSE 表、1.5 pt 对齐、文字/字体与实际 SVG 顶点核对均在 v2。12 个面板已目视；[交付核验](v2/delivery_qa.json)、[分面板核验表](v2/panel_qa.csv)、[图件合同](figure_contract.md)。不制作 PDF，也不声称 PDF 审计通过。

v1 作为图件打包错误的原件保留：三图已绘制，但附带源数组重复尺度键导致打包失败。修复后 v2 完整交付，未修改或重跑训练/评分。
