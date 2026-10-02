"""The competitor's burned-in captions, read by OCR (captions mode ``competitor``, see captions.py).

Engine: RapidOCR (``rapidocr`` 3.x, or the older ``rapidocr-onnxruntime``: ONNX models inside the wheel, pip-only
on Windows, no system installs).
Only its recogniser runs, on a clean image this module builds per text line, which is fast (~15 ms) and much more
reliable on video than detection on the raw picture.

Where to look: the caption band of the layout (the ``captions`` zone layout.py measured; static title / logo /
watermark zones are masked), on every competitor frame within ``margin`` frames of a detected caption event.

Per frame: the caption text is white (or a highlight colour) with a dark outline over arbitrary video. Pixels the
crop border reaches without crossing a dark pixel are background; the bright pixels it cannot reach and that touch
an outline that does touch the background are the letters (letter counters -- the video inside an "o" -- touch only
an inner outline and are dropped). Picture detail that looks like that is dropped when it is not ringed by a
near-black outline, lies outside the boxes of the layout's caption events around that frame, or is not on the
caption's own line of letters. The letters are drawn black on white per text line and recognised.

Captions: consecutive frames showing the same text are one caption, from the first frame it shows to the last. A
colour change (word-by-word highlight) does not change the letters' shape and a pop-in / pop-out (the text growing
or shrinking for a few frames) is joined to the caption it belongs to; only a change of the text -- or the same text
popping in again -- starts a new caption; a frame or two misread inside a caption stays in it. Each caption's text
is the majority of its fully visible frames' readings; frames at either end that are not the caption (picture
detail before it appears / after it goes) are trimmed, and text outside every caption event is left out.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

from .common import log

DARK_THR = 100          # max(B, G, R) at or below this: outline / dark background
BRIGHT_THR = 150        # letters are brighter than this (white or a highlight colour)
OUTLINE_MAX = 70        # the outline right around a letter is at most this bright ...
OUTLINE_FRAC = 0.3      # ... on at least this fraction of the pixels 1-2 px around it
MIN_SCORE = 0.5         # a frame's reading counts when the recogniser is at least this sure
SAME_TEXT = 0.8         # two readings are the same caption at this similarity (OCR noise on one frame)
OCR_VERSION = 2         # bump when the reading changes (cache key)
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

def text_mask(crop: np.ndarray, glyph_h: float, ignore: np.ndarray | None = None,
              allow: np.ndarray | None = None) -> np.ndarray:
    """Boolean mask of the outlined caption letters in a BGR crop (see the module docstring); ``allow`` = where
    the layout saw caption text around this frame."""
    import cv2
    mx = crop.max(axis=2)
    dark = mx <= DARK_THR
    nd = (~dark).astype(np.uint8)
    _, lab = cv2.connectedComponents(nd, connectivity=4)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    outside = np.isin(lab, border[border > 0])
    k3 = np.ones((3, 3), np.uint8)
    _, dlab = cv2.connectedComponents(dark.astype(np.uint8), connectivity=8)
    touch = cv2.dilate(outside.astype(np.uint8), k3).astype(bool) & dark
    outline = np.isin(dlab, np.unique(dlab[touch]))
    inner = (nd > 0) & ~outside
    n, ilab, stats, _ = cv2.connectedComponentsWithStats(inner.astype(np.uint8), connectivity=4)
    adj = cv2.dilate(outline.astype(np.uint8), k3).astype(bool) & inner
    keep = np.zeros(n, bool)
    keep[np.unique(ilab[adj])] = True
    keep[0] = False
    keep &= stats[:, cv2.CC_STAT_HEIGHT] <= 3.0 * max(4.0, glyph_h)
    keep &= stats[:, cv2.CC_STAT_AREA] >= 3
    mask = keep[ilab] & (mx >= BRIGHT_THR)
    if ignore is not None:
        mask &= ~ignore
    if allow is not None:
        mask &= allow
    return caption_line(outlined(mask, mx), glyph_h)


def outlined(mask: np.ndarray, mx: np.ndarray) -> np.ndarray:
    """Only the letters ringed by a near-black outline: most pixels 1-2 px around each component are at most
    OUTLINE_MAX (a bright blob of the video on a merely dark patch is not a caption letter)."""
    import cv2
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return mask
    labf = lab.astype(np.float32)
    near = cv2.dilate(labf, np.ones((5, 5), np.uint8))
    ring = (near > 0) & (lab == 0) & (cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) == 0)
    rl = near[ring].astype(np.int64)
    tot = np.bincount(rl, minlength=n).astype(float)
    dark = np.bincount(rl, weights=(mx[ring] <= OUTLINE_MAX).astype(float), minlength=n)
    ok = dark >= OUTLINE_FRAC * np.maximum(tot, 1)
    ok[0] = False
    return ok[lab]


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
    mask: np.ndarray | None = field(default=None, repr=False)
    color: tuple[float, float, float] | None = None      # mean BGR of the letter pixels

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
# Frames -> captions
# ---------------------------------------------------------------------------------------------

def norm_text(t: str) -> str:
    """Letters and digits only, lower case, accents dropped (a stray mark read as an accent is OCR noise)."""
    import unicodedata
    t = "".join(ch for ch in unicodedata.normalize("NFKD", str(t)) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", t.lower())


def similar(a: str, b: str) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _iou(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    u = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b)) / u if u else 0.0


@dataclass
class Run:
    frames: list[FrameRead]
    restart: bool = False          # started by the same text popping in again (a new caption, never merged back)

    @property
    def first(self) -> int:
        return self.frames[0].k

    @property
    def last(self) -> int:
        return self.frames[-1].k

    def ref(self) -> str:
        c = Counter(norm_text(f.text) for f in self.frames if f.readable)
        return c.most_common(1)[0][0] if c else ""

    def median_area(self) -> float:
        return float(np.median([f.area for f in self.frames]))


def split_runs(reads: Sequence[FrameRead]) -> list[Run]:
    """Consecutive present frames, split where the text changes (a sure reading clearly different from the run's,
    or a different reading together with a different letter shape: "THEN" -> "THE") or where the letters shrink
    suddenly to a different shape (the same text popping in again)."""
    runs: list[Run] = []
    cur: Run | None = None
    for f in reads:
        if f.area <= 0:
            cur = None
            continue
        if cur is None or f.k != cur.last + 1:
            cur = Run([f])
            runs.append(cur)
            continue
        prev = cur.frames[-1]
        ref = cur.ref()
        changed = bool(f.readable and ref and (similar(f.text, ref) < SAME_TEXT or (
            norm_text(f.text) != ref and _iou(prev.mask, f.mask) < 0.4)))
        restart = f.area < 0.6 * prev.area and _iou(prev.mask, f.mask) < 0.5
        if changed or restart:
            cur = Run([f], restart=restart)
            runs.append(cur)
        else:
            cur.frames.append(f)
    return runs


def _popish(r: Run, s: Run) -> bool:
    """Run r looks like run s's text drawn smaller (part of its pop-in / pop-out animation)."""
    ra, sa = r.median_area(), s.median_area()
    if ra >= 0.95 * sa:
        return False
    a, b = r.ref(), s.ref()
    return not a or similar(a, b) >= 0.5 or a in norm_text(b) or ra < 0.7 * sa


def merge_pops(runs: list[Run], pop_frames: int) -> list[Run]:
    """Join a pop-in (a short run of growing, partly read text right before its caption) to the caption after it,
    and a pop-out (a short shrinking tail) to the caption before it. A short run between two captions goes forward
    (pop-in) unless it reads as the previous text and not the next one, or it shrinks."""
    out = list(runs)
    merged = True
    while merged:
        merged = False
        for i, r in enumerate(out):
            if len(r.frames) > pop_frames:
                continue
            nxt = out[i + 1] if i + 1 < len(out) and out[i + 1].first == r.last + 1 else None
            prv = out[i - 1] if i > 0 and out[i - 1].last + 1 == r.first else None
            a0, a1 = r.frames[0].area, r.frames[-1].area
            ref = r.ref()
            if prv is not None and nxt is not None and not nxt.restart and not r.restart \
                    and similar(prv.ref(), nxt.ref()) >= SAME_TEXT \
                    and _iou(prv.frames[-1].mask, nxt.frames[0].mask) >= 0.6:   # the same letters both sides
                prv.frames += r.frames + nxt.frames      # a misread blip inside one caption: KEEP | KEEË | KEEP
                del out[i:i + 2]
                merged = True
                break
            same_n = nxt is not None and bool(ref) and similar(ref, nxt.ref()) >= SAME_TEXT
            same_p = prv is not None and bool(ref) and similar(ref, prv.ref()) >= SAME_TEXT
            if same_n or same_p:                 # a frame or two read differently (picture detail next to the text)
                tgt = nxt if same_n and (not same_p or similar(ref, nxt.ref()) >= similar(ref, prv.ref())) else prv
                if tgt is nxt:
                    nxt.frames = r.frames + nxt.frames
                else:
                    prv.frames += r.frames
                out.pop(i)
                merged = True
                break
            fwd = nxt is not None and a1 <= nxt.frames[0].area and _popish(r, nxt)
            bwd = prv is not None and a0 <= prv.frames[-1].area and _popish(r, prv)
            if fwd and bwd:
                ref = r.ref()
                if ref and similar(ref, prv.ref()) >= SAME_TEXT > similar(ref, nxt.ref()):
                    fwd = False
                elif a1 >= a0:
                    bwd = False
                else:
                    fwd = False
            if fwd:
                nxt.frames = r.frames + nxt.frames
            elif bwd:
                prv.frames += r.frames
            else:
                continue
            out.pop(i)
            merged = True
            break
    return out


def majority(run: Run) -> dict:
    """The caption text: the majority reading of the fully visible, readable frames -- the reading closest to all
    of them (the sum of its character similarity to every reading: identical readings count fully, so the most
    frequent one wins unless it is an outlier). Also how many frames agreed exactly and the mean score."""
    readable = [f for f in run.frames if f.readable]
    full_area = float(np.median([f.area for f in readable])) if readable else 0.0
    pool = [f for f in readable if f.area >= 0.9 * full_area] or readable      # not the pop-in / pop-out frames
    if not pool:
        return {"text": "", "agreement": 0.0, "score": 0.0, "reads": 0, "variants": {}}
    votes: dict[str, list[float]] = {}
    for f in pool:
        votes.setdefault(f.text, []).append(f.score)
    def support(t: str) -> tuple[float, int, float]:
        sim = sum(len(sc) * difflib.SequenceMatcher(None, t, u, autojunk=False).ratio() for u, sc in votes.items())
        return round(sim, 6), len(votes[t]), sum(votes[t])
    best = max(votes.items(), key=lambda kv: support(kv[0]))
    return {"text": best[0], "agreement": round(len(best[1]) / len(pool), 3),
            "score": round(float(np.mean(best[1])), 3), "reads": len(pool),
            "variants": {t: len(s) for t, s in sorted(votes.items(), key=lambda kv: -len(kv[1]))}}


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


def allowed_region(band: Band, k: int, margin: int) -> np.ndarray | None:
    """Where caption letters may be on frame k (band coordinates): the boxes of the layout's caption events within
    ``margin`` frames of k, padded by 2.5 glyph heights sideways (a word the layout missed next to the ones it
    found) and 0.6 up / down. None = anywhere."""
    if not band.events:
        return None
    m = np.zeros((band.h, band.w), bool)
    px, py = 2.5 * band.glyph_h, 0.6 * band.glyph_h
    for a, b, x, y, w, h in band.events:
        if a - margin <= k < b + margin:
            x0, x1 = int(max(0, np.floor(x - px - band.x))), int(min(band.w, np.ceil(x + w + px - band.x)))
            y0, y1 = int(max(0, np.floor(y - py - band.y))), int(min(band.h, np.ceil(y + h + py - band.y)))
            if x1 > x0 and y1 > y0:
                m[y0:y1, x0:x1] = True
    return m


def frames_to_read(spans: Sequence[tuple[int, int]], n_frames: int, margin: int) -> list[int]:
    ks: set[int] = set()
    for a, b in spans:
        ks.update(range(max(0, a - margin), min(n_frames, b + margin)))
    return sorted(ks)


def read_frames(video: str, ks: Sequence[int], band: Band, fps: Fraction | None = None, eng: Any = None,
                progress: Any = None, margin: int = 6) -> list[FrameRead]:
    """Every listed frame read: letter mask inside the band, its area and (when letters are there) the text. A
    frame whose letters are the same pixels as the previous frame's reuses that reading."""
    from .media import VideoReader
    eng = eng or engine()
    ign = _ignore_mask(band)
    min_area = max(6, int(0.08 * band.glyph_h ** 2))
    reads: list[FrameRead] = []
    prev: FrameRead | None = None
    ks = list(ks)
    if not ks:
        return reads
    want = set(ks)
    with VideoReader(video, fps=fps) as vr:
        for k, img in vr.frames(ks[0], ks[-1] + 1, fmt="bgr24"):
            if k not in want:
                continue
            crop = img[band.y:band.y + band.h, band.x:band.x + band.w]
            mask = text_mask(crop, band.glyph_h, ign, allowed_region(band, k, margin))
            area = int(np.count_nonzero(mask))
            if area < min_area:
                f = FrameRead(k)
            else:
                color = tuple(float(v) for v in crop[mask].mean(axis=0))
                if prev is not None and prev.k == k - 1 and prev.area and _iou(prev.mask, mask) >= 0.97:
                    f = FrameRead(k, prev.text, prev.score, area, mask, color)
                else:
                    text, score = recognise(mask, band.glyph_h, eng)
                    f = FrameRead(k, text, score, area, mask, color)
            reads.append(f)
            prev = f
            if progress is not None:
                progress(len(reads), len(ks))
    return reads


def trim_edges(run: Run, text: str, glyph_h: float) -> Run | None:
    """Drop leading / trailing frames that are not this caption: frames whose letters do not read as its text and
    lie mostly outside the box its fully visible frames cover (picture detail next to the caption before it
    appears or after it goes). A pop-in / pop-out (smaller text inside that box) stays."""
    full = [f for f in run.frames if f.readable and similar(f.text, text) >= SAME_TEXT and f.mask is not None]
    if not full:
        return run
    ys, xs = np.nonzero(np.logical_or.reduce([f.mask for f in full]))
    pad = int(round(0.3 * glyph_h))
    y0, y1, x0, x1 = ys.min() - pad, ys.max() + pad + 1, xs.min() - pad, xs.max() + pad + 1

    cx, cy = 0.5 * (xs.min() + xs.max()), 0.5 * (ys.min() + ys.max())
    width = float(xs.max() - xs.min() + 1)
    aspect = width / float(ys.max() - ys.min() + 1)
    n_lines = max(1, text.count("\n") + 1)
    colors = np.array([f.color for f in full if f.color is not None], float).reshape(-1, 3)

    def belongs(f: FrameRead) -> bool:
        if f.readable and similar(f.text, text) >= 0.5:
            return True
        if f.mask is None or not f.area:
            return False
        inside = np.count_nonzero(f.mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)])
        fy, fx = np.nonzero(f.mask)
        centred = (abs(0.5 * (fx.min() + fx.max()) - cx) <= 0.15 * width + 0.5 * glyph_h
                   and abs(0.5 * (fy.min() + fy.max()) - cy) <= 0.35 * glyph_h)
        # a pop-in / pop-out is the caption scaled: the same shape (aspect ratio) around the same centre
        same_shape = abs(np.log((fx.max() - fx.min() + 1) / float(fy.max() - fy.min() + 1) / aspect)) <= np.log(1.4)
        # ... and the caption's colours (picture detail around the caption rarely has them)
        tinted = (f.color is None or not len(colors)
                  or float(np.min(np.linalg.norm(colors - np.asarray(f.color, float), axis=1))) <= 60.0)
        centred = centred and same_shape and tinted
        return (inside >= 0.85 * f.area and centred and f.text.count("\n") + 1 <= n_lines)
    frames = list(run.frames)
    while frames and not belongs(frames[0]):
        frames.pop(0)
    while frames and not belongs(frames[-1]):
        frames.pop()
    return Run(frames) if frames else None


def captions_from_reads(reads: Sequence[FrameRead], fps: Fraction, n_frames: int, glyph_h: float = 20.0,
                        spans: Sequence[tuple[int, int]] | None = None) -> tuple[list[dict], dict]:
    """Caption list [{text, comp_in, comp_out (exclusive), agreement, score, reads, variants}] and notes (static
    text, unreadable runs and runs outside every caption event that were left out)."""
    pop = max(2, int(round(0.15 * float(fps))))
    runs = merge_pops(split_runs(reads), pop)
    caps, notes = [], {"static": [], "unreadable": [], "outside_events": []}
    analysed = max(1, len(reads))
    covered = np.zeros(max(n_frames, max((f.k for f in reads), default=0) + 1), bool)
    for a, b in spans or [(0, len(covered))]:
        covered[max(0, a):max(0, b)] = True
    for r in runs:
        maj = majority(r)
        if not maj["text"]:
            notes["unreadable"].append({"comp_in": r.first, "comp_out": r.last + 1})
            continue
        r = trim_edges(r, maj["text"], glyph_h)
        if r is None:
            continue
        span = (r.first, r.last + 1)
        if covered[span[0]:span[1]].sum() < 0.5 * (span[1] - span[0]):
            notes["outside_events"].append({"comp_in": span[0], "comp_out": span[1], "text": maj["text"]})
            continue
        if len(r.frames) >= 0.9 * analysed and (r.last + 1 - r.first) >= 10 * float(fps):
            notes["static"].append({"comp_in": span[0], "comp_out": span[1], "text": maj["text"]})
            continue
        caps.append({"text": maj["text"], "comp_in": span[0], "comp_out": span[1], **{k: maj[k] for k in
                     ("agreement", "score", "reads", "variants")}})
    return caps, notes


def read_competitor_captions(video: str, layout: Any, frame_wh: tuple[int, int], fps: Fraction, n_frames: int,
                             margin: int | None = None, progress: Any = None) -> dict:
    """Everything competitor mode needs: {captions, band, frames_read, events, notes}. ``captions`` is empty when
    the layout found no caption events."""
    band, spans = band_from_layout(layout, frame_wh)
    if band is None:
        return {"captions": [], "events": 0, "frames_read": 0, "band": None, "notes": {}}
    margin = int(round(0.2 * float(fps))) if margin is None else int(margin)
    ks = frames_to_read(spans, n_frames, margin)
    log.info("captions OCR: %d caption events, band x %d y %d %dx%d (glyph %.0f px), %d frames", len(spans), band.x,
             band.y, band.w, band.h, band.glyph_h, len(ks))
    reads = read_frames(video, ks, band, fps, progress=progress, margin=margin)
    caps, notes = captions_from_reads(reads, fps, n_frames, band.glyph_h, spans)
    return {"captions": caps, "events": len(spans), "frames_read": len(reads),
            "band": {"x": band.x, "y": band.y, "w": band.w, "h": band.h, "glyph_h": band.glyph_h, "keep": band.keep},
            "notes": notes}
