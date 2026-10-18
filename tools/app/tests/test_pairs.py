"""Night 11, the app's side of "which one is the RAW" and of a wrong pair: dropped videos -> jobs by the tool's rule
(match_cuts.pairing: names win over length), the question "Which one is the RAW?" (DropResult.ask, Ask.jobs), the
WRONG PAIR result word, and a project folder with both files in it for a RAW + a competitor dropped from anywhere
(jobs.project_for_job). Run: python -m pytest tools/app/tests/test_pairs.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import jobs as J, projects as PJ, results as R   # noqa: E402


def _vid(p: Path, data: bytes = b"x" * 10) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _secs(table):
    return lambda p: table.get(Path(p).name)


def _size(table):
    return lambda p: table.get(Path(p).name)


# ---------------------------------------------------------------------------------------------------------------------
# names win over length
# ---------------------------------------------------------------------------------------------------------------------

def test_a_file_named_comp_makes_the_other_one_the_raw_even_when_it_is_shorter(tmp_path):
    a = _vid(tmp_path / "dl" / "comp.mp4")
    b = _vid(tmp_path / "dl" / "my episode.mp4")
    res = J.jobs_from_drop([a, b], _secs({"comp.mp4": 120.0, "my episode.mp4": 100.0}))
    assert [(j.competitor, j.raw) for j in res.jobs] == [(a, b)] and res.ask == []


def test_about_as_long_the_portrait_one_is_the_competitor_and_it_is_said(tmp_path):
    a = _vid(tmp_path / "x" / "short.mp4")
    b = _vid(tmp_path / "x" / "long.mp4")
    res = J.jobs_from_drop([a, b], _secs({"short.mp4": 133.0, "long.mp4": 150.0}),
                           size=_size({"short.mp4": (1080, 1920), "long.mp4": (1920, 1080)}))
    assert [(j.competitor.name, j.raw.name) for j in res.jobs] == [("short.mp4", "long.mp4")]
    assert any("the portrait one (short.mp4) is the competitor" in n for n in res.notes)


def test_undecided_videos_become_a_question_and_the_answer_makes_the_job(tmp_path):
    a = _vid(tmp_path / "Desktop" / "clip one.mp4")
    b = _vid(tmp_path / "Desktop" / "clip two.mp4")
    secs = _secs({"clip one.mp4": 133.0, "clip two.mp4": 161.0})
    res = J.jobs_from_drop([a, b], secs, size=_size({"clip one.mp4": (1920, 1080), "clip two.mp4": (1920, 1080)}))
    assert res.jobs == [] and res.waiting is None and len(res.ask) == 1
    q = res.ask[0]
    assert q.pair == (a, b) and q.videos == [a, b]
    assert q.why == ("can't tell which one is the RAW: clip one.mp4 (2:13) and clip two.mp4 (2:41) are about as long "
                     "and the names don't say -- name the RAW raw.mp4")
    assert res.notes == [q.why]                          # said on the status line too (a window without the card)
    jobs = q.jobs(b, seconds=secs)                       # you clicked "clip two.mp4"
    assert [(j.competitor, j.raw, j.name) for j in jobs] == [(a, b, "clip one")]
    assert (jobs[0].comp_s, jobs[0].raw_s) == (133.0, 161.0)
    with pytest.raises(ValueError):
        q.jobs(tmp_path / "other.mp4")


def test_a_waiting_video_and_the_next_one_can_be_a_question_too(tmp_path):
    a = _vid(tmp_path / "a" / "first.mp4")
    b = _vid(tmp_path / "b" / "second.mp4")
    secs = _secs({"first.mp4": 60.0, "second.mp4": 61.0})
    sz = _size({"first.mp4": (1280, 720), "second.mp4": (1280, 720)})
    one = J.jobs_from_drop([a], secs, size=sz)
    two = J.jobs_from_drop([b], secs, waiting=one.waiting, size=sz)
    assert two.jobs == [] and two.waiting is None and two.ask[0].videos == [a, b]


def test_a_folder_with_two_undecided_videos_asks_with_the_folders_name(tmp_path):
    d = tmp_path / "video7"
    _vid(d / "x.mp4")
    _vid(d / "y.mp4")
    res = J.jobs_from_drop([d], _secs({"x.mp4": 30.0, "y.mp4": 31.0}),
                           size=_size({"x.mp4": (1920, 1080), "y.mp4": (1920, 1080)}))
    assert res.jobs == [] and res.ask[0].name == "video7" and res.ask[0].source == "folder video7"
    assert [j.name for j in res.ask[0].jobs(d / "y.mp4")] == ["video7"]


def test_a_folder_of_folders_asks_for_the_undecided_subfolder_and_runs_the_others(tmp_path):
    import subprocess
    from match_cuts import batch
    from match_cuts.common import ffmpeg_bin

    def clip(p: Path, s: float) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=64x36:rate=10",
                        "-t", str(s), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
    root = tmp_path / "videos"
    clip(root / "video1" / "raw.mp4", 3.0)
    clip(root / "video1" / "comp.mp4", 1.0)
    clip(root / "video2" / "a.mp4", 2.0)
    clip(root / "video2" / "b.mp4", 2.1)
    res = J.jobs_from_drop([root], lambda p: None, find_videos=batch.find_videos)
    assert [(j.name, j.raw.name) for j in res.jobs] == [("video1", "raw.mp4")]
    assert len(res.ask) == 1 and res.ask[0].name == "video2" and [v.name for v in res.ask[0].videos] == ["a.mp4",
                                                                                                         "b.mp4"]


def test_pick_raw_keeps_its_old_answers_for_one_video():
    assert J.pick_raw([], lambda p: None) == (None, [])
    assert J.pick_raw([Path("clip.mp4")], lambda p: None) == (None, [Path("clip.mp4")])
    assert J.pick_raw([Path("raw.mp4")], lambda p: None) == (Path("raw.mp4"), [])


# ---------------------------------------------------------------------------------------------------------------------
# WRONG PAIR
# ---------------------------------------------------------------------------------------------------------------------

STOP = ("WRONG PAIR: This competitor doesn't seem to come from this RAW (sound: 0 of 9 pieces found (0 %); picture: 0 "
        "of 16 frames found (0 %); checked in 7 s). To run it anyway: --force-inputs.")


def test_the_wrong_pair_result_word_is_red_like_picture_not_found(tmp_path):
    assert R.WRONG_PAIR == "WRONG PAIR"
    assert R.label_of(2, STOP, None) == R.WRONG_PAIR
    assert R.label_of(2, "PICTURE NOT FOUND (sound found) -- ...", None) == R.PICTURE_NOT_FOUND     # unchanged
    assert R.WRONG_PAIR in R.RED and R.PICTURE_NOT_FOUND in R.RED and R.WRONG_PAIR not in R.GOOD
    res = R.read_result(tmp_path / "nothing", 2, "", "12:00:00 INFO match_cuts pair check: ...\nmatch_cuts: " + STOP +
                        "\nRun folder: x\n")
    assert res["label"] == R.WRONG_PAIR and res["stop"] == STOP
    assert R.short(res).startswith("This competitor doesn't seem to come from this RAW (sound: 0 of 9")


# ---------------------------------------------------------------------------------------------------------------------
# both files always end up in the project
# ---------------------------------------------------------------------------------------------------------------------

def test_a_dropped_raw_and_competitor_get_a_project_folder_with_both_files(tmp_path):
    comp = _vid(tmp_path / "Downloads" / "funny clip #shorts 😂.mp4", b"c" * 100)
    raw = _vid(tmp_path / "Downloads" / "Movie [1080p] (2001).mp4", b"r" * 1000)
    j = J.Job(J.job_name(comp, raw), comp, raw)
    j.channel, j.title = "LouLaughs", "When it finally works 💀"
    root = tmp_path / "projects"
    folder = J.project_for_job(j, root)
    assert folder.parent == root and folder.name.endswith("_funny-clip-shorts")
    pj = PJ.load(folder)
    c, r = pj.file("competitor"), pj.file("raw")
    assert c.read_bytes() == b"c" * 100 and r.read_bytes() == b"r" * 1000
    assert not os.path.samefile(c, comp) and not os.path.samefile(r, raw)    # copies of their own
    assert pj.data["raw"]["source_file"] == str(raw) and pj.data["competitor"]["source_file"] == str(comp)
    st = pj.step("download")
    assert st["state"] == PJ.DONE and st["dropped"] and st["raw"]["how"] == "copied"
    assert st["raw"]["size"] == 1000 and st["raw"]["mtime_ns"] == r.stat().st_mtime_ns     # to notice a change later
    assert (pj.data["channel"], pj.data["title"]) == ("LouLaughs", "When it finally works 💀")
    # the job now runs into the project: <project>\edit, its files the project's
    assert (j.project, j.out_dir, j.run_name) == (folder, folder, "edit")
    assert (j.competitor, j.raw) == (c, r) and j.source == f"project {folder.name}"
    assert (folder / "logs" / "download_raw.log").is_file()


def test_a_new_file_copied_over_the_original_name_never_changes_the_project(tmp_path):
    """The night-11 bug hunt: a hard link is the very same file, so the next RAW copied over Downloads\\raw.mp4
    (Explorer's Replace, copy /Y: they write into the existing file) changed every earlier project and run with it.
    The project's own copy stays as it was."""
    import shutil
    raw = _vid(tmp_path / "Downloads" / "raw.mp4", b"first video" * 100)
    comp = _vid(tmp_path / "Downloads" / "comp.mp4", b"c")
    linked = J.Job("a", comp, raw)
    J.project_for_job(linked, tmp_path / "linked", link=True)
    copied = J.Job("b", comp, raw)
    J.project_for_job(copied, tmp_path / "copied")
    nxt = _vid(tmp_path / "elsewhere" / "next.mp4", b"the next video")
    shutil.copyfile(nxt, raw)                                  # written into the existing file, as Explorer does
    assert linked.raw.read_bytes() == b"the next video"         # the hard link changed with it ...
    assert copied.raw.read_bytes() == b"first video" * 100      # ... the copy did not


def test_with_link_the_same_drive_gives_hard_links_another_drive_copies(tmp_path, monkeypatch):
    from mr_app.core import download as D
    comp = _vid(tmp_path / "dl" / "clip.mp4", b"c" * 50)
    raw = _vid(tmp_path / "dl" / "raw.mp4", b"r" * 500)
    j = J.Job("clip", comp, raw)
    folder = J.project_for_job(j, tmp_path / "projects", link=True)
    pj = PJ.load(folder)
    assert os.path.samefile(pj.file("raw"), raw) and pj.step("download")["raw"]["how"] == "linked"
    monkeypatch.setattr(D, "cannot_link", lambda src, dest: "another drive (D: and C:)")
    j2 = J.Job("clip", comp, raw)
    folder2 = J.project_for_job(j2, tmp_path / "projects", link=True)
    pj2 = PJ.load(folder2)
    assert folder2 != folder and pj2.file("raw").read_bytes() == b"r" * 500
    assert not os.path.samefile(pj2.file("raw"), raw) and pj2.step("download")["raw"]["how"] == "copied"
    assert "copied, not linked: another drive" in (folder2 / "logs" / "download_raw.log").read_text(encoding="utf-8")


def test_two_files_with_the_same_name_both_end_up_in_the_project(tmp_path):
    comp = _vid(tmp_path / "a" / "video.mp4", b"c")
    raw = _vid(tmp_path / "b" / "video.mp4", b"rr")
    j = J.Job("video", comp, raw)
    folder = J.project_for_job(j, tmp_path / "projects")
    pj = PJ.load(folder)
    assert pj.file("competitor").read_bytes() == b"c" and pj.file("raw").read_bytes() == b"rr"
    assert pj.file("competitor") != pj.file("raw")


def test_videos_already_in_a_project_folder_use_that_project(tmp_path):
    pj = PJ.create(tmp_path / "projects", competitor_url="https://youtu.be/AAAAAAAAAAA", name="a short")
    comp = _vid(pj.folder / "a short [AAAAAAAAAAA].mp4")
    raw = _vid(pj.folder / "Movie.mp4")
    pj.update(competitor={"file": comp.name}, raw={"file": raw.name})
    j = J.Job(pj.folder.name, comp, raw)
    assert J.project_of(j) == pj.folder
    folder = J.project_for_job(j, tmp_path / "other root")
    assert folder == pj.folder and not (tmp_path / "other root").exists()
    assert (j.out_dir, j.run_name, j.competitor, j.raw) == (pj.folder, "edit", comp, raw)
    assert json.loads((pj.folder / "project.json").read_text(encoding="utf-8"))["name"] == pj.folder.name


def test_a_missing_file_is_said_and_nothing_is_made(tmp_path):
    j = J.Job("x", tmp_path / "gone.mp4", _vid(tmp_path / "raw.mp4"))
    with pytest.raises(FileNotFoundError, match="gone.mp4 is not there any more"):
        J.project_for_job(j, tmp_path / "projects")
    assert not (tmp_path / "projects").exists()
