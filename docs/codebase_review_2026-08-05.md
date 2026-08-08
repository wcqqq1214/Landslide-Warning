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

## 6. 实施与 Git 记录（完成于 2026-08-08）

| 提交 | 内容 |
| --- | --- |
| `4c279e0` | 保存本次代码库审查基线 |
| `e375d9c` | 将本地 44 个测试文件全部纳入 Git，并修正两条过期断言 |
| `130f8db` | 固定小型 Ruff 正确性规则集 `E7/E9/F` |
| `f4a108e` | 将默认入口收窄为三段最小链，并补齐 7 个 legacy 阶段的 8 个 sidecar 契约 |
| `5ac9c36` | 将总管线 manifest 升级为 schema 3，增加逐输入指纹和工作树状态 |
| `ce83345` | 消除 v2/v3 对未跟踪 Wang 论文 PDF 的运行时硬依赖；保留声明指纹，并在本地副本存在时 fail-closed 核验 |
| `3c3fa94` | 补齐错误本地 PDF 的拒绝测试，并明确当前 v2 历史 manifest 尚未刷新新增字段 |

终审发现：默认 v3 和完整测试曾隐式依赖被 `literature/*` 忽略的 Wang 论文 PDF，因此本机通过不等于新克隆可复现。修复后，计算所需的 O1/O2/O3 拓扑、DOI、页/图定位和已审查 SHA-256 仍由受 Git 管理的 profile 锁定；本地 PDF 不再是计算输入，但若存在且指纹错误，运行会明确拒绝。只含 `git ls-files`、不含该 PDF 的临时副本中，20 项 v2/v3 相关测试全部通过。v3 重建没有改变阈值、测点时间线、滑坡体时间线或图像，只更新 4 份来源/级联血缘 manifest；没有执行 ConvLSTM 训练，也没有读取或启动 Vajont。

## 7. 最终门禁与结论

- 全量：`361 passed, 52 subtests passed`。
- Ruff：`uv run --with ruff ruff check main.py code tests` 通过。
- 编译：`uv run python -m compileall -q main.py code tests` 通过。
- 默认 dry-run：精确为 `features → convlstm → ootang-operational-v3`。
- 差异：`git diff --check` 通过；最终工作树只保留用户原有、未跟踪的 `review.md` 和 Vajont xlsx。

本轮最终状态为 P0=0、P1=0。仍保留两个非阻断 P2：`operational_run.py` 体量较大，以及 `site_fusion.py` 是未来可删除候选。前者拆分会触及冻结产物血缘，后者仍有测试和文档历史证据，因此均不在本轮删除。结论是“需要收窄入口和修复可复现性，但不需要大规模清理过期代码”。
