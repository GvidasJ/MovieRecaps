"""Stage 2a — probe an input file (DESIGN.md §5 probe.py).

``probe()`` combines ffprobe metadata with ONE full decode pass (``media.decode_pts``) so that frame
counts and timestamps are measured, never estimated:

* exact decoded frame count and the PTS of every frame (saved as ``.npy``: seconds relative to the
  video stream start, the ``VideoReader``/``Proxy`` convention; integer PTS + time base alongside),
* nominal frame rate (exact rational, snapped to the NEAREST common broadcast rate within 1 %),
* CFR / VFR (PTS jitter against the nominal grid), rotation (display matrix), SAR/DAR,
  per-stream start times, first decoded PTS, A/V start offset, MP4/MOV edit lists
  (own ``elst`` parser; a single edit equal to the codec delay is benign),
* audio stream parameters, file hash, and ``ae_issues`` — the reasons the file is not AE-safe.

Results are cached in ``WORK_DIR/cache/probe/`` keyed by the file content hash.
"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from .common import Cache, _COMMON_RATES, atomic_write_text, ffprobe_bin, file_hash, fps_str, log, stage_key
from .model import StreamInfo

# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

AE_SAFE_VCODECS = ("h264", "prores")
AE_SAFE_CONTAINERS = ("mp4", "mov", "m4v")
H264_AE_PIX_FMTS = ("yuv420p", "yuvj420p")
VFR_JITTER_FRAMES = 0.1            # PTS deviation (frames) above which a stream is VFR
START_TOL_S = 1e-3                 # start times / offsets below this are 0
AUDIO_PRIMING_MAX_S = 0.1          # a single audio edit skipping <= this is codec priming (benign)
NOMINAL_FPS_TOL = 0.01             # relative tolerance for nominal-rate snapping (VFR phone files)

# Issue codes (``ae_issues`` entries are "<code>: <detail>"). Codes that cannot be derived from the
# StreamInfo fields alone are computed during probe() and carried over by ae_issues().
PROBE_ONLY_CODES = ("edit_list", "interlaced", "stream_layout", "rotation_matrix", "pts_order")


# ----------------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------------

def _frac(value: Any) -> Fraction:
    """Parse ffprobe rationals ('30000/1001', '0/0', '16:9', None) -> Fraction (0 if invalid)."""
    if value is None:
        return Fraction(0)
    s = str(value).strip().replace(":", "/")
    if not s or s in ("N/A", "0/0"):
        return Fraction(0)
    try:
        if "/" in s:
            n, d = s.split("/", 1)
            n_i, d_i = int(n), int(d)
            return Fraction(n_i, d_i) if d_i else Fraction(0)
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def nearest_common_rate(fr: Fraction | float, tol: float = NOMINAL_FPS_TOL) -> Fraction | None:
    """The common broadcast rate NEAREST to ``fr`` (relative distance <= tol), or None.

    (``common.snap_rate`` returns the FIRST rate within tol, which picks 30000/1001 for a 29.99 fps
    measurement at tol=1 %; nominal-rate detection needs the nearest one.)"""
    f = float(fr)
    if not (f > 0 and math.isfinite(f)):
        return None
    best, best_d = None, None
    for r in _COMMON_RATES:
        d = abs(f - float(r)) / float(r)
        if d <= tol and (best_d is None or d < best_d - 1e-12):
            best, best_d = r, d
    return best


def _run_json(cmd: list[str]) -> dict:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr[-3000:]}")
    try:
        return json.loads(res.stdout or "{}")
    except json.JSONDecodeError as e:  # pragma: no cover - ffprobe always emits JSON with -of json
        raise RuntimeError(f"ffprobe returned invalid JSON for {cmd[-1]}: {e}") from e


def ffprobe_json(path: str | os.PathLike) -> dict:
    """ffprobe -show_streams -show_format (incl. stream side data) as a dict."""
    return _run_json([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])


# ----------------------------------------------------------------------------------------------
# MP4 / MOV edit lists (own box parser: ffprobe does not expose elst)
# ----------------------------------------------------------------------------------------------

@dataclass
class TrackEdits:
    track_id: int
    handler: str                 # 'vide' | 'soun' | ...
    media_timescale: int
    movie_timescale: int
    media_duration: int
    entries: list[tuple[int, int, float]]   # (segment_duration [movie ts], media_time [media ts], rate)

    def to_dict(self) -> dict:
        return {"track_id": self.track_id, "handler": self.handler, "media_timescale": self.media_timescale,
                "movie_timescale": self.movie_timescale, "media_duration": self.media_duration,
                "entries": [list(e) for e in self.entries]}


def _iter_boxes(f, start: int, end: int) -> Iterator[tuple[str, int, int]]:
    """Yield (type, payload_start, box_end) for the boxes in [start, end)."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr)
        hlen = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            hlen = 16
        elif size == 0:
            size = end - pos
        if size < hlen or pos + size > end + 8:   # corrupt / truncated
            return
        yield typ.decode("latin-1"), pos + hlen, min(pos + size, end)
        pos += size


def _find(f, start: int, end: int, typ: str) -> tuple[int, int] | None:
    for t, s, e in _iter_boxes(f, start, end):
        if t == typ:
            return s, e
    return None


def read_edit_lists(path: str | os.PathLike) -> list[TrackEdits]:
    """Parse moov/trak/{tkhd, edts/elst, mdia/{mdhd, hdlr}} of an ISO-BMFF (MP4/MOV) file.

    Returns one entry per track (tracks without an ``edts`` box have ``entries == []``).
    Never reads sample tables or media data; returns [] for non-ISO files."""
    out: list[TrackEdits] = []
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        moov = _find(f, 0, size, "moov")
        if moov is None:
            return out
        movie_ts = 1000
        mvhd = _find(f, *moov, "mvhd")
        if mvhd:
            f.seek(mvhd[0])
            ver = f.read(1)[0]
            f.seek(mvhd[0] + (20 if ver == 1 else 12))
            movie_ts = struct.unpack(">I", f.read(4))[0] or 1000
        for t, s, e in _iter_boxes(f, *moov):
            if t != "trak":
                continue
            track_id, handler, media_ts, media_dur, entries = 0, "", 0, 0, []
            tkhd = _find(f, s, e, "tkhd")
            if tkhd:
                f.seek(tkhd[0])
                ver = f.read(1)[0]
                f.seek(tkhd[0] + (20 if ver == 1 else 12))
                track_id = struct.unpack(">I", f.read(4))[0]
            mdia = _find(f, s, e, "mdia")
            if mdia:
                mdhd = _find(f, *mdia, "mdhd")
                if mdhd:
                    f.seek(mdhd[0])
                    ver = f.read(1)[0]
                    if ver == 1:
                        f.seek(mdhd[0] + 20)
                        media_ts, media_dur = struct.unpack(">IQ", f.read(12))
                    else:
                        f.seek(mdhd[0] + 12)
                        media_ts, media_dur = struct.unpack(">II", f.read(8))
                hdlr = _find(f, *mdia, "hdlr")
                if hdlr:
                    f.seek(hdlr[0] + 8)
                    handler = f.read(4).decode("latin-1")
            edts = _find(f, s, e, "edts")
            if edts:
                elst = _find(f, *edts, "elst")
                if elst:
                    f.seek(elst[0])
                    ver = f.read(1)[0]
                    f.read(3)
                    (count,) = struct.unpack(">I", f.read(4))
                    count = min(count, 4096)
                    for _ in range(count):
                        if ver == 1:
                            b = f.read(20)
                            if len(b) < 20:
                                break
                            dur, mt, ri, rf = struct.unpack(">QqhH", b)
                        else:
                            b = f.read(12)
                            if len(b) < 12:
                                break
                            dur, mt, ri, rf = struct.unpack(">IihH", b)
                        entries.append((int(dur), int(mt), ri + rf / 65536.0))
            out.append(TrackEdits(track_id, handler, int(media_ts), int(movie_ts), int(media_dur), entries))
    return out


def _codec_delay_pts(path: str, vindex: int) -> int | None:
    """Composition delay of the video track = first PTS with the edit list IGNORED (mov demuxer)."""
    try:
        d = _run_json([ffprobe_bin(), "-v", "error", "-ignore_editlist", "1", "-select_streams", f"v:{vindex}",
                       "-show_entries", "stream=start_pts", "-of", "json", path])
        st = d.get("streams", [])
        if st and st[0].get("start_pts") not in (None, "N/A"):
            return int(st[0]["start_pts"])
    except Exception as e:  # pragma: no cover - informative only
        log.debug("codec delay probe failed for %s: %s", path, e)
    return None


def _edit_list_issues(path: str, vstream: dict, astream: dict | None, vindex: int, decoded_n: int | None,
                      tracks: list[TrackEdits]) -> tuple[list[str], list[str]]:
    """Classify MP4/MOV edit lists -> (issues, benign notes). DESIGN §5 probe.ae_issues:
    issue = > 1 entry, an empty edit (dwell), rate != 1, a start edit that is not the codec delay
    (video: B-frame reorder shift; audio: AAC priming <= 0.1 s), or samples hidden by the edit
    (decoded frames < stored samples)."""
    issues: list[str] = []
    notes: list[str] = []
    by_id = {t.track_id: t for t in tracks}

    def track_for(stream: dict | None, handler: str) -> TrackEdits | None:
        if stream is None:
            return None
        sid = stream.get("id")
        if sid:
            try:
                t = by_id.get(int(str(sid), 16))
                if t is not None:
                    return t
            except ValueError:
                pass
        cands = [t for t in tracks if t.handler == handler]
        return cands[0] if cands else None

    for kind, stream, handler in (("video", vstream, "vide"), ("audio", astream, "soun")):
        t = track_for(stream, handler)
        if t is None or not t.entries:
            continue
        ents = t.entries
        if len(ents) > 1:
            empties = sum(1 for e in ents if e[1] == -1)
            issues.append(f"edit_list: {kind} edit list has {len(ents)} entries"
                          + (f" incl. {empties} empty edit(s) (dwell / start offset)" if empties else ""))
            continue
        dur, media_time, rate = ents[0]
        if media_time == -1:
            issues.append(f"edit_list: {kind} edit list is a single empty edit")
            continue
        if abs(rate - 1.0) > 1e-6:
            issues.append(f"edit_list: {kind} edit rate {rate:g} != 1")
            continue
        ts = max(1, t.media_timescale)
        if kind == "video":
            delay = _codec_delay_pts(path, vindex) if media_time != 0 else 0
            if delay is not None and media_time != delay:
                issues.append(f"edit_list: video edit starts at media time {media_time}/{ts} s but the codec "
                              f"delay is {delay}/{ts} s (trims {(media_time - delay) / ts:.6f} s)")
                continue
            notes.append(f"benign video edit (media_time {media_time}/{ts} = codec delay)")
        else:
            if media_time / ts > AUDIO_PRIMING_MAX_S:
                issues.append(f"edit_list: audio edit skips {media_time / ts:.6f} s (> priming)")
                continue
            notes.append(f"benign audio edit (priming {media_time}/{ts} s)")
    # samples hidden by an edit list (start or end trim): stored samples vs decoded frames
    try:
        stored = int(vstream.get("nb_frames")) if vstream.get("nb_frames") not in (None, "N/A") else None
    except ValueError:
        stored = None
    vt = track_for(vstream, "vide")
    if (decoded_n is not None and stored is not None and vt is not None and vt.entries
            and decoded_n < stored and not any(i.startswith("edit_list: video") for i in issues)):
        issues.append(f"edit_list: video edit list hides {stored - decoded_n} of {stored} stored frames")
    return issues, notes


# ----------------------------------------------------------------------------------------------
# Display geometry
# ----------------------------------------------------------------------------------------------

def _rotation_cw(vstream: dict) -> tuple[int, list[str]]:
    """CLOCKWISE display rotation = (-displaymatrix_rotation) % 360 (ffmpeg autorotate semantics).
    Falls back to the legacy 'rotate' tag (already clockwise)."""
    issues: list[str] = []
    rot = None
    for sd in vstream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = _float(sd.get("rotation"))
            cw = (-rot) % 360.0
            break
    else:
        tag = (vstream.get("tags") or {}).get("rotate")
        cw = _float(tag) % 360.0 if tag is not None else 0.0
    q = int(round(cw / 90.0)) * 90 % 360
    if abs(((cw - q + 180) % 360) - 180) > 0.5:
        issues.append(f"rotation_matrix: display rotation {cw:g} deg is not a multiple of 90")
    return q, issues


def display_geometry(width: int, height: int, rotation: int, sar: Fraction) -> tuple[int, int, Fraction]:
    """(display_w, display_h, sar_after_rotation) exactly as ``media.VideoReader`` produces frames:
    rotate first (SAR of a 90/270-rotated frame is 1/SAR), then stretch one axis (never shrink)."""
    rot = int(rotation) % 360
    w, h = (height, width) if rot in (90, 270) else (width, height)
    s = Fraction(sar) if sar else Fraction(1)
    if rot in (90, 270) and s:
        s = 1 / s
    if s > 1:
        return int(round(w * float(s))), h, s
    if 0 < s < 1:
        return w, int(round(h / float(s))), s
    return w, h, Fraction(1)


def reader_sar(info: StreamInfo) -> Fraction:
    """SAR to pass to ``media.VideoReader`` (it applies SAR AFTER rotating, so it needs the
    post-rotation SAR: 1/SAR for 90/270 degrees)."""
    s = Fraction(info.sar) if info.sar else Fraction(1)
    if info.rotation % 360 in (90, 270) and s:
        return 1 / s
    return s


# ----------------------------------------------------------------------------------------------
# Timing analysis
# ----------------------------------------------------------------------------------------------

def nominal_fps(avg: Fraction, r: Fraction, rel_pts: np.ndarray | None = None) -> tuple[Fraction, str]:
    """Nominal timeline rate (DESIGN §5 probe): nearest common rate within 1 % of the measured
    regular cadence (mean PTS delta over non-outlier deltas — robust to drops/dups and ms-rounded
    timestamps), else of avg_frame_rate, else of r_frame_rate; else an exact rate when avg == r;
    else raise ValueError('ambiguous nominal fps')."""
    tried = []
    if rel_pts is not None and len(rel_pts) >= 3:
        d = np.diff(rel_pts)
        d = d[d > 0]
        if len(d):
            med = float(np.median(d))
            reg = d[(d > 0.5 * med) & (d < 1.5 * med)]
            if len(reg):
                meas = 1.0 / float(np.mean(reg))
                tried.append(f"pts cadence {meas:.4f}")
                r0 = nearest_common_rate(meas)
                if r0 is not None:
                    # the cadence must also agree with the container's claims when those are sane
                    return r0, f"pts cadence {meas:.5f} fps"
    for name, v in (("avg_frame_rate", avg), ("r_frame_rate", r)):
        if v and v > 0:
            tried.append(f"{name} {fps_str(v)}")
            r0 = nearest_common_rate(v)
            if r0 is not None:
                return r0, f"{name} {fps_str(v)}"
    if avg and r and avg == r and 1 <= float(avg) <= 1000:
        return Fraction(avg), f"exact uncommon rate avg == r_frame_rate {fps_str(avg)}"
    raise ValueError("ambiguous nominal fps: " + "; ".join(tried or ["no frame rate information"]))


def timing_stats(rel_pts: np.ndarray, fps: Fraction) -> tuple[float, bool]:
    """(pts_jitter, vfr). pts_jitter = max over (a) |delta - 1/fps| and (b) |pts_k - k/fps| (drift
    from the nominal grid), in frames. VFR when > VFR_JITTER_FRAMES (0.1 frame)."""
    n = len(rel_pts)
    if n < 2:
        return 0.0, False
    f = float(fps)
    d = np.diff(rel_pts) * f
    jit = float(np.max(np.abs(d - 1.0)))
    grid = float(np.max(np.abs((rel_pts - rel_pts[0]) * f - np.arange(n))))
    j = max(jit, grid)
    return j, j > VFR_JITTER_FRAMES


def _first_audio_time(path: str) -> float | None:
    """Presentation time (s) of the first decoded audio sample (after priming skip), via PyAV."""
    try:
        import av
        with av.open(path) as c:
            if not c.streams.audio:
                return None
            s = c.streams.audio[0]
            for pkt in c.demux(s):
                for fr in pkt.decode():
                    if fr.pts is None:
                        continue
                    return float(Fraction(int(fr.pts)) * Fraction(fr.time_base.numerator, fr.time_base.denominator))
    except Exception as e:
        log.warning("%s: could not decode the first audio frame (%s); using ffprobe start_time", path, e)
    return None


def _container_name(fmt: dict, path: str) -> str:
    name = str(fmt.get("format_name", ""))
    ext = Path(path).suffix.lower().lstrip(".")
    if "mov" in name.split(",") or "mp4" in name.split(","):
        brand = str((fmt.get("tags") or {}).get("major_brand", "")).strip().lower()
        if brand == "qt":
            return "mov"
        if brand.startswith("3g"):
            return "3gp"
        if ext in ("mov", "mp4", "m4v"):
            return ext
        return "mp4"
    if "matroska" in name or "webm" in name:
        return "webm" if ext == "webm" else "mkv"
    return name.split(",")[0] if name else ext


# ----------------------------------------------------------------------------------------------
# AE safety
# ----------------------------------------------------------------------------------------------

def ae_issues(info: StreamInfo) -> list[str]:
    """Reasons why After Effects may not import ``info`` reliably/1:1 (empty list = AE-safe).

    Derived from the StreamInfo fields, plus the probe-only findings (edit lists, interlacing,
    stream layout, non-90° display matrices) that probe() stored in ``info.ae_issues``."""
    out: list[str] = []
    if info.container not in AE_SAFE_CONTAINERS:
        out.append(f"container: {info.container or 'unknown'} is not mp4/mov")
    if info.vcodec not in AE_SAFE_VCODECS:
        out.append(f"vcodec: {info.vcodec or 'unknown'} is not H.264/ProRes"
                   + (" (HEVC import needs AE newer than CC 2019)" if info.vcodec == "hevc" else ""))
    elif info.vcodec == "h264" and info.pix_fmt and info.pix_fmt not in H264_AE_PIX_FMTS:
        out.append(f"vcodec: H.264 with pix_fmt {info.pix_fmt} (AE needs 8-bit 4:2:0)")
    if info.vfr:
        out.append(f"vfr: PTS jitter {info.pts_jitter:.3f} frames at nominal {fps_str(info.fps)}")
    if abs(info.v_start_time) > START_TOL_S:
        out.append(f"start_time: video stream starts at {info.v_start_time:.6f} s")
    if abs(info.first_pts_time - info.v_start_time) > START_TOL_S:
        out.append(f"start_time: first decoded video PTS {info.first_pts_time:.6f} s != stream start "
                   f"{info.v_start_time:.6f} s")
    if info.has_audio:
        if abs(info.a_start_time) > START_TOL_S:
            out.append(f"start_time: audio stream starts at {info.a_start_time:.6f} s")
        if abs(info.av_offset) > START_TOL_S:
            out.append(f"start_time: audio starts {info.av_offset * 1000:+.3f} ms relative to video frame 0")
        if not (info.acodec == "aac" or info.acodec.startswith("pcm_")):
            out.append(f"acodec: {info.acodec or 'unknown'} is not AAC/PCM")
    if info.edit_list and not any(i.startswith("edit_list") for i in info.ae_issues):
        out.append("edit_list: non-benign edit list")
    if info.rotation % 360:
        out.append(f"rotation: display rotation {info.rotation} deg clockwise")
    if info.sar and Fraction(info.sar) != 1:
        out.append(f"sar: non-square pixels (SAR {fps_str(Fraction(info.sar))})")
    if info.width % 2 or info.height % 2:
        out.append(f"odd_dims: {info.width}x{info.height} (4:2:0 / 4:2:2 need even sizes)")
    carried = [i for i in info.ae_issues if i.split(":", 1)[0] in PROBE_ONLY_CODES]
    for i in carried:
        if i not in out:
            out.append(i)
    return out


# ----------------------------------------------------------------------------------------------
# PTS sidecars
# ----------------------------------------------------------------------------------------------

def _sidecar(info_or_pts_file: StreamInfo | str, suffix: str) -> Path:
    p = Path(info_or_pts_file.pts_file if isinstance(info_or_pts_file, StreamInfo) else info_or_pts_file)
    name = p.name[:-len(".pts.npy")] if p.name.endswith(".pts.npy") else p.stem
    return p.with_name(name + suffix)


def load_pts(info: StreamInfo) -> np.ndarray:
    """Decoded PTS (seconds relative to the video stream start) of every frame of ``info``."""
    if info.pts_file and Path(info.pts_file).exists():
        return np.load(info.pts_file)
    return np.arange(int(info.nb_frames), dtype=np.float64) / float(info.fps)


def load_pts_int(info: StreamInfo) -> tuple[np.ndarray, Fraction, int]:
    """(integer PTS array in the stream time base, time_base, origin_pts) — exact timestamps for
    PTS lookups (conform verification). Recomputed with a decode pass if the sidecar is missing."""
    if info.pts_file:
        side = _sidecar(info, ".ptsint.npz")
        if side.exists():
            with np.load(side) as z:
                return (z["pts"].astype(np.int64), Fraction(int(z["tb_num"]), int(z["tb_den"])),
                        int(z["origin"]))
    from .media import decode_pts
    vindex = _video_ordinal(ffprobe_json(info.path))[0]
    pts, tb, start = decode_pts(info.path, vindex)
    pts = np.sort(pts)
    origin = int(start) if start is not None else (int(pts[0]) if len(pts) else 0)
    return pts, tb, origin


def _video_ordinal(pj: dict) -> tuple[int, dict | None, list[str]]:
    """(ordinal among video streams, stream dict, issues) of the main video stream: the first video
    stream that is not an attached picture (cover art)."""
    vids = [s for s in pj.get("streams", []) if s.get("codec_type") == "video"]
    issues: list[str] = []
    for i, s in enumerate(vids):
        if not (s.get("disposition") or {}).get("attached_pic"):
            if i > 0:
                issues.append(f"stream_layout: main video is video stream #{i} (preceded by cover art)")
            return i, s, issues
    return 0, (vids[0] if vids else None), issues


# ----------------------------------------------------------------------------------------------
# probe()
# ----------------------------------------------------------------------------------------------

def probe(path: str | os.PathLike, role: str, work_dir: str | os.PathLike, decode: bool = True) -> StreamInfo:
    """Probe ``path`` (ffprobe + one full decode pass) -> StreamInfo (DESIGN §5 probe.py).

    role: 'raw' | 'competitor'. Cached in ``work_dir/cache/probe`` by file hash (+ decode flag);
    ``path``/``role`` of a cached result are refreshed. Raises FileNotFoundError / RuntimeError /
    ValueError ('ambiguous nominal fps') with an explanatory message."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"input file not found: {p}")
    abspath = str(p.resolve())
    fh = file_hash(abspath)
    cache = Cache(work_dir)
    key = stage_key("probe", fh, bool(decode))
    jpath = cache.path("probe", key, ".json")
    pts_path = cache.path("probe", key, ".pts.npy")
    if jpath.exists() and (not decode or pts_path.exists()):
        try:
            info = StreamInfo.from_dict(json.loads(jpath.read_text()))
            info.path, info.role = abspath, role
            if decode:
                info.pts_file = str(pts_path)
            log.debug("probe cache hit %s (%s)", abspath, key)
            return info
        except Exception as e:  # corrupt cache entry -> recompute
            log.warning("probe cache entry %s unreadable (%s); re-probing", jpath, e)

    pj = ffprobe_json(abspath)
    fmt = pj.get("format", {}) or {}
    vindex, vs, layout_issues = _video_ordinal(pj)
    if vs is None:
        raise RuntimeError(f"{abspath}: no video stream found")
    auds = [s for s in pj.get("streams", []) if s.get("codec_type") == "audio"]
    as_ = auds[0] if auds else None

    info = StreamInfo(path=abspath, role=role)
    info.container = _container_name(fmt, abspath)
    info.vcodec = str(vs.get("codec_name", ""))
    info.vprofile = str(vs.get("profile", "") or "")
    info.pix_fmt = str(vs.get("pix_fmt", "") or "")
    info.color_range = str(vs.get("color_range", "") or "")
    info.width, info.height = int(vs.get("width") or 0), int(vs.get("height") or 0)
    if info.width <= 0 or info.height <= 0:
        raise RuntimeError(f"{abspath}: video stream has no valid size")
    sar = _frac(vs.get("sample_aspect_ratio"))
    info.sar = sar if sar > 0 else Fraction(1)
    dar = _frac(vs.get("display_aspect_ratio"))
    info.dar = dar if dar > 0 else Fraction(info.width, info.height) * info.sar
    info.rotation, rot_issues = _rotation_cw(vs)
    info.display_width, info.display_height, _ = display_geometry(info.width, info.height, info.rotation, info.sar)
    info.r_frame_rate = _frac(vs.get("r_frame_rate"))
    info.avg_frame_rate = _frac(vs.get("avg_frame_rate"))
    info.v_start_time = _float(vs.get("start_time"))
    info.container_duration = _float(fmt.get("duration"), _float(vs.get("duration")))
    info.file_size = p.stat().st_size
    info.file_hash = fh
    field_order = str(vs.get("field_order", "") or "")
    issues_extra: list[str] = list(layout_issues) + list(rot_issues)
    if field_order in ("tt", "bb", "tb", "bt"):
        issues_extra.append(f"interlaced: field order {field_order}")

    if as_ is not None:
        info.has_audio = True
        info.acodec = str(as_.get("codec_name", ""))
        info.a_sample_rate = int(_float(as_.get("sample_rate"), 0))
        info.a_channels = int(as_.get("channels") or 0)
        info.a_start_time = _float(as_.get("start_time"))

    rel = None
    ptsint = None
    if decode:
        from .media import decode_pts
        pts, tb, start = decode_pts(abspath, vindex)
        if len(pts) == 0:
            raise RuntimeError(f"{abspath}: the video stream decoded to 0 frames")
        if np.any(np.diff(pts) <= 0):
            n_bad = int(np.sum(np.diff(pts) <= 0))
            issues_extra.append(f"pts_order: {n_bad} non-increasing decoded PTS")
            pts = np.unique(pts)
        origin = int(start) if start is not None else int(pts[0])
        info.first_pts_time = float(Fraction(int(pts[0])) * tb)
        if start is None:
            info.v_start_time = info.first_pts_time
        rel = (pts - origin).astype(np.float64) * tb.numerator / tb.denominator
        info.nb_frames = int(len(pts))
        ptsint = (pts, tb, origin)
    else:
        info.first_pts_time = info.v_start_time
        try:
            info.nb_frames = int(vs.get("nb_frames"))
        except (TypeError, ValueError):
            info.nb_frames = 0

    fps, why = nominal_fps(info.avg_frame_rate, info.r_frame_rate, rel)
    info.fps = fps
    if rel is not None:
        info.pts_jitter, info.vfr = timing_stats(rel, fps)
    else:
        a, r = info.avg_frame_rate, info.r_frame_rate
        info.vfr = bool(a and r and abs(float(a) / float(r) - 1) > 1e-3)
        if not info.nb_frames:
            info.nb_frames = int(round(info.container_duration * float(fps)))
    info.duration = info.nb_frames / float(fps) if fps else 0.0

    if info.has_audio:
        a_first = _first_audio_time(abspath) if decode else None
        a0 = a_first if a_first is not None else info.a_start_time
        info.av_offset = float(a0 - info.first_pts_time)

    notes: list[str] = []
    if info.container in ("mp4", "mov", "m4v", "3gp"):
        try:
            tracks = read_edit_lists(abspath)
        except Exception as e:  # pragma: no cover - corrupt files
            tracks = []
            notes.append(f"edit list parse failed: {e}")
        el_issues, el_notes = _edit_list_issues(abspath, vs, as_, vindex, info.nb_frames if decode else None, tracks)
        notes += el_notes
        issues_extra += el_issues
        info.edit_list = bool(el_issues)
        edits = [t.to_dict() for t in tracks]
    else:
        edits = []

    info.ae_issues = issues_extra
    info.ae_issues = ae_issues(info)

    # persist
    extra = {"nominal_fps_reason": why, "field_order": field_order, "edit_lists": edits, "notes": notes,
             "video_stream_ordinal": vindex}
    if decode and rel is not None and ptsint is not None:
        np.save(pts_path, rel)
        pts_i, tb, origin = ptsint
        np.savez(_sidecar(str(pts_path), ".ptsint.npz"), pts=pts_i, tb_num=np.int64(tb.numerator),
                 tb_den=np.int64(tb.denominator), origin=np.int64(origin))
        info.pts_file = str(pts_path)
    atomic_write_text(cache.path("probe", key, ".extra.json"), json.dumps(extra, indent=1, sort_keys=True))
    atomic_write_text(cache.path("probe", key, ".ffprobe.json"), json.dumps(pj, indent=1, sort_keys=True))
    atomic_write_text(jpath, json.dumps(info.to_dict(), indent=1, sort_keys=True))
    log.info("probe %s [%s]: %s %dx%d%s @ %s fps (%s), %d frames%s, audio %s; AE issues: %s",
             Path(abspath).name, role, info.vcodec, info.width, info.height,
             f" rot {info.rotation}" if info.rotation else "", fps_str(fps), "VFR" if info.vfr else "CFR",
             info.nb_frames, f", first PTS {info.first_pts_time:.3f}s" if info.first_pts_time else "",
             f"{info.acodec} {info.a_sample_rate} Hz x{info.a_channels} offset {info.av_offset * 1000:+.1f} ms"
             if info.has_audio else "none", "; ".join(info.ae_issues) or "none")
    return info


def probe_extra(info: StreamInfo) -> dict:
    """The probe's side information (nominal-fps reason, field order, edit lists, notes)."""
    if info.pts_file:
        side = _sidecar(info, ".extra.json")
        if side.exists():
            return json.loads(side.read_text())
    return {}


def video_stream_ordinal(info: StreamInfo) -> int:
    """Ordinal (among video streams) of the main video stream (skips leading cover art)."""
    ex = probe_extra(info)
    if "video_stream_ordinal" in ex:
        return int(ex["video_stream_ordinal"])
    return _video_ordinal(ffprobe_json(info.path))[0]
