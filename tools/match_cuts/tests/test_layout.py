"""Unit tests for match_cuts.layout (DESIGN §5 layout.py, prompt Stage 4).

Synthetic competitor-like frame stacks are rendered with numpy/cv2 at FULL resolution and reduced to
the proxy with cv2.INTER_AREA (exactly like proxies.build_proxy), so box edges that fall between proxy
pixels are exercised. One test builds the proxy from a real lavfi-made H.264 file (media.VideoReader +
cv2.resize) to cover decoding, compression and colour sampling.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import pytest

from match_cuts import layout as L
from match_cuts.common import Cache, DecisionLog
from match_cuts.config import Config
from match_cuts.geometry import rounded_rect_mask
from match_cuts.model import Box, Layout, Proxy

FULL_W, FULL_H = 1080, 1920
TRUTH_BOX = (60, 460, 960, 1000, 40)            # the synthetic test's box (DESIGN §6)
CAPTIONS = [("THIS", 4, 14), ("IS", 14, 24), ("THE", 24, 34), ("MOMENT", 40, 52), ("I", 52, 60), ("SAW", 60, 70)]
FULLSCREEN = (30, 38)
FLASH = 71                                      # one full-canvas white flash frame
N_FRAMES = 72
FONT = cv2.FONT_HERSHEY_DUPLEX
from portable import ff_path, font_file  # noqa: E402

TTF = ff_path(font_file("bold"), quoted=False)          # DejaVu Sans Bold on Linux, Arial Bold on Windows
MONO = ff_path(font_file("mono"), quoted=False)


# ----------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------

def make_proxy(frames: np.ndarray, full_size: tuple[int, int], path: str = "", npy_path: str = "") -> Proxy:
    n, h, w = frames.shape
    return Proxy("competitor", path, frames, full_size, (w / full_size[0], h / full_size[1]), Fraction(30),
                 np.arange(n) / 30.0, n, npy_path)


def hole_mask(W: int, H: int, box: tuple) -> np.ndarray:
    """Rounded box tested at pixel centres (the synthetic generator's geq rule)."""
    x, y, w, h, r = box
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = x + w / 2.0, y + h / 2.0
    dx = np.maximum(np.abs(xx + 0.5 - cx) - (w / 2.0 - r), 0)
    dy = np.maximum(np.abs(yy + 0.5 - cy) - (h / 2.0 - r), 0)
    return (xx >= x) & (xx < x + w) & (yy >= y) & (yy < y + h) & (np.hypot(dx, dy) <= r)


def texture(W: int, H: int, seed: int, pad: int = 400) -> np.ndarray:
    """Smooth random texture (uint8), larger than the frame so it can be translated."""
    rng = np.random.default_rng(seed)
    t = rng.random(((H + pad) // 8, (W + pad) // 8)).astype(np.float32)
    t = cv2.resize(t, (W + pad, H + pad), interpolation=cv2.INTER_CUBIC)
    t = cv2.GaussianBlur(t, (0, 0), 3)
    t = (t - t.min()) / (t.max() - t.min())
    return np.clip(40 + 190 * t, 0, 255).astype(np.uint8)


def text_mask(shape: tuple[int, int], text: str, org: tuple[int, int], scale: float, thick: int) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    cv2.putText(m, text, org, FONT, scale, 255, thick, cv2.LINE_AA)
    return m


def put_outlined(img: np.ndarray, text: str, org: tuple[int, int], scale: float, col, thick: int, border: int) -> None:
    """Text with a black outline of ``border`` px (OpenCV 5 putText ignores thickness > 2, so the outline
    is the dilated glyph mask). Composited inside the text bbox only."""
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thick)
    pad = border + 4
    x0, y0 = max(0, org[0] - pad), max(0, org[1] - th - pad)
    x1, y1 = min(img.shape[1], org[0] + tw + pad), min(img.shape[0], org[1] + base + pad)
    sub = img[y0:y1, x0:x1].astype(np.float32)
    m = text_mask(sub.shape[:2], text, (org[0] - x0, org[1] - y0), scale, thick)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * border + 1, 2 * border + 1))
    a_o = (cv2.dilate(m, ker).astype(np.float32) / 255.0)
    a_t = m.astype(np.float32) / 255.0
    if sub.ndim == 3:
        a_o, a_t = a_o[..., None], a_t[..., None]
    sub = sub * (1 - a_o)
    sub = sub * (1 - a_t) + np.asarray(col, np.float32) * a_t
    img[y0:y1, x0:x1] = np.clip(np.round(sub), 0, 255).astype(np.uint8)


def caption_org(word: str, box: tuple, scale: float = 2.4, thick: int = 5) -> tuple[int, int]:
    (tw, _th), _ = cv2.getTextSize(word, FONT, scale, thick)
    x, y, w, h, _r = box
    return int(x + (w - tw) / 2), int(y + 0.76 * h)


def render_stack(n: int = N_FRAMES, box: tuple = TRUTH_BOX, captions=CAPTIONS, fullscreen=FULLSCREEN, counter=True,
                 seed: int = 0, W: int = FULL_W, H: int = FULL_H, flash: int | None = None) -> dict:
    """Competitor-like stack at full res -> INTER_AREA gray proxy at half size.

    Black canvas; static logo (red disc + S), channel name, 3-colour title above the box, grey
    watermark below; rounded box with a translating texture and a RAW-like burned-in frame counter
    (white, black outline, changes every frame); word-by-word white captions with a black outline in
    the lower third; optional fullscreen period (texture over the whole frame)."""
    hole = hole_mask(W, H, box)
    x, y, bw, bh, _r = box
    canvas = np.zeros((H, W, 3), np.uint8)
    cv2.circle(canvas, (110, 120), 46, (46, 38, 226), -1, cv2.LINE_AA)
    cv2.putText(canvas, "S", (92, 140), FONT, 1.6, (255, 255, 255), 4, cv2.LINE_AA)
    cv2.putText(canvas, "SynthRecaps", (180, 138), FONT, 1.4, (255, 255, 255), 3, cv2.LINE_AA)
    for (ty, col, text) in ((250, (255, 255, 255), "WAIT FOR THE"), (330, (31, 210, 255), "LAST SECOND"),
                            (400, (48, 48, 255), "NO WAY")):
        (tw, _), _ = cv2.getTextSize(text, FONT, 1.8, 5)
        put_outlined(canvas, text, ((W - tw) // 2, ty), 1.8, col, 2, 3)
    (tw, _), _ = cv2.getTextSize("synthrecaps dot tv", FONT, 1.0, 2)
    cv2.putText(canvas, "synthrecaps dot tv", ((W - tw) // 2, 1530), FONT, 1.0, (140, 140, 140), 2, cv2.LINE_AA)
    tex = texture(W, H, seed)
    frames, cap_masks, raw = [], [], []
    words = {k: wd for wd, a, b in captions for k in range(a, b)}
    for k in range(n):
        ox, oy = (5 * k) % 380, (3 * k) % 380
        content = cv2.cvtColor(tex[oy:oy + H, ox:ox + W], cv2.COLOR_GRAY2BGR)
        if counter:
            # a RAW-like burned-in number that changes every frame (all digits: none is static over the clip)
            put_outlined(content, f"{(k * 13721 + 24680) % 100000:05d}", (x + 300, y + 200), 2.5, (255, 255, 255), 2, 6)
        # the "RAW" frame shown in the box (identity geometry: RAW = W x H), as a half-size proxy
        raw.append(cv2.resize(cv2.cvtColor(content, cv2.COLOR_BGR2GRAY), (W // 2, H // 2),
                              interpolation=cv2.INTER_AREA))
        fs = fullscreen is not None and fullscreen[0] <= k < fullscreen[1]
        if fs:
            img = content.copy()
        else:
            img = canvas.copy()
            img[hole] = content[hole]
        cm = np.zeros((H, W), np.uint8)
        if k in words:
            org = caption_org(words[k], box)
            put_outlined(img, words[k], org, 2.4, (255, 255, 255), 2, 6)
            cm = text_mask((H, W), words[k], org, 2.4, 2)
        if flash is not None and k == flash:
            img[:] = 255
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        frames.append(cv2.resize(gray, (W // 2, H // 2), interpolation=cv2.INTER_AREA))
        cap_masks.append(cv2.resize(cm, (W // 2, H // 2), interpolation=cv2.INTER_AREA) > 128)
    counter_org = (x + 300, y + 200)
    return {"frames": np.stack(frames), "cap_masks": np.stack(cap_masks), "box": box, "counter_org": counter_org,
            "raw": np.stack(raw)}


def boxes_equal(b: Box, truth: tuple, tol: float = 1.0, rtol: float = 3.0) -> list[str]:
    x, y, w, h, r = truth
    bad = [f"{name} {got:.3f} vs {want}" for name, got, want in
           (("x", b.x, x), ("y", b.y, y), ("w", b.w, w), ("h", b.h, h)) if abs(got - want) > tol]
    if abs(b.corner_radius - r) > rtol:
        bad.append(f"r {b.corner_radius:.3f} vs {r}")
    return bad


def analyze(proxy: Proxy, tmp: Path, cfg: Config | None = None, cache: bool = False):
    cfg = cfg or Config(work_dir=str(tmp / "work"))
    dlog = DecisionLog(tmp / "decisions.jsonl")
    try:
        return L.analyze_layout(proxy, cfg, Cache(tmp / "work") if cache else None, tmp / "debug", dlog)
    finally:
        dlog.close()


# ----------------------------------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_scene(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("layout_main")
    sc = render_stack(flash=FLASH)
    proxy = make_proxy(sc["frames"], (FULL_W, FULL_H))
    lay, ov = analyze(proxy, tmp)
    return {"scene": sc, "proxy": proxy, "layout": lay, "overlays": ov, "tmp": tmp}


# ----------------------------------------------------------------------------------------------
# OverlayMasks / coverage / allowed mask / residual masks
# ----------------------------------------------------------------------------------------------

def test_overlay_masks_roundtrip(tmp_path):
    ov = L.OverlayMasks((40, 60), dilate_px=2)
    a = np.zeros((40, 60), bool)
    a[5:9, 10:30] = True
    b = np.zeros((40, 60), bool)
    b[20:22, 50:55] = True
    ov.set(3, a)
    ov.union(3, b)
    ov.set(7, b)
    ov.set(9, np.zeros((40, 60), bool))              # empty -> not stored
    assert ov.frames() == [3, 7] and len(ov) == 2 and 3 in ov and 9 not in ov
    assert np.array_equal(ov.get(3), a | b) and np.array_equal(ov.get(7), b) and ov.get(1) is None
    assert ov.bbox(7) == (50, 20, 5, 2) and ov.area(3) == int((a | b).sum())
    d = ov.get_dilated(7, 2)
    assert d[18:24, 48:57].sum() > b.sum() and not d[:15].any()
    with pytest.raises(ValueError):
        ov.set(1, np.zeros((10, 10), bool))
    p1, p2 = tmp_path / "a.npz", tmp_path / "b.npz"
    ov.save(p1)
    back = L.OverlayMasks.load(p1)
    assert back.shape == (40, 60) and back.dilate_px == 2 and back.frames() == [3, 7]
    assert np.array_equal(back.get(3), a | b)
    back.save(p2)
    assert p1.read_bytes() == p2.read_bytes()        # byte-stable (fixed zip timestamps)
    empty = L.OverlayMasks()
    empty.save(tmp_path / "e.npz")
    assert L.OverlayMasks.load(tmp_path / "e.npz").frames() == []


def test_box_coverage_matches_geometry_semantics():
    # an integer box at the origin with ratio 1 == geometry.rounded_rect_mask
    lay = Layout(100, 80, box=Box(0, 0, 100, 80, 12))
    proxy = make_proxy(np.zeros((1, 80, 100), np.uint8), (100, 80))
    cov = L.box_coverage(lay, proxy)
    assert np.allclose(cov, rounded_rect_mask(100, 80, 12, ss=4))
    # fractional box at proxy scale: total coverage == analytic area (to the super-sampling error)
    lay2 = Layout(200, 160, box=Box(21, 13, 150, 120, 30))
    proxy2 = make_proxy(np.zeros((1, 80, 100), np.uint8), (200, 160))
    cov2 = L.box_coverage(lay2, proxy2)
    area = (75 * 60 - (4 - np.pi) * 15 ** 2)
    assert abs(float(cov2.sum()) - area) < 0.01 * area
    assert cov2[40, 50] == 1.0 and cov2[0, 0] == 0.0 and 0 < cov2[30, 10] < 1   # x = 10.5 edge column
    assert np.all(L.box_coverage(Layout(200, 160), proxy2) == 1.0)


def test_allowed_mask(tmp_path):
    lay = Layout(100, 80, box=Box(10, 10, 80, 60, 5))
    static = np.zeros((80, 100), bool)
    static[15:18, 20:30] = True
    np.save(tmp_path / "static.npy", static)
    lay.static_mask_file = str(tmp_path / "static.npy")
    proxy = make_proxy(np.zeros((1, 80, 100), np.uint8), (100, 80))
    ov = L.OverlayMasks((80, 100), dilate_px=2)
    m = np.zeros((80, 100), bool)
    m[40, 50] = True
    ov.set(4, m)
    a0 = L.allowed_mask(lay, ov, 0, proxy)
    cov = L.box_coverage(lay, proxy) >= 0.99
    assert np.array_equal(a0, cov & ~static)
    a4 = L.allowed_mask(lay, ov, 4, proxy)
    disc = int(cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)).sum())
    assert not a4[40, 50] and not a4[42, 50] and a4[43, 50] and (a0 & ~a4).sum() == disc   # disc radius 2
    assert (a0 & ~L.allowed_mask(lay, ov, 4, proxy, dilate_px=0)).sum() == 1

    class Plain:                                    # any object with get(k)
        def get(self, k):
            return m if k == 4 else None
    assert np.array_equal(L.allowed_mask(lay, Plain(), 4, proxy, dilate_px=2), a4)
    assert np.array_equal(L.allowed_mask(lay, None, 4, proxy), a0)
    a0[0, 0] = True                                  # callers may modify the returned array
    assert not L.allowed_mask(lay, None, 4, proxy)[0, 0]


def test_masks_from_residuals():
    cfg = Config()
    base = np.zeros((60, 80), bool)
    base[5:55, 5:75] = True
    res = {}
    for k in range(10, 16):
        r = np.zeros((60, 80), np.uint8)
        r[20:26, 30:40] = 90                         # a persistent overlay
        if k == 12:
            r[45, 60] = 200                          # one-frame speck
        res[k] = r
    for k in (30, 31, 32):                           # a mismatch covering everything: not an overlay
        res[k] = np.full((60, 80), 255, np.uint8)
    out = L.masks_from_residuals(res, base, cfg)
    assert set(out) == set(range(10, 16))
    for k in range(10, 16):
        m = out[k]
        assert m[20:26, 30:40].all() and m[18, 35] and not m[45, 60] and not (m & ~base).any()
    assert L.masks_from_residuals({}, base, cfg) == {}


# ----------------------------------------------------------------------------------------------
# the competitor-like scene (numpy-rendered at full res, INTER_AREA proxy)
# ----------------------------------------------------------------------------------------------

def test_box_and_radius(main_scene):
    lay = main_scene["layout"]
    assert lay.mode == "boxed" and lay.box is not None
    assert not boxes_equal(lay.box, TRUTH_BOX), boxes_equal(lay.box, TRUTH_BOX)
    assert (lay.comp_w, lay.comp_h) == (FULL_W, FULL_H) and lay.proxy_ratio == (0.5, 0.5)


def test_periods_fullscreen(main_scene):
    lay = main_scene["layout"]
    per = [(p.comp_in, p.comp_out, p.mode) for p in lay.periods]
    assert per == [(0, FULLSCREEN[0], "boxed"), (FULLSCREEN[0], FULLSCREEN[1], "fullscreen"),
                   (FULLSCREEN[1], N_FRAMES, "boxed")], per
    assert lay.periods[1].box.to_dict() == Box(0, 0, FULL_W, FULL_H, 0).to_dict()
    assert any("fullscreen" in n for n in lay.notes)
    # the white flash frame stays in the boxed period (a transition, not a layout) but is noted and kept
    # out of the static statistics (else the whole canvas would look dynamic)
    assert any(f"frames {FLASH}-{FLASH}: whole canvas uniform" in n for n in lay.notes), lay.notes


def test_background_and_static_mask(main_scene):
    lay = main_scene["layout"]
    assert lay.background["type"] == "solid" and lay.canvas_bg == "#000000"
    st = np.load(lay.static_mask_file)
    assert st.shape == (960, 540) and st.dtype == bool
    assert st[20, 20] and st[900, 20]                # black canvas is static (fullscreen frames excluded)
    inner = st[240:720, 40:500].copy()               # inside the box only a few always-black outline pixels
    cx, cy = main_scene["scene"]["counter_org"]      # of the burned-in number are static
    inner[(cy - 70) // 2 - 240:(cy + 20) // 2 - 240, cx // 2 - 45:(cx + 260) // 2 - 40] = False
    assert not inner.any() and st[240:720, 40:500].mean() < 0.002


def test_static_zones(main_scene):
    lay = main_scene["layout"]
    types = [z.type for z in lay.zones]
    for t in ("logo", "channel_name", "title", "watermark", "captions"):
        assert t in types, types
    z = {t: next(z for z in lay.zones if z.type == t) for t in ("logo", "title", "watermark", "channel_name")}
    lg = z["logo"]
    assert abs(lg.x + lg.w / 2 - 110) <= 4 and abs(lg.y + lg.h / 2 - 120) <= 4 and 84 <= lg.w <= 100
    ti = z["title"]
    assert ti.y + ti.h <= TRUTH_BOX[1] and ti.y < 220 and ti.y + ti.h > 390 and ti.static
    wm = z["watermark"]
    assert wm.y >= TRUTH_BOX[1] + TRUTH_BOX[3] and wm.y < 1540
    assert z["channel_name"].x > lg.x + lg.w
    assert all(zz.type != "watermark" or zz.y > 1400 for zz in lay.zones)   # nothing flagged inside the box


def test_caption_timing_and_masks(main_scene):
    lay, ov, sc = main_scene["layout"], main_scene["overlays"], main_scene["scene"]
    caps = sorted((c["comp_in"], c["comp_out"]) for c in lay.captions if c["type"] == "captions")
    assert caps == [(a, b) for _w, a, b in CAPTIONS], caps
    for c in lay.captions:                           # full-res bbox inside the lower third of the box
        assert TRUTH_BOX[1] + 0.6 * TRUTH_BOX[3] < c["y"] < TRUTH_BOX[1] + TRUTH_BOX[3]
    band = next(z for z in lay.zones if z.type == "captions")
    assert band.comp_in == CAPTIONS[0][1] and band.comp_out == CAPTIONS[-1][2] and not band.static
    # captions end up in the overlay masks ...
    for _w, a, b in CAPTIONS:
        for k in range(a, b):
            m = ov.get(k)
            want = sc["cap_masks"][k]
            assert m is not None and (m & want).sum() >= 0.97 * want.sum(), k
    # ... while the moving texture and the burned-in counter do not
    cap_frames = {k for _w, a, b in CAPTIONS for k in range(a, b)}
    assert set(ov.frames()) <= cap_frames
    cx, cy = sc["counter_org"]
    for k in ov.frames():
        m = ov.get(k)
        assert not m[(cy - 90) // 2:(cy + 10) // 2, cx // 2:(cx + 250) // 2].any()     # counter area
        ys, _xs = np.nonzero(m)
        assert ys.min() * 2 >= TRUTH_BOX[1] + 0.6 * TRUTH_BOX[3]
    assert not any(c["type"] == "text" for c in lay.captions)


def test_allowed_mask_on_scene(main_scene):
    lay, ov, proxy = main_scene["layout"], main_scene["overlays"], main_scene["proxy"]
    k = 45
    a = L.allowed_mask(lay, ov, k, proxy)
    assert a.shape == (960, 540)
    assert not (a & main_scene["scene"]["cap_masks"][k]).any()     # caption pixels excluded
    assert a[400, 270] and not a[100, 270] and not a[235, 35]      # box interior / title / outside the corner
    assert a.sum() > 0.8 * 480 * 500


def test_debug_png_and_decisions(main_scene):
    tmp = main_scene["tmp"]
    png = tmp / "debug" / "layout.png"
    img = cv2.imread(str(png))
    assert img is not None and img.shape[0] >= 960 and img.shape[1] >= 1000
    dec = [json.loads(line) for line in (tmp / "decisions.jsonl").read_text().splitlines()]
    kinds = {d["decision"] for d in dec if d["stage"] == "layout"}
    assert {"statistics", "box", "box_full_res", "background", "zones", "text_overlays", "summary"} <= kinds
    box = next(d for d in dec if d["decision"] == "box")
    assert "edges" in box["evidence"] and "radius" in box["evidence"]


def test_subpixel_edges_odd_geometry(tmp_path):
    """Box edges that fall inside proxy pixels (odd full-res coordinates): sub-pixel estimation."""
    box = (61, 457, 957, 1003, 33)
    sc = render_stack(n=24, box=box, captions=[], fullscreen=None, counter=False, seed=3)
    lay, _ov = analyze(make_proxy(sc["frames"], (FULL_W, FULL_H)), tmp_path)
    assert not boxes_equal(lay.box, box), boxes_equal(lay.box, box)


def test_cache_hit_and_determinism(tmp_path):
    sc = render_stack(n=40, captions=CAPTIONS[:3], fullscreen=None, seed=5)
    proxy = make_proxy(sc["frames"], (FULL_W, FULL_H))
    lay1, ov1 = analyze(proxy, tmp_path / "a", cache=True)
    lay2, ov2 = analyze(proxy, tmp_path / "a", cache=True)          # cache hit
    dec = (tmp_path / "a" / "decisions.jsonl").read_text()
    assert '"cache_hit"' in dec and (tmp_path / "a" / "debug" / "layout.png").exists()
    lay3, ov3 = analyze(proxy, tmp_path / "b", cache=True)          # fresh computation elsewhere

    def strip(d):
        d = dict(d)
        d.pop("static_mask_file")
        d.pop("overlay_mask_file")
        return d
    assert strip(lay1.to_dict()) == strip(lay2.to_dict()) == strip(lay3.to_dict())
    assert Path(lay1.static_mask_file).is_absolute()
    assert Path(lay1.overlay_mask_file).read_bytes() == Path(lay3.overlay_mask_file).read_bytes()
    assert np.load(lay1.static_mask_file).tobytes() == np.load(lay3.static_mask_file).tobytes()
    assert ov1.frames() == ov2.frames() == ov3.frames() and len(ov1) > 0


# ----------------------------------------------------------------------------------------------
# other layouts (rendered directly at proxy scale, ratio 0.5)
# ----------------------------------------------------------------------------------------------

def _moving(h: int, w: int, n: int, seed: int) -> np.ndarray:
    tex = texture(w, h, seed, pad=200)
    return np.stack([tex[(2 * k) % 200:(2 * k) % 200 + h, (3 * k) % 200:(3 * k) % 200 + w] for k in range(n)])


def test_blur_background(tmp_path):
    h, w, n = 480, 270, 36
    x0, y0, bw, bh, r = 15, 115, 240, 250, 10
    content = _moving(bh, bw, n, 11)
    cov = L.rounded_box_coverage((w, h), x0, y0, x0 + bw, y0 + bh, r)
    frames = []
    for k in range(n):
        full = np.zeros((h, w), np.float32)
        full[y0:y0 + bh, x0:x0 + bw] = content[k]
        bg = 0.6 * L._cover_blur(full, (x0, y0, bw, bh), 6.0)
        f = cov * full + (1 - cov) * bg
        frames.append(np.clip(np.round(f), 0, 255).astype(np.uint8))
    lay, _ov = analyze(make_proxy(np.stack(frames), (2 * w, 2 * h)), tmp_path)
    assert lay.mode == "boxed"
    assert lay.background["type"] == "blur", lay.background
    assert 8.0 <= lay.background["sigma"] <= 18.0 and abs(lay.background["gain"] - 0.6) < 0.15
    assert not boxes_equal(lay.box, (2 * x0, 2 * y0, 2 * bw, 2 * bh, 2 * r), tol=2.0, rtol=8.0), lay.box


def test_fullscreen_video(tmp_path):
    frames = _moving(480, 270, 30, 12)
    lay, ov = analyze(make_proxy(frames, (540, 960)), tmp_path)
    assert lay.mode == "fullscreen"
    assert lay.box.to_dict() == Box(0, 0, 540, 960, 0).to_dict()
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == [(0, 30, "fullscreen")]
    assert len(ov) == 0 and not lay.captions


def test_split_screen_extra_region_and_stroke(tmp_path):
    h, w, n = 480, 270, 30
    a = _moving(200, 240, n, 21)
    b = _moving(150, 240, n, 22)
    frames = np.zeros((n, h, w), np.uint8)
    frames[:, 40:240, 15:255] = a
    frames[:, 300:450, 15:255] = b
    # 2-px white stroke around the main box
    frames[:, 38:40, 13:257] = 255
    frames[:, 240:242, 13:257] = 255
    frames[:, 38:242, 13:15] = 255
    frames[:, 38:242, 255:257] = 255
    lay, _ov = analyze(make_proxy(frames, (2 * w, 2 * h)), tmp_path)
    assert not boxes_equal(lay.box, (30, 80, 480, 400, 0), tol=1.0, rtol=2.0), lay.box
    assert len(lay.extra_regions) == 1
    er = lay.extra_regions[0]
    assert abs(er.x - 30) <= 2 and abs(er.y - 600) <= 2 and abs(er.w - 480) <= 4 and abs(er.h - 300) <= 4
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == [(0, n, "split")]
    assert "box_stroke" in lay.background and abs(lay.background["box_stroke"]["width"] - 4) <= 2


def test_gradient_background(tmp_path):
    h, w, n = 480, 270, 30
    grad = np.linspace(30, 160, h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    frames = np.repeat(np.round(grad).astype(np.uint8)[None], n, axis=0)
    frames[:, 120:360, 20:250] = _moving(240, 230, n, 31)
    lay, _ov = analyze(make_proxy(frames, (2 * w, 2 * h)), tmp_path)
    assert lay.background["type"] == "gradient", lay.background
    assert not boxes_equal(lay.box, (40, 240, 460, 480, 0), tol=1.0, rtol=2.0), lay.box


def test_image_background(tmp_path):
    rng = np.random.default_rng(1)
    h, w, n = 480, 270, 30
    pic = cv2.GaussianBlur(rng.integers(0, 255, (h, w)).astype(np.float32), (0, 0), 4)
    pic = ((pic - pic.min()) / np.ptp(pic) * 200 + 20).astype(np.uint8)
    frames = np.repeat(pic[None], n, axis=0)
    frames[:, 120:360, 20:250] = _moving(240, 230, n, 32)
    lay, _ov = analyze(make_proxy(frames, (2 * w, 2 * h)), tmp_path)
    assert lay.background["type"] == "image", lay.background
    assert lay.zones == [] and any("static image background" in nt for nt in lay.notes)
    assert not boxes_equal(lay.box, (40, 240, 460, 480, 0), tol=1.0, rtol=2.0), lay.box


def test_process_pool_matches_sequential(tmp_path, monkeypatch):
    """>= 256 frames with an .npy-backed proxy use the spawn process pool; results must be identical to
    the sequential detector."""
    import multiprocessing
    contexts: list[str] = []
    real_get_context = multiprocessing.get_context

    def spy(method=None):
        contexts.append(method)
        return real_get_context(method)
    monkeypatch.setattr(multiprocessing, "get_context", spy)
    h, w, n = 320, 180, 260
    frames = np.zeros((n, h, w), np.uint8)
    frames[:, 60:260, 10:170] = _moving(200, 160, n, 41)
    plan = [("HELLO", 10, 40), ("WORLD", 40, 75), ("AGAIN", 120, 160), ("OK", 200, 230)]
    for word, a, b in plan:
        (tw, _), _ = cv2.getTextSize(word, FONT, 0.9, 2)
        for k in range(a, b):
            img = cv2.cvtColor(frames[k], cv2.COLOR_GRAY2BGR)
            put_outlined(img, word, ((w - tw) // 2, 215), 0.9, (255, 255, 255), 2, 2)
            frames[k] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    npy = tmp_path / "proxy.npy"
    np.save(npy, frames)
    mm = np.load(npy, mmap_mode="r")
    proxy = make_proxy(mm, (2 * w, 2 * h), npy_path=str(npy))
    lay_seq, ov_seq = analyze(proxy, tmp_path / "seq", Config(workers=1))
    assert contexts == []
    lay_par, ov_par = analyze(proxy, tmp_path / "par", Config(workers=2))
    assert "spawn" in contexts                                        # the process pool was used
    assert lay_seq.captions == lay_par.captions
    assert sorted((c["comp_in"], c["comp_out"]) for c in lay_seq.captions) == [(a, b) for _w, a, b in plan]
    assert ov_seq.frames() == ov_par.frames()
    assert all(np.array_equal(ov_seq.get(k), ov_par.get(k)) for k in ov_seq.frames())


@dataclass
class _HangInWorker(L._Detector):
    """Text-line detector that blocks forever on frame ``hang_k`` when it runs in a pool worker (never in
    the parent): a stuck spawn worker for the layout pool's watchdog (module-level: picklable)."""
    parent: int = 0
    hang_k: int = -1

    def __call__(self, k, f):
        if k == self.hang_k and os.getpid() != self.parent:
            time.sleep(3600)
        return super().__call__(k, f)


def test_process_pool_watchdog_finishes_a_stuck_pool_here(tmp_path, monkeypatch, caplog):
    """Hang protection (DESIGN D7): a stuck text-line worker stops the spawn pool after the stall timeout and
    the remaining chunks are detected in this process -- the same lines as the sequential detector."""
    import logging
    from match_cuts import common
    monkeypatch.setitem(common.POOL_WATCHDOG, "stall_s", 6.0)
    monkeypatch.setitem(common.POOL_WATCHDOG, "poll_s", 0.1)
    h, w, n = 160, 90, 260
    frames = np.zeros((n, h, w), np.uint8)
    frames[:, 30:130, 5:85] = _moving(100, 80, n, 7)
    for k in range(40, 90):
        img = cv2.cvtColor(frames[k], cv2.COLOR_GRAY2BGR)
        put_outlined(img, "HI", (25, 110), 0.7, (255, 255, 255), 2, 2)
        frames[k] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    npy = tmp_path / "proxy.npy"
    np.save(npy, frames)
    proxy = make_proxy(np.load(npy, mmap_mode="r"), (2 * w, 2 * h), npy_path=str(npy))
    det = L._Detector(np.zeros((h, w), bool), (0, 0, w, h), None, 6, 60, w, True, L._text_params(Config()))
    hang = _HangInWorker(**{f: getattr(det, f) for f in det.__dataclass_fields__}, parent=os.getpid(), hang_k=70)
    seq = [r for _kl, _fr, res in L._detections(proxy, n, det, 1) for r in res]
    assert sum(1 for r in seq if r) >= 40                          # the caption frames have lines
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        got = [(kl, res) for kl, _fr, res in L._detections(proxy, n, hang, 2)]
    assert time.monotonic() - t0 < 120
    assert [k for kl, _res in got for k in kl] == list(range(n))
    par = [r for _kl, res in got for r in res]
    assert [[(x.x0, x.y0, x.x1, x.y1) for x in r] for r in par] == [[(x.x0, x.y0, x.x1, x.y1) for x in r] for r in seq]
    assert "stopped the worker pool" in caplog.text and "no result for" in caplog.text


# ----------------------------------------------------------------------------------------------
# real decode path: lavfi-made H.264 competitor -> VideoReader -> INTER_AREA proxy
# ----------------------------------------------------------------------------------------------

LAVFI_BOX = (30, 230, 480, 500, 20)
LAVFI_CAPS = [("THIS", 6, 18), ("IS", 18, 30), ("THE", 30, 41), ("MOMENT", 47, 60), ("NOBODY", 60, 72)]


def _drawtext(text, x, y, fs, col, border=0, font=TTF, enable=None):
    s = f"drawtext=fontfile={font}:text='{text}':x={x}:y={y}:fontsize={fs}:fontcolor={col}"
    if border:
        s += f":borderw={border}:bordercolor=black"
    if enable:
        s += f":enable='{enable}'"
    return s


def make_lavfi_competitor(out: Path, n: int = 84, W: int = 540, H: int = 960, box=LAVFI_BOX, caps=LAVFI_CAPS) -> None:
    bx, by, bw, bh, r = box
    cx, cy = bx + bw / 2, by + bh / 2
    inbox = (f"between(X,{bx},{bx + bw - 1})*between(Y,{by},{by + bh - 1})*"
             f"lte(hypot(max(abs(X+0.5-{cx})-{bw / 2 - r},0),max(abs(Y+0.5-{cy})-{bh / 2 - r},0)),{r})")
    logo = "lte(hypot(X+0.5-55,Y+0.5-60),23)"
    parts = [f"color=c=black:s={W}x{H}:r=30,format=rgba,geq=r='if({logo},226,0)':g='if({logo},38,0)':"
             f"b='if({logo},46,0)':a='if({inbox},0,255)'",
             _drawtext("S", "55-tw/2", "60-th/2", 29, "white"), _drawtext("SynthRecaps", 90, 49, 22, "white"),
             _drawtext("WAIT FOR THE", "(w-tw)/2", 103, 30, "white", border=2),
             _drawtext("LAST SECOND", "(w-tw)/2", 138, 30, "#ffd21f", border=2),
             _drawtext("NO WAY", "(w-tw)/2", 175, 22, "#ff3030", border=2),
             _drawtext("synthrecaps dot tv", "(w-tw)/2", 746, 17, "#8c8c8c")]
    png = out.with_suffix(".frame.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", ",".join(parts), "-frames:v", "1",
                    "-pix_fmt", "rgba", str(png)], check=True)
    content = (f"testsrc2=s={bw}x{bh}:r=30[cb];"
               f"life=s={bw // 6}x{bh // 6}:r=30:seed=7:ratio=0.3:life_color=white:death_color=black,"
               f"scale={bw}:{bh}:flags=neighbor,format=yuv420p[cl];[cb][cl]blend=all_mode=normal:all_opacity=0.35,"
               + _drawtext("%{eif\\:n+1000\\:d\\:5}", "(w-tw)/2", 60, 80, "white", border=4, font=MONO) + "[c]")
    capf = [_drawtext(t, "(w-tw)/2", 582, 37, "white", border=3,
                      enable=f"between(t,{(a - 0.5) / 30:.6f},{(b - 0.5) / 30:.6f})") for t, a, b in caps]
    graph = (content + f";color=c=black:s={W}x{H}:r=30[bg];[bg][c]overlay={bx}:{by}:shortest=1[v1];"
             f"[v1][0:v]overlay=0:0:shortest=1," + ",".join(capf) + f",trim=end_frame={n},format=yuv420p[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(png),
                    "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-threads", "2", str(out)], check=True)


def _hex_colours(notes: str) -> list[tuple[int, int, int]]:
    import re
    return [(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) for h in re.findall(r"#([0-9a-f]{6})", notes)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_real_mp4_proxy(tmp_path):
    from match_cuts.media import VideoReader
    src = tmp_path / "competitor.mp4"
    make_lavfi_competitor(src)
    with VideoReader(src) as vr:
        frames = np.stack([cv2.resize(img, (270, 480), interpolation=cv2.INTER_AREA)
                           for _k, img in vr.frames(0, None, fmt="gray")])
    assert frames.shape == (84, 480, 270)
    npy = tmp_path / "comp.npy"
    np.save(npy, frames)
    proxy = make_proxy(np.load(npy, mmap_mode="r"), (540, 960), path=str(src), npy_path=str(npy))
    lay, ov = analyze(proxy, tmp_path, cache=True)
    assert lay.mode == "boxed"
    assert not boxes_equal(lay.box, LAVFI_BOX), boxes_equal(lay.box, LAVFI_BOX)
    assert lay.background == {"type": "solid", "color": "#000000", "gray": lay.background["gray"]}
    assert lay.canvas_bg == "#000000"
    types = {z.type for z in lay.zones}
    assert {"logo", "channel_name", "title", "watermark", "captions"} <= types, types
    # colours are sampled from the decoded colour video: a white / yellow / red title, a red logo
    title = next(z for z in lay.zones if z.type == "title")
    cols = _hex_colours(title.notes)
    assert "multicolour" in title.notes
    assert any(min(c) > 220 for c in cols)                                  # white
    assert any(c[0] > 220 and c[1] > 180 and c[2] < 100 for c in cols)     # yellow #ffd21f
    assert any(c[0] > 220 and c[1] < 90 and c[2] < 90 for c in cols)       # red #ff3030
    logo = next(z for z in lay.zones if z.type == "logo")
    r, g, b = _hex_colours(logo.notes)[0]
    assert r > 180 and g < 90 and b < 90                                    # red disc (226, 38, 46)
    caps = sorted((c["comp_in"], c["comp_out"]) for c in lay.captions if c["type"] == "captions")
    assert caps == [(a, b) for _t, a, b in LAVFI_CAPS], caps
    assert not [c for c in lay.captions if c["type"] != "captions"]      # the burned-in counter is not text
    assert set(ov.frames()) == {k for _t, a, b in LAVFI_CAPS for k in range(a, b)}
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == [(0, 84, "boxed")]
    assert (tmp_path / "debug" / "layout.png").exists()
    # the cached result is reused
    lay2, ov2 = analyze(proxy, tmp_path, cache=True)
    assert lay2.to_dict() == lay.to_dict() and ov2.frames() == ov.frames()


# ----------------------------------------------------------------------------------------------
# DESIGN §7 D2 (review real-world:F3): static content inside the video box — a locked-off single-camera
# shot (talking head / podcast) or RAW letterbox bars — and the box re-measured against RAW
# ----------------------------------------------------------------------------------------------

RAW_W, RAW_H = 1920, 1080


def identity_frame_map(n: int, sim=None, status: np.ndarray | None = None):
    from match_cuts.geometry import Sim
    from match_cuts.model import FrameMap, Status
    fm = FrameMap(n)
    fm.status = np.full(n, Status.MATCH, np.int8) if status is None else status
    fm.raw = np.arange(n)
    fm.score = np.full(n, 0.99)
    fm.conf = np.full(n, 0.99)
    for k in range(n):
        fm.set_sim(k, sim if sim is not None else Sim.identity())
    return fm


def raw_proxy(frames: np.ndarray, full_size: tuple[int, int]) -> Proxy:
    n, h, w = frames.shape
    return Proxy("raw", "", frames, full_size, (w / full_size[0], h / full_size[1]), Fraction(30),
                 np.arange(n) / 30.0, n)


def draw_canvas(level: int = 0) -> np.ndarray:
    canvas = np.full((FULL_H, FULL_W, 3), level, np.uint8)
    cv2.circle(canvas, (110, 120), 46, (46, 38, 226), -1, cv2.LINE_AA)
    cv2.putText(canvas, "MY TITLE HERE", (200, 300), FONT, 2.0, (255, 255, 255), 4, cv2.LINE_AA)
    cv2.putText(canvas, "watermark dot tv", (380, 1560), FONT, 1.0, (140, 140, 140), 2, cv2.LINE_AA)
    return canvas


def raw_backed_scene(kind: str, n: int = 24, box: tuple = TRUTH_BOX, canvas_level: int = 0, bar_level: int = 0,
                     seed: int = 0, noise: float = 1.0):
    """A single-camera edit rebuilt from a 1920x1080 RAW cover-scaled into the rounded box (RAW height = box
    height, wider than the box): 'talking_head' = static textured background + a moving 'head' ellipse;
    'letterbox' = a moving picture between static letterbox bars (12 % of the RAW height, level
    ``bar_level``). Canvas (``canvas_level``) with logo, title, watermark; captions on frames 6-15; Gaussian
    noise. Returns (competitor proxy, RAW proxy, FrameMap with the true Sim, Sim)."""
    from match_cuts.geometry import Sim, warp_raw_to_comp
    x, y, bw, bh, _r = box
    s = bh / RAW_H
    sim = Sim(s, 0.0, x + bw / 2 - s * RAW_W / 2, float(y))
    still = texture(RAW_W, RAW_H, seed + 1)[:RAW_H, :RAW_W]
    mov = texture(RAW_W, RAW_H, seed + 2)
    hole = hole_mask(FULL_W, FULL_H, box)
    canvas = draw_canvas(canvas_level)
    rng = np.random.default_rng(seed + 7)
    bar = int(round(0.12 * RAW_H))
    comp, raws = [], []
    for k in range(n):
        ox, oy = (5 * k) % 380, (3 * k) % 380
        moving = mov[oy:oy + RAW_H, ox:ox + RAW_W]
        if kind == "talking_head":
            f = still.copy()
            m = np.zeros((RAW_H, RAW_W), np.uint8)
            cv2.ellipse(m, (RAW_W // 2 + int(40 * np.sin(k / 5)), RAW_H // 2 + 60), (190, 300), 0, 0, 360, 255, -1)
            f[m > 0] = moving[m > 0]
        else:
            f = moving.copy()
            f[:bar] = bar_level
            f[RAW_H - bar:] = bar_level
        wr, _v = warp_raw_to_comp(f, sim, False, RAW_W, (FULL_W, FULL_H))
        img = canvas.copy()
        img[hole] = cv2.cvtColor(wr, cv2.COLOR_GRAY2BGR)[hole]
        if 6 <= k < 16:
            put_outlined(img, "WORD", caption_org("WORD", box), 2.4, (255, 255, 255), 2, 6)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) + rng.normal(0, noise, (FULL_H, FULL_W))
        comp.append(cv2.resize(np.clip(np.round(g), 0, 255).astype(np.uint8), (FULL_W // 2, FULL_H // 2),
                               interpolation=cv2.INTER_AREA))
        raws.append(cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA))
    return (make_proxy(np.stack(comp), (FULL_W, FULL_H)), raw_proxy(np.stack(raws), (RAW_W, RAW_H)),
            identity_frame_map(n, sim), sim)


def review_talking_head(n: int = 40, box: tuple = TRUTH_BOX, noise: float = 1.0, seed: int = 0) -> np.ndarray:
    """The review's scene (real-world:F3): black canvas + title; inside the rounded box a static textured
    background with a moving textured ellipse (the 'head') and a caption; Gaussian noise."""
    rng = np.random.default_rng(seed)
    hole = hole_mask(FULL_W, FULL_H, box)
    x, y, bw, bh, _r = box
    canvas = np.zeros((FULL_H, FULL_W, 3), np.uint8)
    cv2.putText(canvas, "MY TITLE HERE", (200, 300), FONT, 2.0, (255, 255, 255), 4, cv2.LINE_AA)
    tex, tex2 = texture(FULL_W, FULL_H, seed), texture(FULL_W, FULL_H, seed + 1)
    frames = []
    for k in range(n):
        content = cv2.cvtColor(tex2[:FULL_H, :FULL_W], cv2.COLOR_GRAY2BGR).copy()
        ox, oy = (3 * k) % 300, (2 * k) % 300
        mov = cv2.cvtColor(tex[oy:oy + FULL_H, ox:ox + FULL_W], cv2.COLOR_GRAY2BGR)
        m = np.zeros((FULL_H, FULL_W), np.uint8)
        cv2.ellipse(m, (FULL_W // 2 + int(20 * np.sin(k / 7)), y + bh // 2 + 60), (170, 260), 0, 0, 360, 255, -1)
        content[m > 0] = mov[m > 0]
        img = canvas.copy()
        img[hole] = content[hole]
        if 12 <= k < 28:
            put_outlined(img, "WORD", caption_org("WORD", box), 2.4, (255, 255, 255), 2, 6)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) + rng.normal(0, noise, (FULL_H, FULL_W))
        frames.append(cv2.resize(np.clip(g, 0, 255).astype(np.uint8), (FULL_W // 2, FULL_H // 2),
                                 interpolation=cv2.INTER_AREA))
    return np.stack(frames)


def effect_scene(box: tuple = TRUTH_BOX, n: int = 24, kind: str = "moving", zoom: float = 1.0, bars: int = 0,
                 bar_level: int = 0, canvas_level: int = 0, effect: tuple | None = None, seed: int = 0,
                 noise: float = 1.0):
    """Review real-world-new-paths:D2-shrink / D2-letterbox-growth scenes: a 1920x1080 RAW cover-scaled
    (x ``zoom``) into the rounded box and centred on it; ``kind`` 'moving' = translating texture,
    'talking_head' = static texture with a moving textured ellipse; ``bars`` letterbox rows (RAW px, top and
    bottom) of level ``bar_level``; ``effect`` = what the competitor does to the video inside the box:
    ('gradient', height, min_gain) the dark gradient behind captions at the bottom of the box, ('band',
    fraction, gain) a darkened lower band with a hard edge, ('feather', width, strength) an inner shadow /
    feathered edge. Canvas (``canvas_level``) with logo, title, watermark; captions on frames 6-15; Gaussian
    noise. Returns (competitor proxy, RAW proxy, FrameMap with the true Sim, Sim)."""
    from match_cuts.geometry import Sim, warp_raw_to_comp
    x, y, bw, bh, _r = box
    s = zoom * max(bh / RAW_H, bw / RAW_W)
    sim = Sim(s, 0.0, x + bw / 2 - s * RAW_W / 2, y + bh / 2 - s * RAW_H / 2)
    still = texture(RAW_W, RAW_H, seed + 1)[:RAW_H, :RAW_W]
    mov = texture(RAW_W, RAW_H, seed + 2)
    hole = hole_mask(FULL_W, FULL_H, box)
    canvas = cv2.cvtColor(draw_canvas(canvas_level), cv2.COLOR_BGR2GRAY).astype(np.float32)
    rng = np.random.default_rng(seed + 7)
    yy, xx = np.mgrid[0:FULL_H, 0:FULL_W].astype(np.float32)
    gain = np.ones((FULL_H, FULL_W), np.float32)
    if effect is not None and effect[0] == "gradient":
        gain = np.clip((y + bh - yy) / effect[1], 0, 1) * (1 - effect[2]) + effect[2]
    elif effect is not None and effect[0] == "band":
        gain[yy >= y + bh * (1 - effect[1])] = effect[2]
    elif effect is not None and effect[0] == "feather":
        d = np.minimum.reduce([xx + 0.5 - x, x + bw - xx - 0.5, yy + 0.5 - y, y + bh - yy - 0.5])
        gain = 1 - effect[2] * np.clip(1 - d / effect[1], 0, 1)
    comp, raws = [], []
    for k in range(n):
        ox, oy = (5 * k) % 380, (3 * k) % 380
        moving = mov[oy:oy + RAW_H, ox:ox + RAW_W]
        if kind == "talking_head":
            f = still.copy()
            m = np.zeros((RAW_H, RAW_W), np.uint8)
            cv2.ellipse(m, (RAW_W // 2 + int(40 * np.sin(k / 5)), RAW_H // 2 - 60), (190, 300), 0, 0, 360, 255, -1)
            f[m > 0] = moving[m > 0]
        else:
            f = moving.copy()
        if bars:
            f[:bars] = bar_level
            f[RAW_H - bars:] = bar_level
        wr, _v = warp_raw_to_comp(f, sim, False, RAW_W, (FULL_W, FULL_H))
        img = canvas.copy()
        img[hole] = (wr.astype(np.float32) * gain)[hole]
        img = cv2.cvtColor(np.clip(np.round(img), 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        if 6 <= k < 16:
            put_outlined(img, "WORD", caption_org("WORD", box), 2.4, (255, 255, 255), 2, 6)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) + rng.normal(0, noise, (FULL_H, FULL_W))
        comp.append(cv2.resize(np.clip(np.round(g), 0, 255).astype(np.uint8), (FULL_W // 2, FULL_H // 2),
                               interpolation=cv2.INTER_AREA))
        raws.append(cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA))
    return (make_proxy(np.stack(comp), (FULL_W, FULL_H)), raw_proxy(np.stack(raws), (RAW_W, RAW_H)),
            identity_frame_map(n, sim), sim)


def refine(lay, ov, cp, rp, fm, tmp: Path, cache: bool = False):
    dlog = DecisionLog(tmp / "refine_decisions.jsonl")
    try:
        return L.refine_box_from_raw(lay, ov, cp, rp, fm, Config(work_dir=str(tmp / "work")),
                                     Cache(tmp / "work") if cache else None, dlog, tmp / "debug")
    finally:
        dlog.close()


def decisions(path: Path, name: str) -> list[dict]:
    return [d for d in (json.loads(line) for line in path.read_text().splitlines()) if d["decision"] == name]


@pytest.mark.parametrize("noise", [1.0, 3.0])
def test_initial_detection_static_background_talking_head(tmp_path, noise):
    """review real-world:F3 repro: temporal activity alone gave the moving head's bbox + an 'image'
    background; a static TEXTURED picture enclosing the moving subject in a clean rounded rectangle on a
    uniform canvas is part of the video box."""
    lay, _ov = analyze(make_proxy(review_talking_head(noise=noise), (FULL_W, FULL_H)), tmp_path)
    assert not boxes_equal(lay.box, TRUTH_BOX), (lay.box, boxes_equal(lay.box, TRUTH_BOX))
    assert lay.background["type"] == "solid" and lay.background["gray"] < 3, lay.background   # (clipped noise)
    assert "title" in [z.type for z in lay.zones]
    # the static picture inside the box is video, not an overlay zone
    assert not [z for z in lay.zones if z.static and z.y >= TRUTH_BOX[1] and z.y + z.h <= TRUTH_BOX[1] + TRUTH_BOX[3]]


def test_initial_detection_ignores_drop_shadow(tmp_path):
    """The static-picture rule must not take a smooth drop shadow around the box (static, non-canvas, a
    clean rounded rectangle too — but without fine detail) for video."""
    hole = hole_mask(FULL_W, FULL_H, TRUTH_BOX)
    dist = cv2.distanceTransform((~hole).astype(np.uint8), cv2.DIST_L2, 5)
    base = np.clip(90.0 - np.clip(1 - dist / 40.0, 0, 1) * 70.0, 0, 255).astype(np.float32)
    tex = texture(FULL_W, FULL_H, 3)
    frames = []
    for k in range(24):
        ox, oy = (5 * k) % 380, (3 * k) % 380
        img = base.copy()
        img[hole] = tex[oy:oy + FULL_H, ox:ox + FULL_W][hole]
        frames.append(cv2.resize(np.clip(img, 0, 255).astype(np.uint8), (FULL_W // 2, FULL_H // 2),
                                 interpolation=cv2.INTER_AREA))
    lay, _ov = analyze(make_proxy(np.stack(frames), (FULL_W, FULL_H)), tmp_path)
    assert not boxes_equal(lay.box, TRUTH_BOX, tol=2.0, rtol=4.0), lay.box
    assert not decisions(tmp_path / "decisions.jsonl", "box_static_region")
    assert "box_shadow" in lay.background


def test_refine_box_from_raw_talking_head(tmp_path):
    """A box that covers only the moving subject (what the temporal analysis gave before) is re-measured
    against RAW: grown to the RAW-matching region (static background included) — exact to ±1 px, radius
    ±3 px — and the layout is re-derived (background solid, title / logo / watermark zones, no in-box zone,
    notes, new initial overlay masks); cached re-runs are identical."""
    import dataclasses
    cp, rp, fm, _sim = raw_backed_scene("talking_head")
    lay0, ov0 = analyze(cp, tmp_path / "a")
    assert not boxes_equal(lay0.box, TRUTH_BOX)                       # (the initial detection handles it too)
    shrunk = Box(356.51, 754.0, 368.49, 530.0, 0.0)                    # the review's (pre-fix) detection
    wrong = dataclasses.replace(lay0, box=shrunk, background={"type": "image", "color": "#6e6e6e"},
                                periods=[dataclasses.replace(p, box=shrunk) for p in lay0.periods])
    lay, changed = refine(wrong, ov0, cp, rp, fm, tmp_path / "b", cache=True)
    assert changed is True and lay is not wrong
    assert not boxes_equal(lay.box, TRUTH_BOX), (lay.box, boxes_equal(lay.box, TRUTH_BOX))
    assert lay.background["type"] == "solid" and lay.canvas_bg == "#000000", lay.background
    types = [z.type for z in lay.zones]
    assert {"logo", "title", "watermark", "captions"} <= set(types), types
    assert not [z for z in lay.zones if z.static and z.y >= TRUTH_BOX[1] and z.y + z.h <= TRUTH_BOX[1] + TRUTH_BOX[3]]
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == [(0, 24, "boxed")]
    assert lay.periods[0].box.to_dict() == lay.box.to_dict()
    assert any("RAW" in nt and "grown" in nt for nt in lay.notes), lay.notes
    ov = L.OverlayMasks.load(lay.overlay_mask_file)                    # the caller reloads the initial masks
    assert set(ov.frames()) == set(range(6, 16))
    assert np.load(lay.static_mask_file).shape == (960, 540)
    assert (tmp_path / "b" / "debug" / "layout_refine.png").exists()
    dec = decisions(tmp_path / "b" / "refine_decisions.jsonl", "box_refined")
    assert dec and dec[0]["evidence"]["votes_contradicted_new"] < dec[0]["evidence"]["votes_contradicted_old"]
    # deterministic, and the re-analysis is cached
    lay2, changed2 = refine(wrong, ov0, cp, rp, fm, tmp_path / "b", cache=True)
    assert changed2 and lay2.to_dict() == lay.to_dict()
    assert decisions(tmp_path / "b" / "refine_decisions.jsonl", "cache_hit")


@pytest.mark.parametrize("canvas_level,bar_level", [(0, 14), (40, 0)])
def test_refine_box_from_raw_letterboxed_raw(tmp_path, canvas_level, bar_level):
    """RAW with its own letterbox bars inside the box: the bars are static, so temporal activity gives only
    the picture between them (h 760 instead of 1000). Where the bars differ from the canvas, they are IN
    evidence beyond the temporal edge: measured against RAW the box reaches the RAW frame edge and the corner
    radius is measured. (Bars that equal the canvas: see the next test.)"""
    cp, rp, fm, _sim = raw_backed_scene("letterbox", canvas_level=canvas_level, bar_level=bar_level)
    lay0, ov0 = analyze(cp, tmp_path / "a")
    assert abs(lay0.box.h - 760) < 6 and abs(lay0.box.y - 580) < 3, lay0.box      # the bug: bars left out
    lay, changed = refine(lay0, ov0, cp, rp, fm, tmp_path / "b")
    assert changed
    assert not boxes_equal(lay.box, TRUTH_BOX), (lay.box, boxes_equal(lay.box, TRUTH_BOX))
    assert lay.background["type"] == "solid"
    # the bars are video: no zone inside the box
    assert not [z for z in lay.zones if z.static and z.y >= TRUTH_BOX[1] - 2
                and z.y + z.h <= TRUTH_BOX[1] + TRUTH_BOX[3] + 2]
    sides = decisions(tmp_path / "b" / "refine_decisions.jsonl", "box_refined")[0]["evidence"]["sides"]
    assert sides["top"]["method"] == sides["bottom"]["method"] == "RAW frame edge", sides


def test_refine_box_from_raw_letterbox_unobservable_keeps_temporal(tmp_path):
    """review real-world-new-paths:D2-letterbox-growth: black RAW letterbox bars on a black canvas cannot be
    seen. Whether the competitor's box includes them (this scene) or crops them off
    (test_refine_keeps_box_letterbox_cropped_off) is not observable, and both render the same in match mode
    (and fill mode must not show the bars): without IN evidence beyond the temporal edge the box does NOT
    grow to the RAW frame edge — the temporal box (the picture, square corners) is kept."""
    cp, rp, fm, _sim = raw_backed_scene("letterbox", canvas_level=0, bar_level=0)
    lay0, ov0 = analyze(cp, tmp_path / "a")
    assert abs(lay0.box.h - 760) < 6 and abs(lay0.box.y - 580) < 3, lay0.box
    lay, changed = refine(lay0, ov0, cp, rp, fm, tmp_path / "b")
    assert changed is False and lay is lay0
    dec = decisions(tmp_path / "b" / "refine_decisions.jsonl", "box_refine")[0]
    measured = Box.from_dict(dec["evidence"]["measured_box"])
    assert not boxes_equal(measured, (lay0.box.x, lay0.box.y, lay0.box.w, lay0.box.h, lay0.box.corner_radius)), \
        measured
    assert dec["evidence"]["sides"]["top"]["stop_class"] == "no_raw"          # the scan crossed the bars


def _effect_refine(tmp_path, truth: tuple, **kw):
    """effect_scene -> temporal analysis (must find the truth box) -> refine_box_from_raw. Returns (temporal
    layout, refined layout, changed, the box_refine decision)."""
    cp, rp, fm, _sim = effect_scene(box=truth, **kw)
    lay0, ov0 = analyze(cp, tmp_path / "a")
    assert not boxes_equal(lay0.box, truth), (lay0.box, boxes_equal(lay0.box, truth))
    lay, changed = refine(lay0, ov0, cp, rp, fm, tmp_path / "b")
    dec = (decisions(tmp_path / "b" / "refine_decisions.jsonl", "box_refine")
           + decisions(tmp_path / "b" / "refine_decisions.jsonl", "box_refined"))
    return lay0, lay, changed, dec[0]


@pytest.mark.parametrize("kind,effect", [
    ("moving", ("gradient", 150.0, 0.6)),        # caption gradient (review: h 1000 -> 873, r 0, 'pip')
    ("moving", ("feather", 60.0, 0.3)),          # soft inner edge / inner shadow (review: all four sides in)
    ("talking_head", ("gradient", 250.0, 0.4)),  # the same gradient over a locked-off (static) background
    ("talking_head", ("band", 1 / 3, 0.5)),      # darkened lower third, hard edge, static background
], ids=["gradient", "feather", "static_gradient", "static_lower_third"])
def test_refine_keeps_box_under_competitor_effects(tmp_path, kind, effect):
    """review real-world-new-paths:D2-shrink: a competitor effect that darkens part of the video inside a
    CORRECT box (the gain / offset fit against RAW is global) must not shrink the box: OUT votes on video
    the temporal analysis saw move are don't-care unless they show the canvas, a side moves inward only over
    static / canvas-like lines that do not show RAW's local structure, and the gain-invariant structure
    comparison counts the darkened video as RAW. Box, radius and layout stay; no PiP / extra region."""
    lay0, lay, changed, dec = _effect_refine(tmp_path, TRUTH_BOX, kind=kind, effect=effect)
    assert changed is False and lay is lay0, dec
    measured = Box.from_dict(dec["evidence"]["measured_box"])
    assert not boxes_equal(measured, TRUTH_BOX), (measured, dec["reason"])
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == [(0, 24, "boxed")]
    assert not lay.extra_regions and not [nt for nt in lay.notes if "pip" in nt.lower()]
    assert dec["evidence"]["structure_votes"] > 0


def test_refine_grows_talking_head_box_through_caption_gradient(tmp_path):
    """The D2 growth case (a box that covers only the moving subject) with a caption gradient over the
    static background at the bottom of the box: the darkened static rows still show RAW's structure, so the
    box grows over them to the true edge instead of stopping where the gradient starts."""
    import dataclasses
    cp, rp, fm, _sim = effect_scene(kind="talking_head", effect=("gradient", 250.0, 0.4))
    lay0, ov0 = analyze(cp, tmp_path / "a")
    shrunk = Box(356.5, 754.0, 368.5, 530.0, 0.0)
    wrong = dataclasses.replace(lay0, box=shrunk, background={"type": "image", "color": "#6e6e6e"},
                                periods=[dataclasses.replace(p, box=shrunk) for p in lay0.periods])
    lay, changed = refine(wrong, ov0, cp, rp, fm, tmp_path / "b")
    assert changed is True
    assert not boxes_equal(lay.box, TRUTH_BOX), (lay.box, boxes_equal(lay.box, TRUTH_BOX))
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == [(0, 24, "boxed")]


_BARS_239 = int(round((RAW_H - RAW_W / 2.39) / 2))                      # 2.39:1 movie in a 16:9 RAW


@pytest.mark.parametrize("truth,kw", [
    # the prompt's target: square rounded box showing exactly the 2.39:1 picture, bars outside it
    ((60, 480, 960, 960, 40), {"bars": _BARS_239,
                               "zoom": (960 / (RAW_H - 2 * _BARS_239)) / max(960 / RAW_H, 960 / RAW_W)}),
    # a wide box, 150 px bars (RAW) cropped off above and below the picture
    ((60, 765, 960, 390, 40), {"bars": 150}),
], ids=["movie_239_square", "wide_box"])
def test_refine_keeps_box_letterbox_cropped_off(tmp_path, truth, kw):
    """review real-world-new-paths:D2-letterbox-growth: a letterboxed RAW whose bars the competitor cropped
    off (box = the picture, rounded, on a black canvas; title / watermark on the canvas). The bars equal the
    canvas: nothing beyond the box is observable, so the box must not grow to the RAW frame edge (was: y 480
    -> 315, h 960 -> 1230 with r40 kept inside the bars); the canvas next to the box is never sampled from
    the watermark / title (that made the black bars look 'discriminating'); partially covered boundary rows
    do not outvote the rounded corners. Box and radius unchanged."""
    lay0, lay, changed, dec = _effect_refine(tmp_path, truth, **kw)
    assert changed is False and lay is lay0, dec
    assert lay.box.corner_radius == lay0.box.corner_radius
    measured = Box.from_dict(dec["evidence"]["measured_box"])
    assert not boxes_equal(measured, truth), (measured, dec["reason"])
    assert "zone" in dec["evidence"]["background_samples"]


def test_refine_box_from_raw_keeps_correct_box(main_scene, tmp_path):
    """A correct box is only verified: same Layout object back, changed False; frames of the fullscreen
    period (and the flash frame) are never used for the RAW comparison."""
    from match_cuts.model import Status
    sc, lay, ov, proxy = main_scene["scene"], main_scene["layout"], main_scene["overlays"], main_scene["proxy"]
    status = np.full(N_FRAMES, Status.MATCH, np.int8)
    status[FLASH] = Status.UNIFORM
    fm = identity_frame_map(N_FRAMES, status=status)
    rp = raw_proxy(sc["raw"], (FULL_W, FULL_H))
    m = L.measure_box_from_raw(lay, ov, proxy, rp, fm, Config())
    assert m["ok"] and not boxes_equal(m["box"], TRUTH_BOX, tol=0.5, rtol=2.0), m["box"]
    assert not [k for k in m["frames"] if FULLSCREEN[0] <= k < FULLSCREEN[1] or k == FLASH], m["frames"]
    before = json.dumps(lay.to_dict(), sort_keys=True)
    lay2, changed = refine(lay, ov, proxy, rp, fm, tmp_path)
    assert changed is False and lay2 is lay and json.dumps(lay.to_dict(), sort_keys=True) == before
    dec = decisions(tmp_path / "refine_decisions.jsonl", "box_refine")
    assert dec and dec[0]["changed"] is False and "agrees" in dec[0]["reason"]


def test_refine_box_from_raw_needs_matches(main_scene, tmp_path):
    from match_cuts.model import Status
    lay, ov, proxy, sc = main_scene["layout"], main_scene["overlays"], main_scene["proxy"], main_scene["scene"]
    fm = identity_frame_map(N_FRAMES, status=np.full(N_FRAMES, Status.NONE, np.int8))
    lay2, changed = refine(lay, ov, proxy, raw_proxy(sc["raw"], (FULL_W, FULL_H)), fm, tmp_path)
    assert lay2 is lay and changed is False
    assert decisions(tmp_path / "refine_decisions.jsonl", "box_refine")[0]["reason"]
    fs_only = Layout(FULL_W, FULL_H, mode="fullscreen", box=Box(0, 0, FULL_W, FULL_H, 0))
    assert L.refine_box_from_raw(fs_only, None, proxy, None, fm, Config(), None, None, None) == (fs_only, False)


@pytest.mark.parametrize("pad", [12, 40])
def test_refine_box_from_raw_shrinks_oversized_box(main_scene, tmp_path, pad):
    """The shrink guards (D2-shrink) keep a genuine shrink working: a box too large over the static canvas
    (lines that are static, show the canvas where RAW would not and do not show RAW's structure) is
    re-measured to the true box."""
    import dataclasses
    from match_cuts.model import Status
    sc, lay, ov, proxy = main_scene["scene"], main_scene["layout"], main_scene["overlays"], main_scene["proxy"]
    status = np.full(N_FRAMES, Status.MATCH, np.int8)
    status[FLASH] = Status.UNIFORM
    fm = identity_frame_map(N_FRAMES, status=status)
    x, y, w, h, r = TRUTH_BOX
    big = Box(x - pad, y - pad, w + 2 * pad, h + 2 * pad, float(r))
    wrong = dataclasses.replace(lay, box=big, periods=[dataclasses.replace(p, box=big) if p.mode == "boxed" else p
                                                       for p in lay.periods])
    lay2, changed = refine(wrong, ov, proxy, raw_proxy(sc["raw"], (FULL_W, FULL_H)), fm, tmp_path)
    assert changed is True
    assert not boxes_equal(lay2.box, TRUTH_BOX), (lay2.box, boxes_equal(lay2.box, TRUTH_BOX))
    dec = decisions(tmp_path / "refine_decisions.jsonl", "box_refined")[0]
    assert "shrunk" in dec["reason"] and "veto" not in dec["evidence"]


def test_scan_side_shrink_guard_and_grow_margin():
    """_scan_side (canonical lines, inside = larger index): a side shrinks over OUT lines only while they are
    shrinkable; it grows only to an IN line at least ``min_grow`` lines outside the edge."""
    DC, IN, OUT = L._L_DC, L._L_IN, L._L_OUT
    cls = np.array([DC] * 10 + [OUT] * 4 + [IN] * 6, np.int8)
    assert L._scan_side(cls, 10, None)[:2] == (14, "shrink")
    assert L._scan_side(cls, 10, None, np.ones(20, bool))[:2] == (14, "shrink")
    no = np.ones(20, bool)
    no[10:14] = False                                        # dynamic video / RAW structure: never shrink over it
    assert L._scan_side(cls, 10, None, no)[:2] == (10, "keep")
    part = np.ones(20, bool)
    part[12] = False
    assert L._scan_side(cls, 10, None, part)[:2] == (12, "shrink")
    near = np.array([DC] * 8 + [IN] + [DC] + [IN] * 10, np.int8)   # IN 2 lines outside: a blurred boundary line
    assert L._scan_side(near, 10, None, None, 3)[:2] == (10, "keep")
    assert L._scan_side(near, 10, None, None, 1)[:2] == (8, "grow")
    far = np.array([DC] * 6 + [IN] * 2 + [DC] * 2 + [IN] * 10, np.int8)
    assert L._scan_side(far, 10, None, None, 3)[:2] == (6, "grow")


def test_structure_agreement_is_gain_invariant():
    """The competitor's local structure vs warped RAW: a darkened / offset copy agrees (a caption gradient,
    an inner shadow), a flat canvas, other content and a canvas row next to the box edge do not; untextured
    RAW gets no verdict."""
    def prox(seed: int) -> np.ndarray:                     # proxy-scale texture (INTER_AREA 1/2, blurred as compared)
        t = texture(800, 600, seed)[:600, :800]
        return cv2.GaussianBlur(cv2.resize(t, (400, 300), interpolation=cv2.INTER_AREA).astype(np.float32),
                                (0, 0), 1.0)
    args = (L.REFINE_ZNCC_WIN, L.REFINE_ZNCC_MIN, L.REFINE_ZNCC_STD, L.REFINE_ZNCC_GAIN_MIN, 8.0)
    R = prox(5)
    V = np.ones(R.shape, bool)
    ramp = np.linspace(1.0, 0.35, R.shape[0], dtype=np.float32)[:, None]
    noise = np.random.default_rng(0).normal(0, 1.0, R.shape).astype(np.float32)
    agree, tx = L._structure_agreement(R * ramp + 12.0 + noise, R, V, *args)
    assert tx.mean() > 0.9 and agree[tx].mean() > 0.99
    agree, _ = L._structure_agreement(noise, R, V, *args)
    assert not agree.any()
    for seed in (9, 11, 13):
        agree, _ = L._structure_agreement(prox(seed), R, V, *args)
        assert agree[tx].mean() < 0.005, seed
    boxed = R.copy()
    boxed[:100] = 0.0                                         # canvas above a box edge at row 100
    agree, _ = L._structure_agreement(boxed, R, V, *args)
    # (a canvas pixel right next to the edge may match the local fit by chance: isolated, never a line)
    assert not agree[:97].any() and agree[97:100].mean() < 0.02 and agree[106:].mean() > 0.99
    V2 = V.copy()
    V2[250:] = False                                          # RAW ends at row 250: windows use RAW pixels only
    agree, _ = L._structure_agreement(R * ramp + 12.0, R, V2, *args)
    assert agree[240:250].mean() > 0.99 and not agree[250:].any()
    agree, tx = L._structure_agreement(np.full_like(R, 50.0), np.full_like(R, 80.0), V, *args)
    assert not tx.any() and not agree.any()


def test_refine_veto_ring_and_corners():
    """_refine_veto: a measured box that drops protected video (the ring between the boxes) or covers the
    rounded corners the competitor shows at the temporal box (canvas where RAW is present) is rejected."""
    cp = make_proxy(np.zeros((1, FULL_H // 2, FULL_W // 2), np.uint8), (FULL_W, FULL_H))
    h, w = FULL_H // 2, FULL_W // 2
    old = Box(*TRUTH_BOX)
    lay_old = Layout(FULL_W, FULL_H, box=old)
    zero = np.zeros((h, w), np.int16)
    band = np.zeros((h, w), bool)
    smaller = Box(60, 460, 960, 800, 40)
    prot = L.box_coverage(lay_old, cp) > 0
    v = L._refine_veto(old, smaller, cp, {"protected": prot, "maps": (zero, zero, zero)}, band, Config())
    assert v is not None and "drops video" in v["reason"] and v["dynamic_px"] > 1000
    assert L._refine_veto(old, smaller, cp, {"protected": np.zeros((h, w), bool), "maps": (zero, zero, zero)},
                          band, Config()) is None
    # rounded corners of the temporal box shown by the competitor (OUT votes where RAW is present)
    rect = L.box_coverage(Layout(FULL_W, FULL_H, box=Box(60, 460, 960, 1000, 0)), cp) >= 1.0
    ears = rect & (L.box_coverage(lay_old, cp) <= 0)
    nO = np.where(ears, 16, 0).astype(np.int16)
    nV = np.where(rect, 16, 0).astype(np.int16)
    grown = Box(60, 400, 960, 1120, 40)
    v = L._refine_veto(old, grown, cp, {"protected": None, "maps": (zero, nO, nV)}, band, Config())
    assert v is not None and "rounded corners" in v["reason"]
    nI = np.where(ears, 16, 0).astype(np.int16)                # the corners show RAW: the growth is plausible
    assert L._refine_veto(old, grown, cp, {"protected": None, "maps": (nI, zero, nV)}, band, Config()) is None


def test_background_sources_never_sample_zones():
    """The canvas next to the box (B) is never sampled from a zone (title / watermark / logo): for a solid
    background only static non-zone pixels with the model level, for a moving (blur) background only
    non-static ones."""
    from match_cuts.model import Zone
    cp = make_proxy(np.zeros((1, FULL_H // 2, FULL_W // 2), np.uint8), (FULL_W, FULL_H))
    h, w = FULL_H // 2, FULL_W // 2
    static = np.ones((h, w), bool)
    static[:, :40] = False
    zones = [Zone("watermark", 380, 1538, 256, 22, 0, 0, True, "", ""), Zone("title", 202, 258, 436, 42, 0, 0, True, "", "")]
    lay = Layout(FULL_W, FULL_H, box=Box(*TRUTH_BOX), zones=zones, background={"type": "solid", "gray": 3.0})
    src, model, rule = L._background_sources(lay, cp, static)
    assert not src[769:780, 190:318].any() and not src[129:150, 101:319].any()       # the zones (proxy px)
    assert src[50, 300] and not src[50, 20] and "zone" in rule
    assert model is not None and float(model[0, 0]) == 3.0
    blur = Layout(FULL_W, FULL_H, box=Box(*TRUTH_BOX), zones=zones, background={"type": "blur", "sigma": 30.0})
    src, model, _rule = L._background_sources(blur, cp, static)
    assert model is None and src[50, 20] and not src[50, 300]


# ----------------------------------------------------------------------------------------------
# fullscreen periods (DESIGN §7 D1 / D8): exact boundaries, statistics without the fullscreen frames
# ----------------------------------------------------------------------------------------------

def d8_scene(n: int, fullscreen: list[tuple[int, int]], dark: list[tuple[int, int]] = (), dim: float = 0.12,
             seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Boxed edit whose fullscreen shots cover the whole canvas with the title / logo / channel name /
    watermark still drawn on top (D8); ``dark`` shots are dimmed to ``dim`` (a night scene: most of the
    canvas changes by less than 20 levels). Returns (competitor proxy frames, RAW content proxy frames)."""
    hole = hole_mask(FULL_W, FULL_H, TRUTH_BOX)
    g = np.zeros((FULL_H, FULL_W, 3), np.uint8)
    cv2.circle(g, (110, 120), 46, (46, 38, 226), -1, cv2.LINE_AA)
    cv2.putText(g, "SynthRecaps", (180, 138), FONT, 1.4, (255, 255, 255), 3, cv2.LINE_AA)
    put_outlined(g, "WAIT FOR THE", (240, 250), 1.8, (255, 255, 255), 2, 3)
    put_outlined(g, "LAST SECOND", (260, 330), 1.8, (31, 210, 255), 2, 3)
    cv2.putText(g, "synthrecaps dot tv", (380, 1530), FONT, 1.0, (140, 140, 140), 2, cv2.LINE_AA)
    glyph = g.max(axis=2) > 0
    tex = texture(FULL_W, FULL_H, seed)
    frames, raw = [], []
    for k in range(n):
        ox, oy = (5 * k) % 380, (3 * k) % 380
        content = cv2.cvtColor(tex[oy:oy + FULL_H, ox:ox + FULL_W], cv2.COLOR_GRAY2BGR)
        if any(a <= k < b for a, b in dark):
            content = (content.astype(np.float32) * dim).astype(np.uint8)
        if any(a <= k < b for a, b in fullscreen):
            img = content.copy()
            img[glyph] = g[glyph]
        else:
            img = g.copy()
            img[hole] = content[hole]
        half = (FULL_W // 2, FULL_H // 2)
        frames.append(cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), half, interpolation=cv2.INTER_AREA))
        raw.append(cv2.resize(cv2.cvtColor(content, cv2.COLOR_BGR2GRAY), half, interpolation=cv2.INTER_AREA))
    return np.stack(frames), np.stack(raw)


@pytest.mark.parametrize("fullscreen,dark", [([(0, 6), (30, 40)], []), ([(20, 30)], [(20, 30)]), ([(52, 60)], [])],
                         ids=["start_and_middle", "dark_shot", "at_the_end"])
def test_fullscreen_period_boundaries_exact(tmp_path, fullscreen, dark):
    n = 60
    frames, raw = d8_scene(n, fullscreen, dark)
    proxy = make_proxy(frames, (FULL_W, FULL_H))
    lay, _ov = analyze(proxy, tmp_path)
    want, k = [], 0
    for a, b in fullscreen:
        if a > k:
            want.append((k, a, "boxed"))
        want.append((a, b, "fullscreen"))
        k = b
    if k < n:
        want.append((k, n, "boxed"))
    assert [(p.comp_in, p.comp_out, p.mode) for p in lay.periods] == want, lay.periods
    for p in lay.periods:                        # D1 consumers read each period's box
        want_box = Box(0, 0, FULL_W, FULL_H, 0) if p.mode == "fullscreen" else lay.box
        assert p.box.to_dict() == want_box.to_dict()
    assert not boxes_equal(lay.box, TRUTH_BOX), lay.box
    # the static statistics ignore the fullscreen frames: canvas static, box interior dynamic, zones found
    st = np.load(lay.static_mask_file)
    assert st[20, 20] and st[900, 20] and st[240:720, 40:500].mean() < 0.01
    assert lay.background["type"] == "solid" and lay.canvas_bg == "#000000"
    assert {"logo", "channel_name", "title", "watermark"} <= {z.type for z in lay.zones}, lay.zones
    # ... and so does the RAW box measurement
    fm = identity_frame_map(n)
    m = L.measure_box_from_raw(lay, None, proxy, raw_proxy(raw, (FULL_W, FULL_H)), fm, Config())
    assert m["ok"] and not [kk for kk in m["frames"] if any(a <= kk < b for a, b in fullscreen)], m["frames"]
    assert not boxes_equal(m["box"], TRUTH_BOX), m["box"]
