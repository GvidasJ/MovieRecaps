"""The competitor's burned-in captions, read exactly (captions mode ``competitor``, see captions.py): every caption's
first and last frame and its text as written on screen (words, capitals, punctuation), all read from the picture.
captions.py then writes them by my rules (caption_rules.py).

Engine: RapidOCR (``rapidocr`` 3.x, or the older ``rapidocr-onnxruntime``: ONNX models inside the wheel, pip-only
on Windows, no system installs).
Only its recogniser runs, on a clean image this module builds per text line (the caption's letters drawn black on
white), which is fast (~15 ms) and much more reliable on video than detection on the raw picture.

Where to look: the caption band of the layout (the ``captions`` zone layout.py measured, the whole frame width;
static title / logo / watermark zones are masked), on every frame. How: read_caption_spans and the section comment
above it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

from .common import log

MIN_SCORE = 0.5         # a frame's reading counts when the recogniser is at least this sure
_ENGINE: list = []


def available() -> str | None:
    """None when an OCR engine can be imported, else the reason (with the pip command)."""
    errs = []
    for mod in ("rapidocr", "rapidocr_onnxruntime"):
        try:
            __import__(mod)
            return None
        except Exception as e:  # noqa: BLE001 - any import failure means: not usable here
            errs.append(f"{mod}: {type(e).__name__}: {e}")
    return ("RapidOCR is not installed (" + "; ".join(errs) + "); install it with `pip install --no-deps rapidocr` "
            "and `pip install onnxruntime pyclipper shapely pyyaml pillow six tqdm omegaconf requests colorlog`")


def engine() -> Any:
    """(kind, engine): RapidOCR 3.x (``rapidocr``, any Python) or the older ``rapidocr_onnxruntime`` (Python <= 3.12)."""
    if not _ENGINE:
        import logging
        try:
            from rapidocr import RapidOCR
            eng, kind = RapidOCR(params={"Global.log_level": "warning"}), "rapidocr"
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR
            eng, kind = RapidOCR(), "rapidocr_onnxruntime"
        for name in ("RapidOCR", "rapidocr", "rapidocr_onnxruntime"):
            logging.getLogger(name).setLevel(logging.WARNING)
        _ENGINE.append((kind, eng))
    return _ENGINE[0]


def engine_name() -> str:
    try:
        kind = engine()[0]
        import importlib.metadata as md
        return f"{kind} {md.version(kind.replace('_', '-'))}"
    except Exception:  # noqa: BLE001 - informational only
        return "RapidOCR"


def recognise_line(img: np.ndarray, eng: Any = None) -> tuple[str, float]:
    """Text and score of one prepared line image (recogniser only)."""
    kind, e = eng or engine()
    out = e(img, use_det=False, use_cls=False, use_rec=True)
    if kind == "rapidocr":
        txts, scores = tuple(getattr(out, "txts", None) or ()), tuple(getattr(out, "scores", None) or ())
        return (str(txts[0]), float(scores[0])) if txts and scores else ("", 0.0)
    res = out[0] if isinstance(out, tuple) else out
    return (str(res[0][0]), float(res[0][1])) if res and res[0] else ("", 0.0)


# ---------------------------------------------------------------------------------------------
# One frame
# ---------------------------------------------------------------------------------------------

def caption_line(mask: np.ndarray, glyph_h: float) -> np.ndarray:
    """The caption's own line(s) of text: letter-sized components grouped by height and chained left to right
    with word-sized gaps; the line(s) with the most letter area win, and small marks (dots, apostrophes,
    asterisks, punctuation) are kept only inside them. Blobs elsewhere in the band are dropped."""
    import cv2
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return mask
    gh = max(4.0, float(glyph_h))
    x, y, w, h, area = (st[1:, i].astype(float) for i in range(5))
    cy = y + h / 2.0
    big = np.nonzero((h >= 0.25 * gh) & (h <= 1.8 * gh))[0]
    if not len(big):
        return np.zeros_like(mask)
    # rows of letters: anchors whose vertical centres are within 0.6 glyph heights
    order = big[np.argsort(cy[big])]
    rows: list[list[int]] = [[int(order[0])]]
    for i in order[1:]:
        if cy[i] - float(np.median(cy[rows[-1]])) > 0.6 * gh:
            rows.append([int(i)])
        else:
            rows[-1].append(int(i))
    groups = []                                   # (letter area, members, x0, x1, top, bottom)
    for r in rows:
        r = sorted(r, key=lambda i: x[i])
        cur = [r[0]]
        for i in r[1:]:
            if x[i] - max(x[j] + w[j] for j in cur) > 1.2 * gh:
                groups.append(cur)
                cur = [i]
            else:
                cur.append(i)
        groups.append(cur)
    scored = sorted(((float(area[g].sum()), g) for g in map(np.array, groups)), key=lambda t: -t[0])
    best = scored[0][0]
    keep = np.zeros(n, bool)
    for a, g in scored:
        if a < 0.3 * best:
            break
        x0, x1 = float(x[g].min()), float((x[g] + w[g]).max())
        t0, t1 = float(y[g].min()), float((y[g] + h[g]).max())
        keep[g + 1] = True
        small = (cy >= t0 - 0.4 * gh) & (cy <= t1 + 0.4 * gh) & (x + w >= x0 - 0.8 * gh) & (x <= x1 + 0.8 * gh) \
            & (h < 1.8 * gh)
        keep[np.nonzero(small)[0] + 1] = True
    return keep[lab]


def text_lines(mask: np.ndarray, glyph_h: float) -> list[tuple[int, int, int, int]]:
    """(y0, y1, x0, x1) per text line: row runs of the mask split by empty rows."""
    rows = np.nonzero(mask.any(axis=1))[0]
    if not len(rows):
        return []
    gap = max(2, int(round(0.25 * glyph_h)))
    runs: list[list[int]] = [[int(rows[0]), int(rows[0])]]
    for r in rows[1:]:
        if r - runs[-1][1] > gap:
            runs.append([int(r), int(r)])
        else:
            runs[-1][1] = int(r)
    out = []
    for y0, y1 in runs:
        if y1 - y0 + 1 < 0.35 * glyph_h:
            continue                       # specks, not a line of text
        cols = np.nonzero(mask[y0:y1 + 1].any(axis=0))[0]
        out.append((y0, y1 + 1, int(cols[0]), int(cols[-1]) + 1))
    return out


def line_image(mask: np.ndarray, box: tuple[int, int, int, int], target_h: int = 40) -> np.ndarray:
    """The line drawn black on white, padded and scaled to ``target_h`` px text height (BGR for the recogniser)."""
    import cv2
    y0, y1, x0, x1 = box
    h = y1 - y0
    pad = max(4, int(round(0.35 * h)))
    sub = mask[y0:y1, x0:x1]
    img = np.full((h + 2 * pad, (x1 - x0) + 2 * pad), 255, np.uint8)
    img[pad:pad + h, pad:pad + (x1 - x0)][sub] = 0
    s = target_h / float(max(1, h))
    img = cv2.resize(img, (max(1, int(round(img.shape[1] * s))), max(1, int(round(img.shape[0] * s)))),
                     interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


@dataclass
class FrameRead:
    k: int
    text: str = ""
    score: float = 0.0
    area: int = 0

    @property
    def readable(self) -> bool:
        return bool(self.text) and self.score >= MIN_SCORE and bool(re.search(r"\w", self.text))


def recognise(mask: np.ndarray, glyph_h: float, eng: Any = None) -> tuple[str, float]:
    """Text of a letter mask (lines joined with a line break) and the recogniser's lowest line score."""
    eng = eng or engine()
    texts, scores = [], []
    for box in text_lines(mask, glyph_h):
        text, score = recognise_line(line_image(mask, box), eng)
        if text.strip():
            texts.append(" ".join(text.split()))
            scores.append(score)
    return "\n".join(texts), (min(scores) if scores else 0.0)


# ---------------------------------------------------------------------------------------------
# The caption band
# ---------------------------------------------------------------------------------------------

def norm_text(t: str) -> str:
    """Letters and digits only, lower case, accents dropped (a stray mark read as an accent is OCR noise)."""
    import unicodedata
    t = "".join(ch for ch in unicodedata.normalize("NFKD", str(t)) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", t.lower())


OCR_ALIKE = (("rn", "m"), ("vv", "w"), ("cl", "d"), ("1", "l"), ("i", "l"), ("0", "o"), ("5", "s"))


def ocr_key(t: str) -> str:
    """norm_text with the letters OCR mixes up in small text made the same (rn / m, vv / w, i / l / 1, 0 / o):
    the misread first frame of a pop-in ('bullds a tearn') compares like its caption ('builds a team')."""
    k = norm_text(t)
    for a, b in OCR_ALIKE:
        k = k.replace(a, b)
    return k


@dataclass
class Band:
    """Where the captions are, in competitor pixels: the rows read (the whole frame width, so letters never touch
    the crop's side), the columns kept, the glyph height and the static zones to ignore."""
    x: int
    y: int
    w: int
    h: int
    glyph_h: float
    ignore: list[tuple[int, int, int, int]] = field(default_factory=list)
    keep: tuple[int, int] | None = None     # columns [x0, x1) (frame px) where caption letters may be
    events: list[tuple[int, int, float, float, float, float]] = field(default_factory=list)  # (in, out, x, y, w, h)


def band_from_layout(layout: Any, frame_wh: tuple[int, int]) -> tuple[Band | None, list[tuple[int, int]]]:
    """(caption band, caption event spans [comp_in, comp_out)) from a layout.Layout (or its dict)."""
    lay = layout.to_dict() if hasattr(layout, "to_dict") else dict(layout or {})
    W, H = frame_wh
    events = [c for c in lay.get("captions") or [] if str(c.get("type", "captions")) == "captions"]
    if not events:
        return None, []
    zones = lay.get("zones") or []
    cz = next((z for z in zones if str(z.get("type")) == "captions"), None)
    m = re.search(r"glyph height (\d+(?:\.\d+)?) px", str((cz or {}).get("notes", "")))
    glyph_h = float(m.group(1)) if m else float(np.median([float(e["h"]) for e in events])) * 0.75
    if cz is not None:
        x0, y0, x1, y1 = float(cz["x"]), float(cz["y"]), float(cz["x"]) + float(cz["w"]), float(cz["y"]) + float(cz["h"])
    else:
        x0 = min(float(e["x"]) for e in events)
        y0 = min(float(e["y"]) for e in events)
        x1 = max(float(e["x"]) + float(e["w"]) for e in events)
        y1 = max(float(e["y"]) + float(e["h"]) for e in events)
    px, py = max(8.0, 2.0 * glyph_h), max(8.0, 1.2 * glyph_h)
    by0, by1 = int(max(0, np.floor(y0 - py))), int(min(H, np.ceil(y1 + py)))
    keep = (int(max(0, np.floor(x0 - px))), int(min(W, np.ceil(x1 + px))))
    ignore = []
    for z in zones:
        if str(z.get("type")) == "captions" or z.get("x") is None:
            continue
        zx, zy, zw, zh = float(z["x"]), float(z["y"]), float(z["w"]), float(z["h"])
        if any(zx <= float(e["x"]) + float(e["w"]) / 2 <= zx + zw and zy <= float(e["y"]) + float(e["h"]) / 2 <= zy + zh
               for e in events):
            continue                 # a zone over the caption events themselves (a misread overlay): not ignored
        ignore.append((int(z["x"]), int(z["y"]), int(z["w"]), int(z["h"])))
    spans = sorted((int(e["comp_in"]), int(e["comp_out"])) for e in events)
    boxes = sorted((int(e["comp_in"]), int(e["comp_out"]), float(e["x"]), float(e["y"]), float(e["w"]), float(e["h"]))
                   for e in events)
    return Band(0, by0, W, by1 - by0, glyph_h, ignore, keep, boxes), spans


def _ignore_mask(band: Band) -> np.ndarray | None:
    m = np.zeros((band.h, band.w), bool)
    if band.keep is not None:
        m[:, :max(0, band.keep[0] - band.x)] = True
        m[:, max(0, band.keep[1] - band.x):] = True
    for x, y, w, h in band.ignore:
        xa, ya = max(0, x - band.x), max(0, y - band.y)
        xb, yb = min(band.w, x + w - band.x), min(band.h, y + h - band.y)
        if xb > xa and yb > ya:
            m[ya:yb, xa:xb] = True
    return m if m.any() else None


# ---------------------------------------------------------------------------------------------
# Caption spans (competitor mode): the competitor's captions read exactly -- WHEN each one is on screen (to the
# frame) and WHAT it says (as written: words, capitals, punctuation), both read from the picture
# ---------------------------------------------------------------------------------------------
#
# Caption styles differ (white text with a black outline or a soft shadow, cream text, a word highlighted in another
# colour, a pop-in that grows the text over its first frames, ...). What they share: bright letters with dark pixels
# right next to them, in a fill colour that repeats over the whole video. The fill colour is learned from the band;
# a frame's caption layer is the bright pixels of that colour next to dark ones, on the caption's own line. Some
# competitors give each speaker a colour of their own (video018: yellow and green, a laugh in pink): where none of the
# colours found so far shows a caption, the colour learned there is one more caption colour when its letters make
# whole captions of their own there, as big as the main colour's (a word highlighted in another colour inside a
# caption, or a bright detail of the picture between captions, never does).
#
# Timing: captions switch on whole frames, so every frame is compared with the one before it. Where the letters
# overlap the previous frame's less than CAND_IOU -- and, around the caption, the bright shapes in any colour changed
# too (a word highlighted in another colour keeps its shape) -- a new caption MAY start; the frames between two such
# points are one run. Every run is read (OCR, a few frames) and touching runs that read the same are one caption (a
# pop-in's growing frames, a frame of noise), unless the same text pops in again (its letters shrink, then grow
# back). So a caption starts on the frame its words appear and ends on the frame before other words (or none) show.
#
# Text: the majority reading of the caption's fully grown frames, as written. Then the video's own conventions,
# learned from all its readings, fix what a recogniser misreads on single frames: in an ALL-CAPS video every letter
# is upper case ("sO" -> "SO"), a lone solid bar is "I" (not "1"), straight double quotes are curly where the video
# writes curly ones, and apostrophes follow the video's majority style. A caption that cannot be read gets no text
# here (captions.py falls back to the transcript and lists it). No other rule touches the text here; the hard rules
# (caption_rules.py) write it my way afterwards.

CAND_IOU = 0.8            # letters overlapping the previous frame's less than this (IoU): a new caption may start ...
SHAPE_SAME = 0.8          # ... unless the bright shapes around them (any colour) still overlap at least this much
READS_PER_RUN = 3         # frames read (OCR) per run, spread over it
RESTART_SHRINK = 0.97     # the same text, its letters this much narrower than the frame before and growing back ...
POP_S = 0.25              # ... within this time: it popped in again (a new caption); also the longest pop-in / pop-out
POP_SHAPE = 0.5           # a pop-in / pop-out frame looks like its caption (scale-free IoU) at least this much ...
POP_TEXT = 0.8            # ... and reads like it at least this much (small text misread), or cannot be read at all
FILL_TOL = 60.0           # BGR distance of a letter pixel from the learned fill colour
EXTRA_FILL_S = 0.5        # another caption colour: captions of its own (the colours found before absent) at least this
#                           long over the sampled frames ...
EXTRA_FILL_AREA = 0.3     # ... their letters at least this share of the main colour's median letter area
MAX_FILLS = 4             # the main colour and at most three more ...
EXTRA_FILL_TRIES = 6      # ... out of this many candidate colours tried (a colourful picture offers some first)
EXTRA_FILL_READS = 8      # ... and its caption lines READ as text: of up to this many of them (spread out), at least half
EXTRA_FILL_SCORE = 0.8    #   read a word of two or more letters at least this surely (a picture detail next to a
#                           shadow reads as nothing, a stray glyph or one bar: zendaya, zendaya-age)
SPAN_VERSION = 7          # 5: pop-in frames compared with OCR look-alike letters (ocr_key); 6: a zone over the
                          #   caption events is not ignored; 7: captions in more than one fill colour
BAR_READS = frozenset(["1", "l", "|", "ı", "i", "I", "/"])     # a lone bar may be read as any of these


def _near_dark(crop: np.ndarray, glyph_h: float) -> np.ndarray:
    """Bright pixels with a dark pixel (outline or shadow) within a quarter glyph height."""
    import cv2
    mx = crop.max(axis=2)
    r = max(2, int(round(0.25 * glyph_h)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    near = cv2.dilate((mx <= 90).astype(np.uint8), k) > 0
    return (mx >= 160) & near


def learn_fill_colour(crops: Sequence[np.ndarray], glyph_h: float, ignore: np.ndarray | None,
                      exclude: Sequence[np.ndarray] = ()) -> np.ndarray | None:
    """The caption fill colour (BGR): the most frequent colour (24-level bins) of the bright-next-to-dark pixels of
    thin bright strokes in the band over the sampled frames -- the caption is on screen far more than any other such
    detail. Pixels within FILL_TOL of a colour in ``exclude`` (the caption colours found already) do not count."""
    import cv2
    hist: Counter = Counter()
    for c in crops:
        m = _near_dark(c, glyph_h)
        if ignore is not None:
            m &= ~ignore
        # only thin bright strokes: letters are a few pixels wide; the edge of a wide bright region (a shirt, a colour
        # bar) next to a shadow is not
        dist = cv2.distanceTransform((c.max(axis=2) >= 160).astype(np.uint8), cv2.DIST_L2, 3)
        r = max(2, int(round(0.25 * glyph_h)))
        widest = cv2.dilate(dist, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        m &= widest <= max(2.5, 0.2 * glyph_h)
        px = c[m]
        if len(exclude) and len(px):
            near = np.min([np.linalg.norm(px.astype(np.float32) - np.asarray(e, np.float32), axis=1) for e in exclude],
                          axis=0)
            px = px[near > FILL_TOL]
        q = (px // 24).astype(np.int32)
        if len(q):
            hist.update(map(tuple, q.tolist()))
    if not hist:
        return None
    top = np.array(hist.most_common(1)[0][0], float) * 24 + 12
    # refine: the mean of the pixels close to the bin centre
    return top


def learn_fill_colours(crops: Sequence[np.ndarray], glyph_h: float, ignore: np.ndarray | None,
                       min_frames: int, eng: Any = None) -> list[np.ndarray]:
    """The caption fill colours (BGR), the main one first (learn_fill_colour over every sampled crop). Then, on the
    sampled crops where none of the colours found so far shows a letter (a frame with a word highlighted in another
    colour shows the rest of its caption in the main colour), the colour learned there is one more when
    its letters make a caption line on at least ``min_frames`` of them, at least EXTRA_FILL_AREA of the main colour's
    median letter area, and when they read as text (EXTRA_FILL_READS / EXTRA_FILL_SCORE): captions of their own in
    another colour (video018: yellow and green per speaker, a laugh in pink). A word highlighted in another colour
    sits inside a caption of the main colour, and a bright detail of the picture between captions reads as no word:
    neither is learned."""
    main = learn_fill_colour(crops, glyph_h, ignore)
    if main is None:
        return []
    fills = [main]
    min_area = max(6.0, 0.06 * glyph_h * glyph_h)
    areas = [int(np.count_nonzero(caption_layer(c, glyph_h, fills, ignore)[0])) for c in crops]
    ref = float(np.median([a for a in areas if a >= min_area] or [0]))
    rest = [c for c, a in zip(crops, areas) if a < min_area]
    tried: list[np.ndarray] = []
    while ref > 0 and len(fills) < MAX_FILLS and rest and len(tried) < EXTRA_FILL_TRIES:
        cand = learn_fill_colour(rest, glyph_h, ignore, exclude=fills + tried)
        if cand is None:
            break
        tried.append(cand)
        layers = [caption_layer(c, glyph_h, [cand], ignore)[0] for c in rest]
        got = [int(np.count_nonzero(m)) for m in layers]
        on = [i for i, a in enumerate(got) if a >= EXTRA_FILL_AREA * ref]
        if len(on) < min_frames:
            continue
        picks = sorted({on[j * (len(on) - 1) // max(1, EXTRA_FILL_READS - 1)] for j in range(EXTRA_FILL_READS)})
        reads = [recognise(layers[i], glyph_h, eng) for i in picks]
        if 2 * sum(s >= EXTRA_FILL_SCORE and re.search(r"[^\W\d_]{2,}", tx) is not None for tx, s in reads) < len(picks):
            continue
        fills.append(cand)
        rest = [c for c, a in zip(rest, got) if a < min_area]
    return fills


def caption_layer(crop: np.ndarray, glyph_h: float, fill: np.ndarray | Sequence[np.ndarray],
                  ignore: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """(letters in the fill colour(s) on the caption line, every bright-next-to-dark pixel) of one band crop."""
    cand = _near_dark(crop, glyph_h)
    if ignore is not None:
        cand &= ~ignore
    fills = [np.asarray(fill)] if np.ndim(fill) == 1 else [np.asarray(f) for f in fill]
    c32 = crop.astype(np.float32)
    d = np.min([np.linalg.norm(c32 - f.astype(np.float32), axis=2) for f in fills], axis=0)
    return caption_line(cand & (d <= FILL_TOL), glyph_h), cand


def iou(a: np.ndarray, b: np.ndarray) -> float:
    u = int(np.count_nonzero(a | b))
    return float(np.count_nonzero(a & b)) / u if u else 1.0


def _box(m: np.ndarray) -> tuple[int, int, int, int]:
    """(y0, y1, x0, x1) of the letters (a mask with at least one pixel)."""
    ys, xs = np.nonzero(m)
    return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1


def scaled_iou(a: np.ndarray, b: np.ndarray) -> float:
    """IoU of two letter masks after scaling b's letters onto a's box: the same text at another size (a pop-in's
    growing frames) scores high, other words low."""
    import cv2
    if not a.any() or not b.any():
        return 0.0
    y0, y1, x0, x1 = _box(a)
    v0, v1, u0, u1 = _box(b)
    bs = cv2.resize(b[v0:v1, u0:u1].astype(np.float32), (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA) >= 0.5
    return iou(a[y0:y1, x0:x1], bs)


def lone_bar(m: np.ndarray, glyph_h: float) -> bool:
    """The letters are one solid upright bar and nothing else: a capital I in a caption font (a 1 has a flag, an
    i / ! a dot)."""
    import cv2
    n, _, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n != 2:
        return False
    w, h, a = (int(st[1, c]) for c in (cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT, cv2.CC_STAT_AREA))
    return h >= 0.5 * glyph_h and h >= 2 * w and a >= 0.8 * w * h


_IN_WORD_APOSTROPHE = re.compile(r"(?<=\w)['’‘](?=\w)")


def screen_conventions(texts: Sequence[str]) -> dict:
    """How the video writes its captions, from all its readings: ALL CAPS (at least 90% of 12 or more letters upper
    case), curly double quotes (any “ or ” read) and the apostrophe style (the majority of ' and ’ inside words)."""
    letters = [ch for t in texts for ch in t if ch.isalpha()]
    caps = len(letters) >= 12 and sum(ch.isupper() for ch in letters) >= 0.9 * len(letters)
    curly = any("“" in t or "”" in t for t in texts)
    aps = Counter(m.group(0) for t in texts for m in _IN_WORD_APOSTROPHE.finditer(t))
    ap = "’" if aps["’"] + aps["‘"] > aps["'"] else "'"
    return {"all_caps": caps, "curly_quotes": curly, "apostrophe": ap}


def apply_conventions(text: str, conv: dict) -> str:
    """One reading written the video's way (screen_conventions): "sO" -> "SO" in an all-caps video, a straight " ->
    “ / ” where the video writes curly quotes, an apostrophe in the video's style. Nothing else changes."""
    t = str(text)
    if conv.get("all_caps"):
        t = t.upper()
    if conv.get("curly_quotes"):
        t = re.sub(r'(?:^|(?<=\s))"', "“", t).replace('"', "”")
    return _IN_WORD_APOSTROPHE.sub(conv.get("apostrophe") or "'", t)


def runs_from_signals(present: np.ndarray, change: np.ndarray) -> list[list[int]]:
    """[[a, b)] of the runs: consecutive frames with caption letters and no possible new caption inside."""
    runs: list[list[int]] = []
    for k in range(len(present)):
        if not present[k]:
            continue
        if k == 0 or not present[k - 1] or bool(change[k]) or not runs:
            runs.append([k, k + 1])
        else:
            runs[-1][1] = k + 1
    return runs


def join_runs(runs: Sequence[Sequence[int]], texts: Sequence[str], widths: np.ndarray, fps: Fraction,
              alike: Any = None) -> list[list[int]]:
    """The captions, as lists of run indices. Touching runs that read the same (letters and digits) are one caption
    -- a pop-in's growing frames, a frame of noise -- unless the same text popped in again: at the join its letters
    are RESTART_SHRINK narrower than the frame before and grow back within POP_S. A run no longer than POP_S that
    cannot be read, or reads almost like a touching neighbour (>= POP_TEXT), joins the neighbour its letters look
    like at another size (``alike(i, j)`` for i < j, scale-free, >= POP_SHAPE): a pop-in / pop-out frame whose small
    text is misread. Any other unreadable run is a caption of its own."""
    import difflib
    pop = max(1, int(round(POP_S * float(fps))))
    keys = [norm_text(t) for t in texts]
    looks = [ocr_key(t) for t in texts]                    # compared with the letters OCR mixes up made alike
    n = len(runs)
    touch = [i > 0 and runs[i - 1][1] == runs[i][0] for i in range(n)]
    for _ in range(n if alike is not None else 0):        # until stable: a pop-in of several misread frames
        changed = False
        for i in range(n):
            if runs[i][1] - runs[i][0] > pop:
                continue
            best, top = None, POP_SHAPE
            for j, ok in ((i - 1, touch[i]), (i + 1, i + 1 < n and touch[i + 1])):
                if not ok or not keys[j] or keys[j] == keys[i] or (keys[i] and difflib.SequenceMatcher(
                        None, looks[i], looks[j], autojunk=False).ratio() < POP_TEXT):
                    continue
                s = alike(min(i, j), max(i, j))
                if s >= top:
                    best, top = j, s
            if best is not None:
                keys[i], looks[i], changed = keys[best], looks[best], True
        if not changed:
            break

    def restart(i: int) -> bool:
        """Run i's letters start narrower than the frame before and grow back while the text stays the same."""
        a, j = runs[i][0], i
        while j + 1 < n and touch[j + 1] and keys[j + 1] == keys[i]:
            j += 1
        w0 = float(widths[a - 1])
        return widths[a] < RESTART_SHRINK * w0 and float(np.max(widths[a:min(runs[j][1], a + pop)])) >= \
            RESTART_SHRINK * w0

    groups: list[list[int]] = []
    for i in range(n):
        g = groups[-1] if groups else None
        if g is not None and touch[i] and keys[i] and keys[i] == keys[g[0]] and not restart(i):
            g.append(i)
        else:
            groups.append([i])
    return groups


def vote(reads: Sequence[FrameRead]) -> dict:
    """A caption's text: the most frequent reading of its fully grown readable frames (letter area at least 90% of
    the largest: not the pop-in / pop-out frames), ties to the higher total score."""
    readable = [f for f in reads if f.readable]
    if not readable:
        return {"text": "", "agreement": 0.0, "score": 0.0, "reads": 0, "variants": {}}
    top = max(f.area for f in readable)
    pool = [f for f in readable if f.area >= 0.9 * top]
    votes: dict[str, list[float]] = {}
    for f in pool:
        votes.setdefault(f.text, []).append(f.score)
    best, sc = max(votes.items(), key=lambda kv: (len(kv[1]), sum(kv[1])))
    return {"text": best, "agreement": round(len(sc) / len(pool), 3), "score": round(float(np.mean(sc)), 3),
            "reads": len(pool), "variants": {t: len(s) for t, s in sorted(votes.items(), key=lambda kv: -len(kv[1]))}}


def caption_band(layout: Any, frame_wh: tuple[int, int]) -> Band | None:
    """The caption band from the layout's captions ZONE (or its caption events), whole frame width."""
    lay = layout.to_dict() if hasattr(layout, "to_dict") else dict(layout or {})
    band, _ = band_from_layout(lay, frame_wh)
    if band is not None:
        return band
    zones = lay.get("zones") or []
    cz = next((z for z in zones if str(z.get("type")) == "captions" and z.get("x") is not None), None)
    if cz is None:
        return None
    fake = dict(lay, captions=[{"type": "captions", "comp_in": 0, "comp_out": 1, "x": cz["x"], "y": cz["y"],
                                "w": cz["w"], "h": cz["h"]}])
    band, _ = band_from_layout(fake, frame_wh)
    if band is not None:
        band.events = []
    return band


def read_caption_spans(video: str, layout: Any, frame_wh: tuple[int, int], fps: Fraction, n_frames: int,
                       eng: Any = None, progress: Any = None) -> dict:
    """{spans: [{comp_in, comp_out, ocr, score, reads, agreement, variants}], band, fill, frames_read, runs,
    conventions, notes}: every caption the competitor shows (the band of the layout's captions zone, every frame),
    from its first frame to its last (``comp_out`` exclusive), with its text as written on screen (``ocr``; "" when
    it cannot be read). See the section comment above."""
    from .media import VideoReader
    empty = {"spans": [], "band": None, "fill": None, "frames_read": 0, "notes": {}}
    band = caption_band(layout, frame_wh)
    if band is None:
        return empty
    ign = _ignore_mask(band)
    gh = band.glyph_h
    with VideoReader(video, fps=fps) as vr:                     # pass 1: the fill colour
        sample = [img[band.y:band.y + band.h].copy() for k, img in vr.frames(0, n_frames) if k % 4 == 0]
    eng = eng or engine()
    fills = learn_fill_colours(sample, gh, ign, max(3, int(round(EXTRA_FILL_S * float(fps) / 4))), eng)
    del sample
    if not fills:
        return empty
    min_area = max(6.0, 0.06 * gh * gh)          # about one small letter (a pop-in's first frame included)
    present = np.zeros(n_frames, bool)
    change = np.zeros(n_frames, bool)
    widths = np.zeros(n_frames, np.int64)
    areas = np.zeros(n_frames, np.int64)
    masks: dict[int, tuple] = {}                 # the letters' box and its bits: (y0, y1, x0, x1, packed)
    prev = None
    count = 0
    with VideoReader(video, fps=fps) as vr:                     # pass 2: the caption layer of every frame
        for k, img in vr.frames(0, n_frames):
            lc, ls = caption_layer(img[band.y:band.y + band.h], gh, fills, ign)
            areas[k] = int(np.count_nonzero(lc))
            present[k] = areas[k] >= min_area
            if present[k]:
                y0, y1, x0, x1 = _box(lc)
                masks[k] = (y0, y1, x0, x1, np.packbits(lc[y0:y1, x0:x1], axis=None))
                widths[k] = x1 - x0
                if prev is not None and iou(prev[0], lc) < CAND_IOU:
                    near = cv2_dilate_box(prev[0] | lc, gh)
                    change[k] = iou(prev[1] & near, ls & near) < SHAPE_SAME
            prev = (lc, ls) if present[k] else None
            count += 1
            if progress is not None:
                progress(count, n_frames)
    shape = (band.h, band.w)

    def mask(k: int) -> np.ndarray:
        y0, y1, x0, x1, bits = masks[k]
        m = np.zeros(shape, bool)
        m[y0:y1, x0:x1] = np.unpackbits(bits, count=(y1 - y0) * (x1 - x0)).reshape(y1 - y0, x1 - x0).astype(bool)
        return m

    runs = runs_from_signals(present, change)
    eng = eng or engine()
    reads: list[list[FrameRead]] = []
    for a, b in runs:
        rr = []
        for k in sorted({a + (b - 1 - a) * i // max(1, READS_PER_RUN - 1) for i in range(READS_PER_RUN)}):
            m = mask(k)
            text, score = recognise(m, gh, eng)
            if text.strip() in BAR_READS and lone_bar(m, gh):
                text = "I"
            rr.append(FrameRead(k, text, score, int(areas[k])))
        reads.append(rr)
    conv = screen_conventions([f.text for rr in reads for f in rr if f.readable])
    for rr in reads:
        for f in rr:
            f.text = apply_conventions(f.text, conv)
    texts = [vote(rr)["text"] for rr in reads]
    groups = join_runs(runs, texts, widths, fps,
                       alike=lambda i, j: scaled_iou(mask(runs[i][1] - 1), mask(runs[j][0])))
    out = []
    for g in groups:
        maj = vote([f for i in g for f in reads[i]])
        out.append({"comp_in": int(runs[g[0]][0]), "comp_out": int(runs[g[-1]][1]), "ocr": maj["text"],
                    "score": maj["score"], "reads": maj["reads"], "agreement": maj["agreement"],
                    "variants": maj["variants"]})
    log.info("captions OCR: %d captions (%d runs), band x %d y %d %dx%d (glyph %.0f px), %d frames, %d fill colour(s) "
             "%s, %s", len(out), len(runs), band.x, band.y, band.w, band.h, gh, count, len(fills),
             " ".join("BGR(%d,%d,%d)" % tuple(int(v) for v in f) for f in fills),
             ", ".join(k for k, v in conv.items() if v is True) or "no case / quote convention")
    return {"spans": out, "frames_read": count, "runs": len(runs), "conventions": conv,
            "fill": [round(float(v), 1) for v in fills[0]], "fills": [[round(float(v), 1) for v in f] for f in fills],
            "band": {"x": band.x, "y": band.y, "w": band.w, "h": band.h, "glyph_h": gh, "keep": band.keep},
            "notes": {}}


def cv2_dilate_box(m: np.ndarray, glyph_h: float) -> np.ndarray:
    """The caption's neighbourhood: its letters dilated by a third of a glyph height (where a highlighted word of
    another colour sits)."""
    import cv2
    r = max(2, int(round(glyph_h / 3.0)))
    return cv2.dilate(m.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0
