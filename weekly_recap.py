"""
Formentera Dynasty - weekly Discord recap bot.

Pulls this week's results from the Sleeper API, asks Claude to write a
Bill Simmons-style recap (power rankings, waiver report, game of the
week, "L" of the week, character-concerns bit), and posts it to a
Discord channel via webhook.

Run manually:
    python weekly_recap.py

Run for a specific week (useful for testing / backfilling):
    python weekly_recap.py --week 3

Required environment variables (see .env.example):
    SLEEPER_LEAGUE_ID   - the current season's Sleeper league ID
    DISCORD_WEBHOOK_URL - the channel webhook to post into
    ANTHROPIC_API_KEY   - API key from console.anthropic.com
"""

import argparse
import os
import sys
import time

import requests

SLEEPER_BASE = "https://api.sleeper.app/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-5"
DISCORD_CHUNK_LIMIT = 1900  # stay under Discord's 2000-char message cap

STYLE_GUIDE = """
You are the ghostwriter for a dynasty fantasy football league's weekly
Discord recap. Write in a loose, tangent-heavy sportswriting voice in
the tradition of Bill Simmons: pop-culture asides, running bits,
parenthetical jokes, real opinions stated with total confidence.

Tone: hard-roast leaning toward no-holds-barred. Call people out by
name for bad lineup decisions, blowout losses, and bad trades. Nobody's
feelings are precious here - this is a league that wants to be
roasted. Profanity is allowed and encouraged where it lands naturally
(this is an adult group chat, not a family newsletter) - use it for
emphasis, not as a crutch, and never direct slurs or attacks on
anything other than fantasy football performance.

Length: a full column, not a blurb. Multiple sections, real tangents,
built to be read in a group chat and argued about.

Every recap must include these sections, in this order:
1. A cold-open paragraph or two setting the tone for the week.
2. "Game of the week" - deep dive on the closest or most chaotic
   matchup.
3. "L of the week" - the worst lineup decision, bench blunder, or
   ugliest loss. Name names.
4. "Waiver wire report" - notable adds/drops and whether they were
   smart or panic moves. If waiver activity is thin, it is fair game
   to roast the inactivity itself.
5. "Character concerns" - a fake NFL-draft-style scouting report bit.
   Frame it as an investigation into a team's "organization" (i.e.
   the fantasy manager's roster-building competence/culture), not a
   real attack on the actual NFL player's character. Keep it
   obviously satirical.
6. "Power rankings" - full ranked list of every team with a one-line
   roast or compliment per team.

Only use real data provided to you. Do not invent stats, scores, or
transactions that were not given to you. If a data point isn't
available, work around it rather than making something up.

Format for Discord: every section header must be its own line, in
**bold** markdown AND written in full capital letters, like a real
title - for example: **GAME OF THE WEEK**. Do not use markdown "#" or
"##" headers - Discord does not render those distinctly from bold
text, so bold + caps is the only header style to use. Leave a blank
line before and after each header. Body paragraphs stay normal
sentence case. No emoji.
"""


def sleeper_get(path):
    resp = requests.get(f"{SLEEPER_BASE}{path}", timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_current_week():
    state = sleeper_get("/state/nfl")
    return state["week"]


def gather_week_data(league_id, week):
    league = sleeper_get(f"/league/{league_id}")
    users = sleeper_get(f"/league/{league_id}/users")
    rosters = sleeper_get(f"/league/{league_id}/rosters")
    matchups = sleeper_get(f"/league/{league_id}/matchups/{week}")
    try:
        transactions = sleeper_get(f"/league/{league_id}/transactions/{week}")
    except requests.HTTPError:
        transactions = []

    user_by_id = {u["user_id"]: u for u in users}
    roster_by_id = {r["roster_id"]: r for r in rosters}

    def team_name(roster_id):
        roster = roster_by_id.get(roster_id, {})
        owner = user_by_id.get(roster.get("owner_id"), {})
        meta = owner.get("metadata") or {}
        return meta.get("team_name") or owner.get("display_name") or f"Team {roster_id}"

    # group matchups by matchup_id (head-to-head pairs)
    pairs = {}
    for m in matchups:
        mid = m.get("matchup_id")
        pairs.setdefault(mid, []).append(m)

    games = []
    for mid, entries in pairs.items():
        if len(entries) != 2:
            continue
        a, b = entries
        games.append({
            "team_a": team_name(a["roster_id"]),
            "score_a": a.get("points", 0),
            "team_b": team_name(b["roster_id"]),
            "score_b": b.get("points", 0),
            "margin": round(abs((a.get("points") or 0) - (b.get("points") or 0)), 2),
        })

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

    moves = []
    for t in transactions:
        if t.get("status") != "complete":
            continue
        roster_ids = t.get("roster_ids") or []
        team = team_name(roster_ids[0]) if roster_ids else "unknown"
        moves.append({
            "team": team,
            "type": t.get("type"),
            "adds": list((t.get("adds") or {}).keys()),
            "drops": list((t.get("drops") or {}).keys()),
        })

    return {
        "league_name": league.get("name"),
        "week": week,
        "games": games,
        "standings": standings,
        "moves": moves,
    }


def build_prompt(data):
    lines = [f"League: {data['league_name']}", f"Week: {data['week']}", ""]
    lines.append("Matchup results:")
    for g in data["games"]:
        lines.append(f"- {g['team_a']} {g['score_a']} vs {g['team_b']} {g['score_b']} "
                      f"(margin {g['margin']})")
    lines.append("")
    lines.append("Standings (record, points for):")
    for s in data["standings"]:
        lines.append(f"- {s['team']}: {s['wins']}-{s['losses']}, {s['points_for']} PF")
    lines.append("")
    lines.append(f"Waiver/free-agent moves this week: {len(data['moves'])}")
    for mv in data["moves"]:
        lines.append(f"- {mv['team']}: {mv['type']}, added {mv['adds']}, dropped {mv['drops']}")
    return "\n".join(lines)


def generate_recap(data):
    prompt = build_prompt(data)
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 4000,
            "system": STYLE_GUIDE,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if not resp.ok:
        print(f"Anthropic API error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    body = resp.json()
    return "".join(block.get("text", "") for block in body.get("content", []))


def chunk_text(text, limit=DISCORD_CHUNK_LIMIT):
    chunks = []
    current = ""
    for paragraph in text.split("\n"):
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def post_to_discord(webhook_url, text):
    for chunk in chunk_text(text):
        resp = requests.post(webhook_url, json={"content": chunk}, timeout=20)
        resp.raise_for_status()
        time.sleep(1)  # keep message order stable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", type=int, default=None)
    args = parser.parse_args()

    league_id = os.environ["SLEEPER_LEAGUE_ID"]
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]
    week = args.week or get_current_week()

    print(f"Fetching week {week} data for league {league_id}...")
    data = gather_week_data(league_id, week)

    if not data["games"]:
        print("No completed matchups found for this week yet - skipping.")
        sys.exit(0)

    print("Generating recap...")
    recap = generate_recap(data)

    print("Posting to Discord...")
    post_to_discord(webhook_url, recap)
    print("Done.")


if __name__ == "__main__":
    main()
