# 藕塘 v4 四指标实施版

## 目的与边界

v4 是藕塘案例的可复算、非正式多测点规则原型。入口为：

```bash
uv run python code/warning/operational_run_v4.py
```

或通过默认总管线：

```bash
uv run python main.py
```

它只接受 `config/ootang_operational_run.v4.draft.json`，并写入 `figures/warning_operational_draft_v4/`。所有核心 CSV、图件 manifest 和时间线固定标记：

```text
formal_warning_output=false
vajont_used=false
artifact_status=operational_draft_not_formal
```

旧 v1/v2/v3 可执行入口已从当前树移除，保留在 Git 历史；历史快照不能被误作 v4 输出。

## 输入与可复算门禁

v4 使用：

- `data/ootang_kinematics_long.csv`：逐点速度、`ΔV`、加速度及状态；
- `figures/convlstm/forecast_predictions.csv` 和其 manifest：全部测点 P10/P50/P90；
- `config/ootang_warning_protocol.v1.draft.json`：基础协议；
- `config/ootang_warning_protocol.v2.draft.json`：加速度扩展；
- `config/ootang_operational_run.v4.draft.json`：可替换的项目实施约定。

运行先验证配置的内容指纹、预测输入与空间拓扑来源；再由 `draft_evidence.py` 在临时目录重建证据 bundle。核心 manifest 记录输入、输出、基础协议、加速度扩展和实施源码的 SHA-256。若本地拓扑论文副本不存在，可以使用已审查摘要运行；若本地副本存在但摘要不匹配，则 fail-closed。

## 四指标

| 指标 | v4 计算与状态 | 五级规则 |
| --- | --- | --- |
| 区间 | 已发布 P10/P50/P90 与随后可见的 `U_t` 形成观测后偏离状态 | 参考指定 Word 图 5-1 的 `μ`、`μ+σ`、`μ+2σ`、`μ+3σ` 区域；不是前瞻现场预警 |
| 速度 | `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})` | 当前运行的 `V0` 是项目比较器；指定 Word 的正式稳定段/V0 尚未在藕塘确认 |
| 加速度 | `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`，采用真实当前 `Δt_i`；首两行加速度 warmup | 导师确认沿用 Word 速度结构。每测点 fit-only `A0=max(1.5A,A+2σ_a)`，green `<A0-σ_a`、blue `A0±σ_a`、yellow `…5A0`、orange `…10A0`、red `≥10A0` |
| 改进切线角 | 由速度相对运行基线转换的角度 | 当前运行的容差和基线是项目操作化，不是已确认的现场阈值 |

`ΔV=v_i-v_{i-1}` 保留为原始过程审计字段。它不是加速度的替代品，也不在 v4 中额外投票。

## 测点和滑坡体融合

测点层有三个证据族：

1. `interval`；
2. `kinematic_velocity_tangent`，即速度和切线角取较高等级且只算一次；
3. `acceleration`。

当前候选取三族最大等级。任一四指标输入在该时刻不是 `valid` 时，测点仍保留明确的 warmup/invalid/not-applicable 状态，绝不补作 green。

空间层按 O1、O2、O3 三个预先记录的空间块输出两条轴：

- `site_confirmed_level/color`：满足全局覆盖和至少两点、两块空间支撑的整体候选；
- `local_max_candidate_level/color`：当天可评估测点中的最高局部候选。

从而，局部 yellow/orange/red 未跨区确认时会显示在 local 轴，而整体轴保持无色；这不是传感器缺失，也不是 green。整体 green 同样只是当前规则候选，不是现场安全判定。

## 已物化结果

当前 bundle 含 8 个测点 × 514 个结果日，共 4,112 条测点记录、514 条滑坡体记录和 8 行阈值。加速度单项 green/blue/yellow/orange/red 为 `4012/98/2/0/0`；整体确认色为 `8/48/31/9/18`，另有 400 日不发布整体颜色。

代表日、完整时间线和全测点联合诊断图分别为：

- `figures/warning_operational_draft_v4/ootang_v4_typical_days.svg`；
- `figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg`；
- `figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg`。

这些是观测后规则审计。它们显示“若在该日使用当前规则会产生什么候选状态”，不证明提前预警性能。

## 不应作出的结论

- 不能将该输出写为正式预警、现场安全色或独立 GNSS 验证；
- 不能将 independent NGBoost+SHAP 的模型依赖当作 ConvLSTM 的解释或物理因果主控；
- 不能以同一四指标规则生成的五级标签训练 NGBoost 后，把高拟合度称作正式模型验证；
- 不能读取、适配或运行 Vajont，除非用户另行明确授权。
