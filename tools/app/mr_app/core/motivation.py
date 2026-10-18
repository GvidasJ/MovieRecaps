"""THE MOTIVATION STRIP (under the quote, on the Match cuts tab) and the board's weekly charts, counted from the
uploads -- (when, channel) of every Short in the board's Uploaded column (board.uploads(cards)).

* ``today(uploads, now, goal=6)``: the ring split into ``goal`` pieces, one piece per Short uploaded today in its
  channel's colour, "4 / 6", "2 more to hit today's goal" (hit: "Goal hit. Want a bonus one?") and the split
  "2 Lou · 1 Mike · 1 Flick707".
* ``month(uploads, now, goal=150)``: "87 / 150 Shorts", a square per day (its colour strength: how many that day;
  today; the days to come), "Oct 12 · 5 Shorts" on hover, and the pace against last month up to the same day:
  "▲ 21 ahead of September" / "▼ 5 behind September" / "= level with September" (no pace before there is a month to
  compare with).
* ``week(uploads, now)``: the 7 bars Mon-Sun of each channel; ``week_total``: "N this week".

Days are calendar days in this computer's time (DAY_START_HOUR moves the start of a day, e.g. to 5 like the quote's
parts). Quick (no files, no network): the GUI thread is fine."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Sequence

DAY_GOAL = 6
MONTH_GOAL = 150
DAY_START_HOUR = 0              # a day starts at this hour (0: midnight)
CHANNEL_ORDER = ("LouLaughs", "MikeMemesYou", "Flick707")
SHORT_NAMES = {"LouLaughs": "Lou", "MikeMemesYou": "Mike", "Flick707": "Flick707"}
NO_CHANNEL = "no channel"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
LEVEL_FLOOR = 0.15              # a day with one Short is at least this strong (the mock-up's .15 + v / 6 * .85)

Upload = tuple[datetime, str]


# ---------------------------------------------------------------------------------------------------------------------
# channels and days
# ---------------------------------------------------------------------------------------------------------------------

def _canon(channel: str | None) -> str:
    """The channel's own spelling when it is one of the known channels in another case."""
    ch = str(channel or "")
    for known in CHANNEL_ORDER:
        if ch.casefold() == known.casefold():
            return known
    return ch


def short_name(channel: str | None) -> str:
    """LouLaughs -> Lou, MikeMemesYou -> Mike, Flick707 -> Flick707; any other channel as it is ('' -> 'no
    channel')."""
    ch = _canon(channel)
    return SHORT_NAMES.get(ch, ch) or NO_CHANNEL


def _channel_key(channel: str) -> tuple[int, str]:
    ch = _canon(channel)
    return (CHANNEL_ORDER.index(ch) if ch in CHANNEL_ORDER else len(CHANNEL_ORDER), ch.casefold())


def channel_order(channels: Iterable[str]) -> list[str]:
    """LouLaughs, MikeMemesYou, Flick707, then the others by name (each once)."""
    return sorted({_canon(c) for c in channels}, key=_channel_key)


def _local(dt: datetime | None) -> datetime:
    """Local clock time without the offset (a time with an offset: in this computer's time first)."""
    dt = dt or datetime.now()
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo is not None else dt


def _day(dt: datetime) -> date:
    return (_local(dt) - timedelta(hours=DAY_START_HOUR)).date()


def _clean(uploads: Iterable[Upload]) -> list[Upload]:
    """The uploads that have a time, their channels in the known spelling, oldest first."""
    out = []
    for item in uploads or ():
        try:
            t, ch = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        if isinstance(t, datetime):
            out.append((t, _canon(ch)))
    return sorted(out, key=lambda x: _local(x[0]))


def _shorts(n: int) -> str:
    return f"{n} Short" if n == 1 else f"{n} Shorts"


# ---------------------------------------------------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Today:
    count: int                              # Shorts uploaded today
    goal: int
    segments: list[str]                     # the channel of each filled ring piece, in upload order (at most goal)
    left: int                               # still to go (0 when hit)
    text: str                               # "2 more to hit today's goal" / "Goal hit. Want a bonus one?"
    split: str                              # "2 Lou · 1 Mike · 1 Flick707" ('' with none)
    split_counts: list[tuple[str, int]]     # [(channel, n)] in channel order, only channels with uploads
    hit: bool


def today(uploads: Iterable[Upload], now: datetime | None = None, goal: int = DAY_GOAL) -> Today:
    """Today's ring: one piece per Short uploaded today (``now``'s day), in upload order."""
    day = _day(_local(now))
    goal = max(1, int(goal))
    todays = [(t, ch) for t, ch in _clean(uploads) if _day(t) == day]
    counts = Counter(ch for _, ch in todays)
    split_counts = [(ch, counts[ch]) for ch in channel_order(counts)]
    count = len(todays)
    left = max(0, goal - count)
    hit = count >= goal
    return Today(count=count, goal=goal, segments=[ch for _, ch in todays[:goal]], left=left,
                 text="Goal hit. Want a bonus one?" if hit else f"{left} more to hit today's goal",
                 split=" · ".join(f"{n} {short_name(ch)}" for ch, n in split_counts), split_counts=split_counts,
                 hit=hit)


# ---------------------------------------------------------------------------------------------------------------------
# this month
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DayCell:
    day: int                                # 1..31
    count: int
    kind: str                               # "past" | "today" | "future"
    tip: str                                # "Oct 12 · 5 Shorts" / "Oct 12 · 1 Short" / a day to come: "Oct 25"
    level: float                            # 0..1: the colour strength (0: none)


@dataclass(frozen=True)
class Month:
    label: str                              # "October"
    count: int                              # this month's Shorts so far
    goal: int
    days: list[DayCell]
    pace: str                               # "▲ 21 ahead of September" ... ('' with nothing to compare with)
    ahead: bool                             # not behind last month by the same day: ahead or level (green)
    behind: bool = False                    # fewer than last month by the same day (amber)
    diff: int = 0                           # this month - last month, up to the same day
    previous: str = ""                      # "September"
    year: int = 0
    month: int = 0


def _month_len(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def level(count: int, day_goal: int = DAY_GOAL) -> float:
    """A day's colour strength, 0..1: 0 without a Short, LEVEL_FLOOR + the rest by the share of the daily goal."""
    if count <= 0:
        return 0.0
    return round(min(1.0, LEVEL_FLOOR + (1 - LEVEL_FLOOR) * count / max(1, int(day_goal))), 4)


def month(uploads: Iterable[Upload], now: datetime | None = None, goal: int = MONTH_GOAL,
          day_goal: int = DAY_GOAL) -> Month:
    """This month (``now``'s): the count, a cell per day, and the pace: this month so far against last month up to
    the same day (the day clamped to last month's length: on Oct 31 all of September); ahead (green) = not behind.
    No upload before this month: no pace (pace '', ahead and behind False)."""
    today_ = _day(_local(now))
    y, m = today_.year, today_.month
    days_in = _month_len(y, m)
    ups = _clean(uploads)
    per_day = Counter(_day(t) for t, _ in ups)
    short = MONTHS[m - 1][:3]
    cells = []
    for d in range(1, days_in + 1):
        n = per_day.get(date(y, m, d), 0)
        if d > today_.day:
            cells.append(DayCell(d, n, "future", f"{short} {d}", level(n, day_goal)))
        else:
            cells.append(DayCell(d, n, "today" if d == today_.day else "past", f"{short} {d} · {_shorts(n)}",
                                 level(n, day_goal)))
    first = date(y, m, 1)
    count = sum(n for d, n in per_day.items() if d.year == y and d.month == m)
    so_far = sum(n for d, n in per_day.items() if first <= d <= today_)
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    previous = MONTHS[pm - 1]
    upto = date(py, pm, min(today_.day, _month_len(py, pm)))
    before = sum(n for d, n in per_day.items() if date(py, pm, 1) <= d <= upto)
    if not any(d < first for d in per_day):
        return Month(MONTHS[m - 1], count, int(goal), cells, "", False, False, 0, previous, y, m)
    diff = so_far - before
    if diff > 0:
        pace = f"▲ {diff} ahead of {previous}"
    elif diff < 0:
        pace = f"▼ {-diff} behind {previous}"
    else:
        pace = f"= level with {previous}"
    return Month(MONTHS[m - 1], count, int(goal), cells, pace, diff >= 0, diff < 0, diff, previous, y, m)


# ---------------------------------------------------------------------------------------------------------------------
# this week
# ---------------------------------------------------------------------------------------------------------------------

def week(uploads: Iterable[Upload], now: datetime | None = None,
         channels: Iterable[str] | None = None) -> dict[str, list[int]]:
    """{channel: [7 counts, Monday .. Sunday]} of the week ``now`` is in, in channel order. ``channels``: exactly these
    (all zero when they uploaded nothing); default: LouLaughs, MikeMemesYou, Flick707 and any other channel that
    uploaded this week."""
    day = _day(_local(now))
    monday = day - timedelta(days=day.weekday())
    wanted = None if channels is None else {_canon(c) for c in channels}
    counts: dict[str, list[int]] = {ch: [0] * 7 for ch in (wanted if wanted is not None else CHANNEL_ORDER)}
    for t, ch in _clean(uploads):
        k = (_day(t) - monday).days
        if 0 <= k < 7 and (wanted is None or ch in wanted):
            counts.setdefault(ch, [0] * 7)[k] += 1
    return {ch: counts[ch] for ch in channel_order(counts)}


def week_total(counts: Sequence[int] | Mapping[str, Sequence[int]]) -> str:
    """'12 this week' -- of one channel's 7 counts, or of every channel in week()'s dict."""
    if isinstance(counts, Mapping):
        n = sum(sum(v) for v in counts.values())
    else:
        n = sum(counts)
    return f"{n} this week"
