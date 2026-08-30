# 藕塘 finalized feed 真实来源审计

**审计日期：** 2026-08-30

**结论状态：** `blocked_by_missing_authentic_source`

**适用范围：** R1 `ootang-epoch-registry` 的真实机器输入；不改变 ConvLSTM、
operational-v4、冻结数据划分、指标、阈值或科研结论。

## 决策

当前不实现 finalized-feed producer，也不把 Figshare 轮询器、通用 HTTP adapter 或人工生成的
JSON 接到 R1。现有生产边界已经足够明确：上游单一机器 writer 应把真实、完整、已 finalized
的文件原子发布到
`runtime/ootang_prequential_live_v1/incoming/daily_finalized_feed.json`，随后由既有 R1 和 source
ingest 验证、内容寻址并继续机器链路。

这个决定不是放弃机器自动化，而是避免把“能下载一个文件”误写成“能生成真实监测事实”。
在没有源端点、点位血缘、时间定义和修订语义时增加 adapter，只会自动化伪 provenance，不能
改善实验，也不能产生 live/E2/formal-warning 证据。

## 已核实的公开证据

1. Wang 等（2025）的公开藕塘复现实验数据来自 Figshare article `28171343`、version `1`；
   [Figshare article API](https://api.figshare.com/v2/articles/28171343) 给出的发布和修改时间均为
   `2025-06-19T01:52:06Z`，[versions API](https://api.figshare.com/v2/articles/28171343/versions)
   当前只列出 v1。公开工作簿 file id 为 `54029702`，MD5 为
   `372d1608f46d7fcdb9805568d1c0782a`，许可为 CC BY 4.0。
2. [Figshare 数据集 DOI](https://doi.org/10.6084/m9.figshare.28171343.v1) 与仓库既有
   `data/monitoring_data.csv` 对应；本地逐行审计得到 1461 个连续日，覆盖
   `2016-07-01`--`2020-06-30`，包含既有八个 MJ/ATU 位移列、Rainfall 与 RWL。
   [论文正文](https://doi.org/10.1029/2025JH000592) 也把研究监测期限定在该区间。
3. Figshare 的公共 API 能可靠回答“这个复现数据集是否发布了新版本”，但当前 article 没有
   v2、外部实时链接或逐日传感器 endpoint。因此它只能作为 release observer 的潜在来源，
   不能提供 `2020-07-01` 以后的 finalized records，也不能把 repository modified time 当成
   `observed_at_utc`、`available_at_utc`、`finalized_at_utc` 或逐日 `revision_id`。
4. 定向检索找到了描述后续藕塘监测的论文。例如 2025 年的
   [Applied Sciences 论文](https://doi.org/10.3390/app152212092) 描述 2022--2024 年数据，
   但其 Data Availability Statement 只说明数据由重庆市地理信息和遥感应用中心提供，没有
   行级下载地址或 API；文中还使用 JW 系列点位，不能据此假定它与当前 MJ/ATU 八点连续兼容。

所以，本次有边界的公开源检索没有找到从 `2020-07-01` 起、同时包含同一 MJ/ATU 八点位移、
Rainfall 和 RWL 的连续机器可读扩展。这是截至审计日对已检查来源的结论，不是对所有私有或
未索引数据的“不存在证明”。论文图表也不得反向数字化后冒充监测 feed。

## 现有 R1 输入合同

真实 producer 必须保留以下最小语义；这些约束已经由
`code/monitoring/ootang_epoch_registry.py` 与 `code/monitoring/ootang_live_source.py` 验证，
无需再写一层重复 validator。

- 顶层只含 `schema_version`、`outcome_source_id`、`exported_at_utc`、`records`；schema 为
  `ootang_daily_finalized_feed_v1`。
- records 从 `2020-07-01` 开始，到本次 watermark 为止逐日连续，不能只发送最新一天；文件
  上限为 16 MiB。
- 每日 record 只含固定 v1 字段，`finalized=true`，并带真实的 `revision_id`、
  `observed_at_utc`、`available_at_utc` 与 `finalized_at_utc`。
- 原始量必须是 `rainfall_mm`、`reservoir_water_level_m` 和顺序固定的八点
  `displacement_mm`：`ATU1`、`ATU2`、`ATU3`、`ATU4`、`ATU5`、`MJ1`、`MJ3`、`MJ9`。
  Rain_cum7/15/30 与 RWL rate 继续由可信代码内部推导，源端不得提交外生派生列。
- `outcome_source_id` 必须稳定；feed 只能 append 或以新 revision 前向修正，不能缩短、回滚
  已见历史或切换来源身份。

## 接入前仍缺少的真实来源信息

只有同时取得下列信息，才应实现 source-specific producer：

1. 上游 endpoint、文件导出或消息队列、认证方式、数据责任方和机器访问授权；
2. 同一八点的权威设备映射、单位、坐标/基准点、位移参考 epoch，以及 MJ/ATU 是否在换机后
   保持同一语义；
3. Rainfall、RWL 与 GNSS 的采样频率、日值聚合、质量控制、缺测和异常剔除规则；
4. `observed`、`available`、`finalized` 三个时刻的源端定义，以及迟到、修正、撤回和设备重算
   如何产生不可回滚的 `revision_id`；
5. 从 `2020-07-01` 起的完整连续历史、授权/许可、可复核 checksum 或签名，以及稳定的
   `outcome_source_id`；
6. 一个上游单 writer 发布协议：先写同目录临时文件，完成 flush/fsync 后 atomic replace，再
   触发 R1；不得由人手工放置、冻结或补日期。

传输时间、HTTP ETag、下载时间或本地文件 mtime 只能证明 transport/repository observation，
不能替代上述科学记录时间。

## P1：日尺度时间合同的可执行性

当前 v1 同时规定：

- Rainfall 单位为 `mm_per_natural_day`，自然日时区是 `Asia/Shanghai`；
- 当日 record 必须在下一自然日边界之前 finalized；
- watermark+1 的预测 bundle/issue 必须在该目标自然日开始之前已经持久化或生成。

如果 `mm_per_natural_day` 指闭合的 00:00--24:00 完整日总量，那么该值通常只有在日界之后
才能确定；这与“日界之前 finalized，并在同一日界之前生成下一日预测”存在可执行性冲突。
真实源也可能采用 23:xx 截止、固定观测时刻或非日历累计窗，但当前公开数据和配置没有定义
这种语义。因此这不是通过调代码时钟即可解决的问题。

接入真实源前，必须由源协议给出可复核的 accumulation interval、cutoff、迟到修订规则和
目标可用时间。若真实语义不是完整自然日，后续应新建版本化 source/target profile，选择并
预先记录一种因果方案，例如“固定截止窗预测下一自然日”或“完整 D 日数据预测 D+2”；不能
静默改写现有 v1、复用旧结果标签，或让已查看的测试段选择方案。

## 下一次可执行动作

当前 R1 的正确机器状态仍是 `waiting_for_candidate_feed`。一旦取得满足上方条件的真实源：

1. 为该具体来源实现最薄的 machine adapter；保存源响应 checksum/签名和 transport receipt；
2. 依据已确认的源端定义生成完整 v1 feed（或经独立审查后的新版本合同），以单 writer 原子
   发布到既有 incoming path；
3. 只调用一次 R1。R1 若仍 waiting 或 fail closed，立即停在首个 gate，不运行 R2a、训练或
   下游 lifecycle；R1 成功后才恢复既有自动链。

如果 Figshare 将来出现 v2，机器可以先报告“new release observed”，但在新文件通过点位、日期、
时间和修订血缘审查前不得自动写 incoming feed。换用其他滑坡或 JW 点位数据则是新的数据集/
模型协议，不是当前 feed 的无缝续接；本审计也不授权 Vajont。
