"""A video project's steps on the command line (night 10) -- each one on its own, the same code the app's tabs run
(mr_app.core.flow), every step writing its state into the project's project.json (part 2's project board reads it).

    .venv\\Scripts\\python tools\\app\\steps.py new COMPETITOR_LINK RAW_LINK_OR_FILE [--channel LouLaughs] [--title "..."] [--edit]
    .venv\\Scripts\\python tools\\app\\steps.py download PROJECT
    .venv\\Scripts\\python tools\\app\\steps.py convert PROJECT            (or: convert VIDEO [VIDEO ...] [--keep-original])
    .venv\\Scripts\\python tools\\app\\steps.py titlecheck PROJECT [--title "..."]   (or: titlecheck VIDEO [--title "..."])
    .venv\\Scripts\\python tools\\app\\steps.py template PROJECT [--channel X] [--title "..."]
    .venv\\Scripts\\python tools\\app\\steps.py template --channel X --title "..." --out PNG
    .venv\\Scripts\\python tools\\app\\steps.py edit PROJECT [--thorough] [--speed 125]
    .venv\\Scripts\\python tools\\app\\steps.py status [PROJECT]       (no project: every project in the folder)

PROJECT is a project folder (input\\downloads\\<date>_<short name>); the folder of all projects is
input\\downloads unless the MOVIERECAPS_PROJECTS environment variable says another (part 2: D:\\Shared\\Projects).
Exit code 0 when the step worked, 1 when it failed (the reason in plain words), 2 for a wrong command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools" / "match_cuts"))


def _progress(role: str, share: float, text: str) -> None:
    print(f"  {role:10s} {100 * share:5.1f} %  {text}", flush=True)


def _project(arg: str):
    from mr_app.core import projects as P
    p = Path(arg)
    if not (p / P.FILE).is_file():
        cand = P.paths.projects_root() / arg
        if (cand / P.FILE).is_file():
            p = cand
    return P.load(p)


def _status_line(p) -> str:
    steps = p.data.get("steps") or {}
    bits = [f"{s} {steps.get(s, {}).get('state', '?')}" + (f" ({steps[s]['error']})" if steps.get(s, {}).get('error')
                                                             else "") for s in ("download", "convert", "title_check",
                                                                                "template", "edit")]
    return f"{p.folder.name}: " + ", ".join(bits)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="steps.py", description="A video project's steps, one at a time (night 10).")
    sub = ap.add_subparsers(dest="step", required=True)
    a = sub.add_parser("new", help="a new project: download, convert, title check, template (and --edit)")
    a.add_argument("competitor")
    a.add_argument("raw", help="the RAW's link or its file")
    a.add_argument("--channel", default="")
    a.add_argument("--title", default="")
    a.add_argument("--edit", action="store_true", help="then make the edit too")
    a.add_argument("--thorough", action="store_true")
    a.add_argument("--speed", type=float, default=100.0)
    for name in ("download", "edit", "status"):
        s = sub.add_parser(name)
        s.add_argument("project", nargs="?" if name == "status" else None)
        if name == "edit":
            s.add_argument("--thorough", action="store_true")
            s.add_argument("--speed", type=float, default=100.0)
    c = sub.add_parser("convert")
    c.add_argument("items", nargs="+", help="a project folder, or video files")
    c.add_argument("--keep-original", action="store_true")
    c.add_argument("--gpu", default="auto", choices=("auto", "cpu", "gpu"))
    t = sub.add_parser("titlecheck")
    t.add_argument("item", help="a project folder or the competitor's video")
    t.add_argument("--title", default=None)
    m = sub.add_parser("template")
    m.add_argument("project", nargs="?")
    m.add_argument("--channel", default=None)
    m.add_argument("--title", default=None)
    m.add_argument("--out", default=None, help="without a project: where the PNG goes")
    args = ap.parse_args(argv)
    from mr_app.core import flow
    from mr_app.core import projects as P
    try:
        if args.step == "new":
            from mr_app.core import download as D
            raw_file = Path(args.raw) if Path(args.raw).is_file() else None
            li = D.probe_link(args.competitor)
            from mr_app.core import names as NM, titles as TT   # day 13: the folder named after the title
            post = TT.post_title({**(getattr(li, "info", None) or {}), "title": li.title})
            t, src = NM.title_of({"title": args.title or "", "post_title": post})
            p = flow.new_project(args.competitor, None if raw_file else args.raw, raw_file, args.channel or None,
                                 args.title or None, name=li.title, folder=NM.folder_base(t) if t else None,
                                 names=NM.marker(src))
            if post:
                p.update(post_title=post)
            print(f"project: {p.folder}")
            res = flow.prepare(p, _progress)
            print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
            if args.edit:
                r = flow.edit_step(p, not args.thorough, args.speed)
                print(f"edit: {r.get('label')} -- {r.get('run_dir')}")
            print(_status_line(P.load(p.folder)))
        elif args.step == "download":
            p = _project(args.project)
            flow.download_step(p, _progress)
            print(_status_line(P.load(p.folder)))
        elif args.step == "convert":
            first = Path(args.items[0])
            if first.is_dir() and (first / P.FILE).is_file():
                p = _project(args.items[0])
                flow.convert_step(p, _progress, gpu=args.gpu)
                print(_status_line(P.load(p.folder)))
            else:
                from mr_app.core import convert as C
                for f in map(Path, args.items):
                    r = C.convert_file(f, delete_original=not args.keep_original, gpu=args.gpu,
                                       progress=lambda s, t="", n=f.name: _progress(n[:10], s, t))
                    print(f"{f.name}: {r.how} -> {r.path}")
        elif args.step == "titlecheck":
            item = Path(args.item)
            if item.is_dir():
                res = flow.title_check_step(_project(args.item), args.title, _progress)
            else:
                from mr_app.core import title_check as T
                rd = T.read_headline(item)
                head = rd.text if rd else ""
                chk = T.check(args.title or "", head)
                res = {"competitor_headline": head, "why_none": "" if rd else T.last_reason(),
                       "message": chk.message, "warn": chk.warn}
            print(json.dumps(res, indent=1, ensure_ascii=False))
        elif args.step == "template":
            if args.project:
                res = flow.template_step(_project(args.project), args.channel, args.title)
            else:
                from mr_app.core import channels as Ch
                if not (args.channel and args.out):
                    print("template without a project needs --channel, --title and --out", file=sys.stderr)
                    return 2
                ch = flow.find_channel(args.channel)
                if ch is None:
                    raise flow.StepError(f"no channel {args.channel!r}")
                res = flow.template_info(Ch.make_template(ch, args.title or "", Path(args.out)))
                res["png"] = args.out
            print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
        elif args.step == "edit":
            p = _project(args.project)
            r = flow.edit_step(p, not args.thorough, args.speed)
            print(f"edit: {r.get('label')} -- {r.get('run_dir')}")
            return 0 if r.get("label") in ("PASS", "OK with a note") else 1
        elif args.step == "status":
            ps = [_project(args.project)] if args.project else P.list_projects()
            for p in ps:
                print(_status_line(p))
            if not ps:
                print(f"no projects in {P.paths.projects_root()}")
        return 0
    except flow.StepError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - said, not a traceback
        print(f"FAILED: {type(e).__name__}: {getattr(e, 'plain', None) or e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
