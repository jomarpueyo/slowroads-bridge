"""Ride dashboard: a single local HTML page with your scoreboard and charts (rides.bat opens it).

  python -m bridge.dashboard [--no-open]     writes logs/dashboard.html

Built from the trainer data in logs/ (never the game). It stays on this PC: logs/ is git-ignored, and the
page loads nothing from the internet.
"""

import html
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .coach import Coach, _hms
from .ridebook import DURATIONS, duration_label

CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d2329;--muted:#6b7480;--line:#e3e1dc;--accent:#2f7de1;--accent2:#e8a23a;
--good:#3aa66a;--bad:#d9534f}
@media (prefers-color-scheme:dark){:root{--bg:#14181c;--card:#1d2328;--ink:#e8ecef;--muted:#8d98a3;--line:#2c343b;
--accent:#5aa2ff;--accent2:#f0b45a;--good:#5cc98a;--bad:#ef7d78}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 "Segoe UI",system-ui,sans-serif}main{max-width:1060px;margin:0 auto;padding:28px 16px 60px}
h1{font-weight:300;font-size:30px;letter-spacing:.5px;margin:0}h2{font-size:12px;letter-spacing:2px;
text-transform:uppercase;color:var(--muted);font-weight:600;margin:0 0 12px}.sub{color:var(--muted);margin:4px 0 22px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile b{display:block;font-size:26px;font-weight:300}.tile span{font-size:11px;letter-spacing:1.5px;
text-transform:uppercase;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
gap:16px;margin-bottom:16px}svg{width:100%;height:auto;display:block}svg text{fill:var(--muted);font-size:11px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:right;padding:6px 8px;
border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600;font-size:12px}th:first-child,td:first-child{text-align:left}
td.l{text-align:left;color:var(--muted)}ul{margin:0;padding-left:18px}li{margin:4px 0}.tag{display:inline-block;
background:var(--accent);color:#fff;border-radius:999px;padding:1px 9px;font-size:12px}.scroll{overflow-x:auto}
.legend{font-size:12px;color:var(--muted)}.legend i{display:inline-block;width:10px;height:3px;margin:0 5px 3px 10px}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _weekly_svg(coach: Coach, weeks: int = 12) -> str:
    data = [coach.week(i) for i in range(weeks - 1, -1, -1)]
    w, h, pad = 520, 200, 30
    top = max([d["minutes"] for d in data] + [coach.weekly_minutes]) * 1.15 or 1
    bw = (w - pad) / weeks
    goal_y = h - pad - coach.weekly_minutes / top * (h - pad - 10)
    bars = []
    for i, d in enumerate(data):
        bh = d["minutes"] / top * (h - pad - 10)
        x = pad + i * bw + 4
        colour = "var(--good)" if coach.goal_met(d) else "var(--accent)"
        bars.append(f'<rect x="{x:.1f}" y="{h - pad - bh:.1f}" width="{bw - 8:.1f}" height="{bh:.1f}" rx="3" '
                    f'fill="{colour}"><title>{d["start"]:%d %b}: {d["rides"]} rides, {d["minutes"]:.0f} min</title></rect>')
        if i % 2 == 0:
            bars.append(f'<text x="{x + (bw - 8) / 2:.1f}" y="{h - 10}" text-anchor="middle">{d["start"]:%d %b}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Minutes ridden per week">'
            f'<line x1="{pad}" x2="{w}" y1="{goal_y:.1f}" y2="{goal_y:.1f}" stroke="var(--accent2)" stroke-dasharray="4 4"/>'
            f'<text x="{w}" y="{goal_y - 4:.1f}" text-anchor="end">goal {coach.weekly_minutes} min</text>'
            + "".join(bars) + "</svg>")


def _curve_svg(coach: Coach) -> str:
    rec = coach.records()["curve"]
    recent_cut = coach.today - timedelta(days=30)
    recent = {}
    for r in coach.rides:
        if r.when.date() >= recent_cut:
            for d, v in r.curve.items():
                recent[d] = max(recent.get(d, 0.0), v)
    if not rec:
        return "<p class='sub'>No power curve yet.</p>"
    w, h, pl, pb = 520, 220, 40, 26
    xs = [d for d in DURATIONS if d in rec]
    top = max(v for v, _ in rec.values()) * 1.1
    lx = lambda d: pl + (math.log(d) - math.log(xs[0])) / max(1e-9, math.log(xs[-1]) - math.log(xs[0])) * (w - pl - 10)  # noqa: E731
    ly = lambda v: h - pb - v / top * (h - pb - 10)  # noqa: E731

    def path(points):
        return " ".join(("M" if i == 0 else "L") + f"{lx(d):.1f},{ly(v):.1f}" for i, (d, v) in enumerate(points))

    best = [(d, rec[d][0]) for d in xs]
    last30 = [(d, recent[d]) for d in xs if d in recent]
    ticks = "".join(f'<text x="{lx(d):.1f}" y="{h - 8}" text-anchor="middle">{duration_label(d)}</text>'
                    for d in xs if d in (5, 60, 300, 1200, 3600))
    grid = "".join(f'<line x1="{pl}" x2="{w}" y1="{ly(v):.1f}" y2="{ly(v):.1f}" stroke="var(--line)"/>'
                   f'<text x="{pl - 6}" y="{ly(v) + 4:.1f}" text-anchor="end">{v}</text>'
                   for v in range(0, int(top), 100 if top > 400 else 50))
    dots = "".join(f'<circle cx="{lx(d):.1f}" cy="{ly(v):.1f}" r="3" fill="var(--accent)"><title>{duration_label(d)}: '
                   f'{v:.0f} W ({rec[d][1].when:%d %b})</title></circle>' for d, v in best)
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Best power for each duration">{grid}'
            f'<path d="{path(best)}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>'
            + (f'<path d="{path(last30)}" fill="none" stroke="var(--accent2)" stroke-width="2" stroke-dasharray="5 4"/>'
               if len(last30) > 1 else "")
            + dots + ticks + "</svg>"
            '<div class="legend"><i style="background:var(--accent)"></i>all-time best'
            '<i style="background:var(--accent2)"></i>last 30 days</div>')


def _load_svg(coach: Coach) -> str:
    series = coach.load_series(90)
    if len(series) < 2:
        return "<p class='sub'>Needs an FTP (set one or do the ramp test) and a few rides.</p>"
    w, h, pl, pb = 520, 200, 34, 24
    top = max(max(s[1], s[2]) for s in series) * 1.2 or 1
    low = min(min(s[3] for s in series), 0) * 1.2
    span = top - low or 1
    lx = lambda i: pl + i / (len(series) - 1) * (w - pl - 10)  # noqa: E731
    ly = lambda v: 10 + (top - v) / span * (h - pb - 10)  # noqa: E731

    def line(idx, colour, dash=""):
        pts = " ".join(f"{lx(i):.1f},{ly(s[idx]):.1f}" for i, s in enumerate(series))
        return f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="2" {dash}/>'

    zero = f'<line x1="{pl}" x2="{w}" y1="{ly(0):.1f}" y2="{ly(0):.1f}" stroke="var(--line)"/>'
    labels = "".join(f'<text x="{lx(i):.1f}" y="{h - 6}" text-anchor="middle">{series[i][0]:%d %b}</text>'
                     for i in range(0, len(series), max(1, len(series) // 5)))
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Fitness, fatigue and form">{zero}'
            + line(1, "var(--accent)") + line(2, "var(--bad)", 'stroke-dasharray="4 3"') + line(3, "var(--good)")
            + labels + "</svg><div class='legend'><i style='background:var(--accent)'></i>fitness"
            "<i style='background:var(--bad)'></i>fatigue<i style='background:var(--good)'></i>form</div>")


def render_dashboard(coach: Coach) -> str:
    life = coach.lifetime()
    w = coach.week(0)
    ftp, source = coach.ftp()
    rec = coach.records()
    curve = rec["curve"]
    tiles = [(f"{life['miles']:.1f}", "lifetime miles"), (f"{life['seconds'] / 3600:.1f}", "hours"),
             (str(life["rides"]), "rides"), (f"{life['kj']:.0f}", "kJ (~kcal)"),
             (f"{w['rides']}/{coach.weekly_rides}", "rides this week"), (f"{coach.streak_weeks()}", "week streak"),
             (f"{ftp:.0f} W" if ftp else "--", "FTP " + ("set" if source == "set" else "estimate"))]
    tile_html = "".join(f"<div class='tile'><b>{_esc(v)}</b><span>{_esc(k)}</span></div>" for v, k in tiles)
    rec_rows = "".join(f"<tr><td>{duration_label(d)}</td><td>{curve[d][0]:.0f} W</td>"
                       f"<td class='l'>{curve[d][1].when:%d %b %Y}</td></tr>"
                       for d in (5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600) if d in curve)
    if rec["longest"]:
        rec_rows += (f"<tr><td>longest ride</td><td>{_hms(rec['longest'].moving_s)}</td>"
                     f"<td class='l'>{rec['longest'].when:%d %b %Y}</td></tr>"
                     f"<tr><td>farthest ride</td><td>{rec['farthest'].miles:.1f} mi</td>"
                     f"<td class='l'>{rec['farthest'].when:%d %b %Y}</td></tr>")
    focus = "".join(f"<li>{_esc(t)}</li>" for _, t in coach.focus_areas()) or "<li>Nothing stands out: keep riding!</li>"
    good = "".join(f"<li>{_esc(t)}</li>" for t in coach.strengths()) or "<li>More rides will show them.</li>"
    from .workouts import title_of

    key, why = coach.suggest()
    rows = "".join(
        f"<tr><td>{r.when:%a %d %b %H:%M}</td><td>{_hms(r.moving_s)}</td><td>{r.miles:.1f}</td><td>{r.avg_w:.0f}</td>"
        f"<td>{(f'{r.np_w:.0f}' if r.np_w else '--')}</td><td>{r.work_kj:.0f}</td><td>{r.avg_cad:.0f}</td>"
        f"<td>{_hms(r.longest_steady_s)}</td><td class='l'>{_esc(r.workout or '')} {_esc(r.note)}</td></tr>"
        for r in reversed(coach.rides))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Ride Book</title><style>{CSS}</style></head>
<body><main><h1>Ride book</h1><p class="sub">Trainer data only &middot; updated {datetime.now():%a %d %b %Y %H:%M}
{f"&middot; since {life['since']:%d %b %Y}" if life['since'] else ""}</p>
<div class="tiles">{tile_html}</div>
<div class="card" style="margin-bottom:16px"><h2>Next ride</h2><span class="tag">{_esc(title_of(key, coach))}</span>
&nbsp;{_esc(why)}</div>
<div class="grid"><div class="card"><h2>Minutes per week</h2>{_weekly_svg(coach)}</div>
<div class="card"><h2>Power curve</h2>{_curve_svg(coach)}</div></div>
<div class="grid"><div class="card"><h2>Work on</h2><ul>{focus}</ul></div>
<div class="card"><h2>Strengths</h2><ul>{good}</ul></div></div>
<div class="grid"><div class="card"><h2>Fitness and form</h2>{_load_svg(coach)}</div>
<div class="card"><h2>Records</h2><table>{rec_rows}</table></div></div>
<div class="card scroll"><h2>All rides</h2><table><tr><th>date</th><th>moving</th><th>miles</th><th>avg W</th><th>NP</th>
<th>kJ</th><th>rpm</th><th>steady</th><th>workout / note</th></tr>{rows}</table></div>
</main></body></html>"""


def write_dashboard(log_dir: Path) -> Path:
    coach = Coach.from_settings(Path(log_dir))
    out = Path(log_dir) / "dashboard.html"
    out.write_text(render_dashboard(coach), encoding="utf-8")
    return out


def main(argv=None) -> int:
    from .ridelog import LOG_DIR

    argv = sys.argv[1:] if argv is None else argv
    out = write_dashboard(LOG_DIR)
    print(f"dashboard: {out}")
    if "--no-open" not in argv and sys.platform == "win32":
        import os

        os.startfile(str(out))
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "dashboard"))
