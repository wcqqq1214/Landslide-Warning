# 历史预警路径与正式入口隔离清单

> 状态：执行边界清单，不新增任何阈值、`V0`、融合函数或预警结果。
>
> 依据：[导师行动计划](advisor_review_action_plan.md)、[藕塘四指标数据字典](ootang_warning_data_dictionary.md)与[`ootang-five-level-rule-v1`草案协议](../config/ootang_warning_protocol.v1.draft.json)。
>
> 数据血缘更新（2026-07-28）：下列所有路径都以发布物化日序列为输入，`data_gate=blocked`。即使其仓库内时间切分和 manifest 完整，也不能升级为独立原始逐日 GNSS 的确认性结果。

## 1. 当前入口

`main.py` 是研究管线和历史/探索性复核入口，不是正式预警入口。其运行清单固定写入：

```text
warning_pipeline_scope=research_legacy_and_operational_draft_only
formal_warning_output=false
```

并对历史预警相关阶段写入 `warning_artifact_scope=legacy_exploratory`，对藕塘实施版阶段写入 `warning_artifact_scope=operational_draft`。因此，`main.py` 的成功运行只说明相应研究、历史复核或非正式实施脚本完成，不能说明本轮四指标五级正式预警已经生成。

正式路径的唯一代码接缝为[`code/warning/formal_warning.py`](../code/warning/formal_warning.py)中的 `run_formal_warning(protocol_path=...)`。它先调用 `require_frozen_protocol()`：协议仍为 `draft` 或仍含未决项时，抛出 `ProtocolNotFrozenError`。即使未来协议冻结，当前也会抛出 `FormalWarningExecutorUnavailableError`，因为尚无经过审查的四指标时间线执行器；入口不接受任意 callable，故不能把下表的旧融合塞入“正式”路径。

## 2. 历史与草案产物的允许用途

| 路径 / 产物 | 实际方法与当前标识 | 允许用途 | 明确禁止的用途 |
| --- | --- | --- | --- |
| [`warning_thresholds.py`](../code/warning/warning_thresholds.py) / `figures/thresholds/v0_thresholds.csv` | 30 日位移增量、四级 `V0` 路径。新导出的阈值行带 `warning_path=legacy_exploratory`、`formal_warning_output=false`、`warning_method_id=legacy_30_day_v0_four_level_primary_secondary_fusion`。 | 复核历史 onset、NGBoost、SHAP 与敏感性产物。 | 替代指定 Word 的逐点日速度、MVIF 初始稳定斜率输入、式（5-3）或五级速度规则。 |
| [`warning_fusion.py`](../code/warning/warning_fusion.py) / `figures/warning_fusion/warning_fusion.csv` | 旧 `V0` 主判、切线角只升级、NGBoost 仅作旁证的主副融合。新生成 CSV 同样带上述三个非正式字段，并在同目录写出 `legacy_warning_manifest.json`。 | 逐日审计旧规则为何给出某一等级。 | 作为四指标 `F`、滑坡体 `F_site` 或本轮正式综合预警。 |
| `onset_analysis.py`、`ngboost_warn.py`、`shap_select.py`、`shap_stability.py`、`sensitivity_analysis.py`、`tangent_stage_review.py` | `main.py` 中均标为 `legacy_exploratory`，并各自在输出目录写出 `legacy_warning_manifest.json`。新生成的表格还附加同一组非正式字段；模型、PNG 等非表格产物由 sidecar 绑定，`models/ngboost.pkl` 另在其同目录写出 sidecar。 | 研究诊断、历史可复核性和局限性说明。 | 将模型概率、F1、事件盘点、敏感性一致率或候选阶段升级为本轮正式风险结论。 |
| [`rule_fusion.py`](../code/warning/rule_fusion.py)、[`site_fusion.py`](../code/warning/site_fusion.py)与`figures/warning_draft/*` | 四指标/多测点草案和输入审计；各自产物已显式为 `draft` 或 `formal_warning_output=false`。 | 为冻结 `F`、`F_site`、容差与缺失规则准备可复算证据。 | 因为存在候选实现就生成正式五级预警。 |
| [`draft_evidence.py`](../code/warning/draft_evidence.py) / `figures/warning_draft/ootang_draft_warning_evidence_manifest.json` | 只重建当前七份有效的藕塘草案诊断，并逐份核验相同 draft 协议内容指纹、未决项、输出哈希和 `formal_warning_output=false`；已退役的 MVIF profile 候选不在 bundle 内。 | 在冻结规则前复现并审查同一版草案证据集。 | 将 bundle 误称为正式运行、把原始速度 KMeans 对照或 Bai--Perron 草案升级为 Word `V0` 实现、生成任何融合/时间线或启动 Vajont。 |
| [`operational_run.py`](../code/warning/operational_run.py) / `figures/warning_operational_draft/ootang_operational_run_manifest.json` | v1 导师复核用、可替换的藕塘四指标实施版；以 JSON 范围表和数值字段执行 fit-only KMeans 对照基线、`V0±σ`、选段 `ΔV` MAD 与透明两项支撑规则。 | 保留首次完整跑通的可比快照。 | 称为正式预警、称 KMeans 为 Word-MVIF `V0`、用 test 期反选参数、将 `uncorroborated` 并入 green，或启动 Vajont。 |
| [`operational_v2_fusion.py`](../code/warning/operational_v2_fusion.py) / `figures/warning_operational_draft_v2/ootang_operational_run_manifest.json` | v2 非监督空间证据族实施版；将速度/切线角作为一个运动学证据族，`ΔV` 只标记加速性。O1/O2/O3 拓扑由 Wang 等（2025，DOI `10.1029/2025JH000592`）PDF 第 7 页图 4(a,d) 与 5.2 节提供来源，并在 manifest 核验文件指纹；blue 仅在最高候选为 blue 时可见，yellow--red 采用跨区支撑且未确认者不降为 blue。每份 CSV/manifest 固定 `operational_draft_not_formal`、`formal_warning_output=false`、`vajont_used=false`。 | 审计测点候选、空间覆盖与跨区确认，和 v1 对照规则影响。 | 称为指定 Word 的 Logistic 融合或正式 `F/F_site`，将候选未确认状态当作 green/传感器缺失，调参使用 test，或启动 Vajont。 |

已存档的历史 CSV 可能早于新增字段和 sidecar；它们仍按本表和原始输出路径解释为历史快照。重新运行相应历史脚本后，新 CSV/sidecar 会写入上述标识；这不改变其中的历史数值或使其成为正式结果。

## 3. 对本轮开发的约束

1. 未冻结的 `stable_segment_selection`、三个蓝/近零容差、`F`、`F_site` 与不规则切线角处理继续由协议门禁拦截；本清单不补写任何数值。
2. 指定 Word 论文优先于藕塘毕业论文。旧 30 日、四级、主副指标路径只保留为历史可复核材料。
3. Vajont 未列入任何入口、输入或产物；其启动仍须用户明确授权。
