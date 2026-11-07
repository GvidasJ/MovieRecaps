"""7 Nov (your call): faster videos.

* ONE VIDEO AT A TIME (Settings -> Speed, on by itself): a video added while another is being made waits until that one
  is finished -- its steps, its piece with Topaz, its edit -- and while this computer runs another edit or Topaz.
  A video that waits for YOU (a button, the headline, which is the RAW), paused, stopped or failed never holds the
  others.
* CHECK VIDEOS (off by itself): the edit runs with --skip-preview --skip-compare (every check still runs).
* The PC makes each new project right in its own "Save projects to" folder (D:\\Shared\\Projects); the laptop (a
  network folder) on its own disk, as before.
* The zip leaves out a big video the edit does not play (the downloaded RAW, now in the PC's project folder too)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mr_app.core import jobs as J
from mr_app.core import paths, run, settings as S, share_zip as Z


# -- the settings --
def test_the_two_new_settings_and_their_defaults():
    s = S.Settings()
    assert s.one_at_a_time is True and s.check_videos is False
    back = S.Settings.from_dict({"one_at_a_time": False, "check_videos": True})
    assert back.one_at_a_time is False and back.check_videos is True
    bad = S.Settings.from_dict({"one_at_a_time": "yes", "check_videos": 1})
    assert bad.one_at_a_time is True and bad.check_videos is False
    assert S.Settings.from_dict(back.to_dict()) == back


# -- where a new project is made --
def test_a_network_folder_or_a_relative_one_is_not_on_this_computer():
    assert not S.on_this_computer("\\\\192.168.101.11\\Shared\\Projects")
    assert not S.on_this_computer("//192.168.101.11/Shared/Projects")
    assert not S.on_this_computer("")
    assert not S.on_this_computer("Projects")


@pytest.mark.skipif(os.name != "nt", reason="drive types are Windows' own")
def test_windows_own_drive_is_on_this_computer():
    assert S.on_this_computer((os.environ.get("SystemDrive") or "C:") + "\\")


def test_the_pc_makes_the_project_in_its_save_folder(tmp_path, monkeypatch):
    save = tmp_path / "Shared" / "Projects"
    s = S.Settings(save_to=str(save))
    monkeypatch.setenv(paths.PROJECTS_ENV, str(tmp_path / "mine"))
    assert S.work_root(s) == tmp_path / "mine"                     # a folder set by hand (the tests) wins
    monkeypatch.delenv(paths.PROJECTS_ENV)
    monkeypatch.setattr(S, "on_this_computer", lambda _f: True)
    assert S.work_root(s) == save and save.is_dir()                # the PC: right in D:\Shared\Projects
    monkeypatch.setattr(S, "on_this_computer", lambda _f: False)
    assert S.work_root(s) == paths.REPO / "input" / "downloads"   # the laptop: its own folder, as before


def test_a_save_folder_that_cannot_be_made_keeps_this_computers_folder(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.delenv(paths.PROJECTS_ENV, raising=False)
    monkeypatch.setattr(S, "on_this_computer", lambda _f: True)
    assert S.work_root(S.Settings(save_to=str(blocker / "Projects"))) == paths.REPO / "input" / "downloads"


# -- the check videos --
def _job_run(tmp_path, resume=False):
    from mr_app.core import pause as PZ
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4")
    if resume:
        d = tmp_path / "out" / "video4"
        PZ.mark(d, "S5.2 visual search", "Finding shots")
        j.run_dir, j.resume = d, True
    return run.JobRun(j, out_dir=tmp_path / "out", work_dir=tmp_path / "work", python=Path(sys.executable))


def test_no_check_videos_by_default_and_resume_stays_last(tmp_path, monkeypatch):
    monkeypatch.setenv(run.CHECK_VIDEOS_ENV, "")
    monkeypatch.setattr(S, "load", lambda: S.Settings())
    r = _job_run(tmp_path, resume=True)
    assert "--skip-preview" in r.cmd and "--skip-compare" in r.cmd and r.cmd[-1] == "--resume"
    monkeypatch.setattr(S, "load", lambda: S.Settings(check_videos=True))
    assert "--skip-preview" not in _job_run(tmp_path).cmd
    monkeypatch.setenv(run.CHECK_VIDEOS_ENV, "0")
    assert "--skip-compare" in _job_run(tmp_path).cmd
    monkeypatch.setenv(run.CHECK_VIDEOS_ENV, "1")
    assert "--skip-compare" not in _job_run(tmp_path).cmd


# -- the zip --
def _xml(path: Path, media: list[Path]) -> None:
    clips = "".join(f"<clipitem><file><pathurl>file://localhost/{m.as_posix().lstrip('/')}</pathurl></file></clipitem>"
                    for m in media)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<xmeml><sequence><media><video><track>{clips}</track></video></media></sequence></xmeml>",
                    encoding="utf-8")


def _big(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(Z.BIG_BYTES + 1024)
    return path


def test_the_zip_leaves_out_the_downloaded_raw_the_edit_does_not_play(tmp_path):
    p = tmp_path / "2026-11-07_The Worst Messenger"
    media = _big(p / "edit" / "extras" / "media" / "raw.mp4")
    spare = _big(p / "edit" / "extras" / "media" / "old_piece.mp4")      # in the edit folder: always in
    down = _big(p / "The Office S03E02.mp4")                              # the download: not played
    played = _big(p / "Played Here.mp4")                                  # played by its name: in
    small = p / "competitor.mp4"
    small.write_bytes(b"0" * 1000)                                       # small: in
    _xml(p / "edit" / "1_edit.xml", [media, played])
    put, left = Z.members(p, p / "edit")
    names = {f.name for f, _a in put}
    assert {"raw.mp4", "old_piece.mp4", "Played Here.mp4", "competitor.mp4", "1_edit.xml"} <= names
    assert "The Office S03E02.mp4" not in names
    assert any(f == down and "does not play" in why for f, why in left)
    assert spare.exists() and down.exists()                               # nothing is ever deleted


def test_without_a_readable_edit_nothing_is_left_out(tmp_path):
    p = tmp_path / "proj"
    _big(p / "Show.mp4")
    (p / "edit").mkdir(parents=True)
    (p / "edit" / "1_edit.xml").write_text("not xml", encoding="utf-8")
    put, _left = Z.members(p, p / "edit")
    assert "Show.mp4" in {f.name for f, _a in put}
