"""testcases.py and check_all.py (task 4): the test library's cases, a smaller copy of a big RAW, and the
check-all scorecard read from a run folder."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from match_cuts import check_all, testcases
from match_cuts.common import ffmpeg_bin

XML = """<?xml version="1.0"?><xmeml version="4"><sequence><rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
<media><video><track/></video><audio><track>
<clipitem id="a1"><start>0</start><end>120</end><in>600</in><out>720</out></clipitem>
</track></audio></media></sequence></xmeml>"""


def make_case(root, name, key=True, meta=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "competitor.mp4").write_bytes(b"c")
    (d / "raw.mp4").write_bytes(b"r")
    if key:
        (d / "answer.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello there\n", encoding="utf-8")
        (d / "answer_edit.json").write_text(json.dumps({"audio": [{"start": 0, "end": 2, "src_in": 10.0}]}),
                                            encoding="utf-8")
    if meta:
        (d / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_cases_load_the_library(tmp_path):
    make_case(tmp_path, "a", meta={"options": ["--keep-silence"], "timeline": "competitor"})
    make_case(tmp_path, "b", key=False)
    (tmp_path / "not-a-case").mkdir()
    cs = testcases.cases(root=tmp_path)
    assert [c.name for c in cs] == ["a", "b"]
    assert cs[0].has_key and cs[0].options == ["--keep-silence"] and cs[0].timeline == "competitor"
    assert not cs[1].has_key
    assert testcases.answer_timeline(cs[0]).at(0.5) == ("raw", pytest.approx(10.5))
    with pytest.raises(ValueError, match="no test case 'zzz'"):
        testcases.cases(["zzz"], root=tmp_path)


@pytest.mark.skipif(shutil.which(ffmpeg_bin()) is None, reason="needs ffmpeg")
def test_small_copy_keeps_size_and_frame_rate(tmp_path):
    src = tmp_path / "big.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3",
                    "-c:v", "libx264", "-b:v", "4M", "-c:a", "aac", "-shortest", str(src)], check=True)
    limit = src.stat().st_size // 2
    info = testcases.small_copy(src, tmp_path / "small.mp4", target=int(limit * 0.8), limit=limit)
    assert info["reencoded"] and info["bytes"] <= limit
    assert (info["width"], info["height"], info["fps"]) == (320, 240, "30000/1001")
    got = testcases.probe(tmp_path / "small.mp4")
    assert any(s.get("codec_type") == "audio" for s in got["streams"])
    same = testcases.small_copy(tmp_path / "small.mp4", tmp_path / "copy.mp4", limit=10 ** 9)   # small: copied
    assert not same["reencoded"]


def test_read_run_and_scorecard(tmp_path):
    case = testcases.load(make_case(tmp_path / "lib", "x"))
    run = tmp_path / "runs" / "x" / "001"
    (run / "extras" / "debug").mkdir(parents=True)
    (run / "1_edit.xml").write_text(XML, encoding="utf-8")
    (run / "2_captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello there\n", encoding="utf-8")
    (run / "extras" / "debug" / "captions.json").write_text(json.dumps({"mode": "voice", "count": 1}), "utf-8")
    (run / "extras" / "verify.json").write_text(json.dumps({
        "criteria": {"c1_coverage": {"status": "pass", "summary": "ok"}},
        "checks": {"s9_8_deliverables": {"status": "pass", "summary": "ok"},
                   "s9_7_determinism": {"status": "pass", "summary": "ok"}}}), encoding="utf-8")
    row = dict(check_all.read_run(case, run), case="x", seconds=75.0)
    assert check_all.hard_ok(row) and row["score"]["exact"] == 1 and row["captions_mode"] == "voice"
    card = check_all.scorecard([row], {"rows": [{"case": "x", "score": dict(row["score"], exact=0)}]})
    assert "1m15s" in card and "PASS" in card and "1/1 (100 %) +1" in card
    bad = dict(row, deliverables={"status": "fail", "failures": ["XML PERSON S14"]})
    assert not check_all.hard_ok(bad) and "XML PERSON S14" in check_all.scorecard([bad])
