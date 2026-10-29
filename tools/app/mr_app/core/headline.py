"""Channel headlines: your title drawn the way Photoshop draws the example headline of the channels' template.psd --
NeueMontreal-Bold 122 pt, Faux Bold, tracking +10, black, centred, 2 lines, emojis from Noto Color Emoji -- on a
transparent canvas of the PSD's own size (2160x3840) or any smaller one of the same shape (the app's live preview).

Every number of the style (below) was measured on the three channel PSDs: the type layer's settings (font, size,
tracking, leading, its anchor = the centre x and the first baseline) and, fitted against Photoshop's own rendering of
the three example headlines, what Photoshop's Faux Bold does (the outline grows 1.5 px on every side, every character
advances a few px more) and how it centres a line. Checked on a fourth headline it never saw (assets/reference/
Example.psd): 97.5 % of the letters' ink shared with Photoshop's, 99.2 % of the emojis'. work/t25/headline/NOTES.md
has all the numbers.

Letters are drawn from the font's own outlines (no hinting, like Photoshop) with the font's kerning (GPOS 'kern',
Photoshop's "Metrics" auto kerning) at 4x and averaged down. Emojis are the bitmaps of Noto Color Emoji (the CBDT
font: v2.034 of googlefonts/noto-emoji, the same designs as Photoshop's NotoColorEmoji-SVG), found with the font's
own tables -- so sequences like the police officer + male sign (a ZWJ sequence), skin tones, flags and keycaps are
one emoji, as in Photoshop.

Line breaks: your own line break (Enter) wins; else the break that keeps both lines inside MAX_WIDTH (1840 px = 920 px
on the 1080x1920 sequence) and is the most balanced, preferably not after a small word (a, the, to ...) and
preferably after a comma or a full stop; emojis stay with their word. A title of one word is one line. When no
break fits, the headline is shrunk until it fits (``Layout.shrunk`` and ``Layout.why`` say so in plain words).
"""
from __future__ import annotations

import contextlib
import functools
import io
import math
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import paths

# ---------------------------------------------------------------------------------------------------------------------
# the style: Photoshop's headline type layer in every channel's template.psd (2160x3840 px, 72 ppi)
# ---------------------------------------------------------------------------------------------------------------------
CANVAS = (2160, 3840)          # the PSD's size: every number below is in its pixels
SIZE = 122.0                   # NeueMontreal-Bold 122 pt = 122 px at the PSD's 72 ppi
TRACKING = 10 / 1000 * SIZE    # Photoshop's tracking +10 = 10/1000 em after every character: 1.22 px
BOLD_GROW = 1.5                # Faux Bold: every letter's outline grows this much on every side (px, fitted)
BOLD_ADVANCE = 3.29            # Faux Bold: every letter and space advances this much more (px, fitted)
EMOJI_BOLD_ADVANCE = 1.64      # ... and every emoji this much more (px, fitted)
LINE_PITCH = 1.2 * SIZE        # Auto leading 120 %: 146.4 px from one baseline to the next
CENTRE_X = 1085.4632           # the type layer's anchor: every line is centred on it ...
BASELINE_1 = 849.2045          # ... and it is the first line's baseline (each baseline lands on a whole pixel)
CENTRE_TRIM = 2.6              # Photoshop centres a line's advance width minus this (px, fitted)
#                                emojis are SIZE px per em too: the font's 109-ppem bitmaps scaled by 122/109
EMOJI_RISE = 0.7               # emojis sit this much higher than their bitmap's own metrics say (px, fitted)
CAP_HEIGHT = 715 / 1000        # NeueMontreal-Bold's capital height (em): the top of the headline's block
MAX_WIDTH = 1840.0             # the widest a line may be (its ink, px): 920 px on the 1080x1920 sequence

TEXT_FONT = "NeueMontreal-Bold.otf"
EMOJI_FONT = "NotoColorEmoji.ttf"

# line breaking: the cost of a break is its longer line (px), plus WEAK_END when line 1 would end on a small word,
# minus PUNCT_END when it ends after punctuation
WEAK_END = 120.0
PUNCT_END = 80.0
WEAK_WORDS = frozenset("""a an the to of and or but nor my his her its our your their in on at for with from by into
onto is are was were am""".split())
PUNCTUATION_END = ",.!?:;"

ZWJ = 0x200D
_LOCK = threading.RLock()        # fontTools reads tables lazily: one thread at a time (the app previews in a thread)


# ---------------------------------------------------------------------------------------------------------------------
# another channel's headline style (29 Oct, MikeCuts: Instagram Sans Condensed Bold 140 pt, tracking -10, Faux Bold)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    """A channel's headline type layer: its font (a file in assets/fonts), size (pt = px on the 2160x3840 PSD),
    Photoshop tracking (1/1000 em) and Faux Bold. Faux Bold's fitted numbers (above) grow with the size."""
    font: str = "NeueMontreal-Bold.otf"
    size: float = 122.0
    tracking: float = 10.0
    faux_bold: bool = True

    def values(self) -> dict:
        k = float(self.size) / 122.0
        fb = 1.0 if self.faux_bold else 0.0
        return {"TEXT_FONT": self.font, "SIZE": float(self.size), "TRACKING": float(self.tracking) / 1000.0 * self.size,
                "BOLD_GROW": 1.5 * k * fb, "BOLD_ADVANCE": 3.29 * k * fb, "EMOJI_BOLD_ADVANCE": 1.64 * k * fb,
                "LINE_PITCH": 1.2 * self.size, "CENTRE_TRIM": 2.6 * k * fb,
                "CAP_HEIGHT": 715 / 1000 if self.font == "NeueMontreal-Bold.otf" else _cap_height(self.font)}


DEFAULT_STYLE = Style()


def style_from(spec) -> Style:
    """channel.json's "headline_style" ({"font", "size", "tracking", "faux_bold"}) -> Style (the default when there
    is none, or its font is not on this computer: see font_file)."""
    if not isinstance(spec, dict) or not spec.get("font"):
        return DEFAULT_STYLE
    try:
        st = Style(str(spec["font"]), float(spec.get("size", 122.0)), float(spec.get("tracking", 0.0)),
                   bool(spec.get("faux_bold", False)))
    except (TypeError, ValueError):
        return DEFAULT_STYLE
    return st if font_file(st.font) is not None else DEFAULT_STYLE


def _cap_height(font: str) -> float:
    """A font's capital height (em) from its OS/2 table (its 'H' when the table does not say)."""
    tf = _text_font(str(font_file(font) or paths.fonts_dir() / font))
    os2 = tf.tt["OS/2"] if "OS/2" in tf.tt else None
    cap = getattr(os2, "sCapHeight", 0) or 0
    if not cap:
        b = tf.bounds(tf.glyph("H") or "")
        cap = b[3] if b else 0.7 * tf.upem
    return float(cap) / tf.upem


@contextlib.contextmanager
def styled(style: Style | None = None):
    """Everything inside draws and measures in ``style`` (the default: the channels' NeueMontreal one). One thread at
    a time (the module's lock), so the app's preview thread never mixes two styles."""
    style = style or DEFAULT_STYLE
    with _LOCK:
        g = globals()
        old = {n: g[n] for n in style.values()}
        g.update(style.values())
        try:
            yield style
        finally:
            g.update(old)


def font_file(name: str) -> Path | None:
    """A headline font by its file name: assets/fonts first, else the fonts installed on this computer (yours, then
    Windows') -- so a font that may not be shared (Instagram Sans) stays out of the repo. None: not found."""
    import os
    dirs = [paths.fonts_dir()]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    for d in dirs:
        p = d / name
        if p.is_file():
            return p
    return None


def text_font_path() -> Path:
    return font_file(TEXT_FONT) or paths.fonts_dir() / TEXT_FONT


def emoji_font_path() -> Path:
    return paths.fonts_dir() / EMOJI_FONT


# ---------------------------------------------------------------------------------------------------------------------
# the fonts (read once)
# ---------------------------------------------------------------------------------------------------------------------

class _FlatPen:
    """A fontTools pen collecting a glyph's contours as point lists (font units, y up), curves cut into short lines."""

    STEP = 6.0                         # font units per line piece along a curve (6/1000 em = 0.7 px at 122 px)

    def __init__(self) -> None:
        self.contours: list[list[tuple[float, float]]] = []
        self.cur: list[tuple[float, float]] = []

    def moveTo(self, p):               # noqa: N802 - the pen protocol's names
        self._close()
        self.cur = [tuple(p)]

    def lineTo(self, p):               # noqa: N802
        self.cur.append(tuple(p))

    def curveTo(self, *pts):           # noqa: N802 - cubic pieces (CFF); several off-curve points: split evenly
        if len(pts) != 3:
            from fontTools.pens.basePen import decomposeSuperBezierSegment
            for seg in decomposeSuperBezierSegment(list(pts)):
                self.curveTo(*seg)
            return
        p0, p1, p2, p3 = self.cur[-1], pts[0], pts[1], pts[2]
        n = max(4, math.ceil((math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)) / self.STEP))
        t = np.linspace(0.0, 1.0, n + 1)[1:]
        u = 1.0 - t
        x = u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]
        y = u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]
        self.cur.extend(zip(x.tolist(), y.tolist()))

    def qCurveTo(self, *pts):          # noqa: N802 - quadratic pieces (TrueType outlines)
        from fontTools.pens.basePen import decomposeQuadraticSegment
        if pts[-1] is None:
            return
        for c, e in decomposeQuadraticSegment(list(pts)):
            p0 = self.cur[-1]
            p1 = (p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1]))
            p2 = (e[0] + 2 / 3 * (c[0] - e[0]), e[1] + 2 / 3 * (c[1] - e[1]))
            self.curveTo(p1, p2, e)

    def closePath(self):               # noqa: N802
        self._close()

    def endPath(self):                 # noqa: N802
        self._close()

    def addComponent(self, name, transform):   # noqa: N802 - composite glyphs are drawn through the glyph set
        raise NotImplementedError

    def _close(self) -> None:
        if len(self.cur) >= 3:
            self.contours.append(self.cur)
        self.cur = []


class _TextFont:
    """NeueMontreal-Bold: glyphs, advances, kerning (GPOS 'kern') and outlines."""

    def __init__(self, path: Path) -> None:
        from fontTools.ttLib import TTFont
        self.tt = TTFont(str(path))
        self.upem = float(self.tt["head"].unitsPerEm)
        self.cmap = self.tt.getBestCmap()
        self.hmtx = self.tt["hmtx"]
        self.glyphset = self.tt.getGlyphSet()
        self.kern_lookups = self._kern_lookups()

    def glyph(self, ch: str) -> str | None:
        return self.cmap.get(ord(ch))

    def advance(self, g: str) -> float:
        return float(self.hmtx[g][0])

    def _kern_lookups(self) -> list:
        if "GPOS" not in self.tt:
            return []
        gpos = self.tt["GPOS"].table
        idx = sorted({i for fr in gpos.FeatureList.FeatureRecord if fr.FeatureTag == "kern"
                      for i in fr.Feature.LookupListIndex})
        return [gpos.LookupList.Lookup[i] for i in idx]

    @functools.lru_cache(maxsize=4096)
    def kern(self, a: str, b: str) -> float:
        """The kerning of the pair (font units): every 'kern' lookup, its first subtable that has the pair (the
        OpenType rule)."""
        total = 0.0
        for lk in self.kern_lookups:
            for st in lk.SubTable:
                if lk.LookupType == 9:
                    st = st.ExtSubTable
                if getattr(st, "Coverage", None) is None or a not in st.Coverage.glyphs:
                    continue
                if st.Format == 1:
                    ps = st.PairSet[st.Coverage.glyphs.index(a)]
                    rec = next((r for r in ps.PairValueRecord if r.SecondGlyph == b), None)
                    if rec is None:
                        continue
                    total += float(getattr(rec.Value1, "XAdvance", 0) or 0) if rec.Value1 is not None else 0.0
                else:
                    c1 = st.ClassDef1.classDefs.get(a, 0)
                    c2 = st.ClassDef2.classDefs.get(b, 0)
                    v = st.Class1Record[c1].Class2Record[c2].Value1
                    total += float(getattr(v, "XAdvance", 0) or 0) if v is not None else 0.0
                break
        return total

    @functools.lru_cache(maxsize=1024)
    def contours(self, g: str) -> tuple[np.ndarray, ...]:
        """The glyph's outline: closed contours of (x, y) font units, y up."""
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        rec = DecomposingRecordingPen(self.glyphset)
        with _LOCK:
            self.glyphset[g].draw(rec)
        pen = _FlatPen()
        rec.replay(pen)
        pen._close()
        return tuple(np.asarray(c, dtype=np.float64) for c in pen.contours)

    @functools.lru_cache(maxsize=1024)
    def bounds(self, g: str) -> tuple[float, float, float, float] | None:
        """(x min, y min, x max, y max) of the outline (font units), None for an empty glyph (the space)."""
        cs = self.contours(g)
        if not cs:
            return None
        allp = np.vstack(cs)
        return (float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max()))


class _EmojiFont:
    """Noto Color Emoji (CBDT bitmaps): an emoji -> its glyph(s) with the font's own tables (cmap + the ligatures of
    its 'ccmp' feature: ZWJ sequences, skin tones, flags, keycaps), each glyph's bitmap and metrics."""

    def __init__(self, path: Path) -> None:
        from fontTools.ttLib import TTFont
        self.tt = TTFont(str(path), lazy=True)
        self.upem = float(self.tt["head"].unitsPerEm)
        self.cmap = self.tt.getBestCmap()
        self.hmtx = self.tt["hmtx"]
        ver = str(self.tt["name"].getDebugName(5) or "")          # 'Version 2.034;GOOG;...'
        self.version = ver.split(";")[0].replace("Version", "").strip() or "?"
        strikes = self.tt["CBDT"].strikeData
        sizes = [s.bitmapSizeTable.ppemY for s in self.tt["CBLC"].strikes]
        best = int(np.argmax(sizes))
        self.strike = strikes[best]
        self.ppem = float(sizes[best])
        self.ligatures = self._ligature_lookups()

    def _ligature_lookups(self) -> list[dict[str, list[tuple[tuple[str, ...], str]]]]:
        if "GSUB" not in self.tt:
            return []
        gsub = self.tt["GSUB"].table
        order: list[int] = []
        for fr in gsub.FeatureList.FeatureRecord:
            if fr.FeatureTag in ("ccmp", "liga", "rlig"):
                for i in fr.Feature.LookupListIndex:
                    if i not in order:
                        order.append(i)
        out = []
        for i in sorted(order):
            lk = gsub.LookupList.Lookup[i]
            table: dict[str, list[tuple[tuple[str, ...], str]]] = {}
            for st in lk.SubTable:
                if lk.LookupType == 7:
                    st = st.ExtSubTable
                for first, ligs in (getattr(st, "ligatures", None) or {}).items():
                    for lg in ligs:
                        table.setdefault(first, []).append((tuple(lg.Component), lg.LigGlyph))
            if table:
                for v in table.values():
                    v.sort(key=lambda t: -len(t[0]))            # the longest sequence first
                out.append(table)
        return out

    def has(self, cp: int) -> bool:
        return cp in self.cmap

    def glyphs(self, cluster: str) -> tuple[list[str], list[str]]:
        """(the glyphs that draw ``cluster``, the characters the font does not have)."""
        names, missing = [], []
        for ch in cluster:
            cp = ord(ch)
            if cp in (0xFE0E, 0xFE0F):
                continue
            g = self.cmap.get(cp)
            if g is None:
                missing.append(ch)
            else:
                names.append(g)
        for table in self.ligatures:
            out, i = [], 0
            while i < len(names):
                for comps, lig in table.get(names[i], ()):
                    n = len(comps)
                    if tuple(names[i + 1:i + 1 + n]) == comps:
                        out.append(lig)
                        i += 1 + n
                        break
                else:
                    out.append(names[i])
                    i += 1
            names = out
        return [g for g in names if g in self.strike and self.advance(g) > 0], missing

    def advance(self, g: str) -> float:
        """The glyph's advance (em)."""
        return float(self.hmtx[g][0]) / self.upem

    def metrics(self, g: str) -> tuple[float, float, float, float]:
        """(bearing x, bearing y, width, height) of the glyph's bitmap (bitmap px; bearing y = above the baseline)."""
        with _LOCK:
            m = self.strike[g].metrics
        bx = getattr(m, "horiBearingX", getattr(m, "BearingX", 0))
        by = getattr(m, "horiBearingY", getattr(m, "BearingY", 0))
        return float(bx), float(by), float(m.width), float(m.height)

    @functools.lru_cache(maxsize=256)
    def bitmap(self, g: str) -> Image.Image:
        with _LOCK:
            data = self.strike[g].imageData
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGBA")

    @functools.lru_cache(maxsize=256)
    def ink(self, g: str) -> tuple[int, int, int, int]:
        """The bitmap's visible box (x0, y0, x1, y1), bitmap px."""
        bm = self.bitmap(g)
        return bm.getchannel("A").getbbox() or (0, 0, bm.width, bm.height)


def _text_font(path: str | None = None) -> _TextFont:
    return _text_font_at(str(path) if path else str(text_font_path()))


@functools.lru_cache(maxsize=4)
def _text_font_at(path: str) -> _TextFont:
    with _LOCK:
        return _TextFont(Path(path))


@functools.lru_cache(maxsize=2)
def _emoji_font(path: str | None = None) -> _EmojiFont:
    with _LOCK:
        return _EmojiFont(Path(path) if path else emoji_font_path())


def warm_up() -> None:
    """Read both fonts now (about half a second, once): the first emoji typed then shows without a wait."""
    tf, ef = _text_font(), _emoji_font()
    for ch in "Thea":
        tf.contours(tf.glyph(ch))
    ef.bitmap(ef.glyphs("😭")[0][0])


# ---------------------------------------------------------------------------------------------------------------------
# characters, emoji clusters, words
# ---------------------------------------------------------------------------------------------------------------------

def _is_extend(cp: int) -> bool:
    """A character that belongs to the one before it: variation selectors, skin tones, emoji tags, combining marks."""
    if 0xFE00 <= cp <= 0xFE0F or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F or cp == 0x200C:
        return True
    return unicodedata.category(chr(cp)) in ("Mn", "Me", "Mc")


def _is_flag_letter(cp: int) -> bool:
    return 0x1F1E6 <= cp <= 0x1F1FF


KEYCAP_BASES = frozenset(map(ord, "0123456789#*"))


def _pictographic(cp: int) -> bool:
    """An emoji-like character (Unicode's Extended_Pictographic, near enough): the emoji blocks, or anything beyond
    Latin-1 the emoji font draws -- also emojis newer than the font."""
    if 0x1F000 <= cp <= 0x1FAFF or 0x1FC00 <= cp <= 0x1FFFD or 0x2600 <= cp <= 0x27BF:
        return True
    return cp > 0xFF and not _is_extend(cp) and cp != ZWJ and _emoji_font().has(cp)


def clusters(text: str) -> list[str]:
    """``text`` cut into what you see as one character each (Unicode's grapheme clusters, the parts emojis need):
    a letter with its accents, an emoji with its skin tone / variation selector / keycap, a ZWJ sequence
    (police officer + ZWJ + male sign: a ZWJ joins two emojis, as Unicode says), a flag (two flag letters)."""
    out, i, n = [], 0, len(text)
    while i < n:
        j = i + 1
        pict = _pictographic(ord(text[i]))
        if _is_flag_letter(ord(text[i])) and j < n and _is_flag_letter(ord(text[j])):
            j += 1
        while j < n:
            cp = ord(text[j])
            if _is_extend(cp):
                j += 1
            elif cp == ZWJ:
                j += 1
                if pict and j < n and _pictographic(ord(text[j])):
                    j += 1
            else:
                break
        out.append(text[i:j])
        i = j
    return out


def is_emoji(cluster: str) -> bool:
    """Drawn with the emoji font: a flag, a keycap (1️⃣), or an emoji-like character -- unless the headline font has
    that character and nothing asks for the emoji look (© stays a letter, ©️ with variation selector 16 is an
    emoji)."""
    if not cluster:
        return False
    cps = [ord(c) for c in cluster]
    first, rest = cps[0], cps[1:]
    if _is_flag_letter(first):
        return True
    if first in KEYCAP_BASES:
        return 0x20E3 in rest or 0xFE0F in rest
    if not (_pictographic(first) or (0xFE0F in rest and _emoji_font().has(first))):
        return False
    if _text_font().glyph(cluster[0]) is not None:
        return any(cp in (0xFE0F, ZWJ, 0x20E3) or 0x1F3FB <= cp <= 0x1F3FF for cp in rest)
    return True


def _has_letter(word: str) -> bool:
    return any(unicodedata.category(c)[0] in "LN" for c in word if not is_emoji(c))


def tokens(title: str) -> list[str]:
    """The pieces a line break may fall between: the words of ``title`` (split at spaces). A piece without a letter or
    a digit -- emojis, a lone dash, '!!' -- sticks to the word before it (at the start: to the word after it), with
    the space between kept: 'is hilarious 🥜😭' -> ['is', 'hilarious 🥜😭']."""
    out: list[str] = []
    lead = ""
    for w in title.split():
        if not _has_letter(w):
            if out:
                out[-1] = f"{out[-1]} {w}"
            else:
                lead = f"{lead} {w}".strip()
            continue
        if lead:
            w, lead = f"{lead} {w}", ""
        out.append(w)
    if lead:
        out.append(lead)
    return out


def _plain(word: str) -> str:
    """A word without emojis and the punctuation around it, lower case: 'The,' -> 'the'."""
    letters = "".join(c for c in clusters(word) if not is_emoji(c))
    return letters.strip(" \"'“”‘’()[]{}«»" + PUNCTUATION_END + "-–—…").lower()


# ---------------------------------------------------------------------------------------------------------------------
# one line: its glyphs, advances and ink
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class _Glyph:
    kind: str                  # "text" (a letter, a space ...) or "emoji"
    name: str                  # the glyph's name in its font
    x: float                   # pen position (px at full size, from the line's start)


@dataclass
class _Line:
    text: str
    glyphs: list[_Glyph]
    advance: float             # the advance width (px at full size): what Photoshop centres (minus CENTRE_TRIM)
    ink_left: float            # the ink's left and right edge (px at full size, from the line's start)
    ink_right: float
    box_left: float            # ... and of everything drawn (emoji bitmaps have clear margins around their ink)
    box_right: float
    warnings: list[str]

    @property
    def width(self) -> float:
        return max(0.0, self.ink_right - self.ink_left)


def _measure(text: str) -> _Line:
    return _measure_in(text, (TEXT_FONT, SIZE, TRACKING, BOLD_ADVANCE, BOLD_GROW))


@functools.lru_cache(maxsize=512)
def _measure_in(text: str, style_key: tuple) -> _Line:
    """The glyphs of one line at full size: pen positions with kerning, tracking and Faux Bold's extra advance, the
    advance width and where the ink starts and ends."""
    tf, warnings = _text_font(), []
    glyphs: list[_Glyph] = []
    sc = SIZE / tf.upem
    x, prev = 0.0, None
    for cl in clusters(unicodedata.normalize("NFC", text)):
        if is_emoji(cl):
            ef = _emoji_font()
            names, missing = ef.glyphs(cl)
            if not names:
                warnings.append(f"{cl} is not in the emoji font (Noto Color Emoji {ef.version}): left out")
                continue
            if len(names) > 1 or missing:
                warnings.append(f"{cl} is not one emoji in the emoji font: shown as {len(names)} emojis")
            for g in names:
                glyphs.append(_Glyph("emoji", g, x))
                x += ef.advance(g) * SIZE + TRACKING + EMOJI_BOLD_ADVANCE
            prev = None
            continue
        for ch in cl:
            g = tf.glyph(ch)
            if g is None:
                # accents the font cannot put on (NFC made the ones it has into single letters) and invisible
                # format characters (ZWJ, variation selectors ...) are left out without a word
                if unicodedata.category(ch) not in ("Mn", "Me", "Mc", "Cf") and not _is_extend(ord(ch)):
                    warnings.append(f"'{ch}' is not in the headline font: left out")
                continue
            if prev is not None:
                x += tf.kern(prev, g) * sc
            glyphs.append(_Glyph("text", g, x))
            x += tf.advance(g) * sc + TRACKING + BOLD_ADVANCE
            prev = g
    left, right = math.inf, -math.inf
    box_l, box_r = math.inf, -math.inf
    for gl in glyphs:
        if gl.kind == "text":
            b = tf.bounds(gl.name)
            if b is None:
                continue
            left = min(left, gl.x + b[0] * sc - BOLD_GROW)
            right = max(right, gl.x + b[2] * sc + BOLD_GROW)
            box_l, box_r = min(box_l, left), max(box_r, right)
        else:
            ef = _emoji_font()
            es = SIZE / ef.ppem
            bx, _, bw, _ = ef.metrics(gl.name)
            x0, _, x1, _ = ef.ink(gl.name)
            left = min(left, gl.x + (bx + x0) * es)
            right = max(right, gl.x + (bx + x1) * es)
            box_l, box_r = min(box_l, gl.x + bx * es), max(box_r, gl.x + (bx + bw) * es)
    if left > right:
        left = right = box_l = box_r = 0.0
    return _Line(text, glyphs, x, left, right, min(box_l, left), max(box_r, right), warnings)


def line_width(text: str) -> float:
    """How wide ``text`` is as one headline line at full size: its ink, px on the PSD (2160 wide)."""
    return _measure(" ".join(text.split())).width


# ---------------------------------------------------------------------------------------------------------------------
# the layout: lines, size, place
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Layout:
    """Where the headline goes. Widths and baselines are px on the PSD (2160x3840); halve them for 1080x1920."""
    title: str
    lines: list[str]
    widths: list[float]                      # each line's ink width at full size (before any shrinking)
    scale: float = 1.0                       # 1.0 = the full size (122 pt); smaller = shrunk
    shrunk: bool = False
    why: str = ""                            # plain words: why it was shrunk / joined (empty when all is normal)
    warnings: list[str] = field(default_factory=list)
    baselines: list[float] = field(default_factory=list)
    own_break: bool = False                  # the title's own line break was used
    centre_x: float = CENTRE_X               # the lines are centred on this x
    size_pt: float = 122.0                   # the headline's full size (its channel's style)

    @property
    def scale_pct(self) -> float:
        return round(100.0 * self.scale, 1)

    @property
    def widths_1080(self) -> list[float]:
        """The lines' widths as drawn (shrinking included), px on the 1080x1920 sequence."""
        return [round(w * self.scale / 2.0, 1) for w in self.widths]

    def summary(self) -> str:
        """One line for the app: '2 lines, full size (122 pt)' or the shrink in plain words."""
        if not self.lines:
            return "no headline"
        n = f"{len(self.lines)} line{'s' if len(self.lines) > 1 else ''}"
        return f"{n}, {self.why}" if self.shrunk else f"{n}, full size ({self.size_pt:g} pt)"


def _block_middle(baseline_1: float = BASELINE_1) -> float:
    """The vertical middle of the PSD's 2-line headline (from the top of line 1's capitals to line 2's baseline)."""
    return (baseline_1 - CAP_HEIGHT * SIZE + baseline_1 + LINE_PITCH) / 2.0


def _baselines(n: int, k: float, baseline_1: float = BASELINE_1) -> list[float]:
    """Baselines of ``n`` lines at scale ``k``: the full-size 2 lines exactly where the PSD has them (each on a whole
    pixel, as Photoshop draws them); otherwise centred where the PSD's 2-line block is."""
    if n == 2 and k == 1.0:
        return [float(round(baseline_1)), float(round(baseline_1 + LINE_PITCH))]
    cap = CAP_HEIGHT * SIZE * k
    height = cap + (n - 1) * LINE_PITCH * k
    first = _block_middle(baseline_1) - height / 2.0 + cap
    return [first + i * LINE_PITCH * k for i in range(n)]


def _break_cost(line1: str, w1: float, w2: float) -> float:
    """The longer line (px), more when line 1 ends on a small word, less when it ends after punctuation."""
    cost = max(w1, w2)
    last = next((w for w in reversed(line1.split()) if _has_letter(w)), "")       # its last word (not an emoji)
    if _plain(last) in WEAK_WORDS:
        cost += WEAK_END
    letters = "".join(c for c in clusters(last) if not is_emoji(c)).rstrip(" \"'”’)")
    if letters and letters[-1] in PUNCTUATION_END:
        cost -= PUNCT_END
    return cost


def _best_break(toks: list[str], max_width: float) -> tuple[list[str], list[float], bool]:
    """(the 2 lines, their widths, whether both fit): the cheapest break that fits; else the one with the shortest
    longer line."""
    best_fit = best_any = None
    for i in range(1, len(toks)):
        l1, l2 = " ".join(toks[:i]), " ".join(toks[i:])
        w1, w2 = line_width(l1), line_width(l2)
        if max(w1, w2) <= max_width:
            c = _break_cost(l1, w1, w2)
            if best_fit is None or c < best_fit[0]:
                best_fit = (c, [l1, l2], [w1, w2])
        if best_any is None or max(w1, w2) < best_any[0]:
            best_any = (max(w1, w2), [l1, l2], [w1, w2])
    if best_fit is not None:
        return best_fit[1], best_fit[2], True
    return best_any[1], best_any[2], False


def layout(title: str, max_width: float = MAX_WIDTH,
           anchor: tuple[float, float] | None = None) -> Layout:
    """The headline's lines for ``title``: your own line break (a new line in the title) wins; else the most natural
    break with both lines inside ``max_width`` (px on the PSD); one word: one line; nothing fits: shrunk.
    ``anchor``: (centre x, first baseline) of a template whose headline sits elsewhere (default: the channels')."""
    cx, b1 = anchor if anchor else (CENTRE_X, BASELINE_1)
    title = title or ""
    parts = [p for p in title.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    parts = [" ".join(p.split()) for p in parts]
    parts = [p for p in parts if p]
    why_extra = ""
    own = len(parts) >= 2
    if own:
        if len(parts) > 2:
            extra = "line 3 was" if len(parts) == 3 else f"lines 3-{len(parts)} were"
            why_extra = f"the headline has 2 lines: {extra} added to line 2"
            parts = [parts[0], " ".join(parts[1:])]
        lines, widths = parts, [line_width(p) for p in parts]
    else:
        toks = tokens(parts[0] if parts else "")
        if not toks:
            return Layout(title, [], [], warnings=[], baselines=[], centre_x=cx, size_pt=SIZE)
        if len(toks) == 1:
            lines, widths = toks, [line_width(toks[0])]
        else:
            lines, widths, _ = _best_break(toks, max_width)
    warnings: list[str] = []
    for ln in lines:
        for w in _measure(ln).warnings:
            if w not in warnings:
                warnings.append(w)
    widest = max(widths) if widths else 0.0
    k, shrunk, why = 1.0, False, ""
    if widest > max_width:
        k = max_width / widest
        shrunk = True
        px = round(max_width / 2)
        if len(lines) == 1:
            why = f"shrunk to {100 * k:.0f} %: the title is wider than {px} px on one line"
        elif own:
            why = f"shrunk to {100 * k:.0f} %: your line break leaves a line wider than {px} px"
        else:
            why = f"shrunk to {100 * k:.0f} %: no break keeps both lines inside {px} px"
    if why_extra:
        warnings.append(why_extra)
    return Layout(title, lines, widths, k, shrunk, why, warnings, _baselines(len(lines), k, b1), own, cx, SIZE)


# ---------------------------------------------------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------------------------------------------------

def _supersample(sc: float) -> int:
    """Letters are drawn this many times finer than the output and averaged down."""
    return 4 if sc >= 0.6 else 8


def _fill(acc: np.ndarray, pts: np.ndarray, shift: int = 8) -> np.ndarray:
    """A 0/1 mask of the polygon ``pts`` (SS px, pixel centres at +0.5) on an array shaped like ``acc``."""
    import cv2
    m = np.zeros(acc.shape, np.uint8)
    q = np.round((pts - 0.5) * (1 << shift)).astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(m, [q], 1, lineType=cv2.LINE_8, shift=shift)
    return m


def _text_mask(line: _Line, k: float, sc: float, ox: float, oy: float, w: int, h: int, ss: int) -> np.ndarray:
    """The letters of ``line`` as a 0/255 mask at ``ss`` times the output resolution: pen origin (ox, oy) output px
    from the strip's top left; outlines filled with the nonzero rule, glyph by glyph."""
    tf = _text_font()
    mask = np.zeros((h * ss, w * ss), np.uint8)
    f = SIZE * k / tf.upem * sc * ss                      # font units -> SS px
    for gl in line.glyphs:
        if gl.kind != "text":
            continue
        cs = tf.contours(gl.name)
        if not cs:
            continue
        px = (ox + gl.x * k * sc) * ss
        py = oy * ss
        polys = [np.column_stack((px + c[:, 0] * f, py - c[:, 1] * f)) for c in cs]
        allp = np.vstack(polys)
        x0 = max(int(math.floor(allp[:, 0].min())) - 1, 0)
        y0 = max(int(math.floor(allp[:, 1].min())) - 1, 0)
        x1 = min(int(math.ceil(allp[:, 0].max())) + 2, mask.shape[1])
        y1 = min(int(math.ceil(allp[:, 1].max())) + 2, mask.shape[0])
        if x1 <= x0 or y1 <= y0:
            continue
        acc = np.zeros((y1 - y0, x1 - x0), np.int16)
        for c, p in zip(cs, polys):
            area = float(np.sum(c[:, 0] * np.roll(c[:, 1], -1) - np.roll(c[:, 0], -1) * c[:, 1]))
            m = _fill(acc, p - (x0, y0))
            if area >= 0:
                acc += m
            else:
                acc -= m
        sub = mask[y0:y1, x0:x1]
        np.maximum(sub, (acc != 0).astype(np.uint8) * 255, out=sub)
    return mask


def _bold(mask: np.ndarray, grow_ss: float) -> np.ndarray:
    """Faux Bold: the ink grown by ``grow_ss`` SS px on every side (round corners, like a stroke)."""
    import cv2
    if grow_ss <= 0:
        return mask
    d = cv2.distanceTransform(255 - mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    # a background pixel next to the ink is 1 px from the nearest ink pixel's centre but about 0.5 px from the edge
    return np.where(d <= grow_ss + 0.5, 255, 0).astype(np.uint8)


def _coverage(mask: np.ndarray, ss: int) -> np.ndarray:
    h, w = mask.shape[0] // ss, mask.shape[1] // ss
    return mask.reshape(h, ss, w, ss).mean(axis=(1, 3)) / 255.0


def _alpha(cov: np.ndarray) -> np.ndarray:
    """Coverage -> alpha. Plain coverage matched Photoshop's "Sharp" anti-aliasing best (a steeper edge did worse)."""
    return np.round(cov * 255.0).astype(np.uint8)


def _draw_emoji(strip: Image.Image, g: str, x: float, y: float, s: float) -> None:
    """The emoji bitmap ``g`` scaled by ``s`` with its top left at (x, y) on ``strip`` (sub-pixel exact)."""
    bm = _emoji_font().bitmap(g).convert("RGBa")       # premultiplied: no dark fringes when resampling
    w = int(math.ceil(x + bm.width * s)) - int(math.floor(x)) + 2
    h = int(math.ceil(y + bm.height * s)) - int(math.floor(y)) + 2
    ix, iy = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - ix, y - iy
    a = 1.0 / s
    im = bm.transform((w, h), Image.Transform.AFFINE, (a, 0, -fx * a, 0, a, -fy * a),
                      resample=Image.Resampling.BICUBIC).convert("RGBA")
    cx, cy = max(0, -ix), max(0, -iy)                        # only the part on the strip
    if cx < im.width and cy < im.height:
        strip.alpha_composite(im, (ix + cx, iy + cy), (cx, cy))


def _draw_line(line: _Line, k: float, baseline: float, sc: float,
               centre_x: float = CENTRE_X) -> tuple[Image.Image, int, int]:
    """One line on its own transparent strip: (the strip, its left and top, output px)."""
    tf, ss = _text_font(), _supersample(sc)
    x0 = centre_x - k * (line.advance - CENTRE_TRIM) / 2.0          # the pen's start, PSD px
    top, bottom = 0.0, 0.0
    for gl in line.glyphs:
        if gl.kind == "text":
            b = tf.bounds(gl.name)
            if b is not None:
                top = max(top, b[3] * SIZE / tf.upem + BOLD_GROW)
                bottom = max(bottom, -b[1] * SIZE / tf.upem + BOLD_GROW)
        else:
            ef = _emoji_font()
            es = SIZE / ef.ppem
            _, by, _, bh = ef.metrics(gl.name)
            top = max(top, by * es + EMOJI_RISE)
            bottom = max(bottom, (bh - by) * es - EMOJI_RISE)
    left = int(math.floor((x0 + k * line.box_left) * sc)) - 3
    right = int(math.ceil((x0 + k * line.box_right) * sc)) + 3
    ytop = int(math.floor((baseline - k * top) * sc)) - 3
    ybot = int(math.ceil((baseline + k * bottom) * sc)) + 3
    w, h = max(1, right - left), max(1, ybot - ytop)
    ox, oy = x0 * sc - left, baseline * sc - ytop                     # the pen origin on the strip
    mask = _text_mask(line, k, sc, ox, oy, w, h, ss)
    mask = _bold(mask, BOLD_GROW * k * sc * ss)
    alpha = _alpha(_coverage(mask, ss))
    strip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    strip.putalpha(Image.fromarray(alpha, "L"))
    ef = None
    for gl in line.glyphs:
        if gl.kind != "emoji":
            continue
        ef = ef or _emoji_font()
        es = SIZE / ef.ppem * k * sc
        bx, by, _, _ = ef.metrics(gl.name)
        _draw_emoji(strip, gl.name, ox + gl.x * k * sc + bx * es, oy - by * es - EMOJI_RISE * k * sc, es)
    return strip, left, ytop


def render(title: str, size: tuple[int, int] = CANVAS, lay: Layout | None = None,
           anchor: tuple[float, float] | None = None) -> tuple[Image.Image, Layout]:
    """(the headline alone on a transparent canvas of ``size``, its Layout). ``size`` is the PSD's 2160x3840 or any
    size of the same shape (540x960 for a preview): the headline is drawn directly at that size. ``lay``: a layout
    made before (else ``layout(title, anchor=anchor)``)."""
    lay = lay or layout(title, anchor=anchor)
    W, H = int(size[0]), int(size[1])
    sc = W / float(CANVAS[0])
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for text, base in zip(lay.lines, lay.baselines):
        strip, x, y = _draw_line(_measure(text), lay.scale, base, sc, lay.centre_x)
        cx, cy = max(0, -x), max(0, -y)                       # the part of the strip inside the canvas
        if cx < strip.width and cy < strip.height:
            out.alpha_composite(strip, (x + cx, y + cy), (cx, cy))
    return out, lay
