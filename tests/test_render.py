"""Tests for static HTML dashboard rendering and escaping."""

from __future__ import annotations

import re
from pathlib import Path

from cmbs_distress.parser import Loan, ParseResult, parse_file
from cmbs_distress.render import BADGE, render_html, write_dashboard

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml"


def test_render_contains_required_badge_charts_heatmap_and_table():
    res = parse_file(FIXTURE)
    doc = render_html(res)

    assert doc.startswith("<!doctype html>")
    assert BADGE in doc
    assert "Fixture-proven v0.1 prototype" in doc
    assert "CMBS_CONTACT" in doc

    # 3 inline SVG charts (maturity wall, distress buckets, appraisal haircut)
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


def test_render_is_deterministic_and_self_contained():
    res = parse_file(FIXTURE)
    first = render_html(res)
    second = render_html(res)
    assert first == second

    # Must not load external scripts, stylesheets or images
    assert not re.search(r'<script[^>]+src=', first, re.IGNORECASE)
    assert not re.search(r'<link[^>]+href=["\']https?://', first, re.IGNORECASE)
    assert not re.search(r'<img[^>]+src=["\']https?://', first, re.IGNORECASE)


def test_render_escapes_html_and_json_script_breakouts():
    evil = Loan(
        asset_number="XSS-1",
        property_name='Tower </script><script>alert("xss")</script>',
        property_type_code="OF",
        property_type="Office",
        property_city="<b>New York</b>",
        property_state="NY",
        metro="New York",
        valuation_securitization=100.0,
        most_recent_valuation=80.0,
        dscr=0.9,
        occupancy=0.7,
        special_servicer_transfer_date=None,
        maturity_date=None,
        current_balance=50.0,
        realized_loss=None,
        reporting_period_end=None,
    )
    doc = render_html(ParseResult(loans=[evil], source="<evil>.xml"))
    assert "</script><script>alert" not in doc
    assert "<evil>.xml" not in doc
    assert "&lt;evil&gt;.xml" in doc
    assert "\\u003c/script\\u003e" in doc


def test_render_empty_result_does_not_crash():
    doc = render_html(ParseResult(loans=[], source="empty.xml"))
    assert "<!doctype html>" in doc
    assert BADGE in doc


def test_write_dashboard_and_committed_docs_match_fixture(tmp_path: Path):
    res = parse_file(FIXTURE)
    out = write_dashboard(res, tmp_path / "index.html")
    rendered = out.read_text(encoding="utf-8")
    assert rendered == render_html(res)

    committed = REPO_ROOT / "docs" / "index.html"
    assert committed.exists(), "docs/index.html must be generated and committed"
    assert committed.read_text(encoding="utf-8") == rendered
