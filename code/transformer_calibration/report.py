"""Generate a source-indexed Chinese comparison report from independently verified CSV."""

import pandas as pd

from .core import B, OLD, REG, HALF, ROOT, read_json, sha, spec, utc, write_json

DISPLAY = {
    B: "B+",
    "DRIFT1": "DRIFT1",
    "RR_COND": "普通岭回归",
    OLD: "Transformer 原版",
    REG: "Transformer REG1",
    HALF: "Transformer 半残差",
}
PHASES = ("development", "final_exploratory")


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    audit = root / "verification_v1"
    receipt = read_json(audit / "receipt.json")
    assert receipt["status"] == "passed"
    figs = ROOT / cfg["figures"]
    assert read_json(figs / "v1/delivery_qa.json")["status"] == "passed"
    assert read_json(figs / "v1/visual_qa.json")["status"] == "passed"
    out = root / "analysis"
    out.mkdir(exist_ok=True)
    for source in audit.glob("*.csv"):
        (out / source.name).write_bytes(source.read_bytes())
    summary = pd.read_csv(out / "phase_summary.csv").set_index(
        ["phase", "model", "rule"]
    )
    points = pd.read_csv(out / "metrics_by_point.csv").set_index(
        ["phase", "model", "rule", "point"]
    )
    pairs = pd.read_csv(out / "shrinkage_pairing.csv").set_index(["phase", "pair"])
    selection = read_json(root / "selection.json")
    tables = []
    cells = []

    def cell(file, filters, metric, precision=3, multiplier=1):
        table = pd.read_csv(out / file)
        row = table
        for k, v in filters.items():
            row = row[row[k] == v]
        assert len(row) == 1, (file, filters)
        value = float(row.iloc[0][metric]) * multiplier
        cells.append(
            dict(
                file=str((out / file).relative_to(ROOT)),
                filters=filters,
                metric=metric,
                precision=precision,
                multiplier=multiplier,
            )
        )
        return f"{value:.{precision}f}"

    def S(phase, model, rule, metric, precision=3, multiplier=1):
        return cell(
            "phase_summary.csv",
            dict(phase=phase, model=model, rule=rule),
            metric,
            precision,
            multiplier,
        )

    def P(model, point, metric, precision=3, multiplier=1):
        return cell(
            "metrics_by_point.csv",
            dict(phase="final_exploratory", model=model, rule="DIST90", point=point),
            metric,
            precision,
            multiplier,
        )

    def table(name, headers, rows):
        tables.append(dict(id=name, headers=headers, rows=rows))
        return (
            f"<!-- table:{name} -->\n| "
            + " | ".join(headers)
            + " |\n| "
            + " | ".join(["---"] * len(headers))
            + " |\n"
            + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows)
            + f"\n<!-- endtable:{name} -->"
        )

    mean_rows = []
    for m in cfg["methods"]:
        mean_rows.append(
            [DISPLAY[m]]
            + [S(ph, m, "LAST90", k) for ph in PHASES for k in ("mae", "rmse")]
        )
    mean_table = table(
        "mean_comparison",
        ["方法", "开发 MAE", "开发 RMSE", "最终 MAE", "最终 RMSE"],
        mean_rows,
    )
    probability = []
    for ph in PHASES:
        rows = []
        for m in cfg["methods"]:
            for r in cfg["rules"]:
                rows.append(
                    [
                        DISPLAY[m],
                        r,
                        S(ph, m, r, "crps"),
                        S(ph, m, r, "interval_score90"),
                        S(ph, m, r, "coverage90", 2, 100),
                        S(ph, m, r, "width90"),
                    ]
                )
        probability.append(
            table(
                "probability_" + ph,
                [
                    "均值方法",
                    "校准",
                    "CRPS",
                    "90% 区间评分",
                    "90% 覆盖 / %",
                    "90% 宽度",
                ],
                rows,
            )
        )
    point_rows = []
    for p in cfg["points"]:
        point_rows.append(
            [p]
            + [P(m, p, "rmse") for m in (B, REG, HALF)]
            + [P(m, p, "coverage90", 2, 100) for m in (B, REG, HALF)]
        )
    point_table = table(
        "point_boundary",
        [
            "测点",
            "B+ RMSE",
            "REG1 RMSE",
            "HALF RMSE",
            "B+ 覆盖 / %",
            "REG1 覆盖 / %",
            "HALF 覆盖 / %",
        ],
        point_rows,
    )
    seed_rows = []
    for ph in PHASES:
        for pair, label in (
            ("half_vs_original", "HALF 优于原版"),
            ("reg_vs_original", "REG1 优于原版"),
            ("reg_vs_half", "REG1 优于 HALF"),
        ):
            row = pairs.loc[(ph, pair)]
            seed_rows.append(
                [
                    "开发" if ph == "development" else "最终（探索）",
                    label,
                    str(int(row.seeds_both_mean_improve)) + "/3",
                    ", ".join(str(s) for s in cfg["seeds"] if bool(row[f"seed{s}"]))
                    or "无",
                ]
            )
    seed_table = table(
        "seed_pairing",
        ["阶段", "比较（MAE 与 RMSE 同时降低）", "种子数量", "种子编号"],
        seed_rows,
    )

    def value(ph, m, r, k):
        return float(summary.loc[(ph, m, r), k])

    fraction = {
        k: (
            value("development", OLD, "LAST90", k)
            - value("development", HALF, "LAST90", k)
        )
        / (
            value("development", OLD, "LAST90", k)
            - value("development", REG, "LAST90", k)
        )
        for k in ("mae", "rmse")
    }
    improvements = {
        m: {
            k: 1
            - value("final_exploratory", m, "DIST90", k)
            / value("final_exploratory", m, "LAST90", k)
            for k in ("crps", "interval_score90", "width90")
        }
        for m in (B, REG, HALF)
    }
    outcome = dict(
        time_utc=utc(),
        mean_selection=selection["mean_winner"],
        probability_selection=selection["probability_winner"],
        search_trigger=selection["search_recommendation"],
        new_search_fits=0,
        development_half_share_of_reg_average_improvement=fraction,
        final_distance_changes=improvements,
        final_reg_vs_half_within_one_percent=bool(
            pairs.loc[
                ("final_exploratory", "reg_vs_half"), "descriptively_within_one_percent"
            ]
        ),
        result_status="complete; no stable joint mean/probability success; final calibration gains exploratory",
        acceptance="not claimed",
    )
    write_json(out / "outcome.json", outcome)

    def link(path, label):
        return f"[{label}]({ROOT / path})"

    doc = f"""# Transformer 半残差与区间校准：完整对比报告 v1.0

**本轮已经完整跑通。半残差复现了大部分均值改善；按预测距离校准在最终段明显改善概率评分，但 Transformer 仍未建立跨阶段、四点一致的 B+ 优势。按冻结规则，不继续 λ 搜索，也未引入 RL。**

## Material Passport

- 类型：代码实验与结果解释；状态：EXECUTED / VERIFIED。核验通过与效果达标分别判断，未声称用户或导师验收。
- 数据与任务：同一藕塘 ATU1、ATU5、MJ3、MJ1；1461 日原序列；前 1168 日拟合，后 293 日完整独立条件预测。
- 信息条件：给定未来逐日降雨、库水位，不接收预测段实测位移；完整 B+ 状态延续。并非未知未来驱动的实时预报。
- 本次复用已保存的三种子模型/预测，新增训练、更新、物理拟合、物理前向均为 0。不是又训练了一版 Transformer。
- 计划 {link(cfg["plan"], "冻结计划")}；{link("config/ootang_transformer_calibration.v1_0.json", "配置")}；{link("docs/ootang_transformer_calibration_sources.v1.0.json", "1,012 项来源")}。
- 独立窗口 2026-09-14 15:41:07—17:41:07 UTC；实际交付用时见最终回执，不延续旧额度。本地分步提交，不 push、不制作 PDF。

## 1. 先验证：正则化是否主要相当于减弱残差

原版为 `B+ + 网络修正`；HALF 固定为 `B+ + 0.5 × 原版网络修正`，不重新训练、不搜索系数。REG1 是上一轮加入 λ=1 修正惩罚后重新训练的模型。三个种子逐一配对，主结果取等权均值。

下面均为四点等权平均，单位 mm；开发为 376 日，最终为完整 293 日。最终段多次暴露，只作探索性比较。

{mean_table}

开发段，HALF 已达到 REG1 相对原版 MAE 降幅的 **{fraction["mae"]:.2%}**、RMSE 降幅的 **{fraction["rmse"]:.2%}**。这是已观察到的降幅比值，不是因果贡献率。REG1 比 HALF 的开发 RMSE 只再降低 {abs(float(pairs.loc[("development", "reg_vs_half"), "rmse_relative_change"])):.2%}。

最终段，HALF 的 MAE/RMSE 均略低于 REG1；二者整体两项差异均在事前 1% 描述性范围内。不能据此宣称统计等效，但它削弱了“复杂正则化训练带来额外稳定外推能力”的解释。轨迹并非完全相同，{link(str((out / "correction_difference.csv").relative_to(ROOT)), "逐点轨迹差")}保留了这种差别。

{seed_table}

REG1 对 HALF 的配对优势从开发 3/3 变为最终 1/3。HALF 相对原版两阶段均为 3/3 改善，但 HAL​F 与 REG1 的开发均值均落后 B+。最终整体略优于 B+ 不能抵消开发失败与逐点退步。

与普通岭回归比较，REG1/HALF 在开发和最终的平均 MAE/RMSE 均更低；相同校准规则下，开发平均 CRPS/区间评分仍更高，最终两项则更低。与 DRIFT1 比较，两版均值在开发更差、最终更好，不能概括为跨阶段优于简单对照。

DRIFT1 是简单的**最后一天增量直线外推**：`y(t+h)=y(t)+h×[y(t)-y(t-1)]`。本协议从固定起点发出全部未来预测，不使用期间新位移，不是神经网络。普通岭回归则沿用相同合法固定特征。

## 2. 再验证：保持均值，改变历史误差的取法

- LAST90：原来的末尾 90 条成熟误差 RMS，每点常数区间尺度。
- DIST90：按当前预测距离寻找上一独立窗内最接近的 90 条误差，每点、每距离冻结尺度；同距取较短距离。
- DIST90_UNIT：同样匹配距离，再按历史/当前模型训练前缀的位移标准差单位换算。没有使用最终标签调比例。

内部前 90 日曾用于选训练次数，已排除。开发校准只剩内部后 90 日（原预测距离 91—180），所以 DIST90 **精确退化为 LAST90**；它在开发段没有新增距离辨识证据。最终校准可使用全部已兑现的 376 日开发预测误差。它们来自自己的较早训练前缀模型，不是最终模型的训练拟合残差。

下面保留全部 18 组，不只显示成功的组合。CRPS、区间评分和宽度单位为 mm；分数越低越好，覆盖率结合宽度一起判断。80%/95% 的完整数值也在 CSV 中。

### 开发段：376 日

{probability[0]}

训练单位换算未带来合格改善。例如 REG1 的 90% 覆盖仍只有 {value("development", REG, "DIST90_UNIT", "coverage90"):.2%}；不能因为区间变宽、覆盖略升就认定校准有效。

### 最终段：293 日，探索性评价

{probability[1]}

固定 REG1 均值后，DIST90 将 CRPS 从 {value("final_exploratory", REG, "LAST90", "crps"):.6f} 降至 {value("final_exploratory", REG, "DIST90", "crps"):.6f}，降幅 {improvements[REG]["crps"]:.2%}；90% 区间评分降幅 {improvements[REG]["interval_score90"]:.2%}，平均宽度从 {value("final_exploratory", REG, "LAST90", "width90"):.3f} 降至 {value("final_exploratory", REG, "DIST90", "width90"):.3f} mm，覆盖仍为 {value("final_exploratory", REG, "DIST90", "coverage90"):.2%}。REG1/HALF 的最终新校准相对自身 LAST90 通过概率工作条件，均值完全未变。

**同样的校准也改善 B+。** 采用 DIST90 后，B+ 的 CRPS 为 {value("final_exploratory", B, "DIST90", "crps"):.6f}，仍低于 REG1 的 {value("final_exploratory", REG, "DIST90", "crps"):.6f} 和 HALF 的 {value("final_exploratory", HALF, "DIST90", "crps"):.6f}；区间评分同样更低。只能说这里观察到校准规则的收益，不能把它归为 Transformer 特有优势。

## 3. 四点保护与选择结果

以下覆盖使用同一个 DIST90 规则，RMSE 与校准无关：

{point_table}

REG1 的 MJ3 RMSE 仍高于 B+；HALF 的 MJ3 同样退步，且 MJ1 MAE 为 {float(points.loc[("final_exploratory", HALF, "DIST90", "MJ1"), "mae"]):.3f} mm，高于 B+ 的 {float(points.loc[("final_exploratory", B, "DIST90", "MJ1"), "mae"]):.3f} mm，不能只看 RMSE。另一方面，B+ DIST90 虽然平均概率分数更好，MJ3 的 90% 覆盖只有 {float(points.loc[("final_exploratory", B, "DIST90", "MJ3"), "coverage90"]):.2%}，未过逐点 80% 保护。它也不能直接被宣布为已经可靠的新概率基线。

开发锁定均值优胜者为 **{selection["mean_winner"]}**，概率优胜者为 **{selection["probability_winner"]}**。最终没有按成绩改名单。所有 Transformer 组合在两阶段对同规则 B+ 的完整联合条件均未通过；相对原 LAST90 B+ 的最终概率局部改善保留，完整联合条件仍未过。

λ 搜索触发条件在开发阶段已失败：REG1 相对 HALF 的 RMSE 降幅不足 1%、MJ1 退步、且未通过 B+ 均值门。不是看见最终结果后决定停。{link(str((root / "selection.json").relative_to(ROOT)), "开发锁定及逐项条件")}保存了全部真假值。

## 4. 给导师看的图

纵轴参照导师图，保留横纵网格、去掉三角标记；黑线实测、灰虚线 B+、蓝线候选。图中 80%/95% 是逐日边际预测区间，不是三种子均值的置信区间，也不保证整条轨迹同时覆盖。参考纵轴以外的区间仅裁切显示，另给未裁切完整范围图；没有缩窄原始区间。

{link(str((figs / "v1/REG1_LAST90_mentor.png").relative_to(ROOT)), "REG1 原区间")} · {link(str((figs / "v1/REG1_DIST90_mentor.png").relative_to(ROOT)), "REG1 距离匹配区间")} · {link(str((figs / "v1/HALF_DIST90_mentor.png").relative_to(ROOT)), "HALF 距离匹配区间")} · {link(str((figs / "v1/all_methods_forecast.png").relative_to(ROOT)), "六方法完整最终曲线")}。

![REG1 距离匹配区间]({figs / "v1/REG1_DIST90_mentor.png"})

{link(str((figs / "README.md").relative_to(ROOT)), "全部七张图及可编辑 SVG")}包含三个完整范围副本。四点、完整日期、三种子与负结果均保留。

## 5. 现在的问题与下一步判断

据本轮证据，主要未解决的是**残差在不同时间段的外推稳定性，以及历史误差分布能否迁移到下一次预测**。本轮没有证据表明需要更大的网络或更复杂的参数搜索器。

本轮结束，不追加 λ 搜索或 RL。若继续研究，先形成下一版跨时间起点验证方案：每个起点均用此前数据完成同一训练流程，分别留出模型选择和概率校准窗口，预先规定长距离支持不足的处理，并让 B+ 与神经模型使用相同校准规则。重点是取得可比较的独立历史预测证据，而不是继续利用已暴露的 293 日调参；本报告仅提出方向，没有自动启动这些拟合。

现有两个独立历史窗口不足以提供大量独立重复；90 条相邻日误差不等于 90 次独立实验。没有宣称高斯假设、交换性、可靠覆盖、实际预警有效性或物理因果机制已获验证。原 B+ 参数拟合非收敛标志与数据来源限制保持，不重写旧结论。

## 6. 核验与交付

{receipt["source_files_checked"]} 项来源，8 项前置检查；18 个 e400 模型重载及独立 NumPy 前向；36 个分布、144 行逐点指标、54 行种子汇总、48,168 行逐日记录核对。共检查 {receipt["numerical_values_checked"]:,} 个数值，最大差 {receipt["max_abs_difference"]:.3g}。图件 7 张、28 面板，全部 SVG 曲线/区间、坐标与 PNG 已核验并目视。

岭回归的 NumPy 矩阵乘法出现历史同类警告；补充显式逐项求和证明输出有限且与保存预测一致，警告原文保留，底层成因未宣称修复。制图静态检查器未解析导入的导出函数，且要求本轮明确不制作的 PDF；保留其原始未通过状态，实际 SVG/PNG 几何与数值核验单独报告。

{link("docs/ootang_transformer_calibration_validation.v1.0.md", "详细核验")} · {link(str((out / "phase_summary.csv").relative_to(ROOT)), "完整 36 组 CSV")} · {link(str((out / "metrics_by_point.csv").relative_to(ROOT)), "四点指标")} · {link(str((out / "seed_summary.csv").relative_to(ROOT)), "种子结果")} · {link(str((root / "final_receipt.json").relative_to(ROOT)), "最终回执")}。
"""
    doc = doc.replace("HAL​F", "HALF")
    path = ROOT / "docs/ootang_transformer_calibration_results.v1.0.md"
    path.write_text(doc)
    write_json(
        out / "report_tables.json",
        dict(
            tables=tables,
            numeric_cells=cells,
            source_files={str(p.relative_to(ROOT)): sha(p) for p in out.glob("*.csv")},
            report_sha256=sha(path),
        ),
    )
    index = """# 半残差与校准：导师图件

所有图使用相同保存均值，完整四点和日期；最终段为探索性条件预测。参考纵轴可能裁切区间显示，完整范围副本保留全部区间。PNG 为 300 dpi，SVG 保留可编辑文字；不制作 PDF。

| 内容 | 参考纵轴 PNG / SVG | 完整范围 PNG / SVG |
| --- | --- | --- |
"""
    for name, label in (
        ("REG1_LAST90", "REG1 原 LAST90 区间"),
        ("REG1_DIST90", "REG1 距离匹配区间"),
        ("HALF_DIST90", "HALF 距离匹配区间"),
    ):
        index += f"| {label} | [PNG]({figs / 'v1' / (name + '_mentor.png')}) · [SVG]({figs / 'v1' / (name + '_mentor.svg')}) | [PNG]({figs / 'v1' / (name + '_full.png')}) · [SVG]({figs / 'v1' / (name + '_full.svg')}) |\n"
    index += f"\n六方法完整最终均值比较：[PNG]({figs / 'v1/all_methods_forecast.png'}) · [SVG]({figs / 'v1/all_methods_forecast.svg'})。\n\n[结果报告]({path}) · [源数组]({figs / 'v1/source_arrays.npz'}) · [图中数值]({figs / 'v1/figure_numbers.csv'}) · [数值/几何核验]({figs / 'v1/delivery_qa.json'}) · [逐图目视记录]({figs / 'v1/visual_qa.json'})。\n"
    (figs / "README.md").write_text(index)
    print(f"Report: {path}; tables={len(tables)} numeric cells={len(cells)}")


if __name__ == "__main__":
    main()
