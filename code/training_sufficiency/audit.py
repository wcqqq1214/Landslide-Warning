"""Independent target, saved-weight, score and gate verification before extension."""
import json
import argparse
import traceback
import numpy as np
import pandas as pd
import torch
from overnight_graph.audit import independent_inputs, raw
from backbone_anchor.numpy_model import numpy_forward
from . import core as c


def main(attempt):
    cfg=c.spec(); c.o.setup(cfg); root=c.ROOT/cfg['out']; src=root/'diagnostic'
    out=root/attempt; out.mkdir(exist_ok=False)
    checks=[]; count=c.guard(); c.o.verify_lock(root/'diagnostic_lock.json')

    def close(name,a,b,tol=1e-9):
        a,b=np.asarray(a,float),np.asarray(b,float)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),name
        diff=float(np.max(abs(a-b))) if a.size else 0.
        assert diff<=tol,(name,diff,tol)
        checks.append(dict(check=name,values=int(a.size),max_difference=diff,tolerance=tol))

    y=c.o.labels(cfg,1168,'independent_diagnostic_replay_no_final_labels'); bank=c.o.bank(cfg)
    forcing,dates=c.o.read_forcing(c.ROOT/cfg['data'],1461)
    tr=pd.read_csv(src/'training_summary.csv',float_precision='round_trip').set_index(['origin','arm','seed','step'])
    tp=pd.read_csv(src/'training_points.csv',float_precision='round_trip').set_index(['origin','arm','seed','step','point'])
    train_metrics={}; hist_metrics={}; checkpoints=0
    for n in cfg['origins']:
        c.o.check_deadline(cfg)
        data=c.o.load_npz(src/f'panel_{n}.npz'); ms,hs=data['origins'],data['horizons']
        em=np.array([432+int((i+.5)*(n-432)/128) for i in range(128)])
        eh=np.array([[1+int((j+.5)*min(293,n-int(m))/32) for j in range(32)] for m in em])
        close(f'{n}/origins',ms,em,0);close(f'{n}/horizons',hs,eh,0)
        assert all(m+h<n for m,row in zip(ms,hs) for h in row-1)
        sc=c.o.read_json(c.ROOT/cfg['source_experiment']/f'origin_{n}/scaling.json')
        tid=1168 if n==1168 else 792 if n>=792 else 612
        rawdata=raw(bank[tid],y[:n])
        close(f'{n}/scale_mean',sc['mean'],rawdata.mean(0),1e-12)
        close(f'{n}/scale_std',sc['std'],np.maximum(rawdata.std(0),1e-6),1e-12)
        close(f'{n}/scale_unit',sc['unit'],np.maximum(y[:n].std(0),1),0)
        for teacher in bank.values():
            size=len(teacher['dates'])
            assert np.array_equal(teacher['dates'],dates[:size])
            close(f'{n}/teacher_forcing/{size}',teacher['forcing'],forcing[:size],0)
        expected=[]
        for m,ds in zip(ms,hs):
            tid=792 if m>=792 else 612 if m>=612 else 432
            t=bank[tid]['mean']; assert tid<=m
            expected.append([(y[m+d-1]-y[m-1]-t[m+d-1]+t[m-1])/data['unit'] for d in ds])
        target=np.asarray(expected);close(f'{n}/all_targets',data['target'],target,1e-12)
        for arm in cfg['arms']:
            for seed in cfg['seeds']:
                for step in cfg['diagnostic']['checkpoints']:
                    q=data[f'{arm}_s{seed}_e{step}']; identity=(n,arm,seed,step)
                    err=q-target; mse=np.sum(err**2,axis=(0,1))/(128*32)
                    penalty=np.sum(q*q,axis=(0,1))/(128*32)
                    lower=np.sum(target**2,axis=(0,1))/(2*128*32)
                    metrics=dict(mse=mse,penalty=penalty,objective=mse+penalty,
                                 free_output_lower_bound=lower,
                                 excess_objective=2*np.sum((q-target/2)**2,axis=(0,1))/(128*32),
                                 mae_mm=np.sum(abs(err),axis=(0,1))*data['unit']/(128*32),
                                 rmse_mm=np.sqrt(mse)*data['unit'])
                    train_metrics[identity]={k:float(v.mean()) for k,v in metrics.items()}
                    for k,v in metrics.items():
                        close(f'{identity}/{k}/summary',tr.loc[identity,k],v.mean())
                        close(f'{identity}/{k}/points',[tp.loc[identity+(p,),k] for p in cfg['points']],v)
                    assert np.isfinite(tr.loc[identity,'gradient_norm']) and tr.loc[identity,'gradient_norm']>=0
                    path=c.checkpoint(n,arm,seed,step)
                    saved=torch.load(path,map_location='cpu',weights_only=True)
                    for slot in [0,42,85,127]:
                        m=int(ms[slot]); distances=np.r_[0,hs[slot]][None]
                        inputs=independent_inputs(bank,y[:n],[m],distances,sc)
                        output,_=numpy_forward(saved,inputs,np.zeros((33,4)))
                        close(f'{identity}/numpy_panel_slot{slot}',q[slot],(output[1:]-output[:1])/data['unit'],1e-8)
                    inputs=independent_inputs(bank,y[:n],[n],np.arange(294)[None],sc,True)
                    output,_=numpy_forward(saved,inputs,np.zeros((294,4)))
                    teacher=bank[1168 if n==1168 else 792 if n>=792 else 612]['mean']
                    mu=teacher[n:n+293]+y[n-1]-teacher[n-1]+output[1:]-output[:1]
                    close(f'{identity}/numpy_full_prediction',np.load(path.with_name(f'e{step}_mean.npy')),mu,1e-7)
                    checkpoints+=1
        print(f'n={n}: all targets/losses and 24 independent checkpoint replays passed',flush=True)
    hi=pd.read_csv(src/'historical_summary.csv',dtype={'seed':str},float_precision='round_trip').set_index(['origin','arm','seed','step'])
    hp=pd.read_csv(src/'historical_points.csv',dtype={'seed':str},float_precision='round_trip').set_index(['origin','arm','seed','step','point'])
    daily=pd.read_csv(src/'historical_daily.csv',float_precision='round_trip')
    paths=c.o.load_npz(src/'historical_predictions.npz')
    for n,end in zip(cfg['diagnostic']['historical_origins'],cfg['diagnostic']['historical_ends']):
        assert end<=1168
        close(f'{n}/mature_truth',paths[f'truth_{n}'],y[n:end],0)
        for arm in cfg['arms']:
            for step in cfg['diagnostic']['checkpoints']:
                predictions=paths[f'{n}_{arm}_e{step}']
                original=np.stack([np.load(c.checkpoint(n,arm,s,step).with_name(f'e{step}_mean.npy')) for s in cfg['seeds']])
                close(f'{n}/{arm}/{step}/original_paths',predictions,original,0)
                for seed,mu in [('ensemble',predictions.mean(0))]+[(str(s),predictions[s]) for s in cfg['seeds']]:
                    error=mu-y[n:end]; metrics={'mae':np.sum(abs(error),axis=0)/293,'rmse':np.sqrt(np.sum(error**2,axis=0)/293)}
                    identity=(n,arm,seed,step);hist_metrics[identity]={k:float(v.mean()) for k,v in metrics.items()}
                    for k,v in metrics.items():
                        close(f'{identity}/{k}/history',hi.loc[identity,k],v.mean())
                        close(f'{identity}/{k}/history_points',[hp.loc[identity+(p,),k] for p in cfg['points']],v)
                    if seed=='ensemble':
                        for p,point in enumerate(cfg['points']):
                            rows=daily[(daily.origin==n)&(daily.arm==arm)&(daily.step==step)&(daily.point==point)].sort_values('distance')
                            close(f'{identity}/{point}/daily_pred',rows.prediction,mu[:,p])
                            close(f'{identity}/{point}/daily_y',rows.observed,y[n:end,p],0)
                            close(f'{identity}/{point}/daily_index',rows.target_index,np.arange(n,end),0)
    # Recompute the gate from independently obtained metrics, without calling c.decision.
    saved=c.o.read_json(src/'trigger.json'); rule=cfg['trigger']; independently={}
    for arm in cfg['arms']:
        train_count=0
        for n in cfg['origins']:
            flags=[]
            for seed in cfg['seeds']:
                a,b=[train_metrics[(n,arm,seed,k)] for k in (100,200)]
                flags.append(all(b[k]<=a[k]-.01*max(abs(a[k]),1e-15) for k in ['mse','objective']))
            train_count+=sum(flags)>=2
            record=next(v for v in saved['arms'][arm]['training'] if v['origin']==n)
            assert record['count']==sum(flags)
        a={k:np.mean([hist_metrics[(n,arm,'ensemble',100)][k] for n in [612,792]]) for k in ['mae','rmse']}
        b={k:np.mean([hist_metrics[(n,arm,'ensemble',200)][k] for n in [612,792]]) for k in ['mae','rmse']}
        hp_pass=all(b[k]<=a[k]-.01*max(abs(a[k]),1e-15) for k in a)
        for n in [612,792]:
            a,b=[hist_metrics[(n,arm,'ensemble',s)] for s in (100,200)]
            hp_pass=hp_pass and all(b[k]<=a[k]+.05*max(abs(a[k]),1e-15) for k in a)
            flags=[all(hist_metrics[(n,arm,str(s),200)][k]<hist_metrics[(n,arm,str(s),100)][k] for k in ['mae','rmse']) for s in cfg['seeds']]
            hp_pass=hp_pass and sum(flags)>=2
            record=next(v for v in saved['arms'][arm]['historical'] if v['origin']==n)
            assert record['seed_count']==sum(flags)
        independently[arm]=bool(train_count>=3 and hp_pass)
        assert saved['arms'][arm]['training_pass']==(train_count>=3)
        assert saved['arms'][arm]['historical_pass']==bool(hp_pass)
        assert saved['arms'][arm]['combined_pass']==independently[arm]
    assert saved['extend']==independently['T10_ANCHOR_UNIFORM']
    events=[json.loads(s) for s in (root/'events.jsonl').read_text().splitlines()]
    assert max(e['rows'] for e in events if e['event']=='label_prefix_read')<=1168
    receipt=dict(status='passed',source_files=count,checkpoints=checkpoints,checks=len(checks),
                 values=sum(v['values'] for v in checks),max_difference=max(v['max_difference'] for v in checks),
                 full_panel_target_loss_values=True,independent_numpy_panel_slots=[0,42,85,127],
                 independent_numpy_full_paths=96,gradient_norm='finite and weights unchanged; not an independent gradient calculation',
                 gate_reproduced=True,extend=saved['extend'],new_fits=0,optimizer_updates=0,max_label_rows=1168)
    c.o.write_json(out/'checks.json',checks);c.o.write_json(out/'receipt.json',receipt)
    c.o.lock(root,'diagnostic_audit_lock.json',list(out.glob('*')),status='passed',extend=saved['extend'])
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='diagnostic_audit')
    args=parser.parse_args()
    try: main(args.attempt)
    except Exception:
        c.o.write_json(c.ROOT/c.spec()['out']/f'{args.attempt}_error.json',dict(error=traceback.format_exc(),time_utc=c.o.utc()))
        raise
