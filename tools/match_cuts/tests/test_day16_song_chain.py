"""Day 16, task 4: the SceneProof song as your own project has it -- each piece's Volume its Clip Gain + your Volume
Level 8.5 dB (+13.5 / +7.5 dB), and the preset "MovieRecaps song chain" (Hard Limiter, Input Boost 2.00 dB + Amplify
+3.00 dB) for both song clips; the preset is the A1 preset (Premiere's own export) with those values."""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from match_cuts import music
from match_cuts.common import ffmpeg_bin

ROOT = Path(__file__).resolve().parents[3]


def _xml(path: Path, frames: int = 600) -> Path:
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5"><sequence>'
                    "<rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate>"
                    f"<duration>{frames}</duration><media><video><track/></video><audio><track/></audio></media>"
                    "</sequence></xmeml>\n", encoding="utf-8")
    return path


def _song(path: Path) -> Path:
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
                    "-ac", "2", str(path)], check=True)
    return path


def test_each_piece_gets_its_clip_gain_plus_the_volume_level_and_names_the_preset(tmp_path):
    xml, song = _xml(tmp_path / "1_edit.xml"), _song(tmp_path / "song.mp3")
    out = music.add_music(xml, song, 0.52, 12.32, [5.0, -1.0], tmp_path / "media", volume_db=8.5)
    assert [p["volume_db"] for p in out["pieces"]] == [13.5, 7.5]
    root = ET.parse(str(xml)).getroot()
    clips = [ci for ci in root.iter("clipitem") if ci.get("id", "").startswith(music.CLIP_ID)]
    vals = [float(ci.find("filter/effect/parameter/value").text) for ci in clips]
    assert vals == [pytest.approx(10 ** (13.5 / 20), abs=1e-6), pytest.approx(10 ** (7.5 / 20), abs=1e-6)]
    notes = [ci.findtext("comments/mastercomment2") for ci in clips]
    assert all('drop the preset "MovieRecaps song chain"' in n for n in notes) and "+13.5 dB" in notes[0]
    xml2 = _xml(tmp_path / "old.xml")                       # night 15's way, without --music-volume: as before
    old = music.add_music(xml2, song, 0.52, 12.32, [5.0, -1.0], tmp_path / "media")
    assert [p["gain_db"] for p in old["pieces"]] == [5.0, -1.0] and "volume_db" not in old["pieces"][0]


def test_the_preset_is_the_a1_preset_with_input_boost_2_db_and_amplify_3_db():
    t = music.song_preset()
    assert "<Name>MovieRecaps song chain</Name>" in t and "MovieRecaps audio chain</Name>" not in t
    assert music.boost_value(0) == "0.666666666667" and music.boost_value(2) == "0.680000000000"   # song.prproj: 0.68
    assert music.amplify_value(3) == "0.687500000000" and abs(float(music.amplify_value(4.5)) - 0.697917) < 1e-6
    boosts = re.findall(r"<Name>Input Boost</Name>.*?<StartKeyframe>-?\d+,([^,<]*),", t, re.S)
    assert boosts == [music.boost_value(2)] * 3                           # the Hard Limiter for 1 / 2 / 6 channels
    gains = re.findall(r"<Name>Gain</Name>.*?<StartKeyframe>-?\d+,([^,<]*),", t, re.S)
    assert gains == [music.amplify_value(3)]
    a1 = (ROOT / "assets" / "premiere" / "MovieRecaps audio chain.prfpset").read_text(encoding="utf-8")
    uid = re.findall(r"PresetUID>([^<]*)<", t)[0]
    assert uid not in a1                                                   # its own preset id
    shipped = (ROOT / "assets" / "premiere" / "MovieRecaps song chain.prfpset").read_text(encoding="utf-8")
    assert re.sub(r"PresetUID>[^<]*<", "", shipped) == re.sub(r"PresetUID>[^<]*<", "", t)


def test_the_sceneproof_channel_passes_its_volume_level():
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "app"))
    from mr_app.core import channels
    m = channels._music(ROOT / "assets" / "channels" / "SceneProof",                    # noqa: SLF001
                        {"file": "x.mp3", "start_s": 0.52, "split_s": 12.32, "gains_db": [5, -1], "volume_db": 8.5}) \
        if (ROOT / "assets" / "channels" / "SceneProof" / "x.mp3").is_file() else None
    spec = __import__("json").loads((ROOT / "assets" / "channels" / "SceneProof" / "channel.json").read_text("utf-8"))
    assert spec["music"]["volume_db"] == 8.5 and spec["music"]["gains_db"] == [5.0, -1.0]
    mm = channels.Music(Path("song.mp3"), 0.52, 12.32, (5.0, -1.0), 8.5)
    assert mm.args()[-2:] == ["--music-volume", "8.5"] and m is None
