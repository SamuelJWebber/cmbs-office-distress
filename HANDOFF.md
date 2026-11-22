# HANDOFF.md

## Current State (v0.1.0)

- **Repository:** `SamuelJWebber/cmbs-office-distress`
- **Status:** Clean v0.1 prototype built with pure Python 3.12 stdlib + `pytest` test suite and deterministic static HTML dashboard at `docs/index.html`.
- **Evidence level:** All parser, analytics, rendering, CLI, and User-Agent validation behaviors are **fixture-proven** against `tests/fixtures/synthetic_cmbs_absee.xml`. Live SEC EDGAR ABS-EE EX-102 schema tags and HTTP ingestion are **unverified** until `CMBS_CONTACT` is configured in GitHub Actions.

## What Was Built

- `src/cmbs_distress/parser.py`: Namespace-agnostic, label-based SEC Form ABS-EE CMBS XML parser covering all 12 core loan-level fields, multi-property notes, 0–100 occupancy normalization, date format handling, metro lookup, and DTD/ENTITY rejection.
- `src/cmbs_distress/analytics.py`: Value-weighted appraisal haircuts, 2026–2028 maturity wall (overall and office), strict distress buckets (`DSCR < 1.00x`, `Occupancy < 75%`, `Special servicing`), balance-weighted DSCR/occupancy, realized loss totals, and Metro × Property-Type rollups.
- `src/cmbs_distress/render.py`: Self-contained, deterministic static HTML dashboard (`docs/index.html`) with 3 inline SVG charts, Metro × Property-Type heatmap, client-side filterable/sortable loan table, XSS-safe JSON escaping, and explicit v0.1 fixture-proven banner.
- `src/cmbs_distress/ingest.py`: Offline-tested SEC EDGAR fetcher using `CMBS_CONTACT` (`"<name> <contact-email>"`, rejecting URLs/parentheses) and polite pacing; never called by tests.
- `src/cmbs_distress/cli.py`: `build` (with `--check` drift guard), `inspect`, and `check-contact` subcommands.
- `tests/`: Unit/integration suite (`test_parser.py`, `test_analytics.py`, `test_render.py`, `test_ingest.py`, `test_cli.py`, `test_no_leaks.py`).
- `.github/workflows/ci.yml`: Runs `pytest -v` and `python -m cmbs_distress build --check` on Python 3.12.

## Next Steps (v0.2)

1. Configure `CMBS_CONTACT` repository secret in GitHub Actions (`"<name> <contact-email>"`).
2. Add a curated list of real CMBS conduit/SASB issuing-entity CIKs and run a manual live workflow to download 1–3 recent EX-102 XML filings.
3. Inspect the downloaded EX-102 XML files to upgrade primary/alternate labels from **unverified** to **seen on the data** (and log any real-world servicer quirks in `parser.py` + regression fixtures).
4. Enable GitHub Pages from `main` branch `/docs` folder and confirm **seen live**.
