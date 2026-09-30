#!/usr/bin/env python3
"""Synthetic RAW + competitor pair with a MEASURED ground truth (DESIGN.md §6, prompt Stage 1).

Everything visible in the two videos is made with ffmpeg filtergraphs (never with our own renderer),
so the transform / timing conventions of match_cuts are cross-checked instead of cancelling out:

* ``raw.mp4``        12 distinct generator shots (each rendered in its own ffmpeg process to a lossless
                     NUT intermediate, max 3 concurrent), concatenated, then once: a RAW-anchored grid
                     texture + a 5-digit frame counter inside the SAFE REGION; unique modulated audio.
* ``id.mp4``         512x64 lossless ID video whose frame n carries n as a 16-bit block code (top row code,
                     bottom row complement, duplicated in both 256-px halves). Same PTS as raw.mp4.
* ``competitor.mp4`` 9:16 edit: every segment is ONE timing chain ``-ss (j-0.5)/fps -i SRC ->
                     setpts=(PTS-STARTPTS)/v[+phase],fps=30,trim=end_frame=N`` followed (real chain only) by
                     whitelisted 1:1 geometry filters (hflip, scale, crop exact=1, perspective), rendered to
                     lossless box-size intermediates (the one FULLSCREEN chain: canvas-size, the RAW cover-
                     scaled to the whole canvas), then xfade / pad onto the canvas / concat, rounded-box
                     layout (frame.png; during the fullscreen chain only its glyph layer, so the title / logo
                     stay on top of the video), word-by-word captions; audio in a separate graph (each
                     chain's audio starts at the NLE in-point = the LOWER bound of its floor-rule raw_in
                     interval, i.e. at the first RAW frame's boundary), muxed with ``-c:v copy``.
* ``truth.json``     MEASURED: every timing chain is applied to id.mp4 and decoded; geometry truth is computed
                     by this module's own numpy code (independent of match_cuts.geometry) and VERIFIED by
                     pushing a calibration texture through each segment's exact geometry filter string.
                     Self-checks at the DESIGN proxy sizes (truth frame beats j±1, j±2 by >= 0.01 masked ZNCC
                     under ±0.5 % scale / ±2 px perturbation; >= 50 RANSAC inliers per shot/crop) must pass
                     before truth.json is written.

API: ``make_synthetic(out_dir, profile='full'|'mini', force=False) -> dict`` (cached, deterministic).
CLI: ``python tests/synth.py --profile mini --out work/synthetic/mini [--force] [--keep-build]``.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

log = logging.getLogger("match_cuts.synth")

SYNTH_VERSION = 2                     # 2: NLE audio in-points (D8), fullscreen chain
RAW_FPS = Fraction(30000, 1001)
COMP_FPS = Fraction(30)
RAW_TB = Fraction(1, 30000)           # -video_track_timescale 30000 on raw.mp4 and id.mp4
TICKS_PER_COMP_FRAME = 1000           # 1/30 s in RAW_TB ticks
AUDIO_SR = 48000
SAMPLES_PER_COMP_FRAME = AUDIO_SR // 30   # 1600
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
MAX_PARALLEL = 3                      # max concurrent ffmpeg processes (shared machine)
ID_W, ID_H = 512, 64
GEOMETRY_WHITELIST = ("hflip", "scale", "crop", "perspective", "pad", "format", "setsar")

# pinned encoder settings (byte-stable output: fixed thread count, bitexact container)
X264_RAW = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-bf", "3", "-g", "250", "-threads", "4",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "30000"]
X264_COMP = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-bf", "3", "-g", "60", "-threads", "4",
             "-pix_fmt", "yuv420p", "-video_track_timescale", "15360"]
X264_ID = ["-c:v", "libx264", "-qp", "0", "-preset", "veryfast", "-threads", "4", "-pix_fmt", "yuv420p",
           "-video_track_timescale", "30000"]
FFV1 = ["-c:v", "ffv1", "-level", "3", "-g", "1", "-threads", "1"]
BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1"]

# DESIGN proxy sizes / scoring parameters (config.Config defaults; read from it when importable)
_CFG_DEFAULTS = {"raw_proxy_width": 640, "comp_proxy_scale": 0.5, "comp_proxy_max_width": 640,
                 "score_blur": 1.0, "sift_nfeatures": 500, "lowe_ratio": 0.75, "ransac_reproj_px": 3.0}

# self-check thresholds (DESIGN §6) -- fix the synthetic design, never these
SELF_MARGIN_MIN = 0.01
SELF_INLIERS_MIN = 50
CALIB_POS_TOL = 0.25                  # px
CALIB_SCALE_TOL = 0.0005              # 0.05 %


# =====================================================================================================
# Profiles
# =====================================================================================================

@dataclass(frozen=True)
class Framing:
    """Static framing inside the video box: cover-scale x zoom, centred crop + (dx, dy) offset
    (offsets are given in FULL-profile pixels and scaled with the profile)."""
    zoom: float = 1.0
    dx: int = 0
    dy: int = 0


@dataclass(frozen=True)
class ChainSpec:
    """One timing chain = one competitor segment (the punch-in chain yields two truth segments)."""
    kind: str                         # normal|hook|jump_cut|flip|pushin|speed|crossfade|reuse|not_in_raw|punchin|
    #                                   fullscreen (RAW cover-scaled to the WHOLE canvas; title/logo on top)
    shot: int = -1                    # RAW shot index (-1: NOT-IN-RAW insert)
    off: int = 0                      # RAW start frame inside the shot
    n: int = 30                       # competitor frames produced by the chain (incl. a crossfade overlap)
    speed: str = "1"                  # decimal literal used verbatim in setpts
    flip: bool = False
    framing: Framing = Framing()
    push_end: float | None = None     # push-in: linear zoom 1 -> push_end over the chain
    punch_at: int | None = None       # punch-in: local frame where the zoom steps to punch_zoom
    punch_zoom: float = 1.25
    xfade: int = 0                    # this chain crossfades into the next one over `xfade` frames
    note: str = ""


@dataclass(frozen=True)
class ShotSpec:
    name: str
    graph: str                        # filtergraph template producing one video stream (placeholders below)
    skip: int = 0                     # generator frames skipped before the shot starts
    tags: tuple = ()                  # e.g. ('mandelbrot',) -> never used for flip / push-in / punch-in
    layer: tuple | None = None        # (seed, opacity, cell divisor): translucent Game-of-Life texture layer


@dataclass(frozen=True)
class Profile:
    name: str
    raw_w: int
    raw_h: int
    shot_len: int
    comp_w: int
    comp_h: int
    box: tuple                        # (x, y, w, h, radius) competitor px
    chains: tuple
    unit: float                       # length unit relative to the full profile (1.0 / 0.5)
    caption_seed: int = 7
    n_shots: int = 12

    @property
    def raw_frames(self) -> int:
        return self.n_shots * self.shot_len

    def u(self, v: float) -> int:
        """A full-profile length scaled to this profile (rounded to an int)."""
        return int(round(v * self.unit))


# Smooth generators (bars, test patterns, fractals) change too little between frames to be matched
# frame-exactly at the proxy sizes (self-check margins 0.003-0.007); they get a translucent, seeded
# Game-of-Life texture layer (cells >= 8 px) that changes every frame and adds SIFT texture.
# Pure Game-of-Life shots use a fixed 160x90 grid (cells ~4 px in the 640-px RAW proxy of either profile;
# finer cells leave too few SIFT inliers at the proxy size).
SHOTS: tuple[ShotSpec, ...] = (
    ShotSpec("testsrc2+life", "testsrc2=s={W}x{H}:r={R}", skip=30, layer=(101, 0.35, 8)),
    ShotSpec("life_a", "life=s={LW}x{LH}:r={R}:seed=11:ratio=0.3:mold=8:life_color=#ffe040:death_color=#101840:"
                       "mold_color=#803020,scale={W}:{H}:flags=neighbor", skip=60),
    ShotSpec("smpte_overlay+life", "smptehdbars=s={W}x{H}:r={R}[b];testsrc2=s={OW}x{OH}:r={R},hue=h=200[o];"
                                   "[b][o]overlay=x='{OX}+{AX}*sin(2*PI*t/3)':y='{OY}+{AY}*cos(2*PI*t/2.3)'",
             layer=(202, 0.3, 8)),
    ShotSpec("mandelbrot_a+life", "mandelbrot=s={W2}x{H2}:r={R},scale={W}:{H}:flags=bicubic",
             tags=("mandelbrot",), layer=(303, 0.3, 8)),
    ShotSpec("testsrc+life", "testsrc=s={W}x{H}:r={R}", skip=45, layer=(404, 0.35, 8)),
    ShotSpec("life_b", "life=s={LW}x{LH}:r={R}:rule=B36/S23:seed=21:ratio=0.4:life_color=#40ff80:"
                       "death_color=#200010,scale={W}:{H}:flags=neighbor", skip=40),
    # gradients: explicit colours + line (its default 'random' colours ignore `seed` -> not reproducible)
    ShotSpec("gradients+life", "gradients=s={W}x{H}:r={R}:speed=0.02:seed=3:n=4:c0=0x2050c0:c1=0xe0a020:"
                               "c2=0x20a060:c3=0xc03070:x0={GX0}:y0={GY0}:x1={GX1}:y1={GY1}", layer=(606, 0.4, 8)),
    ShotSpec("testsrc2_mosaic+life", "testsrc2=s={W4}x{H4}:r={R},split=4[a][b][c][d];[b]hue=h=90[b2];"
                                     "[c]hue=h=180,hflip[c2];[d]hue=h=270,vflip[d2];[a][b2]hstack[t];"
                                     "[c2][d2]hstack[u];[t][u]vstack,scale={W}:{H}:flags=bicubic", skip=90,
             layer=(707, 0.3, 8)),
    ShotSpec("mandelbrot_b+life", "mandelbrot=s={W2}x{H2}:r={R}:start_x=-0.743643887037158:"
                                  "start_y=-0.131825904205311:start_scale=0.3:end_scale=0.01:end_pts=600:"
                                  "outer=iteration_count,scale={W}:{H}:flags=bicubic",
             tags=("mandelbrot",), layer=(808, 0.3, 8)),
    ShotSpec("life_c", "life=s={LW}x{LH}:r={R}:seed=31:ratio=0.5:mold=20:life_color=#ff4040:"
                       "death_color=#003040:mold_color=#00a0a0,scale={W}:{H}:flags=neighbor", skip=60),
    ShotSpec("testsrc2_rotating+life", "testsrc2=s={W}x{H}:r={R},hue=h=150,rotate=a=0.35*t:c=0x303030",
             skip=15, layer=(1010, 0.3, 8)),
    ShotSpec("pal100bars_mandel_inset+life", "pal100bars=s={W}x{H}:r={R}[b];mandelbrot=s={OW}x{OH}:r={R}:"
                                             "start_scale=1.5:end_scale=0.2:end_pts=300:outer=iteration_count[o];"
                                             "[b][o]overlay=x='{OX}+{AX}*cos(2*PI*t/2.7)':"
                                             "y='{OY}+{AY}*sin(2*PI*t/3.1)'", layer=(1111, 0.35, 8)),
)

_FULL_CHAINS = (
    ChainSpec("hook", shot=10, off=200, n=75, note="out-of-order hook from late in RAW"),
    ChainSpec("normal", shot=0, off=60, n=90),
    ChainSpec("jump_cut", shot=0, off=160, n=75, note="same-shot jump cut (skips ~10 RAW frames)"),
    ChainSpec("normal", shot=1, off=50, n=85),
    ChainSpec("flip", shot=2, off=50, n=70, flip=True),
    ChainSpec("fullscreen", shot=10, off=350, n=30, note="fullscreen: RAW covers the whole canvas, title/logo on top"),
    ChainSpec("normal", shot=3, off=50, n=80),
    ChainSpec("jump_cut", shot=3, off=140, n=60, note="same-shot jump cut (skips ~10 RAW frames)"),
    ChainSpec("pushin", shot=4, off=50, n=100, push_end=1.12),
    ChainSpec("speed", shot=5, off=50, n=66, speed="1.1"),
    ChainSpec("normal", shot=6, off=60, n=81, xfade=6, note="crossfade out (A)"),
    ChainSpec("crossfade", shot=7, off=60, n=80, note="crossfade in (B)"),
    ChainSpec("reuse", shot=1, off=70, n=50, framing=Framing(1.12, -120, -30), note="re-uses RAW of segment 4"),
    ChainSpec("not_in_raw", n=30),
    ChainSpec("normal", shot=8, off=50, n=90),
    ChainSpec("punchin", shot=9, off=50, n=130, punch_at=70, punch_zoom=1.25),
    ChainSpec("normal", shot=11, off=50, n=85),
    ChainSpec("normal", shot=4, off=300, n=70, framing=Framing(1.06, 60, 30)),
    ChainSpec("normal", shot=10, off=50, n=80),
    ChainSpec("normal", shot=0, off=300, n=90),
    ChainSpec("normal", shot=6, off=300, n=60),
)

_MINI_CHAINS = (
    ChainSpec("hook", shot=10, off=70, n=30, note="out-of-order hook from late in RAW"),
    ChainSpec("normal", shot=0, off=20, n=36),
    ChainSpec("jump_cut", shot=0, off=62, n=30, note="same-shot jump cut (skips ~6 RAW frames)"),
    ChainSpec("normal", shot=1, off=10, n=36),
    ChainSpec("flip", shot=2, off=10, n=30, flip=True),
    ChainSpec("fullscreen", shot=10, off=110, n=30, note="fullscreen: RAW covers the whole canvas, title/logo on top"),
    ChainSpec("normal", shot=3, off=10, n=32),
    ChainSpec("jump_cut", shot=3, off=48, n=28, note="same-shot jump cut (skips ~6 RAW frames)"),
    ChainSpec("pushin", shot=4, off=10, n=45, push_end=1.12),
    ChainSpec("speed", shot=5, off=10, n=33, speed="1.1"),
    ChainSpec("normal", shot=6, off=10, n=36, xfade=6, note="crossfade out (A)"),
    ChainSpec("crossfade", shot=7, off=10, n=36, note="crossfade in (B)"),
    ChainSpec("reuse", shot=1, off=20, n=24, framing=Framing(1.12, -120, -30), note="re-uses RAW of segment 4"),
    ChainSpec("not_in_raw", n=30),
    ChainSpec("punchin", shot=9, off=10, n=60, punch_at=30, punch_zoom=1.25),
    ChainSpec("normal", shot=11, off=10, n=36),
    ChainSpec("normal", shot=8, off=10, n=30, framing=Framing(1.06, 60, 30)),
)

PROFILES: dict[str, Profile] = {
    "full": Profile("full", 1920, 1080, 450, 1080, 1920, (60, 460, 960, 1000, 40), _FULL_CHAINS, 1.0),
    "mini": Profile("mini", 960, 540, 150, 540, 960, (30, 230, 480, 500, 20), _MINI_CHAINS, 0.5),
}

CAPTION_WORDS = ("THIS IS THE MOMENT EVERYTHING CHANGED NOBODY SAW IT COMING WATCH HIS FACE RIGHT HERE "
                 "THEN THE TWIST HITS AND YOU WILL NOT BELIEVE WHAT HAPPENS NEXT KEEP WATCHING UNTIL THE END "
                 "BECAUSE THE LAST PART IS INSANE SERIOUSLY THIS IS WILD FOLLOW FOR MORE").split()


# =====================================================================================================
# Small utilities
# =====================================================================================================

def ffmpeg_version() -> str:
    res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
    return res.stdout.splitlines()[0].strip()


def run_ffmpeg(args: Sequence[str], *, capture: bool = False, timeout: float | None = None,
               label: str = "") -> bytes:
    """Run ffmpeg (``-v error -nostdin`` prepended). Returns stdout bytes when capture=True."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-nostdin", *map(str, args)]
    log.debug("ffmpeg %s: %s", label, " ".join(cmd))
    res = subprocess.run(cmd, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                         stderr=subprocess.PIPE, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({label or 'job'}, rc={res.returncode}):\n{' '.join(cmd)}\n"
                           f"{res.stderr.decode(errors='replace')[-3000:]}")
    return res.stdout if capture else b""


def parallel(jobs: Sequence[Callable[[], Any]], workers: int = MAX_PARALLEL) -> list[Any]:
    """Run callables (each typically one ffmpeg process) with at most `workers` concurrently; results in
    input order."""
    if len(jobs) <= 1 or workers <= 1:
        return [j() for j in jobs]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(j) for j in jobs]
        return [f.result() for f in futs]


def file_digest(path: str | os.PathLike) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def fps_str(fr: Fraction) -> str:
    return f"{fr.numerator}/{fr.denominator}"


def _json_default(o: Any) -> Any:
    if isinstance(o, Fraction):
        return fps_str(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")


def write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=_json_default) + "\n")
    os.replace(tmp, path)


def decode_pts(path: str | os.PathLike) -> tuple[list[int], Fraction]:
    """Decoded video PTS (integers) and the stream time base (PyAV full decode)."""
    import av
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        tb = Fraction(s.time_base.numerator, s.time_base.denominator)
        pts = [int(f.pts) for f in c.decode(s)]
    return pts, tb


def decode_gray(path: str | os.PathLike, size: tuple[int, int] | None = None,
                keep: Callable[[int], bool] | None = None,
                roi: tuple[int, int, int, int] | None = None) -> dict[int, np.ndarray]:
    """Decode frames as gray (PyAV, sequential, PTS order). size=(w, h) -> cv2.resize INTER_AREA (the
    DESIGN proxy convention); roi=(x, y, w, h) crops after resizing; keep(i) selects frames.
    Returns {frame index: image}."""
    import av
    import cv2
    out: dict[int, np.ndarray] = {}
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        for i, fr in enumerate(c.decode(s)):
            if keep is not None and not keep(i):
                continue
            img = fr.to_ndarray(format="gray")
            if size is not None and (img.shape[1], img.shape[0]) != tuple(size):
                img = cv2.resize(img, tuple(size), interpolation=cv2.INTER_AREA)
            if roi is not None:
                x, y, w, h = roi
                img = img[y:y + h, x:x + w].copy()
            out[i] = img
    return out


def even(v: float) -> int:
    return int(2 * round(v / 2.0))


# =====================================================================================================
# ID code (16-bit block code, complement row, duplicated halves)
# =====================================================================================================

def id_geq_expr() -> str:
    """geq luma expression drawing frame N's code (DESIGN §6 / review recipe 2)."""
    bit = "mod(floor(N/pow(2,floor(mod(X,256)/16))),2)"
    return f"if(lt(Y,32),if({bit},235,16),if({bit},16,235))"


def id_frame(code: int) -> np.ndarray:
    """Reference numpy rendering of one ID frame (64 x 512 uint8) -- used by the unit tests."""
    if not 0 <= int(code) < 1 << 16:
        raise ValueError(f"ID code {code} out of range")
    x = np.arange(ID_W)
    bits = (int(code) >> ((x % 256) // 16)) & 1
    top = np.where(bits == 1, 235, 16).astype(np.uint8)
    bot = np.where(bits == 1, 16, 235).astype(np.uint8)
    img = np.empty((ID_H, ID_W), np.uint8)
    img[:32] = top
    img[32:] = bot
    return img


def decode_id_frames(frames: np.ndarray, sep: float = 60.0) -> tuple[np.ndarray, np.ndarray]:
    """Decode ID frames [n, 64, 512] -> (left_codes, right_codes), -1 where invalid.

    A half is valid only when every 16x16 block (centre 8x8 sampled) is clearly bright or dark
    (|mean - 125.5| > sep) and the bottom row is the bitwise complement of the top row."""
    a = np.asarray(frames, dtype=np.float32)
    if a.ndim == 2:
        a = a[None]
    if a.shape[1:] != (ID_H, ID_W):
        raise ValueError(f"ID frames must be {ID_H}x{ID_W}, got {a.shape[1:]}")
    n = a.shape[0]
    # block centres: rows 12..20 (top) and 44..52 (bottom); cols 16*b+4 .. +12 within each half
    top = a[:, 12:20, :].reshape(n, 8, 32, 16)[:, :, :, 4:12].mean(axis=(1, 3))     # [n, 32]
    bot = a[:, 44:52, :].reshape(n, 8, 32, 16)[:, :, :, 4:12].mean(axis=(1, 3))
    tb = top > 125.5
    bb = bot > 125.5
    clear = (np.abs(top - 125.5) > sep) & (np.abs(bot - 125.5) > sep)
    ok = clear & (tb != bb)
    weights = (1 << np.arange(16)).astype(np.int64)
    res = []
    for h in (0, 1):
        sl = slice(16 * h, 16 * h + 16)
        code = (tb[:, sl].astype(np.int64) * weights).sum(axis=1)
        valid = ok[:, sl].all(axis=1)
        res.append(np.where(valid, code, -1))
    return res[0], res[1]


def make_id_video(path: Path, n_frames: int, fps: Fraction = RAW_FPS) -> None:
    """Lossless H.264 ID video (MP4, timescale 30000): frame n shows code n."""
    if n_frames >= 1 << 16:
        raise ValueError("ID code is 16-bit")
    run_ffmpeg(["-y", "-f", "lavfi", "-i",
                f"color=c=black:s={ID_W}x{ID_H}:r={fps_str(fps)},format=gray,geq=lum='{id_geq_expr()}',"
                f"trim=end_frame={n_frames}", *X264_ID, *BITEXACT, str(path)], label="id video")


# =====================================================================================================
# Timing chain (identical for the real and the ID source)
# =====================================================================================================

def ss_seconds(j: int, src_fps: Fraction = RAW_FPS) -> str:
    """Input seek that selects exactly frame j: half a frame before its PTS (never ms-rounded)."""
    return f"{max(0.0, float((j - Fraction(1, 2)) / src_fps)):.6f}"


def src_frames_needed(n_out: int, speed: str | Fraction, src_fps: Fraction = RAW_FPS) -> int:
    """Source frames a chain of n_out competitor frames consumes (+ margin)."""
    return int(math.ceil(n_out * float(Fraction(speed)) * float(src_fps / COMP_FPS))) + 4


def _source_ticks(i: int, v: Fraction, src_fps: Fraction = RAW_FPS, tb: Fraction = RAW_TB) -> Fraction:
    """Exact setpts value (before truncation) of local source frame i without phase: i*tick/v."""
    return Fraction(i) / (src_fps * tb) / v


def phase_margins(speed: str, n_out: int, phase: float, src_fps: Fraction = RAW_FPS) -> tuple[int, float]:
    """(min distance in ticks of a source frame's truncated setpts value from an fps-rounding tie
    (r mod 1000 == 500), min distance of the exact setpts value from a truncation boundary).
    The second is inf when the division is exact (speed 1)."""
    v = Fraction(speed)
    tie, trunc = 10 ** 9, math.inf
    for i in range(src_frames_needed(n_out, speed, src_fps)):
        val = _source_ticks(i, v, src_fps) + Fraction(phase)
        if v != 1:
            f = val - math.floor(val)
            trunc = min(trunc, float(min(f, 1 - f)))
        r = math.floor(val) % TICKS_PER_COMP_FRAME
        tie = min(tie, abs(r - TICKS_PER_COMP_FRAME // 2))
    return tie, trunc


def choose_phase(speed: str, n_out: int) -> float:
    """Sub-frame setpts phase (RAW_TB ticks) that removes exact fps-rounding ties (DESIGN §6): 0 for speed 1
    when no tie occurs, else the half-integer phase in (0, 500) with the largest tie distance. Half-integer
    phases keep the double-precision setpts value 0.5 tick away from its truncation boundary."""
    if Fraction(speed) == 1 and phase_margins(speed, n_out, 0.0)[0] >= 2:
        return 0.0
    best, best_tie = None, -1
    for ph in [p + 0.5 for p in range(5, 495, 10)]:
        tie, trunc = phase_margins(speed, n_out, ph)
        if trunc >= 0.01 and tie > best_tie:
            best, best_tie = ph, tie
    if best is None or best_tie < 2:
        raise RuntimeError(f"no tie-free setpts phase for speed {speed}, {n_out} frames")
    return float(best)


def timing_filters(speed: str, n_out: int, phase: float) -> str:
    """The per-segment timing chain (without the -ss input option)."""
    v = Fraction(speed)
    if v <= 0:
        raise ValueError("speed must be > 0")
    if v == 1 and phase == 0:
        sp = "setpts=PTS-STARTPTS"
    elif v == 1:
        sp = f"setpts=PTS-STARTPTS+{phase:g}"
    else:
        sp = f"setpts=(PTS-STARTPTS)/{speed}+{phase:g}" if phase else f"setpts=(PTS-STARTPTS)/{speed}"
    return f"{sp},fps=30,trim=end_frame={n_out}"


def expected_chain_frames(j: int, speed: str, n_out: int, phase: float,
                          src_fps: Fraction = RAW_FPS, tb: Fraction = RAW_TB) -> np.ndarray:
    """Model of the timing chain (exact integers, independent of the ID measurement which must agree):
    local source frame i (= RAW j+i) gets setpts ticks P_i = trunc(i*tick/v + phase) (ffmpeg D2TS
    truncation); fps=30 (round=near) maps it to output slot r_i = round_half_away(P_i/1000); output frame n
    shows the LAST source frame with r_i <= n."""
    v = Fraction(speed)
    per_out = Fraction(1) / (COMP_FPS * tb)
    if (Fraction(1) / (src_fps * tb)).denominator != 1 or per_out.denominator != 1:
        raise ValueError("model needs an integer number of ticks per frame")
    po = int(per_out)
    need = src_frames_needed(n_out, speed, src_fps)
    r = np.empty(need, np.int64)
    for i in range(need):
        p = math.floor(_source_ticks(i, v, src_fps, tb) + Fraction(phase))
        q, rem = divmod(p, po)
        r[i] = q + (1 if 2 * rem >= po else 0)
    if r[0] != 0:
        raise ValueError("first output frame must be at pts 0 (phase too large)")
    return j + np.searchsorted(r, np.arange(n_out), side="right").astype(np.int64) - 1


# =====================================================================================================
# Geometry (own numpy code -- deliberately independent of match_cuts.geometry)
# =====================================================================================================

def _T(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]])


def _D(sx: float, sy: float) -> np.ndarray:
    return np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]])


@dataclass(frozen=True)
class Geometry:
    """The exact geometry filter parameters of one chain (ints as passed to ffmpeg)."""
    raw_w: int
    raw_h: int
    flip: bool
    sw: int                     # scale=sw:sh
    sh: int
    cx: int                     # crop=bw:bh:cx:cy:exact=1
    cy: int
    bw: int
    bh: int
    bx: int                     # box origin in the competitor frame
    by: int
    push_a: float | None = None     # push-in: z(n) = 1 + push_a * n   (n = local frame, 0-based)
    punch_at: int | None = None     # punch-in: z = 1 for n < punch_at else punch_zoom
    punch_zoom: float = 1.0

    def zoom(self, n: int) -> float:
        if self.push_a is not None:
            return 1.0 + float(self.push_a) * n
        if self.punch_at is not None:
            return 1.0 if n < self.punch_at else float(self.punch_zoom)
        return 1.0

    @property
    def animated(self) -> bool:
        return self.push_a is not None or self.punch_at is not None

    def filters(self) -> str:
        f = []
        if self.flip:
            f.append("hflip")
        f.append(f"scale={self.sw}:{self.sh}:flags=bicubic")
        f.append(f"crop={self.bw}:{self.bh}:{self.cx}:{self.cy}:exact=1")
        if self.push_a is not None:
            z = f"(1+{self.push_a!r}*(in-1))"
            f.append(_perspective(z))
        elif self.punch_at is not None:
            z = f"if(lt(in,{self.punch_at + 1}),1,{self.punch_zoom!r})"
            f.append(_perspective(z))
        s = ",".join(f)
        for name in (p.split("=")[0] for p in f):
            if name not in GEOMETRY_WHITELIST:
                raise ValueError(f"geometry filter {name} not whitelisted")
        return s

    def affine_box(self, n: int = 0) -> np.ndarray:
        """3x3 CORNER matrix: FLIPPED-RAW full-res px -> box-local px (after the zoom of frame n).

        ffmpeg's ``perspective`` maps output pixel INDEX x (not its centre x + 0.5) through the quad
        (verified with the calibration texture), so a zoom z about the box centre additionally shifts the
        content by -(z - 1)/2 px in both axes. That is what the competitor shows, so it is part of the truth."""
        z = self.zoom(n)
        S = _D(self.sw / self.raw_w, self.sh / self.raw_h)
        C = _T(-self.cx, -self.cy)
        Z = _T(self.bw / 2, self.bh / 2) @ _D(z, z) @ _T(-self.bw / 2, -self.bh / 2)
        if self.animated:
            Z = _T(-(z - 1) / 2, -(z - 1) / 2) @ Z
        return Z @ C @ S

    def affine_comp(self, n: int = 0) -> np.ndarray:
        """FLIPPED-RAW px -> competitor px."""
        return _T(self.bx, self.by) @ self.affine_box(n)

    def flip_matrix(self) -> np.ndarray:
        return np.array([[-1.0, 0.0, self.raw_w], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]) if self.flip else np.eye(3)

    def truth_sim(self, n: int = 0) -> dict:
        """Least-squares similarity (over the visible box area) of the frame-n affine, canonical dict."""
        return lsq_similarity(self.affine_comp(n), (self.bx, self.by, self.bw, self.bh))


def _perspective(z: str) -> str:
    q = f"W*(1-1/{z})/2"
    r = f"H*(1-1/{z})/2"
    return (f"perspective=x0='{q}':y0='{r}':x1='W-{q}':y1='{r}':x2='{q}':y2='H-{r}':x3='W-{q}':y3='H-{r}'"
            f":eval=frame:sense=source:interpolation=cubic")


def lsq_similarity(A: np.ndarray, region: tuple[float, float, float, float], grid: int = 41) -> dict:
    """Closed-form least-squares similarity q ~ a*p + t (complex a = s e^{i theta}) of the affine A over the
    preimage of the competitor rectangle `region` = (x, y, w, h). Returns {scale, rotation_deg, tx, ty}."""
    x, y, w, h = region
    gx, gy = np.meshgrid(np.linspace(x, x + w, grid), np.linspace(y, y + h, grid))
    q = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)])
    p = np.linalg.inv(A) @ q
    pc = p[0] + 1j * p[1]
    qc = q[0] + 1j * q[1]
    pm, qm = pc.mean(), qc.mean()
    a = np.sum((qc - qm) * np.conj(pc - pm)) / np.sum(np.abs(pc - pm) ** 2)
    t = qm - a * pm
    th = math.degrees(math.atan2(a.imag, a.real))
    if abs(th) < 1e-9:
        th = 0.0
    return {"scale": float(abs(a)), "rotation_deg": float(th), "tx": float(t.real), "ty": float(t.imag)}


def sim_matrix(sim: dict) -> np.ndarray:
    th = math.radians(sim.get("rotation_deg", 0.0))
    s = sim["scale"]
    return np.array([[s * math.cos(th), -s * math.sin(th), sim["tx"]],
                     [s * math.sin(th), s * math.cos(th), sim["ty"]], [0.0, 0.0, 1.0]])


def corner_to_cv(M: np.ndarray) -> np.ndarray:
    """CORNER-convention 3x3 -> OpenCV 2x3 (pixel centres at integers): p_cv = p_corner - 0.5."""
    return (_T(-0.5, -0.5) @ M @ _T(0.5, 0.5))[:2].copy()


def scaled_size(w: int, h: int, s_target: float, min_w: int, min_h: int, aniso_max: float = 3e-4) \
        -> tuple[int, int]:
    """Even (sw, sh) ~ s_target * (w, h) with sw >= min_w, sh >= min_h and anisotropy
    |sw/w - sh/h| / s <= aniso_max (a similarity cannot represent anisotropy); closest to s_target."""
    best = None
    sh0 = even(h * s_target)
    for sh in range(max(even(min_h), sh0 - 24), sh0 + 26, 2):
        for sw in (even(w * sh / h) - 2, even(w * sh / h), even(w * sh / h) + 2):
            if sw < min_w or sh < min_h:
                continue
            aniso = abs(sw / w - sh / h) / (sh / h)
            if aniso > aniso_max:
                continue
            cost = abs(math.sqrt(sw * sh / (w * h)) / s_target - 1)
            if best is None or cost < best[0]:
                best = (cost, sw, sh)
    if best is None:
        raise ValueError(f"no near-isotropic even size for {w}x{h} at scale {s_target}")
    return best[1], best[2]


def is_fullscreen(spec: ChainSpec) -> bool:
    return spec.kind == "fullscreen"


def chain_box(profile: Profile, spec: ChainSpec) -> tuple[int, int, int, int]:
    """(x, y, w, h) competitor px the chain's picture covers: the rounded video box, or the whole canvas for
    the fullscreen chain."""
    if is_fullscreen(spec):
        return 0, 0, profile.comp_w, profile.comp_h
    bx, by, bw, bh, _ = profile.box
    return bx, by, bw, bh


def geometry_for(profile: Profile, spec: ChainSpec) -> Geometry:
    bx, by, bw, bh = chain_box(profile, spec)
    W, H = profile.raw_w, profile.raw_h
    cover = max(bh / H, bw / W)
    z = spec.framing.zoom
    sw, sh = scaled_size(W, H, cover * z, bw, bh)
    cx = (sw - bw) // 2 + profile.u(spec.framing.dx)
    cy = (sh - bh) // 2 + profile.u(spec.framing.dy)
    if not (0 <= cx <= sw - bw and 0 <= cy <= sh - bh):
        raise ValueError(f"crop outside the scaled frame for {spec}")
    push_a = None
    if spec.push_end is not None:
        push_a = float(f"{(spec.push_end - 1.0) / (spec.n - 1):.12g}")
    return Geometry(W, H, spec.flip, sw, sh, cx, cy, bw, bh, bx, by, push_a=push_a,
                    punch_at=spec.punch_at, punch_zoom=spec.punch_zoom if spec.punch_at is not None else 1.0)


def visible_raw_rect(g: Geometry, n: int) -> tuple[float, float, float, float]:
    """RAW (UNFLIPPED) rectangle (x0, y0, x1, y1) visible inside the box at local frame n."""
    M = g.affine_box(n) @ g.flip_matrix()
    inv = np.linalg.inv(M)
    pts = inv @ np.array([[0, g.bw, 0, g.bw], [0, 0, g.bh, g.bh], [1, 1, 1, 1]], float)
    return float(pts[0].min()), float(pts[1].min()), float(pts[0].max()), float(pts[1].max())


# =====================================================================================================
# Calibration verification of the geometry truth (ffmpeg filter string vs our numpy transform)
# =====================================================================================================

def make_calibration_texture(path: Path, w: int, h: int, seed: int = 1234, blur: float = 4.0) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.random((h, w), dtype=np.float32) * 255.0, (0, 0), blur)
    img = cv2.normalize(img, None, 20, 235, cv2.NORM_MINMAX).astype(np.uint8)
    cv2.imwrite(str(path), img)
    return img


def render_geometry(texture_png: Path, geom_filters: str, n_frames: int, out_w: int, out_h: int) -> np.ndarray:
    """Push a still texture through the exact geometry filter string (in yuv420p, like the real chain)."""
    raw = run_ffmpeg(["-loop", "1", "-framerate", "30", "-i", str(texture_png), "-filter_complex",
                      f"[0:v]format=yuv420p,trim=end_frame={n_frames},setpts=PTS-STARTPTS,{geom_filters},"
                      f"format=gray[o]", "-map", "[o]", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                     capture=True, label="calibration")
    a = np.frombuffer(raw, np.uint8)
    if a.size != n_frames * out_w * out_h:
        raise RuntimeError(f"calibration render: got {a.size} bytes, expected {n_frames}x{out_w}x{out_h}")
    return a.reshape(n_frames, out_h, out_w)


def residual_affine(expected: np.ndarray, actual: np.ndarray, inset: int = 12) -> tuple[float, float, np.ndarray]:
    """ECC (affine) of `actual` against `expected`; returns (max |displacement| over the inset rectangle's
    corners + centre in px, |scale - 1|, 2x3 warp)."""
    import cv2
    h, w = expected.shape
    mask = np.zeros((h, w), np.uint8)
    mask[inset:h - inset, inset:w - inset] = 255
    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-7)
    _, warp = cv2.findTransformECC(expected.astype(np.float32), actual.astype(np.float32), warp,
                                   cv2.MOTION_AFFINE, crit, mask, 5)
    pts = np.array([[inset, inset], [w - inset, inset], [inset, h - inset], [w - inset, h - inset],
                    [w / 2, h / 2]], np.float64)
    wp = pts @ warp[:, :2].T.astype(np.float64) + warp[:, 2].astype(np.float64)
    dpos = float(np.max(np.linalg.norm(wp - pts, axis=1)))
    ds = abs(math.sqrt(abs(float(np.linalg.det(warp[:, :2].astype(np.float64))))) - 1.0)
    return dpos, ds, warp


def verify_geometry(g: Geometry, texture: np.ndarray, texture_png: Path, n_frames: int,
                    frames: Sequence[int] | None = None, truth: Callable[[int], dict] | None = None,
                    filters: str | None = None) -> dict:
    """Render the chain's geometry filters (or `filters`) on the calibration texture and compare every
    requested frame with our own warp of the texture under the truth similarity (+ g's flip).
    Returns {max_dpos, max_ds, frames, ok, per_frame}."""
    import cv2
    truth = truth or g.truth_sim
    frames = list(range(n_frames)) if frames is None else list(frames)
    out = render_geometry(texture_png, filters or g.filters(), n_frames, g.bw, g.bh)
    worst_p, worst_s, per = 0.0, 0.0, []
    for n in frames:
        sim = truth(n)
        M = _T(-g.bx, -g.by) @ sim_matrix(sim) @ g.flip_matrix()
        exp = cv2.warpAffine(texture.astype(np.float32), corner_to_cv(M), (g.bw, g.bh), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REFLECT)
        dpos, ds, _ = residual_affine(exp, out[n].astype(np.float32))
        per.append((n, dpos, ds))
        worst_p, worst_s = max(worst_p, dpos), max(worst_s, ds)
    return {"max_dpos": worst_p, "max_ds": worst_s, "frames": len(frames),
            "ok": bool(worst_p < CALIB_POS_TOL and worst_s < CALIB_SCALE_TOL), "per_frame": per}


# =====================================================================================================
# Layout (frame.png) and captions
# =====================================================================================================

@dataclass(frozen=True)
class LayoutPlan:
    comp_w: int
    comp_h: int
    box: tuple
    logo: tuple                 # (cx, cy, r)
    channel: tuple              # (x, y, fontsize, text)
    title: tuple                # ((y, fontsize, colour, text), ...) -- one colour per line
    watermark: tuple            # (y, fontsize, text)
    caption_y: int
    caption_fs: int
    caption_border: int


def layout_plan(p: Profile) -> LayoutPlan:
    u = p.u
    return LayoutPlan(p.comp_w, p.comp_h, p.box,
                      logo=(u(110), u(120), u(46)),
                      channel=(u(180), u(98), u(44), "SynthRecaps"),
                      title=((u(206), u(60), "white", "WAIT FOR THE"), (u(276), u(60), "#ffd21f", "LAST SECOND"),
                             (u(350), u(44), "#ff3030", "NO WAY")),
                      watermark=(u(1492), u(34), "synthrecaps dot tv"),
                      caption_y=u(1165), caption_fs=u(74), caption_border=max(2, u(6)))


def _drawtext(text: str, x: str, y: str, fs: int, color: str, font: str = FONT, border: int = 0,
              bcolor: str = "black", enable: str | None = None) -> str:
    s = f"drawtext=fontfile={font}:text='{text}':x={x}:y={y}:fontsize={fs}:fontcolor={color}"
    if border:
        s += f":borderw={border}:bordercolor={bcolor}"
    if enable:
        s += f":enable='{enable}'"
    return s


def _static_text_filters(lp: LayoutPlan) -> list[str]:
    """drawtext chain of the static zones (logo letter, channel name, multicolour title, watermark)."""
    lx, ly, lr = lp.logo
    parts = [_drawtext("S", f"{lx}-tw/2", f"{ly}-th/2", int(lr * 1.3), "white")]
    x, y, fs, text = lp.channel
    parts.append(_drawtext(text, str(x), str(y), fs, "white"))
    for (ty, tfs, col, text) in lp.title:
        parts.append(_drawtext(text, "(w-tw)/2", str(ty), tfs, col, border=max(2, tfs // 16)))
    wy, wfs, wtext = lp.watermark
    parts.append(_drawtext(wtext, "(w-tw)/2", str(wy), wfs, "#8c8c8c"))
    return parts


def _logo_expr(lp: LayoutPlan) -> str:
    lx, ly, lr = lp.logo
    return f"lte(hypot(X+0.5-{lx},Y+0.5-{ly}),{lr})"


def render_frame_png(path: Path, lp: LayoutPlan) -> None:
    """RGBA canvas: black, alpha 0 inside the rounded box (pixel centres), logo disc + letter, channel name,
    two-colour title with a red accent line, watermark under the box. Rendered once with geq + drawtext."""
    bx, by, bw, bh, r = lp.box
    cxb, cyb = bx + bw / 2, by + bh / 2
    inbox = (f"between(X,{bx},{bx + bw - 1})*between(Y,{by},{by + bh - 1})*"
             f"lte(hypot(max(abs(X+0.5-{cxb})-{bw / 2 - r},0),max(abs(Y+0.5-{cyb})-{bh / 2 - r},0)),{r})")
    logo = _logo_expr(lp)
    geq = (f"geq=r='if({logo},226,0)':g='if({logo},38,0)':b='if({logo},46,0)':a='if({inbox},0,255)'")
    parts = [f"color=c=black:s={lp.comp_w}x{lp.comp_h}:r=30,format=rgba,{geq}", *_static_text_filters(lp)]
    run_ffmpeg(["-y", "-f", "lavfi", "-i", ",".join(parts), "-frames:v", "1", "-pix_fmt", "rgba", str(path)],
               label="frame.png")


def render_glyph_png(path: Path, lp: LayoutPlan) -> None:
    """RGBA glyph layer of the static zones ONLY (transparent elsewhere), overlaid during the fullscreen chain
    so the title / logo / channel name / watermark stay on top of the full-canvas video.

    The same logo disc + drawtext chain as frame.png is rendered on a transparent canvas; drawtext blends
    every plane incl. alpha, i.e. it produces PREMULTIPLIED colour (measured). ffmpeg's overlay treats
    premultiplied input wrongly in YUV (it adds the black level), so the layer is un-premultiplied here and
    overlaid with the default straight alpha: over black it reproduces frame.png (check_glyph_png)."""
    import cv2
    logo = _logo_expr(lp)
    geq = f"geq=r='if({logo},226,0)':g='if({logo},38,0)':b='if({logo},46,0)':a='if({logo},255,0)'"
    parts = [f"color=c=black@0.0:s={lp.comp_w}x{lp.comp_h}:r=30,format=rgba,{geq}", *_static_text_filters(lp)]
    raw = run_ffmpeg(["-f", "lavfi", "-i", ",".join(parts), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba",
                      "-"], capture=True, label="glyph layer")
    img = np.frombuffer(raw, np.uint8).reshape(lp.comp_h, lp.comp_w, 4).astype(np.float64)
    a = img[:, :, 3:4]
    rgb = np.where(a > 0, np.clip(np.round(img[:, :, :3] * 255.0 / np.maximum(a, 1.0)), 0, 255), 0.0)
    out = np.dstack([rgb, a]).astype(np.uint8)
    if not cv2.imwrite(str(path), cv2.cvtColor(out, cv2.COLOR_RGBA2BGRA)):
        raise RuntimeError(f"cannot write {path}")


def check_glyph_png(frame_png: Path, glyph_png: Path, lp: LayoutPlan, tol: int = 2) -> dict:
    """The (straight-alpha) glyph layer composited over black must reproduce frame.png outside the box (the
    static zones look the same in boxed and fullscreen frames) and be fully transparent inside the box."""
    import cv2
    fr = cv2.imread(str(frame_png), cv2.IMREAD_UNCHANGED)
    gl = cv2.imread(str(glyph_png), cv2.IMREAD_UNCHANGED)
    if fr is None or gl is None or fr.shape != gl.shape or gl.shape[2] != 4:
        raise RuntimeError("frame.png / glyph layer must be RGBA of the same size")
    outside = fr[:, :, 3] == 255
    a = gl[:, :, 3:4].astype(np.float64) / 255.0
    comp = np.round(gl[:, :, :3].astype(np.float64) * a).astype(np.int16)
    diff = np.abs(comp - fr[:, :, :3].astype(np.int16)).max(axis=2)
    worst = int(diff[outside].max())
    if worst > tol:
        ys, xs = np.nonzero(outside & (diff > tol))
        raise RuntimeError(f"glyph layer over black differs from frame.png by {worst} levels "
                           f"({len(ys)} px, e.g. at x={xs[:5].tolist()}, y={ys[:5].tolist()})")
    if int(gl[:, :, 3][~outside].max(initial=0)) != 0:
        raise RuntimeError("glyph layer is not transparent inside the video box")
    return {"max_diff": worst, "opaque_px": int((gl[:, :, 3] == 255).sum()),
            "covered_px": int((gl[:, :, 3] > 0).sum())}


def measure_zones(frame_png: Path, lp: LayoutPlan) -> list[dict]:
    """Tight bboxes (CORNER px) of the static zones in frame.png, found inside their placement windows."""
    import cv2
    img = cv2.imread(str(frame_png), cv2.IMREAD_UNCHANGED)
    if img is None or img.shape[2] != 4:
        raise RuntimeError("frame.png must be RGBA")
    vis = (img[:, :, 3] == 255) & (img[:, :, :3].max(axis=2) > 30)
    bx, by, bw, bh, r = lp.box
    lx, ly, lr = lp.logo
    W = lp.comp_w
    windows = {
        "logo": (lx - lr - 4, ly - lr - 4, lx + lr + 4, ly + lr + 4),
        "channel_name": (lp.channel[0] - 4, ly - lr - 4, W, ly + lr + 4),
        "title": (0, lp.title[0][0] - 10, W, by - 2),
        "watermark": (0, by + bh + 2, W, lp.comp_h),
    }
    zones = []
    for name, (x0, y0, x1, y1) in windows.items():
        sub = vis[max(0, y0):y1, max(0, x0):x1]
        ys, xs = np.nonzero(sub)
        if ys.size == 0:
            raise RuntimeError(f"zone {name} not found in frame.png")
        zx, zy = int(xs.min()) + max(0, x0), int(ys.min()) + max(0, y0)
        zones.append({"type": name, "x": zx, "y": zy, "w": int(xs.max()) + max(0, x0) + 1 - zx,
                      "h": int(ys.max()) + max(0, y0) + 1 - zy, "static": True})
    # the box hole must be exactly the analytic rounded rectangle tested at pixel centres
    yy, xx = np.mgrid[0:lp.comp_h, 0:lp.comp_w]
    cxb, cyb = bx + bw / 2, by + bh / 2
    dx = np.maximum(np.abs(xx + 0.5 - cxb) - (bw / 2 - r), 0)
    dy = np.maximum(np.abs(yy + 0.5 - cyb) - (bh / 2 - r), 0)
    hole = (xx >= bx) & (xx < bx + bw) & (yy >= by) & (yy < by + bh) & (np.hypot(dx, dy) <= r)
    if not np.array_equal(hole, img[:, :, 3] == 0):
        raise RuntimeError("frame.png alpha hole differs from the analytic rounded box")
    return zones


def plan_captions(p: Profile, n_comp: int, seed: int) -> list[dict]:
    """Word-by-word caption timings [k_in, k_out): 8-16 frames per word, a pause after every 3-5 words."""
    rng = np.random.default_rng(seed)
    caps, k, wi = [], int(round(12 * p.unit)) + 3, 0
    group = int(rng.integers(3, 6))
    while True:
        dur = int(rng.integers(8, 17))
        if k + dur > n_comp - 6:
            break
        caps.append({"text": CAPTION_WORDS[wi % len(CAPTION_WORDS)], "k_in": k, "k_out": k + dur})
        wi += 1
        k += dur
        group -= 1
        if group == 0:
            k += int(rng.integers(8, 21))
            group = int(rng.integers(3, 6))
    return caps


def caption_filters(caps: list[dict], lp: LayoutPlan) -> list[str]:
    out = []
    for c in caps:
        en = f"between(t,{(c['k_in'] - 0.5) / 30:.6f},{(c['k_out'] - 0.5) / 30:.6f})"
        out.append(_drawtext(c["text"], "(w-tw)/2", str(lp.caption_y), lp.caption_fs, "white",
                             border=lp.caption_border, enable=en))
    return out


def iter_raw_frames(args: Sequence[str], w: int, h: int, label: str = "") -> Iterable[np.ndarray]:
    """Stream gray rawvideo frames (h x w) from an ffmpeg command writing to stdout."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-nostdin", *map(str, args)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = w * h
    try:
        while True:
            buf = proc.stdout.read(size)
            if not buf:
                break
            if len(buf) != size:
                raise RuntimeError(f"{label}: truncated frame")
            yield np.frombuffer(buf, np.uint8).reshape(h, w)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed ({label}, rc={rc}): {err[-2000:]}")


def measure_captions(caps: list[dict], lp: LayoutPlan, n_comp: int) -> list[dict]:
    """Render the caption layer alone on black with the identical drawtext chain and MEASURE every word's
    frames and bbox (CORNER px): the word's pixel mask must appear exactly on [k_in, k_out)."""
    y0 = max(0, lp.caption_y - lp.caption_fs)
    hh = min(lp.comp_h - y0, 3 * lp.caption_fs)
    hh -= hh % 2
    chain = ",".join([f"color=c=black:s={lp.comp_w}x{lp.comp_h}:r=30", f"trim=end_frame={n_comp}",
                      *caption_filters(caps, lp), f"crop={lp.comp_w}:{hh}:0:{y0}", "format=gray"])
    sig: list[tuple] = []                      # per frame: (mask digest | None, bbox)
    for fr in iter_raw_frames(["-f", "lavfi", "-i", chain, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                              lp.comp_w, hh, "caption mask"):
        on = fr > 24
        if not on.any():
            sig.append((None, None))
            continue
        ys, xs = np.nonzero(on)
        bbox = (int(xs.min()), int(ys.min()) + y0, int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        sig.append((hashlib.blake2b(np.packbits(on).tobytes(), digest_size=16).hexdigest(), bbox))
    if len(sig) != n_comp:
        raise RuntimeError(f"caption mask has {len(sig)} frames, expected {n_comp}")
    covered = np.zeros(n_comp, bool)
    res = []
    for c in caps:
        k0, k1 = c["k_in"], c["k_out"]
        d0 = sig[k0][0]
        if d0 is None or any(sig[k][0] != d0 for k in range(k0, k1)):
            raise RuntimeError(f"caption {c} not visible (or not static) on all of its frames")
        if (k0 > 0 and sig[k0 - 1][0] == d0) or (k1 < n_comp and sig[k1][0] == d0):
            raise RuntimeError(f"caption {c} visible outside [k_in, k_out)")
        covered[k0:k1] = True
        x, y, w, h = sig[k0][1]
        res.append({**c, "x": x, "y": y, "w": w, "h": h})
    stray = [k for k in range(n_comp) if not covered[k] and sig[k][0] is not None]
    if stray:
        raise RuntimeError(f"caption pixels on frames without a planned caption: {stray[:10]}")
    return res


def count_frames(path: str | os.PathLike) -> int:
    """Packet count of the first video stream (== frames for our intra / CFR intermediates)."""
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True)
    return int(res.stdout.strip().split(",")[0])


# =====================================================================================================
# RAW generation
# =====================================================================================================

def shot_graph(p: Profile, s: ShotSpec) -> str:
    """The shot's lavfi graph (one unlabeled output stream, RAW size, yuv420p)."""
    W, H = p.raw_w, p.raw_h
    R = fps_str(RAW_FPS)
    vals = {"W": W, "H": H, "R": R, "W2": W // 2, "H2": H // 2, "W4": W // 4, "H4": H // 4,
            "W6": W // 6, "H6": H // 6, "W8": W // 8, "H8": H // 8, "LW": 160, "LH": 90,
            "OW": even(W / 4), "OH": even(H / 3), "OX": even(W * 0.375), "OY": even(H * 0.28),
            "AX": even(W * 0.26), "AY": even(H * 0.23),
            "GX0": W // 10, "GY0": H // 10, "GX1": W - W // 10, "GY1": H - H // 10}
    g = s.graph.format(**vals) + f",scale={W}:{H}:flags=neighbor,format=yuv420p"
    if s.layer is None:
        return g
    seed, opacity, div = s.layer
    return (f"life=s={W // div}x{H // div}:r={R}:seed={seed}:ratio=0.32:life_color=white:death_color=black,"
            f"scale={W}:{H}:flags=neighbor,format=yuv420p[lay];{g}[base];"
            f"[lay][base]blend=all_mode=normal:all_opacity={opacity}")


def safe_region(p: Profile, geoms: list[tuple[ChainSpec, Geometry]], caption_band: tuple[float, float]) \
        -> tuple[int, int, int, int]:
    """RAW rectangle (x0, y0, x1, y1) visible in EVERY competitor framing (all frames of all chains; mirrored
    for flipped chains) and above the caption band's preimage."""
    x0, y0, x1, y1 = 0.0, 0.0, float(p.raw_w), float(p.raw_h)
    cap_top = float(p.raw_h)
    for spec, g in geoms:
        ns = range(spec.n) if g.animated else [0]
        for n in ns:
            ax0, ay0, ax1, ay1 = visible_raw_rect(g, n)
            x0, y0, x1, y1 = max(x0, ax0), max(y0, ay0), min(x1, ax1), min(y1, ay1)
            # preimage (RAW y) of the top of the caption band
            M = g.affine_comp(n) @ g.flip_matrix()
            inv = np.linalg.inv(M)
            cy = (inv @ np.array([p.comp_w / 2, caption_band[0], 1.0]))[1]
            cap_top = min(cap_top, cy)
    y1 = min(y1, cap_top)
    return int(math.ceil(x0)), int(math.ceil(y0)), int(math.floor(x1)), int(math.floor(y1))


@dataclass(frozen=True)
class Overlays:
    grid: tuple                 # (x, y, w, h) even ints
    cell: int
    counter_x: int              # centre x
    counter_y: int              # top y
    counter_fs: int
    counter_border: int


def plan_overlays(p: Profile, safe: tuple[int, int, int, int]) -> Overlays:
    """Grid texture over the SAFE REGION and the largest counter (160-200 px at full scale) that fits in it."""
    x0, y0, x1, y1 = safe
    border = max(3, p.u(8))
    fs = p.u(200)
    while fs >= p.u(160) and int(0.62 * fs * 5) + 2 * border + p.u(40) > x1 - x0:
        fs -= 2
    counter_w = int(0.62 * fs * 5) + 2 * border
    if fs < p.u(160) or y1 - y0 < fs * 2:
        raise RuntimeError(f"SAFE REGION {safe} too small for the counter ({counter_w}x{fs})")
    gx0, gy0 = even(x0 + p.u(16)), even(y0 + p.u(16))
    gx1, gy1 = even(x1 - p.u(16)) - 2, even(y1 - p.u(16)) - 2
    cx = even((x0 + x1) / 2)
    cy = even(y0 + p.u(70))
    return Overlays((gx0, gy0, gx1 - gx0, gy1 - gy0), p.u(160), cx, cy, fs, border)


def raw_overlay_filters(o: Overlays) -> str:
    gx, gy, gw, gh = o.grid
    return (f"split=2[m][g0];[g0]crop={gw}:{gh}:{gx}:{gy},drawgrid=w={o.cell}:h={o.cell}:t=2:c=white@0.4[g1];"
            f"[m][g1]overlay={gx}:{gy},"
            f"drawtext=fontfile={FONT_MONO}:text='%{{eif\\:n\\:d\\:5}}':x={o.counter_x}-tw/2:y={o.counter_y}:"
            f"fontsize={o.counter_fs}:fontcolor=white:borderw={o.counter_border}:bordercolor=black")


def raw_audio_graph(n_samples: int) -> str:
    """Unique mono audio: three FM tones with incommensurate AM + seeded pink noise with a bursty envelope."""
    tones = ("0.16*sin(2*PI*(220*t+4.5*sin(2*PI*0.13*t)+2.1*sin(2*PI*0.029*t)))*(0.55+0.45*sin(2*PI*0.71*t))"
             "+0.12*sin(2*PI*(523*t+9.3*sin(2*PI*0.37*t+1)))*(0.5+0.5*sin(2*PI*1.9*t+0.3))"
             "+0.08*sin(2*PI*(1187*t+17*sin(2*PI*0.53*t+2)))*(0.5+0.5*sin(2*PI*2.7*t+1.1))")
    env = "0.15+0.85*pow(abs(sin(2*PI*1.37*t)*sin(2*PI*0.83*t+1)),3)"
    return (f"aevalsrc=exprs='{tones}':s={AUDIO_SR}:d={n_samples / AUDIO_SR + 1:.6f}[t];"
            f"anoisesrc=color=pink:seed=4242:amplitude=0.35:r={AUDIO_SR}:d={n_samples / AUDIO_SR + 1:.6f}[n];"
            f"aevalsrc=exprs='{env}':s={AUDIO_SR}:d={n_samples / AUDIO_SR + 1:.6f}[e];"
            f"[n][e]amultiply[ne];[t][ne]amix=inputs=2:duration=first:normalize=0,"
            f"atrim=end_sample={n_samples},aformat=sample_fmts=flt:channel_layouts=mono[a]")


def generate_raw(p: Profile, build: Path, out: Path, ov: Overlays) -> dict:
    """12 shots in parallel (lossless NUT), concat + overlays once, audio; raw.mp4 (+ timings)."""
    t0 = time.perf_counter()
    jobs = []
    for i, s in enumerate(SHOTS[:p.n_shots]):
        dst = build / f"shot_{i:02d}.nut"
        g = shot_graph(p, s)
        chain = (f"{g},trim=start_frame={s.skip}:end_frame={s.skip + p.shot_len},setpts=PTS-STARTPTS,"
                 f"setsar=1,format=yuv420p[out]")

        def job(chain=chain, dst=dst, i=i):
            run_ffmpeg(["-y", "-filter_threads", "1", "-filter_complex", chain, "-map", "[out]",
                        "-frames:v", str(p.shot_len), *FFV1, str(dst)], label=f"shot {i}")
            return dst
        jobs.append(job)
    audio_wav = build / "raw_audio.wav"

    n_exact = p.raw_frames * Fraction(AUDIO_SR) / RAW_FPS
    if n_exact.denominator != 1:
        raise ValueError("RAW audio length must be an integer number of samples")

    def audio_job():
        n = int(n_exact)
        run_ffmpeg(["-y", "-filter_complex", raw_audio_graph(n), "-map", "[a]", "-c:a", "pcm_f32le",
                    str(audio_wav)], label="raw audio")
        return n
    results = parallel(jobs + [audio_job])
    shots, n_samples = results[:-1], results[-1]
    t_shots = time.perf_counter() - t0
    for i, sp in enumerate(shots):
        cnt = count_frames(sp)
        if cnt != p.shot_len:
            raise RuntimeError(f"shot {i} has {cnt} frames, expected {p.shot_len}")
    ins = []
    for sp in shots:
        ins += ["-i", str(sp)]
    k = len(shots)
    fc = ("".join(f"[{i}:v]" for i in range(k)) + f"concat=n={k}:v=1:a=0,{raw_overlay_filters(ov)},"
          f"format=yuv420p[v]")
    run_ffmpeg(["-y", *ins, "-i", str(audio_wav), "-filter_complex", fc, "-map", "[v]", "-map", f"{k}:a",
                *X264_RAW, "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_SR), *BITEXACT, str(out)],
               label="raw.mp4")
    return {"shots_s": t_shots, "encode_s": time.perf_counter() - t0 - t_shots, "audio_samples": n_samples}


# =====================================================================================================
# Competitor
# =====================================================================================================

@dataclass
class Chain:
    """A resolved ChainSpec: timing + geometry + measured frames."""
    index: int
    spec: ChainSpec
    comp_in: int = 0                   # competitor frame of the chain's local frame 0
    j: int = -1                        # RAW start frame
    phase: float = 0.0
    ss: str = ""
    timing: str = ""
    geom: Geometry | None = None
    frames: np.ndarray | None = None   # measured RAW frame per local frame (ID chain)
    expected: np.ndarray | None = None

    @property
    def in_raw(self) -> bool:
        return self.spec.shot >= 0


def resolve_chains(p: Profile) -> list[Chain]:
    chains, k = [], 0
    for i, spec in enumerate(p.chains):
        c = Chain(i, spec, comp_in=k)
        if c.in_raw:
            if spec.shot >= p.n_shots or spec.off < 0:
                raise ValueError(f"bad shot for {spec}")
            c.j = spec.shot * p.shot_len + spec.off
            if spec.off + src_frames_needed(spec.n, spec.speed) > p.shot_len:
                raise ValueError(f"chain {i} runs past the end of shot {spec.shot}")
            if "mandelbrot" in SHOTS[spec.shot].tags and (spec.flip or spec.push_end or spec.punch_at or
                                                          is_fullscreen(spec)):
                raise ValueError("mandelbrot shots are not used for flip / push-in / punch-in / fullscreen")
            c.phase = choose_phase(spec.speed, spec.n)
            c.ss = ss_seconds(c.j)
            c.timing = timing_filters(spec.speed, spec.n, c.phase)
            c.geom = geometry_for(p, spec)
            c.expected = expected_chain_frames(c.j, spec.speed, spec.n, c.phase)
        chains.append(c)
        k += spec.n - spec.xfade
    for a, b in zip(chains[:-1], chains[1:]):
        if a.spec.xfade and not (a.in_raw and b.in_raw):
            raise ValueError("crossfades only between two RAW chains")
        if a.spec.xfade and (is_fullscreen(a.spec) or is_fullscreen(b.spec)):
            raise ValueError("crossfades only between boxed chains")
    for c in chains:
        if is_fullscreen(c.spec) and (not c.in_raw or c.spec.flip or c.spec.push_end is not None or
                                      c.spec.punch_at is not None or c.spec.framing != Framing()):
            raise ValueError("the fullscreen chain is a plain RAW chain (cover-scaled, centred)")
    return chains


def decode_id_chain(id_mp4: Path, c: Chain) -> np.ndarray:
    raw = run_ffmpeg(["-ss", c.ss, "-i", str(id_mp4), "-filter_complex", f"[0:v]{c.timing},format=gray[v]",
                      "-map", "[v]", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture=True, label="id chain")
    a = np.frombuffer(raw, np.uint8).reshape(-1, ID_H, ID_W)
    left, right = decode_id_frames(a)
    if len(left) != c.spec.n:
        raise RuntimeError(f"ID chain {c.index}: {len(left)} frames, expected {c.spec.n}")
    if (left < 0).any() or not np.array_equal(left, right):
        raise RuntimeError(f"ID chain {c.index}: undecodable frames {np.nonzero(left < 0)[0][:10]}")
    return left


def not_in_raw_graph(bw: int, bh: int, n: int, unit: float) -> str:
    fs = max(12, int(round(96 * unit)))
    return (f"gradients=s={bw}x{bh}:r=30:speed=0.03:seed=9:c0=0x3010a0:c1=0xff7a00:c2=0x00c8a0:n=3:"
            f"x0={bw // 8}:y0={bh // 8}:x1={bw - bw // 8}:y1={bh - bh // 8},"
            f"trim=end_frame={n},drawtext=fontfile={FONT}:text='SUBSCRIBE':x=(w-tw)/2+{int(40 * unit)}*sin(2*PI*t):"
            f"y=(h-th)/2:fontsize={fs}:fontcolor=white:borderw={max(2, int(6 * unit))}:bordercolor=black")


def render_chain(raw_mp4: Path, c: Chain, dst: Path, p: Profile) -> None:
    if c.in_raw:
        run_ffmpeg(["-y", "-threads", "2", "-ss", c.ss, "-i", str(raw_mp4), "-filter_threads", "1",
                    "-filter_complex", f"[0:v]{c.timing},{c.geom.filters()},setsar=1,format=yuv420p[v]",
                    "-map", "[v]", *FFV1, str(dst)], label=f"segment chain {c.index}")
    else:
        bx, by, bw, bh, _ = p.box
        run_ffmpeg(["-y", "-filter_complex", f"{not_in_raw_graph(bw, bh, c.spec.n, p.unit)},setsar=1,"
                    f"format=yuv420p[v]", "-map", "[v]", *FFV1, str(dst)], label="not-in-raw insert")


def composite_graph(chains: list[Chain], inputs: list[str], tail: str, canvas: Profile | None = None) -> str:
    """concat of the chain streams with the crossfade(s); `tail` is appended to the concat.

    canvas=None: box-size streams (every chain boxed; `tail` pads onto the canvas). With a profile: every
    boxed chain (a crossfade pair after its xfade on the box-size streams) is padded onto the black canvas
    at the box origin and fullscreen chains (already canvas-size) are concatenated as they are."""
    pad = ""
    if canvas is not None:
        bx, by = canvas.box[0], canvas.box[1]
        pad = f"pad={canvas.comp_w}:{canvas.comp_h}:{bx}:{by}:black"
    parts, labels, i = [], [], 0
    while i < len(chains):
        c = chains[i]
        if c.spec.xfade:
            b = chains[i + 1]
            if is_fullscreen(c.spec) or is_fullscreen(b.spec):
                raise ValueError("crossfades only between boxed chains")
            d = c.spec.xfade
            off = c.spec.n - d
            parts.append(f"{inputs[i]}{inputs[i + 1]}{tail_xfade(d, off)}" + (f",{pad}" if pad else "") + f"[x{i}]")
            labels.append(f"[x{i}]")
            i += 2
        elif pad and not is_fullscreen(c.spec):
            parts.append(f"{inputs[i]}{pad}[p{i}]")
            labels.append(f"[p{i}]")
            i += 1
        else:
            if canvas is None and is_fullscreen(c.spec):
                raise ValueError("a fullscreen chain needs the canvas-size composite (canvas=profile)")
            labels.append(inputs[i])
            i += 1
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0{tail}")
    return ";".join(parts)


def fullscreen_ranges(chains: list[Chain]) -> list[tuple[int, int]]:
    """Competitor frame ranges [a, b) of the fullscreen chains."""
    return [(c.comp_in, c.comp_in + c.spec.n) for c in chains if is_fullscreen(c.spec)]


def _t_between(a: int, b: int) -> str:
    """Timeline expression selecting competitor frames [a, b) (half-frame guards, never on a PTS)."""
    return f"between(t,{(a - 0.5) / 30:.6f},{(b - 0.5) / 30:.6f})"


def competitor_video_filter(p: Profile, chains: list[Chain], caps: list[dict], lp: LayoutPlan) -> str:
    """The competitor video filtergraph. Inputs: 0..K-1 = the chain intermediates (box-size; canvas-size for
    fullscreen chains), K = frame.png (-loop 1), K+1 = the glyph layer (-loop 1; only when there is a
    fullscreen chain). Boxed frames get frame.png (black canvas, rounded hole, static zones); fullscreen
    frames get only the glyph layer; captions are drawn on every frame. Output label [vout]."""
    k = len(chains)
    fs = fullscreen_ranges(chains)
    if fs:
        en = "+".join(_t_between(a, b) for a, b in fs)
        over = (f"[v0][{k}:v]overlay=0:0:shortest=1:enable='not({en})'[v1];"
                f"[v1][{k + 1}:v]overlay=0:0:shortest=1:enable='{en}'[v2];[v2]")
    else:
        over = f"[v0][{k}:v]overlay=0:0:shortest=1[v1];[v1]"
    # canvas via pad (the `color` source + overlay=shortest=1 recipe drops the final frame -- measured)
    tail = "[v0];" + over + ",".join([*caption_filters(caps, lp), "format=yuv420p"]) + "[vout]"
    return composite_graph(chains, [f"[{i}:v]" for i in range(k)], tail, canvas=p)


def measure_fullscreen_frames(comp_mp4: Path, p: Profile, lp: LayoutPlan, zones: list[dict],
                              scale: int = 4) -> tuple[list[int], np.ndarray]:
    """Frames whose canvas OUTSIDE the video box (minus the static zones and the caption band) shows picture
    rather than the black background. Returns (fullscreen frame indices, per-frame non-black fraction)."""
    w, h = even(p.comp_w / scale), even(p.comp_h / scale)
    rx, ry = w / p.comp_w, h / p.comp_h
    m = np.ones((h, w), bool)

    def cut(x0, y0, x1, y1, pad=2):
        m[max(0, int(math.floor(y0 * ry)) - pad):int(math.ceil(y1 * ry)) + pad,
          max(0, int(math.floor(x0 * rx)) - pad):int(math.ceil(x1 * rx)) + pad] = False
    bx, by, bw, bh, _ = p.box
    cut(bx, by, bx + bw, by + bh)
    for z in zones:
        cut(z["x"], z["y"], z["x"] + z["w"], z["y"] + z["h"])
    cut(0, lp.caption_y - lp.caption_fs, p.comp_w, lp.caption_y + 2 * lp.caption_fs)
    frac = []
    for fr in iter_raw_frames(["-i", str(comp_mp4), "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo",
                               "-pix_fmt", "gray", "-"], w, h, "fullscreen measurement"):
        frac.append(float((fr[m] > 40).mean()))
    a = np.array(frac)
    return [int(k) for k in np.nonzero(a > 0.3)[0]], a


_XFADE_REAL = "fade"


def tail_xfade(d: int, off: int, custom: bool = False) -> str:
    tr = "transition=custom:expr='if(lt(X,W/2),A,B)'" if custom else f"transition={_XFADE_REAL}"
    return f"xfade={tr}:duration={d / 30:.6f}:offset={off / 30:.6f}"


def id_composite(id_mp4: Path, chains: list[Chain]) -> tuple[np.ndarray, np.ndarray]:
    """The whole edit on the ID video (same -ss inputs, timing chains, concat; xfade replaced by the
    custom left=A / right=B split). Returns per competitor frame (left, right) codes (-1 = no code)."""
    args, labels, parts = [], [], []
    ni = 0
    for c in chains:
        if c.in_raw:
            args += ["-ss", c.ss, "-i", str(id_mp4)]
            parts.append(f"[{ni}:v]{c.timing},format=yuv420p,setsar=1[c{c.index}]")
            ni += 1
        else:
            parts.append(f"color=c=gray:s={ID_W}x{ID_H}:r=30,trim=end_frame={c.spec.n},format=yuv420p,"
                         f"setsar=1[c{c.index}]")
        labels.append(f"[c{c.index}]")
    body, i, cat = [], 0, []
    while i < len(chains):
        c = chains[i]
        if c.spec.xfade:
            body.append(f"{labels[i]}{labels[i + 1]}{tail_xfade(c.spec.xfade, c.spec.n - c.spec.xfade, True)}"
                        f"[x{i}]")
            cat.append(f"[x{i}]")
            i += 2
        else:
            cat.append(labels[i])
            i += 1
    fc = ";".join(parts + body + ["".join(cat) + f"concat=n={len(cat)}:v=1:a=0,format=gray[v]"])
    raw = run_ffmpeg([*args, "-filter_complex", fc, "-map", "[v]", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                     capture=True, label="ID composite")
    a = np.frombuffer(raw, np.uint8).reshape(-1, ID_H, ID_W)
    return decode_id_frames(a)


def floor_interval(frames: np.ndarray, speed: str, src_fps: Fraction = RAW_FPS) -> tuple[Fraction, Fraction]:
    """Exact feasible raw_in interval [a, b) (seconds) under the AE floor rule for a chain's frames:
    raw_fps*raw_in + u*i in [m_i, m_i + 1) with u = v*raw_fps/comp_fps (our own phase solve)."""
    u = Fraction(speed) * src_fps / COMP_FPS
    lo = max(Fraction(int(m)) - u * i for i, m in enumerate(frames))
    hi = min(Fraction(int(m) + 1) - u * i for i, m in enumerate(frames))
    if not lo < hi:
        raise RuntimeError(f"chain frames are not consistent with one linear floor-rule map: [{lo}, {hi})")
    return lo / src_fps, hi / src_fps


def audio_in_point(frames: np.ndarray, speed: str, src_fps: Fraction = RAW_FPS) -> Fraction:
    """NLE audio in-point (seconds) of a chain: the clip's in-point sits on the frame BOUNDARY of its first RAW
    frame, i.e. the LOWER bound of the floor-rule raw_in interval (the earliest in-point that shows exactly
    the chain's frames; == j/fps up to the RAW-vs-comp rate drift over the chain). DESIGN §7 D8: the old
    interval centre put the audio ~1/4 RAW frame late and hid the tool's quarter-frame phase bias."""
    return floor_interval(frames, speed, src_fps)[0]


def audio_start_sample(frames: np.ndarray, speed: str, src_fps: Fraction = RAW_FPS) -> int:
    """First RAW audio sample of the chain: the first sample at or after the NLE in-point (so the truth
    raw_in stays inside the floor interval, < 1 sample above its lower bound)."""
    return int(math.ceil(audio_in_point(frames, speed, src_fps) * AUDIO_SR))


def build_competitor_audio(p: Profile, chains: list[Chain], raw_audio: Path, dst: Path, n_comp: int) -> dict:
    """Separate audio graph: per chain sample-exact atrim (tape-style asetrate for v != 1) starting at the NLE
    in-point (:func:`audio_start_sample`), acrossfade for the crossfade, NOT-IN-RAW tone, concat; music under
    it (-12 dB, amix normalize=0). Returns per-chain audio start (seconds) and sample counts."""
    parts, labels, info, inputs = [], [], {}, []
    for c in chains:
        n = c.spec.n
        out_samples = n * SAMPLES_PER_COMP_FRAME
        if c.in_raw:
            a, b = floor_interval(c.frames, c.spec.speed)
            s0 = audio_start_sample(c.frames, c.spec.speed)
            if not a <= Fraction(s0, AUDIO_SR) < min(b, a + Fraction(1, AUDIO_SR)):
                raise RuntimeError(f"chain {c.index}: audio start {s0} outside [{a}, {b})")
            v = Fraction(c.spec.speed)
            src_len = int(math.ceil(out_samples * v)) + 4 * AUDIO_SR // 100
            # one input per chain (a single input feeding many atrim consumers overflows ffmpeg's queues)
            chain = (f"[{len(inputs) // 2}:a]atrim=start_sample={s0}:end_sample={s0 + src_len},"
                     f"asetpts=PTS-STARTPTS")
            inputs += ["-i", str(raw_audio)]
            if v != 1:
                rate = v * AUDIO_SR
                if rate.denominator != 1:
                    raise ValueError("speed * 48000 must be an integer sample rate")
                chain += f",asetrate={int(rate)},aresample={AUDIO_SR}"
            chain += f",atrim=end_sample={out_samples},asetpts=PTS-STARTPTS"
            info[c.index] = {"raw_in_seconds": s0 / AUDIO_SR, "start_sample": s0,
                             "interval_floor": [float(a), float(b)], "in_point": "floor_interval_lower_bound"}
        else:
            chain = (f"aevalsrc=exprs='0.25*sin(2*PI*2960*t)*(0.55+0.45*sin(2*PI*6*t))':s={AUDIO_SR},"
                     f"atrim=end_sample={out_samples}")
            info[c.index] = {"raw_in_seconds": None, "start_sample": None}
        parts.append(chain + f",aformat=sample_fmts=flt:channel_layouts=mono[a{c.index}]")
        labels.append(f"[a{c.index}]")
    cat, i = [], 0
    while i < len(chains):
        c = chains[i]
        if c.spec.xfade:
            parts.append(f"{labels[i]}{labels[i + 1]}acrossfade=ns={c.spec.xfade * SAMPLES_PER_COMP_FRAME}:"
                         f"c1=tri:c2=tri[ax{i}]")
            cat.append(f"[ax{i}]")
            i += 2
        else:
            cat.append(labels[i])
            i += 1
    total = n_comp * SAMPLES_PER_COMP_FRAME
    music = ("0.30*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*2*t))+0.22*sin(2*PI*164.81*t)*(0.5+0.5*sin(2*PI*2*t+2.1))"
             "+0.18*sin(2*PI*220*t+sin(2*PI*0.5*t))*(0.5+0.5*sin(2*PI*4*t))")
    parts.append("".join(cat) + f"concat=n={len(cat)}:v=0:a=1[orig]")
    parts.append(f"aevalsrc=exprs='{music}':s={AUDIO_SR},atrim=end_sample={total},volume=-12dB,"
                 f"aformat=sample_fmts=flt:channel_layouts=mono[m]")
    parts.append(f"[orig][m]amix=inputs=2:duration=first:normalize=0,atrim=end_sample={total},"
                 f"aformat=sample_fmts=flt:channel_layouts=stereo[out]")
    run_ffmpeg(["-y", *inputs, "-filter_complex", ";".join(parts), "-map", "[out]",
                "-c:a", "pcm_f32le", str(dst)], label="competitor audio")
    import soundfile as sf
    got = sf.info(str(dst)).frames
    if got != total:
        raise RuntimeError(f"competitor audio has {got} samples, expected {total}")
    return info


# =====================================================================================================
# Self-checks at the DESIGN proxy sizes
# =====================================================================================================

def _cfg() -> dict:
    d = dict(_CFG_DEFAULTS)
    try:
        from match_cuts.config import Config  # foundation module; only for the defaults
        c = Config()
        for k in d:
            d[k] = getattr(c, k, d[k])
    except Exception:  # pragma: no cover - synth must also work stand-alone
        pass
    return d


def proxy_sizes(p: Profile, cfg: dict) -> tuple[tuple[int, int], tuple[int, int]]:
    rw = min(int(cfg["raw_proxy_width"]), p.raw_w)
    rh = even(p.raw_h * rw / p.raw_w)
    cw = min(int(round(p.comp_w * cfg["comp_proxy_scale"])), int(cfg["comp_proxy_max_width"]))
    cw = even(cw)
    ch = even(p.comp_h * cw / p.comp_w)
    return (rw, rh), (cw, ch)


def _box_mask(p: Profile, cw: int, ch: int) -> np.ndarray:
    """Pixels of the comp proxy whose area is fully inside the rounded box (4x4 supersampled)."""
    bx, by, bw, bh, r = p.box
    rx, ry = cw / p.comp_w, ch / p.comp_h
    ss = 4
    offs = (np.arange(ss) + 0.5) / ss
    xs = ((np.arange(cw)[:, None] + offs[None]) / rx).ravel()
    ys = ((np.arange(ch)[:, None] + offs[None]) / ry).ravel()
    cxb, cyb = bx + bw / 2, by + bh / 2
    dx = np.maximum(np.abs(xs - cxb) - (bw / 2 - r), 0)[None, :]
    dy = np.maximum(np.abs(ys - cyb) - (bh / 2 - r), 0)[:, None]
    inx = ((xs >= bx) & (xs <= bx + bw))[None, :]
    iny = ((ys >= by) & (ys <= by + bh))[:, None]
    inside = inx & iny & (dx * dx + dy * dy <= r * r)
    cov = inside.reshape(ch, ss, cw, ss).mean(axis=(1, 3))
    return cov >= 0.999


def _zncc_rows(v: np.ndarray, m: np.ndarray) -> np.ndarray:
    v = v.astype(np.float64) - v.mean()
    m = m.astype(np.float64)
    m = m - m.mean(axis=1, keepdims=True)
    return (m @ v) / np.sqrt((m * m).sum(axis=1) * (v * v).sum())


# (relative scale change about the box centre, dx, dy) in competitor full-res px
_PERTURB = ((0.0, 0.0, 0.0), (0.005, 0.0, 0.0), (-0.005, 0.0, 0.0), (0.0, 2.0, 2.0), (0.0, -2.0, -2.0),
            (0.0, 2.0, -2.0), (0.0, -2.0, 2.0), (0.005, 2.0, -2.0), (-0.005, -2.0, 2.0))

# state of the self-check workers (spawned processes; big arrays arrive as memory-mapped .npy files)
_SC: dict[str, Any] = {}


def _sc_init(small: dict, comp_npy: str, raw_npy: str, raw_idx_npy: str) -> None:
    """Initializer of a spawned self-check worker: single-threaded OpenCV (3 workers already use 3
    cores), memory-mapped competitor ROIs and RAW proxies."""
    import cv2
    cv2.setNumThreads(1)
    _SC.clear()
    _SC.update(small)
    _SC["comp"] = np.load(comp_npy, mmap_mode="r")
    raw = np.load(raw_npy, mmap_mode="r")
    _SC["raws"] = {int(j): raw[i] for i, j in enumerate(np.load(raw_idx_npy))}


def _perturbed(sim: dict, box_c: tuple[float, float], ds: float, dx: float, dy: float) -> dict:
    """Scale by (1+ds) about the box centre, then translate by (dx, dy) competitor px."""
    f = 1.0 + ds
    return {"scale": sim["scale"] * f, "rotation_deg": sim["rotation_deg"],
            "tx": box_c[0] + f * (sim["tx"] - box_c[0]) + dx, "ty": box_c[1] + f * (sim["ty"] - box_c[1]) + dy}


def _frame_mask(k: int) -> np.ndarray:
    m = _SC["mask_base"].copy()
    for (x0, y0, x1, y1) in _SC["cap_boxes"].get(k, []):
        m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
    return m


def _self_check_frames(items: list[tuple]) -> list[tuple]:
    """Worker: worst margin (truth frame score - best of j±1, j±2) over the perturbations, per frame.
    The five candidates are warped as one 4-channel + one 1-channel image (identical per-channel result)."""
    import cv2
    st = _SC
    x, y, w, h = st["roi"]
    blur = st["blur"]
    kern = np.ones((3, 3), np.uint8)
    out = []
    for (k, j, sim, flip) in items:
        cimg = cv2.GaussianBlur(st["comp"][k].astype(np.float32), (0, 0), blur)
        mask = _frame_mask(k)
        F = np.array([[-1.0, 0, st["raw_w"]], [0, 1, 0], [0, 0, 1]]) if flip else np.eye(3)
        quad = np.dstack([st["raws"][j + d] for d in (-2, -1, 0, 1)]).astype(np.float32)
        last = st["raws"][j + 2].astype(np.float32)
        ones = np.full(last.shape, 255, np.uint8)
        worst = (math.inf, None, math.nan)
        nominal = (math.nan, math.nan)
        for (ds, dx, dy) in _PERTURB:
            sp = _perturbed(sim, st["box_c"], ds, dx, dy)
            M = _T(-x, -y) @ _D(*st["comp_ratio"]) @ sim_matrix(sp) @ F @ np.linalg.inv(_D(*st["raw_ratio"]))
            Mcv = corner_to_cv(M)
            ok = cv2.warpAffine(ones, Mcv, (w, h), flags=cv2.INTER_NEAREST)
            valid = mask & (cv2.erode(ok, kern) > 0)
            wq = cv2.GaussianBlur(cv2.warpAffine(quad, Mcv, (w, h), flags=cv2.INTER_LINEAR,
                                                 borderMode=cv2.BORDER_CONSTANT), (0, 0), blur)
            wl = cv2.GaussianBlur(cv2.warpAffine(last, Mcv, (w, h), flags=cv2.INTER_LINEAR,
                                                 borderMode=cv2.BORDER_CONSTANT), (0, 0), blur)
            rows = np.vstack([wq[valid].T, wl[valid][None]])            # order: j-2, j-1, j, j+1, j+2
            sc = _zncc_rows(cimg[valid], rows)
            margin = float(sc[2] - max(sc[0], sc[1], sc[3], sc[4]))
            if (ds, dx, dy) == (0.0, 0.0, 0.0):
                nominal = (float(sc[2]), margin)
            if margin < worst[0]:
                worst = (margin, (ds, dx, dy), float(sc[2]))
        out.append((k, j, worst[0], worst[1], worst[2], int(mask.sum()), nominal[0], nominal[1]))
    return out


def self_check(p: Profile, raw_mp4: Path, comp_mp4: Path, frames_truth: list[dict], segs: list[dict],
               captions: list[dict], scratch: Path, workers: int = MAX_PARALLEL) -> dict:
    """(a) every matchable competitor frame: the truth RAW frame beats j±1, j±2 by >= SELF_MARGIN_MIN masked
    ZNCC at the DESIGN proxy sizes under ±0.5 % scale / ±2 px perturbations (mask = rounded box minus the
    dilated caption bboxes); (b) >= SELF_INLIERS_MIN SIFT + RANSAC inliers (RAW -> comp, pairwise Lowe
    ratio) on the first / middle / last frame of every segment. Returns the statistics."""
    import cv2
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    cfg = _cfg()
    (rw, rh), (cw, ch) = proxy_sizes(p, cfg)
    raw_ratio = (rw / p.raw_w, rh / p.raw_h)
    comp_ratio = (cw / p.comp_w, ch / p.comp_h)
    seg_by_id = {s["id"]: s for s in segs}
    bx, by, bw, bh, _ = p.box
    x0, y0 = int(math.floor(bx * comp_ratio[0])), int(math.floor(by * comp_ratio[1]))
    x1, y1 = int(math.ceil((bx + bw) * comp_ratio[0])), int(math.ceil((by + bh) * comp_ratio[1]))
    roi = (x0, y0, x1 - x0, y1 - y0)
    items, need = [], set()
    for fr in frames_truth:
        if fr["raw_a"] is None or (fr["alpha_b"] is not None and fr["alpha_b"] > 0):
            continue
        s = seg_by_id[fr["seg"]]
        items.append((fr["k"], fr["raw_a"], _sim_at(s, fr["k"]), s["flip"]))
        need.update(range(fr["raw_a"] - 2, fr["raw_a"] + 3))
    comp_d = decode_gray(comp_mp4, (cw, ch), roi=roi)
    comp = np.stack([comp_d[k] for k in range(len(comp_d))])
    del comp_d
    raws = decode_gray(raw_mp4, (rw, rh), keep=lambda i: i in need)
    dil = 3
    cap_boxes: dict[int, list] = {}
    for c in captions:
        b = (int(math.floor(c["x"] * comp_ratio[0])) - dil - x0, int(math.floor(c["y"] * comp_ratio[1])) - dil - y0,
             int(math.ceil((c["x"] + c["w"]) * comp_ratio[0])) + dil - x0,
             int(math.ceil((c["y"] + c["h"]) * comp_ratio[1])) + dil - y0)
        for k in range(c["k_in"], c["k_out"]):
            cap_boxes.setdefault(k, []).append(b)
    box_c = (bx + bw / 2, by + bh / 2)
    small = dict(mask_base=_box_mask(p, cw, ch)[y0:y1, x0:x1], cap_boxes=cap_boxes, roi=roi, raw_w=p.raw_w,
                 raw_ratio=raw_ratio, comp_ratio=comp_ratio, blur=float(cfg["score_blur"]), box_c=box_c)
    _SC.clear()
    _SC.update(small, comp=comp, raws=raws)
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    npys = [scratch / "selfcheck_comp.npy", scratch / "selfcheck_raw.npy", scratch / "selfcheck_raw_idx.npy"]
    try:
        if workers > 1:
            idx = np.array(sorted(raws), np.int64)
            np.save(npys[0], comp)
            np.save(npys[1], np.stack([raws[int(j)] for j in idx]))
            np.save(npys[2], idx)
            batches = [items[i::workers] for i in range(workers)]
            # spawn (not fork): a forked child must not inherit OpenCV's / PyAV's live thread pools
            with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"), initializer=_sc_init,
                                     initargs=(small, *map(str, npys))) as ex:
                res = [r for part in ex.map(_self_check_frames, batches) for r in part]
        else:
            res = _self_check_frames(items)
        res.sort(key=lambda r: r[0])
        # (b) RANSAC inliers ------------------------------------------------------------------------------
        sift = cv2.SIFT_create(nfeatures=int(cfg["sift_nfeatures"]))
        bf = cv2.BFMatcher(cv2.NORM_L2)
        inl: dict[str, int] = {}
        centre_err: dict[str, float | None] = {}
        for s in segs:
            if s["type"] != "raw":
                continue
            for k in sorted({s["comp_in"], (s["comp_in"] + s["comp_out"] - 1) // 2, s["comp_out"] - 1}):
                fr = frames_truth[k]
                if fr["seg"] != s["id"] or fr["raw_a"] is None or (fr["alpha_b"] or 0) > 0:
                    continue
                j = fr["raw_a"]
                m = _frame_mask(k).astype(np.uint8) * 255
                cv2.setRNGSeed(12345)
                kc, dc = sift.detectAndCompute(comp[k], m)
                rimg = cv2.flip(raws[j], 1) if s["flip"] else raws[j]
                kr, dr = sift.detectAndCompute(rimg, None)
                n_in, err = 0, None
                if dc is not None and dr is not None and len(kc) >= 3 and len(kr) >= 3:
                    good = [a for a, b in (mm for mm in bf.knnMatch(dc, dr, k=2) if len(mm) == 2)
                            if a.distance < cfg["lowe_ratio"] * b.distance]
                    if len(good) >= 3:
                        src = np.float32([kr[g.trainIdx].pt for g in good])
                        dst = np.float32([kc[g.queryIdx].pt for g in good]) + np.float32([x0, y0])
                        M, inl_mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                                  ransacReprojThreshold=cfg["ransac_reproj_px"])
                        if M is not None:
                            n_in = int(inl_mask.sum())
                            Mc = _T(0.5, 0.5) @ np.vstack([M, [0, 0, 1]]) @ _T(-0.5, -0.5)
                            Mf = np.linalg.inv(_D(*comp_ratio)) @ Mc @ _D(*raw_ratio)
                            pc = np.array([box_c[0], box_c[1], 1.0])
                            pr = np.linalg.inv(sim_matrix(_sim_at(s, k))) @ pc
                            err = float(np.linalg.norm((Mf @ pr)[:2] - pc[:2]))
                key = f"{s['id']}@{k}"
                inl[key] = n_in
                centre_err[key] = err
    finally:
        _SC.clear()
        for f in npys:
            f.unlink(missing_ok=True)
    margins = np.array([r[2] for r in res])
    worst = min(res, key=lambda r: r[2])
    per_seg: dict[int, dict] = {}
    for (k, j, mg, pert, sc, npx, _nom, _nom_m) in res:
        d = per_seg.setdefault(frames_truth[k]["seg"], {"min_margin": math.inf, "min_score": math.inf,
                                                         "k_min": None, "min_pixels": 10 ** 9})
        if mg < d["min_margin"]:
            d.update(min_margin=mg, k_min=k)
        d["min_score"] = min(d["min_score"], sc)
        d["min_pixels"] = min(d["min_pixels"], npx)
    fails_m = [r for r in res if r[2] < SELF_MARGIN_MIN]
    fails_i = {k: v for k, v in inl.items() if v < SELF_INLIERS_MIN}
    return {
        "proxy_raw": [rw, rh], "proxy_comp": [cw, ch], "frames_checked": len(res),
        "perturbations": [list(t) for t in _PERTURB],
        "min_margin": float(margins.min()), "median_margin": float(np.median(margins)),
        "worst": {"k": worst[0], "raw": worst[1], "margin": worst[2], "perturbation": list(worst[3]),
                  "score": worst[4]},
        "min_score": float(min(r[4] for r in res)),
        "nominal_min_score": float(min(r[6] for r in res)),
        "nominal_median_score": float(np.median([r[6] for r in res])),
        "nominal_min_margin": float(min(r[7] for r in res)),
        "per_segment": {str(k): v for k, v in sorted(per_seg.items())},
        "margin_failures": [{"k": r[0], "raw": r[1], "margin": r[2], "perturbation": list(r[3])}
                            for r in [x for x in res if x[2] < SELF_MARGIN_MIN][:50]],
        "n_margin_failures": len(fails_m),
        "min_inliers": int(min(inl.values())) if inl else 0,
        "inliers": inl,
        "ransac_centre_err_px": centre_err,
        "inlier_failures": fails_i,
        "ok": bool(not fails_m and not fails_i and inl),
    }


def audio_self_check(raw_mp4: Path, comp_mp4: Path, segs: list[dict], max_lag_ms: float = 1.0) -> dict:
    """Independent audio truth check: per RAW segment, cross-correlate the competitor audio (decoded from
    competitor.mp4, mono) with the RAW audio (decoded from raw.mp4) played from the segment's audio
    raw_in at its speed (tape-style resample for v != 1); the lag must be < max_lag_ms. Crossfade
    overlaps are excluded. The NOT-IN-RAW tone must NOT correlate with the RAW at the neighbours."""
    from scipy.signal import correlate, resample_poly
    sr = AUDIO_SR
    raw = _decode_audio(raw_mp4, sr)
    comp = _decode_audio(comp_mp4, sr)
    res, bad = {}, {}
    for s in segs:
        if s["type"] != "raw":
            continue
        k0 = s["comp_in"] + (s["transition_in"]["D"] if s["transition_in"] else 0)
        k1 = s["comp_out"] - (s["transition_out"]["D"] if s["transition_out"] else 0)
        if k1 - k0 < 12:
            continue
        v = Fraction(s["speed_str"])
        c = comp[k0 * SAMPLES_PER_COMP_FRAME:k1 * SAMPLES_PER_COMP_FRAME].astype(np.float64)
        r0 = s["audio"]["raw_in_seconds"] + float(v) * (k0 - s["comp_in"]) / 30.0
        pad = int(0.02 * sr)
        i0 = int(round(r0 * sr)) - int(round(pad * float(v)))
        n_src = int(math.ceil((len(c) + 2 * pad) * float(v)))
        ref = raw[max(0, i0):i0 + n_src].astype(np.float64)
        if v != 1:
            ref = resample_poly(ref, v.denominator, v.numerator)     # tape-style: v x faster, pitch up
        xc = correlate(ref, c, mode="valid", method="fft")
        lag = int(np.argmax(xc)) - pad
        lag_ms = 1000.0 * lag / sr
        peak = float(xc.max() / (np.linalg.norm(c) * np.linalg.norm(ref[pad:pad + len(c)]) + 1e-12))
        res[str(s["id"])] = {"lag_ms": lag_ms, "corr": peak}
        if abs(lag_ms) >= max_lag_ms:
            bad[str(s["id"])] = lag_ms
    return {"segments": res, "max_abs_lag_ms": max((abs(v["lag_ms"]) for v in res.values()), default=0.0),
            "min_corr": min((v["corr"] for v in res.values()), default=0.0), "failures": bad, "ok": not bad}


def _decode_audio(path: Path, sr: int) -> np.ndarray:
    raw = run_ffmpeg(["-i", str(path), "-map", "0:a", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture=True,
                     label="decode audio")
    return np.frombuffer(raw, np.float32)


def _sim_at(seg: dict, k: int) -> dict:
    keys = seg.get("transform_keys") or []
    if not keys:
        return seg["transform"]
    if k <= keys[0]["comp_frame"]:
        a = b = keys[0]
        u = 0.0
    elif k >= keys[-1]["comp_frame"]:
        a = b = keys[-1]
        u = 0.0
    else:
        for a, b in zip(keys[:-1], keys[1:]):
            if a["comp_frame"] <= k <= b["comp_frame"]:
                break
        u = (k - a["comp_frame"]) / (b["comp_frame"] - a["comp_frame"])
    return {f: a[f] + u * (b[f] - a[f]) for f in ("scale", "rotation_deg", "tx", "ty")}


# =====================================================================================================
# Truth assembly
# =====================================================================================================

def build_truth_segments(p: Profile, chains: list[Chain], audio_info: dict) \
        -> tuple[list[dict], list[dict], list[dict]]:
    """Truth segments (the punch-in chain yields two) + per-competitor-frame truth."""
    segs: list[dict] = []
    for c in chains:
        spec = c.spec
        pieces = [(0, spec.n)]
        if spec.punch_at is not None:
            pieces = [(0, spec.punch_at), (spec.punch_at, spec.n)]
        for pi, (a, b) in enumerate(pieces):
            kind = spec.kind
            if spec.punch_at is not None:
                kind = "normal" if pi == 0 else "punchin"
            sid = len(segs) + 1
            seg = {"id": sid, "chain": c.index, "kind": kind, "type": "raw" if c.in_raw else "not_in_raw",
                   "comp_in": c.comp_in + a, "comp_out": c.comp_in + b, "note": spec.note,
                   "shot": spec.shot, "shot_name": SHOTS[spec.shot].name if c.in_raw else None,
                   "speed": float(Fraction(spec.speed)), "speed_str": spec.speed, "flip": bool(spec.flip),
                   "transition_in": None, "transition_out": None, "transform": None, "transform_keys": [],
                   "animated": False,
                   # DESIGN §7 D1: box in force during the segment (None = the dominant rounded box, region 0;
                   # the whole canvas with radius 0 inside a fullscreen layout period, region 1)
                   "layout_mode": "fullscreen" if is_fullscreen(spec) else "boxed",
                   "box": ({"x": 0, "y": 0, "w": p.comp_w, "h": p.comp_h, "corner_radius": 0}
                           if is_fullscreen(spec) else None),
                   "region": 1 if is_fullscreen(spec) else 0}
            if c.in_raw:
                g = c.geom
                fr = c.frames[a:b]
                seg.update(raw_in_frame=int(fr[0]), raw_out_frame=int(fr[-1]),
                           raw_frames=[int(x) for x in fr],
                           chain_raw_start=c.j, ss=c.ss, timing_filter=c.timing, geometry_filter=g.filters(),
                           setpts_phase_ticks=c.phase,
                           geometry={"flip": g.flip, "scale_w": g.sw, "scale_h": g.sh, "crop_x": g.cx,
                                     "crop_y": g.cy, "box_x": g.bx, "box_y": g.by, "box_w": g.bw, "box_h": g.bh,
                                     "zoom_first": g.zoom(a),
                                     "zoom_last": g.zoom(b - 1), "push_a": g.push_a, "punch_at": g.punch_at})
                seg["transform"] = g.truth_sim(a)
                if g.push_a is not None:
                    seg["animated"] = True
                    seg["transform_keys"] = [{"comp_frame": c.comp_in + n, **g.truth_sim(n)} for n in (a, b - 1)]
                    for n in range(a, b):            # 2 linear keys must reproduce every frame
                        want = g.truth_sim(n)
                        got = _sim_at(seg, c.comp_in + n)
                        if abs(got["scale"] - want["scale"]) > 1e-9 or abs(got["tx"] - want["tx"]) > 1e-6 or \
                                abs(got["ty"] - want["ty"]) > 1e-6:
                            raise RuntimeError("push-in truth is not linear in the frame index")
                ai = audio_info[c.index]
                # interval of THIS piece (the punch-in pieces share the chain's linear map)
                lo, hi = floor_interval(fr, spec.speed)
                seg["raw_in_interval_floor"] = [float(lo), float(hi)]
                seg["raw_in_seconds_chain_audio"] = ai["raw_in_seconds"] + float(
                    Fraction(spec.speed) * Fraction(a) / COMP_FPS)
                seg["audio"] = {"in_offset_frames": 0, "out_offset_frames": 0,
                                "pitch_preserved": False if Fraction(spec.speed) != 1 else None,
                                "exception": None, "raw_in_seconds": seg["raw_in_seconds_chain_audio"],
                                "in_point": ai.get("in_point")}
            else:
                seg.update(raw_in_frame=None, raw_out_frame=None, raw_frames=[], label="NOT-IN-RAW insert",
                           generator="gradients+drawtext", audio={"in_offset_frames": 0, "out_offset_frames": 0,
                                                                   "pitch_preserved": None,
                                                                   "exception": "not_in_raw",
                                                                   "raw_in_seconds": None, "tone_hz": 2960})
            segs.append(seg)
    # crossfades: A.comp_out = O + D (already: A chain has n incl. D), B.comp_in = O
    by_chain: dict[int, list[dict]] = {}
    for s in segs:
        by_chain.setdefault(s["chain"], []).append(s)
    transitions = []
    for c in chains:
        if c.spec.xfade:
            A = by_chain[c.index][-1]
            B = by_chain[c.index + 1][0]
            D = c.spec.xfade
            O = B["comp_in"]
            if A["comp_out"] != O + D:
                raise RuntimeError("crossfade bookkeeping error")
            alpha = [i / D for i in range(D)]
            tr = {"type": "crossfade", "O": O, "D": D, "duration_frames": D, "alpha": alpha,
                  "a_seg": A["id"], "b_seg": B["id"]}
            A["transition_out"] = dict(tr)
            B["transition_in"] = dict(tr)
            transitions.append(tr)
    n_comp = max(s["comp_out"] for s in segs)
    frames: list[dict] = [None] * n_comp  # type: ignore[list-item]
    for s in segs:
        for i, k in enumerate(range(s["comp_in"], s["comp_out"])):
            rj = s["raw_frames"][i] if s["type"] == "raw" else None
            if frames[k] is None:
                frames[k] = {"k": k, "seg": s["id"], "raw_a": rj, "seg_b": None, "raw_b": None, "alpha_b": None}
            else:   # overlap = crossfade: existing entry is A, this is B
                tr = s["transition_in"]
                frames[k].update(seg_b=s["id"], raw_b=rj, alpha_b=(k - tr["O"]) / tr["D"])
    if any(f is None for f in frames):
        raise RuntimeError("truth does not tile the competitor timeline")
    return segs, frames, transitions


def layout_periods(p: Profile, segs: list[dict]) -> list[dict]:
    """Truth layout periods (half-open, tiling the timeline): runs of segments with the same layout mode
    ('boxed' = the rounded box, 'fullscreen' = the whole canvas); a crossfade overlap belongs to its boxed
    neighbours."""
    bx, by, bw, bh, br = p.box
    boxed = {"x": bx, "y": by, "w": bw, "h": bh, "corner_radius": br}
    out: list[dict] = []
    for s in sorted(segs, key=lambda d: (d["comp_in"], d["comp_out"])):
        mode = s["layout_mode"]
        if out and out[-1]["mode"] == mode and s["comp_in"] <= out[-1]["comp_out"]:
            out[-1]["comp_out"] = max(out[-1]["comp_out"], s["comp_out"])
        else:
            if out and s["comp_in"] != out[-1]["comp_out"]:
                raise RuntimeError(f"layout periods do not tile the timeline at {s['comp_in']}")
            out.append({"comp_in": s["comp_in"], "comp_out": s["comp_out"], "mode": mode,
                        "box": dict(s["box"]) if s["box"] else dict(boxed), "region": s["region"]})
    return out


# =====================================================================================================
# Main entry
# =====================================================================================================

def synth_key(profile: Profile) -> str:
    src = Path(__file__).read_bytes()
    blob = json.dumps({"v": SYNTH_VERSION, "ffmpeg": ffmpeg_version(), "src": hashlib.sha256(src).hexdigest(),
                       "profile": dataclasses.asdict(profile)}, sort_keys=True, default=_json_default)
    return hashlib.blake2b(blob.encode(), digest_size=12).hexdigest()


@contextlib.contextmanager
def _locked(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _cached(out: Path, key: str) -> dict | None:
    tj = out / "truth.json"
    if not tj.exists():
        return None
    try:
        truth = json.loads(tj.read_text())
    except Exception:
        return None
    if truth.get("key") != key:
        return None
    for name, meta in truth.get("files", {}).items():
        fp = out / name
        if not fp.exists() or fp.stat().st_size != meta["bytes"]:
            return None
    return truth


def _result(out: Path, truth: dict) -> dict:
    rep = {}
    rp = out / "synth_report.json"
    if rp.exists():
        rep = json.loads(rp.read_text())
    return {"raw": str(out / "raw.mp4"), "competitor": str(out / "competitor.mp4"), "truth": str(out / "truth.json"),
            "id": str(out / "id.mp4"), "frame_png": str(out / "frame.png"), "out_dir": str(out),
            "summary": {**truth.get("summary", {}), "timings": rep.get("timings", {})}}


def make_synthetic(out_dir: str | os.PathLike, profile: str = "full", force: bool = False,
                   keep_build: bool = False) -> dict:
    """Generate (or reuse) the synthetic RAW / competitor / ID videos + truth.json in out_dir.

    Returns {raw, competitor, truth, id, frame_png, out_dir, summary}. Cached by a key over the ffmpeg
    version, this file's source and the profile; deterministic (pinned encoder args)."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r} (expected one of {sorted(PROFILES)})")
    p = PROFILES[profile]
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    key = synth_key(p)
    with _locked(out / ".synth.lock"):
        if not force:
            truth = _cached(out, key)
            if truth is not None:
                log.info("synthetic %s: cached (%s)", profile, out)
                return _result(out, truth)
        truth = _generate(p, out, key, keep_build)
        return _result(out, truth)


def _generate(p: Profile, out: Path, key: str, keep_build: bool) -> dict:
    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    build = out / "_build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    for name in ("raw.mp4", "competitor.mp4", "id.mp4", "truth.json", "frame.png", "synth_report.json"):
        (out / name).unlink(missing_ok=True)
    raw_mp4, comp_mp4, id_mp4 = out / "raw.mp4", out / "competitor.mp4", out / "id.mp4"

    # ---- plan: chains, geometry, safe region, overlays -------------------------------------------------
    chains = resolve_chains(p)
    n_comp = sum(c.spec.n - c.spec.xfade for c in chains)
    lp = layout_plan(p)
    band = (lp.caption_y - p.u(30), lp.caption_y + lp.caption_fs + p.u(40))
    geoms = [(c.spec, c.geom) for c in chains if c.in_raw]
    safe = safe_region(p, geoms, band)
    ov = plan_overlays(p, safe)
    log.info("synthetic %s: %d chains, %d competitor frames, SAFE REGION %s", p.name, len(chains), n_comp, safe)

    # ---- geometry calibration (own numpy truth vs the exact ffmpeg filter strings) ---------------------
    t0 = time.perf_counter()
    tex_png = build / "calib.png"
    tex = make_calibration_texture(tex_png, p.raw_w, p.raw_h)
    calib_jobs = []
    for c in chains:
        if not c.in_raw:
            continue
        nfr = c.spec.n if c.geom.animated else 2
        calib_jobs.append(lambda c=c, nfr=nfr: (c.index, verify_geometry(c.geom, tex, tex_png, nfr)))
    calib = dict(parallel(calib_jobs))
    bad = {i: (r["max_dpos"], r["max_ds"]) for i, r in calib.items() if not r["ok"]}
    if bad:
        raise RuntimeError(f"geometry calibration failed (chain: (dpos px, dscale)): {bad}")
    timings["calibration_s"] = time.perf_counter() - t0

    # ---- RAW + ID video --------------------------------------------------------------------------------
    t0 = time.perf_counter()
    raw_info = generate_raw(p, build, raw_mp4, ov)
    make_id_video(id_mp4, p.raw_frames)
    timings.update(raw_shots_s=raw_info["shots_s"], raw_encode_s=raw_info["encode_s"],
                   raw_total_s=time.perf_counter() - t0)
    for sp in build.glob("shot_*.nut"):
        sp.unlink()
    pr, tbr = decode_pts(raw_mp4)
    pi, tbi = decode_pts(id_mp4)
    if len(pr) != p.raw_frames:
        raise RuntimeError(f"raw.mp4 has {len(pr)} frames, expected {p.raw_frames}")
    if [Fraction(x) * tbr for x in pr] != [Fraction(x) * tbi for x in pi] or tbr != RAW_TB:
        raise RuntimeError("id.mp4 PTS differ from raw.mp4 PTS (or time base is not 1/30000)")
    if [Fraction(x) * tbr for x in pr] != [Fraction(i) / RAW_FPS for i in range(p.raw_frames)]:
        raise RuntimeError("raw.mp4 PTS are not i/fps")

    # ---- truth timing: every chain on the ID video ------------------------------------------------------
    t0 = time.perf_counter()
    in_raw = [c for c in chains if c.in_raw]
    got = parallel([lambda c=c: decode_id_chain(id_mp4, c) for c in in_raw])
    for c, fr in zip(in_raw, got):
        c.frames = fr
        if not np.array_equal(fr, c.expected):
            diff = np.nonzero(fr != c.expected)[0]
            raise RuntimeError(f"ID chain {c.index} differs from the timing model at local frames {diff[:10]}: "
                               f"measured {fr[diff[:10]]}, model {c.expected[diff[:10]]}")
    idl, idr = id_composite(id_mp4, chains)
    if len(idl) != n_comp:
        raise RuntimeError(f"ID composite has {len(idl)} frames, expected {n_comp}")
    timings["id_truth_s"] = time.perf_counter() - t0

    # ---- competitor video ------------------------------------------------------------------------------
    t0 = time.perf_counter()
    seg_paths = [build / f"chain_{c.index:02d}.nut" for c in chains]
    parallel([lambda c=c, d=d: render_chain(raw_mp4, c, d, p) for c, d in zip(chains, seg_paths)])
    for c, d in zip(chains, seg_paths):
        cnt = count_frames(d)
        if cnt != c.spec.n:
            raise RuntimeError(f"chain {c.index} intermediate has {cnt} frames, expected {c.spec.n}")
    frame_png = out / "frame.png"
    render_frame_png(frame_png, lp)
    zones = measure_zones(frame_png, lp)
    caps = plan_captions(p, n_comp, p.caption_seed)
    cap_truth = measure_captions(caps, lp, n_comp)
    for c in cap_truth:
        if c["y"] < band[0] or c["y"] + c["h"] > band[1]:
            raise RuntimeError(f"caption {c} outside the planned caption band {band}")
    bx, by, bw, bh, br = p.box
    fs_ranges = fullscreen_ranges(chains)
    glyph = {}
    ins = []
    for d in seg_paths:
        ins += ["-i", str(d)]
    ins += ["-loop", "1", "-framerate", "30", "-i", str(frame_png)]
    if fs_ranges:
        glyph_png = build / "glyph_layer.png"
        render_glyph_png(glyph_png, lp)
        glyph = check_glyph_png(frame_png, glyph_png, lp)
        ins += ["-loop", "1", "-framerate", "30", "-i", str(glyph_png)]
    fc = competitor_video_filter(p, chains, caps, lp)
    comp_video = build / "competitor_video.mp4"
    run_ffmpeg(["-y", *ins, "-filter_complex", fc, "-map", "[vout]", "-an", *X264_COMP, *BITEXACT,
                str(comp_video)], label="competitor video")
    # the fullscreen frames must be exactly the planned ones (picture outside the box; black elsewhere)
    fs_got, fs_frac = measure_fullscreen_frames(comp_video, p, lp, zones)
    fs_want = [k for a, b in fs_ranges for k in range(a, b)]
    if fs_got != fs_want:
        raise RuntimeError(f"fullscreen frames {fs_got[:10]}... != planned {fs_want[:10]}... "
                           f"(non-black fraction outside the box: {np.round(fs_frac[:20], 3).tolist()} ...)")
    fs_measure = {"frames": len(fs_got),
                  "min_fraction_inside": float(fs_frac[fs_want].min()) if fs_want else None,
                  "max_fraction_outside": float(np.delete(fs_frac, fs_want).max()), "glyph_layer": glyph}
    timings["competitor_video_s"] = time.perf_counter() - t0

    # ---- audio ------------------------------------------------------------------------------------------
    t0 = time.perf_counter()
    raw_dec = build / "raw_decoded.wav"
    run_ffmpeg(["-y", "-i", str(raw_mp4), "-map", "0:a", "-c:a", "pcm_f32le", str(raw_dec)], label="decode raw audio")
    comp_wav = build / "competitor_audio.wav"
    audio_info = build_competitor_audio(p, chains, raw_dec, comp_wav, n_comp)
    run_ffmpeg(["-y", "-i", str(comp_video), "-i", str(comp_wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SR), *BITEXACT, str(comp_mp4)], label="mux")
    timings["audio_s"] = time.perf_counter() - t0

    # ---- competitor assertions --------------------------------------------------------------------------
    pc, tbc = decode_pts(comp_mp4)
    if len(pc) != n_comp or len(pc) != len(idl):
        raise RuntimeError(f"competitor has {len(pc)} frames; ID composite {len(idl)}; planned {n_comp}")
    if [Fraction(x) * tbc for x in pc] != [Fraction(i, 30) for i in range(n_comp)]:
        raise RuntimeError("competitor PTS are not k/30")

    # ---- truth tables -----------------------------------------------------------------------------------
    segs, frames, transitions = build_truth_segments(p, chains, audio_info)
    for fr in frames:        # whole-graph ID decode must agree with the per-chain truth
        k = fr["k"]
        want_l = -1 if fr["raw_a"] is None else fr["raw_a"]
        want_r = want_l if fr["seg_b"] is None else fr["raw_b"]
        if idl[k] != want_l or idr[k] != want_r:
            raise RuntimeError(f"ID composite frame {k}: ({idl[k]}, {idr[k]}) != truth ({want_l}, {want_r})")

    # ---- self-checks at proxy sizes ---------------------------------------------------------------------
    t0 = time.perf_counter()
    sc = self_check(p, raw_mp4, comp_mp4, frames, segs, cap_truth, build)
    asc = audio_self_check(raw_mp4, comp_mp4, segs)
    sc["audio"] = asc
    timings["self_check_s"] = time.perf_counter() - t0
    if not asc["ok"]:
        raise RuntimeError(f"audio self-check failed (segment: lag ms): {asc['failures']}")
    if not sc["ok"]:
        write_json(out / "self_check_failed.json", sc)
        raise RuntimeError(f"synthetic self-check failed: min margin {sc['min_margin']:.4f} "
                           f"({sc['n_margin_failures']} frames < {SELF_MARGIN_MIN}), min inliers {sc['min_inliers']}"
                           f" (failures {sc['inlier_failures']}); details in {out / 'self_check_failed.json'}")
    (out / "self_check_failed.json").unlink(missing_ok=True)

    # ---- write truth ------------------------------------------------------------------------------------
    cuts = []
    for a, b in zip(segs[:-1], segs[1:]):
        typ = "crossfade" if b["transition_in"] else ("punch_in" if b["kind"] == "punchin" else "cut")
        cuts.append({"k": b["comp_in"], "a_seg": a["id"], "b_seg": b["id"], "type": typ,
                     "b_kind": b["kind"], "ambiguity": None})
    raw_samples = raw_info["audio_samples"]
    n_music = n_comp
    truth = {
        "version": SYNTH_VERSION, "key": key, "profile": p.name, "ffmpeg_version": ffmpeg_version(),
        "raw": {"file": "raw.mp4", "width": p.raw_w, "height": p.raw_h, "fps": fps_str(RAW_FPS),
                "frames": p.raw_frames, "time_base": fps_str(RAW_TB), "audio_sr": AUDIO_SR,
                "audio_samples": raw_samples, "audio_channels": 1,
                "shots": [{"index": i, "name": s.name, "raw_in": i * p.shot_len, "raw_out": (i + 1) * p.shot_len,
                           "filter": shot_graph(p, s), "skip": s.skip} for i, s in enumerate(SHOTS[:p.n_shots])],
                "overlays": {"grid": list(ov.grid), "grid_cell": ov.cell, "counter_centre_x": ov.counter_x,
                             "counter_top_y": ov.counter_y, "counter_fontsize": ov.counter_fs,
                             "safe_region": list(safe)}},
        "competitor": {"file": "competitor.mp4", "width": p.comp_w, "height": p.comp_h, "fps": fps_str(COMP_FPS),
                       "frames": n_comp, "audio_sr": AUDIO_SR, "audio_channels": 2},
        "id": {"file": "id.mp4", "width": ID_W, "height": ID_H, "fps": fps_str(RAW_FPS), "frames": p.raw_frames,
               "code": "16-bit, 16 px blocks, top row code / bottom row complement, halves duplicated"},
        "frames": frames,
        "segments": segs,
        "cuts": cuts,
        "transitions": transitions,
        "not_in_raw": [{"comp_in": s["comp_in"], "comp_out": s["comp_out"], "seg": s["id"]}
                       for s in segs if s["type"] == "not_in_raw"],
        "layout": {"canvas": {"w": p.comp_w, "h": p.comp_h}, "canvas_bg": "#000000", "background": "solid",
                   "box": {"x": bx, "y": by, "w": bw, "h": bh, "radius": br, "corner_radius": br},
                   "zones": zones, "captions": cap_truth,
                   "caption_band": [int(band[0]), int(band[1])],
                   # D1: layout changes over time -- the fullscreen chain shows the RAW over the whole
                   # canvas with only the glyph layer (logo, channel, title, watermark) and captions on top
                   "periods": layout_periods(p, segs),
                   "fullscreen": [{"comp_in": a, "comp_out": b} for a, b in fs_ranges],
                   "fullscreen_measurement": fs_measure},
        "audio": {"sr": AUDIO_SR, "pitch_preserved": False, "status": "ok",
                  "segments": {str(s["id"]): s["audio"] for s in segs},
                  "added_audio": [{"type": "music", "comp_in": 0, "comp_out": n_music, "level_db": -12.0}],
                  "speed_method": "asetrate+aresample (tape, pitch not preserved)",
                  "crossfade": "acrossfade tri/tri over the video overlap"},
        "calibration": {str(i): {"max_dpos": r["max_dpos"], "max_ds": r["max_ds"], "frames": r["frames"]}
                        for i, r in sorted(calib.items())},
        "self_check": sc,
        "conventions": {"transform": "canonical Sim: flipped-RAW full-res px -> competitor px (CORNER)",
                        "transform_keys": "absolute comp_frame, linear", "crossfade": "alpha_b(k)=(k-O)/D",
                        "intervals": "half-open [comp_in, comp_out)", "raw_in_interval_floor": "seconds, AE floor rule",
                        "audio_raw_in": "NLE in-point: first sample at/after the LOWER bound of the chain's floor-"
                                        "rule raw_in interval (the first RAW frame's boundary), DESIGN §7 D8",
                        "segment_box": "null = layout.box (region 0); fullscreen = whole canvas, radius 0 (region 1)"},
    }
    files = {}
    for name in ("raw.mp4", "competitor.mp4", "id.mp4", "frame.png"):
        files[name] = {"bytes": (out / name).stat().st_size, "blake2b": file_digest(out / name)}
    truth["files"] = files
    truth["summary"] = _summary(p, truth)
    write_json(out / "truth.json", truth)
    timings["total_s"] = time.perf_counter() - t_all
    write_json(out / "synth_report.json", {"timings": timings, "profile": p.name, "key": key})
    if not keep_build:
        shutil.rmtree(build, ignore_errors=True)
    log.info("synthetic %s done in %.1fs", p.name, timings["total_s"])
    return truth


def _summary(p: Profile, truth: dict) -> dict:
    segs = truth["segments"]
    sc = truth["self_check"]
    return {
        "profile": p.name,
        "raw_frames": truth["raw"]["frames"], "raw_duration_s": truth["raw"]["frames"] / float(RAW_FPS),
        "competitor_frames": truth["competitor"]["frames"],
        "competitor_duration_s": truth["competitor"]["frames"] / 30.0,
        "segments": len(segs), "cuts": len(truth["cuts"]),
        "segment_table": [f"{s['id']:2d} {s['kind']:10s} comp[{s['comp_in']:5d},{s['comp_out']:5d}) "
                          f"raw {s['raw_in_frame'] if s['raw_in_frame'] is not None else '-':>5} "
                          f"v={s['speed_str']:>4} flip={int(s['flip'])} "
                          f"s={s['transform']['scale'] if s['transform'] else 0:.5f}"
                          + (f" audio_in={s['audio']['raw_in_seconds']:.6f}" if s["type"] == "raw" else "")
                          + (" animated" if s["animated"] else "")
                          + (" FULLSCREEN box=canvas" if s.get("layout_mode") == "fullscreen" else "")
                          for s in segs],
        "layout_periods": [f"[{d['comp_in']},{d['comp_out']}) {d['mode']}" for d in truth["layout"]["periods"]],
        "self_check_min_margin": sc["min_margin"], "self_check_median_margin": sc["median_margin"],
        "self_check_min_inliers": sc["min_inliers"],
        "calibration_max_dpos": max(v["max_dpos"] for v in truth["calibration"].values()),
        "calibration_max_ds": max(v["max_ds"] for v in truth["calibration"].values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the match_cuts synthetic RAW + competitor + truth")
    ap.add_argument("--profile", default="mini", choices=sorted(PROFILES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-build", action="store_true", help="keep the lossless intermediates in <out>/_build")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    res = make_synthetic(a.out, a.profile, force=a.force, keep_build=a.keep_build)
    print(json.dumps(res, indent=1, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
