"""The desktop shortcut: 'MovieRecaps' on the Windows desktop starts the app (the project's pythonw.exe -- no console
window -- with tools\\app\\MovieRecaps.pyw) with the app's icon. Made with PowerShell (WScript.Shell), so nothing extra
is needed; works on the PC and the laptop (the desktop may be in OneDrive: Windows says where it is)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import paths

NAME = "MovieRecaps"


def _q(s: str | Path) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(s).replace("'", "''") + "'"


def script(target: Path, launcher: Path, icon: Path, workdir: Path, name: str = NAME) -> str:
    return "; ".join([
        "$d = [Environment]::GetFolderPath('Desktop')",
        f"$p = Join-Path $d {_q(name + '.lnk')}",
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p)",
        f"$s.TargetPath = {_q(target)}",
        f"$s.Arguments = {_q(chr(34) + str(launcher) + chr(34))}",
        f"$s.WorkingDirectory = {_q(workdir)}",
        f"$s.IconLocation = {_q(str(icon) + ',0')}",
        "$s.Description = 'MovieRecaps: match cuts and more'",
        "$s.Save()",
        "Write-Output $p",
    ])


def make() -> Path:
    """Write the shortcut; returns its path."""
    ps = script(paths.pythonw_exe(), paths.LAUNCHER, paths.ICON_ICO, paths.repo())
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, timeout=60, creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "PowerShell failed").strip())
    return Path(r.stdout.strip().splitlines()[-1])
