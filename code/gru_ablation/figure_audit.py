"""Verify saved data against exported SVG geometry and rendered text."""
import argparse
import json
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

from . import core as c
o=c.old
NS={'s':'http://www.w3.org/2000/svg'}
XLINK='{http://www.w3.org/1999/xlink}href'
NUMBER=r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'


def main(attempt, figures):
    cfg=c.spec(); c.guard(); root=c.ROOT/cfg['out']; folder=c.ROOT/cfg['figures']/figures
    out=root/attempt;out.mkdir(exist_ok=False)
    receipt=dict(status='running',started_utc=o.utc(),values=0,max_source_difference=0.,max_svg_difference_pt=0.,max_layout_difference_mm=0.,new_fits=0,physical_forwards=0)
    checks=[]

    def close(name,a,b,tol,kind='source'):
        a,b=np.asarray(a,float),np.asarray(b,float)
        assert a.shape==b.shape,(name,a.shape,b.shape)
        assert np.isfinite(a).all() and np.isfinite(b).all()
        diff=float(np.max(abs(a-b))) if a.size else 0
        assert diff<=tol,(name,diff,tol)
        receipt['values']+=int(a.size);key={'svg':'max_svg_difference_pt','layout':'max_layout_difference_mm'}.get(kind,'max_source_difference')
        receipt[key]=max(receipt[key],diff)
        checks.append(dict(check=name,values=int(a.size),max_difference=diff,tolerance=tol,kind=kind))

    try:
        o.verify_lock(root/'analysis_lock.json')
        records=o.read_json(folder/'plot_records.json');exports=o.read_json(folder/'exports.json')
        source=o.load_npz(folder/'source_arrays.npz')
        y=o.read_labels(c.ROOT/cfg['data'],1461);_,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
        np.testing.assert_array_equal(source['dates'],dates);close('observations',source['observed'],y,0)
        close('y0',source['y0'],y[0],0);close('full_B',source['bplus_full'],o.bank(cfg)[1168]['mean'],0)
        for name,suffix in [('means',''),('seeds','__seeds'),('sigmas','__sigma')]:
            values=o.load_npz(root/'origin_1168'/f'{name}.npz')
            for method,value in values.items():close(f'source/{name}/{method}',source[method+suffix],value,0)
        summary=pd.read_csv(root/'analysis/phase_summary.csv').set_index(['origin','method'])
        seeds=pd.read_csv(root/'analysis/seed_summary.csv').set_index(['origin','method','seed'])
        endpoints=pd.read_csv(root/'analysis/endpoint_errors.csv',dtype={'seed':str})
        endpoints=endpoints[endpoints.horizon==1].groupby(['origin','method','seed']).absolute_error.mean()
        factors=pd.read_csv(root/'analysis/factorial_summary.csv').set_index(['origin','metric'])
        factorseeds=pd.read_csv(root/'analysis/factorial_seeds.csv').set_index(['origin','seed','metric'])
        points=pd.read_csv(root/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
        trees={e['name']:ET.parse(folder/e['svg']).getroot() for e in exports['figures']}
        dx=mdates.date2num(pd.to_datetime(dates))
        for rec in records:
            name,gid,method,seed=rec['figure'],rec['gid'],rec['method'],rec.get('seed')
            if name=='factorial_overview':
                x=np.arange(3)+(0 if seed is None else (seed-1)*.035)
                if method in ('a_main','b_main','interaction'):
                    yy=[factors.loc[(n,'rmse'),method] if seed is None else factorseeds.loc[(n,seed,'rmse'),method] for n in cfg['origins'][1:]]
                elif gid.startswith('p1_'):
                    yy=[endpoints.loc[(n,method,'ensemble' if seed is None else str(seed))] for n in cfg['origins'][1:]]
                else:
                    metric='rmse' if gid.startswith('p0_') else 'crps'
                    yy=[summary.loc[(n,method),metric] if seed is None else seeds.loc[(n,method,seed),metric] for n in cfg['origins'][1:]]
                close(gid+'/x',rec['x'],x,0);close(gid+'/values',rec['y'],yy,1e-10)
            else:
                p=cfg['points'].index(rec['point']);x=dx if method in ('observed',c.B) else dx[1168:]
                close(gid+'/x',rec['x'],x,0)
                if rec['kind']=='band':
                    mu=source[method][:,p]-y[0,p];q=norm.ppf((1+rec['level']/100)/2)*source[method+'__sigma'][p]
                    close(gid+'/lower',rec['lo'],mu-q,0);close(gid+'/upper',rec['hi'],mu+q,0)
                    vertices=np.asarray(rec['vertices'])
                    # Every lower/upper date is present in the rendered polygon, with only closure duplicates.
                    assert len(vertices)==2*293+3
                    close(gid+'/vertices_x',vertices[1:294,0],x,0)
                    close(gid+'/vertices_lower',vertices[1:294,1],mu-q,0)
                    close(gid+'/vertices_upper',vertices[295:588,1],(mu+q)[::-1],0)
                else:
                    yy=(y[:,p] if method=='observed' else source['bplus_full'][:,p] if method==c.B
                        else source[method][:,p] if seed is None else source[method+'__seeds'][seed,:,p])-y[0,p]
                    close(gid+'/values',rec['y'],yy,0)
            tree=trees[name];group=tree.find(f".//*[@id='{gid}']");assert group is not None
            if rec.get('marker_only',False):
                xy=np.array([[float(el.get('x')),float(el.get('y'))] for el in group.findall('.//s:use',NS)])
            else:
                if rec['kind']=='band':
                    use=group.find('.//s:use',NS);assert use is not None
                    path=tree.find(f".//*[@id='{use.get(XLINK)[1:]}']")
                    offset=np.array([float(use.get('x','0')),float(use.get('y','0'))])
                else:
                    path=group.find('s:path',NS);offset=np.zeros(2)
                assert path is not None and not re.search('[CQASTcqast]',path.get('d'))
                xy=np.array([float(v) for v in re.findall(NUMBER,path.get('d'))]).reshape(-1,2)+offset
                # SVG encodes the final polygon closure implicitly with z; Matplotlib stores its vertex.
                if rec['kind']=='band' and path.get('d').strip().lower().endswith('z'):
                    assert len(rec['svg_xy'])==len(xy)+1
                    close(gid+'/closure',rec['svg_xy'][-1],rec['svg_xy'][0],0)
                    xy=np.vstack([xy,xy[:1]])
            close(gid+'/svg',xy,rec['svg_xy'],1.1e-6,'svg')
            assert len(tree.findall('.//s:text',NS))>0
        for note in o.read_json(folder/'annotations.json'):
            figure,point=note['figure'],note['point']; arm=next(a for a in cfg['arms'] if figure==a.lower()+'_four_points')
            close(figure+'/'+point+'/label', [note['rmse'],note['bplus_rmse']],
                  [points.loc[(1168,arm,point),'rmse'],points.loc[(1168,c.B,point),'rmse']],0)
            text=''.join(t.text or '' for t in trees[figure].findall('.//s:text',NS))
            assert f"{note['rmse']:.2f}" in text and f"{note['bplus_rmse']:.2f}" in text
        scripts=Path.home()/'.codex/skills/nature-figure/scripts'
        rendered=[]
        for item in exports['figures']:
            name=item['name'];pdf=Path(item['qa_pdf']);assert pdf.exists()
            layout=o.read_json(folder/f'{name}.alignment.json');geometry=o.read_json(folder/f'{name}.text_geometry.json')
            assert geometry['passed'] and layout['verdict']=='PASS' and layout['auditable']
            for mode,args in [('text',['audit_pdf_text.py',str(pdf),'--min-pt','5','--json']),
                              ('collision',['audit_figure_collisions.py',str(pdf),'--json'])]:
                result=subprocess.run([sys.executable,str(scripts/args[0]),*args[1:]],capture_output=True,text=True)
                (out/f'{name}.{mode}.json').write_text(result.stdout)
                (out/f'{name}.{mode}.stderr.txt').write_text(result.stderr)
                assert result.returncode==0,(name,mode,result.stdout,result.stderr)
                rendered.append(dict(figure=name,audit=mode,returncode=result.returncode))
        sourceqa=o.read_json(root/f'figure_source_qa_{figures}.json');assert sourceqa['summary']['counts']['FAIL']==0
        o.verify_lock(root/'analysis_lock.json');c.guard()
        receipt.update(status='passed',completed_utc=o.utc(),figures=5,panels=20,plot_records=len(records),
                       checks=len(checks),rendered_audits=rendered,visual_review='separate manual record required',
                       source_warnings=['PNG/SVG mentor delivery does not require TIFF',
                           '300dpi meets this frozen display contract',
                           'source parser misreads 240/25.4; SVG measured width is checked as 240mm below'])
        for name,tree in trees.items():
            width=float(tree.attrib['width'].removesuffix('pt'))
            close(name+'/width_mm',width/72*25.4,240,1e-6,'layout')
        receipt['checks']=len(checks)
        assert not list(folder.glob('*.pdf'))
    except Exception:
        receipt.update(status='failed',completed_utc=o.utc(),error=traceback.format_exc());raise
    finally:
        o.write_json(out/'checks.json',checks);o.write_json(out/'receipt.json',receipt)
    o.lock(out,'lock.json',list(out.glob('*')),status='passed')
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='figure_qa_v1');parser.add_argument('--figures',default='v1')
    args=parser.parse_args();main(args.attempt,args.figures)
