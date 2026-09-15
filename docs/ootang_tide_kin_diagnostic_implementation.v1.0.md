# 原KIN距离诊断：实现与预检

## Material Passport

新增独立入口`code/tide_kin_diagnostic`，原模型/训练/概率代码及权重不改。用户授权回到KIN诊断，本轮零优化更新、零新增拟合、零B+拟合/物理前向。预测输入只读当前位移前缀；训练重放同时掩码未成熟目标及协变量，可见性扰动只清除给定日期之后的标准化协变量/known，不改距离编码或位移历史。

283项来源核对通过，预检141项/978945数值，最大差1.687538997430238e−14。覆盖手算监督权重、真实抽样日程、系数总和、原始数据独立构造、未来标签/驱动隔离、四个e400模型重放、NumPy前向、h0/e0、四种可见范围精确掩码及冻结梯度。612原本无概率尺度，显式保留缺失；准备期清单错误和依赖提交失败发生在计划冻结前，记录于preparation_notes，未改变源数据。

重放60份原检查点的所有合法成熟训练样本，归一化目标贡献保持每起点先平均的原定义；无监督单元MAE/RMSE为空、目标贡献0。各种子和集成分开评分，原区间尺度只读复用，不产生新误差池。可见性扰动只比较共同可见距离上的预测变化，不做效果排名。

依次执行固定诊断与独立复算，所有旧原始预测仍保留，启动窗不冒充新主评价窗。诊断完成后交付研究文字/CSV/PNG/SVG，不据结果自动追加训练。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_kin_diagnostic.run
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code /tmp/ootang-tide-env-20260915/bin/python -m tide_kin_diagnostic.audit
```

[计划](ootang_tide_kin_diagnostic_plan.v1.0.md) · [配置](../config/ootang_tide_kin_diagnostic.v1_0.json) · [预检回执](../results/ootang_tide_kin_diagnostic_v1/20260915/preflight/receipt.json)
