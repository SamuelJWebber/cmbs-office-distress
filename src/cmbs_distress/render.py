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
from .parser import ParseResult

BADGE = (
    "Fixture-proven v0.1 prototype \u2014 configure CMBS_CONTACT in Actions for live SEC ABS-EE ingestion"
)

OFFICE_COLOR = "#1f3a5f"
OTHER_COLOR = "#9fb3c8"
ALERT_COLOR = "#b5452b"
POS_COLOR = "#2f7d5b"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_money(v: float | None) -> str:
    if v is None:
        return "\u2014"
    if abs(v) >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


def fmt_pct(v: float | None, digits: int = 1) -> str:
    return "\u2014" if v is None else f"{v * 100:.{digits}f}%"


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
                "ss": l.special_servicer_transfer_date.isoformat() if l.special_servicer_transfer_date else None,
                "loss": l.realized_loss,
                "wall": in_wall(l),
                "flags": flags,
            }
        )
    return rows


CSS = """
:root{--ink:#14202e;--muted:#5b6b7c;--line:#dde3ea;--bg:#f7f8fa;--card:#fff;--navy:#1f3a5f;--rust:#b5452b}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{background:var(--navy);color:#fff;padding:28px 0 22px}.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
h1{margin:0 0 4px;font-size:28px;letter-spacing:-.01em}header p{margin:0;color:#c9d6e3}
.badge{display:inline-block;margin-top:14px;padding:6px 12px;border-radius:999px;background:#fff3d6;color:#6b4a00;font-weight:600;font-size:13px;border:1px solid #f0d48a}
section{margin:26px 0}h2{font-size:19px;margin:0 0 10px}h3{font-size:15px;margin:0 0 6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.kpi .v{font-size:24px;font-weight:700}
.kpi .l{color:var(--muted);font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}.kpi .s{color:var(--muted);font-size:12.5px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}.card p.cap{color:var(--muted);font-size:12.5px;margin:6px 0 0}
svg{width:100%;height:auto;display:block}svg .axis{font-size:11px;fill:#5b6b7c}svg .val{font-size:11px;fill:#14202e;font-weight:600}
svg .grid{stroke:#e8ecf1}svg .zero{stroke:#8795a5;stroke-dasharray:3 3}
.legend{font-size:12px;color:var(--muted)}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
table{border-collapse:collapse;width:100%;background:var(--card)}
.heat th,.heat td{border:1px solid var(--line);padding:8px 10px;text-align:center;font-size:13px}.heat thead th{background:#eef2f6}
.heat tbody th{text-align:left;background:#fafbfc}.heat td b{display:block;font-size:15px}.heat td span{font-size:11.5px;color:#55606c}
.heat td.dark{color:#fff}.heat td.dark span{color:#fbe3dc}.heat td.empty{color:#b8c2cc}.heat td.tot{background:#eef2f6}
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
.filters select,.filters input{font:inherit;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:#fff}
.filters .count{color:var(--muted);font-size:13px;margin-left:auto}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
#loans th,#loans td{padding:7px 10px;border-bottom:1px solid var(--line);font-size:13px;white-space:nowrap}
#loans th{background:#eef2f6;text-align:left;cursor:pointer;user-select:none;position:sticky;top:0}#loans th:hover{background:#e2e8ef}
#loans td.n{text-align:right;font-variant-numeric:tabular-nums}#loans tr:hover td{background:#f8fafc}
.flag{display:inline-block;padding:1px 7px;margin-right:3px;border-radius:999px;font-size:11px;font-weight:600;background:#fbe3dc;color:#8a2f1a}
.flag.wall{background:#e3ebf5;color:#1f3a5f}.bad{color:var(--rust);font-weight:600}
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
function money(v){if(v===null)return '\\u2014';if(Math.abs(v)>=1e9)return '$'+(v/1e9).toFixed(2)+'B';if(Math.abs(v)>=1e6)return '$'+(v/1e6).toFixed(1)+'M';return '$'+Math.round(v).toLocaleString('en-US');}
function pct(v){return v===null?'\\u2014':(v*100).toFixed(1)+'%';}
function x(v){return v===null?'\\u2014':v.toFixed(2)+'x';}
function td(text,cls){const c=document.createElement('td');c.textContent=text;if(cls)c.className=cls;return c;}
function keep(r){
 if(fMetro.value&&r.metro!==fMetro.value)return false;
 if(fType.value&&r.type!==fType.value)return false;
 const f=fFlag.value;
 if(f==='any'&&!r.flags.length)return false;
 if(f==='wall'&&!r.wall)return false;
 if(f&&f!=='any'&&f!=='wall'&&!r.flags.includes(f))return false;
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
) -> str:
    s = summarize(result.loans)
    ov, of = s["overall"], s["office"]
    wall, d = s["maturity_wall"], s["distress"]
    as_of = result.as_of.isoformat() if result.as_of else "unknown"
    ph, oh = s["pool_haircut"], s["office_haircut"]

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

    filters = (
        '<div class="filters">'
        '<select id="f-metro" aria-label="Metro"><option value="">All metros</option></select>'
        '<select id="f-type" aria-label="Property type"><option value="">All property types</option></select>'
        '<select id="f-flag" aria-label="Flag"><option value="">All loans</option>'
        '<option value="any">Any distress flag</option><option value="dscr_lt_1">DSCR &lt; 1.00x</option>'
        '<option value="occ_lt_75">Occupancy &lt; 75%</option><option value="special_servicing">Special servicing</option>'
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
<meta name="description" content="Loan-level CMBS distress dashboard built from SEC Form ABS-EE asset data: appraisal haircuts, 2026-2028 maturity wall, DSCR/occupancy/special-servicing buckets by metro and property type.">
<style>{CSS}</style>
</head>
<body>
<header><div class="wrap">
<h1>{esc(title)}</h1>
<p>Loan-level SEC Form ABS-EE (CMBS) asset data \u2192 appraisal haircuts, the 2026\u20132028 maturity wall, and distress by metro \u00d7 property type. Reporting period end: <b>{esc(as_of)}</b>.</p>
<span class="badge" id="evidence-badge">{esc(badge)}</span>
</div></header>
<main class="wrap">
<section><div class="kpis">{kpis}</div></section>

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
<li><b>Maturity wall</b> = balance with <code>maturityDate</code> in 2026\u20132028 (inclusive), over total current balance (<code>currentEndingScheduledBalanceAmount</code>).</li>
<li><b>Distress</b>: DSCR (<code>mostRecentDebtServiceCoverageNetOperatingIncomePercentage</code>) &lt; 1.00x; occupancy (<code>mostRecentPhysicalOccupancyPercentage</code>) &lt; 75%; special servicing = a special-servicer transfer date is reported.</li>
<li><b>Weighted DSCR / occupancy</b> are balance-weighted over loans that report the metric and carry a positive balance.</li>
<li><b>Metro</b> is assigned from <code>propertyCity</code> + <code>propertyState</code> via a fixed lookup; unmatched cities fall into \u201cOther\u201d.</li>
</ul></div>
<div class="card"><h2>Evidence</h2><table class="ev"><thead><tr><th>Claim</th><th>Level</th></tr></thead><tbody>
<tr><td>Parser, analytics and this page reproduce the synthetic fixture exactly</td><td>fixture-proven</td></tr>
<tr><td>XML labels match live EX-102 filings</td><td>unverified</td></tr>
<tr><td>Numbers on this page describe any real trust</td><td>no \u2014 the input is synthetic</td></tr>
<tr><td>Live ingestion from SEC EDGAR</td><td>unverified (needs <code>CMBS_CONTACT</code>)</td></tr>
</tbody></table>
<h3 style="margin-top:12px">Parse notes</h3><ul class="notes">{notes}</ul></div>
</section>
</main>
<footer><div class="wrap">Source: <code>{esc(result.source)}</code> \u00b7 cmbs-office-distress v{esc(__version__)} \u00b7 Not investment advice. Synthetic fixture data does not describe any real loan, property or trust.</div></footer>
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
) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_html(result, title=title, badge=badge))
    return p
