"""Label-based parser for SEC Form ABS-EE CMBS asset-data XML (Exhibit 102).

Design
------
* **Label-based, namespace-agnostic.** Elements are matched on their *local name*
  (``assetNumber``, ``propertyCity`` ...), never on position or on a namespace URI,
  so a filer that nests property fields one level deeper, or uses a different
  namespace prefix, still parses.
* **One loan per ``<assets>`` element.** Each loan's subtree is walked in document
  order and the *first* occurrence of each label wins. A loan backed by several
  ``<property>`` blocks therefore reports the first property's city / state / type
  (the property count is kept so callers can see it happened).
* **Nothing is invented.** A missing or blank label yields ``None``; analytics treat
  ``None`` as "not reported", never as zero.

Evidence: the primary labels below are fixture-proven (tests/fixtures/). Whether
every label matches what live EX-102 filings use is **unverified** until a real
filing is downloaded and inspected; the alternate labels in ``FIELD_LABELS`` are
likewise unverified guesses and are tried only after the primary label.
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# canonical field -> labels tried in order (first is primary / fixture-proven)
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "asset_number": ("assetNumber",),
    "reporting_period_end": ("reportingPeriodEndDate",),
    "property_name": ("propertyName",),
    "property_type_code": ("propertyTypeCode",),
    "property_city": ("propertyCity",),
    "property_state": ("propertyState",),
    "valuation_securitization": ("valuationSecuritizationAmount",),
    "most_recent_valuation": ("mostRecentValuationAmount",),
    "dscr": ("mostRecentDebtServiceCoverageNetOperatingIncomePercentage",),
    "occupancy": ("mostRecentPhysicalOccupancyPercentage",),
    "special_servicer_transfer_date": (
        "specialServicerTransferDate",
        "mostRecentSpecialServicerTransferDate",  # unverified alternate
    ),
    "maturity_date": ("maturityDate",),
    "current_balance": (
        "currentEndingScheduledBalanceAmount",
        "reportPeriodEndScheduledLoanBalanceAmount",  # unverified alternate
    ),
    "realized_loss": ("realizedLossToTrustAmount",),
}

ASSET_ELEMENT = "assets"
PROPERTY_ELEMENT = "property"

# ABS-EE CMBS property type codes -> display label
PROPERTY_TYPES: dict[str, str] = {
    "OF": "Office",
    "RT": "Retail",
    "MF": "Multifamily",
    "IN": "Industrial",
    "LO": "Lodging",
    "MU": "Mixed Use",
    "WH": "Warehouse",
    "SS": "Self Storage",
    "HC": "Healthcare",
    "MH": "Manufactured Housing",
    "CH": "Cooperative Housing",
    "OT": "Other",
}

# (city upper, state upper) -> metro. Anything not listed falls into "Other".
METROS: dict[tuple[str, str], str] = {
    **{(c, "NY"): "New York" for c in ("NEW YORK", "MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND")},
    **{(c, "CA"): "San Francisco" for c in ("SAN FRANCISCO", "OAKLAND", "SOUTH SAN FRANCISCO", "BERKELEY")},
    **{(c, "IL"): "Chicago" for c in ("CHICAGO", "EVANSTON", "OAK BROOK", "SCHAUMBURG")},
    **{(c, "TX"): "Dallas" for c in ("DALLAS", "FORT WORTH", "PLANO", "IRVING", "ARLINGTON", "FRISCO")},
    **{(c, "MA"): "Boston" for c in ("BOSTON", "CAMBRIDGE", "SOMERVILLE", "WALTHAM")},
}
OTHER_METRO = "Other"

_DATE_FORMATS = ("%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y", "%Y%m%d")
_MONTH_FORMATS = ("%m/%Y", "%m-%Y", "%Y-%m")


@dataclass(frozen=True)
class Loan:
    asset_number: str
    property_name: str | None
    property_type_code: str | None
    property_type: str
    property_city: str | None
    property_state: str | None
    metro: str
    valuation_securitization: float | None
    most_recent_valuation: float | None
    dscr: float | None
    occupancy: float | None  # always a 0-1 fraction after normalisation
    special_servicer_transfer_date: dt.date | None
    maturity_date: dt.date | None
    current_balance: float | None
    realized_loss: float | None
    reporting_period_end: dt.date | None
    property_count: int = 1

    @property
    def in_special_servicing(self) -> bool:
        return self.special_servicer_transfer_date is not None

    @property
    def is_office(self) -> bool:
        return self.property_type_code == "OF"


@dataclass
class ParseResult:
    loans: list[Loan]
    source: str
    notes: list[str] = field(default_factory=list)

    @property
    def as_of(self) -> dt.date | None:
        dates = [l.reporting_period_end for l in self.loans if l.reporting_period_end]
        return max(dates) if dates else None


def local_name(tag: str) -> str:
    """``{namespace}assetNumber`` -> ``assetNumber``."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_number(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip().replace(",", "").replace("$", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(text: str | None) -> dt.date | None:
    """Accept ISO, MM-DD-YYYY, MM/DD/YYYY, YYYYMMDD; month-only forms resolve to day 1."""
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    for fmt in _MONTH_FORMATS:
        try:
            return dt.datetime.strptime(t, fmt).date().replace(day=1)
        except ValueError:
            pass
    return None


def normalise_occupancy(value: float | None) -> tuple[float | None, bool]:
    """Return (fraction, was_rescaled).

    Filers may report 0.92 or 92.0. Values above 1.5 are treated as percent-of-100
    and divided by 100. This threshold is a heuristic (fixture-proven only).
    """
    if value is None:
        return None, False
    if value > 1.5:
        return value / 100.0, True
    return value, False


def metro_for(city: str | None, state: str | None) -> str:
    if not city or not state:
        return OTHER_METRO
    return METROS.get((city.strip().upper(), state.strip().upper()), OTHER_METRO)


def _first_texts(asset: ET.Element) -> tuple[dict[str, str], int]:
    wanted = {label for labels in FIELD_LABELS.values() for label in labels}
    found: dict[str, str] = {}
    props = 0
    for el in asset.iter():
        name = local_name(el.tag)
        if name == PROPERTY_ELEMENT:
            props += 1
        if name in wanted and name not in found and el.text is not None and el.text.strip():
            found[name] = el.text.strip()
    return found, props


def _pick(found: dict[str, str], key: str) -> str | None:
    for label in FIELD_LABELS[key]:
        if label in found:
            return found[label]
    return None


def parse_asset(asset: ET.Element, index: int, notes: list[str]) -> Loan:
    found, props = _first_texts(asset)
    asset_number = _pick(found, "asset_number") or f"row-{index + 1}"
    code = _pick(found, "property_type_code")
    code = code.upper() if code else None
    if code and code not in PROPERTY_TYPES:
        notes.append(f"{asset_number}: unknown propertyTypeCode {code!r} -> Other")
    ptype = PROPERTY_TYPES.get(code, "Other") if code else "Unknown"
    occ, rescaled = normalise_occupancy(parse_number(_pick(found, "occupancy")))
    if rescaled:
        notes.append(f"{asset_number}: occupancy reported on a 0-100 scale, rescaled to a fraction")
    if props > 1:
        notes.append(f"{asset_number}: {props} property blocks; first property's fields used")
    city = _pick(found, "property_city")
    state = _pick(found, "property_state")
    return Loan(
        asset_number=asset_number,
        property_name=_pick(found, "property_name"),
        property_type_code=code,
        property_type=ptype,
        property_city=city,
        property_state=state.upper() if state else None,
        metro=metro_for(city, state),
        valuation_securitization=parse_number(_pick(found, "valuation_securitization")),
        most_recent_valuation=parse_number(_pick(found, "most_recent_valuation")),
        dscr=parse_number(_pick(found, "dscr")),
        occupancy=occ,
        special_servicer_transfer_date=parse_date(_pick(found, "special_servicer_transfer_date")),
        maturity_date=parse_date(_pick(found, "maturity_date")),
        current_balance=parse_number(_pick(found, "current_balance")),
        realized_loss=parse_number(_pick(found, "realized_loss")),
        reporting_period_end=parse_date(_pick(found, "reporting_period_end")),
        property_count=max(props, 1),
    )


def iter_assets(root: ET.Element) -> Iterable[ET.Element]:
    for el in root.iter():
        if local_name(el.tag) == ASSET_ELEMENT:
            yield el


def parse_xml_bytes(data: bytes, source: str = "<bytes>") -> ParseResult:
    # xml.etree does not fetch external entities; DTDs are rejected outright to be safe.
    if b"<!DOCTYPE" in data[:2048].upper() or b"<!ENTITY" in data.upper():
        raise ValueError(f"{source}: DOCTYPE/ENTITY declarations are not accepted")
    root = ET.fromstring(data)
    notes: list[str] = []
    loans = [parse_asset(a, i, notes) for i, a in enumerate(iter_assets(root))]
    if not loans:
        notes.append(f"{source}: no <{ASSET_ELEMENT}> elements found")
    return ParseResult(loans=loans, source=source, notes=notes)


def parse_file(path: str | Path) -> ParseResult:
    p = Path(path)
    return parse_xml_bytes(p.read_bytes(), source=p.name)


def parse_files(paths: Iterable[str | Path]) -> ParseResult:
    loans: list[Loan] = []
    notes: list[str] = []
    names: list[str] = []
    for path in paths:
        r = parse_file(path)
        loans.extend(r.loans)
        notes.extend(r.notes)
        names.append(r.source)
    return ParseResult(loans=loans, source=", ".join(names), notes=notes)
