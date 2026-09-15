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
COLORS = {c.B:'#666666', c.BA:'#9A8569', 'DRIFT1':'#BE8C3C', 'RR_COND':'#8588A3',
          'G00_RAW_UNIFORM':'#55768A', 'G10_ANCHOR_UNIFORM':'#167D8D',
          'G01_RAW_BOUNDARY':'#BC6E58', 'G11_ANCHOR_BOUNDARY':'#774F91'}
NAMES = {c.B:'改进 B+', c.BA:'仅起点校正 B+', 'DRIFT1':'DRIFT1', 'RR_COND':'岭回归',
         'G00_RAW_UNIFORM':'G00 原样', 'G10_ANCHOR_UNIFORM':'G10 起点表达',
         'G01_RAW_BOUNDARY':'G01 边界采样', 'G11_ANCHOR_BOUNDARY':'G11 两项合用'}
STYLES = {c.B:'--', c.BA:':', 'DRIFT1':'-.', 'RR_COND':'--',
          'G00_RAW_UNIFORM':'-', 'G10_ANCHOR_UNIFORM':'--',
          'G01_RAW_BOUNDARY':'-.', 'G11_ANCHOR_BOUNDARY':'-'}
MARKERS = dict(zip(NAMES,['x','+','v','D','s','o','^','>']))


def main(attempt):
    cfg=c.spec(); c.guard(); root=c.ROOT/cfg['out']
    assert o.read_json(root/'independent_audit_v2/receipt.json')['status']=='passed'
    o.verify_lock(root/'analysis_lock.json')
    out=c.ROOT/cfg['figures']/attempt; out.mkdir(parents=True,exist_ok=False)
    qa=Path(tempfile.mkdtemp(prefix='ootang-gru-ablation-figure-qa-'))
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
    fig,axes=canvas('2×2消融：改善原GRU，最终窗仍未超过B+',
        '同一固定图GRU | 各组3种子、固定200次更新 | 完整293日条件预测、无位移反馈')
    order=cfg['controls']+cfg['arms']
    fig.legend(handles=[Line2D([],[],color=COLORS[m],ls=STYLES[m],marker=MARKERS[m],ms=3.7,lw=1,label=NAMES[m]) for m in order],
        loc='upper center',bbox_to_anchor=(.5,.908),ncol=4,fontsize=8,columnspacing=2.0)
    titles=['a  完整预测窗：均值误差','b  第1天：起点跳偏诊断','c  完整预测窗：概率评分','d  RMSE：主效应与交互']
    for ax,title in zip(axes,titles):
        ax.set_title(title,loc='left',fontweight='bold',pad=8)
        ax.set_xticks([0,1,2],['792\n历史窗1','972\n历史窗2','1168\n最终探索'])
        ax.set_xlim(-.16,2.16);ax.grid(axis='y',color='#E3E3E3',linewidth=.6);ax.set_axisbelow(True)
    for pi,(metric,label) in enumerate([('rmse','四点平均 RMSE / mm'),('day1','第1天平均绝对误差 / mm'),('crps','四点平均 CRPS / mm')]):
        ax=axes[pi];ax.set_ylabel(label)
        for method in order:
            vals=[endpoint.loc[(origin,method,'ensemble')] if metric=='day1' else summary.loc[(origin,method),metric] for origin in cfg['origins'][1:]]
            recipe='endpoint ensemble absolute_error averaged over four points at h1' if metric=='day1' else f'phase_summary {metric}'
            line(fig,ax,name,f'p{pi}_{method}',[0,1,2],vals,recipe,method=method,
                color=COLORS[method],ls=STYLES[method],marker=MARKERS[method],lw=1.15,ms=4,zorder=3)
            if method in cfg['arms']:
                for seed in cfg['seeds']:
                    vals=[endpoint.loc[(origin,method,str(seed))] if metric=='day1' else seedsummary.loc[(origin,method,seed),metric] for origin in cfg['origins'][1:]]
                    line(fig,ax,name,f'p{pi}_{method}_s{seed}',np.arange(3)+(seed-1)*.035,vals,
                         f'endpoint seed absolute_error at h1' if metric=='day1' else f'seed_summary {metric}',
                         method=method,seed=seed,color=COLORS[method],ls='None',marker=MARKERS[method],ms=2.6,alpha=.4,zorder=2)
        ax.set_ylim(bottom=0)
    ax=axes[3];ax.grid(False);ax.set_ylabel('RMSE差 / mm（负值为下降）');ax.axhline(0,color='#999999',lw=.7,ls=':')
    effect_styles=[('a_main','A 起点表达','#167D8D','o'),('b_main','B 边界采样','#BC6E58','^'),('interaction','A×B 交互','#774F91','s')]
    handles=[]
    for metric,label,color,marker in effect_styles:
        vals=[factorial.loc[(origin,'rmse'),metric] for origin in cfg['origins'][1:]]
        handles.append(line(fig,ax,name,metric,[0,1,2],vals,f'factorial_summary rmse {metric}',method=metric,
            color=color,marker=marker,ms=4,lw=1.1,label=label))
        for seed in cfg['seeds']:
            vals=[factorialseed.loc[(origin,seed,'rmse'),metric] for origin in cfg['origins'][1:]]
            line(fig,ax,name,f'{metric}_s{seed}',np.arange(3)+(seed-1)*.035,vals,
                f'factorial_seeds rmse {metric}',method=metric,seed=seed,color=color,marker=marker,ms=2.6,alpha=.4,ls='None')
    ax.legend(handles=handles,loc='lower left',fontsize=7.4)
    fig.text(.5,.09,'大符号/连线：三种子等权预测后评分；浅色小符号：全部种子及配对效应。第1天不替代全窗评价。',ha='center',fontsize=7.6)
    fig.text(.5,.055,'A与B均为整组设计改动；窗口已暴露且部分重叠。效应仅作探索性描述，无独立重复置信区间。',ha='center',fontsize=7.6)
    finish(fig,name)

    # Common per-point axes include every displayed seed and the full 95% band of all four arms.
    limits=[]
    for p in range(4):
        values=[y[:,p]-y0[p],bplus[:,p]-y0[p]]
        for arm in cfg['arms']:
            values.extend([seeds[arm][:,:,p].ravel()-y0[p],means[arm][:,p]-y0[p]-norm.ppf(.975)*sigmas[arm][p],
                           means[arm][:,p]-y0[p]+norm.ppf(.975)*sigmas[arm][p]])
        joined=np.concatenate(values); low,high=joined.min(),joined.max(); padding=.045*(high-low)
        limits.append([float(low-padding),float(high+padding)])
    for arm in cfg['arms']:
        name=arm.lower()+'_four_points'
        fig,axes=canvas(f'{NAMES[arm]}：四点8:2独立条件预测',
            '前1168日可见位移；后293日一次发出 | 给定未来降雨/库水位 | 预测期不反馈位移')
        fig.legend(handles=[Line2D([],[],color='#222222',lw=1.2,label='实测'),
            Line2D([],[],color=COLORS[c.B],ls='--',lw=1,label='改进 B+'),
            Line2D([],[],color=COLORS[arm],lw=1.3,label=NAMES[arm]+' 集成'),
            Line2D([],[],color=COLORS[arm],alpha=.35,ls=':',lw=.8,label='全部3种子'),
            Patch(facecolor=COLORS[arm],alpha=.10,label='95%预测区间'),
            Patch(facecolor=COLORS[arm],alpha=.25,label='80%预测区间')],
            loc='upper center',bbox_to_anchor=(.5,.892),ncol=3,fontsize=8,columnspacing=2.0)
        for p,(point,ax) in enumerate(zip(cfg['points'],axes)):
            ax.set_title(f'{chr(97+p)}  {point}',loc='left',fontweight='bold',pad=8)
            ax.axvspan(datex[0],datex[n-1]+.5,color='#F0F6FA',zorder=0)
            ax.axvspan(datex[n-1]+.5,datex[-1],color='#FCF1E8',zorder=0)
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
            ax.set_ylim(limits[p]);ax.set_xlim(datex[0]-12,datex[-1]+12)
            ax.set_ylabel('累计位移 / mm');ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'));ax.grid(color='#DDDDDD',lw=.55,alpha=.8)
            rmse=points.loc[(1168,arm,point),'rmse'];brmse=points.loc[(1168,c.B,point),'rmse']
            ax.set_title(f'全窗RMSE {rmse:.2f} | B+ {brmse:.2f} mm',loc='right',fontsize=7.6,pad=8)
            notes.append(dict(figure=name,point=point,rmse=float(rmse),bplus_rmse=float(brmse),limits=limits[p]))
        fig.text(.5,.092,'蓝底：历史观测及B+拟合；橙底：独立预测。未补画神经训练段拟合；同一测点四组共用纵轴。',ha='center',fontsize=7.6)
        fig.text(.5,.055,'区间来自上一条已发预测的90条成熟误差，整窗固定；展示完整区间、不截尾。结果均为探索性。',ha='center',fontsize=7.6)
        finish(fig,name)
    o.write_json(out/'plot_records.json',records);o.write_json(out/'annotations.json',notes)
    o.write_json(out/'exports.json',dict(completed_utc=o.utc(),figures=exports,source_data=str(root/'analysis'),
        figure_contract='docs/ootang_gru_ablation_figure_contract.v1.0.md',pdf_delivery=False,
        dimensions_mm=[240,170],seed_uncertainty='all individual seeds, no confidence intervals',
        sigma='own previous-issued distance91..180 errors, RMS per point, fixed Gaussian',
        transforms='subtract point initial displacement only',points=cfg['points'],observations_removed=0,
        seed_predictions_removed=0,main_origins=cfg['origins'][1:],curves_origin=1168,common_limits=limits))
    print(out,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='v1')
    main(parser.parse_args().attempt)
