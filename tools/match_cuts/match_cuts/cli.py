"""Command line: ``python -m match_cuts --competitor X --raw Y --out Z [--layout match|fill|source]
[--comp-size WxH|competitor] [--fps competitor|source] [--work DIR] [--workers N] [--force-conform]
[--ae-time-mode auto|stretch|remap|frames] [--audio-sync raw|competitor] [-v]`` (DESIGN.md §5 cli.py, prompt Configuration).

Input auto-detection (prompt Configuration): the competitor is the portrait file, failing that the
shorter one. Explicit arguments that look reversed are swapped with a warning; if the defaults do not
exist, ``./input`` (or --input-dir) is scanned for exactly two videos. Genuinely ambiguous inputs
(same orientation, same duration) stop with a question instead of guessing.

Prints the overall headline ('PASS', 'PASS (criterion 6 not verified: <reason>)' or 'FAIL'), one line
per acceptance criterion c1..c6 (+ determinism and deliverables), the output paths and the warnings.
Exit code (DESIGN §7 D5): 0 = every criterion passed (or passed with explained exceptions) and no check
failed; 1 = something failed; 2 = run error; 3 = nothing failed but a criterion could not be verified
(not_available, e.g. criterion 6 without Node.js / After Effects).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from fractions import Fraction
from pathlib import Path
from typing import Sequence

from . import __version__
from .common import ffprobe_bin
from . import run_folders
from .config import Config

DEFAULTS = {  # the prompt's Configuration block
    "competitor": "./input/competitor.mp4",
    "raw": "./input/raw.mp4",
    "out": "./output",
    "work": "./work",
    "layout": "match",
    "comp_size": "competitor",
    "fps": "competitor",
}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".flv", ".wmv"}
CRITERIA_LABELS = [
    ("c1_coverage", "c1 coverage"),
    ("c2_cuts", "c2 frame-exact cuts"),
    ("c3_source_frames", "c3 frame-exact source frames"),
    ("c4_speed_framing", "c4 speed / framing"),
    ("c5_audio", "c5 audio"),
    ("c6_after_effects", "c6 After Effects"),
]
STATUS_TEXT = {"pass": "PASS", "fail": "FAIL", "pass_with_exceptions": "PASS*", "not_available": "N/A"}


class InputError(ValueError):
    """Inputs are missing or genuinely ambiguous (the user has to decide)."""


def _comp_size(value: str) -> str:
    v = value.strip()
    if v.lower() == "competitor":
        return "competitor"
    m = re.fullmatch(r"(\d+)[xX](\d+)", v)
    if not m or not (4 <= int(m.group(1)) <= 30000 and 4 <= int(m.group(2)) <= 30000):
        raise argparse.ArgumentTypeError(f"--comp-size must be WxH (e.g. 1080x1920) or 'competitor', got {value!r}")
    return f"{int(m.group(1))}x{int(m.group(2))}"


def _workers(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--workers must be an integer, got {value!r}") from None
    if n < 0:
        raise argparse.ArgumentTypeError("--workers must be >= 0 (0 = all CPUs)")
    return n


def _min_move(value: str) -> float:
    try:
        v = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--min-move must be a number of pixels, got {value!r}") from None
    if v < 0 or v != v:
        raise argparse.ArgumentTypeError("--min-move must be >= 0")
    return v


def _seconds_arg(name: str):
    def parse(value: str) -> float:
        try:
            v = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be a number of seconds, got {value!r}") from None
        if v < 0 or v != v:
            raise argparse.ArgumentTypeError(f"{name} must be >= 0")
        return v
    return parse


def _db_arg(value: str) -> float:
    try:
        v = float(str(value).lower().removesuffix("db"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--silence-db must be a number of dB, got {value!r}") from None
    if v > 0 or v != v:
        raise argparse.ArgumentTypeError("--silence-db must be <= 0 (dB below the speech level)")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="match_cuts",
        description="Rebuild a competitor's short-form edit frame-exactly from its RAW source and export an "
                    "After Effects project (build_ae_project.jsx), cutlist.json/csv, FCP7 XML, EDL, a preview "
                    "render, compare.mp4 and a verification report.",
        epilog="Exit code: 0 = every acceptance criterion passed (or passed with explained exceptions) and "
               "every deliverable was produced, 1 = something failed, 2 = run error, 3 = nothing failed but a "
               "criterion could not be verified here (e.g. criterion 6 without Node.js / After Effects). "
               "Restyle the captions of a saved Premiere project: python -m match_cuts restyle PROJECT.prproj. "
               "See tools/match_cuts/README.md.")
    p.add_argument("--competitor", default=None, metavar="X",
                   help=f"the finished edit (default {DEFAULTS['competitor']}; auto-detected in --input-dir)")
    p.add_argument("--raw", default=None, metavar="Y",
                   help=f"the RAW source video (default {DEFAULTS['raw']}; auto-detected in --input-dir)")
    p.add_argument("--out", default=DEFAULTS["out"], metavar="Z",
                   help=f"output folder (default {DEFAULTS['out']}): each run gets its own numbered folder in it (001, "
                        "002, ...) with 1_edit.xml, 2_captions.srt and everything else in extras/")
    p.add_argument("--layout", default=DEFAULTS["layout"], choices=["match", "fill", "source"],
                   help="match = recreate the competitor layout (box, corners, background, per-shot framing); "
                        "fill = full-screen 9:16 keeping the per-shot framing; source = cuts only at RAW size "
                        "(default match)")
    p.add_argument("--comp-size", default=DEFAULTS["comp_size"], type=_comp_size, metavar="WxH|competitor",
                   help="AE comp size: 'competitor' (same pixels as the competitor) or e.g. 1080x1920 "
                        "(match layout: same aspect only)")
    p.add_argument("--fps", default=DEFAULTS["fps"], choices=["competitor", "source"],
                   help="AE comp frame rate: competitor (exact cut timing) or source (cuts rounded to the "
                        "nearest RAW frame; max error reported)")
    p.add_argument("--work", default=DEFAULTS["work"], metavar="DIR",
                   help=f"cache / intermediate folder (default {DEFAULTS['work']})")
    p.add_argument("--workers", default=0, type=_workers, metavar="N", help="worker processes (0 = all CPUs)")
    p.add_argument("--force-conform", action="store_true",
                   help="transcode RAW to an AE-safe copy even when it is already AE-safe")
    p.add_argument("--ae-time-mode", default="auto", choices=["auto", "stretch", "remap", "frames"],
                   help="how AE layers are timed: auto (stretch, per-layer frame-exact fallback), stretch, "
                        "remap (time remapping), frames (per-frame HOLD remap keys; immune to AE time rounding)")
    p.add_argument("--audio-sync", default="raw", choices=["raw", "competitor"],
                   help="export audio: raw = keep RAW's own lip-sync (audio-only layers only for genuine J/L cuts; "
                        "default), competitor = reproduce the competitor's measured A/V offset sample-accurately "
                        "(every segment's audio on an audio-only layer with shifted source time)")
    p.add_argument("--input-dir", default="./input", metavar="DIR",
                   help="folder scanned for the two videos when --competitor/--raw are not given and the "
                        "default names do not exist (default ./input)")
    p.add_argument("--seed", default=None, type=int, help="random seed (RANSAC / FLANN); default from config")
    p.add_argument("--premiere", action="store_true",
                   help="Premiere Pro only: no After Effects export or checks; 1_edit.xml is a 1080x1920 sequence "
                        "at exactly 60.00 fps (every competitor frame = 2 frames), RAW audio on A1, V2+ empty. Every clip "
                        "holds ONE fixed Position / Scale (no keyframes, rotation 0): the competitor's framing that still "
                        "covers the template window x 42-1039, y 555-1591. B-roll follows the audio: every NOT-IN-RAW / "
                        "B-roll / uncertain spot shows the RAW video of the audio playing there, else the previous RAW "
                        "clip keeps playing; a marker on each replaced spot. Competitor captions: their on-screen timing, "
                        "the spoken words. The framing changes only where the competitor's moves --min-move px or more")
    p.add_argument("--keep-silence", action="store_true",
                   help="keep the silences of my edit (default: cut out every silence of the RAW audio under my clips, "
                        "after the competitor's cuts are recreated; without --competitor the RAW alone is cut this way)")
    p.add_argument("--silence-db", type=_db_arg, default=None, metavar="DB",
                   help="silence = the short-window loudness (50 ms RMS) this many dB below the edit's speech level "
                        "(the loudness of its loudest 5%% of windows); default: set for each video from its speech "
                        "level and its background noise")
    p.add_argument("--min-silence", type=_seconds_arg("--min-silence"), default=0.3, metavar="S",
                   help="cut only silences longer than this, in the gaps between words (seconds, default 0.3)")
    p.add_argument("--pad-before", type=_seconds_arg("--pad-before"), default=0.05, metavar="S",
                   help="a clip starts this long before its first word, and a removed silence keeps this much before "
                        "the word that follows it (seconds, default 0.05)")
    p.add_argument("--pad-after", type=_seconds_arg("--pad-after"), default=0.15, metavar="S",
                   help="a clip ends this long after its last word has finished, and a removed silence keeps this much "
                        "after the word before it (seconds, default 0.15)")
    p.add_argument("--allow-repeats", action="store_true",
                   help="--premiere: keep a RAW moment over 0.5 s that plays twice in my edit (default: the copy out of "
                        "chronological order, else the later one, is cut out; a stutter at a cut is trimmed either way)")
    p.add_argument("--min-move", type=_min_move, default=250.0, metavar="PX",
                   help="--premiere: change a clip's framing only when the competitor's framing moves this many px or "
                        "more in the 1080x1920 sequence (the biggest movement of the picture's centre or edges, so zooms "
                        "count); below it the clip keeps the previous clip's framing exactly, and neighbouring pieces of "
                        "one continuous RAW take that end up with the same framing become one clip (default 250; 0 = "
                        "every clip its own framing)")
    p.add_argument("--no-broll", action="store_true",
                   help="where the competitor cuts away (B-roll from the RAW or not in it) while the RAW audio keeps "
                        "playing, the export shows the RAW video that matches the audio instead (the main clip plays "
                        "through); cutaways over music / voice-over stay as they are. Changes 1_edit.xml, the "
                        "EDL and cutlist.csv; report.md lists every replaced and kept cutaway")
    p.add_argument("--captions", default="auto", choices=["auto", "competitor", "voice"],
                   help="2_captions.srt (60 fps sequence): auto = the competitor's burned-in captions when it has "
                        "them (OCR: its timing and splits, my text rules), else made from the voice-over by "
                        "caption-generator-prompt.md; competitor / voice force one mode; both end with the prompt's "
                        "hard rules (acronyms: caption_allowlist.txt)")
    p.add_argument("--voiceover", default=None, metavar="FILE",
                   help="caption this narration (audio or video file, starting at the sequence start) instead of the "
                        "cut edit's audio")
    p.add_argument("--caption-model", default="small.en", metavar="NAME",
                   help="faster-whisper model for the transcription (default small.en; base.en is faster, medium.en "
                        "more accurate; downloaded once on first use)")
    p.add_argument("--caption-recheck-model", default="medium.en", metavar="NAME",
                   help="words the transcription is unsure of (low confidence, music / noise under them, cut at an "
                        "edit point) are transcribed again from the RAW with this bigger model, the whole sentence "
                        "around them (default medium.en, downloaded once on first use; none = off)")
    p.add_argument("--no-ae", action="store_true",
                   help="do not open After Effects automatically (run output/build_ae_project.jsx yourself)")
    p.add_argument("--ae-timeout", default=600.0, type=float, metavar="SECONDS",
                   help="how long to wait for After Effects to save recreated_edit.aep (default 600)")
    p.add_argument("--skip-preview", action="store_true", help="do not render preview_recreation.mp4")
    p.add_argument("--skip-compare", action="store_true", help="do not render compare.mp4")
    p.add_argument("--no-swap", action="store_true",
                   help="never swap --competitor/--raw even if they look reversed")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging on the console")
    p.add_argument("--version", action="version", version=f"match_cuts {__version__}")
    return p


def config_from_args(args: argparse.Namespace, competitor: str | None = None, raw: str | None = None) -> Config:
    """Config for pipeline.run from parsed arguments (resolved input paths override the arguments)."""
    cfg = Config()
    cfg.competitor = str(competitor or args.competitor or DEFAULTS["competitor"])
    cfg.raw = str(raw or args.raw or DEFAULTS["raw"])
    cfg.out_dir = str(args.out)
    cfg.work_dir = str(args.work)
    cfg.layout_mode = args.layout
    cfg.comp_size = args.comp_size
    cfg.fps_mode = args.fps
    cfg.workers = int(args.workers)
    cfg.force_conform = bool(args.force_conform)
    cfg.ae_time_mode = args.ae_time_mode
    cfg.audio_sync = str(getattr(args, "audio_sync", "raw") or "raw")
    cfg.verbose = bool(args.verbose)
    cfg.skip_preview = bool(args.skip_preview)
    cfg.run_ae = not bool(getattr(args, "no_ae", False))
    cfg.premiere = bool(getattr(args, "premiere", False))
    cfg.premiere_min_move = float(getattr(args, "min_move", 250.0))
    cfg.keep_silence = bool(getattr(args, "keep_silence", False))
    db = getattr(args, "silence_db", None)
    cfg.silence_db = None if db is None else float(db)
    cfg.min_silence = float(getattr(args, "min_silence", 0.3))
    cfg.pad_before = float(getattr(args, "pad_before", 0.05))
    cfg.allow_repeats = bool(getattr(args, "allow_repeats", False))
    cfg.pad_after = float(getattr(args, "pad_after", 0.15))
    cfg.ae_timeout_s = float(getattr(args, "ae_timeout", 600.0))
    cfg.skip_compare = bool(args.skip_compare)
    cfg.no_broll = bool(getattr(args, "no_broll", False))
    cfg.captions = str(getattr(args, "captions", "auto") or "auto")
    cfg.voiceover = str(getattr(args, "voiceover", None) or "")
    cfg.caption_model = str(getattr(args, "caption_model", None) or "small.en")
    cfg.caption_recheck_model = str(getattr(args, "caption_recheck_model", None) or "medium.en")
    if args.seed is not None:
        cfg.seed = int(args.seed)
    return cfg


# ---------------------------------------------------------------------------------------------
# Input auto-detection
# ---------------------------------------------------------------------------------------------

def quick_probe(path: str | Path) -> dict:
    """Display size (rotation + SAR applied) and duration of a video via one ffprobe call (no decode)."""
    cmd = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,sample_aspect_ratio,duration:stream_side_data=rotation:stream_tags=rotate:format=duration",
           "-of", "json", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise InputError(f"cannot read {path}: {res.stderr.strip()[-300:]}")
    d = json.loads(res.stdout or "{}")
    streams = d.get("streams") or []
    if not streams:
        raise InputError(f"{path} has no video stream")
    s = streams[0]
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    sar = s.get("sample_aspect_ratio") or "1:1"
    try:
        n, dd = (int(x) for x in sar.split(":"))
        sar_f = Fraction(n, dd) if n > 0 and dd > 0 else Fraction(1)
    except ValueError:
        sar_f = Fraction(1)
    rot = 0
    for sd in s.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if not rot and (s.get("tags") or {}).get("rotate"):
        rot = int(float(s["tags"]["rotate"]))
    dw, dh = int(round(w * float(sar_f))), h
    if abs(rot) % 180 == 90:
        dw, dh = dh, dw
    dur = s.get("duration") or (d.get("format") or {}).get("duration") or 0.0
    return {"path": str(path), "width": dw, "height": dh, "rotation": rot, "duration": float(dur)}


def _is_portrait(p: dict) -> bool:
    return p["height"] > p["width"]


def order_pair(a: dict, b: dict, dur_tol: float = 0.01) -> tuple[dict, dict, str] | None:
    """(competitor, raw, reason) by the prompt rule (portrait one, else shorter); None if ambiguous."""
    pa, pb = _is_portrait(a), _is_portrait(b)
    if pa != pb:
        return (a, b, "portrait") if pa else (b, a, "portrait")
    da, db = a["duration"], b["duration"]
    if abs(da - db) <= dur_tol * max(da, db, 1e-9):
        return None
    return (a, b, "shorter") if da < db else (b, a, "shorter")


def resolve_inputs(competitor: str | None, raw: str | None, input_dir: str | Path = "./input",
                   no_swap: bool = False) -> tuple[str, str, list[str]]:
    """Resolve (competitor, raw, notes). Explicit/default paths that look reversed are swapped (with a
    note); missing defaults are auto-detected among the videos of ``input_dir``."""
    notes: list[str] = []
    c = competitor or DEFAULTS["competitor"]
    r = raw or DEFAULTS["raw"]
    if Path(c).is_file() and Path(r).is_file():
        if no_swap:
            return c, r, notes
        pc, pr = quick_probe(c), quick_probe(r)
        order = order_pair(pc, pr)
        if order is not None and order[0]["path"] == pr["path"]:
            notes.append(f"inputs look reversed ({order[2]} rule: {Path(r).name} "
                         f"{pr['width']}x{pr['height']} {pr['duration']:.1f}s vs {Path(c).name} "
                         f"{pc['width']}x{pc['height']} {pc['duration']:.1f}s) -> swapped: competitor={r}, raw={c}")
            return r, c, notes
        return c, r, notes
    if competitor and not Path(competitor).is_file():
        raise InputError(f"competitor file not found: {competitor}")
    if raw and not Path(raw).is_file():
        raise InputError(f"raw file not found: {raw}")
    d = Path(input_dir)
    vids = sorted(p for p in d.glob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS) if d.is_dir() else []
    known = [p for p in (competitor, raw) if p]
    if known:
        vids = [v for v in vids if v.resolve() != Path(known[0]).resolve()]
        if len(vids) != 1:
            raise InputError(f"{'--competitor' if competitor else '--raw'} given but the other file cannot be "
                             f"determined ({len(vids)} other videos in {d}); pass both --competitor and --raw")
        other = str(vids[0])
        c, r = (competitor, other) if competitor else (other, raw)
        notes.append(f"auto-detected {'raw' if competitor else 'competitor'}: {other}")
        c2, r2, more = resolve_inputs(c, r, input_dir, no_swap)
        return c2, r2, notes + more
    if len(vids) != 2:
        raise InputError(f"expected {DEFAULTS['competitor']} and {DEFAULTS['raw']}, or exactly two videos in {d} "
                         f"(found {len(vids)}: {[v.name for v in vids]}); pass --competitor and --raw")
    a, b = quick_probe(vids[0]), quick_probe(vids[1])
    order = order_pair(a, b)
    if order is None:
        raise InputError(f"cannot tell competitor from raw: {vids[0].name} and {vids[1].name} have the same "
                         "orientation and duration; pass --competitor and --raw")
    notes.append(f"auto-detected by the {order[2]} rule: competitor={order[0]['path']}, raw={order[1]['path']}")
    return order[0]["path"], order[1]["path"], notes


# ---------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------

def headline(result: dict) -> str:
    """Overall verdict (DESIGN §7 D5): 'PASS', 'PASS (criterion 6 not verified: <reason>)' or 'FAIL' --
    derived from the exit code, so the headline and the exit status never disagree."""
    code = result.get("exit_code", 1)
    if result.get("headline"):
        return str(result["headline"])
    try:
        from . import pipeline
        return pipeline.headline_for(result.get("criteria") or {}, result.get("checks") or {}, code)
    except Exception:  # noqa: BLE001 - verify not importable: fall back to the exit code alone
        return {0: "PASS", 2: "ERROR", 3: "PASS (some criterion not verified)"}.get(int(code), "FAIL")


def format_summary(result: dict, out_dir: str | Path, max_warnings: int = 5, max_rows: int = 12) -> str:
    """The end-of-run summary: overall headline, pass/fail per criterion, the files to use (the run folder's
    1_edit.xml and 2_captions.srt; 3_captions_styled.prproj comes from the restyle command; everything else in
    extras/), what to check by hand (B-ROLL REPLACED spots, uncertain / NOT-IN-RAW spots, captions worth a look),
    the first warnings, and the run folder."""
    crit = result.get("criteria") or {}
    checks = result.get("checks") or {}
    lines = []
    lines.append(f"match_cuts result: {headline(result)}")
    for key, label in [] if result.get("raw_only") else CRITERIA_LABELS:
        c = crit.get(key) or {}
        st = STATUS_TEXT.get(c.get("status"), (c.get("status") or "not run").upper())
        lines.append(f"  {label:<30} {st:<6} {c.get('summary', '')}")
    for key, label in (("s9_7_determinism", "9.7 determinism"), ("s9_8_deliverables", "9.8 deliverables")):
        chk = checks.get(key) or {}
        if chk:
            st = STATUS_TEXT.get(chk.get("status"), str(chk.get("status") or "not run").upper())
            lines.append(f"  {label:<30} {st:<6} {chk.get('summary', '')}")
    if result.get("raw_only"):
        ex = getattr(result.get("context"), "exports", None) or {}
        x = ex.get("xml") or {}
        lines.append(f"  {'1_edit.xml check':<30} {'PASS' if ex.get('ok') else 'FAIL':<6} {x.get('clips', 0)} clips, "
                     f"every clip covers the window with one fixed framing, {x.get('framing_changes', 0)} framing "
                     "changes" + ("" if ex.get("ok") else f"; {'; '.join((ex.get('errors') or [])[:3])}"))
    else:
        lines.append("  (PASS* = passed with listed, explained exceptions; N/A = could not be verified on this machine)")
    run = Path(str(result.get("run_dir") or out_dir))
    paths = result.get("paths") or {}
    ctx = result.get("context")
    premiere = bool(getattr(getattr(ctx, "cfg", None), "premiere", False))
    lines.append("Files:")
    lines.append(f"  {run_folders.EDIT_XML:<26} {paths.get('xml') or 'not written'}")
    lines.append(f"  {run_folders.CAPTIONS_SRT:<26} {paths.get('captions') or 'not written'}")
    if premiere:
        lines.append(f"  {run_folders.STYLED_PRPROJ:<26} import both into Premiere, upgrade the captions to graphics, "
                     'save, then: python -m match_cuts restyle "<project>.prproj"')
    if paths.get("jsx"):
        lines.append(f"  {'build_ae_project.jsx':<26} {paths['jsx']}")
    extras = Path(paths["report"]).parent if paths.get("report") else run / run_folders.EXTRAS
    lines.append(f"  {run_folders.EXTRAS:<26} {extras} (report.md, cutlist, EDL, verify.json, preview, compare, debug, "
                 "media, log)")
    hc = result.get("checklist")
    if hc is not None:
        lines.append("Check by hand:")
        for key, title in (("broll", "B-ROLL REPLACED spots"), ("spots", "Uncertain / NOT-IN-RAW / retimed spots"),
                           ("captions", "Captions worth a look")):
            rows = list(hc.get(key) or [])
            lines.append(f"  {title}: {len(rows) if rows else 'none'}")
            lines += [f"    {r}" for r in rows[:max_rows]]
            if len(rows) > max_rows:
                lines.append(f"    ... {len(rows) - max_rows} more in {run_folders.EXTRAS}/report.md")
        if hc.get("other_video"):
            rows = list(hc["other_video"])
            lines.append(f"  Other video (not in RAW), left empty on purpose: {len(rows)}")
            lines += [f"    {r}" for r in rows[:max_rows]]
        if "audio" in hc:
            rows = list(hc.get("audio") or [])
            lines.append(f"  V1 clips without their audio on A1 (on purpose): {len(rows) if rows else 'none'}")
            lines += [f"    {r}" for r in rows[:max_rows]]
        if hc.get("links"):
            rows = list(hc["links"])
            lines.append(f"  Linked clips: {rows[0]}")
            lines += [f"    {r}" for r in rows[1:max_rows + 1]]
        if hc.get("people"):
            rows = list(hc["people"])
            lines.append(f"  The person speaking in the picture: {rows[0]}")
            shown = [r for r in rows[1:] if not r.startswith("not checked")]
            lines += [f"    {r}" for r in shown]                       # every re-framed clip, with its time
            rest = len(rows) - 1 - len(shown)
            if rest:
                lines.append(f"    ... {rest} clip(s) not checked (nobody in the picture / another video): "
                             f"{run_folders.EXTRAS}/report.md")
        for r in hc.get("caption_recheck") or []:
            lines.append(f"  Unclear caption words: {r}")
        if "caption_stutters" in hc:
            rows = list(hc.get("caption_stutters") or [])
            lines.append(f"  Caption stutters kept once: {len(rows) if rows else 'none'}")
            lines += [f"    {r}" for r in rows[:max_rows]]
        if "caption_timing" in hc:
            rows = list(hc.get("caption_timing") or [])
            off = [r for r in rows if "frames late" in r or "frames early" in r]
            unheard = [r for r in rows if "not heard" in r]
            lines.append(f"  Captions off their first word: {len(off) if off else 'none'}"
                         + ("" if off or unheard else f" ({rows[0]})" if rows else ""))
            lines += [f"    {r}" for r in off[:max_rows]]
            if unheard:
                lines.append(f"  Captions whose words my edit does not play: {len(unheard)}")
                lines += [f"    {r}" for r in unheard[:max_rows]]
        for r in hc.get("caption_rules") or []:
            lines.append(f"  Caption rules (captions changed / flagged per rule): {r}")
        talk = list(hc.get("speech") or [])
        if talk:
            lines.append(f"Cuts moved off speech: {talk[0]}")
            lines += [f"  {r}" for r in talk[1:]]
        sil = list(hc.get("silence") or [])
        if sil:
            lines.append(f"Silences: {sil[0]}")
            lines += [f"  {r}" for r in sil[1:]]
        rep = list(hc.get("repeats") or [])
        if rep:
            lines.append(f"Repeats: {rep[0]}")
            lines += [f"  {r}" for r in rep[1:]]
    warns = list(result.get("warnings") or [])
    if warns:
        lines.append(f"Warnings: {len(warns)}" + (f" (the first {max_warnings}; all in {run_folders.EXTRAS}/report.md)"
                                                  if len(warns) > max_warnings else ""))
        for w in warns[:max_warnings]:
            lines.append(f"  - {w}")
    lines.append(f"Run folder: {run}")
    return "\n".join(lines)


def _tolerant_console() -> None:
    """Console text the terminal cannot encode (cp1252 / cp437 on Windows when output is redirected) is
    replaced instead of raising UnicodeEncodeError; files are always written as UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _tolerant_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["restyle"]:                  # python -m match_cuts restyle PROJECT.prproj
        from .restyle import main as restyle_main
        return restyle_main(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_only = args.competitor is None and args.raw is not None        # no competitor: the edit from the RAW alone
    try:
        if raw_only:
            if not Path(args.raw).is_file():
                raise InputError(f"raw file not found: {args.raw}")
            comp, raw, notes = "", str(args.raw), []
        else:
            comp, raw, notes = resolve_inputs(args.competitor, args.raw, args.input_dir, args.no_swap)
    except InputError as e:
        print(f"match_cuts: {e}", file=sys.stderr)
        return 2
    if args.voiceover and not Path(args.voiceover).is_file():
        print(f"match_cuts: voice-over file not found: {args.voiceover}", file=sys.stderr)
        return 2
    for n in notes:
        print(f"match_cuts: WARNING: {n}", file=sys.stderr)
    cfg = config_from_args(args, comp, raw)
    if raw_only:
        cfg.competitor, cfg.premiere = "", True          # the RAW-only edit is the Premiere sequence
    # each run its own numbered folder in --out: 1_edit.xml / 2_captions.srt there, everything else in its extras/
    base = Path(cfg.out_dir)
    prev = run_folders.newest_run_dir(base)
    run_dir = run_folders.new_run_dir(base)
    cfg.deliver_dir, cfg.out_dir = str(run_dir), str(run_dir / run_folders.EXTRAS)
    if prev is not None and (prev / run_folders.EXTRAS / "cutlist.json").is_file():
        cfg.previous_out_dir = str(prev / run_folders.EXTRAS)          # s9_7: compared with the previous run
    from . import pipeline
    try:
        if raw_only:
            from .raw_only import run_raw_only
            result = run_raw_only(cfg)
        else:
            result = pipeline.run(cfg)
    except KeyboardInterrupt:
        print("match_cuts: interrupted", file=sys.stderr)
        print(f"Run folder: {run_dir}", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 - reported to the user with the log location
        print(f"match_cuts: ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        if cfg.verbose:
            traceback.print_exc()
        print(f"match_cuts: details in {Path(cfg.out_dir) / 'match_cuts.log'}", file=sys.stderr)
        print(f"Run folder: {run_dir}", file=sys.stderr)
        return 2
    if notes:
        result.setdefault("warnings", [])
        result["warnings"] = notes + [w for w in result["warnings"] if w not in notes]
    result.setdefault("run_dir", str(run_dir))
    print(format_summary(result, run_dir))
    return int(result.get("exit_code", 1))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
