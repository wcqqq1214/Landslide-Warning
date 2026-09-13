"""Render a compact presentation of frozen v4.0 results; no model execution."""

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/ootang_short_horizon_v4/20260913_short_horizon"
FIGURES = ROOT / "figures/ootang_short_horizon_v4/20260913_short_horizon"
SOURCE = ROOT / "paper/ootang_short_horizon_brief.v4.1.tex"
RECEIPT = ROOT / "paper/ootang_short_horizon_brief.v4.1.sources.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    summary_path = RUN / "analysis/summary_by_horizon.csv"
    selection_path = RUN / "analysis/selection_by_horizon.csv"
    point_path = RUN / "analysis/metrics_by_point_horizon.csv"
    summary = pd.read_csv(summary_path)
    selection = pd.read_csv(selection_path)
    point = pd.read_csv(point_path)
    dev = summary[summary.phase == "development"].set_index(["model", "horizon"])
    later = summary[summary.phase == "later_exploratory"].set_index(
        ["model", "horizon"]
    )
    assert selection.horizon.tolist() == list(range(1, 8))
    assert selection.recommended.tolist() == ["RR_DIRECT"] + ["C16_CORE_RULES"] * 6
    assert selection.mean_best.eq("C16_CORE_RULES").all()
    assert selection.probability_best.eq("C16_CORE_RULES").all()
    assert selection.later_pass.all()

    labels = {
        "B_ANCHOR": "锚定 B+",
        "DRIFT1": "当天速度外推",
        "CL_DIRECT": "ConvLSTM 直接版",
        "CL_BRES": "ConvLSTM 残差版",
        "PINN_EQ": "PINN 方程约束版",
        "PINN_NOEQ": "PINN 去方程对照",
        "RR_DIRECT": "岭回归直接版",
        "RR_BRES": "岭回归残差版",
        "C16_CORE_RULES": "在线回归＋反馈",
        "C16_PHYS_RULES": "在线回归＋反馈＋物理误差",
    }
    rows = []
    expected = []
    for r in selection.itertuples():
        values = later.loc[(r.recommended, r.horizon)]
        cells = [
            f"{values.mae:.6f}",
            f"{values.rmse:.6f}",
            f"{values.crps:.6f}",
            f"{100 * values.coverage90:.2f}",
        ]
        rows.append(
            f"{r.horizon} & {labels[r.recommended]} & "
            + " & ".join(cells[:-1])
            + f" & {cells[-1]}\\% \\\\"
        )
        expected.extend(cells)

    model_rows = []
    for name, label in labels.items():
        d, a = dev.loc[(name, 7)], later.loc[(name, 7)]
        cells = [
            f"{d.rmse:.4f}",
            f"{a.rmse:.4f}",
            f"{a.crps:.4f}",
            f"{100 * a.coverage90:.2f}",
            f"{a.width90:.4f}",
            f"{a.interval_score90:.4f}",
        ]
        model_rows.append(
            label + " & " + " & ".join(cells[:3])
            + f" & {cells[3]}\\% & " + " & ".join(cells[4:]) + r" \\"
        )
        expected.extend(cells)

    points = point[
        (point.phase == "later_exploratory")
        & (point.model == "C16_CORE_RULES")
        & (point.horizon == 7)
    ].set_index("point")
    captions = {}
    for name in ["ATU1", "ATU5", "MJ3", "MJ1"]:
        a = points.loc[name]
        rmse, cov = f"{a.rmse:.4f}", f"{100 * a.coverage90:.2f}"
        captions[name] = (
            f"{name}：7 天 RMSE {rmse} mm，90\\% 区间覆盖 {cov}\\%。"
        )
        expected.extend([rmse, cov])

    tex = r"""\documentclass[UTF8,zihao=-4]{ctexart}
\usepackage[a4paper,top=18mm,bottom=18mm,left=22mm,right=22mm]{geometry}
\usepackage{graphicx,xcolor,booktabs,caption,amsmath,hyperref,fancyhdr}
\xeCJKsetup{PunctStyle=plain}
\definecolor{reportblue}{HTML}{365F91}
\definecolor{lightblue}{HTML}{EEF3F8}
\hypersetup{colorlinks=true,linkcolor=reportblue,urlcolor=reportblue}
\graphicspath{{../figures/ootang_short_horizon_v4/20260913_short_horizon/}}
\captionsetup{font=footnotesize,labelfont=bf,labelsep=quad,skip=2pt,hypcap=false}
\setlength{\parindent}{0pt}\setlength{\parskip}{4pt}
\setlength{\headheight}{14pt}\setlength{\footskip}{10mm}
\renewcommand{\arraystretch}{1.13}
\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0.3pt}
\fancyhead[L]{\small\color{reportblue}藕塘滑坡｜1--7 天位移预测对比}
\fancyhead[R]{\small 简报 v4.1 · 2026-09-14}
\fancyfoot[C]{\small\thepage\ / 4}
\newcommand{\pagetitle}[1]{{\Large\bfseries\color{reportblue}#1}\par\vspace{1mm}}
\newcommand{\takeaway}[1]{\colorbox{lightblue}{\parbox{\dimexpr\textwidth-2\fboxsep}{\small #1}}\par}
\newcommand{\reportfigure}[2]{\begin{minipage}{\textwidth}\includegraphics[width=\linewidth]{#1}\captionof{figure}{#2}\end{minipage}\par}
\begin{document}
\pagetitle{1\quad 七个步长，分别比较}
\takeaway{\textbf{主要结果：}在线回归＋短期误差反馈表现最好；B+ 物理参照的额外收益尚不稳定。}
{\small 每天使用过去 30 天观测，预测第 1、2、3、4、5、6、7 天后的位移；已发出的预测固定保存。四点：ATU1、ATU5、MJ3、MJ1。}\par
\reportfigure{horizon_comparison.pdf}{同一步长比较同一组起点。家族代表按开发 RMSE 固定；纵轴为对数。}
\textbf{兼顾均值与区间的锁定推荐}\quad{\footnotesize 下表均为后期结果；误差单位 mm。}
\begin{center}\footnotesize
\begin{tabular}{clrrrr}\toprule
步长 & 推荐组合 & MAE & RMSE & CRPS & 90\% 覆盖\\\midrule
@@SELECTION@@
\bottomrule\end{tabular}
\end{center}
{\footnotesize 七步长的开发 RMSE／CRPS 最低者均为在线回归＋反馈。1 天的开发覆盖率 95.81\% 超过预定 95\% 上限，因此综合推荐选岭回归；后期保持名单。RMSE 为四点各自 RMSE 的平均。}

{\footnotesize\color{gray}开发：2018-09-01 至 2019-09-11；后期：2019-09-12 至 2020-06-30。后期各步长每点样本数为 293、292、291、290、289、288、287。}
\clearpage
\pagetitle{2\quad 四类模型与残差学习}
{\small 固定看 7 天后位移。所有模型采用相同的观测截止与成熟误差校准；已知未来真实驱动的 B+ 不参加主比较。}\par
\begin{center}\fontsize{8.7}{11}\selectfont\setlength{\tabcolsep}{3pt}
\begin{tabular}{lrrrrrr}\toprule
模型 & 开发 RMSE & 后期 RMSE & CRPS & 90\% 覆盖 & 区间宽度 & 区间评分\\\midrule
@@MODELS@@
\bottomrule\end{tabular}
\end{center}
{\footnotesize 除“开发 RMSE”外均为后期结果，误差／宽度／评分单位 mm。覆盖率接近目标且区间评分低更好；神经结果先合并三种子均值再评分。}\par
\reportfigure{paired_effects.pdf}{固定配对的平均 RMSE 差：负值表示前者更好。四面板纵轴尺度不同。}
\takeaway{\textbf{ConvLSTM：}两版仍未超过当天速度外推。\quad\textbf{PINN：}本版物理状态验收失败。}
{\small 残差与物理参照未带来稳定额外收益。在线回归的优势来自趋势特征、在线更新与误差反馈的完整方案；神经与普通岭回归在阶段内固定权重。}

{\footnotesize\color{gray}B+ 采用最近七日平均降雨和最新库水位保持；概率层统一使用最近 90 条成熟预测误差。PINN 去方程对照仍保留相同物理背景。}
\clearpage
\pagetitle{3\quad ATU1、ATU5：完整后期曲线}
{\small 展示在线回归＋反馈的 7 天预测，每点 287 个起点。每组依次为累计位移、7 日总增量、实测减预测；蓝带为相对均值的 90\% 边际预测区间。}\par\vspace{2mm}
\reportfigure{forecast_atu1_h7.pdf}{@@ATU1@@}
\vspace{4mm}
\reportfigure{forecast_atu5_h7.pdf}{@@ATU5@@}
\vspace{2mm}
\takeaway{两点的区间覆盖仍偏低。困难尾段和误差尖峰全部保留，累计曲线重合不等于所有变化均已准确预测。}
\clearpage
\pagetitle{4\quad MJ3、MJ1：完整后期曲线}
{\small 同样保留全部后期合法起点；图中每条预测均提前 7 天发出。不同测点坐标范围分别标注，7 日增量的单位为 mm。}\par\vspace{2mm}
\reportfigure{forecast_mj3_h7.pdf}{@@MJ3@@}
\vspace{4mm}
\reportfigure{forecast_mj1_h7.pdf}{@@MJ1@@}
\vspace{2mm}
{\small\textbf{解释范围：}公开日序列的月内三次结构、评价期反复暴露限制泛化主张；低位移误差尚不能证明真实失稳预警有效。}

{\small\textbf{下一步：}保持 1--7 天任务与现有对照，明确预警事件和阈值，再评价误报、漏报及提前量。}
\vfill
{\footnotesize\color{gray}仅精简展示，实验仍为已冻结的 v4.0。完整指标、四点全部七步长曲线及核验见配套结果记录。}
\end{document}
"""
    tex = tex.replace("@@SELECTION@@", "\n".join(rows)).replace(
        "@@MODELS@@", "\n".join(model_rows)
    )
    for name, caption in captions.items():
        tex = tex.replace(f"@@{name}@@", caption)
    assert "@@" not in tex
    SOURCE.write_text(tex, encoding="utf-8")
    figure_names = [
        "horizon_comparison", "paired_effects", "forecast_atu1_h7",
        "forecast_atu5_h7", "forecast_mj3_h7", "forecast_mj1_h7",
    ]
    inputs = [summary_path, selection_path, point_path]
    inputs += [FIGURES / f"{name}.pdf" for name in figure_names]
    inputs += [
        RUN / "selection.json", RUN / "internal_selection.json",
        ROOT / "paper/process_report.tex",
        ROOT / "paper/ootang_short_horizon_process_report.tex",
        ROOT / "output/pdf/ootang_short_horizon_comparison_report.v4.0.pdf",
    ]
    RECEIPT.write_text(
        json.dumps({
            "role": "presentation_revision_only",
            "experiment": "v4.0, unchanged",
            "presentation": "v4.1",
            "new_training": 0,
            "new_model_selection": 0,
            "expected_pages": 4,
            "displayed_numeric_cells": expected,
            "input_sha256": {str(p.relative_to(ROOT)): sha(p) for p in inputs},
            "tex_sha256": sha(SOURCE),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(SOURCE)


if __name__ == "__main__":
    main()
