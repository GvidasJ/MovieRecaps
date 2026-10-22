"""The pauses you may want to take out (night 15, task 13 -- day 13's suggestion 2): every quiet gap of MIN_PAUSE_S or
longer between two words INSIDE one clip of 1_edit.xml (a gap at a cut is the cut's business), listed in
``3_pauses.txt`` next to 1_edit.xml with its place on the sequence (the timecode Premiere shows), its length and the
words around it -- so you jump to each one and decide. Nothing is cut: no rule predicts which of them you take out
(day 13: of 38 such pauses you cut 9).

The words are the captions' (``captions.words_final``: the edit's own sound transcribed, its silences cut out -- the
sound A1 of 1_edit.xml plays), the clips are V1's of 1_edit.xml. In no cached stage: written after the captions."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

MIN_PAUSE_S = 0.40          # a gap between two words this long or longer is listed
EDGE_S = 0.05               # a clip edge this close to the gap counts as a cut in it
FILE_NAME = "3_pauses.txt"


def sequence_of(xml: Path) -> tuple[Fraction, list[tuple[int, int]]]:
    """(the sequence's frame rate, V1's clips as [start, end) sequence frames)."""
    root = ET.parse(xml).getroot()
    seq = root.find(".//sequence")
    if seq is None:
        raise ValueError("no sequence")
    rate = seq.find("rate")
    tb = int((rate.findtext("timebase") if rate is not None else None) or 30)
    ntsc = ((rate.findtext("ntsc") if rate is not None else "") or "").strip().upper() == "TRUE"
    fps = Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)
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


def timecode(seconds: float, fps: Fraction) -> str:
    """00:00:05:12 (the sequence's frames)."""
    n = int(round(float(seconds) * float(fps)))
    base = int(round(float(fps)))
    f = n % base
    s = n // base
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}:{f:02d}"


def pauses(words: Sequence[Sequence[Any]], fps: Fraction, clips: list[tuple[int, int]],
           min_s: float = MIN_PAUSE_S) -> list[dict]:
    """The gaps of ``min_s`` or longer between two words ([text, start s, end s], sequence seconds) inside one clip."""
    ws = sorted(((str(w[0]), float(w[1]), float(w[2])) for w in words if len(w) >= 3), key=lambda w: (w[1], w[2]))
    edges = sorted({a for a, _b in clips} | {b for _a, b in clips})
    out = []
    for (t0, _s0, e0), (t1, s1, _e1) in zip(ws, ws[1:]):
        if s1 - e0 < min_s:
            continue
        lo, hi = (e0 - EDGE_S) * float(fps), (s1 + EDGE_S) * float(fps)
        if any(lo <= e <= hi for e in edges):
            continue                                    # a cut in the gap: not a pause inside a take
        clip = next((k for k, (a, b) in enumerate(clips, 1) if a <= e0 * float(fps) < b), None)
        if clip is None:
            continue
        out.append({"start": round(e0, 3), "end": round(s1, 3), "seconds": round(s1 - e0, 2), "before": t0,
                    "after": t1, "clip": clip, "tc": timecode(e0, fps), "tc_end": timecode(s1, fps)})
    return out


def text(found: list[dict], min_s: float = MIN_PAUSE_S) -> str:
    lines = [f"Pauses of {min_s:.2f} s or more inside a clip of 1_edit.xml (between two words) -- each one you may "
             "want to take out by hand; nothing was cut.", ""]
    if not found:
        lines.append("None.")
    for k, p in enumerate(found, 1):
        lines.append(f"{k:2d}. {p['tc']} - {p['tc_end']}  {p['seconds']:.2f} s  clip {p['clip']}:  "
                     f"\"{p['before']}\" ... \"{p['after']}\"")
    total = sum(p["seconds"] for p in found)
    lines += ["", f"{len(found)} pause(s), {total:.2f} s in all."]
    return "\r\n".join(lines) + "\r\n"


def write(run_dir: Path, words: Sequence[Sequence[Any]] | None) -> dict:
    """3_pauses.txt in the run folder (from 1_edit.xml's V1 and the captions' words); never raises."""
    try:
        xml = Path(run_dir) / "1_edit.xml"
        if not words or not xml.is_file():
            return {"path": None, "pauses": []}
        fps, clips = sequence_of(xml)
        found = pauses(words, fps, clips)
        p = Path(run_dir) / FILE_NAME
        p.write_text(text(found), encoding="utf-8")
        return {"path": str(p), "pauses": found}
    except Exception as e:  # noqa: BLE001 - a list to help you, never a stop
        return {"path": None, "pauses": [], "error": f"{type(e).__name__}: {e}"}
