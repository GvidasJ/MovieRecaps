"""What a run was given (day 13, 1.1): the New video form's run of "Are We There Yet" stopped with WRONG PAIR while
the same two files dropped straight into Match cuts ran -- and nothing said which files the stopped run had read.

* ``given(job, competitor, raw)``: both files as the tool got them -- path, size, change time, length, picture, frame
  rate, codec, sound streams (match_cuts.pair_check.facts: one ffprobe each, no decoding) and, for a project, how the
  app made each one (copied / linked / converted: project.json's download and convert steps). The run's result keeps
  it ("inputs") and project.json's edit step too, so a stop can be compared with a run that went on.
* ``copies_hint(project)``: after a WRONG PAIR of a project made from your own files, are the project's copies still
  your files? (a copy of another size, a conversion of another length, a file gone) -- in plain words, "" when they
  are."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _facts(path: Path) -> dict:
    try:
        from match_cuts import pair_check as PC
        from match_cuts.cli import quick_probe
        try:
            info = quick_probe(path)
        except Exception:  # noqa: BLE001 - its size and sound are still worth saying
            info = None
        return PC.facts(path, info)
    except Exception as e:  # noqa: BLE001 - a help, never a stop
        return {"path": str(path), "name": Path(path).name, "error": f"not read ({type(e).__name__})"}


def given(job: Any, competitor: Path, raw: Path) -> dict:
    """{'competitor': facts, 'raw': facts} of the files a run was started with (+ 'made': how the app made them)."""
    out = {"competitor": _facts(Path(competitor)), "raw": _facts(Path(raw))}
    proj = getattr(job, "project", None)
    if proj is not None:
        try:
            from . import projects as P
            p = P.load(proj)
            for role in ("competitor", "raw"):
                dl = (p.step("download").get(role) or {}) if isinstance(p.step("download").get(role), dict) else {}
                cv = (p.step("convert").get(role) or {}) if isinstance(p.step("convert").get(role), dict) else {}
                out[role]["made"] = {"from": (p.data.get(role) or {}).get("source_file") or dl.get("from"),
                                     "how": dl.get("how"), "converted": cv.get("how")}
        except Exception:  # noqa: BLE001
            pass
    return out


def text(inputs: dict) -> str:
    """'competitor: name (0:25, ...); RAW: name (1:34:53, ...)' -- the run's two files in one line."""
    try:
        from match_cuts import pair_check as PC
        return "; ".join(f"{'RAW' if r == 'raw' else 'competitor'}: {PC.facts_text(inputs[r])}"
                         for r in ("competitor", "raw") if inputs.get(r))
    except Exception:  # noqa: BLE001
        return ""


def copies_hint(project: str | Path) -> str:
    """After a WRONG PAIR: are the project's two videos still the files you gave? Plain words ("" = they are, or it
    cannot be told). A RAW copied (or linked) must have your file's size; a converted video your file's length."""
    from . import projects as P
    try:
        p = P.load(project)
    except Exception:  # noqa: BLE001
        return ""
    from match_cuts.cli import quick_probe
    said = []
    for role, who in (("competitor", "the competitor"), ("raw", "the RAW")):
        mine = p.file(role)
        src = (p.data.get(role) or {}).get("source_file")
        if mine is None or not src:
            continue
        src_p = Path(src)
        if not mine.is_file():
            said.append(f"{who} is not in the project folder any more ({mine.name})")
            continue
        if not src_p.is_file():
            continue
        cv = p.step("convert").get(role) if isinstance(p.step("convert").get(role), dict) else {}
        try:
            if not cv or cv.get("how") in (None, "keep"):
                a, b = mine.stat().st_size, src_p.stat().st_size
                if a != b:
                    said.append(f"{who} in the project folder ({a / 1e9:.2f} GB) is not the same size as your file "
                                f"{src_p.name} ({b / 1e9:.2f} GB): it was not copied whole")
            else:
                a, b = float(quick_probe(mine)["duration"]), float(quick_probe(src_p)["duration"])
                if abs(a - b) > 0.5:
                    said.append(f"{who} in the project folder ({a:.1f} s) is not as long as your file {src_p.name} "
                                f"({b:.1f} s): its conversion is not the same video")
        except Exception:  # noqa: BLE001 - cannot tell
            continue
    if not said:
        return ""
    return ("The project's copies are not your files: " + "; ".join(said)
            + ". Press Try again from the download step (or drop your two files on Match cuts).")
