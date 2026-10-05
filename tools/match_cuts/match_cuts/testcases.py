"""testcases.py: the library of real test videos (``tests/real/<case>/``) that check-all runs and ``learn`` adds to.

A case folder holds:

* ``competitor.mp4`` and ``raw.mp4`` -- the inputs (a RAW over MAX_BYTES is committed as a smaller copy: same size,
  same frame rate, same audio; small_copy);
* ``answer.srt`` (optional) -- the user's finished captions: check-all's caption answer key;
* ``answer_edit.xml`` / ``answer_edit.json`` (optional) -- the user's finished edit: the timeline answer.srt is
  timed on (an FCP7 / Premiere XML, or {"audio": [{start, end, src_in, speed}]} seconds of the RAW it plays, its
  "track" picture or sound) -- and, on the user's own edit (timeline "edit"), check-all's cut answer key
  (edit_score.py);
* ``case.json`` (optional) -- {"options": [extra command-line options], "timeline": "edit" | "competitor" (the key
  is timed on the competitor's own edit: answer_edit.json then holds the competitor's timeline -> RAW), "notes"}.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
CASES_DIR = REPO / "tests" / "real"
MAX_BYTES = 100 * 1000 * 1000          # GitHub refuses files over 100 MB
SMALL_TARGET = 90 * 1000 * 1000        # a smaller copy aims here (room for the container and the audio)


@dataclass
class Case:
    name: str
    dir: Path
    competitor: Path
    raw: Path
    options: list[str] = field(default_factory=list)
    answer_srt: Path | None = None
    answer_edit: Path | None = None
    timeline: str = "edit"
    notes: str = ""

    @property
    def has_key(self) -> bool:
        return self.answer_srt is not None and self.answer_edit is not None

    @property
    def has_cut_key(self) -> bool:
        """The answer key holds the user's own edit (not the competitor's timeline): its cuts are scored too."""
        return self.answer_edit is not None and self.timeline == "edit"


def load(d: Path) -> Case | None:
    """The case in folder d (None: not a case -- no competitor and RAW)."""
    d = Path(d)
    meta: dict[str, Any] = {}
    if (d / "case.json").is_file():
        meta = json.loads((d / "case.json").read_text(encoding="utf-8"))
    comp, raw = d / meta.get("competitor", "competitor.mp4"), d / meta.get("raw", "raw.mp4")
    if not comp.is_file() or not raw.is_file():
        return None
    srt = d / "answer.srt"
    edit = next((d / n for n in ("answer_edit.json", "answer_edit.xml") if (d / n).is_file()), None)
    return Case(d.name, d, comp, raw, list(meta.get("options") or []), srt if srt.is_file() else None, edit,
                str(meta.get("timeline") or "edit"), str(meta.get("notes") or ""))


def cases(names: list[str] | None = None, root: Path = CASES_DIR) -> list[Case]:
    """Every case under tests/real (or the named ones, in that order)."""
    if names:
        out = []
        for n in names:
            c = load(root / n)
            if c is None:
                raise ValueError(f"no test case {n!r} in {root} (a folder with competitor.mp4 and raw.mp4)")
            out.append(c)
        return out
    return [c for c in (load(d) for d in sorted(root.iterdir()) if d.is_dir()) if c is not None] if root.is_dir() else []


def answer_timeline(case: Case):
    """The timeline answer.srt is timed on (caption_score.Timeline): the user's finished XML, or answer_edit.json."""
    from .caption_score import Timeline
    if case.answer_edit is None:
        raise ValueError(f"{case.name}: no answer_edit.xml / answer_edit.json")
    if case.answer_edit.suffix.lower() == ".xml":
        return Timeline.from_xml(case.answer_edit)
    return Timeline.from_json(case.answer_edit)


# ---------------------------------------------------------------------------------------------------------------------
# a RAW over 100 MB: a smaller copy for the test library
# ---------------------------------------------------------------------------------------------------------------------

def probe(path: Path) -> dict:
    from .common import ffprobe_bin
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries",
                          "format=duration,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,bit_rate",
                          "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", check=True)
    return json.loads(out.stdout)


def small_copy(src: Path, dst: Path, target: int = SMALL_TARGET, limit: int = MAX_BYTES) -> dict:
    """Copy src to dst under ``limit`` bytes: the same width, height and frame rate (every frame kept, the same
    timestamps), the audio stream copied unchanged; H.264 at a quality capped so the file lands near ``target``.
    A file already under the limit is copied as it is. Returns {"bytes", "reencoded", "video_kbps", ...}."""
    from .common import ffmpeg_bin
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.stat().st_size <= limit:
        shutil.copyfile(src, dst)
        return {"bytes": dst.stat().st_size, "reencoded": False}
    info = probe(src)
    dur = float(info["format"]["duration"])
    audio = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    a_bps = sum(int(s.get("bit_rate") or 192000) for s in audio)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    kbps = int(max(300_000, (target * 8 / dur - a_bps) * 0.97) / 1000)
    for attempt in range(4):
        cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
               "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
               "-pix_fmt", "yuv420p", "-fps_mode", "passthrough", "-c:a", "copy", "-movflags", "+faststart",
               str(tmp)]
        subprocess.run(cmd, check=True)
        size = tmp.stat().st_size
        if size <= limit:
            break
        kbps = int(kbps * 0.85 * limit / size)
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"could not make {src.name} smaller than {limit} bytes")
    tmp.replace(dst)
    got = probe(dst)
    v0 = next(s for s in info["streams"] if s.get("codec_type") == "video")
    v1 = next(s for s in got["streams"] if s.get("codec_type") == "video")
    same = (v0["width"], v0["height"], v0["r_frame_rate"]) == (v1["width"], v1["height"], v1["r_frame_rate"])
    if not same:
        raise RuntimeError(f"the smaller copy changed the size or frame rate: {v0} -> {v1}")
    return {"bytes": dst.stat().st_size, "reencoded": True, "video_kbps_cap": kbps, "width": v1["width"],
            "height": v1["height"], "fps": v1["r_frame_rate"], "seconds": dur}
