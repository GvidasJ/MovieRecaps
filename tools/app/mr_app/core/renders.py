"""YOUR MEDIA ENCODER RENDERS SORTED BY DAY (night 15, task 7): every video Media Encoder made (its own log,
Documents\\Adobe\\Adobe Media Encoder\\<version>\\AMEEncodingLog.txt: "Output File: ..." then "File Successfully
Encoded") goes into a ``renders\\<the day it was made>\\`` folder next to where it was written -- D:\\Shared\\xasd.mp4
made on 22 October -> D:\\Shared\\renders\\2026-10-22\\xasd.mp4 -- the old ones too.

Never deleted: each is MOVED (renamed) on its own drive, a name already there gets "-2"; every move is written to
``renders\\moved.log`` (when, from, to) so it can be put back. A render finished less than SETTLE_S ago, one in use, or
one already in a renders\\<day> folder is left where it is. The app does it at its start and every SWEEP_EVERY_S.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

SETTLE_S = 120.0                      # a render this new may still be written / played: next time
SWEEP_EVERY_S = 10 * 60
FOLDER = "renders"
MOVED_LOG = "moved.log"

_OUT = re.compile(r"^\s*-\s*Output File:\s*(.+?)\s*$")
_OK = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d\d:\d\d [AP]M) : File Successfully Encoded")
_DAY = re.compile(r"^\d{4}-\d\d-\d\d$")


def ame_logs() -> list[Path]:
    """Media Encoder's logs of this computer (every version)."""
    docs = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "Adobe" / "Adobe Media Encoder"
    try:
        return sorted(docs.glob("*/AMEEncodingLog.txt"))
    except OSError:
        return []


def finished_renders(text: str) -> list[tuple[Path, datetime]]:
    """(output file, when it was finished) of every render the log says was encoded successfully."""
    out: list[tuple[Path, datetime]] = []
    pending: Path | None = None
    for ln in text.replace("\x00", "").splitlines():
        m = _OUT.match(ln)
        if m:
            pending = Path(m.group(1))
            continue
        k = _OK.match(ln)
        if k and pending is not None:
            try:
                out.append((pending, datetime.strptime(k.group(1), "%m/%d/%Y %I:%M:%S %p")))
            except ValueError:
                pass
            pending = None
    return out


def in_day_folder(p: Path) -> bool:
    return p.parent.parent.name.lower() == FOLDER and bool(_DAY.match(p.parent.name))


def target(p: Path, when: datetime) -> Path:
    d = p.parent / FOLDER / when.strftime("%Y-%m-%d")
    t, k = d / p.name, 2
    while t.exists():
        t = d / f"{p.stem}-{k}{p.suffix}"
        k += 1
    return t


def plan(logs: list[Path] | None = None, now: float | None = None) -> list[tuple[Path, Path]]:
    """The moves to make: (render, its place in renders\\<day>\\); each render once (its latest log entry)."""
    now = time.time() if now is None else now
    found: dict[str, tuple[Path, datetime]] = {}
    for log in ame_logs() if logs is None else logs:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for p, when in finished_renders(text):
            found[str(p).lower()] = (p, when)
    moves: list[tuple[Path, Path]] = []
    for p, when in found.values():
        try:
            if in_day_folder(p) or not p.is_file() or now - p.stat().st_mtime < SETTLE_S:
                continue
        except OSError:
            continue
        moves.append((p, target(p, datetime.fromtimestamp(p.stat().st_mtime) if when is None else when)))
    return moves


def sort_renders(logs: list[Path] | None = None, now: float | None = None) -> list[tuple[Path, Path]]:
    """Every finished render moved into its day's folder; the moves made (and written to renders\\moved.log)."""
    done: list[tuple[Path, Path]] = []
    for src, dst in plan(logs, now):
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                continue
            os.rename(src, dst)                 # a rename on its own drive: never a copy, never a delete
        except OSError:
            continue                            # in use (Premiere plays it) / no right: next time
        done.append((src, dst))
        try:
            with (dst.parent.parent / MOVED_LOG).open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{src}\t{dst}\n")
        except OSError:
            pass
    return done


def put_back(log: Path) -> int:
    """Undo: every move written in a renders\\moved.log put back where it was (when that place is free)."""
    n = 0
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for ln in reversed(lines):
        parts = ln.split("\t")
        if len(parts) != 3:
            continue
        src, dst = Path(parts[1]), Path(parts[2])
        if dst.is_file() and not src.exists():
            try:
                os.rename(dst, src)
                n += 1
            except OSError:
                pass
    return n
