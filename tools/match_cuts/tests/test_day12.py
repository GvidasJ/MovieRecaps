"""Day 12 (19 October): what the tool learned from its own problems and from your fixes.

* darkhumor (laptop, Family Guy S10E18): the run passed every check and exited 3 -- the tool's own "nothing failed,
  a criterion not verified" (every Premiere-only run: no After Effects check) -- and the app and the batch took 3
  for a stop, with OpenCV's warning "[ WARN:0@720.058] ... setPreferableTarget ..." as the reason. Now 3 is a
  finished run (batch.FINISHED), and a library warning is never a stop message.
"""
from __future__ import annotations

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
    msg = batch.stop_message(DARKHUMOR_STDERR, code=2)
    assert "WARN" not in msg and "cv::dnn" not in msg and "UserWarning" not in msg and "wav =" not in msg
    assert msg == "see the log"


def test_a_warning_after_the_real_message_does_not_hide_it():
    err = ("12:00:09 ERROR match_cuts STOPPED: x\nmatch_cuts: The inputs look wrong: the competitor is longer\n"
           "[ WARN:0@1.0] global foo.cpp:1 something\n")
    assert batch.stop_message(err, code=2) == "The inputs look wrong: the competitor is longer"
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\nValueError: bad frame\n"
    assert batch.stop_message(tb + "[ WARN:0@2.0] global y.cpp:2 z\n", code=2) == "ValueError: bad frame"


def test_crash_codes_in_plain_words():
    assert "memory access" in batch.crash_words(3221225477) and "memory access" in batch.crash_words(-1073741819)
    assert batch.crash_words(2) == "" and batch.crash_words(None) == "" and batch.crash_words(3) == ""
    assert batch.stop_message("", code=3221225477) == batch.crash_words(3221225477)
    assert batch.stop_message("12:00:00 ERROR match_cuts out of frames\n", code=-1073741819).startswith(
        batch.crash_words(-1073741819))
    assert batch.stop_message("", code=7) == "see the log"


def test_exit_3_is_a_finished_run():
    """cli.py: 3 = nothing failed but a criterion could not be verified -- a Premiere-only run that passed."""
    from match_cuts import pipeline
    assert 3 in batch.FINISHED and 0 in batch.FINISHED and 1 in batch.FINISHED and 2 not in batch.FINISHED
    assert pipeline.EXIT_NOT_VERIFIED == 3


# ---------------------------------------------------------------------------------------------------------------------
# learned from video14 / video15 (day 12)
# ---------------------------------------------------------------------------------------------------------------------

def test_fuck_is_written_fu_star_k_as_you_did_and_shit_stays():
    from match_cuts import captions
    table = captions.censor_table()
    assert captions.censor_text("what the fuck", table) == "what the fu*k"
    assert captions.censor_text("you fucking red-boned", table) == "you fu*king red-boned"
    assert captions.censor_text("Fuck!", table).lower() == "fu*k!"
    assert captions.censor_text("you think this shit", table) == "you think this shit"


def test_a_repeated_action_caption_is_joined_but_words_said_twice_stay(tmp_path):
    from match_cuts import action_captions as A
    srt = tmp_path / "2_captions.srt"
    srt.write_text("1\n00:00:08,440 --> 00:00:09,560\non my ultrasound\n\n"
                   "2\n00:00:09,800 --> 00:00:10,360\n*gives the ultrasound*\n\n"
                   "3\n00:00:10,360 --> 00:00:10,920\n*gives the ultrasound*\n\n"
                   "4\n00:00:10,920 --> 00:00:11,200\nnah\n\n"
                   "5\n00:00:11,200 --> 00:00:11,500\nnah\n\n"
                   "6\n00:00:11,500 --> 00:00:12,000\n*...*\n\n"
                   "7\n00:00:12,000 --> 00:00:12,500\n*...*\n", encoding="utf-8")
    assert A.join_repeated_actions(srt) == 1
    blocks = srt.read_text(encoding="utf-8").strip().split("\n\n")
    assert len(blocks) == 6
    assert blocks[1].splitlines() == ["2", "00:00:09,800 --> 00:00:10,920", "*gives the ultrasound*"]
    assert [b.splitlines()[0] for b in blocks] == ["1", "2", "3", "4", "5", "6"]
    assert [b.splitlines()[2] for b in blocks][2:] == ["nah", "nah", "*...*", "*...*"]
    assert A.join_repeated_actions(srt) == 0                                  # nothing more to join


def test_two_actions_far_apart_are_two_captions(tmp_path):
    from match_cuts import action_captions as A
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n*laughs*\n\n2\n00:00:04,000 --> 00:00:05,000\n*laughs*\n",
                   encoding="utf-8")
    assert A.join_repeated_actions(srt) == 0


# ---------------------------------------------------------------------------------------------------------------------
# mindculpt (Rush Hour, letterboxed competitor): the person speaking cut off, the window showing nobody
# ---------------------------------------------------------------------------------------------------------------------

def _letterbox_clip():
    from tests.test_night8 import _narrow_clip
    return _narrow_clip(tx=0.0, scale=720.0 / 1920.0, band=True)       # the whole 16:9 RAW in a band: no choice


def test_the_speaker_cut_off_by_the_window_is_slid_in():
    from types import SimpleNamespace
    from fractions import Fraction
    from match_cuts import export_xml_edl as ex, speakers
    from tests.test_night8 import _view
    W, H = 1920.0, 1080.0
    clip, sim, win, box = _letterbox_clip()
    v0, v1 = _view(sim, win)
    chris = (v1 - 120.0, 150.0, v1 + 260.0, 700.0)                    # most of his face past the window's right edge
    sp = SimpleNamespace(same_shot=lambda a, b: True,
                         faces=lambda a, b: speakers.Faces("speaker", True, chris, [chris], others=[chris]))
    assert not speakers.passes(sim, sp.faces(0, 1), W, False, win)
    assert ex._speaker_cut_off([clip], sp, (W, H), win, Fraction(60), box, 250.0) == 1
    new = clip.keys[0][1]
    assert new.s == sim.s and new.tx < sim.tx                          # slid towards him, zoom kept
    assert speakers.passes(new, sp.faces(0, 1), W, False, win)
    assert abs(new.tx - sim.tx) <= ex.CUT_SLIDE_MAX * win[2] + 1e-6
    assert "moved onto the person speaking" in clip.person_note


def test_the_rule_leaves_a_window_that_shows_someone_and_an_unsure_speaker():
    from types import SimpleNamespace
    from fractions import Fraction
    from match_cuts import export_xml_edl as ex, speakers
    from tests.test_night8 import _view
    W, H = 1920.0, 1080.0
    clip, sim, win, box = _letterbox_clip()
    v0, v1 = _view(sim, win)
    chris = (v1 - 120.0, 150.0, v1 + 260.0, 700.0)
    jackie = (0.5 * (v0 + v1) - 100.0, 200.0, 0.5 * (v0 + v1) + 100.0, 600.0)     # whole in the middle
    sp = SimpleNamespace(same_shot=lambda a, b: True,
                         faces=lambda a, b: speakers.Faces("speaker", True, chris, [chris], others=[chris, jackie]))
    assert ex._speaker_cut_off([clip], sp, (W, H), win, Fraction(60), box, 250.0) == 0   # the competitor's choice
    sp.faces = lambda a, b: speakers.Faces("biggest face", True, chris, [chris], others=[chris])
    assert ex._speaker_cut_off([clip], sp, (W, H), win, Fraction(60), box, 250.0) == 0   # who speaks is unclear
    assert clip.keys[0][1] is sim


def test_the_rule_can_be_switched_off_for_one_run(monkeypatch):
    from match_cuts import export_xml_edl as ex
    assert ex.SPEAKER_CUT_OFF is True and ex._speaker_cut_off_on()
    monkeypatch.setenv("MATCH_CUTS_SPEAKER_CUT_OFF", "0")
    assert not ex._speaker_cut_off_on()
