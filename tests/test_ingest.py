"""Offline unit tests for the SEC EDGAR ingestion helpers (zero network calls)."""

from __future__ import annotations

import urllib.request

import pytest

from cmbs_distress.ingest import (
    build_exhibit_url,
    build_submissions_url,
    extract_abs_ee_filings,
    normalize_cik,
    resolve_user_agent,
    validate_user_agent,
)


@pytest.fixture(autouse=True)
def _block_all_network(monkeypatch: pytest.MonkeyPatch):
    def _forbid(*args, **kwargs):
        raise AssertionError("Tests must NEVER perform live HTTP requests to SEC EDGAR")

    monkeypatch.setattr(urllib.request, "urlopen", _forbid)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cmbs-office-distress maintainer@example.com", "cmbs-office-distress maintainer@example.com"),
        ("  Research   App   analyst@example.org  ", "Research App analyst@example.org"),
    ],
)
def test_validate_user_agent_accepts_compliant_format(raw: str, expected: str):
    assert validate_user_agent(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        "cmbs-office-distress",  # missing email
        "maintainer@example.com",  # missing name/app identifier
        "cmbs-office-distress (https://example.com)",  # URL in parens forbidden
        "cmbs-office-distress (maintainer@example.com)",  # parentheses forbidden
        "cmbs-office-distress https://example.com maintainer@example.com",  # URL forbidden
    ],
)
def test_validate_user_agent_rejects_noncompliant_values(bad: str | None):
    with pytest.raises(ValueError):
        validate_user_agent(bad)


def test_resolve_user_agent_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CMBS_CONTACT", raising=False)
    with pytest.raises(ValueError, match="CMBS_CONTACT"):
        resolve_user_agent()

    monkeypatch.setenv("CMBS_CONTACT", "cmbs-office-distress data@example.com")
    assert resolve_user_agent() == "cmbs-office-distress data@example.com"


def test_url_builders_and_cik_normalization():
    assert normalize_cik("12345") == "0000012345"
    assert normalize_cik("CIK0001789012") == "0001789012"
    with pytest.raises(ValueError):
        normalize_cik("not-digits")

    assert build_submissions_url(12345) == "https://data.sec.gov/submissions/CIK0000012345.json"
    assert (
        build_exhibit_url("0001234567", "0001234567-26-000042", "ex102.xml")
        == "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000042/ex102.xml"
    )

    with pytest.raises(ValueError):
        build_exhibit_url("123", "bad-acc", "ex102.xml")
    with pytest.raises(ValueError):
        build_exhibit_url("123", "0001-26-0001", "../secret.xml")


def test_extract_abs_ee_filings_filters_forms():
    payload = {
        "filings": {
            "recent": {
                "form": ["10-D", "ABS-EE", "8-K", "ABS-EE/A", "ABS-EE"],
                "accessionNumber": ["001", "002", "003", "004", "005"],
                "filingDate": ["2026-10-15", "2026-10-15", "2026-09-30", "2026-09-15", "2026-08-15"],
                "primaryDocument": ["d1.htm", "e1.htm", "k1.htm", "e2.htm", "e3.htm"],
            }
        }
    }
    filings = extract_abs_ee_filings(payload, limit=2)
    assert filings == [
        {
            "form": "ABS-EE",
            "accession_number": "002",
            "filing_date": "2026-10-15",
            "primary_document": "e1.htm",
        },
        {
            "form": "ABS-EE/A",
            "accession_number": "004",
            "filing_date": "2026-09-15",
            "primary_document": "e2.htm",
        },
    ]
