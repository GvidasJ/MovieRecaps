"""The channels and their templates: one folder each in assets/channels (template.psd + watermark.png, and a
channel.json with its link) -- a new folder is a new channel, no code change.

A channel's template is its PSD's merged image (what Photoshop shows: the white card, the channel's picture, name and
@handle, the transparent video window) with the PSD's example headline taken out, plus your headline
(mr_app.core.headline) -> the frame PNG of the edit (V2), 1080x1920 like your finished videos.

Taking the example headline out: the PSD's visible layers WITHOUT the headline's type layer are composited in the
headline area (x 150-2010, y 740-1035 of the 2160x3840 PSD); when that is plain white there, the area is painted white
in the merged image; otherwise (something else shows there) that composite of the visible layers without the headline
is the base instead. The example headline's position (its centre x and first baseline) is where your headline goes.
The result is cached as a PNG in work/channel_cache (opening a 45 MB PSD takes seconds) and built again when the PSD
changes (its size or time stamp)."""
from __future__ import annotations

import functools
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import headline, paths

PSD_NAME = "template.psd"
PNG_NAME = "template.png"                  # night 15: a channel without a PSD (SceneProof): its base as a 2160x3840 PNG --
#                                            the card without a headline, the video window transparent
EXAMPLE_NAME = "example.png"               # ... and its "default template as it is" (with an example headline)
WATERMARK_NAME = "watermark.png"
INFO_NAME = "channel.json"
HEADLINE_AREA = (150, 740, 2010, 1035)     # x0, y0, x1, y1 on the PSD: the example headline's place (plain white)
WINDOW = (44, 1054, 2114, 3473)            # the transparent video window on the PSD (x0, y0, x1, y1, as measured)
SEQUENCE = (1080, 1920)                    # the edit's sequence: the frame PNG's size
WHITE_TOLERANCE = 2                        # "plain white": every pixel at least 255 - this in R, G and B, opaque
CACHE_VERSION = 2                          # bump when the way the base is made changes (2: + the headline anchor)


@dataclass(frozen=True)
class Channel:
    name: str
    folder: Path
    psd: Path
    watermark: Path | None
    link: str
    handle: str
    shorts_only: bool = False
    music: "Music | None" = None


@dataclass(frozen=True)
class Music:
    """Night 15: a channel's song, always on A2 of its edits (SceneProof: "music" in its channel.json) -- the file in the
    channel's folder, from ``start_s`` into the song, cut at ``split_s`` of the edit, ``gains_db`` before / after."""
    file: Path
    start_s: float = 0.0
    split_s: float = 0.0
    gains_db: tuple[float, ...] = (0.0,)

    def args(self) -> list[str]:
        """The tool's switches (match_cuts --music ...)."""
        return ["--music", str(self.file), "--music-start", f"{self.start_s:g}", "--music-split", f"{self.split_s:g}",
                "--music-gains", ",".join(f"{g:g}" for g in self.gains_db)]


def _music(folder: Path, spec: Any) -> Music | None:
    """channel.json's "music": {"file", "start_s", "split_s", "gains_db"} -> Music (None: none, or its file missing)."""
    if not isinstance(spec, dict) or not spec.get("file"):
        return None
    p = Path(str(spec["file"]))
    p = p if p.is_absolute() else folder / p
    if not p.is_file():
        return None
    try:
        gains = tuple(float(g) for g in (spec.get("gains_db") or [0.0]))
        return Music(p, float(spec.get("start_s") or 0.0), float(spec.get("split_s") or 0.0), gains)
    except (TypeError, ValueError):
        return None


@dataclass
class TemplateResult:
    png: Path
    layout: headline.Layout
    shrunk: bool
    scale_pct: float
    why: str
    warnings: list[str] = field(default_factory=list)
    hole: tuple[float, float, float, float] | None = None      # the video window on the PNG (x, y, w, h), its px

    def summary(self) -> str:
        """One line for the app."""
        return self.layout.summary()


# ---------------------------------------------------------------------------------------------------------------------
# the channel folders
# ---------------------------------------------------------------------------------------------------------------------

def _defaults(name: str) -> dict:
    handle = "@" + re.sub(r"\s+", "", name)
    return {"name": name, "link": f"https://www.youtube.com/{handle}", "handle": handle, "shorts_only": False,
            "music": None, "headline_anchor": None}


def read_info(folder: Path) -> dict:
    """The channel's channel.json ({name, link, handle, shorts_only}); what it lacks (or no file): from the folder's
    name (@<folder name>, https://www.youtube.com/@<folder name>)."""
    info = _defaults(folder.name)
    p = folder / INFO_NAME
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            info.update({k: v for k, v in data.items() if k in info and v not in (None, "")})
    info["shorts_only"] = bool(info["shorts_only"])
    return info


def channels(root: Path | None = None) -> list[Channel]:
    """Every channel: a folder of ``root`` (assets/channels) with a template.psd (or, night 15, a template.png),
    sorted by name."""
    root = Path(root) if root is not None else paths.channels_dir()
    out = []
    if not root.is_dir():
        return out
    for folder in sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name.lower()):
        psd = folder / PSD_NAME
        if not psd.is_file():
            psd = folder / PNG_NAME
            if not psd.is_file():
                continue
        info = read_info(folder)
        wm = folder / WATERMARK_NAME
        out.append(Channel(str(info["name"]), folder, psd, wm if wm.is_file() else None, str(info["link"]),
                           str(info["handle"]), bool(info["shorts_only"]), _music(folder, info.get("music"))))
    return out


def find(name: str, root: Path | None = None) -> Channel:
    """The channel called ``name`` (its name or folder name, any case); LookupError when there is none."""
    for ch in channels(root):
        if name.lower() in (ch.name.lower(), ch.folder.name.lower()):
            return ch
    raise LookupError(f"no channel '{name}' in {root or paths.channels_dir()}")


# ---------------------------------------------------------------------------------------------------------------------
# the base: the PSD's merged image without the example headline
# ---------------------------------------------------------------------------------------------------------------------

def cache_dir() -> Path:
    return paths.work_dir() / "channel_cache"


def _key(psd: Path) -> str:
    st = psd.stat()
    return f"v{CACHE_VERSION}_{st.st_size}_{st.st_mtime_ns}"


def _cache_files(ch: Channel) -> tuple[Path, Path]:
    d = cache_dir() / re.sub(r"[^\w.-]+", "_", ch.folder.name)
    return d / "base_2160x3840.png", d / "base.json"


def _headline_layer(psd):
    """The PSD's example headline: its visible type layer in the headline area (the NeueMontreal one first)."""
    x0, y0, x1, y1 = HEADLINE_AREA
    best = None
    for layer in psd.descendants():
        if layer.kind != "type" or not layer.is_visible():
            continue
        bx0, by0, bx1, by1 = layer.bbox
        overlap = max(0, min(x1, bx1) - max(x0, bx0)) * max(0, min(y1, by1) - max(y0, by0))
        if overlap <= 0:
            continue
        neue = "NeueMontreal" in str(layer.resource_dict.get("FontSet", ""))
        score = (neue, overlap)
        if best is None or score > best[0]:
            best = (score, layer)
    return best[1] if best else None


def _anchor(layer) -> list[float] | None:
    """(centre x, first baseline) of a headline type layer -- its position -- when it is centred point text that is
    neither scaled nor turned (the channels' are: 1085.46, 849.20); else None (the standard place is used)."""
    try:
        xx, xy, yx, yy, tx, ty = (float(v) for v in layer.transform)
        ed = layer.engine_dict
        shape = ed["Rendered"]["Shapes"]["Children"][0]["ShapeType"]
        just = ed["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"].get("Justification")
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None
    if (xx, xy, yx, yy) != (1.0, 0.0, 0.0, 1.0) or int(shape) != 0 or int(just or 0) != 2:
        return None
    return [round(tx, 4), round(ty, 4)]


def _area_white(img: Image.Image, area: tuple[int, int, int, int]) -> tuple[bool, str]:
    """(plain white?, what was measured) of ``img`` (RGBA, the PSD's ``area`` x0, y0, x1, y1 composited alone)."""
    a = np.asarray(img.convert("RGBA")).astype(np.int16)
    n = a.shape[0] * a.shape[1]
    off = int(((a[..., :3] < 255 - WHITE_TOLERANCE).any(axis=2) | (a[..., 3] < 255)).sum())
    what = (f"x {area[0]}-{area[2]}, y {area[1]}-{area[3]} without the headline: {n - off:,} of {n:,} pixels white "
            f"(darkest {int(a[..., :3].min())}/255, least opaque {int(a[..., 3].min())}/255)")
    return off == 0, what


def build_base(ch: Channel) -> tuple[Image.Image, dict]:
    """(the base RGBA 2160x3840, what was done) -- straight from the PSD (slow: seconds); base_template caches it. A
    template.png (night 15) is the base as it is (2160x3840; another size is scaled), the headline where channel.json's
    "headline_anchor" says (else the standard place)."""
    if ch.psd.suffix.lower() == ".png":
        with Image.open(ch.psd) as im:
            img = im.convert("RGBA")
        size = (SEQUENCE[0] * 2, SEQUENCE[1] * 2)
        if img.size != size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        a = read_info(ch.folder).get("headline_anchor")
        anchor = [float(a[0]), float(a[1])] if isinstance(a, (list, tuple)) and len(a) == 2 else None
        return img, {"png": str(ch.psd), "size": list(img.size), "method": "template.png as it is",
                     "why": "a channel without a PSD", "anchor": anchor}
    from psd_tools import PSDImage
    psd = PSDImage.open(ch.psd)
    merged = psd.topil()
    if merged is None:
        merged = psd.composite()
    merged = merged.convert("RGBA")
    hl = _headline_layer(psd)
    info: dict = {"psd": str(ch.psd), "size": list(merged.size)}
    if hl is None:
        info.update(method="merged image as it is", why="no visible type layer in the headline area")
        return merged, info
    info["headline_layer"] = {"name": hl.name, "bbox": list(hl.bbox), "text": str(hl.text)}
    info["anchor"] = _anchor(hl)
    x0, y0, x1, y1 = HEADLINE_AREA
    bx0, by0, bx1, by1 = hl.bbox
    area = (min(x0, bx0 - 2), min(y0, by0 - 2), max(x1, bx1 + 2), max(y1, by1 + 2))   # the whole layer, always
    hid = hl.layer_id

    def others(layer) -> bool:
        return layer.is_visible() and layer.layer_id != hid
    without = psd.composite(viewport=area, layer_filter=others)
    white, what = _area_white(without, area)
    info.update(area=list(area), measured=what)
    if white:
        base = merged.copy()
        base.paste((255, 255, 255, 255), area)
        info.update(method="merged image, headline area painted white",
                    why="the visible layers without the headline are plain white there")
        return base, info
    full = psd.composite(layer_filter=others).convert("RGBA")
    info.update(method="the visible layers without the headline (composited by psd-tools)",
                why="the headline area is not plain white without the headline: " + what)
    return full, info


_BUILD_LOCK = threading.Lock()


def _cached(ch: Channel) -> tuple[Path, dict, Image.Image | None]:
    """(the cached base PNG, what was done, the base when it was just built): the cache made first when it is missing
    or older than the PSD (another size or time stamp)."""
    png, meta = _cache_files(ch)
    key = _key(ch.psd)
    with _BUILD_LOCK:
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if info.get("key") == key and png.is_file():
                return png, info, None
        except (OSError, ValueError):
            pass
        base, info = build_base(ch)
        info = {"key": key, **info}
        png.parent.mkdir(parents=True, exist_ok=True)
        tmp = png.with_suffix(".tmp.png")
        base.save(tmp)
        tmp.replace(png)
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        return png, info, base


def base_template(ch: Channel) -> Image.Image:
    """The channel's template without the example headline: RGBA 2160x3840, the video window transparent. Cached in
    work/channel_cache/<channel>/ and built again when the PSD changes."""
    png, _, base = _cached(ch)
    if base is not None:
        return base
    with Image.open(png) as im:
        return im.convert("RGBA")


def base_info(ch: Channel) -> dict:
    """What base_template did for ``ch`` (method, why, what was measured, the headline's anchor), from its cache
    (built when needed)."""
    return dict(_cached(ch)[1])


def headline_anchor(ch: Channel) -> tuple[float, float]:
    """Where the channel's headline goes: (centre x, first baseline) of its PSD's example headline, PSD px -- the
    standard place (1085.46, 849.20) when the PSD does not say."""
    a = base_info(ch).get("anchor")
    return (float(a[0]), float(a[1])) if a else (headline.CENTRE_X, headline.BASELINE_1)


# ---------------------------------------------------------------------------------------------------------------------
# the template with your headline
# ---------------------------------------------------------------------------------------------------------------------

def downscale(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """``img`` (RGBA) at ``size``: area averaging (the 2x2 mean for 2160x3840 -> 1080x1920, premultiplied): no
    ringing, so the window stays exactly transparent and the card exactly opaque. It matched your finished frames
    best."""
    size = (int(size[0]), int(size[1]))
    if img.size == size:
        return img.copy()
    return img.resize(size, Image.Resampling.BOX)


def compose(ch: Channel, title: str) -> tuple[Image.Image, headline.Layout]:
    """(the template with ``title`` at 2160x3840, its layout)."""
    base = base_template(ch)
    hl, lay = headline.render(title, anchor=headline_anchor(ch))
    out = base.copy()
    out.alpha_composite(hl)
    return out, lay


def _hole(png: Path) -> tuple[float, float, float, float] | None:
    """The video window of a frame PNG, found the way the edit finds it (match_cuts.frame.load_frame)."""
    import sys
    try:
        from match_cuts.frame import load_frame
    except ImportError:                      # not installed in this Python: the tool's folder
        sys.path.append(str(paths.repo() / "tools" / "match_cuts"))
        try:
            from match_cuts.frame import load_frame
        except ImportError:
            return None
    try:
        return tuple(load_frame(png).hole)
    except ValueError:                       # no usable transparent window in it
        return None


def make_template(ch: Channel, title: str, out_png: str | Path,
                  size: tuple[int, int] = SEQUENCE) -> TemplateResult:
    """Write the channel's template with your headline to ``out_png`` (1080x1920 unless ``size`` says otherwise): the
    frame PNG for the edit (V2). The result says the layout, whether it was shrunk (and why), any warning, and the
    video window the edit will find in it."""
    img, lay = compose(ch, title)
    img = downscale(img, size)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return TemplateResult(out, lay, lay.shrunk, lay.scale_pct, lay.why, list(lay.warnings), _hole(out))


@functools.lru_cache(maxsize=8)
def _preview_base(ch: Channel, key: str, width: int) -> Image.Image:
    base = base_template(ch)
    headline.warm_up()                         # the fonts now, not at the first emoji typed
    return downscale(base, (width, round(width * base.height / base.width)))


def preview(ch: Channel, title: str, width: int = 540) -> Image.Image:
    """The template with ``title`` at ``width`` px wide (RGBA; the window transparent) -- fast enough to redraw on
    every key press: the base is kept at this size, the headline is drawn directly at it."""
    base = _preview_base(ch, _key(ch.psd), int(width))
    hl, _ = headline.render(title, size=base.size, anchor=_preview_anchor(ch, _key(ch.psd)))
    out = base.copy()
    out.alpha_composite(hl)
    return out


@functools.lru_cache(maxsize=8)
def _preview_anchor(ch: Channel, key: str) -> tuple[float, float]:
    return headline_anchor(ch)
