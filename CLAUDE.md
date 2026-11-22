# CLAUDE.md — Engineering Rules for `cmbs-office-distress`

Follow every rule below before editing, testing, or publishing changes in this repository.

## 1. Evidence Vocabulary (Mandatory)

Label every analytical or schema claim using exactly one of four tiers:

1. **fixture-proven** — Asserted by a passing pytest against synthetic fixtures in `tests/fixtures/`.
2. **seen on the data** — Verified directly on a real downloaded SEC Form ABS-EE XML filing.
3. **seen live** — Verified on the deployed GitHub Pages page or a live GitHub Actions workflow run.
4. **unverified** — Written from specification or memory; not yet confirmed on real data.

Never upgrade a claim's evidence tier without actually observing it. In particular, never describe synthetic fixture numbers as real CMBS market data.

## 2. SEC EDGAR & Network Discipline

- **NEVER hit SEC EDGAR from pytest.** Every test must run 100% offline. `tests/test_ingest.py` monkeypatches `urllib.request.urlopen` to fail immediately on any network attempt, and `tests/test_no_leaks.py` scans tests to block live fetcher invocations.
- **Compliant User-Agent only:** Live requests in `src/cmbs_distress/ingest.py` must read `CMBS_CONTACT` from the environment and validate `"<name> <contact-email>"` format. **Never put a URL or parentheses** in the User-Agent string.
- Respect SEC fair-access pacing (`DEFAULT_MIN_INTERVAL_SECONDS >= 0.25`).

## 3. Zero-Leak Policy (Enforced by `tests/test_no_leaks.py`)

Never commit:
- Personal email addresses (only RFC 2606 domains such as `example.com` / `example.org` may appear in docs, fixtures, or tests).
- Local filesystem paths containing home/user directories (Windows or macOS).
- API keys, GitHub tokens, or private keys.
- Virtual environments (`.venv/`), caches (`__pycache__/`, `.pytest_cache/`), or scratch artifacts.

## 4. Deterministic Static Output

- `docs/index.html` must remain 100% self-contained (zero external `<script src>`, `<link href="http...">`, or web fonts) and deterministic (no wall-clock generation timestamps).
- Whenever parser, analytics, fixture, or renderer code changes, regenerate `docs/index.html` via `python -m cmbs_distress build` and verify with `python -m cmbs_distress build --check` and `pytest -v`.

## 5. Parser & Analytics Invariants

- Label-based XML parsing only (match local element names, ignore namespace URIs/prefixes).
- Reject `<!DOCTYPE` and `<!ENTITY` declarations before parsing XML.
- Missing numeric fields stay `None` — never coerce missing appraisals, DSCR, or occupancy to `0`.
- Distress thresholds are strict inequalities (`DSCR < 1.00`, `occupancy < 0.75`).
- Group/pool haircuts are value-weighted (`1 - sum(recent) / sum(securitization)`), never simple averages of per-loan ratios.
