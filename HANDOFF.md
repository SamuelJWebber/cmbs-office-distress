# HANDOFF.md

## Current State (v0.2.0)

- **Repository:** `SamuelJWebber/cmbs-office-distress`
- **Status:** Upgraded from v0.1 snapshot prototype to **v0.2 longitudinal multi-filing CMBS distress tracker** using pure Python 3.12 stdlib + `pytest` offline test suite and deterministic self-contained dashboard at `docs/index.html`.
- **Evidence level:** All parser, snapshot analytics, longitudinal migration/velocity/cohort math, rendering, CLI, and SEC User-Agent validation behaviors are **fixture-proven** against two dated synthetic ABS-EE XML fixtures (`tests/fixtures/synthetic_cmbs_absee.xml` dated `2026-10-15` and `tests/fixtures/synthetic_cmbs_absee_2026_10.xml` dated `2026-11-15`). Live SEC EDGAR EX-102 schema tags and HTTP ingestion remain **unverified** until `CMBS_CONTACT` is configured in GitHub Actions.

## What Changed in v0.2

- **Second-period synthetic fixture (`tests/fixtures/synthetic_cmbs_absee_2026_10.xml`)** plus baseline `originationDate` & `paymentStatusLoanCode` tags on `tests/fixtures/synthetic_cmbs_absee.xml` (preserving all v0.1 numbers):
  - Simulates the October 2026 remittance cycle (`reportingPeriodEndDate = 2026-11-15`, +31 days after `2026-10-15`).
  - Fresh office appraisal cuts on NYC (`LN-001` `-$14.0M`), SF (`LN-004` `-$8.4M`), Chicago (`LN-007` `-$5.0M`), and Dallas (`LN-010` `-$3.2M`), offset by a modest Chicago Industrial mark-up (`LN-009` `+$1.2M`) -> matched MoM valuation change `-$29.4M` (`-3.8%`), office `-$30.6M` (`-7.4%`, `+4.7pp` matched haircut widening). `LN-002` receives its first post-securitization appraisal (`$66.5M`) and is tracked separately.
  - 2 new special-servicing transfers (`LN-002` `$55.0M` NYC Office, `LN-010` `$38.0M` Dallas Office -> `$93.0M` inflow).
  - DSCR `< 1.00x` threshold crossings (`LN-002` `1.00x -> 0.91x`, `LN-010` `1.18x -> 0.94x`) and 3 combined credit-tier downgrades (`$137.0M`: `LN-012` & `LN-014` Healthy -> Watch; `LN-002` Watch -> Distressed).
  - 1 liquidation with realized loss (`LN-008` Chicago Retail: `$22.0M -> $0`, cumulative loss `$1.2M -> $8.7M`, incremental loss `+$7.5M`, `34.1%` severity; prior-liquidated `LN-005` `$14.5M` not double-counted).
  - 2 new 30+d delinquency transitions (`LN-002`, `LN-006` `0 -> 1`, `$83.0M`) plus 3 delinquency severity rolls (`LN-004` `3 -> 5`, `LN-007` `2 -> 3`, `LN-013` `1 -> 2`).
- **Parser extensions (`src/cmbs_distress/parser.py`)**: Added label-based `originationDate` (`vintage_year`) and `paymentStatusLoanCode` (`payment_status`, `is_delinquent`, severity ordering) with backward-compatible `None` defaults.
- **Longitudinal engine (`src/cmbs_distress/longitudinal.py`)**: Matches 2+ chronologically sorted dated tapes by `asset_number` to compute special-servicing inflows/outflows, matched appraisal-haircut velocity (pool/office/metro/type/movers), DSCR & occupancy credit migration matrices (`healthy -> watch -> distressed -> resolved`), delinquency transitions, incremental realized losses & liquidations, and reconciling maturity-year / origination-vintage cohort exposure tables.
- **Dashboard & CLI (`src/cmbs_distress/render.py`, `src/cmbs_distress/cli.py`, `docs/index.html`)**:
  - Added dedicated **Monthly Migration & Appraisal Velocity** section with 5-card MoM KPI strip, inline SVG appraisal valuation waterfall, inline SVG multi-period distress & office haircut trajectory chart, color-coded DSCR & occupancy migration matrix, consolidated monthly loan transition log, and maturity-year + origination-vintage cohort exposure tables.
  - Added `compare` subcommand and automatic multi-period detection in `build` and `inspect` (while keeping single-file v0.1 CLI usage backward-compatible).
  - Zero external CDN assets, deterministic output, strict HTML + JSON `<script>` XSS escaping, explicit v0.2 fixture-proven badge.
- **Tests (`tests/test_longitudinal.py` + updated `test_parser.py`, `test_render.py`, `test_cli.py`, `test_no_leaks.py`)**: Verifies all migration, velocity, cohort reconciliation, 3-period trajectory, XSS escaping, leak-guard, and CLI invariants 100% offline.

## Next Steps (v0.3)

1. Configure `CMBS_CONTACT` repository secret in GitHub Actions (`"<name> <contact-email>"`).
2. Add a curated list of real CMBS conduit/SASB issuing-entity CIKs and run a manual live workflow downloading 2–3 consecutive monthly EX-102 XML filings per trust.
3. Inspect downloaded EX-102 XML filings to upgrade primary/alternate labels (including `paymentStatusLoanCode` and `originationDate`) from **unverified** to **seen on the data** and capture real servicer edge cases as regression fixtures.
4. Confirm GitHub Pages deployment from `main` branch `/docs` folder as **seen live**.
