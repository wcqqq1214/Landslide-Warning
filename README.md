# Landslide-Warning

基于机器学习方法的水库滑坡位移预测与预警研究代码仓库。当前以三峡库区藕塘滑坡日尺度监测数据为例，完成从特征工程、位移预测、状态分类、SHAP 解释、动态阈值到多指标融合预警的端到端流程。

> 2026-07-30 已按导师要求完成藕塘高程感知初跑。原始 GNSS 确认无法取得，因此门禁拆分为 `prototype_run_gate=allowed` 与 `confirmatory_evidence_gate=blocked`：允许用 Figshare 发布物化日序列和 `station_coords.csv` 完成内部工程案例，但不将其表述为独立原始 GNSS 上的确认性预测或正式预警。

## 当前状态

| 模块 | 当前状态 |
| --- | --- |
| 藕塘最小初跑管线 | `features → convlstm → ootang-operational-v2` 已在同一运行清单中通过 |
| 高程感知 ConvLSTM | `elev_m` 经测点标准化和水平 IDW 后作为静态输入通道；当前共 7 个输入通道 |
| 既有 ConvLSTM 诊断 | 滚动验证、五种子、早停和容量敏感性产物来自加入高程前的 6 通道版本，暂作为历史诊断，不代表当前模型已完成同范围复验 |
| NGBoost 状态分类 | 当前为动态 V0 当日状态识别，不是未来 onset 预警 |
| SHAP 解释 | 独立 NGBoost 的探索性 SHAP：留出样本、时间折/测点分层稳定性和特征组消融均可审计；不是 ConvLSTM-SHAP |
| V0/切线角融合 | 8 个测点均进入融合；切线角等速阶段仍需导师或现场资料确认 |
| 未来 onset | 已生成标签和事件清单；当前仅 3 个互不相连的可预测标签事件 |
| 数据血缘 | 原始锚点和生成链不可取得；工程初跑允许，确认性证据与正式预警继续阻断 |

## 快速运行

项目使用 `uv` 管理依赖，Python 版本为 3.10。

```bash
uv sync
uv run python main.py \
  --stage features \
  --stage convlstm \
  --stage ootang-operational-v2
```

该最小链路不会启动 Vajont，也不会运行遗留 NGBoost/旧融合阶段。运行会把提交哈希、源码指纹、各阶段状态、耗时和产物 SHA-256 写入 `figures/pipeline/latest_run.json`。

## 代码结构

```text
.
├── main.py                  # 统一管线入口
├── code/                    # 按流程分组的特征、预警、解释和 ConvLSTM 脚本
│   ├── features/            # 特征工程、切线角和等速阶段复核
│   ├── warning/             # V0 阈值、事件、NGBoost、融合和敏感性分析
│   ├── explainability/      # SHAP 分析和稳定性验证
│   └── convlstm/            # ConvLSTM 预测模型及滚动/稳定性/容量诊断
├── data/                    # 发布物化序列、工程坐标和派生特征
├── models/                  # 可再生成的模型文件
├── figures/                 # 可再生成的图表、指标和审计表
└── docs/                    # 研究框架、代码设计、结果报告和限制说明
```

## 管线阶段

`main.py` 支持完整研究管线和显式选择的最小阶段链。各阶段声明输入和输出；管线会在执行前检查输入是否存在，并在执行后检查预期产物是否更新，阶段失败时立即停止。模块边界见 `docs/design.md`。

## 主要结果入口

| 文件 | 内容 |
| --- | --- |
| `docs/framework.md` | 研究框架、验证规则和报告边界 |
| `docs/design.md` | 代码架构和模块边界 |
| `docs/ootang_elevation_prototype_run.md` | 2026-07-30 高程感知初跑方法、结果、完整性与边界 |
| `docs/ootang_elevation_warning_expert_review.md` | 高程可信性、400 个未确认状态、典型日和空间规则专家审查 |
| `docs/results_report.md` | 当前完整探索性结果和科研表述边界 |
| `docs/ootang_data_lineage_expert_review.md` | 藕塘发布日序列来源、数值指纹与数据闸门 |
| `figures/README.md` | 每个 PNG/CSV 的用途和保留原则 |

## 当前结论边界

- 当前高程感知单次初跑在最后 287 日物化留出段的总体 RMSE 为 `0.338 mm`，持久性基线为 `0.340 mm`，RMSE skill 仅 `0.007`；属于流程跑通，不构成明显性能优势。
- 与加入高程前的同一单种子快照相比，高程版本总体 RMSE 从约 `0.318 mm` 增至 `0.338 mm`。本轮不根据已查看的 test 结果调节高程尺度、网络或阈值。
- 高程增加的是静态地形先验和结构可解释性，不自动增加预测证据等级；指定 Word 的物理引导来自稳定性计算和半经验物理位移，不是静态高程或 ConvLSTM。
- 当前 400 个未空间确认日全部数据完整，均因 yellow+ 证据只位于 O1；v2 的 514 日 site 输出没有 green，且有效点门禁尚未覆盖非绿色分支，下一步先修复空间规则而不调整模型。
- NGBoost 当前识别的是当日动态 V0 状态；留出段没有 orange/red 样本，不能评价高等级预警召回。
- SHAP 解释的是独立 NGBoost，不是 ConvLSTM；其遗留同日 V0 分类标签也不是本轮正式五级预警输出。
- SHAP 结果描述模型依赖关系，不代表致灾因果关系或预警提前量。
- V0 和切线角规则已跑通，但切线角参考等速阶段尚未由导师或现场资料确认，不能写成确认性切线角结论。
- 当前数据已被多轮探索使用；最终投稿需要新增时段、外部滑坡或其他确认性验证支持。
- 当前输入不能称为已验证的独立原始逐日 GNSS。若论文最终选择其他可追溯数据集，应在新数据上重新冻结切分、阈值和验证协议；藕塘当前只承担初步工程案例角色。
