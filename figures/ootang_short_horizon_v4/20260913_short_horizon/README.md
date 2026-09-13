# v4.0 图件与核验说明

本目录为已完成的 1—7 天位移端点对比产物。31 张最终图均保存 PDF、SVG 和 300 dpi PNG；图宽 166 mm，与五页 process_report 风格简报的正文宽度一致。Python/Matplotlib 沿用项目已有绘图后端；没有上传研究数据或使用生成图像。

## 图件内容与完整性

| 图件 | 面板目的 | 解释边界 |
| --- | --- | --- |
| `workflow` | 历史输入、四类模型、七个端点与开发锁定流程 | 只表示算法关系，不是效果证据 |
| `horizon_comparison` | 开发／后期的平均 RMSE 与 CRPS，共四面板 | 家族代表由开发逐 h 最低 RMSE 固定；纵轴明确为对数，所有值均为正；评分对象为发出的三种子等权均值及共同分布，不是种子分数平均 |
| `paired_effects` | ConvLSTM 残差−直接、Ridge 残差−直接、PINN 方程−去方程、反馈物理−核心 | 四个固定问题，纵轴尺度不同并分别标注；负差有利于配对的前者；不事后替换身份 |
| 28 张 `forecast_{point}_h{h}` | 上：累计位移；中：h 日总增量；下：实测−预测及相对均值的 90% 区间 | 上中保留观测、锁定推荐、B+ 和速度外推；下图蓝带为经验高斯边际预测区间，不是联合七日事件风险或参数置信区间 |

28 张图使用开发锁定的共同推荐：h=1 为 RR_DIRECT，h=2—7 为 C16_CORE_RULES。保留每点每步所有合法后期目标，n 从 293 至 287；累计位移重合也保留增量与误差面板。单位均为 mm；总增量不除以 h，不称单日速度。零参考线、颜色／线型和图例在同类面板间保持一致；不同点的坐标范围明确分开，不据此比较视觉高度。

| 步长 | ATU1 | ATU5 | MJ3 | MJ1 |
| --- | --- | --- | --- | --- |
| 1 天 | [图](forecast_atu1_h1.pdf) | [图](forecast_atu5_h1.pdf) | [图](forecast_mj3_h1.pdf) | [图](forecast_mj1_h1.pdf) |
| 2 天 | [图](forecast_atu1_h2.pdf) | [图](forecast_atu5_h2.pdf) | [图](forecast_mj3_h2.pdf) | [图](forecast_mj1_h2.pdf) |
| 3 天 | [图](forecast_atu1_h3.pdf) | [图](forecast_atu5_h3.pdf) | [图](forecast_mj3_h3.pdf) | [图](forecast_mj1_h3.pdf) |
| 4 天 | [图](forecast_atu1_h4.pdf) | [图](forecast_atu5_h4.pdf) | [图](forecast_mj3_h4.pdf) | [图](forecast_mj1_h4.pdf) |
| 5 天 | [图](forecast_atu1_h5.pdf) | [图](forecast_atu5_h5.pdf) | [图](forecast_mj3_h5.pdf) | [图](forecast_mj1_h5.pdf) |
| 6 天 | [图](forecast_atu1_h6.pdf) | [图](forecast_atu5_h6.pdf) | [图](forecast_mj3_h6.pdf) | [图](forecast_mj1_h6.pdf) |
| 7 天 | [图](forecast_atu1_h7.pdf) | [图](forecast_atu5_h7.pdf) | [图](forecast_mj3_h7.pdf) | [图](forecast_mj1_h7.pdf) |

`curve_source_data.csv` 含 8120 行曲线数据，已逐项对照冻结预测数组；`curve_manifest.json` 和 `source_receipt.json` 保存来源与选择。完整指标、三种子离散度、配对块重采样和每七日起点敏感性在 `results/ootang_short_horizon_v4/20260913_short_horizon/analysis/`。主图展示同一已锁定输出的整体成绩；描述性重采样另表保存，不用来改选模型。

## 最终科学图核验

- 31 张最终图的 render-time 对齐检查均 PASS；实际 PDF 字号、碰撞检查均通过，见 `qa/summary.json` 与各图回执。
- 已逐一目视检查四点七步长的 84 个曲线面板、两张四面板比较图及流程图；最终五页简报也逐页重新渲染检查。困难尾段、极值和欠覆盖均保留。没有缺点、裁切、遮盖、错误图例或用局部放大掩盖全窗结果。
- 初版 ATU1 h=2 轴标题与刻度发生轻微碰撞，原图与失败回执保留在 `qa_first_render/`。最终只将长轴标题换行，数值、日期、坐标尺度与容差未改。
- 最终源码静态预检为 17 PASS、4 WARN、0 FAIL，见 `qa/source_preflight.json`。四项 WARN 保留：使用 PNG 预览而无 TIFF；300 dpi 达到报告需要但不是期刊默认 600 dpi；静态工具把 `166/25.4` 英寸表达式误读为 4216.4 mm（实际 PDF 为 166 mm）；对数检查未识别源码已有的全正断言。这里交付的是简报与可编辑矢量图，不宣称已通过特定期刊投稿检查。

## 整页报告的排版与数值核验

整页 TeX 中，同一字体跨度可能覆盖不相邻的表格单元或多行；通用科学图跨度碰撞器会误报重叠。原 `report_collision.json` 的失败状态保留在结果目录的 `verification/report_layout/`，没有改成通过。最终使用实际逐字包围盒核验，五页均无碰撞或裁切；最小可见字号 6.5 pt，五页渲染已逐页目视确认。原字体替换／caption 警告仍保留在编译日志，没有 Overfull/Underfull。

`verification/report_qa.json` 另核对 70 个报告数字及 128 个配对统计／敏感性单元。独立前缀和重采样实现与原表最大差 3.77e-15 mm；原模型、图数据和门槛不变。排版通过不等于 PINN 物理验收通过，也不等于真实失稳预警已验证。

复现绘图只需读取保存产物：`PYTHONPATH=code .venv/bin/python -m short_horizon.figures --config config/ootang_short_horizon_comparison.v4_0.json`。无需重训；初版失败档案不应覆盖。报告编译见 `paper/README.md`。
