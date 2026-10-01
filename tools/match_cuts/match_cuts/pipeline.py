"""Pipeline orchestration S0..S10 (DESIGN.md §1, §2.5; prompt Stages 0-10).

``run(cfg)`` executes every stage in order, calling the stage modules strictly through their
DESIGN.md §5 signatures. Everything the stages produce is kept in one :class:`Context`, which
``verify.verify_all`` and ``report.write_report`` read.

Caching (DESIGN §1): the stage modules that take a ``cache`` cache themselves (probe, proxies,
layout, RawIndex, conform via ``.conform.json``). The pipeline caches the stages whose functions
do not take one -- AudioHints (``audio_align``), anchors (``sparse_search``) and the refined FrameMap
plus the pass-2 overlay masks (``frame_map``) -- under ``WORK_DIR/cache/<stage>/<key>`` with
``key = stage_key(stage, competitor hash, RAW hash, cfg.analysis_params())`` (anchors and FrameMap also
key on the layout geometry, its static-pixel mask and the starting overlay masks, on BOTH passes:
``visual_pass_key_parts``). Freshly computed
values are always written first and re-read from the cache file, so a first run and a cached
re-run see bit-identical inputs (criterion 9.7).

S5.4 -> S6 (segmentation, phase solve, audio per segment, audio-informed phase, cutlist assembly) is
never cached: it is what ``verify`` s9_7 re-runs from the cached FrameMap/AudioHints in a fresh context.

Decision log (DESIGN §7 D6): every cached stage's records are captured when it computes and stored in
``WORK_DIR/cache/decisions/<stage>-<key>.jsonl``; a cache hit replays them (``cached: true``), so a
re-run's ``decisions.jsonl`` holds the same evidence. The log of each run is copied to
``OUTPUT_DIR/debug/decisions.jsonl`` (the one the report links).

Also here: the D2 box refinement against RAW (re-running S5.2 + S5.3 once when the box changes), the
long-RAW proxy windows re-derived from the cached FrameMap on both cache branches, the D3 audio-informed
phase, the deliverables record (``ctx.exports``) and the D5 exit codes / headline.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import glob
import hashlib
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
from . import phase_solve as _ps
from .common import (STAGE_VERSION, Cache, DecisionLog, configure_pools, dump_json, ffmpeg_bin, ffprobe_bin, file_hash,
                     fmt_seconds, fps_str, json_default, limit_native_threads, load_decisions, log, null_dlog,
                     params_hash, save_decisions, seed_everything, setup_logging, stage_heartbeat, stage_key, timecode)
from .config import Config
from .geometry import Sim
from .model import (AudioHints, Cutlist, FrameMap, Layout, Segment, Status, StreamInfo, cutlist_layout)

CUTLIST_VERSION = 1
MOCK_SCENARIOS = ("default", "media_missing", "new_project_null", "no_marker_property")   # DESIGN §5 export_ae
MAIN_COMP_NAME = "Recreated Edit"
PHASE_TAU = 1e-6            # frame tolerance of the phase LP (DESIGN §2.1)
DEFAULT_SEG_AUDIO = {"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None,
                     "lag_ms": None, "corr": None, "exception": None,
                     "phase_source": None,      # 'audio' | 'video': what placed raw_in inside its interval (D3)
                     "lag_ms_video": None,      # lag at the video-only raw_in (lag_ms = residual after D3); both
                                                # relative to the run's A/V offset cutlist.audio.av_offset (D9)
                     "line": None}              # FX-14: the audio line this segment's audio follows instead of its
                                                # picture map (video-only retime / uncertain / placeholder), or None


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
    """Time a stage; long stages log progress / a heartbeat at least every cfg.progress_log_s (common.Progress,
    common.stage_heartbeat), so the console is never silent for long."""
    log.info("== %s", name)
    t0 = time.perf_counter()
    try:
        with stage_heartbeat(name):
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
        res = subprocess.run([binary, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30)
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
                 "av", "soundfile", "matplotlib", "opentimelineio", "scenedetect"):
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
            node_v = subprocess.run([node, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=30).stdout.strip()
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


def code_hash() -> str:
    """Hash of the package's own source files (match_cuts/*.py + ae_mock/*.js): the s9_7 comparison with a
    previous run only applies when the code is identical too (edits without a STAGE_VERSION bump would
    otherwise be reported as non-determinism)."""
    import hashlib
    root = Path(__file__).resolve().parent
    h = hashlib.blake2b(digest_size=10)
    for p in sorted(list(root.glob("*.py")) + list((root / "ae_mock").glob("*.js"))):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


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
                tie_frames=seg.tie_frames, dropped=dropped, **_slack_evidence(seg, comp_fps, raw_fps, cfg))
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
                violations=viol, **_slack_evidence(seg, comp_fps, raw_fps, cfg))
    return warnings


def _fill_phase_fields(seg: Segment, fm: FrameMap, res: dict, comp_fps: Fraction, raw_fps: Fraction, cfg: Config,
                       phase) -> list[str]:
    """Write a phase solution into the segment (9-decimal seconds), mark tie frames in the FrameMap,
    derive raw_in_frame / raw_out_frame with the AE rule. raw_in is re-placed over the WHOLE layer
    [comp_in, comp_out) inside the solved interval (floor∩round, else floor): the midpoint of the breakpoint
    cell with the most slack for every frame (FX-10; the solve only saw the frames up to its last
    constraint). ae_margin_ms = the exact floor-rule slack of the written raw_in over every frame."""
    warnings: list[str] = []
    v = float(seg.speed)
    raw_in = float(res["raw_in"])
    fl = res.get("interval_floor")
    both = res.get("interval_both")
    allowed = both or fl
    if allowed and v > 0 and float(allowed[1]) > float(allowed[0]) and \
            float(allowed[0]) - 1e-9 <= raw_in <= float(allowed[1]) + 1e-9:
        raw_in = float(_ps.place_raw_in(allowed, seg.comp_in, seg.comp_out, v, comp_fps, raw_fps,
                                        round_rule=bool(both))["raw_in"])
    seg.raw_in_seconds = fmt_seconds(raw_in)
    seg.raw_in_interval = [fmt_seconds(fl[0]), fmt_seconds(fl[1])] if fl else None
    seg.raw_in_interval_both = [fmt_seconds(both[0]), fmt_seconds(both[1])] if both else None
    ties = sorted({int(k) for k in (res.get("tie_frames") or [])} | {int(k) for k in seg.tie_frames})
    seg.tie_frames = ties
    for k in ties:
        if 0 <= k < fm.n:
            fm.tie[k] = True
    _refresh_phase_after_move(seg, fm, comp_fps, raw_fps, phase)
    if seg.ae_margin_ms is None and res.get("margin_ms") is not None:
        seg.ae_margin_ms = round(float(res["margin_ms"]), 6)
    # AE-rule safety is judged on the FINAL segments (after the audio-informed phase) in
    # flag_ae_rule_sensitive(); nothing to report here
    return warnings


def _group_shift_s(seg: Segment, c0: int, comp_fps: Fraction) -> float:
    """RAW seconds a time-line member's raw_in lies after the line's raw_in at c0: v·(comp_in − c0)/comp_fps."""
    return float(seg.speed) * float(Fraction(int(seg.comp_in) - int(c0)) / Fraction(comp_fps))


def time_line_groups(segments: list[Segment], dlog: DecisionLog | None = None) -> list[list[Segment]]:
    """The time-tied groups of the segmentation (``Segment.time_line``, FX-04 2): segments with the same group id
    that show ONE RAW time line. A group is used only as maximal runs of >= 2 ADJACENT forward stretch members at
    the same speed with a raw_in (a member the segmentation re-solved on its own, a gap or another speed splits
    it; logged). Returns the runs in competitor order."""
    by_id: dict[int, list[Segment]] = {}
    for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
        if getattr(s, "time_line", None) is not None:
            by_id.setdefault(int(s.time_line), []).append(s)
    out: list[list[Segment]] = []
    for gid in sorted(by_id):
        run: list[Segment] = []
        runs: list[list[Segment]] = []
        for s in by_id[gid]:
            ok = (s.type == "raw" and segment_time_mode(s) != "remap" and s.time_mode != "remap"
                  and s.raw_in_seconds is not None and s.speed is not None and math.isfinite(float(s.speed))
                  and float(s.speed) > 0 and bool(s.raw_in_interval))
            if ok and run and int(run[-1].comp_out) == int(s.comp_in) and float(run[-1].speed) == float(s.speed):
                run.append(s)
                continue
            if len(run) > 1:
                runs.append(run)
            run = [s] if ok else []
        if len(run) > 1:
            runs.append(run)
        if dlog is not None and (len(runs) != 1 or sum(len(r) for r in runs) != len(by_id[gid])):
            dlog.record("phase_solve", "time_line_split", time_line=gid,
                        members=[s.id for s in by_id[gid]], runs=[[s.id for s in r] for r in runs])
        out.extend(runs)
    return sorted(out, key=lambda r: (r[0].comp_in, r[0].id))


def _group_interval(group: list[Segment], comp_fps: Fraction) -> tuple[list[float] | None, list[float] | None]:
    """(floor, floor∩round) raw_in intervals of a time-line group at its first member's comp_in: the
    intersection of every member's own interval shifted back along the line (one shared solve gives the same
    interval up to the 9-decimal rounding; floor∩round only when every member has one)."""
    c0 = int(group[0].comp_in)
    fl = [-math.inf, math.inf]
    both: list[float] | None = [-math.inf, math.inf]
    for s in group:
        sh = _group_shift_s(s, c0, comp_fps)
        fl = [max(fl[0], float(s.raw_in_interval[0]) - sh), min(fl[1], float(s.raw_in_interval[1]) - sh)]
        if both is not None and s.raw_in_interval_both:
            both = [max(both[0], float(s.raw_in_interval_both[0]) - sh),
                    min(both[1], float(s.raw_in_interval_both[1]) - sh)]
        else:
            both = None
    if not (fl[1] > fl[0]):
        return None, None
    if both is not None and not (both[1] > both[0]):
        both = None
    return fl, both


def _set_group_raw_in(group: list[Segment], raw_in0: float, fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction,
                      phase) -> None:
    """Write one line's raw_in into every member: raw_in_i = raw_in0 + v·(comp_in_i − c0)/comp_fps as a 9-decimal
    value; each member's exact slack (ae_margin_ms) and tie frames are then recomputed over its own frames."""
    c0 = int(group[0].comp_in)
    for s in group:
        s.raw_in_seconds = fmt_seconds(float(raw_in0) + _group_shift_s(s, c0, comp_fps))
        _refresh_phase_after_move(s, fm, comp_fps, raw_fps, phase)


def place_time_lines(segments: list[Segment], fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction, cfg: Config,
                     dlog: DecisionLog, phase=None) -> list[str]:
    """Re-place every time-tied group as ONE line (DESIGN §5 segment.py time ties, §7.3): solve_segment_phase
    placed each member in the breakpoint cells of its OWN frames, so the members of one RAW line could drift
    apart by up to the interval width (two layers of one continuous clip showing different sub-frame phases).
    Here the line's raw_in at the first member's comp_in is the max-min-slack cell midpoint of EVERY frame of
    the whole group inside the members' common interval (floor∩round when all have it, else floor;
    ``phase_solve.place_raw_in``), and every member gets that line at its own comp_in (one common shift). Each
    member's exact slack is then checked on its own layer (ae_margin_ms). Returns warnings (none expected)."""
    if phase is None:
        from . import phase_solve as phase
    warnings: list[str] = []
    for group in time_line_groups(segments, dlog):
        c0, c1 = int(group[0].comp_in), int(group[-1].comp_out)
        v = float(group[0].speed)
        fl, both = _group_interval(group, comp_fps)
        ids = [s.id for s in group]
        if fl is None:
            warnings.append(f"time line S{ids[0]:02d}-S{ids[-1]:02d}: the members' raw_in intervals do not overlap; "
                            "each keeps its own phase")
            dlog.record("phase_solve", "time_line_skipped", segments=ids, reason="no common raw_in interval")
            continue
        allowed = both or fl
        before = [s.raw_in_seconds for s in group]
        p = _ps.place_raw_in(allowed, c0, c1, v, comp_fps, raw_fps, round_rule=bool(both))
        _set_group_raw_in(group, float(p["raw_in"]), fm, comp_fps, raw_fps, phase)
        dlog.record("phase_solve", "time_line_placed", segments=ids, comp_range=[c0, c1], speed=v,
                    interval=[round(allowed[0], 9), round(allowed[1], 9)], rule="both" if both else "floor",
                    raw_in=round(float(p["raw_in"]), 9), cell_half_frames=round(float(p["half"]), 12),
                    raw_in_before=before, raw_in_after=[s.raw_in_seconds for s in group],
                    ae_margin_ms=[s.ae_margin_ms for s in group])
    return warnings


def _slack_evidence(seg: Segment, comp_fps: Fraction, raw_fps: Fraction, cfg: Config) -> dict:
    """Decision-log fields of the AE floor-rule slack of a solved segment (FX-10)."""
    info = phase_slack(seg, comp_fps, raw_fps)
    if info is None:
        return {"ae_phase": "n/a"}
    return {"ae_phase": ae_phase_class(info, cfg), "ae_slack_frames": round(info["slack_frames"], 12),
            "ae_slack_k": info["k"], "ae_cell_half_frames": round(info["cell_half"], 12),
            "ae_best_slack_frames": None if info["best"] is None else round(info["best"], 12),
            "ae_video_pinned": info["video_pinned"]}


def time_line_spans(segments: list[Segment]) -> dict[int, tuple[int, int]]:
    """{segment id: (c0, c1)} the competitor range of the time line each time-tied member belongs to."""
    return {int(s.id): (int(g[0].comp_in), int(g[-1].comp_out)) for g in time_line_groups(segments) for s in g}


def phase_slack(seg: Segment, comp_fps: Fraction, raw_fps: Fraction,
                line: tuple[int, int] | None = None) -> dict | None:
    """AE floor-rule slack of a 'raw' stretch segment as written (DESIGN §2.1, FX-10), or None (no raw_in,
    remap / freeze / reverse: their keys are judged by the export).

    slack        exact minimum over EVERY frame of [comp_in, comp_out) of the distance of raw_fps·(raw_in +
                 v·(t_k − t_in)) to the nearest integer (RAW frames, Fraction; raw_in / v as written)
    k            the frame at that minimum
    cell_half    half the breakpoint cell (of every frame) around raw_in: the most slack any raw_in showing
                 exactly the same frames can have; cell_ms its width
    best         the most slack any raw_in of the solved interval (floor∩round, else floor) can have = half its
                 widest cell (None without an interval)
    video_pinned no breakpoint inside that interval: the measured frames pin raw_in to ONE cell

    ``line`` = (c0, c1): the segment is a member of a time-tied group spanning comp frames [c0, c1)
    (``time_line_spans``) whose raw_in follows the group's ONE line: the cell / best / pinned of that line
    (breakpoints of every frame of the group) decide, the slack stays the layer's own."""
    if seg.type != "raw" or seg.raw_in_seconds is None or segment_time_mode(seg) == "remap" or seg.time_mode == "remap":
        return None
    v = float(seg.speed)
    if not (math.isfinite(v) and v > 0) or seg.comp_out <= seg.comp_in:
        return None
    s, k = _ps.exact_min_slack(seg.raw_in_seconds, v, seg.comp_in, seg.comp_in, seg.comp_out, comp_fps, raw_fps)
    c0, c1, sh = int(seg.comp_in), int(seg.comp_out), 0.0
    if line is not None and int(line[0]) <= c0 and c1 <= int(line[1]) and (int(line[0]), int(line[1])) != (c0, c1):
        c0, c1 = int(line[0]), int(line[1])
        sh = _group_shift_s(seg, c0, comp_fps)
    lc = _ps.layer_cell(float(seg.raw_in_seconds) - sh, c0, c1, v, comp_fps, raw_fps)
    allowed = seg.raw_in_interval_both or seg.raw_in_interval
    best, video_pinned = None, False
    if allowed and float(allowed[1]) > float(allowed[0]):
        p = _ps.place_raw_in([float(allowed[0]) - sh, float(allowed[1]) - sh], c0, c1, v, comp_fps, raw_fps,
                             round_rule=bool(seg.raw_in_interval_both))
        best, video_pinned = float(p["best_half"]), bool(p["pinned"])
    return {"slack": s, "slack_frames": float(s), "slack_ms": float(s / Fraction(raw_fps)) * 1000.0, "k": int(k),
            "cell_half": float(lc["half"]), "cell_ms": (float(lc["cell"][1]) - float(lc["cell"][0])) * 1000.0,
            "best": best, "video_pinned": video_pinned}


def ae_phase_class(info: dict | None, cfg: Config) -> str:
    """'ok' (exact slack >= cfg.ae_slack_tol_frames); 'pinned': below it because the breakpoint cell raw_in
    lies in is itself narrower than 2 x the tolerance -- the frame-rate cadence pins the phase (by the
    measured frames, or by the audio in-point inside a seconds-wide static interval) and raw_in keeps at
    least half of that cell's slack: maximal information, not a risk once exported frame-exact; 'razor': below
    it although its cell allows more (raw_in next to a breakpoint of some frame -- the real run's S32 k594,
    S26 k440): a real razor-edge risk; 'n/a': no stretch phase."""
    if info is None:
        return "n/a"
    tol = float(getattr(cfg, "ae_slack_tol_frames", 0.01))
    s = float(info["slack"])
    if s >= tol:
        return "ok"
    h = float(info["cell_half"])
    if h < tol and s >= 0.5 * h:
        return "pinned"
    return "razor"


def ae_rule_sensitive(seg: Segment, cfg: Config, comp_fps: Fraction, raw_fps: Fraction,
                      line: tuple[int, int] | None = None) -> bool:
    """A real AE timing risk (FX-10): the exact floor-rule slack of the written raw_in is below
    cfg.ae_slack_tol_frames although its breakpoint cell allows more ('razor'), or the phase is pinned by
    the cadence but the configured --ae-time-mode keeps the layer in stretch / remap mode (no frame-exact
    export removes the risk). A pinned phase exported frame-exact is information, not a risk; no
    floor∩round overlap alone is not a risk either (AE samples with the floor rule). ``line``: see phase_slack
    (a time-tied member pinned by its group's line is pinned, not razor)."""
    cls = ae_phase_class(phase_slack(seg, comp_fps, raw_fps, line), cfg)
    if cls == "razor":
        return True
    return cls == "pinned" and str(getattr(cfg, "ae_time_mode", "auto") or "auto") in ("stretch", "remap")


def flag_ae_rule_sensitive(segments: list[Segment], cfg: Config, comp_fps: Fraction, raw_fps: Fraction) -> list[str]:
    """Tag the AE-rule-sensitive segments (``ae_rule_sensitive``) in their notes (and only them) and return
    ONE aggregated warning. Cadence-pinned phases are NOT warnings: the report lists them as information
    ('phase pinned by cadence (±0.083 ms)') and the export makes those layers frame-exact."""
    rows = []
    mode = str(getattr(cfg, "ae_time_mode", "auto") or "auto")
    tol = float(getattr(cfg, "ae_slack_tol_frames", 0.01))
    spans = time_line_spans(segments)
    for s in segments:
        s.notes = "; ".join(p for p in (s.notes or "").split("; ")
                            if p and p != "AE-rule-sensitive" and not p.startswith("AE-rule-sensitive ("))
        info = phase_slack(s, comp_fps, raw_fps, spans.get(int(s.id)))
        if ae_rule_sensitive(s, cfg, comp_fps, raw_fps, spans.get(int(s.id))):
            s.notes = _append_note(s.notes, f"AE-rule-sensitive (slack {info['slack_ms']:.6f} ms at frame {info['k']})")
            rows.append((s.id, info))
    if not rows:
        return []
    shown = ", ".join(f"S{i:02d} {inf['slack_ms']:.6f} ms @ {inf['k']}" for i, inf in rows[:12]) + \
        (f" (+{len(rows) - 12} more)" if len(rows) > 12 else "")
    how = ("exported frame-exact (time-remap HOLD keys at j + 0.25)" if mode in ("auto", "frames") else
           f"kept in {mode} mode as requested: After Effects may show a neighbouring RAW frame there "
           "(--ae-time-mode auto exports them frame-exact)")
    return [f"{len(rows)} segment(s) have an AE floor-rule slack below {tol:g} RAW frame at the written raw_in "
            f"({shown}); {how}"]


def _append_note(notes: str, extra: str) -> str:
    if not notes:
        return extra
    if extra in notes:
        return notes
    return f"{notes}; {extra}"


# ---------------------------------------------------------------------------------------------
# Audio-informed phase (S6, DESIGN §7 D3)
# ---------------------------------------------------------------------------------------------

AUDIO_PHASE_NARROW_S = 0.1       # analyze_segments_audio searches the per-segment lag within +-100 ms
AUDIO_PHASE_WIDE_MAX_S = 60.0    # cap of the half-width of the wider search for static / ambiguous segments
AUDIO_PHASE_WIDE_PAD_S = 0.02    # the wide search reaches this far past the feasible range on both sides
AUDIO_PHASE_MARGIN_FRAC = 0.05   # D3 margin: 5 % of the breakpoint cell (at least ae_slack_tol_frames, at most its half)
AUDIO_PHASE_WIDE_GAIN = 0.02     # a wide-search peak must beat a strong narrow peak by this much
AUDIO_PHASE_MIN_RANGE_S = 0.25   # shortest audio range the wide search correlates
_AE_EPS = 1e-9


def audio_phase_margin(cell_frames: float, tol_frames: float = 0.01) -> float:
    """Margin (RAW frames) the audio-informed raw_in keeps from the edges of its breakpoint cell (DESIGN §7
    D3, FX-10): ``min(cell / 2, max(5 % of the cell, tol_frames))``. Expressed in CELLS -- the pieces of the
    feasible interval between the floor-rule breakpoints of EVERY frame of the layer -- never in integer
    milliseconds: 1 ms is 24/1001 frame of a 23.976 source, so a raw_in 1.000 ms from a binding edge puts the
    frame 30 comp frames later exactly on a frame boundary (the real run's S26 k440). The tolerance gets
    phase_solve.TAU (1e-6 frame) on top so the 9-decimal rounding of raw_in (<= 3e-8 frame) cannot take the
    slack below it. A cell narrower than that gets its midpoint (the most slack it has)."""
    w = max(0.0, float(cell_frames))
    return min(w / 2.0, max(AUDIO_PHASE_MARGIN_FRAC * w, float(tol_frames) + _ps.TAU))


def audio_phase_interval(seg: Segment) -> tuple[list[float] | None, str]:
    """The interval the audio-informed raw_in must stay in: floor∩round when it exists, else floor."""
    if seg.raw_in_interval_both:
        return [float(seg.raw_in_interval_both[0]), float(seg.raw_in_interval_both[1])], "both"
    if seg.raw_in_interval:
        return [float(seg.raw_in_interval[0]), float(seg.raw_in_interval[1])], "floor"
    return None, ""


def _pre_segment_columns(fm: FrameMap) -> dict[str, np.ndarray]:
    """refine's own measurement (segment.py keeps it as 'pre_segment_*'; DESIGN §7 D4), else the
    current columns."""
    d = fm.__dict__["d"]
    return {k: np.asarray(d.get("pre_segment_" + k, d[k])) for k in ("status", "raw", "raw_lo", "raw_hi", "flip")}


def preserved_frames_interval(seg: Segment, fm: FrameMap, raw_in_s: float, comp_fps: Fraction, raw_fps: Fraction,
                              both: bool) -> tuple[float, float, int]:
    """raw_in range (seconds) that keeps every frame of the segment the current raw_in shows correctly
    (AE floor rule; with ``both`` also round-to-nearest) on a RAW frame inside refine's measured range
    [raw_lo, raw_hi]. Returns (lo_s, hi_s, frames used). Moving raw_in inside it never changes a frame
    that criterion 3 counts as exact."""
    k0, k1 = max(0, int(seg.comp_in)), min(fm.n, int(seg.comp_out))
    if k1 <= k0:
        return -math.inf, math.inf, 0
    cols = _pre_segment_columns(fm)
    ks = np.arange(k0, k1)
    rf = float(Fraction(raw_fps))
    u = float(seg.speed) * float(Fraction(raw_fps) / Fraction(comp_fps))
    d = (ks - int(seg.comp_in)).astype(np.float64)
    x = rf * float(raw_in_s)
    lo, hi = cols["raw_lo"][ks].astype(np.int64), cols["raw_hi"][ks].astype(np.int64)
    rw = cols["raw"][ks].astype(np.int64)
    lo = np.where(lo >= 0, np.minimum(lo, rw), rw)
    hi = np.where(hi >= 0, np.maximum(hi, rw), rw)
    meas = (cols["status"][ks] == Status.MATCH) & (cols["flip"][ks] == bool(seg.flip_h)) & (rw >= 0)
    j_f = np.floor(x + u * d + _AE_EPS)
    sel_f = meas & (j_f >= lo) & (j_f <= hi)
    lows = [lo[sel_f] - u * d[sel_f]]
    highs = [hi[sel_f] + 1.0 - u * d[sel_f] - 2 * _AE_EPS]
    n = int(sel_f.sum())
    if both:
        j_r = np.floor(x + u * d + 0.5)
        sel_r = meas & (j_r >= lo) & (j_r <= hi)
        lows.append(lo[sel_r] - 0.5 - u * d[sel_r])
        highs.append(hi[sel_r] + 0.5 - u * d[sel_r] - 2 * _AE_EPS)
    lo_all, hi_all = np.concatenate(lows), np.concatenate(highs)
    a = float(lo_all.max()) / rf if lo_all.size else -math.inf
    b = float(hi_all.min()) / rf if hi_all.size else math.inf
    return a, b, n


def _sliding_ncc(c: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Normalised correlation of ``c`` (length n) with every length-n window of ``r`` (both mean-removed
    per window): array of length len(r) - n + 1; entry o compares c[i] with r[o + i]."""
    import scipy.fft as sfft
    c = np.asarray(c, np.float64)
    r = np.asarray(r, np.float64)
    n, m = c.size, r.size
    if n < 2 or m < n:
        return np.zeros(0)
    c0 = c - c.mean()
    ec = float(np.dot(c0, c0))
    if ec <= 1e-18:
        return np.zeros(m - n + 1)
    N = sfft.next_fast_len(m + n, real=True)
    num = sfft.irfft(sfft.rfft(r, N) * np.conj(sfft.rfft(c0, N)), N)[: m - n + 1]
    cs = np.concatenate([[0.0], np.cumsum(r)])
    cs2 = np.concatenate([[0.0], np.cumsum(r * r)])
    s1, s2 = cs[n:] - cs[:-n], cs2[n:] - cs2[:-n]
    den = np.sqrt(np.maximum(s2 - s1 * s1 / n, 0.0) * ec)
    return np.where(den > 1e-12, num / np.maximum(den, 1e-300), 0.0)


def _segment_audio_range(seg: Segment, comp_fps: Fraction, sr: int, n_comp: int) -> tuple[int, int]:
    """Comp audio samples [a, b) of a segment's own audio (J/L offsets applied, crossfade overlaps
    excluded) -- the range analyze_segments_audio measures."""
    def tr(t: dict | None) -> int:
        return int(t.get("duration_frames") or 0) if t and str(t.get("type", "")) == "crossfade" else 0
    au = seg.audio or {}
    din, dout = tr(seg.transition_in), tr(seg.transition_out)
    k_a = seg.comp_in + (din if din else int(au.get("in_offset_frames") or 0))
    k_b = seg.comp_out - (dout if dout else -int(au.get("out_offset_frames") or 0))
    fr = Fraction(comp_fps)
    a = max(0, int(round(Fraction(int(k_a)) * sr / fr)))
    b = min(int(n_comp), int(round(Fraction(int(k_b)) * sr / fr)))
    return a, b


def wide_audio_lag(seg: Segment, comp_y: np.ndarray, raw_y: np.ndarray, sr: int, comp_fps: Fraction,
                   max_lag_s: float, resample: Callable | None = None,
                   centre_lag_s: float = 0.0) -> tuple[float, float] | None:
    """Lag (s, positive = the RAW-rebuilt audio is LATE, xcorr_lag's convention) and peak NCC of a
    stretch segment's rebuilt audio against the competitor within centre_lag_s +- max_lag_s (lags are
    relative to the segment's current raw_in; ``centre_lag_s`` centres the search on the feasible
    interval rather than on raw_in): the rebuilt track is rendered over the segment's audio range
    shifted by centre_lag_s and widened by max_lag_s on both sides, and the competitor range slides
    across it, so large lags keep the full overlap. The integer-sample peak is then refined on a
    lag-compensated render with ``audio_align.xcorr_lag`` (band-limited sub-sample peak; its NCC is the
    returned peak, so wide-band audio at a half-sample offset is not under-scored). None when the
    segment has too little audio."""
    from . import audio_align
    if resample is None:
        resample = audio_align.resample_at
    comp = np.asarray(comp_y, np.float32).reshape(-1)
    raw = np.asarray(raw_y, np.float32).reshape(-1)
    if comp.size == 0 or raw.size == 0 or seg.raw_in_seconds is None:
        return None
    a, b = _segment_audio_range(seg, comp_fps, sr, comp.size)
    if b - a < int(AUDIO_PHASE_MIN_RANGE_S * sr):
        return None
    L = int(round(max(0.0, float(max_lag_s)) * sr))
    v = float(seg.speed)
    t_in = float(Fraction(int(seg.comp_in)) / Fraction(comp_fps))
    cutoff = min(1.0, 1.0 / max(abs(v), 1e-6))

    def render(n0: int, n1: int, shift_s: float = 0.0) -> np.ndarray:
        t = np.arange(n0, n1, dtype=np.float64) / sr + shift_s
        return resample(raw, (float(seg.raw_in_seconds) + v * (t - t_in)) * sr, cutoff=cutoff)

    c0 = float(centre_lag_s)
    ncc = _sliding_ncc(comp[a:b], render(a - L, b + L, c0))
    if ncc.size == 0:
        return None
    i = int(np.argmax(ncc))
    off = 0.0
    if 0 < i < ncc.size - 1:
        ym, y0, yp = float(ncc[i - 1]), float(ncc[i]), float(ncc[i + 1])
        den = ym - 2.0 * y0 + yp
        if den < 0:
            off = float(np.clip(0.5 * (ym - yp) / den, -0.5, 0.5))
    lag = c0 + (i + off - L) / sr
    # rebuilt(t) ~ comp(t - lag)  =>  rebuilt(t + lag) ~ comp(t): measure what is left on that render
    delta, peak = audio_align.xcorr_lag(comp[a:b], render(a, b, lag), sr, 2.0 / sr + 1e-4)
    if peak >= float(ncc[i]):
        return lag + float(delta), float(np.clip(peak, -1.0, 1.0))
    return lag, float(np.clip(ncc[i], -1.0, 1.0))


def _refresh_phase_after_move(seg: Segment, fm: FrameMap, comp_fps: Fraction, raw_fps: Fraction, phase) -> None:
    """Derived phase fields after raw_in was (re)placed: raw_in/out frames (AE rule), ae_margin_ms = the
    exact floor-rule slack of the written raw_in over EVERY frame of the segment (ms, FX-10), timing-tie
    frames at the new raw_in."""
    v = float(seg.speed)
    seg.raw_in_frame = int(phase.ae_frame(seg.raw_in_seconds, v, seg.comp_in, seg.comp_in, comp_fps, raw_fps))
    seg.raw_out_frame = int(phase.ae_frame(seg.raw_in_seconds, v, seg.comp_out - 1, seg.comp_in, comp_fps, raw_fps))
    if math.isfinite(v) and v > 0 and seg.comp_out > seg.comp_in:
        sl, _k = _ps.exact_min_slack(seg.raw_in_seconds, v, seg.comp_in, seg.comp_in, seg.comp_out, comp_fps, raw_fps)
        seg.ae_margin_ms = round(float(sl / Fraction(raw_fps)) * 1000.0, 6)
    ks, lo, hi = segment_constraints(seg, fm)
    if len(ks):
        tie_slack = float(getattr(phase, "TIE_SLACK", 1e-4))
        u = v * float(Fraction(raw_fps) / Fraction(comp_fps))
        pos = float(Fraction(raw_fps)) * float(seg.raw_in_seconds) + u * (ks - int(seg.comp_in)).astype(np.float64)
        slack = np.minimum(pos - lo, hi + 1.0 - pos)
        new_ties = {int(k) for k in ks[(slack >= -PHASE_TAU) & (slack < tie_slack)]}
        if new_ties:
            seg.tie_frames = sorted(set(int(k) for k in seg.tie_frames) | new_ties)
            for k in new_ties:
                if 0 <= k < fm.n:
                    fm.tie[k] = True


def audio_informed_phase(segments: list[Segment], audio_result: dict, fm: FrameMap, comp_y: np.ndarray | None,
                         raw_y: np.ndarray | None, sr: int, comp_fps: Fraction, raw_fps: Fraction, cfg: Config,
                         dlog: DecisionLog, phase=None, resample: Callable | None = None,
                         av_offset_s: float = 0.0) -> tuple[list[int], list[str]]:
    """DESIGN §7 D3: pick raw_in inside its feasible interval from the sample-precise audio lag.

    The video phase solve leaves raw_in at the midpoint of a breakpoint cell (the centre of the floor∩round
    interval for exact frames), a quarter RAW frame after the frame boundary an NLE in-point sits on (8.3 ms
    at 30p, 10.4 ms at 24p). For every 'raw' stretch segment whose first-pass audio correlation is >=
    cfg.verify_audio_strong_corr with no audio exception, the target ``raw_in + v * residual`` (lag_ms = the
    residual lag after the run's A/V offset ``av_offset_s`` (D9); > 0 = the rebuilt audio is late, i.e.
    raw_in too small) is placed inside raw_in_interval_both (else raw_in_interval) ∩ the range that keeps
    every correctly shown matched frame (refine's measurement) on its RAW frame, in BREAKPOINT CELLS of
    every frame of the layer (FX-10, ``phase_solve.place_raw_in``): the cell containing the target, or the
    nearest one, and the target clamped to ``audio_phase_margin`` (5 % of that cell, at least the slack
    tolerance, at most its half) from its edges -- never an integer-millisecond margin, so no frame of the
    layer lands on a frame boundary (a cadence-narrow cell gets its midpoint: pinned by the audio). A target
    outside that video-feasible range by more than cfg.audio_lag_tol_ms (competitor time) does not move
    raw_in at all (the audio says nothing usable about the phase); those segments are listed in ONE
    run-level warning. Segments whose interval is wider than +-100 ms in competitor time (static /
    ambiguous-identical shots) also get a wider search, centred on that feasible range (+ the offset) and
    covering all of it (half-width capped at AUDIO_PHASE_WIDE_MAX_S).

    A TIME-TIED group (``time_line_groups``: segments showing one RAW line) moves as ONE line: its residual is
    the weighted mean (audio seconds x corr^2) of its confidently correlated members' residuals -- which must
    agree within cfg.audio_lag_tol_ms, else the group keeps its video phase --, the feasible range is the
    intersection of every member's range shifted along the line, the target is placed in the breakpoint cells
    of every frame of the whole group, and every member gets the same shift (the members never drift apart).

    Sets seg.audio['phase_source'] ('audio' when the audio target placed raw_in | 'video') and seg.audio['lag_ms_video'] (the
    first-pass residual); the caller re-runs analyze_segments_audio so lag_ms becomes the residual at the
    new raw_in. Returns (ids moved, warnings)."""
    if phase is None:
        from . import phase_solve as phase
    per = (audio_result or {}).get("segments") or {}
    measured = (audio_result or {}).get("_measured") or {}
    status = (audio_result or {}).get("status")
    strong = float(getattr(cfg, "verify_audio_strong_corr", 0.8))
    comp = np.zeros(0, np.float32) if comp_y is None else np.asarray(comp_y, np.float32).reshape(-1)
    raw = np.zeros(0, np.float32) if raw_y is None else np.asarray(raw_y, np.float32).reshape(-1)
    moved: list[int] = []
    warnings: list[str] = []
    far: list[tuple[list[int], float]] = []    # (segments, ms outside the feasible range): kept at the video phase
    tol_ms = float(getattr(cfg, "audio_lag_tol_ms", 10.0))
    slack_tol = float(getattr(cfg, "ae_slack_tol_frames", 0.01))
    rf_f = float(Fraction(raw_fps))

    def evaluate(s: Segment) -> dict:
        """The segment's own D3 inputs: its audio candidate (lag s, corr, how) or None + why; for a stretch
        segment with a phase ('struct') its interval and the preserved-frames range at its current raw_in."""
        au = {**DEFAULT_SEG_AUDIO, **(s.audio or {})}
        upd = per.get(s.id, per.get(str(s.id))) or {}
        au.update({k: v for k, v in upd.items() if k in ("lag_ms", "corr", "exception")})
        au["lag_ms_video"] = au.get("lag_ms")
        au["phase_source"] = "video"
        s.audio = au
        ev: dict[str, Any] = {"segment": s.id, "lag_ms_video": au.get("lag_ms"), "corr": au.get("corr"),
                              "exception": au.get("exception"), "raw_in_video": s.raw_in_seconds}
        out: dict[str, Any] = {"seg": s, "ev": ev, "cand": None, "why": None, "struct": False}
        # ('audio_replaced' runs still try the wide search below: a run whose every segment is a static shot
        # misplaced by > 100 ms looks replaced to the +-100 ms search; the strong-corr gate keeps genuinely
        # replaced audio out)
        no_audio = f"run audio status {status}" if status == "no_audio" else None
        v = float(s.speed) if s.speed is not None else float("nan")
        if segment_time_mode(s) == "remap" or s.time_mode == "remap" or not math.isfinite(v) or v <= 0:
            out["why"] = no_audio or "not a stretch segment"
            return out
        if s.raw_in_seconds is None:
            out["why"] = no_audio or "no raw_in"
            return out
        interval, kind = audio_phase_interval(s)
        if interval is None or interval[1] <= interval[0]:
            out["why"] = no_audio or "no feasible raw_in interval"
            return out
        old = float(s.raw_in_seconds)
        p_lo, p_hi, n_keep = preserved_frames_interval(s, fm, old, comp_fps, raw_fps, kind == "both")
        lo_e, hi_e = max(interval[0], p_lo), min(interval[1], p_hi)
        out.update(v=v, old=old, interval=interval, kind=kind, width=interval[1] - interval[0], p_lo=p_lo, p_hi=p_hi,
                   n_keep=n_keep, lo_e=lo_e, hi_e=hi_e, struct=True)
        if no_audio:
            out["why"] = no_audio
            return out
        exc = au.get("exception")
        if exc in ("not_in_raw", "no_audio", "pitch_preserved"):
            out["why"] = f"audio exception {exc}"
            return out
        if au.get("line"):
            out["why"] = "its audio follows an audio line, not its picture (FX-14)"
            return out
        cand = None
        if exc is None and au.get("lag_ms") is not None and au.get("corr") is not None and float(au["corr"]) >= strong:
            cand = (float(au["lag_ms"]) / 1000.0, float(au["corr"]), "xcorr")
        half_comp_s = 0.5 * out["width"] / v
        if half_comp_s > AUDIO_PHASE_NARROW_S and comp.size and raw.size:
            # centred on the reachable range (not on raw_in) and covering all of it: an in-point anywhere in
            # a wide ambiguous interval is found even when raw_in sits off-centre
            c_lo, c_hi = (lo_e, hi_e) if hi_e > lo_e else (interval[0], interval[1])
            centre_lag = (0.5 * (c_lo + c_hi) - old) / v + float(av_offset_s)
            max_lag = min(0.5 * (c_hi - c_lo) / v + AUDIO_PHASE_WIDE_PAD_S, AUDIO_PHASE_WIDE_MAX_S)
            wide = wide_audio_lag(s, comp, raw, sr, comp_fps, max_lag, resample=resample, centre_lag_s=centre_lag)
            if wide is not None:
                wide = (wide[0] - float(av_offset_s), wide[1])           # residual after the run's offset
            ev["wide_search"] = {"max_lag_s": round(max_lag, 6), "centre_lag_ms": round(centre_lag * 1000.0, 3),
                                 "lag_ms": None if wide is None else round(wide[0] * 1000.0, 3),
                                 "corr": None if wide is None else round(wide[1], 4)}
            if wide is not None and wide[1] >= strong and (cand is None or wide[1] > cand[1] + AUDIO_PHASE_WIDE_GAIN):
                cand = (wide[0], wide[1], "xcorr_wide")
        if cand is None:
            out["why"] = f"audio not confidently aligned (corr {au.get('corr')} < {strong} or exception {exc})"
        out["cand"] = cand
        return out

    def skip(e: dict, reason: str, **extra: Any) -> None:
        dlog.record("phase_solve", "audio_phase_skipped", reason=reason, **extra, **e["ev"])

    def place(c0: int, c1: int, v: float, lo_e: float, hi_e: float, target: float, kind: str) -> dict | str:
        """D3 placement of a target over the comp frames [c0, c1) (FX-10 cells, margin in cells), or why not."""
        if (hi_e - lo_e) * rf_f <= 2 * _ps.SLACK_MERGE:
            return f"feasible range {max(0.0, hi_e - lo_e) * 1000:.6f} ms leaves no room to move"
        pl = _ps.place_raw_in([lo_e, hi_e], c0, c1, v, comp_fps, raw_fps, target_s=target,
                              margin=lambda w: audio_phase_margin(w, slack_tol), round_rule=kind == "both")
        c_lo, c_hi = (float(x) for x in pl["cell"])
        cell_f = (c_hi - c_lo) * rf_f
        return {"raw_in": float(pl["raw_in"]), "cell": (c_lo, c_hi), "cell_f": cell_f,
                "margin_f": min(pl["half"], audio_phase_margin(cell_f, slack_tol))}

    groups = time_line_groups(segments)
    first_of = {id(g[0]): g for g in groups}
    in_group = {id(s) for g in groups for s in g}
    for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
        if s.type != "raw":
            continue
        if id(s) in in_group and id(s) not in first_of:
            continue                                   # handled with its time line's first member
        unit = first_of.get(id(s), [s])
        evals = [evaluate(m) for m in unit]
        if len(unit) == 1:
            e = evals[0]
            if e["cand"] is None:
                skip(e, e["why"])
                continue
            lag_s, corr, how = e["cand"]
            v, old, lo_e, hi_e = e["v"], e["old"], e["lo_e"], e["hi_e"]
            target = old + v * lag_s
            out_ms = max(lo_e - target, target - hi_e, 0.0) / v * 1000.0
            e["ev"].update(raw_in_audio_target=round(target, 9), outside_ms=round(out_ms, 3),
                           av_offset_ms=round(float(av_offset_s) * 1000.0, 3))
            if out_ms > tol_ms:
                # the audio implies an in-point the picture rules out: keep the video placement (never shrink the
                # AE margin towards an edge the audio does not actually reach)
                far.append(([int(s.id)], out_ms if target > hi_e else -out_ms))
                skip(e, f"audio target {out_ms:.1f} ms outside the video-feasible range (> {tol_ms:g} ms)")
                continue
            pl = place(s.comp_in, s.comp_out, v, lo_e, hi_e, target, e["kind"])
            if isinstance(pl, str):
                skip(e, pl)
                continue
            c_lo, c_hi = pl["cell"]
            new = fmt_seconds(pl["raw_in"])
            if not (c_lo < new < c_hi):          # the 9-decimal rounding left a cell narrower than 1 ns
                skip(e, f"rounded raw_in outside its {pl['cell_f']:.3g}-frame breakpoint cell")
                continue
            clamped = abs(new - target) > 5e-10
            s.audio["phase_source"] = "audio"
            rec = dict(e["ev"], raw_in=new, shift_ms=round((new - old) * 1000.0, 6),
                       lag_ms_used=round(lag_s * 1000.0, 3), corr_used=round(corr, 4), source=how, interval=e["kind"],
                       interval_s=[round(e["interval"][0], 9), round(e["interval"][1], 9)],
                       cell_s=[round(c_lo, 9), round(c_hi, 9)], cell_frames=round(pl["cell_f"], 9),
                       margin_frames=round(pl["margin_f"], 9), margin_ms=round(pl["margin_f"] / rf_f * 1000.0, 6),
                       preserved_range_s=[None if not math.isfinite(e["p_lo"]) else round(e["p_lo"], 9),
                                          None if not math.isfinite(e["p_hi"]) else round(e["p_hi"], 9)],
                       preserved_frames=e["n_keep"], clamped=clamped, speed=v)
            if abs(new - old) <= 5e-10:
                dlog.record("phase_solve", "audio_phase", moved=False, **rec)
                continue
            s.raw_in_seconds = new
            _refresh_phase_after_move(s, fm, comp_fps, raw_fps, phase)
            rec.update(raw_in_frame=s.raw_in_frame, raw_out_frame=s.raw_out_frame, ae_margin_ms=s.ae_margin_ms)
            dlog.record("phase_solve", "audio_phase", moved=True, **rec)
            moved.append(int(s.id))
            if how == "xcorr_wide":
                s.notes = _append_note(s.notes, f"raw_in placed by a wide audio search (lag {lag_s * 1000:+.1f} ms "
                                                f"inside a {e['width'] * 1000:.0f} ms feasible interval)")
            continue

        # ---- a time-tied group: ONE line, one common shift ------------------------------------------
        ids = [m.id for m in unit]
        live = evals
        if not all(e["struct"] for e in live):
            for e in live:
                skip(e, e["why"] if not e["struct"] else "a member of its time line has no stretch phase", time_line=ids)
            continue
        c0, c1 = int(unit[0].comp_in), int(unit[-1].comp_out)
        v = live[0]["v"]
        contrib = [e for e in live if e["cand"] is not None]
        if not contrib:
            for e in live:
                skip(e, e["why"] or "no member of its time line is confidently aligned", time_line=ids)
            continue
        lags = np.array([e["cand"][0] for e in contrib], np.float64)
        wts = []
        for e in contrib:
            mm = measured.get(e["seg"].id, measured.get(str(e["seg"].id))) or {}
            dur = float(mm.get("dur_s") or e["seg"].length / float(Fraction(comp_fps)))
            wts.append(dur * e["cand"][1] ** 2)
        wts = np.asarray(wts, np.float64)
        lag_s = float(np.dot(wts, lags) / wts.sum())
        spread_ms = float(lags.max() - lags.min()) * 1000.0
        members = [{"segment": e["seg"].id, "lag_ms": round(e["cand"][0] * 1000.0, 3), "corr": round(e["cand"][1], 4),
                    "source": e["cand"][2], "weight": round(float(w), 6)} for e, w in zip(contrib, wts)]
        if spread_ms > tol_ms:
            for e in live:
                skip(e, f"its time line's members disagree by {spread_ms:.1f} ms (> {tol_ms:g} ms): one line cannot "
                        "follow both", time_line=ids, members=members)
            continue
        both = all(e["kind"] == "both" for e in live)
        lo_e, hi_e = -math.inf, math.inf
        for e in live:
            m = e["seg"]
            sh = _group_shift_s(m, c0, comp_fps)
            if both:
                a, b = e["lo_e"], e["hi_e"]
            else:
                a_i, b_i = (float(x) for x in m.raw_in_interval)
                p_lo, p_hi, _n = preserved_frames_interval(m, fm, e["old"], comp_fps, raw_fps, False)
                a, b = max(a_i, p_lo), min(b_i, p_hi)
            lo_e, hi_e = max(lo_e, a - sh), min(hi_e, b - sh)
        old0 = live[0]["old"]
        target = old0 + v * lag_s
        out_ms = max(lo_e - target, target - hi_e, 0.0) / v * 1000.0
        grp = {"time_line": ids, "members": members, "lag_ms_used": round(lag_s * 1000.0, 3),
               "members_spread_ms": round(spread_ms, 3), "raw_in_audio_target": round(target, 9),
               "outside_ms": round(out_ms, 3), "av_offset_ms": round(float(av_offset_s) * 1000.0, 3)}
        if not (hi_e > lo_e) or out_ms > tol_ms:
            if hi_e > lo_e:
                far.append((ids, out_ms if target > hi_e else -out_ms))
            for e in live:
                skip(e, (f"audio target {out_ms:.1f} ms outside the time line's video-feasible range (> {tol_ms:g} ms)"
                         if hi_e > lo_e else "the time line's members have no common feasible range"), **grp)
            continue
        pl = place(c0, c1, v, lo_e, hi_e, target, "both" if both else "floor")
        if isinstance(pl, str):
            for e in live:
                skip(e, pl, **grp)
            continue
        c_lo, c_hi = pl["cell"]
        news = [fmt_seconds(pl["raw_in"] + _group_shift_s(m, c0, comp_fps)) for m in unit]
        if not all(c_lo < x - _group_shift_s(m, c0, comp_fps) < c_hi for x, m in zip(news, unit)):
            for e in live:
                skip(e, f"rounded raw_in outside the time line's {pl['cell_f']:.3g}-frame breakpoint cell", **grp)
            continue
        new0 = pl["raw_in"]
        did_move = abs(new0 - old0) > 5e-10
        if did_move:
            _set_group_raw_in(unit, new0, fm, comp_fps, raw_fps, phase)
        for e in live:
            m = e["seg"]
            m.audio["phase_source"] = "audio"
            rec = dict(e["ev"], **grp, raw_in=m.raw_in_seconds, shift_ms=round((new0 - old0) * 1000.0, 6), own_audio=e["why"],
                       source="time_line", interval="both" if both else "floor",
                       cell_s=[round(c_lo, 9), round(c_hi, 9)], cell_frames=round(pl["cell_f"], 9),
                       margin_frames=round(pl["margin_f"], 9), margin_ms=round(pl["margin_f"] / rf_f * 1000.0, 6),
                       clamped=abs(new0 - target) > 5e-10, speed=v)
            if did_move:
                rec.update(raw_in_frame=m.raw_in_frame, raw_out_frame=m.raw_out_frame, ae_margin_ms=m.ae_margin_ms)
            dlog.record("phase_solve", "audio_phase", moved=did_move, **rec)
            if did_move:
                moved.append(int(m.id))
    if far:
        def lab(ids: list[int]) -> str:
            return f"S{ids[0]:02d}" if len(ids) == 1 else f"S{ids[0]:02d}-S{ids[-1]:02d} (one time line)"
        shown = ", ".join(f"{lab(i)} {d:+.1f} ms" for i, d in far[:12]) + (f" (+{len(far) - 12} more)" if len(far) > 12 else "")
        n_far = sum(len(i) for i, _ in far)
        warnings.append(f"audio-informed phase (D3): {n_far} segment(s) keep their video phase because their audio "
                        f"in-point lies more than {tol_ms:g} ms outside the video-feasible interval after the run's "
                        f"A/V offset {float(av_offset_s) * 1000.0:+.1f} ms ({shown})")
        dlog.record("phase_solve", "audio_phase_far", segments=[x for i, _ in far for x in i],
                    outside_ms=[round(d, 3) for _, d in far], av_offset_ms=round(float(av_offset_s) * 1000.0, 3),
                    tol_ms=tol_ms)
    return moved, warnings


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
        if s.type == "uncertain":
            out.append(f"UNCERTAIN: comp frames {s.comp_in}-{s.comp_out - 1} "
                       f"({timecode(s.comp_in, comp_fps)}-{timecode(s.comp_out, comp_fps)}) - '{s.label}' "
                       "(neither matched nor NOT-IN-RAW: rebuild by hand from the guide layer)")
        elif s.uncertain:
            out.append(f"{name}: uncertain ({s.notes or 'see decisions.jsonl'})")
        if s.unsnapped and s.type == "raw":
            out.append(f"{name}: speed {s.speed:.4f} could not be snapped to a common value")
        if s.frame_mix:
            out.append(f"{name}: competitor used frame-blend retiming at speed {s.speed:g} - verified path, exported "
                       "with AE Frame Mix")
        elif s.retime and s.retime != "none":
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
        "ae_time_mode": cfg.ae_time_mode, "ae_slack_tol_frames": cfg.ae_slack_tol_frames,
        "criteria_exact": bool(main_fps == comp_fps),
        "audio_sync": str(getattr(cfg, "audio_sync", "raw") or "raw"),     # D9: raw | competitor (export audio)
    }
    audio_block = {k: v for k, v in (audio_result or {}).items()
                   if k not in ("segments", "added_audio") and not str(k).startswith("_")}
    audio_block.setdefault("status", "ok")
    audio_block.setdefault("notes", [])
    audio_block["analysis_sr"] = int(ctx.audio_sr)
    audio_block["competitor_has_audio"] = bool(ctx.comp_info.has_audio)
    audio_block["raw_has_audio"] = bool(ctx.raw_info.has_audio)
    if ctx.hints is not None and len(ctx.hints.comp_t):
        audio_block["hint_windows"] = int(len(ctx.hints.comp_t))
        audio_block["hint_windows_confident"] = int(ctx.hints.confident(cfg.audio_min_conf).sum())
    warnings: list[str] = []
    seg_warnings = [w for w in seg_warnings if "AE-rule-sensitive" not in w] + flag_ae_rule_sensitive(segments, cfg, comp_fps, raw_fps)
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
        "code_hash": code_hash(),
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


def rebase_segment_lags(segments: list[Segment], audio_result: dict, delta_ms: float) -> None:
    """Per-segment residual lags measured around one offset, re-expressed around another (DESIGN §7 D9):
    lag_ms += delta_ms (= old offset - new offset, ms) in audio_result and on the segments."""
    per = (audio_result or {}).get("segments", {}) or {}
    dicts = {id(d): d for d in list(per.values()) + [s.audio for s in segments if s.audio]}
    for d in dicts.values():
        if d.get("lag_ms") is not None:
            d["lag_ms"] = round(float(d["lag_ms"]) + float(delta_ms), 3)


def published_av_offset(offset: dict, audio_result: dict, cfg: Config) -> dict:
    """cutlist.audio.av_offset (DESIGN §7 D9): the run's measured offset (xcorr convention, ms), its
    interval and support, the audio switch baseline of the final pass and the export sync mode."""
    pub = {k: v for k, v in (offset or {}).items() if k != "lag_s"}
    sw = (audio_result or {}).get("_switch_baseline") or {}
    pub["switch_baseline_ms"] = sw.get("ms")
    pub["switch_baseline"] = {k: v for k, v in sw.items() if k != "ms"}
    pub["sync_mode"] = str(getattr(cfg, "audio_sync", "raw") or "raw")
    return pub


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
    # a time-tied group keeps ONE line: placed once over all its members' frames (each member above saw only its own)
    seg_warn.extend(place_time_lines(segments, fm, ctx.comp_fps, ctx.raw_fps, cfg, dlog))
    comp_y = ctx.comp_audio if ctx.comp_audio is not None else np.zeros(0, np.float32)
    raw_y = ctx.raw_audio if ctx.raw_audio is not None else np.zeros(0, np.float32)

    def analyse(offset_s: float, pass_name: str) -> dict:
        return audio_align.analyze_segments_audio(segments, comp_y, raw_y, ctx.audio_sr, ctx.comp_fps, cfg, dlog,
                                                  av_offset_s=offset_s, pass_name=pass_name) or {}
    # D9: the competitor's global A/V offset -- a prior from the S5.1 windows centres the first pass, the
    # precise offset comes from that pass; every later audio consumer works on residuals around it
    prior = audio_align.av_offset_prior(ctx.hints, segments, ctx.comp_fps, cfg, dlog)
    if not prior.get("accepted") and len(comp_y) and len(raw_y):
        # too few long S5.1 windows: one wide per-segment search instead (offsets beyond the +-100 ms residual search)
        probe = audio_align.av_offset_probe(segments, comp_y, raw_y, ctx.audio_sr, ctx.comp_fps, cfg, dlog)
        if probe.get("accepted"):
            prior = {**prior, **probe, "windows_reason": prior.get("reason")}
    g0 = float(prior["lag_s"])
    audio_result = analyse(g0, "video_phase")
    apply_segment_audio(segments, audio_result)
    offset = audio_align.av_offset_estimate(segments, audio_result, cfg, dlog, prior=prior)
    g = float(offset["lag_s"])
    if abs(g - g0) > 1e-3:
        # the first pass was centred elsewhere: redo it around g (its exceptions are judged on the residual)
        audio_result = analyse(g, "video_phase")
        apply_segment_audio(segments, audio_result)
    elif g != g0:
        rebase_segment_lags(segments, audio_result, (g0 - g) * 1000.0)
    # D3: audio-informed phase inside the video-feasible interval, then re-measure (lag_ms = residual)
    moved, warns = audio_informed_phase(segments, audio_result, fm, comp_y, raw_y, ctx.audio_sr, ctx.comp_fps,
                                        ctx.raw_fps, cfg, dlog, av_offset_s=g)
    seg_warn.extend(warns)
    if moved or audio_result.get("_av_offset_s", g) != g:
        audio_result = analyse(g, "audio_phase")
        apply_segment_audio(segments, audio_result)
        for s in segments:
            if s.id in moved:
                dlog.record("phase_solve", "audio_phase_residual", segment=s.id,
                            lag_ms_video=(s.audio or {}).get("lag_ms_video"), lag_ms=(s.audio or {}).get("lag_ms"),
                            corr=(s.audio or {}).get("corr"), raw_in_seconds=s.raw_in_seconds)
    audio_result["av_offset"] = published_av_offset(offset, audio_result, cfg)
    seg_warn.extend(layout_period_warnings(segments, ctx.layout, cfg.layout_mode, dlog))
    seg_warn.extend(segment_warnings(segments, ctx.comp_fps, audio_result))
    cutlist = build_cutlist(ctx, segments, audio_result, seg_warn)
    return fm, segments, audio_result, cutlist


LAYOUT_SLIVER_FRAMES = 2    # segment.merge_tiny joins a 1-2 frame sliver across a period boundary (D1)


def _transition_len(t: Any) -> int:
    if t is None:
        return 0
    d = t if isinstance(t, dict) else dict(vars(t))
    return int(d.get("duration_frames") or 0)


def transition_overlap_frames(seg: Segment, segments: list[Segment]) -> set[int]:
    """Frames of ``seg`` inside a DECLARED transition overlap with a neighbour of the other framing (one
    of the pair carries a per-segment box, the other none): the overlap [B.comp_in, A.comp_out) of a
    pair A -> B whose A.transition_out or B.transition_in lasts exactly that many frames (the overlap
    rule of verify's c1). During such a dissolve the competitor shows both framings, so a layout-period
    boundary detected inside it is not a framing error of either segment (export_ae keys the upper
    layer; review R2-5 / D1-c1-transition)."""
    out: set[int] = set()
    if seg.type != "raw":
        return out
    for nb in segments:
        # a RAW neighbour of the other framing, or a dip segment carrying the canvas box (the same neighbour
        # set as verify.boxless_fullscreen_frames, so the warning and c1 never disagree)
        if nb is seg or nb.type not in ("raw", "dip") or bool(nb.box) == bool(seg.box):
            continue
        for x, y in ((seg, nb), (nb, seg)):          # x outgoing, y incoming
            lo, hi = int(y.comp_in), int(x.comp_out)
            if not (int(x.comp_in) <= lo < hi <= int(y.comp_out)):
                continue
            if hi - lo in (_transition_len(x.transition_out), _transition_len(y.transition_in)):
                out.update(range(max(lo, int(seg.comp_in)), min(hi, int(seg.comp_out))))
    return out


def period_mismatch_frames(seg: Segment, a: int, b: int, segments: list[Segment],
                           fullscreen: list[tuple[int, int]] | None = None) -> tuple[list[int], dict]:
    """Frames of RAW segment ``seg`` whose framing contradicts the fullscreen period [a, b): a boxless
    segment's frames inside it, a boxed (whole-canvas) segment's frames outside it (and outside every
    other fullscreen period in ``fullscreen``, half-open ranges). Not counted (the rule of
    verify.boxless_fullscreen_frames, mirrored for boxed segments): frames inside a declared
    transition overlap with a neighbour of the other framing (``transition_overlap_frames``), and a
    merged sliver -- the remaining frames form ONE run of at most LAYOUT_SLIVER_FRAMES frames at the
    period boundary of a segment that continues across it and lies mostly on the correct side (the
    detected boundary is off by a frame or two). Returns (unexplained frames, exempt evidence)."""
    k0, k1 = int(seg.comp_in), int(seg.comp_out)
    if seg.box:
        spans = [(int(a), int(b))] + [(int(x), int(y)) for x, y in (fullscreen or [])]
        wrong = [k for k in range(k0, k1) if not any(x <= k < y for x, y in spans)]
    else:
        wrong = list(range(max(a, k0), min(b, k1)))
    if not wrong:
        return [], {}
    tr = transition_overlap_frames(seg, segments)
    rest = [k for k in wrong if k not in tr]
    sliver: list[int] = []
    runs = _ranges(rest)
    if len(runs) == 1 and len(rest) <= LAYOUT_SLIVER_FRAMES and (k1 - k0) - len(wrong) > len(wrong):
        r0, r1 = runs[0]
        if seg.box:      # outside the period, touching it, the segment continuing inside
            at_edge = (r1 == a - 1 and k1 > a) or (r0 == b and k0 < b)
        else:            # inside the period, touching its edge, the segment continuing outside
            at_edge = (r0 == a and k0 < a) or (r1 == b - 1 and k1 > b)
        if at_edge:
            sliver, rest = rest, []
    ev = {}
    if len(rest) + len(sliver) < len(wrong):
        ev["transition_frames"] = [[x, y] for x, y in _ranges([k for k in wrong if k in tr])]
    if sliver:
        ev["sliver_frames"] = [[x, y] for x, y in _ranges(sliver)]
    return rest, ev


def layout_period_warnings(segments: list[Segment], layout: Layout | None, layout_mode: str,
                           dlog: DecisionLog | None = None) -> list[str]:
    """Fullscreen periods are reproduced per segment (DESIGN §7 D1: segment.py gives the segments inside
    one ``box`` = the whole canvas; export_ae / render_preview place them in MAIN without the Video Box
    mask). Warn only where that cannot happen: a RAW segment overlapping a fullscreen period without its
    own box (it would be clipped to the Video Box) or a boxed one straddling the period boundary --
    except frames inside a declared transition overlap with a neighbour of the other framing and merged
    1-2 frame slivers at the boundary (``period_mismatch_frames``; the same rule as verify's c1), which
    are logged as explained."""
    out: list[str] = []
    if layout is None or layout_mode != "match":
        return out
    raw = sorted((s for s in segments if s.type == "raw"), key=lambda s: (s.comp_in, s.id))
    neighbours = sorted(segments, key=lambda s: (s.comp_in, s.id))   # raw + dip neighbours of transitions
    full = [(int(p.comp_in), int(p.comp_out)) for p in layout.periods if p.mode == "fullscreen"]
    boxed_seen: set[int] = set()      # a boxed segment spanning adjacent fullscreen periods is judged once
    for a, b in full:
        boxless: list[str] = []
        straddle: list[str] = []
        for s in raw:
            if not (s.comp_in < b and s.comp_out > a) or (s.box and a <= s.comp_in and s.comp_out <= b):
                continue
            if s.box:
                if int(s.id) in boxed_seen:
                    continue
                boxed_seen.add(int(s.id))
            bad, ev = period_mismatch_frames(s, a, b, neighbours, full)
            if ev and dlog is not None:
                dlog.record("layout", "period_boundary_explained", segment=s.id, period=[a, b - 1],
                            has_box=bool(s.box), unexplained=[[x, y] for x, y in _ranges(bad)], **ev)
            if not bad:
                continue
            (straddle if s.box else boxless).append(f"S{s.id:02d} (frames {_ranges_str(bad)})")
        if boxless:
            out.append(f"frames {a}-{b - 1} are fullscreen in the competitor but {', '.join(boxless)} carry no "
                       "per-segment box: exported inside the Video Box (the fullscreen shot is cropped)")
        if straddle:
            out.append(f"{', '.join(straddle)} straddle the fullscreen period {a}-{b - 1}: part of the segment is "
                       "shown with the wrong layout")
    return out


# ---------------------------------------------------------------------------------------------
# Pipeline-level caches (stages whose functions take no cache)
# ---------------------------------------------------------------------------------------------

def _analysis_key(ctx: Context, stage: str, *extra: Any) -> str:
    return stage_key(stage, ctx.comp_info.file_hash, ctx.raw_info.file_hash, ctx.cfg.analysis_params(), *extra)


# -- decision capture / replay across cache hits (DESIGN §7 D6) --------------------------------------

def decisions_path(ctx: Context, stage: str, key: str) -> Path:
    """work/cache/decisions/<stage>-<key>.jsonl: the records a cached stage emitted when it computed."""
    return ctx.cache.path("decisions", f"{stage}-{key}", ".jsonl")


def store_decisions(ctx: Context, stage: str, key: str, records: list[dict]) -> None:
    save_decisions(decisions_path(ctx, stage, key), records)


def replay_decisions(ctx: Context, stage: str, key: str, fresh: list[dict] | None = None) -> int:
    """Replay a cached stage's stored records into this run's log (cached=true, cache_key). Records the
    stage emitted again in this run (``fresh``, e.g. a measurement done before a nested cache hit) are not
    repeated. Returns the number replayed; a missing store (a cache entry written before D6) is logged,
    never fatal."""
    p = decisions_path(ctx, stage, key)
    if not p.exists():
        ctx.dlog.record("pipeline", "decisions_not_cached", step=stage, cache_key=key,
                        note="cache entry predates the decision store; only the cache hit is logged")
        return 0
    seen: dict[str, int] = {}
    for r in fresh or []:
        s = json.dumps(r, sort_keys=True)
        seen[s] = seen.get(s, 0) + 1
    todo = []
    for r in load_decisions(p):
        s = json.dumps(r, sort_keys=True)
        if seen.get(s):
            seen[s] -= 1
            continue
        todo.append(r)
    return ctx.dlog.replay(todo, cached=True, cache_key=key)


def _is_cache_hit(rec: dict, stages: tuple[str, ...] | None) -> bool:
    dec = str(rec.get("decision", ""))
    if not (dec == "cache_hit" or dec.endswith("_cache_hit")):
        return False
    return stages is None or str(rec.get("stage")) in stages


def self_cached_stage(ctx: Context, stage: str, key: str, fn: Callable[[], Any],
                      hit_stages: tuple[str, ...] | None = None) -> Any:
    """Run a stage whose module caches itself (layout, conform, box refinement): its records are
    captured; when the module reports a cache hit (a 'cache_hit' record of ``hit_stages``) the records
    stored when it last computed are replayed, else the captured records become the store."""
    with ctx.dlog.capture(stage) as cap:
        result = fn()
    if any(_is_cache_hit(r, hit_stages) for r in cap):
        replay_decisions(ctx, stage, key, fresh=list(cap))
    else:
        store_decisions(ctx, stage, key, list(cap))
    return result


def cached_hints(ctx: Context, compute: Callable[[], AudioHints]) -> AudioHints:
    key = _analysis_key(ctx, "audio_align", int(ctx.audio_sr))
    ctx.keys["audio_align"] = key
    p = ctx.cache.path("audio_align", key, ".npz")
    if not p.exists():
        with ctx.dlog.capture("audio_align") as cap:
            hints = compute()
            tmp = p.with_name(p.stem + ".tmp.npz")
            hints.save(tmp)
        store_decisions(ctx, "audio_align", key, list(cap))
        os.replace(tmp, p)
    else:
        log.info("audio hints: cache hit %s", p.name)
        ctx.dlog.record("audio_align", "cache_hit", key=key)
        replay_decisions(ctx, "audio_align", key)
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


def cached_anchors(ctx: Context, compute: Callable[[], list], *extra: Any) -> list:
    """Anchors of the sparse search, cached as JSON (``extra`` = additional key parts, e.g. the refined
    layout of the D2 second pass)."""
    from . import visual_match
    key = _analysis_key(ctx, "sparse_search", *extra)
    ctx.keys["sparse_search"] = key
    p = ctx.cache.path("sparse_search", key, ".json")
    if p.exists():
        ctx.dlog.record("visual_match", "cache_hit", key=key)
        replay_decisions(ctx, "sparse_search", key)
        rows = ctx.cache.json("sparse_search", key, lambda: [])
    else:
        with ctx.dlog.capture("sparse_search") as cap:
            rows = ctx.cache.json("sparse_search", key, lambda: [_anchor_to_dict(a) for a in compute()])
        store_decisions(ctx, "sparse_search", key, list(cap))
    return [_anchor_from_dict(r, visual_match.Anchor) for r in rows]


def layout_key(layout: Layout | None) -> str:
    """Hash of the layout that drives matching: its geometry (box, background, zones, periods, regions)
    and the CONTENT of its static-pixel mask (visual_match excludes those pixels from the box ROI) --
    file paths and notes excluded, so it does not depend on WORK_DIR. The overlay masks are keyed
    separately (``overlays_key``: the masks a pass starts from). Canonicalised through a JSON +
    ``Layout.from_dict`` round trip, so a freshly computed layout (ints, tuples, numpy scalars) and the
    same layout re-read from its cache file give the same key."""
    if layout is None:
        return "none"
    d = Layout.from_dict(json.loads(json.dumps(layout.to_dict(), default=json_default))).to_dict()
    for k in ("static_mask_file", "overlay_mask_file", "notes"):
        d.pop(k, None)
    sm = getattr(layout, "static_mask_file", "") or ""
    return params_hash(d, "static_mask", file_hash(sm) if sm and Path(sm).is_file() else "")


def overlays_key(overlays: Any) -> str:
    """Content hash of the overlay masks S5.2 / S5.3 start from: mask shape, default dilation, and every
    frame's full mask (packed bits) -- no file path, so it does not depend on WORK_DIR."""
    if overlays is None:
        return "none"
    frames = getattr(overlays, "frames", None)
    get = getattr(overlays, "get", None)
    if not callable(frames) or not callable(get):
        return params_hash("overlays", type(overlays).__name__)
    h = hashlib.blake2b(digest_size=10)
    h.update(json.dumps([getattr(overlays, "shape", None), getattr(overlays, "dilate_px", None)],
                        default=str).encode())
    for k in sorted(int(k) for k in frames()):
        m = get(k)
        if m is None:
            continue
        h.update(b"|%d|" % k)
        h.update(np.packbits(np.asarray(m, bool)).tobytes())
    return h.hexdigest()


def visual_pass_key_parts(layout: Layout | None, overlays: Any) -> tuple:
    """Extra cache-key parts of an S5.2 + S5.3 pass (frame_map and sparse_search keys): the layout
    geometry and the starting overlay masks. BOTH passes (the first one and the D2 refined-box re-run)
    key on them, so a layout that changes -- a new layout algorithm, a refined box, other overlays --
    never reuses a FrameMap or anchors matched against another box (review R2-3)."""
    return ("layout", layout_key(layout), "overlays", overlays_key(overlays))


def frame_map_cache_paths(ctx: Context, *extra: Any) -> tuple[Path, Path]:
    key = _analysis_key(ctx, "frame_map", *extra)
    ctx.keys["frame_map"] = key
    return ctx.cache.path("frame_map", key, ".npz"), ctx.cache.path("frame_map", key, ".overlays.npz")


def frame_map_windows(fm: FrameMap, raw_fps: Fraction, comp_fps: Fraction, n_raw: int, cfg: Config
                      ) -> list[tuple[int, int]]:
    """Dense RAW windows (half-open frame ranges) around every RAW frame the FrameMap references (best
    frame, ambiguous and soft ranges, refine's pre-segmentation measurement), padded by what
    segmentation and verification read beyond them (transition search, track radius, refine radius at up
    to 2x speed; >= 2 s). Derived from the CACHED FrameMap only, so a first run and a cached re-run
    expose exactly the same frames of a sparse long-RAW proxy (real-world F5)."""
    d = fm.__dict__["d"]
    cols = [d[k] for k in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi") if k in d]
    cols += [d[k] for k in ("pre_segment_raw", "pre_segment_raw_lo", "pre_segment_raw_hi") if k in d]
    js = np.unique(np.concatenate([np.asarray(c, np.int64).ravel() for c in cols])) if cols else np.zeros(0, np.int64)
    js = js[(js >= 0) & (js < int(n_raw))]
    if js.size == 0:
        return []
    rf, cf = float(Fraction(raw_fps)), float(Fraction(comp_fps))
    reach = int(getattr(cfg, "transition_search", 20)) + int(getattr(cfg, "track_search_radius", 8)) \
        + int(getattr(cfg, "refine_radius", 3)) + 2
    pad = int(math.ceil(max(2.0 * rf, 2.0 * reach * rf / cf)))
    starts = np.maximum(0, js - pad)
    ends = np.minimum(int(n_raw), js + pad + 1)
    wins: list[tuple[int, int]] = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        if wins and a <= wins[-1][1]:
            wins[-1] = (wins[-1][0], max(wins[-1][1], b))
        else:
            wins.append((a, b))
    return wins


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

    log.info("S7.6: opening After Effects (%s) to run %s -- waiting up to %.0f min for %s.",
             Path(app).name, jsx.name, timeout / 60.0, aep.name)
    log.info("      If After Effects shows a dialog (save the current project? / 'Allow Scripts to Write Files'), "
             "answer it there. Press Ctrl+C here to skip this step (the run continues); --no-ae disables it.")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as e:
        return {"status": "failed", "cmd": cmd, "error": str(e)}
    t0 = time.monotonic()
    deadline = t0 + timeout
    next_note = t0 + 60.0
    last_size = -1
    interrupted = False
    try:
        while time.monotonic() < deadline:
            if saved():
                size = aep.stat().st_size
                if size == last_size and size > 0:
                    break                          # written completely
                last_size = size
            elif proc.poll() is not None and env.get("os") == "Darwin":
                break                              # osascript returned without a saved project
            if time.monotonic() >= next_note:
                log.info("      still waiting for After Effects to save %s (%.0f s / %.0f s; Ctrl+C skips)",
                         aep.name, time.monotonic() - t0, timeout)
                next_note += 60.0
            time.sleep(poll_s)
    except KeyboardInterrupt:
        interrupted = True
        log.warning("S7.6: skipped by the user (Ctrl+C); After Effects is left open -- run %s there yourself "
                    "if it did not finish", jsx.name)
    ok = saved()
    if interrupted and not ok:
        return {"status": "not_available", "cmd": cmd, "reason": "skipped by the user (Ctrl+C)"}
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


def _conform_key(ctx: Context, info: StreamInfo) -> str:
    cfg = ctx.cfg
    return stage_key("conform", info.role, info.file_hash, str(Path(cfg.media_dir).resolve()), cfg.force_conform,
                     cfg.conform_codec, cfg.conform_h264_preset, cfg.conform_h264_crf, cfg.competitor_h264_preset,
                     cfg.competitor_h264_crf, cfg.large_file_bytes)


def surface_input_warnings(ctx: Context, probe_mod: Any) -> None:
    """probe.input_warnings(info) (e.g. a truncated / partially downloaded file whose decoded duration is
    far below the container's) for both inputs -> warnings that also go into cutlist.warnings and the
    report (real-world F8). Skipped when the probe module does not provide it."""
    fn = getattr(probe_mod, "input_warnings", None)
    if not callable(fn):
        return
    for info in (ctx.comp_input, ctx.raw_input):
        if info is None:
            continue
        try:
            found = list(fn(info) or [])
        except Exception as e:  # noqa: BLE001 - a diagnostics helper must not stop the run
            log.warning("probe.input_warnings(%s) failed: %s", info.path, e)
            continue
        for w in found:
            ctx.warn(f"{info.role} input {Path(info.path).name}: {w}", analysis=True)
            ctx.dlog.record("probe", "input_warning", role=info.role, path=info.path, warning=str(w))


def stage_probe_conform(ctx: Context) -> None:
    from . import conform, probe
    cfg = ctx.cfg
    work = str(cfg.work)
    ctx.comp_input = probe.probe(cfg.competitor, "competitor", work, decode=True)
    ctx.raw_input = probe.probe(cfg.raw, "raw", work, decode=True)
    for info in (ctx.comp_input, ctx.raw_input):
        ctx.dlog.record("probe", "input", role=info.role, path=info.path, fps=fps_str(info.fps),
                        frames=info.nb_frames, vfr=info.vfr, ae_issues=info.ae_issues, rotation=info.rotation)
    surface_input_warnings(ctx, probe)
    if ctx.comp_input.vfr:
        ctx.warn(f"competitor is VFR (PTS jitter {ctx.comp_input.pts_jitter:.2f} frames): the timeline is built on "
                 f"its nominal {fps_str(ctx.comp_input.fps)} fps (frame displayed at each t_k)", analysis=True)
    if ctx.raw_input.vfr:
        ctx.warn(f"RAW is VFR (PTS jitter {ctx.raw_input.pts_jitter:.2f} frames): conformed to CFR "
                 f"{fps_str(ctx.raw_input.fps)}", analysis=True)
    ctx.raw_conform = self_cached_stage(ctx, "conform_raw", _conform_key(ctx, ctx.raw_input),
                                        lambda: conform.conform(ctx.raw_input, "raw", cfg, ctx.dlog), ("conform",))
    ctx.comp_conform = self_cached_stage(ctx, "conform_competitor", _conform_key(ctx, ctx.comp_input),
                                         lambda: conform.conform(ctx.comp_input, "competitor", cfg, ctx.dlog),
                                         ("conform",))
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
    # the decision store of the self-cached layout stage: keyed like analyze_layout's own cache entry
    # (incl. LAYOUT_ALGO_VERSION), so a replay never mixes records of another layout algorithm
    key = stage_key("layout", ctx.comp_info.file_hash, ctx.cfg.analysis_params(),
                    "algo", getattr(layout_mod, "LAYOUT_ALGO_VERSION", 0))
    ctx.keys["layout"] = key
    ctx.layout, ctx.overlays = self_cached_stage(
        ctx, "layout", key,
        lambda: layout_mod.analyze_layout(ctx.comp_proxy, ctx.cfg, ctx.cache, ctx.cfg.debug_dir, ctx.dlog), ("layout",))
    for n in ctx.layout.notes:
        ctx.dlog.record("layout", "note", note=n)


def layout_warnings(ctx: Context) -> None:
    """Warnings about the (final, possibly RAW-refined) layout. Fullscreen periods are reproduced per
    segment (DESIGN §7 D1) and only logged; split-screen / PiP periods and extra regions are not
    reproduced (one region is recreated) and are warned."""
    lay = ctx.layout
    if lay is None:
        return
    if lay.extra_regions:
        ctx.warn(f"{len(lay.extra_regions)} extra video region(s) (split-screen / picture-in-picture) detected: "
                 "only the dominant region is recreated (see report)", analysis=True)
    for p in lay.periods:
        if p.mode in ("split", "pip"):
            ctx.warn(f"frames {p.comp_in}-{p.comp_out - 1}: {'split-screen' if p.mode == 'split' else 'picture-in-picture'}"
                     " layout: only the dominant region is recreated (After Effects cannot reproduce it from this "
                     "cutlist)", analysis=True)
    full = [[int(p.comp_in), int(p.comp_out)] for p in lay.periods if p.mode == "fullscreen"]
    if full:
        ctx.dlog.record("layout", "fullscreen_periods", periods=full,
                        handling="reproduced per segment: Segment.box = the whole canvas, placed in MAIN "
                                 "without the Video Box mask (DESIGN §7 D1)")


def _extend_raw(ctx: Context, base: Any, windows: list[tuple[int, int]]) -> Any:
    """A sparse (long-RAW) proxy extended by ``windows``; dense proxies are returned unchanged."""
    if base is None or getattr(base, "dense", True) or not windows:
        return base
    from . import proxies
    return proxies.extend_proxy(base, windows, ctx.cfg, ctx.cache)


def visual_refine_pass(ctx: Context, base_raw: Any, overlays_in: Any, label: str, *extra: Any) -> None:
    """One S5.2 + S5.3 pass (skipped on a FrameMap cache hit, whose decisions are replayed). The RAW
    proxy the later stages see is ``base_raw`` extended by windows derived from the CACHED FrameMap on
    BOTH branches, so a cached re-run exposes the same RAW frames as the first run (real-world F5)."""
    cfg = ctx.cfg
    fm_path, ov_path = frame_map_cache_paths(ctx, *extra)
    key = ctx.keys["frame_map"]
    if fm_path.exists() and ov_path.exists():
        log.info("frame map: cache hit %s", fm_path.name)
        ctx.dlog.record("refine", "cache_hit", key=key, frame_pass=label)
        replay_decisions(ctx, "frame_map", key)
    else:
        from . import refine, visual_match
        overlays = overlays_in
        with ctx.dlog.capture("frame_map") as cap:
            with _stage(ctx, f"S5.2 visual search{label}"):
                seed_everything(cfg.seed)
                ctx.index = visual_match.RawIndex.build(base_raw, cfg, ctx.cache)
                ctx.anchors = cached_anchors(ctx, lambda: visual_match.sparse_search(
                    ctx.comp_proxy, base_raw, ctx.layout, overlays, ctx.index, ctx.hints, cfg, ctx.dlog), *extra)
                ctx.dlog.record("visual_match", "summary", anchors=len(ctx.anchors))
                raw_for_refine = base_raw
                if not getattr(base_raw, "dense", True) and ctx.anchors:
                    wins = hint_windows(AudioHints.empty(), ctx.raw_fps, ctx.raw_info.nb_frames, cfg,
                                        extra_times=[a.raw / float(ctx.raw_fps) for a in ctx.anchors])
                    raw_for_refine = _extend_raw(ctx, base_raw, wins)
            with _stage(ctx, f"S5.3 refine{label}"):
                seed_everything(cfg.seed)
                fm = refine.build_frame_map(ctx.comp_proxy, raw_for_refine, ctx.layout, overlays, ctx.anchors,
                                            ctx.hints, ctx.index, cfg, ctx.cache, ctx.dlog, cfg.debug_dir)
                if fm.n != ctx.n_comp:
                    raise RuntimeError(f"FrameMap has {fm.n} rows for {ctx.n_comp} competitor frames")
        store_decisions(ctx, "frame_map", key, list(cap))
        save_frame_map_cache(fm, overlays, fm_path, ov_path)
    # always continue from the cache files (first run == cached re-run, bit for bit)
    ctx.fm_pre = FrameMap.load(fm_path)
    ctx.overlays = load_overlays(ov_path)
    if not getattr(base_raw, "dense", True):
        wins = frame_map_windows(ctx.fm_pre, ctx.raw_fps, ctx.comp_fps, ctx.raw_info.nb_frames, cfg)
        ctx.raw_proxy = _extend_raw(ctx, base_raw, wins)
        ctx.dlog.record("proxies", "frame_map_windows", frame_pass=label, n=len(wins), windows=wins[:200],
                        frames=int(sum(b - a for a, b in wins)))
    else:
        ctx.raw_proxy = base_raw


def refine_layout_from_raw(ctx: Context) -> bool:
    """DESIGN §7 D2: re-fit the box against the warped matched RAW (pixels that agree with RAW belong to
    the video region even when static). Adopts the returned layout; True when it changed materially
    (the caller then re-runs S5.2 + S5.3 once). Skipped when layout.refine_box_from_raw is missing; a
    crash keeps the temporal-analysis box with a warning."""
    try:
        from . import layout as layout_mod
    except ImportError:  # pragma: no cover - the stage modules ship together
        return False
    fn = getattr(layout_mod, "refine_box_from_raw", None)
    if not callable(fn) or ctx.layout is None or ctx.fm_pre is None:
        ctx.dlog.record("layout", "refine_box_skipped", reason="layout.refine_box_from_raw not available"
                        if not callable(fn) else "no layout / FrameMap")
        return False
    old = ctx.layout
    key = stage_key("layout_refine", ctx.keys.get("frame_map"), layout_key(old),
                    "algo", getattr(layout_mod, "LAYOUT_ALGO_VERSION", 0))
    ctx.keys["layout_refine"] = key
    try:
        res = self_cached_stage(ctx, "layout_refine", key, lambda: fn(
            old, ctx.overlays, ctx.comp_proxy, ctx.raw_proxy, ctx.fm_pre, ctx.cfg, ctx.cache, ctx.dlog,
            ctx.cfg.debug_dir))
    except Exception as e:  # noqa: BLE001 - the unrefined box is still a valid (if possibly small) layout
        log.error("box refinement against RAW failed: %s\n%s", e, traceback.format_exc())
        ctx.warn(f"box refinement against RAW failed ({type(e).__name__}: {e}); the box from the temporal "
                 "analysis is kept", analysis=True)
        ctx.dlog.record("layout", "refine_box_error", error=f"{type(e).__name__}: {e}")
        return False
    if isinstance(res, tuple) and len(res) == 2:
        new, changed = res
    else:
        new, changed = res, False
    if new is None:
        return False
    changed = bool(changed)
    ctx.layout = new
    ctx.dlog.record("pipeline", "box_refined_from_raw", changed=changed,
                    old_box=old.box.to_dict() if old.box else None, new_box=new.box.to_dict() if new.box else None,
                    old_background=old.background, new_background=new.background,
                    action="re-run S5.2 + S5.3 with the refined layout" if changed else "keep the FrameMap")
    return changed


def stage_visual_refine(ctx: Context) -> None:
    """S5.2 + S5.3, then the D2 box refinement against RAW; when the box changed materially S5.2 + S5.3
    run once more with the refined layout (its own cache keys)."""
    base_raw = ctx.raw_proxy                       # index frames + audio-hint windows (stage_proxies)
    overlays_pass1 = copy.deepcopy(ctx.overlays)   # layout's overlays, before refine adds residual masks
    visual_refine_pass(ctx, base_raw, ctx.overlays, "", *visual_pass_key_parts(ctx.layout, overlays_pass1))
    if refine_layout_from_raw(ctx):
        log.info("layout refined against RAW: re-running S5.2 + S5.3 with the corrected box")
        overlays_pass2 = initial_overlays(ctx.layout, overlays_pass1)
        visual_refine_pass(ctx, base_raw, overlays_pass2, " (refined box)",
                           *visual_pass_key_parts(ctx.layout, overlays_pass2))
    # later stages do not use the worker pool; free spawn workers (and their kd-trees) now
    try:
        from . import visual_match
        getattr(visual_match, "shutdown_workers", lambda: None)()
    except Exception:  # noqa: BLE001 - freeing memory early is best effort
        pass


def initial_overlays(layout: Layout, fallback: Any) -> Any:
    """The overlay masks S5.2 starts from for ``layout``: the re-analysed layout's own
    ``overlay_mask_file`` (layout.refine_box_from_raw writes new ones), else a copy of ``fallback``."""
    p = getattr(layout, "overlay_mask_file", "") or ""
    if p and Path(p).exists():
        try:
            return load_overlays(Path(p))
        except Exception as e:  # noqa: BLE001 - fall back to the first pass's initial masks
            log.warning("could not load the refined layout's overlay masks %s: %s", p, e)
    return copy.deepcopy(fallback)


def stage_segments(ctx: Context) -> None:
    cfg = ctx.cfg
    prev = cfg.out / "cutlist.json"
    if prev.exists():
        try:
            ctx.previous_cutlist = json.loads(prev.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ctx.previous_cutlist = None
    # the layout used by S5.4+ is persisted and re-read, so this run and the s9_7 re-run use the same object
    dump_json(ctx.layout.to_dict(), cfg.work / "layout.json")
    ctx.layout = Layout.from_dict(json.loads((cfg.work / "layout.json").read_text(encoding="utf-8")))
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
    if not ctx.env.get("ae_app"):
        ctx.ae_run = {"status": "not_available",
                      "reason": f"After Effects not installed on this machine ({ctx.env.get('os')})"}
    elif not getattr(cfg, "run_ae", True):
        ctx.ae_run = {"status": "not_available", "reason": "skipped (--no-ae): run build_ae_project.jsx in After Effects"}
    else:
        ctx.ae_run = run_after_effects(ctx.env, jsx, timeout=float(getattr(cfg, "ae_timeout_s", 600.0)))
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


DELIVERABLES = (   # (name, path under OUTPUT_DIR): the prompt's Deliverables tree, checked by s9_8
    ("jsx", "build_ae_project.jsx"), ("aep", "recreated_edit.aep"), ("cutlist", "cutlist.json"),
    ("csv", "cutlist.csv"), ("xml", "recreated_edit.xml"), ("edl", "recreated_edit.edl"),
    ("preview", "preview_recreation.mp4"), ("compare", "compare.mp4"), ("debug_mapping", "debug/mapping.png"),
    ("debug_scores", "debug/scores.png"), ("debug_layout", "debug/layout.png"))
DIAGNOSTIC_DELIVERABLES = ("debug_mapping", "debug_scores", "debug_layout")   # missing -> listed, never a failure


def stage_exports(ctx: Context) -> None:
    from . import export_xml_edl, render_preview
    cfg, cl, out = ctx.cfg, ctx.cutlist, ctx.cfg.out
    csv, xml, edl = out / "cutlist.csv", out / "recreated_edit.xml", out / "recreated_edit.edl"
    produced: dict[str, bool] = {}
    produced["csv"], _ = _soft(ctx, "S8 cutlist.csv", lambda: export_xml_edl.write_csv(cl, csv))
    produced["xml"], _ = _soft(ctx, "S8 FCP7 XML", lambda: export_xml_edl.write_fcp7_xml(cl, xml, cfg))
    produced["edl"], _ = _soft(ctx, "S8 EDL", lambda: export_xml_edl.write_edl(cl, edl, cfg))
    for key, p in (("csv", csv), ("xml", xml), ("edl", edl)):
        if produced[key] and p.exists():
            ctx.paths[key] = str(p)
    validation: dict = {"ok": False, "errors": ["XML/EDL not written: validation not run"]}
    if produced["xml"] and produced["edl"] and xml.exists() and edl.exists():
        ok, res = _soft(ctx, "S8 validate exports", lambda: export_xml_edl.validate_exports(cl, xml, edl))
        validation = res if ok and isinstance(res, dict) else {"ok": False, "errors": ["validation raised"]}
        if validation.get("ok") is not True:
            ctx.warn(f"XML/EDL re-parse validation failed: {validation.get('errors') or validation.get('error')}")
    ctx.exports = dict(validation)
    if not cfg.skip_preview:
        prev = out / "preview_recreation.mp4"
        with _stage(ctx, "S8.preview"):
            ok, res = _soft(ctx, "S8 preview_recreation.mp4",
                            lambda: render_preview.render_preview(cl, ctx.raw_info.path, prev, cfg))
            ctx.preview = res if ok and isinstance(res, dict) else {}
        produced["preview"] = ok
        if ok and prev.exists():
            ctx.paths["preview"] = str(prev)
    if not cfg.skip_compare:
        cmp_path = out / "compare.mp4"
        produced["compare"] = False
        with _stage(ctx, "S8.compare"):
            if match_preview_usable(ctx):
                ok, src = True, ctx.paths["preview"]
            else:
                ok, src = _soft(ctx, "S8 match-geometry render context", lambda: match_render_context(ctx))
            if ok and src is not None:
                produced["compare"], _ = _soft(ctx, "S8 compare.mp4", lambda: render_preview.render_compare(
                    ctx.comp_info.path, src, cl, cmp_path, cfg))
        if produced["compare"] and cmp_path.exists():
            ctx.paths["compare"] = str(cmp_path)
    ctx.exports.update(collect_deliverables(ctx, produced))


def collect_deliverables(ctx: Context, produced: dict[str, bool] | None = None) -> dict:
    """The prompt's deliverables after S7/S8 (REQ-6, DESIGN §7 D5): ``files`` {name: path | None (not
    produced by THIS run)}, ``skipped`` {name: reason} (explicitly skipped: --skip-preview/--skip-compare,
    the .aep when After Effects is not installed), ``missing`` [names; debug plots go to
    ``missing_diagnostics``], ``ok`` / ``validation_ok`` (the XML/EDL re-parse validation passed) and
    ``errors`` (its errors). report.md / verify.json are written after
    verification (a failure there is a run error, exit 2) and added to ``files`` by the pipeline later."""
    cfg, out = ctx.cfg, ctx.cfg.out
    produced = dict(produced or {})
    files: dict[str, str | None] = {}
    skipped: dict[str, str] = {}
    for name, rel in DELIVERABLES:
        p = out / rel
        ok = produced.get(name, True)
        if name == "jsx":
            ok = bool(ctx.paths.get("jsx"))
        elif name == "cutlist":
            ok = ctx.cutlist is not None
        elif name == "aep":
            st = (ctx.ae_run or {}).get("status")
            if st != "ok":
                if st in (None, "not_available"):
                    skipped[name] = (ctx.ae_run or {}).get("reason") or "After Effects not installed on this machine"
                files[name] = None
                continue
            p = Path(ctx.ae_run.get("aep") or p)
        elif name == "preview" and cfg.skip_preview:
            skipped[name] = "--skip-preview"
            files[name] = None
            continue
        elif name == "compare" and cfg.skip_compare:
            skipped[name] = "--skip-compare"
            files[name] = None
            continue
        files[name] = str(p) if ok and p.exists() else None
    for name, conf in (("media_raw", ctx.raw_conform), ("media_competitor", ctx.comp_conform)):
        mp = getattr(conf, "path", None) if conf is not None else None
        files[name] = str(mp) if mp and Path(mp).exists() else None
    missing = [k for k, v in files.items() if v is None and k not in skipped and k not in DIAGNOSTIC_DELIVERABLES]
    missing_diag = [k for k in DIAGNOSTIC_DELIVERABLES if files.get(k) is None]
    val_ok = ctx.exports.get("ok") is True if isinstance(ctx.exports, dict) else False
    errors = list((ctx.exports or {}).get("errors") or []) if not val_ok else []
    return {"files": files, "skipped": skipped, "missing": missing, "missing_diagnostics": missing_diag,
            "ok": val_ok, "validation_ok": val_ok, "errors": errors}


def deliverables_check(ctx: Context) -> dict:
    """Pipeline-side 's9_8_deliverables' (used when verify does not provide it): every deliverable exists
    unless explicitly skipped, the XML/EDL re-parse validation passed, no S7/S8 stage error."""
    ex = ctx.exports if isinstance(ctx.exports, dict) else {}
    files = ex.get("files") or {}
    skipped = ex.get("skipped") or {}
    missing = [k for k, p in files.items() if k not in skipped and k not in DIAGNOSTIC_DELIVERABLES
               and (not p or not Path(p).exists())]
    warns = [f"debug file missing: {k}" for k in DIAGNOSTIC_DELIVERABLES if k in files and not files.get(k)]
    fails = [f"deliverable missing: {k}" for k in missing]
    if not files:
        fails.append("no deliverables recorded (S7/S8 did not run)")
    if ex.get("ok") is not True:
        errs = list(ex.get("errors") or [])
        fails.append(f"XML/EDL validation did not pass: {'; '.join(map(str, errs[:5])) or 'not run'}")
    fails += [f"stage error: {e.get('stage')}: {e.get('error')}" for e in ctx.errors]
    n_ok = sum(1 for k, p in files.items() if p and k not in missing)
    summary = (f"{n_ok} deliverables present" + (f", skipped: {', '.join(sorted(skipped))}" if skipped else "")
               if not fails else f"{len(fails)} problem(s): {fails[0]}")
    return {"status": "fail" if fails else "pass", "summary": summary, "missing": missing,
            "skipped": dict(skipped), "failures": fails, "warnings": warns, "source": "pipeline"}


def stage_verify(ctx: Context) -> None:
    from . import verify
    try:
        ctx.verify = verify.verify_all(ctx)
    except Exception as e:  # noqa: BLE001 - a crashed verification is a failed verification
        log.error("verification crashed: %s\n%s", e, traceback.format_exc())
        ctx.verify = verify.crashed_result(f"{type(e).__name__}: {e}")
    checks = ctx.verify.setdefault("checks", {})
    if "s9_8_deliverables" not in checks:           # D5: deliverables count like every other check
        chk = deliverables_check(ctx)
        checks["s9_8_deliverables"] = chk
        if chk["status"] == "fail":
            ctx.verify.setdefault("failures", []).extend(f"s9_8 deliverables: {f}" for f in chk["failures"])
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
    # this run's decision log is copied next to its report (debug/decisions.jsonl) when the run ends, so
    # every report links its own evidence even when WORK_DIR is shared by several clip pairs
    ctx.paths["decisions"] = str(ctx.cfg.debug_dir / "decisions.jsonl")
    ctx.paths["log"] = str(ctx.cfg.work / "match_cuts.log")
    ctx.paths["frame_map"] = str(ctx.cfg.work / "frame_map.npz")


EXIT_PASS, EXIT_FAIL, EXIT_ERROR, EXIT_NOT_VERIFIED = 0, 1, 2, 3
OK_CRITERION_STATUSES = ("pass", "pass_with_exceptions")


def exit_code_for(criteria: dict, checks: dict | None = None) -> int:
    """DESIGN §7 D5: 0 = every criterion c1..c6 pass / pass_with_exceptions and no Stage 9 check (s9_7
    determinism, s9_8 deliverables, ...) failed; 1 = something failed (or a criterion is missing / has an
    unknown status); 3 = nothing failed but some criterion is not_available (e.g. criterion 6 without
    Node.js and After Effects). 2 (run error) is returned by the CLI when the run raises."""
    from .verify import CRITERIA
    if not criteria or any(c not in criteria for c in CRITERIA):
        return EXIT_FAIL
    st = [(criteria.get(c) or {}).get("status") for c in CRITERIA]
    if any((v or {}).get("status") == "fail" for v in criteria.values()):
        return EXIT_FAIL
    if any((v or {}).get("status") == "fail" for v in (checks or {}).values()):
        return EXIT_FAIL
    if any(s not in OK_CRITERION_STATUSES + ("not_available",) for s in st):
        return EXIT_FAIL
    return EXIT_NOT_VERIFIED if any(s == "not_available" for s in st) else EXIT_PASS


def _criterion_number(key: str) -> str:
    m = re.match(r"c(\d+)", str(key))
    return m.group(1) if m else str(key)


def headline_for(criteria: dict, checks: dict | None = None, code: int | None = None) -> str:
    """The overall verdict line (DESIGN §7 D5): 'PASS', 'PASS (criterion 6 not verified: <reason>)' or
    'FAIL' ('ERROR' for exit code 2)."""
    code = exit_code_for(criteria, checks) if code is None else int(code)
    if code == EXIT_PASS:
        return "PASS"
    if code == EXIT_ERROR:
        return "ERROR"
    if code == EXIT_NOT_VERIFIED:
        from .verify import CRITERIA
        na = [(k, criteria.get(k) or {}) for k in CRITERIA if (criteria.get(k) or {}).get("status") == "not_available"]
        nums = ", ".join(_criterion_number(k) for k, _ in na)
        reasons = "; ".join(str(c.get("summary") or "not available").strip()[:160] for _, c in na)
        return f"PASS ({'criterion' if len(na) == 1 else 'criteria'} {nums} not verified: {reasons})"
    return "FAIL"


def run(cfg: Config) -> dict:
    """Run S0..S10. Returns {criteria, checks, failures, warnings, paths, timings, exit_code, context}."""
    _guard_paths(cfg)
    _prepare_dirs(cfg)
    setup_logging(cfg.verbose, log_file=cfg.work / "match_cuts.log")
    limit_native_threads()                 # before the first FFT (DESIGN D7 fork hygiene)
    configure_pools(stall_s=cfg.pool_stall_timeout_s, progress_s=cfg.progress_log_s,
                    max_failures=cfg.pool_max_failures)
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
        layout_warnings(ctx)
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
        if isinstance(ctx.exports, dict) and isinstance(ctx.exports.get("files"), dict):
            for key in ("report", "verify"):
                ctx.exports["files"][key] = ctx.paths.get(key)
    finally:
        for p, st in stats.items():
            if Path(p).exists() and _input_stat(p) != st:
                log.error("INPUT FILE CHANGED DURING THE RUN: %s", p)
        ctx.dlog.close()
        copy_decision_log(cfg)
    criteria = ctx.verify.get("criteria", {})
    checks = ctx.verify.get("checks", {})
    code = exit_code_for(criteria, checks)
    return {"criteria": criteria, "checks": checks, "failures": ctx.verify.get("failures", []),
            "warnings": list(ctx.warnings), "paths": dict(ctx.paths), "timings": dict(ctx.timings),
            "exit_code": code, "headline": headline_for(criteria, checks, code), "context": ctx}


def copy_decision_log(cfg: Config) -> Path | None:
    """<out>/debug/decisions.jsonl = this run's complete decision log (REQ-5)."""
    src, dst = cfg.work / "decisions.jsonl", cfg.debug_dir / "decisions.jsonl"
    try:
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
            return dst
    except OSError as e:
        log.warning("could not copy the decision log to %s: %s", dst, e)
    return None


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
    new.layout = (Layout.from_dict(json.loads(lay_path.read_text(encoding="utf-8"))) if lay_path.exists()
                  else copy.deepcopy(ctx.layout))
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
