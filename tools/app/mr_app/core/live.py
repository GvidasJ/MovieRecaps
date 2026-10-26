"""THE RUNNING EDIT, LIVE (run 19, task 3) -- what the queue row's animations show, read from the run's own files while
it runs. Nothing here is the tool's code or its caches: only what the tool writes anyway, read as it is written.

* the run's log (``<run>\\extras\\match_cuts.log``): the stage it is in ('== S5.2 visual search'), the stages done
  ('   S5.2 visual search: 303.56s'), the search's own counter ('search 1/2: 110/262 tasks done'), its end ('sparse
  search: 654 anchors on 523/524 searched frames'), the competitor's frames and rate (its probe line), the captions
  ('captions: 29 captions ...') and the end ('1_edit.xml: 13 clips on a 23.976 fps sequence ...');
* the run's decision log (``<work>\\decisions.jsonl``, flushed record by record; the stages answered from the cache
  write theirs again at once): every competitor frame the search FOUND in the RAW (stage visual_match, "anchor":
  its frame, the RAW frame, its score) and every piece of frames placed (stage refine, "track");
* at the end: the cut list (``cutlist.json``: the competitor's shots -- found, unsure, not in the RAW) and the
  report's XML FLASH line.

``Live.poll()`` reads only what was added since the last call (a few kilobytes) and says whether anything changed;
``snapshot()`` is a plain dict for the window (the queue row and the New video row draw it)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from fractions import Fraction
from pathlib import Path

SURE_ZNCC = 0.90                 # a found frame this alike (its best evidence) is green, below it amber (unsure)
WAVE_BINS = 240                  # the competitor's sound as this many peaks
_STAGE = re.compile(r"INFO match_cuts == (S[\d.]+[^\r\n]*)$")
_DONE = re.compile(r"INFO match_cuts    (S[\d.]+[^:]*): [\d.]+s$")
_SEARCH = re.compile(r"(S5\.2 visual search[^:]*): search (\d+)/(\d+): (\d+)/(\d+) tasks done")
_SEARCHED = re.compile(r"sparse search: (\d+) anchors on (\d+)/(\d+) searched frames")
_PROBE = re.compile(r"probe .+ \[(competitor|raw)\]: .+? @ (\d+(?:/\d+)?) fps .*?, (\d+) frames")
_CAPS = re.compile(r"INFO match_cuts captions: (\d+) captions ")
_FINAL = re.compile(r"INFO match_cuts 1_edit\.xml: (\d+) clips on .*?; (\d+) captions timed to it")
_FLASH = re.compile(r"\|\s*`XML FLASH`\s*\|[^|]*\|\s*([^|]+?)\s*\|")


class _Tail:
    """New complete lines of a growing text file (from where the last call stopped; a file made anew: from 0)."""

    def __init__(self, path: Path):
        self.path, self.pos, self.rest, self.ino = Path(path), 0, b"", None
        self.resets = 0                                       # how often the file was made anew

    def lines(self, limit: int = 4 << 20) -> list[str]:
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        key = (st.st_ino, st.st_ctime_ns)
        if self.ino is not None and (key != self.ino or st.st_size < self.pos):
            self.pos, self.rest = 0, b""                      # truncated / made again: from the start
            self.resets += 1
        self.ino = key
        if st.st_size <= self.pos:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(limit)
        except OSError:
            return []
        self.pos += len(data)
        data = self.rest + data
        parts = data.split(b"\n")
        self.rest = parts.pop()                              # (an unfinished last line: next time)
        return [p.decode("utf-8", "replace").rstrip("\r") for p in parts]


def wave_of(media: str | os.PathLike, bins: int = WAVE_BINS) -> list[float]:
    """The competitor's sound as ``bins`` peaks 0..1 (its first channel, 8 kHz); [] when it has none."""
    try:
        from match_cuts.common import ffmpeg_bin
        exe = ffmpeg_bin()
    except Exception:  # noqa: BLE001
        exe = "ffmpeg"
    try:
        r = subprocess.run([exe, "-v", "error", "-nostdin", "-i", str(media), "-map", "0:a:0", "-ac", "1", "-ar",
                            "8000", "-f", "s16le", "-"], capture_output=True, timeout=60,
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return []
    import numpy as np
    y = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0 if r.returncode == 0 else np.zeros(0)
    if not len(y):
        return []
    edges = np.linspace(0, len(y), bins + 1).astype(int)
    peaks = [float(np.abs(y[a:b]).max()) if b > a else 0.0 for a, b in zip(edges, edges[1:])]
    top = max(peaks) or 1.0
    return [round(p / top, 4) for p in peaks]


class Live:
    """A running edit's live numbers (module docstring)."""

    def __init__(self, run_dir: str | os.PathLike, work: str | os.PathLike | None, competitor: str | os.PathLike | None,
                 started: float | None = None):
        self.run_dir = Path(run_dir)
        self.log = _Tail(self.run_dir / "extras" / "match_cuts.log")
        self.dec = _Tail(Path(work) / "decisions.jsonl") if work else None
        self.started = time.time() if started is None else float(started)
        self.competitor = Path(competitor) if competitor else None
        self.wave: list[float] = []
        self.comp_fps: float = 0.0
        self.comp_frames = 0
        self.stage, self.stages_done = "", []
        self.search = {"pass": 0, "passes": 0, "done": 0, "total": 0}
        self.searched: tuple[int, int] | None = None          # (frames found, frames searched) at the search's end
        self.found: dict[int, tuple[int, float]] = {}         # competitor frame -> (its RAW frame, its best score)
        self.raw_fps: float = 0.0
        self.placed = 0                                       # the furthest competitor frame placed (refine tracks)
        self.pieces = 0                                       # pieces of frames placed (refine tracks)
        self.captions: int | None = None
        self.final: dict | None = None
        self.shots: tuple[int, int] | None = None             # (found, all): the edit's clips (+ not in the RAW)
        self.clips: int | None = None                         # the delivered edit's clips (its last line)
        self.gaps: int | None = None                          # the competitor's stretches not in the RAW (cut list)
        self.coverage: list[tuple[float, float, str]] = []    # (t0, t1, kind), the cut list's
        self._dec_ok = False

    # -- reading --
    def load_wave(self) -> None:
        if self.competitor is not None and self.competitor.is_file() and not self.wave:
            self.wave = wave_of(self.competitor)

    def poll(self) -> bool:
        """Read what the run added since the last call; True when anything changed."""
        changed = False
        for ln in self.log.lines():
            changed |= self._log_line(ln)
        if self.dec is not None and self.stage and self._fresh(self.dec.path):   # (never the run before's records)
            n = self.dec.resets
            lines = self.dec.lines()
            if self.dec.resets != n:                          # made anew since: what it said before is gone
                self.found, self.placed, self.pieces = {}, 0, 0
                changed = True
            for ln in lines:
                changed |= self._decision(ln)
        return changed

    def _fresh(self, p: Path) -> bool:
        try:
            return os.stat(p).st_mtime >= self.started - 5.0
        except OSError:
            return False

    def _log_line(self, ln: str) -> bool:
        m = _STAGE.search(ln)
        if m:
            self.stage = m.group(1).strip()
            return True
        m = _DONE.search(ln)
        if m:
            s = m.group(1).strip()
            if s not in self.stages_done:
                self.stages_done.append(s)
            if s.startswith("S5.4"):                         # the cuts decided: the cut list is written
                self.read_cutlist()
            return True
        m = _SEARCH.search(ln)
        if m:
            self.search = {"pass": int(m.group(2)), "passes": int(m.group(3)), "done": int(m.group(4)),
                           "total": int(m.group(5))}
            return True
        m = _SEARCHED.search(ln)
        if m:
            self.searched = (int(m.group(2)), int(m.group(3)))
            p = self.search.get("passes") or 1
            self.search = {"pass": p, "passes": p, "done": 1, "total": 1}
            return True
        if "probe: input {" in ln:                           # (the probe's record: also on a cache hit)
            try:
                d = json.loads(ln[ln.index("{"):])
            except ValueError:
                d = None
            if isinstance(d, dict) and d.get("fps") and d.get("frames"):
                try:
                    num, _, den = str(d["fps"]).partition("/")
                    fps = float(num) / float(den or 1)
                except (ValueError, ZeroDivisionError):
                    fps = 0.0
                if d.get("role") == "competitor" and not self.comp_frames and fps:
                    self.comp_fps, self.comp_frames = fps, int(d["frames"])
                    return True
                if d.get("role") == "raw" and not self.raw_fps and fps:
                    self.raw_fps = fps
            return False
        m = _PROBE.search(ln)
        if m:
            num, _, den = m.group(2).partition("/")
            fps = float(num) / float(den or 1)
            if m.group(1) == "competitor" and not self.comp_frames:
                self.comp_fps, self.comp_frames = fps, int(m.group(3))
                return True
            if m.group(1) == "raw" and not self.raw_fps:
                self.raw_fps = fps
            return False
        m = _CAPS.search(ln)
        if m:
            self.captions = int(m.group(1))
            return True
        m = _FINAL.search(ln)
        if m:
            self.final = dict(self.final or {}, clips=int(m.group(1)), captions=int(m.group(2)))
            self.captions = int(m.group(2))
            self.clips = int(m.group(1))
            self._shots()
            return True
        return False

    def _decision(self, ln: str) -> bool:
        if '"anchor"' not in ln and '"track"' not in ln:
            return False
        try:
            d = json.loads(ln)
        except ValueError:
            return False
        if d.get("stage") == "visual_match" and d.get("decision") == "anchor" and d.get("comp_frame") is not None:
            ev = [e for e in d.get("evidence") or [] if isinstance(e, dict)]
            if not ev:
                return False
            best = max(ev, key=lambda e: float(e.get("zncc") or 0.0))
            self.found[int(d["comp_frame"])] = (int(best.get("raw") or 0), float(best.get("zncc") or 0.0))
            return True
        if d.get("stage") == "refine" and d.get("decision") == "track":
            frames = [int(a[0]) for a in d.get("anchors") or [] if isinstance(a, (list, tuple)) and a]
            if frames:
                self.pieces += 1
                self.placed = max(self.placed, max(frames))
                return True
        return False

    def finish(self, result: dict | None = None) -> None:
        """The end of the run: the cut list's shots and coverage, the report's flash check, the result."""
        res = dict(result or {})
        self.poll()
        if not self.final:                                    # (a run read afterwards: its last lines)
            try:
                with open(self.run_dir / "extras" / "match_cuts.log", "rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - (256 << 10)))
                    for ln in f.read().decode("utf-8", "replace").splitlines():
                        if _FINAL.search(ln) or _CAPS.search(ln) or _PROBE.search(ln):
                            self._log_line(ln)
            except OSError:
                pass
        pct = self.read_cutlist()
        if pct is not None:
            res.setdefault("raw_pct", pct)
        try:
            rep = (self.run_dir / "extras" / "report.md").read_text(encoding="utf-8", errors="replace")
            m = _FLASH.search(rep)
            word = m.group(1) if m else ""
            res.setdefault("flashes", 0 if word.strip().upper().startswith("OK") else
                           int((re.search(r"\((\d+)\)", word) or [None, "0"])[1]) if word else None)
        except OSError:
            pass
        self.final = dict(self.final or {}, **{k: v for k, v in res.items() if k in (
            "label", "seconds", "raw_pct", "flashes")})

    def read_cutlist(self) -> int | None:
        """The cut list (written when the cuts are decided): the coverage -- found, unsure, not in the RAW -- and the
        stretches not in the RAW; returns the share of the competitor found in the RAW (%), None without it."""
        try:
            cl = json.loads((self.run_dir / "extras" / "cutlist.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(cl, dict):
            return None
        comp = cl.get("competitor") or {}
        try:
            num, _, den = str(comp.get("fps") or "30").partition("/")
            fps = float(Fraction(int(num), int(den or 1)))
        except (ValueError, ZeroDivisionError):
            fps = 30.0
        self.comp_fps = self.comp_fps or fps
        self.comp_frames = int(comp.get("frames") or self.comp_frames or 0)
        cov = []
        for s in (x for x in cl.get("segments") or [] if isinstance(x, dict)):
            a, b = int(s.get("comp_in") or 0) / fps, int(s.get("comp_out") or 0) / fps
            typ = str(s.get("type") or "")
            kind = ("not" if typ in ("not_in_raw", "other", "other_video", "black") else
                    "unsure" if typ == "uncertain" or s.get("uncertain") else "raw")
            cov.append((a, b, kind))
        self.coverage = cov
        self.gaps = sum(1 for i, (_a, _b, k) in enumerate(cov) if k == "not" and (i == 0 or cov[i - 1][2] != "not"))
        self._shots()
        dur = self.comp_frames / fps if self.comp_frames else max((b for _a, b, _k in cov), default=0.0)
        in_raw = sum(b - a for a, b, k in cov if k != "not")
        return round(100.0 * in_raw / dur) if dur > 0 else None

    def _shots(self) -> None:
        """SHOTS FOUND: the edit's clips, of them + the stretches not in the RAW (once the edit is made)."""
        if self.clips is not None:
            self.shots = (self.clips, self.clips + (self.gaps or 0))

    # -- what the window draws --
    @property
    def duration(self) -> float:
        return self.comp_frames / self.comp_fps if self.comp_frames and self.comp_fps else 0.0

    def live_coverage(self) -> list[tuple[float, float, str]]:
        """The cut list's coverage when there is one; before it, every competitor frame the search found, as a short
        piece where it is (green sure, amber unsure) -- the rest is "not searched yet"."""
        if self.coverage:
            return list(self.coverage)
        if not self.found or not self.comp_fps:
            return []
        frames = sorted(self.found)
        step = min((b - a for a, b in zip(frames, frames[1:]) if b > a), default=1)
        out: list[tuple[float, float, str]] = []
        for f in frames:
            kind = "raw" if self.found[f][1] >= SURE_ZNCC else "unsure"
            a, b = f / self.comp_fps, (f + step) / self.comp_fps
            if out and out[-1][2] == kind and abs(out[-1][1] - a) < 1e-6:
                out[-1] = (out[-1][0], b, kind)
            else:
                out.append((a, b, kind))
        return out

    def scan(self) -> float | None:
        """Where the search is along the competitor (0..1) while it searches; None before / after it."""
        if self.searched is not None or not self.search.get("total"):
            return None
        return min(1.0, self.search["done"] / float(self.search["total"]))

    def snapshot(self) -> dict:
        found = len(self.found)
        searched = self.searched[1] if self.searched else None
        return {"stage": self.stage, "stages_done": list(self.stages_done), "duration": self.duration,
                "fps": self.comp_fps, "frames": self.comp_frames, "wave": list(self.wave), "scan": self.scan(),
                "search_pass": (self.search.get("pass"), self.search.get("passes")),
                "coverage": self.live_coverage(), "found": found, "searched": searched,
                "shots": self.shots, "frames_found": (found, searched) if found else None,
                "placed": self.placed, "pieces": self.pieces, "captions": self.captions,
                "final": dict(self.final) if self.final else None,
                "searching": self.stage.startswith("S5.2") and self.searched is None,
                "placing": self.stage.startswith("S5.3")}


def timecode(frame: int, fps: float) -> str:
    """'00:00:09:12' (hours:minutes:seconds:frames)."""
    if fps <= 0:
        return "--:--:--:--"
    r = max(1, int(round(fps)))
    f = max(0, int(frame))
    s = int(f / fps)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{int(round(f - s * fps)) % r:02d}"
