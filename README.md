# Dynasty Discord recap bot

Posts a weekly, Bill Simmons-style recap of your Sleeper dynasty
league to Discord, fully automated. Runs on GitHub Actions on a free
tier - no server, no laptop that has to stay on.

## How it works

Every Tuesday morning, a GitHub Actions job:
1. Pulls this week's matchups, standings, and waiver moves from the
   Sleeper API (no key needed - Sleeper's API is public).
2. Sends that data to Claude with a style guide baked in (hard-roast
   tone, power rankings, waiver report, game of the week, "L" of the
   week, a fake scouting-report roast bit).
3. Posts the recap to your Discord channel via webhook, split into
   Discord-sized chunks.

## One-time setup

### 1. Get an Anthropic API key
Go to [console.anthropic.com](https://console.anthropic.com), create
an API key, and add billing (this uses the API, which is metered
separately from any Claude.ai subscription - a weekly recap costs
well under a cent).

### 2. Create a GitHub repo
Push this folder to a new (can be private) GitHub repo.

### 3. Add repo secrets
In the repo: **Settings -> Secrets and variables -> Actions -> New
repository secret**. Add three:

| Secret name | Value |
|---|---|
| `SLEEPER_LEAGUE_ID` | Your current season's Sleeper league ID |
| `DISCORD_WEBHOOK_URL` | The webhook URL for your Discord channel |
| `ANTHROPIC_API_KEY` | The key from step 1 |

### 4. Enable Actions
Go to the **Actions** tab and enable workflows if prompted. The job
is already scheduled - Tuesdays at 13:00 UTC (roughly 8-9am ET,
depending on daylight saving), after Monday Night Football wraps.

### 5. Test it
From the **Actions** tab, select "weekly-dynasty-recap" -> **Run
workflow** to trigger it manually any time, optionally overriding the
week number. This is the easiest way to confirm everything's wired up
correctly before waiting for the real Tuesday run.

## Running locally (optional, for testing)

```
pip install -r requirements.txt
cp .env.example .env   # fill in real values
export $(cat .env | xargs)   # or use a tool like direnv
python weekly_recap.py --week 1
```

## Notes

- If a week has no completed matchups yet (e.g. mid-week, or the
  offseason), the script exits quietly without posting.
- The style guide lives at the top of `weekly_recap.py` in the
  `STYLE_GUIDE` variable - edit it any time to dial the tone up or
  down.
- Sleeper's API is read-only and free with no rate-limit key needed,
  but stays under roughly 1,000 requests/minute as a courtesy.
