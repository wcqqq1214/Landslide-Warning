# 历史教师更新配对图件

最终图件为 `v1`：三张中文四面板图，PNG 300 dpi 与可编辑 SVG，全部来自本轮已锁定预测和评分。无 PDF 报告。

| 图件 | 内容与下载 |
| --- | --- |
| 三窗比较 | [PNG](v1/teacher_policy_comparison.png) · [SVG](v1/teacher_policy_comparison.svg)：六方法完整293日RMSE、同种子配对、最终逐点误差与90%覆盖 |
| 旧教师GRU四点 | [PNG](v1/cached_final.png) · [SVG](v1/cached_final.svg)：完整观测、B+、两组预测均值及本组三种子和80%/95%区间 |
| 更新教师GRU四点 | [PNG](v1/refresh_final.png) · [SVG](v1/refresh_final.svg)：与旧教师图相同的坐标范围，保留全部尾段和区间 |

四点图采用前1168日、后293日分区。蓝底为观测历史与B+拟合，橙底为给定未来逐日降雨/水位、无预测段位移反馈的条件预测；没有补画不存在的神经训练段重建。细线是三个种子的预测，不是置信区间。色带为已兑现历史误差校准的高斯边际区间，不保证293日整条轨迹同时覆盖。

更新组在三个窗口的集成MAE/RMSE均高于旧教师组；三种子配对方向并非全部一致。最终更新组ATU1覆盖不足，旧教师组ATU5覆盖不足，不能以平均覆盖较高替代逐点概率目标。

[图件合同](../../../docs/ootang_teacher_refresh_figure_contract.v1.0.md) · [图形来源数组](v1/source_arrays.npz) · [逐图目视记录](v1/visual_review.json) · [349项/222772数值核验](../../../results/ootang_teacher_refresh_v1/20260915/figure_qa_v1/receipt.json)。字体、遮挡、1.5pt对齐和实际SVG顶点核验通过；所有观测、预测、种子及95%区间均在坐标范围内。两组历史教师政策的物理拟合限制见[数值核验](../../../docs/ootang_teacher_refresh_validation.v1.0.md)。
