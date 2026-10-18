"""Night 11: a downloaded video with a channel and a title gets its template right after the conversion (the board's
Template column), not only when the edit starts; without a title it waits as before. The slow steps are faked."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import flow  # noqa: E402
from mr_app.core import projects as P  # noqa: E402
from mr_app.tools import download_tool as D  # noqa: E402


@pytest.mark.parametrize("title,made", [("When she finally got the part 💀😭", True), ("", False)])
def test_the_template_is_made_after_the_conversion_when_there_is_a_title(tmp_path, monkeypatch, title, made):
    p = P.create(tmp_path, competitor_url="https://youtu.be/abcdefghijk", raw_url="https://youtu.be/ABCDEFGHIJK")
    calls = []
    monkeypatch.setattr(flow, "download_step", lambda proj, say, cancel: calls.append("download"))
    monkeypatch.setattr(flow, "convert_step", lambda proj, say, cancel: calls.append("convert"))
    monkeypatch.setattr(flow, "title_check_step", lambda proj, title="": {"competitor_headline": "", "message": ""})
    monkeypatch.setattr(flow, "template_step", lambda proj, ch=None, t=None: calls.append(("template", ch, t)))
    it = D.Item("https://youtu.be/abcdefghijk", channel="Flick707", title=title, project=p.folder)
    th = D.PrepareThread(it)
    got = []
    th.done.connect(lambda i, res: got.append(res))
    th.run()                                                # in this thread: the steps are fakes
    assert got and got[0]["ok"]
    assert calls[:2] == ["download", "convert"]
    assert (("template", "Flick707", title) in calls) == made
