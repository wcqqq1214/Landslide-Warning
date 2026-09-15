"""Source-bound Chinese report, with a machine-readable table ledger."""
import json
import pandas as pd
from . import core as c


def main():
    cfg=c.spec();root=c.ROOT/cfg['out'];ext=root/'extension'
    assert c.o.read_json(root/'figure_qa_v3/receipt.json')['status']=='passed'
    assert c.o.read_json(ext/'audit/receipt.json')['status']=='passed'
    qa=root/'document_qa';qa.mkdir(exist_ok=False)
    files={
        'summary':ext/'analysis/phase_summary.csv','points':ext/'analysis/metrics_by_point.csv',
        'train200':root/'diagnostic/training_summary.csv','train400':ext/'audit/training400_summary.csv'}
    frames={k:pd.read_csv(v,float_precision='round_trip') for k,v in files.items()}
    pairings=c.o.read_json(ext/'analysis/pairing.json');trigger=c.o.read_json(root/'diagnostic/trigger.json')
    ledger=[]

    def source(table,filters,column,aggregate='single',scale=1):
        return dict(table=table,filters=filters,column=column,aggregate=aggregate,scale=scale)

    def value(rule):
        if rule.get('op')=='change_percent':
            a,b=value(rule['old']),value(rule['new']);return 100*(b-a)/max(abs(a),1e-15)
        df=frames[rule['table']]
        for key,val in rule['filters'].items():df=df[df[key]==val]
        assert len(df)>0
        if rule['aggregate']=='single':assert len(df)==1
        return float(df[rule['column']].mean())*rule['scale']

    def numeric(rule,places=6):
        return dict(text=f'{value(rule):.{places}f}',rule=rule,places=places)

    def table(name,headers,rows):
        cells=[[v if isinstance(v,dict) else dict(text=str(v)) for v in row] for row in rows]
        ledger.append(dict(name=name,headers=headers,rows=cells))
        return '\n'.join([f'<!-- table:{name} -->','| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
            *['| '+' | '.join(v['text'] for v in row)+' |' for row in cells],f'<!-- endtable:{name} -->'])

    names={'BPLUS_CONTINUOUS':'改进B+','BPLUS_ORIGIN_ANCHOR':'仅起点校正B+','DRIFT1':'DRIFT1','RR_COND':'普通岭回归',
           'G10_ANCHOR_UNIFORM_E200':'GRU起点版200次','G10_ANCHOR_UNIFORM_E400':'GRU起点版400次',
           'T10_ANCHOR_UNIFORM_E200':'Transformer起点版200次','T10_ANCHOR_UNIFORM_E400':'Transformer起点版400次'}
    lines=['# 训练充分性核验与固定预算对照 v1.0','',
        '**已完成零训练诊断、按事前规则触发的200对400次对照、完整评分及独立核验。两骨干的训练拟合继续改善，但追加训练没有带来跨时段稳定的预测收益。**','',
        '最终293日四点平均RMSE：GRU由11.141437升至11.339123mm，Transformer由13.290363升至13.653310mm，B+保持9.124173mm。两种400次版本的最终概率门对B+通过，但均值门失败，三个完整评价窗的联合通过数仍为0/3。','',
        '[预算比较图](../figures/ootang_training_sufficiency_v1/20260915/v3/budget_comparison.png) · [四张图件](../figures/ootang_training_sufficiency_v1/20260915/README.md) · [完整CSV](../results/ootang_training_sufficiency_v1/20260915/extension/analysis/phase_summary.csv)','',
        '## Material Passport','',
        '本地固定协议下的探索性研究；实现、训练与核验完成，效果目标未完成，用户/导师尚未验收。新分支`codex/training-sufficiency-audit`，独立10:08:15—11:08:15 UTC自限窗口，含准备和交付，不恢复旧预算。','',
        '## 1. 为什么执行这次追加','',
        '先冻结规则，再重载两骨干起点版的96个已有检查点。每训练前缀固定128个伪起点×32个成熟距离，分别计算拟合MSE、正则项和总目标。标准化仍按当前训练前缀，教师拟合边界不超过伪起点；这些是训练分布面板，不是验证集。','',
        'e100→e200时，两个骨干的四个训练前缀均有3/3种子同时降低MSE和总目标至少1%。截止1168日已完整兑现的612/792历史路径也均有3/3种子降低平均MAE和RMSE；Transformer两窗等权平均MAE/RMSE分别降低7.2457%/7.0601%，因此达到预定追加条件。最终293日标签和972未完整成熟的路径未用于触发。','',
        'λ=1的目标为J=MSE(q,t)+mean(q²)。另核对J=2mean((q−t/2)²)+0.5mean(t²)，区分拟合与正则收缩；这里的代数下界不是有限网络必能达到的最优值。训练总目标仍降低，不足以单独判断泛化或充分收敛。','',
        '[零训练诊断图](../figures/ootang_training_sufficiency_v1/20260915/v3/checkpoint_diagnostic.png) · [诊断详情](ootang_training_sufficiency_diagnostic.v1.0.md) · [事前触发明细](../results/ootang_training_sufficiency_v1/20260915/diagnostic/trigger.json)','',
        '## 2. 固定对照及全部均值结果','',
        '两骨干保持14维完整历史、相同未来物理/驱动输入、固定图、起点表达、λ=1、三种子、Adam和uniform抽样分布；仅增加训练预算。旧检查点无Adam状态，本轮从原初始化精确重放原200步，核对全部日志、参数与预测后保留优化器状态再追加200步。实际24新拟合/9600更新，其中4800重放、4800追加；不是只付出4800更新。','',
        '前缀612是293日校准启动；主要792/972/1168分别完整评价293日，未来降雨/库水位给定，路径中不接收位移反馈。972继续使用792日教师，不重拟合B+。所有路径和误差池锁定后才解析完整标签评分，不按成绩停止某个窗口。','',
        '以下先平均三种子预测，再分别计算四点RMSE并等权平均，单位mm。']
    rows=[]
    for method,name in names.items():
        rows.append([name]+[numeric(source('summary',dict(origin=n,method=method),'rmse')) for n in cfg['origins'][1:]])
    lines += ['',table('rmse',['方法','792历史窗','972历史窗','1168最终窗'],rows),'',
        '追加训练仅在792窗同时降低两个骨干的平均MAE和RMSE。972窗两者均退步；最终平均MAE略降，但RMSE均升高。不能把MAE的局部改善说成完整均值目标通过。','']
    rows=[]
    for arm in cfg['arms']:
        for n in cfg['origins'][1:]:
            def change(metric):return numeric(dict(op='change_percent',old=source('summary',dict(origin=n,method=arm+'_E200'),metric),new=source('summary',dict(origin=n,method=arm+'_E400'),metric)),4)
            pair=next(v for v in pairings if v['origin']==n and v['candidate']==arm+'_E400' and v['reference']==arm+'_E200')
            rows.append(['GRU' if arm.startswith('G') else 'Transformer',str(n),change('mae'),change('rmse'),f"{pair['seed_both_improve']}/3"])
    lines += [table('budget_changes',['骨干','起点','MAE变化/%','RMSE变化/%','同种子MAE及RMSE均改善'],rows),'',
        '变化为400相对200，负值为降低。同种子计数不是统计显著性：Transformer最终有2/3种子同向改善，但集成RMSE仍增加，不能按种子挑选赢家。400次时Transformer在972的RMSE略低于GRU，其他两个主窗仍较高；GRU三窗均更优的旧结论仅适用于旧200次设置。','',
        '## 3. 训练继续改善为何没有变成稳定外推收益','',
        '相同固定训练面板的变化如下，先对三个种子的损失平均，再比较400与200次。所有前缀的训练MSE和总目标均进一步降低，而外推误差没有保持同向。这支持“追加训练未解决迁移不稳定”，不证明400次已充分收敛，也不能排除其他未做的训练方案。','']
    rows=[]
    for n in cfg['origins']:
        cells=[str(n)]
        for arm in cfg['arms']:
            for metric in ['mse','objective']:
                cells.append(numeric(dict(op='change_percent',old=source('train200',dict(origin=n,arm=arm,step=200),metric,'mean'),new=source('train400',dict(origin=n,arm=arm),metric,'mean')),4))
        rows.append(cells)
    lines += [table('training_changes',['训练前缀','GRU MSE/%','GRU J/%','TF MSE/%','TF J/%'],rows),'',
        '最终四点曲线仍显示MJ3后续走势偏低。保留完整历史输入及起点连接解决的是表达的一部分，不能保证后续残差方向能跨时段迁移。最终教师来自1168日前缀，而训练伪起点教师最多到792日；这一差异仍是未分离的限制，本次不能把全部失败归因于它。','',
        '## 4. 概率改善与逐点失败同时保留','',
        '每个版本均用自己上一条已发出路径第91—180日的90个成熟误差计算固定逐点RMS，构建80/90/95%高斯边际区间。400次沿用同一校准方法，但误差池数值随预测改变；最终池仍为[1062,1152)，距起点16日。区间变化不能全部解释为均值网络更可靠。','']
    rows=[]
    for method,name in names.items():
        rows.append([name]+[numeric(source('summary',dict(origin=1168,method=method),metric,scale=100 if metric=='coverage90' else 1),4)
                            for metric in ['crps','interval_score90','coverage90','width90']])
    lines += [table('probability',['最终窗方法','CRPS/mm','90%区间评分/mm','90%覆盖/%','90%宽度/mm'],rows),'',
        'Transformer的最终平均覆盖由77.82%升至95.65%，CRPS和区间评分也改善，但区间变宽且MJ1概率评分退步，未过相对自身200次的完整概率保护。GRU覆盖提高，但CRPS、区间评分和宽度均比200次更差。两者最终相对B+通过原概率门，均未同时通过均值门。','']
    rows=[]
    for point in cfg['points']:
        cells=[point]
        for method in ['BPLUS_CONTINUOUS','G10_ANCHOR_UNIFORM_E400','T10_ANCHOR_UNIFORM_E400']:
            cells.append(numeric(source('points',dict(origin=1168,method=method,point=point),'rmse')))
        for method in ['G10_ANCHOR_UNIFORM_E400','T10_ANCHOR_UNIFORM_E400']:
            cells.append(numeric(source('points',dict(origin=1168,method=method,point=point),'coverage90',scale=100),2))
        rows.append(cells)
    lines += [table('final_points',['测点','B+ RMSE','GRU400 RMSE','TF400 RMSE','GRU400覆盖/%','TF400覆盖/%'],rows),'',
        '[GRU四点完整曲线](../figures/ootang_training_sufficiency_v1/20260915/v3/gru_final400.png) · [Transformer四点完整曲线](../figures/ootang_training_sufficiency_v1/20260915/v3/transformer_final400.png) · [全部逐点CSV](../results/ootang_training_sufficiency_v1/20260915/extension/analysis/metrics_by_point.csv)','',
        '沿用原均值、概率和逐点保护，以下顺序均为792/972/1168。','']
    rows=[]
    for method in [a+f'_E{s}' for a in cfg['arms'] for s in [200,400]]:
        pairs=[next(v for v in pairings if v['origin']==n and v['candidate']==method and v['reference']=='BPLUS_CONTINUOUS') for n in cfg['origins'][1:]]
        rows.append([names[method],' / '.join('过' if v['mean_pass'] else '未过' for v in pairs),
                     ' / '.join('过' if v['probability_pass'] else '未过' for v in pairs),f"{sum(v['joint_pass'] for v in pairs)}/3"])
    lines += [table('gates',['方法','均值门','概率门','联合通过窗数'],rows),'',
        '## 5. 本轮结论与收尾','',
        '**原200次尚有继续拟合和历史改善的迹象，所以固定追加有依据；实际追加至400次后，跨时段收益仍不稳定。这版Transformer的训练预算和结构搜索在本轮收束。** 保留B+及全部200/400次结果，不按最终成绩把200次重选为新主模型，不增加800次、结构、λ或RL。旧Transformer半残差最终8.867132mm的局部优势和其历史不稳定结论保持，不用本轮小模型代表整个Transformer家族。','',
        '执行：24新拟合、9600实际更新、72新检查点完整完成；原96检查点诊断与零训练复算完成，新增B+拟合/物理前向0。独立诊断2330检查/401128值，追加核验3968检查/1265488值；最大差分别4.55e−13/2.16e−12。原200步日志、参数和预测精确复现，原版本结果未改。四张最终图、16面板已逐图核验；详见核验页和最终回执。','',
        '异常保留：首次只读审计的CSV默认解析往返误差改为round_trip；图v1轴范围导致空面板、v2预测区注释碰撞，v3修正显示并将注释放在绘图区下方。训练及原数值不受这些修订影响，正式24次拟合无失败重试。','',
        '限制：固定单案例/四点；公开日序列as-of及预处理限制、教师旧优化未收敛记录保留；已知未来驱动条件；612训练目标最多180日；历史窗反复暴露且重叠，主要972与最终重叠97日。没有显著性、因果机制或真实预警有效性结论，不宣称导师验收。','',
        '[事前计划](ootang_training_sufficiency_plan.v1.0.md) · [配置](../config/ootang_training_sufficiency.v1_0.json) · [来源清单](ootang_training_sufficiency_sources.v1.0.json) · [核验](ootang_training_sufficiency_validation.v1.0.md) · [最终回执](../results/ootang_training_sufficiency_v1/20260915/final_receipt.json)']
    report=c.ROOT/'docs/ootang_training_sufficiency_results.v1.0.md';report.write_text('\n'.join(lines)+'\n')
    c.o.write_json(qa/'table_ledger.json',dict(report=str(report.relative_to(c.ROOT)),source_files={k:str(v.relative_to(c.ROOT)) for k,v in files.items()},tables=ledger))
    print(str(report),flush=True)


if __name__=='__main__':main()
