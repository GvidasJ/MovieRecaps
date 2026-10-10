"""caption_style.py: where the user breaks captions -- learned from the user's own finished captions (``srt/``), and
the grouping of words into captions that fits that style best.

**What is learned.** Every pair of neighbouring words in the user's SRTs is a *break* (they sit in two captions) or
not. The share of breaks is counted for the two words themselves ("go" | "to"), for a word and the kind of the
other (a pronoun, a preposition, a determiner, an auxiliary, a conjunction, a wh-word, an interjection, an adverb,
a number, a long word of 8+ letters, any other word), and for the two kinds; a rare pair borrows from the more
general counts (additive smoothing towards them), so "a" | "joke" (never broken) and "suggested" | "to" (usually
broken) both come out right. The share of captions of 1, 2, 3, 4 and 5 words is the length prior (the user: 24 %,
41 %, 27 %, 8 %, 1 %).

**The grouping.** The best split of a word sequence into captions (dynamic programming): the product of each
caption's length prior, the chance of not breaking between its words and of breaking after its last one. A pause
in speech makes a break likelier (its odds times exp(PAUSE_WEIGHT x pause / 0.25 s), up to 0.6 s). Hard limits:
at most MAX_CHARS characters and MAX_WORDS words, never across a sentence end ("." "?" "!" heard) or a video cut.

The answer keys of the test library (tests/real/*/answer.srt) are left out of what is learned, so check-all scores
captions the style model has not seen. ``python -m match_cuts.caption_style`` rebuilds ``caption_style.json``
(bundled with the package) from ``srt/``.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

MAX_CHARS = 20
MAX_WORDS = 5
PAUSE_WEIGHT = 3.0               # a 0.25 s pause multiplies the odds of a break by e^3 (~20), capped at 0.6 s
SMOOTH = 1.0                     # additive smoothing of a pair's break share towards the more general counts (3.0
                                 #   until overnight2: 1.0 trusts a pair's own counts more -- the offline replay
                                 #   of 11 answer keys: split errors 203 -> 192, exact captions 118 -> 120)
MODEL_FILE = Path(__file__).resolve().parent / "caption_style.json"
SRT_DIR = Path(__file__).resolve().parents[3] / "srt"

KINDS = {
    "DET": "a an the this these those my your his her our their its every some any no each another",
    "PREP": "of to in on at for with from by about into onto over under after before through without across behind "
            "between around off up down out",
    "PRON": "i you we they he she it me him us them i'm you're we're they're he's she's it's i've you've we've "
            "they've i'll you'll i'd you'd that's there's what's",
    "AUX": "am is are was were be been being have has had do does did can could will would shall should may might must "
           "gonna wanna gotta don't didn't doesn't can't won't isn't wasn't aren't weren't couldn't wouldn't "
           "shouldn't haven't hasn't",
    "CONJ": "and but or so because then if when while though although than as nor",
    "WH": "what when where why how who which whose",
    "INTJ": "oh yeah yes no well ok okay hey wow like um uh ah",
    "ADV": "not just really very too also even still only actually never always already again here there now",
}
_KIND = {w: k for k, ws in KINDS.items() for w in ws.split()}
_END = re.compile(r"[.?!]['\"”’)]*$")


def token(w: str) -> str:
    return re.sub(r"[^\w']", "", str(w).lower().replace("’", "'"))


def kind(w: str) -> str:
    t = token(w)
    if t in _KIND:
        return _KIND[t]
    if t[:1].isdigit():
        return "NUM"
    return "LONG" if len(t) >= 8 else "WORD"


def _keys(a: str, b: str) -> list[str]:
    """The pair's count keys, most specific first: words, word+kind, kind+word, kinds, kind+any, any+kind, any."""
    ta, tb, ka, kb = token(a), token(b), kind(a), kind(b)
    return [f"w:{ta}|w:{tb}", f"w:{ta}|k:{kb}", f"k:{ka}|w:{tb}", f"k:{ka}|k:{kb}", f"k:{ka}|*", f"*|k:{kb}", "*|*"]


def learn(srts: Iterable[str | Path]) -> dict:
    """The style model from SRT files: {"pairs": {key: [n, breaks]}, "lengths": {n: share}, "files": [...]}."""
    from .captions import read_srt
    pairs: dict[str, list[int]] = {}
    lengths: Counter = Counter()
    files = []
    for f in srts:
        files.append(Path(f).name)
        seq: list[tuple[str, bool] | None] = []
        for c in read_srt(f):
            t = c["text"].replace("\n", " ").strip()
            if not t or (t.startswith("*") and t.endswith("*")):
                seq.append(None)
                continue
            ws = t.split()
            lengths[min(len(ws), MAX_WORDS)] += 1
            seq += [(w, k == len(ws) - 1) for k, w in enumerate(ws)]
        for x, y in zip(seq, seq[1:]):
            if x is None or y is None or (x[1] and _END.search(x[0])):
                continue                     # a sound caption, or a sentence end: a break anyway, no preference
            for key in _keys(x[0], y[0]):
                p = pairs.setdefault(key, [0, 0])
                p[0] += 1
                p[1] += int(x[1])
    total = sum(lengths.values()) or 1
    return {"pairs": pairs, "lengths": {str(n): lengths[n] / total for n in range(1, MAX_WORDS + 1)},
            "files": sorted(files), "captions": total}


def held_out() -> set[str]:
    """The SRTs that are answer keys of the test library (same text as a tests/real/*/answer.srt): not learned."""
    from .testcases import CASES_DIR
    keys = {p.read_bytes().replace(b"\r\n", b"\n").strip() for p in CASES_DIR.glob("*/answer.srt")}
    return {p.name for p in SRT_DIR.glob("*.srt") if p.read_bytes().replace(b"\r\n", b"\n").strip() in keys}


def rebuild(srt_dir: Path = SRT_DIR, out: Path = MODEL_FILE) -> dict:
    skip = held_out()
    model = learn(sorted(p for p in srt_dir.glob("*.srt") if p.name not in skip))
    model["held_out"] = sorted(skip)
    out.write_text(json.dumps(model, indent=0, sort_keys=True), encoding="utf-8")
    return model


@lru_cache(maxsize=4)
def model(path: str = str(MODEL_FILE)) -> dict:
    p = Path(path)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"pairs": {}, "lengths": {"1": 0.24, "2": 0.41, "3": 0.27, "4": 0.08, "5": 0.01}}


def p_break(a: str, b: str, m: dict | None = None) -> float:
    """The chance the user breaks between words a and b (no pause information)."""
    m = m or model()
    pairs = m["pairs"]
    p = None
    for key in reversed(_keys(a, b)):
        n, k = pairs.get(key, (0, 0))
        p = (k + SMOOTH * p) / (n + SMOOTH) if p is not None else (k + 1) / (n + 2)
    return min(0.98, max(0.02, float(p)))


def group(texts: Sequence[str], starts: Sequence[float], ends: Sequence[float], forced: Iterable[int] = (),
          m: dict | None = None, hints: dict[int, float] | None = None) -> list[tuple[int, int]]:
    """The best captions for these words: [(i, j)] word index ranges. ``forced``: word indices a caption must start
    at (a video cut before the word, a sentence end before it, another caption's edge). ``hints``: {word index i:
    h} -- the odds of a break before word i times exp(h) (the competitor breaks there: h > 0; keeps the words
    together: h < 0)."""
    m = m or model()
    n = len(texts)
    if not n:
        return []
    forced = {int(i) for i in forced if 0 < int(i) < n}
    forced |= {i + 1 for i in range(n - 1) if _END.search(str(texts[i]))}
    prior = {int(k): float(v) for k, v in m["lengths"].items()}
    lb, lk = [0.0] * n, [0.0] * n
    for i in range(n - 1):
        p = p_break(texts[i], texts[i + 1], m)
        pause = max(0.0, float(starts[i + 1]) - float(ends[i]))
        odds = p / (1.0 - p) * math.exp(PAUSE_WEIGHT * min(pause, 0.6) / 0.25 + (hints or {}).get(i + 1, 0.0))
        p = min(1.0 - 1e-6, max(1e-6, odds / (1.0 + odds)))
        lb[i], lk[i] = math.log(p), math.log(1.0 - p)
    best = [-math.inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(max(0, j - MAX_WORDS), j):
            if best[i] == -math.inf or any(f in forced for f in range(i + 1, j)):
                continue
            if j - i > 1 and len(" ".join(texts[i:j])) > MAX_CHARS:
                continue
            s = best[i] + math.log(max(prior.get(j - i, 1e-4), 1e-4)) + sum(lk[i:j - 1])
            if j < n and j not in forced:
                s += lb[j - 1]
            if s > best[j]:
                best[j], back[j] = s, i
    out, j = [], n
    while j > 0:
        out.append((back[j], j))
        j = back[j]
    return out[::-1]


# ---------------------------------------------------------------------------------------------------------------------
# captions in the user's style
# ---------------------------------------------------------------------------------------------------------------------

LEAD_S = 0.5 / 60               # a caption starts on the frame its first word begins in (half a frame before it,
                                #   rounded): the user's captions
CUT_START_S = 0.5               # a caption whose first word comes this soon after a video cut starts on the cut
FILLERS = frozenset("like um uh umm uhh er erm ah".split())      # said, but left out when the competitor leaves them out
FLICKER_COMP_FRAMES = 2         # a competitor caption this short (its frames) reading like the next: that one's first frame
WORD_IN_S = 0.04                # a word belongs to the caption on screen this long after its (aligned) start
FIRST_WORD_S = 0.1              # follow mode: a caption's first word may start this long before the caption shows
MAX_LEAD_S = 0.5                # ... and the caption shows at most this long before its first word
COMP_BREAK_BONUS = 0.5          # regroup: a break where the competitor's caption changes, its odds times e^this (on
                                #   the answer keys you break there 44 % of the time, inside one of its captions 26 %;
                                #   1.0 until overnight2: 0.5 with SMOOTH 1.0 is the best of the grid, no video worse)


def style_captions(words, fps, n_frames: int, *, cuts: Sequence[int] = (), lo: int = 0, hi: int | None = None,
                   placeholders: bool = True, mode: str = "voice", comp_breaks: Iterable[int] = ()) -> list:
    """Captions of a word list (captions.Word, sequence seconds) in the user's style: grouped by ``group`` (learned
    from the user's SRTs; never across a sentence end or a video cut of the edit, ``cuts`` in sequence frames), each
    starting LEAD_S before its first word is heard -- on the cut when its first word is the first after a cut and
    comes at most CUT_START_S after it (the user starts those on the cut) -- and back to back. ``placeholders``: a
    stretch of more than captions.SILENCE_S without speech gets a ``*...*`` caption (the action goes there).
    ``comp_breaks``: id() of the words the competitor's caption changes before (competitor_breaks) -- a break there
    is likelier (COMP_BREAK_BONUS)."""
    from fractions import Fraction
    from .captions import PLACEHOLDER, SILENCE_S, Caption, _monotonic, to_frame
    fps = Fraction(fps)
    hi = int(n_frames if hi is None else hi)
    words = [w for w in words if w.text]
    if not words:
        if placeholders and hi - lo > to_frame(SILENCE_S, fps):
            return [Caption(PLACEHOLDER, lo, hi, "placeholder")]
        return []
    f = float(fps)
    cut_s = sorted(int(c) / f for c in cuts)
    forced = set()
    for k in range(1, len(words)):                   # a cut between two words: a caption starts at the second
        if any(words[k - 1].end - 0.02 <= c <= words[k].start + 0.02 or words[k - 1].start < c < words[k].start
               for c in cut_s):
            forced.add(k)
    breaks = set(comp_breaks)
    hints = {k: COMP_BREAK_BONUS for k in range(1, len(words)) if id(words[k]) in breaks}
    groups = group([w.raw or w.text for w in words], [w.start for w in words], [w.end for w in words], forced,
                   hints=hints)
    caps: list = []
    if placeholders and words[0].start - lo / f > SILENCE_S:
        caps.append(Caption(PLACEHOLDER, lo, to_frame(words[0].start, fps), "placeholder"))
    for gi, (i, j) in enumerate(groups):
        ws = words[i:j]
        prev_end = words[i - 1].end if i else lo / f
        after = [c for c in cut_s if prev_end - 0.02 <= c <= ws[0].start + 0.02]
        if after and ws[0].start - after[-1] <= CUT_START_S:
            start = to_frame(after[-1], fps)                       # on the cut, exactly
        else:
            start = to_frame(max(prev_end, ws[0].start - LEAD_S), fps)
        caps.append(Caption(" ".join(w.text for w in ws), start, max(start + 1, to_frame(ws[-1].end, fps)), mode,
                            list(ws), {"style": "user"}))
        nxt = words[groups[gi + 1][0]].start if gi + 1 < len(groups) else hi / f
        if placeholders and nxt - ws[-1].end > SILENCE_S:
            caps.append(Caption(PLACEHOLDER, to_frame(ws[-1].end, fps), to_frame(nxt, fps), "placeholder"))
    for a, b in zip(caps, caps[1:]):                 # back to back
        a.end = b.start
    return _monotonic(caps, lo, hi)


def competitor_breaks(spans: Sequence[dict], words, comp_tl, tool_tl, comp_fps) -> set[int]:
    """id() of each heard word the competitor's caption changes before: every word goes with the competitor caption
    on screen where the competitor plays the moment the word is said in the middle of (both edits matched by what
    they play: ``comp_tl`` the competitor's edit, ``tool_tl`` mine); two words in a row on two different captions."""
    cf = float(comp_fps)
    cover = [_intervals(comp_tl, int(d["comp_in"]) / cf, int(d["comp_out"]) / cf) for d in spans if d.get("ocr")]

    def owner(w) -> int | None:
        m = tool_tl.at(0.5 * (w.start + w.end))
        if m is None:
            return None
        return next((i for i, iv in enumerate(cover) if any(k == m[0] and x - WORD_IN_S <= m[1] < y + WORD_IN_S
                                                            for k, x, y in iv)), None)
    own = [owner(w) for w in words]
    return {id(words[k]) for k in range(1, len(words))
            if own[k - 1] is not None and own[k] is not None and own[k] != own[k - 1]}


def competitor_style(spans: Sequence[dict]) -> dict:
    """Does the competitor caption the user's way? Its captions as read: mostly mixed case (not ALL CAPS), about the
    user's length (1.6-3.2 words on average, at most half of them single words). {"follow": bool, "why", the
    numbers}. When it does, its own caption breaks and timing are kept (follow_competitor); otherwise the captions
    are made in the user's style from the words heard (style_captions), its text a second opinion."""
    from .captions import is_action_text
    texts = [str(d.get("ocr") or "").strip() for d in spans]
    spoken = [t for t in texts if t and not is_action_text(t)]
    if len(spoken) < 4:
        return {"follow": False, "why": f"only {len(spoken)} caption(s) read on the competitor", "n": len(spoken)}
    letters = [re.sub(r"[^A-Za-z]", "", t) for t in spoken]
    caps = sum(1 for x in letters if len(x) > 1 and x.isupper()) / len(spoken)
    nw = [len(t.split()) for t in spoken]
    mean = sum(nw) / len(nw)
    single = sum(1 for n in nw if n == 1) / len(nw)
    out = {"n": len(spoken), "all_caps": round(caps, 2), "mean_words": round(mean, 2), "single": round(single, 2)}
    if caps >= 0.3:
        return dict(out, follow=False, why=f"{100 * caps:.0f} % of its captions are in capitals")
    if not 1.6 <= mean <= 3.2:
        return dict(out, follow=False, why=f"{mean:.1f} words a caption on average (yours: about 2)")
    if single > 0.5:
        return dict(out, follow=False, why=f"{100 * single:.0f} % of its captions are single words")
    return dict(out, follow=True, why=f"mixed case, {mean:.1f} words a caption: like yours")


NOISE_GAP_FRAMES = 3            # screen reads this close (competitor frames) are one caption's reads (_screen_noise_out)
NOISE_SIMILAR = 0.75            # ... when their texts are this alike (an animated caption re-read on every frame)
NOISE_LETTERS = 2               # a read with at most this many letters, shorter than NOISE_SHORT_S, is a flicker ...
NOISE_SHORT_S = 0.3             # ... ("A", "1A", "'A", "o?": the pop-in of the caption after it)


def _letters(text: str) -> int:
    return sum(1 for ch in str(text) if "a" <= ch.lower() <= "z")


def _screen_noise_out(rows: list, cf: float) -> list:
    """The competitor's caption reads without screen noise (laptop004: an animated caption read on every frame --
    "A", "1A", "'A", "1", "_____", "Iguess it wuld be", "I guess it would be" -- each a caption of its own): a read with
    no letter goes; a read of one or two letters lasting under NOISE_SHORT_S joins the caption it touches (the one
    after it first: a pop-in); consecutive reads alike (NOISE_SIMILAR) become one caption, with the text read on
    the most frames (a real word first). ``rows``: [(comp_in, comp_out, text, span)]."""
    from difflib import SequenceMatcher

    def clean(t: str) -> str:              # a read on two lines: the line(s) with letters ("so?" over a glyph "Λ")
        lines = [ln.strip() for ln in str(t).split("\n") if ln.strip()]
        most = max([_letters(ln) for ln in lines] or [0])
        keep = [ln for ln in lines if _letters(ln) >= max(2, most // 2)] if most >= 2 else lines
        return re.sub(r"^[^A-Za-z'*\"(]*(?=[A-Za-z'*\"(])", "", " ".join(keep)).strip()
    rows = [(a, b, clean(t) if "\n" in str(t) else t, d) for a, b, t, d in rows]
    rows = [r for r in rows if _letters(r[2]) > 0]
    key = lambda s: re.sub(r"[^a-z0-9 ]", "", str(s).lower())                 # noqa: E731
    alike = lambda x, y: SequenceMatcher(None, key(x), key(y)).ratio() >= NOISE_SIMILAR   # noqa: E731
    short = lambda r: (r[1] - r[0]) / cf < NOISE_SHORT_S                       # noqa: E731
    flick: list = []                                     # a lone letter or two, read for a moment: its neighbour's
    for r in rows:
        if _letters(r[2]) <= NOISE_LETTERS and short(r) and flick and r[0] - flick[-1][1] <= NOISE_GAP_FRAMES and \
                _letters(flick[-1][2]) > NOISE_LETTERS:
            flick[-1] = (flick[-1][0], r[1], flick[-1][2], flick[-1][3])
            continue
        flick.append(r)
    rows = [r for i, r in enumerate(flick)               # a misread of 1-3 frames inside one caption's reads goes
            if not (0 < i < len(flick) - 1 and r[1] - r[0] <= 3 and not alike(r[2], flick[i - 1][2])
                    and alike(flick[i - 1][2], flick[i + 1][2]) and flick[i + 1][0] - flick[i - 1][1] <= 20)]
    out: list = []
    i = 0
    while i < len(rows):
        a, b, text, d = rows[i]
        if _letters(text) <= NOISE_LETTERS and (b - a) / cf < NOISE_SHORT_S:
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt is not None and nxt[0] - b <= NOISE_GAP_FRAMES and _letters(nxt[2]) > NOISE_LETTERS:
                rows[i + 1] = (a, nxt[1], nxt[2], nxt[3])           # the next caption's pop-in
                i += 1
                continue
            if out and a - out[-1][1] <= NOISE_GAP_FRAMES:
                pa, _pb, pt, pd = out[-1]
                out[-1] = (pa, b, pt, pd)
                i += 1
                continue
            i += 1
            continue
        out.append((a, b, text, d))
        i += 1
    merged: list = []
    for a, b, text, d in out:
        if merged:
            pa, pb, pt, pd, votes = merged[-1]
            last = max(votes, key=votes.get)
            if a - pb <= 5 * NOISE_GAP_FRAMES and alike(pt, text) and (votes[last] / cf < NOISE_SHORT_S or
                                                                     (b - a) / cf < NOISE_SHORT_S):
                votes[text] = votes.get(text, 0) + (b - a)
                merged[-1] = (pa, b, pt, pd, votes)
                continue
        merged.append((a, b, text, d, {text: b - a}))
    res = []
    for a, b, text, d, votes in merged:
        if len(votes) > 1:
            def score(t: str) -> tuple:
                ws = re.findall(r"[a-z']+", t.lower())
                try:
                    from .caption_rules import lexicon
                    lex = lexicon().lower
                    known = sum(1 for w in ws if w.strip("'") in lex)
                except Exception:  # noqa: BLE001
                    known = 0
                return (known - (len(ws) - known), votes[t])
            text = max(votes, key=score)
        res.append((a, b, text, d))
    return res


CLEAR_AGREEMENT = 0.9           # a competitor caption read the same on this share of its frames (and twice or more): its
#                                 words are trusted where the transcript lacks them (follow_competitor)


def _split_lost_space(words: list[str]) -> list[str]:
    """Screen words with a space the OCR lost after "I" put back: "Ishould" -> "I", "should" (021) -- an unknown word
    that is "I" + a known word."""
    try:
        from .caption_rules import lexicon
        lex = lexicon().lower
    except Exception:  # noqa: BLE001 - no word list: as read
        return words
    out: list[str] = []
    for w in words:
        core = re.sub(r"[^A-Za-z']", "", w)
        rest = core[1:]
        if len(core) > 2 and core[0] == "I" and rest[:1].islower() and core.lower() not in lex and rest.lower() in lex:
            out += ["I", w[w.index(core) + 1:] if core in w else rest]
        else:
            out.append(w)
    return out


def _same_word(a: str, b: str) -> bool:
    from .captions import norm
    return norm(a).strip("'") == norm(b).strip("'")


def _intervals(tl, t0: float, t1: float) -> list[tuple[str, float, float]]:
    """What a timeline plays over its seconds [t0, t1): [(kind, source from, source to)] -- a caption on screen
    across a cut of that edit covers two stretches of the source."""
    out = []
    for p in tl.pieces:
        a, b = max(t0, p.t0), min(t1, p.t1)
        if b > a:
            x, y = sorted((p.at(a), p.at(b)))
            out.append((p.kind, x, y))
    return out


def _owners(words, cover, starts, tool_tl, f) -> list[int | None]:
    """Which competitor caption each heard word goes to: the caption whose stretch of the source holds the moment
    the word starts on (``cover``: each caption's source intervals) -- else, where my edit plays more of a word
    than the competitor did (a clip played on to finish it), the caption on screen there in my edit (``starts``:
    each caption's first frame in my edit). Then a word that ends a sentence stays with the words before it (the
    competitor may switch captions while it is said: "get out of this" | "This is going")."""
    from .captions import sentence_end
    owner: list[int | None] = []
    last = 0
    known = sorted((s, i) for i, s in enumerate(starts) if s is not None)
    for w in words:
        m = tool_tl.at(w.start + WORD_IN_S)
        hits = [i for i, iv in enumerate(cover) if m is not None and
                any(k == m[0] and x - 1e-6 <= m[1] < y for k, x, y in iv)]
        k = min(hits, key=lambda i: (i < last, abs(i - last))) if hits else None
        if k is None and known:                      # by my edit's own time
            fr = (w.start + WORD_IN_S) * float(f)
            before = [i for s0, i in known if s0 <= fr]
            k = before[-1] if before else known[0][1]
        owner.append(k)
        if k is not None:
            last = k
    for j in range(1, len(words)):                   # a sentence's last word with the caption of its sentence
        if owner[j] is not None and owner[j - 1] is not None and owner[j] == owner[j - 1] + 1 and \
                sentence_end(words[j].raw or words[j].text) and not sentence_end(words[j - 1].raw or words[j - 1].text) \
                and (j + 1 >= len(words) or owner[j + 1] == owner[j]):
            owner[j] = owner[j - 1]
    return owner


def _by_screen(words, owner: list, screens: Sequence[str]) -> None:
    """Each boundary between two competitor captions, settled by what they show: of every way to share the heard
    words around it between the two, the one whose words match the two captions' text best (the same words written
    two ways -- "gonna" / "going to" -- match); a tie keeps the split by time. The competitor may switch captions
    while a word is said ("to get out of" | "This is going" for "get out of this. This is gonna")."""
    from difflib import SequenceMatcher
    from .caption_recheck import _tokens
    toks = [_tokens([t]) for t in screens]

    def match(ws, k) -> int:
        a = _tokens([w.text for w in ws])
        return sum(b.size for b in SequenceMatcher(None, a, toks[k], autojunk=False).get_matching_blocks())
    for k in range(len(screens) - 1):
        idx = [j for j, o in enumerate(owner) if o in (k, k + 1)]
        if not idx or not toks[k] or not toks[k + 1] or idx != list(range(idx[0], idx[-1] + 1)):
            continue
        cur = sum(1 for j in idx if owner[j] == k)
        best, best_n = None, cur
        for n in range(len(idx) + 1):
            sc = match([words[j] for j in idx[:n]], k) + match([words[j] for j in idx[n:]], k + 1)
            if best is None or sc > best or (sc == best and n == cur):
                best, best_n = sc, n
        for i, j in enumerate(idx):
            owner[j] = k if i < best_n else k + 1


def follow_competitor(spans: Sequence[dict], words, comp_tl, tool_tl, comp_fps, seq_fps) -> tuple[list, dict]:
    """The competitor's captions where it captions the user's way (competitor_style): its own caption breaks and
    timing, the words in it **as heard** ("we're going" read, "we're gonna" said: "we're gonna").

    Both edits are matched by what they play, not by their timelines (the speech-safe cuts make mine longer):
    ``comp_tl`` -- the competitor's edit in its picture's time (caption_score.competitor_timeline), ``tool_tl`` --
    my final edit (caption_score.Timeline.from_xml). A caption starts where my edit plays the moment the
    competitor's caption starts on; where the two edits differ between that moment and the caption's first word, on
    the words my edit plays: a cut of the competitor's between them -- the caption goes with the take after the cut,
    as long before my edit plays it as the competitor's caption shows before its cut (one frame before: on the cut);
    my edit without that moment -- as long before its first word as the competitor's caption (at most MAX_LEAD_S),
    never before the word before it ends. A heard word belongs to the caption on screen as it starts (the
    competitor switches captions as the word before ends). The screen gives the capital of a caption's
    first word and keeps a filler (like, um, uh) or a repeat ("yeah yeah") out when it leaves it out; its own text
    stays where nothing was heard (a ``*sound*`` caption, a word the transcript missed). ``spans``: the competitor's
    captions as read (competitor frames). Returns (captions, notes)."""
    from difflib import SequenceMatcher
    from fractions import Fraction
    from .caption_score import SAME_MOMENT_S
    from .captions import Caption, clean_text, is_action_text, norm, to_frame
    cf, f = float(Fraction(comp_fps)), Fraction(seq_fps)
    rows = []
    for d in sorted(spans, key=lambda d: int(d["comp_in"])):
        a, b = int(d["comp_in"]), int(d["comp_out"])
        text = str(d.get("ocr") or "").strip()
        if b <= a:
            continue
        if rows and rows[-1][1] - rows[-1][0] <= FLICKER_COMP_FRAMES and text and rows[-1][2] and \
                SequenceMatcher(None, rows[-1][2].lower(), text.lower()).ratio() >= 0.6:
            a = rows.pop()[0]                        # a misread first frame of this caption ("his is going")
        rows.append((a, b, text, d))
    rows = _screen_noise_out(rows, cf)
    cover = [_intervals(comp_tl, a / cf, b / cf) for a, b, _, _ in rows]
    notes: dict = {"from_screen": [], "changed": [], "not_in_edit": []}
    starts: list[int | None] = []
    after = None
    cuts = [q.t0 for p, q in zip(comp_tl.pieces, comp_tl.pieces[1:])        # where its edit jumps (not a piece
            if q.t0 > p.t1 + 1e-6 or q.kind != p.kind or abs(p.at(q.t0) - q.src) > 0.5 / cf]   # that runs on)
    said: list[float | None] = []                    # where the competitor plays each heard word's start (its time)
    last = None
    for w in words:
        m = tool_tl.at(float(w.start) + 1e-4)
        hit = comp_tl.find(m[0], m[1], last) if m is not None else None
        said.append(hit[0] if hit is not None and hit[1] <= SAME_MOMENT_S else None)
        last = said[-1] if said[-1] is not None else last
    for i, (a, b, text, d) in enumerate(rows):       # where my edit plays each caption's first moment
        t0, pre = a / cf, 0.0
        sw = text.split()
        j = next((k for k, x in enumerate(said) if x is not None and x >= t0 - FIRST_WORD_S), None)
        tw = said[j] if j is not None and sw and _same_word(words[j].text, sw[0]) and said[j] - t0 <= MAX_LEAD_S \
            else None                                # its first word, as the competitor plays it
        cut = max((c for c in cuts if t0 < c <= max(t0 + 1.0 / cf, tw or 0.0) + 1e-6), default=None)
        if cut is not None:      # the competitor cuts before its first word: the caption goes with the take after
            pre = cut - t0 if cut - t0 > 1.0 / cf + 1e-6 else 0.0      # the cut (one frame before it: on the cut)
            t0 = cut
        m = comp_tl.at(t0 + 1e-4)
        hit = tool_tl.find(m[0], m[1], after) if m is not None else None
        if hit is not None and hit[1] <= SAME_MOMENT_S:
            starts.append(to_frame(max(0.0, hit[0] - pre), f))
            after = hit[0]
        else:
            starts.append(None)
    owner = _owners(words, cover, starts, tool_tl, f)
    _by_screen(words, owner, [r[2] for r in rows])
    caps = []
    for i, (a, b, screen, d) in enumerate(rows):
        heard = [w for w, k in zip(words, owner) if k == i]
        info = {k: d.get(k) for k in ("score", "agreement", "reads", "variants", "comp_in", "comp_out")}
        start = starts[i]
        if start is None and heard:              # my edit leaves out its first moment: on its first word, as long
            k = next(n for n, w in enumerate(words) if w is heard[0])      # before it as the competitor's caption
            x = said[k]                                                     # (never before the word before it ends)
            lead = min(MAX_LEAD_S, max(0.0, x - a / cf)) if x is not None else LEAD_S
            lo = float(words[k - 1].end) if k > 0 else 0.0
            start = to_frame(max(0.0, lo, heard[0].start - lead), f)
        if start is None and is_action_text(screen):
            # an action caption ("*disgusted*") whose first moment my edit leaves out: from the first moment of it my
            # edit plays -- never dropped while my edit shows part of it (the caption before would run on over it)
            prev_t = caps[-1].start / float(f) if caps else None          # after the caption before it
            for n in range(int(a) + 1, int(b)):
                m = comp_tl.at(n / cf + 1e-4)
                hit = tool_tl.find(m[0], m[1], prev_t) if m is not None else None
                if hit is not None and hit[1] <= SAME_MOMENT_S:
                    start = to_frame(hit[0], f)
                    break
        if start is None:
            notes["not_in_edit"].append({"text": screen, "comp_in": a})
            continue
        if not heard or is_action_text(screen):
            if screen:
                caps.append(Caption(screen, start, start + 1, "competitor", [],
                                    dict(info, source="screen", style="follow", screen=screen)))
                if not is_action_text(screen):
                    notes["from_screen"].append({"start": start, "text": screen})
            continue
        sw = _split_lost_space(screen.split())
        sm = SequenceMatcher(None, [norm(x).strip("'") for x in sw], [norm(w.text).strip("'") for w in heard],
                             autojunk=False)
        out_words: list[str] = []
        clear = float(info.get("agreement") or 0.0) >= CLEAR_AGREEMENT and int(info.get("reads") or 0) >= 2
        nxt_first = norm(rows[i + 1][2].split()[0]).strip("'") if i + 1 < len(rows) and rows[i + 1][2].split() else ""
        ops = sm.get_opcodes()
        for tag, i1, i2, j1, j2 in ops:
            if clear and tag == "delete" and (i1 == 0 or i2 == len(sw)) and not any(
                    is_action_text(x) for x in sw[i1:i2]):
                # a word the competitor shows at its caption's edge that the transcript lacks here: said (021: "I've-
                # I'm currently" heard as one "I'm", "why are you" as "are you" -- you kept the screen's words)
                out_words.extend(re.sub(r"[.,]+$", "", x) for x in sw[i1:i2])
                notes.setdefault("kept_from_screen", []).append({"start": starts[i], "words": " ".join(sw[i1:i2])})
                continue
            if clear and tag == "replace" and j2 == len(heard) and i2 == len(sw) and nxt_first and \
                    norm(heard[j1].text).strip("'") == nxt_first:
                # the heard word opens the next caption on the screen (021: "children" heard as the "I'm" of "I'm
                # still" -- the transcript missed "children"): the screen's word, the heard one goes with its caption
                out_words.extend(re.sub(r"[.,]+$", "", x) for x in sw[i1:i2])
                notes.setdefault("kept_from_screen", []).append({"start": starts[i], "words": " ".join(sw[i1:i2])})
                continue
            if tag == "equal":
                for k2, (x, w) in enumerate(zip(sw[i1:i2], heard[j1:j2])):   # the screen's capitals, heard ? and !
                    tail = re.sub(r"^[\w'\u2019-]+", "", (w.raw or w.text).strip())
                    x = re.sub(r"[.,?!]+$", "", x)
                    if i1 + k2 == 0 and out_words and x[:1].isupper() and not (w.raw or w.text)[:1].isupper():
                        x = x[:1].lower() + x[1:]        # its sentence start, no longer first: as heard
                    out_words.append(x + (tail[:1] if tail[:1] in "?!" else ""))
            elif tag in ("insert", "replace"):
                for k2, w in enumerate(heard[j1:j2]):
                    n = norm(w.text)
                    prev = norm(out_words[-1]) if out_words else ""
                    if n in FILLERS and not any(_same_word(w.text, x) for x in sw[i1:i2]):
                        continue                         # a filler the competitor leaves out
                    if n == prev and not any(norm(x) == n for x in sw[i1:i2]):
                        continue                         # a repeat ("yeah yeah") the competitor writes once
                    out_words.append(w.raw or w.text)
            # "delete": words only the screen has -- heard as something else ("going to" said "gonna"), or not said
        if out_words and sw and norm(out_words[0]) != norm(sw[0]) and sw[0][:1].isupper() and \
                not any(_same_word(out_words[0], x) for x in sw):
            pass                                         # another first word than the screen's: its own capital
        elif out_words and sw and sw[0][:1].isupper() and out_words[0][:1].islower() and \
                _same_word(out_words[0], sw[0]):
            out_words[0] = out_words[0][:1].upper() + out_words[0][1:]
        text = clean_text(" ".join(out_words)) or screen
        text = re.sub(r"[?!]+(?=.*\S)(?=\s)", lambda mm: mm.group(0)[:1], text)    # "??" -> "?"
        text = re.sub(r"([?!])\1+", r"\1", text)
        if norm(text) != norm(screen):
            notes["changed"].append({"start": start, "screen": screen, "heard": text})
        caps.append(Caption(text, start, start + 1, "competitor", list(heard),
                            dict(info, source="heard", style="follow", screen=screen)))
    caps.sort(key=lambda c: c.start)
    out = []
    for c in caps:                                   # one caption per start frame, in order
        if out and c.start <= out[-1].start:
            c.start = out[-1].start + 1
        out.append(c)
    for x, y in zip(out, out[1:]):                   # back to back
        x.end = y.start
    if out:
        hi = [w.end for w in words if w.end > out[-1].start / float(f)]
        out[-1].end = max(out[-1].start + 1, to_frame(max(hi), f) if hi else out[-1].start + int(f))
    unowned = [w for w, k in zip(words, owner) if k is None]
    notes["unowned"] = [w.raw or w.text for w in unowned]
    return out, notes


def main() -> int:
    m = rebuild()
    print(f"caption_style.json: {m['captions']} captions from {len(m['files'])} SRT(s) in {SRT_DIR}; held out (answer "
          f"keys): {', '.join(m['held_out']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
