# Landslide-Warning

基于机器学习方法的水库滑坡位移预测与预警研究代码仓库。当前以三峡库区藕塘滑坡日尺度监测数据为例，完成从特征工程、位移预测、状态分类、SHAP 解释、动态阈值到多指标融合预警的端到端流程。

> 当前分支已将位移预测阶段切换为官方 CNN-Mamba 实现；位移预测结果需要在 WSL/Linux + NVIDIA CUDA 环境重新生成。

## 当前状态

| 模块 | 当前状态 |
| --- | --- |
| 统一管线 | `main.py` 串联 13 个阶段，当前分支将位移预测阶段切换为 CNN-Mamba |
| CNN-Mamba 位移预测 | 已接入官方 `mamba-ssm` CUDA 实现，输出独立写入 `models/cnn_mamba.pt` 和 `figures/cnn_mamba/` |
| NGBoost 状态分类 | 当前为动态 V0 当日状态识别，不是未来 onset 预警 |
| SHAP 解释 | 已输出单次 SHAP、五折稳定性和特征组消融 |
| V0/切线角融合 | 8 个测点均进入融合；切线角等速阶段仍需导师或现场资料确认 |
| 未来 onset | 已生成标签和事件清单；当前仅 3 个可预测独立事件 |

## 快速运行

项目使用 `uv` 管理依赖，Python 版本为 3.10。

```bash
uv sync --torch-backend cu128 --no-build-isolation
uv run python main.py
```

完整运行会把提交哈希、源码指纹、各阶段状态、耗时和产物 SHA-256 写入 `figures/pipeline/latest_run.json`。当前 CNN-Mamba 阶段依赖官方 `mamba-ssm` CUDA 扩展，macOS 本机不适合跑完整位移预测阶段。

## 代码结构

```text
.
├── main.py                  # 统一管线入口
├── code/                    # 按流程分组的特征、预警、解释和 CNN-Mamba 脚本
│   ├── features/            # 特征工程、切线角和等速阶段复核
│   ├── warning/             # V0 阈值、事件、NGBoost、融合和敏感性分析
│   ├── explainability/      # SHAP 分析和稳定性验证
│   └── cnn_mamba/           # CNN-Mamba 预测模型及滚动/稳定性/容量诊断
├── data/                    # 原始数据、测点坐标和派生特征
├── models/                  # 可再生成的模型文件
├── figures/                 # 可再生成的图表、指标和审计表
└── docs/                    # 研究框架、代码设计和限制说明
```

## 管线阶段

`main.py` 默认运行 13 个阶段。各阶段声明输入和输出；管线会在执行前检查输入是否存在，并在执行后检查预期产物是否更新，阶段失败时立即停止。模块边界见 `docs/design.md`。

## 主要结果入口

| 文件 | 内容 |
| --- | --- |
| `docs/framework.md` | 研究框架、验证规则和报告边界 |
| `docs/design.md` | 代码架构和模块边界 |
| `figures/README.md` | 每个 PNG/CSV 的用途和保留原则 |

## 当前结论边界

- 旧位移预测产物已从当前 Mamba 分支删除；当前 CNN-Mamba 分支需要在 WSL/Linux + NVIDIA CUDA 环境重新生成位移预测指标。
- NGBoost 当前识别的是当日动态 V0 状态；留出段没有 orange/red 样本，不能评价高等级预警召回。
- SHAP 结果描述模型依赖关系，不代表致灾因果关系。
- V0 和切线角规则已跑通，但切线角参考等速阶段尚未由导师或现场资料确认，不能写成确认性切线角结论。
- 当前数据已被多轮探索使用；最终投稿需要新增时段、外部滑坡或其他确认性验证支持。
