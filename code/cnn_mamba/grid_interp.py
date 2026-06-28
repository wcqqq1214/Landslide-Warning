"""IDW interpolation from station displacement values to a regular grid."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
COORD_CSV = ROOT / "data" / "station_coords.csv"
GRID_H = 4
GRID_W = 7
IDW_POWER = 2.0
IDW_EPS = 1e-6


def load_coords(disp_cols=None):
    """Read station coordinates and optionally align them to displacement columns."""
    df = pd.read_csv(COORD_CSV)
    if disp_cols is not None:
        order = {c: i for i, c in enumerate(disp_cols)}
        df = df.sort_values("disp_col", key=lambda s: s.map(order)).reset_index(drop=True)
    names = df["station"].tolist()
    xy = df[["x_m", "y_m"]].values.astype(np.float64)
    return names, xy


def build_grid(xy, h=GRID_H, w=GRID_W, pad_frac=0.05):
    """Build an h x w grid over the station bounding box."""
    xmin, ymin = xy.min(0)
    xmax, ymax = xy.max(0)
    px = (xmax - xmin) * pad_frac + IDW_EPS
    py = (ymax - ymin) * pad_frac + IDW_EPS
    xs = np.linspace(xmin - px, xmax + px, w)
    ys = np.linspace(ymax + py, ymin - py, h)
    gx, gy = np.meshgrid(xs, ys)
    return gx, gy


def _idw_weights(xy, gx, gy, power=IDW_POWER):
    """Precompute IDW weights for all grid points."""
    gpts = np.column_stack([gx.ravel(), gy.ravel()])
    d = np.linalg.norm(gpts[:, None, :] - xy[None, :, :], axis=2)
    d = np.maximum(d, IDW_EPS)
    wts = 1.0 / (d ** power)
    wts /= wts.sum(axis=1, keepdims=True)
    return wts


def make_interpolator(xy, h=GRID_H, w=GRID_W, power=IDW_POWER):
    """Return a function that maps station values to grid values."""
    gx, gy = build_grid(xy, h, w)
    wts = _idw_weights(xy, gx, gy, power)

    def interp(values):
        """values: (T,N) 或 (N,) -> (T,h,w) 或 (h,w)。"""
        v = np.asarray(values, dtype=np.float64)
        single = v.ndim == 1
        if single:
            v = v[None, :]
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            grid = v @ wts.T
        grid = grid.reshape(-1, h, w)
        return grid[0] if single else grid

    return interp, (gx, gy)


if __name__ == "__main__":
    names, xy = load_coords()
    print(f"[grid_interp] 测点: {names}")
    print(f"[grid_interp] 坐标范围 x[{xy[:,0].min():.0f},{xy[:,0].max():.0f}] "
          f"y[{xy[:,1].min():.0f},{xy[:,1].max():.0f}] m")
    interp, (gx, gy) = make_interpolator(xy)
    test = np.arange(1, len(names) + 1, dtype=float)
    g = interp(test)
    print(f"[grid_interp] 网格形状: {g.shape}  (期望 {GRID_H}x{GRID_W})")
    gpts = np.column_stack([gx.ravel(), gy.ravel()])
    for i, nm in enumerate(names):
        j = np.argmin(np.linalg.norm(gpts - xy[i], axis=1))
        print(f"  {nm}: 真值 {test[i]:.1f}  最近网格插值 {g.ravel()[j]:.2f}")
