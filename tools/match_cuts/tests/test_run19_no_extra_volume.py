"""Run 19, task 2 -- no temporary extra Volume effects: 1_edit.xml gives every A1 clip only Volume 0 dB and Panner 0
(Premiere imports one Volume: the clip's own), and its Audio Gain "Normalize All Peaks to 0 dB" only in its comment
(the app's save step writes it as the clip's Clip Gain). Until run 18 the gain came as Gain filters, which Premiere
imports as one or more EXTRA Volume effects (3 on a clip until Ctrl+S)."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET

import pytest

from match_cuts import export_xml_edl as ex
from match_cuts import music
from match_cuts.common import ffmpeg_bin

from test_night11_audio import _gain_filter, _media, _played_peak, _write


def test_every_a1_clip_has_only_volume_0_db_and_panner_its_gain_in_its_comment(tmp_path):
    xml, res = _write(tmp_path)
    _, y = _media(tmp_path)
    root = ET.parse(xml).getroot()
    fps = ex._xml_rate(root.find("sequence"))
    items = root.findall("sequence/media/audio/track/clipitem")
    assert items and not any(e.text == ex.AUDIO_GAIN_ID for e in root.iter("effectid"))      # no Gain filter at all
    for ci in items:
        effects = [e.findtext("effectid") for e in ci.findall("filter/effect")]
        assert [e for e in effects if e != "timeremap"] == ["audiolevels", "audiopan"]
        want = ex.normalize_gain_db(_played_peak(ci, fps, y))
        assert ex.comment_gain_db(ci) == pytest.approx(want, abs=0.01)
        head = f"audio chain: Audio Gain {want:+.2f} dB" if want else "audio chain: Audio Gain 0 dB (silent"
        assert ci.findtext("comments/mastercomment2").startswith(head)
    assert ex.premiere_audio_problems(xml) == []
    assert any(c["gain_db"] > 15 for c in res["audio_chain"])         # (a clip run 18 split into stacked Volumes)


def test_a_gain_filter_would_be_an_extra_volume_and_fails_the_check(tmp_path):
    xml, _ = _write(tmp_path)
    tree = ET.parse(xml)
    ci = tree.getroot().find("sequence/media/audio/track/clipitem")
    lv = next(f for f in ci.findall("filter") if f.find("effect/effectid").text == "audiolevels")
    ci.insert(list(ci).index(lv), _gain_filter("1.995262"))          # run 18's form: +6 dB as a Gain filter
    bad = tmp_path / "bad.xml"
    tree.write(bad)
    probs = ex.premiere_audio_problems(bad)
    assert len(probs) == 1 and "Gain filter(s) on it (Gain +6.00 dB)" in probs[0] and "extra Volume" in probs[0]


def test_the_comment_gain_is_judged_against_the_sound(tmp_path):
    xml, _ = _write(tmp_path)
    tree = ET.parse(xml)
    ci = tree.getroot().find("sequence/media/audio/track/clipitem")
    cm = ci.find("comments/mastercomment2")
    cm.text = ex.AUDIO_GAIN_COMMENT.sub("audio chain: Audio Gain +1.00 dB", cm.text, count=1)
    bad = tmp_path / "bad.xml"
    tree.write(bad)
    probs = ex.premiere_audio_problems(bad)
    assert len(probs) == 1 and "its Audio Gain is +1.00 dB, but the loudest sample it plays" in probs[0]


def test_the_song_on_a2_is_not_judged_as_the_voice(tmp_path):
    xml, _ = _write(tmp_path)
    root = ET.parse(xml).getroot()
    seq = root.find("sequence")
    if seq.find("duration") is None:
        ET.SubElement(seq, "duration").text = str(max(int(c.findtext("end")) for c in root.iter("clipitem")))
        xml.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, "unicode"),
                       encoding="utf-8")
    song = tmp_path / "song.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "60", str(song)], check=True)
    assert music.add_music(xml, song, 0.52, 1.0, [5.0, -1.0], media_dir=tmp_path, volume_db=8.5).get("clips") == 2
    assert ex.premiere_audio_problems(xml) == []                 # (its Volume = Clip Gain + 8.5 dB: music.py's own)
    assert ex.premiere_item_problems(xml) == []                  # (and never "doubled audio": task 1)
