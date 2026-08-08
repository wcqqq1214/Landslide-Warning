# `process_report` 编译说明

本目录保存面向导师的藕塘滑坡研究阶段进展报告。正文采用简体中文和图件驱动结构，报告当前工程原型、实际结果、证据边界及下一步需要确认的问题。

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

报告按“问题—证据—解释—边界”的顺序组织，而不是按代码模块罗列：

1. 一张总览图说明数据、ConvLSTM、SHAP、四指标和多测点融合之间的关系；
2. 全部 8 个测点的训练、校准和测试预测图回答模型输出是否完整；
3. 三折×五种子汇总图回答预测效果和区间覆盖是否稳定；
4. SHAP 图回答独立解释模型依赖哪些特征组；
5. 完整时间线和典型日图回答逐时刻状态与空间融合如何形成；
6. 结尾明确区分已经跑通的原型、尚未完成的正式方法和需导师确认的决策。

所有数值来自版本化 CSV 或运行清单。报告不重新定义阈值，不把现有草案升级为正式预警，也不读取或启动 Vajont。
