"""7 Nov (your call): ONE VIDEO AT A TIME (Settings -> Speed, on by itself) -- the New video page's own logic on a plain
object (no window): a video added while another is being made waits until that one is finished (its steps, its piece
with Topaz, its edit), and while this computer runs another edit or Topaz; one that waits for YOU (a button, the
headline, which is the RAW), paused, stopped or failed never holds the others."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import jobs as J  # noqa: E402
from mr_app.tools import download_tool as D  # noqa: E402

class _Sig:
    def connect(self, _f):
        pass


class _Thread:
    started: list = []

    def __init__(self, it, stages, default):
        self.item, self.stages, self.stage = it, stages, None
        self.named, self.progress, self.done = _Sig(), _Sig(), _Sig()

    def start(self):
        _Thread.started.append((self.item.name, self.stages))


class _MC:
    def __init__(self):
        self.running, self.work, self.jobs = False, False, []

    def has_work(self):
        return self.work


class _Page:
    """The New video page's own methods on a plain object (no window)."""
    queue, _next, _turn, _in_making = D.DownloadPage.queue, D.DownloadPage._next, D.DownloadPage._turn, \
        D.DownloadPage._in_making
    _one_at_a_time, _topaz_here, _say_turns = D.DownloadPage._one_at_a_time, D.DownloadPage._topaz_here, \
        D.DownloadPage._say_turns
    _item, _display_step, _step, now_text = D.DownloadPage._item, D.DownloadPage._display_step, \
        D.DownloadPage._step, D.DownloadPage.now_text

    def __init__(self, one=True):
        self.items, self.rows, self.current, self.work, self._turn_why, self._worked = [], {}, None, [], {}, False
        self.mc, self.worker = _MC(), SimpleNamespace(busy=False)
        self.ctx = SimpleNamespace(pages={"match_cuts": self.mc, "enhance": SimpleNamespace(worker=self.worker)},
                                   settings=SimpleNamespace(one_at_a_time=one))

    def _refresh(self, _it):
        pass

    def _named(self, *_a):
        pass

    def _progress(self, *_a):
        pass

    def _done(self, *_a):
        pass

    def _order(self, *_a, **_k):
        pass

    def _counts(self):
        pass

    def _save_rows(self):
        pass


def _job(state):
    return SimpleNamespace(state=state, complete=True, roles_chosen=True)


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setenv(D.ONE_ENV, "")
    monkeypatch.setattr(D, "StageThread", _Thread)
    _Thread.started = []

    def make(one=True):
        pg = _Page(one)
        a, b = D.Item(competitor_url="c", raw_url="r", name="A"), D.Item(competitor_url="c", raw_url="r", name="B")
        a.added, b.added = 1.0, 2.0
        pg.items += [a, b]
        return pg, a, b
    return make


def test_the_next_video_waits_until_the_one_before_it_is_finished(page):
    pg, a, b = page()
    pg.queue(a, ("download",))
    pg.queue(b, ("download",))
    assert _Thread.started == [("A", ("download",))]
    assert "A is being made first" in pg.now_text(b)
    pg.current = None                                     # A downloaded ... its edit runs in the Match cuts queue
    a.state, a.job = D.SENT, _job(J.RUNNING)
    pg._next()
    assert len(_Thread.started) == 1
    a.state, a.job.state = D.DONE, J.DONE                 # A finished: B starts
    pg._next()
    assert _Thread.started[-1] == ("B", ("download",)) and not pg._turn_why.get(b.id)


def test_switched_off_the_next_video_goes_on_at_once(page):
    pg, a, b = page(one=False)
    pg.queue(a, ("download",))
    pg.queue(b, ("download",))
    pg.current = None
    a.state, a.job = D.SENT, _job(J.RUNNING)
    pg._next()
    assert _Thread.started == [("A", ("download",)), ("B", ("download",))]


@pytest.mark.parametrize("state,gate,job,running,holds", [
    (D.GATED, D.G_ENHANCE, None, False, True),            # its piece is with Topaz: the edit waits for it
    (D.GATED, D.G_HEADLINE, None, False, False),          # waits for YOU
    (D.GATED, D.G_SEND, None, False, False),
    (D.SENT, "", J.PAUSED, True, False),                  # paused by you
    (D.SENT, "", J.READY, False, False),                  # the queue was stopped
    (D.SENT, "", J.READY, True, True),                    # waits its turn in the running queue
    (D.SENT, "", None, False, True),                      # on its way into the queue
    (D.FAILED, "", None, False, False),
    (D.STOPPED, "", None, False, False)])
def test_what_holds_the_next_video(page, state, gate, job, running, holds):
    pg, a, b = page()
    a.state, a.gate, a.job, pg.mc.running = state, gate, _job(job) if job else None, running
    pg.queue(b, ("convert",))
    assert bool(_Thread.started) == (not holds)


def test_another_edit_or_topaz_on_this_computer_holds_it_and_it_says_why(page):
    pg, a, b = page()
    pg.items.remove(a)
    pg.mc.work = True
    pg.queue(b, ("download",))
    assert not _Thread.started and "Match cuts" in pg.now_text(b)
    pg.mc.work, pg.worker.busy = False, True
    pg._next()
    assert not _Thread.started and "Topaz" in pg.now_text(b)
    pg.worker.busy = False
    pg._next()
    assert _Thread.started == [("B", ("download",))]


def test_the_earlier_videos_next_step_goes_before_a_later_videos_first(page):
    pg, a, b = page()
    pg.queue(a, ("download",))
    pg.queue(b, ("download",))
    pg.current = None                                     # A downloaded: its convert is queued after B's download
    pg.queue(a, ("convert",))
    assert _Thread.started == [("A", ("download",)), ("A", ("convert",))]
