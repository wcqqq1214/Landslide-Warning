# 夜间固定小图与残差尺度图件

[六页PDF](../../../output/pdf/ootang_overnight_graph_v1/v2/ootang_spatial_pilot_figures.pdf)：前四页为主要结果，后两页补充全部区间范围。240×170mm矢量图；PNG为300dpi，SVG保留文字。

| PDF页 | 内容 | PNG | SVG |
| --- | --- | --- | --- |
| 1 | 跨时段误差、配对图边增量、最终逐点均值与概率评分 | [查看](v2/comparison.png) | [矢量](v2/comparison.svg) |
| 2 | GRU固定图四点，导师坐标 | [查看](v2/GRU_GRAPH_mentor.png) | [矢量](v2/GRU_GRAPH_mentor.svg) |
| 3 | GRU本点四点，同坐标 | [查看](v2/GRU_LOCAL_mentor.png) | [矢量](v2/GRU_LOCAL_mentor.svg) |
| 4 | 四点因果残差尺度诊断 | [查看](v2/residual_diagnostic.png) | [矢量](v2/residual_diagnostic.svg) |
| 5 | GRU固定图，全部95%区间范围 | [查看](v2/GRU_GRAPH_full.png) | [矢量](v2/GRU_GRAPH_full.svg) |
| 6 | GRU本点，全部95%区间范围 | [查看](v2/GRU_LOCAL_full.png) | [矢量](v2/GRU_LOCAL_full.svg) |

四点均值和区间源于保存预测，蓝色历史区不含虚构的神经拟合曲线。图版/本点版的固定轴95%越界日分别为0/0/248/1和0/0/255/1，所有均值/实测在轴内；后两页使用两臂共同的完整逐点纵轴。评分没有裁剪。

六页24面板已逐一核验并目视；70346实际SVG图形值与源数组/表核对，最大导出量化差4.71e-6mm以内。PDF字体全部嵌入，最小7.2pt，碰撞检查0失败/0警告，1.5pt对齐通过。初版v1因诊断页注释碰撞中止，三页预览原样保留，仅v2为最终交付。

[源数组](v2/source_arrays.npz) · [图元记录](v2/plot_records.json) · [逐点数值及裁剪](v2/figure_numbers.csv) · [交付QA](v2/delivery_qa.json) · [逐面板QA](v2/panel_audit.csv) · [来源清单](v2/manifest.json)
