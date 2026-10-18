"""One line on any computer with this project (the PC, the laptop):

    .venv\\Scripts\\python tools\\app\\setup_app.py

installs what the app needs into the project's venv -- PySide6-Essentials (the window, ~100 MB), yt-dlp (the
downloader; updated by the app at most once a day), psd-tools (reads the channels' template.psd) and, when the
computer has no deno, deno (yt-dlp needs a JavaScript runtime to read YouTube) -- only what is missing, and puts
the MovieRecaps shortcut on the desktop."""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NEEDS = (("PySide6", "PySide6-Essentials==6.12.0"), ("yt_dlp", "yt-dlp[default]"), ("psd_tools", "psd-tools"))


def main() -> int:
    missing = [pkg for mod, pkg in NEEDS if importlib.util.find_spec(mod) is None]
    if missing:
        print("installing " + ", ".join(missing) + " ...", flush=True)
        r = subprocess.run([sys.executable, "-m", "pip", "install", *missing])
        if r.returncode != 0:
            print("pip could not install them -- check the internet connection and run this line again")
            return 1
    deno_home = Path.home() / ".deno" / "bin" / "deno.exe"
    venv_deno = Path(sys.executable).parent / "deno.exe"
    if not (shutil.which("deno") or deno_home.is_file() or venv_deno.is_file()):
        # yt-dlp needs a JavaScript runtime for YouTube: deno, as a pip package into this venv (the app finds it
        # there; Node.js 22+ would do too, but deno is what yt-dlp prefers)
        print("installing deno (yt-dlp needs it to read YouTube) ...", flush=True)
        if subprocess.run([sys.executable, "-m", "pip", "install", "deno"]).returncode != 0:
            print("note: deno could not be installed -- YouTube downloads may miss formats; install it once with: "
                  "winget install DenoLand.Deno")
    from mr_app.core import shortcut
    p = shortcut.make()
    try:                                     # Windows keeps old icons in a cache: show the new logo now (night 11)
        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    print(f"done: the shortcut is on the desktop ({p}) -- double-click MovieRecaps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
