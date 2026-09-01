from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx

from .bookings import fetch_bookings
from .config import Settings
from .orchestrator import Orchestrator
from .report import build_report
from .scenarios import build_scenarios, fetch_scenarios_csv


def _matrix(scenarios) -> None:
    print(f"{'scenario_id':<24} {'node':<12} {'variant':<12} {'booking_id':<12} cancellable  text")
    for s in scenarios:
        text = (s.scenario_text or "")[:60].replace("\n", " ")
        cancellable = s.booking.is_cancellable
        print(
            f"{s.scenario_id:<24} {s.node:<12} {s.variant:<12} {s.booking.booking_id:<12} "
            f"{str(cancellable):<11} {text}"
        )
    print(f"\ntotal scenarios: {len(scenarios)}")


async def _run(settings: Settings, limit: int | None) -> None:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        bookings = await fetch_bookings(settings, client)
        if not bookings:
            raise SystemExit("no bookings found in the sheet")
        scenario_rows = None
        if settings.sheet_scenarios_export_url:
            scenario_rows = await fetch_scenarios_csv(settings.sheet_scenarios_export_url, client)
    scenarios = build_scenarios(bookings, scenario_rows)
    if limit is not None:
        scenarios = scenarios[:limit]
    _matrix(scenarios)

    orchestrator = Orchestrator(settings)
    try:
        result = await orchestrator.run(scenarios)
    finally:
        await orchestrator.aclose()

    report_path = build_report(result, result.run_dir)
    passed = sum(1 for r in result.scenarios if r.grade and r.grade.passed)
    failed = sum(1 for r in result.scenarios if r.grade and not r.grade.passed)
    escalated = sum(1 for r in result.scenarios if r.escalated)
    incomplete = len(result.scenarios) - passed - failed - escalated
    print(f"\nrun {result.run_id}")
    print(f"passed={passed} failed={failed} escalated={escalated} incomplete={incomplete}")
    print(f"transcripts: {result.run_dir}/scenarios")
    print(f"report: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="headout-qa", description="Auto-QA harness for the Zendesk AI Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the scenario matrix")
    run_p.add_argument("--concurrency", type=int, help="max concurrent conversations")
    run_p.add_argument("--max-turns", type=int, help="max user turns per conversation")
    run_p.add_argument("--limit", type=int, help="only run the first N scenarios")
    run_p.add_argument("--dry-run", action="store_true", help="print the matrix and exit")

    rep_p = sub.add_parser("report", help="generate report from an existing run directory")
    rep_p.add_argument("--run-dir", required=True, help="path to a run directory")

    sub.add_parser("serve", help="start the web UI to run simulations")

    args = parser.parse_args()
    settings = Settings()

    if args.command == "serve":
        from .webapp import serve

        serve()
        return

    if args.command == "run":
        if args.concurrency:
            settings.concurrency = args.concurrency
        if args.max_turns:
            settings.max_turns = args.max_turns
        if args.dry_run:
            asyncio.run(_dry_run(settings))
            return
        asyncio.run(_run(settings, args.limit))
    elif args.command == "report":
        path = build_report(None, Path(args.run_dir))
        print(f"report: {path}")


async def _dry_run(settings: Settings) -> None:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        bookings = await fetch_bookings(settings, client)
    scenarios = build_scenarios(bookings)
    _matrix(scenarios)


if __name__ == "__main__":
    main()
