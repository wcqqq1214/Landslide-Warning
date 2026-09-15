"""Frozen shared inputs/decoder and a single-layer historical Transformer."""
import math
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
import overnight_graph.core as old
from gru_ablation.core import origin_residual, tensors, learned, target, predict

ROOT = old.ROOT
CONFIG = ROOT / 'config/ootang_backbone_anchor.v1_0.json'
SOURCES = ROOT / 'docs/ootang_backbone_anchor_sources.v1.0.json'
B = 'BPLUS_CONTINUOUS'
BA = 'BPLUS_ORIGIN_ANCHOR'


def spec():
    return old.read_json(CONFIG)


def guard(implementation=True):
    files = old.read_json(SOURCES)['files']
    for name, digest in files.items():
        if old.sha(ROOT / name) != digest:
            raise ValueError('Frozen source changed: ' + name)
    if implementation:
        for name, digest in old.read_json(ROOT / spec()['out'] / 'implementation_lock.json')['files'].items():
            if old.sha(ROOT / name) != digest:
                raise ValueError('Implementation changed: ' + name)
    return len(files)


def position(length, dtype, device):
    days = torch.arange(length, dtype=dtype, device=device)[:, None]
    rates = torch.exp(torch.arange(0, 8, 2, dtype=dtype, device=device) * (-math.log(10000.) / 8))
    pe = torch.zeros((length, 8), dtype=dtype, device=device)
    pe[:, 0::2] = torch.sin(days * rates)
    pe[:, 1::2] = torch.cos(days * rates)
    return pe


class HistoryTransformer(nn.Module):
    """Last-token evaluation equals a full one-layer causal pre-norm block."""
    def __init__(self):
        super().__init__()
        self.project = nn.Linear(14, 8)
        self.norm1 = nn.LayerNorm(8, eps=1e-5)
        self.qkv = nn.Linear(8, 24)
        self.out = nn.Linear(8, 8)
        self.norm2 = nn.LayerNorm(8, eps=1e-5)
        self.ff1 = nn.Linear(8, 8)
        self.ff2 = nn.Linear(8, 8)

    def forward(self, sequence, lengths):
        b, t, _ = sequence.shape
        x = self.project(sequence) + position(t, sequence.dtype, sequence.device)
        q, k, v = self.qkv(self.norm1(x)).chunk(3, dim=-1)
        index = torch.arange(b, device=x.device)
        q = q[index, lengths - 1].reshape(b, 2, 1, 4)
        k, v = [z.reshape(b, t, 2, 4).transpose(1, 2) for z in (k, v)]
        scores = (q @ k.transpose(-1, -2)) / 2.
        mask = torch.arange(t, device=x.device)[None, :] >= lengths[:, None]
        weights = torch.softmax(scores.masked_fill(mask[:, None, None], -torch.inf), dim=-1)
        attended = (weights @ v).reshape(b, 8)
        h = x[index, lengths - 1] + self.out(attended)
        return h + self.ff2(F.gelu(self.ff1(self.norm2(h))))

    def dense_reference(self, sequence, lengths):
        """Preflight-only quadratic causal implementation; never used for fitting."""
        b, t, _ = sequence.shape
        x = self.project(sequence) + position(t, sequence.dtype, sequence.device)
        q, k, v = [z.reshape(b, t, 2, 4).transpose(1, 2)
                   for z in self.qkv(self.norm1(x)).chunk(3, dim=-1)]
        scores = (q @ k.transpose(-1, -2)) / 2.
        future = torch.triu(torch.ones((t, t), dtype=torch.bool, device=x.device), diagonal=1)
        padding = torch.arange(t, device=x.device)[None, :] >= lengths[:, None]
        weights = torch.softmax(scores.masked_fill(future[None, None] | padding[:, None, None], -torch.inf), dim=-1)
        attended = (weights @ v).transpose(1, 2).reshape(b, t, 8)
        h = x + self.out(attended)
        h = h + self.ff2(F.gelu(self.ff1(self.norm2(h))))
        return h[torch.arange(b, device=x.device), lengths - 1]


class TemporalForecaster(old.GraphGRU):
    def __init__(self, seed, backbone):
        super().__init__(old.spec(), seed, 'GRU_GRAPH')
        self.backbone = backbone
        if backbone == 'TF':
            del self.gru
            self.transformer = HistoryTransformer().double()
        elif backbone != 'GRU':
            raise ValueError(backbone)

    def encode(self, hist, lengths):
        b, p, t, _ = hist.shape
        sequence = hist.reshape(b * p, t, 14)
        lens = lengths.repeat_interleave(p)
        if self.backbone == 'GRU':
            states, _ = self.gru(sequence)
            return states[torch.arange(b * p), lens - 1].reshape(b, p, 8)
        return self.transformer(sequence, lens).reshape(b, p, 8)

    def decode_state(self, hist, lengths, future, distances, h):
        # This expression and module names are identical to frozen GraphGRU.
        b = len(hist)
        context = torch.tanh(self.message(torch.einsum('pq,bqk->bpk', self.adjacency, h)))
        last = hist[torch.arange(b)[:, None], torch.arange(4)[None, :],
                    (lengths - 1)[:, None]][..., [11, 12]]
        queries = future.shape[1]
        z = torch.cat([h[:, None].expand(-1, queries, -1, -1),
                       context[:, None].expand(-1, queries, -1, -1), future,
                       last[:, None].expand(-1, queries, -1, -1),
                       distances[:, :, None].expand(-1, -1, 4, -1),
                       self.point(torch.arange(4))[None, None].expand(b, queries, -1, -1)], dim=-1)
        return self.head(F.gelu(self.decode(z)))

    def forward(self, hist, lengths, future, distances):
        return self.decode_state(hist, lengths, future, distances, self.encode(hist, lengths))


def create_model(seed, arm):
    return TemporalForecaster(seed, spec()['factors'][arm]['backbone'])


def schedule(n, seed, boundary, cfg):
    assert not boundary
    ms, hs = old.draw_schedule(n, seed, cfg)
    assert np.all(ms[:, :, None] + hs - 1 < n)
    return ms, hs


def reload(path):
    saved = torch.load(path, map_location='cpu', weights_only=True)
    model = create_model(saved['seed'], saved['arm'])
    model.load_state_dict(saved['state_dict'], strict=True)
    return model, saved['scaling'], saved


def checkpoint_folder(cfg, n, arm, seed):
    base = cfg['reuse_out'] if arm in cfg['reuse_arms'] else cfg['out']
    return ROOT / base / f'origin_{n}' / arm / f'seed_{seed}'


def factorial_rows(frame, keys, metrics):
    result = []
    names = spec()['arms']
    for group, values in frame[frame.method.isin(names)].groupby(keys, sort=False):
        if not isinstance(group, tuple):
            group = (group,)
        by = values.set_index('method')
        assert len(by) == 4
        for metric in metrics:
            g0, g1, t0, t1 = [float(by.loc[m, metric]) for m in names]
            result.append(dict(zip(keys, group), metric=metric,
                               a_at_gru=g1-g0, a_at_tf=t1-t0,
                               tf_at_raw=t0-g0, tf_at_anchor=t1-g1,
                               a_main=.5*((g1-g0)+(t1-t0)),
                               network_main=.5*((t0-g0)+(t1-g1)),
                               interaction=t1-t0-g1+g0))
    return result
