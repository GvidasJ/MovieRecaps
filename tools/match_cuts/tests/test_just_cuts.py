"""Day 12, Just cuts (match_cuts.just_cuts): a RAW alone -> 1_edit.xml of the whole video cut at every scene change,
no mini cuts, the sequence at the RAW's rate, the audio chain on every audio clip, the frame and the watermark. A short
synthetic video (four shots: 2 s, 2 s, a 0.2 s flash, 2 s; a tone) -- seconds to make and run."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from match_cuts import just_cuts as JC
from match_cuts.common import ffmpeg_bin


def test_no_cut_leaves_a_shot_shorter_than_the_minimum():
    assert JC.keep_changes([2.0, 4.0, 4.2, 5.9], 6.2) == [2.0, 4.0]            # the flash and the last 0.3 s play on
    assert JC.keep_changes([0.1, 0.3, 1.0], 3.0) == [1.0]
    assert JC.keep_changes([], 3.0) == []
    assert JC.shot_bounds([2.0, 4.0], 372) == [(0, 120), (120, 240), (240, 372)]


def test_build_cutlist_one_segment_per_shot_framed_on_its_face():
    """(day 12 extra) The RAW as an edit of itself: each shot one segment at its own frames, centred when no face is
    found, else moved onto the face; the face finder is told what the shot before showed (the same person)."""
    bounds = JC.shot_bounds([2.0, 4.0], 372)
    win, seq = (0.0, 300.0, 1080.0, 1000.0), (1080, 1920)
    asked = []

    def face_x(t0, t1, view):
        asked.append((round(t0, 3), round(t1, 3), view))
        return 1500.0 if len(asked) == 2 else None
    cl, notes = JC.build_cutlist({"file": "raw.mp4", "width": 1920, "height": 1080}, 372, bounds, win, seq, face_x)
    assert [(s.id, s.comp_in, s.comp_out, s.label) for s in cl.segments] == \
        [(1, 0, 120, "shot 1"), (2, 120, 240, "shot 2"), (3, 240, 372, "shot 3")]
    assert [s.raw_in_seconds for s in cl.segments] == [0.0, 2.0, 4.0]
    assert [n["face_x"] for n in notes] == [None, 1500.0, None]
    assert asked[0][2] is None and asked[1][2] is not None                 # the first shot has nothing before it
    centred, on_face = cl.segments[0].transform, cl.segments[1].transform
    assert centred != on_face and cl.segments[2].transform == centred
    s = centred["s"] if "s" in centred else centred["scale"]
    assert s * 1920 >= 1080 and s * 1080 >= 1000 - 1e-6                   # the RAW covers the window
    assert cl.competitor["frames"] == 372 and (cl.competitor["width"], cl.competitor["height"]) == seq
    assert cl.layout["box"]["y"] == 300.0 and cl.layout["captions"] == []
    plain, _ = JC.build_cutlist({"width": 1920, "height": 1080}, 120, [(0, 120)], win, seq)    # no face finder
    assert len(plain.segments) == 1 and plain.segments[0].transform == centred


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("jc")
    out = d / "raw.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:d=2",
                    "-f", "lavfi", "-i", "color=c=red:size=640x360:rate=30:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:size=640x360:rate=30:d=0.2",
                    "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6.2",
                    "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0,format=yuv420p[v]",
                    "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def test_the_whole_video_is_cut_at_each_scene_change_with_the_audio_chain_at_its_own_rate(raw, tmp_path):
    from match_cuts import export_xml_edl as X
    run = tmp_path / "cuts"
    wm = tmp_path / "wm.png"
    from PIL import Image
    Image.new("RGBA", (2160, 3840), (255, 255, 255, 40)).save(wm)
    r = subprocess.run([sys.executable, "-m", "match_cuts.just_cuts", "--raw", str(raw), "--run-dir", str(run),
                        "--work", str(tmp_path / "work"), "--watermark", str(wm), "--no-faces"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(Path(JC.__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr[-2000:]
    info = json.loads((run / "extras" / JC.JSON).read_text(encoding="utf-8"))
    assert info["found"] == 3 and info["cuts"] == 2 and info["clips"] == 3 and info["fps"] == "30/1"
    assert info["audio_chain"] == 3 and info["ok"] is True
    xml = run / "1_edit.xml"
    x = X.parse_premiere_xml(xml)
    assert x["duration"] == 186                                       # the whole video, at 30 fps
    text = xml.read_text(encoding="utf-8")
    assert "frame.png" in text and "watermark.png" in text
    assert X.premiere_audio_problems(xml) == []                        # your audio chain on every audio clip
    assert not (run / "2_captions.srt").exists()                       # no captions


def test_a_missing_raw_is_said_in_plain_words(tmp_path):
    r = subprocess.run([sys.executable, "-m", "match_cuts.just_cuts", "--raw", str(tmp_path / "nope.mp4"),
                        "--run-dir", str(tmp_path / "cuts")], capture_output=True, text=True,
                       cwd=str(Path(JC.__file__).resolve().parents[1]))
    assert r.returncode == 2 and "match_cuts: the RAW is not there" in r.stderr
