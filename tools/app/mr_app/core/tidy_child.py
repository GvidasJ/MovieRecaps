"""17 Nov: the clean-up round (core.archive.tidy) in a process of its own, at the lowest priority.

The round checks every finished project -- it reads each zip back (about 1 GB), makes zips again, deletes folders. In
the app's own process (a background thread) that made the window stutter and freeze right after the app opened: the
thread and the window share one Python, and the disk was busy at normal priority. Here it runs as

    python -m mr_app.core.tidy_child <root> <scene_only 0/1> <one_zip 0/1> <stop file> <result file>

in Windows' background mode (lowest CPU, disk and memory priority: whatever you do comes first). The app makes the
stop file when a video starts being made (``stop()`` is asked between two projects); the lines of what was done are
written to the result file as JSON. ``run`` is the app's side: it starts the process, waits, and reads the lines."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

ENV = "MOVIERECAPS_TIDY_CHILD"          # 0: the round runs in the app's process, as before
IDLE_PRIORITY_CLASS = 0x00000040
CREATE_NO_WINDOW = 0x08000000
PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
ROUND_MAX_S = 30 * 60.0                 # a round that is still going after this is stopped (the next one goes on)


def wanted() -> bool:
    """A process of its own: on Windows, unless switched off (and never inside the app's tests)."""
    v = os.environ.get(ENV, "").strip()
    if v in ("0", "1"):
        return v == "1"
    return sys.platform == "win32" and "PYTEST_CURRENT_TEST" not in os.environ


def _background() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), PROCESS_MODE_BACKGROUND_BEGIN)
    except Exception:  # noqa: BLE001 - the priority the app gave it (idle) stays
        pass


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 5:
        return 2
    root, scene_only, one_zip, stop_file, out_file = a
    _background()
    try:                                   # the tool (match_cuts) importable, as in the app
        from . import learnkit
        learnkit.tool()
    except Exception:  # noqa: BLE001
        pass
    from . import archive
    stop = Path(stop_file)
    try:
        lines = archive.tidy(root, stop=stop.exists, scene_only=scene_only == "1", one_zip=one_zip == "1")
    except Exception as e:  # noqa: BLE001 - said in the log, the next round tries again
        lines = [f"the clean-up round stopped: {type(e).__name__}: {e}"]
    tmp = Path(out_file + ".part")
    tmp.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_file)
    return 0


def run(root: str | os.PathLike, *, stop: Callable[[], bool], scene_only: bool = True, one_zip: bool = True,
        poll_s: float = 2.0) -> list[str]:
    """The app's side (called on a background thread): the round in its own process; ``stop()`` is asked every
    ``poll_s`` and passed on through the stop file. Returns the round's lines ([] when it said nothing). Raises
    OSError when the process cannot be started (the caller then runs the round itself)."""
    d = Path(tempfile.mkdtemp(prefix="mr_tidy_"))
    stop_file, out_file = d / "stop", d / "lines.json"
    app_dir = Path(__file__).resolve().parents[2]                     # tools/app: where "mr_app" is found
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):                           # no console either way (CREATE_NO_WINDOW)
        exe = exe[:-len("pythonw.exe")] + "python.exe" if Path(exe[:-len("pythonw.exe")] + "python.exe").is_file() \
            else exe
    flags = (IDLE_PRIORITY_CLASS | CREATE_NO_WINDOW) if sys.platform == "win32" else 0
    proc = subprocess.Popen([exe, "-m", "mr_app.core.tidy_child", str(root), "1" if scene_only else "0",
                             "1" if one_zip else "0", str(stop_file), str(out_file)], cwd=str(app_dir),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    t0 = time.monotonic()
    try:
        while proc.poll() is None:
            try:
                if not stop_file.exists() and (stop() or time.monotonic() - t0 > ROUND_MAX_S):
                    stop_file.write_text("stop", encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            if time.monotonic() - t0 > ROUND_MAX_S + 20 * 60.0:       # it does not answer the stop file: ended
                proc.kill()
                break
            time.sleep(poll_s)
        try:
            lines = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lines = []
        return [str(x) for x in lines] if isinstance(lines, list) else []
    finally:
        for p in (stop_file, out_file, Path(str(out_file) + ".part")):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
