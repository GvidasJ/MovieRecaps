"""19 Nov (your call: "make the second try use another way of making the piece ... and the third another again. Keep
the full reason of each failure in the project"): until now a piece Topaz failed on -- or whose result failed its
check -- went to Topaz again made the very same way, up to 3 times, so it could fail 3 times for one reason (18 Nov,
The Most Jealous Group Leader: "its first frame is at 0.033 s, not at 0", three times), and each new try wiped the
reason of the one before.

* ``enhance.WAYS``: the RAW's own frames + only the played parts -> an encoded piece, whole -> the RAW's own frames,
  whole -> an encoded piece with every frame a keyframe, whole.
* project.json's enhance "tries" + logs/topaz_failed.log: every try, how its piece was made, the full reason.
* A result whose picture does not line up with the edit (the swap's own check) is a failed try too -- before, the
  video simply kept the normal RAW."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from test_topaz_parts_18nov import SPANS, _edit, _shown, movie, topaz  # noqa: E402,F401 - the fixtures too

SHOWN = [("file-enhanced", 600, 719), ("file-enhanced", 720, 779), ("file-enhanced", 3600, 3719)]


def _rec(p) -> dict:
    return PJ.load(p.folder).data["enhance"]


def _fail(p, k: int) -> dict:
    """Topaz's failure file for the piece that is in the line now, then the row's look at it."""
    rec = _rec(p)
    nm = E.names(rec["folder"], rec["name"])
    assert nm.piece.is_file()
    nm.failed.write_text(f"Topaz stopped (code 69): Conversion failed!\n\n(the laptop X, 2026-11-19 10:0{k})\n\n"
                         f"Topaz's last lines:\n[tvai_up @ 01] reason number {k}\nConversion failed!\n",
                         encoding="utf-8")
    return E.watch(p.folder)


def _keyframes(path: Path) -> tuple[int, int]:
    from match_cuts.common import ffprobe_bin
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=flags",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    rows = [x for x in r.stdout.split() if x]
    return sum(1 for x in rows if x.startswith("K")), len(rows)


def test_the_ways_in_their_order_and_a_failure_that_is_not_the_pieces_keeps_the_way():
    assert E.next_way([]) == (E.COPY, True)
    t1 = {"n": 1, "asked": [E.COPY, True], "way": [E.COPY, True], "why": "Topaz stopped (code 69)"}
    assert E.next_way([t1]) == (E.ENCODED, False)
    t2 = {"n": 2, "asked": [E.ENCODED, False], "way": [E.ENCODED, False], "why": "its first frame is at 0.033 s"}
    assert E.next_way([t1, t2]) == (E.COPY, False)
    t3 = {"n": 3, "asked": [E.COPY, False], "way": [E.COPY, False], "why": "x"}
    assert E.next_way([t1, t2, t3]) == (E.INTRA, False)
    # the parts were not worth it the first time (the piece went whole): "the RAW's frames, whole" is not new then
    whole = dict(t1, way=[E.COPY, False])
    assert E.next_way([whole, t2]) == (E.INTRA, False)
    # the laptop gave the parts up and the whole piece failed too: both are tried
    assert E.next_way([dict(t1, also=[E.COPY, False]), t2]) == (E.INTRA, False)
    # a RAW that cannot be cut by copy: asking for a copy again would make the same encoded piece
    assert E.next_way([dict(t1, way=[E.ENCODED, True])]) == (E.ENCODED, False)
    assert E.next_way([dict(t1, way=[E.ENCODED, False])]) == (E.INTRA, False)
    # a try that is still out (no reason yet), and one that failed because the drive was full: the same way again
    assert E.next_way([{"n": 1, "asked": [E.COPY, True], "way": [E.COPY, True]}]) == (E.COPY, True)
    assert E.next_way([dict(t1, same_way=True)]) == (E.COPY, True)
    every = [{"asked": list(w), "way": list(w), "why": "x"} for w in E.WAYS]
    assert E.next_way(every) == E.WAYS[0]                                             # all failed: the first again
    assert "encoded" in E.way_text(E.ENCODED, False) and "whole" in E.way_text(E.ENCODED, False)


def test_each_try_after_a_failure_makes_the_piece_another_way_and_every_reason_is_kept(tmp_path, movie, topaz):
    p = _edit(tmp_path, movie, SPANS)
    r = ES.cover_all(p.folder)
    assert r["clips"] == 3 and not r["way"]                                           # the first piece: nothing to say
    rec = _rec(p)
    assert [(t["n"], t["asked"], t["way"]) for t in rec["tries"]] == [(1, ["copy", True], ["copy", True])]
    assert not rec["piece"].get("encoded") and len(E.valid_ranges(rec["piece"])) == 2

    r = _fail(p, 1)                                                                   # ---- the 2nd try
    assert r["state"] == "progress" and "made another way: an encoded piece, the whole piece" in r["text"], r
    rec = _rec(p)
    assert rec["state"] == "sent" and rec["redos"] == 1 and rec["way"] == "an encoded piece, the whole piece"
    assert rec["piece"]["encoded"] and not rec["piece"].get("intra") and "ranges" not in rec["piece"]
    assert [t["way"] for t in rec["tries"]] == [["copy", True], ["encoded", False]]
    assert "code 69" in rec["tries"][0]["why"] and rec["tries"][0]["failed"] and "why" not in rec["tries"][1]

    r = _fail(p, 2)                                                                   # ---- the 3rd try
    assert "made another way: the RAW's own frames, the whole piece" in r["text"], r
    rec = _rec(p)
    assert not rec["piece"].get("encoded") and "ranges" not in rec["piece"] and rec["redos"] == 2
    assert all(row["ssim"] > 0.999 for row in rec["piece"]["check"])                  # the RAW's own frames again

    r = _fail(p, 3)                                                                   # ---- the 4th try
    assert "made another way: an encoded piece, every frame a keyframe, the whole piece" in r["text"], r
    rec = _rec(p)
    assert rec["piece"]["encoded"] and rec["piece"]["intra"] and rec["redos"] == 3
    keys, frames = _keyframes(E.names(rec["folder"], rec["name"]).piece)
    assert keys == frames == rec["piece"]["frames"]                                   # every frame is a keyframe

    r = _fail(p, 4)                                                                   # ---- and then it stops
    assert r["state"] == "failed" and "code 69" in r["why"], r
    rec = _rec(p)
    assert rec["state"] == "failed" and E.waiting_pieces(Path(rec["folder"])) == []
    assert [t["way"] for t in rec["tries"]] == [["copy", True], ["encoded", False], ["copy", False], ["intra", False]]
    assert all("code 69" in t["why"] for t in rec["tries"])                           # no reason was wiped
    kept = (p.folder / "logs" / E.FAILURE_LOG).read_text(encoding="utf-8")
    for k in (1, 2, 3, 4):                                                            # ... and Topaz's own words
        assert f"reason number {k}" in kept and f"try {k} of " in kept
    assert "(an encoded piece, the whole piece)" in kept and "(the RAW's own frames, only the played parts)" in kept


def test_the_second_and_the_fourth_way_are_enhanced_and_every_clip_shows_its_own_frames(tmp_path, movie, topaz):
    for sub, fails, way in (("a", 1, ["encoded", False]), ("b", 3, ["intra", False])):
        p = _edit(tmp_path / sub, movie, SPANS)
        assert ES.cover_all(p.folder)["clips"] == 3
        for k in range(fails):
            assert _fail(p, k + 1)["state"] == "progress"
        rec = _rec(p)
        assert rec["tries"][-1]["way"] == way
        job = E.Worker(Path(rec["folder"])).once()
        assert job.state == "done", job.why
        w = E.watch(p.folder)
        assert w.get("state") == "used" and w.get("swapped"), w
        rec = _rec(p)
        assert "3 of 3 V1 clips play it" in rec["text"] and "parts" not in rec["text"]
        assert _shown(PJ.load(p.folder), movie) == SHOWN                              # frame-exact, on every clip
        assert len(rec["tries"]) == fails + 1 and "why" not in rec["tries"][-1]       # the tries stay in the project


def test_a_result_that_fails_its_check_goes_again_another_way_with_the_checks_numbers_kept(tmp_path, movie, topaz):
    from match_cuts.common import ffmpeg_bin
    p = _edit(tmp_path, movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = _rec(p)
    nm = E.names(rec["folder"], rec["name"])
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60:d=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(nm.enhanced)], check=True)
    r = E.watch(p.folder)                                                             # 3 s: not the piece, not its parts
    assert r["state"] == "progress" and "made another way: an encoded piece, the whole piece" in r["text"], r
    rec = _rec(p)
    assert rec["piece"]["encoded"] and rec["redos"] == 1
    why = rec["tries"][0]["why"]
    assert "the enhanced piece is not right" in why and "3.000 s long" in why         # the full reason, not a word
    kept = (p.folder / "logs" / E.FAILURE_LOG).read_text(encoding="utf-8")
    assert "3.000 s long" in kept and '"frames": 180' in kept                         # and what the check measured


def test_a_picture_that_does_not_line_up_with_the_edit_is_a_failed_try_too(tmp_path, movie, topaz, monkeypatch):
    p = _edit(tmp_path, movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    before = (p.folder / "edit" / "1_edit.xml").read_bytes()
    assert E.Worker(Path(_rec(p)["folder"])).once().state == "done"
    real = ES.check
    monkeypatch.setattr(ES, "check", lambda *a, **k: {
        "ok": False, "rows": [{"normal_frame": 660, "enhanced_frame": 150, "zncc": 0.12}],
        "why": "the enhanced frame 150 does not show the RAW's frame 660 (ZNCC 0.12)"})
    r = E.watch(p.folder)
    assert r["state"] == "progress" and "does not line up" in r["text"] and "made another way" in r["text"], r
    rec = _rec(p)
    assert rec["state"] == "sent" and rec["redos"] == 1 and rec["piece"]["encoded"] and not rec.get("swap_failed")
    assert "frame 150 does not show the RAW's frame 660" in rec["tries"][0]["why"]
    assert '"zncc": 0.12' in (p.folder / "logs" / E.FAILURE_LOG).read_text(encoding="utf-8")
    assert (p.folder / "edit" / "1_edit.xml").read_bytes() == before                  # the edit was not touched
    monkeypatch.setattr(ES, "check", real)                                            # the next piece is right:
    assert E.Worker(Path(rec["folder"])).once().state == "done"
    w = E.watch(p.folder)
    assert w.get("state") == "used" and w.get("swapped"), w
    assert _shown(PJ.load(p.folder), movie) == SHOWN


def test_a_piece_that_was_removed_goes_again_made_the_same_way(tmp_path, movie, topaz):
    p = _edit(tmp_path, movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = _rec(p)
    E.clear(rec["folder"], rec["name"])                                               # somebody emptied the folder
    r = E.watch(p.folder)
    assert r["state"] == "progress" and "sent again" in r["text"], r
    rec = _rec(p)
    assert rec["tries"][0]["same_way"] and "not in the Enhance folder" in rec["tries"][0]["why"]
    assert rec["tries"][1]["way"] == ["copy", True] and len(E.valid_ranges(rec["piece"])) == 2


def test_what_the_laptop_did_another_way_is_kept_also_when_it_worked_and_when_it_failed(tmp_path, movie, topaz,
                                                                                        monkeypatch):
    p = _edit(tmp_path / "a", movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    monkeypatch.setattr(E, "_part_starts_right", lambda *a, **k: "its first frame shows the piece's frame 361, not 360")
    job = E.Worker(Path(_rec(p)["folder"])).once()
    assert job.state == "done" and "the parts could not be used" in job.note
    assert E.watch(p.folder).get("swapped")
    assert "frame 361, not 360" in _rec(p)["note"]                                    # in project.json ...
    assert "frame 361" in json.loads((p.folder / "enhance" / "enhance.json").read_text(encoding="utf-8"))["worker_note"]

    p = _edit(tmp_path / "b", movie, SPANS)                                           # the whole piece fails after that
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = _rec(p)
    nm = E.names(rec["folder"], rec["name"])
    nm.failed.write_text("Topaz's result is not right: it is 59.940 fps, not 60\n\n(the laptop X)\n\nbefore that: "
                         "the parts could not be used (part 1: x) -- the whole piece instead\n", encoding="utf-8")
    r = E.watch(p.folder)
    assert "made another way: an encoded piece, the whole piece" in r["text"], r
    rec = _rec(p)
    assert rec["tries"][0]["also"] == ["copy", False]                                 # parts AND whole were tried
    assert E.next_way(rec["tries"][:1] + [dict(rec["tries"][1], why="x")]) == (E.INTRA, False)
