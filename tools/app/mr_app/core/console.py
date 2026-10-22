"""WHAT IT'S DOING NOW (night 15, task 4): a run's log (extras\\match_cuts.log) read as it grows and told in plain
words, one line per thing that happened, with its time -- newest at the bottom:

    04:51  Finding shots -- 66 of 278 pieces searched (24 %), about 3 min left in this step
    04:53  Found in the RAW: 100 % of the picture, 94 % of the sound
    04:55  Double-checking cuts -- the cuts: needs a look

``Console.poll()`` reads only what the log gained since the last call (the app calls it every 2 s); the last line
is a live one, redrawn on every poll: the step, how long it has been at it, and -- when the run's ffmpeg is reading a
video (a conversion) -- how far it is and how fast ("converting 1.8x real time, 12 of 46 min done", from the bytes
the ffmpeg process has read of its input: no change to the tool). The full technical log stays in the run folder;
``text()`` is everything shown, for "Copy log"."""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Callable

MAX_LINES = 400
TAIL_START = 256 * 1024                # a log opened late: its last 256 KB only

_LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d)(:\d\d),\d+ (\w+) match_cuts (.*)$")
_START = re.compile(r"^== (.+?)\s*$")
_END = re.compile(r"^ {3}(.+): ([\d.]+)s$")
_COUNT = re.compile(r"(?:(\w[\w ]*?)(?: (\d+)/(\d+))?: )?(\d+)/(\d+) tasks done")
_STILL = re.compile(r"still running \(([^)]+)\)")
_STEP_PCT = re.compile(r"\| (.+?): (\d+) % -- ([^;]+);")

# what the checks of S9 look at, in plain words
CHECKS = {
    "s9_1_coverage": "every competitor frame is covered", "c2_cuts": "the cuts",
    "s9_2b_temporal": "the movement between frames", "s9_2c_refit": "the framing of each frame",
    "s9_3_preview_file": "the check video", "s9_3_visual": "the check video against the competitor",
    "s9_4_cut_images": "the frames at the cuts", "s9_5_audio": "the sound lines up", "c5_audio": "the sound lines up",
    "c3_source_frames": "the RAW frames shown", "c4_speed_framing": "speed and framing",
    "s9_8_deliverables": "the Premiere files",
}
VERDICT = {"pass": "OK", "fail": "needs a look", "warn": "OK, with a note"}
COUNTED = {"search": "pieces searched", "eval": "frames placed", "pairs": "frame pairs checked",
           "measure": "frames measured", "final": "frames finished"}


def _short(stage: str) -> str:
    try:
        from match_cuts.stage_names import short
        return short(stage)
    except Exception:  # noqa: BLE001
        return stage


def _dur(s: float) -> str:
    s = int(round(max(0.0, s)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def translate(msg: str, level: str = "INFO") -> str | None:
    """One message of the tool's log (after 'match_cuts ') in plain words; None: nothing to tell you."""
    msg = msg.rstrip()
    if level == "DEBUG":
        return None
    if level in ("ERROR", "CRITICAL"):
        return "Problem: " + msg[:220]
    if level == "WARNING":
        return None                                   # (the checks' notes: in the report, not here)
    m = _START.match(msg)
    if m:
        st = m.group(1)
        if st.startswith("S8.") or st in ("S7 AE project",):
            return None
        return f"{_short(st)} -- started"
    m = _END.match(msg)
    if m:
        st, sec = m.group(1), float(m.group(2))
        if st.startswith("S8.") or st == "S7 AE project":
            return None
        return f"{_short(st)} -- done ({_dur(sec)})"
    if " | " in msg:                                   # a progress line: '<stage>: <counter> | <stage>: 40 % -- ...'
        head, _sep, tail = msg.partition(" | ")
        stage = head.split(":", 1)[0].strip()
        bits = []
        c = _COUNT.search(head)
        if c:
            what = COUNTED.get((c.group(1) or "").strip(), "done")
            bits.append(f"{c.group(4)} of {c.group(5)} {what}")
        s = _STILL.search(head)
        if s and not c:
            bits.append(f"still working ({s.group(1)})")
        p = _STEP_PCT.search(" | " + tail)
        if p:
            bits.append(f"{p.group(2)} %")
            left = p.group(3).strip()
            if left:
                bits.append(f"{left} in this step")
        return f"{_short(stage)} -- " + ", ".join(bits) if bits else None
    low = msg.lower()
    if msg.startswith("time estimate: "):
        m = re.search(r"the whole run about (.+?) \(.*?(from \d+ earlier run\(s\) on this machine)?\)$", msg)
        if m:
            return f"Expected time: about {m.group(1)}" + (" (from earlier runs on this PC)" if m.group(2) else "")
        return None
    if msg.startswith("pair check: sound: "):
        m = re.search(r"sound: (\d+) of (\d+) pieces found \((\d+) %\)", msg)
        goes = "-> goes on" in msg
        if m:
            return (f"The sound of the competitor was found in the RAW ({m.group(1)} of {m.group(2)} pieces) -- "
                    + ("the same scene, going on" if goes else "checking more"))
        return None
    if msg.startswith("pair check: ") and ("WRONG PAIR" in msg or "stops" in low):
        return "Pair check: " + msg[len("pair check: "):][:200]
    if msg.startswith("found in the RAW: "):
        m = re.search(r"(\d+) % of the competitor's frames, (\d+) % of its sound", msg)
        if m:
            return f"Found in the RAW: {m.group(1)} % of the picture, {m.group(2)} % of the sound"
    if msg.startswith("conform raw: ") and " -> " in msg:
        return "Converting the RAW once (on the graphics card)" if "NVENC" in msg else "Converting the RAW once"
    if msg.startswith("conform raw: ") and " verified " in msg:
        m = re.search(r"in ([\d.]+)s \(([\d.]+) fps encode\)", msg)
        return "The converted RAW checked against the original -- OK" + (
            f" ({_dur(float(m.group(1)))})" if m else "")
    if msg.startswith("audio: ") and "windows confident" in msg:
        m = re.match(r"audio: (\d+)/(\d+) windows confident", msg)
        if m:
            return f"Sound matched: {m.group(1)} of {m.group(2)} pieces sure"
    if msg.startswith("sparse search: "):
        m = re.match(r"sparse search: (\d+) anchors on (\d+)/(\d+) searched frames", msg)
        if m:
            return f"Picture search: {m.group(2)} of {m.group(3)} looked-at frames found in the RAW"
    if msg.startswith("frame map: "):
        m = re.search(r"'match': (\d+), 'none': (\d+)", msg)
        if m:
            return (f"Every frame placed: {m.group(1)} found in the RAW, {m.group(2)} not in the RAW")
    if msg.startswith("speech map of the RAW: "):
        m = re.search(r"(\d+) words", msg)
        if m:
            return f"Speech recognised in the RAW: {m.group(1)} words"
    if msg.startswith("captions: ") and " captions (" in msg:
        m = re.match(r"captions: (\d+) captions", msg)
        if m:
            return f"Captions written: {m.group(1)}"
    if msg.startswith("verify "):
        m = re.match(r"verify (\S+): (\w+)", msg)
        if m and m.group(2) in VERDICT:
            what = CHECKS.get(m.group(1))
            if what:
                return f"Double-checking cuts -- {what}: {VERDICT[m.group(2)]}"
        return None
    if msg.startswith("music: "):
        return "Music: " + msg[len("music: "):][:200]
    if msg.startswith("stretch: "):
        return "The competitor shows the RAW stretched sideways -- using a stretched copy"
    if msg.startswith("PICTURE NOT FOUND") or msg.startswith("WRONG PAIR") or msg.startswith("NOT IN THIS RAW"):
        return msg[:220]
    return None


class Console:
    """The plain-words lines of one run (module text). ``reading``: () -> (input seconds read, input seconds, speed)
    or None -- what the run's ffmpeg is converting (JobRun.reading)."""

    def __init__(self, log: Path, reading: Callable[[], tuple[float, float, float] | None] | None = None,
                 clock: Callable[[], _dt.datetime] = _dt.datetime.now):
        self.log = Path(log)
        self.reading = reading
        self.clock = clock
        self.lines: list[str] = []
        self.live = ""
        self.pos = -1
        self._rest = b""
        self.stage = ""
        self.stage_t0: _dt.datetime | None = None
        self._stack: list[tuple[str, _dt.datetime | None]] = []
        self._last_progress = False

    def _add(self, hhmm: str, text: str, progress: bool = False) -> None:
        line = f"{hhmm}  {text}"
        if progress and self._last_progress and self.lines and self.lines[-1].split("  ", 1)[-1].startswith(
                text.split(" -- ", 1)[0] + " -- "):
            self.lines[-1] = line                       # the same step's newer count: in place of the older one
        else:
            self.lines.append(line)
        self._last_progress = progress
        if len(self.lines) > MAX_LINES:
            del self.lines[: len(self.lines) - MAX_LINES]

    def poll(self) -> bool:
        """Read what the log gained; True when something new is shown (the live line counts)."""
        before = (len(self.lines), self.lines[-1] if self.lines else "", self.live)
        try:
            size = self.log.stat().st_size
            if self.pos < 0:
                self.pos = max(0, size - TAIL_START)
            if size < self.pos:                         # a new log (the run made again): from its start
                self.pos, self._rest = 0, b""
            if size > self.pos:
                with self.log.open("rb") as f:
                    f.seek(self.pos)
                    data = f.read(size - self.pos)
                self.pos = size
                data = self._rest + data
                parts = data.split(b"\n")
                self._rest = parts.pop()                # (a line still being written)
                for raw in parts:
                    self._line(raw.decode("utf-8", "replace").rstrip("\r"))
        except OSError:
            pass
        self.live = self._live()
        return (len(self.lines), self.lines[-1] if self.lines else "", self.live) != before

    def _line(self, ln: str) -> None:
        m = _LINE.match(ln)
        if not m:
            return
        day, hhmm, sec, level, msg = m.groups()
        s, e = _START.match(msg), _END.match(msg)
        if s and not s.group(1).startswith("S8."):
            try:
                t0 = _dt.datetime.strptime(f"{day} {hhmm}{sec}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                t0 = None
            self._stack.append((s.group(1), t0))
        elif e and self._stack and self._stack[-1][0] == e.group(1):
            self._stack.pop()
            started = f"{_short(e.group(1))} -- started"
            if float(e.group(2)) < 1.5 and self.lines and self.lines[-1].endswith(started):
                self.lines.pop()                        # a step of a moment: neither line
                self._sync()
                return
        self._sync()
        text = translate(msg, level)
        if text:
            self._add(hhmm, text, progress=" | " in msg)

    def _sync(self) -> None:
        self.stage, self.stage_t0 = self._stack[-1] if self._stack else ("", None)

    def _live(self) -> str:
        if not self.stage:
            return ""
        now = self.clock()
        bits = [f"{now:%H:%M}  now: {_short(self.stage)}"]
        # the conversion's speed while the RAW is prepared (other steps' ffmpeg reads a few seconds here and there)
        if self.reading is not None and self.stage.startswith(("S2 ", "S1.5 ")):
            try:
                r = self.reading()
            except Exception:  # noqa: BLE001
                r = None
            if r:
                done_s, total_s, speed = r
                bits.append(f"converting {speed:.1f}x real time, {int(done_s // 60)} of "
                            f"{max(1, int(round(total_s / 60)))} min done")
        if self.stage_t0 is not None:
            bits.append(f"{_dur((now - self.stage_t0).total_seconds())} in this step")
        return " -- ".join(bits)

    def shown(self) -> list[str]:
        return self.lines + ([self.live] if self.live else [])

    def text(self) -> str:
        """Everything shown, for "Copy log"."""
        return "\n".join(self.shown())
