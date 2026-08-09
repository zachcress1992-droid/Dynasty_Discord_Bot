"""
Dynasty league weekly recap bot.

Pulls this week's results from the Sleeper API, asks Claude for the
week's editorial content (headline, three styled columns, a rookie
"prospect watch" bit), renders it into a full recap page
(docs/index.html, served by GitHub Pages), and posts a short teaser +
link to Discord via webhook.

Run manually:
    python weekly_recap.py

Run for a specific week (useful for testing / backfilling):
    python weekly_recap.py --week 3

Required environment variables (see .env.example):
    SLEEPER_LEAGUE_ID   - the current season's Sleeper league ID
    DISCORD_WEBHOOK_URL - the channel webhook to post into
    ANTHROPIC_API_KEY   - API key from console.anthropic.com
    PAGES_URL           - the public GitHub Pages URL for this repo,
                           e.g. https://you.github.io/your-repo/
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

SLEEPER_BASE = "https://api.sleeper.app/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-5"
SEASON_WEEKS = 17
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")
TRANSACTION_LIMIT = 6

CONTENT_STYLE_GUIDE = """
You are the editorial voice behind a dynasty fantasy football league's
weekly recap page. Write in a loose, tangent-heavy sportswriting voice
in the tradition of Bill Simmons: pop-culture asides, running bits,
parenthetical jokes, real opinions stated with total confidence.

Tone: hard-roast leaning toward no-holds-barred. Call people out by
name for bad lineup decisions, blowout losses, and bad trades. Nobody's
feelings are precious here - this is a league that wants to be
roasted. Profanity is allowed and encouraged where it lands naturally
(this is an adult group chat, not a family newsletter) - use it for
emphasis, not as a crutch, and never direct slurs or attacks on
anything other than fantasy football performance.

Only use the real matchup, standings, transaction, and individual
player performance data given to you. Do not invent stats or scores.
Real, rostered players may be named by their real name in "The Beat",
"The Simmons-ish Take", and "The Deadpan" - these three articles are
factual sports commentary, so calling out a real player's real
performance (a monster week, a bench-worthy dud, a bad start/sit call)
is fair game and encouraged.

The "prospects" and "scouting_update" sections are the one exception -
those are fictional/satirical dynasty rookie prospects, not real
players. You will be given a short list of real rostered players as
"name inspiration" for this bit only. Invent an original fictional
prospect whose name is a lightly disguised variant of one of those
real names - close enough to be a fun wink, clearly different enough
that it is not the real name (example: "Josh Allen" -> "Josh Ballen",
"Justin Jefferson" -> "Justin Jeffersen"). Never use a real player's
name verbatim inside the prospects or scouting_update sections, and
never claim or imply the invented prospect IS that real player -
everything about the prospect's stats, school, and backstory must be
made up.

You must respond with ONLY a single valid JSON object (no markdown
fences, no commentary before or after) matching exactly this shape:

{
  "headline": "short punchy headline for the week, under 12 words",
  "teaser": "2-3 sentences that hook the reader into clicking through, same voice as everything else",
  "articles": [
    {
      "title": "article title, title case",
      "byline_name": "The Beat",
      "byline_tagline": "Straight news, no spin",
      "paragraphs": ["...", "..."]
    },
    {
      "title": "article title, title case",
      "byline_name": "The Simmons-ish Take",
      "byline_tagline": "Big theory, small evidence, no regrets",
      "paragraphs": ["...", "..."]
    },
    {
      "title": "article title, lowercase, deadpan style",
      "byline_name": "The Deadpan",
      "byline_tagline": "Tweet-length, allergic to hype",
      "paragraphs": ["one short punchy line per paragraph, 4-6 of them"]
    }
  ],
  "prospects": [
    {"name": "fake full name", "position": "QB/RB/WR/TE", "school_class": "College Name, Jr./Sr.", "grade": "0.0-10.0 scouting grade as a string", "stat_line": "a short fake college stat line", "comp": "short comparison/scouting note"},
    {"name": "...", "position": "...", "school_class": "...", "grade": "...", "stat_line": "...", "comp": "..."},
    {"name": "...", "position": "...", "school_class": "...", "grade": "...", "stat_line": "...", "comp": "..."}
  ],
  "scouting_update": {
    "tag": "Scouting Desk Update - Week N",
    "paragraphs": ["...", "...", "..."]
  }
}

The "The Beat" article should be the straight factual recap of the
week's biggest results. "The Simmons-ish Take" is the main
personality/roast column - this is where the worst lineup decision,
best/worst trade, and any panic waiver activity get roasted by name.
"The Deadpan" is a handful of short, dry, tweet-length reactions.

The "scouting_update" is a fake NFL-draft-style scouting report bit
about one of the invented prospects, satirically tying supposed
"character concerns" to one real team's "organization" (i.e. that
fantasy manager's roster-building competence/culture) - not a real
attack on any actual person. Keep it obviously satirical and use one
of the real team names given to you.
"""


def sleeper_get(path):
    resp = requests.get(f"{SLEEPER_BASE}{path}", timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_current_week():
    state = sleeper_get("/state/nfl")
    return state["week"]


def get_players():
    """Full Sleeper player dump, id -> display name (includes DEF entries)."""
    raw = sleeper_get("/players/nfl")
    names = {}
    for pid, p in raw.items():
        if p.get("position") == "DEF":
            names[pid] = p.get("last_name") or pid
        else:
            names[pid] = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid
    return names


def gather_week_data(league_id, week, players):
    league = sleeper_get(f"/league/{league_id}")
    users = sleeper_get(f"/league/{league_id}/users")
    rosters = sleeper_get(f"/league/{league_id}/rosters")
    matchups = sleeper_get(f"/league/{league_id}/matchups/{week}")
    try:
        raw_transactions = sleeper_get(f"/league/{league_id}/transactions/{week}")
    except requests.HTTPError:
        raw_transactions = []

    user_by_id = {u["user_id"]: u for u in users}
    roster_by_id = {r["roster_id"]: r for r in rosters}

    def team_name(roster_id):
        roster = roster_by_id.get(roster_id, {})
        owner = user_by_id.get(roster.get("owner_id"), {})
        meta = owner.get("metadata") or {}
        return meta.get("team_name") or owner.get("display_name") or f"Team {roster_id}"

    def owner_handle(roster_id):
        roster = roster_by_id.get(roster_id, {})
        owner = user_by_id.get(roster.get("owner_id"), {})
        handle = owner.get("display_name")
        return f"@{handle}" if handle else ""

    def player_name(pid):
        return players.get(pid, pid)

    def best_worst_starter(entry):
        starters = entry.get("starters") or []
        pts = entry.get("starters_points") or []
        pairs = [(sid, p) for sid, p in zip(starters, pts) if sid and sid != "0"]
        if not pairs:
            return None, None
        best_id, best_pts = max(pairs, key=lambda p: p[1])
        worst_id, worst_pts = min(pairs, key=lambda p: p[1])
        best = {"name": player_name(best_id), "points": round(best_pts, 2)}
        worst = {"name": player_name(worst_id), "points": round(worst_pts, 2)}
        return best, worst

    pairs = {}
    for m in matchups:
        mid = m.get("matchup_id")
        pairs.setdefault(mid, []).append(m)

    games = []
    performances = []
    for mid, entries in pairs.items():
        if len(entries) != 2:
            continue
        a, b = entries
        best_a, worst_a = best_worst_starter(a)
        best_b, worst_b = best_worst_starter(b)
        games.append({
            "team_a": team_name(a["roster_id"]),
            "owner_a": owner_handle(a["roster_id"]),
            "score_a": a.get("points", 0),
            "team_b": team_name(b["roster_id"]),
            "owner_b": owner_handle(b["roster_id"]),
            "score_b": b.get("points", 0),
            "margin": round(abs((a.get("points") or 0) - (b.get("points") or 0)), 2),
        })
        for team, best, worst in ((team_name(a["roster_id"]), best_a, worst_a),
                                   (team_name(b["roster_id"]), best_b, worst_b)):
            if best:
                performances.append({"team": team, "player": best["name"], "points": best["points"], "role": "top scorer"})
            if worst:
                performances.append({"team": team, "player": worst["name"], "points": worst["points"], "role": "worst starter"})

    performances.sort(key=lambda p: -p["points"])

    if games:
        blowout = max(games, key=lambda g: g["margin"])
        closest = min(games, key=lambda g: g["margin"])
        for g in games:
            score_a, score_b = g["score_a"], g["score_b"]
            g["win_side"] = "a" if score_a >= score_b else "b"
            g["tag"] = None
            g["score_a"] = round(score_a, 2)
            g["score_b"] = round(score_b, 2)
        if blowout is not closest:
            blowout["tag"] = "blowout"
            closest["tag"] = "closest"

    standings = []
    for r in rosters:
        s = r.get("settings", {})
        standings.append({
            "team": team_name(r["roster_id"]),
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "points_for": round((s.get("fpts", 0) or 0) + (s.get("fpts_decimal", 0) or 0) / 100, 2),
        })
    standings.sort(key=lambda x: (-x["wins"], -x["points_for"]))

    transactions = []
    for t in raw_transactions:
        if t.get("status") != "complete":
            continue
        roster_ids = t.get("roster_ids") or []
        adds = t.get("adds") or {}
        drops = t.get("drops") or {}
        draft_picks = t.get("draft_picks") or []

        if t.get("type") == "trade" and len(roster_ids) > 1:
            parties = []
            for rid in roster_ids:
                received_players = [player_name(pid) for pid, owner_rid in adds.items() if owner_rid == rid]
                received_picks = [
                    f"{p.get('season')} round {p.get('round')} pick"
                    for p in draft_picks if p.get("owner_id") == rid
                ]
                received = received_players + received_picks
                if received:
                    parties.append({"team": team_name(rid), "received": received})
            if parties:
                transactions.append({"kind": "Trade", "parties": parties})
        elif roster_ids:
            rid = roster_ids[0]
            added = [player_name(pid) for pid in adds.keys()]
            dropped = [player_name(pid) for pid in drops.keys()]
            settings = t.get("settings") or {}
            bid = settings.get("waiver_bid")
            note = f"${bid} FAAB" if bid else None
            kind = "Waiver Claim" if t.get("type") == "waiver" else "Free Agent Add"
            if added or dropped:
                transactions.append({
                    "kind": kind,
                    "team": team_name(rid),
                    "added": added,
                    "dropped": dropped,
                    "note": note,
                })

    transactions.sort(key=lambda t: 0 if t["kind"] == "Trade" else 1)
    transactions = transactions[:TRANSACTION_LIMIT]

    high_score_game = max(games, key=lambda g: max(g["score_a"], g["score_b"])) if games else None

    return {
        "league_name": league.get("name"),
        "week": week,
        "games": games,
        "standings": standings,
        "transactions": transactions,
        "high_score_game": high_score_game,
        "performances": performances,
    }


def build_ticker_items(data):
    items = []
    for t in data["transactions"][:4]:
        if t["kind"] == "Trade" and t["parties"]:
            p = t["parties"][0]
            items.append({"label": "TRADE", "detail": f"{p['team']} land {', '.join(p['received'])}"})
        elif t.get("added"):
            items.append({"label": "WIRE", "detail": f"{t['team']} add {', '.join(t['added'])}"})
    if data.get("high_score_game"):
        g = data["high_score_game"]
        top_team = g["team_a"] if g["score_a"] >= g["score_b"] else g["team_b"]
        top_score = max(g["score_a"], g["score_b"])
        items.append({
            "label": f"WEEK {data['week']}",
            "detail": f"{len(data['games'])} games final · high score {top_score} ({top_team})",
        })
    if not items:
        items.append({"label": f"WEEK {data['week']}", "detail": "quiet week on the wire"})
    return items


def build_content_prompt(data):
    lines = [f"League: {data['league_name']}", f"Week: {data['week']}", ""]
    lines.append("Matchup results:")
    for g in data["games"]:
        lines.append(f"- {g['team_a']} {g['score_a']} vs {g['team_b']} {g['score_b']} (margin {g['margin']})")
    lines.append("")
    lines.append("Standings (record, points for):")
    for s in data["standings"]:
        lines.append(f"- {s['team']}: {s['wins']}-{s['losses']}, {s['points_for']} PF")
    lines.append("")
    if data["transactions"]:
        lines.append("Transactions this week:")
        for t in data["transactions"]:
            if t["kind"] == "Trade":
                parts = "; ".join(f"{p['team']} get {', '.join(p['received'])}" for p in t["parties"])
                lines.append(f"- Trade: {parts}")
            else:
                lines.append(f"- {t['kind']}: {t['team']} added {t['added']}, dropped {t['dropped']}")
    else:
        lines.append("Transactions this week: none")
    lines.append("")
    if data["performances"]:
        lines.append("Notable individual player performances this week (real names - fine to use verbatim in The Beat / Simmons-ish Take / Deadpan):")
        for p in data["performances"][:6]:
            lines.append(f"- {p['player']} ({p['team']}) - {p['points']} pts, {p['role']}")
    lines.append("")
    lines.append("Real team names in this league (use these verbatim, do not invent teams):")
    for s in data["standings"]:
        lines.append(f"- {s['team']}")
    lines.append("")
    if data["performances"]:
        seed_names = [p["player"] for p in data["performances"][:8]]
        lines.append("Name-inspiration pool for the fictional prospects/scouting_update ONLY "
                      "(pick one, disguise the name, never use verbatim - see system instructions):")
        for n in seed_names:
            lines.append(f"- {n}")
    return "\n".join(lines)


def call_claude(system_prompt, user_prompt, max_tokens=4000):
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=180,
    )
    if not resp.ok:
        print(f"Anthropic API error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    body = resp.json()
    return "".join(block.get("text", "") for block in body.get("content", []))


def generate_content(data):
    raw = call_claude(CONTENT_STYLE_GUIDE, build_content_prompt(data))
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def render_html(data, content):
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("recap_template.html")
    return template.render(
        league_name=data["league_name"],
        week=data["week"],
        season_weeks=SEASON_WEEKS,
        games_final=len(data["games"]),
        updated_at=datetime.now(timezone.utc).strftime("%a %-I:%M %p UTC"),
        ticker_items=build_ticker_items(data),
        matchups=data["games"],
        transactions=data["transactions"],
        prospects=content["prospects"],
        scouting_update=content["scouting_update"],
        articles=content["articles"],
    )


def write_page(html):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)


def build_discord_message(content, page_url):
    headline = content["headline"].upper()
    return f"**{headline}**\n\n{content['teaser']}\n\nFull rundown: {page_url}"


def post_to_discord(webhook_url, text):
    resp = requests.post(webhook_url, json={"content": text}, timeout=20)
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", type=int, default=None)
    args = parser.parse_args()

    league_id = os.environ["SLEEPER_LEAGUE_ID"]
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]
    page_url = os.environ.get("PAGES_URL", "").rstrip("/") + "/"
    week = args.week or get_current_week()

    print("Fetching player database...")
    players = get_players()

    print(f"Fetching week {week} data for league {league_id}...")
    data = gather_week_data(league_id, week, players)

    if not data["games"]:
        print("No completed matchups found for this week yet - skipping.")
        sys.exit(0)

    print("Generating content...")
    content = generate_content(data)

    print("Rendering page...")
    html = render_html(data, content)
    write_page(html)

    print("Posting to Discord...")
    post_to_discord(webhook_url, build_discord_message(content, page_url))
    print("Done.")


if __name__ == "__main__":
    main()
