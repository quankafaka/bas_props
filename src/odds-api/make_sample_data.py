#!/usr/bin/env python3
"""
Generate a realistic SAMPLE odds snapshot, in exactly the same format as pull_snapshot.py.

Use this to build and rehearse the dashboard demo without an API key or credits.
Teams are real; player names are invented, and meta.json is marked source="sample",
so the dashboard shows a "Sample data" badge.

Usage:
  python make_sample_data.py            # writes to odds-api/data/
  python make_sample_data.py --seed 7   # different players and prices
  python make_sample_data.py --force    # overwrite a real snapshot (normally refused)
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from general_utility.create_path import ODDS_API_DATA_DIR, ensure_dir, rel  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pull_snapshot import SPORTS, implied_prob  # noqa: E402

BOOKS = [
    ("draftkings", "DraftKings"), ("fanduel", "FanDuel"), ("betmgm", "BetMGM"),
    ("williamhill_us", "Caesars"), ("espnbet", "ESPN BET"), ("fanatics", "Fanatics"),
]

GAMES = {
    "nfl": [("Kansas City Chiefs", "Buffalo Bills", 26), ("Dallas Cowboys", "Philadelphia Eagles", 27),
            ("Cincinnati Bengals", "Baltimore Ravens", 27), ("Los Angeles Rams", "San Francisco 49ers", 28)],
    "ncaaf": [("Alabama Crimson Tide", "Georgia Bulldogs", 26), ("Oklahoma Sooners", "Texas Longhorns", 26)],
    "mlb": [("New York Yankees", "Boston Red Sox", 24), ("San Diego Padres", "Los Angeles Dodgers", 24),
            ("Milwaukee Brewers", "Chicago Cubs", 25)],
    "epl": [("Arsenal", "Chelsea", 26), ("Liverpool", "Manchester City", 27)],
}
KICKOFF_HOUR = {"nfl": 17, "ncaaf": 19, "mlb": 23, "epl": 14}

FIRST = ["Marcus", "Jalen", "Tyler", "Devon", "Andre", "Caleb", "Isaiah", "Dominic", "Trevor", "Malik",
         "Brandon", "Xavier", "Cole", "Jordan", "Nate", "Derrick", "Owen", "Luis", "Mateo", "Rafael",
         "Kenji", "Luka", "Theo", "Samuel", "Julian", "Evan", "Rashad", "Brooks", "Carter", "Emeka",
         "Diego", "Tomas", "Felix", "Ruben", "Callum", "Aaron", "Miles", "Grant", "Hunter", "Tobias"]
LAST = ["Whitfield", "Okafor", "Brennan", "Castillo", "Harlow", "Pruitt", "Delgado", "Ashby", "Vance",
        "Mercer", "Kowalski", "Oduya", "Lindqvist", "Marchetti", "Tavares", "Holloway", "Ramsey",
        "Nakamura", "Petrovic", "Fairbanks", "Abernathy", "Sutton", "Moreau", "Kinsley", "Barrett",
        "Oyelaran", "Stroud", "Faulkner", "Quintero", "Hayward", "Albright", "Dunmore", "Ellison",
        "Garrity", "Ishikawa", "Lockhart", "Montague", "Norwood", "Pemberton", "Rourke"]

# Each spec: (market_key, base_line or None for yes-only, step, prob_of_over_or_yes, kind)
# kind: "ou" = Over/Under, "yes" = Yes only, "yn" = Yes/No
FOOTBALL_ROLES = {
    "QB": [("player_pass_yds", 245.5, 5, 0.5, "ou"), ("player_pass_tds", 1.5, 0, 0.52, "ou"),
           ("player_pass_attempts", 33.5, 1, 0.5, "ou"), ("player_pass_completions", 22.5, 1, 0.5, "ou"),
           ("player_pass_interceptions", 0.5, 0, 0.44, "ou"), ("player_rush_yds", 16.5, 2, 0.5, "ou"),
           ("player_anytime_td", None, 0, 0.16, "yes")],
    "RB": [("player_rush_yds", 64.5, 4, 0.5, "ou"), ("player_rush_attempts", 15.5, 1, 0.5, "ou"),
           ("player_receptions", 2.5, 0, 0.5, "ou"), ("player_reception_yds", 18.5, 2, 0.5, "ou"),
           ("player_anytime_td", None, 0, 0.44, "yes")],
    "WR1": [("player_reception_yds", 71.5, 5, 0.5, "ou"), ("player_receptions", 5.5, 0, 0.5, "ou"),
            ("player_reception_longest", 22.5, 2, 0.5, "ou"), ("player_anytime_td", None, 0, 0.38, "yes")],
    "WR2": [("player_reception_yds", 52.5, 4, 0.5, "ou"), ("player_receptions", 4.5, 0, 0.5, "ou"),
            ("player_anytime_td", None, 0, 0.29, "yes")],
    "TE": [("player_reception_yds", 38.5, 3, 0.5, "ou"), ("player_receptions", 3.5, 0, 0.5, "ou"),
           ("player_anytime_td", None, 0, 0.25, "yes")],
    "K": [("player_kicking_points", 7.5, 0, 0.5, "ou"), ("player_field_goals", 1.5, 0, 0.52, "ou")],
    "LB": [("player_solo_tackles", 4.5, 0, 0.5, "ou"), ("player_tackles_assists", 7.5, 0, 0.5, "ou")],
    "EDGE": [("player_sacks", 0.5, 0, 0.42, "ou")],
}
BASEBALL_ROLES = {
    "SP": [("pitcher_strikeouts", 5.5, 1, 0.5, "ou"), ("pitcher_outs", 17.5, 1, 0.5, "ou"),
           ("pitcher_hits_allowed", 5.5, 1, 0.5, "ou"), ("pitcher_earned_runs", 2.5, 0, 0.48, "ou"),
           ("pitcher_walks", 1.5, 0, 0.5, "ou"), ("pitcher_record_a_win", None, 0, 0.45, "yn")],
    "BAT": [("batter_hits", 0.5, 0, 0.64, "ou"), ("batter_total_bases", 1.5, 0, 0.42, "ou"),
            ("batter_home_runs", 0.5, 0, 0.17, "ou"), ("batter_rbis", 0.5, 0, 0.38, "ou"),
            ("batter_hits_runs_rbis", 1.5, 0, 0.5, "ou"), ("batter_runs_scored", 0.5, 0, 0.42, "ou")],
}
SOCCER_ROLES = {
    "ST": [("player_goal_scorer_anytime", None, 0, 0.38, "yes"), ("player_shots_on_target", 0.5, 0, 0.62, "ou"),
           ("player_shots", 2.5, 0, 0.45, "ou"), ("player_to_receive_card", None, 0, 0.14, "yes")],
    "W": [("player_goal_scorer_anytime", None, 0, 0.27, "yes"), ("player_shots_on_target", 0.5, 0, 0.5, "ou"),
          ("player_shots", 1.5, 0, 0.55, "ou")],
    "CM": [("player_goal_scorer_anytime", None, 0, 0.12, "yes"), ("player_shots", 1.5, 0, 0.42, "ou"),
           ("player_to_receive_card", None, 0, 0.24, "yes"), ("player_assists", 0.5, 0, 0.2, "ou")],
    "CB": [("player_to_receive_card", None, 0, 0.2, "yes")],
}
ROSTERS = {
    "nfl": (FOOTBALL_ROLES, ["QB", "RB", "WR1", "WR2", "WR2", "TE", "K", "LB", "EDGE"]),
    "ncaaf": (FOOTBALL_ROLES, ["QB", "RB", "WR1", "WR2", "TE"]),
    "mlb": (BASEBALL_ROLES, ["SP", "BAT", "BAT", "BAT", "BAT", "BAT"]),
    "epl": (SOCCER_ROLES, ["ST", "W", "W", "CM", "CB"]),
}
BOOKS_PER_SPORT = {"nfl": 6, "ncaaf": 4, "mlb": 5, "epl": 4}


def to_american(p):
    p = min(max(p, 0.02), 0.98)
    a = -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p
    a = int(round(a / 5.0) * 5)
    if -100 < a < 100:
        a = 100 if a >= 0 else -105
    return a


def main():
    ap = argparse.ArgumentParser(description="Write a sample odds snapshot for the dashboard demo.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--force", action="store_true", help="overwrite even if a real snapshot exists")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    out = ensure_dir(ODDS_API_DATA_DIR)
    meta_path = out / "meta.json"
    if meta_path.exists() and not args.force:
        existing = json.loads(meta_path.read_text())
        if existing.get("source") != "sample":
            sys.exit(f"{rel(meta_path)} is from a real API pull. Use --force to overwrite it with sample data.")

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    used_names = set()

    def new_player():
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in used_names:
                used_names.add(name)
                return name

    rows, events = [], []
    for sport, games in GAMES.items():
        sport_key, _, market_names, _ = SPORTS[sport]
        roles, lineup = ROSTERS[sport]
        books = BOOKS[:BOOKS_PER_SPORT[sport]]
        for home, away, day in games:
            start = now.replace(day=1, hour=KICKOFF_HOUR[sport], minute=0) + timedelta(days=day - 1)
            if start <= now:
                start = now + timedelta(days=1, hours=rng.randint(1, 6))
            commence = start.strftime("%Y-%m-%dT%H:%M:%SZ")
            event_id = "%032x" % rng.getrandbits(128)
            ev_markets = set()

            for team in (home, away):
                for role in lineup:
                    player = new_player()
                    for market, base_line, step, base_p, kind in roles[role]:
                        # Per-player "true" line/probability, then each book adds its own view + margin
                        line = None
                        if base_line is not None:
                            jitter = rng.choice([-2, -1, 0, 0, 1, 2]) * step if step else 0
                            line = round(base_line + jitter, 1)
                        spread_p = min(0.06, base_p * 0.25, (1 - base_p) * 0.25)
                        true_p = min(max(base_p + rng.gauss(0, spread_p), 0.05, base_p * 0.7), 0.9)

                        for bkey, btitle in books:
                            if rng.random() > 0.85:
                                continue  # not every book posts every prop
                            b_line, b_p = line, true_p + rng.gauss(0, 0.015 * min(1.0, true_p * 4, (1 - true_p) * 4))
                            if line is not None and step and rng.random() < 0.25:
                                shift = rng.choice([-1, 1]) * step
                                b_line = round(line + shift, 1)
                                b_p -= 0.04 * shift / step
                            b_p = min(max(b_p, 0.04), 0.94)
                            if kind == "ou":
                                hold = rng.uniform(0.04, 0.065)
                                prices = [("Over", to_american(b_p * (1 + hold))),
                                          ("Under", to_american((1 - b_p) * (1 + hold)))]
                            elif kind == "yn":
                                hold = rng.uniform(0.045, 0.07)
                                prices = [("Yes", to_american(b_p * (1 + hold))),
                                          ("No", to_american((1 - b_p) * (1 + hold)))]
                            else:
                                prices = [("Yes", to_american(b_p * (1 + rng.uniform(0.08, 0.14))))]
                            for side, price in prices:
                                rows.append({
                                    "sport": sport, "sport_key": sport_key, "event_id": event_id,
                                    "commence_time": commence, "home_team": home, "away_team": away,
                                    "bookmaker": bkey, "bookmaker_title": btitle,
                                    "market_key": market, "market_name": market_names.get(market, market),
                                    "is_alternate": False, "player": player, "side": side,
                                    "line": b_line, "price": price, "implied_prob": implied_prob(price),
                                    "last_update": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                })
                            ev_markets.add(market)

            # Game lines for context
            home_p = min(max(0.5 + rng.gauss(0, 0.12), 0.2), 0.8)
            if sport == "mlb":
                spread = -1.5 if home_p >= 0.5 else 1.5          # run line
            elif sport == "epl":
                spread = round((home_p - 0.5) * -6 * 4) / 4 or -0.25  # Asian handicap
            else:
                spread = round((home_p - 0.5) * (-28 if sport == "nfl" else -40) * 2) / 2 or -1.5
            total = {"nfl": 45.5, "ncaaf": 52.5, "mlb": 8.5, "epl": 2.5}[sport]
            total += rng.choice([-2, -1, 0, 1, 2]) * (0.5 if sport == "mlb" else 1) if sport != "epl" else rng.choice([0, 0, 0.5])
            game_lines = []
            for bkey, _ in books:
                hp = home_p + rng.gauss(0, 0.01)
                game_lines += [
                    {"bookmaker": bkey, "market": "h2h", "side": home, "line": None, "price": to_american(hp * 1.045)},
                    {"bookmaker": bkey, "market": "h2h", "side": away, "line": None, "price": to_american((1 - hp) * 1.045)},
                    {"bookmaker": bkey, "market": "spreads", "side": home, "line": spread, "price": -110},
                    {"bookmaker": bkey, "market": "spreads", "side": away, "line": -spread, "price": -110},
                    {"bookmaker": bkey, "market": "totals", "side": "Over", "line": total, "price": -110},
                    {"bookmaker": bkey, "market": "totals", "side": "Under", "line": total, "price": -110},
                ]
            events.append({"event_id": event_id, "sport": sport, "sport_key": sport_key,
                           "commence_time": commence, "home_team": home, "away_team": away,
                           "prop_markets": sorted(ev_markets), "game_lines": game_lines})

    market_catalog = {}
    for sport in GAMES:
        market_catalog.update(SPORTS[sport][2])
    meta = {
        "pulled_at": now.isoformat(),
        "source": "sample",
        "sports": list(GAMES),
        "events": len(events),
        "prop_rows": len(rows),
        "bookmakers": sorted({r["bookmaker"] for r in rows}),
        "credits_used_this_run": 0,
        "credits_remaining": None,
        "api_calls": 0,
        "market_names": market_catalog,
    }
    (out / "events.json").write_text(json.dumps(events, indent=2))
    (out / "props.json").write_text(json.dumps(rows, indent=2))
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"Sample snapshot: {len(events)} games, {len(rows):,} prop prices from {len(meta['bookmakers'])} books")
    print(f"Saved to: {rel(out)}")


if __name__ == "__main__":
    main()
