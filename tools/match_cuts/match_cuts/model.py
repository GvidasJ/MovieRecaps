"""Data model shared by every stage (DESIGN.md §3). All transforms: canonical ``geometry.Sim``
(CORNER convention, full-res). All frame positions: integer indices, half-open intervals."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .common import fps_str, parse_fps


# ---------------------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------------------

@dataclass
class StreamInfo:
    path: str
    role: str                              # 'raw' | 'competitor'
    container: str = ""
    vcodec: str = ""
    vprofile: str = ""
    pix_fmt: str = ""
    color_range: str = ""
    width: int = 0                         # coded width
    height: int = 0
    display_width: int = 0                 # after rotation + SAR normalisation
    display_height: int = 0
    sar: Fraction = Fraction(1)
    dar: Fraction = Fraction(0)
    rotation: int = 0                      # CLOCKWISE degrees to apply for display
    r_frame_rate: Fraction = Fraction(0)
    avg_frame_rate: Fraction = Fraction(0)
    fps: Fraction = Fraction(0)            # nominal rate used for the timeline
    nb_frames: int = 0                     # decoded frame count (full decode pass)
    duration: float = 0.0                  # nb_frames / fps
    container_duration: float = 0.0
    vfr: bool = False
    pts_jitter: float = 0.0                # max |pts delta - 1/fps| in frames
    v_start_time: float = 0.0
    first_pts_time: float = 0.0            # PTS (s) of the first decoded frame
    has_audio: bool = False
    acodec: str = ""
    a_sample_rate: int = 0
    a_channels: int = 0
    a_start_time: float = 0.0
    av_offset: float = 0.0                 # a_start_time - v_start_time
    edit_list: bool = False
    file_size: int = 0
    file_hash: str = ""
    ae_issues: list[str] = field(default_factory=list)   # reasons it is NOT AE-safe (empty = safe)
    pts_file: str = ""                     # .npy with decoded PTS (seconds, float64)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        for k in ("sar", "dar", "r_frame_rate", "avg_frame_rate", "fps"):
            d[k] = fps_str(Fraction(d[k]))
        return d

    @staticmethod
    def from_dict(d: dict) -> "StreamInfo":
        d = dict(d)
        for k in ("sar", "dar", "r_frame_rate", "avg_frame_rate", "fps"):
            if k in d:
                d[k] = parse_fps(d[k]) if str(d[k]) not in ("0", "0/0", "0/1") else Fraction(0)
        names = {f.name for f in dataclasses.fields(StreamInfo)}
        return StreamInfo(**{k: v for k, v in d.items() if k in names})


# ---------------------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------------------

@dataclass
class Proxy:
    """Memory-mapped grayscale analysis proxy of one AE-imported file.

    frames : np.memmap uint8 [N, h, w] (display orientation, square pixels, cv2 INTER_AREA)
    ratio  : (rx, ry) = (w / full_w, h / full_h) -- CORNER coords scale exactly by these
    pts    : float64 [N] presentation time (s) of each frame relative to stream start
    """
    role: str
    path: str                              # the media file (AE-imported copy)
    frames: Any                            # np.ndarray / np.memmap [rows, h, w] uint8
    full_size: tuple[int, int]             # (W, H) full-res display size
    ratio: tuple[float, float]
    fps: Fraction
    pts: np.ndarray                        # [n] seconds for every frame of the file (not only stored rows)
    n: int                                 # frame count of the FILE
    npy_path: str = ""
    index_map: Any = None                  # None = dense (row j = frame j); else int32 [n]: row or -1 (long RAW)

    @property
    def size(self) -> tuple[int, int]:
        return (int(self.frames.shape[2]), int(self.frames.shape[1]))

    @property
    def dense(self) -> bool:
        return self.index_map is None

    def has(self, j: int) -> bool:
        if j < 0 or j >= self.n:
            return False
        return self.index_map is None or int(self.index_map[j]) >= 0

    def get(self, j: int) -> np.ndarray:
        """Proxy image of frame j (KeyError if a sparse proxy does not hold it). ALWAYS use this
        (or has()) instead of indexing .frames directly, so long-RAW sparse proxies work."""
        if self.index_map is None:
            if j < 0 or j >= self.n:
                raise KeyError(j)
            return self.frames[j]
        row = int(self.index_map[j]) if 0 <= j < self.n else -1
        if row < 0:
            raise KeyError(j)
        return self.frames[row]

    # -- pickling (spawn worker pools, DESIGN §5 visual_match.parallel_map) -----------------------------
    def __getstate__(self) -> dict:
        """A proxy whose ``frames`` is a read-only memmap of a file (dense ``.npy`` = ``npy_path``, or the
        sparse store's ``.u8`` rows) pickles as that file reference (path, offset, dtype, shape), never as
        pixels: a spawned worker re-opens the same memmap (shared page cache) instead of receiving a copy.
        ``pts`` / ``index_map`` are small and pickled by value. In-memory proxies pickle their pixels."""
        d = dict(self.__dict__)
        ref = _memmap_ref(d.get("frames"))
        if ref is not None:
            d["frames"] = None
            d["_frames_ref"] = ref
        return d

    def __setstate__(self, d: dict) -> None:
        d = dict(d)
        ref = d.pop("_frames_ref", None)
        if ref is not None:
            d["frames"] = _open_memmap_ref(ref)
        self.__dict__.update(d)


def _memmap_ref(a: Any) -> dict | None:
    """(file, offset, dtype, shape, order) of a read-only memmap that maps a whole file region (not a
    view of one), else None (pickle by value)."""
    import mmap
    import os
    if not isinstance(a, np.memmap) or not isinstance(getattr(a, "base", None), mmap.mmap):
        return None
    fn = getattr(a, "filename", None)
    if not fn or getattr(a, "mode", None) != "r" or a.size == 0 or not os.path.isfile(fn):
        return None
    if a.flags.c_contiguous:
        order = "C"
    elif a.flags.f_contiguous:
        order = "F"
    else:  # pragma: no cover - memmaps of a file region are always contiguous
        return None
    return {"file": str(fn), "offset": int(a.offset), "dtype": a.dtype.str,
            "shape": tuple(int(s) for s in a.shape), "order": order}


def _open_memmap_ref(ref: dict) -> np.memmap:
    import os
    fn, off, dt, shape = ref["file"], int(ref["offset"]), np.dtype(ref["dtype"]), tuple(ref["shape"])
    need = off + int(np.prod(shape, dtype=np.int64)) * dt.itemsize
    if not os.path.isfile(fn) or os.path.getsize(fn) < need:
        raise FileNotFoundError(f"Proxy: memmapped frames {fn} are missing or truncated (need {need} bytes)")
    return np.memmap(fn, dtype=dt, mode="r", offset=off, shape=shape, order=ref.get("order", "C"))


# ---------------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------------

@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    corner_radius: float = 0.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Box":
        return Box(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]), float(d.get("corner_radius", 0.0)))

    def scaled(self, rx: float, ry: float) -> "Box":
        return Box(self.x * rx, self.y * ry, self.w * rx, self.h * ry, self.corner_radius * (rx + ry) / 2)

    def int_roi(self) -> tuple[int, int, int, int]:
        """Integer (x, y, w, h) covering the box (floor/ceil)."""
        import math
        x0, y0 = int(math.floor(self.x)), int(math.floor(self.y))
        x1, y1 = int(math.ceil(self.x + self.w)), int(math.ceil(self.y + self.h))
        return x0, y0, x1 - x0, y1 - y0


@dataclass
class Zone:
    type: str                              # header|logo|title|captions|watermark|sticker|progress|other
    x: float
    y: float
    w: float
    h: float
    comp_in: int | None = None             # None = whole duration (static)
    comp_out: int | None = None
    static: bool = True
    text: str = ""                         # optional description
    notes: str = ""


@dataclass
class LayoutPeriod:
    comp_in: int
    comp_out: int
    mode: str                              # 'boxed' | 'fullscreen' | 'split' | 'pip'
    box: Box | None = None


@dataclass
class Layout:
    comp_w: int
    comp_h: int
    mode: str = "boxed"                    # dominant layout: 'boxed' | 'fullscreen' | ...
    box: Box | None = None                 # full-res competitor px (CORNER convention)
    canvas_bg: str = "#000000"
    background: dict = field(default_factory=lambda: {"type": "solid", "color": "#000000"})
    zones: list[Zone] = field(default_factory=list)
    periods: list[LayoutPeriod] = field(default_factory=list)
    extra_regions: list[Box] = field(default_factory=list)   # further video regions (split/PiP) - warned
    static_mask_file: str = ""             # .npy bool [h, w] at competitor proxy res (True = static)
    overlay_mask_file: str = ""            # .npz per-frame packed overlay masks (see layout.OverlayMasks)
    proxy_ratio: tuple[float, float] = (1.0, 1.0)
    captions: list[dict] = field(default_factory=list)       # [{comp_in, comp_out, x, y, w, h}]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Layout":
        d = dict(d)
        d["box"] = Box.from_dict(d["box"]) if d.get("box") else None
        d["zones"] = [Zone(**z) for z in d.get("zones", [])]
        d["periods"] = [LayoutPeriod(p["comp_in"], p["comp_out"], p["mode"],
                                     Box.from_dict(p["box"]) if p.get("box") else None) for p in d.get("periods", [])]
        d["extra_regions"] = [Box.from_dict(b) for b in d.get("extra_regions", [])]
        d["proxy_ratio"] = tuple(d.get("proxy_ratio", (1.0, 1.0)))
        names = {f.name for f in dataclasses.fields(Layout)}
        return Layout(**{k: v for k, v in d.items() if k in names})


# ---------------------------------------------------------------------------------------
# Per-frame mapping m(k)
# ---------------------------------------------------------------------------------------

class Status:
    UNKNOWN = -1
    NONE = 0          # no match anywhere (NOT-IN-RAW candidate)
    MATCH = 1         # mapped to a RAW frame
    BLEND = 2         # transition frame (mix of two sources) -- set by segment.py
    UNIFORM = 3       # (near-)uniform frame in the video region: dip / flash


FRAME_MAP_FIELDS: dict[str, tuple[type, Any]] = {
    "status": (np.int8, Status.UNKNOWN),
    "raw": (np.int32, -1),        # best RAW frame index
    "raw_lo": (np.int32, -1),     # inclusive range of RAW frames indistinguishable from `raw`
    "raw_hi": (np.int32, -1),     #   (ambiguous-identical); raw_lo == raw == raw_hi when unique
    "score": (np.float32, np.nan),  # masked ZNCC of the best RAW frame
    "second": (np.float32, np.nan), # best score among RAW frames outside [raw_lo, raw_hi] (any hypothesis)
    "margin": (np.float32, np.nan), # score - second
    "flip": (np.bool_, False),
    "s": (np.float64, np.nan),      # canonical Sim (full-res, CORNER) for this frame
    "theta": (np.float64, np.nan),  # degrees
    "tx": (np.float64, np.nan),
    "ty": (np.float64, np.nan),
    "track": (np.int32, -1),        # hypothesis / track id
    "inliers": (np.int32, -1),      # RANSAC inliers when a keypoint search was run on this frame
    "conf": (np.float32, 0.0),      # [0, 1] confidence
    "mean": (np.float32, np.nan),   # mean luma of the video region (for UNIFORM / dips)
    "std": (np.float32, np.nan),    # std of luma in the video region
    "low_margin": (np.bool_, False),  # best vs neighbours within noise (NOT an ambiguity exemption)
    "soft_lo": (np.int32, -1),      # soft range for the phase LP: {j : S_k(j) >= max S_k - delta_k}
    "soft_hi": (np.int32, -1),      #   (inclusive; always contains [raw_lo, raw_hi])
    "cand_j0": (np.int32, -1),      # RAW index of cand[:, 0]
    "widened": (np.bool_, False),   # the search window had to be extended (argmax was on the edge)
    "tie": (np.bool_, False),       # timing-tie frame (phase LP slack < 1e-4 frame) - set by segment.py
    "confounded": (np.bool_, False),  # pure pan: time and translation trade off (refine step 2)
}

CAND_W = 15   # per-frame candidate score vector length stored in FrameMap.cand (RAW cand_j0 .. cand_j0+14)


class FrameMap:
    """Column store for m(k): one entry per competitor frame. Save/load as .npz."""

    def __init__(self, n: int, data: dict[str, np.ndarray] | None = None):
        object.__setattr__(self, "n", int(n))
        if data is None:
            data = {k: np.full(self.n, v, dtype=t) for k, (t, v) in FRAME_MAP_FIELDS.items()}
        else:
            for k, (t, v) in FRAME_MAP_FIELDS.items():
                if k not in data:
                    data[k] = np.full(self.n, v, dtype=t)
        if "cand" not in data:
            data["cand"] = np.full((self.n, CAND_W), np.nan, dtype=np.float32)
        object.__setattr__(self, "d", data)

    def __setattr__(self, name: str, value) -> None:
        # fm.status = array  ->  writes into the column store (so save()/load() keep it)
        if name in FRAME_MAP_FIELDS or name == "cand":
            d = self.__dict__["d"]
            t = d[name].dtype
            v = np.asarray(value, dtype=t)
            if v.shape != d[name].shape:
                raise ValueError(f"FrameMap.{name}: shape {v.shape} != {d[name].shape}")
            d[name] = v.copy()
        else:
            object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> np.ndarray:
        d = self.__dict__.get("d")
        if d is not None and name in d:
            return d[name]
        raise AttributeError(name)

    def sim(self, k: int):
        from .geometry import Sim
        return Sim(float(self.d["s"][k]), float(self.d["theta"][k]), float(self.d["tx"][k]), float(self.d["ty"][k]))

    def set_sim(self, k: int, sim) -> None:
        self.d["s"][k], self.d["theta"][k], self.d["tx"][k], self.d["ty"][k] = sim.s, sim.theta_deg, sim.tx, sim.ty

    def cand_scores(self, k: int) -> tuple[int, np.ndarray]:
        """(j0, scores[CAND_W]) candidate score vector of frame k (NaN where not evaluated)."""
        return int(self.d["cand_j0"][k]), self.d["cand"][k]

    def save(self, path: str | Path) -> None:
        np.savez_compressed(path, n=np.array(self.n), **self.d)

    @staticmethod
    def load(path: str | Path) -> "FrameMap":
        with np.load(path, allow_pickle=False) as z:
            n = int(z["n"])
            data = {k: z[k].copy() for k in z.files if k != "n"}
        return FrameMap(n, data)

    def copy(self) -> "FrameMap":
        return FrameMap(self.n, {k: v.copy() for k, v in self.d.items()})


# ---------------------------------------------------------------------------------------
# Audio hints (Stage 5.1)
# ---------------------------------------------------------------------------------------

@dataclass
class AudioHints:
    """One row per competitor audio window. raw_t is the RAW time matching comp_t (window centre)."""
    comp_t: np.ndarray                     # float64 seconds (window centre)
    raw_t: np.ndarray                      # float64 seconds, NaN when no candidate beats the null level
    speed: np.ndarray                      # float64, time-scale v of best match (1.0 default)
    conf: np.ndarray                       # float32 peak / max(second peak outside ±0.3 s, null level); < 1 = no evidence, 0 = no candidate
    psr: np.ndarray                        # float32 peak-to-sidelobe ratio
    peak: np.ndarray                       # float32 normalised correlation peak
    window: float = 1.0
    hop: float = 0.25
    wave_peak: np.ndarray | None = None    # float32 waveform NCC of the sample-precise refinement (NaN: not refined)

    def __post_init__(self) -> None:
        if self.wave_peak is None:
            self.wave_peak = np.full(np.shape(self.comp_t), np.nan, np.float32)

    def confident(self, min_conf: float = 1.5) -> np.ndarray:
        return np.isfinite(self.raw_t) & (self.conf >= min_conf)

    def save(self, path: str | Path) -> None:
        np.savez_compressed(path, comp_t=self.comp_t, raw_t=self.raw_t, speed=self.speed, conf=self.conf,
                            psr=self.psr, peak=self.peak, window=np.array(self.window), hop=np.array(self.hop),
                            wave_peak=self.wave_peak)

    @staticmethod
    def load(path: str | Path) -> "AudioHints":
        with np.load(path) as z:
            return AudioHints(z["comp_t"], z["raw_t"], z["speed"], z["conf"], z["psr"], z["peak"],
                              float(z["window"]), float(z["hop"]),
                              z["wave_peak"] if "wave_peak" in z.files else None)

    @staticmethod
    def empty() -> "AudioHints":
        e = np.zeros(0)
        return AudioHints(e, e.copy(), e.copy(), e.astype(np.float32), e.astype(np.float32), e.astype(np.float32))


# ---------------------------------------------------------------------------------------
# Edit decision list
# ---------------------------------------------------------------------------------------

@dataclass
class Transition:
    type: str                              # 'crossfade' | 'dip_black' | 'dip_white' | 'dip_color'
    duration_frames: int                   # overlap length in competitor frames
    alpha: list[float] = field(default_factory=list)   # incoming opacity per overlap frame (0..1)
    color: str | None = None               # for dips
    notes: str = ""


@dataclass
class Segment:
    id: int
    type: str                              # 'raw' | 'not_in_raw' | 'dip' | 'flash'  (freeze/reverse/ramp = raw + remap)
    comp_in: int
    comp_out: int                          # half-open
    raw_in_frame: int | None = None        # RAW frame shown at comp_in
    raw_in_seconds: float | None = None    # phase-solved continuous RAW time at comp_in (AE uses this)
    raw_out_frame: int | None = None       # RAW frame shown at comp_out - 1 (inclusive, for humans)
    speed: float = 1.0                     # Δraw s / Δcomp s ; 0 = freeze ; < 0 reverse
    speed_measured: float | None = None    # before snapping
    speed_range: list[float] | None = None # feasible [vmin, vmax]
    flip_h: bool = False
    transform: dict | None = None          # Sim.to_dict()  (constant framing, or first key)
    transform_keys: list[dict] = field(default_factory=list)   # [{comp_frame, scale, rotation_deg, tx, ty}]
    easing: str = "linear"                 # informational: 'linear' | 'ease_in' | 'ease_out' | 'ease_in_out'
    time_remap_keys: list[dict] = field(default_factory=list)  # [{comp_frame(float ok), raw_seconds}] for freeze/reverse/ramp
    transition_in: dict | None = None      # Transition as dict
    transition_out: dict | None = None
    audio: dict = field(default_factory=lambda: {
        "in_offset_frames": 0, "out_offset_frames": 0,   # audio range = [comp_in+in, comp_out+out)
        "pitch_preserved": None, "lag_ms": None, "corr": None, "exception": None})
    time_mode: str = "stretch"             # stretch | remap  (remap <=> time_remap_keys non-empty)
    retime: str = "none"                   # none | frame_blend | optical_flow
    uncertain: bool = False
    unsnapped: bool = False                # speed could not be snapped to a common/dominant value
    cut_ambiguity: list[int] | None = None # [a, b]: cut can be anywhere in [a, b] (speed-only change)
    tie_frames: list[int] = field(default_factory=list)       # timing-tie frames (phase slack < 1e-4)
    low_margin_frames: list[int] = field(default_factory=list)
    ae_margin_ms: float | None = None      # half-width of the feasible raw_in interval (ms)
    region: int = 0                        # video region index (multi-region layouts)
    box: dict | None = None                # Box in force during the segment (None = layout.box)
    confidence: float = 0.0
    ambiguous_frames: list[int] = field(default_factory=list)  # competitor frames with ambiguous-identical RAW
    raw_in_interval: list[float] | None = None                 # feasible raw_in interval (floor rule)
    raw_in_interval_both: list[float] | None = None            # feasible under floor AND round rules
    color: str | None = None               # dip / flash colour
    label: str = ""                        # e.g. NOT-IN-RAW placeholder label
    notes: str = ""

    @property
    def length(self) -> int:
        return self.comp_out - self.comp_in

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Segment":
        names = {f.name for f in dataclasses.fields(Segment)}
        return Segment(**{k: v for k, v in d.items() if k in names})


@dataclass
class Cutlist:
    version: int
    competitor: dict                       # {file, width, height, fps, frames, ...}
    raw: dict                              # {file, width, height, fps, frames, conformed, ...}
    layout: dict                           # Layout.to_dict() subset + {'mode': LAYOUT_MODE}
    segments: list[Segment]
    overlays_detected: list[dict] = field(default_factory=list)
    added_audio: list[dict] = field(default_factory=list)
    audio: dict = field(default_factory=dict)          # per-run audio notes (pitch, replaced audio, ...)
    settings: dict = field(default_factory=dict)       # layout_mode, comp_size, fps_mode, ...
    warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)     # tool version, input hashes, parameters

    @property
    def comp_fps(self) -> Fraction:
        return parse_fps(self.competitor["fps"])

    @property
    def raw_fps(self) -> Fraction:
        return parse_fps(self.raw["fps"])

    def to_dict(self) -> dict:
        return {
            "version": self.version, "competitor": self.competitor, "raw": self.raw, "layout": self.layout,
            "segments": [s.to_dict() for s in self.segments], "overlays_detected": self.overlays_detected,
            "added_audio": self.added_audio, "audio": self.audio, "settings": self.settings,
            "warnings": self.warnings, "provenance": self.provenance,
        }

    @staticmethod
    def from_dict(d: dict) -> "Cutlist":
        return Cutlist(d["version"], d["competitor"], d["raw"], d["layout"],
                       [Segment.from_dict(s) for s in d["segments"]], d.get("overlays_detected", []),
                       d.get("added_audio", []), d.get("audio", {}), d.get("settings", {}),
                       d.get("warnings", []), d.get("provenance", {}))

    @staticmethod
    def load(path: str | Path) -> "Cutlist":
        from .common import load_json
        return Cutlist.from_dict(load_json(path))

    def save(self, path: str | Path) -> None:
        from .common import dump_json
        dump_json(self.to_dict(), path)


def cutlist_layout(layout: "Layout", layout_mode: str) -> dict:
    """The cutlist.json 'layout' block (DESIGN §3). mode = the requested LAYOUT_MODE; layout_kind = what
    was detected in the competitor. background = type string (prompt schema), details alongside."""
    bg = layout.background or {"type": "solid", "color": layout.canvas_bg}
    return {
        "mode": layout_mode,
        "layout_kind": layout.mode,
        "canvas_bg": layout.canvas_bg,
        "box": layout.box.to_dict() if layout.box else None,
        "background": bg.get("type", "solid"),
        "background_detail": bg,
        "zones": [dataclasses.asdict(z) for z in layout.zones],
        "periods": [dataclasses.asdict(p) for p in layout.periods],
        "regions": [b.to_dict() for b in layout.extra_regions],
        "captions": layout.captions,
        "notes": layout.notes,
    }
