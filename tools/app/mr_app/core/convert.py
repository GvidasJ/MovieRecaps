"""CONVERT: a video into what Premiere Pro edits well -- H.264 (or HEVC) with AAC sound in an .mp4 -- with FFmpeg,
like Shutter Encoder, without losing quality.

* ``inspect(path)`` reads a video with ffprobe (no decoding): codec, profile, pixel format, bit depth, size, frame
  rate, frame count (the header's, else the packets counted), length, sound, colour tags.
* ``plan(info)`` decides, in plain words:
    keep    already .mp4 with H.264 / HEVC and AAC / MP3 sound (or none), or ProRes / DNxHD / CineForm in .mov /
            .mxf: nothing to do, nothing deleted;
    rewrap  H.264 / HEVC in another container (.mkv, .webm, .mov, .avi, .ts ...): the picture is copied into an .mp4
            bit for bit (100 % lossless); the sound is copied when it is AAC / MP3, else made AAC (said so);
    encode  VP9, AV1, VP8, MPEG-2, MPEG-4 part 2 (DivX / Xvid), WMV, ... -- what Premiere handles badly or not at
            all: visually lossless H.264 (module constants below), the same size, frame rate, frames and colours.
  "Premiere handles it well" = H.264 and HEVC (Premiere Pro decodes both natively, HEVC with hardware help) in MP4,
  plus the intermediate codecs ProRes, DNxHD/DNxHR and CineForm in their MOV / MXF files. Sound: AAC and MP3 in MP4.
* ``convert_file(path)`` writes ``<name>.converting.mp4`` next to the video, CHECKS it against the original (the
  same number of frames, the same length within one frame, the sound's length within 0.15 s, the same picture size
  and frame rate), and only then deletes the original and gives the new file the original's name with .mp4. A
  failed check keeps the original and removes the bad copy (ConvertError says why in plain words).
* ``convert_project(project)``: the project's competitor and RAW (projects.py), the step written to project.json.

The encode keeps every frame with its own timing (``-fps_mode passthrough``): a video whose frames sit on its frame
rate's grid (every download: WebM/MKV store times rounded to the millisecond) gets exact constant-rate times
(the encoder's time base is 1/fps -- no frame moves by more than the rounding); a truly variable one keeps its own
times (``-enc_time_base demux``). Colour tags are copied, 8-bit stays 8-bit 4:2:0, 10-bit goes to x264's High 10 on
the CPU (NVENC's H.264 has no 10-bit on most cards). The sound is copied when it is AAC, else AAC 320 kb/s (5.1:
640 kb/s, still 5.1).

GPU or CPU ("Use the GPU if the quality is the same, else CPU"): measured on night 10 (work\\t25\\download_convert\\
NOTES.md) -- see NVENC_SAME_QUALITY. Progress: ``progress(fraction 0..1, text)`` from the thread that converts; cancel:
a ``threading.Event`` -- FFmpeg is killed, the temp file removed, ``Cancelled`` raised."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .run import CREATE_NO_WINDOW, kill_tree

Progress = Callable[[float, str], None]

# what Premiere Pro handles well (module docstring)
COPY_VIDEO = {"h264", "hevc"}                       # never re-encoded: kept / copied into MP4
KEEP_IN_MOV = {"prores", "dnxhd", "cfhd"}           # kept as they are in .mov / .mxf (MP4 cannot hold them)
COPY_AUDIO = {"aac", "mp3"}                         # copied; anything else becomes AAC
MP4_EXTS = {".mp4"}                                 # 'keep' only for these (.m4v / .mov are rewrapped to .mp4)
NOT_VIDEO = {"tty", "image2", "lavfi", "txt"}       # FFmpeg reads text files and pictures as 'video' too
NAMES = {"h264": "H.264", "hevc": "HEVC", "vp9": "VP9", "av1": "AV1", "vp8": "VP8", "mpeg2video": "MPEG-2",
         "mpeg4": "MPEG-4 part 2 (DivX/Xvid)", "msmpeg4v3": "MPEG-4 v3 (DivX 3)", "wmv3": "WMV", "vc1": "VC-1",
         "prores": "ProRes", "dnxhd": "DNxHD", "cfhd": "CineForm", "mjpeg": "Motion JPEG", "theora": "Theora",
         "aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis", "ac3": "AC-3", "eac3": "E-AC-3",
         "flac": "FLAC", "dts": "DTS", "truehd": "TrueHD", "alac": "ALAC"}

# the encodes (night 10's measurement, work\t25\download_convert\NOTES.md: three samples -- a 1080p movie RAW,
# a VP9 1080x1920 60 fps Short, a 4K VP9 RAW -- each encoded both ways and compared with its source)
X264 = ["-c:v", "libx264", "-preset", "slow", "-crf", "13", "-profile:v", "high", "-pix_fmt", "yuv420p"]
X264_10BIT = ["-c:v", "libx264", "-preset", "slow", "-crf", "13", "-profile:v", "high10", "-pix_fmt", "yuv420p10le"]
# NVENC as good as x264 CRF 13 on all three samples needs cq 17 (SSIM +0.00004..+0.0009, PSNR +0.1..+3.3 dB) at
# 1.8-2.3x the file size; at x264's own size it loses on the film (cq 20: 1.2x the size, -0.0008 SSIM, -1.3 dB) and
# only ties on the two VP9 downloads. -level 5.2: NVENC's automatic level capped its bitrate (cq 10 = cq 12).
NVENC = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17", "-b:v", "0",
         "-spatial-aq", "1", "-temporal-aq", "1", "-multipass", "fullres", "-profile:v", "high", "-level", "5.2",
         "-bf", "3", "-b_ref_mode", "middle", "-rc-lookahead", "32", "-pix_fmt", "yuv420p"]
NVENC_SAME_QUALITY = False           # the same quality only at about twice the size: gpu="auto" is the CPU
GOP_S = 2.0                          # a key frame every 2 s (Premiere scrubs long-GOP files more slowly)
AAC_STEREO = "320k"
AAC_SURROUND = "640k"                # 3-6 channels (5.1)
AAC_71 = "768k"
CHECK_AUDIO_S = 0.15                 # the sound's length may differ this much (AAC's start/end padding)
LOW_PRIORITY = True                  # FFmpeg at below-normal priority: the window, Premiere and the matching tool
BELOW_NORMAL_PRIORITY_CLASS = 0x4000 # stay quick (on an idle PC it is just as fast)
GRID_TOLERANCE = 0.3                 # frame times spread less than this share of a frame around the grid -> CFR


class ConvertError(Exception):
    """A conversion that did not work, in plain words (the original is always kept then)."""


class Cancelled(Exception):
    """Stop was pressed (the cancel event was set)."""


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE", "ffprobe")


def _run(cmd: list[str], timeout_s: float = 600.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout_s, creationflags=CREATE_NO_WINDOW)


# ---------------------------------------------------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------------------------------------------------

def _frac(s: str | None) -> Fraction | None:
    try:
        f = Fraction(str(s))
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def _hms(s: str | None) -> float | None:
    """'00:09:15.033000000' (Matroska's DURATION tag) -> 555.033."""
    m = re.match(r"^\s*(\d+):(\d+):(\d+(?:\.\d+)?)\s*$", s or "")
    return 3600 * int(m.group(1)) + 60 * int(m.group(2)) + float(m.group(3)) if m else None


def _stream_duration(s: dict) -> float | None:
    try:
        d = float(s.get("duration"))
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    tags = {k.upper(): v for k, v in (s.get("tags") or {}).items()}
    return _hms(tags.get("DURATION"))


def _bit_depth(s: dict) -> int:
    try:
        b = int(s.get("bits_per_raw_sample") or 0)
        if b:
            return b
    except (TypeError, ValueError):
        pass
    m = re.search(r"(?:p|le|be)?(9|10|12|14|16)(?:le|be)?$", s.get("pix_fmt") or "")
    return int(m.group(1)) if m and not (s.get("pix_fmt") or "").startswith(("rgb", "bgr", "gray")) else 8


def _tag(v: str | None) -> str | None:
    return None if v in (None, "", "unknown", "reserved", "unspecified") else v


@dataclass
class MediaInfo:
    path: Path
    container: str                    # mp4 / mov / mkv / webm / avi / mpegts / flv / mxf ...
    vcodec: str | None                # ffprobe's codec_name: h264, hevc, vp9, av1 ...
    profile: str | None
    pix_fmt: str | None
    bit_depth: int
    width: int
    height: int
    fps: Fraction | None              # the nominal frame rate
    frames: int | None                # video frames (the header's count, else the packets counted)
    duration: float | None            # the picture's length (s)
    acodec: str | None                # the first sound's codec
    channels: int | None
    color_range: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    audio_duration: float | None = None
    size: int = 0
    video_index: int | None = None
    audio: list[dict] = field(default_factory=list)   # every sound stream: index, codec, channels, layout, rate
    rotation: int = 0
    avg_fps: Fraction | None = None
    format_name: str = ""

    @property
    def display_size(self) -> tuple[int, int]:
        return (self.height, self.width) if self.rotation % 180 == 90 else (self.width, self.height)

    def describe(self) -> str:
        """'VP9 1920x1080 29.97 fps, 0:11:22, Opus 2 ch, webm'."""
        bits = [NAMES.get(self.vcodec or "", self.vcodec or "no picture"), f"{self.width}x{self.height}"]
        if self.fps:
            bits.append(f"{float(self.fps):.3f}".rstrip("0").rstrip(".") + " fps")
        if self.bit_depth > 8:
            bits.append(f"{self.bit_depth}-bit")
        if self.duration:
            bits.append(_clock(self.duration))
        bits.append(f"{NAMES.get(self.acodec or '', self.acodec)} {self.channels or '?'} ch" if self.acodec
                    else "no sound")
        return ", ".join(bits) + f", {self.container}"


def _probe_json(path: Path) -> dict:
    try:
        r = _run([ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 300)
    except FileNotFoundError as e:
        raise ConvertError("FFmpeg (ffprobe) is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise ConvertError(f"{path.name}: reading it took too long") from e
    if r.returncode != 0:
        raise ConvertError(f"{path.name} cannot be read: it is not a video or it is damaged "
                           f"({(r.stderr or '').strip().splitlines()[-1:] or ['ffprobe failed']})")
    return json.loads(r.stdout or "{}")


def packets(path: Path, index: int) -> list[float | None] | None:
    """The times of the video stream's SHOWN frames, from its packets (ffprobe, nothing decoded -- like
    ``-count_packets``, but without the packets an MP4 / MOV edit list hides: a trimmed file keeps the frames before
    its start for decoding, and its header counts them too); None when ffprobe fails."""
    try:
        r = _run([ffprobe_bin(), "-v", "error", "-select_streams", str(index), "-show_entries", "packet=pts_time,flags",
                  "-of", "csv=p=0", str(path)], 1800)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    out: list[float | None] = []
    for line in (r.stdout or "").splitlines():
        bits = line.strip().split(",")
        if not bits or not bits[0]:
            continue
        if len(bits) > 1 and "D" in bits[1]:
            continue                                                 # hidden by the edit list
        try:
            out.append(float(bits[0]))
        except ValueError:
            out.append(None)                                         # N/A
    return out


def count_frames(path: Path, index: int) -> int | None:
    """The video stream's shown frames, counted from its packets (``packets``)."""
    p = packets(path, index)
    return len(p) if p is not None else None


def _container(path: Path, fmt: dict) -> str:
    name = fmt.get("format_name") or ""
    ext = path.suffix.lower()
    if "mp4" in name or "mov" in name:
        brand = ((fmt.get("tags") or {}).get("major_brand") or "").strip()
        return "mov" if ext == ".mov" or brand == "qt" else "mp4"
    if "matroska" in name or "webm" in name:
        return "webm" if ext == ".webm" else "mkv"
    return name.split(",")[0] or ext.lstrip(".")


def inspect(path: str | os.PathLike) -> MediaInfo:
    """What a video holds (module docstring); ConvertError when it is not a readable video."""
    p = Path(path)
    if not p.is_file():
        raise ConvertError(f"{p.name} is not there")
    js = _probe_json(p)
    streams = js.get("streams") or []
    fmt = js.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    fmt_dur = None
    try:
        fmt_dur = float(fmt.get("duration"))
    except (TypeError, ValueError):
        pass
    a0 = auds[0] if auds else {}
    rotation = 0
    frames = None
    fps = avg = None
    if v is not None:
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rotation = int(round(abs(float(sd["rotation"])))) % 360
                except (TypeError, ValueError):
                    pass
        try:
            frames = int(v.get("nb_frames")) or None
        except (TypeError, ValueError):
            frames = None
        if frames is None:
            frames = count_frames(p, int(v["index"]))
        fps, avg = _frac(v.get("r_frame_rate")), _frac(v.get("avg_frame_rate"))
        if fps is None or fps > 1000:
            fps = avg
    return MediaInfo(
        path=p, container=_container(p, fmt), vcodec=v.get("codec_name") if v else None,
        profile=v.get("profile") if v else None, pix_fmt=v.get("pix_fmt") if v else None,
        bit_depth=_bit_depth(v) if v else 8, width=int(v.get("width") or 0) if v else 0,
        height=int(v.get("height") or 0) if v else 0, fps=fps, frames=frames,
        duration=(_stream_duration(v) or fmt_dur) if v else fmt_dur,
        acodec=a0.get("codec_name"), channels=a0.get("channels"),
        color_range=_tag(v.get("color_range")) if v else None,
        color_primaries=_tag(v.get("color_primaries")) if v else None,
        color_transfer=_tag(v.get("color_transfer")) if v else None,
        color_space=_tag(v.get("color_space")) if v else None,
        audio_duration=(_stream_duration(a0) or fmt_dur) if a0 else None, size=p.stat().st_size,
        video_index=int(v["index"]) if v else None,
        audio=[{"index": int(a["index"]), "codec": a.get("codec_name"), "channels": a.get("channels"),
                "layout": a.get("channel_layout"), "rate": a.get("sample_rate")} for a in auds],
        rotation=rotation, avg_fps=avg, format_name=fmt.get("format_name") or "")


@dataclass
class Timing:
    """The picture's packets: how many, and whether their times sit on the frame-rate grid."""
    frames: int
    duration: float | None
    on_grid: bool
    first: float | None = None


def on_grid(times: list[float], fps: Fraction, start: float = 0.0) -> bool:
    """Do these frame times sit on the frame-rate grid, so that rounding each to the nearest slot of 1/fps (counted
    from ``start``, where FFmpeg starts the output) moves none by more than the rounding and gives each its own
    slot? (Their offsets from the grid spread by less than GRID_TOLERANCE of a frame -- WebM's millisecond rounding
    is about 0.03 frames at 30 fps -- and none is near half a frame, where rounding could go either way.)"""
    if not times:
        return False
    period = 1.0 / float(fps)
    slots = [(t - start) / period for t in sorted(times)]
    offs = [s - round(s) for s in slots]
    return (max(offs) - min(offs) < GRID_TOLERANCE and max(abs(o) for o in offs) < 0.45
            and len({round(s) for s in slots}) == len(slots))


def video_timing(info: MediaInfo, start: float = 0.0, fps: Fraction | None = None) -> Timing:
    """The shown frames' times (``packets``): how many, the picture's length (first to last + one frame of ``fps``,
    default the file's own) and whether they sit on the frame-rate grid (on_grid)."""
    fps = fps or info.fps
    pk = packets(info.path, int(info.video_index or 0))
    if pk is None:
        return Timing(info.frames or 0, info.duration, False)
    times = sorted(t for t in pk if t is not None)
    if not times or not fps:
        return Timing(len(pk), info.duration, False)
    period = 1.0 / float(fps)
    return Timing(len(pk), times[-1] - times[0] + period,
                  len(times) == len(pk) and on_grid(times, fps, start), times[0])


# ---------------------------------------------------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class AudioPlan:
    index: int
    action: str                       # copy | aac
    bitrate: str | None = None
    remap_51: bool = False            # 5.1(side) relabelled 5.1 (AAC names it then)


@dataclass
class Plan:
    how: str                          # keep | rewrap | encode
    why: str                          # plain words
    audio: list[AudioPlan] = field(default_factory=list)
    ten_bit: bool = False
    hevc: bool = False


def _aac_rate(channels: int | None) -> str:
    c = int(channels or 2)
    return AAC_STEREO if c <= 2 else AAC_SURROUND if c <= 6 else AAC_71


def plan_audio(info: MediaInfo) -> list[AudioPlan]:
    """Every sound stream: copied (AAC / MP3), else made AAC."""
    return [AudioPlan(a["index"], "copy") if a["codec"] in COPY_AUDIO else
            AudioPlan(a["index"], "aac", _aac_rate(a["channels"]),
                      remap_51=a["channels"] == 6 and (a["layout"] or "") not in ("5.1", ""))
            for a in info.audio]


def plan(info: MediaInfo) -> Plan:
    """keep / rewrap / encode, and why (module docstring)."""
    if info.vcodec is None:
        raise ConvertError(f"{info.path.name} has no picture (only sound?)")
    if info.format_name in NOT_VIDEO or info.format_name.endswith("_pipe"):
        raise ConvertError(f"{info.path.name} is not a video (FFmpeg reads it as '{info.format_name}')")
    v = info.vcodec
    vname = NAMES.get(v, v.upper())
    cname = info.container.upper()
    if v in KEEP_IN_MOV and info.container in ("mov", "mxf"):
        return Plan("keep", f"{vname} in {cname}: Premiere edits it as it is -- nothing to do")
    audio = [AudioPlan(a["index"], "copy") if a["codec"] in COPY_AUDIO else
             AudioPlan(a["index"], "aac", _aac_rate(a["channels"]),
                       remap_51=a["channels"] == 6 and (a["layout"] or "") not in ("5.1", ""))
             for a in info.audio]
    made = sorted({NAMES.get(a["codec"], a["codec"] or "?") for a in info.audio if a["codec"] not in COPY_AUDIO})
    anote = (f"; the sound ({', '.join(made)}) made AAC "
             f"{'/'.join(sorted({p.bitrate.replace('k', '') for p in audio if p.bitrate}))} kb/s -- "
             "Premiere does not read it well" if made else "")
    if v in COPY_VIDEO:
        if info.container == "mp4" and info.path.suffix.lower() in MP4_EXTS and not made:
            return Plan("keep", f"{vname} + {'AAC/MP3 sound' if info.audio else 'no sound'} in MP4 already: "
                                "nothing to do")
        return Plan("rewrap", f"{vname} in {cname}: the picture copied into MP4 without re-encoding "
                              f"(100 % lossless){anote}", audio=audio, hevc=v == "hevc")
    return Plan("encode", f"{vname}: Premiere handles it badly -- converted to visually lossless H.264{anote}",
                audio=audio, ten_bit=info.bit_depth > 8)


# ---------------------------------------------------------------------------------------------------------------------
# the encoders
# ---------------------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Does this FFmpeg + GPU encode H.264 with NVENC? (a 6-frame test encode)"""
    try:
        r = _run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                  "color=c=black:s=256x144:r=30:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"], 60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def choose_encoder(info: MediaInfo, gpu: str | bool = "auto") -> tuple[str, list[str], str]:
    """(encoder name, its FFmpeg options, why). gpu: 'auto' (NVENC when NVENC_SAME_QUALITY and it is there),
    True / 'on' (NVENC when it is there), False / 'off' (the CPU). 10-bit always goes to the CPU (x264 High 10)."""
    want = str(gpu).lower()
    if info.bit_depth > 8:
        return "libx264", list(X264_10BIT), "10-bit picture: x264 High 10 on the CPU"
    use_gpu = want in ("true", "on", "gpu", "nvenc", "1") or (want == "auto" and NVENC_SAME_QUALITY)
    if use_gpu and nvenc_available():
        return "h264_nvenc", list(NVENC), "NVENC on the GPU"
    if use_gpu:
        return "libx264", list(X264), "x264 on the CPU (no NVENC here)"
    return "libx264", list(X264), "x264 on the CPU"


def _colour_args(info: MediaInfo) -> list[str]:
    out = []
    for opt, val in (("-color_range", info.color_range), ("-color_primaries", info.color_primaries),
                     ("-color_trc", info.color_transfer), ("-colorspace", info.color_space)):
        if val:
            out += [opt, val]
    return out


def ffmpeg_command(info: MediaInfo, pl: Plan, out_path: Path, encoder_args: list[str] | None = None,
                   time_base: str | None = None, restamp: bool = False) -> list[str]:
    """FFmpeg's command line for the plan: the picture (copied, or encoded with ``encoder_args`` keeping every
    frame's time), every sound stream (copied or AAC), progress on stdout. Day 16, ``restamp``: every decoded frame
    on the next slot of the constant rate (frame k = the k-th frame decoded; nothing dropped or doubled), 8-bit."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "info", "-progress", "pipe:1", "-nostats",
           "-stats_period", "0.5", "-i", str(info.path), "-map", f"0:{info.video_index}"]
    for a in pl.audio:
        cmd += ["-map", f"0:{a.index}"]
    cmd += ["-map_metadata", "0", "-map_chapters", "-1", "-dn", "-sn"]
    if pl.how == "rewrap":
        cmd += ["-c:v", "copy"] + (["-tag:v", "hvc1"] if pl.hevc else [])
    else:
        gop = max(1, round(GOP_S * float(info.fps))) if info.fps else 60
        if restamp and info.fps:
            cmd += ["-vf", f"setpts=N*{info.fps.denominator}/{info.fps.numerator}/TB,format=yuv420p"]
        cmd += list(encoder_args or X264) + ["-g", str(gop), "-fps_mode", "passthrough",
                                             "-enc_time_base:v", time_base or "demux"] + _colour_args(info)
    for k, a in enumerate(pl.audio):
        if a.action == "copy":
            cmd += [f"-c:a:{k}", "copy"]
        else:
            cmd += [f"-c:a:{k}", "aac", f"-b:a:{k}", a.bitrate or AAC_STEREO]
            if a.remap_51:
                cmd += [f"-filter:a:{k}", "channelmap=channel_layout=5.1"]
    cmd += ["-f", "mp4", str(out_path)]
    return cmd


def grid_time_base(fps: Fraction) -> str:
    """The encoder's time base for exact constant-rate times: 30000/1001 fps -> '1001:30000'."""
    return f"{fps.denominator}:{fps.numerator}"


# ---------------------------------------------------------------------------------------------------------------------
# checking the result
# ---------------------------------------------------------------------------------------------------------------------

def check(src: MediaInfo, out: MediaInfo, src_t: Timing, out_t: Timing,
          grid: bool = False, restamped: bool = False) -> tuple[bool, dict, list[str]]:
    """(all right, the numbers, the problems in plain words): the same shown frames, the picture's length within
    one frame, the sound's length within CHECK_AUDIO_S, the same picture size, the same frame rate (the average one
    -- frames per second of length -- and, for exact constant-rate times, the nominal one too). ``src_t`` /
    ``out_t``: the two files' video_timing (frames counted the same way on both)."""
    frame_s = 1.0 / float(src.fps) if src.fps else 0.05
    probs: list[str] = []
    src_frames, out_frames = src_t.frames, out_t.frames
    src_video_s, out_video_s = src_t.duration, out_t.duration
    checks = {"frames": [src_frames, out_frames],
              "duration_s": [round(src_video_s, 3) if src_video_s else None,
                             round(out_video_s, 3) if out_video_s else None],
              "audio_s": [round(src.audio_duration, 3) if src.audio_duration else None,
                          round(out.audio_duration, 3) if out.audio_duration else None],
              "fps": [_fps_text(src.fps), _fps_text(out.fps)],
              "size": ["x".join(map(str, src.display_size)), "x".join(map(str, out.display_size))],
              "bytes": [src.size, out.size]}
    if not src_frames or not out_frames:
        probs.append("the frames could not be counted")
    elif src_frames != out_frames:
        probs.append(f"{out_frames} frames instead of {src_frames}")
    on_grid = bool(restamped and src.fps and out_frames and out_video_s and
                   abs(out_video_s - out_frames / float(src.fps)) <= 0.002)
    if src_video_s and out_video_s and abs(src_video_s - out_video_s) > frame_s + 0.002 and not on_grid:
        # (day 16, ``restamped``: the very same frames on the exact constant rate may differ from an uneven original's
        # length -- the Ted 2 x265 file: 7550.816 s of jittered times, 181040 frames = 7550.877 s on the grid)
        probs.append(f"the picture is {out_video_s:.3f} s long instead of {src_video_s:.3f} s")
    if on_grid and src.audio_duration and abs(out_video_s - src.audio_duration) >             abs(src_video_s - src.audio_duration) + 2 * frame_s:
        probs.append(f"the re-stamped picture ends {out_video_s - src.audio_duration:+.3f} s from its sound")
    if src.audio and not out.audio:
        probs.append("the sound is missing")
    elif len(out.audio) < len(src.audio):
        probs.append(f"{len(out.audio)} sound tracks instead of {len(src.audio)}")
    if src.audio_duration and out.audio_duration and abs(src.audio_duration - out.audio_duration) > CHECK_AUDIO_S:
        probs.append(f"the sound is {out.audio_duration:.2f} s long instead of {src.audio_duration:.2f} s")
    if src.display_size != out.display_size:
        probs.append(f"the picture is {checks['size'][1]} instead of {checks['size'][0]}")
    if src_frames and out_frames and src_video_s and out_video_s:
        a, b = src_frames / src_video_s, out_frames / out_video_s
        if abs(a - b) / a > 0.005:
            probs.append(f"{b:.3f} frames a second instead of {a:.3f}")
    if grid and src.fps and out.fps and abs(float(src.fps) - float(out.fps)) / float(src.fps) > 0.001:
        probs.append(f"{_fps_text(out.fps)} fps instead of {_fps_text(src.fps)}")
    return not probs, checks, probs


def _fps_text(f: Fraction | None) -> str | None:
    if not f:
        return None
    return f"{f.numerator}/{f.denominator}" if f.denominator != 1 else str(f.numerator)


def _clock(s: float | None) -> str:
    s = int(round(max(0.0, float(s or 0))))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _size(n: float) -> str:
    n = float(n or 0)
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


# ---------------------------------------------------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class ConvertResult:
    path: Path                        # the file to use from now on
    how: str                          # keep | rewrap | encode
    encoder: str | None               # copy | libx264 | h264_nvenc | None (keep)
    from_codec: str | None
    checks: dict
    deleted_original: bool
    seconds: float
    note: str                         # plain words: what was done and why
    original: Path | None = None
    size_in: int = 0
    size_out: int = 0


def _ffmpeg_problem(log_tail: str, code: int | None = None) -> str:
    """FFmpeg's own messages (not this module's 'command:' / 'plan:' lines in the log) -> plain words; day 13: else
    its exit code (ffmpeg 7 exits with its error code: 4294967268 = -28 = the disk is full, -12 out of memory ...,
    match_cuts.plain_reason)."""
    log_tail = "\n".join(l for l in log_tail.splitlines()
                         if not l.startswith(("command:", "encoder:", "plan:", "in:", "---", "note:")))
    low = log_tail.lower()
    if "no space left" in low or "not enough space" in low:
        return "the disk is full: free some space and try again"
    if "invalid data found" in low or "error while decoding" in low or "corrupt" in low:
        return "the video seems damaged (FFmpeg could not read all of it)"
    if "nvenc" in low or "cuda" in low or "openencodesessionex" in low:
        return "the GPU encoder (NVENC) failed"
    if "permission denied" in low:
        return "Windows did not allow writing the file (is it open in another program?)"
    if code not in (None, 0, 1):
        try:
            from match_cuts import plain_reason as PR
            kind, words = PR.program_code(code)
            if kind == PR.DISK_FULL:
                return "the disk is full: free some space and try again"
            if kind:
                return f"FFmpeg stopped: {words}"
        except Exception:  # noqa: BLE001 - the tool's module not importable here: FFmpeg's own last line
            pass
    last = [l for l in log_tail.splitlines() if l.strip()][-1:] or ["FFmpeg failed"]
    return f"FFmpeg failed: {last[0].strip()[:160]}"


def _target(src: Path, out_dir: Path, delete_original: bool) -> Path:
    """<stem>.mp4 in out_dir; the original's own place when it is an .mp4 being replaced; never another file."""
    final = out_dir / f"{src.stem}.mp4"
    same = final.resolve() == src.resolve()
    if same and delete_original:
        return final
    if same or final.exists():
        k = 1
        while True:
            final = out_dir / (f"{src.stem}_converted.mp4" if k == 1 else f"{src.stem}_converted-{k}.mp4")
            if not final.exists():
                return final
            k += 1
    return final


def _run_ffmpeg(cmd: list[str], duration: float | None, label: str, say: Progress,
                cancel: threading.Event | None, log_f) -> int:
    """Run FFmpeg hidden: its log -> ``log_f``, its progress (stdout) -> ``say``; cancel kills it (Cancelled)."""
    flags = CREATE_NO_WINDOW | (BELOW_NORMAL_PRIORITY_CLASS if LOW_PRIORITY else 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log_f, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
    state: dict[str, str] = {}
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.2):
            if cancel is not None and cancel.is_set():
                kill_tree(proc.pid)
                return
    w = threading.Thread(target=watch, daemon=True)
    w.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            k, _, val = line.strip().partition("=")
            state[k] = val
            if k == "progress":
                say(*progress_text(label, _num(state.get("out_time_us")) / 1e6,
                                   _num((state.get("speed") or "").rstrip("x")), duration))
        code = proc.wait()
    finally:
        stop.set()
        w.join(2)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return code


def _num(s: str | None) -> float:
    try:
        return max(0.0, float(s))
    except (TypeError, ValueError):
        return 0.0


def progress_text(label: str, done_s: float, speed: float, duration: float | None) -> tuple[float, str]:
    """(fraction, 'converting to H.264 (CPU) 34 % -- 2.1x, about 3:12 left') from FFmpeg's progress (the media
    seconds written, its speed = media seconds per second)."""
    frac = min(0.99, done_s / duration) if duration else 0.0
    text = f"{label} {frac * 100:.0f} %"
    if speed > 0:
        text += f" -- {speed:.2g}x" + (f", about {_clock(max(0.0, duration - done_s) / speed)} left"
                                       if duration else "")
    return frac, text


DECODE_FIX_SUFFIX = ".reencoded"      # day 16: '<stem>.reencoded.mp4' -- a RAW whose frames could not be read


def reencode_for_decode(path: str | os.PathLike, out_dir: str | os.PathLike | None = None,
                        progress: Progress | None = None, cancel: threading.Event | None = None,
                        log_path: str | os.PathLike | None = None) -> ConvertResult:
    """Day 16, task 6: a RAW the tool can't read ("could not decode source frames ... by PTS": the Ted 2 x265 file)
    re-encoded ONCE -- NVENC (the CPU when there is none), 8-bit H.264, every frame on the next slot of its constant
    rate, its sound copied / AAC -- into ``<stem>.reencoded.mp4`` next to it (or ``out_dir``); the original is never
    deleted. A copy made before is used again."""
    src = Path(path)
    dest = Path(out_dir) if out_dir else src.parent
    done = dest / f"{src.stem}{DECODE_FIX_SUFFIX}.mp4"
    if done.is_file() and done.stat().st_size > 0:
        return ConvertResult(done, "encode", "h264_nvenc", None, {}, False, 0.0, "made before: used again",
                             original=src, size_in=src.stat().st_size, size_out=done.stat().st_size)
    return convert_file(src, dest, delete_original=False, progress=progress, cancel=cancel, gpu="on",
                        log_path=log_path, force_encode=True)


def convert_file(path: str | os.PathLike, out_dir: str | os.PathLike | None = None, delete_original: bool = True,
                 progress: Progress | None = None, cancel: threading.Event | None = None, gpu: str | bool = "auto",
                 log_path: str | os.PathLike | None = None, force_encode: bool = False) -> ConvertResult:
    """Make ``path`` Premiere-ready (module docstring): keep, rewrap or encode, CHECK, then (``delete_original``)
    delete the original and name the result ``<stem>.mp4``. ConvertError (plain words; the original kept) or
    Cancelled (the temp file removed). Day 16, ``force_encode`` (reencode_for_decode): always re-encoded, 8-bit,
    every frame re-stamped onto the constant rate, named ``<stem>.reencoded.mp4``."""
    t0 = time.time()
    src = Path(path)
    say = progress or (lambda f, t: None)
    log_f = None
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "a", encoding="utf-8", errors="replace")
    err_f = log_f if log_f is not None else tempfile.TemporaryFile()   # FFmpeg's own messages (why it failed)

    def log(text: str) -> None:
        if log_f is not None:                       # (night 11: the date and time on every line)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_f.write("".join(f"{stamp}  {ln}\n" for ln in text.rstrip("\n").split("\n")))
            log_f.flush()

    def ffmpeg_tail() -> str:
        try:
            if log_path:
                return Path(log_path).read_text(encoding="utf-8", errors="replace")[-3000:]
            err_f.seek(0)
            return err_f.read()[-3000:].decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""
    try:
        log(f"--- {datetime.now().isoformat(timespec='seconds')} convert {src}")
        say(0.0, "reading the video")
        info = inspect(src)
        pl = plan(info)
        if force_encode:
            audio = pl.audio or plan_audio(info)
            pl = Plan("encode", f"{NAMES.get(info.vcodec, info.vcodec)}: its frames can't be read by their time "
                                "stamps -- re-encoded once to H.264, every frame on the constant rate", audio=audio)
        log(f"in: {info.describe()} ({_size(info.size)})")
        log(f"plan: {pl.how} -- {pl.why}")
        if pl.how == "keep":
            say(1.0, "nothing to do: " + pl.why.split(":")[0])
            return ConvertResult(src, "keep", None, info.vcodec, {"frames": [info.frames, info.frames],
                                 "duration_s": [info.duration, info.duration]}, False, round(time.time() - t0, 1),
                                 pl.why, original=src, size_in=info.size, size_out=info.size)
        dest_dir = Path(out_dir) if out_dir else src.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = (dest_dir / f"{src.stem}{DECODE_FIX_SUFFIX}.mp4") if force_encode else \
            _target(src, dest_dir, delete_original)
        tmp = dest_dir / f"{src.stem}.converting.mp4"
        from . import space                       # day 13: the one place of every estimate -- or it does not start
        room = space.convert_problem(info.size, pl.how, dest_dir, src.name)
        if room:
            log(f"not started: {room}")
            raise ConvertError(room)
        start = 0.0
        try:
            r = _run([ffprobe_bin(), "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0",
                      str(src)], 120)
            start = float(r.stdout.strip() or 0.0)
        except (ValueError, subprocess.SubprocessError, OSError):
            pass
        say(0.0, "counting the frames")
        timing = video_timing(info, start)                 # the shown frames (an edit list may hide some)
        src_video_s = timing.duration if timing.duration else info.duration
        encoder, enc_args, enc_why, time_base = "copy", None, "copied", None
        if pl.how == "encode" and force_encode:             # 8-bit H.264 on NVENC, the frames re-stamped
            encoder, enc_args, enc_why = ("h264_nvenc", list(NVENC), "NVENC on the GPU") if nvenc_available() else \
                ("libx264", list(X264), "x264 on the CPU (no NVENC here)")
            time_base = grid_time_base(info.fps) if info.fps else "demux"
            log(f"encoder: {encoder} ({enc_why}); every decoded frame on the next slot of {time_base}")
        elif pl.how == "encode":
            encoder, enc_args, enc_why = choose_encoder(info, gpu)
            time_base = grid_time_base(info.fps) if timing.on_grid and info.fps else "demux"
            log(f"encoder: {encoder} ({enc_why}); frame times: "
                f"{'exact constant rate ' + time_base if time_base != 'demux' else 'kept as they are (variable)'}")
        label = "copying into MP4" if pl.how == "rewrap" else \
            f"converting to H.264 ({'GPU' if encoder == 'h264_nvenc' else 'CPU'})"
        cmd = ffmpeg_command(info, pl, tmp, enc_args, time_base, restamp=force_encode)
        log("command: " + subprocess.list2cmdline(cmd))
        if log_f is not None:
            log_f.flush()
        try:
            code = _run_ffmpeg(cmd, src_video_s, label, say, cancel, err_f)
            if code != 0 and encoder == "h264_nvenc":                     # the GPU failed: once more on the CPU
                log("NVENC failed -- once more with x264 on the CPU")
                encoder, enc_args, enc_why = "libx264", list(X264), "x264 on the CPU (NVENC failed)"
                label = "converting to H.264 (CPU)"
                cmd = ffmpeg_command(info, pl, tmp, enc_args, time_base, restamp=force_encode)
                log("command: " + subprocess.list2cmdline(cmd))
                code = _run_ffmpeg(cmd, src_video_s, label, say, cancel, err_f)
        except Cancelled:
            _unlink(tmp)
            log("stopped: the temp file removed, the original kept")
            raise
        if code != 0 or not tmp.is_file():
            _unlink(tmp)
            raise ConvertError(f"{_ffmpeg_problem(ffmpeg_tail(), code)} -- the original is kept")
        say(0.99, "checking the converted file")
        out = inspect(tmp)
        out_timing = video_timing(out, fps=info.fps)       # one frame added at the end on both: the original's
        ok, checks, probs = check(info, out, timing, out_timing, grid=time_base not in (None, "demux") and
                                  not force_encode, restamped=force_encode)
        log(f"out: {out.describe()} ({_size(out.size)})")
        log(f"check: {json.dumps(checks)}" + (f" -- PROBLEMS: {'; '.join(probs)}" if probs else " -- OK"))
        if not ok:
            _unlink(tmp)
            raise ConvertError(f"the converted file is not right ({'; '.join(probs)}): the original is kept and "
                               "the converted copy removed")
        deleted = False
        note_bits = [pl.why]
        if final.resolve() == src.resolve():
            os.replace(tmp, final)                                         # the original replaced in one step
            deleted = True
        else:
            os.replace(tmp, final)
            if delete_original:
                try:
                    src.unlink()
                    deleted = True
                except OSError as e:
                    note_bits.append(f"the original could not be deleted ({e.strerror or e}): delete it by hand")
        note_bits.append(f"{enc_why}" if pl.how == "encode" else "")
        res = ConvertResult(final, pl.how, encoder, info.vcodec, checks, deleted, round(time.time() - t0, 1),
                            "; ".join(b for b in note_bits if b), original=src, size_in=info.size,
                            size_out=final.stat().st_size)
        log(f"done: {final} ({_size(res.size_out)}, {res.seconds:.1f} s; original "
            f"{'deleted' if deleted else 'kept'})")
        say(1.0, f"{'converted' if pl.how == 'encode' else 'copied into MP4'} -- {_size(res.size_out)} in "
                 f"{_clock(res.seconds)}")
        return res
    except ConvertError as e:
        log(f"ERROR in plain words: {e}")
        raise
    finally:
        err_f.close()


def _unlink(p: Path, tries: int = 30) -> None:
    for _ in range(tries):
        try:
            p.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.1)


# ---------------------------------------------------------------------------------------------------------------------
# both files of a project
# ---------------------------------------------------------------------------------------------------------------------

def convert_project(project, progress: Callable[[str, float, str], None] | None = None,
                    cancel: threading.Event | None = None, gpu: str | bool = "auto",
                    roles: tuple[str, ...] | None = None) -> dict:
    """The project's competitor and RAW made Premiere-ready (convert_file, the originals deleted after the check);
    project.json gets the convert step (with what was done to each file) and the new file names.
    ``progress(role, fraction, text)``. Returns {role: ConvertResult}."""
    from . import projects as P
    out: dict = {}
    details: dict = {}
    project.set_step("convert", P.RUNNING)
    role = ""
    try:
        for role in P.ROLES:
            f = project.file(role)
            if f is None or (roles is not None and role not in roles):    # day 12: only these (Add a competitor)
                old = project.step("convert").get(role)
                if f is not None and isinstance(old, dict):
                    details[role] = old                                     # the one converted before stays said
                continue
            if not f.is_file():
                raise ConvertError(f"{f.name} is not in the project folder")
            say = (lambda fr, t, r=role: progress(r, fr, t)) if progress else None
            res = convert_file(f, delete_original=True, progress=say, cancel=cancel, gpu=gpu,
                               log_path=project.log_path("convert", role))
            out[role] = res
            project.update(**{role: {"file": res.path.name}})
            details[role] = {"how": res.how, "encoder": res.encoder, "from": res.from_codec, "file": res.path.name,
                             "checks": res.checks, "deleted_original": res.deleted_original,
                             "seconds": res.seconds, "note": res.note}
        c, r = out.get("competitor"), out.get("raw")
        cs = (c.checks.get("duration_s") or [None, None])[-1] if c else None
        rs = (r.checks.get("duration_s") or [None, None])[-1] if r else None
        if cs and rs and cs > rs:
            details["note"] = (f"the competitor ({_clock(cs)}) is longer than the RAW ({_clock(rs)}): the tool takes "
                               "the longer video as the RAW -- were the two swapped?")
        project.set_step("convert", P.DONE, **details)
        return out
    except Cancelled:
        project.set_step("convert", P.FAILED, error="stopped", **details)
        raise
    except ConvertError as e:
        project.set_step("convert", P.FAILED, error=f"{'RAW' if role == 'raw' else role}: {e}", **details)
        raise
