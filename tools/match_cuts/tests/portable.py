"""What the tests need from the OS, the same on Linux, Windows and macOS: a TrueType font for burned-in text, its
path written for an ffmpeg filtergraph, fake executables, and which ffmpeg filters this build has.

The tests were written on Linux (DejaVu fonts, ``#!`` scripts, fork); here the DejaVu font stays the first choice --
on Linux nothing changes, recorded hashes included -- and Windows / macOS get their own equivalents.
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path

FONTS = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    "mono": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "C:/Windows/Fonts/consolab.ttf",
             "/Library/Fonts/Courier New Bold.ttf", "/System/Library/Fonts/Supplemental/Courier New Bold.ttf"],
}


MPL_FONTS = {"bold": "DejaVuSans-Bold.ttf", "mono": "DejaVuSansMono-Bold.ttf"}


def _matplotlib_font(kind: str) -> str:
    """The same DejaVu font from matplotlib's own data (a dependency of match_cuts), on any OS."""
    try:
        import matplotlib
        return str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / MPL_FONTS[kind])
    except Exception:  # noqa: BLE001
        return ""


def font_file(kind: str = "bold") -> str:
    """The first font of ``kind`` this machine has: the system's DejaVu (Linux), else matplotlib's copy of the same
    font -- so text renders like on Linux everywhere --, else the OS's Arial / Consolas (the DejaVu path when none:
    drawtext then fails visibly)."""
    cands = FONTS[kind][:1] + [_matplotlib_font(kind)] + FONTS[kind][1:]
    return next((p for p in cands if p and Path(p).is_file()), FONTS[kind][0])


def ff_path(path: str, quoted: bool) -> str:
    """``path`` as an ffmpeg filter option value: forward slashes, and the colon of a Windows drive escaped -- one
    backslash inside '...' (option level only), two when unquoted (filtergraph level, then option level)."""
    p = str(path).replace("\\", "/")
    return p.replace(":", "\\:" if quoted else "\\\\:")


def fake_exe(path: Path, body: str) -> Path:
    """A fake program running the Python ``body`` (it sees the arguments in sys.argv): a ``#!`` script on POSIX; on
    Windows (no ``#!``) the body goes to ``<name>.py`` and the returned program is ``<name>.cmd``, which runs it."""
    path = Path(path)
    if os.name != "nt":
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o755)
        return path
    script = path.with_name(path.stem + "_fake.py")
    script.write_text(body, encoding="utf-8")
    cmd = path.with_suffix(".cmd")
    cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n@exit /b %errorlevel%\r\n', encoding="utf-8")
    return cmd


@functools.lru_cache(maxsize=None)
def ffmpeg_filters() -> frozenset[str]:
    """The filter names this ffmpeg build has (empty when ffmpeg cannot be run)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def pop_in(style: str, text: str, a: int, b: int, pos: str) -> list[str]:
    """drawtext filters of a caption popping in on frames a..b: 22 px on frame a, 33 px on a+1, 44 px from a+2 on --
    what ``fontsize='if(lt(n,a+2),44*(0.5+0.25*(n-a)),44)'`` draws, but with fixed sizes: an expression for
    fontsize crashes some ffmpeg builds (8.0 on Windows: access violation)."""
    return [f"drawtext={style}:text='{text}':fontsize=22:{pos}:enable='eq(n,{a})'",
            f"drawtext={style}:text='{text}':fontsize=33:{pos}:enable='eq(n,{a + 1})'",
            f"drawtext={style}:text='{text}':fontsize=44:{pos}:enable='between(n,{a + 2},{b})'"]
