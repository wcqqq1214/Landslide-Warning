# Landslide-Warning

基于机器学习与多指标融合的滑坡预警实验项目。当前代码以三峡库区藕塘滑坡日尺度监测数据为输入，完成特征工程、驱动因子解释筛选、ConvLSTM 位移区间预测和 NGBoost 预警等级分类。

> 说明:本 README 按当前代码实现整理。`design.md` 和 `framework.md` 是设计/思路记录，其中 `design.md` 里关于 `convlstm.py` 仍是“多通道退化版”的部分描述已经滞后；当前代码已通过 `grid_interp.py` 将 8 个测点 IDW 插值为规则网格，再进入 ConvLSTM。

Framework 指标覆盖、当前模型结果和改进优先级见 `docs/framework_status.md`。

## 目录结构

```text
.
├── main.py                         # uv 初始化生成的模板入口,当前未编排业务管线
├── pyproject.toml                  # Python 3.10 + 依赖声明
├── uv.lock                         # uv 锁定文件
├── design.md                       # 较完整的方案设计与阶段记录
├── framework.md                    # 论文/方法框架草稿
├── code/
│   ├── features.py                 # 管线第 1 段:特征工程
│   ├── warning_thresholds.py       # 测点动态 V0 阈值和四级判级
│   ├── shap_select.py              # 管线第 2 段:SHAP 因子筛选
│   ├── grid_interp.py              # 测点坐标读取 + IDW 网格插值
│   ├── convlstm.py                 # 管线第 3 段:ConvLSTM 位移区间预测
│   └── ngboost_warn.py             # 管线第 4 段:NGBoost 预警分类
├── data/
│   ├── monitoring_data.csv         # 原始日尺度监测数据
│   ├── monitoring_data.xlsx        # 原始数据 Excel 版本
│   ├── station_coords.csv          # 8 个位移测点平面坐标
│   └── features.csv                # features.py 生成的派生特征表
├── models/
│   ├── convlstm.pt                 # ConvLSTM 模型权重
│   └── ngboost.pkl                 # NGBoost 分类模型
└── figures/
    ├── convlstm/
    │   ├── forecast_interval.png   # 位移预测区间图
    │   └── forecast_metrics.csv    # 各测点预测指标
    ├── shap/
    │   ├── shap_reg_summary.png    # NGBoost 回归 SHAP 因子贡献图
    │   ├── shap_cls_summary.png    # NGBoost 预警分类 SHAP 因子贡献图
    │   ├── shap_reg_importance.csv # 回归 mean absolute SHAP 排序
    │   ├── shap_cls_importance.csv # 分类 mean absolute SHAP 排序
    │   ├── shap_model_metrics.csv  # SHAP 阶段 NGBoost 验证指标
    │   └── v0_thresholds.csv       # SHAP 阶段三测点动态 V0
    └── ngboost/
        ├── confusion_matrix.png    # 预警分类混淆矩阵
        └── v0_thresholds.csv       # 最终分类阶段三测点动态 V0
```

## 数据结构

### 原始数据

`data/monitoring_data.csv`

- 行数:1461 行。
- 时间范围:2016-07-01 到 2020-06-30。
- 频率:日尺度。
- 核心列:
  - `Date`:日期。
  - `MJ9/mm`, `MJ1/mm`, `MJ3/mm`, `ATU1/mm` 到 `ATU5/mm`:8 个累计位移测点。
  - `Rainfall/mm`:降雨量。
  - `RWL/m`:库水位。
  - `GWT/m`, `aveT/℃`, `minT/℃`, `maxT/℃`, `DP`, `RH`:其他环境因子。

### 测点坐标

`data/station_coords.csv`

- `station`:测点名。
- `disp_col`:与 `data/features.csv` 中位移列对应的列名。
- `x_m`, `y_m`:平面坐标,单位 m。
- `elev_m`:高程,单位 m。

`code/convlstm.py` 会调用 `code/grid_interp.py` 读取坐标,并按 `DISP_COLS` 顺序对齐位移列和测点坐标。

### 派生特征

`data/features.csv`

- 行数:1432 行。
- 由 `code/features.py` 生成。
- 每个位移测点生成:
  - `*_disp`:累计位移。
  - `*_v`:位移速率。
  - `*_a`:位移加速度。
  - `*_alpha`:许强改进切线角。
- 驱动因子生成:
  - `RWL`:库水位。
  - `RWL_rate`:库水位变化速率。
  - `Rain`:当日降雨。
  - `Rain_cum7`, `Rain_cum15`, `Rain_cum30`:7/15/30 日累计降雨。

## 模块职责

| 文件 | 输入 | 输出 | 主要职责 |
| --- | --- | --- | --- |
| `code/features.py` | `data/monitoring_data.csv` | `data/features.csv` | 计算位移速率、加速度、改进切线角、库水位速率和多窗口累计降雨 |
| `code/warning_thresholds.py` | 原始累计位移 | 动态 V0 阈值和逐日四级标签 | 使用训练期 30 天月速率、90% 分位加速月剔除和 `V0 = 1.5 V_bar + 2 sigma` 计算测点独立阈值 |
| `code/shap_select.py` | `data/monitoring_data.csv` | `figures/shap/shap_reg_summary.png`, `figures/shap/shap_cls_summary.png` | 构造 5 天滞后样本,用 NGBoost 回归/分类并通过 SHAP 解释位移增量和动态 V0 预警状态 |
| `code/grid_interp.py` | `data/station_coords.csv` | 内存中的 `H x W` 网格 | 读取 8 个测点坐标,构建规则网格,提供 IDW 插值函数 |
| `code/convlstm.py` | `data/features.csv`, `data/station_coords.csv` | `models/convlstm.pt`, `figures/convlstm/forecast_interval.png`, `figures/convlstm/forecast_metrics.csv` | 将 8 测点位移插值为 `4 x 7` 网格,训练 ConvLSTM 输出 P10/P50/P90 位移预测区间 |
| `code/ngboost_warn.py` | `data/features.csv`, `data/monitoring_data.csv` | `models/ngboost.pkl`, `figures/ngboost/confusion_matrix.png` | 按三测点动态 V0 标签训练 NGBoost 输出预警等级概率 |

## 执行流程

```mermaid
flowchart TD
    A["data/monitoring_data.csv<br/>原始监测数据"] --> B["code/features.py<br/>特征工程"]
    B --> C["data/features.csv<br/>派生特征表"]
    C --> D["code/shap_select.py<br/>SHAP 因子筛选"]
    A --> D
    D --> E["figures/shap/<br/>SHAP 图、重要性和指标"]
    C --> F["code/convlstm.py<br/>位移区间预测"]
    G["data/station_coords.csv<br/>测点坐标"] --> H["code/grid_interp.py<br/>IDW 网格插值"]
    H --> F
    F --> I["models/convlstm.pt"]
    F --> J["figures/convlstm/<br/>预测区间和指标"]
    C --> K["code/ngboost_warn.py<br/>预警等级分类"]
    A --> K
    K --> L["models/ngboost.pkl"]
    K --> M["figures/ngboost/<br/>混淆矩阵和动态阈值"]
```

推荐按下面顺序运行:

1. `features.py` 先从原始数据生成统一特征表。
2. `shap_select.py` 基于原始监测表构造 5 天滞后样本,用 NGBoost + SHAP 分析位移增量和动态 V0 预警状态的贡献因子。
3. `convlstm.py` 基于特征表中的 8 测点位移和测点坐标,训练位移区间预测模型。
4. `ngboost_warn.py` 基于三测点独立动态 V0 阈值生成四级标签,训练概率分类模型。

## 运行方式

项目使用 `uv` 管理依赖,Python 版本为 3.10。

首次准备环境:

```bash
uv sync
```

依次运行完整管线:

```bash
uv run python code/features.py
uv run python code/shap_select.py
uv run python code/convlstm.py
uv run python code/ngboost_warn.py
```

如果已经使用仓库内 `.venv`,也可以直接运行:

```bash
.venv/bin/python code/features.py
.venv/bin/python code/shap_select.py
.venv/bin/python code/convlstm.py
.venv/bin/python code/ngboost_warn.py
```

当前 `main.py` 只会打印模板文本,不会执行上述管线。

## 各阶段关键逻辑

### 1. 特征工程

`code/features.py` 的配置集中在文件顶部:

- `DATA_CSV`:原始数据路径。
- `OUT_CSV`:派生特征输出路径。
- `DISP_COLS`:8 个累计位移列。
- `RWL_COL`, `RAIN_COL`:库水位与降雨列。
- `RAIN_WINDOWS`:累计降雨窗口,当前为 7/15/30 天。

处理步骤:

1. 读取原始监测数据,按 `Date` 排序。
2. 对每个位移测点计算累计位移、速率、加速度和改进切线角。
3. 计算库水位速率。
4. 计算多窗口累计降雨。
5. 删除差分和滑窗造成的头部不完整行。
6. 输出 `data/features.csv` 并打印形状、日期范围、列名、NaN 数量和切线角范围。

### 2. SHAP 因子筛选

`code/shap_select.py` 参考论文中的 5 天滑动窗口,对 `MJ9`, `MJ1`, `MJ3` 构造位移和环境因子的滞后样本。模型按 `framework.md` 使用 NGBoost。

回归目标为每日位移增量。分类目标为测点 30 天月速率是否达到自身动态 `V0`，即黄色及以上预警状态。

候选因子包括 5 天历史位移、降雨、库水位、地下水位、气温、露点、相对湿度和测点 one-hot 标识。

处理步骤:

1. 从 `data/monitoring_data.csv` 读取原始监测数据。
2. 用训练期前 80% 数据计算各测点 `V0 = 1.5 V_bar + 2 sigma`，并生成 30 天月速率动态标签。
3. 训练 `NGBRegressor` 预测位移增量。
4. 训练 `NGBClassifier` 预测预警状态概率。
5. 使用模型无关 SHAP permutation explainer 计算贡献值。
6. 输出 `figures/shap/shap_reg_summary.png`, `figures/shap/shap_cls_summary.png`, `figures/shap/shap_reg_importance.csv`, `figures/shap/shap_cls_importance.csv`, `figures/shap/shap_model_metrics.csv` 和 `figures/shap/v0_thresholds.csv`。
7. 在终端打印回归和分类的 mean absolute SHAP top10。

### 3. IDW 网格插值

`code/grid_interp.py` 为 ConvLSTM 提供空间输入:

1. `load_coords()` 读取 `data/station_coords.csv`。
2. `build_grid()` 按测点包围盒构建规则网格,当前大小为 `4 x 7`。
3. `make_interpolator()` 预计算 IDW 权重。
4. 插值函数将形状为 `(T, N)` 的测点位移序列转换为 `(T, H, W)` 的网格序列。

### 4. ConvLSTM 位移区间预测

`code/convlstm.py` 的核心目标是预测未来位移增量,再还原为绝对位移区间。

关键配置:

- `THESIS_WINDOWS = {"MJ1": 2, "MJ9": 7, "MJ3": 2}`:参考论文中的测点预测窗口。
- `LOOKBACK = 7`:当前 ConvLSTM 使用论文窗口中的最大值作为统一输入窗口。
- `HORIZON = 1`:预测未来 1 天。
- `TRAIN_FRAC = 0.8`:前 80% 时间序列作为训练段。
- `QUANTILES = [0.1, 0.5, 0.9]`:输出 P10/P50/P90 区间。
- `GRID_H = 4`, `GRID_W = 7`:来自 `grid_interp.py`。

处理步骤:

1. 读取 `data/features.csv` 中 8 个测点位移。
2. 读取测点坐标并构建 IDW 插值器。
3. 只用训练段统计量做标准化,避免时序泄漏。
4. 将测点位移插值为规则网格序列。
5. 构造滑动窗口,目标为未来位移增量。
6. 用 pinball loss 训练 ConvLSTM 分位数预测模型。
7. 在测试段输出 P10/P50/P90 区间。
8. 保存模型到 `models/convlstm.pt`,保存图到 `figures/convlstm/forecast_interval.png`。
9. 保存各测点指标到 `figures/convlstm/forecast_metrics.csv`。
10. 打印 RMSE、MAE、persistence 基线、P10-P90 区间覆盖率和分位数交叉统计。

### 5. NGBoost 预警等级分类

`code/ngboost_warn.py` 对 `MJ9`, `MJ1`, `MJ3` 分别计算动态 V0，并按 30 天月速率判级:

| 等级 | 名称 | 条件 |
| --- | --- | --- |
| 0 | `green` | `V < V0` |
| 1 | `yellow` | `V0 <= V < 5V0` |
| 2 | `orange` | `5V0 <= V < 10V0` |
| 3 | `red` | `V >= 10V0` |

当天整体预警等级取三个测点中的最高等级。每个测点的 V0 只使用训练期数据计算；三点均值仅可用于汇报，不参与判级。

模型输入特征包括:

- 8 测点位移速率的均值和最大值。
- 8 测点加速度的均值和最大值。
- `RWL`, `RWL_rate`, `Rain_cum7`, `Rain_cum15`, `Rain_cum30`。

处理步骤:

1. 读取 `data/features.csv` 和 `data/monitoring_data.csv`。
2. 计算三测点独立动态 V0，并生成每日整体最高预警等级。
3. 构造统计特征和驱动因子特征。
4. 按时间顺序切分训练集和测试集。
5. 训练 `NGBClassifier`。
6. 输出 `models/ngboost.pkl`, `figures/ngboost/confusion_matrix.png` 和 `figures/ngboost/v0_thresholds.csv`。
7. 打印各等级样本数、测试集准确率和分类报告。

## 当前注意事项

- `README.md` 描述当前代码状态,不是论文最终方案。
- `main.py` 当前不是项目入口；真实执行入口是 `code/` 下 4 个脚本。
- `data/features.csv`, `models/*`, `figures/*` 都是可再生成产物。
- 如果更换数据集,优先修改各脚本顶部的 CONFIG 区,尤其是列名、数据路径和测点坐标。
- ConvLSTM 依赖 `data/station_coords.csv`;坐标列和 `DISP_COLS` 顺序必须对齐。
- 当前动态 V0 标签中只有绿色和黄色样本；橙色、红色规则保留，但当前数据没有对应训练样本。
