"""Day 13, item 1.2 in the Learn tab: the three Borat entries of problems_queue.md (written by day 12's app, their
titles "stopped (exit 2) -- details in ... match_cuts.log" / "ERROR: FileNotFoundError ... state-4.pkl ...") are shown
with the real reason in plain words -- read from each entry's own key log lines (text only). The hub is a tmp
folder, never D:\\Shared\\Projects.

Run: python -m pytest tools/app/tests/test_errors_day13_learn.py"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("PySide6.QtWidgets")

from test_learn_columns_ui import cards, env, pump_until, qapp  # noqa: E402,F401 - the Learn page on a tmp hub

from mr_app.core import problems as PR  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402

D = r"C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-19_borat-cultural-learnings-of-america-for"
CMD = (r"ffmpeg -v error -nostdin -y -i " + D + r"\Borat (2006) (1080p BluRay x265 Silence).mp4 -map 0:v:0 -map "
       r"0:a:0 -filter:v settb=1001/24000,setpts=N,fps=fps=24000/1001,trim=end_frame=120553,setsar=1,format=yuv420p "
       r"-c:v libx264 -preset veryfast -crf 12 -map_metadata -1 -map_chapters -1")
BORAT = f"""# Problems to fix

- [ ] **2026-10-19_borat-cultural-learnings-of-america-for** · stopped (exit 2) -- ERROR: FileNotFoundError: [Errno 2] No such file or directory: 'C:\\\\Users\\\\gvida\\\\AppData\\\\Local\\\\Temp\\\\match_cuts_pool_5mhjfe2b\\\\state-4.pkl' match_cuts: details in {D}\\edit\\extr
  - status: open
  - run: {D}\\edit
  - date: 2026-10-20 00:18
  - computer: laptop
  - result: FAIL
  - exit: 2
  - id: d800401a08
  - key log lines:
    > match_cuts: ERROR: FileNotFoundError: [Errno 2] No such file or directory: 'C:\\\\Users\\\\gvida\\\\AppData\\\\Local\\\\Temp\\\\match_cuts_pool_5mhjfe2b\\\\state-4.pkl'
    > match_cuts: details in {D}\\edit\\extras\\match_cuts.log

- [ ] **2026-10-19_borat-cultural-learnings-of-america-for** · stopped (exit 2) -- details in {D}\\edit-2\\extras\\match_cuts.log
  - status: open
  - run: {D}\\edit-2
  - date: 2026-10-20 00:30
  - computer: laptop
  - result: FAIL
  - exit: 2
  - id: 6e5bac6622
  - key log lines:
    > match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (4294967268): {CMD}
    > match_cuts: details in {D}\\edit-2\\extras\\match_cuts.log
"""


def test_old_entries_say_the_plain_reason():
    a, b = PR.parse(BORAT)
    assert PR.plain_check(a).startswith("stopped (exit 2) -- The run crashed: the tool's helper processes lost their "
                                        "working file in the temp folder (C:\\Users\\gvida\\AppData\\Local\\Temp)")
    assert PR.plain_check(b).startswith("stopped (exit 2) -- The disk C: is full: the run stopped while converting "
                                        "the RAW. Free space on C: (Board → Clean up), then press Try again.")
    for p in (a, b):
        assert "details in" not in PR.plain_check(p) and "match_cuts.log" not in PR.plain_check(p)
    plain = PR.Problem("x", check="FAIL: XML PERSON -- 1_edit.xml check: the person speaking is not in the window")
    assert PR.plain_check(plain) == plain.check                                  # plain titles stay as they are
    assert PR.plain_check(PR.Problem("y", check="stopped (exit 3)")) == "stopped (exit 3)"


def test_the_learn_tabs_problem_cards_say_it(env):
    Mo.set_enabled(False)
    (env.hub / PR.FILE).write_text(BORAT, encoding="utf-8")
    page = env.make_page()
    page.refresh_columns()
    col = page.columns.problems
    assert pump_until(env.app, lambda: len(cards(col)) == 2)
    text = " | ".join(c.small.text() for c in cards(col))
    assert "The disk C: is full" in text and "temp folder" in text and "details in" not in text
    assert (env.hub / PR.FILE).read_text(encoding="utf-8") == BORAT              # the shared file is not rewritten
