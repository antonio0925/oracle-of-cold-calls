#!/usr/bin/env python
"""
Oracle agent CLI.

  python -m agent.cli run --campaign "Q3 Outbound" --list "Dial List" --dry-run
  python -m agent.cli run --campaign "Q3 Outbound" --list "Dial List" --max-preps 25
  python -m agent.cli report
  python -m agent.cli serve --interval 3600
"""
from __future__ import print_function

import argparse
import json
import logging
import sys
import time

from agent import loop, state


def _cmd_run(args):
    report = loop.run_once(
        campaign=args.campaign,
        list_name=args.list,
        max_preps=args.max_preps,
        dry_run=args.dry_run,
        allow_escalated=args.allow_escalated,
        post_sheet=not args.no_sheet,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(loop.format_report(report))
    failed = sum(1 for r in report.get("results", []) if r.get("ok") is False)
    return 1 if failed else 0


def _cmd_report(args):
    print(json.dumps(state.summarize(args.day), indent=2))
    return 0


def _cmd_serve(args):
    """Long-running mode: wake on an interval and run a cycle each time."""
    logging.info("agent serving every %ss (ctrl-c to stop)", args.interval)
    while True:
        try:
            report = loop.run_once(
                campaign=args.campaign,
                list_name=args.list,
                max_preps=args.max_preps,
                dry_run=args.dry_run,
                allow_escalated=args.allow_escalated,
                post_sheet=not args.no_sheet,
            )
            print(loop.format_report(report))
        except KeyboardInterrupt:
            print("stopped")
            return 0
        except Exception as exc:
            logging.exception("cycle failed: %s", exc)
        time.sleep(args.interval)


def build_parser():
    p = argparse.ArgumentParser(prog="agent", description="Oracle of Cold Calls — autonomous agent")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    def add_run_args(sp):
        sp.add_argument("--campaign", required=True, help="Campaign name for note headers")
        sp.add_argument("--list", help="HubSpot list name to dial from")
        sp.add_argument("--max-preps", type=int, default=50, help="Budget of prep notes per run")
        sp.add_argument("--limit", type=int, help="Only observe the first N contacts (testing)")
        sp.add_argument("--dry-run", action="store_true", help="Plan only, no writes")
        sp.add_argument("--allow-escalated", action="store_true",
                        help="Also execute transfer/finish/remove routes without sign-off")
        sp.add_argument("--no-sheet", action="store_true", help="Skip the Slack call sheet")

    run = sub.add_parser("run", help="Run one agent cycle")
    add_run_args(run)
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_cmd_run)

    rep = sub.add_parser("report", help="Summarize a day's ledger")
    rep.add_argument("--day", help="YYYY-MM-DD (default today)")
    rep.set_defaults(func=_cmd_report)

    serve = sub.add_parser("serve", help="Run continuously on an interval")
    add_run_args(serve)
    serve.add_argument("--interval", type=int, default=3600, help="Seconds between cycles")
    serve.set_defaults(func=_cmd_serve)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
