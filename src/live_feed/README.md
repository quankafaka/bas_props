# Live odds feed: credit usage tests

**Goal (task 0):** estimate how many The Odds API credits a live props feed uses per month, so we buy the right subscription tier.

Each test keeps one NBA game's prop markets up to date for a set time and records how many credits it spends, how many odds actually changed, and how late the changes arrive.

| Version | Idea | Script | Status |
|---|---|---|---|
| A | Only fetch odds when the API says a market was updated | `feed_version_a.py` | Done: [report](reports/version_a_2026-11-17.md) |
| B | Fixed refresh intervals by time to kickoff (1 min under 30 min, 5 min under 1 h, 15 min otherwise) | `feed_version_b.py` | Done: [report](reports/version_b_2026-11-19.md) |
| C | Other ways to save credits | — | Not started |

## Folder

```
src/live_feed/
├── README.md            this file
├── feed_version_a.py    version A test (calls the real API, spends credits)
├── feed_version_b.py    version B test (calls the real API, spends credits)
├── viewer.html          live page that shows the latest version A run
└── runs/                one folder per run (not in git)
    └── version_a/ or version_b/<UTC start time>/
        ├── polls.jsonl      one line per poll: credits spent, markets that moved, odds changes
        ├── changes.jsonl    one line per odds change: book, player, side, old/new line and price, latency
        ├── summary.json     totals for the report (rewritten after every poll)
        ├── dashboard_received.jsonl  when the props dashboard picked up each odds pull (live mode only)
        └── raw/             the untouched API responses, so versions B and C can be replayed offline
```

The API client (`OddsApi`) is reused from `src/odds-api/pull_snapshot.py`. Paths come from `src/general_utility/create_path.py`.

## Run it

Put your key in `.env` at the project root: `ODDS_API_KEY=...`

```bash
# 1. Preview: which game, how many polls, worst-case credits (spends nothing)
python src/live_feed/feed_version_a.py --dry-run

# 2. Real run: 1 hour, poll every 2 minutes, stop before 80 credits
python src/live_feed/feed_version_a.py --minutes 60 --interval 120 --markets player_points --budget 80

# 3. Watch the run's log live (second terminal), then open http://localhost:8060/viewer.html
python -m http.server 8060 --directory src/live_feed

# 4. See the odds in the props dashboard (third terminal), then open http://localhost:8050
#    The board refreshes on its own every time the feed pulls new odds.
python src/dashboard/server.py --live-run latest
```

In live mode the dashboard reads the run's `raw/*_odds.json` files (newest price per book, player and side wins) and keeps its trader state in `src/dashboard/data/trader_state_live.json`, so the snapshot demo's state is untouched. Every time it picks up a new odds file it adds a line to the run's `dashboard_received.jsonl`: API `last_update`, when the feed received it, when the dashboard loaded it, and the delays between them. Simulated bets still come from `simulator.py`; they don't use the API.

Options: `--event <id>` (default: next NBA game), `--markets` (comma list, e.g. `player_points,player_rebounds`), `--regions` (default `us`).

`--live-run latest` picks the newest run of any version.

## How version B works

No `/markets` check. Every poll fetches odds for all watched markets (1 credit per market per region), on a schedule set by the time left to tip-off:

| Time to tip-off | Refresh |
|---|---|
| 30 min or less | every 1 min |
| 30 to 60 min | every 5 min |
| more than 60 min | every 15 min |

A 15-minute step never jumps past the 60- or 30-minute mark; the next poll lands on it. The feed stops at tip-off (pre-game props only) or after `--minutes`. `polls.jsonl`, `changes.jsonl` and `summary.json` also record the tier each poll ran in, and `summary.json` breaks polls, credits and odds changes down by tier.

Polls per game for `H` hours of pre-game coverage (H ≥ 1): `4 × (H − 1) + 36`. Credits = polls × markets × regions.

```bash
# Check the schedule logic (no API calls)
python src/live_feed/feed_version_b.py --self-check

# Preview: start 60 min before the next tip-off, 1 hour, 3 markets (spends nothing)
python src/live_feed/feed_version_b.py --dry-run --start-before 60 --markets player_points,player_rebounds,player_assists

# Real run: waits until 60 min before tip-off, then polls until tip-off
python src/live_feed/feed_version_b.py --minutes 60 --start-before 60 --markets player_points,player_rebounds,player_assists --budget 150

# Odds on the props dashboard, refreshed on every poll
python src/dashboard/server.py --live-run latest
```

`--start-before 60` covers the 5-min and 1-min tiers (36 polls, 108 credits with 3 markets). Use `--start-before 90 --minutes 90` to also see the 15-min tier.

## How version A works

The Odds API has no push or websocket, so "only on update" has to be built with polling. Each poll:

1. `GET /v4/sports/basketball_nba/events/{eventId}/markets?regions=us`: **1 credit**. Lists every market each bookmaker offers, with its `last_update`.
2. If a watched market's `last_update` moved for any book: `GET /v4/sports/basketball_nba/events/{eventId}/odds?markets=<moved>&regions=us`: **1 credit per market per region**.
3. Compare prices and lines with the previous pull to count real odds changes.

Credit costs are from the [official docs](https://the-odds-api.com/liveapi/guides/v4/). The script counts credits from the API's `x-requests-last` response header, not from estimates.

## What we learned so far (Nov 17, 2026)

- `last_update` is when The Odds API last **scraped** the bookmaker, not when the price changed. All markets of one book share the same time, and most move on every poll. So version A pays for fetches even when nothing changed.
- Watching one market costs the same as just fetching it (1 credit each). Version A only saves credits when many markets per game are watched and few change.
- Latency is measured as "time we received it" minus `last_update`. The API doesn't expose the exact time the book changed the price, and the poll interval adds up to one interval of delay.
- First (interrupted) run: Knicks @ Raptors, `player_points`, 20 minutes, 11 polls, 22 credits, 19 odds changes.
- Full run: Knicks @ Raptors, 3 markets, 60 minutes, 30 polls, 120 credits, 627 odds changes. See `reports/version_a_2026-11-17.md`.
- Version B run (Nov 19): Warriors @ Grizzlies, 3 markets, last 60 minutes before tip-off, 36 polls, 108 credits, 1,944 odds changes, median delay 31 s at 1-minute refresh. Credits depend only on polls × markets × regions. NBA with up to about 8 markets fits the 100K plan. See `reports/version_b_2026-11-19.md`.
