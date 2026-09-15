# TiDE物理特征消融实施

独立入口`code/tide_features/run.py`，原TiDE网络/损失/样本/缩放代码只读复用；新接口仅在标准化后关闭K或H通道。配置对照确认结构、训练、优化器、归一化、校准、保护条件、日期/种子和信息边界与上轮逐项相同。旧两个端点只引用完整检查点与原路径，不重训、不覆盖。

训练前238项/1209578数值检查通过：四组指定通道及关闭组扰动隔离、共同历史/目标/成熟掩码、未来位移和未知驱动隔离、完全相同初始化与全部12个种子/前缀抽样、零头/h0、批量/梯度/独立NumPy前向。612起点旧两组全部30检查点预测逐元素精确重现；无优化器更新，环境无新安装。对不对称人工指标验证全部七条因子公式，防止组别方向写反。正式训练前锁定实现并commit。

执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_features.run train
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_features.run score
```

本文件描述实现核验，不预判效果。训练后需独立复算新旧240检查点、全部训练输入/成熟目标、概率池、分组效应和科学图件；模型整体达标及用户/导师验收分别记录。
