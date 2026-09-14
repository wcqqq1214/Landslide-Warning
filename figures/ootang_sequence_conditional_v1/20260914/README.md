# 四点条件预测图件

五张图均为同一实验的完整日期与三种子均值，PNG 300dpi，SVG 文字可编辑；无PDF。

| 图件 | PNG | SVG |
|---|---|---|
| TRANSFORMER_DIRECT_COND | [PNG](v1/TRANSFORMER_DIRECT_COND.png) | [SVG](v1/TRANSFORMER_DIRECT_COND.svg) |
| TRANSFORMER_BRES_COND | [PNG](v1/TRANSFORMER_BRES_COND.png) | [SVG](v1/TRANSFORMER_BRES_COND.svg) |
| CNN_MAMBA_DIRECT_COND | [PNG](v1/CNN_MAMBA_DIRECT_COND.png) | [SVG](v1/CNN_MAMBA_DIRECT_COND.svg) |
| CNN_MAMBA_BRES_COND | [PNG](v1/CNN_MAMBA_BRES_COND.png) | [SVG](v1/CNN_MAMBA_BRES_COND.svg) |
| all_methods_forecast | [PNG](v1/all_methods_forecast.png) | [SVG](v1/all_methods_forecast.svg) |

各模型图为前1168日拟合与后293日条件预测；给定每日雨/水位，无预测段实测位移反馈。区间为此前90条成熟误差校准的80/95%边际预测区间，不是种子均值置信区间。对比图只叠加均值；完整概率评分另见CSV。

[图源数组](v1/source_arrays.npz)／[数值注记](v1/figure_numbers.csv)／[20面板自动核验](v1/delivery_qa.json)／[目视记录](v1/visual_review.json)。

静态预检的PDF要求按用户范围不适用；布局门已直接接线，SVG实际尺寸240×170mm通过。PNG300dpi/无TIFF符合本次导师查看交付，未宣称期刊投稿合规。
