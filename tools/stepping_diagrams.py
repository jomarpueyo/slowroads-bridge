"""Generate the "how the limit steps" diagrams from the bridge's real logic.

Runs six riding situations through the actual virtual bike, push bonus, coast hold, resume grace,
release and dropout handling (1 trainer packet per second, 20 Hz control loop, like a real ride), then
writes:
  docs/stepping/<situation>.svg   static charts (render on GitHub)
  docs/STEPPING.md                page showing them with explanations
  docs/stepping.html              interactive version (tabs, step through, play); open in a browser

Re-run after changing the control logic so the diagrams stay true:
  .venv\\Scripts\\python tools\\stepping_diagrams.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bridge.drive import DriveConfig, SpeedController, VirtualBike  # noqa: E402
from bridge.limiter import LimitConfig, SpeedLimitPlanner  # noqa: E402
from bridge.mapper import ThrottleMapper  # noqa: E402

HZ = 20
DOCS = ROOT / "docs"


def simulate(power_at, seconds, stale_between=None):
    """[[t, watts, target_mph, limit_mph, throttle, state], ...] once per second."""
    vb = VirtualBike()
    ctl = SpeedController(DriveConfig(gear_ratio=2.0, top_speed_kmh=0))
    pl, mp = SpeedLimitPlanner(LimitConfig()), ThrottleMapper()
    out, drive_w, low_n, last_pkt, power, cad = [], 0.0, 0, -1, 0.0, 0.0
    for k in range(int(seconds * HZ)):
        t = k / HZ
        stale = stale_between and stale_between[0] <= t < stale_between[1]
        if int(t) != last_pkt and not stale:  # one trainer packet per second, like the KICKR
            last_pkt = int(t)
            power = power_at(int(t))
            cad = 85.0 if power > 0 else 0.0
            mp.add_sample(power, t)
            if power > 0 or cad < 20:
                drive_w = power
            low_n = low_n + 1 if power < 25 else 0
        active = not mp.is_stale(t)
        push = pl.push_factor(mp.smoothed) if active else 1.0
        ctl.set_bike_speed(vb.step(drive_w if active else 0.0, 1 / HZ) * push)
        target = ctl.step(1 / HZ, active).target_kmh
        pedalling = pl.is_pedalling(power, cad if low_n < 2 else 0)
        limit, thr = pl.update(target, active, pedalling, t, pushing=push > 1.05)
        if pl.state == "riding":
            thr = pl.push_throttle(thr, push)
        state = pl.state if active else "stale"
        if state == "riding" and push > 1.05:
            state = "push"
        if k % HZ == 0:
            out.append([round(t), int(power if active else 0), round(target / 1.609344, 1), limit,
                        round(thr, 2), state])
    return out


def power_steps(*parts):
    """parts: (until_second, watts), ... -> watts at second t."""
    def at(t):
        for until, w in parts:
            if t < until:
                return w
        return parts[-1][1]
    return at


SITUATIONS = [
    ("start", "Start riding",
     "Steady 150 W from a standstill. The virtual bike builds speed with inertia, so the limit climbs a "
     "step at a time and settles.",
     lambda: simulate(power_steps((3, 0), (60, 150)), 60)),
    ("push", "Push hard",
     "150 W, then a 450 W effort for 15 s, then back to 150 W. The push bonus raises the gear, steps the "
     "limit up early and opens the throttle.",
     lambda: simulate(power_steps((3, 0), (35, 150), (50, 450), (80, 150)), 80)),
    ("coast", "Stop pedalling",
     "Ride at 180 W, then stop. The limit freezes one step up with a light throttle (the game's gravity "
     "decides), then after 12 s eases down to a stop.",
     lambda: simulate(power_steps((3, 0), (35, 180), (80, 0)), 80)),
    ("resume", "Coast, then resume easy",
     "Coast for 8 s, then pedal again at only 110 W. The limit holds for 8 s while watts build, then "
     "steps down one notch at a time.",
     lambda: simulate(power_steps((3, 0), (35, 200), (43, 0), (90, 110)), 90)),
    ("ease", "Ease off gradually",
     "300 W, then 200 W, then 100 W. Downward steps are rate-limited, so the car never snaps to a lower "
     "speed.",
     lambda: simulate(power_steps((3, 0), (35, 300), (60, 200), (95, 100)), 95)),
    ("dropout", "Trainer drops out",
     "Bluetooth goes quiet for 10 s mid-ride. After 3 s without data the throttle is cut and the limit "
     "goes to the floor; riding resumes when data returns.",
     lambda: simulate(power_steps((3, 0), (80, 170)), 80, stale_between=(35, 45))),
]

STATES = {
    "stopped": "Stopped: throttle 0 and the limit at the 5 mph floor. Pedal to start.",
    "riding": "Riding: the limit follows your target in 5 mph steps and the game holds the car at it. "
              "Throttle stays at 0.6; the game caps the speed.",
    "push": "Push bonus: above 150 W the gear rises (up to x1.5 at 400 W), the limit steps up as soon as "
            "the target passes it, and the throttle opens toward 1.0.",
    "coasting": "Coasting: you stopped pedalling. The limit freezes one step above your last speed with a "
                "light 0.05 throttle, so the game's gravity decides: descents roll faster, climbs slow the car.",
    "releasing": "Releasing: 12 s without pedalling, so the limit eases down 5 mph every 4 s to a stop. "
                 "Throttle goes to 0 for the last 10 mph. Pedal to resume.",
    "stale": "No trainer data: after 3 s without a packet the throttle is cut and the limit goes to the "
             "floor. Riding resumes as soon as data returns.",
}
BAND = {"push": ("#EF9F27", "Push bonus"), "coasting": ("#1D9E75", "Coasting"),
        "releasing": ("#D85A30", "Releasing"), "stale": ("#E24B4A", "No trainer data")}
LIMIT_C, TARGET_C, POWER_C = "#534AB7", "#5F5E5A", "#B4B2A9"

# ---------- static SVG (GitHub renders these inline) ----------

W, H = 680, 348
L, R, T, SB, PT, PB = 48, 664, 40, 222, 246, 296
YMAX, PMAX = 85, 500


def svg(title, d):
    n = len(d)
    x = lambda i: L + (R - L) * i / (n - 1)  # noqa: E731
    y = lambda v: SB - (SB - T) * min(v, YMAX) / YMAX  # noqa: E731
    py = lambda w: PB - (PB - PT) * min(w, PMAX) / PMAX  # noqa: E731
    bw = (R - L) / (n - 1)
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'font-family="Segoe UI, Helvetica, Arial, sans-serif" role="img" aria-label="{title}">',
         f'<rect width="{W}" height="{H}" rx="8" fill="#FFFFFF"/>',
         f'<text x="{L}" y="24" font-size="15" font-weight="600" fill="#2C2C2A">{title}</text>']
    for i, r in enumerate(d):
        if r[5] in BAND:
            p.append(f'<rect x="{x(i) - bw / 2:.1f}" y="{T}" width="{bw + 0.5:.1f}" height="{PB - T}" '
                     f'fill="{BAND[r[5]][0]}" fill-opacity="0.16"/>')
    for v in range(0, 81, 20):
        p.append(f'<line x1="{L}" x2="{R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#D3D1C7" stroke-width="0.6"/>')
        p.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" fill="#5F5E5A">{v}</text>')
    p.append(f'<text x="{L - 6}" y="{T - 6}" text-anchor="end" font-size="11" fill="#5F5E5A">mph</text>')
    p.append(f'<line x1="{L}" x2="{R}" y1="{PB}" y2="{PB}" stroke="#D3D1C7" stroke-width="0.6"/>')
    p.append(f'<text x="{L - 6}" y="{PT + 10}" text-anchor="end" font-size="11" fill="#5F5E5A">W</text>')
    for i, r in enumerate(d):
        if r[1] > 0:
            p.append(f'<rect x="{x(i) - 2:.1f}" y="{py(r[1]):.1f}" width="4" height="{PB - py(r[1]):.1f}" fill="{POWER_C}"/>')
    tp = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f} {y(r[2]):.1f}" for i, r in enumerate(d))
    p.append(f'<path d="{tp}" fill="none" stroke="{TARGET_C}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    lp = f"M{x(0):.1f} {y(d[0][3]):.1f}" + "".join(f"H{x(i):.1f}V{y(r[3]):.1f}" for i, r in enumerate(d) if i)
    p.append(f'<path d="{lp}" fill="none" stroke="{LIMIT_C}" stroke-width="2.5"/>')
    for s in range(0, n, 10):
        p.append(f'<text x="{x(s):.1f}" y="{PB + 16}" text-anchor="middle" font-size="11" fill="#5F5E5A">{s} s</text>')
    # legend
    lx, ly = L, 334  # own row under the time axis: the top-right position ran off the edge
    items = [("line", LIMIT_C, "Game limit"), ("dash", TARGET_C, "Target"), ("bar", POWER_C, "Power")]
    items += [("band", c, name) for key, (c, name) in BAND.items() if any(r[5] == key for r in d)]
    for kind, color, name in items:
        if kind == "line":
            p.append(f'<line x1="{lx}" x2="{lx + 16}" y1="{ly - 4}" y2="{ly - 4}" stroke="{color}" stroke-width="2.5"/>')
        elif kind == "dash":
            p.append(f'<line x1="{lx}" x2="{lx + 16}" y1="{ly - 4}" y2="{ly - 4}" stroke="{color}" stroke-width="1.5" stroke-dasharray="4 3"/>')
        elif kind == "bar":
            p.append(f'<rect x="{lx + 5}" y="{ly - 10}" width="6" height="12" fill="{color}"/>')
        else:
            p.append(f'<rect x="{lx}" y="{ly - 10}" width="16" height="12" fill="{color}" fill-opacity="0.3"/>')
        p.append(f'<text x="{lx + 20}" y="{ly}" font-size="11" fill="#444441">{name}</text>')
        lx += 26 + 6.2 * len(name)
    p.append("</svg>")
    return "\n".join(p) + "\n"


# ---------- interactive HTML ----------

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Limit stepping</title>
<style>
:root{--bg:#fafaf8;--card:#f1efe8;--text:#2c2c2a;--muted:#5f5e5a;--line:#d3d1c7;--accent:#534AB7;--on:#eeedfe}
@media (prefers-color-scheme: dark){:root{--bg:#1e1e1c;--card:#2c2c2a;--text:#f1efe8;--muted:#b4b2a9;--line:#444441;--accent:#afa9ec;--on:#26215c}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:20px;font-weight:600;margin:4px 0 2px}.sub{color:var(--muted);font-size:13px;margin:0 0 12px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
button{font:13px inherit;font-family:inherit;padding:5px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
.tabs button.on{background:var(--on);border-color:var(--accent);color:var(--accent)}
.cap{font-size:13px;color:var(--muted);margin:0 0 6px}
svg{width:100%;height:auto;display:block}
.ctl{display:flex;gap:8px;align-items:center;margin-top:6px}.ctl input{flex:1}
.ro{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:8px}
.ro div{background:var(--card);border-radius:8px;padding:6px 8px}.ro span{display:block;font-size:11px;color:var(--muted)}.ro b{font-size:16px;font-weight:600}
.st{margin-top:8px;font-size:13px;padding:8px 10px;border-radius:8px;background:var(--card)}
.lg{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);margin-top:6px}.lg i{display:inline-block;width:14px;height:10px;border-radius:2px;margin-right:4px}
@media (max-width:520px){.ro{grid-template-columns:repeat(3,1fr)}}
</style></head><body><main>
<h1>How the game limit steps</h1>
<p class="sub">Simulated with slowroads-bridge's real logic (1 trainer packet per second, 20 Hz control loop). Generated by tools/stepping_diagrams.py.</p>
<div class="tabs" id="tabs"></div><p class="cap" id="cap"></p>
<svg id="ch" viewBox="0 0 680 300" role="img" aria-label="Limit and target speed over time, with power below"></svg>
<div class="lg"><span><i style="background:#7F77DD"></i>Game limit</span><span><i style="border-top:2px dashed #888780;height:0"></i>Target speed</span><span><i style="background:rgba(239,159,39,.4)"></i>Push bonus</span><span><i style="background:rgba(29,158,117,.4)"></i>Coasting</span><span><i style="background:rgba(216,90,48,.4)"></i>Releasing</span><span><i style="background:rgba(226,75,74,.4)"></i>No trainer data</span></div>
<div class="ctl"><button id="prev" aria-label="Previous second">&#9664;</button><button id="play">Play</button><button id="next" aria-label="Next second">&#9654;</button><input id="sl" type="range" min="0" max="59" value="0" aria-label="Time"></div>
<div class="ro"><div><span>Time</span><b id="rt"></b></div><div><span>Power</span><b id="rp"></b></div><div><span>Target</span><b id="rg"></b></div><div><span>Game limit</span><b id="rl"></b></div><div><span>Throttle</span><b id="rh"></b></div></div>
<div class="st" id="st"></div>
</main>
<script>
const S=__DATA__;const EXPL=__STATES__;
const BAND={push:"rgba(239,159,39,.18)",coasting:"rgba(29,158,117,.18)",releasing:"rgba(216,90,48,.18)",stale:"rgba(226,75,74,.18)"};
const NS="http://www.w3.org/2000/svg",ch=document.getElementById("ch");const L=44,R=664,T=12,SB=208,PT=232,PB=284,YMAX=85,PMAX=500;
let cur=Object.keys(S)[0],i=0,timer=null;
function el(n,a){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);ch.appendChild(e);return e}
function draw(){const d=S[cur].d,n=d.length,x=t=>L+(R-L)*t/(n-1),y=v=>SB-(SB-T)*Math.min(v,YMAX)/YMAX,py=w=>PB-(PB-PT)*Math.min(w,PMAX)/PMAX;ch.innerHTML="";
d.forEach((r,k)=>{if(BAND[r[5]])el("rect",{x:x(k)-(R-L)/(n-1)/2,y:T,width:(R-L)/(n-1)+0.5,height:PB-T,fill:BAND[r[5]]})});
for(let v=0;v<=80;v+=20){el("line",{x1:L,x2:R,y1:y(v),y2:y(v),stroke:"var(--line)","stroke-width":0.6});el("text",{x:L-6,y:y(v)+4,"text-anchor":"end","font-size":11,fill:"var(--muted)"}).textContent=v}
el("text",{x:L-6,y:T-2,"text-anchor":"end","font-size":11,fill:"var(--muted)"}).textContent="mph";el("line",{x1:L,x2:R,y1:PB,y2:PB,stroke:"var(--line)","stroke-width":0.6});
el("text",{x:L-6,y:PT+8,"text-anchor":"end","font-size":11,fill:"var(--muted)"}).textContent="W";
d.forEach((r,k)=>{const h=PB-py(r[1]);if(h>0)el("rect",{x:x(k)-2,y:py(r[1]),width:4,height:h,fill:"#B4B2A9"})});
let tp="";d.forEach((r,k)=>{tp+=(k?"L":"M")+x(k).toFixed(1)+" "+y(r[2]).toFixed(1)});el("path",{d:tp,fill:"none",stroke:"#888780","stroke-width":1.5,"stroke-dasharray":"4 3"});
let lp="M"+x(0)+" "+y(d[0][3]);for(let k=1;k<n;k++)lp+="H"+x(k).toFixed(1)+"V"+y(d[k][3]).toFixed(1);el("path",{d:lp,fill:"none",stroke:"#7F77DD","stroke-width":2.5});
for(let s=0;s<n;s+=10)el("text",{x:x(s),y:PB+14,"text-anchor":"middle","font-size":11,fill:"var(--muted)"}).textContent=s+" s";
el("line",{id:"cursor",x1:x(i),x2:x(i),y1:T,y2:PB,stroke:"var(--text)","stroke-width":1});el("circle",{id:"dot",cx:x(i),cy:y(d[i][3]),r:4.5,fill:"#7F77DD",stroke:"var(--bg)","stroke-width":1.5});upd()}
function upd(){const d=S[cur].d,n=d.length,r=d[i],x=L+(R-L)*i/(n-1),yv=SB-(SB-T)*Math.min(r[3],YMAX)/YMAX,c=document.getElementById("cursor"),dt=document.getElementById("dot");
if(c){c.setAttribute("x1",x);c.setAttribute("x2",x);dt.setAttribute("cx",x);dt.setAttribute("cy",yv)}document.getElementById("sl").value=i;
rt.textContent=r[0]+" s";rp.textContent=r[1]+" W";rg.textContent=r[2].toFixed(1)+" mph";rl.textContent=r[3]+" mph";rh.textContent=Math.round(r[4]*100)+"%";st.textContent=EXPL[r[5]]}
function stop(){if(timer){clearInterval(timer);timer=null}play.textContent="Play"}
function pick(k){cur=k;i=0;stop();sl.max=S[k].d.length-1;cap.textContent=S[k].desc;document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.k===k));draw()}
Object.keys(S).forEach(k=>{const b=document.createElement("button");b.textContent=S[k].title;b.dataset.k=k;b.onclick=()=>pick(k);tabs.appendChild(b)});
sl.oninput=e=>{stop();i=+e.target.value;upd()};prev.onclick=()=>{stop();i=Math.max(0,i-1);upd()};next.onclick=()=>{stop();i=Math.min(S[cur].d.length-1,i+1);upd()};
play.onclick=()=>{if(timer){stop();return}if(i>=S[cur].d.length-1)i=0;play.textContent="Pause";timer=setInterval(()=>{if(i>=S[cur].d.length-1){stop();return}i++;upd()},125)};
pick(cur);
</script></body></html>
"""


def main() -> int:
    out = DOCS / "stepping"
    out.mkdir(parents=True, exist_ok=True)
    data = {}
    md = ["# How the game limit steps", "",
          "Each chart runs one riding situation through the bridge's real logic: 1 trainer packet per "
          "second, 20 Hz control loop, virtual bike at gear 2.0, default push bonus and coasting. The "
          "**purple steps** are the speed limit the bridge sets in Slow Roads (the game holds the car "
          "there), the **dashed line** is your target speed, grey bars are power, and shaded bands mark "
          "push bonus, coasting, releasing and trainer dropouts.", "",
          "**Interactive version** (step through second by second, or play): "
          "<https://jomarpueyo.github.io/slowroads-bridge/stepping.html> (GitHub Pages), or download "
          "[`stepping.html`](stepping.html) and open it in a browser.", "",
          "Regenerate after changing the control logic: `.venv\\Scripts\\python tools\\stepping_diagrams.py`.", ""]
    for key, title, desc, run in SITUATIONS:
        d = run()
        data[key] = {"title": title, "desc": desc, "d": d}
        (out / f"{key}.svg").write_text(svg(title, d), encoding="utf-8")
        md += [f"## {title}", "", desc, "", f"![{title}](stepping/{key}.svg)", ""]
    md += ["## What each state means", "", "| State | What the bridge does |", "| --- | --- |"]
    md += [f"| {k} | {v.split(': ', 1)[1]} |" for k, v in STATES.items()]
    (DOCS / "STEPPING.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    html = HTML.replace("__DATA__", json.dumps(data, separators=(",", ":"))).replace(
        "__STATES__", json.dumps(STATES, separators=(",", ":")))
    (DOCS / "stepping.html").write_text(html, encoding="utf-8")
    print(f"wrote {len(SITUATIONS)} SVGs to {out}, docs/STEPPING.md and docs/stepping.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
