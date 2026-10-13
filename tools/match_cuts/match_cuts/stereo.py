"""stereo.py: a stereo copy of a RAW whose sound has more than two channels (5.1, 7.1), for Premiere (night 6).

A 5.1 RAW's six channels on A1 sound like an echo in Premiere: the surround channels (room, music, effects, often a
little behind the dialogue) are mixed in. ``stereo_copy`` writes ``raw_stereo.mp4`` next to the RAW the tool analysed:
the picture copied as it is (the same frames and timing, so every frame number of 1_edit.xml stays valid) and one clean
stereo track -- left = front left + centre x 0.707, right = front right + centre x 0.707 (the dialogue sits in the
centre channel); the surround and LFE channels are left out. One constant gain keeps the peak at PEAK_DB at most
(measured first; never raised, no compression). The analysis keeps reading the RAW itself: no stage cache changes.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .common import ffmpeg_bin, ffprobe_bin, log

NAME = "raw_stereo"         # + the RAW's own extension (.mp4: AAC; .mov, a ProRes conform: PCM)
CENTRE = 0.7071            # the centre channel's share in each side (-3 dB: the usual stereo downmix of 5.1)
PEAK_DB = -1.0             # the copy's loudest sample at most this (dBFS): a constant gain down when the mix is louder
AUDIO_BITRATE = "320k"
AUDIO_RATE = 48000


def audio_layout(path: str | Path) -> dict:
    """{'channels', 'layout'} of the first audio stream ({} without one)."""
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=channels,channel_layout", "-of", "json", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    s = (json.loads(out.stdout or "{}").get("streams") or [{}])[0] if out.returncode == 0 else {}
    if not s:
        return {}
    return {"channels": int(s.get("channels") or 0), "layout": str(s.get("channel_layout") or "")}


def pan_filter(channels: int, layout: str) -> str:
    """The ffmpeg pan filter of the stereo mix: front left / right + the centre at CENTRE, nothing else. A layout ffmpeg
    names (5.1, 5.1(side), 7.1, 6.1, 4.0 ...) by its channel names; an unnamed one in the standard order (FL FR FC
    ...). A layout without a centre: the front left / right alone."""
    names = {"5.1", "5.1(side)", "6.1", "6.1(back)", "6.1(front)", "7.1", "7.1(wide)", "7.1(wide-side)", "4.0",
             "5.0", "5.0(side)", "3.0", "3.1", "hexagonal", "octagonal"}
    lay = (layout or "").strip().lower()
    no_centre = {"quad", "quad(side)", "2.1", "2.2", "4.1"}
    if lay in no_centre:
        return "pan=stereo|c0=FL|c1=FR"
    if lay in names:
        return f"pan=stereo|c0=FL+{CENTRE}*FC|c1=FR+{CENTRE}*FC"
    if channels >= 3:                                  # unnamed: FL FR FC first (ffmpeg's / SMPTE's order)
        return f"pan=stereo|c0=c0+{CENTRE}*c2|c1=c1+{CENTRE}*c2"
    return "pan=stereo|c0=c0|c1=c1"


def peak_db(path: str | Path, pan: str) -> float | None:
    """The loudest sample of the stereo mix (dBFS; ffmpeg volumedetect), None when it cannot be measured."""
    res = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-v", "info", "-i", str(path), "-vn", "-sn",
                          "-dn", "-map", "0:a:0", "-af", f"{pan},volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.findall(r"max_volume:\s*(-?[\d.]+|-inf)\s*dB", res.stderr or "")
    if res.returncode != 0 or not m:
        return None
    return float("-inf") if m[-1] == "-inf" else float(m[-1])


def _frames(path: str | Path) -> tuple[int, float]:
    """(video frames, duration s) of the first video stream: the container's own count (MP4 / MOV hold it), else the
    packets counted (no decode)."""
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_frames,duration:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    d = json.loads(out.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    dur = s.get("duration") or (d.get("format") or {}).get("duration") or 0.0
    n = s.get("nb_frames")
    if n and str(n).isdigit():
        return int(n), float(dur)
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=nb_read_packets", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    s = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
    return int(s.get("nb_read_packets") or 0), float(dur)


def stereo_copy(src: str | Path, dst: str | Path) -> dict:
    """``dst``: ``src``'s picture copied as it is and its sound as one clean stereo track (module docstring). Returns
    {'path', 'channels' (src's), 'layout', 'pan', 'gain_db', 'peak_db'}; raises when the copy fails or its picture
    differs from src's (another number of frames)."""
    src, dst = Path(src), Path(dst)
    lay = audio_layout(src)
    if not lay:
        raise ValueError(f"{src.name} has no audio stream")
    pan = pan_filter(lay["channels"], lay["layout"])
    peak = peak_db(src, pan)
    gain = 0.0 if peak is None or peak == float("-inf") else min(0.0, PEAK_DB - peak)
    af = pan + (f",volume={gain:.2f}dB" if gain < -0.005 else "")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    acodec = (["-c:a", "pcm_s16le"] if dst.suffix.lower() == ".mov" else ["-c:a", "aac", "-b:a", AUDIO_BITRATE])
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "copy", "-af", af, *acodec, "-ar", str(AUDIO_RATE), "-map_metadata", "0", "-movflags", "+faststart",
           str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not write the stereo copy: {(res.stderr or '').strip()[-300:]}")
    n0, d0 = _frames(src)
    n1, d1 = _frames(tmp)
    if n0 != n1:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"the stereo copy has {n1} video frames, the RAW {n0}")
    got = audio_layout(tmp)
    if got.get("channels") != 2:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"the stereo copy's sound has {got.get('channels')} channels")
    tmp.replace(dst)
    log.info("stereo copy for Premiere: %s (%d-channel %s -> stereo: front left / right + centre x %.3f, surround and "
             "LFE left out; gain %.1f dB, peak %s dB; the picture copied as it is: %d frames, %.2f s)", dst.name,
             lay["channels"], lay["layout"] or "unnamed layout", CENTRE, gain,
             "?" if peak is None else f"{peak:.1f}", n1, d1)
    return {"path": str(dst), "channels": lay["channels"], "layout": lay["layout"], "pan": pan,
            "gain_db": round(gain, 2), "peak_db": None if peak is None else round(peak, 2)}


def stereo_block(raw: dict, path: str | Path, out_dir: str | Path | None = None) -> dict:
    """A cut list's RAW block pointing at the stereo copy (two channels); its frames, size and rate stay the RAW's."""
    p = Path(path).resolve()
    rel = ""
    if out_dir is not None:
        try:
            rel = p.relative_to(Path(out_dir).resolve()).as_posix()
        except ValueError:
            rel = ""
    out: dict[str, Any] = dict(raw, file=rel or str(p), file_abs=str(p), file_rel=rel, audio_channels=2)
    return out
