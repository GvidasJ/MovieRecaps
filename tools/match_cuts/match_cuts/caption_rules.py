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

from .captions import (HARD_CAP, INTERJECTION_RE, MAX_CHARS, MAX_WORDS, NAME_STOP, PAUSE_S, PLACEHOLDER, SILENCE_S,
                       WEAK, Caption, Word, _lonely, _short, clean_text, compute_bonds, cut_breaks, frame_ms, group_words,
                       is_action_text, is_weak, ms_tc, norm, sentence_end, to_frame)

RULES = {1: "one sentence", 2: "one speaker", 3: "casing inside a word", 4: "capitals", 5: "real words",
         6: "length", 7: "weak words", 8: "no gaps", 9: "kept together", 10: "video cuts"}
MAX_SPOKEN_WORDS = 5          # rule 6: spoken captions top out at 20 characters (MAX_CHARS) and 5 words
CLEAR_PROB = 0.6              # a transcript word heard at least this sure is "clearly heard"
CLEAR_SCORE = 0.9             # a screen reading (OCR) at least this sure ...
CLEAR_AGREEMENT = 0.6         # ... and agreed by at least this share of its frames is "clearly read"
SIMILAR = 0.5                 # a garbled reading and the word heard there: letters at least this alike ...
SAME_WORDS = 0.75             # ... a reading with a real word in it ("We wre" ~ "We're"): at least this alike
FLICKER_S = 0.15              # a garbled screen reading on screen less than this: a flicker of noise, not a caption
TOUCH_FRAMES = 2              # competitor captions this close (sequence frames) are back to back
ALIGN_TOL_S = 0.15            # transcript words up to this far outside a caption may belong to it
PAUSE_CAP_S = 0.5             # capitals: the first word after a pause in speech longer than this

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
    """The entries of caption_allowlist.txt: one word or phrase per line ("Bronx High School of Science"; commas
    also separate entries), ``#`` notes; the defaults when it is missing."""
    p = Path(path) if path else ALLOWLIST_FILE
    if not p.is_file():
        return list(DEFAULT_ALLOW)
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0]
        out += [" ".join(w.split()) for w in re.split(r"[,;]", line) if w.strip()]
    return out


def glossary_capitals(path: str | Path) -> list[str]:
    """The glossary's capitals-only corrections (``learn``: "Vr -> VR", one word, the same letters): the written
    forms. The sound cannot tell capitals apart, so these are text rules like the allowlist's (the glossary's other
    entries replace heard words only where the audio fits: captions.py)."""
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        heard, arrow, written = line.split("#", 1)[0].partition("->")
        heard, written = heard.strip(), written.strip()
        if arrow and heard and written and " " not in written and heard != written and heard.lower() == written.lower():
            out.append(written)
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
        words = [w for e in read_allowlist(allowlist) for w in e.split()]     # a phrase's words as written too
        allow = {w.lower().replace("’", "'"): w for w in words}
        for w in glossary_capitals(Path(key).with_name("caption_glossary.txt")):
            allow.setdefault(w.lower(), w)              # your capitals from the glossary ("Vr -> VR"): as written
        _LEX[key] = Lexicon(base.lower, base.forms, allow, key)
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


_CONTRACTED = frozenset({"s", "re", "ll", "ve", "d", "t", "m"})


def _title(w: str) -> str:
    """Spider-Man, O'Brien: each part with a capital -- not a contraction's ending (They're, Vanisher's)."""
    parts = re.split(r"([-'])", w)
    return "".join(p.lower() if k >= 2 and parts[k - 1] == "'" and p.lower() in _CONTRACTED
                   else p[:1].upper() + p[1:].lower() for k, p in enumerate(parts))


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
    sent: bool = False           # the screen started a sentence here (a capital on an ordinary word, mixed case)


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
        self.stutters: list[dict] = []       # a short word said twice in a row inside one caption, kept once

    def change(self, c: Cap, rule: int) -> None:
        if rule not in c.changed:
            c.changed.add(rule)
            self.changed[rule] += 1

    def row(self, rule: int, kind: str, a: int, b: int, text: str, detail: str) -> None:
        self.rows.append({"rule": rule, "kind": kind, "start": a, "end": b, "start_tc": _tc(a, self.fps),
                          "end_tc": _tc(b, self.fps), "text": text, "detail": detail})

    def to_dict(self) -> dict:
        return {"mode": self.mode, "changed": dict(self.changed), "flagged": dict(self.flagged), "rows": self.rows,
                "notes": dict(self.notes), "kept_weak": self.kept_weak, "stutters": self.stutters}


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


def _weak_tail(toks: Sequence[Tok]) -> int:
    """Where the words kept together with the last token start (captions.compute_bonds: "of the", "it was", "didn't
    get it"): a weak last word moves to the next caption with them."""
    ws = [Word(t.text, 0.1 * i, 0.1 * i + 0.1, 1.0, t.raw) for i, t in enumerate(toks)]
    bonds = compute_bonds(ws)
    j = len(toks) - 1
    while j > 0 and bonds[j - 1]:
        j -= 1
    return j


def weak_reason(caps: Sequence[Cap], i: int, fps: Fraction, mode: str, cuts: Sequence[int] = ()) -> str | None:
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
    tail = _weak_tail(c.toks)
    if tail == 0:
        return "kept together with the previous word"
    if any(c.start < f <= nxt.start for f in cuts):
        return "a video cut follows"
    if c.gave:
        return "it already gave one weak word to the next caption"
    if nxt.toks[0].sent:
        return "the next caption starts a new sentence"
    moved = " ".join(t.text for t in c.toks[tail:])
    if len(nxt.text) + 1 + len(moved) > MAX_CHARS or len(nxt.toks) + len(c.toks) - tail > MAX_SPOKEN_WORDS:
        return "the next caption would be too long"
    return None


def lex_starts_sentence(w: str) -> bool:
    """A capitalised ordinary word (That, You -- not a name like Peter): the next caption starts a sentence."""
    lex = lexicon()
    lw = w.lower()
    return lw in lex.lower and not any(f[:1].isupper() and not _letters(f).isupper() for f in lex.forms.get(lw, ()))


def _move_weak(caps: list[Cap], fps: Fraction, mode: str, rep: Report, cuts: Sequence[int] = ()) -> None:
    """Rule 7: a caption's weak last word goes to the front of the next caption with the words kept together with it
    (at most once per caption); the boundary moves to where they are said."""
    for i in range(len(caps) - 1):
        if weak_reason(caps, i, fps, mode, cuts) is not None:
            continue
        c, nxt = caps[i], caps[i + 1]
        tail = _weak_tail(c.toks)
        f = _split_frame(c, tail, fps, hi=c.end)
        if f is None:
            continue
        nxt.toks[:0] = c.toks[tail:]
        del c.toks[tail:]
        if f < c.end:                                 # the boundary moves to where the word is said
            c.end = nxt.start = f
        c.gave = True
        rep.change(c, 7)


def _prev_spoken(caps: Sequence[Cap], i: int) -> Cap | None:
    return next((caps[j] for j in range(i - 1, -1, -1) if caps[j].spoken and caps[j].toks), None)


def _after_pause(caps: Sequence[Cap], i: int, k: int, pos: dict[int, int], words: Sequence[Word], fps: Fraction
                 ) -> bool:
    """Token k of caption i comes after a real pause in speech (> PAUSE_CAP_S): the transcript's gap before its word,
    else the gap before its caption (a first token), or nothing said before it."""
    c, t = caps[i], caps[i].toks[k]
    if t.word is not None and id(t.word) in pos:
        if k > 0 and c.toks[k - 1].word is t.word:
            return False
        j = pos[id(t.word)]
        return j == 0 or t.word.start - words[j - 1].end > PAUSE_CAP_S
    if k > 0:
        return False
    prev = next((caps[j] for j in range(i - 1, -1, -1) if caps[j].toks), None)
    return prev is None or not prev.spoken or (c.start - prev.end) / float(fps) > PAUSE_CAP_S


def _name_signal(c: Cap, k: int, pos: dict[int, int], words: Sequence[Word], all_caps: bool, lex: Lexicon
                 ) -> str | None:
    """The form a name takes when token k is one -- else None. A name: on the allowlist, a word the word list only
    writes with a capital (Bronx, Parker), or written with a capital (by the transcript, or by a competitor that
    does not write in capitals) where that is no sentence start (School, Science, Marvel in mid-sentence), an
    unknown word (Keanu, Spider-Man), or a word the list also writes as a name (Peter); never a word like "and",
    "the", "you", "so"."""
    t = c.toks[k]
    cw = core(t.text).replace("’", "'")
    lw = cw.lower()
    hc = core(t.word.text).replace("’", "'") if t.word is not None else ""
    heard_cap = hc.lower() == lw and hc[:1].isupper()
    wrote_cap = not all_caps and cw[:1].isupper()
    titles = sorted(f for f in lex.forms.get(lw, ()) if f[:1].isupper() and not _letters(f).isupper())
    form = hc if heard_cap and case_ok(hc, lex) else (titles[0] if titles else _title(cw))
    if _name_only(cw, lex):
        return form
    if not (heard_cap or wrote_cap) or lw in NAME_STOP or lw in WEAK or is_interjection_word(lw):
        return None
    if not lex.known(lw) and not any(lex.known(x) for x in _stretched(lw)):
        return form                                                      # Keanu, Firestar, Spider-Man
    if heard_cap and id(t.word) in pos:
        j = pos[id(t.word)]
        if j > 0 and not sentence_end(words[j - 1].raw or words[j - 1].text):
            return form                                                  # a capital in mid-sentence
    if wrote_cap and k > 0 and not sentence_end(c.toks[k - 1].raw):
        return form
    return form if titles else None


def _recase(caps: list[Cap], words: Sequence[Word] | None, lex: Lexicon, rep: Report, fps: Fraction) -> None:
    """Capitals as spoken (rules 3 and 4): "I", names and acronyms start with a capital; every other word is lower
    case -- never a capital only because the transcript or the screen starts a new sentence there (WAS -> was,
    yoU -> you, "joke. And Marvel" -> "and Marvel"); _pause_capitals then gives the first word of a caption after a
    real pause its capital. A capitalised word next to a name is part of it ("Bronx School"). ``*actions*`` too."""
    letters = "".join(_letters(t.text) for c in caps if c.spoken for t in c.toks)
    all_caps = len(letters) >= 12 and sum(ch.isupper() for ch in letters) >= 0.9 * len(letters)
    words = list(words or [])
    pos = {id(w): j for j, w in enumerate(words)}
    for i, c in enumerate(caps):
        follow = c.info.get("style") == "follow"        # the competitor's own captions, followed
        if c.mode == "placeholder" or (follow and is_action_text(c.text)):
            continue
        cores = [core(t.text).replace("’", "'") for t in c.toks]
        if c.toks and not all_caps and cores[0][:1].isupper() and cores[0] != "I" and lex_starts_sentence(cores[0]):
            c.toks[0].sent = True                       # the screen started a sentence here
        names = [(_name_signal(c, k, pos, words, all_caps, lex) if cw and _letters(cw) else None)
                 for k, cw in enumerate(cores)]
        for k, cw in enumerate(cores):                  # a capitalised word next to a name: part of it
            t = c.toks[k]
            capital = ((not all_caps and cw[:1].isupper()) or (t.word is not None and core(t.word.text)[:1].isupper())
                       or (all_caps and t.word is None and any(f[:1].isupper() and not _letters(f).isupper()
                                                               for f in lex.forms.get(cw.lower(), ()))))
            if names[k] is None and capital and cw.lower() not in NAME_STOP and cw.lower() not in WEAK and any(
                    names[j] is not None for j in (k - 1, k + 1) if 0 <= j < len(cores)):
                names[k] = _title(cw) if _letters(cw).isupper() else cw
        for k, (t, cw) in enumerate(zip(c.toks, cores)):
            if not cw or not _letters(cw) or is_number(cw):
                continue
            lw = cw.lower()
            if lw == "i" or lw.startswith("i'"):
                new = "I" + lw[1:]
            elif lw in lex.allow and not (cw == lw and lex.known(lw)):
                new = lex.allow[lw]
            elif len(_letters(cw)) > 1 and _letters(cw).isupper() and is_acronym(cw, lex):
                new = cw
            elif names[k] is not None:
                new = names[k]
            else:
                mixed = sorted(f for f in lex.forms.get(lw, ()) if not _letters(f).isupper() and not f[:1].isupper())
                new = mixed[0] if mixed else lw                                  # iPhone-like forms the list knows
                if k == 0 and follow and cw[:1].isupper() and cw[1:] == cw[1:].lower():
                    new = cw                     # the competitor's capital on its first word: the user keeps it
            if new != cw:
                rule = 3 if not _letters(cw).isupper() and not case_ok(cw, lex) else 4
                t.text = t.text.replace(cw, new, 1) if cw in t.text else t.text.replace("’", "'").replace(cw, new, 1)
                rep.change(c, rule)


def _pause_capitals(caps: list[Cap], words: Sequence[Word] | None, rep: Report, fps: Fraction) -> None:
    """The first word of a caption after a real pause in speech (> PAUSE_CAP_S: the transcript's gap before it, else
    the gap before the caption) starts with a capital -- the only capital besides "I", names and acronyms."""
    words = list(words or [])
    pos = {id(w): j for j, w in enumerate(words)}
    for i, c in enumerate(caps):
        if not c.spoken or not c.toks:
            continue
        t = c.toks[0]
        cw = core(t.text)
        if cw[:1].islower() and _after_pause(caps, i, 0, pos, words, fps):
            t.text = t.text.replace(cw, cw[:1].upper() + cw[1:], 1)
            c.info["pause_capital"] = True              # not a name: rule 5 reads it in lower case
            rep.change(c, 4)


def _name_only(w: str, lex: Lexicon) -> bool:
    """A name the word list writes only with a capital (Parker), never as an ordinary word."""
    lw = w.lower()
    return lw not in lex.lower and any(f[:1].isupper() and not _letters(f).isupper() for f in lex.forms.get(lw, ()))


def _run_words(run: Sequence[Cap], fps: Fraction) -> tuple[list[Word], list[tuple[int, int]]]:
    """The words of consecutive competitor captions on the competitor's timing: a caption's first word from the frame
    it appeared, the others at their transcript time (else shared out by characters); each until the next word, the
    last until the caption ends. The sentence ends: the screen's punctuation, the transcript's, and a capital the
    screen starts a sentence with. Returns (words, [(caption, token)])."""
    words: list[Word] = []
    where: list[tuple[int, int]] = []
    for ci, c in enumerate(run):
        a, b = c.start / float(fps), c.end / float(fps)
        total = max(1, len(c.text))
        starts: list[float] = []
        for k, t in enumerate(c.toks):
            if k == 0:
                starts.append(a)
                continue
            f = t.word.start if t.word is not None and t.word is not c.toks[k - 1].word else None
            if f is None or not starts[-1] < f < b:
                f = a + (b - a) * (len(" ".join(x.text for x in c.toks[:k])) + 1) / total
            starts.append(max(f, starts[-1] + 1e-3))
        for k, t in enumerate(c.toks):
            last_of_word = t.word is not None and (k + 1 == len(c.toks) or c.toks[k + 1].word is not t.word)
            raw = t.raw if sentence_end(t.raw) or not last_of_word else (t.word.raw or t.raw)
            nxt = c.toks[k + 1] if k + 1 < len(c.toks) else (run[ci + 1].toks[0] if ci + 1 < len(run) else None)
            if nxt is not None and nxt.sent and not sentence_end(raw):
                raw = raw + "."                          # the screen starts a new sentence after it
            elif len(c.toks) == 1 and is_interjection_word(norm(core(t.text))) and not re.search(r"[,.!?]$", raw):
                raw = raw + ","                          # an interjection the competitor shows alone stays alone
            words.append(Word(t.text, starts[k], starts[k + 1] if k + 1 < len(c.toks) else b,
                              float(t.word.prob) if t.word is not None else 1.0, raw))
            where.append((ci, k))
    return words, where


def _regroup(cs: list[Cap], fps: Fraction, rep: Report, cuts: Sequence[int] = ()) -> list[Cap]:
    """Competitor mode: the competitor's captions as one stream of words, regrouped by the voice-mode walk
    (captions.group_words: 4 words / 20 characters, keep-together pairs, no lone weak word, weak last words moved, a
    new caption after a pause or a sentence end) on the competitor's timing -- each caption starts on the frame its
    first word appeared on their screen. The competitor's own captions of 2+ words that pass these rules stay as
    they are (a lone weak word just before one may join it). A video cut of the edit (``cuts``) and a pause the
    transcript hears (> 0.25 s) start a new caption; one starting on a cut starts exactly on it."""
    out: list[Cap] = []
    run: list[Cap] = []
    for c in list(cs) + [None]:
        if c is not None and c.spoken and c.toks:
            run.append(c)
            continue
        if run:
            out += _regroup_run(run, fps, rep, cuts)
            run = []
        if c is not None:
            out.append(c)
    return out


def _speech_pauses(run: Sequence[Cap], where: Sequence[tuple[int, int]]) -> set[int]:
    """Word indices the transcript hears a pause (> 0.25 s) before: the speaker split the phrase there."""
    out = set()
    toks = [run[ci].toks[k] for ci, k in where]
    for j in range(1, len(toks)):
        a, b = toks[j - 1].word, toks[j].word
        if a is not None and b is not None and a is not b and b.start - a.end > PAUSE_S:
            out.add(j)
    return out


def _regroup_run(run: list[Cap], fps: Fraction, rep: Report, cuts: Sequence[int] = ()) -> list[Cap]:
    from .captions import _fits
    words, where = _run_words(run, fps)
    bonds = compute_bonds(words, adjectives=False)     # the competitor's own boundary wins over "adjective + noun"
    at_cut = cut_breaks(words, [f for f in cuts if run[0].start < f < run[-1].end], fps, bonds)
    breaks = set(at_cut) | _speech_pauses(run, where)
    for j in breaks:
        bonds[j - 1] = False
    first = {}
    for j, (ci, k) in enumerate(where):
        first.setdefault(ci, j)
    fixed = []
    for ci, c in enumerate(run):
        a = first[ci]
        b = a + len(c.toks)
        if b - a < 2 or not _fits(words, range(a, b)):
            continue
        if is_weak(words[b - 1].text) and not sentence_end(words[b - 1].raw):
            continue                                     # ends on a weak word
        if any(sentence_end(words[j].raw) for j in range(a, b - 1)):
            continue
        if (a > 0 and bonds[a - 1]) or (b < len(words) and bonds[b - 1]):
            continue                                     # it splits a pair kept together
        if any(a < j < b for j in breaks):
            continue                                     # a cut or a pause inside it
        fixed.append((a, b))
    gave: set[int] = set()
    groups = _short(words, _join_singles(words, group_words(words, None, gave, fixed, cuts=at_cut,
                                                            pauses=breaks - set(at_cut)), breaks, set(at_cut)), bonds)
    caps: list[Cap] = []
    for g in groups:
        (c0, k0), (c1, k1) = where[g[0]], where[g[-1]]
        start = at_cut.get(g[0], run[c0].start if k0 == 0 else to_frame(words[g[0]].start, fps))
        if g[0] in at_cut and caps and caps[-1].end is not None and caps[-1].end >= run[c0].start:
            caps[-1].end = start                         # back to back: the caption changes exactly on the cut
        end = run[c1].end if k1 == len(run[c1].toks) - 1 else None
        info = dict(run[c0].info)
        ch = set().union(*(run[c].changed for c in {where[j][0] for j in g}))
        cap = Cap(start, end, [run[where[j][0]].toks[where[j][1]] for j in g], "competitor", info, ch,
                  gave=g[0] in gave)
        if not (k0 == 0 and k1 == len(run[c1].toks) - 1 and c0 == c1):
            rep.change(cap, 9)                           # not the competitor's own caption
        caps.append(cap)
    for c in caps:
        _merge_quotes(c)
    for i, c in enumerate(caps):
        if c.end is None:
            c.end = caps[i + 1].start if i + 1 < len(caps) else run[-1].end
    for i in range(1, len(caps)):                        # starts strictly increasing, at least a frame each
        caps[i].start = max(caps[i].start, caps[i - 1].start + 1)
        caps[i - 1].end = min(caps[i - 1].end, caps[i].start) if caps[i - 1].end > caps[i].start else caps[i - 1].end
    own = {tuple(map(id, c.toks)) for c in caps}         # captions still exactly the competitor's
    rep.notes["regrouped"] = rep.notes.get("regrouped", 0) + sum(1 for c in run if tuple(map(id, c.toks)) not in own)
    return [c for c in caps if c.end > c.start]


def _lone_target(cs: Sequence[Cap], i: int, fps: Fraction, cuts: Sequence[int] = ()) -> str | None:
    """Where caption i -- a single weak word or preposition -- can join: "next", "prev", or None (nowhere: a sentence
    end, a pause or a video cut in the way; or not a lone weak word)."""
    c = cs[i]
    if not (c.spoken and len(c.toks) == 1 and _lonely(c.toks[0].text)):
        return None
    t = c.toks[0]
    nxt = cs[i + 1] if i + 1 < len(cs) else None
    prv = cs[i - 1] if i > 0 else None
    gap = int(round(PAUSE_S * float(fps)))

    def fits(toks):
        return len(" ".join(x.text for x in toks)) <= MAX_CHARS and len(toks) <= MAX_SPOKEN_WORDS
    if (nxt is not None and nxt.spoken and nxt.start - c.end <= gap and not sentence_end(t.raw)
            and not (t.word is not None and sentence_end(t.word.raw)) and fits([t] + nxt.toks)
            and not any(c.start < f < nxt.end for f in cuts)):
        return "next"
    pt = prv.toks[-1] if prv is not None and prv.toks else None
    if (pt is not None and prv.spoken and c.start - prv.end <= gap and not sentence_end(pt.raw)
            and not (pt.word is not None and sentence_end(pt.word.raw)) and fits(prv.toks + [t])
            and not any(prv.start < f <= c.end + gap for f in cuts)):        # right before a cut: it stands alone
        return "prev"
    return None


def _join_singles(words: Sequence[Word], groups: list[list[int]], breaks: set[int] = frozenset(),
                  cuts: set[int] = frozenset()) -> list[list[int]]:
    """Competitor mode: a one-word caption left between two others ("bring" | "me up") joins a neighbour when the
    two fit one caption with no pause or sentence end between them: the next one, or the previous one when a
    comma or a sentence end follows the word ("what dya" | "think," -> "what dya think"). Interjections and a word
    said again stay alone; never across a video cut or a pause (``breaks``), and a weak word or preposition right
    before a cut (``cuts``: word indices a cut falls before) stands alone ("for" | cut | "genius kids")."""
    from .captions import _fits, _repeat, standalone_interjections
    alone = standalone_interjections(words)
    i = 0
    while i < len(groups):
        g = groups[i]
        w = g[0]
        if len(g) != 1 or alone[w] or (w + 1 in cuts and _lonely(words[w].text)):
            i += 1
            continue
        raw = (words[w].raw or words[w].text).strip().rstrip("\"”’'")
        ends = sentence_end(raw) or raw.endswith((",", ";", ":"))

        def ok(a: list[int], b: list[int]) -> bool:
            x, y = a[-1], b[0]
            return (_fits(words, a + b) and words[y].start - words[x].end <= PAUSE_S and not alone[x] and not alone[y]
                    and y not in breaks and not sentence_end(words[x].raw or words[x].text) and not _repeat(words, x, y))
        if not ends and i + 1 < len(groups) and ok(g, groups[i + 1]):
            groups[i:i + 2] = [g + groups[i + 1]]
        elif i > 0 and ok(groups[i - 1], g):
            groups[i - 1:i + 1] = [groups[i - 1] + g]
            i -= 1
        i += 1
    return groups


def _merge_quotes(c: Cap) -> None:
    """Quoted words shown one by one, now one caption: one pair of quotes ('“so” “dude”' -> '“so dude”')."""
    for k in range(len(c.toks) - 1):
        a, b = c.toks[k], c.toks[k + 1]
        if a.text[-1:] in "”\"" and b.text[:1] in "“\"" and len(a.text) > 1 and len(b.text) > 1:
            a.text, b.text = a.text[:-1], b.text[1:]


def _join_lone(cs: list[Cap], fps: Fraction, rep: Report, cuts: Sequence[int] = ()) -> list[Cap]:
    """Rule 7: a caption of a single weak word ("a", "I", "the") joins the caption after it -- or, when a sentence
    end, a pause or a too-long caption is in the way, the one before it; else it is listed."""
    i = 0
    while i < len(cs):
        c = cs[i]
        if not (c.spoken and len(c.toks) == 1 and _lonely(c.toks[0].text)):
            i += 1
            continue
        where = _lone_target(cs, i, fps, cuts)
        if where == "next":
            cs[i + 1].toks.insert(0, c.toks[0])
            cs[i + 1].start = c.start
            rep.change(cs[i + 1], 7)
            del cs[i]
            continue
        if where == "prev":
            cs[i - 1].toks.append(c.toks[0])
            cs[i - 1].end = c.end
            rep.change(cs[i - 1], 7)
            del cs[i]
            continue
        if any(c.end <= f <= c.end + int(round(PAUSE_S * float(fps))) for f in cuts):
            i += 1                                       # alone right before a video cut: allowed (rule A)
            continue
        rep.flagged[7] += 1
        rep.row(7, "flagged", c.start, c.end, c.text, "a lone weak word: nothing it can join (a pause or a sentence "
                                                      "end on both sides)")
        i += 1
    return cs


def _token_frames(c: Cap, fps: Fraction) -> list[int]:
    """The frame each token of caption c starts on: its transcript word's start when that lies inside the caption,
    else the caption's time shared out by characters."""
    out: list[int] = []
    total = max(1, len(c.text))
    for k, t in enumerate(c.toks):
        f = to_frame(t.word.start, fps) if t.word is not None and (k == 0 or c.toks[k - 1].word is not t.word) else None
        if f is None or not c.start <= f < c.end or (out and f <= out[-1]):
            before = len(" ".join(x.text for x in c.toks[:k])) + (1 if k else 0)
            f = c.start + int(round((c.end - c.start) * before / total))
        out.append(max(f, out[-1] + 1) if out else f)
    return out


def _cut_split(cs: list[Cap], cuts: Sequence[int], fps: Fraction, rep: Report) -> list[Cap]:
    """Rule 10 (A): a caption never runs across a video cut of the edit. A cut inside a caption splits it at the word
    boundary nearest the cut -- or, when the cut is nearer the caption's first word's start or its last word's end,
    moves that edge -- so the caption changes exactly on the cut (a placeholder is split in two). This beats the
    lone-weak-word rule: "I" or "for" may stand alone right before a cut."""
    out = list(cs)
    i = 0
    while i < len(out):
        c = out[i]
        inside = [f for f in cuts if c.start < f < c.end]
        if not inside:
            i += 1
            continue
        f = inside[0]
        rep.change(c, 10)
        if not c.spoken or not c.toks:
            out[i:i + 1] = [Cap(c.start, f, c.toks, c.mode, dict(c.info), set(c.changed)),
                            Cap(f, c.end, [Tok(t.text, t.raw, t.word) for t in c.toks], c.mode, dict(c.info),
                                set(c.changed))]
            continue
        ts = _token_frames(c, fps)
        last = c.toks[-1].word
        te = to_frame(last.end, fps) if last is not None and ts[-1] < to_frame(last.end, fps) <= c.end else c.end
        marks = [(0, ts[0])] + [(k, ts[k]) for k in range(1, len(ts))] + [(len(ts), te)]
        k = min(marks, key=lambda m: (abs(m[1] - f), -m[0] if m[1] > f else m[0]))[0]
        if 0 < k < len(c.toks):
            out[i:i + 1] = [Cap(c.start, f, c.toks[:k], c.mode, dict(c.info), set(c.changed), c.gave),
                            Cap(f, c.end, c.toks[k:], c.mode, dict(c.info), set(c.changed))]
        elif k == 0:                                     # the words start after the cut: the caption starts on it
            if i > 0 and out[i - 1].end == c.start and not any(c.start <= g < f for g in cuts):
                out[i - 1].end = f                       # (never across another cut: a clip with no speech)
            c.start = f
        else:                                            # the words end before the cut: the caption ends on it
            if i + 1 < len(out) and out[i + 1].start == c.end and not any(f < g <= c.end for g in cuts):
                out[i + 1].start = f
            c.end = f
    return out


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

def _cores5(c: Cap) -> list[str]:
    """The words as rule 5 reads them: a capital given after a pause is no sign of a name ("Dont" is "dont")."""
    out = [core(t.text) for t in c.toks]
    if out and c.info.get("pause_capital"):
        out[0] = out[0][:1].lower() + out[0][1:]
    return out


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
        if out[-1].toks and info.get("starts_sentence"):
            out[-1].toks[0].sent = True
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
        if c.toks and c.toks[0].sent:
            info["starts_sentence"] = True              # the screen started a sentence here
        out.append(Caption(c.text, c.start, c.end, c.mode, ws, info))
    return out


def enforce(caps: Sequence[Caption], fps: Fraction, mode: str, words: Sequence[Word] | None = None,
            lex: Lexicon | None = None, cuts: Sequence[int] = (), keep_groups: bool = False,
            open_gaps: Sequence[tuple[int, int]] = ()) -> tuple[list[Caption], dict]:
    """The final check and its fixes (module docstring). ``mode``: "voice" (back to back, rule 8) or "competitor"
    (the competitor's timing kept: its start / end / gaps; only the boundaries inside a caption it split, and a
    weak word moving between two touching captions, are re-timed). ``words``: the transcript on the same timeline
    (seconds), None when there is none. ``cuts``: the edit's video cuts (sequence frames: where one V1 clip gives way
    to the next) -- a caption never runs across one. ``keep_groups``: the captions were grouped in the user's style
    already (caption_style.py): no regrouping, no weak word moved, no lone word joined -- and back to back in every
    mode (rule 8), except across ``open_gaps`` (sequence frames: another video's stretches). Returns (captions,
    report)."""
    fps = Fraction(fps)
    cuts = sorted({int(f) for f in cuts})
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
    # rules 1 and 2: one sentence, one speaker per caption (not where the competitor's own caption breaks are
    # followed: the user keeps them, "Vanisher And")
    out: list[Cap] = []
    for c in cs:
        if not c.spoken or len(c.toks) < 2 or (keep_groups and mode == "competitor"):
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
    _recase(cs, words, lex, rep, fps)
    if mode == "competitor" and not keep_groups:
        cs = _regroup(cs, fps, rep, cuts)
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
    # rule 7: weak last words, lone weak words; rule 10: never across a video cut
    if not keep_groups:
        _move_weak(cs, fps, mode, rep, cuts)
        cs = _join_lone(cs, fps, rep, cuts)
    if not (keep_groups and mode == "competitor"):       # the competitor's own caption breaks, followed: kept as they are
        cs = _cut_split(cs, cuts, fps, rep)
    _collapse_stutters(cs, rep)                          # after the regrouping: two captions may have met
    _pause_capitals(cs, words, rep, fps)
    for i, c in enumerate(cs):
        why = weak_reason(cs, i, fps, mode, cuts) if not keep_groups else None
        if why not in (None, "not a weak ending"):
            t = c.toks[-1]
            rep.kept_weak.append({"time": (t.word.start if t.word is not None else c.start / float(fps)),
                                  "text": t.text, "reason": why, "caption": c.text,
                                  "start_tc": _tc(c.start, fps), "end_tc": _tc(c.end, fps)})
            rep.flagged[7] += 1
    # rule 8: no gaps, in every mode -- the user's captions are back to back: a caption stays until the next one
    # starts. The gaps of another video's stretches (``open_gaps``) stay. The earlier voice grouping (not
    # ``keep_groups``): a gap with a video cut in it closes on the cut (rule 10); with two or more, the clip between
    # the first and the last has no speech and stays uncaptioned
    silence = int(round(SILENCE_S * float(fps)))
    filled: list[Cap] = []
    for a, b in zip(cs, cs[1:] + [None]):
        filled.append(a)
        if b is None or a.end == b.start or any(x < b.start + 1 and a.end - 1 < y for x, y in open_gaps):
            continue
        if (keep_groups or mode == "competitor") and b.start - a.end > silence and a.mode != "placeholder"                 and b.mode != "placeholder":       # a silence: the action goes there, as the user writes it
            filled.append(Cap(a.end, b.start, [Tok(PLACEHOLDER, PLACEHOLDER)], "placeholder", {}))
            rep.change(a, 8)
            continue
        inside = [f for f in cuts if a.end <= f <= b.start] if not keep_groups and mode != "competitor" else []
        if b.start > a.end:
            b.info["gap_before"] = b.start - a.end            # the pause it closes (rule 9 still sees it)
        a.end, b.start = (inside[0], inside[-1]) if inside else (b.start, b.start)
        if a.end == b.start:
            rep.change(a, 8)
    cs = filled
    # rule 5: what is still not a word
    for c in cs:
        bad = [x for x in _cores5(c) if c.mode != "placeholder" and x and not is_real(x, lex) and not is_name(x)]
        if bad:
            rep.flagged[5] += 1
            heard = " ".join(t.word.text for t in c.toks if t.word is not None)
            rep.row(5, "flagged", c.start, c.end, c.text, "not a word: " + ", ".join(f"'{b}'" for b in bad) +
                    (f" (heard: '{heard}')" if heard and mode == "competitor" else "") + " -- check the audio (a name, "
                    "acronym or deliberate spelling: add it to caption_allowlist.txt)")
    res = _caps_out(cs)
    said = [c for c in res if c.mode != "placeholder" and not is_action_text(c.text)]
    rep.notes["cuts_in_speech"] = sum(1 for f in cuts if said and said[0].start < f < said[-1].end)
    left = check(res, fps, mode, lex, cuts=cuts)
    for a, b in _split_pairs(_caps_in(res, None, mode), fps, adjectives=mode != "competitor", cuts=cuts):
        rep.flagged[9] += 1                              # rule 9: what could not be kept together
        rep.row(9, "flagged", a.start, b.end, f"{a.text} | {b.text}", "a pair kept together is split here")
    d = rep.to_dict()
    d["left"] = {r: len(v) for r, v in left.items()}
    d["left_rows"] = {r: v[:20] for r, v in left.items() if v}
    return res, d


STUTTER_LETTERS = 3       # a stutter: a word of at most this many letters (or a weak word) said twice in a row


def _short_word(x: str) -> bool:
    return bool(x) and (len(_letters(x)) <= STUTTER_LETTERS or is_weak(x))


def _collapse_stutters(cs: list[Cap], rep: Report) -> None:
    """A short word said twice in a row inside one caption ("The the one that's", "I I", "to to") is kept once --
    the first, with the second's punctuation -- and listed (``rep.stutters``). A word repeated as separate captions
    ("no" | "no" | "no") is a deliberate repeat and stays."""
    for c in cs:
        if not c.spoken or len(c.toks) < 2:
            continue
        keep: list[Tok] = [c.toks[0]]
        was = c.text
        gone = []
        for t in c.toks[1:]:
            p = keep[-1]
            x = norm(core(t.text))
            if x and x == norm(core(p.text)) and _short_word(x):
                tail = t.text[len(t.text.rstrip("?!.,;:…")):]
                p.text = p.text.rstrip("?!.,;:…") + tail
                p.raw = p.raw.rstrip("?!.,;:…") + t.raw[len(t.raw.rstrip("?!.,;:…")):]
                gone.append(t)
                continue
            keep.append(t)
        if gone:
            c.toks = keep
            when = next((t.word.start for t in gone if t.word is not None), c.start / float(rep.fps))
            rep.stutters.append({"time": round(float(when), 3), "start_tc": _tc(c.start, rep.fps),
                                 "end_tc": _tc(c.end, rep.fps), "was": was, "now": c.text,
                                 "words": [t.text for t in gone]})


def check(caps: Sequence[Caption], fps: Fraction, mode: str | None = None, lex: Lexicon | None = None,
          rules: Sequence[int] = tuple(RULES), cuts: Sequence[int] = ()) -> dict[int, list[str]]:
    """The checks on finished captions: {rule: [what breaks it]}. Rule 7 counts a weak last word only where it could
    move (weak_reason) and a caption of a single weak word where it could join a neighbour; rule 8 only in voice
    mode (the competitor's gaps are kept); rule 9 a pair kept together (captions.compute_bonds) split between two
    captions where the caps allowed one caption and no video cut falls there; rule 10 a caption across a video cut
    of the edit (``cuts``, sequence frames)."""
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
        follow = orig.info.get("style") == "follow"     # the competitor's own caption breaks, kept
        if 1 in out and not follow and re.search(r"[?!.]\s+\S", c.text):
            out[1].append(tc)
        if 2 in out and not follow:
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
        if 5 in out and any(x and not is_real(x, lex) and not is_name(x) for x in _cores5(c)):
            out[5].append(tc)
        if 6 in out and _too_long(c):
            out[6].append(tc)
        if 7 in out and (weak_reason(cs, i, fps, mode or "voice", cuts) is None
                         or _lone_target(cs, i, fps, cuts) is not None):
            out[7].append(tc)
        if 10 in out and any(c.start < f < c.end for f in cuts):
            out[10].append(tc)
    if 9 in out:
        out[9] = [f"{_tc(a.start, fps)} '{a.text}' | '{b.text}'"
                  for a, b in _split_pairs(cs, fps, adjectives=(mode or "voice") != "competitor", cuts=cuts)]
    if 8 in out and (mode or "voice") != "competitor":
        cut = set(cuts)                                  # a clip with no speech between two cuts stays uncaptioned
        out[8] = [f"{_tc(a.start, fps)} '{a.text}' ends at {_tc(a.end, fps)}, the next starts at {_tc(b.start, fps)}"
                  for a, b in zip(caps, caps[1:]) if a.end != b.start and not (a.end in cut and b.start in cut)]
    return out


def _split_pairs(cs: Sequence[Cap], fps: Fraction, adjectives: bool = True, cuts: Sequence[int] = ()
                 ) -> list[tuple[Cap, Cap]]:
    """Rule 9: boundaries between two spoken captions (no pause between them) that split a pair kept together, where
    the words bonded across the boundary would fit one caption (competitor mode: its own boundary wins over
    "adjective + noun", ``adjectives`` False)."""
    from .captions import _fits
    out = []
    gap = int(round(PAUSE_S * float(fps)))
    for a, b in zip(cs, cs[1:]):
        if not (a.spoken and b.spoken and a.toks and b.toks) or                 max(b.start - a.end, int(b.info.get("gap_before") or 0)) > gap:
            continue
        if any(a.end - gap <= f <= b.start + gap for f in cuts):
            continue                                     # split on a video cut: rule 10 wins
        ws: list[Word] = []
        for c in (a, b):
            n = len(c.toks)
            for k, t in enumerate(c.toks):
                if t.word is not None and all(x.word is not None for x in c.toks):
                    s0, s1 = t.word.start, t.word.end
                else:
                    s0 = (c.start + (c.end - c.start) * k / n) / float(fps)
                    s1 = (c.start + (c.end - c.start) * (k + 1) / n) / float(fps)
                raw = t.raw if sentence_end(t.raw) or t.word is None else (t.word.raw or t.raw)
                ws.append(Word(t.text, s0, s1, 1.0, raw))
        bonds = compute_bonds(ws, adjectives=adjectives)
        j = len(a.toks) - 1
        if not bonds[j]:
            continue
        lo, hi = j, j + 1
        while lo > 0 and bonds[lo - 1]:
            lo -= 1
        while hi < len(ws) - 1 and bonds[hi]:
            hi += 1
        while lo > 0 and is_weak(ws[lo - 1].text):
            lo -= 1                                      # "And my" + "favorite thing": weak words go with it
        if _fits(ws, range(lo, hi + 1)):
            out.append((a, b))
    return out


def summary_line(rep: dict) -> str:
    """How many captions each rule changed or flagged, for the end summary."""
    ch, fl, notes = rep.get("changed") or {}, rep.get("flagged") or {}, rep.get("notes") or {}

    def n(d: dict, r: int) -> int:
        return int(d.get(r, d.get(str(r), 0)) or 0)
    verbs = {1: "split", 2: "split", 3: "recased", 4: "recased", 6: "split", 7: "moved or joined", 8: "closed",
             9: "regrouped", 10: "split on a cut"}
    parts = []
    for r, name in RULES.items():
        if r == 8 and rep.get("mode") == "competitor":
            parts.append(f"8 {name}: the competitor's timing kept, gaps included")
            continue
        if r == 9 and rep.get("mode") != "competitor":
            parts.append(f"9 {name}: {n(fl, 9)} flagged")
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
        if r == 10:
            k = int(notes.get("cuts_in_speech") or 0)
            across = int((rep.get("left") or {}).get(10, 0) or 0)
            bits = ([f"{k} in the speech, a caption starts on each" if not across else f"{k} in the speech"]
                    if k else ["none in the speech"])
            if n(ch, 10):
                bits.append(f"{n(ch, 10)} {verbs[10]}")
            if across:
                bits.append(f"{across} still inside a caption")
        if r == 9 and notes.get("regrouped"):
            bits = [f"{notes['regrouped']} of the competitor's captions regrouped into {n(ch, 9)}"]
        if n(fl, r) or r == 7:
            bits.append(f"{n(fl, r)} {'kept (listed)' if r == 7 else 'flagged'}")
        parts.append(f"{r} {name}: " + ", ".join(bits))
    return "; ".join(parts)
