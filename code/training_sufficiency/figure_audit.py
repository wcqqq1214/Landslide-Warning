"""Recompute figure series from source tables and verify actual SVG geometry."""
import json
import argparse
from pathlib import Path
import re
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from scipy.stats import norm
from PIL import Image
from . import core as c

NS={'s':'http://www.w3.org/2000/svg'}
XLINK='{http://www.w3.org/1999/xlink}href'
NUMBER=r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'


def main(attempt,figures):
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];ext=root/'extension'
    folder=c.ROOT/cfg['figures']/figures;out=root/attempt;out.mkdir(exist_ok=False)
    c.o.verify_lock(ext/'audit_lock.json');c.o.verify_lock(folder/'artifact_lock.json')
    checks=[];receipt=dict(status='running',source_max_difference=0.,svg_max_difference_pt=0.)

    def close(name,a,b,tol=1e-9,kind='source'):
        a,b=np.asarray(a,float),np.asarray(b,float)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),name
        difference=float(np.max(abs(a-b))) if a.size else 0.
        assert difference<=tol,(name,difference,tol)
        if kind in ['source','svg']:
            key='source_max_difference' if kind=='source' else 'svg_max_difference_pt'
            receipt[key]=max(receipt[key],difference)
        checks.append(dict(check=name,values=int(a.size),max_difference=difference,tolerance=tol,kind=kind))
    try:
        read=lambda p:pd.read_csv(p,float_precision='round_trip')
        tr=read(root/'diagnostic/training_summary.csv')
        tr400=read(ext/'audit/training400_summary.csv')
        history=pd.read_csv(root/'diagnostic/historical_summary.csv',dtype={'seed':str},float_precision='round_trip').set_index(['origin','arm','seed','step'])
        summary=read(ext/'analysis/phase_summary.csv').set_index(['origin','method'])
        seeds=read(ext/'analysis/seed_summary.csv').set_index(['origin','method','seed'])
        points=read(ext/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
        arrays=c.o.load_npz(folder/'source_arrays.npz');y=c.o.read_labels(c.ROOT/cfg['data'],1461)
        _,dates=c.o.read_forcing(c.ROOT/cfg['data'],1461);dx=mdates.date2num(pd.to_datetime(dates))
        close('full_y',arrays['observed'],y,0);assert np.array_equal(arrays['dates'],dates)
        close('full_B',arrays['bplus_full'],c.o.bank(cfg)[1168]['mean'],0)
        for source,suffix in [('means',''),('seeds','__seeds'),('sigmas','__sigma')]:
            for name,value in c.o.load_npz(ext/'origin_1168'/f'{source}.npz').items():close(f'{source}/{name}',arrays[name+suffix],value,0)
        exports=c.o.read_json(folder/'exports.json');trees={v['name']:ET.parse(folder/v['svg']).getroot() for v in exports['figures']}
        records=c.o.read_json(folder/'plot_records.json')
        for rec in records:
            r=rec['recipe'];kind=r['type'];key=rec['gid'];seed=r.get('seed');x=None;yy=None
            if kind=='train_ratio':
                rows=tr[tr.arm==r['arm']].set_index(['origin','seed','step'])
                chosen=cfg['seeds'] if seed is None else [seed];metric=r['metric'];x=np.array([0,50,100,200])
                yy=np.mean([[np.mean([rows.loc[(n,s,step),metric]/rows.loc[(n,s,0),metric] for n in cfg['origins']]) for step in x] for s in chosen],0)
            elif kind=='history':
                x=np.array([0,50,100,200]);yy=[history.loc[(r['origin'],r['arm'],r['seed'],step),'rmse'] for step in x]
            elif kind=='summary':
                x=np.arange(3);yy=[summary.loc[(n,r['method']),r['metric']] for n in cfg['origins'][1:]]
            elif kind=='budget_difference':
                x=np.arange(3)+(0 if seed is None else (seed-1)*.04)
                def score(n,step):return summary.loc[(n,r['arm']+f'_E{step}'),'rmse'] if seed is None else seeds.loc[(n,r['arm']+f'_E{step}',seed),'rmse']
                yy=[score(n,400)-score(n,200) for n in cfg['origins'][1:]]
            elif kind=='point_coverage':
                x=np.arange(4);yy=[100*points.loc[(1168,r['method'],p),'coverage90'] for p in cfg['points']]
            elif kind=='train_budget_change':
                x=np.arange(4)+(0 if seed is None else (seed-1)*.04);yy=[]
                for n in cfg['origins']:
                    a=tr[(tr.arm==r['arm'])&(tr.origin==n)&(tr.step==200)];b=tr400[(tr400.arm==r['arm'])&(tr400.origin==n)]
                    if seed is not None:a=a[a.seed==seed];b=b[b.seed==seed]
                    yy.append(100*(b.mse.mean()/a.mse.mean()-1))
            elif kind in ['trajectory','band']:
                method,p=r['method'],r['point'];x=dx if method in ['observed','bplus_full'] else dx[1168:]
                if kind=='trajectory':
                    data=arrays[method] if seed is None else arrays[method+'__seeds'][seed]
                    yy=data[:,p]-y[0,p]
                else:
                    center=arrays[method][:,p]-y[0,p];half=norm.ppf((1+r['level']/100)/2)*arrays[method+'__sigma'][p]
                    close(key+'/lower',rec['lo'],center-half,0);close(key+'/upper',rec['hi'],center+half,0)
                    vertices=np.asarray(rec['vertices']);assert len(vertices)==589
                    close(key+'/polygon_lower',vertices[1:294,1],center-half,0)
                    close(key+'/polygon_upper',vertices[295:588,1],(center+half)[::-1],0)
            else:raise AssertionError(kind)
            close(key+'/x',rec['x'],x,0)
            if yy is not None:close(key+'/values',rec['y'],yy,1e-10)
            vertices=np.asarray(rec['vertices']) if rec['kind']=='band' else np.column_stack([rec['x'],rec['y']])
            assert np.all((vertices[:,0]>=rec['xlim'][0])&(vertices[:,0]<=rec['xlim'][1])&(vertices[:,1]>=rec['ylim'][0])&(vertices[:,1]<=rec['ylim'][1])),('clipped scientific mark',key)
            tree=trees[rec['figure']];group=tree.find(f".//*[@id='{key}']");assert group is not None
            if rec.get('marker_only'):
                xy=np.array([[float(v.get('x')),float(v.get('y'))] for v in group.findall('.//s:use',NS)])
            else:
                if rec['kind']=='band':
                    use=group.find('.//s:use',NS);path=tree.find(f".//*[@id='{use.get(XLINK)[1:]}']")
                    offset=np.array([float(use.get('x','0')),float(use.get('y','0'))])
                else:path=group.find('s:path',NS);offset=np.zeros(2)
                assert path is not None and not re.search('[CQASTcqast]',path.get('d'))
                xy=np.array([float(v) for v in re.findall(NUMBER,path.get('d'))]).reshape(-1,2)+offset
                if rec['kind']=='band' and path.get('d').strip().lower().endswith('z'):xy=np.vstack([xy,xy[:1]])
            close(key+'/actual_svg',xy,rec['svg_xy'],1.1e-6,'svg')
        for note in c.o.read_json(folder/'annotations.json'):
            p=note['point'];a=note['arm'];metric=points
            expected=[metric.loc[(1168,a+'_E200',p),'rmse'],metric.loc[(1168,a+'_E400',p),'rmse'],metric.loc[(1168,'BPLUS_CONTINUOUS',p),'rmse'],100*metric.loc[(1168,a+'_E400',p),'coverage90']]
            close(note['figure']+'/'+p+'/annotations',[note[k] for k in ['rmse200','rmse400','rmse_bplus','coverage90']],expected,0)
            text=''.join(v.text or '' for v in trees[note['figure']].findall('.//s:text',NS))
            assert all(f'{v:.2f}' in text for v in expected[:3]) and f'{expected[3]:.1f}%' in text
        scripts=Path.home()/'.codex/skills/nature-figure/scripts';rendered=[]
        for item in exports['figures']:
            name=item['name'];tree=trees[name]
            assert len(tree.findall('.//s:text',NS))>0
            close(name+'/width_mm',float(tree.attrib['width'].removesuffix('pt'))/72*25.4,240,1e-6,'layout')
            close(name+'/height_mm',float(tree.attrib['height'].removesuffix('pt'))/72*25.4,170,1e-6,'layout')
            with Image.open(folder/item['png']) as img:
                assert img.size==(int(240/25.4*300),int(170/25.4*300));close(name+'/dpi',img.info['dpi'],[300,300],.01,'dpi')
            layout=c.o.read_json(folder/f'{name}.alignment.json');assert layout['verdict']=='PASS' and layout['auditable']
            assert c.o.read_json(folder/f'{name}.text_geometry.json')['passed']
            for mode,args in [('text',['audit_pdf_text.py',item['qa_pdf'],'--min-pt','5','--json']),('collision',['audit_figure_collisions.py',item['qa_pdf'],'--json'])]:
                result=subprocess.run([sys.executable,str(scripts/args[0]),*args[1:]],capture_output=True,text=True)
                (out/f'{name}.{mode}.json').write_text(result.stdout);(out/f'{name}.{mode}.stderr.txt').write_text(result.stderr)
                assert result.returncode==0,(name,mode,result.stdout)
                rendered.append(dict(figure=name,audit=mode,returncode=result.returncode))
        result=subprocess.run([sys.executable,str(scripts/'validate_figure.py'),str(c.ROOT/'code/training_sufficiency/figures.py'),'--json'],capture_output=True,text=True)
        (out/'source_qa.json').write_text(result.stdout);sourceqa=json.loads(result.stdout);assert sourceqa['summary']['counts']['FAIL']==0
        assert len(exports['figures'])==4 and len(c.o.read_json(folder/'annotations.json'))==8 and not list(folder.glob('*.pdf'))
        receipt.update(status='passed',figures=4,panels=16,records=len(records),checks=len(checks),values=sum(v['values'] for v in checks),
                       all_scientific_marks_inside_axes=True,rendered_audits=rendered,visual_review='separate record',
                       source_warning_review=['PNG300dpi and SVG are the frozen report deliverables; no TIFF required',
                          'Measured final width240mm overrides heuristic misreading of240/25.4',
                          'Forecast annotations are placed below plot rectangles and checked in every panel'],
                       initial_render_issue='v1 budget panel a fixed y range before data; v2 fixed limits but forecast annotation collision gate failed; v3 moves forecast annotations below plots; no data or model changes')
    except Exception:
        receipt.update(status='failed',error=traceback.format_exc());raise
    finally:
        c.o.write_json(out/'checks.json',checks);c.o.write_json(out/'receipt.json',receipt)
    c.o.lock(out,'lock.json',list(out.glob('*')),status='passed')
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='figure_qa_v3');parser.add_argument('--figures',default='v3');args=parser.parse_args()
    main(args.attempt,args.figures)
