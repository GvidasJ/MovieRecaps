"""The app's tests never read or write this computer's real settings (%APPDATA%\\MovieRecaps): every session gets its
own settings file and app-data folder, and Qt runs without a screen."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = Path(tempfile.mkdtemp(prefix="mr_app_tests_"))
os.environ.setdefault("MOVIERECAPS_SETTINGS", str(_TMP / "settings.json"))
os.environ.setdefault("MOVIERECAPS_APPDATA", str(_TMP / "appdata"))
