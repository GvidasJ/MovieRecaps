"""Stage 9 verification (DESIGN.md §5 verify.py): acceptance criteria c1..c6 and checks s9_1..s9_7.

``verify_all(ctx)`` returns::

    {'criteria': {c1_coverage, c2_cuts, c3_source_frames, c4_speed_framing, c5_audio, c6_after_effects}:
                 {'status': 'pass'|'fail'|'pass_with_exceptions'|'not_available', 'summary': str,
                  'details': {...}},
     'checks':   {s9_1_coverage, s9_2_ae_sim, s9_3_visual, s9_4_cut_images, s9_5_audio, s9_6_ae_render,
                  s9_7_determinism, s9_8_deliverables}: {'status', 'summary', 'failures': [...], ...},
     'failures': [str, ...]}

Mapping: c1 <- s9_1; c2 <- an independent per-cut check (+ s9_4 cut images); c3 <- s9_2 (AE-semantics
simulation of the ae_plan AND of the mock-run record, compared with refine's PRE-segmentation measurement
and with the cutlist) + s9_3 (visual, + a probe of the delivered preview); c4 <- speed / framing / flip /
rotation (framing and flip measured independently on sampled frames); c5 <- s9_5; c6 <- mock-run checks +
s9_6 (aerender, when installed). s9_7 (determinism) and s9_8 (deliverables) count like the criteria
(DESIGN §7 D4/D5).

Every check function below takes plain data (segments, FrameMap, arrays, callables) so it can be unit
tested without the analysis modules; ``verify_all`` wires them to a ``pipeline.Context``.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import subprocess
import traceback
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

import numpy as np

from .common import fps_str, json_default, log, parse_fps, timecode
from .geometry import Sim, interpolate_keys
from .model import Box, FrameMap, Segment, Status

CRITERIA = ("c1_coverage", "c2_cuts", "c3_source_frames", "c4_speed_framing", "c5_audio", "c6_after_effects")
CHECKS = ("s9_1_coverage", "s9_2_ae_sim", "s9_3_visual", "s9_4_cut_images", "s9_5_audio", "s9_6_ae_render",
          "s9_7_determinism", "s9_8_deliverables")
STATUSES = ("pass", "pass_with_exceptions", "fail", "not_available")
AUDIO_EXCEPTION_CODES = frozenset({"too_short", "not_in_raw", "audio_replaced", "pitch_preserved",
                                   "music_dominated", "no_audio"})
MAIN_COMP_NAME = "Recreated Edit"
KEY_HOLD = 6614            # KeyframeInterpolationType.HOLD enum value in AE
MAX_FAILURE_IMAGES = 200
AE_EPS = 1e-9              # the AE floor rule's epsilon (phase_solve.ae_frame / export_ae)
PLAN_FRAME_TOL = 1e-6      # plan vs cutlist: RAW-frame positions this close to a frame boundary may floor either way
PLAN_ALPHA_TOL = 0.02      # plan vs cutlist: max |simulated - declared| transition opacity (0..1)
PLAN_SOLID_MAX_WEIGHT = 0.02   # RAW contribution allowed on a dip / flash / NOT-IN-RAW frame of the plan
# c4 independent framing measurement (verification-honesty F5)
FRAMING_SAMPLE_STEP = 5    # every n-th matched frame (+ the ends and the key frames) of each segment
FRAMING_MAX_SAMPLES = 60   # regular samples over the whole edit (the step grows for long edits)
FRAMING_PERTURB = (0.02, 3.0)  # ECC starts from the model scaled by +-2 % and shifted by +-3 px (comp px)
FRAMING_BAD_FRAC = 0.2     # more than this fraction of measured samples off by > tolerance -> fail
# c5: wide search for grossly misaligned audio (verification-honesty F6)
AUDIO_WIDE_LAG_S = 2.0
# s9_3: delivered-preview check (verification-honesty F12)
PREVIEW_SAMPLES = 24       # delivered frames compared with render_frame in the MAIN mode
PREVIEW_ZNCC = 0.98
UNIFORM_MEAN_TOL = 24.0    # s9_3: dip / flash frames, |competitor - recreation| mean luma (8-bit)


# ---------------------------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------------------------

def aggregate(statuses: Iterable[str | None]) -> str:
    """Combine statuses: any fail -> fail; all not_available (or none) -> not_available; any
    pass_with_exceptions -> pass_with_exceptions; else pass. not_available parts are ignored otherwise."""
    st = [s for s in statuses if s]
    if any(s == "fail" for s in st):
        return "fail"
    real = [s for s in st if s != "not_available"]
    if not real:
        return "not_available"
    if any(s == "pass_with_exceptions" for s in real):
        return "pass_with_exceptions"
    if all(s == "pass" for s in real):
        return "pass"
    return "fail"      # unknown status strings never pass


def _status_from(n_fail: int, n_exc: int) -> str:
    return "fail" if n_fail else ("pass_with_exceptions" if n_exc else "pass")


def crashed_result(error: str) -> dict:
    """verify_all() result when verification itself crashed: every criterion fails."""
    crit = {c: {"status": "fail", "summary": f"verification crashed: {error}", "details": {}} for c in CRITERIA}
    checks = {c: {"status": "fail", "summary": "not run (verification crashed)", "failures": [error]} for c in CHECKS}
    return {"criteria": crit, "checks": checks, "failures": [f"verification crashed: {error}"]}


def _get(d: Any, *names: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _ranges(frames: Iterable[int]) -> list[list[int]]:
    fr = sorted(set(int(f) for f in frames))
    out: list[list[int]] = []
    for f in fr:
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def _seg_name(s: Segment) -> str:
    return f"S{int(s.id):02d}"


# ---------------------------------------------------------------------------------------------
# s9_1 coverage (criterion 1)
# ---------------------------------------------------------------------------------------------

def _transition_dict(t: Any) -> dict | None:
    if t is None:
        return None
    if isinstance(t, dict):
        return t
    return dict(vars(t))


def check_coverage(segments: Sequence[Segment], n_frames: int, layout_block: dict | None = None) -> dict:
    """Segments + placeholders tile [0, n_frames) exactly; overlaps only where a measured transition
    explains them (crossfade/dip of exactly the overlap length); raw segments are mapped (raw_in or
    remap keys); placeholders are labelled; extra video regions -> pass_with_exceptions."""
    n = int(n_frames)
    failures: list[str] = []
    exceptions: list[str] = []
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    count = np.zeros(max(n, 0), np.int32)
    for s in segs:
        if not (0 <= s.comp_in < s.comp_out <= n):
            failures.append(f"{_seg_name(s)}: invalid range [{s.comp_in}, {s.comp_out}) for {n} frames")
            a, b = max(0, s.comp_in), min(n, s.comp_out)
        else:
            a, b = s.comp_in, s.comp_out
        if b > a:
            count[a:b] += 1
        if s.type == "raw" and s.raw_in_seconds is None and not s.time_remap_keys:
            failures.append(f"{_seg_name(s)}: raw segment without a RAW mapping (raw_in_seconds / remap keys)")
        if s.type == "not_in_raw" and not (s.label or "").strip():
            failures.append(f"{_seg_name(s)}: NOT-IN-RAW placeholder without a label")
        if s.type not in ("raw", "not_in_raw", "dip", "flash"):
            failures.append(f"{_seg_name(s)}: unknown segment type {s.type!r}")
    gaps = _ranges(np.nonzero(count == 0)[0]) if n else []
    for a, b in gaps:
        failures.append(f"gap: competitor frames {a}-{b} are not covered")
    overlaps = _ranges(np.nonzero(count >= 2)[0]) if n else []
    explained: list[dict] = []
    for a, b in overlaps:
        length = b - a + 1
        if int(count[a:b + 1].max()) > 2:
            failures.append(f"frames {a}-{b} covered by more than two segments")
            continue
        ok = False
        for x in segs:
            for y in segs:
                if x is y or not (x.comp_in <= y.comp_in):
                    continue
                if y.comp_in != a or x.comp_out != b + 1:
                    continue
                ti, to = _transition_dict(y.transition_in), _transition_dict(x.transition_out)
                for t in (ti, to):
                    if t and int(t.get("duration_frames", -1)) == length:
                        ok = True
                        explained.append({"frames": [a, b], "from": x.id, "to": y.id, "type": t.get("type")})
                        break
                if ok:
                    break
            if ok:
                break
        if not ok:
            failures.append(f"unexplained overlap: competitor frames {a}-{b} ({length} frames) covered twice")
    covered = int((count > 0).sum()) if n else 0
    if segs and (min(s.comp_in for s in segs) != 0 or max(s.comp_out for s in segs) != n):
        failures.append(f"timeline spans [{min(s.comp_in for s in segs)}, {max(s.comp_out for s in segs)}) "
                        f"instead of [0, {n})")
    if not segs:
        failures.append("no segments")
    region_frames: list[list[int]] = []
    lb = layout_block or {}
    all_periods = [p for p in (lb.get("periods") or []) if isinstance(p, dict)]
    periods = [p for p in all_periods if str(p.get("mode")) in ("split", "pip")]
    if periods:
        for p in periods:
            region_frames.append([int(p["comp_in"]), int(p["comp_out"]) - 1])
    elif lb.get("regions"):
        region_frames.append([0, n - 1])
    if region_frames:
        exceptions.append(f"{len(lb.get('regions') or periods)} extra video region(s) (split-screen / PiP) not "
                          "recreated in frames " + ", ".join(f"{a}-{b}" for a, b in region_frames))
    multi = [s for s in segs if int(getattr(s, "region", 0) or 0) >= 2]
    if multi:
        exceptions.append("segments of an unsupported video region (split-screen / PiP): "
                          + ", ".join(f"{_seg_name(s)} (region {s.region})" for s in multi))
    # full-screen periods (DESIGN §7 D1) are reproduced: every RAW segment in them carries its own box
    fullscreen: list[list[int]] = []
    for p in all_periods:
        if str(p.get("mode")) != "fullscreen":
            continue
        a, b = int(p["comp_in"]), int(p["comp_out"])
        fullscreen.append([a, b - 1])
        boxed = [s for s in segs if s.type == "raw" and s.comp_in < b and s.comp_out > a and not s.box]
        for s in boxed:
            lo, hi = max(a, s.comp_in), min(b, s.comp_out) - 1
            failures.append(f"{_seg_name(s)}: frames {lo}-{hi} show the video full-screen in the competitor but the "
                            "segment has no box (rebuilt inside the dominant video box)")
    status = _status_from(len(failures), len(exceptions))
    n_ph = sum(1 for s in segs if s.type == "not_in_raw")
    summary = (f"{len(segs)} segments ({n_ph} NOT-IN-RAW), {covered}/{n} frames covered, "
               f"{len(gaps)} gaps, {len(overlaps)} overlaps ({len(explained)} transitions)")
    if fullscreen:
        summary += f", {len(fullscreen)} full-screen period(s)"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions,
            "frames": n, "covered": covered, "gaps": gaps, "overlaps": overlaps, "transitions": explained,
            "extra_region_frames": region_frames, "fullscreen_frames": fullscreen}


# ---------------------------------------------------------------------------------------------
# Segment models: predicted RAW frame and transform at a competitor frame
# ---------------------------------------------------------------------------------------------

def _phase():
    from . import phase_solve
    return phase_solve


def seg_raw_frame(seg: Segment, k: int, comp_fps: Fraction, raw_fps: Fraction, n_raw: int | None = None) -> int | None:
    """RAW frame the segment's time model shows at comp frame k (AE rule, extrapolated past the ends)."""
    if seg.type != "raw":
        return None
    if seg.time_remap_keys:
        from .pipeline import remap_raw_seconds
        t = remap_raw_seconds(seg.time_remap_keys, k)
        j = None if t is None else int(math.floor(t * float(raw_fps) + 1e-9))
    elif seg.raw_in_seconds is None:
        return None
    else:
        j = int(_phase().ae_frame(float(seg.raw_in_seconds), float(seg.speed), int(k), int(seg.comp_in), comp_fps, raw_fps))
    if j is None or j < 0 or (n_raw is not None and j >= n_raw):
        return None
    return j


def seg_sim(seg: Segment, k: float, raw_w: float, raw_h: float) -> Sim | None:
    """Segment transform at comp frame k (AE-linear keys, held outside the key range)."""
    if seg.transform_keys:
        return interpolate_keys(seg.transform_keys, k, raw_w, raw_h)
    if seg.transform:
        return Sim.from_dict(seg.transform)
    return None


def _sims_close(a: Sim | None, b: Sim | None) -> bool:
    if a is None or b is None:
        return False
    return (abs(a.s - b.s) <= 1e-6 * max(1.0, abs(a.s)) and abs(a.theta_deg - b.theta_deg) <= 1e-6
            and abs(a.tx - b.tx) <= 1e-4 and abs(a.ty - b.ty) <= 1e-4)


# ---------------------------------------------------------------------------------------------
# Scoring on the analysis proxies (same masked ZNCC as refine / segment)
# ---------------------------------------------------------------------------------------------

def proxy_roi(box: Box | dict | None, size: tuple[int, int], ratio: tuple[float, float]) -> tuple[int, int, int, int]:
    """Integer ROI (x, y, w, h) of the video box at proxy resolution (whole frame without a box)."""
    w, h = int(size[0]), int(size[1])
    if box is None:
        return 0, 0, w, h
    b = Box.from_dict(box) if isinstance(box, dict) else box
    x, y, bw, bh = b.scaled(float(ratio[0]), float(ratio[1])).int_roi()
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + bw), min(h, y + bh)
    if x1 <= x0 or y1 <= y0:
        return 0, 0, w, h
    return x0, y0, x1 - x0, y1 - y0


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    import cv2
    img = img.astype(np.float32, copy=False)
    return cv2.GaussianBlur(img, (0, 0), float(sigma)) if sigma and sigma > 0 else img


Cand = tuple  # (raw_frame: int, sim: Sim, flip: bool)


class ProxyScorer:
    """Masked ZNCC of competitor proxy frames against candidate RAW proxy frames, each candidate warped
    with ITS OWN transform; all candidates of one call share the intersection of their valid masks, so
    the scores are directly comparable (DESIGN §5 scoring).

    ``box_fn(k)`` (optional) gives the video box in force at frame k (a full-screen period's whole canvas,
    DESIGN §7 D1); without it every frame is scored in ``box``."""

    def __init__(self, comp: Any, raw: Any, box: Box | dict | None, allowed_fn: Callable[[int], np.ndarray | None],
                 raw_w: float, cfg: Any, min_pixels: int = 256,
                 box_fn: Callable[[int], Box | dict | None] | None = None):
        self.comp, self.raw, self.cfg = comp, raw, cfg
        self.roi = proxy_roi(box, comp.size, comp.ratio)
        self.box_fn = box_fn
        self.allowed_fn = allowed_fn
        self.raw_w = float(raw_w)
        self.blur = float(getattr(cfg, "score_blur", 1.0))
        self.grad_weight = float(getattr(cfg, "grad_weight", 0.0))
        self.min_pixels = int(min_pixels)

    def roi_at(self, k: int) -> tuple[int, int, int, int]:
        if self.box_fn is None:
            return self.roi
        return proxy_roi(self.box_fn(int(k)), self.comp.size, self.comp.ratio)

    def _region(self, k: int):
        from . import scoring
        if not (0 <= k < self.comp.n) or not self.comp.has(k):
            return None
        return scoring.prepare_comp(np.asarray(self.comp.get(k)), self.roi_at(k), self.allowed_fn(k), blur=self.blur,
                                    with_grad=self.grad_weight > 0)

    def _warp(self, cand: Cand | None, roi: tuple[int, int, int, int] | None = None):
        from . import scoring
        if cand is None:
            return None
        j, sim, flip = cand
        if j is None or sim is None or not self.raw.has(int(j)):
            return None
        w, v = scoring.warp_to_roi(np.asarray(self.raw.get(int(j))), sim, bool(flip), self.raw_w, self.raw.ratio,
                                   self.comp.ratio, self.roi if roi is None else roi)
        return _blur(w, self.blur), v

    def score(self, k: int, cands: Sequence[Cand | None]) -> np.ndarray:
        from . import scoring
        out = np.full(len(cands), np.nan)
        region = self._region(k)
        if region is None:
            return out
        warped = [self._warp(c, region.roi) for c in cands]
        valid = region.mask.copy()
        for w in warped:
            if w is not None:
                valid &= w[1]
        if int(valid.sum()) < self.min_pixels:
            return out
        for i, w in enumerate(warped):
            if w is None:
                continue
            s = scoring.zncc(region.img, w[0], valid)
            if self.grad_weight > 0:
                g = region.grad if region.grad is not None else scoring._gradmag(region.img)
                s = (1 - self.grad_weight) * s + self.grad_weight * scoring.zncc(g, scoring._gradmag(w[0]), valid)
            out[i] = s
        return out

    def blend(self, k: int, a: Cand | None, b: Cand | None) -> tuple[float, float]:
        """(alpha_B, zncc_of_fit) of comp[k] ~ (1-alpha_B)*A + alpha_B*B (scoring.fit_blend)."""
        from . import scoring
        region = self._region(k)
        if region is None:
            return float("nan"), float("nan")
        wa, wb = self._warp(a, region.roi), self._warp(b, region.roi)
        if wa is None or wb is None:
            return float("nan"), float("nan")
        valid = region.mask & wa[1] & wb[1]
        alpha_a, _res, z = scoring.fit_blend(region, wa[0], wb[0], valid)
        return 1.0 - alpha_a, z

    def uniform(self, k: int) -> tuple[float, float]:
        from . import scoring
        if not (0 <= k < self.comp.n):
            return float("nan"), float("nan")
        return scoring.region_stats(np.asarray(self.comp.get(k)), self.roi_at(k), self.allowed_fn(k))


# ---------------------------------------------------------------------------------------------
# c2 cuts (independent check)
# ---------------------------------------------------------------------------------------------

def _pair_kind(a: Segment, b: Segment) -> str:
    if a.type == "raw" and b.type == "raw":
        t = _transition_dict(b.transition_in) or _transition_dict(a.transition_out)
        if t and t.get("type") == "crossfade":
            return "crossfade"
        return "hard"
    if a.type == "raw" and b.type == "not_in_raw":
        return "raw_to_placeholder"
    if a.type == "not_in_raw" and b.type == "raw":
        return "placeholder_to_raw"
    if a.type == "raw" and b.type in ("dip", "flash"):
        return "raw_to_uniform"
    if a.type in ("dip", "flash") and b.type == "raw":
        return "uniform_to_raw"
    return "other"


class _Models:
    def __init__(self, comp_fps: Fraction, raw_fps: Fraction, raw_wh: tuple[float, float], n_raw: int | None):
        self.comp_fps, self.raw_fps, self.raw_wh, self.n_raw = Fraction(comp_fps), Fraction(raw_fps), raw_wh, n_raw

    def cand(self, seg: Segment, k: int) -> Cand | None:
        j = seg_raw_frame(seg, k, self.comp_fps, self.raw_fps, self.n_raw)
        sim = seg_sim(seg, k, *self.raw_wh)
        if j is None or sim is None:
            return None
        return (j, sim, bool(seg.flip_h))


def _side(scorer: Any, k: int, own: Cand | None, other: Cand | None) -> dict:
    """Does comp frame k match its own segment model better than the other one?"""
    res: dict[str, Any] = {"k": int(k), "own": None if own is None else int(own[0]),
                           "other": None if other is None else int(other[0])}
    if own is None:
        res.update(result="unscorable", reason="own model predicts no RAW frame")
        return res
    if other is not None and own[0] == other[0] and own[2] == other[2] and _sims_close(own[1], other[1]):
        res.update(result="indistinguishable", reason="both models predict the same RAW frame and framing")
        return res
    s = scorer.score(k, [own, other])
    s_own, s_other = float(s[0]), float(s[1]) if len(s) > 1 else float("nan")
    res.update(s_own=None if math.isnan(s_own) else round(s_own, 6),
               s_other=None if math.isnan(s_other) else round(s_other, 6))
    if math.isnan(s_own):
        res.update(result="unscorable", reason="too few scorable pixels")
        return res
    if other is None or math.isnan(s_other):
        s_other = -math.inf
    res["result"] = "ok" if s_own > s_other else "fail"
    return res


def fit_crossfade_window(rows: Sequence[tuple[int, float]]) -> tuple[int, int] | None:
    """(O, D) of a linear crossfade alpha_B(k) = (k - O) / D fitted to measured (k, alpha_B) pairs the way
    segment.py finds crossfades: least squares over the ramp frames (0.02 < alpha < 0.98), O = round(zero
    crossing), D = round(1 / slope); one ramp frame: D = 2 around alpha 0.5, else D from its alpha. None
    when nothing ramps."""
    ramp = [(int(k), float(a)) for k, a in rows if a is not None and math.isfinite(a) and 0.02 < a < 0.98]
    if not ramp:
        return None
    if len(ramp) >= 2:
        ks = np.array([k for k, _ in ramp], np.float64)
        al = np.array([a for _, a in ramp], np.float64)
        slope, icpt = np.polyfit(ks, al, 1)
        if slope <= 1e-6:
            return None
        return int(round(-icpt / slope)), int(round(1.0 / slope))
    k1, a1 = ramp[0]
    if abs(a1 - 0.5) < 0.2:
        return k1 - 1, 2
    if a1 < 0.5:
        return k1 - 1, max(2, int(round(1.0 / a1)))
    d = max(2, int(round(1.0 / (1.0 - a1))))
    return k1 - (d - 1), d


def _layout_change(a: Segment, b: Segment) -> bool:
    """A and B are shown in different layout regions / boxes (a layout cut, DESIGN §7 D1)."""
    return (a.box or None) != (b.box or None) or int(getattr(a, "region", 0) or 0) != int(getattr(b, "region", 0) or 0)


def check_cuts(segments: Sequence[Segment], comp_fps: Fraction, raw_fps: Fraction, raw_wh: tuple[float, float],
               n_raw: int | None, scorer: Any, cfg: Any) -> dict:
    """Criterion 2, independent of segment.py: at every cut A|B, comp frame comp_out(A)-1 scores higher
    against A's model (phase_solve.ae_frame + A's transform) than against B's model extended back, and
    comp frame comp_in(B) the reverse. A hard cut where both models show the same RAW frame, flip and
    framing on both sides (no discontinuity in m(k)) is a spurious cut and fails, unless it is a
    speed-only cut (cut_ambiguity) or a layout change. Crossfades: the fitted alpha ramp must follow the
    declared one, the window (O, D) re-fitted from the alpha measured over O-3 .. O+D+2 must equal the
    declared one (off by one frame = fail), frame O (and O-1) must be pure A and O+D pure B. NOT-IN-RAW
    neighbours: the placeholder frame scores below none_thresh against the extended neighbour model.
    Dips/flashes: the uniform side is uniform."""
    models = _Models(comp_fps, raw_fps, raw_wh, n_raw)
    none_thresh = float(getattr(cfg, "none_thresh", 0.6))
    uniform_std = float(getattr(cfg, "uniform_std", 4.0))
    alpha_tol = float(getattr(cfg, "verify_alpha_tol", 0.15))
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    cuts: list[dict] = []
    failures: list[str] = []
    exceptions: list[str] = []
    for a, b in zip(segs[:-1], segs[1:]):
        kind = _pair_kind(a, b)
        c: dict[str, Any] = {"from": a.id, "to": b.id, "frame": int(b.comp_in), "kind": kind,
                             "tc": timecode(int(b.comp_in), comp_fps)}
        sides: list[dict] = []
        notes: list[str] = []
        if kind == "hard":
            ka, kb = a.comp_out - 1, b.comp_in
            sides.append({"side": "A_last", **_side(scorer, ka, models.cand(a, ka), models.cand(b, ka))})
            sides.append({"side": "B_first", **_side(scorer, kb, models.cand(b, kb), models.cand(a, kb))})
            if (all(sd.get("result") == "indistinguishable" for sd in sides) and not (a.cut_ambiguity or b.cut_ambiguity)
                    and not _layout_change(a, b)):
                for sd in sides:
                    sd.update(result="fail", reason="no discontinuity in m(k): spurious cut (both segment models "
                                                    "show the same RAW frame, flip and framing on both sides)")
        elif kind == "crossfade":
            t = _transition_dict(b.transition_in) or _transition_dict(a.transition_out)
            d = int(t.get("duration_frames", 0))
            o = int(b.comp_in)
            alpha = list(t.get("alpha") or [])
            if len(alpha) != d:
                alpha = [i / d for i in range(d)] if d > 0 else []
            if a.comp_out != o + d:
                sides.append({"side": "overlap", "result": "fail",
                              "reason": f"A ends at {a.comp_out}, expected O+D = {o + d}"})
            errs = []
            fit_rows: dict[int, float] = {}
            for k in range(max(a.comp_in, o - 3), min(b.comp_out, o + d + 3)):
                ab, z = scorer.blend(k, models.cand(a, k), models.cand(b, k))
                ab, z = float(ab), float(z)
                if math.isfinite(ab):
                    fit_rows[k] = ab
                if o <= k < o + d:
                    errs.append({"k": k, "alpha_declared": round(float(alpha[k - o]), 4),
                                 "alpha_fit": None if math.isnan(ab) else round(ab, 4),
                                 "zncc_fit": None if math.isnan(z) else round(z, 4)})
            fitted = [e for e in errs if e["alpha_fit"] is not None]
            max_err = max((abs(e["alpha_fit"] - e["alpha_declared"]) for e in fitted), default=float("nan"))
            c["alpha"] = errs
            c["alpha_max_err"] = None if math.isnan(max_err) else round(max_err, 4)
            if not fitted:
                sides.append({"side": "alpha", "result": "unscorable", "reason": "no blend fit"})
            else:
                sides.append({"side": "alpha", "result": "ok" if max_err <= alpha_tol else "fail",
                              "reason": f"max |alpha_fit - alpha| = {max_err:.3f} (tol {alpha_tol})"})
            # the window itself: re-fit (O, D) from the measured ramp (time-math F4)
            fit = fit_crossfade_window(sorted(fit_rows.items()))
            c["window_fit"] = None if fit is None else {"O": fit[0], "D": fit[1]}
            if d > 0 and fit is None:
                sides.append({"side": "window", "result": "unscorable", "reason": "no ramp frames to fit (O, D)"})
            elif d > 0:
                ok = fit == (o, d)
                sides.append({"side": "window", "result": "ok" if ok else "fail",
                              "reason": f"measured ramp gives O={fit[0]} D={fit[1]}, declared O={o} D={d}"})
            pure = max(0.5 / d, 0.05) if d > 0 else 0.05
            for kk, want_b in ((o - 1, False), (o, False), (o + d, True)):
                if kk not in fit_rows:
                    continue
                ab = fit_rows[kk]
                good = ab >= 1.0 - pure if want_b else ab <= pure
                if not good:
                    sides.append({"side": "pure_B" if want_b else "pure_A", "k": int(kk), "result": "fail",
                                  "reason": f"frame {kk} should be pure {'B' if want_b else 'A'} but alpha_B = {ab:.3f}"})
            if o - 1 >= a.comp_in:
                sides.append({"side": "A_before", **_side(scorer, o - 1, models.cand(a, o - 1), models.cand(b, o - 1))})
            if o + d < b.comp_out:
                sides.append({"side": "B_after", **_side(scorer, o + d, models.cand(b, o + d), models.cand(a, o + d))})
        elif kind in ("raw_to_placeholder", "placeholder_to_raw"):
            r, ph = (a, b) if kind == "raw_to_placeholder" else (b, a)
            k_r = r.comp_out - 1 if kind == "raw_to_placeholder" else r.comp_in
            k_p = ph.comp_in if kind == "raw_to_placeholder" else ph.comp_out - 1
            s_r = float(scorer.score(k_r, [models.cand(r, k_r)])[0])
            cp = models.cand(r, k_p)
            s_p = float(scorer.score(k_p, [cp])[0]) if cp is not None else float("nan")
            sides.append({"side": "raw_frame", "k": int(k_r), "s_own": None if math.isnan(s_r) else round(s_r, 6),
                          "result": "unscorable" if math.isnan(s_r) else ("ok" if s_r >= none_thresh else "fail")})
            sides.append({"side": "placeholder_frame", "k": int(k_p), "s_ext": None if math.isnan(s_p) else round(s_p, 6),
                          "result": "ok" if (math.isnan(s_p) or s_p < none_thresh) else "fail",
                          "reason": f"placeholder vs extended {_seg_name(r)} model must be < none_thresh {none_thresh}"})
        elif kind in ("raw_to_uniform", "uniform_to_raw"):
            r, u = (a, b) if kind == "raw_to_uniform" else (b, a)
            k_r = r.comp_out - 1 if kind == "raw_to_uniform" else r.comp_in
            k_u = u.comp_in if kind == "raw_to_uniform" else u.comp_out - 1
            s_r = float(scorer.score(k_r, [models.cand(r, k_r)])[0])
            mean, std = scorer.uniform(k_u)
            sides.append({"side": "raw_frame", "k": int(k_r), "s_own": None if math.isnan(s_r) else round(s_r, 6),
                          "result": "unscorable" if math.isnan(s_r) else ("ok" if s_r >= none_thresh else "fail")})
            sides.append({"side": "uniform_frame", "k": int(k_u), "std": None if math.isnan(std) else round(float(std), 3),
                          "result": "unscorable" if math.isnan(std) else ("ok" if std < uniform_std else "fail")})
        else:
            sides.append({"side": "n/a", "result": "ok", "reason": f"{a.type} -> {b.type}: nothing to compare"})
        amb = b.cut_ambiguity or a.cut_ambiguity
        for sd in sides:
            if sd.get("result") == "fail" and amb and "k" in sd and amb[0] - 1 <= sd["k"] <= amb[1] + 1:
                sd["result"] = "exempt"
                sd["reason"] = f"speed-only cut: cut position ambiguous within {list(amb)}"
        results = [sd.get("result") for sd in sides]
        if "fail" in results:
            c["status"] = "fail"
            why = []
            for sd in sides:
                if sd.get("result") != "fail":
                    continue
                if sd.get("reason"):
                    why.append(f"{sd['side']}: {sd['reason']}")
                else:
                    why.append(f"{sd['side']} (k={sd.get('k')}): own {sd.get('s_own')} vs other {sd.get('s_other')}")
            failures.append(f"cut {_seg_name(a)}|{_seg_name(b)} at frame {b.comp_in}: " + "; ".join(why))
        elif any(r in ("unscorable", "indistinguishable", "exempt") for r in results):
            c["status"] = "exception"
            exceptions.append(f"cut {_seg_name(a)}|{_seg_name(b)} at frame {b.comp_in}: "
                              + ", ".join(f"{sd['side']}={sd.get('result')}" for sd in sides
                                          if sd.get("result") in ("unscorable", "indistinguishable", "exempt")))
        else:
            c["status"] = "pass"
        c["sides"] = sides
        if notes:
            c["notes"] = notes
        cuts.append(c)
    status = _status_from(len(failures), len(exceptions))
    n_pass = sum(1 for c in cuts if c["status"] == "pass")
    summary = f"{len(cuts)} cuts: {n_pass} verified both sides, {len(exceptions)} exceptions, {len(failures)} failed"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "cuts": cuts}


# ---------------------------------------------------------------------------------------------
# s9_2 AE-semantics simulation vs m(k) (criterion 3)
# ---------------------------------------------------------------------------------------------

def visible_raw_frame(entries: Sequence[dict] | None) -> int | None:
    """RAW frame that fully shows at one MAIN frame. Entries are top -> bottom (integer 'layer' values
    are sorted ascending, AE's 1 = top). With compositing weights (export_ae.simulate_ae), the entry
    whose weight is 1 is the visible one; otherwise the top-most fully opaque layer. Returns None for a
    blend (crossfade interior), when nothing covers the frame, or when a non-RAW layer covers it."""
    ents = [e for e in (entries or []) if not e.get("guide")]
    if ents and all(isinstance(e.get("layer"), (int, np.integer)) for e in ents):
        ents = sorted(ents, key=lambda e: int(e["layer"]))
    if ents and all(e.get("weight") is not None for e in ents):
        for e in ents:
            if float(e["weight"]) >= 0.99999:
                j = e.get("raw_frame")
                return None if j is None else int(j)
        return None
    percent = any(float(e.get("opacity", 100) if e.get("opacity") is not None else 100) > 1.0 + 1e-9 for e in ents)
    for e in ents:
        op = e.get("opacity", 100.0)
        op = 100.0 if op is None else float(op)
        full = op >= 99.999 if percent else op >= 0.99999
        if full:
            j = e.get("raw_frame")
            return None if j is None else int(j)
    return None


def main_to_comp(K: int, comp_fps: Fraction, main_fps: Fraction) -> int:
    """Competitor frame displayed at MAIN frame K: floor(K * comp_fps / main_fps)."""
    if Fraction(main_fps) == Fraction(comp_fps):
        return int(K)
    return math.floor(Fraction(int(K)) * Fraction(comp_fps) / Fraction(main_fps))


def _to_main(k: int, comp_fps: Fraction, main_fps: Fraction) -> int:
    """K = floor(k * main_fps / comp_fps + 1/2) (DESIGN §2.5; identity on the same grid)."""
    if Fraction(main_fps) == Fraction(comp_fps):
        return int(k)
    return math.floor(Fraction(int(k)) * Fraction(main_fps) / Fraction(comp_fps) + Fraction(1, 2))


def _interp_pts(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Linear interpolation, held outside [xs[0], xs[-1]] (xs ascending)."""
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for (x0, y0), (x1, y1) in zip(zip(xs[:-1], ys[:-1]), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return float(y0) if x1 == x0 else float(y0 + (x - x0) / (x1 - x0) * (y1 - y0))
    return float(ys[-1])


class CutlistTimeline:
    """What the cutlist says AE must show on every MAIN frame (independent of export_ae): the segments
    covering MAIN frame K ([to_main(comp_in), to_main(comp_out)), DESIGN §2.5), each RAW segment's RAW
    frame position at K (raw_in re-anchored at K_in, time-remap keys interpolated at K) and the declared
    transition opacity of an overlap."""

    def __init__(self, segments: Sequence[Segment], comp_fps: Fraction, main_fps: Fraction, raw_fps: Fraction,
                 n_raw: int | None = None):
        self.cf, self.mf, self.rf = Fraction(comp_fps), Fraction(main_fps), Fraction(raw_fps)
        self.n_raw = n_raw
        self.segs = sorted(segments, key=lambda s: (s.comp_in, s.comp_out, s.id))
        self.span = {s.id: (_to_main(s.comp_in, self.cf, self.mf), _to_main(s.comp_out, self.cf, self.mf))
                     for s in self.segs}

    def covering(self, K: int) -> list[Segment]:
        return [s for s in self.segs if self.span[s.id][0] <= K < self.span[s.id][1]]

    def raw_position(self, s: Segment, K: int) -> float | None:
        """Continuous RAW frame position (float) the segment's model shows at MAIN frame K."""
        if s.type != "raw":
            return None
        rf, mf = float(self.rf), float(self.mf)
        if s.time_remap_keys:
            keys = sorted(s.time_remap_keys, key=lambda d: float(d["comp_frame"]))
            xs = [float(Fraction(d["comp_frame"]) * self.mf / self.cf) if self.mf != self.cf else float(d["comp_frame"])
                  for d in keys]
            return _interp_pts(xs, [float(d["raw_seconds"]) for d in keys], float(K)) * rf
        if s.raw_in_seconds is None:
            return None
        k_in = self.span[s.id][0]
        v = float(s.speed)
        err = float(Fraction(k_in) / self.mf - Fraction(int(s.comp_in)) / self.cf) if self.mf != self.cf else 0.0
        raw_in = float(s.raw_in_seconds) + v * err if err else float(s.raw_in_seconds)
        return rf * (raw_in + v * ((K - k_in) / mf))

    def raw_frames(self, s: Segment, K: int) -> set[int] | None:
        """RAW frames AE may show (floor rule; both neighbours when the position is within PLAN_FRAME_TOL of a
        frame boundary). None when the position is unknown or outside the RAW."""
        x = self.raw_position(s, K)
        if x is None or not math.isfinite(x):
            return None
        out = {math.floor(x + AE_EPS), math.floor(x - PLAN_FRAME_TOL + AE_EPS), math.floor(x + PLAN_FRAME_TOL + AE_EPS)}
        if min(out) < 0 or (self.n_raw is not None and max(out) >= self.n_raw):
            return None
        return out

    def alpha_in(self, a: Segment, b: Segment, K: int) -> float | None:
        """Declared opacity of the later segment ``b`` at MAIN frame K inside the overlap with ``a``."""
        t = _transition_dict(b.transition_in) or _transition_dict(a.transition_out)
        if not t:
            return None
        d = int(t.get("duration_frames", 0) or 0)
        o = int(b.comp_in)
        d_eff = min(d, int(a.comp_out) - o)
        if d_eff <= 0:
            return None
        alpha = list(t.get("alpha") or [])
        if len(alpha) != d:
            alpha = [i / d for i in range(d)]
        alpha = [min(1.0, max(0.0, float(x))) for x in alpha][:d_eff]
        xs = [_to_main_f_exact(o + i, self.cf, self.mf) for i in range(d_eff)] + [_to_main_f_exact(o + d_eff, self.cf, self.mf)]
        return _interp_pts(xs, alpha + [1.0], float(K))


def _to_main_f_exact(k: float, comp_fps: Fraction, main_fps: Fraction) -> float:
    if Fraction(main_fps) == Fraction(comp_fps):
        return float(k)
    return float(Fraction(k) * Fraction(main_fps) / Fraction(comp_fps))


def _entry_weight(e: dict) -> float:
    """Compositing weight of a simulated layer entry (0..1); records without weights: the opacity."""
    w = e.get("weight")
    if w is None:
        op = e.get("opacity", 100.0)
        op = 100.0 if op is None else float(op)
        return op / 100.0 if op > 1.0 + 1e-9 else op
    return float(w)


def _entry_seg(e: dict) -> int | None:
    s = e.get("seg")
    if isinstance(s, (int, np.integer)):
        return int(s)
    m = re.match(r"seg(\d+)$", str(e.get("layer") or ""))
    return int(m.group(1)) if m else None


def _check_transition_frame(ents: list[dict], a: Segment, b: Segment, K: int, tl: CutlistTimeline) -> str | None:
    """Plan vs cutlist on an overlap frame (crossfade / dip ramp): the visible RAW layers are A's and B's
    model frames with weights (1 - alpha_B) and alpha_B. Returns a problem description or None."""
    alpha = tl.alpha_in(a, b, K)
    if alpha is None:
        return None
    want = {}
    if a.type == "raw":
        want[a.id] = (tl.raw_frames(a, K), 1.0 - alpha)
    if b.type == "raw":
        want[b.id] = (tl.raw_frames(b, K), alpha)
    vis = [e for e in ents if _entry_weight(e) > 1e-3]
    got: dict[int, float] = {}
    for e in vis:
        sid = _entry_seg(e)
        j = e.get("raw_frame")
        if sid is None:            # records without segment ids: attribute by RAW frame
            sid = next((i for i, (fr, _w) in want.items() if fr is not None and j in fr), None)
        if sid not in want:
            return f"layer {e.get('layer')} (RAW {j}, weight {_entry_weight(e):.3f}) is not part of the transition"
        fr = want[sid][0]
        if fr is not None and (j is None or int(j) not in fr):
            return f"S{sid:02d} shows RAW {j}, cutlist {sorted(fr)}"
        got[sid] = got.get(sid, 0.0) + _entry_weight(e)
    for sid, (_fr, w) in want.items():
        if abs(got.get(sid, 0.0) - w) > PLAN_ALPHA_TOL:
            return f"S{sid:02d} weight {got.get(sid, 0.0):.3f}, cutlist {w:.3f} (alpha_B {alpha:.3f})"
    return None


def _measured_reference(fm: FrameMap) -> tuple[dict[str, np.ndarray], str]:
    """refine's measurement (DESIGN §7 D4): the 'pre_segment_*' columns segment.py keeps, else the FrameMap
    itself (maps that never went through segmentation)."""
    d = fm.__dict__.get("d") or {}
    keys = ("raw", "raw_lo", "raw_hi", "status")
    if all(("pre_segment_" + k) in d for k in keys):
        ref = {k: np.asarray(d["pre_segment_" + k]) for k in keys}
        ref["tie"] = np.asarray(d["pre_segment_tie"]) if "pre_segment_tie" in d else np.zeros(fm.n, bool)
        return ref, "pre-segmentation measurement (refine)"
    return {"raw": np.asarray(fm.raw), "raw_lo": np.asarray(fm.raw_lo), "raw_hi": np.asarray(fm.raw_hi),
            "status": np.asarray(fm.status), "tie": np.zeros(fm.n, bool)}, "frame map"


def check_ae_sim(frames: dict, fm: FrameMap, comp_fps: Fraction, main_fps: Fraction, n_main: int,
                 cut_frames_main: Iterable[int], cfg: Any, source: str = "plan",
                 segments: Sequence[Segment] | None = None, raw_fps: Fraction | None = None,
                 n_raw: int | None = None) -> dict:
    """Criterion 3 / Stage 9.2: the RAW frame AE shows (simulated from the plan or the mock record) against
    the RAW frame the competitor showed.

    Reference (DESIGN §7 D4): refine's PRE-segmentation measurement (``fm.d['pre_segment_*']``), not the
    m(k) segmentation rewrote with its own model. Classes of matched frames (all listed):
    exact; ambiguous-identical (j in refine's [raw_lo, raw_hi]); timing-tie (off by one on a tie frame);
    grid (MAIN on a different grid: j between the competitor frames m(k) and m(k+1) that bracket MAIN
    time K/main_fps, REQ-2); re-assigned (j is the frame segment.py wrote instead of refine's measurement,
    or a NONE frame segmentation absorbed); mismatched (anything else). exact + ambiguous + tie + grid
    must reach frame_exact_min.

    With ``segments`` (the cutlist) the plan must also reproduce the cutlist on EVERY frame, whatever the
    fraction: the RAW frame of each single-segment frame, both layers and the opacity of every transition
    frame, and no visible RAW on dip / flash / NOT-IN-RAW frames. Any disagreement fails."""
    exact_min = float(getattr(cfg, "frame_exact_min", 0.99))
    cf, mf = Fraction(comp_fps), Fraction(main_fps)
    same_grid = mf == cf
    cuts = sorted(set(int(c) for c in cut_frames_main))
    near_cut = set()
    if not same_grid:
        for c in cuts:
            near_cut.update((c - 1, c))
    ref, ref_name = _measured_reference(fm)
    post_raw, post_status = np.asarray(fm.raw), np.asarray(fm.status)
    tie_post = np.asarray(fm.tie)
    tl = CutlistTimeline(segments, cf, mf, Fraction(raw_fps) if raw_fps is not None else cf, n_raw) \
        if segments is not None else None
    seg_of_k: dict[int, Segment] = {}          # competitor frame -> its (last-starting) RAW segment
    for s in sorted(segments or [], key=lambda s: (s.comp_in, s.id)):
        if s.type == "raw":
            for k in range(max(0, s.comp_in), min(fm.n, s.comp_out)):
                seg_of_k[k] = s
    n_matched = n_exact = 0
    ambiguous, ties, grid, reassigned, mismatches, excluded = [], [], [], [], [], []
    plan_bad: list[dict] = []
    n_trans = n_solid = 0

    def ents_at(K: int) -> list[dict]:
        e = frames.get(K, frames.get(str(K))) if isinstance(frames, dict) else None
        return list(e or [])

    for K in range(int(n_main)):
        ents = ents_at(K)
        cov = tl.covering(K) if tl is not None else None
        # -- plan vs cutlist on transition / solid frames ------------------------------------------------
        if cov is not None and len(cov) == 2:
            n_trans += 1
            prob = _check_transition_frame(ents, cov[0], cov[1], K, tl)
            if prob:
                plan_bad.append({"K": K, "what": "transition", "problem": prob})
            continue
        if cov is not None and len(cov) == 1 and cov[0].type in ("dip", "flash", "not_in_raw"):
            n_solid += 1
            w = sum(_entry_weight(e) for e in ents)
            if w > PLAN_SOLID_MAX_WEIGHT:
                plan_bad.append({"K": K, "what": cov[0].type, "problem": f"RAW visible (weight {w:.3f}) on a "
                                 f"{cov[0].type} frame of {_seg_name(cov[0])}"})
            continue
        k = main_to_comp(K, cf, mf)
        if not (0 <= k < fm.n) or int(post_status[k]) != Status.MATCH:
            continue
        j = visible_raw_frame(ents)
        # -- plan vs cutlist on single-segment frames ----------------------------------------------------
        if cov is not None and len(cov) == 1 and cov[0].type == "raw":
            want = tl.raw_frames(cov[0], K)
            if want is not None and (j is None or j not in want):
                plan_bad.append({"K": K, "k": k, "what": "frame", "ae": j, "cutlist": sorted(want),
                                 "problem": f"shows RAW {j}, the cutlist ({_seg_name(cov[0])}) says {sorted(want)}"})
        if K in near_cut:
            excluded.append(K)
            continue
        n_matched += 1
        m = int(ref["raw"][k])
        row = {"K": K, "k": k, "ae": j, "m": m}
        if int(ref["status"][k]) != Status.MATCH:
            reassigned.append({**row, "m": None, "model": int(post_raw[k]),
                               "why": "no refine measurement (NONE frame absorbed by segmentation)"})
            continue
        if j is not None and j == m:
            n_exact += 1
            continue
        lo, hi = int(ref["raw_lo"][k]), int(ref["raw_hi"][k])
        if j is not None and lo >= 0 and hi >= 0 and lo <= j <= hi:
            ambiguous.append({**row, "range": [lo, hi]})
            continue
        if j is not None and (bool(tie_post[k]) or bool(ref["tie"][k])) and abs(j - m) <= 1:
            ties.append(row)
            continue
        if j is not None and not same_grid:
            br = _grid_bracket(k, m, ref, seg_of_k, cf, Fraction(raw_fps) if raw_fps is not None else cf)
            if br is not None and br[0] <= j <= br[1]:
                grid.append({**row, "bracket": list(br)})
                continue
        if j is not None and j == int(post_raw[k]) and int(post_raw[k]) != m:
            reassigned.append({**row, "model": int(post_raw[k]), "why": "segment model frame replaced refine's best frame"})
            continue
        soft = [int(fm.soft_lo[k]), int(fm.soft_hi[k])]
        mismatches.append({**row, "range": [lo, hi], "soft": soft,
                           "within_soft": bool(j is not None and soft[0] >= 0 and soft[0] <= j <= soft[1]),
                           "score": None if np.isnan(fm.score[k]) else round(float(fm.score[k]), 4)})
    n_ok = n_exact + len(ambiguous) + len(ties) + len(grid)
    frac = n_ok / n_matched if n_matched else 1.0
    failures: list[str] = []
    exceptions: list[str] = []
    if n_matched == 0:
        failures.append(f"{source}: no matched frames to compare")
    elif frac < exact_min:
        bad = [x["k"] for x in (reassigned + mismatches)]
        failures.append(f"{source}: only {frac:.4%} of {n_matched} matched frames show the measured m(k) "
                        f"(< {exact_min:.0%}); first differences at k = {sorted(bad)[:10]}")
    if plan_bad:
        failures.append(f"{source}: {len(plan_bad)} MAIN frame(s) do not reproduce the cutlist (first: "
                        + "; ".join(f"K={x['K']}: {x['problem']}" for x in plan_bad[:3]) + ")")
    for name, lst in (("ambiguous-identical", ambiguous), ("timing-tie", ties),
                      ("between the bracketing competitor frames (different MAIN grid)", grid),
                      ("re-assigned by segmentation (AE shows the segment model's frame, not refine's measurement)",
                       reassigned),
                      ("not reproduced exactly (AE frame differs from the measured m(k))", mismatches)):
        if lst:
            exceptions.append(f"{source}: {len(lst)} frame(s) {name}: k = {_ranges([x['k'] for x in lst])[:10]}")
    n_exc = len(ambiguous) + len(ties) + len(grid) + len(reassigned) + len(mismatches)
    status = "fail" if failures else ("pass_with_exceptions" if n_exc else "pass")
    summary = (f"{source}: {n_exact}/{n_matched} exact, {len(ambiguous)} ambiguous-identical, {len(ties)} timing-tie, "
               + (f"{len(grid)} between competitor frames (MAIN grid), " if not same_grid else "")
               + f"{len(reassigned)} re-assigned, {len(mismatches)} mismatched ({frac:.4%} ok)")
    if tl is not None:
        summary += (f"; plan vs cutlist: {len(plan_bad)} differing frame(s) "
                    f"({n_trans} transition, {n_solid} placeholder/dip frames checked)")
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions,
            "reference": ref_name, "matched": n_matched, "exact": n_exact, "fraction_ok": round(frac, 6),
            "ambiguous_identical": ambiguous, "timing_tie": ties, "grid": grid[:500], "n_grid": len(grid),
            "reassigned": reassigned[:500], "n_reassigned": len(reassigned),
            "mismatches": mismatches[:500], "n_mismatches": len(mismatches), "excluded_near_cuts": len(excluded),
            "plan_mismatches": plan_bad[:500], "n_plan_mismatches": len(plan_bad),
            "transition_frames_checked": n_trans, "solid_frames_checked": n_solid}


def _grid_bracket(k: int, m: int, ref: dict, seg_of_k: dict[int, Segment], comp_fps: Fraction,
                  raw_fps: Fraction) -> tuple[int, int] | None:
    """RAW frames AE may legitimately show at a MAIN frame whose time lies in [k/comp_fps, (k+1)/comp_fps):
    between m(k) and m(k+1) when k+1 is a matched frame of the same segment, else between m(k) and
    m(k) + ceil(|v| raw_fps / comp_fps) (reversed for v < 0)."""
    s = seg_of_k.get(k)
    n = len(ref["raw"])
    if s is not None and s.type == "raw" and k + 1 < n and seg_of_k.get(k + 1) is s \
            and int(ref["status"][k + 1]) == Status.MATCH and int(ref["raw"][k + 1]) >= 0:
        m1 = int(ref["raw"][k + 1])
        return (min(m, m1), max(m, m1))
    v = float(s.speed) if s is not None and s.speed is not None else 1.0
    step = int(math.ceil(abs(v) * float(raw_fps) / float(comp_fps) - 1e-9))
    return (m, m + step) if v >= 0 else (m - step, m)


# -- plan / mock-record adapters ----------------------------------------------------------------

def _fps_of(v: Any) -> Fraction | None:
    if v is None:
        return None
    if isinstance(v, dict):
        num, den = _get(v, "num", "numerator"), _get(v, "den", "denominator")
        return Fraction(int(num), int(den)) if num is not None and den is not None else None
    try:
        return parse_fps(v)
    except (TypeError, ValueError):
        return None


def plan_main(plan: dict) -> dict:
    """{'name', 'fps', 'frames', 'w', 'h'} of the MAIN comp in an ae_plan."""
    m = _get(plan, "main", "MAIN", "main_comp", "comp", default={}) or {}
    fps = _fps_of(_get(m, "fps")) or _fps_of(_get(plan, "fps", "main_fps"))
    return {"name": _get(m, "name", default=MAIN_COMP_NAME), "fps": fps,
            "frames": _get(m, "frames", default=_get(plan, "frames")),
            "w": _get(m, "w", "width"), "h": _get(m, "h", "height")}


def plan_segment_layers(plan: dict) -> list[dict]:
    """RAW video layers of an ae_plan (kind 'raw'; audio-only duplicates 'raw_audio' excluded). Plans
    without kinds: every layer with a rawIn value."""
    m = _get(plan, "main", "MAIN", "main_comp", "comp", default={}) or {}
    layers = _get(plan, "layers") or _get(m, "layers") or _get(plan, "segments") or []
    out = []
    for L in layers:
        if not isinstance(L, dict):
            continue
        kind = str(L.get("kind", "")).lower()
        if kind in ("raw", "segment") or (not kind and _get(L, "rawIn", "raw_in") is not None):
            out.append(L)
    return out


def _record_comps(rec: dict) -> list[dict]:
    comps = _get(rec, "comps", "compositions", default=[])
    if isinstance(comps, dict):
        comps = [dict(v, name=v.get("name", k)) for k, v in comps.items()]
    return [c for c in comps if isinstance(c, dict)]


def _record_main(rec: dict, name: str = MAIN_COMP_NAME) -> dict | None:
    comps = _record_comps(rec)
    for c in comps:
        if c.get("comment") == "mc:main":
            return c
    for c in comps:
        if c.get("name") == name:
            return c
    return None


def _record_layers(comp: dict) -> list[dict]:
    layers = [L for L in (_get(comp, "layers", default=[]) or []) if isinstance(L, dict)]
    if layers and all(isinstance(L.get("index"), (int, float)) for L in layers):
        layers = sorted(layers, key=lambda L: L["index"])
    return layers


def _layer_source_name(L: dict) -> str:
    src = _get(L, "source", "sourceName", "source_name", "footage", default="")
    if isinstance(src, dict):
        src = _get(src, "name", "file", "path", default="")
    return os.path.basename(str(src))


def _layer_tag(L: dict) -> str:
    c = str(L.get("comment") or "")
    return c[3:] if c.startswith("mc:") else ""


def _find_keys(L: dict, needles: Sequence[str]) -> list[dict] | None:
    """Keys of a property whose name contains one of ``needles`` (searched recursively)."""
    stack: list[Any] = [L]
    while stack:
        d = stack.pop()
        if isinstance(d, dict):
            for k, v in d.items():
                if any(n.lower() in str(k).lower() for n in needles):
                    if isinstance(v, dict) and isinstance(v.get("keys"), list) and v["keys"]:
                        return v["keys"]
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        return v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(d, list):
            stack.extend(x for x in d if isinstance(x, (dict, list)))
    return None


def _static_value(L: dict, prop: str, default: float) -> float:
    p = (L.get("props") or {}).get(prop)
    if isinstance(p, dict) and p.get("value") is not None:
        v = p["value"]
        return float(v[0] if isinstance(v, list) else v)
    v = L.get(prop.split()[-1].lower()) if prop else None
    return float(v) if isinstance(v, (int, float)) else default


def _is_hold(key: dict) -> bool:
    it = _get(key, "outInterp", "out_interp", "outInterpolationType", "interp", "interpolation")
    return it == KEY_HOLD or (isinstance(it, str) and "HOLD" in it.upper())


def _key_layer_times(keys: list[dict], L: dict) -> list[tuple[float, float, bool]]:
    """[(layer time, value, hold)] sorted. AE keys live in LAYER time; records that only give comp
    times are mapped back with the layer's final startTime / stretch."""
    out = []
    st = float(_get(L, "startTime", default=0.0))
    stretch = float(_get(L, "stretch", default=100.0))
    for k in keys:
        if "layerTime" in k:
            lt = float(k["layerTime"])
        else:
            lt = (float(_get(k, "time", "keyTime", "t")) - st) * 100.0 / stretch
        v = _get(k, "value", "v", "keyValue")
        out.append((lt, float(v[0] if isinstance(v, list) else v), _is_hold(k)))
    out.sort(key=lambda x: x[0])
    return out


def _eval_keys(kv: list[tuple[float, float, bool]], x: float, eps: float = 1e-9) -> float:
    if x <= kv[0][0] + eps:
        return kv[0][1]
    if x >= kv[-1][0] - eps:
        return kv[-1][1]
    for (t0, v0, hold), (t1, v1, _) in zip(kv[:-1], kv[1:]):
        if t0 - eps <= x < t1 - eps:
            if hold or t1 == t0:
                return v0
            return v0 + (x - t0) / (t1 - t0) * (v1 - v0)
    return kv[-1][1]


def simulate_record(rec: dict, raw_name: str, raw_fps: Fraction, main_fps: Fraction, n_main: int) -> dict[int, list[dict]]:
    """Independent AE-semantics evaluation of the layers RECORDED by the mock run (what the JSX actually
    set, after its read-back self-check): per MAIN frame, [{layer, raw_frame, opacity, weight}] top first.

    AE rules: a layer shows at comp time t when inPoint <= t < outPoint; its layer time is
    (t - startTime) * 100 / stretch; the source time is the time-remap value at that layer time (HOLD /
    LINEAR keys) or the layer time itself; RAW frame = floor(source time * raw_fps + 1e-9). Pre-comp
    layers (the Video Box) are entered with their own source time; opacities composite top-down
    (weight = what the layer contributes to the final pixel)."""
    num, den = Fraction(main_fps).numerator, Fraction(main_fps).denominator
    comps = _record_comps(rec)
    by_id = {c.get("id"): c for c in comps if c.get("id") is not None}
    by_name = {c.get("name"): c for c in comps}
    footage = {f.get("id"): f for f in (_get(rec, "footage", default=[]) or []) if isinstance(f, dict)}
    raw_ids = {fid for fid, f in footage.items()
               if f.get("comment") == "mc:raw" or os.path.basename(str(f.get("name") or "")) == raw_name}

    def footage_rate(L: dict) -> float:
        f = footage.get(L.get("sourceId")) or {}
        conf = float(f.get("conformFrameRate") or 0.0)
        if conf > 0:
            return conf
        if f.get("fps_num") and f.get("fps_den"):
            return float(f["fps_num"]) / float(f["fps_den"])
        return float(Fraction(raw_fps))

    def is_raw(L: dict) -> bool:
        if L.get("sourceType") == "footage" and L.get("sourceId") in raw_ids:
            return True
        return L.get("sourceType") in (None, "footage") and _layer_source_name(L) == raw_name

    def inner(L: dict) -> dict | None:
        if L.get("sourceType") != "comp":
            return None
        return by_id.get(L.get("sourceId")) or by_name.get(L.get("sourceName"))

    order = {}

    def walk(comp: dict, t: float, w_in: float, entries: list[dict], depth: int) -> float:
        remaining = 1.0
        if depth > 8:
            return remaining
        for L in _record_layers(comp):
            if L.get("enabled") is False or L.get("guideLayer") is True:
                continue
            if _layer_tag(L).startswith("bg_blur"):
                continue
            if not (float(_get(L, "inPoint", default=0.0)) - 1e-7 <= t < float(_get(L, "outPoint", default=0.0)) - 1e-7):
                continue
            start, stretch = float(_get(L, "startTime", default=0.0)), float(_get(L, "stretch", default=100.0))
            lt = (t - start) * 100.0 / stretch
            ok = _find_keys(L, ("ADBE Opacity", "opacity"))
            op = (_eval_keys(_key_layer_times(ok, L), lt) if ok else _static_value(L, "ADBE Opacity", 100.0)) / 100.0
            src_t = lt
            if L.get("timeRemapEnabled"):
                rk = _find_keys(L, ("Time Remap", "timeRemap", "time_remap"))
                if rk:
                    src_t = _eval_keys(_key_layer_times(rk, L), lt)
            sub = inner(L)
            if sub is not None:
                trans = walk(sub, src_t, w_in * remaining * op, entries, depth + 1)
                remaining *= 1.0 - op * (1.0 - trans)
                continue
            if is_raw(L):
                key = _layer_tag(L) or str(L.get("name"))
                order.setdefault(key, len(order))
                sm = re.match(r"seg(\d+)$", key)
                entries.append({"layer": key, "name": L.get("name"), "seg": int(sm.group(1)) if sm else None,
                                "raw_frame": int(math.floor(src_t * footage_rate(L) + 1e-9)),
                                "opacity": op, "weight": w_in * remaining * op})
            remaining *= 1.0 - op
        return remaining

    main = _record_main(rec)
    out: dict[int, list[dict]] = {}
    for K in range(int(n_main)):
        ents: list[dict] = []
        if main is not None:
            walk(main, K * den / num, 1.0, ents, 0)
        out[K] = ents
    return out


# ---------------------------------------------------------------------------------------------
# c6 mock-run checks
# ---------------------------------------------------------------------------------------------

def _close(a: Any, b: float, tol: float = 1e-9) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _saved_list(rec: dict) -> list[str]:
    s = _get(rec, "saved", "saved_path", "savedPath", "save_path", "savePath", "saved_file")
    if s is None:
        return []
    return [str(x) for x in s] if isinstance(s, (list, tuple)) else [str(s)]


def _open_dialog_calls(rec: dict) -> int:
    calls = _get(rec, "calls", default={}) or {}
    n = _get(calls, "openDialog")
    if n is None:
        n = _get(rec, "open_dialog_calls", "openDialogCalls", "openDialog")
    if isinstance(n, list):
        n = len(n)
    if n is None:
        n = sum(1 for d in (_get(rec, "dialogs", default=[]) or []) if "open" in str(d).lower() or "locate" in str(d).lower())
    return int(n or 0)


def _rec_failed(rec: dict) -> str | None:
    st = _get(rec, "status")
    err = _get(rec, "error", "exception", "uncaught", "gate_error")
    if st not in (None, "ok") or err:
        return f"status {st}: {err or ''}".strip()
    return None


def _bad_alerts(rec: dict) -> list[str]:
    return [str(a) for a in (_get(rec, "alerts", default=[]) or []) if "Error" in str(a) or "failed" in str(a)]


def check_mock(plan: dict | None, records: dict, main_fps: Fraction, n_main: int, script_dir: str | Path,
               raw_name: str, layer_checker: Callable[[dict, dict, dict], list[str]] | None | str = "auto") -> dict:
    """Criterion 6 on the mock-run records (DESIGN §5 verify c6).

    default: ran cleanly (status ok, no strict-mock violation, no alert containing 'Error'/'failed',
    balanced undo group), MAIN frameRate == main fps, duration == frames * frameDuration, work area ==
    whole comp, saved exactly once to <script dir>/recreated_edit.aep, every plan layer present (matched
    by its 'mc:<id>' tag, else by name) with the plan's name / startTime / stretch / inPoint / outPoint,
    one RAW video layer per RAW segment layer of the plan. media_missing: File.openDialog called, clean
    abort, nothing saved. new_project_null / no_marker_property when recorded: clean abort / still saves.
    layer_checker(plan_layer, record_layer, fps) -> problems adds per-layer key / render-switch checks;
    'auto' = export_ae.record_layer_problems when that helper exists."""
    rec = (records or {}).get("default")
    if rec is None:
        return {"status": "fail", "summary": "no mock run record", "failures": ["mock run did not produce a record"]}
    if _get(rec, "status") == "not_available":
        return {"status": "not_available", "summary": f"mock not available: {_get(rec, 'reason', default='node missing')}",
                "failures": []}
    if plan is None:
        return {"status": "fail", "summary": "no AE plan", "failures": ["ae_plan missing (export failed)"]}
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    fail = _rec_failed(rec)
    chk("jsx ran without exception", fail is None, fail)
    merr = _get(rec, "mock_errors", default=[]) or []
    chk("no strict-mock violations", not merr, merr[:5])
    chk("no error alerts", not _bad_alerts(rec), _bad_alerts(rec)[:5])
    calls = _get(rec, "calls", default=None)
    if isinstance(calls, dict) and ("beginUndoGroup" in calls or "endUndoGroup" in calls):
        chk("one balanced undo group", calls.get("beginUndoGroup") == 1 and calls.get("endUndoGroup") == 1,
            {"begin": calls.get("beginUndoGroup"), "end": calls.get("endUndoGroup")})
    fr = Fraction(main_fps)
    num, den = fr.numerator, fr.denominator
    pm = plan_main(plan)
    main = _record_main(rec, pm.get("name") or MAIN_COMP_NAME)
    chk("MAIN comp created", main is not None, [c.get("name") for c in _record_comps(rec)])
    if main is not None:
        rate = _get(main, "frameRate")
        f32 = float(np.float32(float(fr)))
        chk("MAIN frameRate == main_fps", _close(rate, float(fr)) or _close(rate, f32), {"recorded": rate, "want": fps_str(fr)})
        dur = _get(main, "duration")
        want_dur = n_main * den / num
        chk("MAIN duration == frames * frameDuration", _close(dur, want_dur, 1e-9 * max(1.0, want_dur)),
            {"recorded": dur, "want": want_dur, "frames": n_main})
        ws, wd = _get(main, "workAreaStart"), _get(main, "workAreaDuration")
        chk("work area == whole comp", _close(ws, 0.0) and dur is not None and _close(wd, float(dur), 1e-9 * max(1.0, want_dur)),
            {"workAreaStart": ws, "workAreaDuration": wd})
        for name, want in (("width", pm.get("w")), ("height", pm.get("h"))):
            if want is not None:
                chk(f"MAIN {name}", _get(main, name) == want, {"recorded": _get(main, name), "want": want})
    want_saved = os.path.normpath(str(Path(script_dir).resolve() / "recreated_edit.aep"))
    saved = [os.path.normpath(s) for s in _saved_list(rec)]
    chk("saved <script dir>/recreated_edit.aep", saved == [want_saved], {"recorded": saved, "want": want_saved})
    # plan layers vs recorded layers
    rec_by_tag: dict[str, dict] = {}
    rec_by_name: dict[str, dict] = {}
    n_rec_raw = 0
    raw_ids = {f.get("id") for f in (_get(rec, "footage", default=[]) or []) if isinstance(f, dict) and f.get("comment") == "mc:raw"}
    for c in _record_comps(rec):
        for L in _record_layers(c):
            if _layer_tag(L):
                rec_by_tag[_layer_tag(L)] = L
            rec_by_name.setdefault(str(L.get("name")), L)
            is_raw = (L.get("sourceId") in raw_ids) if raw_ids else _layer_source_name(L) == raw_name
            if is_raw and L.get("enabled") is not False:
                n_rec_raw += 1
    raw_plan = plan_segment_layers(plan)
    chk("one RAW video layer per segment", n_rec_raw == len(raw_plan), {"recorded": n_rec_raw, "plan": len(raw_plan)})
    layer_problems: list[str] = []
    switched: list[str] = []
    F = {"num": num, "den": den}
    extra_problems = layer_checker
    if layer_checker == "auto":
        try:
            from . import export_ae
            extra_problems = getattr(export_ae, "record_layer_problems", None)
        except ImportError:  # pragma: no cover - export_ae is part of the package
            extra_problems = None
    all_layers = _get(plan, "layers", default=None) or raw_plan
    for P in all_layers:
        if not isinstance(P, dict):
            continue
        L = rec_by_tag.get(str(P.get("id"))) if P.get("id") is not None and rec_by_tag else None
        if L is None:
            L = rec_by_name.get(str(P.get("name")))
        if L is None:
            if str(P.get("kind")) == "reference" and not ((plan.get("footage") or {}).get("ref")):
                continue
            layer_problems.append(f"{P.get('id') or P.get('name')}: not found in the mock record")
            continue
        probs = []
        if P.get("name") is not None and L.get("name") != P.get("name"):
            probs.append(f"name {L.get('name')!r} != {P.get('name')!r}")
        k_in, k_out = _get(P, "compIn", "comp_in"), _get(P, "compOut", "comp_out")
        want_in = _get(P, "inPoint", default=None if k_in is None else int(k_in) * den / num)
        want_out = _get(P, "outPoint", default=None if k_out is None else int(k_out) * den / num)
        for key, want in (("inPoint", want_in), ("outPoint", want_out)):
            if want is not None and not _close(_get(L, key), float(want)):
                probs.append(f"{key} {_get(L, key)} != {want}")
        mode = str(_get(P, "timeMode", "time_mode", default="stretch"))
        remapped = bool(_get(L, "timeRemapEnabled", default=False))
        t_in = float(want_in) if want_in is not None else 0.0
        if mode in ("remap", "frames") or (remapped and mode == "stretch"):
            if mode == "stretch":
                switched.append(str(P.get("name")))     # the JSX read-back self-check chose frames mode
            if P.get("kind") in ("raw", "segment") or P.get("rawIn") is not None:
                if not _close(_get(L, "startTime"), t_in):
                    probs.append(f"startTime {_get(L, 'startTime')} != tIn {t_in} (remap)")
                if not _close(_get(L, "stretch"), 100.0):
                    probs.append(f"stretch {_get(L, 'stretch')} != 100 (remap)")
                if not remapped:
                    probs.append("time remapping not enabled")
        elif mode == "stretch" and _get(P, "stretch") is not None and _get(P, "rawIn", "raw_in") is not None:
            st_plan = float(_get(P, "stretch"))
            st_rec = _get(L, "stretch")
            if not _close(st_rec, st_plan, 1e-9 * max(1.0, abs(st_plan))):
                probs.append(f"stretch {st_rec} != {st_plan}")
            else:
                want_start = _get(P, "startTime")
                if want_start is None:
                    want_start = t_in - float(_get(P, "rawIn", "raw_in")) / (100.0 / float(st_rec))
                if not _close(_get(L, "startTime"), float(want_start), 1e-9 * max(1.0, abs(float(want_start)))):
                    probs.append(f"startTime {_get(L, 'startTime')} != {want_start}")
        elif _get(P, "startTime") is not None and not _close(_get(L, "startTime"), float(_get(P, "startTime"))):
            probs.append(f"startTime {_get(L, 'startTime')} != {_get(P, 'startTime')}")
        if extra_problems is not None and "timeMode" in P and not (mode == "stretch" and remapped):
            try:
                probs.extend(extra_problems(P, L, F))
            except Exception as e:  # noqa: BLE001 - a helper that cannot read this record is reported, not fatal
                probs.append(f"key/render-switch check unavailable: {type(e).__name__}: {e}")
        if probs:
            layer_problems.append(f"{P.get('id') or P.get('name')}: " + "; ".join(probs))
    chk("every plan layer present with the plan's name/startTime/stretch/in/out", not layer_problems, layer_problems[:20])
    # other scenarios
    mm = (records or {}).get("media_missing")
    if mm is None:
        chk("media_missing scenario ran", False, "no record")
    elif _get(mm, "status") != "not_available":
        chk("media_missing: no exception", _rec_failed(mm) is None, _rec_failed(mm))
        chk("media_missing: File.openDialog called", _open_dialog_calls(mm) >= 1, _open_dialog_calls(mm))
        chk("media_missing: aborted without saving", not _saved_list(mm), _saved_list(mm))
        chk("media_missing: clean abort message", bool(_get(mm, "alerts")) and not _bad_alerts(mm), (_get(mm, "alerts") or [])[:2])
    npn = (records or {}).get("new_project_null")
    if npn is not None and _get(npn, "status") != "not_available":
        chk("new_project_null: clean abort", _rec_failed(npn) is None and not _saved_list(npn) and not _bad_alerts(npn),
            (_get(npn, "alerts") or [])[:2])
    nmp = (records or {}).get("no_marker_property")
    if nmp is not None and _get(nmp, "status") != "not_available":
        chk("no_marker_property: still builds and saves",
            _rec_failed(nmp) is None and [os.path.normpath(s) for s in _saved_list(nmp)] == [want_saved] and not _bad_alerts(nmp),
            (_get(nmp, "alerts") or [])[:2])
    failures = [f"{c['check']}: {c['detail']}" for c in checks if not c["ok"]]
    status = "fail" if failures else "pass"
    summary = f"mock run: {sum(c['ok'] for c in checks)}/{len(checks)} checks ok"
    if switched:
        summary += f"; {len(switched)} layer(s) switched to frame-exact remap by the JSX self-check"
    return {"status": status, "summary": summary, "failures": failures, "checks": checks,
            "switched_to_frames": switched, "scenarios": sorted(k for k in (records or {}))}


# ---------------------------------------------------------------------------------------------
# c4 speed / framing / flip / rotation
# ---------------------------------------------------------------------------------------------

def framing_samples(seg: Segment, ks: Sequence[int], step: int) -> list[int]:
    """Frames of a segment where framing is measured independently: every ``step``-th matched frame, the
    first and last matched frames and the transform key frames."""
    ks = sorted(int(k) for k in ks)
    if not ks:
        return []
    kset = set(ks)
    out = set(ks[::max(1, int(step))]) | {ks[0], ks[-1]}
    for kd in seg.transform_keys or []:
        kk = int(round(float(kd.get("comp_frame", -1))))
        if kk in kset:
            out.add(kk)
    return sorted(out)


def _sim_errors(model: Sim, meas: Sim, centre: np.ndarray) -> tuple[float, float, float]:
    """(relative scale error, position error in comp px of the RAW point ``meas`` puts at ``centre``,
    rotation error in degrees) of ``model`` against ``meas``."""
    es = abs(model.s / meas.s - 1.0) if meas.s > 0 else math.inf
    p_raw = meas.inverse().apply(centre)
    ep = float(np.hypot(*(model.apply(p_raw) - centre)[0]))
    er = abs(((model.theta_deg - meas.theta_deg) + 180.0) % 360.0 - 180.0)
    return es, ep, er


def perturb_sim(sim: Sim, centre: tuple[float, float], rel_scale: float, dx: float, dy: float) -> Sim:
    """``sim`` scaled by (1 + rel_scale) about ``centre`` (comp px) and shifted by (dx, dy)."""
    f = 1.0 + float(rel_scale)
    cx, cy = float(centre[0]), float(centre[1])
    return Sim(sim.s * f, sim.theta_deg, f * sim.tx + (1 - f) * cx + dx, f * sim.ty + (1 - f) * cy + dy)


def _measured_constraints(seg: Segment, fm: FrameMap) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ks, lo, hi) of a segment's MATCH frames using refine's measurement (fm.d['pre_segment_*'] when
    segmentation stored it, else the current visually-identical ranges) -- never the soft ranges."""
    k0, k1 = max(0, int(seg.comp_in)), min(fm.n, int(seg.comp_out))
    if k1 <= k0:
        e = np.zeros(0, np.int64)
        return e, e.copy(), e.copy()
    d = fm.d
    st = d.get("pre_segment_status", fm.status)[k0:k1]
    fl = d.get("pre_segment_flip", fm.flip)[k0:k1]
    raw = d.get("pre_segment_raw", fm.raw)[k0:k1].astype(np.int64)
    lo = d.get("pre_segment_raw_lo", fm.raw_lo)[k0:k1].astype(np.int64)
    hi = d.get("pre_segment_raw_hi", fm.raw_hi)[k0:k1].astype(np.int64)
    sel = (st == Status.MATCH) & (fl.astype(bool) == bool(seg.flip_h)) & (raw >= 0)
    ks = np.arange(k0, k1)[sel]
    lo = np.minimum(np.where(lo[sel] >= 0, lo[sel], raw[sel]), raw[sel])
    hi = np.maximum(np.where(hi[sel] >= 0, hi[sel], raw[sel]), raw[sel])
    return ks.astype(np.int64), lo, hi


def check_speed_framing(segments: Sequence[Segment], fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction,
                        raw_wh: tuple[float, float], box: Box | dict | None, comp_wh: tuple[float, float],
                        cfg: Any, feasible_range: Callable | None = None,
                        measure: Callable[[Segment, int, Sim], dict | None] | None = None,
                        sample_step: int | None = None) -> dict:
    """Criterion 4. Speed: inside the feasible range of the segment's constraints (±0.5 %), and snapped
    whenever a snap value was feasible. Framing: the FrameMap Sims vs the segment model within ±1 % scale /
    ±4 px at the box centre (catches summarisation errors only: the FrameMap Sims are refine's track
    model), flip identical, rotation within 0.25°.

    Independent measurement (verification-honesty F5): with ``measure(seg, k, model_sim)`` -> {'sim':
    ECC-measured Sim started from a PERTURBED model, 'z': its score, 'z_model': the model's score on the
    same pixels, 'flip_own', 'flip_other': scores of the model with its own flip and of the mirrored
    hypothesis} on sampled frames (framing_samples): a measured framing off by more than the tolerance
    that also scores higher than the model is a bad frame (more than FRAMING_BAD_FRAC of the measured
    samples -> fail, fewer -> listed); ECC that did not reach the model's score is 'unconverged' (listed).
    Flip: the median (own - mirrored) score must be > +3·soft_delta_max; below -that -> fail (the mirrored
    hypothesis wins); in between -> 'flip not decidable' (listed)."""
    from .pipeline import segment_constraints, segment_time_mode
    if feasible_range is None:
        feasible_range = _phase().feasible_speed_range
    speed_tol, scale_tol, pos_tol = 0.005, 0.01, 4.0
    rot_tol = max(float(getattr(cfg, "rotation_min_deg", 0.2)), 0.2) + 0.05
    snaps = [float(v) for v in getattr(cfg, "speed_snap_values", ())]
    b = Box.from_dict(box) if isinstance(box, dict) else box
    centre = np.array([[b.x + b.w / 2, b.y + b.h / 2]]) if b is not None else np.array([[comp_wh[0] / 2, comp_wh[1] / 2]])
    used_speeds = sorted({round(float(s.speed), 6) for s in segments if s.type == "raw" and not s.unsnapped})
    flip_margin = 3.0 * float(getattr(cfg, "soft_delta_max", 0.01))
    if sample_step is None:
        n_match = int(np.sum(np.asarray(fm.status) == Status.MATCH))
        sample_step = max(FRAMING_SAMPLE_STEP, int(math.ceil(n_match / FRAMING_MAX_SAMPLES)) if n_match else 1)
    rows, failures, exceptions = [], [], []
    for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
        if s.type != "raw":
            continue
        row: dict[str, Any] = {"id": s.id, "speed": s.speed}
        name = _seg_name(s)
        # -- speed --
        if segment_time_mode(s) == "remap":
            row["speed_check"] = "remap (time-remap keys)"
        else:
            ks, lo, hi = segment_constraints(s, fm)
            vr = feasible_range(ks, lo, hi, s.comp_in, comp_fps, raw_fps) if len(ks) >= 2 else None
            src = "independent LP"
            if vr is None:
                vr, src = (tuple(s.speed_range) if s.speed_range else None), "segment.speed_range"
            row["speed_range"] = None if vr is None else [round(float(vr[0]), 6), round(float(vr[1]), 6)]
            row["speed_range_source"] = src
            if vr is None:
                exceptions.append(f"{name}: speed range could not be determined (<2 matched frames or infeasible)")
                row["speed_check"] = "unverifiable"
            else:
                lo_v, hi_v = float(vr[0]), float(vr[1])
                ok = lo_v * (1 - speed_tol) - 1e-12 <= s.speed <= hi_v * (1 + speed_tol) + 1e-12 if s.speed > 0 else \
                    lo_v - speed_tol <= s.speed <= hi_v + speed_tol
                row["speed_check"] = "ok" if ok else "fail"
                if not ok:
                    failures.append(f"{name}: speed {s.speed:.5f} outside the feasible range [{lo_v:.5f}, {hi_v:.5f}] ± 0.5 %")
                if s.unsnapped:
                    # 'a snap value was feasible' is judged on refine's MEASURED frames (pre-segmentation
                    # visually-identical ranges), not on the wider soft ranges: on slow footage the soft
                    # ranges admit 1.05 for a genuine 1.03x segment that segment.py rightly left unsnapped
                    mk, mlo, mhi = _measured_constraints(s, fm)
                    mvr = feasible_range(mk, mlo, mhi, s.comp_in, comp_fps, raw_fps) if len(mk) >= 2 else None
                    slo, shi = (float(mvr[0]), float(mvr[1])) if mvr is not None else (lo_v, hi_v)
                    row["snap_range_measured"] = None if mvr is None else [round(slo, 6), round(shi, 6)]
                    feas = sorted({v for v in snaps + used_speeds if slo <= v <= shi})
                    if feas:
                        failures.append(f"{name}: speed left unsnapped although {feas[:4]} are feasible")
                        row["snap_check"] = "fail"
                    else:
                        exceptions.append(f"{name}: unsnapped speed {s.speed:.5f} (no common value feasible)")
                        row["snap_check"] = "unsnapped"
        # -- framing / flip / rotation --
        k0, k1 = max(0, s.comp_in), min(fm.n, s.comp_out)
        ks = [k for k in range(k0, k1) if int(fm.status[k]) == Status.MATCH and np.isfinite(fm.s[k])]
        if s.transform is None and not s.transform_keys:
            if ks:
                failures.append(f"{name}: no transform")
            continue
        worst_s = worst_p = worst_r = 0.0
        flip_bad, bad_frames = [], []
        for k in ks:
            meas = fm.sim(k)
            model = seg_sim(s, k, *raw_wh)
            if bool(fm.flip[k]) != bool(s.flip_h):
                flip_bad.append(k)
                continue
            es = abs(model.s / meas.s - 1.0) if meas.s > 0 else math.inf
            p_raw = meas.inverse().apply(centre)
            ep = float(np.hypot(*(model.apply(p_raw) - centre)[0]))
            er = abs(((model.theta_deg - meas.theta_deg) + 180.0) % 360.0 - 180.0)
            worst_s, worst_p, worst_r = max(worst_s, es), max(worst_p, ep), max(worst_r, er)
            if es > scale_tol or ep > pos_tol or er > rot_tol:
                bad_frames.append(k)
        row.update(frames_checked=len(ks), max_scale_err=round(worst_s, 6), max_pos_err_px=round(worst_p, 3),
                   max_rot_err_deg=round(worst_r, 4), flip_mismatch=len(flip_bad), framing_bad=len(bad_frames))
        if flip_bad:
            failures.append(f"{name}: flip_h={s.flip_h} but frames {_ranges(flip_bad)[:5]} measured the opposite flip")
        if bad_frames:
            failures.append(f"{name}: framing off on frames {_ranges(bad_frames)[:5]} (max scale err {worst_s:.2%}, "
                            f"pos {worst_p:.2f} px, rot {worst_r:.3f}°)")
        if measure is not None and ks:
            ind = _independent_framing(s, ks, raw_wh, centre, measure, int(sample_step), scale_tol, pos_tol, rot_tol,
                                       flip_margin)
            row["independent"] = ind
            if ind["n_measured"] and ind["n_bad"] > FRAMING_BAD_FRAC * ind["n_measured"]:
                failures.append(f"{name}: independently measured framing differs from the segment model on "
                                f"{ind['n_bad']}/{ind['n_measured']} sampled frames {ind['bad'][:5]} (max scale err "
                                f"{ind['max_scale_err']:.2%}, pos {ind['max_pos_err_px']:.2f} px, tolerance "
                                f"{scale_tol:.0%} / {pos_tol:g} px)")
            elif ind["n_bad"]:
                exceptions.append(f"{name}: independently measured framing off on {ind['n_bad']}/{ind['n_measured']} "
                                  f"sampled frames {ind['bad'][:5]}")
            if ind["n_samples"] and not ind["n_measured"]:
                exceptions.append(f"{name}: framing could not be measured independently (ECC did not converge on "
                                  f"{ind['n_samples']} sampled frames)")
            if ind["flip"] == "wrong":
                failures.append(f"{name}: flip_h={s.flip_h} but the mirrored hypothesis scores higher "
                                f"(median own - mirrored {ind['flip_median_diff']:+.4f})")
            elif ind["flip"] == "undecidable":
                exceptions.append(f"{name}: flip not decidable from the pixels (median own - mirrored "
                                  f"{ind['flip_median_diff']:+.4f}, symmetric content?)")
        rows.append(row)
    status = _status_from(len(failures), len(exceptions))
    summary = f"{len(rows)} raw segments: {len(failures)} problems, {len(exceptions)} exceptions"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "segments": rows}


def _independent_framing(seg: Segment, ks: Sequence[int], raw_wh: tuple[float, float], centre: np.ndarray,
                         measure: Callable[[Segment, int, Sim], dict | None], step: int, scale_tol: float,
                         pos_tol: float, rot_tol: float, flip_margin: float) -> dict:
    samples = framing_samples(seg, ks, step)
    bad, unconv, diffs = [], [], []
    n_meas = 0
    ws = wp = 0.0
    for k in samples:
        model = seg_sim(seg, k, *raw_wh)
        if model is None:
            continue
        r = measure(seg, k, model)
        if not r:
            continue
        own, oth = r.get("flip_own"), r.get("flip_other")
        if own is not None and oth is not None and math.isfinite(float(own)) and math.isfinite(float(oth)):
            diffs.append(float(own) - float(oth))
        meas = r.get("sim")
        if meas is None:
            continue
        es, ep, er = _sim_errors(model, meas, centre)
        z, zm = float(r.get("z", float("nan"))), float(r.get("z_model", float("nan")))
        if es <= scale_tol and ep <= pos_tol and er <= rot_tol:
            n_meas += 1
            ws, wp = max(ws, es), max(wp, ep)
            continue
        if math.isfinite(z) and (not math.isfinite(zm) or z > zm + 1e-4):
            n_meas += 1
            bad.append(int(k))
            ws, wp = max(ws, es), max(wp, ep)
        else:
            unconv.append(int(k))
    med = float(np.median(diffs)) if diffs else float("nan")
    flip = "n/a" if not diffs else ("ok" if med > flip_margin else ("wrong" if med < -flip_margin else "undecidable"))
    return {"n_samples": len(samples), "n_measured": n_meas, "n_bad": len(bad), "bad": bad[:50],
            "unconverged": unconv[:50], "max_scale_err": round(ws, 6), "max_pos_err_px": round(wp, 3),
            "flip": flip, "flip_median_diff": None if not diffs else round(med, 5), "flip_samples": len(diffs)}


# ---------------------------------------------------------------------------------------------
# s9_5 audio (criterion 5)
# ---------------------------------------------------------------------------------------------

def _overlaps(a0: int, a1: int, ranges: Iterable[dict]) -> bool:
    for r in ranges or []:
        try:
            if int(r["comp_in"]) < a1 and int(r["comp_out"]) > a0:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def check_audio(segments: Sequence[Segment], comp_y: np.ndarray, rec_y: np.ndarray, sr: int, comp_fps: Fraction,
                audio_block: dict, added_audio: list[dict], cfg: Any, xcorr: Callable | None = None) -> dict:
    """Per segment, cross-correlation lag of the recreated audio vs the competitor's within ±tol, or an
    explanation from the closed list (too_short, not_in_raw, audio_replaced, pitch_preserved,
    music_dominated, no_audio) -> pass_with_exceptions. A confident correlation (>= 0.8) with a lag out of
    tolerance is a failure whatever the code; an unknown code is a failure.

    Explanations come from the analysis (the segment's audio_align code, pitch analysis, the run-level
    audio status), never from this check: music_dominated is accepted only as the segment's own code.
    A weak peak is re-searched over ±AUDIO_WIDE_LAG_S; a clearly stronger peak at another lag (>= strong,
    or >= twice the ±100 ms peak and >= min_corr) is a gross misalignment and fails whatever the code.
    A weak peak on a segment the analysis found aligned (no code) fails."""
    if xcorr is None:
        from . import audio_align
        xcorr = audio_align.xcorr_lag
    tol = float(getattr(cfg, "audio_lag_tol_ms", 10.0))
    min_corr = float(getattr(cfg, "verify_audio_min_corr", 0.3))
    strong = float(getattr(cfg, "verify_audio_strong_corr", 0.8))
    min_dur = 0.5
    fps = float(Fraction(comp_fps))
    comp_y = np.asarray(comp_y if comp_y is not None else np.zeros(0), np.float32).reshape(-1)
    rec_y = np.asarray(rec_y if rec_y is not None else np.zeros(0), np.float32).reshape(-1)
    status_run = (audio_block or {}).get("status")
    rows, failures, exceptions = [], [], []
    for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
        au = s.audio or {}
        code_in = au.get("exception")
        row: dict[str, Any] = {"id": s.id, "type": s.type}
        name = _seg_name(s)
        if code_in is not None and code_in not in AUDIO_EXCEPTION_CODES:
            failures.append(f"{name}: audio exception code {code_in!r} is not in the closed list")
            row.update(result="fail", code=code_in)
            rows.append(row)
            continue
        if s.type in ("dip", "flash"):
            row.update(result="n/a")
            rows.append(row)
            continue
        if s.type == "not_in_raw":
            row.update(result="exception", code="not_in_raw")
            exceptions.append(f"{name}: not_in_raw")
            rows.append(row)
            continue
        a0 = int(s.comp_in) + int(au.get("in_offset_frames") or 0)
        a1 = int(s.comp_out) + int(au.get("out_offset_frames") or 0)
        dur = (a1 - a0) / fps
        s0, s1 = max(0, int(round(a0 / fps * sr))), int(round(a1 / fps * sr))
        row.update(audio_range=[a0, a1], duration_s=round(dur, 4))
        if comp_y.size == 0 or rec_y.size == 0 or status_run == "no_audio":
            row.update(result="exception", code="no_audio")
            exceptions.append(f"{name}: no_audio")
            rows.append(row)
            continue
        if dur < min_dur:
            row.update(result="exception", code="too_short")
            exceptions.append(f"{name}: too_short ({dur:.2f} s)")
            rows.append(row)
            continue
        a, b = comp_y[s0:s1], rec_y[s0:s1]
        n = min(len(a), len(b))
        a, b = a[:n], b[:n]
        if n < int(0.25 * sr) or float(np.sqrt(np.mean(b.astype(np.float64) ** 2))) < 1e-5 \
                or float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) < 1e-5:
            row.update(result="exception", code="no_audio", reason="silent")
            exceptions.append(f"{name}: no_audio (silent)")
            rows.append(row)
            continue
        lag_s, peak = xcorr(a, b, sr, 0.1)
        lag_ms, peak = float(lag_s) * 1000.0, float(peak)
        row.update(lag_ms=round(lag_ms, 3), corr=round(peak, 4))
        if peak >= min_corr and abs(lag_ms) <= tol:
            row["result"] = "ok"
            rows.append(row)
            continue
        if peak >= strong and abs(lag_ms) > tol:
            failures.append(f"{name}: audio confidently misaligned (lag {lag_ms:+.2f} ms, corr {peak:.2f})")
            row["result"] = "fail"
            rows.append(row)
            continue
        if peak < strong:
            wlag_s, wpeak = xcorr(a, b, sr, AUDIO_WIDE_LAG_S)
            wlag_ms, wpeak = float(wlag_s) * 1000.0, float(wpeak)
            row.update(wide_lag_ms=round(wlag_ms, 3), wide_corr=round(wpeak, 4))
            if abs(wlag_ms) > tol and (wpeak >= strong or (wpeak >= 2.0 * max(peak, 0.0) and wpeak >= min_corr)):
                failures.append(f"{name}: audio misaligned by {wlag_ms:+.1f} ms (corr {wpeak:.2f} there vs {peak:.2f} "
                                f"within ±100 ms)")
                row["result"] = "fail"
                rows.append(row)
                continue
        code = code_in
        evidence = "segment audio analysis"
        if code is None:
            if status_run == "audio_replaced":
                code, evidence = "audio_replaced", "run-level audio status"
            elif au.get("pitch_preserved"):
                code, evidence = "pitch_preserved", "pitch analysis"
        if code is None and peak < min_corr and au.get("corr") is not None:
            row["result"] = "fail"
            failures.append(f"{name}: recreated audio no longer correlates with the competitor (corr {peak:.2f}) although "
                            f"the audio analysis found this segment aligned (corr {float(au['corr']):.2f})")
            rows.append(row)
            continue
        if code in AUDIO_EXCEPTION_CODES:
            row.update(result="exception", code=code, evidence=evidence)
            exceptions.append(f"{name}: {code} (lag {lag_ms:+.2f} ms, corr {peak:.2f})")
        else:
            row["result"] = "fail"
            hint = " (low correlation under detected added audio, but the segment analysis did not report music " \
                   "dominance)" if peak < min_corr and _overlaps(a0, a1, added_audio) else ""
            failures.append(f"{name}: audio lag {lag_ms:+.2f} ms / corr {peak:.2f} outside ±{tol} ms and unexplained{hint}")
        rows.append(row)
    measured = [r for r in rows if "lag_ms" in r]
    status = _status_from(len(failures), len(exceptions))
    lags = [abs(r["lag_ms"]) for r in measured if r.get("result") == "ok"]
    summary = (f"{len(measured)} segments measured, max |lag| {max(lags):.2f} ms" if lags else f"{len(measured)} segments measured") \
        + f", {len(exceptions)} explained exceptions, {len(failures)} failures"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "segments": rows,
            "tolerance_ms": tol}


# ---------------------------------------------------------------------------------------------
# Recreation frame sources (s9_3 / s9_4): the preview video, or a match-geometry render in memory
# ---------------------------------------------------------------------------------------------

def _to_gray(img: np.ndarray) -> np.ndarray:
    import cv2
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    import cv2
    if (img.shape[1], img.shape[0]) == tuple(size):
        return img
    return cv2.resize(img, tuple(int(v) for v in size), interpolation=cv2.INTER_AREA)


class VideoFrames:
    """Frames of an encoded video (preview_recreation.mp4 or the competitor), PTS-indexed."""

    def __init__(self, path: str | Path, fps: Fraction | None = None):
        self.path, self.fps = str(path), fps

    def gray(self, n: int, size: tuple[int, int]) -> Iterator[tuple[int, np.ndarray]]:
        from .media import VideoReader
        with VideoReader(self.path, fps=self.fps) as vr:
            for k, img in vr.frames(0, n, fmt="gray", size=size):
                yield k, img

    def bgr(self, ks: Sequence[int]) -> dict[int, np.ndarray]:
        from .media import VideoReader
        with VideoReader(self.path, fps=self.fps) as vr:
            return vr.get_many(sorted(set(int(k) for k in ks)), fmt="bgr24")


class RenderedFrames:
    """Match-geometry recreation rendered in memory with render_preview.render_frame (used when
    preview_recreation.mp4 is not a match render at competitor size/fps, or was skipped)."""

    def __init__(self, render_ctx: Any, segments: Sequence[Segment], raw_path: str, comp_fps: Fraction,
                 raw_fps: Fraction, n_raw: int, chunk: int = 32):
        self.rctx, self.segments = render_ctx, [s for s in segments if s.type == "raw"]
        self.raw_path, self.comp_fps, self.raw_fps, self.n_raw, self.chunk = raw_path, comp_fps, raw_fps, n_raw, chunk
        self._reader = None

    def _vr(self):
        if self._reader is None:
            from .media import VideoReader
            self._reader = VideoReader(self.raw_path, fps=self.raw_fps)
        return self._reader

    def needed(self, k: int) -> set[int]:
        """RAW frames render_frame needs for frame k: render_preview.frame_sources when the render context
        supports it (any layout mode / MAIN grid), else the segments' AE-rule frames at comp frame k."""
        from . import render_preview
        fs = getattr(render_preview, "frame_sources", None)
        if fs is not None and not isinstance(self.rctx, dict):
            try:
                return {int(j) for _L, j, _w in fs(int(k), self.rctx) if j is not None and 0 <= int(j) < self.n_raw}
            except Exception:  # noqa: BLE001 - fall back to the segment models (render_frame retries a miss)
                pass
        out = set()
        for s in self.segments:
            if s.comp_in <= k < s.comp_out:
                j = seg_raw_frame(s, k, self.comp_fps, self.raw_fps, self.n_raw)
                if j is not None:
                    out.add(j)
        return out

    def bgr(self, ks: Sequence[int]) -> dict[int, np.ndarray]:
        from . import render_preview
        ks = [int(k) for k in ks]
        need = sorted(set().union(*[self.needed(k) for k in ks])) if ks else []
        frames = self._vr().get_many(need, fmt="bgr24") if need else {}
        out = {}
        for k in ks:
            for attempt in range(4):
                try:
                    out[k] = render_preview.render_frame(k, self.rctx, frames)
                    break
                except KeyError as e:
                    j = e.args[0] if e.args else None
                    if attempt == 3 or not isinstance(j, (int, np.integer)):
                        raise
                    frames.update(self._vr().get_many([int(j)], fmt="bgr24"))
        return out

    def gray(self, n: int, size: tuple[int, int]) -> Iterator[tuple[int, np.ndarray]]:
        for a in range(0, int(n), self.chunk):
            ks = list(range(a, min(int(n), a + self.chunk)))
            imgs = self.bgr(ks)
            for k in ks:
                yield k, _resize(_to_gray(imgs[k]), size)

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def probe_video(path: str | Path) -> dict:
    """Frame count (demuxed video packets), frame rate and size of an encoded video, and whether its PTS
    lie on a regular grid of ``fps``."""
    import av
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        tb = Fraction(s.time_base.numerator, s.time_base.denominator)
        rates = [Fraction(r.numerator, r.denominator) for r in (s.average_rate, s.guessed_rate, s.base_rate) if r]
        w, h = int(s.codec_context.width), int(s.codec_context.height)
        pts = sorted(int(p.pts) for p in c.demux(s) if p.pts is not None and p.size)
    return {"frames": len(pts), "rates": rates, "fps": rates[0] if rates else None, "width": w, "height": h,
            "pts": pts, "time_base": tb}


def check_preview_file(path: str | Path | None, n_main: int, main_fps: Fraction, main_size: tuple[int, int] | None,
                       render_bgr: Callable[[list[int]], dict[int, np.ndarray]] | None = None,
                       sample_ks: Sequence[int] | None = None, min_zncc: float = PREVIEW_ZNCC,
                       skipped: str | None = None) -> dict:
    """The DELIVERED preview_recreation.mp4 (verification-honesty F12): n_main frames on the main_fps grid
    at the MAIN size; with ``render_bgr`` (render_preview.render_frame in the run's own layout mode / size /
    fps) the delivered frames ``sample_ks`` must match the renderer (gray ZNCC >= min_zncc, or mean |diff|
    < 6 on uniform frames) -- this is how fill / source / --fps source previews are checked."""
    if skipped:
        return {"status": "not_available", "summary": f"preview not produced ({skipped})", "failures": []}
    if not path or not Path(path).exists():
        return {"status": "fail", "summary": "preview_recreation.mp4 missing", "failures": ["preview_recreation.mp4 missing"]}
    failures: list[str] = []
    info = probe_video(path)
    mf = Fraction(main_fps)
    if info["frames"] != int(n_main):
        failures.append(f"delivered preview has {info['frames']} frames, expected {n_main}")
    pts = np.asarray(info["pts"], np.int64)
    if pts.size:
        idx = (pts - pts[0]).astype(np.float64) * float(info["time_base"] * mf)
        dev = float(np.max(np.abs(idx - np.arange(pts.size)))) if pts.size else 0.0
        if dev > 0.1:
            failures.append(f"delivered preview frames are not on the {fps_str(mf)} grid (max deviation {dev:.2f} frames)")
    if info["rates"] and not any(abs(float(r) / float(mf) - 1.0) < 1e-5 for r in info["rates"]):
        failures.append(f"delivered preview frame rate {[fps_str(r) for r in info['rates']]} != {fps_str(mf)}")
    if main_size is not None:
        W, H = int(main_size[0]), int(main_size[1])
        if (info["width"], info["height"]) not in ((W, H), (W + W % 2, H + H % 2)):
            failures.append(f"delivered preview is {info['width']}x{info['height']}, MAIN is {W}x{H}")
    compared, bad = 0, []
    if render_bgr is not None and not failures:
        ks = sorted({int(k) for k in (sample_ks or []) if 0 <= int(k) < int(n_main)})
        if ks:
            from . import scoring
            got = VideoFrames(path, mf).bgr(ks)
            want = render_bgr(ks)
            for k in ks:
                a, b = got.get(k), want.get(k)
                if a is None or b is None:
                    bad.append({"K": k, "why": "frame missing"})
                    continue
                size = (360, max(2, int(round(360 * b.shape[0] / b.shape[1]))))
                ga = _resize(_to_gray(a), size).astype(np.float32)
                gb = _resize(_to_gray(b), size).astype(np.float32)
                compared += 1
                z = scoring.zncc(ga, gb)
                mad = float(np.mean(np.abs(ga - gb)))
                if not ((math.isfinite(z) and z >= min_zncc) or (not math.isfinite(z) and mad < 6.0)):
                    bad.append({"K": k, "zncc": None if not math.isfinite(z) else round(float(z), 4), "mad": round(mad, 2)})
            if bad:
                failures.append(f"delivered preview differs from the renderer on {len(bad)}/{len(ks)} sampled frames "
                                f"(ZNCC < {min_zncc}): {bad[:5]}")
    status = "fail" if failures else "pass"
    summary = (f"delivered preview: {info['frames']} frames at {fps_str(info['fps']) if info['fps'] else '?'} fps, "
               f"{info['width']}x{info['height']}" + (f"; {compared} frames compared with the renderer" if compared else ""))
    return {"status": status, "summary": summary, "failures": failures, "frames": info["frames"],
            "width": info["width"], "height": info["height"], "compared": compared, "mismatches": bad[:50]}


# ---------------------------------------------------------------------------------------------
# s9_3 visual (criterion 3)
# ---------------------------------------------------------------------------------------------

def _failure_image(path: Path, comp: np.ndarray, rec: np.ndarray, roi: tuple[int, int, int, int], score: float) -> None:
    import cv2
    x, y, w, h = roi
    a, b = comp[y:y + h, x:x + w].astype(np.float32), rec[y:y + h, x:x + w].astype(np.float32)
    d = np.clip(np.abs(a - b) * 4.0, 0, 255)
    tile = np.hstack([a, b, d]).astype(np.uint8)
    tile = cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)
    cv2.putText(tile, f"{path.stem} zncc {score:.3f}", (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), tile)


def placeholder_gray() -> float | None:
    """Luma (0..255) of the NOT-IN-RAW placeholder solid (export_ae.PLACEHOLDER_RGB)."""
    try:
        from .export_ae import PLACEHOLDER_RGB as rgb
    except ImportError:  # pragma: no cover
        return None
    r, g, b = (float(c) for c in list(rgb)[:3])
    return 255.0 * (0.299 * r + 0.587 * g + 0.114 * b)


def _is_placeholder(region: np.ndarray, want_gray: float | None, tol: float = 12.0, min_frac: float = 0.6) -> tuple[bool, str]:
    """Is a gray recreation region the placeholder solid (a label may be drawn on it)?"""
    vals = np.asarray(region, np.float32).ravel()
    if vals.size == 0:
        return False, "empty region"
    hist = np.bincount(np.clip(np.rint(vals), 0, 255).astype(np.int64), minlength=256)
    mode = int(np.argmax(np.convolve(hist, np.ones(9), mode="same")))
    frac = float(np.mean(np.abs(vals - mode) <= tol))
    if frac < min_frac:
        return False, f"not a solid ({frac:.0%} of the pixels near the dominant level {mode})"
    if want_gray is not None and abs(mode - want_gray) > 2 * tol:
        return False, f"solid of level {mode}, placeholder colour is {want_gray:.0f}"
    return True, f"solid level {mode} on {frac:.0%} of the pixels"


def check_visual(comp: Any, rec_frames: Iterable[tuple[int, np.ndarray]], fm: FrameMap,
                 allowed_fn: Callable[[int], np.ndarray | None], box: Box | dict | None, cfg: Any,
                 fail_dir: Path | None = None, source: str = "",
                 box_fn: Callable[[int], Box | dict | None] | None = None,
                 segments: Sequence[Segment] | None = None) -> dict:
    """Masked ZNCC competitor vs match-geometry recreation on every frame (video region of THAT frame --
    ``box_fn(k)``, e.g. the whole canvas in a full-screen period --, static and overlay pixels excluded,
    cfg.score_blur). Every matched frame must reach cfg.verify_zncc; every crossfade (BLEND) frame too
    (cfg.verify_blend_zncc when set). UNIFORM frames (dips / flashes): the recreation's region is uniform
    with the competitor's mean luma. NONE frames of a NOT-IN-RAW segment (``segments``): the recreation
    shows the placeholder solid."""
    from . import scoring
    thr = float(getattr(cfg, "verify_zncc", 0.9))
    blend_thr = float(getattr(cfg, "verify_blend_zncc", thr))
    blur = float(getattr(cfg, "score_blur", 1.0))
    uni_std = float(getattr(cfg, "uniform_std", 4.0))
    roi0 = proxy_roi(box, comp.size, comp.ratio)
    n = int(comp.n)
    status = np.asarray(fm.status)
    scores = np.full(n, np.nan, np.float64)
    seen = np.zeros(n, bool)
    fails: list[int] = []
    blend_fail: list[int] = []
    uniform_fail: list[dict] = []
    ph_fail: list[dict] = []
    nir = np.zeros(n, bool)
    for s in segments or []:
        if s.type == "not_in_raw":
            nir[max(0, s.comp_in):min(n, s.comp_out)] = True
    want_ph = placeholder_gray() if nir.any() else None
    n_img = 0
    n_uniform = n_ph = 0

    def image(k: int, c: np.ndarray, rec: np.ndarray, roi, s: float) -> None:
        nonlocal n_img
        if fail_dir is not None and n_img < MAX_FAILURE_IMAGES:
            _failure_image(Path(fail_dir) / f"k{k:05d}.png", c, rec, roi, 0.0 if math.isnan(s) else s)
            n_img += 1

    for k, rec in rec_frames:
        if not (0 <= k < n):
            continue
        seen[k] = True
        rec = _resize(_to_gray(rec), comp.size)
        c = np.asarray(comp.get(k))
        roi = roi0 if box_fn is None else proxy_roi(box_fn(k), comp.size, comp.ratio)
        x, y, w, h = roi
        allowed = allowed_fn(k)
        m = np.ones((h, w), bool) if allowed is None else np.asarray(allowed)[y:y + h, x:x + w].astype(bool)
        st = int(status[k]) if k < len(status) else Status.UNKNOWN
        if st == Status.UNIFORM:
            n_uniform += 1
            cv = c[y:y + h, x:x + w].astype(np.float32)[m]
            rv = rec[y:y + h, x:x + w].astype(np.float32)[m]
            if cv.size and rv.size:
                mc, mr, sr = float(cv.mean()), float(rv.mean()), float(rv.std())
                if abs(mc - mr) > UNIFORM_MEAN_TOL or sr > 2.0 * uni_std + 2.0:
                    uniform_fail.append({"k": int(k), "comp_mean": round(mc, 2), "rec_mean": round(mr, 2),
                                         "rec_std": round(sr, 2)})
                    image(k, c, rec, roi, float("nan"))
            continue
        if st == Status.NONE and nir[k]:
            n_ph += 1
            ok, why = _is_placeholder(rec[y:y + h, x:x + w], want_ph)
            if not ok:
                ph_fail.append({"k": int(k), "why": why})
                image(k, c, rec, roi, float("nan"))
            continue
        s = scoring.zncc(_blur(c[y:y + h, x:x + w], blur), _blur(rec[y:y + h, x:x + w], blur), m)
        scores[k] = s
        if st == Status.MATCH and not (s >= thr):
            fails.append(k)
            if not math.isnan(s):
                image(k, c, rec, roi, s)
        elif st == Status.BLEND and math.isfinite(s) and s < blend_thr:
            cv = c[y:y + h, x:x + w].astype(np.float32)[m]
            if cv.size and float(cv.std()) >= 2.0 * uni_std:      # near-uniform (deep in a dip): not gated
                blend_fail.append(k)
                image(k, c, rec, roi, s)
    matched = status[:n] == Status.MATCH
    missing = np.nonzero(matched & ~seen)[0]
    nan_fail = [k for k in fails if math.isnan(scores[k])]
    real_fail = [k for k in fails if not math.isnan(scores[k])]
    ms = scores[matched & np.isfinite(scores)]
    dist = {}
    if ms.size:
        dist = {"min": round(float(ms.min()), 5), "p1": round(float(np.percentile(ms, 1)), 5),
                "p5": round(float(np.percentile(ms, 5)), 5), "median": round(float(np.median(ms)), 5),
                "mean": round(float(ms.mean()), 5),
                "hist": {f"{a:.2f}-{b:.2f}": int(((ms >= a) & (ms < b)).sum())
                         for a, b in ((-1.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.98), (0.98, 0.99), (0.99, 1.01))}}
    blend = status[:n] == Status.BLEND
    bs = scores[blend & np.isfinite(scores)]
    failures = []
    if real_fail:
        failures.append(f"{len(real_fail)} matched frames below ZNCC {thr}: {_ranges(real_fail)[:10]}")
    if len(missing):
        failures.append(f"{len(missing)} matched frames missing from the recreation: {_ranges(missing)[:10]}")
    if blend_fail:
        failures.append(f"{len(blend_fail)} crossfade frames below ZNCC {blend_thr}: {_ranges(blend_fail)[:10]}")
    if uniform_fail:
        failures.append(f"{len(uniform_fail)} dip/flash (uniform) frames not reproduced (competitor vs recreation "
                        f"mean/std): {uniform_fail[:3]}")
    if ph_fail:
        failures.append(f"{len(ph_fail)} NOT-IN-RAW frames do not show the placeholder: {ph_fail[:3]}")
    exceptions = [f"{len(nan_fail)} matched frames unscorable (too few visible pixels): {_ranges(nan_fail)[:10]}"] if nan_fail else []
    status_out = _status_from(len(failures), len(exceptions))
    summary = (f"{int(matched.sum())} matched frames, min ZNCC {dist.get('min', float('nan'))}, median "
               f"{dist.get('median', float('nan'))}, {len(real_fail)} below {thr}; {int(blend.sum())} blend frames"
               f"{' (min ' + str(round(float(bs.min()), 5)) + ')' if bs.size else ''}, {n_uniform} uniform, "
               f"{n_ph} placeholder frames checked" + (f" [{source}]" if source else ""))
    return {"status": status_out, "summary": summary, "failures": failures, "exceptions": exceptions, "threshold": thr,
            "distribution": dist, "failed_frames": [int(k) for k in real_fail[:1000]],
            "blend_frames_min": round(float(bs.min()), 5) if bs.size else None, "blend_threshold": blend_thr,
            "blend_failed_frames": blend_fail[:1000], "uniform_frames_checked": n_uniform,
            "uniform_failed": uniform_fail[:200], "placeholder_frames_checked": n_ph, "placeholder_failed": ph_fail[:200],
            "source": source, "scores_file": None, "scores": scores}


# ---------------------------------------------------------------------------------------------
# s9_4 cut images
# ---------------------------------------------------------------------------------------------

def cut_frames(segments: Sequence[Segment]) -> list[int]:
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    return [int(b.comp_in) for b in segs[1:]]


def segment_labeler(segments: Sequence[Segment]) -> Callable[[int], str]:
    """frame -> 'S03' (or 'S02+S03' inside a transition overlap)."""
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))

    def f(k: int) -> str:
        return "+".join(_seg_name(s) for s in segs if s.comp_in <= k < s.comp_out)
    return f


def write_cut_images(cuts: Sequence[int], n_frames: int, comp_bgr: Callable[[list[int]], dict],
                     rec_bgr: Callable[[list[int]], dict], out_dir: Path, height: int = 320,
                     frame_label: Callable[[int], str] | None = None) -> dict:
    """debug/cuts/cut_XX.png: competitor (top) vs recreation (bottom) for frames k-1, k, k+1, k+2;
    the recreation tiles are labelled with the segment(s) covering each frame."""
    import cv2
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    want = sorted({f for k in cuts for f in (k - 1, k, k + 1, k + 2) if 0 <= f < n_frames})
    comp = comp_bgr(want) if want else {}
    rec = rec_bgr(want) if want else {}
    written, failures = [], []

    def tile(img: np.ndarray | None, text: str) -> np.ndarray:
        if img is None:
            img = np.zeros((height, int(height * 9 / 16), 3), np.uint8)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        w = max(1, int(round(img.shape[1] * height / img.shape[0])))
        t = cv2.resize(img, (w, height), interpolation=cv2.INTER_AREA)
        cv2.putText(t, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
        cv2.putText(t, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        return t

    for i, k in enumerate(cuts, start=1):
        fr = [f for f in (k - 1, k, k + 1, k + 2) if 0 <= f < n_frames]
        top = [tile(comp.get(f), f"comp {f}" + (" CUT" if f == k else "")) for f in fr]
        bot = [tile(rec.get(f), f"recr {f} {frame_label(f) if frame_label else ''}".rstrip()) for f in fr]
        wmax = max(t.shape[1] for t in top + bot)
        rows = [np.hstack([np.pad(t, ((0, 0), (0, wmax - t.shape[1]), (0, 0))) for t in row]) for row in (top, bot)]
        img = np.vstack(rows)
        p = out_dir / f"cut_{i:02d}.png"
        if cv2.imwrite(str(p), img):
            written.append(str(p))
        else:
            failures.append(f"could not write {p}")
    status = "fail" if failures or len(written) != len(cuts) else "pass"
    return {"status": status, "summary": f"{len(written)}/{len(cuts)} cut images in {out_dir}", "failures": failures,
            "images": written}


# ---------------------------------------------------------------------------------------------
# s9_6 aerender (criterion 6 when After Effects is installed)
# ---------------------------------------------------------------------------------------------

OM_TEMPLATES = ("PNG Sequence", "Lossless", "TIFF Sequence with Alpha")


def compare_render_to_preview(render: Iterable[tuple[int, np.ndarray]], preview: Iterable[tuple[int, np.ndarray]],
                              cfg: Any, n_expected: int | None = None) -> dict:
    """AE render frame K must match preview frame K better than K-1 / K+1 (same RAW frame on every frame).
    Frames whose preview neighbours are identical (ZNCC >= identical_thresh) are exempt (listed). With
    ``n_expected`` the render must have exactly that many frames (a truncated render fails). Together with
    s9_2 (the plan shows m(k) on every matched frame) this identifies the RAW frame of every AE frame."""
    from . import scoring
    thr = float(getattr(cfg, "verify_zncc", 0.9))
    ident = float(getattr(cfg, "identical_thresh", 0.9995))
    pv = dict(preview)
    bad, amb, n = [], [], 0
    seen: set[int] = set()
    for K, r in render:
        seen.add(int(K))
        p = pv.get(K)
        if p is None:
            bad.append({"K": K, "reason": "no preview frame"})
            continue
        n += 1
        s0 = scoring.zncc(r, p)
        neigh = [(d, pv.get(K + d)) for d in (-1, 1) if pv.get(K + d) is not None]
        s_n = {d: scoring.zncc(r, q) for d, q in neigh}
        same = [d for d, q in neigh if scoring.zncc(p, q) >= ident]
        if not (s0 >= thr) or any(s_n[d] > s0 for d in s_n if d not in same):
            bad.append({"K": K, "zncc": s0, "neighbours": s_n})
        elif same:
            amb.append(K)
    failures = [f"AE render differs from preview at frames {[b['K'] for b in bad[:10]]}"] if bad else []
    if n_expected is not None and len(seen) != int(n_expected):
        missing = sorted(set(range(int(n_expected))) - seen)
        failures.append(f"AE render has {len(seen)} frames, MAIN has {n_expected}"
                        + (f" (missing {_ranges(missing)[:5]})" if missing else ""))
    status = "fail" if failures else ("pass_with_exceptions" if amb else "pass")
    return {"status": status, "summary": f"{n} AE frames vs preview: {len(bad)} mismatches, {len(amb)} ambiguous"
            + (f" ({len(seen)}/{n_expected} frames rendered)" if n_expected is not None else ""),
            "failures": failures, "mismatches": bad[:200], "ambiguous": amb[:200], "frames_rendered": len(seen)}


def check_ae_render(env: dict, aep: str | None, preview: str | None, n_main: int, main_fps: Fraction,
                    proxy_size: tuple[int, int], out_dir: Path, cfg: Any) -> dict:
    """Render MAIN with aerender (if installed) and compare every frame with preview_recreation.mp4."""
    aerender = (env or {}).get("aerender")
    if not aerender:
        return {"status": "not_available", "summary": "aerender not available on this machine (criterion 6 is mock-only)",
                "failures": []}
    if not aep or not Path(aep).exists():
        return {"status": "fail", "summary": "After Effects is installed but recreated_edit.aep was not produced",
                "failures": ["recreated_edit.aep missing"]}
    if not preview or not Path(preview).exists():
        return {"status": "fail", "summary": "no preview_recreation.mp4 to compare the AE render with",
                "failures": ["preview missing"]}
    import cv2
    out_dir = Path(out_dir)
    tried = []
    frames: list[Path] = []
    movie = None
    for tmpl in OM_TEMPLATES:
        # a fresh folder per attempt: frames left by an earlier run or a failed template are never compared
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / ("ae_[#####].png" if "PNG" in tmpl else ("ae_[#####].tif" if "TIFF" in tmpl else "ae_render.mov"))
        cmd = [aerender, "-project", str(Path(aep).resolve()), "-comp", MAIN_COMP_NAME, "-RStemplate", "Best Settings",
               "-OMtemplate", tmpl, "-output", str(target)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
        except (OSError, subprocess.SubprocessError) as e:
            tried.append({"template": tmpl, "error": str(e)})
            continue
        tried.append({"template": tmpl, "returncode": res.returncode, "stdout": (res.stdout or "")[-500:]})
        if res.returncode != 0:
            continue                       # a failed render never counts, whatever it left behind
        frames = sorted(out_dir.glob("ae_*.png")) or sorted(out_dir.glob("ae_*.tif"))
        if frames:
            break
        if (out_dir / "ae_render.mov").exists():
            movie = out_dir / "ae_render.mov"
            break
    if not frames and movie is None:
        return {"status": "fail", "summary": "aerender produced no frames", "failures": ["aerender failed"], "tried": tried}

    def render_iter():
        if frames:
            for K, p in enumerate(frames):
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                yield K, _resize(img, proxy_size).astype(np.float32)
        else:
            for K, img in VideoFrames(movie, main_fps).gray(n_main + 1, proxy_size):
                yield K, img.astype(np.float32)

    prev = [(K, img.astype(np.float32)) for K, img in VideoFrames(preview, main_fps).gray(n_main, proxy_size)]
    res = compare_render_to_preview(render_iter(), prev, cfg, n_expected=n_main)
    res["tried"] = tried
    return res


# ---------------------------------------------------------------------------------------------
# s9_7 determinism
# ---------------------------------------------------------------------------------------------

def canonical_cutlist(d: dict) -> dict:
    """Cutlist dict without provenance.timings (the only wall-clock field)."""
    c = copy.deepcopy(d)
    prov = c.get("provenance")
    if isinstance(prov, dict):
        prov.pop("timings", None)
    return c


def canonical_json(d: dict) -> str:
    return json.dumps(canonical_cutlist(json.loads(json.dumps(d, default=json_default))), sort_keys=True,
                      separators=(",", ":"), default=json_default)


def _diff_paths(a: Any, b: Any, path: str, out: list[str], limit: int = 50) -> None:
    if len(out) >= limit:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'second' if k not in a else 'first'}")
            else:
                _diff_paths(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _diff_paths(x, y, f"{path}[{i}]", out, limit)
    elif a != b or type(a) is not type(b):
        out.append(f"{path}: {a!r} != {b!r}")


def compare_cutlists(a: dict, b: dict) -> dict:
    """Byte-compare the canonical JSON of two cutlists (provenance.timings excluded)."""
    ja, jb = canonical_json(a), canonical_json(b)
    diffs: list[str] = []
    if ja != jb:
        _diff_paths(json.loads(ja), json.loads(jb), "", diffs)
    return {"identical": ja == jb, "differences": diffs, "bytes": len(ja)}


def check_determinism(ctx: Any) -> dict:
    """Re-run S5.4 -> S6 from the cached FrameMap / AudioHints in a fresh context and byte-compare the
    cutlist JSON (timings excluded); also check that the written cutlist.json equals the in-memory one."""
    from . import pipeline
    first = ctx.cutlist.to_dict()
    second = pipeline.rerun_assembly(ctx).to_dict()
    cmp = compare_cutlists(first, second)
    failures = []
    if not cmp["identical"]:
        failures.append(f"re-assembly from caches differs: {cmp['differences'][:5]}")
    p = Path(ctx.cfg.out) / "cutlist.json"
    if p.exists():
        disk = compare_cutlists(json.loads(p.read_text()), first)
        if not disk["identical"]:
            failures.append(f"written cutlist.json differs from the in-memory cutlist: {disk['differences'][:5]}")
    prev = compare_with_previous_run(getattr(ctx, "previous_cutlist", None), first)
    warnings = []
    if prev.get("compared") and not prev.get("identical"):
        failures.append("cutlist.json differs from the previous run with identical inputs, parameters, settings and "
                        f"tool / stage versions ({prev['differences'][:3]}): Stage 9.7 requires a re-run to reproduce it")
    status = "fail" if failures else "pass"
    summary = "cutlist re-assembled from caches is byte-identical" if not failures else "cutlist NOT reproducible"
    if prev.get("compared"):
        summary += "; " + ("identical to the previous run" if prev.get("identical") else "DIFFERS from the previous run")
    return {"status": status, "summary": summary, "failures": failures, "differences": cmp["differences"],
            "previous_run": prev, "warnings": warnings}


def compare_with_previous_run(previous: dict | None, current: dict) -> dict:
    """Compare with the previous run's cutlist.json when it was made from the same inputs, analysis
    parameters, settings and tool / stage versions (else the comparison is skipped)."""
    if not previous:
        return {"compared": False, "reason": "no previous cutlist.json"}
    keys = ("version", "input_hashes", "analysis_params_hash", "stage_versions", "code_hash")
    pp, cp = previous.get("provenance") or {}, current.get("provenance") or {}
    same = all(json.dumps(pp.get(k), sort_keys=True, default=json_default) == json.dumps(cp.get(k), sort_keys=True, default=json_default)
               for k in keys)
    same = same and json.dumps(previous.get("settings"), sort_keys=True) == json.dumps(
        json.loads(json.dumps(current.get("settings"), default=json_default)), sort_keys=True)
    if not same:
        return {"compared": False, "reason": "inputs, parameters, settings or tool version changed"}
    cmp = compare_cutlists(previous, current)
    return {"compared": True, "identical": cmp["identical"], "differences": cmp["differences"][:20]}


# ---------------------------------------------------------------------------------------------
# s9_8 deliverables (DESIGN §7 D5)
# ---------------------------------------------------------------------------------------------

def _media_file(block: dict | None, out_dir: Path) -> Path | None:
    """The file the JSX imports for a cutlist media block: <out>/<file_rel>, else file_abs."""
    b = block or {}
    rel = str(b.get("file_rel") or "")
    f = str(b.get("file") or "")
    if not rel and f and not os.path.isabs(f):
        rel = f
    if rel:
        return out_dir / rel
    ab = str(b.get("file_abs") or (f if os.path.isabs(f) else ""))
    return Path(ab) if ab else None


def check_deliverables(ctx: Any, n_cuts: int | None = None) -> dict:
    """Every deliverable of the prompt's output tree exists (unless the run explicitly skipped it), the XML /
    EDL re-parse validation passed and no export stage recorded an error (DESIGN §7 D5, REQ-6). When the
    pipeline recorded which deliverables THIS run produced (``ctx.exports['missing']``, from
    pipeline.collect_deliverables) a file left over from an earlier run does not count either.
    report.md and verify.json are written after verification (their failure is a run error). Missing debug
    plots are listed, not failed (they are diagnostics)."""
    cfg = ctx.cfg
    out = Path(cfg.out) if hasattr(cfg, "out") else Path(getattr(cfg, "out_dir", "."))
    paths = dict(getattr(ctx, "paths", {}) or {})
    cl = getattr(ctx, "cutlist", None)
    env = getattr(ctx, "env", {}) or {}
    exports = getattr(ctx, "exports", None) or {}
    failures: list[str] = []
    warnings: list[str] = []
    skipped: dict[str, str] = {}
    items: list[dict] = []
    failed_keys: set[str] = set()

    def need(key: str, path: Path | None, what: str) -> None:
        ok = path is not None and Path(path).exists()
        items.append({"key": key, "deliverable": what, "path": None if path is None else str(path), "exists": ok})
        if not ok:
            failed_keys.add(key)
            failures.append(f"{what} missing" + (f" ({path})" if path is not None else ""))

    need("jsx", Path(paths.get("jsx") or out / "build_ae_project.jsx"), "build_ae_project.jsx")
    if env.get("ae_app"):
        need("aep", Path(paths.get("aep") or out / "recreated_edit.aep"), "recreated_edit.aep (After Effects is installed)")
    else:
        skipped["aep"] = "recreated_edit.aep (After Effects not installed)"
    if cl is not None:
        need("media_raw", _media_file(cl.raw, out), "RAW media imported by the JSX")
        need("media_competitor", _media_file(cl.competitor, out), "competitor reference media")
    need("cutlist", Path(paths.get("cutlist") or out / "cutlist.json"), "cutlist.json")
    need("csv", Path(paths.get("csv") or out / "cutlist.csv"), "cutlist.csv")
    need("xml", Path(paths.get("xml") or out / "recreated_edit.xml"), "recreated_edit.xml")
    need("edl", Path(paths.get("edl") or out / "recreated_edit.edl"), "recreated_edit.edl")
    val_ok = exports.get("validation_ok", exports.get("ok"))
    if val_ok is not True:
        failures.append("XML/EDL re-parse validation did not pass: "
                        + str(exports.get("errors") or exports.get("error") or "validation did not run"))
    if getattr(cfg, "skip_preview", False):
        skipped["preview"] = "preview_recreation.mp4 (--skip-preview)"
    else:
        need("preview", Path(paths.get("preview") or out / "preview_recreation.mp4"), "preview_recreation.mp4")
    if getattr(cfg, "skip_compare", False):
        skipped["compare"] = "compare.mp4 (--skip-compare)"
    else:
        need("compare", Path(paths.get("compare") or out / "compare.mp4"), "compare.mp4")
    pipe_skipped = exports.get("skipped") or {}
    for key in exports.get("missing") or []:
        if key not in failed_keys and key not in skipped and key not in pipe_skipped:
            failed_keys.add(str(key))
            failures.append(f"deliverable {key!r} was not produced by this run (a file from an earlier run does not count)")
    dbg = Path(getattr(cfg, "debug_dir", out / "debug"))
    for name in ("mapping.png", "scores.png", "layout.png"):
        if not (dbg / name).exists():
            warnings.append(f"debug/{name} missing")
    if n_cuts is not None:
        got = len(list((dbg / "cuts").glob("cut_*.png"))) if (dbg / "cuts").is_dir() else 0
        if got < n_cuts:
            failures.append(f"debug/cuts has {got} cut images, the edit has {n_cuts} cuts")
    errors = list(getattr(ctx, "errors", None) or [])
    for e in errors:
        failures.append(f"stage error {e.get('stage')}: {e.get('error')}")
    status = "fail" if failures else "pass"
    summary = (f"{sum(1 for i in items if i['exists'])}/{len(items)} deliverables present"
               + (f", {len(skipped)} skipped ({'; '.join(skipped.values())})" if skipped else "")
               + f", exports {'validated' if val_ok is True else 'NOT validated'}, {len(errors)} stage errors")
    return {"status": status, "summary": summary, "failures": failures, "warnings": warnings,
            "skipped": list(skipped.values()), "items": items}


# ---------------------------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------------------------

def _full_canvas(cl: Any) -> dict:
    return {"x": 0.0, "y": 0.0, "w": float(cl.competitor["width"]), "h": float(cl.competitor["height"]),
            "corner_radius": 0.0}


def frame_box_fn(segments: Sequence[Segment], layout_block: dict | None, dominant: Box | dict | None,
                 canvas: dict, n: int) -> Callable[[int], Box | dict | None]:
    """Video box in force at competitor frame k (DESIGN §7 D1): the segment's own box when it has one,
    else the whole canvas inside a full-screen layout period, else the dominant layout box."""
    over: dict[int, Box | dict] = {}
    for p in (layout_block or {}).get("periods") or []:
        if isinstance(p, dict) and str(p.get("mode")) == "fullscreen":
            for k in range(max(0, int(p["comp_in"])), min(n, int(p["comp_out"]))):
                over[k] = canvas
    for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
        if s.box:
            for k in range(max(0, s.comp_in), min(n, s.comp_out)):
                over[k] = s.box
    return lambda k: over.get(int(k), dominant)


def _allowed_fn(ctx: Any, box_fn: Callable[[int], Box | dict | None] | None = None) -> Callable[[int], np.ndarray | None]:
    """Per-frame scoring mask: layout.allowed_mask (dominant box & ~static & ~overlay(k)); on frames shown in
    a larger box (full-screen periods) also the pixels of that box outside the dominant box that are not
    covered by an active zone, caption or overlay (the dominant layout's static canvas shows video there)."""
    from . import layout as layout_mod
    memo: dict[int, np.ndarray] = {}
    lay = getattr(ctx, "layout", None)
    comp = ctx.comp_proxy
    dom = _box(ctx)
    dom_roi = proxy_roi(dom, comp.size, comp.ratio) if dom is not None else None
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    d = int(getattr(ctx.overlays, "dilate_px", getattr(ctx.cfg, "overlay_dilate_px", 3)) or 0) if ctx.overlays is not None \
        else int(getattr(ctx.cfg, "overlay_dilate_px", 3))

    def rect_off(m: np.ndarray, x: float, y: float, w: float, h: float) -> None:
        x0, y0 = max(0, int(math.floor(x * rx)) - d), max(0, int(math.floor(y * ry)) - d)
        x1, y1 = int(math.ceil((x + w) * rx)) + d, int(math.ceil((y + h) * ry)) + d
        m[y0:max(y0, y1), x0:max(x0, x1)] = False

    def widened(k: int, base: np.ndarray) -> np.ndarray:
        b = box_fn(k) if box_fn is not None else None
        if b is None or dom_roi is None:
            return base
        roi = proxy_roi(b, comp.size, comp.ratio)
        if roi == dom_roi:
            return base
        extra = np.zeros(base.shape, bool)
        x, y, w, h = roi
        extra[y:y + h, x:x + w] = True
        dx, dy, dw, dh = dom_roi
        extra[dy:dy + dh, dx:dx + dw] = False
        for z in (getattr(lay, "zones", None) or []):
            zi, zo = getattr(z, "comp_in", None), getattr(z, "comp_out", None)
            if (zi is None or int(zi) <= k) and (zo is None or k < int(zo)):
                rect_off(extra, float(z.x), float(z.y), float(z.w), float(z.h))
        for c in (getattr(lay, "captions", None) or []):
            if int(c.get("comp_in", 0)) <= k < int(c.get("comp_out", 0)) and all(q in c for q in ("x", "y", "w", "h")):
                rect_off(extra, float(c["x"]), float(c["y"]), float(c["w"]), float(c["h"]))
        ov = None
        if ctx.overlays is not None:
            try:
                ov = ctx.overlays.get_dilated(int(k), d) if hasattr(ctx.overlays, "get_dilated") else ctx.overlays.get(int(k))
            except Exception:  # noqa: BLE001 - no overlay mask for this frame
                ov = None
        if ov is not None and np.asarray(ov).shape == extra.shape:
            extra &= ~np.asarray(ov, bool)
        return base | extra

    def f(k: int) -> np.ndarray | None:
        if k not in memo:
            if len(memo) > 64:
                memo.clear()
            base = layout_mod.allowed_mask(ctx.layout, ctx.overlays, int(k), ctx.comp_proxy)
            memo[k] = widened(int(k), np.asarray(base, bool)) if base is not None else base
        return memo[k]
    return f


def _box(ctx: Any) -> Box | dict | None:
    if getattr(ctx, "layout", None) is not None and ctx.layout.box is not None:
        return ctx.layout.box
    return (ctx.cutlist.layout or {}).get("box")


def _run_check(name: str, fn: Callable[[], dict]) -> dict:
    try:
        res = fn()
        if not isinstance(res, dict) or res.get("status") not in STATUSES:
            raise ValueError(f"check returned an invalid result: {res!r:.200}")
        return res
    except Exception as e:  # noqa: BLE001 - a crashed check is a failed check (never a pass)
        log.error("verification check %s crashed: %s\n%s", name, e, traceback.format_exc())
        return {"status": "fail", "summary": f"check crashed: {type(e).__name__}: {e}",
                "failures": [f"{name} crashed: {type(e).__name__}: {e}"]}


def _preview_is_match_render(ctx: Any, main_fps: Fraction) -> bool:
    """The delivered preview is itself a match-geometry render on the competitor's grid (any proportional
    size: check_visual resizes it to the proxy)."""
    p = (getattr(ctx, "paths", {}) or {}).get("preview")
    return (str(getattr(ctx.cfg, "layout_mode", "match")) == "match" and Fraction(main_fps) == Fraction(ctx.comp_fps)
            and bool(p) and Path(p).exists())


def _recreation_source(ctx: Any, main_fps: Fraction | None = None) -> tuple[Any, str]:
    """(frame source, description) of the match-geometry recreation at competitor size and fps."""
    from . import pipeline
    mf = Fraction(main_fps) if main_fps is not None else Fraction(ctx.main_fps or ctx.comp_fps)
    if _preview_is_match_render(ctx, mf):
        return VideoFrames(ctx.paths["preview"], ctx.comp_fps), "preview_recreation.mp4"
    rctx = pipeline.match_render_context(ctx)
    return RenderedFrames(rctx, ctx.cutlist.segments, ctx.raw_info.path, ctx.comp_fps, ctx.raw_fps,
                          int(ctx.raw_info.nb_frames)), "in-memory match render"


def _audio_for_check(ctx: Any) -> tuple[np.ndarray, np.ndarray, int, str]:
    """(competitor audio, RAW audio, sr, description): original-rate mono when both files share a rate
    and are short enough, else the 16 kHz analysis audio."""
    cfg = ctx.cfg
    ci, ri = ctx.comp_info, ctx.raw_info
    max_s = float(getattr(cfg, "verify_full_rate_max_s", 900.0))    # bounds memory (48 kHz stereo float32)
    if (ci.has_audio and ri.has_audio and ci.a_sample_rate and ci.a_sample_rate == ri.a_sample_rate
            and ri.duration <= max_s):
        try:
            from . import proxies
            cy, csr = proxies.load_audio_full(ci)
            ry, rsr = proxies.load_audio_full(ri)
            if csr == rsr and len(cy) and len(ry):
                to_mono = lambda y: np.asarray(y, np.float32).reshape(len(y), -1).mean(axis=1)  # noqa: E731
                return to_mono(cy), to_mono(ry), int(csr), f"original rate {csr} Hz"
        except Exception as e:  # noqa: BLE001 - fall back to the analysis audio, logged
            log.warning("original-rate audio unavailable for verification (%s); using analysis audio", e)
    cy = ctx.comp_audio if ctx.comp_audio is not None else np.zeros(0, np.float32)
    ry = ctx.raw_audio if ctx.raw_audio is not None else np.zeros(0, np.float32)
    return cy, ry, int(ctx.audio_sr), f"analysis rate {ctx.audio_sr} Hz"


def framing_measure(comp: Any, raw: Any, scorer: Any, allowed_fn: Callable[[int], np.ndarray | None],
                    box_fn: Callable[[int], Box | dict | None], raw_wh: tuple[float, float], comp_fps: Fraction,
                    raw_fps: Fraction, n_raw: int | None, cfg: Any) -> Callable[[Segment, int, Sim], dict | None]:
    """measure(seg, k, model) for check_speed_framing: refine.refine_transform (ECC on the competitor proxy
    ROI with the allowed mask) started from the model perturbed by ±FRAMING_PERTURB, the better of the two
    runs; its score and the model's on the same pixels; the model's score with its own flip vs the mirrored
    hypothesis (same Sim, opposite flip)."""
    from . import refine
    rel, px = FRAMING_PERTURB

    def measure(seg: Segment, k: int, model: Sim) -> dict | None:
        j = seg_raw_frame(seg, k, comp_fps, raw_fps, n_raw)
        if j is None or not raw.has(int(j)) or not (0 <= k < comp.n) or not comp.has(int(k)):
            return None
        flip = bool(seg.flip_h)
        b = box_fn(k)
        bb = Box.from_dict(b) if isinstance(b, dict) else b
        centre = (bb.x + bb.w / 2.0, bb.y + bb.h / 2.0) if bb is not None else (comp.full_size[0] / 2.0, comp.full_size[1] / 2.0)
        roi = proxy_roi(b, comp.size, comp.ratio)
        comp_img, raw_img = np.asarray(comp.get(int(k))), np.asarray(raw.get(int(j)))
        best = None
        first = 1.0 if int(k) % 2 == 0 else -1.0          # alternate the side of the start between samples
        for sign in (first, -first):
            init = perturb_sim(model, centre, sign * rel, sign * px, sign * px)
            try:
                sim, z = refine.refine_transform(comp_img, raw_img, init, flip, float(raw_wh[0]), raw.ratio, comp.ratio,
                                                 allowed_fn(k), cfg, roi)
            except Exception:  # noqa: BLE001 - an ECC failure is an unmeasured sample, not a crash
                continue
            if sim is init or not math.isfinite(float(z)):
                continue                        # ECC did not improve on the perturbed start: try the other side
            best = (sim, float(z))
            break
        sc = scorer.score(k, [(j, model, flip), (j, model, not flip)])
        out = {"flip_own": float(sc[0]), "flip_other": float(sc[1]), "sim": None, "z": float("nan"),
               "z_model": float(sc[0])}
        if best is not None:
            both = scorer.score(k, [(j, best[0], flip), (j, model, flip)])
            out.update(sim=best[0], z=float(both[0]), z_model=float(both[1]))
        return out
    return measure


def verify_all(ctx: Any) -> dict:
    """Stage 9: every check s9_1..s9_8 and the acceptance criteria c1..c6 (see module docstring)."""
    from . import pipeline
    cfg = ctx.cfg
    cl = ctx.cutlist
    if cl is None or ctx.fm is None:
        return crashed_result("no cutlist / frame map (analysis did not complete)")
    segs = list(cl.segments)
    comp_fps, raw_fps = ctx.comp_fps, ctx.raw_fps
    main_fps = ctx.main_fps or pipeline.resolve_main_fps(cfg, comp_fps, raw_fps)
    n = int(ctx.n_comp)
    n_main = pipeline.main_frame_count(n, comp_fps, main_fps)
    n_raw = int(ctx.raw_info.nb_frames)
    raw_wh = (float(cl.raw["width"]), float(cl.raw["height"]))
    comp_wh = (float(cl.competitor["width"]), float(cl.competitor["height"]))
    raw_name = Path(ctx.raw_info.path).name
    checks: dict[str, dict] = {}
    extra: dict[str, dict] = {}

    checks["s9_1_coverage"] = _run_check("s9_1_coverage", lambda: check_coverage(segs, n, cl.layout))

    box_fn = frame_box_fn(segs, cl.layout, _box(ctx), _full_canvas(cl), n)
    allowed = _allowed_fn(ctx, box_fn)
    scorer = None

    def get_scorer() -> ProxyScorer:
        nonlocal scorer
        if scorer is None:
            scorer = ProxyScorer(ctx.comp_proxy, ctx.raw_proxy, _box(ctx), allowed, raw_wh[0], cfg, box_fn=box_fn)
        return scorer

    extra["cuts"] = _run_check("c2_cuts", lambda: check_cuts(segs, comp_fps, raw_fps, raw_wh, n_raw, get_scorer(), cfg))

    # s9_2: AE semantics from the plan and from the mock-run record
    cut_main = [pipeline.to_main_frame(k, comp_fps, main_fps) for k in cut_frames(segs)]

    def s9_2_plan() -> dict:
        from . import export_ae
        if ctx.plan is None:
            return {"status": "fail", "summary": "no AE plan", "failures": ["ae_plan missing"]}
        return check_ae_sim(export_ae.simulate_ae(ctx.plan), ctx.fm, comp_fps, main_fps, n_main, cut_main, cfg, "plan",
                            segments=segs, raw_fps=raw_fps, n_raw=n_raw)

    def s9_2_mock() -> dict:
        rec = (ctx.mock or {}).get("default")
        if not rec or _get(rec, "status") == "not_available":
            return {"status": "not_available", "summary": "mock run not available", "failures": []}
        if _get(rec, "status") == "error":
            return {"status": "fail", "summary": "mock run failed", "failures": [str(_get(rec, "error"))]}
        sim = simulate_record(rec, raw_name, raw_fps, main_fps, n_main)
        return check_ae_sim(sim, ctx.fm, comp_fps, main_fps, n_main, cut_main, cfg, "mock record",
                            segments=segs, raw_fps=raw_fps, n_raw=n_raw)

    p2, m2 = _run_check("s9_2_plan", s9_2_plan), _run_check("s9_2_mock", s9_2_mock)
    checks["s9_2_ae_sim"] = {"status": aggregate([p2["status"], m2["status"]]),
                             "summary": f"{p2.get('summary')} | {m2.get('summary')}",
                             "failures": p2.get("failures", []) + m2.get("failures", []),
                             "exceptions": p2.get("exceptions", []) + m2.get("exceptions", []), "plan": p2, "mock": m2}

    # s9_3 visual + s9_4 cut images on the match-geometry recreation
    src_holder: dict[str, Any] = {}

    def get_src():
        if "src" not in src_holder:
            src_holder["src"], src_holder["desc"] = _recreation_source(ctx, main_fps)
        return src_holder["src"], src_holder["desc"]

    def preview_file(desc: str) -> dict:
        from . import render_preview
        if getattr(cfg, "skip_preview", False):
            return check_preview_file(None, n_main, main_fps, None, skipped="--skip-preview")
        path = (ctx.paths or {}).get("preview")
        render_bgr, ks = None, None
        if desc != "preview_recreation.mp4" and path and Path(path).exists():
            mctx = render_preview.make_context(cl, cfg)
            render_bgr = RenderedFrames(mctx, segs, ctx.raw_info.path, comp_fps, raw_fps, n_raw).bgr
            ks = sorted({int(round(i * (n_main - 1) / max(1, PREVIEW_SAMPLES - 1))) for i in range(PREVIEW_SAMPLES)})
        return check_preview_file(path, n_main, main_fps, ctx.main_size, render_bgr, ks)

    def s9_3() -> dict:
        src, desc = get_src()
        res = check_visual(ctx.comp_proxy, src.gray(n, ctx.comp_proxy.size), ctx.fm, allowed, _box(ctx), cfg,
                           cfg.debug_dir / "verify_failures", desc, box_fn=box_fn, segments=segs)
        scores = res.pop("scores")
        np.save(Path(cfg.work) / "verify_zncc.npy", scores.astype(np.float32))
        res["scores_file"] = str(Path(cfg.work) / "verify_zncc.npy")
        pf = _run_check("s9_3_preview_file", lambda: preview_file(desc))
        res["preview_file"] = {k: v for k, v in pf.items() if k != "failures"}    # failures listed once, below
        res["failures"] = list(res.get("failures", [])) + [f"delivered preview: {f}" for f in pf.get("failures", [])]
        res["status"] = aggregate([res["status"], pf["status"]])
        res["summary"] = f"{res['summary']}; {pf.get('summary')}"
        return res

    checks["s9_3_visual"] = _run_check("s9_3_visual", s9_3)

    def s9_4() -> dict:
        src, _desc = get_src()
        comp_src = VideoFrames(ctx.comp_info.path, comp_fps)
        return write_cut_images(cut_frames(segs), n, comp_src.bgr, src.bgr, cfg.debug_dir / "cuts",
                                frame_label=segment_labeler(segs))

    checks["s9_4_cut_images"] = _run_check("s9_4_cut_images", s9_4)
    if isinstance(src_holder.get("src"), RenderedFrames):
        src_holder["src"].close()

    def s9_5() -> dict:
        from . import render_preview
        cy, ry, sr, desc = _audio_for_check(ctx)
        rec_y = render_preview.build_audio(cl, ry, sr) if len(ry) else np.zeros(0, np.float32)
        res = check_audio(segs, cy, rec_y, sr, comp_fps, cl.audio, cl.added_audio, cfg)
        res["audio_source"] = desc
        return res

    checks["s9_5_audio"] = _run_check("s9_5_audio", s9_5)

    def c4() -> dict:
        measure = None
        if getattr(ctx, "comp_proxy", None) is not None and getattr(ctx, "raw_proxy", None) is not None:
            measure = framing_measure(ctx.comp_proxy, ctx.raw_proxy, get_scorer(), allowed, box_fn, raw_wh, comp_fps,
                                      raw_fps, n_raw, cfg)
        return check_speed_framing(segs, ctx.fm, comp_fps, raw_fps, raw_wh, _box(ctx), comp_wh, cfg, measure=measure)

    extra["framing"] = _run_check("c4_speed_framing", c4)

    extra["mock"] = _run_check("c6_mock", lambda: check_mock(ctx.plan, ctx.mock, main_fps, n_main,
                                                             Path(ctx.paths.get("jsx", cfg.out)).parent
                                                             if ctx.paths.get("jsx") else cfg.out, raw_name))
    main_w, main_h = ctx.main_size or (int(comp_wh[0]), int(comp_wh[1]))
    small = (360, max(2, int(round(360 * main_h / main_w / 2)) * 2))
    checks["s9_6_ae_render"] = _run_check("s9_6_ae_render", lambda: check_ae_render(
        ctx.env, ctx.paths.get("aep"), ctx.paths.get("preview"), n_main, main_fps, small,
        Path(cfg.work) / "aerender", cfg))
    checks["s9_7_determinism"] = _run_check("s9_7_determinism", lambda: check_determinism(ctx))
    checks["s9_8_deliverables"] = _run_check("s9_8_deliverables",
                                             lambda: check_deliverables(ctx, n_cuts=len(cut_frames(segs))))

    exact_note = "" if main_fps == comp_fps else (
        f" (MAIN at {fps_str(main_fps)}: cuts rounded to the MAIN grid and source frames checked between the "
        "bracketing competitor frames; frame-exact only with --fps competitor)")
    c2 = {"status": aggregate([extra["cuts"]["status"], checks["s9_4_cut_images"]["status"]]),
          "summary": extra["cuts"].get("summary", "") + exact_note, "details": extra["cuts"]}
    c3 = {"status": aggregate([checks["s9_2_ae_sim"]["status"], checks["s9_3_visual"]["status"]]),
          "summary": f"AE sim: {p2.get('summary')}; visual: {checks['s9_3_visual'].get('summary')}" + exact_note,
          "details": {"s9_2": checks["s9_2_ae_sim"], "s9_3": {k: v for k, v in checks["s9_3_visual"].items()}}}
    mock_only = checks["s9_6_ae_render"]["status"] == "not_available"
    c6_status = aggregate([extra["mock"]["status"], checks["s9_6_ae_render"]["status"]])
    c6 = {"status": c6_status,
          "summary": extra["mock"].get("summary", "") + (" (mock only: After Effects not installed)" if mock_only
                                                          else f"; aerender: {checks['s9_6_ae_render'].get('summary')}"),
          "details": {"mock": extra["mock"], "s9_6": checks["s9_6_ae_render"], "mock_only": mock_only}}
    criteria = {
        "c1_coverage": {"status": checks["s9_1_coverage"]["status"], "summary": checks["s9_1_coverage"].get("summary", ""),
                        "details": checks["s9_1_coverage"]},
        "c2_cuts": c2,
        "c3_source_frames": c3,
        "c4_speed_framing": {"status": extra["framing"]["status"], "summary": extra["framing"].get("summary", ""),
                             "details": extra["framing"]},
        "c5_audio": {"status": checks["s9_5_audio"]["status"], "summary": checks["s9_5_audio"].get("summary", ""),
                     "details": checks["s9_5_audio"]},
        "c6_after_effects": c6,
    }
    failures: list[str] = []
    for name, c in criteria.items():
        for f in _collect_failures(c.get("details")):
            failures.append(f"{name}: {f}")
    for name in ("s9_7_determinism", "s9_8_deliverables"):
        for f in checks[name].get("failures", []):
            failures.append(f"{name}: {f}")
    seen = set()
    failures = [f for f in failures if not (f in seen or seen.add(f))]
    return {"criteria": criteria, "checks": checks, "failures": failures,
            "main_fps": fps_str(main_fps), "frames": n, "main_frames": n_main}


def _collect_failures(d: Any) -> list[str]:
    out: list[str] = []
    if isinstance(d, dict):
        out.extend(str(f) for f in d.get("failures", []) or [])
        for k, v in d.items():
            if k != "failures" and isinstance(v, dict) and ("status" in v or "failures" in v):
                out.extend(_collect_failures(v))
    return out
