"""Learn by drag and drop: your fixed Premiere project and the tool's run folder it was made from are copied to the
names the nightly LEARN item reads -- finished\\<name>\\<name>_fixed.prproj and output\\laptop_<name> -- and the
LEARN line for the next night's list is written for you to copy."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import paths

BS = "\\"


def is_run_folder(p: Path) -> bool:
    """A run folder of the tool: 1_edit.xml or extras\\cutlist.json in it."""
    return p.is_dir() and ((p / "1_edit.xml").is_file() or (p / "extras" / "cutlist.json").is_file())


def name_from(project: Path | None, run: Path | None) -> str:
    """'video11' from video11_fixed.prproj (also 'video11 fixed', 'video11-fixed2'); else the run folder's name
    without 'laptop_'; else 'video'."""
    if project is not None:
        n = re.sub(r"(?i)[ _-]+fix(?:ed)?\d*$", "", project.stem).strip(" _-")
        if n and n.lower() not in ("untitled", "project", "new project"):
            return safe(n)
    if run is not None:
        base = run
        if re.fullmatch(r"(?i)edit(-\d+)?", run.name) and (run.parent / "project.json").is_file():
            base = run.parent                            # night 10: a project's edit -> the project's name
        n = re.sub(r"(?i)^laptop_", "", base.name)
        n = re.sub(r"-\d+$", "", n)
        if n:
            return safe(n)
    return "video"


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name.strip()).strip("._") or "video"


@dataclass
class Plan:
    name: str
    project: Path
    run: Path
    project_to: Path
    run_to: Path

    def problems(self) -> list[str]:
        out = []
        if not self.project.is_file():
            out.append(f"{self.project.name}: not there")
        elif self.project.suffix.lower() != ".prproj":
            out.append(f"{self.project.name}: not a Premiere project (.prproj)")
        if not is_run_folder(self.run):
            out.append(f"{self.run.name}: not a run folder of the tool (no 1_edit.xml)")
        if self.project_to.exists() and self.project_to.resolve() != self.project.resolve():
            out.append(f"{rel(self.project_to)} is there already: it will be replaced")
        if self.run_to.exists() and self.run_to.resolve() != self.run.resolve():
            out.append(f"{rel(self.run_to)} is there already: it will be replaced")
        return out


def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(paths.repo()))
    except ValueError:
        return str(p)


def plan(project: Path, run: Path, name: str | None = None, root: Path | None = None) -> Plan:
    root = Path(root or paths.repo())
    n = safe(name) if name else name_from(project, run)
    return Plan(n, Path(project), Path(run), root / "finished" / n / f"{n}_fixed.prproj",
                root / "output" / f"laptop_{n}")


def sort_drop(items: Sequence[Path]) -> tuple[Path | None, Path | None, list[str]]:
    """(the .prproj, the run folder, notes) among dropped files / folders (a folder holding one .prproj counts as it
    -- a finished\\<name> folder)."""
    project = run = None
    notes = []
    for p in map(Path, items):
        if p.is_file() and p.suffix.lower() == ".prproj":
            project = p
        elif is_run_folder(p):
            run = p
        elif p.is_dir():
            pr = sorted(p.glob("*.prproj"))
            if len(pr) == 1:
                project = pr[0]
            else:
                notes.append(f"{p.name}: not a run folder (no 1_edit.xml) and not one project")
        else:
            notes.append(f"{p.name}: not a .prproj or a run folder")
    return project, run, notes


def tree_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def copy(pl: Plan, progress: Callable[[int, int], None] | None = None) -> None:
    """Copy the project and the run folder (bytes copied / all, to ``progress``); the same place: nothing to do."""
    total = (pl.project.stat().st_size if pl.project.is_file() else 0) + tree_size(pl.run)
    done = 0

    def tick(n: int) -> None:
        nonlocal done
        done += n
        if progress is not None:
            progress(done, total)
    if pl.project.resolve() != pl.project_to.resolve():
        pl.project_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pl.project, pl.project_to)
    tick(pl.project.stat().st_size)
    if pl.run.resolve() == pl.run_to.resolve():
        tick(tree_size(pl.run))
        return
    if pl.run_to.exists():
        shutil.rmtree(pl.run_to)
    for f in sorted(pl.run.rglob("*")):
        t = pl.run_to / f.relative_to(pl.run)
        if f.is_dir():
            t.mkdir(parents=True, exist_ok=True)
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        with f.open("rb") as a, t.open("wb") as b:
            while True:
                chunk = a.read(8 << 20)
                if not chunk:
                    break
                b.write(chunk)
                tick(len(chunk))
        shutil.copystat(f, t)


def learn_line(names: Sequence[str]) -> str:
    """The nightly list's LEARN item for these fixes (the wording of night 9's list)."""
    names = [n for n in names if n]
    if not names:
        return ""
    pairs = [f"finished{BS}{n}{BS}{n}_fixed.prproj and output{BS}laptop_{n}" for n in names]
    if len(pairs) == 1:
        what = f"if {pairs[0]} exist, compare them"
    else:
        what = "if " + "; ".join(pairs) + " exist, compare each pair"
    return (f"LEARN (quick): {what} and learn as on nights 7-8 (check each new rule against my earlier fixes first; "
            "make it a test case). Ignore audio levels, pitch, hand-moved audio, trims at cut points and the ending. "
            "If they're missing, skip this and say so.")
