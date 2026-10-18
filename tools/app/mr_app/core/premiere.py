"""Premiere Pro on this computer: its program file (where Windows says it is -- the 'App Paths' entry its installer
writes -- else the newest 'Adobe Premiere Pro <year>' in the usual folders), and opening a run's 1_edit.xml in it.

Premiere opens an XML handed to it only while it is already running (started with one, it shows its Home screen):
so it is started first when needed, the XML handed over once its window is up. It opens the XML as a new project
named after the file -- the run's copy ``<run>_edit.xml`` (video10_edit), not one more "1_edit" -- and, as Premiere
does, saves that project in the folder of the last project you opened. Not installed: the run folder opens instead."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

EXE = "Adobe Premiere Pro.exe"
NO_WINDOW = 0x08000000
START_WAIT_S = 180.0                 # Premiere's start (its window up) at most this long
SETTLE_S = 8.0                       # ... then this long before the XML is handed over


def _registered() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{EXE}") as k:
                v, _ = winreg.QueryValueEx(k, "")
                p = Path(str(v).strip().strip('"'))
                if p.is_file():
                    return p
        except OSError:
            continue
    return None


def _roots() -> list[Path]:
    out = []
    for drive in ("C:", "D:", "E:", "F:"):
        out += [Path(drive + "\\Program Files\\Adobe"), Path(drive + "\\Adobe")]
    pf = os.environ.get("ProgramFiles")
    if pf:
        out.insert(0, Path(pf) / "Adobe")
    return out


def find() -> Path | None:
    """Premiere Pro's program file, or None."""
    reg = _registered()
    if reg is not None:
        return reg
    found = []
    for adobe in _roots():
        try:
            for d in adobe.glob("Adobe Premiere Pro*"):
                exe = d / EXE
                if exe.is_file():
                    m = re.search(r"(\d{4})", d.name)
                    found.append((int(m.group(1)) if m else 0, str(exe)))
        except OSError:
            continue
    return Path(max(found)[1]) if found else None


def window_up() -> bool:
    """Premiere runs and shows its main window."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-Process 'Adobe Premiere Pro' -ErrorAction SilentlyContinue | "
                        "Where-Object { $_.MainWindowTitle }).Count"],
                       capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    return r.stdout.strip() not in ("", "0")


def named_copy(xml: Path) -> Path:
    """The run's 1_edit.xml as <run folder name>_edit.xml next to it (Premiere names the new project after it); a
    project's edit (<project>\\edit, edit-2 ...) is named after the project (night 10)."""
    run = xml.parent
    name = f"{run.name}_edit"
    if re.fullmatch(r"(?i)edit(-\d+)?", run.name) and (run.parent / "project.json").is_file():
        name = f"{run.parent.name}_{run.name}"              # 2026-10-17_a-lot-to-catch-up-on_edit(-2)
    out = xml.with_name(f"{name}.xml")
    if not out.is_file() or out.stat().st_mtime < xml.stat().st_mtime:
        shutil.copy2(xml, out)
    return out


def open_xml(xml: Path, wait_s: float = START_WAIT_S) -> str:
    """Open a run's 1_edit.xml in Premiere Pro (started first when it is not running; this waits for it -- call it in
    the background); returns what happened (plain words)."""
    exe = find()
    if exe is None:
        os.startfile(str(xml.parent))                        # noqa: S606 - the run folder in Explorer
        return "Premiere Pro was not found on this computer: the run folder is open -- import 1_edit.xml yourself"
    xml = named_copy(Path(xml))
    started = False
    if not window_up():
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        started = True
        t0 = time.time()
        while not window_up():
            if time.time() - t0 > wait_s:
                return "Premiere Pro did not start in time -- open it, then press Open in Premiere again"
            time.sleep(3.0)
        time.sleep(SETTLE_S)
    subprocess.Popen([str(exe), str(xml)], cwd=str(exe.parent))     # (never a network folder as the working folder)
    return (("Premiere Pro started; " if started else "") + f"{xml.name} is opening in {exe.parent.name} as a new "
            "project (Premiere saves it in the folder of the last project you opened)")
