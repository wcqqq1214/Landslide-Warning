"""Read-only model replay and independent scale/score verification."""

import json
import traceback

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

from short_horizon.common import ROOT, CALLS, sha, array_sha, save_json, now
from short_horizon.data import training, query, observations
from short_horizon.models import Scaling
from neural_initial_state.core import read_spec, cache_for, InitialStateNet, predict, guard
from neural_initial_state.reference import OriginalResume
from neural_initial_state.feasibility import independent_one
from neural_initial_state.run import arrays, baselines


def close(a, b, name, atol=1e-9, rtol=1e-12):
    if not np.allclose(a, b, atol=atol, rtol=rtol, equal_nan=True):
        raise AssertionError(name + ': ' + str(np.nanmax(abs(np.asarray(a) - np.asarray(b)))))


def hashes(values):
    for name, digest in values.items():
        if sha(ROOT / name) != digest:
            raise AssertionError('Changed source: ' + name)


def independent_scales(spec, phase, pred, previous, labels):
    start, end = spec['stages'][phase]
    window = spec['calibration']['window']
    answer = np.full_like(pred['mean'], np.nan)
    sources = []
    for k in range(7):
        po = np.array([], int)
        pe = np.empty((0, 4))
        if previous is not None:
            valid = (previous['origins'] + k < start) & np.isfinite(previous['mean'][:, k]).all(1)
            po = previous['origins'][valid][-window:]
            pe = labels[po + k] - previous['mean'][valid, k][-window:]
        count = window - len(po)
        stop = min(start - k - 1, int(po[0]) - 1) if len(po) else start - k - 1
        do = np.arange(max(2, stop - count + 1), stop + 1) if count else np.array([], int)
        de = labels[do + k] - (labels[do - 1] + (k + 1) * (labels[do - 1] - labels[do - 2]))
        initial = np.concatenate([de, pe])
        assert len(initial) == window and (np.concatenate([do, po]) + k < start).all()
        sources.append(dict(horizon=k+1, previous_model_count=len(pe), drift_count=len(de),
                            model_origins=po.tolist(), drift_origins=do.tolist()))
        for i, origin in enumerate(pred['origins']):
            if origin + k >= end:
                continue
            # Directly collect all targets strictly before the current origin.
            matured = pred['origins'] + k < origin
            matured &= np.isfinite(pred['mean'][:, k]).all(1)
            q = pred['origins'][matured]
            errors = labels[q + k] - pred['mean'][matured, k]
            bank = np.concatenate([initial, errors])[-window:]
            answer[i, k] = np.maximum(np.sqrt(np.mean(bank ** 2, axis=0)), spec['calibration']['sigma_floor_mm'])
    return answer, sources


def independent_scores(spec, phase, predictions, common):
    start, end = spec['stages'][phase]
    labels = observations(spec, end)[0]
    rows = []
    for name, pred in predictions.items():
        for k in range(7):
            valid = pred['origins'] + (6 if common else k) < end
            mu, sd = pred['mean'][valid, k], pred['sigma'][valid, k]
            y = labels[pred['origins'][valid] + k]
            assert len(y) == end - start - (6 if common else k)
            assert np.isfinite(mu).all() and np.isfinite(sd).all() and (sd > 0).all()
            error = y - mu
            z = error / sd
            cs = sd * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi))
            for j, point in enumerate(spec['points']):
                row = dict(model=name, horizon=k+1, point=point, n=len(y),
                           mae=float(abs(error[:, j]).mean()), rmse=float(np.sqrt((error[:, j] ** 2).mean())),
                           crps=float(cs[:, j].mean()))
                for level in spec['calibration']['levels']:
                    alpha = 1 - level
                    q = norm.ppf(1 - alpha / 2)
                    low, high = mu[:, j] - q * sd[:, j], mu[:, j] + q * sd[:, j]
                    width = high - low
                    score = width + (2 / alpha) * (np.maximum(low-y[:, j], 0) + np.maximum(y[:, j]-high, 0))
                    row.update({f'coverage{round(100*level)}': float(((low<=y[:, j]) & (y[:, j]<=high)).mean()),
                                f'width{round(100*level)}': float(width.mean()),
                                f'interval_score{round(100*level)}': float(score.mean())})
                rows.append(row)
    frame = pd.DataFrame(rows)
    summaries = []
    numeric = [c for c in frame if c not in ('model','horizon','point','n')]
    for (name, h), group in frame.groupby(['model','horizon'], sort=False):
        summaries.append(dict(model=name, horizon=h, n_per_point=int(group.n.iloc[0]),
                              **{c: float(group[c].mean()) for c in numeric},
                              pooled_rmse=float(np.sqrt(np.average(group.rmse**2, weights=group.n)))))
    return frame, pd.DataFrame(summaries)


def frame_check(actual, saved, keys):
    actual = actual.set_index(keys).sort_index()
    saved = saved.set_index(keys).sort_index()
    assert actual.index.equals(saved.index) and set(actual.columns) == set(saved.columns)
    close(actual[saved.columns].to_numpy(float), saved.to_numpy(float), 'independent metrics', atol=1e-9)
    return len(saved)


def effect_gate(spec, frame, summary):
    cfg = spec['selection']
    rows = []
    for h in range(1, 8):
        s = summary[summary.horizon == h].set_index('model')
        f = frame[frame.horizon == h].set_index(['model', 'point'])
        a, b = s.loc[spec['model']], s.loc['B_ANCHOR']
        mean = {c: bool(a[c] <= (1-cfg['mean_relative_improvement'])*b[c]) for c in ('mae','rmse')}
        prob = {c: bool(a[c] <= (1-cfg['probability_relative_improvement'])*b[c]) for c in ('crps','interval_score90')}
        for p in spec['points']:
            x, y = f.loc[(spec['model'],p)], f.loc[('B_ANCHOR',p)]
            for c in ('mae','rmse'):
                mean[p+'_'+c] = bool(x[c] <= y[c]+cfg['point_mean_tolerance_mm'])
            for c in ('crps','interval_score90'):
                prob[p+'_'+c] = bool(x[c] <= (1+cfg['point_probability_max_regression'])*y[c]+cfg['point_mean_tolerance_mm'])
        prob['coverage_deviation'] = bool(abs(a.coverage90-.9) <= abs(b.coverage90-.9)+1/f.loc[(spec['model'],spec['points'][0]),'n'])
        rows.append(dict(horizon=h, mean_pass=all(mean.values()), probability_pass=all(prob.values()),
                         joint=all(mean.values()) and all(prob.values()),
                         average_rmse_no_worse_B=bool(a.rmse <= b.rmse+cfg['point_mean_tolerance_mm']),
                         mean_checks=mean, probability_checks=prob))
    count = sum(r['joint'] for r in rows)
    return dict(passed=all(r['average_rmse_no_worse_B'] for r in rows) and count >= cfg['development_joint_horizons_min'],
                joint_horizons=count, horizons=rows)


def main():
    spec = read_spec(ROOT / 'config/ootang_neural_initial_state.v1_1.json')
    root = ROOT / spec['output_root']
    out = root / 'verification'
    out.mkdir(exist_ok=True)
    receipt = dict(status='running', started_utc=now(), new_training=0)
    try:
        guard(spec)
        frozen = {str(p.relative_to(ROOT)): sha(p) for p in root.rglob('*') if p.is_file()
                  and 'verification' not in p.relative_to(root).parts and p.suffix != '.log'}
        hashes(json.loads((root / 'implementation_lock.json').read_text()))
        hashes(json.loads((root / 'later_amendment_lock.json').read_text()))
        amendment = json.loads((ROOT / 'config/ootang_neural_initial_state_later_report.v1_2.json').read_text())
        hashes(amendment['source_sha256'])
        old = ROOT / spec['resume_source_root']
        hashes(json.loads((old / 'implementation_lock.json').read_text()))
        legacy = json.loads((old / 'feasibility/receipt.json').read_text())
        assert legacy['passed'] and all(r['passed'] for r in legacy['gradient_checks'])
        registry = json.loads((root / 'fit_registry.json').read_text())
        assert [(r['phase'], r['seed'], r['updates'], r['status']) for r in registry] == [
            (phase, seed, 200, 'completed') for phase in ('development','later_exploratory') for seed in (0,1,2)]
        assert all(r['end_utc'] < spec['training_deadline_utc'] for r in registry)
        torch.set_num_threads(1)
        cache = cache_for(spec)
        labels = observations(spec)[0]
        reference = OriginalResume(spec)
        events = [json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
        totals = dict(trajectories=0, checkpoints=0, forecast_locks=0, point_metric_rows=0, summary_rows=0)
        summaries, audit_records, selections = [], [], []
        for phase, (start,end) in spec['stages'].items():
            data = query(cache, np.arange(start,end))
            tr = training(cache,spec,start)
            scaling = Scaling(tr)
            assert scaling.state == json.loads((root/phase/'training/scaling.json').read_text())
            queries = arrays(root/phase/'training_queries.npz')
            assert np.array_equal(queries['origins'], np.arange(252,start-6))
            assert np.array_equal(queries['teacher'], tr['teacher'])
            assert np.array_equal(queries['target_last'], queries['origins']+6)
            assert (queries['target_last'] < start).all() and (queries['teacher'] <= queries['origins']).all()
            for origin in data['origins']:
                assert str(cache['history_sha'][origin-252]) == array_sha(labels[:origin])
            previous_phase = {'inner':None,'development':'inner','later_exploratory':'development'}[phase]
            previous = arrays(root/previous_phase/(spec['model']+'.npz')) if previous_phase else None
            steps = spec['neural']['checkpoints'] if phase=='inner' else [200]
            for step in steps:
                folder = root/phase/(f'checkpoint_{step}' if phase=='inner' else 'selected')
                stored = arrays(folder/'seed_predictions.npz')
                pred = arrays(folder/(spec['model']+'.npz'))
                saved_audit = json.loads((folder/'physical_audit.json').read_text())
                assert saved_audit['all_seeds_pass']
                assert np.array_equal(pred['origins'],data['origins']) and np.array_equal(stored['origins'],data['origins'])
                max_state, max_mean, strict = 0., 0., []
                for seed in (0,1,2):
                    checkpoint = root/phase/'training'/f'seed_{seed}'/f'step_{step}.pt'
                    if phase=='inner':
                        assert sha(checkpoint)==sha(old/'inner/training'/f'seed_{seed}'/f'step_{step}.pt')
                    model = InitialStateNet()
                    model.load_state_dict(torch.load(checkpoint,map_location='cpu',weights_only=True))
                    mu, detail = predict(model,scaling,data,spec)
                    close(mu,stored['mean'][seed],'model mean reload',atol=0,rtol=0)
                    for key,value in detail.items():
                        close(value,stored[key][seed],'model state reload '+key,atol=0,rtol=0)
                    for i,origin in enumerate(data['origins']):
                        guard(spec)
                        initial = stored['initial'][seed,i]
                        base = data['state'][i,0]
                        radius = np.minimum(scaling.state_unit[:4],np.maximum(base[:4],0))
                        assert np.array_equal(initial[4:],base[4:]) and np.min(initial[:8]) >= -1e-8
                        assert np.all(abs(initial[:4]-base[:4]) <= radius+1e-12)
                        close(radius,stored['radius'][seed,i],'bound radius',atol=0,rtol=0)
                        exact,audit,_ = independent_one(reference,data,initial,i)
                        beta = data['coeff'][i,:4]
                        assert np.isfinite(exact).all() and np.isfinite(audit).all() and ((beta>0)&(beta<1)).all()
                        p = spec['physics']
                        assert audit[0]>=p['x_min'] and audit[1]>=p['gap_min'] and audit[2]<=p['normalized_complementarity_max']
                        daily = np.diff(np.vstack([initial[4:8],exact[:,4:8]]),axis=0).min()
                        assert daily >= p['plastic_daily_min_mm']
                        close(exact,stored['states'][seed,i],'independent C states',atol=p['replay_state_tolerance_original_units'],rtol=0)
                        projected = data['last_y'][i] + (exact[:,:4]+exact[:,20:]-(initial[:4]+initial[20:])) @ data['obs'].T
                        close(projected,mu[i],'independent C displacement',atol=p['replay_mean_tolerance_mm'],rtol=0)
                        max_state=max(max_state,float(abs(exact-stored['states'][seed,i]).max()))
                        max_mean=max(max_mean,float(abs(projected-mu[i]).max()))
                        saved = saved_audit['seeds'][seed]['trajectories'][i]
                        assert saved['origin']==origin and saved['numerical_physics_pass'] and saved['converted_substep_pass']
                        close([audit[0],audit[1],audit[2],audit[3],daily],
                              [saved[c] for c in ('minimum_x','minimum_gap','maximum_complementarity','minimum_substep_plastic_mm','minimum_daily_plastic_mm')], 'physical audit values',atol=1e-12)
                        close(p['x_min']/beta,saved['converted_substep_min_mm_by_coordinate'],'converted bounds',atol=0,rtol=0)
                        strict_pass = audit[3]>=p['strict_substep_diagnostic_min_mm']
                        assert strict_pass==saved['strict_substep_diagnostic_pass']
                        if not strict_pass:
                            strict.append(dict(seed=seed,origin=int(origin),minimum_substep_plastic_mm=float(audit[3])))
                    totals['trajectories']+=len(data['origins'])
                    totals['checkpoints']+=1
                ensemble = stored['mean'].mean(0)
                mask = pred['origins'][:,None]+np.arange(7) >= end
                assert np.isnan(pred['mean'][mask]).all() and np.isnan(pred['sigma'][mask]).all()
                close(pred['mean'][~mask],ensemble[~mask],'ensemble arithmetic mean',atol=0,rtol=0)
                discrepancy = np.zeros(7)
                for i in range(len(data['origins'])):
                    initial = stored['initial'][:,i].mean(0)
                    exact,_,_ = independent_one(reference,data,initial,i)
                    projected = data['last_y'][i]+(exact[:,:4]+exact[:,20:]-(initial[:4]+initial[20:])) @ data['obs'].T
                    discrepancy=np.maximum(discrepancy,abs(projected-ensemble[i]).max(1))
                interpretation=json.loads((folder/'ensemble_interpretation.json').read_text())
                close(discrepancy,interpretation['mean_initial_replay_difference_by_horizon_mm'],'ensemble diagnostic',atol=1e-10)
                assert interpretation['statistical_mean_only'] and not interpretation['used_as_replacement'] and not interpretation['interval_physical_feasibility_claim']
                sigma,sources=independent_scales(spec,phase,pred,previous,labels)
                close(sigma,pred['sigma'],'independent mature scale',atol=1e-12)
                assert sources==json.loads((folder/'calibration.json').read_text())['initialization']
                ev=[e for e in events if e['phase']==phase and e['event']=='forecast_locked' and e['group']==folder.name]
                assert len(ev)==len(pred['origins'])
                trajectory_event=[e for e in events if e['phase']==phase and e['event']=='all_seed_trajectories_locked' and e['path']==str(folder.relative_to(ROOT))]
                assert len(trajectory_event)==1 and trajectory_event[0]['sha256']==sha(folder/'seed_predictions.npz')
                assert trajectory_event[0]['time_utc'] < ev[0]['time_utc']
                for i,e in enumerate(ev):
                    n=int(pred['origins'][i])
                    valid=min(7,end-n)
                    digest=array_sha(np.stack([pred['mean'][i,:valid],pred['sigma'][i,:valid]]))
                    assert e['origin']==n and e['last_observed']==n-1
                    assert digest==pred['locks'][i]==e['prediction_sha256']
                    assert e['feature_history_sha256']==array_sha(labels[:n])
                totals['forecast_locks']+=len(ev)
                assert np.array_equal(pred['teacher_prefixes'],data['teacher'])
                predictions={**baselines(spec,phase),spec['model']:pred}
                for common in (False,True):
                    f,s=independent_scores(spec,phase,predictions,common)
                    suffix='_common' if common else ''
                    totals['point_metric_rows']+=frame_check(f,pd.read_csv(folder/f'metrics_by_point_horizon{suffix}.csv'),['model','horizon','point'])
                    totals['summary_rows']+=frame_check(s,pd.read_csv(folder/f'summary_by_horizon{suffix}.csv'),['model','horizon'])
                    if common:
                        index=s.set_index(['model','horizon'])
                        q=np.mean([.5*(index.loc[(spec['model'],h),'rmse']/max(index.loc[('DRIFT1',h),'rmse'],1e-8)+index.loc[(spec['model'],h),'crps']/max(index.loc[('DRIFT1',h),'crps'],1e-8)) for h in range(1,8)])
                        if phase=='inner':
                            selections.append(dict(step=step,q=float(q),physics_pass=True))
                    else:
                        if phase!='inner':
                            checked=effect_gate(spec,f,s)
                            saved=json.loads((root/phase/'decision.json').read_text())
                            assert all(checked[k]==saved[k] for k in checked)
                            summaries.append(dict(phase=phase,**checked))
                audit_records.append(dict(phase=phase,step=step,trajectories=3*(end-start),numerical_physics_pass=True,
                                          strict_diagnostic_failures=strict,maximum_state_difference=max_state,maximum_mean_difference_mm=max_mean,
                                          mean_initial_replay_difference_by_horizon_mm=discrepancy.tolist(),
                                          maximum_scale_difference_mm=float(np.nanmax(abs(sigma-pred['sigma'])))))
                print(json.dumps(audit_records[-1]),flush=True)
            selected_folder=root/phase/('checkpoint_200' if phase=='inner' else 'selected')
            for path in selected_folder.iterdir():
                if path.is_file():
                    assert sha(path)==sha(root/phase/path.name)
        internal=json.loads((root/'internal_selection.json').read_text())
        close([r['q'] for r in selections],[r['q'] for r in internal['checkpoints']],'internal Q',atol=1e-10)
        best=min(r['q'] for r in selections)
        choice=min(r['step'] for r in selections if r['q']<=best+spec['selection']['inner_tolerance'])
        assert choice==internal['step']==amendment['fixed_steps']==200
        assert internal['passed'] and best<selections[0]['q']-spec['selection']['inner_tolerance']
        hashes(frozen)
        assert CALLS['day_backward']==0 and CALLS['reference_forward']==0
        receipt.update(status='passed',completed_utc=now(),frozen_source_count=len(spec['source_sha256']),
                       preserved_output_files=len(frozen),new_fits=len(registry),new_updates=sum(r['updates'] for r in registry),
                       selected_steps=200,counts=totals,phase_checks=audit_records,effect_gates=summaries,
                       physics_pass=True,strict_diagnostic_failures=sum(len(r['strict_diagnostic_failures']) for r in audit_records),
                       gradient_receipt_reused=True,independent_new_gradient_check=False,
                       training_code_unchanged=True,old_stop_preserved=True,later_authorization_after_development=True,calls=CALLS.copy(),
                       input_sha256=frozen,verifier_sha256=sha(__file__))
        save_json(out/'receipt.json',receipt)
    except Exception as error:
        receipt.update(status='failed',completed_utc=now(),error=repr(error),traceback=traceback.format_exc(),calls=CALLS.copy())
        target=out/f'attempt_{len(list(out.glob("attempt_*.json")))+1}.json'
        save_json(target,receipt)
        raise


if __name__=='__main__':
    main()
