# TiDE分组融合研究图

最终版为v2，四张240×170mm、300dpi PNG与可编辑文字SVG；只用于当前研究。v2仅将分量图的“实线为集成分量”更正为“曲线为集成分量”，所有源数组相同，其余三PNG逐字节相同。v1、其生成代码和核验保留，不改变预测或分数。

| 图件 | 内容 | 文件 |
| --- | --- | --- |
| 跨窗与逐点比较 | 十一方法、三个完整293日窗、全部配对种子与最终覆盖 | [PNG](v2/fusion_comparison.png) · [SVG](v2/fusion_comparison.svg) |
| H分量 | 完整预测路径的最大绝对贡献、原始H/受限H、训练段cap与全部种子 | [PNG](v2/hydro_contributions.png) · [SVG](v2/hydro_contributions.svg) |
| SPLIT最终四点 | 无位移反馈路径、三种子、80/95%区间、完整末段 | [PNG](v2/split_final.png) · [SVG](v2/split_final.svg) |
| BOUND最终四点 | 与SPLIT共用每点坐标、完整路径与区间失败 | [PNG](v2/bound_final.png) · [SVG](v2/bound_final.svg) |

分量上界只约束H附加项，不是总位移、概率区间或物理安全界；两分支联合训练，不能将结果差解释为对同一预测的事后裁剪。全部窗口已暴露且重叠，仍为探索性。历史不补画不存在的神经训练段重建，末段、点和种子不删除。

[研究记录](../../../docs/ootang_tide_fusion_results.v1.0.md) · [图件合同](../../../docs/ootang_tide_fusion_figure_contract.v1.0.md) · [源数组](v2/source_arrays.npz) · [149图形映射](v2/plot_records.json) · [标注映射](v2/annotations.json) · [源数值和SVG/文字核验](../../../results/ootang_tide_fusion_v1/20260915/figure_qa_v2/receipt.json) · [逐图目视](../../../results/ootang_tide_fusion_v1/20260915/visual_review.json)。共16面板、550检查、247192图形与数据值；源数值差0，SVG量化差7.21e−7pt以内，文字/碰撞/1.5pt轴对齐通过。内部核验不替代用户/导师验收。
