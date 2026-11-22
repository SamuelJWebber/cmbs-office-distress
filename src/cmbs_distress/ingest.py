"""Actions-ready SEC EDGAR Form ABS-EE ingestion module.

IMPORTANT:
- This module is NEVER invoked by pytest (enforced by tests/test_ingest.py and
  tests/test_no_leaks.py).
- SEC EDGAR requires a compliant User-Agent of the form
  ``"<Name or App> <contact-email>"`` (for example ``"cmbs-office-distress maintainer@example.com"``).
  Never put a URL in parentheses in the User-Agent string.
- Configure ``CMBS_CONTACT`` as an environment variable or GitHub Actions secret
  containing that compliant User-Agent value before calling any live fetch helper.

Evidence status:
- User-Agent validation and filing-URL construction are **fixture-proven** via unit tests.
- Live SEC EDGAR HTTP calls and real EX-102 schema discovery are **unverified** until run
  in GitHub Actions with ``CMBS_CONTACT`` configured.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

# Require a contact email in the User-Agent and forbid URLs / parenthesised URLs.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_FORBIDDEN_UA_RE = re.compile(r"https?://|\([^)]*\)", re.IGNORECASE)

# Polite SEC fair-access ceiling is 10 requests/second; stay well underneath.
DEFAULT_MIN_INTERVAL_SECONDS = 0.25


def validate_user_agent(user_agent: str | None) -> str:
    """Validate and normalize an SEC-compliant ``"<name> <contact-email>"`` User-Agent.

    Raises ``ValueError`` if missing, lacks a name + email pair, or contains a URL
    or parenthesised clause.
    """
    if not user_agent or not user_agent.strip():
        raise ValueError(
            "CMBS_CONTACT is not set. Set CMBS_CONTACT to '<name> <contact-email>' "
            "before fetching from SEC EDGAR."
        )
    ua = " ".join(user_agent.strip().split())
    if _FORBIDDEN_UA_RE.search(ua):
        raise ValueError(
            "CMBS_CONTACT must be '<name> <contact-email>' without URLs or parentheses."
        )
    match = _EMAIL_RE.search(ua)
    if not match:
        raise ValueError(
            "CMBS_CONTACT must include a contact email in '<name> <contact-email>' format."
        )
    # Require a non-empty name token outside the email address itself.
    remaining = (ua[: match.start()] + " " + ua[match.end() :]).strip()
    if not remaining:
        raise ValueError(
            "CMBS_CONTACT must include a name/application identifier before the contact email."
        )
    return ua


def resolve_user_agent(explicit: str | None = None, env_var: str = "CMBS_CONTACT") -> str:
    """Resolve and validate the SEC User-Agent from an explicit value or ``CMBS_CONTACT``."""
    raw = explicit if explicit is not None else os.environ.get(env_var)
    return validate_user_agent(raw)


def normalize_cik(cik: str | int) -> str:
    """Return a zero-padded 10-digit CIK string."""
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"Invalid CIK: {cik!r}")
    return digits.zfill(10)


def build_submissions_url(cik: str | int) -> str:
    return SEC_SUBMISSIONS_URL.format(cik10=normalize_cik(cik))


def build_exhibit_url(cik: str | int, accession_number: str, filename: str) -> str:
    """Build the SEC EDGAR Archives URL for an ABS-EE exhibit XML file."""
    cik_int = str(int(normalize_cik(cik)))
    acc_clean = accession_number.replace("-", "").strip()
    if not acc_clean.isdigit():
        raise ValueError(f"Invalid accession number: {accession_number!r}")
    fname = filename.strip().lstrip("/")
    if not fname or ".." in fname:
        raise ValueError(f"Invalid exhibit filename: {filename!r}")
    return f"{SEC_ARCHIVE_BASE}/{cik_int}/{acc_clean}/{fname}"


def extract_abs_ee_filings(submissions: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    """Extract recent Form ABS-EE metadata entries from a SEC submissions JSON payload."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    out: list[dict[str, str]] = []
    for form, acc, fdate, doc in zip(forms, accessions, dates, primary_docs):
        if str(form).upper() in {"ABS-EE", "ABS-EE/A"}:
            out.append(
                {
                    "form": str(form),
                    "accession_number": str(acc),
                    "filing_date": str(fdate),
                    "primary_document": str(doc),
                }
            )
            if len(out) >= limit:
                break
    return out


def fetch_bytes(
    url: str,
    user_agent: str | None = None,
    timeout: float = 30.0,
    min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
) -> bytes:
    """Fetch raw bytes from SEC EDGAR using a validated ``CMBS_CONTACT`` User-Agent.

    Never called by tests. Enforces a polite sleep interval before the request.
    """
    ua = resolve_user_agent(user_agent)
    if min_interval > 0:
        time.sleep(min_interval)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def fetch_submissions(cik: str | int, user_agent: str | None = None) -> dict[str, Any]:
    """Download the SEC submissions JSON for a CMBS issuing entity CIK."""
    raw = fetch_bytes(build_submissions_url(cik), user_agent=user_agent)
    return json.loads(raw.decode("utf-8"))


def download_exhibit(
    cik: str | int,
    accession_number: str,
    filename: str,
    dest_dir: str | Path,
    user_agent: str | None = None,
) -> Path:
    """Download an ABS-EE XML exhibit into ``dest_dir`` and return its local path."""
    url = build_exhibit_url(cik, accession_number, filename)
    payload = fetch_bytes(url, user_agent=user_agent)
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    out_path = out_dir / f"{normalize_cik(cik)}_{accession_number.replace('-', '')}_{safe_name}"
    out_path.write_bytes(payload)
    return out_path
