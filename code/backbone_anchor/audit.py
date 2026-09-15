"""Read-only independent replay of the frozen factorial experiment."""
import argparse
import json
import traceback

import numpy as np
import pandas as pd
import torch

from overnight_graph.audit import independent_inputs, raw
from .numpy_model import numpy_forward
from transformer_temporal.audit import independent_gate, independent_scores
from . import core as c

o = c.old


def expected_schedule(n, seed, boundary):
    rng = np.random.default_rng(seed)
    origins = rng.integers(432, n, size=(200, 4))
    horizons = np.array([[rng.integers(1, min(293, n-int(m))+1, 16)
                          for m in row] for row in origins])
    if boundary:
        rng = np.random.default_rng(np.random.SeedSequence([seed, 20260915, 1]))
        available = [t for t in (432, 612, 792) if t < n]
        for step in range(200):
            origins[step, 2] = available[(seed+step) % len(available)]
            origins[step, 3] = available[(seed+step+1) % len(available)] + rng.integers(1, 30)
            for slot in (2, 3):
                horizons[step, slot] = np.r_[np.arange(1, 8), rng.integers(
                    1, min(293, n-int(origins[step, slot]))+1, 9)]
    return origins, horizons


def main(attempt):
    cfg = c.spec(); o.setup(cfg)
    root = c.ROOT/cfg['out']; out = root/attempt
    out.mkdir(exist_ok=False)
    receipt = dict(status='running', started_utc=o.utc(), values_checked=0,
                   max_difference=0., checkpoints=0, original_checkpoints=0,
                   fits_verified=0, updates_verified=0, new_fits=0,
                   optimizer_updates=0, physical_forwards=0)
    records = []

    def close(name, actual, expected, tol=1e-8):
        actual, expected = np.asarray(actual, float), np.asarray(expected, float)
        assert actual.shape == expected.shape, (name, actual.shape, expected.shape)
        assert np.isfinite(actual).all() and np.isfinite(expected).all(), name
        diff = float(np.max(np.abs(actual-expected))) if actual.size else 0.
        assert diff <= tol, (name, diff, tol)
        receipt['values_checked'] += int(actual.size)
        receipt['max_difference'] = max(receipt['max_difference'], diff)
        records.append(dict(check=name, values=int(actual.size), max_difference=diff, tolerance=tol))

    try:
        receipt['source_files'] = c.guard()
        o.verify_lock(root/'training_complete.json'); o.verify_lock(root/'analysis_lock.json')
        # This is a read-only audit after every issued trajectory and score is locked.
        y = o.read_labels(c.ROOT/cfg['data'], 1461)
        forcing, dates = o.read_forcing(c.ROOT/cfg['data'], 1461)
        bank = o.bank(cfg); methods = cfg['controls']+cfg['historical_controls']+cfg['arms']
        saved_metrics = {}; saved_seeds = {}; allmeans = {}; allseeds = {}
        tables = {
            'phase_summary': ['origin', 'method'],
            'metrics_by_point': ['origin', 'method', 'point'],
            'seed_summary': ['origin', 'method', 'seed'],
            'seed_metrics_by_point': ['origin', 'method', 'seed', 'point'],
        }
        frames = {name: pd.read_csv(root/'analysis'/f'{name}.csv').set_index(keys)
                  for name, keys in tables.items()}
        daily = pd.read_csv(root/'analysis/daily_predictions.csv')
        endpoints = pd.read_csv(root/'analysis/endpoint_errors.csv', dtype={'seed':str})
        previous = None
        for n, end in zip(cfg['origins'], cfg['ends']):
            folder = root/f'origin_{n}'; o.verify_lock(folder/'issue_lock.json')
            teacher = bank[1168 if n == 1168 else max(t for t in (432,612,792) if t <= n)]
            np.testing.assert_array_equal(teacher['dates'][:end], dates[:end])
            close(f'{n}/forcing', teacher['forcing'][:end], forcing[:end], 0)
            sc = o.read_json(folder/'scaling.json'); ra = raw(teacher, y[:n])
            close(f'{n}/normalization_mean', sc['mean'], ra.mean(0), 1e-12)
            close(f'{n}/normalization_std', sc['std'], np.maximum(ra.std(0), 1e-6), 1e-12)
            close(f'{n}/unit', sc['unit'], np.maximum(y[:n].std(0), 1), 0)
            close(f'{n}/y0', sc['y0'], y[0], 0); assert sc['fit_prefix'] == n
            means = o.load_npz(folder/'means.npz'); seeds = o.load_npz(folder/'seeds.npz')
            assert set(means) == set(seeds) == set(methods)
            allmeans[n], allseeds[n] = means, seeds
            baseline = teacher['mean'][n:end]; r0 = y[n-1]-teacher['mean'][n-1]
            close(f'{n}/continuous', means[c.B], baseline, 0)
            close(f'{n}/anchored_control', means[c.BA], baseline+r0, 0)
            drift = y[n-1]+np.arange(1,294)[:,None]*(y[n-1]-y[n-2])
            close(f'{n}/drift', means['DRIFT1'], drift, 0)
            prior = c.ROOT/cfg['prior_controls_out']/f'origin_{n}'
            pm, ps = o.load_npz(prior/'means.npz'), o.load_npz(prior/'seeds.npz')
            for method in [c.B, 'DRIFT1', 'RR_COND', 'OLD_TRANSFORMER', 'OLD_HALF', 'OLD_REG1']:
                close(f'{n}/{method}/source_mean', means[method], pm[method], 0)
                close(f'{n}/{method}/source_seeds', seeds[method], ps[method], 0)
            original_gru=o.load_npz(c.ROOT/cfg['reuse_out']/f'origin_{n}/means.npz')
            original_gru_seeds=o.load_npz(c.ROOT/cfg['reuse_out']/f'origin_{n}/seeds.npz')
            close(f'{n}/historical_G11',means['G11_ANCHOR_BOUNDARY'],original_gru['G11_ANCHOR_BOUNDARY'],0)
            close(f'{n}/historical_G11_seeds',seeds['G11_ANCHOR_BOUNDARY'],original_gru_seeds['G11_ANCHOR_BOUNDARY'],0)
            close(f'{n}/anchor_seeds', seeds[c.BA], np.repeat(means[c.BA][None],3,0), 0)
            tensors = {a: independent_inputs(bank,y[:n],[n],np.arange(0 if a else 1,294)[None],sc,True)
                       for a in (False,True)}
            for arm in cfg['arms']:
                anchor, boundary = [cfg['factors'][arm][k] for k in ('anchor','boundary')]
                # Independent h=0 physical/zero-distance reference agrees with the new adapter.
                actual = c.tensors(bank,y[:n],[n],np.arange(1,294)[None],sc,anchor,True)
                for i,(a,b) in enumerate(zip(actual,tensors[anchor])):
                    close(f'{n}/{arm}/input_{i}', a.numpy(), b, 1e-9)
                outputs = []
                for seed in cfg['seeds']:
                    dest = c.checkpoint_folder(cfg,n,arm,seed); done = o.verify_lock(dest/'complete.json')
                    assert done['new_fits']==1 and done['updates']==200
                    receipt['fits_verified'] += 1; receipt['updates_verified'] += 200
                    sched = o.load_npz(dest/'schedule.npz'); ms,hs = expected_schedule(n,seed,boundary)
                    close(f'{n}/{arm}/{seed}/origins',sched['origins'],ms,0)
                    close(f'{n}/{arm}/{seed}/horizons',sched['horizons'],hs,0)
                    assert np.all(ms[:,:,None]+hs-1<n)
                    trace = [json.loads(v) for v in (dest/'training.jsonl').read_text().splitlines()]
                    assert [v['step'] for v in trace]==list(range(1,201))
                    close(f'{n}/{arm}/{seed}/loss', [v['loss'] for v in trace],
                          [v['mse']+v['penalty'] for v in trace], 0)
                    assert all(np.isfinite(v['grad_norm']) for v in trace)
                    for step in cfg['checkpoints']:
                        model, checkpoint_scale, saved = c.reload(dest/f'e{step}.pt')
                        assert checkpoint_scale==sc and saved['training_prefix']==n
                        assert saved['step']==step and saved['seed']==seed and saved['arm']==arm
                        assert saved['anchor']==anchor and saved['boundary']==boundary
                        expected_config=c.ROOT/'config/ootang_gru_ablation.v1_0.json' if arm in cfg['reuse_arms'] else c.CONFIG
                        assert saved['config_sha256']==o.sha(expected_config)
                        assert sum(p.numel() for p in model.parameters())==cfg['parameters'][cfg['factors'][arm]['backbone']]
                        saved_mean = np.load(dest/f'e{step}_mean.npy')
                        mu,change,_ = c.predict(model,bank,y[:n],n,end,sc,anchor)
                        close(f'{n}/{arm}/{seed}/{step}/reload',mu,saved_mean,0)
                        close(f'{n}/{arm}/{seed}/{step}/reload_change',change,np.load(dest/f'e{step}_learned_mm.npy'),0)
                        q,_ = numpy_forward(saved,tensors[anchor],np.zeros((294 if anchor else 293,4)))
                        independent_change = q[1:]-q[:1] if anchor else q
                        expected = baseline+independent_change+(r0 if anchor else 0)
                        close(f'{n}/{arm}/{seed}/{step}/numpy',expected,saved_mean,1e-7)
                        close(f'{n}/{arm}/{seed}/{step}/numpy_change',independent_change,change,1e-7)
                        if anchor:
                            close(f'{n}/{arm}/{seed}/{step}/h0', teacher['mean'][n-1]+r0+q[0]-q[0],y[n-1],1e-12)
                        if step==0:
                            close(f'{n}/{arm}/{seed}/zero_reference',saved_mean,baseline+(r0 if anchor else 0),0)
                            init = c.create_model(seed,arm).state_dict()
                            for k in saved['state_dict']:
                                close(f'{n}/{arm}/{seed}/init/{k}',saved['state_dict'][k].numpy(),init[k].numpy(),0)
                        if arm in cfg['reuse_arms']:
                            source=c.ROOT/cfg['reuse_out']/f'origin_{n}'/arm/f'seed_{seed}'
                            close(f'{n}/{arm}/{seed}/{step}/original',mu,np.load(source/f'e{step}_mean.npy'),0)
                            receipt['original_checkpoints'] += 1
                        if step==200:
                            poisoned = np.array(y); poisoned[n:] = 1e15
                            close(f'{n}/{arm}/{seed}/future_displacement_poison',c.predict(model,bank,poisoned,n,end,sc,anchor)[0],saved_mean,0)
                            outputs.append(saved_mean)
                        receipt['checkpoints'] += 1
                close(f'{n}/{arm}/seed_stack',seeds[arm],np.stack(outputs),0)
                close(f'{n}/{arm}/ensemble',means[arm],np.mean(outputs,axis=0),0)
            if previous is None:
                previous=n; continue
            sigmas=o.load_npz(folder/'sigmas.npz'); errors=o.load_npz(folder/'calibration_errors.npz')
            meta=o.read_json(folder/'calibration.json')
            assert meta==dict(previous_origin=previous,start=previous+90,end=previous+180,
                              matured_before=n,count=90,gap=n-previous-180)
            assert previous+180<=n
            for method in methods:
                es=y[previous+90:previous+180]-allmeans[previous][method][90:180]
                close(f'{n}/{method}/calibration_errors',errors[method],es,0)
                close(f'{n}/{method}/sigma',sigmas[method],np.maximum(np.sqrt(np.mean(es**2,0)),1e-6),0)
                score=independent_scores(y[n:end],means[method],sigmas[method]); saved_metrics[n,method]=score
                for metric,value in score.items():
                    close(f'{n}/{method}/point/{metric}',frames['metrics_by_point'].loc[(n,method)].loc[cfg['points'],metric],value)
                    close(f'{n}/{method}/summary/{metric}',frames['phase_summary'].loc[(n,method),metric],value.mean())
                assert np.all(frames['metrics_by_point'].loc[(n,method),'n']==293)
                for seed,mu in enumerate(seeds[method]):
                    scoreseed=independent_scores(y[n:end],mu,sigmas[method]); saved_seeds[n,method,seed]=scoreseed
                    for metric,value in scoreseed.items():
                        close(f'{n}/{method}/{seed}/point/{metric}',frames['seed_metrics_by_point'].loc[(n,method,seed)].loc[cfg['points'],metric],value)
                        close(f'{n}/{method}/{seed}/summary/{metric}',frames['seed_summary'].loc[(n,method,seed),metric],value.mean())
                for p,point in enumerate(cfg['points']):
                    part=daily[(daily.origin==n)&(daily.method==method)&(daily.point==point)].sort_values('distance')
                    assert len(part)==293 and (part.issue_date==dates[n-1]).all()
                    np.testing.assert_array_equal(part.date.to_numpy(), dates[n:end])
                    close(f'{n}/{method}/{point}/date_index',part.target_index,np.arange(n,end),0)
                    close(f'{n}/{method}/{point}/distance',part.distance,np.arange(1,294),0)
                    close(f'{n}/{method}/{point}/daily_truth',part.observed,y[n:end,p])
                    close(f'{n}/{method}/{point}/daily_mean',part['mean'],means[method][:,p])
                    close(f'{n}/{method}/{point}/daily_sigma',part.sigma,np.repeat(sigmas[method][p],293))
                for label,mu in [('ensemble',means[method])]+[(str(s),v) for s,v in enumerate(seeds[method])]:
                    for h in cfg['reporting']['boundary_diagnostic_horizons']:
                        part=endpoints[(endpoints.origin==n)&(endpoints.method==method)&(endpoints.seed==label)&(endpoints.horizon==h)].set_index('point').loc[cfg['points']]
                        e=mu[h-1]-y[n+h-1]
                        for key,value in [('error',e),('absolute_error',abs(e)),('squared_error',e*e)]:
                            close(f'{n}/{method}/{label}/{h}/{key}',part[key],value)
            previous=n
            print(f'origin {n}: independent checkpoints, scores and chronology passed',flush=True)
        # Paired samples and changes are compared within matching seeds, never pooled as independent data.
        for n in cfg['origins']:
            for seed in cfg['seeds']:
                for left,right in [(cfg['arms'][0],a) for a in cfg['arms'][1:]]:
                    a,b=[o.load_npz(c.checkpoint_folder(cfg,n,arm,seed)/'schedule.npz') for arm in (left,right)]
                    for key in a:close(f'{n}/{seed}/{left}/{right}/{key}',a[key],b[key],0)
        pairings=o.read_json(root/'analysis/pairing.json'); assert len(pairings)==60
        expected_edges={(n,a,b) for n in cfg['origins'][1:] for a,b in
            [(a,b) for a in cfg['arms'] for b in cfg['controls']]+[tuple(v) for v in cfg['reporting']['paired_edges']]}
        assert {(r['origin'],r['candidate'],r['reference']) for r in pairings}==expected_edges
        for row in pairings:
            n,a,b=row['origin'],row['candidate'],row['reference']
            result=independent_gate(saved_metrics[n,a],saved_metrics[n,b],cfg)
            for key,value in result.items():assert row[key]==value, (n,a,b,key)
            flags=[all(saved_seeds[n,a,s][k].mean()<saved_seeds[n,b,s][k].mean() for k in ('mae','rmse')) for s in cfg['seeds']]
            assert row['seed_flags']==flags and row['seed_both_improve']==sum(flags)
            assert row['seed_agreement_pass']==(sum(flags)>=2)
        factorials=[('factorial_summary','phase_summary'),('factorial_points','metrics_by_point'),
                    ('factorial_seeds','seed_summary'),('factorial_seed_points','seed_metrics_by_point')]
        factorial_rows=0
        for dest,source in factorials:
            frame=pd.read_csv(root/'analysis'/f'{dest}.csv'); keynames=[k for k in tables[source] if k!='method']
            expected_count=6*np.prod([3 if k in ('origin','seed') else 4 for k in keynames]); assert len(frame)==expected_count
            for _,row in frame.iterrows():
                key={k:row[k] for k in keynames}; metric=row['metric']
                numbers=[]
                for arm in cfg['arms']:
                    sourcekey=tuple(arm if k=='method' else key[k] for k in tables[source])
                    numbers.append(frames[source].loc[sourcekey,metric])
                v00,v10,v01,v11=numbers
                expected=dict(a_at_gru=v10-v00,a_at_tf=v11-v01,
                    tf_at_raw=v01-v00,tf_at_anchor=v11-v10,
                    a_main=(v10+v11-v00-v01)/2,network_main=(v01+v11-v00-v10)/2,
                    interaction=v11+v00-v10-v01)
                for k,v in expected.items():close(f'{dest}/{key}/{metric}/{k}',row[k],v)
            factorial_rows+=len(frame)
        events=[json.loads(v) for v in (root/'events.jsonl').read_text().splitlines()]
        started=[v for v in events if v['event']=='fit_started']; completed=[v for v in events if v['event']=='fit_completed']
        assert len(started)==len(completed)==24 and sum(v['updates'] for v in completed)==4800
        identities={(n,a,s) for n in cfg['origins'] for a in cfg['new_arms'] for s in cfg['seeds']}
        reused=[v for v in events if v['event']=='fit_reused']
        assert len(reused)==24 and {(v['origin'],v['arm'],v['seed']) for v in reused}=={(n,a,s) for n in cfg['origins'] for a in cfg['reuse_arms'] for s in cfg['seeds']}
        assert all(v['new_fits']==v['updates']==0 for v in reused)
        for items in (started,completed):assert {(v['origin'],v['arm'],v['seed']) for v in items}==identities
        issued=[i for i,v in enumerate(events) if v['event']=='trajectory_issued']
        assert [events[i]['origin'] for i in issued]==cfg['origins']
        for i,v in enumerate(events):
            if v['event']=='fit_started':
                prior_reads=[e for e in events[:i] if e['event']=='label_prefix_read' and e['purpose']=='fit_current_prefix_and_calibrate_previous_issued_errors']
                assert max(e['rows'] for e in prior_reads)<=v['origin']
            if v['event']=='label_prefix_read' and v['rows']==1461:assert i>max(issued)
        assert len([e for e in events if e['event']=='scoring_complete'])==1
        assert receipt['checkpoints']==192 and receipt['original_checkpoints']==96
        assert receipt['fits_verified']==48 and receipt['updates_verified']==9600
        assert len(daily)==42192 and len(endpoints)==1728
        for name,count in [('phase_summary',36),('metrics_by_point',144),('seed_summary',108),('seed_metrics_by_point',432)]:assert len(frames[name])==count
        c.guard(); o.verify_lock(root/'training_complete.json'); o.verify_lock(root/'analysis_lock.json')
        receipt.update(status='passed',completed_utc=o.utc(),check_records=len(records),events=len(events),
                       pairings=60,factorial_rows=factorial_rows,daily_rows=len(daily),endpoint_rows=len(endpoints),
                       experiment_new_fits=24,experiment_new_updates=4800,experiment_reused_fits=24,reused_historical_updates=4800,
                       experiment_training_seconds=sum(v['elapsed_seconds'] for v in completed),
                       score_source='independent Gaussian formulas and gate implementation; production scoring not called')
    except Exception:
        receipt.update(status='failed',completed_utc=o.utc(),error=traceback.format_exc())
        raise
    finally:
        o.write_json(out/'checks.json',records); o.write_json(out/'receipt.json',receipt)
    o.lock(out,'lock.json',[out/'checks.json',out/'receipt.json'],status='passed')
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--attempt',default='independent_audit')
    main(parser.parse_args().attempt)
