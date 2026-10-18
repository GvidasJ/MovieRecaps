"""Two computers never work on one project at once (night 11): a long-work lock, ``<project>\\.movierecaps.lock``,
held while a computer makes the edit, publishes the project, renders ... It is a small JSON file: {computer
("pc" / "laptop"), host, id (this Windows installation's MachineGuid -- both computers are called "Gvidas"), user,
pid, what, started, heartbeat, token}. projects.py's small project.json.lock (15 s) still guards each write of
project.json; this one is for work that takes minutes or hours.

* ``WorkLock(project_folder, what="edit")``: ``acquire()`` makes the file with O_CREAT | O_EXCL, so only one program
  on one of the two computers can make it; a heartbeat thread rewrites its heartbeat every 5 minutes (and touches
  the file); ``release()`` removes it. Or ``with WorkLock(folder, "edit"): ...``.
* Held by someone else: ``LockedError`` in plain words -- "the laptop is working on this project since 02:44
  (edit)".
* Stale -- no heartbeat for 2 hours (the newer of the file's time and its heartbeat field), or a lock of this
  computer whose program is not running any more -- it is taken over, and ``taken_over`` says so: "taken over from
  the laptop (since 02:44, edit): no heartbeat for 2 h 10 min". Of two computers taking one stale lock over at the
  same moment only one may: the taker first makes a claim, .movierecaps.lock.<the stale lock's token>.claim1, with
  O_CREAT | O_EXCL (the one file operation Windows does once only -- a rename is not: three programs renaming one
  file at the same moment are all told it worked), checks the lock is still that stale one, removes it and makes its
  own (O_EXCL again). A claim is never removed while taking over (Clean up removes it an hour later); a claim older
  than a minute was left by a taker that stopped half-way, and the next one (claim2 ...) is made.
* This program's own lock (the same computer and process) can be taken again (reentrant); the file goes when the
  last holder releases it.
* ``holder(project_folder)``: who holds it (for the board), or None.

Only Python's own file operations are used, so UNC paths work. On a network folder every call can wait for the
network: call them from a worker thread."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import machine as M
from . import projects as P

LOCK_FILE = ".movierecaps.lock"
STALE_S = 2 * 3600.0              # no heartbeat for this long: whoever held it stopped (crashed, asleep, unplugged)
HEARTBEAT_S = 5 * 60.0            # the holder rewrites its heartbeat this often
TRIES = 10                        # make / take over the file at most this often in one acquire()
UNREADABLE_WAIT_S = 0.5           # a lock file that cannot be read yet may be being written: read again this long
CLAIM_STALE_S = 60.0              # a takeover claim older than this was left by a taker that stopped half-way
CLAIM_WAIT_S = 2.0                # another computer's fresh claim: wait this long for its takeover to finish


class LockedError(RuntimeError):
    """Someone else is working on the project; the message says who, since when and on what (plain words)."""

    def __init__(self, message: str, holder: dict | None = None):
        super().__init__(message)
        self.holder = dict(holder or {})


def lock_path(project_folder: str | os.PathLike) -> Path:
    return Path(project_folder) / LOCK_FILE


# ---------------------------------------------------------------------------------------------------------------------
# reading a lock file, in plain words
# ---------------------------------------------------------------------------------------------------------------------

def _timestamp(text: Any) -> float | None:
    try:
        d = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.astimezone()).timestamp()


def _read(path: Path) -> tuple[bytes | None, dict | None, float | None]:
    """(the file's bytes, its JSON object or None when it is not one, its mtime); (None, None, None) when there is
    no lock. OSError when it cannot be read now (the network is away, another program replaces it this moment)."""
    try:
        st = os.stat(path)
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None, None
    try:
        info = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        info = None
    return raw, (info if isinstance(info, dict) else None), st.st_mtime


def _since(info: dict) -> str:
    """'02:44' today, else '17 Oct 02:44'."""
    t = _timestamp(info.get("started"))
    if t is None:
        return "some time"
    d = datetime.fromtimestamp(t)
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else f"{d.day} {d:%b %H:%M}"


def _who(info: dict, me: dict) -> str:
    """'the laptop', 'the PC', 'this computer (another program)' ..."""
    if info.get("id") and info.get("id") == me.get("id"):
        return "this computer" + ("" if info.get("pid") == me.get("pid") else " (another program)")
    kind = info.get("computer")
    if kind in M.KINDS:
        if kind == me.get("computer"):
            return f"the other computer (also a {M.label(kind)})"
        return f"the {M.label(kind)}"
    return str(info.get("host") or "another computer")


def _age(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def _pid_running(pid: Any) -> bool | None:
    """Is that process of this computer still running? None: cannot tell (then the lock is not judged by it)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid == os.getpid():
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            err = ctypes.GetLastError()
            return False if err == 87 else (True if err == 5 else None)   # no such process / access denied
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return None
            return code.value == 259                           # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 - not Windows
        return None


def _stale_why(info: dict | None, mtime: float | None, me: dict, now: float | None = None) -> str:
    """Why the lock is stale (it may be taken over), or "" while its holder is alive."""
    now = time.time() if now is None else now
    if info is None:
        if mtime is not None and now - mtime > STALE_S:
            return f"a lock file that cannot be read, untouched for {_age(now - mtime)}"
        return ""
    beats = [t for t in (mtime, _timestamp(info.get("heartbeat"))) if t is not None]
    last = max(beats) if beats else None
    if last is not None and now - last > STALE_S:
        return f"no heartbeat for {_age(now - last)}"
    if info.get("id") and info.get("id") == me.get("id") and info.get("pid") != me.get("pid"):
        if _pid_running(info.get("pid")) is False:
            return "its program is not running any more"
    return ""


def describe(info: dict | None, me: dict | None = None) -> str:
    """'the laptop is working on this project since 02:44 (edit)'."""
    if info is None:
        return "another program is starting work on this project"
    me = me if me is not None else {**M.identity(), "pid": os.getpid()}
    return f"{_who(info, me)} is working on this project since {_since(info)} ({info.get('what') or 'work'})"


def holder(project_folder: str | os.PathLike, me: dict | None = None) -> dict | None:
    """Who holds the project's lock, for the board: the lock's fields plus "text" (plain words), "stale" (may be
    taken over), "why_stale", "mine" (this program) and "this_computer"; None when nobody does."""
    try:
        raw, info, mtime = _read(lock_path(project_folder))
    except OSError:
        return None                                             # cannot be read now: the next look sees it
    if raw is None:
        return None
    me = me if me is not None else {**M.identity(), "pid": os.getpid()}
    why = _stale_why(info, mtime, me)
    out = dict(info or {})
    out.update(text=describe(info, me) if info is not None else "a lock file that cannot be read",
               stale=bool(why), why_stale=why,
               mine=bool(info) and info.get("id") == me.get("id") and info.get("pid") == me.get("pid"),
               this_computer=bool(info) and info.get("id") == me.get("id"))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the lock
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class _Held:
    """One lock file this process holds (shared by its reentrant holders)."""
    path: Path
    info: dict
    count: int = 1
    stop: threading.Event = field(default_factory=threading.Event)
    io: threading.Lock = field(default_factory=threading.Lock)
    lost: str = ""                 # set when someone else took the file over (or it could not be kept)


_held: dict[tuple, _Held] = {}
_guard = threading.Lock()


def _key(path: Path, me: dict) -> tuple:
    return os.path.normcase(os.path.abspath(path)), me.get("id"), me.get("pid")


def _write_new(path: Path, info: dict) -> None:
    """Make the lock file; FileExistsError when there is one (the one atomic step)."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        try:
            os.write(fd, (json.dumps(info, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except BaseException:
        try:
            path.unlink()                                       # never leave an empty lock behind
        except OSError:
            pass
        raise


def _replace(path: Path, info: dict) -> None:
    """Rewrite our own lock file atomically (a temp file moved over it)."""
    tmp = path.with_name(f".{LOCK_FILE}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    try:
        for k in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:                             # someone is reading it this very moment
                if k == 19:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


class WorkLock:
    """The project's long-work lock (module docstring). ``me`` / ``pid`` stand for another computer in the tests;
    by default this computer (machine.identity) and this process."""

    def __init__(self, project_folder: str | os.PathLike, what: str = "edit", *, me: dict | None = None,
                 pid: int | None = None, heartbeat_s: float = HEARTBEAT_S):
        self.folder = Path(project_folder)
        self.path = lock_path(self.folder)
        self.what = what
        self.heartbeat_s = float(heartbeat_s)
        self.taken_over = ""        # "taken over from the laptop (since 02:44, edit): no heartbeat for 2 h 10 min"
        self._me = dict(me) if me is not None else None
        self._pid = int(pid) if pid is not None else os.getpid()
        self._h: _Held | None = None          # the lock file this object holds (shared with reentrant holders)

    def __repr__(self) -> str:
        return f"WorkLock({str(self.folder)!r}, {self.what!r})"

    def __enter__(self) -> "WorkLock":
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()

    @property
    def me(self) -> dict:
        if self._me is None:
            self._me = M.identity()
        return {**self._me, "pid": self._pid}

    @property
    def held(self) -> bool:
        return self._h is not None

    @property
    def lost(self) -> str:
        """Why this lock was lost while held ("" while it is ours): the other computer took it over."""
        return self._h.lost if self._h is not None else ""

    def _info(self) -> dict:
        me, t = self.me, P.now()
        return {"computer": me.get("computer"), "host": me.get("host"), "id": me.get("id"), "user": me.get("user"),
                "pid": self._pid, "what": self.what, "started": t, "heartbeat": t, "token": uuid.uuid4().hex}

    def acquire(self) -> "WorkLock":
        """Take the lock (LockedError when someone else holds it and it is not stale)."""
        if self._h is not None:
            return self
        me = self.me
        key = _key(self.path, me)
        last: dict | None = None
        busy = False
        deadline = time.time() + CLAIM_WAIT_S
        for attempt in range(TRIES * 10):
            if attempt >= TRIES and not (busy and time.time() < deadline):
                break
            with _guard:
                h = _held.get(key)
            if h is not None and not h.lost:
                _raw, cur, _m = self._read_settled()
                with _guard:
                    if _held.get(key) is h:
                        if cur is not None and cur.get("token") == h.info.get("token"):
                            h.count += 1                        # reentrant: this process holds it already
                            self._h = h
                            return self
                        _held.pop(key, None)                    # the file is not ours any more
                        h.stop.set()
            info = self._info()
            try:
                _write_new(self.path, info)
            except PermissionError:
                time.sleep(0.05)                                # the old file is being deleted this very moment
                continue
            except FileExistsError:
                raw, cur, mtime = self._read_settled()
                last = cur
                if raw is None:
                    continue                                    # it went meanwhile: make it again
                if cur is not None and cur.get("id") == me.get("id") and cur.get("pid") == me.get("pid"):
                    self._adopt(key, cur)                       # ours from before (this process): reentrant
                    return self
                why = _stale_why(cur, mtime, me)
                if not why:
                    raise LockedError(describe(cur, me), cur)
                status = self._take_over(raw, cur, mtime)
                busy = status == "busy"
                if status == "taken":
                    who = (f"{_who(cur, me)} (since {_since(cur)}, {cur.get('what') or 'work'})"
                           if cur is not None else "a lock file that could not be read")
                    self.taken_over = f"taken over from {who}: {why}"
                elif busy:
                    time.sleep(0.05)                            # another taker is at it: see who won
                continue
            self._adopt(key, info)
            return self
        if busy:
            raise LockedError("another computer is taking this project over this very moment: try again in a "
                              "minute", last)
        raise LockedError(describe(last, me) if last else "another program keeps taking this project's lock: try "
                          "again in a minute", last)

    def _read_settled(self) -> tuple[bytes | None, dict | None, float | None]:
        """Read the lock; a file that cannot be read yet may be being written by its maker, or replaced this moment
        by its holder's heartbeat: read again briefly (OSError when it still cannot be read)."""
        t0 = time.time()
        while True:
            try:
                raw, info, mtime = _read(self.path)
            except PermissionError:
                if time.time() - t0 > UNREADABLE_WAIT_S:
                    raise
                time.sleep(0.05)
                continue
            if raw is None or info is not None or time.time() - t0 > UNREADABLE_WAIT_S:
                return raw, info, mtime
            time.sleep(0.05)

    def _claim(self, raw: bytes, info: dict | None, mtime: float | None, k: int) -> Path:
        """The k-th takeover claim of one stale lock: named after that very lock (its token; for a file that cannot be
        read, its bytes and time), so two different locks never share a claim."""
        tag = str((info or {}).get("token") or "")
        if not tag:
            tag = hashlib.sha1(raw).hexdigest() + str(int(mtime or 0))
        tag = re.sub(r"[^0-9A-Za-z]", "", tag)[:48] or "x"
        return self.path.with_name(f"{LOCK_FILE}.{tag}.claim{k}")

    def _take_over(self, judged: bytes, info: dict | None, mtime: float | None) -> str:
        """Remove the stale lock -- only after winning its claim (O_EXCL: one taker only). "taken": it is gone, make
        ours now; "busy": another taker holds the claim this very moment; "changed": the lock is not that stale one
        any more (judge it again)."""
        me = self.me
        mark = {"computer": me.get("computer"), "id": me.get("id"), "pid": self._pid, "at": P.now()}
        for k in range(1, 100):
            claim = self._claim(judged, info, mtime, k)
            try:
                _write_new(claim, mark)
                break                                           # ours
            except FileExistsError:
                try:
                    age = time.time() - os.stat(claim).st_mtime
                except OSError:
                    return "busy"
                if age <= CLAIM_STALE_S:
                    return "busy"                               # someone is taking it over right now
            except PermissionError:
                return "busy"                                   # a claim being removed this very moment
        else:
            return "busy"
        try:
            now_raw = self.path.read_bytes()
        except FileNotFoundError:
            return "taken"                                      # gone meanwhile: make ours
        except OSError:
            return "changed"
        if now_raw != judged:
            return "changed"                                    # its holder woke up, or it was taken over
        for _ in range(40):
            try:
                self.path.unlink()
                return "taken"
            except FileNotFoundError:
                return "taken"
            except PermissionError:
                time.sleep(0.02)                                # someone reads it this very moment
            except OSError:
                return "changed"
        return "changed"

    def _adopt(self, key: tuple, info: dict) -> None:
        start = False
        with _guard:
            h = _held.get(key)
            if h is not None and not h.lost and h.info.get("token") == info.get("token"):
                h.count += 1
            else:
                if h is not None:
                    h.stop.set()
                h = _Held(self.path, dict(info))
                _held[key] = h
                start = True
        self._h = h
        if start:
            threading.Thread(target=self._beat, args=(h,), daemon=True,
                             name=f"lock heartbeat {self.folder.name}").start()

    def _beat(self, h: _Held) -> None:
        """Every ``heartbeat_s``: our heartbeat into the file (and its time touched) -- unless the file is not ours
        any more (taken over after we slept for hours): then the lock is lost and nothing is written."""
        while not h.stop.wait(self.heartbeat_s):
            with h.io:
                if h.stop.is_set():
                    return
                try:
                    raw, info, _m = _read(h.path)
                    if raw is None:                             # removed by hand: made again while we work
                        try:
                            _write_new(h.path, {**h.info, "heartbeat": P.now()})
                            h.info["heartbeat"] = P.now()
                            continue
                        except FileExistsError:
                            raw, info, _m = _read(h.path)
                    if info is None or info.get("token") != h.info.get("token"):
                        h.lost = (f"{describe(info, self.me)} -- the lock was taken over" if info is not None
                                  else "the lock file was replaced by one that cannot be read")
                        return
                    h.info["heartbeat"] = P.now()
                    _replace(h.path, h.info)
                    os.utime(h.path, None)
                except OSError:
                    continue                                    # the network is away: the next beat tries again

    def heartbeat(self) -> None:
        """Beat now (the thread does it every ``heartbeat_s`` anyway)."""
        h = self._h
        if h is None or h.lost:
            return
        with h.io:
            try:
                raw, info, _m = _read(h.path)
                if info is not None and info.get("token") == h.info.get("token"):
                    h.info["heartbeat"] = P.now()
                    _replace(h.path, h.info)
                    os.utime(h.path, None)
            except OSError:
                pass                                            # the network is away: the thread tries again

    def release(self) -> bool:
        """Give the lock back; the file is removed when the last holder of this process lets go (and only when it is
        still ours). Never raises: when the folder cannot be reached now the file stays, without a heartbeat, and is
        stale after STALE_S. False only then."""
        h, self._h = self._h, None
        if h is None:
            return True
        key = _key(self.path, self.me)
        with _guard:
            h.count -= 1
            if h.count > 0:
                return True
            if _held.get(key) is h:
                _held.pop(key, None)
        h.stop.set()
        with h.io:
            for _ in range(20):
                try:
                    _raw, info, _m = _read(h.path)
                    if info is not None and info.get("token") == h.info.get("token"):
                        h.path.unlink()
                    return True
                except FileNotFoundError:
                    return True
                except PermissionError:                         # read or replaced this very moment
                    time.sleep(0.05)
                except OSError:
                    return False
            return False
