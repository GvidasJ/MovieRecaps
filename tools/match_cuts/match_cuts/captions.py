"""Captions: ``<run folder>/2_captions.srt`` on the Premiere sequence (60.00 fps), by the rules of caption-generator-prompt.md.

Two modes, chosen per clip (``--captions auto|competitor|voice``, pipeline.stage_captions):

* **competitor** -- the competitor has burned-in captions: the competitor decides the timing (each caption from its
  first frame to its last, gaps included) and where captions split (caption_ocr.read_caption_spans reads the caption
  band on every frame); my rules decide how the text looks (caption_rules.py: a caption split at a sentence or
  speaker boundary, casing, no full stops / commas, a garbled reading replaced by the word the transcript clearly
  heard). A caption the OCR cannot read takes the words heard while it is on screen (listed in the report); speech
  the competitor left uncaptioned stays uncaptioned.
* **voice** -- only when the competitor has no captions: captions made from the voice-over (word timestamps of the
  CUT edit's audio, or ``--voiceover FILE``), the grouping walk, the weak-word fix, back-to-back timing and ``*...*``
  placeholders for silences.

Both end with caption_rules.enforce (the prompt's "Hard rules": fixed where mechanical, else listed), and write_srt
refuses a file that still breaks rules 1-4. This module holds the text rules, grouping, timing, SRT I/O and the
report data (no heavy imports); transcribe.py (faster-whisper) and caption_ocr.py (RapidOCR) hold the optional
engines.
"""
from __future__ import annotations

import bisect
import dataclasses
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

# ---- the caption style (caption-generator-prompt.md) ----------------------------------------------------------
MAX_WORDS = 4              # a caption already holding 4 words starts a new one (never more than 5)
MAX_CHARS = 20             # ... or one that would exceed 20 characters
CUT_TIE_S = 0.1            # a video cut about as near two word boundaries goes to the one outside a pair kept together
SHORT_CHARS = 16           # prefer short captions: one over 16 characters splits at a natural break (median 11)
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
# keep together (never split across captions): a determiner and the word after it, a pronoun and its verb, a
# preposition and its object
DETERMINERS = frozenset("a an the this my your".split())
PRONOUNS = frozenset("i you we they he she it".split())
PREPOSITIONS = frozenset("""of to in on at for with from by about into onto over under after before through without
    across behind between around""".split())
NOT_OBJECT = frozenset("and but or nor so because then if when while though although than as".split())
NAME_LINKS = frozenset("of the de da di von van".split())      # inside a name: "Bronx High School of Science"
THEN_STARTS = frozenset("and so but".split())               # "and then" / "so then" / "but then": a caption's start
CLAUSE_STARTS = frozenset("what when where why how who because if".split())   # (and "that" + a subject): a new clause
SET_PHRASES = frozenset({("no", "idea"), ("i", "know"), ("you", "know"), ("i", "mean"), ("of", "course"),
                         ("thank", "you")})                  # short set phrases, never split
AUX = frozenset("""am is are was were be been being have has had do does did can could will would shall should may
    might must gonna wanna gotta""".split())                   # helping verbs: "they would" | "bring me up"
# words that follow a noun rather than finish it ("a classroom next to", "the class again")
AFTER_NOUN = frozenset("""next too again now here there back up down out off away ago anymore already yet together
    first last right left even still just also only""".split())
_VERB_BASES = """be have do say get make go know take see come think look want give use find tell ask work seem feel
    try leave call need mean keep let begin help talk turn start show hear play run move like live believe hold bring
    happen write provide sit stand lose pay meet include continue set learn change lead understand watch follow stop
    create speak read allow add spend grow open walk win offer remember love consider appear buy wait serve die send
    expect build stay fall cut reach kill remain suggest raise pass sell require report decide pull guess hope wish
    finish put eat drink sleep drive ride fly swim sing dance laugh cry wear choose forget break catch throw hit hate
    wonder agree mind care check miss bet go pretend"""
_VERB_IRREGULAR = """am is are was were been being has had having does did done doing said got gotten made went gone
    knew known took taken saw seen came thought gave given told found felt left kept let began begun heard ran held
    brought wrote written sat stood lost paid met led understood spent grew grown won bought fell cut sold put ate
    eaten drank slept drove driven rode ridden flew flown swam sang laughed wore worn chose chosen forgot forgotten
    broke broken caught threw thrown hit shot can could will would shall should may might must 's 're 've 'd 'll
    gonna wanna gotta"""


def _verb_forms() -> frozenset[str]:
    """The bases with their -s, -ed and -ing forms, and the irregular forms."""
    out = set(_VERB_IRREGULAR.split())
    for b in _VERB_BASES.split():
        if b.endswith("y") and b[-2] not in "aeiou":
            out |= {b, b[:-1] + "ies", b[:-1] + "ied", b + "ing"}                # try, tries, tried, trying
            continue
        s3 = b + "es" if b.endswith(("s", "sh", "ch", "x", "z", "o")) else b + "s"
        ed = b + "d" if b.endswith("e") else b + "ed"
        ing = (b[:-1] if b.endswith("e") and not b.endswith("ee") else b) + "ing"
        out |= {b, s3, ed, ing}
        if re.fullmatch(r"[^aeiou]*[aeiou][^aeiouwxy]", b):                # stop -> stopped, sit -> sitting
            out |= {b + b[-1] + "ed", b + b[-1] + "ing"}
    return frozenset(out)


VERBS = _verb_forms()
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


ABBREVIATIONS = frozenset("mr mrs ms dr st jr sr vs etc prof mt".split())   # "Mr." does not end a sentence


def sentence_end(raw: str) -> bool:
    """A word that ends a sentence (hard rule 1): ``?``, ``!`` or a full stop -- not an abbreviation (Mr., a.m.,
    C.I.D.) and not an ellipsis, which trails off rather than ends."""
    t = str(raw or "").strip().rstrip("\"'”’)*")
    if t.endswith(("?", "!")):
        return True
    if not t.endswith(".") or t.endswith(("..", "…")):
        return False
    w = t[:-1].lower().lstrip("\"'“‘(*")
    return not (w in ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", w) is not None)


def _is_negation(n: str) -> bool:
    return n.endswith("n't") or n in NEGATIONS


def is_verb(n: str) -> bool:
    """A verb form (a rough list of the common ones: a pronoun is kept together with it -- "I know", "we went")."""
    return n in VERBS or _is_negation(n) or any(n.endswith(x) for x in ("'m", "'re", "'ve", "'ll", "'d"))


def _main_verb(n: str) -> bool:
    return is_verb(n) and n not in AUX and not _is_negation(n) and "'" not in n


def _phrase_bond(words: Sequence[Word], i: int, adjectives: bool = True) -> bool:
    """Words i and i+1 make a phrase never split: a determiner + the word after it ("a joke", "the school") and,
    after adjectives, the noun ("a pretty girl"), a pronoun + its verb ("I know"), a verb + its preposition
    ("talking about", "looking at"), a preposition + its object ("of Science"), "and then" / "so then" / "but
    then", a short set phrase ("no idea", "you know"), a link inside a name ("School of Science")."""
    a, b = words[i], words[i + 1]
    na, nb = norm(a.text), norm(b.text)
    if (na in THEN_STARTS and nb == "then") or (na, nb) in SET_PHRASES:
        return True
    if nb in NOT_OBJECT or is_interjection(b.text) or not nb:
        return False
    if na in DETERMINERS:
        return nb not in DETERMINERS and nb not in PREPOSITIONS
    if adjectives and _content(nb):                    # "a pretty girl", "a big red car": det, adjectives, noun
        j = i
        while j > max(0, i - 2) and _content(norm(words[j].text)):
            j -= 1
        if j < i and norm(words[j].text) in DETERMINERS and all(_content(norm(words[k].text)) for k in range(j + 1, i + 1)):
            return True
    if na in PRONOUNS:
        return is_verb(nb)
    if nb in PREPOSITIONS and nb != "to" and _main_verb(na):
        return True                                    # "talking about", "looking at" (not "want to": "to see")
    if na in PREPOSITIONS:
        return nb not in PREPOSITIONS and nb not in PRONOUNS - {"you", "it"} or nb in ("me", "us", "him", "her", "them")
    if na in NAME_LINKS and _is_name_part(b.text) and i > 0 and _is_name_part(words[i - 1].text):
        return True
    return nb in NAME_LINKS and _is_name_part(a.text) and i + 2 < len(words) and _is_name_part(words[i + 2].text)


def _content(w: str) -> bool:
    """An adjective or a noun, roughly: not a function word, a verb or an adverb ("high", "school", "pretty";
    not "next", "didn't", "really")."""
    stop = WEAK | NOT_OBJECT | PREPOSITIONS | DETERMINERS | PRONOUNS | NAME_STOP | AFTER_NOUN
    return bool(w) and w not in stop and not is_verb(w) and not w.endswith("ly") and not is_interjection(w)


def _noun_after(adj: str, noun: str) -> bool:
    """After a determiner, "adj noun" is one phrase when neither is a function word, a verb or an adverb (a rough
    test: "a high school", "the next thing"; not "a classroom next", "the teachers didn't")."""
    return _content(adj) and _content(noun)


def _lonely(w: str) -> bool:
    """A word that never makes a caption alone: a weak word ("a", "I", "the") or a preposition ("about", "with") --
    except right before a video cut."""
    return is_weak(w) or norm(w) in PREPOSITIONS


def cut_breaks(words: Sequence[Word], cuts: Sequence[int], fps: Fraction,
               bonds: Sequence[bool] | None = None) -> dict[int, int]:
    """{word index i: cut frame} for each video cut of the edit (sequence frames) that falls inside the speech: it
    falls on the word boundary nearest it (between words i-1 and i), where a new caption starts -- exactly on the
    cut. A near tie keeps a pair together: a boundary inside one (``bonds``, "my" | "secret") counts CUT_TIE_S
    farther. A cut nearer the first word's start or the last word's end than to any boundary between two words is
    left out: the caption there starts or ends on it (caption_rules._cut_split)."""
    out: dict[int, int] = {}
    n = len(words)
    if n < 2:
        return out
    if bonds is None:
        bonds = compute_bonds(words)
    at = [words[0].start] + [0.5 * (words[k - 1].end + words[k].start) for k in range(1, n)] + [words[-1].end]
    for f in sorted(int(c) for c in cuts):
        t = f / float(fps)
        if not words[0].start < t < words[-1].end:
            continue
        i = min(range(n + 1), key=lambda k: abs(at[k] - t) + (CUT_TIE_S if 0 < k < n and bonds[k - 1] else 0.0))
        if 0 < i < n:
            out.setdefault(i, f)
    return out


def _allowlist_phrases() -> list[list[str]]:
    """The allowlist's entries of two or more words (caption_allowlist.txt), as normalised word lists."""
    try:
        from .caption_rules import read_allowlist
        return [[norm(x) for x in e.split()] for e in read_allowlist() if len(e.split()) > 1]
    except Exception:  # noqa: BLE001 - no allowlist: no phrases
        return []


def compute_bonds(words: Sequence[Word], phrases: bool = True, adjectives: bool = True) -> list[bool]:
    """bonds[i]: words i and i+1 are never split across captions (prompt "Keep together"): a full name (two or more
    capitalised words that are not sentence starters), a number and its unit (``6 am``, ``50 quid``, ``10 year
    old``) and a negation and its verb (``don't move``, ``didn't get it``); with ``phrases`` also a determiner and
    the word after it, a pronoun and its verb, a preposition and its object (_phrase_bond) and every phrase of
    caption_allowlist.txt (``adjectives``: also "a high school" -- the noun after an adjective). Never across a pause
    > 0.25 s or a sentence end."""
    n = len(words)
    bonds = [False] * max(0, n - 1)
    for i in range(n - 1):
        a, b = words[i], words[i + 1]
        if b.start - a.end > PAUSE_S:
            continue                                   # the speaker split it: a pause always may
        if re.search(r"[,;:]['\"’”]?$", (a.raw or "").strip()):
            continue                                   # a comma ends the phrase: "a joke, Marvel"
        na, nb = norm(a.text), norm(b.text)
        if na == nb:
            continue                                   # a repetition is never bonded
        if phrases and not sentence_end(a.raw or a.text) and _phrase_bond(words, i, adjectives):
            bonds[i] = True
        elif _is_name_part(a.text) and _is_name_part(b.text) and not _ends_sentence(a.raw or a.text):
            bonds[i] = True
        elif _is_number(na) and nb in UNITS:
            bonds[i] = True
        elif na in AGE_UNITS and nb == "old" and i > 0 and _is_number(norm(words[i - 1].text)):
            bonds[i] = True
        elif _is_negation(na) and not _ends_sentence(a.raw or a.text):
            bonds[i] = True
            if i + 2 < n and norm(words[i + 2].text) == "it" and words[i + 2].start - b.end <= PAUSE_S:
                bonds[i + 1] = True                    # "didn't get it"
    if phrases and n > 1:
        keys = [norm(w.text) for w in words]
        for ph in _allowlist_phrases():
            for i in range(n - len(ph) + 1):
                if keys[i:i + len(ph)] == ph:
                    for j in range(i, i + len(ph) - 1):
                        bonds[j] = True
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


def _fits(words: Sequence[Word], u: Sequence[int]) -> bool:
    """One caption's worth: 4 words and 20 characters (5 words when it starts with a weak word: "for a 15 year
    old")."""
    if _chars(words, u) > MAX_CHARS:
        return False
    return len(u) <= MAX_WORDS or (len(u) == MAX_WORDS + 1 and is_weak(words[u[0]].text))


def _cut_unit(words: Sequence[Word], u: list[int], core: Sequence[bool] | None) -> list[list[int]]:
    """A run of bonded words too long for one caption, cut where it breaks the fewest names / numbers + units /
    negations (``core`` bonds), leaves no weak word alone, then into the fewest pieces, the fewest ending on a weak
    word, the most even."""
    import itertools
    n = len(u)
    if n > 12:
        return []
    best = None
    for parts in range(2, n + 1):
        for cuts in itertools.combinations(range(1, n), parts - 1):
            bounds = (0,) + cuts + (n,)
            pieces = [u[a:b] for a, b in zip(bounds, bounds[1:])]
            if not all(_fits(words, p) for p in pieces):
                continue
            lens = [_chars(words, p) for p in pieces]
            score = (sum(1 for c in cuts if core is not None and core[u[c - 1]]),
                     sum(1 for p in pieces if len(p) == 1 and _lonely(words[p[0]].text)), parts,
                     sum(1 for p in pieces[:-1] if len(p) > 1 and is_weak(words[p[-1]].text)), max(lens) - min(lens))
            if best is None or score < best[0]:
                best = (score, pieces)
        if best is not None and best[0][0] == 0:
            break
    return best[1] if best else []


def _units(words: Sequence[Word], bonds: Sequence[bool], glue: set[int],
           core: Sequence[bool] | None = None) -> list[list[int]]:
    """Runs of words that stay together (bonds + moved weak words glued to their next word); a run longer than the
    caps (4 words, 20 characters: hard rule 6) is cut where it breaks the fewest ``core`` bonds (_cut_unit; the caps
    win over a bond)."""
    units: list[list[int]] = []
    cur: list[int] = []
    for i in range(len(words)):
        cur.append(i)
        if i == len(words) - 1 or not (bonds[i] or i in glue):
            units.append(cur)
            cur = []
    out: list[list[int]] = []
    for u in units:
        if _fits(words, u):
            out.append(u)
            continue
        pieces = _cut_unit(words, u, core)
        if pieces:
            out += pieces
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
          glue: set[int], core: Sequence[bool] | None = None) -> list[list[int]]:
    """The grouping walk: a new caption starts before a unit when the caption already has 4 words or would exceed
    20 characters, after a pause > 0.25 s, after a sentence end (hard rules 1 and 2: the transcriber has no speaker
    labels, but a reply starts a new sentence), at a standalone interjection (before and after it), at a word said
    again straight away (repetition: one caption each), or where the weak-word fix forced it."""
    groups: list[list[int]] = []
    cur: list[int] = []
    for u in _units(words, bonds, glue, core):
        if cur:
            prev, first = cur[-1], u[0]
            # a caption of nothing but weak words ("for a") may take one word more (never more than 5) rather
            # than end on a weak word: "for a 15 year old"
            max_words = MAX_WORDS + 1 if all(is_weak(words[i].text) for i in cur) else MAX_WORDS
            brk = (first in forced or words[first].start - words[prev].end > PAUSE_S or alone[prev] or alone[first]
                   or sentence_end(words[prev].raw or words[prev].text)
                   or _repeat(words, prev, first) or len(cur) + len(u) > max_words
                   or _chars(words, cur + u) > MAX_CHARS)
            if brk:
                groups.append(cur)
                cur = []
        cur = cur + u
    if cur:
        groups.append(cur)
    return groups


def group_words(words: Sequence[Word], notes: list[dict] | None = None, gave_out: set[int] | None = None,
                fixed: Sequence[tuple[int, int]] = (), cuts: Iterable[int] = (), pauses: Iterable[int] = ()
                ) -> list[list[int]]:
    """Word-index groups, one per caption: the walk, then the weak-word fix -- a caption of more than one word that
    ends on a weak word gives that word to the front of the next caption (which is re-split if it now breaks the
    caps). Each caption gives away at most one word (the prompt's own example keeps "there is" after giving away
    "a"), together with the words kept together with it ("to one of the" -> "to one" | "of the songs"); a caption
    of nothing but function words ("without the") joins the next words whole. Kept, and listed in ``notes``: a
    weak word that ends a sentence, bonded to the previous one ("didn't get it"), before a silence placeholder,
    before a standalone interjection, or at the very end. ``gave_out`` gets the first word of each caption that gave
    its weak last word away.

    A caption is never a single weak word: it joins the word(s) after it (or, when a silence, a sentence end or
    nothing follows, the caption before it). ``fixed``: word ranges [a, b) that stay one caption (competitor mode:
    the competitor's own captions of 2+ words that pass these rules); a lone weak word just before one may join
    it. ``cuts``: word indices a video cut of the edit falls before (cut_breaks): a caption never runs across one,
    nothing is joined or moved across one, and a weak word may stand alone right before one. "and then" / "so
    then" / "but then" start a caption, and a caption over 16 characters splits at a natural break (_short).
    ``pauses``: word indices a pause in speech comes before when the word times do not show it (competitor mode:
    the words are on the competitor's timing, the pauses from the transcript)."""
    if not words:
        return []
    bonds = compute_bonds(words)
    core_bonds = compute_bonds(words, phrases=False)
    alone = standalone_interjections(words)
    cuts = {int(i) for i in cuts if 0 < int(i) < len(words)}
    forced: set[int] = set(cuts) | {int(i) for i in pauses if 0 < int(i) < len(words)}
    glue: set[int] = set()
    for i in range(1, len(words) - 1):                 # "and then" / "so then" / "but then" start their own caption
        if norm(words[i].text) in THEN_STARTS and norm(words[i + 1].text) == "then":
            forced.add(i)
    torn = {i - 1 for i in cuts if bonds[i - 1]}        # the word before a cut that splits a pair ("for" | "genius")
    for i in forced:
        bonds[i - 1] = core_bonds[i - 1] = False
    for a, b in fixed:
        for i in range(a, b - 1):
            bonds[i] = True
        for i in (a - 1, b - 1):                       # nothing reaches across its edges
            if 0 <= i < len(bonds):
                bonds[i] = core_bonds[i] = False
        forced.add(a)
        if b < len(words):
            forced.add(b)
    hard = set(forced)                                 # cuts, pauses, "and then", the competitor's kept captions
    groups = _walk(words, bonds, alone, forced, glue, core_bonds)
    gave: set[int] = set()
    gi = 0
    while gi < len(groups):
        g = groups[gi]
        last = g[-1]
        if len(g) > 1 and is_weak(words[last].text):
            reason = None if g[0] not in gave else "it already gave one weak word to the next caption"
            tail = last                                # the weak word moves with the words kept together with it
            while tail - 1 >= g[0] and bonds[tail - 1]:
                tail -= 1
            if reason is not None:
                pass
            elif last + 1 >= len(words):
                reason = "last word of the captions"
            elif last in torn and tail == last:
                forced.add(last)                         # cut off from its phrase: it stands alone, on the cut
                groups = _walk(words, bonds, alone, forced, glue, core_bonds)
                if notes is not None:
                    notes.append({"word": last, "reason": "a lone weak word right before a video cut"})
                continue
            elif last + 1 in cuts:
                reason = "a video cut follows"
            elif sentence_end(words[last].raw or words[last].text):
                reason = "it ends a sentence"
            elif tail == g[0] and (last in glue or not all(_function_word(words[k].text) for k in g)):
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
                forced.add(tail)                         # "to one of the" -> "to one" | "of the songs"
                glue.add(last)
                gave.add(g[0])
                groups = _walk(words, bonds, alone, forced, glue, core_bonds)
                continue                                 # look at the shortened caption again (for the notes)
            if notes is not None:
                notes.append({"word": last, "reason": reason})
        gi += 1
    groups = _join_lone_weak(words, groups, bonds, alone, forced, glue, notes, core_bonds, cuts)
    groups = _clause_shift(words, groups, bonds, hard)
    groups = _short(words, groups, bonds)
    if gave_out is not None:
        gave_out.update(g[0] for g in groups if g[0] in gave)
    return groups


def _function_word(w: str) -> bool:
    n = norm(w)
    return n in WEAK or n in PREPOSITIONS or n in DETERMINERS or n in NOT_OBJECT


def _join_lone_weak(words: Sequence[Word], groups: list[list[int]], bonds: Sequence[bool], alone: Sequence[bool],
                    forced: set[int], glue: set[int], notes: list[dict] | None, core_bonds: Sequence[bool],
                    cuts: set[int] = frozenset()) -> list[list[int]]:
    """A caption of one weak word ("a", "I", "the") or preposition ("about") joins the word(s) after it; when a
    silence (> 1 s), a sentence end, a standalone interjection or nothing follows, the caption before it; never
    across a video cut, and right before one it stands alone ("for" | cut | "genius kids"); else it stays, listed."""
    tried: set[int] = set()
    while True:
        lone = next((g[0] for g in groups if len(g) == 1 and _lonely(words[g[0]].text) and g[0] not in tried), None)
        if lone is None:
            return groups
        tried.add(lone)
        i, n = lone, len(words)
        nxt_ok = (i + 1 < n and i + 1 not in cuts and not sentence_end(words[i].raw or words[i].text)
                  and not alone[i + 1] and words[i + 1].start - words[i].end <= SILENCE_S
                  and not _repeat(words, i, i + 1))
        prv_ok = (i > 0 and i not in cuts and i + 1 not in cuts and not sentence_end(words[i - 1].raw or words[i - 1].text)
                  and not alone[i - 1] and words[i].start - words[i - 1].end <= SILENCE_S
                  and not _repeat(words, i - 1, i))
        if nxt_ok:
            glue.add(i)
            forced.discard(i + 1)
        elif prv_ok:
            glue.add(i - 1)
            forced.discard(i)
            why = ("nothing follows" if i + 1 >= n
                   else "it ends a sentence" if sentence_end(words[i].raw or words[i].text)
                   else "the next caption is an interjection" if alone[i + 1]
                   else "the next word repeats it" if _repeat(words, i, i + 1) else "a silence follows")
            if notes is not None:
                notes.append({"word": i, "reason": f"a lone weak word, joined to the caption before ({why})"})
        else:
            if notes is not None:
                notes.append({"word": i, "reason": "a lone weak word right before a video cut" if i + 1 in cuts
                              else "a lone weak word with nothing to join"})
            continue
        groups = _walk(words, bonds, alone, forced, glue, core_bonds)


_SUBJECT = re.compile(r"^(?:i|you|we|they|he|she|it|that|there|who|what)'(?:re|m|s|ve|ll|d)$")   # you're, I'm


def _clause_start(words: Sequence[Word], i: int) -> bool:
    """Word i starts a new clause: what, when, where, why, how, who, because, if -- or "that" before a subject ("know
    that I was", "said that the school"; not "that place", "that is")."""
    n = norm(words[i].text)
    if n in CLAUSE_STARTS:
        return True
    if n != "that" or i + 1 >= len(words):
        return False
    nb = norm(words[i + 1].text)
    return nb in PRONOUNS or nb in DETERMINERS or _SUBJECT.match(nb) is not None


def _subject(w: str) -> bool:
    """A subject that cannot make a caption alone: a pronoun or its contraction (I, you're, I've, let's)."""
    n = norm(w)
    return n in PRONOUNS or _SUBJECT.match(n) is not None or n == "let's"


def _natural_breaks(words: Sequence[Word], g: Sequence[int], bonds: Sequence[bool]) -> list[tuple[int, str]]:
    """Where caption g may split to get shorter (never inside a pair kept together), as (index, kind): before a verb
    phrase -- after a subject with its helping verb ("what you're" | "talking about") or after a helping verb,
    before the main verb ("they would" | "bring me up"; not "I've been" | "waiting") --, before a preposition's
    phrase ("suggested" | "to Marvel"; never before "of", which belongs to the noun before it: "lost track of
    time"), before a new clause ("no idea" | "what you're"; _clause_start) and before and after "and then" / "so
    then" / "but then" ("and then" | "she's like")."""
    out = []
    for k in range(1, len(g)):
        i = g[k]
        if bonds[i - 1]:
            continue
        a, b = norm(words[i - 1].text), norm(words[i].text)
        if (b in THEN_STARTS and k + 1 < len(g) and norm(words[g[k + 1]].text) == "then") \
                or (a == "then" and k >= 2 and norm(words[g[k - 2]].text) in THEN_STARTS):
            out.append((k, "then"))
        elif ((_SUBJECT.match(a) is not None and (is_verb(b) or b in AUX))
              or (a in AUX and a not in ("be", "been", "being") and _main_verb(b))):
            out.append((k, "verb"))
        elif _clause_start(words, i) and k + 1 < len(g):
            out.append((k, "clause"))
        elif b in PREPOSITIONS and b != "of" and k + 1 < len(g) and bonds[i] and a not in PREPOSITIONS:
            out.append((k, "prep"))
    return out


def _clause_shift(words: Sequence[Word], groups: list[list[int]], bonds: Sequence[bool], hard: set[int]
                  ) -> list[list[int]]:
    """A caption ending on the first words of a new clause gives them to the next caption when they fit it there
    ("I don't know if" | "he is coming" -> "I don't know" | "if he is coming", "you know what I'm" | "saying" ->
    "you know" | "what I'm saying"): the break goes before the clause. Never across a ``hard`` break (a video cut,
    a pause, "and then", a competitor caption kept as it was) or a sentence end, and never leaving a lone weak word
    or subject, or a caption ending on a weak word."""
    out = [list(g) for g in groups]
    for gi in range(len(out) - 1):
        a, b = out[gi], out[gi + 1]
        if len(a) < 2 or b[0] in hard or sentence_end(words[a[-1]].raw or words[a[-1]].text) \
                or words[b[0]].start - words[a[-1]].end > PAUSE_S:
            continue
        for k in range(len(a) - 1, 0, -1):             # the last clause start in the caption
            if not _clause_start(words, a[k]) or bonds[a[k] - 1]:
                continue
            head, tail = a[:k], a[k:] + b
            if _fits(words, tail) and not is_weak(words[head[-1]].text) and not (
                    len(head) == 1 and (_lonely(words[head[0]].text) or _subject(words[head[0]].text))):
                out[gi], out[gi + 1] = head, tail
            break
    return out


def _short(words: Sequence[Word], groups: list[list[int]], bonds: Sequence[bool]) -> list[list[int]]:
    """Prefer short captions (hard rule B): a caption over SHORT_CHARS splits at a natural break (_natural_breaks)
    when neither piece is a lone weak word / preposition / subject or ends on a weak word, and a verb phrase break
    leaves two words on each side ("You're gonna lose", "what I am saying?" stay whole); the most even split."""
    out: list[list[int]] = []
    todo = list(groups)
    while todo:
        g = todo.pop(0)
        best = None
        if _chars(words, g) > SHORT_CHARS:
            for k, kind in _natural_breaks(words, g, bonds):
                a, b = g[:k], g[k:]
                if (kind in ("verb", "clause") and min(len(a), len(b)) < 2) or is_weak(words[a[-1]].text) \
                        or any(len(p) == 1 and (_lonely(words[p[0]].text) or _subject(words[p[0]].text))
                               for p in (a, b)):
                    continue
                score = max(_chars(words, a), _chars(words, b))
                if best is None or score < best[0]:
                    best = (score, a, b)
        if best is None:
            out.append(g)
        else:
            todo[:0] = [best[1], best[2]]
    return out


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
                   placeholders: bool = True, mode: str = "voice", notes: list[dict] | None = None,
                   cuts: Sequence[int] = ()) -> list[Caption]:
    """Captions of a word list on the sequence (frames at ``fps``; ``n_frames`` = timeline length). Back to back:
    each caption ends where the next starts; the last one at its own last word's end. With ``placeholders``,
    every stretch of more than ~1 s without speech (also before the first / after the last word) becomes a
    ``*...*`` caption. ``lo`` / ``hi`` clamp the captions (a gap between two competitor captions). ``cuts``: the
    edit's video cuts (sequence frames): a caption never runs across one, it changes exactly on the cut."""
    hi = int(n_frames if hi is None else hi)
    words = [w for w in words if w.text]
    if not words:
        if placeholders and hi - lo > to_frame(SILENCE_S, fps):
            return [Caption(PLACEHOLDER, lo, hi, "placeholder")]
        return []
    weak_notes: list[dict] = []
    gave: set[int] = set()
    at_cut = cut_breaks(words, cuts, fps)
    groups = group_words(words, weak_notes, gave, cuts=at_cut)
    if notes is not None:
        for wn in weak_notes:
            notes.append({"text": words[wn["word"]].text, "time": words[wn["word"]].start, "reason": wn["reason"]})
    timeline_end = hi / float(fps)
    caps: list[Caption] = []
    if placeholders and words[0].start - lo / float(fps) > SILENCE_S:
        caps.append(Caption(PLACEHOLDER, lo, to_frame(words[0].start, fps), "placeholder"))
    for gi, g in enumerate(groups):
        ws = [words[i] for i in g]
        start = at_cut.get(g[0], to_frame(ws[0].start, fps))          # on the cut, exactly
        caps.append(Caption(" ".join(w.text for w in ws), start, max(start + 1, to_frame(ws[-1].end, fps)),
                            mode, ws, {"gave": True} if g[0] in gave else {}))
        nxt = words[groups[gi + 1][0]].start if gi + 1 < len(groups) else timeline_end
        if placeholders and nxt - ws[-1].end > SILENCE_S:
            caps.append(Caption(PLACEHOLDER, to_frame(ws[-1].end, fps), to_frame(nxt, fps), "placeholder"))
    for i in range(len(caps) - 1):                     # back to back
        caps[i].end = caps[i + 1].start
    return _monotonic(caps, lo, hi)


# ---------------------------------------------------------------------------------------------
# Competitor mode: the competitor's captions as read (caption_rules.enforce writes them my way)
# ---------------------------------------------------------------------------------------------

def competitor_copy(spans: Sequence[dict], words: Sequence[Word], comp_fps: Fraction, to_seq, seq_fps: Fraction
                    ) -> tuple[list[Caption], dict]:
    """The competitor's captions exactly as on screen (``spans`` from caption_ocr.read_caption_spans: first and last
    frame, the text as written), before the hard rules (caption_rules.enforce, in run_captions). A caption the OCR
    could not read takes the words heard while it is on screen, written the competitor's way
    (caption_ocr.apply_conventions; punctuation the competitor never uses dropped), listed in
    ``notes["from_transcript"]``; one with no words heard either is left out (``notes["unreadable"]``). Returns
    (captions, notes)."""
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
    """Write the SRT -- never one that still breaks hard rules 1-4 (one sentence, one speaker, no capital inside a
    word, no ALL CAPS but acronyms: caption_rules.check); ValueError then."""
    from .caption_rules import RULES, check
    from .common import replace_file
    bad = {r: v for r, v in check(caps, fps, rules=(1, 2, 3, 4)).items() if v}
    if bad:
        raise ValueError("captions still break " + "; ".join(f"rule {r} ({RULES[r]}): {', '.join(v[:3])}"
                                                             for r, v in bad.items()))
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

TIMING_FRAMES = 2         # a caption starts within this many frames of its first word being spoken
ONSET_S = 0.2             # a transcript's word start this close to where a sound starts: the sound's start


def speech_starts(items: Sequence[dict], sm: Any, fps: Fraction) -> tuple[list[float], list[tuple[str, float, float]]]:
    """(where speech starts in the final edit -- every speech sound of the RAW's speech map ``sm`` that starts inside
    an A1 item at 100 %, in sequence seconds --, the RAW's words where A1 plays them: (text, start s, end s)).
    ``items``: the final XML's A1 items {start, end, in, out, speed} (sequence frames; in/out at the sequence rate)."""
    f = float(fps)
    onsets: list[float] = []
    ws: list[tuple[str, float, float]] = []
    for it in items:
        if int(it["start"]) < 0 or abs(float(it.get("speed", 1.0)) - 1.0) > 1e-6:
            continue
        r0, r1 = it["in"] / f, it["out"] / f
        t0 = it["start"] / f
        for snd in sm.sounds:
            if snd.speech and r0 - 1e-6 <= snd.s0 < r1:
                onsets.append(t0 + snd.s0 - r0)
        for text, a, b in sm.words:
            if r0 - 1e-6 <= a < r1:
                ws.append((text, t0 + a - r0, t0 + min(b, r1) - r0))
    return sorted(onsets), sorted(ws, key=lambda w: w[1])


def _matches(caps: Sequence[Caption], ref: Sequence[tuple[str, float, float]]) -> tuple[list, dict[int, int]]:
    """(the captions' tokens as one stream: (caption, token, word), {token: ref word}) -- the captions' text aligned
    with a transcript (difflib: equal runs, and one-for-one replacements)."""
    import difflib
    toks: list[tuple[int, int, str]] = []
    for ci, c in enumerate(caps):
        if c.mode == "placeholder" or is_action_text(c.text):
            continue
        for ti, t in enumerate(c.text.split()):
            toks.append((ci, ti, norm(t)))
    match: dict[int, int] = {}
    ops = difflib.SequenceMatcher(None, [x for _, _, x in toks], [norm(w[0]) for w in ref], autojunk=False).get_opcodes()
    b = [norm(w[0]) for w in ref]
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            match.update(zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace" and i2 - i1 == j2 - j1:          # a misread word: only when it reads alike
            match.update((i, j) for i, j in zip(range(i1, i2), range(j1, j2))
                         if difflib.SequenceMatcher(None, toks[i][2], b[j]).ratio() >= 0.5)
    return toks, match


def spoken_starts(caps: Sequence[Caption], onsets: Sequence[float], refs: Sequence[Sequence[tuple[str, float, float]]],
                  fps: Fraction, cuts_s: Sequence[float] = (), far_s: float = 2.0
                  ) -> tuple[list[float | None], list[bool]]:
    """(when the first word of each caption is spoken in the final edit (seconds), or None; whether the caption is
    heard there at all -- at least half its words, and half its content words when it has two or more: "it I
    haven't got" over "it, I just" is not). The captions' text is aligned, as one stream, with each
    transcript of the final edit in turn (``refs``: the RAW's words where A1 plays them first, then the edit's own
    transcript); the first word's start is moved to where its sound starts (``onsets``) when that is within
    ONSET_S, never back into the word before it -- and past an audio cut (``cuts_s``) a word cannot straddle."""
    f = float(fps)
    starts: list[float | None] = [None] * len(caps)
    spoken = [not (c.mode == "placeholder" or is_action_text(c.text)) for c in caps]
    total = [len(c.text.split()) if sp else 0 for c, sp in zip(caps, spoken)]
    content = [sum(1 for t in c.text.split() if not is_weak(t)) if sp else 0 for c, sp in zip(caps, spoken)]
    best_heard, best_content = [0] * len(caps), [0] * len(caps)
    for ref in refs:
        if not ref:
            continue
        toks, match = _matches(caps, ref)
        heard_n, heard_c = [0] * len(caps), [0] * len(caps)
        for k, (ci, ti, t) in enumerate(toks):
            if k in match and abs(ref[match[k]][1] - caps[ci].start / f) <= far_s:
                heard_n[ci] += 1
                heard_c[ci] += 0 if is_weak(t) else 1
        best_heard = [max(x, y) for x, y in zip(best_heard, heard_n)]
        best_content = [max(x, y) for x, y in zip(best_content, heard_c)]
        for k, (ci, ti, _) in enumerate(toks):
            if ti != 0 or k not in match or starts[ci] is not None:
                continue
            j = match[k]
            t, end = ref[j][1], ref[j][2]
            if abs(t - caps[ci].start / f) > far_s:          # a common word matched in the wrong place
                continue
            lo = max(t - ONSET_S, ref[j - 1][1] + 0.05 if j > 0 else -1.0)
            hi = t + ONSET_S
            across = [x for x in cuts_s if t - 1e-6 < x <= max(end, t + 0.05) + 1e-6]
            if across:                                        # no word runs across an audio cut: it starts after
                lo, hi = across[-1], across[-1] + 2 * ONSET_S
                t = across[-1]
            cand = [o for o in onsets if lo <= o <= hi]
            starts[ci] = min(cand, key=lambda o: abs(o - t)) if cand else t
    # heard: at least half its words -- and half its content words when it has two or more (a weak word -- "I",
    # "it", "the" -- is said everywhere: it alone is no sign the caption's words are)
    heard = [n > 0 and b > 0 and 2 * b >= n and (nc < 2 or 2 * bc >= nc)
             for n, b, nc, bc in zip(total, best_heard, content, best_content)]
    # a caption no transcript has words for, over speech the better transcript has no other words for: its words
    # were said (the transcripts missed them) -- timed to where that speech starts
    every = sorted(w[1] for w in (refs[0] if refs else []))         # the better transcript's words
    for ci, c in enumerate(caps):
        if heard[ci] or not total[ci]:
            continue
        a, b = c.start / f, c.end / f
        i = bisect.bisect_left(every, a - 0.1)
        if i < len(every) and every[i] < b - 0.1:
            continue                                          # other words are said there: not this caption's
        cand = [o for o in onsets if a - 3 * ONSET_S <= o < b                # speech no known word starts at
                and not any(abs(o - w) <= 0.15 for w in every[max(0, i - 3):i + 3])]
        if cand:
            heard[ci] = True
            starts[ci] = min(cand, key=lambda o: abs(o - a))
    return starts, heard


def time_to_speech(caps: list[Caption], fps: Fraction, onsets: Sequence[float],
                   refs: Sequence[Sequence[tuple[str, float, float]]], cuts: Sequence[int] = ()) -> int:
    """Every caption whose first word is heard starts on the frame that word is spoken in the final edit
    (spoken_starts) -- never before an audio cut it started after; the caption before it ends there, or on the last
    audio cut before it. A caption moved past its own end keeps its length; captions that were back to back stay
    so. ``cuts``: the audio cuts of the final edit (sequence frames). Returns how many moved."""
    moved = 0
    cut = sorted(set(int(x) for x in cuts))
    starts, _ = spoken_starts(caps, onsets, refs, fps, [x / float(fps) for x in cut])
    was = [(c.start, c.end) for c in caps]
    joined = [was[i][1] >= was[i + 1][0] for i in range(len(caps) - 1)]
    for i, c in enumerate(caps):
        t = starts[i]
        if t is None:
            continue
        s = to_frame(t, fps)
        lo = caps[i - 1].start + 1 if i else 0
        lo = max([lo] + [x for x in cut if s < x <= c.start])          # not back across an audio cut
        s = max(s, lo)
        nxt = next((to_frame(starts[k], fps) for k in range(i + 1, len(caps)) if starts[k] is not None), None)
        if nxt is not None:
            s = min(s, nxt - 1)
        if s == c.start or s < lo:
            continue
        if i:
            p = caps[i - 1]
            if joined[i - 1] or p.end > s:
                inside = [x for x in cut if p.start < x <= s]
                p.end = max(p.start + 1, inside[-1] if inside else s)
        if c.end <= s:
            c.end = s + max(1, was[i][1] - was[i][0])
        c.start = s
        moved += 1
    for i in range(len(caps) - 1):                       # never overlapping the caption after
        p, c = caps[i], caps[i + 1]
        if p.end > c.start:
            p.end = c.start
        elif joined[i] and p.end < c.start:              # back to back before: up to the next one (or the cut)
            after = [x for x in cut if p.end < x <= c.start]
            if after:
                p.end = after[-1]
            elif p.end not in cut:
                p.end = c.start
    return moved


def no_overlaps(caps: Sequence[Caption]) -> list[Caption]:
    """Captions in order, none running into the next: a caption that does ends where the next starts (one left
    with no frame is dropped) -- e.g. a ``*...*`` placeholder over words captioned from the transcript later."""
    out: list[Caption] = []
    srt = sorted(caps, key=lambda c: (c.start, c.end))
    for i, c in enumerate(srt):
        nxt = srt[i + 1].start if i + 1 < len(srt) else None
        if nxt is not None and c.end > nxt:
            if nxt <= c.start:
                continue
            c = dataclasses.replace(c, end=nxt)
        out.append(c)
    return out


def fill_from_transcript(caps: list[Caption], words: Sequence[Word], fps: Fraction, n_frames: int,
                         cuts: Sequence[int] = (), styled: bool = False) -> tuple[list[Caption], list[Caption]]:
    """Speech of my edit no caption covers (the competitor's captions there were of its own audio): captions made
    from the edit's transcript (voice_captions -- ``styled``: the user's style, caption_style.style_captions -- the
    hard rules applied), between the captions around it. Returns (all captions in order, the new ones)."""
    from . import caption_rules
    caps = sorted(caps, key=lambda c: c.start)
    starts = [c.start for c in caps]

    f = float(fps)
    texts = [{norm(t) for t in c.text.split()} for c in caps]

    def covered(w: Word) -> bool:
        """A caption shows it: the same word in a caption within 0.3 s, or its middle well inside a caption."""
        m = 0.5 * (w.start + w.end)
        x = norm(w.text)
        return any((c.start / f - 0.3 <= m <= c.end / f + 0.3 and x in tx) or
                   (c.start + 3 <= m * f <= c.end - 3) for c, tx in zip(caps, texts))
    groups: list[list[Word]] = []
    for w in words:
        if not w.text or covered(w):
            continue
        if groups and bisect.bisect_right(starts, to_frame(groups[-1][-1].end, fps)) == \
                bisect.bisect_right(starts, to_frame(w.start, fps)):
            groups[-1].append(w)
        else:
            groups.append([w])
    new: list[Caption] = []
    for g in groups:
        if len(g) < 2 and g[-1].end - g[0].start < 0.4:
            continue                                     # a word at the edge of a caption: timing jitter, not speech
        a, b = to_frame(g[0].start, fps), to_frame(g[-1].end, fps)
        lo = max([c.end for c in caps if c.end <= a + 1] or [0])
        hi = min([c.start for c in caps if c.start >= b - 1] or [n_frames])
        if hi - lo < 2:
            continue
        if styled:
            from .caption_style import style_captions
            made = style_captions(g, fps, n_frames, lo=lo, hi=hi, placeholders=False, cuts=cuts)
        else:
            made = voice_captions(g, fps, n_frames, lo=lo, hi=hi, placeholders=False, cuts=cuts)
        if made:
            made, _ = caption_rules.enforce(made, fps, "voice", list(words), cuts=cuts, keep_groups=styled)
            new += [dataclasses.replace(c, info=dict(c.info, source="transcript")) for c in made]
    return sorted(caps + new, key=lambda c: c.start), new


def other_video_stretches(ctx: Any, xml: str | Path, fps: Fraction) -> list[dict]:
    """Another video's stretches in the final edit (broll.py; V1 and A1 left empty, OTHER VIDEO markers):
    [{name, segment, a, b (sequence frames), t0, t1 (competitor s), words [(text, start, end, prob)] and onsets
    (sequence s: the competitor's speech there, its sounds' starts)}] -- what the captions there are made from and
    timed to (the competitor's audio, not my edit's)."""
    from types import SimpleNamespace
    from .export_xml_edl import other_video_name, other_video_of, other_video_ranges, parse_premiere_xml
    x = parse_premiere_xml(xml)
    ranges = other_video_ranges(x)
    if not ranges:
        return []
    br = getattr(ctx, "broll", None)
    cl = (br.get("cutlist") if isinstance(br, dict) else None) or ctx.cutlist
    segs = sorted((s for s in cl.segments if other_video_of(s) is not None), key=lambda s: int(s.comp_in))
    comp_fps, f = Fraction(ctx.comp_fps), float(fps)
    y, sr = getattr(ctx, "comp_audio", None), int(getattr(ctx, "audio_sr", 0) or 0)
    out = []
    for (a, b), sg in zip(ranges, segs):
        t0, t1 = float(Fraction(int(sg.comp_in)) / comp_fps), float(Fraction(int(sg.comp_out)) / comp_fps)
        base, end = a / f, b / f
        ws = []
        for w in other_video_of(sg).get("words") or []:
            s0, s1 = base + float(w[1]) - t0, base + float(w[2]) - t0
            if s1 > base and s0 < end:
                ws.append((str(w[0]), max(base, s0), min(end, max(s0, s1)), float(w[3]) if len(w) > 3 else 1.0))
        onsets: list[float] = []
        if y is not None and sr and len(y) and ws:
            from . import silence, speech
            piece = np.asarray(y[int(t0 * sr):int(math.ceil(t1 * sr))], np.float32)
            sm = speech.speech_map(piece, sr, silence.Settings.from_cfg(ctx.cfg),
                                   [SimpleNamespace(text=w[0], start=w[1] - base, end=w[2] - base) for w in ws])
            onsets = [base + snd.s0 for snd in sm.sounds if snd.speech and snd.s0 < end - base]
        out.append({"name": other_video_name(a, b, Fraction(fps)), "segment": int(sg.id), "a": a, "b": b,
                    "t0": t0, "t1": t1, "words": ws, "onsets": onsets})
    return out


def without_stretches(y16: np.ndarray, ov: Sequence[dict], fps: Fraction) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """(the edit's audio, 16 kHz, with another video's stretches taken out -- my audio alone, transcribed as it was
    before they were kept --, [(where each was taken out in the shortened audio, its length)] in seconds)."""
    from .transcribe import SR
    f = float(fps)
    keep, held, at, gone = [], [], 0, 0
    for st in sorted(ov, key=lambda d: d["a"]):
        n0, n1 = min(len(y16), int(round(st["a"] / f * SR))), min(len(y16), int(round(st["b"] / f * SR)))
        if n1 <= n0 or n0 < at:
            continue
        keep.append(y16[at:n0])
        held.append(((n0 - gone) / float(SR), (n1 - n0) / float(SR)))
        gone += n1 - n0
        at = n1
    keep.append(y16[at:])
    return np.concatenate(keep).astype(np.float32), held


def restore_times(words: Sequence[Word], held: Sequence[tuple[float, float]]) -> list[Word]:
    """Words of the shortened audio (without_stretches) back on the edit's timeline."""
    out = []
    for w in words:
        sh = sum(n for at, n in held if at <= 0.5 * (w.start + w.end) + 1e-9)
        out.append(dataclasses.replace(w, start=w.start + sh, end=w.end + sh))
    return out


def clip_to_stretches(caps: Sequence[Caption], ov: Sequence[dict]) -> int:
    """A competitor caption running a little over the edge of another video's stretch (its screen lagged the cut)
    belongs to the side it is mostly on: it ends / starts on the edge. Returns how many moved."""
    n = 0
    for c in caps:
        for e in sorted({st["a"] for st in ov} | {st["b"] for st in ov}):
            if c.start < e < c.end:
                if e - c.start >= c.end - e:
                    c.end = e
                else:
                    c.start = e
                n += 1
    return n


def unheard(caps: Sequence[Caption], fps: Fraction, onsets: Sequence[float],
            refs: Sequence[Sequence[tuple[str, float, float]]]) -> list[int]:
    """The captions whose words are not heard in the final edit at all (the competitor's audio, not mine)."""
    _, heard = spoken_starts(caps, onsets, refs, fps)
    return [i for i, (c, h) in enumerate(zip(caps, heard))
            if not h and c.mode != "placeholder" and not is_action_text(c.text)]


def timing_off(caps: Sequence[Caption], fps: Fraction, onsets: Sequence[float],
               refs: Sequence[Sequence[tuple[str, float, float]]], cuts: Sequence[int] = ()) -> list[dict]:
    """The check: every caption that starts more than TIMING_FRAMES frames from its first word being spoken in the
    final edit -- {start_tc, text, off (frames, + = late), spoken_tc}; one whose words are not heard there at all
    has off None."""
    out = []
    starts, heard = spoken_starts(caps, onsets, refs, fps, [x / float(fps) for x in cuts])
    for c, t, h in zip(caps, starts, heard):
        if c.mode == "placeholder" or is_action_text(c.text):
            continue
        row = {"start_tc": ms_tc(frame_ms(c.start, fps)), "end_tc": ms_tc(frame_ms(c.end, fps)), "text": c.text}
        if not h or t is None:
            out.append(dict(row, off=None, spoken_tc=None))
            continue
        off = c.start - to_frame(t, fps)
        if abs(off) > TIMING_FRAMES:
            out.append(dict(row, off=int(off), spoken_tc=ms_tc(int(round(t * 1000)))))
    return out


def edit_cuts(xml: str | Path, fps: Fraction) -> list[int]:
    """The video cuts of the edit (rule 10): the frames on the ``fps`` caption sequence where one V1 clip of
    1_edit.xml gives way to the next -- not where the same take simply runs on (same clip, framing and speed, the
    source continuing)."""
    from .export_xml_edl import parse_premiere_xml
    x = parse_premiere_xml(xml)
    rate = Fraction(int(x["timebase"] or 0)) * (Fraction(1000, 1001) if str(x.get("ntsc")).upper() == "TRUE" else 1)
    if rate <= 0:
        return []
    clips = sorted(x["clips"], key=lambda c: c["start"])
    out = []
    for a, b in zip(clips, clips[1:]):
        same = (a["name"] == b["name"] and a["out"] == b["in"] and a["motion"] == b["motion"]
                and a["speed"] == b["speed"] and a["flip"] == b["flip"])
        if not same:
            out.append(int(np.floor(float(Fraction(b["start"]) * Fraction(fps) / rate) + 0.5)))
    return sorted(set(out))


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


def _raw_source(ctx: Any, cl: Any, rp: Any, fps: Fraction) -> tuple[list, Any]:
    """(the edit's pieces of the RAW, the RAW's audio): where each stretch of the edit's audio comes from."""
    from . import caption_recheck as R
    pieces = R.pieces_from_cutlist(cl)
    if rp is not None:
        from .silence import ripple_pieces
        pieces = ripple_pieces(pieces, rp, fps)
    return pieces, R.audio_source(ctx.raw_audio, int(ctx.audio_sr))


def _judge(model: str, cache: Any) -> Any:
    """caption_recheck.screen_readings' judge: speech model ``model`` scoring readings of audio windows."""
    def run(jobs: list) -> list:
        from . import transcribe
        return [transcribe.score_texts(y, texts, model, cache) for y, texts in jobs]
    return run


def _said_as(w: Word) -> tuple:
    """A word and its times: the same word from the same transcript (whatever else was changed on it)."""
    return w.text, round(w.start, 4), round(w.end, 4)


def _time_new_words(y16: np.ndarray, words: list[Word], mine: set[tuple]) -> list[Word]:
    """The words another model or the recheck gave (rough times: spread over the words they replace) timed by
    forced alignment, each between the words around it; the main model's own words keep their times (aligned once
    already -- aligning the whole transcript again only moves them)."""
    new = [k for k, w in enumerate(words) if _said_as(w) not in mine]
    if not new:
        return words
    from . import align
    if align.available() is not None:
        return words
    try:
        timed = align.refine_onsets(y16, align.align(y16, words)[0])
    except Exception as e:  # noqa: BLE001 - the rough times
        warn(f"the replaced words could not be timed ({type(e).__name__}: {e})")
        return words
    if len(timed) != len(words):
        return words
    out = list(words)
    for k in new:
        lo = out[k - 1].end if k > 0 else 0.0
        hi = next((out[j].start for j in range(k + 1, len(out)) if _said_as(out[j]) in mine), math.inf)
        a = min(max(timed[k].start, lo), hi)
        out[k] = dataclasses.replace(words[k], start=a, end=min(max(timed[k].end, a), hi))
    return out


def run_captions(ctx) -> dict:
    """Write ``<run folder>/2_captions.srt`` for a pipeline.Context (after the exports); returns the report data."""
    from .common import dump_json, log
    from .export_xml_edl import premiere_settings
    cfg = ctx.cfg
    requested = str(getattr(cfg, "captions", "auto") or "auto")
    voiceover = str(getattr(cfg, "voiceover", "") or "")
    model = str(getattr(cfg, "caption_model", "large-v3") or "large-v3")
    language = str(getattr(cfg, "caption_language", "en") or "") or None
    fps = Fraction(premiere_settings(cfg)["fps"])
    comp_fps = Fraction(ctx.comp_fps)
    to_seq = seq_frame_of(comp_fps, fps)
    n_seq = to_seq(ctx.n_comp)
    rp = (getattr(ctx, "silence", None) or {}).get("ripple")      # silences cut out of the Premiere export
    rp = rp if rp is not None and rp.active else None
    if rp is not None:
        n_seq = rp.new_frames
    res: dict = {"requested": requested, "fps": str(fps), "frames": n_seq, "notes": [], "warnings": [],
                 "weak_kept": [], "flags": [], "disagreements": [], "placeholders": [], "over_cap": []}

    def warn(msg: str) -> None:
        res["warnings"].append(msg)
        ctx.warn(f"captions: {msg}")

    from .run_folders import CAPTIONS_SRT
    stale = cfg.deliver / CAPTIONS_SRT            # this run's captions only (an older file never survives)
    if stale.exists():
        stale.unlink()

    # ---- the competitor's burned-in captions, read from the picture ----
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
    span_fps, span_seq = comp_fps, to_seq          # the spans' frames -> sequence frames
    comp_spans = list(spans)                         # as read, on the competitor's own frames
    if rp is not None and spans:                    # the silences cut out: the copies move with the cuts
        spans, gone = move_spans(spans, to_seq, rp)
        span_fps, span_seq = fps, int
        res["silence_dropped"] = [{"text": d.get("ocr") or "", "start_tc": ms_tc(frame_ms(d["seq_in"], fps)),
                                   "end_tc": ms_tc(frame_ms(d["seq_out"], fps))} for d in gone]
    mode = "competitor" if spans else "voice"
    if requested == "competitor" and not spans:
        warn("--captions competitor: no competitor captions to copy -- made from the voice-over instead")
    res["mode"] = mode
    res["reason"] = ("--captions " + requested if requested != "auto" else
                     "auto: --voiceover given" if voiceover else
                     f"auto: the competitor has burned-in captions ({len(spans)} on screen)" if spans else
                     "auto: no burned-in captions found on the competitor")

    # ---- another video's stretches (broll.py): left empty in the edit; the competitor's audio there is what will
    # play once it is filled -- transcribed with the edit, it captions them and times them ----
    from .run_folders import EDIT_XML
    xml = cfg.deliver / EDIT_XML
    ov: list[dict] = []
    if xml.exists() and not voiceover:
        try:
            ov = other_video_stretches(ctx, xml, fps)
        except Exception as e:  # noqa: BLE001 - the captions there keep the competitor's timing
            warn(f"another video's stretches could not be read ({type(e).__name__}: {e})")
    in_ov = lambda t: any(st["a"] <= t * float(fps) < st["b"] for st in ov)      # noqa: E731 - t: sequence s

    # ---- the words (the cut edit's audio, or the voice-over): voice mode, and captions that cannot be read ----
    from . import transcribe
    words: list[Word] = []
    y16 = None
    err = None
    cl = None
    if transcribe.available() is not None and not voiceover:
        err = transcribe.available()
        res["source"] = "none (transcription not available)"
        warn(f"no transcription: {err}")
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
        if rp is not None:                          # the edit as exported: its silences cut out
            from .silence import cut_audio
            y16 = cut_audio(y16, transcribe.SR, fps, rp, (ctx.raw_audio, int(ctx.audio_sr)))
            res["source"] += ", its silences cut out"
        if ov:
            res["source"] += "; another video's stretches: the competitor's audio there"
    else:
        res["source"] = "none (the RAW has no audio)"
    hints = caption_hints()
    extra: dict[int, list[str]] = {}               # words the second model heard otherwise: rechecked below
    check_model = str(getattr(cfg, "caption_check_model", "") or "")
    mine: set[tuple] = set()
    if y16 is not None and len(y16) and err is None:
        err = transcribe.available()
        if err:
            warn(f"no transcription: {err}")
        else:
            y_in, held = without_stretches(y16, ov, fps) if ov else (y16, [])    # my audio alone (another video's
            try:                                                                  # stretches taken out)
                words = restore_times(transcribe.transcribe_words(y_in, transcribe.SR, model, language, ctx.cache,
                                                                  hints), held)
            except Exception as e:  # noqa: BLE001 - e.g. the model download failed: captions without a transcript
                err = f"{type(e).__name__}: {e}"
                warn(f"transcription failed: {err}")
            mine = {_said_as(w) for w in words}          # the main model's own words (aligned already)
            if words and check_model.lower() not in ("", "none") and check_model != model:
                try:                                 # the second-best model: where it hears otherwise, decided
                    from . import caption_recheck as R2
                    other = restore_times(transcribe.transcribe_words(y_in, transcribe.SR, check_model, language,
                                                                      ctx.cache, hints, aligned=False), held)
                    words, two, extra = R2.resolve_two(words, other, model, check_model,
                                                       R2.clear_captions(spans, span_fps) if spans else None)
                    res["second_opinion"] = dict(two, words=len(other),
                                                 text=" ".join(w.raw or w.text for w in other))
                    if spans:                    # "want to" heard, "WANNA" on screen: what was said
                        words, res["spoken_forms"] = R2.screen_spoken_forms(words, R2.clear_captions(spans,
                                                                                                    span_fps))
                        try:                     # the screen's own words, where both models find them likelier
                            pieces, source = (_raw_source(ctx, cl, rp, fps) if not voiceover else (None, None))
                            words, res["screen_readings"] = R2.screen_readings(
                                words, R2.screen_words(spans, span_fps), y16,
                                [_judge(m, ctx.cache) for m in (check_model, model)],
                                avoid=lambda a, b: any(st["a"] < b * float(fps) and st["b"] > a * float(fps)
                                                       for st in ov), pieces=pieces, source=source,
                                glossary=glossary_entries())
                        except Exception as e:  # noqa: BLE001 - the words as heard
                            warn(f"the screen's readings could not be scored ({type(e).__name__}: {e})")
                except Exception as e:  # noqa: BLE001 - the main transcript alone
                    warn(f"the second speech model ({check_model}) could not run: {type(e).__name__}: {e}")
            learned = glossary_entries()
            if words and learned:                # the words the user corrected before, where the audio fits
                try:
                    from . import caption_recheck as R2
                    pieces, source = (_raw_source(ctx, cl, rp, fps) if not voiceover else (None, None))
                    names = [m for m in dict.fromkeys((check_model, model)) if m and m.lower() != "none"]
                    words, res["glossary_readings"] = R2.glossary_readings(
                        words, learned, y16, [_judge(m, ctx.cache) for m in names],
                        avoid=lambda a, b: any(st["a"] < b * float(fps) and st["b"] > a * float(fps) for st in ov),
                        pieces=pieces, source=source)
                except Exception as e:  # noqa: BLE001 - the words as heard
                    warn(f"the learned glossary could not be checked against the audio ({type(e).__name__}: {e})")
    res["transcriber"] = {"engine": "asr.py", "model": model, "words": len(words), "error": err, "hints": hints,
                          "runs": list(transcribe.LOG)}
    res["transcript"] = [[w.raw or w.text, round(w.start, 3), round(w.end, 3)] for w in words]
    heard_ok = y16 is not None and len(y16) > 0 and err is None      # the transcript ran (words may be none)

    # ---- unclear words double-checked against the source (caption_recheck.py) ----
    rmodel = str(getattr(cfg, "caption_recheck_model", "") or "")
    if words and rmodel.lower() not in ("", "none"):
        from . import caption_recheck as R
        if voiceover:
            pieces, source, sname = ([R.Piece(0.0, len(y16) / transcribe.SR, 0.0, 1.0)],
                                     R.audio_source(y16, transcribe.SR), "voice-over")
        else:
            (pieces, source), sname = _raw_source(ctx, cl, rp, fps), "RAW"
        opinion = (R.clear_captions(spans, span_fps) if spans else
                   _lazy_opinion(ctx, layout, comp_fps, rp, to_seq, fps) if requested == "voice" and not voiceover
                   else None)
        try:
            words, res["recheck"] = R.recheck(
                words, y16, pieces, source,
                lambda y: transcribe.transcribe_words(y, transcribe.SR, rmodel, language, ctx.cache, hints),
                captions=opinion, source_name=sname, edit_model=model, model=rmodel, extra=extra)
            log.info("captions: %d unclear words rechecked against the %s (%s), %d changed, %d still unclear",
                     res["recheck"]["rechecked"], sname, rmodel, res["recheck"]["changed"],
                     len(res["recheck"]["unclear"]))
        except Exception as e:  # noqa: BLE001 - e.g. the bigger model could not be downloaded: the first transcript
            res["recheck"] = {"error": f"{type(e).__name__}: {e}", "model": rmodel, "source": sname}
            warn(f"unclear words not rechecked with {rmodel}: {type(e).__name__}: {e}")

    if mine and y16 is not None and len(y16):
        words = _time_new_words(y16, words, mine)
    res["words_final"] = [[w.raw or w.text, round(w.start, 3), round(w.end, 3)] for w in words]    # (debug)

    # another video's stretches: the words the competitor's audio says there (as said, with their punctuation)
    from . import caption_rules
    if ov:
        words = sorted([w for w in words if not in_ov(0.5 * (w.start + w.end))] +
                       [Word(clean_text(t), s0, s1, pr, t) for st in ov for t, s0, s1, pr in st["words"]
                        if clean_text(t)], key=lambda w: w.start)

    # ---- captions ----
    cuts: list[int] = []                            # the edit's video cuts: no caption runs across one
    if xml.exists():
        try:
            cuts = edit_cuts(xml, fps)
        except Exception as e:  # noqa: BLE001 - captions without the cut rule rather than none
            warn(f"the video cuts of {EDIT_XML} could not be read ({type(e).__name__}: {e}): captions may run "
                 "across a cut")
    res["cuts"] = len(cuts)
    if mode != "competitor":               # another video starts / ends there (a competitor caption is clipped to it)
        cuts = sorted(set(cuts) | {st["a"] for st in ov} | {st["b"] for st in ov})
    from . import caption_style
    styled = False                                  # grouped in the user's style (caption_style.py)
    if mode == "competitor":
        res["competitor_style"] = caption_style.competitor_style(spans)
    follow_tls = None
    if mode == "competitor" and words and xml.exists():
        try:                                         # both edits, matched by what they play (the RAW)
            from .caption_score import Timeline, competitor_timeline
            bcl = ((getattr(ctx, "broll", None) or {}).get("cutlist") if isinstance(getattr(ctx, "broll", None),
                                                                                       dict) else None) or ctx.cutlist
            follow_tls = (competitor_timeline(bcl),
                          Timeline.from_xml(xml, [{"a": st["a"], "b": st["b"], "t0": st["t0"]} for st in ov]))
        except Exception as e:  # noqa: BLE001 - the competitor's captions copied the earlier way then
            warn(f"the competitor's captions could not be matched to my edit ({type(e).__name__}: {e})")
    if follow_tls is not None and res["competitor_style"]["follow"]:
        caps, res["competitor_notes"] = caption_style.follow_competitor(comp_spans, words, follow_tls[0],
                                                                        follow_tls[1], comp_fps, fps)
        clip_to_stretches(caps, ov)
        styled = True
        res["copied"] = [_caption_dict(c, fps) for c in caps]        # before the hard rules (debug)
    elif mode == "competitor" and (not words or (res["competitor_style"]["follow"] and follow_tls is None)):
        caps, cnotes = competitor_copy(spans, words, span_fps, span_seq, fps)
        res["competitor_notes"] = cnotes
        clip_to_stretches(caps, ov)
        res["copied"] = [_caption_dict(c, fps) for c in caps]        # before the hard rules (debug)
        res["short"] = [_caption_dict(c, fps) for c in caps if (c.end - c.start) / float(fps) < 0.1]
    elif words:
        breaks = (caption_style.competitor_breaks(comp_spans, words, follow_tls[0], follow_tls[1], comp_fps)
                  if mode == "competitor" and follow_tls is not None else set())
        caps = caption_style.style_captions(words, fps, n_seq,
                                            cuts=sorted(set(cuts) | {st["a"] for st in ov} | {st["b"] for st in ov}),
                                            mode="competitor" if mode == "competitor" else "voice",
                                            comp_breaks=breaks)
        styled = True
        rechecked = "rechecked" in (res.get("recheck") or {})        # low-confidence words: listed by the recheck
        res["flags"] = transcript_flags([w for w in words if not in_ov(w.start)], y16, transcribe.SR,
                                        min_prob=0.0 if rechecked else 0.5)
    else:
        caps = []
        warn(f"{CAPTIONS_SRT} not written: no competitor captions and no transcribed speech")
    res["styled"] = styled
    # ---- the hard rules: the final check before the file is written ----
    if caps:
        lex = caption_rules.lexicon()
        follow = mode == "competitor" and any(c.info.get("style") == "follow" for c in caps)
        caps, res["rules"] = caption_rules.enforce(
            caps, fps, "competitor" if (mode == "competitor" and (follow or not styled)) else "voice",
            words if heard_ok else None, lex, cuts=cuts, keep_groups=styled,
            open_gaps=[(st["a"], st["b"]) for st in ov])
        res["rules"]["allowlist"] = lex.allow_file
        if mode == "competitor" and not heard_ok:
            res["notes"].append("no transcript: competitor captions are split only where their own text ends a "
                                "sentence, and garbled readings are listed rather than replaced")
    res["stutters"] = list((res.get("rules") or {}).get("stutters") or [])
    # ---- timed to the speech of the final edit: each caption starts when its first word is spoken ----
    sm = getattr(ctx, "speech", None)
    if caps and sm is not None and not voiceover and xml.exists():
        try:
            from .export_xml_edl import parse_premiere_xml
            from .speech import audio_cuts
            x = parse_premiere_xml(xml)
            onsets, ref = speech_starts(x["audio"], sm, fps)
            onsets = sorted(onsets + [o for st in ov for o in st["onsets"]])     # another video: the competitor's
            ref = sorted(ref + [(t, s0, s1) for st in ov for t, s0, s1, _ in st["words"]], key=lambda w: w[1])
            own = [(w.raw or w.text, float(w.start), float(w.end)) for w in words] if heard_ok else []
            refs = [ref, own]
            a_cuts = sorted({e[3] for e in audio_cuts([dict(it, name="") for it in x["audio"] if it["start"] >= 0],
                                                      fps, int(x["duration"]))})
            gone = unheard(caps, fps, onsets, refs) if mode == "competitor" and not styled else []
            gone = [i for i in gone if not in_ov(0.5 * (caps[i].start + caps[i].end) / float(fps))]
            res["unheard_dropped"] = [_caption_dict(caps[i], fps) for i in gone]
            caps = [c for i, c in enumerate(caps) if i not in set(gone)]
            res["timed"] = time_to_speech(caps, fps, onsets, refs, a_cuts) if not styled else 0
            if mode == "competitor" and heard_ok:
                caps, filled = fill_from_transcript(caps, words, fps, n_seq, cuts, styled=styled)
                res["filled"] = [_caption_dict(c, fps) for c in filled]
                if filled and not styled:
                    res["timed"] += time_to_speech(caps, fps, onsets, refs, a_cuts)
            res["timing_off"] = timing_off(caps, fps, onsets, refs, a_cuts)
            late = [r for r in res["timing_off"] if r["off"] is not None]
            if late:
                warn(f"{len(late)} caption(s) start more than {TIMING_FRAMES} frames from their first word: "
                     + "; ".join(f"{r['start_tc']} '{r['text']}' ({r['off']:+d} frames)" for r in late[:10]))
        except Exception as e:  # noqa: BLE001 - the captions keep their own timing
            warn(f"captions not timed to the final edit's speech ({type(e).__name__}: {e})")
    res["other_video"] = []
    for st in ov:                                   # the end summary: each stretch, and how its captions were made
        mine = [c for c in caps if st["a"] <= (c.start + c.end) // 2 < st["b"]]
        how = Counter("copied from the competitor" if c.mode == "competitor" else
                      "transcribed from the competitor's audio" for c in mine)
        res["other_video"].append({"name": st["name"], "a": st["a"], "b": st["b"], "t0": st["t0"], "t1": st["t1"],
                                   "captions": len(mine), "how": dict(how), "words": len(st["words"]),
                                   "text": " ".join(c.text.replace("\n", " ") for c in mine)})
    caps = no_overlaps(caps)                         # whatever was added late: never two captions at once
    res["weak_kept"] = list((res.get("rules") or {}).get("kept_weak") or [])
    res["captions"] = [_caption_dict(c, fps) for c in caps]
    res["count"] = len(caps)
    res["by_mode"] = dict(Counter(c.mode for c in caps))
    res["parts"] = mode_parts(caps)
    res["placeholders"] = [_caption_dict(c, fps) for c in caps if c.mode == "placeholder"]
    res["over_cap"] = [_caption_dict(c, fps) for c in caps if len(c.text.replace("\n", " ")) >= HARD_CAP]
    res["stats"] = caption_stats(caps, fps) if caps else {}
    if caps:
        try:
            p = write_srt(caps, cfg.deliver / CAPTIONS_SRT, fps)
        except ValueError as e:                     # a hard rule 1-4 still broken: no file rather than a wrong one
            warn(f"{CAPTIONS_SRT} not written: {e}")
            dump_json(res, cfg.debug_dir / "captions.json")
            return res
        res["path"] = str(p)
        dump_json(res, cfg.debug_dir / "captions.json")
        log.info("captions: %d captions (%s) -> %s; %s", len(caps), mode, p,
                 caption_rules.summary_line(res.get("rules") or {}))
    return res


def glossary_entries(path: str | Path | None = None) -> list[tuple[str, str]]:
    """The learned glossary (caption_glossary.txt next to caption_allowlist.txt, written by ``match_cuts learn``):
    [(heard, written)] -- the words the user corrected in earlier videos."""
    from .caption_rules import ALLOWLIST_FILE
    gl = Path(path) if path else ALLOWLIST_FILE.with_name("caption_glossary.txt")
    out = []
    if gl.is_file():
        for line in gl.read_text(encoding="utf-8").splitlines():
            body = line.split("#", 1)[0].split("\t")[0].strip()
            if " -> " in body:
                heard, written = (x.strip() for x in body.split(" -> ", 1))
                if heard and written:
                    out.append((heard, written))
    return out


def caption_hints() -> list[str]:
    """Words the speech model is told to expect (hot words): caption_allowlist.txt and the written side of the
    learned glossary (glossary_entries)."""
    from .caption_rules import read_allowlist
    out = list(read_allowlist()) + [w for _h, w in glossary_entries()]
    return [h for h in dict.fromkeys(out) if h]


def _read_spans(ctx, layout: dict, comp_fps: Fraction) -> dict:
    """caption_ocr.read_caption_spans of the competitor, cached in WORK_DIR."""
    from . import caption_ocr
    from .common import stage_key
    info = ctx.comp_info
    wh = (int(info.display_width or info.width), int(info.display_height or info.height))
    key = stage_key("captions_spans", info.file_hash, json_key(layout), caption_ocr.SPAN_VERSION)
    return ctx.cache.json("captions_spans", key, lambda: caption_ocr.read_caption_spans(
        info.path, layout, wh, comp_fps, ctx.n_comp))


def move_spans(spans: Sequence[dict], to_seq, rp) -> tuple[list[dict], list[dict]]:
    """Caption spans (competitor frames) after the cuts of the Premiere export (a silence.Ripple: the speech-safe cuts,
    the silences and repeats): (the spans moved with the cuts, in sequence frames -- text and splits unchanged, a
    caption partly in a removed range shorter by that much -- the spans completely inside a removed range, with their
    sequence frames)."""
    moved, gone = [], []
    for d in spans:
        a0, b0 = to_seq(int(d["comp_in"])), to_seq(int(d["comp_out"]))
        a, b = a0, b0
        for st in rp.stages():
            if not st.keep(a, b):
                gone.append(dict(d, seq_in=a0, seq_out=b0))
                break
            a, b = st.map1(a), st.map1(b)
        else:
            moved.append(dict(d, comp_in=a, comp_out=b))
    return moved, gone


def _lazy_opinion(ctx, layout: dict, comp_fps: Fraction, rp=None, to_seq=None, seq_fps: Fraction | None = None):
    """The competitor's clearly read captions as the recheck's third opinion in forced voice mode: read (OCR) only
    when the recheck first asks, and only when the layout has a caption band (moved with the cuts when silences were
    cut out: ``rp`` / ``to_seq`` / ``seq_fps``)."""
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
                    got = _read_spans(ctx, layout, comp_fps).get("spans") or []
                    box["get"] = (clear_captions(move_spans(got, to_seq, rp)[0], seq_fps) if rp is not None else
                                  clear_captions(got, comp_fps))
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
