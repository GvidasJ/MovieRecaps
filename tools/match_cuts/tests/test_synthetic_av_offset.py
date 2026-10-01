"""End-to-end reproduction of the first real run's audio failure (DESIGN §7 D8/D9), slow.

The cached 'mini' synthetic competitor's whole soundtrack is delayed by 86 ms with ffmpeg (``adelay`` +
``atrim`` to the original sample count, video stream copied): an offset of the finished mix, so the
content AND every audio switch move by 86 ms. Before D9 this failed criterion 5 on every measured
segment, made every cut a fake +2..+3 frame L-cut and warned 'the audio implies raw_in ...' per segment.

Asserted here, with the default --audio-sync raw:
* the published cutlist.audio.av_offset is 'measured', its interval contains -86 ms (+-0.5) with the
  correct sign (the competitor's audio is LATE) and the text says so;
* criterion 5 is pass_with_exceptions with exactly one run-level 'av_offset' exception (besides the
  NOT-IN-RAW placeholder), confirmed by verify's own re-measurement; every residual within +-3 ms;
* no 'audio implies raw_in' / D3 warnings, no J/L cuts (the truth has none), no AE audio twins, every
  raw_in inside its floor interval; all criteria pass;
and with --audio-sync competitor (same work dir, cached analysis): criterion 5 lags ~0 (the recreation
carries the offset), one competitor-sync audio twin per RAW segment, the mock JSX run passes.
Two more raw-sync runs: 150 ms late (beyond the +-100 ms per-segment search) and 50 ms early (the other sign).
The original mini publishing exactly 0 is asserted by tests/test_synthetic.py::test_audio_truth.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from match_cuts.common import ffmpeg_bin

pytestmark = pytest.mark.slow

TOOL_DIR = Path(__file__).resolve().parents[1]
DELAY_MS = 86.0
DELAY_SAMPLES = 4128                     # 86 ms at 48 kHz
OK = ("pass", "pass_with_exceptions")


def _decode_count(path: Path) -> int:
    res = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-f", "f32le", "-"],
                         capture_output=True, check=True)
    return len(res.stdout) // 4


def _run(py: str, comp: Path, raw: str, out: Path, work: Path, *extra: str) -> subprocess.CompletedProcess:
    cmd = [py, "-m", "match_cuts", "--competitor", str(comp), "--raw", raw, "--out", str(out), "--work", str(work), *extra]
    return subprocess.run(cmd, cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=3600)


def _shifted(src: Path, samples: int, out: Path) -> Path:
    """The competitor with its whole audio track delayed (samples > 0, adelay) or advanced (< 0, atrim + apad) by
    whole 48 kHz samples, the original sample count kept, the video stream copied."""
    n = _decode_count(src)
    af = (f"adelay=delays={samples}S:all=1,atrim=end_sample={n}" if samples > 0 else
          f"atrim=start_sample={-samples},asetpts=PTS-STARTPTS,apad=whole_len={n}")
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                    "-af", af, "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", str(out)],
                   check=True, capture_output=True)
    assert _decode_count(out) == n
    return out


@pytest.fixture(scope="module")
def delayed(synthetic_mini, tmp_path_factory) -> Path:
    return _shifted(Path(synthetic_mini["competitor"]), DELAY_SAMPLES,
                    tmp_path_factory.mktemp("av_offset") / "competitor_delayed_86ms.mp4")


@pytest.fixture(scope="module")
def runs(venv_python, synthetic_mini, delayed, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("av_offset_runs")
    work = root / "work"
    raw = _run(venv_python, delayed, synthetic_mini["raw"], root / "raw", work)
    comp = _run(venv_python, delayed, synthetic_mini["raw"], root / "competitor", work, "--audio-sync", "competitor")
    return {"raw": (raw, root / "raw"), "competitor": (comp, root / "competitor")}


def _load(runs: dict, mode: str) -> tuple[subprocess.CompletedProcess, dict, dict, dict]:
    """(process, cutlist.json, verify.json, the AE plan embedded in build_ae_project.jsx)."""
    proc, out = runs[mode]
    tail = "\n".join(proc.stdout.splitlines()[-40:]) + "\n" + "\n".join(proc.stderr.splitlines()[-40:])
    assert (out / "cutlist.json").exists() and (out / "verify.json").exists(), tail
    cl = json.loads((out / "cutlist.json").read_text())
    ver = json.loads((out / "verify.json").read_text())
    jsx = (out / "build_ae_project.jsx").read_text()
    plan = json.JSONDecoder().raw_decode(jsx, jsx.index("var PLAN = ") + len("var PLAN = "))[0]
    return proc, cl, ver, plan


def test_raw_sync_offset_measured_and_explained(runs):
    proc, cl, ver, plan = _load(runs, "raw")
    av = cl["audio"]["av_offset"]
    lo, hi = av["lag_ms_interval"]
    assert av["status"] == "measured" and av["sync_mode"] == "raw", av
    assert lo <= -DELAY_MS + 0.5 and hi >= -DELAY_MS - 0.5 and av["lag_ms"] < 0, av        # contains -86 +- 0.5
    assert "ms later than its picture, relative to RAW's own A/V sync" in av["text"]
    c5 = ver["criteria"]["c5_audio"]
    det = c5["details"]
    assert c5["status"] == "pass_with_exceptions" and det["failures"] == [], det["failures"]
    run_level = [e for e in det["exceptions"] if e.startswith("av_offset")]
    others = [e for e in det["exceptions"] if not e.startswith("av_offset")]
    assert len(run_level) == 1 and all(e.endswith("not_in_raw") for e in others), det["exceptions"]
    assert det["av_offset"]["confirmed"]
    measured = [r for r in det["segments"] if "residual_ms" in r]
    assert measured and all(abs(r["residual_ms"]) < 3.0 for r in measured), measured
    warns = [w for w in cl["warnings"] if "audio implies raw_in" in w or "keep their video phase" in w]
    assert warns == [], warns
    assert all(not (s["audio"]["in_offset_frames"] or s["audio"]["out_offset_frames"]) for s in cl["segments"])
    assert [L["id"] for L in plan["layers"] if L["kind"] == "raw_audio"] == []          # twins only for genuine J/L
    assert plan["audioSync"]["mode"] == "raw" and plan["audioSync"]["twins"] == 0
    for s in cl["segments"]:
        if s["type"] == "raw" and s.get("time_mode") != "remap" and s.get("raw_in_interval"):
            a, b = s["raw_in_interval"]
            assert a <= s["raw_in_seconds"] <= b, s["id"]
    assert all(c["status"] in OK for c in ver["criteria"].values()), {k: c["status"] for k, c in ver["criteria"].items()}
    assert proc.returncode in (0, 3), proc.stdout[-3000:]


def test_competitor_sync_reproduces_the_offset(runs):
    proc, cl, ver, plan = _load(runs, "competitor")
    av = cl["audio"]["av_offset"]
    assert av["status"] == "measured" and av["sync_mode"] == "competitor" and cl["settings"]["audio_sync"] == "competitor"
    c5 = ver["criteria"]["c5_audio"]
    det = c5["details"]
    assert c5["status"] in OK and det["failures"] == [], det["failures"]
    assert not any(e.startswith("av_offset") for e in det["exceptions"]) and det["av_offset"]["confirmed"]
    measured = [r for r in det["segments"] if "lag_ms" in r]
    assert measured and all(abs(r["lag_ms"]) < 3.0 for r in measured), measured
    twins = [L for L in plan["layers"] if L["kind"] == "raw_audio"]
    n_raw = sum(1 for s in cl["segments"] if s["type"] == "raw")
    assert len(twins) == n_raw and all(L["note"] == "competitor A/V sync" for L in twins)
    assert plan["audioSync"]["lagMs"] == pytest.approx(av["lag_ms"]) and plan["audioSync"]["twins"] == n_raw
    assert not any(L["audio"] for L in plan["layers"] if L["kind"] == "raw")       # video layers silent
    assert ver["criteria"]["c6_after_effects"]["status"] in ("pass", "not_available")
    assert all(c["status"] in OK + ("not_available",) for c in ver["criteria"].values())
    # the analysis is identical in both modes (only export settings differ)
    raw_cl = _load(runs, "raw")[1]
    assert raw_cl["segments"] == cl["segments"]
    assert np.isclose(raw_cl["audio"]["av_offset"]["lag_ms"], av["lag_ms"])


@pytest.mark.parametrize("delay_ms", [150.0, -50.0])
def test_offsets_beyond_the_search_and_early_audio(venv_python, synthetic_mini, tmp_path, delay_ms):
    """150 ms late (beyond the +-100 ms per-segment search: the prior / wide probe centres it) and 50 ms early
    (the opposite sign): the published interval contains the truth +-0.5 ms with the right sign, c5 is
    pass_with_exceptions(av_offset), no fake J/L, no D3 warnings."""
    comp = _shifted(Path(synthetic_mini["competitor"]), int(round(delay_ms * 48)), tmp_path / "competitor.mp4")
    proc = _run(venv_python, comp, synthetic_mini["raw"], tmp_path / "out", tmp_path / "work")
    _, cl, ver, plan = _load({"raw": (proc, tmp_path / "out")}, "raw")
    av = cl["audio"]["av_offset"]
    lo, hi = av["lag_ms_interval"]
    assert av["status"] == "measured" and lo <= -delay_ms + 0.5 and hi >= -delay_ms - 0.5, av
    assert ("later" if delay_ms > 0 else "earlier") in av["text"]
    det = ver["criteria"]["c5_audio"]["details"]
    assert ver["criteria"]["c5_audio"]["status"] == "pass_with_exceptions" and det["failures"] == [], det["failures"]
    assert sum(e.startswith("av_offset") for e in det["exceptions"]) == 1 and det["av_offset"]["confirmed"]
    assert all(not (s["audio"]["in_offset_frames"] or s["audio"]["out_offset_frames"]) for s in cl["segments"])
    assert [w for w in cl["warnings"] if "keep their video phase" in w or "audio implies raw_in" in w] == []
    assert plan["audioSync"]["twins"] == 0
