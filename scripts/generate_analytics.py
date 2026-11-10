#!/usr/bin/env python3
"""
AELIOS SYSTEMS — self-hosted analytics card generator.

Pulls live metrics straight from the GitHub GraphQL API using GITHUB_TOKEN
(no third-party image service, no public-instance rate limits) and renders
on-brand SVG cards into ./generated/ :

    generated/stats.svg       metric grid (commits, repos, stars, followers, PRs, issues)
    generated/languages.svg   top languages by bytes, or a "NO SIGNAL" panel when empty
    generated/activity.svg    contribution heatmap + streak telemetry

Run locally:
    GITHUB_TOKEN=xxx python3 scripts/generate_analytics.py

Only the Python standard library is used.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com/graphql"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generated")

# ---------------------------------------------------------------- brand tokens
BG = "#090d16"
CYAN = "#00f2fe"
BLUE = "#4facfe"
MAGENTA = "#f093fb"
GREEN = "#00ff88"
MUTED = "#64748b"
TEXT = "#e2e8f0"
DIM = "#94a3b8"

# 5-step contribution ramp: index 0 = zero contributions, then low -> high
HEAT_RAMP = ["#101826", "#0a3d4d", "#0e6a86", "#22b8d8", "#f093fb"]

QUERY = """
query ($login: String!) {
  user(login: $login) {
    login
    name
    createdAt
    followers { totalCount }
    pullRequests(first: 1) { totalCount }
    issues(first: 1) { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name
        stargazerCount
        forkCount
        isEmpty
        defaultBranchRef { target { ... on Commit { history { totalCount } } } }
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { contributionCount date } }
      }
    }
  }
}
"""


def fetch(login: str, token: str) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": "aelios-analytics-generator",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise SystemExit(f"GraphQL errors: {json.dumps(payload['errors'], indent=2)}")
    return payload["data"]["user"]


# ------------------------------------------------------------------- analytics
def analyse(user: dict) -> dict:
    repos = user["repositories"]["nodes"] or []
    cc = user["contributionsCollection"]
    cal = cc["contributionCalendar"]

    days = [d for w in (cal.get("weeks") or []) for d in (w.get("contributionDays") or [])]

    commits = 0
    for r in repos:
        ref = r.get("defaultBranchRef") or {}
        tgt = ref.get("target") or {}
        hist = tgt.get("history") or {}
        commits += hist.get("totalCount") or 0

    stars = sum(r.get("stargazerCount") or 0 for r in repos)
    forks = sum(r.get("forkCount") or 0 for r in repos)

    # aggregate languages by byte size across all repos
    langs: dict[str, dict] = {}
    for r in repos:
        for edge in (r.get("languages") or {}).get("edges") or []:
            node = edge.get("node") or {}
            name = node.get("name") or "?"
            entry = langs.setdefault(name, {"bytes": 0, "color": node.get("color") or CYAN})
            entry["bytes"] += edge.get("size") or 0
    total_bytes = sum(v["bytes"] for v in langs.values())
    top = sorted(langs.items(), key=lambda kv: kv[1]["bytes"], reverse=True)[:6]
    top_langs = [
        {
            "name": n,
            "bytes": v["bytes"],
            "color": v["color"],
            "pct": (v["bytes"] / total_bytes * 100.0) if total_bytes else 0.0,
        }
        for n, v in top
    ]

    # streaks over the trailing contribution calendar
    today = dt.date.today()
    by_date: dict[dt.date, int] = {}
    for d in days:
        try:
            by_date[dt.date.fromisoformat(d["date"])] = d.get("contributionCount") or 0
        except (KeyError, ValueError):
            continue

    def active(day: dt.date) -> bool:
        return by_date.get(day, 0) > 0

    current = 0
    cursor = today
    if not active(cursor):  # today may not be over yet — start from yesterday
        cursor = today - dt.timedelta(days=1)
    while active(cursor):
        current += 1
        cursor -= dt.timedelta(days=1)

    longest = run = 0
    for day in sorted(by_date):
        if by_date[day] > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    best_day = max((d.get("contributionCount") or 0 for d in days), default=0)

    return {
        "login": user["login"],
        "commits": commits,
        "commits_year": cc.get("totalCommitContributions") or 0,
        "repos": user["repositories"]["totalCount"] or 0,
        "stars": stars,
        "forks": forks,
        "followers": (user.get("followers") or {}).get("totalCount") or 0,
        "prs": (user.get("pullRequests") or {}).get("totalCount") or 0,
        "issues": (user.get("issues") or {}).get("totalCount") or 0,
        "total_year": cal.get("totalContributions") or 0,
        "current_streak": current,
        "longest_streak": longest,
        "best_day": best_day,
        "langs": top_langs,
        "lang_bytes": total_bytes,
        "calendar": days,
        "joined": (user.get("createdAt") or "")[:10],
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ---------------------------------------------------------------- svg scaffolding
CSS = """
    .bg { fill: __BG__; }
    .card { fill: url(#card-bg); stroke: url(#cyber-grad); stroke-width: 1.2; rx: 16px; }
    .ttl { font-family: 'Fira Code','Courier New',monospace; font-size: 12px; fill: __CYAN__;
           letter-spacing: 3px; }
    .lbl { font-family: 'Fira Code','Courier New',monospace; font-size: 10px; fill: __MUTED__;
           letter-spacing: 1.5px; }
    .val { font-family: 'Segoe UI',-apple-system,BlinkMacSystemFont,sans-serif; font-size: 27px;
           font-weight: 800; fill: url(#cyber-grad); }
    .val-g { font-family: 'Segoe UI',sans-serif; font-size: 27px; font-weight: 800; fill: __GREEN__; }
    .tiny { font-family: 'Fira Code','Courier New',monospace; font-size: 9px; fill: __MUTED__;
            letter-spacing: 1px; }
    .dot { fill: __GREEN__; animation: pulse 2s infinite; }
    .fade { opacity: 0; animation: fadeIn .8s ease-out .15s forwards; }
    @keyframes fadeIn { to { opacity: 1; } }
    @keyframes pulse { 0%{r:3px;opacity:1} 50%{r:5px;opacity:.35} 100%{r:3px;opacity:1} }
    @keyframes scan { 0%{transform:translateX(0)} 100%{transform:translateX(300px)} }
    .scan { animation: scan 3s linear infinite; }
"""


def defs() -> str:
    return f"""
  <defs>
    <linearGradient id="cyber-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{CYAN}"/>
      <stop offset="50%" stop-color="{BLUE}"/>
      <stop offset="100%" stop-color="{MAGENTA}"/>
    </linearGradient>
    <linearGradient id="card-bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.07"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0.02"/>
    </linearGradient>
    <pattern id="grid" width="26" height="26" patternUnits="userSpaceOnUse">
      <path d="M 26 0 L 0 0 0 26" fill="none" stroke="#ffffff" stroke-width="0.5" stroke-opacity="0.05"/>
    </pattern>
    <filter id="soft" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="26"/>
    </filter>
  </defs>"""


def css() -> str:
    body = (CSS.replace("__BG__", BG).replace("__CYAN__", CYAN).replace("__MUTED__", MUTED)
               .replace("__GREEN__", GREEN))
    return f"  <style>{body}  </style>"


def shell(w: int, h: int, title: str, inner: str, orbs: str = "") -> str:
    return f"""<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(title)}">
  <title>{html.escape(title)}</title>
{defs()}
{css()}
  <rect width="{w}" height="{h}" rx="20" class="bg"/>
  <rect width="{w}" height="{h}" rx="20" fill="url(#grid)"/>
  <g filter="url(#soft)" opacity="0.55">{orbs}</g>
  <rect x="12" y="12" width="{w - 24}" height="{h - 24}" class="card"/>
{inner}
</svg>
"""


def header(x: int, y: int, label: str, stamp: str, w: int) -> str:
    return (
        f'  <circle cx="{x}" cy="{y - 4}" r="3" class="dot"/>\n'
        f'  <text x="{x + 12}" y="{y}" class="ttl">{html.escape(label)}</text>\n'
        f'  <text x="{w - 32}" y="{y}" class="tiny" text-anchor="end">{html.escape(stamp)}</text>\n'
        f'  <rect x="{x}" y="{y + 10}" width="{w - x - 32}" height="1" fill="url(#cyber-grad)" opacity="0.35"/>\n'
    )


def esc(s) -> str:
    return html.escape(str(s))


def n(v: int) -> str:
    return f"{v:,}".replace(",", " ")


# ------------------------------------------------------------------- stats.svg
def stats_svg(d: dict) -> str:
    W, H = 460, 232
    metrics = [
        ("COMMITS", d["commits"], False),
        ("REPOSITORIES", d["repos"], False),
        ("STARS EARNED", d["stars"], False),
        ("FOLLOWERS", d["followers"], False),
        ("PULL REQUESTS", d["prs"], False),
        ("ISSUES", d["issues"], False),
    ]
    out = [header(32, 46, "AELIOS // STATS", f"@{d['login']} · {d['generated']}", W)]
    for i, (label, value, good) in enumerate(metrics):
        col, row = i % 2, i // 2
        x, y = 34 + col * 208, 96 + row * 46
        cls = "val-g" if good else "val"
        out.append(f'  <text x="{x}" y="{y - 16}" class="lbl">{esc(label)}</text>')
        out.append(f'  <text x="{x}" y="{y + 8}" class="{cls}">{esc(n(value))}</text>')
    out.append(f'  <text x="34" y="{H - 26}" class="tiny">SRC: GITHUB GRAPHQL · TOKEN-AUTHENTICATED · NO RATE LIMIT</text>')
    orbs = f'<circle cx="90" cy="60" r="46" fill="{CYAN}"/><circle cx="390" cy="190" r="52" fill="{MAGENTA}"/>'
    return shell(W, H, "RAK-AeliosSystems statistics", "\n".join(out), orbs)


# --------------------------------------------------------------- languages.svg
def languages_svg(d: dict) -> str:
    W, H = 460, 232
    out = [header(32, 46, "AELIOS // LANGUAGES", f"{len(d['langs'])} detected · {d['generated']}", W)]
    if not d["langs"]:
        out.append(f'  <text x="{W // 2}" y="118" class="val" text-anchor="middle">NO SIGNAL</text>')
        out.append(f'  <text x="{W // 2}" y="144" class="lbl" text-anchor="middle">AWAITING FIRST CODE DROP</text>')
        out.append(f'  <rect x="60" y="160" width="340" height="2" rx="1" fill="{CYAN}" opacity="0.18"/>')
        out.append(f'  <g class="scan"><rect x="60" y="158" width="60" height="6" rx="3" fill="{CYAN}" opacity="0.75"/></g>')
        out.append(f'  <text x="{W // 2}" y="190" class="tiny" text-anchor="middle">LINGUIST RETURNS {{}} — MARKDOWN + SVG ARE NOT COUNTED</text>')
    else:
        top = d["langs"]
        bar_x, bar_w = 150, 250
        for i, lg in enumerate(top):
            y = 84 + i * 21
            w = max(3.0, bar_w * lg["pct"] / 100.0)
            out.append(f'  <text x="34" y="{y + 4}" class="lbl">{esc(lg["name"].upper())}</text>')
            out.append(f'  <rect x="{bar_x}" y="{y - 6}" width="{bar_w}" height="9" rx="4.5" fill="#ffffff" opacity="0.06"/>')
            out.append(f'  <rect x="{bar_x}" y="{y - 6}" width="{w:.1f}" height="9" rx="4.5" fill="{esc(lg["color"])}"/>')
            out.append(f'  <text x="{W - 32}" y="{y + 4}" class="lbl" text-anchor="end">{lg["pct"]:.1f}%</text>')
        kb = d["lang_bytes"] / 1024.0
        unit, val = ("MB", kb / 1024.0) if kb >= 1024 else ("KB", kb)
        out.append(f'  <text x="34" y="{H - 26}" class="tiny">TOTAL INDEXED CODE: {val:.1f} {unit}</text>')
    orbs = f'<circle cx="380" cy="70" r="48" fill="{BLUE}"/><circle cx="80" cy="185" r="44" fill="{MAGENTA}"/>'
    return shell(W, H, "RAK-AeliosSystems languages", "\n".join(out), orbs)


# ---------------------------------------------------------------- activity.svg
def activity_svg(d: dict) -> str:
    W, H = 940, 244
    out = [header(32, 46, "AELIOS // ACTIVITY", f"trailing 12 months · {d['generated']}", W)]

    days = d["calendar"]
    # rebuild week columns exactly as returned by the API
    cal_weeks = []
    idx = 0
    per_week = 7
    while idx < len(days):
        cal_weeks.append(days[idx:idx + per_week])
        idx += per_week

    ox, oy, pitch, cell = 34, 74, 13, 11
    month_labels: list[tuple[int, str]] = []
    last_month = None
    for wi, week in enumerate(cal_weeks):
        for di, day in enumerate(week):
            count = day.get("contributionCount") or 0
            level = 0
            if count > 0:
                level = 1 if count <= 2 else 2 if count <= 5 else 3 if count <= 9 else 4
            x = ox + wi * pitch
            y = oy + di * pitch
            out.append(f'  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" fill="{HEAT_RAMP[level]}"/>')
            if di == 0:
                m = (day.get("date") or "")[:7]
                if m and m != last_month:
                    month_labels.append((x, m))
                    last_month = m

    names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    for x, m in month_labels:
        try:
            label = names[int(m[5:7]) - 1]
        except (ValueError, IndexError):
            continue
        if x < W - 250:
            out.append(f'  <text x="{x}" y="{oy - 6}" class="tiny">{label}</text>')

    hx_end = ox + len(cal_weeks) * pitch
    rx = max(hx_end + 28, W - 200)
    for i, (label, value, good) in enumerate([
        ("CURRENT STREAK", f"{d['current_streak']}d", True),
        ("LONGEST STREAK", f"{d['longest_streak']}d", False),
        ("TOTAL (12M)", n(d["total_year"]), False),
        ("BEST DAY", n(d["best_day"]), False),
    ]):
        y = 84 + i * 34
        out.append(f'  <text x="{rx}" y="{y}" class="lbl">{esc(label)}</text>')
        cls = "val-g" if good else "val"
        out.append(f'  <text x="{rx + 150}" y="{y + 4}" class="{cls}" text-anchor="end" font-size="20">{esc(value)}</text>')

    ly = H - 30
    out.append(f'  <text x="34" y="{ly}" class="tiny">LESS</text>')
    for i, c in enumerate(HEAT_RAMP):
        out.append(f'  <rect x="{70 + i * 16}" y="{ly - 8}" width="11" height="11" rx="2.5" fill="{c}"/>')
    out.append(f'  <text x="{70 + len(HEAT_RAMP) * 16 + 4}" y="{ly}" class="tiny">MORE</text>')
    out.append(f'  <text x="{W - 32}" y="{ly}" class="tiny" text-anchor="end">JOINED {esc(d["joined"])} · {esc(d["commits_year"])} COMMITS THIS YEAR</text>')

    orbs = (f'<circle cx="140" cy="60" r="52" fill="{CYAN}"/>'
            f'<circle cx="820" cy="200" r="58" fill="{MAGENTA}"/>'
            f'<circle cx="520" cy="140" r="40" fill="{BLUE}"/>')
    return shell(W, H, "RAK-AeliosSystems activity", "\n".join(out), orbs)


# ------------------------------------------------------------------------ main
def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    login = os.environ.get("GITHUB_LOGIN") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "Rak8fx"
    if not token:
        print("error: GITHUB_TOKEN is not set", file=sys.stderr)
        return 1

    print(f"[aelios] querying GraphQL for @{login} ...")
    user = fetch(login, token)
    d = analyse(user)

    os.makedirs(OUT_DIR, exist_ok=True)
    cards = {
        "stats.svg": stats_svg(d),
        "languages.svg": languages_svg(d),
        "activity.svg": activity_svg(d),
    }
    for name, svg in cards.items():
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"[aelios] wrote {os.path.relpath(path)}  ({len(svg):,} bytes)")

    print("[aelios] metrics: " + ", ".join(
        f"{k}={d[k]}" for k in ("commits", "repos", "stars", "followers", "prs", "issues",
                                "total_year", "current_streak", "longest_streak")
    ) + f", languages={len(d['langs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
