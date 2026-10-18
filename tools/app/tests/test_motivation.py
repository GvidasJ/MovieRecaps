"""The motivation strip and the weekly charts (mr_app.core.motivation): today's ring and its words, the month's
squares and the pace against last month (also after a 30-day month, a February, a new year), the week's bars across
week and month edges. Run: python -m pytest tools/app/tests/test_motivation.py"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import motivation as M   # noqa: E402

LOU, MIKE, FLICK = "LouLaughs", "MikeMemesYou", "Flick707"
NOW = datetime(2026, 10, 18, 14, 30)                    # a Sunday


def up(y, mo, d, h=12, mi=0, ch=LOU):
    return (datetime(y, mo, d, h, mi), ch)


def oct_(d, h=12, ch=LOU):
    return up(2026, 10, d, h, 0, ch)


def sep(d, h=12, ch=LOU):
    return up(2026, 9, d, h, 0, ch)


# ---------------------------------------------------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------------------------------------------------

def test_short_names_and_the_channel_order():
    assert [M.short_name(c) for c in (LOU, MIKE, FLICK, "OtherOne", "loulaughs", "")] == \
        ["Lou", "Mike", "Flick707", "OtherOne", "Lou", "no channel"]
    assert M.channel_order(["Zeta", FLICK, "alpha", MIKE, LOU, FLICK]) == [LOU, MIKE, FLICK, "alpha", "Zeta"]


# ---------------------------------------------------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------------------------------------------------

def test_today_as_in_the_mockup():
    ups = [oct_(18, 9, LOU), oct_(18, 10, MIKE), oct_(18, 11, FLICK), oct_(18, 13, LOU), oct_(17, 23, MIKE)]
    t = M.today(ups, NOW)
    assert (t.count, t.goal, t.left, t.hit) == (4, 6, 2, False)
    assert t.segments == [LOU, MIKE, FLICK, LOU]                         # in upload order
    assert t.text == "2 more to hit today's goal"
    assert t.split == "2 Lou · 1 Mike · 1 Flick707"
    assert t.split_counts == [(LOU, 2), (MIKE, 1), (FLICK, 1)]


def test_one_more_and_the_goal_hit_and_the_bonus_ones():
    five = [oct_(18, h, ch) for h, ch in zip(range(8, 13), (FLICK, FLICK, MIKE, FLICK, MIKE))]
    t = M.today(five, NOW)
    assert (t.count, t.left, t.text) == (5, 1, "1 more to hit today's goal")
    assert t.split == "2 Mike · 3 Flick707"                              # only channels with uploads, in order
    t = M.today(five + [oct_(18, 13, LOU)], NOW)
    assert (t.count, t.left, t.hit, t.text) == (6, 0, True, "Goal hit. Want a bonus one?")
    t = M.today(five + [oct_(18, 13, LOU), oct_(18, 14, LOU), oct_(18, 14, MIKE)], NOW)
    assert t.count == 8 and len(t.segments) == 6 and t.hit and t.left == 0
    assert t.segments == [FLICK, FLICK, MIKE, FLICK, MIKE, LOU]          # the first six fill the ring
    t = M.today(five, NOW, goal=4)
    assert t.hit and t.goal == 4 and len(t.segments) == 4
    assert M.today([], NOW, goal=0).goal == 1                            # a goal is at least 1


def test_today_is_the_calendar_day_in_this_computers_time():
    ups = [oct_(17, 23, LOU), (datetime(2026, 10, 17, 23, 59, 59), MIKE), (datetime(2026, 10, 18, 0, 0), FLICK),
           (datetime(2026, 10, 18, 23, 59), LOU), (datetime(2026, 10, 19, 0, 0), MIKE)]
    t = M.today(ups, NOW)
    assert t.count == 2 and t.segments == [FLICK, LOU]
    aware = datetime(2026, 10, 18, 9, 0).astimezone().astimezone(timezone.utc)    # today here, written in UTC
    assert M.today([(aware, MIKE)], NOW).count == 1
    assert M.today([(aware, MIKE)], NOW.astimezone()).count == 1                 # now with its offset too
    unsorted = [oct_(18, 15, MIKE), oct_(18, 8, LOU)]
    assert M.today(unsorted, NOW).segments == [LOU, MIKE]
    assert M.today([(None, LOU), ("x", MIKE), (oct_(18)[0], None), 5], NOW).split == "1 no channel"


def test_the_day_can_start_at_five_like_the_quotes(monkeypatch):
    monkeypatch.setattr(M, "DAY_START_HOUR", 5)
    night = (datetime(2026, 10, 18, 2, 34), LOU)                          # a night session: yesterday's day
    assert M.today([night], datetime(2026, 10, 18, 3, 0)).count == 1
    assert M.today([night], datetime(2026, 10, 18, 9, 0)).count == 0


# ---------------------------------------------------------------------------------------------------------------------
# this month
# ---------------------------------------------------------------------------------------------------------------------

def test_the_months_squares():
    ups = [oct_(12, h) for h in range(8, 13)] + [oct_(3), oct_(18, 9), oct_(18, 10)] + [sep(30)]
    mo = M.month(ups, NOW)
    assert (mo.label, mo.count, mo.goal, len(mo.days), mo.year, mo.month) == ("October", 8, 150, 31, 2026, 10)
    assert [c.day for c in mo.days] == list(range(1, 32))
    assert [c.kind for c in mo.days[:17]] == ["past"] * 17 and mo.days[17].kind == "today"
    assert all(c.kind == "future" for c in mo.days[18:])
    assert mo.days[11] == M.DayCell(12, 5, "past", "Oct 12 · 5 Shorts", round(0.15 + 0.85 * 5 / 6, 4))
    assert mo.days[2].tip == "Oct 3 · 1 Short" and mo.days[2].level == round(0.15 + 0.85 / 6, 4)
    assert mo.days[0].tip == "Oct 1 · 0 Shorts" and mo.days[0].level == 0.0
    assert mo.days[17].tip == "Oct 18 · 2 Shorts"
    assert mo.days[24].tip == "Oct 25" and mo.days[24].count == 0
    assert M.level(6) == 1.0 and M.level(9) == 1.0 and M.level(0) == 0.0 and M.level(3, day_goal=3) == 1.0
    assert M.month([], NOW, goal=200).goal == 200


def test_the_pace_against_last_month_up_to_the_same_day():
    ahead = [oct_(d) for d in range(1, 19) for _ in range(2)] + [sep(d) for d in range(1, 16)] + \
            [sep(d) for d in range(19, 31) for _ in range(3)]                 # September's end does not count yet
    mo = M.month(ahead, NOW)
    assert mo.count == 36 and mo.diff == 21
    assert (mo.pace, mo.ahead, mo.behind, mo.previous) == ("▲ 21 ahead of September", True, False, "September")
    behind = [oct_(5)] * 4 + [sep(d) for d in range(1, 10)]
    mo = M.month(behind, NOW)
    assert (mo.pace, mo.ahead, mo.behind, mo.diff) == ("▼ 5 behind September", False, True, -5)
    level = [oct_(1), oct_(2), sep(3), sep(18, 23)]                            # Sep 18 late: still "up to the 18th"
    mo = M.month(level, NOW)
    assert (mo.pace, mo.ahead, mo.behind, mo.diff) == ("= level with September", True, False, 0)   # green: not behind


def test_the_pace_on_the_31st_after_a_30_day_month():
    now = datetime(2026, 10, 31, 20, 0)
    ups = [sep(1), sep(30, 23), up(2026, 9, 30, 23, 59)] + [oct_(d) for d in (1, 15, 31)] + [oct_(31, 21)]
    mo = M.month(ups, now)
    assert mo.count == 4 and mo.diff == 1 and mo.pace == "▲ 1 ahead of September"   # all of September: 3
    assert mo.days[30].kind == "today" and len(mo.days) == 31


def test_the_pace_after_february_and_over_new_year():
    feb = [up(2027, 2, d) for d in (1, 27, 28)]
    mo = M.month(feb + [up(2027, 3, 30)], datetime(2027, 3, 31, 10))
    assert (mo.label, mo.previous, mo.diff) == ("March", "February", -2) and mo.pace == "▼ 2 behind February"
    dec = [up(2026, 12, 5), up(2026, 12, 10, 23, 59), up(2026, 12, 11)]
    mo = M.month(dec + [up(2027, 1, 2), up(2027, 1, 3)], datetime(2027, 1, 10, 8))
    assert (mo.label, mo.previous, mo.pace, mo.count) == ("January", "December", "= level with December", 2)
    assert M.month([up(2028, 2, 29)], datetime(2028, 2, 29, 9)).days[-1] == \
        M.DayCell(29, 1, "today", "Feb 29 · 1 Short", round(0.15 + 0.85 / 6, 4))           # a leap year


def test_no_pace_before_there_is_a_month_to_compare_with():
    mo = M.month([oct_(1), oct_(2)], NOW)
    assert (mo.pace, mo.ahead, mo.behind, mo.diff, mo.count) == ("", False, False, 0, 2)


def test_month_edges_belong_to_their_own_month():
    ups = [(datetime(2026, 9, 30, 23, 59, 59), LOU), (datetime(2026, 10, 1, 0, 0), MIKE)]
    mo = M.month(ups, datetime(2026, 10, 1, 9, 0))
    assert mo.count == 1 and mo.days[0].count == 1
    assert mo.pace == "▲ 1 ahead of September"                   # Oct 1 against Sep 1 (Sep 30 is later)


# ---------------------------------------------------------------------------------------------------------------------
# this week
# ---------------------------------------------------------------------------------------------------------------------

def test_the_week_monday_to_sunday():
    assert NOW.weekday() == 6                                              # Sunday, Oct 18 2026
    ups = [oct_(12, 8), oct_(12, 23), oct_(18, 23, FLICK), oct_(15, ch=MIKE), oct_(11, 23, LOU),
           (datetime(2026, 10, 19, 0, 0), FLICK), (datetime(2026, 10, 12, 0, 0), MIKE)]
    w = M.week(ups, NOW)
    assert w == {LOU: [2, 0, 0, 0, 0, 0, 0], MIKE: [1, 0, 0, 1, 0, 0, 0], FLICK: [0, 0, 0, 0, 0, 0, 1]}
    assert list(w) == [LOU, MIKE, FLICK]
    assert [M.week_total(v) for v in w.values()] == ["2 this week", "2 this week", "1 this week"]
    assert M.week_total(w) == "5 this week"
    assert M.week(ups, datetime(2026, 10, 12, 0, 5)) == w                   # Monday morning: the same week


def test_a_week_over_a_month_edge_and_the_channels_shown():
    ups = [sep(28, 9, LOU), sep(30, 9, MIKE), oct_(4, 22, FLICK), oct_(5, 9, FLICK), sep(27, 9, LOU),
           oct_(1, 9, "NewChannel")]
    w = M.week(ups, datetime(2026, 9, 30, 12))                              # Wed: Mon Sep 28 .. Sun Oct 4
    assert w == {LOU: [1, 0, 0, 0, 0, 0, 0], MIKE: [0, 0, 1, 0, 0, 0, 0], FLICK: [0, 0, 0, 0, 0, 0, 1],
                 "NewChannel": [0, 0, 0, 1, 0, 0, 0]}
    assert M.week(ups, datetime(2026, 9, 30, 12), channels=[FLICK]) == {FLICK: [0, 0, 0, 0, 0, 0, 1]}
    assert M.week([], NOW) == {LOU: [0] * 7, MIKE: [0] * 7, FLICK: [0] * 7}
    assert M.week_total([0] * 7) == "0 this week"
