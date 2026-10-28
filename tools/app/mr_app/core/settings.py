"""The app's settings on this computer (night 11): one plain JSON object in %APPDATA%\\MovieRecaps\\settings.json
(MOVIERECAPS_SETTINGS moves the file, MOVIERECAPS_APPDATA the folder -- the tests use both). Each computer has its
own: the PC and the laptop save the finished projects to different paths of the same folder (machine.py).

* "Save projects to" (``save_to``): "" = this computer's default -- D:\\Shared\\Projects on the PC,
  \\\\192.168.101.11\\Shared\\Projects on the laptop (``save_root``).
* Which computer this is (``machine``): "auto" (machine.py finds out), "pc" or "laptop"; the host-name table
  (``pc_names`` / ``laptop_names``) for computers whose names differ.
* Torrents (day 12): "Save torrents to" (`torrent_dir`, "" = D:\\Shared\\Downloads\\Torrents on the PC, the same
  folder over the network on the laptop) and "Keep sharing after a download" (`torrent_keep_sharing`, off).
* Run 20: the scene's piece enhanced with Topaz on the laptop (core.enhance): "Enhance the scene on the laptop first"
  (``enhance_send``), "Enhance pieces for the PC with Topaz" (``enhance_topaz``) -- None = this computer's default
  (the PC sends, the laptop enhances) -- and the Enhance folder (``enhance_dir``, "" = this computer's default).
* Animations on/off, the intro, the daily and monthly goals, how often the Shorts are checked for uploads.

A missing file is the defaults. A broken one gives the defaults too and is kept as settings.json.bad, so nothing
is lost. Keys this version does not know are kept when it saves (a newer app on this computer may have written
them). Saved atomically: a temp file next to it, then moved over the old one."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from . import machine as M

ENV = "MOVIERECAPS_SETTINGS"            # the settings file itself (the tests)
APPDATA_ENV = "MOVIERECAPS_APPDATA"     # the folder of the app's small files on this computer (the tests)
FILE = "settings.json"
MACHINES = ("auto", M.PC, M.LAPTOP)


@dataclass
class Settings:
    save_to: str = ""                  # "Save projects to"; "" = this computer's default (save_root)
    machine: str = "auto"              # "auto" | "pc" | "laptop"
    animations: bool = True
    intro: bool = True
    daily_goal: int = 6                # Shorts a day (the motivation ring)
    monthly_goal: int = 150            # Shorts a month
    upload_check_minutes: int = 30     # how often the Shorts tabs are read for uploads
    pc_names: list[str] = field(default_factory=list)       # host names that are the PC ...
    laptop_names: list[str] = field(default_factory=list)   # ... and the laptop (both say "Gvidas" tonight)
    torrent_dir: str = ""              # "Save torrents to"; "" = this computer's default (torrent.default_dir)
    torrent_keep_sharing: bool = False  # "Keep sharing after a download" (off: sharing stops when it is finished)
    console_open: bool = True          # night 15: the queue's "What it's doing now" panel open (remembered)
    look: str = "mono"                 # run 18: "mono" (Mono + red) | "classic" (Settings -> Classic look)
    cache_clean: bool = True           # run 19: the tool's old cache files go by themselves (core.cache_clean)
    enhance_send: bool | None = None   # run 20: "Enhance the scene on the laptop first" (None: on on the PC only)
    enhance_topaz: bool | None = None  # run 20: "Enhance pieces for the PC with Topaz" (None: on on the laptop only)
    enhance_dir: str = ""              # run 20: the Enhance folder ("" = this computer's default: core.enhance.share_dir)
    extra: dict = field(default_factory=dict, repr=False, compare=False)   # keys of the file this version does not know

    def to_dict(self) -> dict:
        """The file's JSON object: the keys kept from the file first, then this version's own."""
        out = dict(self.extra)
        for f in fields(self):
            if f.name != "extra":
                v = getattr(self, f.name)
                out[f.name] = list(v) if isinstance(v, list) else v
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        """A Settings from the file's object: a value of the wrong kind falls back to its default, unknown keys go
        to ``extra``."""
        base = cls()
        known = {f.name for f in fields(cls)} - {"extra"}
        kw: dict[str, Any] = {}
        for name in known:
            if name in d:
                kw[name] = _valid(name, d[name], getattr(base, name))
        return cls(**kw, extra={k: v for k, v in d.items() if k not in known})


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    if isinstance(v, bool):
        return default
    try:
        n = int(float(v)) if isinstance(v, (int, float, str)) else default
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, n))


def normalize_folder(v: str) -> str:
    """Day 17: a folder as Windows writes it -- "//192.168.101.11/Shared/x" and "D:/a/b" with "\\" (aria2c and
    some Windows calls trip over "/"); quotes and spaces around it dropped; "" stays "" (this computer's default)."""
    s = (v or "").strip().strip('"').strip()
    if not s or os.sep != "\\":
        return s
    s = s.replace("/", "\\")
    unc = s.startswith("\\\\")
    s = re.sub(r"\\{2,}", r"\\", s)
    s = ("\\" + s) if unc else s
    return s.rstrip("\\") if len(s) > 3 else s


def _valid(name: str, v: Any, default: Any) -> Any:
    if name in ("save_to", "torrent_dir", "enhance_dir"):
        return normalize_folder(v) if isinstance(v, str) else default
    if name == "machine":
        m = str(v).strip().lower() if isinstance(v, str) else ""
        return m if m in MACHINES else default
    if name in ("animations", "intro", "torrent_keep_sharing", "console_open", "cache_clean"):
        return v if isinstance(v, bool) else default
    if name in ("enhance_send", "enhance_topaz"):
        return v if isinstance(v, bool) or v is None else default
    if name == "daily_goal":
        return _int(v, default, 1, 1000)
    if name == "monthly_goal":
        return _int(v, default, 1, 100000)
    if name == "upload_check_minutes":
        return _int(v, default, 1, 24 * 60)
    if name in ("pc_names", "laptop_names"):
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else default
    if name == "look":
        return v if v in ("mono", "classic") else default
    return v


# ---------------------------------------------------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------------------------------------------------

_lock = threading.Lock()


def app_dir() -> Path:
    """The folder of the app's small files on this computer: MOVIERECAPS_APPDATA, else %APPDATA%\\MovieRecaps."""
    env = os.environ.get(APPDATA_ENV, "").strip()
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA", "").strip()
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "MovieRecaps"


def path() -> Path:
    """MOVIERECAPS_SETTINGS, else settings.json in ``app_dir()`` (%APPDATA%\\MovieRecaps)."""
    env = os.environ.get(ENV, "").strip()
    return Path(env) if env else app_dir() / FILE


def _read(p: Path, tries: int = 10) -> dict | None:
    """The file's object; None when there is no file. ValueError when it is broken (not JSON, not an object). A read
    that meets the moment another program replaces the file is tried again."""
    for k in range(tries):
        try:
            text = p.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.05)
            continue
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"settings.json is not JSON: {e}") from e
        if not isinstance(d, dict):
            raise ValueError("settings.json does not hold an object")
        return d
    return None


def load() -> Settings:
    """This computer's settings; the defaults when there is no file or it is broken (then kept as
    settings.json.bad)."""
    p = path()
    with _lock:
        try:
            d = _read(p)
        except ValueError:
            try:
                os.replace(p, p.with_name(p.name + ".bad"))
            except OSError:
                pass
            return Settings()
        except OSError:
            return Settings()
    return Settings() if d is None else Settings.from_dict(d)


def save(s: Settings) -> Path:
    """Write the settings atomically. Keys in the file this version does not know are kept (also ones written after
    ``s`` was loaded)."""
    p = path()
    with _lock:
        try:
            disk = _read(p) or {}
        except (ValueError, OSError):
            disk = {}
        known = {f.name for f in fields(Settings)} - {"extra"}
        data = {k: v for k, v in disk.items() if k not in known}
        data.update(s.to_dict())
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            for k in range(40):
                try:
                    os.replace(tmp, p)
                    break
                except PermissionError:
                    if k == 39:
                        raise
                    time.sleep(0.05)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    return p


def save_root(s: Settings | None = None) -> Path:
    """Where finished projects are published ("Save projects to"): the setting, else this computer's default."""
    s = s if s is not None else load()
    if (s.save_to or "").strip():
        return Path(os.path.expandvars(os.path.expanduser(s.save_to.strip())))
    return M.default_save_root(M.which(s))
