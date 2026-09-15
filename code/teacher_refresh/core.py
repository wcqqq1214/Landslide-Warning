"""Shared network and samples; explicit cached/refreshed teacher routing."""
import numpy as np
import torch
from gru_ablation import core as previous

o = previous.old
ROOT = o.ROOT
CONFIG = ROOT / 'config/ootang_teacher_refresh.v1_0.json'
SOURCES = ROOT / 'docs/ootang_teacher_refresh_sources.v1.0.json'
B, BA = previous.B, previous.BA


def spec():
    return o.read_json(CONFIG)


def guard(implementation=True):
    files = o.read_json(SOURCES)['files']
    for path, digest in files.items():
        if o.sha(ROOT/path) != digest:
            raise ValueError('Frozen source changed: '+path)
    if implementation:
        for path, digest in o.read_json(ROOT/spec()['out']/'implementation_lock.json')['files'].items():
            if o.sha(ROOT/path) != digest:
                raise ValueError('Frozen implementation changed: '+path)
    return len(files)


def grid(n, cfg):
    return np.array([m for m in cfg['training']['grid_origins'] if m < n], dtype=np.int64)


def schedule(n, seed, cfg):
    rng = np.random.default_rng(seed)
    tc = cfg['training']
    ms = rng.choice(grid(n, cfg), size=(cfg['updates'], tc['batch_origins']))
    hs = np.array([[rng.integers(1, min(tc['max_horizon'], n-int(m))+1,
                                      size=tc['distances_per_origin']) for m in row] for row in ms])
    assert np.all(ms[..., None]+hs-1 < n)
    return ms, hs


def cached_id(m):
    return o.teacher_id(int(m), False)


def bank(cfg, outer_prefix=None):
    cached = o.bank(cfg)
    refreshed = {m: cached[m] for m in (432, 612, 792)}
    for m in cfg['teacher_update']['new_prefixes']:
        if outer_prefix is not None and m >= outer_prefix:
            continue
        path = ROOT/cfg['out']/f'teachers/fit_{m}/teacher.npz'
        if path.exists():
            o.verify_lock(path.parent/'complete.json')
            refreshed[m] = o.load_npz(path)
    return {'cached': cached, 'refreshed': refreshed}


def teacher_for(teachers, m, arm, current=False):
    m = int(m)
    if current:
        tid = 1168 if m == 1168 else cached_id(m)
        teacher = teachers['cached'][tid]
    elif arm == 'G_CACHED':
        tid = cached_id(m)
        teacher = teachers['cached'][tid]
    elif arm == 'G_REFRESH':
        tid = m
        teacher = teachers['refreshed'][tid]
    else:
        raise ValueError('Unregistered teacher policy')
    if int(teacher['teacher_fit_prefix']) != tid or tid > m:
        raise ValueError('Teacher fit extends beyond the query origin')
    return teacher


def tensors(teachers, y, origins, horizons, sc, arm, current=False):
    ms, hs = np.asarray(origins, np.int64), np.asarray(horizons, np.int64)
    if ms.ndim != 1 or hs.ndim != 2 or hs.shape[0] != len(ms):
        raise ValueError('Expected origin-by-distance queries')
    if ms.min() < 432 or ms.max() > len(y) or hs.min() < 1 or hs.max() > 293:
        raise ValueError('Illegal input prefix/distance')
    q = hs.shape[1]+1
    hist = np.zeros((len(ms), 4, int(ms.max()), 14), np.float64)
    future = np.zeros((len(ms), q, 4, 10), np.float64)
    av, sd = np.asarray(sc['mean']), np.asarray(sc['std'])
    for i, m in enumerate(ms):
        t = teacher_for(teachers, m, arm, current)
        ix = np.r_[m-1, m+hs[i]-1]
        if ix.max() >= len(t['mean']):
            raise ValueError('Teacher cache does not cover query')
        hist[i, :, :m] = ((o.history_raw(t, y[:m])-av)/sd).transpose(1, 0, 2)
        future[i] = (o.own_physics(t)[ix]-av[:, :10])/sd[:, :10]
    with_zero = np.concatenate([np.zeros((len(ms), 1), np.int64), hs], axis=1)
    distance = np.stack([with_zero/293, np.log1p(with_zero)/np.log(294)], axis=-1)
    return tuple(torch.from_numpy(a) for a in (hist, ms, future, distance))


def target(teachers, y, ms, hs, sc, arm):
    answer = []
    for m, distances in zip(ms, hs):
        ix = m+distances-1
        if ix.max() >= len(y) or m >= len(y):
            raise ValueError('Unmatured target')
        t = teacher_for(teachers, m, arm)
        r0 = y[m-1]-t['mean'][m-1]
        answer.append((y[ix]-t['mean'][ix]-r0)/sc['unit'])
    return torch.from_numpy(np.array(answer))


def create_model(seed):
    return previous.create_model(seed)


def learned(model, values):
    return previous.learned(model, values, True)


def predict(model, teachers, y, n, end, sc):
    t = teacher_for(teachers, n, 'G_CACHED', True)
    hs = np.arange(1, end-n+1)[None]
    model.eval()
    with torch.no_grad():
        q = learned(model, tensors(teachers, y, [n], hs, sc, 'G_CACHED', True)).numpy()[0]
    change = q*np.asarray(sc['unit'])
    r0 = y[n-1]-t['mean'][n-1]
    mean = t['mean'][n:end]+r0+change
    if not np.isfinite(mean).all():
        raise ArithmeticError('Nonfinite issued prediction')
    return mean, change, r0


def reload(path):
    saved = torch.load(path, map_location='cpu', weights_only=True)
    model = create_model(saved['seed'])
    model.load_state_dict(saved['state_dict'], strict=True)
    return model, saved['scaling'], saved
