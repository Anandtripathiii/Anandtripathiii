#!/usr/bin/env python3
"""
Generate a custom contribution-stats SVG for a GitHub user.
Runs in GitHub Actions. Writes metrics/contribution-city.svg.

Usage:  python3 tools/profile.py <github-username>
Env:    GITHUB_TOKEN  (automatically provided in Actions)
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta


# ---------- GitHub GraphQL ----------

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

def fetch_calendar(login: str, token: str):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = []
    for wk in cal["weeks"]:
        for d in wk["contributionDays"]:
            days.append({"date": d["date"], "count": d["contributionCount"]})
    return cal["totalContributions"], days


# ---------- Stats ----------

def compute_stats(days):
    """days: sorted ascending by date. Returns dict of stats."""
    days = sorted(days, key=lambda d: d["date"])
    total = sum(d["count"] for d in days)
    active = sum(1 for d in days if d["count"] > 0)

    # best day
    best = max(days, key=lambda d: d["count"])
    best_day_date = datetime.fromisoformat(best["date"]).strftime("%b %d")
    best_day_count = best["count"]

    # longest streak (any point in the year)
    longest = cur = 0
    for d in days:
        if d["count"] > 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0

    # current streak (ending today) — walk back from last day
    today_streak = 0
    for d in reversed(days):
        if d["count"] > 0:
            today_streak += 1
        else:
            # if today itself has 0 but yesterday had activity, the streak is 0
            break

    return {
        "total": total,
        "active": active,
        "total_days": len(days),
        "longest": longest,
        "current": today_streak,
        "best_day_date": best_day_date,
        "best_day_count": best_day_count,
        "weeks": _bucket_by_week(days),
    }


def _bucket_by_week(days):
    """Group days into 52 weeks. Returns list of (max_count, total_count) per week."""
    weeks = []
    for i in range(0, len(days), 7):
        chunk = days[i:i+7]
        if not chunk:
            continue
        total = sum(d["count"] for d in chunk)
        mx = max(d["count"] for d in chunk)
        weeks.append({"total": total, "max": mx})
    return weeks[-52:]  # last 52 weeks


# ---------- SVG ----------

# Theme: purple/red/black
BG0 = "#050308"
BG1 = "#1a0a2e"
BRD = "#4C1D95"
TXT = "#F8FAFC"
MUTED = "#C084FC"
DIM = "#7C3AED"
RED = "#E13838"
PURPLE = "#A855F7"
CREAM = "#F5E6C8"

# Colour ramp for buildings (dark → bright by activity)
RAMP = ["#1a0a2e", "#4C1D95", "#7C3AED", "#A855F7", "#C084FC", "#E13838"]


CUBE = 7  # base size of each cube in iso units
HMAX_PX = 90  # max building height in screen pixels


def iso_project(x, y, z):
    """Isometric projection where x goes right-up and y goes left-up.
    sy is negated so screen y DECREASES (goes up) as world x,y increase."""
    sx = (x - y) * 0.866  # cos(30°)
    sy = -(x + y) * 0.5 - z
    return sx, sy


def building_svg(wx, wy, height, color, side_color, top_color):
    """Draw one iso cube with base at (wx, wy, 0) in world coords, extruded upward by `height` px."""
    s = CUBE
    # 4 base corners in world → screen
    A = iso_project(wx,     wy,     0)  # front vertex
    B = iso_project(wx + s, wy,     0)  # right vertex
    C = iso_project(wx + s, wy + s, 0)  # back vertex
    D = iso_project(wx,     wy + s, 0)  # left vertex
    # 4 top corners (same x,y but z = height)
    Ap = (A[0], A[1] - height)
    Bp = (B[0], B[1] - height)
    Cp = (C[0], C[1] - height)
    Dp = (D[0], D[1] - height)
    # Visible faces: right wall, left wall, top
    return (
        f'<polygon points="{A[0]:.1f},{A[1]:.1f} {B[0]:.1f},{B[1]:.1f} {Bp[0]:.1f},{Bp[1]:.1f} {Ap[0]:.1f},{Ap[1]:.1f}" fill="{color}"/>'
        f'<polygon points="{A[0]:.1f},{A[1]:.1f} {D[0]:.1f},{D[1]:.1f} {Dp[0]:.1f},{Dp[1]:.1f} {Ap[0]:.1f},{Ap[1]:.1f}" fill="{side_color}"/>'
        f'<polygon points="{Ap[0]:.1f},{Ap[1]:.1f} {Bp[0]:.1f},{Bp[1]:.1f} {Cp[0]:.1f},{Cp[1]:.1f} {Dp[0]:.1f},{Dp[1]:.1f}" fill="{top_color}" stroke="{top_color}" stroke-width="0.5"/>'
    )


def pick_colors(count, max_count):
    """Choose fill, side (darker), top (brighter) based on activity level."""
    if max_count == 0:
        idx = 0
    else:
        pct = count / max_count
        idx = min(len(RAMP) - 1, int(pct * (len(RAMP) - 1) + 0.5))
    main = RAMP[idx]
    side = RAMP[max(0, idx - 1)]
    top = RAMP[min(len(RAMP) - 1, idx + 1)]
    return main, side, top


def render_svg(stats, username):
    weeks = stats["weeks"]
    max_week = max((w["max"] for w in weeks), default=1) or 1

    # layout
    W, H = 1000, 440
    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Contribution City — generated from live GitHub data">')
    svg.append('<style>')
    svg.append('.mono { font-family: ui-monospace, Menlo, Consolas, monospace; }')
    svg.append('.sans { font-family: -apple-system, "Segoe UI", Helvetica, sans-serif; }')
    svg.append('.pulse { animation: pulse 2.4s ease-in-out infinite; }')
    svg.append('@keyframes pulse { 0%,100% { opacity:.5; } 50% { opacity:1; } }')
    svg.append('@media (prefers-reduced-motion: reduce) { * { animation:none !important; } }')
    svg.append('</style>')
    svg.append('<defs>')
    svg.append(f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG1}"/><stop offset="1" stop-color="{BG0}"/></linearGradient>')
    svg.append(f'<linearGradient id="brd" x1="0" x2="1"><stop offset="0" stop-color="{PURPLE}"/><stop offset=".5" stop-color="{RED}"/><stop offset="1" stop-color="{PURPLE}"/></linearGradient>')
    svg.append('<pattern id="grid" width="20" height="12" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 12" fill="none" stroke="#4C1D95" stroke-opacity=".18" stroke-width="1"/></pattern>')
    svg.append('</defs>')

    # Frame
    svg.append(f'<rect x="2" y="2" width="{W-4}" height="{H-4}" rx="16" fill="url(#bg)" stroke="url(#brd)" stroke-width="2"/>')

    # Section label
    svg.append(f'<g transform="translate(26,30)">')
    svg.append(f'<rect width="12" height="14" fill="{RED}" class="pulse"/>')
    svg.append(f'<text x="22" y="12" class="mono" font-size="10" letter-spacing="3" fill="{MUTED}">[ 05 ] · CONTRIBUTION CITY</text>')
    svg.append(f'</g>')

    # Right side: big number + sub + stat lines
    svg.append(f'<g transform="translate(640,50)" class="sans">')
    svg.append(f'<text x="320" y="60" text-anchor="end" font-size="56" font-weight="900" fill="{PURPLE}">{stats["total"]:,}</text>')
    svg.append(f'<text x="320" y="86" text-anchor="end" class="mono" font-size="10" letter-spacing="4" fill="{MUTED}">CONTRIBUTIONS · LAST 12 MONTHS</text>')
    svg.append('</g>')

    # Stat rows (right column)
    stat_rows = [
        ("CURRENT STREAK", f"{stats['current']} days", RED if stats['current'] > 0 else MUTED),
        ("LONGEST STREAK", f"{stats['longest']} days", PURPLE),
        ("BEST DAY",       f"{stats['best_day_count']} on {stats['best_day_date']}", RED),
        ("ACTIVE DAYS",    f"{stats['active']} of {stats['total_days']}", CREAM),
    ]
    y = 170
    for label, value, color in stat_rows:
        svg.append(f'<text x="640" y="{y}" class="mono" font-size="11" letter-spacing="3" fill="{DIM}">{label}</text>')
        svg.append(f'<text x="960" y="{y}" text-anchor="end" class="mono" font-size="14" font-weight="700" fill="{color}">{value}</text>')
        svg.append(f'<line x1="640" y1="{y+8}" x2="960" y2="{y+8}" stroke="{BRD}" stroke-opacity=".4"/>')
        y += 32

    # Left side: isometric city, 52 weeks as a straight row of buildings
    # Place origin where oldest week lands (left side of panel, near bottom)
    gx0, gy0 = 110, 390
    svg.append(f'<g transform="translate({gx0},{gy0})">')

    # Ground "shadow" baseline across whole row
    first_x = 0
    last_x = (len(weeks) - 1) * 10 + CUBE
    svg.append(f'<line x1="{iso_project(first_x,0,0)[0]:.1f}" y1="{iso_project(first_x,0,0)[1]+2:.1f}" '
               f'x2="{iso_project(last_x,0,0)[0]:.1f}" y2="{iso_project(last_x,0,0)[1]+2:.1f}" '
               f'stroke="{BRD}" stroke-opacity=".3" stroke-width="1"/>')

    # Draw buildings oldest → newest, LEFT to RIGHT
    for i, w in enumerate(weeks):
        mx = w["max"]
        if max_week > 0:
            height = max(3, int((mx / max_week) * HMAX_PX))
        else:
            height = 3
        main, side, top = pick_colors(mx, max_week)
        wx = i * 10.0
        svg.append(building_svg(wx, 0, height, main, side, top))
    svg.append('</g>')

    # X-axis label (year range)
    today = datetime.now(timezone.utc)
    start = (today - timedelta(days=365)).strftime("%b %Y")
    end = today.strftime("%b %Y")
    svg.append(f'<text x="60" y="410" class="mono" font-size="10" letter-spacing="2" fill="{DIM}">{start}  →  {end}</text>')

    # Legend ramp
    svg.append(f'<g transform="translate(400,400)" class="mono" font-size="10" fill="{DIM}">')
    svg.append('<text x="0" y="8">LESS</text>')
    for i, c in enumerate(RAMP):
        svg.append(f'<rect x="{40 + i*14}" y="0" width="12" height="10" fill="{c}"/>')
    svg.append(f'<text x="{40 + len(RAMP)*14 + 10}" y="8">MORE</text>')
    svg.append('</g>')

    # Footer tag
    svg.append(f'<text x="26" y="{H-14}" class="mono" font-size="9" letter-spacing="2" fill="{BRD}">generated by tools/profile.py · daily</text>')

    svg.append('</svg>')
    return "\n".join(svg)


# ---------- Entry point ----------

def main():
    if len(sys.argv) < 2:
        print("usage: profile.py <github-username>", file=sys.stderr)
        sys.exit(2)
    login = sys.argv[1]
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN env var required", file=sys.stderr)
        sys.exit(2)

    print(f"Fetching contribution calendar for {login}...")
    total, days = fetch_calendar(login, token)
    print(f"  total contributions reported by API: {total}")

    stats = compute_stats(days)
    print(f"  computed total: {stats['total']}")
    print(f"  active days: {stats['active']} / {stats['total_days']}")
    print(f"  current streak: {stats['current']}, longest: {stats['longest']}")
    print(f"  best day: {stats['best_day_count']} on {stats['best_day_date']}")

    svg = render_svg(stats, login)
    out = os.path.join("metrics", "contribution-city.svg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {out} ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
