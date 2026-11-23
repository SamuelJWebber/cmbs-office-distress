"""Render a self-contained static HTML dashboard (docs/index.html).

No external assets, fonts, scripts or network calls: CSS, inline SVG charts and a
small vanilla-JS filter/sort table are all embedded. Output is deterministic for a
given input (no generation timestamp) so CI can check docs/ is up to date.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from . import __version__
from .analytics import BUCKETS, haircut, in_wall, loan_flags, summarize
from .longitudinal import TIER_LABELS, TIER_RANK
from .parser import ParseResult

BADGE = (
    "Fixture-proven v0.2 longitudinal prototype \u2014 configure CMBS_CONTACT in Actions for live SEC ABS-EE ingestion"
)

OFFICE_COLOR = "#1f3a5f"
OTHER_COLOR = "#9fb3c8"
ALERT_COLOR = "#b5452b"
POS_COLOR = "#2f7d5b"
AMBER_COLOR = "#c77d1a"
PLUM_COLOR = "#6d3f8c"

_METRO_SHORT: dict[str, str] = {
    "New York": "NYC",
    "San Francisco": "SF",
    "Chicago": "CHI",
    "Dallas": "DAL",
    "Boston": "BOS",
    "Other": "OTH",
}

_TYPE_SHORT: dict[str, str] = {
    "Office": "OF",
    "Retail": "RT",
    "Multifamily": "MF",
    "Industrial": "IN",
    "Lodging": "LO",
    "Mixed Use": "MU",
    "Warehouse": "WH",
    "Self Storage": "SS",
    "Healthcare": "HC",
    "Other": "OT",
}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_money(v: float | None) -> str:
    if v is None:
        return "\u2014"
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1e9:
        return f"{sign}${av / 1e9:,.2f}B"
    if av >= 1e6:
        return f"{sign}${av / 1e6:,.1f}M"
    return f"{sign}${av:,.0f}"


def fmt_signed_money(v: float | None) -> str:
    if v is None:
        return "\u2014"
    if abs(v) < 0.5:
        return "$0"
    prefix = "+" if v > 0 else ""
    return f"{prefix}{fmt_money(v)}"


def fmt_pct(v: float | None, digits: int = 1) -> str:
    return "\u2014" if v is None else f"{v * 100:.{digits}f}%"


def fmt_signed_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "\u2014"
    if abs(v) < 1e-9:
        return f"{0.0:.{digits}f}%"
    prefix = "+" if v > 0 else ""
    return f"{prefix}{v * 100:.{digits}f}%"


def fmt_pp(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "\u2014"
    if abs(v) < 1e-9:
        return f"{0.0:.{digits}f}pp"
    prefix = "+" if v > 0 else ""
    return f"{prefix}{v * 100:.{digits}f}pp"


def fmt_x(v: float | None) -> str:
    return "\u2014" if v is None else f"{v:.2f}x"


def _json_for_script(obj: object) -> str:
    s = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    return s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


# ---------------------------------------------------------------- SVG charts


def svg_maturity_wall(wall: dict) -> str:
    years = list(wall["by_year"])
    w, h, pad_l, pad_b, pad_t = 420, 230, 56, 34, 18
    maxv = max(wall["by_year"].values()) or 1.0
    plot_h = h - pad_b - pad_t
    bw = (w - pad_l - 20) / len(years) * 0.56
    step = (w - pad_l - 20) / len(years)
    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Maturity wall by year, office versus other">'
    ]
    for frac in (0, 0.5, 1.0):
        y = pad_t + plot_h * (1 - frac)
        parts.append(f'<line x1="{pad_l}" x2="{w - 10}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" class="axis">{esc(fmt_money(maxv * frac))}</text>'
        )
    for i, yr in enumerate(years):
        total = wall["by_year"][yr]
        office = wall["by_year_office"][yr]
        x = pad_l + step * i + (step - bw) / 2
        h_total = plot_h * total / maxv
        h_office = plot_h * office / maxv
        y_total = pad_t + plot_h - h_total
        parts.append(
            f'<rect x="{x:.1f}" y="{y_total:.1f}" width="{bw:.1f}" height="{max(0.0, h_total - h_office):.1f}" fill="{OTHER_COLOR}">'
            f"<title>{yr} other: {esc(fmt_money(total - office))}</title></rect>"
        )
        parts.append(
            f'<rect x="{x:.1f}" y="{pad_t + plot_h - h_office:.1f}" width="{bw:.1f}" height="{max(0.0, h_office):.1f}" fill="{OFFICE_COLOR}">'
            f"<title>{yr} office: {esc(fmt_money(office))}</title></rect>"
        )
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{y_total - 5:.1f}" text-anchor="middle" class="val">{esc(fmt_money(total))}</text>'
        )
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{h - 12}" text-anchor="middle" class="axis">{yr}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_distress(distress: dict, office: dict) -> str:
    keys = list(BUCKETS)
    w, row_h, pad_l, pad_t = 420, 46, 130, 8
    h = pad_t + row_h * len(keys) + 8
    plot_w = w - pad_l - 60
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Distress buckets, share of balance">']
    for i, k in enumerate(keys):
        y = pad_t + row_h * i
        parts.append(f'<text x="{pad_l - 8}" y="{y + 20}" text-anchor="end" class="axis">{esc(BUCKETS[k])}</text>')
        for j, (src, color, tag) in enumerate(((distress, OTHER_COLOR, "All loans"), (office, OFFICE_COLOR, "Office"))):
            share = src[k]["share"] or 0.0
            bar_y = y + 6 + j * 16
            parts.append(
                f'<rect x="{pad_l}" y="{bar_y}" width="{plot_w * share:.1f}" height="13" fill="{color}">'
                f"<title>{tag} \u00b7 {esc(BUCKETS[k])}: {esc(fmt_pct(share))} of balance "
                f"({src[k]['count']} loans, {esc(fmt_money(src[k]['balance']))})</title></rect>"
            )
            parts.append(
                f'<text x="{pad_l + plot_w * share + 5:.1f}" y="{bar_y + 11}" class="val">{esc(fmt_pct(share))}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def svg_haircut_by_type(by_type: list[dict]) -> str:
    rows = [r for r in by_type if r["haircut"] is not None]
    rows.sort(key=lambda r: -r["haircut"])
    w, row_h, pad_l, pad_t = 420, 30, 96, 8
    h = pad_t + row_h * max(len(rows), 1) + 8
    lim = max([abs(r["haircut"]) for r in rows] + [0.1])
    bar_left = pad_l + 48
    bar_right = w - 48
    zero_x = (bar_left + bar_right) / 2
    half = (bar_right - bar_left) / 2
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Appraisal haircut by property type">']
    parts.append(f'<line x1="{zero_x:.1f}" x2="{zero_x:.1f}" y1="{pad_t}" y2="{h - 6}" class="zero"/>')
    for i, r in enumerate(rows):
        y = pad_t + row_h * i
        v = r["haircut"]
        bw = half * abs(v) / lim
        x = zero_x if v >= 0 else zero_x - bw
        color = ALERT_COLOR if v >= 0 else POS_COLOR
        parts.append(f'<text x="{pad_l - 8}" y="{y + 18}" text-anchor="end" class="axis">{esc(r["property_type"])}</text>')
        parts.append(
            f'<rect x="{x:.1f}" y="{y + 6}" width="{bw:.1f}" height="16" fill="{color}">'
            f"<title>{esc(r['property_type'])}: {esc(fmt_pct(v))} value change vs securitisation</title></rect>"
        )
        tx = x + bw + 5 if v >= 0 else x - 5
        anchor = "start" if v >= 0 else "end"
        parts.append(f'<text x="{tx:.1f}" y="{y + 19}" text-anchor="{anchor}" class="val">{esc(fmt_pct(v))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_appraisal_waterfall(velocity: dict) -> str:
    """Inline SVG waterfall bridging month-over-month matched valuation changes."""
    w, h = 540, 250
    pad_l, pad_r, pad_t, pad_b = 62, 18, 26, 46
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    movers = list(velocity.get("movers", []))

    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Month-over-month appraisal valuation waterfall">'
    ]
    if not movers:
        parts.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{pad_t + plot_h / 2:.1f}" y2="{pad_t + plot_h / 2:.1f}" class="zero"/>')
        parts.append(
            f'<text x="{(pad_l + w - pad_r) / 2:.1f}" y="{pad_t + plot_h / 2 - 8:.1f}" '
            f'text-anchor="middle" class="axis">No month-over-month valuation changes across matched appraisals</text>'
        )
        parts.append("</svg>")
        return "".join(parts)

    # Cap individual bars at 7 and aggregate overflow into "Other"
    bars: list[dict] = []
    if len(movers) > 7:
        shown = movers[:6]
        rest = movers[6:]
        for m in shown:
            bars.append(m)
        bars.append(
            {
                "asset_number": f"+{len(rest)} more",
                "property_name": f"{len(rest)} additional reappraised loans",
                "metro": "Other",
                "property_type": "Other",
                "prev_valuation": sum(m["prev_valuation"] for m in rest),
                "curr_valuation": sum(m["curr_valuation"] for m in rest),
                "valuation_change": sum(m["valuation_change"] for m in rest),
                "valuation_change_pct": None,
                "is_total": False,
            }
        )
    else:
        bars.extend(movers)

    # Compute cumulative bridge coordinates
    running = 0.0
    steps: list[tuple[float, float, dict]] = []
    extrema = [0.0]
    for b in bars:
        start = running
        end = running + b["valuation_change"]
        steps.append((start, end, {**b, "is_total": False}))
        extrema.extend([start, end])
        running = end

    net = velocity["valuation_change"]
    steps.append(
        (
            0.0,
            net,
            {
                "asset_number": "Net MoM",
                "property_name": "Total matched valuation change",
                "metro": "",
                "property_type": "",
                "prev_valuation": velocity["prev_valuation_sum"],
                "curr_valuation": velocity["curr_valuation_sum"],
                "valuation_change": net,
                "valuation_change_pct": velocity["valuation_change_pct"],
                "is_total": True,
            },
        )
    )
    extrema.append(net)

    ymin = min(extrema)
    ymax = max(extrema)
    span = (ymax - ymin) or 1.0
    # Add vertical headroom for value labels
    ymin -= span * 0.16
    ymax += span * 0.14
    total_span = ymax - ymin

    def y_of(val: float) -> float:
        return pad_t + plot_h * (ymax - val) / total_span

    # Gridlines (ymin-ish, mid, 0, ymax-ish)
    ticks = sorted({0.0, round(min(extrema), 2), round(min(extrema) / 2, 2)})
    if max(extrema) > 0:
        ticks.append(round(max(extrema), 2))
    ticks = sorted(set(ticks))
    for tv in ticks:
        yt = y_of(tv)
        cls = "zero" if abs(tv) < 1e-6 else "grid"
        parts.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{yt:.1f}" y2="{yt:.1f}" class="{cls}"/>')
        parts.append(
            f'<text x="{pad_l - 6}" y="{yt + 3.5:.1f}" text-anchor="end" class="axis">{esc(fmt_signed_money(tv))}</text>'
        )

    n = len(steps)
    slot = plot_w / n
    bw = min(56.0, slot * 0.58)

    for i, (s_val, e_val, item) in enumerate(steps):
        cx = pad_l + slot * i + slot / 2
        x = cx - bw / 2
        y_top = y_of(max(s_val, e_val))
        y_bot = y_of(min(s_val, e_val))
        bar_h = max(2.0, y_bot - y_top)
        delta = item["valuation_change"]
        if item["is_total"]:
            fill = OFFICE_COLOR
        else:
            fill = ALERT_COLOR if delta < 0 else POS_COLOR

        pct_txt = f" / {fmt_signed_pct(item['valuation_change_pct'])}" if item.get("valuation_change_pct") is not None else ""
        title_txt = (
            f"{item['asset_number']} \u00b7 {item['property_name']}: "
            f"{fmt_money(item['prev_valuation'])} \u2192 {fmt_money(item['curr_valuation'])} "
            f"({fmt_signed_money(delta)}{pct_txt})"
        )
        parts.append(
            f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bw:.1f}" height="{bar_h:.1f}" rx="2" fill="{fill}">'
            f"<title>{esc(title_txt)}</title></rect>"
        )

        # Connector to next bar (except after final total bar)
        if i < n - 1:
            next_x = pad_l + slot * (i + 1) + slot / 2 - bw / 2
            ye = y_of(e_val)
            parts.append(
                f'<line x1="{x + bw:.1f}" x2="{next_x:.1f}" y1="{ye:.1f}" y2="{ye:.1f}" class="conn"/>'
            )

        # Value label
        val_y = y_top - 5 if delta >= 0 else y_bot + 12
        parts.append(
            f'<text x="{cx:.1f}" y="{val_y:.1f}" text-anchor="middle" class="val">{esc(fmt_signed_money(delta))}</text>'
        )

        # X-axis label (2 lines: asset + metro/type)
        parts.append(
            f'<text x="{cx:.1f}" y="{h - 22}" text-anchor="middle" class="axis">{esc(item["asset_number"])}</text>'
        )
        if item["is_total"]:
            sub = fmt_signed_pct(item["valuation_change_pct"])
        else:
            m_short = _METRO_SHORT.get(item.get("metro", ""), item.get("metro", "")[:3].upper())
            t_short = _TYPE_SHORT.get(item.get("property_type", ""), "")
            sub = f"{m_short} {t_short}".strip()
        if sub:
            parts.append(
                f'<text x="{cx:.1f}" y="{h - 9}" text-anchor="middle" class="subaxis">{esc(sub)}</text>'
            )

    parts.append("</svg>")
    return "".join(parts)


def svg_distress_trajectory(periods: list[dict]) -> str:
    """Inline SVG trajectory / slope chart across all dated reporting periods."""
    w, h = 540, 250
    pad_l, pad_r, pad_t, pad_b = 48, 56, 22, 36
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    series = [
        ("distressed_share", "Any distress", ALERT_COLOR),
        ("delinquent_share", "30+d delinquent", AMBER_COLOR),
        ("special_servicing_share", "Special servicing", OFFICE_COLOR),
        ("office_haircut", "Office haircut", PLUM_COLOR),
    ]

    vals = [
        p[key]
        for p in periods
        for key, _, _ in series
        if p.get(key) is not None
    ]
    raw_max = max(vals) if vals else 0.5
    ymax = min(1.0, max(0.25, ((int(raw_max * 100) // 20) + 1) * 0.20))
    ymin = min(0.0, min(vals) if vals else 0.0)
    span = (ymax - ymin) or 1.0

    def y_of(v: float) -> float:
        return pad_t + plot_h * (ymax - v) / span

    n = len(periods)
    if n == 1:
        xs = [pad_l + plot_w / 2]
    elif n == 2:
        # Inset endpoints slightly for clean slope-chart label margins
        xs = [pad_l + plot_w * 0.14, pad_l + plot_w * 0.86]
    else:
        xs = [pad_l + 14 + (plot_w - 28) * i / (n - 1) for i in range(n)]

    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Multi-period CMBS distress and office haircut trajectory">'
    ]

    # Horizontal gridlines
    steps_grid = 4
    for i in range(steps_grid + 1):
        gv = ymin + span * i / steps_grid
        yg = y_of(gv)
        cls = "zero" if abs(gv) < 1e-9 else "grid"
        parts.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{yg:.1f}" y2="{yg:.1f}" class="{cls}"/>')
        parts.append(
            f'<text x="{pad_l - 6}" y="{yg + 3.5:.1f}" text-anchor="end" class="axis">{esc(fmt_pct(gv, 0))}</text>'
        )

    # Vertical period guides + date labels
    for i, p in enumerate(periods):
        xp = xs[i]
        parts.append(f'<line x1="{xp:.1f}" x2="{xp:.1f}" y1="{pad_t}" y2="{pad_t + plot_h:.1f}" class="grid"/>')
        parts.append(
            f'<text x="{xp:.1f}" y="{h - 12}" text-anchor="middle" class="axis">{esc(p.get("as_of") or "?")}</text>'
        )

    # Collect right-edge labels so we can de-collide vertically if close
    right_labels: list[tuple[float, str, str]] = []
    left_labels: list[tuple[float, str, str]] = []

    for key, label, color in series:
        pts: list[tuple[float, float, float, str]] = []
        for i, p in enumerate(periods):
            v = p.get(key)
            if v is None:
                continue
            pts.append((xs[i], y_of(v), v, p.get("as_of") or ""))
        if not pts:
            continue
        if len(pts) >= 2:
            d_attr = " ".join(f"{'M' if idx == 0 else 'L'}{px:.1f},{py:.1f}" for idx, (px, py, _, _) in enumerate(pts))
            parts.append(f'<path d="{d_attr}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>')
        for px, py, v, dt_str in pts:
            parts.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{color}" stroke="#fff" stroke-width="1.5">'
                f"<title>{esc(label)} ({esc(dt_str)}): {esc(fmt_pct(v))}</title></circle>"
            )
        right_labels.append((pts[-1][1], fmt_pct(pts[-1][2]), color))
        if n == 2 and len(pts) == 2:
            left_labels.append((pts[0][1], fmt_pct(pts[0][2]), color))

    def _spread(items: list[tuple[float, str, str]], min_gap: float = 13.0) -> list[tuple[float, str, str]]:
        ordered = sorted(items, key=lambda t: t[0])
        placed: list[tuple[float, str, str]] = []
        for y, txt, col in ordered:
            if placed and y - placed[-1][0] < min_gap:
                y = placed[-1][0] + min_gap
            y = min(y, pad_t + plot_h + 2)
            placed.append((y, txt, col))
        return placed

    for y, txt, col in _spread(left_labels):
        parts.append(
            f'<text x="{xs[0] - 8:.1f}" y="{y + 3.5:.1f}" text-anchor="end" class="val" style="fill:{col}">{esc(txt)}</text>'
        )
    for y, txt, col in _spread(right_labels):
        parts.append(
            f'<text x="{xs[-1] + 8:.1f}" y="{y + 3.5:.1f}" text-anchor="start" class="val" style="fill:{col}">{esc(txt)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def heat_color(share: float | None) -> str:
    if share is None:
        return "#f3f5f8"
    s = max(0.0, min(1.0, share))
    # interpolate pale sand -> rust
    r = int(250 + (181 - 250) * s)
    g = int(240 + (69 - 240) * s)
    b = int(228 + (43 - 228) * s)
    return f"rgb({r},{g},{b})"


def heatmap_table(summary: dict) -> str:
    cells = {(r["metro"], r["property_type"]): r for r in summary["by_metro_type"]}
    metros = [r["metro"] for r in summary["by_metro"]]
    if "Other" in metros:
        metros = [m for m in metros if m != "Other"] + ["Other"]
    types = [r["property_type"] for r in summary["by_type"]]
    if "Office" in types:
        types = ["Office"] + [t for t in types if t != "Office"]
    out = ['<table class="heat"><thead><tr><th>Metro</th>']
    out += [f"<th>{esc(t)}</th>" for t in types]
    out.append("<th>Metro total</th></tr></thead><tbody>")
    metro_rows = {r["metro"]: r for r in summary["by_metro"]}
    for m in metros:
        out.append(f"<tr><th>{esc(m)}</th>")
        for t in types:
            c = cells.get((m, t))
            if c is None:
                out.append('<td class="empty">\u00b7</td>')
                continue
            share = c["distressed_share"]
            dark = share is not None and share > 0.55
            out.append(
                f'<td style="background:{heat_color(share)}" class="{"dark" if dark else ""}" '
                f'title="{esc(m)} {esc(t)}: {c["loans"]} {"loan" if c["loans"] == 1 else "loans"}, {esc(fmt_money(c["balance"]))}; '
                f'distressed {esc(fmt_pct(share))}; wall {esc(fmt_pct(c["wall_share"]))}; '
                f'haircut {esc(fmt_pct(c["haircut"]))}">'
                f'<b>{esc(fmt_pct(share, 0))}</b><span>{esc(fmt_money(c["balance"]))} \u00b7 {c["loans"]}</span></td>'
            )
        mr = metro_rows[m]
        out.append(
            f'<td class="tot"><b>{esc(fmt_pct(mr["distressed_share"], 0))}</b><span>{esc(fmt_money(mr["balance"]))}</span></td></tr>'
        )
    out.append("</tbody></table>")
    return "".join(out)


# ---------------------------------------------------------------- Longitudinal HTML blocks


def migration_matrix_table(matrix: dict) -> str:
    """Render DSCR & occupancy credit-tier migration matrix."""
    from_tiers = matrix["from_tiers"]
    to_tiers = matrix["to_tiers"]
    cells = matrix["cells"]
    row_totals = matrix["row_totals"]
    col_totals = matrix["col_totals"]

    out = ['<table class="heat mig"><thead><tr><th>Prior \\ Current</th>']
    for tt in to_tiers:
        out.append(f"<th>{esc(TIER_LABELS.get(tt, tt))}</th>")
    out.append("<th>Prior total</th></tr></thead><tbody>")

    for ft in from_tiers:
        out.append(f"<tr><th>{esc(TIER_LABELS.get(ft, ft))}</th>")
        for tt in to_tiers:
            cell = cells[ft][tt]
            if cell["count"] == 0:
                out.append('<td class="empty">\u00b7</td>')
                continue
            share = cell["row_share"]
            style = ""
            cls = ""
            if tt == "resolved":
                cls = "mig-res"
            elif ft == tt:
                cls = "mig-stay"
            elif ft in TIER_RANK and tt in TIER_RANK:
                if TIER_RANK[tt] > TIER_RANK[ft]:
                    style = f' style="background:{heat_color(share)}"'
                    cls = "dark" if (share is not None and share > 0.55) else "mig-down"
                else:
                    cls = "mig-up"
            assets_str = ", ".join(cell["assets"])
            loan_word = "loan" if cell["count"] == 1 else "loans"
            out.append(
                f'<td{style} class="{cls}" '
                f'title="{esc(TIER_LABELS.get(ft, ft))} \u2192 {esc(TIER_LABELS.get(tt, tt))}: '
                f'{cell["count"]} {loan_word} ({esc(assets_str)}), prior {esc(fmt_money(cell["prior_balance"]))}, '
                f'{esc(fmt_pct(share))} of row">'
                f'<b>{cell["count"]} \u00b7 {esc(fmt_money(cell["prior_balance"]))}</b>'
                f'<span>{esc(fmt_pct(share, 0))} of row</span></td>'
            )
        rt = row_totals[ft]
        out.append(
            f'<td class="tot"><b>{rt["count"]} \u00b7 {esc(fmt_money(rt["prior_balance"]))}</b>'
            f'<span>100%</span></td></tr>'
        )

    out.append('</tbody><tfoot><tr><th>Current total</th>')
    for tt in to_tiers:
        ct = col_totals[tt]
        bal_disp = ct["prior_balance"] if tt == "resolved" else ct["current_balance"]
        lbl = "prior bal" if tt == "resolved" else "curr bal"
        out.append(
            f'<td class="tot"><b>{ct["count"]} \u00b7 {esc(fmt_money(bal_disp))}</b><span>{lbl}</span></td>'
        )
    out.append(
        f'<td class="tot"><b>{matrix["total_count"]} \u00b7 {esc(fmt_money(matrix["total_prior_balance"]))}</b>'
        f'<span>curr {esc(fmt_money(matrix["total_current_balance"]))}</span></td></tr></tfoot></table>'
    )
    return "".join(out)


def transition_events_table(transition: dict) -> str:
    """Consolidated loan-level table of key monthly migration, valuation, SS, and loss events."""
    by_asset: dict[str, dict] = {}

    def _slot(item: dict) -> dict:
        aid = item["asset_number"]
        if aid not in by_asset:
            by_asset[aid] = {
                "asset": aid,
                "name": item.get("property_name", ""),
                "metro": item.get("metro", ""),
                "type": item.get("property_type", ""),
                "balance": item.get("balance", item.get("current_balance", 0.0)),
                "prior_balance": item.get("prior_balance"),
                "tags": [],
                "details": [],
                "severity": 0,
            }
        return by_asset[aid]

    for ev in transition["losses_and_liquidations"]["events"]:
        rec = _slot(ev)
        if ev["event_type"] == "liquidation_with_loss":
            rec["tags"].append(("bad", "Liquidation w/ Loss"))
            rec["details"].append(
                f"Bal {fmt_money(ev['prior_balance'])} \u2192 $0; inc. loss {fmt_money(ev['incremental_loss'])} ({fmt_pct(ev['loss_severity'])} sev)"
            )
            rec["severity"] += 100
        else:
            rec["tags"].append(("good", "Full Payoff"))
            rec["details"].append(f"Paid off {fmt_money(ev['prior_balance'])} with $0 incremental loss")
            rec["severity"] += 40

    for item in transition["special_servicing"]["inflow_loans"]:
        rec = _slot(item)
        rec["tags"].append(("bad", "New SS Transfer"))
        rec["details"].append(f"Transferred to special servicing ({item.get('transfer_date') or 'new'})")
        rec["severity"] += 80

    for item in transition["delinquency"]["newly_delinquent"]["loans"]:
        rec = _slot(item)
        rec["tags"].append(("bad", "New Delinquency"))
        rec["details"].append(f"Pay status: {item['prev_status']} \u2192 {item['curr_status']}")
        rec["severity"] += 70

    for item in transition["delinquency"]["worsened"]["loans"]:
        rec = _slot(item)
        rec["tags"].append(("warn", "Delinquency Roll"))
        rec["details"].append(f"Pay status: {item['prev_status']} \u2192 {item['curr_status']}")
        rec["severity"] += 35

    for item in transition["migration"]["threshold_crossings"]["dscr_crossed_below_1"]:
        rec = _slot(item)
        rec["tags"].append(("bad", "DSCR < 1.00x"))
        rec["details"].append(f"DSCR {fmt_x(item['prev_dscr'])} \u2192 {fmt_x(item['curr_dscr'])}")
        rec["severity"] += 60

    for item in transition["migration"]["combined"]["downgraded"]["loans"]:
        rec = _slot(item)
        rec["tags"].append(("warn", f"{TIER_LABELS[item['from_tier']]} \u2192 {TIER_LABELS[item['to_tier']]}"))
        if item["from_tier"] == "healthy":
            rec["details"].append(
                f"DSCR {fmt_x(item['prev_dscr'])} \u2192 {fmt_x(item['curr_dscr'])}, Occ {fmt_pct(item['prev_occupancy'])} \u2192 {fmt_pct(item['curr_occupancy'])}"
            )
        rec["severity"] += 45

    for item in transition["appraisal_velocity"]["movers"]:
        rec = _slot(item)
        delta = item["valuation_change"]
        if delta < 0:
            rec["tags"].append(("bad", "Appraisal Cut"))
            rec["severity"] += 50
        else:
            rec["tags"].append(("good", "Appraisal Mark-Up"))
            rec["severity"] += 10
        rec["details"].append(
            f"Val {fmt_money(item['prev_valuation'])} \u2192 {fmt_money(item['curr_valuation'])} "
            f"({fmt_signed_money(delta)} / {fmt_signed_pct(item['valuation_change_pct'])})"
        )

    for item in transition["appraisal_velocity"]["newly_appraised_loans"]:
        rec = _slot(item)
        rec["tags"].append(("info", "First Appraisal"))
        rec["details"].append(
            f"Initial post-sec appraisal {fmt_money(item['curr_valuation'])} ({fmt_pct(item['curr_haircut'])} haircut)"
        )
        rec["severity"] += 20

    ordered = sorted(by_asset.values(), key=lambda r: (-r["severity"], -(r["balance"] or 0.0), r["asset"]))
    if not ordered:
        return '<p class="notes">No loan-level migration or valuation transitions recorded between these periods.</p>'

    out = [
        '<div class="tablewrap"><table class="data"><thead><tr>'
        "<th>Asset</th><th>Property</th><th>Metro</th><th>Type</th>"
        '<th class="n">Curr Bal</th><th>Transition Events</th><th>Month-over-Month Detail</th>'
        "</tr></thead><tbody>"
    ]
    for r in ordered:
        tags_html = "".join(
            f'<span class="tag tag-{esc(kind)}">{esc(lbl)}</span>' for kind, lbl in r["tags"]
        )
        detail_txt = " \u00b7 ".join(r["details"])
        out.append(
            f"<tr><td><code>{esc(r['asset'])}</code></td><td>{esc(r['name'])}</td>"
            f"<td>{esc(r['metro'])}</td><td>{esc(r['type'])}</td>"
            f'<td class="n">{esc(fmt_money(r["balance"]))}</td>'
            f"<td>{tags_html}</td><td class=\"detail\">{esc(detail_txt)}</td></tr>"
        )
    out.append("</tbody></table></div>")
    return "".join(out)


def cohort_exposure_table_html(rows: list[dict], heading_col: str) -> str:
    """Render a Vintage or Maturity-Year cohort exposure table with reconciling totals."""
    if not rows:
        return '<p class="notes">No cohort rows available.</p>'

    tot_loans = sum(r["loans"] for r in rows)
    tot_bal = sum(r["balance"] for r in rows)
    tot_prev = sum(r["prev_balance"] for r in rows)
    tot_off = sum(r["office_balance"] for r in rows)
    tot_dist = sum(r["distressed_balance"] for r in rows)
    tot_ss = sum(r["special_servicing_balance"] for r in rows)
    tot_ss_in = sum(r["ss_inflow_balance"] for r in rows)
    tot_val_chg = sum(r["valuation_change"] for r in rows)
    tot_inc_loss = sum(r["incremental_loss"] for r in rows)

    out = [
        '<div class="tablewrap"><table class="data"><thead><tr>'
        f"<th>{esc(heading_col)}</th>"
        '<th class="n">Loans</th><th class="n">Curr Bal</th><th class="n">Pool Share</th>'
        '<th class="n">Office %</th><th class="n">Distressed %</th>'
        '<th class="n">SS Bal (+Inflow)</th><th class="n">Haircut</th>'
        '<th class="n">MoM Val \u0394</th><th class="n">Wtd DSCR</th><th class="n">Inc. Loss</th>'
        "</tr></thead><tbody>"
    ]
    for r in rows:
        cohort_lbl = esc(r["cohort"])
        if heading_col == "Maturity Year" and r.get("in_maturity_wall"):
            cohort_lbl += ' <span class="flag wall">Wall</span>'
        ss_in = f" (+{fmt_money(r['ss_inflow_balance'])})" if r["ss_inflow_balance"] > 0 else ""
        val_cls = "n bad" if r["valuation_change"] < 0 else ("n good" if r["valuation_change"] > 0 else "n")
        loss_cls = "n bad" if r["incremental_loss"] > 0 else "n"
        out.append(
            f"<tr><td><b>{cohort_lbl}</b></td>"
            f'<td class="n">{r["loans"]}</td>'
            f'<td class="n">{esc(fmt_money(r["balance"]))}</td>'
            f'<td class="n">{esc(fmt_pct(r["pool_share"]))}</td>'
            f'<td class="n">{esc(fmt_pct(r["office_share"]))}</td>'
            f'<td class="n">{esc(fmt_pct(r["distressed_share"]))}</td>'
            f'<td class="n">{esc(fmt_money(r["special_servicing_balance"]))}{esc(ss_in)}</td>'
            f'<td class="n">{esc(fmt_pct(r["haircut"]))}</td>'
            f'<td class="{val_cls}">{esc(fmt_signed_money(r["valuation_change"]))}</td>'
            f'<td class="n">{esc(fmt_x(r["wtd_dscr"]))}</td>'
            f'<td class="{loss_cls}">{esc(fmt_money(r["incremental_loss"]) if r["incremental_loss"] else "\u2014")}</td>'
            "</tr>"
        )
    out.append(
        f'</tbody><tfoot><tr><th>Total</th>'
        f'<th class="n">{tot_loans}</th>'
        f'<th class="n">{esc(fmt_money(tot_bal))}</th>'
        f'<th class="n">{esc(fmt_pct(1.0 if tot_bal else None))}</th>'
        f'<th class="n">{esc(fmt_pct(tot_off / tot_bal if tot_bal else None))}</th>'
        f'<th class="n">{esc(fmt_pct(tot_dist / tot_bal if tot_bal else None))}</th>'
        f'<th class="n">{esc(fmt_money(tot_ss))}{esc(f" (+{fmt_money(tot_ss_in)})" if tot_ss_in else "")}</th>'
        f'<th class="n">\u2014</th>'
        f'<th class="n">{esc(fmt_signed_money(tot_val_chg))}</th>'
        f'<th class="n">\u2014</th>'
        f'<th class="n">{esc(fmt_money(tot_inc_loss) if tot_inc_loss else "$0")}</th>'
        f"</tr></tfoot></table></div>"
    )
    _ = tot_prev  # kept for clarity; balance delta visible in KPI strip
    return "".join(out)


def render_longitudinal_section(longitudinal: dict) -> str:
    """Render the complete Monthly Migration & Appraisal Velocity section."""
    lt = longitudinal["latest_transition"]
    ss = lt["special_servicing"]
    vel = lt["appraisal_velocity"]
    off_vel = vel["office"]
    mig = lt["migration"]["combined"]
    cross = lt["migration"]["threshold_crossings"]
    delinq = lt["delinquency"]
    losses = lt["losses_and_liquidations"]
    cohorts = longitudinal["cohorts"]
    periods = longitudinal["periods"]

    mig_kpis = "".join(
        [
            kpi(
                "New SS inflows",
                fmt_money(ss["inflow_balance"]),
                f"{ss['inflow_count']} loans ({ss['inflow_office_count']} office) \u00b7 resolved {fmt_money(ss['resolved_prior_balance'])}",
            ),
            kpi(
                "Appraisal velocity (MoM)",
                fmt_signed_money(vel["valuation_change"]),
                f"{fmt_signed_pct(vel['valuation_change_pct'])} matched \u00b7 office {fmt_signed_money(off_vel['valuation_change'])} ({fmt_signed_pct(off_vel['valuation_change_pct'])})",
            ),
            kpi(
                "Credit downgrades",
                fmt_money(mig["downgraded"]["prior_balance"]),
                f"{mig['downgraded']['count']} loans down \u00b7 {mig['upgraded']['count']} cured \u00b7 {len(cross['dscr_crossed_below_1'])} crossed DSCR < 1.00x",
            ),
            kpi(
                "Incremental realized loss",
                fmt_money(losses["incremental_loss"]),
                f"{losses['liquidation_count']} liquidation ({fmt_money(losses['liquidated_balance'])}, {fmt_pct(losses['weighted_loss_severity'])} sev)",
            ),
            kpi(
                "New 30+d delinquencies",
                fmt_money(delinq["newly_delinquent"]["balance"]),
                f"{delinq['newly_delinquent']['count']} new \u00b7 {delinq['worsened']['count']} rolled deeper \u00b7 active {fmt_pct(delinq['curr_active_delinquent_share'])}",
            ),
        ]
    )

    cross_items = ", ".join(
        f"{c['asset_number']} ({fmt_x(c['prev_dscr'])} \u2192 {fmt_x(c['curr_dscr'])})"
        for c in cross["dscr_crossed_below_1"]
    )
    cross_note = f" DSCR crossed &lt;1.00x: <b>{esc(cross_items)}</b>." if cross_items else ""
    new_app_note = (
        f" Excludes {vel['newly_appraised_count']} newly appraised loan(s) lacking a prior appraisal."
        if vel["newly_appraised_count"]
        else ""
    )

    return f"""<section id="longitudinal">
<h2>Monthly Migration &amp; Appraisal Velocity</h2>
<p class="notes">Matched by <code>assetNumber</code> across {longitudinal['period_count']} dated tapes: <b>{esc(lt['prev_as_of'])}</b> \u2192 <b>{esc(lt['curr_as_of'])}</b> ({lt['matched_count']} matched loans; pool balance {esc(fmt_money(lt['prev_balance']))} \u2192 {esc(fmt_money(lt['curr_balance']))}, {esc(fmt_signed_money(lt['balance_change']))}).</p>
<div class="kpis" style="margin:12px 0 14px">{mig_kpis}</div>

<div class="grid2">
<div class="card"><h3>Appraisal valuation waterfall (MoM)</h3>{svg_appraisal_waterfall(vel)}
<div class="legend"><span class="sw" style="background:{ALERT_COLOR}"></span>Write-down<span class="sw" style="background:{POS_COLOR}"></span>Mark-up<span class="sw" style="background:{OFFICE_COLOR}"></span>Net MoM change</div>
<p class="cap">Matched valuations: {esc(fmt_money(vel['prev_valuation_sum']))} \u2192 {esc(fmt_money(vel['curr_valuation_sum']))} ({esc(fmt_signed_pct(vel['valuation_change_pct']))}); matched office haircut widened {esc(fmt_pp(off_vel['haircut_change']))}.{esc(new_app_note)}</p></div>

<div class="card"><h3>Distress &amp; office haircut trajectory</h3>{svg_distress_trajectory(periods)}
<div class="legend"><span class="sw" style="background:{ALERT_COLOR}"></span>Any distress<span class="sw" style="background:{AMBER_COLOR}"></span>30+d delinq<span class="sw" style="background:{OFFICE_COLOR}"></span>Special serv.<span class="sw" style="background:{PLUM_COLOR}"></span>Office haircut</div>
<p class="cap">Active share of pool balance (and value-weighted office appraisal haircut) across {longitudinal['period_count']} reporting periods.</p></div>
</div>

<div class="card" style="margin-top:14px">
<h3>DSCR &amp; occupancy credit migration matrix</h3>
<p class="notes">Rows = prior tier ({esc(lt['prev_as_of'])}); columns = current tier ({esc(lt['curr_as_of'])}). Tiers use strict thresholds: Healthy (DSCR \u2265 1.25x &amp; Occ \u2265 85%), Watch (1.00x\u20131.25x or 75%\u201385%), Distressed (DSCR &lt; 1.00x or Occ &lt; 75%). Excludes {mig['prior_inactive_count']} previously liquidated $0-balance loan(s).{cross_note}</p>
{migration_matrix_table(mig)}
</div>

<div class="card" style="margin-top:14px">
<h3>Monthly loan-level transition log</h3>
<p class="notes">Every matched loan with a special-servicing transfer, liquidation/payoff, delinquency transition, DSCR threshold breach, credit-tier downgrade, or reappraisal.</p>
{transition_events_table(lt)}
</div>

<div class="grid2" style="margin-top:14px">
<div class="card"><h3>Maturity-year cohort exposure</h3>
<p class="notes">Current pool exposure, 2026\u20132028 maturity wall flags, MoM valuation velocity, and incremental losses by maturity year.</p>
{cohort_exposure_table_html(cohorts['by_maturity_year'], 'Maturity Year')}</div>
<div class="card"><h3>Origination vintage cohort exposure</h3>
<p class="notes">Exposure and monthly credit deterioration grouped by loan <code>originationDate</code> vintage year.</p>
{cohort_exposure_table_html(cohorts['by_vintage_year'], 'Origination Vintage')}</div>
</div>
</section>"""


# ---------------------------------------------------------------- page


def loan_rows(result: ParseResult) -> list[dict]:
    rows = []
    for l in result.loans:
        flags = loan_flags(l)
        hc = haircut(l)
        rows.append(
            {
                "asset": l.asset_number,
                "name": l.property_name or "",
                "metro": l.metro,
                "city": f"{l.property_city or ''}, {l.property_state or ''}".strip(", "),
                "type": l.property_type,
                "balance": l.current_balance,
                "dscr": l.dscr,
                "occ": l.occupancy,
                "haircut": round(hc, 6) if hc is not None else None,
                "maturity": l.maturity_date.isoformat() if l.maturity_date else None,
                "vintage": l.vintage_year,
                "ss": l.special_servicer_transfer_date.isoformat() if l.special_servicer_transfer_date else None,
                "status": l.payment_status,
                "delinq": l.is_delinquent,
                "loss": l.realized_loss,
                "wall": in_wall(l),
                "flags": flags,
            }
        )
    return rows


CSS = """
:root{--ink:#14202e;--muted:#5b6b7c;--line:#dde3ea;--bg:#f7f8fa;--card:#fff;--navy:#1f3a5f;--rust:#b5452b;--green:#2f7d5b}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{background:var(--navy);color:#fff;padding:28px 0 22px}.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
h1{margin:0 0 4px;font-size:28px;letter-spacing:-.01em}header p{margin:0;color:#c9d6e3}
.badge{display:inline-block;margin-top:14px;padding:6px 12px;border-radius:999px;background:#fff3d6;color:#6b4a00;font-weight:600;font-size:13px;border:1px solid #f0d48a}
section{margin:26px 0}h2{font-size:19px;margin:0 0 10px}h3{font-size:15px;margin:0 0 6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.kpi .v{font-size:24px;font-weight:700}
.kpi .l{color:var(--muted);font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}.kpi .s{color:var(--muted);font-size:12.5px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:14px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}.card p.cap{color:var(--muted);font-size:12.5px;margin:6px 0 0}
svg{width:100%;height:auto;display:block}svg .axis{font-size:11px;fill:#5b6b7c}svg .subaxis{font-size:10px;fill:#7b8a99}svg .val{font-size:11px;fill:#14202e;font-weight:600}
svg .grid{stroke:#e8ecf1}svg .zero{stroke:#8795a5;stroke-dasharray:3 3}svg .conn{stroke:#9fb3c8;stroke-dasharray:2 2}
.legend{font-size:12px;color:var(--muted)}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
table{border-collapse:collapse;width:100%;background:var(--card)}
.heat th,.heat td{border:1px solid var(--line);padding:8px 10px;text-align:center;font-size:13px}.heat thead th,.heat tfoot th{background:#eef2f6}
.heat tbody th{text-align:left;background:#fafbfc}.heat td b{display:block;font-size:14.5px}.heat td span{font-size:11.5px;color:#55606c}
.heat td.dark{color:#fff}.heat td.dark span{color:#fbe3dc}.heat td.empty{color:#b8c2cc}.heat td.tot{background:#eef2f6}
.mig td.mig-stay{background:#f1f5f9}.mig td.mig-up{background:#e3f3eb;color:#1b543b}.mig td.mig-res{background:#f3ede4;color:#5c4420}
.data th,.data td{padding:7px 9px;border-bottom:1px solid var(--line);font-size:12.5px;white-space:nowrap}
.data thead th,.data tfoot th{background:#eef2f6;text-align:left}.data td.n,.data th.n{text-align:right;font-variant-numeric:tabular-nums}
.data td.detail{white-space:normal;min-width:220px;color:#334155}
.tag{display:inline-block;padding:1px 7px;margin:1px 3px 1px 0;border-radius:999px;font-size:11px;font-weight:600}
.tag-bad{background:#fbe3dc;color:#8a2f1a}.tag-warn{background:#fef3c7;color:#854d0e}.tag-good{background:#dcfce7;color:#166534}.tag-info{background:#e3ebf5;color:#1f3a5f}
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
.filters select,.filters input{font:inherit;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:#fff}
.filters .count{color:var(--muted);font-size:13px;margin-left:auto}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
#loans th,#loans td{padding:7px 10px;border-bottom:1px solid var(--line);font-size:13px;white-space:nowrap}
#loans th{background:#eef2f6;text-align:left;cursor:pointer;user-select:none;position:sticky;top:0}#loans th:hover{background:#e2e8ef}
#loans td.n{text-align:right;font-variant-numeric:tabular-nums}#loans tr:hover td{background:#f8fafc}
.flag{display:inline-block;padding:1px 7px;margin-right:3px;border-radius:999px;font-size:11px;font-weight:600;background:#fbe3dc;color:#8a2f1a}
.flag.wall{background:#e3ebf5;color:#1f3a5f}.bad{color:var(--rust);font-weight:600}.good{color:var(--green);font-weight:600}
.method{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}.method ul{margin:6px 0;padding-left:18px}
.ev th,.ev td{border:1px solid var(--line);padding:7px 10px;font-size:13px;text-align:left;vertical-align:top}.ev th{background:#eef2f6}
.notes{font-size:13px;color:var(--muted)}footer{color:var(--muted);font-size:12.5px;padding:24px 0 40px;border-top:1px solid var(--line);margin-top:30px}
code{background:#eef2f6;padding:1px 5px;border-radius:4px;font-size:12.5px}
"""

JS = """
(function(){
const rows=JSON.parse(document.getElementById('loan-data').textContent);
const tb=document.querySelector('#loans tbody');
const fMetro=document.getElementById('f-metro'),fType=document.getElementById('f-type'),fFlag=document.getElementById('f-flag'),fQ=document.getElementById('f-q'),cnt=document.getElementById('count');
const FL={dscr_lt_1:'DSCR<1',occ_lt_75:'Occ<75',special_servicing:'SS'};
let sortKey='balance',sortDir=-1;
function uniq(k){return [...new Set(rows.map(r=>r[k]))].sort();}
function opt(sel,vals){vals.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;sel.appendChild(o);});}
opt(fMetro,uniq('metro'));opt(fType,uniq('type'));
function money(v){if(v===null)return '\\u2014';const s=v<0?'-':'';const a=Math.abs(v);if(a>=1e9)return s+'$'+(a/1e9).toFixed(2)+'B';if(a>=1e6)return s+'$'+(a/1e6).toFixed(1)+'M';return s+'$'+Math.round(a).toLocaleString('en-US');}
function pct(v){return v===null?'\\u2014':(v*100).toFixed(1)+'%';}
function x(v){return v===null?'\\u2014':v.toFixed(2)+'x';}
function td(text,cls){const c=document.createElement('td');c.textContent=text;if(cls)c.className=cls;return c;}
function keep(r){
 if(fMetro.value&&r.metro!==fMetro.value)return false;
 if(fType.value&&r.type!==fType.value)return false;
 const f=fFlag.value;
 if(f==='any'&&!r.flags.length)return false;
 if(f==='wall'&&!r.wall)return false;
 if(f==='delinquent'&&!r.delinq)return false;
 if(f&&f!=='any'&&f!=='wall'&&f!=='delinquent'&&!r.flags.includes(f))return false;
 const q=fQ.value.trim().toLowerCase();
 if(q&&!(r.asset+' '+r.name+' '+r.city).toLowerCase().includes(q))return false;
 return true;}
function cmp(a,b){const va=a[sortKey],vb=b[sortKey];if(va===vb)return 0;if(va===null)return 1;if(vb===null)return -1;
 if(Array.isArray(va))return (va.length-vb.length)*sortDir;return (va<vb?-1:1)*sortDir;}
function draw(){
 const shown=rows.filter(keep).sort(cmp);tb.textContent='';let bal=0;
 shown.forEach(r=>{bal+=r.balance||0;const tr=document.createElement('tr');
  tr.appendChild(td(r.asset));tr.appendChild(td(r.name));tr.appendChild(td(r.metro));tr.appendChild(td(r.city));tr.appendChild(td(r.type));
  tr.appendChild(td(money(r.balance),'n'));
  tr.appendChild(td(x(r.dscr),'n'+(r.dscr!==null&&r.dscr<1?' bad':'')));
  tr.appendChild(td(pct(r.occ),'n'+(r.occ!==null&&r.occ<0.75?' bad':'')));
  tr.appendChild(td(pct(r.haircut),'n'+(r.haircut!==null&&r.haircut>=0.3?' bad':'')));
  tr.appendChild(td(r.maturity||'\\u2014'));tr.appendChild(td(r.ss||'\\u2014'));
  tr.appendChild(td(r.status||'\\u2014',r.delinq?'bad':''));
  tr.appendChild(td(r.loss?money(r.loss):'\\u2014','n'));
  const fc=document.createElement('td');
  r.flags.forEach(f=>{const s=document.createElement('span');s.className='flag';s.textContent=FL[f];fc.appendChild(s);});
  if(r.wall){const s=document.createElement('span');s.className='flag wall';s.textContent='Wall';fc.appendChild(s);}
  tr.appendChild(fc);tb.appendChild(tr);});
 cnt.textContent=shown.length+' of '+rows.length+' loans \\u00b7 '+money(bal)+' balance';}
document.querySelectorAll('#loans th').forEach(th=>th.addEventListener('click',()=>{const k=th.dataset.key;
 if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=(th.dataset.num?-1:1);}draw();}));
[fMetro,fType,fFlag].forEach(e=>e.addEventListener('change',draw));fQ.addEventListener('input',draw);
draw();})();
"""

COLUMNS = [
    ("asset", "Asset", False),
    ("name", "Property", False),
    ("metro", "Metro", False),
    ("city", "City", False),
    ("type", "Type", False),
    ("balance", "Balance", True),
    ("dscr", "DSCR", True),
    ("occ", "Occupancy", True),
    ("haircut", "Haircut", True),
    ("maturity", "Maturity", False),
    ("ss", "SS transfer", False),
    ("status", "Pay status", False),
    ("loss", "Realized loss", True),
    ("flags", "Flags", True),
]


def kpi(label: str, value: str, sub: str = "") -> str:
    return (
        f'<div class="kpi"><div class="l">{esc(label)}</div><div class="v">{esc(value)}</div>'
        f'<div class="s">{esc(sub)}</div></div>'
    )


def render_html(
    result: ParseResult,
    title: str = "CMBS Office Distress Monitor",
    badge: str = BADGE,
    longitudinal: dict | None = None,
) -> str:
    s = summarize(result.loans)
    ov, of = s["overall"], s["office"]
    wall, d = s["maturity_wall"], s["distress"]
    as_of = result.as_of.isoformat() if result.as_of else "unknown"
    ph, oh = s["pool_haircut"], s["office_haircut"]

    period_clause = f"Reporting period end: <b>{esc(as_of)}</b>."
    source_display = result.source
    if longitudinal is not None:
        lt = longitudinal["latest_transition"]
        period_clause = (
            f"Reporting period end: <b>{esc(as_of)}</b> "
            f"(tracking {longitudinal['period_count']} filings: <b>{esc(longitudinal['start_as_of'])}</b> \u2192 <b>{esc(longitudinal['end_as_of'])}</b>)."
        )
        source_display = " \u2192 ".join(longitudinal["sources"])
        _ = lt

    kpis = "".join(
        [
            kpi("Current balance", fmt_money(ov["balance"]), f"{ov['loans']} loans \u00b7 office {fmt_pct(_share(of['balance'], ov['balance']))}"),
            kpi("2026\u20132028 maturity wall", fmt_pct(wall["share"]), f"{fmt_money(wall['balance'])} across {wall['count']} loans"),
            kpi("Office appraisal haircut", fmt_pct(oh["haircut"]), f"pool {fmt_pct(ph['haircut'])} \u00b7 {ph['loans_missing_appraisal']} loans lack an appraisal pair"),
            kpi("Special servicing", fmt_pct(d["special_servicing"]["share"]), f"{fmt_money(d['special_servicing']['balance'])} \u00b7 {d['special_servicing']['count']} loans"),
            kpi("DSCR < 1.00x", fmt_pct(d["dscr_lt_1"]["share"]), f"{d['dscr_lt_1']['count']} loans \u00b7 office wtd DSCR {fmt_x(of['wtd_dscr'])}"),
            kpi("Realized losses", fmt_money(ov["realized_loss"]), f"office {fmt_money(of['realized_loss'])}"),
        ]
    )

    longitudinal_html = render_longitudinal_section(longitudinal) if longitudinal is not None else ""

    filters = (
        '<div class="filters">'
        '<select id="f-metro" aria-label="Metro"><option value="">All metros</option></select>'
        '<select id="f-type" aria-label="Property type"><option value="">All property types</option></select>'
        '<select id="f-flag" aria-label="Flag"><option value="">All loans</option>'
        '<option value="any">Any distress flag</option><option value="dscr_lt_1">DSCR &lt; 1.00x</option>'
        '<option value="occ_lt_75">Occupancy &lt; 75%</option><option value="special_servicing">Special servicing</option>'
        '<option value="delinquent">30+d delinquent</option>'
        '<option value="wall">2026\u20132028 maturity</option></select>'
        '<input id="f-q" type="search" placeholder="Search asset, property, city" aria-label="Search">'
        '<span class="count" id="count"></span></div>'
    )
    head = "".join(
        f'<th data-key="{k}"{" data-num=\"1\"" if num else ""}>{esc(lbl)}</th>' for k, lbl, num in COLUMNS
    )
    notes = "".join(f"<li>{esc(n)}</li>" for n in result.notes) or "<li>None.</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="Loan-level CMBS distress dashboard built from SEC Form ABS-EE asset data: appraisal haircuts, longitudinal monthly migration, 2026-2028 maturity wall, and DSCR/occupancy/special-servicing buckets by metro and property type.">
<style>{CSS}</style>
</head>
<body>
<header><div class="wrap">
<h1>{esc(title)}</h1>
<p>Loan-level SEC Form ABS-EE (CMBS) asset data \u2192 appraisal haircuts, monthly credit migration, the 2026\u20132028 maturity wall, and distress by metro \u00d7 property type. {period_clause}</p>
<span class="badge" id="evidence-badge">{esc(badge)}</span>
</div></header>
<main class="wrap">
<section><div class="kpis">{kpis}</div></section>

{longitudinal_html}

<section class="grid3">
<div class="card"><h3>Maturity wall, 2026\u20132028</h3>{svg_maturity_wall(wall)}
<div class="legend"><span class="sw" style="background:{OFFICE_COLOR}"></span>Office<span class="sw" style="background:{OTHER_COLOR}"></span>Other property types</div>
<p class="cap">Current balance by calendar year of <code>maturityDate</code>. {fmt_pct(wall['share'])} of the pool; office is {fmt_pct(s['office_maturity_wall']['share'])} of office balance.</p></div>
<div class="card"><h3>Distress buckets (share of balance)</h3>{svg_distress(d, s['office_distress'])}
<div class="legend"><span class="sw" style="background:{OTHER_COLOR}"></span>All loans<span class="sw" style="background:{OFFICE_COLOR}"></span>Office only</div>
<p class="cap">Strict thresholds; loans that do not report a metric are not counted as distressed on it.</p></div>
<div class="card"><h3>Appraisal haircut by property type</h3>{svg_haircut_by_type(s['by_type'])}
<p class="cap">1 \u2212 \u03a3 most-recent valuation / \u03a3 securitisation valuation, over loans reporting both. Green = value up.</p></div>
</section>

<section><h2>Metro \u00d7 property type: distressed share of balance</h2>
<p class="notes">Cell = share of current balance carrying any distress flag (bold), then balance \u00b7 loan count. Hover for maturity-wall share and haircut.</p>
{heatmap_table(s)}</section>

<section><h2>Loan tape</h2>{filters}
<div class="tablewrap"><table id="loans"><thead><tr>{head}</tr></thead><tbody></tbody></table></div>
<noscript><p class="notes">The loan table needs JavaScript; every aggregate above is static.</p></noscript>
</section>

<section class="method"><div class="card"><h2>Methodology</h2><ul>
<li><b>Haircut</b> = 1 \u2212 <code>mostRecentValuationAmount</code> / <code>valuationSecuritizationAmount</code>. Pool and group haircuts are value-weighted.</li>
<li><b>Appraisal velocity</b> compares <code>mostRecentValuationAmount</code> across matched <code>assetNumber</code> pairs reporting a valuation in both periods; newly appraised loans are tracked separately.</li>
<li><b>Migration matrix</b> classifies active prior-period loans into Healthy (DSCR \u2265 1.25x and occupancy \u2265 85%), Watch (1.00x\u20131.25x or 75%\u201385%), and Distressed (DSCR &lt; 1.00x or occupancy &lt; 75%), plus Resolved/Liq when balance drops to zero.</li>
<li><b>Maturity wall</b> = balance with <code>maturityDate</code> in 2026\u20132028 (inclusive), over total current balance (<code>currentEndingScheduledBalanceAmount</code>).</li>
<li><b>Distress</b>: DSCR (<code>mostRecentDebtServiceCoverageNetOperatingIncomePercentage</code>) &lt; 1.00x; occupancy (<code>mostRecentPhysicalOccupancyPercentage</code>) &lt; 75%; special servicing = a special-servicer transfer date is reported; 30+d delinquency uses <code>paymentStatusLoanCode</code>.</li>
<li><b>Incremental realized loss</b> = current minus prior <code>realizedLossToTrustAmount</code> per loan so previously liquidated loans are never double-counted.</li>
</ul></div>
<div class="card"><h2>Evidence</h2><table class="ev"><thead><tr><th>Claim</th><th>Level</th></tr></thead><tbody>
<tr><td>Parser, snapshot analytics, longitudinal migration/velocity, and this page reproduce the synthetic fixtures exactly</td><td>fixture-proven</td></tr>
<tr><td>XML labels (including <code>paymentStatusLoanCode</code> &amp; <code>originationDate</code>) match live EX-102 filings</td><td>unverified</td></tr>
<tr><td>Numbers on this page describe any real trust</td><td>no \u2014 the inputs are synthetic</td></tr>
<tr><td>Live ingestion from SEC EDGAR</td><td>unverified (needs <code>CMBS_CONTACT</code>)</td></tr>
</tbody></table>
<h3 style="margin-top:12px">Parse notes</h3><ul class="notes">{notes}</ul></div>
</section>
</main>
<footer><div class="wrap">Source: <code>{esc(source_display)}</code> \u00b7 cmbs-office-distress v{esc(__version__)} \u00b7 Not investment advice. Synthetic fixture data does not describe any real loan, property or trust.</div></footer>
<script type="application/json" id="loan-data">{_json_for_script(loan_rows(result))}</script>
<script>{JS}</script>
</body>
</html>
"""


def _share(a: float, b: float) -> float | None:
    return a / b if b else None


def write_dashboard(
    result: ParseResult,
    out: str | Path,
    title: str = "CMBS Office Distress Monitor",
    badge: str = BADGE,
    longitudinal: dict | None = None,
) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_html(result, title=title, badge=badge, longitudinal=longitudinal))
    return p
