"""Four source-linked diagnostic and full-range forecast figures."""
import importlib.util
import argparse
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

COLORS={'G10_ANCHOR_UNIFORM':'#137D8D','T10_ANCHOR_UNIFORM':'#AD4F61','BPLUS_CONTINUOUS':'#666666'}
NAMES={'G10_ANCHOR_UNIFORM':'GRU','T10_ANCHOR_UNIFORM':'Transformer'}


def main(attempt):
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];ext=root/'extension'
    c.o.verify_lock(ext/'audit_lock.json')
    out=c.ROOT/cfg['figures']/attempt;out.mkdir(parents=True,exist_ok=False)
    qa=Path(tempfile.mkdtemp(prefix='ootang-training-sufficiency-figure-qa-'))
    script=Path.home()/'.codex/skills/nature-figure/scripts/audit_panel_alignment.py'
    module_spec=importlib.util.spec_from_file_location('panel_alignment',script)
    alignment=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(alignment)
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial Unicode MS','DejaVu Sans'],
        'font.size':8,'axes.labelsize':8.2,'axes.titlesize':9.4,'axes.spines.top':False,'axes.spines.right':False,
        'axes.linewidth':.7,'svg.fonttype':'none','pdf.fonttype':42,'axes.unicode_minus':False,
        'legend.frameon':False,'path.simplify':False})
    read=lambda p:pd.read_csv(p,float_precision='round_trip')
    tr=read(root/'diagnostic/training_summary.csv')
    tr400=read(ext/'audit/training400_summary.csv')
    history=pd.read_csv(root/'diagnostic/historical_summary.csv',dtype={'seed':str},float_precision='round_trip').set_index(['origin','arm','seed','step'])
    summary=read(ext/'analysis/phase_summary.csv').set_index(['origin','method'])
    seedsummary=read(ext/'analysis/seed_summary.csv').set_index(['origin','method','seed'])
    pointsummary=read(ext/'analysis/metrics_by_point.csv').set_index(['origin','method','point'])
    y=c.o.read_labels(c.ROOT/cfg['data'],1461);_,dates=c.o.read_forcing(c.ROOT/cfg['data'],1461)
    dx=mdates.date2num(pd.to_datetime(dates));y0=y[0]
    means,seeds,sigmas=[c.o.load_npz(ext/'origin_1168'/f'{s}.npz') for s in ['means','seeds','sigmas']]
    bplus=c.o.bank(cfg)[1168]['mean']
    np.savez_compressed(out/'source_arrays.npz',dates=dates,observed=y,bplus_full=bplus,
                        **means,**{m+'__seeds':v for m,v in seeds.items()},**{m+'__sigma':v for m,v in sigmas.items()})
    records=[];exports=[];annotations=[]

    def canvas(title,subtitle):
        fig,axes=plt.subplots(2,2,figsize=(240/25.4,170/25.4))
        fig.subplots_adjust(left=.09,right=.977,bottom=.18,top=.775,wspace=.25,hspace=.55)
        fig.suptitle(title,y=.973,fontsize=12,fontweight='bold')
        fig.text(.5,.934,subtitle,ha='center',fontsize=8.1,color='#444444')
        return fig,axes.ravel()

    def line(fig,ax,name,gid,x,values,recipe,**style):
        artist=ax.plot(x,values,gid=gid,**style)[0]
        records.append(dict(figure=name,gid=gid,kind='line',axes=fig.axes.index(ax),
                            x=np.asarray(x,float).tolist(),y=np.asarray(values,float).tolist(),
                            recipe=recipe,marker_only=style.get('ls')=='None'))
        return artist

    def band(fig,ax,name,gid,arm,p,level):
        method=arm+'_E400';center=means[method][:,p]-y0[p]
        half=norm.ppf((1+level/100)/2)*sigmas[method][p]
        polygon=ax.fill_between(dx[1168:],center-half,center+half,color=COLORS[arm],
                               alpha=.10 if level==95 else .19,lw=0,gid=gid,zorder=1)
        records.append(dict(figure=name,gid=gid,kind='band',axes=fig.axes.index(ax),
            x=dx[1168:].tolist(),lo=(center-half).tolist(),hi=(center+half).tolist(),
            vertices=polygon.get_paths()[0].vertices.tolist(),recipe=dict(type='band',method=method,point=p,level=level)))

    def finish(fig,name):
        fig.canvas.draw();geometry=text_geometry(fig)
        c.o.write_json(out/f'{name}.text_geometry.json',geometry);assert geometry['passed'],geometry
        alignment.require_matplotlib_panel_alignment(fig,json_out=out/f'{name}.alignment.json',tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
        for rec in [r for r in records if r['figure']==name]:
            vertices=np.array(rec['vertices']) if rec['kind']=='band' else np.column_stack([rec['x'],rec['y']])
            ax=fig.axes[rec['axes']]
            xlim,ylim=ax.get_xlim(),ax.get_ylim()
            assert np.all((vertices[:,0]>=xlim[0])&(vertices[:,0]<=xlim[1])&(vertices[:,1]>=ylim[0])&(vertices[:,1]<=ylim[1])),rec['gid']
            rec['xlim']=list(xlim);rec['ylim']=list(ylim)
            xy=ax.transData.transform(vertices)
            rec['svg_xy']=np.column_stack([xy[:,0]*72/fig.dpi,(fig.bbox.height-xy[:,1])*72/fig.dpi]).tolist()
        fig.savefig(out/f'{name}.svg');fig.savefig(out/f'{name}.png',dpi=300)
        fig.savefig(qa/f'{name}.pdf');plt.close(fig)
        exports.append(dict(name=name,png=f'{name}.png',svg=f'{name}.svg',qa_pdf=str(qa/f'{name}.pdf'),panels=4))

    steps=[0,50,100,200]
    name='checkpoint_diagnostic';fig,axes=canvas('原200次检查点仍有训练与历史预测改善',
        '固定面板、同三种子 | 触发仅用100→200次 | 两个历史窗在1168日起点前已完整兑现')
    fig.legend(handles=[Line2D([],[],color=COLORS[a],lw=1.5,label=NAMES[a]) for a in cfg['arms']],loc='upper center',bbox_to_anchor=(.5,.899),ncol=2)
    for ax,title in zip(axes,['a  固定面板：位移拟合误差','b  正则目标：距自由输出下界','c  起点612：完整293日历史预测','d  起点792：完整293日历史预测']):
        ax.set_title(title,loc='left',fontweight='bold',pad=8);ax.set_xlabel('训练更新次数');ax.set_xticks(steps)
    for pi,metric in enumerate(['mse','excess_objective']):
        axes[pi].set_ylabel('相对各训练前缀初值的比值')
        for arm in cfg['arms']:
            values=[]
            for seed in cfg['seeds']:
                a=tr[(tr.arm==arm)&(tr.seed==seed)].set_index(['origin','step'])
                val=[np.mean([a.loc[(n,s),metric]/a.loc[(n,0),metric] for n in cfg['origins']]) for s in steps]
                values.append(val)
                line(fig,axes[pi],name,f'train_{pi}_{arm}_s{seed}',steps,val,dict(type='train_ratio',arm=arm,seed=seed,metric=metric),color=COLORS[arm],alpha=.28,lw=.65)
            line(fig,axes[pi],name,f'train_{pi}_{arm}_mean',steps,np.mean(values,0),dict(type='train_ratio',arm=arm,seed=None,metric=metric),color=COLORS[arm],lw=1.6,marker='o',ms=3.5)
        axes[pi].set_ylim(bottom=0)
    for pi,n in [(2,612),(3,792)]:
        axes[pi].set_ylabel('四点平均 RMSE / mm')
        for arm in cfg['arms']:
            for seed in ['0','1','2','ensemble']:
                val=[history.loc[(n,arm,seed,s),'rmse'] for s in steps]
                main=seed=='ensemble'
                line(fig,axes[pi],name,f'history_{n}_{arm}_{seed}',steps,val,dict(type='history',origin=n,arm=arm,seed=seed),
                     color=COLORS[arm],lw=1.6 if main else .65,alpha=1 if main else .28,marker='o' if main else None,ms=3.5)
        axes[pi].set_ylim(bottom=0)
    fig.text(.5,.09,'训练面板先按各前缀e0归一化，再四前缀等权；粗线为三种子均值，细线为各个种子。',ha='center',fontsize=7.4)
    fig.text(.5,.055,'历史预测粗线为三种子平均预测后评分；两历史窗重叠113日。该诊断支持一次追加，不证明模型有效。',ha='center',fontsize=7.4)
    finish(fig,name)

    name='budget_comparison';fig,axes=canvas('追加至400次：训练拟合改善，长窗预测收益仍不稳定',
        '相同模型、输入、起点表达、λ=1 | 原200步精确重放后追加200步 | 无新增轮数选择')
    methods=['BPLUS_CONTINUOUS']+[a+f'_E{s}' for a in cfg['arms'] for s in [200,400]]
    handles=[]
    for method in methods:
        arm=method.split('_E')[0];step=method.split('_E')[-1] if '_E' in method else None
        handles.append(Line2D([],[],color=COLORS[arm],ls='--' if step=='200' else '-',marker='o' if step=='400' else None,lw=1.2,label='改进 B+' if step is None else f'{NAMES[arm]} {step}次'))
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.5,.899),ncol=5,fontsize=7.6)
    for ax,title in zip(axes,['a  完整293日：均值误差','b  400减200：配对RMSE变化','c  最终预测：逐点90%区间覆盖','d  固定训练面板：MSE变化']):
        ax.set_title(title,loc='left',fontweight='bold',pad=8)
    for ax in axes[:2]:ax.set_xticks([0,1,2],['792\n历史窗1','972\n历史窗2','1168\n最终探索']);ax.set_xlim(-.15,2.15)
    axes[0].set_ylabel('四点平均 RMSE / mm')
    for method in methods:
        arm=method.split('_E')[0];is200=method.endswith('E200')
        values=[summary.loc[(n,method),'rmse'] for n in cfg['origins'][1:]]
        line(fig,axes[0],name,'summary_'+method,[0,1,2],values,dict(type='summary',method=method,metric='rmse'),color=COLORS[arm],ls='--' if is200 else '-',lw=1.2,marker='o',ms=3.5)
    axes[0].set_ylim(bottom=0)
    axes[1].set_ylabel('四点平均 RMSE差 / mm');axes[1].axhline(0,color='#888888',lw=.7,ls=':')
    for arm in cfg['arms']:
        for seed in [0,1,2,None]:
            def value(n,step):
                return summary.loc[(n,arm+f'_E{step}'),'rmse'] if seed is None else seedsummary.loc[(n,arm+f'_E{step}',seed),'rmse']
            yy=[value(n,400)-value(n,200) for n in cfg['origins'][1:]]
            x=np.arange(3) if seed is None else np.arange(3)+(seed-1)*.04
            line(fig,axes[1],name,f'delta_{arm}_{seed}',x,yy,dict(type='budget_difference',arm=arm,seed=seed),color=COLORS[arm],lw=1.3,marker='o',ms=3.6 if seed is None else 2.8,ls='-' if seed is None else 'None',alpha=1 if seed is None else .4)
    axes[2].set_ylabel('覆盖率 / %');axes[2].set_xticks(range(4),cfg['points']);axes[2].set_ylim(0,105)
    axes[2].axhline(80,color='#888888',lw=.7,ls=':')
    for method in methods[1:]:
        arm=method.split('_E')[0];yy=[100*pointsummary.loc[(1168,method,p),'coverage90'] for p in cfg['points']]
        line(fig,axes[2],name,'coverage_'+method,range(4),yy,dict(type='point_coverage',method=method),color=COLORS[arm],ls='--' if method.endswith('E200') else '-',lw=1.2,marker='o',ms=3.5)
    axes[3].set_ylabel('200→400次的MSE变化 / %');axes[3].set_xticks(range(4),['612','792','972','1168']);axes[3].set_xlabel('训练前缀 / 日');axes[3].axhline(0,color='#888888',lw=.7,ls=':')
    for arm in cfg['arms']:
        for seed in [0,1,2,None]:
            before=tr[(tr.arm==arm)&(tr.step==200)];after=tr400[tr400.arm==arm]
            if seed is not None:before=before[before.seed==seed];after=after[after.seed==seed]
            a=before.groupby('origin').mse.mean();b=after.groupby('origin').mse.mean()
            yy=[100*(b.loc[n]/a.loc[n]-1) for n in cfg['origins']]
            x=np.arange(4) if seed is None else np.arange(4)+(seed-1)*.04
            line(fig,axes[3],name,f'train_delta_{arm}_{seed}',x,yy,dict(type='train_budget_change',arm=arm,seed=seed),color=COLORS[arm],lw=1.3,marker='o',ms=3.6 if seed is None else 2.8,ls='-' if seed is None else 'None',alpha=1 if seed is None else .4)
    fig.text(.5,.09,'b/d负值表示改善；浅色小点为同种子变化。c虚线为逐点80%工作门槛，区间来自各版本自己的成熟误差。',ha='center',fontsize=7.4)
    fig.text(.5,.055,'全部293日与三个种子保留；历史已暴露且部分重叠。训练拟合改善不等于跨时段效果改善。',ha='center',fontsize=7.4)
    finish(fig,name)

    limits=[]
    for p in range(4):
        values=[y[:,p]-y0[p],bplus[:,p]-y0[p]]
        for arm in cfg['arms']:
            for step in [200,400]:values.append(seeds[arm+f'_E{step}'][:,:,p].ravel()-y0[p])
            mu=means[arm+'_E400'][:,p]-y0[p];half=norm.ppf(.975)*sigmas[arm+'_E400'][p]
            values.extend([mu-half,mu+half])
        v=np.concatenate(values);pad=.045*(v.max()-v.min());limits.append([float(v.min()-pad),float(v.max()+pad)])
    for arm in cfg['arms']:
        name=NAMES[arm].lower()+'_final400'
        fig,axes=canvas(f'{NAMES[arm]}起点版：400次训练与8:2条件预测',
            '训练段显示实测与B+拟合；预测期不反馈位移 | 未来逐日降雨与库水位给定')
        fig.subplots_adjust(bottom=.215)
        fig.legend(handles=[Line2D([],[],color='#111111',lw=1.3,label='实测'),Line2D([],[],color='#666666',ls='--',lw=1,label='改进 B+'),
            Line2D([],[],color=COLORS[arm],ls='--',lw=1,label='200次均值'),Line2D([],[],color=COLORS[arm],lw=1.5,label='400次均值'),
            Patch(facecolor=COLORS[arm],alpha=.1,label='400次95%区间'),Patch(facecolor=COLORS[arm],alpha=.19,label='400次80%区间')],
            loc='upper center',bbox_to_anchor=(.5,.899),ncol=3,fontsize=7.8)
        for p,(ax,point) in enumerate(zip(axes,cfg['points'])):
            ax.set_title(f'{chr(97+p)}  {point}',loc='left',fontweight='bold',pad=8)
            ax.axvspan(dx[0],dx[1167],color='#EEF5FA',zorder=0)
            ax.axvspan(dx[1168],dx[-1],color='#FCF0E6',zorder=0)
            ax.axvline(dx[1168],color='#888888',ls=':',lw=.8)
            ax.set_ylim(limits[p]);ax.set_ylabel('累计位移 / mm')
            ax.xaxis.set_major_locator(mdates.YearLocator());ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
            band(fig,ax,name,f'{point}_band95',arm,p,95);band(fig,ax,name,f'{point}_band80',arm,p,80)
            for seed in cfg['seeds']:
                line(fig,ax,name,f'{point}_seed{seed}',dx[1168:],seeds[arm+'_E400'][seed,:,p]-y0[p],dict(type='trajectory',method=arm+'_E400',point=p,seed=seed),color=COLORS[arm],alpha=.25,lw=.6,zorder=2)
            for method,style in [('observed',dict(color='#111111',lw=1.2,zorder=5)),('bplus_full',dict(color='#666666',lw=1,ls='--',zorder=3)),
                                  (arm+'_E200',dict(color=COLORS[arm],lw=1,ls='--',zorder=4)),(arm+'_E400',dict(color=COLORS[arm],lw=1.5,zorder=6))]:
                values=y[:,p] if method=='observed' else bplus[:,p] if method=='bplus_full' else means[method][:,p]
                x=dx if method in ['observed','bplus_full'] else dx[1168:]
                line(fig,ax,name,point+'_'+method,x,values-y0[p],dict(type='trajectory',method=method,point=p,seed=None),**style)
            r200=pointsummary.loc[(1168,arm+'_E200',point),'rmse'];r400=pointsummary.loc[(1168,arm+'_E400',point),'rmse']
            rb=pointsummary.loc[(1168,'BPLUS_CONTINUOUS',point),'rmse'];coverage=100*pointsummary.loc[(1168,arm+'_E400',point),'coverage90']
            label=f'RMSE：200次 {r200:.2f} / 400次 {r400:.2f} mm\nB+ {rb:.2f} mm；400次90%覆盖 {coverage:.1f}%'
            ax.text(.5,-.21,label,transform=ax.transAxes,ha='center',va='top',fontsize=7.4)
            annotations.append(dict(figure=name,point=point,arm=arm,rmse200=float(r200),rmse400=float(r400),rmse_bplus=float(rb),coverage90=float(coverage),limits=limits[p],text=label))
        fig.text(.5,.09,'蓝底：前1168日；橙底：后293日。粗线为三种子等权均值；细线为400次各种子，阴影为固定边际区间。',ha='center',fontsize=7.4)
        fig.text(.5,.055,'两模型共用完整显示范围；无尾段删除、无神经训练段补画。最终窗口为探索性评价。',ha='center',fontsize=7.4)
        finish(fig,name)
    c.o.write_json(out/'plot_records.json',records);c.o.write_json(out/'annotations.json',annotations)
    c.o.write_json(out/'exports.json',dict(figures=exports,common_limits=limits,backend='python',panels=16))
    c.o.lock(out,'artifact_lock.json',[p for p in out.glob('*') if p.is_file()],status='rendered_pending_visual_qa')
    print(str(out),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='v1');args=parser.parse_args()
    main(args.attempt)
