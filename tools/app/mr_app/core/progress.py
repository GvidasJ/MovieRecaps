"""The tool's own progress lines, read for the queue's progress bar.

The run logs "S5.2 visual search: 40 % -- about 2 min left; whole run: about 5 min left" at least every 30 s
(match_cuts.eta); match_cuts.batch.latest_progress(run_dir) gives the newest one (or the stage it is in, with the
whole run's time left counted down). From it: the step in plain words, the time left, and the share done."""
from __future__ import annotations

import re
from dataclasses import dataclass

_WHOLE = re.compile(r"whole run: (?:about (\d+) h (\d+) min|about (\d+) min|under (a minute|2 min))")
_STEP = re.compile(r"^\s*(S[\d.]+[a-z]?)\s+(.+?)(?::\s*(\d+)\s*%|\s+--|;|:\s*still|$)")

# the stages' names in plain words (the tool's own names otherwise)
PLAIN = {
    "S2": "reading the videos", "S3": "making small copies", "S4": "finding the picture area",
    "S5": "finding each frame in the RAW", "S6": "checking the cuts", "S7": "planning the edit",
    "S8": "writing the files", "S9": "checking the result", "S10": "writing the report",
}


@dataclass
class Progress:
    step: str = ""            # "S5.2 visual search"
    plain: str = ""           # "finding each frame in the RAW"
    step_pct: float | None = None
    left_s: float | None = None

    def text(self) -> str:
        """One short line for the queue: 'S5.2 visual search (40 %) -- about 5 min left'."""
        bits = []
        if self.step:
            bits.append(self.step + (f" ({self.step_pct:.0f} %)" if self.step_pct is not None else ""))
        if self.left_s is not None:
            bits.append(left_text(self.left_s))
        return " -- ".join(bits) if bits else "starting"


def left_text(s: float) -> str:
    s = max(0.0, float(s))
    if s < 60:
        return "under a minute left"
    m = int(round(s / 60.0))
    return f"about {m // 60} h {m % 60:02d} min left" if m >= 60 else f"about {m} min left"


def whole_left_s(text: str) -> float | None:
    m = _WHOLE.search(text or "")
    if not m:
        return None
    if m.group(1):
        return 3600.0 * int(m.group(1)) + 60.0 * int(m.group(2))
    if m.group(3):
        return 60.0 * int(m.group(3))
    return 45.0 if m.group(4) == "a minute" else 90.0


def parse(text: str) -> Progress:
    """The newest progress text of a run (match_cuts.batch.latest_progress) -> Progress."""
    text = (text or "").strip()
    out = Progress(left_s=whole_left_s(text))
    m = _STEP.match(text)
    if m:
        code, name = m.group(1), m.group(2).strip(" -:;")
        name = re.sub(r":.*$", "", name).strip()
        out.step = f"{code} {name}".strip()
        out.plain = PLAIN.get(code.split(".")[0], name)
        if m.group(3):
            out.step_pct = float(m.group(3))
    return out


MAX_CHANGE = 0.20          # night 15: the whole run's time moves at most this much (relative) per new estimate
STUCK_S = 300.0            # no new output and no CPU for this long: "seems stuck"
BUSY_CPU_S = 1.0           # CPU seconds of the run's processes that count as working
STUCK_TEXT = "seems stuck (no output and no CPU for 5 min) -- Stop, then Try again?"


def _round_min(s: float, step: int) -> int:
    return max(1, int(round(s / 60.0 / step)) * step)


def _hm(m: int) -> str:
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def range_text(left_s: float) -> str:
    """'about 25-35 min left' (5-minute steps from 10 min, 1-minute steps under it, hours past 90 min)."""
    s = max(0.0, float(left_s))
    if s < 60:
        return "under a minute left"
    lo, hi = 0.85 * s, 1.2 * s
    if hi >= 90 * 60:
        a, b = _round_min(lo, 10), _round_min(hi, 10)
        return f"about {_hm(a)} - {_hm(b)} left"
    step = 5 if s >= 10 * 60 else 1
    a, b = _round_min(lo, step), _round_min(hi, step)
    return f"about {a} min left" if a >= b else f"about {a}-{b} min left"


class Honest:
    """Night 15, honest progress: the tool's own estimate (eta.py: each stage from earlier runs on this computer)
    smoothed -- the whole run's time moves at most MAX_CHANGE per new estimate -- shown as a range; the share done
    never goes backwards; a run with no new output and no CPU for STUCK_S "seems stuck"."""

    def __init__(self, estimate_s: float = 0.0):
        self.estimate = float(estimate_s or 0.0)
        self.total: float | None = None
        self.share = 0.0
        self._raw_left: float | None = None
        self._sig: object = None
        self._cpu: float | None = None
        self._active_at = 0.0

    def update(self, elapsed_s: float, left_s: float | None, sig: object = None, cpu_s: float | None = None,
               line: str | None = None) -> dict:
        """{'share' 0..1 (never smaller than before), 'left_s', 'left' (the range in words), 'stuck'}. ``line``: the
        tool's progress line the estimate is from (a new line or a new value: a new estimate); ``sig``: what the run
        showed last (its progress line, its log's size); ``cpu_s``: its processes' CPU seconds."""
        e = max(0.0, float(elapsed_s))
        key = (line, left_s) if line is not None else left_s
        if left_s is not None and key != self._raw_left:             # a new estimate from the tool
            self._raw_left = key
            want = e + float(left_s)
            self.total = want if self.total is None else min(max(want, self.total * (1 - MAX_CHANGE)),
                                                             self.total * (1 + MAX_CHANGE))
        elif self.total is None and self.estimate > 0:
            self.total = self.estimate
        left = None
        if self.total is not None:
            if self.total <= e:            # past the estimate: the end is near, never "0 min" for long
                self.total = e + max(30.0, 0.05 * e)
            left = self.total - e
            share = min(0.99, e / self.total) if self.total > 0 else 0.0
        else:
            share = 0.0
        self.share = max(self.share, share)
        if sig != self._sig or (cpu_s is not None and (self._cpu is None or cpu_s - self._cpu >= BUSY_CPU_S)):
            self._sig, self._active_at = sig, e
            if cpu_s is not None:
                self._cpu = cpu_s
        stuck = e - self._active_at >= STUCK_S
        return {"share": self.share, "left_s": left, "left": None if left is None else range_text(left),
                "stuck": stuck}


def share_done(elapsed_s: float, left_s: float | None, estimate_s: float = 0.0) -> float:
    """0..1 for the bar: the time spent against the time spent plus the tool's 'whole run' time left; without
    one, against the estimate (never full before the run ends)."""
    if left_s is not None:
        tot = elapsed_s + left_s
        return min(0.99, elapsed_s / tot) if tot > 0 else 0.0
    if estimate_s > 0:
        return min(0.95, elapsed_s / estimate_s)
    return 0.0
