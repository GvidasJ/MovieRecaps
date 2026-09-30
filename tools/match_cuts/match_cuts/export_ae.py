"""Stage 7 -- After Effects project export (prompt Stage 7; DESIGN.md §2.3-2.5 and §5 export_ae).

``ae_plan``          every number the JSX sets, computed in Python: the single source of truth for the
                     JSX, for :func:`simulate_ae` and for verification. Times are integer MAIN frames plus
                     fps ``{num, den}``; the JSX computes ``t = k * den / num`` (one correctly rounded
                     division) and so does every simulation here, so the values are bit-identical.
``write_jsx``        ES3 ExtendScript (ASCII only, NaN-free) that builds ``Recreated Edit`` and saves
                     ``recreated_edit.aep`` next to itself.
``simulate_ae``      which RAW frame AE shows on every MAIN frame -- from a plan or from a mock-run record.
``run_jsx_in_mock``  runs the JSX through the ES3 gate and the strict Node AE mock (``ae_mock/``).
``fill_transform``   §2.4 fill-mode framing (shared with the preview renderer).

AE timing model used everywhere (mirrors the mock and, as far as documented, After Effects):
  * stretch mode: layer time ``lt = (t - startTime) * 100 / stretch``; RAW frame
    ``floor((t - startTime) * (100 / stretch) * raw_fps + 1e-9)``.
  * remap / frames mode: ``stretch = 100``, ``startTime = t_in``; Time Remap keys live in LAYER time;
    RAW frame ``floor(remap(lt) * raw_fps + 1e-9)``; frames mode = one HOLD key per MAIN frame at
    ``(m + 0.25) / raw_fps`` (centre of the floor-and-round set, immune to AE time quantisation).
"""
from __future__ import annotations

import bisect
import copy
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from .common import DecisionLog, fps_str, log, null_dlog, parse_fps, timecode
from .geometry import AETransform, Sim, sim_to_ae
from .model import Box, Cutlist, Segment

# ---------------------------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------------------------

AE_TIME_LIMIT_S = 10800.0      # AE layer times (startTime / inPoint / outPoint) must stay within +-3 h
AE_TIME_SAFE_S = 10799.0       # |startTime| above this -> time-remap mode for that layer (DESIGN §5)
AE_STRETCH_LIMIT = 9900.0      # AE Layer.stretch range (percent)
AE_EPS = 1e-9                  # tolerance of the AE floor rule (DESIGN §2.1)
KEY_EPS = 1e-9                 # seconds: a key "is at" time t when within this
ACTIVE_EPS = 1e-7              # seconds: in/out point tolerance when simulating a mock record
HOLD_PHASE = 0.25              # frames-mode key value (m + 0.25) / raw_fps
MIN_GAIN = 1e-3                # audio crossfade gain floor (-60 dB)
MAIN_COMP_NAME = "Recreated Edit"
BOX_COMP_NAME = "Video Box"
REFERENCE_NAME = "REFERENCE - competitor (turn on: black = match)"
PLACEHOLDER_RGB = [0.85, 0.1, 0.55]
TIME_MODES = ("auto", "stretch", "remap", "frames")
LAYOUT_MODES = ("match", "fill", "source")
MOCK_SCENARIOS = ("default", "media_missing", "new_project_null", "no_marker_property", "quantize_time")
DEFAULT_BLURRINESS = 50.0
GUIDE_OPACITY = 35.0
_GUIDE_RGB = {
    "header": [0.2, 0.6, 1.0], "logo": [0.2, 0.9, 0.9], "title": [1.0, 0.8, 0.1],
    "captions": [0.1, 1.0, 0.3], "watermark": [0.8, 0.5, 1.0], "sticker": [1.0, 0.5, 0.2],
    "progress": [1.0, 0.3, 0.3], "other": [0.7, 0.7, 0.7],
}

MOCK_DIR = Path(__file__).resolve().parent / "ae_mock"

# ES3 / ExtendScript bans (DESIGN §5 export_ae). Applied to the CODE with strings and comments removed.
_BAN_RE = re.compile(
    r"\.(forEach|map|filter|reduce|reduceRight|some|every|indexOf|lastIndexOf|trim|bind)\s*\("
    r"|\bJSON\b|Object\.(keys|create|defineProperty)|Array\.isArray|Date\.now")
_BAN_TOKENS_RE = re.compile(r"\b(let|const|NaN|Infinity)\b|=>|`")


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

def _fps_dict(fr: Fraction) -> dict:
    fr = Fraction(fr)
    return {"num": int(fr.numerator), "den": int(fr.denominator)}


def _t(k: int | float, F: dict) -> float:
    """Comp time of (possibly fractional) frame k -- the exact expression the JSX uses: k * den / num."""
    return k * F["den"] / F["num"]


def _num(x: Any, what: str) -> float:
    """Finite float or ValueError (the plan never carries None / NaN / Infinity)."""
    if x is None:
        raise ValueError(f"ae_plan: {what} is None")
    if isinstance(x, bool):
        raise ValueError(f"ae_plan: {what} is a bool, expected a number")
    try:
        f = float(x)
    except (TypeError, ValueError) as e:
        raise ValueError(f"ae_plan: {what} is not a number ({x!r})") from e
    if not math.isfinite(f):
        raise ValueError(f"ae_plan: {what} is not finite ({x!r})")
    return f


def _int(x: Any, what: str) -> int:
    f = _num(x, what)
    if f != int(f):
        raise ValueError(f"ae_plan: {what} must be an integer frame index ({x!r})")
    return int(f)


def _knum(k: float | int) -> float | int:
    """Key frame positions: ints stay ints in the plan (integral floats become ints)."""
    if isinstance(k, int):
        return k
    return int(k) if float(k).is_integer() else float(k)


_ASCII_MAP = {"–": "-", "—": "-", "‒": "-", "−": "-", "‐": "-", "‑": "-",
              "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
              "×": "x", " ": " ", "•": "*"}


def ascii_text(s: Any, limit: int = 200) -> str:
    """ASCII-only rendering of a label/name for AE (en dashes -> '-', accents stripped, others '?')."""
    s = str(s)
    for a, b in _ASCII_MAP.items():
        s = s.replace(a, b)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.encode("ascii", "replace").decode("ascii")
    s = "".join(c if (32 <= ord(c) < 127 or c == "\n") else " " for c in s)
    return s[:limit]


def _rgb(c: Any, default: Iterable[float] = (0.0, 0.0, 0.0)) -> list[float]:
    """'#rrggbb' / [r, g, b] (0..1 or 0..255) -> [r, g, b] floats in [0, 1]."""
    try:
        if isinstance(c, (list, tuple)) and len(c) >= 3:
            v = [float(x) for x in c[:3]]
            if max(v) > 1.0:
                v = [x / 255.0 for x in v]
            return [round(min(1.0, max(0.0, x)), 6) for x in v]
        s = str(c).strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) == 6:
            return [round(int(s[i:i + 2], 16) / 255.0, 6) for i in (0, 2, 4)]
    except (TypeError, ValueError):
        pass
    return [float(x) for x in default]


def parse_comp_size(value: Any) -> tuple[int, int] | None:
    """'1080x1920' -> (1080, 1920); 'competitor' / '' / None -> None."""
    if value is None or str(value).strip().lower() in ("", "competitor"):
        return None
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(value))
    if not m:
        raise ValueError(f"invalid comp size {value!r} (expected WxH or 'competitor')")
    w, h = int(m.group(1)), int(m.group(2))
    if not (4 <= w <= 30000 and 4 <= h <= 30000):
        raise ValueError(f"comp size {w}x{h} outside AE's [4, 30000] range")
    return w, h


def _interp(times: list[float], vals: list[float], holds: list[bool], x: float, eps: float = KEY_EPS) -> float:
    """AE key evaluation (keys sorted by time): value held before the first / after the last key;
    between keys LINEAR, or HOLD when the earlier key's out-interpolation is HOLD. A key within
    ``eps`` of x counts as reached."""
    i = bisect.bisect_right(times, x + eps) - 1
    if i < 0:
        return vals[0]
    if i >= len(times) - 1:
        return vals[-1]
    if holds[i]:
        return vals[i]
    t0, t1 = times[i], times[i + 1]
    if t1 <= t0:
        return vals[i + 1]
    return vals[i] + (x - t0) / (t1 - t0) * (vals[i + 1] - vals[i])


def _layer_time(t: float, start: float, stretch: float) -> float:
    """Layer time exactly as the mock stores keys: (t - startTime) * 100 / stretch."""
    return (t - start) * 100.0 / stretch


# ---------------------------------------------------------------------------------------------
# Geometry (§2.3 / §2.4)
# ---------------------------------------------------------------------------------------------

def box_geometry(box: Box | dict, r: float) -> dict:
    """Integer Video Box pre-comp geometry (DESIGN §2.3) for a CORNER-convention box and scale r.

    bx0 = floor(x r), by0 = floor(y r), bw = ceil((x+w) r) - bx0, bh = ceil((y+h) r) - by0; the rounded
    mask sits at (x r - bx0, y r - by0, w r, h r) with radius corner_radius r (pre-comp pixels)."""
    b = box if isinstance(box, Box) else Box.from_dict(box)
    x0, y0 = b.x * r, b.y * r
    x1, y1 = (b.x + b.w) * r, (b.y + b.h) * r
    bx0, by0 = math.floor(x0 + 1e-9), math.floor(y0 + 1e-9)
    bw, bh = math.ceil(x1 - 1e-9) - bx0, math.ceil(y1 - 1e-9) - by0
    if bw < 4 or bh < 4 or bw > 30000 or bh > 30000:
        raise ValueError(f"Video Box {bw}x{bh} outside AE's [4, 30000] comp size range (box {b}, r={r})")
    return {"bx0": int(bx0), "by0": int(by0), "bw": int(bw), "bh": int(bh),
            "mask": {"x": x0 - bx0, "y": y0 - by0, "w": b.w * r, "h": b.h * r,
                     "r": max(0.0, float(b.corner_radius) * r)}}


def fill_transform(sim: Sim, flip: bool, box: Box | dict | None, raw_wh: tuple[float, float],
                   target_wh: tuple[float, float]) -> Sim:
    """§2.4 fill mode: the canonical Sim (competitor px) -> a Sim into the full-screen target frame.

    The RAW point shown at the centre of the competitor's box goes to the frame centre; the zoom is
    ``s * cover_scale_frame / cover_scale_box`` (the competitor's zoom relative to the box's cover scale,
    applied on top of the frame's cover scale); then it is clamped so no empty edges show (minimum
    scale covering the frame at the segment's rotation, then the centre point clamped inside RAW).
    ``flip`` is carried through unchanged (the Sim acts on the flipped RAW, same size), so the result is
    used with the same flip flag. Rotation is kept."""
    if box is None:
        raise ValueError("fill_transform needs the competitor box (pass the full competitor frame for a "
                         "full-screen layout)")
    b = box if isinstance(box, Box) else Box.from_dict(box)
    W, H = float(raw_wh[0]), float(raw_wh[1])
    Wt, Ht = float(target_wh[0]), float(target_wh[1])
    if min(W, H, Wt, Ht, b.w, b.h) <= 0 or sim.s <= 0:
        raise ValueError("fill_transform: sizes and scale must be positive")
    cover_box = max(b.w / W, b.h / H)
    cover_frame = max(Wt / W, Ht / H)
    th = sim.theta
    c, sn = math.cos(th), math.sin(th)
    # RAW point (flipped space) at the box centre: p = R(-th) (bc - t) / s
    bcx, bcy = b.x + b.w / 2.0, b.y + b.h / 2.0
    dx, dy = bcx - sim.tx, bcy - sim.ty
    px, py = (c * dx + sn * dy) / sim.s, (-sn * dx + c * dy) / sim.s
    # half-extent of the target frame in RAW-aligned axes (rotation by -th)
    ax = (Wt / 2.0) * abs(c) + (Ht / 2.0) * abs(sn)
    ay = (Wt / 2.0) * abs(sn) + (Ht / 2.0) * abs(c)
    s_min = max(2.0 * ax / W, 2.0 * ay / H)
    s_new = max(sim.s * cover_frame / cover_box, s_min)
    lo_x, hi_x = ax / s_new, W - ax / s_new
    lo_y, hi_y = ay / s_new, H - ay / s_new
    px = W / 2.0 if lo_x > hi_x else min(max(px, lo_x), hi_x)
    py = H / 2.0 if lo_y > hi_y else min(max(py, lo_y), hi_y)
    tx = Wt / 2.0 - s_new * (c * px - sn * py)
    ty = Ht / 2.0 - s_new * (sn * px + c * py)
    return Sim(float(s_new), float(sim.theta_deg), float(tx), float(ty))


def _xf_dict(ae: AETransform, dx: float = 0.0, dy: float = 0.0) -> dict:
    return {"anchor": [float(ae.anchor[0]), float(ae.anchor[1])],
            "scale": [float(ae.scale[0]), float(ae.scale[1])],
            "rotation": float(ae.rotation),
            "position": [float(ae.position[0]) - dx, float(ae.position[1]) - dy],
            "keys": [], "rotKeys": False}


def _static_xf(anchor: tuple[float, float], position: tuple[float, float],
               scale: tuple[float, float] = (100.0, 100.0), rotation: float = 0.0) -> dict:
    return {"anchor": [float(anchor[0]), float(anchor[1])], "scale": [float(scale[0]), float(scale[1])],
            "rotation": float(rotation), "position": [float(position[0]), float(position[1])],
            "keys": [], "rotKeys": False}


def _check_sim(d: dict, what: str) -> Sim:
    if d is None:
        raise ValueError(f"ae_plan: {what} is None")
    s = _num(d.get("scale"), f"{what}.scale")
    if s <= 0:
        raise ValueError(f"ae_plan: {what}.scale must be > 0 ({s})")
    return Sim(s, _num(d.get("rotation_deg", 0.0), f"{what}.rotation_deg"),
               _num(d.get("tx"), f"{what}.tx"), _num(d.get("ty"), f"{what}.ty"))


# ---------------------------------------------------------------------------------------------
# ae_plan
# ---------------------------------------------------------------------------------------------

class _PlanBuilder:
    """Internal state of one ae_plan() call."""

    def __init__(self, cutlist: Cutlist, cfg: Any, footage_meta: dict | None, dlog: DecisionLog):
        from .config import Config
        self.cl = cutlist
        self.cfg = cfg if cfg is not None else Config()
        self.meta = footage_meta or {}
        self.dlog = dlog
        self.warnings: list[str] = []
        self.decisions: list[dict] = []

        self.layout_mode = str(getattr(self.cfg, "layout_mode", None) or cutlist.layout.get("mode") or "match")
        if self.layout_mode not in LAYOUT_MODES:
            raise ValueError(f"ae_plan: unknown layout mode {self.layout_mode!r}")
        self.fps_mode = str(getattr(self.cfg, "fps_mode", "competitor") or "competitor")
        self.time_mode_cfg = str(getattr(self.cfg, "ae_time_mode", "auto") or "auto")
        if self.time_mode_cfg not in TIME_MODES:
            raise ValueError(f"ae_plan: unknown ae_time_mode {self.time_mode_cfg!r} (expected {TIME_MODES})")
        self.min_margin_ms = float(getattr(self.cfg, "ae_min_margin_ms", 1.0))

        self.comp_fps = parse_fps(cutlist.competitor["fps"])
        self.raw_fps = parse_fps(cutlist.raw["fps"])
        self.main_fps = self.raw_fps if (self.fps_mode == "source" or self.layout_mode == "source") else self.comp_fps
        self.F = _fps_dict(self.main_fps)
        self.R = _fps_dict(self.raw_fps)
        self.CF = _fps_dict(self.comp_fps)
        self.mf = float(self.main_fps)
        self.rf = self.R["num"] / self.R["den"]
        self.Wc = _int(cutlist.competitor["width"], "competitor.width")
        self.Hc = _int(cutlist.competitor["height"], "competitor.height")
        self.Nc = _int(cutlist.competitor["frames"], "competitor.frames")
        self.raw_w, self.raw_h = _int(cutlist.raw["width"], "raw.width"), _int(cutlist.raw["height"], "raw.height")
        self.raw_frames = _int(cutlist.raw["frames"], "raw.frames")
        self.raw_dur = _t(self.raw_frames, self.R)
        self.N = self.to_main(self.Nc)
        if self.N < 1:
            raise ValueError("ae_plan: the competitor has no frames")

        # footage (RAW + competitor reference)
        self.raw_foot = self._footage(cutlist.raw, "raw", "Locate the RAW video", self.raw_fps, self.raw_w,
                                      self.raw_h, self.raw_frames)
        self.has_audio = bool(self.raw_foot["hasAudio"])
        ref_file = cutlist.competitor.get("file_rel") or cutlist.competitor.get("file") or cutlist.competitor.get("file_abs")
        self.ref_foot = (self._footage(cutlist.competitor, "ref", "Locate the COMPETITOR reference video",
                                       self.comp_fps, self.Wc, self.Hc, self.Nc) if ref_file else None)

        # MAIN size, r, Video Box
        req = parse_comp_size(getattr(self.cfg, "comp_size", "competitor"))
        self.box_model: Box | None = Box.from_dict(cutlist.layout["box"]) if cutlist.layout.get("box") else None
        self.r = 1.0
        if self.layout_mode == "source":
            self.W, self.H = self.raw_w, self.raw_h
        elif self.layout_mode == "fill":
            self.W, self.H = req if req else (1080, 1920)
        else:
            if req is None:
                self.W, self.H = self.Wc, self.Hc
            else:
                r = min(req[0] / self.Wc, req[1] / self.Hc)
                if abs(self.Wc * r - req[0]) > 1.0 or abs(self.Hc * r - req[1]) > 1.0:
                    raise ValueError(f"--comp-size {req[0]}x{req[1]} does not keep the competitor aspect "
                                     f"{self.Wc}x{self.Hc}; match layout needs a proportional size")
                self.W, self.H, self.r = req[0], req[1], r
        self.box_geo = box_geometry(self.box_model, self.r) if (self.layout_mode == "match" and self.box_model) else None
        self.seg_comp = "box" if self.box_geo else "main"
        self.seg_w = self.box_geo["bw"] if self.box_geo else self.W
        self.seg_h = self.box_geo["bh"] if self.box_geo else self.H
        self.fill_box = self.box_model or Box(0.0, 0.0, float(self.Wc), float(self.Hc))

    # -- time grid ----------------------------------------------------------------------------
    def to_main(self, k: int) -> int:
        """K = floor(k * main_fps / comp_fps + 1/2) (exact); identity when the grids agree (§2.5)."""
        if self.main_fps == self.comp_fps:
            return int(k)
        return math.floor(Fraction(int(k)) * self.main_fps / self.comp_fps + Fraction(1, 2))

    def to_main_f(self, k: float) -> float | int:
        """Fractional comp frame (keys) -> MAIN frame units, not rounded."""
        if self.main_fps == self.comp_fps:
            return _knum(k)
        return _knum(float(Fraction(k) * self.main_fps / self.comp_fps))

    def T(self, k: float) -> float:
        return _t(k, self.F)

    # -- bookkeeping ---------------------------------------------------------------------------
    def warn(self, msg: str) -> None:
        msg = ascii_text(msg, 400)
        if msg not in self.warnings:
            self.warnings.append(msg)
        log.warning("export_ae: %s", msg)

    def decide(self, decision: str, **evidence: Any) -> None:
        self.decisions.append({"decision": decision, **evidence})
        self.dlog.record("export_ae", decision, **evidence)

    def _footage(self, block: dict, role: str, prompt: str, fps: Fraction, w: int, h: int, frames: int) -> dict:
        rel = str(block.get("file_rel") or "")
        abs_ = str(block.get("file_abs") or "")
        f = str(block.get("file") or "")
        if not rel and f and not os.path.isabs(f):
            rel = f
        if not abs_ and f and os.path.isabs(f):
            abs_ = f
        rel = rel.replace("\\", "/")
        base = os.path.basename(rel or abs_)
        m = self.meta.get(base)
        has_audio = bool(block.get("has_audio", True))
        if m is not None:
            has_audio = bool(m.get("has_audio", has_audio))
            mfps = Fraction(int(m.get("fps_num", fps.numerator)), int(m.get("fps_den", fps.denominator)))
            if mfps != fps or int(m.get("width", w)) != w or int(m.get("height", h)) != h \
                    or abs(int(m.get("frames", frames)) - frames) > 1:
                self.warn(f"{base}: probed media ({m.get('width')}x{m.get('height')}, {fps_str(mfps)} fps, "
                          f"{m.get('frames')} frames) differs from cutlist.{role} ({w}x{h}, {fps_str(fps)}, {frames})")
        elif self.meta:
            self.warn(f"{base}: no probe metadata for the {role} media file")
        return {"role": role, "rel": rel, "abs": abs_, "base": ascii_text(base) if base else role,
                "w": int(w), "h": int(h), "fps": _fps_dict(fps), "frames": int(frames), "hasAudio": has_audio,
                "prompt": prompt}

    # -- layer templates -----------------------------------------------------------------------
    def _layer(self, **kw: Any) -> dict:
        L = {"id": "", "kind": "", "comp": self.seg_comp, "source": "solid", "seg": None, "name": "",
             "compIn": 0, "compOut": self.N, "timeMode": "still", "speed": None, "stretch": None,
             "rawIn": None, "startTime": 0.0, "startStretch": None, "inPoint": 0.0, "outPoint": 0.0,
             "expect": [], "remap": [], "flip": False, "xf": None, "opacity": [], "opacityValue": 100.0,
             "audioKeys": [], "enabled": True, "audio": False, "guide": False, "blend": "normal",
             "mask": None, "blur": None, "color": None, "w": None, "h": None,
             "aeRuleSensitive": False, "note": ""}
        L.update(kw)
        L["inPoint"] = self.T(L["compIn"])
        L["outPoint"] = self.T(L["compOut"])
        if L["xf"] is None:
            L["xf"] = _static_xf((0.0, 0.0), (0.0, 0.0))
        return L

    def _solid(self, lid: str, kind: str, name: str, color: list[float], k_in: int, k_out: int,
               comp: str | None = None, w: int | None = None, h: int | None = None,
               center: tuple[float, float] | None = None, **kw: Any) -> dict:
        comp = comp or self.seg_comp
        cw, ch = (self.seg_w, self.seg_h) if comp == self.seg_comp else (self.W, self.H)
        w = int(w if w is not None else cw)
        h = int(h if h is not None else ch)
        w, h = max(4, min(30000, w)), max(4, min(30000, h))
        cx, cy = center if center is not None else (cw / 2.0, ch / 2.0)
        return self._layer(id=lid, kind=kind, comp=comp, source="solid", name=ascii_text(name, 240),
                           compIn=int(k_in), compOut=int(k_out), color=[float(x) for x in color], w=w, h=h,
                           xf=_static_xf((w / 2.0, h / 2.0), (cx, cy)), **kw)

    # -- transforms ----------------------------------------------------------------------------
    def seg_xf(self, seg: Segment) -> dict:
        flip = bool(seg.flip_h)
        if self.layout_mode == "source":
            c = (self.raw_w / 2.0, self.raw_h / 2.0)
            return _static_xf(c, c)
        keys = sorted(seg.transform_keys or [], key=lambda d: float(d["comp_frame"]))
        base = _check_sim(seg.transform, f"segment {seg.id} transform") if seg.transform else None
        if base is None and not keys:
            raise ValueError(f"ae_plan: segment {seg.id} has neither transform nor transform_keys")
        if base is None:
            base = _check_sim(keys[0], f"segment {seg.id} transform_keys[0]")
        ox, oy = (self.box_geo["bx0"], self.box_geo["by0"]) if self.box_geo else (0, 0)

        def conv(sim: Sim) -> AETransform:
            if self.layout_mode == "fill":
                return sim_to_ae(fill_transform(sim, flip, self.fill_box, (self.raw_w, self.raw_h), (self.W, self.H)),
                                 flip, self.raw_w, self.raw_h, r=1.0)
            return sim_to_ae(sim, flip, self.raw_w, self.raw_h, r=self.r)

        dx, dy = (float(ox), float(oy)) if self.layout_mode == "match" else (0.0, 0.0)
        xf = _xf_dict(conv(base), dx, dy)
        if keys:
            out = []
            for i, kd in enumerate(keys):
                sim = _check_sim(kd, f"segment {seg.id} transform_keys[{i}]")
                d = _xf_dict(conv(sim), dx, dy)
                out.append({"k": self.to_main_f(_num(kd["comp_frame"], f"segment {seg.id} key comp_frame")),
                            "scale": d["scale"], "rotation": d["rotation"], "position": d["position"]})
            xf["keys"] = out
            xf["rotKeys"] = any(abs(k["rotation"] - out[0]["rotation"]) > 1e-12 for k in out)
            xf["scale"], xf["rotation"], xf["position"] = out[0]["scale"], out[0]["rotation"], out[0]["position"]
        return xf

    # -- RAW segment layers --------------------------------------------------------------------
    def raw_layers(self, seg: Segment, k_in: int, k_out: int) -> list[dict]:
        sid = int(seg.id)
        v = _num(seg.speed, f"segment {sid} speed")
        raw_in = _num(seg.raw_in_seconds, f"segment {sid} raw_in_seconds")
        if raw_in < 0:
            raise ValueError(f"ae_plan: segment {sid} raw_in_seconds {raw_in} < 0")
        err_s = float(Fraction(k_in) / self.main_fps - Fraction(int(seg.comp_in)) / self.comp_fps)
        raw_in_m = raw_in + v * err_s if err_s else raw_in
        remap_keys_in = list(seg.time_remap_keys or [])
        natural = "remap" if (remap_keys_in or v <= 0 or getattr(seg, "time_mode", "stretch") == "remap") else "stretch"
        F, mf, rf = self.F, self.mf, self.rf
        ks = range(k_in, k_out)

        if remap_keys_in:
            rk = sorted(({"k": self.to_main_f(_num(d["comp_frame"], f"segment {sid} time_remap_keys comp_frame")),
                          "v": _num(d["raw_seconds"], f"segment {sid} time_remap_keys raw_seconds")}
                         for d in remap_keys_in), key=lambda d: d["k"])
        else:
            rk = [{"k": k_in, "v": raw_in_m}, {"k": k_out, "v": raw_in_m + v * ((k_out - k_in) / mf)}]
        # ideal expectation (DESIGN §2.1 AE rule; identical float expression to phase_solve.ae_frame)
        if natural == "stretch":
            expect = [math.floor(rf * (raw_in_m + v * ((K - k_in) / mf)) + AE_EPS) for K in ks]
        else:
            kt = [float(d["k"]) for d in rk]
            kv = [d["v"] for d in rk]
            hold = [False] * len(rk)
            expect = [math.floor(_interp(kt, kv, hold, float(K), 1e-12) * rf + AE_EPS) for K in ks]

        stretch = 100.0 / v if v > 0 else None
        start_st = (self.T(k_in) - raw_in_m / (100.0 / stretch)) if stretch else None

        # time mode (DESIGN §5 export_ae: per layer; never chosen from `type`)
        forced = ""                                   # why the configured mode could not be used
        if natural == "remap":
            mode, reason = "remap", ("time_remap_keys" if remap_keys_in else f"speed {v:g} <= 0")
        elif abs(start_st) > AE_TIME_SAFE_S or stretch > AE_STRETCH_LIMIT:
            mode = "remap"
            reason = forced = (f"startTime {start_st:.3f} s / stretch {stretch:.3f} % outside AE's layer limits "
                               f"(+-{AE_TIME_LIMIT_S:.0f} s / {AE_STRETCH_LIMIT:.0f} %)")
        else:
            mode = "stretch" if self.time_mode_cfg == "auto" else self.time_mode_cfg
            reason = f"ae_time_mode={self.time_mode_cfg}"
        if self.time_mode_cfg == "frames":
            mode, reason = "frames", "ae_time_mode=frames"
        # a stretch layer running past the RAW end would be clamped by AE
        if mode == "stretch":
            ext_end = start_st + self.raw_dur * stretch / 100.0
            if self.T(k_out) > ext_end:
                if self.time_mode_cfg == "auto":
                    mode = "frames"
                    reason = forced = f"outPoint {self.T(k_out):.6f} s past the RAW end {ext_end:.6f} s (AE clamps)"
                else:
                    self.warn(f"S{sid:02d}: outPoint lies past the RAW end; AE will clamp it (the JSX self-check "
                              "then falls back to frame-exact remapping)")

        L = self._layer(id=f"seg{sid}", kind="raw", source="raw", seg=sid, compIn=k_in, compOut=k_out,
                        timeMode=mode, speed=v, stretch=stretch, rawIn=raw_in_m, startStretch=start_st,
                        expect=expect, remap=rk, flip=bool(seg.flip_h), xf=self.seg_xf(seg),
                        audio=self.has_audio)
        L["startTime"] = start_st if mode == "stretch" else self.T(k_in)
        # does the AE model of the chosen mode reproduce the expectation (floor rule, §2.1)?
        model = _layer_raw_frames(L, mode, F, self.R)
        bad = [K for K, a, e in zip(ks, model, expect) if a != e]
        if bad:
            if self.time_mode_cfg == "auto" and mode != "frames":
                self.decide("time_mode_fallback", segment=sid, from_mode=mode, to_mode="frames",
                            frames=bad[:20], n=len(bad))
                reason = forced = f"{mode} model differs from the AE rule on {len(bad)} frame(s)"
                mode = "frames"
                L["timeMode"] = mode
                L["startTime"] = self.T(k_in)
            else:
                self.warn(f"S{sid:02d}: the {mode} AE model differs from the expected RAW frame on {len(bad)} "
                          f"frame(s) (first {bad[0]}); re-export with --ae-time-mode frames")
        self.decide("time_mode", segment=sid, mode=mode, natural=natural, reason=reason, speed=v,
                    start_time=L["startTime"])
        if forced:
            self.warn(f"S{sid:02d}: exported with {'frame-exact ' if mode == 'frames' else ''}time remapping "
                      f"({forced})")

        # AE-rule-sensitive (small phase margin) -> report
        margin = getattr(seg, "ae_margin_ms", None)
        both = getattr(seg, "raw_in_interval_both", None)
        floor_iv = getattr(seg, "raw_in_interval", None)
        if mode in ("stretch", "remap") and v > 0 and (
                (margin is not None and math.isfinite(float(margin)) and float(margin) < self.min_margin_ms)
                or (floor_iv is not None and both is None)):
            L["aeRuleSensitive"] = True
            self.warn(f"S{sid:02d}: AE-rule-sensitive (phase margin "
                      f"{'n/a' if margin is None else f'{float(margin):.3f} ms'}); if AE shows a neighbouring "
                      "frame, re-export with --ae-time-mode frames")
        oob = [j for j in expect if j < 0 or j >= self.raw_frames]
        if oob:
            self.warn(f"S{sid:02d}: expected RAW frames outside [0, {self.raw_frames}) ({oob[0]}...); AE holds the "
                      "first/last RAW frame there")

        # name
        tc = timecode(max(0, expect[0]) if expect else 0, self.raw_fps)
        extra = ""
        if v == 0:
            extra = "  FREEZE"
        elif abs(v - 1.0) > 1e-9:
            extra = f"  x{v:.3f}"
        if seg.flip_h:
            extra += "  FLIP"
        if mode == "frames":
            extra += "  [frames]"
        L["name"] = ascii_text(f"S{sid:02d}  RAW {tc}{extra}", 240)
        L["note"] = ascii_text(reason, 300)

        out = [L]
        # audio: J/L duplicate or audio twin of a frames-mode layer (DESIGN §3 / §5)
        au = seg.audio or {}
        in_off = int(au.get("in_offset_frames") or 0)
        out_off = int(au.get("out_offset_frames") or 0)
        if self.has_audio and (in_off or out_off or mode == "frames"):
            a_in = max(0, self.to_main(int(seg.comp_in) + in_off))
            a_out = min(self.N, self.to_main(int(seg.comp_out) + out_off))
            a_mode = natural
            if a_mode == "stretch" and (abs(start_st) > AE_TIME_SAFE_S or stretch > AE_STRETCH_LIMIT):
                a_mode = "remap"
            a_keys = rk
            if a_mode == "stretch":
                lo_t, hi_t = start_st, start_st + self.raw_dur * stretch / 100.0
                a0, a1 = a_in, a_out
                a_in = max(a_in, math.ceil(lo_t * mf - 1e-6))
                while a_in < a_out and self.T(a_in) < lo_t:
                    a_in += 1
                a_out = min(a_out, math.floor(hi_t * mf + 1e-6))
                while a_out > a_in and self.T(a_out) > hi_t:
                    a_out -= 1
                if (a0, a1) != (a_in, a_out):
                    self.warn(f"S{sid:02d}: audio range clamped to the RAW extent ({a0}-{a1} -> {a_in}-{a_out})")
            elif not remap_keys_in:
                # extend the linear map over the audio range, never before RAW time 0
                if v < 0:
                    a_out = min(a_out, k_in + math.floor(raw_in_m * mf / -v + 1e-9))
                elif v > 0:
                    a_in = max(a_in, k_in - math.floor(raw_in_m * mf / v + 1e-9))
                a_keys = [{"k": a_in, "v": raw_in_m + v * ((a_in - k_in) / mf)},
                          {"k": a_out, "v": raw_in_m + v * ((a_out - k_in) / mf)}]
            if a_out > a_in:
                why = "J/L cut" if (in_off or out_off) else "audio of a frame-exact layer"
                # the audio twin uses the same (raw_in, v) map; placeStretch pins its rawIn at its own compIn
                a_raw_in = raw_in_m + v * ((a_in - k_in) / mf)
                a_start = (self.T(a_in) - a_raw_in / (100.0 / stretch)) if a_mode == "stretch" else None
                A = self._layer(id=f"seg{sid}_audio", kind="raw_audio", source="raw", seg=sid, compIn=a_in,
                                compOut=a_out, timeMode=a_mode, speed=v, stretch=stretch if a_mode == "stretch" else None,
                                rawIn=a_raw_in, startStretch=a_start,
                                startTime=a_start if a_mode == "stretch" else self.T(a_in),
                                expect=[], remap=a_keys, flip=bool(seg.flip_h), xf=copy.deepcopy(L["xf"]), enabled=False,
                                audio=True, name=ascii_text(f"S{sid:02d}  audio ({why})", 240), note=why)
                out.append(A)
                L["audio"] = False
                self.decide("audio_duplicate", segment=sid, reason=why, comp_in=a_in, comp_out=a_out, mode=a_mode)
        return out


def _layer_raw_frames(L: dict, mode: str, F: dict, R: dict) -> list[int]:
    """RAW frame per MAIN frame [compIn, compOut) of a RAW layer under a time mode, evaluated with the
    exact float expressions of the JSX and the mock (bit-identical)."""
    rf = R["num"] / R["den"]
    k0, k1 = int(L["compIn"]), int(L["compOut"])
    out: list[int] = []
    if mode == "stretch":
        start, st = L["startStretch"], L["stretch"]
        if start is None or st is None:
            raise ValueError(f"layer {L['id']}: stretch mode needs speed > 0")
        for K in range(k0, k1):
            out.append(math.floor((_t(K, F) - start) * (100.0 / st) * rf + AE_EPS))
        return out
    start = _t(k0, F)
    if mode == "remap":
        keys = L["remap"]
        times = [_layer_time(_t(d["k"], F), start, 100.0) for d in keys]
        vals = [float(d["v"]) for d in keys]
        holds = [False] * len(keys)
    elif mode == "frames":
        exp = L["expect"]
        times = [_layer_time(_t(k0 + i, F), start, 100.0) for i in range(len(exp))]
        vals = [(e + HOLD_PHASE) * R["den"] / R["num"] for e in exp]
        holds = [True] * len(exp)
    else:
        raise ValueError(f"unknown time mode {mode!r}")
    for K in range(k0, k1):
        val = _interp(times, vals, holds, _layer_time(_t(K, F), start, 100.0))
        out.append(math.floor(val * rf + AE_EPS))
    return out


def ae_plan(cutlist: Cutlist, cfg: Any, footage_meta: dict | None, *, dlog: DecisionLog | None = None) -> dict:
    """Every number the JSX sets (DESIGN §5 export_ae) -- the single source of truth for write_jsx and
    simulate_ae.

    footage_meta = {basename: {width, height, fps_num, fps_den, frames, has_audio}} (from probe).
    Raises ValueError on None / NaN / non-finite numbers, invalid layout/time modes and a --comp-size
    that breaks the competitor aspect in match mode. Plan layout:

      main  {name, w, h, fps{num,den}, frames, duration, bg}     box {name, bx0, by0, bw, bh, mask, r} | None
      footage {raw, ref|None}: {rel, abs, base, w, h, fps, frames, hasAudio, prompt}
      layers [ ... ]  per comp in stacking order (top first); the Video Box comp's layers come first.
          {id, kind, comp, source, seg, name, compIn, compOut, timeMode, speed, stretch, rawIn,
           startTime, startStretch, inPoint, outPoint, expect[], remap[{k, v}], flip,
           xf{anchor, scale, rotation, position, keys[{k, scale, rotation, position}], rotKeys},
           opacity[{k, v}], opacityValue, audioKeys[{k, v dB}], enabled, audio, guide, blend,
           mask, blur, color, w, h, aeRuleSensitive, note}
          kinds: raw | raw_audio | placeholder | dip | flash | box | bg_blur | bg_solid | guide | reference
      markers [{k, text}] (merged per MAIN frame), fpsSource, summary, warnings, decisions.
    """
    dlog = dlog or null_dlog()
    b = _PlanBuilder(cutlist, cfg, footage_meta, dlog)
    segs = sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.comp_out), int(s.id)))
    if not segs:
        raise ValueError("ae_plan: the cutlist has no segments")

    upper: list[dict] = []        # dips / flashes (top of the segment comp)
    chrono: list[dict] = []       # RAW video layers + NOT-IN-RAW placeholders, chronological
    audio_dups: list[dict] = []
    seg_layer: dict[int, dict] = {}       # segment id -> its visible layer
    carrier: dict[int, dict] = {}         # segment id -> the layer carrying its audio
    kmap: dict[int, tuple[int, int]] = {}
    fps_err: dict[str, float] = {}           # §2.5 cut error at comp_in (raw_in is re-anchored by v * error)
    fps_err_out: dict[str, float] = {}       # ... and at comp_out
    markers: dict[int, list[str]] = {}

    def add_marker(K: int, text: str) -> None:
        K = min(max(0, int(K)), b.N - 1)
        markers.setdefault(K, [])
        t = ascii_text(text, 300)
        if t not in markers[K]:
            markers[K].append(t)

    n_raw = n_placeholder = 0
    for i, seg in enumerate(segs):
        sid = int(seg.id)
        c_in, c_out = _int(seg.comp_in, f"segment {sid} comp_in"), _int(seg.comp_out, f"segment {sid} comp_out")
        if c_out <= c_in:
            raise ValueError(f"ae_plan: segment {sid} is empty ([{c_in}, {c_out}))")
        k_in, k_out = b.to_main(c_in), b.to_main(c_out)
        if b.main_fps != b.comp_fps:
            fps_err[str(sid)] = float(Fraction(k_in) / b.main_fps - Fraction(c_in) / b.comp_fps)
            fps_err_out[str(sid)] = float(Fraction(k_out) / b.main_fps - Fraction(c_out) / b.comp_fps)
        if k_out <= k_in:
            b.warn(f"S{sid:02d}: {c_out - c_in} competitor frame(s) vanish on the {fps_str(b.main_fps)} MAIN grid")
            continue
        kmap[sid] = (k_in, k_out)
        if getattr(seg, "region", 0):
            b.warn(f"S{sid:02d}: belongs to video region {seg.region}; v1 exports it inside the dominant Video Box")
        if seg.box and b.box_model and Box.from_dict(seg.box) != b.box_model:
            b.warn(f"S{sid:02d}: uses a different layout box; exported inside the dominant Video Box")
        tc_in, tc_out = timecode(k_in, b.main_fps), timecode(k_out, b.main_fps)
        cut_label = "Start" if i == 0 else f"Cut {i:02d}"
        if seg.type == "raw":
            if getattr(seg, "retime", "none") not in (None, "none"):
                b.warn(f"S{sid:02d}: the competitor used {seg.retime} retiming; the layer shows whole RAW frames "
                       "(switch on Frame Blending > Frame Mix in AE to approximate it)")
            layers = b.raw_layers(seg, k_in, k_out)
            chrono.append(layers[0])
            seg_layer[sid] = layers[0]
            carrier[sid] = layers[1] if len(layers) > 1 else layers[0]
            audio_dups.extend(layers[1:])
            n_raw += 1
            v = float(seg.speed)
            j0 = layers[0]["expect"][0] if layers[0]["expect"] else 0
            txt = (f"{cut_label} | S{sid:02d} RAW {timecode(max(0, j0), b.raw_fps)} | speed {v:.3f} | "
                   f"conf {float(seg.confidence or 0.0):.2f}")
            if seg.flip_h:
                txt += " | flip"
            if seg.uncertain:
                txt += " | UNCERTAIN"
            add_marker(k_in, txt)
        elif seg.type in ("dip", "flash"):
            if seg.type == "dip":
                ttypes = str((seg.transition_in or {}).get("type", "")) + str((seg.transition_out or {}).get("type", ""))
                col = seg.color or ("#ffffff" if "white" in ttypes else "#000000")
            else:
                col = seg.color or "#ffffff"
            L = b._solid(f"{seg.type}{sid}", seg.type, f"{seg.type.upper()} {col} ({tc_in}-{tc_out})",
                         _rgb(col), k_in, k_out, seg=sid)
            upper.append(L)
            seg_layer[sid] = L
            add_marker(k_in, f"{cut_label} | {seg.type} {col}")
        else:
            if seg.type != "not_in_raw":
                b.warn(f"S{sid:02d}: unknown segment type {seg.type!r} exported as a NOT-IN-RAW placeholder")
            label = ascii_text(seg.label or "", 120).strip()
            name = f"MISSING - not in RAW ({tc_in}-{tc_out})"
            if label and "MISSING" not in label.upper():
                name += f" {label}"
            L = b._solid(f"nir{sid}", "placeholder", name, PLACEHOLDER_RGB, k_in, k_out, seg=sid)
            chrono.append(L)
            seg_layer[sid] = L
            n_placeholder += 1
            add_marker(k_in, f"{cut_label} | MISSING not in RAW {tc_in}-{tc_out}" + (f" | {label}" if label else ""))

    # ---- transitions (crossfades / dips): only the UPPER layer of each pair is keyed ----------
    keyed: dict[str, dict[float, float]] = {}
    akeyed: dict[str, dict[float, float]] = {}
    ordered = [s for s in segs if int(s.id) in kmap]
    for X, Y in zip(ordered[:-1], ordered[1:]):
        tr = Y.transition_in or X.transition_out
        if not tr:
            continue
        ttype = str(tr.get("type", "crossfade"))
        o0 = int(Y.comp_in)
        D = _int(tr.get("duration_frames", int(X.comp_out) - o0), f"segment {Y.id} transition duration")
        if D <= 0:
            continue
        D_eff = min(D, int(X.comp_out) - o0)
        if D_eff != D:
            b.warn(f"S{int(X.id):02d}->S{int(Y.id):02d}: transition of {D} frames but the layers overlap by "
                   f"{int(X.comp_out) - o0}; keyed over the overlap")
        if D_eff <= 0:
            continue
        alpha = tr.get("alpha") or []
        if len(alpha) != D:
            alpha = [i / D for i in range(D)]
        alpha = [min(1.0, max(0.0, _num(a, f"transition alpha S{int(Y.id):02d}"))) for a in alpha][:D_eff]
        LX, LY = seg_layer.get(int(X.id)), seg_layer.get(int(Y.id))
        if LX is None or LY is None:
            continue
        is_dip = ttype.startswith("dip") or X.type == "dip" or Y.type == "dip"
        if is_dip and Y.type == "dip":
            U, rising = LY, True
        elif is_dip and X.type == "dip":
            U, rising = LX, False
        else:
            if is_dip:
                b.warn(f"S{int(X.id):02d}->S{int(Y.id):02d}: {ttype} without a dip segment; exported as a crossfade")
            U, rising = LX, False
        ks: dict[float, float] = keyed.setdefault(U["id"], {})
        if rising:
            for i2, a in enumerate(alpha):
                ks[b.to_main_f(o0 + i2)] = 100.0 * a
            ks[b.to_main_f(o0 + D_eff)] = 100.0
        else:
            ks.setdefault(b.to_main_f(o0 - 1), 100.0)
            for i2, a in enumerate(alpha):
                ks[b.to_main_f(o0 + i2)] = 100.0 * (1.0 - a)
            ks[b.to_main_f(o0 + D_eff)] = 0.0
        add_marker(b.to_main(o0), f"{'Dip' if is_dip else 'Crossfade'} {D_eff} fr (S{int(X.id):02d} -> S{int(Y.id):02d})")
        b.decide("transition", type=ttype, start_frame=o0, duration_frames=D_eff, upper=U["id"], rising=rising)
        # audio across a crossfade between two RAW layers: Audio Levels keys on both carriers
        if not is_dip and X.type == "raw" and Y.type == "raw" and b.has_audio:
            cx, cy = carrier.get(int(X.id)), carrier.get(int(Y.id))
            if cx is not None and cy is not None:
                ax = akeyed.setdefault(cx["id"], {})
                ay = akeyed.setdefault(cy["id"], {})
                for i2, a in enumerate(alpha):
                    kk = b.to_main_f(o0 + i2)
                    ax[kk] = 20.0 * math.log10(max(1.0 - a, MIN_GAIN))
                    ay[kk] = 20.0 * math.log10(max(a, MIN_GAIN))
                ay[b.to_main_f(o0 + D_eff)] = 0.0
    for L in upper + chrono + audio_dups:
        if L["id"] in keyed:
            L["opacity"] = [{"k": _knum(k), "v": float(v)} for k, v in sorted(keyed[L["id"]].items())]
        if L["id"] in akeyed:
            L["audioKeys"] = [{"k": _knum(k), "v": float(v)} for k, v in sorted(akeyed[L["id"]].items())]

    # ---- MAIN layers ------------------------------------------------------------------------
    main_layers: list[dict] = []
    if b.ref_foot is not None:
        k_ref = min(b.N, math.floor(Fraction(b.Nc) * b.main_fps / b.comp_fps)) if b.main_fps != b.comp_fps else b.N
        sr = min(b.W / b.Wc, b.H / b.Hc)
        main_layers.append(b._layer(
            id="ref", kind="reference", comp="main", source="ref", name=REFERENCE_NAME, compIn=0, compOut=k_ref,
            enabled=False, audio=False, guide=True, blend="difference",
            xf=_static_xf((b.Wc / 2.0, b.Hc / 2.0), (b.W / 2.0, b.H / 2.0), (100.0 * sr, 100.0 * sr))))
    lay = cutlist.layout or {}
    if lay.get("regions"):
        b.warn(f"{len(lay['regions'])} extra video region(s) (split-screen / picture-in-picture) are not recreated; "
               "only the dominant Video Box is built")
    if b.layout_mode == "match":
        zones = list(lay.get("zones") or [])
        if not any(str(z.get("type")) == "captions" for z in zones) and lay.get("captions"):
            caps = lay["captions"]
            x0 = min(float(c["x"]) for c in caps)
            y0 = min(float(c["y"]) for c in caps)
            x1 = max(float(c["x"]) + float(c["w"]) for c in caps)
            y1 = max(float(c["y"]) + float(c["h"]) for c in caps)
            zones.append({"type": "captions", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "comp_in": None,
                          "comp_out": None})
        for zi, z in enumerate(zones):
            try:
                zx, zy = _num(z["x"], "zone.x") * b.r, _num(z["y"], "zone.y") * b.r
                zw, zh = _num(z["w"], "zone.w") * b.r, _num(z["h"], "zone.h") * b.r
            except (KeyError, ValueError) as e:
                b.warn(f"guide zone {zi} skipped ({e})")
                continue
            ztype = ascii_text(z.get("type", "other"), 40)
            if z.get("comp_in") is not None and z.get("comp_out") is not None:
                g_in, g_out = b.to_main(int(z["comp_in"])), b.to_main(int(z["comp_out"]))
            else:
                g_in, g_out = 0, b.N
            g_in, g_out = max(0, g_in), min(b.N, g_out)
            if g_out <= g_in:
                continue
            main_layers.append(b._solid(
                f"guide{zi}", "guide", f"GUIDE - {ztype} zone ({round(zx)},{round(zy)} {round(zw)}x{round(zh)})",
                _GUIDE_RGB.get(ztype, _GUIDE_RGB["other"]), g_in, g_out, comp="main", w=round(zw), h=round(zh),
                center=(zx + zw / 2.0, zy + zh / 2.0), guide=True, opacityValue=GUIDE_OPACITY))
    seg_stack = upper + chrono + audio_dups
    bg = lay.get("background_detail") or {"type": lay.get("background", "solid"), "color": lay.get("canvas_bg", "#000000")}
    bg_rgb = _rgb(bg.get("color") or lay.get("canvas_bg") or "#000000")
    if b.box_geo:
        g = b.box_geo
        main_layers.append(b._layer(
            id="box", kind="box", comp="main", source="box", name=BOX_COMP_NAME, compIn=0, compOut=b.N,
            audio=b.has_audio, xf=_static_xf((0.0, 0.0), (float(g["bx0"]), float(g["by0"]))), mask=g["mask"]))
        if str(bg.get("type", "solid")) == "blur":
            cover = max(b.W / g["bw"], b.H / g["bh"]) * 100.0
            amount = bg.get("blurriness") or bg.get("ae_blurriness")
            if amount is None and bg.get("sigma") is not None:
                amount = 3.0 * float(bg["sigma"])
            amount = min(3000.0, max(0.0, float(amount if amount is not None else DEFAULT_BLURRINESS)))
            main_layers.append(b._layer(
                id="bg_blur", kind="bg_blur", comp="main", source="box",
                name="BACKGROUND - blurred copy of the Video Box", compIn=0, compOut=b.N, audio=False,
                xf=_static_xf((g["bw"] / 2.0, g["bh"] / 2.0), (b.W / 2.0, b.H / 2.0), (cover, cover)),
                blur={"amount": amount}))
        elif str(bg.get("type", "solid")) not in ("solid", "blur"):
            b.warn(f"background type {bg.get('type')!r} is recreated as a solid; add your own image/gradient")
    else:
        main_layers.extend(seg_stack)
    if b.layout_mode == "match":
        bg_name = f"BACKGROUND - solid {bg.get('color') or lay.get('canvas_bg') or '#000000'}"
        main_layers.append(b._solid("bg_solid", "bg_solid", bg_name, bg_rgb, 0, b.N, comp="main"))
    layers = (seg_stack if b.box_geo else []) + main_layers

    if not any(L["kind"] == "raw" for L in layers):
        b.warn("no RAW segment could be exported")
    for L in layers:
        L["time_mode"] = L["timeMode"]          # DESIGN §5 spelling (the JSX reads timeMode)
    frames_layers = sum(1 for L in layers if L["kind"] == "raw" and L["timeMode"] == "frames")
    main = {"name": MAIN_COMP_NAME, "w": int(b.W), "h": int(b.H), "fps": b.F, "frames": int(b.N),
            "duration": b.T(b.N), "bg": bg_rgb if b.layout_mode == "match" else [0.0, 0.0, 0.0]}
    box = None
    if b.box_geo:
        box = {"name": BOX_COMP_NAME, **{k: b.box_geo[k] for k in ("bx0", "by0", "bw", "bh")},
               "mask": b.box_geo["mask"], "r": b.r}
    max_err = max((abs(e) for e in list(fps_err.values()) + list(fps_err_out.values())), default=0.0)
    plan = {
        "version": 1,
        "tool": "match_cuts export_ae",
        "mode": b.layout_mode,
        "timeModeCfg": b.time_mode_cfg,
        "segComp": b.seg_comp,
        "r": b.r,
        "main": main,
        "box": box,
        "rawFps": b.R,
        "compFps": b.CF,
        "raw": {"w": b.raw_w, "h": b.raw_h, "frames": b.raw_frames},
        "footage": {"raw": b.raw_foot, "ref": b.ref_foot},
        "layers": layers,
        "markers": [{"k": int(K), "text": "\n".join(markers[K])} for K in sorted(markers)],
        "fpsSource": {"active": b.main_fps != b.comp_fps, "mainFps": fps_str(b.main_fps),
                      "maxErrorS": max_err, "perSegment": fps_err, "perSegmentOut": fps_err_out},
        "summary": {"segments": n_raw + n_placeholder + len(upper), "raw": n_raw, "placeholders": n_placeholder,
                    "cuts": max(0, len(kmap) - 1), "duration": f"{b.T(b.N):.3f}", "frames": int(b.N),
                    "framesModeLayers": frames_layers},
        "warnings": b.warnings,
        "decisions": b.decisions,
    }
    _validate_plan_numbers(plan)
    return plan


def _validate_plan_numbers(obj: Any, path: str = "plan") -> None:
    """Raise ValueError on NaN/Infinity anywhere in the plan (json allow_nan=False would too, later)."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"{path} is not finite ({obj!r})")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _validate_plan_numbers(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _validate_plan_numbers(v, f"{path}[{i}]")


def apply_fps_source_notes(cutlist: Cutlist, plan: dict) -> None:
    """Copy the §2.5 MAIN-grid cut errors into cutlist.settings / segment notes (pipeline helper; the
    pipeline may already do this itself)."""
    fs = plan.get("fpsSource") or {}
    if not fs.get("active"):
        return
    cutlist.settings["fps_source_max_error_s"] = float(f"{fs.get('maxErrorS', 0.0):.9f}")
    per = fs.get("perSegment") or {}
    for s in cutlist.segments:
        e = per.get(str(s.id))
        if e:
            note = f"MAIN fps {fs.get('mainFps')}: cut moved {e * 1000:+.3f} ms"
            if note not in (s.notes or ""):
                s.notes = (s.notes + "; " if s.notes else "") + note


def footage_meta_from_cutlist(cutlist: Cutlist) -> dict:
    """{basename: {width, height, fps_num, fps_den, frames, has_audio}} built from cutlist.raw/competitor
    (for callers without probe results)."""
    out = {}
    for block in (cutlist.raw, cutlist.competitor):
        f = block.get("file_rel") or block.get("file") or block.get("file_abs")
        if not f:
            continue
        fr = parse_fps(block["fps"])
        out[os.path.basename(str(f))] = {"width": int(block["width"]), "height": int(block["height"]),
                                         "fps_num": fr.numerator, "fps_den": fr.denominator,
                                         "frames": int(block["frames"]), "has_audio": bool(block.get("has_audio", True))}
    return out


# ---------------------------------------------------------------------------------------------
# simulate_ae
# ---------------------------------------------------------------------------------------------

def _plan_opacity(L: dict, K: int) -> float:
    keys = L.get("opacity") or []
    if not keys:
        return float(L.get("opacityValue", 100.0)) / 100.0
    times = [float(d["k"]) for d in keys]
    vals = [float(d["v"]) for d in keys]
    return _interp(times, vals, [False] * len(keys), float(K), 1e-9) / 100.0


def simulate_ae(plan: dict, time_mode_override: str | None = None) -> dict[int, list[dict]]:
    """Which RAW frame AE shows on every MAIN frame.

    ``plan`` is an :func:`ae_plan` result, or a mock-run record from :func:`run_jsx_in_mock` (then the
    recorded startTime / stretch / in / out / remap keys -- in LAYER time -- are used, exactly what the
    JSX set). Returns ``{K: [{layer, seg, raw_frame, opacity, weight}, ...]}`` for K in [0, frames),
    visible RAW video layers only, top first; ``opacity`` is the layer's own opacity (0..1), ``weight``
    its contribution after compositing the stack (solids above -- dips -- reduce it). Frames without a
    RAW layer (NOT-IN-RAW, dips) map to an empty list.

    time_mode_override ('stretch' | 'remap' | 'frames') re-simulates every RAW video layer in that mode
    (stretch only where speed > 0; remap uses the layer's remap keys, linear for stretch layers).
    """
    if plan.get("record_type") == "ae_mock":
        return _simulate_record(plan)
    if time_mode_override not in (None, "stretch", "remap", "frames"):
        raise ValueError(f"simulate_ae: unknown time mode override {time_mode_override!r}")
    F, R, N = plan["main"]["fps"], plan["rawFps"], int(plan["main"]["frames"])
    stack = [L for L in plan["layers"] if L["comp"] == plan["segComp"]
             and L["enabled"] and not L["guide"] and L["kind"] not in ("raw_audio", "bg_blur")]
    frames: dict[str, dict[int, int]] = {}
    for L in stack:
        if L["kind"] != "raw":
            continue
        mode = L["timeMode"]
        if time_mode_override == "frames":
            mode = "frames"
        elif time_mode_override == "remap":
            mode = "remap"
        elif time_mode_override == "stretch" and L["stretch"] is not None and L["startStretch"] is not None \
                and abs(L["startStretch"]) <= AE_TIME_SAFE_S:
            mode = "stretch"
        js = _layer_raw_frames(L, mode, F, R)
        frames[L["id"]] = {K: j for K, j in zip(range(int(L["compIn"]), int(L["compOut"])), js)}
    out: dict[int, list[dict]] = {}
    for K in range(N):
        remaining = 1.0
        entries: list[dict] = []
        for L in stack:
            if not (int(L["compIn"]) <= K < int(L["compOut"])):
                continue
            op = _plan_opacity(L, K)
            if L["kind"] == "raw":
                entries.append({"layer": L["id"], "seg": L["seg"], "raw_frame": int(frames[L["id"]][K]),
                                "opacity": op, "weight": remaining * op})
            remaining *= (1.0 - op)
        out[K] = entries
    return out


def _fraction_from_rate(x: float) -> Fraction:
    fr = Fraction(x).limit_denominator(1001000)
    for cand in (parse_fps(x), fr):
        if abs(float(cand) - x) <= 1e-9 * max(1.0, x):
            return cand
    return Fraction(x)


def _rec_value_at(prop: dict | None, lt: float, default: float) -> float:
    if not prop:
        return default
    keys = prop.get("keys") or []
    if not keys:
        v = prop.get("value")
        return float(v[0] if isinstance(v, list) else v) if v is not None else default
    times = [float(k["layerTime"]) for k in keys]
    vals = [float(k["value"][0] if isinstance(k["value"], list) else k["value"]) for k in keys]
    holds = [str(k.get("outInterp")) == "HOLD" for k in keys]
    return _interp(times, vals, holds, lt)


def _simulate_record(rec: dict) -> dict[int, list[dict]]:
    """simulate_ae on a mock-run record (the values the JSX actually set)."""
    comps = {c["id"]: c for c in rec.get("comps", [])}
    footage = {f["id"]: f for f in rec.get("footage", [])}
    main = next((c for c in rec.get("comps", []) if c.get("comment") == "mc:main"), None)
    if main is None:
        main = next((c for c in rec.get("comps", []) if c.get("name") == MAIN_COMP_NAME), None)
    if main is None:
        raise ValueError("simulate_ae: the mock record has no MAIN comp (comment 'mc:main')")
    Fr = _fraction_from_rate(float(main["frameRate"]))
    F = {"num": Fr.numerator, "den": Fr.denominator}
    N = int(round(float(main["duration"]) * float(Fr)))

    def raw_rate(f: dict) -> float:
        conf = float(f.get("conformFrameRate") or 0.0)
        return conf if conf > 0 else f["fps_num"] / f["fps_den"]

    def walk(comp: dict, t: float, w_in: float, entries: list[dict], depth: int) -> float:
        remaining = 1.0
        if depth > 8:
            return remaining
        for L in comp["layers"]:
            if not L.get("enabled", True) or L.get("guideLayer"):
                continue
            tag = str(L.get("comment") or "")
            if tag.startswith("mc:bg_blur"):
                continue
            if not (float(L["inPoint"]) - ACTIVE_EPS <= t < float(L["outPoint"]) - ACTIVE_EPS):
                continue
            lt = _layer_time(t, float(L["startTime"]), float(L["stretch"]))
            props = L.get("props") or {}
            op = _rec_value_at(props.get("ADBE Opacity"), lt, 100.0) / 100.0
            src_t = _rec_value_at(props.get("ADBE Time Remapping"), lt, lt) if L.get("timeRemapEnabled") else lt
            if L.get("sourceType") == "comp":
                inner = comps.get(L.get("sourceId"))
                if inner is None:
                    continue
                trans = walk(inner, src_t, w_in * remaining * op, entries, depth + 1)
                remaining *= (1.0 - op * (1.0 - trans))
                continue
            if L.get("sourceType") == "footage":
                f = footage.get(L.get("sourceId")) or {}
                if f.get("comment") == "mc:raw":
                    rf = raw_rate(f)
                    if L.get("timeRemapEnabled"):
                        j = math.floor(src_t * rf + AE_EPS)
                    else:
                        j = math.floor((t - float(L["startTime"])) * (100.0 / float(L["stretch"])) * rf + AE_EPS)
                    lid = tag[3:] if tag.startswith("mc:") else str(L.get("name"))
                    seg = None
                    m = re.match(r"seg(\d+)$", lid)
                    if m:
                        seg = int(m.group(1))
                    entries.append({"layer": lid, "seg": seg, "raw_frame": int(j), "opacity": op,
                                    "weight": w_in * remaining * op})
            remaining *= (1.0 - op)
        return remaining

    out: dict[int, list[dict]] = {}
    for K in range(N):
        entries: list[dict] = []
        walk(main, _t(K, F), 1.0, entries, 0)
        out[K] = entries
    return out


def raw_frames_by_layer(sim: dict[int, list[dict]]) -> dict[str, dict[int, int]]:
    """{layer id: {MAIN frame: RAW frame}} view of a simulate_ae result (for comparisons)."""
    out: dict[str, dict[int, int]] = {}
    for K, entries in sim.items():
        for e in entries:
            out.setdefault(e["layer"], {})[int(K)] = int(e["raw_frame"])
    return out


# ---------------------------------------------------------------------------------------------
# write_jsx
# ---------------------------------------------------------------------------------------------

def _strip_js_strings_comments(text: str) -> str:
    """Replace JS string literals and comments by spaces (keeps line structure); '#' directive lines are
    treated as comments. Regex literals are not produced by write_jsx."""
    out = []
    i, n = 0, len(text)
    at_line_start = True
    while i < n:
        c = text[i]
        if at_line_start and c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        at_line_start = c == "\n"
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c in ("'", '"'):
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n":
                    break
                j += 1
            out.append(c + " " * (min(j, n) - i - 1) + (c if j < n else ""))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def es3_static_check(text: str) -> list[str]:
    """Static ExtendScript/ES3 checks on generated JSX text: ASCII only, no ES5+ runtime APIs
    (forEach/map/.../indexOf/trim/bind calls, JSON, Object.keys/create/defineProperty, Array.isArray,
    Date.now), no let/const/=>/template strings, no NaN/Infinity literals. Strings and comments are
    ignored (user file names may contain anything). Returns a list of problems (empty = OK)."""
    problems = []
    if not text.isascii():
        bad = next(i for i, ch in enumerate(text) if ord(ch) > 127)
        problems.append(f"non-ASCII character {text[bad]!r} at offset {bad}")
    code = _strip_js_strings_comments(text)
    for rx in (_BAN_RE, _BAN_TOKENS_RE):
        for m in rx.finditer(code):
            line = code.count("\n", 0, m.start()) + 1
            problems.append(f"forbidden construct {m.group(0)!r} at line {line}")
    return problems


def write_jsx(cutlist: Cutlist, plan: dict, out_path: str | os.PathLike, cfg: Any = None) -> None:
    """Write build_ae_project.jsx: ES3 ExtendScript, ASCII only, the plan embedded as an object literal
    (json.dumps(ensure_ascii=True, allow_nan=False, sort_keys=True)). Raises ValueError if the plan holds
    NaN/Infinity or the generated text fails the static ES3 checks."""
    _validate_plan_numbers(plan)
    try:
        data = json.dumps(plan, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except ValueError as e:
        raise ValueError(f"write_jsx: plan is not NaN/Infinity-free: {e}") from e
    comp = cutlist.competitor or {}
    raw = cutlist.raw or {}
    header = [
        f"// competitor: {ascii_text(os.path.basename(str(comp.get('file', ''))), 120)} "
        f"{comp.get('width')}x{comp.get('height')} @ {comp.get('fps')} fps, {comp.get('frames')} frames",
        f"// raw:        {ascii_text(os.path.basename(str(raw.get('file', ''))), 120)} "
        f"{raw.get('width')}x{raw.get('height')} @ {raw.get('fps')} fps, {raw.get('frames')} frames",
        f"// layout {plan['mode']}, MAIN {plan['main']['w']}x{plan['main']['h']} @ "
        f"{plan['main']['fps']['num']}/{plan['main']['fps']['den']} fps, {plan['main']['frames']} frames, "
        f"{plan['summary']['segments']} segments, time mode {plan['timeModeCfg']}",
    ]
    text = _JSX_TEMPLATE.replace("__HEADER__", "\n".join(header)).replace("__PLAN__", data)
    text = text.replace("\r\n", "\n")
    problems = es3_static_check(text)
    if problems:
        raise ValueError("write_jsx: generated JSX fails the ES3 checks: " + "; ".join(problems[:5]))
    assert text.isascii()
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="ascii", newline="\n")
    os.replace(tmp, p)
    log.info("export_ae: wrote %s (%d layers, %d markers)", p, len(plan["layers"]), len(plan["markers"]))


_JSX_TEMPLATE = r"""#target aftereffects
// build_ae_project.jsx - generated by match_cuts (prompt Stage 7). ExtendScript (ECMAScript 3), ASCII only.
// Run in After Effects CC 2019 or newer: File > Scripts > Run Script File... > build_ae_project.jsx
// It builds the comp "Recreated Edit" from the RAW video in media/ and saves recreated_edit.aep next to
// this script. Saving needs Preferences > Scripting & Expressions (General before AE 16.1) >
// "Allow Scripts to Write Files and Access Network".
// Every number below comes from the embedded PLAN (computed by match_cuts.export_ae.ae_plan).
__HEADER__
(function () {
    var PLAN = __PLAN__;
    var WARN = [];
    var C = PLAN.main.fps;
    var R = PLAN.rawFps;
    var RF = R.num / R.den;

    function T(k, F) { return k * F.den / F.num; }
    function warn(msg) {
        WARN.push(String(msg));
        try { $.writeln("match_cuts warning: " + msg); } catch (eW) { }
    }
    function xprop(L, mn) { return L.property("ADBE Transform Group").property(mn); }

    function findMedia(here, X, required) {
        var f;
        if (X.rel !== "") {
            f = new File(here.fsName + "/" + X.rel);
            if (f.exists) { return f; }
        }
        if (X.abs !== "") {
            f = new File(X.abs);
            if (f.exists) { return f; }
        }
        if (!required) { return null; }
        f = File.openDialog(X.prompt);
        if (f === null) { return null; }
        if (!f.exists) { return null; }
        return f;
    }

    function importFootage(f, X, folder, tag) {
        var io = new ImportOptions(f), it, want, n;
        if (!io.canImportAs(ImportAsType.FOOTAGE)) { throw new Error("cannot import " + f.fsName + " as footage"); }
        io.importAs = ImportAsType.FOOTAGE;
        it = app.project.importFile(io);
        it.parentFolder = folder;
        it.comment = tag;
        want = X.fps.num / X.fps.den;
        if (Math.abs(it.frameRate - want) > 1e-3 * want) {
            warn(it.name + ": AE read " + it.frameRate + " fps; conformed to " + X.fps.num + "/" + X.fps.den);
            it.mainSource.conformFrameRate = want;
        }
        try { it.mainSource.fieldSeparationType = FieldSeparationType.OFF; } catch (e1) { }
        try { it.mainSource.removePulldown = PulldownPhase.OFF; } catch (e2) { }
        if (it.width !== X.w || it.height !== X.h) {
            warn(it.name + ": size " + it.width + "x" + it.height + " (expected " + X.w + "x" + X.h + ")");
        }
        n = Math.round(it.duration * want);
        if (Math.abs(n - X.frames) > 1) {
            warn(it.name + ": " + n + " frames in AE (expected " + X.frames + ")");
        }
        return it;
    }

    function setupComp(c, frames, bg, tag) {
        c.comment = tag;
        c.bgColor = bg;
        c.frameBlending = false;
        c.motionBlur = false;
        c.workAreaStart = 0;
        c.workAreaDuration = c.duration;
        if (Math.round(c.duration / c.frameDuration) !== frames) {
            warn(c.name + ": " + (c.duration / c.frameDuration) + " frames (expected " + frames + ")");
        }
    }

    // Keys: temporal LINEAR (or HOLD); spatial properties also get linear (zero) tangents.
    function setKeys(P, times, vals, hold) {
        var i, it, vt, sp, z;
        P.setValuesAtTimes(times, vals);
        it = hold ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.LINEAR;
        vt = P.propertyValueType;
        sp = (vt === PropertyValueType.TwoD_SPATIAL || vt === PropertyValueType.ThreeD_SPATIAL);
        z = (vt === PropertyValueType.ThreeD_SPATIAL) ? [0, 0, 0] : [0, 0];
        for (i = 1; i <= P.numKeys; i++) {
            P.setInterpolationTypeAtKey(i, it, it);
            if (sp) {
                P.setSpatialAutoBezierAtKey(i, false);
                P.setSpatialContinuousAtKey(i, false);
                P.setSpatialTangentsAtKey(i, z, z);
            }
        }
    }

    // Stretch mode (strict order: stretch -> startTime -> inPoint -> outPoint), then the self-check
    // recomputes every frame from the READ-BACK startTime/stretch. Returns the number of mismatches.
    function placeStretch(L, s) {
        var tIn = T(s.compIn, C), tOut = T(s.compOut, C), bad = 0, k, j, vEff;
        L.stretch = s.stretch;
        vEff = 100 / L.stretch;
        L.startTime = tIn - s.rawIn / vEff;
        L.inPoint = tIn;
        L.outPoint = tOut;
        for (k = s.compIn; k < s.compOut && s.expect.length > 0; k++) {
            j = Math.floor((T(k, C) - L.startTime) * (100 / L.stretch) * RF + 1e-9);
            if (j !== s.expect[k - s.compIn]) { bad++; }
        }
        if (Math.abs(L.inPoint - tIn) > 1e-6 || Math.abs(L.outPoint - tOut) > 1e-6) { bad++; }
        return bad;
    }

    // Time remap: linear keys, or frame-exact HOLD keys at (m + 0.25) / raw_fps (one per MAIN frame).
    function placeRemap(L, s, frameExact) {
        var tIn = T(s.compIn, C), tOut = T(s.compOut, C), times = [], vals = [], i, P;
        L.stretch = 100;
        L.startTime = tIn;
        L.inPoint = tIn;
        L.outPoint = tOut;
        if (!L.canSetTimeRemapEnabled) { throw new Error("layer " + L.name + " cannot be time-remapped"); }
        L.timeRemapEnabled = true;
        L.inPoint = tIn;
        L.outPoint = tOut;
        P = L.property("ADBE Time Remapping");
        while (P.numKeys > 0) { P.removeKey(P.numKeys); }
        if (frameExact) {
            for (i = 0; i < s.expect.length; i++) {
                times.push(T(s.compIn + i, C));
                vals.push((s.expect[i] + 0.25) * R.den / R.num);
            }
        } else {
            for (i = 0; i < s.remap.length; i++) {
                times.push(T(s.remap[i].k, C));
                vals.push(s.remap[i].v);
            }
        }
        setKeys(P, times, vals, frameExact);
        if (P.numKeys !== times.length) { warn(L.name + ": " + P.numKeys + " time remap keys (expected " + times.length + ")"); }
        if (Math.abs(L.inPoint - tIn) > 1e-6 || Math.abs(L.outPoint - tOut) > 1e-6) {
            warn(L.name + ": in/out point differs from the plan");
        }
    }

    function placeStill(L, s) {
        L.startTime = s.startTime;
        L.inPoint = T(s.compIn, C);
        L.outPoint = T(s.compOut, C);
    }

    function renderSwitches(L, s) {
        L.quality = LayerQuality.BEST;
        L.motionBlur = false;
        if (s.source === "raw" || s.source === "box" || s.source === "ref") {
            L.frameBlendingType = FrameBlendingType.NO_FRAME_BLEND;
        } else {
            try { L.frameBlendingType = FrameBlendingType.NO_FRAME_BLEND; } catch (e0) { }
        }
        try { L.samplingQuality = LayerSamplingQuality.BILINEAR; } catch (e) { }
    }

    function applyXf(L, X) {
        var A = xprop(L, "ADBE Anchor Point"), P = xprop(L, "ADBE Position");
        var S = xprop(L, "ADBE Scale"), Rz = xprop(L, "ADBE Rotate Z");
        var t = [], vs = [], vp = [], vr = [], i;
        A.setValue(X.anchor);
        if (X.keys.length === 0) {
            S.setValue(X.scale);
            Rz.setValue(X.rotation);
            P.setValue(X.position);
            return;
        }
        for (i = 0; i < X.keys.length; i++) {
            t.push(T(X.keys[i].k, C));
            vs.push(X.keys[i].scale);
            vp.push(X.keys[i].position);
            vr.push(X.keys[i].rotation);
        }
        setKeys(S, t, vs, false);
        setKeys(P, t, vp, false);
        if (X.rotKeys) { setKeys(Rz, t, vr, false); } else { Rz.setValue(X.rotation); }
    }

    function applyOpacity(L, s) {
        var P = xprop(L, "ADBE Opacity"), t = [], v = [], i;
        if (s.opacity.length === 0) {
            if (s.opacityValue !== 100) { P.setValue(s.opacityValue); }
            return;
        }
        for (i = 0; i < s.opacity.length; i++) {
            t.push(T(s.opacity[i].k, C));
            v.push(s.opacity[i].v);
        }
        setKeys(P, t, v, false);
    }

    function applyAudioKeys(L, s) {
        var P, t = [], v = [], i;
        if (s.audioKeys.length === 0 || !L.hasAudio) { return; }
        P = L.property("ADBE Audio Group").property("ADBE Audio Levels");
        for (i = 0; i < s.audioKeys.length; i++) {
            t.push(T(s.audioKeys[i].k, C));
            v.push([s.audioKeys[i].v, s.audioKeys[i].v]);
        }
        setKeys(P, t, v, false);
    }

    // Rounded rectangle mask (Bezier corners, tangent 0.5522847498 * r, feather 0).
    function addRoundedMask(L, m) {
        var x = m.x, y = m.y, w = m.w, h = m.h, rad = Math.max(0, Math.min(m.r, w / 2, h / 2));
        var k = 0.5522847498 * rad, x1 = x + w, y1 = y + h, sh = new Shape(), mask;
        if (rad <= 0) {
            sh.vertices = [[x, y], [x1, y], [x1, y1], [x, y1]];
            sh.inTangents = [[0, 0], [0, 0], [0, 0], [0, 0]];
            sh.outTangents = [[0, 0], [0, 0], [0, 0], [0, 0]];
        } else {
            sh.vertices = [[x + rad, y], [x1 - rad, y], [x1, y + rad], [x1, y1 - rad],
                           [x1 - rad, y1], [x + rad, y1], [x, y1 - rad], [x, y + rad]];
            sh.inTangents = [[-k, 0], [0, 0], [0, -k], [0, 0], [k, 0], [0, 0], [0, k], [0, 0]];
            sh.outTangents = [[0, 0], [k, 0], [0, 0], [0, k], [0, 0], [-k, 0], [0, 0], [0, -k]];
        }
        sh.closed = true;
        mask = L.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");
        mask.maskMode = MaskMode.ADD;
        mask.property("ADBE Mask Shape").setValue(sh);
        mask.property("ADBE Mask Feather").setValue([0, 0]);
    }

    function addBlur(L, b) {
        var fx, g;
        try {
            fx = L.property("ADBE Effect Parade");
            if (!fx.canAddProperty("ADBE Gaussian Blur 2")) {
                warn("Gaussian Blur (ADBE Gaussian Blur 2) is not available; background left unblurred");
                return;
            }
            g = fx.addProperty("ADBE Gaussian Blur 2");
            g.property(1).setValue(b.amount);
            g.property(3).setValue(1);
        } catch (e) {
            warn("Gaussian Blur could not be added to the background (" + e.message + ")");
        }
    }

    // Audio-only twin of a layer whose picture falls back to frame-exact time remapping.
    function addAudioTwin(comp, src, s) {
        var A = comp.layers.add(src);
        A.moveToEnd();
        A.name = s.name + "  audio";
        A.comment = "mc:" + s.id + "_audio";
        placeStretch(A, s);
        A.enabled = false;
        A.audioEnabled = true;
        renderSwitches(A, s);
        applyAudioKeys(A, s);
        return A;
    }

    function makeLayer(comps, srcs, s) {
        var comp = comps[s.comp], L, bad, audioOn = s.audio;
        if (s.source === "raw") {
            L = comp.layers.add(srcs.raw);
        } else if (s.source === "ref") {
            if (srcs.ref === null) { return null; }
            L = comp.layers.add(srcs.ref);
        } else if (s.source === "box") {
            L = comp.layers.add(srcs.box);
        } else {
            L = comp.layers.addSolid(s.color, s.name, s.w, s.h, 1, comp.duration);
        }
        L.moveToEnd();
        L.name = s.name;
        L.comment = "mc:" + s.id;
        // timing FIRST (keys live in layer time; nothing may move the layer after a key is written)
        if (s.timeMode === "stretch") {
            bad = placeStretch(L, s);
            if (bad > 0 && s.kind === "raw") {
                warn(s.name + ": AE stored stretch/startTime differently from the plan (" + bad +
                     " frame(s) off); switched this layer to frame-exact time remapping");
                if (L.hasAudio && audioOn) { addAudioTwin(comp, srcs.raw, s); }
                audioOn = false;
                placeRemap(L, s, true);
            }
        } else if (s.timeMode === "remap") {
            placeRemap(L, s, false);
        } else if (s.timeMode === "frames") {
            placeRemap(L, s, true);
        } else {
            placeStill(L, s);
        }
        L.enabled = s.enabled;
        if (L.hasAudio) { L.audioEnabled = audioOn; }
        if (s.guide) { L.guideLayer = true; }
        if (s.blend === "difference") { L.blendingMode = BlendingMode.DIFFERENCE; }
        renderSwitches(L, s);
        applyXf(L, s.xf);
        applyOpacity(L, s);
        if (audioOn) { applyAudioKeys(L, s); }
        if (s.mask !== null) { addRoundedMask(L, s.mask); }
        if (s.blur !== null) { addBlur(L, s.blur); }
        return L;
    }

    function addMarkers(comp) {
        var i, mk, mp;
        if (PLAN.markers.length === 0) { return; }
        try {
            mp = comp.markerProperty;
            for (i = 0; i < PLAN.markers.length; i++) {
                mk = PLAN.markers[i];
                mp.setValueAtTime(T(mk.k, C), new MarkerValue(mk.text));
            }
        } catch (e) {
            warn("comp markers could not be added (" + e.message + "); the cut list is in cutlist.csv");
        }
    }

    function build(media, res) {
        var P = PLAN, M = P.main, i, comps = {}, srcs = {}, fComps, fSrc, fRef, main, box = null;
        fComps = app.project.items.addFolder("01 Comps");
        fSrc = app.project.items.addFolder("02 Source");
        fRef = app.project.items.addFolder("03 Reference");
        srcs.raw = importFootage(media.raw, P.footage.raw, fSrc, "mc:raw");
        srcs.ref = null;
        if (media.ref !== null) {
            try { srcs.ref = importFootage(media.ref, P.footage.ref, fRef, "mc:ref"); }
            catch (eRef) { warn("reference video not imported (" + eRef.message + ")"); srcs.ref = null; }
        } else if (P.footage.ref !== null) {
            warn("reference video " + P.footage.ref.base + " not found; reference layer skipped");
        }
        main = app.project.items.addComp(M.name, M.w, M.h, 1, T(M.frames, C), C.num / C.den);
        main.parentFolder = fComps;
        setupComp(main, M.frames, M.bg, "mc:main");
        if (P.box !== null) {
            box = app.project.items.addComp(P.box.name, P.box.bw, P.box.bh, 1, T(M.frames, C), C.num / C.den);
            box.parentFolder = fComps;
            setupComp(box, M.frames, [0, 0, 0], "mc:box");
        }
        comps.main = main;
        comps.box = box;
        srcs.box = box;
        for (i = 0; i < P.layers.length; i++) { makeLayer(comps, srcs, P.layers[i]); }
        addMarkers(main);
        try { main.openInViewer(); } catch (eV) { }
        res.main = main;
    }

    function summary(saved) {
        var S = PLAN.summary, lines = [], i, n;
        lines.push("match_cuts: built \"" + PLAN.main.name + "\"" + (saved ? " and saved recreated_edit.aep" : " (NOT saved)"));
        lines.push(S.raw + " RAW segments, " + S.placeholders + " NOT-IN-RAW placeholders, " + S.cuts + " cuts");
        lines.push("duration " + S.duration + " s = " + PLAN.main.frames + " frames at " + C.num + "/" + C.den + " fps");
        n = PLAN.warnings.length + WARN.length;
        if (n > 0) {
            lines.push("Warnings (" + n + "):");
            for (i = 0; i < PLAN.warnings.length && i < 12; i++) { lines.push("- " + PLAN.warnings[i]); }
            for (i = 0; i < WARN.length && i < 12; i++) { lines.push("- " + WARN[i]); }
            if (n > 24) { lines.push("- ... see report.md"); }
        }
        return lines.join("\n");
    }

    var here = new File($.fileName).parent;
    var media = { raw: findMedia(here, PLAN.footage.raw, true), ref: null };
    if (media.raw === null) {
        alert("Cancelled: the RAW video " + PLAN.footage.raw.base + " was not found (next to this script in " +
              PLAN.footage.raw.rel + ", at " + PLAN.footage.raw.abs + ", or via the file dialog). Nothing was built.");
        return;
    }
    if (PLAN.footage.ref !== null) { media.ref = findMedia(here, PLAN.footage.ref, false); }
    var proj = app.newProject();
    if (proj === null) {
        alert("Cancelled: After Effects did not create a new project. Nothing was built.");
        return;
    }
    var res = { main: null }, ok = false;
    app.beginUndoGroup("match_cuts: build Recreated Edit");
    try {
        build(media, res);
        ok = true;
    } catch (e) {
        alert("build_ae_project.jsx failed (line " + e.line + "): " + e.toString());
    } finally {
        app.endUndoGroup();
    }
    if (!ok) { return; }
    var out = new File(here.fsName + "/recreated_edit.aep");
    try { app.project.save(out); } catch (eS) { warn("saving the project did not work (" + eS.message + ")"); }
    if (!out.exists) {
        alert("Could not save recreated_edit.aep next to this script. Enable Preferences > Scripting & " +
              "Expressions > Allow Scripts to Write Files and Access Network (Preferences > General before " +
              "AE 16.1), then run the script again. The project is open but unsaved.");
    }
    alert(summary(out.exists));
})();
"""


# ---------------------------------------------------------------------------------------------
# Node mock
# ---------------------------------------------------------------------------------------------

def _find_node() -> str | None:
    """Node binary: $MATCH_CUTS_NODE, /opt/node22/bin/node, then 'node' on PATH; None if absent."""
    cands = [os.environ.get("MATCH_CUTS_NODE"), "/opt/node22/bin/node", shutil.which("node")]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def run_jsx_in_mock(jsx_path: str | os.PathLike, footage_meta: dict, scenario: str = "default",
                    timeout: float = 300.0) -> dict:
    """Run a JSX through the ES3 gate and the strict Node AE mock (match_cuts/ae_mock).

    footage_meta = {basename: {width, height, fps_num, fps_den, frames, has_audio}}.
    Scenarios: default | media_missing (openDialog returns null -> clean abort) | new_project_null |
    no_marker_property | quantize_time (AE stores startTime to 10 ms and stretch to 1e-3 % -> exercises the
    JSX self-check fallback). Returns the recorded project (see ae_mock.js ``finish``) with ``status`` in
    {ok, script_error, gate_failed, mock_crash, node_error, not_available}."""
    if scenario not in MOCK_SCENARIOS:
        raise ValueError(f"run_jsx_in_mock: unknown scenario {scenario!r} (expected {MOCK_SCENARIOS})")
    node = _find_node()
    if node is None:
        return {"record_type": "ae_mock", "status": "not_available", "scenario": scenario,
                "reason": "Node.js not found (MATCH_CUTS_NODE, /opt/node22/bin/node, PATH)"}
    runner = MOCK_DIR / "run_mock.js"
    for f in (runner, MOCK_DIR / "ae_mock.js", MOCK_DIR / "acorn.js"):
        if not f.exists():
            return {"record_type": "ae_mock", "status": "not_available", "scenario": scenario,
                    "reason": f"mock file missing: {f}"}
    jsx = Path(jsx_path).resolve()
    with tempfile.TemporaryDirectory(prefix="mc_aemock_") as td:
        meta_p = Path(td) / "meta.json"
        out_p = Path(td) / "record.json"
        meta_p.write_text(json.dumps(footage_meta or {}, default=int))
        try:
            res = subprocess.run([node, str(runner), str(jsx), str(meta_p), scenario, str(out_p)],
                                 capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"record_type": "ae_mock", "status": "node_error", "scenario": scenario,
                    "error": f"mock run timed out after {timeout} s"}
        except OSError as e:
            return {"record_type": "ae_mock", "status": "not_available", "scenario": scenario, "reason": str(e)}
        if not out_p.exists():
            return {"record_type": "ae_mock", "status": "node_error", "scenario": scenario,
                    "error": (res.stderr or res.stdout)[-4000:], "returncode": res.returncode}
        rec = json.loads(out_p.read_text())
    rec.setdefault("record_type", "ae_mock")
    rec["node"] = node
    if res.stderr.strip():
        rec["stderr"] = res.stderr[-4000:]
    return rec


def record_layer_problems(PL: dict, RL: dict, F: dict, tol: float = 1e-6) -> list[str]:
    """Compare one mock-recorded layer with its plan layer: every keyed property (transform, opacity,
    audio levels, time remap) must have its keys at the plan's comp times with the plan's values and
    explicit LINEAR (HOLD for frame-exact remap) interpolation; spatial keys linear (no auto-Bezier, zero
    tangents); RAW / pre-comp layers need BEST quality, no frame blending, no motion blur. Key times
    are the recorded LAYER times mapped with the FINAL startTime/stretch, so a JSX that writes keys
    before the layer's timing is set fails here."""
    out: list[str] = []
    props = RL.get("props") or {}

    def check(mn: str, want: list[tuple[float, Any]], hold: bool = False) -> None:
        got = (props.get(mn) or {}).get("keys") or []
        if len(got) != len(want):
            out.append(f"{mn}: {len(got)} keys (plan {len(want)})")
            return
        for g, (t, v) in zip(got, want):
            if abs(float(g["time"]) - t) > tol:
                out.append(f"{mn}: key at {g['time']} s, plan {t} s")
                return
            gv = g["value"] if isinstance(g["value"], list) else [g["value"]]
            wv = v if isinstance(v, list) else [v]
            if any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(gv, wv)):
                out.append(f"{mn}: key value {gv} != plan {wv} at {t} s")
                return
            want_i = "HOLD" if hold else "LINEAR"
            if g.get("inInterp") != want_i or g.get("outInterp") != want_i:
                out.append(f"{mn}: key interpolation {g.get('inInterp')}/{g.get('outInterp')} (want {want_i})")
                return
            if "autoBezier" in g and (g.get("autoBezier") or g.get("continuous")
                                      or any(abs(x) > 0 for x in (g.get("inTangent") or [1]) + (g.get("outTangent") or [1]))):
                out.append(f"{mn}: spatial key is not linear (autoBezier/continuous/tangents)")
                return

    xk = (PL.get("xf") or {}).get("keys") or []
    if xk:
        ts = [_t(k["k"], F) for k in xk]
        check("ADBE Position", list(zip(ts, [k["position"] for k in xk])))
        check("ADBE Scale", list(zip(ts, [k["scale"] for k in xk])))
        if PL["xf"].get("rotKeys"):
            check("ADBE Rotate Z", list(zip(ts, [k["rotation"] for k in xk])))
    if PL.get("opacity"):
        check("ADBE Opacity", [(_t(k["k"], F), k["v"]) for k in PL["opacity"]])
    if PL.get("audioKeys") and PL.get("audio") and RL.get("hasAudio"):
        check("ADBE Audio Levels", [(_t(k["k"], F), [k["v"], k["v"]]) for k in PL["audioKeys"]])
    if PL["timeMode"] == "remap":
        check("ADBE Time Remapping", [(_t(k["k"], F), k["v"]) for k in PL["remap"]])
    elif PL["timeMode"] == "frames":
        # values are checked through simulate_ae(record); here the count, placement and HOLD type
        got = (props.get("ADBE Time Remapping") or {}).get("keys") or []
        if len(got) != len(PL["expect"]):
            out.append(f"ADBE Time Remapping: {len(got)} keys (plan {len(PL['expect'])})")
        elif got and (abs(float(got[0]["time"]) - _t(PL["compIn"], F)) > tol
                      or any(g.get("outInterp") != "HOLD" for g in got)):
            out.append("ADBE Time Remapping: frame-exact keys misplaced or not HOLD")
    if PL["source"] in ("raw", "box") and PL["kind"] != "raw_audio":
        if RL.get("quality") != "BEST" or RL.get("frameBlendingType") != "NO_FRAME_BLEND" or RL.get("motionBlur"):
            out.append(f"render switches quality={RL.get('quality')} frameBlending={RL.get('frameBlendingType')} "
                       f"motionBlur={RL.get('motionBlur')}")
    if bool(RL.get("enabled")) != bool(PL["enabled"]) or bool(RL.get("guideLayer")) != bool(PL["guide"]):
        out.append(f"enabled/guide {RL.get('enabled')}/{RL.get('guideLayer')} (plan {PL['enabled']}/{PL['guide']})")
    if RL.get("hasAudio") and bool(RL.get("audioEnabled")) != bool(PL["audio"]):
        out.append(f"audioEnabled {RL.get('audioEnabled')} (plan {PL['audio']})")
    return out


def mock_verify(jsx_path: str | os.PathLike, plan: dict, footage_meta: dict,
                scenarios: Iterable[str] = ("default", "media_missing", "new_project_null", "no_marker_property"),
                tol: float = 1e-9) -> dict:
    """Criterion-6 mock checks (DESIGN §5 verify c6) for a written JSX against its plan.

    default: status ok, no alert containing 'Error'/'failed', no strict-mock violations, MAIN frameRate ==
    main fps and duration == frames * frameDuration (within tol), work area == whole comp, saved to
    <script dir>/recreated_edit.aep, balanced undo group, every plan layer present with name / startTime /
    stretch / inPoint / outPoint equal to the plan, and simulate_ae(record) == simulate_ae(plan).
    media_missing: openDialog called, clean abort, nothing saved. new_project_null: clean abort.
    no_marker_property: still builds and saves. Returns {status: pass|fail|not_available, details,
    failures, records}."""
    failures: list[str] = []
    details: dict[str, Any] = {"mock_only": True}
    records: dict[str, dict] = {}
    jsx = Path(jsx_path).resolve()
    want_save = str(jsx.parent / "recreated_edit.aep")
    for sc in scenarios:
        rec = run_jsx_in_mock(jsx, footage_meta, sc)
        records[sc] = rec
        if rec.get("status") == "not_available":
            return {"status": "not_available", "details": {"reason": rec.get("reason")}, "failures": [],
                    "records": records}
        if rec.get("status") != "ok":
            failures.append(f"{sc}: mock status {rec.get('status')}: {rec.get('gate_error') or rec.get('error')}")
            continue
        alerts = rec.get("alerts", [])
        bad_alerts = [a for a in alerts if "Error" in a or "failed" in a]
        if sc == "default":
            if bad_alerts:
                failures.append(f"default: alert {bad_alerts[0][:200]!r}")
            if rec.get("mock_errors"):
                failures.append(f"default: strict mock violations {rec['mock_errors'][:3]}")
            if rec.get("saved") != [want_save]:
                failures.append(f"default: saved {rec.get('saved')} (expected [{want_save}])")
            u = rec.get("calls", {})
            if u.get("beginUndoGroup") != 1 or u.get("endUndoGroup") != 1:
                failures.append(f"default: undo groups {u.get('beginUndoGroup')}/{u.get('endUndoGroup')}")
            main = next((c for c in rec.get("comps", []) if c.get("comment") == "mc:main"), None)
            if main is None:
                failures.append("default: no MAIN comp")
                continue
            F = plan["main"]["fps"]
            fr = F["num"] / F["den"]
            dur = _t(plan["main"]["frames"], F)
            details["main"] = {"frameRate": main["frameRate"], "duration": main["duration"],
                               "workAreaDuration": main["workAreaDuration"], "layers": len(main["layers"])}
            if abs(main["frameRate"] - fr) > tol:
                failures.append(f"default: MAIN frameRate {main['frameRate']} != {fr}")
            if abs(main["duration"] - dur) > tol or abs(main["duration"] - plan["main"]["frames"] / main["frameRate"]) > 1e-6:
                failures.append(f"default: MAIN duration {main['duration']} != {dur}")
            if abs(main["workAreaStart"]) > tol or abs(main["workAreaDuration"] - main["duration"]) > tol:
                failures.append("default: work area is not the whole comp")
            by_tag = {}
            for c in rec.get("comps", []):
                for L in c["layers"]:
                    by_tag[str(L.get("comment", ""))] = L
            n_checked = 0
            for PL in plan["layers"]:
                RL = by_tag.get("mc:" + PL["id"])
                if RL is None:
                    if PL["kind"] == "reference" and plan["footage"].get("ref") is not None:
                        failures.append("default: reference layer missing")
                    elif PL["kind"] != "reference":
                        failures.append(f"default: layer {PL['id']} missing")
                    continue
                n_checked += 1
                if RL["name"] != PL["name"]:
                    failures.append(f"default: {PL['id']} name {RL['name']!r} != {PL['name']!r}")
                exp_start = PL["startTime"]
                exp_stretch = PL["stretch"] if PL["timeMode"] == "stretch" else 100.0
                for key, exp in (("startTime", exp_start), ("inPoint", PL["inPoint"]), ("outPoint", PL["outPoint"])):
                    if abs(float(RL[key]) - float(exp)) > tol:
                        failures.append(f"default: {PL['id']} {key} {RL[key]} != {exp}")
                if PL["timeMode"] in ("stretch", "remap", "frames") and abs(float(RL["stretch"]) - float(exp_stretch)) > tol:
                    failures.append(f"default: {PL['id']} stretch {RL['stretch']} != {exp_stretch}")
                if bool(RL.get("timeRemapEnabled")) != (PL["timeMode"] in ("remap", "frames")):
                    failures.append(f"default: {PL['id']} timeRemapEnabled {RL.get('timeRemapEnabled')} (plan {PL['timeMode']})")
                failures.extend(f"default: {PL['id']} {m}" for m in record_layer_problems(PL, RL, F))
            for c in rec.get("comps", []):
                if c.get("frameBlending") or c.get("motionBlur"):
                    failures.append(f"default: comp {c.get('name')} has frame blending / motion blur on")
            for f in rec.get("footage", []):
                if f.get("comment") == "mc:raw" and f.get("fieldSeparationType") != "OFF":
                    failures.append("default: RAW footage field separation is not OFF")
            details["layers_checked"] = n_checked
            sim_p = raw_frames_by_layer(simulate_ae(plan))
            sim_r = raw_frames_by_layer(simulate_ae(rec))
            diff = [lid for lid in sorted(set(sim_p) | set(sim_r)) if sim_p.get(lid) != sim_r.get(lid)]
            details["sim_layers"] = len(sim_p)
            if diff:
                failures.append(f"default: simulate_ae(record) != simulate_ae(plan) for {diff[:5]}")
        elif sc == "media_missing":
            if rec.get("calls", {}).get("openDialog", 0) < 1:
                failures.append("media_missing: File.openDialog was not called")
            if rec.get("saved"):
                failures.append("media_missing: a project was saved")
            if bad_alerts or not any(a.startswith("Cancelled") for a in alerts):
                failures.append(f"media_missing: no clean abort (alerts {alerts[:2]})")
        elif sc == "new_project_null":
            if rec.get("saved") or not any(a.startswith("Cancelled") for a in alerts):
                failures.append(f"new_project_null: no clean abort (alerts {alerts[:2]})")
        elif sc == "no_marker_property":
            if rec.get("saved") != [want_save] or bad_alerts:
                failures.append(f"no_marker_property: did not build/save cleanly (alerts {alerts[:2]})")
    return {"status": "fail" if failures else "pass", "details": details, "failures": failures, "records": records}
