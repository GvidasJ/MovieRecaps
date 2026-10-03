"""The hard rules of caption-generator-prompt.md ("Hard rules -- check your own output before you emit it"): a final
check on every caption file before it is written (captions.run_captions; captions.write_srt refuses a file that still
breaks rules 1-4).

  1 one utterance per caption: no ``?`` / ``!`` / ``.`` with more text after it
  2 one speaker per caption: the transcriber has no speaker labels, but a reply always starts a new sentence in the
    transcript -- a sentence end the transcript heard inside a caption (also where the screen shows no punctuation,
    "How are you Thanks") is a speaker / sentence boundary
  3 no capital inside a word (``yoU``)
  4 no ALL CAPS except genuine acronyms: ``caption_allowlist.txt`` (AI, MJ, MCU, extend it) and the acronyms the word
    list writes in capitals (FBI, NASA, TV) -- never a word that is also an ordinary word (AS, WAS, SHOULD, IT)
  5 every token a real word: the word list (wordlist/, SCOWL), the allowlist, numbers, names (a capitalised word),
    interjections (``ohhh``, ``ummm``)
  6 length: spoken captions at most 20 characters and 5 words, ``*actions*`` 24
  7 no weak last word (a, the, to, of, ...) where the word can move to the next caption
  8 no gaps: ``end[i] == start[i+1]`` (voice mode; competitor mode keeps the competitor's timing, gaps included)

enforce() fixes what is mechanical and reports the rest: 1, 2 and 6 re-split the caption at the boundary (the split
time from the transcript's word timings, else shared by characters), 7 moves the weak word to the front of the next
caption, 8 closes the gap, 3 and 4 recase the word (the transcript's casing where it heard the same word, else the
word list's, sentence case), 5 -- competitor mode only -- takes the transcript's word where the screen reading is
not a real word, was not read clearly and the transcript heard a real word there clearly (a clearly read word is
never changed, so deliberate misspellings stay), and drops screen noise (a reading with no word in it, "1", "V",
"_", where nothing was heard). Everything else is listed for a look, never guessed. check() lists what is left.
"""
from __future__ import annotations

import difflib
import gzip
import itertools
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Sequence

from .captions import (HARD_CAP, INTERJECTION_RE, MAX_CHARS, MAX_WORDS, PLACEHOLDER, SILENCE_S, Caption, Word,
                       clean_text, compute_bonds, frame_ms, is_action_text, is_weak, ms_tc, norm, sentence_end,
                       to_frame)

RULES = {1: "one sentence", 2: "one speaker", 3: "casing inside a word", 4: "all caps", 5: "real words",
         6: "length", 7: "weak last word", 8: "no gaps"}
MAX_SPOKEN_WORDS = 5          # rule 6: spoken captions top out at 20 characters (MAX_CHARS) and 5 words
CLEAR_PROB = 0.6              # a transcript word heard at least this sure is "clearly heard"
CLEAR_SCORE = 0.9             # a screen reading (OCR) at least this sure ...
CLEAR_AGREEMENT = 0.6         # ... and agreed by at least this share of its frames is "clearly read"
SIMILAR = 0.5                 # a garbled reading and the word heard there: letters at least this alike ...
SAME_WORDS = 0.75             # ... a reading with a real word in it ("We wre" ~ "We're"): at least this alike
FLICKER_S = 0.15              # a garbled screen reading on screen less than this: a flicker of noise, not a caption
TOUCH_FRAMES = 2              # competitor captions this close (sequence frames) are back to back
ALIGN_TOL_S = 0.15            # transcript words up to this far outside a caption may belong to it

ALLOWLIST_FILE = Path(__file__).resolve().parents[1] / "caption_allowlist.txt"
WORDS_FILE = Path(__file__).resolve().parent / "wordlist" / "words_en.txt.gz"
DEFAULT_ALLOW = ("AI", "MJ", "MCU")
SHORT_WORDS = frozenset("""a ad ah am an as at aw ax be by do eh ex go ha he hi hm if in is it lo ma me mm my no of oh
    ok on or ow ox pa so to uh um up us we ya ye yo""".split())     # words of 1-2 letters (and "I"; the list has more)
INTERJECTIONS = frozenset("""oh ohh ah ahh aw aww um umm uh uhh hmm mm mhm erm er amm woah whoa wow woho woohoo wohoo
    wahoo yay yo ugh huh ha haha hahaha eh oops ouch phew shh ooh oo yeah yep yup nope nah okay ok hey hi ew eww meh
    psst tsk""".split())
_LATIN = re.compile(r"^[A-Za-zÀ-ɏ']+$")
_NUMBER = re.compile(r"^[£$€#~]?\d+(?:[.,:/]\d+)*(?:%|k|m|bn|st|nd|rd|th|s|am|pm|p|x|ft|kg|km|mph)?$", re.I)


# ---------------------------------------------------------------------------------------------
# The word list and the allowlist
# ---------------------------------------------------------------------------------------------

@dataclass
class Lexicon:
    """lower: the words the list writes in lower case; forms: lower case -> the other ways it writes them (Peter,
    NASA, McDonald); allow: lower case -> the allowlist's form."""
    lower: set[str] = field(default_factory=set)
    forms: dict[str, set[str]] = field(default_factory=dict)
    allow: dict[str, str] = field(default_factory=dict)
    allow_file: str = ""

    def known(self, lw: str) -> bool:
        return lw in self.lower or lw in self.forms


_LEX: dict[str, Lexicon] = {}


def read_allowlist(path: str | Path | None = None) -> list[str]:
    """The words of caption_allowlist.txt (one or more per line, ``#`` notes); the defaults when it is missing."""
    p = Path(path) if path else ALLOWLIST_FILE
    if not p.is_file():
        return list(DEFAULT_ALLOW)
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0]
        out += [w.strip() for w in re.split(r"[\s,;]+", line) if w.strip()]
    return out


def lexicon(allowlist: str | Path | None = None) -> Lexicon:
    """The word list (loaded once) with this allowlist."""
    key = str(Path(allowlist) if allowlist else ALLOWLIST_FILE)
    if key not in _LEX:
        if "" not in _LEX:
            base = Lexicon()
            for w in gzip.decompress(WORDS_FILE.read_bytes()).decode("utf-8").split("\n"):
                if not w:
                    continue
                lw = w.lower()
                if w == lw:
                    base.lower.add(lw)
                else:
                    base.forms.setdefault(lw, set()).add(w)
            _LEX[""] = base
        base = _LEX[""]
        words = read_allowlist(allowlist)
        _LEX[key] = Lexicon(base.lower, base.forms, {w.lower().replace("’", "'"): w for w in words}, key)
    return _LEX[key]


# ---------------------------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------------------------

def core(tok: str) -> str:
    """A token without its quotes, brackets, asterisks and sentence punctuation: the word itself ("you?" -> you,
    '"d' -> d); curly apostrophes made straight."""
    t = str(tok).replace("’", "'").replace("‘", "'")
    t = t.strip("\"“”«»()[]{}<>*_|~^`")
    t = t.rstrip("?!.,;:…'").lstrip("?!.,;:…'-")
    return t.strip("\"“”«»()[]{}<>*_|~^`")


def is_number(c: str) -> bool:
    return bool(_NUMBER.match(c))


def _stretched(lw: str) -> list[str]:
    """A word said long ("sooo", "woahhh", "yesss"): runs of three or more of a letter as one."""
    one = re.sub(r"(.)\1{2,}", r"\1", lw)
    two = re.sub(r"(.)\1{2,}", r"\1\1", lw)
    return [w for w in (one, two) if w != lw]


def is_interjection_word(lw: str) -> bool:
    return lw in INTERJECTIONS or bool(INTERJECTION_RE.match(lw)) or any(w in INTERJECTIONS for w in _stretched(lw))


def is_real(c: str, lex: Lexicon) -> bool:
    """Rule 5: a word of the list (any case), of the allowlist, a number, an interjection; a hyphenated word whose
    parts are (single letters allowed: "P-P-Peter", "X-Men"); a possessive or contraction of one. Words of one or two
    letters only from SHORT_WORDS ("I", "a", "of"), so a stray "V" or "ou" read off the screen is not a word."""
    c = c.replace("’", "'")
    if not c:
        return False
    if is_number(c):
        return True
    lw = c.lower()
    if lw in lex.allow:
        return True
    if "-" in c:
        parts = [p for p in c.split("-") if p]
        return bool(parts) and all(is_real(p, lex) or (len(p) == 1 and p.isascii() and p.isalpha()) for p in parts) \
            and any(len(p) >= 2 and is_real(p, lex) for p in parts)
    if not _LATIN.match(c):
        return False
    letters = lw.replace("'", "")
    if len(letters) <= 2:
        if "'" in lw:
            return lex.known(lw)                            # I'm, I'd
        return letters in SHORT_WORDS or c == "I" or is_interjection_word(letters)
    if lex.known(lw) or is_interjection_word(lw) or any(lex.known(w) for w in _stretched(lw)):
        return True
    for suffix in ("'s", "s'", "'ll", "'re", "'ve", "'d", "'m"):     # possessive / contraction of a known word
        if lw.endswith(suffix) and len(lw) > len(suffix) + 1:
            stem = c[:-len(suffix)]
            return is_real(stem, lex) or is_name(stem)
    return False


def is_name(c: str) -> bool:
    """A capitalised word (Keanu, Giolitti, Spider-Man): a name, not flagged by rule 5."""
    c = c.replace("’", "'")
    letters = c.replace("-", "").replace("'", "")
    return len(letters) >= 2 and c[:1].isupper() and not letters.isupper() and bool(_LATIN.match(letters))


def _letters(c: str) -> str:
    return re.sub(r"[^A-Za-zÀ-ɏ]", "", c)


def is_acronym(c: str, lex: Lexicon) -> bool:
    """Rule 4: a genuine acronym -- on the allowlist, or written in capitals by the word list and not an ordinary
    word as well (FBI, NASA; never AS, WAS, IT, US)."""
    base = re.sub(r"'[sS]$", "", c.replace("’", "'"))
    lw = base.lower()
    if lex.allow.get(lw) == base:
        return True
    return base in lex.forms.get(lw, ()) and lw not in lex.lower


def case_ok(c: str, lex: Lexicon) -> bool:
    """Rules 3 and 4: lower case, Capitalised (each part of a hyphenated / apostrophe word), or a form the allowlist /
    word list writes that way (MCU, FBI, McDonald); never yoU, never AS."""
    c = c.replace("’", "'")
    letters = _letters(c)
    if len(letters) <= 1:
        return True
    lw = c.lower()
    if lw in lex.allow:
        return c == lex.allow[lw] or (c == lw and lex.known(lw))
    if letters.isupper():
        return is_acronym(c, lex)
    if c in lex.forms.get(lw, ()):
        return True
    for part in re.split(r"[-']", c):
        p = _letters(part)
        if len(p) > 1 and not (p.islower() or (p[0].isupper() and p[1:].islower())):
            return False
    return True


def fix_case(c: str, heard: Word | None, sentence_start: bool, lex: Lexicon) -> str:
    """Rules 3 / 4: a word written as spoken, in sentence case. The allowlist's form; else the transcript's casing
    where it heard this word (when that casing is allowed); else the word list's (WAS -> was, PETER -> Peter, yoU ->
    you), a capital at a sentence start, and "I" always."""
    c = c.replace("’", "'")
    lw = c.lower()
    if lw in lex.allow and not (c == lw and lex.known(lw)):
        return lex.allow[lw]
    hc = core(heard.text).replace("’", "'") if heard is not None else ""
    mixed = [f for f in sorted(lex.forms.get(lw, ())) if not _letters(f).isupper()]
    if hc.lower() == lw and case_ok(hc, lex):
        out = hc                                          # as the transcript wrote it
    elif lw in lex.lower:
        out = lw
    elif mixed:
        out = mixed[0]                                    # Peter, McDonald
    else:
        out = "".join(p[:1] + p[1:].lower() if not _letters(p).isupper() else p.lower()
                      for p in re.split(r"([-'])", c))     # yoU -> you, HOw -> How, GOJO -> gojo
    if lw == "i" or lw.startswith("i'"):
        out = "I" + out[1:]
    if sentence_start and out[:1].islower():
        out = out[:1].upper() + out[1:]
    return out


def _artefact(tok: str) -> bool:
    """A reading that cannot be anyone's spelling: a digit inside letters (t1o, se9), a stray mark inside (d!e), or
    letters of another script."""
    c = core(tok)
    return bool(re.search(r"[A-Za-z]", c) and re.search(r"\d", c)) or bool(re.search(r"[^\w'’\-£$€%.,:/&]", c)) \
        or any(ord(ch) > 0x24F and ch.isalpha() for ch in c)


# ---------------------------------------------------------------------------------------------
# Working form: a caption as tokens, each with the transcript word it stands for
# ---------------------------------------------------------------------------------------------

@dataclass
class Tok:
    text: str                    # as written
    raw: str                     # as read / heard (sentence punctuation kept)
    word: Word | None = None     # the transcript word it stands for (timing, casing, sentence end)


@dataclass
class Cap:
    start: int
    end: int
    toks: list[Tok]
    mode: str
    info: dict
    changed: set = field(default_factory=set)       # rules that changed it
    gave: bool = False                               # it gave its weak last word to the next caption

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.toks)

    @property
    def spoken(self) -> bool:
        return self.mode != "placeholder" and not is_action_text(self.text)


def _tc(fr: int, fps: Fraction) -> str:
    return ms_tc(frame_ms(fr, fps))


class Report:
    def __init__(self, fps: Fraction, mode: str):
        self.fps, self.mode = fps, mode
        self.changed: dict[int, int] = {r: 0 for r in RULES}
        self.flagged: dict[int, int] = {r: 0 for r in RULES}
        self.rows: list[dict] = []           # listed under "Captions worth a look"
        self.notes: dict[str, int] = {"from_transcript": 0, "noise_dropped": 0, "stops_commas": 0}
        self.kept_weak: list[dict] = []

    def change(self, c: Cap, rule: int) -> None:
        if rule not in c.changed:
            c.changed.add(rule)
            self.changed[rule] += 1

    def row(self, rule: int, kind: str, a: int, b: int, text: str, detail: str) -> None:
        self.rows.append({"rule": rule, "kind": kind, "start": a, "end": b, "start_tc": _tc(a, self.fps),
                          "end_tc": _tc(b, self.fps), "text": text, "detail": detail})

    def to_dict(self) -> dict:
        return {"mode": self.mode, "changed": dict(self.changed), "flagged": dict(self.flagged), "rows": self.rows,
                "notes": dict(self.notes), "kept_weak": self.kept_weak}


def _split_frame(c: Cap, k: int, fps: Fraction, lo: int | None = None, hi: int | None = None) -> int | None:
    """The frame where token k starts inside caption c, after lo (default c.start) and at most hi (default
    c.end - 1): its transcript word's start, else the caption's time shared out by characters."""
    lo = c.start if lo is None else lo
    hi = c.end - 1 if hi is None else hi
    if hi <= lo:
        return None
    w = c.toks[k].word
    if w is not None and (k == 0 or c.toks[k - 1].word is not w):
        f = to_frame(w.start, fps)
        if lo < f <= hi:
            return f
    total = len(c.text)
    before = len(" ".join(t.text for t in c.toks[:k])) + 1
    f = c.start + int(round((c.end - c.start) * before / max(1, total)))
    return min(max(f, lo + 1), hi)


def _split(c: Cap, ks: Sequence[int], fps: Fraction, rule: int, rep: Report) -> list[Cap]:
    """Caption c cut before each token index in ks: the pieces back to back inside c's own span."""
    cuts = []
    lo = c.start
    for n, k in enumerate(ks):
        f = _split_frame(c, k, fps, lo=lo, hi=c.end - (len(ks) - n))
        if f is None or f <= lo:
            break
        cuts.append((k, f))
        lo = f
    if not cuts:
        return [c]
    rep.change(c, rule)                               # one caption changed, however many pieces
    out, prev_k, prev_f = [], 0, c.start
    for k, f in cuts + [(len(c.toks), c.end)]:
        out.append(Cap(prev_f, f, c.toks[prev_k:k], c.mode, dict(c.info), set(c.changed)))
        prev_k, prev_f = k, f
    if cuts[-1][0] != ks[-1]:                         # too short to cut everywhere: the rest stays together
        rep.row(rule, "flagged", c.start, c.end, c.text, "too short to split at every boundary")
        rep.flagged[rule] += 1
    return out


# ---------------------------------------------------------------------------------------------
# Competitor mode: the screen tokens matched to the transcript
# ---------------------------------------------------------------------------------------------

def _junk_cleanup(c: Cap, rep: Report) -> None:
    """Screen readings: one line; stray marks off the token edges (".of" -> "of", "!wer" -> "wer"), a lone ? / ! on
    the word before it, tokens with nothing readable ("_", "二") and letters of another script dropped."""
    out: list[Tok] = []
    changed = False
    for t in c.toks:
        s = t.text
        s2 = "".join(ch for ch in s if not (ch.isalpha() and ord(ch) > 0x24F))
        s2 = re.sub(r"^[?!.,;:_|~^`\\/]+(?=[\w\"'“‘*£$])", "", s2)
        if s2 in ("?", "!") and out:
            out[-1] = Tok(out[-1].text + s2, out[-1].raw + s2, out[-1].word)
            changed = True
            continue
        if not any(ch.isalnum() for ch in s2):
            changed = True
            continue
        changed |= s2 != s
        out.append(Tok(s2, s2 if s2 != s else t.raw, t.word))
    if changed:
        rep.change(c, 5)
    c.toks = out


def _heard_in(words: Sequence[Word], a_s: float, b_s: float, used: set[int], tol: float = ALIGN_TOL_S) -> list[Word]:
    return [w for w in words if id(w) not in used and a_s - tol <= 0.5 * (w.start + w.end) < b_s + tol]


def _align(c: Cap, heard: list[Word]) -> list[tuple[str, int, int, int, int]]:
    """difflib opcodes of the caption's tokens against the words heard; each token gets the word it stands for
    (equal or a one-to-one replacement; a token of an uneven block the block's word at its share)."""
    a = [norm(core(t.text)) for t in c.toks]
    b = [norm(w.text) for w in heard]
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 != j2 - j1:
            # an uneven block: the stretch of words heard that reads most like the tokens ("We wre" ~ "We're" of
            # "We're going to"); the words around it were not on screen here
            text = " ".join(t.text for t in c.toks[i1:i2])
            s0, s1 = max(((x, y) for x in range(j1, j2) for y in range(x + 1, j2 + 1)),
                         key=lambda xy: (round(_alike(text, " ".join(w.text for w in heard[xy[0]:xy[1]])), 2),
                                         -abs(xy[1] - xy[0] - (i2 - i1)), -(xy[1] - xy[0])))
            ops += [("insert", i1, i1, j1, s0)] if s0 > j1 else []
            ops.append(("replace", i1, i2, s0, s1))
            ops += [("insert", i2, i2, s1, j2)] if s1 < j2 else []
        else:
            ops.append((tag, i1, i2, j1, j2))
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for i, j in zip(range(i1, i2), range(j1, j2)):
                c.toks[i].word = heard[j]
        elif tag == "replace":
            for i in range(i1, i2):
                c.toks[i].word = heard[j1 + (i - i1) * (j2 - j1) // (i2 - i1)]
    return ops


def _alike(a: str, b: str) -> float:
    x, y = _letters(a).lower(), _letters(b).lower()
    return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() if x and y else 0.0


def _clearly_read(c: Cap) -> bool:
    sc, ag = c.info.get("score"), c.info.get("agreement")
    return (sc is None or float(sc) >= CLEAR_SCORE) and (ag is None or float(ag) >= CLEAR_AGREEMENT)


def _good_heard(w: Word, lex: Lexicon) -> bool:
    c = core(w.text)
    return float(w.prob) >= CLEAR_PROB and (is_real(c, lex) or is_name(c))


def _from_transcript(c: Cap, ops, heard: list[Word], lex: Lexicon, rep: Report) -> None:
    """Rule 5, competitor mode: a garbled screen reading -- not a real word, not a name, and not read clearly (or not
    a possible spelling at all: "t1o") -- takes the word the transcript clearly heard there ("We wre" -> "We're"),
    listed; a clearly read word is never changed."""
    clear = _clearly_read(c)
    new: list[Tok] = []
    done = []
    for tag, i1, i2, j1, j2 in ops:
        if tag in ("equal", "insert"):
            new += c.toks[i1:i2]
            continue
        if tag == "delete" or j2 <= j1:
            new += c.toks[i1:i2]
            continue
        block = c.toks[i1:i2]
        bad = [t for t in block if not is_real(core(t.text), lex) and not is_name(core(t.text))
               and (not clear or _artefact(t.text))]
        hw = heard[j1:j2]
        if not bad or not all(_good_heard(w, lex) for w in hw):
            new += block
            continue
        said = " ".join(t.text for t in block)
        whole = max(((x, y) for x in range(len(hw)) for y in range(x + 1, len(hw) + 1)),
                    key=lambda xy: (round(_alike(said, " ".join(w.text for w in hw[xy[0]:xy[1]])), 2),
                                    -abs(xy[1] - xy[0] - len(block)), -(xy[1] - xy[0])))
        sub = hw[whole[0]:whole[1]]
        alike = _alike(said, " ".join(w.text for w in sub))
        if alike >= (SIMILAR if len(bad) == len(block) else SAME_WORDS):
            # the same words garbled (spacing, an apostrophe, letters misread): the words heard
            new += [Tok(w.text, w.raw or w.text, w) for w in sub]
            done.append((said, " ".join(w.text for w in sub)))
        elif i2 - i1 == j2 - j1:                     # one for one: only the garbled tokens change
            for t, w in zip(block, hw):
                if t in bad and _alike(t.text, w.text) >= SIMILAR:
                    new.append(Tok(w.text, w.raw or w.text, w))
                    done.append((t.text, w.text))
                else:
                    new.append(t)
        else:
            new += block
    if done:
        c.toks = new
        rep.change(c, 5)
        rep.notes["from_transcript"] += 1
        for was, now in done:
            rep.row(5, "changed", c.start, c.end, c.text, f"'{was}' -> '{now}': the screen reading is not a real word "
                                                          "and was not read clearly; the transcript heard it clearly")


def _noise(c: Cap) -> bool:
    """A screen reading with nothing of two or more letters in it ("1", "V", "1i", "i 1"): maybe not a caption."""
    return not any(len(_letters(core(t.text))) >= 2 for t in c.toks)


def _wordlike(t: Tok, lex: Lexicon) -> bool:
    x = core(t.text)
    return bool(_letters(x)) and (is_real(x, lex) or is_name(x))


# ---------------------------------------------------------------------------------------------
# The fixes
# ---------------------------------------------------------------------------------------------

def _sentence_cuts(c: Cap) -> tuple[list[int], list[int]]:
    """(token indices a new caption starts at for rule 1 -- the text's own ? / ! / . --, for rule 2 -- a sentence end
    the transcript heard where the text shows none)."""
    r1, r2 = [], []
    for k in range(len(c.toks) - 1):
        t, nxt = c.toks[k], c.toks[k + 1]
        if sentence_end(t.raw) or sentence_end(t.text):
            r1.append(k + 1)
        elif t.word is not None and nxt.word is not t.word and sentence_end(t.word.raw):
            r2.append(k + 1)
    return r1, r2


def _best_split(toks: Sequence[Tok]) -> list[int] | None:
    """Rule 6: the fewest pieces of at most 20 characters and 4 words, fewest weak endings, then the most even."""
    n = len(toks)
    for parts in range(2, n + 1):
        best = None
        for cuts in itertools.combinations(range(1, n), parts - 1):
            bounds = (0,) + cuts + (n,)
            pieces = [toks[a:b] for a, b in zip(bounds, bounds[1:])]
            lens = [len(" ".join(t.text for t in p)) for p in pieces]
            if any(ln > MAX_CHARS or len(p) > MAX_WORDS for ln, p in zip(lens, pieces)):
                continue
            weak = sum(1 for p in pieces[:-1] if len(p) > 1 and is_weak(p[-1].text))
            score = (weak, max(lens) - min(lens))
            if best is None or score < best[0]:
                best = (score, list(cuts))
        if best is not None:
            return best[1]
    return None


def _too_long(c: Cap) -> bool:
    if not c.spoken:
        return len(c.text) > HARD_CAP
    return len(c.text) > MAX_CHARS or len(c.toks) > MAX_SPOKEN_WORDS


def _bonded(toks: Sequence[Tok]) -> bool:
    """The last of these tokens is kept together with the one before it ("didn't get it": a negation, its verb, "it";
    captions.compute_bonds)."""
    ws = [Word(t.text, 0.1 * i, 0.1 * i + 0.1, 1.0, t.raw) for i, t in enumerate(toks)]
    return len(ws) > 1 and bool(compute_bonds(ws)[-1])


def weak_reason(caps: Sequence[Cap], i: int, fps: Fraction, mode: str) -> str | None:
    """Why caption i keeps its weak last word, or None when the word can move to the front of the next caption."""
    c = caps[i]
    if not c.spoken or len(c.toks) < 2 or not is_weak(c.toks[-1].text):
        return "not a weak ending"
    last = c.toks[-1]
    nxt = caps[i + 1] if i + 1 < len(caps) else None
    if nxt is None:
        return "last caption"
    if not nxt.spoken:
        return "a silence follows"
    if sentence_end(last.raw) or (last.word is not None and sentence_end(last.word.raw)):
        return "it ends a sentence"
    if mode == "competitor" and nxt.start - c.end > TOUCH_FRAMES:
        return "a gap follows (the competitor's timing is kept)"
    nw = nxt.toks[0].word
    if last.word is not None and nw is not None and nw.start - last.word.end > SILENCE_S:
        return "a silence follows"
    if len(nxt.toks) == 1 and is_interjection_word(norm(core(nxt.toks[0].text))):
        return "the next caption is an interjection"
    if norm(core(nxt.toks[0].text)) == norm(core(last.text)):
        return "the next word repeats it"
    if _bonded(c.toks[-3:]):
        return "kept together with the previous word"
    if c.gave:
        return "it already gave one weak word to the next caption"
    first = core(nxt.toks[0].text)
    if first[:1].isupper() and first != "I" and not first.startswith("I'") and lex_starts_sentence(first):
        return "the next caption starts a new sentence"
    if len(nxt.text) + 1 + len(last.text) > MAX_CHARS or len(nxt.toks) + 1 > MAX_SPOKEN_WORDS:
        return "the next caption would be too long"
    return None


def lex_starts_sentence(w: str) -> bool:
    """A capitalised ordinary word (That, You -- not a name like Peter): the next caption starts a sentence."""
    lex = lexicon()
    lw = w.lower()
    return lw in lex.lower and not any(f[:1].isupper() and not _letters(f).isupper() for f in lex.forms.get(lw, ()))


def _move_weak(caps: list[Cap], fps: Fraction, mode: str, rep: Report) -> None:
    """Rule 7: a caption's weak last word goes to the front of the next caption (at most one word per caption); the
    boundary moves to where that word is said."""
    for i in range(len(caps) - 1):
        if weak_reason(caps, i, fps, mode) is not None:
            continue
        c, nxt = caps[i], caps[i + 1]
        f = _split_frame(c, len(c.toks) - 1, fps, hi=c.end)
        if f is None:
            continue
        nxt.toks.insert(0, c.toks.pop())
        if f < c.end:                                 # the boundary moves to where the word is said
            c.end = nxt.start = f
        c.gave = True
        rep.change(c, 7)


def _sentence_start(caps: Sequence[Cap], i: int) -> bool:
    prev = next((caps[j] for j in range(i - 1, -1, -1) if caps[j].spoken and caps[j].toks), None)
    if prev is None:
        return True
    t = prev.toks[-1]
    return sentence_end(t.raw) or (t.word is not None and sentence_end(t.word.raw))


def _recase(caps: list[Cap], lex: Lexicon, rep: Report) -> None:
    """Rules 3 and 4 on every token (``*actions*`` too); in a caption -- or a video -- written in capitals a lone "A"
    too (not at a sentence start)."""
    letters = "".join(_letters(t.text) for c in caps if c.spoken for t in c.toks)
    all_caps = len(letters) >= 12 and sum(ch.isupper() for ch in letters) >= 0.9 * len(letters)
    for i, c in enumerate(caps):
        if c.mode == "placeholder":
            continue
        shouting = all_caps or any(len(_letters(core(t.text))) > 1 and _letters(core(t.text)).isupper()
                                   and not case_ok(core(t.text), lex) for t in c.toks)
        for k, t in enumerate(c.toks):
            if core(t.text) == "A" and (shouting or (t.word is not None and core(t.word.text) == "a")) \
                    and not (k == 0 and _sentence_start(caps, i)):
                t.text = t.text.replace("A", "a", 1)
                rep.change(c, 4)
                continue
            cw = core(t.text)
            if cw == "i" or cw.startswith("i'"):                 # "I" is always a capital
                t.text = t.text.replace(cw, "I" + cw[1:], 1)
                rep.change(c, 3)
                continue
            if not cw or is_number(cw) or case_ok(cw, lex):
                continue
            rule = 4 if _letters(cw).isupper() else 3
            new = fix_case(cw, t.word, k == 0 and _sentence_start(caps, i), lex)
            titles = [f for f in lex.forms.get(new.lower(), ()) if f[:1].isupper() and f[1:] == f[1:].lower()]
            if new.islower() and titles and t.word is None and any(
                    _name_only(core(c.toks[j].text), lex) for j in (k - 1, k + 1) if 0 <= j < len(c.toks)):
                new = titles[0]                           # PETER PARKER -> Peter Parker, not "peter Parker"
            if new != cw:
                t.text = t.text.replace(cw, new, 1) if cw in t.text else t.text.replace("’", "'").replace(cw, new, 1)
                rep.change(c, rule)


def _name_only(w: str, lex: Lexicon) -> bool:
    """A name the word list writes only with a capital (Parker), never as an ordinary word."""
    lw = w.lower()
    return lw not in lex.lower and any(f[:1].isupper() and not _letters(f).isupper() for f in lex.forms.get(lw, ()))


def _strip_stops(caps: list[Cap], rep: Report) -> None:
    """Text cleanup: full stops and commas off, not inside numbers (after the sentence splits used them)."""
    for c in caps:
        if c.mode == "placeholder":
            continue
        before = c.text
        out = []
        for t in c.toks:
            s = clean_text(t.text)
            if s:
                out.append(Tok(s, t.raw, t.word))
        c.toks = out
        if c.text != before:
            rep.notes["stops_commas"] += 1


# ---------------------------------------------------------------------------------------------
# The final check
# ---------------------------------------------------------------------------------------------

def _caps_in(caps: Sequence[Caption], words: Sequence[Word] | None, mode: str) -> list[Cap]:
    out = []
    for c in caps:
        info = dict(c.info or {})
        if c.mode == "placeholder" or c.text == PLACEHOLDER:
            out.append(Cap(c.start, c.end, [Tok(c.text, c.text)], "placeholder", info))
        elif c.mode != "competitor" and c.words and len(c.words) == len(c.text.split()):
            out.append(Cap(c.start, c.end, [Tok(t, w.raw or w.text, w) for t, w in zip(c.text.split(), c.words)],
                           c.mode, info, gave=bool(info.get("gave"))))
        else:
            out.append(Cap(c.start, c.end, [Tok(t, t) for t in " ".join(str(c.text).split()).split(" ") if t],
                           c.mode, info, gave=bool(info.get("gave"))))
    return out


def _caps_out(caps: Sequence[Cap]) -> list[Caption]:
    out = []
    for c in caps:
        ws: list[Word] = []
        for t in c.toks:
            if t.word is not None and (not ws or ws[-1] is not t.word):
                ws.append(t.word)
        info = dict(c.info)
        if c.changed:
            info["rules"] = sorted(c.changed)
        if c.gave:
            info["gave"] = True
        out.append(Caption(c.text, c.start, c.end, c.mode, ws, info))
    return out


def enforce(caps: Sequence[Caption], fps: Fraction, mode: str, words: Sequence[Word] | None = None,
            lex: Lexicon | None = None) -> tuple[list[Caption], dict]:
    """The final check and its fixes (module docstring). ``mode``: "voice" (back to back, rule 8) or "competitor"
    (the competitor's timing kept: its start / end / gaps; only the boundaries inside a caption it split, and a
    weak word moving between two touching captions, are re-timed). ``words``: the transcript on the same timeline
    (seconds), None when there is none. Returns (captions, report)."""
    fps = Fraction(fps)
    lex = lex or lexicon()
    rep = Report(fps, mode)
    cs = _caps_in(caps, words, mode)
    if mode == "competitor":
        kept: list[Cap] = []
        used: set[int] = set()
        for c in cs:
            if c.mode == "placeholder":
                kept.append(c)
                continue
            was = c.text
            _junk_cleanup(c, rep)
            a_s, b_s = c.start / float(fps), c.end / float(fps)
            heard = _heard_in(words, a_s, b_s, used) if words is not None else []
            flicker = (c.end - c.start) / float(fps) < FLICKER_S and not all(_wordlike(t, lex) for t in c.toks)
            if not c.toks or flicker or _noise(c):
                inside = [w for w in heard if _good_heard(w, lex) and a_s <= 0.5 * (w.start + w.end) < b_s]
                said = [norm(core(t.text)) for t in c.toks]
                if flicker:                    # too short to read, and garbled: never a caption
                    keep, why = False, "a flicker of a garbled reading"
                elif any(_wordlike(t, lex) for t in c.toks):     # a lone "I" / "a" read off the screen stays
                    keep, why = True, ""
                    if inside:
                        _align(c, inside)
                        used.update(id(t.word) for t in c.toks if t.word is not None)
                elif words is None:            # no transcript: digits, stray letters and marks are not a caption
                    keep, why = False, "no word in it"
                elif not inside:
                    keep, why = False, "no words heard there"
                elif c.toks and all(x in {norm(w.text) for w in inside} for x in said if x):
                    keep, why = True, ""
                    _align(c, inside)
                    used.update(id(t.word) for t in c.toks if t.word is not None)
                else:
                    c.toks = [Tok(w.text, w.raw or w.text, w) for w in inside]
                    used.update(id(w) for w in inside)
                    rep.change(c, 5)
                    rep.notes["from_transcript"] += 1
                    rep.row(5, "changed", c.start, c.end, c.text, f"the screen reading '{was}' is not words: written "
                                                                  "from the words heard while it is on screen")
                    keep = True
                if not keep:
                    rep.changed[5] += 1
                    rep.notes["noise_dropped"] += 1
                    rep.row(5, "changed", c.start, c.end, was or "(nothing)", f"screen noise, not a caption ({why})"
                                                                              " -- left out")
                    continue
                kept.append(c)
                continue
            if heard:
                ops = _align(c, heard)
                _from_transcript(c, ops, heard, lex, rep)
                used.update(id(t.word) for t in c.toks if t.word is not None)
            kept.append(c)
        cs = kept
    # rules 1 and 2: one sentence, one speaker per caption
    out: list[Cap] = []
    for c in cs:
        if not c.spoken or len(c.toks) < 2:
            out.append(c)
            continue
        r1, r2 = _sentence_cuts(c)
        if not r1 and not r2:
            out.append(c)
            continue
        pieces = [c]
        for rule, ks in ((1, r1), (2, r2)):
            nxt, off = [], 0
            for p in pieces:
                local = [k - off for k in ks if off < k < off + len(p.toks)]
                off += len(p.toks)
                nxt += _split(p, local, fps, rule, rep) if local else [p]
            pieces = nxt
        out += pieces
    cs = out
    _strip_stops(cs, rep)
    cs = [c for c in cs if c.toks]
    _recase(cs, lex, rep)
    # rule 6: length
    out = []
    for c in cs:
        if not _too_long(c):
            out.append(c)
            continue
        ks = _best_split(c.toks) if c.spoken else None
        pieces = _split(c, ks, fps, 6, rep) if ks else [c]
        if len(pieces) == 1:
            rep.flagged[6] += 1
            rep.row(6, "flagged", c.start, c.end, c.text, f"{len(c.text)} characters, {len(c.toks)} words: "
                                                          "cannot be split at a word")
        out += pieces
    cs = out
    # rule 7: weak last words
    _move_weak(cs, fps, mode, rep)
    for i, c in enumerate(cs):
        why = weak_reason(cs, i, fps, mode)
        if why not in (None, "not a weak ending"):
            t = c.toks[-1]
            rep.kept_weak.append({"time": (t.word.start if t.word is not None else c.start / float(fps)),
                                  "text": t.text, "reason": why, "caption": c.text,
                                  "start_tc": _tc(c.start, fps), "end_tc": _tc(c.end, fps)})
            rep.flagged[7] += 1
    # rule 8: no gaps (voice mode)
    if mode != "competitor":
        for a, b in zip(cs, cs[1:]):
            if a.end != b.start:
                a.end = b.start
                rep.change(a, 8)
    # rule 5: what is still not a word
    for c in cs:
        bad = [core(t.text) for t in c.toks if c.mode != "placeholder" and core(t.text)
               and not is_real(core(t.text), lex) and not is_name(core(t.text))]
        if bad:
            rep.flagged[5] += 1
            heard = " ".join(t.word.text for t in c.toks if t.word is not None)
            rep.row(5, "flagged", c.start, c.end, c.text, "not a word: " + ", ".join(f"'{b}'" for b in bad) +
                    (f" (heard: '{heard}')" if heard and mode == "competitor" else "") + " -- check the audio (a name, "
                    "acronym or deliberate spelling: add it to caption_allowlist.txt)")
    res = _caps_out(cs)
    left = check(res, fps, mode, lex)
    d = rep.to_dict()
    d["left"] = {r: len(v) for r, v in left.items()}
    d["left_rows"] = {r: v[:20] for r, v in left.items() if v}
    return res, d


def check(caps: Sequence[Caption], fps: Fraction, mode: str | None = None, lex: Lexicon | None = None,
          rules: Sequence[int] = tuple(RULES)) -> dict[int, list[str]]:
    """The eight checks on finished captions: {rule: [what breaks it]}. Rule 7 counts a weak last word only where it
    could move (weak_reason); rule 8 only in voice mode (the competitor's gaps are kept)."""
    lex = lex or lexicon()
    fps = Fraction(fps)
    out: dict[int, list[str]] = {r: [] for r in rules}
    cs = _caps_in(caps, None, mode or "voice")
    for c, orig in zip(cs, caps):                      # the transcript words the tokens stand for
        ws = list(orig.words or [])
        if ws and c.mode != "placeholder":
            if len(ws) == len(c.toks):
                for t, w in zip(c.toks, ws):
                    t.word = w
            else:
                c.info["_words"] = ws
    for i, (c, orig) in enumerate(zip(cs, caps)):
        tc = f"{_tc(c.start, fps)} '{c.text}'"
        if c.mode == "placeholder":
            continue
        if 1 in out and re.search(r"[?!.]\s+\S", c.text):
            out[1].append(tc)
        if 2 in out:
            ws = [t.word for t in c.toks if t.word is not None] or c.info.get("_words") or []
            if any(sentence_end(w.raw) for w in ws[:-1]):
                out[2].append(tc)
        cores = [core(t.text) for t in c.toks]
        if 3 in out and any(x and not is_number(x) and not _letters(x).isupper() and not case_ok(x, lex)
                            for x in cores):
            out[3].append(tc)
        if 4 in out and any(x and len(_letters(x)) > 1 and _letters(x).isupper() and not case_ok(x, lex)
                            for x in cores):
            out[4].append(tc)
        if 5 in out and any(x and not is_real(x, lex) and not is_name(x) for x in cores):
            out[5].append(tc)
        if 6 in out and _too_long(c):
            out[6].append(tc)
        if 7 in out and weak_reason(cs, i, fps, mode or "voice") is None:
            out[7].append(tc)
    if 8 in out and (mode or "voice") != "competitor":
        out[8] = [f"{_tc(a.start, fps)} '{a.text}' ends at {_tc(a.end, fps)}, the next starts at {_tc(b.start, fps)}"
                  for a, b in zip(caps, caps[1:]) if a.end != b.start]
    return out


def summary_line(rep: dict) -> str:
    """How many captions each rule changed or flagged, for the end summary."""
    ch, fl, notes = rep.get("changed") or {}, rep.get("flagged") or {}, rep.get("notes") or {}

    def n(d: dict, r: int) -> int:
        return int(d.get(r, d.get(str(r), 0)) or 0)
    verbs = {1: "split", 2: "split", 3: "recased", 4: "recased", 6: "split", 7: "moved", 8: "closed"}
    parts = []
    for r, name in RULES.items():
        if r == 8 and rep.get("mode") == "competitor":
            parts.append(f"8 {name}: the competitor's timing kept, gaps included")
            continue
        if r == 5:
            bits = []
            if notes.get("from_transcript"):
                bits.append(f"{notes['from_transcript']} from the transcript")
            if notes.get("noise_dropped"):
                bits.append(f"{notes['noise_dropped']} screen noise left out")
            other = n(ch, 5) - int(notes.get("from_transcript") or 0) - int(notes.get("noise_dropped") or 0)
            if other > 0:
                bits.append(f"{other} cleaned")
            bits.append(f"{n(fl, 5)} flagged")
            parts.append(f"5 {name}: " + ", ".join(bits))
            continue
        bits = [f"{n(ch, r)} {verbs[r]}"]
        if n(fl, r) or r == 7:
            bits.append(f"{n(fl, r)} {'kept (listed)' if r == 7 else 'flagged'}")
        parts.append(f"{r} {name}: " + ", ".join(bits))
    return "; ".join(parts)
