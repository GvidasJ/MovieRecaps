"""The long-work lock (mr_app.core.locks): one computer at a time on a project. The two computers are simulated by
different identities (machine id, PC / laptop) and pids; a stale lock by moving its times into the past.
Run: python -m pytest tools/app/tests/test_locks.py"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import locks as L      # noqa: E402
from mr_app.core import machine as M    # noqa: E402

PC = {"computer": "pc", "host": "Gvidas", "id": "PC-GUID", "user": "gvida"}
LAPTOP = {"computer": "laptop", "host": "Gvidas", "id": "LAPTOP-GUID", "user": "render"}
NO_SUCH_PID = 2 ** 31 - 7


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "Shared Projects" / "2026-10-18_a video é"
    p.mkdir(parents=True)
    (p / "project.json").write_text("{}", encoding="utf-8")
    return p


def read_lock(project: Path) -> dict:
    return json.loads((project / L.LOCK_FILE).read_text(encoding="utf-8"))


def write_lock(project: Path, me: dict, pid: int, hours_ago: float = 0.0, what: str = "edit",
               touch_hours_ago: float | None = None) -> dict:
    t = (datetime.now() - timedelta(hours=hours_ago)).astimezone().isoformat(timespec="seconds")
    info = {**me, "pid": pid, "what": what, "started": t, "heartbeat": t, "token": f"tok-{me['id']}-{pid}"}
    f = project / L.LOCK_FILE
    f.write_text(json.dumps(info), encoding="utf-8")
    ago = hours_ago if touch_hours_ago is None else touch_hours_ago
    old = time.time() - ago * 3600
    os.utime(f, (old, old))
    return info


def test_acquire_writes_the_lock_and_release_removes_it(project):
    lk = L.WorkLock(project, "edit", me=LAPTOP, pid=111)
    assert lk.acquire() is lk and lk.held
    d = read_lock(project)
    assert {k: d[k] for k in ("computer", "host", "id", "user", "pid", "what")} == \
        {"computer": "laptop", "host": "Gvidas", "id": "LAPTOP-GUID", "user": "render", "pid": 111, "what": "edit"}
    assert datetime.fromisoformat(d["started"]).tzinfo is not None and d["heartbeat"] == d["started"] and d["token"]
    assert lk.release() is True and not lk.held
    assert not (project / L.LOCK_FILE).exists()
    assert sorted(p.name for p in project.iterdir()) == ["project.json"]       # nothing left behind


def test_with_block(project):
    with L.WorkLock(project, "render", me=PC, pid=5) as lk:
        assert read_lock(project)["what"] == "render" and lk.held
    assert not (project / L.LOCK_FILE).exists()


def test_the_other_computer_gets_a_plain_message(project):
    a = L.WorkLock(project, "edit", me=LAPTOP, pid=111).acquire()
    with pytest.raises(L.LockedError) as e:
        L.WorkLock(project, "publish", me=PC, pid=222).acquire()
    since = datetime.fromisoformat(read_lock(project)["started"]).strftime("%H:%M")
    assert str(e.value) == f"the laptop is working on this project since {since} (edit)"
    assert e.value.holder["id"] == "LAPTOP-GUID"
    h = L.holder(project, me={**PC, "pid": 222})
    assert h["text"] == str(e.value) and h["stale"] is False and h["mine"] is False and h["this_computer"] is False
    with pytest.raises(L.LockedError) as e2:
        L.WorkLock(project, "edit", me={**LAPTOP, "id": "OTHER"}, pid=1).acquire()    # both say "laptop"
    assert "the other computer (also a laptop)" in str(e2.value)
    a.release()
    b = L.WorkLock(project, "publish", me=PC, pid=222).acquire()                     # free now
    assert read_lock(project)["computer"] == "pc"
    b.release()


def test_an_old_day_is_named(project):
    write_lock(project, LAPTOP, 111, hours_ago=30, touch_hours_ago=0)                # beating, started yesterday
    with pytest.raises(L.LockedError) as e:
        L.WorkLock(project, me=PC, pid=2).acquire()
    d = datetime.now() - timedelta(hours=30)
    assert f"since {d.day} {d:%b %H:%M} (edit)" in str(e.value)


def test_this_process_may_take_its_own_lock_again(project):
    a = L.WorkLock(project, "edit", me=PC, pid=7).acquire()
    token = read_lock(project)["token"]
    b = L.WorkLock(project, "edit", me=PC, pid=7).acquire()                          # reentrant
    assert read_lock(project)["token"] == token
    a.release()
    assert (project / L.LOCK_FILE).exists()                                          # b still holds it
    b.release()
    assert not (project / L.LOCK_FILE).exists()
    a.release()                                                                       # twice: nothing happens
    assert L.WorkLock(project, me=PC, pid=7).acquire().release()


def test_a_lock_this_process_left_from_before_is_ours(project):
    info = write_lock(project, PC, 4242)
    lk = L.WorkLock(project, "edit", me=PC, pid=4242).acquire()
    assert read_lock(project)["token"] == info["token"] and lk.taken_over == ""
    lk.release()
    assert not (project / L.LOCK_FILE).exists()


def test_a_stale_lock_is_taken_over_and_said_so(project):
    write_lock(project, LAPTOP, 111, hours_ago=3)
    lk = L.WorkLock(project, "publish", me=PC, pid=222).acquire()
    since = L._since({"started": (datetime.now() - timedelta(hours=3)).astimezone().isoformat()})   # (another day
    #                                                                       before 03:00: "21 Oct 23:19")
    assert lk.taken_over.startswith(f"taken over from the laptop (since {since}, edit): no heartbeat for 3 h ")
    d = read_lock(project)
    assert d["id"] == "PC-GUID" and d["what"] == "publish"
    claim = f"{L.LOCK_FILE}.tokLAPTOPGUID111.claim1"                     # the takeover's claim, by the old token
    assert sorted(p.name for p in project.iterdir()) == [L.LOCK_FILE, claim, "project.json"]
    assert json.loads((project / claim).read_text(encoding="utf-8"))["id"] == "PC-GUID"
    lk.release()
    write_lock(project, LAPTOP, 333, hours_ago=3)                        # another stale lock later: its own claim
    L.WorkLock(project, me=PC, pid=222).acquire().release()
    assert sorted(p.name for p in project.iterdir()) == \
        [f"{L.LOCK_FILE}.tokLAPTOPGUID111.claim1", f"{L.LOCK_FILE}.tokLAPTOPGUID333.claim1", "project.json"]


def test_a_takeover_under_way_elsewhere_is_waited_for_a_claim_left_half_way_is_not(project, monkeypatch):
    monkeypatch.setattr(L, "CLAIM_WAIT_S", 0.3)
    write_lock(project, LAPTOP, 111, hours_ago=3)
    claim = project / f"{L.LOCK_FILE}.tokLAPTOPGUID111.claim1"
    claim.write_text(json.dumps({"id": "THIRD"}), encoding="utf-8")      # another computer is taking it over now
    with pytest.raises(L.LockedError) as e:
        L.WorkLock(project, me=PC, pid=222).acquire()
    assert str(e.value) == "another computer is taking this project over this very moment: try again in a minute"
    assert read_lock(project)["id"] == "LAPTOP-GUID"                     # untouched
    old = time.time() - L.CLAIM_STALE_S - 5
    os.utime(claim, (old, old))                                          # that taker stopped half-way
    lk = L.WorkLock(project, me=PC, pid=222).acquire()
    assert lk.taken_over.startswith("taken over from the laptop") and read_lock(project)["id"] == "PC-GUID"
    assert (project / f"{L.LOCK_FILE}.tokLAPTOPGUID111.claim2").is_file()
    lk.release()


def test_a_lock_that_changed_after_it_was_judged_is_not_removed(project, monkeypatch):
    stale = write_lock(project, LAPTOP, 111, hours_ago=3)
    real = L.WorkLock._read_settled

    def judged_then_woke_up(self):
        out = real(self)
        if out[1] is not None and out[1]["heartbeat"] == stale["heartbeat"]:   # its holder beats right after our look
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            (project / L.LOCK_FILE).write_text(json.dumps({**stale, "heartbeat": now}), encoding="utf-8")
        return out
    monkeypatch.setattr(L.WorkLock, "_read_settled", judged_then_woke_up)
    with pytest.raises(L.LockedError):
        L.WorkLock(project, me=PC, pid=222).acquire()
    assert read_lock(project)["id"] == "LAPTOP-GUID"                     # the woken holder keeps it


def test_stale_means_neither_its_time_nor_its_heartbeat_moved_for_two_hours(project):
    write_lock(project, LAPTOP, 111, hours_ago=3, touch_hours_ago=0.5)     # heartbeat field old, file touched
    with pytest.raises(L.LockedError):
        L.WorkLock(project, me=PC, pid=2).acquire()
    write_lock(project, LAPTOP, 111, hours_ago=0.5, touch_hours_ago=3)     # file old, heartbeat field new
    with pytest.raises(L.LockedError):
        L.WorkLock(project, me=PC, pid=2).acquire()
    write_lock(project, LAPTOP, 111, hours_ago=2.1, touch_hours_ago=2.05)  # both older than STALE_S
    h = L.holder(project, me={**PC, "pid": 2})
    assert h["stale"] is True and h["why_stale"].startswith("no heartbeat for 2 h")
    assert L.WorkLock(project, me=PC, pid=2).acquire().release()


def test_a_lock_of_this_computer_whose_program_ended_is_stale_at_once(project):
    write_lock(project, PC, NO_SUCH_PID)                                    # fresh, but its program is gone
    lk = L.WorkLock(project, me=PC, pid=os.getpid()).acquire()
    assert lk.taken_over.endswith("its program is not running any more")
    assert lk.taken_over.startswith("taken over from this computer (another program)")
    lk.release()
    write_lock(project, PC, os.getppid())                                   # its program runs: respected
    with pytest.raises(L.LockedError) as e:
        L.WorkLock(project, me=PC, pid=os.getpid()).acquire()
    assert str(e.value).startswith("this computer (another program) is working on this project since")


def test_a_lock_being_written_is_respected_an_old_unreadable_one_is_not(project):
    f = project / L.LOCK_FILE
    f.write_bytes(b"")                                                      # just made, not written yet
    t0 = time.time()
    with pytest.raises(L.LockedError) as e:
        L.WorkLock(project, me=PC, pid=2).acquire()
    assert str(e.value) == "another program is starting work on this project" and time.time() - t0 < 3
    old = time.time() - 3 * 3600
    os.utime(f, (old, old))
    lk = L.WorkLock(project, me=PC, pid=2).acquire()
    assert "could not be read" in lk.taken_over
    lk.release()


def test_the_heartbeat_keeps_it_fresh(project):
    lk = L.WorkLock(project, me=PC, pid=9, heartbeat_s=0.1).acquire()
    first = read_lock(project)["heartbeat"]
    old = time.time() - 3 * 3600
    os.utime(project / L.LOCK_FILE, (old, old))
    t0 = time.time()
    while time.time() - t0 < 5:
        st = os.stat(project / L.LOCK_FILE)
        if time.time() - st.st_mtime < 60 and read_lock(project)["heartbeat"] != first:
            break
        time.sleep(0.1)
    assert time.time() - os.stat(project / L.LOCK_FILE).st_mtime < 60      # touched again
    assert read_lock(project)["heartbeat"] > first and read_lock(project)["token"]
    assert L.holder(project, me={**LAPTOP, "pid": 1})["stale"] is False
    lk.release()
    assert not (project / L.LOCK_FILE).exists()


def test_after_a_takeover_the_old_holder_writes_nothing(project):
    old = L.WorkLock(project, me=LAPTOP, pid=111, heartbeat_s=0.1).acquire()
    write_lock(project, PC, 222)                                            # the PC took it over meanwhile
    t0 = time.time()
    while not old.lost and time.time() - t0 < 5:
        time.sleep(0.05)
    assert "the PC is working on this project" in old.lost
    time.sleep(0.3)
    assert read_lock(project)["id"] == "PC-GUID"                            # not overwritten by the heartbeat
    old.release()
    assert read_lock(project)["id"] == "PC-GUID"                            # and not removed by its release


def test_a_lock_removed_by_hand_is_made_again_while_the_work_goes_on(project):
    lk = L.WorkLock(project, me=PC, pid=3, heartbeat_s=0.1).acquire()
    (project / L.LOCK_FILE).unlink()
    t0 = time.time()
    while not (project / L.LOCK_FILE).exists() and time.time() - t0 < 5:
        time.sleep(0.05)
    assert read_lock(project)["id"] == "PC-GUID" and not lk.lost
    lk.release()
    assert not (project / L.LOCK_FILE).exists()


def test_two_computers_taking_over_at_once_only_one_wins(project):
    for round_ in range(8):
        write_lock(project, {**LAPTOP, "id": "GONE"}, 1, hours_ago=5)
        won, lost, start = [], [], threading.Event()

        def take(me: dict, pid: int) -> None:
            start.wait()
            try:
                won.append(L.WorkLock(project, me=me, pid=pid).acquire())
            except L.LockedError as e:
                lost.append(e)
        ths = [threading.Thread(target=take, args=(m, 100 + k))
               for k, m in enumerate((PC, LAPTOP, {**PC, "id": "THIRD"}))]
        for t in ths:
            t.start()
        start.set()
        for t in ths:
            t.join(10)
        assert len(won) == 1 and len(lost) == 2, (round_, won, lost)
        assert read_lock(project)["id"] == won[0].me["id"]
        claims = sorted(p.name for p in project.iterdir() if ".claim" in p.name)
        assert claims == [f"{L.LOCK_FILE}.tokGONE1.claim1"]               # one claim: one taker removed it
        won[0].release()
        for c in claims:
            (project / c).unlink()


def test_holder_for_the_board(project):
    assert L.holder(project) is None
    lk = L.WorkLock(project, "edit").acquire()                              # this computer, this process
    h = L.holder(project)
    assert h["mine"] is True and h["this_computer"] is True and h["computer"] == M.which()
    assert h["text"].startswith("this computer is working on this project since")
    lk.release()
    assert L.holder(project) is None
