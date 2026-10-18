"""The Learn page (Match cuts tab, night 11): drop only your fixed .prproj -- the app finds the run it was made from
(the name, a project.json next to it, the media inside the project) -- or a project folder (its edit is used).

* Each drop starts the form afresh (the old output folder never sticks).
* A check card shows what was found before anything is copied: the RAW, the competitor, the tool's version, your
  fixed version and what differs (captions / cuts / position).
* The name is the next free one (video13, video14 ...).
* "Learn this one" copies the pair where the nightly LEARN step reads them (finished\\<name>\\<name>_fixed.prproj,
  output\\laptop_<name>) and adds it to learn_queue.md.
* New fixed.prproj (or *_fixed.prproj) files appearing in a project are found by themselves: "Learn from this?".
* Standing rules (always apply) and Wishes (requests.md) are saved in the shared folder with learn_queue.md;
  "Copy tonight's prompt" builds a ready-to-paste Claude Code prompt from them.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QVBoxLayout, QWidget)

from . import Tool
from ..core import learnkit, paths
from ..ui import background as BG
from ..ui import theme
from ..ui import widgets as W

WATCH_MS = 60_000


def _roots(ctx) -> list[Path]:
    """Where runs are looked for: the repo's output, this computer's projects, the shared projects (Settings) and
    the folder they are in (D:\\Shared)."""
    try:
        from ..core import settings
        shared = settings.save_root(getattr(ctx, "settings", None))
    except Exception:  # noqa: BLE001
        shared = None
    try:
        return list(learnkit.default_roots(shared))
    except Exception:  # noqa: BLE001
        return [paths.output_dir(), paths.projects_root()]


def _to_fixed(project) -> bool:
    """A learned fix's project card to Fixed -- only forwards (a card already Rendered or Uploaded stays)."""
    from ..core import board
    card = board.card_of(project)
    keys = [k for k, _ in board.COLUMNS]
    if card.error or keys.index(card.column) >= keys.index("fixed"):
        return False
    board.move(project, "fixed")
    return True


class Offer(W.Card):
    """A new fixed project found in a project: "Learn from this?"."""

    def __init__(self, page: "LearnPage", project: Path, prproj: Path):
        super().__init__(hover=False, line=theme.WINE_LINE, margins=(16, 12, 16, 12))
        self.page, self.prproj = page, prproj
        t = W.label(f"New fix in {Path(project).name}: {prproj.name} -- learn from this?")
        t.setFont(W.font(14, 700))
        t.setWordWrap(True)
        self.lay.addWidget(t)
        row = QHBoxLayout()
        yes = W.Button("Learn from this", "Primary")
        yes.clicked.connect(lambda: self.page.offer_answer(self, True))
        no = W.Button("Not now")
        no.clicked.connect(lambda: self.page.offer_answer(self, False))
        row.addWidget(yes)
        row.addWidget(no)
        row.addStretch(1)
        self.lay.addLayout(row)


class LearnPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.prproj: Path | None = None
        self.run: Path | None = None
        self.card = None
        self._token = 0
        self.form = None
        self._offers: dict[str, Offer] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        self.b_prompt = W.Button("Copy tonight's prompt", "Primary")
        self.b_prompt.setToolTip("A ready-to-paste Claude Code prompt from learn_queue.md, your standing rules and "
                                 "your wishes (requests.md)")
        self.b_prompt.clicked.connect(self.copy_prompt)
        lay.addWidget(W.SectionHeader("Learn from your fix", [self.b_prompt]))
        self.offers = QVBoxLayout()
        self.offers.setSpacing(10)
        lay.addLayout(self.offers)
        self.drop = W.DropZone("Drop your fixed .prproj here",
                               ["only the project: the run it was made from is found by itself  ·  a project folder "
                                "works too (its edit is used)"],
                               [("Choose the .prproj...", self._pick_project), ("Choose the run...", self._pick_run)],
                               big=False)
        self.drop.dropped.connect(self.take)
        lay.addWidget(self.drop)
        # the check card
        self.check = W.Card(hover=False, margins=(18, 16, 18, 16), spacing=8)
        head = QLabel("WHAT I FOUND")
        head.setFont(W.mono(10.5, 600, 14))
        head.setStyleSheet(f"color: {theme.DIM};")
        self.check.lay.addWidget(head)
        self.rows: dict[str, QLabel] = {}
        for key, name in (("prproj", "Your fixed version"), ("run", "The tool's version"), ("raw", "RAW"),
                          ("competitor", "Competitor"), ("captions", "Captions"), ("cuts", "Cuts"),
                          ("position", "Position")):
            r = QHBoxLayout()
            k = QLabel(name)
            k.setFixedWidth(150)
            k.setFont(W.font(12, 600))
            k.setStyleSheet(f"color: {theme.MUTED};")
            v = W.label("", "", wrap=True, select=True)
            v.setFont(W.font(13))
            r.addWidget(k, 0, Qt.AlignmentFlag.AlignTop)
            r.addWidget(v, 1)
            self.check.lay.addLayout(r)
            self.rows[key] = v
        self.note = W.label("", "Dim", wrap=True, select=True)
        self.check.lay.addWidget(self.note)
        self.problem = W.label("", "", wrap=True, select=True)
        self.problem.setStyleSheet(f"color: {theme.AMBER};")
        self.check.lay.addWidget(self.problem)
        self.pick_run = W.Button("Pick the run here...", "Small")
        self.pick_run.clicked.connect(self._pick_run)
        self.check.lay.addWidget(self.pick_run, 0, Qt.AlignmentFlag.AlignLeft)
        go = QHBoxLayout()
        go.setSpacing(8)
        go.addWidget(W.label("Name", "Small"))
        self.name = QLineEdit()
        self.name.setMaximumWidth(200)
        self.name.editingFinished.connect(self._rename)
        go.addWidget(self.name)
        self.b_learn = W.Button("Learn this one", "Primary")
        self.b_learn.clicked.connect(self.learn)
        go.addWidget(self.b_learn)
        go.addStretch(1)
        self.check.lay.addLayout(go)
        self.bar = W.GlideBar(3)
        self.check.lay.addWidget(self.bar)
        lay.addWidget(self.check)
        # the queue, the rules, the wishes
        self.q_count = W.label("", "Small")
        lay.addWidget(W.SectionHeader("Learn queue", [self.q_count]))
        self.queue_box = W.label("", "Muted", wrap=True, select=True)
        self.queue_box.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.queue_box)
        self.b_rules = W.Button("Save", "Small")
        self.b_rules.clicked.connect(self.save_rules)
        lay.addWidget(W.SectionHeader("Standing rules", [W.label("always apply, every night", "Small"), self.b_rules]))
        self.rules = QPlainTextEdit()
        self.rules.setPlaceholderText("Rules that always apply, one per line -- e.g. never touch my audio")
        self.rules.setFixedHeight(120)
        lay.addWidget(self.rules)
        self.b_wish = W.Button("Add to requests.md", "Small")
        self.b_wish.clicked.connect(self.add_wish)
        lay.addWidget(W.SectionHeader("Wishes", [W.label("ideas for future nights", "Small"), self.b_wish]))
        self.wish = QPlainTextEdit()
        self.wish.setPlaceholderText("An idea for a future night -- it goes into requests.md with today's date")
        self.wish.setFixedHeight(70)
        lay.addWidget(self.wish)
        self.wishes = W.label("", "Muted", wrap=True, select=True)
        lay.addWidget(self.wishes)
        self.where = W.label("", "Dim", wrap=True, select=True)
        lay.addWidget(self.where)
        lay.addStretch(1)
        self.reset()
        self._watch = QTimer(self)
        self._watch.timeout.connect(self.watch)
        self._watch.start(WATCH_MS)
        QTimer.singleShot(3000, self.watch)
        if getattr(ctx, "hub", None) is not None or not hasattr(ctx, "hub_folder"):
            QTimer.singleShot(500, self.refresh_hub)      # (else: hub_changed() comes once the window found it)

    # -- the hub (learn_queue.md, requests.md, standing_rules.md) --
    def hub(self) -> Path:
        fn = getattr(self.ctx, "hub_folder", None)
        return Path(fn()) if fn is not None else paths.projects_root()

    def hub_changed(self, _folder) -> None:
        self.refresh_hub()

    def refresh_hub(self) -> None:
        hub = self.hub()

        def work():
            from ..core import learn_hub as H
            try:
                H.ensure_wishes(hub)              # requests.md made once, with night 11's "Later" items in it
            except Exception:  # noqa: BLE001 - a read-only folder: shown without it
                pass
            items = H.queue_items(hub)
            rules = H.read_rules(hub)
            wishes = H.read_wishes(hub)
            return items, rules, wishes

        def got(r) -> None:
            items, rules, wishes = r
            if not self.rules.document().isModified():
                self.rules.setPlainText(rules)
                self.rules.document().setModified(False)
            self._show_queue(items)
            self.wishes.setText("\n".join(f"· {getattr(w, 'date', '')}  {getattr(w, 'text', w)}" for w in wishes[-12:])
                                if wishes else "No wishes yet.")
            self.where.setText(f"learn_queue.md, standing_rules.md and requests.md are in {hub}")
        BG.call(work, got, lambda e: self.where.setText(f"the shared files could not be read: {e}"))

    def _show_queue(self, items) -> None:
        if not items:
            self.queue_box.setText(f"<span style='color:{theme.DIM}'>Nothing waiting to be learned.</span>")
            self.q_count.setText("")
            return
        rows = []
        for it in items:
            rows.append(f"<span style='color:{theme.TEXT};font-weight:600'>{it.name}</span>"
                        f"<span style='color:{theme.DIM}'> · {Path(it.prproj).name if it.prproj else ''}"
                        f" · {it.date} · {it.computer}</span>")
        self.queue_box.setText("<br>".join(rows))
        self.q_count.setText(f"{len(items)} waiting")

    # -- a drop: the form starts afresh --
    def reset(self) -> None:
        """Everything of the last drop goes (night 11: the old output folder never sticks)."""
        self._token += 1
        self.form = None
        self.prproj, self.run, self.card = None, None, None
        for v in self.rows.values():
            v.setText("")
        self.rows["prproj"].setText("drop your fixed .prproj above")
        self.note.setText("")
        self.problem.setText("")
        self.pick_run.hide()
        self.name.setText("")
        self.b_learn.setEnabled(False)
        self.bar.hide()
        self.bar.jump(0.0)

    def _pick_project(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Your fixed project", str(Path.home() / "Desktop"),
                                           "Premiere project (*.prproj)")
        if f:
            self.take([Path(f)])

    def _pick_run(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "The tool's run folder (or the project folder) of that video",
                                             str(paths.output_dir()))
        if d:
            self.set_run(Path(d))

    def take(self, items: list) -> None:
        """A drop or a choice -- a .prproj (its run is found by itself), a project folder (its edit and its fix), a
        run folder: a NEW form every time (learnkit.new_form), so nothing of the last drop sticks. A single folder
        dropped while a project waits for its run is that run (rebuild)."""
        items = [Path(p) for p in items]
        prs = [p for p in items if p.is_file() and p.suffix.lower() == ".prproj"]
        if not prs and len(items) == 1 and items[0].is_dir() and self.form is not None \
                and self.form.prproj is not None and self.form.run is None:
            self.set_run(items[0])
            return
        self.reset()
        token = self._token
        roots, hub = _roots(self.ctx), self.hub()
        self.rows["prproj"].setText("reading your project and looking for its run ...")

        def work():
            return learnkit.new_form(items, roots, queue_folder=hub)
        BG.call(work, lambda f: self._show_form(f, token),
                lambda e: self.problem.setText(f"could not read the drop: {e}"))

    def set_run(self, run: Path) -> None:
        """You picked the run ("Pick the run here"): the form planned and checked again with it."""
        if self.form is None:
            self.take([run])
            return
        token = self._token
        form, hub = self.form, self.hub()
        self.problem.setText("checking what differs ...")
        self.problem.setStyleSheet(f"color: {theme.DIM};")
        BG.call(lambda: learnkit.rebuild(form, run=run, queue_folder=hub), lambda f: self._show_form(f, token),
                lambda e: self.problem.setText(f"the check failed: {e}"))

    def _rename(self) -> None:
        """A typed name: planned again (where the copies go) -- no new check needed, but the plan's notes change."""
        if self.form is None or self.form.prproj is None or self.form.run is None:
            return
        name = self.name.text().strip()
        if not name or name == self.form.name:
            return
        token = self._token
        form, hub = self.form, self.hub()
        BG.call(lambda: learnkit.rebuild(form, name=name, check=False, queue_folder=hub),
                lambda f: self._show_form(f, token, keep_card=True))

    def _show_form(self, f, token: int, keep_card: bool = False) -> None:
        if token != self._token:                          # a newer drop came meanwhile
            return
        card = self.form.card if keep_card and self.form is not None and f.card is None else f.card
        f.card = card
        self.form = f
        self.prproj, self.run, self.card = f.prproj, f.run, card
        self.name.blockSignals(True)
        self.name.setText(f.name or "")
        self.name.blockSignals(False)
        self.rows["prproj"].setText((getattr(card, "fixed_version", "") or str(f.prproj)) if f.prproj else
                                    "no fixed .prproj in what you dropped")
        self.rows["run"].setText((getattr(card, "tool_version", "") or str(f.run)) + (f"  ({f.run_how})"
                                                                                     if f.run_how else "")
                                 if f.run else "not found")
        for k in ("raw", "competitor", "captions", "cuts", "position"):
            self.rows[k].setText(getattr(card, k, "") or "—" if card is not None else "")
        notes = list(f.notes or []) + list(getattr(card, "notes", []) or [])
        ign = list(getattr(card, "ignored", []) or [])
        if ign:
            notes.append(f"Not compared (you do these by hand): {'; '.join(ign)}")
        self.note.setText("\n".join(dict.fromkeys(notes)))
        probs = list(dict.fromkeys(list(f.problems or []) + list(getattr(card, "problems", []) or [])))
        if f.prproj is not None and f.run is None and not probs:
            probs = ["I can't find the run for this project — pick it here (the tool's output folder with 1_edit.xml, "
                     "or the project folder)."]
        self.problem.setText("\n".join(probs))
        self.problem.setStyleSheet(f"color: {theme.RED if f.run is not None else theme.AMBER};")
        self.pick_run.setVisible(f.prproj is not None)
        self.pick_run.setText("Pick the run here..." if f.run is None else "Another run...")
        self.b_learn.setEnabled(bool(f.ready))

    # -- learning: copy + queue --
    def learn(self) -> None:
        f = self.form
        if f is None or not f.ready:
            return
        if self.name.text().strip() and self.name.text().strip() != f.name:
            self._rename()                                # (the typed name first; press Learn again after)
            return
        pl = f.plan
        probs = [p for p in pl.problems() if "replaced" in p]
        if probs and QMessageBox.question(self, "Replace?", "\n".join(probs) + "\n\nReplace them?") != \
                QMessageBox.StandardButton.Yes:
            return
        self.b_learn.setEnabled(False)
        self.bar.show()
        self.bar.state("running")
        prog = {"v": 0.0}
        hub = self.hub()
        item = learnkit.queue_item(pl, f.card)
        prproj = f.prproj
        project = f.project_folder

        def work():
            return learnkit.copy_and_queue(pl, lambda a, b: prog.__setitem__("v", a / b if b else 1.0), hub=hub,
                                           item=item)
        t = QTimer(self)
        t.timeout.connect(lambda: self.bar.set(prog["v"]))
        t.start(150)

        def done(err: str) -> None:
            t.stop()
            fn = getattr(self.ctx, "toast", None)
            if err:
                self.bar.state("fail")
                self.b_learn.setEnabled(True)
                self.problem.setText(err)
                self.problem.setStyleSheet(f"color: {theme.RED};")
                if fn is not None:
                    fn("Not learned: " + err[:90], "fail")
                return
            self.bar.state("pass")
            self.bar.set(1.0)
            if fn is not None:
                fn(f"{pl.name} is in the learn queue ✓")
            try:
                learnkit.mark_seen(learnkit.default_state_path(), prproj)
            except Exception:  # noqa: BLE001
                pass
            self.refresh_hub()
            if project is not None:                       # the project's card to Fixed (never backwards)
                BG.call(lambda: _to_fixed(project), lambda moved: self._board_reload() if moved else None,
                        lambda _e: None)
        BG.call(work, done, lambda e: done(f"the copy failed: {e}"))

    # -- new fixes appearing in projects --
    def watch(self) -> None:
        roots = []
        try:
            from ..core import settings
            roots = [paths.projects_root(), settings.save_root(getattr(self.ctx, "settings", None))]
        except Exception:  # noqa: BLE001
            roots = [paths.projects_root()]
        hub = self.hub()

        def work():
            return list(learnkit.new_fixed(roots, learnkit.default_state_path(), queue_folder=hub) or [])

        def got(found) -> None:
            for project, prproj in found:
                key = str(prproj)
                if key in self._offers:
                    continue
                o = Offer(self, Path(project), Path(prproj))
                self._offers[key] = o
                self.offers.addWidget(o)
                fn = getattr(self.ctx, "toast", None)
                if fn is not None:
                    fn(f"New fix found in {Path(project).name} -- Learn from this?", "info")
            self._badge()
        BG.call(work, got, lambda _e: None)

    def offer_answer(self, offer: Offer, yes: bool) -> None:
        self._offers.pop(str(offer.prproj), None)
        offer.setParent(None)
        offer.deleteLater()
        if yes:
            self.take([offer.prproj])
        else:
            try:
                learnkit.mark_seen(learnkit.default_state_path(), offer.prproj)
            except Exception:  # noqa: BLE001
                pass
        self._badge()

    def _board_reload(self) -> None:
        b = (getattr(self.ctx, "pages", None) or {}).get("board")
        if b is not None and hasattr(b, "reload"):
            b.reload()

    def _badge(self) -> None:
        fn = getattr(self.ctx, "set_badge", None)
        if fn is not None:
            fn("learn", str(len(self._offers)) if self._offers else "")

    # -- the rules, the wishes, tonight's prompt --
    def save_rules(self) -> None:
        hub, text = self.hub(), self.rules.toPlainText()

        def work():
            from ..core import learn_hub as H
            return H.save_rules(hub, text)

        def done(_r) -> None:
            self.rules.document().setModified(False)
            fn = getattr(self.ctx, "toast", None)
            if fn is not None:
                fn("Standing rules saved ✓")
        BG.call(work, done, lambda e: QMessageBox.warning(self, "Not saved", e))

    def add_wish(self) -> None:
        text = self.wish.toPlainText().strip()
        if not text:
            return
        hub = self.hub()

        def work():
            from ..core import learn_hub as H
            return H.add_wish(hub, text)

        def done(_r) -> None:
            self.wish.clear()
            fn = getattr(self.ctx, "toast", None)
            if fn is not None:
                fn("Wish saved to requests.md ✓")
            self.refresh_hub()
        BG.call(work, done, lambda e: QMessageBox.warning(self, "Not saved", e))

    def copy_prompt(self) -> None:
        hub = self.hub()

        def work():
            from ..core import learn_hub as H
            return H.tonight_prompt(hub, paths.repo())

        def done(text: str) -> None:
            QApplication.clipboard().setText(text)
            fn = getattr(self.ctx, "toast", None)
            if fn is not None:
                fn("Tonight's prompt copied ✓ -- paste it into Claude Code")
        BG.call(work, done, lambda e: QMessageBox.warning(self, "Could not build it", e))


class LearnTool(Tool):
    key = "learn"
    title = "Learn"

    def page(self, ctx) -> QWidget:
        return LearnPage(ctx)
