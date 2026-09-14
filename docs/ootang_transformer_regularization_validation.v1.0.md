# Transformer 残差正则化核验 v1.0

## Material Passport

状态：训练前实现核验通过；尚未运行本轮正式训练，尚无效果结论。依据冻结计划及配置，所有核验本地执行。

## 训练前核验

- 779项冻结来源哈希一致；三套合法前缀教师缓存的完整日期、给定驱动、22维输入及前缀输入尺度核对通过。
- 5项聚焦合同检查通过：λ=0数据损失/梯度精确相同；λ=1平方完成恒等式与解析梯度；未来未释放标签/未来特征扰动对训练输入、目标及尺度无影响；非零网络因果性、零输出回退B+、保存重载及独立NumPy注意力一致；非法/非有限目标拒绝。
- 原版内部三个种子的e0与新构造的4916参数模型完全一致；每阶段实际训练前还会重新核对相应原版初始化。
- 三个既有非零内部e400检查点完整792日前向：NumPy与PyTorch最大差2.27e-13 mm，低于冻结1e-7 mm容差。
- 预核验仅解析前612日训练标签；不进行优化器更新、正式拟合或物理前向。
- ruff检查通过；训练核心和合同测试已冻结在implementation_lock中。后续核验/绘图入口可新增，不能改动已冻结训练核心来适应成绩。

凭据：[预核验回执](../results/ootang_transformer_regularization_v1/20260914/implementation_verification/receipt.json)、[合同输出](../results/ootang_transformer_regularization_v1/20260914/implementation_verification/unit_tests.txt)、[实现锁](../results/ootang_transformer_regularization_v1/20260914/implementation_lock.json)。

## 后续核验范围

正式训练后逐个重载45检查点、独立NumPy全前向，复算尺度、均值/概率指标、校准误差池、发出顺序、开发锁定、正则配对、交叉诊断及图件。当前通过只说明训练前实现合同通过，不代表预测改善或导师验收。
