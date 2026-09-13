"""Generate a concise Chinese TeX brief and a source-linked results document."""

import argparse
import json
import pandas as pd

from .common import ROOT, load_spec, save_json, sha, now


def table(frame, columns, labels, digits=6):
    lines = [
        "| " + " | ".join(labels) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, r in frame.iterrows():
        lines.append(
            "| "
            + " | ".join(
                f"{r[c]:.{digits}f}" if isinstance(r[c], float) else str(r[c])
                for c in columns
            )
            + " |"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    analysis = root / "analysis"
    if not json.loads(
        (root / "verification/decision_and_figure_receipt.json").read_text()
    )["passed"]:
        raise ValueError("Decision verification incomplete")
    sel = pd.read_csv(analysis / "selection_by_horizon.csv")
    summary = pd.read_csv(analysis / "summary_by_horizon.csv")
    point = pd.read_csv(analysis / "metrics_by_point_horizon.csv")
    dev = summary[summary.phase == "development"].set_index(["model", "horizon"])
    later = summary[summary.phase == "later_exploratory"].set_index(
        ["model", "horizon"]
    )
    ids = [
        "B_ANCHOR",
        "DRIFT1",
        "CL_DIRECT",
        "CL_BRES",
        "PINN_EQ",
        "RR_DIRECT",
        "RR_BRES",
        "C16_CORE_RULES",
        "C16_PHYS_RULES",
    ]
    display = {
        "B_ANCHOR": "锚定 B+",
        "DRIFT1": "当天速度外推",
        "CL_DIRECT": "ConvLSTM 直接版",
        "CL_BRES": "ConvLSTM 残差版",
        "PINN_EQ": "PINN 方程约束版",
        "PINN_NOEQ": "PINN 去方程配对",
        "RR_DIRECT": "岭回归直接版",
        "RR_BRES": "岭回归残差版",
        "C16_CORE_RULES": "在线回归＋反馈",
        "C16_PHYS_RULES": "在线回归＋反馈＋物理误差",
    }
    texrows = []
    selection_rows = []
    h7rows = []
    for _, r in sel.iterrows():
        label = "岭回归" if r.recommended == "RR_DIRECT" else "回归＋反馈"
        texrows.append(
            f"{r.horizon} & {label} & {r.development_recommended_rmse:.6f} & {r.later_exploratory_recommended_rmse:.6f} & {r.later_exploratory_recommended_crps:.6f} & {100 * r.later_exploratory_recommended_coverage90:.2f}\\% \\\\"
        )
        selection_rows.append(
            dict(
                horizon=r.horizon,
                model=r.recommended,
                development_rmse=r.development_recommended_rmse,
                later_rmse=r.later_exploratory_recommended_rmse,
                later_crps=r.later_exploratory_recommended_crps,
                coverage90_pct=100 * r.later_exploratory_recommended_coverage90,
                later_pass=r.later_pass,
            )
        )
    modelrows = []
    for n in ids:
        d, after = dev.loc[(n, 7)], later.loc[(n, 7)]
        modelrows.append(
            f"{display[n]} & {d.rmse:.4f} & {after.rmse:.4f} & {after.crps:.4f} \\\\"
        )
        h7rows.append(
            dict(
                model=n,
                development_rmse=d.rmse,
                later_rmse=after.rmse,
                later_crps=after.crps,
                coverage90_pct=100 * after.coverage90,
                width90=after.width90,
                interval_score90=after.interval_score90,
            )
        )
    perpoint = point[
        (point.phase == "later_exploratory")
        & (point.model == "C16_CORE_RULES")
        & (point.horizon == 7)
    ].set_index("point")

    def panel(p):
        row = perpoint.loc[p]
        return (
            r"\includegraphics[width=\textwidth]{forecast_"
            + p.lower()
            + r"_h7.pdf}"
            + "\n"
            + r"\captionof{figure}{"
            + f"{p}：在线回归＋反馈，7 天后期 RMSE {row.rmse:.4f} mm，90\\% 区间覆盖 {100 * row.coverage90:.2f}\\%。"
            + r"}"
            + "\n"
        )

    coverage_rows = []
    for n in ["B_ANCHOR", "DRIFT1", "RR_DIRECT", "C16_CORE_RULES", "C16_PHYS_RULES"]:
        r = later.loc[(n, 7)]
        coverage_rows.append(
            f"{display[n]} & {100 * r.coverage90:.2f}\\% & {r.width90:.4f} & {r.interval_score90:.4f} \\\\"
        )
    tex = r"""\documentclass[UTF8,zihao=-4]{ctexart}
\usepackage[a4paper,top=18mm,bottom=18mm,left=22mm,right=22mm]{geometry}
\usepackage{graphicx,xcolor,booktabs,caption,amsmath,hyperref,fancyhdr}
\xeCJKsetup{PunctStyle=plain}
\definecolor{reportblue}{HTML}{365F91}
\definecolor{lightblue}{HTML}{EEF3F8}
\hypersetup{colorlinks=true,linkcolor=reportblue,urlcolor=reportblue}
\graphicspath{{../figures/ootang_short_horizon_v4/20260913_short_horizon/}}
\captionsetup{font=footnotesize,labelfont=bf,labelsep=quad,skip=3pt,hypcap=false}
\setlength{\parindent}{0pt}\setlength{\parskip}{5pt}
\setlength{\headheight}{14pt}\setlength{\footskip}{11mm}
\renewcommand{\arraystretch}{1.16}
\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0.3pt}
\fancyhead[L]{\small\color{reportblue}藕塘滑坡｜1--7 天位移预测对比}
\fancyhead[R]{\small v4.0 · 2026-09-13}\fancyfoot[C]{\small\thepage\ / 5}
\newcommand{\pagetitle}[1]{\vspace*{1mm}{\Large\bfseries\color{reportblue}#1}\par\vspace{2mm}}
\begin{document}
\pagetitle{1\quad 做了什么，七个步长分别选谁}
\colorbox{lightblue}{\parbox{\dimexpr\textwidth-2\fboxsep}{\textbf{结果：}在当前公开日序列上，开发锁定的组合在后期的 1--7 天均通过本轮工作条件，最远为 7 天。在线回归与误差反馈最值得保留；物理参照的额外收益尚未确立。}}

每天读入截至当天的历史，分别预测第 1、2、3、4、5、6、7 天后的位移。
一个起点的七步预测发出后保持不变；下一天的新观测只服务新预测与已到期误差更新。
比较 B+、ConvLSTM、离散状态 PINN、岭回归／在线反馈，并固定直接版与残差版配对。

\includegraphics[width=\textwidth]{workflow.pdf}
\captionof{figure}{所有模型遵守同一观测截止。未来驱动采用当时可计算的简单场景，概率层统一使用最近 90 条成熟预测误差。}

\textbf{开发段先选身份，后期保留身份。}下表为同时满足均值与区间条件的组合；误差单位 mm，RMSE 为四点各自 RMSE 的平均。
\begin{center}\footnotesize
\begin{tabular}{clrrrr}\toprule
步长 & 锁定组合 & 开发 RMSE & 后期 RMSE & 后期 CRPS & 90\% 覆盖\\\midrule
@@SELECTION@@
\bottomrule\end{tabular}
\end{center}

\textbf{“最小误差”和“共同达标”分开判断。}在线回归＋反馈在七个步长的开发 RMSE、CRPS 均最低；1 天开发覆盖率 95.81\%，略高于事前 95\% 上限，因此共同推荐按原规则选择岭回归。均值最小组合为反馈回归的 1 天预测，后期 RMSE 为 0.000209 mm。

\small 开发目标期：2018-09-01--2019-09-11；后期：2019-09-12--2020-06-30。
每点各 h 保留全部合法起点，后期样本量依次为 293、292、291、290、289、288、287。
这些误差针对公开的已处理日序列，不能直接解释成现场传感器精度或真实失稳预警精度。

\clearpage
\pagetitle{2\quad 四类模型在同一短期任务下怎样比较}
\includegraphics[width=\textwidth]{horizon_comparison.pdf}
\captionof{figure}{各家族代表按开发段同 h 的最低 RMSE 固定，再报告后期；因此代表图与第 1 页“共同推荐”的身份可能不同。纵轴为对数；点表示已发预测的整体评分，不是种子评分的平均。神经均值先作三种子等权合并。}

\textbf{固定看最长步长 7 天。}下表同时保留直接版、残差版和强对照，不按后期成绩挑选一条更好的曲线。
\begin{center}\small
\begin{tabular}{lrrr}\toprule
模型 & 开发 RMSE & 后期 RMSE & 后期 CRPS\\\midrule
@@MODELS@@
\bottomrule\end{tabular}
\end{center}

\small ConvLSTM 两版都优于本轮 B+，但仍未超过当天速度外推。
PINN 完成训练，但其神经状态未通过独立物理重放检查，表中仍保留原神经输出；本结论限于这次固定结构。
在线回归方案还使用多尺度趋势、在线更新及误差反馈，不能把全部优势归因于“线性模型优于深度学习”。

\clearpage
\pagetitle{3\quad ATU1、ATU5：完整后期的 7 天预测}
\small 每点依次为累计位移、7 日总增量、实测减预测均值。蓝带为相对均值的 90\% 边际预测区间；误差落在蓝带内即被覆盖。累计曲线重合仍需由增量和误差图核对。
\normalsize\par\vspace{2mm}
@@ATU1@@
\vspace{4mm}
@@ATU5@@
\small 两点覆盖率较低，ATU1 仅略高于本轮逐点 80\% 下界。困难尾段与误差尖峰全部保留，尚不能把“过工作条件”理解为已充分覆盖所有异常变化。

\clearpage
\pagetitle{4\quad MJ3、MJ1：同样保留全部后期日期}
\small 图中每条点日预测都在目标日前 7 天发出，之后未被新预测覆盖；两点均为 287 个合法起点。不同面板纵轴尺度分别标注，增量单位为 mm，未转换为单日速度。
\normalsize\par\vspace{2mm}
@@MJ3@@
\vspace{4mm}
@@MJ1@@
\small 当前最优均值属于监督回归与成熟误差反馈。B+ 在这里提供对照和可选物理误差参照；这组小误差主要来自近期位移信息，尚无稳定的额外物理增益证据。

\clearpage
\pagetitle{5\quad 残差和物理信息有没有额外收益}
\includegraphics[width=\textwidth]{paired_effects.pdf}
\captionof{figure}{固定配对的四点平均 RMSE 差值，负值表示前一版本更好。四个面板纵轴尺度不同：残差输出、方程约束与物理误差参照是不同问题，不能混为同一种增益。}

\begin{center}\footnotesize
\begin{tabular}{lrrr}\toprule
7 天后期的概率表现 & 90\% 覆盖 & 宽度／mm & 区间评分／mm\\\midrule
@@COVERAGE@@
\bottomrule\end{tabular}
\end{center}

\small
\textbf{这次的取舍。}ConvLSTM 残差版在开发段更差，在后期部分步长稍好；岭回归残差版未获得稳定增益。加入 B+ 一日误差后的反馈方案总体略退步。PINN 集成均值与独立 C 重放最大相差约 1.2763 mm，高于 0.01 mm 上限，停止这版结构。

\textbf{值得保留什么。}保留在线趋势回归＋短期误差反馈及共同成熟误差校准，固定 1--7 天任务和完整对照。
7 天反馈回归相对速度外推的 RMSE 差为 $-0.2530$ mm，30 日成块重采样的描述性 95\% 区间为 $[-0.3762,-0.0932]$ mm；不据此宣称新独立泛化证据。

\textbf{下一步。}先把这套短期方法写清楚，再单独确定预警对象、事件标签和阈值，评价误报、漏报与提前量；本轮没有验证真实失稳预警。
公开四条日序列具有强月内三次数值结构，原始日值当时是否可得仍未知，且评价期已被多轮探索使用。它们限制外推主张，也不自动否定本次保存的预测改善。

\vfill
{\footnotesize\color{gray}核验：36 次固定神经拟合、7800 次更新；427 项哈希、14508 个分布锁和 61152 个评分单元通过复算。完整七步长指标、配对统计、三种子结果、原始模型及 31 幅图件随版本保存。}
\end{document}
"""
    replacements = {
        "SELECTION": "\n".join(texrows),
        "MODELS": "\n".join(modelrows),
        "COVERAGE": "\n".join(coverage_rows),
    }
    replacements.update({p: panel(p) for p in spec["points"]})
    for k, v in replacements.items():
        tex = tex.replace("@@" + k + "@@", v)
    source = ROOT / "paper/ootang_short_horizon_process_report.tex"
    source.write_text(tex)
    selected = pd.DataFrame(selection_rows)
    h7 = pd.DataFrame(h7rows)
    result = """# 藕塘 1—7 天位移概率预测对比结果 v4.0

本轮四类比较、固定实验、独立核验及五页图文简报已完成。全部七步长、四点完整曲线及原始正负结果保留，当前不再追加实验。

**结论：在当前公开日序列上，开发锁定的组合在后期的 1—7 天均达到本轮工作条件，最远支持到 7 天。** 在线趋势回归＋成熟误差反馈最值得保留。物理输入的稳定增量没有建立；PINN 完成训练但物理状态验收失败。这里的工作条件不是实际预警验收或新独立泛化证明。

## 1. 任务、数据与比较条件

每天收到新观测后，分别预测第 1、2、3、4、5、6、7 天后的四点位移，一个起点的输出不被后来的观测覆盖。历史输入 30 天；公开监测表、ATU1/ATU5/MJ3/MJ1 四点和完整日期保持。

内部 612/180 日、开发 792/376 日、后期 1168/293 日前缀与窗口分别使用；每个 h 只要求该目标在阶段内，后期样本数依次 293 至 287。拟合查询分别为 354/534/910 条，所有七个目标均在拟合前缀内，教师前缀不晚于起点。标准化仅用训练查询；内部选择采用完整七步长共同起点，开发按 h 锁定身份，后期不改选。

主未来驱动为最近七日平均降雨、最新库水位保持；水文和完整力学记忆从历史重放。已知未来真实驱动的 B_ORACLE 只作副表；其误差不保证更低。原物理 54 参数不重新标定。B_RAW 为未锚定说明对照，不参加主选择。

所有均值采用相同 90 条成熟预测误差 RMS 的经验高斯分布，概率 ID 为均值 ID 加 `_G`。阶段初值优先使用前阶段发出的误差，再以过去的 DRIFT1 补足；不是训练拟合误差。该尺度不是测量噪声、物理不确定性分解或严格贝叶斯后验。历史原生 C16/C18 区间另列，不参加本轮主排名。

来源：[冻结计划](ootang_short_horizon_comparison_plan.v4.0.md)、[执行合同](ootang_short_horizon_execution.v4.0.md)、[冻结配置](../config/ootang_short_horizon_comparison.v4_0.json)。原数据 SHA-256 为 `ee63480ad9b8065dea359d49873182b1554013f910bec1c6988c0b152bede118`。

### 实现和可学习参数

B+ 继续使用原 54 参数及 64 子步／日，只改变起点锚定与可用驱动场景。ConvLSTM 读取 30×25×4 历史和 7×8×4 场景，隐藏通道 16，每个种子 8353 个可学习参数。PINN 对相同历史作三段摘要，32 宽 MLP 输出初始 z 修正及 7×20 状态修正，每个种子 13560 参数；固定物理背景及参数不随梯度优化。RR 两臂均为 16 个标准化特征加截距、四点七步各一组系数，共 476 系数。

在线核心以 D1 为基础，学习 D1−D3、D1−D7、D7−D14、D14−D30 四个多尺度位移外推差的组合。七步长共有 112 个核心系数；CORE 一日误差头另有 28 个系数，PHYS 一日误差头为 56 个。核心和误差头按冻结规则仅用到期监督在线更新；神经和 RR 权重在阶段内固定，只更新观测输入和共同误差池。已有核心模型、标准化和训练暴露沿用来源记录，不称全新内部验证。

四神经臂每臂 9 次拟合（3 阶段×3 种子），累计实际优化循环用时分别为 CL_DIRECT 47.460 秒、CL_BRES 35.652 秒、PINN_EQ 17.941 秒、PINN_NOEQ 1.968 秒；不含数据准备、加载、物理审计或写盘。Ridge 两臂各 7 次拟合（5 个内部 alpha＋开发＋后期），共 392 次小矩阵求解。成本是本机本次记录，不作为硬件无关的速度基准。

## 2. 七步长的锁定组合与后期表现

下表 RMSE 是四点 RMSE 的平均，单位 mm；覆盖为百分比。均值最小与概率评分最小的开发身份均为 C16_CORE_RULES，以下另按全部工作条件列共同推荐。

@@SELECTION_TABLE@@

1 天反馈回归的开发覆盖率 95.8112% 高于预定平均 95% 上限，因此共同推荐为 RR_DIRECT；它并不是均值冠军。均值最小组合是 C16_CORE_RULES、h=1，开发／后期 RMSE 为 0.0000834243／0.0002087453 mm。2—7 天推荐均为 C16_CORE_RULES。七个开发锁定组合在后期均通过原有条件，没有放宽门槛。

## 3. 四类模型与残差配对：7 天完整对照

@@H7_TABLE@@

ConvLSTM 专用七步长训练的直接版开发表现优于残差版，后期残差版稍好；按开发冻结的神经代表仍是直接版。两版均落后于 DRIFT1，不能把只超过 B+ 当成充分增量。RR_DIRECT 的后期 RMSE 为 0.037318 mm，残差版 0.037599 mm，B+ 残差输出组织没有带来稳定收益。

C16_CORE_RULES 复用冻结的 C8 数据核心与 C16 成熟一日误差反馈规则；新回放均值与原 C16_CORE 在 1e-8 mm 内一致。PHYS 两臂使用本轮可用驱动场景真正发出的 B+ 一日误差重新计算，不能搬用旧已知未来驱动的误差。其 7 天开发／后期均略差于 CORE。C16 包含多尺度趋势、在线核心更新与成熟误差反馈，优势不能全归因为回归模型类别，更不是证明物理模型外推产生了这些小误差。

PINN 为本轮离散状态多重射击近似，使用完整 B+ 初始记忆、可学习起点 z 校正以及逐日原 C 方程缺陷；不冒称完整连续二维 PDE PINN。三种子和集成均值均未达到物理验收：后期集成最大独立重放差 1.276258 mm、最大归一化缺陷 0.387848、最小塑性增量 −0.025143 mm，分别违反原先写定的上限与单调要求。神经主输出未被 C 重放替换。NOEQ 保留相同背景和结构，只去方程／塑性损失；该配对不等于去掉全部物理信息。停止这版 PINN，不据此否定整个 PINN 家族。

## 4. 概率质量与四点差异

7 天 CORE 后期 CRPS 为 0.0099777355 mm、90% 覆盖 85.8885%、宽度 0.0415038110 mm、区间评分 0.1116162426 mm。ATU1/ATU5/MJ3/MJ1 覆盖分别 80.1394%/81.8815%/89.8955%/91.6376%。ATU1 接近逐点下界，不能称概率问题已全部解决。

四点 CORE 后期 RMSE 平均为 0.0255796349 mm，合并 RMSE 为 0.0314496385 mm，两个量不混名。本轮的共同成熟误差校准与旧原生区间不同；均值继承已有方案而区间重建，不能把均值收益记作新校准贡献。

## 5. 固定统计与核验

后期锁定组合相对 B+、DRIFT1 的 RMSE／CRPS 配对差，按起点四点同步、30 日非循环移动块、2000 次、种子 20260913 重采样。32 行结果均保存；另有每七日一个起点的敏感性。7 天 CORE−DRIFT1 的 RMSE 差 −0.253008 mm，描述性 95% 区间 [−0.376213,−0.093217]；CRPS 差 −0.116039，区间 [−0.202258,−0.049297]。未作多重比较显著性判定，不用于后期选模，也不消除旧的选择暴露。

11 项相关检查和 lint 通过；独立核对 427 项哈希、14508 个发出分布锁、61152 个评分单元，尺度最大差 0、评分差最大 5.12e-13 mm。已选神经模型全部三种子重载，Ridge 使用独立增广最小二乘验证，C16 系数独立累积重算；3528 项门槛、1008 个拟合评分单元和 8120 行曲线数据另行核对。20 个固定边界起点的原 C 重放、日递推和未来驱动扰动通过；物理状态最大差 3.18e-12。当前验证不证明原始发布序列的真实 as-of。

报告另完成 70 项数字核对及 128 项配对统计／敏感性复算，独立块前缀和算法与原结果最大差 3.77e-15 mm。31 张科学图的字号与碰撞检查通过；30 张多面板图的对齐检查通过，单面板流程图的面板对齐检查不适用。四点七步长的全部面板和五页最终 PDF 已目视核对。整页 TeX 的通用字体跨度检测会误并不相邻的表格单元，原失败记录保留，最终另用实际逐字包围盒验证，五页均无文字碰撞或裁切。详见图件 QA 说明和报告回执。

## 6. 预算、执行与失败记录

总执行窗口 2026-09-13 13:37:17—17:37:17 UTC，含准备、实现、核验和报告。正式训练已于 14:24:21 UTC 完成：内部 12 次 × 400，开发／后期各 12 次，共 36 次固定拟合、7800 次更新。CL_DIRECT 取内部 200 次，其他神经臂取 100 次；RR 两臂共同 α=0.001。没有额外重试、标定、后期调参或模型搜索。

初次检查入口因未安装 pytest 退出，转用项目已有 unittest；未安装依赖。原 macOS 矩阵运算警告保留，输出有限且独立求和／日递推通过，不声称底层库已修复。初版 ATU1 的 h=2 图中轴标题与刻度轻微碰撞，保留初版 QA 和图件后修正标签换行，未改数据。PINN 的物理验收失败是本轮结果，不通过图表修复或换 C 主输出遮盖。

## 7. 判断与下一步

保留“公开日序列上的在线趋势回归＋短期成熟误差反馈”作为当前最有证据的路线。固定七步长任务、强对照、共同概率层和物理消融。研究主张对应为：滚动监督预测有收益、物理误差增量未确立、经验区间有可评分的改进；不要合并成“物理约束全面成功”。

下一步先整理该方法的完整算法与适用条件，预警任务另外固定事件真值、阈值和误报／漏报／提前量。没有这些定义，不能把低位移 RMSE 或七个边际区间称为“未来七日失稳风险已验证”。

公开位移序列的月内三次数值结构、原始日值实际可用时间未知、评价期反复暴露的限制保持。数据固定，不将补采或更换案例设为本轮前置条件；也不据规则性直接断言数据造假、确定泄漏或全部改善无效。原 293 日固定起点与 30 日滚动负结果不改判。

## 8. 交付与复现

- [五页简报源码](../paper/ootang_short_horizon_process_report.tex)；[PDF](../output/pdf/ootang_short_horizon_comparison_report.v4.0.pdf)。
- [完整汇总](../results/ootang_short_horizon_v4/20260913_short_horizon/analysis/summary_by_horizon.csv)、[逐点指标](../results/ootang_short_horizon_v4/20260913_short_horizon/analysis/metrics_by_point_horizon.csv)、[七步长选择](../results/ootang_short_horizon_v4/20260913_short_horizon/analysis/selection_by_horizon.csv)。
- [配对差值](../results/ootang_short_horizon_v4/20260913_short_horizon/analysis/paired_differences.csv)、[成块区间](../results/ootang_short_horizon_v4/20260913_short_horizon/analysis/paired_block_bootstrap.csv)、[原生概率副表](../results/ootang_short_horizon_v4/20260913_short_horizon/analysis/legacy_native_probability.csv)。
- [模型／数组核验](../results/ootang_short_horizon_v4/20260913_short_horizon/verification/receipt.json)、[选择／图数据核验](../results/ootang_short_horizon_v4/20260913_short_horizon/verification/decision_and_figure_receipt.json)、[完整曲线数据](../figures/ootang_short_horizon_v4/20260913_short_horizon/curve_source_data.csv)。
- [报告数字与统计核验](../results/ootang_short_horizon_v4/20260913_short_horizon/verification/report_qa.json)、[图件与排版核验说明](../figures/ootang_short_horizon_v4/20260913_short_horizon/README.md)。

原始训练／预测已冻结，正常复查使用 `short_horizon.verify` 和 `verify_decisions` 的只读逻辑；运行入口拒绝覆盖已有阶段。配置截止后不自动重启原训练。各步骤本地提交，不自动 push，用户原有 Vajont 表未改。
"""
    result = result.replace(
        "@@SELECTION_TABLE@@",
        table(
            selected,
            list(selected.columns),
            [
                "h／天",
                "锁定模型",
                "开发 RMSE",
                "后期 RMSE",
                "后期 CRPS",
                "90%覆盖／%",
                "后期通过",
            ],
        ),
    )
    result = result.replace(
        "@@H7_TABLE@@",
        table(
            h7,
            list(h7.columns),
            [
                "模型",
                "开发 RMSE",
                "后期 RMSE",
                "后期 CRPS",
                "90%覆盖／%",
                "90%宽度",
                "90%区间评分",
            ],
        ),
    )
    (ROOT / "docs/ootang_short_horizon_comparison_results.v4.0.md").write_text(result)
    save_json(
        root / "analysis/report_values.json",
        dict(
            generated_utc=now(),
            selection=selection_rows,
            horizon7=h7rows,
            tex_sha256=sha(source),
            source_summary_sha256=sha(analysis / "summary_by_horizon.csv"),
        ),
    )
    print(str(source))


if __name__ == "__main__":
    main()
