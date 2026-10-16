"""One line on any computer with this project (the PC, the laptop):

    .venv\\Scripts\\python tools\\app\\setup_app.py

installs what the app needs (PySide6-Essentials, ~100 MB, once) into the project's venv and puts the MovieRecaps
shortcut on the desktop."""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    if importlib.util.find_spec("PySide6") is None:
        print("installing PySide6-Essentials (the window toolkit) ...", flush=True)
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(HERE / "requirements.txt")])
        if r.returncode != 0:
            print("pip could not install it -- check the internet connection and run this line again")
            return 1
    from mr_app.core import shortcut
    p = shortcut.make()
    print(f"done: the shortcut is on the desktop ({p}) -- double-click MovieRecaps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
