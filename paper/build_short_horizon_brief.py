"""Present saved v4.0 and complete initial-state results; no model execution."""

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/ootang_short_horizon_v4/20260913_short_horizon"
INITIAL_STATE = ROOT / "results/ootang_neural_initial_state_v1_1/20260914"
FIGURES = ROOT / "paper/figures/short_horizon_zh"
SOURCE = ROOT / "paper/ootang_short_horizon_brief.v4.1.tex"
RECEIPT = ROOT / "paper/ootang_short_horizon_brief.v4.1.sources.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    initial_receipt_path = INITIAL_STATE / "verification/receipt.json"
    initial_report_path = ROOT / "docs/ootang_neural_initial_state_results.v1.1.md"
    initial_dev_path = INITIAL_STATE / "development/summary_by_horizon.csv"
    initial_later_path = INITIAL_STATE / "later_exploratory/summary_by_horizon.csv"
    initial_decision_path = INITIAL_STATE / "development/decision.json"
    initial_later_decision_path = INITIAL_STATE / "later_exploratory/decision.json"
    initial = json.loads(initial_receipt_path.read_text())
    assert initial["status"] == "passed" and initial["physics_pass"]
    assert initial["counts"]["trajectories"] == 4707
    assert initial["selected_steps"] == 200 and initial["old_stop_preserved"]
    assert initial["strict_diagnostic_failures"] == 3
    assert not json.loads(initial_decision_path.read_text())["passed"]
    assert not json.loads(initial_later_decision_path.read_text())["passed"]
    initial_dev = pd.read_csv(initial_dev_path).set_index(["model", "horizon"])
    initial_later = pd.read_csv(initial_later_path).set_index(["model", "horizon"])
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
        "CL_DIRECT": "ConvLSTM 直接预测",
        "CL_BRES": "ConvLSTM 残差学习",
        "PINN_EQ": "软约束状态 PINN",
        "PINN_NOEQ": "PINN 无方程约束对照",
        "NIS_BPLUS": "神经初态估计与 B+ 严格递推",
        "RR_DIRECT": "岭回归直接预测",
        "RR_BRES": "岭回归残差学习",
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
        if name == "NIS_BPLUS":
            d, a = initial_dev.loc[(name, 7)], initial_later.loc[(name, 7)]
        else:
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
@@BPLUS_FONT@@
\definecolor{reportblue}{HTML}{365F91}
\definecolor{lightblue}{HTML}{EEF3F8}
\hypersetup{colorlinks=true,linkcolor=reportblue,urlcolor=reportblue}
\graphicspath{{figures/short_horizon_zh/}}
\captionsetup{font=footnotesize,labelfont=bf,labelsep=quad,skip=2pt,hypcap=false}
\setlength{\parindent}{0pt}\setlength{\parskip}{4pt}
\setlength{\headheight}{14pt}\setlength{\footskip}{10mm}
\renewcommand{\arraystretch}{1.13}
\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0pt}
\fancyfoot[C]{\small\thepage\ / 4}
\newcommand{\pagetitle}[1]{{\Large\bfseries\color{reportblue}#1}\par\vspace{1mm}}
\newcommand{\takeaway}[1]{\colorbox{lightblue}{\parbox{\dimexpr\textwidth-2\fboxsep}{\small #1}}\par}
\newcommand{\reportfigure}[2]{\begin{minipage}{\textwidth}\includegraphics[width=\linewidth]{#1}\captionof{figure}{#2}\end{minipage}\par}
\begin{document}
\pagetitle{1\quad 七个步长，分别比较}
\takeaway{\textbf{在线回归＋短期误差反馈表现最好。}神经初态模型的预测整体收益仍未达标；B+ 物理参照的额外收益尚不稳定。}
{\small 每天使用过去 30 天观测，预测第 1、2、3、4、5、6、7 天后的位移；已发出的预测固定保存。四点：ATU1、ATU5、MJ3、MJ1。}\par
\reportfigure{horizon_comparison.pdf}{同一步长、同组起点；神经初态模型为固定候选，其余各类代表按开发 RMSE 选择并沿用至后期。纵轴为对数，越低越好。}
\textbf{兼顾均值与区间的推荐方法}\quad{\footnotesize 下表均为后期结果；误差单位 mm。}
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
\begingroup\setlength{\parskip}{2pt}
{\small 第 7 天位移比较：观测截止、成熟误差校准相同；未来真实驱动 B+ 不参加主比较。}\par
\begin{center}\fontsize{8.7}{11}\selectfont\setlength{\tabcolsep}{3pt}
\begin{tabular}{lrrrrrr}\toprule
模型 & 开发 RMSE & 后期 RMSE & CRPS & 90\% 覆盖 & 区间宽度 & 区间评分\\\midrule
@@MODELS@@
\bottomrule\end{tabular}
\end{center}
{\footnotesize 除“开发 RMSE”外均为后期结果，误差／宽度／评分单位 mm。覆盖率接近目标且区间评分低更好；神经结果先合并三种子均值再评分。}\par
\reportfigure{paired_effects.pdf}{配对方法的平均 RMSE 差：负值表示前者更好。四面板纵轴尺度不同。}
\takeaway{\textbf{神经初态模型采用 B+ 严格递推，满足求解器数值容差；预测整体收益仍未达标。}}
\begingroup\fontsize{9}{12}\selectfont
\textbf{软约束状态 PINN：物理一致性未达标。}ConvLSTM 直接预测与残差学习均未超过速度外推。在线回归含趋势、在线更新与误差反馈；神经与普通岭回归在阶段内固定权重。\par
\textbf{神经初态估计与 B+ 严格递推：}开发整体未改善；后期 1--7 天平均误差下降，3--7 天达到预定概率改善条件，但逐点均值条件未满足，仍落后在线回归。后期按完整比较补齐，未重选。\par
{\color{gray}B+ 采用最近七日平均降雨和最新库水位保持；概率层统一使用最近 90 条成熟预测误差。PINN 无方程约束对照仍保留相同物理背景。\par}
\endgroup
\endgroup
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
{\footnotesize\color{gray}完整指标、四点全部七步长曲线及核验见配套结果记录。}
\end{document}
"""
    tex = tex.replace("@@SELECTION@@", "\n".join(rows)).replace(
        "@@MODELS@@", "\n".join(model_rows)
    )
    for name, caption in captions.items():
        tex = tex.replace(f"@@{name}@@", caption)
    # Keep the model suffix an upright, unbreakable Latin token in the same
    # font as the saved Matplotlib legends; Chinese composition uses words.
    tex = tex.replace("B+", r"\Bplus{}")
    tex = tex.replace("@@BPLUS_FONT@@", r"""\newfontfamily\bplusfont{Arial Unicode MS}
\newcommand{\Bplus}{\mbox{{\bplusfont\mdseries\upshape B+}}}""")
    assert "@@" not in tex
    SOURCE.write_text(tex, encoding="utf-8")
    figure_names = [
        "horizon_comparison", "paired_effects", "forecast_atu1_h7",
        "forecast_atu5_h7", "forecast_mj3_h7", "forecast_mj1_h7",
    ]
    inputs = [summary_path, selection_path, point_path, FIGURES / "sources.json"]
    inputs += [FIGURES / f"{name}.pdf" for name in figure_names]
    inputs += [
        RUN / "selection.json", RUN / "internal_selection.json",
        ROOT / "paper/process_report.tex",
        ROOT / "paper/ootang_short_horizon_process_report.tex",
        ROOT / "output/pdf/ootang_short_horizon_comparison_report.v4.0.pdf",
        initial_report_path, initial_receipt_path, initial_dev_path, initial_later_path,
        initial_decision_path, initial_later_decision_path,
    ]
    RECEIPT.write_text(
        json.dumps({
            "role": "presentation_revision_only",
            "experiment": "v4.0 unchanged; verified complete initial-state comparison on pages 1 and 2",
            "presentation": "v4.1",
            "new_training": 0,
            "new_model_selection": 0,
            "new_prediction_or_scoring": 0,
            "figure_language": "Chinese labels, axes, legends and panel titles; saved data unchanged",
            "report_focus": "prediction results and concise physical consistency conclusion; diagnostic counts and tolerances remain in technical records",
            "bplus_typography": {
                "model_name": "B+",
                "font": "Arial Unicode MS",
                "coupled_model_name": "神经初态估计与 B+ 严格递推",
                "model_token": "upright, regular, unbreakable ASCII B+",
            },
            "expected_pages": 4,
            "displayed_numeric_cells": expected,
            "initial_state_result": {
                "pages": [1, 2],
                "all_evaluated_trajectories": initial["counts"]["trajectories"],
                "numerical_physics_pass": True,
                "strict_zero_control_diagnostic_failures": initial["strict_diagnostic_failures"],
                "all_seven_horizons_and_both_phases_evaluated": True,
                "development_joint_pass": False,
                "later_joint_pass": False,
                "later_execution": "explicit user amendment after development failure; exploratory",
                "forecast_effectiveness": "later aggregate improvement over B+, no stable overall benefit; weaker than online regression",
            },
            "input_sha256": {str(p.relative_to(ROOT)): sha(p) for p in inputs},
            "tex_sha256": sha(SOURCE),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(SOURCE)


if __name__ == "__main__":
    main()
