"""Windows readiness (the user runs the tool on Windows with spawn workers): files held open by another program,
non-ASCII paths for images, a console that cannot encode every character, picklable pool functions."""
from __future__ import annotations

import io
import logging
import os
import pickle

import cv2
import numpy as np
import pytest

from match_cuts import common


def test_replace_file_retries_a_briefly_locked_target(tmp_path, monkeypatch):
    """A virus scanner / indexer briefly holding the old file: os.replace fails with PermissionError a few times."""
    src, dst = tmp_path / "a.tmp", tmp_path / "cutlist.csv"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    real = os.replace
    fails = [2]

    def flaky(a, b):
        if fails[0] > 0:
            fails[0] -= 1
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(a, b)
    monkeypatch.setattr(common.os, "replace", flaky)
    common.replace_file(src, dst, retry_s=5.0)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()


def test_replace_file_explains_a_file_held_open(tmp_path, monkeypatch):
    """Excel holding cutlist.csv open: after the retries the error names the file and says what to do."""
    src, dst = tmp_path / "a.tmp", tmp_path / "cutlist.csv"
    src.write_text("new", encoding="utf-8")

    def locked(a, b):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(common.os, "replace", locked)
    with pytest.raises(PermissionError, match=r"cutlist\.csv.*Close the program"):
        common.replace_file(src, dst, retry_s=0.2)


def test_atomic_write_text_uses_replace_file(tmp_path, monkeypatch):
    seen = []
    real = common.replace_file
    monkeypatch.setattr(common, "replace_file", lambda a, b, retry_s=None: (seen.append(b), real(a, b)))
    common.atomic_write_text(tmp_path / "report.md", "– ± →")
    assert seen == [tmp_path / "report.md"]
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "– ± →"


def test_write_image_handles_non_ascii_paths(tmp_path):
    """cv2.imwrite cannot open non-ASCII paths on Windows; write_image goes through Python file I/O."""
    d = tmp_path / "Žygimantas Ąžuolas" / "debug"
    img = (np.arange(48 * 64 * 3) % 251).astype(np.uint8).reshape(48, 64, 3)
    assert common.write_image(d / "layout.png", img)
    back = cv2.imdecode(np.frombuffer((d / "layout.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(back, img)
    assert not list(d.glob("*.tmp*"))


def test_read_image_handles_non_ascii_paths(tmp_path):
    """cv2.imread returns None for every file under a non-ASCII Windows path (OpenCV 5.0 here): read_image goes
    through Python file I/O. (The AE render's frames were read with cv2.imread: none could be read there.)"""
    d = tmp_path / "Vidéos Ąžuolas"
    img = (np.arange(40 * 50) % 251).astype(np.uint8).reshape(40, 50)
    assert common.write_image(d / "ae_00001.png", img)
    back = common.read_image(d / "ae_00001.png", cv2.IMREAD_GRAYSCALE)
    assert back is not None and np.array_equal(back, img)
    assert common.read_image(d / "missing.png") is None
    (d / "empty.png").write_bytes(b"")
    assert common.read_image(d / "empty.png") is None


def test_no_module_opens_image_files_through_opencv():
    """OpenCV's own file API cannot open non-ASCII Windows paths: images go through common.write_image / read_image
    (the cut images of check 9.8 failed the run under such a folder)."""
    import re
    from pathlib import Path
    pkg = Path(common.__file__).resolve().parent
    bad = [f"{p.name}:{i}" for p in sorted(pkg.glob("*.py")) for i, line in
           enumerate(p.read_text(encoding="utf-8").splitlines(), start=1)
           if re.search(r"cv2\.(imwrite|imread)\(", line) and not line.lstrip().startswith(("#", '"', "'"))
           and "``cv2." not in line]
    assert not bad, bad


def test_media_urls_of_network_and_long_paths():
    """A RAW over large_file_bytes is referenced where it lies: a network share keeps its server (file://server/...);
    file://localhost/server/... was a folder of the current drive -- the clip offline in Premiere."""
    from match_cuts.export_xml_edl import _file_url
    bs = chr(92)
    unc = bs * 2 + "nas" + bs + "videos" + bs + "raw ep 1.mp4"
    assert _file_url(unc) == "file://nas/videos/raw%20ep%201.mp4"
    assert _file_url(bs * 2 + "?" + bs + "UNC" + bs + "nas" + bs + "v" + bs + "raw.mp4") == "file://nas/v/raw.mp4"
    assert _file_url(bs * 2 + "?" + bs + "C:" + bs + "long" + bs + "raw.mp4") == "file://localhost/C%3A/long/raw.mp4"
    assert _file_url("Z:" + bs + "mapped" + bs + "raw #1.mp4") == "file://localhost/Z%3A/mapped/raw%20%231.mp4"
    assert _file_url("") == ""


def test_console_logging_never_fails_on_unencodable_characters():
    """A redirected Windows console uses the ANSI code page (cp1252): no arrows / >= signs. Such characters
    become escapes instead of '--- Logging error ---' tracebacks."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    common._tolerant_stream(stream)
    h = logging.StreamHandler(stream)
    rec = logging.LogRecord("match_cuts", logging.INFO, __file__, 1, "S05: lag ≥ 3 ms → audio line", None, None)
    h.emit(rec)
    stream.flush()
    out = raw.getvalue().decode("cp1252")
    assert "S05: lag" in out and "\\u2265" in out and "\\u2192" in out


def test_single_thread_blas_finds_the_windows_openblas_dll(tmp_path):
    """The audio stages pin numpy's OpenBLAS to one thread (thousands of tiny products; also the same arithmetic
    as on Linux). On Windows the library is a .dll in numpy.libs -- it used to be looked for as .so only."""
    from match_cuts import audio_align
    for name in ("libscipy_openblas64_-43e11ff0749b8cbe0a615c9cf6737e0e.dll", "libscipy_openblas64_-f48b.so",
                 "libopenblas.0.dylib", "msvcp140.dll"):
        (tmp_path / name).write_bytes(b"")
    names = sorted(os.path.basename(p) for p in audio_align._blas_lib_paths(str(tmp_path)))
    assert names == ["libopenblas.0.dylib", "libscipy_openblas64_-43e11ff0749b8cbe0a615c9cf6737e0e.dll",
                     "libscipy_openblas64_-f48b.so"]
    assert audio_align._blas_ctl() is not None or os.name != "posix"     # found here (Linux wheel)


def test_pool_functions_are_picklable_for_spawn_workers():
    """Spawn workers receive every item function by reference: they must be module-level (no lambdas or
    closures), including the ones waves 2-3 added (line search, temporal signature, detail score)."""
    from match_cuts import refine, visual_match as vm
    fns = [getattr(refine, n) for n in dir(refine) if n.startswith("_w_")]
    fns += [vm._search_worker, vm._line_worker, vm._index_worker, vm._invoke_chunk, vm._spawn_invoke_chunk]
    assert len(fns) >= 10
    for fn in fns:
        assert pickle.loads(pickle.dumps(fn)) is fn, fn


def test_refine_calls_pools_with_module_level_functions_only():
    """Source check: every refine ``self._map(...)`` / ``parallel_map(...)`` call passes a module-level
    ``_w_*`` / worker function by name (a lambda would silently run single-process on Windows)."""
    import ast
    import inspect
    from match_cuts import refine, visual_match as vm
    for mod in (refine, vm):
        tree = ast.parse(inspect.getsource(mod))
        top = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in ("_map", "parallel_map"):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in ("fn",):
                continue                                    # the _map plumbing itself
            assert isinstance(arg, ast.Name) and arg.id in top, (mod.__name__, ast.dump(arg)[:80])
