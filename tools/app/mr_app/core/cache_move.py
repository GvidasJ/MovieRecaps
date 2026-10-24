"""THE TOOL'S CACHE ON D: (day 17, task 1).

C: went from 50 GB to 23 GB free in 1.5 hours, mostly the tool's saved analysis in work\\cache (the picture proxies,
the RAW index). So on a computer with a D: drive the cache lives in ``D:\\mr_cache`` and ``work\\cache`` is a
directory junction to it: the tool, the check-all copies (work\\snapX), the tests and the test cases all keep using
``work\\cache`` and find the very same files, nothing in the tool changes.

* The folder: ``MOVIERECAPS_CACHE_DIR`` when set, else ``D:\\mr_cache`` when D: is a fixed local drive, else none
  (a computer without D: keeps work\\cache where it is).
* ``move()``: only when no MovieRecaps run is going (no python started from this project runs match_cuts or the app);
  the files are moved with robocopy (every file copied, checked, then removed from C:), then ``work\\cache`` becomes
  the junction. Nothing is lost: when anything goes wrong, the files left in work\\cache stay there and the junction
  is not made.
* ``real_dir()``: where the cache really is (the junction followed) -- the free-space checks count it on D:.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

DEFAULT = "D:\\mr_cache"
DRIVE_FIXED = 3


def work_cache() -> Path:
    from . import paths
    return paths.work_dir() / "cache"


def real_dir() -> Path:
    """work\\cache with its junction followed (D:\\mr_cache after the move)."""
    p = work_cache()
    try:
        return Path(os.path.realpath(p))
    except OSError:
        return p


def is_junction(p: Path) -> bool:
    try:
        return bool(os.path.isjunction(p)) or Path(p).is_symlink()
    except (OSError, AttributeError):
        return False


def _fixed(drive: str) -> bool:
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(drive.rstrip("\\") + "\\") == DRIVE_FIXED
    except Exception:  # noqa: BLE001 - not Windows
        return False


def target() -> Path | None:
    """Where the cache should be: MOVIERECAPS_CACHE_DIR, else D:\\mr_cache on a computer with a fixed D:, else None."""
    env = os.environ.get("MOVIERECAPS_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    return Path(DEFAULT) if _fixed("D:") else None


def busy() -> list[str]:
    """The MovieRecaps processes that could be writing to the cache (the app, a match_cuts run, a test session)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
                              "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return []
    me = {str(os.getpid()), str(os.getppid())}          # (a venv's python.exe starts the real one as its child)
    found = []
    for line in out.splitlines():
        pid, _sp, cmd = line.strip().partition(" ")
        low = cmd.lower()
        if pid not in me and any(w in low for w in ("match_cuts", "movierecaps.pyw", "mr_app", "pytest")):
            found.append(line.strip())
    return found


def _count(folder: Path) -> tuple[int, int]:
    n = b = 0
    for dirpath, _dirs, files in os.walk(folder):
        for f in files:
            try:
                b += os.stat(os.path.join(dirpath, f)).st_size
                n += 1
            except OSError:
                pass
    return n, b


def state() -> str:
    """One plain line: where the cache is."""
    src, dst = work_cache(), target()
    if is_junction(src):
        return f"the tool's cache is on {real_dir()} (work\\cache points there)"
    if dst is None:
        return "the tool's cache stays in work\\cache (no D: drive on this computer)"
    return f"the tool's cache is still in work\\cache on C: (it should be in {dst})"


def move(dry: bool = False, log=print) -> bool:
    """work\\cache -> target() with a junction left in its place (module docstring). True when the cache is there."""
    src, dst = work_cache(), target()
    if os.environ.get("MOVIERECAPS_NO_CACHE_MOVE") == "1":       # the app's tests: never move this PC's real cache
        return is_junction(src)
    if dst is None:
        log(state())
        return False
    if is_junction(src):
        log(state())
        return True
    who = busy()
    if who:
        log("not moved: MovieRecaps is running -- close it (and any match_cuts run) first: " + "; ".join(who)[:300])
        return False
    n, b = _count(src) if src.is_dir() else (0, 0)
    log(f"moving the tool's cache ({n} files, {b / 1024 ** 3:.1f} GB) from {src} to {dst} ...")
    if dry:
        return False
    dst.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        # /MOVE: each file is removed from C: only after it was copied whole; /E keeps the folders; /COPY:DAT keeps
        # the times (the tool's caches never look at times, but a later check can)
        r = subprocess.run(["robocopy", str(src), str(dst), "/E", "/MOVE", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:2",
                            "/MT:8", "/NP", "/NFL", "/NDL", "/NJH"], capture_output=True, text=True)
        if r.returncode >= 8:                    # robocopy: 0-7 is success, 8+ is a failure
            log(f"not moved: robocopy failed ({r.returncode}): {r.stdout[-600:]}")
            return False
        left_n, _left_b = _count(src) if src.exists() else (0, 0)
        if left_n:
            log(f"not moved: {left_n} files are still in {src} (in use?) -- nothing was lost; run this again")
            return False
        try:
            for dirpath, _dirs, _files in sorted(os.walk(src), key=lambda t: -len(t[0])):
                os.rmdir(dirpath)                # only empty folders are left (checked above)
        except OSError as e:
            log(f"not moved: {src} could not be removed ({e}) -- the files are all in {dst}")
            return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(src), str(dst)], capture_output=True, text=True)
    if r.returncode != 0 or not is_junction(src):
        log(f"the files are in {dst}, but the junction could not be made: {r.stdout}{r.stderr} -- "
            f"make it by hand: mklink /J \"{src}\" \"{dst}\"")
        return False
    n2, b2 = _count(dst)
    log(f"done: {n2} files, {b2 / 1024 ** 3:.1f} GB in {dst}; work\\cache points there")
    return True


if __name__ == "__main__":                  # .venv\Scripts\python tools\app\mr_app\core\cache_move.py [--dry]
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mr_app.core import cache_move as _m
    sys.exit(0 if _m.move(dry="--dry" in sys.argv) else 1)
