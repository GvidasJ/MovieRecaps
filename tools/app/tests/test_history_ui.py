"""The History tab on the real widgets (night 11, item 9): everything that happened, newest first, each with its date
and time, and a search by title or channel.

Run: python -m pytest tools/app/tests/test_history_ui.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import projects as PJ  # noqa: E402
from mr_app.tools import history_tool as HT  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_the_history_lists_every_step_newest_first_and_searches(qapp, tmp_path):
    a = PJ.create(tmp_path, name="clip one")
    a.update(channel="LouLaughs", title="When she finally got the part 💀😭")
    a.set_step("download", PJ.DONE)
    b = PJ.create(tmp_path, name="clip two")
    b.update(channel="Flick707", title="The funniest allergic reaction 🥜")
    b.set_step("download", PJ.DONE)
    b.set_step("convert", PJ.DONE)
    page = HT.HistoryPage(SimpleNamespace())
    page.resize(900, 700)
    page.show()
    assert page.empty.isVisibleTo(page) and page.count.text() == ""
    page.set_projects([PJ.load(a.folder).data, PJ.load(b.folder).data])
    n = page.items.count()
    assert n >= 3 and page.count.text() == f"{n} event(s)" and not page.empty.isVisibleTo(page)
    whens = [page.items.itemAt(i).widget().ev.at for i in range(n)]
    assert whens == sorted(whens, reverse=True)                                     # newest first
    page.search.setText("flick707")
    m = page.items.count()
    assert 0 < m < n and all(page.items.itemAt(i).widget().ev.channel == "Flick707" for i in range(m))
    page.search.setText("got the part")
    assert page.items.count() >= 1 and all("part" in page.items.itemAt(i).widget().ev.title
                                           for i in range(page.items.count()))
    page.search.setText("nothing like this")
    assert page.items.count() == 0 and page.empty.isVisibleTo(page)
    page.close()
