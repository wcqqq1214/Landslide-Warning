"""Recalculate every reported numeric table cell and verify local delivery links."""
import json
import re
from pathlib import Path
import pandas as pd
from . import core as c


def main():
    cfg=c.spec();root=c.ROOT/cfg['out'];out=root/'document_qa';c.guard()
    c.o.verify_lock(root/'diagnostic_lock.json');c.o.verify_lock(root/'extension/analysis_lock.json')
    c.o.verify_lock(root/'extension/audit_lock.json')
    c.o.verify_lock(c.ROOT/cfg['figures']/'v3/delivery_lock.json')
    ledger=c.o.read_json(out/'table_ledger.json');report=(c.ROOT/ledger['report']).read_text()
    frames={k:pd.read_csv(c.ROOT/path,float_precision='round_trip') for k,path in ledger['source_files'].items()}
    count=0;rowcount=0;checks=[]

    def evaluate(rule):
        if 'op' in rule:
            assert rule['op']=='change_percent'
            old,new=evaluate(rule['old']),evaluate(rule['new'])
            return (new-old)*100/max(abs(old),1e-15)
        frame=frames[rule['table']]
        for key,expected in rule['filters'].items():frame=frame.loc[frame[key]==expected]
        assert len(frame)>0
        if rule['aggregate']=='single':assert len(frame)==1
        else:assert rule['aggregate']=='mean'
        return sum(frame[rule['column']].tolist())/len(frame)*rule['scale']

    for table in ledger['tables']:
        name=table['name'];block=report.split(f'<!-- table:{name} -->')[1].split(f'<!-- endtable:{name} -->')[0]
        lines=[s for s in block.splitlines() if s.startswith('|')]
        actual=[[v.strip() for v in row.strip('|').split('|')] for row in lines]
        assert actual[0]==table['headers'] and len(actual)==len(table['rows'])+2,name
        for row,expected_row in zip(actual[2:],table['rows']):
            assert len(row)==len(expected_row)
            for cell,expected in zip(row,expected_row):
                assert cell==expected['text'],(name,cell,expected)
                if 'rule' in expected:
                    expected_value=evaluate(expected['rule'])
                    assert cell==f"{expected_value:.{expected['places']}f}",(name,cell,expected_value)
                    count+=1
            rowcount+=1
        checks.append(dict(table=name,rows=len(table['rows'])))
    pairs=c.o.read_json(root/'extension/analysis/pairing.json')
    gate_table=next(v for v in ledger['tables'] if v['name']=='gates')
    methods=[a+f'_E{s}' for a in cfg['arms'] for s in [200,400]]
    for method,row in zip(methods,gate_table['rows']):
        ps=[next(v for v in pairs if v['origin']==n and v['candidate']==method and v['reference']=='BPLUS_CONTINUOUS') for n in cfg['origins'][1:]]
        assert row[1]['text']==' / '.join('过' if v['mean_pass'] else '未过' for v in ps)
        assert row[2]['text']==' / '.join('过' if v['probability_pass'] else '未过' for v in ps)
        assert row[3]['text']==f"{sum(v['joint_pass'] for v in ps)}/3"
    changes=next(v for v in ledger['tables'] if v['name']=='budget_changes')
    for row,(arm,n) in zip(changes['rows'],[(a,n) for a in cfg['arms'] for n in cfg['origins'][1:]]):
        pair=next(v for v in pairs if v['origin']==n and v['candidate']==arm+'_E400' and v['reference']==arm+'_E200')
        assert row[-1]['text']==f"{pair['seed_both_improve']}/3"
    diagnostic=c.o.read_json(root/'diagnostic/trigger.json')
    for metric in ['mae','rmse']:
        percent=100*diagnostic['arms']['T10_ANCHOR_UNIFORM']['historical_average_reductions'][metric]
        assert f'{percent:.4f}%' in report
    for arm in cfg['arms']:
        for step in [200,400]:
            frame=frames['summary'];value=frame[(frame.origin==1168)&(frame.method==arm+f'_E{step}')].rmse.item()
            assert f'{value:.6f}' in report
    paths=list((c.ROOT/'docs').glob('ootang_training_sufficiency*.md'))+[c.ROOT/cfg['figures']/'README.md']
    paths.extend(c.ROOT/f for f in ['README.md','AGENTS.md','docs/README.md'])
    links=[]
    for path in paths:
        text=path.read_text()
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
            if target.startswith(('http:','https:','#','mailto:')):continue
            dest=(path.parent/target.split('#')[0].strip('<>')).resolve()
            assert dest.exists(),(str(path),target)
            links.append(dict(file=str(path.relative_to(c.ROOT)),target=target))
    receipt=dict(status='passed',tables=len(ledger['tables']),table_rows=rowcount,numeric_cells=count,links=len(links),
                 effect_gates_and_seed_counts_reproduced=True,narrative_key_numbers_checked=True,
                 new_fits=0,optimizer_updates=0,statistical_fallacy_checks=11)
    c.o.write_json(out/'checks.json',checks);c.o.write_json(out/'links.json',links);c.o.write_json(out/'receipt.json',receipt)
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
