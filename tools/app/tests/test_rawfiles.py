"""Day 12, one RAW file per run -- the Board's Clean up for older runs that hold a 5.1 RAW twice (raw_ae.mp4 / raw.mp4
next to raw_stereo.mp4, which 1_edit.xml plays): the stereo file is checked (the same frames and length, stereo sound
starting with the picture), nothing may play the old copy (the XMLs, the run's own Premiere project, your fixed
project -- only read, never changed), the space is shown, and the old copy goes only after you said yes. Learn still
finds the RAW in such a project by its name (learnkit.project_raw).

Run: python -m pytest tools/app/tests/test_rawfiles.py"""
from __future__ import annotations

import gzip
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from match_cuts import stereo  # noqa: E402
from match_cuts.common import ffmpeg_bin  # noqa: E402

from mr_app.core import learnkit as K  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import rawfiles as RF  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")
from test_board_ui import pump_until, qapp  # noqa: E402,F401 - the Board page's test helpers (qapp: a fixture)


def _video(path: Path, channels: int = 6, seconds: float = 2.0) -> Path:
    pan = "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0" if channels == 6 else "pan=stereo|FL=c0|FR=c0"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-filter_complex", f"[1:a]{pan}[a]",
                    "-map", "0:v", "-map", "[a]", "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def media_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ae = _video(d / "raw_ae.mp4")
    one = Path(stereo.stereo_copy(ae, d / "raw_stereo.mp4")["path"])
    return SimpleNamespace(ae=ae, one=one)


def _url(p: Path) -> str:
    return "file://localhost/" + urllib.parse.quote(str(p).replace("\\", "/"))


def old_run(root: Path, media_files, name: str = "clip one", link: bool = False) -> SimpleNamespace:
    """A project with a finished run made before day 12: both RAW files, 1_edit.xml playing raw_stereo.mp4, the run's
    own Premiere project and your fixed project (both playing raw_stereo.mp4)."""
    folder = PJ.create(root, name=name).folder
    edit = folder / "edit"
    media = edit / "extras" / "media"
    media.mkdir(parents=True)
    (edit / "extras" / "report.md").write_text("# report", encoding="utf-8")
    old = media / "raw_ae.mp4"
    if link:
        os.link(media_files.ae, old)
    else:
        old.write_bytes(media_files.ae.read_bytes())
    one = media / "raw_stereo.mp4"
    one.write_bytes(media_files.one.read_bytes())
    xml = edit / "1_edit.xml"
    xml.write_text(f"<xmeml><file id='f1'><pathurl>{_url(one)}</pathurl></file></xmeml>", encoding="utf-8")
    own = edit / f"{folder.name}_edit.prproj"
    own.write_bytes(gzip.compress(f"<PremiereData><Media><ActualMediaFilePath>{one}</ActualMediaFilePath></Media>"
                                  "</PremiereData>".encode()))
    fixed = folder / f"{folder.name}_fixed.prproj"
    fixed.write_bytes(gzip.compress(f"<PremiereData><Media><FilePath>{one}</FilePath></Media></PremiereData>"
                                    .encode()))
    return SimpleNamespace(folder=folder, edit=edit, media=media, old=old, one=one, xml=xml, own=own, fixed=fixed)


def test_an_old_run_is_checked_then_its_old_copy_goes_only_when_asked(tmp_path, media_files):
    r = old_run(tmp_path, media_files)
    fixed_bytes, fixed_time = r.fixed.read_bytes(), os.stat(r.fixed).st_mtime_ns
    said: list = []
    items = RF.plan([r.folder], say=said.append)
    assert len(items) == 1
    it = items[0]
    assert it.ok, it.problems
    assert it.old == [r.old] and it.single == r.one and it.freed == it.size == r.old.stat().st_size
    assert said and all("One RAW file per run (1 of 1)" in s for s in said)
    assert "raw_ae.mp4" in it.line() and "raw_stereo.mp4" in it.line()
    assert r.old.is_file()                                    # the plan never deletes: only after you said yes
    freed, errors = RF.remove(items)
    assert errors == [] and freed == it.size
    assert not r.old.exists() and r.one.is_file() and r.xml.is_file() and r.own.is_file()
    assert r.fixed.read_bytes() == fixed_bytes and os.stat(r.fixed).st_mtime_ns == fixed_time   # never touched
    assert RF.plan([r.folder]) == []                          # one RAW file now: nothing to do


def test_a_hard_linked_old_copy_frees_nothing(tmp_path, media_files):
    r = old_run(tmp_path, media_files, link=True)
    it = RF.plan([r.folder])[0]
    assert it.ok and it.freed == 0 and it.size > 0 and "frees no space" in it.line()
    freed, errors = RF.remove([it])
    assert freed == 0 and errors == [] and not r.old.exists() and media_files.ae.is_file()


def test_your_project_playing_the_old_copy_keeps_both_and_is_never_changed(tmp_path, media_files):
    r = old_run(tmp_path, media_files)
    r.fixed.write_bytes(gzip.compress(f"<PremiereData><Media><FilePath>{r.old}</FilePath></Media></PremiereData>"
                                      .encode()))
    before = r.fixed.read_bytes()
    it = RF.plan([r.folder])[0]
    assert not it.ok and any("your project" in p and "raw_ae.mp4" in p for p in it.problems)
    assert RF.remove([it]) == (0, [])                         # not ok: nothing removed
    assert r.old.is_file() and r.fixed.read_bytes() == before


def test_the_runs_own_premiere_project_or_an_xml_playing_the_old_copy_keeps_both(tmp_path, media_files):
    r = old_run(tmp_path, media_files)
    r.own.write_bytes(gzip.compress(f"<x><ActualMediaFilePath>{r.old}</ActualMediaFilePath></x>".encode()))
    it = RF.plan([r.folder])[0]
    assert not it.ok and any("its Premiere project" in p for p in it.problems)
    r2 = old_run(tmp_path, media_files, name="clip two")
    (r2.edit / "extras" / "edit_59.94fps.xml").write_text(f"<xmeml><pathurl>{_url(r2.old)}</pathurl></xmeml>",
                                                           encoding="utf-8")
    it2 = RF.plan([r2.folder])[0]
    assert not it2.ok and any("edit_59.94fps.xml plays raw_ae.mp4" in p for p in it2.problems)


def test_a_stereo_file_that_is_not_the_same_video_keeps_both(tmp_path, media_files):
    r = old_run(tmp_path, media_files)
    _video(r.one, channels=2, seconds=1.0)
    it = RF.plan([r.folder])[0]
    assert not it.ok and any("not the same video" in p for p in it.problems) and r.old.is_file()


def test_remove_checks_again_first(tmp_path, media_files):
    r = old_run(tmp_path, media_files)
    items = RF.plan([r.folder])
    assert items[0].ok
    r.fixed.write_bytes(gzip.compress(f"<x><FilePath>{r.old}</FilePath></x>".encode()))   # you opened it meanwhile
    freed, errors = RF.remove(items)
    assert freed == 0 and errors and "your project" in errors[0] and r.old.is_file()


def test_remove_keeps_the_files_when_work_on_the_project_started_after_the_yes(tmp_path, media_files):
    from mr_app.core import locks
    r = old_run(tmp_path, media_files)
    items = RF.plan([r.folder])
    assert items[0].ok
    lock = locks.WorkLock(r.folder, "edit")                   # an edit started on it meanwhile
    lock.acquire()
    try:
        freed, errors = RF.remove(items)
    finally:
        lock.release()
    assert freed == 0 and errors and "working on this project" in errors[0] and r.old.is_file()


def test_a_stereo_raw_run_or_an_unfinished_one_is_not_offered(tmp_path, media_files):
    r = old_run(tmp_path, media_files)
    r.one.unlink()                                             # a stereo RAW: raw_ae / raw.mp4 is its one file
    assert RF.plan([r.folder]) == []
    r2 = old_run(tmp_path, media_files, name="clip two")
    (r2.edit / "extras" / "report.md").unlink()                # not finished
    assert RF.plan([r2.folder]) == []


def test_learn_finds_the_raw_a_project_plays_as_raw_stereo_by_its_name():
    raw = {"file": "media/raw_ae.mp4", "file_abs": "C:\\x\\edit\\extras\\media\\raw_ae.mp4", "width": 1920,
           "height": 1080, "fps": "24000/1001", "duration_s": 120.12}
    PR, _L = K.tool()
    m = "D:\\Shared\\Projects\\p\\edit\\extras\\media\\raw_stereo.mp4"
    seq = SimpleNamespace(items=[SimpleNamespace(media=m, enabled=True, kind=PR.VIDEO, start=0.0, end=10.0)])
    pr = SimpleNamespace(sequences=[seq], media={m: {"width": 1920, "height": 1080, "fps": 23.976,
                                                     "duration": 120.12}})
    got = K.project_raw(pr, raw)
    assert got is not None and got[0] == m and got[2] is True              # found by its name


# ---------------------------------------------------------------------------------------------------------------------
# the Board's Clean up button, on the real widgets
# ---------------------------------------------------------------------------------------------------------------------

def test_board_clean_up_offers_the_old_copy_with_the_space_saved_and_removes_it_only_on_yes(qapp, tmp_path,
                                                                                            monkeypatch, media_files):
    from PySide6 import QtWidgets

    from mr_app.core import publish
    from mr_app.tools import board_tool as BT
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    r = old_run(local, media_files)
    size = r.old.stat().st_size
    toasts: list = []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    page = BT.BoardPage(ctx)
    page._up_timer.stop()                                       # noqa: SLF001 - no Shorts check in tests
    page.check_uploads = lambda force=False: None
    page.show()
    try:
        assert pump_until(qapp, lambda: len(page.cards) == 1)
        progress: list = []
        page.cleanup_progress.connect(progress.append)
        asked: list = []
        answer = [QtWidgets.QMessageBox.StandardButton.No]
        monkeypatch.setattr(BT.QMessageBox, "question",
                            lambda _w, _t, text, *a, **k: (asked.append(text), answer[0])[1])
        page.clean_up()
        assert pump_until(qapp, lambda: asked and page.b_clean.isEnabled())
        text = asked[0]
        assert "One RAW file per run" in text and "raw_ae.mp4" in text and "raw_stereo.mp4" in text
        assert f"Space saved: {publish.plain_size(size)}" in text
        assert any("One RAW file per run (1 of 1)" in p for p in progress)       # progress while it checked
        assert r.old.is_file()                                                     # No: nothing removed
        answer[0] = QtWidgets.QMessageBox.StandardButton.Yes
        page.clean_up()
        assert pump_until(qapp, lambda: not r.old.exists())
        assert r.one.is_file() and r.xml.is_file() and r.fixed.is_file() and r.own.is_file()
        assert pump_until(qapp, lambda: any("Freed" in t for t, _k in toasts))
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
