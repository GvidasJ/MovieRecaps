"""MovieRecaps desktop app. Double-click (or the desktop shortcut); ``python MovieRecaps.pyw --shortcut`` makes the
desktop shortcut."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_app.app import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))
