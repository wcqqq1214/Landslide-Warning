"""Generate the fixed experiment report and a source ledger for each numeric table."""
import argparse
import json
import pandas as pd
from . import core as c


NAMES={c.B:'改进 B+',c.BA:'起点校正 B+','DRIFT1':'DRIFT1','RR_COND':'普通岭回归',
       'G_CACHED':'旧教师 GRU','G_REFRESH':'更新教师 GRU'}


def main():
    cfg=c.spec();c.guard();o=c.o;root=c.ROOT/cfg['out']
    o.verify_lock(root/'audit_lock.json')
    o.verify_lock(c.ROOT/cfg['figures']/'delivery_lock.json')
    out=root/'document_qa';out.mkdir(exist_ok=True)
    sources={
        'summary':f"{cfg['out']}/analysis/phase_summary.csv",
        'points':f"{cfg['out']}/analysis/metrics_by_point.csv",
        'fits':f"{cfg['out']}/audit/teacher_fit_points.csv",
        'differences':f"{cfg['out']}/analysis/paired_differences.csv"}
    frames={k:pd.read_csv(c.ROOT/v,float_precision='round_trip') for k,v in sources.items()}
    pairs=o.read_json(root/'analysis/pairing.json')
    windows=cfg['origins'][1:];methods=cfg['controls']+cfg['arms'];tables=[]

    def text(value):return {'text':str(value)}

    def number(table,column,filters,places=6,aggregate='single',scale=1):
        f=frames[table]
        for key,val in filters.items():f=f[f[key]==val]
        assert len(f)>0
        values=f[column].to_numpy()
        if aggregate=='single':assert len(values)==1
        elif aggregate=='mean_abs':values=abs(values)
        else:assert aggregate=='mean'
        val=float(values.mean())*scale
        return {'text':f'{val:.{places}f}','places':places,
                'rule':dict(table=table,column=column,filters=filters,aggregate=aggregate,scale=scale)}

    def pair(n,candidate,reference):
        return next(x for x in pairs if x['origin']==n and x['candidate']==candidate and x['reference']==reference)

    def table(name,headers,rows):
        tables.append(dict(name=name,headers=headers,rows=rows))
        lines=['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']
        lines.extend('| '+' | '.join(v['text'] for v in row)+' |' for row in rows)
        return f'<!-- table:{name} -->\n'+'\n'.join(lines)+f'\n<!-- endtable:{name} -->'

    summary=table('rmse',['方法','792历史窗','972历史窗','1168最终探索'],[
        [text(NAMES[m])]+[number('summary','rmse',dict(origin=n,method=m)) for n in windows] for m in methods])
    changes=table('paired_changes',['起点','旧教师MAE','更新教师MAE','RMSE变化/mm','RMSE变化/%','同种子均值同时改善'],[
        [text(n)]+[number('summary','mae',dict(origin=n,method=m)) for m in cfg['arms']]+
        [number('differences',key,dict(origin=n,seed='ensemble',point='average',metric='rmse'),
                places=6 if key=='difference' else 2,scale=1 if key=='difference' else 100)
         for key in ['difference','relative_change']]+[text(f"{pair(n,'G_REFRESH','G_CACHED')['seed_both_improve']}/3")]
        for n in windows])
    points=table('final_points',['测点','B+ RMSE','旧教师 RMSE','更新教师 RMSE','旧教师90%覆盖/%','更新教师90%覆盖/%'],[
        [text(p)]+[number('points','rmse',dict(origin=1168,method=m,point=p)) for m in [c.B,*cfg['arms']]]+
        [number('points','coverage90',dict(origin=1168,method=m,point=p),places=2,scale=100) for m in cfg['arms']]
        for p in cfg['points']])
    probability=table('final_probability',['方法','CRPS/mm','90%区间评分/mm','90%覆盖/%','90%宽度/mm'],[
        [text(NAMES[m])]+[number('summary',key,dict(origin=1168,method=m),places=2 if key=='coverage90' else 6,
                               scale=100 if key=='coverage90' else 1)
                         for key in ['crps','interval_score90','coverage90','width90']] for m in methods])
    gates=[]
    for m in cfg['arms']:
        ps=[pair(n,m,c.B) for n in windows]
        row=[text(NAMES[m]),text(' / '.join('过' if p['mean_pass'] else '未过' for p in ps)),
             text(f"{sum(p['probability_pass'] for p in ps)}/3"),text(f"{sum(p['joint_pass'] for p in ps)}/3")]
        row.extend(text(f"{sum(pair(n,m,ref)['mean_pass'] for n in windows)}/3") for ref in ['DRIFT1','RR_COND'])
        gates.append(row)
    gates=table('gates',['方法','对B+均值门：792/972/1168','对B+概率门','对B+联合门','对DRIFT1均值门','对岭回归均值门'],gates)
    fit_rows=[]
    for m in cfg['teacher_update']['new_prefixes']:
        fit_rows.append([text(m)]+[number('fits','fit_rmse',dict(fit_prefix=m,policy=a),aggregate='mean') for a in cfg['arms']]+
                       [number('fits','terminal_error',dict(fit_prefix=m,policy=a),aggregate='mean_abs') for a in cfg['arms']]+[text('未收敛')])
    fits=table('teacher_fits',['拟合前缀','旧教师拟合RMSE','更新后拟合RMSE','旧教师终点绝对误差','更新后终点绝对误差','新拟合状态'],fit_rows)
    report=f'''# 历史 B+ 教师更新配对：完整比较报告 v1.0

**全部预定实验已跑完，更新历史教师没有带来稳定收益。** 在三个完整293日窗口，更新教师GRU的集成MAE和RMSE均高于同场训练的旧教师GRU。最终四点平均RMSE为 **11.068245 mm**，旧教师组 **10.600134 mm**，改进B+ **9.124173 mm**。两组相对B+的均值与概率联合门均为0/3。本轮保留负结果并停止追加。

这次只改变训练样本所用的B+教师政策：旧组取当时合法的最近缓存；更新组在该历史前缀内重新更新参数。网络、起点表达、样本、单位、种子和200次更新完全配对，外层B+预测保持原样。两组都使用本轮共同的60日起点网格；旧教师组是新训练的参照，不是旧G10均匀采样实验的精确复现。教师改变会一起影响物理输入、起点基线和残差目标，不能把差异仅归因于标签或真实物理机制。

[冻结计划](ootang_teacher_refresh_plan.v1.0.md) · [配置](../config/ootang_teacher_refresh.v1_0.json) · [来源清单](ootang_teacher_refresh_sources.v1.0.json) · [数值核验](ootang_teacher_refresh_validation.v1.0.md) · [最终回执](../{cfg['out']}/final_receipt.json)

## 1. 完整预测比较

每窗均一次发出完整293日、四点预测，途中不接收位移观测；未来逐日降雨和水位给定。792窗为2018-09-01至2019-06-20，972窗为2019-02-28至2019-12-17，1168最终窗为2019-09-12至2020-06-30。612起点只作校准启动，也保存了完整路径。窗口已反复暴露并部分重叠，最终明确为探索性评价。

下表是先对三个种子的预测等权平均，再逐点计算RMSE、最后四点等权平均，单位mm；不是把所有点合并算一次RMSE，也不是种子RMSE的平均。

{summary}

DRIFT1是无需训练的匀速外推：未来位移=最后观测+h×最近一天位移增量。普通岭回归是同条件协议的固定特征对照。起点校正B+只加上最后观测与B+之间的起点偏差，原物理状态不改。

![三窗完整比较]({c.ROOT/cfg['figures']}/v1/teacher_policy_comparison.png)

## 2. 教师更新的配对结果

“变化”均为更新组减旧组，正值表示误差变大；最后一列统计同一种子下平均MAE和RMSE同时改善的数量，不代替集成结果。

{changes}

792窗退步最大；972窗虽有2/3种子同时改善，集成预测仍退步，不能挑种子宣称成功。最终更新组仅ATU5的RMSE低于旧组，ATU1、MJ3、MJ1均更高。两组在最终ATU1/MJ3仍落后B+，完整尾段未删除。

{points}

## 3. 概率质量与达标判断

最终窗口的平均CRPS与区间评分如下，越低越好。两组部分平均概率评分优于当前B+校准区间，但旧组ATU5覆盖仅53.92%，更新组ATU1仅69.62%，均低于冻结的逐点80%工作门槛；较高平均覆盖不足以完成概率目标。B+的100%覆盖伴随约201.64 mm平均宽度，也不代表概率预测已理想。

{probability}

均值门要求平均MAE/RMSE各改善至少1%，且每点不退步；概率门要求平均CRPS/90%区间评分各改善至少1%、逐点评分退步不超过5%、90%平均覆盖至少85%且各点至少80%。它们是本项目冻结工作条件，不能称为导师已验收标准。

{gates}

旧组在前两个历史窗通过对B+均值门，更新组只在972窗通过；两组均未通过对DRIFT1的完整逐点均值门。最终两组的平均MAE/RMSE虽优于DRIFT1和普通岭回归，逐点保护仍失败，不能表述为全面超过简单或机器学习对照。完整27组判定、各点各级区间、各个种子均保存在原表中。

## 4. 教师确实改变了什么

10个新增历史前缀各执行一次原54参数B+的终点加权更新。下表只描述各自训练前缀，RMSE及终点绝对误差均为四点平均，单位mm；它不是未来预测评分。

{fits}

更新后有8/10个前缀降低了拟合RMSE，672与912两前缀反而更高；所有终点绝对误差明显减小。这与终点加权目标一致，却没有转化为后续GRU的预测收益。由于网络本已通过r0消除起点偏差，单纯把教师终点贴近观测并不足以支持额外的长窗收益。这是本轮结果的解释，不是唯一失败原因的识别。

**10次物理优化全部达到800次函数评价上限，优化器收敛标志均为false；参数和状态均通过有限性及重放核验。** 本次是有限预算的单阶段更新，不是导师全套标定的完整复现或已收敛最优教师。因此能否由更充分拟合得到不同结果仍未验证；本轮按事前规则保留这些输出并完成所有比较，没有补迭代或按成绩换教师。

各外层前缀612/792/972/1168对应完整成熟293日监督起点仅0/2/5/8个，而且大量重叠。最终最晚的完整监督起点为852，更近的新教师只有部分成熟目标。972发报仍复用792教师，最终发报使用原1168教师。本次改善了训练教师的时间更新频率，但没有消除全部距离支持及使用场景差异，也不能分别量化未收敛、监督稀少和场景差异的贡献。

## 5. 给导师看的四点图

两图使用相同纵轴范围，显示完整观测、两组均值、本组三种子和原80%/95%边际区间。蓝底是观测历史与B+拟合，橙底是独立条件预测；没有补画不存在的神经训练段重建，也没有平滑或缩窄区间。

![旧教师GRU四点条件预测]({c.ROOT/cfg['figures']}/v1/cached_final.png)

![更新教师GRU四点条件预测]({c.ROOT/cfg['figures']}/v1/refresh_final.png)

[PNG/SVG下载及读图说明](../{cfg['figures']}/README.md) · [图件合同](ootang_teacher_refresh_figure_contract.v1.0.md) · [图形数据/字体/对齐核验](../{cfg['out']}/figure_qa_v1/receipt.json) · [逐图目视记录](../{cfg['figures']}/v1/visual_review.json)

## 6. 完成情况、限制与本轮结论

固定10次物理拟合、24次GRU拟合、4800次神经更新、96份检查点以及四次293日发报全部完成，效果差没有中断任何预定窗口。物理优化共8000次函数评价、388325次内部物理前向，正式后检20次；两轮预检累计4次固定前向，独立审计另20次，均单独计数。

预检149项通过；独立复算5022项、5588338个数值比较，最大差2.17e−12以内，涵盖原C状态重放、96个检查点重载及独立NumPy神经前向、成熟目标/日期/输入隔离、校准、全部评分和门槛。物理重放用原C求解器，不冒称另一物理求解器。三图12面板的349项、222772个数值及字体、遮挡、对齐和目视检查通过。

90条校准误差来自本方法上一已发出路径第91—180天；最终误差池[1062,1152)，距发报16天。相邻误差不是独立重复，高斯边际区间不保证293日同时覆盖。种子概率评分使用本组集成尺度。当前结果还保留数据as-of/预处理、历史反复暴露、旧B+优化和原矩阵乘法警告等限制；有限结果经显式求和核对，不声称底层警告成因已修复。

203项冻结来源哈希保持，18项原本未跟踪的本地运行时依赖已按原字节归档；16项对应版本化ZIP原件、两项为可追溯平台派生文件。[来源补充回执](../{cfg['out']}/runtime_source_receipt.json)记录映射，原始导师资料不改。图件交付封装的父目录相对路径错误只修封装根目录，图形和预测保持。

本轮应吸取的经验是：历史拟合更好、起点更贴近、训练继续下降，都需要通过完整跨窗预测来检验，不能自动等同可迁移收益。本次配对没有支持继续扩展已检验的教师更新残差路线，按冻结计划收束，保留B+及全部旧正负结果；不自动追加教师密度、训练轮数、λ/RL或换网络。它没有证明所有教师更新或残差方法无效，也未确定唯一失败原因。

实现、实验和核验已完成；均值与概率联合效果目标未完成；用户/导师尚未验收。新独立自限窗口11:01:47—13:01:47 UTC包含全部准备与核验，实际结束和用时见最终回执，剩余时间不转用。交付Markdown、CSV、PNG/SVG和预测/权重，本地分步提交，未push，未交付PDF报告。

[18组完整汇总CSV](../{cfg['out']}/analysis/phase_summary.csv) · [72组逐点CSV](../{cfg['out']}/analysis/metrics_by_point.csv) · [54组种子CSV](../{cfg['out']}/analysis/seed_summary.csv) · [21096行点日预测](../{cfg['out']}/analysis/daily_predictions.csv) · [配对差](../{cfg['out']}/analysis/paired_differences.csv) · [判定明细](../{cfg['out']}/analysis/pairing.json) · [教师拟合诊断](../{cfg['out']}/audit/teacher_fit_points.csv) · [成熟监督支持](ootang_teacher_refresh_support.v1.0.csv)
'''
    path=c.ROOT/'docs/ootang_teacher_refresh_results.v1.0.md';path.write_text(report)
    o.write_json(out/'table_ledger.json',dict(report=str(path.relative_to(c.ROOT)),source_files=sources,tables=tables))
    print(json.dumps(dict(report=str(path),tables=len(tables),rows=sum(len(t['rows']) for t in tables))))


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();main()
