"""Pictures of the app's main pages (run 18, task 8: the look before -> after): New video, the Match cuts queue,
Learn, Board, History and Settings, drawn offscreen by Qt itself (never a picture of the desktop), with a few demo
rows on the queue and New video (a failed convert, a running edit, a finished one ...). Nothing of yours is written:
the settings and the app's small files are temporary copies, the kept lists are off.

usage: python tools\\app\\look_shots.py <out folder> [classic|mono]"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _env(look: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="mr_look_"))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MOVIERECAPS_KEEP_ROWS"] = "0"
    os.environ["MOVIERECAPS_NO_START_CHECKS"] = "1"
    os.environ["MOVIERECAPS_NO_AI"] = "1"
    sys.path.insert(0, str(HERE))
    from mr_app.core import settings as S
    real = S.path()
    data = json.loads(real.read_text(encoding="utf-8")) if real.is_file() else {}
    data.update(intro=False, look=look)
    (tmp / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    os.environ["MOVIERECAPS_SETTINGS"] = str(tmp / "settings.json")
    appdata = tmp / "appdata"
    appdata.mkdir()
    for name in ("learn_seen.json", "quote_state.json", "uploads_state.json", "history_log.jsonl"):
        src = S.app_dir() / name
        if src.is_file():
            shutil.copy2(src, appdata / name)
    os.environ["MOVIERECAPS_APPDATA"] = str(appdata)
    return tmp


def _demo(win) -> None:
    """A few rows to show: the queue (a failed WRONG PAIR, a running edit at 68 %, a PASS) and New video (a failed
    convert, a running download, a stopped download, a finished edit)."""
    from mr_app.core import jobs as J
    from mr_app.tools import download_tool as D
    q, dl = win.pages["match_cuts"], win.pages["download"]
    demo = Path(tempfile.mkdtemp(prefix="mr_demo_"))
    for f in ("comp.mp4", "raw.mp4"):
        (demo / f).write_bytes(b"x")
    rows = [("The Smoothest Office Flirt of All Time", "SceneProof", J.DONE,
             {"label": "WRONG PAIR", "stop": "WRONG PAIR: this competitor doesn't come from this RAW"}, 0.0),
            ("The Funniest Phone Call of All Time", "Flick707", J.RUNNING, {}, 0.68),
            ("The Most Honest Answer of All Time", "MikeMemesYou", J.DONE, {"label": "PASS"}, 1.0)]
    for name, ch, state, res, pct in rows:
        j = J.Job(name, demo / "comp.mp4", demo / "raw.mp4", channel=ch, title=name, run_dir=demo / name)
        q._add_row(j)                                                               # noqa: SLF001
        j.state, j.result, j.percent = state, res, pct
        if state == J.RUNNING:
            j.progress = "Placing every frame exactly -- 68 %, about 10 min left"
            j.progress_plain = "Placing every frame exactly"
        q.rows[j.id].refresh()
    items = [("The Funniest Background Squad of All Time", "Flick707", D.FAILED, "convert",
              "the convert check: the sound is 5690.40 s long instead of 5692.70 s"),
             ("The Fastest Personality Switch In Cartoon History", "LouLaughs", D.WORKING, "download", ""),
             ("The Most Awkward Helicopter Ride of All Time", "SceneProof", D.STOPPED, "download", "stopped by you"),
             ("The Most Willing Victim of All Time", "MikeMemesYou", D.DONE, "", "")]
    for name, ch, state, step, err in items:
        it = D.Item("https://www.instagram.com/reel/Ddm3LHisOm6/", channel=ch, title=name, name=name)
        it.state, it.failed_step, it.error = state, step, err
        if state == D.WORKING:
            it.steps["download"] = D.Step(D.S_RUN, 0.42, "downloading the RAW: 42 %, 1.2 GB of 2.9 GB")
        elif state == D.FAILED:
            it.steps["download"] = D.Step(D.S_DONE, 1.0, "")
            it.steps["convert"] = D.Step(D.S_FAIL, 0.0, "failed")
        elif state == D.DONE:
            for k in it.steps:
                it.steps[k] = D.Step(D.S_DONE, 1.0, "")
            it.result = {"label": "PASS"}
        dl.items.append(it)
        dl.rows[it.id] = D.VideoRow(dl, it)
    dl._order()                                                                     # noqa: SLF001


def main(argv: list[str]) -> int:
    out = Path(argv[0])
    look = argv[1] if len(argv) > 1 else "mono"
    out.mkdir(parents=True, exist_ok=True)
    _env(look)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from mr_app.ui import theme
    if hasattr(theme, "apply_look"):
        theme.apply_look(look)                        # before any widget module (as app.py)
    from mr_app.tools import tools
    from mr_app.ui import widgets as W
    app = QApplication([])
    fam = theme.load_fonts()
    theme.FONT = fam
    if hasattr(theme, "mono_fonts"):
        theme.mono_fonts()
        fam = theme.FONT
    app.setFont(W.font(13, 400, family=fam))
    app.setStyleSheet(theme.qss(fam))
    from mr_app.ui.main_window import MainWindow
    win = MainWindow(tools())
    win.resize(1500, 1050)
    win.show()

    def wait(s: float) -> None:
        t = time.time()
        while time.time() - t < s:
            app.processEvents()
            time.sleep(0.02)
    wait(1.0)
    _demo(win)
    wait(3.0)                                         # the board's cards, History's events
    shots = []
    for key, name in (("download", "1_new_video"), ("match_cuts", "2_match_cuts_queue"), ("learn", "3_learn"),
                      ("board", "4_board"), ("history", "5_history")):
        win.select(key)
        wait(1.2)
        p = out / f"{name}_{look}.png"
        win.grab().save(str(p))
        shots.append(p)
    from mr_app.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(win, win.settings)
    d.resize(900, 900)
    d.show()
    wait(1.0)
    p = out / f"6_settings_{look}.png"
    d.grab().save(str(p))
    shots.append(p)
    d.reject()
    for s in shots:
        print(s)
    QTimer.singleShot(0, app.quit)
    os._exit(0)                                       # (no closing(): nothing is written back)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
