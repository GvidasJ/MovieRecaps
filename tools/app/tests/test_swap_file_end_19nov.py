"""19 Nov ("work on the notes that are left") -- The Worst Prank In The Office, 17 Nov: a 27 s RAW, and the edit
plays it to its very last frame. Three things went wrong at the END OF THE FILE, each of them leaving the normal RAW
in the timeline:

1. the edit's last clip asked for frames past the RAW's end (fixed in the tool: tests of match_cuts,
   test_past_media_19nov.py);
2. the swap's own looks -- "half a second later", "the frame after the clip" -- asked for frames the file does not
   have, and the whole swap stopped with "IndexError: frames [1620] not found";
3. the enhanced file Topaz gives back may be a frame or two shorter than the piece (Apollo makes no new frames after
   the last one: 1610 for 1611 there) -- the clip that plays the RAW's last frame then did not fit, stayed on the
   normal RAW, and the piece was sent again and again.

Now a frame the file does not have is simply "not there", and a clip a frame or two past the end of the enhanced file
plays that much earlier (``END_SHORT``)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from test_topaz_parts_18nov import _edit, _shown, movie, topaz  # noqa: E402,F401 - the fixtures too

END = [(600, 720), (4680, 4800)]                       # the second clip plays the movie to its last frame (4799)


def _frames(path: Path) -> int:
    from match_cuts.common import ffmpeg_bin
    ffprobe = str(Path(ffmpeg_bin()).with_name("ffprobe" + Path(ffmpeg_bin()).suffix))
    r = subprocess.run([ffprobe, "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(r.stdout.strip().split(",")[0])


def _topaz(p, cut: int = 0) -> dict:
    """The piece through the fake Topaz; ``cut``: that many frames taken off the end of what it gives back."""
    from match_cuts.common import ffmpeg_bin
    assert ES.cover_all(p.folder)["clips"] == 2
    rec = PJ.load(p.folder).data["enhance"]
    folder = Path(rec["folder"])
    job = E.Worker(folder).once()
    assert job.state == "done", (job.why, job.note)
    if cut:
        done, = [f for f in folder.glob("*.mp4") if "TOENHANCE" not in f.name]
        n = _frames(done)
        tmp = done.with_name("short.mp4")
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(done), "-map", "0", "-c", "copy", "-frames:v",
                        str(n - cut), str(tmp)], check=True)
        assert _frames(tmp) == n - cut
        tmp.replace(done)
    return E.watch(p.folder)


def test_a_clip_that_ends_on_the_raws_last_frame_gets_the_enhanced_picture(tmp_path, movie, topaz):  # noqa: F811
    p = _edit(tmp_path, movie, END)
    w = _topaz(p)
    assert w.get("state") == "used" and w.get("swapped"), w                # (before: "not swapped: IndexError ...")
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["swap"]["clips"] == 2 and rec["swap"]["kept"] == 0
    shown = _shown(PJ.load(p.folder), movie)
    assert [x[0] for x in shown] == ["file-enhanced"] * 2 and shown[1][2] == 4799


@pytest.mark.parametrize("cut", [1, 2])
def test_an_enhanced_file_a_frame_or_two_short_still_goes_on_every_clip(tmp_path, movie, topaz, cut,  # noqa: F811
                                                                        monkeypatch):
    monkeypatch.setenv(ES.RANGES_ENV, "0")             # the piece whole (a piece in parts must have every frame)
    p = _edit(tmp_path, movie, END)
    w = _topaz(p, cut)
    assert w.get("state") == "used" and w.get("swapped"), w
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["swap"]["clips"] == 2 and rec["swap"]["kept"] == 0          # no clip left on the normal RAW
    assert rec["swap"]["ends"]["short"] == {"clips": 1, "frames": cut}
    shown = _shown(PJ.load(p.folder), movie)
    assert [x[0] for x in shown] == ["file-enhanced"] * 2
    assert shown[0][1:] == (600, 719) and shown[1][2] in (4798, 4799)       # the first clip untouched; the last one
    #                                                                         ends on the file's last picture
    import xml.etree.ElementTree as ET
    last = list(ET.parse(str(p.folder / "edit" / "1_edit.xml")).getroot().iter("clipitem"))[-1]
    assert int(last.findtext("out")) == _frames(p.folder / "edit" / "extras" / "media" / E.PROJECT_FILE)
    assert int(last.findtext("out")) - int(last.findtext("in")) == 300     # as long as before (120 RAW frames)


def test_a_file_much_shorter_than_its_piece_is_not_stretched_over_the_clip(tmp_path, movie, topaz,  # noqa: F811
                                                                           monkeypatch):
    monkeypatch.setenv(ES.RANGES_ENV, "0")
    p = _edit(tmp_path, movie, END)
    w = _topaz(p, ES.END_SHORT + 1)
    rec = PJ.load(p.folder).data["enhance"]
    sw = rec.get("swap") or {}
    assert not (sw.get("clips") == 2 and sw.get("kept") == 0), (w, sw)     # as before: that clip is not swapped


def test_a_frame_the_file_does_not_have_is_simply_not_there(movie):  # noqa: F811
    from fractions import Fraction
    got = ES._gray(movie, Fraction(24), [4798, 4799, 4800, 4830])            # noqa: SLF001
    assert sorted(got) == [4798, 4799]
