"""Five source-linked Python figures of all frozen ablation arms."""
import argparse
import importlib.util
from pathlib import Path
import tempfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
from scipy.stats import norm

from tcn_short_horizon.figures import text_geometry
from . import core as c

o = c.old
COLORS={c.B:'#666666',c.BA:'#9A8569','DRIFT1':'#BE8C3C','OLD_HALF':'#8A7B9B',
        'G00_RAW_UNIFORM':'#55768A','G10_ANCHOR_UNIFORM':'#137D8D',
        'T00_RAW_UNIFORM':'#BD8875','T10_ANCHOR_UNIFORM':'#AD4F61'}
NAMES={c.B:'改进 B+',c.BA:'仅起点校正 B+','DRIFT1':'DRIFT1','OLD_HALF':'旧Transformer半残差',
       'G00_RAW_UNIFORM':'GRU 原残差','G10_ANCHOR_UNIFORM':'GRU 起点表达',
       'T00_RAW_UNIFORM':'TF 原残差','T10_ANCHOR_UNIFORM':'TF 起点表达'}
STYLES={c.B:'--',c.BA:':','DRIFT1':'-.','OLD_HALF':':',
        'G00_RAW_UNIFORM':'-','G10_ANCHOR_UNIFORM':'--','T00_RAW_UNIFORM':'-','T10_ANCHOR_UNIFORM':'--'}
MARKERS=dict(zip(NAMES,['x','+','d','P','s','o','D','X']))


def main(attempt):
    cfg=c.spec(); c.guard(); root=c.ROOT/cfg['out']
    assert o.read_json(root/'independent_audit/receipt.json')['status']=='passed'
    o.verify_lock(root/'analysis_lock.json')
    out=c.ROOT/cfg['figures']/attempt; out.mkdir(parents=True,exist_ok=False)
    qa=Path(tempfile.mkdtemp(prefix='ootang-backbone-anchor-figure-qa-'))
    module_spec=importlib.util.spec_from_file_location('panel_alignment',
        Path.home()/'.codex/skills/nature-figure/scripts/audit_panel_alignment.py')
    alignment=importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(alignment)
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial Unicode MS','DejaVu Sans'],
        'font.size':8,'axes.labelsize':8.2,'axes.titlesize':9.4,'axes.spines.top':False,
        'axes.spines.right':False,'axes.linewidth':.7,'svg.fonttype':'none','pdf.fonttype':42,
        'axes.unicode_minus':False,'legend.frameon':False,'path.simplify':False})
    y=o.read_labels(c.ROOT/cfg['data'],1461); _,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    datex=mdates.date2num(pd.to_datetime(dates)); n=1168
    means,seeds,sigmas=[o.load_npz(root/f'origin_{n}'/f'{k}.npz') for k in ('means','seeds','sigmas')]
    bplus=o.bank(cfg)[1168]['mean']; y0=y[0]
    np.savez_compressed(out/'source_arrays.npz',dates=dates,observed=y,y0=y0,bplus_full=bplus,
        **means,**{k+'__seeds':v for k,v in seeds.items()},**{k+'__sigma':v for k,v in sigmas.items()})
    summary=pd.read_csv(root/'analysis/phase_summary.csv').set_index(['origin','method'])
    seedsummary=pd.read_csv(root/'analysis/seed_summary.csv').set_index(['origin','method','seed'])
    points=pd.read_csv(root/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
    endpoint=pd.read_csv(root/'analysis/endpoint_errors.csv',dtype={'seed':str})
    endpoint=endpoint[endpoint.horizon==1].groupby(['origin','method','seed']).absolute_error.mean()
    factorial=pd.read_csv(root/'analysis/factorial_summary.csv').set_index(['origin','metric'])
    factorialseed=pd.read_csv(root/'analysis/factorial_seeds.csv').set_index(['origin','seed','metric'])
    records=[];exports=[]; notes=[]

    def canvas(title, subtitle):
        fig,axes=plt.subplots(2,2,figsize=(240/25.4,170/25.4))
        fig.subplots_adjust(left=.09,right=.977,bottom=.18,top=.775,wspace=.24,hspace=.55)
        fig.suptitle(title,y=.971,fontsize=12,fontweight='bold')
        fig.text(.5,.932,subtitle,ha='center',fontsize=8.1,color='#444444')
        return fig,axes.ravel()

    def line(fig,ax,name,gid,x,y,recipe,*,method=None,seed=None,point=None,**style):
        artist=ax.plot(x,y,gid=gid,**style)[0]
        records.append(dict(figure=name,gid=gid,kind='line',x=np.asarray(x,float).tolist(),
            y=np.asarray(y,float).tolist(),recipe=recipe,method=method,seed=seed,point=point,
            axes=fig.axes.index(ax),marker_only=style.get('ls')=='None'))
        return artist

    def band(fig,ax,name,gid,x,lo,hi,method,point,level):
        artist=ax.fill_between(x,lo,hi,color=COLORS[method],alpha=.10 if level==95 else .18,
            linewidth=0,gid=gid,zorder=1)
        records.append(dict(figure=name,gid=gid,kind='band',method=method,point=point,level=level,
            x=np.asarray(x,float).tolist(),lo=np.asarray(lo,float).tolist(),hi=np.asarray(hi,float).tolist(),
            vertices=artist.get_paths()[0].vertices.tolist(),axes=fig.axes.index(ax),
            recipe='ensemble minus y0 plus/minus normal quantile times own frozen calibration RMS'))

    def finish(fig,name):
        fig.canvas.draw()
        geometry=text_geometry(fig);o.write_json(out/f'{name}.text_geometry.json',geometry)
        assert geometry['passed'],geometry
        alignment.require_matplotlib_panel_alignment(fig,json_out=out/f'{name}.alignment.json',
            tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
        for record in (r for r in records if r['figure']==name):
            vertices=(np.array(record['vertices']) if record['kind']=='band'
                      else np.column_stack([record['x'],record['y']]))
            xy=fig.axes[record['axes']].transData.transform(vertices)
            record['svg_xy']=np.column_stack([xy[:,0]*72/fig.dpi,(fig.bbox.height-xy[:,1])*72/fig.dpi]).tolist()
        fig.savefig(out/f'{name}.svg')
        fig.savefig(out/f'{name}.png',dpi=300)
        # This same-backend temporary file is only an input to rendered text/collision QA.
        fig.savefig(qa/f'{name}.pdf')
        plt.close(fig);exports.append(dict(name=name,png=f'{name}.png',svg=f'{name}.svg',qa_pdf=str(qa/f'{name}.pdf'),panels=4))

    name='factorial_overview'
    fig,axes=canvas('起点表达降低平均误差，本轮GRU起点版优于TF起点版',
        '统一输入、图与解码器 | GRU 1241 / Transformer 1249参数 | 三种子、固定200更新')
    order=[c.B,c.BA,'DRIFT1','OLD_HALF']+cfg['arms']
    fig.legend(handles=[Line2D([],[],color=COLORS[m],ls=STYLES[m],marker=MARKERS[m],ms=3.7,lw=1,label=NAMES[m]) for m in order],
        loc='upper center',bbox_to_anchor=(.5,.908),ncol=4,fontsize=7.7,columnspacing=1.3)
    titles=['a  完整293日：均值误差','b  起点表达的收益能否迁移？',
            'c  相同表达下：TF减GRU','d  完整293日：概率评分']
    for ax,title in zip(axes,titles):
        ax.set_title(title,loc='left',fontweight='bold',pad=8)
        ax.set_xticks([0,1,2],['792\n历史窗1','972\n历史窗2','1168\n最终探索'])
        ax.set_xlim(-.16,2.16);ax.set_axisbelow(True)
    for pi,metric,label in [(0,'rmse','四点平均 RMSE / mm'),(3,'crps','四点平均 CRPS / mm')]:
        ax=axes[pi];ax.set_ylabel(label);ax.grid(axis='y',color='#E3E3E3',linewidth=.6)
        for method in order:
            vals=[summary.loc[(n,method),metric] for n in cfg['origins'][1:]]
            line(fig,ax,name,f'p{pi}_{method}',[0,1,2],vals,f'phase_summary {metric}',method=method,
                color=COLORS[method],ls=STYLES[method],marker=MARKERS[method],lw=1.15,ms=4,zorder=3)
            if method in cfg['arms']:
                for seed in cfg['seeds']:
                    vals=[seedsummary.loc[(n,method,seed),metric] for n in cfg['origins'][1:]]
                    line(fig,ax,name,f'p{pi}_{method}_s{seed}',np.arange(3)+(seed-1)*.035,vals,f'seed_summary {metric}',
                        method=method,seed=seed,color=COLORS[method],ls='None',marker=MARKERS[method],ms=2.6,alpha=.4,zorder=2)
        ax.set_ylim(bottom=0)
    panels=[(1,[('a_at_gru','GRU：起点表达减原残差','#137D8D','o'),
                 ('a_at_tf','TF：起点表达减原残差','#AD4F61','X')]),
            (2,[('tf_at_raw','原残差：TF减GRU','#BD8875','D'),
                 ('tf_at_anchor','起点表达：TF减GRU','#AD4F61','X')])]
    for pi,items in panels:
        ax=axes[pi];ax.set_ylabel('四点平均 RMSE差 / mm')
        ax.axhline(0,color='#999999',lw=.7,ls=':')
        handles=[]
        for metric,label,color,marker in items:
            vals=[factorial.loc[(n,'rmse'),metric] for n in cfg['origins'][1:]]
            handles.append(line(fig,ax,name,metric,[0,1,2],vals,f'factorial_summary rmse {metric}',
                method=metric,color=color,marker=marker,ms=4,lw=1.1,label=label))
            for seed in cfg['seeds']:
                vals=[factorialseed.loc[(n,seed,'rmse'),metric] for n in cfg['origins'][1:]]
                line(fig,ax,name,f'{metric}_s{seed}',np.arange(3)+(seed-1)*.035,vals,f'factorial_seeds rmse {metric}',
                    method=metric,seed=seed,color=color,marker=marker,ms=2.6,alpha=.4,ls='None')
        lo,hi=ax.get_ylim();ax.set_ylim(lo,hi+(hi-lo)*.34)
        ax.legend(handles=handles,loc='upper center',fontsize=7.3)
    fig.text(.5,.09,'大符号/连线：三种子平均预测后评分；浅色小符号：各个种子或同种子配对差。差值小于0表示前者更低。',ha='center',fontsize=7.4)
    fig.text(.5,.055,'同窗给定未来驱动、无位移反馈；历史已暴露且部分重叠。四组均未通过B+均值与概率联合门。',ha='center',fontsize=7.4)
    finish(fig,name)

    limits=[]
    for p in range(4):
        values=[y[:,p]-y0[p],bplus[:,p]-y0[p]]
        for arm in cfg['arms']:
            values.extend([seeds[arm][:,:,p].ravel()-y0[p],
                           means[arm][:,p]-y0[p]-norm.ppf(.975)*sigmas[arm][p],
                           means[arm][:,p]-y0[p]+norm.ppf(.975)*sigmas[arm][p]])
        values=np.concatenate(values);pad=.045*(values.max()-values.min())
        limits.append([float(values.min()-pad),float(values.max()+pad)])
    mentor=[[-27,565],[-37,760],[-17,365],[-18,399]]
    ticks=[np.arange(0,501,100),np.arange(0,701,100),np.arange(0,351,50),np.arange(0,351,50)]
    aliases=['O3','O2','O1-up','O1-down']
    for arm in cfg['arms']:
        for view in ['mentor','full']:
            name=arm.lower()+'_'+view
            fig,axes=canvas(f'{NAMES[arm]}：四点8:2独立条件预测',
                '前1168日可见位移；后293日一次发出 | 给定未来降雨/库水位 | 预测期不反馈位移')
            fig.legend(handles=[Line2D([],[],color='#222222',lw=1.2,label='实测'),
                Line2D([],[],color=COLORS[c.B],ls='--',lw=1,label='改进 B+'),
                Patch(facecolor=COLORS[arm],alpha=.10,label='95%预测区间'),
                Patch(facecolor=COLORS[arm],alpha=.18,label='80%预测区间'),
                Line2D([],[],color=COLORS[arm],lw=1.3,label=NAMES[arm]+' 集成'),
                Line2D([],[],color=COLORS[arm],alpha=.35,ls=':',lw=.8,label='全部3种子')],
                loc='upper center',bbox_to_anchor=(.5,.892),ncol=3,fontsize=8,columnspacing=2.0)
            clipped=[]
            for p,(point,ax) in enumerate(zip(cfg['points'],axes)):
                ax.set_title(f'{chr(97+p)}  {point} | {aliases[p]}',loc='left',fontweight='bold',pad=8)
                ax.axvspan(datex[0],datex[n-1]+.5,color='#F0F6FA',zorder=0)
                ax.axvspan(datex[n-1]+.5,datex[-1],color='#FFF3E9',zorder=0)
                ax.axvline(datex[n-1]+.5,color='#999999',ls=':',lw=.8)
                for level in [95,80]:
                    q=norm.ppf((1+level/100)/2)*sigmas[arm][p]
                    band(fig,ax,name,f'{point}_band{level}',datex[n:],means[arm][:,p]-y0[p]-q,
                         means[arm][:,p]-y0[p]+q,arm,point,level)
                line(fig,ax,name,f'{point}_observed',datex,y[:,p]-y0[p],'observed minus point y0',
                     method='observed',point=point,color='#222222',lw=1.2,zorder=4)
                line(fig,ax,name,f'{point}_bplus',datex,bplus[:,p]-y0[p],'teacher1168 continuous minus point y0',
                     method=c.B,point=point,color=COLORS[c.B],ls='--',lw=.9,zorder=3)
                for seed in cfg['seeds']:
                    line(fig,ax,name,f'{point}_{arm}_s{seed}',datex[n:],seeds[arm][seed,:,p]-y0[p],
                         'saved seed prediction minus point y0',method=arm,seed=seed,point=point,
                         color=COLORS[arm],ls=':',alpha=.4,lw=.7,zorder=2)
                line(fig,ax,name,f'{point}_{arm}',datex[n:],means[arm][:,p]-y0[p],
                     'saved ensemble prediction minus point y0',method=arm,point=point,color=COLORS[arm],lw=1.4,zorder=5)
                bounds=mentor[p] if view=='mentor' else limits[p]
                ax.set_ylim(bounds);ax.set_xlim(datex[0]-12,datex[-1]+12)
                if view=='mentor':ax.set_yticks(ticks[p])
                ax.set_ylabel('累计位移 / mm');ax.xaxis.set_major_locator(mdates.YearLocator())
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'));ax.set_axisbelow('line')
                ax.grid(True,which='major',axis='both',color='#D9DDE1',lw=.55,alpha=.85)
                rmse=points.loc[(1168,arm,point),'rmse'];brmse=points.loc[(1168,c.B,point),'rmse']
                ax.set_title(f'RMSE {rmse:.2f} | B+ {brmse:.2f} mm',loc='right',fontsize=7.2,pad=8)
                mu=means[arm][:,p]-y0[p];low,high=bounds
                outside=lambda v:int(np.count_nonzero((v<low)|(v>high)))
                counts=dict(observed_days=outside(y[:,p]-y0[p]),bplus_days=outside(bplus[:,p]-y0[p]),
                            mean_days=outside(mu),seed_point_days=outside(seeds[arm][:,:,p]-y0[p]))
                for level in [80,95]:
                    q=norm.ppf((1+level/100)/2)*sigmas[arm][p]
                    counts[f'band{level}_days']=int(np.count_nonzero((mu-q<low)|(mu+q>high)))
                clipped.append(counts['band95_days'])
                notes.append(dict(figure=name,point=point,arm=arm,view=view,rmse=float(rmse),
                                  bplus_rmse=float(brmse),limits=bounds,clipped=counts))
            fig.text(.5,.092,'蓝底：历史观测与B+拟合；橙底：独立预测。未补画神经训练段拟合；全部3种子和完整293日保留。',ha='center',fontsize=7.4)
            foot=('导师固定纵轴；95%区间越界日数（ATU1/ATU5/MJ3/MJ1）：'+ '/'.join(map(str,clipped))+'；完整范围版同时保留。'
                  if view=='mentor' else '同一点四组共用完整纵轴，包含全部均值、种子及95%区间；区间整窗固定，结果为探索性。')
            fig.text(.5,.055,foot,ha='center',fontsize=7.4)
            finish(fig,name)
    o.write_json(out/'plot_records.json',records);o.write_json(out/'annotations.json',notes)
    o.write_json(out/'exports.json',dict(completed_utc=o.utc(),figures=exports,source_data=str(root/'analysis'),
        figure_contract='docs/ootang_backbone_anchor_figure_contract.v1.0.md',pdf_delivery=False,
        dimensions_mm=[240,170],seed_uncertainty='all individual seeds, no confidence intervals',
        sigma='own previous-issued distance91..180 errors, RMS per point, fixed Gaussian',
        transforms='subtract point initial displacement only',points=cfg['points'],observations_removed=0,
        seed_predictions_removed=0,main_origins=cfg['origins'][1:],curves_origin=1168,common_limits=limits,
        overview_methods=order,mentor_limits=mentor))
    print(out,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='v1')
    main(parser.parse_args().attempt)
