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
import re
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
            edit_model: str = "small.en", model: str = RECHECK_MODEL,
            extra: dict[int, list[str]] | None = None) -> tuple[list[Word], dict]:
    """(the words with the unclear spots rechecked, the report): see the module docstring. ``source(s0, s1)`` ->
    (16 kHz mono samples of the source from about s0 to s1 seconds, the second they start at); ``transcribe(y)`` ->
    words timed from the start of y; ``captions(t0, t1)`` -> the competitor caption read clearly over those edit
    seconds, or None. ``extra``: more unsure words {index: why} -- e.g. where a second speech model heard something
    else (captions.run_captions)."""
    end = len(y16) / SR if y16 is not None else max((w.end for w in words), default=0.0)
    unsure = unsure_words(words, y16, cut_times(pieces, end), only)
    for i, why in (extra or {}).items():
        if 0 <= int(i) < len(words):
            unsure.setdefault(int(i), []).extend(why)
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


REPEAT_GAP_S = 0.3        # a word the source hears this near the same word in the edit's transcript is that word


def _said_beside(b: Word, words: Sequence[Word]) -> bool:
    """The edit's transcript has this word right there (within REPEAT_GAP_S): a recheck window's edge fell between
    the two transcriptions' timings of one word -- not a word to add again."""
    n = norm(b.text)
    return any(norm(w.text) == n and w.start - REPEAT_GAP_S < b.end and b.start < w.end + REPEAT_GAP_S
               for w in words)


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
        if not a_ws:                                      # the edit's own word just outside the window, heard again
            b_ws = [b for b in b_ws if not _said_beside(b, words)]
            if not b_ws or hallucination(b_ws):
                continue
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


HALLUCINATIONS = ("thanks for watching", "thank you for watching", "thanks for joining us", "thank you for joining us",
                  "thanks for listening", "thank you for listening", "please subscribe", "like and subscribe",
                  "subscribe to my channel", "see you next time", "see you in the next video", "subtitles by",
                  "captions by", "transcribed by", "amara org")


def hallucination(ws: Sequence[Word]) -> bool:
    """Words Whisper is known to make up over laughter, music or silence ("Thanks for watching", "Thanks for
    joining us", "Please subscribe" ...): dropped where only one transcription has them."""
    t = " ".join(_tokens([w.text for w in ws]))
    return bool(t) and any(h in t for h in HALLUCINATIONS)


COLLOQUIAL = frozenset(_SPOKEN) - {"cause", "cuz", "ok"}      # gonna, wanna, gotta ...: the reduced form as said
OPINION_SLACK_S = 0.5     # the competitor's caption read this much around a place (my edit's timing is not its own)


def screen_spoken_forms(words: Sequence[Word], captions: Callable[[float, float], str | None] | None
                        ) -> tuple[list[Word], list[dict]]:
    """Where the transcript writes the full form ("want to") and the competitor's caption read clearly there writes
    the reduced one ("WANNA"): the reduced form -- what was said (the speech models tend to write it out). Returns
    (the words, one row per change)."""
    out = list(words)
    rows: list[dict] = []
    if captions is None:
        return out, rows
    full = {v: k for k, v in _SPOKEN.items() if k in COLLOQUIAL}           # "want to" -> "wanna"
    i = 0
    while i + 1 < len(out):
        pair = f"{norm(out[i].text)} {norm(out[i + 1].text)}"
        short = full.get(pair)
        if short:
            cap = captions(out[i].start - OPINION_SLACK_S, out[i + 1].end + OPINION_SLACK_S) or ""
            if short in [norm(x) for x in str(cap).split()]:
                raw = out[i].raw or out[i].text
                new = short.capitalize() if raw[:1].isupper() else short
                tail = re.sub(r"^[\w']+", "", (out[i + 1].raw or out[i + 1].text).strip())
                out[i:i + 2] = [replace(out[i], text=new, end=out[i + 1].end, raw=new + tail)]
                rows.append({"time": round(out[i].start, 3), "from": pair, "to": short, "caption": cap})
        i += 1
    return out, rows


def _retime(new: Sequence[Word], old: Sequence[Word], lo: float, hi: float) -> list[Word]:
    """``new`` words placed where ``old`` ones were (their span shared by characters), or between lo and hi when
    there were none; forced alignment times them properly afterwards."""
    a = old[0].start if old else lo
    b = old[-1].end if old else max(lo, min(hi, new[-1].end if new else lo))
    if b <= a:
        b = a + 0.05 * max(1, len(new))
    total = sum(max(1, len(w.text)) for w in new) or 1
    out, t = [], a
    for w in new:
        d = (b - a) * max(1, len(w.text)) / total
        out.append(replace(w, start=t, end=t + d))
        t += d
    return out


def _agrees_near(ws: Sequence[Word], caption: str | None, left: Word | None, right: Word | None) -> bool:
    """_agrees with the words on both sides, or on one side (the competitor's caption may begin or end right there),
    or -- for two words or more -- on their own."""
    if not ws or not caption:
        return False
    if any(_agrees(ws, caption, x, y) for x, y in ((left, right), (None, right), (left, None))):
        return True
    return len(_tokens([w.text for w in ws])) >= 2 and _agrees(ws, caption)


def resolve_two(a: Sequence[Word], b: Sequence[Word], name_a: str, name_b: str,
                captions: Callable[[float, float], str | None] | None = None
                ) -> tuple[list[Word], dict, dict[int, list[str]]]:
    """Where the two best speech models disagree (``a``: the best, ``b``: the second; their transcripts aligned
    word by word, a difference decided as a whole place): (the words, the report, {word index: why} still unsure).

    * the same words written two ways ("going to" / "gonna"): the reduced form when either model wrote it -- a model
      writes "gonna" only when it heard it (the user captions what is said);
    * otherwise the competitor's caption read clearly there (``captions``) decides when it agrees with exactly one
      version, in context (the words kept on either side);
    * otherwise the best model's version is kept and marked unsure: the recheck from the RAW decides, and what is
      still unclear is listed with both versions."""
    a, b = list(a), list(b)
    sm = difflib.SequenceMatcher(None, [norm(w.text) for w in a], [norm(w.text) for w in b], autojunk=False)
    out: list[Word] = []
    unsure: dict[int, list[str]] = {}
    rep: dict = {"model": name_b, "differences": 0, "spoken_form": [], "caption": [], "open": []}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():           # each difference on its own (its own words around it)
        a_ws, b_ws = a[i1:i2], b[j1:j2]
        if tag == "equal":
            out += a_ws
            continue
        rep["differences"] += 1
        left = a[i1 - 1] if i1 > 0 else None
        right = a[i2] if i2 < len(a) else None
        lo = left.end if left else 0.0
        hi = right.start if right else math.inf
        t0 = min([w.start for w in a_ws + b_ws] or [lo])
        t1 = max([w.end for w in a_ws + b_ws] or [lo])
        row = {"time": round(t0, 3), "end": round(t1, 3), name_a: _text(a_ws), name_b: _text(b_ws)}
        if a_ws and b_ws and _tokens([w.text for w in a_ws]) == _tokens([w.text for w in b_ws]):
            spoken_b = any(norm(w.text) in COLLOQUIAL for w in b_ws)
            spoken_a = any(norm(w.text) in COLLOQUIAL for w in a_ws)
            if spoken_b and not spoken_a:
                out += _retime(b_ws, a_ws, lo, hi)
                rep["spoken_form"].append(row)
            else:
                out += a_ws
            continue
        if a_ws and not b_ws and hallucination(a_ws):
            rep.setdefault("hallucination", []).append(row)   # only one model "heard" a stock phrase: made up
            continue
        cap = captions(t0 - OPINION_SLACK_S, t1 + OPINION_SLACK_S) if captions is not None else None
        agree_a, agree_b = _agrees_near(a_ws, cap, left, right), _agrees_near(b_ws, cap, left, right)
        if agree_b and not agree_a and b_ws:
            out += _retime(b_ws, a_ws, lo, hi)
            rep["caption"].append(dict(row, caption=cap))
            continue
        if agree_a and not agree_b:
            out += a_ws
            continue
        k0 = len(out)
        out += a_ws
        why = f"{name_b} heard '{_text(b_ws) or 'nothing'}'"
        for k in (range(k0, len(out)) if len(out) > k0 else [k0 - 1]):
            if k >= 0:
                unsure.setdefault(k, []).append(why)
        rep["open"].append(row)
    return out, rep, unsure


SCREEN_CONTEXT = 3        # words heard on either side of a place, scored with it (the same in both readings)
SCREEN_PAD_S = 0.3        # the audio scored reaches this far past them
SCREEN_JUMP_S = 1.5       # a longer gap between two of those words: no single phrase to score
SCREEN_MAX_S = 28.0       # the speech models hear at most 30 s at once
SCREEN_QUIET_DB = 20.0    # source the edit leaves out between two words, this much under them at its loudest: a pause


def screen_words(spans: Sequence[dict], fps: Any) -> list[Word]:
    """The words of the captions read clearly (clear_captions' measure; no ``*action*``), each with its share of
    its caption's time on screen (edit seconds) -- to line them up with the words heard."""
    from .caption_rules import is_action_text
    from .captions import clean_text
    f = float(fps)
    out: list[Word] = []
    for d in spans:
        text = str(d.get("ocr") or "")
        if not text or float(d.get("score") or 0) < 0.8 or float(d.get("agreement") or 0) < 0.6 or \
                is_action_text(text):
            continue
        ws = text.split()
        a, b = int(d["comp_in"]) / f, int(d["comp_out"]) / f
        out += [Word(clean_text(w), a + (b - a) * k / len(ws), a + (b - a) * (k + 1) / len(ws), 1.0, w)
                for k, w in enumerate(ws)]
    return sorted(out, key=lambda w: w.start)


def _plain(ws: Sequence[Word]) -> str:
    """Words as a speech model is asked to score them: lower case, no punctuation (both readings alike)."""
    t = " ".join(str(w.raw or w.text) for w in ws).replace("’", "'").lower()
    return " ".join(re.sub(r"[^\w' -]+", " ", t).split())


def _source_window(ctx: Sequence[Word], pieces: Sequence[Piece] | None,
                   source: Callable[[float, float], tuple[np.ndarray | None, float]] | None) -> np.ndarray | None:
    """The source's own audio (the RAW: every word whole, however the edit cuts into it) under these words, when
    the edit plays them in the source's order with nothing left out between them; else None."""
    if not pieces or source is None:
        return None
    src = []
    for w in ctx:
        p = piece_at(pieces, 0.5 * (w.start + w.end))
        if p is None:
            return None
        src.append((p.src(w.start), p.src(w.end)))
    for (a0, a1), (b0, _b1), x, y in zip(src, src[1:], ctx, ctx[1:]):
        if b0 < a0 or ((b0 - a1) - (y.start - x.end) > 0.3 and not _a_pause(source, a1, b0, src)):
            return None                                  # a jump in the source, or speech the edit leaves out
    s0, s1 = max(0.0, src[0][0] - SCREEN_PAD_S), src[-1][1] + SCREEN_PAD_S
    y, at = source(s0, s1)
    if y is None or not len(y):
        return None
    return y[int(max(0.0, s0 - at) * SR):int(max(0.0, s1 - at) * SR)]


def _a_pause(source: Callable[[float, float], tuple[np.ndarray | None, float]], a: float, b: float,
             words: Sequence[tuple[float, float]]) -> bool:
    """The source between a and b is a pause (what silence removal cuts out): its loudest 50 ms at least
    SCREEN_QUIET_DB under the words' (source seconds) average level."""
    gap, _ = source(a, b)
    said = [y for y in (source(x0, x1)[0] for x0, x1 in words) if y is not None and len(y)]
    if gap is None or not len(gap) or not said:
        return False
    level = 10 * math.log10(float(np.mean(np.concatenate(said).astype(np.float64) ** 2)) + 1e-12)
    n = SR // 20
    win = [gap[k:k + n] for k in range(0, max(1, len(gap) - n + 1), n // 2)]
    loud = max(10 * math.log10(float(np.mean(w.astype(np.float64) ** 2)) + 1e-12) for w in win if len(w))
    return loud <= level - SCREEN_QUIET_DB


def screen_readings(words: Sequence[Word], shown: Sequence[Word], y16: np.ndarray | None,
                    judges: Sequence[Callable[[list], list]], sr: int = SR,
                    avoid: Callable[[float, float], bool] | None = None, pieces: Sequence[Piece] | None = None,
                    source: Callable[[float, float], tuple[np.ndarray | None, float]] | None = None
                    ) -> tuple[list[Word], list[dict]]:
    """Where a caption read clearly on screen says other words than the speech models heard ("SO AS A JOKE" for
    "There was a joke", "the X-Force" for "X-Force"): both readings of the phrase -- the same words heard around
    them -- are scored for that audio by each judge (a speech model's likelihood of the text), and the screen's
    reading replaces the heard one only when every judge finds it the more likely (a misread screen, or a word the
    competitor wrote but nobody said, stays out). The audio scored is the source's (``pieces`` / ``source``, as
    recheck) where the edit plays the phrase in order -- the words whole, however the edit cuts into them --, else
    the edit's (y16). ``judges``: f([(audio, [text, ...]), ...]) -> [[score, ...]]; ``avoid(t0, t1)``: edit audio
    not to score (another video's stretch). Returns (the words in their spoken order, one row per place)."""
    out = list(words)
    if not out or not shown or not judges or y16 is None or not len(y16):
        return list(out), []
    letters = "".join(ch for w in shown for ch in str(w.raw or w.text) if ch.isalpha())
    all_caps = len(letters) >= 12 and sum(ch.isupper() for ch in letters) >= 0.9 * len(letters)
    sm = difflib.SequenceMatcher(None, [norm(w.text) for w in out], [norm(w.text) for w in shown], autojunk=False)
    places = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag not in ("replace", "insert"):               # words only heard: the competitor left them out
            continue
        a_ws, b_ws = out[i1:i2], list(shown[j1:j2])
        if not any(norm(w.text) for w in b_ws) or (a_ws and _tokens([w.text for w in a_ws]) ==
                                                  _tokens([w.text for w in b_ws])):
            continue                                        # the same words written two ways
        k0, k1 = max(0, i1 - SCREEN_CONTEXT), min(len(out), i2 + SCREEN_CONTEXT)
        ctx = out[k0:k1]
        if not ctx or any(y.start - x.end > SCREEN_JUMP_S for x, y in zip(ctx, ctx[1:])):
            continue
        t0, t1 = max(0.0, ctx[0].start - SCREEN_PAD_S), min(len(y16) / sr, ctx[-1].end + SCREEN_PAD_S)
        mids = [0.5 * (w.start + w.end) for w in b_ws]
        if t1 - t0 > SCREEN_MAX_S or min(mids) < t0 - 0.5 or max(mids) > t1 + 0.5 or (avoid and avoid(t0, t1)):
            continue                                        # the screen's words are elsewhere
        pre, post = out[k0:i1], out[i2:k1]
        y = _source_window(ctx, pieces, source) if sr == SR else None
        places.append((i1, i2, b_ws, t0, t1, [_plain(pre + list(a_ws) + post), _plain(pre + b_ws + post)],
                       y if y is not None and len(y) else y16[int(t0 * sr):int(t1 * sr)]))
    if not places:
        return list(out), []
    jobs = [(y, texts) for *_rest, texts, y in places]
    verdicts = [judge(jobs) for judge in judges]
    rows: list[dict] = []
    take = []
    for n, (i1, i2, b_ws, t0, _t1, _texts, _y) in enumerate(places):
        scores = [v[n] for v in verdicts]
        ok = all(sc[1] > sc[0] for sc in scores)
        rows.append({"time": round(out[i1].start if i2 > i1 else t0, 3), "heard": _text(out[i1:i2]),
                     "screen": " ".join(str(w.raw or w.text) for w in b_ws),
                     "scores": [[round(x, 2) for x in sc] for sc in scores], "taken": ok})
        if ok:
            take.append((i1, i2, b_ws))
    for i1, i2, b_ws in sorted(take, key=lambda x: -x[0]):
        lo = out[i1 - 1].end if i1 > 0 else 0.0
        hi = out[i2].start if i2 < len(out) else math.inf
        new = [Word(w.text.lower() if all_caps else w.text, w.start, w.end, 0.9,
                    str(w.raw or w.text).lower() if all_caps else str(w.raw or w.text)) for w in b_ws]
        out[i1:i2] = _retime(new, out[i1:i2], lo, hi)
    return out, rows


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
