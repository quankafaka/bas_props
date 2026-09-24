# Trader Props Dashboard: Demo Data Snapshot

A one-time pull of player-prop odds from The Odds API. The demo dashboard will run locally off these files.

## Run it

Requires Python 3.8+. Nothing to install.

```bash
# ODDS_API_KEY is read from the .env file in the project root

python pull_snapshot.py --dry-run         # preview: which games/markets, estimated cost
python pull_snapshot.py                   # real pull (asks to confirm first)
```

Useful options:

| Flag | Default | What it does |
|---|---|---|
| `--sports` | `nfl,ncaaf,mlb,epl` | Any of: nfl, ncaaf, cfl, nba, ncaab, wnba, mlb, nhl, afl, nrl, epl, laliga, bundesliga, seriea, ligue1, mls |
| `--max-events` | 3 | Games per sport |
| `--days-ahead` | 7 | Only games starting within this window |
| `--include-alternates` | off | Adds alt lines (many more markets, so many more credits) |
| `--budget` | 1000 | Aborts if the estimate is higher |

## What it costs

- Sports list and events list: free
- Market discovery: 1 credit per game
- Game lines: about 3 credits per sport
- Props: 1 credit per market returned, per game (1 region)

A default run of 4 sports × 3 games is typically a few hundred credits.

## Output (`src/odds-api/data/`)

| File | Contents |
|---|---|
| `meta.json` | Pull time, sports, books, credits used, market display names |
| `events.json` | Each game with teams, start time, game lines, prop markets found |
| `props.json` / `props.csv` | One row per book × market × player × side, with line, American price, implied probability |
| `raw/` | Untouched API responses |

No working API key yet? `python make_sample_data.py` writes a sample snapshot in the same format, so the dashboard can run without one.

**Timing tip:** props are posted closer to game time. Run it about 24 hours before a big NFL slate (e.g. Saturday for Sunday games) to get the fullest market coverage.
