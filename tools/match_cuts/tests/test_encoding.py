"""Text files are UTF-8 on every platform.

Windows' default text encoding is cp1252, which cannot encode the arrows / dashes / ± that report.md
contains: S10 crashed there with ``UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'``.
The static scan keeps every text open / read / write / subprocess explicit; the dynamic test reproduces a
non-UTF-8 default encoding (ASCII locale, UTF-8 mode off) and runs the writers through it.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "match_cuts"
TESTS = Path(__file__).resolve().parent
TEXT_FACTORIES = ("read_text", "write_text", "FileHandler", "NamedTemporaryFile", "TemporaryFile", "TextIOWrapper")


def _binary_mode(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Constant):
        return "b" in str(node.value)
    if isinstance(node, ast.IfExp):
        return _binary_mode(node.body) and _binary_mode(node.orelse)
    return False


def _default_encoding_calls(path: Path) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        kws = {k.arg for k in n.keywords}
        if "encoding" in kws or None in kws:          # explicit, or **kwargs we cannot see into
            continue
        if name in TEXT_FACTORIES and not (name.endswith("TemporaryFile") and _binary_mode(
                next((k.value for k in n.keywords if k.arg == "mode"), ast.Constant("w+b")))):
            out.append(f"{path.name}:{n.lineno} {name}()")
        elif name == "open" and isinstance(fn, ast.Name):
            mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
            if not _binary_mode(mode):
                out.append(f"{path.name}:{n.lineno} open()")
        elif name in ("run", "Popen", "check_output", "check_call") and kws & {"text", "universal_newlines"}:
            out.append(f"{path.name}:{n.lineno} {name}(text=True)")
    return out


def test_every_text_io_call_names_its_encoding():
    bad = [b for f in sorted(PKG.rglob("*.py")) for b in _default_encoding_calls(f)]
    assert not bad, "text I/O with the platform default encoding (cp1252 on Windows): " + ", ".join(bad)


_SCRIPT = textwrap.dedent(r'''
    import json, locale, logging, sys
    from pathlib import Path
    enc = locale.getpreferredencoding(False)
    if "utf" in enc.lower().replace("-", ""):
        print("SKIP " + enc)
        raise SystemExit(0)
    try:
        "\u2192".encode(enc)
        print("SKIP " + enc + " encodes the arrow")
        raise SystemExit(0)
    except UnicodeEncodeError:
        pass
    tmp, tests = Path(sys.argv[1]), sys.argv[2]
    sys.path.insert(0, tests)
    from match_cuts import cli, common, report
    import test_report

    cli._tolerant_console()
    text = "frames 10\u201320 \u2192 \u00b10.5 ms"
    common.atomic_write_text(tmp / "t.txt", text)
    common.atomic_write_text(tmp / "t.json", json.dumps({"k": text}, ensure_ascii=False))
    assert common.load_json(tmp / "t.json")["k"] == text
    common.setup_logging(False, tmp / "work" / "match_cuts.log")
    logging.getLogger(common.LOG_NAME).warning("console + file: %s", text)
    dl = common.DecisionLog(tmp / "decisions.jsonl")
    dl.record("S10", "test", note=text)
    dl.close()
    assert common.load_decisions(tmp / "decisions.jsonl")[0]["note"] == text
    ctx = test_report.make_ctx(tmp)
    report.write_report(ctx, tmp / "report.md")
    print("console:", text)
    print("OK " + enc)
''')


def test_writers_survive_a_non_utf8_default_encoding(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "LC_CTYPE", "LANG", "LC_ALL")}
    env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
               PYTHONPATH=os.pathsep.join([str(PKG.parent)] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
    res = subprocess.run([sys.executable, "-c", _SCRIPT, str(tmp_path), str(TESTS)], capture_output=True, env=env,
                         timeout=300)
    out = res.stdout.decode("ascii", "replace")
    err = res.stderr.decode("ascii", "replace")
    if out.startswith("SKIP"):
        pytest.skip(f"cannot force a non-UTF-8 default encoding here ({out.strip()})")
    assert res.returncode == 0, err[-3000:]
    assert "OK " in out and "Logging error" not in err, err[-3000:]
    text = "frames 10–20 → ±0.5 ms"
    assert (tmp_path / "t.txt").read_bytes() == text.encode("utf-8")
    assert text in (tmp_path / "work" / "match_cuts.log").read_bytes().decode("utf-8")
    md = (tmp_path / "report.md").read_bytes().decode("utf-8")       # strict: raises unless valid UTF-8
    assert any(ord(c) > 127 for c in md), "the fixture report should exercise non-ASCII text"
