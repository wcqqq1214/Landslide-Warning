# 训练充分性核验图件

最终版本为 **v3**，四张中文图共16面板，PNG300dpi及可编辑SVG。完整数据、实际SVG坐标、字体/碰撞、1.5pt面板对齐及逐图目视均已完成；没有交付PDF。

| 图件 | PNG | SVG |
| --- | --- | --- |
| 原检查点的训练与成熟历史预测诊断 | [查看](v3/checkpoint_diagnostic.png) | [矢量图](v3/checkpoint_diagnostic.svg) |
| 固定200对400次预算比较 | [查看](v3/budget_comparison.png) | [矢量图](v3/budget_comparison.svg) |
| GRU最终四点完整曲线 | [查看](v3/gru_final400.png) | [矢量图](v3/gru_final400.svg) |
| Transformer最终四点完整曲线 | [查看](v3/transformer_final400.png) | [矢量图](v3/transformer_final400.svg) |

训练面板曲线显示全部固定前缀的既定汇总，原始逐点/种子CSV完整保存。历史与最终预测均完整293日；四点曲线展示原200次和新400次均值，阴影只代表400次版本的固定80/95%边际区间。训练段不补画不存在的神经重构结果。两模型对应测点使用共同完整纵轴范围。

v1预算图轴范围问题与v2预测区注释碰撞的初稿、失败QA留档，不作为最终交付入口。v3仅修显示与排版，没有重新训练或更改评分。

[完整结果](../../../docs/ootang_training_sufficiency_results.v1.0.md) · [作图约定](../../../docs/ootang_training_sufficiency_figure_contract.v1.0.md) · [数据及SVG核验](../../../results/ootang_training_sufficiency_v1/20260915/figure_qa_v3/receipt.json) · [目视记录](v3/visual_review.json)
