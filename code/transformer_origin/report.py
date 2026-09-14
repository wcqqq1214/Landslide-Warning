"""Assemble the small-pilot Markdown and source-indexed numerical tables."""

import pandas as pd

from .core import (
    B,
    ROOT,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    spec,
    write_json,
)

NAMES = {
    B: "改进 B+",
    "DRIFT1": "DRIFT1",
    "RR_COND": "普通岭回归",
    "OLD_TRANSFORMER": "旧 Transformer 原版",
    "OLD_HALF": "旧固定半残差",
    "OLD_REG1": "旧固定 λ=1",
    "COND_ATTN": "完整历史注意力",
    "NO_OBS_ATTN": "去显式历史位移的注意力",
    "POOL_MLP": "历史均匀池化",
}


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = root / "delivery"
    out.mkdir(exist_ok=False)
    summary = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    points = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    pairings = read_json(root / "analysis/pairing.json")
    gates = read_json(root / "analysis/effect_gates.json")
    y = read_labels(ROOT / cfg["data"], 1461)
    _, dates = read_forcing(ROOT / cfg["data"], 1461)
    tables = []

    def table(name, headers, rows):
        text = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        text += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
        tables.append(dict(name=name, headers=headers, rows=rows))
        return "\n".join(text)

    def f(value):
        return f"{value:.6f}"

    windows = table(
        "windows",
        ["拟合前缀", "最后可用位移日", "完整预测期", "用途"],
        [
            [
                n,
                dates[n - 1],
                f"{dates[n]}—{dates[e - 1]}",
                "校准启动，不计主效果"
                if n == 612
                else "最终探索"
                if n == 1168
                else "历史探索",
            ]
            for n, e in zip(cfg["origins"], cfg["ends"])
        ],
    )
    rmse = table(
        "rmse",
        ["方法", "历史792", "历史972", "最终1168"],
        [
            [NAMES[m]] + [f(summary.loc[(n, m), "rmse"]) for n in cfg["origins"][1:]]
            for m in cfg["controls"] + cfg["arms"]
        ],
    )
    final = table(
        "final",
        ["方法", "MAE", "RMSE", "CRPS", "90%区间评分", "90%覆盖", "90%宽度"],
        [
            [NAMES[m]]
            + [
                f(summary.loc[(1168, m), k])
                for k in ["mae", "rmse", "crps", "interval_score90"]
            ]
            + [
                f"{100 * summary.loc[(1168, m), 'coverage90']:.2f}%",
                f(summary.loc[(1168, m), "width90"]),
            ]
            for m in cfg["controls"] + cfg["arms"]
        ],
    )
    spatial = table(
        "spatial",
        ["测点", "B+ RMSE", "完整历史 RMSE", "去历史位移 RMSE", "池化 RMSE"],
        [
            [p] + [f(points.loc[(1168, m, p), "rmse"]) for m in [B] + cfg["arms"]]
            for p in cfg["points"]
        ],
    )
    gate_rows = []
    for n in cfg["origins"][1:]:
        for m in cfg["arms"]:
            g = gates[str(n)][m]
            r = next(
                r
                for r in pairings
                if (r["origin"], r["candidate"], r["reference"]) == (n, m, B)
            )
            gate_rows.append(
                [
                    n,
                    NAMES[m],
                    "通过" if g["mean_pass"] else "未过",
                    "通过" if g["probability_pass"] else "未过",
                    f"{r['seed_both_improve']}/3",
                ]
            )
    gates_table = table(
        "gates",
        ["起点", "方法（相对B+）", "均值门", "概率门", "均值同向种子"],
        gate_rows,
    )
    pair_rows = []
    for r in pairings:
        if r["reference"] == B:
            continue
        n, a, b = r["origin"], r["candidate"], r["reference"]
        pair_rows.append(
            [
                n,
                NAMES[b],
                f(summary.loc[(n, a), "mae"] - summary.loc[(n, b), "mae"]),
                f(summary.loc[(n, a), "rmse"] - summary.loc[(n, b), "rmse"]),
                f"{r['seed_both_improve']}/3",
                "通过" if r["mean_pass"] else "未过",
                "通过" if r["probability_pass"] else "未过",
            ]
        )
    paired = table(
        "paired",
        ["起点", "对照", "ΔMAE", "ΔRMSE", "均值同向种子", "配对均值门", "配对概率门"],
        pair_rows,
    )
    directions = []
    for n in cfg["origins"][1:]:
        means = load_npz(root / f"origin_{n}/means.npz")
        for j, p in enumerate(cfg["points"]):
            directions.append(
                dict(
                    origin=n,
                    point=p,
                    required_correction_mean_mm=float(
                        (y[n : n + 293, j] - means[B][:, j]).mean()
                    ),
                    predicted_correction_mean_mm=float(
                        (means["COND_ATTN"][:, j] - means[B][:, j]).mean()
                    ),
                )
            )
    pd.DataFrame(directions).to_csv(
        out / "residual_direction.csv", index=False, float_format="%.17g"
    )
    correction = table(
        "correction",
        ["最终测点", "实测−B+的时间均值", "模型−B+的时间均值"],
        [
            [
                r["point"],
                f(r["required_correction_mean_mm"]),
                f(r["predicted_correction_mean_mm"]),
            ]
            for r in directions
            if r["origin"] == 1168
        ],
    )
    report = f"""# 起点条件化小型 Transformer：完整小试报告 v1.0

日期：2026-09-15（北京时间）。**全部预定实验、独立复算及三张图件已完成。这版原型没有带来稳定收益，最终293日明显差于B+。** 本轮不追加模型、训练或λ/RL搜索，旧结果及负结果保留。

完整历史注意力版的最终四点平均RMSE为24.702585 mm，B+为9.124173 mm；去显式历史位移版24.565083 mm，均匀池化版24.929915 mm。三臂都只在972历史窗通过均值门；最终均值与概率完整条件均失败。实现跑通、研究效果与用户/导师验收是三件事，目前仅前两项已有明确记录，尚未声称验收。

## 1. 本次实际尝试了什么

本轮把起点前**全部合法历史日**显式交给一个小型交叉注意力解码器，以未来距离及给定驱动/物理特征为查询，一次输出293日四点位移。历史包括22维物理/驱动特征、四点相对位移、四点实测−同教师B+残差。隐层16、一层两头交叉注意力、FFN 16→32→16；没有历史自注意力编码器栈或未来查询之间的自注意力，不构造空间图，不称完整TFT/TimeXer复现或PINN。

- COND_ATTN：全部30个历史通道，3220参数。
- NO_OBS_ATTN：只把最后8个显式历史观测/残差通道归一化后置零，其他结构和初始化完全相同，3220参数。它仍有B+及驱动历史，不能称“无历史/无物理信息”。
- POOL_MLP：同样读取完整30通道，用均匀池化代替可学习的q/k注意力，2644参数；因此配对包含注意力方式与少量参数量差异。

三臂输出都为连续B+轨迹＋网络残差；零输出精确回退B+，不强制在起点贴合实测。种子0/1/2、float64 CPU、Adam 0.001、固定200次更新、归一化MSE＋λ=1残差平方惩罚。每次抽4个训练伪起点及各16个成熟距离。保存e0/50/100/200，主输出固定e200三种子等权均值，无选模或训练后改轮数。

## 2. 日期、教师与信息边界

{windows}

每条路径没有预测段位移反馈；未来逐日降雨/库水位作为**给定条件**，因此不是未来驱动未知的真实部署测试。B+状态从首日连续递推，新增起点允许使用此前观测训练，但旧已发出路径保留。

训练伪起点m从432开始，教师选432/612/792中拟合边界不晚于m的最新一组，目标索引m+h−1必须小于当前拟合前缀n。标准化用当前n日训练前缀，各臂一致；这些episode是当前训练样本，不能冒称此前独立发出的在线预测。当前四个实际起点使用612/792/792/1168教师，972不重新拟合B+。432教师延长至904行、612教师延长至1084行，新增物理前向2次、B+重新拟合0次；[实现说明](ootang_transformer_origin_implementation.v1.0.md)记录了原计划432覆盖长度笔误的训练前勘误。

612拟合只有最长180日可训练目标，却需发出293日作为后续校准启动；后来三个拟合阶段具备293日合法训练距离。最终预测教师1168与训练episode教师≤792存在差异。保留这些限制，不能把未覆盖长距离的bootstrap说成完整293日直接监督。

概率层逐点取上一已发出路径第91—180日的90条成熟误差RMS，形成固定高斯80/90/95%边际区间，整条293日路径不更新、不偏差校正。校准依次用[702,792)、[882,972)、[1062,1152)，最终16日空隙保持；种子概率评分共用该方法集成的尺度。90条相邻误差不等于独立重复，距离91—180的误差向完整293日迁移不保证长期覆盖。所有均值与尺度锁定后统一评分完整日期。

## 3. 与B+、简单模型及旧模型比较

各窗口四点RMSE的平均值，单位mm，越低越好；不是把四点误差合并后再开根号。

{rmse}

DRIFT1是固定起点的匀速外推：未来第h天位移＝最后一次实测位移＋h×最近一天位移增量。它没有训练参数，本次发出后不再用新位移更新。RR_COND是同条件下的固定特征普通岭回归。

完整历史版在972窗超过B+和普通岭回归，但仍落后DRIFT1；792及最终窗均落后上述三个对照。旧固定半残差的最终局部收益保留。旧Transformer训练400次，新原型200次，输入、训练任务及结构也变了，不能把新旧差异单独归因于某个模块。

![跨时段及配对比较]({ROOT / cfg["figures"] / "v1/comparison.png"})

相对B+的原工作条件保持：平均MAE/RMSE各至少改善1%、四点均不退步；平均CRPS/90%区间评分各至少改善1%，逐点最多退步5%，90%平均覆盖至少85%、每点至少80%。种子列另报三个种子中平均MAE和RMSE同时改善的数量，不作为独立统计显著性。

{gates_table}

## 4. 显式历史与注意力有没有额外收益

下表候选均为COND_ATTN，Δ＝候选−对照，负数更好。均值/概率配对门沿用上节条件，不能将相对较差候选的改善当成超过B+。

{paired}

显式历史输入相对NO_OBS在792、972平均误差略好，最终却略差；平均MAE/RMSE同向种子由3/3、2/3变为1/3。相对POOL在792和最终平均误差略好，972则较差，完整配对均值门三窗均未通过。最终相对两个新对照的概率门通过是局部正结果，但三臂都未超过B+的完整概率门。由此尚未建立稳定的显式观测历史收益或注意力收益。

## 5. 最终完整293日：各点与概率

{spatial}

新完整历史版四点RMSE都高于B+，MJ3和MJ1退步更大。最终完整均值和概率评分如下；MAE/RMSE/CRPS/区间评分/宽度单位mm，覆盖率除外。全部逐点80/90/95%指标、种子和日期见CSV，未删除困难尾部。

{final}

完整历史版90%平均覆盖为96.76%，但平均宽度153.60 mm、CRPS为16.901026 mm，仍高于B+的14.894908 mm。区间评分较B+改善，来自不同的宽度与失覆盖权衡，不能据覆盖较高称概率目标达标。NO_OBS和POOL的MJ3覆盖分别79.18%和78.16%，低于原80%逐点保护。

![导师固定纵轴四点图]({ROOT / cfg["figures"] / "v1/COND_ATTN_mentor.png"})

蓝色背景表示训练历史、橙色为完整预测段；历史只显示实测与B+，神经曲线从预测起点开始，避免伪造训练拟合。导师固定纵轴未截任何实测或均值，但95%区间分别有16/10/160/7日超出坐标；[完整范围PNG](../{cfg["figures"]}/v1/COND_ATTN_full.png)与SVG保留整个区间，评分始终使用完整真实区间。

## 6. 这次失败给出的线索

以下为最终293日残差的时间平均，单位mm；是事后诊断，不用于选模或修改预测。

{correction}

972窗的B+整体偏高，完整历史版向下修正，四点平均MAE/RMSE都改善；最终MJ3需要正修正，但模型给出明显负修正，MJ1修正幅度也过大。这是保存数组的描述性证据，提示跨教师/时段残差关系变化值得关注，尚不能证明它是唯一失效原因。完整历史输入可影响非零网络输出的正控已通过；“模型接收了历史”并不等于“学到了可迁移的历史状态”。

目前不支持用这个原型替换B+。本轮到此结束，不追加训练、结构或RL。后续讨论可优先围绕教师变化、修正方向和幅度的跨时段可迁移性形成一个可验证假设；本报告不授权下一轮，也不推论所有Transformer、xLSTM或状态空间模型都无效。

## 7. 执行、复算及交付

36次新拟合、7200次更新、144检查点、全部预定窗口完整执行，无训练失败或中途按效果停止。三臂×四起点×三种子均保留。253冻结来源、全部144权重精确重载、独立NumPy完整前向、训练目标/日期/标准化/成熟误差池/旧对照/事件顺序与评分核验通过，共9027212数值单元，最大差2.16e-12。训练使用原始物理前向警告留档，未声称修复底层数值库。

共27汇总、108逐点、81种子、31644点日记录；三张最终PNG/SVG共12面板，35247实际图形值核对，最小文字7.6pt，1.5pt面板对齐/文字边界/曲线碰撞及目视检查完成。首版绘图的NumPy整数JSON序列化错误已修复，失败预览与源码保留，零重训/重评分。静态源码检查保留16 PASS/2 WARN/3 FAIL原始结果：PDF项按用户范围不适用，导入的共用导出函数由实际SVG/PNG尺寸、dpi和可编辑文字检查补证，不将静态扫描声称为全通过。

窗口重叠（972与最终相交97日）、历史日期反复暴露、原日值as-of/预处理与B+旧优化未收敛限制保持。四点同一剖面、三个种子和多个重叠窗不构成多个独立案例，全部结果均为探索性条件位移预测，不代表真实预警效果。

- [冻结计划](ootang_transformer_origin_plan.v1.0.md)、[配置](../config/ootang_transformer_origin.v1_0.json)、[来源](ootang_transformer_origin_sources.v1.0.json)、[完整核验](ootang_transformer_origin_validation.v1.0.md)。
- [全部汇总CSV](../{cfg["out"]}/analysis/phase_summary.csv)、[逐点CSV](../{cfg["out"]}/analysis/metrics_by_point.csv)、[种子CSV](../{cfg["out"]}/analysis/seed_summary.csv)、[逐日CSV](../{cfg["out"]}/analysis/daily_predictions.csv)。
- [图件导航](../{cfg["figures"]}/README.md)、[最终回执](../{cfg["out"]}/final_receipt.json)。本地分步提交，未push，不制作PDF；独立17:47:02—19:17:02 UTC自限窗口含准备核验，实际总用时见回执，余额不转用。
"""
    (ROOT / "docs/ootang_transformer_origin_results.v1.0.md").write_text(report)
    write_json(out / "report_tables.json", tables)
    print("wrote report with", len(tables), "source-indexed tables")


if __name__ == "__main__":
    main()
