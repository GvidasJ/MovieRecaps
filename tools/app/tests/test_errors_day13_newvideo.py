"""Day 13, item 1.2 on the real New video row: an edit that stopped by itself (Borat on the laptop: ffmpeg's
4294967268 = the disk is full) says why in plain words and what to do -- never "details in ... match_cuts.log".

Run: python -m pytest tools/app/tests/test_errors_day13_newvideo.py"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("PySide6.QtWidgets")

from test_errors_day13 import BORAT_FFMPEG, _run  # noqa: E402
from test_newvideo_ui import COMP, YT, page  # noqa: E402,F401 - the New video page with its stages faked

from mr_app.core import results as R  # noqa: E402
from mr_app.tools import download_tool as D  # noqa: E402


def test_the_new_video_row_says_why_its_edit_stopped(page, tmp_path):            # noqa: F811
    it = D.Item(COMP, raw_url=YT)
    page._add_item(it)                                                          # noqa: SLF001
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    page._finish(it, res)                                                       # noqa: SLF001 - as the queue's poll does
    assert it.state == D.FAILED and it.failed_step == "edit"
    assert it.error == res["reason_line"]
    text = page.now_text(it)
    assert text.startswith("Stopped at edit: The disk C: ran out of space") and text.endswith("then press Try again.")
    assert "details in" not in text and "match_cuts.log" not in text and "4294967268" not in text
