"""The stage caches (common.Cache, stage_key; Task 10): a cached result is only ever reused by the code that computed it,
and a cache file a reset or a power loss left unreadable is computed again -- never a crash, never stale numbers."""
from __future__ import annotations

import ast
import logging
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from match_cuts import common

PKG = Path(common.__file__).resolve().parent


def _cached_stage_names() -> set[str]:
    """Every stage name a cache key is made for in the package: stage_key("<stage>", ...) and the pipeline's
    _analysis_key(ctx, "<stage>", ...) calls, and the transcripts' own caches (transcribe.audio_key)."""
    names = set()
    for p in PKG.glob("*.py"):
        for n in ast.walk(ast.parse(p.read_bytes())):
            if not isinstance(n, ast.Call):
                continue
            fn = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            pos = {"stage_key": 0, "_analysis_key": 1}.get(fn)
            if pos is not None and len(n.args) > pos and isinstance(n.args[pos], ast.Constant):
                names.add(n.args[pos].value)
    return names | {"captions_asr", "captions_score"}


def test_every_cached_stage_keys_on_the_code_that_computes_it():
    names = _cached_stage_names()
    assert {"probe", "proxy", "layout", "audio_align", "raw_index", "sparse_search", "frame_map",
            "fullres_recheck"} <= names
    missing = sorted(names - set(common.STAGE_CODE))
    assert not missing, f"cached stages without a code fingerprint (add them to common.STAGE_CODE): {missing}"
    for stage in names:
        assert common.stage_code_hash(stage), stage


def test_the_fingerprint_covers_the_modules_a_stage_imports_and_not_the_orchestration():
    fm = set(common.stage_modules("frame_map"))
    assert {"refine", "visual_match", "scoring", "temporal", "gpu", "geometry", "common"} <= fm
    assert not fm & common.NOT_STAGE_CODE                       # never the pipeline, exports, report, checks
    full = common.stage_modules("fullres_recheck")
    assert "fullres" in full and "verify" not in full           # (fullres imports verify only for check 9.9)
    assert common.stage_modules("no such stage") == [] and common.stage_code_hash("no such stage") == ""


def test_a_change_to_a_stages_code_changes_its_key_and_only_its_own(tmp_path):
    """Before Task 10 the keys held only STAGE_VERSION: a change that forgot to bump it would have reused what the old
    code computed. Now the key changes with the code itself -- on a copy of the package edited here."""
    pkg = tmp_path / "match_cuts"
    pkg.mkdir()
    for p in PKG.glob("*.py"):                                 # the modules (what the fingerprint reads)
        shutil.copyfile(p, pkg / p.name)

    def h(stage: str, root: Path) -> str:
        common._STAGE_CODE_HASH.clear()              # (computed once per process: a new copy each time here)
        common._PACKAGE_IMPORTS.clear()
        return common.stage_code_hash(stage, root)
    try:
        fm0, pr0 = h("frame_map", pkg), h("probe", pkg)
        with open(pkg / "scoring.py", "a", encoding="utf-8") as f:
            f.write("\n# a change to the scoring\n")
        assert h("frame_map", pkg) != fm0                     # refine imports scoring: its results may change
        assert h("probe", pkg) == pr0                         # the probe does not use it
        fm1 = h("frame_map", pkg)
        with open(pkg / "report.py", "a", encoding="utf-8") as f:
            f.write("\n# a change to the report\n")
        assert h("frame_map", pkg) == fm1 and h("probe", pkg) == pr0      # orchestration: no cache is affected
        crlf = tmp_path / "crlf" / "match_cuts"               # a checkout with CRLF line ends keys the same
        shutil.copytree(pkg, crlf)
        for p in crlf.glob("*.py"):
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        assert h("frame_map", crlf) == fm1
    finally:
        common._STAGE_CODE_HASH.clear()
        common._PACKAGE_IMPORTS.clear()


def test_the_key_holds_the_fingerprint(monkeypatch):
    a = common.stage_key("frame_map", "the same inputs")
    monkeypatch.setattr(common, "stage_code_hash", lambda stage, root=None: "another version of the code")
    assert common.stage_key("frame_map", "the same inputs") != a


@pytest.mark.parametrize("damage", ["truncated", "zeroed", "empty"])
def test_an_unreadable_cache_file_is_computed_again(tmp_path, caplog, damage):
    """A cache file cut short or zeroed (a reset while it was written) is a cache miss: dropped, computed again and
    logged -- before, json.loads / np.load raised and the run crashed."""
    cache = common.Cache(tmp_path)
    calls = []

    def compute_json():
        calls.append("json")
        return {"frames": [1, 2, 3]}

    def compute_npz():
        calls.append("npz")
        return {"a": np.arange(5, dtype=np.int64)}
    assert cache.json("s", "k", compute_json) == {"frames": [1, 2, 3]}
    cache.npz("s", "k", compute_npz)
    for p in (cache.path("s", "k", ".json"), cache.path("s", "k", ".npz")):
        data = p.read_bytes()
        p.write_bytes({"truncated": data[: len(data) // 2], "zeroed": bytes(len(data)), "empty": b""}[damage])
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        assert cache.json("s", "k", compute_json) == {"frames": [1, 2, 3]}
        assert np.array_equal(cache.npz("s", "k", compute_npz)["a"], np.arange(5))
    assert calls == ["json", "npz", "json", "npz"]
    assert caplog.text.count("is unreadable") == 2
    assert cache.json("s", "k", compute_json) == {"frames": [1, 2, 3]} and calls[-1] == "npz"   # written again


def test_a_file_is_on_the_disk_before_the_rename_that_publishes_it(tmp_path, monkeypatch):
    """replace_file (every cache entry and deliverable goes through it) fsyncs the new file first: after a reset the
    renamed file can no longer hold blocks of zeros that still load."""
    synced = []
    real = os.fsync
    monkeypatch.setattr(common.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    src, dst = tmp_path / "a.tmp", tmp_path / "frame_map.npz"
    src.write_bytes(b"x" * 1000)
    common.replace_file(src, dst)
    assert len(synced) == 1 and dst.read_bytes() == b"x" * 1000 and not src.exists()


def test_temporary_files_are_per_process(tmp_path, monkeypatch):
    """Two runs on one work folder never write the same temporary file (they used to share '<name>.tmp')."""
    seen = []
    real = common.replace_file
    monkeypatch.setattr(common, "replace_file", lambda a, b, retry_s=None: (seen.append(Path(a).name), real(a, b)))
    common.atomic_write_text(tmp_path / "report.md", "text")
    common.Cache(tmp_path).npz("s", "k", lambda: {"a": np.zeros(2)})
    assert len(seen) == 2 and all(f".{os.getpid()}.tmp" in n for n in seen), seen
