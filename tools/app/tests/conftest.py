"""The app's tests never touch this computer's real folders: every session gets its own settings file and app-data
folder (%APPDATA%\\MovieRecaps), its own projects folder (input\\downloads: a dropped RAW and competitor become a
project there) and its own "Save projects to" folder (D:\\Shared\\Projects: published projects, learn_queue.md,
requests.md, quotes.txt); Qt runs without a screen."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = Path(tempfile.mkdtemp(prefix="mr_app_tests_"))
os.environ.setdefault("MOVIERECAPS_APPDATA", str(_TMP / "appdata"))
os.environ.setdefault("MOVIERECAPS_PROJECTS", str(_TMP / "projects"))
os.environ.setdefault("MOVIERECAPS_NO_CACHE_MOVE", "1")   # day 17: setup_app's tests never move work\cache to D:
os.environ.setdefault("MOVIERECAPS_ZIP_ANY_DRIVE", "1")   # day 17: the zip tests' temp folders are on C:
os.environ.setdefault("MOVIERECAPS_NO_AI", "1")       # day 16: Find source never spends NexLev / Algrow credits in a test
os.environ.setdefault("MOVIERECAPS_KEEP_ROWS", "0")   # run 18: each test's lists start empty (its own test switches it on)
os.environ.setdefault("MOVIERECAPS_ENHANCE", "0")     # run 20: no row sends a piece to the laptop, no Topaz worker starts
#                                                       (the enhance tests switch them on)
if "MOVIERECAPS_SETTINGS" not in os.environ:
    os.environ["MOVIERECAPS_SETTINGS"] = str(_TMP / "settings.json")
    (_TMP / "settings.json").write_text(json.dumps({"save_to": str(_TMP / "shared"), "intro": False}),
                                        encoding="utf-8")


import sys  # noqa: E402

import pytest  # noqa: E402

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NoShutdown(AssertionError):
    pass


def _is_shutdown_command(args) -> bool:
    if isinstance(args, (str, bytes)):
        first = (args.decode() if isinstance(args, bytes) else args).strip().split(" ", 1)[0]
    else:
        try:
            first = str(list(args)[0])
        except (IndexError, TypeError):
            return False
    name = first.replace("\\", "/").rsplit("/", 1)[-1].strip('"').lower()
    return name in ("shutdown", "shutdown.exe")


def _aria2c_on_this_computer_only(args):
    """Day 13: an aria2c a test starts (its RPC daemon) listens on 127.0.0.1 only -- torrent_selftest.LOCAL_ONLY
    (--interface=127.0.0.1, no IPv6, no DHT / local discovery) is added when missing -- so Windows never asks its
    firewall question about aria2c.exe (it popped up during the torrent tests and blocked every click)."""
    if isinstance(args, (str, bytes)):
        return args
    try:
        lst = [str(a) for a in args]
    except TypeError:
        return args
    if not lst or lst[0].replace("\\", "/").rsplit("/", 1)[-1].strip('"').lower() not in ("aria2c", "aria2c.exe") \
            or "--enable-rpc=true" not in lst:
        return args
    from mr_app.core.torrent_selftest import LOCAL_ONLY
    return lst + [a for a in LOCAL_ONLY if a not in lst]


@pytest.fixture(autouse=True)
def _never_turn_the_pc_off(monkeypatch):
    """No test can ever turn this PC off: core.shutdown.SHUTDOWN (and its real command) raise, and any process
    started as "shutdown ..." through subprocess is refused. A test that wants turn_off to "work" replaces SHUTDOWN
    with its own fake, as before. Day 13: every aria2c a test starts listens on 127.0.0.1 only (above)."""
    import subprocess

    from mr_app.core import shutdown as SD

    def refuse(*_a, **_k):
        raise _NoShutdown("a test tried to turn the PC off")
    monkeypatch.setattr(SD, "SHUTDOWN", refuse)
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(args, *a, **k):
        if _is_shutdown_command(args):
            refuse()
        return real_run(args, *a, **k)

    class Popen(real_popen):                        # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **k):
            if _is_shutdown_command(args):
                refuse()
            super().__init__(_aria2c_on_this_computer_only(args), *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    yield


@pytest.fixture(autouse=True)
def _classic_look_after_each_test():
    """Run 18: a test that starts the whole app (app.main sets the look from the settings: Mono + red by default)
    puts the theme back afterwards -- the other tests are written for the classic look."""
    from mr_app.ui import theme
    saved = {k: v for k, v in vars(theme).items() if k.isupper() and not callable(v)}
    yield
    for k, v in saved.items():
        setattr(theme, k, v)
