"""Build a compact report from locked arrays/CSV; no model execution."""
from pathlib import Path
import pandas as pd
from . import core as c

o=c.old
NAMES={c.B:'改进B+',c.BA:'仅起点校正B+','DRIFT1':'DRIFT1','RR_COND':'普通岭回归',
       'OLD_TRANSFORMER':'旧Transformer原版','OLD_HALF':'旧Transformer半残差','OLD_REG1':'旧Transformer正则化λ=1',
       'G11_ANCHOR_BOUNDARY':'旧GRU起点表达＋边界采样',
       'G00_RAW_UNIFORM':'GRU原残差','G10_ANCHOR_UNIFORM':'GRU起点表达',
       'T00_RAW_UNIFORM':'Transformer原残差','T10_ANCHOR_UNIFORM':'Transformer起点表达'}


def main():
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];o.verify_lock(root/'analysis_lock.json')
    assert o.read_json(root/'independent_audit/receipt.json')['status']=='passed'
    assert o.read_json(root/'figure_qa_v2/receipt.json')['status']=='passed'
    score=pd.read_csv(root/'analysis/phase_summary.csv').set_index(['origin','method'])
    point=pd.read_csv(root/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
    endpoint=pd.read_csv(root/'analysis/endpoint_errors.csv',dtype={'seed':str})
    endpoint=endpoint[endpoint.seed=='ensemble'].groupby(['origin','method','horizon']).absolute_error.mean()
    factorial=pd.read_csv(root/'analysis/factorial_summary.csv').set_index(['origin','metric'])
    pairs={(v['origin'],v['candidate'],v['reference']):v for v in o.read_json(root/'analysis/pairing.json')}
    _,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    methods=cfg['controls']+cfg['historical_controls']+cfg['arms'];bindings={}
    def table(name,headers,rows):
        rows=[[str(v) for v in row] for row in rows]
        bindings[name]=dict(headers=headers,rows=rows)
        return '\n'.join([f'<!-- table:{name} -->','| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                         ['| '+' | '.join(row)+' |' for row in rows]+[f'<!-- endtable:{name} -->'])
    fmt=lambda v:f'{float(v):.6f}'
    pct=lambda v:f'{100*float(v):.2f}%'
    t0,t1='T00_RAW_UNIFORM','T10_ANCHOR_UNIFORM';g0,g1=cfg['reuse_arms']
    improvement=100*(1-score.loc[(1168,t1),'rmse']/score.loc[(1168,t0),'rmse'])
    parts=['# GRU/Transformer × 起点残差表达：完整比较报告 v1.0',
    '**本轮已完整跑完。起点表达在两个骨干上都降低了三个窗口的平均MAE/RMSE；同样采用起点表达时，本轮GRU三个窗口的平均MAE/RMSE/CRPS更低。但四组都没有达到均值与概率同时优于B+的目标。**',
    f'最终293日Transformer平均RMSE由{fmt(score.loc[(1168,t0),"rmse"])}降至{fmt(score.loc[(1168,t1),"rmse"])}mm，下降{improvement:.2f}%；同口径GRU起点版为{fmt(score.loc[(1168,g1),"rmse"])}，B+为{fmt(score.loc[(1168,c.B),"rmse"])}mm。旧Transformer半残差{fmt(score.loc[(1168,"OLD_HALF"),"rmse"])}mm的局部优势仍保留，不能推广为某个网络家族必胜。',
    '[比较总览PNG](../figures/ootang_backbone_anchor_v1/20260915/v1/factorial_overview.png) · [九张导师式/完整范围图](../figures/ootang_backbone_anchor_v1/20260915/README.md) · [完整36组CSV](../results/ootang_backbone_anchor_v1/20260915/analysis/phase_summary.csv) · [核验记录](ootang_backbone_anchor_validation.v1.0.md)',
    '## Material Passport',
    '本地代码实验，固定协议下的探索性消融。训练、模型重放与图件核验完成；效果目标未达，用户/导师尚未验收。原始数据与旧结果保持。',
    '## 1. 这次比较控制了什么',
    '四组共用14维逐点完整历史、相同教师/归一化、固定MJ3—MJ1图、相同解码器、uniform起点与成熟距离样本、三种子、λ=1和固定200更新。GRU1241参数，Transformer1249参数（差8，0.64%）。新增Transformer为一层8维两头历史因果自注意力、FFN8，读取最后历史token；它是统一接口的新实现，与此前旧版及起点条件注意力原型分别保留身份。',
    table('design',['组别','编码器','起点表达','执行'],[[a,'GRU' if a.startswith('G') else 'Transformer','开' if cfg['factors'][a]['anchor'] else '关','复用并重载核验' if a.startswith('G') else '新训练'] for a in cfg['arms']]),
    '起点表达保留已知r0=y[m−1]−B[m−1]，网络学习f(h)−f(0)，输出B(h)+r0+u[f(h)−f(0)]；原样为B(h)+u*f(h)。A同时改变输出基线、监督中心和正则化对象，不能单独归因于硬边界。边界采样本轮全部关闭。GRU的24份旧拟合/96检查点在新接口下预测差0，因此复用；新增24份Transformer拟合4800更新/96检查点，未重训GRU或B+。',
    table('windows',['已观测日数','教师拟合日数','完整预测日期','用途'],[[n,cfg['teacher_fit_prefixes'][str(n)],f'{dates[n]}—{dates[end-1]}','校准启动' if n==612 else '历史探索' if n<1168 else '最终探索（8:2）'] for n,end in zip(cfg['origins'],cfg['ends'])]),
    '每窗一次发出完整293日，给定未来逐日降雨/库水位，路径内不接收实测位移；972沿用792日教师。标准化仅用当前训练前缀，教师拟合边界≤训练伪起点，目标在前缀内成熟。既有窗口反复暴露且972与最终重叠97日，全部属于探索性条件预测。',
    '## 2. 全窗均值比较',
    '以下为三种子预测先等权平均，再分别计算四点RMSE并等权平均，单位mm。历史补充单列身份，不进入本轮纯因素效应。',
    table('rmse',['方法','792历史窗','972历史窗','1168最终窗'],[[NAMES[m]]+[fmt(score.loc[(n,m),'rmse']) for n in cfg['origins'][1:]] for m in methods]),
    '本轮GRU起点版11.141437与上轮两项合用10.785141属于不同组：前者关闭边界采样，后者保留为历史补充，旧成绩没有改变。两种起点版在972平均误差接近，但最终GRU更低；早期Transformer原版/半残差在最终窗仍更低，不能只拿较差的新注意力原型代表所有Transformer。旧半残差在此前合法历史选模中没有胜出，不根据最终成绩追认其为主模型。',
    'DRIFT1是最近一天速度不变的线性外推：y[m−1]+h×(y[m−1]−y[m−2])，没有网络或待训练参数。历史两个窗中它仍低于本轮四组。最终GRU起点版的平均MAE/RMSE低于DRIFT1和岭回归，但Transformer两组均未同时超过这些简单对照。',
    '## 3. 配对消融回答了什么',
    '下表为完整窗平均RMSE差，负值表示降低。A为起点表达减原残差，网络差为TF减GRU；交互=(TF起点−TF原样)−(GRU起点−GRU原样)，单位mm。',
    table('factorial',['起点','A在GRU','A在TF','TF−GRU原样','TF−GRU起点','交互'],[[n]+[fmt(factorial.loc[(n,'rmse'),k]) for k in ['a_at_gru','a_at_tf','tf_at_raw','tf_at_anchor','interaction']] for n in cfg['origins'][1:]]),
    table('paired_seeds',['配对（前者对后者）','792同向种子','972同向种子','1168同向种子'],[[NAMES[a]+' 对 '+NAMES[b]]+[str(pairs[n,a,b]['seed_both_improve'])+'/3' for n in cfg['origins'][1:]] for a,b in cfg['reporting']['paired_edges']]),
    '同向指同种子四点平均MAE和RMSE同时降低，不是显著性检验。起点表达的平均收益可以跨两个骨干出现，但TF在792/最终都只有2/3种子同向，最终ATU1/ATU5的MAE反而增加；不能称每点每种子有效。A开时GRU三窗集成MAE/RMSE/CRPS均更低，支持本轮固定实现/预算下优先保留GRU作研究对照，不能证明GRU普遍优于Transformer。',
    '第1天四点平均绝对误差如下，单位mm；第7/30天及全部种子在[端点CSV](../results/ootang_backbone_anchor_v1/20260915/analysis/endpoint_errors.csv)，不替代完整293日评价。',
    table('day1',['方法','792第1天','972第1天','1168第1天'],[[NAMES[m]]+[fmt(endpoint.loc[(n,m,1)]) for n in cfg['origins'][1:]] for m in [c.B,c.BA]+cfg['arms']]),
    '## 4. 概率与逐点失败',
    '各方法使用自己上一条已发出路径第91—180日90条成熟误差的逐点RMS，形成固定高斯80/90/95%边际区间。最终误差池[1062,1152)，距起点16日；种子评分使用本方法集成尺度。以下为最终窗，覆盖率为百分数，其余mm。',
    table('probability',['方法','CRPS','90%区间评分','90%覆盖','90%宽度'],[[NAMES[m]]+[pct(score.loc[(1168,m),k]) if k=='coverage90' else fmt(score.loc[(1168,m),k]) for k in ['crps','interval_score90','coverage90','width90']] for m in methods]),
    table('final_points',['测点','B+ RMSE','GRU起点 RMSE','TF起点 RMSE','GRU 90%覆盖','TF 90%覆盖'],[[p]+[fmt(point.loc[(1168,m,p),'rmse']) for m in [c.B,g1,t1]]+[pct(point.loc[(1168,m,p),'coverage90']) for m in [g1,t1]] for p in cfg['points']]),
    f'TF起点版的最终平均覆盖为{pct(score.loc[(1168,t1),"coverage90"])}，ATU1/ATU5只有{pct(point.loc[(1168,t1,"ATU1"),"coverage90"])}和{pct(point.loc[(1168,t1,"ATU5"),"coverage90"])}；GRU起点版平均覆盖{pct(score.loc[(1168,g1),"coverage90"])}，ATU1仍只有{pct(point.loc[(1168,g1,"ATU1"),"coverage90"])}。较低CRPS或较窄区间不能覆盖逐点欠覆盖失败。两个起点版在MJ3的后续轨迹仍偏低，起点连接改善没有解决完整走势。',
    '沿用原保护：平均MAE/RMSE各改善至少1%、各点均值不退步；平均CRPS/90%区间评分各改善至少1%、逐点评分最多退步5%、90%平均覆盖≥85%且各点≥80%。对B+的判定如下；对仅起点校正B+、DRIFT1和岭回归的全部判定见[60组配对](../results/ootang_backbone_anchor_v1/20260915/analysis/pairing.json)。',
    table('bplus_gates',['组别','均值门792/972/1168','概率门792/972/1168','联合通过窗数'],[[NAMES[m],' / '.join('过' if pairs[n,m,c.B]['mean_pass'] else '未过' for n in cfg['origins'][1:]),' / '.join('过' if pairs[n,m,c.B]['probability_pass'] else '未过' for n in cfg['origins'][1:]),str(sum(pairs[n,m,c.B]['joint_pass'] for n in cfg['origins'][1:]))+'/3'] for m in cfg['arms']]),
    '## 5. 交付与结论边界',
    '已完成24次新拟合4800更新，复用24份GRU拟合；192份新旧检查点、1106来源、3813856数值独立复算通过，最大差2.16e−12。九张PNG/SVG共36面板、780896图形/数据/尺寸值核对及全部目视通过，数据差0。导师固定轴仅裁切显示区间、越界日数已注，另有完整范围版；没有删日期/种子、缩窄区间或补画不存在的神经训练拟合。',
    '参数手算与实现锁封装异常在训练前处理，原错误记录保留；正式拟合无崩溃或按成绩重试。固定200更新不代表两个编码器都充分收敛，A改变目标后损失数值不能直接混排。无p值或独立重复置信区间，统计解释11/11项见核验；原as-of/预处理、教师未收敛、已知未来驱动、窗口反复暴露和高斯距离迁移限制保持。',
    '**本轮支持的是：起点表达存在可迁移的平均收益，当前统一接口的GRU起点版优于TF起点版；尚未得到能稳定替换B+的均值与概率模型。** 本次小试已完成并停止追加，保留B+与全部负结果，不自动扩网络、轮数、λ或RL。后续若继续，应先提出能解释后续残差走势或逐点尺度迁移的具体新假设；本轮未启动下一实验。执行与核验完成、效果目标未完成、用户/导师尚未验收分别记录。',
    '本地分步提交：冻结da685f33、计数勘误61285287、实现abcc7b67、完整实验d49185e0、独立复算78950377、图件6be09004；图文收尾另行提交。新独立窗口09:18:32—11:18:32 UTC包含准备与核验，实际任务用时见[最终回执](../results/ootang_backbone_anchor_v1/20260915/final_receipt.json)，旧预算不恢复、剩余额度不转用。只本地提交，未push或交付PDF。',
    '[计划](ootang_backbone_anchor_plan.v1.0.md) · [配置](../config/ootang_backbone_anchor.v1_0.json) · [冻结来源](ootang_backbone_anchor_sources.v1.0.json) · [实现](ootang_backbone_anchor_implementation.v1.0.md) · [逐点评分](../results/ootang_backbone_anchor_v1/20260915/analysis/metrics_by_point.csv) · [全部种子逐点](../results/ootang_backbone_anchor_v1/20260915/analysis/seed_metrics_by_point.csv)']
    (c.ROOT/'docs/ootang_backbone_anchor_results.v1.0.md').write_text('\n\n'.join(parts)+'\n')
    o.write_json(root/'report_tables.json',bindings)
    print('report tables',len(bindings),'rows',sum(len(v['rows']) for v in bindings.values()))


if __name__=='__main__':main()
