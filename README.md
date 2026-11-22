# cmbs-office-distress

Loan-level SEC Form ABS-EE (CMBS) XML parser, distress analytics, and self-contained static GitHub Pages dashboard tracking appraisal-reduction haircuts, the 2026–2028 maturity wall, and distress buckets (DSCR < 1.00x, occupancy < 75%, special servicing) across metros and property types.

> **Status (v0.1):** **Fixture-proven v0.1 prototype** — all metrics currently rendered in [`docs/index.html`](docs/index.html) come from a synthetic 16-loan SEC ABS-EE CMBS XML fixture (`tests/fixtures/synthetic_cmbs_absee.xml`). Configure the `CMBS_CONTACT` repository secret in GitHub Actions to enable live SEC EDGAR ABS-EE ingestion.

## Evidence Discipline

Every claim in this repository uses a strict four-tier evidence vocabulary:

| Tier | Meaning | Current v0.1 scope |
| --- | --- | --- |
| **fixture-proven** | Verified by automated pytest assertions against synthetic XML fixtures | XML parser, analytics math, static HTML/SVG dashboard, User-Agent validation |
| **seen on the data** | Observed in a real downloaded SEC Form ABS-EE EX-102 XML filing | None yet |
| **seen live** | Observed on deployed GitHub Pages or a live Actions ingestion run | None yet |
| **unverified** | Written from SEC ABS-EE schema specification, not yet checked against live filings | Live EX-102 tag variations, alternate label fallbacks, live EDGAR fetch helpers |

## What Is Built

- **Pure Python 3.12 standard library at runtime** (`pyproject.toml`, zero heavy third-party runtime dependencies; `pytest` only for tests).
- **`src/cmbs_distress/parser.py`** — Label-based, namespace-agnostic SEC Form ABS-EE CMBS XML parser extracting:
  - `assetNumber`, `propertyName`, `propertyTypeCode`, `propertyCity`, `propertyState`
  - `valuationSecuritizationAmount`, `mostRecentValuationAmount`
  - `mostRecentDebtServiceCoverageNetOperatingIncomePercentage`, `mostRecentPhysicalOccupancyPercentage`
  - `specialServicerTransferDate`, `maturityDate`, `currentEndingScheduledBalanceAmount`, `realizedLossToTrustAmount`
  - Rejects `DOCTYPE`/`ENTITY` declarations, preserves missing values as `None`, normalizes 0–100 occupancy scales with explicit parse notes, and tracks multi-property loans.
- **`src/cmbs_distress/analytics.py`** — Computes:
  - Value-weighted appraisal haircut (`1 - sum(recent) / sum(securitization)` over loans reporting both valuations)
  - 2026–2028 maturity-wall balance, share, and office vs. other breakdown by calendar year
  - Distress buckets (`DSCR < 1.00x`, `Occupancy < 75%`, `Special servicing`, and `Any distress flag`) with strict boundary semantics and reporting coverage counts
  - Metro × Property-Type rollups reconciling 100% of loan count, balance, and realized loss
- **`src/cmbs_distress/render.py`** — Generates a deterministic, self-contained static dashboard at `docs/index.html` (embedded CSS, 3 inline SVG charts, Metro × Property-Type heatmap, and client-side filterable/sortable loan table with zero external scripts or fonts).
- **`src/cmbs_distress/ingest.py`** — Actions-ready SEC EDGAR fetcher enforcing a compliant `"<name> <contact-email>"` User-Agent from `CMBS_CONTACT` (no URLs or parentheses) and polite request pacing. Never invoked by tests.

## Quickstart

```bash
python -m pip install -e ".[test]"
pytest -v

# Inspect synthetic fixture summary
python -m cmbs_distress inspect

# Regenerate docs/index.html cleanly
python -m cmbs_distress build

# Verify docs/index.html is in sync with the fixture
python -m cmbs_distress build --check
```

## Configuring Live SEC EDGAR Ingestion

SEC EDGAR fair-access guidelines require every automated request to declare a contact in the `User-Agent` header in `"<name> <contact-email>"` format (never a URL inside parentheses).

1. Add a GitHub Actions secret named `CMBS_CONTACT`, e.g.:
   ```text
   cmbs-office-distress maintainer@example.com
   ```
2. Validate locally without making any network call:
   ```bash
   CMBS_CONTACT="cmbs-office-distress maintainer@example.com" python -m cmbs_distress check-contact
   ```
3. Download live EX-102 XML filings using `cmbs_distress.ingest` and pass them to `python -m cmbs_distress build -i path/to/ex102.xml -o docs/index.html`.

## Repository Layout

```text
├── pyproject.toml
├── src/cmbs_distress/
│   ├── __init__.py
│   ├── __main__.py
│   ├── parser.py
│   ├── analytics.py
│   ├── render.py
│   ├── ingest.py
│   └── cli.py
├── tests/
│   ├── fixtures/synthetic_cmbs_absee.xml
│   ├── test_parser.py
│   ├── test_analytics.py
│   ├── test_render.py
│   ├── test_ingest.py
│   ├── test_cli.py
│   └── test_no_leaks.py
├── docs/index.html
├── .github/workflows/ci.yml
├── CLAUDE.md
└── HANDOFF.md
```

## Disclaimer

This repository is an engineering prototype for public-filing analytics and is not investment advice. The v0.1 synthetic fixture does not represent any real commercial mortgage loan, borrower, property, or securitization trust.
