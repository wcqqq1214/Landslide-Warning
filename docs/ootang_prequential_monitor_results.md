# 藕塘机器 prequential 监测器 E1 结果

> 运行日期：2026-08-26<br>
> 协议：`ootang-prequential-monitor-v1`<br>
> 证据层级：E1 历史 prequential replay<br>
> 产物状态：`retrospective_prequential_self_supervised_not_confirmatory`<br>
> 正式预警：`false`

## 1. 本轮完成了什么

本轮实现并物化了一条不需要逐日人工挑样本、人工冻结阈值或人工批准状态更新的
机器科研监测支路。输入为固定 5-seed、3-fold 严格时序 OOF P50 与 persistence；
每个目标日先用旧状态生成全部 8 个测点 issue，再统一 reveal 同日 outcome，并且
只允许 outcome 更新未来日期。

机器自动执行：

- 站点特异的 scale-free exponential expert weighting；
- 基于过去绝对残差的双侧对称 conformal 区间与 ACI；
- 基于正向低估残差的单侧经验 p-value 和连续 anomaly score；
- ADWIN-inspired 有界 Hoeffding 漂移检测、自动状态重置与 rewarm abstain；
- O1/O2/O3 内 block max、跨 block min 的连续空间聚合；
- run-wide issue-only SHA-256 审计链、逐点状态前后哈希、原子产物提升。

这不是实时盲态运行。源 CSV 在 E1 进程开始时整体载入并校验；因果保证是同日
`actual` 不进入 issue 载荷、issue-time 状态或 issue hash，不能表述为 outcome
在当年已经物理隔离或经可信时间戳封存。E2 live runner 尚未实现。

## 2. 输入与产物规模

- 源预测：34,440 行 = 5 seeds × 3 folds × 287 dates × 8 stations；
- 日期：2018-02-21 至 2020-06-30，共 861 个 fold-date；
- station timeline：6,888 行；
- site timeline：861 行；
- metrics：27 行 = 3 folds ×（8 stations + 1 overall）；
- 正常 point forecast：4,041 行；abstain/rewarm：2,847 行；
- 自动漂移重置：26 次；可对外计算的连续 anomaly：4,041 行。

空间输出状态为：

- `complete_station_coverage`：80 日；
- `available_subset_all_blocks`：445 日；
- `abstain_spatial_incomplete`：336 日。

`available_subset_all_blocks` 只是三个 block 均有可用连续分数，但并非所有站都
active；它不能被解释成完整空间覆盖或低风险。

## 3. 三折总体结果

“all”包括 warmup/rewarm 的 shadow point forecast；“active”只包括区间和 anomaly
可用、`issue_forecast_action=point_forecast` 的行。skill 定义为
`1 - model_metric / persistence_metric`，正值才优于 persistence。

| fold | all RMSE skill | all MAE skill | active RMSE skill | active MAE skill | active coverage | mean width (mm) | abstain rate | drift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | -0.200329 | 0.119925 | 0.106951 | 0.103120 | 0.791192 | 0.453594 | 0.426394 | 9 |
| 2 | -0.086410 | 0.132698 | -0.035278 | 0.083312 | 0.695061 | 0.287116 | 0.391551 | 8 |
| 3 | 0.038628 | 0.119456 | 0.036118 | 0.090128 | 0.630746 | 0.387131 | 0.422038 | 9 |

具体点误差为：

| fold | all RMSE model / persistence (mm) | all MAE model / persistence (mm) | active RMSE model / persistence (mm) | active MAE model / persistence (mm) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.294220 / 0.245116 | 0.143519 / 0.163076 | 0.184301 / 0.206373 | 0.119148 / 0.132847 |
| 2 | 0.130383 / 0.120012 | 0.080352 / 0.092646 | 0.142504 / 0.137648 | 0.101122 / 0.110313 |
| 3 | 0.327118 / 0.340261 | 0.158967 / 0.180533 | 0.353721 / 0.366975 | 0.179832 / 0.197646 |

## 4. 科学解释

当前 ensemble 的 MAE 在三折均优于 persistence，但 RMSE 没有稳定优势：all
population 的 fold 1/2 更差，active population 的 fold 2 仍更差。因此不能写成
“在线组合稳定优于朴素基线”。更合理的结论是它减少了典型绝对误差，但偶发较大
误差仍会破坏平方误差表现。

目标覆盖率为 0.8。fold 1 覆盖率 0.791 接近目标，fold 2/3 下降到 0.695/0.631，
说明当前 rolling absolute-residual conformal + 固定 ACI 更新在后续机制变化下
校准不足。v1 不宜直接晋升生产；下一模型研究应以新版本预声明比较 SPCI、AgACI
或其他时间依赖校准 challenger，而不是在已经查看的三折输出上反复调参并改写
本版本。

最大 anomaly score 为 2.257679，对应 180 日历史窗下经验 p-value 下限
`1/181`。多个相邻日达到同一上限并不等于多个独立灾害事件。没有独立现场结局
时，该分数只能表述为“相对先前预测机制的正向残差新颖性”，不得计算灾害
AUROC、event recall、FAR、报警提前量或风险概率。由于残差窗口会自适应更新且
时间相关，该经验秩也没有被证明具备经典 p-value 的显著性/I 类错误保证。

## 5. 因果、完整性与确定性校验

runner 的 bundle validator 与回归测试共同验证：

- v1 配置必须与完整预声明 profile 精确相等，未知键、缺失键或任一
  算法语义漂移都会 fail closed；
- 从源 manifest 的 commit
  `1e06629119e08b33ded2540a435e726c2d2da97a` 取回历史
  `data/features.csv` Git blob（SHA-256
  `366188ba1f55fd56b6606f5333e34566fca26c4771dd9745eebfabfcbd4286d1`，
  1,424,208 bytes），精确核对 6,888 条 target actual 和 6,888 条上一
  自然日 persistence，首日参考 2018-02-20；
- 固定 fold/date/station 顺序，同日必须恰有 8 个 station issue；
- 从全零 genesis 开始逐日重算 run-wide issue batch hash；
- 每批 `previous_issue_batch_sha256` 必须链接上一批，station/site hash 必须一致；
- 同一 fold/站点的上一日 `reveal_state_after_sha256` 必须等于下一日
  `issue_state_before_sha256`；
- 修改同日 actual 不改变该日 issue 或 batch hash，只能影响 reveal 和未来状态；
- 修改未来 actual 不改变完整因果前缀；输入行顺序不改变时间线或哈希链；
- CSV 以 `%.17g` 落盘、用 binary64 round-trip 规则重读；提升前从
  staged 文件重算 issue chain，完整重放站点状态/ACI/anomaly/drift，
  并重建 site 聚合和 metrics；
- 配置、源 manifest、实现、依赖和输出均由 SHA-256 绑定；半成品不会覆盖旧 bundle。

最终验证结果：

- prequential 模块：19/19 通过，用时 59.487 秒；
- prequential + pipeline + G0--G4 preflight + automatic-V0 + v5 定向回归：91/91 通过，用时 98.267 秒；
- 全量：373/373 通过，用时 326.565 秒；
- `ruff`、`compileall`、`git diff --check`：通过；
- 默认 dry-run：严格保持 `features -> convlstm -> ootang-operational-v4`；
- 相同显式阶段连续复跑：四个输出 SHA-256 完全一致；
- 97 个 v4/ConvLSTM/NGBoost/model 受保护路径聚合哈希保持
  `6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`。

## 6. 最终产物与哈希

| 文件 | 行数 | SHA-256 |
| --- | ---: | --- |
| `figures/prequential_anomaly_ootang_v1/station_timeline.csv` | 6,888 | `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe` |
| `figures/prequential_anomaly_ootang_v1/site_timeline.csv` | 861 | `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd` |
| `figures/prequential_anomaly_ootang_v1/prequential_metrics.csv` | 27 | `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96` |
| `figures/prequential_anomaly_ootang_v1/manifest.json` | — | `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253` |

终端 issue batch hash 为
`b89e98453a4d18c061291b60cf78d1f5badfc7a5a640a170c66b6010fb600ec6`。
输入预测文件 SHA-256 为
`2627ad33c8caab5ff216ab5dd4cd6799e020625434a282300cfd149155ba6f6d`，
输入 manifest SHA-256 为
`6e6fd0361241803a3cf5404583fd0a1be413abc227863d98d3b0bfddd653ffb5`。

## 7. 复现命令与下一步

```bash
uv run python main.py \
  --stage ootang-prequential-monitor \
  --manifest /tmp/ootang-prequential-monitor-v1-run.json

uv run python -m unittest tests.test_ootang_prequential_monitor
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run ruff check code tests main.py
uv run python -m compileall -q code main.py tests
```

下一工程步不是人工审核每日样本，而是实现 E2 live runner：自动 ingest 新数据、
真实 issue/outcome 隔离、append-only 事件 ledger、幂等恢复、修订事件与自动外部
时间锚。没有新日期时，机器应保持 `waiting_for_new_data`；有新日期时按同一协议
自动推进。E3 只有在独立、机器可读、带可见时间的灾害结局源到位后才能启动，
不能由本监测器自己的残差或 anomaly score 生成。
