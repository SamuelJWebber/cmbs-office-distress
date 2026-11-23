"""Longitudinal multi-filing CMBS distress analytics across dated ABS-EE loan tapes.

Compares two or more dated ``ParseResult`` loan tapes matched by ``asset_number`` to
quantify month-over-month (or multi-period) credit deterioration:

* **Special-servicing inflows** — newly transferred loans (count, balance, office split),
  returns to master servicing (outflows), and resolutions out of special servicing.
* **Appraisal-haircut velocity** — matched-pair valuation change ($ and %) and
  value-weighted haircut widening across pool, office, metro, property type, and
  individual reappraised loans (never inventing values for loans missing an appraisal
  in either period).
* **DSCR & occupancy migration matrix** — strict-threshold transitions across
  ``healthy`` -> ``watch`` -> ``distressed`` (plus ``resolved`` for liquidations/payoffs),
  computed for combined credit tier as well as standalone DSCR and occupancy.
* **Delinquency transitions** — newly 30+d delinquent loans, cures, severity rolls, and
  resolutions based on reported ``paymentStatusLoanCode`` values.
* **Incremental realized losses & liquidations** — month-over-month realized loss deltas
  (never double-counting prior-period cumulative losses), liquidation loss severity, and
  full payoffs without loss.
* **Vintage & maturity-year cohort exposure** — pool share, office concentration,
  distress, special-servicing inflows, haircuts, MoM valuation change, and incremental
  losses broken out by maturity year and origination vintage.

Evidence level: all calculations and invariants in this module are **fixture-proven**
against ``tests/fixtures/synthetic_cmbs_absee.xml`` and
``tests/fixtures/synthetic_cmbs_absee_2026_10.xml``.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .analytics import (
    DSCR_THRESHOLD,
    OCCUPANCY_THRESHOLD,
    WALL_END,
    WALL_START,
    distress_buckets,
    group_stats,
    haircut,
    in_wall,
    loan_flags,
    pool_haircut,
    summarize,
)
from .parser import (
    PAYMENT_STATUS_SEVERITY,
    Loan,
    ParseResult,
    parse_file,
)

DSCR_DISTRESSED = DSCR_THRESHOLD  # < 1.00x (strict)
DSCR_WATCH = 1.25  # 1.00x <= DSCR < 1.25x
OCC_DISTRESSED = OCCUPANCY_THRESHOLD  # < 75% (strict)
OCC_WATCH = 0.85  # 75% <= Occupancy < 85%

CORE_TIERS: tuple[str, ...] = ("healthy", "watch", "distressed")
TIER_RANK: dict[str, int] = {"healthy": 0, "watch": 1, "distressed": 2}
TIER_LABELS: dict[str, str] = {
    "healthy": "Healthy",
    "watch": "Watch",
    "distressed": "Distressed",
    "resolved": "Resolved / Liq",
    "unreported": "Unreported",
}


def _bal(loan: Loan) -> float:
    return loan.current_balance or 0.0


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def dscr_tier(dscr: float | None) -> str:
    """Classify standalone DSCR into healthy / watch / distressed / unreported."""
    if dscr is None:
        return "unreported"
    if dscr < DSCR_DISTRESSED:
        return "distressed"
    if dscr < DSCR_WATCH:
        return "watch"
    return "healthy"


def occupancy_tier(occupancy: float | None) -> str:
    """Classify standalone physical occupancy (0-1 fraction) into a credit tier."""
    if occupancy is None:
        return "unreported"
    if occupancy < OCC_DISTRESSED:
        return "distressed"
    if occupancy < OCC_WATCH:
        return "watch"
    return "healthy"


def credit_tier(loan: Loan) -> str:
    """Combined DSCR & occupancy credit tier (worst reported metric wins).

    Missing metrics are never coerced to zero: if only one metric is reported,
    that metric determines the tier; if neither is reported, returns ``"unreported"``.
    """
    dtier = dscr_tier(loan.dscr)
    otier = occupancy_tier(loan.occupancy)
    if "distressed" in (dtier, otier):
        return "distressed"
    if "watch" in (dtier, otier):
        return "watch"
    if "healthy" in (dtier, otier):
        return "healthy"
    return "unreported"


def load_dated_tapes(paths: Iterable[str | Path]) -> list[ParseResult]:
    """Parse one or more XML files and group them by ``as_of`` reporting date.

    Files sharing the same ``as_of`` date are combined into one ``ParseResult`` (for
    example, multiple trusts for the same remittance month). Returned tapes are
    ordered chronologically by ``as_of``, with undated tapes placed last.
    """
    grouped: dict[dt.date | None, list[ParseResult]] = defaultdict(list)
    for path in paths:
        res = parse_file(path)
        grouped[res.as_of].append(res)

    dated_keys = sorted(d for d in grouped if d is not None)
    ordered_keys: list[dt.date | None] = [*dated_keys]
    if None in grouped:
        ordered_keys.append(None)

    out: list[ParseResult] = []
    for key in ordered_keys:
        batch = grouped[key]
        if len(batch) == 1:
            out.append(batch[0])
        else:
            loans: list[Loan] = []
            notes: list[str] = []
            sources: list[str] = []
            for item in batch:
                loans.extend(item.loans)
                notes.extend(item.notes)
                sources.append(item.source)
            out.append(ParseResult(loans=loans, source=", ".join(sources), notes=notes))
    return out


def order_tapes(tapes: Sequence[ParseResult]) -> list[ParseResult]:
    """Validate and sort 2+ dated ``ParseResult`` tapes chronologically by ``as_of``."""
    if len(tapes) < 2:
        raise ValueError("Longitudinal comparison requires at least 2 dated loan tapes.")
    seen: set[dt.date] = set()
    for tape in tapes:
        if tape.as_of is None:
            raise ValueError(f"Tape {tape.source!r} is missing reportingPeriodEndDate (as_of).")
        if tape.as_of in seen:
            raise ValueError(f"Duplicate reporting period end date {tape.as_of.isoformat()} across tapes.")
        seen.add(tape.as_of)
    return sorted(tapes, key=lambda t: t.as_of)  # type: ignore[arg-type,return-value]


def _index_by_asset(tape: ParseResult) -> dict[str, Loan]:
    by_id: dict[str, Loan] = {}
    for loan in tape.loans:
        if loan.asset_number in by_id:
            raise ValueError(f"{tape.source}: duplicate asset_number {loan.asset_number!r}")
        by_id[loan.asset_number] = loan
    return by_id


def _active_delinquent_stats(loans: Iterable[Loan]) -> tuple[int, float, float | None]:
    loans_list = list(loans)
    total_bal = sum(_bal(l) for l in loans_list)
    delinq = [l for l in loans_list if _bal(l) > 0 and l.is_delinquent]
    bal = sum(_bal(l) for l in delinq)
    return len(delinq), bal, _ratio(bal, total_bal)


def period_snapshot(tape: ParseResult) -> dict:
    """Compact period-level KPI snapshot suitable for multi-period trajectory charts."""
    s = summarize(tape.loans)
    ov, of = s["overall"], s["office"]
    d = s["distress"]
    del_count, del_bal, del_share = _active_delinquent_stats(tape.loans)
    active_loans = sum(1 for l in tape.loans if _bal(l) > 0)
    return {
        "as_of": tape.as_of.isoformat() if tape.as_of else None,
        "source": tape.source,
        "loans": ov["loans"],
        "active_loans": active_loans,
        "balance": ov["balance"],
        "office_balance": of["balance"],
        "pool_haircut": s["pool_haircut"]["haircut"],
        "office_haircut": s["office_haircut"]["haircut"],
        "distressed_balance": d["any"]["balance"],
        "distressed_share": d["any"]["share"],
        "special_servicing_count": d["special_servicing"]["count"],
        "special_servicing_balance": d["special_servicing"]["balance"],
        "special_servicing_share": d["special_servicing"]["share"],
        "delinquent_count": del_count,
        "delinquent_balance": del_bal,
        "delinquent_share": del_share,
        "wtd_dscr": ov["wtd_dscr"],
        "office_wtd_dscr": of["wtd_dscr"],
        "wtd_occupancy": ov["wtd_occupancy"],
        "office_wtd_occupancy": of["wtd_occupancy"],
        "cumulative_loss": ov["realized_loss"],
    }


def _special_servicing_flows(
    prev_by_id: dict[str, Loan],
    curr_by_id: dict[str, Loan],
    matched_ids: list[str],
    removed_ids: list[str],
) -> dict:
    inflows: list[dict] = []
    outflows: list[dict] = []
    resolved: list[dict] = []

    for aid in matched_ids:
        p, c = prev_by_id[aid], curr_by_id[aid]
        if not p.in_special_servicing and c.in_special_servicing:
            inflows.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "is_office": c.is_office,
                    "balance": _bal(c),
                    "prior_balance": _bal(p),
                    "transfer_date": c.special_servicer_transfer_date.isoformat()
                    if c.special_servicer_transfer_date
                    else None,
                    "prev_dscr": p.dscr,
                    "curr_dscr": c.dscr,
                    "prev_occupancy": p.occupancy,
                    "curr_occupancy": c.occupancy,
                    "payment_status": c.payment_status,
                }
            )
        elif p.in_special_servicing and not c.in_special_servicing and _bal(c) > 0:
            outflows.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "balance": _bal(c),
                    "prior_balance": _bal(p),
                }
            )
        elif p.in_special_servicing and _bal(p) > 0 and _bal(c) == 0:
            resolved.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or p.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "prior_balance": _bal(p),
                    "incremental_loss": (c.realized_loss or 0.0) - (p.realized_loss or 0.0),
                }
            )

    for aid in removed_ids:
        p = prev_by_id[aid]
        if p.in_special_servicing and _bal(p) > 0:
            resolved.append(
                {
                    "asset_number": aid,
                    "property_name": p.property_name or "",
                    "metro": p.metro,
                    "property_type": p.property_type,
                    "prior_balance": _bal(p),
                    "incremental_loss": 0.0,
                }
            )

    inflows.sort(key=lambda r: (-r["balance"], r["asset_number"]))
    outflows.sort(key=lambda r: (-r["balance"], r["asset_number"]))
    resolved.sort(key=lambda r: (-r["prior_balance"], r["asset_number"]))

    prev_active = sum(_bal(l) for l in prev_by_id.values() if l.in_special_servicing)
    curr_active = sum(_bal(l) for l in curr_by_id.values() if l.in_special_servicing)
    office_inflows = [r for r in inflows if r["is_office"]]

    return {
        "inflow_count": len(inflows),
        "inflow_balance": sum(r["balance"] for r in inflows),
        "inflow_office_count": len(office_inflows),
        "inflow_office_balance": sum(r["balance"] for r in office_inflows),
        "inflow_loans": inflows,
        "outflow_count": len(outflows),
        "outflow_balance": sum(r["balance"] for r in outflows),
        "outflow_loans": outflows,
        "resolved_count": len(resolved),
        "resolved_prior_balance": sum(r["prior_balance"] for r in resolved),
        "resolved_loans": resolved,
        "prev_active_balance": prev_active,
        "curr_active_balance": curr_active,
        "net_active_balance_change": curr_active - prev_active,
    }


def _velocity_group(pairs: list[tuple[Loan, Loan]]) -> dict:
    """Compute matched-pair valuation velocity over (prev, curr) loan tuples."""
    prev_val_sum = curr_val_sum = 0.0
    prev_sec_sum = curr_sec_sum = 0.0
    prev_val_hc_sum = curr_val_hc_sum = 0.0
    matched_count = reappraised_count = cut_count = gain_count = unchanged_count = 0
    cut_amount = gain_amount = 0.0

    for p, c in pairs:
        pv, cv = p.most_recent_valuation, c.most_recent_valuation
        if pv is None or cv is None or pv <= 0:
            continue
        matched_count += 1
        prev_val_sum += pv
        curr_val_sum += cv
        delta = cv - pv
        if abs(delta) > 1e-6:
            reappraised_count += 1
            if delta < 0:
                cut_count += 1
                cut_amount += delta
            else:
                gain_count += 1
                gain_amount += delta
        else:
            unchanged_count += 1

        if (
            p.valuation_securitization is not None
            and p.valuation_securitization > 0
            and c.valuation_securitization is not None
            and c.valuation_securitization > 0
        ):
            prev_sec_sum += p.valuation_securitization
            curr_sec_sum += c.valuation_securitization
            prev_val_hc_sum += pv
            curr_val_hc_sum += cv

    val_change = curr_val_sum - prev_val_sum
    hc_prev = (1.0 - prev_val_hc_sum / prev_sec_sum) if prev_sec_sum > 0 else None
    hc_curr = (1.0 - curr_val_hc_sum / curr_sec_sum) if curr_sec_sum > 0 else None
    hc_change = (hc_curr - hc_prev) if (hc_prev is not None and hc_curr is not None) else None

    return {
        "matched_count": matched_count,
        "reappraised_count": reappraised_count,
        "cut_count": cut_count,
        "cut_amount": cut_amount,
        "gain_count": gain_count,
        "gain_amount": gain_amount,
        "unchanged_count": unchanged_count,
        "prev_valuation_sum": prev_val_sum,
        "curr_valuation_sum": curr_val_sum,
        "valuation_change": val_change,
        "valuation_change_pct": _ratio(val_change, prev_val_sum),
        "haircut_prev": hc_prev,
        "haircut_curr": hc_curr,
        "haircut_change": hc_change,
    }


def _appraisal_velocity(
    prev_by_id: dict[str, Loan],
    curr_by_id: dict[str, Loan],
    matched_ids: list[str],
) -> dict:
    pairs = [(prev_by_id[aid], curr_by_id[aid]) for aid in matched_ids]
    overall = _velocity_group(pairs)
    office = _velocity_group([(p, c) for p, c in pairs if c.is_office])

    movers: list[dict] = []
    newly_appraised: list[dict] = []
    for p, c in pairs:
        pv, cv = p.most_recent_valuation, c.most_recent_valuation
        if pv is None and cv is not None:
            newly_appraised.append(
                {
                    "asset_number": c.asset_number,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "securitization_value": c.valuation_securitization,
                    "curr_valuation": cv,
                    "curr_haircut": haircut(c),
                }
            )
        elif pv is not None and cv is not None and pv > 0 and abs(cv - pv) > 1e-6:
            delta = cv - pv
            phc, chc = haircut(p), haircut(c)
            movers.append(
                {
                    "asset_number": c.asset_number,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "balance": _bal(c),
                    "securitization_value": c.valuation_securitization,
                    "prev_valuation": pv,
                    "curr_valuation": cv,
                    "valuation_change": delta,
                    "valuation_change_pct": delta / pv,
                    "prev_haircut": phc,
                    "curr_haircut": chc,
                    "haircut_change": (chc - phc) if (phc is not None and chc is not None) else None,
                }
            )
    movers.sort(key=lambda r: (r["valuation_change"], r["asset_number"]))
    newly_appraised.sort(key=lambda r: r["asset_number"])

    by_metro_groups: dict[str, list[tuple[Loan, Loan]]] = defaultdict(list)
    by_type_groups: dict[str, list[tuple[Loan, Loan]]] = defaultdict(list)
    for p, c in pairs:
        by_metro_groups[c.metro].append((p, c))
        by_type_groups[c.property_type].append((p, c))

    by_metro = []
    for metro, grp in by_metro_groups.items():
        stats = _velocity_group(grp)
        stats["metro"] = metro
        by_metro.append(stats)
    by_metro.sort(key=lambda r: (r["valuation_change"], r["metro"]))

    by_property_type = []
    for ptype, grp in by_type_groups.items():
        stats = _velocity_group(grp)
        stats["property_type"] = ptype
        by_property_type.append(stats)
    by_property_type.sort(key=lambda r: (r["valuation_change"], r["property_type"]))

    return {
        **overall,
        "office": office,
        "movers": movers,
        "newly_appraised_count": len(newly_appraised),
        "newly_appraised_loans": newly_appraised,
        "by_metro": by_metro,
        "by_property_type": by_property_type,
    }


def _build_migration_matrix(
    prev_by_id: dict[str, Loan],
    curr_by_id: dict[str, Loan],
    matched_ids: list[str],
    removed_ids: list[str],
    classifier: Callable[[Loan], str],
) -> dict:
    """Build a count & balance transition matrix over loans active in Period 1."""
    transitions: list[tuple[str, str, Loan, Loan | None]] = []
    prior_inactive_count = 0

    for aid in matched_ids:
        p, c = prev_by_id[aid], curr_by_id[aid]
        if _bal(p) <= 0 and _bal(c) <= 0:
            prior_inactive_count += 1
            continue
        if _bal(p) <= 0:
            continue
        src = classifier(p)
        dst = "resolved" if _bal(c) <= 0 else classifier(c)
        transitions.append((src, dst, p, c))

    for aid in removed_ids:
        p = prev_by_id[aid]
        if _bal(p) <= 0:
            prior_inactive_count += 1
            continue
        transitions.append((classifier(p), "resolved", p, None))

    has_unreported_src = any(src == "unreported" for src, _, _, _ in transitions)
    has_unreported_dst = any(dst == "unreported" for _, dst, _, _ in transitions)

    from_tiers = list(CORE_TIERS) + (["unreported"] if has_unreported_src else [])
    to_tiers = list(CORE_TIERS) + (["unreported"] if has_unreported_dst else []) + ["resolved"]

    cells: dict[str, dict[str, dict]] = {
        ft: {
            tt: {
                "count": 0,
                "prior_balance": 0.0,
                "current_balance": 0.0,
                "row_share": None,
                "assets": [],
            }
            for tt in to_tiers
        }
        for ft in from_tiers
    }

    downgraded_loans: list[dict] = []
    upgraded_loans: list[dict] = []
    resolved_loans: list[dict] = []
    stable_count = 0
    stable_prior_bal = 0.0
    stable_curr_bal = 0.0

    for src, dst, p, c in transitions:
        pbal = _bal(p)
        cbal = _bal(c) if c is not None else 0.0
        cell = cells[src][dst]
        cell["count"] += 1
        cell["prior_balance"] += pbal
        cell["current_balance"] += cbal
        cell["assets"].append(p.asset_number)

        record = {
            "asset_number": p.asset_number,
            "property_name": (c.property_name if c else p.property_name) or "",
            "metro": c.metro if c else p.metro,
            "property_type": c.property_type if c else p.property_type,
            "from_tier": src,
            "to_tier": dst,
            "prior_balance": pbal,
            "current_balance": cbal,
            "prev_dscr": p.dscr,
            "curr_dscr": c.dscr if c else None,
            "prev_occupancy": p.occupancy,
            "curr_occupancy": c.occupancy if c else None,
        }

        if dst == "resolved":
            resolved_loans.append(record)
        elif src == dst:
            stable_count += 1
            stable_prior_bal += pbal
            stable_curr_bal += cbal
        elif src in TIER_RANK and dst in TIER_RANK:
            if TIER_RANK[dst] > TIER_RANK[src]:
                downgraded_loans.append(record)
            else:
                upgraded_loans.append(record)

    row_totals: dict[str, dict] = {}
    for ft in from_tiers:
        rc = sum(cells[ft][tt]["count"] for tt in to_tiers)
        rpb = sum(cells[ft][tt]["prior_balance"] for tt in to_tiers)
        rcb = sum(cells[ft][tt]["current_balance"] for tt in to_tiers)
        row_totals[ft] = {"count": rc, "prior_balance": rpb, "current_balance": rcb}
        for tt in to_tiers:
            cells[ft][tt]["row_share"] = _ratio(cells[ft][tt]["prior_balance"], rpb)

    col_totals: dict[str, dict] = {}
    for tt in to_tiers:
        col_totals[tt] = {
            "count": sum(cells[ft][tt]["count"] for ft in from_tiers),
            "prior_balance": sum(cells[ft][tt]["prior_balance"] for ft in from_tiers),
            "current_balance": sum(cells[ft][tt]["current_balance"] for ft in from_tiers),
        }

    downgraded_loans.sort(key=lambda r: (-r["prior_balance"], r["asset_number"]))
    upgraded_loans.sort(key=lambda r: (-r["prior_balance"], r["asset_number"]))
    resolved_loans.sort(key=lambda r: (-r["prior_balance"], r["asset_number"]))

    return {
        "from_tiers": from_tiers,
        "to_tiers": to_tiers,
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "total_count": sum(r["count"] for r in row_totals.values()),
        "total_prior_balance": sum(r["prior_balance"] for r in row_totals.values()),
        "total_current_balance": sum(r["current_balance"] for r in row_totals.values()),
        "prior_inactive_count": prior_inactive_count,
        "downgraded": {
            "count": len(downgraded_loans),
            "prior_balance": sum(r["prior_balance"] for r in downgraded_loans),
            "current_balance": sum(r["current_balance"] for r in downgraded_loans),
            "loans": downgraded_loans,
        },
        "upgraded": {
            "count": len(upgraded_loans),
            "prior_balance": sum(r["prior_balance"] for r in upgraded_loans),
            "current_balance": sum(r["current_balance"] for r in upgraded_loans),
            "loans": upgraded_loans,
        },
        "stable": {
            "count": stable_count,
            "prior_balance": stable_prior_bal,
            "current_balance": stable_curr_bal,
        },
        "resolved": {
            "count": len(resolved_loans),
            "prior_balance": sum(r["prior_balance"] for r in resolved_loans),
            "loans": resolved_loans,
        },
    }


def _threshold_crossings(
    prev_by_id: dict[str, Loan],
    curr_by_id: dict[str, Loan],
    matched_ids: list[str],
) -> dict:
    dscr_crossed: list[dict] = []
    occ_crossed: list[dict] = []
    for aid in matched_ids:
        p, c = prev_by_id[aid], curr_by_id[aid]
        if _bal(c) <= 0:
            continue
        if p.dscr is not None and c.dscr is not None and p.dscr >= DSCR_DISTRESSED and c.dscr < DSCR_DISTRESSED:
            dscr_crossed.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "balance": _bal(c),
                    "prev_dscr": p.dscr,
                    "curr_dscr": c.dscr,
                    "delta_dscr": c.dscr - p.dscr,
                }
            )
        if (
            p.occupancy is not None
            and c.occupancy is not None
            and p.occupancy >= OCC_DISTRESSED
            and c.occupancy < OCC_DISTRESSED
        ):
            occ_crossed.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "balance": _bal(c),
                    "prev_occupancy": p.occupancy,
                    "curr_occupancy": c.occupancy,
                    "delta_occupancy": c.occupancy - p.occupancy,
                }
            )
    dscr_crossed.sort(key=lambda r: (-r["balance"], r["asset_number"]))
    occ_crossed.sort(key=lambda r: (-r["balance"], r["asset_number"]))
    return {
        "dscr_crossed_below_1": dscr_crossed,
        "occ_crossed_below_75": occ_crossed,
    }


def _delinquency_transitions(
    prev_tape: ParseResult,
    curr_tape: ParseResult,
    prev_by_id: dict[str, Loan],
    curr_by_id: dict[str, Loan],
    matched_ids: list[str],
    removed_ids: list[str],
) -> dict:
    prev_cnt, prev_bal, prev_share = _active_delinquent_stats(prev_tape.loans)
    curr_cnt, curr_bal, curr_share = _active_delinquent_stats(curr_tape.loans)

    reported_pair_count = 0
    newly_delinquent: list[dict] = []
    cured: list[dict] = []
    worsened: list[dict] = []
    improved_delinquent: list[dict] = []
    resolved_delinquent: list[dict] = []

    for aid in matched_ids:
        p, c = prev_by_id[aid], curr_by_id[aid]
        if _bal(p) <= 0:
            continue
        if p.payment_status_code is None or c.payment_status_code is None:
            continue
        reported_pair_count += 1
        rec = {
            "asset_number": aid,
            "property_name": c.property_name or "",
            "metro": c.metro,
            "property_type": c.property_type,
            "prior_balance": _bal(p),
            "balance": _bal(c),
            "prev_status_code": p.payment_status_code,
            "prev_status": p.payment_status,
            "curr_status_code": c.payment_status_code,
            "curr_status": c.payment_status,
        }
        if _bal(c) == 0:
            if p.is_delinquent:
                resolved_delinquent.append(rec)
            continue
        if not p.is_delinquent and c.is_delinquent:
            newly_delinquent.append(rec)
        elif p.is_delinquent and not c.is_delinquent:
            cured.append(rec)
        elif p.is_delinquent and c.is_delinquent:
            p_sev = PAYMENT_STATUS_SEVERITY.get(p.payment_status_code, -1)
            c_sev = PAYMENT_STATUS_SEVERITY.get(c.payment_status_code, -1)
            if p_sev >= 0 and c_sev >= 0:
                if c_sev > p_sev:
                    worsened.append(rec)
                elif c_sev < p_sev:
                    improved_delinquent.append(rec)

    for aid in removed_ids:
        p = prev_by_id[aid]
        if _bal(p) > 0 and p.is_delinquent:
            resolved_delinquent.append(
                {
                    "asset_number": aid,
                    "property_name": p.property_name or "",
                    "metro": p.metro,
                    "property_type": p.property_type,
                    "prior_balance": _bal(p),
                    "balance": 0.0,
                    "prev_status_code": p.payment_status_code,
                    "prev_status": p.payment_status,
                    "curr_status_code": None,
                    "curr_status": "Removed / Resolved",
                }
            )

    for bucket in (newly_delinquent, cured, worsened, improved_delinquent, resolved_delinquent):
        bucket.sort(key=lambda r: (-r["prior_balance"], r["asset_number"]))

    return {
        "reported_pair_count": reported_pair_count,
        "prev_active_delinquent_count": prev_cnt,
        "prev_active_delinquent_balance": prev_bal,
        "prev_active_delinquent_share": prev_share,
        "curr_active_delinquent_count": curr_cnt,
        "curr_active_delinquent_balance": curr_bal,
        "curr_active_delinquent_share": curr_share,
        "net_active_delinquent_change": curr_bal - prev_bal,
        "newly_delinquent": {
            "count": len(newly_delinquent),
            "balance": sum(r["balance"] for r in newly_delinquent),
            "loans": newly_delinquent,
        },
        "cured": {
            "count": len(cured),
            "balance": sum(r["balance"] for r in cured),
            "loans": cured,
        },
        "worsened": {
            "count": len(worsened),
            "balance": sum(r["balance"] for r in worsened),
            "loans": worsened,
        },
        "improved_delinquent": {
            "count": len(improved_delinquent),
            "balance": sum(r["balance"] for r in improved_delinquent),
            "loans": improved_delinquent,
        },
        "resolved_delinquent": {
            "count": len(resolved_delinquent),
            "prior_balance": sum(r["prior_balance"] for r in resolved_delinquent),
            "loans": resolved_delinquent,
        },
    }


def _losses_and_liquidations(
    prev_tape: ParseResult,
    curr_tape: ParseResult,
    prev_by_id: dict[str, Loan],
    curr_by_id: dict[str, Loan],
    matched_ids: list[str],
    added_ids: list[str],
    removed_ids: list[str],
) -> dict:
    prev_cum_loss = sum(l.realized_loss or 0.0 for l in prev_tape.loans)
    curr_cum_loss = sum(l.realized_loss or 0.0 for l in curr_tape.loans)

    inc_loss_total = 0.0
    inc_loss_office = 0.0
    events: list[dict] = []
    modification_loss_events: list[dict] = []

    for aid in matched_ids:
        p, c = prev_by_id[aid], curr_by_id[aid]
        ploss = p.realized_loss or 0.0
        closs = c.realized_loss or 0.0
        inc_loss = closs - ploss
        inc_loss_total += inc_loss
        if c.is_office:
            inc_loss_office += inc_loss

        pbal, cbal = _bal(p), _bal(c)
        if pbal > 0 and cbal == 0:
            ev_type = "liquidation_with_loss" if inc_loss > 0 else "payoff_no_loss"
            events.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "is_office": c.is_office,
                    "event_type": ev_type,
                    "prior_balance": pbal,
                    "current_balance": 0.0,
                    "prev_loss": ploss,
                    "curr_loss": closs,
                    "incremental_loss": inc_loss,
                    "loss_severity": _ratio(inc_loss, pbal) if inc_loss > 0 else 0.0,
                    "cumulative_loss_severity": _ratio(closs, pbal) if closs > 0 else 0.0,
                }
            )
        elif cbal > 0 and abs(inc_loss) > 1e-6:
            modification_loss_events.append(
                {
                    "asset_number": aid,
                    "property_name": c.property_name or "",
                    "metro": c.metro,
                    "property_type": c.property_type,
                    "balance": cbal,
                    "prev_loss": ploss,
                    "curr_loss": closs,
                    "incremental_loss": inc_loss,
                }
            )

    for aid in added_ids:
        c = curr_by_id[aid]
        closs = c.realized_loss or 0.0
        if abs(closs) > 1e-6:
            inc_loss_total += closs
            if c.is_office:
                inc_loss_office += closs

    for aid in removed_ids:
        p = prev_by_id[aid]
        pbal = _bal(p)
        if pbal > 0:
            events.append(
                {
                    "asset_number": aid,
                    "property_name": p.property_name or "",
                    "metro": p.metro,
                    "property_type": p.property_type,
                    "is_office": p.is_office,
                    "event_type": "payoff_no_loss",
                    "prior_balance": pbal,
                    "current_balance": 0.0,
                    "prev_loss": p.realized_loss or 0.0,
                    "curr_loss": p.realized_loss or 0.0,
                    "incremental_loss": 0.0,
                    "loss_severity": 0.0,
                    "cumulative_loss_severity": _ratio(p.realized_loss or 0.0, pbal),
                }
            )

    events.sort(key=lambda r: (-r["incremental_loss"], -r["prior_balance"], r["asset_number"]))
    modification_loss_events.sort(key=lambda r: (-r["incremental_loss"], r["asset_number"]))

    liquidations = [e for e in events if e["event_type"] == "liquidation_with_loss"]
    payoffs = [e for e in events if e["event_type"] == "payoff_no_loss"]
    liq_bal = sum(e["prior_balance"] for e in liquidations)
    liq_loss = sum(e["incremental_loss"] for e in liquidations)

    return {
        "prev_cumulative_loss": prev_cum_loss,
        "curr_cumulative_loss": curr_cum_loss,
        "net_pool_loss_change": curr_cum_loss - prev_cum_loss,
        "incremental_loss": inc_loss_total,
        "incremental_loss_office": inc_loss_office,
        "incremental_loss_non_office": inc_loss_total - inc_loss_office,
        "liquidation_count": len(liquidations),
        "liquidated_balance": liq_bal,
        "liquidated_incremental_loss": liq_loss,
        "weighted_loss_severity": _ratio(liq_loss, liq_bal),
        "payoff_count": len(payoffs),
        "payoff_balance": sum(e["prior_balance"] for e in payoffs),
        "total_resolved_count": len(events),
        "total_resolved_balance": sum(e["prior_balance"] for e in events),
        "events": events,
        "modification_loss_events": modification_loss_events,
    }


def _cohort_key(loan: Loan, dimension: str) -> object:
    if dimension == "maturity_year":
        return loan.maturity_year if loan.maturity_year is not None else "Unknown"
    if dimension == "vintage_year":
        return loan.vintage_year if loan.vintage_year is not None else "Unknown"
    if dimension == "vintage_maturity":
        vy = loan.vintage_year if loan.vintage_year is not None else "Unknown"
        my = loan.maturity_year if loan.maturity_year is not None else "Unknown"
        return (vy, my)
    raise ValueError(f"Unknown cohort dimension: {dimension!r}")


def _cohort_sort_key(val: object) -> tuple:
    if isinstance(val, tuple):
        return tuple((0, v) if isinstance(v, int) else (1, str(v)) for v in val)
    return (0, val) if isinstance(val, int) else (1, str(val))


def cohort_exposure_table(
    prev_tape: ParseResult,
    curr_tape: ParseResult,
    dimension: str = "maturity_year",
) -> list[dict]:
    """Compute Vintage / Maturity-Year cohort exposure and MoM changes.

    Supported ``dimension`` values: ``"maturity_year"``, ``"vintage_year"``,
    ``"vintage_maturity"``.
    """
    prev_by_id = _index_by_asset(prev_tape)
    curr_by_id = _index_by_asset(curr_tape)
    total_curr_bal = sum(_bal(l) for l in curr_tape.loans)

    curr_groups: dict[object, list[Loan]] = defaultdict(list)
    pair_groups: dict[object, list[tuple[Loan, Loan]]] = defaultdict(list)
    prev_bal_by_cohort: dict[object, float] = defaultdict(float)
    inc_loss_by_cohort: dict[object, float] = defaultdict(float)
    ss_inflow_bal: dict[object, float] = defaultdict(float)
    ss_inflow_cnt: dict[object, int] = defaultdict(int)

    for aid, c in curr_by_id.items():
        k = _cohort_key(c, dimension)
        curr_groups[k].append(c)
        p = prev_by_id.get(aid)
        if p is not None:
            pair_groups[k].append((p, c))
            prev_bal_by_cohort[k] += _bal(p)
            inc_loss_by_cohort[k] += (c.realized_loss or 0.0) - (p.realized_loss or 0.0)
            if not p.in_special_servicing and c.in_special_servicing:
                ss_inflow_bal[k] += _bal(c)
                ss_inflow_cnt[k] += 1
        else:
            inc_loss_by_cohort[k] += c.realized_loss or 0.0

    for aid, p in prev_by_id.items():
        if aid not in curr_by_id:
            k = _cohort_key(p, dimension)
            curr_groups.setdefault(k, [])
            prev_bal_by_cohort[k] += _bal(p)

    rows: list[dict] = []
    for k in sorted(curr_groups, key=_cohort_sort_key):
        cloans = curr_groups[k]
        gs = group_stats(cloans)
        vel = _velocity_group(pair_groups.get(k, []))
        office_bal = sum(_bal(l) for l in cloans if l.is_office)
        del_bal = sum(_bal(l) for l in cloans if _bal(l) > 0 and l.is_delinquent)
        active_loans = sum(1 for l in cloans if _bal(l) > 0)
        pbal = prev_bal_by_cohort[k]
        cbal = gs["balance"]

        row: dict = {
            "loans": gs["loans"],
            "active_loans": active_loans,
            "prev_balance": pbal,
            "balance": cbal,
            "balance_change": cbal - pbal,
            "pool_share": _ratio(cbal, total_curr_bal),
            "office_balance": office_bal,
            "office_share": _ratio(office_bal, cbal),
            "distressed_balance": gs["distressed_balance"],
            "distressed_share": gs["distressed_share"],
            "special_servicing_balance": gs["special_servicing_balance"],
            "special_servicing_share": _ratio(gs["special_servicing_balance"], cbal),
            "ss_inflow_count": ss_inflow_cnt[k],
            "ss_inflow_balance": ss_inflow_bal[k],
            "delinquent_balance": del_bal,
            "delinquent_share": _ratio(del_bal, cbal),
            "haircut": gs["haircut"],
            "valuation_change": vel["valuation_change"],
            "valuation_change_pct": vel["valuation_change_pct"],
            "wtd_dscr": gs["wtd_dscr"],
            "wtd_occupancy": gs["wtd_occupancy"],
            "cumulative_loss": gs["realized_loss"],
            "incremental_loss": inc_loss_by_cohort[k],
        }
        if dimension == "vintage_maturity":
            vy, my = k  # type: ignore[misc]
            row["vintage_year"] = vy
            row["maturity_year"] = my
            row["cohort"] = f"{vy} / {my}"
            row["in_maturity_wall"] = isinstance(my, int) and WALL_START <= my <= WALL_END
        elif dimension == "maturity_year":
            row["cohort"] = k
            row["in_maturity_wall"] = isinstance(k, int) and WALL_START <= k <= WALL_END
        else:
            row["cohort"] = k
            row["in_maturity_wall"] = any(in_wall(l) for l in cloans)
        rows.append(row)
    return rows


def compare_pair(prev_tape: ParseResult, curr_tape: ParseResult) -> dict:
    """Compare two chronologically ordered loan tapes by ``asset_number``."""
    ordered = order_tapes([prev_tape, curr_tape])
    p_tape, c_tape = ordered[0], ordered[1]

    prev_by_id = _index_by_asset(p_tape)
    curr_by_id = _index_by_asset(c_tape)

    matched_ids = sorted(prev_by_id.keys() & curr_by_id.keys())
    added_ids = sorted(curr_by_id.keys() - prev_by_id.keys())
    removed_ids = sorted(prev_by_id.keys() - curr_by_id.keys())

    p_snap = period_snapshot(p_tape)
    c_snap = period_snapshot(c_tape)
    bal_change = c_snap["balance"] - p_snap["balance"]

    p_hc, c_hc = p_snap["pool_haircut"], c_snap["pool_haircut"]
    po_hc, co_hc = p_snap["office_haircut"], c_snap["office_haircut"]
    p_ds, c_ds = p_snap["distressed_share"], c_snap["distressed_share"]
    p_ss, c_ss = p_snap["special_servicing_share"], c_snap["special_servicing_share"]

    days_between = (
        (c_tape.as_of - p_tape.as_of).days
        if (p_tape.as_of is not None and c_tape.as_of is not None)
        else None
    )

    migration = {
        "combined": _build_migration_matrix(
            prev_by_id, curr_by_id, matched_ids, removed_ids, credit_tier
        ),
        "dscr": _build_migration_matrix(
            prev_by_id, curr_by_id, matched_ids, removed_ids, lambda l: dscr_tier(l.dscr)
        ),
        "occupancy": _build_migration_matrix(
            prev_by_id, curr_by_id, matched_ids, removed_ids, lambda l: occupancy_tier(l.occupancy)
        ),
        "threshold_crossings": _threshold_crossings(prev_by_id, curr_by_id, matched_ids),
    }

    return {
        "prev_as_of": p_snap["as_of"],
        "curr_as_of": c_snap["as_of"],
        "prev_source": p_tape.source,
        "curr_source": c_tape.source,
        "days_between": days_between,
        "prev_loan_count": p_snap["loans"],
        "curr_loan_count": c_snap["loans"],
        "matched_count": len(matched_ids),
        "added_count": len(added_ids),
        "removed_count": len(removed_ids),
        "added_assets": added_ids,
        "removed_assets": removed_ids,
        "prev_balance": p_snap["balance"],
        "curr_balance": c_snap["balance"],
        "balance_change": bal_change,
        "balance_change_pct": _ratio(bal_change, p_snap["balance"]),
        "pool_haircut_delta": (c_hc - p_hc) if (p_hc is not None and c_hc is not None) else None,
        "office_haircut_delta": (co_hc - po_hc) if (po_hc is not None and co_hc is not None) else None,
        "distressed_share_delta": (c_ds - p_ds) if (p_ds is not None and c_ds is not None) else None,
        "special_servicing_share_delta": (c_ss - p_ss) if (p_ss is not None and c_ss is not None) else None,
        "special_servicing": _special_servicing_flows(prev_by_id, curr_by_id, matched_ids, removed_ids),
        "appraisal_velocity": _appraisal_velocity(prev_by_id, curr_by_id, matched_ids),
        "migration": migration,
        "delinquency": _delinquency_transitions(
            p_tape, c_tape, prev_by_id, curr_by_id, matched_ids, removed_ids
        ),
        "losses_and_liquidations": _losses_and_liquidations(
            p_tape, c_tape, prev_by_id, curr_by_id, matched_ids, added_ids, removed_ids
        ),
        "cohorts": {
            "by_maturity_year": cohort_exposure_table(p_tape, c_tape, "maturity_year"),
            "by_vintage_year": cohort_exposure_table(p_tape, c_tape, "vintage_year"),
            "by_vintage_maturity": cohort_exposure_table(p_tape, c_tape, "vintage_maturity"),
        },
    }


def compare_tapes(tapes: Sequence[ParseResult]) -> dict:
    """Compare two or more dated loan tapes and return full longitudinal analytics."""
    ordered = order_tapes(tapes)
    periods = [period_snapshot(t) for t in ordered]
    transitions = [compare_pair(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)]
    latest = transitions[-1]
    cumulative = latest if len(ordered) == 2 else compare_pair(ordered[0], ordered[-1])

    return {
        "period_count": len(ordered),
        "start_as_of": periods[0]["as_of"],
        "end_as_of": periods[-1]["as_of"],
        "sources": [t.source for t in ordered],
        "periods": periods,
        "transitions": transitions,
        "latest_transition": latest,
        "cumulative": cumulative,
        "cohorts": latest["cohorts"],
    }
