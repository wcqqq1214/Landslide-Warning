"""Independent checks plus first-day diagnosis at all saved teacher-fit boundaries."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
import torch
import overnight_graph.core as g
import transformer_origin.core as o
from overnight_graph.audit import independent_inputs as gi, numpy_forward as gn
from transformer_origin.audit import independent_inputs as oi, numpy_forward as on

P=Path(__file__).parent
cfg=g.spec();ocfg=o.spec();g.setup(cfg)
g.verify_implementation(cfg);o.verify_implementation(ocfg)
bank=g.bank(cfg);y=g.read_labels(g.ROOT/cfg['data'],1461)
arms=['GRU_LOCAL','GRU_GRAPH','COND_ATTN'];points=['ATU1','ATU5','MJ3','MJ1']
older={612:432,792:612,972:612,1168:792}
roots={a:g.ROOT/(cfg['out']+'/base' if a.startswith('GRU') else ocfg['out']) for a in arms}
summary=pd.read_csv(P/'forecast_summary.csv');by_point=pd.read_csv(P/'forecast_by_point.csv')
current=lambda n:1168 if n==1168 else g.teacher_id(n)
checks=[];boundary=[];coverage=[]


def close(a,b,label):
    a,b=np.asarray(a),np.asarray(b)
    assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),label
    delta=float(np.max(abs(a-b))) if a.size else 0.
    assert delta<1e-8,(label,delta)
    checks.append(dict(label=label,values=int(a.size),difference=delta))


with torch.inference_mode():
    for n in [612,792,972,1168]:
        new=current(n);old=older[n];h=min(293,len(bank[old]['mean'])-n)
        base=bank[new]['mean'][n:n+293];target=y[n:n+293];r0=y[n-1]-bank[new]['mean'][n-1]
        for seed in [0,1,2]:
            z=np.load(roots['GRU_LOCAL']/f'origin_{n}/GRU_LOCAL/seed_{seed}/schedule.npz')
            m=z['origins'].reshape(-1);dist=z['horizons'].reshape(-1,16)
            tid=np.array([g.teacher_id(int(i)) for i in m]);fresh=m==tid
            coverage.append(dict(origin=n,seed=seed,query_draws=int(dist.size),origin_draws=int(m.size),
                fresh_origin_draws=int(fresh.sum()),fresh_origin_ids=','.join(map(str,sorted(set(m[fresh])))),
                fresh_query_draws=int(dist[fresh].size),fresh_h1=int((dist[fresh]==1).sum()),
                fresh_h1_7=int((dist[fresh]<=7).sum()),all_h1=int((dist==1).sum()),all_h1_7=int((dist<=7).sum())))
        for arm in arms:
            for seed in [0,1,2,'ensemble']:
                raw=np.load(roots[arm]/f'origin_{n}/{arm}/seed_{seed}/e200_mean.npy') if seed!='ensemble' else np.mean([
                    np.load(roots[arm]/f'origin_{n}/{arm}/seed_{s}/e200_mean.npy') for s in [0,1,2]],axis=0)
                saved=np.load(P/f'paths_{n}_{arm}_{seed}.npz')
                ref=raw[0]-base[0]
                expected=dict(BPLUS=base,ANCHOR_ONLY=base+r0,SHAPE_ONLY=raw-ref,
                              ANCHORED_SHAPE=raw-ref+r0,ORIGINAL=raw)
                for variant,pred in expected.items():
                    close(saved[variant],pred,'path_identity')
                    e=pred-target;rmse=np.sqrt(np.sum(e*e,axis=0)/293);mae=np.sum(abs(e),axis=0)/293
                    row=summary.query('origin==@n and arm==@arm and seed==@seed and variant==@variant')
                    if row.empty:
                        row=summary.query('origin==@n and arm==@arm and variant==@variant').loc[lambda v:v.seed.astype(str)==str(seed)]
                    row=row.iloc[0]
                    close([row.mae,row.rmse],[mae.mean(),rmse.mean()],'summary_scores')
                    for p,point in enumerate(points):
                        row=by_point.query('origin==@n and arm==@arm and variant==@variant and point==@point').loc[lambda v:v.seed.astype(str)==str(seed)].iloc[0]
                        close([row.mae,row.rmse],[mae[p],rmse[p]],'point_scores')
                if seed=='ensemble':continue
                module,config=(g,cfg) if arm.startswith('GRU') else (o,ocfg)
                model,scale,cp=module.reload(roots[arm]/f'origin_{n}/{arm}/seed_{seed}/e200.pt',config);model.eval()
                in_versions=[]
                for teacher in [old,new]:
                    alias=dict(bank);alias[new]=bank[teacher]
                    args=(gi if arm.startswith('GRU') else oi)(alias,y[:n],[n],np.arange(1,h+1)[None],scale,current=True)
                    in_versions.append(args)
                factor=np.load(P/f'factor_{n}_{arm}_{seed}.npz')['correction']
                for phys in [0,1]:
                    for res in [0,1]:
                        args=[v.copy() for v in in_versions[phys]]
                        channels=slice(12,14) if arm.startswith('GRU') else slice(26,30)
                        args[0][...,channels]=in_versions[res][0][...,channels]
                        independent=gn(cp,args,base[:h])[0] if arm.startswith('GRU') else on(cp,args,base[:h])
                        close(independent,base[:h]+factor[phys,res],'numpy_mixed_teacher_forward')
                for m in [tid for tid in [432,612,792] if tid<n]+[n]:
                    teacher=current(m) if m==n else g.teacher_id(m)
                    args=module.inputs(bank,y[:m],[m],np.array([[1]]),scale,current=(m==n))
                    c=model(*args).numpy()[0,0]
                    if arm.startswith('GRU'):c=c.sum(-1)
                    c=c*np.asarray(scale['unit'])
                    r1=y[m]-bank[teacher]['mean'][m];r_start=y[m-1]-bank[teacher]['mean'][m-1]
                    if m==n:close(c,ref,'boundary_matches_saved_h1')
                    for p,point in enumerate(points):
                        boundary.append(dict(fit_prefix=n,arm=arm,seed=seed,query_origin=m,teacher=teacher,
                            teacher_age=m-teacher,within_fitting_prefix=bool(m<n),point=point,
                            initial_residual_mm=float(r_start[p]),true_day1_residual_mm=float(r1[p]),
                            predicted_day1_correction_mm=float(c[p]),error_to_half_target_mm=float(c[p]-.5*r1[p]),
                            forecast_day1_error_mm=float(c[p]-r1[p])))
                del model

pd.DataFrame(coverage).to_csv(P/'fresh_teacher_query_counts.csv',index=False)
pd.DataFrame(boundary).to_csv(P/'boundary_by_point.csv',index=False,float_format='%.17g')
frame=pd.DataFrame(boundary);rows=[]
for keys,group in frame.groupby(['fit_prefix','arm','query_origin','teacher','teacher_age','within_fitting_prefix']):
    # Average predictions over seeds before scoring across points.
    v=group.groupby('point')[['initial_residual_mm','true_day1_residual_mm','predicted_day1_correction_mm']].mean()
    e=v.predicted_day1_correction_mm-v.true_day1_residual_mm
    half=v.predicted_day1_correction_mm-.5*v.true_day1_residual_mm
    rows.append(dict(zip(['fit_prefix','arm','query_origin','teacher','teacher_age','within_fitting_prefix'],keys),
        bplus_day1_mae_mm=float(abs(v.true_day1_residual_mm).mean()),model_day1_mae_mm=float(abs(e).mean()),
        half_target_day1_mae_mm=float(abs(half).mean()),max_absolute_initial_residual_mm=float(abs(v.initial_residual_mm).max())))
pd.DataFrame(rows).to_csv(P/'boundary_summary.csv',index=False,float_format='%.17g')
(P/'verification_checks.json').write_text(json.dumps(checks,indent=2))
receipt=dict(status='passed',finished_utc=g.utc(),checked_values=sum(x['values'] for x in checks),
    max_difference=max(x['difference'] for x in checks),boundary_rows=len(boundary),
    training_updates=0,physical_forwards=0,h0_queries=0,
    source_entries=dict(graph=g.guard(),origin=o.guard()),
    interpretation='Historical teacher-boundary evaluations are in-sample diagnosis. Mixed teacher inputs measure model dependence only.')
(P/'verification_receipt.json').write_text(json.dumps(receipt,indent=2))
(P/'final_hashes.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(P.iterdir()) if p.is_file() and p.name!='final_hashes.json'},indent=2))
print(json.dumps(receipt))
