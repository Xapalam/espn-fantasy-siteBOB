# League History & Hall of Shame

A free, static site that pulls your ESPN Fantasy Football league's full history
and turns it into standings, all-time records, head-to-head rivalries, and a
(deliberately crude) "Hall of Shame" roast section. Hosted free on GitHub
Pages; data refreshes automatically via GitHub Actions.

## 1. Create the GitHub repo

```bash
cd espn-fantasy-site
git init
git add .
git commit -m "Initial site scaffold"
git branch -M main
git remote add origin https://github.com/iasllani/YOUR-REPO-NAME.git
git push -u origin main
```

(Create the empty repo on GitHub first at github.com/new, then use its URL above.)

## 2. Add your ESPN credentials as encrypted secrets

**Do not commit your SWID or espn_s2 anywhere.** Instead, in your new repo on
GitHub:

`Settings -> Secrets and variables -> Actions -> New repository secret`

Add two secrets:
- `ESPN_SWID` — your SWID cookie value, including the curly braces, e.g. `{12345678-90AB-CDEF-1234-567890ABCDEF}`
- `ESPN_S2` — your espn_s2 cookie value

These are only ever used inside the GitHub Actions runner (`.github/workflows/update-data.yml`)
and are encrypted at rest by GitHub.

### Optional: AI-generated content

If you add a third secret, `ANTHROPIC_API_KEY` (from console.anthropic.com —
note this is a separate paid API account, not a Claude Pro subscription),
the Action also calls Claude (explicit language, on purpose) to write:
- fresh, stat-specific roast lines for every owner, in place of the fixed
  template bank
- one roast of each owner's current roster as a whole
- an explicit recap of last week's matchups and a trash-talk preview of
  this week's
- a per-player LIVE feed after each night's games: who beat or missed their
  projection, and who got left on the bench while going off
- a "Matchup Live" analysis of every matchup — who's winning, who's likely
  to lose, and the worst lineup mistake each manager made. Predictions use
  ESPN's own win probability and projected finals rather than anything the
  model invents; bench mistakes are only claimed when the benched player was
  actually eligible for the slot the started player occupied. Recent lines
  are fed back into the prompt so it stops recycling the same jokes.
- the **LIVE** tab's per-player feed: individual call-outs for players who
  beat or missed their week's projection, plus roasts aimed at any owner who
  left a big scorer on the bench

Cost is small (a few dollars per season at most, depending on roster size —
still far under what a $5 prepaid balance covers). If this secret isn't
set, roasts fall back to templates and matchup blurbs fall back to plain
factual text — nothing breaks either way.

## 3. Turn on GitHub Pages

`Settings -> Pages -> Build and deployment -> Deploy from a branch -> Branch: main, folder: /docs`

Your site will be live at `https://iasllani.github.io/YOUR-REPO-NAME/` within a
minute or two.

## 4. Run the data fetch for the first time

Go to the **Actions** tab in your repo, select **Update ESPN Fantasy Data**,
and click **Run workflow**. This pulls every season of your league's history
it can find (auto-discovers how far back it goes) and commits the JSON into
`docs/data/seasons/`. Once that commit lands, refresh the live site.

After the first run, it also fires automatically three times a week during
the season — Friday, Monday, and Tuesday mornings UTC, timed to land a few
hours after Thursday Night Football, Sunday's full slate, and Monday Night
Football wrap up (see the `cron` lines in the workflow file — tweak them if
you want a different cadence, e.g. if games are running very late).

There's a **second, separate workflow** (`.github/workflows/live-feed.yml`)
for the LIVE tab, running ~11:30pm ET on those same game nights. It's
deliberately kept apart from the main pipeline: it only needs fresh
per-player box scores for the current week, so it skips the full 10-season
history fetch and career-roast regeneration entirely, which makes it fast
and cheap enough to run right after games instead of the next morning. Both
workflows commit to `main`, so they share a `concurrency` group to keep them
from racing each other.

## 5. Fix up owner names for past seasons

`docs/data/owners.json` maps ESPN team names to real names. It's pre-filled
with this season's team names. If anyone renamed their team in past years,
their historical seasons will show up on the site as "(unmapped)" — just add
their old team name (or ESPN member id) to `owners.json` and re-push.

## Local development / testing

```bash
cd scripts
pip install -r requirements.txt
ESPN_SWID='{...}' ESPN_S2='...' python3 fetch_espn_data.py --league-id 703243
```

Then open `docs/index.html` in a browser (or run `python3 -m http.server`
from inside `docs/` and visit `localhost:8000`) to preview locally before
pushing.

## Notes / known limitations

- The "Top 3 Each Season" table (All-Time tab) shows actual playoff finish
  (champion/runner-up/3rd place), not regular-season record. 3rd place is
  reconstructed from the bracket structure (the two semifinal losers' game)
  since ESPN doesn't expose final placement directly — see the comment on
  `find_playoff_placements` in `scripts/fetch_espn_data.py` for how that's
  verified.
- Owner identity is resolved by stable ESPN member ID (`docs/data/owners.json`
  → `memberNameOverrides`), not by team name, so it survives someone renaming
  their team every year. Former league members who left before the earliest
  mapped season still show up under their raw ESPN handle until you add them
  to `memberNameOverrides` yourself — there's no way to auto-discover a real
  name for someone no longer in the league.
- Championship history is parsed (last `WINNERS_BRACKET` game of each season)
  and shown as a "Titles" column plus dedicated roast cards.
- Hall of Shame roasts every owner individually, not just the league-wide
  extremes. With `ANTHROPIC_API_KEY` set, those per-owner roasts are
  AI-generated fresh each week from real stats; otherwise they're drawn from
  a template bank baked into `docs/app.js`. Former league members who left
  the league are shown separately at the bottom, under their own divider.
- The "This Week" tab shows a preview of the upcoming matchups and a recap
  of the last completed week.
- Clicking any team on the Standings tab opens its current roster: each
  player's position, NFL team, injury status, this-season and last-season
  stats (passing/rushing/receiving lines where applicable), ESPN's own
  real "season outlook" blurb, and one AI-written roast of the roster as a
  whole. Roster/player stat data comes from ESPN's `mRoster` view; the raw
  stat categories ESPN returns are numeric IDs with no public mapping, so
  `scripts/fetch_espn_data.py` only surfaces the ones verified against real
  data (passing/rushing/receiving yards, TDs, INTs, receptions, fumbles
  lost) rather than guessing at the rest (kicking/defense detail, etc).
