"""Render stats into ONE self-contained HTML file (no CDN, no external requests).

Charts are inline SVG generated here; the only script is a few lines for the
theme and privacy toggles. Every derived number states its formula on the page.
"""
import datetime
import hashlib
import html
import os

from . import DISPLAY_NAME, __version__
from .prices import estimate, load_prices

SERIES = [  # draw order bottom -> top
    ("cache_read", "Cache read"),
    ("cache_creation", "Cache write"),
    ("input", "Input"),
    ("output", "Output"),
]


def esc(s):
    return html.escape(str(s), quote=True)


def compact(n):
    n = float(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return ("%.2f" % v if v < 10 else "%.1f" % v if v < 100 else "%.0f" % v) + suf
    return "%d" % n


def full(n):
    return f"{int(n):,}"


def project_label(cwd):
    base = os.path.basename(cwd.rstrip("/\\")) or cwd
    return base


def project_hash(cwd):
    return "project-" + hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:8]


def streaks(days, today=None):
    """(current, longest) runs of consecutive active days. The current streak counts
    back from today, or from yesterday if today has no activity yet."""
    active = sorted(d for d, v in days.items() if v.get("tokens") or v.get("messages"))
    if not active:
        return 0, 0
    dates = [datetime.date.fromisoformat(d) for d in active]
    longest = run = 1
    for a, b in zip(dates, dates[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        longest = max(longest, run)
    today = today or datetime.date.today()
    s = set(dates)
    cur_day = today if today in s else today - datetime.timedelta(days=1)
    current = 0
    while cur_day in s:
        current += 1
        cur_day -= datetime.timedelta(days=1)
    return current, longest


def cache_share(t):
    denom = t["input"] + t["cache_read"] + t["cache_creation"]
    return (100.0 * t["cache_read"] / denom) if denom else 0.0


ICON = {
    "logo": '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 9h8M8 13h8M8 17h5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    "theme": '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    "eye": '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
}


def daily_chart(days, n_days=90, today=None):
    last = max(days) if days else (today or datetime.date.today()).isoformat()
    end = datetime.date.fromisoformat(last)
    span = [(end - datetime.timedelta(days=i)).isoformat() for i in range(n_days - 1, -1, -1)]
    vals = [days.get(d, {}) for d in span]
    top = max([sum(v.get(k, 0) for k, _ in SERIES) for v in vals] + [1])
    # round the axis max up to a 1/2/5 step
    mag = 10 ** (len(str(int(top))) - 1)
    axis = next(m * mag for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if m * mag >= top)
    W, H, L, B, T = 960, 280, 56, 28, 12
    pw, ph = W - L - 8, H - B - T
    bw = pw / len(span)
    out = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="Tokens per day, last %d days">' % (W, H, n_days)]
    for i in range(5):
        v = axis * i / 4
        y = T + ph - ph * i / 4
        out.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (L, W - 8, y, y))
        out.append('<text class="ax" x="%d" y="%.1f" text-anchor="end">%s</text>' % (L - 8, y + 4, compact(v)))
    for i, (d, v) in enumerate(zip(span, vals)):
        x = L + i * bw
        y = T + ph
        tip = "%s: %s tokens" % (d, full(sum(v.get(k, 0) for k, _ in SERIES)))
        out.append('<g><title>%s</title>' % esc(tip))
        for key, _ in SERIES:
            h = ph * v.get(key, 0) / axis
            if h <= 0:
                continue
            y -= h
            out.append('<rect class="s-%s" x="%.2f" y="%.2f" width="%.2f" height="%.2f"/>'
                       % (key, x + bw * 0.12, y, max(bw * 0.76, 0.8), h))
        out.append('</g>')
        if d.endswith("-01") or i == 0:
            label = datetime.date.fromisoformat(d).strftime("%b %d" if i == 0 else "%b")
            out.append('<text class="ax" x="%.1f" y="%d">%s</text>' % (x, H - 8, esc(label)))
    out.append('</svg>')
    return "\n".join(out)


def mix_bar(t):
    total = sum(t[k] for k, _ in SERIES) or 1
    segs, rows = [], []
    for key, name in reversed(SERIES):
        pct = 100.0 * t[key] / total
        segs.append('<span class="s-%s" style="width:%.3f%%"></span>' % (key, pct))
        rows.append('<tr><td><i class="sw s-%s"></i>%s</td><td class="num">%s</td><td class="num">%.1f%%</td></tr>'
                    % (key, name, full(t[key]), pct))
    rows.append('<tr class="sum"><td>Total</td><td class="num">%s</td><td class="num">100%%</td></tr>' % full(total))
    return '<div class="mix">%s</div><table class="tbl"><thead><tr><th>Kind</th><th class="num">Tokens</th><th class="num">Share</th></tr></thead><tbody>%s</tbody></table>' % ("".join(segs), "".join(rows))


def render(stats, private=False, show_cost=False, price_file=None, n_days=90, today=None):
    t = stats["totals"]
    days = stats.get("days", {})
    cur, longest = streaks(days, today)
    share = cache_share(t)

    kpis = [
        ("Total tokens", compact(t["tokens"]), full(t["tokens"]) + " exact"),
        ("Sessions", full(t["sessions"]), "distinct session ids"),
        ("Messages", full(t["messages"]), "%s prompts typed" % full(t.get("prompts", 0))),
        ("Days active", full(t["days_active"]), "%s to %s" % (t.get("first_day") or "-", t.get("last_day") or "-")),
        ("Cache-hit share", "%.1f%%" % share, "of prompt tokens served from cache"),
        ("Streak", "%d day%s" % (cur, "" if cur == 1 else "s"), "longest %d" % longest),
    ]
    kpi_html = "".join('<div class="kpi"><div class="k">%s</div><div class="v">%s</div><div class="s">%s</div></div>'
                       % (esc(a), esc(b), esc(c)) for a, b, c in kpis)

    model_total = sum(m["tokens"] for m in stats.get("models", {}).values()) or 1
    model_rows = "".join(
        '<tr><td class="mono">%s</td><td class="num">%s</td><td class="num">%.1f%%</td><td class="num">%s</td></tr>'
        % (esc(name), full(m["tokens"]), 100.0 * m["tokens"] / model_total, full(m["messages"]))
        for name, m in stats.get("models", {}).items())

    proj_rows = []
    for cwd, p in list(stats.get("projects", {}).items())[:20]:
        hashed = project_hash(cwd)
        name = hashed if private else project_label(cwd)
        proj_rows.append('<tr><td class="mono"><span class="pn" data-h="%s" data-n="%s">%s</span></td>'
                         '<td class="num">%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td></tr>'
                         % (esc(hashed), esc(name), esc(hashed), full(p["sessions"]), full(p["messages"]),
                            full(p["tokens"]), esc(p.get("last_day") or "-")))

    cost_html = ""
    if show_cost:
        est = estimate(stats.get("models", {}), load_prices(price_file))
        rows = "".join('<tr><td class="mono">%s</td><td class="num">$%s</td></tr>' % (esc(m), f"{v:,.2f}")
                       for m, v in est["by_model"].items())
        cost_html = ('<section class="card"><h2>Cost estimate</h2><p class="note">%s. Computed as tokens x '
                     'per-million list price for each kind, summed over models. %s tokens had no matching '
                     'price rule and are excluded.</p><table class="tbl"><thead><tr><th>Model</th><th class="num">USD</th>'
                     '</tr></thead><tbody>%s<tr class="sum"><td>Total</td><td class="num">$%s</td></tr></tbody></table></section>'
                     % (esc(est["label"].capitalize()), full(est["unpriced_tokens"]), rows, f"{est['total_usd']:,.2f}"))

    toggle = "" if private else ('<button type="button" id="priv" aria-pressed="false" title="Show or hide project names">'
                                 '%s<span>Show names</span></button>' % ICON["eye"])
    legend = "".join('<span><i class="sw s-%s"></i>%s</span>' % (k, n) for k, n in reversed(SERIES))
    gen = stats.get("generated_at", "")
    try:
        gen = datetime.datetime.fromisoformat(gen).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        pass

    return TEMPLATE.format(
        title=esc(DISPLAY_NAME), logo=ICON["logo"], theme_icon=ICON["theme"], toggle=toggle,
        span=esc("%s to %s" % (t.get("first_day") or "-", t.get("last_day") or "-")),
        generated=esc(gen), kpis=kpi_html, chart=daily_chart(days, n_days, today), legend=legend,
        n_days=n_days, mix=mix_bar(t), model_rows=model_rows or '<tr><td colspan="4">No data</td></tr>',
        proj_rows="".join(proj_rows) or '<tr><td colspan="5">No data</td></tr>',
        privacy_note=("Project names are hashed in this file and cannot be revealed."
                      if private else "Project names are hashed on screen by default; the toggle reveals the folder name only."),
        cost=cost_html, version=esc(__version__), css=CSS, js=JS,
    )


def write(stats, path, **kw):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(render(stats, **kw))
    os.replace(path + ".tmp", path)
    return path


CSS = """
:root{--bg:#f7f6f2;--paper:#ffffff;--ink:#1b1d22;--muted:#5d6370;--line:#e4e1d8;--accent:#2b4c8c;
--s-cache_read:#c9d4ea;--s-cache_creation:#93a8d3;--s-input:#4f6fae;--s-output:#1d3466;--grid:#ebe8e0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121317;--paper:#1a1c21;--ink:#ecebe6;
--muted:#9aa0ab;--line:#2c2f36;--accent:#8fb0ec;--s-cache_read:#2c3a57;--s-cache_creation:#45598a;
--s-input:#6f8fd0;--s-output:#b8cdf5;--grid:#24272d}}
:root[data-theme="dark"]{--bg:#121317;--paper:#1a1c21;--ink:#ecebe6;--muted:#9aa0ab;--line:#2c2f36;--accent:#8fb0ec;
--s-cache_read:#2c3a57;--s-cache_creation:#45598a;--s-input:#6f8fd0;--s-output:#b8cdf5;--grid:#24272d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",
Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:32px 16px 48px}
header{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;justify-content:space-between;
border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:24px}
.brand{display:flex;gap:10px;align-items:center;color:var(--accent)}
h1{font:600 30px/1.1 "Iowan Old Style","Palatino Linotype",Georgia,serif;margin:0;color:var(--ink);letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin-top:6px}
.ctl{display:flex;gap:8px}
button{display:inline-flex;gap:6px;align-items:center;font:inherit;font-size:13px;color:var(--ink);
background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:7px 11px;cursor:pointer}
button:hover{border-color:var(--accent)}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(150px,100%),1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.kpi .k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.kpi .v{font:600 28px/1.2 "Iowan Old Style","Palatino Linotype",Georgia,serif;margin:6px 0 2px;
font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.kpi .s{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:20px;min-width:0}
h2{font:600 19px/1.3 "Iowan Old Style","Palatino Linotype",Georgia,serif;margin:0 0 4px}
.note{color:var(--muted);font-size:13px;margin:0 0 14px}
.chart{width:100%;height:auto;display:block}
.grid{stroke:var(--grid);stroke-width:1}
.ax{fill:var(--muted);font-size:11px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin-top:8px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.s-cache_read{fill:var(--s-cache_read);background:var(--s-cache_read)}
.s-cache_creation{fill:var(--s-cache_creation);background:var(--s-cache_creation)}
.s-input{fill:var(--s-input);background:var(--s-input)}
.s-output{fill:var(--s-output);background:var(--s-output)}
.mix{display:flex;height:14px;border-radius:7px;overflow:hidden;margin:4px 0 14px;background:var(--grid)}
.mix span{display:block;height:100%}
.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}
@media (max-width:760px){.two{grid-template-columns:minmax(0,1fr)}}
.scroll{overflow-x:auto}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em;
border-bottom:1px solid var(--line);padding:8px 8px}
.tbl td{border-bottom:1px solid var(--line);padding:8px 8px;vertical-align:top}
.tbl tr:last-child td{border-bottom:0}
.tbl .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tbl .sum td{font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;overflow-wrap:anywhere}
footer{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:16px}
footer dt{font-weight:600;color:var(--ink);margin-top:8px}
footer dd{margin:2px 0 0}
"""

JS = """
(function(){var r=document.documentElement,h=(location.hash||'').slice(1);
if(h==='dark'||h==='light'){r.setAttribute('data-theme',h);}
document.getElementById('theme').addEventListener('click',function(){
var dark=r.getAttribute('data-theme')?r.getAttribute('data-theme')==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
r.setAttribute('data-theme',dark?'light':'dark');});
var b=document.getElementById('priv');if(!b)return;
b.addEventListener('click',function(){var on=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',on);
b.querySelector('span').textContent=on?'Hide names':'Show names';
document.querySelectorAll('.pn').forEach(function(e){e.textContent=on?e.getAttribute('data-n'):e.getAttribute('data-h');});});
})();
"""

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<title>{title} Report</title><style>{css}</style></head>
<body><div class="wrap">
<header><div><div class="brand">{logo}<h1>{title}</h1></div>
<div class="sub">Usage report, {span}. Generated {generated}.</div></div>
<div class="ctl"><button type="button" id="theme" title="Switch light or dark">{theme_icon}<span>Theme</span></button>{toggle}</div></header>
<section class="kpis">{kpis}</section>
<section class="card"><h2>Tokens per day</h2><p class="note">Last {n_days} days ending on the most recent active day. Hover a bar for the exact total.</p>
{chart}<div class="legend">{legend}</div></section>
<div class="two">
<section class="card"><h2>Token mix</h2><p class="note">All recorded usage, by kind.</p>{mix}</section>
<section class="card"><h2>Models</h2><p class="note">Share of all tokens per model id.</p><div class="scroll"><table class="tbl">
<thead><tr><th>Model</th><th class="num">Tokens</th><th class="num">Share</th><th class="num">Replies</th></tr></thead>
<tbody>{model_rows}</tbody></table></div></section></div>
<section class="card"><h2>Projects</h2><p class="note">Top 20 working directories by tokens. {privacy_note}</p><div class="scroll"><table class="tbl">
<thead><tr><th>Project</th><th class="num">Sessions</th><th class="num">Messages</th><th class="num">Tokens</th><th class="num">Last active</th></tr></thead>
<tbody>{proj_rows}</tbody></table></div></section>
{cost}
<footer><dl>
<dt>Total tokens</dt><dd>input + output + cache read + cache write, summed over every assistant reply, de-duplicated by message id.</dd>
<dt>Cache-hit share</dt><dd>cache read / (input + cache read + cache write): the part of each prompt served from the prompt cache.</dd>
<dt>Messages</dt><dd>distinct user records (by uuid) + distinct assistant replies (by message id). Prompts are user records typed by a person.</dd>
<dt>Streak</dt><dd>consecutive days with any activity, counted back from today (or yesterday if today has none yet).</dd>
</dl><p>{title} {version}. Built from local transcripts; this file makes no network requests.</p></footer>
</div><script>{js}</script></body></html>
"""
