"""Report and comparison tables from independently verified frozen outputs."""

import numpy as np
import pandas as pd

from .core import ROOT, choose, read_json, spec, write_json
from .figures import NAMES


def table(frame, columns, labels):
    lines = [
        "| " + " | ".join(labels) + " |",
        "| " + " | ".join(["---"] * len(labels)) + " |",
    ]
    for _, row in frame.iterrows():
        values = []
        for k in columns:
            v = row[k]
            if k == "model":
                values.append(NAMES[v])
            elif isinstance(v, (bool, np.bool_)):
                values.append("是" if v else "否")
            elif isinstance(v, str):
                values.append(v)
            elif k.startswith("coverage"):
                values.append(f"{100 * v:.2f}%")
            elif k in ("seed", "mean_seeds", "probability_seeds"):
                values.append(str(int(v)))
            else:
                values.append(f"{v:.4f}")
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    figs = ROOT / cfg["figures"] / "v1"
    audit = read_json(root / "verification_v1/receipt.json")
    assert audit["status"] == "passed"
    assert read_json(figs / "delivery_qa.json")["status"] == "passed"
    assert read_json(figs / "visual_review.json")["status"] == "passed"
    dest = root / "analysis"
    dest.mkdir(exist_ok=True)
    phases = ["development", "final_exploratory"]
    names = {"development": "开发 376 日", "final_exploratory": "最终探索性 293 日"}
    summary = pd.concat(
        [pd.read_csv(root / p / "summary.csv").assign(phase=p) for p in phases],
        ignore_index=True,
    )
    point = pd.concat(
        [
            pd.read_csv(root / p / "metrics_by_point.csv").assign(phase=p)
            for p in phases
        ],
        ignore_index=True,
    )
    fitting = pd.concat(
        [
            pd.read_csv(root / p / "fitting_by_point.csv")
            .groupby("model", sort=False)[["mae", "rmse"]]
            .mean()
            .reset_index()
            .assign(phase=p)
            for p in phases
        ],
        ignore_index=True,
    )
    seeds = pd.concat(
        [
            pd.read_csv(root / p / "seed_metrics.csv")
            .groupby(["model", "seed"], sort=False)[
                ["mae", "rmse", "crps", "interval_score90"]
            ]
            .mean()
            .reset_index()
            .assign(phase=p)
            for p in phases
        ],
        ignore_index=True,
    )
    comparisons = pd.read_csv(root / "verification_v1/paired_comparisons.csv")
    for name, frame in (
        ("phase_summary", summary),
        ("metrics_by_point", point),
        ("fitting_summary", fitting),
        ("seed_summary", seeds),
        ("comparisons", comparisons),
    ):
        frame.to_csv(dest / (name + ".csv"), index=False, float_format="%.17g")
    wide = (
        point[point.phase == "final_exploratory"]
        .pivot(index="point", columns="model", values="rmse")
        .reindex(cfg["points"])[cfg["methods"]]
    )
    wide.to_csv(dest / "final_rmse_by_point.csv", float_format="%.17g")
    decisions = read_json(root / "selection.json")
    gates = {p: read_json(root / p / "effect_gates.json") for p in phases}
    counts = {p: sum(v["joint_pass"] for v in gates[p].values()) for p in phases}
    pair_rows = []
    baseline_rows = []
    for p in phases:
        s = summary[summary.phase == p].set_index("model")
        for f, pair in cfg["families"].items():
            agreement = audit["seed_agreement"][p][f]
            row = dict(
                phase=p,
                family=f,
                mean_ensemble=bool(
                    all(s.loc[pair[1], k] < s.loc[pair[0], k] for k in ("mae", "rmse"))
                ),
                probability_ensemble=bool(
                    all(
                        s.loc[pair[1], k] < s.loc[pair[0], k]
                        for k in ("crps", "interval_score90")
                    )
                ),
                mean_seeds=agreement["mean"]["improving_seeds"],
                probability_seeds=agreement["probability"]["improving_seeds"],
                rmse_difference=float(s.loc[pair[1], "rmse"] - s.loc[pair[0], "rmse"]),
                crps_difference=float(s.loc[pair[1], "crps"] - s.loc[pair[0], "crps"]),
                is90_difference=float(
                    s.loc[pair[1], "interval_score90"]
                    - s.loc[pair[0], "interval_score90"]
                ),
            )
            row["mean_paired_condition"] = (
                row["mean_ensemble"] and row["mean_seeds"] >= 2
            )
            row["probability_paired_condition"] = (
                row["probability_ensemble"] and row["probability_seeds"] >= 2
            )
            pair_rows.append(row)
        for a in cfg["arms"]:
            row = dict(
                phase=p,
                model=a,
                mean_condition=gates[p][a]["mean_pass"],
                probability_condition=gates[p][a]["probability_pass"],
                joint_condition=gates[p][a]["joint_pass"],
            )
            for control in cfg["reuse_methods"]:
                row[control + "_mean_both"] = bool(
                    all(s.loc[a, k] < s.loc[control, k] for k in ("mae", "rmse"))
                )
                row[control + "_probability_both"] = bool(
                    all(
                        s.loc[a, k] < s.loc[control, k]
                        for k in ("crps", "interval_score90")
                    )
                )
            baseline_rows.append(row)
    pairs = pd.DataFrame(pair_rows)
    controls = pd.DataFrame(baseline_rows)
    pairs.to_csv(dest / "residual_pairing.csv", index=False, float_format="%.17g")
    controls.to_csv(dest / "control_conditions.csv", index=False)
    final = summary[summary.phase == "final_exploratory"].set_index("model")
    ranked = {m: row.to_dict() for m, row in final.iterrows()}
    outcomes = dict(
        status="executed_and_verified",
        joint_pass_counts=counts,
        final_mean_rank_minimum=choose(ranked, ["mae", "rmse"], cfg["methods"], 1e-12),
        final_probability_rank_minimum=choose(
            ranked, ["crps", "interval_score90"], cfg["methods"], 1e-12
        ),
        final_rmse_minimum=choose(ranked, ["rmse", "mae"], cfg["methods"], 1e-12),
        development_selection=decisions,
        residual_pairing=pair_rows,
        new_fits=audit["formal_fits_verified"],
        optimizer_updates=audit["optimizer_updates_verified"],
        checkpoints=audit["model_checkpoints"],
        old_model_fits=0,
        physical_calls=0,
        user_or_mentor_acceptance="not provided",
        exploratory=True,
    )
    write_json(dest / "outcome.json", outcomes)
    primary = [
        "model",
        "mae",
        "rmse",
        "crps",
        "interval_score90",
        "coverage90",
        "width90",
    ]
    primary_labels = [
        "方法",
        "MAE",
        "RMSE",
        "CRPS",
        "90%区间评分",
        "90%覆盖率",
        "90%宽度",
    ]
    pass_sentence = (
        "四个新方法均未达到完整工作条件。"
        if not counts["final_exploratory"]
        else f"最终窗有 {counts['final_exploratory']}/4 个新方法通过工作条件，仍需结合开发结果解释。"
    )
    point_table = [
        "| 测点 | " + " | ".join(NAMES[m] for m in cfg["methods"]) + " |",
        "| --- | " + " | ".join(["---:"] * len(cfg["methods"])) + " |",
    ]
    for p in cfg["points"]:
        point_table.append(
            "| "
            + p
            + " | "
            + " | ".join(f"{wide.loc[p, m]:.4f}" for m in cfg["methods"])
            + " |"
        )
    update = read_json(root / "internal_selection.json")["selected_updates"]
    paragraph = []
    for a in cfg["arms"]:
        x = controls[
            (controls.phase == "final_exploratory") & (controls.model == a)
        ].iloc[0]

        def status(value):
            return "均降低" if value else "未同时降低"

        paragraph.append(
            f"- **{NAMES[a]}**：相对 B+ 的 MAE/RMSE{x['BPLUS_CONTINUOUS_mean_both'] and '均降低' or '未同时降低'}，CRPS/区间评分{status(x['BPLUS_CONTINUOUS_probability_both'])}；相对 DRIFT1 的两项均值指标{status(x['DRIFT1_mean_both'])}，相对岭回归{status(x['RR_COND_mean_both'])}。完整均值条件{'通过' if x.mean_condition else '未通过'}、概率条件{'通过' if x.probability_condition else '未通过'}。"
        )
    pair_display = pairs.copy()
    pair_display["phase"] = pair_display.phase.map(names)
    pair_display["family"] = pair_display.family.map(
        {"TRANSFORMER": "Transformer", "CNN_MAMBA": "CNN-Mamba"}
    )
    pair_table = table(
        pair_display,
        [
            "phase",
            "family",
            "mean_ensemble",
            "mean_seeds",
            "probability_ensemble",
            "probability_seeds",
        ],
        [
            "阶段",
            "结构",
            "残差版两均值指标均改善",
            "均值同向种子/3",
            "残差版两概率评分均改善",
            "概率同向种子/3",
        ],
    )
    figure_lines = []
    for a in cfg["arms"]:
        rel = f"../{cfg['figures']}/v1/{a}"
        figure_lines.append(
            f"### {NAMES[a]}\n\n![{NAMES[a]}四点拟合及条件预测]({rel}.png)\n\n[可编辑 SVG]({rel}.svg)"
        )
    text = f"""# 藕塘四点：轻量 Transformer 与 CNN-Mamba 完整对比报告

**两个结构、四个输出版本已从头训练，并跑完全部开发及最终 293 日评价；没有因效果差中断。** {pass_sentence} 开发完整条件通过 {counts["development"]}/4，最终通过 {counts["final_exploratory"]}/4；“程序跑通”和“预测有效”分别记录。

本次最终平均 **MAE 最低为{NAMES[outcomes["final_mean_rank_minimum"]]}，RMSE 最低为{NAMES[outcomes["final_rmse_minimum"]]}**；CRPS优先的概率排序最小者为 **{NAMES[outcomes["final_probability_rank_minimum"]]}**。MAE略低不等于MAE/RMSE同时改善。这些是同窗探索性排序，不回改开发时已锁定的 **{NAMES[decisions["mean_winner"]]}（均值）/ {NAMES[decisions["probability_winner"]]}（概率）**。

## 1. 本次确实训练了什么

| 项目 | 记录 |
|---|---|
| Material Passport | EXECUTED / VERIFIED；探索性研究结果；未声称用户或导师已验收 |
| 固定数据与协议 | ATU1、ATU5、MJ3、MJ1；给定未来逐日降雨/库水位，预测段无实测位移反馈 |
| 日期 | 内部612/180日、开发792/376日、最终1168/293日；原日历完整保留 |
| Transformer | 4916参数，16维/2头/2层、因果注意力、正弦日期编码；共同选择{update["TRANSFORMER"]}次 |
| CNN-Mamba | 7956参数，16通道时间卷积+2层Mamba-1；共同选择{update["CNN_MAMBA"]}次 |
| 正式新增训练 | {audit["formal_fits_verified"]}次拟合，{audit["optimizer_updates_verified"]}次Adam更新，{audit["model_checkpoints"]}份检查点 |
| 旧模型 | B+、DRIFT1、岭回归及两版TCN复用已核验同协议产物；旧模型拟合和物理调用0 |
| 交付 | Markdown、CSV、五张中文四点PNG/SVG；本地分步提交，不push/PDF |

三个种子0/1/2、CPU单线程float64、Adam 0.001；内部固定跑50/100/200/400检查点，选择只用内部前90日。每种结构的直接/残差两臂共用一次内部选择，开发和最终各从头训练。三种子等权均值为主输出，全部种子保存，不按最终表现换轮数或挑种子。

直接版输出 `y0 + 网络修正`，残差版输出 `连续原 B+ + 网络修正`，输出单位相同。两版都读相同22维合法物理/驱动特征；这是**输出基线/残差目标的配对**，不是有无物理信息消融，也不是PINN。
每个模型从第0日给定特征编码，最终293日一次发出；不把实测位移回灌，也不依靠自身位移每7天递推。已知未来驱动属于条件回算，不等于实际业务中预知未来天气。

旧Mamba分支为8点、7日输入→1日、空间网格和CUDA分位数模型。本次是时间CNN与Mamba的四点适配，未使用其权重。本机CPU版本保留Mamba-1选择性状态递推，核对了[官方参考扫描](https://github.com/state-spaces/mamba/blob/e9594ce1c732d97440f0332fdc43170a2294dbfa/mamba_ssm/ops/selective_scan_interface.py)及独立串行实现；未冒称运行官方CUDA内核。Transformer使用小型因果编码器，不称PatchTST；架构和掩码参照[编码器文档](https://docs.pytorch.org/docs/stable/generated/torch.nn.TransformerEncoderLayer.html)。

**DRIFT1** 是最后一天速度不变的外推基线：`预测 = 最后观测 + h × (最后观测 − 前一天观测)`，无待训练参数。普通岭回归固定alpha=1，使用同协议22维特征。原TCN为6996参数、固定100次更新；各结构训练次数和参数量不同，本次不宣称等算力比较。

## 2. 完整最终293日结果

2019-09-12—2020-06-30，每点293日。指标均为四点等权平均；RMSE先逐点计算再平均。除覆盖率外，表内单位均为mm。误差和区间评分越低越好；覆盖率与宽度须一起判断，不能单看覆盖率越高或宽度越小越好。

{table(summary[summary.phase == "final_exploratory"], primary, primary_labels)}

{chr(10).join(paragraph)}

逐点RMSE（全部测点，不拼接最优方法）：

{chr(10).join(point_table)}

完整CSV：[阶段汇总](../{cfg["out"]}/analysis/phase_summary.csv)、[逐点评分](../{cfg["out"]}/analysis/metrics_by_point.csv)、[相对各基线和TCN的差值](../{cfg["out"]}/analysis/comparisons.csv)。各点MAE、CRPS、80/90/95%区间评分、覆盖与宽度均保留。

## 3. 开发结果和残差收益分别判断

开发2018-09-01—2019-09-11共376日。其名单在最终阶段开始前已锁定；最终成绩没有用于改名单、结构、种子或更新次数。

{table(summary[summary.phase == "development"], primary, primary_labels)}

残差配对的均值改善同时要求MAE/RMSE降低，概率改善同时要求CRPS/90%区间评分降低；还需至少2/3同种子方向一致。下表报告集成方向和种子数量，不把一个阶段的改善冒充跨阶段稳定收益：

{pair_table}

每种子概率诊断共用该臂已发出的集成校准尺度，未为种子另挑误差池。所有种子数值见[种子汇总](../{cfg["out"]}/analysis/seed_summary.csv)，配对差值和完整判定见[残差配对](../{cfg["out"]}/analysis/residual_pairing.csv)；正差表示残差版更差。

最终阶段的训练拟合误差另列，不能用于替代预测成绩：

{table(fitting[fitting.phase == "final_exploratory"], ["model", "mae", "rmse"], ["方法", "拟合MAE", "拟合RMSE"])}

## 4. 导师可直接查看的图件

四张模型图均按同一形式绘制：蓝色训练段、橙色完整预测段、实测、原B+、模型均值及80/95%预测区间。所有曲线统一减原始首日位移，不在分界处重新贴合实测。区间只画在预测段；图内RMSE来自完整293日。

{(chr(10) * 2).join(figure_lines)}

### 九方法完整预测窗

![九方法完整预测比较](../{cfg["figures"]}/v1/all_methods_forecast.png)

[可编辑SVG](../{cfg["figures"]}/v1/all_methods_forecast.svg)／[图件索引与数据](../{cfg["figures"]}/README.md)。此图只叠加均值，概率区间见上方配套图，未删去误差大的日期或方法。

## 5. 如何理解结果和限制

均值、概率以及残差额外收益须分别作答。完整工作条件沿用事前TCN协议：平均MAE/RMSE相对B+改善至少1%且逐点保护，概率两评分改善至少1%且逐点退步≤5%、平均/逐点覆盖达到85%/80%。这些是研究工作条件，不是导师逐条确认的数值门槛。超过B+、超过简单/岭回归、超过既有TCN以及残差收益是不同结论。

区间由此前90条已兑现独立预测误差的RMS构造，预测期不更新。误差存在序列相关性，尺度从旧训练前缀模型转移到本次重训模型；100%覆盖若伴随宽区间和较差区间评分，不能作为概率模型成功依据。本轮无新概率头、C18方差项或同时覆盖保证。
数据日值原始as-of及历史预处理限制沿用原记录，前缀教师既有优化器未收敛标志保留。历史窗口已反复评价，不能当成新盲测或真实预警证据；四点来自同一案例，三种子不是独立滑坡样本。
这轮只能回答两个固定小结构在当前条件任务中的表现，不能推论整个Transformer或Mamba家族无效。与旧滚动、起点固定驱动递推、旧8点Mamba任务不混排；本轮停止追加模型和轮数，负结果、原始预测和失败记录保留。

## 6. 执行与复核凭据

来源73项、训练前六项合同核验通过。独立复核全部{audit["model_checkpoints"]}份权重，使用NumPy显式因果注意力与Mamba逐日串行递推；PyTorch重载也逐数组核对。共核对{audit["numerical_values_checked"]}项数值，最大绝对差{audit["max_abs_difference"]:.3g}；含原单位预测、归一化、尺度、逐日区间、所有评分和开发锁定。原始模型数值容差和SVG坐标量化容差分开记录。
正式训练异常{audit["execution_errors"]}次；未因为成绩差退出。纯正式训练循环累计{audit["training_loop_seconds"]:.2f}秒，准备/核验/绘图也计入独立120分钟窗口，实际总用时见[最终回执](../{cfg["out"]}/final_receipt.json)。

[事前计划](ootang_sequence_conditional_plan.v1.0.md)／[配置](../config/ootang_sequence_conditional.v1_0.json)／[来源清单](ootang_sequence_conditional_sources.v1.0.json)／[核验报告](ootang_sequence_conditional_validation.v1.0.md)／[独立数值回执](../{cfg["out"]}/verification_v1/receipt.json)。
复现入口为 `PYTHONPATH=code .venv/bin/python -m sequence_conditional.run <phase>`，已完成有效拟合只重载；不自动重新训练。保存结果只读复核入口为 `sequence_conditional.audit --attempt <新核验目录名>`，无需优化器更新。
"""
    (ROOT / "docs/ootang_sequence_conditional_results.v1.0.md").write_text(text)
    print(outcomes)


if __name__ == "__main__":
    main()
