"""
Live odds for the dashboard, read from a live_feed test run instead of the one-time snapshot.

The feed (src/live_feed/feed_version_a.py or feed_version_b.py) saves every /odds response it pulls in
<run>/raw/NNN_odds.json. This module merges them (latest price per book x market x
player x side wins) into the same (meta, events, rows) shape load_snapshot() returns,
so build_board() and the rest of the desk work unchanged.

Each time the dashboard picks up a new odds file it appends a line to
<run>/dashboard_received.jsonl, so the report can measure the delay from the API's
last_update to the moment the dashboard had the price.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from general_utility.create_path import LIVE_FEED_RUNS_DIR

SPORT_ALIAS = {"basketball_nba": "nba"}


def latest_run():
    """Newest run of any version (version_a/, version_b/, ...); run folders are named by UTC start time."""
    runs = sorted((p for p in LIVE_FEED_RUNS_DIR.glob("*/*") if (p / "raw").is_dir()), key=lambda p: p.name)
    if not runs:
        raise FileNotFoundError(f"No live feed runs in {LIVE_FEED_RUNS_DIR}")
    return runs[-1]


def odds_files(run_dir):
    return sorted((Path(run_dir) / "raw").glob("*_odds.json"))


def market_name(key):
    return (key[len("player_"):] if key.startswith("player_") else key).replace("_", " ").title()


def load_live(run_dir):
    """(meta, events, rows) merged from every odds response in the run so far."""
    files = odds_files(run_dir)
    if not files:
        raise FileNotFoundError(f"No odds pulled yet in {run_dir}")
    latest, event, received = {}, None, None
    for f in files:
        saved = json.loads(f.read_text())
        ev, received = saved["response"], saved["received"]
        event = event or ev
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    latest[(b["key"], m["key"], o.get("description"), o["name"])] = {
                        "bookmaker": b["key"], "bookmaker_title": b.get("title", b["key"]),
                        "market_key": m["key"], "player": o.get("description"), "side": o["name"],
                        "line": o.get("point"), "price": o.get("price"), "last_update": m.get("last_update"),
                    }

    sport = SPORT_ALIAS.get(event["sport_key"], event["sport_key"])
    base = {"sport": sport, "sport_key": event["sport_key"], "event_id": event["id"],
            "commence_time": event["commence_time"], "home_team": event["home_team"],
            "away_team": event["away_team"]}
    rows = [{**base, **r, "market_name": market_name(r["market_key"]),
             "is_alternate": r["market_key"].endswith("_alternate")} for r in latest.values()]
    markets = sorted({r["market_key"] for r in rows})
    events = [{**{k: base[k] for k in ("event_id", "sport", "sport_key", "commence_time", "home_team", "away_team")},
               "prop_markets": markets, "game_lines": []}]
    meta = {
        "pulled_at": received, "source": "live", "sports": [sport], "events": 1, "prop_rows": len(rows),
        "bookmakers": sorted({r["bookmaker"] for r in rows}),
        "market_names": {k: market_name(k) for k in markets},
        "live_run": Path(run_dir).name, "odds_pulls": len(files),
    }
    return meta, events, rows


def log_received(run_dir, odds_file):
    """Record when the dashboard picked up an odds file, next to when the feed received it."""
    saved = json.loads(Path(odds_file).read_text())
    stamps = [m.get("last_update") for b in saved["response"].get("bookmakers", [])
              for m in b.get("markets", []) if m.get("last_update")]
    loaded = datetime.now(timezone.utc)
    newest = max(stamps) if stamps else None
    line = {
        "odds_file": Path(odds_file).name,
        "api_last_update_newest": newest,
        "feed_received": saved["received"],
        "dashboard_loaded": loaded.isoformat(),
        "feed_to_dashboard_s": round((loaded - datetime.fromisoformat(saved["received"])).total_seconds(), 2),
        "api_to_dashboard_s": (round((loaded - datetime.fromisoformat(newest.replace("Z", "+00:00"))).total_seconds(), 2)
                               if newest else None),
    }
    with (Path(run_dir) / "dashboard_received.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
