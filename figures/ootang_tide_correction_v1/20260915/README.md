# 冻结KIN与历史样本外校正研究图

四张最终PNG/SVG，240×170mm，PNG为300dpi、SVG文字可编辑。主比较为原KIN与CAL/HCAL；全部十三方法的完整293日结果保留。四图16面板、101图形记录、257388图形及数据值独立核对通过；数据差0、实际SVG最大差7.204e−7pt，字体/对齐/碰撞与逐图目视通过。只有v1，无科学数据或图稿修订。

- [完整比较PNG](v1/correction_comparison.png) · [SVG](v1/correction_comparison.svg)：全部十三方法、配对种子/集成差、逐点RMSE和覆盖。
- [监督支持PNG](v1/oof_support.png) · [SVG](v1/oof_support.svg)：路径成熟、历史基模型前缀与最长直接监督距离；不视为独立样本数。
- [CAL四点PNG](v1/cal_final.png) · [SVG](v1/cal_final.svg)：冻结KIN加不读H的校正。
- [HCAL四点PNG](v1/hcal_final.png) · [SVG](v1/hcal_final.svg)：同容量校正器读取H。

[图形源数组](v1/source_arrays.npz) · [图形记录](v1/plot_records.json) · [独立核验](../../../results/ootang_tide_correction_v1/20260915/figure_qa_v1/receipt.json) · [目视记录](../../../results/ootang_tide_correction_v1/20260915/visual_review.json) · [图件约定](../../../docs/ootang_tide_correction_figure_contract.v1.0.md)

蓝底仅显示实测历史与B+曲线，橙底为给定完整未来降雨/水位、无位移反馈的条件预测。区间来自之前已发报预测的成熟误差，不用新模型训练残差替换，不声称完整293日同时覆盖。CAL/HCAL全部逐点种子和95%区间均保留，二图共用轴包络。临时同源PDF只用于字形/碰撞检查，不作为PDF交付。
