"""Render fixed descriptive research tables; never select or train a model."""

from pathlib import Path
import pandas as pd
from . import core as c


def main():
    cfg, o = c.spec(), c.o
    c.guard()
    root = c.ROOT / cfg["out"]
    o.verify_lock(root / "diagnostic_v1/lock.json")
    out = root / "report_tables"
    out.mkdir(exist_ok=False)
    source = {}

    def read(name):
        p = root / "diagnostic_v1" / (name + ".csv")
        source[name] = o.sha(p)
        return pd.read_csv(p, float_precision="round_trip", dtype={"seed": str})

    issued = read("issued_summary")
    points = read("issued_by_point")
    weights = read("supervision_weights")
    training = read("training_summary")
    forecasts = read("checkpoint_forecast_summary")
    visibility = read("visibility_sensitivity")
    tail = read("tail_sse")
    mse = read("mse_decomposition")
    tables = {}

    def table(name, headers, rows):
        frame = pd.DataFrame(rows, columns=headers)
        frame.to_csv(out / (name + ".csv"), index=False)
        tables[name] = dict(
            headers=headers,
            rows=len(frame),
            numeric_cells=(len(headers) - 1) * len(frame),
        )

        def fmt(v):
            return f"{v:.6f}" if isinstance(v, float) else str(v)

        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] + ["---:"] * (len(headers) - 1)) + " |",
        ]
        lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows]
        return (
            f"<!-- table:{name} -->\n"
            + "\n".join(lines)
            + f"\n<!-- endtable:{name} -->"
        )

    def scalar(frame, metric, **filters):
        for key, value in filters.items():
            frame = frame[frame[key] == value]
        assert len(frame) == 1, (metric, filters, len(frame))
        return float(frame.iloc[0][metric])

    full = table(
        "full_rmse",
        ["方法", "792历史窗", "972历史窗", "1168最终探索"],
        [
            [m]
            + [
                scalar(issued, "rmse", origin=n, method=m, seed="ensemble", bin="all")
                for n in cfg["primary_origins"]
            ]
            for m in cfg["methods"]
        ],
    )
    bins = table(
        "kin_distance",
        ["前缀", "1—30日", "31—90日", "91—180日", "181—293日", "完整293日"],
        [
            [n]
            + [
                scalar(
                    issued,
                    "rmse",
                    origin=n,
                    method="TiDE_KIN",
                    seed="ensemble",
                    bin=b["name"],
                )
                for b in cfg["bins"]
            ]
            for n in cfg["primary_origins"]
        ],
    )
    rows = []
    for n in cfg["origins"]:
        w = weights[(weights.origin == n) & (weights.step == 400)]
        rows.append(
            [
                n,
                n - 432,
                int(w[(w.seed == "0") & (w.horizon == 293)].iloc[0].eligible_count),
                float(
                    w[(w.seed == "0") & (w.horizon >= 181)].expected_loss_weight.sum()
                    * 100
                ),
            ]
            + [
                float(
                    w[(w.seed == str(s)) & (w.horizon >= 181)].actual_loss_weight.sum()
                    * 100
                )
                for s in cfg["seeds"]
            ]
        )
    support = table(
        "supervision_support",
        [
            "前缀",
            "合法起点数",
            "完整293日起点数",
            "尾段期望权重%",
            "种子0实际%",
            "种子1实际%",
            "种子2实际%",
        ],
        rows,
    )
    rows = []
    for n in cfg["origins"]:
        row = [n]
        for frame in [training, forecasts]:
            row += [
                float(
                    frame[
                        (frame.origin == n)
                        & (frame.bin == "all")
                        & (frame.step == e)
                        & (frame.seed != "ensemble")
                    ].rmse.mean()
                )
                for e in [200, 400]
            ]
        row += [
            scalar(forecasts, "rmse", origin=n, step=e, seed="ensemble", bin="all")
            for e in [200, 400]
        ]
        rows.append(row)
    fit = table(
        "checkpoint_fit",
        [
            "前缀",
            "训练e200",
            "训练e400",
            "发报种子均值e200",
            "发报种子均值e400",
            "发报集成e200",
            "发报集成e400",
        ],
        rows,
    )
    vis = table(
        "visibility",
        ["前缀", "d30变化", "d90变化", "d180变化", "d293变化"],
        [
            [n]
            + [
                float(
                    visibility[
                        (visibility.origin == n)
                        & (visibility.seed == "ensemble")
                        & (visibility.visible == d)
                    ].rms_change.mean()
                )
                for d in cfg["visibility"]
            ]
            for n in cfg["origins"]
        ],
    )
    rows = []
    for p in cfg["points"]:
        rows.append(
            [
                p,
                scalar(
                    points,
                    "rmse",
                    origin=1168,
                    method="TiDE_KIN",
                    seed="ensemble",
                    bin="all",
                    point=p,
                ),
                scalar(
                    points,
                    "rmse",
                    origin=1168,
                    method="BPLUS_CONTINUOUS",
                    seed="ensemble",
                    bin="all",
                    point=p,
                ),
                scalar(
                    points,
                    "bias",
                    origin=1168,
                    method="TiDE_KIN",
                    seed="ensemble",
                    bin="h181_293",
                    point=p,
                ),
                scalar(
                    points,
                    "rmse",
                    origin=1168,
                    method="TiDE_KIN",
                    seed="ensemble",
                    bin="h181_293",
                    point=p,
                ),
                100
                * scalar(
                    points,
                    "coverage90",
                    origin=1168,
                    method="TiDE_KIN",
                    seed="ensemble",
                    bin="all",
                    point=p,
                ),
                100
                * scalar(
                    points,
                    "coverage90",
                    origin=1168,
                    method="TiDE_KIN",
                    seed="ensemble",
                    bin="h181_293",
                    point=p,
                ),
                100
                * scalar(
                    tail,
                    "tail_fraction",
                    origin=1168,
                    method="TiDE_KIN",
                    seed="ensemble",
                    point=p,
                ),
            ]
        )
    final = table(
        "final_points",
        [
            "点",
            "KIN全窗RMSE",
            "B+全窗RMSE",
            "KIN尾段偏差",
            "KIN尾段RMSE",
            "KIN全窗90%覆盖%",
            "KIN尾段90%覆盖%",
            "尾段SSE占全窗%",
        ],
        rows,
    )
    claims = {
        "early_kin": scalar(
            issued,
            "rmse",
            origin=792,
            method="TiDE_KIN",
            seed="ensemble",
            bin="h001_030",
        ),
        "early_drift": scalar(
            issued, "rmse", origin=792, method="DRIFT1", seed="ensemble", bin="h001_030"
        ),
        "uniform_tail_percent": 113 / 293 * 100,
        "mj1_972_seed_mse": scalar(
            mse, "mean_seed_mse", origin=972, method="TiDE_KIN", bin="all", point="MJ1"
        ),
        "mj1_972_ensemble_mse": scalar(
            mse, "ensemble_mse", origin=972, method="TiDE_KIN", bin="all", point="MJ1"
        ),
        "mj3_final_spread_percent": 100
        * scalar(
            mse,
            "ensemble_reduction_fraction",
            origin=1168,
            method="TiDE_KIN",
            bin="all",
            point="MJ3",
        ),
        "first_coverage90": 100
        * scalar(
            issued,
            "coverage90",
            origin=792,
            method="TiDE_KIN",
            seed="ensemble",
            bin="all",
        ),
        "first_width90": scalar(
            issued, "width90", origin=792, method="TiDE_KIN", seed="ensemble", bin="all"
        ),
    }
    doc = f"""# 回到原 TiDE_KIN：距离与跨时段诊断 v1.0

原TiDE_KIN的固定诊断已完整完成。**第一窗存在早期偏差，最终窗的困难集中在尾段；远距离训练权重偏低与整条未来输入的依赖同时存在，但尚不能确定哪一项造成跨窗不稳定。** 本轮没有新训练、没有改善后的新模型，也没有重选旧检查点。

用户授权回到TiDE_KIN后，按[冻结计划](ootang_tide_kin_diagnostic_plan.v1.0.md)在新分支`codex/tide-kin-horizon-diagnostic`开展三项诊断。原110392参数、180日历史、三种子、e400主结果及四点不变；使用给定未来293日降雨/水位的条件预测，路径中无实测位移反馈。612只作启动诊断，792/972/1168为三个原主窗。所有日期已暴露且窗口重叠，新增解释全部探索性。

## 原成绩与分距离误差

下表保持三种子均值预测的四点平均RMSE（mm）：先逐点开方，再平均四点，不能与合池RMSE混用。原发报精确保留；全部六方法、四窗、三个种子及集成、四点和五个固定距离集合见[逐点CSV](../{cfg["out"]}/diagnostic_v1/issued_by_point.csv)与[汇总CSV](../{cfg["out"]}/diagnostic_v1/issued_summary.csv)。完整MAE、CRPS、80/90/95%区间评分/覆盖/宽度均保留。

{full}

DRIFT1是固定最后一天速度外推：`预测(h)=最后位移+h×(最后位移−前一天位移)`，没有可训练参数。RR_COND为同条件任务的普通岭回归，G_CACHED是既有GRU流程；此表不是骨干单因素比较。

下表仅把原KIN主结果拆到预先固定距离段，单位mm。

{bins}

第一历史窗前30日KIN平均RMSE已为 **{claims["early_kin"]:.6f} mm**，DRIFT1为 **{claims["early_drift"]:.6f} mm**。因而“失败全在181—293日，只补远期监督就能解决”与现有误差不符；这也不证明远期监督无关。第一窗偏差、后两窗长距离误差需要分开验证，不能只看最终平均分。四点详图见[分距离误差PNG](../{cfg["figures"]}/v1/error_by_distance.png)。

## 远距离获得的监督与权重

原训练起点m在[432,n)，成熟长度L=min(293,n−m)。每个起点先对自身成熟距离取均值；距离h的期望损失系数为`mean_m(1[h≤L]/L)`。下表尾段指181—293日，实际系数复算原400次更新×每批8个起点，种子全部列出。

{support}

如果各距离等权，113日尾段占 **{claims["uniform_tail_percent"]:.6f}%**；原三个主窗只有约14.26%、22.36%、26.68%。原始目标因此更偏重近期，但这些是损失系数，不是最终参数梯度，也不是独立样本数。较多完整起点与较好成绩跨窗共现不能识别原因，因为历史分布、标准化和教师也同时变化。612没有181—293日直接监督，不能把缺失填成零误差。

各种子在h293的采样次数/唯一支持起点、每个距离含监督的更新次数及五个原检查点的权重全部保存于[17580行权重表](../{cfg["out"]}/diagnostic_v1/supervision_weights.csv)。图见[监督权重PNG](../{cfg["figures"]}/v1/supervision_weights.png)。

## 训练拟合、发报与种子抵消

完整60份原检查点均重放全部合法训练起点的成熟目标。下表训练/发报种子均值都先计算各模型四点平均RMSE，再平均三个种子；发报集成另列，单位mm。训练样本是在各前缀内拟合，发报是完整293日外推，两者日期及距离支持不同。

{fit}

第一窗从e200到e400，训练拟合继续下降，而原发报种子均值和集成RMSE均上升；后两窗则均下降。这个模式不足以宣称所有TiDE都过拟合、训练已经收敛，或继续增加更新必然有效。本轮保留e400，不按回顾成绩改用e50/e200。五个检查点与全部种子见[检查点PNG](../{cfg["figures"]}/v1/checkpoint_fit.png)。

逐点检查了`MSE=偏差²+误差总体方差`及`种子平均MSE=集成MSE+种子预测离散平方均值`。第二历史窗MJ1的种子平均MSE为 **{claims["mj1_972_seed_mse"]:.6f} mm²**，集成为 **{claims["mj1_972_ensemble_mse"]:.6f} mm²**：集成好不等于每个种子都好。最终MJ3的种子离散项仅占种子平均MSE的 **{claims["mj3_final_spread_percent"]:.6f}%**，相近预测仍有共同偏差，说明这个点不能主要靠消除随机种子波动解释。两例只说明不同模式，全部点/窗/方法的分解均保存在[MSE分解CSV](../{cfg["out"]}/diagnostic_v1/mse_decomposition.csv)，不据例子选种子或点。

## 未来可见范围依赖

固定e400权重、历史位移、标准化和B+教师，只保留前d日未来驱动/K，之后标准化值清零、known=0，距离编码保留，H一直关闭。下表是**相对完整293日情景的预测变化RMS**，先逐点计算共同h1…d上的变化，再平均四点；不是对实测的误差，不作为候选评分。

{vis}

完整d293严格零变化；第一窗缩短可见范围时，共同早期输出存在明显变化。这支持进一步检查训练部分可见情景与发报完整情景的关系，但不能据此称晚期驱动有害或标签泄露：原任务允许给定全路径协变量，改变可见范围同时改变了条件信息。不同d使用不同共同日期集合，表中曲线不构成等距离剂量效应。全部三种子/四点/四前缀条件数组与指标均保留；见[可见性PNG](../{cfg["figures"]}/v1/visibility_sensitivity.png)。

## 尾段偏差和概率限制

下表保留最终窗四点。尾段仍为181—293日，偏差=预测−实测；RMSE/偏差单位mm，覆盖和SSE份额单位%。SSE份额使用逐点平方误差，不从四点平均RMSE反推。

{final}

最终MJ3尾段持续低估，尾段约占其全窗平方误差的98%，90%区间覆盖仅约15%。三个点全窗覆盖仍低于原80%工作门槛，不能凭平均RMSE低于B+就宣布达标。第一历史窗KIN的90%平均覆盖 **{claims["first_coverage90"]:.6f}%** 伴随平均宽度 **{claims["first_width90"]:.6f} mm**，高覆盖也不是自动可靠。

原概率层保持上一发出路径第91—180日90条误差，最终来源[1062,1152)、距当前前缀16日。原612没有sigma，所有概率单元保持不可用，没有补建误差池或用未来标签重新校准。90条相邻误差不是独立重复，高斯边际区间也不保证293日同时覆盖。原KIN对B+的均值/概率联合门仍为0/3，详见[原分组结论](ootang_tide_features_results.v1.0.md)；本轮不定义新通过门槛。

## 收束与下一步

本轮排除了“只看远期样本少即可解释全部失败”“所有好成绩代表种子稳定”“继续增加训练就会跨窗稳定”这三种过强解释，尚未锁定唯一机制。

下一项值得冻结的**单一假设**是：在TiDE_KIN中，预测第h天时隔离h之后的协变量输入，能否缓解早期输出对整条未来路径的依赖，同时保留后两窗收益？建议首先只改变这项输入传递规则，保持K/历史、原目标与损失权重、训练样本、种子、预算、原概率层及完整293日评价。应先验证“修改h之后驱动不改变h之前输出”的数值不变性，再报告全部窗/点/种子；如实现必须改变参数量或解码接口，须在新版本计划中明确，不冒充单因素。这个限制是预测接口设计，不是严格满足B+方程或识别物理因果。

这只是由本轮提出的后续候选，尚未实现或训练。距离均衡损失应留作另一项配对，避免同时改输入依赖和权重；也不追加H、RL、λ、结构扫描、轮数或事后最佳检查点。原CAL/HCAL和融合负结果保持，当前H附加校正路线不重开。

## 执行、核验和边界

283来源、60检查点、1816个原训练起点—前缀组合及27240次起点评价、48条可见性种子路径全部完成；新拟合/更新/B+拟合/物理前向均0。独立NumPy重算45766892个数值，最大差1.1641532182693481e-9；四图16面板/152曲线/21088个源与图形值、字体/对齐/碰撞及逐图目视通过。11项统计解释检查见[解释记录](../{cfg["out"]}/statistical_interpretation_audit.json)，过程与来源见[核验](ootang_tide_kin_diagnostic_validation.v1.0.md)。

准备时误要求612存在概率文件，冻结前已修正并留档；正式诊断、数值审计和制图失败重试均0。图形静态检查4项WARN为格式/解析及种子图示提示，全部解释并通过实际渲染检查，未改科学数据。原as-of/预处理、物理拟合未收敛、历史≤792教师与最终1168教师差异、972沿用792教师、180日并非完整神经历史、跨窗重叠和反复暴露等限制继续保留。

独立窗口为2026-09-15 **17:27:16—18:27:16 UTC**，含准备与核验；实际完成见[最终回执](../{cfg["out"]}/final_receipt.json)。本地分步commit，Markdown/CSV/PNG/SVG研究产物，无push/PR/PDF汇报；旧预算及本轮余额不转用。实施和核验完成，未产生新的效果改善结论，用户/导师尚未验收。
"""
    path = c.ROOT / "docs/ootang_tide_kin_diagnostic_results.v1.0.md"
    assert not path.exists()
    path.write_text(doc)
    o.write_json(out / "tables.json", tables)
    o.write_json(out / "claims.json", claims)
    o.write_json(
        out / "source_code.json",
        dict(
            path="code/tide_kin_diagnostic/report.py",
            sha256=o.sha(Path(__file__)),
            source_tables=source,
            historical_references={
                "docs/ootang_tide_features_results.v1.0.md": o.sha(
                    c.ROOT / "docs/ootang_tide_features_results.v1.0.md"
                )
            },
        ),
    )
    o.lock(
        out, "lock.json", list(out.glob("*")), status="rendered awaiting document audit"
    )
    print(dict(tables=len(tables), path=str(path)), flush=True)


if __name__ == "__main__":
    main()
