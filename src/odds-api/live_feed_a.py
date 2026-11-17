#!/usr/bin/env python3
"""
Version A live feed: only pull odds for markets that the API says were updated.

The Odds API has no push/websocket. The cheapest "has it changed?" signal is
GET /events/{id}/markets (1 credit per call per event): it lists every market
each bookmaker offers with its last_update. Each poll:
  1. /markets for the event (1 credit)
  2. for watched markets whose last_update moved for any book, one /events/{id}/odds
     call with just those markets (1 credit per market returned, per region)
  3. diff prices/lines against the last pull to count real odds changes

Caveat found while testing: last_update is when The Odds API last scraped the
book, not when the price changed, so most markets "move" on every poll.
The log records both, so the report can show how many paid fetches had no change.

  python src/odds-api/live_feed_a.py --dry-run
  python src/odds-api/live_feed_a.py --minutes 60 --interval 120 --markets player_points --budget 80

Output: src/odds-api/data/live_feed_a/<run>/polls.jsonl, changes.jsonl, summary.json
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/, for general_utility
sys.path.insert(0, str(Path(__file__).resolve().parent))      # pull_snapshot

from general_utility.create_path import ODDS_API_DATA_DIR, SRC_DIR  # noqa: E402
from pull_snapshot import ApiError, OddsApi, load_dotenv, parse_time  # noqa: E402

SPORT = "basketball_nba"


def now():
    return datetime.now(timezone.utc)


def load_key():
    load_dotenv()
    if not os.environ.get("ODDS_API_KEY"):
        # the key currently lives in src/.env rather than the project root
        alt = SRC_DIR / ".env"
        if alt.is_file():
            for line in alt.read_text(encoding="utf-8-sig").splitlines():
                name, _, value = line.partition("=")
                if name.strip() == "ODDS_API_KEY":
                    os.environ["ODDS_API_KEY"] = value.strip().strip("\"'")
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        sys.exit("No ODDS_API_KEY in .env or src/.env")
    return key


def market_stamps(markets_resp, watched):
    """(book, market) -> last_update, watched markets only."""
    return {(b["key"], m["key"]): m.get("last_update")
            for b in markets_resp.get("bookmakers", []) for m in b.get("markets", [])
            if m["key"] in watched}


def outcome_prices(odds_resp):
    """(book, market, player, side) -> (point, price, market last_update)."""
    out = {}
    for b in odds_resp.get("bookmakers", []):
        for m in b.get("markets", []):
            for o in m.get("outcomes", []):
                key = (b["key"], m["key"], o.get("description", ""), o["name"])
                out[key] = (o.get("point"), o.get("price"), m.get("last_update"))
    return out


def pick_event(api, event_id):
    events = api.get(f"/sports/{SPORT}/events")  # free
    if event_id:
        match = [e for e in events if e["id"] == event_id]
        if not match:
            sys.exit(f"Event {event_id} not found in upcoming NBA events")
        return match[0]
    upcoming = sorted((e for e in events if parse_time(e["commence_time"]) > now()),
                      key=lambda e: e["commence_time"])
    if not upcoming:
        sys.exit("No upcoming NBA events")
    return upcoming[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--event", help="event id (default: next NBA game)")
    ap.add_argument("--markets", default="player_points", help="comma list of prop market keys to keep live")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--interval", type=float, default=120, help="seconds between /markets checks")
    ap.add_argument("--budget", type=int, default=80, help="stop before credits used this run exceed this")
    ap.add_argument("--dry-run", action="store_true", help="show the plan and estimated cost, spend nothing")
    args = ap.parse_args()

    watched = [m.strip() for m in args.markets.split(",") if m.strip()]
    n_regions = len(args.regions.split(","))
    polls = int(args.minutes * 60 // args.interval) + 1
    worst = polls * (1 + len(watched) * n_regions)

    api = OddsApi(load_key())
    ev = pick_event(api, args.event)
    label = f"{ev['away_team']} @ {ev['home_team']} ({ev['commence_time']})"
    print(f"Event:    {label}")
    print(f"Markets:  {', '.join(watched)}  | regions: {args.regions}")
    print(f"Plan:     {polls} polls every {args.interval:.0f}s for {args.minutes:.0f} min")
    print(f"Cost:     {polls} credits for /markets + up to {polls * len(watched) * n_regions} for odds "
          f"(worst case {worst}); budget {args.budget}; credits left on plan: {api.remaining}")
    if args.dry_run:
        return

    run_dir = ODDS_API_DATA_DIR / "live_feed_a" / now().strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    polls_log = (run_dir / "polls.jsonl").open("a", encoding="utf-8")
    changes_log = (run_dir / "changes.jsonl").open("a", encoding="utf-8")

    stamps, prices = {}, {}
    n_fetches = n_wasted = n_changes = n_polls = 0
    latencies = []
    start = now()
    end_at = time.monotonic() + args.minutes * 60
    stop_reason = "finished"

    try:
        while time.monotonic() < end_at:
            tick = time.monotonic()
            if api.credits_this_run + 1 + len(watched) * n_regions > args.budget:
                stop_reason = "budget"
                break
            before = api.credits_this_run
            mk = api.get(f"/sports/{SPORT}/events/{ev['id']}/markets", regions=args.regions)
            markets_cost = api.credits_this_run - before
            new_stamps = market_stamps(mk, set(watched))
            moved = sorted({m for (b, m), ts in new_stamps.items() if stamps.get((b, m)) != ts})
            stamps = new_stamps

            odds_cost, changed_here = 0, 0
            if moved:
                before = api.credits_this_run
                odds = api.get(f"/sports/{SPORT}/events/{ev['id']}/odds", regions=args.regions,
                               markets=",".join(moved), oddsFormat="american")
                received = now()
                odds_cost = api.credits_this_run - before
                n_fetches += 1
                new_prices = outcome_prices(odds)
                for key, (point, price, upd) in new_prices.items():
                    old = prices.get(key)
                    if old is None or old[:2] == (point, price):
                        continue  # first sight, or no change
                    lag = (received - parse_time(upd)).total_seconds() if upd else None
                    if lag is not None:
                        latencies.append(lag)
                    changed_here += 1
                    changes_log.write(json.dumps({
                        "received": received.isoformat(), "book": key[0], "market": key[1],
                        "player": key[2], "side": key[3], "old_point": old[0], "old_price": old[1],
                        "point": point, "price": price, "api_last_update": upd, "latency_s": lag}) + "\n")
                prices.update(new_prices)
                if prices and changed_here == 0 and n_fetches > 1:
                    n_wasted += 1
            n_changes += changed_here
            n_polls += 1
            polls_log.write(json.dumps({
                "at": now().isoformat(), "markets_cost": markets_cost, "moved_markets": moved,
                "odds_cost": odds_cost, "odds_changes": changed_here,
                "credits_this_run": api.credits_this_run, "credits_left": api.remaining}) + "\n")
            polls_log.flush(); changes_log.flush()
            print(f"{now():%H:%M:%S} poll {n_polls}: moved {len(moved)} market(s), "
                  f"{changed_here} odds changes, credits {api.credits_this_run} (left {api.remaining})", flush=True)
            time.sleep(max(0, args.interval - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        stop_reason = "interrupted"
    except ApiError as e:
        stop_reason = f"api error: {e}"

    minutes = (now() - start).total_seconds() / 60
    summary = {
        "version": "A (poll /markets last_update, fetch only moved markets)",
        "event": label, "event_id": ev["id"], "markets_watched": watched, "regions": args.regions,
        "books_seen": len({k[0] for k in prices}),
        "player_lines_tracked": len({k[:3] for k in prices}),
        "started": start.isoformat(), "duration_min": round(minutes, 1), "stop_reason": stop_reason,
        "interval_s": args.interval, "polls": n_polls, "odds_fetches": n_fetches,
        "fetches_with_no_price_change": n_wasted, "odds_changes": n_changes,
        "credits_used": api.credits_this_run,
        "credits_per_hour": round(api.credits_this_run / minutes * 60, 1) if minutes else None,
        "credits_left_on_plan": api.remaining,
        "latency_s": ({"n": len(latencies), "median": statistics.median(latencies),
                       "min": min(latencies), "max": max(latencies)} if latencies else None),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"Logs: {run_dir}")


if __name__ == "__main__":
    main()
