"""Turn off the PC when everything is done (day 12, 6d): after the last download, convert or edit in the whole app the
window shows a 60-second warning with Cancel, then this turns the PC off -- but never while Premiere has unsaved work
(its window title ends with "*") or a render is running (Adobe Media Encoder busy, or Premiere's export / render
progress box). ``blockers()`` says what stops it, in plain words; ``turn_off()`` calls ``SHUTDOWN`` -- the one place
the PC is really turned off, replaced in every test so a test never turns anything off."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Callable

WARNING_S = 60
AME_EXES = ("adobe media encoder.exe",)
AME_BUSY_CPU = 8.0               # % of one core over the sample: AME encoding (idle it sits near 0)
RENDER_WORDS = re.compile(r"\b(export(?:ing)?|encod(?:e|ing)|render(?:ing)?)\b", re.I)


def _real_shutdown() -> None:
    subprocess.run(["shutdown", "/s", "/t", "0"], capture_output=True, timeout=30,
                   creationflags=0x08000000 if os.name == "nt" else 0)


SHUTDOWN: Callable[[], None] = _real_shutdown       # tests replace this


def premiere_unsaved() -> bool:
    """Premiere is open with changes not saved (its title's "*")."""
    try:
        from . import premiere
        return bool(premiere.state().changed)
    except Exception:  # noqa: BLE001 - cannot tell: not a blocker by itself
        return False


def premiere_rendering() -> bool:
    """Premiere shows an export / render progress box."""
    try:
        from . import premiere
        st = premiere.state()
        return any(RENDER_WORDS.search(d or "") for d in st.dialogs)
    except Exception:  # noqa: BLE001
        return False


def media_encoder_busy(sample_s: float = 1.0) -> bool:
    """Adobe Media Encoder is encoding (open and using the processor); open and idle does not count."""
    try:
        import psutil
    except ImportError:
        return False
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() in AME_EXES:
                p.cpu_percent(None)
                procs.append(p)
        except Exception:  # noqa: BLE001 - gone meanwhile
            continue
    if not procs:
        return False
    import time
    time.sleep(sample_s)
    for p in procs:
        try:
            if p.cpu_percent(None) >= AME_BUSY_CPU:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


CHECKS: list[tuple[str, Callable[[], bool]]] = [
    ("Premiere has unsaved work -- save it, then the PC turns off", premiere_unsaved),
    ("a render is running in Premiere", premiere_rendering),
    ("Adobe Media Encoder is rendering", media_encoder_busy),
]


def blockers() -> list[str]:
    """What stops the PC from turning off now (empty: nothing). Reads processes and windows: call it off the window's
    thread."""
    out = []
    for words, check in CHECKS:
        try:
            if check():
                out.append(words)
        except Exception:  # noqa: BLE001
            continue
    return out


def turn_off() -> list[str]:
    """Turn the PC off now unless something stops it; returns what stopped it (empty: it was turned off)."""
    b = blockers()
    if b:
        return b
    SHUTDOWN()
    return []
