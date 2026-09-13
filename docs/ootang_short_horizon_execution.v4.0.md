# v4.0 实施记录与 P1 执行合同

2026-09-13。依据 [v4.0 计划](ootang_short_horizon_comparison_plan.v4.0.md)，P0 提交 `3863847`。用户已要求“根据 plan，一步步进行”，现启动独立执行窗口 **13:37:17—17:37:17 UTC**（北京时间 21:37:17—次日 01:37:17）。P1 最迟 14:22:17 UTC；实现、训练、核验、图文报告与提交均计入，不恢复旧额度。

## P1：事前固定的实现选择

配置为 [ootang_short_horizon_comparison.v4_0.json](../config/ootang_short_horizon_comparison.v4_0.json)，已登记 30 个原始输入、参数与依赖来源哈希。配置只使用原前缀 B+ 参数，不重新标定。七个端点、四点、原日期、直接／残差配对、三种子、均值 MSE、共同概率校准和每 h 开发选择均按计划执行。

### 1. 物理原件和状态接口

实际核读 `manuscript.pdf` 第 9—11 页并目视，式 (15)—(25) 对应迟滞、不可逆滑移、三类反力与 64 子步离散。PDF 页树预检为 PASS，26 页一致；首次以模块方式导入结构检查器因其局部导入路径退出，改用其原 CLI 后成功，未涉及实验运行。

已将解压目录的 `physical_model.py`、`physical_solver.c` 与 `section2d_v4.zip` 原字节比较，完全一致。物理 PDF SHA-256 为 `9b2785eb9c1795afe66a380b0fa972c0c6370953a1febd33fcb68315bbe9c367`，ZIP 为 `57f3e7c68f004a47875c59ee93d38c384900b5263f111341fdd4aee36fdcc966`。原件保留。

原 Python `forward` 给出 `coordinates, plastic, basal_reaction, contact, bulk_reaction, background`；原 PDF 的 z 对应实现中的迟滞变量。采用 24 维完整状态：

```text
S = [z(4), p(4), rb(4), rc(4), rE(4), background(4)]
z = coordinates - background
```

原 `code/physics_guided/mechanics.c` 的 `day_forward` 枚举同样 16 种活动集、每日前进 64 子步；`day_backward` 可对前一完整状态求导。当前只学习起点状态，不学习力／水文参数，所以不要求旧接口提供力梯度；这不修复、也不掩盖其不能端到端学习外力的边界。

起点前完整状态由对应教师和实际已发生驱动递推。未来驱动按七日平均降雨／最后库水位生成，重算完整水文记忆和背景增量，再继续原离散力学。原 B+ 均值按当前观测锚定，原未锚定与已知未来驱动只作副表。原物理状态的各分量是模拟量，不称实测机制。

### 2. PINN：短窗状态多重射击约束

候选精确称为 **离散状态 PINN**，不是连续二维 PDE 全场 PINN。唯一待检验假设是：用最近观测估计起点地表迟滞状态的偏差，能否改善随后七天的预测；原 54 参数、塑性初值与反力记忆均保留。

网络的历史编码输入为 30 天合法特征的末日、最近七日平均、30 日平均；两层 32 单元 tanh 编码器。历史编码经 4 维头输出初始迟滞修正；每个未来 h 将历史编码与合法场景特征送入一层 32 单元 tanh 解码器，输出 20 个状态修正。背景分量固定为 B+，不是自由网络输出。

```text
S0* = S0_B + [a_z, 0, 0, 0, 0, 0]
Sh_NN = Sh_B + [a_h, 0_background]
mean_h = y[n-1] + obs @ ((z_h_NN + background_h) - (z_0* + background_0))
defect_h = Sh_NN - Day(Sh-1_NN, background_h, forcing_h)
```

`Day` 为原 64 子步互补解的一日映射；每个 h 的上一状态采用该网络实际给出的前一状态，h=1 用修正初值。此为日节点多重射击约束，不把相互独立的局部力摘要当成施力历史。批量反向只对初始／前日状态求导，水文和参数冻结。

各状态修正用 tanh 限幅到一个训练单位。20 个单位由当前训练前缀内物理预测状态相对起点的 RMS 变化确定，各自原单位的数值下限 `1e-6`；初始 z 修正用同一 z 单位。位移为 mm，反力沿原求解器的广义力单位，不把状态标准化解释为观测误差。末层零初始化，零输出必须回到 B_ANCHOR。

损失事前固定：

```text
PINN_EQ: normalized_displacement_MSE
         + 100 * mean((defect_first20 / state_unit)^2)
         + 100 * mean(relu(-(p_h-p_h-1))^2 / plastic_unit^2)
         + 0.01 * mean((a_z / z_unit)^2)
PINN_NOEQ: 同结构、同位移项和初值正则，去掉两个物理约束项。
```

权重 100、0.01 与限幅一个单位是本次工程默认，不是物理识别结果；不扫描权重。NOEQ 仍保留 B+ 参考特征、状态表示与背景，所以比较只解释显式方程／不可逆性损失的作用；它不称“完全无物理信息”。NOEQ 是诊断对照，不冒充 PINN 家族代表或共同达标主推荐。

训练后独立从 `S0*` 逐日完整 C 递推，与神经主输出比较；不得用该 C 轨迹替换主输出。物理核验分别要求：

- 零修正回退最大位移差不超过 `1e-8 mm`，沿用既有求解器比较容差。
- 标准化日映射缺陷最大绝对值不超过 `1e-3`；神经塑性日增量不小于 `-1e-8 mm`。
- 每 h 的神经／C 位移最大差不超过 `min(0.01 mm, 0.1 × 当前训练前缀 DRIFT1 该 h 四点平均 RMSE)`。
- 梯度有限，固定活动分支的数值差分检查通过；实际 C 日步可行性沿用原 `x>=-1e-8、gap>=-1e-7` 检查。

这是独立神经近似预算，不是修改旧求解器容差。任何一项未过仍保留该臂的神经预测和误差，标注“物理近似未过”，不进入共同达标推荐。单纯数值检查过门也不证明效果或初始状态可辨识。

### 3. C16 的保留方式

主比较复用已冻结、仅用历史位移与成熟监督的 C8 DATA 核心均值和原 C16 的响应单位；CORE 反馈用原历史单位，并以原 C16_CORE 保存均值作回放一致性检查。这些 h 独立的均值规则不需要重训 30 日模型。

PHYS 使用本轮主场景重新发出的 B+ 一日预测误差，其特征 RMS 从当前阶段以前的合法场景预测计算；核心一日误差单位和响应单位仍沿原来源。初始误差头为零、alpha=1、不遗忘，只在目标兑现后更新。区间统一使用新共同校准器，原 C8 第一份 sigma 仅是内部响应单位，不冒称本轮概率尺度。

C16 为固定强基线，开发／后期均完成；没有原 C16 内部选择段时，不制造其独立内选成绩。共同校准的开发初始池按计划用合法 DRIFT1 补足，后期可以使用本轮开发已发误差。这一初始化差异和 PHYS 场景变化均需在结果中披露。旧 C16/C18 原生概率只作历史表。

### 4. 输入、选择与运行接口

ConvLSTM 复用原 25 个历史通道及 8 个未来场景通道的定义，后者全部由起点前资料和固定驱动场景生成；h 特征为 h/7。直接和残差两臂用同一训练前缀归一化、直接目标 RMS 单位。纯 RR 特征在配置中逐项固定；两臂的 alpha 按同一个内部配对分数确定。

所有神经模块使用 CPU float64，避免七天小误差与大累计位移相减时被 float32 输出舍入主导；这是统一数值约定，不改变优化器、轮数或均值目标。最终均值先合并三个种子，再用该均值的成熟误差校准。单个种子也另存。

P2 实现以下入口后才能使用，P1 当前没有运行它们：

```bash
PYTHONPATH=code .venv/bin/python -m short_horizon.run --config config/ootang_short_horizon_comparison.v4_0.json --phase prepare
PYTHONPATH=code .venv/bin/python -m short_horizon.run --config config/ootang_short_horizon_comparison.v4_0.json --phase inner
PYTHONPATH=code .venv/bin/python -m short_horizon.run --config config/ootang_short_horizon_comparison.v4_0.json --phase development
PYTHONPATH=code .venv/bin/python -m short_horizon.run --config config/ootang_short_horizon_comparison.v4_0.json --phase later
PYTHONPATH=code .venv/bin/python -m short_horizon.verify --config config/ootang_short_horizon_comparison.v4_0.json
```

开发逐 h 锁定均值／概率领先者和共同达标推荐；后期不能改名单。内部检查点分数使用内部 DRIFT1，禁止误用开发分母。保留七个端点全部合法起点和七目标完整的共同掩码。

## P1 状态

数学接口、输入场景、C16 复用边界、精度、配置和原件来源已明确。尚未运行物理前向或训练；来源哈希与合同检查后提交 P1，接着实现并核验，而非先宣称新模型可用或有效。
