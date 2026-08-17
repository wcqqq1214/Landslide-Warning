# Landslide-Warning

藕塘水库滑坡日尺度案例的位移概率预测和多测点预警原型。当前仓库只保留可执行的当前技术路线；已退役的 30 日 `V0` 标签、旧融合与旧运行入口仅在 Git 历史中保留。

## 当前结论边界

- 默认链为 `features → convlstm → ootang-operational-v4`，均为藕塘内部的**非正式原型**。所有 v4 输出均标记 `formal_warning_output=false`、`vajont_used=false`。
- ConvLSTM 独立输出全部 8 个测点的 P10/P50/P90 位移预测，以及训练、校准和测试时段的图表与覆盖率诊断。
- 独立 NGBoost 回归 + SHAP 用于识别候选模型依赖；它不是 ConvLSTM 的 SHAP，也不构成因果主控因素或正式预警分类器。
- 显式阶段 `ootang-ngboost-interval-proxy-pilot` 使用四项指标预测下一日五级区间风险代理状态；它不替换 ConvLSTM 或 v4，也未使用其他案例。当前 calibration/test 全时刻表现均略低于状态持续基线，故暂不引入主流程。
- 显式敏感性阶段以完全相同的 NGBoost、输入和训练协议并列运行 h=1/3/7；三个提前量的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过各自持续基线，且概率质量随提前量增加而减弱。本结果不排名或选择 horizon。
- v4 按导师确认的逐点方法计算速度和加速度：
  `v_i=(U_i-U_{i-1})/(t_i-t_{i-1})`，
  `a_i=(v_i-v_{i-1})/(t_i-t_{i-1})`。
  加速度以每测点 fit-only 的 `A0=max(1.5A,A+2σ_a)` 为基准，沿用课题组确认的 `1×/5×/10×` 五级相对结构。
- 当前测点候选融合使用区间、速度/改进切线角（同一运动学证据族）和加速度三族；原始 `ΔV` 仅保留审计，不重复投票。空间层输出“整体确认”和“局部最高候选”两条轴。
- 数据源是发布的物化日序列，原始 GNSS 及完整生成血缘不可得。因此 `prototype_run_gate=allowed`，`confirmatory_evidence_gate=blocked`；不得把本案例表述为已验证的现场正式预警。

截至当前 v4 结果含 4,112 条测点—时刻记录、514 条滑坡体结果和 8 行阈值。加速度单项 green/blue/yellow/orange/red 为 `4012/98/2/0/0`；滑坡体整体确认 green/blue/yellow/orange/red 为 `8/48/31/9/18`，另有 400 日因空间确认条件未满足而不发布整体颜色。

## 快速运行

项目使用 `uv`，Python 3.10：

```bash
uv sync
uv run python main.py
```

无参数只运行 `features → convlstm → ootang-operational-v4`。其余当前诊断需显式选择：

```bash
uv run python main.py --list
uv run python main.py --stage ngboost-shap
uv run python main.py --stage ootang-ngboost-interval-proxy-pilot
uv run python main.py --stage ootang-ngboost-interval-proxy-horizon-sensitivity
uv run python main.py --stage convlstm-rolling --stage convlstm-seeds
```

每个阶段声明输入输出，管线在运行前后检查文件新鲜度，并将提交、输入输出 SHA-256、状态与耗时写入 `figures/pipeline/latest_run.json`。该文件当前不存在：原有清单是 2026-08-01 的 v3 阶段残留记录，已于 2026-08-15 删除，下次完整运行会重新生成。解释任何运行清单时须核对其自身提交和源码指纹。

Vajont 尚未启动；读取、适配或运行其数据前必须获得用户明确许可。

## 验证与历史边界

当前测试只保护工作树中仍可执行的接口：参考阶段加载/训练期门禁、人工阶段优先级，以及 v4 的非正式、fail-closed 证据与协议契约。可用以下命令复核：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
uv run ruff check code tests main.py
.venv/bin/python -m compileall -q code main.py tests
```

旧 30 日 `V0`、v1/v2/v3 运行入口和对应历史测试已从当前工作树移除；需要复现历史快照时，必须按提交从 Git 历史恢复，不应把旧产物当作当前 v4 接口或结果。

## 代码结构

```text
main.py                         # 当前管线入口（10 个可选阶段）
code/features/                  # 特征、逐点运动学、切线角
code/convlstm/                  # 概率位移预测与时间验证诊断
code/explainability/            # 独立 NGBoost 回归与 SHAP
code/warning/                   # v4 四指标、多测点非正式运行与协议门禁
data/                           # 发布物化序列、坐标和派生特征
figures/                        # 版本化预测、规则审计和图件
docs/                           # 当前方法、结果边界和研究计划
```

`code/warning/operational_spatial_fusion.py` 是 v4 当前调用的共享双轴空间融合实现；旧 v3 运行入口及其专属实现只保留在 Git 历史。

## 主要文档和结果

| 文件 | 内容 |
| --- | --- |
| [`docs/design.md`](docs/design.md) | 当前代码架构、输入输出和非正式边界 |
| [`docs/ootang_operational_run.md`](docs/ootang_operational_run.md) | v4 四指标、加速度阈值和多测点双轴规则 |
| [`docs/ootang_stage_results_package.md`](docs/ootang_stage_results_package.md) | 藕塘阶段性结论与可写/不可写边界 |
| [`docs/advisor_review_action_plan.md`](docs/advisor_review_action_plan.md) | 导师意见逐项状态与下一步门禁 |
| [`figures/convlstm/forecast_all_stations.png`](figures/convlstm/forecast_all_stations.png) | 全测点概率位移预测及训练/结果分段 |
| [`figures/shap/ngboost_regression_shap.png`](figures/shap/ngboost_regression_shap.png) | 独立 NGBoost 回归的候选模型依赖 SHAP 图 |
| [`docs/ootang_ngboost_interval_proxy_pilot.md`](docs/ootang_ngboost_interval_proxy_pilot.md) | NGBoost 下一日五级区间代理试验、基线比较和不引入主流程的当前判断 |
| [`docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md`](docs/ootang_ngboost_interval_proxy_horizon_sensitivity.md) | 固定 NGBoost 的 h=1/3/7 非排名提前量敏感性和概率质量诊断 |
| [`figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg`](figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg) | 514 个结果时刻的测点候选与滑坡体双轴状态 |
| [`docs/progress.md`](docs/progress.md) | 当前实现进度、已清理历史代码与未完成门禁 |

## 尚未完成的关键事项

1. 当前 NGBoost 区间代理 pilot 及 h=1/3/7 敏感性已完成，但所有提前量的全时刻 accuracy、macro-F1 和 ordinal MAE 均未超过状态持续基线，不能据此引入主流程或选择 horizon；正式模型仍需独立、可核验的五级结局标签。
2. 取得可追溯的原始观测或独立验证资料，并冻结正式稳定段/V0、切线角容差、融合和评价协议。
3. 只有获得用户授权后，才启动 Vajont 的数据适配与外部案例评估。
