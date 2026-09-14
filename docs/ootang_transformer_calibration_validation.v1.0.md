# 半残差与区间校准验证记录 v1.0

## Material Passport

- 类型：确定性复算与统计解释；Verification Status：VERIFIED。
- 原始输入、规则与执行依据：[冻结计划](/Users/wcqqq1214/Project/Landslide-Warning/docs/ootang_transformer_calibration_plan.v1.0.md)、[配置](/Users/wcqqq1214/Project/Landslide-Warning/config/ootang_transformer_calibration.v1_0.json)、[来源](/Users/wcqqq1214/Project/Landslide-Warning/docs/ootang_transformer_calibration_sources.v1.0.json)。
- 完整开发 376 日、最终 293 日、六均值乘三规则全部执行。实现核验通过；Transformer 的完整效果门未通过；用户/导师验收未声称完成。
- 新训练、更新、B+ 拟合、物理前向均为 0。独立 NumPy 前向属于保存权重核验，不是新增训练。

## 1. 前置核验

[前置回执](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/implementation_verification/receipt.json)及版本化测试覆盖八项：

1. 权重零回退 B+、权重一回退原版，逐种子组合与组合后集成一致。
2. 每个距离精确选择 90 个不同历史索引、确定性并列顺序与边界处理。
3. 未成熟误差和参与选择的标签被拒绝。
4. 将预测段标签毒化为极大值，前缀读取及三种校准输出不变。
5. 开发 DIST90 与 LAST90 精确相同；只对选中样本的求和顺序作统一，不改变取样规则。
6. 归一化单位仅来自两个对应训练前缀；原单位 RMS 不读取历史校准段之外的标签。
7. 零误差尺度下限及无效规则拒绝。
8. 概率数组保存重载逐值一致。

实际三个阶段的日期连续性、完整预测尺寸、三种子均值与 HALF 组合均在新评分前验证。源文件 1,012 项 SHA256 在执行前、复算前后保持；旧参数、检查点、预测、阈值和选择记录不变。

## 2. 保存结果独立复算

[数值回执](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/verification_v1/receipt.json)：

| 核验内容 | 结果 |
| --- | --- |
| e400 检查点 | 原版/REG1 × 三阶段 × 三种子，共 18 份；PyTorch 重载预测最大差 0 |
| 独立网络计算 | NumPy 显式因果注意力、LayerNorm 与输出换算，不调用训练网络的前向 |
| 三个岭回归保存模型 | 权重前向核对；无重新拟合 |
| 半残差 | 使用 `(B+ + 原版)/2` 独立检查生产实现的 `B+ + 0.5×(原版−B+)` |
| 校准 | 独立 Python 距离排序与 `math.fsum` RMS，所有日/点/方法/规则尺度复算 |
| 评分 | 独立 erf/正态分位数公式复算 MAE、RMSE、CRPS、80/90/95% 覆盖/宽度/区间评分 |
| 完整分布 | 36 组；144 行逐点、54 行种子汇总、48,168 行逐日记录 |
| 误差池与距离支持 | 2,007 行距离支持表；每个目标距离的 90 条实际索引全部保存并核对 |
| 数值核对 | 1,171,142 个数值；最大绝对差 2.9558577807620168e-12；原容差不变 |
| 选择与门槛 | 开发选择、全部效果条件、λ 搜索触发与最终原名单评价逐项一致 |
| 事件 | 13 条；发出完整分布早于当前目标标签读取，开发选择早于最终开始 |

对旧五方法，LAST90 的均值与逐日广播 sigma 精确保留；所有概率规则共享同一均值。内部 [612,702) 不进入校准池；开发只读 [702,792) 的历史误差，最终使用 [792,1168)，均严格早于当前预测起点。新模型的单位只读自己的训练前缀，误差取自当时的合法较早模型，不是当前模型的拟合残差。给定未来驱动的条件与无预测段位移反馈并存，不能误称未知驱动实时预测。

## 3. 警告及制图修订

保存岭回归的 NumPy 矩阵乘法出现 `divide by zero`、`overflow`、`invalid value` 同类警告。没有删除或当作底层修复：三阶段分别捕获原文，检查输入/输出全部有限，用显式逐项 `math.fsum` 验证。矩阵结果最大差 1.3322676295501878e-15，换算后与保存预测最大差 2.2737367544323206e-13 mm；见[警告补充核验](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/verification_v1/linear_warning_check.json)。只支持有限计算一致，未确定警告底层成因。

制图交付前补了源数组的完整 1461 日 `full_dates` 元数据；原 `dates` 为 293 日发出日期。数值数组、图像均未改变，旧/新源数组哈希记录在图件 manifest。首版装饰标记检查把 SVG 区间填色路径误判成标记；修订为仅排除明确命名、另经逐顶点核对的区间路径，其余标记检查保持。两项均为元数据/只读检查器修订，未重跑实验或修改概率值。

## 4. 图件与报告交付核验

[图件核验](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/delivery_qa.json)与[目视记录](/Users/wcqqq1214/Project/Landslide-Warning/figures/ootang_transformer_calibration_v1/20260914/v1/visual_qa.json)覆盖 7 张图、28 个面板。实际 SVG 曲线/区间共核对 141,524 个值，最大坐标序列化误差 5.450869934975344e-6 mm；1e-4 mm 仅用于 SVG 六位小数的渲染坐标，模型/评分容差未放宽。参考纵轴与主刻度、横纵网格、无三角形、完整日期、80/95% 逐日区间、RMSE 标注均检查。

全部输出 240×170 mm、PNG 2834×2007 像素/300 dpi、可编辑文字 SVG，最小文字 7.6 pt，无缺字。实际面板对齐容差 1.5 pt 和文字边界检查通过，并检查全部图与四点面板。参考纵轴裁切仅用于显示，全部区间在三个完整范围副本可见，均值/观测无裁切。

通用静态源检查保留原结果 16 PASS、2 WARN、3 FAIL：它不解析导入的画布/导出函数，且要求本轮用户明确排除的 PDF。没有宣称静态检查全通过；实际 SVG 文字/曲线、PNG DPI/尺寸、对齐和碰撞分别核验。PDF 字形/碰撞审计未运行，按用户不制作 PDF 的要求处理。未修改技能。

报告五张表的 192 个数值单元格与保存 CSV 回查；种子表、降幅比值、概率改善、逐点失败和链接另行核对。机器回执保存在[报告核验](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/analysis/report_qa.json)；最终来源与文件追踪见[最终回执](/Users/wcqqq1214/Project/Landslide-Warning/results/ootang_transformer_calibration_v1/20260914/final_receipt.json)。

## 5. 统计解释：11 类误用核查

| 类型 | 本轮处理 |
| --- | --- |
| Simpson 悖论 | 同时报告等权总体与四点，保留 MJ3 和 MJ1 的反向表现 |
| 生态谬误 | 四点平均改善不推为每点、其他剖面或滑坡普遍改善 |
| Berkson 选择偏差 | 六方法、三规则、完整两阶段均保留，不只展示赢家 |
| 碰撞变量 | 未以误差或覆盖结果筛选日期、种子、测点；不作因果推断 |
| 基率忽略 | 位移概率评价不换算预警命中或安全可靠性 |
| 均值回归 | 开发/最终及同种子配对分开；不把某次整体小幅降低视为稳定规律 |
| 幸存者偏差 | 坏效果、覆盖不足、计算警告、制图检查器修订均保存 |
| 多重比较 | 预定 18 个分布全部报告，无显著性或家族错误率主张 |
| 分析路径分叉 | 计划先提交、开发锁定先于最终；不按最终调整校准、λ 或删除尾段 |
| 相关即因果 | HALF 降幅比值不是“解释方差/因果贡献率”；校准收益不归因于 Transformer 架构 |
| 反向因果 | 区间不读取当前最终误差；实际核验通过不反推模型有效或导师认可 |

90 条日误差具有序列相关性，只来自很少的历史模型/窗口，不当作 90 次独立重复。三种子体现训练随机性，不增加独立数据集数。不提供未经支持的显著性、等效检验或覆盖保证；最终段与本次设计均属探索性。

## 6. 复算入口

运行时从仓库根目录，使用 `.venv` 与 `PYTHONPATH=code`。正式入口只运行已冻结的两个阶段；已存在目录会拒绝覆盖，效果差不会自动终止后期。原始顺序为 plan/config/source commit → preflight → implementation commit → development → final_exploratory → audit → figures/QA → report/QA。

重新核验已有结果可使用一个尚不存在的审计输出名称，例如：

```sh
PYTHONPATH=code .venv/bin/python -m transformer_calibration.audit --attempt independent_replay
```

该命令重载保存模型并写入独立核验目录，不训练，不改旧发出数组、评分或选择。初次审计后仅补了输出目录参数与 CLI，数值实现保持；不要删除旧实验目录来“重跑”。新参数搜索、更多时间起点训练和 RL 均未在本轮自动启动。
