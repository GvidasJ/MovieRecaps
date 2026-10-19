"""Day 12, the Learn tab's new shared files (core, no window): the problems list (problems_queue.md: written by the
queue's hook for FAIL / OK with a note, never for wrong input; its format, dedupe, Skip (my mistake), fixed / not
fixed marks; the key log lines; tonight's prompt), the waiting cards (learn_waiting.json: added when an edit finishes,
amber once the project is rendered, Skip, gone once learned, a fix saved into the project found) and the suggestions
(suggestions.md: I want this -> requests.md, No -> removed). Everything in tmp folders -- never the real hub.

Run: python -m pytest tools/app/tests/test_learn_day12.py"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import board as B  # noqa: E402
from mr_app.core import learn_hub as H  # noqa: E402
from mr_app.core import problems as PR  # noqa: E402
from mr_app.core import results as R  # noqa: E402
from mr_app.core import suggestions as SG  # noqa: E402
from mr_app.core import waiting as WT  # noqa: E402


@pytest.fixture(autouse=True)
def _own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))


def a_run(root: Path, name: str = "2026-10-19_video-by-x", log: str = "") -> Path:
    run = root / name / "edit"
    (run / "extras").mkdir(parents=True)
    (run / "extras" / "batch_console.log").write_text(log or "match_cuts result: FAIL\n", encoding="utf-8")
    return run


LOG = """C:\\venv\\python.exe -m match_cuts --competitor c.mp4 --raw r.mp4

match_cuts result: FAIL
  c2 frame-exact cuts            FAIL   38 cuts: 11 verified both sides, 9 exceptions, 18 failed
  c4 speed / framing             PASS   39 raw segments
02:38:43 INFO segment: 39 scenes
02:38:43 WARNING premiere export validation: 2 problem(s): XML PERSON S06+S07 at 00:00:07:41: the person speaking is not in the window
C:\\x\\align.py:124: UserWarning: The given NumPy array is not writable
[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::Net::Impl::setPreferableTarget Targets are not supported
02:44:57 INFO verify s9_1_coverage: pass (0.0s)
02:44:57 WARNING verification: c5_audio: S07: audio lag -93.67 ms
02:44:58 WARNING verification: c5_audio: S07: audio lag -93.67 ms
"""


def res_of(run: Path, label: str, **kw) -> dict:
    d = {"label": label, "failures": [], "notes": [], "stop": "", "run_dir": str(run),
         "log": str(run / "extras" / "batch_console.log"), "exit": 1}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------------------------------------------------
# problems_queue.md
# ---------------------------------------------------------------------------------------------------------------------

def test_a_fail_and_a_note_are_written_with_video_run_date_computer_check_and_key_log_lines(tmp_path):
    hub = tmp_path / "Projects"
    run = a_run(tmp_path / "local", log=LOG)
    note = ("every file is there, but a check of 1_edit.xml found something to look at in Premiere: ['XML PERSON "
            "S06+S07 at 00:00:07:41: the person speaking is not in the window']")
    assert PR.record(hub, "2026-10-19_video-by-x", res_of(run, R.OK_NOTE, notes=[note], exit=1),
                     computer="laptop") == ""
    text = (hub / PR.FILE).read_text(encoding="utf-8")
    assert text.startswith("# Problems to fix")
    assert "- [ ] **2026-10-19_video-by-x** · OK with a note: XML PERSON -- 1_edit.xml check:" in text
    assert "  - status: open" in text and "  - computer: laptop" in text
    assert "  - run: ..\\local\\2026-10-19_video-by-x\\edit" in text            # next to the hub: relative
    assert "  - date: 20" in text and "  - result: OK with a note" in text
    assert "    > match_cuts result: FAIL" in text
    assert "XML PERSON S06+S07 at 00:00:07:41" in text
    assert "UserWarning" not in text and "WARN:0" not in text           # library warnings are never the reason
    assert text.count("audio lag -93.67 ms") == 1                        # the same message at another time: once
    got = PR.read(hub)
    assert len(got) == 1 and got[0].name == "2026-10-19_video-by-x" and got[0].computer == "laptop"
    assert got[0].run == str(run) and got[0].is_open and got[0].lines[0] == "match_cuts result: FAIL"


@pytest.mark.parametrize("label, kw", [
    (R.WRONG_PAIR, {"stop": "WRONG PAIR: This competitor doesn't seem to come from this RAW", "exit": 2}),
    (R.NOT_IN_RAW, {"stop": "the competitor's scenes are not in this RAW", "exit": 2}),
    (R.FAIL, {"stop": "The inputs look wrong: the competitor is almost as long as the RAW", "exit": 2}),
    (R.FAIL, {"stop": "raw file not found: C:\\x.mp4", "exit": 2}),
    (R.FAIL, {"failures": ["the laptop is working on this project since 02:44 (edit)"], "exit": None}),
    (R.STOPPED, {}),
    (R.PASS, {"exit": 0}),
])
def test_wrong_input_a_stop_by_you_and_a_pass_are_never_written(tmp_path, label, kw):
    hub = tmp_path / "Projects"
    run = a_run(tmp_path / "local")
    assert PR.record(hub, "v", res_of(run, label, **kw)) == ""
    assert not (hub / PR.FILE).exists()


def test_a_crash_with_an_exit_code_says_stopped_and_keeps_the_real_lines_not_the_opencv_warning(tmp_path):
    hub = tmp_path / "Projects"
    log = ("match_cuts result: PASS (criterion 6 not verified)\n"
           "02:59:46 WARNING captions: 7 caption(s) start more than 2 frames from their first word\n"
           "[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::Net::Impl::setPreferableTarget\n")
    run = a_run(tmp_path / "local", log=log)
    stop = "[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::Net::Impl::setPreferableTarget"
    assert PR.is_tool_problem(res_of(run, R.FAIL, stop=stop, exit=3, failures=[f"the run stopped (exit 3): {stop}"]))
    p = PR.from_result("v", res_of(run, R.FAIL, stop=stop, exit=3, failures=[f"the run stopped (exit 3): {stop}"]))
    assert p.check.startswith("stopped (exit 3)") and p.exit == "3"
    assert p.lines == ["match_cuts result: PASS (criterion 6 not verified)",
                       "02:59:46 WARNING captions: 7 caption(s) start more than 2 frames from their first word"]


def test_a_traceback_gives_its_last_line(tmp_path):
    log = ("02:00:00 INFO start\nTraceback (most recent call last):\n  File \"x.py\", line 3, in <module>\n"
           "    boom()\nZeroDivisionError: division by zero\n")
    assert "ZeroDivisionError: division by zero" in PR.key_lines(log)
    assert not any(ln.startswith("File ") for ln in PR.key_lines(log))
    assert len(PR.key_lines("\n".join(f"02:00:{i:02d} WARNING thing {i}" for i in range(40)), limit=5)) == 5


def test_the_same_run_again_replaces_its_entry_a_new_run_adds_one_skipped_and_fixed_stay_closed(tmp_path):
    hub = tmp_path / "Projects"
    run = a_run(tmp_path / "local")
    res = res_of(run, R.FAIL, failures=["coverage: fail -- 2 gaps"])
    assert PR.record(hub, "v", res) == "" and PR.record(hub, "v", res) == ""
    assert len(PR.read(hub, include_closed=True)) == 1                   # dedupe: the same run and check
    t1 = (hub / PR.FILE).stat().st_mtime_ns
    run2 = a_run(tmp_path / "local", "other")
    assert PR.record(hub, "other", res_of(run2, R.FAIL, failures=["x"])) == ""
    items = PR.read(hub)
    assert [p.name for p in items] == ["v", "other"]
    assert PR.skip(hub, items[0].key()) == ""
    assert PR.fixed(hub, items[1].key(), "2026-10-20") == ""
    allp = PR.read(hub, include_closed=True)
    assert [p.state for p in allp] == [PR.SKIPPED, PR.FIXED] and PR.read(hub) == []
    text = (hub / PR.FILE).read_text(encoding="utf-8")
    assert "- [x] **v**" in text and "  - status: skipped (my mistake) 20" in text
    assert "- [x] **other**" in text and "  - status: fixed 2026-10-20" in text
    t2 = (hub / PR.FILE).stat().st_mtime_ns
    assert PR.record(hub, "v", res) == ""                                 # skipped: it does not come back
    assert (hub / PR.FILE).stat().st_mtime_ns == t2 and t2 >= t1          # (nothing written)
    assert PR.read(hub) == []


def test_not_fixed_keeps_it_open_with_its_reason_and_a_hand_written_entry_is_read(tmp_path):
    hub = tmp_path / "Projects"
    hub.mkdir()
    (hub / PR.FILE).write_text(PR.HEAD + "- [ ] **video-by-y** · FAIL: a flash at 00:00:03\n"
                               "  - run: ..\\problems\\video-by-y\\edit\n  - computer: PC\n  - note: by hand\n"
                               "  - key log lines:\n    > 03:00:00 WARNING flash\n", encoding="utf-8")
    got = PR.read(hub)
    assert len(got) == 1 and got[0].check == "FAIL: a flash at 00:00:03" and got[0].state == PR.OPEN
    assert got[0].run == str(tmp_path / "problems" / "video-by-y" / "edit")     # ..\ resolved against the hub
    assert got[0].lines == ["03:00:00 WARNING flash"] and got[0].extra == {"note": "by hand"}
    assert PR.not_fixed(hub, got[0].key(), "the flash comes from the RAW itself") == ""
    got = PR.read(hub)
    assert got[0].state == PR.NOT_FIXED and got[0].is_open
    text = (hub / PR.FILE).read_text(encoding="utf-8")
    assert "- [ ] **video-by-y**" in text and "  - status: not fixed — the flash comes from the RAW itself" in text
    assert "  - run: ..\\problems\\video-by-y\\edit" in text and "  - note: by hand" in text


def test_open_problems_go_into_tonights_prompt_with_learn_queue_and_wishes(tmp_path):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    hub = tmp_path / "Projects"
    run = a_run(tmp_path / "local", log=LOG)
    assert PR.record(hub, "video-by-x", res_of(run, R.FAIL, failures=["coverage: fail"]), computer="laptop") == ""
    assert H.add_wish(hub, "A wish", computer="PC") == ""
    assert H.queue_add(hub, H.QueueItem("video16", prproj=r"C:\p.prproj", run=r"C:\run")) == ""
    p = H.tonight_prompt(hub, repo)
    assert "Learn from my fixes" in p and "Problems to fix (from problems_queue.md)" in p and "My wishes" in p
    assert p.index("Learn from my fixes") < p.index("Problems to fix") < p.index("My wishes")
    assert "**video-by-x** — FAIL -- coverage: fail" in p and f"`{run}`" in p
    assert "> match_cuts result: FAIL" in p and "not fixed — <reason>" in p
    assert "each problem from problems_queue.md" in p
    PR.skip(hub, PR.read(hub)[0].key())
    assert "Problems to fix" not in H.tonight_prompt(hub, repo)


def test_day12_script_writes_the_two_problems_once(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import add_problem
    hub = tmp_path / "Projects"
    assert add_problem.main(["day12", "--hub", str(hub)]) == 0
    assert add_problem.main(["day12", "--hub", str(hub)]) == 0
    got = PR.read(hub)
    assert [p.name for p in got] == ["2026-10-19_video-by-mindculpt", "2026-10-19_video-by-darkhumor"]
    assert all(p.computer == "laptop" and p.copy.startswith("D:\\Shared\\problems\\") for p in got)
    assert "XML PERSON" in got[0].check and "S06–S34" in got[0].check and "exit 3" in got[1].check
    assert got[1].run == r"C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-19_video-by-darkhumor\edit"
    assert add_problem.main(["day12", "--hub", str(tmp_path / "dry"), "--dry-run"]) == 0
    assert not (tmp_path / "dry").exists()


def test_open_log_finds_the_log_in_the_run_the_hub_or_the_copy(tmp_path):
    hub = tmp_path / "Projects"
    gone = tmp_path / "laptop" / "2026-10-19_v" / "edit"
    p = PR.Problem("v", run=str(gone), log=str(gone / "extras" / "batch_console.log"))
    assert PR.log_to_open(p, hub) == ""
    moved = a_run(hub, "2026-10-19_v")                                  # published to the hub
    assert PR.log_to_open(p, hub) == str(moved / "extras" / "batch_console.log")
    copy = a_run(tmp_path / "problems", "2026-10-19_v")
    p2 = PR.Problem("v", run=str(gone), copy=str(copy))
    assert PR.log_to_open(p2) == str(copy / "extras" / "batch_console.log")


# ---------------------------------------------------------------------------------------------------------------------
# learn_waiting.json
# ---------------------------------------------------------------------------------------------------------------------

def a_project(root: Path, name: str, channel: str = "Flick707") -> Path:
    f = root / name
    (f / "edit" / "extras").mkdir(parents=True)
    (f / "project.json").write_text(json.dumps({"version": 1, "name": name, "channel": channel,
                                                "created": "2026-10-19T01:00:00+03:00"}), encoding="utf-8")
    return f


def a_job(project: Path | None, run: Path, name: str = "job") -> SimpleNamespace:
    return SimpleNamespace(project=project, name=name, run_dir=run, raw=Path(r"C:\x\Rush.Hour.mp4"),
                           competitor=Path(r"C:\x\Video by mindculpt.mp4"), channel="Flick707", title="Hi")


def test_a_finished_edit_waits_with_run_raw_competitor_channel_and_the_next_free_name(tmp_path):
    hub, repo = tmp_path / "Projects", tmp_path / "repo"
    (repo / "finished" / "video15").mkdir(parents=True)
    hub.mkdir()
    p1 = a_project(hub, "2026-10-19_a")
    p2 = a_project(hub, "2026-10-19_b")
    assert WT.record(hub, a_job(p1, p1 / "edit"), {"label": R.PASS, "run_dir": str(p1 / "edit")}) == ""
    time.sleep(0.01)
    assert WT.record(hub, a_job(p2, p2 / "edit"), {"label": R.OK_NOTE, "run_dir": str(p2 / "edit")}) == ""
    for bad in (R.FAIL, R.WRONG_PAIR, R.STOPPED):                          # only PASS / OK with a note wait
        assert WT.record(hub, a_job(p1, p1 / "edit2"), {"label": bad, "run_dir": str(p1 / "edit2")}) == ""
    data = json.loads((hub / WT.FILE).read_text(encoding="utf-8"))
    assert [d["name"] for d in data["items"]] == ["2026-10-19_a", "2026-10-19_b"]
    assert data["items"][0]["project"] == ".\\2026-10-19_a" and data["items"][0]["run"] == ".\\2026-10-19_a\\edit"
    assert data["items"][0]["raw"] == "Rush.Hour.mp4" and data["items"][0]["competitor"] == "Video by mindculpt.mp4"
    assert data["items"][0]["channel"] == "Flick707" and data["items"][0]["run_in_project"] == "edit"
    cards = WT.cards(hub, repo)
    assert len(cards) == 2
    by = {c.entry.name: c for c in cards}
    assert by["2026-10-19_a"].name == "video16" and by["2026-10-19_b"].name == "video17"   # the oldest: the lowest
    assert by["2026-10-19_a"].run == p1 / "edit" and not by["2026-10-19_a"].rendered


def test_newest_first_rendered_turns_amber_skip_and_learned_take_it_away(tmp_path):
    hub, repo = tmp_path / "Projects", tmp_path / "repo"
    hub.mkdir()
    p1, p2 = a_project(hub, "p1"), a_project(hub, "p2")
    WT.add(hub, WT.Entry("p1", project=str(p1), run=str(p1 / "edit"), finished="2026-10-19 01:00"))
    WT.add(hub, WT.Entry("p2", project=str(p2), run=str(p2 / "edit"), finished="2026-10-19 02:00"))
    assert [c.entry.name for c in WT.cards(hub, repo)] == ["p2", "p1"]
    B.move(p1, "rendered")
    cards = {c.entry.name: c for c in WT.cards(hub, repo)}
    assert cards["p1"].rendered and not cards["p2"].rendered
    assert WT.skip(hub, cards["p2"].id) == ""
    assert [c.entry.name for c in WT.cards(hub, repo)] == ["p1"]
    WT.add(hub, WT.Entry("p2", project=str(p2), run=str(p2 / "edit")))   # the skipped run never comes back
    assert [c.entry.name for c in WT.cards(hub, repo)] == ["p1"]
    assert WT.learned(hub, run=str(p1 / "edit")) == ""
    assert WT.cards(hub, repo) == []
    assert {e.state for e in WT.read(hub, include_closed=True)} == {WT.SKIPPED, WT.LEARNED}


def test_a_project_moved_to_the_hub_is_found_there_and_a_queued_run_is_left_out(tmp_path):
    hub, local, repo = tmp_path / "Projects", tmp_path / "local", tmp_path / "repo"
    hub.mkdir()
    lp = a_project(local, "2026-10-19_c")
    WT.record(hub, a_job(lp, lp / "edit"), {"label": R.PASS, "run_dir": str(lp / "edit")})
    import shutil
    shutil.move(str(lp), str(hub / "2026-10-19_c"))                       # published
    c = WT.cards(hub, repo)[0]
    assert c.project == hub / "2026-10-19_c" and c.run == hub / "2026-10-19_c" / "edit"
    H.queue_add(hub, H.QueueItem("video16", prproj=str(hub / "x.prproj"), run=str(hub / "2026-10-19_c" / "edit")))
    assert WT.cards(hub, repo) == []


def test_a_newer_run_of_the_same_project_replaces_its_card_and_nothing_changed_writes_nothing(tmp_path):
    hub = tmp_path / "Projects"
    hub.mkdir()
    p = a_project(hub, "p")
    e = WT.Entry("p", project=str(p), run=str(p / "edit"), finished="2026-10-19 01:00")
    WT.add(hub, e)
    t = (hub / WT.FILE).stat().st_mtime_ns
    time.sleep(0.02)
    WT.add(hub, e)
    assert (hub / WT.FILE).stat().st_mtime_ns == t
    WT.add(hub, WT.Entry("p", project=str(p), run=str(p / "edit-2"), finished="2026-10-19 02:00"))
    got = WT.read(hub)
    assert len(got) == 1 and got[0].run == str(p / "edit-2")


def test_a_prproj_saved_into_a_waiting_project_is_found_once(tmp_path):
    hub = tmp_path / "Projects"
    hub.mkdir()
    p = a_project(hub, "p")
    WT.add(hub, WT.Entry("p", project=str(p), run=str(p / "edit"), finished="2026-10-19 01:00"))
    state = tmp_path / "seen.json"
    now = time.time()
    fix = p / "my fix.prproj"
    fix.write_bytes(b"x")
    own = p / "edit" / "p_edit.prproj"                                    # the app's own project, just made
    own.write_bytes(b"x")
    old = now - 60
    os.utime(fix, (old, old))
    os.utime(own, (old, old))
    cards = WT.cards(hub, tmp_path / "repo")
    found = WT.find_fixes(cards, state)
    assert [f for _c, f in found] == [fix] and cards[0].fix == fix
    from mr_app.core import learnkit
    learnkit.mark_seen(state, fix)
    assert WT.find_fixes(WT.cards(hub, tmp_path / "repo"), state) == []
    fresh = p / "fixed.prproj"
    fresh.write_bytes(b"x")                                               # still being saved: not yet
    assert WT.find_fixes(WT.cards(hub, tmp_path / "repo"), state) == []
    assert WT.find_fixes(WT.cards(hub, tmp_path / "repo"), state, now=time.time() + 30)[0][1] == fresh


# ---------------------------------------------------------------------------------------------------------------------
# suggestions.md
# ---------------------------------------------------------------------------------------------------------------------

SUGS = [SG.Suggestion("Re-frame onto the speaker by itself", "saves about 3 minutes per video", "half a night",
                      "day 12 (2026-10-19)"),
        SG.Suggestion("Warn before a download fills the disk", "no failed nights", "small", "day 12 (2026-10-19)")]


def test_suggestions_format_newest_run_first_want_goes_to_requests_and_no_removes(tmp_path):
    hub = tmp_path / "Projects"
    assert SG.add(hub, SUGS) == "" and SG.add(hub, SUGS) == ""            # not twice
    assert SG.add(hub, [SG.Suggestion("Older idea", "x", "small", "night 11 (2026-10-18)")]) == ""
    text = (hub / SG.FILE).read_text(encoding="utf-8")
    assert "- **Re-frame onto the speaker by itself**\n  - why: saves about 3 minutes per video\n" \
           "  - size: half a night\n  - from: day 12 (2026-10-19)\n" in text
    got = SG.read(hub)
    assert [s.title for s in got] == [SUGS[0].title, SUGS[1].title, "Older idea"]
    assert [s.title for s in SG.newest_first(got)] == [SUGS[0].title, SUGS[1].title, "Older idea"]
    assert SG.want(hub, got[0], computer="PC") == ""
    wishes = H.read_wishes(hub)
    assert any(w.text.startswith("Re-frame onto the speaker by itself — saves about 3 minutes") and not w.done
               for w in wishes)
    assert [s.title for s in SG.read(hub)] == [SUGS[1].title, "Older idea"]
    assert SG.no(hub, SG.read(hub)[0]) == ""
    assert [s.title for s in SG.read(hub)] == ["Older idea"]
    assert "A wish" not in H.tonight_prompt(hub, tmp_path)
    assert "Re-frame onto the speaker by itself" in H.tonight_prompt(hub, tmp_path)   # wanted: in the prompt


def test_a_hand_written_suggestion_is_read(tmp_path):
    hub = tmp_path / "Projects"
    hub.mkdir()
    (hub / SG.FILE).write_text("# Suggestions\n\n- Faster convert\n  - size: small\n"
                               "- **Join split words** — fewer caption fixes\n", encoding="utf-8")
    got = SG.read(hub)
    assert [(s.title, s.size, s.why) for s in got] == [("Faster convert", "small", ""),
                                                       ("Join split words", "", "fewer caption fixes")]
