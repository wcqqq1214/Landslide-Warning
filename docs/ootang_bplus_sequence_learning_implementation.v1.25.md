# 藕塘连续预测有限学习 v1.25：实现与核验入口

## Material Passport

- 日期：2026-09-11；Origin Skill/Mode：academic-research-suite / experiment-agent / run。
- 依据：[已登记方案](ootang_bplus_sequence_learning_plan.v1.25.md)，方案 commit `ddb2c35`。
- 实现及相关测试 commit `8c748f6`。实现完成不代表预测效果改善；实验结果另行记录。

## 实现范围

新增 `code/physics_guided_sequence_learning/`，复用已验证的 v1.24 卷积递推和
v1.21 梯度平衡/Adam，不修改旧版本源码、配置、权重或结果。四组为 IN_CARRY、
IN_RESET、OOF_CARRY、OOF_RESET；每组使用三个前缀和三个种子，共 36 个训练实例。

原查询按教师与起点打包，保留全部日期、标签和权重，仅在原查询位置监督。
RESET 只清零编码器到预测端的状态，预测日之间仍连续递推。两组参数量、初值、
优化器和训练次数匹配。未改变遗忘门、输入通道或教师参数。

每个前缀的输入、权重、训练日志和完整均值先锁定，再读取下一前缀观测。
全部均值及八组常尺度锁定后评分。B+ 和原 NORM 参考直接复用，不新增物理拟合。
固定 3,600 次更新，CPU 单线程、float64、1,800 秒硬超时，不自动重试。

## 已完成的实现验证

19 项相关测试通过，耗时 1.619 秒；Ruff 检查和格式检查通过。测试覆盖打包与
逐条查询的输出/完整梯度、教师分组、原监督权重、填充无损失、未来观测隔离、
完整历史反向、RESET 边界及独立 NumPy 递推、初值/B+ 等值、单步平衡 Adam、
预算拒绝、独立概率评分、日志缺步/权重篡改及逐点四项改善门限。

执行前来源保护核对 2,878 个文件；原三前缀查询表及已登记方案指纹一致。
真实输入、180 个 checkpoint 身份及 36 个最终输出/梯度在登记的主运行及核验中
进一步检查，不能把上述合成测试当作真实结果已复现。

## 运行和结果位置

命令（在仓库根目录运行；既有 run-id 不可覆盖）：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code .venv/bin/python -m physics_guided_sequence_learning.run --run-id 20260911_sequence_learning
```

产物位置为 `results/ootang_bplus_v1_25/20260911_sequence_learning/`。主进程先做
一次有限数值核验；仅在成功封存后，按方案执行一次 `--verify` 只读回放。
两次核验各自记录求值/梯度预算，没有独立重训全部优化器轨迹。

四点拟合/预测均值与概率指标均按原标准报告。原始日值当时可用性仍为 unknown，
未来雨量/库水位为已知条件，既有历史窗口已暴露；本实现不改变这些研究边界，
也不推定用户或导师接受。
