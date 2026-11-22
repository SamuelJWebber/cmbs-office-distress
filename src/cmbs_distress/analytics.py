"""Distress analytics over parsed ABS-EE CMBS loans.

All definitions here are deliberately simple and written down so a reader can
recompute any number on the dashboard by hand from the loan table.

Definitions (fixture-proven by tests/test_analytics.py)
-------------------------------------------------------
* **Appraisal haircut** (per loan) = ``1 - mostRecentValuationAmount / valuationSecuritizationAmount``.
  Positive = value has fallen since securitisation; negative = value has risen.
  ``None`` when either value is missing or the securitisation value is not positive.
  Pool haircut = ``1 - sum(recent) / sum(securitisation)`` over loans that report both
  (value-weighted, not an average of per-loan ratios).
* **Maturity wall** = current balance of loans whose ``maturityDate`` falls in calendar
  years 2026-2028 inclusive, and its share of total current balance. Loans already past
  maturity but still in the pool are *included* if their maturity year is in range.
* **Distress buckets** (strict inequalities):
  DSCR < 1.00x; physical occupancy < 75%; in special servicing (a special-servicer
  transfer date is reported). "Any distress" = at least one of the three.
  Shares are of *total* current balance; a loan that does not report a metric is not
  counted as distressed on that metric (the ``reported`` count says how many did).
* **Weighted DSCR / occupancy** are current-balance weighted over loans that report the
  metric and carry a positive balance (liquidated loans with zero balance drop out).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .parser import Loan

DSCR_THRESHOLD = 1.0
OCCUPANCY_THRESHOLD = 0.75
WALL_START = 2026
WALL_END = 2028

BUCKETS: dict[str, str] = {
    "dscr_lt_1": "DSCR < 1.00x",
    "occ_lt_75": "Occupancy < 75%",
    "special_servicing": "Special servicing",
    "any": "Any distress flag",
}


def _bal(loan: Loan) -> float:
    return loan.current_balance or 0.0


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def haircut(loan: Loan) -> float | None:
    sec, recent = loan.valuation_securitization, loan.most_recent_valuation
    if sec is None or recent is None or sec <= 0:
        return None
    return 1.0 - recent / sec


def pool_haircut(loans: Iterable[Loan]) -> dict:
    sec_sum = recent_sum = 0.0
    with_both = missing = 0
    for loan in loans:
        if haircut(loan) is None:
            missing += 1
            continue
        with_both += 1
        sec_sum += loan.valuation_securitization  # type: ignore[operator]
        recent_sum += loan.most_recent_valuation  # type: ignore[operator]
    return {
        "haircut": (1.0 - recent_sum / sec_sum) if sec_sum else None,
        "securitization_value": sec_sum,
        "recent_value": recent_sum,
        "loans_with_both": with_both,
        "loans_missing_appraisal": missing,
    }


def in_wall(loan: Loan, start: int = WALL_START, end: int = WALL_END) -> bool:
    return loan.maturity_date is not None and start <= loan.maturity_date.year <= end


def maturity_wall(loans: Iterable[Loan], start: int = WALL_START, end: int = WALL_END) -> dict:
    loans = list(loans)
    total = sum(_bal(l) for l in loans)
    by_year = {y: 0.0 for y in range(start, end + 1)}
    by_year_office = {y: 0.0 for y in range(start, end + 1)}
    count = 0
    for loan in loans:
        if in_wall(loan, start, end):
            count += 1
            y = loan.maturity_date.year  # type: ignore[union-attr]
            by_year[y] += _bal(loan)
            if loan.is_office:
                by_year_office[y] += _bal(loan)
    balance = sum(by_year.values())
    return {
        "start": start,
        "end": end,
        "balance": balance,
        "share": _ratio(balance, total),
        "count": count,
        "total_balance": total,
        "by_year": by_year,
        "by_year_office": by_year_office,
    }


def loan_flags(loan: Loan) -> list[str]:
    flags = []
    if loan.dscr is not None and loan.dscr < DSCR_THRESHOLD:
        flags.append("dscr_lt_1")
    if loan.occupancy is not None and loan.occupancy < OCCUPANCY_THRESHOLD:
        flags.append("occ_lt_75")
    if loan.in_special_servicing:
        flags.append("special_servicing")
    return flags


def distress_buckets(loans: Iterable[Loan]) -> dict:
    loans = list(loans)
    total = sum(_bal(l) for l in loans)
    reported = {
        "dscr_lt_1": sum(1 for l in loans if l.dscr is not None),
        "occ_lt_75": sum(1 for l in loans if l.occupancy is not None),
        "special_servicing": len(loans),
        "any": len(loans),
    }
    out = {}
    for key, label in BUCKETS.items():
        if key == "any":
            hit = [l for l in loans if loan_flags(l)]
        else:
            hit = [l for l in loans if key in loan_flags(l)]
        bal = sum(_bal(l) for l in hit)
        out[key] = {
            "label": label,
            "count": len(hit),
            "balance": bal,
            "share": _ratio(bal, total),
            "reported": reported[key],
        }
    return out


def _weighted(loans: list[Loan], attr: str) -> float | None:
    num = den = 0.0
    for l in loans:
        v = getattr(l, attr)
        b = _bal(l)
        if v is not None and b > 0:
            num += v * b
            den += b
    return _ratio(num, den)


def group_stats(loans: Iterable[Loan]) -> dict:
    loans = list(loans)
    total = sum(_bal(l) for l in loans)
    distressed = sum(_bal(l) for l in loans if loan_flags(l))
    wall = sum(_bal(l) for l in loans if in_wall(l))
    return {
        "loans": len(loans),
        "balance": total,
        "wtd_dscr": _weighted(loans, "dscr"),
        "wtd_occupancy": _weighted(loans, "occupancy"),
        "haircut": pool_haircut(loans)["haircut"],
        "distressed_balance": distressed,
        "distressed_share": _ratio(distressed, total),
        "wall_balance": wall,
        "wall_share": _ratio(wall, total),
        "special_servicing_balance": sum(_bal(l) for l in loans if l.in_special_servicing),
        "realized_loss": sum(l.realized_loss or 0.0 for l in loans),
    }


def rollup(loans: Iterable[Loan], keys: tuple[str, ...] = ("metro", "property_type")) -> list[dict]:
    groups: dict[tuple, list[Loan]] = defaultdict(list)
    for l in loans:
        groups[tuple(getattr(l, k) for k in keys)].append(l)
    rows = []
    for gk in sorted(groups, key=lambda t: tuple(str(x) for x in t)):
        row = dict(zip(keys, gk))
        row.update(group_stats(groups[gk]))
        rows.append(row)
    return rows


def summarize(loans: Iterable[Loan]) -> dict:
    loans = list(loans)
    office = [l for l in loans if l.is_office]
    return {
        "overall": group_stats(loans),
        "office": group_stats(office),
        "non_office": group_stats([l for l in loans if not l.is_office]),
        "pool_haircut": pool_haircut(loans),
        "office_haircut": pool_haircut(office),
        "maturity_wall": maturity_wall(loans),
        "office_maturity_wall": maturity_wall(office),
        "distress": distress_buckets(loans),
        "office_distress": distress_buckets(office),
        "by_metro_type": rollup(loans, ("metro", "property_type")),
        "by_metro": rollup(loans, ("metro",)),
        "by_type": rollup(loans, ("property_type",)),
    }
