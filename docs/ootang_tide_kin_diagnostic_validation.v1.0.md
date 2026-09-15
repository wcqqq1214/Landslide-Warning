# 原 TiDE_KIN 距离诊断：核验 v1.0

本轮只重放既有权重，不训练、选检查点或改原预测。正式诊断与独立审计均完整通过；科学解释与图件核验另列于研究结果及最终回执，执行通过不代表模型改善。

## 数值与信息边界

- 来源清单283文件；全部60份原KIN检查点、1816个起点—前缀组合、27240次训练起点评价及48条可见性条件种子路径完成。
- 预检141项、978945个数值，最大差1.687538997430238e-14；核对手算损失系数、时间对齐、未来标签/驱动隔离、完整输入恒等和冻结权重。
- 独立审计1789项、45766892个数值，最大差1.1641532182693481e-9，在预设绝对容差内。使用独立输入/成熟标签构建及NumPy网络前向，重算全部训练指标、原发报评分、MSE恒等式及可见性结果。
- 612启动路径原本没有概率尺度，保持不可用；审计6300次不可用单元比较，不将它们当独立样本数。没有用未来误差补建概率池。
- 新拟合、优化更新、B+拟合、物理前向均0；原发报数组精确保持，未产生替代发报，检查点不重选。

## 复现入口与失败记录

在项目根目录使用独立运行环境 `/tmp/ootang-tide-env-20260915/bin/python`，设 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code`。入口为 `tide_kin_diagnostic.preflight`、`tide_kin_diagnostic.run`、`tide_kin_diagnostic.audit`；重跑须指定新的输出尝试目录，不能覆盖已锁定结果。原环境不修改。

准备时来源枚举曾错误要求612存在sigmas文件，冻结前已修正为明确缺失，原错误及失败的依赖提交留在 `preparation_notes.json`。正式诊断和独立数值审计失败重试均0。

可追溯产物： [配置](../config/ootang_tide_kin_diagnostic.v1_0.json)、[计划](ootang_tide_kin_diagnostic_plan.v1.0.md)、[来源](ootang_tide_kin_diagnostic_sources.v1.0.json)、[独立数值回执](../results/ootang_tide_kin_diagnostic_v1/20260915/audit_v1/receipt.json)。数值审计不检验物理因果或新增方法有效性；旧协议、数据和探索性局限保持。
