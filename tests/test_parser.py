"""Fixture-proven tests for SEC Form ABS-EE CMBS XML parsing."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from cmbs_distress.parser import (
    FIELD_LABELS,
    metro_for,
    normalise_occupancy,
    parse_date,
    parse_file,
    parse_files,
    parse_number,
    parse_xml_bytes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_cmbs_absee.xml"


def test_parse_synthetic_fixture_counts_and_as_of():
    result = parse_file(FIXTURE)
    assert len(result.loans) == 16
    assert result.as_of == dt.date(2026, 10, 15)
    assert result.source == "synthetic_cmbs_absee.xml"

    metros = {loan.metro for loan in result.loans}
    assert metros == {"New York", "San Francisco", "Chicago", "Dallas", "Boston", "Other"}

    types = {loan.property_type for loan in result.loans}
    assert types == {"Office", "Retail", "Multifamily", "Industrial"}


def test_parse_fixture_all_fields_and_edge_cases():
    result = parse_file(FIXTURE)
    by_id = {loan.asset_number: loan for loan in result.loans}

    ln1 = by_id["LN-001"]
    assert ln1.property_name == "Midtown West Tower"
    assert ln1.property_type_code == "OF"
    assert ln1.property_type == "Office"
    assert ln1.property_city == "New York"
    assert ln1.property_state == "NY"
    assert ln1.metro == "New York"
    assert ln1.valuation_securitization == 140_000_000.0
    assert ln1.most_recent_valuation == 84_000_000.0
    assert ln1.dscr == pytest.approx(0.85)
    assert ln1.occupancy == pytest.approx(0.64)
    assert ln1.special_servicer_transfer_date == dt.date(2025, 11, 12)
    assert ln1.in_special_servicing is True
    assert ln1.maturity_date == dt.date(2026, 9, 1)
    assert ln1.origination_date == dt.date(2016, 8, 15)
    assert ln1.vintage_year == 2016
    assert ln1.maturity_year == 2026
    assert ln1.payment_status_code == "5"
    assert ln1.payment_status == "Non-Performing Matured Balloon"
    assert ln1.is_delinquent is True
    assert ln1.current_balance == 80_000_000.0
    assert ln1.realized_loss is None

    # Missing appraisal edge case
    ln2 = by_id["LN-002"]
    assert ln2.valuation_securitization == 95_000_000.0
    assert ln2.most_recent_valuation is None
    assert ln2.in_special_servicing is False
    assert ln2.payment_status_code == "0"
    assert ln2.payment_status == "Current"
    assert ln2.is_delinquent is False

    # MM-DD-YYYY date format for specialServicerTransferDate
    ln4 = by_id["LN-004"]
    assert ln4.special_servicer_transfer_date == dt.date(2025, 4, 18)

    # Liquidated loan with realized loss
    ln5 = by_id["LN-005"]
    assert ln5.current_balance == 0.0
    assert ln5.realized_loss == 14_500_000.0

    # Multi-property block + 0-100 occupancy rescaling
    ln9 = by_id["LN-009"]
    assert ln9.property_count == 2
    assert ln9.property_city == "Chicago"
    assert ln9.occupancy == pytest.approx(0.97)
    assert any("LN-009: 2 property blocks" in n for n in result.notes)
    assert any("LN-009: occupancy reported on a 0-100 scale" in n for n in result.notes)

    # Blank appraisal + missing DSCR
    ln15 = by_id["LN-015"]
    assert ln15.most_recent_valuation is None
    assert ln15.dscr is None

    # Metro aliases + Other fallback
    assert by_id["LN-003"].metro == "New York"  # Brooklyn, NY
    assert by_id["LN-011"].metro == "Dallas"  # Irving, TX
    assert by_id["LN-014"].metro == "Boston"  # Cambridge, MA
    assert by_id["LN-016"].metro == "Other"  # Denver, CO


def test_namespace_agnostic_parsing():
    raw = FIXTURE.read_text(encoding="utf-8")
    no_ns = raw.replace(' xmlns="http://www.sec.gov/edgar/document/absee/cmbs/assetdata"', "")
    prefixed = (
        raw.replace(
            ' xmlns="http://www.sec.gov/edgar/document/absee/cmbs/assetdata"',
            ' xmlns:cmbs="http://example.org/custom-absee"',
        )
        .replace("<assetData", "<cmbs:assetData")
        .replace("</assetData>", "</cmbs:assetData>")
        .replace("<assets>", "<cmbs:assets>")
        .replace("</assets>", "</cmbs:assets>")
        .replace("<assetNumber>", "<cmbs:assetNumber>")
        .replace("</assetNumber>", "</cmbs:assetNumber>")
    )

    r_default = parse_xml_bytes(raw.encode("utf-8"), "default")
    r_none = parse_xml_bytes(no_ns.encode("utf-8"), "none")
    r_pref = parse_xml_bytes(prefixed.encode("utf-8"), "prefixed")

    assert r_default.loans == r_none.loans == r_pref.loans


def test_alternate_labels_and_unknown_property_code():
    xml = b"""
    <root>
      <assets>
        <assetNumber>ALT-1</assetNumber>
        <propertyTypeCode>ZZ</propertyTypeCode>
        <propertyCity>Austin</propertyCity>
        <propertyState>tx</propertyState>
        <loanOriginationDate>2019-06-01</loanOriginationDate>
        <loanPaymentStatusCode>9</loanPaymentStatusCode>
        <mostRecentSpecialServicerTransferDate>20260105</mostRecentSpecialServicerTransferDate>
        <reportPeriodEndScheduledLoanBalanceAmount>$12,500,000</reportPeriodEndScheduledLoanBalanceAmount>
      </assets>
    </root>
    """
    res = parse_xml_bytes(xml, "alt.xml")
    assert len(res.loans) == 1
    loan = res.loans[0]
    assert loan.asset_number == "ALT-1"
    assert loan.property_type == "Other"
    assert loan.property_state == "TX"
    assert loan.metro == "Other"
    assert loan.origination_date == dt.date(2019, 6, 1)
    assert loan.vintage_year == 2019
    assert loan.payment_status_code == "9"
    assert loan.payment_status == "Code 9"
    assert loan.is_delinquent is False
    assert loan.special_servicer_transfer_date == dt.date(2026, 1, 5)
    assert loan.current_balance == 12_500_000.0
    assert any("unknown propertyTypeCode 'ZZ'" in n for n in res.notes)
    assert any("unknown paymentStatusLoanCode '9'" in n for n in res.notes)


def test_rejects_doctype_and_entity():
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_xml_bytes(b"<!DOCTYPE foo><assetData/>")
    with pytest.raises(ValueError, match="ENTITY"):
        parse_xml_bytes(b"<?xml version='1.0'?><!ENTITY x 'y'><assetData/>")


def test_empty_tape_returns_note():
    res = parse_xml_bytes(b"<assetData></assetData>", "empty.xml")
    assert res.loans == []
    assert res.as_of is None
    assert any("no <assets> elements found" in n for n in res.notes)


def test_parse_files_combines_multiple(tmp_path: Path):
    copy = tmp_path / "copy.xml"
    copy.write_bytes(FIXTURE.read_bytes())
    res = parse_files([FIXTURE, copy])
    assert len(res.loans) == 32
    assert "synthetic_cmbs_absee.xml" in res.source and "copy.xml" in res.source


def test_helper_parsers():
    assert parse_number("1,234.50") == 1234.5
    assert parse_number(" $500 ") == 500.0
    assert parse_number("") is None
    assert parse_number("N/A") is None

    assert parse_date("2026-07-04") == dt.date(2026, 7, 4)
    assert parse_date("07/04/2026") == dt.date(2026, 7, 4)
    assert parse_date("07-04-2026") == dt.date(2026, 7, 4)
    assert parse_date("20260704") == dt.date(2026, 7, 4)
    assert parse_date("2026-07") == dt.date(2026, 7, 1)
    assert parse_date("not-a-date") is None

    assert normalise_occupancy(None) == (None, False)
    assert normalise_occupancy(0.82) == (0.82, False)
    assert normalise_occupancy(82.0) == (pytest.approx(0.82), True)

    assert metro_for("Boston", "ma") == "Boston"
    assert metro_for(None, "NY") == "Other"
    assert "assetNumber" in FIELD_LABELS["asset_number"]
