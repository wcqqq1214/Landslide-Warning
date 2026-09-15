"""Read back reported tables and verify their data, decisions and local links."""
import json
import re
import pandas as pd
from . import core as c


def main():
    cfg=c.spec();o=c.o;root=c.ROOT/cfg['out'];out=root/'document_qa';c.guard()
    o.verify_lock(root/'analysis_lock.json');o.verify_lock(root/'audit_lock.json')
    o.verify_lock(c.ROOT/cfg['figures']/'delivery_lock.json')
    ledger=o.read_json(out/'table_ledger.json');report=(c.ROOT/ledger['report']).read_text()
    frames={k:pd.read_csv(c.ROOT/v,float_precision='round_trip') for k,v in ledger['source_files'].items()}
    summary=frames['summary'].set_index(['origin','method'])
    windows=cfg['origins'][1:];checks=[];values=0;rows=0

    def calc(rule):
        frame=frames[rule['table']]
        for key,value in rule['filters'].items():frame=frame[frame[key]==value]
        v=frame[rule['column']].tolist();assert v
        if rule['aggregate']=='single':assert len(v)==1
        elif rule['aggregate']=='mean_abs':v=[abs(x) for x in v]
        else:assert rule['aggregate']=='mean'
        return sum(v)/len(v)*rule['scale']

    for table in ledger['tables']:
        name=table['name']
        block=report.split(f'<!-- table:{name} -->')[1].split(f'<!-- endtable:{name} -->')[0]
        actual=[[cell.strip() for cell in line.strip('|').split('|')] for line in block.splitlines() if line.startswith('|')]
        assert actual[0]==table['headers'] and len(actual)==len(table['rows'])+2,name
        for actual_row,expected_row in zip(actual[2:],table['rows']):
            assert len(actual_row)==len(expected_row)
            for actual_cell,expected in zip(actual_row,expected_row):
                assert actual_cell==expected['text'],(name,actual_cell,expected)
                if 'rule' in expected:
                    value=calc(expected['rule'])
                    assert actual_cell==f"{value:.{expected['places']}f}",(name,actual_cell,value)
                    values+=1
            rows+=1
        checks.append(dict(table=name,rows=len(table['rows'])))
    pairs=o.read_json(root/'analysis/pairing.json')

    def pair(n,arm,ref):return next(x for x in pairs if x['origin']==n and x['candidate']==arm and x['reference']==ref)

    gate=next(t for t in ledger['tables'] if t['name']=='gates')
    for arm,row in zip(cfg['arms'],gate['rows']):
        ps=[pair(n,arm,c.B) for n in windows]
        assert row[1]['text']==' / '.join('过' if p['mean_pass'] else '未过' for p in ps)
        for col,key in [(2,'probability_pass'),(3,'joint_pass')]:assert row[col]['text']==f"{sum(p[key] for p in ps)}/3"
        for col,ref in [(4,'DRIFT1'),(5,'RR_COND')]:
            assert row[col]['text']==f"{sum(pair(n,arm,ref)['mean_pass'] for n in windows)}/3"
    delta=next(t for t in ledger['tables'] if t['name']=='paired_changes')
    for n,row in zip(windows,delta['rows']):
        a,b=(summary.loc[(n,m)] for m in cfg['arms']);diff=float(b.rmse-a.rmse)
        assert row[3]['text']==f'{diff:.6f}' and row[4]['text']==f'{100*diff/a.rmse:.2f}'
        assert row[-1]['text']==f"{pair(n,'G_REFRESH','G_CACHED')['seed_both_improve']}/3"
        assert b.mae>a.mae and b.rmse>a.rmse
    fit_rows=next(t for t in ledger['tables'] if t['name']=='teacher_fits')['rows']
    reduced=[]
    for m,row in zip(cfg['teacher_update']['new_prefixes'],fit_rows):
        fit=o.read_json(root/f'teachers/fit_{m}/fit.json')
        assert fit['nfev']==800 and fit['optimizer_success'] is False and row[-1]['text']=='未收敛'
        subset=frames['fits'][frames['fits'].fit_prefix==m]
        def avg(policy,column,absolute=False):
            vals=subset[subset.policy==policy][column].tolist();assert len(vals)==4
            return sum(abs(v) if absolute else v for v in vals)/4
        if avg('G_REFRESH','fit_rmse')<avg('G_CACHED','fit_rmse'):reduced.append(m)
        assert avg('G_REFRESH','terminal_error',True)<avg('G_CACHED','terminal_error',True)
    assert len(reduced)==8 and set(cfg['teacher_update']['new_prefixes'])-set(reduced)=={672,912}
    assert '8/10' in report
    for m in [c.B,*cfg['arms']]:assert f'{summary.loc[(1168,m),"rmse"]:.6f}' in report
    point=frames['points'].set_index(['origin','method','point'])
    for p in cfg['points']:
        old,new=(point.loc[(1168,m,p),'rmse'] for m in cfg['arms'])
        assert (new<old)==(p=='ATU5')
    for m,p in [('G_CACHED','ATU5'),('G_REFRESH','ATU1')]:
        value=point.loc[(1168,m,p),'coverage90']*100
        assert f'{value:.2f}%' in report and value<80
    for arm in cfg['arms']:
        for ref in ['DRIFT1','RR_COND']:
            for key in ['mae','rmse']:assert summary.loc[(1168,arm),key]<summary.loc[(1168,ref),key]
            assert pair(1168,arm,ref)['mean_pass'] is False
    daily=pd.read_csv(root/'analysis/daily_predictions.csv',float_precision='round_trip')
    for n,frame in daily.groupby('origin'):
        assert str(frame.date.min()) in report and str(frame.date.max()) in report
        assert frame.distance.min()==1 and frame.distance.max()==293
    readme=(c.ROOT/'README.md').read_text()
    readme_rows=[line for line in readme.splitlines() if line.startswith(('| 792','| 972','| 1168'))]
    assert len(readme_rows)==3
    for n,line in zip(windows,readme_rows):
        actual=[x.strip() for x in line.strip('|').split('|')][1:]
        assert actual==[f'{summary.loc[(n,m),"rmse"]:.6f}' for m in [c.B,*cfg['arms']]]
    audit=o.read_json(root/'audit/receipt.json');figure=o.read_json(root/'figure_qa_v1/receipt.json')
    experiment=o.read_json(root/'experiment_receipt.json');outcome=o.read_json(root/'analysis/outcome.json')
    assert audit['values_checked']==5588338 and audit['checkpoints']==96 and figure['values']==222772
    assert experiment['new_bplus_fits']==10 and experiment['neural_new_fits']==24 and experiment['neural_updates']==4800
    assert outcome['stable_mean_policy'] is False and outcome['replacement_goal_met'] is False
    assert all(v==0 for v in outcome['joint_bplus_pass_counts'].values())
    for token in ['未收敛','单阶段','探索性','0/2/5/8','部分成熟目标','16天','不自动追加','尚未验收','原矩阵乘法警告']:
        assert token in report,token
    paths=list((c.ROOT/'docs').glob('ootang_teacher_refresh*.md'))+[c.ROOT/cfg['figures']/'README.md']
    paths.extend(c.ROOT/p for p in ['README.md','AGENTS.md','docs/README.md'])
    links=[]
    for path in paths:
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',path.read_text()):
            if target.startswith(('https:','http:','#','mailto:')):continue
            dest=(path.parent/target.split('#')[0].strip('<>')).resolve()
            assert dest.exists(),(str(path),target)
            links.append(dict(file=str(path.relative_to(c.ROOT)),target=target))
    receipt=dict(status='passed',tables=len(ledger['tables']),table_rows=rows,numeric_cells=values,links=len(links),
                 paired_changes_independently_reconstructed=True,gate_and_seed_counts_verified=True,
                 teacher_fit_interpretation_verified=True,narrative_key_numbers_verified=True,
                 readme_metric_cells=9,new_fits=0,optimizer_updates=0,physical_forwards=0)
    o.write_json(out/'checks.json',checks);o.write_json(out/'links.json',links);o.write_json(out/'receipt.json',receipt)
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
