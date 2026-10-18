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
if "MOVIERECAPS_SETTINGS" not in os.environ:
    os.environ["MOVIERECAPS_SETTINGS"] = str(_TMP / "settings.json")
    (_TMP / "settings.json").write_text(json.dumps({"save_to": str(_TMP / "shared"), "intro": False}),
                                        encoding="utf-8")
