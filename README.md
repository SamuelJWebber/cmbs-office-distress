# cmbs-office-distress

Loan-level SEC Form ABS-EE (CMBS) XML parser, longitudinal multi-filing distress analytics, and self-contained static GitHub Pages dashboard tracking appraisal-reduction haircuts, monthly credit migration, appraisal velocity, the 2026–2028 maturity wall, and distress buckets (DSCR < 1.00x, occupancy < 75%, special servicing, 30+d delinquency) across metros, property types, origination vintages, and maturity cohorts.

> **Status (v0.2):** **Fixture-proven v0.2 longitudinal prototype** — all metrics currently rendered in [`docs/index.html`](docs/index.html) come from two dated synthetic 16-loan SEC ABS-EE CMBS XML fixtures (`tests/fixtures/synthetic_cmbs_absee.xml` dated `2026-10-15` and `tests/fixtures/synthetic_cmbs_absee_2026_10.xml` dated `2026-11-15`). The CI workflow validates a configured contact and rebuilds the fixture dashboard. Live filing retrieval is a separate manual operation.

## Evidence Discipline

Every claim in this repository uses a strict four-tier evidence vocabulary:

| Tier | Meaning | Current v0.2 scope |
| --- | --- | --- |
| **fixture-proven** | Verified by automated pytest assertions against synthetic XML fixtures | XML parser, snapshot analytics, longitudinal multi-filing migration & velocity engine, static HTML/SVG dashboard, CLI, User-Agent validation |
| **seen on the data** | Observed in a real downloaded SEC Form ABS-EE EX-102 XML filing | None yet |
| **seen live** | Observed on deployed GitHub Pages or a live Actions ingestion run | None yet |
| **unverified** | Written from SEC ABS-EE schema specification, not yet checked against live filings | Live EX-102 tag variations (including `paymentStatusLoanCode` and `originationDate`), alternate label fallbacks, live EDGAR fetch helpers |

## What Is Built

- **Pure Python 3.12 standard library at runtime** (`pyproject.toml`, zero heavy third-party runtime dependencies; `pytest` only for tests).
- **`src/cmbs_distress/parser.py`** — Label-based, namespace-agnostic SEC Form ABS-EE CMBS XML parser extracting:
  - `assetNumber`, `reportingPeriodEndDate`, `originationDate`, `maturityDate`
  - `propertyName`, `propertyTypeCode`, `propertyCity`, `propertyState`
  - `valuationSecuritizationAmount`, `mostRecentValuationAmount`
  - `mostRecentDebtServiceCoverageNetOperatingIncomePercentage`, `mostRecentPhysicalOccupancyPercentage`
  - `specialServicerTransferDate`, `paymentStatusLoanCode`, `currentEndingScheduledBalanceAmount`, `realizedLossToTrustAmount`
  - Rejects `DOCTYPE`/`ENTITY` declarations, preserves missing values as `None`, normalizes 0–100 occupancy scales with explicit parse notes, and tracks multi-property loans.
- **`src/cmbs_distress/analytics.py`** — Single-period pool analytics:
  - Value-weighted appraisal haircut (`1 - sum(recent) / sum(securitization)` over loans reporting both valuations)
  - 2026–2028 maturity-wall balance, share, and office vs. other breakdown by calendar year
  - Distress buckets (`DSCR < 1.00x`, `Occupancy < 75%`, `Special servicing`, and `Any distress flag`) with strict boundary semantics and reporting coverage counts
  - Metro × Property-Type rollups reconciling 100% of loan count, balance, and realized loss
- **`src/cmbs_distress/longitudinal.py`** — Multi-filing longitudinal surveillance engine matching loans by `asset_number` across 2+ dated loan tapes:
  - **Special-servicing inflows & outflows**: newly transferred loans ($ and count, office breakdown), returns to master servicing, and resolutions out of special servicing
  - **Appraisal-haircut velocity**: matched-pair month-over-month valuation change ($ and %) and value-weighted haircut widening across pool, office, metro, property type, and individual movers (tracking newly appraised loans separately without distorting matched velocity)
  - **DSCR & occupancy migration matrix**: strict-threshold transitions across `Healthy` (DSCR >= 1.25x & Occ >= 85%) -> `Watch` (1.00x–1.25x or 75%–85%) -> `Distressed` (DSCR < 1.00x or Occ < 75%) plus `Resolved / Liq`, computed for combined credit tier and standalone DSCR/occupancy alongside explicit `< 1.00x` / `< 75%` threshold crossings
  - **Delinquency transitions**: newly 30+d delinquent loans, cures, severity rolls, and resolutions derived strictly from reported `paymentStatusLoanCode` values
  - **Incremental realized losses & liquidations**: month-over-month realized-loss deltas (never double-counting previously liquidated loans), liquidation loss severity, full payoffs without loss, and active modification losses
  - **Vintage & maturity-year cohort exposure tables**: reconciling pool share, office share, distress, special-servicing inflows, haircuts, MoM valuation velocity, and incremental losses across maturity years and origination vintages
- **`src/cmbs_distress/render.py`** — Generates a deterministic, self-contained static dashboard at `docs/index.html` (embedded CSS, 5 inline SVG charts including MoM appraisal valuation waterfall and multi-period distress trajectory, credit migration matrix, transition log, maturity/vintage cohort tables, Metro × Property-Type heatmap, and client-side filterable/sortable loan table with zero external scripts or fonts).
- **`src/cmbs_distress/ingest.py`** — Actions-ready SEC EDGAR fetcher enforcing a compliant `"<name> <contact-email>"` User-Agent from `CMBS_CONTACT` (no URLs or parentheses) and polite request pacing. Never invoked by tests.

## Quickstart

```bash
python -m pip install -e ".[test]"
python -m pytest -v

# Inspect two-period synthetic fixture summary (defaults to both v0.2 fixtures)
python -m cmbs_distress inspect

# Compare dated loan tapes directly (add --json for machine-readable output)
python -m cmbs_distress compare

# Regenerate docs/index.html cleanly
python -m cmbs_distress build

# Verify docs/index.html is in sync with the fixtures
python -m cmbs_distress build --check
```

## Reproduce the fixture dashboard

The two source tapes and the expected dashboard are committed. These commands check their SHA-256 digests, rerun the period comparison and verify that a fresh render exactly matches the saved HTML.

```bash
sha256sum -c checks/fixture-dashboard.sha256
python -m cmbs_distress compare
python -m cmbs_distress build --check
```

At revision `ca00cde11475d64fb37b5af04934fbccf1fec340`, 16 synthetic loans span October 15 and November 15. Two loans enter special servicing ($93.0 million). Matched appraisals change by −$29.4 million. These are fixture-proven outputs. No underlying market loan tape has been archived in this revision.

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
3. Download two or more monthly EX-102 XML filings using `cmbs_distress.ingest` and pass them to:
   ```bash
   python -m cmbs_distress build -i path/to/month1.xml path/to/month2.xml -o docs/index.html
   ```

## Repository Layout

```text
├── pyproject.toml
├── src/cmbs_distress/
│   ├── __init__.py
│   ├── __main__.py
│   ├── parser.py
│   ├── analytics.py
│   ├── longitudinal.py
│   ├── render.py
│   ├── ingest.py
│   └── cli.py
├── tests/
│   ├── fixtures/
│   │   ├── synthetic_cmbs_absee.xml
│   │   └── synthetic_cmbs_absee_2026_10.xml
│   ├── test_parser.py
│   ├── test_analytics.py
│   ├── test_longitudinal.py
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

This repository is an engineering prototype for public-filing analytics and is not investment advice. The v0.2 synthetic fixtures do not represent any real commercial mortgage loan, borrower, property, or securitization trust.
