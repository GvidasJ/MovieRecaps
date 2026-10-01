"""Exact phase solve (prompt Stage 6, DESIGN.md §2.1 / §5 phase_solve.py). Pure math, no I/O.

After Effects shows, at comp frame ``k`` of a stretch-mode layer, the RAW frame

    floor(raw_fps * (raw_in + v * (t_k - t_in)) + 1e-9),      t_k = k / comp_fps, t_in = comp_in / comp_fps.

Every matched competitor frame ``k`` whose RAW frame is known to lie in ``[lo_k, hi_k]`` (inclusive; a
soft / ambiguous range, ``lo_k == hi_k`` for a unique match) therefore constrains ``(raw_in, v)``.
Everything here works in LOCAL FRAME UNITS, which is well conditioned for any RAW offset:

    base = min(lo)                      (an integer RAW frame)
    x    = raw_fps * raw_in - base      (RAW frames)
    u    = v * raw_fps / comp_fps       (RAW frames per comp frame)
    d_k  = k - comp_in

Frame ``k`` is consistent with ``(x, u)`` when (closed and tolerant, tau = 1e-6 frame)

    lo_k - base - tau  <=  x + u * d_k  <=  hi_k - base + 1 + tau.

The tolerance makes exact timing ties (ffmpeg ``setpts=PTS/1.1`` resolving an exact .5 either way,
editing apps rounding in floating point) feasible instead of splitting segments; frames whose
Chebyshev slack is below ``TIE_SLACK`` (1e-4 frame) are reported as *timing-tie* frames: they may differ
by one frame from the AE rule and are listed like ambiguous-identical frames.

AE floor-rule safety (DESIGN §2.1, FX-10). Frame d of a layer changes its RAW frame where x + u d crosses an
integer, i.e. at the BREAKPOINTS x = n - u d (every integer n). They are 1-periodic in x and cut the feasible
interval into CELLS; every x inside one cell shows exactly the same RAW frame on every frame of the layer.
The SLACK of a placement is the distance of x to the nearest breakpoint of ANY frame of the layer (binding or
not) = min over frames of the distance of x + u d to the nearest integer. raw_in is therefore placed at the
midpoint of the cell that maximises that minimum (``place_in_cells``) instead of the interval centre (which
can coincide with a breakpoint of a non-binding frame). For rational rates the breakpoints live on a lattice:
24000/1001 in 30 (u = 800/1001) has cells of 4/1001 frame around every 'cadence slip' (a 5-frame window with
3 RAW advances); exact frames across a slip PIN raw_in to that cell (+-2/1001 frame = +-0.083 ms) -- maximal
information, not weak evidence. ``exact_min_slack`` evaluates the slack with Fractions of the values
actually written (9-decimal raw_in, AE's startTime / stretch).
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

__all__ = [
    "TAU", "TIE_SLACK", "feasible_speed_range", "is_feasible", "solve_raw_in", "solve_shared_raw_in", "ae_frame",
    "snap_speed",
    "estimate_speed", "dominant_speed", "chebyshev_x", "best_subinterval", "min_penalty", "prefer_penalties",
]

TAU = 1e-6          # constraint tolerance (RAW frames)
TIE_SLACK = 1e-4    # Chebyshev slack below which a frame is a timing-tie frame (RAW frames)
_AE_EPS = 1e-9      # the AE floor rule's epsilon (DESIGN §2.1)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------

def _ratio(comp_fps: Any, raw_fps: Any) -> float:
    """u / v = raw_fps / comp_fps as a float (computed exactly from the Fractions first)."""
    return float(Fraction(raw_fps) / Fraction(comp_fps))


def _prep(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int] | None, comp_in: int
          ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Validate the constraint arrays; return (d, lo_rel, hi_rel, base) as float64/int."""
    ks_a = np.asarray(ks, dtype=np.int64).ravel()
    lo_a = np.asarray(lo, dtype=np.int64).ravel()
    hi_a = lo_a.copy() if hi is None else np.asarray(hi, dtype=np.int64).ravel()
    if not (ks_a.shape == lo_a.shape == hi_a.shape):
        raise ValueError(f"phase_solve: ks/lo/hi shapes differ: {ks_a.shape} {lo_a.shape} {hi_a.shape}")
    if ks_a.size == 0:
        raise ValueError("phase_solve: no constraint frames")
    if np.any(hi_a < lo_a):
        bad = int(np.nonzero(hi_a < lo_a)[0][0])
        raise ValueError(f"phase_solve: hi < lo at comp frame {int(ks_a[bad])} ({int(hi_a[bad])} < {int(lo_a[bad])})")
    base = int(lo_a.min())
    d = (ks_a - int(comp_in)).astype(np.float64)
    return d, (lo_a - base).astype(np.float64), (hi_a - base).astype(np.float64), base


def chebyshev_x(d: np.ndarray, lo_rel: np.ndarray, hi_rel: np.ndarray, u: float) -> tuple[float, float, float, float]:
    """Closed-form solution of the Chebyshev LP with the speed fixed (u given):

        max t  s.t.  lo_k + t <= x + u d_k <= hi_k + 1 - t ,  t <= 0.5

    With u fixed the LP is one-dimensional: x - t >= Lmax = max(lo_k - u d_k) and
    x + t <= Umin = min(hi_k + 1 - u d_k), so t* = (Umin - Lmax) / 2 (capped at 0.5) at the centre
    x* = (Lmax + Umin) / 2 (the centre of the optimal face when the cap binds). This is exactly the
    optimum ``scipy.optimize.linprog`` returns for that LP (tested), without solver tolerances.

    Returns (x*, t*, Lmax, Umin) in local frame units.
    """
    L = lo_rel - u * d
    U = hi_rel + 1.0 - u * d
    lmax = float(L.max())
    umin = float(U.min())
    t = min((umin - lmax) / 2.0, 0.5)
    return (lmax + umin) / 2.0, t, lmax, umin


# ---------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------

def _u_range_exact(d: np.ndarray, lo_rel: np.ndarray, hi_rel: np.ndarray, ub: tuple[float, float],
                   tau: float, chunk: int = 256) -> tuple[float, float] | None:
    """Exact projection of the 2-variable LP onto u (Fourier-Motzkin): x exists iff every pair (a, b)
    satisfies lo_a - tau - u d_a <= hi_b + 1 + tau - u d_b, i.e. for d_b > d_a
        (lo_b - hi_a - 1 - 2 tau) / (d_b - d_a)  <=  u  <=  (hi_b + 1 - lo_a + 2 tau) / (d_b - d_a),
    and for d_a == d_b: lo_a <= hi_b + 1 + 2 tau. O(n^2) vectorised in row chunks; no solver tolerance."""
    order = np.argsort(d, kind="stable")
    d, lo_rel, hi_rel = d[order], lo_rel[order], hi_rel[order]
    umin, umax = float(ub[0]), float(ub[1])
    n = d.size
    for i0 in range(0, n, chunk):
        i1 = min(n, i0 + chunk)
        D = d[None, :] - d[i0:i1, None]                    # d_b - d_a, rows a, cols b
        la, ha = lo_rel[i0:i1, None], hi_rel[i0:i1, None]
        pos = D > 0
        if pos.any():
            with np.errstate(divide="ignore", invalid="ignore"):
                up = np.where(pos, (hi_rel[None, :] + 1.0 - la + 2 * tau) / D, np.inf)
                dn = np.where(pos, (lo_rel[None, :] - ha - 1.0 - 2 * tau) / D, -np.inf)
            umax = min(umax, float(up.min()))
            umin = max(umin, float(dn.max()))
        eq = D == 0
        if eq.any():
            if np.any(eq & (la > hi_rel[None, :] + 1.0 + 2 * tau)):
                return None
        if umin > umax + 1e-12:
            return None
    return umin, umax


def feasible_speed_range(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int], comp_in: int,
                         comp_fps: Any, raw_fps: Any, v_bounds: tuple[float, float] = (-8, 8),
                         tau: float = TAU, method: str = "auto") -> tuple[float, float] | None:
    """Tolerant feasible speed range [vmin, vmax] of a segment, or None when no (raw_in, v) explains
    every frame (the model is wrong: a cut, a mis-scored frame, frame blending, VFR ...).

    Two LPs in local frame units: min / max u subject to
    ``lo_k - base - tau <= x + u d_k <= hi_k - base + 1 + tau`` with u within v_bounds.
    method='highs': scipy ``linprog`` (HiGHS); method='exact': the same LP solved exactly by eliminating
    x (Fourier-Motzkin, O(n^2), identical result without solver tolerance -- tested against HiGHS);
    'auto' (default) = exact up to 1500 frames (~50x faster; the segment DP calls this thousands of
    times), HiGHS beyond.
    """
    d, lo_rel, hi_rel, _ = _prep(ks, lo, hi, comp_in)
    r = _ratio(comp_fps, raw_fps)
    ub = (float(v_bounds[0]) * r, float(v_bounds[1]) * r)
    n = d.size
    if method == "exact" or (method == "auto" and n <= 1500):
        res = _u_range_exact(d, lo_rel, hi_rel, ub, tau)
        return None if res is None else (res[0] / r, res[1] / r)
    if method not in ("auto", "highs"):
        raise ValueError(f"feasible_speed_range: unknown method {method!r}")
    from scipy.optimize import linprog
    # rows: -(x + u d) <= -(lo - tau) ; (x + u d) <= hi + 1 + tau
    a_ub = np.empty((2 * n, 2))
    a_ub[:n, 0] = -1.0
    a_ub[:n, 1] = -d
    a_ub[n:, 0] = 1.0
    a_ub[n:, 1] = d
    b_ub = np.concatenate([-(lo_rel - tau), hi_rel + 1.0 + tau])
    out = []
    for c in ((0.0, 1.0), (0.0, -1.0)):
        res = linprog(c, A_ub=a_ub, b_ub=b_ub, bounds=[(None, None), ub], method="highs")
        if res.status != 0 or res.x is None:
            return None
        out.append(float(res.x[1]))
    umin, umax = min(out), max(out)
    return umin / r, umax / r


def is_feasible(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int], comp_in: int, comp_fps: Any,
                raw_fps: Any, v: float | None = None, tau: float = TAU) -> bool:
    """True when some raw_in (and, if v is None, some speed in (-8, 8)) explains every frame
    under the tolerant closed constraints."""
    if v is None:
        return feasible_speed_range(ks, lo, hi, comp_in, comp_fps, raw_fps, tau=tau) is not None
    d, lo_rel, hi_rel, _ = _prep(ks, lo, hi, comp_in)
    u = float(v) * _ratio(comp_fps, raw_fps)
    lmax = float((lo_rel - u * d).max()) - tau
    umin = float((hi_rel + 1.0 - u * d).min()) + tau
    return lmax <= umin


def best_subinterval(starts: Any, ends: Any, costs: Any, xlo: float, xhi: float,
                     min_width: float = 2 * TAU) -> tuple[float, float, float]:
    """Minimum of a sum of interval penalties over [xlo, xhi].

    The penalty at x is the sum of ``costs[i]`` over the half-open intervals ``[starts[i], ends[i])``
    that contain x (piecewise constant). Returns ``(cost, a, b)``: the minimum and the widest maximal
    sub-interval [a, b] of [xlo, xhi] where it is attained (ties: nearest the centre of [xlo, xhi], then
    leftmost). Pieces narrower than ``min_width`` (floating-point slivers between abutting intervals,
    e.g. j - u d + 1 vs (j + 1) - u d) neither define the minimum nor break a run. An empty / degenerate
    [xlo, xhi] returns the cost at its centre and (xlo, xhi) unchanged. Exact sweep over the breakpoints
    (O(m log m)), deterministic."""
    xlo, xhi = float(xlo), float(xhi)
    s = np.asarray(starts, dtype=np.float64).ravel()
    e = np.asarray(ends, dtype=np.float64).ravel()
    c = np.asarray(costs, dtype=np.float64).ravel()
    if not (s.shape == e.shape == c.shape):
        raise ValueError("best_subinterval: starts/ends/costs shapes differ")
    r = _penalty_sweep(s, e, c, xlo, xhi, min_width, True)
    return r


def _penalty_sweep(s: np.ndarray, e: np.ndarray, c: np.ndarray, xlo: float, xhi: float, min_width: float,
                   interval: bool) -> tuple[float, float, float]:
    """best_subinterval's sweep (``interval=False``: only the minimum, a, b = xlo, xhi)."""
    if not xhi > xlo:
        x = (xlo + xhi) / 2.0
        return (float(c[(s <= x) & (x < e)].sum()) if c.size else 0.0), xlo, xhi
    if c.size == 0:
        return 0.0, xlo, xhi
    c0 = float(c[(s <= xlo) & (xlo < e)].sum())          # value on [xlo, first event)
    pos = np.concatenate([s, e])
    dw = np.concatenate([c, -c])
    inside = (pos > xlo) & (pos < xhi)
    pos, dw = pos[inside], dw[inside]
    order = np.argsort(pos, kind="stable")
    pos = pos[order]
    val = np.empty(pos.size + 1)
    val[0] = c0
    np.cumsum(dw[order], out=val[1:])
    val[1:] += c0
    bps = np.empty(pos.size + 2)
    bps[0], bps[-1] = xlo, xhi
    bps[1:-1] = pos
    sliver = (bps[1:] - bps[:-1]) < float(min_width)       # incl. zero-width pieces (duplicate breakpoints)
    if sliver.all():
        sliver[:] = False
    vmin = float(val[~sliver].min())
    out = float(round(vmin, 9)) + 0.0
    if not interval:
        return out, xlo, xhi
    good = val <= vmin + 1e-9 * max(1.0, abs(vmin))
    left = np.zeros_like(good)
    right = np.zeros_like(good)
    left[1:], right[:-1] = good[:-1], good[1:]
    good &= ~sliver | left | right                          # an isolated sliver is no minimum ...
    left[1:], right[:-1] = good[:-1], good[1:]
    good |= sliver & left & right                           # ... nor a break between two good pieces
    gi = np.flatnonzero(good)
    brk = np.flatnonzero(np.diff(gi) > 1)
    starts_i = np.concatenate([gi[:1], gi[brk + 1]])
    ends_i = np.concatenate([gi[brk], gi[-1:]])
    a_s, b_s = bps[starts_i], bps[ends_i + 1]
    centre = (xlo + xhi) / 2.0
    i = int(np.lexsort((a_s, np.abs((a_s + b_s) / 2.0 - centre), -(b_s - a_s)))[0])
    return out, float(a_s[i]), float(b_s[i])


def min_penalty(starts: np.ndarray, costs: np.ndarray, xlo: float, xhi: float,
                min_width: float = 2 * TAU) -> float:
    """Minimum of ``best_subinterval`` for unit intervals [starts, starts + 1) (the DP data term)."""
    s = np.asarray(starts, dtype=np.float64)
    return _penalty_sweep(s, s + 1.0, np.asarray(costs, dtype=np.float64), float(xlo), float(xhi),
                          min_width, False)[0]


def prefer_penalties(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int], plo: Sequence[int],
                     phi: Sequence[int], weight: Any = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Penalty pairs for ``solve_raw_in(penalties=...)``: every RAW frame j of a frame's constraint range
    [lo_k, hi_k] that lies outside its PREFERRED (measured) range [plo_k, phi_k] costs ``weight`` (default
    1; a callable weight(i, j) -> array may return per-pair costs). Frames whose preferred range does not
    intersect the constraint range are not penalised. Returns (i, j, w) aligned arrays (i indexes ks)."""
    lo_a = np.asarray(lo, dtype=np.int64).ravel()
    hi_a = np.asarray(hi, dtype=np.int64).ravel()
    flo = np.maximum(lo_a, np.asarray(plo, dtype=np.int64).ravel())
    fhi = np.minimum(hi_a, np.asarray(phi, dtype=np.int64).ravel())
    has = (flo <= fhi) & ((lo_a < flo) | (hi_a > fhi))
    ii, jj = [], []
    if has.any():
        width = int((hi_a - lo_a)[has].max())
        for off in range(width + 1):
            j = lo_a + off
            sel = has & (j <= hi_a) & ((j < flo) | (j > fhi))
            if sel.any():
                ii.append(np.nonzero(sel)[0])
                jj.append(j[sel])
    if not ii:
        z = np.zeros(0, np.int64)
        return z, z.copy(), np.zeros(0, np.float64)
    i = np.concatenate(ii)
    j = np.concatenate(jj)
    order = np.lexsort((j, i))
    i, j = i[order], j[order]
    if weight is None:
        w = np.ones(i.size, np.float64)
    elif callable(weight):
        w = np.asarray(weight(i, j), dtype=np.float64).reshape(i.size)
    else:
        w = np.full(i.size, float(weight))
    return i, j, w


# ---------------------------------------------------------------------------------------------
# AE floor-rule safety: breakpoint cells, max-min-slack placement, exact slack (FX-10)
# ---------------------------------------------------------------------------------------------

__all__ += ["SLACK_MERGE", "breakpoints_in", "place_in_cells", "cell_around", "place_raw_in", "layer_cell",
            "exact_min_slack", "exact_line_slack", "float_min_slack"]

SLACK_MERGE = 2 * TAU    # breakpoints closer than this (RAW frames) are one boundary (float noise between
                         # abutting constraints, as best_subinterval's min_width)
_CELL_WINDOW = 2.0       # half-width (frames) searched around the preferred point in a wide interval: the
                         # breakpoints are 1-periodic, so this window holds a full copy of every cell


def _residues(u: float, d: np.ndarray, round_rule: bool = False) -> np.ndarray:
    """Sorted residues in [0, 1) of the breakpoints n - u*d (all integers n) of frames d -- with
    ``round_rule`` also the round-to-nearest ones n + 1/2 - u*d -- near-duplicates (< SLACK_MERGE apart, also
    across the wrap 1 == 0) merged."""
    r = np.mod(-float(u) * np.asarray(d, dtype=np.float64).ravel(), 1.0)
    if round_rule:
        r = np.concatenate([r, np.mod(r + 0.5, 1.0)])
    r = np.sort(r)
    if r.size == 0:
        return r
    r = r[np.concatenate([[True], np.diff(r) > SLACK_MERGE])]
    if r.size > 1 and r[0] + 1.0 - r[-1] <= SLACK_MERGE:
        r = r[:-1]
    return r


def breakpoints_in(a: float, b: float, u: float, d: Any, round_rule: bool = False) -> np.ndarray:
    """Sorted floor-rule (with ``round_rule`` also round-to-nearest) breakpoints (local frame units) of frames
    ``d`` strictly inside (a, b); points within SLACK_MERGE of a or b are the edge itself."""
    a, b = float(a), float(b)
    r = _residues(u, np.asarray(d), round_rule)
    if r.size == 0 or not b > a:
        return np.zeros(0)
    periods = np.arange(math.floor(a), math.floor(b) + 1, dtype=np.float64)
    bp = (periods[:, None] + r[None, :]).ravel()
    return np.sort(bp[(bp > a + SLACK_MERGE) & (bp < b - SLACK_MERGE)])


def place_in_cells(a: float, b: float, u: float, d: Any, target: float | None = None, margin: Any = None,
                   round_rule: bool = False) -> dict:
    """Max-min-slack placement of x inside the allowed interval [a, b] (local frame units).

    The floor-rule breakpoints of frames ``d`` (every frame of the layer, binding or not) cut [a, b] into
    cells; a and b count as cell edges (they are breakpoints of the binding frames, or the round-rule /
    preferred / preserved-frames limits the caller keeps). ``round_rule`` (the allowed interval is the
    floor∩round set): the round-to-nearest breakpoints cut cells too, so the placement holds under either
    rule (it is never ON a round-rule boundary). Without ``target``: x = the midpoint of the widest
    cell (ties: nearest the centre of [a, b], then leftmost). With ``target`` (D3, the audio in-point): the
    cell containing the target, or the nearest one (ties: wider, then leftmost), and x = the target clamped
    to ``margin(cell width)`` from its edges (``margin`` None: the midpoint; never more than the half-width).

    Returns {x, lo, hi (the chosen cell), half (its half-width = the slack x keeps from the cell edges at
    the midpoint), best_half (the widest cell's half-width: the most slack any x in [a, b] can have),
    pinned (no breakpoint inside [a, b]: the constraints pin x to ONE cell), n_breaks (inside the searched
    window)}."""
    a, b = float(a), float(b)
    if not b > a:
        x = (a + b) / 2.0
        return {"x": x, "lo": a, "hi": b, "half": 0.0, "best_half": 0.0, "pinned": True, "n_breaks": 0}
    c = (a + b) / 2.0 if target is None else min(max(float(target), a), b)
    wa, wb = a, b
    if b - a > 2.0 * _CELL_WINDOW + 1.0:
        wa, wb = max(a, c - _CELL_WINDOW), min(b, c + _CELL_WINDOW)
        if wb - wa < 2.0 * _CELL_WINDOW:            # the preferred point near an edge: keep the window 4 wide
            wa, wb = (a, a + 2.0 * _CELL_WINDOW) if wa == a else (b - 2.0 * _CELL_WINDOW, b)
    bp = breakpoints_in(wa, wb, u, d, round_rule)
    edges = np.concatenate([[wa], bp, [wb]])
    lo, hi = edges[:-1], edges[1:]
    if wa > a and lo.size > 1:                      # a piece cut by the window is a partial copy of a cell
        lo, hi = lo[1:], hi[1:]
    if wb < b and lo.size > 1:
        lo, hi = lo[:-1], hi[:-1]
    half = (hi - lo) / 2.0
    best = float(half.max())
    hq = np.round(half * 1e9)                       # equal cells differ by float noise only
    if target is None:
        mid = (lo + hi) / 2.0
        i = int(np.lexsort((lo, np.abs(mid - c), -hq))[0])
    else:
        t = float(target)
        dist = np.maximum(0.0, np.maximum(lo - t, t - hi))
        i = int(np.lexsort((lo, -hq, dist))[0])
    cl, ch, h = float(lo[i]), float(hi[i]), float(half[i])
    if target is None:
        x = (cl + ch) / 2.0
    else:
        m = h if margin is None else min(h, max(0.0, float(margin(ch - cl))))
        x = min(max(float(target), cl + m), ch - m)
    return {"x": x, "lo": cl, "hi": ch, "half": h, "best_half": best,
            "pinned": bool(bp.size == 0 and wa == a and wb == b), "n_breaks": int(bp.size)}


def cell_around(x: float, u: float, d: Any) -> tuple[float, float]:
    """The floor-rule breakpoint cell [lo, hi] (local frame units) of frames ``d`` that contains x (on a
    breakpoint: the cell starting there)."""
    bp = breakpoints_in(float(x) - 1.5, float(x) + 1.5, u, d)
    left, right = bp[bp <= float(x)], bp[bp > float(x)]
    return (float(left[-1]) if left.size else float(x) - 1.5), (float(right[0]) if right.size else float(x) + 1.5)


def place_raw_in(interval_s: Sequence[float], comp_in: int, comp_out: int, v: float, comp_fps: Any, raw_fps: Any,
                 target_s: float | None = None, margin: Any = None, round_rule: bool = False) -> dict:
    """``place_in_cells`` in seconds for a whole stretch layer: every comp frame of [comp_in, comp_out), the
    allowed raw_in interval ``interval_s`` = [a, b] seconds (e.g. raw_in_interval_both with ``round_rule``,
    else raw_in_interval; for D3 also intersected with the preserved-frames range). Returns {raw_in (s),
    cell ([lo, hi] s), half (frames), best_half (frames), pinned, n_breaks}."""
    rf = float(Fraction(raw_fps))
    u = float(v) * _ratio(comp_fps, raw_fps)
    a_s, b_s = float(interval_s[0]), float(interval_s[1])
    base = math.floor(a_s * rf)
    d = np.arange(0, max(1, int(comp_out) - int(comp_in)), dtype=np.float64)
    t = None if target_s is None else float(target_s) * rf - base
    p = place_in_cells(a_s * rf - base, b_s * rf - base, u, d, t, margin, round_rule)
    return {"raw_in": (base + p["x"]) / rf, "cell": [(base + p["lo"]) / rf, (base + p["hi"]) / rf],
            "half": p["half"], "best_half": p["best_half"], "pinned": p["pinned"], "n_breaks": p["n_breaks"]}


def layer_cell(raw_in_s: float, comp_in: int, comp_out: int, v: float, comp_fps: Any, raw_fps: Any) -> dict:
    """The floor-rule breakpoint cell of EVERY frame of [comp_in, comp_out) that contains raw_in (seconds):
    {cell ([lo, hi] s), half (frames) -- the most slack any raw_in showing exactly these frames can have}."""
    rf = float(Fraction(raw_fps))
    u = float(v) * _ratio(comp_fps, raw_fps)
    base = math.floor(float(raw_in_s) * rf)
    d = np.arange(0, max(1, int(comp_out) - int(comp_in)), dtype=np.float64)
    lo, hi = cell_around(float(raw_in_s) * rf - base, u, d)
    return {"cell": [(base + lo) / rf, (base + hi) / rf], "half": (hi - lo) / 2.0}


def float_min_slack(x: float, u: float, d: Any) -> float:
    """Float slack (frames) of local position x over frames d: min distance of x + u d to an integer."""
    pos = float(x) + float(u) * np.asarray(d, dtype=np.float64)
    fr = pos - np.floor(pos)
    return float(np.minimum(fr, 1.0 - fr).min()) if fr.size else 0.5


def _exact(x: Any) -> Fraction:
    """The exact rational value of a written number (a float's binary value, a decimal string's value)."""
    if isinstance(x, Fraction):
        return x
    if isinstance(x, str):
        return Fraction(x)
    if isinstance(x, (int, np.integer)):
        return Fraction(int(x))
    return Fraction(float(x))


def exact_line_slack(p0: Any, step: Any, d0: int, d1: int) -> tuple[Fraction, int]:
    """Exact minimum over integers d in [d0, d1) of the distance of p0 + step * d (RAW frame positions,
    Fractions) to the nearest integer. Returns (slack, d) -- (1/2, d0) for an empty range."""
    p0, step = _exact(p0), _exact(step)
    den = math.lcm(p0.denominator, step.denominator)
    a = p0.numerator * (den // p0.denominator)
    s = step.numerator * (den // step.denominator)
    best, arg = den, int(d0)                       # numerators over den; den = a distance of one frame
    for dd in range(int(d0), int(d1)):
        r = (a + s * dd) % den
        m = min(r, den - r)
        if m < best:
            best, arg = m, dd
            if m == 0:
                break
    if best == den:
        return Fraction(1, 2), int(d0)
    return Fraction(best, den), arg


def exact_min_slack(raw_in: Any, v: Any, comp_in: int, k0: int, k1: int, comp_fps: Any, raw_fps: Any
                    ) -> tuple[Fraction, int]:
    """Exact AE floor-rule slack of a stretch segment: min over comp frames k in [k0, k1) of the distance of
    raw_fps * (raw_in + v (k - comp_in) / comp_fps) to the nearest integer (RAW frames, Fraction), with
    raw_in and v as WRITTEN (a float's exact binary value, e.g. the cutlist's 9-decimal seconds). Returns
    (slack, k at the minimum)."""
    rf, cf = Fraction(raw_fps), Fraction(comp_fps)
    s, d = exact_line_slack(rf * _exact(raw_in), rf * _exact(v) / cf, int(k0) - int(comp_in), int(k1) - int(comp_in))
    return s, int(comp_in) + d


def solve_raw_in(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int], comp_in: int, v: float,
                 comp_fps: Any, raw_fps: Any, prefer: tuple[Sequence[int], Sequence[int]] | None = None,
                 penalties: tuple[Any, Any, Any] | None = None) -> dict:
    """Phase-solve raw_in (seconds) for a segment with its speed v fixed (the snapped speed).

    Chebyshev LP with u fixed: max t s.t. lo_k + t <= x + u d_k <= hi_k + 1 - t, -tau <= t <= 0.5
    (closed form, see ``chebyshev_x``). The allowed interval is the floor-rule feasible interval, or -- when
    the set that ALSO satisfies round-to-nearest sampling (lo_k - 0.5 <= x + u d_k < hi_k + 0.5) is
    non-empty (wider than 2 * TIE_SLACK) -- that overlap, so the result holds under either rule. raw_in is
    the midpoint of the breakpoint cell of that interval with the most slack for EVERY frame from comp_in to
    the last constraint frame (``place_in_cells``, FX-10; = the interval centre when no breakpoint lies
    inside, e.g. all frames exact), never the bare centre.

    Soft ranges are tolerances, not evidence: when ``prefer`` = (plo, phi) (the MEASURED argmax range per
    frame, e.g. refine's pristine raw_lo/raw_hi) or explicit ``penalties`` = (i, j, w) (frame index into
    ks, RAW frame, cost; see ``prefer_penalties``) are given, x is first restricted to the sub-interval of
    the floor-rule interval where the total penalty of frames showing a non-preferred RAW frame is minimal
    (``best_subinterval``) -- so the phase agrees with the measured frames, instead of centring an
    asymmetric soft intersection -- and the rules above are applied inside it. ``interval_floor`` /
    ``margin_ms`` then refer to that sub-interval (the raw_in values that reproduce the claimed frames);
    ``interval_soft`` is the whole soft-feasible interval and ``data_cost`` the minimal penalty.

    Returns
      raw_in          seconds (float) -- the value After Effects gets
      raw_in_frames   raw_fps * raw_in (float RAW frame position at comp_in)
      slack           t* (frames) of the floor-rule Chebyshev LP (negative = infeasible)
      interval_floor  [a, b] seconds: feasible raw_in interval under the floor rule
      interval_soft   [a, b] seconds: the whole soft-range feasible interval (== interval_floor without
                      prefer / penalties)
      interval_both   [a, b] seconds under floor AND round-to-nearest, or None
      margin_ms       distance of raw_in to the nearest edge of interval_floor (ms)
      tie_frames      comp frames whose own slack at raw_in is < TIE_SLACK (timing ties)
      frame_slack     per-frame slack at raw_in (frames), aligned with ks
      data_cost       total penalty at raw_in (0 without prefer / penalties)
      ok              the constraints are feasible (t* >= -tau)
      used_both       raw_in was taken from interval_both
      min_slack       floor-rule slack (frames) of raw_in over every frame comp_in .. last constraint
      cell            [a, b] seconds: the breakpoint cell raw_in sits in (None when infeasible)
      cell_width      its width (frames)
      best_slack      the most slack (frames) any raw_in of the allowed interval has = half the widest cell
      pinned          no breakpoint inside the allowed interval: the frames pin raw_in to ONE cell
    """
    d, lo_rel, hi_rel, base = _prep(ks, lo, hi, comp_in)
    rf = Fraction(raw_fps)
    u = float(v) * _ratio(comp_fps, rf)
    x, t, lmax, umin = chebyshev_x(d, lo_rel, hi_rel, u)
    ok = t >= -TAU
    a_int, b_int = lmax, umin
    data_cost = 0.0
    if penalties is None and prefer is not None:
        penalties = prefer_penalties(ks, lo, hi, prefer[0], prefer[1])
    has_pen = penalties is not None and np.asarray(penalties[0]).size > 0
    if has_pen:
        pi = np.asarray(penalties[0], dtype=np.int64).ravel()
        pj = np.asarray(penalties[1], dtype=np.float64).ravel() - base
        pw = np.asarray(penalties[2], dtype=np.float64).ravel()
        st = pj - u * d[pi]
        if ok:
            data_cost, a_int, b_int = best_subinterval(st, st + 1.0, pw, lmax, umin)
            x = (a_int + b_int) / 2.0
        else:
            data_cost, _a, _b = best_subinterval(st, st + 1.0, pw, x, x)
    # round-to-nearest feasible set: lo - 0.5 <= x + u d < hi + 0.5
    lr = float((lo_rel - 0.5 - u * d).max())
    ur = float((hi_rel + 0.5 - u * d).min())
    both_lo, both_hi = max(a_int, lr), min(b_int, ur)
    used_both = False
    if ok and both_hi - both_lo > 2 * TIE_SLACK:
        x = (both_lo + both_hi) / 2.0
        used_both = True
    # FX-10: not the interval centre (it can sit on a breakpoint of a non-binding frame) but the midpoint
    # of the max-min-slack cell of EVERY frame from comp_in to the last constraint frame
    span = np.arange(min(0.0, float(d.min())), float(d.max()) + 1.0)
    cell = None
    if ok:
        cell = place_in_cells(both_lo if used_both else a_int, both_hi if used_both else b_int, u, span,
                              round_rule=used_both)
        x = cell["x"]
    pos = x + u * d
    fslack = np.minimum(pos - lo_rel, hi_rel + 1.0 - pos)
    tie_mask = fslack < TIE_SLACK
    if has_pen and ok and b_int - a_int < 2 * TIE_SLACK:
        # the preferred sub-interval is a single point: frames at a frame boundary may show either frame
        fr = pos - np.floor(pos)
        tie_mask |= (fr < TIE_SLACK) | (fr > 1.0 - TIE_SLACK)
    ties = np.asarray(ks, dtype=np.int64).ravel()[tie_mask]
    rff = float(rf)
    base_s = float(Fraction(base) / rf)

    def sec(xl: float) -> float:
        return base_s + xl / rff

    return {
        "raw_in": sec(x),
        "raw_in_frames": base + x,
        "slack": float(t),
        "interval_floor": [sec(a_int), sec(b_int)],
        "interval_soft": [sec(lmax), sec(umin)],
        "interval_both": [sec(both_lo), sec(both_hi)] if (ok and both_hi - both_lo > 2 * TIE_SLACK) else None,
        "margin_ms": float(min(x - a_int, b_int - x) / rff * 1000.0),
        "tie_frames": [int(k) for k in ties],
        "frame_slack": fslack,
        "data_cost": float(data_cost),
        "ok": bool(ok),
        "used_both": used_both,
        "u": u,
        "base": base,
        "x": x,
        "min_slack": float_min_slack(x, u, span),
        "cell": [sec(cell["lo"]), sec(cell["hi"])] if cell else None,
        "cell_width": float(cell["hi"] - cell["lo"]) if cell else 0.0,
        "best_slack": float(cell["best_half"]) if cell else 0.0,
        "pinned": bool(cell["pinned"]) if cell else False,
    }


def solve_shared_raw_in(parts: Sequence[tuple[Sequence[int], Sequence[int], Sequence[int], int]], v: float,
                        comp_fps: Any, raw_fps: Any, penalties: tuple[Any, Any, Any] | None = None) -> list[dict]:
    """One phase solve for TIME-TIED segments (DESIGN §5 segment.py, FX-04 2): consecutive segments at the same speed
    v that show ONE RAW time line (a reframe at a RAW-native shot change, a framing step on a continuous clip).

    ``parts`` = [(ks, lo, hi, comp_in), ...] in competitor order; ``penalties`` (i, j, w) index the concatenation of
    the parts' ks (see :func:`prefer_penalties`). The union is solved once by :func:`solve_raw_in` at the first part's
    comp_in c0; every part gets that line's values at its own comp_in: raw_in_i = raw_in + v (comp_in_i - c0) /
    comp_fps (interval_floor / interval_soft / interval_both shifted alike, so AE's floor rule gives the same RAW
    frame on every comp frame whichever layer shows it), the shared slack / margin_ms / data_cost / ok, its own
    frames' tie_frames and frame_slack, and 'shared' = {'comp_in': c0, 'parts': n, 'raw_in': the line's raw_in at
    c0}. Returns one dict per part (the keys of :func:`solve_raw_in`)."""
    if not parts:
        return []
    ks = np.concatenate([np.asarray(p[0], dtype=np.int64).ravel() for p in parts])
    lo = np.concatenate([np.asarray(p[1], dtype=np.int64).ravel() for p in parts])
    hi = np.concatenate([np.asarray(p[2], dtype=np.int64).ravel() for p in parts])
    c0 = int(parts[0][3])
    sol = solve_raw_in(ks, lo, hi, c0, v, comp_fps, raw_fps, penalties=penalties)
    cf = float(Fraction(comp_fps))
    u = float(sol["u"])
    fslack = np.asarray(sol["frame_slack"])
    ties = set(int(k) for k in sol["tie_frames"])
    out: list[dict] = []
    off = 0
    for pk, _lo, _hi, ci in parts:
        pk = np.asarray(pk, dtype=np.int64).ravel()
        sh_s = float(v) * (int(ci) - c0) / cf
        sh_f = u * (int(ci) - c0)

        def shift(iv: Any) -> list[float] | None:
            return None if iv is None else [float(iv[0]) + sh_s, float(iv[1]) + sh_s]
        d = dict(sol)
        d.update(raw_in=float(sol["raw_in"]) + sh_s, raw_in_frames=float(sol["raw_in_frames"]) + sh_f,
                 interval_floor=shift(sol["interval_floor"]), interval_soft=shift(sol["interval_soft"]),
                 interval_both=shift(sol["interval_both"]), x=float(sol["x"]) + sh_f,
                 tie_frames=sorted(int(k) for k in pk if int(k) in ties), frame_slack=fslack[off:off + pk.size],
                 shared={"comp_in": c0, "parts": len(parts), "raw_in": float(sol["raw_in"])})
        out.append(d)
        off += pk.size
    return out


def ae_frame(raw_in: float, v: float, k: int | np.ndarray, comp_in: int, comp_fps: Any, raw_fps: Any,
             rule: str = "floor") -> int | np.ndarray:
    """RAW frame After Effects shows at comp frame k (DESIGN §2.1).

    rule='floor' (AE): floor(raw_fps * (raw_in + v * (t_k - t_in)) + 1e-9);
    rule='round': floor(... + 0.5) (round-to-nearest, used to check rule-robustness).
    k may be an int (returns int) or an integer array (returns int64 array).
    """
    rf = float(Fraction(raw_fps))
    cf = float(Fraction(comp_fps))
    scalar = np.isscalar(k)
    kk = np.asarray(k, dtype=np.float64)
    val = rf * (float(raw_in) + float(v) * ((kk - float(comp_in)) / cf))
    if rule == "floor":
        out = np.floor(val + _AE_EPS)
    elif rule == "round":
        out = np.floor(val + 0.5)
    else:
        raise ValueError(f"ae_frame: unknown rule {rule!r}")
    out = out.astype(np.int64)
    return int(out) if scalar else out


def dominant_speed(preferred: Any) -> float | None:
    """Dominant speed of already-solved segments.

    preferred: a sequence of speeds (each occurrence = one vote), a sequence of (speed, weight) pairs,
    or a mapping {speed: weight} (e.g. frames). Values within 1e-6 are grouped. Ties -> first seen.
    """
    if preferred is None:
        return None
    if isinstance(preferred, Mapping):
        items = [(float(k), float(w)) for k, w in preferred.items()]
    else:
        items = []
        for p in preferred:
            if isinstance(p, (tuple, list)) and len(p) == 2:
                items.append((float(p[0]), float(p[1])))
            else:
                items.append((float(p), 1.0))
    groups: list[list[float]] = []   # [speed, weight]
    for s, w in items:
        if not math.isfinite(s):
            continue
        for g in groups:
            if abs(g[0] - s) <= 1e-6 * max(1.0, abs(s)):
                g[1] += w
                break
        else:
            groups.append([s, w])
    if not groups:
        return None
    best = max(groups, key=lambda g: g[1])  # max keeps the first of equal weights
    return float(best[0])


def _speed_values(preferred: Any) -> list[float]:
    if preferred is None:
        return []
    if isinstance(preferred, Mapping):
        return [float(k) for k in preferred.keys()]
    out = []
    for p in preferred:
        out.append(float(p[0]) if isinstance(p, (tuple, list)) and len(p) == 2 else float(p))
    return out


def snap_speed(v_ols: float, vrange: tuple[float, float] | None, cfg: Any, preferred: Any = (),
               exact_range: tuple[float, float] | None = None) -> tuple[float, bool]:
    """Choose the reported speed of a segment from its tolerant feasible range.

    Candidates = cfg.speed_snap_values ∪ preferred (speeds of already-solved segments) that lie inside
    [vmin, vmax] AND pass the prompt's snap test ('snap only if within 0.3 % and the residuals don't get
    worse'): the candidate lies inside ``exact_range`` -- the speeds that reproduce the MEASURED (argmax)
    frames, i.e. the residuals do not get worse -- or within ``cfg.speed_snap_tol`` (relative) of v_ols,
    the robust slope of the measured frames. ``exact_range=None`` means vrange itself is the measurement
    (exact constraints), so every candidate inside it passes (the previous behaviour).
    Preference: (1) the dominant speed of the edit (``dominant_speed(preferred)``), (2) 1.0, (3) the
    candidate closest to v_ols. No candidate -> (clip(v_ols, exact_range or [vmin, vmax]), True)
    (unsnapped). The LP centre is never reported as the speed.
    """
    if vrange is None:
        v = float(v_ols) if math.isfinite(float(v_ols)) else 1.0
        return v, True
    vmin, vmax = float(vrange[0]), float(vrange[1])
    tol = float(getattr(cfg, "speed_snap_tol", 0.003))
    vo_ok = math.isfinite(float(v_ols))

    def inside(c: float, r: tuple[float, float] = (vmin, vmax)) -> bool:
        eps = 1e-9 * max(1.0, abs(c))
        return float(r[0]) - eps <= c <= float(r[1]) + eps

    def passes(c: float) -> bool:
        if not inside(c):
            return False
        if exact_range is None or inside(c, exact_range):
            return True
        return vo_ok and abs(c - float(v_ols)) <= tol * abs(c)

    snaps = [float(s) for s in getattr(cfg, "speed_snap_values", (1.0,))]
    cands: list[float] = []
    for c in snaps + _speed_values(preferred):
        if math.isfinite(c) and passes(c) and not any(abs(c - e) <= 1e-9 * max(1.0, abs(c)) for e in cands):
            cands.append(c)
    dom = dominant_speed(preferred)
    if dom is not None and passes(dom):
        return dom, False
    if passes(1.0):
        return 1.0, False
    vo = float(v_ols) if vo_ok else (vmin + vmax) / 2.0
    if cands:
        return min(cands, key=lambda c: (abs(c - vo), c)), False
    r0, r1 = vmin, vmax
    if exact_range is not None and float(exact_range[0]) <= vmax and float(exact_range[1]) >= vmin:
        r0, r1 = max(vmin, float(exact_range[0])), min(vmax, float(exact_range[1]))
    return float(min(max(vo, r0), r1)), True


def estimate_speed(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int] | None, comp_fps: Any, raw_fps: Any,
                   iters: int = 8) -> float:
    """Robust (Huber IRLS) slope of RAW time vs competitor time on the range midpoints -> speed v_ols
    (Δ RAW seconds / Δ competitor seconds). NaN when fewer than 2 frames."""
    k = np.asarray(ks, dtype=np.float64).ravel()
    lo_a = np.asarray(lo, dtype=np.float64).ravel()
    hi_a = lo_a if hi is None else np.asarray(hi, dtype=np.float64).ravel()
    if k.size < 2 or np.ptp(k) == 0:
        return float("nan")
    y = (lo_a + hi_a + 1.0) / 2.0          # centre of [lo, hi+1) in RAW frames
    x = k - k.mean()
    w = np.ones_like(x)
    slope = float("nan")
    for _ in range(max(1, iters)):
        prev = slope
        sw = w.sum()
        xm = (w * x).sum() / sw
        ym = (w * y).sum() / sw
        sxx = (w * (x - xm) ** 2).sum()
        if sxx <= 0:
            break
        slope = float((w * (x - xm) * (y - ym)).sum() / sxx)
        if abs(slope - prev) <= 1e-12 * max(1.0, abs(slope)):
            break
        r = y - (ym + slope * (x - xm))
        mad = float(np.median(np.abs(r - np.median(r))))
        c = max(1.345 * 1.4826 * mad, 0.75)   # never below the quantisation of integer frames
        a = np.abs(r)
        w = np.where(a <= c, 1.0, c / np.maximum(a, 1e-12))
    return slope / _ratio(comp_fps, raw_fps)


def speeds_in(values: Iterable[float], vrange: tuple[float, float]) -> list[float]:
    """Helper: the values that lie inside a (tolerant) speed range."""
    vmin, vmax = vrange
    return [float(v) for v in values if vmin - 1e-9 * max(1, abs(v)) <= v <= vmax + 1e-9 * max(1, abs(v))]
