# 代码库整体审查与收敛记录（2026-08-05）

> 审查基线：`main@b4df226`。本次按用户要求审查当前完整代码快照，不重新评判已冻结的科学阈值、模型结果或论文结论，也不启动 Vajont。

## 1. 范围与门禁

- 范围：`main.py`、`code/**/*.py`、`tests/**/*.py`、配置、运行清单契约及直接相关文档。
- 目标：识别错误入口、不可复现测试、过期代码和可安全收敛项。
- 原则：历史代码只有在无生产、测试、文档和产物血缘依赖时才删除；否则保留并移出默认入口。
- Git：每个产生变更的步骤单独提交，测试随对应修复提交，不使用 `test:` 提交。

## 2. Standards 轴

### P1

1. [`.gitignore`](../.gitignore) 忽略整个 `tests/`。本地 44 个测试文件中只有 29 个受 Git 管理；15 个核心测试共 3705 行，仅存在本地。新克隆无法复现完整门禁。
2. [`main.py`](../main.py) 无参数运行默认选择 14 个阶段，混合当前藕塘链路、历史 V0/融合、v1/v2/v3 和昂贵的滚动/五种子诊断，容易覆盖不同证据版本。
3. 总管线 manifest 只记录输入路径，不记录逐阶段输入 SHA-256；`source_sha256` 也不能替代数据和配置指纹。

### P2

1. 仓库没有固定 Ruff 规则集；当前工具默认全规则产生 302 项风格/复杂度提示，而聚焦 `F401/F811/F821/F841` 的正确性检查通过。应固定小而可执行的基线，不在本轮机械改写全仓库。
2. [`operational_run.py`](../code/warning/operational_run.py) 同时承载三代 profile 校验、融合、I/O 和 manifest，复杂度较高；拆分会触及冻结产物，本轮不做。

## 3. Spec 轴

### P1

1. 未跟踪的完整测试集与[`行动计划`](advisor_review_action_plan.md)要求的可复现工程门禁不一致。
2. 无参数默认 14 阶段与 README 已声明的当前最小链 `features → convlstm → ootang-operational-v3` 不一致。
3. 历史脚本会写 `legacy_warning_manifest.json`，但 `main.py` 没有把 sidecar 纳入相应阶段输出契约；旧/新产物隔离不能由总管线完整验收。

### P2

1. `figures/pipeline/latest_run.json` 是历史快照，不应被解释为当前代码的最新运行证据；本轮不重跑模型，保留并明确边界。
2. 当前 v3 仍是 `formal_warning_output=false` 的原型草案；代码清理不得把它重标为正式 `F/F_site`。

## 4. 过期代码判断

| 类别 | 结论 |
| --- | --- |
| legacy warning 整组 | 保留作历史复现，但退出默认入口 |
| operational v1/v2 入口 | 保留快照与显式重跑能力，但退出默认入口 |
| `operational_run.py`、`operational_v2_fusion.py` | v3 直接依赖，不能删除 |
| stable-segment、MVIF、Bai–Perron 诊断 | 当前 draft evidence bundle 直接依赖，不能删除 |
| `formal_warning.py` | 正式输出 fail-closed 安全门禁，不能删除 |
| MVIF initial-slope 退休路径 | 协议要求保留历史复现，本轮不迁移 |
| `site_fusion.py` | 无当前生产导入，但仍是行动计划中的历史审计证据；列为后续删除候选，本轮不删 |

## 5. 本轮收敛步骤

1. 让完整测试集进入 Git，并修正两条与现行实现不一致的旧断言。
2. 固定最小 Ruff 正确性规则集。
3. 将无参数默认入口收窄到当前藕塘最小链；其他阶段保留为 explicit-only，并补齐 legacy sidecar 输出契约。
4. 为总管线 manifest 增加逐阶段输入指纹和工作树状态。
5. 运行全量测试、Ruff、编译和 dry-run，回填结果后推送 `main`。

本轮不拆分大型科研模块、不迁移历史 6 通道产物、不删除历史复现代码，也不重新训练模型。
