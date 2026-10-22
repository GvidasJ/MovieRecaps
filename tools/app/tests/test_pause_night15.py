"""Night 15, task 6: Pause / Resume -- a paused video goes on later from the step it was at (the same run folder, the
tool's --resume: the finished steps from its caches), also after the app was closed and opened again."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mr_app.core import jobs as J
from mr_app.core import pause as PZ
from mr_app.core import results as R
from mr_app.core import run


def _paused_folder(tmp_path: Path, name: str = "video4") -> Path:
    d = tmp_path / "out" / name
    PZ.mark(d, "S5.2 visual search", "Finding shots")
    return d


def test_a_paused_video_is_kept_on_this_computer_and_comes_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    d = _paused_folder(tmp_path)
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4", mode=J.THOROUGH, speed=125.0, channel="SceneProof",
              title="The fact that", out_dir=tmp_path / "out")
    j.run_dir, j.state, j.take, j.paused_step = d, J.PAUSED, 2, "Finding shots"
    PZ.save([j])
    back = PZ.load()
    assert len(back) == 1
    b = back[0]
    assert (b.name, b.mode, b.speed, b.channel, b.title, b.take) == ("video4", J.THOROUGH, 125.0, "SceneProof",
                                                                     "The fact that", 2)
    assert b.competitor == tmp_path / "c.mp4" and b.run_dir == d and b.state == J.PAUSED
    assert PZ.line(b) == "paused at Finding shots -- Resume goes on from there"
    (d / "1_edit.xml").write_text("<xmeml/>")                    # paused in its last steps: 1_edit.xml there
    assert PZ.can_resume(d) and len(PZ.load()) == 1              # ... and it still goes on (S8 writes it again)
    assert PZ.read_mark(d)["step"] == "S5.2 visual search"
    PZ.mark_path(d).unlink()                                     # the note gone (the run ended / resumed)
    assert not PZ.can_resume(d) and PZ.load() == []


def test_resume_runs_the_tool_into_the_same_folder(tmp_path):
    d = _paused_folder(tmp_path)
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4")
    j.run_dir, j.resume = d, True
    r = run.JobRun(j, out_dir=tmp_path / "out", work_dir=tmp_path / "work", python=Path(sys.executable))
    assert r.run_dir == d and r.resuming and r.cmd[-1] == "--resume"
    assert r.cmd[r.cmd.index("--run-dir") + 1] == str(d)
    j.resume = False                                              # not resumed: its own new folder, as always
    r2 = run.JobRun(j, out_dir=tmp_path / "out", work_dir=tmp_path / "work", python=Path(sys.executable))
    assert r2.run_dir == tmp_path / "out" / "video4-2" and "--resume" not in r2.cmd


def test_pause_stops_the_run_and_leaves_the_note_for_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "command", lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(60)"])
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4")
    r = run.JobRun(j, out_dir=tmp_path / "out", work_dir=tmp_path / "work", python=Path(sys.executable))
    r.start()
    time.sleep(0.5)
    t = time.time()
    r.pause("S5.3 refine", "Placing every frame exactly")
    res = r.wait(every_s=0.2)
    assert time.time() - t < 15
    assert res["label"] == PZ.LABEL and res["paused"] and res["run_dir"] == str(r.run_dir)
    assert PZ.read_mark(r.run_dir)["plain"] == "Placing every frame exactly"


def test_a_run_that_reached_its_end_before_the_pause_is_finished_not_paused(tmp_path, monkeypatch):
    """Exit code 1 is both a finished run with notes and a stopped one: the run's own result line decides."""
    from match_cuts import batch
    code = f"import time; print({batch.RESULT_LINE!r} + ' PASS', flush=True); time.sleep(60)"
    monkeypatch.setattr(run, "command", lambda *a, **k: [sys.executable, "-c", code])
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4")
    r = run.JobRun(j, out_dir=tmp_path / "out", work_dir=tmp_path / "work", python=Path(sys.executable))
    r.start()
    time.sleep(1.5)
    r.pause("S10 report", "Writing the report")
    res = r.wait(every_s=0.2)
    assert res["label"] != PZ.LABEL and not res.get("paused") and not PZ.mark_path(r.run_dir).exists()


def test_the_tool_and_the_app_name_the_note_alike():
    """The tool goes on in a --run-dir that holds a run only when its note is there and --resume is given (the real
    pause and resume: work/t31/t6)."""
    from match_cuts import cli
    assert cli.PAUSED_FILE == PZ.MARK
    assert cli.build_parser().parse_args(["--resume"]).resume is True


def _page(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.tools import match_cuts_tool as M
    from mr_app.ui import motion as Mo
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    toasts = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append(text))
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    return M, app, Mo, ctx, toasts


def test_the_queue_shows_a_paused_video_after_the_app_opened_again_and_resume_runs_it_first(tmp_path, monkeypatch):
    M, app, Mo, ctx, toasts = _page(tmp_path, monkeypatch)
    for f in ("c.mp4", "r.mp4", "c2.mp4", "r2.mp4"):
        (tmp_path / f).write_bytes(b"x")
    d = _paused_folder(tmp_path)
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4", out_dir=tmp_path / "out")
    j.run_dir, j.state, j.paused_step = d, J.PAUSED, "Finding shots"
    PZ.save([j])
    page = M.MatchCutsPage(ctx)                                    # the app opened again
    try:
        assert [x.state for x in page.jobs] == [J.PAUSED]
        p = page.jobs[0]
        row = page.rows[p.id]
        assert row.b_resume.isVisibleTo(row) and not row.b_pause.isVisibleTo(row)
        assert row.step.text() == "paused at Finding shots -- Resume goes on from there"
        other = J.Job("video5", tmp_path / "c2.mp4", tmp_path / "r2.mp4")
        page._added_jobs(J.DropResult([other], None, []))            # noqa: SLF001
        started = []
        monkeypatch.setattr(page, "start", lambda confirm=True: started.append(confirm))
        page.resume_job(p)
        assert p.state == J.READY and p.resume and started == [False]
        assert page.jobs[0] is p                                    # it runs first
        assert json.loads(PZ.list_path().read_text(encoding="utf-8")) == []    # not paused any more
    finally:
        Mo.set_enabled(True)
        page.deleteLater()
        app.processEvents()


def test_pause_on_the_running_row_stops_the_queue_and_keeps_the_video(tmp_path, monkeypatch):
    M, app, Mo, ctx, toasts = _page(tmp_path, monkeypatch)
    for f in ("c.mp4", "r.mp4"):
        (tmp_path / f).write_bytes(b"x")
    page = M.MatchCutsPage(ctx)
    try:
        j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4")
        page._added_jobs(J.DropResult([j], None, []))               # noqa: SLF001
        paused = []
        page.current = SimpleNamespace(job=j, pause=lambda: paused.append(1), wait=lambda ms=0: True)
        page.running = True
        j.state, j.progress_plain = J.RUNNING, "Finding shots"
        row = page.rows[j.id]
        row.refresh()
        assert row.b_pause.isVisibleTo(row) and not row.b_resume.isVisibleTo(row)
        page.pause_job(j)
        assert paused == [1] and row.step.text() == "pausing ..." and not row.b_pause.isVisibleTo(row)
        d = _paused_folder(tmp_path)
        page._done(j.id, {"label": PZ.LABEL, "paused": True, "run_dir": str(d)})   # noqa: SLF001 - the thread's end
        assert j.state == J.PAUSED and j.run_dir == d and not page.running
        assert j.paused_step == "Finding shots" and "Paused — Resume goes on from here" in toasts
        assert [x["name"] for x in json.loads(PZ.list_path().read_text(encoding="utf-8"))] == ["video4"]
        assert J.display_order(page.jobs)[0] is j
        page.remove_job(j)                                         # removed: no longer kept (its folder stays)
        assert json.loads(PZ.list_path().read_text(encoding="utf-8")) == [] and d.is_dir()
    finally:
        Mo.set_enabled(True)
        page.deleteLater()
        app.processEvents()


def test_a_paused_project_edit_waits_in_project_json():
    from mr_app.core import flow
    seen = {}
    p = SimpleNamespace(data={}, set_step=lambda step, state, **kw: seen.update(step=step, state=state, **kw))
    flow.record_edit(p, {"label": PZ.LABEL, "run_dir": "x"})
    from mr_app.core import projects as P
    assert seen["state"] == P.WAITING and seen["result"] == PZ.LABEL and "error" not in seen
    assert R.STOPPED != PZ.LABEL
