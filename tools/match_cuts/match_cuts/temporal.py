"""Competitor-only temporal signature (DESIGN.md §5 temporal.py; hypothesis-neutral verification).

For consecutive frames (k, k+1) -- and (k, k+2) for the motion-growth test -- of ONE image sequence inside
the video box, with the layout's caption / overlay masks only: an ECC alignment (affine, started from a
phase-correlation translation, projected to the closest similarity), then the post-warp masked ZNCC ``cc``,
the mean |diff| ``mad`` (8-bit) and the warp (centre displacement, scale, rotation). Nothing here needs the
RAW or any segmentation: the same measurement runs on the competitor proxy and, in verify, on the recreation.

A pair is labelled (``label_pairs``, per competitor shot):

* ``cut``     -- post-alignment ZNCC below ``temporal_shot_cc``: a shot change (never compared);
* ``repeat``  -- both frames show the same source image up to an editor similarity transform (a 23.976->30
  pulldown duplicate, a hold): its residual r = 1 - cc sits in the shot's lower residual cluster, at least
  ``temporal_gap_ratio`` below every changing pair, AND the two frames are interchangeable with respect to
  their neighbours (r(k, k+2) and r(k-1, k+1) do not grow by more than ``temporal_growth_ratio`` over the
  one-frame residuals -- slow motion grows, a repeat does not);
* ``move``    -- the content changed beyond the shot's noise floor: an upper-cluster pair of a shot whose
  residuals split into two clusters, or -- in a shot without such a gap whose residual grows with the frame
  distance (median growth above ``temporal_growth_ratio``) -- a pair whose own growth exceeds it;
* ``unknown`` -- everything else (an all-repeat static run, a static noise plate, saturated motion, too
  few pairs): never used as evidence.

The noise floor is therefore measured per shot from the data (the repeat-vs-move margin is ~0.001 ZNCC at
thumbnail scale, so no absolute threshold separates them); a shot without a measurable floor gives no
repeat labels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np

REPEAT, MOVE, UNKNOWN, CUT = "repeat", "move", "unknown", "cut"
R_EPS = 1e-5                 # residual floor (1 - cc of two identical images is ~0)
MIN_PIXELS = 64              # fewer valid pixels after alignment -> unmeasured
# physical bounds of an editor move between two consecutive competitor frames: an ECC result outside them is
# a degenerate fit (low-texture content lets a large zoom / rotation 'explain' real motion) and is replaced by
# the phase-correlation translation
MAX_PAIR_SCALE = 0.10        # |relative scale change|
MAX_PAIR_ROT_DEG = 5.0
MAX_PAIR_SHIFT = 0.25        # translation, fraction of the image's long side
IDENTITY_START_PX = 1.0      # a phase-correlation start this close to no motion IS the identity start
IDENTITY_START_LEVELS = 2    # coarse-to-fine levels of the identity start (x4: a 12 px motion starts 3 px away)
ECC_PYRAMID_MIN_SIDE = 16    # a pyramid level keeps at least this many px on its short side


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    v = getattr(cfg, name, None) if cfg is not None else None
    return default if v is None else v


# ---------------------------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------------------------

def prepare(img: np.ndarray, mask: np.ndarray | None, max_side: int, blur: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(float32 image, bool mask), downscaled (INTER_AREA) so the long side is at most ``max_side`` px and
    blurred with ``blur`` (px at the output scale). Mask pixels survive the downscale only when fully valid;
    the blur's reach (ceil(3 blur) px) is removed from the mask along the image border and around invalid
    pixels, where the blurred values depend on content the crop does not hold (it would not move with the
    picture and bias the alignment)."""
    import cv2
    a = np.asarray(img, np.float32)
    m = np.ones(a.shape[:2], bool) if mask is None else np.asarray(mask, bool)
    h, w = a.shape[:2]
    side = max(h, w)
    if max_side and side > int(max_side):
        f = float(max_side) / float(side)
        size = (max(8, int(round(w * f))), max(8, int(round(h * f))))
        a = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
        m = cv2.resize(m.astype(np.float32), size, interpolation=cv2.INTER_AREA) >= 0.999
    if blur and blur > 0:
        a = cv2.GaussianBlur(a, (0, 0), float(blur))
        r = int(math.ceil(3.0 * float(blur)))
        pad = cv2.copyMakeBorder(m.astype(np.uint8), r, r, r, r, cv2.BORDER_CONSTANT, value=0)
        m = cv2.erode(pad, np.ones((2 * r + 1, 2 * r + 1), np.uint8))[r:-r, r:-r] > 0
    return a, m


def scale_of(shape: Sequence[int], max_side: int) -> float:
    """Downscale factor ``prepare`` applies to an image of ``shape`` (h, w)."""
    side = max(int(shape[0]), int(shape[1]))
    return float(max_side) / float(side) if max_side and side > int(max_side) else 1.0


def _zncc(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> float:
    x = a[m].astype(np.float64)
    y = b[m].astype(np.float64)
    if x.size < MIN_PIXELS:
        return float("nan")
    x -= x.mean()
    y -= y.mean()
    den = math.sqrt(float(x @ x) * float(y @ y))
    return float(x @ y / den) if den > 1e-9 else float("nan")


# ---------------------------------------------------------------------------------------------
# pair alignment
# ---------------------------------------------------------------------------------------------

@dataclass
class PairMeasure:
    """One aligned pair (a, b): ``cc`` post-alignment masked ZNCC (NaN: unmeasured), ``mad`` mean |a - b|
    after alignment (8-bit), the similarity part of the warp that maps a's pixels onto b's (``dx``, ``dy``:
    displacement of the image centre in px of the measured scale; ``ds``: relative scale - 1; ``dtheta``
    degrees), ``aligned`` (ECC converged; else the phase-correlation translation only)."""
    cc: float = float("nan")
    mad: float = float("nan")
    dx: float = 0.0
    dy: float = 0.0
    ds: float = 0.0
    dtheta: float = 0.0
    aligned: bool = False

    @property
    def r(self) -> float:
        """Residual 1 - cc (>= R_EPS), NaN when unmeasured."""
        return max(1.0 - self.cc, R_EPS) if math.isfinite(self.cc) else float("nan")

    def to_dict(self) -> dict:
        f = lambda v, n=6: None if not math.isfinite(float(v)) else round(float(v), n)  # noqa: E731
        return {"cc": f(self.cc), "mad": f(self.mad, 3), "dx": f(self.dx, 3), "dy": f(self.dy, 3), "ds": f(self.ds),
                "dtheta": f(self.dtheta, 4), "aligned": bool(self.aligned)}


def _phase_shift(a: np.ndarray, ma: np.ndarray, b: np.ndarray, mb: np.ndarray) -> tuple[float, float]:
    """Translation (sx, sy) with b(x + s) ~ a(x) from phase correlation (masked pixels set to the mean)."""
    import cv2
    fa = np.where(ma, a, float(a[ma].mean()) if ma.any() else 0.0).astype(np.float32)
    fb = np.where(mb, b, float(b[mb].mean()) if mb.any() else 0.0).astype(np.float32)
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    try:
        (sx, sy), _resp = cv2.phaseCorrelate(fa, fb, win)
    except cv2.error:
        return 0.0, 0.0
    if not (math.isfinite(sx) and math.isfinite(sy)) or abs(sx) > a.shape[1] / 3 or abs(sy) > a.shape[0] / 3:
        return 0.0, 0.0
    return float(sx), float(sy)


def _similarity(W: np.ndarray, shape: Sequence[int]) -> np.ndarray | None:
    """The similarity closest to the affine W (template -> input): the rotation of its polar decomposition
    times sqrt(det), keeping where W maps the image centre. An editor transform is a similarity (AE scales
    uniformly); shear / anisotropic scale in the affine fit only absorbs content motion, so it is dropped
    before the residual is measured. None when outside the MAX_PAIR_* bounds or not finite."""
    if not np.all(np.isfinite(W)):
        return None
    W = np.asarray(W, np.float64)
    A, t = W[:, :2], W[:, 2]
    det = float(np.linalg.det(A))
    if det <= 0:
        return None
    U, _sv, Vt = np.linalg.svd(A)
    R = U @ Vt
    if float(np.linalg.det(R)) <= 0:
        return None
    S = math.sqrt(det) * R
    rot = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    h, w = int(shape[0]), int(shape[1])
    c = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    tc = A @ c + t                                 # where the affine maps the centre
    d = tc - c
    if abs(math.sqrt(det) - 1.0) > MAX_PAIR_SCALE or abs(rot) > MAX_PAIR_ROT_DEG or \
            float(np.hypot(*d)) > MAX_PAIR_SHIFT * max(h, w):
        return None
    return np.hstack([S, (tc - S @ c)[:, None]]).astype(np.float32)


def _ecc_from(a: np.ndarray, ma: np.ndarray, b: np.ndarray, mb: np.ndarray, W0: np.ndarray, crit: tuple
              ) -> np.ndarray | None:
    """ECC (MOTION_AFFINE) from the start ``W0`` projected to the closest editor similarity; None when ECC does not
    converge or the fit is outside the MAX_PAIR_* bounds."""
    import cv2
    try:
        if hasattr(cv2, "findTransformECCWithMask"):
            _cc, Wm = cv2.findTransformECCWithMask(a, b, ma.astype(np.uint8) * 255, mb.astype(np.uint8) * 255, W0.copy(),
                                                   cv2.MOTION_AFFINE, crit, 1)
        else:  # pragma: no cover - older OpenCV: no input mask
            _cc, Wm = cv2.findTransformECC(a, b, W0.copy(), cv2.MOTION_AFFINE, crit, ma.astype(np.uint8) * 255, 1)
    except cv2.error:
        return None
    return _similarity(Wm, a.shape)


def _ecc_pyramid(a: np.ndarray, ma: np.ndarray, b: np.ndarray, mb: np.ndarray, crit: tuple,
                 levels: int = IDENTITY_START_LEVELS) -> np.ndarray | None:
    """ECC from the identity, coarse to fine: each level halves the images (INTER_AREA; a mask pixel survives only
    when fully valid), so a motion of several pixels is a sub-pixel start at the coarsest level. A level whose ECC
    fails passes its start on unchanged; the full-resolution fit decides (None when it fails)."""
    import cv2
    pyr = [(a, ma, b, mb)]
    for _ in range(int(levels)):
        pa, pma, pb, pmb = pyr[-1]
        h, w = pa.shape
        if min(h, w) < 2 * ECC_PYRAMID_MIN_SIDE:
            break
        size = (w // 2, h // 2)
        down = lambda x: cv2.resize(x, size, interpolation=cv2.INTER_AREA)  # noqa: E731
        pyr.append((down(pa), down(pma.astype(np.float32)) >= 0.999, down(pb), down(pmb.astype(np.float32)) >= 0.999))
    W = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32)
    for lev in range(len(pyr) - 1, -1, -1):
        la, lma, lb, lmb = pyr[lev]
        if int((lma & lmb).sum()) < MIN_PIXELS:
            continue
        Ws = _ecc_from(la, lma, lb, lmb, W, crit)
        if lev == 0:
            return Ws
        if Ws is not None:
            W = Ws
        W = W.copy()
        W[:, 2] *= 2.0                         # translation to the next (finer) level
    return None


def _measure_warp(a: np.ndarray, ma: np.ndarray, b: np.ndarray, mb: np.ndarray, W: np.ndarray, aligned: bool
                  ) -> PairMeasure:
    """The pair's post-warp masked ZNCC / mean |diff| and the warp's similarity parameters under ``W``."""
    import cv2
    out = PairMeasure(aligned=bool(aligned))
    h, w = a.shape
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    bw = cv2.warpAffine(b, W, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    mw = cv2.warpAffine(mb.astype(np.uint8), W, (w, h), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
    inside = cv2.warpAffine(np.full((h, w), 255, np.uint8), W, (w, h), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    inside = cv2.erode(inside, np.ones((3, 3), np.uint8)) > 0
    m = ma & mw & inside
    out.cc = _zncc(a, bw, m)
    if int(m.sum()) >= MIN_PIXELS:
        out.mad = float(np.mean(np.abs(a[m].astype(np.float64) - bw[m])))
    A = W[:, :2].astype(np.float64)
    c = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    d = A @ c + W[:, 2].astype(np.float64) - c
    out.dx, out.dy = float(d[0]), float(d[1])
    out.ds = math.sqrt(max(abs(float(np.linalg.det(A))), 1e-12)) - 1.0
    out.dtheta = math.degrees(math.atan2(A[1, 0] - A[0, 1], A[0, 0] + A[1, 1]))
    return out


def align_pair(a: np.ndarray, ma: np.ndarray, b: np.ndarray, mb: np.ndarray, cfg: Any = None) -> PairMeasure:
    """Align ``b`` onto ``a`` (both prepared, same shape): phase-correlation translation, then ECC
    (MOTION_AFFINE; template = a with its mask, input = b with its mask) projected to the closest similarity
    (``_similarity``); measure the masked ZNCC and the mean |diff| of a and the warped b over a's mask & b's
    warped mask & the warp's support.

    Phase correlation can lock onto an ALIAS of a periodic texture (blocky cell grids, stripes: film24 pair 122,
    a -17/+17 px peak for a true 6 px editor pan) and ECC from there does not converge. When the first start does
    not give a converged fit above ``temporal_shot_cc`` (a pair that would read as a cut), ECC is also started from
    the identity (no motion, the small-motion prior of consecutive frames) and the start whose fit has the higher
    post-warp ZNCC is kept -- measured the same way on every sequence (competitor and recreation alike)."""
    import cv2
    if a is None or b is None or a.shape != b.shape:
        return PairMeasure()
    ma, mb = np.asarray(ma, bool), np.asarray(mb, bool)
    if int((ma & mb).sum()) < MIN_PIXELS:
        return PairMeasure()
    sx, sy = _phase_shift(a, ma, b, mb)
    W0 = np.array([[1.0, 0.0, sx], [0.0, 1.0, sy]], np.float32)
    iters = int(_cfg(cfg, "temporal_ecc_iterations", 40))
    eps = float(_cfg(cfg, "temporal_ecc_eps", 1e-5))
    crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, iters, eps)
    Ws = _ecc_from(a, ma, b, mb, W0, crit)
    out = _measure_warp(a, ma, b, mb, W0 if Ws is None else Ws, Ws is not None)
    shot_cc = float(_cfg(cfg, "temporal_shot_cc", 0.8))
    if (Ws is None or not (out.cc >= shot_cc)) and math.hypot(sx, sy) > IDENTITY_START_PX:
        Wi = _ecc_pyramid(a, ma, b, mb, crit)
        if Wi is not None:
            alt = _measure_warp(a, ma, b, mb, Wi, True)
            if math.isfinite(alt.cc) and not (alt.cc <= out.cc):
                out = alt
    return out


# ---------------------------------------------------------------------------------------------
# signature of a sequence
# ---------------------------------------------------------------------------------------------

Getter = Callable[[int], "tuple[np.ndarray, np.ndarray] | None"]


@dataclass
class Signature:
    """Pair measurements of a sequence: ``d1[k]`` = (k, k+1), ``d2[k]`` = (k, k+2)."""
    d1: dict[int, PairMeasure] = field(default_factory=dict)
    d2: dict[int, PairMeasure] = field(default_factory=dict)

    def r1(self, k: int) -> float:
        p = self.d1.get(int(k))
        return float("nan") if p is None else p.r

    def r2(self, k: int) -> float:
        p = self.d2.get(int(k))
        return float("nan") if p is None else p.r


def measure(get: Getter, ks: Iterable[int], cfg: Any = None, pairs: Iterable[tuple[int, int]] | None = None,
            gaps: Sequence[int] = (1, 2), same: Callable[[int, int], bool] | None = None) -> Signature:
    """Measure the pairs (k, k+g) for g in ``gaps`` over the frames ``ks`` (both frames in ``ks``), or the
    explicit ``pairs`` [(k, g)]. ``get(k)`` -> (prepared image, mask) or None (frame not usable); masks of a
    pair are each frame's own (``align_pair`` intersects them after the warp). ``same(k, k2)`` (optional)
    says whether two frames are comparable (same ROI); pairs it rejects are skipped. Frames are fetched once
    each (small LRU) in ascending order."""
    sig = Signature()
    kset = sorted({int(k) for k in ks})
    have = set(kset)
    todo: list[tuple[int, int]] = []
    if pairs is not None:
        todo = sorted({(int(k), int(g)) for k, g in pairs})
    else:
        for k in kset:
            for g in gaps:
                if k + g in have:
                    todo.append((k, int(g)))
    cache: dict[int, Any] = {}

    def fetch(k: int):
        if k not in cache:
            if len(cache) > 8:
                for old in sorted(cache)[:len(cache) - 6]:
                    cache.pop(old, None)
            cache[k] = get(k)
        return cache[k]

    for k, g in todo:
        if same is not None and not same(k, k + g):
            continue
        fa, fb = fetch(k), fetch(k + g)
        if fa is None or fb is None or fa[0].shape != fb[0].shape:
            continue
        pm = align_pair(fa[0], fa[1], fb[0], fb[1], cfg)
        (sig.d1 if g == 1 else sig.d2 if g == 2 else {})[k] = pm
    return sig


# ---------------------------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------------------------

@dataclass
class Labels:
    """Per pair k (frames k, k+1): ``label[k]`` in {repeat, move, unknown, cut}; ``shot[k]`` its competitor
    shot id; ``shots`` [{id, pairs: [k0, k1], mode, floor, threshold, gap_ratio, growth}] where ``floor`` is
    the measured repeat residual level (max of the lower cluster) and ``threshold`` the repeat/move split
    (geometric middle of the gap), both None without a repeat cluster."""
    label: dict[int, str] = field(default_factory=dict)
    shot: dict[int, int] = field(default_factory=dict)
    shots: list[dict] = field(default_factory=list)
    growth: dict[int, float] = field(default_factory=dict)

    def get(self, k: int) -> str:
        return self.label.get(int(k), UNKNOWN)

    def shot_info(self, k: int) -> dict | None:
        i = self.shot.get(int(k))
        return None if i is None else self.shots[i]

    def counts(self) -> dict[str, int]:
        out = {REPEAT: 0, MOVE: 0, UNKNOWN: 0, CUT: 0}
        for v in self.label.values():
            out[v] = out.get(v, 0) + 1
        return out


def _growth(sig: Signature, k: int, r1: dict[int, float]) -> list[float]:
    """Interchangeability ratios of pair (k, k+1): r(k, k+2) / max(r(k, k+1), r(k+1, k+2)) and
    r(k-1, k+1) / max(r(k-1, k), r(k, k+1)), where measured. ~1 when k and k+1 show the same image (each
    relates to the third frame alike), > 1 when the content moves between them (two steps grow)."""
    out = []
    a, b = r1.get(k), r1.get(k + 1)
    r2 = sig.r2(k)
    if a is not None and b is not None and math.isfinite(r2):
        out.append(r2 / max(a, b))
    a0 = r1.get(k - 1)
    r2l = sig.r2(k - 1)
    if a0 is not None and r1.get(k) is not None and math.isfinite(r2l):
        out.append(r2l / max(a0, r1[k]))
    return out


def label_pairs(sig: Signature, cfg: Any = None, breaks: Iterable[int] = ()) -> Labels:
    """Label every measured pair of ``sig`` (module docstring). Shots are maximal runs of consecutive measured
    pairs with cc >= temporal_shot_cc; ``breaks`` (pair indices) split shots too (e.g. a layout-period change)."""
    shot_cc = float(_cfg(cfg, "temporal_shot_cc", 0.8))
    gap_ratio = float(_cfg(cfg, "temporal_gap_ratio", 2.5))
    growth_ratio = float(_cfg(cfg, "temporal_growth_ratio", 1.5))
    out = Labels()
    brk = {int(b) for b in breaks}
    ks = sorted(k for k, p in sig.d1.items() if math.isfinite(p.cc))
    runs: list[list[int]] = []
    for k in ks:
        p = sig.d1[k]
        if p.cc < shot_cc or k in brk:
            out.label[k] = CUT if p.cc < shot_cc else UNKNOWN
            if runs and runs[-1]:
                runs.append([])
            continue
        if runs and runs[-1] and runs[-1][-1] == k - 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    for run in (r for r in runs if r):
        sid = len(out.shots)
        r1 = {k: sig.d1[k].r for k in run}
        info: dict[str, Any] = {"id": sid, "pairs": [run[0], run[-1]], "mode": "short", "floor": None,
                                "threshold": None, "gap_ratio": None, "growth": None}
        out.shots.append(info)
        for k in run:
            out.shot[k] = sid
            out.label[k] = UNKNOWN
            g = _growth(sig, k, r1)
            if g:
                out.growth[k] = round(max(g), 4)
        if len(run) < 2:
            continue
        vals = np.sort(np.log(np.array([r1[k] for k in run], np.float64)))
        gaps = np.diff(vals)
        i = int(np.argmax(gaps))
        ratio = float(math.exp(gaps[i]))
        info["gap_ratio"] = round(ratio, 3)
        if ratio >= gap_ratio:
            lo_max, hi_min = float(math.exp(vals[i])), float(math.exp(vals[i + 1]))
            thr = math.sqrt(lo_max * hi_min)
            info.update(mode="split", floor=round(lo_max, 7), threshold=round(thr, 7))
            for k in run:
                if r1[k] >= thr:
                    out.label[k] = MOVE
                    continue
                g = _growth(sig, k, r1)
                out.label[k] = REPEAT if g and max(g) <= growth_ratio else UNKNOWN
            continue
        grow = [max(g) for k in run for g in [_growth(sig, k, r1)] if g]
        med = float(np.median(grow)) if grow else float("nan")
        info["growth"] = None if not math.isfinite(med) else round(med, 4)
        if math.isfinite(med) and med > growth_ratio and len(grow) >= 2:
            # no repeat cluster, but the residual grows with the frame distance: the content moves. Only pairs
            # with their own growth evidence are MOVE (a lone repeat inside, ~1, stays unknown)
            info["mode"] = "moving"
            for k in run:
                g = _growth(sig, k, r1)
                if g and max(g) > growth_ratio:
                    out.label[k] = MOVE
        else:
            info["mode"] = "undecided"         # an all-repeat static run, a noise plate or saturated motion: never evidence
    return out


def summary(labels: Labels) -> dict:
    """JSON-friendly summary of the labels: counts, shots and the label runs."""
    runs: dict[str, list[list[int]]] = {}
    for lab in (REPEAT, MOVE, UNKNOWN, CUT):
        fr = sorted(k for k, v in labels.label.items() if v == lab)
        rr: list[list[int]] = []
        for k in fr:
            if rr and rr[-1][1] == k - 1:
                rr[-1][1] = k
            else:
                rr.append([k, k])
        runs[lab] = rr
    return {"counts": labels.counts(), "shots": labels.shots[:500], "runs": {k: v[:200] for k, v in runs.items()}}


def local_labels(get: Getter, k0: int, k1: int, cfg: Any = None, same: Callable[[int, int], bool] | None = None) -> Labels:
    """Labels of the pairs inside frames [k0, k1] (measured d1 and d2) -- e.g. around one cut."""
    ks = list(range(int(k0), int(k1) + 1))
    return label_pairs(measure(get, ks, cfg, same=same), cfg)
