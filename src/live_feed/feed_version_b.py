#!/usr/bin/env python3
"""
Version B live feed: fetch odds on a fixed schedule that tightens as tip-off nears.

  1 minute  refreshes when tip-off is 30 minutes away or less
  5 minute  refreshes when tip-off is 60 minutes away or less
  15 minute refreshes otherwise

Each poll is one GET /events/{id}/odds for all watched markets (1 credit per market
returned, per region). No /markets check: version A showed it never skips a fetch.
The feed stops at tip-off (pre-game props only) or after --minutes, whichever is first.

  python src/live_feed/feed_version_b.py --self-check
  python src/live_feed/feed_version_b.py --dry-run --start-before 60
  python src/live_feed/feed_version_b.py --minutes 60 --start-before 60 --markets player_points,player_rebounds,player_assists --budget 150

Output: src/live_feed/runs/version_b/<run>/ with the same files as version A
        (polls.jsonl, changes.jsonl, summary.json, raw/), so the dashboard's live mode reads it as-is.
"""

import argparse
import json
import statistics
import time
from collections import Counter
from datetime import timedelta

from feed_version_a import (SPORT, load_key, now, outcome_prices, pick_event,  # also sets up sys.path
                            save_raw)
from general_utility.create_path import LIVE_FEED_RUNS_DIR
from pull_snapshot import ApiError, OddsApi, parse_time

TIERS = [(30, 60), (60, 300)]  # (minutes to tip-off or less, seconds between polls)
OTHERWISE_S = 900


def interval_for(minutes_to_tip):
    return next((secs for limit, secs in TIERS if minutes_to_tip <= limit), OTHERWISE_S)


def tier_name(minutes_to_tip):
    return f"{interval_for(minutes_to_tip) // 60} min"


def schedule(start, end, tip):
    """Poll times from start until end or tip-off. A long interval never jumps past a tier boundary."""
    bounds = [tip - timedelta(minutes=limit) for limit, _ in TIERS]
    times, t = [], start
    while t < min(end, tip):
        times.append(t)
        step = t + timedelta(seconds=interval_for((tip - t).total_seconds() / 60))
        t = min([step] + [b for b in bounds if t < b < step])
    return times


def self_check():
    tip = now().replace(microsecond=0)
    mins = lambda times: [round((tip - t).total_seconds() / 60) for t in times]  # noqa: E731
    h = timedelta(hours=1)
    # last hour: 6 polls at 5 min, then 30 at 1 min
    assert mins(schedule(tip - h, tip, tip)) == [60, 55, 50, 45, 40, 35] + list(range(30, 0, -1))
    # 90 to 30 min out: 15 min tier, then snaps to the 60 min boundary
    assert mins(schedule(tip - 1.5 * h, tip - 0.5 * h, tip)) == [90, 75, 60, 55, 50, 45, 40, 35]
    # a 15 min step that would cross the boundary stops on it
    assert mins(schedule(tip - timedelta(minutes=68), tip - timedelta(minutes=50), tip)) == [68, 60, 55]
    assert len(schedule(tip - 6 * h, tip, tip)) == 4 * 5 + 36  # 6 h coverage: 20 + 6 + 30 polls
    assert schedule(tip, tip + h, tip) == []  # nothing after tip-off
    print("self-check ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--event", help="event id (default: next NBA game)")
    ap.add_argument("--markets", default="player_points", help="comma list of prop market keys to keep live")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--start-before", type=float,
                    help="wait until this many minutes before tip-off to start (default: start now)")
    ap.add_argument("--budget", type=int, default=150, help="stop before credits used this run exceed this")
    ap.add_argument("--dry-run", action="store_true", help="show the plan and estimated cost, spend nothing")
    ap.add_argument("--self-check", action="store_true", help="test the schedule logic, no API calls")
    args = ap.parse_args()
    if args.self_check:
        return self_check()

    watched = [m.strip() for m in args.markets.split(",") if m.strip()]
    per_poll = len(watched) * len(args.regions.split(","))

    api = OddsApi(load_key())
    ev = pick_event(api, args.event)
    # shortcut: tip-off is read once at start; re-read the event if a game gets delayed mid-run
    tip = parse_time(ev["commence_time"])
    start = max(now(), tip - timedelta(minutes=args.start_before)) if args.start_before else now()
    plan = schedule(start, start + timedelta(minutes=args.minutes), tip)
    tiers = Counter(tier_name((tip - t).total_seconds() / 60) for t in plan)
    label = f"{ev['away_team']} @ {ev['home_team']} ({ev['commence_time']})"
    print(f"Event:    {label}")
    print(f"Markets:  {', '.join(watched)}  | regions: {args.regions}")
    print(f"Start:    {start:%H:%M:%S} UTC, {(tip - start).total_seconds() / 60:.0f} min before tip-off")
    print(f"Plan:     {len(plan)} polls ({', '.join(f'{n} at {k}' for k, n in tiers.items()) or 'none'})")
    print(f"Cost:     up to {len(plan) * per_poll} credits ({per_poll} per poll); budget {args.budget}; "
          f"credits left on plan: {api.remaining}")
    if args.dry_run or not plan:
        return

    run_dir = LIVE_FEED_RUNS_DIR / "version_b" / start.strftime("%Y%m%dT%H%M%SZ")
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    polls_log = (run_dir / "polls.jsonl").open("a", encoding="utf-8")
    changes_log = (run_dir / "changes.jsonl").open("a", encoding="utf-8")

    prices, latencies = {}, []
    by_tier = {}  # tier -> polls, credits, changes
    n_polls = n_changes = n_unchanged = 0
    stop_reason = "finished"

    def write_summary(status):
        """Totals for the report; rewritten after every poll so a cut-off run still has them."""
        minutes = (now() - start).total_seconds() / 60
        summary = {
            "version": "B (fixed refresh by time to tip-off: 1 min <= 30 min, 5 min <= 60 min, else 15 min)",
            "status": status,
            "event": label, "event_id": ev["id"], "markets_watched": watched, "regions": args.regions,
            "books_seen": len({k[0] for k in prices}),
            "player_lines_tracked": len({k[:3] for k in prices}),
            "started": start.isoformat(), "minutes_before_tip_at_start": round((tip - start).total_seconds() / 60, 1),
            "duration_min": round(minutes, 1), "polls": n_polls, "polls_planned": len(plan),
            "polls_with_no_price_change": n_unchanged, "odds_changes": n_changes,
            "by_tier": by_tier,
            "credits_used": api.credits_this_run,
            "credits_per_hour": round(api.credits_this_run / minutes * 60, 1) if minutes else None,
            "credits_left_on_plan": api.remaining,
            "latency_s": ({"n": len(latencies), "median": statistics.median(latencies),
                           "min": min(latencies), "max": max(latencies)} if latencies else None),
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary

    try:
        for at in plan:
            time.sleep(max(0, (at - now()).total_seconds()))
            if api.credits_this_run + per_poll > args.budget:
                stop_reason = "budget"
                break
            to_tip = (tip - now()).total_seconds() / 60
            tier = tier_name((tip - at).total_seconds() / 60)  # planned time: sleep can wake a hair early
            before = api.credits_this_run
            odds = api.get(f"/sports/{SPORT}/events/{ev['id']}/odds", regions=args.regions,
                           markets=",".join(watched), oddsFormat="american")
            received = now()
            cost = api.credits_this_run - before
            n_polls += 1
            save_raw(run_dir, n_polls, "odds", odds, cost, received)

            changed_here = 0
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
                    "received": received.isoformat(), "tier": tier, "book": key[0], "market": key[1],
                    "player": key[2], "side": key[3], "old_point": old[0], "old_price": old[1],
                    "point": point, "price": price, "api_last_update": upd, "latency_s": lag}) + "\n")
            if prices and changed_here == 0:
                n_unchanged += 1
            prices.update(new_prices)
            n_changes += changed_here
            t = by_tier.setdefault(tier, {"polls": 0, "credits": 0, "odds_changes": 0})
            t["polls"] += 1; t["credits"] += cost; t["odds_changes"] += changed_here

            polls_log.write(json.dumps({
                "at": received.isoformat(), "minutes_to_tip": round(to_tip, 1), "tier": tier,
                "odds_cost": cost, "odds_changes": changed_here,
                "credits_this_run": api.credits_this_run, "credits_left": api.remaining}) + "\n")
            polls_log.flush(); changes_log.flush()
            write_summary("running")
            print(f"{received:%H:%M:%S} poll {n_polls}/{len(plan)} [{tier}, {to_tip:.0f} min to tip]: "
                  f"{changed_here} odds changes, credits {api.credits_this_run} (left {api.remaining})", flush=True)
    except KeyboardInterrupt:
        stop_reason = "interrupted"
    except ApiError as e:
        stop_reason = f"api error: {e}"

    summary = write_summary(stop_reason)
    print(json.dumps(summary, indent=2))
    print(f"Logs: {run_dir}")


if __name__ == "__main__":
    main()
