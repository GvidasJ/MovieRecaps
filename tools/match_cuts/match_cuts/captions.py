"""Captions: ``<run folder>/2_captions.srt`` on the Premiere sequence (60.00 fps), by the rules of caption-generator-prompt.md.

Two modes, chosen per clip (``--captions auto|competitor|voice``, pipeline.stage_captions):

* **competitor** -- the competitor has burned-in captions: an exact copy (caption_ocr.read_caption_spans reads the
  caption band on every frame): each caption's words, capitals and punctuation as written on screen (``*actions*``
  included), from its first frame to its last. None of the style rules below apply. Only a caption the OCR cannot
  read takes the words heard while it is on screen (listed in the report); speech the competitor left uncaptioned
  stays uncaptioned.
* **voice** -- only when the competitor has no captions: captions made from the voice-over (word timestamps of the
  CUT edit's audio, or ``--voiceover FILE``), the grouping walk, the weak-word fix, back-to-back timing and ``*...*``
  placeholders for silences.

This module holds the text rules, grouping, timing, SRT I/O and the report data (no heavy imports); transcribe.py
(faster-whisper) and caption_ocr.py (RapidOCR) hold the optional engines.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

# ---- the caption style (caption-generator-prompt.md) ----------------------------------------------------------
MAX_WORDS = 4              # a caption already holding 4 words starts a new one (never more than 5)
MAX_CHARS = 20             # ... or one that would exceed 20 characters
HARD_CAP = 24              # hard cap (only *...* placeholders / single unbreakable units may reach it)
PAUSE_S = 0.25             # a pause longer than this before the next word starts a new caption
SILENCE_S = 1.0            # more than this with no speech: a *...* placeholder
INTERJECTION_PAUSE_S = 0.15  # an interjection followed by a pause (or punctuation) stands alone
PLACEHOLDER = "*...*"

WEAK = frozenset("a an the to of and is are was were in on at for with my your our that it but so or as i".split())
INTERJECTION_RE = re.compile(r"^(?:o+h+|ye+a+h*|yeah+|he+y+|no+|who+a+|woah+|o+k+a+y+|ok|so+r+r+y+|u+m+|a+m{2,})$")
NUMBER_WORDS = frozenset("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
    sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million
    half""".split())
UNITS = frozenset("""am pm year years yr yrs month months week weeks day days hour hours hr hrs minute minutes min mins
    second seconds sec secs percent % quid pound pounds p pence dollar dollars bucks euro euros k grand million
    millions billion billions thousand hundred mile miles km kilometres kilometers metre metres meter meters m cm mm
    foot feet ft inch inches kg kilo kilos lb lbs mph degrees o'clock times""".split())
AGE_UNITS = frozenset("year years month months week weeks day days".split())
NEGATIONS = frozenset(["cannot"])
# capitalised words that start sentences rather than names (a full name is never one of these)
NAME_STOP = WEAK | frozenset("""oh yeah yea hey no whoa okay ok sorry um amm what why how when where who whose which this
    these those there here then well just we you he she they his her its their them me him us please thanks thank do
    does did can could would should will if because not now look listen wait come go get all one two yes hi hello dear
    good great nice cool wow sure right maybe also even only still plus every everyone everything nobody someone
    something today tonight yesterday tomorrow let's let honestly actually basically anyway apparently guys man mate
    sir madam mister mr mrs ms dr alright not""".split())


@dataclass
class Word:
    """One transcribed word on the edit timeline (seconds). ``text`` is cleaned; ``raw`` is as transcribed."""
    text: str
    start: float
    end: float
    prob: float = 1.0
    raw: str = ""


@dataclass
class Caption:
    """One SRT block on the sequence: frames [start, end) at the sequence fps."""
    text: str
    start: int
    end: int
    mode: str = "voice"            # voice | competitor | placeholder
    words: list[Word] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def is_action(self) -> bool:
        return is_action_text(self.text)


# ---------------------------------------------------------------------------------------------
# Text rules
# ---------------------------------------------------------------------------------------------

_SEP_RE = re.compile(r"(?<!\d)[.,]|[.,](?!\d)")


def clean_text(s: str) -> str:
    """Strip full stops and commas -- except inside numbers (``£4.50``, ``15,000``) -- and surplus spaces.
    Abbreviation dots go too: ``Mr.`` -> ``Mr``, ``C.I.D.`` -> ``CID``, ``6 a.m.`` -> ``6 am``."""
    s = str(s).replace("…", "")
    s = _SEP_RE.sub("", s)
    return " ".join(s.split())


def norm(s: str) -> str:
    """Lower-case word for the rule look-ups (letters, digits, apostrophes, %)."""
    return re.sub(r"[^\w'%]", "", str(s).lower().replace("’", "'"))


def is_action_text(s: str) -> bool:
    t = str(s).strip()
    return len(t) >= 2 and t.startswith("*") and t.endswith("*")


def is_weak(w: str) -> bool:
    return norm(w) in WEAK


def is_interjection(w: str) -> bool:
    return bool(INTERJECTION_RE.match(re.sub(r"[^a-z]", "", str(w).lower())))


def _is_number(n: str) -> bool:
    return bool(re.search(r"\d", n)) or n in NUMBER_WORDS


def _is_name_part(text: str) -> bool:
    t = str(text).strip("'’?!*\"-")
    if len(t) < 2 or not t[0].isupper() or not re.search(r"[A-Za-z]", t):
        return False
    n = norm(t)
    return n not in NAME_STOP and not n.startswith("i'") and not _PRONOUN_CONTRACTION.match(n)


_PRONOUN_CONTRACTION = re.compile(r"^(?:it|that|there|here|he|she|we|they|you|what|who|where|how|when|why|let)'"
                                  r"(?:s|re|ll|ve|d|m)$")


def _ends_sentence(raw: str) -> bool:
    return bool(re.search(r"[.!?…]['\"’]?$", str(raw).strip()))


def _is_negation(n: str) -> bool:
    return n.endswith("n't") or n in NEGATIONS


def compute_bonds(words: Sequence[Word]) -> list[bool]:
    """bonds[i]: words i and i+1 are never split across captions (prompt "Keep together"): a full name (two
    capitalised words that are not sentence starters), a number and its unit (``6 am``, ``50 quid``, ``10 year
    old``) and a negation and its verb (``don't move``, ``didn't get it``). Never across a pause > 0.25 s."""
    n = len(words)
    bonds = [False] * max(0, n - 1)
    for i in range(n - 1):
        a, b = words[i], words[i + 1]
        if b.start - a.end > PAUSE_S:
            continue                                   # the speaker split it: a pause always may
        na, nb = norm(a.text), norm(b.text)
        if na == nb:
            continue                                   # a repetition is never bonded
        if _is_name_part(a.text) and _is_name_part(b.text) and not _ends_sentence(a.raw or a.text):
            bonds[i] = True
        elif _is_number(na) and nb in UNITS:
            bonds[i] = True
        elif na in AGE_UNITS and nb == "old" and i > 0 and _is_number(norm(words[i - 1].text)):
            bonds[i] = True
        elif _is_negation(na) and not _ends_sentence(a.raw or a.text):
            bonds[i] = True
            if i + 2 < n and norm(words[i + 2].text) == "it" and words[i + 2].start - b.end <= PAUSE_S:
                bonds[i + 1] = True                    # "didn't get it"
    return bonds


def standalone_interjections(words: Sequence[Word]) -> list[bool]:
    """An interjection (oh, yeah, hey, no, whoa, okay, sorry, um, amm, ohhh) gets its own caption when it stands
    alone: followed by punctuation or a pause, or the last word ("no idea" / "No hands" keep together)."""
    out = []
    for i, w in enumerate(words):
        if not is_interjection(w.text):
            out.append(False)
        elif i == len(words) - 1 or re.search(r"[,.!?…;:]['\"’]?$", (w.raw or "").strip()):
            out.append(True)
        else:
            out.append(words[i + 1].start - w.end > INTERJECTION_PAUSE_S)
    return out


def _chars(words: Sequence[Word], idx: Sequence[int]) -> int:
    return len(" ".join(words[i].text for i in idx))


# ---------------------------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------------------------

def _repeat(words: Sequence[Word], i: int, j: int) -> bool:
    """A word said again straight away: a deliberate repetition, one caption each (prompt "Repetition")."""
    a = norm(words[i].text)
    return bool(a) and a == norm(words[j].text)


def _units(words: Sequence[Word], bonds: Sequence[bool], glue: set[int]) -> list[list[int]]:
    """Runs of words that stay together (bonds + moved weak words glued to their next word); a run longer than the
    caps is cut greedily (the word / character caps win over a bond)."""
    units: list[list[int]] = []
    cur: list[int] = []
    for i in range(len(words)):
        cur.append(i)
        if i == len(words) - 1 or not (bonds[i] or i in glue):
            units.append(cur)
            cur = []
    out: list[list[int]] = []
    for u in units:
        if len(u) <= MAX_WORDS and _chars(words, u) <= HARD_CAP:
            out.append(u)
            continue
        part: list[int] = []
        for i in u:
            if part and (len(part) >= MAX_WORDS or _chars(words, part + [i]) > MAX_CHARS):
                out.append(part)
                part = []
            part.append(i)
        out.append(part)
    return out


def _walk(words: Sequence[Word], bonds: Sequence[bool], alone: Sequence[bool], forced: set[int],
          glue: set[int]) -> list[list[int]]:
    """The grouping walk: a new caption starts before a unit when the caption already has 4 words or would exceed
    20 characters, after a pause > 0.25 s, at a standalone interjection (before and after it), at a word said
    again straight away (repetition: one caption each), or where the weak-word fix forced it. The speaker-change
    rule needs diarisation, which the transcriber does not provide."""
    groups: list[list[int]] = []
    cur: list[int] = []
    for u in _units(words, bonds, glue):
        if cur:
            prev, first = cur[-1], u[0]
            # a caption of nothing but weak words ("for a") may take one word more (never more than 5) rather
            # than end on a weak word: "for a 15 year old"
            max_words = MAX_WORDS + 1 if all(is_weak(words[i].text) for i in cur) else MAX_WORDS
            brk = (first in forced or words[first].start - words[prev].end > PAUSE_S or alone[prev] or alone[first]
                   or _repeat(words, prev, first) or len(cur) + len(u) > max_words
                   or _chars(words, cur + u) > MAX_CHARS)
            if brk:
                groups.append(cur)
                cur = []
        cur = cur + u
    if cur:
        groups.append(cur)
    return groups


def group_words(words: Sequence[Word], notes: list[dict] | None = None) -> list[list[int]]:
    """Word-index groups, one per caption: the walk, then the weak-word fix -- a caption of more than one word that
    ends on a weak word gives that word to the front of the next caption (which is re-split if it now breaks the
    caps). Each caption gives away at most one word (the prompt's own example keeps "there is" after giving away
    "a"). Kept, and listed in ``notes``: a weak word bonded to the previous one ("didn't get it"), before a silence
    placeholder, before a standalone interjection, or at the very end."""
    if not words:
        return []
    bonds = compute_bonds(words)
    alone = standalone_interjections(words)
    forced: set[int] = set()
    glue: set[int] = set()
    groups = _walk(words, bonds, alone, forced, glue)
    gave: set[int] = set()
    gi = 0
    while gi < len(groups):
        g = groups[gi]
        last = g[-1]
        if len(g) > 1 and is_weak(words[last].text):
            reason = None if g[0] not in gave else "it already gave one weak word to the next caption"
            if reason is not None:
                pass
            elif last + 1 >= len(words):
                reason = "last word of the captions"
            elif bonds[last - 1]:
                reason = "kept together with the previous word"
            elif last - 1 in glue:
                reason = "it follows a weak word moved here; moving it too would strand that one"
            elif _repeat(words, last, last + 1):
                reason = "the next word repeats it"
            elif words[last + 1].start - words[last].end > SILENCE_S:
                reason = "a silence follows"
            elif alone[last + 1]:
                reason = "the next caption is an interjection"
            if reason is None:
                forced.add(last)
                glue.add(last)
                gave.add(g[0])
                groups = _walk(words, bonds, alone, forced, glue)
                continue                                 # look at the shortened caption again (for the notes)
            if notes is not None:
                notes.append({"word": last, "reason": reason})
        gi += 1
    return groups


# ---------------------------------------------------------------------------------------------
# Timing on the sequence
# ---------------------------------------------------------------------------------------------

def to_frame(t: float, fps: Fraction) -> int:
    return int(np.floor(float(t) * float(fps) + 0.5))


def _monotonic(caps: list[Caption], lo: int, hi: int) -> list[Caption]:
    """Starts strictly increasing inside [lo, hi), every caption at least one frame long; back to back except the
    last one, which keeps its own end (clamped to hi)."""
    out: list[Caption] = []
    for c in caps:
        s = max(c.start, lo, out[-1].start + 1 if out else lo)
        if s >= hi:
            break
        c.start = s
        out.append(c)
    for i, c in enumerate(out):
        c.end = out[i + 1].start if i + 1 < len(out) else min(max(c.end, c.start + 1), hi)
    return out


def voice_captions(words: Sequence[Word], fps: Fraction, n_frames: int, *, lo: int = 0, hi: int | None = None,
                   placeholders: bool = True, mode: str = "voice", notes: list[dict] | None = None) -> list[Caption]:
    """Captions of a word list on the sequence (frames at ``fps``; ``n_frames`` = timeline length). Back to back:
    each caption ends where the next starts; the last one at its own last word's end. With ``placeholders``,
    every stretch of more than ~1 s without speech (also before the first / after the last word) becomes a
    ``*...*`` caption. ``lo`` / ``hi`` clamp the captions (a gap between two competitor captions)."""
    hi = int(n_frames if hi is None else hi)
    words = [w for w in words if w.text]
    if not words:
        if placeholders and hi - lo > to_frame(SILENCE_S, fps):
            return [Caption(PLACEHOLDER, lo, hi, "placeholder")]
        return []
    weak_notes: list[dict] = []
    groups = group_words(words, weak_notes)
    if notes is not None:
        for wn in weak_notes:
            notes.append({"text": words[wn["word"]].text, "time": words[wn["word"]].start, "reason": wn["reason"]})
    timeline_end = hi / float(fps)
    caps: list[Caption] = []
    if placeholders and words[0].start - lo / float(fps) > SILENCE_S:
        caps.append(Caption(PLACEHOLDER, lo, to_frame(words[0].start, fps), "placeholder"))
    for gi, g in enumerate(groups):
        ws = [words[i] for i in g]
        caps.append(Caption(" ".join(w.text for w in ws), to_frame(ws[0].start, fps), to_frame(ws[-1].end, fps),
                            mode, ws))
        nxt = words[groups[gi + 1][0]].start if gi + 1 < len(groups) else timeline_end
        if placeholders and nxt - ws[-1].end > SILENCE_S:
            caps.append(Caption(PLACEHOLDER, to_frame(ws[-1].end, fps), to_frame(nxt, fps), "placeholder"))
    for i in range(len(caps) - 1):                     # back to back
        caps[i].end = caps[i + 1].start
    return _monotonic(caps, lo, hi)


# ---------------------------------------------------------------------------------------------
# Competitor mode: an exact copy of the competitor's captions
# ---------------------------------------------------------------------------------------------

def competitor_copy(spans: Sequence[dict], words: Sequence[Word], comp_fps: Fraction, to_seq, seq_fps: Fraction
                    ) -> tuple[list[Caption], dict]:
    """The competitor's captions exactly as on screen (``spans`` from caption_ocr.read_caption_spans: first and last
    frame, the text as written); no style rule touches them. A caption the OCR could not read takes the words heard
    while it is on screen, written the competitor's way (caption_ocr.apply_conventions; punctuation the competitor
    never uses dropped), listed in ``notes["from_transcript"]``; one with no words heard either is left out
    (``notes["unreadable"]``). Returns (captions, notes)."""
    from .caption_ocr import apply_conventions, screen_conventions
    spans = sorted(spans, key=lambda d: int(d["comp_in"]))
    read = [str(d.get("ocr") or "") for d in spans if d.get("ocr")]
    conv = screen_conventions(read)
    unused = str.maketrans("", "", "".join(ch for ch in ",.;:!?" if not any(ch in t for t in read)))
    notes: dict = {"from_transcript": [], "unreadable": []}
    caps: list[Caption] = []
    for d in spans:
        a, b = to_seq(int(d["comp_in"])), to_seq(int(d["comp_out"]))
        info = {k: d.get(k) for k in ("score", "agreement", "reads", "variants", "comp_in", "comp_out")}
        tc = (ms_tc(frame_ms(a, seq_fps)), ms_tc(frame_ms(b, seq_fps)))
        text = str(d.get("ocr") or "").strip()
        if text:
            caps.append(Caption(text, a, b, "competitor", info=dict(info, source="screen")))
            continue
        t0, t1 = float(Fraction(int(d["comp_in"])) / comp_fps), float(Fraction(int(d["comp_out"])) / comp_fps)
        heard = [w for w in words if t0 <= 0.5 * (w.start + w.end) < t1]
        text = " ".join(apply_conventions(" ".join(w.raw or w.text for w in heard).translate(unused), conv).split())
        if text:
            caps.append(Caption(text, a, b, "competitor", heard, dict(info, source="transcript")))
            notes["from_transcript"].append({"start_tc": tc[0], "end_tc": tc[1], "text": text})
        else:
            notes["unreadable"].append({"start_tc": tc[0], "end_tc": tc[1]})
    return caps, notes


# ---------------------------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------------------------

def frame_ms(frame: int, fps: Fraction) -> int:
    """Milliseconds of a sequence frame, rounded to the nearest ms (exact on the 1/60 s grid)."""
    f = Fraction(fps)
    return int(np.floor(float(Fraction(int(frame) * 1000) / f) + 0.5))


def ms_tc(ms: int) -> str:
    ms = max(0, int(ms))
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, r = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{r:03d}"


def srt_text(caps: Sequence[Caption], fps: Fraction) -> str:
    """Standard SRT: sequential numbers, ``HH:MM:SS,mmm --> HH:MM:SS,mmm``, the text, a blank line between
    blocks and no extra trailing lines (LF line ends, like the reference files)."""
    blocks = []
    for i, c in enumerate(caps, start=1):
        text = "\n".join(line.strip() for line in str(c.text).splitlines() if line.strip())
        blocks.append(f"{i}\n{ms_tc(frame_ms(c.start, fps))} --> {ms_tc(frame_ms(c.end, fps))}\n{text}\n")
    return "\n".join(blocks)


def write_srt(caps: Sequence[Caption], path: str | Path, fps: Fraction) -> Path:
    from .common import replace_file
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(srt_text(caps, fps).encode("utf-8"))
    replace_file(tmp, p)
    return p


_TC_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def _tc_ms(s: str) -> int:
    m = _TC_RE.search(s)
    if not m:
        raise ValueError(f"not an SRT time: {s!r}")
    h, mi, se, ms = m.groups()
    return ((int(h) * 60 + int(mi)) * 60 + int(se)) * 1000 + int(ms.ljust(3, "0"))


def parse_srt(text: str) -> list[dict]:
    """[{index, start_ms, end_ms, text}] of an SRT (CRLF / BOM tolerant; multi-line text joined with \\n)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.split("\n")]
        if len(lines) < 2:
            continue
        i0 = 0 if "-->" in lines[0] else 1
        if "-->" not in lines[i0]:
            continue
        a, b = lines[i0].split("-->")
        idx = int(lines[0]) if i0 == 1 and lines[0].strip().isdigit() else len(out) + 1
        out.append({"index": idx, "start_ms": _tc_ms(a), "end_ms": _tc_ms(b),
                    "text": "\n".join(ln.strip() for ln in lines[i0 + 1:] if ln.strip())})
    return out


def read_srt(path: str | Path) -> list[dict]:
    return parse_srt(Path(path).read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------------------------
# Style statistics and report data
# ---------------------------------------------------------------------------------------------

def style_stats(items: Iterable[tuple[str, float]], last_duration: bool = False) -> dict:
    """The prompt's measured style of (text, seconds on screen) pairs: words per caption, characters (median /
    90th percentile / max), median seconds, characters per second, lower-case starts, full stops / commas and
    weak endings. Placeholders and action captions count as captions but not as speech."""
    items = list(items)
    if not items:
        return {"captions": 0}
    durs = [d for _, d in (items if last_duration else items[:-1])] or [items[-1][1]]
    speech = [t for t, _ in items if not is_action_text(t)]
    nw = [len(t.split()) for t in speech]
    chars = [len(t.replace("\n", " ")) for t, _ in items]
    cps = [len(t) / d for t, d in items[:-1] if d > 0 and not is_action_text(t)]
    hist = {str(k): sum(1 for n in nw if n == k) for k in (1, 2, 3, 4)}
    hist["5+"] = sum(1 for n in nw if n >= 5)
    starts = [t for t in speech if re.search(r"[A-Za-z]", t)]
    lower = sum(1 for t in starts if re.search(r"[A-Za-z]", t).group(0).islower())
    weak_end = [t for t in speech if len(t.split()) > 1 and is_weak(t.split()[-1])]
    return {
        "captions": len(items), "speech": len(speech), "actions": len(items) - len(speech),
        "words": hist, "words_pct": {k: round(100.0 * v / max(1, len(nw)), 1) for k, v in hist.items()},
        "chars_median": float(statistics.median(chars)), "chars_p90": float(np.percentile(chars, 90)),
        "chars_max": max(chars), "duration_median_s": round(float(statistics.median(durs)), 3),
        "cps_median": round(float(statistics.median(cps)), 1) if cps else None,
        "lower_start_pct": round(100.0 * lower / max(1, len(starts)), 1),
        "stops_commas": sum(len(re.findall(r"[.,]", re.sub(r"(?<=\d)[.,](?=\d)", "", t.replace(PLACEHOLDER, ""))))
                            for t, _ in items),
        "weak_endings": len(weak_end),
    }


def caption_stats(caps: Sequence[Caption], fps: Fraction) -> dict:
    st = style_stats([(c.text, (c.end - c.start) / float(fps)) for c in caps])
    gaps = sum(1 for a, b in zip(caps, caps[1:]) if b.start != a.end)
    st["back_to_back_pct"] = round(100.0 * (1 - gaps / max(1, len(caps) - 1)), 1) if len(caps) > 1 else 100.0
    return st


def transcript_flags(words: Sequence[Word], audio: np.ndarray | None = None, sr: int = 16000,
                     min_prob: float = 0.5) -> list[dict]:
    """Things to check by ear (flagged, never corrected): low-confidence words (possible mis-transcriptions),
    doubled words (repetition is kept -- it is often the joke) and gaps where the audio carries voice-level sound
    but no word was transcribed (possible missing words)."""
    flags: list[dict] = []
    for i, w in enumerate(words):
        if w.prob < min_prob:
            flags.append({"time": w.start, "kind": "possible mis-transcription",
                          "detail": f"'{w.text}' heard with low confidence ({w.prob:.2f})"})
        if i and norm(w.text) and norm(w.text) == norm(words[i - 1].text):
            flags.append({"time": words[i - 1].start, "kind": "doubled word",
                          "detail": f"'{words[i - 1].text} {w.text}' (kept as said)"})
    if audio is not None and len(audio) and len(words) > 1:
        y = np.asarray(audio, np.float32)
        if y.ndim > 1:
            y = y.mean(axis=1)
        hop = max(1, int(sr * 0.02))
        n = len(y) // hop
        if n:
            rms = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
            in_words = np.zeros(n, bool)
            for w in words:
                in_words[int(w.start / 0.02):int(np.ceil(w.end / 0.02)) + 1] = True
            speech_level = float(np.median(rms[in_words[:n]])) if in_words[:n].any() else 0.0
            for a, b in zip(words, words[1:]):
                if b.start - a.end < 0.4 or speech_level <= 0:
                    continue
                i0, i1 = int(np.ceil(a.end / 0.02)) + 2, int(b.start / 0.02) - 2
                if i1 - i0 < 10:
                    continue
                loud = rms[i0:i1] >= 0.5 * speech_level
                if loud.sum() * 0.02 >= 0.3:
                    flags.append({"time": a.end, "kind": "possible missing word",
                                  "detail": f"{(i1 - i0) * 0.02:.1f} s of voice-level sound between '{a.text}' "
                                            f"and '{b.text}' with no word transcribed"})
    flags.sort(key=lambda f: f["time"])
    return flags


def mode_parts(caps: Sequence[Caption]) -> list[dict]:
    """Consecutive captions of the same mode as parts: [{mode, start, end, count}] (placeholders join voice)."""
    parts: list[dict] = []
    for c in caps:
        m = "voice" if c.mode == "placeholder" else c.mode
        if parts and parts[-1]["mode"] == m:
            parts[-1]["end"] = c.end
            parts[-1]["count"] += 1
        else:
            parts.append({"mode": m, "start": c.start, "end": c.end, "count": 1})
    return parts


# ---------------------------------------------------------------------------------------------
# The pipeline stage (pipeline.stage_captions)
# ---------------------------------------------------------------------------------------------

def seq_frame_of(comp_fps: Fraction, seq_fps: Fraction):
    """Competitor frame k -> sequence frame (k x 2 for 30 -> 60 fps; time-rounded for other rates)."""
    r = Fraction(seq_fps) / Fraction(comp_fps)
    if r.denominator == 1:
        return lambda k: int(k) * int(r)
    return lambda k: to_frame(float(Fraction(int(k)) / Fraction(comp_fps)), seq_fps)


def _caption_dict(c: Caption, fps: Fraction) -> dict:
    d = {"text": c.text, "start": c.start, "end": c.end, "mode": c.mode,
         "start_tc": ms_tc(frame_ms(c.start, fps)), "end_tc": ms_tc(frame_ms(c.end, fps))}
    if c.info:
        d.update({k: v for k, v in c.info.items() if k in ("agreement", "score", "reads", "variants", "comp_in",
                                                         "comp_out")})
    return d


def run_captions(ctx) -> dict:
    """Write ``<run folder>/2_captions.srt`` for a pipeline.Context (after the exports); returns the report data."""
    from .common import dump_json, log
    from .export_xml_edl import premiere_settings
    cfg = ctx.cfg
    requested = str(getattr(cfg, "captions", "auto") or "auto")
    voiceover = str(getattr(cfg, "voiceover", "") or "")
    model = str(getattr(cfg, "caption_model", "small.en") or "small.en")
    language = str(getattr(cfg, "caption_language", "en") or "") or None
    fps = Fraction(premiere_settings(cfg)["fps"])
    comp_fps = Fraction(ctx.comp_fps)
    to_seq = seq_frame_of(comp_fps, fps)
    n_seq = to_seq(ctx.n_comp)
    res: dict = {"requested": requested, "fps": str(fps), "frames": n_seq, "notes": [], "warnings": [],
                 "weak_kept": [], "flags": [], "disagreements": [], "placeholders": [], "over_cap": []}

    def warn(msg: str) -> None:
        res["warnings"].append(msg)
        ctx.warn(f"captions: {msg}")

    from .run_folders import CAPTIONS_SRT
    stale = cfg.deliver / CAPTIONS_SRT            # this run's captions only (an older file never survives)
    if stale.exists():
        stale.unlink()

    # ---- the competitor's burned-in captions: an exact copy, read from the picture ----
    want_ocr = requested == "competitor" or (requested == "auto" and not voiceover)
    layout = getattr(ctx.cutlist, "layout", None) or {}
    n_events = sum(1 for c in (layout.get("captions") or []) if str(c.get("type", "captions")) == "captions")
    res["caption_events"] = n_events
    spans: list[dict] = []
    if want_ocr:
        from . import caption_ocr
        info = ctx.comp_info
        wh = (int(info.display_width or info.width), int(info.display_height or info.height))
        err = caption_ocr.available()
        if caption_ocr.caption_band(layout, wh) is None:
            pass                                   # no caption zone: the competitor has no burned-in captions
        elif err:
            warn(f"the competitor has burned-in captions but they cannot be read: {err}")
        else:
            got = _read_spans(ctx, layout, comp_fps)
            spans = list(got.get("spans") or [])
            res["ocr"] = {"frames_read": got.get("frames_read"), "band": got.get("band"), "fill": got.get("fill"),
                          "events": len(spans), "runs": got.get("runs"), "conventions": got.get("conventions"),
                          "engine": caption_ocr.engine_name(), "notes": {}}
            if n_events and not spans:
                warn(f"{n_events} caption events detected but no caption could be read")
    mode = "competitor" if spans else "voice"
    if requested == "competitor" and not spans:
        warn("--captions competitor: no competitor captions to copy -- made from the voice-over instead")
    res["mode"] = mode
    res["reason"] = ("--captions " + requested if requested != "auto" else
                     "auto: --voiceover given" if voiceover else
                     f"auto: the competitor has burned-in captions ({len(spans)} on screen)" if spans else
                     "auto: no burned-in captions found on the competitor")

    # ---- the words (the cut edit's audio, or the voice-over): voice mode, and captions that cannot be read ----
    from . import transcribe
    words: list[Word] = []
    y16 = None
    err = None
    cl = None
    if mode == "competitor" and all(d.get("ocr") for d in spans):
        res["source"] = "not needed (every caption was read from the screen)"
    elif voiceover:
        from .media import extract_audio
        y16 = extract_audio(voiceover, sr=transcribe.SR, mono=True)
        res["source"] = f"voice-over {Path(voiceover).name}"
        if len(y16) > n_seq / float(fps) * transcribe.SR + transcribe.SR // 2:
            warn(f"the voice-over ({len(y16) / transcribe.SR:.1f} s) is longer than the sequence "
                 f"({n_seq / float(fps):.1f} s): captions after the end are left out")
        if not len(y16):
            warn(f"the voice-over {Path(voiceover).name} has no audio")
    elif ctx.raw_audio is not None and len(ctx.raw_audio):
        from .render_preview import build_audio
        cl = ((getattr(ctx, "broll", None) or {}).get("cutlist") if isinstance(getattr(ctx, "broll", None), dict)
              else None) or ctx.cutlist        # --no-broll: the audio of the edit you import
        y16 = transcribe.resample(build_audio(cl, ctx.raw_audio, int(ctx.audio_sr)), int(ctx.audio_sr))
        res["source"] = "the cut edit (RAW audio on the edit's cuts)"
    else:
        res["source"] = "none (the RAW has no audio)"
    if y16 is not None and len(y16):
        err = transcribe.available()
        if err:
            warn(f"no transcription: {err}")
        else:
            try:
                words = transcribe.transcribe_words(y16, transcribe.SR, model, language, ctx.cache)
            except Exception as e:  # noqa: BLE001 - e.g. the model download failed: captions without a transcript
                err = f"{type(e).__name__}: {e}"
                warn(f"transcription failed: {err}")
    res["transcriber"] = {"engine": "faster-whisper", "model": model, "words": len(words), "error": err}

    # ---- unclear words double-checked against the source (caption_recheck.py) ----
    rmodel = str(getattr(cfg, "caption_recheck_model", "") or "")
    unread = ([(int(d["comp_in"]) / float(comp_fps), int(d["comp_out"]) / float(comp_fps)) for d in spans
               if not d.get("ocr")] if mode == "competitor" else None)
    if words and rmodel.lower() not in ("", "none") and (mode == "voice" or unread):
        from . import caption_recheck as R
        if voiceover:
            pieces, source, sname = ([R.Piece(0.0, len(y16) / transcribe.SR, 0.0, 1.0)],
                                     R.audio_source(y16, transcribe.SR), "voice-over")
        else:
            pieces, source, sname = R.pieces_from_cutlist(cl), R.audio_source(ctx.raw_audio, int(ctx.audio_sr)), "RAW"
        opinion = (R.clear_captions(spans, comp_fps) if spans else
                   _lazy_opinion(ctx, layout, comp_fps) if requested == "voice" and not voiceover else None)
        try:
            words, res["recheck"] = R.recheck(
                words, y16, pieces, source,
                lambda y: transcribe.transcribe_words(y, transcribe.SR, rmodel, language, ctx.cache),
                captions=opinion, only=unread, source_name=sname, edit_model=model, model=rmodel)
            log.info("captions: %d unclear words rechecked against the %s (%s), %d changed, %d still unclear",
                     res["recheck"]["rechecked"], sname, rmodel, res["recheck"]["changed"],
                     len(res["recheck"]["unclear"]))
        except Exception as e:  # noqa: BLE001 - e.g. the bigger model could not be downloaded: the first transcript
            res["recheck"] = {"error": f"{type(e).__name__}: {e}", "model": rmodel, "source": sname}
            warn(f"unclear words not rechecked with {rmodel}: {type(e).__name__}: {e}")

    # ---- captions ----
    weak: list[dict] = []
    if mode == "competitor":
        caps, cnotes = competitor_copy(spans, words, comp_fps, to_seq, fps)
        res["competitor_notes"] = cnotes
        res["short"] = [_caption_dict(c, fps) for c in caps if (c.end - c.start) / float(fps) < 0.1]
    elif words:
        caps = voice_captions(words, fps, n_seq, notes=weak)
        rechecked = "rechecked" in (res.get("recheck") or {})        # low-confidence words: listed by the recheck
        res["flags"] = transcript_flags(words, y16, transcribe.SR, min_prob=0.0 if rechecked else 0.5)
    else:
        caps = []
        warn(f"{CAPTIONS_SRT} not written: no competitor captions and no transcribed speech")
    res["weak_kept"] = [{"time": w["time"], "text": w["text"], "reason": w["reason"]} for w in weak]
    res["captions"] = [_caption_dict(c, fps) for c in caps]
    res["count"] = len(caps)
    res["by_mode"] = dict(Counter(c.mode for c in caps))
    res["parts"] = mode_parts(caps)
    res["placeholders"] = [_caption_dict(c, fps) for c in caps if c.mode == "placeholder"]
    res["over_cap"] = [_caption_dict(c, fps) for c in caps if len(c.text.replace("\n", " ")) >= HARD_CAP]
    res["stats"] = caption_stats(caps, fps) if caps else {}
    if caps:
        p = write_srt(caps, cfg.deliver / CAPTIONS_SRT, fps)
        res["path"] = str(p)
        dump_json(res, cfg.debug_dir / "captions.json")
        log.info("captions: %d captions (%s) -> %s", len(caps), mode, p)
    return res


def _read_spans(ctx, layout: dict, comp_fps: Fraction) -> dict:
    """caption_ocr.read_caption_spans of the competitor, cached in WORK_DIR."""
    from . import caption_ocr
    from .common import stage_key
    info = ctx.comp_info
    wh = (int(info.display_width or info.width), int(info.display_height or info.height))
    key = stage_key("captions_spans", info.file_hash, json_key(layout), caption_ocr.SPAN_VERSION)
    return ctx.cache.json("captions_spans", key, lambda: caption_ocr.read_caption_spans(
        info.path, layout, wh, comp_fps, ctx.n_comp))


def _lazy_opinion(ctx, layout: dict, comp_fps: Fraction):
    """The competitor's clearly read captions as the recheck's third opinion in forced voice mode: read (OCR) only
    when the recheck first asks, and only when the layout has a caption band."""
    box: dict = {}

    def get(t0: float, t1: float) -> str | None:
        if "get" not in box:
            box["get"] = None
            try:
                from . import caption_ocr
                from .caption_recheck import clear_captions
                info = ctx.comp_info
                wh = (int(info.display_width or info.width), int(info.display_height or info.height))
                if caption_ocr.available() is None and caption_ocr.caption_band(layout, wh) is not None:
                    box["get"] = clear_captions(_read_spans(ctx, layout, comp_fps).get("spans") or [], comp_fps)
            except Exception as e:  # noqa: BLE001 - no third opinion: the two transcriptions decide
                from .common import log
                log.info("captions: no competitor captions as a third opinion: %s: %s", type(e).__name__, e)
        return box["get"](t0, t1) if box["get"] else None
    return get


def json_key(layout: dict) -> str:
    import hashlib
    import json
    caps = [c for c in layout.get("captions") or [] if str(c.get("type", "captions")) == "captions"]
    zones = layout.get("zones") or []
    return hashlib.sha1(json.dumps([caps, zones], sort_keys=True, default=str).encode()).hexdigest()[:16]
