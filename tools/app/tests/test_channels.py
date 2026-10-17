"""The channels (mr_app.core.channels): the channel folders, the template base (the PSD's merged image without the
example headline), the frame PNG with your headline (1080x1920, a clean transparent window) and the live preview.
Run: python -m pytest tools/app/tests"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import channels as C   # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def _real(name: str) -> C.Channel:
    try:
        return C.find(name)
    except LookupError:
        pytest.skip(f"the {name} channel is not in assets/channels")


# ---------------------------------------------------------------------------------------------------------------------
# the channel folders
# ---------------------------------------------------------------------------------------------------------------------

def test_the_three_channels_are_found_with_their_links():
    chs = {c.name: c for c in C.channels()}
    if not {"LouLaughs", "MikeMemesYou", "Flick707"} <= set(chs):
        pytest.skip("the channel folders are not in assets/channels")
    for name in ("LouLaughs", "MikeMemesYou", "Flick707"):
        ch = chs[name]
        assert ch.link == f"https://www.youtube.com/@{name}" and ch.handle == f"@{name}"
        assert ch.psd == REPO / "assets" / "channels" / name / "template.psd"
        assert ch.watermark == REPO / "assets" / "channels" / name / "watermark.png"
        info = json.loads((ch.folder / "channel.json").read_text(encoding="utf-8"))
        assert info == {"name": name, "link": ch.link, "handle": ch.handle, "shorts_only": ch.shorts_only}
    assert chs["Flick707"].shorts_only and not chs["LouLaughs"].shorts_only and not chs["MikeMemesYou"].shorts_only
    assert C.find("loulaughs").name == "LouLaughs"
    with pytest.raises(LookupError):
        C.find("NoSuchChannel")


def test_a_new_folder_is_a_new_channel_with_or_without_channel_json(tmp_path):
    root = tmp_path / "channels"
    for name in ("New Channel", "Partial", "Broken", "NoTemplate"):
        (root / name).mkdir(parents=True)
    for name in ("New Channel", "Partial", "Broken"):
        (root / name / "template.psd").write_bytes(b"8BPS")
    (root / "New Channel" / "watermark.png").write_bytes(b"png")
    (root / "Partial" / "channel.json").write_text(json.dumps({"link": "https://www.youtube.com/@partial_real",
                                                               "shorts_only": True}), encoding="utf-8")
    (root / "Broken" / "channel.json").write_text("{not json", encoding="utf-8")
    (root / "NoTemplate" / "channel.json").write_text("{}", encoding="utf-8")
    (root / "loose.psd").write_bytes(b"8BPS")
    chs = C.channels(root)
    assert [c.name for c in chs] == ["Broken", "New Channel", "Partial"]        # no template.psd: no channel
    new, partial, broken = chs[1], chs[2], chs[0]
    assert (new.handle, new.link, new.shorts_only) == ("@NewChannel", "https://www.youtube.com/@NewChannel", False)
    assert new.watermark == root / "New Channel" / "watermark.png" and new.psd == root / "New Channel" / "template.psd"
    assert (partial.link, partial.handle, partial.shorts_only) == ("https://www.youtube.com/@partial_real",
                                                                   "@Partial", True)
    assert partial.watermark is None
    assert (broken.handle, broken.link) == ("@Broken", "https://www.youtube.com/@Broken")
    assert C.find("new channel", root) == new
    assert C.channels(tmp_path / "nothing") == []


# ---------------------------------------------------------------------------------------------------------------------
# the base: the PSD's merged image without the example headline
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["LouLaughs", "MikeMemesYou", "Flick707"])
def test_the_base_is_the_merged_image_with_the_headline_area_white(name):
    ch = _real(name)
    base = C.base_template(ch)
    assert base.size == (2160, 3840) and base.mode == "RGBA"
    a = np.asarray(base)
    x0, y0, x1, y1 = C.HEADLINE_AREA
    assert (a[y0:y1, x0:x1] == 255).all()                                   # the example headline is gone
    wx0, wy0, wx1, wy1 = C.WINDOW
    assert (a[wy0 + 150:wy1 - 150, wx0 + 150:wx1 - 150, 3] == 0).all()        # the video window stays transparent
    assert (a[600:700, :, 3] == 255).all() and (a[3600:3700, :, 3] == 255).all()     # the card around it
    avatar = a[260:500, 310:550, :3].astype(int)
    assert (avatar.max(axis=2) - avatar.min(axis=2)).mean() > 15            # the channel's picture is there
    info = C.base_info(ch)
    assert info["method"] == "merged image, headline area painted white"
    assert info["measured"].endswith("548,700 of 548,700 pixels white (darkest 255/255, least opaque 255/255)")
    assert C.headline_anchor(ch) == (1085.4632, 849.2045)                  # the type layer's: centre x, baseline 1


class _Layer:
    """A stand-in for a psd-tools type layer: its transform and the bits of its text engine data the anchor reads."""

    def __init__(self, transform, shape=0, justification=2):
        self.transform = transform
        self.engine_dict = {"Rendered": {"Shapes": {"Children": [{"ShapeType": shape}]}},
                            "ParagraphRun": {"RunArray": [{"ParagraphSheet": {"Properties": {
                                "Justification": justification}}}]}}


def test_the_headline_anchor_is_read_only_from_centred_point_text():
    assert C._anchor(_Layer((1.0, 0.0, 0.0, 1.0, 1085.46322441, 849.20445730))) == [1085.4632, 849.2045]
    assert C._anchor(_Layer((0.83, 0.0, 0.0, 0.83, 1085.0, 849.0))) is None             # scaled text
    assert C._anchor(_Layer((1.0, 0.0, 0.0, 1.0, 1085.0, 849.0), shape=1)) is None      # a text box
    assert C._anchor(_Layer((1.0, 0.0, 0.0, 1.0, 1085.0, 849.0), justification=0)) is None   # left aligned
    assert C._anchor(object()) is None


def test_the_base_is_cached_and_made_again_when_the_psd_changes(tmp_path, monkeypatch):
    psd_tools = pytest.importorskip("psd_tools")
    folder = tmp_path / "channels" / "Synthetic"
    folder.mkdir(parents=True)
    psd = folder / "template.psd"

    def write(colour):
        img = Image.new("RGBA", (2160, 3840), colour)
        img.paste((0, 0, 0, 0), (44, 1054, 2114, 3473))                       # a window
        psd_tools.PSDImage.frompil(img).save(psd)
    write((255, 255, 255, 255))
    monkeypatch.setattr(C, "cache_dir", lambda: tmp_path / "cache")
    builds = []
    real_build = C.build_base
    monkeypatch.setattr(C, "build_base", lambda ch: builds.append(ch.name) or real_build(ch))
    ch = C.channels(tmp_path / "channels")[0]
    first = C.base_template(ch)
    assert C.base_template(ch).tobytes() == first.tobytes() and builds == ["Synthetic"]       # the second: cached
    assert (tmp_path / "cache" / "Synthetic" / "base_2160x3840.png").is_file()
    assert C.base_info(ch)["method"] == "merged image as it is"                               # no headline in it
    write((250, 240, 10, 255))                                                                # the PSD changes
    st = psd.stat()
    os.utime(psd, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    second = C.base_template(ch)
    assert builds == ["Synthetic", "Synthetic"]
    assert second.getpixel((10, 10)) == (250, 240, 10, 255) and second.getpixel((1000, 2000))[3] == 0


# ---------------------------------------------------------------------------------------------------------------------
# the frame PNG with your headline
# ---------------------------------------------------------------------------------------------------------------------

def _check_clean_window(png: Path) -> tuple[float, float, float, float]:
    """The window of a frame PNG: exactly transparent inside, the card exactly opaque around it, the in-between
    only on its edge; returns load_frame's hole."""
    a = np.asarray(Image.open(png).convert("RGBA"))[..., 3]
    hole = a < 128
    lab, n = ndimage.label(hole)
    big = lab == (np.argmax(np.bincount(lab.ravel())[1:]) + 1)
    inside = ndimage.binary_erosion(big, iterations=2)
    near = ndimage.binary_dilation(big, iterations=2)
    assert (a[inside] == 0).all()
    assert (a[~near] == 255).all()
    sys.path.insert(0, str(REPO / "tools" / "match_cuts"))
    from match_cuts.frame import load_frame
    return load_frame(png).hole


def test_the_template_is_a_1080x1920_frame_png_with_a_clean_window(tmp_path):
    ch = _real("MikeMemesYou")
    r = C.make_template(ch, "The fact that he didn't even think twice is hilarious 🏃‍♂️😭", tmp_path / "f.png")
    assert r.png == tmp_path / "f.png" and Image.open(r.png).size == (1080, 1920)
    assert (r.shrunk, r.scale_pct, r.why, r.warnings) == (False, 100.0, "", [])
    assert r.layout.lines == ["The fact that he didn't even", "think twice is hilarious 🏃‍♂️😭"]
    x, y, w, h = _check_clean_window(r.png)
    assert r.hole == (x, y, w, h)
    # the window: x 22-1057, y 527-1736 on the sequence (x 44-2114, y 1054-3473 on the PSD), give or take 1 px
    assert abs(x - 22) <= 1 and abs(y - 527) <= 1 and abs(x + w - 1 - 1057) <= 1 and abs(y + h - 1 - 1736) <= 1
    d = np.asarray(Image.open(r.png).convert("L"))
    assert (d[380:430, 140:950] < 50).sum() > 5000                       # the headline is on it


def test_a_title_too_long_is_shrunk_and_the_result_says_so(tmp_path):
    ch = _real("LouLaughs")
    title = "The fact that the dog understood the assignment better than him 😭"
    r = C.make_template(ch, title, tmp_path / "long.png")
    assert r.shrunk and r.scale_pct < 100 and r.why.startswith(f"shrunk to {r.scale_pct:.0f} %")
    assert r.summary() == f"2 lines, {r.why}"
    _check_clean_window(r.png)


def test_the_preview_is_quick_enough_to_follow_your_typing():
    ch = _real("LouLaughs")
    C.preview(ch, "warm up")
    title = "The fact that almonds sent him to the hospital is hilarious 🥜😭"
    times = []
    for i in range(1, len(title) + 1, 3):                                   # a key press after another
        t0 = time.perf_counter()
        img = C.preview(ch, title[:i])
        times.append(time.perf_counter() - t0)
    assert img.size == (540, 960) and img.mode == "RGBA"
    assert float(np.median(times)) < 0.2                                   # measured: about 15-30 ms
    a = np.asarray(img)
    assert a[700, 270, 3] == 0 and a[160, 270, 3] == 255                   # the window clear, the card opaque
    assert (np.asarray(img.convert("L"))[185:260, 30:510] < 60).sum() > 1000    # the headline
    small = C.preview(ch, "Hilarious", width=360)
    assert small.size == (360, 640)
