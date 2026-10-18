"""THE QUOTE LINE under the top bar: one quote for each part of the day, from an editable quotes.txt.

quotes.txt has one quote per line, ``Morning | the quote | who said it`` (who said it can be left out; lines starting
with # are notes). The day has three parts -- Morning 05:00-12:00, Afternoon 12:00-18:00, Evening 18:00-05:00 (the
hours after midnight are the evening of the day before) -- plus ``Goal`` lines for when the daily goal is hit. Each
part shows one quote picked from its list and keeps it for the whole part, even after a restart (the pick is kept in
a small JSON), and never the quote that part showed the day before (when it has two or more); the parts go through
their whole list before a quote comes back. A click shows the next quote of the same part, kept for the rest of the
part. A Goal line stays the same for the day.

quotes.txt is read again when it changes (its time stamp or size), so edits show up within seconds. It may live on
the shared folder (a network path on the laptop): the window's calls never wait for it longer than READ_WAIT_S -- a
slow read goes on in the background and its quotes show when it is done. The app's own quotes
(assets/quotes_default.txt) stand in for a part with no quotes and for a file that is missing.

The QuoteBook is for the GUI thread (``current``, ``next``, ``goal`` are quick); ``ensure_quotes_file`` touches the
folder it is given (maybe the share): call it from a worker thread."""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

PARTS = ("Morning", "Afternoon", "Evening", "Goal")
DAY_PARTS = PARTS[:3]
MORNING_AT, AFTERNOON_AT, EVENING_AT = 5, 12, 18      # the hours the parts start
QUOTES_NAME = "quotes.txt"
STATE_NAME = "quotes_state.json"
STATE_VERSION = 1
DEFAULT_FILE = paths.ASSETS / "quotes_default.txt"
RECHECK_S = 5.0          # quotes.txt is looked at again at most this often ...
READ_WAIT_S = 0.25       # ... and a look waits at most this long (a share that does not answer: in the background)

_ALIASES = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening", "night": "Evening",
            "goal": "Goal", "goal hit": "Goal"}
_QUOTE_PAIRS = ('""', "“”", "''", "‘’", "«»", "„“", "„”")
_BUILT_IN = {"Morning": ("I just never thought I couldn't.", "McQueen"),
             "Afternoon": ("Be so good they can't ignore you.", "Steve Martin"),
             "Evening": ("Hard work beats talent when talent doesn't work hard.", "Tim Notke"),
             "Goal": ("Goal hit. Now the bonus ones are pure profit.", "")}


@dataclass(frozen=True)
class Quote:
    part: str                 # Morning, Afternoon, Evening or Goal
    text: str
    author: str = ""

    @property
    def tag(self) -> str:
        """The small tag next to the quote: the part of the day, 'Goal hit' for a Goal line."""
        return "Goal hit" if self.part == "Goal" else self.part

    @property
    def shown(self) -> str:
        """The quote in curly quotes: '“I just never thought I couldn't.”'."""
        return f"“{self.text}”"

    @property
    def by(self) -> str:
        """'— McQueen' ('' without an author)."""
        return f"— {self.author}" if self.author else ""


# ---------------------------------------------------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------------------------------------------------

def _squash(s: str) -> str:
    return " ".join(str(s or "").split())


def _unquote(s: str) -> str:
    """One pair of quote marks around the whole quote taken off (the line shows its own)."""
    for pair in _QUOTE_PAIRS:
        if len(s) >= 2 and s[0] == pair[0] and s[-1] == pair[1]:
            return s[1:-1].strip()
    return s


def _note(notes: list[str] | None, text: str) -> None:
    log.info("quotes: %s", text)
    if notes is not None:
        notes.append(text)


def parse(text: str | None, notes: list[str] | None = None) -> dict[str, list[Quote]]:
    """quotes.txt's text -> {part: [Quote]} for every part (an empty list when a part has none). # lines and blank
    lines are skipped, extra spaces dropped, the author may be left out ('Morning | quote'), quote marks around a
    quote and a dash before the author are taken off, the part's name in any case ('night' = Evening). A line without
    a known part or without a quote, and a quote twice in one part, are left out with a note in ``notes``."""
    out: dict[str, list[Quote]] = {p: [] for p in PARTS}
    seen: dict[str, set[str]] = {p: set() for p in PARTS}
    for no, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.replace("﻿", "").strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            _note(notes, f"line {no}: no part -- write it as 'Morning | the quote | who said it'; left out")
            continue
        fields = [_squash(f) for f in line.split("|")]
        part = _ALIASES.get(fields[0].casefold())
        if part is None:
            _note(notes, f"line {no}: '{fields[0]}' is not a part (Morning, Afternoon, Evening or Goal); left out")
            continue
        quote = _unquote(fields[1])
        author = " | ".join(f for f in fields[2:] if f).lstrip("—–-~ ").strip()
        if not quote:
            _note(notes, f"line {no}: no quote after '{fields[0]} |'; left out")
            continue
        if quote.casefold() in seen[part]:
            _note(notes, f"line {no}: this {part} quote is there already; left out")
            continue
        seen[part].add(quote.casefold())
        out[part].append(Quote(part, quote, author))
    return out


@lru_cache(maxsize=4)
def _defaults(path: str, key: tuple[int, int] | None) -> dict[str, list[Quote]]:
    try:
        parsed = parse(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        parsed = {p: [] for p in PARTS}
    for p in PARTS:
        if not parsed[p]:
            parsed[p] = [Quote(p, *_BUILT_IN[p])]
    return parsed


def default_quotes() -> dict[str, list[Quote]]:
    """The app's own quotes (assets/quotes_default.txt; a few built in when even that is missing)."""
    try:
        st = DEFAULT_FILE.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    return {p: list(v) for p, v in _defaults(str(DEFAULT_FILE), key).items()}


def ensure_quotes_file(folder: str | os.PathLike) -> Path:
    """``<folder>\\quotes.txt``, made from the app's default when it is missing (never overwritten -- also not when
    two computers make it at once); the app's default file itself when the folder cannot be written. Touches the
    folder (maybe the share): call it from a worker thread."""
    target = Path(folder) / QUOTES_NAME
    try:
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        text = DEFAULT_FILE.read_text(encoding="utf-8")
        try:
            with open(target, "x", encoding="utf-8", newline="\r\n") as f:     # Notepad-friendly line ends
                f.write(text)
        except FileExistsError:
            pass
        return target
    except OSError as e:
        log.info("quotes.txt not made in %s: %s", folder, e)
        return target if target.is_file() else DEFAULT_FILE


# ---------------------------------------------------------------------------------------------------------------------
# the parts of the day
# ---------------------------------------------------------------------------------------------------------------------

def part_of(dt: datetime | None = None) -> tuple[date, str]:
    """(the day, the part) of a time: 05:00-11:59 Morning, 12:00-17:59 Afternoon, 18:00-04:59 Evening -- 00:30 is the
    Evening of the day before. The clock time as it is (local)."""
    dt = dt or datetime.now()
    if dt.hour < MORNING_AT:
        return dt.date() - timedelta(days=1), "Evening"
    if dt.hour < AFTERNOON_AT:
        return dt.date(), "Morning"
    if dt.hour < EVENING_AT:
        return dt.date(), "Afternoon"
    return dt.date(), "Evening"


def part_ends(dt: datetime | None = None) -> datetime:
    """When the part of ``dt`` ends (the next 05:00, 12:00 or 18:00; with dt's own time zone) -- for a timer that
    fades in the next part's quote."""
    dt = dt or datetime.now()
    for hour in (MORNING_AT, AFTERNOON_AT, EVENING_AT):
        if dt.hour < hour:
            return dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return (dt + timedelta(days=1)).replace(hour=MORNING_AT, minute=0, second=0, microsecond=0)


APPDATA_ENV = "MOVIERECAPS_APPDATA"


def app_data_dir() -> Path:
    """This computer's small state files: the MOVIERECAPS_APPDATA folder (the tests), else %APPDATA%\\MovieRecaps."""
    env = os.environ.get(APPDATA_ENV, "").strip()
    if env:
        return Path(env)
    base = os.environ.get("APPDATA", "").strip() or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "MovieRecaps"


def default_state_path() -> Path:
    """<app data>\\quotes_state.json (this computer's: app_data_dir())."""
    return app_data_dir() / STATE_NAME


# ---------------------------------------------------------------------------------------------------------------------
# the book
# ---------------------------------------------------------------------------------------------------------------------

_UNSET = object()


class QuoteBook:
    """The quotes of ``quotes_path`` (default: the app's own) and the picks, kept in ``state_path`` (default
    app_data_dir()\\quotes_state.json: MOVIERECAPS_APPDATA, else %APPDATA%\\MovieRecaps). ``notes``: what the last
    read of the file left out, in plain words. ``rng``: a random.Random for the picks (tests)."""

    def __init__(self, quotes_path: str | os.PathLike | None = None, state_path: str | os.PathLike | None = None,
                 rng: random.Random | None = None):
        self.quotes_path = Path(quotes_path) if quotes_path is not None else DEFAULT_FILE
        self.state_path = Path(state_path) if state_path is not None else default_state_path()
        self.notes: list[str] = []
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._look_lock = threading.Lock()
        self._key: object = _UNSET
        self._quotes: dict[str, list[Quote]] = default_quotes()
        self._looked = 0.0
        self._reader: threading.Thread | None = None
        self._state = self._load_state()

    # -- quotes.txt ---------------------------------------------------------------------------------------------------

    def _read(self) -> None:
        """Look at quotes.txt and read it when it changed (a worker of _look, or the caller's thread)."""
        try:
            st = self.quotes_path.stat()
            key: object = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        if key == self._key:
            return
        notes: list[str] = []
        parsed: dict[str, list[Quote]] = {p: [] for p in PARTS}
        if key is None:
            notes.append(f"{self.quotes_path} is not there: the app's quotes are shown")
        else:
            try:
                raw = self.quotes_path.read_bytes()
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252", errors="replace")     # saved as ANSI
                parsed = parse(text, notes)
            except OSError as e:
                notes.append(f"{self.quotes_path} cannot be read ({e}): the app's quotes are shown")
                key = None
        defaults = default_quotes()
        for p in PARTS:
            if not parsed[p]:
                if key is not None:
                    notes.append(f"no {p} quotes in {self.quotes_path.name}: the app's are shown")
                parsed[p] = defaults[p]
        with self._lock:
            self._quotes, self.notes, self._key = parsed, notes, key

    def _look(self) -> None:
        """quotes.txt read again when it changed -- at most every RECHECK_S, waiting at most READ_WAIT_S for it (safe
        from several threads at once: one look at a time)."""
        with self._look_lock:
            now = time.monotonic()
            if self._key is not _UNSET and now - self._looked < RECHECK_S:
                return
            if self._reader is not None and self._reader.is_alive():
                reader = self._reader                               # a slow look is still going: no second one
            else:
                self._looked = now
                reader = self._reader = threading.Thread(target=self._read, name="quotes-read", daemon=True)
                reader.start()
        reader.join(READ_WAIT_S)

    def quotes(self) -> dict[str, list[Quote]]:
        """The quotes by part as they are now (a part with none: the app's)."""
        self._look()
        with self._lock:
            return {p: list(v) for p, v in self._quotes.items()}

    # -- the picks ----------------------------------------------------------------------------------------------------

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("parts", {})
                return data
        except (OSError, ValueError):
            pass
        return {"version": STATE_VERSION, "parts": {}}

    def _save(self) -> None:
        tmp = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as e:                     # not remembered: the quote may change at the next start, no more
            log.info("quote picks not saved (%s): %s", self.state_path, e)
            try:
                tmp.unlink()
            except OSError:
                pass

    def _slot(self, part: str) -> dict:
        if part == "Goal":
            slot = self._state.setdefault("goal", {})
        else:
            slot = self._state.setdefault("parts", {}).setdefault(part, {})
        return slot if isinstance(slot, dict) else {}

    @staticmethod
    def _find(quotes: list[Quote], text: str | None) -> Quote | None:
        return next((q for q in quotes if q.text == text), None) if text else None

    def _pick(self, part: str, quotes: list[Quote], slot: dict, day: date) -> Quote:
        """A new pick for (day, part): at random from the quotes not shown yet in this round, never the one shown last
        (the day before) when there are two or more."""
        if not quotes:
            return Quote(part, "", "")
        today = day.isoformat()
        last = (slot.get("last") if slot.get("day") == today else slot.get("text")) or ""
        texts = {q.text for q in quotes}
        used = [u for u in slot.get("used") or [] if u in texts]
        cands = [q for q in quotes if q.text != last and q.text not in used]
        if not cands:                                               # the round is over: a new round
            used = []
            cands = [q for q in quotes if q.text != last] or list(quotes)
        q = self._rng.choice(cands)
        slot.clear()
        slot.update(day=today, text=q.text, last=last, used=used + [q.text])
        return q

    def _get(self, part: str, day: date) -> Quote:
        quotes = self.quotes()[part]
        with self._lock:
            slot = self._slot(part)
            q = self._find(quotes, slot.get("text")) if slot.get("day") == day.isoformat() else None
            if q is None:                                           # a new part, or its quote was taken out
                q = self._pick(part, quotes, slot, day)
                self._save()
            return q

    def current(self, now: datetime | None = None) -> Quote:
        """The quote of the part of the day ``now`` is in (default: now): the same for the whole part."""
        day, part = part_of(now)
        return self._get(part, day)

    def next(self, now: datetime | None = None) -> Quote:
        """A click: the next quote of the same part (after the one shown, in the file's order), shown for the rest of
        the part."""
        day, part = part_of(now)
        cur = self._get(part, day)
        quotes = self.quotes()[part]
        if len(quotes) < 2:
            return cur
        with self._lock:
            k = next((i for i, q in enumerate(quotes) if q.text == cur.text), -1)
            q = quotes[(k + 1) % len(quotes)]
            slot = self._slot(part)
            used = [u for u in slot.get("used") or [] if u != q.text] + [q.text]
            slot.update(text=q.text, used=used)
            self._save()
            return q

    def goal(self, now: datetime | None = None) -> Quote:
        """A Goal line (for when the daily goal is hit): the same for the whole day (the calendar day)."""
        return self._get("Goal", (now or datetime.now()).date())
