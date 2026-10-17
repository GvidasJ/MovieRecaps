"""TITLE CHECK: the competitor's headline read from its video, shown next to my title, and a warning when my title
uses too many of the same words (80 % or more, in any order).

Where the headline is: a competitor's Short shows a still band above its video window -- often the channel's round
picture with its name and @handle, then the headline in big letters, in one to three lines (white card and black
letters, or white / coloured letters on black). read_headline finds the video window (the rows that change between
frames spread over the video), reads (OCR, the tool's RapidOCR, CPU) the still band above it on a few frames, keeps the
lines that read the same on most of them, drops the channel header (an @handle line, the name next to it, anything on
their rows) and keeps the biggest block of lines that is left: the headline. Only when that band has no headline: the
band below the window, then the top half of the frame (a headline written on the picture, or on a blurred copy of it;
stricter there: the same text at the same place on 4 of 5 frames, two words or more, and marked not sure). Nothing
found: None, and last_reason() says why. About 1-2 s a video (CPU), plus about 1 s for the OCR's first start.

The words: words() lower-cases, makes ’ and ' the same, and drops punctuation, emojis and #hashtags. same_words() is
the share of the same words, order ignored, counted with repeats: common / the longer title's word count. "dont" and
"don't" are the same word, and two words of five letters or more that differ by one letter count as the same (an OCR
misreading -- "Freakiest" read "Freakiost" -- or a plural, "hospital" / "hospitals"). check() warns at WARN_SHARE
(80 %) and says it in plain words.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

WARN_SHARE = 0.80      # my title warns when at least this share of the words are the competitor's
TYPO_LETTERS = 5       # two words this long or longer that differ by one letter are the same word

# reading the headline
SAMPLES = 8            # frames looked at, spread over the video (the first and last EDGE skipped) ...
OCR_FRAMES = 5         # ... of which this many are read (OCR); a line counts when it reads the same on most of them
EDGE = 0.05
MOTION_W = 180         # the frames shrunk to this width to find what moves
MOVE_STD = 6.0         # a pixel moves when it changes more than this over the samples (grey levels, std)
WINDOW_SHARE = 0.25    # a row is part of the video window when at least this share of it moves ...
WINDOW_ROWS = 3        # ... for this many rows in a row (shrunk rows)
MIN_BAND = 0.04        # a still band above (below) the window at least this share of the frame height
WORK_W = 1080          # wider frames are read shrunk to this width (faster; boxes are given in the video's pixels)
PAD_TO = 0.8           # a band is padded below to this share of its width: the detector blows a thin band up until
#                        its short side is 736 px, and letters that big are cut in pieces ('‘Stop' 'fingering me’')
TINY = 0.5             # a box of one or two characters less than this share of its row's height is a quote mark or
#                        a speck read on its own ('”' read '99'): not part of the line
MIN_SCORE = 0.5        # the recogniser's score a line needs
SAME_TEXT = 0.8        # two readings of a line are the same when their letters match this much (difflib ratio)
BLOCK_GAP = 0.5        # lines further apart than this many line heights are separate blocks (header / headline)
OVER_PICTURE = 0.5     # no headline in a still band: the top half of the frame is read (a headline on the picture or
OVER_NEED = 4          # on a blurred copy of it), a line must read the same at the same place on 4 of the 5 frames
OVER_WORDS = 2         # and have 2 words or more (a watermark or a logo is one word); also for a band below

_OCR_LOCK = threading.Lock()     # the RapidOCR object keeps each call's settings: one call at a time
_TLS = threading.local()


# ---------------------------------------------------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------------------------------------------------

_QUOTES = str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'", "ʼ": "'", "′": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"'})
_WORD = re.compile(r"\d+(?:[.,]\d+)+|[^\W_]+(?:'[^\W_]+)*")
_HASHTAG = re.compile(r"#[^\W\d_]\w*")


def _fold(text: str) -> str:
    """Lower case, accents dropped (é -> e), every apostrophe and quote mark the plain one."""
    t = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTES).casefold()
    return "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))


def words(text: str) -> list[str]:
    """The words of a title: lower case, ’ -> ', accents dropped, '&' -> 'and'; punctuation, quotes, emojis and
    #hashtags dropped (#shorts is not part of what the title says; '#1' stays '1'); an apostrophe inside a word
    stays ("don't"), a hyphen splits ("well-known" -> well, known)."""
    return _WORD.findall(_HASHTAG.sub(" ", _fold(text)).replace("&", " and "))


def _as_words(x: str | Sequence[str]) -> list[str]:
    return words(x) if isinstance(x, str) else [w for w in x if w]


def one_letter_apart(a: str, b: str) -> bool:
    """Two different words of TYPO_LETTERS letters or more, one letter changed, added or dropped (letters only;
    apostrophes are not letters: "winslet's" / "winslet" is one letter)."""
    a, b = a.replace("'", ""), b.replace("'", "")
    if a == b or min(len(a), len(b)) < TYPO_LETTERS or abs(len(a) - len(b)) > 1:
        return False
    if not (a.isalpha() and b.isalpha()):
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def _bare(w: str) -> str:
    return w.replace("'", "")


def _same(a: str, b: str, typos: bool) -> bool:
    """The same word: equal but for apostrophes ("dont" / "don't"), or one letter apart (typos)."""
    return _bare(a) == _bare(b) or (typos and one_letter_apart(a, b))


def matched_pairs(mine: Sequence[str], theirs: Sequence[str], typos: bool = True) -> list[tuple[int, int]]:
    """(index in mine, index in theirs) of the words both have, each word used once: equal words paired first, then
    the words left pair with words one letter apart -- re-pairing where that makes more pairs (the largest
    matching: the count is what the share needs)."""
    match_of: dict[int, int] = {}            # theirs index -> mine index

    def take(i: int, seen: set[int]) -> bool:
        for j, w in enumerate(theirs):
            if j in seen or not _same(mine[i], w, typos):
                continue
            seen.add(j)
            if j not in match_of or take(match_of[j], seen):
                match_of[j] = i
                return True
        return False

    free = set(range(len(mine)))
    for i in range(len(mine)):               # equal words first
        for j, w in enumerate(theirs):
            if j not in match_of and _bare(mine[i]) == _bare(w):
                match_of[j] = i
                free.discard(i)
                break
    for i in sorted(free):
        take(i, set())
    return sorted((i, j) for j, i in match_of.items())


def same_words(mine: str | Sequence[str], theirs: str | Sequence[str], typos: bool = True) -> float:
    """The share of the same words, order ignored, counted with repeats: |common| / max(len(mine), len(theirs));
    0 when either has no words. Words one letter apart (TYPO_LETTERS+ letters) count as the same unless typos=False."""
    a, b = _as_words(mine), _as_words(theirs)
    if not a or not b:
        return 0.0
    return len(matched_pairs(a, b, typos)) / max(len(a), len(b))


@dataclass
class TitleCheck:
    share: float                  # 0..1: the share of the same words
    warn: bool                    # share >= WARN_SHARE
    common: list[str]             # the words both have, in my title's order (my spelling)
    message: str                  # plain words for the window
    other_order: bool = False     # the common words are in another order than the competitor's
    mine: list[str] = field(default_factory=list)
    theirs: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        """The share in whole per cent, rounded down (79.6 % shows 79, never the 80 that would warn)."""
        return int(self.share * 100 + 1e-9)


def check(my_title: str, competitor_headline: str | HeadlineRead | None) -> TitleCheck:
    """My title against the competitor's headline (its text, a HeadlineRead or None)."""
    theirs_text = competitor_headline.text if isinstance(competitor_headline, HeadlineRead) else \
        str(competitor_headline or "")
    a, b = words(my_title), words(theirs_text)
    if not b:
        return TitleCheck(0.0, False, [], "no competitor headline to compare with", mine=a, theirs=b)
    if not a:
        return TitleCheck(0.0, False, [], "type your title to compare it with the competitor's headline", mine=a,
                          theirs=b)
    pairs = matched_pairs(a, b)
    share = len(pairs) / max(len(a), len(b))
    warn = share >= WARN_SHARE - 1e-9
    js = [j for _, j in pairs]
    other_order = js != sorted(js)
    out = TitleCheck(share, warn, [a[i] for i, _ in pairs], "", other_order, a, b)
    if warn:
        out.message = (f"{out.percent} % the same words as the competitor's headline"
                       + (" (in another order)" if other_order else "") + " -- change your title more")
    else:
        out.message = f"{out.percent} % the same words as the competitor's headline -- different enough"
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the OCR text, tidied
# ---------------------------------------------------------------------------------------------------------------------

_KEEP_SYMBOLS = set("&$%#+£€/°")
_CLOSING = re.compile(r"[.!?…\"')]+")       # a token of these after a word belongs to it ('sense "' -> 'sense"')
_OPENING = re.compile(r"[\"'(]+")           # ... and of these before a word


def _keep_char(ch: str) -> bool:
    """Letters (not Chinese / Japanese / Korean: what the recogniser reads an emoji as), digits, punctuation, space."""
    if ch == " ":
        return True
    if ord(ch) >= 0x2E80:                     # CJK, kana, hangul, emoji, ...: never a word of an English headline
        return False
    cat = unicodedata.category(ch)
    return cat[0] in "LNP" or ch in _KEEP_SYMBOLS


def tidy(text: str) -> str:
    """One OCR line (or the joined headline) cleaned: one kind of quotes and apostrophes, single spaces, no space
    before , . ! ? : ; and no emoji garbage (symbols the recogniser invents for an emoji, Chinese characters, a lone
    '@' at an end); a closing ' after an opening " is the closing "."""
    t = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTES).replace("''", '"')
    t = "".join(ch if _keep_char(ch) else " " for ch in t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.!?:;%])", r"\1", t)
    toks = t.split(" ") if t else []
    while toks and not _alnum(toks[-1]):        # tokens without a letter or digit at the end: emoji garbage ...
        last = toks.pop()
        if toks and _CLOSING.fullmatch(last):   # ... unless it closes the sentence or a quote
            toks[-1] += last
            break
    while toks and not _alnum(toks[0]):         # ... and at the start
        first = toks.pop(0)
        if toks and _OPENING.fullmatch(first):
            toks[0] = first + toks[0]
            break
    t = " ".join(toks)
    end = t.rstrip(".!?…")
    if len(end) > 2 and end[0] in "'\"" and end[-1] in "'\"" and end[0] != end[-1]:
        t = '"' + end[1:-1] + '"' + t[len(end):]    # a quote opened and closed by different marks: one misread
    return t


def _alnum(tok: str) -> bool:
    return re.search(r"[^\W_]", tok) is not None


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in str(text))


def _key(text: str) -> str:
    """Letters and digits only, lower case: two readings of one line compare on this."""
    return re.sub(r"[^a-z0-9]", "", _fold(text))


_HANDLE = re.compile(r"@[\w.]{2,}")
NAME_WORDS = 3         # a channel's name (or what sits next to its @handle) has at most this many words


def is_handle(text: str) -> bool:
    """A channel's @handle line: an @handle and at most NAME_WORDS other words ('@SILLYJOKERS', 'VS Viral Sanity
    @ViralSanity101'). A lone '@' is an emoji misread; a long line that mentions someone ('When @MrBeast gave away a
    car') is a headline."""
    t = str(text)
    return bool(_HANDLE.search(t)) and len(words(_HANDLE.sub(" ", t))) <= NAME_WORDS


# ---------------------------------------------------------------------------------------------------------------------
# reading the headline
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class HeadlineRead:
    text: str                                  # the headline, its lines joined with a space (tidied)
    lines: list[str]                           # its lines, top to bottom (tidied)
    score: float                               # the recogniser's mean score of those lines (0..1)
    frames: int                                # frames the lines read the same on ...
    frames_read: int                           # ... of the frames read (OCR)
    box: tuple[int, int, int, int]             # x, y, w, h of the headline in the video's pixels
    seconds: float = 0.0                       # the time the reading took
    where: str = "above the picture"           # "below the picture", "on the picture", "on a still frame"
    sure: bool = True                          # False "on the picture": text that stays on the picture may be a sign
    #                                            or a show's logo rather than the headline -- show it as a guess
    others: list[str] = field(default_factory=list)   # the other still lines (channel name, @handle, logo text)


@dataclass
class _Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass
class _Stable(_Line):
    frames: int = 0                            # frames this line read the same on


def last_reason() -> str:
    """Why the last read_headline call of this thread found no headline ("" after a headline was read)."""
    return str(getattr(_TLS, "reason", ""))


def _none(reason: str, t0: float) -> None:
    _TLS.reason = reason
    log.info("title check: no headline (%s, %.1f s)", reason, time.perf_counter() - t0)
    return None


def _engine() -> Any:
    """The tool's OCR engine (match_cuts.caption_ocr: RapidOCR, CPU), made once per process."""
    from match_cuts import caption_ocr
    return caption_ocr.engine()


def warm_up() -> None:
    """Load the OCR models now (about 1 s, once per process) -- e.g. in the background when the window opens, so
    the first read_headline does not wait for it."""
    with _OCR_LOCK:
        _engine()


class _QuietEmpty(logging.Filter):
    """RapidOCR warns 'The text detection result is empty' on every band without text: not news here."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "detection result is empty" not in record.getMessage()


def ocr_lines(img: Any, eng: Any = None) -> list[_Line]:
    """Detection + recognition on one image: its text lines (pixels of the image), score MIN_SCORE or more."""
    quiet = _QuietEmpty()
    rlog = logging.getLogger("RapidOCR")
    with _OCR_LOCK:
        kind, e = eng or _engine()
        rlog.addFilter(quiet)
        try:
            if kind == "rapidocr":
                res = e(img, use_det=True, use_cls=False, use_rec=True)
                boxes, txts, scores = (getattr(res, k, None) for k in ("boxes", "txts", "scores"))
            else:                                              # rapidocr_onnxruntime: ([box, text, score], ...)
                res = e(img, use_det=True, use_cls=False, use_rec=True)
                rows = (res[0] if isinstance(res, tuple) else res) or []
                boxes = [r[0] for r in rows]
                txts = [r[1] for r in rows]
                scores = [r[2] for r in rows]
        finally:
            rlog.removeFilter(quiet)
    out = []
    if boxes is None or txts is None or scores is None:
        return out
    for b, tx, sc in zip(boxes, txts, scores):
        if not str(tx).strip() or float(sc) < MIN_SCORE:
            continue
        xs = [float(p[0]) for p in b]
        ys = [float(p[1]) for p in b]
        out.append(_Line(min(xs), min(ys), max(xs), max(ys), " ".join(str(tx).split()), float(sc)))
    return out


def merge_rows(lines: Sequence[_Line]) -> list[_Line]:
    """Boxes on the same row (overlapping by half the smaller height) are one line, left to right; a box of one or
    two characters much lower than the row (TINY) is dropped."""
    rows: list[list[_Line]] = []
    for ln in sorted(lines, key=lambda l: l.cy):
        for r in rows:
            top, bot = min(l.y0 for l in r), max(l.y1 for l in r)
            if min(bot, ln.y1) - max(top, ln.y0) >= 0.5 * min(ln.h, bot - top):
                r.append(ln)
                break
        else:
            rows.append([ln])
    out = []
    for r in rows:
        tall = max(l.h for l in r)
        r = sorted((l for l in r if not (len(l.text.replace(" ", "")) <= 2 and l.h < TINY * tall)),
                   key=lambda l: l.x0)
        n = sum(max(1, len(l.text)) for l in r)
        out.append(_Line(min(l.x0 for l in r), min(l.y0 for l in r), max(l.x1 for l in r), max(l.y1 for l in r),
                         " ".join(l.text for l in r), sum(l.score * max(1, len(l.text)) for l in r) / n))
    return sorted(out, key=lambda l: l.cy)


def _same_place(a: _Line, b: _Line) -> bool:
    ox = min(a.x1, b.x1) - max(a.x0, b.x0)
    return abs(a.cy - b.cy) < 0.5 * max(a.h, b.h) and ox >= 0.5 * min(a.w, b.w)


def _similar(a: str, b: str) -> bool:
    ka, kb = _key(a), _key(b)
    return ka == kb or (bool(ka) and bool(kb) and difflib.SequenceMatcher(None, ka, kb, autojunk=False).ratio()
                        >= SAME_TEXT)


class _Votes:
    """The lines of every frame read, grouped by place; a line is still when it reads the same on most frames."""

    def __init__(self) -> None:
        self.groups: list[list[tuple[int, _Line]]] = []

    def add(self, k: int, lines: Sequence[_Line]) -> None:
        for ln in lines:
            for g in self.groups:
                if all(f != k for f, _ in g) and _same_place(g[0][1], ln):
                    g.append((k, ln))
                    break
            else:
                self.groups.append([(k, ln)])

    @staticmethod
    def majority(g: list[tuple[int, _Line]]) -> tuple[str, list[_Line]]:
        """The group's text (the most frequent reading of the readings alike, ties to the higher score) and the
        readings that agree with it."""
        best: list[_Line] = []
        for _, ln in g:
            agree = [m for _, m in g if _similar(ln.text, m.text)]
            if len(agree) > len(best):
                best = agree
        counts: dict[str, list[float]] = {}
        for m in best:
            counts.setdefault(m.text, []).append(m.score)
        text = max(counts.items(), key=lambda kv: (len(kv[1]), sum(kv[1])))[0]
        return text, best

    def settled(self, read: int, total: int, need: int) -> bool:
        """No group can change its answer with the frames still to read."""
        left = total - read
        return read >= need and all(len(self.majority(g)[1]) >= need or len(g) + left < need for g in self.groups)

    def stable(self, need: int) -> list[_Stable]:
        out = []
        for g in self.groups:
            text, agree = self.majority(g)
            if len(agree) < need:
                continue
            med = sorted(agree, key=lambda l: l.cy)[len(agree) // 2]
            out.append(_Stable(med.x0, med.y0, med.x1, med.y1, text,
                               sum(l.score for l in agree) / len(agree), len(agree)))
        return sorted(out, key=lambda l: l.cy)


def pick_headline(lines: Sequence[_Line]) -> tuple[list[_Line], list[_Line]]:
    """(the headline's lines, the other lines) of the still lines of a band, top to bottom: the channel header
    goes (every @handle line, the name line right above it -- NAME_WORDS words at most, starting where the handle
    starts or centred on it -- and anything on their rows: the round picture's text, a badge), lines with fewer than
    two letters go (emoji garbage), the rest is split into blocks where the gap between two lines is more than
    BLOCK_GAP line heights, and the block with the most text (width x height) is the headline."""
    lines = sorted(lines, key=lambda l: l.cy)
    drop: set[int] = set()
    handles = [i for i, l in enumerate(lines) if is_handle(l.text)]
    drop.update(handles)
    for i in handles:
        h = lines[i]
        for j, l in enumerate(lines):
            if j in drop or l.cy >= h.cy or len(words(l.text)) > NAME_WORDS:
                continue
            near = h.y0 - l.y1 <= 0.5 * h.h
            aligned = abs(l.x0 - h.x0) <= 0.6 * max(l.h, h.h) or abs(l.cx - h.cx) <= 0.6 * max(l.h, h.h)
            if near and aligned:
                drop.add(j)
    if drop:
        z0 = min(lines[i].y0 for i in drop)
        z1 = max(lines[i].y1 for i in drop)
        drop.update(j for j, l in enumerate(lines) if z0 <= l.cy <= z1)
    rest = [l for j, l in enumerate(lines) if j not in drop and _letters(tidy(l.text)) >= 2]
    blocks: list[list[_Line]] = []
    for l in rest:
        if blocks:
            prev = blocks[-1][-1]
            if l.y0 - prev.y1 <= BLOCK_GAP * (l.h + prev.h) / 2.0:
                blocks[-1].append(l)
                continue
        blocks.append([l])
    if not blocks:
        return [], list(lines)
    best = max(blocks, key=lambda b: sum(l.w * l.h for l in b))
    return best, [l for l in lines if all(l is not m for m in best)]


def _sample(path: Path, steps: _Steps | None = None) -> tuple[list, list, tuple[int, int]]:
    """(small grey frames for the motion, full frames to read, (width, height)): SAMPLES frames spread between EDGE
    and 1 - EDGE of the video, OCR_FRAMES of them kept whole."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return [], [], (0, 0)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if n <= 0 or W <= 0 or H <= 0:
            return [], [], (W, H)
        picks = [int(round((EDGE + (1 - 2 * EDGE) * i / (SAMPLES - 1)) * (n - 1))) for i in range(SAMPLES)]
        keep = {int(round(i * (SAMPLES - 1) / (OCR_FRAMES - 1))) for i in range(OCR_FRAMES)}
        small, full = [], []
        for i, k in enumerate(picks):
            cap.set(cv2.CAP_PROP_POS_FRAMES, k)
            ok, img = cap.read()
            if ok and img is not None:
                H, W = img.shape[:2]
                sh = max(1, int(round(H * MOTION_W / W)))
                small.append(cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (MOTION_W, sh),
                                        interpolation=cv2.INTER_AREA))
                if i in keep:
                    full.append(img)
            if steps is not None:
                steps.step()
        return small, full, (W, H)
    finally:
        cap.release()


def window_rows(small: Sequence[Any], H: int) -> tuple[int, int] | None:
    """[top, bottom) of the video window in the video's pixel rows: the rows where at least WINDOW_SHARE of the
    pixels change over the samples (WINDOW_ROWS shrunk rows in a row); None when nothing moves."""
    import numpy as np
    shapes = {s.shape for s in small}
    if len(small) < 2 or len(shapes) != 1:
        return None
    std = np.stack(small).astype(np.float32).std(axis=0)
    share = (std > MOVE_STD).mean(axis=1)
    on = share >= WINDOW_SHARE
    sh = len(share)
    runs = [r for r in range(sh - WINDOW_ROWS + 1) if on[r:r + WINDOW_ROWS].all()]
    if not runs:
        return None
    top, bot = runs[0], runs[-1] + WINDOW_ROWS            # shrunk rows [top, bot)
    return int(top * H / sh), min(H, int(np.ceil(bot * H / sh)))


def _band_image(frame: Any, y0: int, y1: int) -> tuple[Any, float]:
    """The band to read, wider frames shrunk to WORK_W, padded below to PAD_TO of its width (a thin band would be
    blown up far too much by the detector); and the scale used."""
    import cv2
    import numpy as np
    band = frame[y0:y1]
    s = 1.0
    if band.shape[1] > WORK_W:
        s = WORK_W / float(band.shape[1])
        band = cv2.resize(band, (WORK_W, max(1, int(round(band.shape[0] * s)))), interpolation=cv2.INTER_AREA)
    need = int(PAD_TO * band.shape[1])
    if band.shape[0] < need:
        fill = np.median(band.reshape(-1, band.shape[2]), axis=0).astype(band.dtype)
        pad = np.empty((need - band.shape[0], band.shape[1], band.shape[2]), band.dtype)
        pad[:] = fill
        band = np.vstack([band, pad])
    return np.ascontiguousarray(band), s


class _Steps:
    """The progress callback fed step by step: frames looked at, then frames read; it never shows 'done' before the
    end (how many frames get read is known only then)."""

    def __init__(self, progress: Callable[[int, int], None] | None) -> None:
        self.progress = progress
        self.total = SAMPLES + OCR_FRAMES
        self.done = 0

    def step(self) -> None:
        self.done += 1
        if self.progress is not None:
            self.total = max(self.total, self.done + 1)
            self.progress(self.done, self.total)

    def finish(self) -> None:
        if self.progress is not None:
            self.progress(self.total, self.total)


def _read_band(frames: Sequence[Any], y0: int, y1: int, eng: Any, steps: _Steps, need: int,
               skip_above: float = 0.0) -> tuple[list[_Stable], int]:
    """(the lines of the band [y0, y1) that read the same at the same place on ``need`` frames or more, in the
    video's pixels; the frames read). Lines centred above ``skip_above`` are not looked at."""
    votes = _Votes()
    read = 0
    for k, img in enumerate(frames):
        band, s = _band_image(img, y0, y1)
        cut = (y1 - y0) * s
        lines = []
        for l in ocr_lines(band, eng):
            if l.cy >= cut:
                continue                         # mostly in the padding: a line cut by the window's edge
            ln = _Line(l.x0 / s, y0 + l.y0 / s, l.x1 / s, y0 + l.y1 / s, l.text, l.score)
            if ln.cy >= skip_above:
                lines.append(ln)
        votes.add(k, merge_rows(lines))
        read += 1
        steps.step()
        if votes.settled(read, len(frames), need):
            break
    return votes.stable(need), read


def _bands(win: tuple[int, int] | None, H: int) -> list[tuple[str, int, int]]:
    """(where, y0, y1) of the still bands to read: above the video window, then below it (the whole frame when
    nothing moves)."""
    if win is None:
        return [("on a still frame", 0, H)]
    top, bottom = win
    out = []
    if top >= MIN_BAND * H:
        out.append(("above the picture", 0, top))
    if H - bottom >= MIN_BAND * H:
        out.append(("below the picture", bottom, H))
    return out


def read_headline(video_path: str | Path, progress: Callable[[int, int], None] | None = None,
                  eng: Any = None) -> HeadlineRead | None:
    """The competitor's headline read from its video (see the module text), or None -- last_reason() says why.
    ``progress(done, total)`` after every frame looked at and every frame read; ``eng`` an OCR engine as
    match_cuts.caption_ocr.engine() gives it (the tool's, by default)."""
    t0 = time.perf_counter()
    _TLS.reason = ""
    steps = _Steps(progress)
    try:
        return _read_headline(Path(video_path), eng, steps, t0)
    finally:
        steps.finish()


def _read_headline(path: Path, eng: Any, steps: _Steps, t0: float) -> HeadlineRead | None:
    if not path.is_file():
        return _none(f"no video at {path}", t0)
    small, frames, (W, H) = _sample(path, steps)
    if len(small) < 3 or not frames:
        return _none("the video could not be read", t0)
    win = window_rows(small, H)
    need = len(frames) // 2 + 1
    others: list[str] = []
    looked_above = 0.0
    for where, y0, y1 in _bands(win, H):
        stable, read = _read_band(frames, y0, y1, eng, steps, need)
        if y0 == 0:
            looked_above = float(y1)
        out = _headline_of(stable, read, where, others, t0)
        if out is not None:
            return out
    if win is not None:
        # no headline in a still band: it may be written on the picture itself (or on a blurred copy of it behind
        # the window) -- the top half of the frame, stricter: the same place and text on OVER_NEED frames, words
        stable, read = _read_band(frames, 0, int(OVER_PICTURE * H), eng, steps, min(len(frames), OVER_NEED),
                                  skip_above=looked_above)
        out = _headline_of(stable, read, "on the picture", others, t0)
        if out is not None:
            return out
    if others:
        return _none("no headline: the only text that stays on screen is the channel's name, handle or logo ("
                     + "; ".join(others[:4]) + ")", t0)
    if win is not None and not _bands(win, H):
        return _none("no headline: the picture fills the frame and no text stays on it", t0)
    return _none("no text that stays on screen above, below or on the picture", t0)


def _headline_of(stable: Sequence[_Stable], read: int, where: str, others: list[str],
                 t0: float) -> HeadlineRead | None:
    """The HeadlineRead of a band's still lines (pick_headline), or None (the lines left are added to ``others``).
    Below or on the picture, the headline needs OVER_WORDS words."""
    head, rest = pick_headline(stable)
    for l in rest:
        if tidy(l.text) and tidy(l.text) not in others:
            others.append(tidy(l.text))
    if not head:
        return None
    lines = [tidy(l.text) for l in head]
    text = tidy(" ".join(lines))
    if where in ("below the picture", "on the picture") and len(words(text)) < OVER_WORDS:
        if text not in others:
            others.append(text)
        return None
    x0, y0 = min(l.x0 for l in head), min(l.y0 for l in head)
    x1, y1 = max(l.x1 for l in head), max(l.y1 for l in head)
    out = HeadlineRead(text=text, lines=lines, score=round(sum(l.score for l in head) / len(head), 3),
                       frames=min(getattr(l, "frames", read) for l in head), frames_read=read,
                       box=(int(x0), int(y0), int(round(x1 - x0)), int(round(y1 - y0))),
                       seconds=round(time.perf_counter() - t0, 2), where=where,
                       sure=where in ("above the picture", "below the picture"), others=list(others))
    _TLS.reason = ""
    log.info("title check: %r (%s, score %.2f, %d/%d frames, %.1f s)", out.text, where, out.score, out.frames,
             out.frames_read, out.seconds)
    return out
