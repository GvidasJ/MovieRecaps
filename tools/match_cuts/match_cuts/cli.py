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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="match_cuts",
        description="Rebuild a competitor's short-form edit frame-exactly from its RAW source and export an "
                    "After Effects project (build_ae_project.jsx), cutlist.json/csv, FCP7 XML, EDL, a preview "
                    "render, compare.mp4 and a verification report.",
        epilog="Exit code: 0 = every acceptance criterion passed (or passed with explained exceptions) and "
               "every deliverable was produced, 1 = something failed, 2 = run error, 3 = nothing failed but a "
               "criterion could not be verified here (e.g. criterion 6 without Node.js / After Effects). "
               "See tools/match_cuts/README.md.")
    p.add_argument("--competitor", default=None, metavar="X",
                   help=f"the finished edit (default {DEFAULTS['competitor']}; auto-detected in --input-dir)")
    p.add_argument("--raw", default=None, metavar="Y",
                   help=f"the RAW source video (default {DEFAULTS['raw']}; auto-detected in --input-dir)")
    p.add_argument("--out", default=DEFAULTS["out"], metavar="Z", help=f"output folder (default {DEFAULTS['out']})")
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
                   help="Premiere Pro only: no After Effects export or checks; recreated_edit.xml is a 1080x1920 sequence "
                        "at exactly 60.00 fps (every competitor frame = 2 frames), each clip framed into the template "
                        "window x 42-1039, y 555-1591, RAW audio on A1, markers on UNCERTAIN / NOT-IN-RAW spots, V2+ empty")
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
    cfg.ae_timeout_s = float(getattr(args, "ae_timeout", 600.0))
    cfg.skip_compare = bool(args.skip_compare)
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


def format_summary(result: dict, out_dir: str | Path, max_warnings: int = 20) -> str:
    """The final chat summary: overall headline, pass/fail per criterion, output paths, warnings."""
    crit = result.get("criteria") or {}
    checks = result.get("checks") or {}
    lines = []
    lines.append(f"match_cuts result: {headline(result)}")
    for key, label in CRITERIA_LABELS:
        c = crit.get(key) or {}
        st = STATUS_TEXT.get(c.get("status"), (c.get("status") or "not run").upper())
        lines.append(f"  {label:<30} {st:<6} {c.get('summary', '')}")
    for key, label in (("s9_7_determinism", "9.7 determinism"), ("s9_8_deliverables", "9.8 deliverables")):
        chk = checks.get(key) or {}
        if chk:
            st = STATUS_TEXT.get(chk.get("status"), str(chk.get("status") or "not run").upper())
            lines.append(f"  {label:<30} {st:<6} {chk.get('summary', '')}")
    lines.append("  (PASS* = passed with listed, explained exceptions; N/A = could not be verified on this machine)")
    paths = result.get("paths") or {}
    if paths:
        lines.append(f"Outputs ({out_dir}):")
        for k, p in paths.items():
            lines.append(f"  {k:<10} {p}")
    warns = list(result.get("warnings") or [])
    if warns:
        lines.append(f"Warnings ({len(warns)}):")
        for w in warns[:max_warnings]:
            lines.append(f"  - {w}")
        if len(warns) > max_warnings:
            lines.append(f"  ... {len(warns) - max_warnings} more in report.md")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        comp, raw, notes = resolve_inputs(args.competitor, args.raw, args.input_dir, args.no_swap)
    except InputError as e:
        print(f"match_cuts: {e}", file=sys.stderr)
        return 2
    for n in notes:
        print(f"match_cuts: WARNING: {n}", file=sys.stderr)
    cfg = config_from_args(args, comp, raw)
    from . import pipeline
    try:
        result = pipeline.run(cfg)
    except KeyboardInterrupt:
        print("match_cuts: interrupted", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 - reported to the user with the log location
        print(f"match_cuts: ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        if cfg.verbose:
            traceback.print_exc()
        print(f"match_cuts: details in {Path(cfg.work_dir) / 'match_cuts.log'}", file=sys.stderr)
        return 2
    if notes:
        result.setdefault("warnings", [])
        result["warnings"] = notes + [w for w in result["warnings"] if w not in notes]
    print(format_summary(result, cfg.out_dir))
    return int(result.get("exit_code", 1))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
