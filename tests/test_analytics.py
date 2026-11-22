"""Fixture-proven math tests for CMBS distress analytics."""

from __future__ import annotations

from pathlib import Path

import pytest

from cmbs_distress.analytics import (
    distress_buckets,
    haircut,
    in_wall,
    loan_flags,
    maturity_wall,
    pool_haircut,
    rollup,
    summarize,
)
from cmbs_distress.parser import Loan, parse_file

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml"


@pytest.fixture(scope="module")
def loans() -> list[Loan]:
    return parse_file(FIXTURE).loans


def test_pool_totals_and_office_split(loans: list[Loan]):
    s = summarize(loans)
    assert s["overall"]["loans"] == 16
    assert s["overall"]["balance"] == pytest.approx(640_000_000.0)
    assert s["office"]["loans"] == 8
    assert s["office"]["balance"] == pytest.approx(398_000_000.0)
    assert s["non_office"]["loans"] == 8
    assert s["non_office"]["balance"] == pytest.approx(242_000_000.0)
    assert s["overall"]["realized_loss"] == pytest.approx(15_700_000.0)
    assert s["office"]["realized_loss"] == pytest.approx(14_500_000.0)


def test_appraisal_haircuts_per_loan_and_value_weighted(loans: list[Loan]):
    by_id = {l.asset_number: l for l in loans}
    assert haircut(by_id["LN-001"]) == pytest.approx(0.40)
    assert haircut(by_id["LN-002"]) is None  # missing mostRecentValuationAmount
    assert haircut(by_id["LN-003"]) == pytest.approx(-0.10)  # appreciation
    assert haircut(by_id["LN-004"]) == pytest.approx(0.55)
    assert haircut(by_id["LN-015"]) is None

    # Value-weighted pool haircut across the 14 loans reporting both valuations
    ph = pool_haircut(loans)
    assert ph["loans_with_both"] == 14
    assert ph["loans_missing_appraisal"] == 2
    expected_sec = (
        140 + 70 + 120 + 50 + 45 + 100 + 35 + 62 + 64 + 52 + 48 + 82 + 90 + 40
    ) * 1_000_000.0
    expected_recent = (
        84 + 77 + 54 + 22 + 36 + 58 + 28 + 71.3 + 51.2 + 57.2 + 45.6 + 57.4 + 85.5 + 42
    ) * 1_000_000.0
    assert ph["securitization_value"] == pytest.approx(expected_sec)
    assert ph["recent_value"] == pytest.approx(expected_recent)
    assert ph["haircut"] == pytest.approx(1.0 - expected_recent / expected_sec)

    # Office haircut across 7 office loans with both valuations
    oh = pool_haircut([l for l in loans if l.is_office])
    assert oh["loans_with_both"] == 7
    assert oh["loans_missing_appraisal"] == 1
    off_sec = (140 + 120 + 50 + 100 + 64 + 82 + 90) * 1_000_000.0
    off_recent = (84 + 54 + 22 + 58 + 51.2 + 57.4 + 85.5) * 1_000_000.0
    assert oh["haircut"] == pytest.approx(1.0 - off_recent / off_sec)


def test_haircut_non_positive_securitization_is_none():
    loan = Loan(
        asset_number="Z",
        property_name=None,
        property_type_code="OF",
        property_type="Office",
        property_city=None,
        property_state=None,
        metro="Other",
        valuation_securitization=0.0,
        most_recent_valuation=100.0,
        dscr=None,
        occupancy=None,
        special_servicer_transfer_date=None,
        maturity_date=None,
        current_balance=100.0,
        realized_loss=None,
        reporting_period_end=None,
    )
    assert haircut(loan) is None


def test_maturity_wall_2026_2028(loans: list[Loan]):
    wall = maturity_wall(loans)
    assert wall["start"] == 2026
    assert wall["end"] == 2028
    assert wall["count"] == 11
    assert wall["balance"] == pytest.approx(444_000_000.0)
    assert wall["share"] == pytest.approx(444_000_000.0 / 640_000_000.0)
    assert wall["by_year"] == {
        2026: pytest.approx(163_000_000.0),
        2027: pytest.approx(185_000_000.0),
        2028: pytest.approx(96_000_000.0),
    }
    assert wall["by_year_office"] == {
        2026: pytest.approx(145_000_000.0),
        2027: pytest.approx(163_000_000.0),
        2028: pytest.approx(38_000_000.0),
    }

    by_id = {l.asset_number: l for l in loans}
    assert in_wall(by_id["LN-001"]) is True
    assert in_wall(by_id["LN-003"]) is False  # 2030
    assert in_wall(by_id["LN-011"]) is False  # 2029


def test_distress_buckets_and_strict_boundaries(loans: list[Loan]):
    by_id = {l.asset_number: l for l in loans}

    # Strict inequalities: DSCR == 1.00 and Occupancy == 0.75 are NOT flagged
    assert loan_flags(by_id["LN-002"]) == []
    # Missing DSCR is NOT flagged
    assert loan_flags(by_id["LN-015"]) == []
    # Full distress flags
    assert loan_flags(by_id["LN-001"]) == ["dscr_lt_1", "occ_lt_75", "special_servicing"]

    buckets = distress_buckets(loans)
    # dscr_lt_1: LN-001 (80), LN-004 (65), LN-005 (0), LN-006 (28), LN-007 (60), LN-013 (48) = 281M, 6 loans
    assert buckets["dscr_lt_1"]["count"] == 6
    assert buckets["dscr_lt_1"]["balance"] == pytest.approx(281_000_000.0)
    assert buckets["dscr_lt_1"]["reported"] == 15  # LN-015 missing DSCR

    # occ_lt_75: LN-001 (80), LN-004 (65), LN-005 (0), LN-007 (60), LN-008 (22), LN-010 (38), LN-013 (48) = 313M, 7 loans
    assert buckets["occ_lt_75"]["count"] == 7
    assert buckets["occ_lt_75"]["balance"] == pytest.approx(313_000_000.0)
    assert buckets["occ_lt_75"]["reported"] == 16

    # special_servicing: LN-001 (80), LN-004 (65), LN-005 (0), LN-007 (60), LN-008 (22), LN-013 (48) = 275M, 6 loans
    assert buckets["special_servicing"]["count"] == 6
    assert buckets["special_servicing"]["balance"] == pytest.approx(275_000_000.0)

    # any: above union = LN-001,4,5,6,7,8,10,13 -> 8 loans, 341M
    assert buckets["any"]["count"] == 8
    assert buckets["any"]["balance"] == pytest.approx(341_000_000.0)
    assert buckets["any"]["share"] == pytest.approx(341_000_000.0 / 640_000_000.0)


def test_weighted_dscr_excludes_zero_balance_and_missing(loans: list[Loan]):
    s = summarize(loans)
    # Office loans with positive balance and DSCR: LN-001,2,4,7,10,13,14 (LN-005 has 0 balance)
    num = (
        80 * 0.85
        + 55 * 1.00
        + 65 * 0.72
        + 60 * 0.88
        + 38 * 1.18
        + 48 * 0.92
        + 52 * 1.48
    ) * 1_000_000.0
    den = 398_000_000.0
    assert s["office"]["wtd_dscr"] == pytest.approx(num / den)


def test_metro_by_property_type_rollup_reconciles(loans: list[Loan]):
    rows = rollup(loans, ("metro", "property_type"))
    assert sum(r["loans"] for r in rows) == len(loans)
    assert sum(r["balance"] for r in rows) == pytest.approx(640_000_000.0)
    assert sum(r["realized_loss"] for r in rows) == pytest.approx(15_700_000.0)

    lookup = {(r["metro"], r["property_type"]): r for r in rows}
    sf_office = lookup[("San Francisco", "Office")]
    assert sf_office["loans"] == 2
    assert sf_office["balance"] == pytest.approx(65_000_000.0)
    assert sf_office["distressed_share"] == pytest.approx(1.0)
    assert sf_office["realized_loss"] == pytest.approx(14_500_000.0)

    bos_office = lookup[("Boston", "Office")]
    assert bos_office["loans"] == 2
    assert bos_office["balance"] == pytest.approx(100_000_000.0)
    assert bos_office["distressed_share"] == pytest.approx(0.48)


def test_empty_loan_list_is_safe():
    s = summarize([])
    assert s["overall"]["loans"] == 0
    assert s["overall"]["balance"] == 0.0
    assert s["overall"]["wtd_dscr"] is None
    assert s["overall"]["haircut"] is None
    assert s["maturity_wall"]["share"] is None
    assert s["distress"]["any"]["share"] is None
    assert s["by_metro_type"] == []
