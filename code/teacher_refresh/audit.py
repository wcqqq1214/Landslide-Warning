"""Independent formulas, saved-weight NumPy replay, and physical source audit."""
import argparse
import json
import traceback
import warnings
import numpy as np
import pandas as pd
import torch
from physics_guided.reference import load
from tcn_conditional_trajectory.core import array_sha
from overnight_graph.audit import physical, raw, numpy_forward
from transformer_temporal.audit import independent_scores, independent_gate
from . import core as c

o=c.o


def independent_tensors(bank,y,m,hs,sc,arm,current=False):
    if current:
        tid=1168 if m==1168 else max(t for t in (432,612,792) if t<=m)
        t=bank['cached'][tid]
    elif arm=='G_CACHED':
        t=bank['cached'][max(t for t in (432,612,792) if t<=m)]
    else:
        t=bank['refreshed'][m]
    h=np.r_[0,np.asarray(hs,np.int64)]
    av,sd=np.array(sc['mean']),np.array(sc['std'])
    history=((raw(t,y[:m])-av)/sd).transpose(1,0,2)[None]
    future=((physical(t)[m+h-1]-av[:,:10])/sd[:,:10])[None]
    distance=np.stack([h/293,np.log1p(h)/np.log(294)],-1)[None]
    return (history,np.array([m],np.int64),future,distance),t


def main(attempt):
    cfg=c.spec();o.setup(cfg);root=c.ROOT/cfg['out'];out=root/attempt
    out.mkdir(exist_ok=False)
    receipt=dict(status='running',started_utc=o.utc(),values_checked=0,max_difference=0.,
                 checkpoints=0,teacher_fits_verified=0,neural_fits_verified=0,updates_verified=0,
                 audit_new_fits=0,audit_optimizer_updates=0,audit_physical_forwards=0)
    records=[];teacher_rows=[];coverage=[];support_rows=[]

    def close(name,a,b,tol=1e-8):
        a,b=np.asarray(a,float),np.asarray(b,float)
        assert a.shape==b.shape,(name,a.shape,b.shape)
        assert np.isfinite(a).all() and np.isfinite(b).all(),name
        diff=float(np.max(abs(a-b))) if a.size else 0.
        assert diff<=tol,(name,diff,tol)
        receipt['values_checked']+=a.size
        receipt['max_difference']=max(receipt['max_difference'],diff)
        records.append(dict(check=name,values=a.size,max_difference=diff,tolerance=tol))

    try:
        receipt['source_files']=c.guard()
        done=o.verify_lock(root/'training_complete.json');o.verify_lock(root/'analysis_lock.json')
        assert done['new_fits']==24 and done['updates']==4800 and done['new_bplus_fits']==10
        y=o.read_labels(c.ROOT/cfg['data'],1461);forcing,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
        bank=c.bank(cfg);ref=load(c.ROOT/cfg['runtime'])
        all_warnings=[]
        for m in cfg['teacher_update']['new_prefixes']:
            folder=root/f'teachers/fit_{m}';o.verify_lock(folder/'complete.json')
            inp=o.read_json(folder/'input.json');meta=o.read_json(folder/'fit.json');t=bank['refreshed'][m]
            oldid=max(p for p in (432,612,792) if p<=m);old=bank['cached'][oldid]
            assert inp['initialization_teacher']==inp['initialization_fit_prefix']==oldid<m
            assert inp['label_rows']==inp['forcing_rows']==inp['fit_prefix']==m
            assert meta['fit_prefix']==int(t['teacher_fit_prefix'])==m
            assert inp['config_sha256']==o.sha(c.CONFIG)
            close(f'teacher/{m}/initialization',inp['input_theta'],old['theta'],0)
            close(f'teacher/{m}/clipped_initialization',inp['optimizer_initial_theta'],np.clip(old['theta'],ref.LO+1e-9,ref.HI-1e-9),0)
            close(f'teacher/{m}/parameter_record',meta['theta'],t['theta'],0)
            assert np.all(t['theta']>=ref.LO) and np.all(t['theta']<=ref.HI)
            assert inp['training_label_sha256']==meta['training_label_sha256']==array_sha(y[:m])
            assert inp['training_forcing_sha256']==meta['training_forcing_sha256']==array_sha(forcing[:m])
            assert inp['training_last_date']==dates[m-1]
            end=m+293
            assert np.array_equal(t['dates'],dates[:end]) and meta['rollout_end_exclusive']==end
            close(f'teacher/{m}/forcing',t['forcing'],forcing[:end],0)
            assert 1<=meta['nfev']<=800 and meta['max_nfev']==800 and meta['terminal_weight']==100*m
            assert meta['optimization_forward_calls']==meta['nfev']+55*meta['njev']
            with warnings.catch_warnings(record=True) as ws:
                warnings.simplefilter('default')
                ctx=ref.Context(forcing[:end])
                mu,states=ref.forward(t['theta'],ctx,substeps=64,states=True)
                prefix,ps=ref.forward(t['theta'],ref.Context(forcing[:m]),substeps=64,states=True)
            receipt['audit_physical_forwards']+=2
            all_warnings.extend(dict(fit_prefix=m,message=str(w.message)) for w in ws)
            close(f'teacher/{m}/full_mean',mu+y[0],t['mean'],0)
            close(f'teacher/{m}/prefix_mean',prefix,mu[:m],1e-8)
            for k in states:
                close(f'teacher/{m}/state/{k}',states[k],t[k],0)
                a=states[k][:m] if len(states[k])==end else states[k]
                close(f'teacher/{m}/prefix/{k}',ps[k],a,1e-8)
            close(f'teacher/{m}/explicit_projection',np.einsum('ij,kj->ik',states['coordinates'],ctx.obs,optimize=False),mu,1e-8)
            close(f'teacher/{m}/derived_features',physical(t),o.own_physics(t),1e-8)
            error=prefix-(y[:m]-y[0]);trajectory=np.sum((error/100)**2);terminal=100*m*np.sum((error[-1]/100)**2)
            close(f'teacher/{m}/objective',[meta[k] for k in ('trajectory_objective','terminal_objective','objective')],
                  [trajectory,terminal,trajectory+terminal],1e-8)
            close(f'teacher/{m}/terminal_error',meta['terminal_error_mm'],error[-1],0)
            for p,point in enumerate(cfg['points']):
                for policy,mu0 in [('G_CACHED',old['mean'][:m]),('G_REFRESH',t['mean'][:m])]:
                    e=mu0[:,p]-y[:m,p]
                    teacher_rows.append(dict(fit_prefix=m,point=point,policy=policy,
                        fit_mae=float(abs(e).mean()),fit_rmse=float(np.sqrt(np.mean(e*e))),
                        terminal_error=float(e[-1]),optimizer_success=meta['optimizer_success'] if policy=='G_REFRESH' else None))
            receipt['teacher_fits_verified']+=1
        methods=cfg['controls']+cfg['arms'];previous=None;allmeans={};allseeds={};metrics={};seedmetrics={}
        keys={'phase_summary':['origin','method'],'metrics_by_point':['origin','method','point'],
              'seed_summary':['origin','method','seed'],'seed_metrics_by_point':['origin','method','seed','point']}
        frames={name:pd.read_csv(root/'analysis'/f'{name}.csv',float_precision='round_trip').set_index(k) for name,k in keys.items()}
        daily=pd.read_csv(root/'analysis/daily_predictions.csv',float_precision='round_trip')
        endpoints=pd.read_csv(root/'analysis/endpoint_errors.csv',float_precision='round_trip',dtype={'seed':str})
        for n,end in zip(cfg['origins'],cfg['ends']):
            folder=root/f'origin_{n}';o.verify_lock(folder/'issue_lock.json')
            sc=o.read_json(folder/'scaling.json');teacher=bank['cached'][1168 if n==1168 else max(t for t in (432,612,792) if t<=n)]
            ra=raw(teacher,y[:n])
            close(f'{n}/scale_mean',sc['mean'],ra.mean(0),1e-12)
            close(f'{n}/scale_std',sc['std'],np.maximum(ra.std(0),1e-6),1e-12)
            close(f'{n}/scale_unit',sc['unit'],np.maximum(y[:n].std(0),1),0)
            assert sc['fit_prefix']==n
            means=o.load_npz(folder/'means.npz');seeds=o.load_npz(folder/'seeds.npz')
            allmeans[n],allseeds[n]=means,seeds
            assert set(means)==set(seeds)==set(methods)
            base=teacher['mean'][n:end];r0=y[n-1]-teacher['mean'][n-1]
            close(f'{n}/bplus',means[c.B],base,0)
            close(f'{n}/anchored_bplus',means[c.BA],base+r0,0)
            close(f'{n}/drift',means['DRIFT1'],y[n-1]+np.arange(1,294)[:,None]*(y[n-1]-y[n-2]),0)
            pm=o.load_npz(c.ROOT/cfg['prior_controls_out']/f'origin_{n}/means.npz')
            for method in (c.B,'DRIFT1','RR_COND'):
                close(f'{n}/{method}/frozen_control',means[method],pm[method],0)
            values,_=independent_tensors(bank,y[:n],n,np.arange(1,294),sc,'G_CACHED',True)
            for i,(a,b) in enumerate(zip(c.tensors(bank,y[:n],[n],np.arange(1,294)[None],sc,'G_CACHED',True),values)):
                close(f'{n}/issue_input_{i}',a.numpy(),b,1e-9)
            for arm in cfg['arms']:
                grid=np.arange(432,n,60)
                for m in grid:
                    hs=np.arange(1,min(293,n-m)+1)
                    expected,t=independent_tensors(bank,y[:n],m,hs,sc,arm)
                    actual=c.tensors(bank,y[:n],[m],hs[None],sc,arm)
                    for j,(a,b) in enumerate(zip(actual,expected)):
                        close(f'{n}/{arm}/{m}/training_input_{j}',a.numpy(),b,1e-9)
                    tr=(y[m+hs-1]-t['mean'][m+hs-1]-(y[m-1]-t['mean'][m-1]))/sc['unit']
                    close(f'{n}/{arm}/{m}/all_mature_targets',c.target(bank,y[:n],[m],hs[None],sc,arm).numpy()[0],tr,1e-12)
                    for p,point in enumerate(cfg['points']):
                        support_rows.append(dict(outer_prefix=n,training_origin=m,policy=arm,point=point,
                            teacher_fit_prefix=int(t['teacher_fit_prefix']),mature_distances=len(hs),
                            centered_target_mean_mm=float(tr[:,p].mean()*sc['unit'][p]),
                            positive_centered_target_fraction=float((tr[:,p]>0).mean())))
                predictions=[]
                for seed in cfg['seeds']:
                    dest=folder/arm/f'seed_{seed}';done=o.verify_lock(dest/'complete.json')
                    assert done['new_fits']==1 and done['updates']==200
                    receipt['neural_fits_verified']+=1;receipt['updates_verified']+=200
                    sched=o.load_npz(dest/'schedule.npz')
                    rng=np.random.default_rng(seed)
                    ms=grid[rng.integers(len(grid),size=(200,4))]
                    hs=np.array([[rng.integers(1,min(293,n-int(m))+1,size=16) for m in row] for row in ms])
                    close(f'{n}/{arm}/{seed}/origins',sched['origins'],ms,0)
                    close(f'{n}/{arm}/{seed}/distances',sched['horizons'],hs,0)
                    assert np.all(ms[...,None]+hs-1<n)
                    if arm=='G_CACHED':
                        for m in grid:
                            chosen=ms==m;dh=hs[chosen]
                            coverage.append(dict(outer_prefix=n,seed=seed,training_origin=int(m),
                                origin_draws=int(chosen.sum()),distance_draws=int(dh.size),
                                min_distance=int(dh.min()),max_distance=int(dh.max()),
                                maximum_mature_horizon=int(min(293,n-m)),complete_293=bool(n-m>=293)))
                    trace=[json.loads(row) for row in (dest/'training.jsonl').read_text().splitlines()]
                    assert [r['step'] for r in trace]==list(range(1,201))
                    close(f'{n}/{arm}/{seed}/loss',[r['loss'] for r in trace],[r['mse']+r['penalty'] for r in trace],0)
                    assert all(np.isfinite(r['grad_norm']) for r in trace)
                    for step in cfg['checkpoints']:
                        model,scale,saved=c.reload(dest/f'e{step}.pt')
                        assert scale==sc and saved['training_prefix']==n and saved['step']==step
                        assert saved['arm']==arm and saved['seed']==seed and saved['anchor']
                        assert saved['config_sha256']==o.sha(c.CONFIG)
                        assert saved['teacher_ids']==sorted(set(int(m) if arm=='G_REFRESH' else max(t for t in (432,612,792) if t<=m) for m in grid))
                        assert sum(p.numel() for p in model.parameters())==1241
                        states=saved['optimizer_state_dict']['state']
                        assert len(states)==(0 if step==0 else len(list(model.parameters())))
                        assert all(int(st['step'])==step for st in states.values())
                        mu,change,_=c.predict(model,bank,y[:n],n,end,sc)
                        close(f'{n}/{arm}/{seed}/{step}/reload',mu,np.load(dest/f'e{step}_mean.npy'),0)
                        close(f'{n}/{arm}/{seed}/{step}/change',change,np.load(dest/f'e{step}_learned_mm.npy'),0)
                        q,_=numpy_forward(saved,values,np.zeros((294,4)))
                        close(f'{n}/{arm}/{seed}/{step}/numpy',base+r0+q[1:]-q[:1],mu,1e-7)
                        close(f'{n}/{arm}/{seed}/{step}/h0',teacher['mean'][n-1]+r0+q[0]-q[0],y[n-1],1e-10)
                        if step==0:
                            close(f'{n}/{arm}/{seed}/zero',mu,base+r0,0)
                            expected=torch.load(c.ROOT/cfg['prior_controls_out']/f'origin_{n}/GRU_GRAPH/seed_{seed}/e0.pt',map_location='cpu',weights_only=True)['state_dict']
                            for k,v in saved['state_dict'].items():
                                close(f'{n}/{arm}/{seed}/init/{k}',v.numpy(),expected[k].numpy(),0)
                        if step==200:
                            poisoned=y.copy();poisoned[n:]=1e15
                            close(f'{n}/{arm}/{seed}/future_label_poison',c.predict(model,bank,poisoned,n,end,sc)[0],mu,0)
                            predictions.append(mu)
                        receipt['checkpoints']+=1
                    print(f'audit origin={n} {arm} seed={seed}',flush=True)
                close(f'{n}/{arm}/seeds',seeds[arm],predictions,0)
                close(f'{n}/{arm}/ensemble',means[arm],np.mean(predictions,axis=0),0)
            if previous is None:
                previous=n;continue
            sigma=o.load_npz(folder/'sigmas.npz');errors=o.load_npz(folder/'calibration_errors.npz')
            meta=o.read_json(folder/'calibration.json')
            assert meta==dict(previous_origin=previous,start=previous+90,end=previous+180,matured_before=n,count=90,gap=n-previous-180)
            assert previous+180<=n
            metrics[n]={};seedmetrics[n]={}
            for method in methods:
                e=y[previous+90:previous+180]-allmeans[previous][method][90:180]
                close(f'{n}/{method}/calibration',errors[method],e,0)
                close(f'{n}/{method}/scale',sigma[method],np.maximum(np.sqrt(np.mean(e*e,0)),1e-6),0)
                got=independent_scores(y[n:end],means[method],sigma[method]);metrics[n][method]=got
                for metric,v in got.items():
                    close(f'{n}/{method}/{metric}/summary',frames['phase_summary'].loc[(n,method),metric],np.mean(v),1e-8)
                    close(f'{n}/{method}/{metric}/points',[frames['metrics_by_point'].loc[(n,method,p),metric] for p in cfg['points']],v,1e-8)
                seedmetrics[n][method]=[]
                for s in cfg['seeds']:
                    got=independent_scores(y[n:end],seeds[method][s],sigma[method]);seedmetrics[n][method].append(got)
                    for metric,v in got.items():
                        close(f'{n}/{method}/{s}/{metric}/summary',frames['seed_summary'].loc[(n,method,s),metric],np.mean(v),1e-8)
                        close(f'{n}/{method}/{s}/{metric}/points',[frames['seed_metrics_by_point'].loc[(n,method,s,p),metric] for p in cfg['points']],v,1e-8)
                frame=daily[(daily.origin==n)&(daily.method==method)]
                assert len(frame)==1172 and set(frame.point)==set(cfg['points'])
                for p,point in enumerate(cfg['points']):
                    f=frame[frame.point==point]
                    close(f'{n}/{method}/{point}/target_indices',f.target_index.to_numpy(),np.arange(n,end),0)
                    assert np.array_equal(f.date.to_numpy(),dates[n:end]) and (f.issue_date==dates[n-1]).all()
                    close(f'{n}/{method}/{point}/daily',f[['observed','mean','sigma']].to_numpy(),
                          np.column_stack([y[n:end,p],means[method][:,p],np.repeat(sigma[method][p],293)]),0)
                for ident,mu in [('ensemble',means[method])]+[(str(s),seeds[method][s]) for s in cfg['seeds']]:
                    f=endpoints[(endpoints.origin==n)&(endpoints.method==method)&(endpoints.seed==ident)]
                    for row in f.itertuples():
                        p=cfg['points'].index(row.point);e=mu[row.horizon-1,p]-y[n+row.horizon-1,p]
                        close(f'{n}/{method}/{ident}/{row.horizon}/{p}/endpoint',[row.error,row.absolute_error,row.squared_error],[e,abs(e),e*e],1e-8)
            previous=n
        pairing=o.read_json(root/'analysis/pairing.json');assert len(pairing)==27
        for pair in pairing:
            n,a,b=pair['origin'],pair['candidate'],pair['reference']
            expected=independent_gate(metrics[n][a],metrics[n][b],cfg)
            for key,v in expected.items():assert pair[key]==v,(n,a,b,key)
            flags=[all(seedmetrics[n][a][s][k].mean()<seedmetrics[n][b][s][k].mean() for k in ('mae','rmse')) for s in cfg['seeds']]
            assert pair['seed_flags']==flags and pair['seed_both_improve']==sum(flags)
        diff=pd.read_csv(root/'analysis/paired_differences.csv',dtype={'seed':str},float_precision='round_trip')
        assert len(diff)==360
        for row in diff.itertuples():
            metric=row.metric;n=row.origin
            if row.seed=='ensemble':a,b=metrics[n]['G_REFRESH'][metric],metrics[n]['G_CACHED'][metric]
            else:a,b=seedmetrics[n]['G_REFRESH'][int(row.seed)][metric],seedmetrics[n]['G_CACHED'][int(row.seed)][metric]
            a,b=(a.mean(),b.mean()) if row.point=='average' else (a[cfg['points'].index(row.point)],b[cfg['points'].index(row.point)])
            close(f'pair/{n}/{row.seed}/{row.point}/{metric}',[row.refreshed,row.cached,row.difference],[a,b,a-b],1e-8)
            if b:close('relative_difference',row.relative_change,a/b-1,1e-8)
        events=[json.loads(s) for s in (root/'events.jsonl').read_text().splitlines()]
        issued=set();teacher_done=set();ready=set();fit_count={};final_label=False
        for ev in events:
            kind=ev['event']
            if kind=='label_prefix_read' and ev['rows']==1461:
                assert issued==set(cfg['origins']);final_label=True
            if kind=='teacher_fit_completed':teacher_done.add(ev['fit_prefix'])
            if kind=='teachers_ready_for_neural_prefix':
                n=ev['origin'];assert all(m in teacher_done for m in cfg['teacher_update']['new_prefixes'] if m<n);ready.add(n)
            if kind=='fit_started':assert ev['origin'] in ready and not final_label
            if kind=='fit_completed':fit_count[ev['origin']]=fit_count.get(ev['origin'],0)+1
            if kind=='trajectory_issued':assert fit_count[ev['origin']]==6;issued.add(ev['origin'])
        assert final_label and teacher_done==set(cfg['teacher_update']['new_prefixes'])
        outcome=o.read_json(root/'analysis/outcome.json')
        assert outcome['stable_mean_policy']==all(p['mean_pass'] and p['seed_both_improve']>=2 for p in pairing if p['reference']=='G_CACHED')
        assert receipt['checkpoints']==96 and receipt['teacher_fits_verified']==10 and receipt['updates_verified']==4800
        pd.DataFrame(teacher_rows).to_csv(out/'teacher_fit_points.csv',index=False,float_format='%.17g')
        pd.DataFrame(support_rows).to_csv(out/'training_target_support.csv',index=False,float_format='%.17g')
        pd.DataFrame(coverage).to_csv(out/'sample_coverage.csv',index=False,float_format='%.17g')
        o.write_json(out/'warnings.json',all_warnings)
        receipt.update(status='passed',finished_utc=o.utc(),checks=len(records),paired_gates=27,events=len(events),
                       independent_full_prediction_paths=96,teacher_projection='original C state replay and explicit observation contraction',
                       numerical_independence='separate NumPy GRU, feature construction and score formulas; physics uses original native solver')
    except Exception:
        receipt.update(status='failed',finished_utc=o.utc(),error=traceback.format_exc())
        o.write_json(out/'receipt.json',receipt)
        pd.DataFrame(records).to_csv(out/'checks.csv',index=False,float_format='%.17g')
        raise
    o.write_json(out/'receipt.json',receipt)
    pd.DataFrame(records).to_csv(out/'checks.csv',index=False,float_format='%.17g')
    o.write_json(out/'implementation.json',dict(file='code/teacher_refresh/audit.py',sha256=o.sha(c.ROOT/'code/teacher_refresh/audit.py')))
    o.lock(root,'audit_lock.json',list(out.glob('*')),status='passed')
    print(receipt,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='audit');args=parser.parse_args();main(args.attempt)
