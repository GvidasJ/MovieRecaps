"""Draw the app's icon (mr_app/assets/icon.png and icon.ico): a red square with a white bold M, square corners like
the rest of the look. Run once: python tools/app/make_icon.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QRectF, Qt                    # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter   # noqa: E402

from mr_app.core import paths                            # noqa: E402
from mr_app.ui import theme                              # noqa: E402


def draw(size: int, family: str) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.fillRect(QRectF(0, 0, size, size), QColor(theme.RED))
    f = QFont(family)
    f.setPixelSize(int(size * 0.74))
    f.setWeight(QFont.Weight.ExtraBold)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, -size * 0.03, size, size), Qt.AlignmentFlag.AlignCenter, "M")
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv[:1])                  # noqa: F841 - fonts need it
    fam = theme.load_fonts()
    big = draw(256, fam)
    big.save(str(paths.ICON_PNG))
    from PIL import Image
    im = Image.open(paths.ICON_PNG).convert("RGBA")
    im.save(paths.ICON_ICO, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128),
                                   (256, 256)])
    print(paths.ICON_PNG, paths.ICON_ICO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
