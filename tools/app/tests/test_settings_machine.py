"""This computer's settings (mr_app.core.settings) and which computer it is (mr_app.core.machine): the file, its
defaults, unknown keys kept, a broken file kept aside; the PC / laptop rules (Settings, the host-name table, the
battery, the firmware's profile, a lid, a UPS) and each one's default "Save projects to".
Run: python -m pytest tools/app/tests/test_settings_machine.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import machine as M    # noqa: E402
from mr_app.core import settings as S   # noqa: E402


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    f = tmp_path / "appdata" / "MovieRecaps" / "settings.json"
    monkeypatch.setenv(S.ENV, str(f))
    return f


def fake_hardware(monkeypatch, **kw):
    hw = M.Hardware(**kw)
    monkeypatch.setattr(M, "hardware", lambda: hw)
    monkeypatch.setattr(M, "host", lambda: "Gvidas")
    return hw


# ---------------------------------------------------------------------------------------------------------------------
# settings.json
# ---------------------------------------------------------------------------------------------------------------------

def test_no_file_gives_the_defaults_and_writes_nothing(settings_file):
    s = S.load()
    assert s == S.Settings()
    assert (s.save_to, s.machine, s.animations, s.intro) == ("", "auto", True, True)
    assert (s.daily_goal, s.monthly_goal, s.upload_check_minutes) == (6, 150, 30)
    assert (s.pc_names, s.laptop_names) == ([], [])
    assert not settings_file.exists()


def test_save_and_load_keep_every_setting(settings_file):
    s = S.Settings(save_to=r"E:\Somewhere else\Projects", machine="laptop", animations=False, intro=False,
                   daily_goal=8, monthly_goal=200, upload_check_minutes=15, pc_names=["DESKTOP-1"],
                   laptop_names=["Gvidas"])
    assert S.save(s) == settings_file
    assert S.load() == s
    d = json.loads(settings_file.read_text(encoding="utf-8"))
    assert d["save_to"] == r"E:\Somewhere else\Projects" and d["laptop_names"] == ["Gvidas"]
    assert sorted(p.name for p in settings_file.parent.iterdir()) == ["settings.json"]   # no temp file left


def test_keys_this_version_does_not_know_are_kept(settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({"daily_goal": 9, "future": {"a": [1, 2]}, "theme": "dark"}),
                             encoding="utf-8")
    s = S.load()
    assert s.daily_goal == 9 and s.extra == {"future": {"a": [1, 2]}, "theme": "dark"}
    d = json.loads(settings_file.read_text(encoding="utf-8"))
    d["written_meanwhile"] = True                               # a newer app on this computer, after our load
    settings_file.write_text(json.dumps(d), encoding="utf-8")
    s.daily_goal = 4
    S.save(s)
    d = json.loads(settings_file.read_text(encoding="utf-8"))
    assert d["daily_goal"] == 4 and d["future"] == {"a": [1, 2]} and d["theme"] == "dark"
    assert d["written_meanwhile"] is True


def test_a_broken_file_gives_the_defaults_and_is_kept_as_bad(settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{not json", encoding="utf-8")
    assert S.load() == S.Settings()
    bad = settings_file.with_name("settings.json.bad")
    assert bad.read_text(encoding="utf-8") == "{not json" and not settings_file.exists()
    settings_file.write_text("[1, 2]", encoding="utf-8")       # JSON, but not an object
    assert S.load() == S.Settings()


@pytest.mark.parametrize("key, value, want", [
    ("daily_goal", "abc", 6), ("daily_goal", "7", 7), ("daily_goal", 0, 1), ("daily_goal", True, 6),
    ("daily_goal", float("inf"), 6), ("monthly_goal", -5, 1), ("upload_check_minutes", 99999, 1440),
    ("animations", "yes", True), ("intro", 0, True), ("machine", "desktop", "auto"), ("machine", " Laptop ", "laptop"),
    ("save_to", None, ""), ("save_to", "  D:\\X  ", "D:\\X"), ("pc_names", "Gvidas", []),
    ("laptop_names", ["Gvidas", " ", 5], ["Gvidas", "5"]),
])
def test_a_value_of_the_wrong_kind_falls_back_to_its_default(settings_file, key, value, want):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({key: value}), encoding="utf-8")
    assert getattr(S.load(), key) == want


def test_where_the_file_is(monkeypatch, tmp_path):
    monkeypatch.setenv(S.ENV, str(tmp_path / "x.json"))
    monkeypatch.setenv(S.APPDATA_ENV, str(tmp_path / "appdata"))
    assert S.path() == tmp_path / "x.json"                                  # the file itself wins
    monkeypatch.delenv(S.ENV)
    assert S.path() == tmp_path / "appdata" / "settings.json" and S.app_dir() == tmp_path / "appdata"
    monkeypatch.delenv(S.APPDATA_ENV)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert S.path() == tmp_path / "Roaming" / "MovieRecaps" / "settings.json"
    assert S.app_dir() == tmp_path / "Roaming" / "MovieRecaps"


def test_save_root_is_the_setting_else_this_computers_default(monkeypatch):
    fake_hardware(monkeypatch, battery=False, profile=1, lid=False)
    assert S.save_root(S.Settings()) == Path(r"D:\Shared\Projects")
    fake_hardware(monkeypatch, battery=True, profile=2, lid=True)
    assert S.save_root(S.Settings()) == Path(r"\\192.168.101.11\Shared\Projects")
    assert S.save_root(S.Settings(machine="pc")) == Path(r"D:\Shared\Projects")
    assert S.save_root(S.Settings(save_to=r" E:\My Projects ")) == Path(r"E:\My Projects")
    S.save(S.Settings(machine="pc"))
    assert S.save_root() == Path(r"D:\Shared\Projects")           # None: this computer's settings file


# ---------------------------------------------------------------------------------------------------------------------
# which computer
# ---------------------------------------------------------------------------------------------------------------------

def test_settings_say_which_computer_it_is(monkeypatch):
    fake_hardware(monkeypatch, battery=True, profile=2, lid=True)          # a laptop by its hardware ...
    assert M.which(S.Settings(machine="pc")) == "pc"                       # ... but you said PC
    assert M.describe(S.Settings(machine="pc")) == "PC (set in Settings)"
    fake_hardware(monkeypatch, battery=False, profile=1)
    assert M.which(S.Settings(machine="laptop")) == "laptop"
    assert M.describe(S.Settings(machine="laptop")) == "laptop (set in Settings)"


def test_the_host_name_table_counts_when_the_name_is_in_one_list(monkeypatch):
    fake_hardware(monkeypatch, battery=True, profile=2)                    # laptop hardware
    assert M.which(S.Settings(pc_names=["GVIDAS"])) == "pc"                # the name decides (any case)
    assert M.describe(S.Settings(pc_names=["gvidas"])) == "PC (Gvidas, in Settings' PC names)"
    fake_hardware(monkeypatch, battery=False, profile=1)
    assert M.which(S.Settings(laptop_names=["Gvidas"])) == "laptop"
    both = S.Settings(pc_names=["Gvidas"], laptop_names=["Gvidas"])       # in both lists: the hardware decides
    assert M.which(both) == "pc"
    fake_hardware(monkeypatch, battery=True, profile=2)
    assert M.which(both) == "laptop"
    assert M.which(S.Settings(pc_names=["OTHER"])) == "laptop"            # not in a list: the hardware


@pytest.mark.parametrize("hw, kind, why", [
    (dict(battery=True, profile=2, lid=True), "laptop", "battery"),
    (dict(battery=True, profile=None), "laptop", "battery"),
    (dict(battery=False, profile=1, lid=False), "pc", "no battery"),
    (dict(battery=None, profile=2), "laptop", "mobile firmware"),        # PCSystemType 2
    (dict(battery=None, profile=8), "laptop", "mobile firmware"),        # a tablet
    (dict(battery=False, profile=None, lid=True), "laptop", "lid"),
    (dict(battery=True, short_term=True, profile=None, lid=False), "pc", "UPS"),
    (dict(battery=True, profile=1, lid=False), "pc", "UPS"),             # a battery on a desktop: its UPS
    (dict(battery=True, profile=3, lid=False), "pc", "UPS"),             # a workstation
    (dict(), "pc", "could not be asked"),
])
def test_the_hardware_rules(monkeypatch, hw, kind, why):
    fake_hardware(monkeypatch, **hw)
    got, reason = M.decide(S.Settings())
    assert got == kind and why in reason and reason.startswith("Gvidas, ")


def test_describe_in_plain_words(monkeypatch):
    fake_hardware(monkeypatch, battery=False, profile=1, lid=False)
    assert M.describe(S.Settings()) == "PC (Gvidas, no battery)"
    fake_hardware(monkeypatch, battery=True, profile=2, lid=True)
    assert M.describe(S.Settings()) == "laptop (Gvidas, battery)"
    assert M.label("pc") == "PC" and M.label("laptop") == "laptop"


def test_which_without_settings_reads_this_computers_file(monkeypatch):
    fake_hardware(monkeypatch, battery=False, profile=1)
    assert M.which() == "pc"
    S.save(S.Settings(machine="laptop"))
    assert M.which() == "laptop"


def test_the_default_save_roots():
    assert M.default_save_root("pc") == Path(r"D:\Shared\Projects")
    assert M.default_save_root("laptop") == Path(r"\\192.168.101.11\Shared\Projects")
    assert str(M.default_save_root("laptop")) == "\\\\192.168.101.11\\Shared\\Projects"


def test_the_hardware_is_asked_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "_probe", lambda: calls.append(1) or M.Hardware(battery=False))
    M.hardware.cache_clear()
    try:
        assert M.hardware() == M.Hardware(battery=False) and M.hardware() is M.hardware()
        assert len(calls) == 1
    finally:
        M.hardware.cache_clear()


def test_this_computer_for_real():
    """Asked from Windows in well under a second (no WMI); on a computer without a battery whose firmware says
    desktop (this PC: BatteryFlag 128, profile 1) it is the PC."""
    t0 = time.perf_counter()
    hw = M._probe()
    assert time.perf_counter() - t0 < 0.5
    assert hw.battery in (True, False, None) and hw.lid in (True, False, None)
    assert hw.profile is None or 0 <= hw.profile <= 255
    kind, why = M.decide(S.Settings())
    assert kind in M.KINDS and why
    if hw.battery is False and hw.profile in M.DESKTOP_PROFILES and not hw.lid:
        assert kind == "pc" and why.endswith("no battery")


def test_identity_tells_the_two_computers_apart():
    me = M.identity(S.Settings(machine="laptop"))
    assert me["computer"] == "laptop" and me["host"] == M.host() and me["id"] == M.machine_id()
    assert me["id"] and M.machine_id() == M.machine_id()
