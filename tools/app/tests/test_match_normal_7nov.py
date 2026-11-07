"""7 Nov (run 21's task 1, your call): the edit is matched on the NORMAL RAW (accurate) and does not wait for Topaz;
the enhanced piece is swapped into the finished 1_edit.xml when it is back (core.enhance_swap): V1 clips inside the
piece play it at the same moment (RAW time - the piece's start, on the clip's own scale; through keep_piece's groups
when the edit plays only the scene), their scale halved; clips outside it, and A1, stay on the normal RAW."""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import flow  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402


def _url(p: Path) -> str:
    return "file://localhost/" + p.resolve().as_posix().lstrip("/")


def _clip(cid: str, fid: str, a: int, b: int, define: Path | None, rate="24", ntsc="FALSE", scale=None, speed=None):
    f = (f"<file id=\"{fid}\"><name>{define.name}</name><pathurl>{_url(define)}</pathurl><rate><timebase>{rate}"
         f"</timebase><ntsc>{ntsc}</ntsc></rate><duration>9999</duration></file>" if define is not None
         else f"<file id=\"{fid}\"/>")
    eff = ""
    if scale is not None:
        eff += (f"<filter><effect><name>Basic Motion</name><effectid>basic</effectid><parameter><parameterid>scale"
                f"</parameterid><value>{scale}</value></parameter></effect></filter>")
    if speed is not None:
        eff += (f"<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><parameter><parameterid>speed"
                f"</parameterid><value>{speed}</value></parameter></effect></filter>")
    return (f"<clipitem id=\"{cid}\"><name>raw.mp4</name><duration>9999</duration><rate><timebase>{rate}</timebase>"
            f"<ntsc>{ntsc}</ntsc></rate><in>{a}</in><out>{b}</out>{f}{eff}</clipitem>")


def _project(tmp_path: Path, spans, *, start=100, enh_frames=600, fps="24", groups=None, state="used",
             speeds=None) -> PJ.Project:
    p = PJ.create(tmp_path / "projects", name="army song")
    media = p.folder / "edit" / "extras" / "media"
    media.mkdir(parents=True)
    raw = media / "raw.mp4"
    raw.write_bytes(b"r" * 1000)
    wav = media / "raw_stereo.wav"
    wav.write_bytes(b"w" * 100)
    v = "".join(_clip(f"v{i}", "file-raw", a, b, raw if i == 0 else None, scale=86.0,
                      speed=(speeds or {}).get(i)) for i, (a, b) in enumerate(spans))
    au = "".join(_clip(f"a{i}", "file-raw-sound", a, b, wav if i == 0 else None) for i, (a, b) in enumerate(spans))
    (p.folder / "edit" / "1_edit.xml").write_text(
        "<?xml version=\"1.0\"?>\n<xmeml version=\"5\"><sequence><rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>"
        f"<media><video><track>{v}</track></video><audio><track>{au}</track></audio></media></sequence></xmeml>",
        encoding="utf-8")
    if groups is not None:
        (p.folder / "edit" / "extras" / "keep_piece.json").write_text(json.dumps({"kept": True, "groups": groups}),
                                                                     encoding="utf-8")
    enh = p.folder / E.PROJECT_SUB / E.PROJECT_FILE
    enh.parent.mkdir()
    enh.write_bytes(b"e" * 2000)
    (enh.parent / "enhance.json").write_text(json.dumps({"start_frame": start, "start_s": start / 24, "fps": fps,
                                                         "frames": 240, "enhanced": {"frames": enh_frames,
                                                                                     "width": 3840, "height": 2160}}),
                                             encoding="utf-8")
    p.set_step("edit", PJ.DONE, result="PASS", run_dir=str(p.folder / "edit"))
    p.update(enhance={"state": state})
    return PJ.load(p.folder)


def _clips(xml: Path) -> dict:
    root = ET.parse(str(xml)).getroot()
    out = {}
    for ci in root.iter("clipitem"):
        f = ci.find("file")
        sc = [v.text for par in ci.iter("parameter") if par.findtext("parameterid") == "scale" for v in par.iter("value")]
        out[ci.get("id")] = (f.get("id"), int(ci.findtext("in")), int(ci.findtext("out")), sc[0] if sc else None,
                             f.findtext("pathurl"))
    return out


def test_the_clips_inside_the_piece_play_the_enhanced_picture_at_the_same_moment(tmp_path):
    p = _project(tmp_path, [(120, 150), (200, 260), (330, 400)])      # the piece: RAW frames 100-340 (10 s)
    r = ES.swap_in(p.folder, do_check=False)
    assert r["ok"] and r["swapped"] == 2 and r["kept"] == 1, r
    c = _clips(p.folder / "edit" / "1_edit.xml")
    assert c["v0"][:3] == ("file-enhanced", 20, 50) and float(c["v0"][3]) == pytest.approx(43.0)   # scale halved
    assert c["v1"][:3] == ("file-enhanced", 100, 160)
    assert c["v0"][4].endswith("/edit/extras/media/raw_enhanced.mp4")                  # next to the RAW it plays
    assert c["v2"][:3] == ("file-raw", 330, 400) and c["v2"][4] is not None             # outside: the RAW, defined
    assert [c[f"a{i}"][:3] for i in range(3)] == [("file-raw-sound", 120, 150), ("file-raw-sound", 200, 260),
                                                  ("file-raw-sound", 330, 400)]          # A1 untouched
    assert (p.folder / "edit" / "extras" / ES.NORMAL_XML).is_file()
    assert (p.folder / "edit" / "extras" / "media" / E.PROJECT_FILE).is_file()
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["swapped"] and rec["swap"]["clips"] == 2 and "2 of 3 V1 clips" in rec["text"]
    again = ES.swap_in(p.folder, do_check=False)
    assert again["ok"] and again.get("already")


def test_an_edit_that_plays_only_the_scene_is_put_back_on_the_movies_frames_first(tmp_path):
    groups = [{"k0": 80, "k1": 500, "offset": 0, "pts0": 0, "dts_end": None}]       # raw.mov = movie frames 80-500
    p = _project(tmp_path, [(40, 70)], groups=groups)                                  # = movie frames 120-150
    r = ES.swap_in(p.folder, do_check=False)
    assert r["ok"], r
    assert _clips(p.folder / "edit" / "1_edit.xml")["v0"][:3] == ("file-enhanced", 20, 50)


def test_all_v1_clips_swapped_the_raws_definition_moves_to_a1(tmp_path):
    p = _project(tmp_path, [(120, 150)])
    xml = p.folder / "edit" / "1_edit.xml"
    t = xml.read_text(encoding="utf-8").replace("file-raw-sound", "file-raw")          # A1 plays the RAW's sound
    t = t.replace("<name>raw_stereo.wav</name>", "<name>raw.mp4</name>").replace("raw_stereo.wav", "raw.mp4")
    xml.write_text(t, encoding="utf-8")
    assert ES.swap_in(p.folder, do_check=False)["ok"]
    c = _clips(xml)
    assert c["v0"][0] == "file-enhanced" and c["a0"][0] == "file-raw" and c["a0"][4] is not None


def test_a_speed_shift_that_is_not_whole_keeps_the_normal_raw(tmp_path):
    p = _project(tmp_path, [(120, 150), (200, 240)], speeds={1: 125})                 # 1.25 x: 100 frames = 80 steps
    r = ES.swap_in(p.folder, do_check=False)
    c = _clips(p.folder / "edit" / "1_edit.xml")
    assert c["v0"][0] == "file-enhanced"
    assert c["v1"][:3] == ("file-enhanced", 120, 160) or c["v1"][0] == "file-raw"     # 200*1.25=250 -> 150/1.25
    assert r["ok"]


def test_nothing_changes_before_the_piece_is_back_or_the_edit_is_finished(tmp_path):
    p = _project(tmp_path / "a", [(120, 150)], state="sent")
    xml = p.folder / "edit" / "1_edit.xml"
    before = xml.read_bytes()
    assert not ES.swap_in(p.folder, do_check=False)["ok"] and xml.read_bytes() == before
    p = _project(tmp_path / "b", [(120, 150)])
    p.set_step("edit", PJ.RUNNING)
    assert not ES.swap_in(p.folder, do_check=False)["ok"]


def test_a_failed_check_changes_nothing(tmp_path, monkeypatch):
    p = _project(tmp_path, [(120, 150)])
    xml = p.folder / "edit" / "1_edit.xml"
    before = xml.read_bytes()
    monkeypatch.setattr(ES, "check", lambda *a, **k: {"ok": False, "rows": [], "why": "not the same picture"})
    r = ES.swap_in(p.folder)
    assert not r["ok"] and "line up" in r["why"] and xml.read_bytes() == before
    assert not (p.folder / "edit" / "extras" / ES.NORMAL_XML).exists()


def test_the_edit_is_matched_on_the_normal_raw_even_with_the_piece_back(tmp_path, monkeypatch):
    p = _project(tmp_path, [(120, 150)])
    comp = p.folder / "comp.mp4"
    comp.write_bytes(b"c")
    raw = p.folder / "The.Show.S04E01.mp4"
    raw.write_bytes(b"r")
    p.update(competitor={"file": comp.name}, raw={"file": raw.name})
    monkeypatch.setenv(ES.MATCH_ENV, "1")
    assert flow.edit_job(PJ.load(p.folder)).raw == raw
    monkeypatch.setenv(ES.MATCH_ENV, "0")                                             # run 20: the piece is the RAW
    assert flow.edit_job(PJ.load(p.folder)).raw == p.folder / E.PROJECT_SUB / E.PROJECT_FILE


def test_to_movie_reads_the_scenes_groups():
    g = [{"k0": 100, "k1": 200, "offset": 0}, {"k0": 900, "k1": 950, "offset": 100}]
    assert ES.to_movie(10, g) == 110 and ES.to_movie(120, g) == 920 and ES.to_movie(400, g) is None
    assert ES.to_movie(7, []) == 7


def test_an_edit_matched_on_the_enhanced_piece_itself_is_never_swapped(tmp_path):
    p = _project(tmp_path, [(120, 150)])                                               # run 20: raw.mp4 IS the piece
    media = p.folder / "edit" / "extras" / "media" / "raw.mp4"
    media.write_bytes((p.folder / E.PROJECT_SUB / E.PROJECT_FILE).read_bytes())
    xml = p.folder / "edit" / "1_edit.xml"
    before = xml.read_bytes()
    r = ES.swap_in(p.folder, do_check=False)
    assert not r["ok"] and "matched on the enhanced piece" in r["why"] and xml.read_bytes() == before
    p2 = _project(tmp_path / "b", [(120, 150)], fps="24000/1001")                     # its RAW is not the piece's
    assert not ES.swap_in(p2.folder, do_check=False)["ok"]


def test_the_check_wants_the_mapped_frame_to_be_the_best_one(monkeypatch):
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(3)
    pics = {k: rng.integers(0, 255, (90, 160)).astype(np.uint8) for k in range(10)}

    def gray(path, fps, frames):                         # normal frame f <-> enhanced frame 60 * f / 24 (shot k)
        if "raw" in str(path):
            return {f: pics[f % 10] for f in frames}
        return {e: pics[int(round(e * 24 / 60)) % 10] for e in frames}
    monkeypatch.setattr(ES, "_gray", gray)
    from fractions import Fraction
    ok = ES.check(Path("raw.mov"), Fraction(24), Path("enh.mp4"), [(5, Fraction(5, 24) + 0)])
    assert ok["ok"], ok
    bad = ES.check(Path("raw.mov"), Fraction(24), Path("enh.mp4"), [(5, Fraction(7, 24))])   # 2 frames off
    assert not bad["ok"] and "does not show" in bad["why"]


def test_the_zip_to_send_waits_for_the_topaz_picture(tmp_path, monkeypatch):
    from mr_app.core import share_zip as Z
    made = []
    monkeypatch.setattr(Z, "make_later", lambda project, run=None, *a, **k: made.append(Path(project)))
    p = _project(tmp_path, [(120, 150)], state="sent")                                # Topaz still at it
    flow.record_edit(PJ.load(p.folder), {"label": "PASS", "run_dir": str(p.folder / "edit")})
    assert made == []                                                                  # no zip of the normal picture
    p.update(enhance={"state": "used"})                                               # the piece is back
    assert ES.swap_in(p.folder, do_check=False)["ok"]
    assert made == [p.folder]                                                          # the zip: the cut Topaz version
    q = _project(tmp_path / "b", [(120, 150)], state="skipped")                       # no Topaz: zipped at once
    flow.record_edit(PJ.load(q.folder), {"label": "PASS", "run_dir": str(q.folder / "edit")})
    assert made[-1] == q.folder


def test_the_position_is_halved_too():
    """7 Nov: Premiere reads Position in the clip's own pixels -- the 2x enhanced picture with the same numbers sat
    twice as far off (a black band over the picture)."""
    import xml.etree.ElementTree as ET
    par = ET.fromstring("<parameter><parameterid>center</parameterid><value><horiz>0.0738493</horiz>"
                        "<vert>0.162963</vert></value><keyframe><when>0</when><value><horiz>-0.2</horiz>"
                        "<vert>0.1</vert></value></keyframe></parameter>")
    ES._half_center(par)
    got = [(float(v.findtext("horiz")), float(v.findtext("vert"))) for v in par.iter("value")]
    assert got == [pytest.approx((0.0369247, 0.0814815)), pytest.approx((-0.1, 0.05))]


def test_a_60_fps_sequence_plays_the_nearest_enhanced_frame(tmp_path):
    """7 Nov ("it should always be 60"): the edit is made in a 60 fps sequence -- its clips count 60ths of a second
    of the 24 fps RAW, so the piece's start is rarely a whole step: the nearest 60 fps frame of the enhanced piece."""
    p = _project(tmp_path, [(300, 400)], start=101)                   # 60ths: RAW frames 120-160
    xml = p.folder / "edit" / "1_edit.xml"
    root = ET.parse(str(xml)).getroot()
    for ci in root.iter("clipitem"):
        if ci.get("id") == "v0":
            ci.find("rate").find("timebase").text = "60"              # (the clip's own rate; the file stays 24)
    ET.ElementTree(root).write(str(xml), encoding="utf-8")
    r = ES.swap_in(p.folder, do_check=False)
    assert r["ok"] and r["swapped"] == 1, r
    c = _clips(xml)
    assert c["v0"][:3] == ("file-enhanced", 48, 148)                  # (120 - 101) / 0.4 = 47.5 -> 48, same length


def test_an_edit_that_sends_its_piece_to_topaz_is_made_at_60(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv(ES.MATCH_ENV, "1")
    p = PJ.create(tmp_path, name="x")
    job = SimpleNamespace(project=p.folder)
    assert not ES.wants_60(job)
    p.update(enhance={"state": "sent"})
    assert ES.wants_60(job)
    monkeypatch.setenv(ES.MATCH_ENV, "0")
    assert not ES.wants_60(job)
