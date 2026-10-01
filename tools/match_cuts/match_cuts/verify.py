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
and with the cutlist) + s9_2b (temporal signature: the recreation's frame-to-frame changes against the
competitor's comp-only repeat / move labels, temporal.py) + s9_2c (+-1 RAW frame refit) + s9_3 (visual, + a
probe of the delivered preview); c4 <- speed / framing / flip / rotation (framing and flip measured
independently on sampled frames, with a global start); c5 <- s9_5; c6 <- mock-run checks + s9_6 (aerender,
when installed). s9_7 (determinism) and s9_8 (deliverables) count like the criteria (DESIGN §7 D4/D5).

Hypothesis-neutral (DESIGN §5 verify): verification never imports a decision function of segment.py or
refine.py (only the shared ECC primitive refine.refine_transform and the scorers), re-measures framing
instead of trusting the model's held keys, and masks only what the layout stage found (captions / text
overlays) -- never refine's pass-2 residual masks, which are computed from the match being judged.

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
CHECKS = ("s9_1_coverage", "s9_2_ae_sim", "s9_2b_temporal", "s9_2c_refit", "s9_3_visual", "s9_4_cut_images",
           "s9_5_audio", "s9_6_ae_render", "s9_7_determinism", "s9_8_deliverables")
STATUSES = ("pass", "pass_with_exceptions", "fail", "not_available")
AUDIO_EXCEPTION_CODES = frozenset({"too_short", "not_in_raw", "audio_replaced", "pitch_preserved",
                                   "music_dominated", "no_audio", "av_offset"})
AUDIO_RUN_EXCEPTION_CODES = frozenset({"av_offset"})   # run-level only (DESIGN §7 D9), never a segment's own code
AUDIO_UNCERTAIN_CODE = "uncertain"   # an 'uncertain' segment (FX-08) makes no audio claim: listed, never an exception
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
        if s.type == "uncertain" and not (s.label or "").strip():
            failures.append(f"{_seg_name(s)}: UNCERTAIN segment without a label")
        if s.type not in ("raw", "not_in_raw", "dip", "flash", "uncertain"):
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
    # full-screen periods (DESIGN §7 D1) are reproduced: every RAW segment in them carries its own box. A
    # boxless segment's frames there are explained only by its declared transition overlap (crossfade / dip) with
    # a neighbour carrying the box (the detected period boundary falls mid-transition) or as a merged 1-2 frame
    # sliver at the period boundary (the detected boundary is off by a frame or two); its other frames fail
    # (review R2-5 / D1-c1)
    fullscreen: list[list[int]] = []
    fs_explained: list[dict] = []
    for p in all_periods:
        if str(p.get("mode")) != "fullscreen":
            continue
        a, b = int(p["comp_in"]), int(p["comp_out"])
        fullscreen.append([a, b - 1])
        boxed = [s for s in segs if s.type == "raw" and s.comp_in < b and s.comp_out > a and not s.box]
        for s in boxed:
            part = boxless_fullscreen_frames(s, segs, a, b)
            for why, text in (("transition", "lie in its declared transition overlap with {nb}, which carries the "
                                              "full-screen box (the detected period boundary falls inside the transition)"),
                              ("sliver", "are a merged sliver at the full-screen period boundary (the detected "
                                         "boundary is off by a frame or two)")):
                fr = part[why]
                if not fr:
                    continue
                nb = ", ".join(_seg_name(x) for x in segs if x.id in part["neighbours"]) or "a neighbour"
                rng = ", ".join(f"{x}-{y}" for x, y in _ranges(fr))
                exceptions.append(f"{_seg_name(s)}: frames {rng} of the full-screen period {a}-{b - 1} "
                                  + text.format(nb=nb))
                fs_explained.append({"segment": s.id, "frames": _ranges(fr), "why": why, "period": [a, b - 1]})
            if part["unexplained"]:
                rng = ", ".join(f"{x}-{y}" for x, y in _ranges(part["unexplained"]))
                failures.append(f"{_seg_name(s)}: frames {rng} show the video full-screen in the competitor but the "
                                "segment has no box (rebuilt inside the dominant video box)")
    status = _status_from(len(failures), len(exceptions))
    n_ph = sum(1 for s in segs if s.type == "not_in_raw")
    summary = (f"{len(segs)} segments ({n_ph} NOT-IN-RAW), {covered}/{n} frames covered, "
               f"{len(gaps)} gaps, {len(overlaps)} overlaps ({len(explained)} transitions)")
    if fullscreen:
        summary += f", {len(fullscreen)} full-screen period(s)"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions,
            "frames": n, "covered": covered, "gaps": gaps, "overlaps": overlaps, "transitions": explained,
            "extra_region_frames": region_frames, "fullscreen_frames": fullscreen,
            "fullscreen_explained": fs_explained}


FULLSCREEN_SLIVER_MAX = 2      # merge_tiny joins at most 2-frame slivers across a layout period boundary


def boxless_fullscreen_frames(seg: Segment, segments: Sequence[Segment], a: int, b: int) -> dict:
    """Classify the frames of a boxless RAW segment ``seg`` that lie in the full-screen period [a, b):

    * 'transition': inside a declared transition overlap (crossfade or dip: the overlap length equals the
      declared duration on either side) with a neighbour that carries a box -- a RAW segment (the full-screen
      shot dissolving in or out while the period detection switched mid-dissolve; segment.py keeps such a
      straddling segment boxed by the majority rule) or a dip segment (the whole canvas fading to / from the
      dip colour);
    * 'sliver': the remaining frames form one run of <= FULLSCREEN_SLIVER_MAX frames at the period boundary
      while most of the segment lies outside the period (segment.merge_tiny's merged sliver);
    * 'unexplained': everything else (the segment should have carried the full-screen box).

    Returns {'transition', 'sliver', 'unexplained': sorted frame lists, 'neighbours': [segment ids]}. The same
    rule serves pipeline.layout_period_warnings / report._warnings (DESIGN §7 D1)."""
    lo, hi = max(int(a), int(seg.comp_in)), min(int(b), int(seg.comp_out))
    frames = set(range(lo, hi))
    trans: set[int] = set()
    nbs: list[int] = []
    for o in segments:
        if o is seg or not getattr(o, "box", None) or o.type not in ("raw", "dip"):
            continue
        ov0, ov1 = max(int(seg.comp_in), int(o.comp_in)), min(int(seg.comp_out), int(o.comp_out))
        if ov1 <= ov0:
            continue
        first, second = (o, seg) if int(o.comp_in) < int(seg.comp_in) else (seg, o)
        if int(second.comp_in) != ov0 or int(first.comp_out) != ov1:
            continue
        ts = (_transition_dict(first.transition_out), _transition_dict(second.transition_in))
        if not any(t and int(t.get("duration_frames", -1) or -1) == ov1 - ov0 for t in ts):
            continue
        got = frames & set(range(ov0, ov1))
        if got:
            trans |= got
            nbs.append(int(o.id))
    rest = sorted(frames - trans)
    sliver: list[int] = []
    if rest:
        runs = _ranges(rest)
        n_in = hi - lo
        n_out = (int(seg.comp_out) - int(seg.comp_in)) - n_in
        if len(runs) == 1 and len(rest) <= FULLSCREEN_SLIVER_MAX and n_out > n_in:
            r0, r1 = runs[0]
            at_start = r0 == a and int(seg.comp_in) < a          # the segment runs into the period ...
            at_end = r1 == b - 1 and int(seg.comp_out) > b       # ... or out of it
            if at_start or at_end:
                sliver, rest = rest, []
    return {"transition": sorted(trans), "sliver": sliver, "unexplained": rest, "neighbours": sorted(set(nbs))}


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


def seg_raw_position(seg: Segment, k: int, comp_fps: Fraction, raw_fps: Fraction) -> float | None:
    """Continuous RAW position (frames, raw_fps * source time) of the segment's time model at comp frame k."""
    if seg.type != "raw":
        return None
    if seg.time_remap_keys:
        from .pipeline import remap_raw_seconds
        t = remap_raw_seconds(seg.time_remap_keys, k)
        return None if t is None else float(t) * float(raw_fps)
    if seg.raw_in_seconds is None:
        return None
    return float(raw_fps) * (float(seg.raw_in_seconds) + float(seg.speed) * (int(k) - int(seg.comp_in)) / float(comp_fps))


def seg_shown(seg: Segment, k: int, comp_fps: Fraction, raw_fps: Fraction,
              n_raw: int | None = None) -> tuple[int, float] | None:
    """(RAW frame j, weight f of RAW j + 1) the segment shows at comp frame k: f = 0 except on a verified frame-blend
    path, whose AE Frame Mix shows (1 - f) RAW j + f RAW j + 1 at the fraction of its position (FX-08)."""
    j = seg_raw_frame(seg, k, comp_fps, raw_fps, n_raw)
    if j is None:
        return None
    if not seg.frame_mix or (n_raw is not None and j + 1 >= n_raw):
        return j, 0.0
    p = seg_raw_position(seg, k, comp_fps, raw_fps)
    f = 0.0 if p is None else float(min(1.0, max(0.0, p - j)))
    return j, (f if f > PLAN_FRAME_TOL else 0.0)


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

    def score_with_mix(self, k: int, a: Cand, b: Cand, f: float,
                       cands: Sequence[Cand | None] = ()) -> tuple[float, np.ndarray]:
        """(score of the FIXED mix (1 - f) A + f B -- AE's Frame Mix of a frame-blend path, FX-08 --, scores of
        ``cands``) on comp frame k, all on one common valid mask (directly comparable)."""
        from . import scoring
        out = np.full(len(cands), np.nan)
        region = self._region(k)
        if region is None:
            return float("nan"), out
        wa, wb = self._warp(a, region.roi), self._warp(b, region.roi)
        warped = [self._warp(c, region.roi) for c in cands]
        if wa is None or wb is None:
            return float("nan"), out
        valid = region.mask & wa[1] & wb[1]
        for w in warped:
            if w is not None:
                valid &= w[1]
        if int(valid.sum()) < self.min_pixels:
            return float("nan"), out
        def zz(img: np.ndarray) -> float:
            s = scoring.zncc(region.img, img, valid)
            if self.grad_weight > 0:
                g = region.grad if region.grad is not None else scoring._gradmag(region.img)
                s = (1 - self.grad_weight) * s + self.grad_weight * scoring.zncc(g, scoring._gradmag(img), valid)
            return float(s)
        z = zz((1.0 - float(f)) * wa[0] + float(f) * wb[0])
        for i, w in enumerate(warped):
            if w is not None:
                out[i] = zz(w[0])
        return z, out

    def blend(self, k: int, a: Cand | None, b: Cand | None) -> tuple[float, float]:
        """(alpha_B, zncc_of_fit) of comp[k] ~ g * ((1-alpha_B)*A + alpha_B*B) + c. alpha_B comes from the
        gain-independent fit (scoring.fit_blend_free: beta_B / (beta_A + beta_B)) so a contrast change or
        a softness mismatch of the repost does not bias it (review R2-1); NaN when comp[k] is no blend of
        A and B (beta_A + beta_B <= scoring.BLEND_MIN_GAIN)."""
        from . import scoring
        region = self._region(k)
        if region is None:
            return float("nan"), float("nan")
        wa, wb = self._warp(a, region.roi), self._warp(b, region.roi)
        if wa is None or wb is None:
            return float("nan"), float("nan")
        valid = region.mask & wa[1] & wb[1]
        if int(valid.sum()) < self.min_pixels:
            return float("nan"), float("nan")
        alpha_a, _gain, z = scoring.fit_blend_free(region, wa[0], wb[0], valid)
        return 1.0 - alpha_a, z

    def uniform(self, k: int) -> tuple[float, float]:
        from . import scoring
        if not (0 <= k < self.comp.n):
            return float("nan"), float("nan")
        return scoring.region_stats(np.asarray(self.comp.get(k)), self.roi_at(k), self.allowed_fn(k))

    # -- free re-measurement of framing (hypothesis-neutral checks) ----------------------------------------
    def refit(self, k: int, cand: Cand | None, inits: Sequence[Sim] = ()) -> tuple[Sim, float] | None:
        """Framing of RAW frame cand[0] on comp frame k measured by ECC (refine.refine_transform, the
        shared ECC recipe) from cand[1] and each of ``inits``; the best (Sim, score) -- the start itself
        when no ECC run raised the score. None when the frame or the RAW frame is unavailable."""
        from . import refine
        if cand is None or cand[0] is None or cand[1] is None or not self.raw.has(int(cand[0])):
            return None
        if not (0 <= k < self.comp.n) or not self.comp.has(int(k)):
            return None
        j, sim0, flip = cand
        comp_img, raw_img = np.asarray(self.comp.get(int(k))), np.asarray(self.raw.get(int(j)))
        best: tuple[Sim, float] | None = None
        for init in [sim0, *[s for s in inits if s is not None]]:
            try:
                sim, z = refine.refine_transform(comp_img, raw_img, init, bool(flip), self.raw_w, self.raw.ratio,
                                                 self.comp.ratio, self.allowed_fn(int(k)), self.cfg, self.roi_at(int(k)))
            except Exception:  # noqa: BLE001 - an ECC failure is an unmeasured start, not a crash
                continue
            if math.isfinite(float(z)) and (best is None or float(z) > best[1]):
                best = (sim, float(z))
        return best

    def global_init(self, k: int, cand: Cand | None) -> Sim | None:
        """cand[1] shifted by the phase-correlation translation between the comp ROI and the RAW frame warped
        with cand[1] (a GLOBAL start for ECC, independent of how close the model is); None when unmeasurable."""
        from . import scoring
        region = self._region(int(k))
        w = self._warp(cand, None if region is None else region.roi)
        if region is None or w is None:
            return None
        m = region.mask & w[1]
        if int(m.sum()) < self.min_pixels:
            return None
        sx, sy = temporal_phase_shift(region.img, m, w[0], m)
        if sx == 0.0 and sy == 0.0:
            return None
        rx, ry = float(self.comp.ratio[0]), float(self.comp.ratio[1])
        sim = cand[1]
        # the RAW content at comp-proxy x + s matches the comp at x: move the picture by -s (comp full-res px)
        return Sim(sim.s, sim.theta_deg, sim.tx - sx / rx, sim.ty - sy / ry)

    def score_grad(self, k: int, cands: Sequence[Cand | None]) -> np.ndarray:
        """Gradient-domain ZNCC (scoring.grad_zncc) of comp frame k against each candidate, on the common
        valid mask -- the dark / low-texture companion of ``score``."""
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
            if w is not None:
                out[i] = scoring.grad_zncc(region.img, w[0], valid)
        return out


def temporal_phase_shift(a: np.ndarray, ma: np.ndarray, b: np.ndarray, mb: np.ndarray) -> tuple[float, float]:
    """Phase-correlation translation (sx, sy) with b(x + s) ~ a(x) (temporal._phase_shift)."""
    from . import temporal
    return temporal._phase_shift(np.asarray(a, np.float32), np.asarray(ma, bool), np.asarray(b, np.float32),
                                 np.asarray(mb, bool))


def derotated(sim: Sim, centre: Sequence[float]) -> Sim:
    """``sim`` with theta = 0 keeping the RAW point it shows at ``centre`` (comp px) in place -- the second
    ECC start of the +-1 refit (a compensating rotation must not survive just because ECC started there)."""
    c = np.asarray(centre, np.float64).reshape(1, 2)
    p = sim.inverse().apply(c)[0]
    return Sim(sim.s, 0.0, float(c[0, 0] - sim.s * p[0]), float(c[0, 1] - sim.s * p[1]))


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
    if a.type == "raw" and b.type == "uncertain":
        return "raw_to_uncertain"
    if a.type == "uncertain" and b.type == "raw":
        return "uncertain_to_raw"
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


def _can_refit(scorer: Any) -> bool:
    return callable(getattr(scorer, "refit", None))


def _r6(v: float) -> float | None:
    return None if v is None or not math.isfinite(float(v)) else round(float(v), 6)


def _nanmax(vals: Iterable[float]) -> float:
    v = [float(x) for x in vals if x is not None and math.isfinite(float(x))]
    return max(v) if v else float("nan")


def _timed_scores(scorer: Any, k: int, own: Cand, other: Cand) -> dict:
    """Both TIME hypotheses of comp frame k with their framing re-measured by ECC (hypothesis-neutral c2):
    own = max(own model, own frame refitted from it); other = max(other model held at its boundary key, other
    frame refitted from the other model AND from the own model's framing). All scored on one common mask."""
    r_own = scorer.refit(k, own)
    r_oth = scorer.refit(k, other, [own[1]] if bool(own[2]) == bool(other[2]) else [])
    cands = [own, other]
    i_own = i_oth = None
    if r_own is not None:
        i_own = len(cands)
        cands.append((own[0], r_own[0], own[2]))
    if r_oth is not None:
        i_oth = len(cands)
        cands.append((other[0], r_oth[0], other[2]))
    s = scorer.score(k, cands)
    pick = lambda i: float(s[i]) if i is not None and i < len(s) else float("nan")  # noqa: E731
    return {"s_own_model": pick(0), "s_other_held": pick(1), "s_own_refit": pick(i_own), "s_other_refit": pick(i_oth),
            "s_own": _nanmax([pick(0), pick(i_own)]), "s_other": _nanmax([pick(1), pick(i_oth)])}


def _side(scorer: Any, k: int, own: Cand | None, other: Cand | None, refit: bool = False) -> dict:
    """Does comp frame k match its own segment model better than the other one? With ``refit`` (and a scorer
    that can re-measure framing) the two models' RAW frames are compared with their framing re-measured on
    THIS frame (``_timed_scores``), not with the neighbour's key held at its boundary (in a pan the held
    framing lags v px per frame and always loses)."""
    res: dict[str, Any] = {"k": int(k), "own": None if own is None else int(own[0]),
                           "other": None if other is None else int(other[0])}
    if own is None:
        res.update(result="unscorable", reason="own model predicts no RAW frame")
        return res
    if other is not None and own[0] == other[0] and own[2] == other[2] and _sims_close(own[1], other[1]):
        res.update(result="indistinguishable", reason="both models predict the same RAW frame and framing")
        return res
    timed = (refit and other is not None and (own[0] != other[0] or bool(own[2]) != bool(other[2]))
             and _can_refit(scorer))
    if timed:
        t = _timed_scores(scorer, k, own, other)
        s_own, s_other = t["s_own"], t["s_other"]
        res.update({key: _r6(v) for key, v in t.items()}, framing="re-measured")
    else:
        s = scorer.score(k, [own, other])
        s_own, s_other = float(s[0]), float(s[1]) if len(s) > 1 else float("nan")
        res.update(s_own=_r6(s_own), s_other=_r6(s_other))
    if math.isnan(s_own):
        res.update(result="unscorable", reason="too few scorable pixels")
        return res
    if other is None or math.isnan(s_other):
        s_other = -math.inf
    res["result"] = "ok" if s_own > s_other else "fail"
    return res


def _cut_noise(scorer: Any, models: "_Models", a: Segment, b: Segment, cfg: Any, n: int = 10) -> float:
    """Score noise delta of the two segments next to a cut: scoring.noise_delta of the shown models' scores on
    up to ``n`` frames per side (DESIGN §3 delta: 3 x robust std, clipped to [soft_delta_min, soft_delta_max])."""
    from . import scoring
    vals = []
    for seg, ks in ((a, range(max(a.comp_in, a.comp_out - n), a.comp_out)), (b, range(b.comp_in, min(b.comp_out, b.comp_in + n)))):
        for k in ks:
            c = models.cand(seg, k)
            if c is not None:
                vals.append(float(scorer.score(k, [c])[0]))
    return scoring.noise_delta(vals, float(getattr(cfg, "soft_delta_min", 0.001)), float(getattr(cfg, "soft_delta_max", 0.01)))


def _extrapolate(s0: Sim, s1: Sim, steps: float = 1.0) -> Sim:
    """Linear extrapolation of the AE-linear parameters (scale, rotation, position) ``steps`` frames past s1."""
    return Sim(s1.s + steps * (s1.s - s0.s), s1.theta_deg + steps * (s1.theta_deg - s0.theta_deg),
               s1.tx + steps * (s1.tx - s0.tx), s1.ty + steps * (s1.ty - s0.ty))


def _framing_continuity(scorer: Any, models: "_Models", a: Segment, b: Segment, centre: Sequence[float],
                        cfg: Any) -> dict:
    """Re-measured framing across a cut whose two sides show the same RAW frames: ECC framing of the shown RAW
    frame on ka-1, ka (A) and kb, kb+1 (B); each side's linear extrapolation must miss the other side's
    measured framing by a framing STEP (punch_scale_step / punch_pos_step / rotation) for the cut to be real."""
    ka, kb = a.comp_out - 1, b.comp_in
    ks = [k for k in (ka - 1, ka) if k >= a.comp_in] + [k for k in (kb, kb + 1) if k < b.comp_out]
    f: dict[int, Sim] = {}
    for k in ks:
        own, oth = (a, b) if k < kb else (b, a)
        c, o = models.cand(own, k), models.cand(oth, k)
        if c is None:
            continue
        r = scorer.refit(k, c, [o[1]] if o is not None and bool(o[2]) == bool(c[2]) else [])
        if r is not None:
            f[k] = r[0]
    if ka not in f or kb not in f:
        return {"result": "unscorable", "reason": "framing not measurable at the cut"}
    c = np.asarray(centre, np.float64).reshape(1, 2)
    pred_b = _extrapolate(f[ka - 1], f[ka], kb - ka) if ka - 1 in f else f[ka]
    pred_a = _extrapolate(f[kb + 1], f[kb], kb - ka) if kb + 1 in f else f[kb]
    e1, e2 = _sim_errors(pred_b, f[kb], c), _sim_errors(pred_a, f[ka], c)
    st, pt = float(getattr(cfg, "punch_scale_step", 0.01)), float(getattr(cfg, "punch_pos_step", 4.0))
    rt = max(float(getattr(cfg, "rotation_min_deg", 0.2)), 0.2) + 0.05
    step = any(e[0] > st or e[1] > pt or e[2] > rt for e in (e1, e2))
    ev = {"scale_err": [round(e1[0], 5), round(e2[0], 5)], "pos_err_px": [round(e1[1], 3), round(e2[1], 3)],
          "rot_err_deg": [round(e1[2], 4), round(e2[2], 4)], "frames": sorted(f)}
    return {"result": "step" if step else "continuous", **ev}


def _union_test(scorer: Any, models: "_Models", a: Segment, b: Segment, n_side: int, delta: float) -> dict:
    """No-cut alternative of a hard cut A|B: A's time line extended over B's first ``n_side`` frames, and B's
    extended back over A's last ones, each with its framing re-measured (ECC from the line's own model and
    from the shown model). A line that scores within ``delta`` of the split (shown model or its refit) on ALL
    tested frames explains both sides: the cut is spurious. Frames where line and split show the same RAW
    frame are 'same_raw' (time cannot decide there)."""
    out: dict[str, Any] = {"delta": round(float(delta), 6)}
    fa = [k for k in range(max(a.comp_in, a.comp_out - n_side), a.comp_out)]
    fb = [k for k in range(b.comp_in, min(b.comp_out, b.comp_in + n_side))]
    for name, line, frames, split in (("A_line", a, fb, b), ("B_line", b, fa, a)):
        rows = []
        for k in frames:
            sp, un = models.cand(split, k), models.cand(line, k)
            if sp is None or un is None:
                continue
            row: dict[str, Any] = {"k": int(k), "split": int(sp[0]), "line": int(un[0])}
            if int(sp[0]) == int(un[0]) and bool(sp[2]) == bool(un[2]):
                row["same_raw"] = True
                rows.append(row)
                continue
            t = _timed_scores(scorer, k, sp, un)
            row.update(z_split=_r6(t["s_own"]), z_line=_r6(t["s_other"]))
            rows.append(row)
        timed = [r for r in rows if not r.get("same_raw")]
        explained = bool(timed) and all(r.get("z_split") is not None and r.get("z_line") is not None
                                        and r["z_line"] >= r["z_split"] - delta for r in timed) \
            and all(r.get("same_raw") or r.get("z_line") is not None for r in rows)
        out[name] = {"rows": rows, "explains_both_sides": explained,
                     "all_same_raw": bool(rows) and all(r.get("same_raw") for r in rows)}
    return out


def _excursion(models: "_Models", a: Segment, b: Segment, c: Segment, n_side: int, off: int) -> dict | None:
    """A 1-2 frame segment B between A and C whose RAW frames lie more than ``off`` RAW frames off the line A
    and C share (A's line extended over C's first frames within +-1 of C's own): {'line_frames', 'offsets'}."""
    if not (a.type == b.type == c.type == "raw") or b.comp_out - b.comp_in > 2 or bool(a.flip_h) != bool(c.flip_h):
        return None
    for k in range(c.comp_in, min(c.comp_out, c.comp_in + n_side)):
        ja, jc = models.cand(a, k), models.cand(c, k)
        if ja is None or jc is None or abs(int(ja[0]) - int(jc[0])) > 1:
            return None
    offs = []
    for k in range(b.comp_in, b.comp_out):
        ja, jb = models.cand(a, k), models.cand(b, k)
        if ja is None or jb is None:
            return None
        offs.append(int(jb[0]) - int(ja[0]))
    if not offs or not all(abs(o) > off for o in offs):
        return None
    return {"frames": list(range(b.comp_in, b.comp_out)), "offsets": offs}


def _placeholder_hypotheses(scorer: Any, models: "_Models", k: int, neighbours: Sequence[Segment]) -> list[dict]:
    """Scores of placeholder frame k under every hypothesis of its RAW neighbours (FX-08 c2): each neighbour's time
    line extended to k (model frame + framing) and its boundary RAW frame held with its boundary framing (a freeze),
    each the max of the model framing and -- when the scorer can re-measure -- the framing refitted on k."""
    out: list[dict] = []
    for n in neighbours:
        kb = n.comp_in if n.comp_in > k else n.comp_out - 1
        cands = [("line", models.cand(n, k))]
        cb = models.cand(n, kb)
        if cb is not None:
            cands.append(("hold", cb))
        for name, c in cands:
            if c is None:
                continue
            vals = [float(scorer.score(k, [c])[0])]
            if _can_refit(scorer):
                r = scorer.refit(k, c)
                if r is not None:
                    vals.append(float(scorer.score(k, [(c[0], r[0], c[2])])[0]))
            s = _nanmax(vals)
            out.append({"neighbour": n.id, "hypothesis": name, "raw": int(c[0]),
                        "s": None if math.isnan(s) else round(s, 6)})
    return out


def fit_crossfade_window(rows: Sequence[tuple[int, float]], pure: float | None = None) -> tuple[int, int] | None:
    """(O, D) of a linear crossfade alpha_B(k) = (k - O) / D fitted to measured (k, alpha_B) pairs the way
    segment.py finds crossfades: least squares over the ramp frames (0.02 < alpha < 0.98), O = round(zero
    crossing), D = round(1 / slope); one ramp frame: D = 2 around alpha 0.5, else D from its alpha. None
    when nothing ramps. ``pure`` (the purity tolerance of the pure-A / pure-B checks) also drops frames
    within ``pure`` of 0 or 1 before the fit: those are the pure frames around the dissolve, whose small
    residual alpha (grading, softness) would otherwise tilt the fitted ramp (review R2-1)."""
    edge = max(0.02, float(pure)) if pure is not None and math.isfinite(float(pure)) else 0.02
    ramp = [(int(k), float(a)) for k, a in rows if a is not None and math.isfinite(a) and edge < a < 1.0 - edge]
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
               n_raw: int | None, scorer: Any, cfg: Any, labels: Any = None,
               box_centre: Sequence[float] | None = None) -> dict:
    """Criterion 2, independent of segment.py: at every cut A|B, comp frame comp_out(A)-1 scores higher
    against A's model (phase_solve.ae_frame + A's transform) than against B's model extended back, and
    comp frame comp_in(B) the reverse. A hard cut where both models show the same RAW frame, flip and
    framing on both sides (no discontinuity in m(k)) is a spurious cut and fails, unless it is a
    speed-only cut (cut_ambiguity) or a layout change. Crossfades: the fitted alpha ramp must follow the
    declared one, the window (O, D) re-fitted from the alpha measured over O-3 .. O+D+2 must equal the
    declared one (off by one frame = fail), frame O (and O-1) must be pure A and O+D pure B. NOT-IN-RAW
    neighbours (FX-08, the same rule refine applies: NOT-IN-RAW only when EVERY hypothesis is below none_thresh):
    the placeholder's boundary frame scores below none_thresh against every hypothesis its neighbours offer --
    each RAW neighbour's time line extended AND its boundary RAW frame held (a freeze), each with its framing
    re-measured when the scorer can, for the adjacent neighbour and the one across the placeholder. An UNCERTAIN
    neighbour (no claim) is not compared; its RAW neighbour's boundary frame must still match its own model.
    Dips/flashes: the uniform side is uniform.

    Hypothesis-neutral additions (a scorer with ``refit``, i.e. ECC re-measurement of framing):

    * the two models' RAW frames are compared with their framing RE-MEASURED on the boundary frame (never
      the neighbour's key held at its boundary: in a pan that lags v px per frame and always loses);
    * no-cut alternative (``_union_test``): A's time line extended over B's first ``verify_union_frames``
      frames, and B's back over A's last ones, with re-measured framing; a line within the score noise of the
      split on all of them explains both sides -> 'spurious cut'. When both lines show the same RAW frames,
      the re-measured framing decides (``_framing_continuity``: no framing step -> 'spurious cut');
    * excursion: a 1-2 frame segment more than ``verify_excursion_frames`` RAW frames off the line its two
      neighbours share must beat that line on its own frames by more than the noise, else 'suspected
      misidentification'.

    ``labels`` (temporal.Labels or {k: label} of the competitor pairs (k, k+1)): a hard cut between the two
    frames of a competitor REPEAT pair (the same image) fails. ``box_centre`` (comp px) enables the framing
    continuity test."""
    from .temporal import REPEAT
    models = _Models(comp_fps, raw_fps, raw_wh, n_raw)
    none_thresh = float(getattr(cfg, "none_thresh", 0.6))
    uniform_std = float(getattr(cfg, "uniform_std", 4.0))
    alpha_tol = float(getattr(cfg, "verify_alpha_tol", 0.15))
    n_side = max(1, int(getattr(cfg, "verify_union_frames", 2) or 2))
    ex_off = int(getattr(cfg, "verify_excursion_frames", 3) or 3)
    refit = _can_refit(scorer)
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    cuts: list[dict] = []
    failures: list[str] = []
    exceptions: list[str] = []
    for i, (a, b) in enumerate(zip(segs[:-1], segs[1:])):
        kind = _pair_kind(a, b)
        c: dict[str, Any] = {"from": a.id, "to": b.id, "frame": int(b.comp_in), "kind": kind,
                             "tc": timecode(int(b.comp_in), comp_fps)}
        sides: list[dict] = []
        notes: list[str] = []
        if kind == "hard":
            ka, kb = a.comp_out - 1, b.comp_in
            sides.append({"side": "A_last", **_side(scorer, ka, models.cand(a, ka), models.cand(b, ka), refit)})
            sides.append({"side": "B_first", **_side(scorer, kb, models.cand(b, kb), models.cand(a, kb), refit)})
            exempt = bool(a.cut_ambiguity or b.cut_ambiguity) or _layout_change(a, b)
            if all(sd.get("result") == "indistinguishable" for sd in sides) and not exempt:
                for sd in sides:
                    sd.update(result="fail", reason="no discontinuity in m(k): spurious cut (both segment models "
                                                    "show the same RAW frame, flip and framing on both sides)")
            elif refit and not exempt:
                delta = _cut_noise(scorer, models, a, b, cfg)
                u = _union_test(scorer, models, a, b, n_side, delta)
                c["union"] = u
                explains = [nm for nm, sg in (("A_line", a), ("B_line", b)) if u[nm]["explains_both_sides"]]
                if explains:
                    who = " and ".join(_seg_name(a if nm == "A_line" else b) for nm in explains)
                    sides.append({"side": "no_cut", "result": "fail",
                                  "reason": f"spurious cut: {who}'s time line extended over the other side explains "
                                            f"both sides within the score noise (delta {delta:.4f}, framing "
                                            "re-measured)"})
                elif u["A_line"]["all_same_raw"] and u["B_line"]["all_same_raw"] and box_centre is not None:
                    fc = _framing_continuity(scorer, models, a, b, box_centre, cfg)
                    c["framing_continuity"] = fc
                    if fc["result"] == "continuous":
                        sides.append({"side": "no_cut", "result": "fail",
                                      "reason": "spurious cut: both sides show the same RAW frames and the "
                                                f"re-measured framing is continuous (extrapolation errors "
                                                f"{fc['pos_err_px']} px, {fc['scale_err']} scale)"})
                    elif fc["result"] == "unscorable":
                        sides.append({"side": "no_cut", "result": "unscorable", "reason": fc.get("reason")})
            if labels is not None and not exempt and labels.get(ka) == REPEAT:
                sides.append({"side": "repeat_pair", "k": int(ka), "result": "fail",
                              "reason": f"cut between the two frames of a competitor repeat pair ({ka}, {kb} show "
                                        "the same image up to a similarity transform)"})
            nxt = segs[i + 2] if i + 2 < len(segs) else None
            if (nxt is not None and _pair_kind(b, nxt) == "hard" and not _layout_change(a, b)
                    and not _layout_change(b, nxt) and not (b.cut_ambiguity or nxt.cut_ambiguity)):
                ex = _excursion(models, a, b, nxt, n_side, ex_off)
                if ex is not None:
                    c["excursion"] = ex
                    if not refit:
                        sides.append({"side": "excursion", "result": "unscorable",
                                      "reason": f"{_seg_name(b)} lies {ex['offsets']} RAW frames off the line of "
                                                f"{_seg_name(a)}/{_seg_name(nxt)}: not verifiable without re-measured framing"})
                    else:
                        delta = c["union"]["delta"] if "union" in c else _cut_noise(scorer, models, a, b, cfg)
                        rows = []
                        for k in ex["frames"]:
                            t = _timed_scores(scorer, k, models.cand(b, k), models.cand(a, k))
                            rows.append({"k": int(k), "z_own": _r6(t["s_own"]), "z_line": _r6(t["s_other"])})
                        ex["rows"] = rows
                        verified = any(r["z_own"] is not None and (r["z_line"] is None or r["z_own"] > r["z_line"] + delta)
                                       for r in rows)
                        ex["verified"] = bool(verified)
                        if verified:
                            notes.append(f"{_seg_name(b)} is a verified flash cut off the line of its neighbours")
                        else:
                            sides.append({"side": "excursion", "result": "fail",
                                          "reason": f"suspected misidentification: {_seg_name(b)} lies {ex['offsets']} "
                                                    f"RAW frames off the line {_seg_name(a)} and {_seg_name(nxt)} share, "
                                                    f"which explains its frames as well (delta {float(delta):.4f})"})
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
            # the window itself: re-fit (O, D) from the measured ramp (time-math F4); frames within the
            # purity tolerance of 0 / 1 are the pure frames around the ramp, judged below (review R2-1)
            pure = max(0.5 / d, 0.05) if d > 0 else 0.05
            fit = fit_crossfade_window(sorted(fit_rows.items()), pure=pure)
            c["window_fit"] = None if fit is None else {"O": fit[0], "D": fit[1]}
            if d > 0 and fit is None:
                sides.append({"side": "window", "result": "unscorable", "reason": "no ramp frames to fit (O, D)"})
            elif d > 0:
                ok = fit == (o, d)
                sides.append({"side": "window", "result": "ok" if ok else "fail",
                              "reason": f"measured ramp gives O={fit[0]} D={fit[1]}, declared O={o} D={d}"})
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
            sides.append({"side": "raw_frame", "k": int(k_r), "s_own": None if math.isnan(s_r) else round(s_r, 6),
                          "result": "unscorable" if math.isnan(s_r) else ("ok" if s_r >= none_thresh else "fail")})
            # every hypothesis the neighbours offer (FX-08): the adjacent RAW neighbour and the one across the
            # placeholder, each by its extended time line and by its boundary frame held
            far = segs[i + 2] if kind == "raw_to_placeholder" and i + 2 < len(segs) else \
                (segs[i - 1] if kind == "placeholder_to_raw" and i >= 1 else None)
            hyp = _placeholder_hypotheses(scorer, models, k_p, [r] + ([far] if far is not None and far.type == "raw"
                                                                       else []))
            vals = [h["s"] for h in hyp if h.get("s") is not None]
            s_p = max(vals) if vals else float("nan")
            sides.append({"side": "placeholder_frame", "k": int(k_p), "s_ext": None if math.isnan(s_p) else round(s_p, 6),
                          "hypotheses": hyp,
                          "result": "ok" if (math.isnan(s_p) or s_p < none_thresh) else "fail",
                          "reason": f"placeholder vs every neighbour hypothesis (time line extended, boundary frame "
                                    f"held) must be < none_thresh {none_thresh}"})
        elif kind in ("raw_to_uncertain", "uncertain_to_raw"):
            r = a if kind == "raw_to_uncertain" else b
            k_r = r.comp_out - 1 if kind == "raw_to_uncertain" else r.comp_in
            s_r = float(scorer.score(k_r, [models.cand(r, k_r)])[0])
            sides.append({"side": "raw_frame", "k": int(k_r), "s_own": None if math.isnan(s_r) else round(s_r, 6),
                          "result": "unscorable" if math.isnan(s_r) else ("ok" if s_r >= none_thresh else "fail")})
            sides.append({"side": "uncertain", "result": "n/a",
                          "reason": "an UNCERTAIN segment claims no RAW frame (counted under criterion 3)"})
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
# s9_2b temporal signature and s9_2c +-1 refit (criterion 3, hypothesis-neutral)
# ---------------------------------------------------------------------------------------------

def single_raw_segments(segments: Sequence[Segment], n: int) -> dict[int, Segment]:
    """Competitor frame -> the ONE raw segment covering it (frames inside a transition overlap or covered by a
    placeholder / dip / flash are absent)."""
    cover: dict[int, list[Segment]] = {}
    for s in segments:
        for k in range(max(0, int(s.comp_in)), min(int(n), int(s.comp_out))):
            cover.setdefault(k, []).append(s)
    return {k: v[0] for k, v in cover.items() if len(v) == 1 and v[0].type == "raw"}


class TemporalFrames:
    """Prepared ROI images (temporal.prepare) of the competitor proxy (``comp``) and of the recreation
    (``rec``: each frame's RAW proxy frame warped with its segment's OWN model -- what AE shows), blurred
    with cfg.score_blur (proxy px), masked with the layout-only allowed masks of that frame."""

    def __init__(self, comp: Any, raw: Any, segments: Sequence[Segment], allowed_fn: Callable[[int], np.ndarray | None],
                 box_fn: Callable[[int], Box | dict | None] | None, box: Box | dict | None, raw_wh: tuple[float, float],
                 comp_fps: Fraction, raw_fps: Fraction, n_raw: int | None, cfg: Any,
                 mask_out: Callable[[int], np.ndarray | None] | None = None):
        self.comp, self.raw, self.cfg = comp, raw, cfg
        self.allowed_fn, self.box_fn, self.box = allowed_fn, box_fn, box
        self.raw_wh, self.comp_fps, self.raw_fps, self.n_raw = raw_wh, comp_fps, raw_fps, n_raw
        self.n = int(comp.n)
        self.seg_at = single_raw_segments(segments, self.n)
        self.blur = float(getattr(cfg, "score_blur", 1.0))
        self.max_side = int(getattr(cfg, "temporal_max_side", 200) or 0)
        self.mask_out = mask_out          # extra pixels removed from BOTH sequences (animated text overlays)

    def roi(self, k: int) -> tuple[int, int, int, int]:
        b = self.box_fn(int(k)) if self.box_fn is not None else self.box
        return proxy_roi(b, self.comp.size, self.comp.ratio)

    def same(self, k: int, k2: int) -> bool:
        return self.roi(k) == self.roi(k2)

    def _blur_at(self, roi: tuple[int, int, int, int]) -> float:
        """cfg.score_blur (proxy px) at the scale temporal.prepare measures the ROI at."""
        from . import temporal
        return self.blur * temporal.scale_of((roi[3], roi[2]), self.max_side)

    def _mask(self, k: int, roi: tuple[int, int, int, int]) -> np.ndarray:
        x, y, w, h = roi
        al = self.allowed_fn(int(k))
        m = np.ones((h, w), bool) if al is None else np.asarray(al)[y:y + h, x:x + w].astype(bool)
        ex = self.mask_out(int(k)) if self.mask_out is not None else None
        if ex is not None:
            m = m & ~np.asarray(ex, bool)[y:y + h, x:x + w]
        return m

    def comp_frame(self, k: int):
        from . import temporal
        if not (0 <= k < self.n) or not self.comp.has(int(k)):
            return None
        x, y, w, h = roi = self.roi(k)
        img = np.asarray(self.comp.get(int(k)))[y:y + h, x:x + w]
        return temporal.prepare(img, self._mask(k, roi), self.max_side, self._blur_at(roi))

    def rec_frame(self, k: int):
        from . import scoring, temporal
        s = self.seg_at.get(int(k))
        if s is None:
            return None
        sh = seg_shown(s, int(k), self.comp_fps, self.raw_fps, self.n_raw)
        sim = seg_sim(s, int(k), *self.raw_wh)
        if sh is None or sim is None or not self.raw.has(int(sh[0])):
            return None
        j, f = sh
        roi = self.roi(k)
        w, valid = scoring.warp_to_roi(np.asarray(self.raw.get(int(j))), sim, bool(s.flip_h), float(self.raw_wh[0]),
                                       self.raw.ratio, self.comp.ratio, roi)
        if f > 0.0 and self.raw.has(int(j) + 1):      # Frame Mix of a frame-blend path (FX-08)
            w1, v1 = scoring.warp_to_roi(np.asarray(self.raw.get(int(j) + 1)), sim, bool(s.flip_h),
                                         float(self.raw_wh[0]), self.raw.ratio, self.comp.ratio, roi)
            w, valid = (1.0 - f) * w + f * w1, valid & v1
        return temporal.prepare(w, valid & self._mask(k, roi), self.max_side, self._blur_at(roi))


# ---------------------------------------------------------------------------------------------
# RAW-only overlays (wave 4): burned-in graphics the RAW carries and the competitor does not show
# ---------------------------------------------------------------------------------------------
#
# A RAW upload may carry a legal disclaimer, a subtitle or a channel bug that the competitor's master did not have
# (the real run's shots from 605 on, film24's two-clip pan). The recreation (AE / preview) shows it, the competitor
# does not, and the visual check fails although every frame is exact. Such a region is measured, never assumed:
# per raw segment, on sampled matched frames, the competitor is warped back into RAW coordinates with the
# segment's own model and compared with the shown RAW frame (gain / offset fitted, score blur). Only a segment where
# BOTH play is examined: >= 3 distinct RAW frames shown and the competitor's picture changing over the segment
# (75th percentile of the per-pixel std >= verify_overlay_comp_var) -- a hold, a freeze or a static scene shown at a
# wrong, far-away RAW frame (which would differ only where the RAW once changed) is never explained this way.
# A RAW-only overlay is then a compact region where
#   1. the RAW is STATIC in RAW coordinates (per-pixel range over >= verify_overlay_raw_span distinct RAW frames
#      around the shown ones <= verify_overlay_static): a frame or time error only shows where the RAW CHANGES,
#      so no wrong frame can produce a residual confined to a static region;
#   2. the competitor differs from the recreation PERSISTENTLY (residual above max(verify_overlay_resid_min,
#      verify_overlay_resid_k robust sigmas) on >= verify_overlay_persist of the frames that see the pixel);
#   3. the RAW carries a GRAPHIC there that the competitor lacks: edge energy of the RAW >= verify_overlay_grad_ratio x
#      the competitor's (warped to RAW coordinates, averaged over the segment) -- a competitor-side element (a sliding
#      caption, a sticker) has its edges in the competitor and is never called RAW-only; a misframing has edges on
#      both sides;
#   4. it is small: all regions of a segment together cover <= verify_overlay_max_frac of the visible picture
#      (a misframing or a different take differs EVERYWHERE and is never a set of small static regions).
# Accepted regions (bounding boxes in RAW px) are masked -- mapped through each frame's own model -- in s9_3, s9_2b and
# s9_2c; s9_3 lists every frame that passes only with the mask as explained ('RAW-only overlay at x,y,w,h in RAW px
# over frames a-b: not shown by the competitor'), and the frame must still reach verify_zncc on everything else.

def _gradmag_f(img: np.ndarray) -> np.ndarray:
    import cv2
    a = np.asarray(img, np.float32)
    return cv2.magnitude(cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3))


def _sample_evenly(ks: Sequence[int], n: int) -> list[int]:
    if len(ks) <= n:
        return list(ks)
    idx = np.unique(np.rint(np.linspace(0, len(ks) - 1, int(n))).astype(int))
    return [ks[i] for i in idx]


def _raw_span(js: Sequence[int], span: int, has: Callable[[int], bool]) -> list[int]:
    """The distinct RAW frames ``js`` extended alternately below / above until at least ``span`` frames."""
    out = sorted(set(int(j) for j in js))
    lo, hi = (out[0], out[-1]) if out else (0, -1)
    step = 0
    while out and len(out) < int(span) and step < 4 * int(span):
        step += 1
        cand = lo - 1 if step % 2 else hi + 1
        if has(cand):
            out.append(cand)
            lo, hi = min(lo, cand), max(hi, cand)
        elif step % 2:
            lo -= 1
        else:
            hi += 1
    return sorted(set(out))


class RawOnlyOverlays:
    """Accepted RAW-only overlay regions per raw segment and their per-frame competitor-proxy masks (each frame's own
    model maps the RAW-coordinate mask; dilated like the layout overlays + the score blur's reach)."""

    def __init__(self, comp: Any, raw: Any, raw_wh: tuple[float, float], comp_fps: Fraction, raw_fps: Fraction,
                 dilate_px: int):
        self.comp, self.raw, self.raw_wh = comp, raw, raw_wh
        self.comp_fps, self.raw_fps = comp_fps, raw_fps
        self.dilate_px = int(dilate_px)
        self.by_seg: dict[int, tuple[Segment, np.ndarray]] = {}       # segment id -> (segment, RAW proxy bool mask)
        self.regions: list[dict] = []                                  # accepted, reported
        self.rejected: list[dict] = []
        self._memo: dict[int, np.ndarray | None] = {}
        self._frame_seg: dict[int, Segment] = {}

    def add(self, seg: Segment, raw_mask: np.ndarray, frames: Sequence[int]) -> None:
        self.by_seg[int(seg.id)] = (seg, raw_mask)
        for k in frames:
            self._frame_seg[int(k)] = seg

    def __bool__(self) -> bool:
        return bool(self.by_seg)

    def mask(self, k: int) -> np.ndarray | None:
        """Bool competitor-proxy mask of the RAW-only overlays at frame k (None when none applies)."""
        import cv2
        from .geometry import to_cv_matrix
        k = int(k)
        if k in self._memo:
            return self._memo[k]
        s = self._frame_seg.get(k)
        out = None
        if s is not None:
            sim = seg_sim(s, k, *self.raw_wh)
            if sim is not None:
                _seg, rm = self.by_seg[int(s.id)]
                m = to_cv_matrix(sim, bool(s.flip_h), float(self.raw_wh[0]), self.raw.ratio, self.comp.ratio)
                w, h = int(self.comp.size[0]), int(self.comp.size[1])
                o = cv2.warpAffine(rm.astype(np.uint8) * 255, np.asarray(m, np.float64)[:2], (w, h),
                                   flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
                if self.dilate_px > 0 and o.any():
                    d = self.dilate_px
                    o = cv2.dilate(o.astype(np.uint8), np.ones((2 * d + 1, 2 * d + 1), np.uint8)) > 0
                out = o if o.any() else None
        if len(self._memo) > 256:
            self._memo.clear()
        self._memo[k] = out
        return out

    def allowed(self, allowed_fn: Callable[[int], np.ndarray | None]) -> Callable[[int], np.ndarray | None]:
        """``allowed_fn`` with the RAW-only overlays removed."""
        def f(k: int) -> np.ndarray | None:
            a = allowed_fn(int(k))
            o = self.mask(int(k))
            if o is None:
                return a
            if a is None:
                return ~o
            return np.asarray(a, bool) & ~o
        return f


def find_raw_only_overlays(comp: Any, raw: Any, segments: Sequence[Segment], fm: FrameMap,
                           allowed_fn: Callable[[int], np.ndarray | None], box_fn: Callable[[int], Box | dict | None],
                           raw_wh: tuple[float, float], comp_fps: Fraction, raw_fps: Fraction, n_raw: int | None,
                           cfg: Any) -> RawOnlyOverlays:
    """RAW-only overlays of every raw segment (section comment above). Deterministic; layout-only masks."""
    import cv2
    from .geometry import to_cv_matrix
    blur = float(getattr(cfg, "score_blur", 1.0))
    reach = int(math.ceil(3.0 * blur))
    dil = int(getattr(cfg, "overlay_dilate_px", 3)) + reach
    out = RawOnlyOverlays(comp, raw, raw_wh, comp_fps, raw_fps, dil)
    if comp is None or raw is None or getattr(comp, "frames", None) is None or getattr(raw, "frames", None) is None:
        return out
    n_s = int(getattr(cfg, "verify_overlay_samples", 16))
    span = int(getattr(cfg, "verify_overlay_raw_span", 5))
    st_thr = float(getattr(cfg, "verify_overlay_static", 6.0))
    rk = float(getattr(cfg, "verify_overlay_resid_k", 4.0))
    rmin = float(getattr(cfg, "verify_overlay_resid_min", 12.0))
    persist = float(getattr(cfg, "verify_overlay_persist", 0.8))
    cvar = float(getattr(cfg, "verify_overlay_comp_var", 8.0))
    gratio = float(getattr(cfg, "verify_overlay_grad_ratio", 2.0))
    max_frac = float(getattr(cfg, "verify_overlay_max_frac", 0.15))
    min_px = int(getattr(cfg, "verify_overlay_min_px", 24))
    n = int(comp.n)
    status = np.asarray(fm.status) if fm is not None else None
    seg_at = single_raw_segments(segments, n)
    rw, rh = int(raw.size[0]), int(raw.size[1])
    cw, ch = int(comp.size[0]), int(comp.size[1])
    for s in sorted((s for s in segments if s.type == "raw"), key=lambda s: (s.comp_in, s.id)):
        ks = [k for k in range(max(0, s.comp_in), min(n, s.comp_out)) if seg_at.get(k) is s and comp.has(k)
              and (status is None or (k < len(status) and int(status[k]) == Status.MATCH))]
        if len(ks) < 3:
            continue
        samples = []
        for k in _sample_evenly(ks, n_s):
            sh = seg_shown(s, k, comp_fps, raw_fps, n_raw)
            sim = seg_sim(s, k, *raw_wh)
            if sh is None or sim is None:
                continue
            j = int(sh[0]) + (1 if sh[1] >= 0.5 else 0)
            if not raw.has(j):
                continue
            m = np.asarray(to_cv_matrix(sim, bool(s.flip_h), float(raw_wh[0]), raw.ratio, comp.ratio), np.float64)[:2]
            x, y, w, h = proxy_roi(box_fn(k) if box_fn is not None else None, comp.size, comp.ratio)
            al = allowed_fn(k)
            am = np.zeros((ch, cw), np.uint8)
            am[y:y + h, x:x + w] = 255 if al is None else (np.asarray(al, bool)[y:y + h, x:x + w] * 255).astype(np.uint8)
            flags = cv2.WARP_INVERSE_MAP
            c_r = cv2.warpAffine(np.asarray(comp.get(k), np.float32), m, (rw, rh), flags=cv2.INTER_LINEAR | flags,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            v_r = cv2.warpAffine(am, m, (rw, rh), flags=cv2.INTER_NEAREST | flags, borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=0)
            v_r = cv2.erode(v_r, np.ones((2 * reach + 1, 2 * reach + 1), np.uint8)) > 0
            if int(v_r.sum()) < 256:
                continue
            c_b = _blur(c_r, blur)
            r_b = _blur(np.asarray(raw.get(j), np.float32), blur)
            xs, ys = r_b[v_r].astype(np.float64), c_b[v_r].astype(np.float64)
            A = np.stack([xs, np.ones_like(xs)], axis=1)
            coef = np.linalg.lstsq(A, ys, rcond=None)[0]
            res = np.abs(ys - A @ coef)
            keep = res <= np.percentile(res, 80.0)            # refit without the worst 20 % (the overlay itself)
            if int(keep.sum()) >= 64:
                coef = np.linalg.lstsq(A[keep], ys[keep], rcond=None)[0]
            R = np.abs(c_b - (coef[0] * r_b + coef[1]))
            rv = R[v_r]
            med = float(np.median(rv))
            thr = max(rmin, med + rk * 1.4826 * float(np.median(np.abs(rv - med))))
            samples.append((k, j, v_r, (R > thr) & v_r, c_b))
        if len(samples) < 3:
            continue
        js = _raw_span([sm[1] for sm in samples], span, lambda j: raw.has(int(j)) and (n_raw is None or 0 <= j < n_raw))
        stack = np.stack([_blur(np.asarray(raw.get(int(j)), np.float32), blur) for j in js])
        raw_range = stack.max(axis=0) - stack.min(axis=0)
        static = raw_range <= st_thr
        valid = np.stack([sm[2] for sm in samples])
        high = np.stack([sm[3] for sm in samples])
        nv = valid.sum(axis=0)
        nh = high.sum(axis=0)
        cand = static & (nv >= 3) & (nh >= persist * np.maximum(nv, 1))
        seen = nv > 0
        visible = int(seen.sum())
        if not cand.any() or visible == 0:
            continue
        cvals = np.stack([sm[4] for sm in samples])
        cnt = np.maximum(nv, 1)
        mean = np.where(valid, cvals, 0.0).sum(axis=0) / cnt
        cstd = np.sqrt(np.maximum(np.where(valid, (cvals - mean) ** 2, 0.0).sum(axis=0) / cnt, 0.0))
        rstd = stack.std(axis=0)
        many = nv >= 3
        play_c = float(np.percentile(cstd[many], 75)) if many.any() else 0.0
        n_shown = len({sm[1] for sm in samples})
        if n_shown < 3 or play_c < cvar:
            # a hold / freeze or a static competitor: 'static in RAW coordinates' is not measurable (and a static scene
            # shown at a wrong, far-away RAW frame would differ only where the RAW once changed) -- nothing explained
            if int(cand.sum()) >= min_px:
                out.rejected.append({"segment": int(s.id), "frames": [int(ks[0]), int(ks[-1])], "pixels": int(cand.sum()),
                                     "why": f"{n_shown} distinct RAW frame(s) shown, competitor picture change "
                                            f"{play_c:.1f} (8-bit std, 75th pct): a RAW-only overlay is only measured "
                                            "where both play"})
            continue
        gr = _gradmag_f(stack.mean(axis=0))
        gc = _gradmag_f(mean)
        closed = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 5), np.uint8)) > 0
        nlab, lab, stt, _c = cv2.connectedComponentsWithStats(closed.astype(np.uint8), connectivity=8)
        # glyph groups of one text line (vertical overlap >= half the smaller height, gap <= 1.5 x the taller) -> one
        # region: a disclaimer is one rectangle, not one per word
        boxes = [[int(v) for v in stt[i, :4]] + [i] for i in range(1, nlab)]
        parent = list(range(len(boxes)))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                xa, ya, wa, ha, _ = boxes[a]
                xb, yb, wb, hb, _ = boxes[b]
                vov = min(ya + ha, yb + hb) - max(ya, yb)
                gap = max(xa, xb) - min(xa + wa, xb + wb)
                if vov >= 0.5 * min(ha, hb) and gap <= 1.5 * max(ha, hb):
                    parent[find(a)] = find(b)
        groups: dict[int, list[int]] = {}
        for a in range(len(boxes)):
            groups.setdefault(find(a), []).append(boxes[a][4])
        acc_mask = np.zeros((rh, rw), bool)
        regions = []
        for members in sorted(groups.values(), key=lambda m: (int(stt[m[0], 1]), int(stt[m[0], 0]))):
            x0 = min(int(stt[i, 0]) for i in members)
            y0 = min(int(stt[i, 1]) for i in members)
            ww = max(int(stt[i, 0] + stt[i, 2]) for i in members) - x0
            hh = max(int(stt[i, 1] + stt[i, 3]) for i in members) - y0
            comp_px = cand & np.isin(lab, members)
            npx = int(comp_px.sum())
            reg = {"segment": int(s.id), "raw_rect_proxy": [x0, y0, ww, hh], "pixels": npx,
                   "raw_rect": [round(x0 / raw.ratio[0], 1), round(y0 / raw.ratio[1], 1), round(ww / raw.ratio[0], 1),
                                round(hh / raw.ratio[1], 1)],
                   "comp_std": round(float(np.median(cstd[comp_px])), 2) if npx else None,
                   "raw_std": round(float(np.median(rstd[comp_px])), 2) if npx else None,
                   "grad_raw": round(float(np.mean(gr[comp_px])), 2) if npx else None,
                   "grad_comp": round(float(np.mean(gc[comp_px & many])), 2) if npx and (comp_px & many).any() else None,
                   "frac": round(ww * hh / float(visible), 4), "raw_frames": [int(js[0]), int(js[-1])],
                   "frames": [int(ks[0]), int(ks[-1])]}
            if npx < min_px:
                continue                       # specks: not even a candidate worth listing
            why = None
            if reg["grad_comp"] is None or reg["grad_raw"] < gratio * reg["grad_comp"]:
                why = (f"the RAW shows no graphic the competitor lacks there (edge energy RAW {reg['grad_raw']} vs "
                       f"competitor {reg['grad_comp']}, < {gratio:g}x): a competitor-side element or a mismatch")
            elif reg["frac"] > max_frac:
                why = f"too large ({reg['frac']:.1%} of the visible picture > {max_frac:.0%})"
            if why is not None:
                out.rejected.append({**reg, "why": why})
                continue
            regions.append(reg)
            acc_mask[y0:y0 + hh, x0:x0 + ww] = True
        if not regions:
            continue
        tot = float(acc_mask.sum()) / float(visible)
        if tot > max_frac:
            out.rejected.extend({**r, "why": f"the segment's regions together cover {tot:.1%} of the visible picture "
                                             f"(> {max_frac:.0%}): a mismatch spread over the frame"} for r in regions)
            continue
        out.add(s, cv2.dilate(acc_mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0, ks)
        vis = [k for k in ks if out.mask(k) is not None]
        for r in regions:
            r["frames"] = [int(vis[0]), int(vis[-1])] if vis else r["frames"]
        out.regions.extend(regions)
    return out


def raw_only_overlay_lines(regions: Sequence[dict]) -> list[str]:
    """One line per RAW-only overlay, consecutive segments with overlapping RAW rectangles merged."""
    groups: list[dict] = []
    for r in sorted(regions, key=lambda r: (r["frames"][0], r["segment"])):
        x, y, w, h = r["raw_rect"]
        g = groups[-1] if groups else None
        if g is not None and r["frames"][0] <= g["frames"][1] + 1:
            gx, gy, gw, gh = g["raw_rect"]
            ix = max(0.0, min(gx + gw, x + w) - max(gx, x))
            iy = max(0.0, min(gy + gh, y + h) - max(gy, y))
            if ix * iy >= 0.3 * min(gw * gh, w * h):
                x0, y0 = min(gx, x), min(gy, y)
                g["raw_rect"] = [x0, y0, max(gx + gw, x + w) - x0, max(gy + gh, y + h) - y0]
                g["frames"] = [g["frames"][0], max(g["frames"][1], r["frames"][1])]
                g["segments"].append(r["segment"])
                continue
        groups.append({"raw_rect": [x, y, w, h], "frames": list(r["frames"]), "segments": [r["segment"]]})
    return [f"RAW-only overlay at {g['raw_rect'][0]:.0f},{g['raw_rect'][1]:.0f},{g['raw_rect'][2]:.0f},"
            f"{g['raw_rect'][3]:.0f} (x,y,w,h in RAW px) over frames {g['frames'][0]}-{g['frames'][1]}: not shown by the "
            f"competitor (segment(s) {', '.join('S%02d' % s for s in g['segments'])})" for g in groups]


def recreation_proxy_frame(comp: Any, raw: Any, seg_at: dict[int, Segment], k: int, raw_wh: tuple[float, float],
                           comp_fps: Fraction, raw_fps: Fraction, n_raw: int | None) -> np.ndarray | None:
    """The recreation at competitor frame k on the WHOLE competitor proxy (uint8): the shown RAW proxy frame (a Frame
    Mix's two frames mixed) warped with its segment's own model, 0 outside the RAW frame; None when k is not shown by
    one raw segment."""
    from . import scoring
    s = seg_at.get(int(k))
    if s is None:
        return None
    sh = seg_shown(s, int(k), comp_fps, raw_fps, n_raw)
    sim = seg_sim(s, int(k), *raw_wh)
    if sh is None or sim is None or not raw.has(int(sh[0])):
        return None
    j, f = sh
    full = (0, 0, int(comp.size[0]), int(comp.size[1]))
    img, valid = scoring.warp_to_roi(np.asarray(raw.get(int(j))), sim, bool(s.flip_h), float(raw_wh[0]), raw.ratio,
                                     comp.ratio, full)
    if f > 0.0 and raw.has(int(j) + 1):
        img1, v1 = scoring.warp_to_roi(np.asarray(raw.get(int(j) + 1)), sim, bool(s.flip_h), float(raw_wh[0]), raw.ratio,
                                       comp.ratio, full)
        img, valid = (1.0 - f) * img + f * img1, valid & v1
    return np.clip(np.rint(np.where(valid, img, 0.0)), 0, 255).astype(np.uint8)


def animated_text_zones(comp: Any, raw: Any, segments: Sequence[Segment], layout: Any, overlays: Any,
                        raw_wh: tuple[float, float], comp_fps: Fraction, raw_fps: Fraction, n_raw: int | None,
                        cfg: Any) -> list[dict]:
    """Animated (moving) text overlays of the competitor for the temporal signature (s9_2b, wave 4): the layout
    module's comp-only detector (layout.animated_text_overlays: outlined text that moves over the picture), each word
    checked against the recreation -- one the recreation also shows is picture content, never masked. Returns every
    candidate (kind 'overlay' | 'picture_content' | 'not_compared': no RAW segment shows its frames); [] without a
    layout module / proxy."""
    from . import layout as layout_mod
    fn = getattr(layout_mod, "animated_text_overlays", None)
    if fn is None or comp is None or getattr(comp, "frames", None) is None:
        return []
    seg_at = single_raw_segments(segments, int(comp.n))
    ref = (lambda k: recreation_proxy_frame(comp, raw, seg_at, k, raw_wh, comp_fps, raw_fps, n_raw)) \
        if raw is not None and getattr(raw, "frames", None) is not None else None
    return fn(comp, layout, cfg, overlays=overlays, reference=ref)


def temporal_signatures(tf: TemporalFrames, cfg: Any) -> tuple[Any, Any, Any]:
    """(competitor signature (k, k+1) and (k, k+2), its labels, recreation signature (k, k+1))."""
    from . import temporal
    ks = range(tf.n)
    comp_sig = temporal.measure(tf.comp_frame, ks, cfg, gaps=(1, 2), same=tf.same)
    breaks = [k for k in range(tf.n - 1) if not tf.same(k, k + 1)]
    labels = temporal.label_pairs(comp_sig, cfg, breaks=breaks)
    rec_ks = sorted(tf.seg_at)
    rec_sig = temporal.measure(tf.rec_frame, rec_ks, cfg, gaps=(1,), same=tf.same)
    return comp_sig, labels, rec_sig


def check_temporal(segments: Sequence[Segment], labels: Any, comp_sig: Any, rec_sig: Any, comp_fps: Fraction,
                   raw_fps: Fraction, n_raw: int | None, n: int, cfg: Any, masked: Sequence[dict] = ()) -> dict:
    """s9_2b: the recreation's temporal signature against the competitor's (comp-only labels, temporal.py).
    Both signatures are measured on the layout's caption / overlay masks AND without the animated text overlays
    ``masked`` (``animated_text_zones``, kind 'overlay': moving words the recreation does not show), so a hold
    under a sliding caption is judged by the motion OUTSIDE the overlays. For every pair (k, k+1) of frames each
    shown by ONE raw segment:

    * the competitor MOVEs but the recreation shows the same RAW frame twice -> 'recreation repeats';
    * the competitor REPEATs but the recreation changes RAW frame by more than the shot's repeat/move split
      AND more than temporal_mag_ratio x the competitor's change -> 'recreation changes RAW frame';
    * both change, but by residuals more than temporal_mag_ratio apart after the shot's own measured
      comp/recreation bias (median over the shot's within-segment pairs) -> 'recreation jumps' / 'competitor
      changes more' (fake boundaries, wrong skips, jump-backs);
    * a hold of >= 4 frames in the recreation (>= 3 consecutive repeat pairs) whose labelled competitor pairs
      mostly MOVE -> 'motion mismatch' (always a failure: a freeze where the competitor plays).

    Unlabelled pairs (cut / unknown) are never evidence. Disagreeing pairs count against frame_exact_min over
    all pairs considered (like the other criterion-3 frame classes); listed otherwise."""
    from . import temporal as T
    exact_min = float(getattr(cfg, "frame_exact_min", 0.99))
    R = float(getattr(cfg, "temporal_mag_ratio", 3.0))
    seg_at = single_raw_segments(segments, n)
    floors = [float(s["floor"]) for s in labels.shots if s.get("floor")]
    g_floor = float(np.median(floors)) if floors else None
    n_pairs = n_checked = n_unmeasured = 0
    bad: list[dict] = []
    same_pairs: list[tuple[int, int, str]] = []           # (k, segment id, comp label) of recreation repeats
    movemove: list[dict] = []
    for k in range(int(n) - 1):
        s0, s1 = seg_at.get(k), seg_at.get(k + 1)
        if s0 is None or s1 is None:
            continue
        sh0, sh1 = seg_shown(s0, k, comp_fps, raw_fps, n_raw), seg_shown(s1, k + 1, comp_fps, raw_fps, n_raw)
        if sh0 is None or sh1 is None:
            continue
        (j0, f0), (j1, f1) = sh0, sh1
        n_pairs += 1
        lab = labels.get(k)
        # the same picture twice: one RAW frame (or one Frame Mix of the same two frames at the same weight)
        same = j0 == j1 and abs(f0 - f1) <= PLAN_FRAME_TOL and bool(s0.flip_h) == bool(s1.flip_h)
        if same:
            same_pairs.append((k, s0.id if s0 is s1 else -1, lab))
        pc = comp_sig.d1.get(k)
        if lab not in (T.REPEAT, T.MOVE) or pc is None or not math.isfinite(pc.cc):
            continue
        n_checked += 1
        shot = labels.shot_info(k) or {}
        floor = shot.get("floor") or g_floor
        row = {"k": int(k), "comp": lab, "raw": [int(j0), int(j1)], "seg": [s0.id, s1.id], "r_comp": round(pc.r, 6)}
        if same:
            if lab == T.MOVE:
                bad.append({**row, "kind": "recreation_repeats", "why": f"the recreation shows RAW {j0} twice where "
                                                                         "the competitor changes"})
            continue
        pr = rec_sig.d1.get(k)
        if pr is None or not math.isfinite(pr.cc):
            n_unmeasured += 1
            continue
        row["r_rec"] = round(pr.r, 6)
        if lab == T.REPEAT:
            thr = float(shot.get("threshold") or 0.0)
            if pr.r >= thr and pr.r > R * max(pc.r, float(floor or 0.0)):
                bad.append({**row, "kind": "recreation_changes", "why": f"the recreation changes RAW {j0}->{j1} where "
                                                                         "the competitor repeats its image"})
            continue
        movemove.append({**row, "shot": shot.get("id"), "within": s0 is s1, "_r": (pr.r, pc.r, float(floor or 0.0))})
    bias: dict[Any, float] = {}
    by_shot: dict[Any, list[float]] = {}
    for m in movemove:
        if m["within"]:
            rr, rc, f = m["_r"]
            by_shot.setdefault(m["shot"], []).append(math.log(max(rr, f, T.R_EPS) / max(rc, f, T.R_EPS)))
    for sid, v in by_shot.items():
        if len(v) >= 3:
            bias[sid] = float(np.median(v))
    for m in movemove:
        rr, rc, f = m.pop("_r")
        b = bias.get(m["shot"], 0.0)
        lr = math.log(max(rr, f, T.R_EPS) / max(rc, f, T.R_EPS)) - b
        m["ratio"] = round(math.exp(lr), 4)
        if lr > math.log(R):
            bad.append({**m, "kind": "recreation_jumps", "why": f"the recreation changes {m['ratio']:.1f}x more than "
                                                                 f"the competitor (RAW {m['raw'][0]}->{m['raw'][1]})"})
        elif lr < -math.log(R):
            bad.append({**m, "kind": "competitor_changes_more", "why": f"the competitor changes {1 / m['ratio']:.1f}x "
                                                                        f"more than the recreation (RAW {m['raw'][0]}->{m['raw'][1]})"})
    # holds of the recreation against a moving competitor (S65-type freeze)
    mismatch: list[dict] = []
    run: list[tuple[int, int, str]] = []
    for item in same_pairs + [(-10, -2, "")]:
        if run and (item[0] != run[-1][0] + 1 or item[1] != run[-1][1] or item[1] < 0):
            if len(run) >= 3:
                n_mv = sum(1 for _k, _s, lab in run if lab == T.MOVE)
                n_rp = sum(1 for _k, _s, lab in run if lab == T.REPEAT)
                if n_mv >= 2 and n_mv > n_rp:
                    mismatch.append({"segment": run[0][1], "frames": [run[0][0], run[-1][0] + 1], "comp_move": n_mv,
                                     "comp_repeat": n_rp})
            run = []
        if item[1] >= 0:
            run.append(item)
    bad.sort(key=lambda d: d["k"])
    frac = 1.0 - len(bad) / n_pairs if n_pairs else 1.0
    failures: list[str] = []
    exceptions: list[str] = []
    for mm in mismatch:
        failures.append(f"motion mismatch: S{int(mm['segment']):02d} holds one RAW frame on frames {mm['frames'][0]}-"
                        f"{mm['frames'][1]} while the competitor moves ({mm['comp_move']} of its pairs change)")
    kinds = {}
    for d in bad:
        kinds.setdefault(d["kind"], []).append(d["k"])
    listing = "; ".join(f"{kd.replace('_', ' ')}: pairs k = {_ranges(ks)[:10]}" for kd, ks in kinds.items())
    if bad and frac < exact_min:
        failures.append(f"temporal signature differs from the competitor on {len(bad)}/{n_pairs} frame pairs "
                        f"({frac:.4%} agree < {exact_min:.0%}): {listing}")
    elif bad:
        exceptions.append(f"temporal signature differs on {len(bad)}/{n_pairs} frame pairs: {listing}")
    counts = labels.counts()
    status = _status_from(len(failures), len(exceptions))
    ov = [z for z in masked if z.get("kind", "overlay") == "overlay"]
    summary = (f"{n_pairs} frame pairs, {n_checked} with a competitor repeat/move label ({counts.get(T.REPEAT, 0)} "
               f"repeat, {counts.get(T.MOVE, 0)} move, {counts.get(T.UNKNOWN, 0)} unknown, {counts.get(T.CUT, 0)} cut); "
               f"{len(bad)} disagree, {len(mismatch)} motion mismatch(es)"
               + (f"; {len(ov)} animated text overlay(s) masked ("
                  + ", ".join(f"frames {z['comp_in']}-{z['comp_out'] - 1}" for z in ov[:4]) + ")" if ov else ""))
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions,
            "pairs": n_pairs, "checked": n_checked, "unmeasured": n_unmeasured, "fraction_agree": round(frac, 6),
            "disagreements": bad[:500], "n_disagreements": len(bad), "motion_mismatch": mismatch,
            "labels": T.summary(labels), "noise_floor": g_floor, "bias": {str(k): round(math.exp(v), 4) for k, v in bias.items()},
            "animated_text": [{k: v for k, v in z.items() if k != "rects"} for z in masked][:50]}


def check_refit(segments: Sequence[Segment], fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction,
                raw_wh: tuple[float, float], n_raw: int | None, scorer: Any, cfg: Any, n: int,
                box_centre: Sequence[float] | None = None) -> dict:
    """s9_2c: +-1 RAW frame refit. On every matched frame shown by one raw segment, the neighbours j-1, j+1 of
    the shown RAW frame j each get their OWN framing by ECC (from the shown model and, for a rotated model,
    from its derotated version); a neighbour that beats the shown (frame, framing) -- and the shown frame's own
    refit, computed when a neighbour comes within verify_refit_margin -- by more than max(3 * delta,
    verify_refit_margin) (delta = scoring.noise_delta of the segment's shown scores) means the recreation shows
    the wrong RAW frame with a compensating framing (time / translation confound). Such frames count against
    frame_exact_min; listed otherwise. RAW-identical neighbours never trigger it (they score the same)."""
    from . import scoring
    exact_min = float(getattr(cfg, "frame_exact_min", 0.99))
    floor_margin = float(getattr(cfg, "verify_refit_margin", 0.01))
    dmin, dmax = float(getattr(cfg, "soft_delta_min", 0.001)), float(getattr(cfg, "soft_delta_max", 0.01))
    if not _can_refit(scorer):
        return {"status": "not_available", "summary": "no framing re-measurement available", "failures": []}
    seg_at = single_raw_segments(segments, n)
    status = np.asarray(fm.status)
    rows_all: list[dict] = []
    bad: list[dict] = []
    for s in sorted((s for s in segments if s.type == "raw"), key=lambda s: (s.comp_in, s.id)):
        rows = []
        for k in range(max(0, s.comp_in), min(int(n), s.comp_out)):
            if seg_at.get(k) is not s or k >= len(status) or int(status[k]) != Status.MATCH:
                continue
            sh = seg_shown(s, k, comp_fps, raw_fps, n_raw)
            sim = seg_sim(s, k, *raw_wh)
            if sh is None or sim is None:
                continue
            j0, f = sh
            # a Frame Mix (FX-08) shows (1 - f) RAW j0 + f RAW j0 + 1: the neighbours of its dominant frame are judged
            # against the mix itself (and the dominant frame's own refit)
            mixed = f > 0.0 and hasattr(scorer, "score_with_mix")
            j = j0 + 1 if mixed and f >= 0.5 else j0
            flip = bool(s.flip_h)
            inits = [derotated(sim, box_centre)] if box_centre is not None and abs(sim.theta_deg) > 1e-9 else []
            fits: dict[int, Sim] = {}
            for jj in (j - 1, j + 1):
                if jj < 0 or (n_raw is not None and jj >= n_raw):
                    continue
                r = scorer.refit(k, (jj, sim, flip), inits)
                if r is not None:
                    fits[jj] = r[0]

            def scores() -> tuple[float, dict[int, float]]:
                order = sorted(fits)
                others = [(jj, fits[jj], flip) for jj in order]
                if mixed:
                    z0, sc = scorer.score_with_mix(k, (j0, sim, flip), (j0 + 1, sim, flip), f, others)
                    return float(z0), {jj: float(sc[i]) for i, jj in enumerate(order)}
                sc = scorer.score(k, [(j, sim, flip)] + others)
                return float(sc[0]), {jj: float(sc[1 + i]) for i, jj in enumerate(order)}
            z_shown, z = scores()
            if not math.isfinite(z_shown):
                continue
            # the shown frame's own refit only matters when a neighbour comes within the smallest possible margin
            # (it can only raise the bar the neighbour must clear)
            if any(math.isfinite(v) and v > z_shown + floor_margin for v in z.values()):
                r = scorer.refit(k, (j, sim, flip), inits)
                if r is not None:
                    fits[j] = r[0]
                    z_shown, z = scores()
                    if not math.isfinite(z_shown):
                        continue
            z_self = _nanmax([z_shown, z.get(j, float("nan"))])
            nb = {jj: v for jj, v in z.items() if jj != j and math.isfinite(v)}
            j_nb = max(nb, key=nb.get) if nb else None
            rows.append({"k": int(k), "seg": s.id, "raw": int(j), "z_shown": round(z_shown, 6),
                         "z_refit": _r6(z.get(j, float("nan"))), "best_neighbour": j_nb,
                         "z_neighbour": None if j_nb is None else round(nb[j_nb], 6), "_self": z_self})
        delta = scoring.noise_delta([r["z_shown"] for r in rows], dmin, dmax)
        margin = max(3.0 * delta, floor_margin)
        for r in rows:
            z_self = r.pop("_self")
            r["margin"] = round(margin, 6)
            if r["z_neighbour"] is not None and r["z_neighbour"] > z_self + margin:
                bad.append(r)
        rows_all.extend(rows)
    n_checked = len(rows_all)
    frac = 1.0 - len(bad) / n_checked if n_checked else 1.0
    failures: list[str] = []
    exceptions: list[str] = []
    listing = (f"frames {_ranges([r['k'] for r in bad])[:12]} (e.g. k={bad[0]['k']}: RAW {bad[0]['best_neighbour']} "
               f"{bad[0]['z_neighbour']} vs shown RAW {bad[0]['raw']} {bad[0]['z_shown']})") if bad else ""
    if bad and frac < exact_min:
        failures.append(f"{len(bad)}/{n_checked} matched frames show a RAW frame whose neighbour (own refitted framing) "
                        f"matches the competitor better ({frac:.4%} ok < {exact_min:.0%}): {listing}")
    elif bad:
        exceptions.append(f"{len(bad)}/{n_checked} matched frames: a neighbouring RAW frame matches better: {listing}")
    status_out = _status_from(len(failures), len(exceptions))
    summary = f"{n_checked} matched frames refitted with RAW j-1 / j / j+1: {len(bad)} where a neighbour wins ({frac:.4%} ok)"
    return {"status": status_out, "summary": summary, "failures": failures, "exceptions": exceptions,
            "checked": n_checked, "fraction_ok": round(frac, 6), "neighbour_wins": bad[:500], "n_neighbour_wins": len(bad)}


# ---------------------------------------------------------------------------------------------
# s9_2 AE-semantics simulation vs m(k) (criterion 3)
# ---------------------------------------------------------------------------------------------

def visible_raw_frame(entries: Sequence[dict] | None) -> int | None:
    """RAW frame that fully shows at one MAIN frame. Entries are top -> bottom (integer 'layer' values
    are sorted ascending, AE's 1 = top). With compositing weights (export_ae.simulate_ae), the entry
    whose weight is 1 is the visible one; otherwise the top-most fully opaque layer. Returns None for a
    blend (crossfade interior), when nothing covers the frame, or when a non-RAW layer covers it."""
    e = visible_entry(entries)
    j = None if e is None else e.get("raw_frame")
    return None if j is None else int(j)


def visible_entry(entries: Sequence[dict] | None) -> dict | None:
    """The simulated layer entry that fully shows at one MAIN frame (see :func:`visible_raw_frame`); its 'mix' (when
    present) is the Frame Mix weight of RAW raw_frame + 1 (FX-08)."""
    ents = [e for e in (entries or []) if not e.get("guide")]
    if ents and all(isinstance(e.get("layer"), (int, np.integer)) for e in ents):
        ents = sorted(ents, key=lambda e: int(e["layer"]))
    if ents and all(e.get("weight") is not None for e in ents):
        for e in ents:
            if float(e["weight"]) >= 0.99999:
                return e
        return None
    percent = any(float(e.get("opacity", 100) if e.get("opacity") is not None else 100) > 1.0 + 1e-9 for e in ents)
    for e in ents:
        op = e.get("opacity", 100.0)
        op = 100.0 if op is None else float(op)
        full = op >= 99.999 if percent else op >= 0.99999
        if full:
            return e
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


def _tie_slack(seg: Segment | None, k: int, comp_fps: Fraction, raw_fps: Fraction) -> float | None:
    """Distance (RAW frames) of the segment's own continuous position at comp frame k to the nearest frame boundary
    -- the per-frame slack of the plan, which reproduces the cutlist exactly (plan vs cutlist is checked on every
    frame). None for a hold (speed 0: a freeze is exported with keys inside a RAW frame) or a non-raw segment."""
    if seg is None or seg.type != "raw" or float(seg.speed or 0.0) == 0.0:
        return None
    p = seg_raw_position(seg, k, comp_fps, raw_fps)
    if p is None or not math.isfinite(p):
        return None
    return abs(p - round(p))


def reassignment_evidence(rows: list[dict], fm: FrameMap, seg_of_k: dict[int, Segment], scorer: Any,
                          raw_wh: tuple[float, float], cfg: Any, memo: dict | None = None, limit: int = 200) -> None:
    """FX-11 / FX-12 evidence of re-assigned and mismatched c3 rows, in place: ``why`` (segment.py's reason,
    FrameMap 'reassigned'), the AE frame's and refine's frame's scores each under its OWN per-frame ECC refit (from
    the segment model; not the model's framing), ``gap`` = z(m) - z(AE frame), the frame's ``delta`` (refine's score
    noise) and a ``class``: 'within noise' (gap <= delta), 'outside noise', or 'systematic run' (>= 2 consecutive rows
    re-assigned in the same direction whose summed gap exceeds delta -- the measured frames jointly prefer another
    line). Evidence only: no class is an exemption."""
    from .model import REASSIGN_REASONS
    dmin = float(getattr(cfg, "soft_delta_min", 0.001))
    ra = np.asarray(fm.reassigned) if "reassigned" in fm.__dict__.get("d", {}) else None
    dcol = np.asarray(fm.delta) if "delta" in fm.__dict__.get("d", {}) else None
    memo = {} if memo is None else memo
    for r in rows:
        k = int(r["k"])
        if ra is not None and 0 <= k < len(ra):
            r["why"] = REASSIGN_REASONS[int(ra[k])] if int(ra[k]) > 0 else r.get("why", "mismatch")
        d = float(dcol[k]) if dcol is not None and 0 <= k < len(dcol) and math.isfinite(float(dcol[k])) else dmin
        r["delta"] = round(d, 6)
    if scorer is None or not hasattr(scorer, "refit"):
        return
    for r in rows[:int(limit)]:
        k, j, m = int(r["k"]), r.get("ae"), r.get("m")
        s = seg_of_k.get(k)
        sim = seg_sim(s, k, *raw_wh) if s is not None else None
        if j is None or m is None or sim is None:
            continue
        zs = []
        for jj in (int(m), int(j)):
            key = (k, jj, bool(s.flip_h))
            if key not in memo:
                fit = scorer.refit(k, (jj, sim, bool(s.flip_h)))
                memo[key] = float(fit[1]) if fit is not None else float(scorer.score(k, [(jj, sim, bool(s.flip_h))])[0])
            zs.append(memo[key])
        if all(math.isfinite(z) for z in zs):
            r["z_m"], r["z_ae"] = round(zs[0], 5), round(zs[1], 5)
            r["gap"] = round(zs[0] - zs[1], 5)
    for r in rows:
        g = r.get("gap")
        r["class"] = None if g is None else ("within noise" if g <= r["delta"] else "outside noise")
    by_k = sorted((r for r in rows if r.get("ae") is not None and r.get("m") is not None), key=lambda r: r["k"])
    run: list[dict] = []
    for r in by_k + [None]:
        if r is not None and run and r["k"] == run[-1]["k"] + 1 and \
                np.sign(r["ae"] - r["m"]) == np.sign(run[-1]["ae"] - run[-1]["m"]):
            run.append(r)
            continue
        if len(run) >= 2 and sum(max(0.0, float(x.get("gap") or 0.0)) for x in run) > max(x["delta"] for x in run):
            for x in run:
                x["class"] = "systematic run"
        run = [r] if r is not None else []


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
                 n_raw: int | None = None, scorer: Any = None, raw_wh: tuple[float, float] | None = None,
                 evidence_memo: dict | None = None) -> dict:
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
    frame_mix: list[dict] = []          # Frame Mix frames (FX-08), the exact ones included in n_exact
    mix_ties: list[dict] = []           # ... whose m is the lighter source of a near-even mix (blend tie)
    mix_tie = float(getattr(cfg, "verify_mix_tie", 0.1))
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
        if cov is not None and len(cov) == 1 and cov[0].type in ("dip", "flash", "not_in_raw", "uncertain"):
            n_solid += 1
            w = sum(_entry_weight(e) for e in ents)
            if w > PLAN_SOLID_MAX_WEIGHT:
                plan_bad.append({"K": K, "what": cov[0].type, "problem": f"RAW visible (weight {w:.3f}) on a "
                                 f"{cov[0].type} frame of {_seg_name(cov[0])}"})
            continue
        k = main_to_comp(K, cf, mf)
        if not (0 <= k < fm.n) or int(post_status[k]) != Status.MATCH:
            continue
        ve = visible_entry(ents)
        j = None if ve is None or ve.get("raw_frame") is None else int(ve["raw_frame"])
        mix = None if ve is None or ve.get("mix") is None else float(ve["mix"])
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
        if mix is not None and j is not None and mix > PLAN_FRAME_TOL:
            # Frame Mix (FX-08): AE shows (1 - f) RAW j + f RAW j + 1. On a frame-blended competitor frame refine's
            # single-frame argmax is the heavier source -> compared with the mix's DOMINANT frame; m on the lighter
            # side counts only within verify_mix_tie of an even mix (a blend tie); otherwise the dominant frame is
            # judged like any shown frame below
            row["mix"] = round(mix, 4)
            dom = j + 1 if mix >= 0.5 else j
            if m == dom:
                n_exact += 1
                frame_mix.append(row)
                continue
            if m in (j, j + 1) and abs(mix - 0.5) <= mix_tie:
                frame_mix.append({**row, "tie": True})
                mix_ties.append(row)
                continue
            j = dom
            row["ae"] = j
        if j is not None and j == m:
            n_exact += 1
            continue
        lo, hi = int(ref["raw_lo"][k]), int(ref["raw_hi"][k])
        if j is not None and lo >= 0 and hi >= 0 and lo <= j <= hi:
            ambiguous.append({**row, "range": [lo, hi]})
            continue
        if j is not None and (bool(tie_post[k]) or bool(ref["tie"][k])) and abs(j - m) <= 1:
            # FX-11: a tie is a property of sampling a moving line ON a frame boundary -- accepted only where the
            # covering segment's own position is within TIE_SLACK of a boundary; a hold (freeze, frame-exact keys
            # at j + 0.25) never ties (with segments; without them the tie flag is all there is)
            sl = _tie_slack(seg_of_k.get(k), k, cf, Fraction(raw_fps) if raw_fps is not None else cf) \
                if segments is not None else 0.0
            if sl is not None and sl < float(getattr(_phase(), "TIE_SLACK", 1e-4)):
                ties.append({**row, "slack": None if segments is None else round(sl, 7)})
                continue
            row["tie_rejected"] = "hold" if sl is None else f"slack {sl:.2e} RAW frame >= TIE_SLACK"
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
    n_ok = n_exact + len(ambiguous) + len(ties) + len(grid) + len(mix_ties)
    frac = n_ok / n_matched if n_matched else 1.0
    for r in mismatches:
        r.setdefault("why", "mismatch")
    if raw_wh is not None and (reassigned or mismatches):
        reassignment_evidence(reassigned + mismatches, fm, seg_of_k, scorer, raw_wh, cfg, evidence_memo)
    classes: dict[str, int] = {}
    for r in reassigned + mismatches:
        if r.get("class"):
            classes[r["class"]] = classes.get(r["class"], 0) + 1
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
                      ("on the lighter side of a near-even Frame Mix (blend tie)", mix_ties),
                      ("between the bracketing competitor frames (different MAIN grid)", grid),
                      ("re-assigned by segmentation (AE shows the segment model's frame, not refine's measurement)",
                       reassigned),
                      ("not reproduced exactly (AE frame differs from the measured m(k))", mismatches)):
        if lst:
            cls: dict[str, int] = {}
            for x in lst:
                if x.get("class"):
                    cls[x["class"]] = cls.get(x["class"], 0) + 1
            extra_c = (" (" + ", ".join(f"{v} {c}" for c, v in sorted(cls.items())) + ")") if cls else ""
            exceptions.append(f"{source}: {len(lst)} frame(s) {name}: k = {_ranges([x['k'] for x in lst])[:10]}{extra_c}")
    n_exc = len(ambiguous) + len(ties) + len(grid) + len(reassigned) + len(mismatches) + len(mix_ties)
    status = "fail" if failures else ("pass_with_exceptions" if n_exc else "pass")
    summary = (f"{source}: {n_exact}/{n_matched} exact"
               + (f" ({len(frame_mix) - len(mix_ties)} on a Frame Mix's dominant frame)" if frame_mix else "")
               + f", {len(ambiguous)} ambiguous-identical, {len(ties)} timing-tie, "
               + (f"{len(mix_ties)} Frame Mix tie, " if mix_ties else "")
               + (f"{len(grid)} between competitor frames (MAIN grid), " if not same_grid else "")
               + f"{len(reassigned)} re-assigned, {len(mismatches)} mismatched ({frac:.4%} ok)")
    if tl is not None:
        summary += (f"; plan vs cutlist: {len(plan_bad)} differing frame(s) "
                    f"({n_trans} transition, {n_solid} placeholder/dip frames checked)")
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions,
            "reference": ref_name, "matched": n_matched, "exact": n_exact, "fraction_ok": round(frac, 6),
            "ambiguous_identical": ambiguous, "timing_tie": ties, "grid": grid[:500], "n_grid": len(grid),
            "frame_mix": frame_mix[:500], "n_frame_mix": len(frame_mix), "frame_mix_tie": mix_ties,
            "reassigned": reassigned[:500], "n_reassigned": len(reassigned),
            "mismatches": mismatches[:500], "n_mismatches": len(mismatches), "excluded_near_cuts": len(excluded),
            "reassigned_classes": classes,
            "plan_mismatches": plan_bad[:500], "n_plan_mismatches": len(plan_bad),
            "transition_frames_checked": n_trans, "solid_frames_checked": n_solid}


_AE_SIM_LISTS = ("ambiguous_identical", "timing_tie", "grid", "frame_mix_tie", "reassigned", "mismatches",
                 "plan_mismatches")


def _ae_sim_signature(r: dict) -> str:
    """What a check_ae_sim result classified, independent of its source label."""
    sig = {name: [x.get("k", x.get("K")) for x in (r.get(name) or [])] for name in _AE_SIM_LISTS}
    sig.update({k: r.get(k) for k in ("status", "matched", "exact", "fraction_ok", "n_plan_mismatches")})
    return json.dumps(sig, sort_keys=True, default=str)


def merge_ae_sim(p2: dict, m2: dict) -> dict:
    """s9_2 from the plan and the mock-run record. When both classify every frame identically (the usual case) the
    result is printed ONCE ('plan == mock record', FX-12); otherwise both are listed."""
    if p2.get("status") == m2.get("status") and "matched" in p2 and "matched" in m2 and \
            _ae_sim_signature(p2) == _ae_sim_signature(m2):
        lab = lambda s: s.replace("plan:", "plan == mock record:", 1) if s.startswith("plan:") else s  # noqa: E731
        p = dict(p2)
        p["failures"] = [lab(f) for f in p2.get("failures", [])]
        p["exceptions"] = [lab(e) for e in p2.get("exceptions", [])]
        p["summary"] = lab(str(p2.get("summary", "")))
        return {"status": p2["status"], "summary": p["summary"], "failures": p["failures"], "exceptions": p["exceptions"],
                "plan": p, "mock": {"status": m2["status"], "summary": "identical to the plan's simulation",
                                    "same_as_plan": True}, "same": True}
    return {"status": aggregate([p2["status"], m2["status"]]), "summary": f"{p2.get('summary')} | {m2.get('summary')}",
            "failures": p2.get("failures", []) + m2.get("failures", []),
            "exceptions": p2.get("exceptions", []) + m2.get("exceptions", []), "plan": p2, "mock": m2, "same": False}


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
        comp_mix = bool(comp.get("frameBlending"))
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
                p = src_t * footage_rate(L)
                j = int(math.floor(p + 1e-9))
                e = {"layer": key, "name": L.get("name"), "seg": int(sm.group(1)) if sm else None,
                     "raw_frame": j, "opacity": op, "weight": w_in * remaining * op}
                # AE Frame Mix (layer FRAME_MIX + the comp's frame-blending switch): RAW j + 1 blended in by the
                # fraction of the source position (FX-08)
                if comp_mix and L.get("frameBlendingType") == "FRAME_MIX":
                    e["mix"] = float(min(1.0, max(0.0, p - j)))
                entries.append(e)
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


FRAMES_SUFFIX = "  [frames]"   # export_ae's JSX renames a layer its read-back self-check switched to frames mode
AUDIO_TWIN_SUFFIX = "  audio"   # ... and moves its audio to a disabled audio-only twin '<name>  audio' (mc:<id>_audio)


def _record_name_ok(P: dict, L: dict, mode: str, remapped: bool) -> bool:
    """The recorded layer name is the plan's, or -- for a stretch layer the JSX self-check switched to
    frame-exact remapping at runtime -- the plan's name + '  [frames]' (export_ae.record_name_matches)."""
    if L.get("name") == P.get("name"):
        return True
    try:
        from .export_ae import record_name_matches
        if record_name_matches(P, L):
            return True
    except Exception:  # noqa: BLE001 - fall back to the local rule below
        pass
    return mode == "stretch" and remapped and L.get("name") == f"{P.get('name')}{FRAMES_SUFFIX}"


def _audio_twin_problems(P: dict, L: dict, rec_by_tag: dict, rec_by_name: dict,
                         checker: Callable[[dict, dict, dict], list[str]] | None, F: dict) -> list[str]:
    """A stretch layer the JSX self-check switched to frame-exact remapping keeps its sound on a runtime
    audio-only twin (export_ae addAudioTwin: stretch-placed, video disabled, audio enabled, the plan's audio
    level keys). Problems of that twin; none when the plan layer carries no audio or the footage has none."""
    if not P.get("audio") or not L.get("hasAudio"):
        return []
    T = rec_by_tag.get(f"{P.get('id')}_audio") if P.get("id") is not None else None
    if T is None:
        T = rec_by_name.get(f"{P.get('name')}{AUDIO_TWIN_SUFFIX}")
    if T is None:
        return ["switched to frame-exact remapping without its audio twin layer (the segment's audio is lost)"]
    probs = []
    if T.get("enabled") is not False or not T.get("audioEnabled"):
        probs.append(f"audio twin: enabled {T.get('enabled')} / audioEnabled {T.get('audioEnabled')} "
                     "(want video off, audio on)")
    if checker is not None:
        PA = {**P, "id": f"{P.get('id')}_audio", "kind": "raw_audio", "xf": None, "opacity": [], "mask": None,
              "maskPath": None, "enabled": False, "guide": False, "audio": True, "timeMode": "stretch"}
        try:
            probs.extend(f"audio twin: {m}" for m in checker(PA, T, F))
        except Exception as e:  # noqa: BLE001 - reported, not fatal
            probs.append(f"audio twin: key check unavailable: {type(e).__name__}: {e}")
    return probs


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
            # guide layers (an 'uncertain' segment's best-evidence RAW frames, FX-08) are never rendered
            if is_raw and L.get("enabled") is not False and L.get("guideLayer") is not True:
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
        mode = str(_get(P, "timeMode", "time_mode", default="stretch"))
        L = rec_by_tag.get(str(P.get("id"))) if P.get("id") is not None and rec_by_tag else None
        if L is None:
            L = rec_by_name.get(str(P.get("name")))
        if L is None and mode == "stretch":            # renamed by the JSX self-check (untagged records)
            L = rec_by_name.get(f"{P.get('name')}{FRAMES_SUFFIX}")
        if L is None:
            if str(P.get("kind")) == "reference" and not ((plan.get("footage") or {}).get("ref")):
                continue
            layer_problems.append(f"{P.get('id') or P.get('name')}: not found in the mock record")
            continue
        probs = []
        remapped = bool(_get(L, "timeRemapEnabled", default=False))
        if P.get("name") is not None and not _record_name_ok(P, L, mode, remapped):
            probs.append(f"name {L.get('name')!r} != {P.get('name')!r}")
        k_in, k_out = _get(P, "compIn", "comp_in"), _get(P, "compOut", "comp_out")
        want_in = _get(P, "inPoint", default=None if k_in is None else int(k_in) * den / num)
        want_out = _get(P, "outPoint", default=None if k_out is None else int(k_out) * den / num)
        for key, want in (("inPoint", want_in), ("outPoint", want_out)):
            if want is not None and not _close(_get(L, key), float(want)):
                probs.append(f"{key} {_get(L, key)} != {want}")
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
        if extra_problems is not None and "timeMode" in P:
            PP = P
            if mode == "stretch" and remapped:
                # switched to frame-exact remapping at runtime: check its keys / mask / opacity / switches as the
                # frames-mode layer the JSX made of it (its audio moved to a runtime audio twin) (review AE2-1)
                # (a plan without the per-frame 'expect' list: the remap keys are left to simulate_ae(record))
                PP = {**P, "timeMode": "frames" if "expect" in P else "still", "startTime": t_in, "stretch": 100.0,
                      "audio": False}
            try:
                probs.extend(extra_problems(PP, L, F))
            except Exception as e:  # noqa: BLE001 - a helper that cannot read this record is reported, not fatal
                probs.append(f"key/render-switch check unavailable: {type(e).__name__}: {e}")
        if mode == "stretch" and remapped:
            probs.extend(_audio_twin_problems(P, L, rec_by_tag, rec_by_name, extra_problems, F))
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

def framing_samples(seg: Segment, ks: Sequence[int], step: int, min_samples: int = 0, all_max: int = 0) -> list[int]:
    """Frames of a segment where framing is measured independently: every ``step``-th matched frame, the
    first and last matched frames, the transform key frames and at least ``min_samples`` evenly spaced frames;
    every matched frame of a segment with at most ``all_max`` of them."""
    ks = sorted(int(k) for k in ks)
    if not ks:
        return []
    if len(ks) <= int(all_max):
        return ks
    kset = set(ks)
    out = set(ks[::max(1, int(step))]) | {ks[0], ks[-1]}
    if min_samples and min_samples > 1:
        out |= {ks[int(i)] for i in np.round(np.linspace(0, len(ks) - 1, int(min_samples))).astype(int)}
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


SNAP_OUTLIER_MAX_FRAC = 0.05   # c4 snap judgement: at most this fraction (min 2 frames) of measured outliers dropped


def measured_speed_range(seg: Segment, fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction,
                         feasible_range: Callable | None = None) -> tuple[tuple[float, float] | None, list[int]]:
    """Speed range that reproduces refine's MEASURED frames of a segment (``_measured_constraints``), and the
    frames dropped to get it. When the measured frames are jointly infeasible, isolated argmax errors are
    removed first -- the largest subset consistent with one line at the segment's own speed
    (pipeline.max_consistent_subset), provided it drops at most max(2, 5 %) of the frames (segment.py tolerates
    such frames as drops). (None, dropped) when still undecidable; never falls back to the soft ranges."""
    if feasible_range is None:
        feasible_range = _phase().feasible_speed_range
    mk, mlo, mhi = _measured_constraints(seg, fm)
    if len(mk) < 2:
        return None, []
    mvr = feasible_range(mk, mlo, mhi, seg.comp_in, comp_fps, raw_fps)
    if mvr is not None:
        return (float(mvr[0]), float(mvr[1])), []
    v = float(seg.speed or 0.0)
    if not (v > 0 and math.isfinite(v)):
        return None, []
    from .pipeline import max_consistent_subset
    u = v * float(raw_fps) / float(comp_fps)
    keep = np.asarray(max_consistent_subset(mk, mlo, mhi, int(seg.comp_in), u), bool)
    dropped = [int(k) for k in mk[~keep]]
    if int(keep.sum()) < 2 or not dropped or len(dropped) > max(2, int(SNAP_OUTLIER_MAX_FRAC * len(mk))):
        return None, dropped
    mvr = feasible_range(mk[keep], mlo[keep], mhi[keep], seg.comp_in, comp_fps, raw_fps)
    return (None if mvr is None else (float(mvr[0]), float(mvr[1]))), dropped


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
    min_samples = int(getattr(cfg, "verify_framing_min_samples", 0) or 0)
    all_max = int(getattr(cfg, "verify_framing_all_max", 0) or 0)
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
                    # ranges admit 1.05 for a genuine 1.03x segment that segment.py rightly left unsnapped.
                    # Isolated argmax errors (which segment.py tolerates as drops) are removed first; the soft
                    # range is never used for this judgement (review R2-4)
                    mvr, dropped = measured_speed_range(s, fm, comp_fps, raw_fps, feasible_range)
                    row["snap_range_measured"] = None if mvr is None else [round(float(mvr[0]), 6),
                                                                           round(float(mvr[1]), 6)]
                    if dropped:
                        row["snap_outliers_dropped"] = _ranges(dropped)
                    if mvr is None:
                        exceptions.append(f"{name}: snap not decidable from the measured frames (unsnapped speed "
                                          f"{s.speed:.5f}; refine's measured frames admit no single linear time map)")
                        row["snap_check"] = "undecidable"
                    else:
                        slo, shi = float(mvr[0]), float(mvr[1])
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
                                       flip_margin, min_samples, all_max)
            row["independent"] = ind
            if ind["n_measured"] and ind["n_bad"] > FRAMING_BAD_FRAC * ind["n_measured"]:
                failures.append(f"{name}: independently measured framing differs from the segment model on "
                                f"{ind['n_bad']}/{ind['n_measured']} sampled frames {ind['bad'][:5]} (max scale err "
                                f"{ind['max_scale_err']:.2%}, pos {ind['max_pos_err_px']:.2f} px, tolerance "
                                f"{scale_tol:.0%} / {pos_tol:g} px)")
            elif ind["n_bad"]:
                exceptions.append(f"{name}: independently measured framing off on {ind['n_bad']}/{ind['n_measured']} "
                                  f"sampled frames {ind['bad'][:5]}")
            if ind["flip"] == "wrong":
                failures.append(f"{name}: flip_h={s.flip_h} but the mirrored hypothesis scores higher "
                                f"(median own - mirrored {ind['flip_median_diff']:+.4f})")
            elif ind["flip"] == "undecidable":
                exceptions.append(f"{name}: flip not decidable from the pixels (median own - mirrored "
                                  f"{ind['flip_median_diff']:+.4f}, symmetric content?)")
        rows.append(row)
    # unconverged samples (ECC from the model, the +-perturbed and the global starts found nothing better and
    # did not come back to the model): an exception only while the model itself scores like its neighbours;
    # a model below verify_zncc, or whose gradient-domain score falls below the neighbouring segments' median
    # by more than max(3 x their noise, verify_low_score_margin), fails (dark / low-texture misframing)
    _judge_unconverged(rows, cfg, failures, exceptions)
    status = _status_from(len(failures), len(exceptions))
    summary = f"{len(rows)} raw segments: {len(failures)} problems, {len(exceptions)} exceptions"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "segments": rows}


def _judge_unconverged(rows: list[dict], cfg: Any, failures: list[str], exceptions: list[str]) -> None:
    from . import scoring
    thr = float(getattr(cfg, "verify_zncc", 0.9))
    floor_margin = float(getattr(cfg, "verify_low_score_margin", 0.02))
    dmin, dmax = float(getattr(cfg, "soft_delta_min", 0.001)), float(getattr(cfg, "soft_delta_max", 0.01))
    inds = [(r, r.get("independent")) for r in rows]
    for i, (row, ind) in enumerate(inds):
        if not ind:
            continue
        name = f"S{int(row['id']):02d}"
        if not ind.get("unconverged_samples"):
            if ind.get("n_samples") and not ind.get("n_measured"):
                exceptions.append(f"{name}: framing could not be measured independently (no usable ECC result on "
                                  f"{ind['n_samples']} sampled frames)")
            continue
        ref = [float(x["z_grad_model"]) for jj in (i - 1, i, i + 1) if 0 <= jj < len(inds) and inds[jj][1]
               for x in inds[jj][1].get("samples", []) if x.get("cls") == "ok" and x.get("z_grad_model") is not None]
        med = float(np.median(ref)) if len(ref) >= 3 else None
        margin = max(3.0 * scoring.noise_delta(ref, dmin, dmax), floor_margin) if ref else floor_margin
        low = []
        for x in ind["unconverged_samples"]:
            zm, zg = x.get("z_model"), x.get("z_grad_model")
            why = None
            if zm is not None and zm < thr:
                why = f"model ZNCC {zm:.3f} < {thr}"
            elif med is not None and zg is not None and zg < med - margin:
                why = f"model gradient ZNCC {zg:.3f} < neighbours' median {med:.3f} - {margin:.3f}"
            if why:
                low.append(f"{x['k']} ({why})")
        unc = [x["k"] for x in ind["unconverged_samples"]]
        ind["unconverged_low_score"] = low[:20]
        if low:
            failures.append(f"{name}: framing could not be measured independently on frames {_ranges(unc)[:5]} and the "
                            f"model scores low there: " + "; ".join(low[:3]))
        elif not ind["n_measured"]:
            exceptions.append(f"{name}: framing could not be measured independently (ECC did not converge on "
                              f"{ind['n_samples']} sampled frames)")
        else:
            exceptions.append(f"{name}: framing not measurable on {len(unc)} of {ind['n_samples']} sampled frames "
                              f"{_ranges(unc)[:5]} (model scores like its neighbours)")


def _independent_framing(seg: Segment, ks: Sequence[int], raw_wh: tuple[float, float], centre: np.ndarray,
                         measure: Callable[[Segment, int, Sim], dict | None], step: int, scale_tol: float,
                         pos_tol: float, rot_tol: float, flip_margin: float, min_samples: int = 0,
                         all_max: int = 0) -> dict:
    samples = framing_samples(seg, ks, step, min_samples, all_max)
    bad, unconv, diffs, recs = [], [], [], []
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
        zm = float(r.get("z_model", float("nan")))
        zg = r.get("z_grad_model")
        rec = {"k": int(k), "z_model": _r6(zm), "z_grad_model": None if zg is None else _r6(float(zg))}
        recs.append(rec)
        meas = r.get("sim")
        if meas is None:
            rec["cls"] = "unconverged"
            unconv.append(rec)
            continue
        es, ep, er = _sim_errors(model, meas, centre)
        z = float(r.get("z", float("nan")))
        rec.update(z=_r6(z), scale_err=round(es, 6), pos_err_px=round(ep, 3), rot_err_deg=round(er, 4))
        if es <= scale_tol and ep <= pos_tol and er <= rot_tol:
            n_meas += 1
            rec["cls"] = "ok"
            ws, wp = max(ws, es), max(wp, ep)
            continue
        if math.isfinite(z) and (not math.isfinite(zm) or z > zm + 1e-4):
            n_meas += 1
            rec["cls"] = "bad"
            bad.append(int(k))
            ws, wp = max(ws, es), max(wp, ep)
        else:
            rec["cls"] = "unconverged"
            unconv.append(rec)
    med = float(np.median(diffs)) if diffs else float("nan")
    flip = "n/a" if not diffs else ("ok" if med > flip_margin else ("wrong" if med < -flip_margin else "undecidable"))
    return {"n_samples": len(samples), "n_measured": n_meas, "n_bad": len(bad), "bad": bad[:50],
            "unconverged": [x["k"] for x in unconv][:50], "unconverged_samples": unconv[:50],
            "max_scale_err": round(ws, 6), "max_pos_err_px": round(wp, 3),
            "flip": flip, "flip_median_diff": None if not diffs else round(med, 5), "flip_samples": len(diffs),
            "samples": recs[:200]}


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


def _slice0(y: np.ndarray, a: int, b: int) -> np.ndarray:
    """y[a:b] for a possibly negative start (zero-filled before sample 0; truncated at the end like a slice)."""
    if a >= 0:
        return y[a:b]
    head = np.zeros(min(-a, max(0, b - a)), y.dtype)
    return np.concatenate([head, y[0:max(0, b)]])


def audio_offset_expectation(audio_block: dict | None, comp_fps: Fraction) -> dict:
    """What c5 expects of the recreated audio given the published A/V offset (DESIGN §7 D9).

    g = cutlist.audio.av_offset.lag_ms when measured (xcorr convention: g < 0 = the competitor's audio is
    late), else 0. The recreation carries g_M = g in competitor sync and 0 in raw sync (RAW lip-sync), so
    its expected lag against the competitor is E = g - g_M. The competitor switches to a segment's audio at
    its range + the switch baseline b (published; unknown -> anywhere between 0 and -g), the recreation at
    its range + b_M (competitor sync: b rounded to whole frames, like the AE twins); after the expected lag
    the comparison keeps only [a0 + shift_lo, a1 + shift_hi) where both surely play the segment."""
    av = (audio_block or {}).get("av_offset") or {}
    measured = av.get("status") == "measured" and av.get("lag_ms") is not None
    g = float(av["lag_ms"]) / 1000.0 if measured else 0.0
    mode = str(av.get("sync_mode") or "raw")
    g_m = g if mode == "competitor" else 0.0
    e = g - g_m
    b = av.get("switch_baseline_ms")
    b = None if b is None else float(b) / 1000.0
    fps = float(Fraction(comp_fps))
    b_m = 0.0
    if mode == "competitor" and b is not None:
        b_m = math.copysign(math.floor(abs(b * fps) + 0.5), b) / fps
    b_lo, b_hi = (b, b) if b is not None else (min(0.0, -g), max(0.0, -g))
    s_m = b_m - e
    iv = av.get("lag_ms_interval")
    return {"measured": measured, "g": g, "mode": mode, "g_m": g_m, "expected": e, "baseline": b,
            "shift_lo": max(b_hi, s_m), "shift_hi": min(b_lo, s_m),
            "width_ms": (float(iv[1]) - float(iv[0])) if measured and iv else 0.0, "text": av.get("text") or ""}


def check_audio(segments: Sequence[Segment], comp_y: np.ndarray, rec_y: np.ndarray, sr: int, comp_fps: Fraction,
                audio_block: dict, added_audio: list[dict], cfg: Any, xcorr: Callable | None = None) -> dict:
    """Per segment, cross-correlation lag of the recreated audio vs the competitor's within ±tol, or an
    explanation from the closed list (too_short, not_in_raw, audio_replaced, pitch_preserved,
    music_dominated, no_audio; run-level av_offset) -> pass_with_exceptions. A confident correlation
    (>= 0.8) with a lag out of tolerance is a failure whatever the code; an unknown code is a failure.
    A segment whose audio follows an audio line (FX-14, ``audio['line']``: the recreation plays that line there,
    also under a placeholder) is measured like any other -- never exempted as not_in_raw.

    Explanations come from the analysis (the segment's audio_align code, pitch analysis, the run-level
    audio status), never from this check: music_dominated is accepted only as the segment's own code.
    A weak peak is re-searched over ±AUDIO_WIDE_LAG_S; a clearly stronger peak at another lag (>= strong,
    or >= twice the ±100 ms peak and >= min_corr) is a gross misalignment and fails whatever the code.
    A weak peak on a segment the analysis found aligned (no code) fails. An inverted audio range
    (a1 <= a0) fails.

    A/V offset (DESIGN §7 D9): with a measured cutlist.audio.av_offset every lag is searched around the
    expected lag (``audio_offset_expectation``: the offset in raw sync, 0 in competitor sync) and judged as
    the residual. This check re-estimates the offset itself -- the median measured lag (+ the offset the
    recreation already carries) of the confidently correlated segments -- and it must agree with the
    published value within (its interval width + 1 ms), else 'A/V offset not confirmed' fails; in raw sync a
    confirmed offset is ONE run-level explained exception 'av_offset'.

    Segments shorter than cfg.verify_audio_min_s are checked as maximal runs of consecutive short pieces
    whose union is long enough: the run must correlate >= strong with its residual within ±tol (searched
    within ±cfg.verify_audio_run_search_ms), and a piece that does not follow the run (its own correlation
    < min_corr while the rest of the run correlates >= strong and both its signals carry >= 1/4 of the
    run's mean power) fails. A short piece no run covers stays 'too_short' (inconclusive)."""
    if xcorr is None:
        from . import audio_align
        xcorr = audio_align.xcorr_lag
    tol = float(getattr(cfg, "audio_lag_tol_ms", 10.0))
    min_corr = float(getattr(cfg, "verify_audio_min_corr", 0.3))
    strong = float(getattr(cfg, "verify_audio_strong_corr", 0.8))
    min_dur = float(getattr(cfg, "verify_audio_min_s", 0.5))
    run_search = float(getattr(cfg, "verify_audio_run_search_ms", 20.0)) / 1000.0
    fps = float(Fraction(comp_fps))
    comp_y = np.asarray(comp_y if comp_y is not None else np.zeros(0), np.float32).reshape(-1)
    rec_y = np.asarray(rec_y if rec_y is not None else np.zeros(0), np.float32).reshape(-1)
    status_run = (audio_block or {}).get("status")
    ex = audio_offset_expectation(audio_block, comp_fps)
    E = ex["expected"]
    e_smp = int(round(E * sr))

    def window(a0: int, a1: int) -> tuple[int, int]:
        return max(0, int(round((a0 / fps + ex["shift_lo"]) * sr))), int(round((a1 / fps + ex["shift_hi"]) * sr))

    def pair(s0: int, s1: int, extra: int = 0) -> tuple[np.ndarray, np.ndarray]:
        a, b = comp_y[s0:s1], _slice0(rec_y, s0 + e_smp + extra, s1 + e_smp + extra)
        n = min(len(a), len(b))
        return a[:n], b[:n]

    rows, failures, exceptions = [], [], []
    shorts: list[tuple[int, Segment, dict]] = []       # (position, segment, row) of short pieces, checked as runs
    for pos, s in enumerate(sorted(segments, key=lambda s: (s.comp_in, s.id))):
        au = s.audio or {}
        code_in = au.get("exception")
        row: dict[str, Any] = {"id": s.id, "type": s.type}
        name = _seg_name(s)
        if s.type == "uncertain":
            # no RAW timing is claimed, so no audio is (FX-08): listed; its frames fail criterion 3 instead
            row.update(result="uncertain", code=AUDIO_UNCERTAIN_CODE)
            rows.append(row)
            continue
        if code_in is not None and (code_in not in AUDIO_EXCEPTION_CODES or code_in in AUDIO_RUN_EXCEPTION_CODES):
            failures.append(f"{name}: audio exception code {code_in!r} is not in the closed list"
                            + (" of segment codes (run-level code)" if code_in in AUDIO_RUN_EXCEPTION_CODES else ""))
            row.update(result="fail", code=code_in)
            rows.append(row)
            continue
        if s.type in ("dip", "flash"):
            row.update(result="n/a")
            rows.append(row)
            continue
        if s.type == "not_in_raw" and not au.get("line"):
            row.update(result="exception", code="not_in_raw")
            exceptions.append(f"{name}: not_in_raw")
            rows.append(row)
            continue
        if au.get("line"):
            # its audio follows an audio line (FX-14): the recreation plays that line here, measured like any other
            row["audio_line"] = au["line"].get("id")
        a0 = int(s.comp_in) + int(au.get("in_offset_frames") or 0)
        a1 = int(s.comp_out) + int(au.get("out_offset_frames") or 0)
        dur = (a1 - a0) / fps
        s0, s1 = window(a0, a1)
        row.update(audio_range=[a0, a1], duration_s=round(dur, 4))
        if a1 <= a0:
            row["result"] = "fail"
            failures.append(f"{name}: inverted audio range [{a0}, {a1}) (J/L offsets {au.get('in_offset_frames')}/"
                            f"{au.get('out_offset_frames')})")
            rows.append(row)
            continue
        if comp_y.size == 0 or rec_y.size == 0 or status_run == "no_audio":
            row.update(result="exception", code="no_audio")
            exceptions.append(f"{name}: no_audio")
            rows.append(row)
            continue
        a, b = pair(s0, s1)
        n = len(a)
        if dur < min_dur or n < int(0.25 * sr):
            if code_in not in (None, "too_short"):
                # the analysis already explains this piece (pitch / music / replaced audio): not a run member
                row.update(result="exception", code=code_in, evidence="segment audio analysis")
                exceptions.append(f"{name}: {code_in} ({dur:.2f} s)")
            else:
                shorts.append((pos, s, row))
            rows.append(row)
            continue
        if float(np.sqrt(np.mean(b.astype(np.float64) ** 2))) < 1e-5 \
                or float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) < 1e-5:
            row.update(result="exception", code="no_audio", reason="silent")
            exceptions.append(f"{name}: no_audio (silent)")
            rows.append(row)
            continue
        lag_s, peak = xcorr(a, b, sr, 0.1)
        lag_s, peak = float(lag_s) + e_smp / sr, float(peak)
        res_ms = (lag_s - E) * 1000.0
        lag_ms = lag_s * 1000.0
        row.update(lag_ms=round(lag_ms, 3), residual_ms=round(res_ms, 3), corr=round(peak, 4))
        what = f"lag {lag_ms:+.2f} ms" if not E else f"residual {res_ms:+.2f} ms after the expected {E * 1000.0:+.1f} ms"
        if peak >= min_corr and abs(res_ms) <= tol:
            row["result"] = "ok"
            rows.append(row)
            continue
        if peak >= strong and abs(res_ms) > tol:
            failures.append(f"{name}: audio confidently misaligned ({what}, corr {peak:.2f})")
            row["result"] = "fail"
            rows.append(row)
            continue
        if peak < strong:
            wlag_s, wpeak = xcorr(a, b, sr, AUDIO_WIDE_LAG_S)
            wres_ms, wpeak = (float(wlag_s) + e_smp / sr - E) * 1000.0, float(wpeak)
            row.update(wide_lag_ms=round(wres_ms + E * 1000.0, 3), wide_corr=round(wpeak, 4))
            if abs(wres_ms) > tol and (wpeak >= strong or (wpeak >= 2.0 * max(peak, 0.0) and wpeak >= min_corr)):
                failures.append(f"{name}: audio misaligned by {wres_ms:+.1f} ms (corr {wpeak:.2f} there vs {peak:.2f} "
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
            exceptions.append(f"{name}: {code} ({what}, corr {peak:.2f})")
        else:
            row["result"] = "fail"
            hint = " (low correlation under detected added audio, but the segment analysis did not report music " \
                   "dominance)" if peak < min_corr and _overlaps(a0, a1, added_audio) else ""
            failures.append(f"{name}: audio {what} / corr {peak:.2f} outside ±{tol} ms and unexplained{hint}")
        rows.append(row)

    # ---- short pieces: maximal runs of consecutive short segments, checked together -----------------
    runs: list[list[tuple[int, Segment, dict]]] = []
    for item in shorts:
        prev = runs[-1][-1] if runs else None
        if prev is not None and item[0] == prev[0] + 1 and item[2]["audio_range"][0] <= prev[2]["audio_range"][1]:
            runs[-1].append(item)
        else:
            runs.append([item])
    for run in runs:
        names = f"{_seg_name(run[0][1])}-{_seg_name(run[-1][1])}" if len(run) > 1 else _seg_name(run[0][1])
        union = (run[-1][2]["audio_range"][1] - run[0][2]["audio_range"][0]) / fps
        res = _check_short_run(run, union, min_dur, pair, window, xcorr, sr, e_smp, E, tol, min_corr, strong, run_search)
        for (_pos, s, row), piece in zip(run, res["pieces"]):
            name = _seg_name(s)
            row.update({k: v for k, v in piece.items() if k != "result"}, run=names)
            if piece["result"] == "ok":
                row["result"] = "ok"
            elif piece["result"] == "fail":
                row["result"] = "fail"
                failures.append(f"{name}: {piece['why']}")
            else:
                row.update(result="exception", code="too_short")
                exceptions.append(f"{name}: too_short ({row['duration_s']:.2f} s{'; ' + piece['why'] if piece.get('why') else ''})")

    # ---- the run's A/V offset, re-measured here (DESIGN §7 D9) -------------------------------------
    off: dict[str, Any] = {"published_ms": round(ex["g"] * 1000.0, 3) if ex["measured"] else None, "mode": ex["mode"],
                           "expected_lag_ms": round(E * 1000.0, 3)}
    conf_rows = [r for r in rows if "residual_ms" in r and "run" not in r and r.get("corr", 0.0) >= strong]
    if conf_rows:
        own = float(np.median([r["lag_ms"] for r in conf_rows])) + ex["g_m"] * 1000.0
        off.update(verified_ms=round(own, 3), n=len(conf_rows),
                   residual_spread_ms=round(float(np.ptp([r["residual_ms"] for r in conf_rows])), 3))
    if ex["measured"]:
        lim = ex["width_ms"] + 1.0
        agree = bool(conf_rows) and abs(off["verified_ms"] - ex["g"] * 1000.0) <= lim
        off.update(tolerance_ms=round(lim, 3), confirmed=agree)
        if not agree:
            failures.append("A/V offset not confirmed: the analysis published "
                            f"{ex['g'] * 1000.0:+.1f} ms, " + (f"this check measures {off['verified_ms']:+.1f} ms over "
                                                              f"{off['n']} segment(s) (tolerance ±{lim:.1f} ms)"
                                                              if conf_rows else "and no segment correlates confidently here"))
        elif ex["mode"] == "raw":
            exceptions.append(f"av_offset (run): {ex['text']} (published {ex['g'] * 1000.0:+.1f} ms, measured here "
                              f"{off['verified_ms']:+.1f} ms over {off['n']} segment(s)); the recreation keeps RAW lip-sync")
    measured = [r for r in rows if "lag_ms" in r]
    status = _status_from(len(failures), len(exceptions))
    lags = [abs(r["residual_ms"]) for r in measured if r.get("result") == "ok"]
    word = "residual" if E else "lag"
    summary = (f"{len(measured)} segments measured, max |{word}| {max(lags):.2f} ms" if lags else f"{len(measured)} segments measured") \
        + f", {len(exceptions)} explained exceptions, {len(failures)} failures"
    if ex["measured"]:
        summary += (f"; A/V offset {ex['g'] * 1000.0:+.1f} ms ({ex['mode']} sync) "
                    + ("confirmed" if off.get("confirmed") else "NOT confirmed"))
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "segments": rows,
            "tolerance_ms": tol, "av_offset": off}


def _check_short_run(run: list, union_s: float, min_dur: float, pair: Callable, window: Callable, xcorr: Callable,
                     sr: int, e_smp: int, E: float, tol: float, min_corr: float, strong: float, search_s: float) -> dict:
    """c5 for one maximal run of consecutive short pieces (see check_audio): their comparison windows are
    concatenated and correlated around the expected lag; a confident run then checks every piece at the run's
    alignment against the rest of the run (leave-one-out). Returns {'pieces': [{result: ok | fail |
    inconclusive, why, lag_ms, residual_ms, corr, piece_corr, rest_corr}]}."""
    n_p = len(run)
    if union_s < min_dur:
        why = f"run of {n_p} short pieces is only {union_s:.2f} s" if n_p > 1 else ""
        return {"pieces": [{"result": "inconclusive", "why": why} for _ in range(n_p)]}
    spans = []
    for _pos, _s, row in run:
        s0, s1 = window(*row["audio_range"])
        spans.append((s0, max(s0, s1)))
    parts = [pair(s0, s1) for s0, s1 in spans]
    cat_c = np.concatenate([a for a, _ in parts])
    cat_r = np.concatenate([b for _, b in parts])
    if cat_c.size < int(0.25 * sr) or float(np.sqrt(np.mean(cat_c.astype(np.float64) ** 2))) < 1e-5 \
            or float(np.sqrt(np.mean(cat_r.astype(np.float64) ** 2))) < 1e-5:
        return {"pieces": [{"result": "inconclusive", "why": "aggregated run has too little audible audio"}
                           for _ in range(n_p)]}
    lag_m, peak = xcorr(cat_c, cat_r, sr, search_s)
    lag_m, peak = float(lag_m), float(peak)
    lag_s = lag_m + e_smp / sr
    res_ms = (lag_s - E) * 1000.0
    base = {"lag_ms": round(lag_s * 1000.0, 3), "residual_ms": round(res_ms, 3), "corr": round(peak, 4)}
    if peak < strong:
        return {"pieces": [dict(base, result="inconclusive", why=f"aggregated run corr {peak:.2f} < {strong:g}")
                           for _ in range(n_p)]}
    if abs(res_ms) > tol:
        return {"pieces": [dict(base, result="fail", why=f"audio of the aggregated run confidently misaligned (residual "
                                                         f"{res_ms:+.2f} ms, corr {peak:.2f})") for _ in range(n_p)]}
    # leave-one-out: every piece at the run's alignment (b(t) ~ a(t - lag): compare a(t) with b(t + lag))
    li = int(round(lag_m * sr))
    st = []
    for s0, s1 in spans:
        a, b = pair(s0, s1, li)
        a, b = a.astype(np.float64), b.astype(np.float64)
        st.append((float(np.dot(a, b)), float(np.dot(a, a)), float(np.dot(b, b)), len(a)))
    num, eca, ecb, cnt = (sum(x[i] for x in st) for i in range(4))
    pa, pb = eca / max(cnt, 1), ecb / max(cnt, 1)
    pieces = []
    for nm, ea, eb, n in st:
        own = nm / math.sqrt(ea * eb) if ea > 0 and eb > 0 else 0.0
        da, db = eca - ea, ecb - eb
        rest = (num - nm) / math.sqrt(da * db) if da > 0 and db > 0 else 0.0
        loud = n > 0 and ea / n >= 0.25 * pa and eb / n >= 0.25 * pb
        p = dict(base, piece_corr=round(own, 4), rest_corr=round(rest, 4), result="ok", why="")
        if own < min_corr and rest >= strong and loud:
            p.update(result="fail", why=f"audio does not follow its aggregated run (piece corr {own:.2f}, the rest of the "
                                        f"run {rest:.2f} at residual {res_ms:+.2f} ms)")
        pieces.append(p)
    return {"pieces": pieces}


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
                out = set()
                for L, j, _w in fs(int(k), self.rctx):
                    if j is None or not 0 <= int(j) < self.n_raw:
                        continue
                    out.add(int(j))
                    if getattr(L, "mix", False) and int(j) + 1 < self.n_raw:     # Frame Mix: RAW j + 1 too (FX-08)
                        out.add(int(j) + 1)
                return out
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


def placeholder_gray(name: str = "PLACEHOLDER_RGB") -> float | None:
    """Luma (0..255) of the NOT-IN-RAW placeholder solid (export_ae.PLACEHOLDER_RGB; UNCERTAIN_RGB for the
    uncertain solid)."""
    try:
        from . import export_ae
        rgb = getattr(export_ae, name)
    except (ImportError, AttributeError):  # pragma: no cover
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
                 segments: Sequence[Segment] | None = None,
                 overlay_fn: Callable[[int], np.ndarray | None] | None = None,
                 overlay_lines: Sequence[str] = ()) -> dict:
    """Masked ZNCC competitor vs match-geometry recreation on every frame (video region of THAT frame --
    ``box_fn(k)``, e.g. the whole canvas in a full-screen period --, static and overlay pixels excluded,
    cfg.score_blur). Every matched frame must reach cfg.verify_zncc; every crossfade (BLEND) frame too
    (cfg.verify_blend_zncc when set). UNIFORM frames (dips / flashes): the recreation's region is uniform
    with the competitor's mean luma. NONE frames of a NOT-IN-RAW segment (``segments``): the recreation
    shows the placeholder solid.

    ``overlay_fn(k)`` (wave 4): the MEASURED RAW-only overlays (``find_raw_only_overlays``) at frame k, excluded
    too; a matched frame that reaches the threshold only with them excluded is listed as explained
    (``overlay_lines`` say what and where) -- it must still reach the threshold on everything else."""
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
    unc = np.zeros(n, bool)
    for s in segments or []:
        if s.type == "not_in_raw":
            nir[max(0, s.comp_in):min(n, s.comp_out)] = True
        if s.type == "uncertain":
            unc[max(0, s.comp_in):min(n, s.comp_out)] = True
    want_ph = placeholder_gray() if nir.any() else None
    want_unc = placeholder_gray("UNCERTAIN_RGB") if unc.any() else None
    n_img = 0
    n_uniform = n_ph = 0
    explained: list[int] = []

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
        if (st == Status.NONE and nir[k]) or unc[k]:
            n_ph += 1
            ok, why = _is_placeholder(rec[y:y + h, x:x + w], want_unc if unc[k] else want_ph)
            if not ok:
                ph_fail.append({"k": int(k), "why": why, "segment": "uncertain" if unc[k] else "not_in_raw"})
                image(k, c, rec, roi, float("nan"))
            continue
        cb, rb = _blur(c[y:y + h, x:x + w], blur), _blur(rec[y:y + h, x:x + w], blur)
        ov = overlay_fn(k) if overlay_fn is not None else None
        if ov is not None:
            mo = m & ~np.asarray(ov, bool)[y:y + h, x:x + w]
            s_full = scoring.zncc(cb, rb, m)
            s = scoring.zncc(cb, rb, mo)
            if st == Status.MATCH and s >= thr and not (s_full >= thr):
                explained.append(int(k))
        else:
            s = scoring.zncc(cb, rb, m)
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
    matched = (status[:n] == Status.MATCH) & ~unc
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
        failures.append(f"{len(ph_fail)} NOT-IN-RAW / UNCERTAIN frames do not show their labelled solid: {ph_fail[:3]}")
    exceptions = [f"{len(nan_fail)} matched frames unscorable (too few visible pixels): {_ranges(nan_fail)[:10]}"] if nan_fail else []
    if explained:
        exceptions.append(f"{len(explained)} matched frames {_ranges(explained)[:10]} reach ZNCC {thr} only with the "
                          "measured RAW-only overlay(s) excluded: " + "; ".join(overlay_lines or ["RAW-only overlay"]))
    status_out = _status_from(len(failures), len(exceptions))
    summary = (f"{int(matched.sum())} matched frames, min ZNCC {dist.get('min', float('nan'))}, median "
               f"{dist.get('median', float('nan'))}, {len(real_fail)} below {thr}; {int(blend.sum())} blend frames"
               f"{' (min ' + str(round(float(bs.min()), 5)) + ')' if bs.size else ''}, {n_uniform} uniform, "
               f"{n_ph} placeholder frames checked"
               + (f"; {len(explained)} frames explained by RAW-only overlay(s)" if explained else "")
               + (f" [{source}]" if source else ""))
    return {"status": status_out, "summary": summary, "failures": failures, "exceptions": exceptions, "threshold": thr,
            "raw_only_overlay_frames": explained[:1000], "raw_only_overlays": list(overlay_lines),
            "distribution": dist, "failed_frames": [int(k) for k in real_fail[:1000]],
            "blend_frames_min": round(float(bs.min()), 5) if bs.size else None, "blend_threshold": blend_thr,
            "blend_failed_frames": blend_fail[:1000], "uniform_frames_checked": n_uniform,
            "uniform_failed": uniform_fail[:200], "placeholder_frames_checked": n_ph, "placeholder_failed": ph_fail[:200],
            "source": source, "scores_file": None, "scores": scores}


# ---------------------------------------------------------------------------------------------
# s9_4 cut images
# ---------------------------------------------------------------------------------------------

def check_uncertain(segments: Sequence[Segment]) -> dict:
    """Criterion 3 accounting of 'uncertain' segments (FX-08): their frames are neither mapped to a RAW frame nor
    NOT-IN-RAW, so they are FAILURES of criterion 3 ('uncertain' class) -- never exceptions, never placeholders."""
    rows = [{"id": s.id, "comp_in": int(s.comp_in), "comp_out": int(s.comp_out), "label": s.label}
            for s in segments if s.type == "uncertain"]
    n = sum(r["comp_out"] - r["comp_in"] for r in rows)
    failures = [f"{n} frames in {len(rows)} UNCERTAIN segment(s) (neither matched nor NOT-IN-RAW): "
                + "; ".join(f"S{r['id']:02d} {r['comp_in']}-{r['comp_out'] - 1} {r['label']}" for r in rows[:8])] \
        if rows else []
    return {"status": "fail" if rows else "pass", "summary": f"{n} uncertain frames in {len(rows)} segment(s)",
            "failures": failures, "segments": rows, "frames": n}


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


def _plan_stretch_slack(plan: dict | None) -> dict[int, float]:
    """MAIN frame -> the smallest floor-rule slack (RAW frames) of the stretch-mode RAW video layers the plan
    shows there: |raw_fps * (t_K - startTime) * 100 / stretch| to the nearest integer."""
    out: dict[int, float] = {}
    if not plan:
        return out
    F, R = plan["main"]["fps"], plan["rawFps"]
    rf = R["num"] / R["den"]
    for L in plan.get("layers", []):
        if L.get("kind") != "raw" or L.get("timeMode") != "stretch" or L.get("stretch") is None:
            continue
        for K in range(int(L["compIn"]), int(L["compOut"])):
            p = (K * F["den"] / F["num"] - float(L["startStretch"])) * (100.0 / float(L["stretch"])) * rf
            s = min(p - math.floor(p), math.floor(p) + 1 - p)
            out[K] = min(out.get(K, 1.0), s)
    return out


def ae_time_calibration(plan: dict | None, time_check: dict | None, render: dict | None, cfg: Any) -> dict:
    """s9_6 evidence on After Effects' real time resolution (FX-10): the plan's tolerance and the layers it
    exported frame-exact because their exact slack was below it; the JSX's AE source-time check
    (ae_time_check.txt of the After Effects run: frames off the plan, smallest slack AE showed, largest |AE -
    plan| residual); and, when aerender rendered MAIN, the smallest plan slack of a stretch-layer frame AE
    rendered right and the render mismatches at frames with less than 2 x the tolerance. That is what may
    later justify a smaller ae_slack_tol_frames -- never an assumption."""
    tol = float(getattr(cfg, "ae_slack_tol_frames", 0.01))
    summary = None
    if time_check is not None:
        from .export_ae import time_check_summary
        summary = time_check_summary(time_check)
    out: dict[str, Any] = {
        "slack_tol_frames": tol,
        "frame_exact_for_slack": sorted({int(d["segment"]) for d in (plan or {}).get("decisions", [])
                                         if d.get("decision") == "time_mode_slack"}),
        "stretch_layers": sum(1 for L in (plan or {}).get("layers", []) if L.get("kind") == "raw"
                              and L.get("timeMode") == "stretch"),
        "time_check": summary,
        "rendered": None,
    }
    if render is not None and "mismatches" in render:
        slack = _plan_stretch_slack(plan)
        n = int(render.get("frames_rendered") or 0)
        bad = {int(m["K"]) for m in render.get("mismatches", []) if "K" in m}
        ok = [s for K, s in slack.items() if K < n and K not in bad]
        out["rendered"] = {"stretch_frames": sum(1 for K in slack if K < n),
                           "min_slack_rendered_ok": min(ok) if ok else None,
                           "mismatches_low_slack": sorted(K for K in bad if slack.get(K, 1.0) < 2 * tol)}
    return out


def check_ae_render(env: dict, aep: str | None, preview: str | None, n_main: int, main_fps: Fraction,
                    proxy_size: tuple[int, int], out_dir: Path, cfg: Any, plan: dict | None = None,
                    time_check_path: str | os.PathLike | None = None) -> dict:
    """Render MAIN with aerender (if installed) and compare every frame with preview_recreation.mp4; plus
    ``ae_time`` (``ae_time_calibration``): After Effects' own source-time check of this run's JSX
    (``time_check_path`` = its ae_time_check.txt, when After Effects ran it) -- a frame-exact or remap layer
    AE still maps to another RAW frame fails; stretch layers the JSX already switched are recovered."""
    res = _ae_render_compare(env, aep, preview, n_main, main_fps, proxy_size, out_dir, cfg)
    rows = None
    if time_check_path is not None and Path(time_check_path).is_file():
        from .export_ae import parse_time_check
        rows = parse_time_check(Path(time_check_path).read_text(encoding="utf-8", errors="replace"))
    res["ae_time"] = ae_time_calibration(plan, rows, res, cfg)
    tc = res["ae_time"]["time_check"]
    if tc is not None:
        res["summary"] = (res.get("summary", "") + f"; AE source-time check: {tc['layers']} layer(s), {tc['frames']} "
                          f"frames, {tc['off']} off the plan" + (f", max |AE - plan| {tc['max_res']:.2e} RAW frame"
                                                                  if tc["max_res"] is not None else ""))
        if tc["layers_off"]:
            res.setdefault("failures", []).append(f"After Effects' own source time differs from the plan on "
                                                  f"{tc['layers_off'][:5]} (ae_time_check.txt)")
            res["status"] = "fail"
    return res


def _ae_time_check_path(ctx: Any) -> Path | None:
    """ae_time_check.txt next to this run's JSX when After Effects ran it in this run (written after the
    JSX; a file left by an earlier run is ignored), else None."""
    jsx = (getattr(ctx, "paths", None) or {}).get("jsx")
    if not jsx or (getattr(ctx, "ae_run", None) or {}).get("status") != "ok":
        return None
    from .export_ae import TIME_CHECK_FILE
    p = Path(jsx).parent / TIME_CHECK_FILE
    try:
        return p if p.is_file() and p.stat().st_mtime_ns >= Path(jsx).stat().st_mtime_ns else None
    except OSError:
        return None


def _ae_render_compare(env: dict, aep: str | None, preview: str | None, n_main: int, main_fps: Fraction,
                       proxy_size: tuple[int, int], out_dir: Path, cfg: Any) -> dict:
    """check_ae_render's aerender part: render MAIN and compare every frame with preview_recreation.mp4."""
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
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=6 * 3600)
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
        disk = compare_cutlists(json.loads(p.read_text(encoding="utf-8")), first)
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
    elif prev.get("changed"):
        summary += "; previous run not compared (" + ", ".join(prev["changed"]) + " changed)"
    return {"status": status, "summary": summary, "failures": failures, "differences": cmp["differences"],
            "previous_run": prev, "warnings": warnings}


# a previous run is comparable only when all of these provenance entries are equal; ffmpeg decodes the pixels
# every stage measures, so a different ffmpeg version may legitimately change results (review R2-6)
PREVIOUS_RUN_GATE_KEYS = ("version", "input_hashes", "analysis_params_hash", "stage_versions", "code_hash",
                          "ffmpeg_version")
# location-only fields of the competitor / raw media blocks: where the inputs and the output folder live, not
# what was analysed (the content is pinned by 'hash' / 'source_hash' and provenance.input_hashes)
MEDIA_LOCATION_FIELDS = ("source_path", "file_abs", "file_rel", "file")


def previous_run_canonical(d: dict) -> dict:
    """``canonical_cutlist`` minus the location-only fields (MEDIA_LOCATION_FIELDS of cutlist.competitor /
    cutlist.raw): moving / renaming the same inputs or the output folder is no reproducibility failure."""
    c = canonical_cutlist(json.loads(json.dumps(d, default=json_default)))
    for role in ("competitor", "raw"):
        blk = c.get(role)
        if isinstance(blk, dict):
            for f in MEDIA_LOCATION_FIELDS:
                blk.pop(f, None)
    return c


def compare_with_previous_run(previous: dict | None, current: dict) -> dict:
    """Compare with the previous run's cutlist.json when it was made from the same inputs, analysis
    parameters, settings, tool / stage versions and ffmpeg version (else the comparison is skipped), ignoring
    provenance.timings and the location-only media fields (``previous_run_canonical``)."""
    if not previous:
        return {"compared": False, "reason": "no previous cutlist.json"}
    pp, cp = previous.get("provenance") or {}, current.get("provenance") or {}

    def js(v: Any) -> str:
        return json.dumps(json.loads(json.dumps(v, default=json_default)), sort_keys=True)

    changed = [k for k in PREVIOUS_RUN_GATE_KEYS if js(pp.get(k)) != js(cp.get(k))]
    if js(previous.get("settings")) != js(current.get("settings")):
        changed.append("settings")
    if changed:
        return {"compared": False, "reason": "inputs, parameters, settings, tool or ffmpeg version changed",
                "changed": changed}
    cmp = compare_cutlists(previous_run_canonical(previous), previous_run_canonical(current))
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


def verify_overlays(ctx: Any) -> Any:
    """Overlay masks verification may exclude: the LAYOUT stage's own caption / text-overlay masks
    (layout.layout_overlay_masks: comp-only), never ``ctx.overlays`` -- that also holds refine's pass-2
    residual masks, computed from the match being verified (a misframed match masks its own mismatch away)."""
    from . import layout as layout_mod
    lay = getattr(ctx, "layout", None)
    comp = getattr(ctx, "comp_proxy", None)
    shape = (int(comp.size[1]), int(comp.size[0])) if comp is not None else None
    ratio = tuple(comp.ratio) if comp is not None else None
    d = int(getattr(ctx.cfg, "overlay_dilate_px", 3))
    fn = getattr(layout_mod, "layout_overlay_masks", None)
    if fn is None:                       # a layout module without the provider (test stubs): no overlay masks
        log.warning("verify: layout.layout_overlay_masks unavailable - scoring without overlay masks")
        return None
    return fn(lay, shape, ratio, d) if lay is not None else None


def _allowed_fn(ctx: Any, box_fn: Callable[[int], Box | dict | None] | None = None) -> Callable[[int], np.ndarray | None]:
    """Per-frame scoring mask: layout.allowed_mask (dominant box & ~static & ~layout overlay(k), the overlays
    of ``verify_overlays`` -- never refine's residual masks); on frames shown in a larger box (full-screen
    periods) also the pixels of that box outside the dominant box that are not covered by an active zone,
    caption or overlay (the dominant layout's static canvas shows video there)."""
    from . import layout as layout_mod
    memo: dict[int, np.ndarray] = {}
    lay = getattr(ctx, "layout", None)
    comp = ctx.comp_proxy
    dom = _box(ctx)
    dom_roi = proxy_roi(dom, comp.size, comp.ratio) if dom is not None else None
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    overlays = verify_overlays(ctx)
    d = int(getattr(overlays, "dilate_px", getattr(ctx.cfg, "overlay_dilate_px", 3)) or 0) if overlays is not None \
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
        if overlays is not None:
            try:
                ov = overlays.get_dilated(int(k), d) if hasattr(overlays, "get_dilated") else overlays.get(int(k))
            except Exception:  # noqa: BLE001 - no overlay mask for this frame
                ov = None
        if ov is not None and np.asarray(ov).shape == extra.shape:
            extra &= ~np.asarray(ov, bool)
        return base | extra

    def f(k: int) -> np.ndarray | None:
        if k not in memo:
            if len(memo) > 64:
                memo.clear()
            base = layout_mod.allowed_mask(ctx.layout, overlays, int(k), ctx.comp_proxy)
            memo[k] = widened(int(k), np.asarray(base, bool)) if base is not None else base
        return memo[k]
    return f


def _box(ctx: Any) -> Box | dict | None:
    if getattr(ctx, "layout", None) is not None and ctx.layout.box is not None:
        return ctx.layout.box
    return (ctx.cutlist.layout or {}).get("box")


def _run_check(name: str, fn: Callable[[], dict]) -> dict:
    import time
    t0 = time.perf_counter()
    try:
        res = fn()
        if not isinstance(res, dict) or res.get("status") not in STATUSES:
            raise ValueError(f"check returned an invalid result: {res!r:.200}")
        res["seconds"] = round(time.perf_counter() - t0, 2)
        log.info("verify %s: %s (%.1fs)", name, res.get("status"), res["seconds"])
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
    ROI with the layout-only allowed mask) started from the model perturbed by ±FRAMING_PERTURB and from a
    GLOBAL start (the model moved by the phase-correlation translation between the competitor ROI and the
    warped RAW frame, so a framing tens of px off is still found); never from the model itself; the best run
    that improved on its start, its score and the model's on the same pixels, the model's gradient-domain
    score (dark / low-texture frames) and the model's score with its own flip vs the mirrored hypothesis
    (same Sim, opposite flip). 'sim' is None when no start improved (unconverged)."""
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
        first = 1.0 if int(k) % 2 == 0 else -1.0          # alternate the side of the start between samples
        # never the model itself: ECC started there only confirms its own local optimum
        starts = [perturb_sim(model, centre, sign * rel, sign * px, sign * px) for sign in (first, -first)]
        g = scorer.global_init(k, (j, model, flip)) if hasattr(scorer, "global_init") else None
        if g is not None:
            starts.append(g)
        found: list[Sim] = []
        for init in starts:
            try:
                sim, z = refine.refine_transform(comp_img, raw_img, init, flip, float(raw_wh[0]), raw.ratio, comp.ratio,
                                                 allowed_fn(k), cfg, roi)
            except Exception:  # noqa: BLE001 - an ECC failure is an unmeasured start, not a crash
                continue
            if sim is init or not math.isfinite(float(z)):
                continue                        # ECC did not improve on this start
            found.append(sim)
        sc = scorer.score(k, [(j, model, flip), (j, model, not flip)] + [(j, s, flip) for s in found])
        out = {"flip_own": float(sc[0]), "flip_other": float(sc[1]), "sim": None, "z": float("nan"),
               "z_model": float(sc[0]), "starts": len(starts), "improved": len(found), "global_start": g is not None}
        zs = [float(v) for v in sc[2:]]
        if found and any(math.isfinite(v) for v in zs):
            i = int(np.nanargmax(np.asarray(zs, np.float64)))
            out.update(sim=found[i], z=zs[i])
        if hasattr(scorer, "score_grad"):
            out["z_grad_model"] = float(scorer.score_grad(k, [(j, model, flip)])[0])
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

    dom = _box(ctx)
    db = Box.from_dict(dom) if isinstance(dom, dict) else dom
    centre = (db.x + db.w / 2.0, db.y + db.h / 2.0) if db is not None else (comp_wh[0] / 2.0, comp_wh[1] / 2.0)

    # RAW-only overlays (measured, explained; masked in s9_2b / s9_2c / s9_3 -- wave 4)
    try:
        rov = find_raw_only_overlays(ctx.comp_proxy, ctx.raw_proxy, segs, ctx.fm, allowed, box_fn, raw_wh, comp_fps,
                                     raw_fps, n_raw, cfg)
    except Exception as e:  # noqa: BLE001 - a failed measurement explains nothing (never a pass)
        log.error("verify: RAW-only overlay measurement failed: %s\n%s", e, traceback.format_exc())
        rov = RawOnlyOverlays(getattr(ctx, "comp_proxy", None), getattr(ctx, "raw_proxy", None), raw_wh, comp_fps,
                              raw_fps, 0)
    rov_lines = raw_only_overlay_lines(rov.regions)
    allowed_ov = rov.allowed(allowed) if rov else allowed
    scorer_ov = None

    def get_scorer_ov() -> ProxyScorer:
        nonlocal scorer_ov
        if not rov:
            return get_scorer()
        if scorer_ov is None:
            scorer_ov = ProxyScorer(ctx.comp_proxy, ctx.raw_proxy, _box(ctx), allowed_ov, raw_wh[0], cfg, box_fn=box_fn)
        return scorer_ov

    # competitor-only temporal signature (labels) + the recreation's, shared by c2 and s9_2b
    temporal_state: dict[str, Any] = {}

    def s9_2b() -> dict:
        from . import layout as layout_mod
        zones = animated_text_zones(ctx.comp_proxy, ctx.raw_proxy, segs, getattr(ctx, "layout", None),
                                    verify_overlays(ctx), raw_wh, comp_fps, raw_fps, n_raw, cfg)
        shape = (int(ctx.comp_proxy.size[1]), int(ctx.comp_proxy.size[0]))
        d = int(getattr(cfg, "overlay_dilate_px", 3))

        def mask_out(k: int) -> np.ndarray | None:
            a = layout_mod.animated_text_mask(zones, k, shape, d) if zones else None
            o = rov.mask(k) if rov else None
            return a if o is None else (o if a is None else (a | o))
        tf = TemporalFrames(ctx.comp_proxy, ctx.raw_proxy, segs, allowed, box_fn, dom, raw_wh, comp_fps, raw_fps, n_raw, cfg,
                            mask_out=mask_out if (zones or rov) else None)
        comp_sig, labels, rec_sig = temporal_signatures(tf, cfg)
        temporal_state["labels"] = labels
        res = check_temporal(segs, labels, comp_sig, rec_sig, comp_fps, raw_fps, n_raw, n, cfg, masked=zones)
        if rov_lines:
            res["raw_only_overlays"] = rov_lines
        return res

    checks["s9_2b_temporal"] = _run_check("s9_2b_temporal", s9_2b)
    extra["cuts"] = _run_check("c2_cuts", lambda: check_cuts(segs, comp_fps, raw_fps, raw_wh, n_raw, get_scorer(), cfg,
                                                             labels=temporal_state.get("labels"), box_centre=centre))
    checks["s9_2c_refit"] = _run_check("s9_2c_refit", lambda: check_refit(segs, ctx.fm, comp_fps, raw_fps, raw_wh, n_raw,
                                                                           get_scorer_ov(), cfg, n, box_centre=centre))

    # s9_2: AE semantics from the plan and from the mock-run record
    cut_main = [pipeline.to_main_frame(k, comp_fps, main_fps) for k in cut_frames(segs)]

    def s9_2_plan() -> dict:
        from . import export_ae
        if ctx.plan is None:
            return {"status": "fail", "summary": "no AE plan", "failures": ["ae_plan missing"]}
        return check_ae_sim(export_ae.simulate_ae(ctx.plan), ctx.fm, comp_fps, main_fps, n_main, cut_main, cfg, "plan",
                            segments=segs, raw_fps=raw_fps, n_raw=n_raw, scorer=get_scorer_ov(), raw_wh=raw_wh,
                            evidence_memo=ev_memo)

    def s9_2_mock() -> dict:
        rec = (ctx.mock or {}).get("default")
        if not rec or _get(rec, "status") == "not_available":
            return {"status": "not_available", "summary": "mock run not available", "failures": []}
        if _get(rec, "status") == "error":
            return {"status": "fail", "summary": "mock run failed", "failures": [str(_get(rec, "error"))]}
        sim = simulate_record(rec, raw_name, raw_fps, main_fps, n_main)
        return check_ae_sim(sim, ctx.fm, comp_fps, main_fps, n_main, cut_main, cfg, "mock record",
                            segments=segs, raw_fps=raw_fps, n_raw=n_raw, scorer=get_scorer_ov(), raw_wh=raw_wh,
                            evidence_memo=ev_memo)

    ev_memo: dict = {}
    p2, m2 = _run_check("s9_2_plan", s9_2_plan), _run_check("s9_2_mock", s9_2_mock)
    checks["s9_2_ae_sim"] = merge_ae_sim(p2, m2)

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
                           cfg.debug_dir / "verify_failures", desc, box_fn=box_fn, segments=segs,
                           overlay_fn=rov.mask if rov else None, overlay_lines=rov_lines)
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
        Path(cfg.work) / "aerender", cfg, plan=getattr(ctx, "plan", None), time_check_path=_ae_time_check_path(ctx)))
    checks["s9_7_determinism"] = _run_check("s9_7_determinism", lambda: check_determinism(ctx))
    checks["s9_8_deliverables"] = _run_check("s9_8_deliverables",
                                             lambda: check_deliverables(ctx, n_cuts=len(cut_frames(segs))))

    exact_note = "" if main_fps == comp_fps else (
        f" (MAIN at {fps_str(main_fps)}: cuts rounded to the MAIN grid and source frames checked between the "
        "bracketing competitor frames; frame-exact only with --fps competitor)")
    c2 = {"status": aggregate([extra["cuts"]["status"], checks["s9_4_cut_images"]["status"]]),
          "summary": extra["cuts"].get("summary", "") + exact_note, "details": extra["cuts"]}
    unc = check_uncertain(segs)
    c3 = {"status": aggregate([checks["s9_2_ae_sim"]["status"], checks["s9_3_visual"]["status"],
                               checks["s9_2b_temporal"]["status"], checks["s9_2c_refit"]["status"], unc["status"]]),
          "summary": (f"AE sim: {p2.get('summary')}; visual: {checks['s9_3_visual'].get('summary')}; temporal: "
                      f"{checks['s9_2b_temporal'].get('summary')}; +-1 refit: {checks['s9_2c_refit'].get('summary')}; "
                      f"uncertain: {unc['summary']}" + exact_note),
          "details": {"s9_2": checks["s9_2_ae_sim"], "s9_3": {k: v for k, v in checks["s9_3_visual"].items()},
                      "s9_2b": checks["s9_2b_temporal"], "s9_2c": checks["s9_2c_refit"], "uncertain": unc}}
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
            "main_fps": fps_str(main_fps), "frames": n, "main_frames": n_main,
            "raw_only_overlays": {"lines": rov_lines, "regions": [{k: v for k, v in r.items() if k != "raw_rect_proxy"}
                                                                   for r in rov.regions],
                                  "rejected": rov.rejected[:50]}}


def _collect_failures(d: Any) -> list[str]:
    out: list[str] = []
    if isinstance(d, dict):
        out.extend(str(f) for f in d.get("failures", []) or [])
        for k, v in d.items():
            if k != "failures" and isinstance(v, dict) and ("status" in v or "failures" in v):
                out.extend(_collect_failures(v))
    return out
