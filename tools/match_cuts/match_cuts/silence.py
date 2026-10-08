"""Silence removal (Premiere export; ``--keep-silence`` turns it off): the silences of MY edit's audio -- the RAW audio
under my clips, never the competitor's, so music it added does not count as speech -- are cut out of the sequence.
In competitor mode this runs after the competitor's cuts are recreated and moved off speech (speech.py: every clip
ends ``--pad-after`` after its last word and starts ``--pad-before`` before its first; pipeline.stage_exports);
without a competitor the RAW alone is cut this way (raw_only.run_raw_only).

Silence: the short-window loudness (RMS over WIN_S, every HOP_S; a louder blip under BLIP_S is a click, not
speech -- single peaks never count) stays below this video's silence threshold for longer than ``--min-silence``
(default 0.3 s), outside every transcribed word. The threshold adapts to each video: its speech level (the loudness
of its loudest 5% of windows) and its background noise (its quietest 10%) are measured, and the threshold sits a
third of the way from the noise up to the speech, so the pauses of a noisy video are cut too (``--silence-db`` sets
it that many dB under the speech level instead). Words: the edit's audio is transcribed (word timings) and a cut
only ever falls in a gap between words -- each word's timing trimmed to its audible part, so a timing that runs on
into the pause does not keep the pause -- and the soft end or start of a sound (the windows next to it still SOFT_DB
above the noise, at most SOFT_MAX_S) belongs to it. Of each gap, ``--pad-after`` (0.05 s) after the word before it
and ``--pad-before`` (0.03 s) before the word after it are kept; at the very start and end of the edit there is no
word to protect. The cut points land on whole sequence frames (rounded inwards: never more
is removed than the silence), and never inside a cross dissolve.

The cuts: every clip, audio clip and marker after a removed range moves earlier by the time removed before it
(Ripple: the speech-safe cuts first, then the silences and repeats); a clip that spans a removed range is split
around it; a clip extended so its speech can finish moves everything after it later. No click: the audio on both sides of every cut
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

MIN_SILENCE_S = 0.3       # cut silences longer than this ...
PAD_BEFORE_S = 0.03       # ... keeping this much before each word (or other sound) that follows
PAD_AFTER_S = 0.05        # ... and this much after each word (or other sound) that precedes (config.pad_after)
WIN_S = 0.05              # loudness window (RMS) ...
HOP_S = 0.01              # ... every HOP_S
SPEECH_PCT = 95           # the speech level: this percentile of the windows' loudness
NOISE_PCT = 10            # the background noise: this percentile (digital silence below FLOOR_DB ignored)
FLOOR_DB = -90.0
THRESHOLD_FRAC = 0.35     # silence threshold: this fraction of the way from the noise up to the speech level ...
MIN_ABOVE_NOISE_DB = 3.0  # ... at least this far above the noise ...
MIN_BELOW_SPEECH_DB = 6.0  # ... and at least this far below the speech
WORD_SOUND_DB = 6.0       # a word's audible part: its windows at least this far above the noise
BLIP_S = 0.08             # a louder stretch shorter than this inside a silence is a click / peak, not speech
MAX_IN_WORD_QUIET_S = 0.25  # a word's timing holding a quiet stretch this long is two sounds (a misplaced timing)
SOFT_DB = 3.0             # a sound's soft start / end: the windows next to it still this far above the noise ...
SOFT_MAX_S = 0.2          # ... for at most this long (the soft "s", "f", "-ty five" a word starts or ends with)
FADE_FRAMES = 1           # audio fade on each side of a cut (sequence frames)


@dataclass
class Settings:
    db: float | None = None          # --silence-db: the threshold this many dB under the speech level (None: set
    min_s: float = MIN_SILENCE_S     # from the video's speech level and background noise)
    pad_before: float = PAD_BEFORE_S
    pad_after: float = PAD_AFTER_S

    @classmethod
    def from_cfg(cls, cfg: Any) -> "Settings":
        def get(name: str, default: float) -> float:
            v = getattr(cfg, name, None)
            return float(default if v is None else v)
        db = getattr(cfg, "silence_db", None)
        return cls(None if db is None else float(db), get("min_silence", MIN_SILENCE_S),
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


def levels(db: np.ndarray, st: Settings) -> dict:
    """This video's speech level, background noise and silence threshold (dBFS). The threshold sits THRESHOLD_FRAC
    of the way from the noise up to the speech (at least MIN_ABOVE_NOISE_DB above the one, MIN_BELOW_SPEECH_DB under
    the other), so the pauses of a noisy video are cut too; --silence-db sets it under the speech level instead."""
    speech = float(np.percentile(db, SPEECH_PCT))
    live = db[db > FLOOR_DB]
    noise = float(np.percentile(live, NOISE_PCT)) if len(live) else FLOOR_DB
    noise = min(noise, speech)
    if st.db is not None:
        thr, how = speech + st.db, f"--silence-db {st.db:g}"
    else:
        thr = noise + THRESHOLD_FRAC * (speech - noise)
        thr = min(max(thr, noise + MIN_ABOVE_NOISE_DB), speech - MIN_BELOW_SPEECH_DB)
        how = "set from the speech level and the background noise"
    return {"speech_db": round(speech, 1), "noise_db": round(noise, 1), "threshold_db": round(thr, 1), "how": how}


def word_cores(words: Sequence[Any], t: np.ndarray, db: np.ndarray, noise: float) -> list[tuple[float, float]]:
    """Each transcribed word's audible part: its timing trimmed to its windows at least WORD_SOUND_DB above the
    noise (a timing that runs on into the pause after the word does not keep that pause), else its whole timing. A
    timing that holds a quiet stretch of MAX_IN_WORD_QUIET_S or more gives one part per sound (a word timed across
    a pause -- "daughter" over 3 s -- does not hide that pause)."""
    out = []
    loud = db >= noise + WORD_SOUND_DB
    split = int(round(MAX_IN_WORD_QUIET_S / HOP_S))
    for w in words:
        s0, s1 = float(w.start), float(w.end)
        i0, i1 = int(np.searchsorted(t, s0, "left")), int(np.searchsorted(t, s1, "right"))
        inside = i0 + np.flatnonzero(loud[i0:i1])
        if not len(inside):
            if s1 > s0:
                out.append((s0, s1))
            continue
        for part in np.split(inside, np.flatnonzero(np.diff(inside) > split) + 1):
            a = max(s0, float(t[part[0]]) - WIN_S / 2.0)
            b = min(s1, float(t[part[-1]]) + WIN_S / 2.0)
            if b > a:
                out.append((a, b))
    return out


def quiet_windows(db: np.ndarray, lv: dict) -> np.ndarray:
    """The loudness windows below this video's silence threshold (levels); a louder stretch shorter than BLIP_S
    between two quiet ones is a click / peak, not a sound: quiet too."""
    q = db < lv["threshold_db"]
    blip = int(round(BLIP_S / HOP_S))
    edges = np.flatnonzero(np.diff(np.concatenate([[True], q, [True]]).astype(np.int8)))
    for i0, i1 in zip(edges[::2], edges[1::2]):        # louder stretches i0 .. i1 - 1 between quiet ones
        if i1 - i0 < blip and i0 > 0 and i1 < len(q):
            q[i0:i1] = True
    return q


def quiet_runs(q: np.ndarray, t: np.ndarray, db: np.ndarray, noise: float, dur: float, min_s: float = 0.0
               ) -> list[tuple[float, float]]:
    """[(start s, end s)] of the quiet stretches of q (quiet_windows) without the soft start / end of the sounds
    around them -- the windows next to a sound still SOFT_DB above the noise, at most SOFT_MAX_S: the soft end of
    a word is part of the word. A stretch that never falls that low (words run together) is quiet as a whole.
    Only the stretches of at least ``min_s`` are listed."""
    quiet = np.concatenate([[False], q, [False]])
    edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    soft = db >= noise + SOFT_DB
    n_max = int(round(SOFT_MAX_S / HOP_S))
    out = []
    for i0, i1 in zip(edges[::2], edges[1::2]):        # windows i0 .. i1 - 1 are quiet
        j0, j1 = i0, i1
        deep = np.flatnonzero(~soft[i0:i1])
        if len(deep):
            if i0 > 0:                                  # a sound before it: its soft end is not quiet
                j0 = i0 + min(int(deep[0]), n_max)
            if i1 < len(db):                            # a sound after it: its soft start is not quiet
                j1 = i1 - min(i1 - i0 - 1 - int(deep[-1]), n_max)
            if j1 <= j0:
                j0, j1 = i0, i1
        a = 0.0 if j0 == 0 else float(t[j0]) - HOP_S / 2.0          # (the first / last window reaches the edge)
        b = dur if j1 == len(db) else min(dur, float(t[j1 - 1]) + HOP_S / 2.0)
        if b - a < min_s - 1e-9 and (j0, j1) != (i0, i1):          # too little left: the whole stretch
            a = 0.0 if i0 == 0 else float(t[i0]) - HOP_S / 2.0
            b = dur if i1 == len(db) else min(dur, float(t[i1 - 1]) + HOP_S / 2.0)
        if b - a >= min_s - 1e-9 and b > a:
            out.append((a, b))
    return out


def silent_runs(y: np.ndarray, sr: int, st: Settings, words: Sequence[Any] | None = None,
                min_s: float | None = None, lv: dict | None = None) -> tuple[list[tuple[float, float]], dict]:
    """([(start s, end s)] of every stretch below this video's silence threshold (levels) for longer than st.min_s
    (or ``min_s``) -- without the soft start / end of the sounds around it (quiet_runs), and, with ``words`` (the
    transcript, timed on this audio), outside every word's audible part: a cut never falls inside a word --, the
    levels). ``lv``: the levels to use (the speech map's, so silence and speech agree), else measured on y."""
    t, db = loudness(y, sr)
    dur = len(y) / float(sr)
    if not len(db):
        return [], {"speech_db": 0.0, "noise_db": 0.0, "threshold_db": 0.0, "how": "no audio", "words": None}
    lv = dict(lv) if lv is not None and lv.get("threshold_db") is not None else levels(db, st)
    q = quiet_windows(db, lv)
    lv["words"] = None if words is None else len(words)
    if words:
        for s0, s1 in word_cores(words, t, db, lv["noise_db"]):
            q[np.searchsorted(t, s0 - HOP_S / 2.0, "left"):np.searchsorted(t, s1 + HOP_S / 2.0, "right")] = False
    least = st.min_s if min_s is None else float(min_s)
    out = [(a, b) for a, b in quiet_runs(q, t, db, lv["noise_db"], dur) if b - a > least]
    return out, lv


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
                   protect: Sequence[tuple[int, int]] = (), words: Sequence[Any] | None = None,
                   cuts_at: Sequence[int] = (), guard: Any = None, lv: dict | None = None,
                   quiet: Sequence[tuple[float, float]] | None = None,
                   sound: Sequence[tuple[int, int]] = ()) -> tuple[list[Cut], dict]:
    """(the ranges to remove, in sequence frames, the levels used): each silence (silent_runs; between words when
    ``words`` are given) minus the pads around the word or sound on either side (none at the edit's start / end),
    rounded inwards to whole frames, and never inside a protected range (a cross dissolve). ``cuts_at``: the
    sequence frames where the edit cuts -- the end of one clip and the start of the next never keep more silence
    than --pad-after + --pad-before together, however short (--min-silence is for pauses inside a clip).
    ``guard(a, b)`` -> (a, b): the range moved so no sliver of a RAW shot is left at the cut (shot_guard_frames).
    ``quiet``: the quiet stretches of y (s) already known (a1_quiet: the RAW's speech map on A1), else measured.
    ``sound``: ranges (sequence frames) that count as sound however quiet A1 is there -- another video's stretch
    (broll.py), left empty to be filled by hand: no silence inside it, the pads kept around it."""
    f = float(fps)
    at_cut = sorted(int(c) for c in cuts_at)
    joined = st.pad_after + st.pad_before
    if quiet is not None:
        least = min(st.min_s, joined) if at_cut else st.min_s
        runs = [(a, b) for a, b in quiet if b - a > least]
        lv = dict(lv or {})
        lv["words"] = None if words is None else len(words)
    else:
        runs, lv = silent_runs(y, sr, st, words, min(st.min_s, joined) if at_cut else None, lv)
    for p0, p1 in sound:
        runs = [q for x0, x1 in runs for q in ((x0, min(x1, p0 / f)), (max(x0, p1 / f), x1)) if q[1] > q[0]]
    dur = n_frames / f
    cuts: list[Cut] = []
    for s0, s1 in runs:
        across = any(s0 - 1e-6 <= c / f <= s1 + 1e-6 for c in at_cut)
        if s1 - s0 <= st.min_s and not (across and s1 - s0 > joined + 1e-9):
            continue
        lo = s0 + (st.pad_after if s0 > 1e-6 else 0.0)
        hi = s1 - (st.pad_before if s1 < dur - 1e-6 else 0.0)
        a = 0 if s0 <= 1e-6 else int(math.ceil(lo * f - 1e-9))
        b = n_frames if s1 >= dur - 1e-6 else int(math.floor(hi * f + 1e-9))
        if guard is not None and b > a:
            a, b = guard(a, b)
        pieces = [(max(0, a), min(n_frames, b))]
        for p0, p1 in protect:
            pieces = [q for x0, x1 in pieces for q in ((x0, min(x1, p0)), (max(x0, p1), x1)) if q[1] > q[0]]
        cuts += [Cut(x0, x1, s0, s1) for x0, x1 in pieces if x1 > x0]
    if cuts and sum(c.frames for c in cuts) >= n_frames:        # all silent: keep the edit rather than nothing
        cuts = []
    return cuts, lv


def shot_guard_frames(clips: Sequence[Any] | None, sm: Any, changes_s: Sequence[float], fps: Fraction,
                      min_s: float | None = None) -> Any:
    """``guard(a, b)`` for removal_ranges: a removed range [a, b) (sequence frames) moved so the picture kept on
    either side of the cut does not end / start with a sliver of a RAW shot shorter than shots.MIN_SHOT_S -- a
    sliver with no speech in it (``sm``: the RAW's speech map) is cut away with the silence, one the speech runs into
    is kept that long. ``clips``: the V1 clips (rec_start / rec_end / src_in at the sequence rate, speed); None: the
    sequence is the RAW itself (RAW-only)."""
    from .shots import MIN_SHOT_S
    m = MIN_SHOT_S if min_s is None else float(min_s)
    f = float(fps)
    cs = sorted(float(c) for c in changes_s)

    def clip_at(fr: int) -> tuple[float, float, float] | None:
        """(RAW time at frame fr, the clip's first / end RAW time) of the V1 clip showing frame fr at 100 %."""
        if clips is None:
            return fr / f, 0.0, float("inf")
        for cl in clips:
            if cl.rec_start <= fr < cl.rec_end and abs(float(cl.speed) - 1.0) < 1e-6:
                t0 = cl.src_in / f
                return t0 + (fr - cl.rec_start) / f, t0, t0 + (cl.rec_end - cl.rec_start) / f
        return None

    def said(t0: float, t1: float) -> bool:
        return sm is not None and any(s.speech and s.s1 > t0 + 1e-3 and s.s0 < t1 - 1e-3 for s in sm.sounds)

    def hole(fr: int) -> tuple[int, int] | None:
        """The stretch of the sequence around frame fr no clip covers (V1 would be black there), or None."""
        if clips is None or any(cl.rec_start <= fr < cl.rec_end for cl in clips):
            return None
        lo = max([cl.rec_end for cl in clips if cl.rec_end <= fr] or [0])
        hi = min([cl.rec_start for cl in clips if cl.rec_start > fr] or [fr + 1])
        return lo, hi

    n = int(math.ceil(m * f - 1e-9))

    def guard(a: int, b: int) -> tuple[int, int]:
        ha, hb = hole(a - 1), hole(b)                # empty V1 (black) kept at the cut: both sides join after it
        black = (a - ha[0] if ha else 0) + (hb[1] - b if hb else 0)
        if 0 < black < n:                            # a sliver of black: cut away with the silence
            a = ha[0] if ha else a
            b = hb[1] if hb else b
        got = clip_at(a - 1)
        if got is not None:
            t_end, lo, _ = got[0] + 1.0 / f, got[1], got[2]
            near = [c for c in cs if max(lo, t_end - m) < c < t_end - 1e-6]
            if near:
                c = near[-1]
                a = (a - int(round((t_end - c) * f)) if not said(c, t_end) else
                     a + int(math.ceil((c + m - t_end) * f - 1e-9)))
        got = clip_at(b)
        if got is not None:
            t0, _, hi = got
            near = [c for c in cs if t0 + 1e-6 < c < min(hi, t0 + m)]
            if near:
                c = near[0]
                b = (b + int(round((c - t0) * f)) if not said(t0, c) else
                     b - int(math.ceil((t0 - (c - m)) * f - 1e-9)))
        # nor a sliver of a V1 clip (another framing) shorter than that, with no speech in it, on either side (the
        # pieces of one clip kept on both sides of the cut join after it)
        for cl in clips or []:
            if abs(float(cl.speed) - 1.0) > 1e-6:
                continue
            t = lambda fr: (cl.src_in + (fr - cl.rec_start)) / f                  # noqa: E731
            in_a, in_b = cl.rec_start < a < cl.rec_end, cl.rec_start < b < cl.rec_end
            kept = (a - cl.rec_start if in_a else 0) + (cl.rec_end - b if in_b else 0)
            if not 0 < kept < n:
                continue
            if in_a and not said(t(cl.rec_start), t(a)):
                a = cl.rec_start
            if in_b and not said(t(b), t(cl.rec_end)):
                b = cl.rec_end
        return (a, b) if b > a else (a, a)
    return guard


@dataclass
class Insert:
    """Frames added at a cut (``at``, a frame of the edit before): the clip ending there plays ``frames`` more of
    its source (side "end"), or the clip starting there starts that much earlier in its source (side "start");
    ``src``: the RAW frame (sequence rate) the added frames start at -- a clip extended to let its speech finish."""
    at: int
    frames: int
    side: str
    src: float = 0.0


@dataclass
class Ripple:
    """The timeline after removing ``cuts`` (sorted, disjoint [a, b) sequence frames) from n_frames, adding
    ``inserts`` (clips extended at their cuts), moving the audio lines of ``shifts`` [(first frame, frames)] in
    their source and sliding the A1 cuts of ``slides`` [(frame, frames)] under a cross dissolve (A1 only: the clip
    before plays on, the one after starts later). ``before``: a ripple applied first (its new timeline is this one's old one): the speech-safe cuts
    (speech.py), then the silences and repeats."""
    cuts: list[Cut] = field(default_factory=list)
    n_frames: int = 0
    inserts: list[Insert] = field(default_factory=list)
    shifts: list[tuple[int, int]] = field(default_factory=list)
    slides: list[tuple[int, int]] = field(default_factory=list)
    before: "Ripple | None" = None

    def __post_init__(self) -> None:
        self.cuts = sorted(self.cuts, key=lambda c: c.a)
        self.inserts = sorted(self.inserts, key=lambda i: (i.at, i.side != "end"))

    def stages(self) -> list["Ripple"]:
        """The ripples applied in turn: ``before``'s first."""
        return (self.before.stages() if self.before is not None else []) + [self]

    @property
    def active(self) -> bool:
        """Anything changes the edit (in any stage)."""
        return any(r.cuts or r.inserts or r.shifts or r.slides for r in self.stages())

    @property
    def first_frames(self) -> int:
        """The length of the edit before any stage."""
        return self.stages()[0].n_frames

    @property
    def removed(self) -> int:
        return sum(c.frames for c in self.cuts)

    @property
    def added(self) -> int:
        return sum(i.frames for i in self.inserts)

    @property
    def new_frames(self) -> int:
        return self.n_frames - self.removed + self.added

    def map(self, f: int) -> int:
        """New frame of old frame f (of the edit before every stage; a frame inside a removed range maps to where
        that range was)."""
        if self.before is not None:
            f = self.before.map(f)
        return self.map1(f)

    def map1(self, f: int) -> int:
        """map() of this stage alone: frames removed before f out, frames inserted at or before f in."""
        shift = sum(i.frames for i in self.inserts if i.at <= f)
        for c in self.cuts:
            if f >= c.b:
                shift -= c.frames
            elif f > c.a:
                return c.a + shift
            else:
                break
        return f + shift

    def map_hole1(self, a: int, b: int) -> tuple[int, int]:
        """map1() of an empty stretch [a, b) of V1 / A1 (another video's, broll.py): the clip before it playing on
        and the clip after it starting earlier both stay out of it."""
        a, b = self.map1(a) - self.ext(a, "start"), self.map1(b) - self.ext(b, "start")
        return a, max(a, b)

    def map_hole(self, a: int, b: int) -> tuple[int, int]:
        """map_hole1() through every stage."""
        for st in self.stages():
            a, b = st.map_hole1(a, b)
        return a, b

    def keep(self, a: int, b: int) -> list[tuple[int, int]]:
        """The parts of old range [a, b) that are kept (this stage)."""
        out = [(a, b)]
        for c in self.cuts:
            out = [q for x0, x1 in out for q in ((x0, min(x1, c.a)), (max(x0, c.b), x1)) if q[1] > q[0]]
        return out

    def ext(self, at: int, side: str) -> int:
        """Frames inserted at old frame ``at`` on ``side`` (this stage)."""
        return sum(i.frames for i in self.inserts if i.at == at and i.side == side)

    def joins(self) -> set[int]:
        """Old frames where a cut was made: the start and the end of every removed range."""
        return {c.a for c in self.cuts} | {c.b for c in self.cuts}

    def seconds(self, fps: Fraction) -> list[tuple[float, float]]:
        f = float(fps)
        return [(c.a / f, c.b / f) for c in self.cuts]


def fade_samples(sr: int, fps: Fraction) -> int:
    return max(1, int(round(FADE_FRAMES * sr / float(fps))))


def cut_audio(y: np.ndarray, sr: int, fps: Fraction, rp: Ripple, raw: Any = None) -> np.ndarray:
    """The edit's audio with every stage of ``rp`` applied: the removed ranges taken out, faded over FADE_FRAMES on
    both sides of every such cut (exactly what A1 plays with the XML's Audio Levels keys), the extensions played from
    ``raw`` = (RAW audio, its rate) (silent without it)."""
    y = np.asarray(y, np.float32)
    for st in rp.stages():
        y = _cut_audio1(y, sr, fps, st, raw)
    return y


def _cut_audio1(y: np.ndarray, sr: int, fps: Fraction, rp: Ripple, raw: Any) -> np.ndarray:
    if not (rp.cuts or rp.inserts):
        return y.copy()
    f = float(fps)
    nf = fade_samples(sr, fps)
    joins = rp.joins()
    parts = []                                           # (old frame, order, samples): order 0 / 1 inserts, 2 kept
    for a, b in _split_at(rp.keep(0, rp.n_frames), [i.at for i in rp.inserts]):
        s0, s1 = int(round(a * sr / f)), min(len(y), int(round(b * sr / f)))
        seg = y[s0:s1].copy()
        if len(seg):
            ramp = np.linspace(0.0, 1.0, min(nf, len(seg)), endpoint=False, dtype=np.float32)
            if a in joins and a > 0:
                seg[:len(ramp)] *= ramp[(slice(None),) + (None,) * (seg.ndim - 1)]
            if b in joins and b < rp.n_frames:
                seg[len(seg) - len(ramp):] *= ramp[::-1][(slice(None),) + (None,) * (seg.ndim - 1)]
        parts.append((a, 2, seg))
    for ins in rp.inserts:
        n = int(round(ins.frames * sr / f))
        seg = np.zeros((n,) + y.shape[1:], np.float32)
        if raw is not None and raw[0] is not None and len(raw[0]):
            x, xsr = raw
            r0 = int(round(ins.src / f * xsr))
            piece = np.asarray(x[max(0, r0):max(0, r0) + int(round(ins.frames / f * xsr))], np.float32)
            if piece.ndim > 1:
                piece = piece.mean(axis=1)
            if int(xsr) != int(sr) and len(piece):
                from math import gcd
                from scipy.signal import resample_poly
                g = gcd(int(sr), int(xsr))
                piece = resample_poly(piece, int(sr) // g, int(xsr) // g).astype(np.float32)
            got = piece[:n]
            if seg.ndim == 1:
                seg[:len(got)] = got
            else:
                seg[:len(got)] = got[:, None]
        parts.append((ins.at, 0 if ins.side == "end" else 1, seg))
    parts.sort(key=lambda p: (p[0], p[1]))
    return np.concatenate([p[2] for p in parts]) if parts else y[:0].copy()


def _split_at(ranges: Sequence[tuple[int, int]], at: Sequence[int]) -> list[tuple[int, int]]:
    """The ranges split at the frames ``at`` inside them."""
    out = []
    for a, b in ranges:
        for x in sorted(set(at)):
            if a < x < b:
                out.append((a, x))
                a = x
        out.append((a, b))
    return out


def apply_premiere(clips: list, audio: list[dict], markers: list[dict], rp: Ripple) -> tuple[list, list[dict], list[dict]]:
    """(V1 clips, A1 items, markers) of the Premiere export after every stage of ``rp``: every piece that is kept,
    moved by the time removed / added before it; a clip spanning a removed range becomes two clips (the same source
    continuing from where the removed time ends); a clip extended at a cut plays more of its source there. A1
    pieces carry ``fade_in`` / ``fade_out`` where a cut was made; pieces the removal leaves playing one continuous
    RAW take (a trimmed repeat, a clip that went between two pieces of one take) become one clip again."""
    for st in rp.stages():
        if st.cuts or st.inserts or st.shifts or st.slides:
            clips, audio, markers = _apply1(clips, audio, markers, st)
    return clips, audio, markers


def _apply1(clips: list, audio: list[dict], markers: list[dict], rp: Ripple) -> tuple[list, list[dict], list[dict]]:
    joins = rp.joins()
    at_ins = [i.at for i in rp.inserts]
    out_c = []
    for cl in clips:
        lo = cl.rec_start if cl.start == -1 else cl.start
        hi = cl.rec_end if cl.end == -1 else cl.end
        for a, b in _split_at(rp.keep(lo, hi), at_ins):
            e0, e1 = rp.ext(a, "start"), rp.ext(b, "end")              # the clip extended at its cut
            n_in = cl.src_in if a == lo else cl.src_in + int(round((a - lo) * cl.speed))
            n_out = cl.src_out if b == hi else n_in + int(round((b - a) * cl.speed))
            n_in, n_out = n_in - int(round(e0 * cl.speed)), n_out + int(round(e1 * cl.speed))
            r0 = rp.map1(a) - e0
            out_c.append(dataclasses.replace(
                cl, start=-1 if (cl.start == -1 and a == lo) else r0,
                end=-1 if (cl.end == -1 and b == hi) else r0 + (b - a) + e0 + e1,
                rec_start=r0, rec_end=r0 + (b - a) + e0 + e1, src_in=n_in, src_out=n_out))
    shift, slide = dict(rp.shifts), dict(rp.slides)
    out_a = []
    for it in audio:
        d = shift.get(it["start"], 0)
        if d:                                                         # an audio line moved to play on (speech.py)
            it = dict(it, **{"in": it["in"] + d, "out": it["out"] + d})
        d0, d1 = slide.get(it["start"], 0), slide.get(it["end"], 0)    # an A1 cut slid under a cross dissolve
        if d0 or d1:
            v = it["speed"]
            it = dict(it, start=it["start"] + d0, end=it["end"] + d1, **{"in": it["in"] + int(round(d0 * v))},
                      out=it["out"] + int(round(d1 * v)))
        for a, b in _split_at(rp.keep(it["start"], it["end"]), at_ins):
            e0, e1 = rp.ext(a, "start"), rp.ext(b, "end")
            n_in = it["in"] if a == it["start"] else it["in"] + int(round((a - it["start"]) * it["speed"]))
            n_out = it["out"] if b == it["end"] else n_in + int(round((b - a) * it["speed"]))
            n_in, n_out = n_in - int(round(e0 * it["speed"])), n_out + int(round(e1 * it["speed"]))
            r0 = rp.map1(a) - e0
            out_a.append(dict(it, start=r0, end=r0 + (b - a) + e0 + e1, **{"in": n_in}, out=n_out,
                              fade_in=(bool(it.get("fade_in")) and a == it["start"] and not e0) or
                              (a in joins and a > 0),
                              fade_out=(bool(it.get("fade_out")) and b == it["end"] and not e1) or
                              (b in joins and b < rp.n_frames)))
    out_m = [dict(m, **dict(zip(("in", "out"), rp.map_hole1(m["in"], m["out"])))) if m.get("other_video") else
             dict(m, **{"in": rp.map1(m["in"]), "out": max(rp.map1(m["in"]), rp.map1(m["out"]))}) for m in markers]
    # a removed repeat (repeats.py) can leave the two sides playing one continuous RAW take: one clip, no fade there
    from .export_xml_edl import _merge_continuous
    out_c = _merge_continuous(out_c)
    merged_a: list[dict] = []
    for it in out_a:
        p = merged_a[-1] if merged_a else None
        if (p is not None and p["end"] == it["start"] and p["out"] == it["in"] and abs(p["speed"] - it["speed"]) < 1e-9
                and p.get("what") == it.get("what")):
            merged_a[-1] = dict(p, end=it["end"], out=it["out"], fade_out=it["fade_out"])
            continue
        merged_a.append(it)
    return out_c, merged_a, out_m


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


def plan_premiere(cutlist: Any, raw_audio: np.ndarray | None, sr: int, cfg: Any = None,
                  words_of: Any = None, speech: Any = None, remove: bool = True,
                  shots: Sequence[float] | None = None) -> dict:
    """The cuts of the Premiere export of ``cutlist`` before it is written: {cuts, ripple, threshold_db, levels,
    rows, removed_s, old_s, new_s, settings, speech}. First the speech-safe cuts (``speech``: the RAW's
    speech.SpeechMap -- every audio cut moved into the quiet, clips trimmed or extended: speech.plan_cuts), then
    (``remove``; --keep-silence: not) the silences of A1 (the RAW audio under the clips, after those cuts), never
    inside a cross dissolve; at every cut, the end of one clip and the start of the next keep at most --pad-after +
    --pad-before of silence together. The words that keep a silence cut out of a word: the speech map's (mapped onto
    A1), else ``words_of(y)`` -> the words heard in that audio, or None: then the cuts follow the loudness alone.
    ``shots``: the RAW's shot changes (s) -- no cut leaves a sliver of a shot (shots.py)."""
    from .export_xml_edl import premiere_audio, premiere_clips, premiere_factor, premiere_settings
    st = Settings.from_cfg(cfg)
    pst = premiere_settings(cfg)
    fps = pst["fps"]
    fac = premiere_factor(cutlist.comp_fps, fps)
    n_frames = int(cutlist.competitor["frames"]) * fac
    clips, markers, _ = premiere_clips(cutlist, cfg)
    other = [(int(m["in"]), int(m["out"])) for m in markers if m.get("other_video")]     # another video's stretches
    audio = premiere_audio(cutlist, clips, cfg) if bool(cutlist.raw.get("has_audio", True)) else []
    snap, snap_rows = None, []
    if speech is not None and audio:
        from .speech import plan_cuts
        src_max = int(math.floor(int(cutlist.raw["frames"]) * float(fps) / float(cutlist.raw_fps)))
        snap, snap_rows = plan_cuts(clips, audio, speech, fps, st, n_frames, src_max, fac, shots)
        if snap.active:
            clips, audio, _ = apply_premiere(clips, audio, [], snap)
            other = [snap.map_hole1(a, b) for a, b in other]
        n_frames = snap.new_frames
    protect = [(cl.rec_start, cl.rec_start + int(cl.ev.dissolve_in) * fac) for cl in clips if cl.start == -1]
    protect += other                       # another video plays there (filled by hand): never cut, it is no silence
    if not remove:
        cuts, lv = [], {"how": "--keep-silence"}
    elif raw_audio is None or not len(raw_audio) or not audio:
        cuts, lv = [], {"how": "no RAW audio under the clips"}
    else:
        y = a1_audio(audio, raw_audio, sr, fps, n_frames)
        if speech is not None and (speech.levels or {}).get("words") is not None:
            words = words_on_a1(speech.words, audio, fps)
        else:
            words = words_of(y) if words_of is not None else None
        from .speech import audio_cuts
        at = sorted({c[3] for c in audio_cuts([dict(it, name="") for it in audio], fps, n_frames)})
        guard = shot_guard_frames(clips, speech, shots or [], fps)
        fixed = {k: (speech.levels or {}).get(k) for k in ("speech_db", "noise_db", "threshold_db", "how")} \
            if speech is not None else None
        quiet = (a1_quiet(audio, speech, fps, n_frames, bool(getattr(cfg, "silence_breaths", False)))
                 if speech is not None else None)
        cuts, lv = removal_ranges(y, sr, fps, n_frames, st, protect, words, at, guard, fixed, quiet, sound=other)
    out = summarize(cuts, n_frames, fps, st, lv, before=snap)
    out["speech"] = {"rows": snap_rows, "levels": dict((speech.levels or {}) if speech is not None else {}),
                     "on": speech is not None, "fps": str(fps)}
    if not remove:
        out["off"] = "--keep-silence"
    return out


def a1_quiet(audio: Sequence[dict], sm: Any, fps: Fraction, n_frames: int,
             breaths: bool = False) -> list[tuple[float, float]]:
    """The quiet stretches of A1 (sequence seconds): inside every item at 100 % the gaps of the RAW's speech map
    ``sm`` (the same quiet the speech check knows: a breath or other sound is not quiet -- with ``breaths``, the gaps
    between speech: a pause with a breath in it is a pause), and wherever A1 plays nothing; an item at another speed
    counts as sound. Stretches that meet at a cut are one."""
    f = float(fps)
    spans: list[tuple[float, float]] = []
    covered: list[tuple[float, float]] = []
    gaps = sm.speech_gaps if breaths else sm.gaps
    for it in sorted(audio, key=lambda d: d["start"]):
        t0, t1 = it["start"] / f, it["end"] / f
        covered.append((t0, t1))
        if abs(float(it["speed"]) - 1.0) > 1e-6:
            continue
        r0 = it["in"] / f
        r1 = r0 + (t1 - t0)
        for g0, g1 in gaps:
            a, b = max(g0, r0), min(g1, r1)
            if b > a:
                spans.append((t0 + a - r0, t0 + b - r0))
    at = 0.0
    for a, b in sorted(covered):
        if a > at + 1e-9:
            spans.append((at, a))
        at = max(at, b)
    if n_frames / f > at + 1e-9:
        spans.append((at, n_frames / f))
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def words_on_a1(words: Sequence[tuple[str, float, float]], audio: Sequence[dict], fps: Fraction) -> list[Any]:
    """The RAW's words (text, start s, end s) where A1 plays them: their times in the sequence (seconds), cut to the
    items they are heard in."""
    from types import SimpleNamespace
    f = float(fps)
    out = []
    for it in sorted(audio, key=lambda d: d["start"]):
        v = float(it["speed"])
        if v <= 0:
            continue
        r0, r1 = it["in"] / f, it["in"] / f + (it["end"] - it["start"]) * v / f
        for text, s0, s1 in words:
            if s1 <= r0 or s0 >= r1:
                continue
            a = it["start"] / f + (max(s0, r0) - r0) / v
            b = it["start"] / f + (min(s1, r1) - r0) / v
            out.append(SimpleNamespace(text=text, raw=text, start=a, end=max(a, b)))
    return out


def summarize(cuts: list[Cut], n_frames: int, fps: Fraction, st: Settings, lv: dict, before: Ripple | None = None
              ) -> dict:
    """The plan as the pipeline / report / summary use it (``lv``: removal_ranges' levels; ``before``: the
    speech-safe cuts made first -- the silences' frames are of the edit after them)."""
    rp = Ripple(list(cuts), n_frames, before=before)
    f = float(fps)
    rows = [{"start_s": round(c.a / f, 3), "end_s": round(c.b / f, 3), "len_s": round(c.frames / f, 3),
             "new_at_s": round(rp.map1(c.a) / f, 3), "a": c.a, "b": c.b} for c in rp.cuts]
    return {"cuts": [(c.a, c.b) for c in rp.cuts], "ripple": rp, "threshold_db": lv.get("threshold_db"),
            "levels": dict(lv), "rows": rows, "removed_s": round(rp.removed / f, 3),
            "old_s": round(rp.first_frames / f, 3), "speech_s": round(n_frames / f, 3),
            "new_s": round(rp.new_frames / f, 3), "fps": str(fps), "settings": dataclasses.asdict(st)}


def settings_line(plan: dict) -> str:
    """The settings one video got, for the end summary / report: its levels, the threshold, the minimum, the pads,
    and whether the cuts kept to the gaps between words."""
    lv, st = plan.get("levels") or {}, plan.get("settings") or {}
    if lv.get("speech_db") is None:
        return f"settings: {lv.get('how', 'no audio')}"
    n = lv.get("words")
    words = (f"cuts only between words ({n} words timed)" if n else
             "word timings not available: cuts from loudness alone" if n is None else "no words heard")
    return (f"settings for this video: speech {lv['speech_db']:.1f} dBFS, background {lv['noise_db']:.1f} dBFS -> "
            f"silence below {lv['threshold_db']:.1f} dBFS ({lv['how']}), longer than {st.get('min_s', 0):g} s; "
            f"kept {st.get('pad_before', 0):g} s before / {st.get('pad_after', 0):g} s after each word; {words}")


def ripple_pieces(pieces: Sequence[Any], rp: Ripple, fps: Fraction) -> list[Any]:
    """caption_recheck.Piece maps of the edit before every stage of ``rp`` -> of the edit after them (split, moved,
    extended at a cut)."""
    f = float(fps)
    for st in rp.stages():
        out = []
        shift = dict(st.shifts)
        for p in pieces:
            a0, b0 = int(round(p.t0 * f)), int(round(p.t1 * f))
            if shift.get(a0):
                p = dataclasses.replace(p, src0=p.src0 + shift[a0] / f)
            for a, b in _split_at(st.keep(a0, b0), [i.at for i in st.inserts]):
                e0, e1 = st.ext(a, "start"), st.ext(b, "end")
                t0 = (st.map1(a) - e0) / f
                out.append(dataclasses.replace(p, t0=t0, t1=t0 + (b - a + e0 + e1) / f, src0=p.src((a - e0) / f)))
        pieces = out
    return list(pieces)


def tc(seconds: float) -> str:
    """mm:ss.cc for the summary."""
    s = max(0.0, float(seconds))
    return f"{int(s // 60):02d}:{s % 60:05.2f}"
