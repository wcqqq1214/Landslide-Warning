# Transformer / CNN-Mamba 条件实验核验 v1.0

**最终追加核验：本轮全部36次拟合/14400次更新、完整376日开发及293日最终评价、五张图和报告已核验通过。四个新方法两阶段的完整效果条件均未通过，用户/导师验收未提供。**

## 完整保存结果复算

- 73项冻结来源和实现锁保持；既有TCN/三个对照同协议数据原样复用，没有旧模型拟合或物理调用。
- 全部180份检查点重载前向与保存预测逐值相同。另用NumPy显式因果注意力、独立逐日串行Mamba-1状态递推核对，不调用训练模型前向；岭回归另以SVD代数求解核对，不做新优化。
- 核对2220800个数值，最大差1.47224e-11；训练归一化、全部种子/集成、原单位均值、成熟90条误差尺度、逐日80/90/95%区间、全部逐点和平均评价、开发名单/效果门均通过。
- 三阶段均先锁均值再读取对应评价标签；开发和最终还先锁概率分布。开发均值/概率名单DRIFT1在最终阶段开始前锁定，最终不改名单。内部两结构分别选择400次，没有追加检查点或优化次数。
- 36条完整拟合记录共14400次更新均为有限损失/梯度，正式训练错误0；两结构最终均值残差配对各3/3种子改善，但开发仅Transformer0/3、CNN-Mamba1/3，概率配对两阶段均0/3。

NumPy在独立岭回归SVD后的矩阵乘法仍输出历史环境的divide/overflow/invalid警告，原日志已保存；计算结果有限，且与原求解及保存数组一致，最大差如上。没有将警告写成底层库已修复，也没有重复训练。

## 图件与报告

五张240×170mm、300dpi PNG和可编辑SVG的20个面板均核验、目视。核对77224个实际SVG曲线/区间值，最大坐标量化差5.52062e-06 mm，低于仅适用SVG六位小数坐标的1e-4 mm容差；不放宽模型/评分容差。所有日期、点、巨大区间和困难尾段保留。
实际1.5pt面板对齐、字体缺字/字号、文字边界、注记与数据线碰撞及PNG尺寸检查通过。目视结果单独保存，不当作导师验收。
静态源预检原件及接线修订后记录保留：修订后16通过、3警告、2个仅针对PDF的失败。用户明确不制作PDF，因此两项不适用；PNG300dpi、无TIFF、导师查看尺寸240mm是本轮约定。静态器把240/25.4英寸表达式误识别为6096mm，实际SVG/PNG尺寸已直接核对。没有声称静态预检全部通过或期刊投稿合规。
报告的34行表格、160个显示数值和3190个派生CSV单元格复查通过；最终回执链接在收尾创建后再次完整核对。

[独立模型/评分回执](../results/ootang_sequence_conditional_v1/20260914/verification_v1/receipt.json)／[SVG与PNG核验](../figures/ootang_sequence_conditional_v1/20260914/v1/delivery_qa.json)／[五图目视](../figures/ootang_sequence_conditional_v1/20260914/v1/visual_review.json)／[报告核验](../results/ootang_sequence_conditional_v1/20260914/analysis/delivery_checks.json)。所有复核为只读计算，无新拟合/更新。

## 下方为训练前形成时记录

2026-09-14，正式训练前核验已通过；训练和结果核验尚待执行，不能据此声称效果达标。

来源 73 项哈希一致；三套教师缓存的日期、给定驱动和 22 维特征完整重建差为 0。没有重新拟合 B+、旧模型或做物理调用。
六项可复现合同检查通过：未来标签毒化/前缀标准化；同种子两臂初始化一致/零输出回退；非零模型因果性和前缀一致性；重载/独立 NumPy 前向/编码器梯度；并行扫描对串行 float64 前向与梯度；官方参考 float32 前向与梯度。

Transformer 4916 参数，CNN-Mamba 7956 参数。真实 792 日特征的独立 NumPy 全模型前向最大差分别 1.28e-13 和 2.27e-13 mm。合成核验和计时只有前向/反向，优化器更新 0，不纳入正式训练。
Mamba 官方参考函数从冻结原件按 AST 提取，函数体保持；只为所需实值三维 B/C 分支提供 D→D×1 的布局辅助，没有加载 CUDA 模块。float32 比较容差 2e-5，float64 梯度容差 1e-10；完整 Mamba 在 NumPy 中以逐日串行状态递推核对。

复现：`PYTHONPATH=code .venv/bin/python -m unittest discover -s tests -p test_sequence_conditional.py -v`。
入口 `code/sequence_conditional/run.py` 分内部/开发/最终执行，正式启动前检查 `implementation_lock.json` 和来源锁。有效完成的拟合可重载，不重复训练；效果门与继续执行解耦。

[训练前回执](../results/ootang_sequence_conditional_v1/20260914/implementation_verification/receipt.json)／[详细合同日志](../results/ootang_sequence_conditional_v1/20260914/implementation_verification/unit_tests.txt)。

准备记录：第一次 lint 提示一条未用 import，已在正式冻结前删除；GitHub API 限流已改用公开 Git commit 定位；上游原件三处尾空格保留，以保持原始字节和来源哈希。未出现训练异常。
