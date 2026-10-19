"""Just cuts (day 12, 6b): a YouTube video (or any RAW) with no competitor, as a Premiere edit of the WHOLE video cut
at every detected scene change -- ``python -m match_cuts.just_cuts --raw RAW --run-dir DIR [--work W] [--frame PNG]
[--watermark PNG]``.

It is the RAW-only run (raw_only.py) without its silence removal and captions: the RAW laid out as an edit of itself
on the 60 fps planning grid, one segment per shot of the RAW (shots.py: the shot changes of the whole video), each
framed to cover the template window with the main face at its centre (faces.py), then written by the usual Premiere
export -- so the sequence comes out at the RAW's own rate (night 7's last step), every A1 clip carries your audio
chain, the frame PNG (the channel's template, else templates\\default.png) sits on V2 and the channel's watermark
above it, and the export's own checks run. No mini cuts: a shot change closer than MIN_SHOT_S to the one before (a
flash, a fast montage) is not cut on -- the shot plays on. No captions, no competitor, no WRONG PAIR check.

The run folder gets 1_edit.xml and extras\\ (report.md, cutlist.json, just_cuts.json: the cuts, the clips, the
problems the export check found). Exit 0: 1_edit.xml made and checked; 1: made, a check found something to look at;
2: not made (the reason on stderr after "match_cuts:"). New code lives here, not in model.py / common.py (their edit
would re-run every cached stage of every video)."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import traceback
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .common import Cache, DecisionLog, fps_str, log, setup_logging
from .model import Cutlist, Segment
from .raw_only import SEQ_FPS, framing, window_box

MIN_SHOT_S = 0.5          # no cut that leaves a shot shorter than this ("no mini cuts of a few frames")
FACE_FRAMES = 3           # frames per shot the main face is looked for on
JSON = "just_cuts.json"


def keep_changes(changes_s: Sequence[float], duration_s: float, min_shot_s: float = MIN_SHOT_S) -> list[float]:
    """The shot changes cut on: in order, each at least ``min_shot_s`` after the one kept before it (and after 0),
    and the last at least that long before the end -- a flash or a burst of fast cuts plays on in one piece."""
    out: list[float] = []
    last = 0.0
    for t in sorted(float(c) for c in changes_s or []):
        if t - last < min_shot_s or duration_s - t < min_shot_s:
            continue
        out.append(t)
        last = t
    return out


def shot_bounds(changes_s: Sequence[float], n_frames: int, fps: Fraction = SEQ_FPS) -> list[tuple[int, int]]:
    """[a, b) frame ranges on the ``fps`` grid, one per shot, tiling [0, n_frames)."""
    f = float(fps)
    cuts = sorted({min(n_frames, max(0, int(round(t * f)))) for t in changes_s})
    edges = [0] + [c for c in cuts if 0 < c < n_frames] + [n_frames]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def build_cutlist(raw_block: dict, n_frames: int, bounds: Sequence[tuple[int, int]],
                  win: tuple[float, float, float, float], seq_wh: tuple[int, int], face_x: Any = None
                  ) -> tuple[Cutlist, list[dict]]:
    """(the RAW as an edit of itself, one segment per shot; its framing notes). ``face_x(t0, t1, view)`` -> the RAW
    x of the main face in that shot (or None: centred); ``view`` keeps the same person while they stay in view."""
    raw_wh = (float(raw_block["width"]), float(raw_block["height"]))
    f = float(SEQ_FPS)
    segs: list[Segment] = []
    notes: list[dict] = []
    last = None
    for a, b in bounds:
        view = None if last is None else ((win[0] - last.tx) / last.s, (win[0] + win[2] - last.tx) / last.s)
        fx = face_x(a / f, b / f, view) if face_x is not None else None
        sim = framing(raw_wh, win, fx)
        last = sim
        t = a / f
        notes.append({"from_s": round(a / f, 3), "to_s": round(b / f, 3),
                      "face_x": None if fx is None else round(float(fx), 1)})
        segs.append(Segment(id=len(segs) + 1, type="raw", comp_in=a, comp_out=b, raw_in_seconds=t, speed=1.0,
                            raw_in_interval=[t - 0.25 / f, t + 0.25 / f], transform=sim.to_dict(), confidence=1.0,
                            label=f"shot {len(segs) + 1}"))
    comp = {"file": raw_block.get("file", ""), "width": int(seq_wh[0]), "height": int(seq_wh[1]),
            "fps": fps_str(SEQ_FPS), "frames": int(n_frames)}
    layout = {"mode": "match", "layout_kind": "boxed", "box": window_box(win), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000", "zones": [],
              "captions": []}
    return Cutlist(1, comp, dict(raw_block), layout, segs), notes


def run_just_cuts(cfg: Any, faces_on: bool = True) -> dict:
    """The Just cuts run (see the module docstring). Returns {ok, exit_code, cuts, clips, errors, warnings, ...}."""
    from . import conform, export_xml_edl, pipeline, probe
    from .run_folders import EDIT_XML
    pipeline._prepare_dirs(cfg)                                        # noqa: SLF001 - the package's own
    setup_logging(getattr(cfg, "verbose", False), log_file=cfg.out / "match_cuts.log")
    log.info("match_cuts Just cuts: raw=%s out=%s work=%s", cfg.raw, cfg.out_dir, cfg.work_dir)
    ctx = pipeline.Context(cfg=cfg, dlog=DecisionLog(cfg.work / "decisions.jsonl", truncate=True),
                           cache=Cache(cfg.work))
    ctx.input_stats = {cfg.raw: pipeline._input_stat(cfg.raw)}        # noqa: SLF001
    t_all = time.perf_counter()
    ctx.frame = pipeline.prepare_frame(ctx)
    pipeline.prepare_watermark(ctx)
    with pipeline._stage(ctx, "J1 probe+media"):                       # noqa: SLF001
        ctx.raw_input = probe.probe(cfg.raw, "raw", str(cfg.work), decode=True)
        ctx.raw_conform = pipeline.self_cached_stage(
            ctx, "conform_raw", pipeline._conform_key(ctx, ctx.raw_input),                    # noqa: SLF001
            lambda: conform.conform(ctx.raw_input, "raw", cfg, ctx.dlog), ("conform",))
        ctx.raw_info = probe.probe(str(ctx.raw_conform.path), "raw", str(cfg.work), decode=True)
        if ctx.raw_info.nb_frames <= 0 or not ctx.raw_info.fps:
            raise RuntimeError(f"the RAW {Path(cfg.raw).name} has no picture that can be read")
    st = export_xml_edl.premiere_settings(cfg)
    win, seq_wh = st["window"], st["size"]
    raw_fps = Fraction(ctx.raw_info.fps)
    cfg.premiere_seq_rate = fps_str(export_xml_edl.sequence_rate_for_raw(raw_fps))
    raw_block = pipeline.media_block(ctx.raw_info, ctx.raw_conform, ctx.raw_input)
    duration = ctx.raw_info.nb_frames / float(raw_fps)
    n_frames = int(duration * float(SEQ_FPS))
    with pipeline._stage(ctx, "J2 scene changes"):                     # noqa: SLF001
        found = pipeline.shots_of(ctx) or []
        kept = keep_changes(found, duration)
        ctx.shots = kept
        cfg.premiere_shots = kept                     # the export cuts V1 / A1 on these (Scene Edit Detection)
        log.info("Just cuts: %d scene change(s) found, %d cut on (none closer than %.2f s)", len(found), len(kept),
                 MIN_SHOT_S)
    with pipeline._stage(ctx, "J3 framing"):                           # noqa: SLF001
        video = str(raw_block.get("file_abs") or ctx.raw_info.path)
        face_x = None
        if faces_on:
            from . import faces

            def face_x(t0: float, t1: float, view):
                ts = [t0 + (t1 - t0) * (i + 0.5) / FACE_FRAMES for i in range(FACE_FRAMES)]
                return faces.main_face_x(video, float(raw_fps), ts, view)[0]
        bounds = shot_bounds(kept, n_frames)
        ctx.cutlist, framing_notes = build_cutlist(raw_block, n_frames, bounds, win, seq_wh, face_x)
    ctx.silence = {"off": "Just cuts: nothing is removed"}
    ctx.comp_info = dataclasses.replace(ctx.raw_info, role="competitor", fps=SEQ_FPS, nb_frames=n_frames,
                                        width=int(seq_wh[0]), height=int(seq_wh[1]),
                                        display_width=int(seq_wh[0]), display_height=int(seq_wh[1]))
    xml = cfg.deliver / EDIT_XML
    with pipeline._stage(ctx, "J4 Premiere XML"):                      # noqa: SLF001
        res = export_xml_edl.write_premiere_xml(ctx.cutlist, xml, cfg, None)
        ctx.paths["xml"] = str(xml)
        ctx.premiere_xml = res
        ctx.exports = export_xml_edl.validate_premiere_exports(ctx.cutlist, xml, None, cfg, None, None, kept)
        ctx.exports["clips"], ctx.exports["framing"] = res["clips"], framing_notes
    errors = [str(e) for e in (ctx.exports.get("errors") or [])]
    rate = Fraction(str(cfg.premiere_seq_rate or SEQ_FPS))
    if rate != SEQ_FPS and xml.exists():
        with pipeline._stage(ctx, "J5 the RAW's frame rate"):          # noqa: SLF001
            ctx.premiere_cutlist = ctx.cutlist
            fin = pipeline.apply_final(ctx, 1.0, rate)
            ctx.finish["speed"] = fin
            errors += [str(p) for p in (fin.get("problems") or [])[:10]]
    ctx.cutlist.save(cfg.out / "cutlist.json")
    ok = ctx.exports.get("ok") is True and not errors
    clips = int(res.get("clips") or 0)
    out = {"ok": ok, "exit_code": 0 if ok else 1, "found": len(found), "cuts": len(kept),
           "clips": clips, "audio_chain": len(res.get("audio_chain") or []), "fps": fps_str(rate),
           "errors": errors[:20], "warnings": list(ctx.warnings)[:20], "run_dir": str(cfg.deliver),
           "seconds": round(time.perf_counter() - t_all, 1)}
    (cfg.out / "report.md").write_text(report(out, cfg), encoding="utf-8")
    ctx.dlog.close()
    return out


def report(r: dict, cfg: Any) -> str:
    lines = [f"# Just cuts: {Path(cfg.raw).name}", "",
             f"- scene changes found: {r['found']}; cut on: {r['cuts']} (none closer than {MIN_SHOT_S:g} s)",
             f"- 1_edit.xml: {r['clips']} clips on a {r['fps']} fps sequence, the audio chain on {r['audio_chain']} "
             "audio clip(s)",
             f"- frame: {Path(str(getattr(cfg, 'frame_png', '') or '')).name or 'none'}; watermark: "
             f"{Path(str(getattr(cfg, 'watermark_png', '') or '')).name or 'none'}",
             f"- check: {'every check passed' if r['ok'] else 'something to look at'}", ""]
    lines += [f"- {e}" for e in r["errors"]]
    return "\n".join(lines).rstrip() + "\n"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m match_cuts.just_cuts",
                                description="The whole RAW cut at every scene change into a Premiere edit.")
    p.add_argument("--raw", required=True)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--work", default=None)
    p.add_argument("--frame", default=None)
    p.add_argument("--watermark", default=None)
    p.add_argument("--no-faces", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    from . import run_folders
    from .cli import build_parser, config_from_args
    a = parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    raw = Path(a.raw)
    if not raw.is_file():
        print(f"match_cuts: the RAW is not there: {raw}", file=sys.stderr)
        return 2
    run_dir = Path(a.run_dir)
    if (run_dir / run_folders.EDIT_XML).exists():
        print(f"match_cuts: {run_dir} holds an edit already", file=sys.stderr)
        return 2
    tool = ["--raw", str(raw), "--premiere", "--run-dir", str(run_dir)]
    if a.work:
        tool += ["--work", str(a.work)]
    if a.frame:
        tool += ["--frame", str(a.frame)]
    if a.watermark:
        tool += ["--watermark", str(a.watermark)]
    args = build_parser().parse_args(tool)
    if a.frame:
        try:
            from .frame import load_frame
            load_frame(a.frame)
        except Exception as e:  # noqa: BLE001 - said in plain words
            print(f"match_cuts: the template {Path(a.frame).name} cannot be used: {e}", file=sys.stderr)
            return 2
    cfg = config_from_args(args, "", str(raw))
    cfg.competitor, cfg.premiere = "", True
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg.deliver_dir, cfg.out_dir = str(run_dir), str(run_dir / run_folders.EXTRAS)
    try:
        r = run_just_cuts(cfg, faces_on=not a.no_faces)
    except Exception as e:  # noqa: BLE001 - said in plain words, the details in the log
        print(f"match_cuts: the cuts could not be made: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            (run_dir / run_folders.EXTRAS).mkdir(parents=True, exist_ok=True)
            (run_dir / run_folders.EXTRAS / JSON).write_text(json.dumps(
                {"ok": False, "errors": [f"{type(e).__name__}: {e}"], "trace": traceback.format_exc()}, indent=2),
                encoding="utf-8")
        except OSError:
            pass
        return 2
    (Path(cfg.out_dir) / JSON).write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Just cuts: {r['cuts']} cuts, {r['clips']} clips at {r['fps']} fps -- "
          f"{'PASS' if r['ok'] else 'made, with something to look at'}  ({run_dir})")
    return int(r["exit_code"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
