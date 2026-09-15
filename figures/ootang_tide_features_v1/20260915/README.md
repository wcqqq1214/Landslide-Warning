# TiDE物理特征分组研究图

四张PNG（300dpi）和可编辑文字SVG，均为完整数据研究记录。K为B+位移/日增量，H为B+水文状态；共同驱动始终保留。全部结果探索性，无PDF汇报。

- [三窗与逐点比较 PNG](v1/feature_group_comparison.png) · [SVG](v1/feature_group_comparison.svg)：九方法、全部配对种子、最终均值误差及覆盖。
- [四点条件因素差 PNG](v1/feature_factor_effects.png) · [SVG](v1/feature_factor_effects.svg)：加入K/H分别依赖另一组是否存在，负差表示降低RMSE。
- [TiDE K四点路径 PNG](v1/kin_final.png) · [SVG](v1/kin_final.svg)：最终完整293日、全部种子及80/95%边际区间。
- [TiDE H四点路径 PNG](v1/hyd_final.png) · [SVG](v1/hyd_final.svg)：相同纵轴，保留宽区间和末段偏差。

[源数组](v1/source_arrays.npz)、[科学图形记录](v1/plot_records.json)、[数值/渲染核验](../../../results/ootang_tide_features_v1/20260915/figure_qa_v1/receipt.json)、[逐面板目视](../../../results/ootang_tide_features_v1/20260915/visual_review.json)、[研究结论](../../../docs/ootang_tide_features_results.v1.0.md)。

四图16面板、237192图形及数据值核对，源数值差0；SVG坐标误差小于1e−6pt，字体、碰撞与1.5pt面板对齐通过。历史只展示真实观测与B+，不补画神经训练重建；路径图共享范围，未平滑/裁剪。三窗已暴露且重叠，区间不保证293日同时覆盖，内部核验不代表导师验收。
