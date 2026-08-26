# 藕塘 E1 prequential 区间校准 bakeoff

> 运行日期：2026-08-26<br>
> 协议：`ootang-prequential-calibration-bakeoff-v1`<br>
> 证据层级：固定 E1 点预测上的回顾性 prequential 比较<br>
> 产物状态：`retrospective_prequential_calibration_bakeoff_not_confirmatory`<br>
> 选择 / 晋升 / 正式预警：`false / false / false`

## Material Passport

| 字段 | 记录 |
| --- | --- |
| mode | `run + validate` |
| source | 已保护的 E1 `station_timeline/site_timeline/metrics/manifest` |
| source point forecasts | 6,888 行，三种方法逐 binary64 精确复用 |
| candidates | `aci_v1_control`、`agaci_ewa_variant_v1`、`spci_qrf_v1` |
| target | 中心 80% 位移预测区间 |
| status | `ANALYZED`；回顾性描述，不是模型选择或 E2 证据 |
| reproducibility | 两次完整生成的四个产物 SHA-256 逐字节一致 |
| protected boundary | E1 四产物、v5 门禁和 97 条保护路径均不改写 |

## 1. 研究问题与不能越过的边界

E1 的固定 ACI 控制在三个 fold 的覆盖率为 `0.791/0.695/0.631`。本轮只回答一个
工程研究问题：在**完全相同的 E1 point forecast、fold、自动 drift reset 和
station-date 支持**下，时间自适应的区间校准器能否改善 80% 区间的覆盖—宽度权衡。

这不是盲态确认实验。E1 结果在设计本轮前已经被查看，因此本轮禁止输出
`winner`、排名、选择或自动晋升字段，也不允许回写 E1 v1。区间 score、宽度或
覆盖率中的任一单项都不能单独触发晋升；只有新 E2 未来流中的预声明 shadow
协议才有资格产生下一层证据。

## 2. 固定方法及论文关系

### 2.1 `aci_v1_control`

控制方法逐字段重放既有 E1 ACI：每站保存最近 180 个绝对残差，至少 60 个历史
残差后使用 finite-sample higher-rank quantile 生成对称区间；reveal 后才按
`alpha <- clip(alpha + gamma * (0.2 - miss), 0.01, 0.5)` 更新，其中
`gamma=1/180`。

它以 Gibbs 与 Candès 的 Adaptive Conformal Inference 为理论来源，但项目实现有
alpha 截断、180 日窗口、fold/drift 重置和 warmup abstain。因此原论文针对其算法
与假设的长期覆盖结论不能直接转移到本项目变体。

### 2.2 `agaci_ewa_variant_v1`

该方法并列运行七个固定 ACI gamma 专家：

```text
0, gamma0/4, gamma0/2, gamma0, 2*gamma0, 4*gamma0, 8*gamma0
```

上下端点分别依据**过去已经 reveal 的** 0.1/0.9 pinball loss 做指数权重聚合；
同日 loss 只更新下一日权重。学习率固定为
`sqrt(8 log(K) / (active_update_index + 1))`，每侧当日专家 loss 用该侧最大值归一化。

Zaffran 等人的 AgACI 提供了“并行 ACI 学习率专家 + 在线端点聚合”的研究方向，
但论文正式算法使用 BOA 及 gradient trick。本项目当前实现是明确命名的 EWA
工程变体，**不是**论文 BOA+gradient-trick AgACI 的精确复现，也不继承其 regret
结论。

### 2.3 `spci_qrf_v1`

该方法依照 SPCI 的核心结构使用有符号残差
`e_t = actual_t - point_forecast_t`。最近 180 个残差组成 lag=10 的条件预测样本；
训练和 live query 均按论文式的“最新到最旧”顺序排列。达到 60 个 lag-target pair
后，固定浅层 QRF 为当前残差分布赋权；加权分位数只在严格正权且有限的条件叶
支持上计算，零权全局极值不能污染 0/1 端点。随后在预声明的
`beta={0,0.05,0.10,0.15,0.20}` 中选择当日最窄的
`[Q(beta), Q(0.8+beta)]`，相同宽度取最小 beta。

本项目固定 `10` 棵树、`max_depth=2`、单线程和 seed `20260826`，且仍沿用 180 日
窗口与 E1 reset。它是对 Xu 与 Xie 的 SPCI Eq. 10--13 的确定性工程化实现，不能
声称论文条件之外的 finite-sample 或 conditional coverage 保证。

## 3. 因果和完整性协议

每个 fold-date 严格执行：

```text
读取旧状态与固定 E1 point forecast
  -> 先生成 3 methods x 8 stations = 24 个 issue
  -> 生成不含 outcome 的 candidate issue batch hash
  -> 才读取同日 8 个 actual
  -> reveal、评分并更新下一日状态
  -> 按 E1 的 fold/drift schedule 自动 reset
```

所有动态设置均写入不可变状态并进入 canonical SHA-256。候选 issue chain 绑定原
E1 issue batch hash，point forecast 要求 binary64 精确相等；ACI 控制的 alpha、区间
和 warmup 也要求逐项与 E1 相等。QRF 失败、非有限边界或 crossing 会 fail closed，
不会用另一个方法或常数回退。

反事实测试把首日一个站的 actual 改动 50 mm：首日 24 个 issue 及 batch hash
完全不变，而三种方法在该站的次日 state-before 与次日 batch hash 全部改变。这
验证的是代码中的 issue-before-reveal 因果屏障，不把 E1 文件整体预载误写成历史
盲态封存。

## 4. 三折总体结果

区间可用行数以方法自己的固定 warmup 为准。ACI 与 AgACI-EWA 均从 60 条历史后
开始；SPCI 需要 lag 10 加 60 个训练 pair，因此可用支持更少。

| method | fold | interval rows / 2,296 | coverage | absolute coverage gap | mean width (mm) | interval score 80 (mm) | max 30-date coverage gap | longest miss streak |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ACI control | 1 | 1,317 | 0.791192 | 0.008808 | 0.453594 | 0.588802 | 0.282540 | 37 |
| ACI control | 2 | 1,397 | 0.695061 | 0.104939 | 0.287116 | 0.489232 | 0.430159 | 43 |
| ACI control | 3 | 1,327 | 0.630746 | 0.169254 | 0.387131 | 0.676074 | 0.498333 | 42 |
| AgACI-EWA variant | 1 | 1,317 | 0.848140 | 0.048140 | 0.381411 | 0.455630 | 0.200000 | 23 |
| AgACI-EWA variant | 2 | 1,397 | 0.745884 | 0.054116 | 0.250212 | 0.354443 | 0.312103 | 21 |
| AgACI-EWA variant | 3 | 1,327 | 0.657121 | 0.142879 | 0.358189 | 0.455756 | 0.460238 | 40 |
| SPCI-QRF | 1 | 1,180 | 0.721186 | 0.078814 | 0.111893 | 0.157592 | 0.424444 | 36 |
| SPCI-QRF | 2 | 1,266 | 0.622433 | 0.177567 | 0.065836 | 0.146790 | 0.373810 | 17 |
| SPCI-QRF | 3 | 1,187 | 0.498736 | 0.301264 | 0.056012 | 0.163372 | 0.616667 | 51 |

在与 ACI 完全共同的 interval support 上，challenger 减去 ACI 的总体差值为：

| challenger | fold | coverage diff | abs coverage-gap diff | width diff (mm) | interval-score diff (mm) |
| --- | ---: | ---: | ---: | ---: | ---: |
| AgACI-EWA variant | 1 | +0.056948 | +0.039332 | -0.072183 | -0.133172 |
| AgACI-EWA variant | 2 | +0.050823 | -0.050823 | -0.036904 | -0.134789 |
| AgACI-EWA variant | 3 | +0.026375 | -0.026375 | -0.028942 | -0.220318 |
| SPCI-QRF | 1 | -0.093220 | +0.064407 | -0.334908 | -0.425942 |
| SPCI-QRF | 2 | -0.099526 | +0.099526 | -0.235611 | -0.354414 |
| SPCI-QRF | 3 | -0.117102 | +0.117102 | -0.350317 | -0.560293 |

## 5. 结论：有改善信号，但没有晋升结论

AgACI-EWA 是当前值得进入**未来 shadow 比较**的信号：它在 fold 2/3 缩小覆盖率
误差，在三折都降低平均宽度、interval score、30 日覆盖偏差峰值和最长 miss streak。
但 fold 1 从轻微欠覆盖变成过覆盖，绝对覆盖误差由 `0.0088` 增到 `0.0481`；跨折
覆盖标准差也由 ACI 的 `0.0659` 增到 `0.0780`。因此不能写成“稳定优于 ACI”或
“已解决校准漂移”。

固定 SPCI-QRF 配置的区间非常窄，interval score 也低，但覆盖率三折都低于 ACI，
fold 3 仅 `0.499`。这说明该 score 在当前误差尺度上的改善不能替代 80% coverage
约束；统一的 central-80 score 也不是 SPCI 所选 beta 的专属校准分数，必须与
coverage、availability 和 width 联合解释。本配置不能作为晋升候选。该负结果不能
通过查看本轮结果后反复搜索树深、lag、窗口或 beta 再回写 v1 来消除。

合理结论是：EWA 在线聚合多个自适应速度可能改善项目当前的覆盖—宽度权衡，值得
在不影响操作输出的 E2 shadow 流中前瞻检验；当前数据不足以证明原始 AgACI、SPCI
理论保证或任何灾害预警效能。

## 6. 产物与复现

| 文件 | 行数 | SHA-256 |
| --- | ---: | --- |
| `candidate_timeline.csv` | 20,664 | `8857e77a96cba8ad2ae011a822759c0c08cc65e0c6fdab684b3e3fc0334df91e` |
| `candidate_metrics.csv` | 108 | `dfc314041a2982456428b51dd4cd08c7b232706dea77ff77c200e3e76e620168` |
| `pairwise_comparison.csv` | 144 | `4e13a366bfe37b58d9692bdbefd23f4fa686ee731657460b21f04964662eccf8` |
| `manifest.json` | — | `229a26f5ec2c5a7082b14d18b8af7d21d44ba42f3b5b5d365b765f6fead4422f` |

profile 文件 SHA-256 为
`fb9db3e1e43a30d7d0b2becb3f1dee1e5bd474041ae2e7acdf51d2a8acf0a48b`；候选
issue chain 终点为
`d01de30964ed425303a7517a11688c5686830f47e44ee694f516ec7a6593b18c`。

```bash
uv run python main.py --stage ootang-prequential-calibration-bakeoff
uv run python -m unittest tests.test_calibration_challengers
uv run python -m unittest tests.test_ootang_prequential_calibration_bakeoff
```

runner 先校验受保护 E1 四文件及其完整 E1 validator，再生成 candidate timeline；
三个 CSV 以 `%.17g` 暂存并用 round-trip binary64 规则读回，重新计算 metrics、
pairwise，并由同一版本化 runner/core 从源数据确定性重放全部 candidate timeline
后才逐文件原子提升。该重放验证物化一致性，不是另一套独立实现复算；多文件提升
也不是可抵抗 SIGKILL 的 bundle-wide transaction，未来 live 消费前仍需加强。
连续两次完整运行的四个 SHA-256 完全一致。

最终验证记录：calibration core `13/13`、bakeoff runner `4/4`、pipeline
`28/28`，全仓 `584/584` 测试通过（`575.016 s`，0 failure / 0 error）；Ruff、
compileall 和 `git diff --check` 均通过。正式 v5 preflight `23/23`，状态保持
G0 PASS、G1--G4 BLOCKED、G5a 未授权。E1 四产物哈希未变，97 条保护路径聚合
仍为 `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。
最终只读 P0/P1 审计无开放问题。

## 7. E2 shadow 已实现与下一道机器门禁

上述后续协议现已由 `ootang-prequential-calibration-shadow-v1` 和 cycle v2 实现：
三套状态从 verified live issue 读取同一固定 point forecast，在任何同目标 actual
之前原子写入独立 issue-only 链；actual 公开后才由机器 reveal/update，漏签、修订、
epoch 变化和重放冲突分别自动排除、重评分、冷启动或 fail closed。coverage gap、
interval score、availability、30 日 rolling 和至少 180 个共同未来目标日的门槛已在
首个 shadow outcome 前版本化预声明，全程不需要人工选日、冻结或批准。

当前 shadow 仍是 engineering-only：激活时已有 issue、backfill 和 revision 不计未来
支持，任何 gate 达标也不会选择或自动晋升。下一道机器门禁是 runner-independent
checkpoint/input replay；其后还需可信密码学时间、immutable epoch registry/自动
轮换和长链扫描优化。只有这些门禁与未来支持同时满足，才可创建新的校准协议版本；
绝不改写当前 live v1、shadow v1 或历史 E1。完整合同见
`docs/ootang_prequential_calibration_shadow_engineering.md`。

## 8. 主要论文来源

- Gibbs, I. & Candès, E. *Adaptive Conformal Inference Under Distribution Shift*,
  NeurIPS 2021：<https://proceedings.neurips.cc/paper/2021/hash/0d441de75945e5acbc865406fc9a2559-Abstract.html>
- Zaffran, M. et al. *Adaptive Conformal Predictions for Time Series*, ICML 2022：
  <https://proceedings.mlr.press/v162/zaffran22a.html>
- Xu, C. & Xie, Y. *Sequential Predictive Conformal Inference for Time Series*,
  ICML 2023：<https://proceedings.mlr.press/v202/xu23r.html>
