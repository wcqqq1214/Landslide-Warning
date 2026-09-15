"""Full-window policy differences and mentor-style four-point forecasts."""
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

o=c.o
COLORS={c.B:'#555555',c.BA:'#999999','DRIFT1':'#758452','RR_COND':'#9C83AD',
        'G_CACHED':'#137D8D','G_REFRESH':'#CE7540'}
NAMES={c.B:'改进 B+',c.BA:'起点校正 B+','DRIFT1':'DRIFT1','RR_COND':'普通岭回归',
       'G_CACHED':'旧教师 GRU','G_REFRESH':'更新教师 GRU'}


def main(attempt):
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];o.verify_lock(root/'audit_lock.json')
    out=c.ROOT/cfg['figures']/attempt;out.mkdir(parents=True,exist_ok=False)
    qa=Path(tempfile.mkdtemp(prefix='ootang-teacher-refresh-qa-'))
    script=Path.home()/'.codex/skills/nature-figure/scripts/audit_panel_alignment.py'
    spec=importlib.util.spec_from_file_location('teacher_refresh_alignment',script)
    alignment=importlib.util.module_from_spec(spec);spec.loader.exec_module(alignment)
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial Unicode MS','DejaVu Sans'],
        'font.size':8,'axes.labelsize':8.2,'axes.titlesize':9.4,'axes.spines.top':False,
        'axes.spines.right':False,'axes.linewidth':.7,'svg.fonttype':'none','pdf.fonttype':42,
        'axes.unicode_minus':False,'legend.frameon':False,'path.simplify':False})
    read=lambda p:pd.read_csv(p,float_precision='round_trip')
    summary=read(root/'analysis/phase_summary.csv').set_index(['origin','method'])
    ss=read(root/'analysis/seed_summary.csv').set_index(['origin','method','seed'])
    points=read(root/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
    y=o.read_labels(c.ROOT/cfg['data'],1461);_,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    dx=mdates.date2num(pd.to_datetime(dates));y0=y[0]
    means,seeds,sigmas=[o.load_npz(root/f'origin_1168/{key}.npz') for key in ('means','seeds','sigmas')]
    bplus=c.bank(cfg)['cached'][1168]['mean']
    np.savez_compressed(out/'source_arrays.npz',dates=dates,observed=y,bplus_full=bplus,
        **means,**{m+'__seeds':v for m,v in seeds.items()},**{m+'__sigma':v for m,v in sigmas.items()})
    records=[];exports=[];notes=[]

    def canvas(title,subtitle):
        fig,axes=plt.subplots(2,2,figsize=(240/25.4,170/25.4))
        fig.subplots_adjust(left=.09,right=.977,bottom=.18,top=.775,wspace=.25,hspace=.55)
        fig.suptitle(title,y=.973,fontsize=12,fontweight='bold')
        fig.text(.5,.934,subtitle,ha='center',fontsize=8,color='#444444')
        return fig,axes.ravel()

    def line(fig,ax,name,gid,x,values,recipe,**style):
        ax.plot(x,values,gid=gid,**style)
        records.append(dict(figure=name,gid=gid,kind='line',axes=fig.axes.index(ax),
            x=np.asarray(x,float).tolist(),y=np.asarray(values,float).tolist(),recipe=recipe,
            marker_only=style.get('ls')=='None'))

    def band(fig,ax,name,gid,method,p,level):
        mu=means[method][:,p]-y0[p];half=norm.ppf((1+level/100)/2)*sigmas[method][p]
        polygon=ax.fill_between(dx[1168:],mu-half,mu+half,color=COLORS[method],
            alpha=.10 if level==95 else .20,lw=0,gid=gid,zorder=1)
        records.append(dict(figure=name,gid=gid,kind='band',axes=fig.axes.index(ax),
            x=dx[1168:].tolist(),lo=(mu-half).tolist(),hi=(mu+half).tolist(),
            vertices=polygon.get_paths()[0].vertices.tolist(),recipe=dict(type='band',method=method,point=p,level=level)))

    def finish(fig,name):
        fig.canvas.draw();geometry=text_geometry(fig)
        o.write_json(out/f'{name}.text_geometry.json',geometry);assert geometry['passed'],geometry
        alignment.require_matplotlib_panel_alignment(fig,json_out=out/f'{name}.alignment.json',
            tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
        for rec in [r for r in records if r['figure']==name]:
            vertices=np.asarray(rec['vertices']) if rec['kind']=='band' else np.column_stack([rec['x'],rec['y']])
            ax=fig.axes[rec['axes']];xlim,ylim=ax.get_xlim(),ax.get_ylim()
            assert np.all((vertices[:,0]>=xlim[0])&(vertices[:,0]<=xlim[1])&(vertices[:,1]>=ylim[0])&(vertices[:,1]<=ylim[1])),rec['gid']
            rec['xlim']=list(xlim);rec['ylim']=list(ylim)
            xy=ax.transData.transform(vertices)
            rec['svg_xy']=np.column_stack([xy[:,0]*72/fig.dpi,(fig.bbox.height-xy[:,1])*72/fig.dpi]).tolist()
        fig.savefig(out/f'{name}.svg');fig.savefig(out/f'{name}.png',dpi=300)
        fig.savefig(qa/f'{name}.pdf');plt.close(fig)
        exports.append(dict(name=name,png=f'{name}.png',svg=f'{name}.svg',qa_pdf=str(qa/f'{name}.pdf'),panels=4))

    name='teacher_policy_comparison'
    fig,axes=canvas('历史教师更新配对：三个完整预测窗的结果',
        '同一 GRU、同一起点表达、相同样本与200次更新 | 每组3个种子 | 外层 B+ 保持原样')
    methods=cfg['controls']+cfg['arms']
    fig.legend(handles=[Line2D([],[],color=COLORS[m],ls='-' if m in cfg['arms'] else '--',
        lw=1.5 if m in cfg['arms'] else 1,label=NAMES[m]) for m in methods],
        loc='upper center',bbox_to_anchor=(.5,.896),ncol=3,fontsize=7.8)
    titles=['a  完整293日：六方法均值比较','b  更新减旧教师：同种子配对',
            'c  最终预测：四点均值误差','d  最终预测：逐点90%区间覆盖']
    for ax,title in zip(axes,titles):
        ax.set_title(title,loc='left',fontweight='bold',pad=8)
        ax.grid(axis='y',color='#dddddd',lw=.5,zorder=0)
    for ax in axes[:2]:
        ax.set_xticks(range(3),['792\n历史窗1','972\n历史窗2','1168\n最终探索']);ax.set_xlim(-.15,2.15)
    axes[0].set_ylabel('四点平均 RMSE / mm')
    for m in methods:
        yy=[summary.loc[(n,m),'rmse'] for n in cfg['origins'][1:]]
        line(fig,axes[0],name,'overall_'+m,range(3),yy,dict(type='summary',method=m,metric='rmse'),
            color=COLORS[m],ls='-' if m in cfg['arms'] else '--',lw=1.4 if m in cfg['arms'] else .85,marker='o',ms=3)
    axes[0].set_ylim(bottom=0)
    axes[1].set_ylabel('四点平均 RMSE差 / mm');axes[1].axhline(0,color='#777777',ls=':',lw=.7)
    for s in [0,1,2,None]:
        vals=[]
        for n in cfg['origins'][1:]:
            a,b=(summary.loc[(n,m),'rmse'] for m in cfg['arms']) if s is None else (ss.loc[(n,m,s),'rmse'] for m in cfg['arms'])
            vals.append(b-a)
        x=np.arange(3)+(0 if s is None else (s-1)*.045)
        line(fig,axes[1],name,'paired_'+str(s),x,vals,dict(type='paired',seed=s),
            color=COLORS['G_REFRESH'],lw=1.4,ls='-' if s is None else 'None',
            marker='o',ms=3.5 if s is None else 3,alpha=1 if s is None else .4)
    for ax in axes[2:]:ax.set_xticks(range(4),cfg['points']);ax.set_xlim(-.2,3.2)
    axes[2].set_ylabel('RMSE / mm');axes[3].set_ylabel('覆盖率 / %')
    for m in [c.B,c.BA,*cfg['arms']]:
        yy=[points.loc[(1168,m,p),'rmse'] for p in cfg['points']]
        line(fig,axes[2],name,'point_rmse_'+m,range(4),yy,dict(type='point',method=m,metric='rmse'),
            color=COLORS[m],ls='-' if m in cfg['arms'] else '--',lw=1.2,marker='o',ms=3)
    axes[2].set_ylim(bottom=0)
    for m in cfg['arms']:
        yy=[100*points.loc[(1168,m,p),'coverage90'] for p in cfg['points']]
        line(fig,axes[3],name,'point_coverage_'+m,range(4),yy,dict(type='point',method=m,metric='coverage90',multiplier=100),
            color=COLORS[m],lw=1.3,marker='o',ms=3)
    axes[3].axhline(80,color='#777777',ls=':',lw=.7);axes[3].set_ylim(0,105)
    fig.text(.5,.09,'b负值表示更新教师组误差更低；实线为集成预测差，浅色点为同种子差。d虚线为逐点80%工作门槛。',ha='center',fontsize=7.2)
    fig.text(.5,.055,'全部293日、四点与三个种子保留；窗口已暴露且重叠。较高覆盖须结合区间宽度和概率评分判断。',ha='center',fontsize=7.2)
    finish(fig,name)

    limits=[]
    for p in range(4):
        values=[y[:,p]-y0[p],bplus[:,p]-y0[p]]
        for m in cfg['arms']:
            values.append(seeds[m][:,:,p].ravel()-y0[p])
            mu=means[m][:,p]-y0[p];half=norm.ppf(.975)*sigmas[m][p]
            values.extend([mu-half,mu+half])
        vv=np.concatenate(values);pad=.045*(vv.max()-vv.min())
        limits.append([float(vv.min()-pad),float(vv.max()+pad)])
    for m,name in [('G_CACHED','cached_final'),('G_REFRESH','refresh_final')]:
        other=next(a for a in cfg['arms'] if a!=m)
        fig,axes=canvas(NAMES[m]+'：四点8:2条件预测',
            '前1168日观测历史，后293日一次发出 | 未来逐日降雨/水位给定，预测期无位移反馈')
        fig.subplots_adjust(bottom=.215)
        fig.legend(handles=[Line2D([],[],color='#111111',lw=1.2,label='实测'),
            Line2D([],[],color=COLORS[c.B],ls='--',lw=1,label='改进 B+'),
            Line2D([],[],color=COLORS[m],lw=1.5,label=NAMES[m]),
            Line2D([],[],color=COLORS[other],lw=.9,ls='--',label=NAMES[other]),
            Patch(facecolor=COLORS[m],alpha=.1,label='本组95%预测区间'),
            Patch(facecolor=COLORS[m],alpha=.2,label='本组80%预测区间')],
            loc='upper center',bbox_to_anchor=(.5,.897),ncol=3,fontsize=7.8)
        for p,(ax,point) in enumerate(zip(axes,cfg['points'])):
            ax.set_title(f'{chr(97+p)}  {point}',loc='left',fontweight='bold',pad=8)
            ax.axvspan(dx[0],dx[1167],color='#EEF5FA',zorder=0)
            ax.axvspan(dx[1168],dx[-1],color='#FFF4EA',zorder=0)
            ax.axvline(dx[1168],color='#777777',ls=':',lw=.8)
            band(fig,ax,name,f'{name}_{point}_95',m,p,95)
            band(fig,ax,name,f'{name}_{point}_80',m,p,80)
            for method,vals in [('observed',y[:,p]-y0[p]),('bplus_full',bplus[:,p]-y0[p])]:
                line(fig,ax,name,f'{name}_{point}_{method}',dx,vals,dict(type='trajectory',method=method,point=p,seed=None),
                    color='#111111' if method=='observed' else COLORS[c.B],lw=1.1 if method=='observed' else .9,
                    ls='-' if method=='observed' else '--',zorder=3)
            for s in cfg['seeds']:
                line(fig,ax,name,f'{name}_{point}_seed{s}',dx[1168:],seeds[m][s,:,p]-y0[p],
                    dict(type='trajectory',method=m,point=p,seed=s),color=COLORS[m],lw=.55,alpha=.32,zorder=2)
            for method in [other,m]:
                line(fig,ax,name,f'{name}_{point}_{method}',dx[1168:],means[method][:,p]-y0[p],
                    dict(type='trajectory',method=method,point=p,seed=None),color=COLORS[method],
                    lw=1.6 if method==m else .85,ls='-' if method==m else '--',zorder=4 if method==m else 2)
            ax.set_xlim(dx[0]-35,dx[-1]+35);ax.set_ylim(*limits[p]);ax.set_ylabel('累计位移 / mm')
            ax.xaxis.set_major_locator(mdates.YearLocator());ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
            ax.grid(color='#dddddd',lw=.5,zorder=0)
            values=[float(points.loc[(1168,method,point),'rmse']) for method in (m,c.B)]
            cov=100*float(points.loc[(1168,m,point),'coverage90']);width=float(points.loc[(1168,m,point),'width90'])
            message=f'RMSE：本组 {values[0]:.2f} | B+ {values[1]:.2f} mm\n90%区间：覆盖 {cov:.1f}% | 宽度 {width:.1f} mm'
            ax.text(.5,-.32,message,transform=ax.transAxes,ha='center',va='top',fontsize=6.9,linespacing=1.45)
            notes.append(dict(figure=name,method=m,point=point,rmse=values[0],rmse_bplus=values[1],coverage90=cov,width90=width))
        fig.text(.5,.065,'蓝底：观测历史与B+拟合；橙底：独立条件预测。细线为本组三种子，色带为高斯边际预测区间。',ha='center',fontsize=7.2)
        fig.text(.5,.029,'两图纵轴相同，全部观测、种子与95%区间均完整显示；不补画不存在的神经训练段重建。',ha='center',fontsize=7.2)
        finish(fig,name)
    o.write_json(out/'plot_records.json',records);o.write_json(out/'annotations.json',notes)
    o.write_json(out/'exports.json',dict(figures=exports,common_limits=limits,qa_only_directory=str(qa),
        source_code_sha256=o.sha(c.ROOT/'code/teacher_refresh/figures.py'),backend='python',width_mm=240,height_mm=170))
    o.lock(out,'artifact_lock.json',list(out.glob('*')),status='rendered_pending_visual_and_data_audit')
    print({'figures':3,'panels':12,'records':len(records),'folder':str(out)},flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='v1');args=parser.parse_args();main(args.attempt)
