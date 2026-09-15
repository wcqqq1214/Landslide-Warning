# TiDE直接预测实施核验 v1.0

独立入口为 `code/tide_direct/run.py`，配置及研究计划已先行提交。两臂共同110392参数，180日位移历史与294槽条件协变量编码，输出硬锚定当前位移；DATA在归一化后关闭全部5个物理通道，目标及单位完全不依赖教师。

训练前87项检查、399342数值通过：共同初始化/抽样/标签、成熟索引及尾部特征屏蔽、历史位移隔离、DATA在教师为空或物理标准化扰动时完全不变、h0/零头、批量、梯度有限及有限差分、权重重载和独立NumPy前向。使用真实标签仅至612，更晚前缀边界检查用合成位移。验证无优化器更新，不占用或替代24次正式拟合。条件化全未来驱动可改变早期输出，属于冻结的信息合同，不宣称驱动逐日因果。

原环境及同版隔离SciPy1.15.3均有Mach-O导入异常，保留失败记录；新隔离Python3.12.13/SciPy1.16.3可正常运行，其他核心包维持版本，原环境未覆盖。首轮独立NumPy矩阵乘法虽有限且与PyTorch一致，但发出浮点状态告警，改用非BLAS的显式einsum复核，87项检查再次通过且无告警；初稿核验目录保留。代码格式整理前后AST逐文件一致，ruff检查通过；最终实现哈希锁定。未查看或选择任何新预测成绩。

执行命令（相同版本环境可在其他目录重建）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_direct.run train
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_direct.run score
```

运行环境版本见本轮environment.json，依赖、旧缓存、原对照和计划261项来源保持。正式执行后还需独立复算检查点、指标、误差池和图件；本文件只记录实施核验，不预判模型效果。
