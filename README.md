# Dynasty Discord recap bot

Publishes a full weekly recap page for your Sleeper dynasty league
(scoreboard, transactions, a rookie "prospect watch" bit, and three
styled recap columns in a hard-roast Bill Simmons voice), hosted for
free on GitHub Pages, with a short teaser + link posted to Discord.
Runs on GitHub Actions - no server, no laptop that has to stay on.

## How it works

Every Tuesday morning, a GitHub Actions job:
1. Pulls this week's matchups, standings, and transactions from the
   Sleeper API (no key needed - Sleeper's API is public), resolving
   real player names.
2. Sends that data to Claude, which returns the week's headline,
   teaser, three recap columns, and a satirical rookie scouting bit.
3. Renders it all into `docs/index.html` using the template in
   `templates/recap_template.html`, and commits that page back to the
   repo - GitHub Pages serves it at your public URL.
4. Posts a short headline + teaser + link to that page in your
   Discord channel via webhook.

## One-time setup

### 1. Get an Anthropic API key
Go to [console.anthropic.com](https://console.anthropic.com), create
an API key, and add billing (this uses the API, which is metered
separately from any Claude.ai subscription - a weekly recap costs
well under a cent).

### 2. Create a GitHub repo
Push this folder to a new (can be private or public - see note below)
GitHub repo.

### 3. Enable GitHub Pages
In the repo: **Settings -> Pages**. Under "Build and deployment",
set **Source** to "Deploy from a branch", **Branch** to `main`,
folder to `/docs`, then **Save**. GitHub will show your public URL
(something like `https://yourname.github.io/your-repo/`) - copy it,
you'll need it in the next step.

Note: GitHub Pages for a *private* repo requires a paid GitHub plan.
On the free plan, the repo (and this page) needs to be public to be
served. Since it's a fantasy league recap, that's usually fine, but
worth knowing.

### 4. Add repo secrets
In the repo: **Settings -> Secrets and variables -> Actions -> New
repository secret**. Add four:

| Secret name | Value |
|---|---|
| `SLEEPER_LEAGUE_ID` | Your current season's Sleeper league ID |
| `DISCORD_WEBHOOK_URL` | The webhook URL for your Discord channel |
| `ANTHROPIC_API_KEY` | The key from step 1 |
| `PAGES_URL` | The GitHub Pages URL from step 3 |

### 5. Enable Actions
Go to the **Actions** tab and enable workflows if prompted. The job
is already scheduled - Tuesdays at 13:00 UTC (roughly 8-9am ET,
depending on daylight saving), after Monday Night Football wraps.

### 6. Test it
From the **Actions** tab, select "weekly-dynasty-recap" -> **Run
workflow** to trigger it manually any time, optionally overriding the
week number. Check the page URL and your Discord channel afterward to
confirm everything's wired up.

## Running locally (optional, for testing)

```
pip install -r requirements.txt
cp .env.example .env   # fill in real values
export $(cat .env | xargs)   # or use a tool like direnv
python weekly_recap.py --week 1
```

This writes `docs/index.html` locally - open it directly in a browser
to preview before pushing.

## Notes

- If a week has no completed matchups yet (e.g. mid-week, or the
  offseason), the script exits quietly without posting or updating
  the page.
- The style guide lives at the top of `weekly_recap.py` in the
  `CONTENT_STYLE_GUIDE` variable - edit it any time to dial the tone
  up or down.
- The page's look and layout live in `templates/recap_template.html` -
  it's a Jinja2 template, safe to restyle without touching the Python.
- Sleeper's API is read-only and free with no rate-limit key needed,
  but stays under roughly 1,000 requests/minute as a courtesy.
