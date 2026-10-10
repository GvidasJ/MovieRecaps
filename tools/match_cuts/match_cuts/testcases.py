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
  is timed on the competitor's own edit: answer_edit.json then holds the competitor's timeline -> RAW), "notes",
  "full_raw" / "full_competitor": the full-size original a smaller copy was made from, on the machine that made
  the case (relative to the repository; never committed) -- ``check-all --full-size`` runs on it where it exists,
  so a fix that only works on the smaller copy is caught (output/019: video018's "insurance" ending)}.
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
    full_raw: Path | None = None             # the full-size original on this machine (None: not here)
    full_competitor: Path | None = None
    raw_offset: float = 0.0                  # raw.mp4 is a window of full_raw starting at this second (window_copy)

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
    opts = [str(o).replace("{case}", str(d)) for o in meta.get("options") or []]      # {case}: the case's folder
    return Case(d.name, d, comp, raw, opts, srt if srt.is_file() else None, edit,
                str(meta.get("timeline") or "edit"), str(meta.get("notes") or ""),
                _here(meta.get("full_raw")), _here(meta.get("full_competitor")), float(meta.get("raw_offset") or 0.0))


def _here(p: Any) -> Path | None:
    """A case.json path (relative to the repository, or absolute) when that file is on this machine."""
    if not p:
        return None
    q = Path(str(p))
    q = q if q.is_absolute() else REPO / q
    return q if q.is_file() else None


def full_size(case: Case) -> Case | None:
    """The case on its full-size originals (named <case>@full), or None when none is on this machine."""
    if case.full_raw is None and case.full_competitor is None:
        return None
    import dataclasses
    edit = case.answer_edit
    if case.full_raw is not None and abs(case.raw_offset) > 1e-9 and edit is not None and edit.suffix == ".json":
        # raw.mp4 is a window of the full RAW: the key moves to the full RAW's time
        edit = shifted_answer(edit, case.raw_offset, REPO / "work" / "check-all" / "keys" / case.name)
    return dataclasses.replace(case, name=f"{case.name}@full", raw=case.full_raw or case.raw,
                               competitor=case.full_competitor or case.competitor, answer_edit=edit, raw_offset=0.0)


def for_raw(case: Case, raw: str | Path | None) -> Case:
    """The case as a run on ``raw`` scores against it: a window case (raw_offset) run on another file than its own
    raw.mp4 -- the full RAW, e.g. your output\\021 -- gets its key moved onto the full RAW's time."""
    import dataclasses
    if not raw or abs(case.raw_offset) < 1e-9 or case.answer_edit is None or case.answer_edit.suffix != ".json":
        return case
    try:
        if Path(raw).stat().st_size == case.raw.stat().st_size:
            return case                                  # the case's own window copy: its key as it is
    except OSError:
        return case
    edit = shifted_answer(case.answer_edit, case.raw_offset, REPO / "work" / "check-all" / "keys" / case.name)
    return dataclasses.replace(case, answer_edit=edit, raw_offset=0.0)


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


WINDOW_MIN_KBPS = 1500                  # a whole-video smaller copy below this video bit rate is too blurry to test on
WINDOW_MARGIN_S = 30.0                  # a window copy keeps this much of the RAW before and after what the edit plays


def copy_kbps(src: Path, target: int = SMALL_TARGET) -> float:
    """The video bit rate (kbps) a whole-video smaller copy of src could have under ``target`` bytes."""
    info = probe(src)
    dur = float(info["format"]["duration"])
    a_bps = sum(int(s.get("bit_rate") or 192000) for s in info["streams"] if s.get("codec_type") == "audio")
    return (target * 8 / max(dur, 1e-6) - a_bps) * 0.97 / 1000.0


def window_copy(src: Path, dst: Path, t0: float, t1: float, target: int = SMALL_TARGET, limit: int = MAX_BYTES) -> dict:
    """The part [t0, t1) of src (seconds) as a video of its own under ``limit`` bytes, its timestamps from 0: the same
    width, height and frame rate, every frame of that part (t0 moved back onto a frame of src), the sound of the same
    instants; H.264 at a quality capped so the file lands near ``target``. For a RAW too long for a sharp whole-video
    copy (21 or 95 minutes in 90 MB: a blur the analysis fails on, video020-fixed). Returns {"bytes", "offset" (the
    second of src the copy's first frame is), "frames", "fps", ...}."""
    import math
    from fractions import Fraction
    from .common import ffmpeg_bin
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src)
    v0 = next(s for s in info["streams"] if s.get("codec_type") == "video")
    fps = Fraction(str(v0["r_frame_rate"]))
    total = float(info["format"]["duration"])
    k0 = max(0, int(math.floor(max(0.0, t0) * fps)))
    k1 = max(k0 + 1, int(math.ceil(min(total, t1) * fps)))
    offset, dur = float(k0 / fps), float((k1 - k0) / fps)
    ss = max(0.0, offset - 5.0)                      # seek near, then cut on the frames exactly
    a = (k0 - 0.5) / float(fps) - ss
    b = (k1 - 0.5) / float(fps) - ss
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    kbps = int(max(WINDOW_MIN_KBPS * 1000, (target * 8 / dur - 256000) * 0.97) / 1000)
    for attempt in range(4):
        graph = f"[0:v:0]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v]"
        if has_audio:
            graph += f";[0:a:0]atrim=start={max(0.0, offset - ss):.6f}:duration={dur:.6f},asetpts=PTS-STARTPTS[a]"
        cmd = [ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{ss:.6f}", "-i", str(src), "-filter_complex", graph,
               "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "256k"] if has_audio else []) + [
               "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
               "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart", str(tmp)]
        subprocess.run(cmd, check=True)
        size = tmp.stat().st_size
        if size <= limit:
            break
        kbps = int(kbps * 0.85 * limit / size)
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"could not make a window of {src.name} smaller than {limit} bytes")
    tmp.replace(dst)
    got = probe(dst)
    v1 = next(s for s in got["streams"] if s.get("codec_type") == "video")
    if (v0["width"], v0["height"], v0["r_frame_rate"]) != (v1["width"], v1["height"], v1["r_frame_rate"]):
        raise RuntimeError(f"the window copy changed the size or frame rate: {v0} -> {v1}")
    return {"bytes": dst.stat().st_size, "reencoded": True, "window": True, "offset": round(offset, 6),
            "end": round(offset + dur, 6), "frames": k1 - k0, "video_kbps_cap": kbps, "width": v1["width"],
            "height": v1["height"], "fps": v1["r_frame_rate"], "seconds": dur}


def shifted_answer(path: Path, shift: float, out_dir: Path) -> Path:
    """answer_edit.json with every RAW time moved by ``shift`` seconds (a case whose raw.mp4 is a window of the
    full-size RAW: its key is timed on the window; the full-size run needs it on the whole RAW), written to out_dir."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    for it in d.get("audio") or []:
        if it.get("kind", "raw") == "raw" and it.get("src_in") is not None:
            it["src_in"] = round(float(it["src_in"]) + float(shift), 6)
    out_dir.mkdir(parents=True, exist_ok=True)
    q = out_dir / f"answer_edit_shift{shift:+.3f}.json"
    q.write_text(json.dumps(d, indent=1), encoding="utf-8", newline="\n")
    return q
