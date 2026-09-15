"""Information boundaries, pairing, exact reference behavior and numerical checks."""
import argparse
import numpy as np
import pandas as pd
import torch
from . import core as c

o=c.old


def main(attempt):
    cfg=c.spec();o.setup(cfg);count=c.guard(False);root=c.ROOT/cfg['out'];out=root/attempt
    out.mkdir(exist_ok=False);o.check_deadline(cfg)
    bank=o.bank(cfg);y=o.labels(cfg,612,'preflight_612_only');sc=o.scaling(bank[612],y,cfg);cache=o.target_bank(bank,y)
    checks=[];coverage=[]
    def close(a,b,name,tol=1e-9):
        a,b=np.asarray(a),np.asarray(b)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),name
        d=float(abs(a-b).max()) if a.size else 0.
        assert d<=tol,(name,d)
        checks.append(dict(name=name,difference=d,values=int(a.size)))
    for n in cfg['origins']:
        for seed in cfg['seeds']:
            raw=c.schedule(n,seed,False,cfg);boundary=c.schedule(n,seed,True,cfg)
            original=o.load_npz(c.ROOT/cfg['prior_controls_out']/f'origin_{n}/GRU_GRAPH/seed_{seed}/schedule.npz')
            close(raw[0],original['origins'],'original_origin_schedule',0)
            close(raw[1],original['horizons'],'original_distance_schedule',0)
            close(raw[0][:,:2],boundary[0][:,:2],'retained_uniform_origins',0)
            close(raw[1][:,:2],boundary[1][:,:2],'retained_uniform_distances',0)
            for policy,(ms,hs) in [('uniform',raw),('boundary',boundary)]:
                assert ms.shape==(200,4) and hs.shape==(200,4,16)
                assert (ms[:,:,None]+hs-1<n).all()
                tid=np.array([[o.teacher_id(int(m)) for m in row] for row in ms]);age=ms-tid
                assert (age>=0).all()
                coverage.append(dict(origin=n,seed=seed,policy=policy,origin_draws=int(ms.size),query_draws=int(hs.size),fresh_origin_draws=int((age==0).sum()),fresh_queries=int(hs[age==0].size),fresh_h1=int((hs[age==0]==1).sum()),all_h1=int((hs==1).sum())))
            assert np.all(boundary[0][:,2]==np.array([t for t in [432,612,792] if t<n])[(np.arange(200)+seed)%sum(t<n for t in [432,612,792])])
            close(boundary[1][:,2:,:7],np.broadcast_to(np.arange(1,8),(200,2,7)),'fixed_short_queries',0)
    for seed in cfg['seeds']:
        models=[c.create_model(seed) for arm in cfg['arms']]
        assert all(sum(p.numel() for p in model.parameters())==1241 for model in models)
        for model in models[1:]:
            for name,p in model.state_dict().items():close(p,models[0].state_dict()[name],'paired_initialization',0)
    ms,hs=c.schedule(612,0,False,cfg);m,h=ms[0],hs[0]
    model=c.create_model(0)
    v=c.tensors(bank,y,m,h,sc,False)
    expected=o.inputs(bank,y,m,h,sc)
    for a,b in zip(v,expected):close(a,b,'raw_original_inputs',0)
    close(c.target(cache,bank,y,m,h,sc,False),o.targets(cache,m,h,sc).sum(-1),'raw_original_target',0)
    for anchor in [False,True]:
        pred,change,r0=c.predict(model,bank,y,612,905,sc,anchor)
        close(change,np.zeros((293,4)),'zero_learned_change',0)
        close(pred,bank[612]['mean'][612:905]+(r0 if anchor else 0),'zero_model_reference')
        # Inputs ignore every supplied observation beyond each queried origin.
        poisoned=np.vstack([y,np.full((100,4),1e9)])
        a=c.tensors(bank,y,[612],np.array([[1,7,293]]),sc,anchor,True)
        b=c.tensors(bank,poisoned,[612],np.array([[1,7,293]]),sc,anchor,True)
        for x,z in zip(a,b):close(x,z,'future_observation_poison',0)
    invalid=0
    for m_bad,h_bad in [([431],[[1]]),([613],[[1]]),([612],[[0]]),([612],[[294]])]:
        try:c.tensors(bank,y,m_bad,np.array(h_bad),sc,True,True)
        except (ValueError,AssertionError):invalid+=1
    try:c.target(cache,bank,y,[611],np.array([[2]]),sc,True)
    except (ValueError,AssertionError):invalid+=1
    assert invalid==5
    checks.append(dict(name='illegal_and_unmatured_queries_rejected',difference=0,values=invalid))
    # A nonzero network probes the reference, rather than relying on a zero head.
    with torch.no_grad():
        model.head.weight.copy_(torch.linspace(-.2,.3,model.head.weight.numel()).reshape_as(model.head.weight))
        model.head.bias.fill_(.1)
    values=c.tensors(bank,y,[612],np.array([[1,7,293]]),sc,True,True)
    duplicate=[z.clone() for z in values]
    duplicate[2][:,1]=duplicate[2][:,0];duplicate[3][:,1]=duplicate[3][:,0]
    output=c.learned(model,tuple(duplicate),True)
    close(output[:,:1].detach(),np.zeros((1,1,4)),'arbitrary_weight_h0_connection',0)
    r0=c.origin_residual(bank,y,[612],True)[0]
    close(bank[612]['mean'][611]+r0,y[611],'known_observation_connection')
    altered={k:{name:value.copy() for name,value in data.items()} for k,data in bank.items()}
    altered[612]['x'][612:]+=1000
    altered_values=c.tensors(altered,y,[612],np.array([[1,7,293]]),sc,True,True)
    close(altered_values[2][:,0],values[2][:,0],'reference_uses_last_known_physics',0)
    assert not np.allclose(altered_values[2][:,1:],values[2][:,1:])
    target=c.target(cache,bank,y,m,h,sc,True)
    direct=np.array([(y[mi+hi-1]-bank[o.teacher_id(int(mi))]['mean'][mi+hi-1]-c.origin_residual(bank,y,[mi])[0])/sc['unit'] for mi,hi in zip(m,h)])
    close(target,direct,'centered_target_identity')
    # Centered loss derivative includes both the future and the reference query.
    values=c.tensors(bank,y,m,h,sc,True)
    def objective():
        q=c.learned(model,values,True)
        return ((q-target)**2).mean()+(q*q).mean()
    loss=objective();loss.backward();grad=model.head.weight.grad.detach().clone()
    at=np.unravel_index(int(grad.abs().argmax()),tuple(grad.shape));analytic=float(grad[at]);initial=float(model.head.weight[at].detach());eps=1e-6
    assert abs(analytic)>1e-10
    with torch.no_grad():
        model.head.weight[at]=initial+eps;plus=float(objective())
        model.head.weight[at]=initial-eps;minus=float(objective());model.head.weight[at]=initial
    close([analytic],[(plus-minus)/(2*eps)],'centered_gradient',1e-8)
    model.zero_grad(set_to_none=True)
    # Rebuild the exact original nonzero raw loss and gradient.
    q=c.learned(model,c.tensors(bank,y,m,h,sc,False),False);t=c.target(cache,bank,y,m,h,sc,False)
    newloss=((q-t)**2).mean()+(q*q).mean();newloss.backward();newgrad=[p.grad.detach().clone() for p in model.parameters()]
    model.zero_grad(set_to_none=True)
    q0=model(*o.inputs(bank,y,m,h,sc)).sum(-1);t0=o.targets(cache,m,h,sc).sum(-1)
    oldloss=((q0-t0)**2).mean()+(q0*q0).mean();oldloss.backward()
    close(newloss.detach(),oldloss.detach(),'original_loss',0)
    for a,p in zip(newgrad,model.parameters()):close(a,p.grad,'original_gradients',0)
    for anchor in [False,True]:
        whole,_,_=c.predict(model,bank,y,612,905,sc,anchor)
        small=c.learned(model,c.tensors(bank,y,[612],np.array([[1,7,293]]),sc,anchor,True),anchor).detach().numpy()[0]*sc['unit']
        small+=bank[612]['mean'][np.array([612,618,904])]+(r0 if anchor else 0)
        close(small,whole[[0,6,292]],'query_batch_invariance')
        path=out/f'probe_anchor_{int(anchor)}.pt'
        torch.save(dict(state_dict=model.state_dict(),scaling=sc,seed=0,arm='probe',anchor=anchor),path)
        loaded,lsc,_=c.reload(path);replay,_,_=c.predict(loaded,bank,y,612,905,lsc,anchor)
        close(replay,whole,'model_reload',0)
    pd.DataFrame(coverage).to_csv(out/'sampling_coverage.csv',index=False)
    o.write_json(out/'checks.json',checks)
    o.write_json(out/'receipt.json',dict(status='passed',time_utc=o.utc(),source_entries=count,check_records=len(checks),checked_values=sum(x['values'] for x in checks),max_difference=max(x['difference'] for x in checks),parameters=1241,new_fits=0,optimizer_updates=0,physical_forwards=0,label_prefix_read=612))
    o.lock(root,'implementation_lock.json',[],status='frozen',note='Files below use repository-relative paths')
    files={str(p.relative_to(c.ROOT)):o.sha(p) for p in sorted((c.ROOT/'code/gru_ablation').glob('*.py'))}
    o.write_json(root/'implementation_lock.json',dict(time_utc=o.utc(),status='frozen',files=files,config_sha256=o.sha(c.CONFIG),preflight=str((out/'receipt.json').relative_to(c.ROOT))))
    o.event(root,'preflight_passed',checks=len(checks),source_entries=count)
    print(o.read_json(out/'receipt.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='preflight');args=parser.parse_args();main(args.attempt)
