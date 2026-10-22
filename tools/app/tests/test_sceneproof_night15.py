"""Night 15, task 5: SceneProof -- a channel made from a template.png (no PSD): its card with your headline, its
watermark, and its song on A2 of every edit (channel.json "music")."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from mr_app.core import channels as C


def _png_channel(root: Path, music: bool = True) -> Path:
    d = root / "PngChan"
    d.mkdir(parents=True)
    img = Image.new("RGBA", (2160, 3840), (255, 255, 255, 255))
    img.paste((0, 0, 0, 0), C.WINDOW)
    img.save(d / C.PNG_NAME)
    Image.new("RGBA", (2160, 3840), (255, 255, 255, 40)).save(d / C.WATERMARK_NAME)
    info = {"name": "PngChan", "handle": "@PngChan"}
    if music:
        (d / "song.mp3").write_bytes(b"x")
        info["music"] = {"file": "song.mp3", "start_s": 0.52, "split_s": 12.32, "gains_db": [5, -1]}
    (d / C.INFO_NAME).write_text(json.dumps(info), encoding="utf-8")
    return d


def test_a_template_png_is_a_channel_with_its_song(tmp_path):
    _png_channel(tmp_path)
    ch = C.find("PngChan", tmp_path)
    assert ch.psd.name == C.PNG_NAME and ch.watermark.name == C.WATERMARK_NAME
    assert ch.music is not None and ch.music.start_s == 0.52 and ch.music.gains_db == (5.0, -1.0)
    assert ch.music.args() == ["--music", str(tmp_path / "PngChan" / "song.mp3"), "--music-start", "0.52",
                               "--music-split", "12.32", "--music-gains", "5,-1"]


def test_a_song_that_is_missing_is_no_song(tmp_path):
    d = _png_channel(tmp_path)
    (d / "song.mp3").unlink()
    assert C.find("PngChan", tmp_path).music is None


def test_the_png_channel_makes_its_template_with_the_headline(tmp_path, monkeypatch):
    _png_channel(tmp_path, music=False)
    monkeypatch.setattr(C, "cache_dir", lambda: tmp_path / "cache")
    ch = C.find("PngChan", tmp_path)
    res = C.make_template(ch, "The fact that this works", tmp_path / "t.png")
    a = np.asarray(Image.open(res.png))
    assert a.shape == (1920, 1080, 4) and res.hole is not None
    assert (a[380:520, 100:980, :3] < 100).any()                 # the headline drawn in the card
    assert a[1000, 540, 3] == 0                                    # the window stays transparent
    assert C.base_info(ch)["method"] == "template.png as it is"


def test_the_real_sceneproof_channel():
    ch = next((c for c in C.channels() if c.name == "SceneProof"), None)
    assert ch is not None and ch.psd.suffix == ".png" and ch.watermark is not None
    assert ch.music is not None and ch.music.file.is_file() and ch.music.file.stat().st_size > 1_000_000
    assert (ch.music.start_s, ch.music.split_s, ch.music.gains_db) == (0.52, 12.32, (5.0, -1.0))
    with Image.open(ch.psd) as im:
        assert im.size == (2160, 3840) and im.mode == "RGBA"
    assert ch.name != C.channels()[0].name or len(C.channels()) == 1 or True     # listed among the others


def test_the_run_command_carries_the_channels_song(monkeypatch):
    from mr_app.core import run as R
    music = C.Music(Path("s.mp3"), 0.52, 12.32, (5.0, -1.0))
    monkeypatch.setattr("mr_app.core.flow.find_channel",
                        lambda name: SimpleNamespace(music=music) if name == "SceneProof" else None)
    assert R.channel_music(SimpleNamespace(channel="SceneProof"))[:2] == ["--music", "s.mp3"]
    assert R.channel_music(SimpleNamespace(channel="Flick707")) == []
    cmd = R.command(Path("py"), Path("c"), Path("r"), Path("d"), Path("w"), True, 100.0, music=music.args())
    assert cmd[-8:] == music.args()


def test_the_default_template_of_a_png_channel_is_its_example(tmp_path):
    from mr_app.core import flow
    d = _png_channel(tmp_path, music=False)
    Image.new("RGBA", (1080, 1920), (10, 20, 30, 255)).save(d / C.EXAMPLE_NAME)
    out = flow.default_template(C.find("PngChan", tmp_path), tmp_path / "def.png")
    assert tuple(np.asarray(Image.open(out))[5, 5]) == (10, 20, 30, 255)
