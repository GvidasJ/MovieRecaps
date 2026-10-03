"""Captions: unclear speech double-checked against the source (captions.run_captions: voice mode and the
transcript fallbacks of competitor mode).

The transcript of the edit's audio is UNSURE about a word when
* it heard the word with low confidence (below LOW_PROB: mumbling, an odd word), or
* music or noise lies under it: the word stands less than NOISY_DB above the sound bed around it (the quietest
  tenth of the BED_S on each side), or
* an edit point falls inside it, or within CUT_TOL_S of its edges: the word may be cut off.

Each unsure spot is transcribed again from the SOURCE -- the RAW footage the edit plays there (render_preview's own
audio map, so J/L cuts, speed changes and audio lines are followed), or the voice-over file -- with CONTEXT_S on each
side, so the model hears the whole sentence and not just the cut piece, with a bigger model (``--caption-recheck-
model``, default medium.en) for these spots only. The source's words are mapped back onto the edit's timeline
through the same map; a word cut off at an edit point keeps the part the edit plays.

The two versions are aligned word by word around the spot. Where they agree, the word is confirmed. Where they
differ, the source's version is used when it is clearly more confident (mean word confidence, MARGIN), and the
same words written two ways ("gonna" / "going to") are no difference; a competitor caption on screen there that was
read clearly is a third opinion, and it decides when exactly one version agrees with it -- the version and the words
kept on either side of it, in that order, so one common word cannot agree on its own. A word only the source
heard is added when it is clear (CLEAR_PROB) or the caption has it; a word only the edit's transcript heard is never
dropped. Nothing is guessed: when the version kept is still unsure (a word below LOW_PROB that no second opinion
backs), it is kept and listed with the alternatives heard. Unsure words that cannot be rechecked (no source audio
there) are listed the same way.
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

import numpy as np

from .captions import Word, norm

SR = 16000
LOW_PROB = 0.5            # a word heard with less confidence than this is unsure
CLEAR_PROB = 0.7          # a word only the source heard is added when it is at least this sure
MARGIN = 0.1              # the source's version replaces the edit's when it is at least this much more confident
NOISY_DB = 12.0           # a word less than this above the sound bed around it has music / noise under it
BED_S = 1.5               # the sound bed: the quietest tenth of the 20 ms frames this far on each side of the word
CUT_TOL_S = 0.05          # an edit point this close to a word (or inside it) may cut it off
CONTEXT_S = 3.0           # the source is transcribed again with this much on each side of the unsure words
MIN_OVERLAP = 0.35        # a source word is in the edit when this much of it (or its middle) is in the part played
HOP_S = 0.02
RECHECK_MODEL = "medium.en"


@dataclass
class Piece:
    """A stretch of the edit's audio: edit seconds [t0, t1) play source seconds src0 + v (t - t0)."""
    t0: float
    t1: float
    src0: float
    v: float = 1.0

    def src(self, t: float) -> float:
        return self.src0 + self.v * (t - self.t0)

    def edit(self, s: float) -> float:
        return self.t0 + (s - self.src0) / self.v

    @property
    def src_range(self) -> tuple[float, float]:
        return self.src0, self.src(self.t1)


def pieces_from_cutlist(cutlist: Any, sr: int = SR) -> list[Piece]:
    """The edit's audio map (render_preview.audio_pieces, build_audio's own) in seconds; reversed pieces left out."""
    from .render_preview import audio_pieces
    return [Piece(a / sr, b / sr, float(tau), float(v)) for _sid, a, b, tau, v in audio_pieces(cutlist, sr) if v > 0]


def piece_at(pieces: Sequence[Piece], t: float) -> Piece | None:
    """The piece playing at edit second t (in a crossfade: the incoming one)."""
    hits = [p for p in pieces if p.t0 <= t < p.t1]
    return max(hits, key=lambda p: p.t0) if hits else None


def cut_times(pieces: Sequence[Piece], end_s: float) -> list[float]:
    """Edit seconds where the sound jumps: a piece starts or ends without the source continuing (a cut, or the audio
    starting / stopping), except at the timeline's ends."""
    out: set[float] = set()
    for p in pieces:
        for t, s, starts in ((p.t0, p.src0, True), (p.t1, p.src(p.t1), False)):
            if t <= 1e-6 or t >= end_s - 1e-6:
                continue
            go_on = any(q is not p and abs((q.t1 if starts else q.t0) - t) < 1e-3
                        and abs((q.src(q.t1) if starts else q.src0) - s) < 0.02 for q in pieces)
            if not go_on:
                out.add(round(t, 4))
    return sorted(out)


def frame_rms(y: np.ndarray, sr: int = SR) -> np.ndarray:
    hop = max(1, int(round(sr * HOP_S)))
    y = np.asarray(y, np.float32)
    n = len(y) // hop
    return np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12) if n else np.zeros(0, np.float32)


def word_snr(rms: np.ndarray, w: Word) -> float | None:
    """dB the word stands above the sound bed around it (None: no sound to measure)."""
    i0, i1 = int(w.start / HOP_S), max(int(w.start / HOP_S) + 1, int(math.ceil(w.end / HOP_S)))
    word = rms[i0:i1]
    around = rms[max(0, int((w.start - BED_S) / HOP_S)):int(math.ceil((w.end + BED_S) / HOP_S))]
    if not len(word) or not len(around):
        return None
    level = float(np.sqrt(np.mean(word ** 2)))
    if level < 1e-4:
        return None
    return 20.0 * math.log10(level / max(float(np.percentile(around, 10)), 1e-6))


def unsure_words(words: Sequence[Word], y16: np.ndarray | None, cuts: Sequence[float],
                 only: Sequence[tuple[float, float]] | None = None) -> dict[int, list[str]]:
    """{word index: why it is unsure} (see the module docstring); ``only``: just the words whose middle lies in one
    of these edit-second ranges (the transcript fallbacks of competitor mode)."""
    rms = frame_rms(y16) if y16 is not None and len(y16) else np.zeros(0, np.float32)
    out: dict[int, list[str]] = {}
    for i, w in enumerate(words):
        mid = 0.5 * (w.start + w.end)
        if only is not None and not any(a <= mid < b for a, b in only):
            continue
        why = []
        if w.prob < LOW_PROB:
            why.append(f"low confidence ({w.prob:.2f})")
        snr = word_snr(rms, w) if len(rms) else None
        if snr is not None and snr < NOISY_DB:
            why.append(f"music / noise under it ({snr:.0f} dB above the sound bed)")
        if any(w.start - CUT_TOL_S <= c <= w.end + CUT_TOL_S for c in cuts):
            why.append("an edit point cuts into it")
        if why:
            out[i] = why
    return out


def _text(ws: Sequence[Word]) -> str:
    return " ".join(w.text for w in ws)


def _conf(ws: Sequence[Word]) -> float:
    return float(np.mean([w.prob for w in ws])) if ws else 0.0


# the same words said, written two ways
_SPOKEN = {"gonna": "going to", "wanna": "want to", "gotta": "got to", "kinda": "kind of", "sorta": "sort of",
           "lemme": "let me", "gimme": "give me", "dunno": "don't know", "outta": "out of", "lotta": "lot of",
           "cause": "because", "cuz": "because", "ok": "okay"}


def _tokens(texts: Sequence[str]) -> list[str]:
    """Words for comparing versions: letters, digits and apostrophes, any case; "gonna" = "going to" etc."""
    out: list[str] = []
    for t in texts:
        for x in str(t).split():
            n = norm(x).strip("'")
            out += _SPOKEN.get(n, n).replace("'", "").split() if n else []
    return out


def _agrees(ws: Sequence[Word], caption: str | None, left: Word | None = None, right: Word | None = None) -> bool:
    """The caption says these words, in this order, between the words kept on either side of them (so one common
    word cannot agree on its own, and a version missing a word does not)."""
    if not ws or not caption:
        return False
    have = _tokens([caption])
    want = _tokens([w.text for w in ([left] if left else []) + list(ws) + ([right] if right else [])])
    return any(have[k:k + len(want)] == want for k in range(len(have) - len(want) + 1))


def recheck(words: Sequence[Word], y16: np.ndarray | None, pieces: Sequence[Piece],
            source: Callable[[float, float], tuple[np.ndarray, float]] | None,
            transcribe: Callable[[np.ndarray], list[Word]], *, captions: Callable[[float, float], str | None] | None
            = None, only: Sequence[tuple[float, float]] | None = None, source_name: str = "RAW",
            edit_model: str = "small.en", model: str = RECHECK_MODEL) -> tuple[list[Word], dict]:
    """(the words with the unclear spots rechecked, the report): see the module docstring. ``source(s0, s1)`` ->
    (16 kHz mono samples of the source from about s0 to s1 seconds, the second they start at); ``transcribe(y)`` ->
    words timed from the start of y; ``captions(t0, t1)`` -> the competitor caption read clearly over those edit
    seconds, or None."""
    end = len(y16) / SR if y16 is not None else max((w.end for w in words), default=0.0)
    unsure = unsure_words(words, y16, cut_times(pieces, end), only)
    rep: dict = {"model": model, "source": source_name, "unsure": len(unsure), "rechecked": 0, "changed": 0,
                 "changes": [], "unclear": []}
    by_piece: dict[int, list[int]] = {}
    lost: list[int] = []
    for i in sorted(unsure):
        w = words[i]
        p = piece_at(pieces, 0.5 * (w.start + w.end)) if source is not None else None
        if p is None:
            lost.append(i)
        else:
            by_piece.setdefault(pieces.index(p), []).append(i)
    probs = {i: w.prob for i, w in enumerate(words)}
    swaps: list[tuple[list[int], list[Word]]] = []                 # (edit word indices replaced, the words used)
    done: set[int] = set()
    jobs: list[tuple[Piece, float, float, list[int]]] = []         # (piece, source window, its unsure words)
    for pi, idx in sorted(by_piece.items()):
        p = pieces[pi]
        for i in idx:
            a, b = max(0.0, p.src(words[i].start) - CONTEXT_S), p.src(words[i].end) + CONTEXT_S
            if jobs and jobs[-1][0] is p and a <= jobs[-1][2]:
                jobs[-1] = (p, jobs[-1][1], max(jobs[-1][2], b), jobs[-1][3] + [i])
            else:
                jobs.append((p, a, b, [i]))
    spans: list[list[float]] = []          # the source transcribed: overlapping windows (any pieces) heard once
    for _, a, b, _ in sorted(jobs, key=lambda j: j[1]):
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    said: list[list[Word] | None] = []     # each span's words, in source seconds
    for a, b in spans:
        y, at = source(a, b)
        said.append(None if y is None or len(y) < SR // 10 else
                    [replace(w, start=w.start + at, end=w.end + at) for w in transcribe(y)])
    for p, s0, s1, idx in jobs:
        k = next(k for k, (a, b) in enumerate(spans) if a <= s0 and s1 <= b)
        if said[k] is None:
            lost += idx
            continue
        lo_s, hi_s = p.src_range
        heard = []
        for b in said[k]:
            bs, be = b.start, b.end
            ov = min(be, hi_s) - max(bs, lo_s)
            if ov <= 0 or not (ov >= MIN_OVERLAP * max(be - bs, 1e-3) or lo_s <= 0.5 * (bs + be) < hi_s):
                continue
            heard.append(replace(b, start=p.edit(max(bs, lo_s)), end=p.edit(min(be, hi_s))))
        e0, e1 = max(p.t0, p.edit(s0)), min(p.t1, p.edit(s1))
        A = [i for i, w in enumerate(words) if e0 <= 0.5 * (w.start + w.end) < e1
             and piece_at(pieces, 0.5 * (w.start + w.end)) is p]
        B = [b for b in heard if e0 <= 0.5 * (b.start + b.end) < e1]
        done.update(i for i in A if i in unsure)
        _decide(words, A, B, unsure, probs, swaps, rep, captions, source_name, edit_model, model)
    rep["rechecked"] = len(done)
    for i in sorted(set(lost) - done):
        w = words[i]
        if w.prob < LOW_PROB:
            rep["unclear"].append({"time": round(w.start, 3), "end": round(w.end, 3), "text": w.text,
                                   "why": "; ".join(unsure[i]) +
                                   f" -- not rechecked (no {source_name} audio there)",
                                   "alternatives": [{"source": f"edit ({edit_model})", "text": w.text,
                                                     "conf": round(w.prob, 2)}]})
    gone = {i for a_idx, _ in swaps for i in a_idx}
    out = [replace(w, prob=probs[i]) for i, w in enumerate(words) if i not in gone]
    out += [b for _, bs in swaps for b in bs]
    out.sort(key=lambda w: (w.start, w.end))
    rep["unclear"].sort(key=lambda r: r["time"])
    return out, rep


def _blocks(ops: Sequence[tuple]) -> list[tuple]:
    """The alignment's differences as whole places: touching differences, and a single matching word between two of
    them ("Peter Parker" / "Parker Peter"), are one block -- decided as a whole, never word by word."""
    out: list[list] = []
    for op in ops:
        if out and op[0] != "equal" and out[-1][0] != "equal":
            out[-1][2], out[-1][4] = op[2], op[4]
        else:
            out.append(list(op))
    k = 1
    while k < len(out) - 1:
        if out[k][0] == "equal" and out[k][2] - out[k][1] == 1 and "equal" not in (out[k - 1][0], out[k + 1][0]):
            out[k - 1:k + 2] = [["replace", out[k - 1][1], out[k + 1][2], out[k - 1][3], out[k + 1][4]]]
        else:
            k += 1
    for b in out:
        if b[0] != "equal":
            b[0] = "replace" if b[2] > b[1] and b[4] > b[3] else "delete" if b[2] > b[1] else "insert"
    return [tuple(b) for b in out]


def _decide(words: Sequence[Word], A: list[int], B: list[Word], unsure: dict[int, list[str]], probs: dict[int, float],
            swaps: list, rep: dict, captions: Any, source_name: str, edit_model: str, model: str) -> None:
    """One recheck window: align the edit's words A (indices) with the source's words B and settle every place they
    differ around an unsure word (see the module docstring)."""
    sm = difflib.SequenceMatcher(None, [norm(words[i].text) for i in A], [norm(b.text) for b in B], autojunk=False)
    for tag, i1, i2, j1, j2 in _blocks(sm.get_opcodes()):
        a_idx, b_ws = A[i1:i2], B[j1:j2]
        if tag == "equal":
            for ai, b in zip(a_idx, b_ws):
                if ai in unsure:                          # two transcriptions agree: confirmed
                    probs[ai] = max(probs[ai], b.prob)
            continue
        near = a_idx if a_idx else [A[k] for k in (i1 - 1, i1) if 0 <= k < len(A)]
        if not any(i in unsure for i in near):
            continue
        a_ws = [words[i] for i in a_idx]
        conf_a, conf_b = _conf(a_ws), _conf(b_ws)
        if a_ws and b_ws and _tokens([w.text for w in a_ws]) == _tokens([b.text for b in b_ws]):
            for ai in a_idx:                              # the same words written two ways ("gonna" / "going to")
                probs[ai] = max(probs[ai], conf_b)
            continue
        left = words[A[i1 - 1]] if i1 > 0 else None
        right = words[A[i2]] if i2 < len(A) else None
        t0 = min([w.start for w in a_ws] + [b.start for b in b_ws] + ([left.start] if left else []))
        t1 = max([w.end for w in a_ws] + [b.end for b in b_ws] + ([right.end] if right else []))
        cap = captions(t0, t1) if captions is not None else None
        agree_a, agree_b = _agrees(a_ws, cap, left, right), _agrees(b_ws, cap, left, right)
        t0 = min([w.start for w in a_ws] + [b.start for b in b_ws])
        t1 = max([w.end for w in a_ws] + [b.end for b in b_ws])
        if not b_ws:                                      # only the edit's transcript heard it: never dropped
            use_b, why = False, "kept: the source did not hear it"
        elif not a_ws:                                    # only the source heard it
            use_b = agree_b or conf_b >= CLEAR_PROB
            why = "added: the competitor's caption has it" if agree_b else "added: heard clearly in the source"
        elif agree_a != agree_b:
            use_b, why = agree_b, "the competitor's caption agrees"
        else:
            use_b, why = conf_b >= conf_a + MARGIN, "more confident"
        kept = b_ws if use_b else a_ws
        backed = agree_b if use_b else agree_a
        if use_b:
            lo = left.end if left else -math.inf                          # between the words kept around them
            hi = right.start if right else math.inf

            def fit(t: float) -> float:
                return min(max(t, lo), hi)
            swaps.append((list(a_idx), [replace(b, start=fit(b.start), end=max(fit(b.end), fit(b.start)))
                                        for b in b_ws]))
            if norm(_text(a_ws)) != norm(_text(b_ws)):
                rep["changed"] += max(len(a_ws), len(b_ws))
                rep["changes"].append({"time": round(t0, 3), "end": round(t1, 3), "from": _text(a_ws),
                                       "to": _text(b_ws), "why": why,
                                       "conf": [round(conf_a, 2), round(conf_b, 2)]})
        still = (kept and min(w.prob for w in kept) < LOW_PROB and not backed) or (not use_b and not a_ws)
        if still:
            alts = [{"source": f"edit ({edit_model})", "text": _text(a_ws), "conf": round(conf_a, 2)},
                    {"source": f"{source_name} ({model})", "text": _text(b_ws), "conf": round(conf_b, 2)}]
            if cap:
                alts.append({"source": "competitor caption", "text": cap})
            reasons = sorted({r for i in near if i in unsure for r in unsure[i]})
            rep["unclear"].append({"time": round(t0, 3), "end": round(t1, 3), "text": _text(kept), "why": "; ".join(reasons),
                                   "alternatives": alts})


def audio_source(y: np.ndarray, sr: int) -> Callable[[float, float], tuple[np.ndarray | None, float]]:
    """``source`` for recheck over audio y at sr (any channel layout): 16 kHz mono slices."""
    from .transcribe import resample

    def get(s0: float, s1: float) -> tuple[np.ndarray | None, float]:
        a, b = max(0, int(math.floor(s0 * sr))), min(len(y), int(math.ceil(s1 * sr)))
        return (resample(y[a:b], sr), a / sr) if b > a else (None, s0)
    return get


def clear_captions(spans: Sequence[dict], comp_fps: Any) -> Callable[[float, float], str | None]:
    """``captions`` for recheck from caption_ocr.read_caption_spans: the captions read clearly (OCR score >= 0.8,
    frames agreeing >= 0.6) on screen over edit seconds [t0, t1], joined; None when there is none."""
    f = float(comp_fps)
    clear = [(int(d["comp_in"]) / f, int(d["comp_out"]) / f, str(d["ocr"])) for d in spans
             if d.get("ocr") and float(d.get("score") or 0) >= 0.8 and float(d.get("agreement") or 0) >= 0.6]

    def get(t0: float, t1: float) -> str | None:
        return " ".join(t for a, b, t in clear if a < t1 and b > t0) or None
    return get
