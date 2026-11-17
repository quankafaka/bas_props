# Live odds feed: credit usage tests

**Goal (task 0):** estimate how many The Odds API credits a live props feed uses per month, so we buy the right subscription tier.

Each test keeps one NBA game's prop markets up to date for a set time and records how many credits it spends, how many odds actually changed, and how late the changes arrive.

| Version | Idea | Script | Status |
|---|---|---|---|
| A | Only fetch odds when the API says a market was updated | `feed_version_a.py` | Ready |
| B | Fixed refresh intervals by time to kickoff (1 min under 30 min, 5 min under 1 h, 15 min otherwise) | — | Not started |
| C | Other ways to save credits | — | Not started |

## Folder

```
src/live_feed/
├── README.md            this file
├── feed_version_a.py    version A test (calls the real API, spends credits)
├── viewer.html          live page that shows the latest run
└── runs/                one folder per run (not in git)
    └── version_a/<UTC start time>/
        ├── polls.jsonl      one line per poll: credits spent, markets that moved, odds changes
        ├── changes.jsonl    one line per odds change: book, player, side, old/new line and price, latency
        ├── summary.json     totals for the report (written when the run finishes)
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

# 3. Watch it live (in a second terminal), then open http://localhost:8060/viewer.html
python -m http.server 8060 --directory src/live_feed
```

Options: `--event <id>` (default: next NBA game), `--markets` (comma list, e.g. `player_points,player_rebounds`), `--regions` (default `us`).

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
