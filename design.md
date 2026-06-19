# 代码设计文档 — 基于机器学习与多指标的滑坡自适应在线智能预警

> 目标:先跑通 code(不考虑论文结构)。主线复刻 liu2025 的四段框架,迁移到库岸阶跃型滑坡场景。
> 参考:liu2025(多指标融合预警框架)、许强 2009(改进切线角及预警判据)、吴爽爽(库岸/间歇式滑坡变形机制)。

## 1. 数据现状(已核对)

- 数据集:**三峡库区藕塘滑坡**(库岸阶跃型)。后续可能换数据集试验 —— 因此各脚本把数据加载路径与列名集中在文件顶部一处,换数据集时只改这一处。
- `data/monitoring_data.csv`:1461 行,日尺度,**2016-07-01 → 2020-06-30,0 缺失、0 日期断点**。
- 8 个位移测点(累计位移 mm,单调累积+阶跃):`MJ9 MJ1 MJ3 ATU1 ATU2 ATU3 ATU4 ATU5`。
- 诱发/环境因子:`Rainfall`(降雨)、`GWT`(地下水位)、`RWL`(库水位,145–175 m,4 年≈4 个消落周期)、`aveT/minT/maxT`(温度)、`DP`、`RH`。
- 场景判定:库岸阶跃型 / 间歇式滑坡,驱动为库水位涨落 + 降雨。

## 2. 与 liu2025 的映射 & 关键简化

liu2025 骨架四段:**数据预处理 → 多指标融合 → 自适应阈值 → 分类预警**。灾种由"煤矿瓦斯/顶板"换成"库岸滑坡",核心指标由 EMR/AE/微震换成位移派生量,辅助/驱动指标换成库水位速率与降雨。

**简化决定:跳过 liu2025 第一段(xLSTM 异常检测 + BayOTIDE 插补)。**
理由:本数据 0 缺失 0 断点,不为不存在的问题写代码。后续若要复刻,需像 liu2025 那样人工注入 3% 异常/缺失,届时再加,不属于跑通主线的必需。

## 3. 锁定的设计选择

| 决策点 | 选择 |
| --- | --- |
| NGBoost 分类标签来源 | **切线角自动打标**:用许强改进切线角阈值 45°/80°/85° → 4 级(等速 / 初加速 / 中加速 / 临滑) |
| 8 测点建模方式 | **多通道退化版**:8 点作为 8 个输入通道喂多变量 LSTM。严格意义非空间卷积;待测点平面坐标到位可升级真 ConvLSTM(IDW/克里金插值成规则网格) |
| ConvLSTM 预测目标 | **预测未来位移**,区间输出(分位数 pinball loss → P10/P50/P90) |
| 预测步长 horizon | 起步 1 天,参数可调(7/30) |
| 等速段速率 v̄ 估计 | 无人工标注 → 用整段一阶差分的稳健中位数近似;留参数可手动指定等速段区间 |

## 4. 管线与文件结构

四段拆成独立脚本放 `code/`,中间产物落 `data/`(派生特征)或 `models/`,图落 `figures/`。

```
1. code/features.py     位移速率 v / 加速度 a、改进切线角 α(先估等速段 v̄)、
                        RWL 速率、降雨多窗累积(7/15/30 天)
                        → 产出 data/features.csv
   verify: 特征表头打印 + 无 NaN + 切线角范围合理(0–90°)

2. code/shap_select.py  回归器(对位移速率建模)+ SHAP,量化 RWL 速率/降雨贡献
                        → 产出 figures/shap_summary.png
   verify: SHAP 图生成 + 打印 top 因子排序

3. code/convlstm.py     8 点位移作 8 通道喂多变量 LSTM,预测未来 horizon 天位移 + 区间
                        (pinball loss,P10/P50/P90)
                        → 产出 models/convlstm.pt、figures/forecast_interval.png
   verify: 测试集 RMSE 打印 + 区间覆盖率 ≈ 80% + 预测 vs 真值图

4. code/ngboost_warn.py 切线角标签(45/80/85° → 4 级)做 y,NGBoost 输出等级概率分布
                        → 产出 models/ngboost.pkl、figures/confusion_matrix.png
   verify: 混淆矩阵 + 各级概率分布图
```

## 5. Python 环境(uv 管理)

```bash
# 初始化(项目根目录)
uv init --python 3.10
uv add pandas numpy scikit-learn torch shap ngboost matplotlib

# 运行各段
uv run python code/features.py
uv run python code/shap_select.py
uv run python code/convlstm.py
uv run python code/ngboost_warn.py
```

torch 走 CPU 即可(数据量小)。

## 6. 跑通的成功标准

四个脚本依次无报错跑完,各自产出 verify 行的指标与图;`models/` 有保存的模型,`figures/` 有 SHAP 图 / 预测区间图 / 混淆矩阵。即视为 "code 跑通"。

## 7. 待升级项(非跑通必需)

- 真 ConvLSTM:需 8 测点平面坐标 + 空间插值。
- 预处理模块:人工注入异常/缺失后复刻 xLSTM + BayOTIDE。
- 等速段 v̄:接入专家划分的等速变形阶段区间替代中位数近似。

## 8. 跑通后的已知差距(非 bug,如实记录)

四段管线均无报错跑通,产物齐全。以下是当前实现的诚实短板,后续可针对性改进:

| 差距 | 现象 | 性质 | 可能的改进方向 |
| --- | --- | --- | --- |
| **区间校准偏窄** | convlstm 区间覆盖率 0.58 < 目标 0.80;P50 很准(测试集 RMSE 0.95 mm) | 分位数校准问题,非精度问题 | 增加训练轮次 / 小批量训练 / 用更宽的分位数(如 P05–P95)/ 训练后做保形预测(conformal)校准 |
| **类别不平衡** | 切线角 4 级样本悬殊:stable 112 / early-accel 973 / mid-accel 193 / critical 154;mid-accel 测试集仅 7 例,f1 仅 0.35 | 数据本身分布,藕塘大部分时间处初加速段 | 重采样 / 类权重 / 合并相邻稀疏等级 / 更长时段数据 |
| **切线角等速段近似** | v̄ 用整段速率中位数,非专家划分的等速变形阶段 | 无标注下的近似 | 接入专家划分区间(见第 7 节) |
| **多通道≠空间卷积** | convlstm 是多变量 LSTM,非真 ConvLSTM | 缺测点坐标 | 补坐标 + 插值成网格(见第 7 节) |

跑通阶段这些差距可接受;进入论文/精调阶段再逐项处理。

## 9. Python 环境(uv)落地说明

项目文件夹是跨系统挂载层(macOS 文件经 VM 挂载),不支持 uv 安装时的硬链接与
`.data` 目录删除操作(fonttools/numba 会失败)。因此 venv 不放项目目录,而放 VM 本机磁盘:

```bash
# 在 VM 内运行各脚本(venv 在 /tmp,需带环境变量)
export UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/tmp/.venv-landslide
uv run --no-sync python code/features.py
```

**在你本机(macOS 终端)则无此限制**,可直接把 venv 建在项目里、直接运行,见下方"本机运行"。
`pyproject.toml` + `uv.lock` 已在项目内,任何机器 `uv sync` 一行即可重建环境。
