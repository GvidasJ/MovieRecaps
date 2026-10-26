"""THE TOOL'S CACHE CLEANS ITSELF (run 19, task 4: the laptop).

The laptop has no D: drive, so its ``work\\cache`` (the tool's saved analysis: proxies, the RAW index, the sound,
the captions ... -- all of it made again by the next run that needs it) grew to 91 GB and left C: with 12 GB free. So:

* on a computer **without a D: drive** (the cache stays on C:) the cache files **older than 7 days** go;
* when the cache's drive is **below 30 GB free** (any computer -- on the PC the cache lives on D:\\mr_cache, so only
  when D: itself gets that low) the files **older than 2 days** go too, the oldest first, until the drive is back
  above 30 GB; the newest are always kept (a file used in the last 2 days stays even then).

"Older" counts from a file's last use (its last write or read, whichever is later). Only files inside the cache folder
(``work\\cache``, its junction followed) are ever touched -- never a project, a RAW, a run folder or anything outside
it, and never a folder link inside it. ``clean()`` runs when the app starts and every hour (Settings: "Clean the
tool's cache by itself", on by default) and says what it freed (``last()``: shown in Settings). Run it from a worker
thread."""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DAY = 86400.0
OLD_DAYS = 7.0                    # without a D: drive: the files older than this go
LOW_DAYS = 2.0                    # the drive below LOW_FREE: older than this too (the oldest first)
LOW_FREE = 30 * 1024 ** 3
EVERY_S = 3600.0                  # the app runs it at its start and this often
STATE = "cache_clean.json"        # in the app's folder: what the last clean did (Settings shows it)


@dataclass
class Report:
    when: str = ""
    folder: str = ""
    why: str = ""                 # "no D: drive" / "the drive is low" / "" (nothing to do)
    files: int = 0
    freed: int = 0
    free_after: int = 0
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        """Settings' line, in plain words."""
        from .publish import plain_size
        if not self.when:
            return "has not run yet"
        if not self.files:
            return f"{self.when}: nothing to clean ({self.why or 'the cache is on D: and D: has room'})"
        return (f"{self.when}: freed {plain_size(self.freed)} ({self.files} cache file(s); {self.why}) -- "
                f"{plain_size(self.free_after)} free now")


def has_d_drive() -> bool:
    """A fixed D: drive on this computer (the laptop has none)."""
    from .cache_move import _fixed
    try:
        return _fixed("D:")
    except Exception:  # noqa: BLE001
        return os.path.isdir("D:\\")


def _free(p: Path) -> int:
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 1 << 62


def _used(st: os.stat_result) -> float:
    return max(st.st_mtime, st.st_atime)


def _files(root: Path) -> list[tuple[float, int, Path]]:
    """(last use, size, path) of every file inside ``root`` -- no folder link is followed."""
    out = []
    for d, dirs, names in os.walk(root, followlinks=False):
        keep = []
        for x in dirs:
            p = Path(d) / x
            try:
                if not p.is_symlink() and not _is_junction(p):
                    keep.append(x)
            except OSError:
                continue
        dirs[:] = keep
        for n in names:
            p = Path(d) / n
            try:
                st = p.lstat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                out.append((_used(st), int(st.st_size), p))
    return out


def _is_junction(p: Path) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


def plan(root: Path, now: float | None = None, d_drive: bool | None = None,
         free: int | None = None) -> tuple[str, list[tuple[float, int, Path]]]:
    """(why, the files to delete, oldest first) for the cache folder ``root`` (module docstring)."""
    now = time.time() if now is None else now
    d_drive = has_d_drive() if d_drive is None else d_drive
    free = _free(root) if free is None else free
    if d_drive and free >= LOW_FREE:
        return "", []                                    # (the PC: D: has room -- not even a look at the files)
    files = sorted(_files(root)) if root.is_dir() else []
    out: list[tuple[float, int, Path]] = []
    whys = []
    if not d_drive:
        out = [f for f in files if now - f[0] > OLD_DAYS * DAY]
        if out:
            whys.append(f"no D: drive: the files not used for {OLD_DAYS:g} days")
    if free + sum(s for _t, s, _p in out) < LOW_FREE:
        have = {p for _t, _s, p in out}
        more, gain = [], free + sum(s for _t, s, _p in out)
        for f in files:                                  # the oldest first, until the drive has room again
            if f[2] in have or now - f[0] <= LOW_DAYS * DAY:
                continue
            if gain >= LOW_FREE:
                break
            more.append(f)
            gain += f[1]
        if more:
            whys.append(f"the drive below {LOW_FREE // 1024 ** 3} GB free: the oldest files not used for "
                        f"{LOW_DAYS:g} days")
        out = sorted(out + more)
    return "; ".join(whys), out


def state_file() -> Path:
    from .settings import app_dir
    return app_dir() / STATE


def last() -> Report:
    try:
        d = json.loads(state_file().read_text(encoding="utf-8"))
        return Report(**{k: d[k] for k in Report.__dataclass_fields__ if k in d})
    except (OSError, ValueError, TypeError):
        return Report()


def clean(root: Path | None = None, now: float | None = None, d_drive: bool | None = None,
          save: bool = True) -> Report:
    """Delete what ``plan`` says (each file checked again: still inside the cache, not used since) and keep what
    was done for Settings. Never raises."""
    from .cache_move import real_dir
    folder = Path(root) if root is not None else real_dir()
    rep = Report(when=time.strftime("%d %b %H:%M"), folder=str(folder))
    try:
        why, todo = plan(folder, now, d_drive)
        rep.why = why or ("the cache is on D: and D: has room" if (d_drive if d_drive is not None else
                                                                    has_d_drive()) else "nothing old enough")
        base = os.path.normcase(os.path.realpath(folder)).rstrip("\\/") + os.sep
        for used, size, p in todo:
            try:
                real = os.path.normcase(os.path.realpath(p))
                st = p.lstat()
                if not real.startswith(base) or not stat.S_ISREG(st.st_mode) or _used(st) > used + 1.0:
                    continue                             # (moved, linked, or used meanwhile: kept)
                p.unlink()
                rep.files += 1
                rep.freed += size
            except FileNotFoundError:
                continue
            except OSError as e:
                if len(rep.errors) < 5:
                    rep.errors.append(f"{p.name}: {e.strerror or e}")
        rep.free_after = _free(folder)
    except Exception as e:  # noqa: BLE001 - a clean that can not run never stops anything
        rep.errors.append(f"{type(e).__name__}: {e}")
    if save:
        try:
            f = state_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(json.dumps(asdict(rep), indent=1), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass
    return rep
