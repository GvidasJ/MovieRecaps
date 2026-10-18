"""The quote line (mr_app.core.quotes): the default quotes.txt (the mock-up's lists), the file's rules, the parts of the
day and their edges, one quote per part kept over a restart, never yesterday's, the click, the Goal line, edits of the
file, a share that does not answer. Run: python -m pytest tools/app/tests/test_quotes.py"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import paths, quotes as Q   # noqa: E402

MOCKUP = paths.repo() / "docs" / "ui_preview.html"
# the mock-up's lists (docs/ui_preview.html, its JS Q and goalQ) as they were on night 11
MORNING = [("I just never thought I couldn't.", "McQueen"),
           ("Amateurs sit and wait for inspiration, the rest of us just get up and go to work.", "Stephen King"),
           ("First Short of the day is the hardest. Start it.", ""), ("Discipline equals freedom.", "Jocko Willink")]
AFTERNOON = [("Be so good they can't ignore you.", "Steve Martin"),
             ("You miss 100% of the shots you don't take.", "Wayne Gretzky"),
             ("The algorithm can't push a Short that doesn't exist.", ""), ("Done beats perfect. Upload it.", "")]
EVENING = [("Hard work beats talent when talent doesn't work hard.", "Tim Notke"),
           ("One more Short could be the one that blows up.", ""),
           ("Every upload is a lottery ticket you print yourself.", ""), ("Consistency beats going viral once.", ""),
           ("Queue it up. Let the PC work while you sleep.", ""), ("Tomorrow's views are made tonight.", ""),
           ("Small daily wins, big monthly numbers.", "")]
GOAL = [("Goal hit. Now the bonus ones are pure profit.", ""), ("That's 6. The ones who win do one more.", "")]
DAY = date(2026, 10, 18)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m)


@pytest.fixture(autouse=True)
def quick_and_patient(monkeypatch):
    monkeypatch.setattr(Q, "RECHECK_S", 0.0)        # every call looks at the file (the tests edit it)
    monkeypatch.setattr(Q, "READ_WAIT_S", 10.0)     # and waits for it (a busy test machine)


def book(tmp_path, text=None, seed=1, name="quotes.txt"):
    f = tmp_path / name
    if text is not None:
        f.write_text(text, encoding="utf-8")
    return Q.QuoteBook(f, tmp_path / "state" / "quotes_state.json", rng=random.Random(seed))


# ---------------------------------------------------------------------------------------------------------------------
# the default file
# ---------------------------------------------------------------------------------------------------------------------

def _pairs(quotes):
    return [(q.text, q.author) for q in quotes]


def test_the_default_file_has_the_mockups_quotes_exactly():
    notes = []
    got = Q.parse(Q.DEFAULT_FILE.read_text(encoding="utf-8"), notes)
    assert notes == []
    assert (_pairs(got["Morning"]), _pairs(got["Afternoon"]), _pairs(got["Evening"]), _pairs(got["Goal"])) == \
        (MORNING, AFTERNOON, EVENING, GOAL)
    assert got["Morning"][0] == Q.Quote("Morning", "I just never thought I couldn't.", "McQueen")
    lines = Q.DEFAULT_FILE.read_text(encoding="utf-8").splitlines()
    head = lines[:lines.index("")]                                    # the comment header says how to edit it
    assert len(head) >= 3 and all(l.startswith("#") for l in head)
    assert any("Part | the quote | who said it" in l for l in head) and any("Goal" in l for l in head)


def test_the_default_file_matches_the_mockups_js():
    if not MOCKUP.is_file():
        pytest.skip("docs/ui_preview.html is not in this checkout")
    js = MOCKUP.read_text(encoding="utf-8")
    q = re.search(r"const Q=(\{.*?\});", js, re.S).group(1)
    q = json.loads(re.sub(r"(\b(?:Morning|Afternoon|Evening)):", r'"\1":', q))
    goal = json.loads(re.search(r"const goalQ=(\[.*?\]);", js, re.S).group(1))
    got = Q.default_quotes()
    for part in Q.DAY_PARTS:
        assert _pairs(got[part]) == [tuple(x) for x in q[part]]
    assert _pairs(got["Goal"]) == [tuple(x) for x in goal]


# ---------------------------------------------------------------------------------------------------------------------
# the file's rules
# ---------------------------------------------------------------------------------------------------------------------

def test_parse_comments_blank_lines_spaces_and_a_missing_author():
    text = ("﻿# my quotes\n\n   # an indented note\nMorning|  Stay   hungry.  |   Steve Jobs  \n"
            "Afternoon | Just ship it.\n  evening | “Keep going.” | — Nobody \n"
            "GOAL | \"One more.\" |\nNight | Late one. | Owl\n")
    notes = []
    got = Q.parse(text, notes)
    assert got["Morning"] == [Q.Quote("Morning", "Stay hungry.", "Steve Jobs")]
    assert got["Afternoon"] == [Q.Quote("Afternoon", "Just ship it.", "")]
    assert got["Evening"] == [Q.Quote("Evening", "Keep going.", "Nobody"), Q.Quote("Evening", "Late one.", "Owl")]
    assert got["Goal"] == [Q.Quote("Goal", "One more.", "")]
    assert notes == []


def test_lines_that_cannot_be_used_are_left_out_with_a_note():
    text = ("Lunch | Eat. | Me\nJust a quote without a part\nMorning |  | McQueen\nMorning | Same. \n"
            "morning | same. | someone\nMorning | text || author\n")
    notes = []
    got = Q.parse(text, notes)
    assert _pairs(got["Morning"]) == [("Same.", ""), ("text", "author")]
    assert got["Afternoon"] == got["Evening"] == got["Goal"] == []
    assert len(notes) == 4
    assert notes[0] == "line 1: 'Lunch' is not a part (Morning, Afternoon, Evening or Goal); left out"
    assert notes[1].startswith("line 2: no part") and notes[2].startswith("line 3: no quote")
    assert notes[3] == "line 5: this Morning quote is there already; left out"
    assert Q.parse("") == Q.parse(None) == {p: [] for p in Q.PARTS}


def test_how_a_quote_is_shown():
    q = Q.Quote("Morning", "I just never thought I couldn't.", "McQueen")
    assert (q.tag, q.shown, q.by) == ("Morning", "“I just never thought I couldn't.”", "— McQueen")
    assert Q.Quote("Goal", "One more.").tag == "Goal hit" and Q.Quote("Evening", "x").by == ""


# ---------------------------------------------------------------------------------------------------------------------
# the parts of the day
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("h, m, day, part", [
    (4, 59, DAY - timedelta(days=1), "Evening"),
    (5, 0, DAY, "Morning"), (11, 59, DAY, "Morning"),
    (12, 0, DAY, "Afternoon"), (17, 59, DAY, "Afternoon"),
    (18, 0, DAY, "Evening"), (23, 59, DAY, "Evening"),
    (0, 0, DAY - timedelta(days=1), "Evening"), (0, 30, DAY - timedelta(days=1), "Evening"),
])
def test_the_parts_of_the_day(h, m, day, part):
    assert Q.part_of(at(h, m)) == (day, part)


def test_the_part_of_a_time_with_its_zone_and_after_midnight_on_the_first_of_a_month():
    assert Q.part_of(datetime(2026, 10, 18, 4, 59, tzinfo=timezone(timedelta(hours=3)))) == \
        (date(2026, 10, 17), "Evening")
    assert Q.part_of(datetime(2026, 11, 1, 0, 30)) == (date(2026, 10, 31), "Evening")
    assert Q.part_of(datetime(2027, 1, 1, 3, 0)) == (date(2026, 12, 31), "Evening")


def test_when_the_part_ends():
    assert Q.part_ends(at(4, 59)) == at(5)
    assert Q.part_ends(at(5)) == at(12) and Q.part_ends(at(11, 59)) == at(12)
    assert Q.part_ends(at(12)) == at(18) and Q.part_ends(at(18)) == at(5, day=DAY + timedelta(days=1))
    assert Q.part_ends(at(0, 30)) == at(5)


# ---------------------------------------------------------------------------------------------------------------------
# the picks
# ---------------------------------------------------------------------------------------------------------------------

def test_one_quote_for_the_whole_part_and_the_same_after_a_restart(tmp_path):
    src = Q.DEFAULT_FILE.read_text(encoding="utf-8")
    b = book(tmp_path, src, seed=3)
    morning = b.current(at(5))
    assert morning.part == "Morning" and (morning.text, morning.author) in MORNING
    assert all(b.current(at(h, m)) == morning for h, m in ((6, 0), (9, 30), (11, 59)))
    for seed in (4, 5, 6):                              # the app started again (another random start): the same
        assert book(tmp_path, seed=seed).current(at(10, 15)) == morning
    afternoon = b.current(at(12))
    assert afternoon.part == "Afternoon" and (afternoon.text, afternoon.author) in AFTERNOON
    evening = b.current(at(23, 0))
    assert evening.part == "Evening" and b.current(at(0, 30, DAY + timedelta(days=1))) == evening   # after midnight
    assert book(tmp_path, seed=9).current(at(4, 59, DAY + timedelta(days=1))) == evening


@pytest.mark.parametrize("seed", range(6))
def test_never_yesterdays_quote_and_every_quote_once_a_round(tmp_path, seed):
    b = book(tmp_path, Q.DEFAULT_FILE.read_text(encoding="utf-8"), seed=seed)
    for part, hour, n in (("Morning", 8, 4), ("Afternoon", 15, 4), ("Evening", 20, 7)):
        picks = []
        for d in range(4 * n):
            day = DAY + timedelta(days=d)
            if d % 3 == 0:                                   # restarts in between
                b = book(tmp_path, seed=seed * 100 + d)
            picks.append(b.current(at(hour, 0, day)).text)
        assert all(a != b_ for a, b_ in zip(picks, picks[1:])), picks
        for r in range(4):                                   # each round of n days shows every quote once
            assert len(set(picks[r * n:(r + 1) * n])) == n


def test_a_part_with_one_quote_shows_it_every_day(tmp_path):
    b = book(tmp_path, "Morning | Only one. | Me\n")
    assert {b.current(at(8, 0, DAY + timedelta(days=d))).text for d in range(5)} == {"Only one."}
    assert b.next(at(8)).text == "Only one."


def test_a_click_shows_the_next_quote_of_the_part_for_the_rest_of_the_part(tmp_path):
    b = book(tmp_path, Q.DEFAULT_FILE.read_text(encoding="utf-8"), seed=2)
    texts = [t for t, _ in MORNING]
    first = b.current(at(7))
    k = texts.index(first.text)
    clicked = [b.next(at(7, 1 + i)).text for i in range(5)]
    assert clicked == [texts[(k + 1 + i) % 4] for i in range(5)]           # in the file's order, round and round
    assert b.current(at(11, 0)).text == clicked[-1]                        # kept for the rest of the part ...
    assert book(tmp_path, seed=77).current(at(11, 30)).text == clicked[-1]   # ... also after a restart
    tomorrow = b.current(at(7, 0, DAY + timedelta(days=1)))
    assert tomorrow.text != clicked[-1]                                    # and not shown again the next day
    assert b.current(at(13)).part == "Afternoon"


def test_the_goal_line_stays_for_the_day_and_changes_the_next(tmp_path):
    b = book(tmp_path, Q.DEFAULT_FILE.read_text(encoding="utf-8"), seed=5)
    g = b.goal(at(9))
    assert g.part == "Goal" and g.tag == "Goal hit" and (g.text, g.author) in GOAL
    assert b.goal(at(23, 59)) == g and book(tmp_path, seed=8).goal(at(15)) == g
    days = [book(tmp_path, seed=d).goal(at(20, 0, DAY + timedelta(days=d))).text for d in range(1, 6)]
    seq = [g.text, *days]
    assert all(a != b_ for a, b_ in zip(seq, seq[1:]))                     # two Goal lines: they take turns
    assert b.current(at(9)).part == "Morning"                              # the parts' picks are separate


# ---------------------------------------------------------------------------------------------------------------------
# the file changes, is missing, or does not answer
# ---------------------------------------------------------------------------------------------------------------------

def test_an_edit_of_the_file_shows_up(tmp_path):
    b = book(tmp_path, "Morning | Old one. | A\nMorning | Old two. | B\n")
    first = b.current(at(8))
    assert first.text in ("Old one.", "Old two.")
    f = tmp_path / "quotes.txt"
    f.write_text("Morning | Brand new. | C\nMorning | Also new, a bit longer. | D\n", encoding="utf-8")
    later = b.current(at(9))                                    # the quote shown is gone from the file: a new pick
    assert later.text in ("Brand new.", "Also new, a bit longer.")
    assert b.current(at(10)) == later
    keep = later.text
    f.write_text(f"Morning | {keep} | someone else\nMorning | Another. | E\nAfternoon | New afternoon. | F\n",
                 encoding="utf-8")
    assert b.current(at(11)).text == keep                       # still in the file: it stays
    assert b.current(at(13)) == Q.Quote("Afternoon", "New afternoon.", "F")


def test_no_file_or_an_empty_part_shows_the_apps_quotes(tmp_path):
    b = book(tmp_path)                                          # no quotes.txt at all
    assert (b.current(at(8)).text, b.current(at(8)).author) in MORNING
    assert b.notes and "is not there" in b.notes[0]
    b = book(tmp_path, "Morning | Mine. | Me\n# nothing else\n", name="partial.txt")
    assert b.current(at(8)).text == "Mine."
    assert (b.current(at(20)).text, b.current(at(20)).author) in EVENING
    assert any("no Evening quotes" in n for n in b.notes) and (b.goal(at(20)).text, "") in GOAL


def test_a_file_saved_as_ansi_still_reads(tmp_path):
    f = tmp_path / "quotes.txt"
    f.write_bytes("Morning | Café first. | René\n".encode("cp1252"))
    b = Q.QuoteBook(f, tmp_path / "s.json")
    assert b.current(at(8)) == Q.Quote("Morning", "Café first.", "René")


def test_a_share_that_does_not_answer_never_blocks_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "READ_WAIT_S", 0.2)
    b = book(tmp_path, "Morning | From the share. | Me\n")
    real_read = b._read

    def slow_read():
        time.sleep(3.0)
        real_read()
    b._read = slow_read
    t0 = time.time()
    first = b.current(at(8))
    assert time.time() - t0 < 2.0                              # did not wait for the share (3 s)
    assert (first.text, first.author) in MORNING                # the app's quotes meanwhile
    b._reader.join(5)
    assert b.current(at(8, 5)).text == "From the share."       # read in the background: shown now


def test_calls_from_several_threads_at_once_agree(tmp_path):
    import threading
    b = book(tmp_path, Q.DEFAULT_FILE.read_text(encoding="utf-8"), seed=11)
    got, errors = [], []

    def ask():
        try:
            got.append(b.current(at(9)))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=ask) for _ in range(12)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert errors == [] and len(got) == 12 and len(set(got)) == 1           # one pick for the part, not twelve
    assert book(tmp_path, seed=12).current(at(10)) == got[0]


def test_ensure_quotes_file_copies_the_default_once(tmp_path):
    hub = tmp_path / "Shared" / "Projects"
    p = Q.ensure_quotes_file(hub)
    assert p == hub / "quotes.txt"
    assert Q.parse(p.read_text(encoding="utf-8")) == Q.parse(Q.DEFAULT_FILE.read_text(encoding="utf-8"))
    p.write_text("Morning | Mine. | Me\n", encoding="utf-8")
    assert Q.ensure_quotes_file(hub) == p and p.read_text(encoding="utf-8") == "Morning | Mine. | Me\n"
    blocker = tmp_path / "a file"
    blocker.write_text("x", encoding="utf-8")
    assert Q.ensure_quotes_file(blocker / "sub") == Q.DEFAULT_FILE   # cannot be made: the app's own


def test_a_broken_or_unwritable_state_file_is_not_an_error(tmp_path):
    state = tmp_path / "quotes_state.json"
    state.write_text("{nope", encoding="utf-8")
    b = Q.QuoteBook(Q.DEFAULT_FILE, state, rng=random.Random(1))
    assert b.current(at(8)).part == "Morning"
    assert json.loads(state.read_text(encoding="utf-8"))["parts"]["Morning"]["day"] == "2026-10-18"
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    b = Q.QuoteBook(Q.DEFAULT_FILE, blocker / "s.json", rng=random.Random(1))
    assert b.current(at(8)) == b.current(at(9))                # kept in memory at least


def test_the_picks_live_in_the_app_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    assert Q.default_state_path() == tmp_path / "appdata" / "quotes_state.json"
    b = Q.QuoteBook(Q.DEFAULT_FILE)                              # no state path: the default
    assert b.state_path == tmp_path / "appdata" / "quotes_state.json"
    b.current(at(8))
    assert b.state_path.is_file()
    monkeypatch.delenv("MOVIERECAPS_APPDATA")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert Q.default_state_path() == tmp_path / "Roaming" / "MovieRecaps" / "quotes_state.json"
