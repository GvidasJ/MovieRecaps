"""21 Nov (your job 4): "No Topaz" for one video. Until now, moving a video's pieces out of the Enhance folder made the
app send a new piece by itself (a piece "gone" counted as a failed try). Now the row's button takes its piece out of
the line, stops a Topaz working on it (either computer: the stop file), marks the project "off" so nothing sends it
again (the rows, the sweep, a new run of the row), and lets the video finish with its zip. "Topaz again" undoes it."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402


@pytest.fixture
def zips(monkeypatch):
    made: list = []
    monkeypatch.setattr(ES, "zip_when_done", lambda project: made.append(Path(project)))
    return made


def _sent(tmp_path: Path) -> tuple[PJ.Project, E.Names]:
    """A project whose piece is with Topaz: the piece, its json, a part made, Topaz's kept blocks."""
    p = PJ.create(tmp_path / "projects", name="no topaz")
    enh = tmp_path / "Enhance"
    enh.mkdir()
    nm = E.names(enh, p.folder.name + "_all")
    nm.piece.write_bytes(b"piece")
    nm.info.write_text(json.dumps({"project": nm.name, "project_folder": str(p.folder), "duration": 10.0,
                                   "frames": 240, "fps": "24", "width": 1920, "height": 1080}), encoding="utf-8")
    E._part_file(nm, 0).write_bytes(b"part 1")                               # noqa: SLF001
    E._seg_file(nm.part, 0).write_bytes(b"blocks")                           # noqa: SLF001
    E._resume_file(nm.part).write_text("{}", encoding="utf-8")               # noqa: SLF001
    p.update(enhance={"state": "sent", "name": nm.name, "folder": str(enh), "sent": time.time(),
                      "piece": json.loads(nm.info.read_text(encoding="utf-8"))})
    return PJ.load(p.folder), nm


def _left(nm: E.Names) -> list[str]:
    return sorted(f.name for f in nm.folder.iterdir() if f.is_file())


def test_no_topaz_with_nobody_enhancing_takes_the_piece_out_and_nothing_sends_it_again(tmp_path, zips):
    p, nm = _sent(tmp_path)
    r = E.no_topaz(p.folder)
    assert r["ok"] and "left the Enhance folder" in r["text"], r
    assert _left(nm) == []                                                  # every file of it, its parts and blocks
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["state"] == "off" and rec["no_topaz"] and rec["why"] == E.NO_TOPAZ_TEXT
    assert zips == [p.folder]                                               # the video finishes with its zip
    assert E.watch(p.folder) == {}                                          # the row: nothing to wait for
    assert not E._resend_wanted(PJ.load(p.folder), rec)                     # noqa: SLF001 - the sweep: nothing
    assert ES.outside(p.folder) == {} and ES.cover_all(p.folder)["clips"] == 0    # after an edit: nothing either


def test_no_topaz_while_the_laptop_enhances_it_stops_topaz_there(tmp_path, zips, monkeypatch):
    p, nm = _sent(tmp_path)
    monkeypatch.setattr(E, "STOP_LOOK_S", 0.01)
    monkeypatch.setattr(E, "check_enhanced", lambda *a, **k: {"ok": True, "why": ""})
    running = threading.Event()

    def topaz(src, dst, n, tz, prog, cancel, *a, **k):                      # a long Topaz: until it is stopped
        running.set()
        while not cancel.is_set():
            time.sleep(0.01)
        raise E.EnhanceError("stopped")
    monkeypatch.setattr(E, "run_topaz", topaz)
    token = E.take(nm)
    out: list = []
    t = threading.Thread(target=lambda: out.append(E.enhance_one(nm, token)))
    t.start()
    assert running.wait(10)
    r = E.no_topaz(p.folder)
    assert r["ok"] and "stops it now" in r["text"], r
    t.join(10)
    assert not t.is_alive() and out and out[0].why == E.NO_TOPAZ_TEXT
    assert _left(nm) == []                                                  # the stopped piece and the stop file too
    assert PJ.load(p.folder).data["enhance"]["state"] == "off"


def test_an_enhanced_piece_that_came_back_is_moved_aside_not_deleted(tmp_path, zips):
    p, nm = _sent(tmp_path)
    nm.enhanced.write_bytes(b"an hour of Topaz")
    assert E.no_topaz(p.folder)["ok"]
    assert _left(nm) == [] and (nm.folder / E.TO_DELETE / nm.enhanced.name).read_bytes() == b"an hour of Topaz"


def test_a_piece_with_the_stop_file_is_not_taken(tmp_path, zips):
    p, nm = _sent(tmp_path)
    assert [x.name for x in E.waiting_pieces(nm.folder)] == [nm.name]
    nm.stop.write_text("{}", encoding="utf-8")
    assert E.waiting_pieces(nm.folder) == [] and _left(nm) == []


def test_a_video_already_playing_the_enhanced_picture_is_left_alone(tmp_path, zips):
    p, nm = _sent(tmp_path)
    p.update(enhance={"state": "used"})
    r = E.no_topaz(p.folder)
    assert not r["ok"] and "already" in r["why"] and nm.piece.is_file()


def test_topaz_again_undoes_it(tmp_path, zips):
    p, nm = _sent(tmp_path)
    E.no_topaz(p.folder)
    r = E.topaz_again(p.folder)
    rec = PJ.load(p.folder).data["enhance"]
    assert r["ok"] and rec["state"] == "skipped" and rec["no_topaz"] is False and rec["after_edit"]


# ---------------------------------------------------------------------------------------------------------------------
# the row
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Ctx:
    def __init__(self):
        self.pages, self.notes, self.strip = {}, [], None

    def notify(self, title, text):
        self.notes.append((title, text))

    def set_strip_queue(self, n, running):
        self.strip = (n, running)


def test_the_rows_button(qapp, tmp_path, zips):
    from mr_app.tools import download_tool as D
    p, nm = _sent(tmp_path)
    page = D.DownloadPage(_Ctx())
    it = D.Item("https://youtu.be/abcdefghijk", title="My title", project=p.folder, name=p.folder.name,
                state=D.DONE, enhance=True)
    page.items.append(it)
    row = D.VideoRow(page, it)
    page.rows[it.id] = row
    page._enhance_said(it, PJ.load(p.folder).data["enhance"])               # noqa: SLF001 - with Topaz
    row.refresh()
    assert not row.b_notopaz.isHidden() and row.b_topaz_again.isHidden()
    page.no_topaz(it)
    t0 = time.perf_counter()
    while (it.steps.get("enhance").text != E.NO_TOPAZ_TEXT) and time.perf_counter() - t0 < 20:
        qapp.processEvents()
        time.sleep(0.02)
    assert it.steps["enhance"].text == E.NO_TOPAZ_TEXT and _left(nm) == []
    row.refresh()
    assert row.b_notopaz.isHidden() and not row.b_topaz_again.isHidden()
    page._enhance_said(it, PJ.load(p.folder).data["enhance"])               # noqa: SLF001 - as after a restart
    assert it.steps["enhance"].text == E.NO_TOPAZ_TEXT


def test_a_new_run_of_the_row_keeps_no_topaz(tmp_path, monkeypatch):
    """Try again runs the row's stages again; its "enhance" stage (before the edit) keeps the project "off"."""
    from mr_app.tools import download_tool as D
    p, nm = _sent(tmp_path)
    p.update(enhance={"state": "off", "why": E.NO_TOPAZ_TEXT, "no_topaz": True})
    from types import SimpleNamespace
    w = SimpleNamespace(_project=lambda: PJ.load(p.folder))                 # (the stage alone, no thread)
    out: dict = {}
    D.StageThread._enhance(w, out)                                          # noqa: SLF001
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["state"] == "off" and rec["no_topaz"] and out["enhance"]["state"] == "off"
