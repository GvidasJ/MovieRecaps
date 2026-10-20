"""Day 13 (the lead's click-through of the real window): a board card's name -- now the video's title -- may break
inside a long word in a narrow column ("Questionable" was cut to "Questional" in a 1280 px window), never inside an
emoji; folder names still break after _ and -.

Run: python -m pytest tools/app/tests/test_board_day13.py"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import board as B  # noqa: E402
from mr_app.core import names as NM  # noqa: E402
from mr_app.core import premiere as PM  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import renames as RN  # noqa: E402
from mr_app.tools import board_tool as BT  # noqa: E402

ZW = "​"
TITLE = "The Most Questionable Ad of All Time 📻😭"


def test_soft_breaks_inside_long_words_only():
    got = BT.soft_breaks(TITLE)
    assert got.replace(ZW, "") == TITLE                                  # only break opportunities are added
    assert ZW.join("Questionable") in got                                # a word of 11+ letters may break anywhere
    assert "The Most " in got and " Ad of All Time " in got              # short words untouched
    assert "📻😭" in got                                                  # never inside an emoji
    assert BT.soft_breaks("2026-10-20_interesting-fact") == f"2026-{ZW}10-{ZW}20_{ZW}" + ZW.join("interesting") + f"-{ZW}fact"
    assert BT.soft_breaks("Señor Ahí") == "Señor Ahí"                     # accents: short words stay whole
    family = "👨‍👩‍👧 Unbelievably"                               # a joined emoji keeps its joiners
    assert BT.soft_breaks(family).startswith("👨‍👩‍👧 ")


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_a_long_word_in_a_cards_title_fits_its_narrow_column(qapp, tmp_path, monkeypatch):
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(PM, "state", lambda: PM.State())
    monkeypatch.setattr(RN, "scan_places", lambda hub: [])
    monkeypatch.setattr(NM, "root_len", lambda *roots: NM.ROOT_LEN)
    p = PJ.create(local, competitor_url="https://youtu.be/AbCdEf12345", name="interesting fact")
    p.set_step("download", PJ.DONE)
    B.set_title(p.folder, TITLE)
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": None)
    page = BT.BoardPage(ctx)
    try:
        page._up_timer.stop()                                            # noqa: SLF001 - no Shorts check in tests
        page.check_uploads = lambda force=False: None
        page.resize(1280, 900)                                           # 8 narrow columns
        page.show()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 8 and not [w for c in page.columns.values() for w in getattr(c, "widgets", [])]:
            qapp.processEvents()
            time.sleep(0.01)
        for _ in range(20):
            qapp.processEvents()
        w = [w for c in page.columns.values() for w in getattr(c, "widgets", [])][0]
        lab = w.name_label
        assert lab.text().replace(ZW, "") == TITLE
        assert ZW.join("Questionable") in lab.text()
        assert lab.minimumSizeHint().width() <= lab.width()             # nothing wider than the label: no word cut
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
