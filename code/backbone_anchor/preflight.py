"""No-fit validation: exact GRU reuse and Transformer information boundaries."""
import argparse
import copy
import traceback
import numpy as np
import torch
from . import core as c

o = c.old


def main(attempt):
    cfg=c.spec();o.setup(cfg);sources=c.guard(False);root=c.ROOT/cfg['out'];out=root/attempt
    out.mkdir(exist_ok=False);checks=[]
    receipt=dict(status='running',new_fits=0,optimizer_updates=0,physical_forwards=0,source_files=sources,started_utc=o.utc())
    def close(a,b,name,tol=1e-9):
        a,b=np.asarray(a),np.asarray(b)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),name
        d=float(abs(a-b).max()) if a.size else 0.
        assert d<=tol,(name,d,tol)
        checks.append(dict(name=name,difference=d,values=int(a.size),tolerance=tol))
    try:
        o.check_deadline(cfg);bank=o.bank(cfg);reused=0
        for n,end in zip(cfg['origins'],cfg['ends']):
            y=o.labels(cfg,n,'reuse_preflight_only');sc=o.scaling(bank[o.teacher_id(n,True)],y,cfg)
            assert sc==o.read_json(c.ROOT/cfg['reuse_out']/f'origin_{n}/scaling.json')
            for seed in cfg['seeds']:
                ms,hs=c.schedule(n,seed,False,cfg)
                assert np.all(ms[:,:,None]+hs-1<n)
                assert all(o.teacher_id(int(m))<=m for m in ms.ravel())
                for arm in cfg['reuse_arms']:
                    source=c.checkpoint_folder(cfg,n,arm,seed);o.verify_lock(source/'complete.json')
                    sch=o.load_npz(source/'schedule.npz')
                    close(ms,sch['origins'],f'{n}/{arm}/{seed}/paired_origins',0)
                    close(hs,sch['horizons'],f'{n}/{arm}/{seed}/paired_distances',0)
                    for step in cfg['checkpoints']:
                        model,scale,saved=c.reload(source/f'e{step}.pt')
                        assert saved['boundary'] is False and scale==sc
                        mu,change,_=c.predict(model,bank,y,n,end,sc,cfg['factors'][arm]['anchor'])
                        close(mu,np.load(source/f'e{step}_mean.npy'),f'{n}/{arm}/{seed}/{step}/old_gru_forecast',0)
                        close(change,np.load(source/f'e{step}_learned_mm.npy'),f'{n}/{arm}/{seed}/{step}/old_gru_change',0)
                        reused+=1
            print(f'preflight reused GRU origin {n}: all24 checkpoints exact',flush=True)
        y=o.labels(cfg,612,'new_model_preflight_only_612');sc=o.scaling(bank[612],y,cfg)
        cache=o.target_bank(bank,y)
        for seed in cfg['seeds']:
            models={a:c.create_model(seed,a) for a in cfg['arms']}
            for arm,model in models.items():
                assert sum(p.numel() for p in model.parameters())==cfg['parameters'][cfg['factors'][arm]['backbone']]
            for left,right in [(cfg['arms'][0],cfg['arms'][1]),(cfg['arms'][2],cfg['arms'][3])]:
                for k,v in models[left].state_dict().items():close(v.numpy(),models[right].state_dict()[k].numpy(),f'{seed}/same_pair_initial/{k}',0)
            gs,ts=models[cfg['arms'][0]].state_dict(),models[cfg['arms'][2]].state_dict()
            for k in gs:
                if not k.startswith('gru.'):
                    close(gs[k].numpy(),ts[k].numpy(),f'{seed}/shared_initial/{k}',0)
            for arm in cfg['new_arms']:
                model=models[arm];anchor=cfg['factors'][arm]['anchor']
                mu,change,r0=c.predict(model,bank,y,612,905,sc,anchor)
                close(mu,bank[612]['mean'][612:905]+(r0 if anchor else 0),f'{seed}/{arm}/zero_baseline',0)
                close(change,np.zeros((293,4)),f'{seed}/{arm}/zero_change',0)
                with torch.no_grad():model.head.weight.fill_(.013)
                mu,_,_=c.predict(model,bank,y,612,905,sc,anchor)
                poison=np.concatenate([y,np.full((293,4),1e12)])
                close(mu,c.predict(model,bank,poison,612,905,sc,anchor)[0],f'{seed}/{arm}/future_label_isolation',0)
                ms=np.array([432,500]);hs=np.tile(np.arange(1,17),(2,1))
                values=c.tensors(bank,y,ms,hs,sc,anchor)
                actual=c.learned(model,values,anchor)
                truth=np.array([y[m+h-1]-bank[o.teacher_id(int(m))]['mean'][m+h-1] for m,h in zip(ms,hs)])
                if anchor:truth-=c.origin_residual(bank,y,ms)[:,None]
                truth/=np.asarray(sc['unit'])
                close(c.target(cache,bank,y,ms,hs,sc,anchor).numpy(),truth,f'{seed}/{arm}/independent_target',1e-12)
                loss=(actual-torch.from_numpy(truth)).square().mean()+actual.square().mean()
                loss.backward()
                assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
                assert sum(p.grad.abs().sum().item() for p in model.transformer.parameters())>0
                altered=list(values);altered[0]=values[0].clone();altered[0][0,:,432:]=1e10
                with torch.no_grad():close(actual.detach().numpy(),c.learned(model,tuple(altered),anchor).numpy(),f'{seed}/{arm}/padding_isolation',0)
                single=c.tensors(bank,y,[432],hs[:1],sc,anchor)
                with torch.no_grad():close(actual[0].detach().numpy(),c.learned(model,single,anchor)[0].numpy(),f'{seed}/{arm}/batch_padding_equivalence',1e-12)
                if anchor:
                    q=model(*values).sum(-1).detach().numpy()
                    close(q[:,:1]-q[:,:1],np.zeros((2,1,4)),f'{seed}/{arm}/h0_change',0)
                    b0=np.array([bank[o.teacher_id(int(m))]['mean'][m-1] for m in ms])
                    close(b0+c.origin_residual(bank,y,ms),y[ms-1],f'{seed}/{arm}/h0_anchor',1e-12)
                rejected=False
                try:c.target(cache,bank,y,[611],np.array([[2]]),sc,anchor)
                except ValueError:rejected=True
                assert rejected,'unmatured target must be rejected'
                path=out/f'{arm}_seed_{seed}.pt'
                torch.save(dict(state_dict=model.state_dict(),seed=seed,arm=arm,scaling=sc),path)
                loaded,_,_=c.reload(path)
                close(c.predict(loaded,bank,y,612,905,sc,anchor)[0],mu,f'{seed}/{arm}/reload',0)
            # Independent dense causal forward and its parameter/input gradients.
            torch.manual_seed(seed+97)
            fast=models[cfg['new_arms'][0]].transformer
            dense=copy.deepcopy(fast)
            x=torch.randn(8,11,14,dtype=torch.float64,requires_grad=True)
            xd=x.detach().clone().requires_grad_(True);lengths=torch.tensor([5,5,5,5,11,11,11,11])
            fast.zero_grad();dense.zero_grad()
            a=fast(x,lengths);b=dense.dense_reference(xd,lengths)
            close(a.detach().numpy(),b.detach().numpy(),f'{seed}/dense_causal_forward',1e-12)
            w=torch.randn_like(a);(a*w).sum().backward();(b*w).sum().backward()
            close(x.grad.numpy(),xd.grad.numpy(),f'{seed}/dense_causal_input_gradient',1e-11)
            for (name,p),(other,q) in zip(fast.named_parameters(),dense.named_parameters()):
                assert name==other
                close(p.grad.numpy(),q.grad.numpy(),f'{seed}/dense_causal_parameter_gradient/{name}',1e-11)
            # Common decoding must be identical even for nonzero shared readout.
            gm,tm=models[cfg['arms'][0]],models[cfg['arms'][2]]
            with torch.no_grad():gm.head.weight.copy_(tm.head.weight)
            values=c.tensors(bank,y,[432],np.array([[1,7,293]]),sc,False)
            state=torch.randn(1,4,8,dtype=torch.float64)
            with torch.no_grad():
                close(gm.decode_state(*values,state).numpy(),tm.decode_state(*values,state).numpy(),f'{seed}/common_decoder_identity',0)
        assert reused==96
        c.guard(False)
        receipt.update(status='passed',completed_utc=o.utc(),reused_checkpoints=reused,check_records=len(checks),values_checked=sum(v['values'] for v in checks),max_difference=max(v['difference'] for v in checks),parameters=cfg['parameters'])
    except Exception:
        receipt.update(status='failed',completed_utc=o.utc(),error=traceback.format_exc());raise
    finally:
        o.write_json(out/'checks.json',checks);o.write_json(out/'receipt.json',receipt)
    o.lock(out,'lock.json',list(out.glob('*')),status='passed')
    print(receipt,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attempt',default='preflight');main(p.parse_args().attempt)
