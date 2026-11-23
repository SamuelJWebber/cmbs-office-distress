"""Command-line interface for cmbs-office-distress."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .analytics import summarize
from .ingest import resolve_user_agent
from .longitudinal import compare_tapes, load_dated_tapes
from .parser import ParseResult, parse_files
from .render import (
    BADGE,
    fmt_money,
    fmt_pct,
    fmt_pp,
    fmt_signed_money,
    fmt_signed_pct,
    fmt_x,
    render_html,
    write_dashboard,
)

DEFAULT_FIXTURE = Path("tests/fixtures/synthetic_cmbs_absee.xml")
DEFAULT_SECOND_FIXTURE = Path("tests/fixtures/synthetic_cmbs_absee_2026_10.xml")
DEFAULT_FIXTURES = (DEFAULT_FIXTURE, DEFAULT_SECOND_FIXTURE)
DEFAULT_OUTPUT = Path("docs/index.html")


def _load_inputs(paths: Sequence[str | Path]) -> tuple[ParseResult, dict | None]:
    """Load input XML file(s); enable longitudinal comparison when 2+ dated periods exist."""
    tapes = load_dated_tapes(paths)
    if len(tapes) >= 2 and all(t.as_of is not None for t in tapes):
        longitudinal = compare_tapes(tapes)
        return tapes[-1], longitudinal
    return parse_files(paths), None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cmbs-distress",
        description="Parse SEC Form ABS-EE CMBS XML and build the static distress dashboard.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    default_inputs = [str(fp) for fp in DEFAULT_FIXTURES]

    b = sub.add_parser("build", help="Parse XML tape(s) and generate docs/index.html.")
    b.add_argument(
        "-i",
        "--input",
        nargs="+",
        default=default_inputs,
        help="Input ABS-EE XML file(s). Defaults to the v0.2 two-period synthetic fixtures.",
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
        default=default_inputs,
        help="Input ABS-EE XML file(s).",
    )
    ins.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON summary.")

    cmp_p = sub.add_parser(
        "compare",
        help="Compare two or more dated XML loan tapes and report longitudinal migrations.",
    )
    cmp_p.add_argument(
        "-i",
        "--input",
        nargs="+",
        default=default_inputs,
        help="Two or more dated ABS-EE XML files.",
    )
    cmp_p.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report.")

    chk = sub.add_parser(
        "check-contact",
        help="Validate CMBS_CONTACT User-Agent format without making any network call.",
    )
    chk.add_argument("--contact", default=None, help="Override CMBS_CONTACT for validation.")

    return p


def cmd_build(args: argparse.Namespace) -> int:
    result, longitudinal = _load_inputs(args.input)
    out = Path(args.output)
    rendered = render_html(result, title=args.title, badge=args.badge, longitudinal=longitudinal)
    period_note = (
        f" across {longitudinal['period_count']} periods ({longitudinal['start_as_of']} -> {longitudinal['end_as_of']})"
        if longitudinal
        else ""
    )

    if args.check:
        if not out.exists():
            print(f"STALE: {out} does not exist.", file=sys.stderr)
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != rendered:
            print(f"STALE: {out} differs from freshly rendered output.", file=sys.stderr)
            return 1
        print(f"OK: {out} is up to date ({len(result.loans)} loans{period_note}).")
        return 0

    write_dashboard(result, out, title=args.title, badge=args.badge, longitudinal=longitudinal)

    if args.summary_json:
        sp = Path(args.summary_json)
        sp.parent.mkdir(parents=True, exist_ok=True)
        payload = summarize(result.loans)
        if longitudinal is not None:
            payload["longitudinal"] = longitudinal
        sp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Wrote {out} ({len(result.loans)} loans from {result.source}{period_note}).")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    result, longitudinal = _load_inputs(args.input)
    summary = summarize(result.loans)
    if args.as_json:
        payload = dict(summary)
        if longitudinal is not None:
            payload["longitudinal"] = longitudinal
        print(json.dumps(payload, indent=2, sort_keys=True))
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
    if longitudinal is not None:
        lt = longitudinal["latest_transition"]
        ss = lt["special_servicing"]
        vel = lt["appraisal_velocity"]
        mig = lt["migration"]["combined"]
        losses = lt["losses_and_liquidations"]
        delinq = lt["delinquency"]
        print(f"Longitudinal:  {lt['prev_as_of']} -> {lt['curr_as_of']} ({lt['matched_count']} matched loans)")
        print(f"  SS inflows:  {ss['inflow_count']} loans, {fmt_money(ss['inflow_balance'])}")
        print(
            f"  Appraisals:  {fmt_signed_money(vel['valuation_change'])} ({fmt_signed_pct(vel['valuation_change_pct'])}) "
            f"| office {fmt_signed_money(vel['office']['valuation_change'])} ({fmt_pp(vel['office']['haircut_change'])} haircut)"
        )
        print(
            f"  Migration:   {mig['downgraded']['count']} downgraded ({fmt_money(mig['downgraded']['prior_balance'])}), "
            f"{mig['upgraded']['count']} upgraded, {mig['resolved']['count']} resolved"
        )
        print(
            f"  Inc. loss:   {fmt_money(losses['incremental_loss'])} across {losses['liquidation_count']} liquidation(s) "
            f"| new delinq {delinq['newly_delinquent']['count']} ({fmt_money(delinq['newly_delinquent']['balance'])})"
        )
    if result.notes:
        print("Notes:")
        for note in result.notes:
            print(f"  - {note}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    tapes = load_dated_tapes(args.input)
    try:
        report = compare_tapes(tapes)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    lt = report["latest_transition"]
    ss = lt["special_servicing"]
    vel = lt["appraisal_velocity"]
    mig = lt["migration"]["combined"]
    delinq = lt["delinquency"]
    losses = lt["losses_and_liquidations"]
    print(f"Periods:            {report['start_as_of']} -> {report['end_as_of']} ({report['period_count']} filings)")
    print(f"Balance change:     {fmt_money(lt['prev_balance'])} -> {fmt_money(lt['curr_balance'])} ({fmt_signed_money(lt['balance_change'])})")
    print(f"SS inflows:         {ss['inflow_count']} loans, {fmt_money(ss['inflow_balance'])} (office {fmt_money(ss['inflow_office_balance'])})")
    print(
        f"Appraisal velocity: {fmt_signed_money(vel['valuation_change'])} ({fmt_signed_pct(vel['valuation_change_pct'])}) "
        f"| office {fmt_signed_money(vel['office']['valuation_change'])} ({fmt_signed_pct(vel['office']['valuation_change_pct'])})"
    )
    print(
        f"Credit migration:   {mig['downgraded']['count']} downgraded ({fmt_money(mig['downgraded']['prior_balance'])}), "
        f"{mig['upgraded']['count']} upgraded, {mig['resolved']['count']} resolved"
    )
    print(
        f"Delinquency:        {delinq['newly_delinquent']['count']} new ({fmt_money(delinq['newly_delinquent']['balance'])}), "
        f"{delinq['worsened']['count']} rolled deeper, active {fmt_pct(delinq['curr_active_delinquent_share'])}"
    )
    print(
        f"Realized losses:    +{fmt_money(losses['incremental_loss'])} ({losses['liquidation_count']} liquidation, "
        f"{fmt_pct(losses['weighted_loss_severity'])} severity)"
    )
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
    if args.cmd == "compare":
        return cmd_compare(args)
    if args.cmd == "check-contact":
        return cmd_check_contact(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
