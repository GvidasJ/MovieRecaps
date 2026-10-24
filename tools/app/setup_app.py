"""One line on any computer with this project (the PC, the laptop):

    .venv\\Scripts\\python tools\\app\\setup_app.py

installs what the app and the matching tool need into the project's venv -- only what is missing, too old or broken
(the versions the project expects are ONE table, mr_app\\core\\versions.py, which the app's start-up check uses too:
the two never disagree): PySide6-Essentials (the window), yt-dlp (the downloader; updated by the app at most once a
day), psd-tools (reads the channels' template.psd), PySceneDetect 0.7.1 (the tool's cut cross-check), the tool's own
packages, PyTorch with CUDA when the computer has an NVIDIA card and PyTorch is missing or built without CUDA, deno
(yt-dlp needs a JavaScript runtime to read YouTube), aria2c (the torrent engine, from its official GitHub release,
checksum-checked, into tools\\app\\bin) -- and puts the MovieRecaps shortcut on the desktop.

``--from-app``: started by the app's Fix now (the window is open): the window's own files (PySide6) are not replaced
-- that waits for a run with the app closed -- and the shortcut is left as it is. Exit code 0: everything is as the
project expects; 1: an install failed (no internet?); 2: something is left for a step by hand or for a run with the
app closed (said in plain words on the last lines)."""
import importlib.util  # noqa: F401 - (kept: the tests replace importlib.util.find_spec to fake a broken package)
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def pip(args: list, label: str) -> bool:
    print(f"installing {label} ...", flush=True)
    return subprocess.run([sys.executable, "-m", "pip", "install", *args]).returncode == 0


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from_app = "--from-app" in argv
    from mr_app.core import versions as V
    wants = V.all_wants()
    problems = V.check(wants=wants)
    for p in problems:
        print(f"found: {p.text}", flush=True)
    jobs, left = V.pip_plan(problems, wants, app_open=from_app)
    failed = []
    for label, args in jobs:
        if not pip(args, label):
            failed.append(label)
    keys = {p.key for p in problems}
    if "deno" in keys:
        # yt-dlp needs a JavaScript runtime for YouTube: deno, as a pip package into this venv (the app finds it
        # there; Node.js 22+ would do too, but deno is what yt-dlp prefers)
        if not pip(["deno"], "deno (yt-dlp needs it to read YouTube)"):
            print("note: deno could not be installed -- YouTube downloads may miss formats; install it once with: "
                  "winget install DenoLand.Deno")
            failed.append("deno")
    if "aria2c" in keys:
        # torrents (day 12): aria2c, because libtorrent has no wheel for this Python (3.14) yet -- into tools\app\bin,
        # which the app looks at first (so a newer one there replaces an old one on PATH)
        from mr_app.core import torrent
        try:
            print(f"aria2c installed: {torrent.install_aria2c()}")
        except Exception as e:  # noqa: BLE001
            print(f"note: aria2c (torrents) could not be installed: {e} -- run this line again later")
            failed.append("aria2c")
    if not from_app:
        from mr_app.core import shortcut
        p = shortcut.make()
        try:                                 # Windows keeps old icons in a cache: show the new logo now (night 11)
            subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
        print(f"the shortcut is on the desktop ({p}) -- double-click MovieRecaps")
        from mr_app.core import cache_move   # day 17: the tool's cache on D: (work\cache a junction to D:\mr_cache)
        try:
            cache_move.move()
        except Exception as e:  # noqa: BLE001 - never stops the setup; the cache stays where it is
            print(f"note: the tool's cache was not moved to D: ({e})")
    after = V.check(wants=wants) if (jobs or "deno" in keys or "aria2c" in keys) else problems
    for p in after:
        if p.closed and from_app:
            print(f"left: {p.text} -- close MovieRecaps, then run: .venv\\Scripts\\python tools\\app\\setup_app.py")
        elif p.hand:
            print(f"left: {p.text} -- {p.hand}")
        else:
            print(f"left: {p.text}")
    if failed and after:
        print("pip could not install " + ", ".join(failed) + " -- check the internet connection and run this line "
              "again")
        return 1
    if after:
        return 2
    print("done: everything is as the project expects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
