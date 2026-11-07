"""7 Nov (run 21's task 1, done now -- your call: "accurate as fast as possible"): MATCH ON THE NORMAL RAW, PLAY THE
ENHANCED PICTURE.

Run 20 matched the edit ON Topaz's 60 fps piece. Apollo makes new in-between frames, so many competitor frames have no
exact frame there: the last 33 enhanced edits had a median of 19 cuts per 10 s (1-3 frame clips), and 8 of 33 passed.
Now the edit is matched on the normal RAW, exactly as without enhance (flow.edit_job), and does not wait for Topaz;
when the enhanced piece is back (core.enhance.watch / take_back) and the edit is finished, ``swap_in`` makes the
finished 1_edit.xml play it:

* every V1 clip whose RAW frames lie inside the enhanced piece plays enhance\\raw_enhanced.mp4 at the same moment --
  enhanced time = RAW time - the piece's start (its _TOENHANCE.json: start_frame, fps) -- on the clip's own scale (its
  rate and speed: a shift that is not whole keeps that clip on the normal RAW); a RAW clip of an edit that plays only
  the scene (extras\\keep_piece.json) is first put back on the movie's frames;
* its framing stays: its position and scale halved (the enhanced picture has 2x the pixels -- Premiere reads both
  in the clip's own pixels);
* a clip partly outside the piece keeps the normal RAW; A1 (the sound) is not touched -- in sync, as matched;
* checked before anything changes: three swapped clips show the normal RAW's picture at the mapped moment (gray,
  small, closer than half a second before or after) -- else nothing changes and the edit stays on the normal RAW;
* the edit as it was matched stays in extras\\1_edit_normal.xml (swapping again starts from it).

Settings -> "Match on the normal RAW" (on): off goes back to run 20 (the edit waits for the piece and is matched on
it). Everything here reads files and decodes frames: call it from a worker thread."""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

NORMAL_XML = "1_edit_normal.xml"
CHECK_MIN = 0.55                    # the mapped frame's correlation with the normal RAW's (gray, 160 px) at least
CHECK_MARGIN = 0.02                 # ... and not worse than half a second before / after by more than this
MATCH_ENV = "MOVIERECAPS_MATCH_NORMAL"
MAX_REDOS = 3                       # 7 Nov: a video's piece goes to Topaz again at most this often (a failure, clips
                                    #   left on the normal RAW) -- your call: no normal RAW in the timeline


def match_normal(settings=None) -> bool:
    """Settings -> Match on the normal RAW (on by itself); MOVIERECAPS_MATCH_NORMAL=0/1 wins (the tests)."""
    env = os.environ.get(MATCH_ENV, "").strip()
    if env in ("0", "1"):
        return env == "1"
    try:
        if settings is None:
            from . import settings as S
            settings = S.load()
        return bool(getattr(settings, "match_normal", True))
    except Exception:  # noqa: BLE001
        return True


def _rate(el) -> Fraction | None:
    if el is None or el.find("timebase") is None:
        return None
    tb = int(el.findtext("timebase") or 0)
    return Fraction(tb * 1000, 1001) if str(el.findtext("ntsc") or "").upper() == "TRUE" else Fraction(tb)


def _speed(ci) -> Fraction:
    try:
        from match_cuts.excerpt import _clip_speed
        return Fraction(str(round(float(_clip_speed(ci)), 6))).limit_denominator(100000)
    except Exception:  # noqa: BLE001
        return Fraction(1)


def _groups(run: Path) -> list[dict]:
    try:
        d = json.loads((run / "extras" / "keep_piece.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [g for g in (d.get("groups") or []) if isinstance(g, dict)] if d.get("kept") else []


def to_movie(frame: Fraction | int, groups: list[dict]) -> Fraction | None:
    """A frame of the RAW 1_edit.xml plays -> the movie's frame (the scene's piece: keep_piece's groups)."""
    if not groups:
        return Fraction(frame)
    for g in groups:
        off, n = int(g["offset"]), int(g["k1"]) - int(g["k0"])
        if off <= frame <= off + n:
            return Fraction(frame) + int(g["k0"]) - off
    return None


def _v1(root) -> list:
    seq = root.find(".//sequence")
    out = []
    for tr in (seq.findall("media/video/track") if seq is not None else []):
        for c in tr.findall("clipitem"):
            f = c.find("file")
            if f is not None and f.get("id") == "file-raw":
                out.append(c)
    return out


def _gray(path: Path, fps: Fraction, frames: list[int]) -> dict:
    import cv2
    from match_cuts.media import VideoReader
    with VideoReader(str(path), fps=fps) as r:
        got = r.get_many(sorted({f for f in frames if f >= 0}), fmt="gray")
    out = {}
    for k, a in got.items():
        if a is None:
            continue
        h, w = a.shape[:2]
        out[k] = cv2.resize(a, (160, max(8, int(round(h * 160 / max(1, w))))), interpolation=cv2.INTER_AREA)
    return out


def _zncc(a, b) -> float:
    import numpy as np
    if a is None or b is None or a.shape != b.shape:
        return -1.0
    x, y = a.astype(np.float64).ravel(), b.astype(np.float64).ravel()
    x, y = x - x.mean(), y - y.mean()
    d = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return float((x * y).sum()) / d if d > 0 else -1.0


def check(normal_media: Path, raw_fps: Fraction, enhanced: Path, pairs: list[tuple[int, Fraction]]) -> dict:
    """``pairs``: (the normal media's frame, the enhanced piece's time in s) in the middle of up to three swapped
    clips -- the mapped enhanced frame looks like the normal one, and is not beaten by half a second before or after.
    {ok, rows, why}."""
    from .enhance import OUT_FPS
    want = {int(round(float(t) * OUT_FPS)) for _f, t in pairs}
    alt = {e + d for e in want for d in (-OUT_FPS // 2, OUT_FPS // 2)}
    norm = _gray(normal_media, raw_fps, [f for f, _t in pairs])
    enh = _gray(enhanced, Fraction(OUT_FPS), sorted(want | alt))
    rows, bad = [], []
    for f, t in pairs:
        e = int(round(float(t) * OUT_FPS))
        a = norm.get(f)
        s = _zncc(a, enh.get(e))
        others = [_zncc(a, enh.get(x)) for x in (e - OUT_FPS // 2, e + OUT_FPS // 2) if enh.get(x) is not None]
        rows.append({"normal_frame": f, "enhanced_frame": e, "zncc": round(s, 4),
                     "half_second_off": [round(x, 4) for x in others]})
        if s < CHECK_MIN or any(x > s + CHECK_MARGIN for x in others):
            bad.append(f"the enhanced frame {e} does not show the RAW's frame {f} (ZNCC {s:.2f})")
    return {"ok": not bad and bool(rows), "rows": rows, "why": "; ".join(bad) or ("" if rows else "nothing read")}


def _same(a: Path, b: Path) -> bool:
    """The same video: one file (a hard link) or the same size."""
    try:
        return os.path.samefile(a, b) or a.stat().st_size == b.stat().st_size
    except OSError:
        return False


def _media(root, fid: str) -> Path | None:
    from .share_zip import _path_of_url, reach
    for f in root.iter("file"):
        if f.get("id") == fid and f.findtext("pathurl"):
            p = _path_of_url((f.findtext("pathurl") or "").strip())
            return reach(p) if p is not None else None
    return None


def outside(project) -> dict:
    """7 Nov (your call: "everything must be in enhanced"): the V1 clips of the finished edit (as matched: on the
    normal RAW) whose frames are not in the piece sent to Topaz. {"need": [(movie frame a, b) ...], "run", "media",
    "groups", "fps", "start", "frames"} -- {} when there is nothing to look at."""
    from . import projects as PJ
    from .share_zip import run_dir_of
    p = PJ.load(project)
    rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
    info = rec.get("piece") if isinstance(rec.get("piece"), dict) else {}
    if rec.get("state") not in ("sent", "used", "failed", "skipped", "late"):
        return {}
    if rec.get("state") in ("failed", "skipped", "late") or "start_frame" not in info or not info.get("fps"):
        info = {}                                        # no piece with Topaz: every clip needs one
    ed = (p.data.get("steps") or {}).get("edit") or {}
    if ed.get("state") != PJ.DONE:
        return {}
    run = run_dir_of(Path(p.folder), ed)
    normal = run / "extras" / NORMAL_XML
    src = normal if normal.is_file() else run / "1_edit.xml"
    try:
        root = ET.parse(str(src)).getroot()
    except (OSError, ET.ParseError):
        return {}
    raw_def = next((f for f in root.iter("file") if f.get("id") == "file-raw" and f.find("rate") is not None), None)
    raw_fps = Fraction(str(info["fps"])) if info else (_rate(raw_def.find("rate")) if raw_def is not None else None)
    if raw_fps is None:
        return {}
    start, n = (int(info["start_frame"]), int(info.get("frames") or 0)) if info else (None, 0)
    groups = _groups(run)
    need = []
    for c in _v1(root):
        rate = _rate(c.find("rate")) or raw_fps
        k = _speed(c) * raw_fps / rate
        try:
            a, b = int(c.findtext("in")), int(c.findtext("out"))
        except (TypeError, ValueError):
            continue
        ma, mb = to_movie(a * k, groups), to_movie(b * k, groups)
        if ma is None or mb is None:
            continue
        if start is None or ma < start or mb > start + n:
            need.append((int(math.floor(ma)), int(math.ceil(mb))))
    return {"need": need, "run": run, "media": _media(root, "file-raw"), "groups": groups, "fps": raw_fps,
            "start": start, "frames": n, "rec": rec}


def cover_all(project, say=None) -> dict:
    """7 Nov: when clips of the finished edit lie outside the piece Topaz has, a new piece goes to Topaz instead --
    from the first frame to the last one the edit plays (the old piece with them, 1 s more each side), cut from the
    file the edit plays; the old piece leaves the line when nobody enhances it yet. The swap then puts the enhanced
    picture on every clip. {ok, why, clips} -- never raises."""
    from . import enhance as E
    from . import projects as PJ
    try:
        o = outside(project)
        if not o or not o["need"]:
            return {"ok": True, "why": "every clip is in the enhanced piece" if o else "nothing to look at", "clips": 0}
        media = o["media"]
        if media is None or not Path(media).is_file():
            return {"ok": False, "why": "the RAW the edit plays is not here to cut a new piece from"}
        rec = o["rec"]
        if int(rec.get("redos") or 0) >= MAX_REDOS:
            return {"ok": False, "why": f"Topaz was tried {MAX_REDOS} times for this video"}
        if rec.get("state") == "skipped":               # never sent: only when this computer sends to Topaz
            from . import enhance as E0
            from . import settings as S0
            if not E0.sends(S0.load()):
                return {"ok": False, "why": "sending to Topaz is off in Settings"}
        fps, groups = o["fps"], o["groups"]
        old_span = [] if o["start"] is None else [(o["start"], o["start"] + o["frames"])]
        lo = min([a for a, _b in old_span + o["need"]])
        hi = max([b for _a, b in old_span + o["need"]])
        off = 0                                          # movie frames -> the frames of the file the edit plays
        if groups:
            if len(groups) > 1:
                g = [g for g in groups if int(g["k0"]) <= lo and hi <= int(g["k1"])]
                if not g:
                    return {"ok": False, "why": "the clips are in several pieces of the RAW"}
                g = g[0]
            else:
                g = groups[0]
            off = int(g["k0"]) - int(g["offset"])
            lo, hi = max(lo, int(g["k0"])), min(hi, int(g["k1"]))
        a_s = max(0.0, float(Fraction(lo - off) / fps) - 1.0)
        b_s = float(Fraction(hi - off) / fps) + 1.0
        p = PJ.load(project)
        old = str(rec.get("name") or E.safe_name(Path(p.folder).name))
        name = E.safe_name(Path(p.folder).name) + "_all"
        if old == name:
            name = E.safe_name(Path(p.folder).name) + "_all2"
        folder = rec.get("folder") or str(E.share_dir())
        info = E.make_piece(None, media, name, folder, project=p.folder, span=(a_s, b_s),
                            topaz_on=str((rec.get("piece") or {}).get("topaz_on") or ""),
                            progress=(lambda s, t: say(s, t)) if say else None)
        if off:                                          # the piece's start in the movie's frames (swap_in maps so)
            info["start_frame"] = int(info["start_frame"]) + off
            info["start_s"] = round(float(Fraction(int(info["start_frame"])) / fps), 6)
            E._write_json(E.names(folder, name).info, info)                       # noqa: SLF001
        lk = E.read_lock(E.names(folder, old).lock)
        if lk is None or E.lock_stale(E.names(folder, old).lock):
            E.clear(folder, old)                         # the old piece: nobody enhances it -- it leaves the line
        p.update(enhance={"name": name, "folder": str(folder), "state": "sent", "sent": time.time(),
                          "where": info.get("where"), "piece": info, "swapped": None, "swap": None, "used": None,
                          "text": None, "file": None, "why": None, "redos": int(rec.get("redos") or 0) + 1,
                          "redo": f"{len(o['need'])} clip(s) were not enhanced: a piece with all of them again"})
        return {"ok": True, "why": "", "clips": len(o["need"]), "where": info.get("where")}
    except Exception as e:  # noqa: BLE001 - the edit stays as it is (the clips outside keep the normal RAW)
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def swap_in(project, do_check: bool = True) -> dict:
    """The finished edit plays the enhanced picture (module text). {ok, why, swapped, kept, text}; changes nothing
    when it is not ok. Never raises."""
    from . import projects as PJ
    from .enhance import OUT_FPS, PROJECT_FILE, PROJECT_SUB, read_info
    from .share_zip import run_dir_of
    try:
        p = PJ.load(project)
        rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
        if rec.get("state") != "used":
            return {"ok": False, "why": "the enhanced piece is not back yet"}
        ed = (p.data.get("steps") or {}).get("edit") or {}
        if ed.get("state") != PJ.DONE:
            return {"ok": False, "why": "the edit is not finished yet"}
        folder = Path(p.folder)
        if rec.get("swapped") and (rec.get("swap") or {}).get("run") == run_dir_of(folder, ed).name and \
                (run_dir_of(folder, ed) / "extras" / NORMAL_XML).is_file():
            return {"ok": True, "why": "", "text": str(rec.get("text") or ""), "already": True}
        enh = folder / PROJECT_SUB / PROJECT_FILE
        info = read_info(folder / PROJECT_SUB / "enhance.json") or {}
        if not enh.is_file() or "start_frame" not in info or not info.get("fps"):
            return {"ok": False, "why": "the enhanced piece or its start is not in the project"}
        run = run_dir_of(folder, ed)
        xml = run / "1_edit.xml"
        normal = run / "extras" / NORMAL_XML
        if not xml.is_file():
            return {"ok": False, "why": "the edit has no 1_edit.xml"}
        src = normal if normal.is_file() else xml
        tree = ET.parse(str(src))
        root = tree.getroot()
        clips = _v1(root)
        if not clips:
            return {"ok": False, "why": "the edit has no RAW clip on V1"}
        raw_fps = Fraction(str(info["fps"]))
        start = int(info["start_frame"])
        chk = info.get("enhanced") if isinstance(info.get("enhanced"), dict) else {}
        enh_frames = int(chk.get("frames") or 0)
        width, height = chk.get("width"), chk.get("height")
        if enh_frames <= 0:
            return {"ok": False, "why": "the enhanced piece's length is not known"}
        covered = int(math.floor(Fraction(enh_frames) * raw_fps / OUT_FPS))
        groups = _groups(run)
        normal_media = _media(root, "file-raw")
        raw_def = next((copy.deepcopy(f) for f in root.iter("file") if f.get("id") == "file-raw"
                        and f.find("pathurl") is not None), None)
        if raw_def is None or normal_media is None:
            return {"ok": False, "why": "1_edit.xml does not say where its RAW is"}
        if _rate(raw_def.find("rate")) not in (None, raw_fps) or _same(normal_media, enh):
            return {"ok": False, "why": "this edit was matched on the enhanced piece itself (run 20): it plays it "
                                        "already -- Redo it on the normal RAW first"}
        raw_url = raw_def.findtext("pathurl") or ""
        if "/" not in raw_url or normal_media.parent.name.lower() != "media":
            return {"ok": False, "why": "1_edit.xml's RAW is not in the edit's media folder"}
        media_enh = normal_media.parent / PROJECT_FILE      # next to the RAW the edit plays: published, zipped with it
        enh_def = ET.Element("file", {"id": "file-enhanced"})
        ET.SubElement(enh_def, "name").text = PROJECT_FILE
        ET.SubElement(enh_def, "pathurl").text = raw_url.rsplit("/", 1)[0] + f"/{PROJECT_FILE}"
        r = ET.SubElement(enh_def, "rate")
        ET.SubElement(r, "timebase").text = str(OUT_FPS)
        ET.SubElement(r, "ntsc").text = "FALSE"
        ET.SubElement(enh_def, "duration").text = str(enh_frames)
        sc = ET.SubElement(ET.SubElement(ET.SubElement(enh_def, "media"), "video"), "samplecharacteristics")
        r2 = ET.SubElement(sc, "rate")
        ET.SubElement(r2, "timebase").text = str(OUT_FPS)
        ET.SubElement(r2, "ntsc").text = "FALSE"
        if width and height:
            ET.SubElement(sc, "width").text = str(width)
            ET.SubElement(sc, "height").text = str(height)
        ET.SubElement(sc, "pixelaspectratio").text = "square"
        plan, kept = [], 0
        for c in clips:
            rate = _rate(c.find("rate")) or raw_fps
            k = _speed(c) * raw_fps / rate                 # one step of <in> / <out> in the RAW's frames
            try:
                a, b = int(c.findtext("in")), int(c.findtext("out"))
            except (TypeError, ValueError):
                kept += 1
                continue
            ma, mb = to_movie(a * k, groups), to_movie(b * k, groups)
            if ma is None or mb is None or ma < start or mb > start + covered:      # (<out> is the frame after)
                kept += 1
                continue
            na, nb = (ma - start) / k, (mb - start) / k
            if (na.denominator != 1 or nb.denominator != 1) and rate == OUT_FPS:
                na = Fraction(int(round(na)))              # 7 Nov: a 60 fps sequence's clip -- the nearest of the
                nb = na + (b - a)                          #   enhanced piece's 60 fps frames (within 1/120 s)
            if na.denominator != 1 or nb.denominator != 1:
                kept += 1                                  # not a whole step on its scale: the normal RAW
                continue
            mid = int((a * k + b * k) / 2)                 # (the check looks inside the clip, not at a cut)
            mm = to_movie(mid, groups)
            plan.append((c, int(na), int(nb), mid, (mm if mm is not None else ma) - start, rate, k))
        if not plan:
            return {"ok": False, "why": f"no V1 clip lies inside the enhanced piece ({kept} outside it)"}
        if do_check:
            idx = sorted({0, len(plan) // 2, len(plan) - 1})
            pairs = [(plan[i][3], Fraction(plan[i][4]) / raw_fps) for i in idx]
            ck = check(normal_media, raw_fps, enh, pairs)
            if not ck["ok"]:
                p.update(enhance={"swap_failed": ck["why"], "swap_check": ck["rows"]})
                return {"ok": False, "why": f"the enhanced piece does not line up with the edit: {ck['why']}"}
        else:
            ck = {"rows": []}
        defined = False
        for c, na, nb, _f, _m, rate, k in plan:
            c.find("in").text, c.find("out").text = str(na), str(nb)
            dur = c.find("duration")
            if dur is not None:
                dur.text = str(int(math.floor(Fraction(enh_frames, OUT_FPS) * raw_fps / k)))
            if c.find("name") is not None:
                c.find("name").text = PROJECT_FILE
            f = c.find("file")
            i = list(c).index(f)
            c.remove(f)
            c.insert(i, copy.deepcopy(enh_def) if not defined else ET.Element("file", {"id": "file-enhanced"}))
            defined = True
            for par in c.iter("parameter"):
                if (par.findtext("parameterid") or "") == "scale":
                    for v in par.iter("value"):
                        if v.text and v.text.strip():
                            v.text = f"{float(v.text) / 2.0:.6f}"
                elif (par.findtext("parameterid") or "") == "center":
                    _half_center(par)
        users = [ci for ci in root.iter("clipitem") if ci.find("file") is not None
                 and ci.find("file").get("id") == "file-raw"]
        if users and not any(ci.find("file").find("pathurl") is not None for ci in users):
            first = users[0]                               # the RAW's definition goes to its first clip left
            f = first.find("file")
            i = list(first).index(f)
            first.remove(f)
            first.insert(i, copy.deepcopy(raw_def))
        if not normal.is_file():
            normal.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(xml, normal)
        if not media_enh.is_file() or media_enh.stat().st_size != enh.stat().st_size:
            part = media_enh.with_name(media_enh.stem + ".part" + media_enh.suffix)
            part.unlink(missing_ok=True)
            try:
                os.link(enh, part)                         # a hard link: no space
            except OSError:
                shutil.copyfile(enh, part)
            os.replace(part, media_enh)
        tmp = xml.with_name(xml.stem + ".swap.part.xml")
        ET.indent(root, space="  ")
        tmp.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' +
                       ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
        ET.parse(str(tmp))                                 # (reads back)
        os.replace(tmp, xml)
        try:                                               # 7 Nov: no small cuts (core.join_clips)
            from . import join_clips
            join_clips.join(xml)
        except Exception:  # noqa: BLE001
            pass
        text = (f"enhanced: {width}x{height}, {OUT_FPS} fps -- {len(plan)} of {len(plan) + kept} V1 clips play it "
                f"(matched on the normal RAW)")
        p.update(enhance={"swapped": _now(), "text": text, "swap": {"run": run.name, "clips": len(plan),
                                                                   "kept": kept, "check": ck.get("rows")}})
        try:                                               # only the scene stays: the downloaded RAW nothing plays
            from . import archive, run as RN              #   any more leaves the folder (as after an edit)
            if RN.scene_env():
                archive.drop_unplayed(folder, PJ.load(folder).data)
        except Exception:  # noqa: BLE001
            pass
        zip_when_done(folder)                              # the zip to send: the cut Topaz version
        return {"ok": True, "why": "", "swapped": len(plan), "kept": kept, "text": text}
    except Exception as e:  # noqa: BLE001 - the edit stays on the normal RAW
        try:
            tmp = locals().get("tmp")
            if tmp is not None and Path(tmp).exists():
                Path(tmp).unlink()
        except OSError:
            pass
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def _half_center(par) -> None:
    """7 Nov: Premiere reads a clip's Position (center horiz / vert) in the clip's OWN pixels -- the enhanced picture
    has 2x the pixels, so the same numbers moved it twice as far (a black band over the picture). Halved, like the
    scale; keyframed positions too."""
    for v in par.iter("value"):
        for ax in ("horiz", "vert"):
            e = v.find(ax)
            if e is not None and e.text and e.text.strip():
                e.text = f"{float(e.text) / 2.0:.7f}"


def wants_60(job) -> bool:
    """7 Nov (your call: "it should always be 60"): an edit whose project sends its piece to Topaz is made in a 60 fps
    sequence (--sequence-fps 60) -- the enhanced picture swapped in later is 60 fps, and plays at its own rate."""
    try:
        if not match_normal():
            return False
        folder = getattr(job, "project", None)
        if folder is None:
            return False
        d = json.loads((Path(folder) / "project.json").read_text(encoding="utf-8"))
        e = d.get("enhance") if isinstance(d.get("enhance"), dict) else {}
        return e.get("state") in ("sent", "used")
    except Exception:  # noqa: BLE001 - as before: the RAW's own rate
        return False


def zip_when_done(project) -> None:
    """The zip to send, made now that the picture is final (the Topaz one, or the normal RAW when Topaz failed or
    is late) -- only for a finished edit; in its own thread (core.share_zip)."""
    try:
        from . import projects as PJ
        from . import share_zip as Z
        p = PJ.load(project)
        ed = (p.data.get("steps") or {}).get("edit") or {}
        if ed.get("state") == PJ.DONE:
            Z.make_later(Path(p.folder), Z.run_dir_of(Path(p.folder), ed))
    except Exception:  # noqa: BLE001
        pass


def _now() -> str:
    from . import projects as PJ
    return PJ.now()
