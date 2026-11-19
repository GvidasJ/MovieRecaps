"""19 Nov -- the first whole video through the REAL Topaz (The Best Business Idea In Key and Peele (2), 55 clips):
5 clips ended with ONE frame (1/60 s) of the next shot. Premiere shows the normal RAW's frame that is on at a moment;
Topaz's 60 fps picture shows the NEAREST RAW frame, so the last 60 fps frame before a RAW shot change -- always in
the second half of its RAW frame -- showed the shot after it. The fake Topaz of the tests holds every frame like
Premiere does, which is why no test saw it; ``nearest`` below is a fake Topaz that behaves like the real one.

``enhance_swap._fit_ends``: after the swap's own check, every take's first and last enhanced frame is compared with
the normal RAW; a take that ends on the next shot (or starts on the one before) has its source moved by a 60 fps
frame or two. The cuts stay where they are."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from test_topaz_parts_18nov import FPS, SPANS, _edit, _shown, movie, topaz  # noqa: E402,F401 - the fixtures too

SHOWN = [("file-enhanced", 600, 719), ("file-enhanced", 720, 779), ("file-enhanced", 3600, 3719)]


@pytest.fixture
def nearest(monkeypatch, topaz):  # noqa: F811
    """The fake Topaz, but every 60 fps frame shows the NEAREST frame of the piece (as Apollo does at a shot
    change), not the one that is on at that moment."""
    real = E.topaz_command

    def command(tz, src, dst, vram=1.0, scale=2, part=None):
        cmd = real(tz, src, dst, vram, scale, part)
        i = cmd.index("-vf") + 1
        assert f"fps={E.OUT_FPS}" in cmd[i]
        cmd[i] = cmd[i].replace(f"fps={E.OUT_FPS}", f"setpts=PTS-(0.5/{FPS})/TB,fps={E.OUT_FPS}:start_time=0")
        return cmd
    monkeypatch.setattr(E, "topaz_command", command)


def _through_topaz(p) -> dict:
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = PJ.load(p.folder).data["enhance"]
    job = E.Worker(Path(rec["folder"])).once()
    assert job.state == "done", (job.why, job.note)
    w = E.watch(p.folder)
    assert w.get("state") == "used" and w.get("swapped"), w
    return PJ.load(p.folder).data["enhance"]


def test_with_a_topaz_that_shows_the_nearest_frame_every_clip_ended_on_the_next_shot(tmp_path, movie, nearest,  # noqa: F811
                                                                                     monkeypatch):
    monkeypatch.setattr(ES, "END_SHIFTS", (0,))                                       # (as it was until 19 Nov)
    p = _edit(tmp_path, movie, SPANS)
    rec = _through_topaz(p)
    shown = _shown(PJ.load(p.folder), movie)
    assert [x[2] for x in shown] == [720, 780, 3720]                                  # one frame of what comes after
    assert len(rec["swap"]["ends"]["left"]) == 3 and "look at 3 clip end(s)" in rec["text"]   # ... and it says so


def test_no_clip_ends_on_the_next_shot_and_none_starts_on_the_one_before(tmp_path, movie, nearest):  # noqa: F811
    p = _edit(tmp_path, movie, SPANS)
    before = ET.parse(str(p.folder / "edit" / "1_edit.xml")).getroot()
    rec = _through_topaz(p)
    assert _shown(PJ.load(p.folder), movie) == SHOWN                                  # every clip: its own frames only
    ends = rec["swap"]["ends"]
    assert ends["moved"] == 3 and ends["by"] == [-1] and ends["left"] == [] and "look at" not in rec["text"]
    after = ET.parse(str(p.folder / "edit" / "1_edit.xml")).getroot()
    a, b = list(before.iter("clipitem")), list(after.iter("clipitem"))
    assert len(a) == len(b) == 3
    for x, y in zip(a, b):                                                           # the clips are as long as before
        assert int(y.findtext("out")) - int(y.findtext("in")) == int(x.findtext("out")) - int(x.findtext("in"))


def test_a_topaz_that_holds_each_frame_needs_nothing_moved(tmp_path, movie, topaz):  # noqa: F811
    p = _edit(tmp_path, movie, SPANS)
    rec = _through_topaz(p)
    assert _shown(PJ.load(p.folder), movie) == SHOWN
    assert rec["swap"]["ends"] == {"moved": 0, "by": [], "left": []}


def _on_a_timeline(p) -> None:
    """The edit's clips one after the other on the timeline (<start> / <end>), as the tool writes them."""
    xml = p.folder / "edit" / "1_edit.xml"
    tree = ET.parse(str(xml))
    at = 0
    for c in tree.getroot().iter("clipitem"):
        n = int(c.findtext("out")) - int(c.findtext("in"))
        for tag, v in (("start", at), ("end", at + n)):
            e = ET.SubElement(c, tag)
            e.text = str(v)
        at += n
    tree.write(str(xml), encoding="utf-8", xml_declaration=True)


def test_clips_that_play_one_take_move_together_and_stay_one_take(tmp_path, movie, nearest):  # noqa: F811
    p = _edit(tmp_path, movie, SPANS)
    _on_a_timeline(p)                                      # clips 1 and 2 follow each other on the timeline and in the RAW
    rec = _through_topaz(p)
    shown = _shown(PJ.load(p.folder), movie)
    # one take, played through (the two clips are joined, or still meet frame on frame), then the third clip
    assert shown in ([("file-enhanced", 600, 779), ("file-enhanced", 3600, 3719)], SHOWN), shown
    root = ET.parse(str(p.folder / "edit" / "1_edit.xml")).getroot()
    clips = list(root.iter("clipitem"))
    if len(clips) == 3:
        assert clips[0].findtext("out") == clips[1].findtext("in")
    assert rec["swap"]["ends"]["left"] == [] and rec["swap"]["ends"]["moved"] == 3


def test_the_whole_piece_without_parts_is_fitted_too(tmp_path, movie, nearest, monkeypatch):  # noqa: F811
    monkeypatch.setenv(ES.RANGES_ENV, "0")
    p = _edit(tmp_path, movie, SPANS)
    rec = _through_topaz(p)
    assert "parts" not in rec["text"] and _shown(PJ.load(p.folder), movie) == SHOWN
    assert rec["swap"]["ends"]["moved"] == 3
