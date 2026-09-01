# `process_report` 编译说明

本目录保存藕塘滑坡阶段报告。正文按当前 ConvLSTM–NGBoost–SHAP 主线组织，以图为主，
只保留理解方法和结果所需的文字。

## 编译

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

## 图件逻辑

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
