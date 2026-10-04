"""shots.py: the RAW's own shot changes, so a clip of my edit never flashes a sliver of another shot.

The RAW is often an edited video itself (an interview with its own cuts). A clip of my edit whose start or end
reaches a few frames over one of those shot changes shows a frame or two of a different shot -- a flash frame.
``raw_shot_changes`` finds the shot changes of the RAW where the edit plays it: thumbnails (64x36 gray) of every frame,
a change where the picture differs from the frame before far more than it moves around it. The rules that use them
(speech.snap_edits, silence.removal_ranges): padding stops at a shot change, and no clip starts or ends with a piece of
a shot shorter than MIN_SHOT_S. ``flash_problems`` is the hard check on the final XML: every run of frames of one RAW
shot in the edit (across cuts that stay in that shot) must last MIN_SHOT_S.
"""
from __future__ import annotations

import bisect
import math
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

MIN_SHOT_S = 0.25         # no piece of a shot shorter than this at a clip's start or end
THUMB = (64, 36)          # thumbnail size (gray) the shot changes are found on
CHANGE_ABS = 8.0          # a shot change: the mean absolute difference to the frame before at least this ...
CHANGE_RATIO = 4.0        # ... and this many times the frame-to-frame difference around it (camera moves, noise)
AROUND = 6                # frames on each side the "around" level is taken from
MARGIN_S = 2.0            # the RAW looked at this far around what the edit plays (an edge may move that far)
VERSION = 2


def changes_of(thumbs: np.ndarray) -> list[int]:
    """Indices k (into ``thumbs``, frames in order) where frame k starts a new shot."""
    f = np.asarray(thumbs, np.float32)
    if len(f) < 2:
        return []
    d = np.concatenate([[0.0], np.abs(np.diff(f, axis=0)).mean(axis=tuple(range(1, f.ndim)))])
    out = []
    for k in range(1, len(d)):
        if d[k] < CHANGE_ABS:
            continue
        around = np.concatenate([d[max(1, k - AROUND):k], d[k + 1:k + 1 + AROUND]])
        level = float(np.median(around)) if len(around) else 0.0
        if d[k] >= CHANGE_RATIO * max(level, 1.0):
            out.append(k)
    return out


def _windows(ranges_s: Sequence[tuple[float, float]], fps: Fraction, n: int) -> list[tuple[int, int]]:
    f = float(fps)
    rs = sorted((max(0, int(math.floor((a - MARGIN_S) * f))), min(n, int(math.ceil((b + MARGIN_S) * f)) + 1))
                for a, b in ranges_s if b > a)
    out: list[list[int]] = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out if b > a]


def raw_shot_changes(info: Any, ranges_s: Sequence[tuple[float, float]] | None = None, cache: Any = None,
                     proxy: Any = None) -> list[int]:
    """RAW frame indices (its own fps) where a new shot starts, inside ``ranges_s`` (seconds, +- MARGIN_S; None:
    the whole RAW) -- and the edges of the stretches looked at (what lies beyond them was not seen: two frames on
    either side of one are never taken for one shot). From the dense analysis proxy when there is one, else decoded
    (media.VideoReader). Cached by the file's hash and the frames looked at."""
    fps = Fraction(info.fps)
    n = int(info.nb_frames)
    wins = _windows(ranges_s if ranges_s is not None else [(0.0, n / float(fps))], fps, n)
    if not wins:
        return []

    def compute() -> dict:
        out: list[int] = []
        for a, b in wins:
            th = _thumbs(info, a, b, proxy)
            out += [a + k for k in changes_of(th)] + [k for k in (a, b) if 0 < k < n]
        return {"changes": sorted(set(out))}
    if cache is not None and getattr(info, "file_hash", None):
        from .common import stage_key
        key = stage_key("raw_shots", info.file_hash, wins, THUMB, CHANGE_ABS, CHANGE_RATIO, AROUND, VERSION)
        return [int(k) for k in cache.json("raw_shots", key, compute)["changes"]]
    return compute()["changes"]


def _thumbs(info: Any, a: int, b: int, proxy: Any = None) -> np.ndarray:
    """Gray thumbnails of RAW frames [a, b)."""
    import cv2
    if proxy is not None and getattr(proxy, "index_map", None) is None and len(getattr(proxy, "frames", ())) >= b:
        return np.stack([cv2.resize(np.asarray(proxy.frames[j]), THUMB, interpolation=cv2.INTER_AREA)
                         for j in range(a, b)])
    from .probe import reader_sar, video_stream_ordinal
    from .proxies import _reader, iter_selected_frames
    rd = _reader(info.path, Fraction(info.fps), video_stream_ordinal(info), int(getattr(info, "rotation", 0) or 0),
                 reader_sar(info))
    try:
        got = dict(iter_selected_frames(rd, range(a, b), THUMB))
    finally:
        close = getattr(rd, "close", None)
        if close is not None:
            close()
    return np.stack([got[j] for j in range(a, b)])


def seconds(changes: Sequence[int], fps: Fraction) -> list[float]:
    """The shot changes as RAW times: the new shot shows from this time on."""
    f = float(Fraction(fps))
    return [k / f for k in changes]


def shot_of(t: float, changes_s: Sequence[float]) -> int:
    """The shot a RAW time shows (0 = before the first change)."""
    return bisect.bisect_right(changes_s, t + 1e-9)


def flash_problems(items: Sequence[dict], fps: Fraction, changes_s: Sequence[float], n_frames: int | None = None,
                   min_s: float = MIN_SHOT_S) -> list[str]:
    """The hard flash check of an edit's V1 items [{label, start, end, in, out, speed}] (sequence frames at ``fps``,
    ``in`` the source position at the sequence rate; an item with speed None is not the RAW; one with ``allowed``
    is a stretch left empty on purpose -- another video's, filled by hand -- of any length): every run of
    consecutive frames showing one RAW shot -- across cuts that stay in the same shot -- lasts at least ``min_s``.
    One line per shorter run (a flash frame), unless the run is the whole edit."""
    f = float(Fraction(fps))
    runs: list[list] = []                 # [shot key, first frame, end frame, labels]
    for it in sorted(items, key=lambda d: d["start"]):
        s, e = int(it["start"]), int(it["end"])
        if e <= s:
            continue
        if runs and runs[-1][2] < s:      # nothing on V1 in between: black frames
            runs.append([("black", s), runs[-1][2], s, ["(empty)"]])
        v = it.get("speed")
        if it.get("allowed"):                 # a stretch left empty on purpose (another video): any length
            keys = [(("allowed", it.get("label")), s, e)]
        elif v is None:
            keys = [(("other", it.get("label")), s, e)]
        else:
            keys = []
            for r in range(s, e):
                t = (float(it["in"]) + (r - s) * float(v)) / f
                k = ("raw", shot_of(t, changes_s))
                if keys and keys[-1][0] == k and keys[-1][2] == r:
                    keys[-1] = (k, keys[-1][1], r + 1)
                else:
                    keys.append((k, r, r + 1))
        for k, a, b in keys:
            if runs and runs[-1][0] == k and runs[-1][2] == a:
                runs[-1][2] = b
                if it.get("label") not in runs[-1][3]:
                    runs[-1][3].append(it.get("label"))
            else:
                runs.append([k, a, b, [it.get("label")]])
    if len(runs) <= 1:
        return []
    out = []
    need = int(math.ceil(min_s * f - 1e-9))
    for k, a, b, labels in runs:
        if b - a < need and k[0] != "allowed":
            h = int(round(f))
            tc = f"{a // (3600 * h):02d}:{a // (60 * h) % 60:02d}:{a // h % 60:02d}:{a % h:02d}"
            what = {"raw": "a different RAW shot", "black": "nothing (black)"}.get(k[0], "a clip")
            out.append(f"{'+'.join(str(x) for x in labels)} at {tc}: {b - a} frame(s) of {what} "
                       f"({(b - a) / f:.2f} s, under {min_s:g} s) -- a flash frame")
    return out
