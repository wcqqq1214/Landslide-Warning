# 藕塘历史信息审计与前缀接口 v1.16：原失败及补充结果

## Material Passport

- 日期：2026-09-11；Origin Skill/Mode：academic-research-suite / experiment-agent / validate。
- Verification Status：ANALYZED；v1.16 原执行 exitcode=1，v1.16.1 补充执行及独立核验 exitcode=0。
- 接口与数值核验完成；三来源原等值标准未通过，原始观测当时可用性仍为 unknown。
- 没有新拟合、神经/力学调用或预测。整体四点精度与概率目标未实现，用户/导师接受状态另计。

## 1. 原运行为什么失败，补充改变了什么

按[v1.16 方案](ootang_bplus_history_availability_plan.v1.16.md)先检查三份来源。
原运行在来源数值比较处停止，启动总耗时 1.309 秒，历史数组尚未生成。
原方案、源码、日志、失败和 1e-12 容差均完整保留，未将原结果改写为通过。

随后只读定位到导师 CSV 的末位小数差异，并在补充执行前提交
[v1.16.1 方案](ootang_bplus_history_completion_plan.v1.16.1.md)。补充按原容差记录
全部三对比较，只要求公开 CSV/XLSX 一致才继续从公开 CSV 构造历史。这个处理
变化与原失败分开登记，没有修改来源、放宽数值门限或覆盖旧代码。

补充科学进程 3.440 秒，含启动 3.658 秒；固定 120 秒上限内完成一次执行。
所有历史读取范围仍为前 612 日，窗口、四点顺序和差分定义不变。

## 2. 来源与工作簿核对

核对当前 `data/monitoring_data.csv`、`data/monitoring_data.xlsx` 和导师 ZIP 最终
`section2d_v4/work/delivery_final/outang_repro/work/monitoring.csv`。哈希及列映射见
[来源检查](../results/ootang_bplus_v1_16/20260911_history_completion/source_checks.json)。
数值范围仅为 2016-07-01 至 2018-03-04 的 612 行、四点位移和雨量/库水位六列。

| 来源比较 | 比较值数 | 最大绝对差 | 超过原 1e-12 容差的值数 | 原标准 |
| --- | ---: | ---: | ---: | --- |
| 公开 CSV / XLSX | 3,672 | 0 | 0 | 通过 |
| 公开 CSV / 导师 CSV | 3,672 | 5.002220859751105e-12 mm（MJ3） | 240（均为 MJ3） | 未通过 |
| XLSX / 导师 CSV | 3,672 | 5.002220859751105e-12 mm（MJ3） | 240（均为 MJ3） | 未通过 |

最大差出现在 2017-05-03：公开表 MJ3 为 1005.100114453055 mm，导师 CSV 为
1005.10011445305 mm。它是数值末位差异；本轮未核实产生它的具体序列化方法，
也未据它重新评价旧模型。三方日期对应，公开 CSV/XLSX 的全部列名及顺序一致。

XLSX 实际只有一个可见工作表 `Sheet1`，无公式或隐藏工作表；XML 结构为
1,461 行数据、17 列，采用 1900 日期体系。只转换第 2–613 行中日期和六个指定
数值列，其余行仅检查 XML 结构。未转换 612 日后的位移值，未修改原工作簿。
来源一致只证明已发布建模表的对应关系，不恢复原始 GNSS 观测链。

## 3. 四个起点的历史接口

[接口](../code/physics_guided_history_availability/core.py)名为 `materialized_history`。
第一个预测日索引为 c，读取仅限前 c 行；返回 `[c−30,c)` 的四点累计位移和
日增量。第一条增量额外使用 c−31 日，因此缺失该左端点也报错。增量是物化日表
的相邻日差分，不称未经处理的原始 GNSS 速度。

| c | 第一个预测日 | 返回历史范围（两端含） | 差分额外左端点 | 同月过去行 / 剩余日 |
| ---: | --- | --- | --- | --- |
| 252 | 2017-03-10 | 2017-02-08 至 2017-03-09 | 2017-02-07 | 9 / 22 |
| 342 | 2017-06-08 | 2017-05-09 至 2017-06-07 | 2017-05-08 | 7 / 23 |
| 432 | 2017-09-06 | 2017-08-07 至 2017-09-05 | 2017-08-06 | 5 / 25 |
| 612 | 2018-03-05 | 2018-02-03 至 2018-03-04 | 2018-02-02 | 4 / 27 |

点位顺序保持 ATU1、ATU5、MJ3、MJ1。每组 NPZ 有 30 个日期及两个 30×4 数组
`u`/`du`；CSV 共 480 条点位记录。接口拒绝缺日、重复、乱序、非有限值与不足历史，
没有插值、平滑、填补、scaler 拟合或预测平移。

将四个起点连接到既有逐月指纹后，16 个点月均满足原三次多项式数值指纹标准。
四个起点都切在同一自然月的日值段中间；剩余日数由日历计算，指纹直接引用冻结
旧表，未读取对应未来位移重新拟合或作月内预测。这不证明已使用未来观测，也不
证明原始日值在这些起点当时可获得。上游锚点、处理算法与未来信息使用状态仍未知。

[可用性记录](../results/ootang_bplus_v1_16/20260911_history_completion/availability.json)
明确区分 `released_prefix_constructible=true` 与
`raw_observation_as_of_verified=unknown`。合成未来行隔离只验证本次下游接口，
不能替代上游生成过程的证据。原始 GNSS 无法取得的既有项目约束保持，现有日表
上的条件历史原型可以继续，不重新把索取原始材料列为原型前置任务。

## 4. 验证与完整性

14 项合成测试、Ruff/格式/CLI 检查通过。测试包括预测日起点及未来值毒化、逐行
访问边界、差分左端点、异常历史拒绝、XLSX 未来数值不转换、来源日期错位拒绝，
以及导师差异必须保留失败状态。开发期修正过一处合成差分预期值和补充模块导入，
均在相应正式执行前完成，没有改动真实数据、差分公式或门限。

封存后独立解析 CSV，用标量相邻行差核对全部历史值。960 个历史数值在 NPZ/CSV
分别比较，共 1,920 次检查，位移及日增量最大差均为 0；32 个旧指纹数值精确
一致。三对来源共 11,016 次比较，导师差异仍显式记为未通过。日期、点位、月内
计数及 unknown 状态精确核对，2,074 个保护文件保持。

统计解释 11/11 类已检查：完整四点/四起点均保留，不作跨点汇总推断或选择性筛样，
避免 Simpson、生态、Berkson、碰撞及幸存者偏差；没有分类基准率、显著性搜索或
极端值干预推断，基准率、look-elsewhere 和均值回归不产生效能结论；补充处理变化
另版登记、原失败保留，避免多路径选择掩盖结果；同月数值关联不作因果或反向因果
解释。该审计没有模型效果估计，不能把 480 行视作 480 个独立原始 GNSS 样本。

原失败 14 个文件与补充 24 个文件均包含源码快照、日志和索引；补充被索引内容
为 390,209 字节。原失败索引 SHA-256：
`e93490b465e1a2655d51e900da9fd2e32c079d9b261083bd00d003369b734b58`；
补充索引 SHA-256：
`9c837930d62d39a30341c060cb596813f6c2abd37c414b43bd94a43bf15baedd`。

## 5. 下一步与提交

下一步另版固定“起点前历史位移/增量编码 + 同一 B+ 引导”的有限对照，明确输入
表示、一次性使用历史的预测方式、比较对象和预算。保留原时间划分及已经暴露的
历史窗事实；整段预测不注入未来实测。逐点跨窗评价均值误差，并在同一流程下评价
概率覆盖、区间宽度和 CRPS。本次没有新训练预算，不据接口通过宣称历史编码有效。

分步提交：`f6a28a4` 原方案，`e9b78b8` 原实现/测试，`5fcfa20` 原失败，
`4deb041` 补充方案，`3a7c6b5` 补充实现/测试，`6778fea` 补充产物；结果与入口另行提交。
未 push/创建 PR，无关 Vajont 表格保持未跟踪。

补充只读复核命令（使用本机已配置的 bundled Python）：

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code \
/Users/wcqqq1214/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m physics_guided_history_completion.run --run-id 20260911_history_completion --verify
```

产物：[起点](../results/ootang_bplus_v1_16/20260911_history_completion/origins.csv)、
[旧月内指纹连接](../results/ootang_bplus_v1_16/20260911_history_completion/monthly_links.csv)、
[历史位移/增量](../results/ootang_bplus_v1_16/20260911_history_completion/history.csv)、
[数值复核](../results/ootang_bplus_v1_16/20260911_history_completion/verification.json)。
