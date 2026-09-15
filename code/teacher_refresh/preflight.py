"""Pre-training provenance, chronology, routing and numerical checks."""
import argparse
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from physics_guided.reference import load
from physics_guided_diagnostics.core import bounded_jacobian, residual_vector
from tcn_conditional_trajectory.core import feature_matrix
from overnight_graph.audit import independent_inputs, numpy_forward
from . import core as c
from .teachers import objective_inputs, context_check

o = c.o


def main(output):
    cfg=c.spec();o.setup(cfg)
    root=c.ROOT/cfg['out'];out=root/output;out.mkdir(parents=True,exist_ok=False)
    records=[];count=0;maximum=0.

    def close(name,a,b,tol=1e-9):
        nonlocal count,maximum
        a,b=np.asarray(a),np.asarray(b)
        assert a.shape==b.shape,(name,a.shape,b.shape)
        assert np.isfinite(a).all() and np.isfinite(b).all(),name
        diff=float(np.max(abs(a-b))) if a.size else 0.
        assert diff<=tol,(name,diff,tol)
        count+=a.size;maximum=max(maximum,diff)
        records.append(dict(check=name,values=a.size,max_difference=diff,tolerance=tol))

    sources=c.guard(False)
    forcing,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    y=o.labels(cfg,612,'preflight_prefix_only')
    original=o.bank(cfg)
    for tid,t in original.items():
        assert int(t['teacher_fit_prefix'])==tid
        assert np.array_equal(t['dates'],dates[:len(t['dates'])])
        close(f'{tid}/forcing',t['forcing'],forcing[:len(t['forcing'])],0)
    teachers={'cached':original,'refreshed':{k:original[k] for k in (432,612,792)}}
    # Synthetic routing fixture only. Never saved or passed to the experiment.
    fixture={k:v.copy() if hasattr(v,'copy') else v for k,v in original[432].items()}
    fixture['teacher_fit_prefix']=np.array(492)
    fixture['mean']=fixture['mean']+np.arange(len(fixture['mean']))[:,None]*np.array([.01,-.005,.008,-.004])
    fixture['x']=feature_matrix(fixture['mean'],fixture['forcing'],fixture,fixture['y0'])
    teachers['refreshed'][492]=fixture
    sc=o.scaling(original[612],y,cfg)
    support=pd.read_csv(c.ROOT/'docs/ootang_teacher_refresh_support.v1.0.csv')
    for n in cfg['origins']:
        available=np.arange(432,n,60)
        close(f'{n}/grid',c.grid(n,cfg),available,0)
        r=support[support.outer_prefix==n]
        close(f'{n}/support',r.training_origin.to_numpy(),available,0)
        close(f'{n}/complete_support',r.complete_293.to_numpy().astype(int),(n-available>=293).astype(int),0)
        for s in cfg['seeds']:
            rng=np.random.default_rng(s)
            expected=available[rng.integers(0,len(available),size=(200,4))]
            eh=np.array([[rng.integers(1,min(293,n-int(m))+1,size=16) for m in row] for row in expected])
            ms,hs=c.schedule(n,s,cfg)
            close(f'{n}/{s}/origin_schedule',ms,expected,0)
            close(f'{n}/{s}/horizon_schedule',hs,eh,0)
            assert np.all(ms[...,None]+hs-1<n) and np.all(hs>0)
            assert all(c.cached_id(m)<=m for m in ms.ravel())
    hs=np.array([[1,60,120]])
    for arm in cfg['arms']:
        values=c.tensors(teachers,y,[492],hs,sc,arm)
        poisoned=y.copy();poisoned[492:]=1e12
        p=c.tensors(teachers,poisoned,[492],hs,sc,arm)
        for j,(a,b) in enumerate(zip(values,p)):
            close(f'{arm}/future_label_input_{j}',a.numpy(),b.numpy(),0)
        t=c.teacher_for(teachers,492,arm)
        expected=(y[492+hs[0]-1]-t['mean'][492+hs[0]-1]-(y[491]-t['mean'][491]))/sc['unit']
        close(f'{arm}/target',c.target(teachers,y,[492],hs,sc,arm).numpy()[0],expected,0)
        for s in cfg['seeds']:
            model=c.create_model(s)
            assert sum(p.numel() for p in model.parameters())==1241
            prior=c.ROOT/cfg['prior_controls_out']/f'origin_612/GRU_GRAPH/seed_{s}/e0.pt'
            init=torch.load(prior,map_location='cpu',weights_only=True)['state_dict']
            for k,v in model.state_dict().items():
                close(f'{arm}/{s}/initialization/{k}',v.numpy(),init[k].numpy(),0)
            mu,change,_=c.predict(model,teachers,y,612,905,sc)
            close(f'{arm}/{s}/zero_output',change,np.zeros((293,4)),0)
            close(f'{arm}/{s}/zero_fallback',mu,original[612]['mean'][612:905]+y[611]-original[612]['mean'][611],1e-12)
        model=c.create_model(0)
        with torch.no_grad():
            model.head.weight.normal_(std=.02)
        q=c.learned(model,values)
        target=c.target(teachers,y,[492],hs,sc,arm)
        loss=(q-target).square().mean()+q.square().mean();loss.backward()
        assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
        analytical=float(model.head.weight.grad[0,0]);eps=1e-6
        old=float(model.head.weight[0,0].detach());vals=[]
        with torch.no_grad():
            for delta in (eps,-eps):
                model.head.weight[0,0]=old+delta
                qq=c.learned(model,values)
                vals.append(float((qq-target).square().mean()+qq.square().mean()))
            model.head.weight[0,0]=old
        close(f'{arm}/head_gradient',analytical,(vals[0]-vals[1])/(2*eps),1e-8)
        saved=dict(state_dict=model.state_dict(),scaling=sc,seed=0,arm=arm,training_prefix=612)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'checkpoint.pt';torch.save(saved,path)
            loaded,_,_=c.reload(path)
            close(f'{arm}/reload',c.learned(loaded,values).detach().numpy(),q.detach().numpy(),0)
        npq,_=numpy_forward(saved,tuple(a.numpy() for a in values),np.zeros((4,4)))
        close(f'{arm}/numpy',npq[1:]-npq[:1],q.detach().numpy()[0]*sc['unit'],1e-7)
    cached=c.tensors(teachers,y,[492],hs,sc,'G_CACHED')
    legacy=c.previous.tensors(original,y,[492],hs,sc,True,False)
    for i,(a,b) in enumerate(zip(cached,legacy)):
        close(f'cached_legacy_adapter_{i}',a.numpy(),b.numpy(),0)
    ref=load(c.ROOT/cfg['runtime'])
    ctx,target=objective_inputs(ref,cfg,492,forcing[:492],y[:492])
    close('context_matrix_explicit_contraction',context_check(ref,ctx),0,1e-8)
    theta=original[432]['theta'];p,states=ref.forward(theta,ctx,states=True)
    close('physical_explicit_projection',p,np.einsum('ij,kj->ik',states['coordinates'],ctx.obs,optimize=False),1e-8)
    residual=residual_vector(p,target,49200)
    close('physical_objective',residual,np.r_[((p-target)/100).ravel(),np.sqrt(49200)*(p[-1]-target[-1])/100],1e-10)
    extended=ref.forward(theta,ref.Context(forcing[:612]))
    close('physical_prefix_isolation',extended[:492],p,1e-8)
    rejected=0
    for force,labels in ((forcing[:612],y),(forcing[:492],y)):
        try: objective_inputs(ref,cfg,492,force,labels)
        except ValueError: rejected+=1
    assert rejected==2
    synthetic=np.array([.2,.9999999]);upper=np.ones(2)
    def fn(x):return np.r_[x,x*x]
    j=bounded_jacobian(fn,synthetic,upper)
    h=np.array([2e-6,-2e-6]);expected=np.vstack([np.eye(2),np.diag(2*synthetic+h)])
    close('bounded_jacobian',j,expected,1e-9)
    illegal={'cached':original,'refreshed':{492:original[612]}}
    try: c.teacher_for(illegal,492,'G_REFRESH')
    except ValueError: pass
    else: raise AssertionError('Future teacher accepted')
    try: c.target(teachers,y,[492],np.array([[121]]),sc,'G_REFRESH')
    except ValueError: pass
    else: raise AssertionError('Unmatured target accepted')
    receipt=dict(status='passed',time_utc=o.utc(),source_files=sources,checks=len(records),values_checked=int(count),
        max_difference=maximum,new_neural_fits=0,new_bplus_fits=0,optimizer_updates=0,physical_forwards=2,
        max_label_prefix=612,synthetic_teacher='preflight fixture only; never saved or used in training')
    pd.DataFrame(records).to_csv(out/'checks.csv',index=False,float_format='%.17g')
    o.write_json(out/'receipt.json',receipt)
    print(receipt,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='preflight');args=parser.parse_args()
    main(args.output)
