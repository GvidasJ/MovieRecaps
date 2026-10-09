"""check-all --full-size (testcases.full_size): a case whose committed RAW is a smaller copy runs on the full-size
original where that is on this machine -- output/019 (video018 at full size) lost its "insurance" ending with a fix
that worked on the smaller copy."""
import json

from match_cuts import testcases as T


def test_a_case_runs_on_its_full_size_original_only_where_it_is_here(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "REPO", tmp_path)
    d = tmp_path / "tests" / "real" / "v"
    d.mkdir(parents=True)
    for f in ("competitor.mp4", "raw.mp4"):
        (d / f).write_bytes(b"x")
    (d / "case.json").write_text(json.dumps({"full_raw": "output/018/extras/media/raw.mp4"}), encoding="utf-8")
    c = T.load(d)
    assert c.full_raw is None and T.full_size(c) is None                  # not on this machine
    big = tmp_path / "output" / "018" / "extras" / "media" / "raw.mp4"
    big.parent.mkdir(parents=True)
    big.write_bytes(b"big")
    f = T.full_size(T.load(d))
    assert f.name == "v@full" and f.raw == big and f.competitor == d / "competitor.mp4"
