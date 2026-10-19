"""Day 12, sections 4 and 5 (the Match cuts queue): which file is the RAW is always your choice -- the pair line on
every row, ⇄ Swap before and during a run (the run starts again with the same files, their roles switched in
project.json, nothing copied again), the amber "Check which is the RAW" row for two videos within 30 % whose names
don't say (never started by itself; your pick remembered and run as "Run anyway"); and newest on top -- running rows
first, then waiting rows newest first, then finished rows newest first -- while the run order stays as added or
dragged; rows glide to their new places (positions in between, never a jump); the board's newest card on top with
the pinned ones above.

Run: python -m pytest tools/app/tests/test_queue_day12.py"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import flow  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.tools import match_cuts_tool as M  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402

LENGTHS = {"comp": 45.0, "raw": 47.0}


def pump_until(cond, timeout_s: float = 8.0) -> bool:
    app = QtWidgets.QApplication.instance()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(cond())


def seconds(p) -> float:
    """The test videos' lengths by name: '...short...' 58 s, '...episode...' 22:41, others from LENGTHS by stem."""
    n = Path(p).stem.lower()
    if "short" in n:
        return 58.0
    if "episode" in n:
        return 1361.0
    for k, v in LENGTHS.items():
        if k in n:
            return v
    return 46.0


class FakeThread:
    """JobThread without the tool: records what it was asked to run; ``finish(label)`` ends it."""
    made: list = []
    page = None

    def __init__(self, job, converted, out_dir):
        from PySide6.QtCore import QObject, Signal

        class Sig(QObject):
            progress = Signal(int, str, float)
            done = Signal(int, dict)
        self._sig = Sig()
        self.progress, self.done = self._sig.progress, self._sig.done
        self.job = job
        self.files = (Path(job.competitor), Path(job.raw))
        self.stopped = False
        cur = FakeThread.page                                # a page left over from an earlier test (its background
        if cur is None or any(x is job for x in cur.jobs):  # start check ending late) is not this test's
            FakeThread.made.append(self)

    def start(self) -> None:
        pass

    def wait(self, _ms=0) -> bool:
        return True

    def stop(self) -> None:
        self.stopped = True
        self.done.emit(self.job.id, {"label": "STOPPED"})

    def finish(self, label: str = "PASS") -> None:
        self.done.emit(self.job.id, {"label": label, "seconds": 3.0})


@pytest.fixture()
def page(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)                                   # no glides: the tests look at the end states
    toasts = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    monkeypatch.setattr(M, "seconds_of", seconds)
    FakeThread.made = []
    monkeypatch.setattr(M, "JobThread", FakeThread)
    p = M.MatchCutsPage(ctx)
    FakeThread.page = p
    p.toasts = toasts
    p.resize(900, 1200)
    yield p
    FakeThread.page = None
    Mo.set_enabled(True)
    p.deleteLater()
    app.processEvents()


def files(tmp_path, comp: str, raw: str) -> tuple[Path, Path]:
    d = tmp_path / f"{comp}-{raw}"
    d.mkdir(parents=True, exist_ok=True)
    c, r = d / comp, d / raw
    c.write_bytes(b"c")
    r.write_bytes(b"r")
    return c, r


def add(page, tmp_path, name: str, comp: str = "comp.mp4", raw: str = "raw.mp4", lengths: bool = True) -> J.Job:
    c, r = files(tmp_path / name, comp, raw)
    j = J.Job(name, c, r, source=f"folder {name}")          # (not copied into a project: not tested here)
    if lengths:
        j.comp_s, j.raw_s = seconds(c), seconds(r)
    page._added_jobs(J.DropResult([j], None, []))           # noqa: SLF001 - as a drop does
    return j


def project_job(tmp_path, comp: str, raw: str, autostart_root: Path | None = None) -> tuple[J.Job, PJ.Project]:
    """A Download-tab project with its two files (in the project folder) and its edit job (flow.edit_job)."""
    p = PJ.create(tmp_path / "projects", name="clip")
    (p.folder / comp).write_bytes(b"c")
    (p.folder / raw).write_bytes(b"r")
    p.update(competitor={"file": comp}, raw={"file": raw})
    p.set_step("convert", PJ.DONE, competitor={"how": "keep", "file": comp}, raw={"how": "encode", "file": raw})
    return flow.edit_job(p), p


# ---------------------------------------------------------------------------------------------------------------------
# section 4: the pair line, Swap, the amber check, your pick = Run anyway
# ---------------------------------------------------------------------------------------------------------------------

def test_every_row_says_which_file_is_which_with_its_length(page, tmp_path):
    j = add(page, tmp_path, "v1", "short.mp4", "episode.mp4")
    assert M.pair_text(j) == "RAW: episode.mp4 (22:41) · COMP: short.mp4 (0:58)"
    row = page.rows[j.id]
    html = row.pair.text()
    assert "episode.mp4" in html and "short.mp4" in html and "22:41" in html and "0:58" in html
    assert html.index("RAW") < html.index("COMP")
    assert "color:#ff7a59;font-weight:600;letter-spacing:1px'>RAW<" in html                 # coral RAW
    assert "color:#5b9cf5;font-weight:600;letter-spacing:1px'>COMP<" in html                # info-blue COMP
    assert row.swap.text() == "⇄ Swap" and row.swap.isVisibleTo(row)


def test_the_lengths_are_read_in_the_background_and_the_row_waits_for_them(page, tmp_path, monkeypatch):
    import threading
    gate = threading.Event()
    seen = []

    def slow(p):
        seen.append(threading.current_thread() is threading.main_thread())
        gate.wait(5)
        return seconds(p)
    monkeypatch.setattr(M, "seconds_of", slow)
    j = add(page, tmp_path, "v2", "short.mp4", "episode.mp4", lengths=False)
    assert j.reading_lengths and "(…)" in M.pair_text(j) and not J.runnable(j)
    gate.set()
    assert pump_until(lambda: not j.reading_lengths)
    assert seen and not any(seen)                           # never on the window's thread
    assert M.pair_text(j) == "RAW: episode.mp4 (22:41) · COMP: short.mp4 (0:58)" and J.runnable(j)


def test_swap_before_the_start_switches_the_roles_and_remembers_them_in_project_json(page, tmp_path):
    job, p = project_job(tmp_path, "my clip.mp4", "the episode.mp4")
    job.comp_s, job.raw_s = 58.0, 1361.0
    page._added_jobs(J.DropResult([job], None, []))         # noqa: SLF001
    row = page.rows[job.id]
    row.swap.click()
    assert job.raw.name == "my clip.mp4" and job.competitor.name == "the episode.mp4" and job.roles_chosen
    assert (job.raw_s, job.comp_s) == (58.0, 1361.0)
    assert M.pair_text(job).startswith("RAW: my clip.mp4 (0:58)")
    assert pump_until(lambda: (PJ.load(p.folder).data.get("roles") or {}).get("raw") == "my clip.mp4")
    d = PJ.load(p.folder).data
    assert d["raw"]["file"] == "my clip.mp4" and d["competitor"]["file"] == "the episode.mp4"
    assert d["steps"]["convert"]["raw"]["file"] == "my clip.mp4"           # what the steps say moved with it
    assert d["roles"]["how"] == "swapped"
    assert ("Swapped — RAW and competitor switched", "ok") in page.toasts
    again = flow.edit_job(PJ.load(p.folder))                               # the next time it is loaded: as chosen
    assert again.raw.name == "my clip.mp4" and again.roles_chosen


def test_swap_during_a_run_stops_it_and_starts_again_with_the_same_files_switched(page, tmp_path, monkeypatch):
    copies = []
    monkeypatch.setattr(J, "project_for_job", lambda *a, **k: copies.append(a), raising=False)
    job, p = project_job(tmp_path, "comp.mp4", "raw.mp4")
    job.comp_s, job.raw_s = 45.0, 47.0
    page._added_jobs(J.DropResult([job], None, []))         # noqa: SLF001
    page.start(confirm=False)
    assert pump_until(lambda: len(FakeThread.made) == 1)
    first = FakeThread.made[0]
    assert job.state == J.RUNNING and first.files == (p.folder / "comp.mp4", p.folder / "raw.mp4")
    page.swap_job(job)
    assert first.stopped                                    # the run was stopped ...
    assert len(FakeThread.made) == 2                        # ... and started again at once
    second = FakeThread.made[1]
    assert second.files == (p.folder / "raw.mp4", p.folder / "comp.mp4")    # the very same files, roles switched
    assert job.state == J.RUNNING and job.take == 2 and job.roles_chosen and not copies     # nothing copied again
    assert pump_until(lambda: (PJ.load(p.folder).data.get("roles") or {}).get("raw") == "comp.mp4")
    d = PJ.load(p.folder).data
    assert d["raw"]["file"] == "comp.mp4" and d["competitor"]["file"] == "raw.mp4"
    second.finish("PASS")
    assert job.state == J.DONE and not page.running


def test_swap_during_a_run_waits_for_the_old_runs_thread_to_end_before_starting_again(page, tmp_path, monkeypatch):
    """The old run releases the project's edit lock last (after its result): the restart waits for its thread to
    end, so it never finds the lock still held by this computer."""
    monkeypatch.setattr(J, "project_for_job", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(M.MatchCutsPage, "RESTART_POLL_MS", 10)
    job, p = project_job(tmp_path, "comp.mp4", "raw.mp4")
    job.comp_s, job.raw_s = 45.0, 47.0
    page._added_jobs(J.DropResult([job], None, []))         # noqa: SLF001
    page.start(confirm=False)
    assert pump_until(lambda: len(FakeThread.made) == 1)
    first = FakeThread.made[0]
    ended = {"yes": False}
    first.isFinished = lambda: ended["yes"]                  # noqa: N815 - its lock is not released yet
    page.swap_job(job)
    assert first.stopped and len(FakeThread.made) == 1      # not started again while the old thread runs
    QtWidgets.QApplication.processEvents()
    assert len(FakeThread.made) == 1
    ended["yes"] = True
    assert pump_until(lambda: len(FakeThread.made) == 2)    # ... then at once
    assert job.state == J.RUNNING and job.take == 2
    FakeThread.made[1].finish("PASS")
    assert job.state == J.DONE


def test_the_right_click_menu_has_swap(page, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    from mr_app.ui import menu as MN
    shown = []

    class Menu:
        def __init__(self, _parent, items):
            self.items = [it for it in items if it]

        def popup(self, _pos):
            shown.append(self)
    monkeypatch.setattr(MN, "FadeMenu", Menu)
    j = add(page, tmp_path, "v3", "short.mp4", "episode.mp4")
    page.rows[j.id].contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5)))
    item = next(it for it in shown[0].items if it[0] == "Swap RAW and competitor")
    item[1]()
    assert j.raw.name == "short.mp4" and j.roles_chosen


def test_about_as_long_and_the_names_dont_say_waits_amber_for_your_choice(page, tmp_path):
    j = add(page, tmp_path, "v4", "clip a.mp4", "clip b.mp4")              # 46 s and 46 s, names don't say
    row = page.rows[j.id]
    assert J.needs_check(j) and row.check.isVisibleTo(row)
    assert row.pill.text() == "Check which is the RAW" and row.pill._to[0] == "#d29922"      # noqa: SLF001
    texts = sorted(b.text() for b in (row.pick_a, row.pick_b))
    assert texts == ["clip a.mp4 (0:46) is the RAW", "clip b.mp4 (0:46) is the RAW"]
    assert row.runs_text() == "waits for your choice" and "WAITS FOR YOUR CHOICE" in row.slate.text()
    page.start()                                            # Start pressed: it still does not run
    assert not FakeThread.made and not page.running
    assert "1 to check" in page.count.text()


def test_the_30_percent_rule_and_the_names():
    def job(c, r, cs, rs):
        j = J.Job("x", Path(c), Path(r))
        j.comp_s, j.raw_s = cs, rs
        return j
    assert J.needs_check(job("a.mp4", "b.mp4", 45.0, 47.0))                 # a 47 s RAW for a 45 s Short
    assert J.needs_check(job("a.mp4", "b.mp4", 71.0, 100.0))                # 29 % apart
    assert not J.needs_check(job("a.mp4", "b.mp4", 69.0, 100.0))            # 31 % apart: the longer is the RAW
    assert not J.needs_check(job("comp.mp4", "b.mp4", 45.0, 47.0))          # the names say
    assert not J.needs_check(job("a.mp4", "RAW episode.mp4", 45.0, 47.0))
    assert not J.needs_check(job("a.mp4", "b.mp4", None, 47.0))             # a length unknown: as before
    chosen = job("a.mp4", "b.mp4", 45.0, 47.0)
    chosen.roles_chosen = True
    assert not J.needs_check(chosen)


def test_with_automatic_on_it_still_waits_and_your_pick_is_remembered_and_starts_it(page, tmp_path, monkeypatch):
    job, p = project_job(tmp_path, "clip a.mp4", "clip b.mp4")              # Download sends it, Automatic ON
    starts = []
    real_start = page.start
    monkeypatch.setattr(page, "start", lambda confirm=True: (starts.append(confirm), real_start(confirm)))
    page.add_project_job(job, autostart=True)
    assert pump_until(lambda: job.comp_s is not None and not getattr(job, "reading_lengths", False))
    QtWidgets.QApplication.processEvents()
    assert J.needs_check(job) and not starts and not FakeThread.made       # not started by itself
    row = page.rows[job.id]
    pick = row.pick_a if row.pick_a.text().startswith("clip a.mp4") else row.pick_b
    pick.click()                                            # "clip a" is the RAW
    assert job.raw.name == "clip a.mp4" and job.roles_chosen
    assert starts == [False]                                                # now it starts (Automatic was ON)
    assert pump_until(lambda: len(FakeThread.made) == 1) and FakeThread.made[0].job is job
    assert pump_until(lambda: (PJ.load(p.folder).data.get("roles") or {}).get("raw") == "clip a.mp4")
    d = PJ.load(p.folder).data
    assert d["raw"]["file"] == "clip a.mp4" and d["roles"]["how"] == "picked"
    page.stop()


def test_your_pick_runs_with_the_input_checks_skipped_and_never_swapped_back(tmp_path):
    from match_cuts import inputs_check
    from mr_app.core import run as RN
    j = J.Job("x", tmp_path / "short.mp4", tmp_path / "raw47.mp4")
    j.comp_s, j.raw_s = 45.0, 47.0
    pc = {"path": str(tmp_path / "c"), "duration": 45.0, "width": 1080, "height": 1920}
    pr = {"path": str(tmp_path / "r"), "duration": 47.0, "width": 1080, "height": 1920}
    found = inputs_check.problems(pc, pr)                   # night 6's check would stop this pair ...
    assert found and "almost as long as the RAW" in found[0]
    assert not J.forced(j)                                  # ... unless you chose
    J.choose_raw(j, tmp_path / "raw47.mp4")
    assert J.forced(j)
    cmd = RN.command(Path("py"), j.competitor, j.raw, Path("o"), Path("w"), True, 100.0, force_inputs=J.forced(j),
                     no_swap=j.roles_chosen)
    assert cmd[-2:] == ["--force-inputs", "--no-swap"]
    far = J.Job("y", tmp_path / "short.mp4", tmp_path / "ep.mp4", roles_chosen=True)
    far.comp_s, far.raw_s = 58.0, 1361.0
    far.swap()                                              # a clear pair you swapped the wrong way round:
    assert not J.forced(far) and "longer than the RAW" in far.problems()[0]   # its checks stay on, and it is said
    assert not [x for x in j.problems() if "longer" in x]   # (your pick of two about as long: nothing to say)


def test_the_forms_raw_field_never_skips_the_wrong_pair_check(tmp_path):
    """Day 12 review 2: the New video form's RAW field is your choice for the amber check and --no-swap, but never
    --force-inputs (that would skip the quick WRONG PAIR check): a wrong link in the form still stops as WRONG PAIR.
    Only "almost as long as the RAW" is skipped (--allow-short-raw), for a pair within 30 %."""
    from mr_app.core import flow
    from mr_app.core import run as RN
    p = PJ.create(tmp_path, name="clip")
    (p.folder / "short.mp4").write_bytes(b"c")
    (p.folder / "raw.mp4").write_bytes(b"r")
    p.update(competitor={"file": "short.mp4"}, raw={"file": "raw.mp4"})
    flow.set_roles(p.folder, "raw.mp4", "form")
    j = flow.edit_job(PJ.load(p.folder))
    assert j.roles_chosen and j.roles_how == "form"
    j.comp_s, j.raw_s = 28.0, 30.0                                          # within 30 % (and >= 90 %)
    assert not J.needs_check(j) and not J.forced(j) and J.short_raw(j)
    run = RN.JobRun(j, out_dir=tmp_path / "o", work_dir=tmp_path / "w", python=Path("py"))
    assert "--force-inputs" not in run.cmd and run.cmd[-2:] == ["--no-swap", "--allow-short-raw"]
    j.comp_s = 10.0                                                         # not about as long: nothing to skip
    assert not J.short_raw(j) and not J.forced(j)
    j.comp_s = 28.0
    J.choose_raw(j, j.raw)                                                  # picked on the row: your pick (as before)
    assert J.forced(j) and not J.short_raw(j)
    j2 = J.Job("y", j.competitor, j.raw, roles_chosen=True, roles_how="form", force_inputs=True)
    j2.comp_s, j2.raw_s = 28.0, 30.0                                        # Run anyway after a WRONG PAIR: forced
    assert J.forced(j2) and not J.short_raw(j2)


def _video(path: Path, seconds_: float, size: str) -> Path:
    from match_cuts.common import ffmpeg_bin
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:d={seconds_}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_the_tool_keeps_your_roles_with_no_swap(tmp_path):
    """What --force-inputs alone would do (cli.resolve_inputs swaps two files that look reversed: the shorter / the
    portrait one is the competitor) -- and what --no-swap keeps: your choice."""
    from match_cuts.cli import resolve_inputs
    comp = _video(tmp_path / "a.mp4", 3.0, "160x90")                       # you said: the longer one is the Short
    raw = _video(tmp_path / "b.mp4", 2.5, "160x90")
    c, r, notes = resolve_inputs(str(comp), str(raw), tmp_path, no_swap=False)
    assert (c, r) == (str(raw), str(comp)) and notes                        # swapped back without --no-swap
    c, r, notes = resolve_inputs(str(comp), str(raw), tmp_path, no_swap=True)
    assert (c, r) == (str(comp), str(raw)) and not notes


def test_the_pre_start_check_leaves_out_a_pair_you_chose(page, tmp_path, monkeypatch):
    from match_cuts import batch
    asked = []
    monkeypatch.setattr(batch, "input_problems", lambda pairs, short=(): asked.append([n for n, _c, _r in pairs]) or {})
    a = add(page, tmp_path, "mine", "clip a.mp4", "clip b.mp4")
    page.pick_raw(a, a.raw)
    b = add(page, tmp_path, "other", "short.mp4", "episode.mp4")
    page.start(confirm=False)
    assert pump_until(lambda: bool(asked))
    assert asked[0] == ["other"]


def test_chosen_roles_survive_the_convert_steps_longer_is_raw(tmp_path, monkeypatch):
    p = PJ.create(tmp_path, name="clip")
    (p.folder / "a.mp4").write_bytes(b"a")
    (p.folder / "b.mp4").write_bytes(b"b")
    p.update(competitor={"file": "a.mp4"}, raw={"file": "b.mp4"})
    import match_cuts.cli as CLI
    monkeypatch.setattr(CLI, "quick_probe", lambda f: {"duration": 47.0 if Path(f).name == "a.mp4" else 45.0})
    flow.set_roles(p.folder, "b.mp4")                       # you said: the shorter b is the RAW
    assert flow._longer_is_raw(PJ.load(p.folder)) is False                 # noqa: SLF001 - never swapped back
    assert PJ.load(p.folder).data["raw"]["file"] == "b.mp4"
    q = PJ.create(tmp_path, name="other")
    (q.folder / "a.mp4").write_bytes(b"a")
    (q.folder / "b.mp4").write_bytes(b"b")
    q.update(competitor={"file": "a.mp4"}, raw={"file": "b.mp4"})
    assert flow._longer_is_raw(PJ.load(q.folder)) is True                  # noqa: SLF001 - not chosen: the rule
    with pytest.raises(flow.StepError):
        flow.set_roles(q.folder, "nope.mp4")


def test_a_dropped_project_folder_keeps_the_raw_you_chose(tmp_path):
    p = PJ.create(tmp_path, name="clip")
    for n in ("x.mp4", "y.mp4"):
        (p.folder / n).write_bytes(b"v")
    p.update(competitor={"file": "x.mp4"}, raw={"file": "y.mp4"})
    flow.set_roles(p.folder, "x.mp4")                       # chosen earlier: x is the RAW
    j = J.Job("clip", p.folder / "x.mp4", p.folder / "y.mp4")              # the drop decided the other way round
    J.project_for_job(j)
    assert j.raw.name == "x.mp4" and j.competitor.name == "y.mp4" and j.roles_chosen


# ---------------------------------------------------------------------------------------------------------------------
# section 5: newest on top (the order shown), the run order unchanged
# ---------------------------------------------------------------------------------------------------------------------

def _shown(page) -> list[str]:
    return [r.job.name for r in page.queue.rows()]


def test_display_order_running_then_waiting_newest_then_finished_newest():
    t0 = datetime(2026, 10, 19, 9, 0)
    js = [J.Job(n, Path("c.mp4"), Path("r.mp4")) for n in "abcdef"]
    for k, j in enumerate(js):
        j.added = t0 + timedelta(minutes=k)
    js[0].state, js[0].edited = J.DONE, t0 + timedelta(hours=1)
    js[1].state, js[1].edited = J.DONE, t0 + timedelta(hours=2)
    js[2].state = J.RUNNING
    js[5].state, js[5].edited = J.STOPPED, t0 + timedelta(minutes=30)
    assert [j.name for j in J.display_order(js)] == ["c", "e", "d", "b", "a", "f"]
    assert J.run_places(js) == {js[3].id: 1, js[4].id: 2}                  # the run order: as added
    assert [J.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st"]


def test_the_queue_shows_newest_on_top_but_runs_in_the_order_added(page, tmp_path):
    a, b, c = (add(page, tmp_path, n) for n in ("a", "b", "c"))
    assert _shown(page) == ["c", "b", "a"]                                  # the newest on top
    assert [page.rows[x.id].runs_text() for x in (a, b, c)] == ["runs 1st", "runs 2nd", "runs 3rd"]
    page.start(confirm=False)
    assert pump_until(lambda: len(FakeThread.made) == 1)
    assert FakeThread.made[0].job is a                                      # the oldest runs first
    assert _shown(page) == ["a", "c", "b"]                                  # running at the very top
    FakeThread.made[0].finish("PASS")
    assert FakeThread.made[1].job is b
    assert _shown(page) == ["b", "c", "a"]                                  # running, waiting, finished
    FakeThread.made[1].finish("OK with a note")
    assert _shown(page) == ["c", "b", "a"]                                  # the one finished last on top
    d = add(page, tmp_path, "d")                                            # a new one while c runs
    assert _shown(page) == ["c", "d", "b", "a"]
    FakeThread.made[2].finish("FAIL")
    assert FakeThread.made[3].job is d
    assert _shown(page) == ["d", "c", "b", "a"]
    FakeThread.made[3].finish("PASS")
    assert _shown(page) == ["d", "c", "b", "a"] and not page.running


def test_a_dragged_row_stays_where_dropped_and_sets_the_run_order(page, tmp_path):
    a, b, c, d = (add(page, tmp_path, n) for n in ("a", "b", "c", "d"))
    assert _shown(page) == ["d", "c", "b", "a"]
    page.drop_row(a.id, 0)                                  # a (next to run) dragged to the top ...
    assert _shown(page) == ["a", "d", "c", "b"]              # ... stays there,
    assert [j.name for j in page.jobs] == ["b", "c", "d", "a"]               # ... and runs last now
    assert page.rows[a.id].runs_text() == "runs 4th" and page.rows[b.id].runs_text() == "runs 1st"
    assert ("Order changed ✓", "ok") in page.toasts
    page.drop_row(d.id, 4)                                  # d dropped at the bottom: runs next
    assert _shown(page) == ["a", "c", "b", "d"] and page.rows[d.id].runs_text() == "runs 1st"
    page.start(confirm=False)
    assert pump_until(lambda: len(FakeThread.made) == 1) and FakeThread.made[0].job is d


def test_only_waiting_rows_can_be_dragged_and_finished_rows_never_move(page, tmp_path):
    a, b = add(page, tmp_path, "a"), add(page, tmp_path, "b")
    a.state, a.result, a.edited = J.DONE, {"label": "PASS"}, datetime.now()
    page._reorder()                                         # noqa: SLF001
    before = [j.name for j in page.jobs]
    page.drop_row(a.id, 0)
    assert [j.name for j in page.jobs] == before and _shown(page) == ["b", "a"]


def test_a_new_row_scrolls_the_list_to_the_top(page, tmp_path):
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(page)
    sa.resize(900, 400)
    sa.show()
    for n in "abcdef":
        add(page, tmp_path, n)
    QtWidgets.QApplication.processEvents()
    bar = sa.verticalScrollBar()
    assert pump_until(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum())
    add(page, tmp_path, "g")
    assert pump_until(lambda: bar.value() == 0, 2.0)
    sa.takeWidget()
    sa.deleteLater()


# ---------------------------------------------------------------------------------------------------------------------
# motion: a row glides to its new place
# ---------------------------------------------------------------------------------------------------------------------

def test_a_row_that_changes_state_glides_to_its_new_place(page, tmp_path):
    a, b, c = (add(page, tmp_path, n) for n in ("a", "b", "c"))
    page.show()
    pump_until(lambda: False, 0.1)
    Mo.set_enabled(True)
    row = page.rows[a.id]
    y0 = row.y()
    assert y0 > 0                                           # a (the oldest waiting row) is at the bottom
    page.start(confirm=False)                               # a runs: it goes to the top
    seen = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.0:
        QtWidgets.QApplication.processEvents()
        seen.append(row.y())
        time.sleep(0.004)
    assert seen[-1] == 0 and page.queue.target_y(row) == 0
    between = [y for y in seen if 0 < y < y0]
    assert len(set(between)) >= 5                           # many positions in between: it glided
    steps = [abs(b_ - a_) for a_, b_ in zip(seen, seen[1:])]
    assert max(steps) < 0.6 * y0                             # never one jump
    other = page.rows[c.id]
    assert other.y() > 0                                    # the others moved down to make room


def test_glide_list_api():
    from PySide6.QtWidgets import QLabel
    from mr_app.ui.glide_list import GlideList
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    lst = GlideList(spacing=4)
    lst.resize(300, 10)
    ws = [QLabel(t) for t in "abc"]
    for w in ws:
        w.setFixedHeight(20)
        lst.append(w)
    assert [w.y() for w in ws] == [0, 24, 48] and lst.height() == 68
    lst.set_order([ws[2], ws[0]])                           # left out: kept at the end
    assert lst.items() == [ws[2], ws[0], ws[1]] and [lst.target_y(w) for w in lst.items()] == [0, 24, 48]
    lst.insert(QLabel("new"), 0)
    assert lst.count() == 4 and lst.items()[1] is ws[2]
    lst.remove(ws[2])
    assert lst.count() == 3 and ws[0].y() == lst.target_y(ws[0])
    Mo.set_enabled(True)
    lst.deleteLater()
    app.processEvents()


# ---------------------------------------------------------------------------------------------------------------------
# the board: the newest card on top in every column, the pinned ones above
# ---------------------------------------------------------------------------------------------------------------------

def test_the_board_shows_the_newest_card_on_top_with_the_pinned_ones_above(tmp_path, monkeypatch):
    from mr_app.core import board as B
    from mr_app.tools import board_tool as BT
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    folders = {}
    for name, hhmm in (("old clip", "02:00"), ("new clip", "05:00"), ("mid clip", "03:00")):
        p = PJ.create(local, name=name)

        def apply(d, hhmm=hhmm):
            d["steps"]["download"].update(state="done", started=f"2026-10-18T{hhmm}:00+03:00",
                                          finished=f"2026-10-18T{hhmm}:00+03:00")
        p.change(apply)
        folders[name] = p.folder
    B.pin(folders["old clip"])
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": None)
    page = BT.BoardPage(ctx)
    page._up_timer.stop()                                   # noqa: SLF001
    page.check_uploads = lambda force=False: None
    page.resize(1400, 900)
    page.show()
    assert pump_until(lambda: len(page.cards) == 3)
    shown = [w.card.folder.name for w in page.columns["downloaded"].widgets]
    assert shown == [folders["old clip"].name, folders["new clip"].name, folders["mid clip"].name]
    page.close()
    page.deleteLater()
    app.processEvents()


def test_history_is_newest_first(tmp_path):
    from mr_app.core import history as H
    p = PJ.create(tmp_path, name="clip")

    def apply(d):
        for s, hhmm in (("download", "02:00"), ("convert", "03:00"), ("edit", "05:00")):
            d["steps"][s].update(state="done", started=f"2026-10-18T{hhmm}:00+03:00",
                                 finished=f"2026-10-18T{hhmm}:00+03:00")
    p.change(apply)
    evs = H.events([PJ.load(p.folder).data])
    ats = [e.at for e in evs]
    assert ats == sorted(ats, reverse=True) and len(ats) >= 3


def test_long_downloaded_names_are_shortened_in_the_middle_on_the_pick_buttons_and_the_pair_line():
    # the e2e run (day 12): yt-dlp's names are long -- the pick button showed "fore Tobey Maguire ... is the RA"
    from mr_app.tools.match_cuts_tool import short_file_name
    n = "Before Tobey Maguire Was Spider-Man He Had One Job 🕷️📺 [f18B0Accoig].mp4"
    s = short_file_name(n)
    assert s.startswith("Before Tobey") and s.endswith("[f18B0Accoig].mp4") and "…" in s and len(s) <= 48
    assert short_file_name("raw.mp4") == "raw.mp4" and short_file_name("clip a.mp4") == "clip a.mp4"
    long_plain = "x" * 90 + ".mkv"
    assert short_file_name(long_plain).endswith(".mkv") and len(short_file_name(long_plain)) <= 48
