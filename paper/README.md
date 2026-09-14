# 阶段报告编译说明

## 四页简报已取消，源码归档

2026-09-14，用户要求删除四页短期对比 PDF 并停止报告整理。`output/pdf/ootang_short_horizon_brief.v4.1.pdf` 已移除；最后已提交的 PDF 可从 Git `6093dc7` 追溯。本节不再提供当前交付或待运行命令。

已有未提交的措辞精简与图例调整按原内容归档：[TeX 源码](ootang_short_horizon_brief.v4.1.tex)、[生成脚本](build_short_horizon_brief.py)、[中文图件脚本](build_short_horizon_brief_figures_zh.py)及对应图件保留。它们仅是历史备份，本次没有生成报告、训练、选模或重新评分。

[来源清单](ootang_short_horizon_brief.v4.1.sources.json)、[中文图件来源](figures/short_horizon_zh/sources.json)和[删除前核验记录](ootang_short_horizon_brief.v4.1.qa.json)描述各自形成时的产物，不代表本次重新进行 PDF 核验；对应核验脚本依赖已删除的 PDF，当前不执行。

科学结论仍以[短期对比完整结果](../docs/ootang_short_horizon_comparison_results.v4.0.md)及[初态耦合完整结果](../docs/ootang_neural_initial_state_results.v1.1.md)为准。下方原报告和实验图件继续作为历史记录保留。

## 五页原版（2026-09-13）

[五页 PDF](../output/pdf/ootang_short_horizon_comparison_report.v4.0.pdf)参照原 `process_report` 的蓝色标题与简洁图文布局。内容包括七个端点的开发锁定选择、四类模型及残差配对、四点完整后期 7 天曲线、概率质量和研究边界。四点全部 1—7 天图件另存，原报告不修改。

可编辑源码：[ootang_short_horizon_process_report.tex](ootang_short_horizon_process_report.tex)；完整数值与来源见 [v4.0 结果](../docs/ootang_short_horizon_comparison_results.v4.0.md)。文档生成器只读取已保存结果：

```bash
PYTHONPATH=code .venv/bin/python -m short_horizon.report \
  --config config/ootang_short_horizon_comparison.v4_0.json
```

在仓库根目录创建 `tmp/pdfs/v4` 后，在 `paper/` 目录编译两次以固定页码与引用：

```bash
xelatex -interaction=nonstopmode -halt-on-error \
  -output-directory ../tmp/pdfs/v4 ootang_short_horizon_process_report.tex
```

最终 PDF 复制至 `output/pdf/ootang_short_horizon_comparison_report.v4.0.pdf`。图件和 PDF 的来源、视觉检查与误报解释见 [图件说明](../figures/ootang_short_horizon_v4/20260913_short_horizon/README.md)；数字及描述性统计另由 `short_horizon.qa_report` 对最终 PDF 核对。编译与核验不需要重训；PDF、TeX 与完整图件均分步备份。

## 历史四点 B+ 阶段简报（2026-09-12）

[两页 PDF](../output/pdf/ootang_bplus_process_report_20260912.pdf)参照原 `process_report` 的版式，
仅使用文字和表格，汇总最终 293 日已有预测、ConvLSTM v2.0 与 PINN v2.3 开发结果及下一步讨论。
各预测窗分别比较，保留未达标与局部收益的解释边界；没有新训练或任务变更。

可编辑源码为 [ootang_bplus_process_report.tex](ootang_bplus_process_report.tex)。在仓库根目录执行：

```bash
mkdir -p tmp/pdfs/ootang_bplus_brief output/pdf
latexmk -xelatex -interaction=nonstopmode -halt-on-error \
  -outdir=tmp/pdfs/ootang_bplus_brief paper/ootang_bplus_process_report.tex
cp tmp/pdfs/ootang_bplus_brief/ootang_bplus_process_report.pdf \
  output/pdf/ootang_bplus_process_report_20260912.pdf
```

本简报的 PDF 与源码一同备份；编译辅助文件位于被忽略的 `tmp/`。原八点报告保持原样。

## 历史八点 `process_report`

本目录保存藕塘滑坡阶段报告。用户于 2026-09-05 确认报告已提交；原导师要求已退役。
正文按该阶段的 ConvLSTM–NGBoost–SHAP 方法组织，以图为主，只保留理解方法和结果所需的文字。
本说明保留报告的复现方式，不构成后续任务的固定路线或待提交要求；本次未修改报告正文。

### 编译

在 `paper/` 目录执行：

```bash
latexmk -xelatex -interaction=nonstopmode -halt-on-error \
  -outdir=build process_report.tex
```

生成的 `build/process_report.pdf` 仅用于本地审阅。`build/` 和 PDF 均受仓库忽略规则约束，不进入 Git。

清理本地辅助文件：

```bash
latexmk -C -outdir=build process_report.tex
```

### 图件逻辑

流程总览由 Draw.io 维护：可编辑源文件为 `figures/process_overview.drawio`，报告使用其导出的
`figures/process_overview.png`。`process_report_figures.py` 生成 8 张测点预测图、ConvLSTM
三折汇总图和四指标图，不会覆盖 Draw.io 流程图。逐时预警图和完整 32 项 SHAP 图直接使用
`../figures/ngboost_auto_state_classifier_v1/` 中的版本化产物，编译报告不会重训 NGBoost。

如需从既有 CSV 免训练重绘本目录的数据图，在仓库根目录执行：

```bash
PYTHONPATH=code uv run python paper/process_report_figures.py
```

如需修改流程图，在 Draw.io 中编辑源文件后重新导出 PNG；不要把流程图改回 Python 生成。

报告按展示顺序组织：

1. 一张总览图说明 ConvLSTM、四项指标、H=7 自动标签、NGBoost、SHAP 和逐时输出的关系；
2. 全部 8 个测点各一页：依次显示全时段累计位移、测试段日增量与 P10–P90 区间、
   测试段残差与对应区间带；
3. 三折×五种子汇总图回答预测效果和区间覆盖是否稳定；
4. 四指标热图展示 514 日内全部测点的指标等级和测点内候选融合；
5. 分类器与基线表格如实保留 NGBoost 的负结果；
6. 完整时间线展示 861 个模型可用日期、三个时间折和 21 日未成熟标签；
7. NGBoost SHAP 热图完整展示 8 点 × 4 指标的 32 项模型依赖。

所有数值来自版本化 CSV 或运行清单。报告不重训模型，不重新定义阈值，不把自动代理
状态写成真实灾害等级，也不读取或启动 Vajont。
