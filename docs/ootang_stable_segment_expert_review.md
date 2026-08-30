# 藕塘初始稳定段审查结论

> 状态：`ANALYZED`；范围仅为藕塘 8 个测点的 fit 期稳定段与 `V0` 前置条件。本文记录阻断结论，不产生正式预警阈值，也未使用 Vajont。

## 冻结结论

8 个测点的审查状态统一为：

```text
kmeans_candidate_acceptance = rejected_for_formal_v0
formal_mvif_stable_segment_status = no_stable_baseline_identified
formal_v = NA
formal_sigma = NA
formal_v0 = NA
```

- 原始逐点速度 KMeans 给出的 8/8 个低速前缀候选均被拒绝作为正式初始稳定段。
- 严格 MVIF 多起点拟合在 8/8 个测点均为 `failed / tf_multistart_unstable`，无法产生可辨识的趋势初始斜率。
- 因而正式 `V`、`sigma`、`V0=max(1.5V, V+2sigma)` 均保持 `NA`。
- `no_stable_baseline_identified` 只表示当前数据与数值门禁未识别出稳定基线，不表示地质上不存在稳定阶段。

## KMeans 候选为何不能正式采用

当前 KMeans 仅在 fit 期原始逐点速度上区分高低两类，并截取起始低速前缀。它不是导师指定论文中的“MVIF 趋势初始稳定斜率”方法，也没有独立验证累计位移线性、段内速度趋势、段末持续升速、监测方向或仪器修正。

复核显示，8/8 个候选的最大速度位于段末且次日继续增大；候选段累计位移线性拟合不足，多个候选还包含明显阶段性。该结果足以拒绝其正式 `V0` 身份，但不能把发布日序列中的规则变化直接解释成真实地质加速。

KMeans 数值只允许作为 `comparator-only / operational_draft_not_formal` 的透明规则基线输入。它不得写成论文复现的 MVIF `V0`，不得据此宣称现场阈值有效。

## 严格 MVIF 为何停止

每个测点的 6 个多起点优化均出现多个近似最优解，但这些解对应的有限失稳时间 `t_f` 差异很大。8/8 个测点因此未通过预先固定的可辨识性门禁，后续初始斜率与正式 `V0` 计算停止。

该失败是数值可辨识性结论，而不是失稳时间估计，也不能通过从多解中人工挑选一条“看起来合理”的曲线来消除。

## 自动化与回退边界

- 禁止人工挑选稳定段、人工指定 `V0`，或为了得到可用阈值而在评价结果后放宽门禁。
- 禁止把 KMeans、原始速度均值或其他趋势方法静默回退成论文指定的 MVIF 结果。
- 如以后更换自动稳定段方法，必须基于 fit-only 数据预先登记版本、接受条件和失败处置；旧结果继续保留为 comparator。
- 当前 NGBoost 主预警模型不以人工稳定段标签训练；本结论主要约束 v4 透明规则基线的速度与改进切线角口径。

## 权威证据

| 证据 | 作用 |
| --- | --- |
| [`stable_segment_candidates.csv`](../figures/warning_draft_v4/stable_segment_candidates.csv) | 8 个 KMeans comparator 候选及其 `V/sigma/V0` 数值 |
| [`stable_segment_candidates_manifest.json`](../figures/warning_draft_v4/stable_segment_candidates_manifest.json) | 候选方法角色、输入与协议指纹 |
| [`mvif_fit_candidates.csv`](../figures/warning_draft_v4/mvif_fit_candidates.csv) | 8 个严格 MVIF 可辨识性诊断及失败原因 |
| [`mvif_fit_candidates_manifest.json`](../figures/warning_draft_v4/mvif_fit_candidates_manifest.json) | MVIF 诊断输入、协议与输出指纹 |
| [`ootang_operational_thresholds.csv`](../figures/warning_operational_draft_v4/ootang_operational_thresholds.csv) | v4 comparator 阈值的可审计落地，不代表正式阈值 |
| [`ootang_operational_run_manifest.json`](../figures/warning_operational_draft_v4/ootang_operational_run_manifest.json) | v4 非正式运行边界与产物指纹 |

所有上述产物均保持 `formal_warning_output=false`。若需查看本文件压缩前的逐点长表、统计谬误扫描或历史说明，可从基线提交 `6f499cd` 恢复：

```bash
git show 6f499cd:docs/ootang_stable_segment_expert_review.md
```
