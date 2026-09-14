# Transformer 半残差与区间校准：完整对比报告 v1.0

**本轮已经完整跑通。半残差复现了大部分均值改善；按预测距离校准在最终段明显改善概率评分，但 Transformer 仍未建立跨阶段、四点一致的 B+ 优势。按冻结规则，不继续 λ 搜索，也未引入 RL。**

## Material Passport

- 类型：代码实验与结果解释；状态：EXECUTED / VERIFIED。核验通过与效果达标分别判断，未声称用户或导师验收。
- 数据与任务：同一藕塘 ATU1、ATU5、MJ3、MJ1；1461 日原序列；前 1168 日拟合，后 293 日完整独立条件预测。
- 信息条件：给定未来逐日降雨、库水位，不接收预测段实测位移；完整 B+ 状态延续。并非未知未来驱动的实时预报。
- 本次复用已保存的三种子模型/预测，新增训练、更新、物理拟合、物理前向均为 0。不是又训练了一版 Transformer。
- 计划 [冻结计划](/Users/wcqqq1214/Project/Landslide-Warning/docs/ootang_transformer_calibration_plan.v1.0.md)；[配置](/Users/wcqqq1214/Project/Landslide-Warning/config/ootang_transformer_calibration.v1_0.json)；[1,012 项来源](/Users/wcqqq1214/Project/Landslide-Warning/docs/ootang_transformer_calibration_sources.v1.0.json)。
- 独立窗口 2026-09-14 15:41:07—17:41:07 UTC；实际交付用时见最终回执，不延续旧额度。本地分步提交，不 push、不制作 PDF。

## 1. 先验证：正则化是否主要相当于减弱残差

原版为 `B+ + 网络修正`；HALF 固定为 `B+ + 0.5 × 原版网络修正`，不重新训练、不搜索系数。REG1 是上一轮加入 λ=1 修正惩罚后重新训练的模型。三个种子逐一配对，主结果取等权均值。

下面均为四点等权平均，单位 mm；开发为 376 日，最终为完整 293 日。最终段多次暴露，只作探索性比较。

<!-- table:mean_comparison -->
| 方法 | 开发 MAE | 开发 RMSE | 最终 MAE | 最终 RMSE |
| --- | --- | --- | --- | --- |
| B+ | 33.265 | 41.368 | 6.902 | 9.124 |
| DRIFT1 | 5.425 | 6.541 | 9.110 | 12.978 |
| 普通岭回归 | 38.182 | 45.900 | 11.318 | 12.338 |
| Transformer 原版 | 39.837 | 48.187 | 6.884 | 9.279 |
| Transformer REG1 | 36.147 | 44.414 | 6.539 | 8.907 |
| Transformer 半残差 | 36.529 | 44.620 | 6.489 | 8.867 |
<!-- endtable:mean_comparison -->

开发段，HALF 已达到 REG1 相对原版 MAE 降幅的 **89.66%**、RMSE 降幅的 **94.52%**。这是已观察到的降幅比值，不是因果贡献率。REG1 比 HALF 的开发 RMSE 只再降低 0.46%。

最终段，HALF 的 MAE/RMSE 均略低于 REG1；二者整体两项差异均在事前 1% 描述性范围内。不能据此宣称统计等效，但它削弱了“复杂正则化训练带来额外稳定外推能力”的解释。轨迹并非完全相同，[逐点轨迹差](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/analysis/correction_difference.csv)保留了这种差别。

<!-- table:seed_pairing -->
| 阶段 | 比较（MAE 与 RMSE 同时降低） | 种子数量 | 种子编号 |
| --- | --- | --- | --- |
| 开发 | HALF 优于原版 | 3/3 | 0, 1, 2 |
| 开发 | REG1 优于原版 | 3/3 | 0, 1, 2 |
| 开发 | REG1 优于 HALF | 3/3 | 0, 1, 2 |
| 最终（探索） | HALF 优于原版 | 3/3 | 0, 1, 2 |
| 最终（探索） | REG1 优于原版 | 2/3 | 0, 2 |
| 最终（探索） | REG1 优于 HALF | 1/3 | 0 |
<!-- endtable:seed_pairing -->

REG1 对 HALF 的配对优势从开发 3/3 变为最终 1/3。HALF 相对原版两阶段均为 3/3 改善，但 HALF 与 REG1 的开发均值均落后 B+。最终整体略优于 B+ 不能抵消开发失败与逐点退步。

与普通岭回归比较，REG1/HALF 在开发和最终的平均 MAE/RMSE 均更低；相同校准规则下，开发平均 CRPS/区间评分仍更高，最终两项则更低。与 DRIFT1 比较，两版均值在开发更差、最终更好，不能概括为跨阶段优于简单对照。

DRIFT1 是简单的**最后一天增量直线外推**：`y(t+h)=y(t)+h×[y(t)-y(t-1)]`。本协议从固定起点发出全部未来预测，不使用期间新位移，不是神经网络。普通岭回归则沿用相同合法固定特征。

## 2. 再验证：保持均值，改变历史误差的取法

- LAST90：原来的末尾 90 条成熟误差 RMS，每点常数区间尺度。
- DIST90：按当前预测距离寻找上一独立窗内最接近的 90 条误差，每点、每距离冻结尺度；同距取较短距离。
- DIST90_UNIT：同样匹配距离，再按历史/当前模型训练前缀的位移标准差单位换算。没有使用最终标签调比例。

内部前 90 日曾用于选训练次数，已排除。开发校准只剩内部后 90 日（原预测距离 91—180），所以 DIST90 **精确退化为 LAST90**；它在开发段没有新增距离辨识证据。最终校准可使用全部已兑现的 376 日开发预测误差。它们来自自己的较早训练前缀模型，不是最终模型的训练拟合残差。

下面保留全部 18 组，不只显示成功的组合。CRPS、区间评分和宽度单位为 mm；分数越低越好，覆盖率结合宽度一起判断。80%/95% 的完整数值也在 CSV 中。

### 开发段：376 日

<!-- table:probability_development -->
| 均值方法 | 校准 | CRPS | 90% 区间评分 | 90% 覆盖 / % | 90% 宽度 |
| --- | --- | --- | --- | --- | --- |
| B+ | LAST90 | 31.035 | 460.600 | 49.80 | 96.273 |
| B+ | DIST90 | 31.035 | 460.600 | 49.80 | 96.273 |
| B+ | DIST90_UNIT | 30.994 | 450.332 | 50.86 | 108.414 |
| DRIFT1 | LAST90 | 6.451 | 75.879 | 99.73 | 75.866 |
| DRIFT1 | DIST90 | 6.451 | 75.879 | 99.73 | 75.866 |
| DRIFT1 | DIST90_UNIT | 7.276 | 89.460 | 100.00 | 89.460 |
| 普通岭回归 | LAST90 | 32.693 | 430.653 | 50.27 | 117.517 |
| 普通岭回归 | DIST90 | 32.693 | 430.653 | 50.27 | 117.517 |
| 普通岭回归 | DIST90_UNIT | 32.511 | 404.503 | 52.93 | 132.430 |
| Transformer 原版 | LAST90 | 35.162 | 523.597 | 48.47 | 99.027 |
| Transformer 原版 | DIST90 | 35.162 | 523.597 | 48.47 | 99.027 |
| Transformer 原版 | DIST90_UNIT | 35.044 | 509.888 | 51.66 | 111.378 |
| Transformer REG1 | LAST90 | 32.755 | 487.008 | 51.93 | 97.123 |
| Transformer REG1 | DIST90 | 32.755 | 487.008 | 51.93 | 97.123 |
| Transformer REG1 | DIST90_UNIT | 32.685 | 476.095 | 53.26 | 109.267 |
| Transformer 半残差 | LAST90 | 33.095 | 494.131 | 49.80 | 97.251 |
| Transformer 半残差 | DIST90 | 33.095 | 494.131 | 49.80 | 97.251 |
| Transformer 半残差 | DIST90_UNIT | 33.023 | 482.562 | 51.20 | 109.425 |
<!-- endtable:probability_development -->

训练单位换算未带来合格改善。例如 REG1 的 90% 覆盖仍只有 53.26%；不能因为区间变宽、覆盖略升就认定校准有效。

### 最终段：293 日，探索性评价

<!-- table:probability_final_exploratory -->
| 均值方法 | 校准 | CRPS | 90% 区间评分 | 90% 覆盖 / % | 90% 宽度 |
| --- | --- | --- | --- | --- | --- |
| B+ | LAST90 | 16.154 | 220.078 | 100.00 | 220.078 |
| B+ | DIST90 | 7.560 | 90.745 | 92.75 | 85.003 |
| B+ | DIST90_UNIT | 8.039 | 98.147 | 92.92 | 92.620 |
| DRIFT1 | LAST90 | 7.278 | 78.314 | 68.09 | 23.714 |
| DRIFT1 | DIST90 | 7.568 | 98.532 | 62.97 | 17.628 |
| DRIFT1 | DIST90_UNIT | 7.549 | 96.162 | 63.82 | 19.263 |
| 普通岭回归 | LAST90 | 18.573 | 249.321 | 100.00 | 249.321 |
| 普通岭回归 | DIST90 | 9.153 | 95.658 | 100.00 | 95.658 |
| 普通岭回归 | DIST90_UNIT | 9.525 | 102.614 | 100.00 | 102.614 |
| Transformer 原版 | LAST90 | 19.177 | 262.830 | 100.00 | 262.830 |
| Transformer 原版 | DIST90 | 8.391 | 100.459 | 100.00 | 100.459 |
| Transformer 原版 | DIST90_UNIT | 8.928 | 108.805 | 100.00 | 108.805 |
| Transformer REG1 | LAST90 | 17.657 | 241.655 | 100.00 | 241.655 |
| Transformer REG1 | DIST90 | 7.780 | 91.115 | 98.12 | 90.734 |
| Transformer REG1 | DIST90_UNIT | 8.289 | 98.852 | 98.38 | 98.638 |
| Transformer 半残差 | LAST90 | 17.619 | 241.328 | 100.00 | 241.328 |
| Transformer 半残差 | DIST90 | 7.818 | 92.404 | 99.49 | 92.389 |
| Transformer 半残差 | DIST90_UNIT | 8.330 | 100.349 | 100.00 | 100.349 |
<!-- endtable:probability_final_exploratory -->

固定 REG1 均值后，DIST90 将 CRPS 从 17.657105 降至 7.780426，降幅 55.94%；90% 区间评分降幅 62.30%，平均宽度从 241.655 降至 90.734 mm，覆盖仍为 98.12%。REG1/HALF 的最终新校准相对自身 LAST90 通过概率工作条件，均值完全未变。

**同样的校准也改善 B+。** 采用 DIST90 后，B+ 的 CRPS 为 7.560232，仍低于 REG1 的 7.780426 和 HALF 的 7.817727；区间评分同样更低。只能说这里观察到校准规则的收益，不能把它归为 Transformer 特有优势。

## 3. 四点保护与选择结果

以下覆盖使用同一个 DIST90 规则，RMSE 与校准无关：

<!-- table:point_boundary -->
| 测点 | B+ RMSE | REG1 RMSE | HALF RMSE | B+ 覆盖 / % | REG1 覆盖 / % | HALF 覆盖 / % |
| --- | --- | --- | --- | --- | --- | --- |
| ATU1 | 8.068 | 7.576 | 7.598 | 100.00 | 100.00 | 100.00 |
| ATU5 | 9.115 | 6.874 | 6.999 | 100.00 | 100.00 | 100.00 |
| MJ3 | 13.016 | 15.273 | 14.697 | 70.99 | 92.49 | 97.95 |
| MJ1 | 6.297 | 5.906 | 6.174 | 100.00 | 100.00 | 100.00 |
<!-- endtable:point_boundary -->

REG1 的 MJ3 RMSE 仍高于 B+；HALF 的 MJ3 同样退步，且 MJ1 MAE 为 5.590 mm，高于 B+ 的 5.288 mm，不能只看 RMSE。另一方面，B+ DIST90 虽然平均概率分数更好，MJ3 的 90% 覆盖只有 70.99%，未过逐点 80% 保护。它也不能直接被宣布为已经可靠的新概率基线。

开发锁定均值优胜者为 **DRIFT1**，概率优胜者为 **DRIFT1__LAST90**。最终没有按成绩改名单。所有 Transformer 组合在两阶段对同规则 B+ 的完整联合条件均未通过；相对原 LAST90 B+ 的最终概率局部改善保留，完整联合条件仍未过。

λ 搜索触发条件在开发阶段已失败：REG1 相对 HALF 的 RMSE 降幅不足 1%、MJ1 退步、且未通过 B+ 均值门。不是看见最终结果后决定停。[开发锁定及逐项条件](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/selection.json)保存了全部真假值。

## 4. 给导师看的图

纵轴参照导师图，保留横纵网格、去掉三角标记；黑线实测、灰虚线 B+、蓝线候选。图中 80%/95% 是逐日边际预测区间，不是三种子均值的置信区间，也不保证整条轨迹同时覆盖。参考纵轴以外的区间仅裁切显示，另给未裁切完整范围图；没有缩窄原始区间。

[REG1 原区间](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/REG1_LAST90_mentor.png) · [REG1 距离匹配区间](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/REG1_DIST90_mentor.png) · [HALF 距离匹配区间](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/HALF_DIST90_mentor.png) · [六方法完整最终曲线](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/all_methods_forecast.png)。

![REG1 距离匹配区间](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/REG1_DIST90_mentor.png)

[全部七张图及可编辑 SVG](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/README.md)包含三个完整范围副本。四点、完整日期、三种子与负结果均保留。

## 5. 现在的问题与下一步判断

据本轮证据，主要未解决的是**残差在不同时间段的外推稳定性，以及历史误差分布能否迁移到下一次预测**。本轮没有证据表明需要更大的网络或更复杂的参数搜索器。

本轮结束，不追加 λ 搜索或 RL。若继续研究，先形成下一版跨时间起点验证方案：每个起点均用此前数据完成同一训练流程，分别留出模型选择和概率校准窗口，预先规定长距离支持不足的处理，并让 B+ 与神经模型使用相同校准规则。重点是取得可比较的独立历史预测证据，而不是继续利用已暴露的 293 日调参；本报告仅提出方向，没有自动启动这些拟合。

现有两个独立历史窗口不足以提供大量独立重复；90 条相邻日误差不等于 90 次独立实验。没有宣称高斯假设、交换性、可靠覆盖、实际预警有效性或物理因果机制已获验证。原 B+ 参数拟合非收敛标志与数据来源限制保持，不重写旧结论。

## 6. 核验与交付

1012 项来源，8 项前置检查；18 个 e400 模型重载及独立 NumPy 前向；36 个分布、144 行逐点指标、54 行种子汇总、48,168 行逐日记录核对。共检查 1,171,142 个数值，最大差 2.96e-12。图件 7 张、28 面板，全部 SVG 曲线/区间、坐标与 PNG 已核验并目视。

岭回归的 NumPy 矩阵乘法出现历史同类警告；补充显式逐项求和证明输出有限且与保存预测一致，警告原文保留，底层成因未宣称修复。制图静态检查器未解析导入的导出函数，且要求本轮明确不制作的 PDF；保留其原始未通过状态，实际 SVG/PNG 几何与数值核验单独报告。

[详细核验](/Users/wcqqq1214/Project/Landslide-Warning/docs/ootang_transformer_calibration_validation.v1.0.md) · [完整 36 组 CSV](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/analysis/phase_summary.csv) · [四点指标](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/analysis/metrics_by_point.csv) · [种子结果](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/analysis/seed_summary.csv) · [最终回执](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/final_receipt.json)。
