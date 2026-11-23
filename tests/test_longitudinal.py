"""Fixture-proven unit tests for longitudinal multi-filing CMBS distress analytics."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from cmbs_distress.longitudinal import (
    cohort_exposure_table,
    compare_pair,
    compare_tapes,
    credit_tier,
    dscr_tier,
    load_dated_tapes,
    occupancy_tier,
    order_tapes,
)
from cmbs_distress.parser import Loan, ParseResult, parse_file

FIXTURE_P1 = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml"
FIXTURE_P2 = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee_2026_10.xml"


def _make_loan(
    asset_number: str,
    as_of: dt.date,
    *,
    property_type_code: str = "OF",
    property_type: str = "Office",
    metro: str = "New York",
    balance: float | None = 50_000_000.0,
    sec_val: float | None = 80_000_000.0,
    rec_val: float | None = 70_000_000.0,
    dscr: float | None = 1.35,
    occupancy: float | None = 0.90,
    ss_date: dt.date | None = None,
    maturity_date: dt.date | None = dt.date(2027, 6, 1),
    origination_date: dt.date | None = dt.date(2017, 5, 1),
    realized_loss: float | None = None,
    payment_status_code: str | None = "0",
) -> Loan:
    return Loan(
        asset_number=asset_number,
        property_name=f"Property {asset_number}",
        property_type_code=property_type_code,
        property_type=property_type,
        property_city="New York" if metro == "New York" else "Chicago",
        property_state="NY" if metro == "New York" else "IL",
        metro=metro,
        valuation_securitization=sec_val,
        most_recent_valuation=rec_val,
        dscr=dscr,
        occupancy=occupancy,
        special_servicer_transfer_date=ss_date,
        maturity_date=maturity_date,
        current_balance=balance,
        realized_loss=realized_loss,
        reporting_period_end=as_of,
        origination_date=origination_date,
        payment_status_code=payment_status_code,
    )


@pytest.fixture(scope="module")
def fixture_report() -> dict:
    tapes = load_dated_tapes([FIXTURE_P2, FIXTURE_P1])  # reversed intentionally to test sorting
    return compare_tapes(tapes)


def test_tier_classifiers_strict_boundaries_and_missing():
    assert dscr_tier(None) == "unreported"
    assert dscr_tier(1.25) == "healthy"
    assert dscr_tier(1.2499) == "watch"
    assert dscr_tier(1.00) == "watch"
    assert dscr_tier(0.9999) == "distressed"

    assert occupancy_tier(None) == "unreported"
    assert occupancy_tier(0.85) == "healthy"
    assert occupancy_tier(0.8499) == "watch"
    assert occupancy_tier(0.75) == "watch"
    assert occupancy_tier(0.7499) == "distressed"

    d = dt.date(2026, 10, 15)
    # Worst reported metric wins; missing metrics never coerce to 0
    assert credit_tier(_make_loan("A", d, dscr=None, occupancy=None)) == "unreported"
    assert credit_tier(_make_loan("B", d, dscr=None, occupancy=0.90)) == "healthy"
    assert credit_tier(_make_loan("C", d, dscr=None, occupancy=0.80)) == "watch"
    assert credit_tier(_make_loan("D", d, dscr=1.50, occupancy=0.74)) == "distressed"
    assert credit_tier(_make_loan("E", d, dscr=1.00, occupancy=0.75)) == "watch"


def test_fixture_chronological_ordering_and_pool_bridge(fixture_report: dict):
    assert fixture_report["period_count"] == 2
    assert fixture_report["start_as_of"] == "2026-10-15"
    assert fixture_report["end_as_of"] == "2026-11-15"
    assert fixture_report["sources"] == [
        "synthetic_cmbs_absee.xml",
        "synthetic_cmbs_absee_2026_10.xml",
    ]
    lt = fixture_report["latest_transition"]
    assert lt["days_between"] == 31
    assert lt["matched_count"] == 16
    assert lt["added_count"] == 0
    assert lt["removed_count"] == 0
    assert lt["prev_balance"] == pytest.approx(640_000_000.0)
    assert lt["curr_balance"] == pytest.approx(618_000_000.0)
    assert lt["balance_change"] == pytest.approx(-22_000_000.0)
    assert lt["balance_change_pct"] == pytest.approx(-22_000_000.0 / 640_000_000.0)


def test_fixture_special_servicing_inflows_and_reconciliation(fixture_report: dict):
    ss = fixture_report["latest_transition"]["special_servicing"]
    assert ss["inflow_count"] == 2
    assert ss["inflow_balance"] == pytest.approx(93_000_000.0)
    assert ss["inflow_office_count"] == 2
    assert ss["inflow_office_balance"] == pytest.approx(93_000_000.0)
    assert [r["asset_number"] for r in ss["inflow_loans"]] == ["LN-002", "LN-010"]
    assert ss["outflow_count"] == 0
    assert ss["resolved_count"] == 1
    assert ss["resolved_prior_balance"] == pytest.approx(22_000_000.0)
    assert ss["resolved_loans"][0]["asset_number"] == "LN-008"

    # Active SS balance reconciles exactly: 275M + 93M - 22M = 346M
    assert ss["prev_active_balance"] == pytest.approx(275_000_000.0)
    assert ss["curr_active_balance"] == pytest.approx(346_000_000.0)
    assert ss["net_active_balance_change"] == pytest.approx(71_000_000.0)
    assert ss["prev_active_balance"] + ss["inflow_balance"] - ss["resolved_prior_balance"] == pytest.approx(
        ss["curr_active_balance"]
    )


def test_fixture_appraisal_velocity_and_metro_type_reconciliation(fixture_report: dict):
    vel = fixture_report["latest_transition"]["appraisal_velocity"]
    assert vel["matched_count"] == 14  # LN-002 missing in P1, LN-015 missing in both
    assert vel["reappraised_count"] == 5
    assert vel["cut_count"] == 4
    assert vel["cut_amount"] == pytest.approx(-30_600_000.0)
    assert vel["gain_count"] == 1
    assert vel["gain_amount"] == pytest.approx(1_200_000.0)
    assert vel["unchanged_count"] == 9
    assert vel["prev_valuation_sum"] == pytest.approx(769_200_000.0)
    assert vel["curr_valuation_sum"] == pytest.approx(739_800_000.0)
    assert vel["valuation_change"] == pytest.approx(-29_400_000.0)
    assert vel["valuation_change_pct"] == pytest.approx(-29_400_000.0 / 769_200_000.0)

    # Newly appraised LN-002 tracked separately without distorting matched velocity
    assert vel["newly_appraised_count"] == 1
    assert vel["newly_appraised_loans"][0]["asset_number"] == "LN-002"
    assert vel["newly_appraised_loans"][0]["curr_haircut"] == pytest.approx(0.30)

    # Office matched velocity
    off = vel["office"]
    assert off["matched_count"] == 7
    assert off["prev_valuation_sum"] == pytest.approx(412_100_000.0)
    assert off["curr_valuation_sum"] == pytest.approx(381_500_000.0)
    assert off["valuation_change"] == pytest.approx(-30_600_000.0)
    assert off["haircut_change"] == pytest.approx(30_600_000.0 / 646_000_000.0)

    # Movers ordered worst cut -> mark-up
    assert [(m["asset_number"], m["valuation_change"]) for m in vel["movers"]] == [
        ("LN-001", pytest.approx(-14_000_000.0)),
        ("LN-004", pytest.approx(-8_400_000.0)),
        ("LN-007", pytest.approx(-5_000_000.0)),
        ("LN-010", pytest.approx(-3_200_000.0)),
        ("LN-009", pytest.approx(1_200_000.0)),
    ]

    # Exact reconciliation across metros and property types
    assert sum(r["valuation_change"] for r in vel["by_metro"]) == pytest.approx(vel["valuation_change"])
    assert sum(r["valuation_change"] for r in vel["by_property_type"]) == pytest.approx(vel["valuation_change"])
    assert vel["by_metro"][0]["metro"] == "New York"
    assert vel["by_metro"][0]["valuation_change"] == pytest.approx(-14_000_000.0)
    assert vel["by_metro"][1]["metro"] == "San Francisco"
    assert vel["by_metro"][1]["valuation_change"] == pytest.approx(-8_400_000.0)


def test_fixture_dscr_occupancy_migration_matrix_invariants(fixture_report: dict):
    mig_block = fixture_report["latest_transition"]["migration"]
    comb = mig_block["combined"]

    # 15 active loans in P1 + 1 prior liquidated loan (LN-005) excluded from active matrix
    assert comb["total_count"] == 15
    assert comb["prior_inactive_count"] == 1
    assert comb["total_prior_balance"] == pytest.approx(640_000_000.0)
    assert comb["total_current_balance"] == pytest.approx(618_000_000.0)

    cells = comb["cells"]
    assert cells["healthy"]["healthy"]["count"] == 4
    assert cells["healthy"]["healthy"]["prior_balance"] == pytest.approx(144_000_000.0)
    assert cells["healthy"]["watch"]["count"] == 2
    assert set(cells["healthy"]["watch"]["assets"]) == {"LN-012", "LN-014"}
    assert cells["healthy"]["watch"]["prior_balance"] == pytest.approx(82_000_000.0)
    assert cells["watch"]["watch"]["assets"] == ["LN-015"]
    assert cells["watch"]["distressed"]["assets"] == ["LN-002"]
    assert cells["watch"]["distressed"]["prior_balance"] == pytest.approx(55_000_000.0)
    assert cells["distressed"]["distressed"]["count"] == 6
    assert cells["distressed"]["resolved"]["assets"] == ["LN-008"]
    assert cells["distressed"]["resolved"]["prior_balance"] == pytest.approx(22_000_000.0)

    # Row transition probabilities sum to 1.0 for every populated row
    for ft in comb["from_tiers"]:
        if comb["row_totals"][ft]["prior_balance"] > 0:
            row_prob = sum(cells[ft][tt]["row_share"] or 0.0 for tt in comb["to_tiers"])
            assert row_prob == pytest.approx(1.0)

    # Downgrade / upgrade / resolved aggregates
    assert comb["downgraded"]["count"] == 3
    assert comb["downgraded"]["prior_balance"] == pytest.approx(137_000_000.0)
    assert comb["upgraded"]["count"] == 0
    assert comb["resolved"]["count"] == 1
    assert comb["resolved"]["prior_balance"] == pytest.approx(22_000_000.0)

    # Standalone DSCR matrix captures LN-010 (1.18 -> 0.94) watch -> distressed and LN-015 unreported
    dscr_m = mig_block["dscr"]
    assert set(dscr_m["cells"]["watch"]["distressed"]["assets"]) == {"LN-002", "LN-010"}
    assert dscr_m["cells"]["unreported"]["unreported"]["assets"] == ["LN-015"]

    # Explicit strict threshold crossings
    cross = mig_block["threshold_crossings"]
    assert [x["asset_number"] for x in cross["dscr_crossed_below_1"]] == ["LN-002", "LN-010"]
    assert [x["asset_number"] for x in cross["occ_crossed_below_75"]] == ["LN-002"]


def test_fixture_delinquency_and_incremental_losses(fixture_report: dict):
    lt = fixture_report["latest_transition"]

    # Delinquency transitions decoupled from SS transfers
    delinq = lt["delinquency"]
    assert delinq["reported_pair_count"] == 15
    assert delinq["newly_delinquent"]["count"] == 2
    assert [r["asset_number"] for r in delinq["newly_delinquent"]["loans"]] == ["LN-002", "LN-006"]
    assert delinq["newly_delinquent"]["balance"] == pytest.approx(83_000_000.0)
    assert delinq["worsened"]["count"] == 3
    assert {r["asset_number"] for r in delinq["worsened"]["loans"]} == {"LN-004", "LN-007", "LN-013"}
    assert delinq["resolved_delinquent"]["count"] == 1
    assert delinq["resolved_delinquent"]["loans"][0]["asset_number"] == "LN-008"
    assert delinq["prev_active_delinquent_balance"] == pytest.approx(275_000_000.0)
    assert delinq["curr_active_delinquent_balance"] == pytest.approx(336_000_000.0)

    # Incremental realized losses (LN-005 $14.5M & LN-008 prior $1.2M NOT double-counted)
    losses = lt["losses_and_liquidations"]
    assert losses["prev_cumulative_loss"] == pytest.approx(15_700_000.0)
    assert losses["curr_cumulative_loss"] == pytest.approx(23_200_000.0)
    assert losses["incremental_loss"] == pytest.approx(7_500_000.0)
    assert losses["net_pool_loss_change"] == pytest.approx(7_500_000.0)
    assert losses["liquidation_count"] == 1
    assert losses["liquidated_balance"] == pytest.approx(22_000_000.0)
    assert losses["weighted_loss_severity"] == pytest.approx(7_500_000.0 / 22_000_000.0)
    assert losses["payoff_count"] == 0
    ev = losses["events"][0]
    assert ev["asset_number"] == "LN-008"
    assert ev["event_type"] == "liquidation_with_loss"
    assert ev["prev_loss"] == pytest.approx(1_200_000.0)
    assert ev["curr_loss"] == pytest.approx(8_700_000.0)
    assert ev["incremental_loss"] == pytest.approx(7_500_000.0)


def test_fixture_vintage_and_maturity_cohorts_reconcile(fixture_report: dict):
    cohorts = fixture_report["cohorts"]
    for key in ("by_maturity_year", "by_vintage_year", "by_vintage_maturity"):
        rows = cohorts[key]
        assert sum(r["loans"] for r in rows) == 16
        assert sum(r["balance"] for r in rows) == pytest.approx(618_000_000.0)
        assert sum(r["prev_balance"] for r in rows) == pytest.approx(640_000_000.0)
        assert sum(r["pool_share"] for r in rows) == pytest.approx(1.0)
        assert sum(r["ss_inflow_balance"] for r in rows) == pytest.approx(93_000_000.0)
        assert sum(r["valuation_change"] for r in rows) == pytest.approx(-29_400_000.0)
        assert sum(r["incremental_loss"] for r in rows) == pytest.approx(7_500_000.0)

    mat = {r["cohort"]: r for r in cohorts["by_maturity_year"]}
    assert mat[2026]["in_maturity_wall"] is True
    assert mat[2026]["valuation_change"] == pytest.approx(-22_400_000.0)  # LN-001 + LN-004
    assert mat[2027]["ss_inflow_balance"] == pytest.approx(55_000_000.0)  # LN-002
    assert mat[2027]["incremental_loss"] == pytest.approx(7_500_000.0)  # LN-008
    assert mat[2029]["in_maturity_wall"] is False

    vin = {r["cohort"]: r for r in cohorts["by_vintage_year"]}
    assert vin[2016]["balance"] == pytest.approx(145_000_000.0)  # LN-001 + LN-004 + LN-005(0)
    assert vin[2021]["ss_inflow_balance"] == pytest.approx(38_000_000.0)  # LN-010


def test_three_period_trajectory_upgrades_payoffs_and_edge_cases():
    d1, d2, d3 = dt.date(2026, 9, 15), dt.date(2026, 10, 15), dt.date(2026, 11, 15)

    t1 = ParseResult(
        source="m1.xml",
        loans=[
            _make_loan("A1", d1, balance=40e6, dscr=0.90, occupancy=0.70, ss_date=dt.date(2026, 6, 1), payment_status_code="2", rec_val=50e6),
            _make_loan("A2", d1, balance=30e6, dscr=1.40, occupancy=0.92, payment_status_code="0", rec_val=45e6),
            _make_loan("A3", d1, balance=20e6, dscr=1.10, occupancy=0.80, payment_status_code=None, origination_date=None, rec_val=30e6),
            _make_loan("A4", d1, balance=10e6, dscr=1.30, occupancy=0.90, payment_status_code="0", rec_val=15e6),
        ],
    )
    t2 = ParseResult(
        source="m2.xml",
        loans=[
            # A1 cures from Distressed -> Watch, exits special servicing, cures delinquency 2 -> 0!
            _make_loan("A1", d2, balance=40e6, dscr=1.12, occupancy=0.80, ss_date=None, payment_status_code="0", rec_val=52e6),
            # A2 pays off in full with $0 loss
            _make_loan("A2", d2, balance=0.0, dscr=1.40, occupancy=0.92, payment_status_code="0", realized_loss=0.0, rec_val=45e6),
            # A3 takes an active modification loss of $500k while staying active
            _make_loan("A3", d2, balance=20e6, dscr=1.10, occupancy=0.80, payment_status_code=None, origination_date=None, realized_loss=500_000.0, rec_val=28e6),
            # A4 is removed from the tape completely (treated as payoff_no_loss / resolved)
        ],
    )
    t3 = ParseResult(
        source="m3.xml",
        loans=[
            # A1 continues curing Watch -> Healthy
            _make_loan("A1", d3, balance=40e6, dscr=1.32, occupancy=0.88, ss_date=None, payment_status_code="0", rec_val=55e6),
            _make_loan("A2", d3, balance=0.0, dscr=1.40, occupancy=0.92, payment_status_code="0", realized_loss=0.0, rec_val=45e6),
            _make_loan("A3", d3, balance=20e6, dscr=1.15, occupancy=0.82, payment_status_code=None, origination_date=None, realized_loss=500_000.0, rec_val=28e6),
            # A5 newly added to tape
            _make_loan("A5", d3, balance=25e6, dscr=1.50, occupancy=0.95, payment_status_code="0", rec_val=38e6),
        ],
    )

    rep = compare_tapes([t3, t1, t2])
    assert rep["period_count"] == 3
    assert len(rep["transitions"]) == 2
    assert rep["start_as_of"] == "2026-09-15"
    assert rep["end_as_of"] == "2026-11-15"

    tr1 = rep["transitions"][0]
    assert tr1["special_servicing"]["outflow_count"] == 1
    assert tr1["special_servicing"]["outflow_loans"][0]["asset_number"] == "A1"
    assert tr1["delinquency"]["cured"]["count"] == 1
    assert tr1["delinquency"]["cured"]["loans"][0]["asset_number"] == "A1"
    # Missing payment status (A3) not invented into delinquency counts
    assert tr1["delinquency"]["reported_pair_count"] == 2
    assert tr1["migration"]["combined"]["upgraded"]["count"] == 1
    assert tr1["losses_and_liquidations"]["payoff_count"] == 2  # A2 ($0 bal) + A4 (removed)
    assert tr1["losses_and_liquidations"]["payoff_balance"] == pytest.approx(40_000_000.0)
    assert tr1["losses_and_liquidations"]["incremental_loss"] == pytest.approx(500_000.0)
    assert len(tr1["losses_and_liquidations"]["modification_loss_events"]) == 1

    # Cumulative transition spans m1 -> m3 (A1 distressed -> healthy, A5 added, A4 removed)
    cum = rep["cumulative"]
    assert cum["added_assets"] == ["A5"]
    assert cum["removed_assets"] == ["A4"]
    assert cum["migration"]["combined"]["cells"]["distressed"]["healthy"]["assets"] == ["A1"]
    assert cum["appraisal_velocity"]["movers"][0]["asset_number"] == "A3"

    # Missing originationDate lands cleanly in "Unknown" vintage cohort
    vintages = {r["cohort"]: r for r in rep["cohorts"]["by_vintage_year"]}
    assert "Unknown" in vintages
    assert vintages["Unknown"]["loans"] == 1


def test_validation_errors_and_same_date_grouping(tmp_path: Path):
    p1 = parse_file(FIXTURE_P1)
    p2 = parse_file(FIXTURE_P2)

    with pytest.raises(ValueError, match="at least 2"):
        order_tapes([p1])

    with pytest.raises(ValueError, match="missing reportingPeriodEndDate"):
        order_tapes([p1, ParseResult(loans=[], source="empty.xml")])

    with pytest.raises(ValueError, match="Duplicate reporting period"):
        order_tapes([p1, p1])

    dup_tape = ParseResult(loans=[p1.loans[0], p1.loans[0]], source="dup.xml")
    with pytest.raises(ValueError, match="duplicate asset_number"):
        compare_pair(dup_tape, p2)

    with pytest.raises(ValueError, match="Unknown cohort dimension"):
        cohort_exposure_table(p1, p2, dimension="bogus")

    # load_dated_tapes combines two distinct files sharing the same as_of date
    extra = tmp_path / "extra_same_date.xml"
    extra.write_text(
        """<assetData>
          <assets>
            <assetNumber>LN-999</assetNumber>
            <reportingPeriodEndDate>2026-10-15</reportingPeriodEndDate>
            <currentEndingScheduledBalanceAmount>10000000</currentEndingScheduledBalanceAmount>
          </assets>
        </assetData>""",
        encoding="utf-8",
    )
    grouped = load_dated_tapes([FIXTURE_P1, extra, FIXTURE_P2])
    assert len(grouped) == 2
    assert grouped[0].as_of == dt.date(2026, 10, 15)
    assert len(grouped[0].loans) == 17
    assert grouped[1].as_of == dt.date(2026, 11, 15)
    assert len(grouped[1].loans) == 16
