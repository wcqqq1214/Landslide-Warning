# 藕塘当前方法文献登记

更新日期：2026-09-01

## 范围

- 只登记实际支撑当前 ConvLSTM–NGBoost–SHAP 路线、分位数预测、区间校准或概率评价的来源。
- 只下载正文 PDF，未请求或下载 Supporting Information（SI）。
- 已失败、已退役、尚未实施的实验路线不进入当前论文参考文献或 PDF 批次。
- PDF 仅作本地原文核验，位于被 Git 忽略的 `literature/`；本文件保存可版本化的元数据、用途、来源、路径和校验值。

## 新增或补齐的当前方法来源

| 来源 | 当前方法中的作用与边界 | 标识符与合法来源 | 本地状态 |
|---|---|---|---|
| Shi et al. (2015), *Convolutional LSTM Network: A Machine Learning Approach for Precipitation Nowcasting*, NIPS 2015, pp. 802–810 | ConvLSTM 时空骨干的原始来源，对应 `code/convlstm/model.py:101`。当前实现省略原论文 peephole 项，并增加 P10/P50/P90 有序分位数头，属于项目适配而非原样复现。 | [NeurIPS 记录](https://proceedings.neurips.cc/paper_files/paper/2015/hash/07563a3fe3bbe7e3ba84431ad9d055af-Abstract.html)；arXiv:1506.04214 | `downloaded_verified`；12 页；`literature/method_sources/convlstm/PDFs/Convolutional_LSTM_Network_A_Machine_Learning_Approach_for_Precipitation_Nowcasting.pdf`；SHA-256 `6b83643820fa049a9cdf30621385bd5d33c67408d348a93edb6c59ed247986ef` |
| Shepard (1968), *A Two-Dimensional Interpolation Function for Irregularly-Spaced Data*, ACM '68, pp. 517–524 | 反距离加权插值的经典来源，对应 `code/convlstm/grid_interp.py:65` 及当前方法草稿的 `4×7` 高程网格。项目实现是归一化 `1/d^p` 的 Shepard-type IDW，不等同于原文完整的局部化、平滑化构造。 | DOI [10.1145/800186.810616](https://doi.org/10.1145/800186.810616)；[ACM 正式记录](https://dl.acm.org/doi/10.1145/800186.810616) | `metadata_only_no_lawful_oa_pdf`；ACM 正文接口返回 403，开放获取解析未找到可核验的授权仓储 PDF，不使用来源不明的镜像。 |
| Duan et al. (2020), *NGBoost: Natural Gradient Boosting for Probabilistic Prediction*, ICML 2020, PMLR 119:2690–2700 | NGBoost 概率提升框架的原始来源，对应五类 categorical distribution、LogScore、树基学习器和 natural gradient，见 `code/warning/ootang_ngboost_auto_state_classifier.py:376`。五警色、自动标签及固定参数均为本项目设定。 | [PMLR 记录及正文](https://proceedings.mlr.press/v119/duan20a.html)；arXiv:1910.03225 | `downloaded_verified`；11 页；`literature/method_sources/ngboost/PDFs/NGBoost_Natural_Gradient_Boosting_for_Probabilistic_Prediction.pdf`；SHA-256 `ba05d460d20c43e5eba6538a6a9b01ad5ac72a8d9e0178a48ed29274fc0888be` |
| Lundberg and Lee (2017), *A Unified Approach to Interpreting Model Predictions*, NIPS 2017, pp. 4765–4774 | SHAP 加性特征归因框架的原始来源。当前实现用 permutation SHAP 解释 site NGBoost 的期望五级输出，见 `code/warning/ootang_ngboost_auto_state_classifier.py:740`；不是 ConvLSTM-SHAP，也不支持物理因果结论。 | [NeurIPS 记录](https://proceedings.neurips.cc/paper_files/paper/2017/hash/8a20a8621978632d76c43dfd28b67767-Abstract.html)；arXiv:1705.07874 | `downloaded_verified`；10 页；`literature/method_sources/shap/PDFs/A_Unified_Approach_to_Interpreting_Model_Predictions.pdf`；SHA-256 `32518459c313a57876ec1646f3266d54569d3b9a70152dd4f28e742eef05a5f3` |
| Koenker and Bassett (1978), *Regression Quantiles*, *Econometrica* 46(1):33–50 | 回归分位数及非对称 check/pinball loss 的方法起源，对应 `code/convlstm/model.py:181`。当前非交叉 softplus 输出头和 conformal 调整不来自该文。 | DOI [10.2307/1913643](https://doi.org/10.2307/1913643)；[JSTOR 正式记录](https://www.jstor.org/stable/1913643) | `metadata_only_no_lawful_oa_pdf`；开放获取解析未找到可核验的合法 PDF，不使用来源不明的镜像。 |
| Koenker and Hallock (2001), *Quantile Regression*, *Journal of Economic Perspectives* 15(4):143–156 | 分位数回归和非对称加权绝对误差的权威可读说明；作为 1978 原始来源的核验辅助，不替代方法起源归属。 | DOI [10.1257/jep.15.4.143](https://doi.org/10.1257/jep.15.4.143)；[AEA 正式记录](https://www.aeaweb.org/articles?id=10.1257/jep.15.4.143) | `downloaded_verified`；14 页；作者机构站正文 `literature/method_sources/quantile_regression_review/PDFs/Quantile_Regression.pdf`；SHA-256 `f61b4daae9e5706448733f16091d09aea4ec5ba79dcbafcb54d11e898aa65d8a` |
| Romano, Patterson and Candès (2019), *Conformalized Quantile Regression*, NeurIPS 2019, pp. 3543–3553 | 当前 P10/P90 conformity score 与双端点调整的直接相近来源，对应 `code/convlstm/model.py:455`。必须写成 “CQR-inspired 的逐测点时间后置 split-conformal 调整”，不能写成完整复现。 | [NeurIPS 记录](https://papers.neurips.cc/paper_files/paper/2019/hash/5103c3584b063c431bd1268e9b5e76fb-Abstract.html)；arXiv:1905.03222 | `downloaded_verified`；11 页；`literature/method_sources/cqr/PDFs/Conformalized_Quantile_Regression.pdf`；SHA-256 `348d7f4ced8028de86d1c61f9c3da84e413cac2a0938c3dbc6ac62d21e994d97` |
| Gneiting, Balabdaoui and Raftery (2007), *Probabilistic Forecasts, Calibration and Sharpness*, *JRSS B* 69(2):243–268 | 支撑“在 calibration 前提下评价 sharpness”及覆盖率与宽度同时报告的原则；不提供项目的 qhat、PICP 简称或测点阈值。 | DOI [10.1111/j.1467-9868.2007.00587.x](https://doi.org/10.1111/j.1467-9868.2007.00587.x)；[作者机构站正文](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf) | `downloaded_verified`；26 页；`literature/method_sources/calibration_sharpness/PDFs/Probabilistic_Forecasts_Calibration_and_Sharpness.pdf`；SHA-256 `5ca1dc51db5fc619ed757bf1b79f3601408d579373c43d6dc06d601d51b271ea` |
| Gneiting and Raftery (2007), *Strictly Proper Scoring Rules, Prediction, and Estimation*, *JASA* 102(477):359–378 | interval score 的直接来源，对应 `code/convlstm/model.py:235`；该分数同时惩罚宽度和漏覆，但本身不是校准算法，也不单独提供 coverage guarantee。 | DOI [10.1198/016214506000001437](https://doi.org/10.1198/016214506000001437)；[作者机构站正文](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf) | `downloaded_verified`；20 页；`literature/method_sources/proper_scoring_rules/PDFs/Strictly_Proper_Scoring_Rules_Prediction_and_Estimation.pdf`；SHA-256 `d31a0c5f0ae8fec1a0a6544db5d056645b2d7296d71b44a2e8efb293c7d87ba2` |

## 已有的直接项目来源

- `literature/物理引导的阶跃型水库滑坡变形智能概率预测模型与预警方法研究.docx`：四项指标及相对等级结构的主要项目依据。
- `literature/一种改进的切线角及对应的滑坡预警判据_许强.pdf`：改进切线角指标来源。
- `literature/韦承谦_基于机器学习方法的水库滑坡位移预测及预警研究——以藕塘滑坡为例.pdf`：方法角色分工的论文先例，不作为当前算法已验证有效的证据。
- `literature/Journal of Geophysical Research  Machine Learning and Computation - 2025 - Wang - Enhancing Landslide Displacement.pdf`：藕塘空间拓扑和数据语义支持，不是当前核心算法的替代来源。

## CQR 表述边界

Romano et al. (2019) 的有限样本边际覆盖结果依赖 calibration/test 样本的 exchangeability。当前数据按时间排序且可能发生分布漂移，并且实现采用逐测点 qhat、非负截断和更保守的离散分位数约定，因此不能直接援引该理论保证，也不能宣称 8 个测点的联合覆盖。当前文稿应把它描述为 CQR 式或 CQR-inspired 调整，并继续以样本外 PICP、区间宽度、覆盖偏差和 interval score 报告实际结果。

## 明确排除

- v1“变点检测 + KMeans”自动标签路线及其论文：已失败并由 ECDF v2 取代。
- ACI、AgACI、SPCI 等已退役或未实施的校准分支及其论文。
- 只用于数据血缘核对、但未被当前算法借鉴的论文。
- Deng et al. (2021) 默认不进入当前参考文献；只有正文确实使用其“自动运动状态分类”动机时才重新核验，并且不能写成 ECDF、`H=7` 或空间综合规则的出处。

最终参考文献表只收录正文实际引用的条目，保持文内引用与参考文献零孤立项。
