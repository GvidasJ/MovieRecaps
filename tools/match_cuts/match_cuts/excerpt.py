"""excerpt.py -- day 14: only the needed part of the movie.

A long RAW (a film: over MIN_RAW_S) whose quick sound check (S1, pair_check) found where the competitor's sound is gets
analysed in a PIECE: the found places +- MARGIN_S, cut out of the original by stream copy -- from a keyframe before
to a keyframe after, every frame as it is in the movie, nothing re-encoded -- with its sound decoded sample-exact to
PCM from the same moment. S2 and everything after it run on the piece (raw_piece.mov) instead of the whole movie (the
probe's decoding, the proxies, the sound, the picture search, the A1 sound file: minutes on a two-hour film).

What you get plays the ORIGINAL movie at the right frames (``finish``): 1_edit.xml and the edits kept in extras/ are
moved onto the movie -- its V1 clips play the movie (linked into extras/media as raw.<ext>) at <in> / <out> + the
piece's first frame in the movie, A1 plays raw_stereo.wav made to the movie's frames (exact silence before the piece,
then the piece's sound) -- and the piece is deleted. cutlist.json, the report and every check keep the piece's own
frames (they are the analysis'); extras/excerpt.json says where the piece is in the movie (``to_movie``).

The whole movie, as before, when: the RAW is shorter than MIN_RAW_S, it is not a Premiere run, the sound check did not
find the place (or was skipped), the piece would be more than MAX_SHARE of the movie, or the movie cannot be cut this
way -- a container / codec / picture Premiere does not play as it is, frames not one step apart (a variable rate,
scrambled timestamps, frames an edit list hides), no keyframe to start from -- or the piece does not show the movie's
frames (checked at three places). Exact at another --speed too: the piece starts on a frame number the speed's
numerator divides (a 125 % clip's <in> counts the movie in steps of 1.25 frames).

One piece covers every found place (from the first one's -MARGIN_S to the last one's +MARGIN_S); places too far apart
for that (more than MAX_SHARE of the movie in all) make the run use the whole movie.

Night 15 (task 8): a TV episode (a RAW of MIN_RAW_S to EPISODE_MAX_S) is cut too, with its own numbers: the found
places +- EPISODE_MARGIN_S, the whole episode only when that is more than EPISODE_MAX_SHARE of it (``rules``).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .common import ffmpeg_bin, ffprobe_bin, fps_str, log

MIN_RAW_S = 15 * 60.0           # a RAW shorter than this is analysed whole
MARGIN_S = 5 * 60.0             # the piece: every found place +- this
MAX_SHARE = 0.40                # a piece longer than this share of the movie: the whole movie instead
EPISODE_MAX_S = 70 * 60.0       # night 15 (task 8): a RAW up to this long is a TV episode: its piece is the found places
EPISODE_MARGIN_S = 2 * 60.0     #   +- this, and the whole episode only when that is more than EPISODE_MAX_SHARE of it
EPISODE_MAX_SHARE = 0.60
STEP_FRAMES = 0.1               # every frame one frame after the one before (probe's VFR rule for one step) ...
DRIFT_FRAMES = 0.5              # ... and every frame within half a frame of its slot (pipeline.steady_timing)
SOUND_RATE = 48000
PIECE_NAME = "raw_piece.mov"
INFO_NAME = "excerpt.json"
CHECK_SSIM = 0.97               # the piece's frame and the movie's at the same place: this alike (gray, <= 480 px) ...
PLAYABLE = {"h264": ("yuv420p", "yuvj420p", "yuv420p10le"), "hevc": ("yuv420p", "yuvj420p", "yuv420p10le")}
CONTAINERS = ("mp4", "mov", "m4v")


# ---------------------------------------------------------------------------------------------------------------------
# the movie, as ffprobe sees it (no decoding)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Movie:
    """What cutting a piece needs to know of the original RAW. ``why``: '' = it can be cut, else why not."""
    path: str
    why: str = ""
    container: str = ""
    vcodec: str = ""
    pix_fmt: str = ""
    vindex: int = 0                 # the video stream's ordinal among the video streams
    width: int = 0
    height: int = 0
    fps: str = ""                   # nominal rate (Fraction text)
    tb: str = ""                    # the video stream's time base
    frames: int = 0                 # frames (packets that are shown)
    first_pts: int = 0              # the first shown frame's PTS (time base ticks)
    duration: float = 0.0           # frames / fps
    keyframes: list = field(default_factory=list)      # [(display index, pts, dts)] of every keyframe
    has_audio: bool = False
    a_codec: str = ""
    a_channels: int = 0
    a_start: float = 0.0            # the sound's first sample, seconds after the first shown frame
    step: float = 0.0               # max |frame step - 1| (frames)
    drift: float = 0.0              # max |frame - its slot| (frames)

    @property
    def rate(self) -> Fraction:
        return Fraction(self.fps)


def _ffprobe_json(path: str) -> dict:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {Path(path).name}: {(r.stderr or '').strip()[-300:]}")
    return json.loads(r.stdout or "{}")


def _packets(path: str, vindex: int) -> list[tuple[int, int, str]]:
    """(pts, dts, flags) of every video packet in file order (ffprobe, no decoding: seconds even on a film)."""
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", f"v:{int(vindex)}", "-show_entries",
                        "packet=pts,dts,flags", "-of", "csv=p=0", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe could not list the packets of {Path(path).name}")
    out = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3:
            continue
        try:
            pts = int(parts[0]) if parts[0] not in ("", "N/A") else None
            dts = int(parts[1]) if parts[1] not in ("", "N/A") else None
        except ValueError:
            pts = dts = None
        if pts is None or dts is None:
            out.append((None, None, parts[2]))          # (cannot be cut on a timestamp)
            continue
        out.append((pts, dts, parts[2]))
    return out


def movie_facts(path: str | os.PathLike) -> Movie:
    """The original RAW's facts for a piece (``Movie.why`` says when it cannot be cut). Never raises."""
    from . import probe as P
    path = str(path)
    m = Movie(path=path)
    try:
        js = _ffprobe_json(path)
        streams = js.get("streams") or []
        vids = [s for s in streams if s.get("codec_type") == "video"
                and not int((s.get("disposition") or {}).get("attached_pic") or 0)]
        if not vids:
            m.why = "no video stream"
            return m
        v = vids[0]
        m.vindex = [s for s in streams if s.get("codec_type") == "video"].index(v)
        m.container = P._container_name(js.get("format") or {}, path)
        m.vcodec, m.pix_fmt = str(v.get("codec_name") or ""), str(v.get("pix_fmt") or "")
        m.width, m.height = int(v.get("width") or 0), int(v.get("height") or 0)
        rot, _notes = P._rotation_cw(v)
        sar = P._frac(v.get("sample_aspect_ratio"))
        tb = P._frac(v.get("time_base"))
        m.tb = fps_str(tb)
        if m.container not in CONTAINERS:
            m.why = f"Premiere plays .{m.container or '?'} files only after a conversion"
        elif m.vcodec not in PLAYABLE or m.pix_fmt not in PLAYABLE[m.vcodec]:
            m.why = f"{m.vcodec or '?'} {m.pix_fmt or '?'} video is not played by Premiere as it is"
        elif rot % 360 or (sar and sar != 1) or m.width % 2 or m.height % 2:
            m.why = "a rotated, stretched or odd-sized picture"
        if m.why:
            return m
        pk = _packets(path, m.vindex)
        if not pk or any(p[0] is None for p in pk):
            m.why = "video packets without timestamps"
            return m
        if any("D" in p[2] for p in pk):
            m.why = "an edit list hides some of its frames"
            return m
        pts = np.array([p[0] for p in pk], dtype=np.int64)
        dts = np.array([p[1] for p in pk], dtype=np.int64)
        if int(v.get("has_b_frames") or 0) > 0 and len(pts) > 2 and bool(np.all(pts == dts)):
            m.why = "its timestamps are scrambled (PTS = DTS with B-frames)"
            return m
        order = np.argsort(pts, kind="stable")
        sp = pts[order]
        if len(sp) < 2 or np.any(np.diff(sp) <= 0):
            m.why = "two frames share a timestamp"
            return m
        rel = (sp - sp[0]).astype(np.float64) * float(tb)
        fps, _why = P.nominal_fps(P._frac(v.get("avg_frame_rate")), P._frac(v.get("r_frame_rate")), rel)
        f = float(fps)
        m.step = float(np.max(np.abs(np.diff(rel) * f - 1.0)))
        m.drift = float(np.max(np.abs(rel * f - np.arange(len(rel)))))
        if not (m.step < STEP_FRAMES and m.drift < DRIFT_FRAMES):
            m.why = (f"its frames are not one step apart (steps off by up to {m.step:.2f} frames, drift "
                     f"{m.drift:.2f} frames)")
            return m
        m.fps, m.frames, m.first_pts = fps_str(fps), int(len(sp)), int(sp[0])
        m.duration = m.frames / f
        # display index of each keyframe: its place among the sorted PTS
        kidx = [i for i, p in enumerate(pk) if "K" in p[2]]
        m.keyframes = [(int(np.searchsorted(sp, pts[i])), int(pts[i]), int(dts[i])) for i in kidx]
        auds = [s for s in streams if s.get("codec_type") == "audio"]
        if auds:
            a = auds[0]
            m.has_audio, m.a_codec, m.a_channels = True, str(a.get("codec_name") or ""), int(a.get("channels") or 0)
            a0 = P._first_audio_time(path)
            if a0 is None:
                a0 = float(a.get("start_time") or 0.0)
            m.a_start = float(a0) - float(sp[0] * tb)
    except Exception as e:  # noqa: BLE001 - the whole movie then, as before
        m.why = f"it could not be read for a piece ({type(e).__name__}: {e})"
    return m


# ---------------------------------------------------------------------------------------------------------------------
# where the piece goes
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Plan:
    """The piece in the movie: frames [k0, k1) -- k0 a keyframe's display index (a multiple of ``step``), k1 the next
    piece-ending keyframe's (or the end of the movie); ``dts_end``: that keyframe's DTS (the cut is made on it)."""
    k0: int
    k1: int
    pts0: int
    dts_end: int | None
    places: list
    why: str = ""

    def seconds(self, fps: Fraction) -> tuple[float, float]:
        return self.k0 / float(fps), self.k1 / float(fps)


def speed_step(speed: float) -> int:
    """The piece's first frame is a multiple of this (the numerator of the speed in lowest terms: 1.25 -> 5): a
    clip's <in> on the retimed scale then moves by a whole number."""
    s = Fraction(str(round(float(speed or 1.0), 4))).limit_denominator(10000)
    return max(1, int(abs(s.numerator))) if s != 0 else 1


def rules(duration: float) -> tuple[float, float, str]:
    """(margin s, max share, "episode" | "movie") for a RAW this long (night 15: episodes +- 2 min, up to 60 %)."""
    if duration and duration <= EPISODE_MAX_S:
        return EPISODE_MARGIN_S, EPISODE_MAX_SHARE, "episode"
    return MARGIN_S, MAX_SHARE, "movie"


def plan_piece(m: Movie, places: list[float], speed: float = 1.0) -> Plan | None:
    """The piece around the found ``places`` (RAW seconds where the competitor's sound pieces were found; each piece
    WIN_S long) -- None with the reason logged when the whole movie is used instead."""
    from .pair_check import WIN_S
    fps = m.rate
    f = float(fps)
    if not places:
        return None
    margin, share, kind = rules(m.duration)
    lo = max(0.0, min(places) - margin)
    hi = min(m.duration, max(places) + WIN_S + margin)
    if hi - lo > share * m.duration:
        log.info("piece of the %s: the found places span %.1f of %.1f min (+- %.0f min) -- more than %.0f %%: the "
                 "whole %s", kind, (max(places) - min(places)) / 60, m.duration / 60, margin / 60, 100 * share, kind)
        return None
    step = speed_step(speed)
    want0 = int(math.floor(lo * f))
    starts = [k for k in m.keyframes if k[0] <= want0 and k[0] % step == 0]
    if not starts:
        starts = [k for k in m.keyframes if k[0] <= want0 + int(60 * f) and k[0] % step == 0]   # (a minute later)
    if not starts:
        log.info("piece of the movie: no keyframe to start from (every %d frames) -- the whole movie", step)
        return None
    k0, pts0, _dts0 = max(starts)
    want1 = int(math.ceil(hi * f))
    ends = [k for k in m.keyframes if k[0] >= want1]
    if ends:
        k1, _p1, dts_end = min(ends)
    else:
        k1, dts_end = m.frames, None
    if (k1 - k0) / max(1, m.frames) > share:
        log.info("piece of the %s: %d of %d frames between keyframes -- more than %.0f %%: the whole %s",
                 kind, k1 - k0, m.frames, 100 * share, kind)
        return None
    return Plan(int(k0), int(k1), int(pts0), dts_end, sorted(float(x) for x in places))


# ---------------------------------------------------------------------------------------------------------------------
# cutting it
# ---------------------------------------------------------------------------------------------------------------------

def copy_packets(m: Movie, plan: Plan, dst: Path) -> int:
    """The piece's picture: the movie's video packets in decoding order from the plan's keyframe up to (not with) the
    keyframe that ends it -- copied as they are (no seek of ffmpeg's: its stream-copy seek hides frames before the
    seek point with an edit list), their times moved so the keyframe is shown at 0. Returns the packets written."""
    import av
    n = 0
    with av.open(m.path) as inp:
        vin = inp.streams.video[m.vindex]
        inp.seek(int(plan.pts0), stream=vin, backward=True, any_frame=False)
        with av.open(str(dst), mode="w", format="mov") as out:
            try:
                vout = out.add_stream_from_template(vin)
            except AttributeError:                    # (older PyAV)
                vout = out.add_stream(template=vin)
            first = True
            for pkt in inp.demux(vin):
                if pkt.dts is None or pkt.pts is None:
                    if pkt.size == 0:
                        continue                      # (the demuxer's flush packet)
                    raise RuntimeError("a video packet without a timestamp")
                if first:
                    if int(pkt.pts) != int(plan.pts0) or not pkt.is_keyframe:
                        raise RuntimeError(f"the seek landed on {pkt.pts}, not on the keyframe {plan.pts0}")
                    first = False
                if plan.dts_end is not None and int(pkt.dts) >= int(plan.dts_end):
                    break
                pkt.pts = int(pkt.pts) - int(plan.pts0)
                pkt.dts = int(pkt.dts) - int(plan.pts0)
                pkt.stream = vout
                out.mux(pkt)
                n += 1
    if n == 0:
        raise RuntimeError("no video packet copied")
    return n


def cut_piece(m: Movie, plan: Plan, dst: str | os.PathLike) -> dict:
    """``dst`` (.mov): the movie's video packets from the plan's keyframe up to the next one in decoding order (stream
    copy: the same frames), and its sound decoded to 16-bit PCM 48 kHz, sample 0 at the piece's first frame (decoded
    from the movie's start and cut by counting samples: exact). Returns {path, seconds, bytes}; raises on failure."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fps = m.rate
    t0 = time.perf_counter()
    vtmp = dst.with_name(dst.stem + ".video.part.mov")
    try:
        copy_packets(m, plan, vtmp)
    except Exception:
        vtmp.unlink(missing_ok=True)
        raise
    parts = [vtmp]
    atmp = None
    if m.has_audio:
        # the movie's sample at the piece's first frame: (frame time - the sound's start) x 48 kHz, counted from the
        # first decoded sample (no seek: a seek in AAC lands samples off)
        n = plan.k1 - plan.k0
        s0 = int(round((plan.k0 / float(fps) - m.a_start) * SOUND_RATE))
        s1 = s0 + int(round(n / float(fps) * SOUND_RATE))
        chain = [f"aresample={SOUND_RATE}", "asetpts=PTS-STARTPTS"]
        if s0 < 0:
            chain += [f"adelay=delays={-s0}S:all=1"]
            s1 -= s0
            s0 = 0
        chain += [f"atrim=start_sample={s0}:end_sample={s1}", "asetpts=PTS-STARTPTS"]
        atmp = dst.with_name(dst.stem + ".audio.part.wav")
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", m.path, "-map",
                            "0:a:0", "-vn", "-sn", "-dn", "-af", ",".join(chain), "-c:a", "pcm_s16le", "-ar",
                            str(SOUND_RATE), "-rf64", "auto", "-map_metadata", "-1", "-fflags", "+bitexact",
                            str(atmp)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not atmp.is_file():
            vtmp.unlink(missing_ok=True)
            atmp.unlink(missing_ok=True)
            raise RuntimeError(f"the piece's sound could not be made: {(r.stderr or '').strip()[-300:]}")
        parts.append(atmp)
    tmp = dst.with_name(dst.stem + ".part.mov")
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(vtmp)]
    if atmp is not None:
        cmd += ["-i", str(atmp), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-c", "copy", "-map_metadata", "-1", "-fflags", "+bitexact", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for p in parts:
        p.unlink(missing_ok=True)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"the piece could not be written: {(r.stderr or '').strip()[-300:]}")
    os.replace(tmp, dst)
    return {"path": str(dst), "seconds": round(time.perf_counter() - t0, 2), "bytes": int(dst.stat().st_size)}


def same_frames(piece: str, m: Movie, k0: int, n: int, points: int = 3) -> dict:
    """The piece's frame j and the movie's frame k0 + j look alike at ``points`` places (start, middle, end): gray
    SSIM at <= 480 px above CHECK_SSIM and above the movie's neighbouring frames. {ok, rows, why}."""
    import cv2
    from .conform import ssim
    from .media import VideoReader
    js = sorted({0, max(0, n // 2), max(0, n - 2)})[:points]
    rows, bad = [], []
    with VideoReader(piece, fps=m.rate) as rp, VideoReader(m.path, fps=m.rate, stream_index=m.vindex) as ro:
        fp = rp.get_many(js, fmt="gray")
        fo = ro.get_many(sorted({k0 + j + d for j in js for d in (-1, 0, 1) if k0 + j + d >= 0}), fmt="gray")
    for j in js:
        a, b = fp.get(j), fo.get(k0 + j)
        if a is None or b is None:
            bad.append(f"frame {j} could not be read")
            continue
        h, w = a.shape[:2]
        f = min(1.0, 480.0 / max(h, w))
        size = (max(8, int(round(w * f))), max(8, int(round(h * f))))
        A = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
        s = ssim(A, cv2.resize(b, size, interpolation=cv2.INTER_AREA))
        nb = [ssim(A, cv2.resize(fo[k], size, interpolation=cv2.INTER_AREA)) for k in (k0 + j - 1, k0 + j + 1)
              if fo.get(k) is not None]
        rows.append({"j": j, "k": k0 + j, "ssim": round(s, 4), "neighbours": [round(x, 4) for x in nb]})
        if s < CHECK_SSIM or any(x > s + 0.003 for x in nb):     # (a neighbour clearly closer: one frame off)
            bad.append(f"piece frame {j} is not the movie's frame {k0 + j} (SSIM {s:.3f}, its neighbours "
                       f"{', '.join(f'{x:.3f}' for x in nb)})")
    return {"ok": not bad, "rows": rows, "why": "; ".join(bad)}


# ---------------------------------------------------------------------------------------------------------------------
# the run's piece
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Excerpt:
    """The piece a run analyses: its file, the movie, where it is in the movie (frames [k0, k0 + frames))."""
    piece: str
    movie: str
    k0: int
    frames: int
    fps: str
    movie_frames: int
    movie_seconds: float
    places: list
    cut_seconds: float = 0.0
    check: dict = field(default_factory=dict)
    sound: str = ""                  # 'wav': A1 plays a stereo WAV (the movie's own sound would need one too)

    @property
    def t0(self) -> float:
        return self.k0 / float(Fraction(self.fps))

    def to_dict(self) -> dict:
        return asdict(self)


def applies(cfg: Any, pair: Any) -> str:
    """'' when a run may use a piece, else why not (plain words, for the log)."""
    if not getattr(cfg, "premiere", False):
        return "not a Premiere run"
    if getattr(cfg, "no_excerpt", False) or os.environ.get("MATCH_CUTS_NO_PIECE", "").strip() not in ("", "0"):
        return "switched off"
    if pair is None or getattr(pair, "sound", None) is None:
        return "the sound check did not run"
    if getattr(pair.sound, "state", "") != "found" or not list(getattr(pair.sound, "where", None) or []):
        return "the sound check did not find the place"
    return ""


def prepare(ctx: Any, pair: Any) -> Excerpt | None:
    """After S1: cut the piece and make the run analyse it (``cfg.raw`` = the piece; ``ctx.excerpt``), or None -- the
    whole movie, as before (why: logged). Never raises."""
    cfg = ctx.cfg
    why = applies(cfg, pair)
    if why:
        log.info("piece of the movie: no (%s) -- the whole RAW is analysed", why)
        return None
    raw = str(cfg.raw)
    try:
        dur = float((getattr(pair, "inputs", None) or {}).get("raw", {}).get("duration") or 0.0)
    except (TypeError, ValueError, AttributeError):
        dur = 0.0
    places = [float(x) for x in pair.sound.where]
    if dur and dur < MIN_RAW_S:                       # (the quick answers first: no look at the packets)
        log.info("piece of the movie: no (the RAW is %.1f min, under %.0f) -- the whole RAW is analysed", dur / 60,
                 MIN_RAW_S / 60)
        return None
    margin, share, _kind = rules(dur)
    if dur and (max(places) - min(places)) + 2 * margin > share * dur:
        log.info("piece of the movie: no (the found places +- %.0f min are more than %.0f %% of the RAW's %.1f min) "
                 "-- the whole RAW is analysed", margin / 60, 100 * share, dur / 60)
        return None
    t0 = time.perf_counter()
    m = movie_facts(raw)
    if m.why:
        log.info("piece of the movie: no -- %s (the whole RAW is analysed)", m.why)
        return None
    if (dur or m.duration) < MIN_RAW_S:
        log.info("piece of the movie: no (the RAW is %.1f min, under %.0f) -- the whole RAW is analysed",
                 (dur or m.duration) / 60, MIN_RAW_S / 60)
        return None
    plan = plan_piece(m, places, float(getattr(cfg, "premiere_speed", 1.0) or 1.0))
    if plan is None:
        return None
    dst = Path(cfg.work) / "excerpt" / PIECE_NAME
    try:
        made = cut_piece(m, plan, dst)
        n = _frames_in(dst)
        if n <= 0 or n > plan.k1 - plan.k0:
            raise RuntimeError(f"the piece has {n} frames, the plan {plan.k1 - plan.k0}")
        chk = same_frames(str(dst), m, plan.k0, n)
        if not chk["ok"]:
            raise RuntimeError(chk["why"])
    except Exception as e:  # noqa: BLE001 - the whole movie then
        log.warning("piece of the movie: no -- %s (the whole RAW is analysed)", e)
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    sound = "wav" if (m.has_audio and not (m.a_codec == "aac" and m.a_channels <= 2 and abs(m.a_start) < 1e-3)) \
        else ""
    ex = Excerpt(str(dst), raw, plan.k0, n, m.fps, m.frames, round(m.duration, 3), plan.places,
                 round(time.perf_counter() - t0, 2), chk, sound or ("wav" if m.has_audio else ""))
    a, b = plan.seconds(m.rate)
    log.info("piece of the movie: %s-%s of %s (%.1f of %.1f min, %d frames from frame %d) cut by stream copy in "
             "%.1f s, its frames checked against the movie at %d places -- the run analyses the piece; 1_edit.xml "
             "will play the movie itself", _clock(a), _clock(a + n / float(m.rate)), _clock(m.duration),
             n / float(m.rate) / 60, m.duration / 60, n, plan.k0, ex.cut_seconds, len(chk.get("rows") or []))
    try:
        ctx.dlog.record("inputs", "excerpt", **{k: v for k, v in ex.to_dict().items() if k != "check"},
                        check=chk.get("rows"), made=made)
    except Exception:  # noqa: BLE001
        pass
    cfg.raw_movie = raw
    cfg.raw = str(dst)
    cfg.excerpt_sound = ex.sound
    ctx.excerpt = ex
    return ex


def _frames_in(path: str | os.PathLike) -> int:
    from . import stereo
    return int(stereo._frames(path)[0])


# ---------------------------------------------------------------------------------------------------------------------
# a run made on a piece, read as if the whole movie had been analysed (Learn, the app)
# ---------------------------------------------------------------------------------------------------------------------

def run_info(run_dir: str | os.PathLike) -> dict | None:
    """extras/excerpt.json of a run folder (``run_dir``: the run folder or its extras/), only when its edit was moved
    onto the movie; None otherwise."""
    d = Path(run_dir)
    for p in (d / "extras" / INFO_NAME, d / INFO_NAME):
        if p.is_file():
            try:
                info = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return info if isinstance(info, dict) and (info.get("moved") or {}).get("moved") else None
    return None


def movie_cutlist(cl: dict, run_dir: str | os.PathLike) -> dict:
    """A run's cut list (cutlist.json: the piece's frames) as if the whole movie had been analysed -- every RAW
    frame + k0, every RAW second + k0 / fps, the RAW block the movie (extras/media/raw.<ext>, its frames and length)
    -- for a run made on a piece (``run_info``); the cut list itself otherwise. What 1_edit.xml plays is then what
    the cut list says (Learn compares your project with both)."""
    import copy
    info = run_info(run_dir)
    if not info or not isinstance(cl, dict) or not cl:
        return cl
    k0 = int(info.get("k0") or 0)
    t0 = k0 / float(Fraction(str(info.get("fps") or "1")))
    out = copy.deepcopy(cl)
    movie = Path(str(info.get("movie") or ""))
    name = "raw" + (movie.suffix.lower() or ".mp4")
    raw = out.setdefault("raw", {})
    media = Path(run_dir) / ("extras" if (Path(run_dir) / "extras").is_dir() else "") / "media" / name
    raw.update(frames=int(info.get("movie_frames") or raw.get("frames") or 0),
               duration_s=float(info.get("movie_seconds") or raw.get("duration_s") or 0.0),
               file=f"media/{name}", file_rel=f"media/{name}", file_abs=str(media), source_path=str(movie),
               excerpt={"k0": k0, "frames": int(info.get("frames") or 0)})
    for s in out.get("segments") or []:
        for key in ("raw_in_frame", "raw_out_frame"):
            if s.get(key) is not None:
                s[key] = int(s[key]) + k0
        if s.get("raw_in_seconds") is not None:
            s["raw_in_seconds"] = float(s["raw_in_seconds"]) + t0
        for kk in s.get("time_remap_keys") or []:
            if isinstance(kk, dict) and kk.get("raw_seconds") is not None:
                kk["raw_seconds"] = float(kk["raw_seconds"]) + t0
        for key in ("raw_in_interval", "raw_in_interval_both"):
            if s.get(key) and len(s[key]) == 2:
                s[key] = [float(s[key][0]) + t0, float(s[key][1]) + t0]
        for e in s.get("evidence") or []:
            if isinstance(e, dict) and e.get("raw") is not None and int(e["raw"]) >= 0:
                e["raw"] = int(e["raw"]) + k0
    return out


# ---------------------------------------------------------------------------------------------------------------------
# what the piece did not show: looked for in the rest of the movie
# ---------------------------------------------------------------------------------------------------------------------

REST_MIN_S = 3.0                # a stretch the piece did not show, at least this long (one sound piece), is looked for


def rest_check(ctx: Any) -> dict:
    """After a run on a piece: the competitor's stretches it did not show (NOT-IN-RAW, uncertain; at least REST_MIN_S)
    looked for in the WHOLE movie by their sound (pair_check's sound check on that stretch). {looked, found: [{comp,
    movie_at}], seconds}; ``found`` non-empty -- the sound is in the movie outside the piece: the run is made again on
    the whole movie (pipeline.run). Never raises (nothing found then)."""
    ex = getattr(ctx, "excerpt", None)
    cl = getattr(ctx, "cutlist", None)
    out: dict[str, Any] = {"looked": 0, "found": []}
    if ex is None or cl is None:
        return out
    t0 = time.perf_counter()
    try:
        cf = float(cl.comp_fps)
        spans = [(s.comp_in / cf, s.comp_out / cf) for s in cl.segments
                 if s.type in ("not_in_raw", "uncertain") and (s.comp_out - s.comp_in) / cf >= REST_MIN_S]
        if not spans:
            return out
        from . import pair_check as PC
        comp_y = PC.decode_audio(ctx.cfg.competitor, float(cl.competitor.get("frames") or 0) / cf)
        raw_y = PC.decode_audio(ex.movie, ex.movie_seconds)
        if comp_y is None or raw_y is None:
            out["why"] = "no sound to look with"
            return out
        lo, hi = ex.t0 - 1.0, ex.t0 + ex.frames / float(Fraction(ex.fps)) + 1.0
        for a, b in spans:
            seg = comp_y[int(a * PC.SR):int(b * PC.SR)]
            out["looked"] += 1
            side = PC.sound_check(seg, raw_y)
            outside = [float(t) for t in side.where if not lo <= float(t) <= hi]
            if side.found and outside:
                out["found"].append({"comp": [round(a, 2), round(b, 2)], "movie_at": outside[:4]})
    except Exception as e:  # noqa: BLE001 - nothing found then
        out["why"] = f"{type(e).__name__}: {e}"
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def drop_piece(cfg: Any) -> None:
    """The whole movie from now on (``cfg.raw`` the movie again); the piece deleted (and the run folder's link of it,
    when it is one)."""
    movie = getattr(cfg, "raw_movie", None)
    if not movie:
        return
    piece = Path(str(cfg.raw))
    cfg.raw, cfg.raw_movie, cfg.no_excerpt, cfg.excerpt_sound = movie, None, True, ""
    try:
        media = Path(cfg.media_dir)
        for p in media.glob("raw.*") if media.is_dir() else []:
            try:
                if piece.exists() and os.path.samefile(p, piece):
                    p.unlink()
            except OSError:
                pass
        piece.unlink(missing_ok=True)
    except OSError as e:
        log.warning("piece of the movie: %s could not be deleted (%s)", piece, e)


# ---------------------------------------------------------------------------------------------------------------------
# the end of the run: what you get plays the movie
# ---------------------------------------------------------------------------------------------------------------------

RAW_IDS = ("file-raw", "file-raw-sound")


def _rate(el: Any) -> Fraction | None:
    """<rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate> -> 24000/1001."""
    if el is None or el.find("timebase") is None:
        return None
    tb = int(el.findtext("timebase") or 0)
    return Fraction(tb * 1000, 1001) if str(el.findtext("ntsc") or "").upper() == "TRUE" else Fraction(tb)


def _clip_speed(ci: Any) -> float:
    """A clip item's Time Remap speed as a factor (1.0 without one; export_xml_edl._remap_factor's rounding)."""
    from .export_xml_edl import _remap_factor
    for e in ci.iter("effect"):
        if (e.findtext("effectid") or "") != "timeremap":
            continue
        for p in e.findall("parameter"):
            if (p.findtext("parameterid") or "") == "speed":
                try:
                    return _remap_factor(float(p.findtext("value") or 100.0) / 100.0)
                except ValueError:
                    return 1.0
    return 1.0


def shift_xml(src: str | os.PathLike, dst: str | os.PathLike, ex: Excerpt, movie_media: Path, movie_name: str,
              wav_frames: int | None) -> dict:
    """``src``'s edit moved onto the movie, written to ``dst``: every clip item of the RAW (V1's picture, A1's
    sound) at <in> / <out> (and its keyframes' <when>) + the piece's first frame in the movie -- on the clip's own
    scale (a 125 % clip counts 1.25 frames a step); the picture's <file> is the movie (its name, path, length), the
    sound's its length. Returns {items, exact, shifts}."""
    import xml.etree.ElementTree as ET
    from .common import atomic_write_text
    from .export_xml_edl import _file_url
    raw_fps = Fraction(ex.fps)
    root = ET.parse(str(src)).getroot()
    for fe in root.iter("file"):
        fid = fe.get("id")
        if fid not in RAW_IDS or fe.find("pathurl") is None:
            continue
        rate = _rate(fe.find("rate")) or raw_fps
        frames = ex.movie_frames if fid == "file-raw" else (wav_frames if wav_frames is not None else ex.movie_frames)
        if fe.find("duration") is not None:
            fe.find("duration").text = str(int(math.floor(frames * float(rate) / float(raw_fps))))
        if fid == "file-raw":
            fe.find("pathurl").text = _file_url(str(movie_media.resolve()))
            if fe.find("name") is not None:
                fe.find("name").text = movie_name
    items, exact, shifts = 0, True, set()
    for ci in root.iter("clipitem"):
        f = ci.find("file")
        if f is None or f.get("id") not in RAW_IDS:
            continue
        rate = _rate(ci.find("rate")) or raw_fps
        s = _clip_speed(ci)
        d = Fraction(int(ex.k0)) * rate / raw_fps / Fraction(str(s))
        if d.denominator != 1:
            exact = False
        di = int(round(d))
        shifts.add(di)
        for tag in ("in", "out"):
            el = ci.find(tag)
            if el is not None and el.text is not None and int(el.text) >= 0:
                el.text = str(int(el.text) + di)
        for kf in ci.iter("keyframe"):
            w = kf.find("when")
            if w is not None and w.text is not None:
                w.text = str(int(w.text) + di)
        frames = ex.movie_frames if f.get("id") == "file-raw" else (wav_frames if wav_frames is not None
                                                                     else ex.movie_frames)
        media_len = int(math.floor(frames * float(rate) / float(raw_fps)))
        if ci.find("duration") is not None:
            ci.find("duration").text = str(media_len if s == 1.0 else int(math.floor(media_len / s)))
        if f.get("id") == "file-raw" and ci.find("name") is not None:
            ci.find("name").text = movie_name
        items += 1
    ET.indent(root, space="  ")
    atomic_write_text(Path(dst), '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' +
                      ET.tostring(root, encoding="unicode") + "\n")
    return {"items": items, "exact": exact, "shifts": sorted(shifts)}


def movie_wav(piece_wav: Path, dst: Path, ex: Excerpt) -> tuple[int, Path]:
    """The movie's A1 sound, made beside ``dst`` (``<dst>.part.wav``; the caller moves it into place): exact silence
    up to the piece's first frame (k0 frames at the movie's rate), then the piece's stereo sound file as it is.
    Returns (its length in samples, the file)."""
    lead = int(round(ex.k0 * SOUND_RATE / float(Fraction(ex.fps))))
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    fmt = f"aformat=sample_fmts=s16:channel_layouts=stereo:sample_rates={SOUND_RATE}"   # (the piece's samples as
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",  # they are)
                        f"anullsrc=r={SOUND_RATE}:cl=stereo", "-i", str(piece_wav), "-filter_complex",
                        f"[0:a]{fmt},atrim=end_sample={lead}[s];[1:a]{fmt}[p];[s][p]concat=n=2:v=0:a=1[a]", "-map",
                        "[a]", "-c:a",
                        "pcm_s16le", "-ar", str(SOUND_RATE), "-ac", "2", "-rf64", "auto", "-map_metadata", "-1",
                        "-fflags", "+bitexact", str(tmp)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"the movie's sound file could not be made: {(r.stderr or '').strip()[-300:]}")
    from .edit_media import wav_info
    got = wav_info(tmp)
    want = lead + int(wav_info(piece_wav).get("samples") or 0)
    if int(got.get("samples") or 0) != want:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"the movie's sound file has {got.get('samples')} samples, not {want}")
    return want, tmp


def check_moved(before: Path, after: Path, ex: Excerpt, piece: str, points: int = 3) -> dict:
    """The moved edit shows the movie's frame where the piece's showed: for up to ``points`` V1 clips of the RAW,
    the piece's frame at the old <in> and the movie's at the new one (gray SSIM, as same_frames). {ok, rows, why}."""
    import cv2
    import xml.etree.ElementTree as ET
    from .conform import ssim
    from .media import VideoReader
    raw_fps = Fraction(ex.fps)

    def v1_items(p: Path) -> list[tuple[str, int, Fraction, float]]:
        root = ET.parse(str(p)).getroot()
        out = []
        video = root.find("sequence/media/video")
        for tr in (video.findall("track") if video is not None else []):
            for ci in tr.findall("clipitem"):
                f = ci.find("file")
                if f is None or f.get("id") != "file-raw" or ci.find("in") is None:
                    continue
                out.append((ci.get("id"), int(ci.findtext("in")), _rate(ci.find("rate")) or raw_fps, _clip_speed(ci)))
        return out

    a, b = v1_items(before), v1_items(after)
    if [x[0] for x in a] != [x[0] for x in b]:
        return {"ok": False, "rows": [], "why": "the moved edit has other clips"}
    pick = [i for i in sorted({0, len(a) // 2, len(a) - 1}) if 0 <= i < len(a)][:points]
    rows, bad = [], []
    jobs = []
    for i in pick:
        cid, x0, rate, s = a[i]
        _cid, x1, _r, _s = b[i]
        j = int(math.floor(float(Fraction(x0) * Fraction(str(s)) * raw_fps / rate) + 1e-6))
        k = int(math.floor(float(Fraction(x1) * Fraction(str(s)) * raw_fps / rate) + 1e-6))
        jobs.append((cid, j, k))
    with VideoReader(piece, fps=raw_fps) as rp, VideoReader(ex.movie, fps=raw_fps) as rm:
        fp = rp.get_many([j for _c, j, _k in jobs], fmt="gray")
        fm = rm.get_many([k for _c, _j, k in jobs], fmt="gray")
    for cid, j, k in jobs:
        x, y = fp.get(j), fm.get(k)
        if x is None or y is None:
            bad.append(f"{cid}: a frame could not be read")
            continue
        h, w = x.shape[:2]
        f = min(1.0, 480.0 / max(h, w))
        size = (max(8, int(round(w * f))), max(8, int(round(h * f))))
        sv = ssim(cv2.resize(x, size, interpolation=cv2.INTER_AREA), cv2.resize(y, size, interpolation=cv2.INTER_AREA))
        rows.append({"clip": cid, "piece_frame": j, "movie_frame": k, "ssim": round(sv, 4)})
        if k - j != ex.k0 or sv < CHECK_SSIM:
            bad.append(f"{cid}: the movie's frame {k} is not the piece's frame {j} (SSIM {sv:.3f})")
    return {"ok": not bad, "rows": rows, "why": "; ".join(bad)}


def finish(ctx: Any) -> dict:
    """The end of a run that analysed a piece (module docstring): 1_edit.xml and the edits in extras/ moved onto the
    movie, A1's raw_stereo.wav made to the movie's frames, the movie linked into the run's media, extras/excerpt.json
    written, the piece deleted. Everything is made aside and checked first; if anything fails, nothing is changed:
    the edit keeps playing the piece (kept in the run's media) and a warning says so. Never raises."""
    ex = getattr(ctx, "excerpt", None)
    if ex is None:
        return {}
    cfg = ctx.cfg
    t0 = time.perf_counter()
    out: dict[str, Any] = {"moved": False}
    temps: list[Path] = []
    try:
        media = Path(cfg.media_dir)
        movie = Path(ex.movie)
        movie_name = "raw" + (movie.suffix.lower() or ".mp4")
        piece_link = Path(ctx.raw_conform.path) if getattr(ctx, "raw_conform", None) is not None else None
        xmls = [p for p in [Path(cfg.deliver) / "1_edit.xml"] + sorted(Path(cfg.out).glob("edit_*.xml"))
                if p.is_file()]
        if not xmls:
            raise RuntimeError("no edit XML to move")
        # the sound: A1 plays raw_stereo.wav, made to the movie's frames
        wav = media / "raw_stereo.wav"
        wav_frames, wav_tmp = None, None
        if wav.is_file():
            samples, wav_tmp = movie_wav(wav, wav, ex)
            temps.append(wav_tmp)
            wav_frames = int(math.floor(samples * float(Fraction(ex.fps)) / SOUND_RATE))
        # the movie in the run's media (raw.<ext>; the piece's own link goes when the names are the same)
        movie_media = media / movie_name
        same_name = piece_link is not None and os.path.normcase(str(piece_link.resolve())) == \
            os.path.normcase(str(movie_media.resolve()))
        target = movie_media.with_name(movie_media.stem + ".movie" + movie_media.suffix) if same_name else movie_media
        from .conform import _link_or_copy
        how, why = _link_or_copy(movie.resolve(), target)
        if same_name:
            temps.append(target)
        moved = []
        for x in xmls:
            tmp = x.with_name(x.stem + ".movie.part.xml")
            temps.append(tmp)
            info = shift_xml(x, tmp, ex, movie_media, movie_name, wav_frames)
            moved.append((x, tmp, info))
        from .export_xml_edl import premiere_item_problems
        final = Path(cfg.deliver) / "1_edit.xml"
        for x, tmp, info in moved:
            if x == final:
                probs = premiere_item_problems(tmp)
                if probs:
                    raise RuntimeError(f"the moved 1_edit.xml has problems: {'; '.join(probs[:3])}")
                chk = check_moved(x, tmp, Excerpt(**{**ex.to_dict(), "movie": str(target)}), ex.piece)
                if not chk["ok"]:
                    raise RuntimeError(chk["why"])
                out["check"] = chk["rows"]
        # everything made and checked: swap it in
        if wav_tmp is not None:
            os.replace(wav_tmp, wav)
        if same_name:
            piece_link.unlink(missing_ok=True)
            os.replace(target, movie_media)
        for x, tmp, info in moved:
            os.replace(tmp, x)
        out.update(moved=True, xmls=[str(x) for x, _t, _i in moved], placed=how, why_copy=why,
                   exact=all(i["exact"] for x, _t, i in moved if x == final),
                   planning_copies_exact=all(i["exact"] for x, _t, i in moved if x != final),
                   shift_frames=ex.k0)
        (Path(cfg.out) / INFO_NAME).write_text(json.dumps({**ex.to_dict(), "piece": None, "piece_name": PIECE_NAME,
                                                           "moved": out}, indent=1, default=str), encoding="utf-8")
        # the piece goes: the file in work and every copy / link of it in the run's media (what V1 played, the
        # analysis copy) -- never the movie's link or the sound file
        pic = ((getattr(ctx, "edit_media", None) or {}).get("picture") or {}).get("path")
        keep = {os.path.normcase(str(p.resolve())) for p in (movie_media, wav) if p.exists()}
        for p in {Path(ex.piece), *(x for x in (piece_link, Path(pic) if pic else None) if x is not None)}:
            if p.exists() and os.path.normcase(str(p.resolve())) not in keep:
                try:
                    p.unlink()
                except OSError as e:
                    log.warning("piece of the movie: %s could not be deleted (%s)", p, e)
        out["seconds"] = round(time.perf_counter() - t0, 2)
        fps = float(Fraction(ex.fps))
        where = f"{_clock(ex.t0)}-{_clock(ex.t0 + ex.frames / fps)} of {_clock(ex.movie_seconds)}"
        line = (f"V1 plays the movie itself (extras\\media\\{movie_name}, "
                f"{'a hard link: no extra space' if how in ('hardlink', 'same') else 'a copy'}); A1 plays "
                f"raw_stereo.wav (stereo, made to the movie's frames) -- the run analysed only the piece {where} "
                f"(cut in {ex.cut_seconds:.0f} s, deleted now)")
        if isinstance(getattr(ctx, "edit_media", None), dict):
            ctx.edit_media["line"] = line            # (the end summary says what 1_edit.xml plays now)
        out["line"] = line
        try:
            rep = Path(cfg.out) / "report.md"
            if rep.is_file():
                with open(rep, "a", encoding="utf-8") as fh:
                    fh.write(f"\n\n## Piece of the movie (day 14)\n\n- The run analysed the piece {where} (frames "
                             f"{ex.k0}-{ex.k0 + ex.frames - 1} of the movie's {ex.movie_frames}), cut by stream copy "
                             f"where the sound check found the place ({', '.join(_clock(t) for t in ex.places[:4])})."
                             f"\n- RAW times and frames in this report count from the piece's start: add "
                             f"{_clock(ex.t0)} ({ex.k0} frames) for the movie's.\n- {line}.\n")
        except OSError:
            pass
        log.info("piece of the movie: 1_edit.xml now plays the movie itself (extras\\media\\%s, %s) -- every RAW clip "
                 "at its frame + %d%s; A1 plays raw_stereo.wav made to the movie's frames; the piece deleted (%.1f s)",
                 movie_name, "a hard link" if how in ("hardlink", "same") else "a copy", ex.k0,
                 "" if out["exact"] else " (rounded to the nearest frame of a sped-up clip)", out["seconds"])
        ctx.dlog.record("premiere", "excerpt_moved", **{k: v for k, v in out.items() if k != "check"},
                        check=out.get("check"))
    except Exception as e:  # noqa: BLE001 - the edit keeps playing the piece
        for p in temps:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        log.error("piece of the movie: 1_edit.xml could not be moved onto the movie (%s: %s) -- it plays the piece "
                  "(kept as extras\\media\\%s)", type(e).__name__, e,
                  Path(ctx.raw_conform.path).name if getattr(ctx, "raw_conform", None) else PIECE_NAME)
        ctx.warn(f"1_edit.xml plays the piece of the movie the run analysed, not the movie itself ({e})")
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _clock(s: float) -> str:
    s = max(0.0, float(s))
    h, rem = divmod(int(round(s)), 3600)
    mm, ss = divmod(rem, 60)
    return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"
