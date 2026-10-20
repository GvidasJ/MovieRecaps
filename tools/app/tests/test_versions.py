"""Day 13, items 1.4 and 1.5: the versions the project expects (core.versions, ONE table for setup_app.py and the
app's start-up check) and setup_app.py going on when PySceneDetect is broken. Every reader is faked: nothing here
installs, downloads or imports a real package."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import versions as V  # noqa: E402

APP = Path(__file__).resolve().parents[1]


def _want(key):
    return next(w for w in V.WANTS if w.key == key)


def _readers(dists=None, exes=None, gpu="", cuda=(True, ""), broken=(), homes=None):
    dists = dict(dists or {})
    exes = dict(exes or {})
    return V.Readers(dist_version=lambda d: dists.get(d),
                     module_problem=lambda m: "it does not load (ModuleNotFoundError: x)" if m in broken else "",
                     module_home=lambda m: (homes or {}).get(m, ""),
                     exe_version=lambda e: exes.get(e, (None, "")),
                     gpu_name=lambda: gpu, torch_cuda=lambda: cuda)


def test_versions_are_compared_as_numbers():
    assert V.vtuple("2026.08.19") == (2026, 8, 19) and V.vtuple("2.11.0+cu130") == (2, 11, 0)
    assert V.vtuple("n8.0-3-g08a81b090b-20250830") == (8, 0) and V.vtuple("") == ()
    assert V.older("0.6.4", "0.7.1") and not V.older("0.7.1", "0.7.1") and not V.older("2026.10.1", "2026.8.19")
    assert V.older("1.19.0", "1.37.0") and not V.older("", "1.0")          # an unreadable version is never "old"
    assert V.same("6.12", "6.12.0") and not V.same("6.11.0", "6.12.0")


@pytest.mark.parametrize("have,broken,expect", [
    (None, (), "PySceneDetect is missing"),
    ("0.6.4", (), "PySceneDetect 0.6.4 is too old (0.7.1 needed)"),
    ("0.7.1", ("scenedetect.output",), "PySceneDetect 0.7.1 is broken: it does not load"),
])
def test_pyscenedetect_missing_old_or_broken_is_said_in_plain_words(have, broken, expect):
    p = V.check_one(_want("scenedetect"), _readers({"scenedetect": have}, broken=broken))
    assert p is not None and p.text.startswith(expect) and p.fix == V.PIP
    assert V.check_one(_want("scenedetect"), _readers({"scenedetect": "0.7.1"})) is None


def test_programs_missing_or_too_old_and_the_ones_done_by_hand():
    rd = _readers(exes={"aria2c": ("1.19.0", r"C:\aria2\aria2c.exe"), "ffprobe": ("n8.0-3-g08a8", "x")})
    a = V.check_one(_want("aria2c"), rd)
    assert a.text == "aria2c 1.19.0 is too old (1.37.0 needed)" and a.fix == V.ARIA2C
    f = V.check_one(_want("ffmpeg"), rd)
    assert f.text.startswith("FFmpeg is missing") and f.fix == V.HAND and "winget install" in f.hand
    assert V.check_one(_want("ffprobe"), rd) is None


def test_pytorch_must_use_the_graphics_card_when_there_is_one():
    w = _want("torch")
    assert V.check_one(w, _readers({"torch": "2.11.0+cpu"})) is None                    # no NVIDIA card: fine
    p = V.check_one(w, _readers({"torch": "2.11.0+cpu"}, gpu="NVIDIA GeForce RTX 3060"))
    assert "cannot use the graphics card" in p.text and p.fix == V.PIP                    # setup installs CUDA's
    p = V.check_one(w, _readers({"torch": "2.11.0+cu130"}, gpu="RTX", cuda=(False, "PyTorch does not see the "
                                                                                    "graphics card (the NVIDIA driver "
                                                                                    "may be too old for it)")))
    assert p.fix == V.HAND and "NVIDIA driver" in p.hand
    assert V.check_one(w, _readers({"torch": "2.11.0+cu130"}, gpu="RTX")) is None


def test_the_tool_from_another_folder_and_its_own_packages(tmp_path):
    w = _want("match_cuts")
    p = V.check_one(w, _readers({"match_cuts": "0.1.0"}, homes={"match_cuts": str(tmp_path / "old")}))
    # only said, never "fixed" by itself: a setup_app run from a second copy (a git worktree) must not repoint it
    assert "installed from another folder" in p.text and p.fix == V.HAND and "pip install -e" in p.hand
    assert V.pip_plan([p], [w]) == ([], [p])
    assert V.check_one(w, _readers({"match_cuts": "0.1.0"}, homes={"match_cuts": w.home})) is None
    missing = V.check_one(w, _readers({}))
    assert missing.fix == V.PIP and V.pip_plan([missing], [w])[0] == [("the matching tool", ["-e", str(V.TOOL_DIR)])]
    py = tmp_path / "pyproject.toml"
    py.write_text('[project]\ndependencies = ["numpy>=1.26", "opencv-contrib-python-headless>=4.9", "psutil"]\n',
                  encoding="utf-8")
    tw = {x.dist: x for x in V.tool_wants(py)}
    assert tw["numpy"].least == "1.26" and tw["psutil"].least == "" and tw["numpy"].pip == ("numpy>=1.26",)
    p = V.check_one(tw["numpy"], _readers({"numpy": "1.21.0"}))
    assert p.text == "numpy 1.21.0 is too old (1.26 needed)"


def test_one_line_and_what_setup_app_installs():
    rd = _readers({"scenedetect": "0.6.4", "torch": "2.5.0+cpu", "PySide6-Essentials": "6.11.0"},
                  exes={"aria2c": ("1.37.0", "x"), "ffmpeg": ("8.0", "x"), "ffprobe": ("8.0", "x"),
                        "deno": ("2.6.7", "x")}, gpu="RTX")
    wants = [_want(k) for k in ("scenedetect", "torch", "torchaudio", "pyside6", "ffmpeg", "aria2c")]
    probs = V.check(rd, wants)
    assert [p.key for p in probs] == ["scenedetect", "torch", "torchaudio", "pyside6"]
    line = V.summary(probs)
    assert line.startswith("PySceneDetect 0.6.4 is too old (0.7.1 needed) · PyTorch 2.5.0+cpu cannot use the "
                           "graphics card") and line.endswith("· and 1 more")
    jobs, left = V.pip_plan(probs, wants, app_open=True)
    assert jobs == [("PySceneDetect", ["--no-deps", "scenedetect==0.7.1", "click", "platformdirs"]),
                    ("PyTorch, torchaudio", ["torch", "torchaudio", "--index-url", V.TORCH_INDEX])]
    assert [p.key for p in left] == ["pyside6"]                        # the window's own files: with the app closed
    jobs, left = V.pip_plan(probs, wants, app_open=False)
    assert ("PySide6", ["PySide6-Essentials==6.12.0"]) in jobs and left == []


def test_requirements_txt_says_what_the_table_says():
    req = (APP / "requirements.txt").read_text(encoding="utf-8")
    for key in ("pyside6", "yt-dlp", "psd-tools"):
        w = _want(key)
        pin = f"=={w.exact}" if w.exact else f">={w.least}"
        assert any(line.split("#")[0].replace(" ", "").lower().startswith(w.dist.lower()) and pin in line
                   for line in req.splitlines()), (w.dist, pin)


def test_the_app_reads_the_check_from_its_own_process(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        out = json.dumps({"problems": [V.Problem("scenedetect", "PySceneDetect", "PySceneDetect 0.6.4 is too old "
                                                 "(0.7.1 needed)", have="0.6.4").to_dict()]})
        return subprocess.CompletedProcess(cmd, 0, "some library warning\n" + out + "\n", "")
    monkeypatch.setattr(V.subprocess, "run", run)
    probs = V.check_in_process("py.exe")
    assert seen["cmd"][:4] == ["py.exe", "-m", "mr_app.core.versions", "--json"] and seen["cwd"] == str(APP)
    assert [p.text for p in probs] == ["PySceneDetect 0.6.4 is too old (0.7.1 needed)"]
    monkeypatch.setattr(V.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Traceback\n"
                                                                                           "ImportError: boom"))
    assert V.check_in_process("py.exe")[0].text == "the version check failed (ImportError: boom)"


class _FakeProc:
    def __init__(self, lines, code):
        self.stdout, self._code = iter(lines), code

    def wait(self):
        return self._code


def test_fix_now_runs_setup_app_from_the_app_and_keeps_its_log(tmp_path):
    seen, said = {}, []

    def popen(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        return _FakeProc(["found: PySceneDetect 0.6.4 is too old (0.7.1 needed)\n", "installing PySceneDetect ...\n",
                          "Successfully installed scenedetect-0.7.1\n", "done: everything is as the project expects\n"], 0)
    log = tmp_path / "logs" / "setup_app.log"
    assert V.run_setup("py.exe", log, said.append, popen) == (0, "")
    assert seen["cmd"] == ["py.exe", str(V.SETUP), "--from-app"] and seen["cwd"] == str(V.REPO)
    assert said == ["installing PySceneDetect ..."]
    text = log.read_text(encoding="utf-8")
    assert "Fix now: setup_app.py --from-app" in text and "Successfully installed scenedetect-0.7.1" in text
    assert "--- exit code 0" in text
    code, why = V.run_setup("py.exe", log, None, lambda cmd, **kw: _FakeProc(
        ["left: PySide6 6.11.0 is too old (6.12.0 needed) -- close MovieRecaps, then run: ...\n"], 2))
    assert code == 2 and why.startswith("PySide6 6.11.0 is too old")
    code, why = V.run_setup("py.exe", log, None, lambda cmd, **kw: _FakeProc(
        ["pip could not install yt-dlp -- check the internet connection and run this line again\n"], 1))
    assert code == 1 and why.startswith("pip could not install yt-dlp")

    def broken(cmd, **kw):
        raise OSError("no such file")
    assert V.run_setup("py.exe", log, None, broken) == (1, "setup_app could not start (no such file)")


# ---------------------------------------------------------------------------------------------------------------------
# setup_app.py (1.4): a broken PySceneDetect no longer crashes it -- it goes on and installs 0.7.1
# ---------------------------------------------------------------------------------------------------------------------

def _setup_app():
    spec = importlib.util.spec_from_file_location("setup_app_under_test", APP / "setup_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fake_setup(monkeypatch, tmp_path):
    """setup_app with every outside effect faked: pip only records its command (and "installs" what it was asked:
    the fake package state changes), the shortcut is not made, nothing is downloaded."""
    state = {"scenedetect": "0.7.1", "broken": True, "pips": []}
    real_find = importlib.util.find_spec

    def find_spec(name, *a, **k):
        if name.startswith("scenedetect") and state["broken"]:
            raise ModuleNotFoundError("No module named 'scenedetect.platform'")     # the laptop's broken package
        if name.startswith("scenedetect"):
            return object()
        return real_find(name, *a, **k)
    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(V, "dist_version", lambda d: state["scenedetect"] if d == "scenedetect" else "1.0")
    monkeypatch.setattr(V, "all_wants", lambda: [_want("scenedetect")])

    def run(cmd, *a, **k):
        cmd = [str(x) for x in cmd]
        if cmd[1:4] == ["-m", "pip", "install"]:
            state["pips"].append(cmd[4:])
            if "scenedetect==0.7.1" in cmd:
                state["broken"], state["scenedetect"] = False, "0.7.1"
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    from mr_app.core import shortcut
    monkeypatch.setattr(shortcut, "make", lambda *a, **k: tmp_path / "MovieRecaps.lnk")
    return state


def test_setup_app_goes_on_and_installs_when_pyscenedetect_is_broken(fake_setup, capsys):
    code = _setup_app().main([])
    out = capsys.readouterr().out
    assert code == 0, out
    assert fake_setup["pips"] == [["--no-deps", "scenedetect==0.7.1", "click", "platformdirs"]]
    assert "found: PySceneDetect 0.7.1 is broken: it does not load (ModuleNotFoundError" in out
    assert "done: everything is as the project expects" in out


def test_setup_app_updates_an_old_pyscenedetect(fake_setup, capsys):
    fake_setup.update(scenedetect="0.6.4", broken=False)
    assert _setup_app().main([]) == 0
    assert fake_setup["pips"] == [["--no-deps", "scenedetect==0.7.1", "click", "platformdirs"]]
    assert "PySceneDetect 0.6.4 is too old (0.7.1 needed)" in capsys.readouterr().out


def test_setup_app_from_the_app_leaves_the_window_files_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(V, "all_wants", lambda: [_want("pyside6")])
    monkeypatch.setattr(V, "dist_version", lambda d: "6.11.0")
    monkeypatch.setattr(V, "module_problem", lambda m: "")
    pips = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: (pips.append(cmd), subprocess.CompletedProcess(
        cmd, 0, "", ""))[1])
    assert _setup_app().main(["--from-app"]) == 2
    out = capsys.readouterr().out
    assert pips == [] and "left: PySide6 6.11.0 is too old (6.12.0 needed) -- close MovieRecaps, then run" in out
