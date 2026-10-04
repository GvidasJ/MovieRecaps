"""align.py: forced alignment -- each transcribed word timed to the audio it is in (wav2vec2 CTC: torchaudio's MMS_FA
model, on the GPU when there is one).

The speech models time words coarsely (Whisper from its cross-attention, Parakeet in 80 ms steps) or not at all
(Canary-Qwen). Forced alignment takes the words as heard and finds, frame by frame (20 ms), where each one is: the
CTC path through the model's per-frame letter probabilities that spells exactly those words. ``*`` (a wildcard
token) at the start and end of each window soaks up speech the transcript left out. Numbers are aligned as they
are spoken ("25" -> "twenty five"). A word with nothing to align (no letters) keeps the time it had.

``refine_onsets`` then moves a word's start onto the first sound of the word where it follows a pause: the 5 ms
loudness rises out of the quiet before it (sample-exact where the frame grid is 20 ms).
"""
from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Sequence

import numpy as np

from .captions import Word

SR = 16000
MAX_WINDOW_S = 120.0            # one pass up to this long; longer audio in windows split at the widest pauses
PAD_S = 0.6                     # each window's audio reaches this far past its first / last word
_MODELS: dict[str, tuple] = {}

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def spell(n: int) -> str:
    """An integer in words (0 .. 999 999 999)."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10 - 2] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + spell(n % 100))
    for size, word in ((1_000_000, "million"), (1000, "thousand")):
        if n >= size:
            return spell(n // size) + " " + word + ("" if n % size == 0 else " " + spell(n % size))
    return str(n)


def _number_words(m: re.Match) -> str:
    s = m.group(0).replace(",", "")
    if not s.isdigit() or len(s) > 9:
        return " ".join(_ONES[int(c)] for c in s if c.isdigit())
    n = int(s)
    if len(s) == 4 and 1100 <= n <= 2099 and n % 100:          # a year: "1999" -> nineteen ninety nine
        return spell(n // 100) + " " + (("oh " + _ONES[n % 100]) if n % 100 < 10 else spell(n % 100))
    return spell(n)


def speakable(text: str) -> str:
    """The word as letters to align: lower case a-z and ', digits spelled out, & / % / + as words."""
    t = str(text).lower().replace("’", "'").replace("&", " and ").replace("%", " percent ").replace("+", " plus ")
    t = re.sub(r"\d[\d,]*", _number_words, t)
    t = re.sub(r"[^a-z' ]+", " ", t)
    return " ".join(w.strip("'") for w in t.split() if w.strip("'"))


def _device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _model(device: str):
    if device not in _MODELS:
        import torchaudio
        bundle = torchaudio.pipelines.MMS_FA
        m = bundle.get_model(with_star=True).to(device).eval()
        _MODELS[device] = (m, bundle.get_dict(star="*"))
    return _MODELS[device]


def available() -> str | None:
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
        from torchaudio.functional import forced_align  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return f"forced alignment needs torch + torchaudio ({type(e).__name__}: {e})"
    return None


PHRASE_GAP_S = 0.35            # words further apart than this (as heard) are aligned in separate windows
PHRASE_PAD_S = 0.35            # a phrase window reaches this far past its first / last word
MAX_SHIFT_S = 0.35             # alignment may move a timed word this far; further is a failed alignment (kept)


def _align_window(y16: np.ndarray, t0: float, t1: float, words: Sequence[Word], dev: str
                  ) -> list[tuple[float, float, float] | None]:
    """Forced alignment of ``words`` to y16[t0:t1] (``*`` at both ends): per word (start, end, score) in seconds of
    y16, or None (nothing alignable)."""
    import torch
    from torchaudio.functional import forced_align, merge_tokens
    model, vocab = _model(dev)
    a, b = max(0, int(t0 * SR)), min(len(y16), int(np.ceil(t1 * SR)))
    toks, owner = [vocab["*"]], [-1]
    for k, w in enumerate(words):
        for ch in speakable(w.raw or w.text).replace(" ", ""):
            if ch in vocab:
                toks.append(vocab[ch])
                owner.append(k)
    toks.append(vocab["*"])
    owner.append(-1)
    res: list[tuple[float, float, float] | None] = [None] * len(words)
    if len(toks) <= 2 or b - a < SR // 20:
        return res
    wav = torch.from_numpy(np.ascontiguousarray(y16[a:b], np.float32))[None].to(dev)
    with torch.inference_mode():
        emission, _ = model(wav)
    n_frames = emission.size(1)
    if n_frames < len(toks) + 2:
        return res
    ali, scores = forced_align(emission, torch.tensor([toks], dtype=torch.int32, device=dev), blank=0)
    spans = merge_tokens(ali[0], scores[0].exp())
    ratio = (b - a) / n_frames / SR
    first: dict[int, float] = {}
    last: dict[int, float] = {}
    conf: dict[int, list[float]] = {}
    for sp_, k in zip(spans, owner):
        if k >= 0:
            first.setdefault(k, sp_.start * ratio)
            last[k] = sp_.end * ratio
            conf.setdefault(k, []).append(float(sp_.score))
    for k in first:
        res[k] = (a / SR + first[k], a / SR + max(first[k], last[k]), float(np.mean(conf[k])))
    return res


def phrases(words: Sequence[Word], gap_s: float = PHRASE_GAP_S, max_s: float = 25.0) -> list[list[int]]:
    """Word indices grouped into phrases: a new one after a pause longer than gap_s, or at max_s."""
    out: list[list[int]] = []
    for i, w in enumerate(words):
        if out and (w.start - words[out[-1][-1]].end <= gap_s and w.end - words[out[-1][0]].start <= max_s):
            out[-1].append(i)
        else:
            out.append([i])
    return out


def align(y16: np.ndarray, words: Sequence[Word], device: str = "auto", timed: bool = True
          ) -> tuple[list[Word], dict]:
    """The words with start / end from forced alignment (``prob`` kept). ``timed``: the words carry the engine's
    own (coarse) timings -- each phrase is aligned in a window around them, and a word the alignment moves more
    than MAX_SHIFT_S keeps its time (a failed alignment, e.g. the wildcard swallowed it). Untimed words (Canary-Qwen:
    spread over each ~28 s piece) are first aligned piece by piece, then refined the same way. Returns (words, info:
    {device, aligned, kept, windows})."""
    words = list(words)
    dev = _device(device)
    info = {"device": dev, "aligned": 0, "kept": 0, "windows": 0}
    if not words or not len(y16):
        return words, info
    if not timed:                                   # pass 1: whole pieces (the engine's own split), loose
        groups: list[list[int]] = []
        for i, w in enumerate(words):
            if groups and w.start - words[groups[-1][-1]].start < 1.0 and \
                    w.start - words[groups[-1][0]].start < MAX_WINDOW_S:
                groups[-1].append(i)
            else:
                groups.append([i])
        rough = list(words)
        for g in groups:
            t0 = max(0.0, words[g[0]].start - 1.0)
            t1 = min(len(y16) / SR, max(words[g[-1]].end, words[g[-1]].start) + 3.0)
            got = _align_window(y16, t0, t1, [words[i] for i in g], dev)
            info["windows"] += 1
            for i, r in zip(g, got):
                if r is not None:
                    rough[i] = replace(words[i], start=r[0], end=r[1])
        words = rough
    out = list(words)
    kept: list[int] = []
    for g in phrases(words):                        # pass 2: phrase by phrase, around where the words are
        t0 = max(0.0, words[g[0]].start - PHRASE_PAD_S)
        t1 = min(len(y16) / SR, words[g[-1]].end + PHRASE_PAD_S)
        got = _align_window(y16, t0, t1, [words[i] for i in g], dev)
        info["windows"] += 1
        for i, r in zip(g, got):
            if r is None or abs(r[0] - words[i].start) > MAX_SHIFT_S:
                info["kept"] += 1
                kept.append(i)
                continue
            out[i] = replace(words[i], start=r[0], end=max(r[0], r[1]))
            info["aligned"] += 1
    return _in_order(out, kept), info


def _in_order(words: Sequence[Word], kept: Sequence[int]) -> list[Word]:
    """Each word that kept its own time (its alignment failed) back in the spoken order: when that time falls
    outside the words around it, it moves between them (its length kept as far as the room allows)."""
    out = list(words)
    for i in sorted(kept):
        lo = out[i - 1].end if i > 0 else 0.0
        hi = out[i + 1].start if i + 1 < len(out) else math.inf
        if lo - 1e-6 <= out[i].start <= hi + 1e-6:
            continue
        hi = max(hi, lo)
        d = min(out[i].end - out[i].start, hi - lo)
        a = min(max(out[i].start, lo), hi - d)
        out[i] = replace(out[i], start=a, end=a + d)
    return out


def refine_onsets(y16: np.ndarray, words: Sequence[Word], pause_s: float = 0.08, look_s: float = 0.06) -> list[Word]:
    """A word that follows a pause (``pause_s`` of quiet before it) starts at its first sound: the moment within
    ``look_s`` of its aligned start where the 5 ms loudness rises above the quiet before it (the larger of 3x the
    quiet's level and 10 % of the word's own peak)."""
    out = list(words)
    hop = int(0.005 * SR)
    if not len(y16):
        return out
    k = len(y16) // hop
    rms = np.sqrt(np.mean(np.asarray(y16[:k * hop], np.float32).reshape(k, hop) ** 2, axis=1) + 1e-12)
    for i, w in enumerate(words):
        prev_end = words[i - 1].end if i else -1.0
        if w.start - prev_end < pause_s:
            continue
        a = max(0, int((w.start - look_s) / 0.005))
        b = min(k, int((w.start + look_s) / 0.005) + 1)
        q0 = max(0, int((max(prev_end, w.start - 0.3)) / 0.005))
        quiet = float(np.median(rms[q0:a])) if a > q0 else float(rms[a])
        peak = float(np.max(rms[a:min(k, int(w.end / 0.005) + 1)])) if b > a else 0.0
        thr = max(3.0 * quiet, 0.1 * peak)
        hit = next((j for j in range(a, b) if rms[j] >= thr), None)
        if hit is not None:
            out[i] = replace(w, start=min(w.end, hit * 0.005))
    return out
