"""Stage 4 — competitor layout analysis (DESIGN.md §5 layout.py, prompt Stage 4).

Everything here works on the competitor PROXY (``model.Proxy``: gray uint8 memmap, per-axis ratios
``(rx, ry)``); every geometry that leaves the module (``Layout.box``, zones, captions, extra regions)
is converted to competitor FULL-RES CORNER coordinates (pixel (i, j) covers [i, i+1) x [j, j+1)).

Algorithm (``analyze_layout``):

1. **Canvas / periods / static mask.** ``SAMPLE_FRAMES`` evenly spaced frames give a per-pixel median
   canvas and ``q`` = fraction of samples deviating from it; the dominant video region is the large
   component with ``q > 0.5``. One streamed pass over ALL frames then measures, per frame, the fraction
   of the reference region (outside that region) that deviates from the canvas -> frames that do not
   show the dominant layout (fullscreen video, full-canvas flashes) are found and excluded from the
   temporal statistics. The static mask is ``temporal std < cfg.static_std_thresh`` over the remaining
   frames (all frames when the layout never changes), saved as ``.npy`` (True = static). Fullscreen
   periods (``Layout.periods``, exact frame boundaries, box = the whole canvas; consumed per segment by
   D1): > NONDOM_FRAC of the canvas region changed by > DEV_LEVEL, or — a dark shot over a black canvas —
   > NONDOM_FRAC_LO changed by > DEV_LEVEL_LO with a mean change the statistics exclude; uniform
   full-canvas frames (flash / dip) stay in the dominant layout.
2. **Video box.** Dynamic pixels (std >= thresh) -> largest connected component -> bbox trimmed with
   row / column dynamic fractions (``cfg.dynamic_frac_thresh``). Each edge is located to sub-pixel
   precision on the temporal MEAN image: the mean is linear in the pixel coverage (area-averaged
   proxy, static outside level S, dynamic inside level P), so ``edge = x_ref - sum_x (p(x)-S)/(P-S)``
   over the transition window (integral / "equivalent sharp step" estimator, robust to symmetric
   blur). Where static structure sits right next to the edge (stroke, shadow, textured background)
   the mean estimate disagrees with the same estimator on the temporal STD image (std of
   c*X + (1-c)*S = c*std X) and the std estimate is used. Corner radius: joint least-squares fit over
   the four corners of the supersampled rounded-rectangle coverage model
   ``M = S (1 - C_r) + (P0 + P1 x + P2 y) C_r`` (per-corner linear levels, shared r; 1 px grid, then
   0.1 / 0.02 px refinement). When the whole frame moves (box over a blurred copy of the video) the box
   is the region clearly more active (temporal std) — or sharper (high-frequency energy) — than its
   surroundings. A locked-off shot (talking head, podcast) has a static background: when a static
   TEXTURED picture encloses the moving subject and forms a clean rounded rectangle on a uniform canvas
   (:func:`_static_video_region`), that rectangle is the box (edges from the mean image only).
   Edges are snapped to integers when within 0.25 full-res px (measured values logged).
   Border stroke / shadow hugging the box are measured from the static ring profile outside the box.
   Further dynamic components: large ones -> ``Layout.extra_regions`` (split screen / PiP, with
   per-frame activity -> ``periods``), small ones -> dynamic zones (progress bar / sticker).
3. **Background.** solid (refined mode of the static canvas; colour sampled from a few decoded colour
   frames), gradient (robust quadratic fit), image (static picture), or blur (blurred cover-scaled box
   content, sigma searched; ``sigma`` / ``blurriness`` in Video-Box pre-comp px exactly as export_ae /
   render_preview apply it; ``gain`` / ``offset`` = dimming) / dynamic.
4. **Static zones.** Connected components of static non-background pixels: compact solid blobs ->
   ``logo``; glyphs merged into lines and blocks; text next to a logo -> ``channel_name``; blocks above
   the box -> ``title`` (the largest) / ``header``; below -> ``watermark``; static overlays inside the
   box -> ``watermark`` / ``logo``; else ``other``. Colours of each zone are sampled (multicolour
   titles are noted). Not separable from a static textured ``image`` background (noted, skipped).
5. **Dynamic text overlays inside the video region** (and animated areas outside it). Per frame:
   bright components (>= TEXT_WHITE) whose 1-px ring is mostly dark (outlined text), grouped into
   lines of similar-height glyphs sharing a baseline; lines are linked over time while the track's
   CORE glyph pixels (present in > half of its frames) persist unchanged — glyphs may be added (texture
   touching the outline), none may change, so a RAW burned-in frame counter is rejected. Events lasting
   >= ``cfg.overlay_min_frames`` frames (detected on >= 75 % of their span, consensus glyphs never
   changing shape) become ``Layout.captions`` entries ``{type, comp_in, comp_out, x, y, w, h}``: the
   vertical band holding the most text frames is ``captions`` (one-letter words are searched there in a
   second pass: same letter height, centred like the band, adjacent to a caption word); other text
   lasting >= 0.4 s is ``text``. Boundaries are refined frame by frame on the consensus glyphs.
   Per-frame :class:`OverlayMasks` = consensus glyph pixels + outline margin. Moving text, stickers /
   emojis without an outline are left to the residual pass (:func:`masks_from_residuals`). Per-frame
   detection runs in a spawn process pool (proxy re-opened from its .npy) or threads; linking is
   sequential, so results do not depend on the execution mode.
6. ``debug/layout.png``: annotated frame (a caption frame: box outline + geometry, every zone, caption
   band, regions, the frame's overlay mask shaded), temporal std map, timeline (layout periods,
   caption / text events, canvas deviation).

Cached (``Cache``, stage ``layout``) by the proxy content id + ``cfg.analysis_params()``.
Every decision is logged with its evidence in the DecisionLog.

After the first FrameMap, :func:`refine_box_from_raw` (DESIGN §7 D2) re-measures the box against the
matched RAW frames — static content inside the box (a locked-off background, letterbox bars) matches RAW
and belongs to the video region — and, when it changes materially, re-runs the analysis above with that
box (``debug/layout_refine.png``); see the section comment there.
"""
from __future__ import annotations

import io
import math
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np

from .common import (Cache, DecisionLog, atomic_write_text, file_hash, json_default, log, null_dlog, params_hash,
                     replace_file, stage_key, write_image)
from .model import Box, Layout, LayoutPeriod, Proxy, Zone

__all__ = ["OverlayMasks", "analyze_layout", "refine_box_from_raw", "measure_box_from_raw", "box_coverage",
           "allowed_mask", "masks_from_residuals", "rounded_box_coverage", "LAYOUT_ALGO_VERSION"]

LAYOUT_ALGO_VERSION = 3           # bump when the algorithm changes (part of the cache key)

# ------------------------------------------------------------------------------------------------
# Internal thresholds (8-bit levels / proxy px). Each can be overridden with a Config attribute of
# the same name in lower case prefixed by ``layout_`` (e.g. cfg.layout_text_white) -- see _p().
# ------------------------------------------------------------------------------------------------
SAMPLE_FRAMES = 80                # frames sampled for the canvas median / blur test
DEV_LEVEL = 20.0                  # |frame - canvas| counted as a canvas change
NONDOM_FRAC = 0.35                # fraction of the canvas region changed -> frame not in the dominant layout
DEV_LEVEL_LO = 10.0               # ... a smaller change (dark fullscreen shots on a black canvas) ...
NONDOM_FRAC_LO = 0.7              # ... over most of the canvas region (and a mean change above STATS_EXCLUDE_MAD)
STATS_EXCLUDE_FRAC = 0.02         # ... above this (or a large mean deviation) -> excluded from the statistics
STATS_EXCLUDE_MAD = 3.0
EDGE_MIN_CONTRAST = 6.0           # |P - S| on the mean image below which the std image is used for edges
SOLID_TOL = 6.0                   # |canvas - mode| for a solid background pixel
SOLID_FRAC = 0.75                 # fraction of static outside pixels within SOLID_TOL -> solid
ZONE_CONTRAST = 20.0              # |mean - background model| of a static zone pixel
BLUR_MIN_ZNCC = 0.70              # blurred cover-scaled box content vs outside region
TEXT_WHITE = 170                  # text fill (proxy gray)
TEXT_DARK = 100                   # outline pixel
TEXT_RING_FRAC = 0.5              # fraction of the 1-px ring that must be dark
TEXT_SAME_MAD = 14.0              # mean |diff| over glyph pixels between frames of one caption event
TEXT_MIN_H_FRAC = 0.012           # minimum glyph height as a fraction of the proxy width (>= 6 px)


def _p(cfg: Any, name: str, default: Any) -> Any:
    """Tunable: cfg.layout_<name> if present, else the module default."""
    return getattr(cfg, "layout_" + name.lower(), default) if cfg is not None else default


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    return getattr(cfg, name, default) if cfg is not None else default


# ================================================================================================
# OverlayMasks
# ================================================================================================

def _save_npz_deterministic(path: str | os.PathLike, arrays: dict[str, np.ndarray]) -> None:
    """np.savez_compressed-compatible archive with fixed zip timestamps (byte-stable across runs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(arrays):
            buf = io.BytesIO()
            a = np.asarray(arrays[name])
            np.save(buf, a if a.ndim == 0 else np.ascontiguousarray(a), allow_pickle=False)
            zi = zipfile.ZipInfo(name + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, buf.getvalue())
    replace_file(tmp, p)


class OverlayMasks:
    """Per-frame bool overlay masks [h, w] at competitor PROXY resolution (True = overlay pixel).

    Sparse: only frames with a non-empty mask are stored, each as its bounding-box crop packed with
    ``np.packbits``. ``dilate_px`` is the dilation :func:`allowed_mask` applies by default (set from
    ``cfg.overlay_dilate_px`` by :func:`analyze_layout`). ``save``/``load`` use a byte-stable npz.
    """

    def __init__(self, shape: tuple[int, int] | None = None, dilate_px: int = 3):
        self.shape: tuple[int, int] | None = (int(shape[0]), int(shape[1])) if shape is not None else None
        self.dilate_px = int(dilate_px)
        self._d: dict[int, tuple[int, int, int, int, np.ndarray]] = {}

    # -- internals ---------------------------------------------------------------------------------
    def _as_mask(self, mask: np.ndarray) -> np.ndarray:
        m = np.asarray(mask).astype(bool, copy=False)
        if m.ndim != 2:
            raise ValueError(f"OverlayMasks: mask must be 2-D, got shape {m.shape}")
        if self.shape is None:
            self.shape = (int(m.shape[0]), int(m.shape[1]))
        elif tuple(m.shape) != self.shape:
            raise ValueError(f"OverlayMasks: mask shape {m.shape} != {self.shape}")
        return m

    def _crop(self, k: int) -> tuple[int, int, np.ndarray] | None:
        e = self._d.get(int(k))
        if e is None:
            return None
        y0, x0, hh, ww, packed = e
        crop = np.unpackbits(packed, count=hh * ww).reshape(hh, ww).astype(bool)
        return y0, x0, crop

    # -- public API ----------------------------------------------------------------------------------
    def set(self, k: int, mask: np.ndarray | None) -> None:
        """Replace frame k's mask (None / all-False removes it)."""
        k = int(k)
        if mask is None:
            self._d.pop(k, None)
            return
        m = self._as_mask(mask)
        rows = np.flatnonzero(m.any(axis=1))
        if rows.size == 0:
            self._d.pop(k, None)
            return
        cols = np.flatnonzero(m.any(axis=0))
        y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
        crop = m[y0:y1, x0:x1]
        self._d[k] = (y0, x0, y1 - y0, x1 - x0, np.packbits(crop.ravel()))

    def get(self, k: int) -> np.ndarray | None:
        """Full-size bool mask of frame k, or None when the frame has no overlay."""
        c = self._crop(k)
        if c is None or self.shape is None:
            return None
        y0, x0, crop = c
        out = np.zeros(self.shape, bool)
        out[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]] = crop
        return out

    def get_dilated(self, k: int, dilate_px: int | None = None) -> np.ndarray | None:
        """get(k) dilated by a disc of ``dilate_px`` (default ``self.dilate_px``), computed on the crop."""
        c = self._crop(k)
        if c is None or self.shape is None:
            return None
        d = self.dilate_px if dilate_px is None else int(dilate_px)
        y0, x0, crop = c
        H, W = self.shape
        if d <= 0:
            out = np.zeros(self.shape, bool)
            out[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]] = crop
            return out
        import cv2
        py0, px0 = max(0, y0 - d), max(0, x0 - d)
        py1, px1 = min(H, y0 + crop.shape[0] + d), min(W, x0 + crop.shape[1] + d)
        pad = np.zeros((py1 - py0, px1 - px0), np.uint8)
        pad[y0 - py0:y0 - py0 + crop.shape[0], x0 - px0:x0 - px0 + crop.shape[1]] = crop
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))
        pad = cv2.dilate(pad, ker)
        out = np.zeros(self.shape, bool)
        out[py0:py1, px0:px1] = pad > 0
        return out

    def union(self, k: int, mask: np.ndarray | None) -> None:
        """OR ``mask`` into frame k's mask."""
        if mask is None:
            return
        m = self._as_mask(mask)
        if not m.any():
            return
        cur = self.get(k)
        self.set(k, m if cur is None else (cur | m))

    def bbox(self, k: int) -> tuple[int, int, int, int] | None:
        """(x, y, w, h) proxy bbox of frame k's mask, or None."""
        e = self._d.get(int(k))
        if e is None:
            return None
        y0, x0, hh, ww, _ = e
        return x0, y0, ww, hh

    def area(self, k: int) -> int:
        c = self._crop(k)
        return 0 if c is None else int(c[2].sum())

    def frames(self) -> list[int]:
        return sorted(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, k: object) -> bool:
        return isinstance(k, (int, np.integer)) and int(k) in self._d

    def copy(self) -> "OverlayMasks":
        o = OverlayMasks(self.shape, self.dilate_px)
        o._d = {k: (a, b, c, d, e.copy()) for k, (a, b, c, d, e) in self._d.items()}
        return o

    def save(self, path: str | os.PathLike) -> None:
        """Byte-stable npz at exactly ``path``: shape, dilate_px, frames, boxes [n, 4], offsets, data."""
        ks = self.frames()
        boxes = np.array([self._d[k][:4] for k in ks], np.int32).reshape(-1, 4)
        chunks = [self._d[k][4] for k in ks]
        offsets = np.zeros(len(ks) + 1, np.int64)
        if chunks:
            offsets[1:] = np.cumsum([c.size for c in chunks])
        data = np.concatenate(chunks) if chunks else np.zeros(0, np.uint8)
        shape = np.array(self.shape if self.shape is not None else (-1, -1), np.int64)
        _save_npz_deterministic(path, {"shape": shape, "dilate_px": np.array(self.dilate_px, np.int64),
                                       "frames": np.array(ks, np.int64), "boxes": boxes, "offsets": offsets,
                                       "data": data.astype(np.uint8)})

    @staticmethod
    def load(path: str | os.PathLike) -> "OverlayMasks":
        with np.load(path, allow_pickle=False) as z:
            shape = tuple(int(v) for v in z["shape"])
            o = OverlayMasks(None if shape[0] < 0 else shape, int(z["dilate_px"]) if "dilate_px" in z.files else 3)
            frames, boxes, offsets, data = z["frames"], z["boxes"], z["offsets"], z["data"]
            for i, k in enumerate(frames.tolist()):
                y0, x0, hh, ww = (int(v) for v in boxes[i])
                o._d[int(k)] = (y0, x0, hh, ww, data[int(offsets[i]):int(offsets[i + 1])].copy())
        return o


# ================================================================================================
# Box coverage / allowed mask / residual masks
# ================================================================================================

def rounded_box_coverage(size: tuple[int, int], x0: float, y0: float, x1: float, y1: float, radius: float,
                         ss: int = 4) -> np.ndarray:
    """Coverage float32 [h, w] of the rounded rectangle [x0, x1] x [y0, y1] (CORNER coords, radius r)
    on an image of ``size = (w, h)``, with ss x ss super-sampling at sample centres — the same
    point-inside test as ``geometry.rounded_rect_mask`` (which it equals for an integer box at 0,0)."""
    w, h = int(size[0]), int(size[1])
    r = max(0.0, min(float(radius), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    offs = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(w)[:, None] + offs[None, :]).reshape(-1)
    ys = (np.arange(h)[:, None] + offs[None, :]).reshape(-1)
    inx = (xs >= x0) & (xs <= x1)
    iny = (ys >= y0) & (ys <= y1)
    out = np.zeros((h, w), np.float32)
    cols = np.flatnonzero(inx.reshape(w, ss).any(axis=1))
    rows = np.flatnonzero(iny.reshape(h, ss).any(axis=1))
    if cols.size == 0 or rows.size == 0:
        return out
    c0, c1, r0, r1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
    sx = xs[c0 * ss:c1 * ss]
    sy = ys[r0 * ss:r1 * ss]
    inside = iny[r0 * ss:r1 * ss, None] & inx[None, c0 * ss:c1 * ss]
    if r > 0:
        cx = np.clip(sx, x0 + r, x1 - r)
        cy = np.clip(sy, y0 + r, y1 - r)
        dx = (sx - cx)[None, :]
        dy = (sy - cy)[:, None]
        inside &= (dx * dx + dy * dy) <= r * r
    out[r0:r1, c0:c1] = inside.reshape(r1 - r0, ss, c1 - c0, ss).mean(axis=(1, 3))
    return out


def box_coverage(layout: Layout, comp: Proxy) -> np.ndarray:
    """Float [h, w] rounded-box coverage at competitor proxy resolution (1 everywhere without a box).

    Semantics of ``geometry.rounded_rect_mask`` (CORNER convention, 4x4 super-sampling at sample
    centres) applied to ``layout.box`` scaled by the proxy ratios (radius scaled by the mean ratio)."""
    w, h = int(comp.size[0]), int(comp.size[1])
    if layout is None or layout.box is None:
        return np.ones((h, w), np.float32)
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    b = layout.box
    return rounded_box_coverage((w, h), b.x * rx, b.y * ry, (b.x + b.w) * rx, (b.y + b.h) * ry,
                                b.corner_radius * (rx + ry) / 2.0)


_BASE_MEMO: dict[tuple, np.ndarray] = {}


def _base_allowed(layout: Layout | None, comp: Proxy) -> np.ndarray:
    """coverage >= 0.99 AND NOT static (memoised per box / static-mask file / proxy geometry)."""
    w, h = int(comp.size[0]), int(comp.size[1])
    sm = getattr(layout, "static_mask_file", "") if layout is not None else ""
    st_sig: tuple = ()
    if sm and Path(sm).is_file():
        s = Path(sm).stat()
        st_sig = (str(sm), s.st_size, s.st_mtime_ns)
    b = layout.box if layout is not None else None
    key = ((b.x, b.y, b.w, b.h, b.corner_radius) if b is not None else None, st_sig, (w, h),
           (float(comp.ratio[0]), float(comp.ratio[1])))
    base = _BASE_MEMO.get(key)
    if base is None:
        base = box_coverage(layout, comp) >= 0.99
        if st_sig:
            st = np.load(sm).astype(bool)
            if st.shape == base.shape:
                base &= ~st
            else:
                log.warning("static mask %s has shape %s != proxy %s - ignored", sm, st.shape, base.shape)
        base.setflags(write=False)
        if len(_BASE_MEMO) >= 8:
            _BASE_MEMO.pop(next(iter(_BASE_MEMO)))
        _BASE_MEMO[key] = base
    return base


def allowed_mask(layout: Layout, overlays: Any, k: int, comp: Proxy, dilate_px: int | None = None) -> np.ndarray:
    """Bool [h, w] at proxy res: box coverage >= 0.99 AND NOT static AND NOT dilated overlay(k).

    ``dilate_px`` defaults to ``overlays.dilate_px`` (cfg.overlay_dilate_px at analysis time, 3)."""
    base = _base_allowed(layout, comp)
    if overlays is None:
        return base.copy()
    d = dilate_px if dilate_px is not None else int(getattr(overlays, "dilate_px", 3))
    if hasattr(overlays, "get_dilated"):
        ov = overlays.get_dilated(int(k), d)
    else:  # any object with get(k) -> mask | None
        ov = overlays.get(int(k))
        if ov is not None and d > 0 and np.any(ov):
            import cv2
            ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))
            ov = cv2.dilate(np.asarray(ov, np.uint8), ker) > 0
    if ov is None:
        return base.copy()
    ov = np.asarray(ov, bool)
    if ov.shape != base.shape:
        log.warning("overlay mask of frame %d has shape %s != proxy %s - ignored", k, ov.shape, base.shape)
        return base.copy()
    return base & ~ov


class LayoutOverlays:
    """Read-only overlay provider of the LAYOUT stage's own findings (competitor-only), for verification: its
    per-frame caption / text masks (``OverlayMasks``) united with its DYNAMIC zones (``Zone.static`` False --
    the caption band over the caption period, stickers, progress bars) on their active frames. The zone
    rectangles cover words the per-frame text detection missed (a word missed there would otherwise count as a
    recreation mismatch: overlays are never recreated). ``get`` / ``get_dilated`` like ``OverlayMasks``."""

    def __init__(self, masks: "OverlayMasks | None", rects: list[tuple[int | None, int | None, int, int, int, int]],
                 shape: tuple[int, int], dilate_px: int = 3):
        self.masks, self.rects = masks, rects          # rects: (comp_in, comp_out, y0, y1, x0, x1) at proxy res
        self.shape = (int(shape[0]), int(shape[1]))
        self.dilate_px = int(getattr(masks, "dilate_px", dilate_px) if masks is not None else dilate_px)

    def _zones(self, k: int) -> np.ndarray | None:
        out = None
        for a, b, y0, y1, x0, x1 in self.rects:
            if (a is None or a <= k) and (b is None or k < b):
                if out is None:
                    out = np.zeros(self.shape, bool)
                out[y0:y1, x0:x1] = True
        return out

    def get(self, k: int) -> np.ndarray | None:
        m = self.masks.get(int(k)) if self.masks is not None else None
        z = self._zones(int(k))
        if z is None:
            return m
        return z if m is None else (m | z)

    def get_dilated(self, k: int, dilate_px: int | None = None) -> np.ndarray | None:
        d = self.dilate_px if dilate_px is None else int(dilate_px)
        m = self.masks.get_dilated(int(k), d) if self.masks is not None else None
        z = self._zones(int(k))
        if z is not None and d > 0:
            z = _dilate(z, d)
        if z is None:
            return m
        return z if m is None else (m | z)

    def frames(self) -> list[int]:
        return self.masks.frames() if self.masks is not None else []


def layout_overlay_masks(layout: Layout | None, shape: tuple[int, int] | None = None,
                         ratio: tuple[float, float] | None = None, dilate_px: int = 3) -> LayoutOverlays | None:
    """The overlays the LAYOUT stage found on its own (competitor-only): the per-frame caption / text masks of
    ``layout.overlay_mask_file`` (without the file: rectangles of ``layout.captions``) plus the dynamic zones
    (``LayoutOverlays``) -- never refine's pass-2 residual masks, which are computed from the match being
    judged and would hide its own mismatch (verify's masks, DESIGN §5 verify). ``shape`` (h, w) and ``ratio``
    (proxy rx, ry; default ``layout.proxy_ratio``) place the rectangles. None when nothing is available."""
    if layout is None:
        return None
    masks = None
    p = getattr(layout, "overlay_mask_file", "") or ""
    if p and Path(p).is_file():
        try:
            masks = OverlayMasks.load(p)
        except Exception as e:  # noqa: BLE001 - fall back to the caption rectangles
            log.warning("layout overlay masks %s unreadable (%s): using the caption rectangles", p, e)
    if shape is None and masks is not None:
        shape = masks.shape
    ratio = ratio if ratio is not None else getattr(layout, "proxy_ratio", None)
    if shape is None or ratio is None:
        return None if masks is None else LayoutOverlays(masks, [], masks.shape, dilate_px)
    h, w = int(shape[0]), int(shape[1])
    rx, ry = float(ratio[0]), float(ratio[1])

    def rect(x: float, y: float, ww: float, hh: float) -> tuple[int, int, int, int] | None:
        x0, y0 = max(0, int(math.floor(float(x) * rx))), max(0, int(math.floor(float(y) * ry)))
        x1 = min(w, int(math.ceil((float(x) + float(ww)) * rx)))
        y1 = min(h, int(math.ceil((float(y) + float(hh)) * ry)))
        return None if x1 <= x0 or y1 <= y0 else (y0, y1, x0, x1)

    rects: list[tuple[int | None, int | None, int, int, int, int]] = []
    if masks is None:
        for c in getattr(layout, "captions", None) or []:
            if not isinstance(c, dict) or not all(q in c for q in ("x", "y", "w", "h", "comp_in", "comp_out")):
                continue
            r = rect(c["x"], c["y"], c["w"], c["h"])
            if r is not None:
                rects.append((int(c["comp_in"]), int(c["comp_out"]), *r))
    for z in getattr(layout, "zones", None) or []:
        if getattr(z, "static", True):
            continue                     # static zones are in the static mask already
        r = rect(z.x, z.y, z.w, z.h)
        if r is not None:
            zi, zo = getattr(z, "comp_in", None), getattr(z, "comp_out", None)
            rects.append((None if zi is None else int(zi), None if zo is None else int(zo), *r))
    return LayoutOverlays(masks, rects, (h, w), dilate_px)


def masks_from_residuals(residuals: dict[int, np.ndarray], base_allowed: np.ndarray, cfg: Any) -> dict[int, np.ndarray]:
    """Overlay pass 2 (DESIGN §5 refine step 4): per-frame masks of pixels that consistently do not match RAW.

    ``residuals[k]`` = |comp - warped best RAW| (8-bit, proxy res, zeros outside the evaluated ROI).
    A pixel of frame k is an overlay pixel when residual >= ``cfg.overlay_resid_thresh`` in at least
    ``cfg.overlay_min_frames`` of the given frames within k-3..k+3 (temporal support: a real overlay
    persists, a mismatch speck does not); specks < 3 px are dropped, the rest is dilated by
    ``cfg.overlay_dilate_px`` and clipped to ``base_allowed``. A frame whose mask would cover > 35 % of
    the allowed region is skipped (that is a wrong match, not an overlay)."""
    import cv2
    ks = sorted(int(k) for k in residuals)
    if not ks:
        return {}
    base_allowed = np.asarray(base_allowed, bool)
    ys, xs = np.nonzero(base_allowed)
    if ys.size == 0:
        return {}
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    thr = float(_cfg(cfg, "overlay_resid_thresh", 40.0))
    min_frames = int(_cfg(cfg, "overlay_min_frames", 3))
    d = int(_cfg(cfg, "overlay_dilate_px", 3))
    base = base_allowed[y0:y1, x0:x1]
    hi = np.stack([(np.asarray(residuals[k])[y0:y1, x0:x1] >= thr) & base for k in ks])
    kk = np.asarray(ks)
    lo_i = np.searchsorted(kk, kk - 3, side="left")
    hi_i = np.searchsorted(kk, kk + 3, side="right")
    csum = np.concatenate([np.zeros((1,) + hi.shape[1:], np.int32), np.cumsum(hi, axis=0, dtype=np.int32)])
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1)) if d > 0 else None
    area = max(1, int(base_allowed.sum()))
    out: dict[int, np.ndarray] = {}
    for i, k in enumerate(ks):
        if not hi[i].any():
            continue
        need = min(min_frames, int(hi_i[i] - lo_i[i]))
        support = csum[hi_i[i]] - csum[lo_i[i]]
        m = hi[i] & (support >= need)
        if not m.any():
            continue
        n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        small = np.flatnonzero(st[:, cv2.CC_STAT_AREA] < 3)
        small = small[small > 0]
        if small.size:
            m &= ~np.isin(lab, small)
        if not m.any():
            continue
        m8 = m.astype(np.uint8)
        if ker is not None:
            m8 = cv2.dilate(m8, ker)
        m = (m8 > 0) & base
        if int(m.sum()) > 0.35 * area:
            continue
        full = np.zeros(base_allowed.shape, bool)
        full[y0:y1, x0:x1] = m
        out[k] = full
    return out


# ================================================================================================
# Small helpers
# ================================================================================================

def _med(v: np.ndarray) -> float:
    """Median of a small 1-D array (much cheaper than np.median for a handful of values)."""
    a = sorted(float(x) for x in v)
    n = len(a)
    if n == 0:
        return float("nan")
    return a[n // 2] if n % 2 else 0.5 * (a[n // 2 - 1] + a[n // 2])


def _hex(bgr: Sequence[float]) -> str:
    b, g, r = (int(round(min(255.0, max(0.0, float(v))))) for v in bgr[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _gray_hex(v: float) -> str:
    return _hex((v, v, v))


def _cc(mask: np.ndarray, connectivity: int = 8):
    import cv2
    return cv2.connectedComponentsWithStats(np.ascontiguousarray(mask, dtype=np.uint8), connectivity=connectivity)


def _morph(mask: np.ndarray, op: str, k: int) -> np.ndarray:
    import cv2
    if k <= 1:
        return mask.astype(bool)
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    f = {"open": cv2.MORPH_OPEN, "close": cv2.MORPH_CLOSE}[op]
    return cv2.morphologyEx(mask.astype(np.uint8), f, ker) > 0


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    import cv2
    if r <= 0:
        return mask.astype(bool)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(mask.astype(np.uint8), ker) > 0


def _erode_mask(mask: np.ndarray, r: int) -> np.ndarray:
    import cv2
    if r <= 0:
        return mask.astype(bool)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.erode(mask.astype(np.uint8), ker, borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0


def _runs(flags: np.ndarray) -> list[tuple[int, int, Any]]:
    """Maximal runs [a, b) of equal values in a 1-D array -> [(a, b, value)]."""
    v = np.asarray(flags)
    if v.size == 0:
        return []
    change = np.flatnonzero(v[1:] != v[:-1]) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [v.size]])
    return [(int(a), int(b), v[a].item() if hasattr(v[a], "item") else v[a]) for a, b in zip(starts, ends)]


def _iter_chunks(comp: Proxy, ks: np.ndarray, chunk: int = 16) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (frame indices, uint8 [c, h, w]) for the given (sorted) frames, contiguous runs sliced."""
    ks = np.asarray(ks, np.int64)
    if ks.size == 0:
        return
    breaks = np.flatnonzero(np.diff(ks) != 1) + 1
    for run in np.split(ks, breaks):
        for i in range(0, run.size, chunk):
            sub = run[i:i + chunk]
            if comp.index_map is None:
                yield sub, np.asarray(comp.frames[int(sub[0]):int(sub[-1]) + 1])
            else:
                yield sub, np.stack([np.asarray(comp.get(int(k))) for k in sub])


def _proxy_id(comp: Proxy) -> str:
    """Content identity of the proxy (media file hash + geometry, else a hash of the frames)."""
    parts: list[Any] = [int(comp.n), list(comp.size), list(comp.full_size), [float(r) for r in comp.ratio],
                        str(comp.fps)]
    p = Path(comp.path) if comp.path else None
    if p is not None and p.is_file():
        parts.append(file_hash(p))
    else:
        import hashlib
        h = hashlib.blake2b(digest_size=16)
        for _, fr in _iter_chunks(comp, np.arange(comp.n), 64):
            h.update(np.ascontiguousarray(fr).tobytes())
        parts.append(h.hexdigest())
    return params_hash(*parts)


def _colour_frames(comp: Proxy, ks: Sequence[int]) -> dict[int, np.ndarray] | None:
    """BGR frames at proxy size decoded from the competitor file (None if unavailable)."""
    if not comp.path or not Path(comp.path).is_file():
        return None
    try:
        import cv2
        from .media import VideoReader
        with VideoReader(comp.path, fps=comp.fps) as vr:
            got = vr.get_many(sorted(set(int(k) for k in ks)), fmt="bgr24")
        out = {}
        W, H = comp.full_size
        for k, img in got.items():
            if img.shape[1] != int(W) or img.shape[0] != int(H):
                log.warning("layout: decoded frame %dx%d != proxy full size %dx%d; colour sampling skipped",
                            img.shape[1], img.shape[0], W, H)
                return None
            out[k] = cv2.resize(img, tuple(int(v) for v in comp.size), interpolation=cv2.INTER_AREA)
        return out
    except Exception as e:  # noqa: BLE001 - colour is optional evidence
        log.warning("layout: colour frames unavailable (%s); using gray values", e)
        return None


# ================================================================================================
# 1. Statistics: canvas, per-frame deviation, static mask
# ================================================================================================

@dataclass
class _Stats:
    med: np.ndarray                  # float32 [h, w] median canvas of the sampled frames
    q: np.ndarray                    # float32 [h, w] fraction of samples deviating from the canvas
    sample_idx: np.ndarray           # sampled frame indices
    ref: np.ndarray | None           # bool [h, w] reference (canvas) region, None = no canvas
    prov_box: tuple[int, int, int, int]
    dev: np.ndarray                  # [n] fraction of ref pixels deviating by > DEV_LEVEL
    mad: np.ndarray                  # [n] mean |frame - canvas| over ref
    fstd: np.ndarray                 # [n] spatial std of the frame over ref
    used: np.ndarray                 # bool [n] frames in the dominant statistics
    mean: np.ndarray                 # float32 [h, w] temporal mean over used frames
    std: np.ndarray                  # float32 [h, w] temporal std over used frames
    notes: list[str] = field(default_factory=list)
    dev_lo: np.ndarray | None = None # [n] fraction of ref pixels deviating by > DEV_LEVEL_LO
    thr_mad: float = STATS_EXCLUDE_MAD   # mean-deviation level above which a frame left the dominant layout


def _largest_component_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    n, lab, st, _ = _cc(mask)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(st[1:, 4]))
    x, y, w, h = (int(v) for v in st[i, :4])
    return x, y, x + w, y + h


def _compute_stats(comp: Proxy, cfg: Any, dlog: DecisionLog,
                   region: tuple[int, int, int, int] | None = None) -> _Stats:
    """``region``: known dominant video region (proxy x0, y0, x1, y1), e.g. a box verified against RAW;
    default: the provisional region of pixels that differ from the canvas in most samples."""
    h, w = int(comp.size[1]), int(comp.size[0])
    n = int(comp.n)
    S = int(min(n, _p(cfg, "SAMPLE_FRAMES", SAMPLE_FRAMES)))
    idx = np.unique(np.round(np.linspace(0, n - 1, S)).astype(np.int64))
    samples = np.stack([np.asarray(comp.get(int(k))) for k in idx])
    med = np.median(samples, axis=0).astype(np.float32)
    dev_level = float(_p(cfg, "DEV_LEVEL", DEV_LEVEL))
    dev_level_lo = float(_p(cfg, "DEV_LEVEL_LO", DEV_LEVEL_LO))
    q = np.zeros((h, w), np.float32)
    for i in range(0, len(idx), 16):
        q += (np.abs(samples[i:i + 16].astype(np.float32) - med) > dev_level).sum(axis=0)
    q /= max(1, len(idx))
    del samples
    # provisional dominant video region: pixels that differ from the canvas in most samples
    if region is not None:
        bb = (max(0, int(region[0])), max(0, int(region[1])), min(w, int(region[2])), min(h, int(region[3])))
    else:
        dyn_q = _morph(_morph(q > 0.5, "open", 3), "close", 5)
        bb = _largest_component_bbox(dyn_q)
        if bb is None:
            bb = (0, 0, w, h)
    margin = max(2, int(round(0.01 * max(h, w))))
    x0, y0, x1, y1 = bb
    ref = np.ones((h, w), bool)
    ref[max(0, y0 - margin):min(h, y1 + margin), max(0, x0 - margin):min(w, x1 + margin)] = False
    ref &= q < 0.5
    if ref.sum() < 0.05 * h * w:
        ref = None
    # streamed pass over all frames: integer sums for mean/std + per-frame canvas deviation measured on
    # every 4th reference pixel (plenty for a fraction / mean; 4x cheaper)
    s1 = np.zeros((h, w), np.uint64)
    s2 = np.zeros((h, w), np.uint64)
    dev = np.zeros(n, np.float32)
    dev_lo = np.zeros(n, np.float32)
    mad = np.zeros(n, np.float32)
    fstd = np.zeros(n, np.float32)
    ref_idx = np.flatnonzero(ref.ravel())[::4] if ref is not None else None
    med_ref = np.round(med.ravel()[ref_idx]).astype(np.int16) if ref_idx is not None else None
    for ks, fr in _iter_chunks(comp, np.arange(n), 32):
        c = np.asarray(fr)
        c16 = c.astype(np.uint16)
        s1 += c.sum(axis=0, dtype=np.uint32)
        s2 += (c16 * c16).sum(axis=0, dtype=np.uint32)
        if ref_idx is not None:
            v = c.reshape(len(ks), -1)[:, ref_idx].astype(np.int16)
            d = np.abs(v - med_ref)
            dev[ks] = (d > dev_level).mean(axis=1)
            dev_lo[ks] = (d > dev_level_lo).mean(axis=1)
            mad[ks] = d.mean(axis=1)
            fstd[ks] = v.std(axis=1)
    used = np.ones(n, bool)
    notes: list[str] = []
    thr_mad = float(_p(cfg, "STATS_EXCLUDE_MAD", STATS_EXCLUDE_MAD))
    if ref_idx is not None:
        mad_med = float(np.median(mad))
        mad_spread = 1.4826 * float(np.median(np.abs(mad - mad_med)))
        thr_mad = max(float(_p(cfg, "STATS_EXCLUDE_MAD", STATS_EXCLUDE_MAD)), mad_med + 8 * mad_spread)
        thr_dev = max(float(_p(cfg, "STATS_EXCLUDE_FRAC", STATS_EXCLUDE_FRAC)), float(np.median(dev)) * 3)
        used = ~((dev > thr_dev) | (mad > thr_mad))
        if used.sum() < max(10, int(0.3 * n)):
            notes.append(f"layout changes on {int((~used).sum())} of {n} frames: static statistics use all frames")
            dlog.record("layout", "stats_all_frames", excluded=int((~used).sum()), frames=n)
            used = np.ones(n, bool)
    excl = np.flatnonzero(~used)
    for ks, fr in _iter_chunks(comp, excl, 32):
        c = np.asarray(fr)
        c16 = c.astype(np.uint16)
        s1 -= c.sum(axis=0, dtype=np.uint32).astype(np.uint64)
        s2 -= (c16 * c16).sum(axis=0, dtype=np.uint32).astype(np.uint64)
    nu = max(1, int(used.sum()))
    mean = s1.astype(np.float64) / nu
    std = np.sqrt(np.maximum(s2.astype(np.float64) / nu - mean * mean, 0.0))
    dlog.record("layout", "statistics", frames=n, samples=int(len(idx)), used=int(used.sum()),
                excluded=[int(k) for k in excl[:200]], provisional_region=list(bb),
                region_source="given" if region is not None else "canvas deviation",
                ref_pixels=int(ref.sum()) if ref is not None else 0)
    return _Stats(med, q, idx, ref, bb, dev, mad, fstd, used, mean.astype(np.float32), std.astype(np.float32), notes,
                  dev_lo, float(thr_mad))


# ================================================================================================
# 2. Video box: detection, sub-pixel edges, radius, stroke
# ================================================================================================

def _edge_canonical(A: np.ndarray, x0: int, rows_ok: np.ndarray | None) -> tuple[float, float, float] | None:
    """Left-edge estimate on a (rows x cols) array whose content (inside) starts near column x0.

    Integral estimator: e = (x0+2) - sum_{x=x0-3}^{x0+1} (p(x) - S) / (P - S), with p the row-averaged
    profile, S = mean of p over [x0-6, x0-3), P = mean over [x0+2, x0+6). Returns (e, S, P) or None."""
    W = A.shape[1]
    if x0 - 4 < 0 or x0 + 6 > W:
        return None
    rows = A if rows_ok is None or rows_ok.sum() < 3 else A[rows_ok]
    p = rows[:, max(0, x0 - 6):x0 + 6].astype(np.float64).mean(axis=0)
    off = max(0, x0 - 6)
    xs = np.arange(off, x0 + 6)
    Sv = p[(xs < x0 - 3)]
    Pv = p[(xs >= x0 + 2)]
    if Sv.size == 0 or Pv.size == 0:
        return None
    S, P = float(Sv.mean()), float(Pv.mean())
    if abs(P - S) < 1e-6:
        return None
    sel = (xs >= x0 - 3) & (xs < x0 + 2)
    c = (p[sel] - S) / (P - S)
    return float((x0 + 2) - c.sum()), S, P


def _subpixel_edge(img: np.ndarray, side: str, e_int: int, band: tuple[int, int],
                   valid: np.ndarray | None) -> tuple[float, dict]:
    """Sub-pixel position (CORNER coords, proxy) of one box edge on ``img`` (mean / sharpness image).

    side: left|right|top|bottom; e_int: integer boundary (left/top: first inside index; right/bottom:
    exclusive end); band: index range along the edge used for the profile."""
    H, W = img.shape
    a, b = int(max(0, band[0])), int(min(W if side in ("top", "bottom") else H, band[1]))
    if side == "left":
        A, V, x0, L = img[a:b, :], (valid[a:b, :] if valid is not None else None), e_int, W
    elif side == "right":
        A, V, x0, L = img[a:b, ::-1], (valid[a:b, ::-1] if valid is not None else None), W - e_int, W
    elif side == "top":
        A, V, x0, L = img[:, a:b].T, (valid[:, a:b].T if valid is not None else None), e_int, H
    else:
        A, V, x0, L = img[::-1, a:b].T, (valid[::-1, a:b].T if valid is not None else None), H - e_int, H
    ev: dict = {"side": side, "integer": int(e_int)}
    if x0 <= 1:
        # the box touches the frame border: the edge is the border itself
        e = 0.0
        ev["border"] = True
    else:
        e = float(x0)
        res = None
        for _ in range(3):
            xi = int(round(e))
            rows_ok = None
            if V is not None and 0 <= xi - 6 and xi + 6 <= A.shape[1]:
                rows_ok = V[:, xi - 6:xi + 6].all(axis=1)
            r = _edge_canonical(A, xi, rows_ok)
            if r is None:
                break
            res = r
            if abs(r[0] - e) < 0.05:
                e = r[0]
                break
            e = r[0]
        if res is not None:
            ev.update({"S": round(res[1], 3), "P": round(res[2], 3)})
        ev["canonical"] = round(e, 4)
    out = e if side in ("left", "top") else L - e
    ev["value"] = round(out, 4)
    return float(out), ev


def _corner_setup(M: np.ndarray, valid: np.ndarray, edges: tuple[float, float, float, float], name: str,
                  wc: int, ss: int) -> dict | None:
    H, W = M.shape
    ex0, ey0, ex1, ey1 = edges
    fx, fy = name in ("tr", "br"), name in ("bl", "br")
    A = M[:, ::-1] if fx else M
    V = valid[:, ::-1] if fx else valid
    if fy:
        A, V = A[::-1], V[::-1]
    cx = (W - ex1) if fx else ex0
    cy = (H - ey1) if fy else ey0
    i0, j0 = max(0, int(math.floor(cx)) - 3), max(0, int(math.floor(cy)) - 3)
    i1, j1 = min(W, int(math.floor(cx)) + wc), min(H, int(math.floor(cy)) + wc)
    if i1 - i0 < 6 or j1 - j0 < 6:
        return None
    sub = A[j0:j1, i0:i1].astype(np.float64)
    vs = V[j0:j1, i0:i1]
    if vs.mean() < 0.5:
        return None
    offs = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(i0, i1)[:, None] + offs[None, :]).reshape(-1)
    ys = (np.arange(j0, j1)[:, None] + offs[None, :]).reshape(-1)
    px, py = np.meshgrid((np.arange(i0, i1) + 0.5 - cx) / wc, (np.arange(j0, j1) + 0.5 - cy) / wc)
    return {"name": name, "y": sub[vs], "vs": vs, "xs": xs, "ys": ys, "cx": cx, "cy": cy, "ss": ss,
            "nh": j1 - j0, "nw": i1 - i0, "px": px[vs], "py": py[vs]}


def _corner_sse(c: dict, r: float) -> float:
    X = c["xs"][None, :]
    Y = c["ys"][:, None]
    inside = (X >= c["cx"]) & (Y >= c["cy"])
    if r > 0:
        qx = X - (c["cx"] + r)
        qy = Y - (c["cy"] + r)
        inside &= ~((qx < 0) & (qy < 0) & (qx * qx + qy * qy > r * r))
    ss = c["ss"]
    C = inside.reshape(c["nh"], ss, c["nw"], ss).mean(axis=(1, 3))[c["vs"]]
    D = np.stack([1.0 - C, C, C * c["px"], C * c["py"]], axis=1)
    coef, *_ = np.linalg.lstsq(D, c["y"], rcond=None)
    res = c["y"] - D @ coef
    return float(res @ res)


def _fit_radius(M: np.ndarray, valid: np.ndarray, edges: tuple[float, float, float, float],
                corners: Sequence[str]) -> tuple[float, dict]:
    """Joint least-squares corner radius (proxy px) over the given corners (see module doc):
    coarse grid (1 px), then 0.1 px and 0.02 px refinements around the minimum."""
    ex0, ey0, ex1, ey1 = edges
    r_cap = max(0.0, min(ex1 - ex0, ey1 - ey0) / 2.0 - 1.0)
    ev: dict = {"corners": list(corners)}
    if not corners or r_cap <= 0:
        return 0.0, {**ev, "reason": "no fittable corner"}
    wc = int(min(max(12, min(28, round(0.3 * min(ex1 - ex0, ey1 - ey0)))), r_cap + 4))
    cs: list[dict] = []
    grid = np.zeros(0)
    table = np.zeros((0, 0))
    r_hi = 0.0
    for _attempt in range(5):
        cs = [c for c in (_corner_setup(M, valid, edges, nm, wc, 6) for nm in corners) if c is not None]
        if not cs:
            return 0.0, {**ev, "reason": "corner windows invalid"}
        r_hi = min(r_cap, wc - 3.0)
        grid = np.arange(0.0, r_hi + 1e-9, 1.0)
        table = np.array([[_corner_sse(c, r) for c in cs] for r in grid])
        r0 = float(grid[int(np.argmin(table.sum(axis=1)))])
        if r0 >= r_hi - 1.0 and wc < r_cap + 4:
            wc = int(min(r_cap + 4, wc * 2))
            continue
        break
    tot = table.sum(axis=1)
    r_best = float(grid[int(np.argmin(tot))])
    best_sse = float(tot.min())
    for step, half in ((0.1, 1.0), (0.02, 0.1)):
        fine = np.arange(max(0.0, r_best - half), min(r_hi, r_best + half) + 1e-9, step)
        tf = np.array([sum(_corner_sse(c, r) for c in cs) for r in fine])
        if tf.size and float(tf.min()) <= best_sse:
            r_best, best_sse = float(fine[int(np.argmin(tf))]), float(tf.min())
    per = {c["name"]: float(grid[int(np.argmin(table[:, i]))]) for i, c in enumerate(cs)}
    ev.update({"window": wc, "radius_proxy": round(r_best, 3), "per_corner_proxy_1px": per,
               "sse": best_sse, "sse_square_corner": float(table[0].sum())})
    return r_best, ev


def _sharpness(comp: Proxy, ks: np.ndarray) -> np.ndarray:
    """Temporal mean of |f - GaussianBlur(f, 1.5)| (high-frequency energy) over frames ks."""
    import cv2
    acc = None
    for _, fr in _iter_chunks(comp, ks):
        for f in fr:
            f32 = f.astype(np.float32)
            hf = np.abs(f32 - cv2.GaussianBlur(f32, (0, 0), 1.5))
            acc = hf if acc is None else acc + hf
    return (acc / max(1, len(ks))).astype(np.float32)


@dataclass
class _BoxResult:
    kind: str                                # 'boxed' | 'fullscreen'
    edges: tuple[float, float, float, float] # proxy CORNER coords (x0, y0, x1, y1)
    radius: float                            # proxy px
    source: str                              # 'mean' | 'std' | 'sharpness' | 'frame'
    bbox_int: tuple[int, int, int, int]
    dyn: np.ndarray                          # cleaned dynamic mask
    other_components: list[tuple[int, int, int, int, int]]   # (x0, y0, x1, y1, area) of other dynamic parts
    evidence: dict
    static_video: np.ndarray | None = None   # bool [h, w]: static picture content taken into the box (video)


def _trim_bbox(dyn: np.ndarray, bb: tuple[int, int, int, int], thr: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bb
    for _ in range(2):
        sub = dyn[y0:y1, x0:x1]
        if sub.size == 0:
            break
        colf = sub.mean(axis=0)
        rowf = sub.mean(axis=1)

        def longest(fr: np.ndarray) -> tuple[int, int] | None:
            best = None
            for a, b, v in _runs(fr >= thr):
                if v and (best is None or b - a > best[1] - best[0]):
                    best = (a, b)
            return best
        c = longest(colf)
        r = longest(rowf)
        if c is None or r is None:
            break
        nx0, nx1, ny0, ny1 = x0 + c[0], x0 + c[1], y0 + r[0], y0 + r[1]
        if (nx0, ny0, nx1, ny1) == (x0, y0, x1, y1):
            break
        x0, y0, x1, y1 = nx0, ny0, nx1, ny1
    return x0, y0, x1, y1


def _contrast_region(M: np.ndarray) -> tuple[tuple[int, int, int, int], float, np.ndarray] | None:
    """Largest Otsu region of a positive map (log-scaled) covering 5-90 % of the frame: (bbox,
    median(M outside) / median(M inside), region mask) or None."""
    import cv2
    h, w = M.shape
    lm = np.log1p(np.maximum(M, 0).astype(np.float32))
    top = float(np.percentile(lm, 99.5))
    if top <= 1e-6:
        return None
    m8 = np.clip(lm * (255.0 / top), 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(m8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = _morph(_morph(m8 > t, "open", 3), "close", 9)
    n, lab, st, _ = _cc(mask)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(st[1:, 4]))
    x, y, bw, bh, a = (int(v) for v in st[i])
    if a < 0.05 * h * w or bw * bh >= 0.9 * h * w:
        return None
    rect = np.zeros((h, w), bool)
    rect[y:y + bh, x:x + bw] = True
    outer = ~_dilate(rect, 3)
    if outer.sum() < 0.05 * h * w:
        return None
    ratio = float(np.median(M[outer])) / max(1e-6, float(np.median(M[y:y + bh, x:x + bw])))
    return (x, y, x + bw, y + bh), round(ratio, 4), lab == i


STATIC_VIDEO_CONTRAST = 12.0     # |mean - canvas| of a pixel that is not the canvas
STATIC_VIDEO_FILL = 0.96          # the grown region must fill its bounding box (a rounded rectangle)
STATIC_VIDEO_TEXTURE = 0.5        # fraction of its static part with fine detail (a picture, not a flat panel /
                                  # a smooth drop shadow): |mean - GaussianBlur(mean, 1.5)| > 1.5


def _static_video_region(st: _Stats, dyn: np.ndarray, tb: tuple[int, int, int, int], thr: float,
                         cfg: Any) -> tuple[tuple[int, int, int, int], np.ndarray, float, dict] | None:
    """Static picture content around the moving subject (a locked-off camera: talking head, podcast).

    On a uniform canvas (the frame border ring is >= 80 % one level), the non-canvas region that holds the
    dynamic component is the video box when it is a clean rounded rectangle (fills >= STATIC_VIDEO_FILL of
    its bounding box, holes filled) larger than the dynamic bbox, and its static part is TEXTURED like a
    picture (fine detail: a flat title panel or bar attached to the video, or a smooth drop shadow around
    it, is not taken). Returns (bbox, region mask,
    canvas level, evidence) or None. Cheap first guess only: :func:`refine_box_from_raw` verifies the box
    against RAW (and also catches flat static strips such as coloured letterbox bars)."""
    import cv2
    from scipy.ndimage import binary_fill_holes
    h, w = st.mean.shape
    x0, y0, x1, y1 = tb
    bw_ = max(2, int(round(0.01 * max(h, w))))
    ring = np.zeros((h, w), bool)
    ring[:bw_], ring[-bw_:], ring[:, :bw_], ring[:, -bw_:] = True, True, True, True
    vals = st.mean[ring & (st.std < thr)]
    if vals.size < 0.5 * ring.sum():
        return None
    tol = float(_p(cfg, "SOLID_TOL", SOLID_TOL))
    m0 = float(np.argmax(np.bincount(np.clip(np.round(vals), 0, 255).astype(int), minlength=256)))
    canvas = float(np.median(vals[np.abs(vals - m0) <= tol]))
    if float((np.abs(vals - canvas) <= tol).mean()) < 0.8:
        return None
    nonbg = _morph((np.abs(st.mean - canvas) > float(_p(cfg, "STATIC_VIDEO_CONTRAST", STATIC_VIDEO_CONTRAST))) | dyn,
                   "close", 3)
    n, lab, sts, _ = _cc(nonbg)
    if n <= 1:
        return None
    inner = lab[y0:y1, x0:x1][dyn[y0:y1, x0:x1]]
    inner = inner[inner > 0]
    if inner.size == 0:
        return None
    li = int(np.argmax(np.bincount(inner, minlength=n)))
    cx, cy, cw, ch = (int(v) for v in sts[li, :4])
    bb = (cx, cy, cx + cw, cy + ch)
    ev: dict = {"canvas_level": round(canvas, 2), "region_bbox": list(bb), "dynamic_bbox": list(tb)}
    if max(abs(bb[0] - x0), abs(bb[1] - y0), abs(bb[2] - x1), abs(bb[3] - y1)) <= 2:
        return None
    if cw * ch > 0.9 * h * w or not (bb[0] <= x0 + 2 and bb[1] <= y0 + 2 and bb[2] >= x1 - 2 and bb[3] >= y1 - 2):
        return None
    region = np.zeros((h, w), bool)
    region[cy:cy + ch, cx:cx + cw] = binary_fill_holes(lab[cy:cy + ch, cx:cx + cw] == li)
    fill = float(region[cy:cy + ch, cx:cx + cw].mean())
    ev["fill"] = round(fill, 4)
    if fill < float(_p(cfg, "STATIC_VIDEO_FILL", STATIC_VIDEO_FILL)):
        return None
    rect = np.zeros((h, w), bool)
    rect[y0:y1, x0:x1] = True
    part = region & ~_dilate(rect, 4) & (st.std < thr)
    part &= _erode_mask(region, 3)                       # not the (blurred) rim of the region
    if int(part.sum()) < 50:
        return None
    m32 = st.mean.astype(np.float32)
    detail = np.abs(m32 - cv2.GaussianBlur(m32, (0, 0), 1.5))
    tex = float((detail[part] > 1.5).mean())
    ev["texture_fraction"] = round(tex, 4)
    if tex < float(_p(cfg, "STATIC_VIDEO_TEXTURE", STATIC_VIDEO_TEXTURE)):
        return None
    nb = _trim_bbox(region, bb, 0.5)
    ev["bbox"] = list(nb)
    return nb, region, canvas, ev


def _detect_box(comp: Proxy, st: _Stats, cfg: Any, dlog: DecisionLog) -> _BoxResult:
    h, w = st.mean.shape
    thr = float(_cfg(cfg, "static_std_thresh", 2.0))
    dyn = _morph(_morph(st.std >= thr, "open", 3), "close", 5)
    n, lab, stats, _ = _cc(dyn)
    full = _BoxResult("fullscreen", (0.0, 0.0, float(w), float(h)), 0.0, "frame", (0, 0, w, h), dyn, [], {})
    if n <= 1:
        dlog.record("layout", "box", kind="fullscreen", reason="no dynamic pixels")
        full.evidence = {"reason": "no dynamic pixels"}
        return full
    order = 1 + np.argsort(-stats[1:, 4], kind="stable")
    main = int(order[0])
    x, y, bw, bh, area = (int(v) for v in stats[main])
    bb = (x, y, x + bw, y + bh)
    others = [(int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 0] + stats[i, 2]), int(stats[i, 1] + stats[i, 3]),
               int(stats[i, 4])) for i in order[1:] if stats[i, 4] >= 12]
    source = "mean"
    img = st.mean
    ev: dict = {"component_bbox": list(bb), "component_area": area}
    if bw * bh >= 0.97 * w * h:
        # the whole frame moves: fullscreen video, or a box over a moving (blurred) background. The box is
        # the region clearly more active (temporal std; preferred: its profile is ~linear in the pixel
        # coverage, so edges stay sub-pixel) or else sharper (high-frequency energy, whose response to the
        # box's own edge spreads a few px outwards) than its surroundings.
        used = np.flatnonzero(st.used)
        ks = used[np.unique(np.round(np.linspace(0, len(used) - 1, min(len(used), 48))).astype(int))]
        cands = []
        for name, M in (("std", st.std), ("sharpness", _sharpness(comp, ks))):
            r = _contrast_region(M)
            ev[f"{name}_region"] = r[:2] if r is not None else None
            if r is not None and r[1] < (0.5 if name == "std" else 0.4):
                cands.append((r[1], name, r[0], r[2], M))
        if not cands:
            dlog.record("layout", "box", kind="fullscreen", evidence=ev)
            full.evidence = ev
            return full
        _ratio, source, bb, dyn, img = cands[0]
        others = []
    frac_thr = float(_cfg(cfg, "dynamic_frac_thresh", 0.5))
    tb = _trim_bbox(dyn, bb, frac_thr)
    ev["trimmed_bbox"] = list(tb)
    grown = _static_video_region(st, dyn, tb, thr, cfg) if source == "mean" else None
    if grown is not None:
        # static picture content around the moving subject is part of the video box (its edges are measured
        # on the mean image only: the static region has no temporal std)
        tb, region, out_level, gev = grown
        ev["static_video_region"] = gev
        dlog.record("layout", "box_static_region", evidence=gev)
    x0, y0, x1, y1 = tb
    # sub-pixel edges on the mean image (contrast check), std image as fallback
    # pixels of static zones (text, logos) are not part of the edge / corner profiles
    if grown is None:
        out_level = float(np.median(st.mean[~dyn])) if (~dyn).any() else 0.0
    valid = ~((st.std < thr) & (np.abs(st.mean - out_level) > 8.0))
    if grown is not None:
        valid |= region
    bands = {"left": (y0 + (y1 - y0) // 4, y1 - (y1 - y0) // 4), "right": (y0 + (y1 - y0) // 4, y1 - (y1 - y0) // 4),
             "top": (x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4), "bottom": (x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4)}
    ints = {"left": x0, "right": x1, "top": y0, "bottom": y1}
    edges_ev = {}
    vals = {}
    min_c = float(_p(cfg, "EDGE_MIN_CONTRAST", EDGE_MIN_CONTRAST))
    for side in ("left", "right", "top", "bottom"):
        v, e1 = _subpixel_edge(img, side, ints[side], bands[side], valid)
        if source == "mean" and not e1.get("border") and grown is None:
            # the mean-image estimator assumes a uniform static level outside the edge; the std-image one
            # (std of c*X + (1-c)*S = c*std X) does not. Low contrast, or a disagreement (a stroke / shadow /
            # static structure right next to the edge) -> use the std estimate.
            v2, e2 = _subpixel_edge(st.std, side, ints[side], bands[side], valid)
            low = abs(e1.get("P", 0.0) - e1.get("S", 0.0)) < min_c
            if low or abs(v - v2) > 0.75:
                e2["rejected_mean_estimate"] = e1
                e2["reason"] = "low contrast" if low else "static structure next to the edge"
                v, e1 = v2, e2
                e1["source"] = "std"
            else:
                e1["std_estimate"] = round(v2, 4)
        vals[side] = v
        edges_ev[side] = e1
    edges = (vals["left"], vals["top"], vals["right"], vals["bottom"])
    if not (edges[2] - edges[0] > 4 and edges[3] - edges[1] > 4):
        edges = (float(x0), float(y0), float(x1), float(y1))
        edges_ev["fallback"] = "integer bbox (edge fit degenerate)"
    ev["edges"] = edges_ev
    # corners that are not on the frame border
    corners = [nm for nm, bx, by in (("tl", "left", "top"), ("tr", "right", "top"), ("bl", "left", "bottom"),
                                     ("br", "right", "bottom"))
               if not edges_ev[bx].get("border") and not edges_ev[by].get("border")]
    std_fallback = any(isinstance(e, dict) and e.get("source") == "std" for e in edges_ev.values())
    radius, rev = _fit_radius(st.std if std_fallback else img, valid, edges, corners)
    ev["radius"] = rev
    dlog.record("layout", "box", kind="boxed", source=source, edges_proxy=[round(v, 4) for v in edges],
                radius_proxy=round(radius, 3), evidence=ev)
    return _BoxResult("boxed", edges, radius, source, tb, dyn, others, ev, region if grown is not None else None)


def _snap(v: float, tol: float) -> float:
    r = round(v)
    return float(r) if abs(v - r) <= tol else round(float(v), 3)


def _box_full(br: _BoxResult, comp: Proxy) -> tuple[Box, dict]:
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    x0, y0, x1, y1 = br.edges
    fx0, fy0, fx1, fy1 = x0 / rx, y0 / ry, x1 / rx, y1 / ry
    sx0, sy0, sx1, sy1 = (_snap(v, 0.25) for v in (fx0, fy0, fx1, fy1))
    r_full = br.radius / ((rx + ry) / 2.0)
    r_s = _snap(r_full, 0.35) if r_full > 0.5 else 0.0
    ev = {"measured_full": [round(fx0, 3), round(fy0, 3), round(fx1, 3), round(fy1, 3)],
          "radius_measured_full": round(r_full, 3)}
    return Box(sx0, sy0, sx1 - sx0, sy1 - sy0, r_s), ev


def _stroke(st: _Stats, cov: np.ndarray, bg_level: float, zones_mask: np.ndarray, thr: float) -> dict | None:
    """Static ring hugging the box (border stroke / shadow): ring profile of |mean - bg| outside the box
    (distance 1..16 proxy px). A strong first ring that drops to background within <= 12 px is a
    stroke; a slowly decaying one a shadow."""
    import cv2
    outside = cov <= 0.0
    if not outside.any():
        return None
    dist = cv2.distanceTransform(outside.astype(np.uint8), cv2.DIST_L2, 3)
    static = st.std < thr
    prof = []
    for d in range(1, 17):
        m = (dist > d - 1) & (dist <= d) & static & ~zones_mask
        if m.sum() < 20:
            prof.append(0.0)
            continue
        prof.append(float(np.median(np.abs(st.mean[m] - bg_level))))
    prof = np.asarray(prof)
    if prof[0] < 12.0:
        return None
    below = np.flatnonzero(prof < 0.2 * prof[0])
    width = int(below[0]) if below.size else len(prof)
    kind = "stroke" if width <= 12 and (below.size and prof[below[0]] < 4.0) else "shadow"
    return {"kind": kind, "width_proxy": width, "level": float(prof[0]), "profile": [round(v, 2) for v in prof]}


# ================================================================================================
# 3. Background
# ================================================================================================

def _zncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64).ravel() - a.mean()
    b = b.astype(np.float64).ravel() - b.mean()
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-9 else float("nan")


def _cover_blur(frame: np.ndarray, roi: tuple[int, int, int, int], sigma: float) -> np.ndarray:
    """The render_preview/export_ae blur model at proxy scale: blurred box content cover-scaled about
    the frame centre (float32 [h, w])."""
    import cv2
    from .geometry import CORNER_TO_CV, CV_TO_CORNER
    h, w = frame.shape[:2]
    x, y, rw, rh = roi
    C = frame[y:y + rh, x:x + rw].astype(np.float32)
    if sigma > 0:
        C = cv2.GaussianBlur(C, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
    cover = max(w / rw, h / rh)
    Mc = np.array([[cover, 0.0, w / 2.0 - cover * rw / 2.0], [0.0, cover, h / 2.0 - cover * rh / 2.0], [0, 0, 1]])
    Mcv = (CORNER_TO_CV @ Mc @ CV_TO_CORNER)[:2, :]
    return cv2.warpAffine(C, Mcv, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _classify_background(comp: Proxy, st: _Stats, br: _BoxResult, cov: np.ndarray, colour: dict | None,
                         cfg: Any, dlog: DecisionLog) -> tuple[dict, float]:
    """Returns (background dict, gray level of the background model at a typical pixel)."""
    h, w = st.mean.shape
    if br.kind == "fullscreen":
        bg = {"type": "solid", "color": "#000000", "notes": "fullscreen video: no visible background"}
        dlog.record("layout", "background", type="solid", reason="fullscreen")
        return bg, 0.0
    thr = float(_cfg(cfg, "static_std_thresh", 2.0))
    outside = ~_dilate(cov > 0, 2)
    for (x0, y0, x1, y1, _a) in br.other_components:
        outside[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2] = False
    if outside.sum() < 50:
        return {"type": "solid", "color": "#000000", "notes": "no visible background"}, 0.0
    static = st.std < thr
    static_frac = float(static[outside].mean())
    ev: dict = {"static_fraction_outside": round(static_frac, 4)}
    if static_frac >= 0.5:
        vals = st.mean[outside & static]
        hist = np.bincount(np.clip(np.round(vals), 0, 255).astype(int), minlength=256)
        tol = float(_p(cfg, "SOLID_TOL", SOLID_TOL))
        m0 = float(np.argmax(hist))
        mode = float(np.median(vals[np.abs(vals - m0) <= tol]))      # refined histogram mode
        near = np.abs(vals - mode) <= tol
        frac = float(near.mean())
        ev.update({"mode_gray": mode, "fraction_near_mode": round(frac, 4)})
        if frac >= float(_p(cfg, "SOLID_FRAC", SOLID_FRAC)):
            sel = outside & static & (np.abs(st.mean - mode) <= tol)
            if colour:
                pix = np.concatenate([c[sel] for c in colour.values()]).astype(np.float64)
                col = _hex(np.median(pix, axis=0))
            else:
                col = _gray_hex(mode)
            bg = {"type": "solid", "color": col, "gray": round(mode, 2)}
            dlog.record("layout", "background", type="solid", color=col, evidence=ev,
                        rejected=["gradient", "image", "blur"])
            return bg, mode
        # gradient: robust quadratic surface over the static outside pixels
        yy, xx = np.nonzero(outside & static)
        v = st.mean[yy, xx].astype(np.float64)
        X = np.stack([np.ones_like(v), xx / w, yy / h, (xx / w) ** 2, (yy / h) ** 2, (xx / w) * (yy / h)], axis=1)
        inl = np.ones(v.size, bool)
        coef = np.zeros(6)
        for _ in range(4):
            coef, *_ = np.linalg.lstsq(X[inl], v[inl], rcond=None)
            res = v - X @ coef
            s = 1.4826 * float(np.median(np.abs(res[inl]))) + 1e-6
            inl = np.abs(res) <= max(3 * s, 4.0)
        res_mad = 1.4826 * float(np.median(np.abs((v - X @ coef)[inl])))
        ev.update({"gradient_inliers": round(float(inl.mean()), 4), "gradient_resid_mad": round(res_mad, 3),
                   "gradient_range": round(float(np.ptp(X[inl] @ coef)) if inl.any() else 0.0, 2)})
        if inl.mean() >= 0.7 and res_mad <= 3.0 and ev["gradient_range"] > 2 * tol:
            def col_at(rows: slice) -> str:
                m = np.zeros((h, w), bool)
                m[rows] = True
                m &= outside & static
                if colour and m.any():
                    return _hex(np.median(np.concatenate([c[m] for c in colour.values()]).astype(np.float64), axis=0))
                return _gray_hex(float(np.median(st.mean[m]))) if m.any() else "#000000"
            top, bot = col_at(slice(0, max(1, h // 10))), col_at(slice(h - max(1, h // 10), h))
            bg = {"type": "gradient", "color": top, "color_top": top, "color_bottom": bot,
                  "coef_gray": [round(float(c), 4) for c in coef]}
            dlog.record("layout", "background", type="gradient", evidence=ev, rejected=["solid", "image", "blur"])
            return bg, float(np.median(v))
        med = float(np.median(v))
        col = (_hex(np.median(np.concatenate([c[outside & static] for c in colour.values()]).astype(np.float64), axis=0))
               if colour else _gray_hex(med))
        bg = {"type": "image", "color": col, "notes": "static image behind the box (recreated as a solid; "
                                                     "add your own image)"}
        dlog.record("layout", "background", type="image", evidence=ev, rejected=["solid", "gradient", "blur"])
        return bg, med
    # dynamic background: blurred copy of the box content?
    used = np.flatnonzero(st.used)
    ks = used[np.unique(np.round(np.linspace(0, len(used) - 1, min(len(used), 12))).astype(int))]
    x0, y0, x1, y1 = br.edges
    roi = (int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)) - int(math.floor(x0)),
           int(math.ceil(y1)) - int(math.floor(y0)))
    frames = {int(k): np.asarray(comp.get(int(k))).astype(np.float32) for k in ks}
    sel = outside & ~_dilate(cov > 0, 4)
    best = (-2.0, 0.0, 1.0, 0.0)
    for sig in (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0):
        zs, gains = [], []
        for f in frames.values():
            model = _cover_blur(f, roi, sig)
            a, b = model[sel], f[sel]
            zs.append(_zncc(a, b))
            A = np.stack([a, np.ones_like(a)], axis=1)
            gains.append(np.linalg.lstsq(A.astype(np.float64), b.astype(np.float64), rcond=None)[0])
        z = float(np.nanmedian(zs))
        if z > best[0]:
            g = np.median(np.asarray(gains), axis=0)
            best = (z, sig, float(g[0]), float(g[1]))
    ev.update({"blur_zncc": round(best[0], 4), "blur_sigma_proxy": best[1], "gain": round(best[2], 4),
               "offset": round(best[3], 3)})
    rx = (float(comp.ratio[0]) + float(comp.ratio[1])) / 2.0
    colv = float(np.median(st.mean[outside]))
    if best[0] >= float(_p(cfg, "BLUR_MIN_ZNCC", BLUR_MIN_ZNCC)):
        sigma_full = best[1] / rx
        bg = {"type": "blur", "color": "#000000", "sigma": round(sigma_full, 3), "blurriness": round(3 * sigma_full, 3),
              "gain": round(best[2], 4), "offset": round(best[3], 3), "zncc": round(best[0], 4),
              "notes": "sigma in Video-Box pre-comp px (blur applied before the cover scale)"}
        dlog.record("layout", "background", type="blur", evidence=ev, rejected=["solid", "gradient", "image"])
        return bg, colv
    bg = {"type": "dynamic", "color": _gray_hex(colv),
          "notes": "moving background that is not a blurred copy of the box (recreated as a solid)"}
    dlog.record("layout", "background", type="dynamic", evidence=ev)
    return bg, colv


# ================================================================================================
# 4. Static zones
# ================================================================================================

def _union_groups(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for _, g in sorted(groups.items())]


def _line_groups(boxes: np.ndarray, gap_k: float, max_ratio: float, align: float | None) -> np.ndarray:
    """Group id per glyph box: glyphs join a line when their vertical overlap is >= half the smaller
    height and the horizontal gap <= gap_k x the larger height; glyphs whose heights differ by more than
    ``max_ratio`` join only when they overlap horizontally (a dot / accent over its letter). ``align``:
    additionally require a shared baseline or top line (|delta| <= align x the larger height) — typeset
    text has one, random texture blobs touching a caption do not. Sparse connected components."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = len(boxes)
    b = np.asarray(boxes, np.float64)
    hgt = b[:, 3] - b[:, 1]
    ov = np.minimum(b[:, None, 3], b[None, :, 3]) - np.maximum(b[:, None, 1], b[None, :, 1])
    hmin = np.minimum(hgt[:, None], hgt[None, :])
    hmax = np.maximum(hgt[:, None], hgt[None, :])
    gap = np.maximum(b[:, None, 0], b[None, :, 0]) - np.minimum(b[:, None, 2], b[None, :, 2])
    ok = (ov >= 0.5 * hmin) & (gap <= gap_k * hmax)
    ok &= (hmax <= max_ratio * np.maximum(hmin, 1.0)) | (gap <= 0)
    if align is not None:
        tol = align * hmax
        ok &= (np.abs(b[:, None, 3] - b[None, :, 3]) <= tol) | (np.abs(b[:, None, 1] - b[None, :, 1]) <= tol)
    ii, jj = np.nonzero(ok)
    _, gid = connected_components(coo_matrix((np.ones(ii.size, np.int8), (ii, jj)), shape=(n, n)), directed=False)
    return gid.astype(np.int64)


def _group_lines(boxes: np.ndarray, gap_k: float = 0.9, max_ratio: float = 2.5,
                 align: float | None = None) -> list[list[int]]:
    """Group glyph bboxes [(x0, y0, x1, y1)] into text lines (lists of indices, see :func:`_line_groups`)."""
    if len(boxes) == 0:
        return []
    gid = _line_groups(np.asarray(boxes), gap_k, max_ratio, align)
    groups: dict[int, list[int]] = {}
    for i, g in enumerate(gid.tolist()):
        groups.setdefault(g, []).append(i)
    return [groups[g] for g in sorted(groups, key=lambda g: groups[g][0])]


def _bbox_union(boxes: np.ndarray, idx: Sequence[int]) -> tuple[int, int, int, int]:
    b = boxes[list(idx)]
    return int(b[:, 0].min()), int(b[:, 1].min()), int(b[:, 2].max()), int(b[:, 3].max())


def _colour_canvas(colour: dict | None) -> tuple[np.ndarray, np.ndarray] | None:
    """(median BGR image of the decoded colour frames, per-pixel 5x5 max of max(B,G,R))."""
    if not colour:
        return None
    import cv2
    img = np.median(np.stack(list(colour.values())), axis=0).astype(np.uint8)
    v = img.max(axis=2).astype(np.float32)
    return img, cv2.dilate(v, np.ones((5, 5), np.uint8))


def _zone_colours(canvas: tuple[np.ndarray, np.ndarray] | None, mask: np.ndarray) -> list[tuple[str, float]]:
    """Dominant colours of the zone pixels (core pixels only; AA edges excluded)."""
    if canvas is None or not mask.any():
        return []
    img, vmax = canvas
    v = img.max(axis=2).astype(np.float32)
    core = mask & (v >= 0.8 * vmax) & (v > 40)
    pix = img[core].astype(np.int32)
    if pix.shape[0] < 5:
        return []
    q = pix // 32
    keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    uniq, inv, cnt = np.unique(keys, return_inverse=True, return_counts=True)
    order = np.argsort(-cnt, kind="stable")
    out = []
    for i in order:
        f = cnt[i] / pix.shape[0]
        if f < 0.08:
            break
        out.append((_hex(np.median(pix[inv == i], axis=0)), round(float(f), 3)))
    return out[:5]


def _multicolour(hexes: Sequence[str]) -> bool:
    """>= 2 clearly different colours (RGB distance > 100) of which at least one is saturated."""
    rgb = [np.array([int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)], np.float64) for c in hexes]
    sat = [float(v.max() - v.min()) > 80 for v in rgb]
    for i in range(len(rgb)):
        for j in range(i + 1, len(rgb)):
            if np.linalg.norm(rgb[i] - rgb[j]) > 100 and (sat[i] or sat[j]):
                return True
    return False


def _detect_zones(st: _Stats, br: _BoxResult, cov: np.ndarray, bg: dict, bg_level: float, colour: dict | None,
                  comp: Proxy, cfg: Any, dlog: DecisionLog, ring_px: int = 0,
                  raw_match: np.ndarray | None = None) -> tuple[list[Zone], np.ndarray]:
    """Static zones (logo / channel_name / title / header / watermark / other). Returns (zones, zone mask).

    ``raw_match``: pixels whose content is known to match RAW (box measured against RAW): static RAW
    content inside the box (a locked-off background, letterbox bars) is video, not an overlay zone."""
    import cv2
    h, w = st.mean.shape
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    thr = float(_cfg(cfg, "static_std_thresh", 2.0))
    static = st.std < thr
    btype = bg.get("type")
    if btype == "gradient" and "coef_gray" in bg:
        yy, xx = np.mgrid[0:h, 0:w]
        c = bg["coef_gray"]
        model = (c[0] + c[1] * xx / w + c[2] * yy / h + c[3] * (xx / w) ** 2 + c[4] * (yy / h) ** 2
                 + c[5] * (xx / w) * (yy / h))
    elif btype in ("dynamic", "blur"):
        k = max(3, int(round(0.06 * w)) | 1)
        model = cv2.medianBlur(np.clip(st.mean, 0, 255).astype(np.uint8), min(k, 255)).astype(np.float32)
    else:
        model = np.full((h, w), bg_level, np.float32)
    zc = float(_p(cfg, "ZONE_CONTRAST", ZONE_CONTRAST))
    inside = cov >= 0.99
    near_box = _dilate(cov > 0, 1 + int(ring_px))          # the box edge and its stroke / shadow ring
    cand = static & (np.abs(st.mean - model) > zc) & ~near_box
    in_cand = static & inside & (np.abs(st.mean - model) > zc)
    if raw_match is not None and np.shape(raw_match) == (h, w):
        in_cand &= ~_dilate(np.asarray(raw_match, bool), 4)
        # the box rim (a few px the RAW comparison cannot reach) belongs to the video too
        in_cand &= ~(inside & ~_erode_mask(inside, 4))
    cand |= in_cand
    zmask = np.zeros((h, w), bool)
    if br.kind == "boxed":
        bx0, by0, bx1, by1 = br.edges
    else:
        bx0, by0, bx1, by1 = 0.0, 0.0, float(w), float(h)
    canvas = _colour_canvas(colour)
    n, lab, sts, _ = _cc(cand)
    # static specks inside the box (static RAW texture, compression) are not overlays: an in-box static
    # component must be at least 0.02 % of the frame
    min_in = max(12, int(2e-4 * h * w))
    comps = []
    for i in range(1, n):
        x, y, cw, ch, a = (int(v) for v in sts[i])
        if a < 4:
            continue
        if br.kind == "boxed" and inside[y + ch // 2, x + cw // 2] and a < min_in:
            continue
        comps.append((i, x, y, x + cw, y + ch, a))
    if not comps:
        dlog.record("layout", "zones", count=0)
        return [], zmask
    boxes = np.array([[c[1], c[2], c[3], c[4]] for c in comps], np.int64)
    areas = np.array([c[5] for c in comps], np.int64)
    hs = boxes[:, 3] - boxes[:, 1]
    ws = boxes[:, 2] - boxes[:, 0]
    # logos: compact solid blobs clearly taller than neighbouring text
    logo_idx = []
    for i in range(len(comps)):
        fill = areas[i] / max(1, ws[i] * hs[i])
        asp = ws[i] / max(1, hs[i])
        size = max(ws[i], hs[i])
        if fill >= 0.6 and 0.7 <= asp <= 1.4 and 0.03 * w <= size <= 0.25 * w:
            neigh = [j for j in range(len(comps)) if j != i
                     and min(boxes[i, 3], boxes[j, 3]) - max(boxes[i, 1], boxes[j, 1]) > 0.3 * min(hs[i], hs[j])
                     and max(boxes[i, 0], boxes[j, 0]) - min(boxes[i, 2], boxes[j, 2]) <= 0.3 * hs[i]]
            if all(hs[j] * 1.5 <= hs[i] for j in neigh):
                logo_idx.append(i)
    rest = [i for i in range(len(comps)) if i not in logo_idx]
    lines = [[rest[j] for j in g] for g in _group_lines(boxes[rest])] if rest else []
    line_boxes = [_bbox_union(boxes, g) for g in lines]
    # blocks: vertically adjacent lines that overlap horizontally
    pairs = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            a, b = line_boxes[i], line_boxes[j]
            hmax = max(a[3] - a[1], b[3] - b[1])
            vgap = max(a[1], b[1]) - min(a[3], b[3])
            hov = min(a[2], b[2]) - max(a[0], b[0])
            if vgap <= 1.0 * hmax and hov >= 0.3 * min(a[2] - a[0], b[2] - b[0]):
                pairs.append((i, j))
    blocks = [[k for i in g for k in lines[i]] for g in _union_groups(len(lines), pairs)]
    min_block = max(12, int(1e-4 * h * w))                   # specks are not zones
    blocks = [b for b in blocks if int(areas[list(b)].sum()) >= min_block]
    zones: list[Zone] = []

    def add(ztype: str, idx: Sequence[int], extra_notes: str = "") -> None:
        x0, y0, x1, y1 = _bbox_union(boxes, idx)
        m = np.isin(lab[y0:y1, x0:x1], [comps[i][0] for i in idx])
        zm = np.zeros((h, w), bool)
        zm[y0:y1, x0:x1] = m
        zmask[y0:y1, x0:x1] |= m
        cols = _zone_colours(canvas, zm)
        notes = extra_notes
        if cols:
            notes = (notes + "; " if notes else "") + "colours " + ", ".join(f"{c} {int(100 * f)}%" for c, f in cols)
            if ztype in ("title", "header", "channel_name", "other") and _multicolour([c for c, _f in cols]):
                notes = "multicolour; " + notes
        zones.append(Zone(ztype, x0 / rx, y0 / ry, (x1 - x0) / rx, (y1 - y0) / ry, None, None, True, "", notes))
    for i in logo_idx:
        b = boxes[i]
        where = "inside box" if b[0] >= bx0 and b[2] <= bx1 and b[1] >= by0 and b[3] <= by1 else "canvas"
        add("logo", [i], f"compact blob ({where})")
    logo_boxes = [boxes[i] for i in logo_idx]
    above, other_blocks = [], []
    for blk in blocks:
        x0, y0, x1, y1 = _bbox_union(boxes, blk)
        cy = (y0 + y1) / 2.0
        in_box = x0 >= bx0 - 1 and x1 <= bx1 + 1 and y0 >= by0 - 1 and y1 <= by1 + 1 and br.kind == "boxed"
        if in_box:
            add("watermark", blk, "static overlay over the video")
            continue
        chan = False
        for lb in logo_boxes:
            lh = lb[3] - lb[1]
            if lb[1] - 0.25 * lh <= cy <= lb[3] + 0.25 * lh and (
                    0 <= x0 - lb[2] <= 2.0 * lh or 0 <= lb[0] - x1 <= 2.0 * lh):
                chan = True
        if chan:
            add("channel_name", blk, "text next to the logo")
        elif br.kind == "boxed" and y1 <= by0 + 1:
            above.append(blk)
        elif br.kind == "boxed" and y0 >= by1 - 1:
            add("watermark" if (y1 - y0) <= 0.05 * h else "other", blk, "below the box")
        else:
            other_blocks.append(blk)
    if above:
        areas_b = [int(areas[list(b)].sum()) for b in above]
        ti = int(np.argmax(areas_b))
        ty0 = _bbox_union(boxes, above[ti])[1]
        for i, blk in enumerate(above):
            if i == ti:
                add("title", blk, "text block above the box")
            else:
                add("header" if _bbox_union(boxes, blk)[3] <= ty0 else "other", blk, "text above the box")
    for blk in other_blocks:
        add("other", blk, "static element")
    zones.sort(key=lambda z: (z.y, z.x))
    dlog.record("layout", "zones", count=len(zones),
                zones=[{"type": z.type, "x": round(z.x, 1), "y": round(z.y, 1), "w": round(z.w, 1), "h": round(z.h, 1),
                        "notes": z.notes} for z in zones])
    return zones, zmask


# ================================================================================================
# 5. Dynamic text overlays (captions / stickers)
# ================================================================================================

@dataclass
class _Line:
    x0: int
    y0: int
    x1: int
    y1: int
    mask: np.ndarray            # bool crop [y1-y0, x1-x0]: glyph fill pixels
    n_glyphs: int               # glyphs of text height (dots / accents excluded)
    glyph_h: float              # median glyph height
    texty: bool                 # >= 2 glyphs of consistent height on a common baseline (a word / line)
    ring: float                 # mean dark fraction of the glyphs' 1-px rings


def _text_lines(f: np.ndarray, excl: np.ndarray, min_h: int, max_h: int, max_w: int, cfg: Any,
                singles: bool = False) -> list[_Line]:
    """Outlined bright text lines in one (ROI) frame.

    Glyphs = bright components (>= TEXT_WHITE) whose 1-px ring is mostly dark (<= TEXT_DARK within a
    3x3 neighbourhood), grouped into lines (similar heights, shared baseline or top line, small gaps).
    Returned lines are ``texty``: >= 2 glyphs of text height whose heights agree (robust CV <= 0.25)
    and whose bottoms (or tops) are aligned within max(1 px, 0.12 h) — random texture blobs (e.g.
    Game-of-Life still lifes) fail these. With ``singles=True`` lone glyphs of text height are returned
    instead (candidates for one-letter caption words, searched only inside the caption band)."""
    import cv2
    white_t = int(_p(cfg, "TEXT_WHITE", TEXT_WHITE))
    dark_t = int(_p(cfg, "TEXT_DARK", TEXT_DARK))
    ring_t = float(_p(cfg, "TEXT_RING_FRAC", TEXT_RING_FRAC))
    white = (f >= white_t) & ~excl
    if not white.any():
        return []
    n, lab, st, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), connectivity=8)
    if n <= 1 or n > 65535:
        return []
    area = st[:, cv2.CC_STAT_AREA]
    gw, gh = st[:, cv2.CC_STAT_WIDTH], st[:, cv2.CC_STAT_HEIGHT]
    # glyphs of text height only (a line needs >= 1 glyph of min_h; its other main glyphs are >= 0.55 of
    # it; dots / accents are left to the mask dilation)
    pre = (area >= 3) & (gh >= max(2, int(0.55 * min_h))) & (gh <= max_h) & (gw <= max_w)
    pre[0] = False
    if not pre.any():
        return []
    lab16 = np.where(pre[lab], lab, 0).astype(np.uint16)
    labd = cv2.dilate(lab16, np.ones((3, 3), np.uint8))
    ring = (labd > 0) & ~white
    rl = labd[ring]
    cnt = np.bincount(rl, minlength=n)
    # a ring pixel counts as outline when its 3x3 neighbourhood reaches the dark level: at small proxy
    # scales the 1-px ring is often the anti-aliased transition and the outline is one pixel further
    fmin = cv2.erode(f, np.ones((3, 3), np.uint8))
    cd = np.bincount(rl[fmin[ring] <= dark_t], minlength=n)
    frac = cd / np.maximum(cnt, 1)
    idx = np.flatnonzero(pre & (frac >= ring_t))
    if idx.size == 0:
        return []
    boxes = np.stack([st[idx, 0], st[idx, 1], st[idx, 0] + gw[idx], st[idx, 1] + gh[idx]], axis=1).astype(np.int64)
    hts = gh[idx].astype(np.float64)
    gid = _line_groups(boxes, 0.45, 1.8, 0.2)
    ng = int(gid.max()) + 1
    big = np.zeros(ng)
    np.maximum.at(big, gid, hts)
    main = hts >= 0.55 * big[gid]                   # text-height glyphs (dots, commas, accents excluded)
    nmain = np.bincount(gid, weights=main.astype(np.float64), minlength=ng).astype(int)
    want = (big >= min_h) & ((nmain == 1) if singles else (nmain >= 2))
    if not want.any():
        return []
    order = np.argsort(gid, kind="stable")
    starts = np.searchsorted(gid[order], np.arange(ng))
    ends = np.searchsorted(gid[order], np.arange(ng), side="right")
    lut = np.zeros(n, bool)
    lines = []
    for g in np.flatnonzero(want):
        mem = order[starts[g]:ends[g]]
        labs = idx[mem]
        mm = main[mem]
        hm = hts[mem][mm]
        med_h = _med(hm)
        bb = boxes[mem]
        x0, y0, x1, y1 = int(bb[:, 0].min()), int(bb[:, 1].min()), int(bb[:, 2].max()), int(bb[:, 3].max())
        ring_m = float(frac[labs][mm].mean())
        if singles:
            asp = (x1 - x0) / max(1, y1 - y0)
            fill = float(area[labs].sum()) / max(1, (x1 - x0) * (y1 - y0))
            if fill > 0.85 and 0.6 <= asp <= 1.6:       # a lone solid square blob is texture, not a letter
                continue
            texty = False
        else:
            if med_h < min_h or ring_m < ring_t:
                continue
            b = bb[mm]
            cv_h = 1.4826 * _med(np.abs(hm - med_h)) / med_h
            tol = max(1.0, 0.12 * med_h)
            bot = _med(np.abs(b[:, 3] - _med(b[:, 3])))
            top = _med(np.abs(b[:, 1] - _med(b[:, 1])))
            if not (cv_h <= 0.25 and min(bot, top) <= tol):
                continue                                 # irregular blobs: texture, not a line of text
            texty = True
        lut[labs] = True
        m = lut[lab[y0:y1, x0:x1]]
        lut[labs] = False
        lines.append(_Line(x0, y0, x1, y1, m, int(mm.sum()), med_h, texty, ring_m))
    return lines


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class _Track:
    """A text line followed over time. ``acc`` counts, per pixel of the track bbox, the frames in which
    it was a glyph pixel; the CORE (pixels in more than half of the frames) is what must persist."""

    def __init__(self, k: int, line: _Line):
        self.frames: list[int] = [k]
        self.lines: list[_Line] = [line]
        self.x0, self.y0, self.x1, self.y1 = line.x0, line.y0, line.x1, line.y1
        self.acc = line.mask.astype(np.float32)

    @property
    def last(self) -> int:
        return self.frames[-1]

    def add(self, k: int, line: _Line) -> None:
        x0, y0 = min(self.x0, line.x0), min(self.y0, line.y0)
        x1, y1 = max(self.x1, line.x1), max(self.y1, line.y1)
        if (x0, y0, x1, y1) != (self.x0, self.y0, self.x1, self.y1):
            acc = np.zeros((y1 - y0, x1 - x0), np.float32)
            acc[self.y0 - y0:self.y1 - y0, self.x0 - x0:self.x1 - x0] = self.acc
            self.acc, self.x0, self.y0, self.x1, self.y1 = acc, x0, y0, x1, y1
        self.acc[line.y0 - self.y0:line.y1 - self.y0, line.x0 - self.x0:line.x1 - self.x0] += line.mask
        self.frames.append(k)
        self.lines.append(line)
        self._core = None

    _core: tuple | None = None

    def core(self) -> tuple[int, int, np.ndarray, tuple[int, int, int, int]]:
        """(x0, y0, bool mask, core bbox) of the pixels present in more than half of the frames (cached)."""
        if self._core is None:
            core = self.acc > 0.5 * len(self.frames)
            if core.any():
                rows, cols = np.flatnonzero(core.any(axis=1)), np.flatnonzero(core.any(axis=0))
                bb = (self.x0 + int(cols[0]), self.y0 + int(rows[0]), self.x0 + int(cols[-1]) + 1,
                      self.y0 + int(rows[-1]) + 1)
            else:
                bb = (0, 0, 0, 0)
            self._core = (self.x0, self.y0, core, bb)
        return self._core


def _link(fa: np.ndarray, fb: np.ndarray, T: _Track, white_t: int, same_t: float) -> float | None:
    """Pixel test of a line (frame fa) continuing track ``T`` (last seen in frame fb), after the bbox
    prefilter of :func:`_link_candidates`: every CORE glyph pixel of the track must persist (>= 97 %
    still bright in fa, mean |fa - fb| <= same_t). Glyphs may be ADDED (texture touching the outline,
    word-by-word accumulation) but none of the core may change: a RAW burned-in frame counter changes a
    digit every frame and never links for long. Returns the mean |diff| (link cost) or None."""
    x0, y0, core, _bb = T.core()
    hh, ww = core.shape
    ca = fa[y0:y0 + hh, x0:x0 + ww][core].astype(np.int16)
    if ca.size == 0 or float((ca >= white_t - 40).mean()) < 0.97:
        return None
    cb = fb[y0:y0 + hh, x0:x0 + ww][core].astype(np.int16)
    mad = float(np.abs(ca - cb).mean())
    return mad if mad <= same_t else None


def _link_candidates(lines: list[_Line], tracks: list[_Track], k: int) -> list[tuple[int, int]]:
    """(line, track) pairs whose bboxes are compatible: the track's core bbox lies >= 80 % inside the
    line bbox and the line is at most 4x larger (vectorised)."""
    act = [ti for ti, T in enumerate(tracks) if k - T.last <= 2]
    if not lines or not act:
        return []
    lb = np.array([[L.x0, L.y0, L.x1, L.y1] for L in lines], np.int64)
    tb = np.array([tracks[ti].core()[3] for ti in act], np.int64)
    iw = np.minimum(lb[:, None, 2], tb[None, :, 2]) - np.maximum(lb[:, None, 0], tb[None, :, 0])
    ih = np.minimum(lb[:, None, 3], tb[None, :, 3]) - np.maximum(lb[:, None, 1], tb[None, :, 1])
    inter = np.clip(iw, 0, None) * np.clip(ih, 0, None)
    at = (tb[:, 2] - tb[:, 0]) * (tb[:, 3] - tb[:, 1])
    al = (lb[:, 2] - lb[:, 0]) * (lb[:, 3] - lb[:, 1])
    ok = (at[None, :] > 0) & (inter >= 0.8 * at[None, :]) & (al[:, None] <= 4 * at[None, :])
    li, tj = np.nonzero(ok)
    return [(int(a), act[int(b)]) for a, b in zip(li, tj)]


def _event_support(e: dict) -> bool:
    """(Re)compute an event's pixel support (fraction of its frames in which each pixel is a glyph pixel),
    consensus mask (>= 50 %), kept mask (>= 30 %: texture that touched the text now and then is
    dropped), tight bbox of the kept pixels and timing. False when nothing is kept."""
    ls = e["lines"]
    x0, y0 = min(L.x0 for L in ls), min(L.y0 for L in ls)
    x1, y1 = max(L.x1 for L in ls), max(L.y1 for L in ls)
    acc = np.zeros((y1 - y0, x1 - x0), np.float32)
    for L in ls:
        acc[L.y0 - y0:L.y1 - y0, L.x0 - x0:L.x1 - x0] += L.mask
    sup = acc / len(ls)
    keep = sup >= 0.3
    if not keep.any():
        return False
    rows, cols = np.flatnonzero(keep.any(axis=1)), np.flatnonzero(keep.any(axis=0))
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    k_in = min(int(min(e["frames"])), int(e.get("comp_in", 1 << 60)))
    k_out = max(int(max(e["frames"])) + 1, int(e.get("comp_out", -1)))      # keeps refined boundaries
    e.update({"x0": x0 + c0, "y0": y0 + r0, "x1": x0 + c1, "y1": y0 + r1, "support": sup[r0:r1, c0:c1],
              "mask": sup[r0:r1, c0:c1] >= 0.5, "comp_in": k_in, "comp_out": k_out})
    return True


def _mask_iou(a: dict, b: dict) -> float:
    """IoU of two events' consensus glyph masks (placed in their union bbox)."""
    x0, y0 = min(a["x0"], b["x0"]), min(a["y0"], b["y0"])
    x1, y1 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"])
    ma = np.zeros((y1 - y0, x1 - x0), bool)
    mb = ma.copy()
    ma[a["y0"] - y0:a["y1"] - y0, a["x0"] - x0:a["x1"] - x0] = a["mask"]
    mb[b["y0"] - y0:b["y1"] - y0, b["x0"] - x0:b["x1"] - x0] = b["mask"]
    u = int((ma | mb).sum())
    return float((ma & mb).sum()) / u if u else 0.0


def _event_glyph_change(e: dict) -> float:
    """Fraction of consecutive detections of the event in which a CONSENSUS glyph changed shape
    (|XOR| > max(3, 12 %) of the glyph within 1 px of its shape). Overlaid text keeps identical glyphs
    (texture touching it now and then is not part of the consensus); a burned-in frame counter
    changes its last digit every frame."""
    import cv2
    ls = e["lines"]
    if len(ls) < 2:
        return 0.0
    ex0, ey0, ex1, ey1 = e["x0"], e["y0"], e["x1"], e["y1"]
    n, lab, st, _ = cv2.connectedComponentsWithStats(e["mask"].astype(np.uint8), connectivity=8)
    near = cv2.dilate(lab.astype(np.uint16), np.ones((3, 3), np.uint8))     # each glyph + 1 px
    glyphs = []
    for i in range(1, n):
        if st[i, 4] < 3:
            continue
        gx0, gy0 = max(0, int(st[i, 0]) - 1), max(0, int(st[i, 1]) - 1)
        gx1, gy1 = int(st[i, 0] + st[i, 2]) + 1, int(st[i, 1] + st[i, 3]) + 1
        glyphs.append((gx0, gy0, gx1, gy1, int(st[i, 4]), near[gy0:gy1, gx0:gx1] == i))
    if not glyphs:
        return 1.0

    def placed(L: _Line) -> np.ndarray:
        m = np.zeros((ey1 - ey0, ex1 - ex0), bool)
        sy0, sx0 = max(ey0, L.y0), max(ex0, L.x0)
        sy1, sx1 = min(ey1, L.y1), min(ex1, L.x1)
        if sy1 > sy0 and sx1 > sx0:
            m[sy0 - ey0:sy1 - ey0, sx0 - ex0:sx1 - ex0] = L.mask[sy0 - L.y0:sy1 - L.y0, sx0 - L.x0:sx1 - L.x0]
        return m
    changed = 0
    prev = placed(ls[0])
    for L in ls[1:]:
        cur = placed(L)
        for gx0, gy0, gx1, gy1, ga, gm in glyphs:
            if int(((prev[gy0:gy1, gx0:gx1] ^ cur[gy0:gy1, gx0:gx1]) & gm).sum()) > max(3, 0.12 * ga):
                changed += 1
                break
        prev = cur
    return changed / (len(ls) - 1)


def _extend_event(e: dict, comp: Proxy, white_t: int, same_t: float, lo: int, hi: int) -> None:
    """Refine an event's boundaries: extend it frame by frame (backwards from comp_in, forwards from
    comp_out) while its consensus glyph pixels are still >= 97 % bright and match the event's first /
    last detected frame (mean |diff| <= same_t). Recovers frames whose own detection failed (texture
    touching the outline) — a different word replacing it stops the extension at once."""
    m = e["mask"]
    if not m.any():
        return
    y0, y1, x0, x1 = e["y0"], e["y1"], e["x0"], e["x1"]
    ref_in = np.asarray(comp.get(e["comp_in"]))[y0:y1, x0:x1][m].astype(np.int16)
    k = e["comp_in"] - 1
    while k >= lo:
        v = np.asarray(comp.get(k))[y0:y1, x0:x1][m].astype(np.int16)
        if (v >= white_t - 40).mean() < 0.97 or np.abs(v - ref_in).mean() > same_t:
            break
        e["comp_in"] = k
        k -= 1
    ref_out = np.asarray(comp.get(e["comp_out"] - 1))[y0:y1, x0:x1][m].astype(np.int16)
    k = e["comp_out"]
    while k < hi:
        v = np.asarray(comp.get(k))[y0:y1, x0:x1][m].astype(np.int16)
        if (v >= white_t - 40).mean() < 0.97 or np.abs(v - ref_out).mean() > same_t:
            break
        e["comp_out"] = k + 1
        k += 1


@dataclass
class _Detector:
    """Picklable per-frame text-line detector (runs in thread or spawn-process workers)."""
    static: np.ndarray                       # bool [h, w] static mask (excluded)
    roi: tuple[int, int, int, int]           # (x0, y0, x1, y1) searched on ordinary frames
    full_frames: np.ndarray | None           # bool [n]: frames searched on the whole frame (fullscreen)
    min_h: int
    max_h: int
    max_w: int
    singles: bool
    params: Any                              # namespace with the layout_* thresholds (see _p)

    def __call__(self, k: int, f: np.ndarray) -> list[_Line]:
        if self.full_frames is not None and self.full_frames[k]:
            rx0, ry0, rx1, ry1 = 0, 0, f.shape[1], f.shape[0]
        else:
            rx0, ry0, rx1, ry1 = self.roi
        ls = _text_lines(f[ry0:ry1, rx0:rx1], self.static[ry0:ry1, rx0:rx1], self.min_h, self.max_h, self.max_w,
                         self.params, singles=self.singles)
        for L in ls:
            L.x0 += rx0
            L.x1 += rx0
            L.y0 += ry0
            L.y1 += ry0
        return ls


def _text_params(cfg: Any) -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(**{"layout_" + nm.lower(): _p(cfg, nm, globals()[nm])
                              for nm in ("TEXT_WHITE", "TEXT_DARK", "TEXT_RING_FRAC")})


_WORKER: dict = {}


def _spawn_safe() -> bool:
    """Spawned workers re-import the parent's __main__: only safe for ``python -m pkg`` entry points, a
    guarded script (``if __name__ == "__main__":``) or no main file (interactive / -c)."""
    import sys
    m = sys.modules.get("__main__")
    f = getattr(m, "__file__", None)
    if not f:
        return True
    spec = getattr(m, "__spec__", None)
    if spec is not None and str(getattr(spec, "name", "")).endswith("__main__"):
        return True
    try:
        txt = Path(f).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "__name__" in txt and "__main__" in txt


def _spawn_init(npy_path: str, detector: _Detector) -> None:
    import cv2
    cv2.setNumThreads(1)
    _WORKER["frames"] = np.load(npy_path, mmap_mode="r")
    _WORKER["detect"] = detector


def _spawn_chunk(bounds: tuple[int, int]) -> list[list[_Line]]:
    """Spawn-pool worker: detector on frames [a, b) of the memmapped proxy."""
    fr, det = _WORKER["frames"], _WORKER["detect"]
    return [det(k, np.asarray(fr[k])) for k in range(bounds[0], bounds[1])]


def _detections(comp: Proxy, n: int, detect: _Detector, workers: int) -> Iterator[tuple[list[int], np.ndarray, list]]:
    """Yield (frame indices, frames, lines per frame) in frame order. With several workers and a dense
    proxy backed by its .npy file: a SPAWN process pool (each worker re-opens the memmap; fork is not
    used because OpenCV's thread pool does not survive it); otherwise a thread pool / plain loop."""
    chunk = 32
    bounds = [(a, min(n, a + chunk)) for a in range(0, n, chunk)]
    npy = getattr(comp, "npy_path", "") or ""
    use_proc = (workers > 1 and n >= 8 * chunk and comp.index_map is None and npy and Path(npy).is_file()
                and tuple(np.load(npy, mmap_mode="r").shape) == (n, int(comp.size[1]), int(comp.size[0]))
                and _spawn_safe())
    if use_proc:
        # hang protection (DESIGN D7): results are collected with the pool watchdog; a stalled pool or a dead
        # worker stops the pool and the remaining chunks run here (same detector, same frames -> same lines)
        import multiprocessing as mp
        from .common import PoolFailure, close_pool, pool_workers, watched_results
        pool = mp.get_context("spawn").Pool(workers, initializer=_spawn_init, initargs=(npy, detect))
        clean = False
        i = 0
        try:
            try:
                for res in watched_results(pool.imap(_spawn_chunk, bounds), len(bounds), "text lines",
                                           pool_workers(pool), what="spawn pool"):
                    a, b = bounds[i]
                    i += 1
                    yield list(range(a, b)), np.stack([np.asarray(comp.get(k)) for k in range(a, b)]), res
                clean = True
            except PoolFailure as e:
                log.warning("layout: text lines: %s - stopped the worker pool; detecting the remaining %d of %d "
                            "chunks in this process (identical results, only slower)", e, len(bounds) - i, len(bounds))
                close_pool(pool, kill=True)
                pool = None
                for a, b in bounds[i:]:
                    kl = list(range(a, b))
                    fr = np.stack([np.asarray(comp.get(k)) for k in kl])
                    yield kl, fr, [detect(k, f) for k, f in zip(kl, fr)]
        finally:
            if pool is not None:
                close_pool(pool, kill=not clean)
        return
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for a, b in bounds:
            kl = list(range(a, b))
            fr = np.stack([np.asarray(comp.get(k)) for k in kl])
            res = list(pool.map(detect, kl, list(fr))) if pool is not None else [detect(k, f) for k, f in zip(kl, fr)]
            yield kl, fr, res
    finally:
        if pool is not None:
            pool.shutdown()


def _track_text(comp: Proxy, n: int, detect: _Detector, cfg: Any, parallel: bool = True) -> list[_Track]:
    """Run ``detect(k, frame) -> list[_Line]`` on every frame (see :func:`_detections`) and link the
    lines over time (sequential, greedy by link cost). Returns every track."""
    same_t = float(_p(cfg, "TEXT_SAME_MAD", TEXT_SAME_MAD))
    white_t = int(_p(cfg, "TEXT_WHITE", TEXT_WHITE))
    workers = max(1, min(4, int(cfg.resolved_workers()) if hasattr(cfg, "resolved_workers") else 1)) if parallel else 1
    active: list[_Track] = []
    done: list[_Track] = []
    prev_frames: dict[int, np.ndarray] = {}
    for kl, fr, res in _detections(comp, n, detect, workers):
        for k, f, lines in zip(kl, fr, res):
            cands = []
            for li, ti in _link_candidates(lines, active, k):
                fp = prev_frames.get(active[ti].last)
                if fp is None:
                    continue
                cost = _link(f, fp, active[ti], white_t, same_t)
                if cost is not None:
                    cands.append((cost, li, ti))
            cands.sort()
            used_l, used_t = set(), set()
            for _cost, li, ti in cands:
                if li in used_l or ti in used_t:
                    continue
                used_l.add(li)
                used_t.add(ti)
                active[ti].add(k, lines[li])
            for li, L in enumerate(lines):
                if li not in used_l:
                    active.append(_Track(k, L))
            still = []
            for T in active:
                (still if k - T.last <= 1 else done).append(T)    # a 1-frame detection gap is bridged
            active = still
            prev_frames[k] = f
            for old in [kk for kk in prev_frames if kk < k - 2]:
                del prev_frames[old]
    return done + active


def _tracks_to_events(tracks: list[_Track], etype: str, comp: Proxy, n: int, cfg: Any) -> list[dict]:
    """Tracks -> events: >= overlay_min_frames detections, detected on >= 75 % of their span (no
    flicker), glyphs that never change shape; split pieces re-joined; boundaries refined."""
    same_t = float(_p(cfg, "TEXT_SAME_MAD", TEXT_SAME_MAD))
    white_t = int(_p(cfg, "TEXT_WHITE", TEXT_WHITE))
    min_frames = max(2, int(_cfg(cfg, "overlay_min_frames", 3)))
    events = []
    for T in tracks:
        if len(T.frames) < min_frames:
            continue
        if len(T.frames) < 0.75 * (T.frames[-1] - T.frames[0] + 1):
            continue                                    # flickers (e.g. an oscillating texture blob)
        e = {"type": etype, "frames": list(T.frames), "lines": list(T.lines),
             "glyphs": int(np.median([L.n_glyphs for L in T.lines])),
             "h": float(np.median([L.glyph_h for L in T.lines]))}
        if _event_support(e) and _event_glyph_change(e) <= 0.75:
            events.append(e)
    events.sort(key=lambda e: (e["comp_in"], e["y0"], e["x0"]))
    # re-join one text split by a short detection gap (same consensus glyphs, <= 2 frames apart)
    merged: list[dict] = []
    for e in events:
        m = None
        for p in reversed(merged[-8:]):
            if 0 <= e["comp_in"] - p["comp_out"] <= 2 and _mask_iou(p, e) >= 0.8:
                m = p
                break
        if m is None:
            merged.append(e)
            continue
        m["frames"] = m["frames"] + e["frames"]
        m["lines"] = m["lines"] + e["lines"]
        _event_support(m)
    for e in merged:
        _extend_event(e, comp, white_t, same_t, 0, n)
    return merged


def _detect_text_overlays(comp: Proxy, st: _Stats, br: _BoxResult, periods: list[LayoutPeriod], static: np.ndarray,
                          cfg: Any, dlog: DecisionLog,
                          extra_rois: Sequence[tuple[int, int, int, int]] = ()) -> list[dict]:
    """Text overlay events [{type, comp_in, comp_out, x0, y0, x1, y1 (proxy), mask, support, frames,
    lines, glyphs, h}] with type 'captions' (the caption band) or 'text' (other overlaid text).

    Pass 1: texty lines (>= 2 aligned glyphs) in the video region of every frame -> events -> caption
    band = the vertical window (one text height) holding the most event frames. Pass 2: one-letter
    words (single glyphs) searched only inside the band (its rows and horizontal extent)."""
    h, w = st.mean.shape
    n = int(comp.n)
    x0, y0, x1, y1 = br.edges
    bx0, by0 = max(0, int(math.floor(x0)) - 2), max(0, int(math.floor(y0)) - 2)
    bx1, by1 = min(w, int(math.ceil(x1)) + 2), min(h, int(math.ceil(y1)) + 2)
    box_h, box_w = by1 - by0, bx1 - bx0
    for (ax0, ay0, ax1, ay1) in extra_rois:          # animated areas outside the box (captions on the canvas)
        bx0, by0 = max(0, min(bx0, ax0 - 2)), max(0, min(by0, ay0 - 2))
        bx1, by1 = min(w, max(bx1, ax1 + 2)), min(h, max(by1, ay1 + 2))
    full_frames = np.zeros(n, bool)
    for p in periods:
        if p.mode == "fullscreen":
            full_frames[p.comp_in:p.comp_out] = True
    min_h = max(6, int(round(float(_p(cfg, "TEXT_MIN_H_FRAC", TEXT_MIN_H_FRAC)) * w)))
    min_frames = max(2, int(_cfg(cfg, "overlay_min_frames", 3)))
    # overlaid text outside the caption band must last >= 0.4 s (and >= 2 x overlay_min_frames)
    min_text = max(2 * min_frames, int(round(0.4 * float(comp.fps or 30))))

    params = _text_params(cfg)
    max_h, max_w = max(min_h, int(0.3 * box_h)), int(0.95 * box_w)
    det1 = _Detector(static, (bx0, by0, bx1, by1), full_frames if full_frames.any() else None, min_h, max_h, max_w,
                     False, params)
    texts = _tracks_to_events(_track_text(comp, n, det1, cfg), "text", comp, n, cfg)
    band = None
    if texts:
        cys = np.array([(e["y0"] + e["y1"]) / 2.0 for e in texts])
        wts = np.array([e["comp_out"] - e["comp_in"] for e in texts], np.float64)
        half = max(2.0, 0.6 * float(np.median([e["h"] for e in texts])))
        score = [(float(wts[np.abs(cys - c) <= half].sum()), -i) for i, c in enumerate(cys)]
        ci = -max(score)[1]
        sel = np.abs(cys - cys[ci]) <= half
        band = (float(np.average(cys[sel], weights=wts[sel])), half,
                float(np.median([texts[i]["h"] for i in np.flatnonzero(sel)])),
                min(texts[i]["x0"] for i in np.flatnonzero(sel)), max(texts[i]["x1"] for i in np.flatnonzero(sel)))
    out = []
    for e in texts:
        cy = (e["y0"] + e["y1"]) / 2.0
        if band is not None and abs(cy - band[0]) <= band[1] and 0.6 * band[2] <= e["h"] <= 1.6 * band[2]:
            e["type"] = "captions"
        elif e["comp_out"] - e["comp_in"] < min_text:
            continue                     # short text outside the band (RAW counter digits, texture clusters)
        out.append(e)
    n_singles = 0
    if band is not None:
        # pass 2: one-letter words of the band: same letter height, horizontally centred like the band's
        # words, and directly before / after a caption word (word-by-word captions are contiguous)
        by_c, half, bh_, bxa, bxb = band
        in_band = [e for e in out if e["type"] == "captions"]
        band_cx = float(np.median([(e["x0"] + e["x1"]) / 2.0 for e in in_band]))
        edges_t = sorted({e["comp_out"] for e in in_band} | {e["comp_in"] for e in in_band})
        ry0, ry1 = max(0, int(by_c - half - 1.5 * bh_)), min(h, int(math.ceil(by_c + half + 1.5 * bh_)))
        rx0, rx1 = max(0, int(bxa) - 2), min(w, int(bxb) + 2)
        det2 = _Detector(static, (rx0, ry0, rx1, ry1), None, min_h, max_h, max_w, True, params)
        singles = _tracks_to_events(_track_text(comp, n, det2, cfg), "captions", comp, n, cfg)
        for e in singles:
            cy, cx = (e["y0"] + e["y1"]) / 2.0, (e["x0"] + e["x1"]) / 2.0
            touches = any(abs(e["comp_in"] - t) <= 1 or abs(e["comp_out"] - t) <= 1 for t in edges_t)
            if (abs(cy - by_c) <= half and 0.75 * bh_ <= e["h"] <= 1.3 * bh_ and bxa <= cx <= bxb
                    and abs(cx - band_cx) <= max(2.0, 0.5 * bh_) and touches):
                out.append(e)
                n_singles += 1
    out.sort(key=lambda e: (e["comp_in"], e["y0"], e["x0"]))
    # pieces of one caption line found separately (same frames, same row) -> one event
    dedup: list[dict] = []
    for e in out:
        twin = None
        for p in dedup[-8:]:
            t_ov = min(p["comp_out"], e["comp_out"]) - max(p["comp_in"], e["comp_in"])
            ix = min(p["x1"], e["x1"]) - max(p["x0"], e["x0"])
            iy = min(p["y1"], e["y1"]) - max(p["y0"], e["y0"])
            small = min((p["x1"] - p["x0"]) * (p["y1"] - p["y0"]), (e["x1"] - e["x0"]) * (e["y1"] - e["y0"]))
            same_row = iy >= 0.5 * min(p["y1"] - p["y0"], e["y1"] - e["y0"]) and ix >= -2.0 * max(p["h"], e["h"])
            if (p["type"] == e["type"] and t_ov >= 0.8 * min(p["comp_out"] - p["comp_in"], e["comp_out"] - e["comp_in"])
                    and (same_row or (ix > 0 and iy > 0 and ix * iy >= 0.5 * small))):
                twin = p
                break
        if twin is None:
            dedup.append(e)
            continue
        twin["frames"] = twin["frames"] + e["frames"]
        twin["lines"] = twin["lines"] + e["lines"]
        _event_support(twin)
    out = dedup
    dlog.record("layout", "text_overlays", events=len(out),
                band=None if band is None else {"centre_y_proxy": round(band[0], 2), "half": band[1],
                                                "glyph_h_proxy": band[2], "x_range_proxy": [band[3], band[4]]},
                captions=sum(1 for e in out if e["type"] == "captions"), single_letter_words=n_singles,
                other_text=sum(1 for e in out if e["type"] == "text"),
                timing=[[e["type"], e["comp_in"], e["comp_out"], e["x0"], e["y0"], e["x1"], e["y1"]] for e in out[:300]])
    return out


def _event_masks(events: list[dict], shape: tuple[int, int], dilate_px: int) -> OverlayMasks:
    """Per-frame overlay masks of the text events: the consensus glyph mask (pixels in >= 50 % of the
    event's frames) plus the frame's own glyph pixels that have >= 30 % support (words added during the
    event), dilated by an outline margin (0.2 x glyph height, >= 2 px) so the dark outline and the
    anti-aliasing are covered. Frames inside the event where detection failed get the consensus."""
    ov = OverlayMasks(shape, dilate_px)
    h, w = shape
    for e in events:
        margin = max(2, int(round(0.2 * e["h"])))
        own: dict[int, _Line] = {}
        for k, L in zip(e["frames"], e["lines"]):
            own.setdefault(k, L)
        keep = e["support"] >= 0.3
        y0, y1 = max(0, e["y0"] - margin), min(h, e["y1"] + margin)
        x0, x1 = max(0, e["x0"] - margin), min(w, e["x1"] + margin)
        oy, ox = e["y0"] - y0, e["x0"] - x0
        eh, ew = e["y1"] - e["y0"], e["x1"] - e["x0"]
        base = np.zeros((y1 - y0, x1 - x0), bool)
        base[oy:oy + eh, ox:ox + ew] = e["mask"]
        base_d = _dilate(base, margin)
        for k in range(e["comp_in"], e["comp_out"]):
            m = base
            L = own.get(k)
            if L is not None:
                cur = np.zeros_like(base)
                # the line's mask in event-bbox coordinates, clipped to the event bbox
                ly0, lx0 = L.y0 - e["y0"], L.x0 - e["x0"]
                sy0, sx0 = max(0, ly0), max(0, lx0)
                sy1, sx1 = min(eh, ly0 + L.mask.shape[0]), min(ew, lx0 + L.mask.shape[1])
                if sy1 > sy0 and sx1 > sx0:
                    cur[oy + sy0:oy + sy1, ox + sx0:ox + sx1] = (
                        L.mask[sy0 - ly0:sy1 - ly0, sx0 - lx0:sx1 - lx0] & keep[sy0:sy1, sx0:sx1])
                if (cur & ~base).any():
                    m = base | cur
            md = base_d if m is base else _dilate(m, margin)
            full = np.zeros(shape, bool)
            full[y0:y1, x0:x1] = md
            ov.union(k, full)
    return ov


# ================================================================================================
# Periods / extra regions / dynamic zones
# ================================================================================================

def _region_activity(comp: Proxy, st: _Stats, rects: list[tuple[int, int, int, int]]) -> np.ndarray:
    """[n, len(rects)] mean |frame - canvas| inside each proxy rect."""
    n = int(comp.n)
    out = np.zeros((n, len(rects)), np.float32)
    if not rects:
        return out
    for ks, fr in _iter_chunks(comp, np.arange(n)):
        f = fr.astype(np.float32)
        for j, (x0, y0, x1, y1) in enumerate(rects):
            out[ks, j] = np.abs(f[:, y0:y1, x0:x1] - st.med[y0:y1, x0:x1]).mean(axis=(1, 2))
    return out


def _periods(st: _Stats, n: int, mode: str, box_full: Box | None, W: int, H: int, cfg: Any,
             notes: list[str]) -> list[LayoutPeriod]:
    """Partition [0, n) into dominant-layout vs fullscreen runs (uniform full-canvas frames stay in the
    dominant layout and are noted)."""
    if st.ref is None or mode != "boxed":
        return [LayoutPeriod(0, n, mode, box_full)]
    nd = st.dev > float(_p(cfg, "NONDOM_FRAC", NONDOM_FRAC))
    if st.dev_lo is not None:
        # a dark fullscreen shot over a black canvas changes most of the canvas region by less than DEV_LEVEL:
        # a smaller change over (nearly) all of it, with a mean change the statistics would exclude, also counts
        nd |= (st.dev_lo > float(_p(cfg, "NONDOM_FRAC_LO", NONDOM_FRAC_LO))) & (st.mad > st.thr_mad)
    uni = nd & (st.fstd < max(float(_cfg(cfg, "uniform_std", 4.0)), 4.0))
    lab = np.where(nd & ~uni, 1, 0)
    periods = []
    for a, b, v in _runs(lab):
        if v == 1:
            periods.append(LayoutPeriod(a, b, "fullscreen", Box(0.0, 0.0, float(W), float(H), 0.0)))
            notes.append(f"frames {a}-{b - 1} show the video fullscreen (not boxed): their segments cover the whole "
                         "canvas in the recreation (per-segment box)")
        else:
            periods.append(LayoutPeriod(a, b, mode, box_full))
    for a, b, v in _runs(uni.astype(np.int8)):
        if v:
            notes.append(f"frames {a}-{b - 1}: whole canvas uniform (flash / dip over the full frame)")
    return periods


# ================================================================================================
# debug/layout.png
# ================================================================================================

_ZONE_BGR = {"logo": (60, 60, 255), "channel_name": (255, 160, 60), "title": (0, 220, 255), "header": (255, 100, 200),
             "watermark": (200, 200, 200), "captions": (80, 255, 80), "text": (160, 255, 160),
             "sticker": (0, 140, 255), "progress": (255, 255, 0), "other": (180, 120, 255)}


def _draw_layout_png(path: Path, comp: Proxy, st: _Stats, layout: Layout, events: list[dict],
                     colour: dict | None, overlays: OverlayMasks) -> None:
    """debug/layout.png: annotated competitor frame (a frame showing a caption when there is one: box
    outline with geometry, every zone, caption band, extra regions, the frame's overlay mask shaded),
    the temporal-std map, and a timeline (layout periods, caption / text events, canvas deviation)."""
    import cv2
    h, w = st.mean.shape
    scale = max(1.0, 960.0 / h)
    W2, H2 = int(round(w * scale)), int(round(h * scale))
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    caps = [e for e in events if e["type"] == "captions"]
    if caps:
        e = max(caps, key=lambda e: (e["comp_out"] - e["comp_in"], -e["comp_in"]))
        k_show = (e["comp_in"] + e["comp_out"] - 1) // 2
    else:
        used = np.flatnonzero(st.used)
        k_show = int(used[len(used) // 2]) if used.size else 0
    col = _colour_frames(comp, [k_show]) if colour is not None else None
    base = col[k_show] if col else cv2.cvtColor(np.asarray(comp.get(k_show)), cv2.COLOR_GRAY2BGR)
    A = cv2.resize(base, (W2, H2), interpolation=cv2.INTER_NEAREST)
    A = (A.astype(np.float32) * 0.8).astype(np.uint8)
    ov = overlays.get(k_show)
    if ov is not None:
        m = cv2.resize(ov.astype(np.uint8), (W2, H2), interpolation=cv2.INTER_NEAREST) > 0
        A[m] = (0.55 * A[m] + 0.45 * np.array([255, 0, 255])).astype(np.uint8)
    s_full = (scale * rx, scale * ry)

    def rect(img, x, y, ww, hh, col, label, thick=2):
        p0 = (int(round(x * s_full[0])), int(round(y * s_full[1])))
        p1 = (int(round((x + ww) * s_full[0])) - 1, int(round((y + hh) * s_full[1])) - 1)
        cv2.rectangle(img, p0, p1, col, thick, cv2.LINE_AA)
        ty = p0[1] - 4 if p0[1] > 14 else p1[1] + 14
        cv2.putText(img, label, (p0[0] + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, label, (p0[0] + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA)

    if layout.box is not None:
        b = layout.box
        cov = rounded_box_coverage((W2, H2), b.x * s_full[0], b.y * s_full[1], (b.x + b.w) * s_full[0],
                                   (b.y + b.h) * s_full[1], b.corner_radius * (s_full[0] + s_full[1]) / 2)
        cnts, _ = cv2.findContours((cov >= 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(A, cnts, -1, (0, 255, 0), 2, cv2.LINE_AA)
        lbl = f"video box x{b.x:g} y{b.y:g} w{b.w:g} h{b.h:g} r{b.corner_radius:g}"
        org = (int(b.x * s_full[0] + 0.3 * b.corner_radius * s_full[0]) + 8, int(b.y * s_full[1]) + 24)
        cv2.putText(A, lbl, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(A, lbl, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)
    cv2.putText(A, f"frame {k_show} (overlay mask shaded)", (6, H2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 0, 255), 1, cv2.LINE_AA)
    for z in layout.zones:
        rect(A, z.x, z.y, z.w, z.h, _ZONE_BGR.get(z.type, _ZONE_BGR["other"]),
             z.type + ("" if z.static else f" {z.comp_in}-{z.comp_out}"))
    for i, r in enumerate(layout.extra_regions):
        rect(A, r.x, r.y, r.w, r.h, (255, 0, 255), f"extra region {i + 1}")
    # panel B: temporal std (log) + static mask outline
    sd = np.log1p(st.std) / max(1e-6, float(np.log1p(st.std).max()))
    B = cv2.applyColorMap((sd * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    B = cv2.resize(B, (W2, H2), interpolation=cv2.INTER_NEAREST)
    cv2.putText(B, "temporal std (log); static = dark", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1,
                cv2.LINE_AA)
    # timeline strip
    n = int(comp.n)
    TW = 2 * W2
    rows = 4
    T = np.full((24 * rows + 30, TW, 3), 24, np.uint8)
    xs = lambda k: int(round(k / max(1, n) * (TW - 1)))  # noqa: E731
    for p in layout.periods:
        col = {"boxed": (0, 160, 0), "fullscreen": (0, 120, 255), "split": (255, 0, 255), "pip": (200, 0, 200)}.get(
            p.mode, (128, 128, 128))
        cv2.rectangle(T, (xs(p.comp_in), 4), (max(xs(p.comp_in), xs(p.comp_out) - 1), 22), col, -1)
    cv2.putText(T, "layout periods", (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    for e in events:
        col = _ZONE_BGR.get(e["type"], (200, 200, 200))
        row = {"captions": 1, "text": 2, "sticker": 2}.get(e["type"], 2)
        cv2.rectangle(T, (xs(e["comp_in"]), 4 + 24 * row), (max(xs(e["comp_in"]), xs(e["comp_out"]) - 1),
                                                            22 + 24 * row), col, -1)
    cv2.putText(T, "captions", (4, 18 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(T, "other text / stickers", (4, 18 + 48), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    d = np.clip(st.dev, 0, 1)
    pts = np.array([[xs(k), int(24 * 3 + 22 - 18 * d[k])] for k in range(n)], np.int32)
    if len(pts) > 1:
        cv2.polylines(T, [pts], False, (0, 200, 255), 1, cv2.LINE_AA)
    cv2.putText(T, "canvas deviation", (4, 18 + 72), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    bg = layout.background or {}
    info = (f"layout {layout.mode} | background {bg.get('type')} {bg.get('color', '')} | {n} frames | "
            f"{sum(1 for c in layout.captions if c.get('type') == 'captions')} caption events | "
            f"zones {len(layout.zones)} | extra regions {len(layout.extra_regions)}")
    cv2.putText(T, info, (4, 24 * rows + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    img = np.vstack([np.hstack([A, B]), T])
    write_image(path, img)                   # unicode-safe (cv2.imwrite cannot open non-ASCII Windows paths)


# ================================================================================================
# analyze_layout
# ================================================================================================

def analyze_layout(comp: Proxy, cfg: Any, cache: Cache | None, debug_dir: str | os.PathLike | None,
                   dlog: DecisionLog | None) -> tuple[Layout, OverlayMasks]:
    """Stage 4 (see the module docstring). Returns (Layout in competitor full-res CORNER px, initial
    per-frame OverlayMasks at proxy res). Writes ``debug_dir/layout.png``; cached by proxy id + params."""
    return _analyze(comp, cfg, cache, debug_dir, dlog, None)


@dataclass
class _ForcedBox:
    """A video box measured against RAW (:func:`refine_box_from_raw`) that replaces the detection of step 2."""
    box: Box                                    # full-res CORNER px (what the Layout stores)
    edges: tuple[float, float, float, float]    # proxy CORNER coords (x0, y0, x1, y1)
    radius: float                               # proxy px
    raw_match: np.ndarray | None                # bool [h, w]: pixels whose (static) content matches RAW
    evidence: dict
    notes: list[str] = field(default_factory=list)

    def signature(self) -> list:
        import hashlib
        mh = ""
        if self.raw_match is not None:
            mh = hashlib.blake2b(np.packbits(np.asarray(self.raw_match, bool)).tobytes(), digest_size=12).hexdigest()
        return ["raw_refined", [round(float(v), 4) for v in (self.box.x, self.box.y, self.box.w, self.box.h,
                                                              self.box.corner_radius)], mh, list(self.notes)]


def _forced_box_result(st: _Stats, fb: _ForcedBox, cfg: Any, dlog: DecisionLog) -> _BoxResult:
    """A :class:`_BoxResult` for a box given from outside: the dynamic mask and the dynamic components that
    do not touch the box (extra regions / animated zones) are measured as in :func:`_detect_box`."""
    h, w = st.mean.shape
    thr = float(_cfg(cfg, "static_std_thresh", 2.0))
    dyn = _morph(_morph(st.std >= thr, "open", 3), "close", 5)
    x0, y0, x1, y1 = fb.edges
    bi = (max(0, int(math.floor(x0))), max(0, int(math.floor(y0))), min(w, int(math.ceil(x1))),
          min(h, int(math.ceil(y1))))
    near = np.zeros((h, w), bool)
    near[max(0, bi[1] - 2):bi[3] + 2, max(0, bi[0] - 2):bi[2] + 2] = True
    n, _lab, stats, _ = _cc(dyn & ~near)
    order = 1 + np.argsort(-stats[1:, 4], kind="stable") if n > 1 else np.zeros(0, int)
    others = [(int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 0] + stats[i, 2]), int(stats[i, 1] + stats[i, 3]),
               int(stats[i, 4])) for i in order if stats[i, 4] >= 12]
    ev = dict(fb.evidence)
    dlog.record("layout", "box", kind="boxed", source="raw", edges_proxy=[round(float(v), 4) for v in fb.edges],
                radius_proxy=round(float(fb.radius), 3), evidence=ev)
    return _BoxResult("boxed", tuple(float(v) for v in fb.edges), float(fb.radius), "raw", bi, dyn, others, ev)


def _analyze(comp: Proxy, cfg: Any, cache: Cache | None, debug_dir: str | os.PathLike | None,
             dlog: DecisionLog | None, forced: _ForcedBox | None) -> tuple[Layout, OverlayMasks]:
    """analyze_layout; with ``forced`` the video box is not detected but taken from a RAW-verified
    measurement (everything that depends on the box is re-derived: statistics region, periods, background,
    border, zones, text overlays, masks). Cached under a key that includes the forced box."""
    dlog = dlog or null_dlog()
    if comp.n <= 0:
        raise ValueError("analyze_layout: the competitor proxy has no frames")
    params = cfg.analysis_params() if hasattr(cfg, "analysis_params") else {}
    key = stage_key("layout", _proxy_id(comp), params, LAYOUT_ALGO_VERSION,
                    *([] if forced is None else forced.signature()))
    if cache is not None:
        pj = cache.path("layout", key, ".json")
        ps, po, pp = (cache.path("layout", key, s) for s in (".static.npy", ".overlays.npz", ".png"))
    else:
        root = Path(debug_dir) / "layout_data" if debug_dir is not None else Path(tempfile.mkdtemp(prefix="mc_layout_"))
        root.mkdir(parents=True, exist_ok=True)
        pj, ps, po, pp = (root / f"{key}{s}" for s in (".json", ".static.npy", ".overlays.npz", ".png"))
    ps, po = ps.resolve(), po.resolve()          # stored in the Layout: independent of the working directory
    dbg = Path(debug_dir) if debug_dir is not None else None
    if cache is not None and pj.exists() and ps.exists() and po.exists():
        import json
        layout = Layout.from_dict(json.loads(pj.read_text(encoding="utf-8")))
        layout.static_mask_file, layout.overlay_mask_file = str(ps), str(po)
        overlays = OverlayMasks.load(po)
        if dbg is not None and pp.exists():
            import shutil
            dbg.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pp, dbg / "layout.png")
        dlog.record("layout", "cache_hit", key=key)
        log.info("layout: cache hit %s", key)
        return layout, overlays

    import json
    import time
    t_last = [time.perf_counter()]
    times: dict[str, float] = {}

    def _tick(name: str) -> None:
        t = time.perf_counter()
        times[name] = round(t - t_last[0], 3)
        t_last[0] = t
    W, H = int(comp.full_size[0]), int(comp.full_size[1])
    h, w = int(comp.size[1]), int(comp.size[0])
    region = None
    if forced is not None:
        fx0, fy0, fx1, fy1 = forced.edges
        region = (int(math.floor(fx0)), int(math.floor(fy0)), int(math.ceil(fx1)), int(math.ceil(fy1)))
    st = _compute_stats(comp, cfg, dlog, region=region)
    _tick("stats")
    notes = list(st.notes)
    if forced is not None:
        br = _forced_box_result(st, forced, cfg, dlog)
        box, box_ev = Box(**forced.box.to_dict()), {"source": "measured against RAW (refine_box_from_raw)"}
        notes.extend(forced.notes)
    else:
        br = _detect_box(comp, st, cfg, dlog)
        if br.kind == "boxed":
            box, box_ev = _box_full(br, comp)
        else:
            box, box_ev = Box(0.0, 0.0, float(W), float(H), 0.0), {}
    _tick("box")
    dlog.record("layout", "box_full_res", box=box.to_dict(), evidence=box_ev)
    lay_tmp = Layout(W, H, box=box)
    cov = box_coverage(lay_tmp, comp)
    used = np.flatnonzero(st.used)
    ck = used[:3] if used.size else []          # first dominant-layout frames: decoded from the file start
    colour = _colour_frames(comp, [int(k) for k in ck])
    _tick("colour")
    bg, bg_level = _classify_background(comp, st, br, cov, colour, cfg, dlog)
    _tick("background")
    ring_px = 0
    if br.kind == "boxed" and bg.get("type") in ("solid", "gradient"):
        stroke = _stroke(st, cov, bg_level, np.zeros(cov.shape, bool), float(_cfg(cfg, "static_std_thresh", 2.0)))
        if stroke is not None:
            rmean = (float(comp.ratio[0]) + float(comp.ratio[1])) / 2
            stroke["width"] = round(stroke["width_proxy"] / rmean, 2)
            ring_px = int(stroke["width_proxy"])
            bg["box_" + stroke["kind"]] = {"width": stroke["width"], "level_gray": round(stroke["level"], 1)}
            notes.append(f"box {stroke['kind']} detected (~{stroke['width']:g} px): not recreated")
            dlog.record("layout", "box_border", evidence=stroke)
    if bg.get("type") == "image":
        # a static textured picture behind the box: static overlays cannot be told apart from it
        zones, zmask = [], np.zeros(cov.shape, bool)
        notes.append("static image background: logo / title zones are not separated from the picture")
        dlog.record("layout", "zones", count=0, reason="static image background")
    else:
        zones, zmask = _detect_zones(st, br, cov, bg, bg_level, colour, comp, cfg, dlog, ring_px=ring_px,
                                     raw_match=forced.raw_match if forced is not None else br.static_video)
    _tick("zones")
    # extra regions (split / PiP) and dynamic zones outside the box
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    extra: list[Box] = []
    extra_px: list[tuple[int, int, int, int]] = []
    dyn_zone_px: list[tuple[int, int, int, int]] = []
    if br.kind == "boxed":
        barea = (br.edges[2] - br.edges[0]) * (br.edges[3] - br.edges[1])
        for (x0, y0, x1, y1, a) in br.other_components:
            if _iou((x0, y0, x1, y1), br.bbox_int) > 0:
                continue
            fill = a / max(1, (x1 - x0) * (y1 - y0))
            if a >= 0.05 * barea and fill >= 0.6:
                extra_px.append((x0, y0, x1, y1))
                extra.append(Box(x0 / rx, y0 / ry, (x1 - x0) / rx, (y1 - y0) / ry, 0.0))
            elif a >= 12:
                dyn_zone_px.append((x0, y0, x1, y1))
    act = _region_activity(comp, st, extra_px + dyn_zone_px)
    _tick("regions")
    mode = br.kind
    periods = _periods(st, int(comp.n), mode, box if br.kind == "boxed" else Box(0.0, 0.0, float(W), float(H), 0.0),
                       W, H, cfg, notes)
    if extra_px:
        on = (act[:, :len(extra_px)] > 4.0).any(axis=1)
        big = max((e[2] - e[0]) * (e[3] - e[1]) for e in extra_px) >= 0.4 * barea
        pm = "split" if big else "pip"
        new_periods = []
        for p in periods:
            for a, b, v in _runs(on[p.comp_in:p.comp_out].astype(np.int8)):
                new_periods.append(LayoutPeriod(p.comp_in + a, p.comp_in + b, pm if (v and p.mode == "boxed") else p.mode,
                                                p.box))
        periods = new_periods
        notes.append(f"{len(extra_px)} extra video region(s) ({pm}) detected; only the dominant box is recreated")
        dlog.record("layout", "extra_regions", regions=[b.to_dict() for b in extra], mode=pm,
                    active_frames=int(on.sum()))
    for j, (x0, y0, x1, y1) in enumerate(dyn_zone_px):
        a_on = np.flatnonzero(act[:, len(extra_px) + j] > 4.0)
        if a_on.size == 0:
            continue
        ztype = "progress" if (x1 - x0) >= 8 * max(1, y1 - y0) else "sticker"
        zones.append(Zone(ztype, x0 / rx, y0 / ry, (x1 - x0) / rx, (y1 - y0) / ry, int(a_on[0]), int(a_on[-1]) + 1,
                          False, "", "animated element outside the video box"))
    # merge adjacent runs of the same mode
    merged_p: list[LayoutPeriod] = []
    for p in periods:
        if merged_p and merged_p[-1].mode == p.mode and merged_p[-1].comp_out == p.comp_in:
            merged_p[-1] = LayoutPeriod(merged_p[-1].comp_in, p.comp_out, p.mode, p.box)
        else:
            merged_p.append(p)
    periods = merged_p
    thr = float(_cfg(cfg, "static_std_thresh", 2.0))
    static = st.std < thr
    events = _detect_text_overlays(comp, st, br, periods, static, cfg, dlog, extra_rois=dyn_zone_px)
    _tick("text")
    # animated zones outside the box that are really captions are represented by the captions zone
    ev_boxes = [(e["x0"], e["y0"], e["x1"], e["y1"]) for e in events]
    kept = []
    for z in zones:
        if not z.static and z.type in ("sticker", "progress"):
            zx0, zy0, zx1, zy1 = z.x * rx, z.y * ry, (z.x + z.w) * rx, (z.y + z.h) * ry
            za = max(1e-9, (zx1 - zx0) * (zy1 - zy0))
            cov_e = sum(max(0.0, min(zx1, b[2]) - max(zx0, b[0])) * max(0.0, min(zy1, b[3]) - max(zy0, b[1]))
                        for b in ev_boxes)
            if cov_e >= 0.5 * za:
                continue
        kept.append(z)
    zones = kept
    overlays = _event_masks(events, (h, w), int(_cfg(cfg, "overlay_dilate_px", 3)))
    _tick("masks")
    captions = []
    for e in events:
        captions.append({"type": e["type"], "comp_in": e["comp_in"], "comp_out": e["comp_out"],
                         "x": round(e["x0"] / rx, 2), "y": round(e["y0"] / ry, 2),
                         "w": round((e["x1"] - e["x0"]) / rx, 2), "h": round((e["y1"] - e["y0"]) / ry, 2)})
    capev = [e for e in events if e["type"] == "captions"]
    if capev:
        cx0 = min(e["x0"] for e in capev)
        cy0 = min(e["y0"] for e in capev)
        cx1 = max(e["x1"] for e in capev)
        cy1 = max(e["y1"] for e in capev)
        zones.append(Zone("captions", cx0 / rx, cy0 / ry, (cx1 - cx0) / rx, (cy1 - cy0) / ry,
                          min(e["comp_in"] for e in capev), max(e["comp_out"] for e in capev), False, "",
                          f"{len(capev)} caption events; white text with dark outline, median glyph height "
                          f"{np.median([e['h'] for e in capev]) / ry:.0f} px"))
    zones.sort(key=lambda z: (z.y, z.x, z.type))
    canvas_bg = bg.get("color", "#000000")
    layout = Layout(comp_w=W, comp_h=H, mode=mode, box=box, canvas_bg=canvas_bg, background=bg, zones=zones,
                    periods=periods, extra_regions=extra, static_mask_file=str(ps), overlay_mask_file=str(po),
                    proxy_ratio=(rx, ry), captions=captions, notes=notes)
    np.save(ps, static)
    overlays.save(po)
    if dbg is not None or cache is not None:
        try:
            _draw_layout_png(pp, comp, st, layout, events, colour, overlays)
            if dbg is not None:
                import shutil
                dbg.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(pp, dbg / "layout.png")
        except Exception as e:  # noqa: BLE001 - a debug image must never break the analysis
            log.warning("layout: debug/layout.png failed: %s", e)
    atomic_write_text(pj, json.dumps(layout.to_dict(), default=json_default, indent=1, sort_keys=True))
    dlog.record("layout", "summary", mode=mode, box=box.to_dict(), background=bg, zones=len(zones),
                caption_events=len(capev), periods=[[p.comp_in, p.comp_out, p.mode] for p in periods],
                extra_regions=len(extra), overlay_frames=len(overlays))
    log.debug("layout timings (s): %s", times)
    log.info("layout: %s box x%.2f y%.2f w%.2f h%.2f r%.2f, background %s %s, %d zones, %d caption events",
             mode, box.x, box.y, box.w, box.h, box.corner_radius, bg.get("type"), bg.get("color", ""), len(zones),
             len(capev))
    return layout, overlays


# ================================================================================================
# Box refinement against RAW (DESIGN §7 D2)
# ================================================================================================
#
# Step 2 finds the video box from TEMPORAL activity. A locked-off single-camera shot (talking head,
# podcast, stream) has a static background for the whole edit, and RAW letterbox bars / baked-in panels
# are static too: the dynamic region then covers only the moving subject. Once the FrameMap exists, the
# box is re-measured against RAW itself: confidently matched RAW frames are warped into competitor space
# with their fitted Sims, and every pixel votes
#   IN   the competitor agrees with the (gain/offset-fitted) warped RAW, and RAW differs there from what the
#        canvas next to the box would show (so the agreement is evidence, not a coincidence) — or, gain-
#        invariant, the competitor shows RAW's local STRUCTURE (windowed ZNCC >= REFINE_ZNCC_MIN where RAW is
#        textured): a caption gradient / darkened lower third / inner shadow / vignette changes the local gain
#        and offset, not the structure, and a flat canvas or a title never correlates with RAW;
#   OUT  RAW is present but the competitor disagrees (canvas, title, ... or an unmasked overlay) — except on
#        pixels INSIDE the current box that the temporal analysis proved dynamic: there the disagreement is a
#        competitor effect on the video (caption gradient, darkened lower third, inner shadow / feathered edge,
#        vignette: the gain / offset fit is global), not canvas, and counts only where the competitor shows
#        the canvas model while RAW would not;
#   neither when RAW is absent or indistinguishable from the canvas.
# The canvas next to the box (B) is sampled from background pixels only: never a zone (title, watermark,
# logo, ...), for a solid / gradient background only static pixels that match its model, for a moving
# background only non-static pixels — static text must never make RAW content that equals the canvas (black
# letterbox bars on a black canvas) look discriminating.
# Per box side, lines (columns / rows over the middle half of the box) are classified in / out / no-RAW /
# don't-care and scanned outward from the current edge (growing through in and don't-care lines to the
# outermost in line, stopping at the first out / no-RAW line) — or inward (shrinking) over lines with RAW
# disagreement, and only over lines that are mostly static or show the canvas model and do not show RAW's
# local structure (a side never moves inward over video the temporal analysis saw or RAW explains). The
# sub-pixel edge is the integral estimator over a per-pixel COVERAGE map
#   c(x) = sum_k (C_k - B_k)(R_k - B_k) / sum_k (R_k - B_k)^2
# (C competitor, R fitted warped RAW, B background = the nearest background pixel outside the box: the proxy
# is the area average of c R + (1 - c) B), or the RAW frame's own edge when the box ends where RAW ends — an
# edge moves OUT to the RAW frame edge only when IN lines prove video beyond the temporal edge; an extent
# nothing can observe (RAW that equals the canvas) keeps the temporal edge. The corner radius is the joint
# corner fit of :func:`_fit_radius` on the same coverage map (kept when no corner is observable).
# The measured box replaces the temporal one only when it explains the votes better away from both outlines
# (partially covered boundary lines decide nothing), removes no video (dynamic or structure-agreeing pixels
# with RAW present in the ring between the boxes) and does not cover rounded corners the competitor shows at
# the temporal box.

REFINE_FRAMES = 16                # confidently matched frames used (spread over the edit)
REFINE_BLUR = 1.0                 # Gaussian sigma (proxy px) before the per-pixel comparison
REFINE_TAU_MIN = 8.0              # |competitor - fitted warped RAW| counted as disagreement (8-bit) ...
REFINE_TAU_K = 4.0                # ... or this many robust sigmas of the in-box residual
REFINE_DISC_MIN = 12.0            # |warped RAW - background| for a pixel to discriminate inside / outside
REFINE_COV_MIN = 6.0              # |warped RAW - background| for a pixel's coverage estimate
REFINE_EDGE_PX = 2.0              # full-res edge change that is material (D2)
REFINE_RADIUS_PX = 3.0            # full-res radius change that is material (D2)
REFINE_SHRINK_FRAC = 0.5          # a side moves inward only over lines at least this static or canvas-like
REFINE_RING_DYN_FRAC = 0.05       # max fraction of dynamic, not canvas-like pixels a refined box may drop
REFINE_BAND_PX = 2                # votes this close (proxy px) to either outline do not decide old vs new box
REFINE_FRAME_EDGE_TOL = 1.5       # proxy px: the RAW frame edge 'coincides' with a measured edge
REFINE_GROW_MIN_LINES = 3         # a side grows only to an IN line at least this far outside (blurred boundary)
REFINE_ZNCC_WIN = 11              # proxy px window of the gain-invariant local structure comparison ...
REFINE_ZNCC_MIN = 0.85            # ... the competitor shows RAW's local structure when the windowed ZNCC >= this
REFINE_ZNCC_STD = 3.0             # ... over a window where warped RAW has at least this local std (8-bit levels)
REFINE_ZNCC_GAIN_MIN = 0.1        # ... and the competitor at least this fraction of it (a flat canvas never agrees)

_L_DC, _L_IN, _L_OUT, _L_NORAW, _L_MIX = 0, 1, 2, 3, 4
_SIDES = ("left", "top", "right", "bottom")


def _canon(a: np.ndarray, side: str) -> np.ndarray:
    """View of a [h, w] map with the given box side as a LEFT edge: lines = columns, inside = larger index."""
    if side == "left":
        return a
    if side == "right":
        return a[:, ::-1]
    if side == "top":
        return a.T
    return a[::-1, :].T


def _to_canon(v: float, side: str, w: int, h: int) -> float:
    return v if side in ("left", "top") else (w - v if side == "right" else h - v)


_from_canon = _to_canon                   # the mapping is an involution


def _nearest_fill(img: np.ndarray, src: np.ndarray) -> np.ndarray | None:
    """Every pixel gets the value of ``img`` at its nearest ``src`` pixel (None when src is empty)."""
    import cv2
    if not src.any():
        return None
    _d, lab = cv2.distanceTransformWithLabels((~src).astype(np.uint8), cv2.DIST_L2, 3,
                                              labelType=cv2.DIST_LABEL_PIXEL)
    vals = np.zeros(int(lab.max()) + 1, np.float32)
    vals[lab[src]] = img[src]
    return vals[lab]


def _refine_frames(layout: Layout, comp: Proxy, raw: Proxy, fm: Any, cfg: Any) -> tuple[list[int], dict]:
    """Confidently matched frames of the dominant layout, spread over the edit (deterministic): the
    MATCH frames with a finite Sim, a RAW frame the proxy holds and a score within 0.05 of the median
    (>= 0.8), split into REFINE_FRAMES consecutive groups, best score of each group."""
    from .model import Status
    n = int(min(int(fm.n), int(comp.n)))
    if n <= 0:
        return [], {"reason": "empty frame map"}
    ok = np.asarray(fm.status[:n]) == Status.MATCH
    dom = np.ones(n, bool)
    if layout.periods:
        dom[:] = False
        for p in layout.periods:
            if p.mode == layout.mode:
                dom[max(0, int(p.comp_in)):min(n, int(p.comp_out))] = True
    s, th, tx, ty = (np.asarray(fm.d[c][:n], np.float64) for c in ("s", "theta", "tx", "ty"))
    j = np.asarray(fm.raw[:n], np.int64)
    sc = np.asarray(fm.score[:n], np.float64)
    ok &= dom & np.isfinite(s) & np.isfinite(th) & np.isfinite(tx) & np.isfinite(ty) & (s > 0)
    ok &= (j >= 0) & (j < int(raw.n)) & np.isfinite(sc)
    if "low_margin" in fm.d:
        lm = ok & ~np.asarray(fm.d["low_margin"][:n], bool)
        if lm.sum() >= 3:
            ok = lm
    idx = np.array([k for k in np.flatnonzero(ok) if raw.has(int(j[k]))], np.int64)
    if idx.size == 0:
        return [], {"reason": "no confidently matched frame in the dominant layout"}
    thr = max(0.8, float(np.median(sc[idx])) - 0.05)
    idx = idx[sc[idx] >= thr]
    groups = max(1, int(_p(cfg, "REFINE_FRAMES", REFINE_FRAMES)))
    out = [int(g[int(np.argmax(sc[g]))]) for g in np.array_split(idx, min(groups, idx.size)) if g.size]
    return sorted(out), {"candidates": int(idx.size), "score_threshold": round(thr, 4)}


def _refine_frame(k: int, comp: Proxy, raw: Proxy, fm: Any, overlays: Any, base_fit: np.ndarray,
                  static: np.ndarray | None, sig: float) -> dict | None:
    """Competitor frame k and its RAW frame warped with the fitted Sim, gain / offset fitted (robust least
    squares on the low frequencies) over the current box's dynamic, overlay-free pixels away from its rim;
    None when the fit region is too small or degenerate. Keys: C, R (fitted warped RAW), Cb, Rb (blurred by
    ``sig``, as they are compared; RAW with a normalised blur, valid up to its own frame edge), V (RAW
    present), Ok (dilated overlay mask)."""
    import cv2
    from .geometry import warp_raw_to_comp
    w, h = int(comp.size[0]), int(comp.size[1])
    C = np.asarray(comp.get(int(k)), np.float32)
    j = int(fm.raw[k])
    Wr, V = warp_raw_to_comp(np.asarray(raw.get(j), np.float32), fm.sim(int(k)), bool(fm.flip[k]),
                             float(raw.full_size[0]), (w, h), tuple(float(v) for v in raw.ratio),
                             tuple(float(v) for v in comp.ratio))
    if sig > 0:
        Vf = V.astype(np.float32)
        Cb = cv2.GaussianBlur(C, (0, 0), sig)
        Wb = cv2.GaussianBlur(Wr * Vf, (0, 0), sig) / np.maximum(cv2.GaussianBlur(Vf, (0, 0), sig), 1e-3)
    else:
        Cb, Wb = C, Wr
    Ok = None
    if overlays is not None:
        try:
            Ok = overlays.get_dilated(int(k)) if hasattr(overlays, "get_dilated") else overlays.get(int(k))
        except Exception:  # noqa: BLE001 - masks are optional evidence
            Ok = None
    Ok = np.zeros((h, w), bool) if Ok is None or np.shape(Ok) != (h, w) else np.asarray(Ok, bool)
    fit = base_fit & V & ~Ok
    if int(_erode_mask(fit, 6).sum()) >= 400:
        fit = _erode_mask(fit, 6)          # the sigma-3 blur below must not mix in the canvas / RAW border
    if static is not None and int((fit & ~static).sum()) >= 400:
        fit &= ~static
    if int(fit.sum()) < 400:
        return None
    # gain / offset from the low frequencies (sigma 3): the two resampling chains (competitor render + INTER_AREA
    # vs RAW proxy + warp) keep different amounts of fine contrast, which must not bias the photometric fit
    Vf3 = V.astype(np.float32)
    C3 = cv2.GaussianBlur(C, (0, 0), 3.0)
    W3 = cv2.GaussianBlur(Wr * Vf3, (0, 0), 3.0) / np.maximum(cv2.GaussianBlur(Vf3, (0, 0), 3.0), 1e-3)
    x = W3[fit].astype(np.float64)
    y = C3[fit].astype(np.float64)
    sel = np.ones(x.size, bool)
    a, b = 1.0, 0.0
    for _ in range(3):
        A = np.stack([x[sel], np.ones(int(sel.sum()))], axis=1)
        (a, b), *_ = np.linalg.lstsq(A, y[sel], rcond=None)
        r = np.abs(y - (a * x + b))
        s = 1.4826 * float(np.median(r[sel])) + 1e-6
        sel = r <= max(3.0 * s, 6.0)
        if sel.sum() < 200:
            return None
    if not (0.2 <= a <= 5.0):
        return None
    return {"k": int(k), "j": j, "C": C, "R": (a * Wr + b).astype(np.float32), "Cb": Cb,
            "Rb": (a * Wb + b).astype(np.float32), "V": V, "Ok": Ok, "gain": round(float(a), 4),
            "offset": round(float(b), 3)}


def _footprints(fm: Any, ks: Sequence[int], raw: Proxy, comp: Proxy) -> dict[str, list[float]] | None:
    """Canonical proxy coordinate of the RAW frame's own edge per side and frame (axis-aligned Sims only)."""
    w, h = int(comp.size[0]), int(comp.size[1])
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    Wr, Hr = float(raw.full_size[0]), float(raw.full_size[1])
    out: dict[str, list[float]] = {s: [] for s in _SIDES}
    for k in ks:
        sim = fm.sim(int(k))
        if abs(float(sim.theta_deg)) > 0.05:
            return None
        pts = sim.apply(np.array([[0.0, 0.0], [Wr, 0.0], [0.0, Hr], [Wr, Hr]]))
        x0, x1 = float(pts[:, 0].min()) * rx, float(pts[:, 0].max()) * rx
        y0, y1 = float(pts[:, 1].min()) * ry, float(pts[:, 1].max()) * ry
        for side, v in (("left", x0), ("top", y0), ("right", x1), ("bottom", y1)):
            out[side].append(_to_canon(v, side, w, h))
    return out


def _line_classes(nI: np.ndarray, nO: np.ndarray, nV: np.ndarray, side: str, band: tuple[int, int],
                  nfr: int) -> np.ndarray:
    """Per canonical line (see :func:`_canon`) over the band rows: in / out / no-RAW / don't-care / mixed."""
    b0, b1 = int(band[0]), int(band[1])
    ci, co, cv = (_canon(a, side)[b0:b1].sum(axis=0).astype(np.float64) for a in (nI, nO, nV))
    L = max(1, (b1 - b0) * nfr)
    ev = ci + co
    cls = np.full(ci.size, _L_DC, np.int8)
    has_raw = cv >= 0.3 * L
    has = has_raw & (ev >= 0.15 * L)
    cls[~has_raw] = _L_NORAW
    cls[has] = _L_MIX
    cls[has & (ci >= 0.6 * ev)] = _L_IN
    cls[has & (co >= 0.6 * ev)] = _L_OUT
    return cls


def _structure_agreement(C: np.ndarray, R: np.ndarray, V: np.ndarray, win: int, zmin: float, smin: float,
                         gmin: float, tau: float) -> tuple[np.ndarray, np.ndarray]:
    """Gain-invariant local comparison of the competitor ``C`` with the fitted warped RAW ``R`` (both
    blurred): (agree, textured) bool [h, w]. Windowed statistics over the RAW-present pixels (``V``) of the
    ``win`` x ``win`` window around each pixel (at least half of it). ``textured``: RAW present and varying
    there (local std >= ``smin``); ``agree``: textured, the competitor varies too (>= ``gmin`` x RAW's local
    std), the zero-mean normalised cross-correlation is >= ``zmin`` AND the pixel itself is explained by the
    window's local gain / offset fit (|C - (a R + b)| <= ``tau``: a canvas pixel next to the box edge, in a
    window that is mostly video, is not). A competitor effect on the video (caption gradient, darkened lower
    third, inner shadow, feathered edge, vignette) changes the local gain / offset, not the structure: it
    still agrees; a flat canvas, a title or any content other than RAW does not. (Where RAW's own content
    ends at the box edge too — letterbox bars cropped off, bars at the canvas level — the outer pixels agree
    as well: the caller counts agreement as evidence only where RAW differs from the canvas.)"""
    import cv2
    k = (int(win), int(win))
    Vf = np.asarray(V, np.float32)

    def box(a: np.ndarray) -> np.ndarray:
        return cv2.boxFilter(a, cv2.CV_32F, k, normalize=True, borderType=cv2.BORDER_CONSTANT)
    wv = box(Vf)
    inv = 1.0 / np.maximum(wv, 1e-3)
    Cv, Rv = C * Vf, R * Vf
    mC, mR = box(Cv) * inv, box(Rv) * inv
    vC = np.maximum(box(Cv * C) * inv - mC * mC, 0.0)
    vR = np.maximum(box(Rv * R) * inv - mR * mR, 0.0)
    cCR = box(Cv * R) * inv - mC * mR
    textured = np.asarray(V, bool) & (wv >= 0.5) & (vR >= float(smin) ** 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = cCR / np.sqrt(np.maximum(vC * vR, 1e-6))
        a = cCR / np.maximum(vR, 1e-6)
    resid = np.abs(C - (a * (R - mR) + mC))
    agree = textured & (vC >= (float(gmin) ** 2) * vR) & (z >= float(zmin)) & (resid <= float(tau))
    return agree, textured


def _line_shrinkable(static: np.ndarray, nBG: np.ndarray, nV: np.ndarray, side: str, band: tuple[int, int],
                     frac: float, nSA: np.ndarray | None = None, nTX: np.ndarray | None = None) -> np.ndarray:
    """Per canonical line over the band rows: may a box side move inward over it? Only when the line is
    mostly static (the temporal analysis did not see video there) or mostly shows the canvas model where RAW
    would not (``nBG`` votes over the RAW-present pixel-frames ``nV``) — and never when the competitor shows
    RAW's local structure on most of the line's textured pixel-frames (``nSA`` of ``nTX``, see
    :func:`_structure_agreement`): video under a competitor effect, static or not. A line of dynamic video
    that merely disagrees with the globally gain-fitted RAW (caption gradient, inner shadow, vignette) is
    not shrinkable."""
    b0, b1 = int(band[0]), int(band[1])
    st = _canon(np.asarray(static, np.float32), side)[b0:b1].mean(axis=0)
    bg = _canon(nBG, side)[b0:b1].sum(axis=0).astype(np.float64)
    vv = _canon(nV, side)[b0:b1].sum(axis=0).astype(np.float64)
    ok = (st >= frac) | ((vv > 0) & (bg >= frac * vv))
    if nSA is not None and nTX is not None:
        sa = _canon(nSA, side)[b0:b1].sum(axis=0).astype(np.float64)
        tx = _canon(nTX, side)[b0:b1].sum(axis=0).astype(np.float64)
        ok &= ~((tx > 0) & (tx >= 0.2 * vv) & (sa >= frac * tx))
    return ok


def _scan_side(cls: np.ndarray, e0: int, f_out: float | None, shrinkable: np.ndarray | None = None,
               min_grow: int = 1) -> tuple[int, str, int | None, int | None]:
    """Integer canonical edge (first inside line) from the line classes, starting at the current edge e0.

    Grow: outward over in / don't-care / mixed lines to the outermost in line before the first out or
    no-RAW line, when that line lies at least ``min_grow`` lines outside e0 (the lines right next to the
    edge are blurred mixtures of both sides: agreement there proves nothing). Else shrink: inward over the lines that are not in, if RAW disagrees on at least one of them
    (no-RAW lines alone never shrink: the RAW frame edge often coincides with the box edge) — never past a
    line that ``shrinkable`` (see :func:`_line_shrinkable`) marks False: the new edge is then at most that
    line — or to the outermost RAW frame edge ``f_out`` when no frame's RAW reaches the current edge.
    Returns (edge, action, stop line index, stop class)."""
    n = cls.size
    e0 = int(min(max(e0, 0), n))

    def stop_from(e: int) -> tuple[int | None, int | None]:
        x = e - 1
        while x >= 0:
            if cls[x] in (_L_OUT, _L_NORAW):
                return x, int(cls[x])
            x -= 1
        return None, None
    last_in = None
    x = e0 - 1
    while x >= 0:
        c = cls[x]
        if c == _L_IN:
            last_in = x
        elif c in (_L_OUT, _L_NORAW):
            break
        x -= 1
    if last_in is not None and last_in <= e0 - max(1, int(min_grow)):
        return (last_in, "grow") + stop_from(last_in)
    x, bad = e0, False
    while x < n and cls[x] != _L_IN:
        if shrinkable is not None and not bool(shrinkable[x]):
            break                        # dynamic video: the box cannot end inside it
        bad |= bool(cls[x] == _L_OUT)
        x += 1
    if bad and x < n:
        return (x, "shrink") + stop_from(x)
    x_in = e0
    while x_in < n and cls[x_in] != _L_IN:
        x_in += 1
    if f_out is not None and f_out > e0 + 1.5 and x_in < n:
        e = int(min(math.ceil(f_out), x_in))
        return (e, "shrink_to_raw_frame") + stop_from(e)
    return (e0, "keep") + stop_from(e0)


def _coverage_subpixel(cov_c: np.ndarray, band: tuple[int, int], e_int: int) -> tuple[float | None, dict]:
    """Sub-pixel canonical edge from the canonical coverage map: c(x) = per-column median over the band rows
    (clipped to [0, 1]); the transition is the outermost column xo (searching outward from e_int + 2) whose
    coverage drops below 0.5; integral estimator e = (xo + 4) - sum_{x=xo-3}^{xo+3} c(x) (unmeasured columns
    beyond the transition count as 0 outside / 1 inside). None when the transition is not measurable."""
    b0, b1 = int(band[0]), int(band[1])
    n = cov_c.shape[1]
    lo, hi = max(0, e_int - 10), min(n, e_int + 6)
    need = max(5, int(0.2 * (b1 - b0)))
    cm = np.full(n, np.nan)
    for x in range(lo, hi):
        col = cov_c[b0:b1, x]
        col = col[np.isfinite(col)]
        if col.size >= need:
            cm[x] = float(np.median(np.clip(col, -0.25, 1.25)))
    xo = None
    for x in range(min(hi - 1, e_int + 2), lo - 1, -1):
        if np.isfinite(cm[x]) and cm[x] < 0.5:
            xo = x
            break
    prof = {int(x): round(float(cm[x]), 3) for x in range(lo, hi) if np.isfinite(cm[x])}
    if xo is None or xo + 1 >= n or not np.isfinite(cm[xo + 1]):
        return None, {"reason": "no measurable coverage transition", "profile": prof}
    # levels from the profile itself (a gain / offset misfit of RAW biases c away from exactly 0 / 1)
    outer = [cm[x] for x in range(max(0, xo - 4), xo) if np.isfinite(cm[x])]
    inner = [cm[x] for x in range(xo + 2, min(n, xo + 6)) if np.isfinite(cm[x])]
    S = float(np.median(outer)) if outer else 0.0
    P = float(np.median(inner)) if inner else 1.0
    if P - S < 0.5:
        return None, {"reason": "coverage levels not separated", "profile": prof, "levels": [S, P]}
    a, b = xo - 3, xo + 4
    xs = np.arange(a, b)
    vals = np.array([cm[x] if 0 <= x < n and np.isfinite(cm[x]) else (S if x <= xo else P) for x in xs])
    e = float(b - np.clip((vals - S) / (P - S), 0.0, 1.0).sum())
    ev = {"transition": int(xo), "profile": prof, "levels": [round(S, 3), round(P, 3)], "estimate": round(e, 4)}
    if not (xo - 1.0 <= e <= xo + 2.0):
        return None, {**ev, "reason": "estimate outside the transition"}
    return e, ev


def _draw_refine_png(path: Path, frames: list[dict], nI: np.ndarray, nO: np.ndarray, nV: np.ndarray,
                     old: Box, new: Box, comp: Proxy, changed: bool) -> None:
    """debug/layout_refine.png: median matched competitor frame, RAW agreement (green = matches RAW, red =
    RAW present but different, blue = no RAW), current box (red) and measured box (green)."""
    import cv2
    base = np.median(np.stack([f["C"] for f in frames]), axis=0).astype(np.uint8)
    h, w = base.shape
    img = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR).astype(np.float32) * 0.6
    ev = nI + nO
    tint = np.zeros((h, w, 3), np.float32)
    tint[(ev > 0) & (nI >= nO)] = (0, 200, 0)
    tint[(ev > 0) & (nO > nI)] = (0, 0, 220)
    tint[nV == 0] = (160, 60, 0)
    img = np.clip(img + 0.4 * tint, 0, 255).astype(np.uint8)
    scale = max(1.0, 960.0 / h)
    img = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_NEAREST)
    rx, ry = float(comp.ratio[0]) * scale, float(comp.ratio[1]) * scale
    for b, col in ((old, (0, 0, 255)), (new, (0, 255, 0))):
        cov = rounded_box_coverage((img.shape[1], img.shape[0]), b.x * rx, b.y * ry, (b.x + b.w) * rx,
                                   (b.y + b.h) * ry, b.corner_radius * (rx + ry) / 2)
        cnts, _ = cv2.findContours((cov >= 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cnts, -1, col, 2, cv2.LINE_AA)
    txt = (f"{'CHANGED' if changed else 'kept'}: x{new.x:g} y{new.y:g} w{new.w:g} h{new.h:g} r{new.corner_radius:g} "
           f"(was x{old.x:g} y{old.y:g} w{old.w:g} h{old.h:g} r{old.corner_radius:g}), {len(frames)} frames")
    cv2.putText(img, txt, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, txt, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    write_image(path, img)                   # unicode-safe (cv2.imwrite cannot open non-ASCII Windows paths)


def _bg_gray(bg: dict) -> float:
    """Gray level of a background dict's model colour ('gray', else the 'color' hex through BT.601)."""
    g = bg.get("gray")
    if g is not None:
        return float(g)
    c = str(bg.get("color", "#000000")).lstrip("#")
    try:
        r, gg, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 0.0
    return 0.299 * r + 0.587 * gg + 0.114 * b


def _background_sources(layout: Layout, comp: Proxy, static: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None, str]:
    """Where the refinement may sample the canvas next to the box (B): (bool [h, w] candidate pixels, float
    [h, w] gray model of the background or None, rule). Zones (logo, title, watermark, captions, stickers,
    ... dilated 2 px) never qualify — static text must not act as the background. Solid / gradient
    backgrounds: static pixels (a frame then keeps only those within tolerance of the model); image: static
    pixels; blur / dynamic backgrounds: non-static pixels (static ones are overlays on the moving canvas)."""
    w, h = int(comp.size[0]), int(comp.size[1])
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    src = np.ones((h, w), bool)
    for z in getattr(layout, "zones", None) or []:
        x0, y0 = max(0, int(math.floor(z.x * rx)) - 2), max(0, int(math.floor(z.y * ry)) - 2)
        x1, y1 = min(w, int(math.ceil((z.x + z.w) * rx)) + 2), min(h, int(math.ceil((z.y + z.h) * ry)) + 2)
        if x1 > x0 and y1 > y0:
            src[y0:y1, x0:x1] = False
    bg = getattr(layout, "background", None) or {}
    kind = str(bg.get("type", "solid"))
    model = None
    if kind in ("solid", "gradient"):
        if static is not None:
            src &= static
        coef = bg.get("coef_gray")
        if kind == "gradient" and coef is not None and len(coef) == 6:
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
            u, v = xx / w, yy / h
            c = [float(t) for t in coef]
            model = (c[0] + c[1] * u + c[2] * v + c[3] * u * u + c[4] * v * v + c[5] * u * v).astype(np.float32)
        elif kind == "solid":
            model = np.full((h, w), _bg_gray(bg), np.float32)
        rule = (f"{kind}: static, non-zone pixels matching the background model" if model is not None else
                f"{kind}: static, non-zone pixels (no background model)")
    elif kind == "image":
        if static is not None:
            src &= static
        rule = "image: static, non-zone pixels"
    else:
        if static is not None:
            src &= ~static
        rule = f"{kind}: non-static, non-zone pixels"
    return src, model, rule


def measure_box_from_raw(layout: Layout, overlays: Any, comp: Proxy, raw: Proxy, fm: Any, cfg: Any,
                         dlog: DecisionLog | None = None) -> dict:
    """The RAW-agreement box measurement of :func:`refine_box_from_raw` without re-analysing anything.

    Returns {'ok': bool, 'reason', 'box' (full-res Box), 'edges' (proxy), 'radius' (proxy), 'radius_kept',
    'raw_match' (bool [h, w]), 'frames', 'evidence', 'maps' (nI, nO, nV), 'canvas_votes' (int [h, w]: OUT
    votes where the competitor shows the canvas model and RAW would not), 'structure_votes' ((nSA, nTX) int
    [h, w]: pixel-frames where the competitor shows RAW's local structure, of those where RAW is locally
    textured; see :func:`_structure_agreement`), 'protected' (bool [h, w]: pixels inside the current box,
    RAW present, not mostly showing the canvas, and dynamic or mostly structure-agreeing — video a refined
    box must keep), 'frame_data'}."""
    dlog = dlog or null_dlog()
    w, h = int(comp.size[0]), int(comp.size[1])
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    if layout is None or layout.box is None or layout.mode != "boxed":
        return {"ok": False, "reason": "no boxed layout"}
    ks, sel_ev = _refine_frames(layout, comp, raw, fm, cfg)
    if len(ks) < 3:
        return {"ok": False, "reason": sel_ev.get("reason", f"only {len(ks)} confidently matched frame(s)"),
                "selection": sel_ev}
    cov_cur = box_coverage(layout, comp)
    base_fit = cov_cur >= 0.99
    static = None
    sm = getattr(layout, "static_mask_file", "")
    if sm and Path(sm).is_file():
        st_ = np.load(sm).astype(bool)
        static = st_ if st_.shape == (h, w) else None
    sig = float(_p(cfg, "REFINE_BLUR", REFINE_BLUR))
    frames = [f for f in (_refine_frame(k, comp, raw, fm, overlays, base_fit, static, sig) for k in ks)
              if f is not None]
    if len(frames) < 3:
        return {"ok": False, "reason": "RAW could not be fitted photometrically on enough frames", "selection": sel_ev}
    # what the temporal analysis proved: the layout's static mask, else the spread over the refine frames
    if static is not None:
        static_t, static_how = static, "layout static mask"
    else:
        thr = float(_cfg(cfg, "static_std_thresh", 2.0))
        static_t = np.std(np.stack([f["C"] for f in frames]).astype(np.float32), axis=0) < thr
        static_how = f"temporal std over the {len(frames)} refine frames < {thr:g}"
    dyn_in = (cov_cur > 0) & ~static_t              # video inside the current box
    src_bg, bg_model, bg_rule = _background_sources(layout, comp, static_t)
    tau_min = float(_p(cfg, "REFINE_TAU_MIN", REFINE_TAU_MIN))
    tau_k = float(_p(cfg, "REFINE_TAU_K", REFINE_TAU_K))
    disc_min = float(_p(cfg, "REFINE_DISC_MIN", REFINE_DISC_MIN))
    sol_tol = float(_p(cfg, "SOLID_TOL", SOLID_TOL))
    z_win = max(3, int(_p(cfg, "REFINE_ZNCC_WIN", REFINE_ZNCC_WIN)) | 1)
    z_min = float(_p(cfg, "REFINE_ZNCC_MIN", REFINE_ZNCC_MIN))
    z_std = float(_p(cfg, "REFINE_ZNCC_STD", REFINE_ZNCC_STD))
    z_gain = float(_p(cfg, "REFINE_ZNCC_GAIN_MIN", REFINE_ZNCC_GAIN_MIN))
    near_cur = _dilate(cov_cur > 0, 2)
    nI = np.zeros((h, w), np.int16)
    nO = np.zeros((h, w), np.int16)
    nV = np.zeros((h, w), np.int16)
    nBG = np.zeros((h, w), np.int16)
    nSA = np.zeros((h, w), np.int16)          # the competitor shows RAW's local structure (gain-invariant)
    nTX = np.zeros((h, w), np.int16)          # ... of the pixel-frames where RAW is locally textured
    taus = []
    no_bg = 0
    for f in frames:
        Cb, Rb, V, Ok = f["Cb"], f["Rb"], f["V"], f["Ok"]
        res = np.abs(Cb - Rb)
        fitm = base_fit & V & ~Ok
        tau = max(tau_min, tau_k * 1.4826 * float(np.median(res[fitm]))) if fitm.any() else tau_min
        f["tau"] = tau
        taus.append(round(tau, 2))
        agree = res <= tau
        sagree, textured = _structure_agreement(Cb, Rb, V, z_win, z_min, z_std, z_gain, tau)
        # background samples: outside the current box, no overlay, RAW absent or disagreeing, background pixels
        # only (never a zone; see _background_sources) — eroded, so the thin band of blurred box-edge pixels
        # (box content mixed with the canvas) never serves as background; with a background model, only the
        # pixels that show it in this frame
        cand = _erode_mask(~near_cur & ~Ok & (~V | ~agree) & src_bg, 3)
        if bg_model is not None:
            cand &= np.abs(Cb - bg_model) <= max(sol_tol, tau)
        B = _nearest_fill(Cb, cand)
        use = V & ~Ok
        sagree &= use
        # the competitor shows RAW's local structure under a different local gain / offset (caption gradient,
        # darkened lower third, inner shadow, feathered edge, vignette: the gain / offset fit is global): RAW
        # content, whatever the level test says
        out = use & ~agree & ~sagree
        if B is None:
            # (no background visible at all: a level agreement proves nothing about the box extent)
            no_bg += 1
            disc = np.zeros((h, w), bool)
            canvas = disc
        else:
            disc = np.abs(Rb - B) > max(disc_min, tau)
            canvas = disc & (np.abs(Cb - B) <= tau)     # the competitor shows the canvas where RAW would not
        # (structure agreement is evidence only where RAW itself differs from the canvas: next to a RAW
        # content edge that coincides with the box edge — letterbox bars cropped off on a canvas of their
        # level — a window straddling the edge correlates on the outside pixels too)
        nI += (use & (agree | sagree) & disc).astype(np.int16)
        # a disagreement on video the temporal analysis saw inside the box is a competitor effect or an
        # unmasked overlay, not the box edge: don't-care unless it shows the canvas
        nO += (out & (~dyn_in | canvas)).astype(np.int16)
        nBG += (out & canvas).astype(np.int16)
        nSA += sagree.astype(np.int16)
        nTX += (textured & use).astype(np.int16)
        nV += V.astype(np.int16)           # RAW present (a caption hiding a line makes it don't-care, not RAW-less)
        del f["Cb"], f["Rb"]
    nfr = len(frames)
    foot = _footprints(fm, [f["k"] for f in frames], raw, comp)
    shrink_frac = float(_p(cfg, "REFINE_SHRINK_FRAC", REFINE_SHRINK_FRAC))
    tol_fe = float(_p(cfg, "REFINE_FRAME_EDGE_TOL", REFINE_FRAME_EDGE_TOL))
    grow_min = int(_p(cfg, "REFINE_GROW_MIN_LINES", REFINE_GROW_MIN_LINES))
    b = layout.box
    cur = [b.x * rx, b.y * ry, (b.x + b.w) * rx, (b.y + b.h) * ry]           # proxy x0, y0, x1, y1
    cur_c = {sd: _to_canon(v, sd, w, h) for sd, v in zip(_SIDES, cur)}      # temporal edges, canonical
    ints = {"left": int(round(cur[0])), "top": int(round(cur[1])), "right": int(round(cur[2])),
            "bottom": int(round(cur[3]))}
    e_start = {sd: int(round(_to_canon(ints[sd], sd, w, h))) for sd in _SIDES}
    side_ev: dict[str, dict] = {}
    for _it in range(3):
        prev = dict(ints)
        for side in _SIDES:
            if side in ("left", "right"):
                a0, a1 = ints["top"], ints["bottom"]
            else:
                a0, a1 = ints["left"], ints["right"]
            band = (max(0, a0 + (a1 - a0) // 4), max(0, a1 - (a1 - a0) // 4))
            if band[1] - band[0] < 4:
                continue
            cls = _line_classes(nI, nO, nV, side, band, nfr)
            shr = _line_shrinkable(static_t, nBG, nV, side, band, shrink_frac, nSA, nTX)
            e0 = int(round(_to_canon(ints[side], side, w, h)))
            f_out = min(foot[side]) if foot else None
            e_c, action, stop, stop_kind = _scan_side(cls, e0, f_out, shr, grow_min)
            ints[side] = int(round(_from_canon(e_c, side, w, h)))
            side_ev[side] = {"band": list(band), "from": e0, "edge_canonical": e_c, "action": action,
                             "stop": stop, "stop_class": {None: None, _L_OUT: "out", _L_NORAW: "no_raw"}[stop_kind],
                             "cls": cls}
        if ints == prev:
            break
    if not (ints["right"] - ints["left"] > 8 and ints["bottom"] - ints["top"] > 8):
        return {"ok": False, "reason": f"degenerate measured box {ints}", "selection": sel_ev}
    edges_c: dict[str, float] = {}
    how_c: dict[str, str] = {}

    def grew(side: str) -> bool:
        """IN lines (the only lines that move an edge outward) prove video beyond the temporal edge."""
        return int(round(_to_canon(ints[side], side, w, h))) < e_start[side]

    def raw_frame_edge(side: str) -> float | None:
        """The box ends where RAW ends: the innermost RAW frame edge (the box cannot extend beyond it), unless
        RAW disagrees between it and the measured edge (the 3 lines next to either edge are blurred mixtures).
        Beyond the measured edge only when IN lines prove video outside the temporal edge: RAW content that
        equals the canvas (black letterbox bars on a black canvas) leaves the extent unobservable."""
        sev = side_ev.get(side)
        if not foot or sev is None or "cls" not in sev:
            return None
        e_int = int(round(_to_canon(ints[side], side, w, h)))
        f_in = max(foot[side])
        if f_in > e_int + tol_fe:
            return None
        if f_in < e_int - tol_fe and not grew(side):
            sev["raw_frame_edge_rejected"] = {"raw_frame_edge": round(float(f_in), 3),
                                              "reason": "no IN line beyond the temporal edge (unobservable extent)"}
            return None
        lo = int(max(0, math.ceil(f_in))) + 3
        gap = sev["cls"][lo:max(lo, e_int - 3)]
        if not (gap == _L_OUT).any():
            return float(min(f_in, e_int + 1.0))
        return None
    # 1. frame border; sides that GREW (IN lines beyond the temporal edge) until their outward scan ran into
    #    RAW-less lines (possibly across RAW content that equals the canvas, e.g. black letterbox bars on a
    #    black canvas): the RAW frame edge. (A side that did not grow is measured on the coverage map first:
    #    the competitor may crop a pixel or two of RAW, and the RAW frame edge right outside the box edge is
    #    no evidence that the box reaches it.)
    for side in _SIDES:
        e_int = int(round(_to_canon(ints[side], side, w, h)))
        if e_int <= 0:
            edges_c[side], how_c[side] = 0.0, "frame border"
        elif side_ev.get(side, {}).get("stop_class") == "no_raw" and grew(side):
            v = raw_frame_edge(side)
            if v is not None:
                edges_c[side], how_c[side] = v, "RAW frame edge"
    # 2. coverage map (unblurred) with the background = nearest background pixel >= 2 px outside the box known
    #    so far (the same background rule as the votes)
    rect = {sd: (_from_canon(edges_c[sd], sd, w, h) if sd in edges_c else float(ints[sd])) for sd in _SIDES}
    outside = np.ones((h, w), bool)
    outside[max(0, int(math.floor(rect["top"])) - 2):int(math.ceil(rect["bottom"])) + 2,
            max(0, int(math.floor(rect["left"])) - 2):int(math.ceil(rect["right"])) + 2] = False
    cov_min = float(_p(cfg, "REFINE_COV_MIN", REFINE_COV_MIN))
    num = np.zeros((h, w), np.float64)
    den = np.zeros((h, w), np.float64)
    for f in frames:
        C, R, V, Ok = f["C"], f["R"], f["V"], f["Ok"]
        cand = outside & ~Ok & src_bg
        if bg_model is not None:
            cand &= np.abs(C - bg_model) <= max(sol_tol, float(f["tau"]))
        B = _nearest_fill(C, cand)
        if B is None:
            continue
        d = R - B
        wgt = V & ~Ok & (np.abs(d) >= cov_min)
        num += np.where(wgt, (C - B) * d, 0.0)
        den += np.where(wgt, d * d, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        covm = np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)
    # 3. the other sides: sub-pixel coverage transition, else the RAW frame edge, else — a side the scan kept
    #    whose edge nothing measures — the temporal edge, else the integer edge
    for side in _SIDES:
        sev = side_ev.setdefault(side, {"band": None, "stop_class": None})
        if side not in edges_c:
            e_int = int(round(_to_canon(ints[side], side, w, h)))
            val: float | None = None
            how = "integer"
            if sev.get("band") is not None:
                e_sub, cev = _coverage_subpixel(_canon(covm, side), tuple(sev["band"]), e_int)
                sev["coverage"] = cev
                if e_sub is not None:
                    val, how = e_sub, "coverage"
            if val is None:
                val = raw_frame_edge(side)
                how = "RAW frame edge" if val is not None else how
            if val is None and e_int == e_start[side]:
                val, how = float(cur_c[side]), "temporal (not observable against RAW)"
            edges_c[side], how_c[side] = (float(e_int) if val is None else val), how
        sev["method"] = how_c[side]
        sev["value_canonical"] = round(edges_c[side], 4)
        sev.pop("cls", None)
    edges = (_from_canon(edges_c["left"], "left", w, h), _from_canon(edges_c["top"], "top", w, h),
             _from_canon(edges_c["right"], "right", w, h), _from_canon(edges_c["bottom"], "bottom", w, h))
    corners = [nm for nm, sx, sy in (("tl", "left", "top"), ("tr", "right", "top"), ("bl", "left", "bottom"),
                                     ("br", "right", "bottom")) if edges_c[sx] > 0.5 and edges_c[sy] > 0.5]
    Mc = np.clip(np.nan_to_num(covm, nan=0.0), -0.5, 1.5)
    radius, rev = _fit_radius(Mc, np.isfinite(covm), edges, corners)
    r_kept = "reason" in rev or not corners
    if r_kept:
        radius = float(b.corner_radius) * (rx + ry) / 2.0
    # full-res, snapped like the detected box
    fx0, fy0, fx1, fy1 = edges[0] / rx, edges[1] / ry, edges[2] / rx, edges[3] / ry
    sx0, sy0, sx1, sy1 = (_snap(v, 0.25) for v in (fx0, fy0, fx1, fy1))
    r_full = radius / ((rx + ry) / 2.0)
    r_s = float(b.corner_radius) if r_kept else (_snap(r_full, 0.35) if r_full > 0.5 else 0.0)
    new_box = Box(sx0, sy0, sx1 - sx0, sy1 - sy0, r_s)
    ev_tot = nI.astype(np.int32) + nO
    raw_match = (ev_tot > 0) & (nI.astype(np.int32) * 2 > ev_tot)
    # video a refined box must keep: inside the current box, RAW present, not mostly showing the canvas, and
    # either dynamic (the temporal analysis saw it move) or mostly showing RAW's local structure
    struct_mostly = (nTX > 0) & (nSA.astype(np.int32) * 2 >= nTX)
    protected = (cov_cur > 0) & (nV.astype(np.int32) * 2 > nfr) & ~(nBG.astype(np.int32) * 2 > nV) & \
        (~static_t | struct_mostly)
    evidence = {"frames": [f["k"] for f in frames], "raw_frames": [f["j"] for f in frames],
                "gain_offset": [[f["gain"], f["offset"]] for f in frames], "tau": taus, "selection": sel_ev,
                "sides": side_ev, "radius": rev, "radius_kept": bool(r_kept),
                "measured_full": [round(fx0, 3), round(fy0, 3), round(fx1, 3), round(fy1, 3)],
                "radius_measured_full": round(r_full, 3), "static": static_how, "background_samples": bg_rule,
                "frames_without_background": no_bg, "dynamic_in_box_px": int(dyn_in.sum()),
                "canvas_votes_in_box": int(nBG[cov_cur > 0].sum(dtype=np.int64)),
                "structure_votes": int(nSA.sum(dtype=np.int64)), "protected_px": int(protected.sum())}
    return {"ok": True, "box": new_box, "edges": edges, "radius": float(radius), "radius_kept": bool(r_kept),
            "raw_match": raw_match, "frames": [f["k"] for f in frames], "evidence": evidence,
            "maps": (nI, nO, nV), "canvas_votes": nBG, "structure_votes": (nSA, nTX), "protected": protected,
            "frame_data": frames}


def _outline_band(boxes: Sequence[Box], comp: Proxy, px: int) -> np.ndarray:
    """Bool [h, w]: pixels within ~``px`` proxy px of any of the boxes' outlines (the partially covered
    boundary pixels on either side included). Their votes mix both sides of an edge (blur, area average)."""
    w, h = int(comp.size[0]), int(comp.size[1])
    W, H = int(comp.full_size[0]), int(comp.full_size[1])
    edge = np.zeros((h, w), bool)
    for bx in boxes:
        cov = box_coverage(Layout(W, H, box=bx), comp)
        ins = cov >= 0.5
        edge |= (ins & ~_erode_mask(ins, 1)) | (_dilate(ins, 1) & ~ins) | ((cov > 0) & (cov < 1))
    return _dilate(edge, max(0, int(px)))


def _evidence_error(box: Box, comp: Proxy, nI: np.ndarray, nO: np.ndarray, exclude: np.ndarray | None = None) -> int:
    """RAW-agreement votes a box contradicts: disagreements inside + agreements outside (pixels of
    ``exclude`` — the band around the compared outlines — do not count)."""
    lay = Layout(int(comp.full_size[0]), int(comp.full_size[1]), box=box)
    inside = box_coverage(lay, comp) >= 0.5
    keep = np.ones(inside.shape, bool) if exclude is None else ~np.asarray(exclude, bool)
    return int(nO[inside & keep].sum(dtype=np.int64) + nI[~inside & keep].sum(dtype=np.int64))


def _refine_veto(old: Box, new: Box, comp: Proxy, m: dict, band: np.ndarray, cfg: Any) -> dict | None:
    """Reasons a measured box must not replace the temporal one although it explains the votes better:
    (1) the ring it drops holds video (``m['protected']``: RAW present, dynamic or showing RAW's local
    structure, not the canvas — a competitor effect on the video, not the box edge); (2) it covers rounded corners the competitor shows at the temporal box (RAW
    present, canvas shown). None when neither applies."""
    W, H = int(comp.full_size[0]), int(comp.full_size[1])
    cov_old = box_coverage(Layout(W, H, box=old), comp)
    cov_new = box_coverage(Layout(W, H, box=new), comp)
    prot = m.get("protected")
    ring = (cov_old >= 0.5) & (cov_new < 0.5) & ~band
    if prot is not None and ring.any():
        n_ring, n_prot = int(ring.sum()), int((ring & prot).sum())
        frac = float(_p(cfg, "REFINE_RING_DYN_FRAC", REFINE_RING_DYN_FRAC))
        if n_prot > max(4, frac * n_ring):
            return {"reason": "the measured box drops video the temporal analysis saw inside the box (dynamic "
                              "pixels that do not show the canvas: a competitor effect such as a caption gradient "
                              "or an inner shadow, not the box edge)",
                    "ring_px": n_ring, "dynamic_px": n_prot}
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    if float(old.corner_radius) * (rx + ry) / 2.0 > 1.0:
        rect = box_coverage(Layout(W, H, box=Box(old.x, old.y, old.w, old.h, 0.0)), comp)
        ears = (rect >= 1.0) & (cov_old <= 0.0)
        if ears.any():
            nI, nO, nV = m["maps"]
            o = int(nO[ears].sum(dtype=np.int64))
            i = int(nI[ears].sum(dtype=np.int64))
            v = int(nV[ears].sum(dtype=np.int64))
            covered = float((cov_new[ears] >= 0.5).mean())
            if v > 0 and o >= 0.5 * v and o > 3 * i and covered >= 0.5:
                return {"reason": "the measured box covers the rounded corners the competitor shows at the detected "
                                  "box (RAW present there, canvas shown)",
                        "corner_px": int(ears.sum()), "votes_out": o, "votes_in": i, "raw_present": v,
                        "covered_fraction": round(covered, 3)}
    return None


def refine_box_from_raw(layout: Layout, overlays: Any, comp: Proxy, raw: Proxy, fm: Any, cfg: Any,
                        cache: Cache | None, dlog: DecisionLog | None,
                        debug_dir: str | os.PathLike | None) -> tuple[Layout, bool]:
    """DESIGN §7 D2: re-measure the video box against the matched RAW frames (see the section comment).

    Returns ``(layout, changed)``. ``changed`` is True when an edge moved by more than REFINE_EDGE_PX or
    the radius by more than REFINE_RADIUS_PX (full-res px) AND the measured box explains the RAW agreement
    votes better than the current one away from both outlines AND neither veto of :func:`_refine_veto`
    applies (no dynamic video dropped, no rounded corner of the temporal box covered); the returned Layout
    is then a complete re-analysis with that box (statistics region, periods, background, border, zones,
    captions, static mask, and NEW initial overlay masks at ``layout.overlay_mask_file`` — the caller
    reloads them before re-running S5.2/S5.3). Otherwise the input layout is returned unchanged. Frames of
    fullscreen / split / PiP periods are never used. Writes ``debug_dir/layout_refine.png``; every decision
    is logged."""
    dlog = dlog or null_dlog()
    if layout is None or layout.box is None or layout.mode != "boxed" or fm is None or raw is None:
        dlog.record("layout", "box_refine", changed=False, reason="no boxed layout / frame map")
        return layout, False
    m = measure_box_from_raw(layout, overlays, comp, raw, fm, cfg, dlog)
    old = layout.box
    if not m.get("ok"):
        dlog.record("layout", "box_refine", changed=False, reason=m.get("reason"), selection=m.get("selection"))
        log.info("layout: box not re-measured against RAW (%s)", m.get("reason"))
        return layout, False
    new: Box = m["box"]
    d_edge = max(abs(new.x - old.x), abs(new.y - old.y), abs((new.x + new.w) - (old.x + old.w)),
                 abs((new.y + new.h) - (old.y + old.h)))
    d_r = abs(new.corner_radius - old.corner_radius)
    material = d_edge > float(_p(cfg, "REFINE_EDGE_PX", REFINE_EDGE_PX)) or \
        d_r > float(_p(cfg, "REFINE_RADIUS_PX", REFINE_RADIUS_PX))
    nI, nO, nV = m["maps"]
    band = _outline_band((old, new), comp, int(_p(cfg, "REFINE_BAND_PX", REFINE_BAND_PX)))
    err_old, err_new = _evidence_error(old, comp, nI, nO, band), _evidence_error(new, comp, nI, nO, band)
    changed = bool(material and err_new < err_old)
    veto = _refine_veto(old, new, comp, m, band, cfg) if changed else None
    if veto is not None:
        changed = False
    ev = {**m["evidence"], "old_box": old.to_dict(), "measured_box": new.to_dict(), "max_edge_change": round(d_edge, 3),
          "radius_change": round(d_r, 3), "votes_contradicted_old": err_old, "votes_contradicted_new": err_new,
          "outline_band_px": int(band.sum())}
    if veto is not None:
        ev["veto"] = veto
    if debug_dir is not None:
        try:
            _draw_refine_png(Path(debug_dir) / "layout_refine.png", m["frame_data"], nI, nO, nV, old, new, comp,
                             changed)
        except Exception as e:  # noqa: BLE001 - a debug image must never break the analysis
            log.warning("layout: debug/layout_refine.png failed: %s", e)
    if not changed:
        why = ("measured box agrees with the detected one" if not material else
               "measured box does not explain the RAW agreement better than the detected one"
               if veto is None else f"measured box rejected: {veto['reason']}")
        dlog.record("layout", "box_refine", changed=False, reason=why, evidence=ev)
        log.info("layout: box verified against RAW (%s; max edge change %.2f px, radius change %.2f px)", why,
                 d_edge, d_r)
        return layout, False
    grown = new.w * new.h > old.w * old.h
    why = ("box grown to the RAW-matching region (static content inside the video box)" if grown else
           "box shrunk to the RAW-matching region")
    dlog.record("layout", "box_refined", changed=True, reason=why, evidence=ev)
    log.info("layout: %s: x%.2f y%.2f w%.2f h%.2f r%.2f (was x%.2f y%.2f w%.2f h%.2f r%.2f)", why, new.x, new.y,
             new.w, new.h, new.corner_radius, old.x, old.y, old.w, old.h, old.corner_radius)
    notes = [f"video {why} (measured against the matched RAW frames): x{new.x:g} y{new.y:g} w{new.w:g} "
             f"h{new.h:g} r{new.corner_radius:g}; temporal activity alone gave x{old.x:g} y{old.y:g} w{old.w:g} "
             f"h{old.h:g} r{old.corner_radius:g}"]
    if m["radius_kept"]:
        notes.append("corner radius not observable against RAW (corners indistinguishable from the canvas): "
                     "kept from the temporal analysis")
    fb = _ForcedBox(new, tuple(float(v) for v in m["edges"]), float(m["radius"]), m["raw_match"],
                    {"refined_from": old.to_dict(), "reason": why, "frames": m["frames"],
                     "radius_kept": m["radius_kept"]}, notes)
    lay2, _ov2 = _analyze(comp, cfg, cache, debug_dir, dlog, fb)
    return lay2, True
