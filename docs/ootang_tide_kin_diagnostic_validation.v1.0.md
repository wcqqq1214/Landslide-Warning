# 原 TiDE_KIN 距离诊断：核验 v1.0

本轮只重放既有权重，不训练、选检查点或改原预测。正式诊断与独立审计均完整通过；科学解释与图件核验另列于研究结果及最终回执，执行通过不代表模型改善。

## 数值与信息边界

- 来源清单283文件；全部60份原KIN检查点、1816个起点—前缀组合、27240次训练起点评价及48条可见性条件种子路径完成。
- 预检141项、978945个数值，最大差1.687538997430238e-14；核对手算损失系数、时间对齐、未来标签/驱动隔离、完整输入恒等和冻结权重。
- 独立审计1789项、45766892个数值，最大差1.1641532182693481e-9，在预设绝对容差内。使用独立输入/成熟标签构建及NumPy网络前向，重算全部训练指标、原发报评分、MSE恒等式及可见性结果。
- 612启动路径原本没有概率尺度，保持不可用；审计6300次不可用单元比较，不将它们当独立样本数。没有用未来误差补建概率池。
- 新拟合、优化更新、B+拟合、物理前向均0；原发报数组精确保持，未产生替代发报，检查点不重选。

## 复现入口与失败记录

在项目根目录使用独立运行环境 `/tmp/ootang-tide-env-20260915/bin/python`，设 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code`。入口为 `tide_kin_diagnostic.preflight`、`tide_kin_diagnostic.run`、`tide_kin_diagnostic.audit`。run/audit支持`--attempt`写入独立尝试目录；预检写入冻结的preflight目录，不应原地再跑覆盖。旧预算不恢复，未来重跑须另设版本/输出及时间窗口；原环境不修改。

准备时来源枚举曾错误要求612存在sigmas文件，冻结前已修正为明确缺失，原错误及失败的依赖提交留在 `preparation_notes.json`。正式诊断和独立数值审计失败重试均0。

可追溯产物： [配置](../config/ootang_tide_kin_diagnostic.v1_0.json)、[计划](ootang_tide_kin_diagnostic_plan.v1.0.md)、[来源](ootang_tide_kin_diagnostic_sources.v1.0.json)、[独立数值回执](../results/ootang_tide_kin_diagnostic_v1/20260915/audit_v1/receipt.json)。数值审计不检验物理因果或新增方法有效性；旧协议、数据和探索性局限保持。

## 图形与解释核验

四图16面板/152条科学曲线、468项/21088个源与实际图形值通过。独立从已审计CSV重新选择和汇总曲线，源最大差7.105427357601002e-15，实际SVG最大差4.997838232156937e-7pt；全部科学标记在坐标范围内。实际尺寸240×170mm，PNG300dpi；字体最小7.2pt，1.5pt对齐门通过，四图碰撞FAIL/WARN均0。全部PNG已逐图目视，无截字、重叠或不利数据省略。

源代码静态检查四项WARN均已解释：无投稿TIFF要求、冻结300dpi研究预览、240/25.4被静态扫描误读但实际尺寸正确、种子用曲线/全部散点而非推断置信带。原发报集成误差图按契约集中三方法，完整六方法和各种子仍在CSV。见[图件入口与目视记录](../figures/ootang_tide_kin_diagnostic_v1/20260915/README.md)、[独立图形回执](../results/ootang_tide_kin_diagnostic_v1/20260915/figure_qa_v1/receipt.json)。仅临时同源PDF用于QA，不交付PDF。

11/11统计解释项目已检查，未新增p值/显著性或效果门；[解释记录](../results/ootang_tide_kin_diagnostic_v1/20260915/statistical_interpretation_audit.json)保留种子平均/集成、损失系数/梯度、可见性依赖/效果/因果及概率迁移边界。研究表格与关键叙述另外作CSV及本地链接核对，结果见[文档核验](../results/ootang_tide_kin_diagnostic_v1/20260915/document_audit_v2/receipt.json)。实施与验证完成、没有新模型效果结论、用户/导师尚未验收分别记录。

最后文档封装v1因前序样式检查使回执文件未生成，后续链接检查发现缺失而失败。v1源码/失败回执和[问题记录](../results/ootang_tide_kin_diagnostic_v1/20260915/document_assembly_issue.json)保留；修复命令依赖顺序、补齐组装回执并以v2重新核验，未修改训练、预测、评分或图件。文档核验重试1次，与正式诊断/数值审计/图形失败重试0分别报告。
