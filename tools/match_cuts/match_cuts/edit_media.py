"""edit_media.py -- the RAW files 1_edit.xml plays (day 13: keep the original RAW, no more 14 GB copies).

The conversion of a RAW (raw_ae.mp4: H.264 CRF 12 veryfast -- Borat's 3.7 GB x265 became 15 GB) only existed for After
Effects. A Premiere run (--premiere: the app and the batch) now plays the ORIGINAL RAW; the converted copy is the tool's
own scratch for its analysis and is removed when the run is done (pipeline.drop_analysis_copy):

* V1 -- ``media/raw.<ext>``: the RAW the run was given, hard-linked into the run folder (no extra space) or copied
  (another drive; conform._link_or_copy -- not enough space stops the run, as night 10). Premiere 2024 opens H.264,
  HEVC / x265 (also 10-bit) and ProRes in .mp4 / .mov / .m4v. An .mkv with H.264 / HEVC is remuxed to ``media/raw.mp4``
  (the same video packets, ``-c copy``: nothing re-encoded) and checked frame for frame.
* A1 -- the V1 file itself when its sound is plain stereo / mono AAC or PCM that starts with the picture (as before);
  else ``media/raw_stereo.wav``: one stereo track, 16-bit PCM 48 kHz (a 5.1 RAW: front left / right + centre x 0.707,
  stereo.py's mix and peak), made from the RAW with the SAME audio steps as the analysis copy (conform.plan_transcode:
  re-based so that sample 0 is video frame 0; Borat's sound starts 20 ms after its picture: 960 samples of silence) and
  checked sample-exact against the analysis copy at three places. A WAV has no encoder delay (AAC in an .m4a starts
  with 1024 priming samples that every program has to skip by itself), so A1 lands on the planned sample anywhere.
* The plan's frame N is the original's frame N only for a constant frame rate that starts at 0, with square pixels, an
  even size and no rotation (conform's CFR re-stamp: conformed frame k = decoded frame k). Anything else -- VFR, a start
  time, an edit list on the picture, rotation, SAR, interlacing, another codec or container -- and V1 plays
  ``media/raw_premiere.mp4``: converted with the analysis copy's own picture steps (the same frames) but at H.264
  CRF 17 preset slow (not CRF 12 veryfast), no sound (A1: raw_stereo.wav); the run's report and summary say why.

No cached stage imports this module (it is not in common.STAGE_CODE): it changes no cache key.
"""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from .common import ffmpeg_bin, ffprobe_bin, log, replace_file

ORIGINAL_STEM = "raw"                  # media/raw.<ext>: the original RAW (a hard link / copy, or its .mp4 remux)
SOUND_NAME = "raw_stereo.wav"          # A1 when the RAW's own sound cannot be played as it is
CONVERTED_NAME = "raw_premiere.mp4"    # V1 when the original picture cannot be used (CRF 17 slow)
SOUND_RATE = 48000
DELIVERY_CRF = 17
DELIVERY_PRESET = "slow"
PREMIERE_CONTAINERS = ("mp4", "mov", "m4v")
REMUX_CONTAINERS = ("mkv",)            # webm holds VP8 / VP9 / AV1: no .mp4 Premiere opens as it is
PREMIERE_PIX = {"h264": ("yuv420p", "yuvj420p", "yuv420p10le"),
                "hevc": ("yuv420p", "yuvj420p", "yuv420p10le"),
                "prores": None}        # None: any ProRes flavour
SYNC_WINDOW_S = 2.0                    # the sound check: windows of this length ...
SYNC_MAX_LAG = 4800                    # ... searched for a lag of up to 0.1 s either way
SYNC_MIN_CORR = 0.9                    # a window this similar (normalised) counts; quieter ones are left out
SYNC_MIN_RMS = 1e-3


# ---------------------------------------------------------------------------------------------------------------------
# what plays where
# ---------------------------------------------------------------------------------------------------------------------

def issue_kind(issue: str) -> str:
    """What an AE issue (probe.ae_issues: '<code>: ...') says about the original in Premiere: 'sound' (only the sound's
    timing / codec: A1 gets the WAV), 'codec' (the video codec: Premiere may open it, see PREMIERE_PIX), 'container'
    (.mkv: remuxed) or 'picture' (frame N would not be the plan's frame N: converted)."""
    code = issue.split(":", 1)[0].strip().lower()
    text = issue.lower()
    if code == "acodec" or (code == "start_time" and "audio" in text) or text.startswith("edit_list: audio"):
        return "sound"
    if code == "vcodec":
        return "codec"
    if code == "container":
        return "container"
    return "picture"


def codec_ok(vcodec: str, pix_fmt: str) -> bool:
    """Premiere 2024 opens this video codec / pixel format as it is."""
    if vcodec not in PREMIERE_PIX:
        return False
    allowed = PREMIERE_PIX[vcodec]
    return allowed is None or (pix_fmt or "") in allowed


def picture_choice(src: Any, analysis: Any, conformed: bool) -> dict:
    """How V1 plays the RAW (``src``: the input's probe.StreamInfo; ``analysis``: the analysed file's; ``conformed``:
    the analysis copy is a conversion): {'how': 'analysis' (the analysis copy IS the input, linked / copied as it is) |
    'original' | 'remux' | 'convert', 'why': plain words}."""
    if not conformed:
        return {"how": "analysis", "why": "the RAW as it is (it needed no conversion)"}
    issues = list(getattr(src, "ae_issues", None) or [])
    blocking = [i for i in issues if issue_kind(i) == "picture"]
    remuxable = str(src.container) in REMUX_CONTAINERS and src.vcodec in ("h264", "hevc")
    if remuxable:                         # a picture that starts late in an .mkv starts at 0 in its remux (checked)
        blocking = [i for i in blocking if not i.startswith("start_time:")]
    if blocking:
        return {"how": "convert", "why": "; ".join(blocking)}
    if not codec_ok(str(src.vcodec), str(src.pix_fmt)):
        return {"how": "convert", "why": f"Premiere cannot open {src.vcodec or 'this'} video ({src.pix_fmt or '?'}) "
                                         "as it is"}
    same = (int(src.nb_frames) == int(analysis.nb_frames) and str(src.fps) == str(analysis.fps)
            and (int(src.display_width), int(src.display_height)) == (int(analysis.display_width),
                                                                        int(analysis.display_height)))
    if not same:
        return {"how": "convert", "why": f"the original ({src.nb_frames} frames, {src.display_width}x"
                                         f"{src.display_height}) is not frame for frame the analysed copy "
                                         f"({analysis.nb_frames} frames, {analysis.display_width}x"
                                         f"{analysis.display_height})"}
    if str(src.container) in PREMIERE_CONTAINERS:
        return {"how": "original", "why": f"{src.vcodec} {src.pix_fmt} in .{src.container}: Premiere opens it"}
    if str(src.container) in REMUX_CONTAINERS and src.vcodec in ("h264", "hevc"):
        return {"how": "remux", "why": f"{src.vcodec} in .{src.container}: the same video moved into an .mp4 "
                                       "(nothing re-encoded)"}
    return {"how": "convert", "why": f"Premiere cannot open .{src.container or '?'} files"}


def sound_choice(src: Any, picture_how: str) -> dict:
    """How A1 plays the RAW's sound: {'how': 'same' (the V1 file's own sound) | 'wav' | 'none', 'why'}."""
    if not bool(getattr(src, "has_audio", False)):
        return {"how": "none", "why": "the RAW has no sound"}
    if picture_how not in ("analysis", "original"):
        return {"how": "wav", "why": "V1 plays a copy without sound"}
    ch = int(getattr(src, "a_channels", 0) or 0)
    if ch > 2:
        return {"how": "wav", "why": f"{ch}-channel sound (5.1?): one clean stereo track"}
    timing = [i for i in (getattr(src, "ae_issues", None) or []) if issue_kind(i) == "sound"]
    if timing and picture_how == "original":
        return {"how": "wav", "why": "; ".join(timing)}
    return {"how": "same", "why": "plain stereo sound that starts with the picture"}


# ---------------------------------------------------------------------------------------------------------------------
# the stereo sound file
# ---------------------------------------------------------------------------------------------------------------------

def mix_filter(channels: int, layout: str) -> str:
    """The stereo mix of a sound with ``channels`` (stereo.pan_filter for more than two; mono on both sides)."""
    from . import stereo
    if channels > 2:
        return stereo.pan_filter(channels, layout)
    if channels == 1:
        return "pan=stereo|c0=c0|c1=c0"
    return "pan=stereo|c0=c0|c1=c1"


def timing_filter(src: Any, cfg: Any, conformed: bool) -> str:
    """The audio steps the analysis copy's sound went through, so the WAV's sample k is the analysed sound's sample k:
    a conversion's own (conform.plan_transcode: resampled to 48 kHz, re-based to video frame 0, a late start padded /
    an early one trimmed); a RAW analysed as it is: resampled only (the analysis read its sound from its first
    sample)."""
    if not conformed:
        return f"aresample={SOUND_RATE}"
    from . import conform
    af = conform.plan_transcode(src, "raw", cfg).get("af")
    return str(af or f"aresample={SOUND_RATE}")


def make_sound(src: str | Path, dst: str | Path, timing: str, channels: int, layout: str) -> dict:
    """``dst`` (a .wav): ``src``'s first sound stream through ``timing`` and the stereo mix, one constant gain keeping
    its peak at stereo.PEAK_DB at most (never raised). Returns {path, filter, gain_db, peak_db, samples, bytes}."""
    from . import stereo
    src, dst = Path(src), Path(dst)
    mix = mix_filter(int(channels), str(layout or ""))
    chain = f"{timing},{mix}"
    peak = stereo.peak_db(src, chain)
    gain = 0.0 if peak is None or peak == float("-inf") else min(0.0, stereo.PEAK_DB - peak)
    af = chain + (f",volume={gain:.2f}dB" if gain < -0.005 else "")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:a:0", "-vn",
           "-sn", "-dn", "-af", af, "-c:a", "pcm_s16le", "-ar", str(SOUND_RATE), "-ac", "2", "-rf64", "auto",
           "-map_metadata", "-1", "-map_chapters", "-1", "-f", "wav", str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not write {dst.name}: {(res.stderr or '').strip()[-300:]}")
    info = wav_info(tmp)
    if info.get("channels") != 2 or info.get("rate") != SOUND_RATE or not info.get("samples"):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{dst.name} came out wrong ({info})")
    replace_file(tmp, dst)
    return {"path": str(dst), "filter": af, "gain_db": round(gain, 2),
            "peak_db": None if peak is None or peak == float("-inf") else round(peak, 2),
            "samples": int(info["samples"]), "seconds": round(info["samples"] / SOUND_RATE, 3),
            "bytes": int(dst.stat().st_size)}


def wav_info(path: str | Path) -> dict:
    """{codec, channels, rate, samples, start} of a sound file's first audio stream (ffprobe; {} when unreadable)."""
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_name,channels,sample_rate,duration_ts,duration,start_time", "-of", "json",
                          str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    s = (json.loads(out.stdout or "{}").get("streams") or [{}])[0] if out.returncode == 0 else {}
    if not s:
        return {}
    rate = int(float(s.get("sample_rate") or 0))
    n = s.get("duration_ts")
    if n in (None, "N/A") and s.get("duration") not in (None, "N/A"):
        n = round(float(s["duration"]) * rate)
    try:
        start = float(s.get("start_time") or 0.0)
    except ValueError:
        start = 0.0
    return {"codec": str(s.get("codec_name") or ""), "channels": int(s.get("channels") or 0), "rate": rate,
            "samples": int(n or 0), "start": start}


def decode_window(path: str | Path, t: float, dur: float, af: str = "") -> np.ndarray:
    """``dur`` s of ``path``'s first sound stream from second ``t`` (48 kHz stereo float32, [n, 2]): decoded from the
    start, resampled, then cut by COUNTING samples (sample k of the window = decoded sample t x 48000 + k). Never a
    seek and never a cut by timestamps: in ffmpeg 8 both land 8-56 samples off on AAC in an .mp4 (measured on Borat's
    analysis copy -- its full decode and the WAV made from it agree to the sample)."""
    s0 = int(round(t * SOUND_RATE))
    s1 = s0 + int(round(dur * SOUND_RATE))
    chain = ",".join(x for x in (f"aresample={SOUND_RATE}", f"atrim=start_sample={s0}:end_sample={s1}",
                                 "asetpts=PTS-STARTPTS", af) if x)
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-vn",
           "-af", chain, "-ac", "2", "-t", f"{dur:.6f}", "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not read the sound of {Path(path).name} at {t:.1f} s")
    y = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2)
    return y[:int(round(dur * SOUND_RATE))]


def best_lag(a: np.ndarray, b: np.ndarray, max_lag: int = SYNC_MAX_LAG) -> tuple[int, float]:
    """(lag in samples, normalised correlation) that best lines ``b`` up with ``a`` (mono sums): b[n] ~ a[n + lag]."""
    x = a.mean(axis=1).astype(np.float64)
    y = b.mean(axis=1).astype(np.float64)
    n = min(len(x), len(y))
    if n < 2 * max_lag + 16:
        max_lag = max(1, n // 4)
    x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
    m = 1 << int(math.ceil(math.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(x, m) * np.conj(np.fft.rfft(y, m)), m)
    lags = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    vals = np.concatenate([c[:max_lag + 1], c[-max_lag:]])
    i = int(np.argmax(vals))
    den = math.sqrt(float(np.dot(x, x)) * float(np.dot(y, y))) or 1.0
    return int(lags[i]), float(vals[i] / den)


def check_sync(sound: str | Path, ref: str | Path, ref_af: str, points: list[float]) -> dict:
    """The sound file against the sound the tool analysed (``ref``: the analysis copy, its stereo mix ``ref_af``) at
    ``points`` (s): the lag must be 0 samples wherever the two are clearly the same sound. Returns {ok, points: [{t,
    lag, corr} | {t, skipped}], why}."""
    rows, bad = [], []
    for t in points:
        a = decode_window(ref, t, SYNC_WINDOW_S, ref_af)
        b = decode_window(sound, t, SYNC_WINDOW_S)
        n = min(len(a), len(b))
        if n < SOUND_RATE // 4 or float(np.sqrt(np.mean(a[:n] ** 2))) < SYNC_MIN_RMS:
            rows.append({"t": round(t, 3), "skipped": "quiet or too short"})
            continue
        lag, corr = best_lag(a[:n], b[:n])
        rows.append({"t": round(t, 3), "lag": lag, "corr": round(corr, 4)})
        if corr < SYNC_MIN_CORR:
            bad.append(f"at {t:.1f} s the two sounds differ (correlation {corr:.2f})")
        elif lag != 0:
            bad.append(f"at {t:.1f} s the sound file is {lag:+d} samples ({1000.0 * lag / SOUND_RATE:+.2f} ms) off")
    return {"ok": not bad, "points": rows, "why": "; ".join(bad)}


def sync_points(seconds: float) -> list[float]:
    """Where the sound is checked: 5 s in, the middle and 10 s before the end (a short RAW: what fits)."""
    d = max(0.0, float(seconds))
    pts = [min(5.0, d / 4), d / 2, max(0.0, d - 10.0)]
    out: list[float] = []
    for p in pts:
        p = max(0.0, min(p, d - SYNC_WINDOW_S))
        if all(abs(p - q) > SYNC_WINDOW_S for q in out):
            out.append(round(p, 3))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the picture file
# ---------------------------------------------------------------------------------------------------------------------

def frames_of(path: str | Path) -> int:
    """The video frames of a file (the container's count, else its packets)."""
    from . import stereo
    return int(stereo._frames(path)[0])


def remux(src: str | Path, dst: str | Path, vindex: int, vcodec: str, start: float = 0.0) -> dict:
    """``src``'s video packets moved into an .mp4 (``-c copy``; no sound: A1 plays the WAV), the first frame moved to 0
    (``start``: the picture's first time in ``src``); the same number of frames. Returns {path, frames, bytes}; raises
    when ffmpeg fails."""
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    tag = ["-tag:v", "hvc1"] if vcodec == "hevc" else []
    shift = ["-itsoffset", f"{-float(start):.6f}"] if abs(float(start)) > 1e-6 else []
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", *shift, "-i", str(src),
           "-map", f"0:v:{int(vindex)}", "-c:v", "copy", *tag, "-an", "-sn", "-dn", "-map_metadata", "-1",
           "-map_chapters", "-1", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not remux {src.name}: {(r.stderr or '').strip()[-300:]}")
    replace_file(tmp, dst)
    return {"path": str(dst), "frames": frames_of(dst), "bytes": int(dst.stat().st_size)}


def delivery_cfg(cfg: Any) -> Any:
    """``cfg`` with the delivery copy's encoder: H.264 CRF 17, preset slow (the analysis copy's own picture steps)."""
    c = copy.copy(cfg)
    c.conform_codec = "h264"
    c.conform_h264_crf = DELIVERY_CRF
    c.conform_h264_preset = DELIVERY_PRESET
    return c


def convert(src: Any, dst: str | Path, cfg: Any) -> dict:
    """V1's copy when the original cannot be used: the analysis copy's picture steps (conform.plan_transcode: the same
    frames, CFR from 0, square pixels, even size) at H.264 CRF 17 preset slow, no sound. Returns {path, frames, plan
    (the encoder words), seconds, bytes}; raises when it fails or its frame count is not the plan's."""
    from . import conform
    dst = Path(dst)
    plan = dict(conform.plan_transcode(src, "raw", delivery_cfg(cfg)))
    plan.update(af=None, aargs=[])
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    cmd = conform.ffmpeg_command(str(src.path), str(tmp), plan)
    t0 = time.perf_counter()
    log.info("the RAW for Premiere: converting %s -> %s (H.264 CRF %d, preset %s, %d frames)", Path(src.path).name,
             dst.name, DELIVERY_CRF, DELIVERY_PRESET, plan["expected_frames"])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not convert the RAW for Premiere ({r.returncode}): {(r.stderr or '')[-300:]}")
    n = frames_of(tmp)
    if n != int(plan["expected_frames"]):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"the converted RAW has {n} frames, the plan {plan['expected_frames']}")
    replace_file(tmp, dst)
    return {"path": str(dst), "frames": n, "seconds": round(time.perf_counter() - t0, 1),
            "bytes": int(dst.stat().st_size), "encoder": f"H.264 CRF {DELIVERY_CRF}, preset {DELIVERY_PRESET}"}


def same_pictures(a: str | Path, b: str | Path, n: int, count: int = 8) -> dict:
    """Frame k of ``a`` and of ``b`` (both CFR from 0, ``n`` frames) look alike at ``count`` places (gray SSIM at
    <= 480 px > 0.95, and closer than to the neighbouring frame where the two differ): {ok, samples, why}."""
    import cv2
    from .conform import ssim
    from .media import VideoReader
    ks = sorted({int(round(x)) for x in np.linspace(0, max(0, n - 2), max(1, count))})
    rows, bad = [], []
    with VideoReader(str(a)) as ra, VideoReader(str(b)) as rb:
        fa = ra.get_many(ks + [k + 1 for k in ks], fmt="gray")
        fb = rb.get_many(ks, fmt="gray")
    for k in ks:
        x, y, nx = fa.get(k), fb.get(k), fa.get(k + 1)
        if x is None or y is None:
            bad.append(f"frame {k} could not be read")
            continue
        h, w = x.shape[:2]
        f = min(1.0, 480.0 / max(h, w))
        size = (max(8, int(round(w * f))), max(8, int(round(h * f))))
        x, y = cv2.resize(x, size, interpolation=cv2.INTER_AREA), cv2.resize(y, size, interpolation=cv2.INTER_AREA)
        s = ssim(x, y)
        s_nb = ssim(cv2.resize(nx, size, interpolation=cv2.INTER_AREA), y) if nx is not None else None
        rows.append({"k": k, "ssim": round(s, 4), "next": None if s_nb is None else round(s_nb, 4)})
        if s < 0.95 or (s_nb is not None and s_nb > s + 1e-4 and 1.0 - s_nb > 0.002):
            bad.append(f"frame {k} differs (SSIM {s:.3f}" + (f", the next frame {s_nb:.3f})" if s_nb is not None
                                                             else ")"))
    return {"ok": not bad, "samples": rows, "why": "; ".join(bad)}


# ---------------------------------------------------------------------------------------------------------------------
# the step
# ---------------------------------------------------------------------------------------------------------------------

def original_name(src: str | Path, how: str) -> str:
    """media/raw.<ext> -- the RAW's own extension (a remux: .mp4)."""
    ext = ".mp4" if how == "remux" else (Path(src).suffix.lower() or ".mp4")
    return ORIGINAL_STEM + ext


def prepare(src: Any, analysis: Any, conf: Any, cfg: Any, media_dir: str | Path) -> dict:
    """Place the files 1_edit.xml plays (module docstring). ``src``: the input's StreamInfo; ``analysis``: the analysed
    file's; ``conf``: its conform.ConformResult. Returns {'picture': {how, why, path, name, placed}, 'sound': {how,
    why, path?, ...}, 'line': one plain sentence for the summary, 'fallback': why V1 plays a converted copy ('' when it
    plays the original)}. conform.NoSpaceError when a copy does not fit; any other failure raises (the caller keeps
    the old way)."""
    from . import conform
    media = Path(media_dir)
    media.mkdir(parents=True, exist_ok=True)
    conformed = bool(getattr(conf, "conformed", False))
    pic = picture_choice(src, analysis, conformed)
    out: dict[str, Any] = {"picture": dict(pic), "sound": {}, "fallback": ""}
    if pic["how"] == "analysis":
        out["picture"].update(path=str(Path(conf.path).resolve()), name=Path(conf.path).name, placed="as analysed")
    elif pic["how"] == "original":
        dst = media / original_name(src.path, "original")
        how, why = conform._link_or_copy(Path(src.path).resolve(), dst)
        if how == "copy":
            log.info("the RAW for Premiere: copied, not linked: %s", why)
        out["picture"].update(path=str(dst.resolve()), name=dst.name, placed=how, why_copy=why)
    elif pic["how"] == "remux":
        dst = media / original_name(src.path, "remux")
        need, free = int(getattr(src, "file_size", 0) or 0), shutil.disk_usage(media).free
        if free < need + conform.COPY_RESERVE:
            raise conform.NoSpaceError(f"not enough free space to remux the RAW into the run folder ({need / 1024 ** 3:.1f}"
                                       f" GB needed, {free / 1024 ** 3:.1f} GB free on {media})")
        from .probe import probe as probe_file, video_stream_ordinal
        rm = remux(src.path, dst, video_stream_ordinal(src), str(src.vcodec), float(src.first_pts_time or 0.0))
        got = probe_file(dst, "raw", str(getattr(cfg, "work_dir", media.parent)), decode=True)
        bad = [i for i in got.ae_issues if issue_kind(i) == "picture"]
        if got.vfr or int(got.nb_frames) != int(src.nb_frames) or bad:
            dst.unlink(missing_ok=True)
            pic = {"how": "convert", "why": (f"its remux into an .mp4 is not frame for frame the RAW ({got.nb_frames} "
                                             f"frames of {src.nb_frames}" + (f"; {'; '.join(bad)}" if bad else "")
                                             + (", a variable frame rate" if got.vfr else "") + ")")}
            out["picture"] = dict(pic)
        else:
            out["picture"].update(path=str(dst.resolve()), name=dst.name, placed="remux", bytes=rm["bytes"])
    if pic["how"] == "convert":
        dst = media / CONVERTED_NAME
        need, free = int(getattr(src, "file_size", 0) or 0), shutil.disk_usage(media).free
        if free < need + conform.COPY_RESERVE:
            raise conform.NoSpaceError(f"not enough free space to convert the RAW for Premiere (about "
                                       f"{need / 1024 ** 3:.1f} GB, {free / 1024 ** 3:.1f} GB free on {media})")
        cv = convert(src, dst, cfg)
        chk = same_pictures(analysis.path, dst, int(analysis.nb_frames))
        if not chk["ok"]:
            dst.unlink(missing_ok=True)
            raise RuntimeError(f"the converted RAW does not show the analysed frames: {chk['why']}")
        out["picture"].update(path=str(dst.resolve()), name=dst.name, placed="converted", bytes=cv["bytes"],
                              encoder=cv["encoder"], check=chk["samples"])
        out["fallback"] = pic["why"]
    snd = sound_choice(src, out["picture"]["how"])
    out["sound"] = dict(snd)
    if snd["how"] == "wav":
        dst = media / SOUND_NAME
        made = make_sound(src.path, dst, timing_filter(src, cfg, conformed), int(src.a_channels or 0),
                          _layout(src.path))
        ref_af = mix_filter(int(getattr(analysis, "a_channels", 0) or src.a_channels or 0), _layout(analysis.path))
        sync = check_sync(dst, analysis.path, ref_af, sync_points(float(analysis.duration)))
        if not sync["ok"]:
            # made from the analysed sound itself: the same samples by construction
            log.warning("the RAW for Premiere: %s from the original RAW is off (%s) -- made from the analysed copy "
                        "instead", dst.name, sync["why"])
            made = make_sound(analysis.path, dst, f"aresample={SOUND_RATE}",
                              int(getattr(analysis, "a_channels", 0) or 2), _layout(analysis.path))
            sync = check_sync(dst, analysis.path, ref_af, sync_points(float(analysis.duration)))
            made["from"] = "the analysed copy"
            if not sync["ok"]:
                dst.unlink(missing_ok=True)
                raise RuntimeError(f"{dst.name} is not in sync with the analysed sound: {sync['why']}")
        else:
            made["from"] = "the original RAW"
        out["sound"].update(made, name=dst.name, sync=sync)
    out["line"] = summary_line(out)
    return out


def _layout(path: str | Path) -> str:
    from . import stereo
    return str((stereo.audio_layout(path) or {}).get("layout") or "")


def summary_line(res: dict) -> str:
    """One plain sentence: what V1 and A1 of 1_edit.xml play."""
    p, s = res.get("picture") or {}, res.get("sound") or {}
    how = p.get("how")
    if how == "analysis":
        v = f"V1 plays the RAW itself (extras\\media\\{p.get('name')})"
    elif how == "original":
        v = (f"V1 plays the original RAW (extras\\media\\{p.get('name')}, "
             + ("a hard link: no extra space" if p.get("placed") in ("hardlink", "same") else "a copy") + ")")
    elif how == "remux":
        v = f"V1 plays the original RAW's video moved into extras\\media\\{p.get('name')} (nothing re-encoded)"
    else:
        v = (f"V1 plays a converted copy (extras\\media\\{p.get('name')}, {p.get('encoder', 'H.264')}) because the "
             f"original cannot be used as it is: {p.get('why')}")
    if s.get("how") == "wav":
        a = f"A1 plays {s.get('name')} (stereo, {s.get('why')})"
    elif s.get("how") == "same":
        a = "A1 plays the same file's sound"
    else:
        a = "no sound"
    return f"{v}; {a}"


def premiere_block(raw: dict, res: dict, out_dir: str | Path | None = None) -> dict:
    """The cut list's RAW block for 1_edit.xml: ``file`` = what V1 plays, ``sound_file`` = what A1 plays when it is
    another file (its frames, size and rate stay the RAW's: the same video)."""
    def rel(p: Path) -> str:
        if out_dir is None:
            return ""
        try:
            return p.relative_to(Path(out_dir).resolve()).as_posix()
        except ValueError:
            return ""
    pic = Path(res["picture"]["path"]).resolve()
    out = dict(raw, file=rel(pic) or str(pic), file_abs=str(pic), file_rel=rel(pic))
    snd = res.get("sound") or {}
    out["picture_audio_channels"] = int(raw.get("audio_channels") or 0)
    if snd.get("how") == "wav" and snd.get("path"):
        sp = Path(snd["path"]).resolve()
        out.update(sound_file=rel(sp) or str(sp), sound_file_abs=str(sp), sound_file_rel=rel(sp),
                   audio_channels=2, audio_sample_rate=SOUND_RATE, has_audio=True)
        if res["picture"].get("how") in ("remux", "convert"):
            out["picture_audio_channels"] = 0          # a copy without sound
    return out


def drop_analysis_copy(analysed: str | Path, picture: str | Path, xmls: list, raw_input: str | Path | None) -> dict:
    """The analysis copy (media/raw_ae.<ext>) once the run is done: removed when V1 plays another file (stereo.one_raw_file
    checks: the same number of video frames, no XML plays it, not the input itself)."""
    from . import stereo
    return stereo.one_raw_file(analysed, picture, xmls, raw_input)


def sound_from_stereo_copy(single: str | Path, dst: str | Path) -> dict:
    """Clean up of an older run (the app's core/rawkeep.py): its raw_stereo.mp4's sound -- already the stereo mix, on
    the run's own clock -- decoded into ``dst`` (.wav, 16-bit 48 kHz), sample for sample (a decode from the start: no
    seek). Returns {path, samples, bytes}; raises when it comes out shorter or longer than the sound it holds."""
    src, dst = Path(single), Path(dst)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:a:0", "-vn",
           "-af", f"aresample={SOUND_RATE}", "-c:a", "pcm_s16le", "-ac", "2", "-rf64", "auto", "-map_metadata", "-1",
           "-f", "wav", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not write {dst.name}: {(r.stderr or '').strip()[-300:]}")
    got, had = wav_info(tmp), wav_info(src)
    if got.get("channels") != 2 or not got.get("samples") or (
            had.get("samples") and abs(int(got["samples"]) - int(had["samples"]) * SOUND_RATE // max(1, had["rate"]))
            > 2048):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{dst.name} came out wrong ({got} from {had})")
    replace_file(tmp, dst)
    return {"path": str(dst), "samples": int(got["samples"]), "bytes": int(dst.stat().st_size)}


def retarget_xml(xml_path: str | Path, old_name: str, picture: str | Path, sound: str | Path,
                 picture_channels: int = 0) -> str:
    """Clean up of an older run: its own XML (1_edit.xml / extras/edit_*.xml -- never your projects) whose V1 and A1
    play one file, ``old_name`` (raw_stereo.mp4: the picture copied + stereo sound), as the new XML text that plays
    ``picture`` (the original RAW: the same frames) on V1 and ``sound`` (raw_stereo.wav: the same samples) on A1 --
    only the file references change, every clip's in / out, framing, speed, audio chain and link stay as they are.
    Raises ValueError when the XML does not play ``old_name`` on V1 and A1 as one file."""
    import xml.etree.ElementTree as ET
    from .export_xml_edl import SOUND_FILE_ID, SOUND_MASTERCLIP, _file_url, _url_path
    root = ET.parse(str(xml_path)).getroot()
    seq = root.find("sequence")
    if seq is None:
        raise ValueError("no <sequence>")
    defs = [f for f in root.iter("file") if f.find("pathurl") is not None and
            Path(_url_path(str(f.findtext("pathurl") or "")).replace("\\", "/")).name.lower() == old_name.lower()]
    ids = {f.get("id") for f in defs}
    if len(ids) != 1 or not defs:
        raise ValueError(f"the XML names {old_name} {len(defs)} time(s) under {len(ids)} id(s)")
    fid, old = next(iter(ids)), defs[0]
    if fid == SOUND_FILE_ID:
        raise ValueError("already two files")
    vt, a_tracks = seq.find("media/video/track"), seq.findall("media/audio/track")
    v_items = [ci for ci in (vt.findall("clipitem") if vt is not None else [])
               if ci.find("file") is not None and ci.find("file").get("id") == fid]
    a_items = [ci for tr in a_tracks for ci in tr.findall("clipitem")
               if ci.find("file") is not None and ci.find("file").get("id") == fid]
    if not v_items or not a_items:
        raise ValueError(f"{old_name} is not played on both V1 and A1")
    keep = {k: copy.deepcopy(old.find(k)) for k in ("rate", "duration", "timecode") if old.find(k) is not None}
    # A1 -> the sound file (its definition in the first A1 clip, a reference in the others)
    for n, ci in enumerate(a_items):
        fe = ci.find("file")
        i = list(ci).index(fe)
        ci.remove(fe)
        new = ET.Element("file", id=SOUND_FILE_ID)
        if n == 0:
            ET.SubElement(new, "name").text = Path(sound).name
            ET.SubElement(new, "pathurl").text = _file_url(str(Path(sound).resolve()))
            for k in ("rate", "duration", "timecode"):
                if keep.get(k) is not None:
                    new.append(copy.deepcopy(keep[k]))
            media = ET.SubElement(new, "media")
            a = ET.SubElement(media, "audio")
            sc = ET.SubElement(a, "samplecharacteristics")
            ET.SubElement(sc, "depth").text = "16"
            ET.SubElement(sc, "samplerate").text = str(SOUND_RATE)
            ET.SubElement(a, "channelcount").text = "2"
        ci.insert(i, new)
        if ci.find("masterclipid") is not None:
            ci.find("masterclipid").text = SOUND_MASTERCLIP
        if ci.find("name") is not None:
            ci.find("name").text = Path(sound).name
    # V1 -> the original RAW (the definition moved into the first V1 clip when it sat in an A1 clip)
    if not any(ci.find("file").find("pathurl") is not None for ci in v_items):
        fe = v_items[0].find("file")
        for child in list(old):
            fe.append(copy.deepcopy(child))
    for ci in v_items:
        fe = ci.find("file")
        if fe.find("pathurl") is not None:
            fe.find("pathurl").text = _file_url(str(Path(picture).resolve()))
            if fe.find("name") is not None:
                fe.find("name").text = Path(picture).name
            media = fe.find("media")
            aud = media.find("audio") if media is not None else None
            if aud is not None:
                if picture_channels > 0 and aud.find("channelcount") is not None:
                    aud.find("channelcount").text = str(int(picture_channels))
                elif picture_channels <= 0:
                    media.remove(aud)
        if ci.find("name") is not None:
            ci.find("name").text = Path(picture).name
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"


def describe_for_report(res: dict) -> list[str]:
    """Report lines (markdown) about the RAW files of 1_edit.xml."""
    if not res:
        return []
    p, s = res.get("picture") or {}, res.get("sound") or {}
    out = [f"- {summary_line(res)}."]
    if p.get("how") == "convert":
        out.append(f"- Why not the original RAW: {p.get('why')}. The copy uses the analysis copy's picture steps (the "
                   f"same frames) at {p.get('encoder')}, checked on {len(p.get('check') or [])} frames.")
    if s.get("how") == "wav":
        sync = s.get("sync") or {}
        pts = ", ".join(f"{r['t']:.0f} s: {r['lag']:+d} samples" if "lag" in r else f"{r['t']:.0f} s: {r['skipped']}"
                        for r in sync.get("points") or [])
        out.append(f"- {s.get('name')}: {s.get('seconds', 0):.1f} s of 48 kHz 16-bit stereo, made from "
                   f"{s.get('from', 'the RAW')} ({s.get('filter')}); in sync with the analysed sound ({pts}).")
    return out
