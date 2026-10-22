"""The pauses you may want to take out (night 15, task 13 -- day 13's suggestion 2): every quiet gap of MIN_PAUSE_S or
longer between two words in 1_edit.xml -- inside one clip, or around one of its cuts ("at the cut") -- listed in
``3_pauses.txt`` next to 1_edit.xml with its place on the sequence (the timecode Premiere shows), its length and the
words around it -- so you jump to each one and decide. Nothing is cut: no rule predicts which of them you take out
(day 13: of 38 such pauses you cut 9).

The words are the captions' (``captions.words_final``: the edit's own sound transcribed, its silences cut out), each
trimmed to its AUDIBLE part on the sound A1 of 1_edit.xml plays (silence.word_cores: a recognised word's timing runs
on into the pause after it). Night 15, task 16, measured on Liquid Death against your 10 cut pauses: the gaps between
the words' timings found 0 of them; between their audible parts, inside the clips only, 3; with the pauses around the
cuts too (most of yours are trims at the tool's cuts) 8 of 10 at 0.25 s (16 listed), 5 at 0.4 s -- so the cuts' pauses
are listed and MIN_PAUSE_S is 0.25. Without A1's sound (a file that can not be read) the words' own timings are used. In no
cached stage: written after S8 speed + frame."""
from __future__ import annotations

import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np

MIN_PAUSE_S = 0.25          # a gap between two words' audible parts this long or longer is listed
EDGE_S = 0.05               # a clip edge this close to the gap counts as a cut in it
FILE_NAME = "3_pauses.txt"
SR = 16000                  # A1's sound is read at this rate for the loudness


def _rate(el: Any, default: Fraction) -> Fraction:
    if el is None:
        return default
    tb = int(el.findtext("timebase") or 0) or int(round(float(default)))
    ntsc = (el.findtext("ntsc") or "").strip().upper() == "TRUE"
    return Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def sequence_of(xml: Path) -> tuple[Fraction, list[tuple[int, int]]]:
    """(the sequence's frame rate, V1's clips as [start, end) sequence frames)."""
    root = ET.parse(xml).getroot()
    seq = root.find(".//sequence")
    if seq is None:
        raise ValueError("no sequence")
    fps = _rate(seq.find("rate"), Fraction(30))
    track = seq.find("./media/video/track")
    clips: list[tuple[int, int]] = []
    for ci in (track.findall("clipitem") if track is not None else []):
        try:
            a, b = int(ci.findtext("start") or -1), int(ci.findtext("end") or -1)
        except ValueError:
            continue
        if a >= 0 and b > a:
            clips.append((a, b))
    return fps, sorted(clips)


PC_SHARE, PC_LOCAL = "//192.168.101.11/shared/", "D:/Shared/"     # the PC's D:\Shared as the laptop names it


def _path_of(u: str) -> Path:
    p = urllib.parse.urlparse(u)
    s = urllib.parse.unquote(p.path)
    if p.netloc and p.netloc.lower() != "localhost":
        unc = "//" + p.netloc + s
        if unc.lower().startswith(PC_SHARE) and Path(PC_LOCAL).is_dir():     # on the PC itself: its own disk
            local = Path(PC_LOCAL + unc[len(PC_SHARE):])
            if local.exists():
                return local
        return Path(unc)
    return Path(s[1:] if s[:1] == "/" and s[2:3] == ":" else s)


def _speed(ci: Any) -> float:
    """A clip's speed (Time Remap's 'speed' in %; 1.0 without one)."""
    for par in ci.iter("parameter"):
        if (par.findtext("parameterid") or "").strip().lower() == "speed":
            try:
                return max(1e-3, abs(float(par.findtext("value") or 100.0)) / 100.0)
            except ValueError:
                pass
    return 1.0


def a1_sound(xml: Path) -> np.ndarray | None:
    """The sound A1 of 1_edit.xml plays (mono, SR), on the sequence's clock; None when it can not be read."""
    from .common import ffmpeg_bin
    root = ET.parse(xml).getroot()
    seq = root.find(".//sequence")
    fps = _rate(seq.find("rate"), Fraction(30))
    files = {f.get("id"): _path_of(f.findtext("pathurl")) for f in root.iter("file")
             if f.get("id") and f.findtext("pathurl")}
    n = int(seq.findtext("duration") or 0)
    track = seq.find("./media/audio/track")
    if track is None or n <= 0:
        return None
    y = np.zeros(int(n / float(fps) * SR) + SR, np.float32)
    got = 0
    for ci in track.findall("clipitem"):
        try:
            a, b, i = int(ci.findtext("start")), int(ci.findtext("end")), int(ci.findtext("in"))
        except (TypeError, ValueError):
            continue
        f = ci.find("file")
        src = files.get(f.get("id")) if f is not None else None
        if src is None or a < 0 or b <= a or not src.exists():
            continue
        k = _speed(ci)
        dur_seq = (b - a) / float(fps)
        out = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{i / float(fps):.4f}", "-t", f"{dur_seq * k:.4f}",
                              "-i", str(src), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                             capture_output=True, timeout=600)
        seg = np.frombuffer(out.stdout, np.float32)
        if not len(seg):
            continue
        want = int(round(dur_seq * SR))
        if abs(k - 1.0) > 1e-6 and want > 0:                 # a sped-up clip: its loudness on the sequence's clock
            seg = np.interp(np.linspace(0, len(seg) - 1, want), np.arange(len(seg)), seg).astype(np.float32)
        s0 = int(round(a / float(fps) * SR))
        seg = seg[: max(0, len(y) - s0)]
        y[s0:s0 + len(seg)] = seg
        got += 1
    return y if got else None


def audible(words: Sequence[Sequence[Any]], y: np.ndarray | None) -> list[tuple[str, float, float]]:
    """Each word's audible part on A1's sound (silence.word_cores); the words' own timings without the sound."""
    ws = sorted(((str(w[0]), float(w[1]), float(w[2])) for w in words if len(w) >= 3), key=lambda w: (w[1], w[2]))
    if y is None or not len(y) or not ws:
        return ws
    from . import silence as SI
    t, db = SI.loudness(y, SR)
    lv = SI.levels(db, SI.Settings())
    out: list[tuple[str, float, float]] = []
    for w in ws:
        for a, b in SI.word_cores([SimpleNamespace(text=w[0], start=w[1], end=w[2])], t, db, lv["noise_db"]):
            if b > a:
                out.append((w[0], float(a), float(b)))
    return sorted(out, key=lambda w: (w[1], w[2])) or ws


def timecode(seconds: float, fps: Fraction) -> str:
    """00:00:05:12 (the sequence's frames)."""
    n = int(round(float(seconds) * float(fps)))
    base = int(round(float(fps)))
    f = n % base
    s = n // base
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}:{f:02d}"


def pauses(words: Sequence[Sequence[Any]], fps: Fraction, clips: list[tuple[int, int]],
           min_s: float = MIN_PAUSE_S) -> list[dict]:
    """The gaps of ``min_s`` or longer between two words ([text, start s, end s], sequence seconds: their audible
    parts when known) inside one clip."""
    ws = sorted(((str(w[0]), float(w[1]), float(w[2])) for w in words if len(w) >= 3), key=lambda w: (w[1], w[2]))
    edges = sorted({a for a, _b in clips} | {b for _a, b in clips})
    out = []
    for (t0, _s0, e0), (t1, s1, _e1) in zip(ws, ws[1:]):
        if s1 - e0 < min_s:
            continue
        lo, hi = (e0 - EDGE_S) * float(fps), (s1 + EDGE_S) * float(fps)
        at_cut = any(lo <= e <= hi for e in edges)      # a cut in the gap: the pause around a cut (you trim those too)
        clip = next((k for k, (a, b) in enumerate(clips, 1) if a <= e0 * float(fps) < b), None)
        nxt = next((k for k, (a, b) in enumerate(clips, 1) if a <= s1 * float(fps) < b), None)
        if clip is None and nxt is None:
            continue
        out.append({"start": round(e0, 3), "end": round(s1, 3), "seconds": round(s1 - e0, 2), "before": t0,
                    "after": t1, "clip": clip or nxt, "next_clip": nxt, "at_cut": at_cut,
                    "tc": timecode(e0, fps), "tc_end": timecode(s1, fps)})
    return out


def text(found: list[dict], min_s: float = MIN_PAUSE_S) -> str:
    lines = [f"Pauses of {min_s:.2f} s or more between two words in 1_edit.xml -- inside a clip, or around a cut (you "
             "trim those too) -- each one you may want to take out by hand; nothing was cut.", ""]
    if not found:
        lines.append("None.")
    for k, p in enumerate(found, 1):
        where = (f"at the cut, clips {p['clip']}-{p['next_clip']}" if p.get("at_cut") and p.get("next_clip") and
                 p.get("next_clip") != p.get("clip") else ("at a cut" if p.get("at_cut") else f"clip {p['clip']}"))
        lines.append(f"{k:2d}. {p['tc']} - {p['tc_end']}  {p['seconds']:.2f} s  {where}:  "
                     f"\"{p['before']}\" ... \"{p['after']}\"")
    total = sum(p["seconds"] for p in found)
    lines += ["", f"{len(found)} pause(s), {total:.2f} s in all."]
    return "\r\n".join(lines) + "\r\n"


def write(run_dir: Path, words: Sequence[Sequence[Any]] | None) -> dict:
    """3_pauses.txt in the run folder (from 1_edit.xml's V1 and A1 and the captions' words); never raises."""
    try:
        xml = Path(run_dir) / "1_edit.xml"
        if not words or not xml.is_file():
            return {"path": None, "pauses": []}
        fps, clips = sequence_of(xml)
        try:
            y = a1_sound(xml)
        except Exception:  # noqa: BLE001 - the words' own timings then
            y = None
        found = pauses(audible(words, y), fps, clips)
        p = Path(run_dir) / FILE_NAME
        p.write_text(text(found), encoding="utf-8")
        return {"path": str(p), "pauses": found, "audible": y is not None}
    except Exception as e:  # noqa: BLE001 - a list to help you, never a stop
        return {"path": None, "pauses": [], "error": f"{type(e).__name__}: {e}"}
