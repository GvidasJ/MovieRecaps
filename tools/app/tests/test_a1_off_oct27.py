"""27 Oct: A1 (the voice) is left alone by default -- no Clip Gain, no Hard Limiter, no Amplify; the song on A2 stays.

Run: python -m pytest tools/app/tests/test_a1_off_oct27.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import a1_chain as A  # noqa: E402


def test_a1_left_alone_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MOVIERECAPS_A1_CHAIN", raising=False)
    xml = tmp_path / "1_edit.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    assert A.a1_pieces(xml) == ("", A.Fraction(0), [])
    assert A.a1_needs_fix("", xml) is False
