"""Stage 2b — conform inputs to AE-safe media (DESIGN.md §5 conform.py, prompt Stage 2).

RAW:
  * AE-safe            -> hard-linked (or copied) into ``output/media/`` unchanged, or referenced by
                          absolute path when larger than ``cfg.large_file_bytes`` (the JSX relinks).
  * not AE-safe        -> ``output/media/raw_ae.mov`` (ProRes 422 LT, prores_aw profile 1, PCM s16le
                          48 kHz) for <= 10 min, else ``raw_ae.mp4`` (H.264 CRF 12, AAC 48 kHz).
COMPETITOR (reference layer + analysis): always ``output/media/competitor_ref.mp4`` (H.264 + AAC):
  copied when AE-safe mp4/H.264/AAC, else transcoded.

Every transcode keeps the resolution (display orientation, square pixels — rotation and SAR are baked
in, odd sizes cropped by one px) and the NOMINAL frame rate, is CFR and starts at 0:
  * CFR sources are re-stamped by frame index (``settb=1/fps,setpts=N``): immune to ms-rounded
    timestamps (WebM/MKV), frame k of the output IS decoded frame k of the source;
  * VFR sources use ``fps=fps=N/D:round=up`` = "the frame displayed at t_k" (verified). Stored PTS are
    QUANTISED (1 ms in MKV/WebM, 1/600 s in phone MOVs, ...): a frame rounded late by < 1 tick would
    land one slot late and collide with its successor (dropping ~1/3 of the frames of an ms-timebase
    29.97 file), so every PTS is shifted back by ``pts_shift = min(1 tick, 1/(4·fps))`` before the
    ``fps`` filter (``settb=T,setpts=max(PTS-STARTPTS-D,0)``). The last frame is cloned for a short
    while BEFORE the ``fps`` filter (``tpad``; a container that gives the last frame a 1-tick duration
    would otherwise end the stream before the final slot), then trimmed to exactly
    #{k : k/fps < last_pts - pts_shift + median_frame_duration} frames;
  * audio is re-based so that sample 0 is video frame 0 (start offsets / edit lists removed);
  * ``-fps_mode passthrough`` on ffmpeg >= 5.1, ``-vsync 0`` on older builds.
Transcodes are verified: exact frame count, AE-safety of the result, and >= 50 frames sampled by PTS
whose SSIM against their source frame is > 0.98 and higher than against the source's neighbours.
VFR conforms get a second check (content_coverage): the conformed frames are matched by CONTENT to the
source frames (every frame when short, evenly spread windows when long) and every source frame that
must be shown — stated independently of the ffmpeg rule: its stored display interval is >= 1 slot, or
it lies within timestamp precision of a slot boundary (must_show_frames) — or that the rule shows, has
to appear; only frames sharing a slot with their successor (real jitter) may be dropped.
Results are cached in ``output/media/.conform.json`` ({src_hash, params, out_hash}).
"""
from __future__ import annotations

import dataclasses
import functools
import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .common import (DecisionLog, STAGE_VERSION, atomic_write_text, ffmpeg_bin, file_hash, fps_str, log,
                     null_dlog, params_hash)
from .model import StreamInfo
from .probe import (ae_issues, display_geometry, load_pts_int, probe, reader_sar, video_stream_ordinal)

CONFORM_JSON = ".conform.json"
SSIM_MIN = 0.98
MIN_SAMPLES = 50
SAMPLE_TARGET = 64
COMPARE_MAX_SIDE = 480
PRORES_MAX_S = 600.0                 # 'auto': ProRes LT up to 10 min, H.264 beyond
AUDIO_RATE = 48000
FPS_MODE_MIN_VERSION = (5, 1)        # '-fps_mode' appeared in ffmpeg 5.1; older builds need '-vsync 0'
# rule-independent VFR content check (verify_transcode, mode 'fps')
COVER_MAX_SIDE = 96                  # frames compared at <= 96 px (content identity, not quality)
COVER_FULL_MAX = 6000                # conforms up to this many frames are checked completely ...
COVER_WINDOW = 240                   # ... longer ones in evenly spread windows of this many output slots
COVER_OVERLAP = 8                    # adjacent full-coverage windows overlap (no unchecked seam frames)


@dataclass
class ConformResult:
    """Where the AE-imported (and analysed) copy of one input lives and how it was made."""
    path: str                 # absolute path of the file AE imports and every later stage analyses
    conformed: bool           # True = transcoded (verification applies)
    reason: str
    verification: dict = field(default_factory=dict)
    source_path: str = ""     # the original input (never modified)
    file_rel: str = ""        # path relative to the output dir ('media/raw_ae.mov'); '' = absolute reference
    file_abs: str = ""        # absolute path (== path)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ConformResult":
        names = {f.name for f in dataclasses.fields(ConformResult)}
        return ConformResult(**{k: v for k, v in d.items() if k in names})


# ----------------------------------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------------------------------

def _even(x: float) -> int:
    return max(2, int(2 * round(x / 2.0)))


def output_geometry(info: StreamInfo) -> dict:
    """Display-oriented, square-pixel, even-sized output geometry of a conform.

    Returns {w, h, crop: (cw, ch) | None applied to the rotated frame, scale: (tw, th) | None}.
    The SAR axis is stretched (never shrunk) to an even size; an odd size on an unscaled axis is
    cropped by one pixel at the right/bottom (keeps the CORNER geometry of every other pixel)."""
    rot = int(info.rotation) % 360
    w, h = (info.height, info.width) if rot in (90, 270) else (info.width, info.height)
    _, _, s = display_geometry(info.width, info.height, rot, info.sar)
    scale_x, scale_y = s > 1, (0 < s < 1)
    cw = w if (scale_x or w % 2 == 0) else w - 1
    ch = h if (scale_y or h % 2 == 0) else h - 1
    tw = _even(cw * float(s)) if scale_x else cw
    th = _even(ch / float(s)) if scale_y else ch
    return {"w": int(tw), "h": int(th), "crop": (int(cw), int(ch)) if (cw, ch) != (w, h) else None,
            "scale": (int(tw), int(th)) if (scale_x or scale_y) else None, "rotated": (int(w), int(h)),
            "scale_x": bool(scale_x), "scale_y": bool(scale_y)}


def vfr_pts_shift(tb: Fraction, fps: Fraction) -> Fraction:
    """Seconds every VFR source PTS is moved back before the ``fps`` filter: min(1 tick, 1/(4·fps)).

    Stored PTS are quantised to the stream time base (1 ms in MKV/WebM, 1/600 s in phone MOVs,
    1/15360 s for ffmpeg MP4s at 29.97, ...); a frame rounded LATE by less than a tick would otherwise
    land one output slot late and collide with its successor (``round=up``). One full tick also covers
    truncating/ceiling muxers and a rounded first PTS; the 1/4-frame cap keeps a coarse time base from
    moving any frame by more than a quarter of a slot. Exact time bases (1/90000 at 30 fps) move by
    one tick = 11 µs, i.e. only frames that lie within 11 µs after a slot boundary change slot."""
    tb, fps = Fraction(tb), Fraction(fps)
    if tb <= 0 or fps <= 0:
        return Fraction(0)
    return min(tb, 1 / (4 * fps))


def _frac_gcd(a: Fraction, b: Fraction) -> Fraction:
    """Largest T such that a/T and b/T are integers (a, b > 0)."""
    return Fraction(math.gcd(a.numerator * b.denominator, b.numerator * a.denominator),
                    a.denominator * b.denominator)


def _median_delta(rel: np.ndarray) -> Fraction:
    """Median of the integer PTS deltas (x.5 exact), in time-base ticks."""
    return Fraction(int(np.median(np.diff(rel)) * 2), 2)


def _vfr_expected_frames(pts: np.ndarray, tb: Fraction, fps: Fraction, shift: Fraction | None = None) -> int:
    """#{k : k/fps < last_pts - shift + median_frame_duration} with PTS relative to the first frame
    (exact). ``shift`` defaults to vfr_pts_shift(tb, fps) (the conform's PTS shift)."""
    rel = np.asarray(pts, dtype=np.int64) - int(pts[0])
    if len(rel) < 2:
        return 1
    shift = vfr_pts_shift(tb, fps) if shift is None else Fraction(shift)
    med = _median_delta(rel)
    end = ((Fraction(int(rel[-1])) + med) * tb - shift) * fps     # in output frames
    return int(max(1, math.ceil(end)))


def source_index_for_output(k: np.ndarray, mode: str, pts: np.ndarray, tb: Fraction, fps: Fraction,
                            shift: Fraction | None = None) -> np.ndarray:
    """Decoded-order index of the source frame shown at conformed frame k.

    restamp: k itself. fps (VFR): max{i : (pts_i - pts_0)·tb - shift <= k/fps} with
    shift = vfr_pts_shift(tb, fps) unless given (the conform's quantisation shift), evaluated EXACTLY
    with Python ints so exact-tie frames are never misplaced."""
    import bisect
    ks = [int(x) for x in np.asarray(k).ravel()]
    if mode == "restamp":
        return np.minimum(np.asarray(ks, dtype=np.int64), len(pts) - 1)
    tb, fps = Fraction(tb), Fraction(fps)
    shift = vfr_pts_shift(tb, fps) if shift is None else Fraction(shift)
    # (rel·a/b - c/d) <= k·f/e  <=>  rel·a·d·e - c·b·e <= k·f·b·d   (tb = a/b, shift = c/d, fps = e/f)
    a, b = tb.numerator, tb.denominator
    c, d = shift.numerator, shift.denominator
    e, f = fps.numerator, fps.denominator
    p0 = int(pts[0])
    lhs = [(int(p) - p0) * a * d * e - c * b * e for p in pts]     # increasing
    scale = f * b * d
    return np.asarray([max(0, bisect.bisect_right(lhs, x * scale) - 1) for x in ks], dtype=np.int64)


def interval_slots(pts: np.ndarray, tb: Fraction, fps: Fraction) -> list[Fraction]:
    """Stored display interval of every source frame in output slots (exact): (pts_{i+1} - pts_i)·tb·fps;
    the last frame gets the median frame duration (as in the expected frame count)."""
    rel = np.asarray(pts, dtype=np.int64) - int(pts[0])
    if len(rel) < 2:
        return [Fraction(1)]
    q = Fraction(tb) * Fraction(fps)
    out = [Fraction(int(x)) * q for x in np.diff(rel)]
    out.append(_median_delta(rel) * q)
    return out


def plan_transcode(info: StreamInfo, role: str, cfg) -> dict:
    """Everything that determines a transcode (also the cache 'params')."""
    fps = Fraction(info.fps)
    geo = output_geometry(info)
    codec = "h264_ref" if role == "competitor" else str(getattr(cfg, "conform_codec", "auto") or "auto")
    if codec == "auto":
        codec = "prores_lt" if info.duration <= PRORES_MAX_S else "h264"
    if codec not in ("prores_lt", "prores", "prores_ks", "h264", "h264_ref"):
        raise ValueError(f"unknown conform_codec {codec!r} (auto|prores_lt|prores|prores_ks|h264)")
    pts, tb, _origin = load_pts_int(info)
    extra: dict[str, Any] = {}
    if info.vfr:
        mode = "fps"
        shift = vfr_pts_shift(tb, fps)
        tb_f = _frac_gcd(Fraction(tb), shift) if shift > 0 else Fraction(tb)   # PTS and shift exact in tb_f
        shift_ticks = int(shift / tb_f)
        expected = _vfr_expected_frames(pts, tb, fps, shift)
        rel = np.asarray(pts, dtype=np.int64) - int(pts[0])
        med_s = float(_median_delta(rel) * tb) if len(rel) > 1 else float(1 / fps)
        # clone the last frame BEFORE the fps filter so the final slot(s) always see it (a container can
        # give the last frame a 1-tick duration: the stream would end before the last slot). tpad spaces
        # the clones by 1/link-frame-rate; >= 0.5 s keeps >= 1 clone for any link rate >= 2 fps.
        pad_s = max(0.5, 4.0 * med_s)
        timing = [f"settb={tb_f.numerator}/{tb_f.denominator}",
                  f"setpts=max(PTS-STARTPTS-{shift_ticks}\\,0)",
                  f"tpad=stop_mode=clone:stop_duration={pad_s:.6f}",
                  f"fps=fps={fps.numerator}/{fps.denominator}:round=up", f"trim=end_frame={expected}"]
        extra = {"pts_shift": fps_str(shift), "filter_tb": fps_str(tb_f)}
    else:
        mode = "restamp"
        expected = int(info.nb_frames)
        timing = [f"settb={fps.denominator}/{fps.numerator}", "setpts=N",
                  f"fps=fps={fps.numerator}/{fps.denominator}", f"trim=end_frame={expected}"]
    vf = list(timing)
    if geo["crop"]:
        vf.append(f"crop={geo['crop'][0]}:{geo['crop'][1]}:0:0:exact=1")
    if geo["scale"]:
        vf.append(f"scale={geo['scale'][0]}:{geo['scale'][1]}:flags=bicubic")
    vf.append("setsar=1")
    if any(i.startswith("interlaced") for i in info.ae_issues):
        vf.append("setfield=prog")
    prores = codec.startswith("prores")
    vf.append("format=yuv422p10le" if prores else "format=yuv420p")

    af = None
    if info.has_audio:
        af = [f"aresample={AUDIO_RATE}", "asetpts=PTS-STARTPTS"]
        off = int(round(float(info.av_offset) * AUDIO_RATE))
        if off > 0:
            af.append(f"adelay=delays={off}S:all=1")
        elif off < 0:
            af += [f"atrim=start_sample={-off}", "asetpts=PTS-STARTPTS"]

    g = max(1, int(round(float(fps) * (1 if codec == "h264_ref" else 2))))
    if codec == "prores_lt":
        vargs = ["-c:v", "prores_aw", "-profile:v", "1", "-vendor", "apl0"]
    elif codec == "prores":
        vargs = ["-c:v", "prores_aw", "-profile:v", "2", "-vendor", "apl0"]
    elif codec == "prores_ks":
        vargs = ["-c:v", "prores_ks", "-profile:v", "1", "-vendor", "apl0"]
    elif codec == "h264":
        vargs = ["-c:v", "libx264", "-preset", str(getattr(cfg, "conform_h264_preset", "veryfast")),
                 "-crf", str(getattr(cfg, "conform_h264_crf", 12)), "-g", str(g), "-bf", "2",
                 "-profile:v", "high"]
    else:  # h264_ref (competitor)
        vargs = ["-c:v", "libx264", "-preset", str(getattr(cfg, "competitor_h264_preset", "medium")),
                 "-crf", str(getattr(cfg, "competitor_h264_crf", 12)), "-g", str(g), "-bf", "2",
                 "-profile:v", "high"]
    if af is None:
        aargs = []
    elif prores:
        aargs = ["-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE)]
    else:
        aargs = ["-c:a", "aac", "-b:a", "320k", "-ar", str(AUDIO_RATE)]
    ext = ".mov" if prores else ".mp4"
    name = "competitor_ref.mp4" if role == "competitor" else f"raw_ae{ext}"
    return {"version": STAGE_VERSION.get("conform", 1), "mode": mode, "codec": codec, "name": name,
            "fps": fps_str(fps), "expected_frames": int(expected), "width": geo["w"], "height": geo["h"],
            "vf": ",".join(vf), "af": ",".join(af) if af else None, "vargs": vargs, "aargs": aargs,
            "vindex": video_stream_ordinal(info), **extra}


def _parse_version(text: str) -> tuple[int, ...]:
    """(major, minor[, patch]) from ``ffmpeg -version`` output; () for git builds ('N-112345-g...')."""
    m = re.search(r"version\s+n?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")[:3]) if m else ()


@functools.lru_cache(maxsize=8)
def _detect_sync_args(binary: str) -> tuple[str, ...]:
    """Passthrough frame-sync flag the given ffmpeg accepts (cached per binary)."""
    try:
        out = subprocess.run([binary, "-hide_banner", "-version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    ver = _parse_version(out)
    if not ver:                  # git / distro build without a release number: ask the option parser
        try:
            helptext = subprocess.run([binary, "-hide_banner", "-h", "long"], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            helptext = ""
        if helptext and "-fps_mode" not in helptext:
            ver = (0,)
    return sync_args(ver)


def sync_args(ffmpeg_version: tuple[int, ...] | None = None) -> tuple[str, ...]:
    """``-fps_mode passthrough`` (ffmpeg >= 5.1) or ``-vsync 0`` (older builds, e.g. Ubuntu 22.04's
    4.4 — prompt: "-vsync 0 on old builds"). ``ffmpeg_version`` None = detect the configured binary;
    () = unknown (treated as a current build)."""
    if ffmpeg_version is None:
        return _detect_sync_args(ffmpeg_bin())
    v = tuple(int(x) for x in ffmpeg_version)
    if v and v < FPS_MODE_MIN_VERSION:
        return ("-vsync", "0")
    return ("-fps_mode", "passthrough")


def ffmpeg_command(src: str, out: str, plan: dict, ffmpeg_version: tuple[int, ...] | None = None) -> list[str]:
    """The conform's ffmpeg command line. ``ffmpeg_version`` None = detect (see sync_args)."""
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", src, "-map", f"0:v:{plan['vindex']}"]
    if plan["af"]:
        cmd += ["-map", "0:a:0"]
    cmd += ["-filter:v", plan["vf"]]
    if plan["af"]:
        cmd += ["-filter:a", plan["af"]]
    cmd += [*sync_args(ffmpeg_version), *plan["vargs"], *plan["aargs"], "-map_metadata", "-1",
            "-map_chapters", "-1"]
    if plan["codec"] == "h264_ref":
        cmd += ["-movflags", "+faststart"]       # small reference file; a multi-GB RAW would be rewritten
    return cmd + [out]


# ----------------------------------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------------------------------

def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM of two 8-bit-range grayscale images (Gaussian window sigma 1.5, K1=0.01, K2=0.03)."""
    import cv2
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    saa = blur(a * a) - mu_a * mu_a
    sbb = blur(b * b) - mu_b * mu_b
    sab = blur(a * b) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * sab + c2)
    den = (mu_a * mu_a + mu_b * mu_b + c1) * (saa + sbb + c2)
    return float(np.mean(num / den))


def _compare_size(w: int, h: int) -> tuple[int, int]:
    f = min(1.0, COMPARE_MAX_SIDE / max(w, h))
    return max(8, int(round(w * f))), max(8, int(round(h * f)))


def decode_source_frames(info: StreamInfo, indices: list[int], geo: dict, size: tuple[int, int]) -> dict[int, np.ndarray]:
    """Gray display-oriented source frames by DECODED-ORDER index, located by their exact PTS (valid for
    VFR files, unlike VideoReader indices). Rotation/SAR as VideoReader; the conform crop is applied;
    resized to ``size`` (INTER_AREA)."""
    import cv2
    from .media import VideoReader

    pts, tb, _origin = load_pts_int(info)
    want = sorted(set(int(i) for i in indices if 0 <= int(i) < len(pts)))
    out: dict[int, np.ndarray] = {}
    if not want:
        return out
    gap = int(4 / float(tb)) if tb else 0
    clusters: list[list[int]] = [[want[0]]]
    for i in want[1:]:
        if int(pts[i]) - int(pts[clusters[-1][-1]]) > gap:
            clusters.append([i])
        else:
            clusters[-1].append(i)
    rd = VideoReader(info.path, fps=info.fps, stream_index=video_stream_ordinal(info), rotation=info.rotation,
                     sar=reader_sar(info))
    try:
        stream = rd.stream
        for cl in clusters:
            wanted = {int(pts[i]): i for i in cl}
            lo, hi = int(pts[cl[0]]), int(pts[cl[-1]])
            targets = [lo - max(1, int(0.5 / float(tb))), lo - int(10 / float(tb)), None]
            done = False
            for target in targets:
                if target is None:
                    rd.container.seek(0, stream=stream, backward=True, any_frame=False)
                else:
                    rd.container.seek(max(target, int(stream.start_time or 0)), stream=stream, backward=True,
                                      any_frame=False)
                first = True
                got: dict[int, np.ndarray] = {}
                for frame in rd.container.decode(stream):
                    p = frame.pts if frame.pts is not None else frame.dts
                    if p is None:
                        continue
                    p = int(p)
                    if first:
                        first = False
                        if p > lo and target is not None:
                            break               # seek overshot -> retry further back
                    if p in wanted:
                        img = rd._convert(frame, "gray", None, None)
                        if geo.get("crop"):
                            # the conform crops only an odd UNSCALED axis (after rotation, before scaling)
                            if not geo.get("scale_y"):
                                img = img[:geo["crop"][1]]
                            if not geo.get("scale_x"):
                                img = img[:, :geo["crop"][0]]
                        got[wanted[p]] = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                    if p >= hi:
                        break
                if len(got) == len(cl):
                    out.update(got)
                    done = True
                    break
            if not done:
                raise RuntimeError(f"{info.path}: could not decode source frames {cl[:5]}... by PTS")
    finally:
        rd.close()
    return out


def _plan_shift(plan: dict, tb: Fraction, fps: Fraction) -> Fraction:
    """The PTS shift a VFR plan applied (plans without the key: the current default)."""
    s = plan.get("pts_shift")
    return Fraction(str(s)) if s not in (None, "") else vfr_pts_shift(tb, fps)


def _cover_windows(n: int) -> list[tuple[int, int]]:
    """Output-slot windows [k0, k1) of the content check: the whole conform (overlapping chunks) when
    n <= COVER_FULL_MAX, else evenly spread windows of COVER_WINDOW slots incl. the first and last."""
    if n <= COVER_FULL_MAX:
        chunk = 600
        wins, k0 = [], 0
        while True:
            k1 = min(n, k0 + chunk)
            wins.append((k0, k1))
            if k1 >= n:
                return wins
            k0 = k1 - COVER_OVERLAP
    m = max(2, COVER_FULL_MAX // COVER_WINDOW)
    starts = np.unique(np.round(np.linspace(0, n - COVER_WINDOW, m)).astype(np.int64))
    return [(int(s), int(s) + COVER_WINDOW) for s in starts]


def must_show_frames(pts: np.ndarray, tb: Fraction, fps: Fraction, shift: Fraction | None = None,
                     n_out: int | None = None) -> dict[str, set]:
    """Source frames a correct VFR->CFR conform MUST show, from two physical requirements stated
    independently of the conform's ffmpeg rule (``source_index_for_output``):

      * 'long':     the stored display interval Δ = (pts_{j+1} - pts_j)·tb·fps >= 1 output slot (the last
                    frame: the median duration) — it contains a slot boundary however the rule rounds;
      * 'boundary': the frame lies within the timestamp precision u (= vfr_pts_shift: one tick, at most a
                    quarter slot) of a slot boundary m/fps and its successor lies more than u after that
                    boundary — its true time may be exactly m/fps, where 'the frame displayed at t_m' is
                    this frame (an ms-rounded 29.97 frame stored 0.4 ms late is such a frame: the
                    unshifted rule dropped ~1/3 of them).
    ``shift`` = u (default vfr_pts_shift); with ``n_out`` only frames whose slot is < n_out are required.
    Both sets are exact (Python ints / Fractions)."""
    tb, fps = Fraction(tb), Fraction(fps)
    u = (vfr_pts_shift(tb, fps) if shift is None else Fraction(shift)) * fps       # in slots
    rel = [int(p) - int(pts[0]) for p in pts]
    n = len(rel)
    lim = n_out
    inter = interval_slots(np.asarray(rel, dtype=np.int64), tb, fps)
    long_, boundary = set(), set()
    for j in range(n):
        x = rel[j] * tb * fps                        # position in slots (exact)
        if inter[j] >= 1 and (lim is None or math.ceil(max(x - u, 0)) < lim):
            long_.add(j)
        m = round(x)                                 # nearest boundary
        if (abs(x - m) <= u and (lim is None or m < lim)
                and (j == n - 1 or rel[j + 1] * tb * fps > m + u)):
            boundary.add(j)
    return {"long": long_, "boundary": boundary}


def content_coverage(src: StreamInfo, out: StreamInfo, plan: dict) -> dict:
    """Content check of a VFR conform over (nearly) every frame: which SOURCE frames does it really show?

    Every conformed frame k (all of them up to COVER_FULL_MAX, else evenly spread windows) is matched by
    content (gray, <= COVER_MAX_SIDE px, RMSE) against the source frames around the rule's prediction;
    a source frame is 'shown' when some conformed frame is as close to it as to its best match (within
    codec noise: rmse <= 1.5·best + 1 level, so visually identical neighbours count as shown). Every
    checked source frame that must be shown and is not fails the conform:
      * 'unexplained': frames must_show_frames() requires (interval >= 1 slot, or within timestamp
        precision of a slot boundary) — requirements stated independently of the ffmpeg rule, so a wrong
        rule (e.g. one ignoring ms-rounded timestamps) cannot hide its own damage;
      * 'rule_violations': other frames the 'frame displayed at t_k' rule shows (the SSIM samples check
        the same on 64 frames; this covers every checked frame).
    Frames the rule drops (two source frames inside one slot: real jitter) are reported as
    'jitter_drops'. Returns {checked, windows, missing, unexplained, rule_violations, informative, ...}."""
    import cv2
    from .media import VideoReader

    pts, tb, _ = load_pts_int(src)
    fps = Fraction(plan["fps"])
    shift = _plan_shift(plan, tb, fps)
    n = int(min(out.nb_frames, plan["expected_frames"]))
    n_src = len(pts)
    rec: dict[str, Any] = {"frames_source": int(n_src), "frames_output": n, "problems": []}
    if n < 1 or n_src < 2:
        rec.update({"checked": 0, "note": "too short for a content check"})
        return rec
    idx_all = source_index_for_output(np.arange(n), "fps", pts, tb, fps, shift)
    rule_set = set(int(x) for x in np.unique(idx_all).tolist())
    rec["rule_shown"] = len(rule_set)
    rec["rule_dropped"] = int(n_src - len(rule_set))
    req = must_show_frames(pts, tb, fps, vfr_pts_shift(tb, fps), n_out=n)
    inter = interval_slots(pts, tb, fps)
    wins = _cover_windows(n)
    geo = output_geometry(src)
    f = min(1.0, COVER_MAX_SIDE / max(plan["width"], plan["height"]))
    size = (max(8, int(round(plan["width"] * f))), max(8, int(round(plan["height"] * f))))
    covered = np.zeros(n_src, dtype=bool)
    evaluable = np.zeros(n_src, dtype=bool)
    informative = np.zeros(n_src, dtype=bool)
    seen_out = 0
    with VideoReader(out.path, fps=out.fps) as rd:
        for k0, k1 in wins:
            lo = 0 if k0 == 0 else max(0, int(idx_all[k0]) - 3)
            hi = n_src - 1 if k1 >= n else min(n_src - 1, int(idx_all[k1 - 1]) + 3)
            srcf = decode_source_frames(src, list(range(lo, hi + 1)), geo, size)
            S = np.stack([srcf[j] for j in range(lo, hi + 1)]).astype(np.float32)
            for j in range(lo, hi + 1):          # does the source frame differ from its neighbours at all?
                nb = [float(np.sqrt(np.mean((S[j - lo] - S[x - lo]) ** 2))) for x in (j - 1, j + 1) if lo <= x <= hi]
                if nb and min(nb) > 2.0:
                    informative[j] = True
            for k, img in rd.frames(k0, k1, "gray"):
                c = cv2.resize(img, size, interpolation=cv2.INTER_AREA).astype(np.float32)
                i = int(idx_all[k])
                cand = np.arange(max(lo, i - 3), min(hi, i + 3) + 1)
                rmse = np.array([float(np.sqrt(np.mean((S[j - lo] - c) ** 2))) for j in cand])
                covered[cand[rmse <= 1.5 * float(rmse.min()) + 1.0]] = True
                seen_out += 1
            e_lo = 0 if k0 == 0 else int(idx_all[k0]) + 1
            e_hi = n_src - 1 if k1 >= n else int(idx_all[k1 - 1]) - 1
            if e_hi >= e_lo:
                evaluable[e_lo:e_hi + 1] = True
    missing = np.flatnonzero(evaluable & ~covered).tolist()
    required = req["long"] | req["boundary"]
    unexplained = [j for j in missing if j in required]
    rule_violations = [j for j in missing if j in rule_set and j not in required]
    jitter = [j for j in missing if j not in rule_set and j not in required]
    n_eval = int(evaluable.sum())
    needed = sum(k1 - k0 for k0, k1 in wins)
    rec.update({"checked": n_eval, "output_frames_matched": int(seen_out), "windows": len(wins),
                "full": n <= COVER_FULL_MAX, "compare_size": list(size),
                "informative": int((informative & evaluable).sum()), "missing": len(missing),
                "required": int(sum(1 for j in required if evaluable[j])),
                "unexplained": unexplained[:50], "n_unexplained": len(unexplained),
                "rule_violations": rule_violations[:50], "n_rule_violations": len(rule_violations),
                "jitter_drops": len(jitter)})
    if seen_out < needed:
        rec["problems"].append(f"content check decoded only {seen_out} of the {needed} conformed frames it needed")
    if unexplained:
        j0 = unexplained[0]
        why = "display interval >= 1 slot" if j0 in req["long"] else "within timestamp precision of a slot boundary"
        rec["problems"].append(
            f"{len(unexplained)} source frames that must be shown are missing from the conform (e.g. "
            f"{unexplained[:8]}; frame {j0}: {why}, interval {float(inter[j0]):.3f} slots) — the conform dropped "
            "real frames")
    if rule_violations:
        rec["problems"].append(
            f"{len(rule_violations)} source frames the 'frame displayed at t_k' rule shows are missing from the "
            f"conform (e.g. {rule_violations[:8]})")
    if n_eval and not rec["informative"]:
        rec["note"] = "no checked source frame differs from its neighbours: content check uninformative (static)"
    return rec


def verify_transcode(src: StreamInfo, out: StreamInfo, plan: dict) -> dict:
    """Frame count + AE safety + >= 50 PTS-sampled SSIM checks (DESIGN §5 conform verification), plus
    the rule-independent content check for VFR conforms (content_coverage)."""
    import cv2
    from .media import VideoReader

    t0 = time.perf_counter()
    res: dict[str, Any] = {"method": plan["mode"], "frames_expected": plan["expected_frames"],
                           "frames_actual": int(out.nb_frames), "frames_source": int(src.nb_frames),
                           "fps_expected": plan["fps"],
                           "fps_actual": fps_str(out.fps), "size": [out.display_width, out.display_height],
                           "ae_issues_after": list(out.ae_issues)}
    problems: list[str] = []
    if out.nb_frames != plan["expected_frames"]:
        problems.append(f"frame count {out.nb_frames} != expected {plan['expected_frames']}")
    if fps_str(out.fps) != plan["fps"]:
        problems.append(f"fps {fps_str(out.fps)} != {plan['fps']}")
    if (out.display_width, out.display_height) != (plan["width"], plan["height"]):
        problems.append(f"size {out.display_width}x{out.display_height} != {plan['width']}x{plan['height']}")
    if out.ae_issues:
        problems.append("conformed file is still not AE-safe: " + "; ".join(out.ae_issues))
    if out.has_audio != src.has_audio:
        problems.append(f"audio presence changed ({src.has_audio} -> {out.has_audio})")

    n = int(min(out.nb_frames, plan["expected_frames"]))
    pts, tb, _ = load_pts_int(src)
    fps = Fraction(plan["fps"])
    if n > 0:
        m = min(n, SAMPLE_TARGET)
        ks = np.unique(np.round(np.linspace(0, n - 1, m)).astype(np.int64))
        src_idx = source_index_for_output(ks, plan["mode"], pts, tb, fps,
                                          _plan_shift(plan, tb, fps) if plan["mode"] == "fps" else None)
        need = sorted({int(j) for i in src_idx for j in (i - 1, i, i + 1) if 0 <= j < len(pts)})
        size = _compare_size(plan["width"], plan["height"])
        geo = output_geometry(src)
        orig = decode_source_frames(src, need, geo, size)
        with VideoReader(out.path, fps=out.fps) as rd:
            conf = {k: cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                    for k, img in rd.get_many([int(k) for k in ks], fmt="gray").items()}
        samples, fails = [], []
        n_inf = 0
        for k, i in zip(ks.tolist(), src_idx.tolist()):
            c = conf[k]
            s0 = ssim(c, orig[i])
            nb = {}
            informative = False
            ok = s0 > SSIM_MIN
            for j in (i - 1, i + 1):
                if j not in orig:
                    continue
                snb = ssim(c, orig[j])
                d_nb = 1.0 - ssim(orig[i], orig[j])        # how different the neighbour really is
                inf = d_nb > 3.0 * max(1.0 - s0, 1e-4)      # clearly above the codec noise
                nb[j] = round(snb, 5)
                if inf:
                    informative = True
                    ok = ok and s0 > snb
            n_inf += int(informative)
            rec = {"k": int(k), "src": int(i), "ssim": round(s0, 5), "neighbours": nb, "informative": informative}
            samples.append(rec)
            if not ok:
                fails.append(rec)
        ss = np.array([r["ssim"] for r in samples])
        margins = [r["ssim"] - max(r["neighbours"].values()) for r in samples if r["informative"] and r["neighbours"]]
        res.update({"samples": len(samples), "informative": n_inf, "failed": fails[:20], "n_failed": len(fails),
                    "min_ssim": float(ss.min()), "median_ssim": float(np.median(ss)),
                    "min_margin": float(min(margins)) if margins else None,
                    "compare_size": list(size)})
        if len(samples) < min(MIN_SAMPLES, n):
            problems.append(f"only {len(samples)} samples (< {min(MIN_SAMPLES, n)})")
        if fails:
            problems.append(f"{len(fails)} sampled frames fail SSIM > {SSIM_MIN} / better-than-neighbours "
                            f"(e.g. k={fails[0]['k']} src={fails[0]['src']} ssim={fails[0]['ssim']} nb={fails[0]['neighbours']})")
        if n_inf == 0 and n > 2:
            res["note"] = "no sampled frame differs from its neighbours: offset check uninformative (static video)"
    if plan["mode"] == "fps" and n > 0:
        cov = content_coverage(src, out, plan)
        problems += cov.pop("problems")
        res["coverage"] = cov
    res["problems"] = problems
    res["ok"] = not problems
    res["seconds"] = round(time.perf_counter() - t0, 3)
    return res


# ----------------------------------------------------------------------------------------------
# .conform.json cache + file placement
# ----------------------------------------------------------------------------------------------

def _load_state(media: Path) -> dict:
    p = media / CONFORM_JSON
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log.warning("%s unreadable; ignoring the conform cache", p)
    return {}


def _save_state(media: Path, state: dict) -> None:
    atomic_write_text(media / CONFORM_JSON, json.dumps(state, indent=1, sort_keys=True))


def _link_or_copy(src: Path, dst: Path) -> str:
    """Place ``src`` at ``dst`` without ever writing into an existing inode (dst may be a hard link to an
    input file: it is unlinked, never truncated). Returns 'same' | 'hardlink' | 'copy'."""
    if dst.exists():
        try:
            if os.path.samefile(src, dst):
                return "same"
        except OSError:
            pass
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        return "copy"


def _media_name_for_raw(src: Path) -> str:
    """The RAW's copy is always raw.<ext>, whatever the input file is called (an input named competitor.mp4 would
    otherwise put "competitor.mp4" into the Premiere project as the RAW)."""
    return "raw" + (src.suffix.lower() or ".mp4")


# ----------------------------------------------------------------------------------------------
# conform()
# ----------------------------------------------------------------------------------------------

def conform(info: StreamInfo, role: str, cfg, dlog: DecisionLog | None = None) -> ConformResult:
    """Make the AE-imported copy of one input (DESIGN §5 conform.py). Never modifies the input.

    role 'raw' | 'competitor'. Returns a ConformResult whose ``path`` every later stage must analyse.
    Raises RuntimeError when a transcode fails its verification (nothing is cached then)."""
    dlog = dlog or null_dlog()
    media = Path(cfg.out_dir) / "media"
    media.mkdir(parents=True, exist_ok=True)
    src = Path(info.path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"conform: input not found: {src}")
    src_hash = info.file_hash or file_hash(src)
    issues = ae_issues(info)
    force = bool(getattr(cfg, "force_conform", False))
    out_root = Path(cfg.out_dir).resolve()
    state = _load_state(media)

    if role == "competitor":
        ref_ok = (not issues and info.container in ("mp4", "m4v") and info.vcodec == "h264"
                  and (not info.has_audio or info.acodec == "aac"))
        need = force or not ref_ok
        why_not_copy = issues or (["forced (--force-conform)"] if force else
                                  [] if ref_ok else [f"reference must be H.264/AAC .mp4 (is {info.container}/"
                                                     f"{info.vcodec}/{info.acodec or 'no audio'})"])
    else:
        need = force or bool(issues)
        why_not_copy = issues or (["forced (--force-conform)"] if force else [])

    # ------------------------------------------------------------------ untouched placement
    if not need:
        if role != "competitor" and info.file_size > int(getattr(cfg, "large_file_bytes", 2 * 1024 ** 3)):
            params = {"mode": "reference", "version": STAGE_VERSION.get("conform", 1)}
            res = ConformResult(str(src), False, f"AE-safe; {info.file_size / 1e9:.2f} GB > large_file_bytes: "
                                "referenced by absolute path (the JSX offers a relink dialog)",
                                {"method": "reference", "ok": True}, str(src), "", str(src))
            state[role] = {"src_hash": src_hash, "params": params, "out_hash": src_hash, "out_file": "",
                           "result": res.to_dict()}
            _save_state(media, state)
            dlog.record("conform", "reference", role=role, evidence={"file": str(src), "bytes": info.file_size})
            log.info("conform %s: AE-safe, referenced by absolute path (%s)", role, src)
            return res
        name = "competitor_ref.mp4" if role == "competitor" else _media_name_for_raw(src)
        dst = media / name
        how = _link_or_copy(src, dst)
        params = {"mode": "copy", "name": name, "version": STAGE_VERSION.get("conform", 1)}
        res = ConformResult(str(dst.resolve()), False, f"AE-safe; {how} into media/ unchanged",
                            {"method": "copy", "ok": True, "identical": True, "frames": int(info.nb_frames)},
                            str(src), os.path.relpath(dst.resolve(), out_root).replace(os.sep, "/"), str(dst.resolve()))
        state[role] = {"src_hash": src_hash, "params": params, "out_hash": src_hash, "out_file": name,
                       "result": res.to_dict()}
        _save_state(media, state)
        dlog.record("conform", "copy", role=role, evidence={"file": str(src), "method": how, "ae_issues": []})
        log.info("conform %s: AE-safe -> %s (%s)", role, dst, how)
        return res

    # ------------------------------------------------------------------ transcode
    plan = plan_transcode(info, role, cfg)
    dst = media / plan["name"]
    prev = state.get(role)
    if (prev and prev.get("src_hash") == src_hash and prev.get("params") == plan and dst.exists()
            and file_hash(dst) == prev.get("out_hash")):
        res = ConformResult.from_dict(prev["result"])
        res.path = res.file_abs = str(dst.resolve())
        res.source_path = str(src)
        res.file_rel = os.path.relpath(dst.resolve(), out_root).replace(os.sep, "/")
        dlog.record("conform", "cache_hit", role=role, evidence={"file": str(dst), "params": params_hash(plan)})
        log.info("conform %s: cached %s", role, dst)
        return res

    tmp = dst.with_name(".tmp_" + dst.name)
    cmd = ffmpeg_command(str(src), str(tmp), plan)
    t0 = time.perf_counter()
    log.info("conform %s: %s -> %s (%s, %s, %d frames)", role, src.name, dst.name, plan["codec"], plan["mode"],
             plan["expected_frames"])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"conform {role}: ffmpeg failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr[-3000:]}")
    enc_s = time.perf_counter() - t0
    os.replace(tmp, dst)
    out_info = probe(dst, role, cfg.work_dir, decode=True)
    ver = verify_transcode(info, out_info, plan)
    ver["encode_seconds"] = round(enc_s, 3)
    ver["encode_fps"] = round(plan["expected_frames"] / enc_s, 2) if enc_s > 0 else None
    dlog.record("conform", "transcode", role=role,
                evidence={"source": str(src), "out": str(dst), "issues": why_not_copy, "plan": plan,
                          "verification": ver})
    if not ver["ok"]:
        raise RuntimeError(f"conform {role}: verification of {dst} failed: " + "; ".join(ver["problems"]))
    codec_desc = {"prores_lt": "ProRes 422 LT (prores_aw) + PCM 48 kHz", "prores": "ProRes 422 (prores_aw) + PCM 48 kHz",
                  "prores_ks": "ProRes 422 LT (prores_ks) + PCM 48 kHz", "h264": "H.264 CRF 12 + AAC 48 kHz",
                  "h264_ref": "H.264 + AAC 48 kHz"}[plan["codec"]]
    timing_desc = (f"VFR->CFR fps round=up (PTS shifted back by {plan.get('pts_shift')} s for timestamp "
                   "quantisation)" if plan["mode"] == "fps" else "CFR re-stamped by frame index")
    reason = ("not AE-safe: " + "; ".join(why_not_copy) + f" -> transcoded to {codec_desc}, {timing_desc} at "
              f"{plan['fps']} fps, {plan['width']}x{plan['height']}, start 0")
    res = ConformResult(str(dst.resolve()), True, reason, ver, str(src),
                        os.path.relpath(dst.resolve(), out_root).replace(os.sep, "/"), str(dst.resolve()))
    state = _load_state(media)
    state[role] = {"src_hash": src_hash, "params": plan, "out_hash": out_info.file_hash, "out_file": plan["name"],
                   "result": res.to_dict()}
    _save_state(media, state)
    log.info("conform %s: %s verified (%d samples, min SSIM %.4f) in %.1fs (%.1f fps encode)", role, dst.name,
             ver.get("samples", 0), ver.get("min_ssim", float("nan")), time.perf_counter() - t0,
             ver["encode_fps"] or 0)
    return res
