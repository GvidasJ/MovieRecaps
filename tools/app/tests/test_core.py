"""The desktop app's logic (no window): dropped videos -> jobs, the tool's command line, progress, results, learning
from a fix, the shortcut. Run: python -m pytest tools/app/tests"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import jobs as J, learnkit, progress as P, results as R, run, shortcut   # noqa: E402


def _vid(p: Path, size: int = 10) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


def _secs(table):
    return lambda p: table.get(p.name)


# ---------------------------------------------------------------------------------------------------------------------
# dropped videos -> jobs
# ---------------------------------------------------------------------------------------------------------------------

def test_a_raw_and_a_competitor_dropped_together_are_one_job_the_longest_is_the_raw(tmp_path):
    a = _vid(tmp_path / "video4" / "clip.mp4")
    b = _vid(tmp_path / "video4" / "episode.mp4")
    res = J.jobs_from_drop([a, b], _secs({"clip.mp4": 12.0, "episode.mp4": 1281.0}))
    assert len(res.jobs) == 1 and res.waiting is None
    j = res.jobs[0]
    assert (j.competitor, j.raw) == (a, b) and j.name == "video4"          # the folder both are in
    assert (j.comp_s, j.raw_s) == (12.0, 1281.0) and j.problems() == []


def test_a_file_named_raw_is_the_raw_whatever_its_length(tmp_path):
    a = _vid(tmp_path / "raw.mp4")
    b = _vid(tmp_path / "comp.mp4")
    res = J.jobs_from_drop([b, a], _secs({"raw.mp4": 5.0, "comp.mp4": 60.0}))
    j = res.jobs[0]
    assert j.raw == a and j.competitor == b
    assert any("longer than the RAW" in p for p in j.problems())             # warned before the start
    j.swap()                                                                 # one click
    assert j.raw == b and j.competitor == a and j.problems() == []


def test_one_video_waits_for_the_next_one_and_pairs_with_it(tmp_path):
    raw = _vid(tmp_path / "ep" / "episode.mkv")
    comp = _vid(tmp_path / "ep" / "short.mp4")
    first = J.jobs_from_drop([raw], _secs({"episode.mkv": 900.0, "short.mp4": 30.0}))
    assert first.jobs == [] and first.waiting == raw
    second = J.jobs_from_drop([comp], _secs({"episode.mkv": 900.0, "short.mp4": 30.0}), waiting=first.waiting)
    assert second.waiting is None and len(second.jobs) == 1
    assert second.jobs[0].raw == raw and second.jobs[0].competitor == comp


def test_several_pairs_dropped_one_after_another_all_join_the_queue(tmp_path):
    secs = _secs({"raw.mp4": 900.0, "comp.mp4": 30.0})
    queue = []
    for v in ("video1", "video2", "video3"):
        res = J.jobs_from_drop([_vid(tmp_path / v / "raw.mp4"), _vid(tmp_path / v / "comp.mp4")], secs)
        J.name_jobs(res.jobs, [j.name for j in queue])
        queue += res.jobs
    assert [j.name for j in queue] == ["video1", "video2", "video3"]


def test_a_folder_of_video_folders_is_a_batch_one_job_per_subfolder(tmp_path):
    root = tmp_path / "videos"
    for v in ("video1", "video2"):
        _vid(root / v / "raw.mp4")
        _vid(root / v / "comp.mp4")
    _vid(root / "video3" / "raw.mp4")                                        # no competitor: skipped, said why
    from match_cuts import batch
    res = J.jobs_from_drop([root], _secs({}), find_videos=batch.find_videos)
    assert [(j.name, j.competitor.name, j.raw.name) for j in res.jobs] == [("video1", "comp.mp4", "raw.mp4"),
                                                                            ("video2", "comp.mp4", "raw.mp4")]
    assert any("video3" in n for n in res.notes)
    assert all(j.source == "folder videos" for j in res.jobs)


def test_a_folder_with_the_two_videos_in_it_is_one_job(tmp_path):
    d = tmp_path / "video9"
    _vid(d / "raw.mov")
    _vid(d / "comp.mp4")
    res = J.jobs_from_drop([d], _secs({}))
    assert [(j.name, j.competitor.name, j.raw.name) for j in res.jobs] == [("video9", "comp.mp4", "raw.mov")]


def test_the_speed_txt_of_a_videos_folder_is_used(tmp_path):
    d = tmp_path / "laptop7"
    _vid(d / "raw.mp4")
    _vid(d / "comp.mp4")
    (d / "speed.txt").write_text("125", encoding="utf-8")
    from match_cuts import batch
    res = J.jobs_from_drop([d], _secs({}), speed_of=batch.video_speed)
    assert res.jobs[0].speed == 125.0 and res.jobs[0].speed_from == "speed.txt"


def test_names_stay_unique_and_generic_folders_give_the_file_name(tmp_path):
    assert J.unique_name("video4", ["video4", "video4-2"]) == "video4-3"
    a = _vid(tmp_path / "Desktop" / "clipA.mp4")
    b = _vid(tmp_path / "Desktop" / "movie.mp4")
    assert J.job_name(a, b) == "clipA"                                     # "Desktop" is no video's name


def test_not_a_video_is_said_and_ignored(tmp_path):
    t = tmp_path / "notes.txt"
    t.write_text("x")
    res = J.jobs_from_drop([t], _secs({}))
    assert res.jobs == [] and res.notes == ["notes.txt: not a video"]


# ---------------------------------------------------------------------------------------------------------------------
# the command line, progress and the result
# ---------------------------------------------------------------------------------------------------------------------

def test_the_command_line_is_the_batchs():
    cmd = run.command(Path("py.exe"), Path("c.mp4"), Path("r.mp4"), Path("output/video4"), Path("work"), True, 125.0)
    assert cmd == ["py.exe", "-m", "match_cuts", "--competitor", "c.mp4", "--raw", "r.mp4", "--run-dir",
                   str(Path("output/video4")), "--work", "work", "--premiere", "--fast", "--speed", "125"]
    assert "--fast" not in run.command(Path("p"), Path("c"), Path("r"), Path("o"), Path("w"), False, 100.0)


def test_the_tools_progress_lines_give_the_step_and_the_time_left():
    pr = P.parse("S5.2 visual search: 40 % -- about 2 min left; whole run: about 5 min left")
    assert pr.step == "S5.2 visual search" and pr.step_pct == 40.0 and pr.left_s == 300.0
    assert pr.text() == "S5.2 visual search (40 %) -- about 5 min left"
    pr = P.parse("S8 export -- whole run: about 1 h 05 min left")            # batch.latest_progress's count-down
    assert pr.step == "S8 export" and pr.left_s == 3900.0
    assert P.parse("S3 proxies: still running (31 s) | x").step == "S3 proxies"
    assert P.parse("").text() == "starting"
    assert P.parse("S9 verify: 10 % -- under a minute left; whole run: under a minute left").left_s == 45.0
    assert P.share_done(60.0, 60.0) == 0.5 and P.share_done(10.0, None, 100.0) == 0.1
    assert P.share_done(500.0, None, 100.0) == 0.95                         # never full before the end


def test_the_result_words():
    ok = {"pass": True, "failures": [], "notes": []}
    assert R.label_of(1, "", ok) == R.PASS                                  # 1: a recreation criterion (always)
    assert R.label_of(0, "", dict(ok, notes=["a flash at 00:00:14:13"])) == R.OK_NOTE
    assert R.label_of(0, "", {"pass": False, "failures": ["x"], "notes": []}) == R.FAIL
    assert R.label_of(2, "PICTURE NOT FOUND (sound found) -- the RAW may be a different version", None) == \
        R.PICTURE_NOT_FOUND
    assert R.label_of(2, "the competitor's scenes are not in this RAW: only 0 % of its frames", None) == R.NOT_IN_RAW
    assert R.label_of(3, "Traceback ... ValueError", None) == R.FAIL
    assert R.label_of(None, "", None, stopped=True) == R.STOPPED


def test_a_crash_while_closing_keeps_the_finished_edit(tmp_path, monkeypatch):
    """Day 12, darkhumor: the edit was made and checked (PASS printed), then exit 3 while closing -- not a FAIL with
    OpenCV's warning as the reason."""
    from match_cuts import batch
    warn = "[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::Net::Impl::setPreferableTarget Targets ...\n"
    run = tmp_path / "edit"
    (run / "extras").mkdir(parents=True)
    monkeypatch.setattr(batch, "checks_of", lambda d: {"pass": True, "failures": [], "notes": []})
    monkeypatch.setattr(batch, "captions_of", lambda d: {"written": True, "why": "", "doubtful": []})
    res = R.read_result(run, 3, "match_cuts result: PASS\n", warn)
    assert res["label"] == R.OK_NOTE and "finished and checked" in res["notes"][0] and "WARN" not in R.short(res)
    res = R.read_result(run, 3, "", warn)                    # it never printed its result: a crash, in plain words
    assert res["label"] == R.FAIL and "WARN" not in res["failures"][0] and "exit code 3" in res["failures"][0]


def test_a_finished_run_folder_is_read_with_the_batchs_checks(tmp_path):
    res = R.read_result(tmp_path / "nothing", 2, "", "match_cuts: PICTURE NOT FOUND (sound found) -- the RAW ...")
    assert res["label"] == R.PICTURE_NOT_FOUND and res["failures"][0].startswith("the run stopped (exit 2)")
    real = Path(__file__).resolve().parents[3] / "work" / "t24" / "b10_n8" / "out" / "video10"
    if real.is_dir():                                                       # tonight's night-8 run of your video10
        res = R.read_result(real, 1, "", "")
        assert res["label"] == R.PASS and res["captions"] and Path(res["report"]).is_file()


# ---------------------------------------------------------------------------------------------------------------------
# learn from your fix
# ---------------------------------------------------------------------------------------------------------------------

def test_the_name_of_a_fix():
    assert learnkit.name_from(Path("video11_fixed.prproj"), None) == "video11"
    assert learnkit.name_from(Path("video11 fixed2.prproj"), None) == "video11"
    assert learnkit.name_from(Path("Untitled.prproj"), Path("laptop_video12")) == "video12"
    assert learnkit.name_from(None, Path("video4-2")) == "video4"


def test_learn_copies_to_the_names_the_learn_item_reads_and_writes_its_line(tmp_path):
    src = tmp_path / "laptop"
    pr = src / "video11_fixed.prproj"
    pr.parent.mkdir(parents=True)
    pr.write_bytes(b"gz")
    run_dir = src / "video4"
    (run_dir / "extras" / "media").mkdir(parents=True)
    (run_dir / "1_edit.xml").write_text("<xmeml/>")
    (run_dir / "extras" / "media" / "raw_ae.mp4").write_bytes(b"v" * 1000)
    proj, rd, notes = learnkit.sort_drop([pr, run_dir])
    assert (proj, rd, notes) == (pr, run_dir, [])
    pl = learnkit.plan(proj, rd, root=tmp_path / "repo")
    assert pl.name == "video11" and pl.problems() == []
    assert pl.project_to == tmp_path / "repo" / "finished" / "video11" / "video11_fixed.prproj"
    assert pl.run_to == tmp_path / "repo" / "output" / "laptop_video11"
    seen = []
    learnkit.copy(pl, lambda a, b: seen.append((a, b)))
    assert pl.project_to.read_bytes() == b"gz" and (pl.run_to / "extras" / "media" / "raw_ae.mp4").stat().st_size == 1000
    assert seen[-1][0] == seen[-1][1]                                       # all copied
    line = learnkit.learn_line(["video11"])
    assert line == ("LEARN (quick): if finished\\video11\\video11_fixed.prproj and output\\laptop_video11 exist, "
                    "compare them and learn as on nights 7-8 (check each new rule against my earlier fixes first; make "
                    "it a test case). Ignore audio levels, pitch, hand-moved audio, trims at cut points and the ending. "
                    "If they're missing, skip this and say so.")
    two = learnkit.learn_line(["video11", "video12"])
    assert "output\\laptop_video11; finished\\video12\\video12_fixed.prproj" in two and "compare each pair" in two
    assert any("will be replaced" in p for p in learnkit.plan(proj, rd, root=tmp_path / "repo").problems())


def test_a_folder_that_is_not_a_run_is_said(tmp_path):
    d = tmp_path / "random"
    d.mkdir()
    _, rd, notes = learnkit.sort_drop([d])
    assert rd is None and "not a run folder" in notes[0]


# ---------------------------------------------------------------------------------------------------------------------
# the desktop shortcut
# ---------------------------------------------------------------------------------------------------------------------

def test_the_shortcut_script_points_at_the_app_with_its_icon():
    s = shortcut.script(Path(r"C:\x\.venv\Scripts\pythonw.exe"), Path(r"C:\x\tools\app\MovieRecaps.pyw"),
                        Path(r"C:\x\tools\app\mr_app\assets\icon.ico"), Path(r"C:\x"))
    assert "GetFolderPath('Desktop')" in s and "'MovieRecaps.lnk'" in s
    assert r"$s.TargetPath = 'C:\x\.venv\Scripts\pythonw.exe'" in s
    assert r"""$s.Arguments = '"C:\x\tools\app\MovieRecaps.pyw"'""" in s
    assert r"$s.IconLocation = 'C:\x\tools\app\mr_app\assets\icon.ico,0'" in s
    assert shortcut._q("it's") == "'it''s'"


@pytest.mark.parametrize("name", ["icon.ico", "icon.png", "fonts/OpenSans-Regular.ttf", "fonts/OFL.txt"])
def test_the_apps_files_are_there(name):
    from mr_app.core import paths
    assert (paths.ASSETS / name).is_file()


def test_a_job_runs_hidden_and_stop_kills_it_and_everything_it_started(tmp_path, monkeypatch):
    """The runner with a stand-in for the tool (a Python that starts a child and sleeps): its progress is polled,
    Stop ends it and its child (taskkill /T), the result says STOPPED and the console is kept."""
    import subprocess
    import time
    child = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); " \
            "open(sys.argv[1],'w').write(str(p.pid)); print('started', flush=True); time.sleep(60)"
    pidfile = tmp_path / "child.pid"
    monkeypatch.setattr(run, "command", lambda *a, **k: [sys.executable, "-c", child, str(pidfile)])
    job = J.Job("video4", tmp_path / "comp.mp4", tmp_path / "raw.mp4")
    r = run.JobRun(job, out_dir=tmp_path / "out", work_dir=tmp_path / "work", python=Path(sys.executable))
    assert r.run_dir == tmp_path / "out" / "video4"
    r.start()
    polls = []
    for _ in range(100):
        if pidfile.is_file() and pidfile.read_text():
            break
        time.sleep(0.1)
    r.stop()
    res = r.wait(poll=lambda rr: polls.append(rr.elapsed()), every_s=0.2)
    assert res["label"] == R.STOPPED and res["exit"] is not None
    kid = int(pidfile.read_text())
    out = subprocess.run(["tasklist", "/FI", f"PID eq {kid}"], capture_output=True, text=True).stdout
    assert str(kid) not in out                                               # the child is gone too
    assert "started" in (tmp_path / "out" / "video4.batch.log").read_text(encoding="utf-8")


def test_premiere_gets_a_copy_named_after_the_run(tmp_path):
    """Premiere names the new project after the XML: video10_edit, not one more 1_edit."""
    from mr_app.core import premiere
    run_dir = tmp_path / "video10"
    run_dir.mkdir()
    xml = run_dir / "1_edit.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    got = premiere.named_copy(xml)
    assert got == run_dir / "video10_edit.xml" and got.read_text(encoding="utf-8") == "<xmeml/>"
    assert premiere.find() is None or premiere.find().name == "Adobe Premiere Pro.exe"
