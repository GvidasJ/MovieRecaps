"""Premiere Pro on this computer: its program file (where Windows says it is -- the 'App Paths' entry its installer
writes -- else the newest 'Adobe Premiere Pro <year>' in the usual folders), and opening a run's edit in it (open_xml).

Each run's edit gets its own Premiere project next to it: ``<name>.prproj`` -- the name of the run's XML copy
(video10_edit.prproj; a project's edit: <project>_edit.prproj; day 13, a project named after its title:
``<title>_edit.xml`` with the sequence called after the title, and ``<title>.prproj``) -- a copy of an empty project
Premiere Pro 2024 made itself (assets\\premiere\\empty_project.prproj). A project of the run folder that you saved
with the edit in it is opened again rather than a new one (after a title change its name is the old title's). What
open_xml does depends on what Premiere is doing (each way tested with Premiere Pro 2024, night 11):

* not running: Premiere is started WITH that project (as a double-click on it in Explorer does);
* running on its Home screen (no project open): the project is handed to it and it opens.
  Then, once Premiere's window title names the project, the edit (<name>.xml) is handed over and Premiere imports it
  into that project (its sequence and media; the user saves it). A project that holds the edit already -- saved
  after the import -- opens as it is: nothing is imported twice; an edit made again after the project's last save
  is imported once more (one more sequence). The project stays where the edit is (never "the folder of the last
  project opened", where Premiere puts a project it makes from an XML itself).
* running with a project open (its title names the .prproj): the edit is imported into that open project; when that
  project is the run's own and holds the edit already -- saved, or not saved yet (the title's "*": the empty project
  changed), or handed over from here since Premiere started -- nothing again.

A file is handed over the way Explorer does it: Premiere's program run with the file as its argument (the running
Premiere takes it over). Premiere answered files handed over through PowerShell's Start-Process with "This file path
does not exist on disk at this location"; run this way it never did (Home screen, fresh or after closing projects).
Not installed: the run folder opens instead. open_xml waits for Premiere (up to minutes): call it in the background."""
from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

EXE = "Adobe Premiere Pro.exe"
MAIN_CLASS = "Premiere Pro"          # the window class of Premiere's main window ("Adobe Premiere Pro 2024 - <project>")
START_WAIT_S = 180.0                 # Premiere's start -- or a project handed to it -- open at most this long
SETTLE_S = 8.0                       # a project just opened: this long before the edit is handed over (it still loads)
IMPORT_WAIT_S = 30.0                 # the import watched this long (the title's "*": the project changed)
DIALOG_S = 30.0                      # a message Premiere shows this long unanswered ends the waiting
STEP_S = 0.5
PROGRESS = re.compile(r"(?i)^(opening|importing|import files|loading|saving|reading|conforming|indexing)\b")
HOME = ("Premiere Pro is on its Home screen and did not open {name}: open it there (File > Open Project, it is in "
        "the run folder) or close Premiere, then press Open in Premiere again")

_LOCK = threading.Lock()             # one open_xml at a time (two quick presses: the second finds the project open)
_HANDED: dict[str, tuple[int, int]] = {}      # project -> (Premiere's process id, the edit's mtime_ns) imported here


def _registered() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{EXE}") as k:
                v, _ = winreg.QueryValueEx(k, "")
                p = Path(str(v).strip().strip('"'))
                if p.is_file():
                    return p
        except OSError:
            continue
    return None


def _roots() -> list[Path]:
    out = []
    for drive in ("C:", "D:", "E:", "F:"):
        out += [Path(drive + "\\Program Files\\Adobe"), Path(drive + "\\Adobe")]
    pf = os.environ.get("ProgramFiles")
    if pf:
        out.insert(0, Path(pf) / "Adobe")
    return out


def find() -> Path | None:
    """Premiere Pro's program file, or None."""
    reg = _registered()
    if reg is not None:
        return reg
    found = []
    for adobe in _roots():
        try:
            for d in adobe.glob("Adobe Premiere Pro*"):
                exe = d / EXE
                if exe.is_file():
                    m = re.search(r"(\d{4})", d.name)
                    found.append((int(m.group(1)) if m else 0, str(exe)))
        except OSError:
            continue
    return Path(max(found)[1]) if found else None


def template() -> Path:
    """The empty project Premiere Pro 2024 made (File > New Project, its defaults, nothing in it): every run's own
    project starts as a copy of it."""
    from . import paths
    return paths.assets_dir() / "premiere" / "empty_project.prproj"


# ---------------------------------------------------------------------------------------------------------------------
# what Premiere is doing: its processes and windows (Windows' own calls, no PowerShell)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class State:
    """Premiere now: running (a process), its main window's title (None: no main window yet), the project that title
    names (None: its Home screen), the titles of its other windows (a message waiting for an answer, a progress
    box), its main window hung (busy)."""
    running: bool = False
    title: str | None = None
    project: Path | None = None
    dialogs: list[str] = field(default_factory=list)
    hung: bool = False
    pid: int = 0
    hwnd: int = 0

    @property
    def home(self) -> bool:
        return self.title is not None and self.project is None

    @property
    def changed(self) -> bool:
        """The title's "*": the open project has changes not saved yet."""
        return bool(self.title) and self.title.rstrip().endswith("*")

    @property
    def message(self) -> str | None:
        """A message box waiting for an answer (not a progress box: "Opening project: ...", "Import Files")."""
        return next((d for d in self.dialogs if not PROGRESS.match(d)), None)


def project_in(title: str | None) -> Path | None:
    """The project a Premiere window title names ("Adobe Premiere Pro 2024 - C:\\...\\video10_edit.prproj *"); None
    for its Home screen ("Adobe Premiere Pro 2024")."""
    if not title or " - " not in title:
        return None
    rest = title.split(" - ", 1)[1]
    i = rest.lower().rfind(".prproj")
    return Path(rest[:i + len(".prproj")].strip()) if i >= 0 else None


def _key(p: Path | str) -> str:
    s = str(p)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return os.path.normcase(os.path.normpath(s))


def same_file(a: Path | str | None, b: Path | str | None) -> bool:
    return a is not None and b is not None and _key(a) == _key(b)


def _pids() -> list[int]:
    """The process ids of Premiere Pro (its program file's name)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == EXE.lower():
                out.append(int(e.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _windows(pids: list[int]) -> list[tuple[int, int, str, str, bool]]:
    """(window, process id, title, class, hung) of the visible top-level windows of these processes."""
    if os.name != "nt" or not pids:
        return []
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsHungAppWindow.argtypes = [wintypes.HWND]
    u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    want, out = set(pids), []

    def each(h, _l):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in want and u32.IsWindowVisible(h):
            text = ctypes.create_unicode_buffer(u32.GetWindowTextLengthW(h) + 2)
            u32.GetWindowTextW(h, text, len(text))              # (another process's title: no message sent, no hang)
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(h, cls, 256)
            out.append((int(h or 0), int(pid.value), text.value, cls.value, bool(u32.IsHungAppWindow(h))))
        return True

    u32.EnumWindows(proc(each), 0)
    return out


def state() -> State:
    """What Premiere shows now (see State)."""
    pids = _pids()
    if not pids:
        return State()
    st = State(running=True, pid=pids[0])
    wins = _windows(pids)
    main = [w for w in wins if w[3] == MAIN_CLASS and w[2].lower().startswith("adobe premiere pro")] or \
        [w for w in wins if w[3] != "#32770" and w[2].lower().startswith("adobe premiere pro")]
    if main:
        st.hwnd, st.pid, st.title, _cls, st.hung = main[0]
        st.project = project_in(st.title)
    st.dialogs = [w[2].strip() for w in wins if w[2].strip() and (not main or w[0] != main[0][0])]
    return st


def window_up() -> bool:
    """Premiere runs and shows its main window."""
    return state().title is not None


# ---------------------------------------------------------------------------------------------------------------------
# the run's files
# ---------------------------------------------------------------------------------------------------------------------

_SEQ_NAME = re.compile(r"(<sequence\b[^>]*>\s*<name>)(.*?)(</name>)", re.S)


def sequence_name_in(text: str) -> str | None:
    """The name of the XML's (first) sequence, as written (None when it has none)."""
    m = _SEQ_NAME.search(text or "")
    if m is None:
        return None
    from xml.sax.saxutils import unescape
    return unescape(m.group(2))


def rename_sequence(text: str, name: str) -> str:
    """The XML with its (first) sequence called ``name`` (day 13: Premiere names the render after the sequence) --
    only that <name>'s text changes, every other byte stays."""
    from xml.sax.saxutils import escape
    m = _SEQ_NAME.search(text)
    if m is None:
        raise ValueError("the edit's sequence has no name")
    return text[:m.start(2)] + escape(name) + text[m.end(2):]


def write_copy(xml: Path, out: Path, sequence: str) -> Path:
    """``out`` (1_edit.xml itself: ``out`` == ``xml``) = 1_edit.xml with its sequence called ``sequence``, written
    atomically, with 1_edit.xml's times (Open in Premiere compares the edit's time with the project's last save: a
    rename made now must not count as an edit made again)."""
    st = os.stat(xml)
    text = Path(xml).read_bytes().decode("utf-8")
    data = rename_sequence(text, sequence).encode("utf-8")
    tmp = out.with_name(f".{out.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, out)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return out


def edit_for_premiere(xml: Path) -> Path:
    """What Open in Premiere imports (day 14): the run's 1_edit.xml itself -- ONE XML per edit, no copies (day 13's
    ``<title>_edit.xml`` next to it made a project hold the edit twice: "Recreated Edit (Premiere)" and the title) --
    its sequence called after the project's title without emoji (names.sequence_name: Premiere names the render after
    it; a project from before day 13 keeps the tool's own name). Only that <name>'s text changes, and the file keeps
    its times (needs_import: never "the edit made again"). The app's old copies of the edit go (renames.drop_copies;
    never your own files). Returns 1_edit.xml."""
    from . import names
    xml = Path(xml)
    _fname, seq = names.copy_name(xml.parent)
    if seq is not None:
        try:
            with open(xml, "rb") as f:
                head = f.read(65536).decode("utf-8", "replace")
            if sequence_name_in(head) not in (None, seq):
                write_copy(xml, xml, seq)
        except (OSError, ValueError, UnicodeDecodeError):
            pass                                      # (not the tool's XML: as it is)
    try:
        from . import renames
        renames.drop_copies(xml.parent)
    except Exception:  # noqa: BLE001 - an old copy that stays does no harm
        pass
    return xml


named_copy = edit_for_premiere                        # (day 13's name, kept for its callers)


def project_for(xml: Path) -> Path:
    """The run's own Premiere project next to its 1_edit.xml: '<title>.prproj' for a project whose names follow its
    title (day 13), else night 10's '<project>_<run>.prproj' / '<run>_edit.prproj'."""
    from . import names
    xml = Path(xml)
    fname, seq = names.copy_name(xml.parent)
    if seq is not None:
        return xml.with_name(names.project_name(fname))
    return xml.with_name(Path(fname).with_suffix(".prproj").name)


def is_own_project(prproj: str | os.PathLike) -> bool:
    """The app made this .prproj (Open in Premiere: an empty project the edit is imported into): night 10's
    '<name>_edit.prproj', day 13's '<title>.prproj' (next to its '<title>_edit.xml', or the name the run's project
    has now), or one on the project's list of the app's projects (renames.own_projects: day 14 keeps no XML copy to
    tell them by)."""
    from . import names
    p = Path(prproj)
    if p.stem.lower().endswith("_edit"):
        return True
    try:
        if p.with_name(p.stem + names.COPY_SUFFIX).is_file():
            return True
        if project_for(p.parent / "1_edit.xml").name.lower() == p.name.lower():
            return True
        from . import renames
        return p.name.lower() in renames.own_projects(p.parent)
    except Exception:  # noqa: BLE001
        return False


def saved_own_project(run_dir: str | os.PathLike) -> Path | None:
    """The newest project the app made in the run folder that you saved with the edit in it -- Open in Premiere opens
    that one again (after a title change its name is not the title's any more), never a new empty one."""
    found = []
    try:
        for f in Path(run_dir).iterdir():
            if f.suffix.lower() == ".prproj" and f.is_file() and is_own_project(f) and holds_edit(f):
                found.append((f.stat().st_mtime, f))
    except OSError:
        return None
    return max(found)[1] if found else None


def holds_edit(prproj: Path) -> bool:
    """The project as saved holds a sequence (the edit was imported and the project saved since). A project that
    cannot be read counts as holding it (never an import into something unknown)."""
    try:
        data = Path(prproj).read_bytes()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
    except (OSError, EOFError, gzip.BadGzipFile):
        return True
    return b"<Sequence ObjectUID=" in data


def needs_import(prproj: Path, xml: Path) -> bool:
    """The edit goes into the project: it holds no sequence yet (new, or never saved after the import), or the edit was
    made again after the project's last save."""
    if not holds_edit(prproj):
        return True
    try:
        return os.stat(xml).st_mtime > os.stat(prproj).st_mtime + 1.0
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------------------------------
# opening the edit
# ---------------------------------------------------------------------------------------------------------------------

def _launch(exe: Path, *args: Path) -> None:
    """Premiere's program with these files: a new Premiere when none runs, else a hand-over to the running one (as
    Explorer does it; never a network folder as the working folder)."""
    subprocess.Popen([str(exe), *map(str, args)], cwd=str(exe.parent))


def _show_folder(folder: Path) -> None:
    try:
        os.startfile(str(folder))                                   # noqa: S606 - the folder in Explorer
    except OSError:
        pass


def _front(hwnd: int) -> None:
    """Premiere's window to the front (best effort: Windows may only flash it)."""
    if os.name != "nt" or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.WinDLL("user32")
        u32.IsIconic.argtypes = [wintypes.HWND]
        u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)                                 # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - only a convenience
        pass


_clock, _sleep = time.monotonic, time.sleep


def _wait(ok, timeout: float) -> State:
    """state() until ok(state) -- or the time is up, or a message box has stood DIALOG_S unanswered; the last
    state."""
    t0, since = _clock(), None
    while True:
        st = state()
        if ok(st):
            return st
        now = _clock()
        since = (now if since is None else since) if st.message else None
        if now - t0 >= timeout or (since is not None and now - since >= DIALOG_S):
            return st
        _sleep(STEP_S)


def _settle() -> State:
    """A project just opened: SETTLE_S, then until its window answers again (it loads)."""
    _sleep(SETTLE_S)
    return _wait(lambda s: not s.hung, 60.0)


def _import(exe: Path, xml: Path, st: State, into: Path) -> tuple[State, bool]:
    """Hand the edit over: the running Premiere imports it into its open project (``into``). (the state after, seen
    imported): seen when the title shows the project changed within IMPORT_WAIT_S -- or it showed changes before."""
    _launch(exe, xml)
    try:
        _HANDED[_key(into)] = (st.pid, os.stat(xml).st_mtime_ns)
    except OSError:
        pass
    if st.changed:
        return st, True
    after = _wait(lambda s: s.changed, IMPORT_WAIT_S)
    return after, after.changed


def _handed_here(prproj: Path, xml: Path, st: State) -> bool:
    """This edit went into this project from here since Premiere started (it may not be saved yet)."""
    try:
        return _HANDED.get(_key(prproj)) == (st.pid, os.stat(xml).st_mtime_ns)
    except OSError:
        return False


def _unseen(st: State) -> str:
    """The end of a sentence when the import did not show within IMPORT_WAIT_S."""
    if st.message:
        return f" -- Premiere shows a message ({st.message!r}): answer it"
    return " -- look at Premiere: it did not show the import yet"


# ---------------------------------------------------------------------------------------------------------------------
# day 17 (task 3): the SceneProof song's real Clip Gain, written into the saved project (core.song_gain); run 18
# (task 1): every edit's A1 chain too -- each clip's own Clip Gain, one Volume 0 dB, Hard Limiter + Amplify (a1_chain)
# ---------------------------------------------------------------------------------------------------------------------

SONG_WATCH_S = 12 * 3600.0           # run 19: watched as long as Premiere runs (at most this; was 30 minutes)
SONG_CLOSE_S = 90.0                  # Premiere closed (its project saved) within this long, else tried after a new save
SONG_STATUS: dict[str, str] = {}     # project -> what happened (plain words)
_SONG_WATCHING: set[str] = set()
# run 19 (task 2): the edit's sound for its row -- the XML brings no extra Volume any more, so until the app's save step
# has written the Clip Gain the A1 clips play at the RAW's own level: the row says so plainly
SOUND: dict[str, dict] = {}          # project -> {"state": WAITING / SET / NOT_SET, "text": plain words, "at": time}
WAITING, SET, NOT_SET = "waiting", "set", "not set"
NOT_SET_YET = "the sound isn't set yet: press Ctrl+S in Premiere"


def _sound(k: str, state_: str, text: str) -> str:
    SOUND[k] = {"state": state_, "text": text, "at": time.time()}
    SONG_STATUS[k] = text
    return text


def _done_or_not(k: str, out: str, proj: Path, xml: Path, why: str) -> str:
    """The state after the save step ran (``out``: song_gain.fix's words) -- set, else not set and ``why``."""
    from . import song_gain
    if "written into" in out or (not out and proj.is_file() and song_gain.holds_edit_sound(proj, xml)
                                 and not song_gain.needs_fix(proj, xml)):
        return _sound(k, SET, "the sound is set: " + (out or "Clip Gain, Hard Limiter (2 dB) and Amplify (+3 dB) are "
                                                      "in the project"))
    return _sound(k, NOT_SET, f"{NOT_SET_YET} -- {out or why}")


_PROJ_OF: dict[str, str] = {}        # run 19: an edit (its 1_edit.xml) -> the project Open in Premiere put it into
_PRESET = "CorrectVolume"


def sound_note(xml: str | os.PathLike) -> dict | None:
    """Run 19: the sound of a run's edit in the project Open in Premiere put it into, for the row: {"state", "text"};
    None when the app did not open it in Premiere since it started. Two dictionary looks: any thread may call it."""
    k = _PROJ_OF.get(_key(Path(xml)))
    return SOUND.get(k) if k else None


def song_before_open(proj: Path, xml: Path) -> str:
    """A saved project whose song clips have no Clip Gain yet (Premiere doesn't hold it now): written now."""
    from . import song_gain
    if proj.is_file() and song_gain.needs_fix(proj, xml):
        done = song_gain.fix(proj, xml)
        _done_or_not(_key(proj), done, proj, xml, "it could not be written")
        return done
    return ""


def _close(st: State) -> bool:
    """Premiere's main window asked to close (as its X does); True when Premiere is gone within SONG_CLOSE_S."""
    if os.name != "nt" or not st.hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.WinDLL("user32")
        u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.PostMessageW(st.hwnd, 0x0010, 0, 0)                     # WM_CLOSE
    except Exception:  # noqa: BLE001
        return False
    t0 = _clock()
    while _clock() - t0 < SONG_CLOSE_S:
        if not _pids():
            return True
        _sleep(1.0)
    return False


def _mtime(p: Path) -> float | None:
    try:
        return os.stat(p).st_mtime
    except OSError:
        return None


def song_watch(exe: Path, proj: Path, xml: Path, timeout: float = SONG_WATCH_S, t_import: float | None = None) -> str:
    """After the edit went into its own project: when you have saved it (the title without "*", the file newer
    than the import) Premiere is closed (nothing is lost: everything is saved), A1's chain and the song's real Clip
    Gain and Volume 8.5 dB are written into the project, and Premiere opens it again. Premiere closed by you: written
    (when it was saved), not opened again. Run 19 -- it holds on as long as Premiere runs: another project open in
    Premiere meanwhile is waited out (back to this one and saved: done then), and when Premiere does not close (a
    message?) it is tried again after your next save; the row says "the sound isn't set yet: press Ctrl+S in
    Premiere" with why (``SOUND``) until it is done. Returns the last words."""
    from . import song_gain
    k = _key(proj)
    if t_import is None:
        t_import = _mtime(proj) or 0.0
    t0 = _clock()
    refused: float | None = None          # the project's time when Premiere did not close (tried after a newer save)
    _sound(k, WAITING, NOT_SET_YET)
    while _clock() - t0 < timeout:
        _sleep(2.0)
        st = state()
        mt = _mtime(proj)
        saved = mt is not None and mt > t_import
        if not st.running:
            out = song_gain.fix(proj, xml) if saved or song_gain.needs_fix(proj, xml) else ""
            return _done_or_not(k, out, proj, xml, "Premiere was closed before the project was saved: press Open in "
                                                   "Premiere, then Ctrl+S")
        if st.title is None or st.project is None:
            continue
        if not same_file(st.project, proj):
            if SOUND.get(k, {}).get("text", "").find("another project") < 0:
                _sound(k, NOT_SET, f"{NOT_SET_YET} -- another project is open in Premiere ({st.project.name}): "
                                   "press Open in Premiere on this video, then Ctrl+S")
            continue
        if saved and not st.changed and not st.message and song_gain.needs_fix(proj, xml):
            if refused is not None and mt is not None and mt <= refused:
                continue                                         # (the same save that did not close: wait for a new one)
            if not _close(st):
                refused = mt
                _sound(k, NOT_SET, f"{NOT_SET_YET} -- Premiere did not close (a message?): answer it, then press "
                                   "Ctrl+S again")
                continue
            out = song_gain.fix(proj, xml)
            _launch(exe, proj)
            _wait(lambda s: same_file(s.project, proj), START_WAIT_S)
            return _done_or_not(k, (out + " -- Premiere opened it again") if out else "", proj, xml,
                                "it could not be written")
        if saved and not song_gain.needs_fix(proj, xml):
            if song_gain.holds_edit_sound(proj, xml):
                return _done_or_not(k, "", proj, xml, "")
            if SOUND.get(k, {}).get("state") != NOT_SET:             # saved without the edit's clips as it made
                _sound(k, NOT_SET, f"{NOT_SET_YET} -- the saved project does not hold the edit's A1 clips as the "
                                   "edit made them (moved or cut by hand?): its sound is yours to set")
            continue
        if SOUND.get(k, {}).get("state") == NOT_SET and same_file(st.project, proj) and refused is None:
            _sound(k, WAITING, NOT_SET_YET)                      # (back to this project after another one)
    return _sound(k, NOT_SET, f"{NOT_SET_YET} -- not saved within {timeout / 3600:g} hours")


def _song_later(exe: Path, proj: Path, xml: Path, saved_counts: bool = False) -> str:
    """song_watch in its own thread when the edit has a song with its Clip Gain or A1 clips with their chain; the words
    for the message ('' without either). ``saved_counts``: the project as saved now counts (it was open with the edit
    already) -- else only a save after now."""
    from . import song_gain
    if not song_gain.has_work(xml) or _key(proj) in _SONG_WATCHING:
        return ""
    if saved_counts and not song_gain.needs_fix(proj, xml):
        if proj.is_file():
            _done_or_not(_key(proj), "", proj, xml, "")             # (its saved project has the sound already)
        return ""

    def run() -> None:
        try:
            song_watch(exe, proj, xml, t_import=0.0 if saved_counts else None)
        finally:
            _SONG_WATCHING.discard(_key(proj))
    _SONG_WATCHING.add(_key(proj))
    threading.Thread(target=run, name="song_gain", daemon=True).start()
    what = "A1's Clip Gain, Hard Limiter and Amplify" + (
        " and the song's Clip Gain (+5 / -1 dB) and Volume 8.5 dB" if song_gain.pieces(xml)[1] else "")
    return f" -- then the app closes Premiere once, writes {what} into the saved project and opens it again"


def open_xml(xml: Path, wait_s: float = START_WAIT_S) -> str:
    """Open a run's 1_edit.xml in Premiere Pro, in the run's own project next to it (see the module's text); returns
    what happened (plain words). Waits for Premiere: call it in the background."""
    with _LOCK:
        return _open(Path(xml), wait_s)


def _open(xml: Path, wait_s: float) -> str:
    exe = find()
    if exe is None:
        _show_folder(xml.parent)
        return "Premiere Pro was not found on this computer: the run folder is open -- import 1_edit.xml yourself"
    xml = named_copy(xml)
    proj = saved_own_project(xml.parent) or project_for(xml)
    _PROJ_OF[_key(xml)] = _key(proj)                                     # (run 19: the row's sound line)
    st = state()
    if st.running and st.title is None:                                  # starting (or closing): its window waited for
        st = _wait(lambda s: s.title is not None or not s.running, wait_s)
        if st.running and st.title is None:
            if st.message:
                return f"Premiere Pro shows a message ({st.message!r}): answer it, then press Open in Premiere again"
            return "Premiere Pro runs but shows no window -- wait for it (or close it), then press Open in Premiere again"

    # (1) a project open in Premiere
    if st.project is not None:
        if not same_file(st.project, proj):
            other = st.project
            st, seen = _import(exe, xml, st, other)
            _front(st.hwnd)
            from . import song_gain
            if song_gain.has_work(xml):                                  # run 19: the app sets the sound only in the
                _PROJ_OF[_key(xml)] = _key(other)                        # edit's own project, never in yours
                _sound(_key(other), NOT_SET, f"{NOT_SET_YET} -- the edit went into {other.name} (the project open in "
                                             "Premiere): the app sets the sound only in the edit's own project -- "
                                             "close it, then press Open in Premiere (or Audio Gain > Normalize All "
                                             f"Peaks to 0 dB and your {_PRESET} preset by hand)")
            return (f"The edit is imported into {other.name}, the project open in Premiere -- save it there"
                    + ("" if seen else _unseen(st)))
        if not needs_import(proj, xml) or st.changed or _handed_here(proj, xml, st):     # ("*": the empty project
            _front(st.hwnd)                                                                 #  changed -- by the import)
            return f"{proj.name} is open in Premiere already, with the edit" + _song_later(exe, proj, xml, True)
        st, seen = _import(exe, xml, st, proj)
        _front(st.hwnd)
        return f"{proj.name} is open in Premiere: the edit is imported into it -- save it (Ctrl+S)" + \
            ("" if seen else _unseen(st)) + _song_later(exe, proj, xml)

    # (2) not running, (3) its Home screen: the run's own project, opened by Premiere, then the edit into it
    made = False
    if not proj.is_file():
        try:
            shutil.copyfile(template(), proj)
            made = True
            try:                                      # (the app's own project: never taken for your fix)
                from . import renames
                renames.remember_project(proj.parent, proj.name)
            except Exception:  # noqa: BLE001
                pass
        except OSError as e:
            _show_folder(xml.parent)
            return (f"{proj.name} could not be made next to the edit ({e.strerror or e}): the run folder is open -- "
                    f"import {xml.name} into a project yourself (File > Import)")
    again = not made and holds_edit(proj) and needs_import(proj, xml)       # an edit made again since the last save
    need = made or needs_import(proj, xml)
    song = "" if made or need else song_before_open(proj, xml)    # day 17: saved, song clips without Clip Gain
    started = not st.running
    _launch(exe, proj)
    st = _wait(lambda s: same_file(s.project, proj), wait_s)
    if not same_file(st.project, proj):
        _show_folder(xml.parent)
        if st.message:
            return (f"Premiere Pro shows a message ({st.message!r}) instead of opening {proj.name}: answer it, then "
                    "press Open in Premiere again (the run folder is open)")
        if not started and st.home:
            return HOME.format(name=proj.name)
        return (f"Premiere Pro did not open {proj.name} in time: open it yourself (the run folder is open), then "
                "press Open in Premiere again")
    who = "Premiere Pro started with" if started else "Premiere Pro opened"
    if not need:
        _front(st.hwnd)
        from . import song_gain
        if not song and song_gain.has_work(xml):                         # run 19: say it on the row (set before)
            _done_or_not(_key(proj), "", proj, xml, "press Ctrl+S in Premiere")
        return f"{who} {proj.name} (next to the edit) as you saved it -- the edit is in it already" + \
            (f" ({song})" if song else "")
    st, seen = _import(exe, xml, _settle(), proj)
    _front(st.hwnd)
    what = "the edit made again (one more sequence)" if again else "the edit"
    return f"{who} {proj.name} (next to the edit) and imported {what} into it -- save it (Ctrl+S)" + \
        ("" if seen else _unseen(st)) + _song_later(exe, proj, xml)
