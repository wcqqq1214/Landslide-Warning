"""Read-only numeric and local-link checks for the current result report."""
import json
from pathlib import Path
import re
import urllib.parse

import numpy as np
import pandas as pd
from . import core as c
o=c.old


def main():
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];o.verify_lock(root/'analysis_lock.json')
    doc=c.ROOT/'docs/ootang_gru_ablation_results.v1.0.md';text=doc.read_text()
    bindings=o.read_json(root/'report_tables.json');parsed={};values=0;maxdifference=0
    for key,binding in bindings.items():
        part=text.split(f'<!-- table:{key} -->')[1].split(f'<!-- endtable:{key} -->')[0]
        rows=[[cell.strip() for cell in line.strip().strip('|').split('|')] for line in part.splitlines() if line.startswith('|')]
        assert rows[0]==binding['headers'] and rows[2:]==binding['rows'],key
        parsed[key]=rows[2:]
    score=pd.read_csv(root/'analysis/phase_summary.csv').set_index(['origin','method'])
    point=pd.read_csv(root/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
    endpoint=pd.read_csv(root/'analysis/endpoint_errors.csv',dtype={'seed':str})
    endpoint=endpoint[endpoint.seed=='ensemble'].groupby(['origin','method','horizon']).absolute_error.mean()
    factorial=pd.read_csv(root/'analysis/factorial_summary.csv').set_index(['origin','metric'])
    pairings={(r['origin'],r['candidate'],r['reference']):r for r in o.read_json(root/'analysis/pairing.json')}

    def equal(s,v,percent=False):
        nonlocal values,maxdifference
        a=float(s.rstrip('%'));b=float(v)*(100 if percent else 1)
        difference=abs(a-b);assert difference<=(.005000001 if percent else .000000501),(s,v)
        values+=1;maxdifference=max(maxdifference,difference)

    methods=cfg['controls']+cfg['arms']
    for row,method in zip(parsed['rmse'],methods):
        for s,n in zip(row[1:],cfg['origins'][1:]):equal(s,score.loc[(n,method),'rmse'])
    for row,method in zip(parsed['day1'],[c.B,c.BA]+cfg['arms']):
        for s,n in zip(row[1:],cfg['origins'][1:]):equal(s,endpoint.loc[(n,method,1)])
    for row,n in zip(parsed['factorial'],cfg['origins'][1:]):
        assert row[0]==str(n)
        for s,k in zip(row[1:],['a_main','b_main','interaction','b_at_anchor']):equal(s,factorial.loc[(n,'rmse'),k])
    for row,method in zip(parsed['probability'],methods):
        for s,k in zip(row[1:],['crps','interval_score90','coverage90','width90']):equal(s,score.loc[(1168,method),k],k=='coverage90')
    for row,p in zip(parsed['final_points'],cfg['points']):
        assert row[0]==p
        for s,(method,k) in zip(row[1:],[(c.B,'mae'),('G11_ANCHOR_BOUNDARY','mae'),(c.B,'rmse'),('G11_ANCHOR_BOUNDARY','rmse'),('G11_ANCHOR_BOUNDARY','coverage90')]):
            equal(s,point.loc[(1168,method,p),k],k=='coverage90')
    for row,(a,b) in zip(parsed['paired_seeds'],cfg['reporting']['paired_edges']):
        assert row[1:]==[str(pairings[n,a,b]['seed_both_improve'])+'/3' for n in cfg['origins'][1:]]
    for row,method in zip(parsed['bplus_gates'],cfg['arms']):
        assert row[1]==' / '.join('过' if pairings[n,method,c.B]['mean_pass'] else '未过' for n in cfg['origins'][1:])
        assert row[2]==' / '.join('过' if pairings[n,method,c.B]['probability_pass'] else '未过' for n in cfg['origins'][1:])
        assert row[3]==str(sum(pairings[n,method,c.B]['joint_pass'] for n in cfg['origins'][1:]))+'/3'
    _,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    for row,n,end in zip(parsed['windows'],cfg['origins'],cfg['ends']):
        assert row[:3]==[str(n),str(cfg['teacher_fit_prefixes'][str(n)]),f'{dates[n]}—{dates[end-1]}']
    # Verify headline numbers independently from the body table bindings.
    assert f"{(1-score.loc[(1168,'G11_ANCHOR_BOUNDARY'),'rmse']/score.loc[(1168,'G00_RAW_UNIFORM'),'rmse'])*100:.2f}%" in text
    for p,method in [('ATU1','G11_ANCHOR_BOUNDARY'),('ATU1','G10_ANCHOR_UNIFORM')]:
        assert f"{point.loc[(1168,method,p),'coverage90']*100:.2f}%" in text
    assert all(not pairings[n,a,c.B]['joint_pass'] for a in cfg['arms'] for n in cfg['origins'][1:])
    current_docs=[doc,c.ROOT/'README.md',c.ROOT/'docs/README.md',c.ROOT/'AGENTS.md',
        c.ROOT/'docs/ootang_gru_ablation_validation.v1.0.md',c.ROOT/cfg['figures']/'README.md']
    links=[]
    for path in current_docs:
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',path.read_text()):
            if re.match(r'^[a-z]+://',target) or target.startswith('#'):continue
            target=urllib.parse.unquote(target.strip('<>').split('#')[0]);destination=(path.parent/target).resolve()
            assert destination.exists(),(path,target)
            links.append(dict(document=str(path.relative_to(c.ROOT)),target=target))
    # README uses the same canonical three-window RMSE table.
    readme=(c.ROOT/'README.md').read_text()
    for n in cfg['origins'][1:]:
        cells=[f'{score.loc[(n,m),"rmse"]:.6f}' for m in [c.B]+cfg['arms']]
        assert '| '+' | '.join(cells)+' |' in readme
    rec=o.read_json(root/'final_receipt.json')
    assert rec['new_fits']==48 and rec['optimizer_updates']==9600 and rec['checkpoints']==192
    assert rec['full_horizon']==293 and rec['all_planned_methods_seeds_dates_completed']
    o.verify_lock(c.ROOT/cfg['figures']/'v2/artifact_lock.json');c.guard()
    out=root/'document_qa';out.mkdir(exist_ok=False)
    o.write_json(out/'links.json',links)
    o.write_json(out/'receipt.json',dict(status='passed',completed_utc=o.utc(),tables=len(parsed),
        table_rows=sum(len(v) for v in parsed.values()),numeric_values=values,local_links=len(links),
        rounding_tolerance='6 decimals for mm; two decimals for coverage percent',
        new_fits=0,physical_forwards=0,source_report_sha256=o.sha(doc),
        boundary='current report and navigation links; historical progress log links not reclassified'))
    o.lock(out,'lock.json',list(out.glob('*')),status='passed')
    print(json.dumps(o.read_json(out/'receipt.json'),ensure_ascii=False))


if __name__=='__main__':main()
