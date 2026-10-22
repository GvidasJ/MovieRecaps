"""Night 15, task 3: a competitor that shows the RAW stretched sideways (American Dad S03E02: a 4:3 episode pulled out
to 16:9 by the reel -- 94 % of its sound found, 0 % of its picture after 2 hours: one zoom on both axes can never fit
it). stretch.detect fits a full affine on frames where the sound placed the competitor; the RAW then gets that pixel
aspect, S2 converts it as wide, and V1 plays that copy."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import stretch as ST


def _scene(seed: int = 3, w: int = 640, h: int = 480) -> np.ndarray:
    """A cartoon-like picture: flat shapes with dark outlines."""
    import cv2
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 200, np.uint8)
    for _ in range(40):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        r = int(rng.integers(10, 60))
        c = int(rng.integers(30, 230))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), r, c, -1)
            cv2.circle(img, (x, y), r, 20, 2)
        else:
            cv2.rectangle(img, (x, y), (x + r, y + int(r * 0.7)), c, -1)
            cv2.rectangle(img, (x, y), (x + r, y + int(r * 0.7)), 20, 2)
    return img


def _stretched(img: np.ndarray, sx: float, sy: float, rot: float = 0.0) -> np.ndarray:
    import cv2
    h, w = img.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, 1.0)
    M[0] *= sx
    M[1] *= sy
    return cv2.warpAffine(img, M, (int(w * sx), int(h * sy)), borderValue=0)


def test_a_sideways_stretch_is_measured_and_a_zoom_is_not():
    raw = _scene()
    f = ST.fit(_stretched(raw, 1.333 * 0.8, 0.8), raw)
    assert f is not None and abs(f["ratio"] - 1.333) < 0.03 and abs(f["rot"]) < 1.0 and f["inliers"] >= 20
    g = ST.fit(_stretched(raw, 0.8, 0.8), raw)                          # a plain zoom: the same on both axes
    assert g is not None and abs(g["ratio"] - 1.0) < 0.03


def test_snap_to_the_usual_stretches():
    assert ST.snap(1.314) == Fraction(4, 3) and ST.snap(0.76) == Fraction(3, 4)
    assert ST.snap(1.2) == Fraction(6, 5)                               # any other: as measured
    assert ST.snap(1.5) == Fraction(3, 2)


def test_parse_the_switch():
    assert ST.parse("auto") == "auto" and ST.parse(None) == "auto"
    assert ST.parse("off") is None and ST.parse("1:1") is None
    assert ST.parse("4:3") == Fraction(4, 3) and ST.parse("1.333") == Fraction(4, 3)
    with pytest.raises(ValueError):
        ST.parse("wide")


def _pair(places):
    return SimpleNamespace(sound=SimpleNamespace(details={"tried": {"1.00": [
        {"at": a, "raw_at": r, "found": True} for a, r in places] + [{"at": 99, "raw_at": 5, "found": False}]}}))


def test_places_are_the_middles_of_the_found_pieces_at_the_normal_speed():
    p = _pair([(0.0, 290.0), (6.0, 296.0)])
    assert ST.places(p) == [(1.5, 291.5), (7.5, 297.5)]
    sped = SimpleNamespace(sound=SimpleNamespace(details={"tried": {"1.00": [], "1.25 tempo": [
        {"at": 0, "raw_at": 10, "found": True}]}}))
    assert ST.places(sped) == [] and ST.places(None) == []              # sped-up: not checked (the safe side)


@pytest.mark.parametrize("sx, want", [(1.333, Fraction(4, 3)), (1.0, None)])
def test_detect_on_frames(monkeypatch, sx, want):
    """detect() with the frames read from memory: a 4:3 picture stretched to 16:9 gives 4/3; a plain zoom nothing."""
    scenes = {t: _scene(seed=t) for t in range(6)}

    def frame(path, t, w):
        k = int(round(t)) % 6 if path == "raw" else int(round(t - 0.0)) % 6
        img = scenes[k]
        return _stretched(img, 0.9 * sx, 0.9) if path == "comp" else img
    monkeypatch.setattr(ST, "_frame", frame)
    monkeypatch.setattr(ST, "RAW_NEIGHBOURS", (0.0,))
    pair = _pair([(float(k) - 1.5, float(k) - 1.5) for k in range(6)])
    res = ST.detect("comp", "raw", pair)
    assert res["stretch"] == want, res["why"]


def test_apply_to_gives_the_raw_its_pixel_aspect_and_a_conversion():
    from match_cuts.probe import StreamInfo
    info = StreamInfo(path="raw.mp4", role="raw")
    info.width, info.height, info.sar, info.rotation = 1440, 1080, Fraction(1), 0
    info.ae_issues = ["vcodec: hevc"]
    out = ST.apply_to(info, Fraction(4, 3))
    assert out.sar == Fraction(4, 3) and (out.display_width, out.display_height) == (1920, 1080)
    assert out.ae_issues[0] == "vcodec: hevc" and out.ae_issues[-1].startswith("sar: stretched")
    from match_cuts.conform import output_geometry
    geo = output_geometry(out)
    assert (geo["w"], geo["h"]) == (1920, 1080) and geo["scale"] == (1920, 1080)
    from match_cuts.edit_media import picture_choice
    assert picture_choice(out, out, True)["how"] == "convert"            # V1 plays S2's stretched copy


REAL = Path("D:/Shared/laptop_cases/2026-10-21_The Most Dramatic Dad of All Time 💀😭")


@pytest.mark.slow
def test_the_real_american_dad_reel_is_stretched_4_3():
    comp = REAL / "Video by cinemaclips991 [DczA1q3MTge].mp4"
    raw = REAL / "American Dad! - S03E02 - The American Dad After School Special - [HDTV-1080p]h265 AAC.mp4"
    if not (comp.is_file() and raw.is_file()):
        pytest.skip("the real files are on the PC's D:\\Shared only")
    from match_cuts import pair_check as PC
    pair = PC.check(comp, raw, None)
    assert ST.detect(str(comp), str(raw), pair)["stretch"] == Fraction(4, 3)
