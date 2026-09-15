# TiDE分支融合实施核验

入口为`code/tide_fusion/run.py`。原网络/输入/损失只读复用，主支屏蔽H，独立H支仅读三项水文状态、相对距离、known及独立点ID，位移增量层相加。两新版123571参数，同seed初值与原主支110392参数完全一致；BOUND只在起点化H输出上增加训练前缀决定的tanh变换。所有主支和H支参数从头联合训练，无预训练后追加。

训练前预检通过472项、1394125个数值比较，优化更新0。包括训练段cap、四组日期/成熟目标、前缀外位移和驱动隔离、12组原抽样日程、两新版同初始化/初始梯度、原主支初值、e0/h0、非零独立NumPy前向、批量一致、分支输入隔离、零H回退当前主支、大幅H仍有界、非零梯度及重载。612起点旧KIN/PHYS共30检查点预测逐元素精确回放。

配置和1272来源先冻结提交，再实现及预检提交。执行命令如下，工作目录为仓库根目录：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_fusion.run train
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_fusion.run score
```

训练日志、每次更新的deadline检查、开始/结束及发报事件用于执行核验；全部预定拟合不按成绩提前停止。e0/50/100/200/400各保存模型、优化器、均值/增量和主支/原H/实际H三种分量。下一步独立复算240新旧检查点、全部起点输入/目标、cap和分量上界、校准/评分/配对门及图件。实现通过、模型效果和用户/导师验收分开。
