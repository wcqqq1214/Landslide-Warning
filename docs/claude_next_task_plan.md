# Claude 下一任务计划：切线角等速阶段复核

> 任务性质：切线角参数审计与专家复核支持。
>
> 核心边界：不得根据最终预警表现反向选择 `v_eq`，不得更换 ConvLSTM、NGBoost 或其他主模型。

## 1. 任务背景

当前预设敏感性分析表明：

- V0 参数变化会实质影响等级分布和事件定义，但整体等级一致率仍为 90.1%-100%。
- 在固定等速候选窗口的前提下，改变切线角平滑和持续性规则时，融合等级一致率为 96.9%-100%。
- 将等速候选窗口从 30 日改为 15 日或 60 日后，相对当前默认融合结果的一致率仅约 29.8%-30.5%。
- MJ1 和 MJ3 在不同候选窗口下得到的 `v_eq` 存在数量级差异。

因此，当前切线角流程的主要风险不是平滑窗口或持续性规则，而是自动等速阶段选择不稳健。下一任务应建立可审计的等速阶段复核支持流程，而不是继续扩大参数搜索。

## 2. 任务目标

为 MJ9、MJ1、MJ3 建立一套可复现的切线角等速阶段复核流程，输出：

1. 累计位移、位移速率和自动候选阶段复核图。
2. 15、30、60 日自动候选阶段参数对比表。
3. 可选的人工阶段配置与严格校验接口。
4. 人工阶段确定前后的切线角等级和融合影响审计表。
5. 对应测试、运行说明和研究限制记录。

本任务只提供证据和配置能力。最终等速阶段需要结合累计位移曲线、宏观变形资料和专家判断确定，不能由报警数量、等级一致率或模型指标自动决定。

## 3. 开始前处理

1. 检查 `git status`，保留当前工作区全部修改，不得覆盖或回退已有成果。
2. 确认以下敏感性分析文件完整：
   - `code/sensitivity_analysis.py`
   - `tests/test_sensitivity_analysis.py`
   - `figures/sensitivity/`
   - 与敏感性分析相关的 Markdown 修改
3. 运行现有验证：

```bash
uv run --with pytest pytest -q
uv run --with ruff ruff check code
uv run --with ruff ruff check tests --ignore E402
uv run python -m compileall -q code tests
git diff --check
```

4. 验证通过后，先将现有敏感性分析作为独立提交保存：

```text
feat: add warning-rule sensitivity analysis
```

提交不得包含 `Co-Authored-By`，不得创建 PR，后续直接推送 `main`。

## 4. 文献与实现核对

开始编码前完整阅读：

- `literature/一种改进的切线角及对应的滑坡预警判据_许强.pdf`
- `framework.md`
- `docs/results_report.md`
- `docs/warning-limitations.md`
- `code/tangent_angle.py`
- `code/sensitivity_analysis.py`
- `code/warning_fusion.py`

在实现和文档中明确区分：

| 内容 | 来源与性质 |
| --- | --- |
| 根据累计位移曲线和宏观变形迹象识别等速阶段 | 许强等（2009）的基本要求 |
| `alpha > 45`、`alpha > 80`、`alpha > 85` | 文献阶段判据 |
| 15/30/60 日自动候选窗口 | 当前项目的辅助算法 |
| 3 日因果平滑 | 当前项目的工程扩展 |
| 5 日内至少 3 次命中 | 当前项目的工程持续性规则 |
| 绿色、黄色、橙色、红色映射 | 当前项目的工程映射 |
| V0 主判、切线角只升级不降级 | 当前项目的融合设计 |

不得把自动候选窗口、平滑、持续性或颜色映射描述成许强等（2009）的原始方法。

## 5. 复核图表实现

新增脚本：

```text
code/tangent_stage_review.py
```

为 MJ9、MJ1、MJ3 分别生成一张多面板复核图，至少包含：

1. 全时段累计位移曲线。
2. 日位移速率及仅使用当前和历史数据的平滑速率。
3. 候选阶段内速率离散程度或加速度稳定性指标。
4. 15、30、60 日算法候选阶段的位置和训练期边界。
5. 每个候选阶段的起止日期、长度和 `v_eq`。
6. 必要时标注已有 V0 事件区间，但避免图面信息过载。

输出目录：

```text
figures/tangent_angle/review/
```

建议输出：

```text
figures/tangent_angle/review/MJ9_stage_review.png
figures/tangent_angle/review/MJ1_stage_review.png
figures/tangent_angle/review/MJ3_stage_review.png
figures/tangent_angle/review/candidate_stage_comparison.csv
```

图表中只能使用“候选阶段”等中性表述，不得自动标记“最佳阶段”或“推荐阶段”。

## 6. 人工阶段配置接口

新增配置表：

```text
config/tangent_reference_stages.csv
```

建议字段：

| 字段 | 含义 |
| --- | --- |
| `station` | 测点名称 |
| `start_date` | 等速阶段起始日期 |
| `end_date` | 等速阶段结束日期 |
| `status` | `candidate`、`approved` 或 `rejected` |
| `source` | 自动候选来源或 `expert_manual` |
| `review_note` | 阶段选择依据和审查记录 |

实现要求：

1. 初始自动候选记录必须使用 `status=candidate`。
2. Claude 不得自行将任何阶段设置为 `approved`。
3. `source` 应区分 `automatic_15d`、`automatic_30d`、`automatic_60d` 和 `expert_manual`。
4. 人工阶段必须完整位于对应训练期内，不能使用测试期或未来观测。
5. 起止日期无效、阶段样本不足、累计位移缺失或 `v_eq <= 0` 时必须明确报错。
6. 同一测点存在多个 `approved` 阶段时必须拒绝运行，不能静默选择其中一个。

在 `code/tangent_angle.py` 中增加可选的人工阶段读取能力，同时满足：

- 无已批准人工配置时，默认行为与当前实现完全一致。
- 有且仅有一个已批准阶段时，使用该阶段计算 `v_eq`。
- 所有参数输出必须记录 `method`、阶段日期和参数来源。
- 人工配置不能根据测试集融合效果自动更新。

## 7. 对比审计输出

对每个关键测点和候选阶段至少输出：

- 阶段起止日期和持续天数。
- `v_eq`。
- 阶段速率均值、中位数和 MAD。
- 平均绝对加速度。
- 有效速率样本数。
- 切线角四阶段天数。
- 相对当前默认配置的等级一致率。
- 对融合等级、升级天数和融合原因的影响。

审计表只能用于展示参数影响，不得：

- 按融合一致率自动选优。
- 按报警天数最少或最多选优。
- 按 NGBoost、V0 或测试期表现选优。
- 删除看起来不合理但真实产生的候选结果。

## 8. 测试要求

至少增加以下测试：

1. 人工阶段只能使用训练期数据。
2. 人工阶段起止日期不存在时抛出明确错误。
3. `v_eq` 只使用指定阶段计算。
4. 无人工配置时，结果与当前默认行为一致。
5. 同一测点存在多个批准阶段时拒绝运行。
6. 阶段样本不足或 `v_eq <= 0` 时拒绝运行。
7. 图表和 CSV 输出路径正确。
8. 参数来源字段可以区分自动候选和人工配置。
9. 候选阶段比较不访问训练期后的数据。
10. 原有切线角、融合和敏感性测试继续通过。

## 9. 文档更新

更新以下文档：

- `README.md`
- `design.md`
- `framework.md`
- `figures/README.md`
- `docs/framework_status.md`
- `docs/results_report.md`
- `docs/warning-limitations.md`

文档必须记录：

1. 复核图和参数表的生成方式与科研用途。
2. 自动候选阶段只是专家复核辅助，不是文献原始方法。
3. 当前是否存在已批准的人工阶段。
4. 不得根据预警结果反向选择等速阶段。
5. 最终 `v_eq` 冻结后才能开展确认性切线角事件级评价。

## 10. 验证与完成标准

执行：

```bash
uv run --with pytest pytest -q
uv run --with ruff ruff check code
uv run --with ruff ruff check tests --ignore E402
uv run python -m compileall -q code tests
git diff --check
```

任务完成必须同时满足：

- 三个关键测点均生成可审计的复核图。
- 自动候选参数可以追溯到日期和训练数据。
- 人工配置接口已经实现并有测试覆盖。
- 未擅自批准任何等速阶段。
- 未修改 `models/convlstm.pt`、`models/ngboost.pkl` 或模型结构。
- 未更换模型。
- 所有测试、Ruff、编译和差异检查通过。
- 结果和限制已经同步到项目文档。

## 11. Git 要求

新任务完成后使用以下提交信息：

```text
feat: add tangent-stage review workflow
```

要求：

- 不添加 `Co-Authored-By`。
- 不创建 `test:` 类型提交。
- 不提交 `docs/superpowers/`。
- 不创建 PR。
- 直接推送到 `main`。

## 12. 完成后汇报内容

Claude 完成任务后应报告：

1. 新增和修改的文件。
2. 三个测点的候选阶段与 `v_eq` 差异。
3. 是否发现数据泄漏或参数来源问题。
4. 当前是否存在已批准人工阶段。
5. 测试、Ruff 和编译结果。
6. 提交哈希和推送状态。
7. 仍需用户或领域专家决定的事项。
