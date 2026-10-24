"""FIND SOURCE (night 15, task 12): which movie or episode each competitor clip comes from (core.find_source).

Paste many links (one per line) or drop many files, then "Find all (N)". FIVE run side by side, the rest wait in line;
each row fills in as soon as its clip is done. One row per clip, the same columns every time: # · Clip (name, length,
where it is from) · Comes from (big: "Show — S03E02" / "Movie (year)"; small: the episode's title and what found it)
· How sure (green Sure 90 %+, yellow Check with the second guess, red Not found with why). Each row's "Torrent
search" copies the words to search ("American Dad S03E02 1080p") and opens a web search with them; "Copy all" copies
the list as text, one line a clip. Every list is kept in History; the last one is back after a restart."""
from __future__ import annotations

import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from ..core import find_source as FS
from ..core.jobs import clock
from ..ui import theme
from ..ui import widgets as W
from . import Tool

AT_ONCE = 5                                    # clips looked at side by side; the others wait in line
COLOURS = {FS.SURE: (theme.GREEN, "Sure"), FS.CHECK: (theme.AMBER, "Check"), FS.NOT_FOUND: (theme.RED, "Not found")}


class _Relay(QObject):
    """The workers' answers, handed to the page's thread."""
    answered = Signal(int, object)
    working = Signal(int, str)
    all_done = Signal(int)


def _esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ResultRow(W.Card):
    """One clip: # · Clip · Comes from · How sure · Torrent search."""

    def __init__(self, k: int, source: str, page: "FindSourcePage"):
        super().__init__(margins=(14, 10, 14, 10), spacing=4)
        self.k, self.source, self.page = k, source, page
        self.answer: FS.Answer | None = None
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(2)
        self.num = W.mono_label(f"{k}", theme.DIM, 12)
        self.clip = W.label(_short(source), None, wrap=True, select=True)
        self.clip_small = W.mono_label("", theme.DIM, 9.5)
        self.src = QLabel("…")
        self.src.setFont(W.font(15, 600))
        self.src.setWordWrap(True)
        self.src.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.src_small = W.label("waiting in line", "Dim", wrap=True)
        self.sure = QLabel("")
        self.sure.setFont(W.mono(11, 600))
        self.sure_small = W.label("", "Dim", wrap=True)
        self.tor = W.Button("Torrent search", "Small")
        self.tor.setToolTip("Copies the words to search for its RAW (\"American Dad S03E02 1080p\") and opens a web "
                            "search with them")
        self.tor.clicked.connect(self._torrent)
        self.tor.setEnabled(False)
        g.addWidget(self.num, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        g.addWidget(self.clip, 0, 1)
        g.addWidget(self.clip_small, 1, 1)
        g.addWidget(self.src, 0, 2)
        g.addWidget(self.src_small, 1, 2)
        g.addWidget(self.sure, 0, 3)
        g.addWidget(self.sure_small, 1, 3)
        g.addWidget(self.tor, 0, 4, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        g.setColumnStretch(1, 3)
        g.setColumnStretch(2, 5)
        g.setColumnStretch(3, 3)
        g.setColumnMinimumWidth(0, 22)
        self.lay.addLayout(g)

    def working(self, text: str) -> None:
        if self.answer is None:
            self.src.setText("…")
            self.src_small.setText(text)

    def show_answer(self, a: FS.Answer) -> None:
        self.answer = a
        c = a.clip
        self.clip.setText(_esc(c.name or _short(c.source)))
        bits = [x for x in (clock(c.length) if c.length else "", c.platform) if x]
        self.clip_small.setText(" · ".join(bits))
        colour, word = COLOURS.get(a.verdict, COLOURS[FS.NOT_FOUND])
        if a.title is not None:
            self.src.setText(_esc(a.comes_from()))
            small = [a.title.episode_title] if a.title.episode_title else []
            small.append(a.how)
            self.src_small.setText(_esc(" — ".join(x for x in small if x)))
        else:
            self.src.setText("—")
            self.src_small.setText("")
        pct = f" {round(100 * a.sure)} %" if a.verdict != FS.NOT_FOUND else ""
        self.sure.setText(f"{word}{pct}")
        self.sure.setStyleSheet(f"color: {colour};")
        others = [o for o in (a.others or [])[1:3] if isinstance(o, dict) and o.get("text")]
        if a.verdict == FS.CHECK and others:          # day 17: the top 3 -- the best one big, the other two small
            self.sure_small.setText(_esc("also: " + "  ·  ".join(
                f"{k}. {o['text']}" + (f" ({o['why'][:90]})" if o.get("why") else "") for k, o in
                enumerate(others, 2))))
        elif a.verdict == FS.CHECK and a.second is not None:
            self.sure_small.setText(_esc(f"2nd guess: {a.second.text()}"))
        elif a.verdict == FS.NOT_FOUND:
            self.sure_small.setText(_esc(a.reason))
        else:
            self.sure_small.setText("")
        self.tor.setEnabled(a.title is not None)

    def _torrent(self) -> None:
        a = self.answer
        if a is None or a.title is None:
            return
        words = a.title.search_words()
        QApplication.clipboard().setText(words)
        QDesktopServices.openUrl(QUrl("https://duckduckgo.com/?q=" + urllib.parse.quote(words + " torrent")))
        self.page.toast(f"Copied “{words}” — paste it into your torrent search")


def _short(source: str) -> str:
    s = source.strip()
    return Path(s).name if not s.lower().startswith("http") else s[:80]


class FindSourcePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.rows: list[ResultRow] = []
        self.answers: dict[int, FS.Answer] = {}
        self.running = False
        self.cancel = threading.Event()
        self.batch = 0
        self.relay = _Relay(self)                  # (a child of the page: deleted with it, never left to the GC)
        self.relay.answered.connect(self._answered)
        self.relay.working.connect(self._working)
        self.relay.all_done.connect(self._all_done)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(14)
        lay.addWidget(W.SectionHeader("Find source"))
        lay.addWidget(W.label("Which movie or episode each clip comes from -- only its name (and season / episode); "
                              "you download the RAW yourself. Paste links, one per line, or drop files. First an AI "
                              "watches each clip (picture and sound: NexLev, else Algrow); its guess is checked in "
                              "public title lists (TVmaze, IMDb) and by the clip's sound in your RAWs on this "
                              "computer -- Sure only when checked. No login is used.", "Muted", wrap=True))
        from ..core.ai_watch import CREDITS_NOTE
        lay.addWidget(W.label(CREDITS_NOTE, "Dim", wrap=True))
        self.box = QPlainTextEdit()
        self.box.setPlaceholderText("https://www.instagram.com/reel/DdVZePAga2I/\nhttps://www.tiktok.com/@.../video/...\n"
                                    "(or drop video files here)")
        self.box.setFixedHeight(120)
        self.box.textChanged.connect(self._count)
        self.box.setAcceptDrops(False)
        lay.addWidget(self.box)
        self.drop = W.DropZone("Drop clips here", ["many at once -- each becomes a line above"],
                               [("Choose clips...", self._pick)])
        self.drop.dropped.connect(self.add_files)
        lay.addWidget(self.drop)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.b_find = W.Button("Find all", "Primary")
        self.b_find.clicked.connect(self.find_all)
        self.b_copy = W.Button("Copy all")
        self.b_copy.clicked.connect(self.copy_all)
        self.b_stop = W.Button("Stop", "Ghost")
        self.b_stop.clicked.connect(self.stop)
        for b in (self.b_find, self.b_copy, self.b_stop):
            btns.addWidget(b)
        btns.addStretch(1)
        self.status = W.label("", "Small")
        btns.addWidget(self.status)
        lay.addLayout(btns)
        head = QGridLayout()
        head.setHorizontalSpacing(16)
        for col, (t, st) in enumerate((("#", 0), ("Clip", 3), ("Comes from", 5), ("How sure", 3), ("", 0))):
            lab = W.mono_label(t.upper(), theme.DIM, 9.5)
            head.addWidget(lab, 0, col)
            head.setColumnStretch(col, st)
        head.setColumnMinimumWidth(0, 22)
        head.setContentsMargins(14, 0, 14, 0)
        self.head = QWidget()
        self.head.setObjectName("Transparent")
        self.head.setLayout(head)
        lay.addWidget(self.head)
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        lay.addLayout(self.list)
        lay.addWidget(W.SectionHeader("History"))
        self.hist = QVBoxLayout()
        self.hist.setSpacing(6)
        lay.addLayout(self.hist)
        lay.addStretch(1)
        self._restore()
        self._count()

    # -- input -------------------------------------------------------------------------------------------------------
    def sources(self) -> list[str]:
        return FS.split_input(self.box.toPlainText())

    def _count(self) -> None:
        n = len(self.sources())
        self.b_find.setText(f"Find all ({n})" if n else "Find all")
        self.b_find.setEnabled(n > 0 and not self.running)
        self.b_copy.setEnabled(bool(self.answers))
        self.b_stop.setVisible(self.running)

    def add_files(self, paths) -> None:
        have = self.sources()
        new = [str(p) for p in paths if str(p) not in have]
        if new:
            text = self.box.toPlainText().rstrip()
            self.box.setPlainText((text + "\n" if text else "") + "\n".join(new))

    def _pick(self) -> None:
        fs, _ = QFileDialog.getOpenFileNames(self, "Clips", "", "Videos (*.mp4 *.mov *.mkv *.webm *.m4v)")
        if fs:
            self.add_files(fs)

    # -- finding -----------------------------------------------------------------------------------------------------
    def _clear_rows(self) -> None:
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows, self.answers = [], {}

    def find_all(self) -> None:
        srcs = self.sources()
        if not srcs or self.running:
            return
        self._clear_rows()
        for k, s in enumerate(srcs, 1):
            row = ResultRow(k, s, self)
            self.list.addWidget(row)
            self.rows.append(row)
        self.running, self.cancel = True, threading.Event()
        self.batch += 1
        batch, cancel, relay = self.batch, self.cancel, self.relay
        self.status.setText(f"0 of {len(srcs)} done")
        self._count()

        def work() -> None:
            lib = FS.library()
            with ThreadPoolExecutor(max_workers=AT_ONCE) as ex:
                def one(k: int, s: str) -> None:
                    if cancel.is_set():
                        return
                    relay.working.emit(k, "looking ...")
                    a = FS.find(s, lib, cancel=cancel, say=lambda t, k=k: relay.working.emit(k, t))
                    relay.answered.emit(k, a)
                for k, s in enumerate(srcs, 1):
                    ex.submit(one, k, s)
            relay.all_done.emit(batch)
        threading.Thread(target=work, daemon=True).start()

    def _working(self, k: int, text: str) -> None:
        if 0 < k <= len(self.rows):
            self.rows[k - 1].working(text)

    def _answered(self, k: int, a) -> None:
        if not 0 < k <= len(self.rows):
            return
        self.answers[k] = a
        self.rows[k - 1].show_answer(a)
        self.status.setText(f"{len(self.answers)} of {len(self.rows)} done")
        self._count()

    def _all_done(self, batch: int) -> None:
        if batch != self.batch:
            return
        self.running = False
        done = [self.answers[k] for k in sorted(self.answers)]
        if done:
            FS.save_history(done)
            self._history()
        sure = sum(1 for a in done if a.verdict == FS.SURE)
        self.status.setText(f"done: {len(done)} clip{'s' if len(done) != 1 else ''}, {sure} sure")
        self._count()

    def stop(self) -> None:
        self.cancel.set()
        self.status.setText("stopping (the clips being looked at finish first) ...")

    def busy(self) -> bool:
        return self.running

    def copy_all(self) -> None:
        text = FS.copy_all([self.answers[k] for k in sorted(self.answers)])
        if text:
            QApplication.clipboard().setText(text)
            self.toast("The list is copied ✓")

    def toast(self, text: str) -> None:
        fn = getattr(self.ctx, "toast", None)
        if fn is not None:
            fn(text)

    # -- history -----------------------------------------------------------------------------------------------------
    def _restore(self) -> None:
        h = FS.load_history()
        last = [FS.Answer.from_dict(d) for d in h.get("last") or []]
        if last:
            self.box.setPlainText("\n".join(a.clip.source for a in last))
            for k, a in enumerate(last, 1):
                row = ResultRow(k, a.clip.source, self)
                row.show_answer(a)
                self.list.addWidget(row)
                self.rows.append(row)
                self.answers[k] = a
            self.status.setText(f"the last list ({len(last)} clips)")
        self._history()

    def _history(self) -> None:
        while self.hist.count():
            it = self.hist.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        runs = FS.load_history().get("runs") or []
        if not runs:
            self.hist.addWidget(W.label("Nothing yet.", "Dim"))
            return
        for r in runs[:15]:
            answers = [FS.Answer.from_dict(d) for d in r.get("answers") or []]
            lab = W.label("", "Dim", wrap=True, select=True)
            lab.setText(_esc(r.get("at", "").replace("T", " ")) + "<br>" +
                        "<br>".join(_esc(a.line(k)) for k, a in enumerate(answers, 1)))
            self.hist.addWidget(lab)


class FindSourceTool(Tool):
    key = "find_source"
    title = "Find source"

    def page(self, ctx) -> QWidget:
        return FindSourcePage(ctx)
