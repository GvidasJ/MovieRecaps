"""``python -m match_cuts batch`` (batch.py): every video subfolder run one after another into its own run folder named
after it, a failure never stopping the batch, and a summary -- with a stand-in for the tool (no video is analysed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from match_cuts import batch

STUB = r'''
import sys
from pathlib import Path
a = sys.argv[1:]
assert a[:2] == ["-m", "match_cuts"], a
run = Path(a[a.index("--run-dir") + 1])
comp = Path(a[a.index("--competitor") + 1])
assert "--premiere" in a and "--fast" in a
if comp.read_bytes() == b"crash":
    print("Traceback: boom", file=sys.stderr)
    sys.exit(2)
(run / "extras" / "media").mkdir(parents=True)
media = run / "extras" / "media" / "raw.mp4" if comp.read_bytes() != b"outside" else comp.parent / "raw.mp4"
url = "file://localhost/" + str(media.resolve()).replace("\\", "/").lstrip("/")
(run / "1_edit.xml").write_text(f"<xmeml><sequence><media><video><track><clipitem><file><pathurl>{url}</pathurl>"
                                "</file></clipitem></track></video></media></sequence></xmeml>", encoding="utf-8")
(run / "2_captions.srt").write_text("", encoding="utf-8")
(run / "extras" / "verify.json").write_text(
    '{"checks": {"s9_8_deliverables": {"status": "pass", "summary": "ok"}}, '
    '"criteria": {"c1_coverage": {"status": "pass", "summary": "ok"}}}', encoding="utf-8")
print("match_cuts result: FAIL")
print("Check by hand:")
print("  B-ROLL REPLACED spots: none")
print("  Captions worth a look: 1")
print("    00:00:01,000 'hi': check")
print("Run folder: " + str(run))
sys.exit(1)
'''


def _video(root: Path, name: str, comp: bytes = b"x", raw: bool = True) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "competitor.mp4").write_bytes(comp)
    if raw:
        (d / "raw.mp4").write_bytes(b"r")


def test_every_video_into_its_own_folder_a_crash_does_not_stop_the_batch_and_the_summary_says_it_all(tmp_path):
    vids, out = tmp_path / "videos", tmp_path / "out"
    _video(vids, "b second", b"crash")
    _video(vids, "a first")
    _video(vids, "c outside", b"outside")
    _video(vids, "d no raw", raw=False)
    (out / "a first").mkdir(parents=True)                       # an earlier run of the same name stays untouched
    stub = tmp_path / "stub.py"
    stub.write_text(STUB, encoding="utf-8")
    pyw = tmp_path / "py.cmd" if sys.platform == "win32" else tmp_path / "py.sh"
    if sys.platform == "win32":
        pyw.write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
    else:
        pyw.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
        pyw.chmod(0o755)
    code = batch.main([str(vids), "--out", str(out), "--work", str(tmp_path / "work"), "--python", str(pyw)])
    assert code == 1
    s = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    rows = {r["name"]: r for r in s["rows"]}
    assert list(rows) == ["a first", "b second", "c outside"]                     # in name order; "d no raw" skipped
    assert Path(rows["a first"]["run_dir"]).name == "a first-2" and rows["a first"]["pass"]
    assert rows["a first"]["hand"][1] == "  Captions worth a look: 1" and not rows["a first"]["media_outside"]
    assert not rows["b second"]["pass"] and rows["b second"]["exit"] == 2
    assert rows["c outside"]["media_outside"] and rows["c outside"]["pass"]
    md = (out / "batch_summary.md").read_text(encoding="utf-8")
    assert "**2 passed, 1 failed**" in md and "d no raw: no raw.mp4" in md and "OUTSIDE: 1 file(s)" in md
    assert (Path(rows["a first"]["run_dir"]) / "extras" / "batch_console.log").is_file()


def test_nothing_to_run_is_an_error(tmp_path):
    (tmp_path / "v").mkdir()
    assert batch.main([str(tmp_path / "v"), "--out", str(tmp_path / "o")]) == 2
