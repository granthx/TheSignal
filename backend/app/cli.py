"""Command line: python -m app.cli <command>

  serve           run the website + API + scheduler
  run             build today's edition once and print a summary
  send            build/refresh today's edition and email it to subscribers who haven't had it
  preview         write today's newsletter HTML to a file (open it in a browser)
  check-sources   fetch every configured feed and report which work
  sudoku          print a puzzle (handy for testing)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")


def cmd_serve(args) -> int:
    import uvicorn

    from .main import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_run(args) -> int:
    from .config import Settings
    from .pipeline import PipelineError
    from .services import Services

    services = Services(Settings.from_env())
    try:
        ed = services.refresh()
    except PipelineError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    stats = ed["stats"]
    print(f"Edition #{ed['edition_no']} for {ed['date']}: {stats['published']} stories "
          f"({stats['cleared_the_bar']} cleared the bar of {stats['unique_stories']} unique, {stats['articles_seen']} seen), "
          f"{len(ed['reads'])} smart reads, llm={'on' if stats['llm'] else 'off'}")
    print(f"Sources: {stats['sources_ok']} ok, {stats['sources_failed']} failed")
    if ed["lead"]:
        print(f"Lead: {ed['lead']['headline']}")
    return 0


def cmd_send(args) -> int:
    from .config import Settings
    from .services import Services

    services = Services(Settings.from_env())
    counts = services.send_daily_job()
    if counts is None:
        print("Nothing sent: no live edition could be built.", file=sys.stderr)
        return 1
    print(f"sent={counts['sent']} skipped(already had it)={counts['skipped']} failed={counts['failed']}")
    return 0 if not counts["failed"] else 2


def cmd_preview(args) -> int:
    from .config import Settings
    from .demo import demo_edition
    from .digest import build_digest
    from .services import Services

    settings = Settings.from_env()
    services = Services(settings)
    edition = services.db.latest_edition() or demo_edition(settings)
    digest = build_digest(edition, settings)
    out = Path(args.out)
    out.write_text(digest.html, encoding="utf-8")
    print(f"Wrote {out} ({digest.words} words, about {digest.minutes} min read, edition {edition['date']})")
    return 0


def cmd_check_sources(args) -> int:
    from .agents.scout import ScoutAgent, build_specs
    from .config import Settings
    from .pipeline import load_yaml

    settings = Settings.from_env()
    specs = build_specs(load_yaml(settings.sources_file))
    result = ScoutAgent().scout(specs)
    width = max(len(r["name"]) for r in result.report)
    for r in result.report:
        status = f"ok   {r['count']:>3} items" if r["ok"] else f"FAIL {r['error']}"
        print(f"{r['name']:<{width}}  {status}")
    print(f"\n{len(result.ok)}/{len(specs)} sources working")
    return 0 if result.ok else 1


def cmd_sudoku(args) -> int:
    from . import sudoku

    p = sudoku.random_puzzle(args.difficulty)
    for r in range(9):
        print(" ".join(p["puzzle"][r * 9 + c].replace("0", ".") for c in range(9)))
    print(f"\n{p['difficulty']}, {p['clues']} clues")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve"); p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=8000); p.set_defaults(fn=cmd_serve)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("send").set_defaults(fn=cmd_send)
    p = sub.add_parser("preview"); p.add_argument("--out", default="newsletter-preview.html"); p.set_defaults(fn=cmd_preview)
    sub.add_parser("check-sources").set_defaults(fn=cmd_check_sources)
    p = sub.add_parser("sudoku"); p.add_argument("--difficulty", default="medium", choices=["easy", "medium", "hard"]); p.set_defaults(fn=cmd_sudoku)
    args = parser.parse_args(argv)
    _setup_logging()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
