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
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

__all__ = [
    "TAU", "TIE_SLACK", "feasible_speed_range", "is_feasible", "solve_raw_in", "ae_frame", "snap_speed",
    "estimate_speed", "dominant_speed", "chebyshev_x", "best_subinterval", "prefer_penalties",
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

    def at(x: float) -> float:
        return float(c[(s <= x) & (x < e)].sum()) if c.size else 0.0

    if not xhi > xlo:
        return at((xlo + xhi) / 2.0), xlo, xhi
    if c.size == 0:
        return 0.0, xlo, xhi
    inner = np.concatenate([s, e])
    inner = inner[(inner > xlo) & (inner < xhi)]
    bps = np.unique(np.concatenate([[xlo, xhi], inner]))
    mids = (bps[:-1] + bps[1:]) / 2.0
    os_ = np.argsort(s, kind="stable")
    oe = np.argsort(e, kind="stable")
    cs = np.concatenate([[0.0], np.cumsum(c[os_])])
    ce = np.concatenate([[0.0], np.cumsum(c[oe])])
    val = cs[np.searchsorted(s[os_], mids, side="right")] - ce[np.searchsorted(e[oe], mids, side="right")]
    sliver = (bps[1:] - bps[:-1]) < float(min_width)
    if sliver.all():
        sliver[:] = False
    vmin = float(val[~sliver].min())
    good = val <= vmin + 1e-9 * max(1.0, abs(vmin))
    good &= ~sliver | np.r_[False, good[:-1]] | np.r_[good[1:], False]      # an isolated sliver is no minimum
    left, right = np.r_[False, good[:-1]], np.r_[good[1:], False]
    good |= sliver & left & right                                            # ... nor a break between two
    best = None
    i, n = 0, good.size
    centre = (xlo + xhi) / 2.0
    while i < n:
        if not good[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and good[j + 1]:
            j += 1
        a, b = float(bps[i]), float(bps[j + 1])
        key = (-(b - a), abs((a + b) / 2.0 - centre), a)
        if best is None or key < best[0]:
            best = (key, a, b)
        i = j + 1
    _k, a, b = best
    return at((a + b) / 2.0), a, b


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


def solve_raw_in(ks: Sequence[int], lo: Sequence[int], hi: Sequence[int], comp_in: int, v: float,
                 comp_fps: Any, raw_fps: Any, prefer: tuple[Sequence[int], Sequence[int]] | None = None,
                 penalties: tuple[Any, Any, Any] | None = None) -> dict:
    """Phase-solve raw_in (seconds) for a segment with its speed v fixed (the snapped speed).

    Chebyshev LP with u fixed: max t s.t. lo_k + t <= x + u d_k <= hi_k + 1 - t, -tau <= t <= 0.5
    (closed form, see ``chebyshev_x``). raw_in = centre of the floor-rule feasible interval, or -- when
    the set that ALSO satisfies round-to-nearest sampling (lo_k - 0.5 <= x + u d_k < hi_k + 0.5) is
    non-empty (wider than 2 * TIE_SLACK) -- the centre of that overlap, so the result holds under
    either rule.

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
    }


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
