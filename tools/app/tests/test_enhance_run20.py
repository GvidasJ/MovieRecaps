"""Run 20: the scene's piece enhanced on the laptop (core.enhance) -- with a fake Topaz (MOVIERECAPS_FAKE_TOPAZ: a plain
2x scale + 60 fps with ffmpeg): the PC's piece (the sound check finds the scene, at most 2 minutes, lossless), the
laptop taking it and enhancing it, the PC checking it and making it the edit's RAW; the 1-hour fallback and a late
piece; a broken enhanced file; two computers taking the same piece; the settings."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from mr_app.core import enhance as E
from mr_app.core import projects as P
from mr_app.core import settings as S


def _ffmpeg() -> str:
    from match_cuts.common import ffmpeg_bin
    return ffmpeg_bin()


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    """A 3:20 'movie' (320x180, 23.976 fps, a keyframe every 2 s, pink noise) and a 10 s competitor cut from it at
    1:50 (its sound: the movie's)."""
    d = tmp_path_factory.mktemp("enh_pair")
    raw, comp = d / "movie.mp4", d / "comp.mp4"
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.4:seed=7",
                    "-t", "200", "-c:v", "libx264", "-preset", "ultrafast", "-bf", "2", "-g", "48",
                    "-keyint_min", "48", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    str(raw)], check=True)
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-ss", "110", "-i", str(raw), "-t", "10", "-vf",
                    "crop=180:180,scale=360:640", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(comp)], check=True)
    return SimpleNamespace(raw=raw, comp=comp)


@pytest.fixture
def fake_topaz(monkeypatch):
    monkeypatch.setenv(E.FAKE_ENV, "1")


def _project(root: Path, pair, name: str = "2026-10-29_Test scene") -> P.Project:
    p = P.create(root, competitor_url=None, raw_url=None, raw_file=str(pair.raw), name=name, folder=name)
    for role, src in (("competitor", pair.comp), ("raw", pair.raw)):
        dst = p.folder / src.name
        if not dst.exists():
            dst.hardlink_to(src)
        p.update(**{role: {"file": src.name}})
    return P.load(p.folder)


def _send(p: P.Project, share: Path, pair) -> dict:
    """The PC's stage (download_tool.StageThread._enhance) without the window."""
    info = E.make_piece(pair.comp, pair.raw, p.folder.name, share, project=p.folder)
    p.update(enhance={"state": "sent", "name": E.safe_name(p.folder.name), "folder": str(share), "sent": time.time(),
                      "piece": info})
    return info


# ---------------------------------------------------------------------------------------------------------------------
# where the piece goes (at most 2 minutes, the scene in the middle, keyframe to keyframe)
# ---------------------------------------------------------------------------------------------------------------------

def _facts(minutes: float = 100.0, gop: int = 48):
    from match_cuts.excerpt import Movie
    fps = Fraction(24000, 1001)
    n = int(minutes * 60 * float(fps))
    return Movie(path="m.mp4", fps=str(fps), tb="1/24000", frames=n, duration=n / float(fps),
                 keyframes=[(k, k * 1001, k * 1001 - 2002) for k in range(0, n, gop)])


def test_the_piece_is_at_most_two_minutes_with_the_scene_in_the_middle():
    m = _facts()
    f = float(m.rate)
    p = E.plan(m, [1800.0, 1810.0, 1820.0], 3.0)
    a, b = p.k0 / f, p.k1 / f
    assert p.k0 % 48 == 0 and p.k1 % 48 == 0 and p.dts_end == p.k1 * 1001 - 2002
    assert b - a <= E.MAX_PIECE_S and a <= 1800.0 and b >= 1823.0
    mid, scene_mid = (a + b) / 2, (1800.0 + 1823.0) / 2
    assert abs(mid - scene_mid) < 2.5                       # (keyframes every 2 s)
    assert abs(a - (1800.0 - (E.MAX_PIECE_S - 23.0) / 2)) < 2.1          # the keyframe nearest the wanted start


def test_a_scene_longer_than_two_minutes_or_far_apart_places_are_not_enhanced():
    m = _facts()
    with pytest.raises(E.EnhanceError, match="long"):
        E.plan(m, [1000.0, 1200.0], 3.0)
    with pytest.raises(E.EnhanceError, match="keyframes"):
        E.plan(_facts(gop=24 * 200), [1800.0], 3.0)       # (a keyframe every 200 s: no piece of 2 min fits)


def test_near_the_movies_ends_the_piece_stays_inside_it():
    m = _facts(minutes=10.0)
    f = float(m.rate)
    p = E.plan(m, [5.0], 3.0)
    assert p.k0 == 0 and p.k1 / f <= E.MAX_PIECE_S + 1e-6 and p.k1 / f >= 8.0
    p = E.plan(m, [595.0], 3.0)
    assert p.k1 == m.frames and p.dts_end is None and (p.k1 - p.k0) / f <= E.MAX_PIECE_S
    short = _facts(minutes=1.5)                              # a RAW under 2 minutes: all of it
    p = E.plan(short, [30.0], 3.0)
    assert (p.k0, p.k1) == (0, short.frames)


# ---------------------------------------------------------------------------------------------------------------------
# the whole flow: the PC's piece -> the laptop's Topaz -> the PC's edit RAW
# ---------------------------------------------------------------------------------------------------------------------

def test_the_whole_flow_the_pc_and_the_laptop(tmp_path, pair, fake_topaz):
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    info = _send(p, share, pair)
    nm = E.names(share, p.folder.name)
    # the PC's piece: <= 2 min, the scene (1:50-2:00) in it, from the RAW's own frames, its json written last
    assert nm.piece.is_file() and nm.info.is_file()
    assert info["duration"] <= E.MAX_PIECE_S and info["start_s"] <= 110.5 and \
        info["start_s"] + info["duration"] >= 120.0
    assert info["width"] == 320 and info["height"] == 180 and Fraction(info["fps"]) == Fraction(24000, 1001)
    assert all(r["ssim"] > 0.999 for r in info["check"])
    assert json.loads(nm.info.read_text(encoding="utf-8"))["project_folder"] == str(p.folder)
    assert E.watch(p.folder)["state"] == "progress" and E.status(share, p.folder.name).state == "waiting"
    # the laptop: takes it, Topaz (fake), checks it, renames it
    seen = []
    w = E.Worker(share, seen.append)
    job = w.once()
    assert job is not None and job.state == "done", job.why
    assert nm.enhanced.is_file() and not nm.part.exists() and not nm.lock.exists()
    assert any(j.state == "done" for j in seen)
    assert w.once() is None                                  # (nothing more to take)
    # the PC: checked, copied into the project, the share's files gone, the edit's RAW
    r = E.watch(p.folder)
    assert r["state"] == "used" and "640x360" in r["text"]
    assert not any(f.exists() for f in nm.all())
    p = P.load(p.folder)
    raw = E.edit_raw(p.data, p.folder)
    assert raw == p.folder / "enhance" / "raw_enhanced.mp4"
    chk = E.check_enhanced(raw, info)
    assert chk["ok"] and chk["width"] == 640 and chk["fps"] == "60"
    from mr_app.core import flow
    j = flow.edit_job(p)
    assert j.raw == raw and j.roles_chosen and j.roles_how == "form"
    assert p.file("raw") == p.folder / pair.raw.name           # (the project's own RAW stays as it was)


def test_the_progress_line_and_the_pcs_view_while_the_laptop_works(tmp_path, pair):
    share = tmp_path / "Enhance"
    nm = E.names(share, "x")
    share.mkdir()
    nm.piece.write_bytes(b"x")
    nm.info.write_text("{}", encoding="utf-8")
    tok = E.take(nm, "LAPTOP")
    assert tok and E.beat(nm, tok, share=0.34, left_s=700.0)
    b = E.status(share, "x")
    assert b.state == "enhancing" and "34 %" in b.text and "about 12 min left" in b.text
    job = E.Job("Office Flirt", share=0.34, left_s=700.0)
    assert job.line() == "Enhancing Office Flirt on this laptop · 34 % · about 12 min left"


# ---------------------------------------------------------------------------------------------------------------------
# the laptop does not answer / answers late / fails
# ---------------------------------------------------------------------------------------------------------------------

def test_no_answer_in_an_hour_then_a_late_piece_is_used_only_when_you_say(tmp_path, pair, fake_topaz):
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    _send(p, share, pair)
    sent = P.load(p.folder).data["enhance"]["sent"]
    assert E.watch(p.folder, now=sent + 1800)["state"] == "progress"
    r = E.watch(p.folder, now=sent + E.TIMEOUT_S + 1)
    assert r == {"state": "late", "why": E.TIMEOUT_WHY} and E.is_late("not enhanced: " + r["why"])
    assert E.edit_raw(P.load(p.folder).data, p.folder) is None          # the normal RAW
    assert E.watch(p.folder) == {}                                       # nothing came yet
    assert E.Worker(share).once().state == "done"                        # ... then the laptop's piece comes, late
    assert E.watch(p.folder) == {"state": "late_ready"}                  # not used by itself
    assert E.edit_raw(P.load(p.folder).data, p.folder) is None
    u = E.use_late(p.folder)                                             # "Use the enhanced piece"
    assert u["ok"] and "came late" in u["text"]
    assert E.edit_raw(P.load(p.folder).data, p.folder) is not None
    assert not any(f.exists() for f in E.names(share, p.folder.name).all())


def test_a_laptop_still_working_after_an_hour_is_waited_for_a_while(tmp_path, pair):
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    _send(p, share, pair)
    nm = E.names(share, p.folder.name)
    tok = E.take(nm, "LAPTOP")
    E.beat(nm, tok, share=0.6)
    import os
    sent = P.load(p.folder).data["enhance"]["sent"]
    beat = sent + E.TIMEOUT_S + 30                           # (its lock rewritten a moment ago, an hour on)
    os.utime(nm.lock, (beat, beat))
    r = E.watch(p.folder, now=sent + E.TIMEOUT_S + 60)
    assert r["state"] == "progress" and "still working" in r["text"]
    beat = sent + E.MAX_WAIT_S + 30
    os.utime(nm.lock, (beat, beat))
    r = E.watch(p.folder, now=sent + E.MAX_WAIT_S + 60)
    assert r["state"] == "late" and E.STILL_WHY in r["why"] and E.is_late(r["why"])


def test_the_laptops_failure_says_why_and_the_normal_raw_is_used(tmp_path, pair):
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    _send(p, share, pair)
    nm = E.names(share, p.folder.name)
    nm.failed.write_text("the graphics card ran out of memory\n\n(the laptop X)\n", encoding="utf-8")
    r = E.watch(p.folder)
    assert r["state"] == "failed" and r["why"] == "the laptop could not enhance it: the graphics card ran out of memory"
    assert not any(f.exists() for f in nm.all()) and E.edit_raw(P.load(p.folder).data, p.folder) is None


def test_topaz_failing_on_the_laptop_writes_the_failure_file(tmp_path, pair, monkeypatch):
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    _send(p, share, pair)
    monkeypatch.setattr(E, "find_topaz", lambda: E.Topaz(why="Topaz Video AI is not installed on this computer"))
    job = E.Worker(share).once()
    nm = E.names(share, p.folder.name)
    assert job.state == "failed" and "not installed" in job.why
    assert "not installed" in nm.failed.read_text(encoding="utf-8") and not nm.lock.exists()
    assert "not installed" in E.watch(p.folder)["why"]


def test_a_broken_enhanced_piece_is_not_used(tmp_path, pair):
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    _send(p, share, pair)
    nm = E.names(share, p.folder.name)
    nm.enhanced.write_bytes(nm.piece.read_bytes())          # the piece itself: not 2x, not 60 fps
    r = E.watch(p.folder)
    assert r["state"] == "failed" and "not twice the piece's size" in r["why"]
    assert E.edit_raw(P.load(p.folder).data, p.folder) is None and not nm.enhanced.exists()
    info = P.load(p.folder).data["enhance"]["piece"]
    bad = tmp_path / "half.mp4"                              # 2x and 60 fps, but cut short / not decodable
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60", "-t",
                    "3", "-c:v", "libx264", "-preset", "ultrafast", str(bad)], check=True)
    assert "long" in E.check_enhanced(bad, info)["why"]
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\0" * 5000)
    assert not E.check_enhanced(junk, info)["ok"]


# ---------------------------------------------------------------------------------------------------------------------
# two computers, one piece
# ---------------------------------------------------------------------------------------------------------------------

def test_two_computers_never_take_the_same_piece(tmp_path):
    share = tmp_path / "Enhance"
    share.mkdir()
    nm = E.names(share, "x")
    nm.piece.write_bytes(b"x")
    nm.info.write_text("{}", encoding="utf-8")
    got, gate = [], threading.Barrier(8)

    def grab(i):
        gate.wait()
        got.append(E.take(nm, f"computer{i}"))
    ts = [threading.Thread(target=grab, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len([t for t in got if t]) == 1
    assert E.waiting_pieces(share) == []                    # taken: nobody else sees it
    assert E.take(nm, "laptop2") is None


def test_a_lock_left_by_a_computer_that_stopped_is_taken_over(tmp_path):
    import os
    share = tmp_path / "Enhance"
    share.mkdir()
    nm = E.names(share, "x")
    nm.piece.write_bytes(b"x")
    nm.info.write_text("{}", encoding="utf-8")
    assert E.take(nm, "laptop")
    old = time.time() - E.LOCK_STALE_S - 60
    os.utime(nm.lock, (old, old))
    assert [n.name for n in E.waiting_pieces(share)] == ["x"]
    tok = E.take(nm, "laptop2")
    assert tok and json.loads(nm.lock.read_text(encoding="utf-8"))["computer"] == "laptop2"


def test_a_piece_without_its_json_or_with_a_result_is_not_taken(tmp_path):
    share = tmp_path / "Enhance"
    share.mkdir()
    a, b, c = (E.names(share, n) for n in "abc")
    for nm in (a, b, c):
        nm.piece.write_bytes(b"x")
    b.info.write_text("{}", encoding="utf-8")
    c.info.write_text("{}", encoding="utf-8")
    c.failed.write_text("no", encoding="utf-8")
    assert [n.name for n in E.waiting_pieces(share)] == ["b"]


# ---------------------------------------------------------------------------------------------------------------------
# Topaz, the settings
# ---------------------------------------------------------------------------------------------------------------------

def test_the_topaz_command_has_the_users_settings_exactly():
    tz = E.Topaz(Path(r"C:\Topaz\ffmpeg.exe"), Path(r"C:\Topaz\models"), Path(r"C:\Topaz"))
    cmd = E.topaz_command(tz, "in.mp4", "out.mp4")
    vf = cmd[cmd.index("-vf") + 1]
    up, fi = vf.split(",")
    assert up.startswith("tvai_up=model=prob-3:scale=2:") and fi.startswith("tvai_fi=model=apo-8:")
    for k, v in (("compression", "0.8"), ("details", "0.8"), ("blur", "0.6"), ("noise", "0.8"), ("halo", "0.1"),
                 ("preblur", "-0.6"), ("prenoise", "0"), ("blend", "0.1"), ("grain", "0")):
        assert f":{k}={v}:" in up + ":"
    assert ":slowmo=1:" in fi and ":fps=60:" in fi and ":rdt=-1e-06:" in fi
    assert "tvai_stb" not in vf
    for a, b in (("-c:v", "hevc_nvenc"), ("-profile:v", "main10"), ("-rc", "cbr"), ("-b:v", "180M"),
                 ("-c:a", "copy")):
        assert cmd[cmd.index(a) + 1] == b
    assert cmd[-1] == "out.mp4" and cmd[cmd.index("-f") + 1] == "mp4"
    env = E.topaz_env(tz)
    assert env["TVAI_MODEL_DIR"] == env["TVAI_MODEL_DATA_DIR"] == r"C:\Topaz\models"


def test_test_topaz_with_the_fake_one(tmp_path, fake_topaz):
    r = E.test_topaz(tmp_path)
    assert r["ok"], r["text"]
    assert "1920x1080 at 60 fps" in r["text"] and not list(tmp_path.glob("*.mp4"))


def test_no_topaz_says_so(monkeypatch, tmp_path):
    monkeypatch.delenv(E.FAKE_ENV, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "pf"))
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    tz = E.find_topaz()
    assert not tz.found and "not installed" in tz.line()


def test_the_switches_follow_the_computer_and_the_folder_is_a_setting(monkeypatch):
    monkeypatch.delenv(E.ENHANCE_ENV, raising=False)
    pc, laptop = S.Settings(machine="pc"), S.Settings(machine="laptop")
    assert E.sends(pc) and not E.enhances(pc)
    assert E.enhances(laptop) and not E.sends(laptop)
    assert E.share_dir(pc) == Path(r"D:\Shared\Enhance")
    assert E.share_dir(laptop) == Path(r"\\192.168.101.11\Shared\Enhance")
    assert E.share_dir(S.Settings(machine="laptop", enhance_dir=r"C:\T\enhance_test")) == Path(r"C:\T\enhance_test")
    assert not E.sends(S.Settings(machine="pc", enhance_send=False))
    assert E.enhances(S.Settings(machine="pc", enhance_topaz=True))
    monkeypatch.setenv(E.ENHANCE_ENV, "0")                  # (the app's tests: off unless the settings say)
    assert not E.sends(pc) and not E.enhances(laptop)
    s = S.Settings.from_dict({"enhance_send": "yes", "enhance_topaz": None, "enhance_dir": "//192.168.101.11/Shared/E"})
    assert s.enhance_send is None and s.enhance_topaz is None and s.enhance_dir == r"\\192.168.101.11\Shared\E"
    assert S.Settings.from_dict(s.to_dict()) == s


def test_the_row_shows_the_enhance_step_before_the_edit_and_keeps_it():
    from mr_app.core import rows_keep as RK
    from mr_app.tools import download_tool as DT
    it = DT.Item("https://youtu.be/abcdefghijk", enhance=True)
    assert [k for k, _ in it.step_list()] == ["download", "convert", "template", "enhance", "edit"]
    assert "enhance" in it.steps
    assert [k for k, _ in DT.Item("x").step_list()] == ["download", "convert", "template", "edit"]
    d = RK.item_to_dict(it)
    assert d["enhance"] is True and RK.item_fields(d)["kw"]["enhance"] is True


# ---------------------------------------------------------------------------------------------------------------------
# the row (no screen): it waits for the laptop, then its edit starts by itself on the enhanced piece
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Ctx:
    def __init__(self):
        self.pages, self.notes, self.strip = {}, [], None

    def notify(self, title, text):
        self.notes.append((title, text))

    def set_strip_queue(self, n, running):
        self.strip = (n, running)


def _row(qapp, tmp_path, pair, monkeypatch, sent_ago: float = 0.0):
    from mr_app.tools import download_tool as D
    from mr_app.tools.match_cuts_tool import MatchCutsPage
    ctx = _Ctx()
    mc = MatchCutsPage(ctx)
    starts = []
    monkeypatch.setattr(mc, "start", lambda confirm=True: starts.append(confirm))
    ctx.pages["match_cuts"] = mc
    page = D.DownloadPage(ctx)
    share = tmp_path / "Enhance"
    p = _project(tmp_path / "projects", pair)
    _send(p, share, pair)
    if sent_ago:
        p.update(enhance={"sent": time.time() - sent_ago})
    it = D.Item("https://youtu.be/abcdefghijk", title="My title", project=p.folder, name=p.folder.name,
                state=D.READY, enhance=True)
    page.items.append(it)
    page.rows[it.id] = D.VideoRow(page, it)
    page._enhance_said(it, P.load(p.folder).data["enhance"])                 # noqa: SLF001 - the stage's end
    return D, page, mc, it, share, p, starts


def _wait(qapp, cond, page, s: float = 30.0):
    t0 = time.perf_counter()
    while not cond() and time.perf_counter() - t0 < s:
        page._enhance_wanted = 0.0                                           # noqa: SLF001 - (no 10 s wait)
        page._poll()                                                         # noqa: SLF001
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def test_the_row_waits_for_the_laptop_then_the_edit_runs_on_the_enhanced_piece(qapp, tmp_path, pair, monkeypatch,
                                                                               fake_topaz):
    D, page, mc, it, share, p, starts = _row(qapp, tmp_path, pair, monkeypatch)
    row = page.rows[it.id]
    assert it.steps["enhance"].state == D.S_RUN
    if row.pipe is not None:                                                 # (the Mono look's line of dots)
        assert row.pipe.text() == "○ DOWNLOAD ─── ○ CONVERT ─── ○ TEMPLATE ─── ◉ ENHANCE ─── ○ EDIT ─── ○ DONE"
    page.send_to_edit(it, start=True)
    assert it.gate == D.G_ENHANCE and not mc.jobs                            # the edit waits
    assert row._pill()[0] == "waiting for the laptop"                        # noqa: SLF001
    assert "with the laptop" in page.now_text(it)
    E.Worker(share).once()                                                   # the laptop
    assert _wait(qapp, lambda: bool(mc.jobs) and bool(starts), page)
    assert it.steps["enhance"].state == D.S_DONE and "enhanced on the laptop" in it.steps["enhance"].text
    assert mc.jobs[-1].raw == p.folder / "enhance" / "raw_enhanced.mp4" and it.gate == ""


def test_no_answer_in_an_hour_the_row_makes_the_edit_with_the_normal_raw(qapp, tmp_path, pair, monkeypatch):
    D, page, mc, it, share, p, starts = _row(qapp, tmp_path, pair, monkeypatch, sent_ago=E.TIMEOUT_S + 5)
    page.send_to_edit(it, start=True)
    assert _wait(qapp, lambda: bool(mc.jobs) and bool(starts), page)
    assert it.steps["enhance"].state == D.S_NOTE
    assert it.steps["enhance"].text == "not enhanced: the laptop did not answer in 1 hour"
    assert mc.jobs[-1].raw == p.folder / pair.raw.name                       # the normal RAW
    it.state = D.DONE                                                        # (its edit made)
    monkeypatch.setenv(E.FAKE_ENV, "1")
    E.Worker(share).once()                                                   # the laptop's piece comes late ...
    assert _wait(qapp, lambda: it.enhance_late, page)
    assert page.rows[it.id].b_enh.isVisibleTo(page.rows[it.id])              # ... "Use the enhanced piece"
    n = len(mc.jobs)
    page.use_enhanced(it)
    assert _wait(qapp, lambda: len(mc.jobs) > n, page)
    assert mc.jobs[-1].raw == p.folder / "enhance" / "raw_enhanced.mp4"
    assert it.steps["enhance"].state == D.S_DONE and "came late" in it.steps["enhance"].text


def test_after_the_conversion_the_piece_is_made_first_then_the_row_goes_on(qapp, monkeypatch):
    from mr_app.tools import download_tool as D
    started = []

    class FakeThread:
        def __init__(self, item, stages=("download",), default_template=False):
            self.item, self.stages, self.stage = item, stages, stages[0]
            self.named = self.moved = self.progress = self.done = SimpleNamespace(connect=lambda f: None)

        def start(self):
            started.append(self.stages)
    monkeypatch.setattr(D, "StageThread", FakeThread)
    page = D.DownloadPage(_Ctx())
    page.auto.setChecked(False)
    it = D.Item("https://youtu.be/abcdefghijk", title="My title", enhance=True)
    page.items.append(it)
    page.rows[it.id] = D.VideoRow(page, it)
    page.after(it, ("convert",))
    assert started == [("enhance",)]
    page.current = None
    page._enhance_said(it, {"state": "skipped", "why": "the sound check could not find the scene in the RAW"})
    page.after(it, ("enhance",))
    assert it.gate == D.G_SEND                                                # (Automatic off: as after a conversion)
    assert it.steps["enhance"].state == D.S_NOTE
    assert it.steps["enhance"].text == "not enhanced: the sound check could not find the scene in the RAW"
    page.send_to_edit(it)                                                     # a skipped piece never holds the edit
    assert it.gate != D.G_ENHANCE
