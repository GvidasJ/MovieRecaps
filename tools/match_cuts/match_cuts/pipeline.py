"""Pipeline orchestration S0..S10 (DESIGN.md §1, §2.5; prompt Stages 0-10).

``run(cfg)`` executes every stage in order, calling the stage modules strictly through their
DESIGN.md §5 signatures. Everything the stages produce is kept in one :class:`Context`, which
``verify.verify_all`` and ``report.write_report`` read.

Caching (DESIGN §1): the stage modules that take a ``cache`` cache themselves (probe, proxies,
layout, RawIndex, conform via ``.conform.json``). The pipeline caches the stages whose functions
do not take one -- AudioHints (``audio_align``), anchors (``sparse_search``) and the refined FrameMap
plus the pass-2 overlay masks (``frame_map``) -- under ``WORK_DIR/cache/<stage>/<key>`` with
``key = stage_key(stage, competitor hash, RAW hash, cfg.analysis_params())``. Freshly computed
values are always written first and re-read from the cache file, so a first run and a cached
re-run see bit-identical inputs (criterion 9.7).

S5.4 -> S6 (segmentation, phase solve, audio per segment, cutlist assembly) is never cached: it is
what ``verify`` s9_7 re-runs from the cached FrameMap/AudioHints in a fresh context.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import glob
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from . import __version__
from .common import (STAGE_VERSION, Cache, DecisionLog, dump_json, ffmpeg_bin, ffprobe_bin, fmt_seconds,
                     fps_str, log, null_dlog, params_hash, seed_everything, setup_logging, stage_key, timecode)
from .config import Config
from .geometry import Sim
from .model import (AudioHints, Cutlist, FrameMap, Layout, Segment, Status, StreamInfo, cutlist_layout)

CUTLIST_VERSION = 1
MOCK_SCENARIOS = ("default", "media_missing", "new_project_null", "no_marker_property")   # DESIGN §5 export_ae
MAIN_COMP_NAME = "Recreated Edit"
PHASE_TAU = 1e-6            # frame tolerance of the phase LP (DESIGN §2.1)
DEFAULT_SEG_AUDIO = {"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None,
                     "lag_ms": None, "corr": None, "exception": None}


# ---------------------------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------------------------

@dataclass
class Context:
    """Everything one run produces. Attributes are filled stage by stage (None = not reached)."""

    cfg: Config
    env: dict = field(default_factory=dict)
    dlog: DecisionLog = field(default_factory=null_dlog)
    cache: Cache | None = None
    # --- inputs (S2) ---
    comp_input: StreamInfo | None = None      # probe of the user's original COMPETITOR file
    raw_input: StreamInfo | None = None       # probe of the user's original RAW file
    comp_conform: Any = None                  # conform.ConformResult
    raw_conform: Any = None
    comp_info: StreamInfo | None = None       # probe of the AE-imported files (analysis uses ONLY these)
    raw_info: StreamInfo | None = None
    # --- analysis (S3..S5.3) ---
    comp_proxy: Any = None                    # model.Proxy
    raw_proxy: Any = None
    comp_audio: np.ndarray | None = None      # mono float32 at audio_sr, sample 0 = video t 0
    raw_audio: np.ndarray | None = None
    audio_sr: int = 16000
    layout: Layout | None = None
    overlays: Any = None                      # layout.OverlayMasks
    hints: AudioHints | None = None
    index: Any = None                         # visual_match.RawIndex (None on a FrameMap cache hit)
    anchors: list = field(default_factory=list)
    fm_pre: FrameMap | None = None            # refine output, as cached (input of S5.4)
    fm: FrameMap | None = None                # after segmentation (saved to <work>/frame_map.npz)
    # --- S5.4..S6 ---
    segments: list[Segment] = field(default_factory=list)
    audio_result: dict = field(default_factory=dict)
    cutlist: Cutlist | None = None
    main_fps: Fraction | None = None
    main_size: tuple[int, int] | None = None
    # --- S7..S9 ---
    plan: dict | None = None
    mock: dict = field(default_factory=dict)          # scenario -> mock-run record
    ae_run: dict = field(default_factory=dict)        # Stage 7.6 (AE app) result
    exports: dict = field(default_factory=dict)       # export_xml_edl.validate_exports result
    preview: dict = field(default_factory=dict)       # render_preview result
    verify: dict = field(default_factory=dict)
    # --- bookkeeping ---
    paths: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)            # everything (report + CLI)
    analysis_warnings: list[str] = field(default_factory=list)   # known before S5.4 -> cutlist.warnings
    errors: list[dict] = field(default_factory=list)             # non-fatal stage errors (S7/S8)
    previous_cutlist: dict | None = None                          # cutlist.json of the previous run (s9_7)
    keys: dict[str, str] = field(default_factory=dict)           # cache keys used

    # -- helpers ------------------------------------------------------------------------------
    @property
    def comp_fps(self) -> Fraction:
        return Fraction(self.comp_info.fps)

    @property
    def raw_fps(self) -> Fraction:
        return Fraction(self.raw_info.fps)

    @property
    def n_comp(self) -> int:
        return int(self.comp_info.nb_frames)

    def warn(self, msg: str, analysis: bool = False) -> None:
        """Record a warning (analysis=True: it also goes into cutlist.warnings)."""
        log.warning(msg)
        if msg not in self.warnings:
            self.warnings.append(msg)
        if analysis and msg not in self.analysis_warnings:
            self.analysis_warnings.append(msg)


@contextlib.contextmanager
def _stage(ctx: Context, name: str) -> Iterator[None]:
    log.info("== %s", name)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        ctx.timings[name] = round(ctx.timings.get(name, 0.0) + dt, 3)
        log.info("   %s: %.2fs", name, dt)


def _soft(ctx: Context, name: str, fn: Callable[[], Any]) -> tuple[bool, Any]:
    """Run an export step; an exception is recorded (ctx.errors + warning) instead of aborting, so
    verification and the report still explain what failed. Returns (ok, value)."""
    try:
        return True, fn()
    except Exception as e:  # noqa: BLE001 - recorded, logged with traceback, surfaced in verify/report
        log.error("%s failed: %s\n%s", name, e, traceback.format_exc())
        ctx.errors.append({"stage": name, "error": f"{type(e).__name__}: {e}"})
        ctx.warn(f"{name} failed: {type(e).__name__}: {e}")
        ctx.dlog.record("pipeline", "stage_error", step=name, error=str(e))
        return False, None


# ---------------------------------------------------------------------------------------------
# S0 environment
# ---------------------------------------------------------------------------------------------

def _tool_version(binary: str | None) -> str | None:
    if not binary:
        return None
    try:
        res = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"version\s+n?(\d+(?:\.\d+)*)", res.stdout or "")
    return m.group(1) if m else None


def _version_tuple(v: str | None) -> tuple[int, ...]:
    if not v:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def find_after_effects(system: str | None = None, roots: dict[str, str] | None = None) -> dict:
    """Search the standard After Effects install locations (prompt Stage 0).

    Windows: ``C:\\Program Files\\Adobe\\Adobe After Effects *\\Support Files\\{AfterFX,aerender}.exe``;
    macOS: ``/Applications/Adobe After Effects */{Adobe After Effects *.app, aerender}``. The newest
    version (natural sort of the folder name) wins. ``roots`` overrides the base folders (tests).
    """
    system = system or platform.system()
    out: dict[str, Any] = {"ae_app": None, "aerender": None, "ae_version": None, "ae_app_name": None}
    if system == "Windows":
        base = (roots or {}).get("Windows", r"C:\Program Files\Adobe")
        dirs = sorted(glob.glob(os.path.join(base, "Adobe After Effects *")), key=_natural_key, reverse=True)
        for d in dirs:
            sf = os.path.join(d, "Support Files")
            app, rnd = os.path.join(sf, "AfterFX.exe"), os.path.join(sf, "aerender.exe")
            if os.path.exists(app) or os.path.exists(rnd):
                out.update(ae_app=app if os.path.exists(app) else None,
                           aerender=rnd if os.path.exists(rnd) else None,
                           ae_version=os.path.basename(d).replace("Adobe After Effects", "").strip(),
                           ae_app_name=os.path.basename(d))
                break
    elif system == "Darwin":
        base = (roots or {}).get("Darwin", "/Applications")
        dirs = sorted(glob.glob(os.path.join(base, "Adobe After Effects *")), key=_natural_key, reverse=True)
        for d in dirs:
            apps = sorted(glob.glob(os.path.join(d, "Adobe After Effects*.app")), key=_natural_key, reverse=True)
            rnd = os.path.join(d, "aerender")
            if apps or os.path.exists(rnd):
                out.update(ae_app=apps[0] if apps else None, aerender=rnd if os.path.exists(rnd) else None,
                           ae_version=os.path.basename(d).replace("Adobe After Effects", "").strip(),
                           ae_app_name=os.path.splitext(os.path.basename(apps[0]))[0] if apps else None)
                break
    return out


def find_node() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    for cand in ("/opt/node22/bin/node", "/usr/local/bin/node", "/usr/bin/node"):
        if os.path.exists(cand):
            return cand
    return None


def check_env() -> dict:
    """S0: OS, ffmpeg/ffprobe (>= 5.1 preferred), Python package versions, Node (for the ES3/AE mock)
    and After Effects / aerender (optional)."""
    import importlib.metadata as md

    ff, fp = shutil.which(ffmpeg_bin()), shutil.which(ffprobe_bin())
    ffv, fpv = _tool_version(ff), _tool_version(fp)
    versions: dict[str, str | None] = {}
    for dist in ("numpy", "scipy", "opencv-contrib-python-headless", "opencv-contrib-python", "opencv-python",
                 "av", "soundfile", "matplotlib", "scikit-image", "opentimelineio", "scenedetect"):
        try:
            versions[dist] = md.version(dist)
        except md.PackageNotFoundError:
            continue
    try:
        import cv2
        versions["cv2"] = cv2.__version__
    except Exception:  # pragma: no cover - reported, never fatal here
        versions["cv2"] = None
    node = find_node()
    node_v = None
    if node:
        try:
            node_v = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            node_v = None
    env = {
        "os": platform.system(), "platform": platform.platform(), "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(), "ffmpeg": ff, "ffprobe": fp, "ffmpeg_version": ffv, "ffprobe_version": fpv,
        "ffmpeg_ok": bool(ff and fp and _version_tuple(ffv) >= (5, 1)),
        "versions": versions, "node": node, "node_version": node_v, "tool_version": __version__,
    }
    env.update(find_after_effects())
    return env


# ---------------------------------------------------------------------------------------------
# Timeline fps / comp size (DESIGN §2.4, §2.5)
# ---------------------------------------------------------------------------------------------

def resolve_main_fps(cfg: Config, comp_fps: Fraction, raw_fps: Fraction) -> Fraction:
    """main_fps = comp_fps unless fps_mode == 'source' or layout_mode == 'source' (then raw_fps)."""
    if cfg.fps_mode == "source" or cfg.layout_mode == "source":
        return Fraction(raw_fps)
    return Fraction(comp_fps)


def to_main_frame(k: int, comp_fps: Fraction, main_fps: Fraction) -> int:
    """K = floor(k * main_fps / comp_fps + 1/2) (exact rational arithmetic)."""
    if Fraction(main_fps) == Fraction(comp_fps):
        return int(k)
    return math.floor(Fraction(int(k)) * Fraction(main_fps) / Fraction(comp_fps) + Fraction(1, 2))


def main_frame_count(n_comp: int, comp_fps: Fraction, main_fps: Fraction) -> int:
    return to_main_frame(n_comp, comp_fps, main_fps)


def fps_mapping_errors(segments: list[Segment], comp_fps: Fraction, main_fps: Fraction) -> tuple[float, dict[int, dict]]:
    """Per-segment cut errors when MAIN runs on a different grid (DESIGN §2.5).

    Returns (max |error_s| over all cut points, {seg.id: {K_in, K_out, err_in_s, err_out_s}})."""
    per: dict[int, dict] = {}
    worst = Fraction(0)
    for s in segments:
        k_in, k_out = to_main_frame(s.comp_in, comp_fps, main_fps), to_main_frame(s.comp_out, comp_fps, main_fps)
        e_in = Fraction(k_in) / Fraction(main_fps) - Fraction(s.comp_in) / Fraction(comp_fps)
        e_out = Fraction(k_out) / Fraction(main_fps) - Fraction(s.comp_out) / Fraction(comp_fps)
        worst = max(worst, abs(e_in), abs(e_out))
        per[s.id] = {"K_in": k_in, "K_out": k_out, "err_in_s": fmt_seconds(float(e_in)),
                     "err_out_s": fmt_seconds(float(e_out))}
    return fmt_seconds(float(worst)), per


def parse_size(value: str) -> tuple[int, int] | None:
    """'1080x1920' -> (1080, 1920); 'competitor' -> None."""
    if value is None or str(value).strip().lower() in ("", "competitor"):
        return None
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(value))
    if not m:
        raise ValueError(f"invalid comp size {value!r} (expected WxH or 'competitor')")
    w, h = int(m.group(1)), int(m.group(2))
    if not (4 <= w <= 30000 and 4 <= h <= 30000):
        raise ValueError(f"comp size {w}x{h} outside AE's [4, 30000] range")
    return w, h


def resolve_main_size(cfg: Config, comp_wh: tuple[int, int], raw_wh: tuple[int, int]) -> tuple[int, int]:
    """MAIN comp size per layout mode (DESIGN §2.4). In match mode a --comp-size with a different
    aspect than the competitor is rejected (r = min(Wt/Wc, Ht/Hc) must reproduce both sides)."""
    req = parse_size(cfg.comp_size)
    if cfg.layout_mode == "source":
        return int(raw_wh[0]), int(raw_wh[1])
    if cfg.layout_mode == "fill":
        return req if req else (1080, 1920)
    if req is None:
        return int(comp_wh[0]), int(comp_wh[1])
    wc, hc = comp_wh
    r = min(req[0] / wc, req[1] / hc)
    if abs(wc * r - req[0]) > 1.0 or abs(hc * r - req[1]) > 1.0:
        raise ValueError(f"--comp-size {req[0]}x{req[1]} does not keep the competitor aspect {wc}x{hc}; "
                         "match layout needs a proportional size (use --layout fill for other aspects)")
    return req


# ---------------------------------------------------------------------------------------------
# Phase solve (S6) -- DESIGN §2.1, §5 phase_solve
# ---------------------------------------------------------------------------------------------

def segment_time_mode(seg: Segment) -> str:
    """'remap' for freeze / reverse / ramps (time_remap_keys or speed <= 0), else 'stretch'."""
    if seg.time_remap_keys or (seg.speed is not None and seg.speed <= 0):
        return "remap"
    return "stretch"


def remap_raw_seconds(keys: list[dict], k: float) -> float | None:
    """Linear interpolation of time_remap_keys [{comp_frame, raw_seconds}] at comp frame k (held
    outside the key range)."""
    ks = sorted(keys, key=lambda d: float(d["comp_frame"]))
    if not ks:
        return None
    if k <= float(ks[0]["comp_frame"]):
        return float(ks[0]["raw_seconds"])
    if k >= float(ks[-1]["comp_frame"]):
        return float(ks[-1]["raw_seconds"])
    for a, b in zip(ks[:-1], ks[1:]):
        ka, kb = float(a["comp_frame"]), float(b["comp_frame"])
        if ka <= k <= kb:
            u = 0.0 if kb == ka else (k - ka) / (kb - ka)
            return float(a["raw_seconds"]) + u * (float(b["raw_seconds"]) - float(a["raw_seconds"]))
    return float(ks[-1]["raw_seconds"])


def segment_constraints(seg: Segment, fm: FrameMap) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ks, lo, hi) phase constraints of a segment: MATCH frames in [comp_in, comp_out) with their soft
    ranges (fallback: ambiguous-identical range, then the argmax)."""
    k0, k1 = max(0, int(seg.comp_in)), min(fm.n, int(seg.comp_out))
    if k1 <= k0:
        e = np.zeros(0, np.int64)
        return e, e.copy(), e.copy()
    ks = np.arange(k0, k1)
    sel = (fm.status[k0:k1] == Status.MATCH) & (fm.flip[k0:k1] == bool(seg.flip_h))
    ks = ks[sel]
    raw = fm.raw[ks].astype(np.int64)
    lo = np.where(fm.soft_lo[ks] >= 0, fm.soft_lo[ks], np.where(fm.raw_lo[ks] >= 0, fm.raw_lo[ks], raw)).astype(np.int64)
    hi = np.where(fm.soft_hi[ks] >= 0, fm.soft_hi[ks], np.where(fm.raw_hi[ks] >= 0, fm.raw_hi[ks], raw)).astype(np.int64)
    lo = np.minimum(lo, raw)
    hi = np.maximum(hi, raw)
    ok = raw >= 0
    ks, lo, hi = ks[ok].astype(np.int64), lo[ok], hi[ok]
    # extra constraints of frames that are not MATCH in the FrameMap (the A/B frames chosen on crossfade
    # blend frames): segment.py keeps them as seg.__dict__['_phase_extra'] = {k: (lo, hi)}; a future
    # Segment.extra_constraints [[k, lo, hi], ...] field is read too
    extra = [list(x) for x in (getattr(seg, "extra_constraints", None) or [])]
    extra += [[int(k), int(v[0]), int(v[1])] for k, v in sorted((seg.__dict__.get("_phase_extra") or {}).items())
              if int(seg.comp_in) <= int(k) < int(seg.comp_out)]
    if extra:
        e = np.asarray(extra, np.int64).reshape(-1, 3)
        have = set(ks.tolist())
        e = e[[int(k) not in have for k in e[:, 0]]]
        if len(e):
            order = np.argsort(np.concatenate([ks, e[:, 0]]), kind="stable")
            ks = np.concatenate([ks, e[:, 0]])[order]
            lo = np.concatenate([lo, e[:, 1]])[order]
            hi = np.concatenate([hi, e[:, 2]])[order]
    return ks, lo, hi


def max_consistent_subset(ks: np.ndarray, lo: np.ndarray, hi: np.ndarray, comp_in: int, u: float,
                          tau: float = PHASE_TAU) -> np.ndarray:
    """Largest set of frames whose tolerant x-intervals [lo-u*d-tau, hi+1-u*d+tau] share a point
    (interval stabbing sweep; x = raw_fps*raw_in in frames, d = k - comp_in). Returns a bool mask."""
    if len(ks) == 0:
        return np.zeros(0, bool)
    d = (ks - comp_in).astype(np.float64)
    a = lo.astype(np.float64) - u * d - tau
    b = hi.astype(np.float64) + 1.0 - u * d + tau
    ev = sorted([(float(x), 0) for x in a] + [(float(x), 1) for x in b])   # starts before ends at ties
    best, cur, best_x = -1, 0, float(a[0])
    for x, typ in ev:
        if typ == 0:
            cur += 1
            if cur > best:
                best, best_x = cur, x
        else:
            cur -= 1
    return (a <= best_x + 1e-12) & (b >= best_x - 1e-12)


def solve_segment_phase(seg: Segment, fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction, cfg: Config,
                        dlog: DecisionLog, phase=None) -> list[str]:
    """Fill raw_in_seconds / raw_in_interval(_both) / ae_margin_ms / tie_frames / raw_in_frame /
    raw_out_frame of one segment with phase_solve.solve_raw_in (soft ranges, DESIGN §5). Returns
    the warnings raised. Frames the model cannot explain (isolated argmax errors) are dropped by a
    max-consistent-subset sweep, listed in the notes and logged -- never silently."""
    if phase is None:
        from . import phase_solve as phase
    warnings: list[str] = []
    if seg.type != "raw":
        return warnings
    name = f"S{seg.id:02d}"
    if segment_time_mode(seg) == "remap":
        seg.time_mode = "remap"
        if seg.time_remap_keys:
            r0 = remap_raw_seconds(seg.time_remap_keys, seg.comp_in)
            r1 = remap_raw_seconds(seg.time_remap_keys, seg.comp_out - 1)
            seg.raw_in_seconds = fmt_seconds(r0)
            seg.raw_in_frame = int(math.floor(r0 * float(raw_fps) + 1e-9))
            seg.raw_out_frame = int(math.floor(r1 * float(raw_fps) + 1e-9))
        elif seg.raw_in_seconds is None:
            seg.uncertain = True
            warnings.append(f"{name}: remap segment without time-remap keys (speed {seg.speed}) - uncertain")
        dlog.record("phase_solve", "remap_segment", segment=seg.id, keys=len(seg.time_remap_keys),
                    raw_in_seconds=seg.raw_in_seconds)
        return warnings
    seg.time_mode = "stretch"
    ks, lo, hi = segment_constraints(seg, fm)
    v = float(seg.speed)
    if seg.raw_in_seconds is not None and seg.raw_in_interval is not None:
        return _adopt_segment_phase(seg, fm, ks, lo, hi, comp_fps, raw_fps, cfg, dlog, phase)
    if len(ks) == 0:
        seg.uncertain = True
        if seg.raw_in_seconds is not None:
            warnings.append(f"{name}: no matched frame constrains the phase; keeping the segmentation's raw_in "
                            f"{seg.raw_in_seconds:.6f}s - uncertain")
        elif seg.raw_in_frame is not None:
            seg.raw_in_seconds = fmt_seconds((seg.raw_in_frame + 0.25) / float(raw_fps))
            warnings.append(f"{name}: no matched frame constrains the phase; raw_in placed inside RAW frame "
                            f"{seg.raw_in_frame} - uncertain")
        else:
            warnings.append(f"{name}: no matched frame and no RAW frame - cannot place the segment")
        dlog.record("phase_solve", "unconstrained", segment=seg.id, raw_in_frame=seg.raw_in_frame)
        return warnings
    res = phase.solve_raw_in(ks, lo, hi, seg.comp_in, v, comp_fps, raw_fps)
    dropped: list[int] = []
    if not res.get("ok", False):
        u = v * float(raw_fps) / float(comp_fps)
        keep = max_consistent_subset(ks, lo, hi, seg.comp_in, u)
        dropped = [int(k) for k in ks[~keep]]
        res2 = phase.solve_raw_in(ks[keep], lo[keep], hi[keep], seg.comp_in, v, comp_fps, raw_fps)
        dlog.record("phase_solve", "infeasible_outliers_dropped", segment=seg.id, speed=v, dropped=dropped,
                    n_frames=int(len(ks)), ok_after=bool(res2.get("ok", False)))
        res = res2
        if dropped:
            seg.notes = _append_note(seg.notes, f"phase-solve outlier frames (model shows its own frame): "
                                                f"{_ranges_str(dropped)}")
            too_many = len(dropped) > max(1, int(0.01 * len(ks)))
            if too_many:
                seg.uncertain = True
            warnings.append(f"{name}: {len(dropped)} matched frame(s) inconsistent with one linear time map "
                            f"({_ranges_str(dropped)})" + (" - segment marked uncertain" if too_many else ""))
        if not res.get("ok", False):
            seg.uncertain = True
            warnings.append(f"{name}: phase solve infeasible even after dropping outliers - uncertain "
                            "(frame blending / VFR?)")
    if res.get("raw_in") is None:
        # still no LP answer: centre of the max-consistent region of the tolerant constraints
        u = v * float(raw_fps) / float(comp_fps)
        keep = max_consistent_subset(ks, lo, hi, seg.comp_in, u)
        d = (ks[keep] - seg.comp_in).astype(np.float64)
        a = float(np.max(lo[keep] - u * d)) if keep.any() else float(lo[0])
        b = float(np.min(hi[keep] + 1.0 - u * d)) if keep.any() else float(lo[0] + 1)
        res = {**res, "raw_in": 0.5 * (a + b) / float(raw_fps),
               "interval_floor": [a / float(raw_fps), b / float(raw_fps)] if b >= a else None,
               "interval_both": None, "margin_ms": max(0.0, 0.5 * (b - a)) / float(raw_fps) * 1000.0}
    warnings += _fill_phase_fields(seg, fm, res, comp_fps, raw_fps, cfg, phase)
    dlog.record("phase_solve", "raw_in", segment=seg.id, source="pipeline", speed=v, n_constraints=int(len(ks)),
                raw_in_seconds=seg.raw_in_seconds, interval_floor=seg.raw_in_interval,
                interval_both=seg.raw_in_interval_both, margin_ms=seg.ae_margin_ms, slack=res.get("slack"),
                tie_frames=seg.tie_frames, dropped=dropped, ae_rule_sensitive="AE-rule-sensitive" in (seg.notes or ""))
    return warnings


def _adopt_segment_phase(seg: Segment, fm: FrameMap, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                         comp_fps: Fraction, raw_fps: Fraction, cfg: Config, dlog: DecisionLog, phase) -> list[str]:
    """segment.py already phase-solved this segment on its own constraint set (which also knows its
    excluded and blend frames): keep that solution, validate it against the FrameMap and fill the
    derived fields. Frames whose AE-rule RAW frame falls outside their soft range are listed."""
    warnings: list[str] = []
    name = f"S{seg.id:02d}"
    v = float(seg.speed)
    ties = {int(k) for k in seg.tie_frames}
    viol = []
    for k, a, b in zip(ks.tolist(), lo.tolist(), hi.tolist()):
        j = int(phase.ae_frame(float(seg.raw_in_seconds), v, int(k), int(seg.comp_in), comp_fps, raw_fps))
        if not (a <= j <= b) and k not in ties:
            viol.append(int(k))
    if viol:
        seg.notes = _append_note(seg.notes, f"frames outside their soft range at this raw_in: {_ranges_str(viol)}")
        warnings.append(f"{name}: {len(viol)} matched frame(s) outside their soft RAW range at the segmentation's "
                        f"raw_in ({_ranges_str(viol)})")
    a, b = (float(x) for x in seg.raw_in_interval)
    margin = seg.ae_margin_ms
    if margin is None:
        margin = max(0.0, min(float(seg.raw_in_seconds) - a, b - float(seg.raw_in_seconds))) * 1000.0
    res = {"raw_in": float(seg.raw_in_seconds), "interval_floor": [a, b], "interval_both": seg.raw_in_interval_both,
           "margin_ms": margin, "tie_frames": sorted(ties)}
    warnings += _fill_phase_fields(seg, fm, res, comp_fps, raw_fps, cfg, phase)
    dlog.record("phase_solve", "raw_in", segment=seg.id, source="segment", speed=v, n_constraints=int(len(ks)),
                raw_in_seconds=seg.raw_in_seconds, interval_floor=seg.raw_in_interval,
                interval_both=seg.raw_in_interval_both, margin_ms=seg.ae_margin_ms, tie_frames=seg.tie_frames,
                violations=viol, ae_rule_sensitive="AE-rule-sensitive" in (seg.notes or ""))
    return warnings


def _fill_phase_fields(seg: Segment, fm: FrameMap, res: dict, comp_fps: Fraction, raw_fps: Fraction, cfg: Config,
                       phase) -> list[str]:
    """Write a phase solution into the segment (9-decimal seconds), mark tie frames in the FrameMap,
    derive raw_in_frame / raw_out_frame with the AE rule and flag AE-rule-sensitive segments."""
    warnings: list[str] = []
    name = f"S{seg.id:02d}"
    v = float(seg.speed)
    raw_in = float(res["raw_in"])
    seg.raw_in_seconds = fmt_seconds(raw_in)
    fl = res.get("interval_floor")
    seg.raw_in_interval = [fmt_seconds(fl[0]), fmt_seconds(fl[1])] if fl else None
    both = res.get("interval_both")
    seg.raw_in_interval_both = [fmt_seconds(both[0]), fmt_seconds(both[1])] if both else None
    seg.ae_margin_ms = None if res.get("margin_ms") is None else round(float(res["margin_ms"]), 6)
    ties = sorted({int(k) for k in (res.get("tie_frames") or [])} | {int(k) for k in seg.tie_frames})
    seg.tie_frames = ties
    for k in ties:
        if 0 <= k < fm.n:
            fm.tie[k] = True
    seg.raw_in_frame = int(phase.ae_frame(seg.raw_in_seconds, v, seg.comp_in, seg.comp_in, comp_fps, raw_fps))
    seg.raw_out_frame = int(phase.ae_frame(seg.raw_in_seconds, v, seg.comp_out - 1, seg.comp_in, comp_fps, raw_fps))
    sensitive = (seg.ae_margin_ms is not None and seg.ae_margin_ms < cfg.ae_min_margin_ms) or seg.raw_in_interval_both is None
    if sensitive:
        why = []
        if seg.ae_margin_ms is not None and seg.ae_margin_ms < cfg.ae_min_margin_ms:
            why.append(f"margin {seg.ae_margin_ms:.4f} ms < {cfg.ae_min_margin_ms} ms")
        if seg.raw_in_interval_both is None:
            why.append("no raw_in satisfies both floor and round sampling")
        warnings.append(f"{name}: AE-rule-sensitive segment ({'; '.join(why)}); if After Effects shows an "
                        "off-by-one frame, re-export with --ae-time-mode frames")
        seg.notes = _append_note(seg.notes, "AE-rule-sensitive")
    return warnings


def _append_note(notes: str, extra: str) -> str:
    if not notes:
        return extra
    if extra in notes:
        return notes
    return f"{notes}; {extra}"


def _ranges(frames: list[int]) -> list[tuple[int, int]]:
    """Sorted ints -> inclusive runs [(a, b), ...]."""
    fr = sorted(set(int(f) for f in frames))
    out: list[tuple[int, int]] = []
    for f in fr:
        if out and f == out[-1][1] + 1:
            out[-1] = (out[-1][0], f)
        else:
            out.append((f, f))
    return out


def _ranges_str(frames: list[int], limit: int = 12) -> str:
    rs = _ranges(frames)
    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in rs[:limit]]
    if len(rs) > limit:
        parts.append(f"... (+{len(rs) - limit} runs)")
    return ", ".join(parts)


# ---------------------------------------------------------------------------------------------
# Cutlist assembly (S6)
# ---------------------------------------------------------------------------------------------

def media_block(info: StreamInfo, conf: Any, source: StreamInfo | None) -> dict:
    """cutlist.competitor / cutlist.raw block for the AE-imported file ``info``."""
    file_rel = getattr(conf, "file_rel", "") or ""
    file_abs = getattr(conf, "file_abs", "") or str(Path(info.path).resolve())
    src_path = getattr(conf, "source_path", "") or (source.path if source else info.path)
    w = int(info.display_width or info.width)
    h = int(info.display_height or info.height)
    fps = Fraction(info.fps)
    return {
        "file": file_rel or file_abs,
        "file_rel": file_rel,
        "file_abs": file_abs,
        "source_path": str(src_path),
        "width": w, "height": h,
        "fps": fps_str(fps),
        "frames": int(info.nb_frames),
        "duration_s": fmt_seconds(info.nb_frames / float(fps)) if fps else 0.0,
        "conformed": bool(getattr(conf, "conformed", False)),
        "conform_reason": conform_reason(info, conf, source),
        "container": info.container, "codec": info.vcodec, "profile": info.vprofile, "pix_fmt": info.pix_fmt,
        "has_audio": bool(info.has_audio), "audio_codec": info.acodec,
        "audio_sample_rate": int(info.a_sample_rate or 0), "audio_channels": int(info.a_channels or 0),
        "hash": info.file_hash,
        "source_hash": source.file_hash if source else info.file_hash,
        "source_issues": list(source.ae_issues) if source else list(info.ae_issues),
    }


def conform_reason(info: StreamInfo, conf: Any, source: StreamInfo | None) -> str:
    """Deterministic conform reason for cutlist.json (conform's own wording can depend on the state of
    output/media, e.g. 'hardlink' on the first run vs 'same' on a re-run; the report shows that text)."""
    issues = list(source.ae_issues) if source is not None else []
    if getattr(conf, "conformed", False):
        return "conformed: " + (", ".join(issues) if issues else "forced (--force-conform)")
    if not getattr(conf, "file_rel", "") and getattr(conf, "file_abs", ""):
        return "AE-safe: referenced by absolute path (large file)"
    return "AE-safe: used unchanged"


def overlays_detected(layout: Layout, n_frames: int) -> list[dict]:
    """Detected creative overlays (never recreated): static zones + caption timings (prompt Stage 4)."""
    out: list[dict] = []
    for z in layout.zones:
        out.append({"type": z.type, "comp_in": 0 if z.comp_in is None else int(z.comp_in),
                    "comp_out": int(n_frames) if z.comp_out is None else int(z.comp_out),
                    "x": float(z.x), "y": float(z.y), "w": float(z.w), "h": float(z.h),
                    "static": bool(z.static), "text": z.text, "notes": z.notes})
    for c in layout.captions:
        d = {"type": c.get("type", "captions")}
        for k in ("comp_in", "comp_out"):
            if k in c:
                d[k] = int(c[k])
        for k in ("x", "y", "w", "h"):
            if k in c:
                d[k] = float(c[k])
        for k, v in c.items():
            if k not in d and k != "type":
                d[k] = v
        out.append(d)
    out.sort(key=lambda d: (d.get("comp_in", 0), str(d.get("type")), d.get("y", 0.0), d.get("x", 0.0)))
    return out


def placeholder_label(seg: Segment, comp_fps: Fraction) -> str:
    return (f"MISSING - not in RAW ({timecode(seg.comp_in, comp_fps)}-{timecode(seg.comp_out, comp_fps)}, "
            f"frames {seg.comp_in}-{seg.comp_out - 1})")


def segment_warnings(segments: list[Segment], comp_fps: Fraction, audio_result: dict) -> list[str]:
    """Deterministic warnings derived from the segments (go into cutlist.warnings)."""
    out: list[str] = []
    for s in segments:
        name = f"S{s.id:02d}"
        if s.type == "not_in_raw":
            out.append(f"NOT-IN-RAW: comp frames {s.comp_in}-{s.comp_out - 1} "
                       f"({timecode(s.comp_in, comp_fps)}-{timecode(s.comp_out, comp_fps)}) - placeholder '{s.label}'")
        if s.uncertain:
            out.append(f"{name}: uncertain ({s.notes or 'see decisions.jsonl'})")
        if s.unsnapped and s.type == "raw":
            out.append(f"{name}: speed {s.speed:.4f} could not be snapped to a common value")
        if s.retime and s.retime != "none":
            out.append(f"{name}: competitor used {s.retime} retiming - AE Frame Blending only approximates it")
        if s.type == "raw" and s.speed not in (0, 1) and (s.audio or {}).get("pitch_preserved"):
            out.append(f"{name}: competitor preserved pitch at speed {s.speed:.3f}; AE's time stretch changes pitch")
    status = (audio_result or {}).get("status")
    if status == "audio_replaced":
        out.append("competitor audio does not match RAW anywhere (audio replaced): mapping is visual-only")
    elif status == "no_audio":
        out.append("no usable audio in competitor and/or RAW: mapping is visual-only")
    return out


def build_cutlist(ctx: Context, segments: list[Segment], audio_result: dict, seg_warnings: list[str]) -> Cutlist:
    """Assemble cutlist.json (prompt Stage 6 schema + DESIGN §3 extras). Deterministic: no wall-clock
    values except provenance.timings (excluded from the determinism comparison)."""
    cfg = ctx.cfg
    comp_fps, raw_fps = ctx.comp_fps, ctx.raw_fps
    main_fps = ctx.main_fps or resolve_main_fps(cfg, comp_fps, raw_fps)
    max_err, per = fps_mapping_errors(segments, comp_fps, main_fps)
    if main_fps != comp_fps:
        for s in segments:
            p = per[s.id]
            if p["err_in_s"] or p["err_out_s"]:
                s.notes = _append_note(s.notes, f"MAIN fps {fps_str(main_fps)}: cut error in {p['err_in_s'] * 1000:+.3f} ms, "
                                                f"out {p['err_out_s'] * 1000:+.3f} ms")
    comp_block = media_block(ctx.comp_info, ctx.comp_conform, ctx.comp_input)
    raw_block = media_block(ctx.raw_info, ctx.raw_conform, ctx.raw_input)
    main_size = ctx.main_size or resolve_main_size(cfg, (comp_block["width"], comp_block["height"]),
                                                   (raw_block["width"], raw_block["height"]))
    settings = {
        "layout_mode": cfg.layout_mode, "comp_size": cfg.comp_size, "main_size": [int(main_size[0]), int(main_size[1])],
        "fps_mode": cfg.fps_mode, "main_fps": fps_str(main_fps), "fps_source_max_error_s": max_err,
        "ae_time_mode": cfg.ae_time_mode, "ae_min_margin_ms": cfg.ae_min_margin_ms,
        "criteria_exact": bool(main_fps == comp_fps),
    }
    audio_block = {k: v for k, v in (audio_result or {}).items() if k not in ("segments", "added_audio")}
    audio_block.setdefault("status", "ok")
    audio_block.setdefault("notes", [])
    audio_block["analysis_sr"] = int(ctx.audio_sr)
    audio_block["competitor_has_audio"] = bool(ctx.comp_info.has_audio)
    audio_block["raw_has_audio"] = bool(ctx.raw_info.has_audio)
    if ctx.hints is not None and len(ctx.hints.comp_t):
        audio_block["hint_windows"] = int(len(ctx.hints.comp_t))
        audio_block["hint_windows_confident"] = int(ctx.hints.confident(cfg.audio_min_conf).sum())
    warnings: list[str] = []
    for w in list(ctx.analysis_warnings) + list(seg_warnings):
        if w not in warnings:
            warnings.append(w)
    if main_fps != comp_fps:
        warnings.append(f"MAIN comp runs at {fps_str(main_fps)} (fps_mode={cfg.fps_mode}, layout={cfg.layout_mode}): "
                        f"cuts rounded to the nearest MAIN frame, max error {max_err * 1000:.3f} ms; "
                        "criteria 2/6 are exact only with --fps competitor")
    provenance = {
        "tool": "match_cuts", "version": __version__,
        "input_hashes": {"competitor": ctx.comp_input.file_hash if ctx.comp_input else ctx.comp_info.file_hash,
                         "raw": ctx.raw_input.file_hash if ctx.raw_input else ctx.raw_info.file_hash},
        "analysed_hashes": {"competitor": ctx.comp_info.file_hash, "raw": ctx.raw_info.file_hash},
        "analysis_params_hash": params_hash(cfg.analysis_params()),
        "analysis_params": cfg.analysis_params(),
        "stage_versions": dict(sorted(STAGE_VERSION.items())),
        "seed": cfg.seed,
        "ffmpeg_version": ctx.env.get("ffmpeg_version"),
        "timings": dict(ctx.timings),
    }
    layout_block = cutlist_layout(ctx.layout, cfg.layout_mode) if ctx.layout is not None else {"mode": cfg.layout_mode}
    return Cutlist(
        version=CUTLIST_VERSION, competitor=comp_block, raw=raw_block, layout=layout_block,
        segments=sorted(segments, key=lambda s: (s.comp_in, s.id)),
        overlays_detected=overlays_detected(ctx.layout, ctx.n_comp) if ctx.layout is not None else [],
        added_audio=list((audio_result or {}).get("added_audio", [])), audio=audio_block, settings=settings,
        warnings=warnings, provenance=provenance)


def _normalise_segments(segments: list[Segment], comp_fps: Fraction, dlog: DecisionLog) -> list[Segment]:
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    ids = [s.id for s in segs]
    if len(set(ids)) != len(ids):
        for i, s in enumerate(segs, start=1):
            s.id = i
        dlog.record("pipeline", "segments_renumbered", reason="duplicate ids", old_ids=ids)
    for s in segs:
        s.audio = {**DEFAULT_SEG_AUDIO, **(s.audio or {})}
        if s.type == "not_in_raw" and not s.label:
            s.label = placeholder_label(s, comp_fps)
    return segs


def apply_segment_audio(segments: list[Segment], audio_result: dict) -> None:
    per = (audio_result or {}).get("segments", {}) or {}
    for s in segments:
        upd = per.get(s.id, per.get(str(s.id)))
        if upd:
            s.audio = {**DEFAULT_SEG_AUDIO, **(s.audio or {}), **upd}


def segment_and_assemble(ctx: Context, fm_pre: FrameMap, dlog: DecisionLog, debug_dir: Path
                         ) -> tuple[FrameMap, list[Segment], dict, Cutlist]:
    """S5.4 -> S6: segmentation + framing, phase solve, audio per segment, Cutlist.

    Works on a copy of ``fm_pre`` (segment.py marks BLEND/tie frames and model-consistent m(k)).
    This is exactly what verify s9_7 re-runs in a fresh context."""
    from . import audio_align, segment
    cfg = ctx.cfg
    fm = fm_pre.copy()
    Path(debug_dir).mkdir(parents=True, exist_ok=True)
    segments = segment.build_segments(fm, ctx.comp_proxy, ctx.raw_proxy, ctx.layout, ctx.overlays, cfg, dlog,
                                      debug_dir, hints=ctx.hints)
    segments = _normalise_segments(list(segments), ctx.comp_fps, dlog)
    seg_warn: list[str] = []
    for s in segments:
        seg_warn.extend(solve_segment_phase(s, fm, ctx.comp_fps, ctx.raw_fps, cfg, dlog))
    comp_y = ctx.comp_audio if ctx.comp_audio is not None else np.zeros(0, np.float32)
    raw_y = ctx.raw_audio if ctx.raw_audio is not None else np.zeros(0, np.float32)
    audio_result = audio_align.analyze_segments_audio(segments, comp_y, raw_y, ctx.audio_sr, ctx.comp_fps, cfg, dlog)
    audio_result = audio_result or {}
    apply_segment_audio(segments, audio_result)
    seg_warn.extend(segment_warnings(segments, ctx.comp_fps, audio_result))
    cutlist = build_cutlist(ctx, segments, audio_result, seg_warn)
    return fm, segments, audio_result, cutlist


# ---------------------------------------------------------------------------------------------
# Pipeline-level caches (stages whose functions take no cache)
# ---------------------------------------------------------------------------------------------

def _analysis_key(ctx: Context, stage: str, *extra: Any) -> str:
    return stage_key(stage, ctx.comp_info.file_hash, ctx.raw_info.file_hash, ctx.cfg.analysis_params(), *extra)


def cached_hints(ctx: Context, compute: Callable[[], AudioHints]) -> AudioHints:
    key = _analysis_key(ctx, "audio_align", int(ctx.audio_sr))
    ctx.keys["audio_align"] = key
    p = ctx.cache.path("audio_align", key, ".npz")
    if not p.exists():
        hints = compute()
        tmp = p.with_name(p.stem + ".tmp.npz")
        hints.save(tmp)
        os.replace(tmp, p)
    else:
        log.info("audio hints: cache hit %s", p.name)
    return AudioHints.load(p)


def _anchor_to_dict(a: Any) -> dict:
    d = dataclasses.asdict(a) if dataclasses.is_dataclass(a) else dict(vars(a))
    sim = getattr(a, "sim", None)
    if isinstance(sim, Sim):
        d["sim"] = {"s": sim.s, "theta_deg": sim.theta_deg, "tx": sim.tx, "ty": sim.ty}
    return d


def _anchor_from_dict(d: dict, anchor_cls: Any) -> Any:
    d = dict(d)
    if isinstance(d.get("sim"), dict):
        d["sim"] = Sim(**{k: float(v) for k, v in d["sim"].items()})
    if dataclasses.is_dataclass(anchor_cls):
        names = {f.name for f in dataclasses.fields(anchor_cls)}
        return anchor_cls(**{k: v for k, v in d.items() if k in names})
    return anchor_cls(**d)


def cached_anchors(ctx: Context, compute: Callable[[], list]) -> list:
    from . import visual_match
    key = _analysis_key(ctx, "sparse_search")
    ctx.keys["sparse_search"] = key
    rows = ctx.cache.json("sparse_search", key, lambda: [_anchor_to_dict(a) for a in compute()])
    return [_anchor_from_dict(r, visual_match.Anchor) for r in rows]


def frame_map_cache_paths(ctx: Context) -> tuple[Path, Path]:
    key = _analysis_key(ctx, "frame_map")
    ctx.keys["frame_map"] = key
    return ctx.cache.path("frame_map", key, ".npz"), ctx.cache.path("frame_map", key, ".overlays.npz")


def load_overlays(path: Path) -> Any:
    from . import layout as layout_mod
    return layout_mod.OverlayMasks.load(path)


def save_frame_map_cache(fm: FrameMap, overlays: Any, fm_path: Path, ov_path: Path) -> None:
    tmp = fm_path.with_name(fm_path.stem + ".tmp.npz")
    fm.save(tmp)
    ov_tmp = ov_path.with_name(ov_path.stem + ".tmp.npz")
    overlays.save(ov_tmp)
    os.replace(ov_tmp, ov_path)
    os.replace(tmp, fm_path)     # the FrameMap file last: its existence marks a complete entry


def hint_windows(hints: AudioHints, raw_fps: Fraction, n_raw: int, cfg: Config, extra_times: list[float] = ()) -> list[tuple[int, int]]:
    """Dense RAW windows (frame ranges, half-open) around confident audio hints (long-RAW proxies)."""
    ts = []
    if hints is not None and len(hints.comp_t):
        conf = hints.confident(cfg.audio_min_conf)
        ts.extend(float(t) for t in hints.raw_t[conf])
    ts.extend(float(t) for t in extra_times)
    wins: list[tuple[int, int]] = []
    for t in sorted(ts):
        a = max(0, int(math.floor((t - cfg.long_raw_window_s) * float(raw_fps))))
        b = min(int(n_raw), int(math.ceil((t + cfg.long_raw_window_s) * float(raw_fps))) + 1)
        if b <= a:
            continue
        if wins and a <= wins[-1][1]:
            wins[-1] = (wins[-1][0], max(wins[-1][1], b))
        else:
            wins.append((a, b))
    return wins


# ---------------------------------------------------------------------------------------------
# Stage 7.6: run the JSX in After Effects when it is installed
# ---------------------------------------------------------------------------------------------

def run_after_effects(env: dict, jsx_path: str | Path, timeout: float = 3600.0, poll_s: float = 2.0) -> dict:
    """Run build_ae_project.jsx in After Effects (Windows ``AfterFX.exe -r``, macOS ``osascript
    DoScriptFile``) and wait until recreated_edit.aep appears next to it (AfterFX.exe keeps running after
    the script, so the process is polled instead of waited for; After Effects is left open)."""
    jsx = Path(jsx_path).resolve()
    aep = jsx.parent / "recreated_edit.aep"
    app = env.get("ae_app")
    if not app:
        return {"status": "not_available", "reason": f"After Effects not found on {env.get('os', platform.system())}"}
    if env.get("os") == "Windows":
        cmd = [app, "-r", str(jsx)]
    elif env.get("os") == "Darwin":
        name = env.get("ae_app_name") or Path(app).stem
        cmd = ["osascript", "-e", f'tell application "{name}" to DoScriptFile "{jsx}"']
    else:
        return {"status": "not_available", "reason": f"unsupported OS {env.get('os')}"}
    before = aep.stat().st_mtime_ns if aep.exists() else None

    def saved() -> bool:
        return aep.exists() and aep.stat().st_mtime_ns != before

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return {"status": "failed", "cmd": cmd, "error": str(e)}
    deadline = time.monotonic() + timeout
    last_size = -1
    while time.monotonic() < deadline:
        if saved():
            size = aep.stat().st_size
            if size == last_size and size > 0:
                break                          # written completely
            last_size = size
        elif proc.poll() is not None and env.get("os") == "Darwin":
            break                              # osascript returned without a saved project
        time.sleep(poll_s)
    ok = saved()
    rc = proc.poll()
    err = ""
    if rc is not None and proc.stderr is not None:
        err = (proc.stderr.read() or "")[-2000:]
    return {"status": "ok" if ok else "failed", "cmd": cmd, "returncode": rc, "stderr": err,
            "aep": str(aep) if ok else None,
            "error": None if ok else f"recreated_edit.aep did not appear within {timeout:.0f}s"}


# ---------------------------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------------------------

def _prepare_dirs(cfg: Config) -> None:
    for d in (cfg.out, cfg.work, cfg.debug_dir, cfg.media_dir, cfg.debug_dir / "cuts"):
        Path(d).mkdir(parents=True, exist_ok=True)


def _input_stat(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def _guard_paths(cfg: Config) -> None:
    """Inputs must exist, differ, and never live where outputs are written (inputs are never modified)."""
    for role, p in (("competitor", cfg.competitor), ("raw", cfg.raw)):
        if not Path(p).is_file():
            raise FileNotFoundError(f"{role} file not found: {p}")
    if Path(cfg.competitor).resolve() == Path(cfg.raw).resolve():
        raise ValueError("competitor and raw are the same file")
    protected = [Path(cfg.out_dir).resolve() / "media", Path(cfg.work_dir).resolve() / "cache"]
    for p in (cfg.competitor, cfg.raw):
        rp = Path(p).resolve()
        for d in protected:
            if rp.is_relative_to(d):
                raise ValueError(f"input {p} lives inside {d}, where outputs are written; move it first")
        if rp.parent == Path(cfg.out_dir).resolve() and rp.name in (
                "cutlist.json", "cutlist.csv", "preview_recreation.mp4", "compare.mp4", "recreated_edit.xml",
                "recreated_edit.edl", "build_ae_project.jsx", "report.md", "verify.json"):
            raise ValueError(f"input {p} would be overwritten by an output of the same name")


def stage_env(ctx: Context) -> None:
    env = check_env()
    ctx.env = env
    if not env.get("ffmpeg") or not env.get("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe not found on PATH. Install them (Linux: apt install ffmpeg; macOS: brew "
                           "install ffmpeg; Windows: winget install Gyan.FFmpeg) or set FFMPEG/FFPROBE.")
    if not env.get("ffmpeg_ok"):
        ctx.warn(f"ffmpeg {env.get('ffmpeg_version')} is older than the recommended 5.1")
    ctx.dlog.record("env", "check", os=env["os"], ffmpeg=env.get("ffmpeg_version"), node=env.get("node_version"),
                    ae_app=env.get("ae_app"), aerender=env.get("aerender"))


def stage_probe_conform(ctx: Context) -> None:
    from . import conform, probe
    cfg = ctx.cfg
    work = str(cfg.work)
    ctx.comp_input = probe.probe(cfg.competitor, "competitor", work, decode=True)
    ctx.raw_input = probe.probe(cfg.raw, "raw", work, decode=True)
    for info in (ctx.comp_input, ctx.raw_input):
        ctx.dlog.record("probe", "input", role=info.role, path=info.path, fps=fps_str(info.fps),
                        frames=info.nb_frames, vfr=info.vfr, ae_issues=info.ae_issues, rotation=info.rotation)
    if ctx.comp_input.vfr:
        ctx.warn(f"competitor is VFR (PTS jitter {ctx.comp_input.pts_jitter:.2f} frames): the timeline is built on "
                 f"its nominal {fps_str(ctx.comp_input.fps)} fps (frame displayed at each t_k)", analysis=True)
    if ctx.raw_input.vfr:
        ctx.warn(f"RAW is VFR (PTS jitter {ctx.raw_input.pts_jitter:.2f} frames): conformed to CFR "
                 f"{fps_str(ctx.raw_input.fps)}", analysis=True)
    ctx.raw_conform = conform.conform(ctx.raw_input, "raw", cfg, ctx.dlog)
    ctx.comp_conform = conform.conform(ctx.comp_input, "competitor", cfg, ctx.dlog)
    for role, res in (("raw", ctx.raw_conform), ("competitor", ctx.comp_conform)):
        ctx.dlog.record("conform", "result", role=role, path=str(res.path), conformed=bool(res.conformed),
                        reason=res.reason, verification=getattr(res, "verification", {}))
        if not Path(res.path).exists():
            raise FileNotFoundError(f"conform did not produce the {role} media file {res.path}")
        ver = getattr(res, "verification", None) or {}
        if res.conformed and ver and (ver.get("ok") is False or ver.get("passed") is False):
            raise RuntimeError(f"conformed {role} failed verification against the original: {ver}")
    # analysis happens ONLY on the files After Effects imports
    ctx.raw_info = probe.probe(str(ctx.raw_conform.path), "raw", work, decode=True)
    ctx.comp_info = probe.probe(str(ctx.comp_conform.path), "competitor", work, decode=True)
    for info in (ctx.raw_info, ctx.comp_info):
        if info.nb_frames <= 0 or not info.fps:
            raise RuntimeError(f"{info.role} media {info.path} has no decodable video frames")
        if info.ae_issues:
            ctx.warn(f"{info.role} media {Path(info.path).name} still has AE issues after conform: "
                     f"{', '.join(info.ae_issues)}", analysis=True)
    ctx.main_fps = resolve_main_fps(ctx.cfg, ctx.comp_fps, ctx.raw_fps)
    ctx.main_size = resolve_main_size(ctx.cfg, (ctx.comp_info.display_width or ctx.comp_info.width,
                                                ctx.comp_info.display_height or ctx.comp_info.height),
                                      (ctx.raw_info.display_width or ctx.raw_info.width,
                                       ctx.raw_info.display_height or ctx.raw_info.height))
    ctx.dlog.record("pipeline", "timeline", main_fps=fps_str(ctx.main_fps), main_size=list(ctx.main_size),
                    comp_fps=fps_str(ctx.comp_fps), raw_fps=fps_str(ctx.raw_fps), fps_mode=ctx.cfg.fps_mode,
                    layout_mode=ctx.cfg.layout_mode)


def stage_audio(ctx: Context) -> None:
    from . import proxies
    sr = int(ctx.cfg.audio_sr)
    ctx.audio_sr = sr
    ctx.comp_audio = proxies.load_audio(ctx.comp_info, sr, ctx.cache)
    ctx.raw_audio = proxies.load_audio(ctx.raw_info, sr, ctx.cache)
    if ctx.comp_audio is None or len(ctx.comp_audio) == 0:
        ctx.warn("competitor has no audio: audio alignment skipped (visual-only mapping)", analysis=True)
    if ctx.raw_audio is None or len(ctx.raw_audio) == 0:
        ctx.warn("RAW has no audio: audio alignment skipped (visual-only mapping)", analysis=True)


def stage_align(ctx: Context) -> None:
    from . import audio_align

    def compute() -> AudioHints:
        if ctx.comp_audio is None or ctx.raw_audio is None or not len(ctx.comp_audio) or not len(ctx.raw_audio):
            return AudioHints.empty()
        return audio_align.coarse_align(ctx.comp_audio, ctx.raw_audio, ctx.audio_sr, ctx.cfg, ctx.dlog)

    ctx.hints = cached_hints(ctx, compute)
    if len(ctx.hints.comp_t):
        conf = ctx.hints.confident(ctx.cfg.audio_min_conf)
        ctx.dlog.record("audio_align", "summary", windows=int(len(ctx.hints.comp_t)), confident=int(conf.sum()))


def stage_proxies(ctx: Context) -> None:
    from . import proxies
    cfg = ctx.cfg
    ctx.comp_proxy = proxies.build_proxy(ctx.comp_info, "competitor", cfg, ctx.cache)
    windows = None
    if ctx.raw_info.duration > cfg.long_raw_s:
        windows = hint_windows(ctx.hints, ctx.raw_fps, ctx.raw_info.nb_frames, cfg)
        ctx.dlog.record("proxies", "long_raw_windows", n=len(windows), windows=windows[:200])
    ctx.raw_proxy = proxies.build_proxy(ctx.raw_info, "raw", cfg, ctx.cache, windows=windows)
    for p, info in ((ctx.comp_proxy, ctx.comp_info), (ctx.raw_proxy, ctx.raw_info)):
        if p.n != info.nb_frames:
            raise RuntimeError(f"{info.role} proxy holds {p.n} frames but the file has {info.nb_frames}")


def stage_layout(ctx: Context) -> None:
    from . import layout as layout_mod
    ctx.layout, ctx.overlays = layout_mod.analyze_layout(ctx.comp_proxy, ctx.cfg, ctx.cache, ctx.cfg.debug_dir, ctx.dlog)
    lay = ctx.layout
    if lay.extra_regions or any(p.mode in ("split", "pip") for p in lay.periods):
        ctx.warn(f"{len(lay.extra_regions)} extra video region(s) (split-screen / picture-in-picture) detected: "
                 "only the dominant region is recreated (see report)", analysis=True)
    for n in lay.notes:
        ctx.dlog.record("layout", "note", note=n)


def stage_visual_refine(ctx: Context) -> None:
    """S5.2 + S5.3 (skipped entirely on a FrameMap cache hit)."""
    cfg = ctx.cfg
    fm_path, ov_path = frame_map_cache_paths(ctx)
    if fm_path.exists() and ov_path.exists():
        log.info("frame map: cache hit %s", fm_path.name)
        ctx.dlog.record("refine", "cache_hit", key=ctx.keys["frame_map"])
    else:
        from . import proxies, refine, visual_match
        with _stage(ctx, "S5.2 visual search"):
            seed_everything(cfg.seed)
            ctx.index = visual_match.RawIndex.build(ctx.raw_proxy, cfg, ctx.cache)
            ctx.anchors = cached_anchors(ctx, lambda: visual_match.sparse_search(
                ctx.comp_proxy, ctx.raw_proxy, ctx.layout, ctx.overlays, ctx.index, ctx.hints, cfg, ctx.dlog))
            ctx.dlog.record("visual_match", "summary", anchors=len(ctx.anchors))
            if not ctx.raw_proxy.dense and ctx.anchors:
                wins = hint_windows(AudioHints.empty(), ctx.raw_fps, ctx.raw_info.nb_frames, cfg,
                                    extra_times=[a.raw / float(ctx.raw_fps) for a in ctx.anchors])
                ctx.raw_proxy = proxies.extend_proxy(ctx.raw_proxy, wins, cfg, ctx.cache)
        with _stage(ctx, "S5.3 refine"):
            seed_everything(cfg.seed)
            fm = refine.build_frame_map(ctx.comp_proxy, ctx.raw_proxy, ctx.layout, ctx.overlays, ctx.anchors,
                                        ctx.hints, ctx.index, cfg, ctx.cache, ctx.dlog, cfg.debug_dir)
            if fm.n != ctx.n_comp:
                raise RuntimeError(f"FrameMap has {fm.n} rows for {ctx.n_comp} competitor frames")
            save_frame_map_cache(fm, ctx.overlays, fm_path, ov_path)
    # always continue from the cache files (first run == cached re-run, bit for bit)
    ctx.fm_pre = FrameMap.load(fm_path)
    ctx.overlays = load_overlays(ov_path)


def stage_segments(ctx: Context) -> None:
    cfg = ctx.cfg
    prev = cfg.out / "cutlist.json"
    if prev.exists():
        try:
            ctx.previous_cutlist = json.loads(prev.read_text())
        except (OSError, ValueError):
            ctx.previous_cutlist = None
    # the layout used by S5.4+ is persisted and re-read, so this run and the s9_7 re-run use the same object
    dump_json(ctx.layout.to_dict(), cfg.work / "layout.json")
    ctx.layout = Layout.from_dict(json.loads((cfg.work / "layout.json").read_text()))
    ctx.fm, ctx.segments, ctx.audio_result, ctx.cutlist = segment_and_assemble(ctx, ctx.fm_pre, ctx.dlog, cfg.debug_dir)
    ctx.fm.save(cfg.work / "frame_map.npz")
    for w in ctx.cutlist.warnings:
        if w not in ctx.warnings:
            ctx.warnings.append(w)
    write_cutlist(ctx)


def write_cutlist(ctx: Context) -> Path:
    ctx.cutlist.provenance["timings"] = dict(ctx.timings)
    p = ctx.cfg.out / "cutlist.json"
    ctx.cutlist.save(p)
    ctx.paths["cutlist"] = str(p)
    return p


def footage_meta(ctx: Context) -> dict:
    """{basename: {width, height, fps_num, fps_den, frames, has_audio}} for export_ae / the mock."""
    out = {}
    for info in (ctx.raw_info, ctx.comp_info):
        fps = Fraction(info.fps)
        out[Path(info.path).name] = {"width": int(info.display_width or info.width),
                                     "height": int(info.display_height or info.height),
                                     "fps_num": fps.numerator, "fps_den": fps.denominator,
                                     "frames": int(info.nb_frames), "has_audio": bool(info.has_audio)}
    return out


def stage_ae(ctx: Context) -> None:
    from . import export_ae
    cfg = ctx.cfg
    meta = footage_meta(ctx)
    ok, plan = _soft(ctx, "S7 ae_plan", lambda: export_ae.ae_plan(ctx.cutlist, cfg, meta))
    if not ok or plan is None:
        return
    ctx.plan = plan
    dump_json(ctx.plan, cfg.work / "ae_plan.json")
    jsx = cfg.out / "build_ae_project.jsx"
    ok, _ = _soft(ctx, "S7 write_jsx", lambda: export_ae.write_jsx(ctx.cutlist, ctx.plan, jsx, cfg))
    if not ok or not jsx.exists():
        return
    ctx.paths["jsx"] = str(jsx)
    for scenario in MOCK_SCENARIOS:
        ok, rec = _soft(ctx, f"S7 mock run ({scenario})",
                        lambda s=scenario: export_ae.run_jsx_in_mock(jsx, meta, scenario=s))
        ctx.mock[scenario] = rec if ok and rec is not None else {
            "status": "error", "error": (ctx.errors[-1]["error"] if ctx.errors else "no record")}
    dump_json(ctx.mock, cfg.work / "ae_mock_runs.json")
    ctx.ae_run = run_after_effects(ctx.env, jsx) if ctx.env.get("ae_app") else {
        "status": "not_available", "reason": f"After Effects not installed on this machine ({ctx.env.get('os')})"}
    if ctx.ae_run.get("status") == "ok":
        ctx.paths["aep"] = ctx.ae_run["aep"]
    elif ctx.ae_run.get("status") == "failed":
        ctx.warn(f"After Effects run failed: {ctx.ae_run.get('error') or str(ctx.ae_run.get('stderr', ''))[-300:]}")


def match_preview_usable(ctx: Context) -> bool:
    """preview_recreation.mp4 is itself a match-geometry render at competitor size and fps."""
    cfg = ctx.cfg
    if cfg.layout_mode != "match" or ctx.main_fps != ctx.comp_fps:
        return False
    req = parse_size(cfg.comp_size)
    if req is not None and tuple(req) != (int(ctx.cutlist.competitor["width"]), int(ctx.cutlist.competitor["height"])):
        return False
    p = ctx.paths.get("preview")
    return bool(p) and Path(p).exists()


def match_render_context(ctx: Context) -> Any:
    """render_preview.RenderContext for a match-geometry render at competitor size and fps."""
    from . import render_preview
    cl = ctx.cutlist
    return render_preview.make_context(cl, ctx.cfg, layout_mode="match",
                                       target_size=(int(cl.competitor["width"]), int(cl.competitor["height"])),
                                       fps=ctx.comp_fps)


def stage_exports(ctx: Context) -> None:
    from . import export_xml_edl, render_preview
    cfg, cl, out = ctx.cfg, ctx.cutlist, ctx.cfg.out
    csv, xml, edl = out / "cutlist.csv", out / "recreated_edit.xml", out / "recreated_edit.edl"
    _soft(ctx, "S8 cutlist.csv", lambda: export_xml_edl.write_csv(cl, csv))
    _soft(ctx, "S8 FCP7 XML", lambda: export_xml_edl.write_fcp7_xml(cl, xml, cfg))
    _soft(ctx, "S8 EDL", lambda: export_xml_edl.write_edl(cl, edl, cfg))
    for key, p in (("csv", csv), ("xml", xml), ("edl", edl)):
        if p.exists():
            ctx.paths[key] = str(p)
    if xml.exists() and edl.exists():
        ok, res = _soft(ctx, "S8 validate exports", lambda: export_xml_edl.validate_exports(cl, xml, edl))
        ctx.exports = res if ok and isinstance(res, dict) else {"ok": False, "error": "validation raised"}
        if ctx.exports.get("ok") is False:
            ctx.warn(f"XML/EDL re-parse validation failed: {ctx.exports.get('errors') or ctx.exports.get('error')}")
    if not cfg.skip_preview:
        prev = out / "preview_recreation.mp4"
        with _stage(ctx, "S8.preview"):
            ok, res = _soft(ctx, "S8 preview_recreation.mp4",
                            lambda: render_preview.render_preview(cl, ctx.raw_info.path, prev, cfg))
            ctx.preview = res if ok and isinstance(res, dict) else {}
        if prev.exists():
            ctx.paths["preview"] = str(prev)
    if not cfg.skip_compare:
        cmp_path = out / "compare.mp4"
        with _stage(ctx, "S8.compare"):
            if match_preview_usable(ctx):
                ok, src = True, ctx.paths["preview"]
            else:
                ok, src = _soft(ctx, "S8 match-geometry render context", lambda: match_render_context(ctx))
            if ok and src is not None:
                _soft(ctx, "S8 compare.mp4", lambda: render_preview.render_compare(ctx.comp_info.path, src, cl, cmp_path, cfg))
        if cmp_path.exists():
            ctx.paths["compare"] = str(cmp_path)


def stage_verify(ctx: Context) -> None:
    from . import verify
    try:
        ctx.verify = verify.verify_all(ctx)
    except Exception as e:  # noqa: BLE001 - a crashed verification is a failed verification
        log.error("verification crashed: %s\n%s", e, traceback.format_exc())
        ctx.verify = verify.crashed_result(f"{type(e).__name__}: {e}")
    p = ctx.cfg.out / "verify.json"
    dump_json(ctx.verify, p)
    ctx.paths["verify"] = str(p)
    ctx.dlog.record("verify", "criteria",
                    criteria={k: (v or {}).get("status") for k, v in ctx.verify.get("criteria", {}).items()},
                    checks={k: (v or {}).get("status") for k, v in ctx.verify.get("checks", {}).items()},
                    failures=ctx.verify.get("failures", [])[:50])
    for f in ctx.verify.get("failures", []):
        ctx.warn(f"verification: {f}")
    for w in (ctx.verify.get("checks", {}).get("s9_7_determinism") or {}).get("warnings", []):
        ctx.warn(w)


def stage_report(ctx: Context) -> None:
    from . import report
    p = ctx.cfg.out / "report.md"
    report.write_report(ctx, p)
    ctx.paths["report"] = str(p)


def _collect_paths(ctx: Context) -> None:
    out = ctx.cfg.out
    for key, rel in (("jsx", "build_ae_project.jsx"), ("aep", "recreated_edit.aep"), ("cutlist", "cutlist.json"),
                     ("csv", "cutlist.csv"), ("xml", "recreated_edit.xml"), ("edl", "recreated_edit.edl"),
                     ("preview", "preview_recreation.mp4"), ("compare", "compare.mp4"), ("report", "report.md"),
                     ("verify", "verify.json"), ("media", "media"), ("debug", "debug")):
        p = out / rel
        if p.exists():
            ctx.paths[key] = str(p)
    ctx.paths["decisions"] = str(ctx.cfg.work / "decisions.jsonl")
    ctx.paths["log"] = str(ctx.cfg.work / "match_cuts.log")
    ctx.paths["frame_map"] = str(ctx.cfg.work / "frame_map.npz")


def exit_code_for(criteria: dict, checks: dict | None = None) -> int:
    """0 only if nothing is 'fail': every criterion c1..c6 present and not failed, and no Stage 9 check
    (e.g. s9_7 determinism) failed."""
    from .verify import CRITERIA
    if not criteria or any(c not in criteria for c in CRITERIA):
        return 1
    if any((v or {}).get("status") == "fail" for v in criteria.values()):
        return 1
    return 1 if any((v or {}).get("status") == "fail" for v in (checks or {}).values()) else 0


def run(cfg: Config) -> dict:
    """Run S0..S10. Returns {criteria, checks, failures, warnings, paths, timings, exit_code, context}."""
    _guard_paths(cfg)
    _prepare_dirs(cfg)
    setup_logging(cfg.verbose, log_file=cfg.work / "match_cuts.log")
    log.info("match_cuts %s: competitor=%s raw=%s out=%s work=%s layout=%s comp_size=%s fps=%s", __version__,
             cfg.competitor, cfg.raw, cfg.out_dir, cfg.work_dir, cfg.layout_mode, cfg.comp_size, cfg.fps_mode)
    ctx = Context(cfg=cfg, dlog=DecisionLog(cfg.work / "decisions.jsonl", truncate=True), cache=Cache(cfg.work))
    stats = {p: _input_stat(p) for p in (cfg.competitor, cfg.raw)}
    seed_everything(cfg.seed)
    t_all = time.perf_counter()
    try:
        with _stage(ctx, "S0 env"):
            stage_env(ctx)
        with _stage(ctx, "S2 probe+conform"):
            stage_probe_conform(ctx)
        with _stage(ctx, "S3 audio"):
            stage_audio(ctx)
        with _stage(ctx, "S5.1 audio align"):
            stage_align(ctx)
        with _stage(ctx, "S3 proxies"):
            stage_proxies(ctx)
        with _stage(ctx, "S4 layout"):
            stage_layout(ctx)
        stage_visual_refine(ctx)
        with _stage(ctx, "S5.4-S6 segments+cutlist"):
            stage_segments(ctx)
        with _stage(ctx, "S7 AE project"):
            stage_ae(ctx)
        with _stage(ctx, "S8 exports"):
            stage_exports(ctx)
        with _stage(ctx, "S9 verify"):
            stage_verify(ctx)
        ctx.timings["total"] = round(time.perf_counter() - t_all, 3)
        write_cutlist(ctx)             # final timings; everything else unchanged since S6
        _collect_paths(ctx)
        with _stage(ctx, "S10 report"):
            stage_report(ctx)
    finally:
        for p, st in stats.items():
            if Path(p).exists() and _input_stat(p) != st:
                log.error("INPUT FILE CHANGED DURING THE RUN: %s", p)
        ctx.dlog.close()
    criteria = ctx.verify.get("criteria", {})
    code = exit_code_for(criteria, ctx.verify.get("checks", {}))
    return {"criteria": criteria, "checks": ctx.verify.get("checks", {}), "failures": ctx.verify.get("failures", []),
            "warnings": list(ctx.warnings), "paths": dict(ctx.paths), "timings": dict(ctx.timings),
            "exit_code": code, "context": ctx}


# ---------------------------------------------------------------------------------------------
# Fresh-context re-run for determinism (verify s9_7)
# ---------------------------------------------------------------------------------------------

def _readonly(a: np.ndarray | None) -> np.ndarray | None:
    if a is None:
        return None
    v = a.view()
    v.flags.writeable = False
    return v


def fresh_context_from_cache(ctx: Context, rerun_dir: Path | None = None) -> Context:
    """A new Context holding only cached analysis inputs of S5.4: probe results (deep copies), the
    read-only proxies/audio, layout from <work>/layout.json, overlays + FrameMap + AudioHints re-read
    from their cache files. No in-memory state of the first assembly is shared."""
    cfg = copy.deepcopy(ctx.cfg)
    rerun_dir = Path(rerun_dir or (cfg.work / "verify_rerun"))
    rerun_dir.mkdir(parents=True, exist_ok=True)
    new = Context(cfg=cfg, env=copy.deepcopy(ctx.env), dlog=DecisionLog(rerun_dir / "decisions.jsonl", truncate=True),
                  cache=Cache(cfg.work))
    for name in ("comp_input", "raw_input", "comp_info", "raw_info"):
        info = getattr(ctx, name)
        setattr(new, name, StreamInfo.from_dict(info.to_dict()) if info is not None else None)
    new.comp_conform = copy.deepcopy(ctx.comp_conform)
    new.raw_conform = copy.deepcopy(ctx.raw_conform)
    new.comp_proxy, new.raw_proxy = ctx.comp_proxy, ctx.raw_proxy       # read-only memmaps (cache files)
    new.comp_audio, new.raw_audio = _readonly(ctx.comp_audio), _readonly(ctx.raw_audio)
    new.audio_sr = ctx.audio_sr
    lay_path = cfg.work / "layout.json"
    new.layout = Layout.from_dict(json.loads(lay_path.read_text())) if lay_path.exists() else copy.deepcopy(ctx.layout)
    new.main_fps, new.main_size = ctx.main_fps, ctx.main_size
    new.analysis_warnings = list(ctx.analysis_warnings)
    new.keys = dict(ctx.keys)
    hk = ctx.keys.get("audio_align")
    hp = new.cache.path("audio_align", hk, ".npz") if hk else None
    new.hints = AudioHints.load(hp) if hp is not None and hp.exists() else copy.deepcopy(ctx.hints)
    fk = ctx.keys.get("frame_map")
    fm_path = new.cache.path("frame_map", fk, ".npz") if fk else None
    ov_path = new.cache.path("frame_map", fk, ".overlays.npz") if fk else None
    if fm_path is not None and fm_path.exists():
        new.fm_pre = FrameMap.load(fm_path)
        new.overlays = load_overlays(ov_path)
    else:
        new.fm_pre = ctx.fm_pre.copy()
        new.overlays = copy.deepcopy(ctx.overlays)
    return new


def rerun_assembly(ctx: Context, rerun_dir: Path | None = None) -> Cutlist:
    """Re-run S5.4 -> S6 from the caches in a fresh context; returns the new Cutlist."""
    new = fresh_context_from_cache(ctx, rerun_dir)
    try:
        _, _, _, cutlist = segment_and_assemble(new, new.fm_pre, new.dlog, Path(rerun_dir or (ctx.cfg.work / "verify_rerun")) / "debug")
    finally:
        new.dlog.close()
    return cutlist
