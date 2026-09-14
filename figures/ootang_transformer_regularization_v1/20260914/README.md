# Transformer 正则化验证图件

两张中文四点图，已进行模型/数值核验、实际SVG图元核对及目视；无PDF。给定未来逐日降雨/库水位、预测段无实测位移反馈，完整293日为探索性评价。

- [正则版训练与预测 PNG](v1/TRANSFORMER_BRES_REG1.png)／[可编辑 SVG](v1/TRANSFORMER_BRES_REG1.svg)
- [五方法预测比较 PNG](v1/all_methods_forecast.png)／[可编辑 SVG](v1/all_methods_forecast.svg)
- [源数组](v1/source_arrays.npz)／[自动核验](v1/delivery_qa.json)／[后续目视记录](v1/visual_qa.json)
- [完整报告](../../../docs/ootang_transformer_regularization_results.v1.0.md)

240×170mm，300dpi；三种子等权均值，80/95%冻结边际预测区间。所有四点、完整日期及困难尾段保留；未在分界重新对齐观测。最终平均RMSE正则版8.9073、原版9.2791、B+9.1242 mm；MJ3和跨阶段/概率限制见完整报告。
