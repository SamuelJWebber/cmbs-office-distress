"""Command-line interface for cmbs-office-distress."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .analytics import summarize
from .ingest import resolve_user_agent
from .parser import parse_files
from .render import BADGE, fmt_money, fmt_pct, fmt_x, render_html, write_dashboard

DEFAULT_FIXTURE = Path("tests/fixtures/synthetic_cmbs_absee.xml")
DEFAULT_OUTPUT = Path("docs/index.html")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cmbs-distress",
        description="Parse SEC Form ABS-EE CMBS XML and build the static distress dashboard.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Parse XML tape(s) and generate docs/index.html.")
    b.add_argument(
        "-i",
        "--input",
        nargs="+",
        default=[str(DEFAULT_FIXTURE)],
        help="Input ABS-EE XML file(s). Defaults to the synthetic fixture.",
    )
    b.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output HTML path (default: docs/index.html).",
    )
    b.add_argument("--title", default="CMBS Office Distress Monitor", help="HTML page title.")
    b.add_argument("--badge", default=BADGE, help="Evidence banner text.")
    b.add_argument("--summary-json", default=None, help="Optional path to write summary JSON.")
    b.add_argument(
        "--check",
        action="store_true",
        help="Do not write output; exit 1 if the existing output file is missing or stale.",
    )

    ins = sub.add_parser("inspect", help="Print pool summary metrics from XML tape(s).")
    ins.add_argument(
        "-i",
        "--input",
        nargs="+",
        default=[str(DEFAULT_FIXTURE)],
        help="Input ABS-EE XML file(s).",
    )
    ins.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON summary.")

    chk = sub.add_parser(
        "check-contact",
        help="Validate CMBS_CONTACT User-Agent format without making any network call.",
    )
    chk.add_argument("--contact", default=None, help="Override CMBS_CONTACT for validation.")

    return p


def cmd_build(args: argparse.Namespace) -> int:
    result = parse_files(args.input)
    out = Path(args.output)
    rendered = render_html(result, title=args.title, badge=args.badge)

    if args.check:
        if not out.exists():
            print(f"STALE: {out} does not exist.", file=sys.stderr)
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != rendered:
            print(f"STALE: {out} differs from freshly rendered output.", file=sys.stderr)
            return 1
        print(f"OK: {out} is up to date ({len(result.loans)} loans).")
        return 0

    write_dashboard(result, out, title=args.title, badge=args.badge)

    if args.summary_json:
        sp = Path(args.summary_json)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(summarize(result.loans), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Wrote {out} ({len(result.loans)} loans from {result.source}).")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    result = parse_files(args.input)
    summary = summarize(result.loans)
    if args.as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    ov = summary["overall"]
    of = summary["office"]
    wall = summary["maturity_wall"]
    d = summary["distress"]
    print(f"Source:        {result.source}")
    print(f"As of:         {result.as_of.isoformat() if result.as_of else 'unknown'}")
    print(f"Pool:          {ov['loans']} loans, {fmt_money(ov['balance'])} balance")
    print(f"Office:        {of['loans']} loans, {fmt_money(of['balance'])} balance, wtd DSCR {fmt_x(of['wtd_dscr'])}")
    print(f"Maturity wall: {fmt_money(wall['balance'])} ({fmt_pct(wall['share'])}) across {wall['count']} loans (2026-2028)")
    print(f"Haircut:       pool {fmt_pct(summary['pool_haircut']['haircut'])}, office {fmt_pct(summary['office_haircut']['haircut'])}")
    print(f"Distressed:    {fmt_pct(d['any']['share'])} any flag | {fmt_pct(d['special_servicing']['share'])} special servicing")
    print(f"Realized loss: {fmt_money(ov['realized_loss'])}")
    if result.notes:
        print("Notes:")
        for note in result.notes:
            print(f"  - {note}")
    return 0


def cmd_check_contact(args: argparse.Namespace) -> int:
    try:
        ua = resolve_user_agent(args.contact)
    except ValueError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: SEC User-Agent format accepted ({len(ua)} chars).")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "check-contact":
        return cmd_check_contact(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
