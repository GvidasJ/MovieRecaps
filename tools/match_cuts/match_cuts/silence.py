"""Silence removal (Premiere export; ``--keep-silence`` turns it off): the silences of MY edit's audio -- the RAW audio
under my clips, never the competitor's, so music it added does not count as speech -- are cut out of the sequence.
In competitor mode this runs after the competitor's cuts are recreated (pipeline.stage_exports); without a
competitor the RAW alone is cut this way (pipeline.run_raw_only).

Silence: the short-window loudness (RMS over WIN_S, every HOP_S; a louder blip under BLIP_S is a click, not
speech -- single peaks never count) stays below ``--silence-db``
(default -20 dB) for longer than ``--min-silence`` (default 0.35 s). The dB are relative to the edit's own speech
level, the loudness of its loudest 5% of windows (SPEECH_PCT), so the same setting works whatever the recording
gain: on input/raw_test.mp4 (speech at about -16 dBFS, half its windows below -26 dBFS) an absolute -20 dBFS would
have called half the speech silence. Of each silence, ``--pad-after`` (0.12 s) after the speech before it and
``--pad-before`` (0.08 s) before the speech after it are kept, so words are never clipped; at the very start and end
of the edit there is no speech to protect. The cut points land on whole sequence frames (rounded inwards: never more
is removed than the silence), and never inside a cross dissolve.

The cuts: every clip, audio clip and marker after a removed range moves earlier by the time removed before it
(Ripple); a clip that spans a removed range is split around it. No click: the audio on both sides of every cut
fades over FADE_FRAMES (Audio Levels keyframes in the XML; the same fades in the audio the captions are made from),
and as the cuts lie inside silences the fades only touch the quiet room tone.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

SILENCE_DB = -20.0        # silence: this far below the speech level ...
MIN_SILENCE_S = 0.35      # ... for longer than this
PAD_BEFORE_S = 0.08       # kept before the speech that follows a silence
PAD_AFTER_S = 0.12        # kept after the speech that precedes a silence
WIN_S = 0.05              # loudness window (RMS) ...
HOP_S = 0.01              # ... every HOP_S
SPEECH_PCT = 95           # the speech level: this percentile of the windows' loudness
BLIP_S = 0.08             # a louder stretch shorter than this inside a silence is a click / peak, not speech
FADE_FRAMES = 1           # audio fade on each side of a cut (sequence frames)


@dataclass
class Settings:
    db: float = SILENCE_DB
    min_s: float = MIN_SILENCE_S
    pad_before: float = PAD_BEFORE_S
    pad_after: float = PAD_AFTER_S

    @classmethod
    def from_cfg(cls, cfg: Any) -> "Settings":
        def get(name: str, default: float) -> float:
            v = getattr(cfg, name, None)
            return float(default if v is None else v)
        return cls(get("silence_db", SILENCE_DB), get("min_silence", MIN_SILENCE_S),
                   get("pad_before", PAD_BEFORE_S), get("pad_after", PAD_AFTER_S))


def loudness(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(window centres in seconds, loudness in dBFS) of mono audio: RMS over WIN_S every HOP_S."""
    y = np.asarray(y, np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    w, hop = max(1, int(round(WIN_S * sr))), max(1, int(round(HOP_S * sr)))
    if len(y) < w:
        return np.zeros(0), np.zeros(0)
    n = (len(y) - w) // hop + 1
    c = np.concatenate([[0.0], np.cumsum(y * y)])
    starts = hop * np.arange(n)
    ms = (c[starts + w] - c[starts]) / w
    return (starts + w / 2.0) / sr, 10.0 * np.log10(ms + 1e-12)


def silent_runs(y: np.ndarray, sr: int, st: Settings) -> tuple[list[tuple[float, float]], float]:
    """([(start s, end s)] of every stretch whose loudness stays below the speech level + st.db for longer than
    st.min_s, the threshold in dBFS)."""
    t, db = loudness(y, sr)
    dur = len(y) / float(sr)
    if not len(db):
        return [], 0.0
    thr = float(np.percentile(db, SPEECH_PCT)) + st.db
    q = db < thr
    blip = int(round(BLIP_S / HOP_S))
    edges = np.flatnonzero(np.diff(np.concatenate([[True], q, [True]]).astype(np.int8)))
    for i0, i1 in zip(edges[::2], edges[1::2]):        # louder stretches i0 .. i1 - 1 between quiet ones
        if i1 - i0 < blip and i0 > 0 and i1 < len(q):
            q[i0:i1] = True
    quiet = np.concatenate([[False], q, [False]])
    edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    out = []
    for i0, i1 in zip(edges[::2], edges[1::2]):        # windows i0 .. i1 - 1 are quiet
        a = 0.0 if i0 == 0 else float(t[i0]) - HOP_S / 2.0          # (the first / last window reaches the edge)
        b = dur if i1 == len(db) else min(dur, float(t[i1 - 1]) + HOP_S / 2.0)
        if b - a > st.min_s:
            out.append((a, b))
    return out, thr


@dataclass
class Cut:
    """One removed range: sequence frames [a, b) of the edit before removal, inside the silence s0..s1 (seconds)."""
    a: int
    b: int
    s0: float
    s1: float

    @property
    def frames(self) -> int:
        return self.b - self.a


def removal_ranges(y: np.ndarray, sr: int, fps: Fraction, n_frames: int, st: Settings,
                   protect: Sequence[tuple[int, int]] = ()) -> tuple[list[Cut], float]:
    """(the ranges to remove, in sequence frames, the silence threshold in dBFS): each silence minus the pads
    (none at the edit's start / end), rounded inwards to whole frames, and never inside a protected range (a cross
    dissolve)."""
    f = float(fps)
    runs, thr = silent_runs(y, sr, st)
    dur = n_frames / f
    cuts: list[Cut] = []
    for s0, s1 in runs:
        lo = s0 + (st.pad_after if s0 > 1e-6 else 0.0)
        hi = s1 - (st.pad_before if s1 < dur - 1e-6 else 0.0)
        a = 0 if s0 <= 1e-6 else int(math.ceil(lo * f - 1e-9))
        b = n_frames if s1 >= dur - 1e-6 else int(math.floor(hi * f + 1e-9))
        pieces = [(max(0, a), min(n_frames, b))]
        for p0, p1 in protect:
            pieces = [q for x0, x1 in pieces for q in ((x0, min(x1, p0)), (max(x0, p1), x1)) if q[1] > q[0]]
        cuts += [Cut(x0, x1, s0, s1) for x0, x1 in pieces if x1 > x0]
    if cuts and sum(c.frames for c in cuts) >= n_frames:        # all silent: keep the edit rather than nothing
        cuts = []
    return cuts, thr


@dataclass
class Ripple:
    """The timeline after removing ``cuts`` (sorted, disjoint [a, b) sequence frames) from n_frames."""
    cuts: list[Cut] = field(default_factory=list)
    n_frames: int = 0

    def __post_init__(self) -> None:
        self.cuts = sorted(self.cuts, key=lambda c: c.a)

    @property
    def removed(self) -> int:
        return sum(c.frames for c in self.cuts)

    @property
    def new_frames(self) -> int:
        return self.n_frames - self.removed

    def map(self, f: int) -> int:
        """New frame of old frame f (a frame inside a removed range maps to where that range was)."""
        shift = 0
        for c in self.cuts:
            if f >= c.b:
                shift += c.frames
            elif f > c.a:
                return c.a - shift
            else:
                break
        return f - shift

    def keep(self, a: int, b: int) -> list[tuple[int, int]]:
        """The parts of old range [a, b) that are kept."""
        out = [(a, b)]
        for c in self.cuts:
            out = [q for x0, x1 in out for q in ((x0, min(x1, c.a)), (max(x0, c.b), x1)) if q[1] > q[0]]
        return out

    def joins(self) -> set[int]:
        """Old frames where a cut was made: the start and the end of every removed range."""
        return {c.a for c in self.cuts} | {c.b for c in self.cuts}

    def seconds(self, fps: Fraction) -> list[tuple[float, float]]:
        f = float(fps)
        return [(c.a / f, c.b / f) for c in self.cuts]


def fade_samples(sr: int, fps: Fraction) -> int:
    return max(1, int(round(FADE_FRAMES * sr / float(fps))))


def cut_audio(y: np.ndarray, sr: int, fps: Fraction, rp: Ripple) -> np.ndarray:
    """The edit's audio with the removed ranges taken out, faded over FADE_FRAMES on both sides of every cut
    (exactly what A1 plays with the XML's Audio Levels keys)."""
    y = np.asarray(y, np.float32)
    if not rp.cuts:
        return y.copy()
    f = float(fps)
    nf = fade_samples(sr, fps)
    parts = []
    for a, b in rp.keep(0, rp.n_frames):
        s0, s1 = int(round(a * sr / f)), min(len(y), int(round(b * sr / f)))
        seg = y[s0:s1].copy()
        if len(seg):
            ramp = np.linspace(0.0, 1.0, min(nf, len(seg)), endpoint=False, dtype=np.float32)
            if a in rp.joins() and a > 0:
                seg[:len(ramp)] *= ramp[(slice(None),) + (None,) * (seg.ndim - 1)]
            if b in rp.joins() and b < rp.n_frames:
                seg[len(seg) - len(ramp):] *= ramp[::-1][(slice(None),) + (None,) * (seg.ndim - 1)]
        parts.append(seg)
    return np.concatenate(parts) if parts else y[:0].copy()


def apply_premiere(clips: list, audio: list[dict], markers: list[dict], rp: Ripple) -> tuple[list, list[dict], list[dict]]:
    """(V1 clips, A1 items, markers) of the Premiere export after the removal: every piece that is kept, moved
    earlier by the time removed before it; a clip spanning a removed range becomes two clips (the same source
    continuing from where the removed time ends). A1 pieces carry ``fade_in`` / ``fade_out`` where a cut was made."""
    if not rp.cuts:
        return clips, audio, markers
    joins = rp.joins()
    out_c = []
    for cl in clips:
        lo = cl.rec_start if cl.start == -1 else cl.start
        hi = cl.rec_end if cl.end == -1 else cl.end
        for a, b in rp.keep(lo, hi):
            n_in = cl.src_in if a == lo else cl.src_in + int(round((a - lo) * cl.speed))
            out_c.append(dataclasses.replace(
                cl, start=-1 if (cl.start == -1 and a == lo) else rp.map(a),
                end=-1 if (cl.end == -1 and b == hi) else rp.map(a) + (b - a),
                rec_start=rp.map(a), rec_end=rp.map(a) + (b - a), src_in=n_in,
                src_out=cl.src_out if b == hi else n_in + int(round((b - a) * cl.speed))))
    out_a = []
    for it in audio:
        for a, b in rp.keep(it["start"], it["end"]):
            n_in = it["in"] if a == it["start"] else it["in"] + int(round((a - it["start"]) * it["speed"]))
            out_a.append(dict(it, start=rp.map(a), end=rp.map(a) + (b - a), **{"in": n_in},
                              out=it["out"] if b == it["end"] else n_in + int(round((b - a) * it["speed"])),
                              fade_in=a in joins and a > 0, fade_out=b in joins and b < rp.n_frames))
    out_m = [dict(m, **{"in": rp.map(m["in"]), "out": max(rp.map(m["in"]), rp.map(m["out"]))}) for m in markers]
    return out_c, out_a, out_m


def a1_audio(audio: list[dict], raw_audio: np.ndarray, sr: int, fps: Fraction, n_frames: int) -> np.ndarray:
    """What A1 plays (the RAW audio under my clips, at sr): every A1 item's RAW audio at its record range."""
    from .render_preview import sample_positions
    f = float(fps)
    x = np.asarray(raw_audio, np.float32)
    out = np.zeros(int(round(n_frames * sr / f)), np.float32)
    for it in audio:
        n0, n1 = int(round(it["start"] * sr / f)), min(len(out), int(round(it["end"] * sr / f)))
        if n1 > n0 and len(x):
            y = sample_positions(x, it["in"] / f * sr, float(it["speed"]), n1 - n0)
            out[n0:n1] += y if y.ndim == 1 else y.mean(axis=1)
    return out


def plan_premiere(cutlist: Any, raw_audio: np.ndarray | None, sr: int, cfg: Any = None) -> dict:
    """The silence removal of the Premiere export of ``cutlist``: {cuts, ripple, threshold_db, rows, removed_s,
    old_s, new_s, settings}. Measured on A1 (the RAW audio under the clips), never inside a cross dissolve."""
    from .export_xml_edl import premiere_audio, premiere_clips, premiere_factor, premiere_settings
    st = Settings.from_cfg(cfg)
    fps = premiere_settings(cfg)["fps"]
    fac = premiere_factor(cutlist.comp_fps, fps)
    n_frames = int(cutlist.competitor["frames"]) * fac
    clips, _, _ = premiere_clips(cutlist, cfg)
    audio = premiere_audio(cutlist, clips, cfg) if bool(cutlist.raw.get("has_audio", True)) else []
    protect = [(cl.rec_start, cl.rec_start + int(cl.ev.dissolve_in) * fac) for cl in clips if cl.start == -1]
    if raw_audio is None or not len(raw_audio) or not audio:
        cuts, thr = [], 0.0
    else:
        y = a1_audio(audio, raw_audio, sr, fps, n_frames)
        cuts, thr = removal_ranges(y, sr, fps, n_frames, st, protect)
    return summarize(cuts, n_frames, fps, st, thr)


def summarize(cuts: list[Cut], n_frames: int, fps: Fraction, st: Settings, thr: float) -> dict:
    """The plan as the pipeline / report / summary use it."""
    rp = Ripple(list(cuts), n_frames)
    f = float(fps)
    rows = [{"start_s": round(c.a / f, 3), "end_s": round(c.b / f, 3), "len_s": round(c.frames / f, 3),
             "new_at_s": round(rp.map(c.a) / f, 3), "a": c.a, "b": c.b} for c in rp.cuts]
    return {"cuts": [(c.a, c.b) for c in rp.cuts], "ripple": rp, "threshold_db": round(thr, 1),
            "rows": rows, "removed_s": round(rp.removed / f, 3), "old_s": round(n_frames / f, 3),
            "new_s": round(rp.new_frames / f, 3), "fps": str(fps), "settings": dataclasses.asdict(st)}


def ripple_pieces(pieces: Sequence[Any], rp: Ripple, fps: Fraction) -> list[Any]:
    """caption_recheck.Piece maps of the edit before removal -> of the edit after it (split and moved)."""
    f = float(fps)
    out = []
    for p in pieces:
        a0, b0 = int(round(p.t0 * f)), int(round(p.t1 * f))
        for a, b in rp.keep(a0, b0):
            t0 = rp.map(a) / f
            out.append(dataclasses.replace(p, t0=t0, t1=t0 + (b - a) / f, src0=p.src(a / f)))
    return out


def tc(seconds: float) -> str:
    """mm:ss.cc for the summary."""
    s = max(0.0, float(seconds))
    return f"{int(s // 60):02d}:{s % 60:05.2f}"
