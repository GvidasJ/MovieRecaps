"""Stage 9 verification (DESIGN.md §5 verify.py): acceptance criteria c1..c6 and checks s9_1..s9_7.

``verify_all(ctx)`` returns::

    {'criteria': {c1_coverage, c2_cuts, c3_source_frames, c4_speed_framing, c5_audio, c6_after_effects}:
                 {'status': 'pass'|'fail'|'pass_with_exceptions'|'not_available', 'summary': str,
                  'details': {...}},
     'checks':   {s9_1_coverage, s9_2_ae_sim, s9_3_visual, s9_4_cut_images, s9_5_audio, s9_6_ae_render,
                  s9_7_determinism}: {'status', 'summary', 'failures': [...], ...},
     'failures': [str, ...]}

Mapping: c1 <- s9_1; c2 <- an independent per-cut check (+ s9_4 cut images); c3 <- s9_2 (AE-semantics
simulation of the ae_plan AND of the mock-run record) + s9_3 (visual); c4 <- speed / framing / flip /
rotation; c5 <- s9_5; c6 <- mock-run checks + s9_6 (aerender, when installed).

Every check function below takes plain data (segments, FrameMap, arrays, callables) so it can be unit
tested without the analysis modules; ``verify_all`` wires them to a ``pipeline.Context``.
"""
from __future__ import annotations

import copy
import json
import math
import os
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
          "s9_7_determinism")
STATUSES = ("pass", "pass_with_exceptions", "fail", "not_available")
AUDIO_EXCEPTION_CODES = frozenset({"too_short", "not_in_raw", "audio_replaced", "pitch_preserved",
                                   "music_dominated", "no_audio"})
MAIN_COMP_NAME = "Recreated Edit"
KEY_HOLD = 6614            # KeyframeInterpolationType.HOLD enum value in AE
MAX_FAILURE_IMAGES = 200


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
    periods = [p for p in (lb.get("periods") or []) if str(p.get("mode")) in ("split", "pip")]
    if periods:
        for p in periods:
            region_frames.append([int(p["comp_in"]), int(p["comp_out"]) - 1])
    elif lb.get("regions"):
        region_frames.append([0, n - 1])
    if region_frames:
        exceptions.append(f"{len(lb.get('regions') or periods)} extra video region(s) not recreated in frames "
                          + ", ".join(f"{a}-{b}" for a, b in region_frames))
    status = _status_from(len(failures), len(exceptions))
    n_ph = sum(1 for s in segs if s.type == "not_in_raw")
    summary = (f"{len(segs)} segments ({n_ph} NOT-IN-RAW), {covered}/{n} frames covered, "
               f"{len(gaps)} gaps, {len(overlaps)} overlaps ({len(explained)} transitions)")
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions,
            "frames": n, "covered": covered, "gaps": gaps, "overlaps": overlaps, "transitions": explained,
            "extra_region_frames": region_frames}


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
    the scores are directly comparable (DESIGN §5 scoring)."""

    def __init__(self, comp: Any, raw: Any, box: Box | dict | None, allowed_fn: Callable[[int], np.ndarray | None],
                 raw_w: float, cfg: Any, min_pixels: int = 256):
        self.comp, self.raw, self.cfg = comp, raw, cfg
        self.roi = proxy_roi(box, comp.size, comp.ratio)
        self.allowed_fn = allowed_fn
        self.raw_w = float(raw_w)
        self.blur = float(getattr(cfg, "score_blur", 1.0))
        self.grad_weight = float(getattr(cfg, "grad_weight", 0.0))
        self.min_pixels = int(min_pixels)

    def _region(self, k: int):
        from . import scoring
        if not (0 <= k < self.comp.n) or not self.comp.has(k):
            return None
        return scoring.prepare_comp(np.asarray(self.comp.get(k)), self.roi, self.allowed_fn(k), blur=self.blur,
                                    with_grad=self.grad_weight > 0)

    def _warp(self, cand: Cand | None):
        from . import scoring
        if cand is None:
            return None
        j, sim, flip = cand
        if j is None or sim is None or not self.raw.has(int(j)):
            return None
        w, v = scoring.warp_to_roi(np.asarray(self.raw.get(int(j))), sim, bool(flip), self.raw_w, self.raw.ratio,
                                   self.comp.ratio, self.roi)
        return _blur(w, self.blur), v

    def score(self, k: int, cands: Sequence[Cand | None]) -> np.ndarray:
        from . import scoring
        out = np.full(len(cands), np.nan)
        region = self._region(k)
        if region is None:
            return out
        warped = [self._warp(c) for c in cands]
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
        wa, wb = self._warp(a), self._warp(b)
        if region is None or wa is None or wb is None:
            return float("nan"), float("nan")
        valid = region.mask & wa[1] & wb[1]
        alpha_a, _res, z = scoring.fit_blend(region, wa[0], wb[0], valid)
        return 1.0 - alpha_a, z

    def uniform(self, k: int) -> tuple[float, float]:
        from . import scoring
        if not (0 <= k < self.comp.n):
            return float("nan"), float("nan")
        return scoring.region_stats(np.asarray(self.comp.get(k)), self.roi, self.allowed_fn(k))


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


def check_cuts(segments: Sequence[Segment], comp_fps: Fraction, raw_fps: Fraction, raw_wh: tuple[float, float],
               n_raw: int | None, scorer: Any, cfg: Any) -> dict:
    """Criterion 2, independent of segment.py: at every cut A|B, comp frame comp_out(A)-1 scores higher
    against A's model (phase_solve.ae_frame + A's transform) than against B's model extended back, and
    comp frame comp_in(B) the reverse. Crossfades: the fitted alpha ramp must follow the declared one
    (plus pure-A / pure-B boundary frames). NOT-IN-RAW neighbours: the placeholder frame scores below
    none_thresh against the extended neighbour model. Dips/flashes: the uniform side is uniform."""
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
            for i, k in enumerate(range(o, o + d)):
                ab, z = scorer.blend(k, models.cand(a, k), models.cand(b, k))
                errs.append({"k": k, "alpha_declared": round(float(alpha[i]), 4),
                             "alpha_fit": None if math.isnan(ab) else round(float(ab), 4),
                             "zncc_fit": None if math.isnan(z) else round(float(z), 4)})
            fitted = [e for e in errs if e["alpha_fit"] is not None]
            max_err = max((abs(e["alpha_fit"] - e["alpha_declared"]) for e in fitted), default=float("nan"))
            c["alpha"] = errs
            c["alpha_max_err"] = None if math.isnan(max_err) else round(max_err, 4)
            if not fitted:
                sides.append({"side": "alpha", "result": "unscorable", "reason": "no blend fit"})
            else:
                sides.append({"side": "alpha", "result": "ok" if max_err <= alpha_tol else "fail",
                              "reason": f"max |alpha_fit - alpha| = {max_err:.3f} (tol {alpha_tol})"})
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


def check_ae_sim(frames: dict, fm: FrameMap, comp_fps: Fraction, main_fps: Fraction, n_main: int,
                 cut_frames_main: Iterable[int], cfg: Any, source: str = "plan") -> dict:
    """AE-simulated RAW frame per MAIN frame == m(k) for >= frame_exact_min of matched frames.

    Exemptions (listed): ambiguous-identical (j in [raw_lo, raw_hi]) and timing-tie frames (off by one).
    On a different MAIN grid (--fps source) MAIN frame K is compared with m(floor(K comp_fps/main_fps)),
    excluding frames within one MAIN frame of a cut (DESIGN §2.5)."""
    exact_min = float(getattr(cfg, "frame_exact_min", 0.99))
    same_grid = Fraction(main_fps) == Fraction(comp_fps)
    cuts = sorted(set(int(c) for c in cut_frames_main))
    near_cut = set()
    if not same_grid:
        for c in cuts:
            near_cut.update((c - 1, c))
    n_matched = n_exact = 0
    ambiguous, ties, mismatches, excluded = [], [], [], []
    for K in range(int(n_main)):
        k = main_to_comp(K, comp_fps, main_fps)
        if not (0 <= k < fm.n) or int(fm.status[k]) != Status.MATCH:
            continue
        if K in near_cut:
            excluded.append(K)
            continue
        n_matched += 1
        ents = frames.get(K, frames.get(str(K))) if isinstance(frames, dict) else None
        j = visible_raw_frame(ents)
        m = int(fm.raw[k])
        if j is not None and j == m:
            n_exact += 1
            continue
        lo, hi = int(fm.raw_lo[k]), int(fm.raw_hi[k])
        if j is not None and lo >= 0 and hi >= 0 and lo <= j <= hi:
            ambiguous.append({"K": K, "k": k, "ae": j, "m": m, "range": [lo, hi]})
        elif j is not None and bool(fm.tie[k]) and abs(j - m) <= 1:
            ties.append({"K": K, "k": k, "ae": j, "m": m})
        else:
            soft = [int(fm.soft_lo[k]), int(fm.soft_hi[k])]
            mismatches.append({"K": K, "k": k, "ae": j, "m": m, "range": [lo, hi], "soft": soft,
                               "within_soft": bool(j is not None and soft[0] >= 0 and soft[0] <= j <= soft[1]),
                               "score": None if np.isnan(fm.score[k]) else round(float(fm.score[k]), 4)})
    n_exempt = len(ambiguous) + len(ties)
    frac = (n_exact + n_exempt) / n_matched if n_matched else 1.0
    failures: list[str] = []
    if n_matched == 0:
        failures.append(f"{source}: no matched frames to compare")
    elif frac < exact_min:
        failures.append(f"{source}: only {frac:.4%} of {n_matched} matched frames show m(k) "
                        f"(< {exact_min:.0%}); first mismatches at k = {[x['k'] for x in mismatches[:10]]}")
    exc = n_exempt + len(mismatches)
    status = "fail" if failures else ("pass_with_exceptions" if exc else "pass")
    summary = (f"{source}: {n_exact}/{n_matched} exact, {len(ambiguous)} ambiguous-identical, {len(ties)} timing-tie, "
               f"{len(mismatches)} mismatched ({frac:.4%} ok)")
    return {"status": status, "summary": summary, "failures": failures, "matched": n_matched, "exact": n_exact,
            "fraction_ok": round(frac, 6), "ambiguous_identical": ambiguous, "timing_tie": ties,
            "mismatches": mismatches[:500], "n_mismatches": len(mismatches), "excluded_near_cuts": len(excluded)}


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
                entries.append({"layer": key, "name": L.get("name"),
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

def check_speed_framing(segments: Sequence[Segment], fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction,
                        raw_wh: tuple[float, float], box: Box | dict | None, comp_wh: tuple[float, float],
                        cfg: Any, feasible_range: Callable | None = None) -> dict:
    """Criterion 4. Speed: inside the feasible range of the segment's constraints (±0.5 %), and snapped
    whenever a snap value was feasible. Framing: per-frame measured Sims (FrameMap) vs the segment model
    within ±1 % scale / ±4 px at the box centre; flip identical; rotation within 0.25°."""
    from .pipeline import segment_constraints, segment_time_mode
    if feasible_range is None:
        feasible_range = _phase().feasible_speed_range
    speed_tol, scale_tol, pos_tol = 0.005, 0.01, 4.0
    rot_tol = max(float(getattr(cfg, "rotation_min_deg", 0.2)), 0.2) + 0.05
    snaps = [float(v) for v in getattr(cfg, "speed_snap_values", ())]
    b = Box.from_dict(box) if isinstance(box, dict) else box
    centre = np.array([[b.x + b.w / 2, b.y + b.h / 2]]) if b is not None else np.array([[comp_wh[0] / 2, comp_wh[1] / 2]])
    used_speeds = sorted({round(float(s.speed), 6) for s in segments if s.type == "raw" and not s.unsnapped})
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
                    feas = sorted({v for v in snaps + used_speeds if lo_v <= v <= hi_v})
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
        rows.append(row)
    status = _status_from(len(failures), len(exceptions))
    summary = f"{len(rows)} raw segments: {len(failures)} problems, {len(exceptions)} exceptions"
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "segments": rows}


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
    tolerance is a failure whatever the code; an unknown code is a failure."""
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
        code = code_in
        evidence = "segment audio analysis"
        if code is None:
            if status_run == "audio_replaced":
                code, evidence = "audio_replaced", "run-level audio status"
            elif au.get("pitch_preserved"):
                code, evidence = "pitch_preserved", "pitch analysis"
            elif peak < min_corr and _overlaps(a0, a1, added_audio):
                code, evidence = "music_dominated", "low correlation under detected added audio"
        if code in AUDIO_EXCEPTION_CODES:
            row.update(result="exception", code=code, evidence=evidence)
            exceptions.append(f"{name}: {code} (lag {lag_ms:+.2f} ms, corr {peak:.2f})")
        else:
            row["result"] = "fail"
            failures.append(f"{name}: audio lag {lag_ms:+.2f} ms / corr {peak:.2f} outside ±{tol} ms and unexplained")
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
            for attempt in range(2):
                try:
                    out[k] = render_preview.render_frame(k, self.rctx, frames)
                    break
                except KeyError as e:
                    j = e.args[0] if e.args else None
                    if attempt or not isinstance(j, (int, np.integer)):
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


def check_visual(comp: Any, rec_frames: Iterable[tuple[int, np.ndarray]], fm: FrameMap,
                 allowed_fn: Callable[[int], np.ndarray | None], box: Box | dict | None, cfg: Any,
                 fail_dir: Path | None = None, source: str = "") -> dict:
    """Masked ZNCC competitor vs match-geometry recreation on every frame (video region, static and
    overlay pixels excluded, cfg.score_blur). Every matched frame must reach cfg.verify_zncc."""
    from . import scoring
    thr = float(getattr(cfg, "verify_zncc", 0.9))
    blur = float(getattr(cfg, "score_blur", 1.0))
    roi = proxy_roi(box, comp.size, comp.ratio)
    x, y, w, h = roi
    n = int(comp.n)
    scores = np.full(n, np.nan, np.float64)
    seen = np.zeros(n, bool)
    fails: list[int] = []
    n_img = 0
    for k, rec in rec_frames:
        if not (0 <= k < n):
            continue
        seen[k] = True
        rec = _resize(_to_gray(rec), comp.size)
        c = np.asarray(comp.get(k))
        allowed = allowed_fn(k)
        m = np.ones((h, w), bool) if allowed is None else np.asarray(allowed)[y:y + h, x:x + w].astype(bool)
        s = scoring.zncc(_blur(c[y:y + h, x:x + w], blur), _blur(rec[y:y + h, x:x + w], blur), m)
        scores[k] = s
        if int(fm.status[k]) == Status.MATCH and not (s >= thr):
            fails.append(k)
            if fail_dir is not None and n_img < MAX_FAILURE_IMAGES and not math.isnan(s):
                _failure_image(Path(fail_dir) / f"k{k:05d}.png", c, rec, roi, s)
                n_img += 1
    matched = fm.status[:n] == Status.MATCH
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
    blend = fm.status[:n] == Status.BLEND
    bs = scores[blend & np.isfinite(scores)]
    failures = []
    if real_fail:
        failures.append(f"{len(real_fail)} matched frames below ZNCC {thr}: {_ranges(real_fail)[:10]}")
    if len(missing):
        failures.append(f"{len(missing)} matched frames missing from the recreation: {_ranges(missing)[:10]}")
    exceptions = [f"{len(nan_fail)} matched frames unscorable (too few visible pixels): {_ranges(nan_fail)[:10]}"] if nan_fail else []
    status = _status_from(len(failures), len(exceptions))
    summary = (f"{int(matched.sum())} matched frames, min ZNCC {dist.get('min', float('nan'))}, median "
               f"{dist.get('median', float('nan'))}, {len(real_fail)} below {thr}" + (f" [{source}]" if source else ""))
    return {"status": status, "summary": summary, "failures": failures, "exceptions": exceptions, "threshold": thr,
            "distribution": dist, "failed_frames": [int(k) for k in real_fail[:1000]],
            "blend_frames_min": round(float(bs.min()), 5) if bs.size else None,
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
                              cfg: Any) -> dict:
    """AE render frame K must match preview frame K better than K-1 / K+1 (same RAW frame on every frame).
    Frames whose preview neighbours are identical (ZNCC >= identical_thresh) are exempt (listed)."""
    from . import scoring
    thr = float(getattr(cfg, "verify_zncc", 0.9))
    ident = float(getattr(cfg, "identical_thresh", 0.9995))
    pv = dict(preview)
    bad, amb, n = [], [], 0
    for K, r in render:
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
    status = "fail" if bad else ("pass_with_exceptions" if amb else "pass")
    return {"status": status, "summary": f"{n} AE frames vs preview: {len(bad)} mismatches, {len(amb)} ambiguous",
            "failures": [f"AE render differs from preview at frames {[b['K'] for b in bad[:10]]}"] if bad else [],
            "mismatches": bad[:200], "ambiguous": amb[:200]}


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
    out_dir.mkdir(parents=True, exist_ok=True)
    tried = []
    frames: list[Path] = []
    movie = None
    for tmpl in OM_TEMPLATES:
        target = out_dir / ("ae_[#####].png" if "PNG" in tmpl else ("ae_[#####].tif" if "TIFF" in tmpl else "ae_render.mov"))
        cmd = [aerender, "-project", str(Path(aep).resolve()), "-comp", MAIN_COMP_NAME, "-RStemplate", "Best Settings",
               "-OMtemplate", tmpl, "-output", str(target)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
        except (OSError, subprocess.SubprocessError) as e:
            tried.append({"template": tmpl, "error": str(e)})
            continue
        tried.append({"template": tmpl, "returncode": res.returncode, "stdout": (res.stdout or "")[-500:]})
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
            for K, p in enumerate(frames[:n_main]):
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                yield K, _resize(img, proxy_size).astype(np.float32)
        else:
            for K, img in VideoFrames(movie, main_fps).gray(n_main, proxy_size):
                yield K, img.astype(np.float32)

    prev = [(K, img.astype(np.float32)) for K, img in VideoFrames(preview, main_fps).gray(n_main, proxy_size)]
    res = compare_render_to_preview(render_iter(), prev, cfg)
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
        warnings.append("cutlist.json differs from the previous run with identical inputs, parameters and tool "
                        f"version ({prev['differences'][:3]}); if the code did not change this is non-determinism")
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
    keys = ("version", "input_hashes", "analysis_params_hash", "stage_versions")
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
# Driver
# ---------------------------------------------------------------------------------------------

def _allowed_fn(ctx: Any) -> Callable[[int], np.ndarray | None]:
    from . import layout as layout_mod
    memo: dict[int, np.ndarray] = {}

    def f(k: int) -> np.ndarray | None:
        if k not in memo:
            if len(memo) > 64:
                memo.clear()
            memo[k] = layout_mod.allowed_mask(ctx.layout, ctx.overlays, int(k), ctx.comp_proxy)
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


def _recreation_source(ctx: Any) -> tuple[Any, str]:
    """(frame source, description) of the match-geometry recreation at competitor size and fps."""
    from . import pipeline
    if pipeline.match_preview_usable(ctx):
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


def verify_all(ctx: Any) -> dict:
    """Stage 9: every check s9_1..s9_7 and the acceptance criteria c1..c6 (see module docstring)."""
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
    raw_wh = (float(cl.raw["width"]), float(cl.raw["height"]))
    comp_wh = (float(cl.competitor["width"]), float(cl.competitor["height"]))
    raw_name = Path(ctx.raw_info.path).name
    checks: dict[str, dict] = {}
    extra: dict[str, dict] = {}

    checks["s9_1_coverage"] = _run_check("s9_1_coverage", lambda: check_coverage(segs, n, cl.layout))

    allowed = _allowed_fn(ctx)
    scorer = None

    def get_scorer() -> ProxyScorer:
        nonlocal scorer
        if scorer is None:
            scorer = ProxyScorer(ctx.comp_proxy, ctx.raw_proxy, _box(ctx), allowed, raw_wh[0], cfg)
        return scorer

    extra["cuts"] = _run_check("c2_cuts", lambda: check_cuts(segs, comp_fps, raw_fps, raw_wh, int(ctx.raw_info.nb_frames),
                                                             get_scorer(), cfg))

    # s9_2: AE semantics from the plan and from the mock-run record
    cut_main = [pipeline.to_main_frame(k, comp_fps, main_fps) for k in cut_frames(segs)]

    def s9_2_plan() -> dict:
        from . import export_ae
        if ctx.plan is None:
            return {"status": "fail", "summary": "no AE plan", "failures": ["ae_plan missing"]}
        return check_ae_sim(export_ae.simulate_ae(ctx.plan), ctx.fm, comp_fps, main_fps, n_main, cut_main, cfg, "plan")

    def s9_2_mock() -> dict:
        rec = (ctx.mock or {}).get("default")
        if not rec or _get(rec, "status") == "not_available":
            return {"status": "not_available", "summary": "mock run not available", "failures": []}
        if _get(rec, "status") == "error":
            return {"status": "fail", "summary": "mock run failed", "failures": [str(_get(rec, "error"))]}
        sim = simulate_record(rec, raw_name, raw_fps, main_fps, n_main)
        return check_ae_sim(sim, ctx.fm, comp_fps, main_fps, n_main, cut_main, cfg, "mock record")

    p2, m2 = _run_check("s9_2_plan", s9_2_plan), _run_check("s9_2_mock", s9_2_mock)
    checks["s9_2_ae_sim"] = {"status": aggregate([p2["status"], m2["status"]]),
                             "summary": f"{p2.get('summary')} | {m2.get('summary')}",
                             "failures": p2.get("failures", []) + m2.get("failures", []), "plan": p2, "mock": m2}

    # s9_3 visual + s9_4 cut images on the match-geometry recreation
    src_holder: dict[str, Any] = {}

    def get_src():
        if "src" not in src_holder:
            src_holder["src"], src_holder["desc"] = _recreation_source(ctx)
        return src_holder["src"], src_holder["desc"]

    def s9_3() -> dict:
        src, desc = get_src()
        res = check_visual(ctx.comp_proxy, src.gray(n, ctx.comp_proxy.size), ctx.fm, allowed, _box(ctx), cfg,
                           cfg.debug_dir / "verify_failures", desc)
        scores = res.pop("scores")
        np.save(Path(cfg.work) / "verify_zncc.npy", scores.astype(np.float32))
        res["scores_file"] = str(Path(cfg.work) / "verify_zncc.npy")
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

    extra["framing"] = _run_check("c4_speed_framing", lambda: check_speed_framing(
        segs, ctx.fm, comp_fps, raw_fps, raw_wh, _box(ctx), comp_wh, cfg))

    extra["mock"] = _run_check("c6_mock", lambda: check_mock(ctx.plan, ctx.mock, main_fps, n_main,
                                                             Path(ctx.paths.get("jsx", cfg.out)).parent
                                                             if ctx.paths.get("jsx") else cfg.out, raw_name))
    main_w, main_h = ctx.main_size or (int(comp_wh[0]), int(comp_wh[1]))
    small = (360, max(2, int(round(360 * main_h / main_w / 2)) * 2))
    checks["s9_6_ae_render"] = _run_check("s9_6_ae_render", lambda: check_ae_render(
        ctx.env, ctx.paths.get("aep"), ctx.paths.get("preview"), n_main, main_fps, small,
        Path(cfg.work) / "aerender", cfg))
    checks["s9_7_determinism"] = _run_check("s9_7_determinism", lambda: check_determinism(ctx))

    exact_note = "" if main_fps == comp_fps else f" (MAIN at {fps_str(main_fps)}: exact only with --fps competitor)"
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
    for name in ("s9_7_determinism",):
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
