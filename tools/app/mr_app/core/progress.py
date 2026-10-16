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


def share_done(elapsed_s: float, left_s: float | None, estimate_s: float = 0.0) -> float:
    """0..1 for the bar: the time spent against the time spent plus the tool's 'whole run' time left; without
    one, against the estimate (never full before the run ends)."""
    if left_s is not None:
        tot = elapsed_s + left_s
        return min(0.99, elapsed_s / tot) if tot > 0 else 0.0
    if estimate_s > 0:
        return min(0.95, elapsed_s / estimate_s)
    return 0.0
