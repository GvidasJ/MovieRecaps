"""Stage 8 frame-exact preview renderer, audio rebuild and compare video (prompt Stage 8; DESIGN.md §5
render_preview.py).

``make_context``    RenderContext: every per-layer number the renderer needs, derived from the cutlist
                    with the SAME semantics as ``export_ae.ae_plan`` (so the preview is what After Effects
                    renders): layout geometry (§2.3/§2.4), MAIN size and fps (§2.5), one layer per segment,
                    stacked like the AE comp (dips / flashes on top, then segments chronologically, first
                    on top), crossfade / dip opacity keys.
``render_frame``    one MAIN frame (BGR uint8) from a dict {RAW frame index: full-res BGR image}.
``render_preview``  preview_recreation.mp4: per segment ONE seek + sequential PTS-indexed decode
                    (media.VideoReader; overlapping segments get their own reader), ffmpeg pipe (H.264,
                    CRF <= 16, yuv420p, +faststart), audio from :func:`build_audio` muxed as AAC.
``build_audio``     sample-accurate RAW audio rebuild on the MAIN timeline.
``render_compare``  compare.mp4: competitor | recreation (match geometry) | |diff| x 4, 960 px high, frame
                    number / timecode / segment id in a label strip above each panel (never over the
                    picture, where the competitor's logo / name / title sit), competitor audio.

Rendering model (identical to the AE project, DESIGN §2.1-2.5, §3, §5 export_ae)
---------------------------------------------------------------------------------
* RAW frame of a layer at MAIN frame K (AE rule): stretch layers
  ``floor(raw_fps * (raw_in' + v * (K - K_in) / main_fps) + 1e-9)`` with ``raw_in'`` re-anchored to the
  MAIN grid (§2.5; identical to ``phase_solve.ae_frame`` when MAIN = competitor fps); remap layers
  ``floor(remap(K) * raw_fps + 1e-9)`` (time_remap_keys linear in MAIN frames, held outside). Clamped to
  [0, n_raw) (AE holds the first / last frame).
* Transform: canonical Sim (+flip) -> ``geometry.to_cv_matrix`` -> ``cv2.warpAffine`` (bilinear) of the
  full-res RAW frame; animated framing via ``geometry.interpolate_keys`` (AE-linear); match: Sim scaled by
  r = MAIN px / competitor px; fill: ``export_ae.fill_transform`` (§2.4, local fallback); source: identity
  (scaled to fit when a different size is requested).
* Compositing in premultiplied float: a RAW layer's alpha is its bilinear coverage (warped ones), a
  solid's is 1; ``C = op*Cl + (1 - op*Al) * C`` bottom-up. Crossfade = only the upper (outgoing) layer A is
  keyed ``1 - alpha_B`` => ``out = (1 - alpha_B) A + alpha_B B``; dips key the solid above both.
* match: the segment stack is rendered over the Video Box pre-comp rectangle (bx0, by0, bw, bh) and
  composited with the anti-aliased rounded-box coverage over the background (solid colour, or a blurred
  cover-scaled copy of the pre-comp as in the AE plan); fill / source: over black.
* match, per-period layout (DESIGN §7 D1; placement = ``export_ae._PlanBuilder.place``): a segment whose
  ``Segment.box`` differs from the layout box (by > export_ae.BOX_TOL_PX = 0.5 px; a fullscreen period:
  the whole canvas, radius 0) is a MAIN-level layer -- composited directly on the MAIN canvas ABOVE the
  Video Box composite and the background (below the disabled reference / guide layers), with the
  canonical Sim x r at origin (0, 0), clipped by the anti-aliased (rounded-)rect of its own box x r (no
  clip for the whole canvas); dips / flashes / NOT-IN-RAW solids there are export_ae's round(w) x
  round(h) solids centred on the box. MAIN-level layers keep the segment-stack order among themselves
  (dips / flashes, then chronological, first on top); without a Video Box the clipped layers keep their
  place in the single MAIN stack. A crossfade from a Video-Box layer into a MAIN-level one keys the
  incoming (upper) layer rising, so the pair still renders (1 - alpha_B) A + alpha_B B; dips keep keying
  the dip solid. fill: a segment's own box is the box its fill framing is computed from.
* NOT-IN-RAW: a solid of export_ae's placeholder colour with the placeholder label drawn on it.
"""
from __future__ import annotations

import copy
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import numpy as np

from .common import ffmpeg_bin, fps_str, log, parse_fps, timecode
from .geometry import CORNER_TO_CV, CV_TO_CORNER, Sim, interpolate_keys, to_cv_matrix, translate3, h3
from .model import Box, Cutlist, Segment

__all__ = ["RenderContext", "Layer", "make_context", "render_frame", "render_preview", "build_audio",
           "render_compare", "compare_geometry", "iter_render", "layer_raw_frame", "frame_sources",
           "fill_transform_local", "rounded_box_coverage", "sample_positions", "load_raw_audio"]

AE_EPS = 1e-9
PLACEHOLDER_RGB = (0.85, 0.1, 0.55)        # export_ae.PLACEHOLDER_RGB (AE solid colour, 0..1)
DEFAULT_BLURRINESS = 50.0                  # export_ae.DEFAULT_BLURRINESS
MIN_GAIN = 1e-3                            # export_ae.MIN_GAIN (-60 dB floor of crossfade audio keys)
LAYOUT_MODES = ("match", "fill", "source")
COMPARE_HEIGHT = 960
COMPARE_MAX_WIDTH = 3840
DIFF_GAIN = 4.0
STRIP_BGR = (24, 24, 24)                   # compare.mp4 label strip (text never covers the picture)


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

def _hex_bgr(c: Any, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """'#rrggbb' / [r, g, b] (0..1 or 0..255) -> BGR floats 0..255."""
    if c is None:
        return default
    if isinstance(c, str):
        s = c.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return default
        try:
            r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return default
        return (float(b), float(g), float(r))
    try:
        vals = [float(x) for x in c][:3]
    except (TypeError, ValueError):
        return default
    if len(vals) != 3:
        return default
    if max(vals) <= 1.0:
        vals = [round(v * 255.0) for v in vals]
    return (vals[2], vals[1], vals[0])


def _ascii(s: str) -> str:
    rep = {"–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "..."}
    for a, b in rep.items():
        s = s.replace(a, b)
    return s.encode("ascii", "replace").decode("ascii")


def _parse_size(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return int(value[0]), int(value[1])
    s = str(value).strip().lower()
    if s in ("", "competitor"):
        return None
    m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", s)
    if not m:
        raise ValueError(f"invalid size {value!r} (expected WxH)")
    return int(m.group(1)), int(m.group(2))


def _interp_keys(keys: Sequence[tuple[float, float]], x: float) -> float:
    """Linear interpolation of (time, value) keys, held before the first / after the last key (AE)."""
    if x <= keys[0][0]:
        return float(keys[0][1])
    if x >= keys[-1][0]:
        return float(keys[-1][1])
    for (ta, va), (tb, vb) in zip(keys[:-1], keys[1:]):
        if ta <= x <= tb:
            return float(va) if tb == ta else float(va + (x - ta) / (tb - ta) * (vb - va))
    return float(keys[-1][1])


def _transition_dict(t: Any) -> dict | None:
    if not t:
        return None
    return t if isinstance(t, dict) else dict(vars(t))


def fill_transform_local(sim: Sim, flip: bool, box: Box | dict | None, raw_wh: tuple[float, float],
                         target_wh: tuple[float, float]) -> Sim:
    """DESIGN §2.4 fill-mode framing (identical to export_ae.fill_transform; used when that module is not
    importable): the RAW point at the centre of the competitor box goes to the frame centre, zoom =
    s * cover_frame / cover_box, clamped so no empty edges show."""
    if box is None:
        raise ValueError("fill_transform needs the competitor box")
    b = box if isinstance(box, Box) else Box.from_dict(box)
    W, H = float(raw_wh[0]), float(raw_wh[1])
    Wt, Ht = float(target_wh[0]), float(target_wh[1])
    if min(W, H, Wt, Ht, b.w, b.h) <= 0 or sim.s <= 0:
        raise ValueError("fill_transform: sizes and scale must be positive")
    cover_box = max(b.w / W, b.h / H)
    cover_frame = max(Wt / W, Ht / H)
    c, sn = math.cos(sim.theta), math.sin(sim.theta)
    dx, dy = b.x + b.w / 2.0 - sim.tx, b.y + b.h / 2.0 - sim.ty
    px, py = (c * dx + sn * dy) / sim.s, (-sn * dx + c * dy) / sim.s
    ax = (Wt / 2.0) * abs(c) + (Ht / 2.0) * abs(sn)
    ay = (Wt / 2.0) * abs(sn) + (Ht / 2.0) * abs(c)
    s_new = max(sim.s * cover_frame / cover_box, max(2.0 * ax / W, 2.0 * ay / H))
    lo_x, hi_x, lo_y, hi_y = ax / s_new, W - ax / s_new, ay / s_new, H - ay / s_new
    px = W / 2.0 if lo_x > hi_x else min(max(px, lo_x), hi_x)
    py = H / 2.0 if lo_y > hi_y else min(max(py, lo_y), hi_y)
    return Sim(float(s_new), float(sim.theta_deg), float(Wt / 2.0 - s_new * (c * px - sn * py)),
               float(Ht / 2.0 - s_new * (sn * px + c * py)))


BOX_TOL_PX = 0.5                           # export_ae.BOX_TOL_PX (competitor px)


def _same_box_local(a: Box, b: Box, tol: float = BOX_TOL_PX) -> bool:
    """export_ae.same_box: two CORNER boxes within tol px on every edge and radius."""
    return (abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol and abs(a.w - b.w) <= tol and abs(a.h - b.h) <= tol
            and abs(float(a.corner_radius) - float(b.corner_radius)) <= tol)


def _is_full_canvas_local(b: Box, w: float, h: float, tol: float = BOX_TOL_PX) -> bool:
    """export_ae.is_full_canvas: the box covers the whole competitor canvas (no mask needed)."""
    return (b.x <= tol and b.y <= tol and b.x + b.w >= w - tol and b.y + b.h >= h - tol
            and float(b.corner_radius) <= tol)


def _export_ae_attr(name: str, fallback: Any) -> Any:
    """export_ae's own implementation of a placement helper (so the preview follows the AE plan), else the
    local copy."""
    try:
        from . import export_ae
    except ImportError:  # pragma: no cover - export_ae is a sibling module
        return fallback
    return getattr(export_ae, name, fallback)


def _fill_transform() -> Callable[..., Sim]:
    try:
        from .export_ae import fill_transform
        return fill_transform
    except ImportError:  # pragma: no cover - export_ae is a sibling module
        return fill_transform_local


def rounded_box_coverage(w: int, h: int, x: float, y: float, bw: float, bh: float, radius: float,
                         ss: int = 4) -> np.ndarray:
    """Anti-aliased coverage (float32 [h, w]) of the rounded rectangle [x, x+bw] x [y, y+bh] (CORNER
    coordinates, sub-pixel position allowed) with corner radius `radius`, ss x ss super-sampled -- the
    generalisation of geometry.rounded_rect_mask to a float rectangle (= the AE mask on the pre-comp)."""
    r = max(0.0, min(float(radius), bw / 2.0, bh / 2.0))
    offs = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(w)[:, None] + offs[None, :]).reshape(-1)
    ys = (np.arange(h)[:, None] + offs[None, :]).reshape(-1)
    x0, x1, y0, y1 = float(x), float(x) + float(bw), float(y), float(y) + float(bh)
    in_x = (xs >= x0) & (xs <= x1)
    in_y = (ys >= y0) & (ys <= y1)
    if r > 0:
        dx = (xs - np.clip(xs, x0 + r, x1 - r))[None, :]
        dy = (ys - np.clip(ys, y0 + r, y1 - r))[:, None]
        inside = ((dx * dx + dy * dy) <= r * r) & in_x[None, :] & in_y[:, None]
    else:
        inside = in_x[None, :] & in_y[:, None]
    return inside.reshape(h, ss, w, ss).mean(axis=(1, 3)).astype(np.float32)


def _box_geometry(box: Box, r: float) -> dict:
    """Integer Video Box pre-comp rectangle (DESIGN §2.3) and the float mask inside it."""
    x0, y0 = box.x * r, box.y * r
    x1, y1 = (box.x + box.w) * r, (box.y + box.h) * r
    bx0, by0 = math.floor(x0 + 1e-9), math.floor(y0 + 1e-9)
    bw, bh = math.ceil(x1 - 1e-9) - bx0, math.ceil(y1 - 1e-9) - by0
    return {"bx0": int(bx0), "by0": int(by0), "bw": int(bw), "bh": int(bh),
            "mask": (x0 - bx0, y0 - by0, box.w * r, box.h * r, max(0.0, float(box.corner_radius) * r))}


# ---------------------------------------------------------------------------------------------
# Render context
# ---------------------------------------------------------------------------------------------

@dataclass
class Layer:
    """One layer of the segment stack (MAIN frame units, MAIN pixels)."""
    idx: int
    kind: str                                   # 'raw' | 'solid'
    seg_id: int
    seg_type: str
    k_in: int                                   # active on MAIN frames [k_in, k_out)
    k_out: int
    opacity: list[tuple[float, float]] = field(default_factory=list)   # (MAIN frame, 0..1), [] = 1
    raw_in: float = 0.0                         # RAW seconds at k_in (re-anchored to the MAIN grid)
    speed: float = 1.0
    remap: list[tuple[float, float]] | None = None                     # (MAIN frame, RAW seconds)
    flip: bool = False
    sim: Sim | None = None                      # constant transform (MAIN px, CORNER)
    keys: list[dict] = field(default_factory=list)                     # interpolate_keys dicts (MAIN)
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)                # BGR 0..255 (solids)
    label: str = ""                             # NOT-IN-RAW placeholder / UNCERTAIN label (drawn)
    mix: bool = False                           # verified frame-blend path (FX-08): AE Frame Mix = linear blend of
                                                #   RAW floor(p) and floor(p) + 1 by the fraction of p
    main: bool = False                          # D1: MAIN-level layer (Segment.box set) above the Video Box
    clip: tuple[float, float, float, float, float] | None = None       # MAIN-level clip (x, y, w, h, radius)
    #                                             MAIN px, CORNER; None = no mask (the whole canvas)

    def active(self, K: int) -> bool:
        return self.k_in <= K < self.k_out

    def op(self, K: int) -> float:
        return 1.0 if not self.opacity else min(1.0, max(0.0, _interp_keys(self.opacity, float(K))))


@dataclass
class RenderContext:
    """Everything render_frame needs (see module docstring)."""
    cutlist: Cutlist
    layout_mode: str
    size: tuple[int, int]                       # MAIN (W, H)
    fps: Fraction                               # MAIN fps
    n_frames: int                               # MAIN frames
    comp_fps: Fraction
    raw_fps: Fraction
    raw_size: tuple[int, int]
    n_raw: int
    r: float                                    # MAIN px / competitor px (match); 1 otherwise
    roi: tuple[int, int, int, int]              # segment stack area in MAIN px (pre-comp rect or frame)
    mask: np.ndarray | None                     # float32 box coverage over roi (None = no box)
    bg_type: str                                # 'solid' | 'blur' | 'none'
    bg_color: tuple[float, float, float]        # BGR 0..255
    blur_sigma: float                           # pre-comp px (blur background)
    layers: list[Layer]                         # top first (MAIN-level layers, then the Video Box stack)
    raw_path: str | None = None
    placeholder_color: tuple[float, float, float] = (140.0, 26.0, 217.0)
    interp: int = 1                             # cv2.INTER_LINEAR
    warnings: list[str] = field(default_factory=list)
    cache: dict = field(default_factory=dict, repr=False)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
            log.warning("render_preview: %s", msg)


def _to_main(k: int, main_fps: Fraction, comp_fps: Fraction) -> int:
    """K = floor(k * main_fps / comp_fps + 1/2) exactly (§2.5); identity on the same grid."""
    if main_fps == comp_fps:
        return int(k)
    return math.floor(Fraction(int(k)) * main_fps / comp_fps + Fraction(1, 2))


def _to_main_f(k: float, main_fps: Fraction, comp_fps: Fraction) -> float:
    if main_fps == comp_fps:
        return float(k)
    return float(Fraction(k) * main_fps / comp_fps)


def _transition_keys(segs: list[Segment], kmap: dict[int, tuple[int, int]], main_fps: Fraction,
                     comp_fps: Fraction, main_ids: set[int] | frozenset[int] = frozenset()
                     ) -> tuple[dict[int, dict[float, float]], dict[int, dict[float, float]]]:
    """Opacity keys (0..1) and crossfade Audio Levels keys (dB) per segment id, exactly as export_ae.ae_plan
    writes them: only the UPPER layer of a pair is keyed (the outgoing one, or the dip solid). main_ids =
    the MAIN-level segments (D1, above the Video Box): a crossfade from a Video-Box layer into a MAIN-level
    one keys the incoming (upper) layer rising; dips keep keying the dip solid (export_ae warns when its
    neighbour is a MAIN-level layer above it)."""
    opacity: dict[int, dict[float, float]] = {}
    audio_db: dict[int, dict[float, float]] = {}
    ordered = [s for s in segs if int(s.id) in kmap]
    for X, Y in zip(ordered[:-1], ordered[1:]):
        tr = _transition_dict(Y.transition_in) or _transition_dict(X.transition_out)
        if not tr:
            continue
        ttype = str(tr.get("type", "crossfade"))
        O = int(Y.comp_in)
        D = int(tr.get("duration_frames", int(X.comp_out) - O) or 0)
        if D <= 0:
            continue
        D_eff = min(D, int(X.comp_out) - O)
        if D_eff <= 0:
            continue
        alpha = list(tr.get("alpha") or [])
        if len(alpha) != D:
            alpha = [i / D for i in range(D)]
        alpha = [min(1.0, max(0.0, float(a))) for a in alpha][:D_eff]
        is_dip = ttype.startswith("dip") or X.type == "dip" or Y.type == "dip"
        if is_dip and Y.type == "dip":
            uid, rising = int(Y.id), True
        elif is_dip and X.type == "dip":
            uid, rising = int(X.id), False
        elif int(Y.id) in main_ids and int(X.id) not in main_ids:
            uid, rising = int(Y.id), True                  # the incoming layer is above the outgoing one
        else:
            uid, rising = int(X.id), False
        ks = opacity.setdefault(uid, {})
        f = lambda k: _to_main_f(k, main_fps, comp_fps)  # noqa: E731
        if rising:
            for i, a in enumerate(alpha):
                ks[f(O + i)] = a
            ks[f(O + D_eff)] = 1.0
        else:
            ks.setdefault(f(O - 1), 1.0)
            for i, a in enumerate(alpha):
                ks[f(O + i)] = 1.0 - a
            ks[f(O + D_eff)] = 0.0
        if not is_dip and X.type == "raw" and Y.type == "raw":
            ax = audio_db.setdefault(int(X.id), {})
            ay = audio_db.setdefault(int(Y.id), {})
            for i, a in enumerate(alpha):
                ax[f(O + i)] = 20.0 * math.log10(max(1.0 - a, MIN_GAIN))
                ay[f(O + i)] = 20.0 * math.log10(max(a, MIN_GAIN))
            ay[f(O + D_eff)] = 0.0
    return opacity, audio_db


def _seg_raw_in(seg: Segment, raw_fps: Fraction) -> float | None:
    if seg.raw_in_seconds is not None:
        return float(seg.raw_in_seconds)
    if seg.raw_in_frame is not None:
        return float((Fraction(int(seg.raw_in_frame)) + Fraction(1, 2)) / raw_fps)
    return None


def make_context(cutlist: Cutlist, cfg: Any, layout_mode: str | None = None,
                 target_size: tuple[int, int] | None = None, fps: Fraction | str | None = None) -> RenderContext:
    """Build the RenderContext (DESIGN §5). layout_mode defaults to cfg.layout_mode; target_size to the MAIN
    size of that mode (match: --comp-size keeping the competitor aspect, else competitor size; fill:
    --comp-size or 1080x1920; source: RAW size); fps to main_fps (§2.5)."""
    from .config import Config
    cfg = cfg if cfg is not None else Config()
    mode = str(layout_mode or getattr(cfg, "layout_mode", None) or (cutlist.layout or {}).get("mode") or "match")
    if mode not in LAYOUT_MODES:
        raise ValueError(f"render_preview: unknown layout mode {mode!r} (expected {LAYOUT_MODES})")
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    fps_mode = str(getattr(cfg, "fps_mode", "competitor") or "competitor")
    if fps is not None:
        main_fps = parse_fps(fps)
    else:
        main_fps = raw_fps if (fps_mode == "source" or mode == "source") else comp_fps
    Nc = int(cutlist.competitor["frames"])
    N = _to_main(Nc, main_fps, comp_fps)
    Wc, Hc = int(cutlist.competitor["width"]), int(cutlist.competitor["height"])
    raw_w, raw_h = int(cutlist.raw["width"]), int(cutlist.raw["height"])
    n_raw = int(cutlist.raw.get("frames") or 0) or 1 << 30
    lay = cutlist.layout or {}
    box = Box.from_dict(lay["box"]) if lay.get("box") else None
    req = _parse_size(target_size) if target_size is not None else _parse_size(getattr(cfg, "comp_size", None))

    r = 1.0
    if mode == "source":
        W, H = req if (target_size is not None and req) else (raw_w, raw_h)
    elif mode == "fill":
        W, H = req if req else (1080, 1920)
    else:
        if req is None:
            W, H = Wc, Hc
        else:
            r = min(req[0] / Wc, req[1] / Hc)
            if abs(Wc * r - req[0]) > 1.0 or abs(Hc * r - req[1]) > 1.0:
                raise ValueError(f"size {req[0]}x{req[1]} does not keep the competitor aspect {Wc}x{Hc}; "
                                 "the match layout needs a proportional size")
            W, H = req
    W, H = int(W), int(H)

    # segment-stack area + box coverage + background
    roi, mask = (0, 0, W, H), None
    bg_type, bg_color, blur_sigma = "none", (0.0, 0.0, 0.0), 0.0
    warnings: list[str] = []
    if mode == "match":
        bg = lay.get("background_detail") or {"type": lay.get("background", "solid"),
                                              "color": lay.get("canvas_bg", "#000000")}
        bg_color = _hex_bgr(bg.get("color") or lay.get("canvas_bg") or "#000000")
        bg_type = "solid"
        if box is not None:
            g = _box_geometry(box, r)
            roi = (g["bx0"], g["by0"], g["bw"], g["bh"])
            mx, my, mw, mh, mr = g["mask"]
            mask = rounded_box_coverage(g["bw"], g["bh"], mx, my, mw, mh, mr)
            if str(bg.get("type", "solid")) == "blur":
                amount = bg.get("blurriness") or bg.get("ae_blurriness")
                if amount is None and bg.get("sigma") is not None:
                    amount = 3.0 * float(bg["sigma"])
                amount = min(3000.0, max(0.0, float(amount if amount is not None else DEFAULT_BLURRINESS)))
                bg_type, blur_sigma = "blur", amount / 3.0
            elif str(bg.get("type", "solid")) not in ("solid", "blur"):
                warnings.append(f"background type {bg.get('type')!r} rendered as a solid")
        if lay.get("regions"):
            warnings.append(f"{len(lay['regions'])} extra video region(s) are not recreated (dominant box only)")

    try:
        from .export_ae import PLACEHOLDER_RGB as ph_rgb
    except ImportError:  # pragma: no cover
        ph_rgb = PLACEHOLDER_RGB
    placeholder = _hex_bgr(list(ph_rgb))
    fill_tf = _fill_transform()
    fill_box = box or Box(0.0, 0.0, float(Wc), float(Hc))

    same_box = _export_ae_attr("same_box", _same_box_local)
    is_full_canvas = _export_ae_attr("is_full_canvas", _is_full_canvas_local)

    def place(seg: Segment) -> dict:
        """Per-segment placement, exactly export_ae._PlanBuilder.place (DESIGN §7 D1): the segment's own box
        (Segment.box; None, or equal to the layout box -> the dominant layout) -> match: a clipped layer
        directly in MAIN, above the Video Box when there is one ('main'), unclipped when the box is the
        whole canvas ('full'); fill: that box frames the segment (fill_transform); source: ignored."""
        own = None
        if seg.box:
            try:
                own = seg.box if isinstance(seg.box, Box) else Box.from_dict(seg.box)
                if not all(math.isfinite(float(v)) for v in (own.x, own.y, own.w, own.h, own.corner_radius)):
                    raise ValueError("non-finite value")
                if own.w <= 0 or own.h <= 0:
                    raise ValueError("no area")
            except (KeyError, TypeError, ValueError) as e:
                warnings.append(f"S{int(seg.id):02d}: invalid per-segment box ignored ({e})")
                own = None
        if mode == "source":
            return {"own": None, "full": True, "fill_box": None, "main": False}
        if mode == "fill":
            return {"own": None, "full": True, "fill_box": own or fill_box, "main": False}
        if own is not None and box is not None and same_box(own, box):
            own = None
        if own is None:
            return {"own": None, "full": box is None, "fill_box": None, "main": False}
        return {"own": own, "full": bool(is_full_canvas(own, Wc, Hc)), "fill_box": None, "main": box is not None}

    def clip_of(pl: dict, solid: bool) -> tuple[float, float, float, float, float] | None:
        """MAIN px clip (x, y, w, h, radius) of a segment at its own box; None = no mask. RAW layers: the
        (rounded-)rect mask at box x r, none for the whole canvas. Solids: export_ae's round(w) x round(h)
        solid centred on the box, rounded corners only when not the whole canvas."""
        own = pl["own"]
        if own is None:
            return None
        x, y, w, h, rad = own.x * r, own.y * r, own.w * r, own.h * r, max(0.0, float(own.corner_radius) * r)
        if solid:
            sw, sh = max(4, min(30000, round(w))), max(4, min(30000, round(h)))
            x, y, w, h = x + w / 2.0 - sw / 2.0, y + h / 2.0 - sh / 2.0, float(sw), float(sh)
            rad = rad if not pl["full"] else 0.0
            if rad <= 0.0 and x <= 1e-6 and y <= 1e-6 and x + w >= W - 1e-6 and y + h >= H - 1e-6:
                return None
            return float(x), float(y), float(w), float(h), float(rad)
        if pl["full"]:
            return None
        return float(x), float(y), float(w), float(h), float(rad)

    def to_main_sim(sim: Sim, flip: bool, fbox: Box | None = None) -> Sim:
        if mode == "match":
            return Sim(sim.s * r, sim.theta_deg, sim.tx * r, sim.ty * r)
        if mode == "fill":
            return fill_tf(sim, flip, fbox or fill_box, (raw_w, raw_h), (W, H))
        s = min(W / raw_w, H / raw_h)
        return Sim(s, 0.0, (W - s * raw_w) / 2.0, (H - s * raw_h) / 2.0)

    segs = sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.comp_out), int(s.id)))
    kmap: dict[int, tuple[int, int]] = {}
    places: dict[int, dict] = {}
    upper: list[Layer] = []
    chrono: list[Layer] = []
    for seg in segs:
        sid = int(seg.id)
        k_in, k_out = _to_main(int(seg.comp_in), main_fps, comp_fps), _to_main(int(seg.comp_out), main_fps, comp_fps)
        if k_out <= k_in:
            warnings.append(f"S{sid:02d}: vanishes on the {fps_str(main_fps)} MAIN grid")
            continue
        kmap[sid] = (k_in, k_out)
        pl = place(seg)
        places[sid] = pl
        if seg.type == "uncertain":
            # FX-08: what AE renders is the amber UNCERTAIN solid (the evidence is a guide layer, never rendered)
            from .export_ae import UNCERTAIN_RGB
            col = tuple(255.0 * float(c) for c in list(UNCERTAIN_RGB)[:3][::-1])
            chrono.append(Layer(0, "solid", sid, "uncertain", k_in, k_out, color=col,
                                label=seg.label or f"UNCERTAIN ({timecode(k_in, main_fps)}-{timecode(k_out, main_fps)})"))
            continue
        if seg.type == "raw":
            v = float(seg.speed)
            raw_in = _seg_raw_in(seg, raw_fps)
            remap = None
            if seg.time_remap_keys:
                remap = sorted(((_to_main_f(float(d["comp_frame"]), main_fps, comp_fps), float(d["raw_seconds"]))
                                for d in seg.time_remap_keys), key=lambda t: t[0])
                if raw_in is None:
                    raw_in = remap[0][1]
            if raw_in is None:
                warnings.append(f"S{sid:02d}: no raw_in_seconds / raw_in_frame; rendered as a placeholder")
                chrono.append(Layer(0, "solid", sid, "not_in_raw", k_in, k_out, color=placeholder,
                                    label=f"S{sid:02d} (no RAW timing)"))
                continue
            err = float(Fraction(k_in) / main_fps - Fraction(int(seg.comp_in)) / comp_fps)
            raw_in_m = raw_in + v * err if err else raw_in
            flip = bool(seg.flip_h) and mode != "source"
            keys = sorted(seg.transform_keys or [], key=lambda d: float(d["comp_frame"]))
            if mode == "source":
                sim, mkeys = to_main_sim(Sim(), False), []
            elif keys:
                mkeys = []
                for kd in keys:
                    s2 = to_main_sim(Sim.from_dict(kd), flip, pl["fill_box"])
                    mkeys.append({"comp_frame": _to_main_f(float(kd["comp_frame"]), main_fps, comp_fps),
                                  **s2.to_dict()})
                sim = None
            elif seg.transform:
                sim, mkeys = to_main_sim(Sim.from_dict(seg.transform), flip, pl["fill_box"]), []
            else:
                warnings.append(f"S{sid:02d}: no transform; identity used")
                sim, mkeys = to_main_sim(Sim(), flip, pl["fill_box"]), []
            mix = seg.frame_mix
            chrono.append(Layer(0, "raw", sid, "raw", k_in, k_out, raw_in=raw_in_m, speed=v, remap=remap,
                                flip=flip, sim=sim, keys=mkeys, mix=mix))
        elif seg.type in ("dip", "flash"):
            if seg.type == "dip":
                tt = str((_transition_dict(seg.transition_in) or {}).get("type", "")) + \
                    str((_transition_dict(seg.transition_out) or {}).get("type", ""))
                col = seg.color or ("#ffffff" if "white" in tt else "#000000")
            else:
                col = seg.color or "#ffffff"
            upper.append(Layer(0, "solid", sid, seg.type, k_in, k_out, color=_hex_bgr(col)))
        else:
            label = seg.label or f"MISSING - not in RAW ({timecode(k_in, main_fps)}-{timecode(k_out, main_fps)})"
            chrono.append(Layer(0, "solid", sid, "not_in_raw", k_in, k_out, color=placeholder, label=label))
    main_ids: set[int] = set()
    for L in upper + chrono:
        pl = places[L.seg_id]
        L.main = bool(pl["main"])
        L.clip = clip_of(pl, L.kind == "solid")
        if L.main:
            main_ids.add(L.seg_id)
    opacity, _ = _transition_keys(segs, kmap, main_fps, comp_fps, main_ids)
    # final stack (top first): MAIN-level layers above the Video Box composite, each group in the
    # segment-stack order (dips / flashes, then chronological, first on top); without a Video Box the
    # clipped own-box layers keep their place in the one MAIN stack (export_ae)
    layers = [L for L in upper + chrono if L.main] + [L for L in upper + chrono if not L.main]
    for i, L in enumerate(layers):
        L.idx = i
        if L.seg_id in opacity:
            L.opacity = sorted(opacity[L.seg_id].items())
    import cv2
    ctx = RenderContext(cutlist=cutlist, layout_mode=mode, size=(W, H), fps=main_fps, n_frames=N,
                        comp_fps=comp_fps, raw_fps=raw_fps, raw_size=(raw_w, raw_h), n_raw=n_raw, r=r, roi=roi,
                        mask=mask, bg_type=bg_type, bg_color=bg_color, blur_sigma=blur_sigma, layers=layers,
                        raw_path=_raw_path_from_cutlist(cutlist, cfg), placeholder_color=placeholder,
                        interp=cv2.INTER_LINEAR)
    for w in warnings:
        ctx.warn(w)
    return ctx


def _raw_path_from_cutlist(cutlist: Cutlist, cfg: Any) -> str | None:
    for key in ("file_abs", "file", "file_rel", "source_path"):
        f = cutlist.raw.get(key)
        if not f:
            continue
        p = Path(str(f))
        if not p.is_absolute():
            p = Path(getattr(cfg, "out_dir", ".") or ".") / p
        if p.exists():
            return str(p)
    return None


# ---------------------------------------------------------------------------------------------
# Per-frame rendering
# ---------------------------------------------------------------------------------------------

def _layer_pos(L: Layer, K: int, ctx: RenderContext) -> float:
    """Continuous RAW position (frames) of a RAW layer at MAIN frame K."""
    rf = float(ctx.raw_fps)
    if L.remap:
        return _interp_keys(L.remap, float(K)) * rf
    return rf * (L.raw_in + L.speed * ((K - L.k_in) / float(ctx.fps)))


def layer_raw_frame(L: Layer, K: int, ctx: RenderContext) -> int:
    """RAW frame a RAW layer shows at MAIN frame K (AE rule, clamped to the RAW extent)."""
    j = math.floor(_layer_pos(L, K, ctx) + AE_EPS)
    return int(min(max(j, 0), ctx.n_raw - 1))


def layer_mix(L: Layer, K: int, ctx: RenderContext) -> float:
    """Weight of RAW frame j + 1 in a Frame Mix layer at MAIN frame K (0 for any other layer)."""
    if not L.mix:
        return 0.0
    j = layer_raw_frame(L, K, ctx)
    if j + 1 >= ctx.n_raw:
        return 0.0
    return float(min(1.0, max(0.0, _layer_pos(L, K, ctx) - j)))


def _raw_image(L: Layer, K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]) -> np.ndarray:
    """The RAW image a RAW layer shows at MAIN frame K: frame j, or -- Frame Mix -- (1 - f) j + f (j + 1); a frame not
    in ``raw_frames`` raises KeyError(frame) (callers fetch it and retry)."""
    j = layer_raw_frame(L, K, ctx)
    if j not in raw_frames:
        raise KeyError(j)
    f = layer_mix(L, K, ctx)
    if f <= 1e-6:
        return raw_frames[j]
    if j + 1 not in raw_frames:
        raise KeyError(j + 1)
    a, b = raw_frames[j], raw_frames[j + 1]
    if a.shape != b.shape:
        return a
    return np.clip(np.rint(a.astype(np.float32) * (1.0 - f) + b.astype(np.float32) * f), 0, 255).astype(np.uint8)


def _clip_hides_roi(L: Layer, ctx: RenderContext) -> bool:
    """A MAIN-level layer's clip rectangle contains the whole Video Box ROI (it hides the stack below)."""
    if L.clip is None:
        return True
    x, y, w, h, _ = L.clip
    x0, y0, rw, rh = ctx.roi
    return x <= x0 + 1e-6 and y <= y0 + 1e-6 and x + w >= x0 + rw - 1e-6 and y + h >= y0 + rh - 1e-6


def frame_sources(K: int, ctx: RenderContext) -> list[tuple[Layer, int, float]]:
    """[(layer, RAW frame, weight)] of the visible RAW layers at MAIN frame K, top first; weight = the
    layer's contribution after the stack above it (solids included), like export_ae.simulate_ae. A
    layer clipped to its own box (D1) that does not contain the Video Box does not reduce the weights below."""
    out: list[tuple[Layer, int, float]] = []
    remaining = 1.0
    for L in ctx.layers:
        if not L.active(K):
            continue
        op = L.op(K)
        if L.kind == "raw" and op > 0:
            out.append((L, layer_raw_frame(L, K, ctx), remaining * op))
        if L.clip is not None and not _clip_hides_roi(L, ctx):
            continue
        remaining *= 1.0 - op
    return out


def _layer_sim(L: Layer, K: int, ctx: RenderContext) -> Sim:
    if L.keys:
        return interpolate_keys(L.keys, float(K), ctx.raw_size[0], ctx.raw_size[1])
    return L.sim if L.sim is not None else Sim()


def _layer_roi(L: Layer, ctx: RenderContext) -> tuple[int, int, int, int]:
    """Area a layer is rendered over: the Video Box ROI, or the whole MAIN frame for a MAIN-level layer."""
    return (0, 0, ctx.size[0], ctx.size[1]) if L.main else ctx.roi


def _roi_matrix(sim: Sim, flip: bool, ctx: RenderContext, roi: tuple[int, int, int, int] | None = None) -> np.ndarray:
    m = to_cv_matrix(sim, flip, ctx.raw_size[0])
    x0, y0 = (roi or ctx.roi)[0], (roi or ctx.roi)[1]
    return (translate3(-x0, -y0) @ h3(m))[:2, :]


def _coverage(M: np.ndarray, ctx: RenderContext, roi: tuple[int, int, int, int] | None = None) -> np.ndarray | float:
    """Bilinear alpha of the warped RAW frame over the ROI: 1.0 (scalar) when the RAW frame covers the
    whole ROI (checked on the ROI corners mapped back into RAW), else warped ones (cached per matrix)."""
    import cv2
    rw, rh = (roi or ctx.roi)[2], (roi or ctx.roi)[3]
    W, H = ctx.raw_size
    inv = np.linalg.inv(h3(M))
    corners = np.array([[0, 0, 1], [rw - 1, 0, 1], [0, rh - 1, 1], [rw - 1, rh - 1, 1]], np.float64)
    src = corners @ inv.T
    if np.all(src[:, 0] >= 0) and np.all(src[:, 0] <= W - 1) and np.all(src[:, 1] >= 0) and np.all(src[:, 1] <= H - 1):
        return 1.0
    key = ("cov", rw, rh, tuple(np.round(M, 9).ravel()))
    cache = ctx.cache.setdefault("coverage", {})
    if key in cache:
        return cache[key]
    ones = ctx.cache.get("ones")
    if ones is None or ones.shape != (H, W):
        ones = np.ones((H, W), np.float32)
        ctx.cache["ones"] = ones
    cov = cv2.warpAffine(ones, M, (rw, rh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if len(cache) > 8:
        cache.pop(next(iter(cache)))
    cache[key] = cov
    return cov


def _placeholder_image(L: Layer, ctx: RenderContext) -> np.ndarray:
    """The placeholder solid over the layer's area (Video Box ROI, or MAIN for a MAIN-level layer) with its
    label drawn inside the visible box (cached per layer)."""
    import cv2
    key = ("ph", L.idx)
    img = ctx.cache.get(key)
    if img is not None:
        return img
    rw, rh = _layer_roi(L, ctx)[2], _layer_roi(L, ctx)[3]
    img = np.empty((rh, rw, 3), np.uint8)
    img[:] = np.array([round(c) for c in L.color], np.uint8)
    # draw inside the visible (box) area
    vis = _main_clip(L, ctx) if (L.main or L.clip is not None) else ctx.mask
    if vis is not None:
        ys, xs = np.nonzero(vis >= 0.999)
        vx0, vx1 = (int(xs.min()), int(xs.max())) if xs.size else (0, rw - 1)
        vy0, vy1 = (int(ys.min()), int(ys.max())) if ys.size else (0, rh - 1)
    else:
        vx0, vx1, vy0, vy1 = 0, rw - 1, 0, rh - 1
    lines = (["UNCERTAIN"] if L.seg_type == "uncertain" else ["NOT IN RAW"]) + _wrap(_ascii(L.label), 28)
    avail_w = max(8, int(0.85 * (vx1 - vx0)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 1.0
    widths = [cv2.getTextSize(t, font, 1.0, 2)[0][0] for t in lines]
    if max(widths) > 0:
        scale = min(2.0, avail_w / max(widths))
    th = max(1, int(round(2 * scale)))
    lh = int(cv2.getTextSize("Ag", font, scale, th)[0][1] * 1.9) + 2
    y = (vy0 + vy1) // 2 - lh * (len(lines) - 1) // 2
    for t in lines:
        tw = cv2.getTextSize(t, font, scale, th)[0][0]
        x = (vx0 + vx1) // 2 - tw // 2
        cv2.putText(img, t, (x, y), font, scale, (0, 0, 0), th + 2, cv2.LINE_AA)
        cv2.putText(img, t, (x, y), font, scale, (255, 255, 255), th, cv2.LINE_AA)
        y += lh
    ctx.cache[key] = img
    return img


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:4]


def _stack(K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]) -> tuple[np.ndarray, np.ndarray | float]:
    """Premultiplied (C [h, w, 3] float32, A [h, w] float32 or a scalar) of the segment stack over the ROI,
    composited bottom-up: C = op*Cl + (1 - op*Al) C, A = op*Al + (1 - op*Al) A."""
    import cv2
    rw, rh = ctx.roi[2], ctx.roi[3]
    C = np.zeros((rh, rw, 3), np.float32)
    A: np.ndarray | float = 0.0
    for L in reversed([L for L in ctx.layers if L.active(K) and not L.main]):     # bottom -> top
        op = L.op(K)
        if op <= 0.0:
            continue
        if L.kind == "raw":
            j = layer_raw_frame(L, K, ctx)
            img = _raw_image(L, K, ctx, raw_frames)
            if img.shape[1] != ctx.raw_size[0] or img.shape[0] != ctx.raw_size[1]:
                raise ValueError(f"RAW frame {j} is {img.shape[1]}x{img.shape[0]}, expected "
                                 f"{ctx.raw_size[0]}x{ctx.raw_size[1]}")
            M = _roi_matrix(_layer_sim(L, K, ctx), L.flip, ctx)
            Cl = cv2.warpAffine(img, M, (rw, rh), flags=ctx.interp, borderMode=cv2.BORDER_CONSTANT,
                                borderValue=(0, 0, 0)).astype(np.float32)
            Al: np.ndarray | float = _coverage(M, ctx)
        elif L.label:
            Cl, Al = _placeholder_image(L, ctx).astype(np.float32), 1.0
        else:
            Cl, Al = np.array(L.color, np.float32), 1.0
        if Cl.ndim == 1:
            Cl = np.array(np.broadcast_to(Cl, (rh, rw, 3)), np.float32)
        clip = _main_clip(L, ctx)                              # own-box layer without a Video Box (ROI = MAIN)
        if clip is not None:
            Cl = Cl * clip[..., None]
            Al = Al * clip
        if isinstance(Al, float) and Al * op >= 1.0:           # opaque layer: replaces everything below
            C, A = Cl, 1.0
            continue
        if isinstance(Al, float):                              # full coverage, partial opacity: one pass
            C = cv2.addWeighted(C, 1.0 - op, Cl, op, 0.0, dtype=cv2.CV_32F)
            A = A * (1.0 - op) + op
            continue
        a = Al * np.float32(op)
        keep = 1.0 - a
        C = C * keep[..., None] + Cl * np.float32(op)
        A = A * keep + a
    return C, A


def _blur_background(ctx: RenderContext, C: np.ndarray, A: np.ndarray | float) -> np.ndarray:
    """Float MAIN background of a 'blur' layout: the Video Box pre-comp (premultiplied C, A) blurred in
    layer space (Gaussian, repeat edge pixels, sigma = blurriness / 3 as export_ae assumes), cover-scaled
    about the frame centre, over the solid colour -- the AE plan's bg_blur layer."""
    import cv2
    W, H = ctx.size
    base = np.empty((H, W, 3), np.float32)
    base[:] = np.array(ctx.bg_color, np.float32)
    rw, rh = ctx.roi[2], ctx.roi[3]
    Aa = np.full((rh, rw), float(A), np.float32) if not isinstance(A, np.ndarray) else A
    sig = max(0.0, ctx.blur_sigma)
    if sig > 0:
        Cb = cv2.GaussianBlur(C, (0, 0), sig, borderType=cv2.BORDER_REPLICATE)
        Ab = cv2.GaussianBlur(Aa, (0, 0), sig, borderType=cv2.BORDER_REPLICATE)
    else:
        Cb, Ab = C, Aa
    cover = max(W / rw, H / rh)
    Mc = np.array([[cover, 0.0, W / 2.0 - cover * rw / 2.0], [0.0, cover, H / 2.0 - cover * rh / 2.0], [0, 0, 1]])
    Mcv = (CORNER_TO_CV @ Mc @ CV_TO_CORNER)[:2, :]
    Cw = cv2.warpAffine(Cb, Mcv, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    Aw = cv2.warpAffine(Ab, Mcv, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return Cw + (1.0 - Aw)[..., None] * base


def _roi_clip(ctx: RenderContext) -> tuple[int, int, int, int, tuple[slice, slice]]:
    """ROI clipped to the MAIN frame: (cx0, cy0, cx1, cy1, slices into ROI arrays)."""
    x0, y0, rw, rh = ctx.roi
    W, H = ctx.size
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x0 + rw), min(H, y0 + rh)
    return cx0, cy0, cx1, cy1, (slice(cy0 - y0, cy1 - y0), slice(cx0 - x0, cx1 - x0))


def _bg_u8(ctx: RenderContext) -> np.ndarray:
    """Cached uint8 MAIN frame of the (solid / black) background."""
    bg = ctx.cache.get("bg_u8")
    if bg is None:
        W, H = ctx.size
        bg = np.empty((H, W, 3), np.uint8)
        bg[:] = np.array([int(round(c)) for c in (ctx.bg_color if ctx.bg_type != "none" else (0, 0, 0))], np.uint8)
        ctx.cache["bg_u8"] = bg
    return bg


def _mask_edges(ctx: RenderContext) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ys, xs, m) of the clipped-ROI pixels whose box coverage is < 1 (edges and outside), cached."""
    e = ctx.cache.get("mask_edges")
    if e is None:
        m = ctx.mask[_roi_clip(ctx)[4]]
        ys, xs = np.nonzero(m < 1.0)
        e = (ys, xs, m[ys, xs].astype(np.float32))
        ctx.cache["mask_edges"] = e
    return e


def _opaque_top(K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]) -> np.ndarray | None:
    """uint8 ROI image of the top layer when it hides everything below (opacity 1, full coverage), else None."""
    import cv2
    for L in ctx.layers:                                   # top first
        if not L.active(K) or L.main:
            continue
        op = L.op(K)
        if op <= 0.0:
            continue
        if op < 1.0 or L.clip is not None:
            return None
        rw, rh = ctx.roi[2], ctx.roi[3]
        if L.kind == "raw":
            j = layer_raw_frame(L, K, ctx)
            img = _raw_image(L, K, ctx, raw_frames)
            if img.shape[1] != ctx.raw_size[0] or img.shape[0] != ctx.raw_size[1]:
                return None
            M = _roi_matrix(_layer_sim(L, K, ctx), L.flip, ctx)
            if not isinstance(_coverage(M, ctx), float):
                return None
            return cv2.warpAffine(img, M, (rw, rh), flags=ctx.interp, borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(0, 0, 0))
        if L.label:
            return _placeholder_image(L, ctx)
        key = ("solid", L.idx)
        if key not in ctx.cache:
            im = np.empty((rh, rw, 3), np.uint8)
            im[:] = np.array([int(round(c)) for c in L.color], np.uint8)
            ctx.cache[key] = im
        return ctx.cache[key]
    return None


def _main_clip(L: Layer, ctx: RenderContext) -> np.ndarray | None:
    """Anti-aliased coverage [H, W] of a MAIN-level layer's clip box (cached), None = no mask."""
    if L.clip is None:
        return None
    key = ("clip", L.clip)
    m = ctx.cache.get(key)
    if m is None:
        W, H = ctx.size
        x, y, w, h, rad = L.clip
        m = np.zeros((H, W), np.float32)
        # super-sampled only over the box's pixel bounds (clipped to the frame): 0 elsewhere
        bx0, by0 = max(0, math.floor(x)), max(0, math.floor(y))
        bx1, by1 = min(W, math.ceil(x + w)), min(H, math.ceil(y + h))
        if bx1 > bx0 and by1 > by0:
            m[by0:by1, bx0:bx1] = rounded_box_coverage(bx1 - bx0, by1 - by0, x - bx0, y - by0, w, h, rad)
        ctx.cache[key] = m
    return m


def _main_layer(L: Layer, K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]
                ) -> tuple[np.ndarray, np.ndarray | float]:
    """Premultiplied (C [H, W, 3] float32, A [H, W] or scalar) of one MAIN-level layer over the MAIN frame:
    warped RAW (bilinear coverage) / solid / labelled placeholder, times its clip coverage."""
    import cv2
    W, H = ctx.size
    full = (0, 0, W, H)
    if L.kind == "raw":
        j = layer_raw_frame(L, K, ctx)
        img = _raw_image(L, K, ctx, raw_frames)
        if img.shape[1] != ctx.raw_size[0] or img.shape[0] != ctx.raw_size[1]:
            raise ValueError(f"RAW frame {j} is {img.shape[1]}x{img.shape[0]}, expected "
                             f"{ctx.raw_size[0]}x{ctx.raw_size[1]}")
        M = _roi_matrix(_layer_sim(L, K, ctx), L.flip, ctx, full)
        Cl = cv2.warpAffine(img, M, (W, H), flags=ctx.interp, borderMode=cv2.BORDER_CONSTANT,
                            borderValue=(0, 0, 0)).astype(np.float32)
        Al: np.ndarray | float = _coverage(M, ctx, full)
    elif L.label:
        Cl, Al = _placeholder_image(L, ctx).astype(np.float32), 1.0
    else:
        Cl, Al = np.array(np.broadcast_to(np.array(L.color, np.float32), (H, W, 3))), 1.0
    clip = _main_clip(L, ctx)
    if clip is not None:
        Cl = Cl * clip[..., None]
        Al = Al * clip
    return Cl, Al


def _main_opaque_top(K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray], L: Layer) -> np.ndarray | None:
    """uint8 MAIN frame when the top MAIN-level layer hides everything (opacity 1, no clip, full
    coverage), else None."""
    import cv2
    if L.op(K) < 1.0 or L.clip is not None:
        return None
    W, H = ctx.size
    full = (0, 0, W, H)
    if L.kind == "raw":
        j = layer_raw_frame(L, K, ctx)
        img = _raw_image(L, K, ctx, raw_frames)
        if img.shape[1] != ctx.raw_size[0] or img.shape[0] != ctx.raw_size[1]:
            return None
        M = _roi_matrix(_layer_sim(L, K, ctx), L.flip, ctx, full)
        if not isinstance(_coverage(M, ctx, full), float):
            return None
        return cv2.warpAffine(img, M, (W, H), flags=ctx.interp, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    if L.label:
        return _placeholder_image(L, ctx).copy()
    im = np.empty((H, W, 3), np.uint8)
    im[:] = np.array([int(round(c)) for c in L.color], np.uint8)
    return im


def _composite_main(K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray], base: np.ndarray,
                    mains: list[Layer]) -> np.ndarray:
    """MAIN-level layers (top first) composited bottom-up over the uint8 Video Box composite `base`:
    C = op Cl + (1 - op Al) C (premultiplied)."""
    import cv2
    C = base.astype(np.float32)
    for L in reversed(mains):                                       # bottom -> top
        op = L.op(K)
        Cl, Al = _main_layer(L, K, ctx, raw_frames)
        if isinstance(Al, float):
            if Al * op >= 1.0:
                C = Cl
            else:
                C = cv2.addWeighted(C, 1.0 - Al * op, Cl, op, 0.0, dtype=cv2.CV_32F)
            continue
        a = Al * np.float32(op)
        C = C * (1.0 - a)[..., None] + Cl * np.float32(op)
    return cv2.convertScaleAbs(C)


def render_frame(k: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]) -> np.ndarray:
    """MAIN frame k (BGR uint8 [H, W, 3]). raw_frames = {RAW frame index: full-res BGR frame}; a missing
    frame raises KeyError(j) (callers fetch it and retry).

    out = C * m + (1 - A * m) * background over the ROI (m = box coverage, 1 without a box). Fast path
    (the usual frame): the top layer is opaque and covers the ROI -> uint8 warp straight into a copy of the
    cached background, blending only the mask-edge pixels (identical result). MAIN-level layers (D1,
    Segment.box set) are then composited over the whole frame, each clipped by its own box; an opaque,
    unclipped, fully covering top MAIN-level layer (a fullscreen shot) is warped straight into the frame."""
    K = int(k)
    mains = [L for L in ctx.layers if L.main and L.active(K) and L.op(K) > 0.0]
    if mains:
        top = _main_opaque_top(K, ctx, raw_frames, mains[0])
        if top is not None:
            return top
        return _composite_main(K, ctx, raw_frames, _render_box_level(K, ctx, raw_frames), mains)
    return _render_box_level(K, ctx, raw_frames)


def _render_box_level(K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]) -> np.ndarray:
    """The Video Box composite (segment stack in the box, masked, over the background) as a MAIN frame."""
    cx0, cy0, cx1, cy1, sl = _roi_clip(ctx)
    if ctx.bg_type != "blur":
        top = _opaque_top(K, ctx, raw_frames)
        if top is not None:
            out = _bg_u8(ctx).copy()
            if cx1 > cx0 and cy1 > cy0:
                src = top[sl]
                dst = out[cy0:cy1, cx0:cx1]
                dst[...] = src
                if ctx.mask is not None:
                    ys, xs, mv = _mask_edges(ctx)
                    bgc = np.array(ctx.bg_color if ctx.bg_type != "none" else (0, 0, 0), np.float32)
                    blend = src[ys, xs].astype(np.float32) * mv[:, None] + bgc * (1.0 - mv[:, None])
                    dst[ys, xs] = np.clip(np.rint(blend), 0, 255).astype(np.uint8)
            return out
    return _render_general(K, ctx, raw_frames)


def _render_general(K: int, ctx: RenderContext, raw_frames: dict[int, np.ndarray]) -> np.ndarray:
    """Float premultiplied compositing of the whole stack (crossfades, dips, partial coverage, blur)."""
    import cv2
    cx0, cy0, cx1, cy1, sl = _roi_clip(ctx)
    C, A = _stack(K, ctx, raw_frames)
    if ctx.bg_type == "blur":
        full = _blur_background(ctx, C, A)
        below = full[cy0:cy1, cx0:cx1]
    else:
        full = None
        below = np.array(ctx.bg_color if ctx.bg_type != "none" else (0, 0, 0), np.float32)
    if cx1 > cx0 and cy1 > cy0 and full is None and not isinstance(A, np.ndarray) and float(A) >= 1.0:
        # opaque stack over a constant background: only the mask-edge pixels need blending
        out = _bg_u8(ctx).copy()
        Cc = C[sl]
        dst = out[cy0:cy1, cx0:cx1]
        dst[...] = cv2.convertScaleAbs(Cc)
        if ctx.mask is not None:
            ys, xs, mv = _mask_edges(ctx)
            blend = Cc[ys, xs] * mv[:, None] + below * (1.0 - mv[:, None])
            dst[ys, xs] = np.clip(np.rint(blend), 0, 255).astype(np.uint8)
        return out
    if cx1 > cx0 and cy1 > cy0:
        Cc = C[sl]
        Ac = A[sl] if isinstance(A, np.ndarray) else np.float32(A)
        if ctx.mask is not None:
            m = ctx.mask[sl]
            res = Cc * m[..., None] + below * (1.0 - Ac * m)[..., None]
        elif isinstance(Ac, np.ndarray):
            res = Cc + below * (1.0 - Ac)[..., None]
        else:
            res = Cc + below * (1.0 - Ac)
    if full is not None:
        if cx1 > cx0 and cy1 > cy0:
            full[cy0:cy1, cx0:cx1] = res
        return cv2.convertScaleAbs(full)
    out = _bg_u8(ctx).copy()
    if cx1 > cx0 and cy1 > cy0:
        out[cy0:cy1, cx0:cx1] = cv2.convertScaleAbs(res)
    return out


# ---------------------------------------------------------------------------------------------
# Sequential RAW decoding for whole renders
# ---------------------------------------------------------------------------------------------

class _LayerStream:
    """RAW frames of ONE layer in timeline order. Non-decreasing frame sequences (normal, sped-up, slowed,
    frozen) use one seek + a forward PTS-indexed decode; other sequences (reverse, odd remaps) decode small
    look-ahead chunks. get() must be called once per entry of `frames`, in order."""

    CHUNK = 12

    def __init__(self, path: str, raw_fps: Fraction, frames: list[int]):
        from .media import VideoReader
        self.reader = VideoReader(path, fps=raw_fps)
        self.frames = frames
        self.pos = 0
        self.monotonic = all(b >= a for a, b in zip(frames[:-1], frames[1:]))
        self.gen: Iterator[tuple[int, np.ndarray]] | None = None
        self.cur: tuple[int, np.ndarray] | None = None
        self.pending: tuple[int, np.ndarray] | None = None
        self.chunk: dict[int, np.ndarray] = {}
        if self.monotonic and frames:
            self.gen = self.reader.frames(frames[0], frames[-1] + 1)

    def _next(self) -> tuple[int, np.ndarray] | None:
        if self.pending is not None:
            nxt, self.pending = self.pending, None
            return nxt
        return next(self.gen, None) if self.gen is not None else None

    def get(self, j: int) -> np.ndarray:
        self.pos += 1
        if self.monotonic:
            while self.cur is None or self.cur[0] < j:
                nxt = self._next()
                if nxt is None:
                    break
                if nxt[0] > j and self.cur is not None:
                    self.pending = nxt
                    break
                self.cur = nxt
            if self.cur is None:
                raise IndexError(f"RAW frame {j} not decodable from {self.reader.path}")
            if self.cur[0] != j:
                log.warning("render_preview: RAW frame %d missing in the decode; showing %d", j, self.cur[0])
            return self.cur[1]
        if j not in self.chunk:
            upcoming = self.frames[self.pos - 1:self.pos - 1 + self.CHUNK] or [j]
            lo, hi = min(upcoming + [j]), max(upcoming + [j])
            if hi - lo > 4 * self.CHUNK:
                lo = hi = j
            wanted = set(upcoming) | {j}
            self.chunk = {i: img for i, img in self.reader.frames(lo, hi + 1) if i in wanted}
            if j not in self.chunk:
                raise IndexError(f"RAW frame {j} not decodable from {self.reader.path}")
        return self.chunk[j]

    def close(self) -> None:
        if self.gen is not None:
            self.gen.close()
        self.reader.close()


def iter_render(ctx: RenderContext, raw_path: str | os.PathLike | None = None, start: int = 0,
                stop: int | None = None) -> Iterator[tuple[int, np.ndarray, list[tuple[int, int, float]]]]:
    """Yield (K, BGR frame, [(segment id, RAW frame, weight)]) for MAIN frames [start, stop), decoding the
    RAW sequentially per layer (one reader per concurrently active layer, closed when the layer ends)."""
    path = str(raw_path or ctx.raw_path or "")
    if not path:
        raise ValueError("render_preview: no RAW path (pass raw_path or put file_abs in cutlist.raw)")
    stop = ctx.n_frames if stop is None else min(int(stop), ctx.n_frames)
    need: dict = {}
    for L in ctx.layers:
        if L.kind != "raw":
            continue
        js = [layer_raw_frame(L, K, ctx) for K in range(max(L.k_in, start), min(L.k_out, stop)) if L.op(K) > 0]
        if js:
            need[L.idx] = js
            if L.mix:            # Frame Mix: RAW j + 1 as a second monotone stream
                need[(L.idx, 1)] = [min(j + 1, ctx.n_raw - 1) for j in js]
    streams: dict = {}
    try:
        for K in range(int(start), stop):
            for li in [li for li in streams if not ctx.layers[li if isinstance(li, int) else li[0]].active(K)]:
                streams.pop(li).close()
            frames: dict[int, np.ndarray] = {}
            for L in ctx.layers:
                if L.kind != "raw" or not L.active(K) or L.op(K) <= 0 or L.idx not in need:
                    continue
                if L.idx not in streams:
                    streams[L.idx] = _LayerStream(path, ctx.raw_fps, need[L.idx])
                j = layer_raw_frame(L, K, ctx)
                img = streams[L.idx].get(j)            # every stream advances on every frame it serves
                frames.setdefault(j, img)
                if (L.idx, 1) in need:
                    if (L.idx, 1) not in streams:
                        streams[(L.idx, 1)] = _LayerStream(path, ctx.raw_fps, need[(L.idx, 1)])
                    j1 = min(j + 1, ctx.n_raw - 1)
                    frames.setdefault(j1, streams[(L.idx, 1)].get(j1))
            img = render_frame(K, ctx, frames)
            info = [(L.seg_id, j, float(w)) for L, j, w in frame_sources(K, ctx)]
            yield K, img, info
    finally:
        for s in streams.values():
            s.close()


# ---------------------------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------------------------

def _frac_shift(x: np.ndarray, f: float, half: int = 32, beta: float = 8.0) -> np.ndarray:
    """x'(u) = x(u + f) for 0 <= f < 1 (band-limited Kaiser-windowed-sinc fractional delay, 2*half taps,
    zero outside x); same length as x; axis 0."""
    if abs(f) < 1e-9:
        return x
    from scipy.signal import oaconvolve
    t = np.arange(-half + 1, half + 1, dtype=np.float64)
    d = t - f
    w = np.i0(beta * np.sqrt(np.clip(1.0 - (d / half) ** 2, 0.0, None))) / np.i0(beta)
    h = np.sinc(d) * w
    h /= h.sum()
    g = h[::-1]
    if x.ndim == 2:
        g = g[:, None]
    c = oaconvolve(x, g, mode="full", axes=0)
    return c[half:half + x.shape[0]].astype(np.float32)


def _rational(v: float, n_out: int, max_den: int = 10000) -> Fraction:
    """P/Q ~ v with a drift below 0.05 samples over n_out output samples (or the best within max_den)."""
    for den in (10, 100, 1000):
        fr = Fraction(v).limit_denominator(den)
        if abs(float(fr) - v) * max(1, n_out) <= 0.05:
            return fr
    return Fraction(v).limit_denominator(max_den)


def sample_positions(x: np.ndarray, p0: float, v: float, n: int) -> np.ndarray:
    """Tape-style resampling: y[i] = x(p0 + v*i), i in [0, n) -- x band-limited, zero outside [0, len(x)).

    v = 0 -> silence (a frozen time-remapped layer plays no audio); v < 0 plays the RAW backwards.
    Exact positions: the input is shifted by the fractional part of its start with a windowed-sinc
    fractional delay, then resampled with ``scipy.signal.resample_poly(up=Q, down=P)`` where P/Q is a
    rational approximation of |v| whose drift stays below 0.05 samples over the chunk (1.1 -> 11/10 exactly).
    """
    from scipy.signal import resample_poly
    shape = (n,) + x.shape[1:]
    if n <= 0:
        return np.zeros(shape, np.float32)
    if v == 0 or x.shape[0] == 0:
        return np.zeros(shape, np.float32)
    if v < 0:
        xr = x[::-1]
        return sample_positions(xr, (x.shape[0] - 1) - p0, -v, n)
    fr = _rational(v, n)
    P, Q = fr.numerator, fr.denominator
    pad = P * max(1, math.ceil(64 / P))
    base = math.floor(p0)
    f = p0 - base
    lo = base - pad
    span = int(math.ceil(float(fr) * (n - 1))) + 2 * pad + 2
    hi = lo + span
    chunk = np.zeros((span,) + x.shape[1:], np.float32)
    a, b = max(lo, 0), min(hi, x.shape[0])
    if b > a:
        chunk[a - lo:b - lo] = x[a:b]
    chunk = _frac_shift(chunk, f)                    # chunk(u) = x(lo + u + f)
    if P == Q:
        y = chunk[pad:pad + n]
    else:
        yr = resample_poly(chunk, Q, P, axis=0)      # yr[m] = chunk(m P / Q)
        m0 = pad * Q // P                            # chunk(pad + i P/Q) = x(p0 + i v)
        y = yr[m0:m0 + n]
    if y.shape[0] < n:
        y = np.concatenate([y, np.zeros((n - y.shape[0],) + x.shape[1:], np.float32)])
    return np.ascontiguousarray(y, dtype=np.float32)


def _sample_at(K: int | Fraction, sr: int, fps: Fraction) -> int:
    """Output sample index of MAIN frame boundary K: floor(K sr / fps + 1/2) (exact)."""
    return math.floor(Fraction(K) * sr / Fraction(fps) + Fraction(1, 2))


def audio_segment(seg: Segment) -> Segment | None:
    """The segment whose RAW map plays this segment's AUDIO: the segment itself (a RAW segment), or -- when its
    audio follows a verified audio line (FX-14, ``seg.audio['line']``: a video-only retime, an uncertain segment
    or a placeholder over continuous audio) -- a stretch view of that line over the same range (raw_in = the
    line's picture-synced RAW time at comp_in, its speed); None when it plays no RAW audio (placeholders, dips)."""
    line = (seg.audio or {}).get("line")
    if line:
        import dataclasses
        return dataclasses.replace(seg, type="raw", raw_in_seconds=float(line["raw_in_seconds"]),
                                   speed=float(line["speed"]), time_remap_keys=[], time_mode="stretch")
    return seg if seg.type == "raw" else None


def _audio_frames(seg: Segment, main_fps: Fraction, comp_fps: Fraction, N: int, shift_k: int) -> tuple[int, int]:
    """MAIN frames [a0, a1) of a segment's audio: [comp_in + in_offset, comp_out + out_offset) (J/L, DESIGN
    §3) moved by the export's switch shift (D9 competitor sync), clipped to [0, N]."""
    au = seg.audio or {}
    a0 = max(0, _to_main(int(seg.comp_in) + int(au.get("in_offset_frames") or 0), main_fps, comp_fps) + shift_k)
    a1 = min(N, _to_main(int(seg.comp_out) + int(au.get("out_offset_frames") or 0), main_fps, comp_fps) + shift_k)
    return a0, a1


def build_audio(cutlist: Cutlist, raw_audio: np.ndarray, sr: int, *, fps: Fraction | None = None,
                n_frames: int | None = None, audio_sync: str | None = None, av_offset_lag_s: float | None = None,
                switch_baseline_s: float | None = None) -> np.ndarray:
    """Sample-accurate RAW audio rebuild of the edit (DESIGN §5 render_preview.build_audio).

    raw_audio: (N,) or (N, C) float RAW audio at `sr` (sample 0 = RAW video t = 0). Returns float32 of
    exactly floor(n_frames * sr / fps + 1/2) samples (n_frames MAIN frames at `fps`; defaults: the
    competitor frames at the competitor fps), same channel layout.

    Mapping (the AE layer model): segment audio range = MAIN frames [comp_in + in_offset, comp_out +
    out_offset) (J/L cuts, DESIGN §3) = output samples [floor(K_a sr/fps + 1/2), floor(K_b sr/fps + 1/2)).
    Output sample n (time n/sr) plays RAW time tau(n) = raw_in' + v (n/sr - K_in/fps), i.e. RAW sample
    position tau*sr, via :func:`sample_positions` (tape-style: speed and pitch change together, like an AE
    stretched layer; v = 1 is a sub-sample fractional shift). A segment whose audio follows an audio line
    (FX-14, ``audio_segment``) plays that line instead -- also a placeholder. time_remap_keys are applied piecewise
    (frozen pieces are silent). Crossfades: gains from export_ae's Audio Levels keys (20 log10 of
    1 - alpha_B for A and alpha_B for B at each overlap frame, B back to 0 dB at O + D), interpolated
    linearly in dB between the key times like AE; at every overlap frame boundary the gains are exactly
    the linear 1 - alpha_B / alpha_B. Overlapping audio ranges sum. NOT-IN-RAW (without an audio line) / dips /
    flashes: silent.

    Audio sync (DESIGN §7 D9, mirrors export_ae's audio twins; ``export_ae.audio_sync_params``): 'raw'
    (cutlist.settings.audio_sync default) keeps RAW lip-sync; 'competitor' plays every segment's audio with
    the competitor's measured offset -- content tau(n) + v * g (g = cutlist.audio.av_offset.lag_ms, xcorr
    convention: g < 0 = the competitor's audio is late) over its range moved by round(switch baseline x
    fps) MAIN frames. Explicit ``av_offset_lag_s`` / ``switch_baseline_s`` override (and imply) the shift.
    """
    from .export_ae import audio_sync_params
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    main_fps = Fraction(fps) if fps is not None else comp_fps
    N = int(n_frames) if n_frames is not None else _to_main(int(cutlist.competitor["frames"]), main_fps, comp_fps)
    g_s, shift_k = audio_sync_params(cutlist, main_fps, audio_sync, av_offset_lag_s, switch_baseline_s)
    x = np.asarray(raw_audio, dtype=np.float32)
    total = _sample_at(N, sr, main_fps)
    out = np.zeros((total,) + x.shape[1:], np.float32)
    if x.shape[0] == 0 or total == 0:
        return out
    mf = float(main_fps)
    segs = sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.comp_out), int(s.id)))
    kmap = {}
    for s in segs:
        a, b = _to_main(int(s.comp_in), main_fps, comp_fps), _to_main(int(s.comp_out), main_fps, comp_fps)
        if b > a:
            kmap[int(s.id)] = (a, b)
    _, audio_db = _transition_keys(segs, kmap, main_fps, comp_fps)
    for seg0 in segs:
        sid = int(seg0.id)
        seg = audio_segment(seg0)               # its own map, or the audio line it follows (FX-14)
        if seg is None or sid not in kmap:
            continue
        k_in, _k_out = kmap[sid]
        v = float(seg.speed)
        raw_in = _seg_raw_in(seg, raw_fps)
        a0, a1 = _audio_frames(seg, main_fps, comp_fps, N, shift_k)
        n0, n1 = _sample_at(a0, sr, main_fps), _sample_at(a1, sr, main_fps)
        if n1 <= n0:
            continue
        # pieces [(n_start, n_end, RAW seconds at n_start, speed)]
        pieces: list[tuple[int, int, float, float]] = []
        if seg.time_remap_keys:
            # competitor sync: the remap curve is played g later in comp time (key times - g)
            keys = sorted(((_to_main_f(float(d["comp_frame"]), main_fps, comp_fps) - g_s * mf, float(d["raw_seconds"]))
                           for d in seg.time_remap_keys), key=lambda t: t[0])
            bounds = [n0] + [min(max(math.ceil(kf * sr / mf - 1e-9), n0), n1) for kf, _ in keys] + [n1]
            bounds = sorted(set(bounds))
            for sa, sb in zip(bounds[:-1], bounds[1:]):
                if sb <= sa:
                    continue
                ka, kb = sa * mf / sr, sb * mf / sr
                ta, tb = _interp_keys(keys, ka), _interp_keys(keys, kb)
                vv = (tb - ta) / ((sb - sa) / sr)
                pieces.append((sa, sb, ta, vv))
        else:
            if raw_in is None:
                continue
            err = float(Fraction(k_in) / main_fps - Fraction(int(seg.comp_in)) / comp_fps)
            raw_in_m = raw_in + v * err if err else raw_in
            tau0 = raw_in_m + v * (n0 / sr - k_in / mf)
            if g_s:
                tau0 += v * g_s
            pieces.append((n0, n1, tau0, v))
        for sa, sb, tau, vv in pieces:
            y = sample_positions(x, tau * sr, vv, sb - sa)
            keys_db = audio_db.get(sid)
            if keys_db:
                kt = np.array(sorted(keys_db), np.float64) + shift_k
                kv = np.array([keys_db[k] for k in sorted(keys_db)], np.float64)
                frames_pos = np.arange(sa, sb, dtype=np.float64) * mf / sr
                g = np.power(10.0, np.interp(frames_pos, kt, kv) / 20.0).astype(np.float32)
                y = y * (g[:, None] if y.ndim == 2 else g)
            out[sa:sb] += y
    return out


def _write_wav(path: Path, y: np.ndarray, sr: int) -> None:
    import soundfile as sf
    sf.write(str(path), np.clip(y, -1.0, 1.0), int(sr), subtype="FLOAT")


def _mux(video: Path, audio_src: Path | None, out: Path, audio_codec_args: Sequence[str] = ("-c:a", "aac", "-b:a", "256k"),
         audio_map: str = "1:a:0") -> None:
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(video)]
    if audio_src is not None:
        cmd += ["-i", str(audio_src), "-map", "0:v:0", "-map", audio_map, "-c:v", "copy", *audio_codec_args]
    else:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed for {out}: {res.stderr[-3000:]}")


def _seg_raw_time_span(seg: Segment, a0: int, a1: int, main_fps: Fraction, comp_fps: Fraction,
                       raw_fps: Fraction, g_s: float = 0.0) -> tuple[float, float] | None:
    """RAW seconds spanned by a RAW segment's audio over MAIN frames [a0, a1) (same map as build_audio,
    g_s = its competitor-sync content offset)."""
    if seg.time_remap_keys:
        vals = [float(d["raw_seconds"]) for d in seg.time_remap_keys]
        return min(vals), max(vals)
    raw_in = _seg_raw_in(seg, raw_fps)
    if raw_in is None:
        return None
    k_in = _to_main(int(seg.comp_in), main_fps, comp_fps)
    err = float(Fraction(k_in) / main_fps - Fraction(int(seg.comp_in)) / comp_fps)
    base = raw_in + float(seg.speed) * (err + g_s)
    ends = [base + float(seg.speed) * (a - k_in) / float(main_fps) for a in (a0, a1)]
    return min(ends), max(ends)


def _extract_window(path: str | os.PathLike, sr: int, channels: int, s0: int, s1: int) -> np.ndarray:
    """RAW audio samples [s0, s1) at sr as float32 (n, channels); ffmpeg accurate input seek (decode and
    discard up to the exact start), zero-padded when the file ends early."""
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-accurate_seek", "-ss", f"{s0 / sr:.9f}", "-i", str(path),
           "-map", "0:a:0", "-vn", "-t", f"{(s1 - s0) / sr:.9f}", "-f", "f32le", "-acodec", "pcm_f32le",
           "-ar", str(sr), "-ac", str(channels), "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"audio window extraction failed for {path}: {res.stderr.decode(errors='replace')[-2000:]}")
    y = np.frombuffer(res.stdout, dtype=np.float32).reshape(-1, channels)[:s1 - s0]
    if y.shape[0] < s1 - s0:
        y = np.concatenate([y, np.zeros((s1 - s0 - y.shape[0], channels), np.float32)])
    return y


def load_raw_audio(cutlist: Cutlist, raw_path: str | os.PathLike, *, fps: Fraction | None = None,
                   n_frames: int | None = None, budget_bytes: int = 1 << 30, margin_s: float = 0.5
                   ) -> tuple[np.ndarray, int, Cutlist]:
    """RAW audio for build_audio with bounded memory: (audio (N, C) float32, sr, cutlist to use).

    sr = the RAW's own sample rate (no resampling). When the whole track fits in `budget_bytes` it is
    decoded at once (media.extract_audio, sample 0 = video t 0) and the cutlist is returned unchanged.
    Otherwise (hour-long RAWs) only the windows the edit plays (+ margin_s) are extracted and concatenated
    in RAW order, and a copy of the cutlist is returned whose RAW times are shifted by whole samples into
    that compact array -- build_audio then produces the identical result."""
    from .media import extract_audio
    if cutlist.raw.get("has_audio") is False:
        return np.zeros((0, 1), np.float32), 48000, cutlist
    sr = int(cutlist.raw.get("audio_sample_rate") or 0) or 48000
    ch = max(1, int(cutlist.raw.get("audio_channels") or 2))
    raw_fps, comp_fps = cutlist.raw_fps, cutlist.comp_fps
    total_s = int(cutlist.raw.get("frames") or 0) / float(raw_fps)
    if total_s * sr * ch * 4 <= budget_bytes or total_s <= 0:
        return extract_audio(raw_path, sr=sr, mono=False), sr, cutlist
    main_fps = Fraction(fps) if fps is not None else comp_fps
    N = int(n_frames) if n_frames is not None else _to_main(int(cutlist.competitor["frames"]), main_fps, comp_fps)
    from .export_ae import audio_sync_params
    g_s, shift_k = audio_sync_params(cutlist, main_fps)
    spans: list[tuple[int, int, int]] = []           # (s0, s1, segment id)
    for seg0 in cutlist.segments:
        seg = audio_segment(seg0)                    # an audio line plays its own RAW span (FX-14)
        if seg is None:
            continue
        a0, a1 = _audio_frames(seg, main_fps, comp_fps, N, shift_k)
        span = _seg_raw_time_span(seg, a0, a1, main_fps, comp_fps, raw_fps, g_s) if a1 > a0 else None
        if span is None:
            continue
        s0 = max(0, math.floor((span[0] - margin_s) * sr))
        s1 = min(math.ceil(total_s * sr), math.ceil((span[1] + margin_s) * sr))
        if s1 > s0:
            spans.append((s0, s1, int(seg.id)))
    windows: list[list[int]] = []                    # merged [s0, s1, [ids]]
    for s0, s1, sid in sorted(spans):
        if windows and s0 <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], s1)
            windows[-1][2].append(sid)
        else:
            windows.append([s0, s1, [sid]])
    parts, shift, off = [], {}, 0
    for s0, s1, ids in windows:
        parts.append(_extract_window(raw_path, sr, ch, s0, s1))
        for sid in ids:
            shift[sid] = (off - s0) / sr             # whole samples -> exact
        off += s1 - s0
    cl = copy.deepcopy(cutlist)
    for seg in cl.segments:
        d = shift.get(int(seg.id))
        if d is None:
            continue
        if seg.raw_in_seconds is not None:
            seg.raw_in_seconds = float(seg.raw_in_seconds) + d
        elif seg.raw_in_frame is not None:
            seg.raw_in_seconds = float((Fraction(int(seg.raw_in_frame)) + Fraction(1, 2)) / raw_fps) + d
        for key in seg.time_remap_keys or []:
            key["raw_seconds"] = float(key["raw_seconds"]) + d
        if (seg.audio or {}).get("line"):
            seg.audio["line"]["raw_in_seconds"] = float(seg.audio["line"]["raw_in_seconds"]) + d
    log.info("preview audio: %d RAW window(s), %.1f s of %.1f s decoded", len(windows), off / sr, total_s)
    audio = np.concatenate(parts) if parts else np.zeros((0, ch), np.float32)
    return audio, sr, cl


# ---------------------------------------------------------------------------------------------
# preview_recreation.mp4
# ---------------------------------------------------------------------------------------------

def render_preview(cutlist: Cutlist, raw_path: str | os.PathLike, out_path: str | os.PathLike, cfg: Any,
                   layout_mode: str | None = None) -> dict:
    """preview_recreation.mp4 at MAIN size / fps / layout (DESIGN §2.5): own frame-exact renderer (never
    an ffmpeg trim/concat chain), H.264 CRF <= 16 yuv420p +faststart, RAW audio rebuilt with build_audio
    and muxed as AAC. Returns {'frames', 'raw_frames': {K: [(seg, j, weight)]}, 'path', 'size', 'fps',
    'layout_mode', 'audio', 'warnings'}."""
    from .media import FFmpegWriter
    ctx = make_context(cutlist, cfg, layout_mode=layout_mode)
    ctx.raw_path = str(raw_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = ctx.size
    crf = min(16, int(getattr(cfg, "preview_crf", 14)))
    preset = str(getattr(cfg, "preview_preset", "fast"))
    tmpdir = Path(tempfile.mkdtemp(prefix=".preview_", dir=str(out.parent)))
    try:
        audio_info: dict[str, Any] = {"status": "none"}
        wav = None
        try:
            raw_y, sr, acl = load_raw_audio(cutlist, raw_path, fps=ctx.fps, n_frames=ctx.n_frames,
                                            budget_bytes=int(getattr(cfg, "preview_audio_budget_bytes", 1 << 30)))
        except Exception as e:  # noqa: BLE001 - a broken audio stream must not kill the preview
            ctx.warn(f"RAW audio unavailable ({e}); preview is silent")
            raw_y, sr, acl = np.zeros((0, 1), np.float32), 48000, cutlist
        if raw_y.shape[0]:
            y = build_audio(acl, raw_y, sr, fps=ctx.fps, n_frames=ctx.n_frames)
            wav = tmpdir / "audio.wav"
            _write_wav(wav, y, sr)
            audio_info = {"status": "ok", "sample_rate": sr, "channels": int(y.shape[1]) if y.ndim == 2 else 1,
                          "samples": int(y.shape[0]), "peak": float(np.max(np.abs(y))) if y.size else 0.0}
        video = tmpdir / "video.mp4"
        raw_frames: dict[int, list[tuple[int, int, float]]] = {}
        n = 0
        extra: list[str] = []
        if W % 2 or H % 2:                          # yuv420p needs even sizes: pad one row / column
            ctx.warn(f"MAIN size {W}x{H} is odd; the preview is padded to {W + W % 2}x{H + H % 2} for yuv420p")
            extra = ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        with FFmpegWriter(video, W, H, ctx.fps, crf=crf, preset=preset, extra_out=extra) as wr:
            for K, img, info in iter_render(ctx, raw_path):
                wr.write(img)
                raw_frames[K] = info
                n += 1
        if n != ctx.n_frames:
            raise RuntimeError(f"rendered {n} frames, expected {ctx.n_frames}")
        _mux(video, wav, out)
    finally:
        for p in sorted(tmpdir.glob("*")):
            p.unlink(missing_ok=True)
        tmpdir.rmdir()
    log.info("preview: %s (%dx%d @ %s, %d frames, layout %s)", out, W, H, fps_str(ctx.fps), n, ctx.layout_mode)
    return {"frames": n, "raw_frames": raw_frames, "path": str(out), "size": [W, H], "fps": fps_str(ctx.fps),
            "layout_mode": ctx.layout_mode, "audio": audio_info, "warnings": list(ctx.warnings)}


# ---------------------------------------------------------------------------------------------
# compare.mp4
# ---------------------------------------------------------------------------------------------

def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def compare_geometry(width: int, height: int, cfg: Any) -> dict:
    """compare.mp4 panel geometry for a competitor of width x height: panel size (pw, ph) -- ph =
    cfg.compare_height (960), lowered so 3 panels stay <= 3840 px wide -- the height of the label strip
    above every panel (two text lines: frame number / timecode / segment id, so the labels never cover the
    competitor's logo, name or title at the top of the picture), text scale and line pitch. The video is
    3 pw x (strip + ph), both even."""
    ph = _even(float(getattr(cfg, "compare_height", COMPARE_HEIGHT) or COMPARE_HEIGHT))
    pw = _even(width * ph / height)
    if 3 * pw > COMPARE_MAX_WIDTH:                  # landscape competitors: keep the video <= 4K wide
        ph = _even(ph * COMPARE_MAX_WIDTH / (3 * pw))
        pw = _even(width * ph / height)
    scale = max(0.4, ph / 960.0 * 0.9)              # readable at small test heights too
    line = int(34 * scale) + 4
    strip = _even(2 * line + int(14 * scale) + 4)
    return {"pw": int(pw), "ph": int(ph), "strip": int(strip), "scale": float(scale), "line": int(line)}


def _label_strip(width: int, lines: Sequence[str], geo: dict) -> np.ndarray:
    """A dedicated label strip (uint8 [strip, width, 3]) with `lines` in white, each shrunk to fit."""
    import cv2
    font = cv2.FONT_HERSHEY_SIMPLEX
    img = np.empty((geo["strip"], width, 3), np.uint8)
    img[:] = np.array(STRIP_BGR, np.uint8)
    scale, line = geo["scale"], geo["line"]
    th = max(1, int(round(2 * scale)))
    x0 = int(10 * scale) + 4
    avail = max(8, width - 2 * x0)
    y = int(6 * scale) + 2 + cv2.getTextSize("Ag", font, scale, th)[0][1]
    for t in lines:
        t = _ascii(t)
        sc, tt = scale, th
        tw = cv2.getTextSize(t, font, sc, tt)[0][0]
        if tw > avail:
            sc = scale * avail / tw
            tt = max(1, int(round(2 * sc)))
        cv2.putText(img, t, (x0, y), font, sc, (255, 255, 255), tt, cv2.LINE_AA)
        y += line
    return img


def _segment_label(cutlist: Cutlist, k: int) -> str:
    parts = []
    for s in sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.id))):
        if int(s.comp_in) <= k < int(s.comp_out):
            tag = f"S{int(s.id):02d}"
            if s.type != "raw":
                tag += {"not_in_raw": " NOT-IN-RAW", "dip": " dip", "flash": " flash"}.get(s.type, f" {s.type}")
            parts.append(tag)
    return " > ".join(parts) if parts else "no segment"


class _RecSource:
    """Recreation frames by competitor frame index k from: a video path, a RenderContext, a callable
    f(k) -> BGR, or an object with .bgr(list[k]) -> {k: BGR} (verify's frame sources)."""

    def __init__(self, src: Any, comp_fps: Fraction, n: int, raw_path: str | None):
        self.src, self.n, self.comp_fps = src, n, comp_fps
        self._it: Iterator | None = None
        self._cur: tuple[int, np.ndarray] | None = None
        self._buf: dict[int, np.ndarray] = {}
        self._pf = comp_fps
        if isinstance(src, (str, os.PathLike)):
            from .media import VideoReader
            self.kind = "video"
            self._vr = VideoReader(str(src))
            self._pf = self._vr.fps
            self._it = self._vr.frames(0, None)
        elif isinstance(src, RenderContext):
            self.kind = "render"
            self._pf = src.fps
            self._it = ((K, img) for K, img, _ in iter_render(src, raw_path or src.raw_path))
        elif callable(src):
            self.kind = "callable"
        elif hasattr(src, "bgr"):
            self.kind = "frames"
        else:
            raise TypeError(f"render_compare: unsupported recreation source {type(src).__name__}")
        if self._pf != comp_fps:
            log.warning("render_compare: recreation runs at %s fps, competitor at %s; frames are mapped by "
                        "time", fps_str(self._pf), fps_str(comp_fps))

    def get(self, k: int) -> np.ndarray | None:
        if self.kind == "callable":
            return self.src(k)
        if self.kind == "frames":
            if k not in self._buf:
                ks = list(range(k, min(self.n, k + 32)))
                self._buf = self.src.bgr(ks)
            return self._buf.get(k)
        want = k if self._pf == self.comp_fps else math.floor(Fraction(k) * self._pf / self.comp_fps + Fraction(1, 2))
        while self._cur is None or self._cur[0] < want:
            try:
                self._cur = next(self._it)
            except StopIteration:
                break
        return None if self._cur is None else self._cur[1]

    def close(self) -> None:
        if self.kind == "video":
            if self._it is not None:
                self._it.close()
            self._vr.close()
        elif self.kind == "render" and self._it is not None:
            self._it.close()


def render_compare(comp_path: str | os.PathLike, preview_frames_source: Any, cutlist: Cutlist,
                   out_path: str | os.PathLike, cfg: Any) -> None:
    """compare.mp4: competitor | recreation | amplified |difference| (x4), each panel scaled to 960 px high
    (cfg.compare_height; lowered so the video stays <= 3840 px wide for landscape competitors), frame
    number, timecode and segment id burned into a dedicated label strip above each panel (never over the
    picture, see :func:`compare_geometry`), the competitor's audio.

    preview_frames_source: the match-geometry recreation -- a video path (preview_recreation.mp4 when it is
    a match render at competitor size/fps), a RenderContext (rendered on the fly from the RAW), a callable
    f(k) -> BGR, or an object with .bgr(list[k])."""
    import cv2
    from .media import FFmpegWriter, VideoReader
    comp_fps = cutlist.comp_fps
    N = int(cutlist.competitor["frames"])
    Wc, Hc = int(cutlist.competitor["width"]), int(cutlist.competitor["height"])
    geo = compare_geometry(Wc, Hc, cfg)
    ph, pw, sh = geo["ph"], geo["pw"], geo["strip"]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw_path = _raw_path_from_cutlist(cutlist, cfg)
    rec = _RecSource(preview_frames_source, comp_fps, N, raw_path)
    tmpdir = Path(tempfile.mkdtemp(prefix=".compare_", dir=str(out.parent)))
    blank = np.zeros((ph, pw, 3), np.uint8)
    try:
        video = tmpdir / "video.mp4"
        last_comp = None
        with VideoReader(str(comp_path), fps=comp_fps) as cr, \
                FFmpegWriter(video, 3 * pw, sh + ph, comp_fps, crf=int(getattr(cfg, "compare_crf", 18)),
                             preset=str(getattr(cfg, "compare_preset", "veryfast"))) as wr:
            it = cr.frames(0, N)
            nxt = next(it, None)
            for k in range(N):
                while nxt is not None and nxt[0] < k:
                    nxt = next(it, None)
                if nxt is not None and nxt[0] == k:
                    last_comp = nxt[1]
                c = cv2.resize(last_comp, (pw, ph), interpolation=cv2.INTER_AREA) if last_comp is not None else blank.copy()
                r_img = rec.get(k)
                r = cv2.resize(r_img, (pw, ph), interpolation=cv2.INTER_AREA) if r_img is not None else blank.copy()
                d = cv2.convertScaleAbs(cv2.absdiff(c, r), alpha=DIFF_GAIN)
                tc = timecode(k, comp_fps)
                lab = _segment_label(cutlist, k)
                panels = []
                for title, img in (("COMPETITOR", c), ("RECREATION", r), (f"|DIFF| x{DIFF_GAIN:g}", d)):
                    strip = _label_strip(pw, [f"{title}  frame {k}", f"{tc}  {lab}"], geo)
                    panels.append(np.vstack([strip, img]))
                wr.write(np.hstack(panels))
        _mux(video, Path(comp_path), out, audio_map="1:a:0?")
    finally:
        rec.close()
        for p in sorted(tmpdir.glob("*")):
            p.unlink(missing_ok=True)
        tmpdir.rmdir()
    log.info("compare: %s (%dx%d, %d frames)", out, 3 * pw, sh + ph, N)
