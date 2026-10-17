"""Free space before a run (night 10). Every run folder holds its own media in extras\\media: a hard link when the
run folder is on the RAW's own drive (no copy, no extra space), else a copy -- another drive, a network folder
(D:\\Shared over Wi-Fi), a USB drive (match_cuts.conform._link_or_copy). So a run whose folder is on another drive
needs room there for the RAW and the competitor, plus the run's own files; the app warns before the start when a
drive is too full (the tool itself stops at once with the same words if it still happens)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Sequence

GB = 1024 ** 3
RUN_FILES = 1 * GB           # a run's own files (previews, the compare video, the cut list ...) -- generous
RESERVE = 512 * 1024 ** 2    # the tool's own margin for a copy (conform.COPY_RESERVE)
SAFE = {".mp4", ".mov", ".m4v"}  # inputs the tool can place unchanged (others are converted inside the run first)


def existing(p: Path) -> Path:
    """``p`` or its nearest folder that exists (a run folder is made at the start)."""
    p = Path(p)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def same_drive(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` are on one volume (a hard link can work there)."""
    try:
        return os.stat(existing(a)).st_dev == os.stat(existing(b)).st_dev
    except OSError:
        return False


def drive_name(p: Path) -> str:
    """'C:', 'D:' or '\\\\pc\\Shared' -- how the warning names a drive."""
    d = os.path.splitdrive(os.path.abspath(existing(p)))[0]
    return d or str(existing(p).anchor or p)


def size(p: Path | None) -> int:
    try:
        return int(Path(p).stat().st_size) if p is not None else 0
    except OSError:
        return 0


def need_bytes(competitor: Path, raw: Path, run_dir: Path) -> tuple[int, str]:
    """(bytes the run needs on its folder's drive, why in plain words)."""
    bits, need = [], RUN_FILES
    for role, f in (("RAW", raw), ("competitor", competitor)):
        n = size(f)
        if Path(f).suffix.lower() not in SAFE:            # converted into the run folder first: about twice the size
            need += 2 * n
            bits.append(f"the {role}'s converted copy (~{2 * n / GB:.1f} GB)")
        elif not same_drive(f, run_dir):
            need += n
            bits.append(f"a copy of the {role} ({n / GB:.1f} GB: another drive, it cannot be linked)")
    return need + RESERVE, (", ".join(bits) + " and the run's own files") if bits else "the run's own files"


def free_bytes(p: Path) -> int | None:
    try:
        return int(shutil.disk_usage(existing(p)).free)
    except OSError:
        return None


def problems(runs: Sequence[tuple[str, Path, Path, Path]]) -> list[str]:
    """Plain-words warnings for the runs [(name, competitor, raw, run folder)] about to start: the runs going to one
    drive are added up, and a drive without room for them is named."""
    per_drive: dict[str, list] = {}
    for name, comp, raw, run_dir in runs:
        if comp is None or raw is None:
            continue
        n, why = need_bytes(Path(comp), Path(raw), Path(run_dir))
        per_drive.setdefault(drive_name(Path(run_dir)), []).append((name, n, why, Path(run_dir)))
    out = []
    for drive, items in per_drive.items():
        free = free_bytes(items[0][3])
        total = sum(n for _, n, _, _ in items)
        if free is not None and free < total:
            names = ", ".join(f"{name} ({n / GB:.1f} GB: {why})" for name, n, why, _ in items)
            out.append(f"not enough free space on {drive}: the run folders there need about {total / GB:.1f} GB "
                       f"({names}), only {free / GB:.1f} GB is free -- free some space or choose an output folder on "
                       "the RAW's own drive (there the videos are linked, not copied)")
    return out


def summary(paths: Iterable[Path]) -> str:
    """'C: 64.2 GB free, D: 2619.8 GB free' for the drives of ``paths``."""
    seen, bits = set(), []
    for p in paths:
        d = drive_name(Path(p))
        if d in seen:
            continue
        seen.add(d)
        f = free_bytes(Path(p))
        bits.append(f"{d} {f / GB:.1f} GB free" if f is not None else f"{d} ?")
    return ", ".join(bits)
