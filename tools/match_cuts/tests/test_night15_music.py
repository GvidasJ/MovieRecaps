"""Night 15, task 5: a channel's song on A2 of 1_edit.xml (SceneProof: One Piece "Mother Sea" from 0.52 s, cut at
12.32 s, +5 dB / -1 dB as in song.prproj), added after the edit's checks; A1 untouched."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from match_cuts import music as M
from match_cuts.common import ffmpeg_bin


def test_parse_gains():
    assert M.parse_gains("5,-1") == [5.0, -1.0] and M.parse_gains("") == [0.0] and M.parse_gains([3]) == [3.0]


def test_pieces_cut_at_the_split_and_end_with_the_sequence():
    ps = M.pieces(seq_frames=25 * 47, fps=Fraction(25), song_s=204.8, start_s=0.52, split_s=12.32, gains_db=[5, -1])
    assert [(p["start"], p["end"], p["in"], p["out"], p["gain_db"]) for p in ps] == [
        (0, 308, 13, 321, 5.0), (308, 1175, 321, 1188, -1.0)]          # song.prproj: 0.52-12.84 s, then 12.84 s on
    short = M.pieces(25 * 10, Fraction(25), 204.8, 0.52, 12.32, [5, -1])
    assert len(short) == 1 and short[0]["end"] == 250 and short[0]["gain_db"] == 5.0
    assert M.pieces(25 * 300, Fraction(25), 100.0, 0.52, 12.32, [5, -1])[-1]["end"] == int((100.0 - 0.52) * 25)


def _xml(path: Path, n: int = 600, timebase: int = 24, ntsc: str = "TRUE") -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="s">'
        f"<name>e</name><duration>{n}</duration><rate><timebase>{timebase}</timebase><ntsc>{ntsc}</ntsc></rate>"
        "<media><video><track/></video><audio><numOutputChannels>2</numOutputChannels><track>"
        '<clipitem id="clipitem-a1"><name>raw.wav</name></clipitem></track></audio></media></sequence></xmeml>\n',
        encoding="utf-8")
    return path


@pytest.fixture
def song(tmp_path) -> Path:
    p = tmp_path / "song one.mp3"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=40", "-ac", "2", "-y", str(p)],
                   check=True, timeout=60)
    return p


def test_the_song_goes_on_a2_and_a1_stays(tmp_path, song):
    xml = _xml(tmp_path / "1_edit.xml")
    media = tmp_path / "extras" / "media"
    r = M.add_music(xml, song, 0.52, 12.32, [5, -1], media)
    assert r["clips"] == 2 and Path(r["file"]).parent == media and Path(r["file"]).is_file()
    text = xml.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')
    seq = ET.fromstring(text.split("\n", 2)[2]).find("sequence")
    a1, a2 = seq.findall("media/audio/track")
    assert [c.get("id") for c in a1.findall("clipitem")] == ["clipitem-a1"]          # A1 untouched
    clips = a2.findall("clipitem")
    assert [c.findtext("start") for c in clips] == ["0", "295"] and clips[-1].findtext("end") == "600"
    lv = [float(c.find("filter/effect/parameter/value").text) for c in clips]
    assert lv == pytest.approx([1.778279, 0.891251], abs=1e-5)                       # song.prproj's 1.7783 / 0.8913
    assert "pathurl" in ET.tostring(clips[0].find("file"), encoding="unicode")
    assert clips[1].find("file").get("id") == M.FILE_ID and clips[1].find("file/pathurl") is None
    assert M.add_music(xml, song, 0.52, 12.32, [5, -1])["error"] == "the XML has its music already"


def test_a_missing_song_leaves_the_xml_as_it_was(tmp_path):
    xml = _xml(tmp_path / "1_edit.xml")
    before = xml.read_text(encoding="utf-8")
    assert "not there" in M.add_music(xml, tmp_path / "nope.mp3")["error"]
    assert xml.read_text(encoding="utf-8") == before


def test_the_pipeline_step_needs_music_and_warns_on_failure(tmp_path, song):
    warned = []
    cfg = SimpleNamespace(music_file="", deliver=tmp_path, media_dir=None)
    ctx = SimpleNamespace(cfg=cfg, warn=warned.append, dlog=SimpleNamespace(record=lambda *a, **k: None))
    assert M.apply(ctx) is None
    cfg.music_file = str(song)
    assert M.apply(ctx)["error"] == "no 1_edit.xml"
    _xml(tmp_path / "1_edit.xml")
    cfg.music_start_s, cfg.music_split_s, cfg.music_gains_db = 0.52, 12.32, "5,-1"
    assert M.apply(ctx)["clips"] == 2 and not warned


def test_the_switches_reach_the_config():
    from match_cuts.cli import build_parser, config_from_args
    a = build_parser().parse_args(["--competitor", "c.mp4", "--raw", "r.mp4", "--music", "s.mp3", "--music-start",
                                   "0.52", "--music-split", "12.32", "--music-gains", "5,-1"])
    cfg = config_from_args(a, "c.mp4", "r.mp4")
    assert (cfg.music_file, cfg.music_start_s, cfg.music_split_s, cfg.music_gains_db) == ("s.mp3", 0.52, 12.32, "5,-1")
    assert "music_file" not in cfg.analysis_params()                                 # no cache key changes
