"""Night 10: a video project's steps (core.flow) -- the project's edit job and run folder, the template and watermark
on the tool's command line, the edit's result in project.json, the longer video as the RAW, the title check kept in
the project, the steps' errors in plain words, and steps.py on the command line. The download / convert / template
modules are stood in for here (their own tests: test_download, test_convert, test_headline, test_channels)."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import flow, jobs as J, projects as P, run as R  # noqa: E402


def _video(path: Path, seconds: float) -> Path:
    from match_cuts.common import ffmpeg_bin
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=160x284:rate=30:d={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _project(tmp_path: Path, comp_s: float = 1.0, raw_s: float = 3.0) -> P.Project:
    p = P.create(tmp_path / "projects", competitor_url="https://www.youtube.com/shorts/abcdefghijk",
                 raw_url="https://www.youtube.com/watch?v=ABCDEFGHIJK", name="When dad tries to be the tour guide")
    _video(p.folder / "comp [abcdefghijk].mp4", comp_s)
    _video(p.folder / "raw [ABCDEFGHIJK].mp4", raw_s)
    p.update(competitor={"file": "comp [abcdefghijk].mp4"}, raw={"file": "raw [ABCDEFGHIJK].mp4"},
             channel="LouLaughs", title="The fact that dad became the tour guide is hilarious")
    return p


def test_the_command_gets_the_template_and_the_watermark_only_with_a_channel(tmp_path):
    base = R.command(Path("py"), Path("c.mp4"), Path("r.mp4"), tmp_path / "run", tmp_path / "w", True, 100.0)
    assert "--frame" not in base and "--watermark" not in base          # as before night 10: the default frame
    cmd = R.command(Path("py"), Path("c.mp4"), Path("r.mp4"), tmp_path / "run", tmp_path / "w", True, 125.0,
                    tmp_path / "template.png", Path("assets/channels/LouLaughs/watermark.png"))
    assert cmd[:len(base) - 1] == base[:-1] and cmd[cmd.index("--speed") + 1] == "125"
    assert cmd[cmd.index("--frame") + 1] == str(tmp_path / "template.png")
    assert cmd[cmd.index("--watermark") + 1].endswith("watermark.png")


def test_a_project_edit_runs_into_the_project_folder(tmp_path):
    p = _project(tmp_path)
    job = flow.edit_job(p, fast=False, speed=125)
    assert job.competitor.name.startswith("comp") and job.raw.name.startswith("raw")
    assert job.mode == J.THOROUGH and job.speed == 125.0
    assert (job.channel, job.title) == ("LouLaughs", p.data["title"]) and job.project == p.folder
    run = R.JobRun(job, out_dir=tmp_path / "output", frame=tmp_path / "t.png", watermark=tmp_path / "w.png")
    assert run.run_dir == p.folder / "edit"                                # not output\<name>: the project's own
    (p.folder / "edit").mkdir()
    assert R.JobRun(job).run_dir == p.folder / "edit-2"                     # a second edit never overwrites the first
    assert "--frame" in run.cmd and "--watermark" in run.cmd


def test_without_both_files_there_is_no_edit_job(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/abcdefghijk", raw_url="https://youtu.be/ABCDEFGHIJK")
    with pytest.raises(flow.StepError, match="download and convert first"):
        flow.edit_job(p)


def test_the_edit_result_goes_into_project_json(tmp_path):
    p = _project(tmp_path)
    flow.record_edit(p, {"label": "PASS", "run_dir": str(p.folder / "edit"), "seconds": 61.0})
    st = P.load(p.folder).step("edit")
    assert st["state"] == P.DONE and st["result"] == "PASS" and st["run_dir"].endswith("edit")
    flow.record_edit(p, {"label": "FAIL", "failures": ["XML FLASH: 2 frames"], "run_dir": "x"})
    st = P.load(p.folder).step("edit")
    assert st["state"] == P.FAILED and st["error"] == "XML FLASH: 2 frames"
    flow.record_edit(p, {"label": "STOPPED"})
    assert P.load(p.folder).step("edit")["state"] == P.WAITING


def test_a_new_project_needs_both_videos(tmp_path):
    with pytest.raises(flow.StepError, match="competitor's link"):
        flow.new_project("", "https://youtu.be/ABCDEFGHIJK", root=tmp_path)
    with pytest.raises(flow.StepError, match="RAW's link or drop the RAW file"):
        flow.new_project("https://youtu.be/abcdefghijk", root=tmp_path)
    with pytest.raises(flow.StepError, match="not there"):
        flow.new_project("https://youtu.be/abcdefghijk", raw_file=tmp_path / "gone.mp4", root=tmp_path)
    p = flow.new_project("https://youtu.be/abcdefghijk", "https://youtu.be/ABCDEFGHIJK", channel="Flick707",
                         title="x", root=tmp_path, name="Dad's tour 😂")
    assert p.folder.name.endswith("_dads-tour") and p.data["channel"] == "Flick707"


def test_the_longer_video_is_the_raw(tmp_path):
    p = _project(tmp_path, comp_s=3.0, raw_s=1.0)                          # the links pasted the other way round
    p.update(steps={**p.data["steps"], "convert": {**p.step("convert"), "competitor": {"how": "keep"},
                                                   "raw": {"how": "encode"}}})
    assert flow._longer_is_raw(p)                                          # noqa: SLF001
    q = P.load(p.folder)
    assert q.file("raw").name.startswith("comp") and q.file("competitor").name.startswith("raw")
    assert q.step("convert")["raw"]["how"] == "keep" and "swapped" in q.data
    assert not flow._longer_is_raw(q)                                      # noqa: SLF001 - already right


def test_the_title_check_reads_the_headline_once_and_warns_at_80_percent(tmp_path, monkeypatch):
    from mr_app.core import title_check as T
    p = _project(tmp_path)
    calls = []

    def fake_read(path, progress=None, eng=None):
        calls.append(path)
        return T.HeadlineRead("When dad tries to be the tour guide", ["When dad tries to be", "the tour guide"],
                              0.98, 5, 5, (0, 0, 10, 10))
    monkeypatch.setattr(T, "read_headline", fake_read)
    res = flow.title_check_step(p, title="The tour guide is when dad tries to be")
    assert res["competitor_headline"] == "When dad tries to be the tour guide" and res["warn"]
    res2 = flow.title_check_step(p, title="Nobody asked for this tour")
    assert not res2["warn"] and len(calls) == 1                           # read once, kept in project.json
    assert P.load(p.folder).step("title_check")["competitor_headline"].startswith("When dad")


def test_download_and_convert_errors_come_back_in_plain_words(tmp_path, monkeypatch):
    from mr_app.core import download as D
    p = _project(tmp_path)
    monkeypatch.setattr(D, "update_ytdlp", lambda *a, **k: "yt-dlp is up to date")

    def fail(project, progress=None, cancel=None, **k):
        project.set_step("download", P.FAILED, error="RAW: this video is private")
        raise D.DownloadError("this video is private")
    monkeypatch.setattr(D, "download_project", fail)
    with pytest.raises(flow.StepError, match="RAW: this video is private"):
        flow.download_step(p)

    def stopped(project, progress=None, cancel=None, **k):
        raise D.Cancelled()
    monkeypatch.setattr(D, "download_project", stopped)
    with pytest.raises(flow.StepError, match="stopped by you"):
        flow.download_step(p, cancel=threading.Event())


def test_a_job_with_a_channel_makes_its_template_in_the_project(tmp_path, monkeypatch):
    from mr_app.core import channels as Ch
    p = _project(tmp_path)
    wm = tmp_path / "watermark.png"
    wm.write_bytes(b"png")
    ch = SimpleNamespace(name="LouLaughs", watermark=wm)
    monkeypatch.setattr(flow, "find_channel", lambda name: ch if name == "LouLaughs" else None)
    made = []

    def fake_make(channel, title, out, size=(1080, 1920)):
        made.append((channel.name, title, Path(out)))
        Path(out).write_bytes(b"png")
        return SimpleNamespace(layout=SimpleNamespace(lines=["The fact that dad became", "the tour guide is hilarious"]),
                               shrunk=False, scale_pct=100.0, why="", warnings=[])
    monkeypatch.setattr(Ch, "make_template", fake_make)
    job = flow.edit_job(p)
    frame, mark = flow.template_for_job(job)
    assert frame == p.folder / "template.png" and mark == wm and made[0][:2] == ("LouLaughs", job.title)
    st = P.load(p.folder).step("template")
    assert st["state"] == P.DONE and st["lines"][1] == "the tour guide is hilarious" and not st["shrunk"]
    job.channel = ""
    assert flow.template_for_job(job) == (None, None)                    # no channel: the default frame


def test_the_rows_say_the_template_and_warn_before_the_start(monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from mr_app.tools import match_cuts_tool as M
    j = J.Job("v", Path("c.mp4"), Path("r.mp4"))
    assert M.template_line(j).startswith("Template: default") and M.template_problems(j) == []
    j.channel = "LouLaughs"
    assert "NO TITLE" in M.template_line(j) and "no title" in M.template_problems(j)[0]
    j.title, j.headline = "When dad tries to be the tour guide", "When dad tries to be the tour guide 💀😭"
    assert "too close" in M.template_problems(j)[0] and "!!" in M.template_line(j)
    j.title = "Dad became the guide nobody asked for"
    assert M.template_problems(j) == []


def test_learning_from_a_projects_edit_takes_the_projects_name(tmp_path):
    from mr_app.core import learnkit
    p = _project(tmp_path)
    run = p.folder / "edit-2"
    run.mkdir()
    assert learnkit.name_from(None, run) == p.folder.name                 # not "edit"
    assert learnkit.name_from(None, tmp_path / "output" / "video4-2") == "video4"


def test_steps_status_lists_the_projects(tmp_path, monkeypatch, capsys):
    import importlib
    p = _project(tmp_path)
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(p.folder.parent))
    steps = importlib.import_module("steps")
    assert steps.main(["status"]) == 0
    out = capsys.readouterr().out
    assert p.folder.name in out and "download waiting" in out
    assert steps.main(["status", p.folder.name]) == 0                     # a project by its name in the root
    assert steps.main(["edit", str(tmp_path / "nothing")]) == 1           # not a project: said, exit 1
