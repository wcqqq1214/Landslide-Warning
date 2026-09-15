"""Produce a concise Chinese report from locked, independently verified tables."""
import pandas as pd
from . import core as c
o=c.old
NAMES={c.B:'改进B+',c.BA:'仅起点校正B+','DRIFT1':'DRIFT1','RR_COND':'岭回归RR_COND',
       'G00_RAW_UNIFORM':'G00 原样','G10_ANCHOR_UNIFORM':'G10 起点表达',
       'G01_RAW_BOUNDARY':'G01 边界采样','G11_ANCHOR_BOUNDARY':'G11 两项合用'}


def main():
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];o.verify_lock(root/'analysis_lock.json')
    for path in ['independent_audit_v2/receipt.json','figure_qa_v4/receipt.json']:
        assert o.read_json(root/path)['status']=='passed'
    o.verify_lock(c.ROOT/cfg['figures']/'v2/artifact_lock.json')
    scores=pd.read_csv(root/'analysis/phase_summary.csv').set_index(['origin','method'])
    point=pd.read_csv(root/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
    end=pd.read_csv(root/'analysis/endpoint_errors.csv',dtype={'seed':str})
    ends=end[end.seed=='ensemble'].groupby(['origin','method','horizon']).absolute_error.mean()
    fac=pd.read_csv(root/'analysis/factorial_summary.csv').set_index(['origin','metric'])
    pairs=o.read_json(root/'analysis/pairing.json');by={(r['origin'],r['candidate'],r['reference']):r for r in pairs}
    _,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    bindings={}

    def table(key,headers,rows):
        content=[list(map(str,r)) for r in rows];bindings[key]=dict(headers=headers,rows=content)
        return '\n'.join([f'<!-- table:{key} -->','| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(r)+' |' for r in content])+f'\n<!-- endtable:{key} -->'

    windows=table('windows',['已观测日数','教师拟合日数','完整预测日期','用途'],[
        [n,cfg['teacher_fit_prefixes'][str(n)],f'{dates[n]}—{dates[e-1]}','仅校准启动' if n==612 else '历史探索窗' if n!=1168 else '最终探索窗（8:2）']
        for n,e in zip(cfg['origins'],cfg['ends'])])
    methodrows=[['G00','关','关'],['G10','开','关'],['G01','关','开'],['G11','开','开']]
    design=table('design',['组别','A 起点残差表达','B 更新边界采样'],methodrows)
    rmse=table('rmse',['方法','792窗RMSE','972窗RMSE','1168最终窗RMSE'],[
        [NAMES[m],*[f'{scores.loc[(n,m),"rmse"]:.6f}' for n in cfg['origins'][1:]]] for m in cfg['controls']+cfg['arms']])
    day1=table('day1',['方法','792窗第1天','972窗第1天','1168窗第1天'],[
        [NAMES[m],*[f'{ends.loc[(n,m,1)]:.6f}' for n in cfg['origins'][1:]]] for m in [c.B,c.BA]+cfg['arms']])
    effect=table('factorial',['起点','A平均效应','B平均效应','交互效应','A开启时再加B'],[
        [n,*[f'{fac.loc[(n,"rmse"),k]:.6f}' for k in ['a_main','b_main','interaction','b_at_anchor']]] for n in cfg['origins'][1:]])
    seeds=table('paired_seeds',['配对（前者对后者）','792窗','972窗','1168窗'],[
        [f'{NAMES[a]} 对 {NAMES[b]}',*[str(by[n,a,b]['seed_both_improve'])+'/3' for n in cfg['origins'][1:]]]
        for a,b in cfg['reporting']['paired_edges']])
    probability=table('probability',['方法','CRPS','90%区间评分','90%覆盖','90%宽度'],[
        [NAMES[m],f'{scores.loc[(1168,m),"crps"]:.6f}',f'{scores.loc[(1168,m),"interval_score90"]:.6f}',
         f'{scores.loc[(1168,m),"coverage90"]*100:.2f}%',f'{scores.loc[(1168,m),"width90"]:.6f}'] for m in cfg['controls']+cfg['arms']])
    finalpoints=table('final_points',['测点','B+ MAE','G11 MAE','B+ RMSE','G11 RMSE','G11 90%覆盖'],[
        [p,f'{point.loc[(1168,c.B,p),"mae"]:.6f}',f'{point.loc[(1168,"G11_ANCHOR_BOUNDARY",p),"mae"]:.6f}',
         f'{point.loc[(1168,c.B,p),"rmse"]:.6f}',f'{point.loc[(1168,"G11_ANCHOR_BOUNDARY",p),"rmse"]:.6f}',
         f'{point.loc[(1168,"G11_ANCHOR_BOUNDARY",p),"coverage90"]*100:.2f}%'] for p in cfg['points']])
    gates=table('bplus_gates',['组别','B+均值门 792/972/1168','B+概率门 792/972/1168','B+联合门通过窗数'],[
        [NAMES[m],' / '.join('过' if by[n,m,c.B]['mean_pass'] else '未过' for n in cfg['origins'][1:]),
         ' / '.join('过' if by[n,m,c.B]['probability_pass'] else '未过' for n in cfg['origins'][1:]),
         str(sum(by[n,m,c.B]['joint_pass'] for n in cfg['origins'][1:]))+'/3'] for m in cfg['arms']])
    gain=(1-scores.loc[(1168,'G11_ANCHOR_BOUNDARY'),'rmse']/scores.loc[(1168,'G00_RAW_UNIFORM'),'rmse'])*100
    text=f'''# 起点残差表达×边界采样：2×2消融结果 v1.0

**这轮属于重新训练的两因素消融。全部48次拟合、9600次更新及三个完整评价窗已跑完。起点跳偏明显减轻，但最终窗仍未达到均值和概率同时优于B+的目标。**

最终293日四点平均RMSE由原GRU的14.257499降至两项合用的10.785141mm，下降{gain:.2f}%；B+为9.124173mm。原样组已复现上一轮GRU，改善不是换了对照。保留全部负结果，未据最终成绩追加模型或训练。

[消融总览PNG](../figures/ootang_gru_ablation_v1/20260915/v2/factorial_overview.png) · [四组四点图](../figures/ootang_gru_ablation_v1/20260915/README.md) · [完整24组CSV](../results/ootang_gru_ablation_v1/20260915/analysis/phase_summary.csv) · [逐点CSV](../results/ootang_gru_ablation_v1/20260915/analysis/metrics_by_point.csv) · [核验记录](ootang_gru_ablation_validation.v1.0.md)

## 1. 消融具体检验什么

上一轮两次固定权重检查属于事后诊断；本轮在同一1241参数固定图GRU上分别打开两个因素，四组都从相同种子初始化重新训练：

{design}

A保留已知起点残差`r0=y[m−1]−B[m−1]`，网络学习未来变化`f(h)−f(0)`，输出为`B(h)+r0+u[f(h)−f(0)]`。h=0严格连接已知位移，但不强制第1天等于实测。A关则输出`B(h)+u f(h)`。A同时改变输出基线、监督中心和正则化对象，因此只能归因于这整组表达，不能单独称“硬边界约束的效果”。

B将每批一半样本用于教师刚更新/更新后1—29日，并提高1—7日距离的采样权重；另一半保留原均匀抽样。它同时改变教师年龄与步长分布，也不能拆成单个细节的独立收益。四组同样使用完整历史、同一物理特征、三种子0/1/2、float64、Adam .001、固定200更新和λ=1；A同B水平的配对共享相同训练样本。此次不检验图边、网络家族或物理信息有无。

## 2. 相同任务与信息条件

{windows}

每窗一次输出完整293日，给定未来逐日降雨/库水位，期间不接收实测位移。972沿用已可用的792日教师。B+原参数/完整历史状态保持，新增B+拟合及物理前向均0。训练目标必须在当前前缀内成熟；教师拟合边界不晚于训练伪起点，标准化仅用当前训练前缀。训练伪起点不冒充当时实际发出的在线预测。

这与早期每天更新观测的1—7日滚动任务、未来驱动固定的长期递推任务不同，数值不混排。既有窗口和数据已被多轮查看，972与最终窗重叠97日；本轮是探索性结果。

DRIFT1是**最近一天速度不变的线性外推**，没有神经网络或待训练参数：`预测(m+h−1)=y[m−1]+h×(y[m−1]−y[m−2])`。RR_COND为原固定条件特征岭回归。仅起点校正B+只将已知r0加到整条B+路径，不学习后续变化。

## 3. 完整293日均值比较

以下均为“先平均三种子预测，再计算各点RMSE，最后平均四点”，单位mm；不是先平均各种子RMSE。

{rmse}

G11在792窗和972窗通过B+的完整均值保护，最终窗未通过。G10/G01相对原样组均有平均误差改善，但不等于所有点均改善。三个改动组最终平均MAE/RMSE均低于DRIFT1和岭回归，但逐点保护均未全过；历史两窗仍明显落后DRIFT1。对照比较与消融配对合计60组判定保留在[配对记录](../results/ootang_gru_ablation_v1/20260915/analysis/pairing.json)。

{gates}

门槛保持：平均MAE/RMSE各改善至少1%、逐点均值不退步；平均CRPS/90%区间评分各改善至少1%、逐点概率评分最多退步5%、90%平均覆盖≥85%且各点≥80%。另外独立报告至少2/3种子均值同向改善，不用种子多数覆盖逐点失败。

## 4. 起点问题改善了多少，是否存在交互

第1天四点平均绝对误差，单位mm：

{day1}

起点表达使三个窗口的首日误差大幅下降；最终G11为1.229503mm，原样为15.314678mm，但仍高于B+的0.076502mm。边界采样单独在最终起点有效，在前两个起点的首日误差略增。因此“增加边界样本就必然修好起点”没有得到支持。第7/30天误差及各种子见[完整端点表](../results/ootang_gru_ablation_v1/20260915/analysis/endpoint_errors.csv)，不取代全窗评价。

以下RMSE差为mm，负值表示降低；A主效应为`[(10−00)+(11−01)]/2`，B为`[(01−00)+(11−10)]/2`，交互为`11−10−01+00`：

{effect}

A、B的平均效应三窗都为负；但A已开启时，再加B在972窗使RMSE增加0.287557mm，最终只降低0.356296mm。交互在792窗为负、另两窗为正，不能称为稳定叠加收益。全部逐点/种子效应见[因子效应目录](../results/ootang_gru_ablation_v1/20260915/analysis/factorial_seed_points.csv)。

下面的数字表示同种子配对中，四点平均MAE和RMSE同时改善的种子数；不是显著性检验：

{seeds}

## 5. 概率改善与剩余问题

每种方法使用自己上一条已发路径第91—180日的90条成熟误差，逐点RMS形成固定高斯80/90/95%区间。没有概率头、事后偏差校正或预测期滚动更新。最终窗指标如下，覆盖率以百分数表示，其余mm：

{probability}

G11的CRPS与区间评分低于B+，区间也更窄；但平均覆盖91.04%掩盖了ATU1只有67.24%的欠覆盖。G10的ATU1覆盖76.11%同样未达80%。G00/G01最终对原B+的概率门通过，均值门仍失败；对“仅起点校正B+”时四组的概率门均未全过。概率门和均值门分别判断，不能只凭平均CRPS宣布达标。

最终G11逐点表现：

{finalpoints}

ATU5、MJ1的均值误差低于B+，ATU1、MJ3仍更差。图中起点连接已改善，后续路径尤其MJ3仍偏离实测。这说明起点表达是可以改进的环节，尚不能解释或解决全部长窗误差；因子内包含多个修改，也不能认定单一失败原因。

## 6. 交付、核验和结论边界

48次拟合/9600次更新/192检查点全部完成；612校准启动和其他三个293日窗均保存，没有删除失败种子或尾段。708项来源锁、所有检查点重载与独立NumPy前向、3439904个数值复算通过，最大差2.16e−12。G00全部48个保存检查点复现原GRU。先锁定全部发报轨迹，再读取完整标签评分；90条误差池、60组配对和360行因子效应均复算。

五张最终PNG/SVG、20面板、406161个数据/图形/尺寸值已核对；源数据差0，全部图已目视，字体/碰撞与1.5pt对齐通过。图形初稿和只读核验器异常留档，仅修核验与排版，未改训练产物。完整数组、288行种子逐点评分、28128点日及1152端点记录全部保留。统计解释11/11项已审阅，见[核验文档](ootang_gru_ablation_validation.v1.0.md)。

本轮支持的结论是：**在这个固定GRU实验中，起点残差表达和采样政策值得明确控制；修好起点后，跨时段、跨点的后续残差预测与区间校准仍未稳定。** 后续研究问题应落到ATU1/MJ3的后续走势及ATU1尺度迁移；本轮不据最终成绩选出新的正式主模型，也不追加第五组。

原始日值as-of未知、既有h预处理、给定未来驱动、历史参数优化未收敛记录及多轮日期暴露的限制保持。区间是边际预测区间，不是同时路径覆盖或参数后验；不推论整个GRU/Transformer家族无效，更不转为真实预警能力。实验执行与核验完成，效果目标未完成，用户/导师尚未验收。

[冻结计划](ootang_gru_ablation_plan.v1.0.md) · [配置](../config/ootang_gru_ablation.v1_0.json) · [708项来源](ootang_gru_ablation_sources.v1.0.json) · [最终回执](../results/ootang_gru_ablation_v1/20260915/final_receipt.json) · [上一轮GRU结果](ootang_overnight_graph_results.v1.0.md)

分步本地提交：冻结39a28fb、实现18bcc41、完整实验36b6135、独立复算f359d3a9；图件与收尾提交见Git和最终回执。独立窗口06:30—08:30 UTC包含准备核验，实际用时以最终回执为准；本轮交付Markdown/CSV/PNG/SVG，没有push。
'''
    path=c.ROOT/'docs/ootang_gru_ablation_results.v1.0.md';path.write_text(text)
    o.write_json(root/'report_tables.json',bindings)
    print(path)


if __name__=='__main__':main()
