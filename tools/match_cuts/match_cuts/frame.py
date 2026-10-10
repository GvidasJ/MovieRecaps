"""frame.py: ``--frame PNG`` -- the channel's overlay: a PNG (input/frame.png: 2160x3840) with a header and a headline
around a TRANSPARENT hole where the video goes.

``load_frame`` finds the hole from the alpha channel: the largest connected area of pixels more transparent than
ALPHA_HOLE (a faint watermark inside it -- "FLICK707" at a few percent -- belongs to the hole), as its bounding box
(rounded corners included: the video covers the whole box, the frame's corners hide what is behind them).

``Frame.window(W, H)`` is that hole on a W x H sequence (the PNG scaled to the sequence: same aspect, or stretched
like Premiere's "Scale to Frame Size"): the Premiere export frames every clip to COVER it (export_xml_edl:
premiere_settings' window -- the competitor's framing mapped into the hole, zoomed only as much as needed, the person
speaking kept in it) instead of the 998x1037 template window, the PNG goes on V2 over V1 for the whole edit, and the
captions keep inside the hole (``caption_zone``: below the hole's top quarter -- never over the header or the
headline -- and above its bottom edge).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ALPHA_HOLE = 128            # a pixel at most this opaque (0-255) is part of the hole
MIN_HOLE_FRAC = 0.05        # a hole smaller than this share of the picture is no video window (a stray clear pixel)
CAPTION_TOP = 0.35          # captions start at least this far down the hole (the headline sits just above it) ...
CAPTION_BOTTOM = 0.92       # ... and end above this much of it


@dataclass
class Frame:
    """A frame PNG: its file, own size (px) and the hole's bounding box (x, y, w, h) in its own px."""
    path: str
    width: int
    height: int
    hole: tuple[float, float, float, float]
    hole_frac: float                     # the hole's share of the picture (transparent pixels / all)

    def scale_to(self, W: int, H: int) -> tuple[float, float]:
        """(sx, sy): PNG px -> sequence px for a W x H sequence (the PNG scaled to fill it)."""
        return W / float(self.width), H / float(self.height)

    def window(self, W: int, H: int) -> tuple[float, float, float, float]:
        """The hole on a W x H sequence: (x, y, w, h) corner px -- the window every clip must cover."""
        sx, sy = self.scale_to(W, H)
        x, y, w, h = self.hole
        return (x * sx, y * sy, w * sx, h * sy)

    def caption_zone(self, H: int, W: int | None = None) -> tuple[float, float]:
        """(top, bottom) sequence px where captions may sit: inside the hole, clear of the header and headline."""
        x, y, w, h = self.window(W or int(round(H * self.width / self.height)), H)
        return y + CAPTION_TOP * h, y + CAPTION_BOTTOM * h

    def premiere_scale(self, W: int, H: int) -> float:
        """The PNG's Scale in Premiere (%, its native size = 100) to fill a W x H sequence (the larger factor when the
        aspect differs, so no edge shows)."""
        sx, sy = self.scale_to(W, H)
        return 100.0 * max(sx, sy)

    def to_dict(self, W: int, H: int) -> dict:
        x, y, w, h = self.window(W, H)
        top, bottom = self.caption_zone(H, W)
        return {**asdict(self), "sequence": [W, H], "window": [round(v, 3) for v in (x, y, w, h)],
                "window_frac": [round(x / W, 5), round(y / H, 5), round(w / W, 5), round(h / H, 5)],
                "caption_zone": [round(top, 1), round(bottom, 1)],
                "caption_zone_frac": [round(top / H, 5), round(bottom / H, 5)],
                "png_scale": round(self.premiere_scale(W, H), 4)}


def load_frame(path: str | Path) -> Frame:
    """The frame PNG at ``path`` and its hole (module docstring); ValueError when it has no usable transparent hole."""
    from PIL import Image
    from scipy import ndimage
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"no such file: {p}")
    with Image.open(p) as im:
        im.load()
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        if not has_alpha:
            raise ValueError(f"{p.name} has no transparency (no alpha channel): the video would be hidden under it -- "
                             "save it as a PNG with a transparent hole where the video goes")
        a = np.asarray(im.convert("RGBA"))[..., 3]
        w, h = im.size
    clear = a < ALPHA_HOLE
    lab, n = ndimage.label(clear)
    if n == 0:
        raise ValueError(f"{p.name} has no transparent area: nowhere for the video to show")
    sizes = np.bincount(lab.ravel())[1:]
    k = int(np.argmax(sizes)) + 1
    frac = float(sizes[k - 1]) / float(a.size)
    if frac < MIN_HOLE_FRAC:
        raise ValueError(f"{p.name}: its largest transparent area is only {100 * frac:.1f} % of the picture -- not a "
                         "window for the video")
    ys, xs = np.nonzero(lab == k)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return Frame(str(p.resolve()), int(w), int(h), (float(x0), float(y0), float(x1 - x0), float(y1 - y0)),
                 round(frac, 5))


def sequence_size(cfg) -> tuple[int, int]:
    """The sequence size of a --frame run (cfg.frame_size, default 2160x3840)."""
    import re
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(getattr(cfg, "frame_size", "") or "2160x3840"))
    return (int(m.group(1)), int(m.group(2))) if m else (2160, 3840)


def apply_to_config(cfg) -> Frame | None:
    """--frame: the Premiere settings of ``cfg`` set from the frame (the sequence size, the window = the hole on it);
    the Frame, or None without --frame."""
    if not str(getattr(cfg, "frame_png", "") or ""):
        return None
    fr = load_frame(cfg.frame_png)
    W, H = sequence_size(cfg)
    cfg.premiere_size = f"{W}x{H}"
    cfg.premiere_window = tuple(round(v, 6) for v in fr.window(W, H))
    return fr


def write_info(fr: Frame, cfg, path: str | Path) -> Path:
    """extras/frame.json: the frame, its hole on the sequence and the caption zone (restyle checks the captions'
    position against it)."""
    W, H = sequence_size(cfg)
    q = Path(path)
    q.write_text(json.dumps(fr.to_dict(W, H), indent=1), encoding="utf-8", newline="\n")
    return q
