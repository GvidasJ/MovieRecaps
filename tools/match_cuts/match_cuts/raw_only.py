"""No competitor (``--raw`` without ``--competitor``): the edit is made from the RAW alone -- its speech kept, its
silences cut out (silence.py), in the usual numbered run folder: 1_edit.xml (the 1080x1920 / 60.00 fps Premiere
sequence) and 2_captions.srt, everything else in extras/.

The RAW is laid out as an edit of itself on the sequence's own 60 fps grid (competitor frame k = RAW second k / 60):
one segment per stretch of speech and one per silence to remove, so the Premiere export, its fixed framing and
--min-move rule, the silence removal, the captions and the end summary are exactly those of competitor mode. Framing:
every stretch of speech shows the RAW scaled to cover the template window, the main person's face (faces.py, five
frames: the largest face, then the same person while they stay in view) at the window's centre; then, as in
competitor mode, the person speaking (people.py / speakers.py: YuNet faces, Light-ASD) must be in the window -- a clip
that does not show them is moved sideways to centre them -- and --min-move holds the framing inside one shot of the
RAW until it would move that far. Captions: voice
mode with the RAW recheck, transcribed from the cut edit.
"""
from __future__ import annotations

import time
import traceback
from fractions import Fraction
from pathlib import Path
from typing import Any

from .common import Cache, DecisionLog, fps_str, log, setup_logging
from .geometry import Sim
from .model import Cutlist, Segment

SEQ_FPS = Fraction(60)


def window_box(win: tuple[float, float, float, float]) -> dict:
    return {"x": float(win[0]), "y": float(win[1]), "w": float(win[2]), "h": float(win[3]), "corner_radius": 0.0}


def framing(raw_wh: tuple[float, float], win: tuple[float, float, float, float], face_x: float | None) -> Sim:
    """The RAW scaled to just cover the window, centred on it -- or with RAW x face_x at the window's centre (then
    moved the least that still covers the window)."""
    from .export_xml_edl import _face_centred
    s = max(win[2] / raw_wh[0], win[3] / raw_wh[1])
    centred = Sim(s, 0.0, win[0] + win[2] / 2.0 - s * raw_wh[0] / 2.0, win[1] + win[3] / 2.0 - s * raw_wh[1] / 2.0)
    return centred if face_x is None else _face_centred(centred, face_x, raw_wh, win)


def build_cutlist(raw_block: dict, n_frames: int, cuts: list[tuple[int, int]], win: tuple[float, float, float, float],
                  seq_wh: tuple[int, int], face_x: Any = None) -> tuple[Cutlist, list[dict]]:
    """(the RAW as an edit of itself, its framing notes): segments tile [0, n_frames) of the 60 fps grid -- a
    stretch of speech, then the silence after it (removed by the export), and so on; RAW second of frame k = k / 60.
    ``face_x(t0, t1, view)`` -> RAW x of the main face over those RAW seconds (or None): the largest face, or the
    largest one inside ``view`` (the RAW x range the previous stretch's window showed) -- the same person while they
    stay in view, so a two-shot does not flip between the two faces."""
    raw_wh = (float(raw_block["width"]), float(raw_block["height"]))
    f = float(SEQ_FPS)
    bounds = sorted({0, n_frames} | {a for a, _ in cuts} | {b for _, b in cuts})
    removed = {(a, b) for a, b in cuts}
    segs: list[Segment] = []
    notes: list[dict] = []
    last: Sim | None = None
    for a, b in zip(bounds, bounds[1:]):
        if b <= a:
            continue
        silent = (a, b) in removed
        if silent and last is not None:
            sim = last
        else:
            view = None if last is None else ((win[0] - last.tx) / last.s, (win[0] + win[2] - last.tx) / last.s)
            fx = face_x(a / f, b / f, view) if (face_x is not None and not silent) else None
            sim = framing(raw_wh, win, fx)
            if not silent:
                last = sim
                notes.append({"from_s": round(a / f, 3), "to_s": round(b / f, 3),
                              "face_x": None if fx is None else round(float(fx), 1)})
        t = a / f
        segs.append(Segment(id=len(segs) + 1, type="raw", comp_in=a, comp_out=b, raw_in_seconds=t, speed=1.0,
                            raw_in_interval=[t - 0.25 / f, t + 0.25 / f], transform=sim.to_dict(), confidence=1.0,
                            label="silence" if silent else "speech"))
    for i, sg in enumerate(segs):                    # a leading silence takes the first speech's framing
        if sg.label == "silence" and i + 1 < len(segs) and (i == 0 or segs[i - 1].label == "silence"):
            nxt = next((s for s in segs[i + 1:] if s.label == "speech"), None)
            if nxt is not None:
                sg.transform = dict(nxt.transform)
    comp = {"file": raw_block.get("file", ""), "width": int(seq_wh[0]), "height": int(seq_wh[1]),
            "fps": fps_str(SEQ_FPS), "frames": int(n_frames)}
    layout = {"mode": "match", "layout_kind": "boxed", "box": window_box(win), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000", "zones": [],
              "captions": []}
    return Cutlist(1, comp, dict(raw_block), layout, segs), notes


def run_raw_only(cfg: Any) -> dict:
    """The RAW-only run (see the module docstring). Returns the dict cli.format_summary prints."""
    from . import conform, export_xml_edl, faces, pipeline, probe, proxies, silence, speech
    from .run_folders import CAPTIONS_SRT, EDIT_XML
    pipeline._prepare_dirs(cfg)
    setup_logging(cfg.verbose, log_file=cfg.out / "match_cuts.log")
    log.info("match_cuts RAW-only: raw=%s out=%s work=%s", cfg.raw, cfg.out_dir, cfg.work_dir)
    ctx = pipeline.Context(cfg=cfg, dlog=DecisionLog(cfg.work / "decisions.jsonl", truncate=True),
                           cache=Cache(cfg.work))
    ctx.input_stats = {cfg.raw: pipeline._input_stat(cfg.raw)}
    t_all = time.perf_counter()
    ok = False
    try:
        with pipeline._stage(ctx, "R1 probe+media"):
            ctx.raw_input = probe.probe(cfg.raw, "raw", str(cfg.work), decode=True)
            ctx.raw_conform = pipeline.self_cached_stage(
                ctx, "conform_raw", pipeline._conform_key(ctx, ctx.raw_input),
                lambda: conform.conform(ctx.raw_input, "raw", cfg, ctx.dlog), ("conform",))
            ctx.raw_info = probe.probe(str(ctx.raw_conform.path), "raw", str(cfg.work), decode=True)
            if ctx.raw_info.nb_frames <= 0 or not ctx.raw_info.fps:
                raise RuntimeError(f"RAW media {ctx.raw_info.path} has no decodable video frames")
        with pipeline._stage(ctx, "R2 audio"):
            ctx.audio_sr = int(cfg.audio_sr)
            ctx.raw_audio = proxies.load_audio(ctx.raw_info, ctx.audio_sr, ctx.cache)
            if ctx.raw_audio is None or not len(ctx.raw_audio):
                ctx.warn("the RAW has no audio: nothing to tell speech from silence -- the whole RAW is the edit")
        st = export_xml_edl.premiere_settings(cfg)
        win, seq_wh = st["window"], st["size"]
        raw_block = pipeline.media_block(ctx.raw_info, ctx.raw_conform, ctx.raw_input)
        n_frames = int(ctx.raw_info.nb_frames / float(Fraction(ctx.raw_info.fps)) * float(SEQ_FPS))
        with pipeline._stage(ctx, "R3 silences"):
            sst = silence.Settings.from_cfg(cfg)
            words = None
            if ctx.raw_audio is not None and len(ctx.raw_audio):
                words_of = pipeline.words_reader(ctx)
                words = words_of(ctx.raw_audio) if words_of else None
                ctx.speech = speech.speech_map(ctx.raw_audio, ctx.audio_sr, sst, words)   # the hard speech check
            ctx.shots = pipeline.shots_of(ctx)                                             # no flash frame
            if getattr(cfg, "keep_silence", False):
                cuts, lv = [], {"how": "--keep-silence"}
            elif ctx.raw_audio is not None and len(ctx.raw_audio):
                guard = (silence.shot_guard_frames(None, ctx.speech, ctx.shots, SEQ_FPS) if ctx.shots else None)
                found, lv = silence.removal_ranges(ctx.raw_audio, ctx.audio_sr, SEQ_FPS, n_frames, sst, words=words,
                                                   guard=guard)
                cuts = [(c.a, c.b) for c in found]
            else:
                cuts, lv = [], {"how": "the RAW has no audio"}
        with pipeline._stage(ctx, "R4 framing"):
            video, raw_fps = str(raw_block.get("file_abs") or ctx.raw_info.path), float(Fraction(ctx.raw_info.fps))

            def face_x(t0: float, t1: float, view: tuple[float, float] | None) -> float | None:
                return faces.main_face_x(video, raw_fps, [t0 + (t1 - t0) * (i + 0.5) / 5.0 for i in range(5)],
                                         view)[0]
            ctx.cutlist, framing_notes = build_cutlist(raw_block, n_frames, cuts, win, seq_wh, face_x)
            ctx.people = pipeline.people_of(ctx, ctx.cutlist)          # the person speaking always in the picture
            cfg.premiere_people = ctx.people
        if getattr(cfg, "keep_silence", False):
            ctx.silence = {"off": "--keep-silence"}
        else:
            plan = silence.summarize([silence.Cut(a, b, a / 60.0, b / 60.0) for a, b in cuts], n_frames, SEQ_FPS,
                                     sst, lv)
            ctx.silence = plan
        ctx.silence = pipeline.repeat_plan(ctx, ctx.cutlist, ctx.silence)
        rp = ctx.silence.get("ripple")
        # the "competitor" of this run is the RAW itself on the 60 fps grid (captions: voice mode, no OCR)
        import dataclasses
        ctx.comp_info = dataclasses.replace(ctx.raw_info, role="competitor", fps=SEQ_FPS, nb_frames=n_frames,
                                            width=int(seq_wh[0]), height=int(seq_wh[1]),
                                            display_width=int(seq_wh[0]), display_height=int(seq_wh[1]))
        xml = cfg.deliver / EDIT_XML
        with pipeline._stage(ctx, "R5 Premiere XML"):
            res = export_xml_edl.write_premiere_xml(ctx.cutlist, xml, cfg, rp)
            ctx.paths["xml"] = str(xml)
            ctx.exports = export_xml_edl.validate_premiere_exports(ctx.cutlist, xml, None, cfg, rp, ctx.speech,
                                                                   ctx.shots)
            ctx.exports["clips"], ctx.exports["framing"] = res["clips"], framing_notes
            if ctx.exports.get("gaps"):
                ctx.warn("Premiere XML: clip(s) leave part of the template window uncovered: "
                         + "; ".join(ctx.exports["gaps"]))
            if ctx.exports.get("repeat_problems"):
                ctx.warn("Premiere XML: repeat(s) of RAW footage / audio left -- the run fails: "
                         + "; ".join(ctx.exports["repeat_problems"]))
            if ctx.exports.get("item_problems"):
                ctx.warn("Premiere XML: item(s) Premiere would skip or misplace on import -- the run fails: "
                         + "; ".join(ctx.exports["item_problems"]))
            if ctx.exports.get("speech_problems"):
                ctx.warn("Premiere XML: audio cut(s) inside speech -- the run fails: "
                         + "; ".join(ctx.exports["speech_problems"]))
            if ctx.exports.get("person_problems"):
                ctx.warn("Premiere XML: clip(s) do not show the person speaking -- the run fails: "
                         + "; ".join(ctx.exports["person_problems"]))
            ctx.premiere_xml = res
            pipeline.warn_flash_silence(ctx, ctx.exports)
            if ctx.exports.get("ok") is not True:
                ctx.warn(f"Premiere XML check failed: {'; '.join(ctx.exports.get('errors') or [])[:500]}")
        with pipeline._stage(ctx, "R6 captions"):
            pipeline.stage_captions(ctx)
        ctx.cutlist.save(cfg.out / "cutlist.json")
        ctx.paths["cutlist"] = str(cfg.out / "cutlist.json")
        report = cfg.out / "report.md"
        report.write_text(render_report(ctx), encoding="utf-8")
        ctx.paths["report"] = str(report)
        ok = ctx.exports.get("ok") is True
    except Exception as e:  # noqa: BLE001 - reported in the summary with the log location
        log.error("RAW-only run failed: %s\n%s", e, traceback.format_exc())
        raise
    finally:
        ctx.timings["total"] = round(time.perf_counter() - t_all, 3)
        ctx.dlog.close()
    cap_path = cfg.deliver / CAPTIONS_SRT
    if cap_path.exists():
        ctx.paths["captions"] = str(cap_path)
    try:
        checklist = pipeline.hand_checks(ctx)
    except Exception as e:  # noqa: BLE001 - the summary must not fail the run
        checklist = {"broll": [], "spots": [], "captions": [f"(could not list: {type(e).__name__}: {e})"]}
    checks: dict = {}
    ctx.verify = {"checks": checks}
    pipeline.run_checks(ctx, checks)              # (as in competitor mode) a changed RAW, a hard check left unrun
    moved = (checks.get("inputs_unchanged") or {}).get("summary")
    nv = pipeline.not_verified(checks)
    ok = ok and not moved
    code = 1 if not ok else (3 if nv else 0)
    headline = ("FAIL" if not ok else f"PASS (not checked: {'; '.join(nv)})" if nv else "PASS") + \
        " (RAW-only edit, no competitor: 1_edit.xml checked against its plan)"
    fails = ([] if ok else list(ctx.exports.get("errors", []))) + ([moved] if moved else [])
    return {"raw_only": True, "criteria": {}, "checks": checks, "failures": fails,
            "warnings": list(ctx.warnings), "paths": dict(ctx.paths), "timings": dict(ctx.timings),
            "exit_code": code, "headline": headline, "context": ctx, "run_dir": str(cfg.deliver),
            "checklist": checklist}


def render_report(ctx: Any) -> str:
    """extras/report.md of a RAW-only run: the input, the framing, the silences removed, the export check, the
    captions."""
    from . import report
    cfg = ctx.cfg
    info = ctx.raw_info
    ex = ctx.exports or {}
    lines = [f"# Match cuts report: {Path(cfg.raw).name} (RAW only, no competitor)", "",
             "## 1. Input", "",
             report.md_table(["property", "value"], [
                 ["file", str(cfg.raw)], ["size", f"{info.display_width or info.width}x{info.display_height or info.height}"],
                 ["fps", fps_str(Fraction(info.fps))], ["frames", int(info.nb_frames)],
                 ["audio", "yes" if info.has_audio else "no"]]), "",
             "## 2. Framing", "",
             "Every stretch of speech: the RAW scaled to cover the template window, the main person's face at the "
             f"window's centre; --min-move {float(getattr(cfg, 'premiere_min_move', 250.0)):g} px holds the framing "
             "until it would move that far.", ""]
    rows = [[f"{r['from_s']:.2f}-{r['to_s']:.2f} s", "no face found: centred" if r["face_x"] is None else
             f"face at RAW x {r['face_x']:.0f}"] for r in ex.get("framing") or []]
    if rows:
        lines += [report.md_table(["RAW", "framing"], rows), ""]
    lines += ["## 3. Silence removal", ""] + report._silence(ctx) + [""]
    xml = ex.get("xml") or {}
    lines += ["## 4. 1_edit.xml check", "",
              f"- {'passed' if ex.get('ok') else 'FAILED'}: {xml.get('clips', 0)} V1 clips, {xml.get('audio', 0)} A1 "
              f"clips, {xml.get('size', '')} at {xml.get('rate', '')}, framing changes {xml.get('framing_changes', 0)}"]
    lines += [f"- {e}" for e in ex.get("errors") or []]
    lines += ["", "## 5. Captions", ""] + report._captions(ctx) + [""]
    lines += ["## 6. Warnings", ""] + ([f"- {w}" for w in ctx.warnings] or ["none"])
    return "\n".join(lines).rstrip() + "\n"
