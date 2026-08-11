# Landslide-Warning

基于机器学习方法的水库滑坡位移预测与预警研究代码仓库。当前以三峡库区藕塘滑坡日尺度监测数据为例，已跑通从特征工程、概率位移预测、独立 SHAP 探索到四指标融合的**非正式工程原型链路**；正式阈值、确认性验证和工程预警尚未完成。

> 2026-08-11 v4 收口：导师确认逐点加速度计算方法，用户授权本轮沿用速度相对带作为加速度阈值；默认入口切换到 `features → convlstm → ootang-operational-v4`。v4 保留 v3 双轴空间逻辑，v3 数值快照仍为 explicit-only 对照。原始 GNSS 确认无法取得，因此门禁仍为 `prototype_run_gate=allowed` 与 `confirmatory_evidence_gate=blocked`：这是 Figshare 物化序列上的藕塘内部探索性诊断，不是独立原始 GNSS 上的确认性预测或正式预警。

## 当前状态

| 模块 | 当前状态 |
| --- | --- |
| 藕塘最小初跑管线 | `features → convlstm → ootang-operational-v4`；v4 加入严格逐点加速度并复用 v3 双轴空间规则，v3/v2 快照保留 |
| v4 规则诊断图 | 已生成代表日图、514 日完整等级图，以及 8 点累计位移 + interval/velocity/acceleration/tangent/fused 联合图；均属观测后非正式审计 |
| 高程感知 ConvLSTM | `elev_m` 经测点标准化和水平 IDW 后作为静态输入通道；当前共 7 个输入通道 |
| ConvLSTM 时间验证 | 当前 7 通道 fixed-120 已完成 3 个扩展窗口折和 `seed=0-4` 的 15 个折—种子拟合；未选最佳种子 |
| ConvLSTM 后续诊断 | 7 通道早停与容量敏感性未运行；根目录的早停/容量结果是历史 6 通道产物，不能当作 7 通道证据 |
| NGBoost 状态分类 | 当前为动态 V0 当日状态识别，不是未来 onset 预警 |
| ConvLSTM / SHAP 分工 | 用户已批准原型采用其毕业论文的分离式模型角色：ConvLSTM 单独输出 P10/P50/P90 并评价覆盖；独立 NGBoost+SHAP 分析候选模型依赖，不是 ConvLSTM-SHAP；这是对导师 R3 的当前解释，尚无导师验收记录 |
| 四指标透明融合 | v4 以区间、速度/切线角运动学 family、严格逐点加速度和原始 `ΔV` 审计字段运行；运行用 V0 只是项目比较器，指定 Word 的稳定段/V0 仍未解决 |
| 未来 onset | 已生成标签和事件清单；当前仅 3 个互不相连的可预测标签事件 |
| 数据血缘 | 原始锚点和生成链不可取得；工程初跑允许，确认性证据与正式预警继续阻断 |
| 工程可复现性 | v4 核心产物为 4,112 条测点记录、514 条滑坡体记录和 8 行阈值；加速度 green/blue/yellow/orange/red=`4012/98/2/0/0`；默认入口和输入指纹已固定 |

## 快速运行

项目使用 `uv` 管理依赖，Python 版本为 3.10。

```bash
uv sync
uv run python main.py
```

无参数默认只执行 `features → convlstm → ootang-operational-v4`。该最小链路不会启动 Vajont，也不会运行遗留 NGBoost、旧融合、v1/v2/v3 或滚动/多种子诊断阶段。使用 `--list` 查看全部阶段；其他阶段必须用 `--stage` 显式选择。运行会把提交哈希、工作树状态、源码指纹、逐阶段输入/输出 SHA-256、状态和耗时写入 schema 3 的 `figures/pipeline/latest_run.json`。已有清单可能是历史快照，解释结果时必须同时核对其提交和源码指纹。

复现当前 7 通道 fixed-120 诊断时，只选择已冻结的滚动和五种子阶段：

```bash
uv run python main.py \
  --stage convlstm-rolling \
  --stage convlstm-seeds \
  --manifest figures/pipeline/convlstm_elevation_fixed120_v1_run.json
```

该命令不运行早停或容量敏感性。Vajont 尚未启动，后续读取、适配或运行都必须先获得用户明确许可。

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

`main.py` 编排 17 个可独立选择的阶段；无参数入口只运行当前藕塘最小链，其余历史复现和诊断阶段均为 explicit-only。各阶段声明输入和输出，并为历史预警阶段声明 `legacy_warning_manifest.json` sidecar；管线会在执行前检查输入是否存在，并在执行后检查预期产物是否更新，阶段失败时立即停止。模块边界见 `docs/design.md`。

## 主要结果入口

| 文件 | 内容 |
| --- | --- |
| `docs/framework.md` | 研究框架、验证规则和报告边界 |
| `docs/design.md` | 代码架构和模块边界 |
| `docs/ootang_elevation_prototype_run.md` | 2026-07-30 高程感知初跑方法、结果、完整性与边界 |
| `docs/ootang_convlstm_elevation_fixed120_review.md` | 7 通道 fixed-120 三折与五种子的完整性、误差、动态响应和区间审查 |
| `docs/ootang_elevation_warning_expert_review.md` | 高程可信性、400 个未确认状态、典型日和空间规则专家审查 |
| `docs/ootang_stage_results_package.md` | 藕塘原型的统一阶段结论、可写/不可写边界与后续数据决策入口 |
| `docs/ootang_operational_run.md` | v1/v2/v3/v4 规则、加速度公式、双轴字段、独立产物目录与非正式边界 |
| `figures/warning_operational_draft_v4/ootang_v4_typical_days.svg` | v4 代表日的 interval/velocity/acceleration/tangent/fused 证据与双轴空间诊断图 |
| `figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg` | 514 日 × 8 点候选状态及滑坡体整体确认/局部最高双轴 |
| `figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg` | 8 点累计位移、区间/速度/加速度/切线角和最终候选等级联合诊断 |
| `docs/results_report.md` | 当前完整探索性结果和科研表述边界 |
| `docs/codebase_review_2026-08-05.md` | 代码审查、过期代码处置、Git 提交记录和最终工程门禁 |
| `docs/ootang_data_lineage_expert_review.md` | 藕塘发布日序列来源、数值指纹与数据闸门 |
| `figures/README.md` | 每个 PNG/CSV 的用途和保留原则 |

## 当前结论边界

- 7 通道 fixed-120 的五种子结果不支持“跨时期稳定优于持久性基线”：fold 1/2 的 RMSE 和 MAE 在 5/5 种子上均更差；fold 3 在 5/5 种子上仅小幅改善，平均 RMSE skill 约 `0.036`。
- fold 3 的平均预测增量标准差只有实际增量的约 `0.156`，平均增量相关为 `-0.041`，仅 1/5 种子为正；点误差小幅优势主要伴随强平滑，不能解释为稳定跟踪逐日加减速。
- 校准后 P10–P90 coverage 在 fold 1/2/3 约为 `0.387/0.956/0.754`，没有跨时段稳定达到名义 80%。本轮不根据已查看的 test 结果调节高程尺度、网络或区间参数。
- 历史 6 通道与当前 7 通道的事后对照只能描述版本表现变化，不能证明高程的因果贡献；历史 6 通道早停和容量结果也不能外推到当前模型。
- 高程增加的是静态地形先验和结构可解释性，不自动增加预测证据等级；指定 Word 的物理引导来自稳定性计算和半经验物理位移，不是静态高程或 ConvLSTM。
- 当前 400 个未空间确认日全部数据完整，均因 yellow+ 证据只位于 O1；v2 的全局最少有效点门禁已修复，v3/v4 又把整体确认色与局部最高候选分轴。514 日中整体 green/blue/yellow/orange/red 为 `8/48/31/9/18`，另有 400 日不发布整体颜色；8 个 green 日仍保留 `localized_blue_attention`。
- v4 加速度单项等级计数为 `4012/98/2/0/0`（green/blue/yellow/orange/red）；该计数只是冻结规则在物化序列上的工程审计，不是现场验证的预警性能。NGBoost 当前识别的是当日动态 V0 状态；本任务未完成 NGBoost，留出段没有 orange/red 样本，不能评价高等级预警召回。
- 模型分工已由用户确认，并有其毕业论文中 LightGBM+SHAP 与 LSTM 概率预测分离的方法先例；当前 SHAP 解释的仍是独立 NGBoost，不是 ConvLSTM，其遗留同日 V0 分类标签也不是本轮正式五级预警输出。毕业论文以多次 LSTM 独立训练形成分布，当前项目采用分位数 ConvLSTM，二者只共享角色分工，不是同一不确定性算法；该先例也不用于改写指定 Word 的阈值或融合规则。
- SHAP 结果描述模型依赖关系，不代表致灾因果关系或预警提前量。
- 项目特有 V0 比较器和切线角计算链已跑通，但严格 MVIF 仍未识别出可接受初始稳定段，指定 Word 的正式 `V/σ/V0` 尚未获得；切线角参考等速阶段也未由导师或现场资料确认。
- 当前数据已被多轮探索使用；最终投稿需要新增时段、外部滑坡或其他确认性验证支持。
- 当前输入不能称为已验证的独立原始逐日 GNSS。若论文最终选择其他可追溯数据集，应在新数据上重新冻结切分、阈值和验证协议；藕塘当前只承担初步工程案例角色。
- 7 通道早停与容量敏感性未运行；Vajont 未启动，必须经用户明确许可才能开始。
