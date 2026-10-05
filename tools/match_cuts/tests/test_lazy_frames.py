"""fullres.LazyFrames: frames read on demand through ONE decoder (Task 8: a decoder opened per read started and ended
its frame threads every time, and with CUDA loaded that kept ~70 MB each -- 92 GB on video1's finished video)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from match_cuts import fullres


class FakeReader:
    """Frames whose pixels are their index; counts how often it is opened and closed."""
    opened = 0
    closed = 0

    def __init__(self, info):
        FakeReader.opened += 1

    def frames(self, a, b, fmt="gray"):
        for j in range(a, b):
            yield j, np.full((4, 6), j % 256, np.uint8)

    def close(self):
        FakeReader.closed += 1


def test_one_decoder_serves_every_read(monkeypatch):
    monkeypatch.setattr(fullres, "open_reader", FakeReader)
    FakeReader.opened = FakeReader.closed = 0
    lf = fullres.LazyFrames(SimpleNamespace(nb_frames=1000), keep=8, around=1)
    for i in (5, 500, 7, 900, 3, 501, 250):
        img = lf.get(i)
        assert img is not None and int(img[0, 0]) == i % 256
    lf.prefetch(100, 104)
    assert len(lf.frames) <= 8
    assert FakeReader.opened == 1                       # one decoder, seeked for every read
    lf.close()
    assert FakeReader.closed == 1 and not lf.frames
    assert lf.get(42) is not None and FakeReader.opened == 2     # reopened after close, when asked again
