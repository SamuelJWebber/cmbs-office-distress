"""Tests for static HTML dashboard rendering, longitudinal visuals, and escaping."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from cmbs_distress.longitudinal import compare_tapes, load_dated_tapes
from cmbs_distress.parser import Loan, ParseResult, parse_file
from cmbs_distress.render import (
    BADGE,
    render_html,
    svg_appraisal_waterfall,
    svg_distress_trajectory,
    write_dashboard,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml"
FIXTURE_P2 = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee_2026_10.xml"


def test_render_single_period_contains_required_badge_charts_heatmap_and_table():
    res = parse_file(FIXTURE)
    doc = render_html(res)

    assert doc.startswith("<!doctype html>")
    assert BADGE in doc
    assert "Fixture-proven v0.2 longitudinal prototype" in doc
    assert "CMBS_CONTACT" in doc

    # 3 inline SVG charts in single-period mode (maturity wall, distress buckets, appraisal haircut)
    assert doc.count("<svg ") == 3
    assert "Maturity wall, 2026" in doc
    assert "Distress buckets" in doc
    assert "Appraisal haircut by property type" in doc

    # Heatmap and interactive filterable table
    assert '<table class="heat">' in doc
    assert '<table id="loans">' in doc
    for control_id in ("f-metro", "f-type", "f-flag", "f-q", "loan-data"):
        assert f'id="{control_id}"' in doc

    # All 5 core metros appear
    for metro in ("New York", "San Francisco", "Chicago", "Dallas", "Boston"):
        assert metro in doc


def test_render_longitudinal_section_charts_and_tables():
    tapes = load_dated_tapes([FIXTURE, FIXTURE_P2])
    report = compare_tapes(tapes)
    doc = render_html(tapes[-1], longitudinal=report)

    # 5 inline SVGs: appraisal waterfall + distress trajectory + 3 core snapshot charts
    assert doc.count("<svg ") == 5
    assert "Monthly Migration &amp; Appraisal Velocity" in doc
    assert 'id="longitudinal"' in doc
    assert "Appraisal valuation waterfall (MoM)" in doc
    assert "Distress &amp; office haircut trajectory" in doc
    assert "DSCR &amp; occupancy credit migration matrix" in doc
    assert "Monthly loan-level transition log" in doc
    assert "Maturity-year cohort exposure" in doc
    assert "Origination vintage cohort exposure" in doc

    # Headline longitudinal numbers appear
    assert "$93.0M" in doc  # 2 new SS transfers
    assert "-$29.4M" in doc  # MoM appraisal velocity
    assert "$7.5M" in doc  # LN-008 incremental realized loss
    assert "2026-10-15" in doc and "2026-11-15" in doc


def test_render_is_deterministic_and_self_contained():
    tapes = load_dated_tapes([FIXTURE, FIXTURE_P2])
    report = compare_tapes(tapes)
    first = render_html(tapes[-1], longitudinal=report)
    second = render_html(tapes[-1], longitudinal=report)
    assert first == second

    # Must not load external scripts, stylesheets or images
    assert not re.search(r"<script[^>]+src=", first, re.IGNORECASE)
    assert not re.search(r'<link[^>]+href=["\']https?://', first, re.IGNORECASE)
    assert not re.search(r'<img[^>]+src=["\']https?://', first, re.IGNORECASE)


def test_render_escapes_html_and_json_script_breakouts_including_longitudinal():
    evil_p1 = Loan(
        asset_number="XSS-1",
        property_name='Tower </script><script>alert("xss")</script>',
        property_type_code="OF",
        property_type="Office",
        property_city="<b>New York</b>",
        property_state="NY",
        metro="New York",
        valuation_securitization=100_000_000.0,
        most_recent_valuation=80_000_000.0,
        dscr=1.30,
        occupancy=0.88,
        special_servicer_transfer_date=None,
        maturity_date=dt.date(2027, 6, 1),
        current_balance=50_000_000.0,
        realized_loss=None,
        reporting_period_end=dt.date(2026, 10, 15),
        origination_date=dt.date(2017, 5, 1),
        payment_status_code="0",
    )
    evil_p2 = Loan(
        asset_number="XSS-1",
        property_name='Tower </script><script>alert("xss")</script>',
        property_type_code="OF",
        property_type="Office",
        property_city="<b>New York</b>",
        property_state="NY",
        metro="New York",
        valuation_securitization=100_000_000.0,
        most_recent_valuation=65_000_000.0,
        dscr=0.90,
        occupancy=0.70,
        special_servicer_transfer_date=dt.date(2026, 11, 1),
        maturity_date=dt.date(2027, 6, 1),
        current_balance=50_000_000.0,
        realized_loss=None,
        reporting_period_end=dt.date(2026, 11, 15),
        origination_date=dt.date(2017, 5, 1),
        payment_status_code="1",
    )
    r1 = ParseResult(loans=[evil_p1], source="<evil1>.xml")
    r2 = ParseResult(loans=[evil_p2], source="<evil2>.xml")
    report = compare_tapes([r1, r2])
    doc = render_html(r2, longitudinal=report)

    assert "</script><script>alert" not in doc
    assert "<evil1>.xml" not in doc
    assert "<evil2>.xml" not in doc
    assert "&lt;evil1&gt;.xml" in doc
    assert "&lt;evil2&gt;.xml" in doc
    assert "\\u003c/script\\u003e" in doc


def test_render_empty_result_and_svg_edge_cases():
    doc = render_html(ParseResult(loans=[], source="empty.xml"))
    assert "<!doctype html>" in doc
    assert BADGE in doc

    empty_wf = svg_appraisal_waterfall(
        {"movers": [], "prev_valuation_sum": 0.0, "curr_valuation_sum": 0.0, "valuation_change": 0.0, "valuation_change_pct": None}
    )
    assert "<svg " in empty_wf and "No month-over-month valuation changes" in empty_wf

    # 3-period trajectory SVG renders cleanly without crashing
    traj3 = svg_distress_trajectory(
        [
            {"as_of": "2026-09-15", "distressed_share": 0.40, "delinquent_share": 0.30, "special_servicing_share": 0.32, "office_haircut": 0.31},
            {"as_of": "2026-10-15", "distressed_share": 0.50, "delinquent_share": 0.42, "special_servicing_share": 0.43, "office_haircut": 0.36},
            {"as_of": "2026-11-15", "distressed_share": 0.60, "delinquent_share": 0.54, "special_servicing_share": 0.56, "office_haircut": 0.40},
        ]
    )
    assert traj3.count("<path ") == 4
    assert "2026-09-15" in traj3 and "2026-11-15" in traj3


def test_write_dashboard_and_committed_docs_match_v02_fixtures(tmp_path: Path):
    tapes = load_dated_tapes([FIXTURE, FIXTURE_P2])
    report = compare_tapes(tapes)
    out = write_dashboard(tapes[-1], tmp_path / "index.html", longitudinal=report)
    rendered = out.read_text(encoding="utf-8")
    assert rendered == render_html(tapes[-1], longitudinal=report)

    committed = REPO_ROOT / "docs" / "index.html"
    assert committed.exists(), "docs/index.html must be generated and committed"
    assert committed.read_text(encoding="utf-8") == rendered
