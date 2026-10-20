"""The programs this computer needs and the versions the project expects (day 13) -- ONE table (``WANTS``, plus the
matching tool's own packages read from tools\\match_cuts\\pyproject.toml), used by BOTH setup_app.py (what it
installs) and the app (the amber line under the top strip, with Fix now), so the two never disagree.

Each entry says what it is in plain words, how it is read and what is good:

* a pip package: its version from pip's records (``importlib.metadata`` -- nothing is imported), the version the
  project pins (``exact``) or the lowest good one (``least``), and for PySceneDetect an import check made safely
  (``scenedetect.output``: a broken or old install says why instead of crashing -- day 13, the laptop);
* a program (aria2c, FFmpeg, ffprobe, deno): found on this computer, and its ``--version``;
* PyTorch: on a computer with an NVIDIA card it must be a CUDA build that sees the card (else the matching and the
  captions run without the graphics card's help).

How setup_app fixes each one is in the entry too (``fix``): pip arguments, aria2c's own installer, deno, or a step
done by hand (FFmpeg: said in plain words). ``closed``: the window's own files (PySide6) are replaced only while the
app is closed. ``check()`` takes fake readers in the tests; the app runs ``python -m mr_app.core.versions`` (the
venv's Python, in the background: a broken package can never touch the window) and reads its JSON.

Only the standard library here: setup_app.py imports this before PySide6 is installed."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

REPO = Path(__file__).resolve().parents[4]                # tools/app/mr_app/core -> the project folder
TOOL_DIR = REPO / "tools" / "match_cuts"
APP_BIN = REPO / "tools" / "app" / "bin"
TORCH_INDEX = "https://download.pytorch.org/whl/cu130"   # the PC's build (torch 2.11.0+cu130, day 13)
CREATE_NO_WINDOW = 0x08000000
EXE_TIMEOUT_S = 20.0
TORCH_TIMEOUT_S = 90.0
CHECK_TIMEOUT_S = 180.0                                  # the whole check in its own process (the app's call)

PIP, ARIA2C, DENO, HAND = "pip", "aria2c", "deno", "hand"


@dataclass(frozen=True)
class Want:
    """One thing this computer needs (module docstring)."""
    key: str                       # short name (the log, the tests)
    name: str                      # what the user reads: "PySceneDetect"
    what: str                      # what it is for, plain words: "the tool's cross-check of the cuts"
    dist: str = ""                 # its pip name (pip's records)
    exact: str = ""                # the version the project pins ("0.7.1") ...
    least: str = ""                # ... or the lowest good one ("2026.8.19")
    module: str = ""               # must be found without errors ("scenedetect.output")
    exe: str = ""                  # a program: aria2c / ffmpeg / ffprobe / deno
    pip: tuple[str, ...] = ()      # setup_app's fix: pip install <these>
    fix: str = PIP                 # pip | aria2c | deno | hand
    hand: str = ""                 # the step done by hand (fix = hand), plain words
    closed: bool = False           # replaced only while the app is closed (the window's own files)
    gpu: bool = False              # PyTorch: a CUDA build that sees the card on a computer with an NVIDIA card
    home: str = ""                 # the package must come from this folder (the matching tool: this project's)


SCENEDETECT = "0.7.1"
WANTS: tuple[Want, ...] = (
    Want("scenedetect", "PySceneDetect", "the tool's cross-check of the cuts", dist="scenedetect", exact=SCENEDETECT,
         module="scenedetect.output",
         # without its own dependencies (its metadata pulls opencv-python, which breaks the tool's OpenCV build)
         pip=("--no-deps", f"scenedetect=={SCENEDETECT}", "click", "platformdirs")),
    Want("torch", "PyTorch", "the graphics card's help for the matching and the captions", dist="torch",
         module="torch", gpu=True, pip=("torch", "torchaudio", "--index-url", TORCH_INDEX)),
    Want("torchaudio", "torchaudio", "the captions' word timing", dist="torchaudio", module="torchaudio",
         pip=("torch", "torchaudio", "--index-url", TORCH_INDEX)),
    Want("yt-dlp", "yt-dlp", "the YouTube downloader", dist="yt-dlp", least="2026.8.19", module="yt_dlp",
         pip=("-U", "yt-dlp[default]")),
    Want("aria2c", "aria2c", "the torrent engine", exe="aria2c", least="1.37.0", fix=ARIA2C),
    Want("ffmpeg", "FFmpeg", "reads and converts the videos", exe="ffmpeg", least="6.0", fix=HAND,
         hand="install FFmpeg once (PowerShell: winget install Gyan.FFmpeg), then open the app again"),
    Want("ffprobe", "ffprobe", "reads the videos' details", exe="ffprobe", least="6.0", fix=HAND,
         hand="install FFmpeg once (PowerShell: winget install Gyan.FFmpeg), then open the app again"),
    Want("pyside6", "PySide6", "the app's window", dist="PySide6-Essentials", exact="6.12.0", module="PySide6",
         pip=("PySide6-Essentials==6.12.0",), closed=True),
    Want("faster-whisper", "faster-whisper", "the captions' speech recogniser", dist="faster-whisper", least="1.0",
         module="faster_whisper", pip=("faster-whisper>=1.0",)),
    Want("psd-tools", "psd-tools", "reads the channels' template.psd", dist="psd-tools", least="1.25.0",
         module="psd_tools", pip=("psd-tools>=1.25.0",)),
    # missing: installed (editable) from this project; installed from ANOTHER folder: only said, never changed by
    # itself (a setup_app run from a second copy of the project -- a git worktree -- must never repoint the venv)
    Want("match_cuts", "the matching tool", "makes the edits", dist="match_cuts", module="match_cuts",
         pip=("-e", str(TOOL_DIR)), home=str(TOOL_DIR / "match_cuts"),
         hand=f"install it from this project once: .venv\\Scripts\\python -m pip install -e {TOOL_DIR}"),
    Want("deno", "deno", "lets yt-dlp read YouTube", exe="deno", fix=DENO),
)


def tool_wants(pyproject: Path | None = None) -> list[Want]:
    """The matching tool's own packages (tools\\match_cuts\\pyproject.toml's dependencies: numpy, scipy, OpenCV ...);
    the fix installs that requirement as written there (``pip install "numpy>=1.26"``)."""
    p = Path(pyproject or TOOL_DIR / "pyproject.toml")
    try:
        import tomllib
        deps = tomllib.loads(p.read_text(encoding="utf-8")).get("project", {}).get("dependencies", [])
    except Exception:  # noqa: BLE001 - an old Python or no pyproject: nothing more to check
        return []
    out = []
    for d in deps:
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(?:>=\s*([0-9][^,;\s]*))?", d)
        if not m:
            continue
        out.append(Want(f"tool:{m.group(1).lower()}", m.group(1), "a package of the matching tool", dist=m.group(1),
                        least=m.group(2) or "", pip=(d.split(";")[0].strip(),)))
    return out


def all_wants() -> list[Want]:
    seen = {w.dist.lower() for w in WANTS if w.dist}
    return list(WANTS) + [w for w in tool_wants() if w.dist.lower() not in seen]


# ---------------------------------------------------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------------------------------------------------

def vtuple(v: str | None) -> tuple[int, ...]:
    """'2026.08.19' -> (2026, 8, 19); '2.11.0+cu130' -> (2, 11, 0); 'n8.0-3-g08a8' -> (8, 0); '' -> ()."""
    if not v:
        return ()
    head = str(v).split("+", 1)[0]
    m = re.search(r"\d+(?:\.\d+)*", head)
    return tuple(int(x) for x in m.group(0).split(".")) if m else ()


def older(have: str, want: str) -> bool:
    """Is ``have`` older than ``want``? (an unreadable version is never called old)"""
    a, b = vtuple(have), vtuple(want)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) < b + (0,) * (n - len(b))


def same(have: str, want: str) -> bool:
    a, b = vtuple(have), vtuple(want)
    n = max(len(a), len(b))
    return bool(a) and a + (0,) * (n - len(a)) == b + (0,) * (n - len(b))


# ---------------------------------------------------------------------------------------------------------------------
# the readers (the tests give fake ones)
# ---------------------------------------------------------------------------------------------------------------------

def dist_version(dist: str) -> str | None:
    """A pip package's version from pip's records (nothing imported); None when it is not installed."""
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - broken records: as if missing
        return None


def module_problem(name: str) -> str:
    """'' when ``name`` is found without errors, else why (plain words). A dotted name imports its package first --
    a broken or old package raises there (day 13: ModuleNotFoundError crashed setup_app on the laptop): caught."""
    try:
        return "" if importlib.util.find_spec(name) is not None else f"{name} is missing"
    except Exception as e:  # noqa: BLE001 - ModuleNotFoundError, ImportError, anything a broken package raises
        return f"it does not load ({type(e).__name__}: {str(e)[:120]})"


def module_home(name: str) -> str:
    """Where a package is found ('' when not found)."""
    try:
        s = importlib.util.find_spec(name)
    except Exception:  # noqa: BLE001
        return ""
    if s is None:
        return ""
    locs = list(s.submodule_search_locations or [])
    return str(locs[0]) if locs else str(Path(s.origin).parent) if s.origin else ""


def exe_path(name: str) -> str | None:
    """Where a program is: FFMPEG / FFPROBE for those two (as the tools use them), aria2c the torrent part's way,
    deno where setup_app looks; else on PATH."""
    if name in ("ffmpeg", "ffprobe"):
        env = os.environ.get(name.upper(), "").strip()
        if env:
            return env if (Path(env).is_file() or shutil.which(env)) else None
    if name == "aria2c":
        try:
            from . import torrent
            p = torrent.find_aria2c()
            return str(p) if p else None
        except Exception:  # noqa: BLE001
            pass
    if name == "deno":
        for p in (Path.home() / ".deno" / "bin" / "deno.exe", Path(sys.executable).parent / "deno.exe"):
            if p.is_file():
                return str(p)
    return shutil.which(name)


def exe_version(name: str) -> tuple[str | None, str]:
    """(its version -- '' when it runs but says none --, where it is); (None, '') when it is not on this
    computer."""
    p = exe_path(name)
    if not p:
        return None, ""
    flag = "-version" if name in ("ffmpeg", "ffprobe") else "--version"
    try:
        r = subprocess.run([p, flag], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=EXE_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None, p
    first = ((r.stdout or "") + "\n" + (r.stderr or "")).strip().splitlines()[:1]
    m = re.search(r"version\s+(\S+)", first[0]) if first else None
    if m is None and first:
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", first[0])
    return (m.group(1) if m else ""), p


def gpu_name() -> str:
    """The NVIDIA card's name ('' when there is none)."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                           text=True, timeout=EXE_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
    except Exception:  # noqa: BLE001
        return ""


def torch_cuda() -> tuple[bool, str]:
    """(PyTorch sees the card, why not) -- asked in a process of its own (importing PyTorch takes seconds)."""
    code = ("import torch; ok = torch.cuda.is_available(); "
            "print('OK' if ok else 'NO', torch.version.cuda or 'cpu')")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TORCH_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "PyTorch did not answer within a minute and a half"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"PyTorch could not be started ({e})"
    out = (r.stdout or "").strip().split()
    if r.returncode != 0 or not out:
        tail = ((r.stderr or "").strip().splitlines() or ["it does not load"])[-1]
        return False, f"PyTorch does not load ({tail[:140]})"
    if out[0] == "OK":
        return True, ""
    return False, ("this PyTorch is built without CUDA" if len(out) > 1 and out[1] == "cpu" else
                   "PyTorch does not see the graphics card (the NVIDIA driver may be too old for it)")


READERS = ("dist_version", "module_problem", "module_home", "exe_version", "gpu_name", "torch_cuda")


class Readers:
    """What ``check`` reads with: this module's readers (looked up at every call, so a test may replace them), or
    the ones given (``Readers(exe_version=fake)``)."""

    def __init__(self, **given: Callable):
        unknown = set(given) - set(READERS)
        if unknown:
            raise TypeError(f"unknown readers: {sorted(unknown)}")
        self._given = given

    def __getattr__(self, name: str) -> Callable:
        if name.startswith("_") or name not in READERS:
            raise AttributeError(name)
        return self._given.get(name) or globals()[name]


# ---------------------------------------------------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Problem:
    key: str
    name: str
    text: str                       # plain words: "PySceneDetect 0.6.4 is too old (0.7.1 needed)"
    fix: str = PIP                  # how setup_app fixes it (Want.fix)
    hand: str = ""                  # the step done by hand
    closed: bool = False            # only while the app is closed
    have: str = ""                  # the version found ('' = none)

    def to_dict(self) -> dict:
        return asdict(self)


def _same_folder(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return False


def check_one(w: Want, rd: Readers) -> Problem | None:
    """What is wrong with one entry (None: fine)."""
    def bad(text: str, have: str = "") -> Problem:
        return Problem(w.key, w.name, text, w.fix, w.hand, w.closed, have)
    if w.exe:
        have, _where = rd.exe_version(w.exe)
        if have is None:
            return bad(f"{w.name} is missing ({w.what})")
        if w.least and have and older(have, w.least):
            return bad(f"{w.name} {have} is too old ({w.least} needed)", have)
        return None
    have = rd.dist_version(w.dist) if w.dist else None
    if w.dist and have is None:
        return bad(f"{w.name} is missing ({w.what})")
    if w.exact and have is not None and not same(have, w.exact):
        word = "too old" if older(have, w.exact) else "a different version"
        return bad(f"{w.name} {have} is {word} ({w.exact} needed)", have or "")
    if w.least and have is not None and older(have, w.least):
        return bad(f"{w.name} {have} is too old ({w.least} needed)", have or "")
    if w.module:
        why = rd.module_problem(w.module)
        if why:
            return bad(f"{w.name} {have or ''} is broken: {why}".replace("  ", " "), have or "")
    if w.home:
        found = rd.module_home(w.module or w.dist)
        if found and not _same_folder(found, w.home):
            return Problem(w.key, w.name, f"{w.name} is installed from another folder ({found})", HAND, w.hand,
                           w.closed, have or "")
    if w.gpu and rd.gpu_name():
        local = str(have or "").partition("+")[2]
        if local == "cpu":
            return bad(f"{w.name} {have} cannot use the graphics card (a build without CUDA)", have or "")
        ok, why = rd.torch_cuda()
        if not ok:
            return Problem(w.key, w.name, f"{w.name} {have} cannot use the graphics card: {why}",
                           HAND if "driver" in why else w.fix,
                           "update the NVIDIA driver (GeForce Experience / nvidia.com), then open the app again"
                           if "driver" in why else "", w.closed, have or "")
    return None


def check(readers: Readers | None = None, wants: Iterable[Want] | None = None) -> list[Problem]:
    """Every problem on this computer, most important first (the table's order)."""
    rd = readers or Readers()
    out = []
    for w in (list(wants) if wants is not None else all_wants()):
        try:
            p = check_one(w, rd)
        except Exception as e:  # noqa: BLE001 - one odd entry never stops the others
            p = Problem(w.key, w.name, f"{w.name} could not be checked ({type(e).__name__})", w.fix, w.hand,
                        w.closed)
        if p is not None:
            out.append(p)
    return out


def summary(problems: list[Problem], most: int = 3) -> str:
    """ONE line for the window: 'PySceneDetect 0.6.4 is too old (0.7.1 needed) · yt-dlp is missing (...)'."""
    if not problems:
        return ""
    bits = [p.text for p in problems[:most]]
    more = len(problems) - most
    return " · ".join(bits) + (f" · and {more} more" if more > 0 else "")


def pip_plan(problems: Iterable[Problem], wants: Iterable[Want] | None = None, app_open: bool = False
             ) -> tuple[list[tuple[str, list[str]]], list[Problem]]:
    """(the pip installs that fix ``problems`` -- [(names, pip arguments)], one per different argument list --, the
    problems pip cannot fix now: by hand, or the window's own files while the app is open)."""
    table = {w.key: w for w in (list(wants) if wants is not None else all_wants())}
    jobs: dict[tuple[str, ...], list[str]] = {}
    left = []
    for p in problems:
        w = table.get(p.key)
        if w is None or p.fix != PIP or not w.pip:
            if p.fix not in (ARIA2C, DENO):
                left.append(p)
            continue
        if w.closed and app_open:
            left.append(p)
            continue
        jobs.setdefault(tuple(w.pip), [])
        if w.name not in jobs[tuple(w.pip)]:
            jobs[tuple(w.pip)].append(w.name)
    return [(", ".join(names), list(args)) for args, names in jobs.items()], left


# ---------------------------------------------------------------------------------------------------------------------
# the app's call: the check in its own process (python -m mr_app.core.versions)
# ---------------------------------------------------------------------------------------------------------------------

def check_in_process(python: str | os.PathLike | None = None, timeout_s: float = CHECK_TIMEOUT_S
                     ) -> list[Problem]:
    """The check run by the venv's Python in a process of its own (a broken package can never touch the window);
    its JSON read back. A check that cannot run says so as one problem."""
    py = str(python or sys.executable)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = subprocess.run([py, "-m", "mr_app.core.versions", "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout_s, cwd=str(REPO / "tools" / "app"),
                           env=env, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return [Problem("check", "the check", "the version check did not finish within 3 minutes", HAND)]
    except (OSError, subprocess.SubprocessError) as e:
        return [Problem("check", "the check", f"the version check could not start ({e})", HAND)]
    for line in reversed((r.stdout or "").strip().splitlines()):
        if line.startswith("{"):
            try:
                return [Problem(**d) for d in json.loads(line).get("problems", [])]
            except (ValueError, TypeError):
                break
    tail = ((r.stderr or "").strip().splitlines() or ["no answer"])[-1]
    return [Problem("check", "the check", f"the version check failed ({tail[:140]})", HAND)]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    probs = check()
    if "--json" in argv:
        print(json.dumps({"problems": [p.to_dict() for p in probs]}, ensure_ascii=False))
    else:
        for p in probs:
            print(f"- {p.text}")
        print("everything as the project expects" if not probs else f"{len(probs)} problem(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
