"""A shared frozen GRU body; only output expression and sampling policy vary."""
import numpy as np
import torch
import overnight_graph.core as old

ROOT = old.ROOT
CONFIG = ROOT / 'config/ootang_gru_ablation.v1_0.json'
SOURCES = ROOT / 'docs/ootang_gru_ablation_sources.v1.0.json'
B = 'BPLUS_CONTINUOUS'
BA = 'BPLUS_ORIGIN_ANCHOR'


def spec():
    return old.read_json(CONFIG)


def guard(implementation=True):
    files=old.read_json(SOURCES)['files']
    for name,digest in files.items():
        if old.sha(ROOT/name)!=digest:
            raise ValueError('Frozen source changed: '+name)
    if implementation:
        lock=old.read_json(ROOT/spec()['out']/'implementation_lock.json')
        for name,digest in lock['files'].items():
            if old.sha(ROOT/name)!=digest:
                raise ValueError('Implementation changed: '+name)
    return len(files)


def create_model(seed):
    return old.GraphGRU(old.spec(),seed,'GRU_GRAPH')


def schedule(n,seed,boundary,cfg):
    ms,hs=old.draw_schedule(n,seed,cfg)
    if boundary:
        rng=np.random.default_rng(np.random.SeedSequence([seed,*cfg['training']['auxiliary_seed_words']]))
        teachers=[t for t in cfg['training']['boundary_teacher_cycle'] if t<n]
        for step in range(cfg['updates']):
            ms[step,2]=teachers[(step+seed)%len(teachers)]
            tid=teachers[(step+seed+1)%len(teachers)]
            ms[step,3]=tid+rng.integers(cfg['training']['near_age_min'],cfg['training']['near_age_max']+1)
            for slot in [2,3]:
                maximum=min(cfg['training']['max_horizon'],n-int(ms[step,slot]))
                assert maximum>=7
                hs[step,slot,:7]=cfg['training']['fixed_short_horizons']
                hs[step,slot,7:]=rng.integers(1,maximum+1,9)
    assert np.all(ms[:,:,None]+hs-1<n)
    assert np.all(ms>=432) and np.all(hs>=1) and np.all(hs<=293)
    return ms,hs


def origin_residual(teachers,y,origins,current=False):
    origins=np.asarray(origins)
    if origins.min()<432 or origins.max()>len(y):
        raise ValueError('Illegal observation prefix')
    return np.array([y[m-1]-teachers[old.teacher_id(int(m),current)]['mean'][m-1] for m in origins])


def tensors(teachers,y,origins,horizons,sc,anchor,current=False):
    values=list(old.inputs(teachers,y,origins,horizons,sc,current))
    if anchor:
        mean,std=np.asarray(sc['mean']),np.asarray(sc['std'])
        reference=np.array([(old.own_physics(teachers[old.teacher_id(int(m),current)])[m-1]-mean[:,:10])/std[:,:10] for m in origins])
        values[2]=torch.cat([torch.from_numpy(reference[:,None]),values[2]],dim=1)
        values[3]=torch.cat([torch.zeros((len(origins),1,2),dtype=torch.float64),values[3]],dim=1)
    return tuple(values)


def learned(model,values,anchor):
    output=model(*values).sum(-1)
    return output[:,1:]-output[:,:1] if anchor else output


def target(cache,teachers,y,origins,horizons,sc,anchor):
    value=old.targets(cache,origins,horizons,sc,False).sum(-1)
    if anchor:
        r0=origin_residual(teachers,y,origins)/np.asarray(sc['unit'])
        value=value-torch.from_numpy(r0[:,None])
    return value


def predict(model,teachers,y,n,end,sc,anchor):
    hs=np.arange(1,end-n+1)[None]
    model.eval()
    with torch.no_grad():
        change=learned(model,tensors(teachers,y,[n],hs,sc,anchor,True),anchor).numpy()[0]*np.asarray(sc['unit'])
    baseline=teachers[old.teacher_id(n,True)]['mean'][n:end]
    r0=origin_residual(teachers,y,[n],True)[0]
    mean=baseline+change+(r0 if anchor else 0)
    if not np.isfinite(mean).all():raise ArithmeticError('Nonfinite forecast')
    return mean,change,r0


def reload(path):
    saved=torch.load(path,map_location='cpu',weights_only=True)
    model=create_model(saved['seed'])
    model.load_state_dict(saved['state_dict'],strict=True)
    return model,saved['scaling'],saved


def factorial_rows(frame,keys,metrics):
    result=[]
    names=['G00_RAW_UNIFORM','G10_ANCHOR_UNIFORM','G01_RAW_BOUNDARY','G11_ANCHOR_BOUNDARY']
    for group,values in frame[frame.method.isin(names)].groupby(keys,sort=False):
        if not isinstance(group,tuple):group=(group,)
        by=values.set_index('method')
        assert len(by)==4
        for metric in metrics:
            x00,x10,x01,x11=[float(by.loc[m,metric]) for m in names]
            result.append(dict(zip(keys,group),metric=metric,
                a_at_uniform=x10-x00,a_at_boundary=x11-x01,
                b_at_raw=x01-x00,b_at_anchor=x11-x10,
                a_main=.5*((x10-x00)+(x11-x01)),b_main=.5*((x01-x00)+(x11-x10)),
                interaction=x11-x10-x01+x00))
    return result
