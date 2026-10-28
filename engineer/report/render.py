"""The driver report as one self-contained HTML page (inline CSS and SVG, no internet needed),
laid out for printing to a Letter-size PDF."""

from __future__ import annotations

from datetime import datetime
from html import escape

from engineer.report.insights import CornerAgg, Report

GAIN, LOSS = "#2a78d6", "#e34948"       # diverging pair: blue = quicker, red = slower

CSS = """
@page { size: Letter; margin: 12mm 12mm 14mm; }
:root {
  --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #8a8984; --rule: #e6e5e0; --soft: #f4f3ef;
  --surface: #fcfcfb; --gain: #2a78d6; --loss: #e34948; --mid: #d6d5cf; --brand: #e10600;
  --night: #15151e;
}
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fff; color: var(--ink);
  font: 10.5pt/1.45 "Segoe UI", "Helvetica Neue", Arial, sans-serif; }
.page { max-width: 780px; margin: 0 auto; padding: 0 0 24px; }
.num { font-variant-numeric: tabular-nums; }
header { background: var(--night); color: #fff; border-radius: 10px; padding: 22px 26px 20px;
  position: relative; overflow: hidden; }
header::after { content: ""; position: absolute; right: -40px; top: 0; bottom: 0; width: 170px;
  background: repeating-linear-gradient(115deg, transparent 0 14px, rgba(225,6,0,.85) 14px 22px);
  opacity: .9; }
header > * { position: relative; z-index: 1; margin-right: 150px; }
header .kicker { letter-spacing: .18em; font-size: 8pt; text-transform: uppercase;
  color: #ff6b63; font-weight: 600; }
header h1 { margin: 4px 0 6px; font-size: 22pt; line-height: 1.1; font-weight: 700; }
header .meta { color: #c9c8d2; font-size: 9.5pt; }
header .meta b { color: #fff; font-weight: 600; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 14px 0 6px; }
.tile { background: #fff; border: 1px solid var(--rule); border-radius: 8px; padding: 10px 12px; }
.tile .k { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .1em; color: var(--ink-3);
  font-weight: 600; }
.tile .v { font-size: 17pt; font-weight: 700; margin-top: 2px; line-height: 1.15; }
.tile .v.small { font-size: 11pt; line-height: 1.3; }
.tile .s { font-size: 8.5pt; color: var(--ink-2); margin-top: 3px; }
section { margin-top: 18px; }
h2, .lede { break-after: avoid; }
h2 { font-size: 12.5pt; margin: 0 0 2px; display: flex; align-items: baseline; gap: 8px; }
h2 .n { color: var(--brand); font-size: 10pt; font-weight: 700; }
.lede { color: var(--ink-2); font-size: 9pt; margin: 0 0 8px; }
.card { background: #fff; border: 1px solid var(--rule); border-radius: 8px; padding: 12px 14px; }
.rows { display: grid; grid-template-columns: 210px 1fr 64px 128px; gap: 6px 12px;
  align-items: center; }
.rows .h { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-3);
  font-weight: 600; }
.rows .name { font-weight: 600; break-inside: avoid; }
.rows .name small { display: block; font-weight: 400; color: var(--ink-3); font-size: 8pt; }
.rows .val { text-align: right; font-weight: 600; }
.rows .info { font-size: 8.5pt; color: var(--ink-2); }
.pill { display: inline-block; font-size: 7.5pt; padding: 1px 7px; border-radius: 99px;
  border: 1px solid var(--rule); color: var(--ink-2); background: var(--soft); white-space: nowrap; }
.pill.solid { border-color: #b9d3f2; background: #eaf2fc; color: #1c5cab; }
.themes { display: grid; gap: 10px; }
.theme { display: grid; grid-template-columns: 34px 1fr 150px; gap: 4px 12px; break-inside: avoid; }
.theme .rank { font-size: 20pt; font-weight: 800; color: var(--brand); line-height: 1; }
.theme h3 { margin: 0; font-size: 11.5pt; }
.theme .cost { font-weight: 700; color: var(--loss); }
.theme p { margin: 4px 0 0; font-size: 9.5pt; }
.theme .try { color: var(--ink-2); }
.theme .try b { color: var(--ink); }
.chips { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 8pt; background: var(--soft); border-radius: 4px; padding: 1px 6px; }
.chip b { color: var(--loss); font-weight: 600; }
.spark .k { font-size: 7.5pt; color: var(--ink-3); text-transform: uppercase; letter-spacing: .08em; }
ul.good { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
ul.good li { padding-left: 22px; position: relative; font-size: 9.5pt; }
ul.good li::before { content: "\\2713"; position: absolute; left: 0; top: 0; width: 15px; height: 15px;
  border-radius: 50%; background: #0ca30c; color: #fff; font-size: 9px; text-align: center;
  line-height: 15px; font-weight: 700; }
table { width: 100%; border-collapse: collapse; font-size: 8.8pt; }
th { text-align: left; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .08em;
  color: var(--ink-3); font-weight: 600; padding: 4px 6px; border-bottom: 1px solid var(--rule); }
td.nw { white-space: nowrap; }
td { padding: 4px 6px; border-bottom: 1px solid var(--soft); vertical-align: middle; }
tr { break-inside: avoid; }
td.r, th.r { text-align: right; }
.since { background: #fff8e6; border: 1px solid #f3dca0; border-radius: 8px; padding: 10px 14px;
  font-size: 9.5pt; margin-top: 12px; }
.since b { font-weight: 600; }
.up { color: var(--loss); } .down { color: #1c5cab; }
footer { margin-top: 18px; font-size: 8pt; color: var(--ink-3); border-top: 1px solid var(--rule);
  padding-top: 8px; }
footer p { margin: 3px 0; }
.legend { font-size: 8pt; color: var(--ink-2); display: flex; gap: 14px; margin: 4px 0 0; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  vertical-align: -1px; margin-right: 4px; }
"""


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone()


def _date(d: datetime) -> str:
    return f"{d:%a} {d.day} {d:%b %Y}"


def _laptime(ms: float) -> str:
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:06.3f}"


def _signed(v: float, unit: str = "s", digits: int = 2) -> str:
    return f"{'+' if v > 0 else '−' if v < 0 else '±'}{abs(v):.{digits}f}{unit}"


def _bar(v: float, span: float, width: int = 260, height: int = 14) -> str:
    """A diverging bar around a centre line: blue left (quicker), red right (slower)."""
    mid = width / 2
    w = min(abs(v) / span, 1.0) * (mid - 2) if span else 0
    x = mid - w if v < 0 else mid
    color = GAIN if v < 0 else LOSS
    return (f'<svg width="100%" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
            f'style="height:{height}px;display:block">'
            f'<line x1="{mid}" y1="0" x2="{mid}" y2="{height}" stroke="#d6d5cf" stroke-width="1"/>'
            f'<rect x="{x:.1f}" y="2" width="{max(w, 1.5):.1f}" height="{height - 4}" rx="3" '
            f'fill="{color}"/></svg>')


def _spark(points: list[tuple[str, float]], width: int = 150, height: int = 40) -> str:
    """Cost per session, oldest to newest (lower is better)."""
    if len(points) < 2:
        return '<div class="k">Trend</div><div class="lede">Needs another session</div>'
    vals = [v for _, v in points]
    top = max(max(vals), 0.05)
    pad = 5
    xs = [pad + i * (width - 2 * pad) / (len(vals) - 1) for i in range(len(vals))]
    ys = [height - pad - (v / top) * (height - 2 * pad) for v in vals]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{LOSS}" stroke="#fff" '
                   f'stroke-width="1.5"/>' for x, y in zip(xs, ys))
    first, last = _local(points[0][0]), _local(points[-1][0])
    return (f'<div class="k">Per session, s/lap</div>'
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" '
            f'stroke="#e6e5e0"/><path d="{path}" fill="none" stroke="{LOSS}" stroke-width="2" '
            f'stroke-linejoin="round"/>{dots}</svg>'
            f'<div class="lede" style="margin:0;font-size:7.5pt">{first:%b} {first.day} → '
            f'{last:%b} {last.day} · latest {vals[-1]:.2f}</div>')


def _history_chart(report: Report, width: int = 740, height: int = 170) -> str:
    """Your pace vs the quickest AI, session by session (below the line = quicker)."""
    es = report.sessions
    if len(es) < 2:
        return '<p class="lede">The trend appears once there are two or more sessions.</p>'
    vals = [100 * (e["my_median_ms"] - e["top_ai_median_ms"]) / e["top_ai_median_ms"] for e in es]
    span = max(1.0, max(abs(v) for v in vals)) * 1.15
    left, right, top, bottom = 44, 14, 16, 34
    pw, ph = width - left - right, height - top - bottom
    y = lambda v: top + ph / 2 + (v / span) * (ph / 2)
    xs = [left + (i + 0.5) * pw / len(vals) for i in range(len(vals))]
    grid = "".join(
        f'<line x1="{left}" x2="{width - right}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="'
        f'{"#bdbcb5" if g == 0 else "#efeee9"}"/><text x="{left - 6}" y="{y(g) + 3:.1f}" '
        f'text-anchor="end" font-size="8" fill="#8a8984">{_signed(g, "%", 1) if g else "0%"}</text>'
        for g in (-span / 1.15, -span / 2.3, 0, span / 2.3, span / 1.15))
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y(v):.1f}" for i, (x, v) in enumerate(zip(xs, vals)))
    marks = ""
    for x, v, e in zip(xs, vals, es):
        d = _local(e["recorded_at"])
        marks += (f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="4" fill="{GAIN if v < 0 else LOSS}" '
                  f'stroke="#fff" stroke-width="2"/>'
                  f'<text x="{x:.1f}" y="{y(v) - 8 if v < 0 else y(v) + 14:.1f}" text-anchor="middle" '
                  f'font-size="8" fill="#52514e">{_signed(v, "%", 1)}</text>'
                  f'<text x="{x:.1f}" y="{height - 18}" text-anchor="middle" font-size="8.5" '
                  f'font-weight="600" fill="#0b0b0b">{escape(e["track"])}</text>'
                  f'<text x="{x:.1f}" y="{height - 7}" text-anchor="middle" font-size="7.5" '
                  f'fill="#8a8984">{d:%b} {d.day} · {escape(e["session"])}</text>')
    return (f'<svg width="100%" viewBox="0 0 {width} {height}">{grid}'
            f'<path d="{path}" fill="none" stroke="#8a8984" stroke-width="1.5" '
            f'stroke-dasharray="3 3"/>{marks}<text x="{left - 6}" y="9" text-anchor="end" '
            f'font-size="8" fill="#8a8984">quicker ↑</text></svg>')


def _phase(c: CornerAgg) -> str:
    """Where in the corner the time goes (or comes from)."""
    parts = {"entry": c.dv("in_s"), "exit": c.dv("out_s"), "the straight after": c.dv("carry_s")}
    if c.dv("time_s") + max(0.0, c.dv("carry_s")) >= 0:
        k = max(parts, key=parts.get)
        return f"losing on {k}" if parts[k] > 0.01 else "level"
    k = min(parts, key=parts.get)
    return f"gaining on {k}"


def _since(report: Report) -> str:
    prev = report.previous
    if not prev:
        return ""
    new = [e for e in report.sessions if e["file"] not in set(prev.get("files", []))]
    when = _local(prev["generated"])
    bits = [f"<b>{len(new)} new session{'s' if len(new) != 1 else ''}</b> since the last report "
            f"({_date(when)})."]
    d = report.overall_pct - prev.get("overall_pct", report.overall_pct)
    if abs(d) >= 0.05:
        bits.append(f"Overall pace vs the quickest AI {'improved' if d < 0 else 'slipped'} by "
                    f"{abs(d):.2f}%.")
    from engineer.report.insights import theme_costs
    now = theme_costs(report.corners)
    titles = {t.key: t.title for t in report.themes}
    for k, v in now.items():
        old = prev.get("themes", {}).get(k)
        if old is None or abs(v - old) < 0.03 or k not in titles:
            continue
        cls, word = ("down", "down") if v < old else ("up", "up")
        bits.append(f'{escape(titles[k])}: <span class="{cls}">{word} {abs(v - old):.2f} s/lap</span>.')
    return f'<div class="since">{" ".join(bits)}</div>'


def render_html(report: Report) -> str:
    r = report
    gen = r.generated.astimezone()
    tracks = ", ".join(t.track for t in r.tracks) or "none yet"
    first = _local(r.sessions[0]["recorded_at"]) if r.sessions else gen
    best_type = r.types[0] if r.types else None
    top_theme = r.themes[0] if r.themes else None
    tiles = [
        ("Pace vs quickest AI", f'<div class="v num" style="color:{GAIN if r.overall_pct < 0 else LOSS}">'
         f'{_signed(r.overall_pct, "%")}</div>',
         "median clean lap, all tracks" if r.overall_pct else "no laps yet"),
        ("Biggest strength", f'<div class="v small">{escape(best_type.name) if best_type else "—"}</div>',
         f"{_signed(best_type.pct, '%', 1)} vs the AI" if best_type else ""),
        ("Work on first", f'<div class="v small">{escape(top_theme.title) if top_theme else "Nothing stands out"}</div>',
         f"about {top_theme.cost_s:.2f} s a lap" if top_theme else "keep racing for more data"),
        ("Typical vs your best", f'<div class="v num">{r.ideal_gap_s:.2f}s</div>',
         "per lap, if every corner matched your best run"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="k">{k}</div>{v}<div class="s">{s}</div></div>'
                         for k, v, s in tiles)

    span = max([abs(t.pct) for t in r.tracks] + [0.5]) * 1.1
    track_rows = "".join(
        f'<div class="name">{escape(t.track)}<small>best lap {_laptime(t.best_lap_ms)}</small></div>'
        f'<div>{_bar(t.pct, span)}</div><div class="val num">{_signed(t.pct, "%")}</div>'
        f'<div class="info">P{t.rank:.0f} pace of {t.cars} · {t.laps} laps '
        f'<span class="pill {"solid" if t.confidence == "solid" else ""}">{t.confidence}</span></div>'
        for t in r.tracks)

    tspan = max([abs(t.relative) for t in r.types] + [0.5]) * 1.1
    type_rows = "".join(
        f'<div class="name">{escape(t.name)}<small>{", ".join(escape(x) for x in t.examples[:3])}</small></div>'
        f'<div>{_bar(t.relative, tspan)}</div><div class="val num">{_signed(t.relative, "%", 1)}</div>'
        f'<div class="info">{_signed(t.pct, "%", 1)} vs AI · {t.corners} corner{"s" if t.corners != 1 else ""}</div>'
        for t in r.types)

    theme_cards = "".join(
        f'<div class="card theme"><div class="rank">{i}</div><div>'
        f'<h3>{escape(t.title)} · <span class="cost num">{t.cost_s:.2f} s/lap</span> '
        f'<span class="pill {"solid" if t.confidence == "solid" else ""}">{t.confidence}</span></h3>'
        f'<p>{escape(t.detail)}</p><p class="try"><b>Try:</b> {escape(t.advice)}</p>'
        f'<div class="chips">{"".join(f"<span class=chip>{escape(lbl)} <b>{v:.2f}s</b></span>" for lbl, v in t.corners[:6])}</div>'
        f'</div><div class="spark">{_spark(t.trend)}</div></div>'
        for i, t in enumerate(r.themes, start=1)) or \
        '<div class="card">No habit costs more than a few hundredths a lap yet.</div>'

    strengths = "".join(f"<li>{escape(s)}</li>" for s in r.strengths) or \
        "<li>More sessions needed to call a strength with confidence.</li>"

    cspan = max([abs(c.pct) for c in r.corners] + [1.0]) * 1.05
    corner_rows = "".join(
        f'<tr><td class="nw"><b>{escape(c.track)}</b> T{c.number}</td><td class="nw">{escape(c.short_type)}</td>'
        f'<td class="r num">{c.ref_min_kph:.0f}</td>'
        f'<td class="r num">{_signed(c.dv("time_s"))}</td>'
        f'<td style="width:120px">{_bar(c.pct, cspan, 120, 12)}</td>'
        f'<td class="nw">{_phase(c)}</td><td class="r num">{f"{c.spread_s:.2f}s" if c.laps > 1 else "—"}</td>'
        f'<td class="r num">{c.laps}</td></tr>'
        for c in sorted(r.corners, key=lambda c: c.pct))

    skipped = ""
    if r.skipped:
        skipped = "<p>Not used: " + "; ".join(
            f"{escape(e['file'])} ({escape(e.get('skipped', 'no corners'))})" for e in r.skipped) + ".</p>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Driver Report</title><style>{CSS}</style></head>
<body><div class="page">
<header>
  <div class="kicker">Race Engineer · Driver report</div>
  <h1>Where your lap time lives</h1>
  <div class="meta"><b>{_date(gen)}</b> · {len(r.sessions)} session{"s" if len(r.sessions) != 1 else ""},
  {r.laps} clean laps since {_date(first)} · {escape(tracks)}</div>
</header>
{_since(r)}
<div class="tiles">{tiles_html}</div>

<section><h2><span class="n">01</span>Tracks</h2>
<p class="lede">Your median clean lap against the median of the five quickest AI cars in the same session.</p>
<div class="card"><div class="rows">{track_rows or "<div>No sessions yet.</div>"}</div>
<div class="legend"><span><i style="background:{GAIN}"></i>quicker than the AI</span>
<span><i style="background:{LOSS}"></i>slower</span></div></div></section>

<section><h2><span class="n">02</span>Corner types</h2>
<p class="lede">Each type against your average corner: left of the line is where you're strongest.</p>
<div class="card"><div class="rows">{type_rows or "<div>No corners measured yet.</div>"}</div></div></section>

<section><h2><span class="n">03</span>What costs you time</h2>
<p class="lede">Habits found in your traces, ranked by the time they cost per lap. Costs can overlap
(one corner can show two habits).</p>
<div class="themes">{theme_cards}</div></section>

<section><h2><span class="n">04</span>Strengths</h2>
<div class="card"><ul class="good">{strengths}</ul></div></section>

<section><h2><span class="n">05</span>Session by session</h2>
<p class="lede">Your pace against the quickest AI in each session. AI difficulty and tyres differ
between sessions, so read the direction, not single points.</p>
<div class="card">{_history_chart(r)}</div></section>

<section style="break-inside:auto"><h2><span class="n">06</span>Corner by corner</h2>
<p class="lede">Quickest to slowest against the AI. "Spread" is your typical run minus your best run
through that corner.</p>
<div class="card" style="padding:6px 8px"><table>
<tr><th>Corner</th><th>Type</th><th class="r">AI min kph</th><th class="r">vs AI</th><th></th>
<th>Where</th><th class="r">Spread</th><th class="r">Laps</th></tr>
{corner_rows}</table></div></section>

<footer>
<p>Built from your recordings by the race engineer. Reference: the five quickest AI cars in each
session, clean laps only (no safety car, start, pit or invalid laps). Newer sessions count more
(weight halves every 30 days). Turn numbers come from the track maps in data/tracks (official
numbers where the map has been named; otherwise the engineer's own count of braking zones).</p>
{skipped}
</footer>
</div></body></html>"""
