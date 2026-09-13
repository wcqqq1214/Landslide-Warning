"""Execute the explicit full-report amendment without changing frozen gates."""

import json
import traceback
from pathlib import Path

import numpy as np
import torch

from short_horizon.common import ROOT, CALLS, Recorder, save_json, sha, now
from short_horizon.data import training, query
from short_horizon.models import Scaling
from neural_initial_state.core import read_spec, guard, cache_for
from neural_initial_state.reference import OriginalResume
from neural_initial_state.run import arrays, train_once, development_gates, copy_selected
from neural_initial_state_continuation.run import lock_code, evaluate


def main():
    config = ROOT / 'config/ootang_neural_initial_state_later_report.v1_2.json'
    amendment = json.loads(config.read_text())
    for name, digest in amendment['source_sha256'].items():
        if sha(ROOT / name) != digest:
            raise ValueError('Amendment source changed: ' + name)
    spec = read_spec(ROOT / amendment['base_config'])
    guard(spec, 'training')
    if any(amendment[k] != spec[k] for k in ('deadline_utc', 'training_deadline_utc')):
        raise ValueError('Budget must remain unchanged')
    root = ROOT / spec['output_root']
    lock_code(spec, amendment['base_config'])
    selected = json.loads((root / 'internal_selection.json').read_text())
    development = json.loads((root / 'development/decision.json').read_text())
    if not selected['passed'] or selected['step'] != amendment['fixed_steps']:
        raise ValueError('Selected checkpoint differs')
    if not development['physics_pass'] or development['passed']:
        raise ValueError('Amendment requires the preserved development failure and physics pass')
    registry = json.loads((root / 'fit_registry.json').read_text())
    if len(registry) != 3 or any(r['phase'] != 'development' or r['status'] != 'completed' or r['updates'] != 200 for r in registry):
        raise ValueError('Unexpected prior fits')
    if spec['neural']['seeds'] != amendment['seeds'] or amendment['max_new_fits'] != 3 or amendment['max_new_updates'] != 600:
        raise ValueError('Unexpected training scope')
    phase = amendment['phase']
    out = root / phase
    out.mkdir(exist_ok=False)
    save_json(root / 'later_amendment_lock.json', {
        str(p.relative_to(ROOT)): sha(p) for p in (config, Path(__file__).resolve())
    })
    recorder = Recorder(root, spec, phase)
    torch.set_num_threads(spec['neural']['cpu_threads'])
    try:
        recorder.event('full_report_authorized', authority=amendment['authority'],
                       development_passed=False, fixed_steps=selected['step'])
        cache = cache_for(spec)
        start, end = spec['stages'][phase]
        tr = training(cache, spec, start)
        scaling = Scaling(tr)
        data = query(cache, np.arange(start, end))
        np.savez_compressed(out / 'training_queries.npz', origins=tr['origins'],
                            teacher=tr['teacher'], target_last=tr['origins'] + 6)
        train_once(tr, scaling, spec, phase, selected['step'], recorder)
        previous = arrays(root / 'development' / (spec['model'] + '.npz'))
        result = evaluate(spec, phase, cache, data, scaling, selected['step'], previous,
                          out / 'selected', OriginalResume(spec), recorder)
        copy_selected(out / 'selected', out, spec['model'])
        decision = development_gates(spec, result['metrics'], result['summary'])
        decision.update(physics_pass=result['physics_pass'], step=selected['step'], phase=phase,
                        exploratory=True, development_passed=False,
                        execution_policy='user authorized full later report after development failure',
                        later_reselection=False)
        save_json(out / 'decision.json', decision)
        save_json(out / 'completion.json', dict(status='completed', utc=now(), calls=CALLS.copy(), decision=decision))
        recorder.event('phase_completed', decision=decision, calls=CALLS.copy())
        print(json.dumps(decision, indent=2), flush=True)
    except Exception as error:
        save_json(root / 'later_exploratory_failure.json', dict(error=repr(error),
                  traceback=traceback.format_exc(), utc=now(), calls=CALLS.copy()))
        recorder.event('phase_failed', error=repr(error), calls=CALLS.copy())
        raise


if __name__ == '__main__':
    main()
