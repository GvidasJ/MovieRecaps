"""18 Nov (the session on the PC, after the handover): what the first real checks showed.

* The settings file: a value changed in the file while the app ran was written back old (17 Nov) -- ``settings.save``
  now writes only what was changed since the settings were read.
* The 60 fps sequence: since "no piece before the edit" (17 Nov) the edits were made at the RAW's rate again (The
  Smartest Kevin Moment In The Office, 18 Nov) -- a project whose Topaz comes after the edit gets its 60 fps too."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import settings as S  # noqa: E402


def _file(tmp_path, monkeypatch, d: dict) -> Path:
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setenv(S.ENV, str(f))
    return f


def _on_disk(f: Path) -> dict:
    return json.loads(f.read_text(encoding="utf-8"))


def test_a_value_changed_in_the_file_while_the_app_ran_is_not_written_back_old(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, {"enhance_where": "pc", "daily_goal": 6, "newvideo_channel": "DanCuts"})
    at_start = S.load()                                   # the window's settings, read when the app opened
    d = _on_disk(f)                                       # ... then the file changes (by hand, another page, a tool)
    d.update(enhance_where="laptop", daily_goal=9, newvideo_channel="TOMSCUTS", enhance_dir=r"D:\Shared\Enhance")
    f.write_text(json.dumps(d), encoding="utf-8")
    at_start.animations = False                           # Settings -> OK: one switch changed by you
    S.save(at_start)
    now = _on_disk(f)
    assert now["animations"] is False                     # your change is written
    assert now["enhance_where"] == "laptop" and now["daily_goal"] == 9      # the file's newer values stay
    assert now["newvideo_channel"] == "TOMSCUTS" and now["enhance_dir"] == r"D:\Shared\Enhance"
    assert at_start.enhance_where == "laptop" and at_start.daily_goal == 9  # ... and the window knows them now
    assert at_start.extra["newvideo_channel"] == "TOMSCUTS"


def test_what_you_changed_wins_over_the_file_and_a_second_save_keeps_it(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, {"enhance_where": "pc"})
    s = S.load()
    f.write_text(json.dumps({"enhance_where": "laptop"}), encoding="utf-8")
    s.enhance_where = ""                                  # changed here too: yours wins
    S.save(s)
    assert _on_disk(f)["enhance_where"] == ""
    s.daily_goal = 7
    S.save(s)
    assert _on_disk(f)["enhance_where"] == "" and _on_disk(f)["daily_goal"] == 7


def test_a_remembered_choice_and_settings_made_by_hand_are_written_as_before(tmp_path, monkeypatch):
    from mr_app.core import newvideo as NV
    f = _file(tmp_path, monkeypatch, {"daily_goal": 9})
    NV.set_pref("newvideo_channel", "MikeCuts")
    assert _on_disk(f)["newvideo_channel"] == "MikeCuts" and _on_disk(f)["daily_goal"] == 9
    S.save(S.Settings(daily_goal=3))                      # made by hand (not read from the file): written whole
    assert _on_disk(f)["daily_goal"] == 3 and _on_disk(f)["newvideo_channel"] == "MikeCuts"
    assert S.load() == S.Settings(daily_goal=3)


def test_a_wrong_kind_of_value_in_the_file_is_replaced_and_a_missing_file_is_made(tmp_path, monkeypatch):
    f = _file(tmp_path, monkeypatch, {"daily_goal": 9})
    s = S.load()
    f.write_text(json.dumps({"daily_goal": "many", "machine": "toaster"}), encoding="utf-8")
    S.save(s)
    assert _on_disk(f)["daily_goal"] == 9 and _on_disk(f)["machine"] == "auto"
    f.unlink()
    S.save(s)
    assert _on_disk(f)["daily_goal"] == 9


def _job(tmp_path, rec: dict | None):
    p = PJ.create(tmp_path / "projects", name="sixty")
    if rec is not None:
        p.update(enhance=rec)
    return SimpleNamespace(project=p.folder)


def test_topaz_after_the_edit_still_gets_a_60_fps_sequence(tmp_path, monkeypatch):
    monkeypatch.setenv(ES.MATCH_ENV, "1")
    after = {"state": "skipped", "after_edit": True, "why": "Topaz comes after the edit"}
    assert ES.wants_60(_job(tmp_path / "a", after))                       # 17 Nov's flow: Topaz comes after the edit
    assert ES.wants_60(_job(tmp_path / "b", {"state": "sent"}))           # as before
    assert ES.wants_60(_job(tmp_path / "c", {"state": "used"}))
    assert not ES.wants_60(_job(tmp_path / "d", {"state": "skipped", "why": "sending to Topaz is off in Settings"}))
    assert not ES.wants_60(_job(tmp_path / "e", None))                    # no Topaz: the RAW's own rate
    monkeypatch.setenv(ES.MATCH_ENV, "0")
    assert not ES.wants_60(_job(tmp_path / "f", after))


def test_the_edits_command_line_asks_for_60_fps_when_topaz_comes_after(tmp_path, monkeypatch):
    from mr_app.core import jobs as J
    from mr_app.core import run as RN
    monkeypatch.setenv(ES.MATCH_ENV, "1")
    p = PJ.create(tmp_path / "projects", name="sixty")
    p.update(enhance={"state": "skipped", "after_edit": True})
    comp, raw = tmp_path / "c.mp4", tmp_path / "r.mp4"
    comp.write_bytes(b"c")
    raw.write_bytes(b"r")
    j = J.Job("sixty", comp, raw)
    j.project, j.out_dir, j.run_name = p.folder, p.folder, "edit"
    cmd = RN.JobRun(j, work_dir=tmp_path / "work").cmd
    assert cmd[cmd.index("--sequence-fps") + 1] == "60"
    p.update(enhance=None)
    assert "--sequence-fps" not in RN.JobRun(j, work_dir=tmp_path / "work").cmd


# ---------------------------------------------------------------------------------------------------------------------
# "it must not stop": an edit marked FAIL whose 1_edit.xml is there still gets Topaz
# ---------------------------------------------------------------------------------------------------------------------

def _failed_edit(tmp_path, monkeypatch, with_xml: bool = True):
    from mr_app.core import enhance as E
    from test_cover_all_7nov import _movie
    from test_match_normal_7nov import _project
    monkeypatch.setenv(ES.MATCH_ENV, "1")
    monkeypatch.setenv(E.FAKE_ENV, "1")
    p = _project(tmp_path, [(120, 150), (330, 400)])
    run = p.folder / "edit"
    _movie(run / "extras" / "media" / "raw.mp4")
    (p.folder / "enhance" / "raw_enhanced.mp4").unlink()             # (no piece yet: Topaz comes after the edit)
    (p.folder / "enhance" / "enhance.json").unlink()
    if not with_xml:
        (run / "1_edit.xml").unlink()
    enh = tmp_path / "Enhance"
    enh.mkdir()
    p.update(enhance=None)
    p.update(enhance={"state": "skipped", "why": "Topaz comes after the edit", "after_edit": True, "topaz_on": "",
                      "name": E.safe_name(p.folder.name), "folder": str(enh)})
    return PJ.load(p.folder), run, enh


def test_an_edit_marked_fail_with_its_xml_gets_topaz_and_plays_the_enhanced_picture(tmp_path, monkeypatch):
    from mr_app.core import enhance as E
    from mr_app.core import flow
    from mr_app.core import share_zip as Z
    p, run, enh = _failed_edit(tmp_path, monkeypatch)
    flow.record_edit(p, {"label": "FAIL", "run_dir": str(run), "failures": ["a check of 1_edit.xml failed"]})
    d = PJ.load(p.folder).data
    assert d["steps"]["edit"]["state"] == PJ.FAILED                   # it is still a FAIL ...
    assert d["enhance"]["state"] == "sent" and len(E.waiting_pieces(enh)) == 1      # ... but its piece went to Topaz
    assert E.Worker(enh).once().state == "done"
    r = E.watch(p.folder)
    assert r.get("state") == "used" and r.get("swapped"), r
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["swapped"] and "2 of 2 V1 clips play it" in rec["text"]
    assert (run / "extras" / "media" / "raw.mp4").is_file()           # nothing left its folder
    assert not Z.zip_path(p.folder).exists()                          # and a FAIL is not zipped to send


def test_a_failed_edit_without_its_xml_sends_nothing(tmp_path, monkeypatch):
    from mr_app.core import enhance as E
    from mr_app.core import flow
    p, run, enh = _failed_edit(tmp_path, monkeypatch, with_xml=False)
    flow.record_edit(p, {"label": "FAIL", "run_dir": str(run), "failures": ["the tool stopped"]})
    assert PJ.load(p.folder).data["enhance"]["state"] == "skipped" and E.waiting_pieces(enh) == []
    assert not ES.edit_made(p.folder, PJ.load(p.folder).data["steps"]["edit"])


# ---------------------------------------------------------------------------------------------------------------------
# "it must not stop": Topaz's failures say why, are tried again, and survive the Enhance folder going away
# ---------------------------------------------------------------------------------------------------------------------

def test_topaz_failure_names_the_line_with_the_reason_not_ffmpegs_last_words():
    from mr_app.core import enhance as E
    out = "\n".join(["Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'piece.mp4':",
                     "[hevc_nvenc @ 000001] InitializeEncoder failed: invalid param (8)",
                     "Error while filtering: Generic error in an external library",
                     "frame=   12 fps=3.0 q=-0.0 size=       0kB time=00:00:00.20",
                     "Conversion failed!"])
    assert E._plain_topaz_error(out, 69) == \
        "Topaz stopped (code 69): [hevc_nvenc @ 000001] InitializeEncoder failed: invalid param (8)"   # noqa: SLF001
    assert E._plain_topaz_error("Conversion failed!", 69) == "Topaz stopped (code 69): Conversion failed!"  # noqa: SLF001
    assert E._plain_topaz_error("CUDA_ERROR_OUT_OF_MEMORY\nConversion failed!", 1) == \
        "the graphics card ran out of memory"                                                             # noqa: SLF001


def test_a_topaz_failure_nobody_can_name_is_tried_once_more_as_it_was(tmp_path, monkeypatch):
    from mr_app.core import enhance as E
    monkeypatch.setenv(E.FAKE_ENV, "1")
    calls = []

    def once(src, dst, out_frames, tz, progress, cancel, vram, scale=2, part=None):
        calls.append(vram)
        if len(calls) <= fails[0]:
            raise E.EnhanceError("Topaz stopped (code 69): Conversion failed!")
    monkeypatch.setattr(E, "_run_topaz_once", once)
    fails = [1]
    E.run_topaz(tmp_path / "a.mp4", tmp_path / "b.mp4", 100)          # the second try works: no failure
    assert calls == [1.0, 1.0]
    calls.clear()
    fails[0] = 9
    try:
        E.run_topaz(tmp_path / "a.mp4", tmp_path / "b.mp4", 100)
        raise AssertionError("it must fail")
    except E.EnhanceError as e:
        assert "code 69" in str(e)
    assert calls == [1.0, 1.0]                                        # once more, not for ever


def _done_edit(tmp_path, monkeypatch, state: str, why: str, **more):
    from mr_app.core import enhance as E
    from test_cover_all_7nov import _movie
    from test_match_normal_7nov import _project
    monkeypatch.setenv(ES.MATCH_ENV, "1")
    p = _project(tmp_path, [(120, 150), (330, 400)])
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4")
    (p.folder / "enhance" / "raw_enhanced.mp4").unlink()
    (p.folder / "enhance" / "enhance.json").unlink()
    enh = tmp_path / "Enhance"
    enh.mkdir()
    p.set_step("edit", PJ.DONE, result="PASS", run_dir=str(p.folder / "edit"), finished=PJ.now())
    p.update(enhance=None)
    p.update(enhance={"state": state, "why": why, "name": E.safe_name(p.folder.name), "folder": str(enh), **more})
    monkeypatch.setattr(E, "share_dir", lambda *a, **k: enh)
    return PJ.load(p.folder), enh


def test_the_enhance_folder_away_for_a_moment_does_not_fail_the_video(tmp_path, monkeypatch):
    from mr_app.core import enhance as E
    p, enh = _done_edit(tmp_path, monkeypatch, "skipped", "Topaz comes after the edit", after_edit=True)
    assert ES.cover_all(p.folder)["clips"] == 2
    away = tmp_path / "Enhance_away"
    enh.rename(away)                                                  # the share does not answer
    r = E.watch(p.folder)
    assert r["state"] == "progress" and "cannot be reached" in r["text"]
    assert PJ.load(p.folder).data["enhance"]["state"] == "sent"       # still with Topaz: nothing was given up
    away.rename(enh)                                                  # it is back, the piece with it
    assert E.watch(p.folder)["state"] == "progress" and len(E.waiting_pieces(enh)) == 1


def test_topaz_failure_file_is_kept_in_the_project_before_the_enhance_folder_is_cleared(tmp_path, monkeypatch):
    from mr_app.core import enhance as E
    p, enh = _done_edit(tmp_path, monkeypatch, "skipped", "Topaz comes after the edit", after_edit=True)
    assert ES.cover_all(p.folder)["clips"] == 2
    nm = E.names(enh, PJ.load(p.folder).data["enhance"]["name"])
    nm.failed.write_text("Topaz stopped (code 69): Conversion failed!\n\n(the laptop X, 2026-11-17 21:02)\n\n"
                         "Topaz's last lines:\n[tvai_up @ 01] the reason nobody saw\nConversion failed!\n",
                         encoding="utf-8")
    r = E.watch(p.folder)
    assert r["state"] == "progress" and "sent again" in r["text"]     # the piece went again by itself
    kept = (p.folder / "logs" / E.FAILURE_LOG).read_text(encoding="utf-8")
    assert "the reason nobody saw" in kept and "code 69" in kept
    assert not nm.failed.exists()


def test_a_failed_piece_that_could_not_be_sent_again_then_is_sent_by_the_round_later(tmp_path, monkeypatch):
    import contextlib
    from mr_app.core import enhance as E
    p, enh = _done_edit(tmp_path, monkeypatch, "failed", "Topaz could not enhance it: Topaz stopped (code 69)")
    entered = []

    @contextlib.contextmanager
    def busy():
        entered.append(1)
        yield
    out = E.sweep([p.folder.parent], busy=busy)
    assert out == [f"{p.folder.name}: sent to Topaz again"] and entered == [1]
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["state"] == "sent" and rec["redos"] == 1 and len(E.waiting_pieces(enh)) == 1
    entered.clear()
    assert E.sweep([p.folder.parent], busy=busy) == [] and entered == []     # nothing to do: not counted as work


def test_a_piece_tried_three_times_or_skipped_on_purpose_is_not_sent_again(tmp_path, monkeypatch):
    from mr_app.core import enhance as E
    p, enh = _done_edit(tmp_path / "a", monkeypatch, "failed", "Topaz could not enhance it: x", redos=ES.MAX_REDOS)
    assert E.sweep([p.folder.parent]) == [] and E.waiting_pieces(enh) == []
    p, enh = _done_edit(tmp_path / "b", monkeypatch, "skipped", "sending to Topaz is off in Settings")
    assert E.sweep([p.folder.parent]) == [] and E.waiting_pieces(enh) == []


# ---------------------------------------------------------------------------------------------------------------------
# "it must not stop": the edit's background thread always says how it ended
# ---------------------------------------------------------------------------------------------------------------------

def test_an_error_of_the_apps_own_around_the_edit_ends_the_row_as_a_fail_that_says_so(tmp_path, monkeypatch):
    import pytest
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from mr_app.core import jobs as J
    from mr_app.core import settings as S2
    from mr_app.core import trouble
    from mr_app.tools import match_cuts_tool as MC
    monkeypatch.setenv(S2.APPDATA_ENV, str(tmp_path / "appdata"))
    p = PJ.create(tmp_path / "projects", name="oops")
    p.set_step("edit", PJ.RUNNING)
    j = J.Job("oops", tmp_path / "c.mp4", tmp_path / "r.mp4")
    j.project = p.folder
    th = MC.JobThread(j, {}, tmp_path)
    said = []
    th.done.connect(lambda job_id, res: said.append((job_id, res)))

    def boom():
        raise OSError(22, "the share did not answer")
    monkeypatch.setattr(th, "_work", boom)
    th.run()                                                          # (the thread's own function, on this thread)
    assert len(said) == 1 and said[0][0] == j.id
    res = said[0][1]
    assert res["label"] == "FAIL" and "press Try again" in res["failures"][0] and "did not answer" in res["failures"][0]
    ed = PJ.load(p.folder).data["steps"]["edit"]
    assert ed["state"] == PJ.FAILED and "Try again" in ed["error"]    # the project is not "running" for ever
    log = (tmp_path / "appdata" / trouble.FILE).read_text(encoding="utf-8")
    assert "the edit of oops" in log and "OSError" in log             # the reason can be read afterwards


def test_a_thread_that_said_how_it_ended_is_not_said_twice(tmp_path, monkeypatch):
    import pytest
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from mr_app.core import jobs as J
    from mr_app.core import settings as S2
    from mr_app.tools import match_cuts_tool as MC
    monkeypatch.setenv(S2.APPDATA_ENV, str(tmp_path / "appdata"))
    th = MC.JobThread(J.Job("twice", tmp_path / "c.mp4", tmp_path / "r.mp4"), {}, tmp_path)
    said = []
    th.done.connect(lambda job_id, res: said.append(res))

    def work():
        th._say_done(th.job.id, {"label": "PASS"})                    # noqa: SLF001
        raise RuntimeError("after the result was said")
    monkeypatch.setattr(th, "_work", work)
    th.run()
    assert [r["label"] for r in said] == ["PASS"]
