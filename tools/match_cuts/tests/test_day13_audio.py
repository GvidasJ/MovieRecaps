"""Day 13, section 2: the audio chain stays the same in the same order, only Amplify is +3.0 dB (night 11: +4.5):
Normalize All Peaks 0 dB -> Volume 0 dB -> Hard Limiter (0 / 0 / 7 ms / 100 ms / 1.00) -> Amplify +3.0 dB -> Panner 0.
The preset (assets/premiere/MovieRecaps audio chain.prfpset) was made again with Premiere's own Save Preset + Export
Preset; its values are checked in test_night11_audio.py. Here: every word the tool writes says +3.0 dB."""
from __future__ import annotations

from types import SimpleNamespace

from match_cuts import export_xml_edl as ex, report


def test_the_chain_is_the_same_with_amplify_plus_3_db():
    assert ex.AMPLIFY == "Amplify +3.0 dB" and ex.AMPLIFY_DB == 3.0
    assert ex.AUDIO_CHAIN_TEXT == ("Audio Gain (Normalize All Peaks to 0 dB), Volume 0 dB, Hard Limiter (Maximum "
                                   "Amplitude 0 dB, Input Boost 2 dB, Look-Ahead 7 ms, Release 100 ms, Link Channels "
                                   "1.00), Amplify +3.0 dB, Panner 0")
    assert ex.NOT_IN_XML.endswith("and Amplify +3.0 dB")


def test_each_a1_clip_comment_names_the_preset_with_plus_3_db():
    note = ex.audio_chain_note(12.5, 0.237, [ex.db_factor(12.5)] if hasattr(ex, "db_factor") else [4.2])
    assert "Hard Limiter + Amplify +3.0 dB" in note and "4.5" not in note
    assert note.startswith("audio chain: Audio Gain +12.50 dB (Normalize All Peaks to 0 dB")


def test_the_report_says_plus_3_db_and_not_the_old_preset():
    chain = [{"gain_db": 12.0, "read": True, "stages": 1, "label": "S01"}]
    lines = "\n".join(report._audio_chain_lines(SimpleNamespace(premiere_xml={"audio_chain": chain})))
    assert "Amplify +3.0 dB" in lines and "not your older LoudVOGOOOD: its Amplify is +4.5 dB" in lines
    assert "4.5 dB)" not in lines.replace("+4.5 dB)", "")
