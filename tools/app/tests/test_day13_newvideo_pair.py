"""Day 13, 1.1: "Are We There Yet" made in Progress -> New video stopped with WRONG PAIR, the same two files dropped
straight into Match cuts ran. This case on its real files (tools/match_cuts/tests/data/are_we_there_yet: two minutes of
the movie around the scene with its 5.1 sound, the competitor as VP9 + Opus .webm): both paths give the tool the same
two videos in the same roles, both pass the tool's quick pair check, the run's result says what it read, and a
WRONG PAIR names copies that are not your files. (The sticky part -- a cached WRONG PAIR answer stopping every Try
again at once -- is fixed in the tool: tools/match_cuts/tests/test_day13_pairs.py.)"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from mr_app.core import flow, inputs as IN, jobs as J, projects as P
from mr_app.core.run import JobRun

DATA = Path(__file__).resolve().parents[2] / "match_cuts" / "tests" / "data" / "are_we_there_yet"
WEBM, MOVIE = DATA / "competitor.webm", DATA / "movie_piece.mp4"


@pytest.fixture()
def files(tmp_path):
    """Your two files where you keep them (a Downloads folder and an earlier project's folder)."""
    dl = tmp_path / "Downloads"
    other = tmp_path / "input" / "downloads" / "2026-10-19_never-say-never"
    dl.mkdir(parents=True)
    other.mkdir(parents=True)
    comp = dl / "Funny scene from Are We There Yet (2005) - Movies Verse (1080p, vp9).webm"
    raw = other / "Are We There Yet 2005 1080p ATVP STZ WEB-DL DD 5 1 H 264-PiRaTeS.mp4"
    shutil.copy2(WEBM, comp)
    shutil.copy2(MOVIE, raw)
    return comp, raw


def _new_video(root: Path, raw_field: Path, comp_field: Path) -> J.Job:
    """The New video form's steps (download_tool's StageThread + send_to_edit): the project, the download (your
    files copied in), the conversion, your RAW field kept as your choice, the edit's job."""
    p = flow.new_project("", raw_file=str(raw_field), competitor_file=str(comp_field), root=root,
                         name=Path(comp_field).stem)
    flow.download_step(p)
    flow.convert_step(P.load(p.folder))
    p = P.load(p.folder)
    flow.set_roles(p.folder, (p.data.get("raw") or {}).get("file"), "form")
    return flow.fill_lengths(flow.edit_job(P.load(p.folder), True, 100.0))


def _drop(root: Path, comp: Path, raw: Path) -> J.Job:
    """The same two files dropped on Match cuts: a project of their own (night 11), the job pointing at it."""
    job = J.Job("drop", comp, raw, source="drop")
    J.project_for_job(job, root)
    return job


def _cmd_files(job: J.Job, tmp_path: Path) -> tuple[Path, Path, list[str]]:
    run = JobRun(job, work_dir=tmp_path / "work")
    cmd = run.cmd
    return Path(cmd[cmd.index("--competitor") + 1]), Path(cmd[cmd.index("--raw") + 1]), cmd


def test_new_video_and_the_drop_give_the_tool_the_same_pair_and_both_pass(files, tmp_path):
    from match_cuts import pair_check as PC
    from match_cuts.cli import quick_probe
    comp, raw = files
    nv_c, nv_r, nv_cmd = _cmd_files(_new_video(tmp_path / "projects", raw, comp), tmp_path)
    dr_c, dr_r, dr_cmd = _cmd_files(_drop(tmp_path / "projects", comp, raw), tmp_path)
    # the roles: the movie is the RAW on both paths, the Short the competitor (New video: converted to H.264 .mp4)
    assert nv_c.suffix == ".mp4" and dr_c.suffix == ".webm" and nv_r.stat().st_size == dr_r.stat().st_size \
        == raw.stat().st_size
    assert abs(float(quick_probe(nv_c)["duration"]) - float(quick_probe(dr_c)["duration"])) < 0.1
    assert "--no-swap" in nv_cmd and "--force-inputs" not in nv_cmd      # your RAW field: never swapped back
    for c, r in ((nv_c, nv_r), (dr_c, dr_r)):
        res = PC.check(c, r, None)
        assert not res.wrong and res.sound.state == PC.FOUND, res.summary()


def test_the_raw_field_holding_the_short_still_runs_the_movie_as_the_raw(files, tmp_path):
    """Both files dropped on the form at once land in the order Windows gives them: the Short can be in the RAW
    field. The conversion's rule (the longer video is the RAW) puts them right before your field is kept."""
    comp, raw = files
    c, r, _cmd = _cmd_files(_new_video(tmp_path / "projects", comp, raw), tmp_path)
    assert r.name == raw.name and c.suffix == ".mp4" and "Funny scene" in c.name


def test_the_result_keeps_what_the_run_read_and_names_copies_that_are_not_your_files(files, tmp_path):
    comp, raw = files
    job = _new_video(tmp_path / "projects", raw, comp)
    got = IN.given(job, Path(job.competitor), Path(job.raw))
    assert got["raw"]["size"] == raw.stat().st_size and got["raw"]["sound"][0]["channels"] == 6
    assert got["competitor"]["video"] == "h264" and got["competitor"]["made"]["converted"] == "encode"
    assert got["raw"]["made"]["from"] == str(raw) and got["raw"]["made"]["how"] in ("copied", "linked", None)
    line = IN.text(got)
    assert line.startswith("competitor: Funny scene") and "RAW: Are We There Yet" in line and "aac 6 ch" in line
    assert IN.copies_hint(job.project) == ""                         # the copies are your files
    proj_raw = Path(job.raw)                                          # a RAW copy cut short (a copy that broke off)
    data = proj_raw.read_bytes()
    os.remove(proj_raw)
    proj_raw.write_bytes(data[: len(data) // 2])
    hint = IN.copies_hint(job.project)
    assert hint.startswith("The project's copies are not your files: the RAW in the project folder") and \
        "not copied whole" in hint and "Try again" in hint


def test_record_edit_keeps_the_inputs(files, tmp_path):
    comp, raw = files
    job = _new_video(tmp_path / "projects", raw, comp)
    p = P.load(job.project)
    res = {"label": "WRONG PAIR", "failures": ["WRONG PAIR: ..."], "run_dir": str(p.folder / "edit"),
           "inputs": IN.given(job, Path(job.competitor), Path(job.raw))}
    flow.record_edit(p, res)
    ed = P.load(job.project).step("edit")
    assert ed["result"] == "WRONG PAIR" and ed["inputs"]["raw"]["name"] == raw.name
