"""Night 15, task 13 (day 13's suggestion 2): 3_pauses.txt -- the pauses of 0.4 s or more between two words inside
one clip of 1_edit.xml, with their timecodes; nothing cut."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from match_cuts import pause_list as PL, pipeline


def _xml(tmp_path: Path, clips, timebase=24, ntsc=True) -> Path:
    items = "".join(f"<clipitem><start>{a}</start><end>{b}</end></clipitem>" for a, b in clips)
    x = tmp_path / "1_edit.xml"
    x.write_text(f"<xmeml><sequence><rate><timebase>{timebase}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc>"
                 f"</rate><media><video><track>{items}</track></video></media></sequence></xmeml>", encoding="utf-8")
    return x


def test_only_the_long_pauses_inside_a_clip_are_listed(tmp_path):
    fps, clips = PL.sequence_of(_xml(tmp_path, [(0, 48), (48, 120)]))
    assert fps == Fraction(24000, 1001) and clips == [(0, 48), (48, 120)]
    words = [["late", 2.95, 3.3], ["we", 0.1, 0.4], ["are", 0.45, 0.7], ["here", 1.3, 1.6],   # (any order)
             ["now", 1.75, 1.95], ["so", 2.3, 2.6]]
    found = PL.pauses(words, fps, clips)
    # are -> here: 0.6 s inside clip 1 (listed); now -> so: 0.35 s (short); so -> late at 2.6-2.95 s: 0.35 s;
    # here -> now: 0.15 s; a gap holding the cut at frame 48 (2.0 s) is never listed
    assert [(p["before"], p["after"], p["clip"], p["seconds"]) for p in found] == [("are", "here", 1, 0.6)]
    assert found[0]["tc"] == "00:00:00:17"


def test_a_gap_with_a_cut_in_it_is_the_cuts_business(tmp_path):
    fps, clips = PL.sequence_of(_xml(tmp_path, [(0, 48), (48, 120)]))
    assert PL.pauses([["a", 1.0, 1.5], ["b", 2.4, 2.6]], fps, clips) == []      # the cut at 2.0 s is in the gap


def test_the_file_says_each_pause_and_nothing_is_cut(tmp_path):
    _xml(tmp_path, [(0, 240)])
    res = PL.write(tmp_path, [["one", 0.1, 0.4], ["two", 1.4, 1.6]])
    text = Path(res["path"]).read_text(encoding="utf-8")
    assert "nothing was cut" in text and '1. 00:00:00:10 - 00:00:01:10  1.00 s  clip 1:  "one" ... "two"' in text
    assert PL.write(tmp_path, [])["path"] is None and PL.write(tmp_path / "none", [["a", 0, 1]])["path"] is None


def test_the_run_writes_it_at_the_edits_speed(tmp_path):
    _xml(tmp_path, [(0, 240)])
    ctx = SimpleNamespace(captions={"words_final": [["one", 0.125, 0.5], ["two", 1.75, 2.0]]}, finish={},
                          cfg=SimpleNamespace(premiere_speed=1.25, deliver_dir=str(tmp_path)))
    pipeline.write_pause_list(ctx)
    assert ctx.finish["pauses"]["n"] == 1
    assert "1.00 s" in (tmp_path / PL.FILE_NAME).read_text(encoding="utf-8")     # (1.75 - 0.5) / 1.25
