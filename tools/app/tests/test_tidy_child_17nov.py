"""17 Nov: the clean-up round runs in a process of its own (core.tidy_child) and hands its lines back."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import archive, tidy_child  # noqa: E402


def test_the_round_in_its_own_process_gives_its_lines_back(tmp_path, monkeypatch):
    root = tmp_path / "Projects"
    root.mkdir()
    assert tidy_child.run(root, stop=lambda: False, poll_s=0.1) == []        # nothing there: nothing done, no error


def test_main_writes_what_the_round_did(tmp_path, monkeypatch):
    seen = {}

    def tidy(root, *, stop, scene_only, one_zip):
        seen.update(root=root, stopped=stop(), scene_only=scene_only, one_zip=one_zip)
        return ["a: now only a.zip (0.10 GB)"]
    monkeypatch.setattr(archive, "tidy", tidy)
    out, stop = tmp_path / "lines.json", tmp_path / "stop"
    stop.write_text("x")
    assert tidy_child.main([str(tmp_path), "1", "0", str(stop), str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["a: now only a.zip (0.10 GB)"]
    assert seen == {"root": str(tmp_path), "stopped": True, "scene_only": True, "one_zip": False}


def test_inside_the_tests_the_round_stays_in_the_apps_process(monkeypatch):
    monkeypatch.delenv(tidy_child.ENV, raising=False)
    assert not tidy_child.wanted()
    monkeypatch.setenv(tidy_child.ENV, "1")
    assert tidy_child.wanted()
