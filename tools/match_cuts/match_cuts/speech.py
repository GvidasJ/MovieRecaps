"""speech.py: a cut never interrupts speech (the Premiere export; the hard check of every run).

The speech map of the RAW audio (``speech_map``) -- what is heard where:

* A sound: the loudness (silence.loudness) at or above this video's silence threshold (silence.levels; a louder
  blip under silence.BLIP_S is a click), with its soft start and end -- the windows next to it still
  silence.SOFT_DB above the background noise, for at most silence.SOFT_MAX_S: the soft "s", "f" or "-ty five" a
  word starts or ends with belongs to the word. A dip that never falls that low (words run together) stays a gap as
  a whole: the quietest place between two words.
* Words: a dip inside a transcribed word (its audible part, silence.word_cores) is part of that word -- the closure
  of a "t" -- except the dip a boundary between two words falls in or next to (the nearest one within BOUNDARY_S:
  word timings are often that far off).
* Speech: a sound a transcribed word overlaps, or one with a clear pitch for at least VOICED_S (an untranscribed
  "mm", a laugh): everything said, fillers included. A sound with neither -- a breath, a lip smack -- is not speech:
  no cut lands inside it, but a clip's start or end may leave it out. Without word timings every sound is speech.
* Gaps: the quiet between two sounds (at least MIN_GAP_S). A cut may only land in a gap.

Every audio cut of the edit is placed by speech, not by the competitor (``snap_edits``): a clip ends ``pad_after``
after the end of its last speech -- inside the quiet after it; a gap shorter than both pads is split between them,
a breath after it stops it there -- and starts ``pad_before`` before its first speech (the same way). A cut that
falls inside speech moves to the nearer end of that sound: the clip plays on to its end, or stops before it. A
clip never shows again what the clip before it now shows. ``check`` lists every cut of a finished edit that lands
inside speech (the hard check of every run).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

MIN_GAP_S = 0.02          # a gap: the quiet between two sounds at least this long (a dip between two words counts)
WORD_GAP_S = 0.04         # words run together: the gap is the quietest MIN_GAP_S this near where the two words meet
KEEP_FRAC = 0.25          # a cut inside a sound (a word) keeps it whole when the clip plays this much of it or more
BOUNDARY_S = 0.25         # the dip nearest a boundary between two transcribed words, this close, is the gap there
VOICED = 0.8              # a clear pitch: the normalised autocorrelation peak (70-400 Hz, 40 ms) at least this ...
VOICED_S = 0.05           # ... for this long makes an untranscribed sound speech (less: a breath, a smack, a click)
TAIL_GAP_S = 0.08         # an untranscribed sound starting this soon after a transcribed word ...
TAIL_VOICED_S = 0.02      # ... voiced this long is that word's end, its timing cut short (video2: the "-kay" of "OK?",
#                           voiced 0.04 s, 0.06 s after "team, OK?" -- a breath is not voiced at all)
MAX_SHIFT_S = 0.1         # an audio line jumping this little inside speech where the picture does not cut plays on
MAX_RETIMED_EXT_S = 0.1   # an audio line under a retimed picture (V1 not at 100 %) is extended by at most this much new
#                           picture: the shared ripple extends V1 too, at its speed
MAX_JUMP_S = 0.1          # a cut skipping (or repeating) this little of the RAW inside speech: the clips play on as one take
NOISE_S = 0.6             # a sound this long with no word in it (laughter, applause, cheering, music) is noise: a cut may
#                           land inside it -- the competitor's cut stays (021: you cut inside 3.4 s of applause at S44 and
#                           kept S11's sound to the competitor's cut inside 1.7 s of "ewww"; playing on to its end added
#                           3.08 s / 0.53 s, CHECK BY HAND)
WORD_TAIL_S = 0.12        # a word's sound running on (no quiet) into such noise ends this long after the word (021 S50:
#                           "this" 1204.98-1205.16 s, its sound to 1206.45 s through applause)
EPS = 1e-6


@dataclass
class Sound:
    """One sound of the recording (seconds): speech (said) or not (a breath, a click: may be left out at an edge).
    ``noise``: a long sound with no word in it (NOISE_S): kept like speech (a clip is not trimmed out of it), but a
    cut may land inside it (laughter, applause, cheering, music under no words)."""
    s0: float
    s1: float
    speech: bool
    why: str = ""
    noise: bool = False


@dataclass
class SpeechMap:
    """The sounds of a recording (sorted, disjoint; the rest is quiet), its length and transcribed words."""
    sounds: list[Sound]
    dur: float
    words: list[tuple[str, float, float]] = field(default_factory=list)
    levels: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._s0 = [s.s0 for s in self.sounds]

    @property
    def gaps(self) -> list[tuple[float, float]]:
        out, at = [], 0.0
        for s in self.sounds:
            if s.s0 > at + EPS:
                out.append((at, s.s0))
            at = max(at, s.s1)
        if self.dur > at + EPS:
            out.append((at, self.dur))
        return out

    @property
    def speech_gaps(self) -> list[tuple[float, float]]:
        """The stretches between speech (``gaps`` with every sound that is not speech -- a breath, a lip smack, a
        click -- counted as quiet)."""
        out, at = [], 0.0
        for s in self.sounds:
            if not s.speech:
                continue
            if s.s0 > at + EPS:
                out.append((at, s.s0))
            at = max(at, s.s1)
        if self.dur > at + EPS:
            out.append((at, self.dur))
        return out

    def sound_at(self, x: float, tol: float = EPS) -> int | None:
        """The index of the sound x lies inside (more than ``tol`` from both of its ends), or None."""
        i = bisect.bisect_right(self._s0, x) - 1
        if i >= 0 and self.sounds[i].s0 + tol < x < self.sounds[i].s1 - tol:
            return i
        return None

    def speech_before(self, x: float) -> int | None:
        """The last speech sound ending at or before x."""
        i = bisect.bisect_right(self._s0, x + EPS) - 1
        while i >= 0 and (not self.sounds[i].speech or self.sounds[i].s1 > x + EPS):
            i -= 1
        return i if i >= 0 else None

    def speech_after(self, x: float) -> int | None:
        """The first speech sound starting at or after x."""
        i = bisect.bisect_left(self._s0, x - EPS)
        while i < len(self.sounds) and not self.sounds[i].speech:
            i += 1
        return i if i < len(self.sounds) else None

    def said(self, a: float, b: float) -> str:
        """The words heard between a and b (for messages)."""
        return " ".join(w for w, s0, s1 in self.words if s1 > a and s0 < b)


def voicing(y: np.ndarray, sr: int, t: np.ndarray, win: float = 0.04, fmin: float = 70.0, fmax: float = 400.0
            ) -> np.ndarray:
    """The clarity of the pitch at each time t (s) of mono audio y: the peak of the normalised autocorrelation over
    a ``win`` window at lags of a voice's pitch (fmin .. fmax Hz): about 0.5 for noise and breath, 0.8+ for a vowel."""
    y = np.asarray(y, np.float32)
    n = max(8, int(round(win * sr)))
    t = np.asarray(t, np.float64)
    if len(y) < n or not len(t):
        return np.zeros(len(t), np.float32)
    lo, hi = max(1, int(sr / fmax)), min(n - 1, int(sr / fmin))
    nfft = 1 << (2 * n - 1).bit_length()
    w = np.hanning(n).astype(np.float32)
    norm = np.correlate(w, w, "full")[n - 1:n + hi]
    starts = np.clip((t * sr).astype(np.int64) - n // 2, 0, len(y) - n)
    out = np.zeros(len(t), np.float32)
    for k in range(0, len(starts), 2048):
        idx = starts[k:k + 2048, None] + np.arange(n)[None, :]
        fr = y[idx] * w
        fr = fr - fr.mean(axis=1, keepdims=True)
        ac = np.fft.irfft(np.abs(np.fft.rfft(fr, nfft, axis=1)) ** 2, nfft, axis=1)[:, :hi + 1]
        acn = (ac / norm[None, :]) / (ac[:, :1] / norm[0] + 1e-12)
        out[k:k + 2048] = acn[:, lo:hi + 1].max(axis=1)
    return out


def speech_map(y: np.ndarray, sr: int, st: Any = None, words: Sequence[Any] | None = None,
               also: Sequence[Any] | None = None, heard: Sequence[tuple[float, float]] | None = None) -> SpeechMap:
    """The speech map of mono audio y (the RAW; see the module docstring). ``words``: the transcript timed on y
    (objects with .start / .end / .text), [] when nothing is said, None when there is no transcript (then every
    sound is speech); ``also``: a second transcript -- a sound either one heard a word in is speech; ``heard``: the
    stretches (s) the transcripts cover (None: all of y) -- a sound outside them is speech."""
    from . import silence
    st = st or silence.Settings()
    y = np.asarray(y, np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    t, db = silence.loudness(y, sr)
    dur = len(y) / float(sr)
    if not len(db):
        return SpeechMap([], dur, levels={"how": "no audio", "words": None})
    in_heard = np.zeros(len(t), bool)
    for h0, h1 in heard or []:
        in_heard[np.searchsorted(t, h0, "left"):np.searchsorted(t, h1, "right")] = True
    lv = silence.levels(db[in_heard] if in_heard.sum() > 100 else db, st)   # the levels where the edit plays
    q = silence.quiet_windows(db, lv)
    ws = sorted((w for w in (words or []) if float(w.end) >= float(w.start)), key=lambda w: float(w.start))
    if ws:
        q = _block_word_dips(q, t, db, lv["noise_db"], ws)
        q = _word_gaps(q, t, db, ws)
    runs = silence.quiet_runs(q, t, db, lv["noise_db"], dur, MIN_GAP_S)
    sounds: list[Sound] = []
    at = 0.0
    for a, b in runs + [(dur, dur)]:
        if a > at + EPS:
            sounds.append(Sound(at, a, True))
        at = max(at, b)
    named = [(str(getattr(w, "raw", None) or w.text).strip(), float(w.start), float(w.end)) for w in ws]
    if words is not None:
        both = sorted(named + [(str(getattr(w, "raw", None) or w.text).strip(), float(w.start), float(w.end))
                               for w in (also or []) if float(w.end) >= float(w.start)], key=lambda n: n[1])
        w0 = [n[1] for n in both]
        span = max([n[2] - n[1] for n in both] or [0.0])
        need = []
        for k, s in enumerate(sounds):
            if heard is not None and not any(h0 <= s.s0 + EPS and s.s1 <= h1 + EPS for h0, h1 in heard):
                s.why = "not transcribed here"
                continue
            i0, i1 = bisect.bisect_left(w0, s.s0 - span - 0.01), bisect.bisect_right(w0, s.s1)
            said = [n for n in both[i0:i1] if n[2] > s.s0 + 0.01 and n[1] < s.s1 - 0.01]
            if said:
                s.why = "words: " + " ".join(dict.fromkeys(n[0] for n in said))
            else:
                need.append((k, s))
        if need:
            from .transcribe import resample, SR
            y16 = resample(y, sr)
            tails: dict[int, float] = {}
            for k, s in need:
                tt = np.arange(s.s0 + silence.HOP_S / 2.0, s.s1, silence.HOP_S)
                v = voicing(y16, SR, tt)
                voiced_s = float(np.sum(v >= VOICED)) * silence.HOP_S
                prev = sounds[k - 1] if k > 0 else None
                if (voiced_s < VOICED_S - 1e-9 and voiced_s >= TAIL_VOICED_S - 1e-9 and prev is not None
                        and str(prev.why).startswith("words:") and s.s0 - prev.s1 <= TAIL_GAP_S + 1e-9):
                    tails[k] = voiced_s
                    continue
                s.speech = voiced_s >= VOICED_S - 1e-9
                s.why = (f"no words, voiced {voiced_s:.2f} s" if s.speech else
                         f"no words, voiced {voiced_s:.2f} s: a breath or a noise")
            if tails:                       # a word's end: one sound with the word (no cut between them)
                merged: list[Sound] = []
                for k, s in enumerate(sounds):
                    if k in tails and merged:
                        merged[-1].s1 = s.s1
                        merged[-1].why = f"{merged[-1].why} + its end (no words, voiced {tails[k]:.2f} s)"
                        continue
                    merged.append(s)
                sounds = merged
        sounds = _noise(sounds, both)
    lv["words"] = None if words is None else len(ws)
    return SpeechMap(sounds, dur, named, lv)


def _noise(sounds: list[Sound], words: Sequence[tuple[str, float, float]]) -> list[Sound]:
    """The noise of a transcribed recording (NOISE_S): a long sound with no word in it is noise; a sound whose words
    end WORD_TAIL_S or more before a long stretch of it (no quiet in between: applause after "this") is split there --
    the words, then the noise (and the same before its first word)."""
    out: list[Sound] = []
    w0 = [n[1] for n in words]
    span = max([n[2] - n[1] for n in words] or [0.0])
    for s in sounds:
        if s.why == "not transcribed here":
            out.append(s)
            continue
        i0, i1 = bisect.bisect_left(w0, s.s0 - span - 0.01), bisect.bisect_right(w0, s.s1)
        said = [n for n in words[i0:i1] if n[2] > s.s0 + 0.01 and n[1] < s.s1 - 0.01]
        if not said:
            if s.s1 - s.s0 >= NOISE_S - 1e-9:      # voiced (a laugh, cheering): kept like speech; else trimmable
                out.append(Sound(s.s0, s.s1, s.speech, f"{s.why or 'no words'}; {s.s1 - s.s0:.2f} s with no word: "
                                                       "laughter / applause / noise -- a cut may land in it", True))
            else:
                out.append(s)
            continue
        first, last = min(n[1] for n in said), max(n[2] for n in said)
        a, b = s.s0, s.s1
        head = first - WORD_TAIL_S
        tail = last + WORD_TAIL_S
        if head - a >= NOISE_S - 1e-9:
            out.append(Sound(a, head, True, f"before '{said[0][0]}': {head - a:.2f} s with no word -- noise", True))
            a = head
        if b - tail >= NOISE_S - 1e-9:
            out.append(Sound(a, tail, s.speech, s.why))
            out.append(Sound(tail, b, True, f"after '{said[-1][0]}': {b - tail:.2f} s with no word -- noise", True))
        else:
            out.append(Sound(a, b, s.speech, s.why))
    return out


def _block_word_dips(q: np.ndarray, t: np.ndarray, db: np.ndarray, noise: float, ws: Sequence[Any]) -> np.ndarray:
    """q with the quiet inside a word's audible part made loud -- except in the dip nearest each boundary between
    two words (within BOUNDARY_S) when that dip has no quiet of its own outside the words: there the timings are
    off, the dip is the gap between the two words."""
    from . import silence
    inside = np.zeros(len(q), bool)
    for s0, s1 in silence.word_cores(ws, t, db, noise):
        inside[np.searchsorted(t, s0, "left"):np.searchsorted(t, s1, "right")] = True
    flips = np.flatnonzero(np.diff(np.concatenate([[False], q, [False]]).astype(np.int8)))
    i0s, i1s = flips[::2], flips[1::2]                     # quiet windows i0 .. i1 - 1
    if not len(i0s):
        return q
    ta = t[i0s] - silence.HOP_S / 2.0
    tb = t[i1s - 1] + silence.HOP_S / 2.0
    min_n = int(round(MIN_GAP_S / silence.HOP_S))
    claimed = set()
    for w, nxt in zip(ws, ws[1:]):
        b0, b1 = sorted((float(w.end), float(nxt.start)))
        k0 = int(np.searchsorted(tb, b0 - BOUNDARY_S))
        best = None
        for k in range(k0, len(i0s)):
            if ta[k] > b1 + BOUNDARY_S:
                break
            d = max(0.0, ta[k] - b1, b0 - tb[k])
            if d <= BOUNDARY_S and (best is None or d < best[0]):
                best = (d, k)
        if best is not None:
            claimed.add(best[1])
    q = q & ~inside
    for k in claimed:
        i0, i1 = int(i0s[k]), int(i1s[k])
        own = np.diff(np.concatenate([[0], q[i0:i1].astype(np.int8), [0]]))
        longest = max([int(e) - int(b) for b, e in zip(np.flatnonzero(own == 1), np.flatnonzero(own == -1))] or [0])
        if longest < min_n:                                # no gap of its own outside the words: the dip is it
            q[i0:i1] = True
    return q


def _word_gaps(q: np.ndarray, t: np.ndarray, db: np.ndarray, ws: Sequence[Any]) -> np.ndarray:
    """q with a gap at every boundary between two transcribed words that has no quiet of its own (words run
    together): the quietest MIN_GAP_S around it (within WORD_GAP_S of the two words' meeting point, word timings
    from forced alignment). A sound is then one word, or the words said with no boundary heard between them -- a
    cut inside speech moves to the nearest end of a WORD, not past a whole run of words."""
    from . import silence
    q = q.copy()
    n = max(1, int(round(MIN_GAP_S / silence.HOP_S)))
    for w, nxt in zip(ws, ws[1:]):
        b0, b1 = sorted((float(w.end), float(nxt.start)))
        i0 = int(np.searchsorted(t, b0 - WORD_GAP_S, "left"))
        i1 = int(np.searchsorted(t, b1 + WORD_GAP_S, "right"))
        if i1 - i0 < n or q[i0:i1].any():
            continue                                     # a quiet gap there already (or nothing to search)
        k = i0 + int(np.argmin([db[j:j + n].mean() for j in range(i0, i1 - n + 1)]))
        q[k:k + n] = True
    return q


def _end_in(g: tuple[float, float], pa: float, pb: float) -> float:
    return g[0] + pa if g[1] - g[0] >= pa + pb else g[0] + (g[1] - g[0]) * pa / (pa + pb)


def _start_in(g: tuple[float, float], pa: float, pb: float) -> float:
    return g[1] - pb if g[1] - g[0] >= pa + pb else g[0] + (g[1] - g[0]) * pa / (pa + pb)


def end_after(sm: SpeechMap, k: int, pa: float, pb: float) -> float:
    """Where a clip whose last speech is sound k ends: ``pa`` after it, inside the quiet that follows (a gap to the
    next speech shorter than both pads is split between them; a breath or other sound stops it at its start)."""
    s = sm.sounds[k]
    nxt = sm.sounds[k + 1] if k + 1 < len(sm.sounds) else None
    if nxt is None:
        return min(sm.dur, s.s1 + pa)
    if nxt.speech:
        return _end_in((s.s1, nxt.s0), pa, pb)
    return min(s.s1 + pa, nxt.s0)


def start_before(sm: SpeechMap, k: int, pa: float, pb: float) -> float:
    """Where a clip whose first speech is sound k starts: ``pb`` before it, inside the quiet before it."""
    s = sm.sounds[k]
    prv = sm.sounds[k - 1] if k > 0 else None
    if prv is None:
        return max(0.0, s.s0 - pb)
    if prv.speech:
        return _start_in((prv.s1, s.s0), pa, pb)
    return max(s.s0 - pb, prv.s1)


def end_at(sm: SpeechMap, x: float, lo: float, pa: float, pb: float) -> float:
    """Where a clip playing the RAW from ``lo`` ends when the plan ends it at ``x`` (seconds): ``pa`` after its last
    speech (end_after). Inside speech (a word, or words heard as one sound): the clip plays on to its end -- the
    competitor played part of it -- or, when it has played less than KEEP_FRAC of it and speech before it, stops
    before it. Inside noise (NOISE_S: laughter, applause): ``x``, where the competitor cut. A clip with no speech
    before ``x`` keeps ``x`` (out of a breath)."""
    k = sm.sound_at(x)
    if k is not None and sm.sounds[k].noise:
        return x
    if k is not None and sm.sounds[k].speech:
        s = sm.sounds[k]
        j = sm.speech_before(s.s0)
        if j is not None and sm.sounds[j].s1 > lo + EPS and (x - s.s0) < KEEP_FRAC * (s.s1 - s.s0):
            return end_after(sm, j, pa, pb)
        return end_after(sm, k, pa, pb)
    j = sm.speech_before(x)
    if j is None or sm.sounds[j].s1 <= lo + EPS:
        return _out_of(sm, k, x, lo, None)
    return end_after(sm, j, pa, pb)


def start_at(sm: SpeechMap, x: float, hi: float, pa: float, pb: float) -> float:
    """Where a clip ending in the RAW at ``hi`` starts when the plan starts it at ``x``: ``pb`` before its first
    speech (start_before). Inside speech (a word, or words heard as one sound): the clip starts before it -- the
    competitor played part of it -- or, when less than KEEP_FRAC of it is left to play and speech follows in the
    clip, after it. Inside noise (laughter, applause): ``x``. A clip with no speech after ``x`` keeps ``x`` (out of a
    breath)."""
    k = sm.sound_at(x)
    if k is not None and sm.sounds[k].noise:
        return x
    if k is not None and sm.sounds[k].speech:
        s = sm.sounds[k]
        j = sm.speech_after(s.s1)
        if j is not None and sm.sounds[j].s0 < hi - EPS and (s.s1 - x) < KEEP_FRAC * (s.s1 - s.s0):
            return start_before(sm, j, pa, pb)
        return start_before(sm, k, pa, pb)
    j = sm.speech_after(x)
    if j is None or sm.sounds[j].s0 >= hi - EPS:
        return _out_of(sm, k, x, None, hi)
    return start_before(sm, j, pa, pb)


def _out_of(sm: SpeechMap, k: int | None, x: float, lo: float | None, hi: float | None) -> float:
    """x moved out of the (non-speech) sound k to its nearer end inside the clip [lo, hi]; x when k is None."""
    if k is None:
        return x
    s = sm.sounds[k]
    near0 = (x - s.s0) <= (s.s1 - x)
    if lo is not None:
        return s.s0 if near0 and s.s0 > lo + EPS else s.s1
    return s.s1 if not near0 and s.s1 < hi - EPS else s.s0


def start_after(sm: SpeechMap, x: float, pa: float, pb: float) -> float:
    """The first start allowed at or after x (where the clip before now ends): pb before the next speech, never
    before x."""
    k = sm.sound_at(x)
    j = sm.speech_after(sm.sounds[k].s1 if k is not None and sm.sounds[k].speech else x)
    if j is None:
        return sm.dur
    return max(start_before(sm, j, pa, pb), x)


def end_before(sm: SpeechMap, x: float, lo: float, pa: float, pb: float) -> float:
    """The last end allowed at or before x for a clip starting at lo: pa after its speech before the sound x is in
    (end_after); lo -- nothing left to play -- when it has none."""
    k = sm.sound_at(x)
    j = sm.speech_before(sm.sounds[k].s0 if k is not None and sm.sounds[k].speech else x)
    if j is None or sm.sounds[j].s1 <= lo + EPS:
        return lo
    return min(end_after(sm, j, pa, pb), x)


@dataclass
class Piece:
    """One A1 item of the plan: sequence frames [r0, r1) playing the RAW from frame ``src`` (sequence rate) at
    ``speed``; a locked edge is never moved (a cross dissolve, a cut V1 does not make there)."""
    label: str
    r0: int
    r1: int
    src: float
    speed: float
    lock_start: bool = False
    lock_end: bool = False
    shiftable: bool = False      # an audio line (not the picture's own audio): its source may move a little
    slack: int = 0               # a locked start (a cross dissolve of this many frames): the A1 cut may slide under it
    v_off: float | None = 0.0    # V1's RAW time minus A1's here (s; an audio line); None: V1 is not the RAW at 100 %
    v_speed: float | None = None  # V1's speed at the piece's first frame (None: no V1 there): extending the piece
    #                               extends that V1 too, at this speed


def _shift_jumps(ps: list[Piece], sm: SpeechMap, f: float, v1_cuts: set[int] | None) -> list[tuple[int, int]]:
    """Audio-line jumps inside speech: where A1 jumps a little (at most MAX_SHIFT_S) inside speech and the piece before
    the jump is an audio line (its sound is not the picture's own), that line moves by the jump so A1 plays on -- the
    picture is not touched; the lines before it in a chain move with it (from the last jump back). A jump where V1
    does not cut and only the piece after is an audio line moves that one instead. [(the moved piece's first
    sequence frame, frames)] -- ``ps`` changed in place."""
    cap = MAX_SHIFT_S * f + 1e-9
    moved: dict[int, int] = {}
    for p, q in reversed(list(zip(ps, ps[1:]))):
        if p.r1 != q.r0 or abs(p.speed - 1) > 1e-6 or abs(q.speed - 1) > 1e-6:
            continue
        j = int(round(q.src - (p.src + (p.r1 - p.r0))))
        if j == 0 or abs(j) > cap or not (_inside(sm, (p.src + (p.r1 - p.r0)) / f) or _inside(sm, q.src / f)):
            continue
        if p.shiftable and abs(moved.get(p.r0, 0) + j) <= cap:
            p.src += j
            moved[p.r0] = moved.get(p.r0, 0) + j
        elif (v1_cuts is not None and p.r1 not in v1_cuts and q.shiftable and q.r0 not in moved
              and abs(j) <= cap):
            q.src -= j
            moved[q.r0] = -j
    return [(r, d) for r, d in sorted(moved.items()) if d]


def _own_sound(ps: list[Piece], sm: SpeechMap, f: float, v1_cuts: set[int] | None, done: set[int]
               ) -> list[tuple[int, int]]:
    """Audio-line jumps inside speech too far to shift (_shift_jumps: MAX_SHIFT_S) where V1 does not cut: the audio
    line takes its picture's own sound (its source moves by its v_off), so A1 plays on in one take and cuts where the
    picture cuts -- where snap_edits puts the cut into the quiet. The competitor's J or L cut inside speech: the
    thorough Deadpool S09, whose sound switches to the next take 4 frames before its picture, 0.38 s on inside
    "Pitt's going". ``done``: the pieces _shift_jumps moved. [(the moved piece's first sequence frame, frames)] --
    ``ps`` changed in place."""
    if v1_cuts is None:
        return []
    cap = MAX_SHIFT_S * f + 1e-9
    moved: list[tuple[int, int]] = []
    for p, q in zip(ps, ps[1:]):
        if p.r1 != q.r0 or p.r1 in v1_cuts or abs(p.speed - 1) > 1e-6 or abs(q.speed - 1) > 1e-6:
            continue
        end = p.src + (p.r1 - p.r0)
        if abs(q.src - end) <= cap or not (_inside(sm, end / f) or _inside(sm, q.src / f)):
            continue
        for x in (q, p):                 # the audio line after the jump first: the sound before it is its own
            if not x.shiftable or x.v_off is None or x.r0 in done:
                continue
            d = int(round(x.v_off * f))
            if d:
                x.src += d
                x.v_off, x.shiftable = 0.0, False
                moved.append((x.r0, d))
            break
    return moved


def _slide_dissolves(ps: list[Piece], sm: SpeechMap, f: float) -> list[tuple[int, int]]:
    """A1 cuts under a cross dissolve (both sides locked: the picture mixes there, A1 cuts hard) that land inside
    speech: the cut slides inside the dissolve to the nearest frame where both sides are quiet -- the clip before
    plays on, the one after starts later (no change to the timeline). [(the cut's frame, frames slid)]."""
    out = []
    for p, q in zip(ps, ps[1:]):
        if p.r1 != q.r0 or not (p.lock_end and q.lock_start and q.slack) or abs(p.speed - 1) > 1e-6 or \
                abs(q.speed - 1) > 1e-6:
            continue
        b, a = (p.src + (p.r1 - p.r0)) / f, q.src / f
        if not (_inside(sm, b) or _inside(sm, a)):
            continue
        # inside the dissolve (both pictures there: up to its length later), or a little before it
        for d in sorted((d for d in range(-(q.slack // 2), q.slack + 1) if d), key=lambda d: (abs(d), d < 0)):
            if p.r0 < p.r1 + d < q.r1 and not _inside(sm, b + d / f) and not _inside(sm, a + d / f):
                out.append((p.r1, d))
                break
    return out


def shot_guard(sm: SpeechMap, na: float, nb: float, changes: Sequence[float], pa: float, pb: float,
               free_start: bool = True, free_end: bool = True, min_s: float | None = None) -> tuple[float, float]:
    """(start, end) of a clip playing RAW [na, nb) (s) with no piece of a shot shorter than ``min_s``
    (shots.MIN_SHOT_S) at its start or end -- ``changes``: the RAW's shot changes, on the clip's source times. A
    sliver of another shot with no speech in it goes (the padding stops at the shot change); one the speech runs
    into is shown for ``min_s`` (the clip plays on / starts earlier, in the quiet)."""
    from .shots import MIN_SHOT_S
    m = MIN_SHOT_S if min_s is None else float(min_s)
    for _ in range(4):
        moved = False
        inside = [c for c in changes if na + 1e-6 < c < nb - 1e-6]
        if free_end and inside and nb - inside[-1] < m - 1e-6:
            c = inside[-1]
            j = sm.speech_before(nb)
            kc = sm.sound_at(c)
            if j is None or sm.sounds[j].s1 <= c + 1e-3 or (kc is not None and sm.sounds[kc].noise):
                nb = c                       # the speech ended before the shot change, or noise runs over it: cut there
            else:                                                     # it runs into the new shot: show it m long
                x = min(sm.dur, c + m)
                k = sm.sound_at(x)
                nb = max(x, end_after(sm, k, pa, pb)) if k is not None and sm.sounds[k].speech else x
            moved = True
        inside = [c for c in changes if na + 1e-6 < c < nb - 1e-6]
        if free_start and inside and inside[0] - na < m - 1e-6:
            c = inside[0]
            j = sm.speech_after(na)
            kc = sm.sound_at(c)
            if j is None or sm.sounds[j].s0 >= c - 1e-3 or (kc is not None and sm.sounds[kc].noise):
                na = c                       # no speech before the shot change, or noise runs over it: start there
            else:
                x = max(0.0, c - m)
                k = sm.sound_at(x)
                na = min(x, start_before(sm, k, pa, pb)) if k is not None and sm.sounds[k].speech else x
            moved = True
        if not moved or nb <= na:
            break
    return na, nb


def _a1_gap_edges(ps: Sequence[Piece], sm: SpeechMap, f: float, pa: float, pb: float, v1_cuts: set[int] | None,
                  seq_end: int | None, rows: list[dict]) -> list[tuple[int, int]]:
    """A1 edges where V1 does not cut and A1 has nothing on the other side (the next piece is muted: a cutaway over
    music / voice-over) that land inside speech: the A1 edge alone moves (an A1 slide: the picture is not touched)
    -- the audio plays on into the silence until the word ends (pa after it), or, when that silence is too short,
    stops before the word (pa after the speech before it); an A1 start the same way, earlier. [(frame, frames)]."""
    out: list[tuple[int, int]] = []
    if v1_cuts is None:
        return out
    for i, p in enumerate(ps):
        if abs(p.speed - 1.0) > 1e-6:                                     # at 100 % only
            continue
        nxt = ps[i + 1] if i + 1 < len(ps) else None
        prv = ps[i - 1] if i > 0 else None
        a, b = p.src / f, (p.src + (p.r1 - p.r0)) / f
        room_after = (nxt.r0 if nxt is not None else (seq_end if seq_end is not None else p.r1)) - p.r1
        if not p.lock_end and p.r1 not in v1_cuts and room_after > 0 and _inside(sm, b):
            k = sm.sound_at(b)
            on = int(round((end_after(sm, k, pa, pb) - b) * f))             # play on to the end of the word
            j = sm.speech_before(sm.sounds[k].s0)
            back = int(round((end_after(sm, j, pa, pb) - b) * f)) if j is not None else None
            d = on if 0 < on <= room_after else (back if back is not None and p.r1 + back > p.r0 else 0)
            if d:
                out.append((p.r1, d))
                rows.append({"clip": p.label, "edge": "end", "at": p.r1, "from_s": b, "to_s": b + d / f,
                             "frames": d, "said": sm.said(min(b, b + d / f) - 0.15, max(b, b + d / f) + 0.15),
                             "inside": True})
        room_before = p.r0 - (prv.r1 if prv is not None else 0)
        if not p.lock_start and p.r0 not in v1_cuts and room_before > 0 and _inside(sm, a):
            k = sm.sound_at(a)
            early = int(round((start_before(sm, k, pa, pb) - a) * f))       # start before the word
            j = sm.speech_after(sm.sounds[k].s1)
            late = int(round((start_before(sm, j, pa, pb) - a) * f)) if j is not None else None
            d = early if -room_before <= early < 0 else (late if late is not None and p.r0 + late < p.r1 else 0)
            if d:
                out.append((p.r0, d))
                rows.append({"clip": p.label, "edge": "start", "at": p.r0, "from_s": a, "to_s": a + d / f,
                             "frames": d, "said": sm.said(min(a, a + d / f) - 0.15, max(a, a + d / f) + 0.15),
                             "inside": True})
    return out


def snap_edits(pieces: Sequence[Piece], sm: SpeechMap, fps: Fraction, pad_after: float, pad_before: float,
               v1_cuts: set[int] | None = None, src_max: float | None = None, shots: Sequence[float] | None = None,
               seq_end: int | None = None, keep: Sequence[tuple[int, int]] = ()
               ) -> tuple[list[tuple[int, int]], list[tuple[int, int, str, float]], list[dict], list[tuple[int, int]]]:
    """(the trims: removed sequence frames [a, b), the extensions: (at, frames, side, RAW frame the added frames
    start at) -- silence.Insert --, one row per moved cut, the audio lines moved: (first frame, frames)) that put
    every audio cut of ``pieces`` into the quiet of ``sm`` (module docstring). Only clips at 100 % move; a piece
    running on in the very next RAW frame is one take (no cut there). ``v1_cuts``: the sequence frames where V1
    cuts -- an A1 edge elsewhere moves only when the pieces between it and V1's last cut went (the picture cuts there
    then); an audio line jumping a little inside speech there plays on instead (_shift_jumps), one jumping further
    takes its picture's own sound, so A1 cuts where V1 cuts (_own_sound). ``shots``: the RAW's
    shot changes (s): no clip starts or ends with a sliver of another shot (shot_guard). ``keep``: sequence frames
    [a, b) a clip is never trimmed into -- the beats the competitor shows an action caption over ("*looks over*": 021's
    reaction shot after "gentlemen", trimmed to 5 frames of quiet -- a flash -- where you kept the competitor's 0.5 s).
    A clip left with nothing to play goes."""
    f = float(fps)
    hi_s = sm.dur if src_max is None else min(sm.dur, float(src_max) / f)
    ps = [Piece(**vars(p)) for p in sorted(pieces, key=lambda p: p.r0)]
    shifts = _shift_jumps(ps, sm, f, v1_cuts)
    shifts += _own_sound(ps, sm, f, v1_cuts, {r for r, _ in shifts})
    slides = _slide_dissolves(ps, sm, f)
    trims: list[tuple[int, int]] = []
    inserts: list[tuple[int, int, str, float]] = []
    rows: list[dict] = []
    prev = None            # the last clip kept: (piece, new RAW end s, planned RAW end s, new RAW start s, joined on)
    reach = None           # the sequence frame up to which nothing plays after it (pieces that went in between)
    gone_from = None       # the first frame of the pieces that went right before this one (V1 cuts there)

    def free(r: int, lock: bool) -> bool:
        return not lock and (v1_cuts is None or r in v1_cuts or (gone_from is not None and gone_from in v1_cuts))

    for i, p in enumerate(ps):
        if abs(p.speed - 1.0) > 1e-6:
            prev = reach = gone_from = None
            continue
        a, b = p.src / f, (p.src + (p.r1 - p.r0)) / f            # the RAW it plays (s)
        nxt = ps[i + 1] if i + 1 < len(ps) else None
        touching = prev is not None and reach == p.r0
        # one take with the clip before: it now ends (where it was planned to, or played on to) where this one starts
        # -- Task 10: a sliver between them gone, the clip before playing on through its place, the clip after was
        # still taken for a cut inside the word and moved (A1 jumped inside "know what's funny")
        cont_in = touching and abs(prev[1] - a) < 0.5 / f
        cont_out = (nxt is not None and nxt.r0 == p.r1 and abs(nxt.speed - 1.0) < 1e-6
                    and abs(nxt.src / f - b) < 0.5 / f)
        free_start = free(p.r0, p.lock_start) and not cont_in
        free_end = not p.lock_end and (v1_cuts is None or p.r1 in v1_cuts) and not cont_out
        # a tiny jump (or repeat) of the RAW inside speech at a cut: the two clips play on as one take -- the clip
        # after starts exactly where the one before ends (the picture still cuts, the sound does not jump)
        join_out = (free_end and nxt is not None and nxt.r0 == p.r1 and abs(nxt.speed - 1.0) < 1e-6
                    and not nxt.lock_start and 0.5 / f <= abs(nxt.src / f - b) <= MAX_JUMP_S + 1e-9
                    and (_inside(sm, b) or _inside(sm, nxt.src / f)))
        join_in = touching and free_start and prev[4]
        na = (prev[1] if join_in else start_at(sm, a, b, pad_after, pad_before)) if free_start else a
        nb = end_at(sm, b, a, pad_after, pad_before) if free_end and not join_out else b
        if join_out and nxt.src / f > b:
            nb = nxt.src / f                         # a skip: this clip plays on to where the next one starts
        if touching and free_start and not join_in and na < prev[1] - 0.5 / f and nb > prev[3] + 0.5 / f:
            na = start_after(sm, prev[1], pad_after, pad_before)    # never show again what the clip before shows
        if shots and p.v_off is not None and (free_start or free_end):
            cs = [c - p.v_off for c in shots]                          # the shot changes on A1's source times
            na, nb = shot_guard(sm, na, nb, cs, pad_after, pad_before, free_start and not join_in,
                                free_end and not join_out)
            if touching and free_start and na < prev[1] - 0.5 / f and nb > prev[3] + 0.5 / f:
                na = prev[1]                                           # never back into what the clip before shows
        if p.v_speed is not None and abs(p.v_speed - 1.0) > 1e-6 and abs(p.v_speed) > 1e-9:
            # a retimed picture over this audio line: the ripple extends V1 too, at its speed (video018 S15: 74
            # frames = 85 frames of picture at 115 %, back into what S13+S14 shows -- the repeat removal then cut
            # A1 into the laugh after 'minute?'). No more than MAX_RETIMED_EXT_S of new picture: the cut moves into
            # the clip instead, and the clip goes when nothing is left
            cap = MAX_RETIMED_EXT_S / abs(p.v_speed) + 0.5 / f
            if a - na > cap:
                na = start_after(sm, a, pad_after, pad_before) if _inside(sm, a) else a
            if nb - b > cap:
                nb = end_before(sm, b, na, pad_after, pad_before) if _inside(sm, b) else b
        for k0, k1 in keep:                     # an action-captioned beat this clip shows: never trimmed into
            if k1 > p.r0 and k0 < p.r1:          # (unless that would cut inside a word: the speech-safe cut wins)
                lo_k = a + (max(p.r0, k0) - p.r0) / f
                hi_k = a + (min(p.r1, k1) - p.r0) / f
                if free_start and na > lo_k + 1e-9 and not _inside(sm, lo_k):
                    na = lo_k
                if free_end and nb < hi_k - 1e-9 and not _inside(sm, hi_k):
                    nb = hi_k
        if p.r0 == 0 and na < a:
            # the edit's first frame is never before the competitor's (it may start later): on all 9 of your answer
            # keys you start at or after it, and every time this step had started earlier you moved it back
            # (video1 -0.42 s, video017 -0.23 s, video018 -0.35 s with 16 frames of the shot before)
            na = a
        na, nb = max(0.0, na), min(hi_s, nb)
        da = int(round((na - a) * f))
        db = int(round((nb - b) * f))
        if da >= p.r1 - p.r0 and (p.r1 + db) - (p.r0 + da) > 0:
            # its start moved past its own frames (a sliver the clip before plays on through, or a start pushed to
            # the end of a word that outlasts the clip): a trim of the original timeline reaching past the clip would
            # cut the next clip's first frames, and the extensions at its end would belong to no clip -- a hole (the
            # Spider-Man S11: one frame inside "kids,"). The clip before, playing on into this take, plays what this
            # one would; otherwise it goes
            if join_in and prev is not None and prev[0].r1 == p.r0:
                end = nb
                if shots and prev[0].v_off is not None:                # never a sliver of the next RAW shot
                    _, end = shot_guard(sm, prev[3], nb, [c - prev[0].v_off for c in shots], pad_after, pad_before,
                                        False, True)
                extra = int(round((min(hi_s, end) - prev[1]) * f))
                if extra > 0:
                    inserts.append((prev[0].r1, extra, "end", prev[1] * f))
                    rows.append({"clip": prev[0].label, "edge": "end", "at": prev[0].r1, "from_s": prev[1],
                                 "to_s": prev[1] + extra / f, "frames": extra,
                                 "said": sm.said(prev[1] - 0.15, prev[1] + extra / f + 0.15),
                                 "inside": _inside(sm, prev[1])})
                prev = (prev[0], prev[1] + max(0, extra) / f, prev[2], prev[3], False)
            trims.append((p.r0, p.r1))
            rows.append({"clip": p.label, "edge": "whole", "at": p.r0, "from_s": a, "to_s": b,
                         "frames": -(p.r1 - p.r0), "said": sm.said(a, b), "inside": False})
            if touching or prev is None:
                reach = p.r1
                gone_from = p.r0 if gone_from is None else gone_from
            continue
        if (p.r1 + db) - (p.r0 + da) <= 0:                         # nothing left to play
            trims.append((p.r0, p.r1))
            rows.append({"clip": p.label, "edge": "whole", "at": p.r0, "from_s": a, "to_s": b,
                         "frames": -(p.r1 - p.r0), "said": sm.said(a, b), "inside": False})
            if touching or prev is None:
                reach = p.r1
                gone_from = p.r0 if gone_from is None else gone_from
            continue
        gone_from = None
        if da > 0:
            trims.append((p.r0, p.r0 + da))
        elif da < 0:
            inserts.append((p.r0, -da, "start", p.src + da))
        if db < 0:
            trims.append((p.r1 + db, p.r1))
        elif db > 0:
            inserts.append((p.r1, db, "end", p.src + (p.r1 - p.r0)))
        for edge, d, old in (("start", da, a), ("end", db, b)):
            if d:
                new = old + d / f
                rows.append({"clip": p.label, "edge": edge, "at": p.r0 if edge == "start" else p.r1, "from_s": old,
                             "to_s": new, "frames": d, "said": sm.said(min(old, new) - 0.15, max(old, new) + 0.15),
                             "inside": _inside(sm, old)})
        prev, reach = (p, b + db / f, b, a + da / f, join_out), p.r1
    gaps = _a1_gap_edges(ps, sm, f, pad_after, pad_before, v1_cuts, seq_end, rows)
    for at, d in shifts:
        rows.append({"clip": next(p.label for p in ps if p.r0 == at), "edge": "audio line", "at": at,
                     "from_s": None, "to_s": None, "frames": d, "said": "", "inside": True})
    for at, d in slides:
        rows.append({"clip": next(p.label for p in ps if p.r0 == at), "edge": "dissolve", "at": at,
                     "from_s": None, "to_s": None, "frames": d, "said": "", "inside": True})
    return trims, inserts, rows, shifts + [(at, d, "slide") for at, d in slides + gaps]


def plan_cuts(clips: Sequence[Any], audio: Sequence[dict], sm: SpeechMap, fps: Fraction, st: Any, n_frames: int,
              src_max: float | None = None, fac: int = 1, shots: Sequence[float] | None = None,
              keep: Sequence[tuple[int, int]] = ()) -> tuple[Any, list[dict]]:
    """(silence.Ripple of the speech-safe cuts, one row per moved cut) of the Premiere plan's V1 clips
    (export_xml_edl.PremiereClip) and A1 items: snap_edits on the A1 items, V1 cutting where it cuts; both sides of a
    cross dissolve stay (an A1 cut inside speech slides under the dissolve: _slide_dissolves). The trims and extensions
    apply to V1 and A1 alike (picture and sound stay in sync). ``fac``: sequence frames per competitor frame;
    ``shots``: the RAW's shot changes (s) -- no clip starts or ends with a sliver of another shot."""
    from .silence import Cut, Insert, Ripple
    f = float(fps)
    v1_cuts: set[int] = set()
    locked: dict[int, int] = {}                  # a cross dissolve's cut -> its length (sequence frames)
    for cl in clips:
        v1_cuts |= {int(cl.rec_start), int(cl.rec_end)}
        if cl.start == -1:
            locked[int(cl.rec_start)] = max(0, int(getattr(cl.ev, "dissolve_in", 0) or 0)) * int(fac)
    v1_cuts -= set(locked)
    pieces = []
    for it in audio:
        seg = it.get("seg")
        label = f"S{int(seg.id):02d}" if seg is not None else str(it.get("label") or "?")
        v = next((c for c in clips if c.rec_start <= int(it["start"]) < c.rec_end), None)
        v_off = ((v.src_in + (int(it["start"]) - v.rec_start) * float(v.speed) - float(it["in"])) / f
                 if v is not None and abs(float(v.speed) - 1.0) < 1e-6 else None)
        pieces.append(Piece(label, int(it["start"]), int(it["end"]), float(it["in"]), float(it["speed"]),
                            int(it["start"]) in locked, int(it["end"]) in locked, it.get("what") == "audio line",
                            locked.get(int(it["start"]), 0), v_off, float(v.speed) if v is not None else None))
    trims, inserts, rows, shifts = snap_edits(pieces, sm, fps, float(st.pad_after), float(st.pad_before), v1_cuts,
                                              src_max, shots, int(n_frames), keep)
    merged: list[list[int]] = []
    for a, b in sorted(trims):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    rp = Ripple([Cut(a, b, a / f, b / f) for a, b in merged], int(n_frames),
                [Insert(at, d, side, src) for at, d, side, src in inserts],
                [(at, d) for at, d, *kind in shifts if not kind], [(at, d) for at, d, *kind in shifts if kind])
    lost = [i for i in rp.inserts if any(c.a < i.at < c.b or (i.at == c.b and i.side == "end") or
                                         (i.at == c.a and i.side == "start") for c in rp.cuts)]
    if lost:                     # an extension no clip carries would move everything after it: a hole on V1 and A1
        from .common import log
        log.error("speech-safe cuts: %d extension(s) inside removed frames, no clip plays them: %s", len(lost),
                  [(i.at, i.frames, i.side) for i in lost])
    return rp, rows


def _inside(sm: SpeechMap, x: float) -> bool:
    k = sm.sound_at(x)
    return k is not None and sm.sounds[k].speech and not sm.sounds[k].noise


def _inside_sound(sm: SpeechMap, x: float) -> bool:
    """Inside speech or noise (laughter, applause): a short dissolve there becomes a cut (harden_dissolves) -- its
    incoming frames are often another shot's (021 S11|S12)."""
    k = sm.sound_at(x)
    return k is not None and sm.sounds[k].speech


def check(edges: Sequence[tuple[str, str, float, int]], sm: SpeechMap, fps: Fraction, tol_frames: float = 0.5
          ) -> list[dict]:
    """The cuts that land inside speech: ``edges`` = (clip label, "start" / "end", RAW time s, sequence frame) of
    every audio cut of a finished edit; one row each (with the words there) for a time more than ``tol_frames``
    inside a speech sound."""
    tol = tol_frames / float(fps)
    out = []
    for label, edge, x, at in edges:
        k = sm.sound_at(x, tol)
        if k is not None and sm.sounds[k].speech and not sm.sounds[k].noise:       # noise: a cut may land in it
            s = sm.sounds[k]
            out.append({"clip": label, "edge": edge, "raw_s": x, "at": at, "speech": (s.s0, s.s1),
                        "said": sm.said(s.s0, s.s1) or s.why})       # what the sound holds, not the words near it
    return out


def audio_cuts(items: Sequence[dict], fps: Fraction, n_frames: int | None = None) -> list[tuple[str, str, float, int]]:
    """The audio cuts of A1 items [{start, end, in, out, speed, name}] (sequence-rate frames; the XML's own): the
    start and the end of every item, unless the item before / after plays on in the very next RAW frame (one take)
    or it is the very start / end (``n_frames``) of the sequence; items at speeds other than 100 % are left out."""
    f = float(fps)
    its = sorted(items, key=lambda d: d["start"])
    out = []
    for i, it in enumerate(its):
        if abs(float(it.get("speed", 1.0)) - 1.0) > 1e-6:
            continue
        prv = its[i - 1] if i else None
        nxt = its[i + 1] if i + 1 < len(its) else None
        name = str(it.get("name") or it.get("label") or "?")
        joined_in = (prv is not None and prv["end"] == it["start"] and abs(float(prv.get("speed", 1.0)) - 1.0) < 1e-6
                     and prv["out"] == it["in"])
        joined_out = (nxt is not None and nxt["start"] == it["end"] and abs(float(nxt.get("speed", 1.0)) - 1.0) < 1e-6
                      and nxt["in"] == it["out"])
        if it["start"] > 0 and not joined_in:
            out.append((name, "start", it["in"] / f, int(it["start"])))
        if (n_frames is None or it["end"] < n_frames) and not joined_out:
            out.append((name, "end", it["out"] / f, int(it["end"])))
    return out
