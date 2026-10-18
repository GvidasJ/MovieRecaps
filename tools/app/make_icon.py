"""Draw the app's icon (mr_app/assets/icon.png and icon.ico) -- night 11's "match cut" logo: a play triangle split
diagonally, the left half white, the right half red and a little offset, inside red viewfinder corners, on the
app's dark background (square corners like the rest of the look). Each size is drawn on its own (sharp at 16 px).
Run once: python tools/app/make_icon.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QRectF                         # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter   # noqa: E402

from mr_app.core import paths                            # noqa: E402
from mr_app.ui import theme                              # noqa: E402
from mr_app.ui import widgets as W                       # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(theme.BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pad = max(1.0, size * 0.06)
    W.draw_logo(p, QRectF(pad, pad, size - 2 * pad, size - 2 * pad), stroke=2.4 if size <= 24 else 2.0)
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv[:1])                  # noqa: F841 - painting needs it
    theme.load_fonts()
    big = draw(256)
    big.save(str(paths.ICON_PNG))
    from PIL import Image
    work = paths.ICON_PNG.parent / "_sizes"
    work.mkdir(exist_ok=True)
    frames = []
    for s in SIZES:
        f = work / f"{s}.png"
        draw(s).save(str(f))
        frames.append(Image.open(f).convert("RGBA"))
    frames[-1].save(paths.ICON_ICO, sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    for f in work.glob("*.png"):
        f.unlink()
    work.rmdir()
    print(paths.ICON_PNG, paths.ICON_ICO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
