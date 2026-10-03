"""Where a run's files go: ``<--out>/<NNN>/`` per run (001, 002, ...: the next free number, so a new video never
overwrites the previous one), holding only the files used in Premiere, numbered in the order they are used --

    1_edit.xml                  the Premiere sequence (FCP7 XML)
    2_captions.srt              the captions
    3_captions_styled.prproj    written by ``python -m match_cuts restyle``

-- and everything else (report.md, cutlist.json / .csv, the EDL, verify.json, the preview and compare renders,
debug/, media/, the run's log) in its ``extras/`` subfolder. 1_edit.xml points at the RAW in extras/media/ by its
absolute path, so it finds the media wherever Premiere opens it from.
"""
from __future__ import annotations

import re
from pathlib import Path

EDIT_XML = "1_edit.xml"
CAPTIONS_SRT = "2_captions.srt"
STYLED_PRPROJ = "3_captions_styled.prproj"
EXTRAS = "extras"
RUN_RE = re.compile(r"^\d{3,}$")


def run_dirs(base: str | Path) -> list[tuple[int, Path]]:
    """The numbered run folders in base, oldest (lowest number) first."""
    b = Path(base)
    if not b.is_dir():
        return []
    return sorted((int(p.name), p) for p in b.iterdir() if p.is_dir() and RUN_RE.match(p.name))


def newest_run_dir(base: str | Path) -> Path | None:
    runs = run_dirs(base)
    return runs[-1][1] if runs else None


def new_run_dir(base: str | Path) -> Path:
    """Create and return the next numbered run folder in base (the highest number + 1, never an existing one)."""
    b = Path(base)
    b.mkdir(parents=True, exist_ok=True)
    runs = run_dirs(b)
    n = (runs[-1][0] if runs else 0) + 1
    while True:
        p = b / f"{n:03d}"
        try:
            p.mkdir()
            return p
        except FileExistsError:                  # another run took it meanwhile
            n += 1


def default_output_bases() -> list[Path]:
    """Where to look for run folders when none is given: ./output, then the repository's output/ folder (the runs
    of ``python -m match_cuts ... --out ..\\..\\output`` started in tools/match_cuts)."""
    out: list[Path] = []
    for p in (Path.cwd() / "output", Path(__file__).resolve().parents[3] / "output"):
        if p.resolve() not in [q.resolve() for q in out]:
            out.append(p)
    return out
