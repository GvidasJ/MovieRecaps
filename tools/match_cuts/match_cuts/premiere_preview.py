"""premiere_preview.py: ``--frame`` -- preview_recreation.mp4 as the edit you import: 1_edit.xml's V1 clips (the RAW
each plays, its speed, Position / Scale and mirror) seen through the frame PNG of V2, with A1's sound (each item's RAW
sound at its speed, pitch kept). Rendered at half the size of a 2160-wide sequence (1080x1920), at the sequence's
frame rate, one clip at a time with ffmpeg, then joined -- what Premiere shows, as a quick check of the framing and the
cuts (a cross dissolve is shown as a cut).
"""
from __future__ import annotations

import subprocess
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

PREVIEW_WIDTH = 1080        # the preview's width (a wider sequence is scaled down to it)
SR = 48000


def _raw_file(xml_path: Path) -> tuple[str, int, int, Fraction]:
    """(path, width, height, frame rate) of the RAW the V1 clips play (its <file> entry in the XML)."""
    root = ET.parse(str(xml_path)).getroot()
    fe = next((f for f in root.iter("file") if f.findtext("pathurl") and f.get("id") == "file-raw"), None)
    if fe is None:
        raise ValueError("no RAW <file> in the XML")
    url = fe.findtext("pathurl") or ""
    if url.startswith("file://localhost/"):
        path = urllib.parse.unquote(url[len("file://localhost/"):])
    elif url.startswith("file://"):
        path = "//" + urllib.parse.unquote(url[len("file://"):])
    else:
        path = urllib.parse.unquote(url)
    w = int(fe.findtext("media/video/samplecharacteristics/width") or 0)
    h = int(fe.findtext("media/video/samplecharacteristics/height") or 0)
    tb = int(fe.findtext("rate/timebase") or 30)
    ntsc = (fe.findtext("rate/ntsc") or "").upper() == "TRUE"
    return path, w, h, Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def _spans(x: dict) -> list[tuple[int, int]]:
    """Each V1 clip's [start, end) on the timeline (a transition's -1 resolved to its cut: the transition's start)."""
    clips = x["clips"]
    starts = sorted(t["start"] for t in x.get("transitions") or [])
    out: list[tuple[int, int]] = []
    for i, c in enumerate(clips):
        a = c["start"] if c["start"] >= 0 else (out[-1][1] if out else 0)
        if c["end"] >= 0:
            b = c["end"]
        else:
            b = next((t for t in starts if t > a), None)
            if b is None:
                b = clips[i + 1]["start"] if i + 1 < len(clips) and clips[i + 1]["start"] >= 0 else a + 1
        out.append((int(a), int(max(b, a + 1))))
    return out


def render(xml_path: str | Path, out_path: str | Path, cfg: Any, frame: Any) -> dict:
    """Render ``xml_path`` (1_edit.xml) through ``frame`` (frame.Frame) to ``out_path``; returns {'frames', 'clips',
    'size', 'seconds'}."""
    import time

    from .common import ffmpeg_bin
    from .export_xml_edl import parse_premiere_xml, premiere_position
    t_start = time.perf_counter()
    xml_path, out_path = Path(xml_path), Path(out_path)
    x = parse_premiere_xml(xml_path)
    fps = Fraction(int(x["timebase"]) * 1000, 1001) if str(x.get("ntsc") or "").upper() == "TRUE" \
        else Fraction(int(x["timebase"]))
    W, H = int(x["width"]), int(x["height"])
    f = min(1.0, PREVIEW_WIDTH / float(W))
    OW, OH = int(round(W * f / 2)) * 2, int(round(H * f / 2)) * 2
    raw, rw, rh, raw_fps = _raw_file(xml_path)
    n_total = int(x["duration"])
    ff = ffmpeg_bin()
    with tempfile.TemporaryDirectory(prefix="mc_preview_", dir=str(out_path.parent)) as td:
        tdir = Path(td)
        parts: list[Path] = []
        spans = _spans(x)
        cursor = 0
        for i, (c, (a, b)) in enumerate(zip(x["clips"], spans)):
            if a > cursor:                                    # a gap on V1: black under the frame
                parts.append(_still(ff, tdir / f"gap{i:03d}.mp4", frame.path, a - cursor, fps, OW, OH))
            n = b - max(a, cursor)
            if n <= 0:
                continue
            m = c.get("motion") or {}
            sc = float(m.get("scale") or 100.0) / 100.0
            ctr = m.get("center") or (0.0, 0.0)
            px, py = premiere_position(ctr, (W, H), (rw, rh))
            sw, sh = max(2, int(round(rw * sc * f))), max(2, int(round(rh * sc * f)))
            ox, oy = int(round(px * f - sw / 2.0)), int(round(py * f - sh / 2.0))
            v = abs(float(c.get("speed") or 1.0)) or 1.0
            s0 = (float(c["in"]) + (max(a, cursor) - a) * v) / float(fps)
            flip = ",hflip" if c.get("flip") else ""
            graph = (f"[0:v]setpts=(PTS-STARTPTS)/{v:.6f},fps={fps},scale={sw}:{sh}{flip},setsar=1[v];"
                     f"color=c=black:s={OW}x{OH}:r={fps}[bg];[bg][v]overlay=x={ox}:y={oy}:eof_action=repeat[base];"
                     f"[1:v]scale={OW}:{OH},format=rgba[fr];[base][fr]overlay=0:0,format=yuv420p[out]")
            p = tdir / f"clip{i:03d}.mp4"
            cmd = [ff, "-v", "error", "-y", "-ss", f"{max(0.0, s0):.6f}", "-i", raw, "-loop", "1", "-i", str(frame.path),
                   "-filter_complex", graph, "-map", "[out]", "-frames:v", str(n), "-r", str(fps), "-an",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(p)]
            subprocess.run(cmd, check=True)
            parts.append(p)
            cursor = b
        if n_total > cursor:
            parts.append(_still(ff, tdir / "tail.mp4", frame.path, n_total - cursor, fps, OW, OH))
        lst = tdir / "parts.txt"
        lst.write_text("".join(f"file '{q.as_posix()}'\n" for q in parts), encoding="utf-8")
        video = tdir / "video.mp4"
        subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        str(video)], check=True)
        audio = _audio(ff, raw, x, fps, n_total, tdir)
        cmd = [ff, "-v", "error", "-y", "-i", str(video)] + (["-i", str(audio)] if audio else []) + \
              ["-map", "0:v"] + (["-map", "1:a", "-c:a", "aac", "-b:a", "192k"] if audio else []) + \
              ["-c:v", "copy", "-movflags", "+faststart", "-shortest", str(out_path)]
        subprocess.run(cmd, check=True)
    return {"frames": n_total, "clips": len(x["clips"]), "size": [OW, OH], "fps": str(fps),
            "seconds": round(time.perf_counter() - t_start, 1), "path": str(out_path)}


def _still(ff: str, path: Path, png: str, n: int, fps: Fraction, OW: int, OH: int) -> Path:
    """n frames of black under the frame (a gap on V1, or after the last clip)."""
    graph = (f"color=c=black:s={OW}x{OH}:r={fps}[bg];[0:v]scale={OW}:{OH},format=rgba[fr];"
             f"[bg][fr]overlay=0:0,format=yuv420p[out]")
    subprocess.run([ff, "-v", "error", "-y", "-loop", "1", "-i", png, "-filter_complex", graph, "-map", "[out]",
                    "-frames:v", str(int(n)), "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    str(path)], check=True)
    return path


def _atempo(v: float) -> str:
    """ffmpeg's atempo chain for speed v (each stage 0.5 .. 2)."""
    stages = []
    while v > 2.0:
        stages.append(2.0)
        v /= 2.0
    while v < 0.5:
        stages.append(0.5)
        v /= 0.5
    stages.append(v)
    return ",".join(f"atempo={s:.6f}" for s in stages if abs(s - 1.0) > 1e-9) or "anull"


def _audio(ff: str, raw: str, x: dict, fps: Fraction, n_total: int, tdir: Path) -> Path | None:
    """A1 as one WAV: each item's RAW sound at its speed (pitch kept) at its place, short fades at its ends."""
    items = [it for it in x["audio"] if it["end"] > it["start"] >= 0]
    if not items:
        return None
    total = int(round(n_total / float(fps) * SR))
    buf = np.zeros((total + SR, 2), np.float32)
    for it in items:
        v = abs(float(it.get("speed") or 1.0)) or 1.0
        s0 = float(it["in"]) / float(fps)
        dur = (it["end"] - it["start"]) / float(fps)
        p = subprocess.run([ff, "-v", "error", "-ss", f"{max(0.0, s0):.6f}", "-t", f"{dur * v + 0.1:.6f}", "-i", raw,
                            "-vn", "-ac", "2", "-ar", str(SR), "-af", _atempo(v), "-f", "f32le", "-"],
                           capture_output=True, check=True)
        y = np.frombuffer(p.stdout, np.float32).reshape(-1, 2)
        n = int(round(dur * SR))
        y = y[:n]
        if not len(y):
            continue
        ramp = min(len(y) // 2, int(0.004 * SR))
        if ramp > 0:
            w = np.linspace(0.0, 1.0, ramp, dtype=np.float32)[:, None]
            y = y.copy()
            y[:ramp] *= w
            y[-ramp:] *= w[::-1]
        a = int(round(it["start"] / float(fps) * SR))
        buf[a:a + len(y)] += y
    import soundfile as sf
    path = tdir / "a1.wav"
    sf.write(str(path), np.clip(buf[:total], -1.0, 1.0), SR, subtype="PCM_16")
    return path
