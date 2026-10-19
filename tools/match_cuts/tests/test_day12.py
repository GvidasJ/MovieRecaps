"""Day 12 (19 October): what the tool learned from its own problems.

* darkhumor (laptop, Family Guy S10E18): the run made and checked its edit, printed "match_cuts result: PASS", then
  Windows ended the process with exit code 3 (a native library aborted while Python closed) -- the app showed FAIL
  with OpenCV's warning "[ WARN:0@720.058] ... setPreferableTarget ..." as the reason. Now a library warning is never
  the message, a crash's exit code is said in plain words, a run that finished before the crash keeps its result
  (with a note), and ``python -m match_cuts`` leaves without Python's own shutdown.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from match_cuts import batch

DARKHUMOR_STDERR = (
    "02:56:49 INFO == S8 exports\n"
    "C:\\Users\\gvida\\Desktop\\bh\\MovieRecaps\\tools\\match_cuts\\match_cuts\\align.py:124: UserWarning: The given "
    "NumPy array is not writable, and PyTorch does not support non-writable tensors.\n"
    "  wav = torch.from_numpy(np.ascontiguousarray(y16[a:b], np.float32))[None].to(dev)\n"
    "02:56:59 INFO speech map of the RAW: 2114 sounds\n"
    "[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::dnn5_v20260605::Net::Impl::setPreferableTarget "
    "Targets are not supported by the new graph engine for now\n"
    "02:57:19 INFO people: 2 faces tracked\n"
    "03:00:46 INFO == S10 report\n"
    "03:00:46 INFO    S10 report: 0.12s\n")


def test_a_library_warning_is_never_the_stop_message():
    msg = batch.stop_message(DARKHUMOR_STDERR, code=3)
    assert "WARN" not in msg and "cv::dnn" not in msg and "UserWarning" not in msg and "wav =" not in msg
    assert msg == batch.crash_words(3)
    assert "exit code 3" in msg and "aborted" in msg


def test_a_warning_after_the_real_message_does_not_hide_it():
    err = ("12:00:09 ERROR match_cuts STOPPED: x\nmatch_cuts: The inputs look wrong: the competitor is longer\n"
           "[ WARN:0@1.0] global foo.cpp:1 something\n")
    assert batch.stop_message(err, code=2) == "The inputs look wrong: the competitor is longer"
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\nValueError: bad frame\n"
    assert batch.stop_message(tb + "[ WARN:0@2.0] global y.cpp:2 z\n", code=1) == "ValueError: bad frame"


def test_crash_codes_in_plain_words():
    assert "memory access" in batch.crash_words(3221225477) and "memory access" in batch.crash_words(-1073741819)
    assert batch.crash_words(2) == "" and batch.crash_words(None) == ""
    # no message at all, a crash code: the plain words; an ERROR line too: both
    assert batch.stop_message("", code=3) == batch.crash_words(3)
    assert batch.stop_message("12:00:00 ERROR match_cuts out of frames\n", code=3).startswith(batch.crash_words(3))
    assert batch.stop_message("", code=7) == "see the log"


def test_a_run_that_finished_before_the_crash_keeps_its_result():
    ok = {"pass": True, "failures": [], "notes": []}
    out = "match_cuts result: PASS (criterion 6 not verified)\n"
    note = batch.closed_after_finish(3, out, ok)
    assert note.startswith("The edit was finished and checked") and "exit code 3" in note
    assert batch.closed_after_finish(0, out, ok) == "" and batch.closed_after_finish(1, out, ok) == ""
    assert batch.closed_after_finish(2, out, ok) == ""                       # WRONG PAIR & co: a real stop
    assert batch.closed_after_finish(3, "", ok) == ""                         # it never got to the end
    assert batch.closed_after_finish(3, out, {"pass": False, "failures": ["no verify.json (the run did not "
                                                                         "finish)"], "notes": []}) == ""


def test_python_m_match_cuts_returns_its_exit_code(tmp_path):
    """__main__ leaves with os._exit after flushing: the exit code and the printed text arrive."""
    pkg = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(pkg))
    p = subprocess.run([sys.executable, "-m", "match_cuts", "--help"], capture_output=True, text=True, env=env,
                       cwd=tmp_path, timeout=120)
    assert p.returncode == 0 and "usage" in p.stdout.lower()
    p = subprocess.run([sys.executable, "-m", "match_cuts", "--competitor", str(tmp_path / "missing.mp4"), "--raw",
                        str(tmp_path / "missing2.mp4")], capture_output=True, text=True, env=env, cwd=tmp_path,
                       timeout=120)
    assert p.returncode not in (0, 3)
