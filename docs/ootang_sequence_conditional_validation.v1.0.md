# Transformer / CNN-Mamba 条件实验核验 v1.0

2026-09-14，正式训练前核验已通过；训练和结果核验尚待执行，不能据此声称效果达标。

来源 73 项哈希一致；三套教师缓存的日期、给定驱动和 22 维特征完整重建差为 0。没有重新拟合 B+、旧模型或做物理调用。
六项可复现合同检查通过：未来标签毒化/前缀标准化；同种子两臂初始化一致/零输出回退；非零模型因果性和前缀一致性；重载/独立 NumPy 前向/编码器梯度；并行扫描对串行 float64 前向与梯度；官方参考 float32 前向与梯度。

Transformer 4916 参数，CNN-Mamba 7956 参数。真实 792 日特征的独立 NumPy 全模型前向最大差分别 1.28e-13 和 2.27e-13 mm。合成核验和计时只有前向/反向，优化器更新 0，不纳入正式训练。
Mamba 官方参考函数从冻结原件按 AST 提取，函数体保持；只为所需实值三维 B/C 分支提供 D→D×1 的布局辅助，没有加载 CUDA 模块。float32 比较容差 2e-5，float64 梯度容差 1e-10；完整 Mamba 在 NumPy 中以逐日串行状态递推核对。

复现：`PYTHONPATH=code .venv/bin/python -m unittest discover -s tests -p test_sequence_conditional.py -v`。
入口 `code/sequence_conditional/run.py` 分内部/开发/最终执行，正式启动前检查 `implementation_lock.json` 和来源锁。有效完成的拟合可重载，不重复训练；效果门与继续执行解耦。

[训练前回执](../results/ootang_sequence_conditional_v1/20260914/implementation_verification/receipt.json)／[详细合同日志](../results/ootang_sequence_conditional_v1/20260914/implementation_verification/unit_tests.txt)。

准备记录：第一次 lint 提示一条未用 import，已在正式冻结前删除；GitHub API 限流已改用公开 Git commit 定位；上游原件三处尾空格保留，以保持原始字节和来源哈希。未出现训练异常。
