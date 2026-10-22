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
    # are -> here: 0.6 s inside clip 1; now -> so 0.35 s holds the cut at frame 48 (2.0 s): around the cut;
    # so -> late 0.35 s inside clip 2; here -> now: 0.15 s (short)
    assert [(p["before"], p["after"], p["clip"], p["at_cut"], p["seconds"]) for p in found] == [
        ("are", "here", 1, False, 0.6), ("now", "so", 1, True, 0.35), ("so", "late", 2, False, 0.35)]
    assert found[0]["tc"] == "00:00:00:17"
    assert PL.MIN_PAUSE_S == 0.25                     # (task 16: 8 of your 10 Liquid Death cuts; 0.4 s: 5)


def test_a_word_timed_into_the_pause_is_trimmed_to_its_sound():
    import numpy as np
    sr = PL.SR
    y = np.zeros(3 * sr, np.float32)
    for a, b in ((0.2, 0.6), (1.2, 1.6)):                          # "hello" sounds 0.2-0.6 s, "there" 1.2-1.6 s
        i0, i1 = int(round(a * sr)), int(round(b * sr))
        y[i0:i1] = 0.5 * np.sin(2 * np.pi * 220 * np.arange(i1 - i0) / sr).astype(np.float32)
    y += 0.001 * np.random.default_rng(1).standard_normal(len(y)).astype(np.float32)
    words = [["hello", 0.2, 1.1], ["there", 1.15, 1.6]]             # the recogniser timed "hello" into the pause
    assert PL.pauses(words, Fraction(24), [(0, 72)]) == []          # by the timings: no pause
    found = PL.pauses(PL.audible(words, y), Fraction(24), [(0, 72)])
    assert len(found) == 1 and 0.5 <= found[0]["seconds"] <= 0.65   # by the sound: the 0.6 s pause


def test_a_pause_around_a_cut_is_listed_as_at_the_cut(tmp_path):
    """Task 16: most of your Liquid Death pause cuts are trims at the tool's cuts -- listed, said so."""
    fps, clips = PL.sequence_of(_xml(tmp_path, [(0, 48), (48, 120)]))
    found = PL.pauses([["a", 1.0, 1.5], ["b", 2.4, 2.6]], fps, clips)          # the cut at 2.0 s is in the gap
    assert len(found) == 1 and found[0]["at_cut"] and (found[0]["clip"], found[0]["next_clip"]) == (1, 2)
    assert "at the cut, clips 1-2" in PL.text(found)


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
