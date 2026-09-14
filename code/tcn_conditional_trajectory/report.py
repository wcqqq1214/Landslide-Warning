"""Assemble the complete report and small comparison tables from verified results."""

import numpy as np
import pandas as pd

from .core import ROOT, read_json, spec, write_json


NAMES = {
    "BPLUS_CONTINUOUS": "连续原 B+",
    "DRIFT1": "DRIFT1",
    "RR_COND": "普通岭回归",
    "TCN_DIRECT_COND": "TCN 直接版",
    "TCN_BRES_COND": "TCN 残差版",
}


def table(frame, keys, labels, model=True):
    header = (["方法"] if model else []) + labels
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for _, row in frame.iterrows():
        vals = [NAMES[row.model]] if model else []
        for key in keys:
            value = row[key]
            if isinstance(value, str):
                vals.append(value)
            elif key == "seed":
                vals.append(str(int(value)))
            elif key.startswith("coverage"):
                vals.append(f"{value * 100:.2f}%")
            else:
                vals.append(f"{value:.4f}")
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    audit = read_json(root / "verification_v1/receipt.json")
    assert audit["status"] == "passed"
    assert (
        read_json(ROOT / cfg["figures"] / "v2/delivery_qa.json")["status"] == "passed"
    )
    analysis = root / "analysis"
    analysis.mkdir(exist_ok=True)
    phases = ["development", "final_exploratory"]
    summaries, fit_summaries, seed_summaries = [], [], []
    for phase in phases:
        s = pd.read_csv(root / phase / "summary.csv")
        s.insert(0, "phase", phase)
        summaries.append(s)
        f = pd.read_csv(root / phase / "fitting_by_point.csv")
        f = f.groupby("model", sort=False)[["mae", "rmse"]].mean().reset_index()
        f.insert(0, "phase", phase)
        fit_summaries.append(f)
        seeds = pd.read_csv(root / phase / "seed_metrics.csv")
        seeds = (
            seeds.groupby(["model", "seed"], sort=False)[
                ["mae", "rmse", "crps", "interval_score90"]
            ]
            .mean()
            .reset_index()
        )
        seeds.insert(0, "phase", phase)
        seed_summaries.append(seeds)
    summary = pd.concat(summaries, ignore_index=True)
    fitting = pd.concat(fit_summaries, ignore_index=True)
    seeds = pd.concat(seed_summaries, ignore_index=True)
    summary.to_csv(analysis / "phase_summary.csv", index=False, float_format="%.17g")
    fitting.to_csv(analysis / "fitting_summary.csv", index=False, float_format="%.17g")
    seeds.to_csv(analysis / "seed_summary.csv", index=False, float_format="%.17g")
    final = summaries[1].set_index("model")
    point = pd.read_csv(root / "final_exploratory/metrics_by_point.csv")
    wide = point.pivot(index="point", columns="model", values="rmse").reindex(
        cfg["points"]
    )
    wide.to_csv(analysis / "final_rmse_by_point.csv", float_format="%.17g")
    outcomes = dict(
        status="complete_negative_overall_effect",
        mean_and_probability_winners_locked_on_development=read_json(
            root / "selection.json"
        ),
        final_mean_best="BPLUS_CONTINUOUS",
        final_probability_score_best="DRIFT1",
        residual_seed_agreement=audit["seed_agreement"],
        final_residual_vs_direct_rmse_reduction=float(
            1
            - final.loc["TCN_BRES_COND", "rmse"] / final.loc["TCN_DIRECT_COND", "rmse"]
        ),
        formal_fits=18,
        optimizer_updates=3600,
        checkpoint_count=66,
    )
    write_json(analysis / "outcome.json", outcomes)
    cols = ["mae", "rmse", "crps", "interval_score90", "coverage90", "width90"]
    labels = ["MAE", "RMSE", "CRPS", "90%区间评分", "90%覆盖率", "90%宽度"]
    seedtable = table(
        seeds[seeds.phase == "final_exploratory"],
        ["seed", "mae", "rmse", "crps", "interval_score90"],
        ["种子", "MAE", "RMSE", "CRPS", "90%区间评分"],
    )
    pointtable = [
        "| 测点 | B+ | DRIFT1 | 岭回归 | TCN 直接 | TCN 残差 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for p in cfg["points"]:
        pointtable.append(
            "| "
            + p
            + " | "
            + " | ".join(f"{wide.loc[p, m]:.4f}" for m in cfg["methods"])
            + " |"
        )
    pooled = float(np.sqrt(np.mean(wide["BPLUS_CONTINUOUS"] ** 2)))
    text = f"""# 藕塘四点 TCN：按导师条件重新训练的完整对比报告

**本次已从头训练，并跑完全部开发和最终评价。整体仍未达到“优于改进 B+ 的均值与概率预测”目标，负结果完整保留。**

相比直接版，B+ 残差版在开发和最终的平均 MAE/RMSE 均改善，三个配对种子的方向也一致；但最终四个测点的均值及主要概率评分仍全部落后连续原 B+。执行完成、数值核验通过和研究效果未达标分别记录，不声称导师已验收。

## Material Passport

| 项目 | 本轮记录 |
| --- | --- |
| 任务 | 藕塘 ATU1、ATU5、MJ3、MJ1 四点累计位移条件概率预测 |
| 状态 | EXECUTED / VERIFIED；整体效果未达标；用户/导师效果验收尚未提供 |
| 协议 | 给定未来逐日降雨/库水位，预测段无实测位移反馈，B+ 连续状态不重置 |
| 数据 | 原 1461 日；最终训练 1168 日，完整预测 293 日 |
| 新训练 | 18 次拟合、3600 次更新、66 份检查点；B+ 参数重估 0 |
| 执行 | 分支 `codex/tcn-conditional-training`，仅本地提交；120 分钟独立上限，实际用时见最终回执 |

本轮依据[训练计划](ootang_tcn_conditional_training_plan.v1.0.md)及用户“无论效果多差都要跑通”的[执行补充](ootang_tcn_conditional_execution.v1.0.md)。旧七天滚动和旧权重 293 日递推都已完成；本轮是另外的整段重新训练。训练目标、推断方式、未来驱动以及 B+ 对照定义均与上一轮递推不同，不能把跨轮误差差值归因于单一改动。

## 1. 实际怎样训练和预测

两个 TCN 使用相同 6996 参数结构、22 个合法物理/驱动通道、四块因果卷积、三个种子及输出单位。直接版学习相对初始位移，残差版学习相对连续 B+ 的修正；两版都能读取相同物理特征，所以这不是“有/无物理信息”消融，也不称为 PINN。

每阶段用完整前缀从头训练，逐日输出四点轨迹。最终 2019-09-12—2020-06-30 的 293 日均值与区间一次锁定，不回灌预测段位移，也不消费自身位移递推。第一日位移仅作为共同原点；图中所有曲线统一减去同一原点，不在训练/预测分界贴合实测。

内部前 90 日选择 100 次更新，共同 Q：50次 0.262849，100次 0.121938，200次 0.165076，400次 0.162085。选定后，内部后 90 日误差用于开发区间；开发末 90 日误差用于最终区间，整段不更新尺度。所有检查点保留，未按最终成绩改成其他次数。

开发完整 376 日的均值/概率优胜者均为 **DRIFT1**，名单已在最终训练前锁定。开发阶段两臂效果门失败，仍继续执行全部五方法最终评价。最终表中 B+ 均值最好是探索性比较结果，不回改开发名单。

**DRIFT1** 是最后一天速度的恒速外推：`预测位移 = 起点前最后观测 + h × (最后观测 − 前一天观测)`。全预测段速度固定，没有学习参数。普通岭回归用相同 22 个当前日特征重新拟合，alpha=1，不复用旧七天模型。

## 2. 完整 293 日预测结果

每个点均为 293 日，没有截尾。表中指标为四点等权平均；MAE、RMSE、CRPS、宽度与区间评分单位均为 mm。RMSE 先逐点计算再平均；原 B+ 的全部点日合并 RMSE 为 {pooled:.4f} mm，与四点 RMSE 均值是不同统计口径。

{table(summaries[1], cols, labels)}

逐点 RMSE：

{chr(10).join(pointtable)}

三个需要分开回答的结论：

- **是否超过 B+：没有。** 残差版四点 MAE、RMSE、CRPS 和 90% 区间评分均高于连续原 B+；两版完整均值/概率工作条件都失败。
- **是否超过简单及普通机器学习对照：没有建立整体优势。** 残差版平均 RMSE 12.8621 略低于 DRIFT1 的 12.9777，但其 MAE、CRPS 和区间评分更高；其平均四项主要评分也均落后普通岭回归。不能只用 RMSE 的局部优势称整体达标。
- **残差版是否优于直接版：本轮有均值收益。** 两阶段平均 MAE/RMSE 均下降，各阶段 3/3 配对种子方向一致；最终平均 RMSE 相对直接版降低 {outcomes["final_residual_vs_direct_rmse_reduction"] * 100:.2f}%。概率收益有边界：最终平均 CRPS/区间评分改善、3/3 种子方向一致，但 MJ1 概率评分退步；开发区间评分退步，配对种子只有 1/3 同时改善两项概率评分，不能称为跨阶段稳定的概率收益。

**100% 覆盖并非概率预测成功。** 最终直接版、残差版的 90% 平均宽度分别为 446.40、252.26 mm；原 B+ 也达 220.08 mm。这些方法的尺度来自开发末 90 日的较大误差，转用于重新拟合的最终模型后很宽。CRPS/区间评分保留这一代价，不通过缩窄区间或增补概率头补救本轮。DRIFT1 的概率评分较低，但最终 90% 覆盖只有 68.09%，同样不是可靠覆盖的证明。

## 3. 训练拟合与开发表现

最终前 1168 日完整训练段的四点平均拟合指标：

{table(fit_summaries[1], ["mae", "rmse"], ["拟合 MAE", "拟合 RMSE"])}

残差版拟合 RMSE 降到 1.7890 mm，但预测 RMSE 为 12.8621 mm，高于 B+。直接版在固定 100 次更新下的训练拟合仍较差。这里说明拟合改善没有转化为导师要求的预测优势；不将固定次数结束称为已经收敛，也不据此对整个 TCN 家族作结论。

开发段 2018-09-01—2019-09-11，完整 376 日：

{table(summaries[0], cols, labels)}

最终种子级四点平均指标（种子概率评分使用其对应臂发出时的集成校准尺度，未逐种子重新挑选区间）：

{seedtable}

## 4. 给导师看的四点图

全部图使用完整日期，蓝底为训练拟合、橙底为预测。色带为 80%/95% 逐日边际区间，训练段不虚构概率带；主线为三个种子等权均值。PNG 为 300 dpi，另有可编辑文字 SVG。

### B+ 残差版

![TCN B+残差版四点完整训练与独立条件预测](../figures/ootang_tcn_conditional_v1/20260914/v2/TCN_BRES_COND.png)

### 直接版

![TCN直接版四点完整训练与独立条件预测](../figures/ootang_tcn_conditional_v1/20260914/v2/TCN_DIRECT_COND.png)

### 完整预测段的五方法均值对比

![完整293日五方法均值对比](../figures/ootang_tcn_conditional_v1/20260914/v2/all_methods_forecast.png)

## 5. 核验、交付与限制

训练前六项合同检查、39 项来源、三组教师参数和原 B+ 连续重放通过。训练后全部 **66 份检查点**用独立 NumPy 因果卷积重算，另以 SVD 代数复算岭回归；共核对 **509586 个数值**，最大差 **1.47e-11**，所有预测、尺度、评分与选择顺序通过。没有为这些复算新增训练或优化器更新。

三张最终图的 12 面板已目视，38612 个曲线/区间数值从实际 SVG 顶点核对，最大导出差 7.44e-6 mm；1.5 pt 对齐、中文字体、PNG 尺寸、完整日期和图中 RMSE 通过。SVG 数值容差仅针对六位小数坐标导出，不修改模型/评分容差。首次图件附带数组因重复尺度键打包失败，修复后 v2 全部成功；v1 图件及错误日志保留，训练与评分不重跑。

静态绘图检查要求 PDF、未识别动态对齐函数与尺寸表达式；本任务明确不制作 PDF，按实际 SVG/PNG 和渲染几何核验，不称 PDF 审计通过。原物理与独立代数复算中的矩阵运算警告保留；所有被比较结果有限且一致，不声称底层警告成因已解决。

| 交付 | 入口 |
| --- | --- |
| 两阶段汇总 CSV | [phase_summary.csv](../results/ootang_tcn_conditional_v1/20260914/analysis/phase_summary.csv) |
| 最终逐点完整指标 | [metrics_by_point.csv](../results/ootang_tcn_conditional_v1/20260914/final_exploratory/metrics_by_point.csv) |
| 最终 5860 行点日预测与全部区间 | [daily_predictions.csv](../results/ootang_tcn_conditional_v1/20260914/final_exploratory/daily_predictions.csv) |
| 拟合与种子汇总 | [拟合表](../results/ootang_tcn_conditional_v1/20260914/analysis/fitting_summary.csv) / [种子表](../results/ootang_tcn_conditional_v1/20260914/analysis/seed_summary.csv) |
| 三张 PNG/SVG | [图件索引](../figures/ootang_tcn_conditional_v1/20260914/README.md) |
| 核验记录 | [核验文档](ootang_tcn_conditional_validation.v1.0.md) / [独立复算](../results/ootang_tcn_conditional_v1/20260914/verification_v1/receipt.json) / [图件核验](../figures/ootang_tcn_conditional_v1/20260914/v2/delivery_qa.json) |
| 最终用时与完整产物锁 | [final_receipt.json](../results/ootang_tcn_conditional_v1/20260914/final_receipt.json) |

给定未来驱动、历史日期反复暴露、原始日值 as-of 状态未知、前缀 B+ 教师为拟合回代及历史优化器未收敛的限制保持。90 条连续误差并非独立样本，重新拟合后的尺度迁移无覆盖保证，区间也不是整段同时覆盖带。成果只支持这个公开日序列、这项固定配置的探索性结论，不代表真实预警有效。

**全部预定实验和核验已经完成，保留负结果。本轮结束，不追加模型或训练次数，不恢复旧预算。**
"""
    (ROOT / "docs/ootang_tcn_conditional_training_results.v1.0.md").write_text(text)
    print("report and comparison tables written")


if __name__ == "__main__":
    main()
