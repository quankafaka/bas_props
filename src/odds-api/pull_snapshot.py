#!/usr/bin/env python3
"""
One-time snapshot pull from The Odds API for the Trader Props Dashboard demo.

Credit-efficient flow:
  1. /sports                       -> free   (which sports are in season)
  2. /sports/{s}/events            -> free   (upcoming games)
  3. /events/{id}/markets          -> 1 credit per event (which prop markets books actually offer)
  4. cost preview + confirmation   -> you approve before any odds are pulled
  5. /sports/{s}/odds (game lines) -> ~3 credits per sport (h2h, spreads, totals)
  6. /events/{id}/odds (props)     -> markets returned x regions, per event

Output (default: data/ folder next to this script):
  meta.json     pull time, sports, credits used/remaining
  events.json   games + game lines + which prop markets are available
  props.json    flat rows: one row per bookmaker x market x player x side
  props.csv     same rows, for Excel
  raw/          untouched API responses, for reference

Usage:
  export ODDS_API_KEY=your_key
  python pull_snapshot.py --dry-run               # discover + cost preview only
  python pull_snapshot.py                          # default sports, 3 games each
  python pull_snapshot.py --sports nfl,mlb --max-events 5 --budget 1500
  python pull_snapshot.py --include-alternates     # adds alt lines (much more expensive)

Standard library only (Python 3.8+). No pip install needed.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Make src/ importable so shared utilities load however this script is launched
# (terminal, PyCharm, VS Code) and from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from general_utility.create_path import ENV_FILE, ODDS_API_DATA_DIR, ensure_dir, rel  # noqa: E402

BASE_URL = "https://api.the-odds-api.com/v4"
GAME_LINE_MARKETS = ["h2h", "spreads", "totals"]

# ---------------------------------------------------------------------------
# Market catalogue (key -> display name), taken from The Odds API docs
# ---------------------------------------------------------------------------

FOOTBALL_PROPS = {
    "player_assists": "Assists",
    "player_defensive_interceptions": "Defensive Interceptions",
    "player_field_goals": "Field Goals",
    "player_kicking_points": "Kicking Points",
    "player_pass_attempts": "Pass Attempts",
    "player_pass_completions": "Pass Completions",
    "player_pass_interceptions": "Pass Interceptions",
    "player_pass_longest_completion": "Longest Pass Completion",
    "player_pass_rush_yds": "Pass + Rush Yards",
    "player_pass_rush_reception_tds": "Pass + Rush + Reception TDs",
    "player_pass_rush_reception_yds": "Pass + Rush + Reception Yards",
    "player_pass_tds": "Pass TDs",
    "player_pass_yds": "Pass Yards",
    "player_pass_yds_q1": "1st Quarter Pass Yards",
    "player_pats": "Points After Touchdown",
    "player_receptions": "Receptions",
    "player_reception_longest": "Longest Reception",
    "player_reception_tds": "Reception TDs",
    "player_reception_yds": "Reception Yards",
    "player_rush_attempts": "Rush Attempts",
    "player_rush_longest": "Longest Rush",
    "player_rush_reception_tds": "Rush + Reception TDs",
    "player_rush_reception_yds": "Rush + Reception Yards",
    "player_rush_tds": "Rush TDs",
    "player_rush_yds": "Rush Yards",
    "player_sacks": "Sacks",
    "player_solo_tackles": "Solo Tackles",
    "player_tackles_assists": "Tackles + Assists",
    "player_tds_over": "Touchdowns (Over only)",
    "player_tds": "Touchdowns",
    "player_1st_td": "1st TD Scorer",
    "player_anytime_td": "Anytime TD Scorer",
    "player_last_td": "Last TD Scorer",
}
FOOTBALL_ALTS = {
    f"{k}_alternate": f"Alt {v}"
    for k, v in FOOTBALL_PROPS.items()
    if k not in {"player_defensive_interceptions", "player_pass_yds_q1", "player_tds_over",
                 "player_tds", "player_1st_td", "player_anytime_td", "player_last_td"}
}

BASKETBALL_PROPS = {
    "player_points": "Points",
    "player_points_q1": "1st Quarter Points",
    "player_rebounds": "Rebounds",
    "player_rebounds_q1": "1st Quarter Rebounds",
    "player_assists": "Assists",
    "player_assists_q1": "1st Quarter Assists",
    "player_threes": "Threes",
    "player_blocks": "Blocks",
    "player_steals": "Steals",
    "player_blocks_steals": "Blocks + Steals",
    "player_turnovers": "Turnovers",
    "player_points_rebounds_assists": "Points + Rebounds + Assists",
    "player_points_rebounds": "Points + Rebounds",
    "player_points_assists": "Points + Assists",
    "player_rebounds_assists": "Rebounds + Assists",
    "player_field_goals": "Field Goals",
    "player_frees_made": "Free Throws Made",
    "player_frees_attempts": "Free Throws Attempted",
    "player_first_basket": "First Basket Scorer",
    "player_first_team_basket": "First Team Basket Scorer",
    "player_double_double": "Double Double",
    "player_triple_double": "Triple Double",
    "player_method_of_first_basket": "Method of First Basket",
}
BASKETBALL_ALTS = {
    f"{k}_alternate": f"Alt {BASKETBALL_PROPS[k]}"
    for k in ["player_points", "player_rebounds", "player_assists", "player_blocks",
              "player_steals", "player_turnovers", "player_threes", "player_points_assists",
              "player_points_rebounds", "player_rebounds_assists",
              "player_points_rebounds_assists"]
}

BASEBALL_PROPS = {
    "batter_home_runs": "Batter Home Runs",
    "batter_first_home_run": "Batter First Home Run",
    "batter_hits": "Batter Hits",
    "batter_total_bases": "Batter Total Bases",
    "batter_rbis": "Batter RBIs",
    "batter_runs_scored": "Batter Runs Scored",
    "batter_hits_runs_rbis": "Batter Hits + Runs + RBIs",
    "batter_singles": "Batter Singles",
    "batter_doubles": "Batter Doubles",
    "batter_triples": "Batter Triples",
    "batter_walks": "Batter Walks",
    "batter_strikeouts": "Batter Strikeouts",
    "batter_stolen_bases": "Batter Stolen Bases",
    "pitcher_strikeouts": "Pitcher Strikeouts",
    "pitcher_record_a_win": "Pitcher to Record a Win",
    "pitcher_hits_allowed": "Pitcher Hits Allowed",
    "pitcher_walks": "Pitcher Walks",
    "pitcher_earned_runs": "Pitcher Earned Runs",
    "pitcher_outs": "Pitcher Outs",
}
BASEBALL_ALTS = {
    f"{k}_alternate": f"Alt {BASEBALL_PROPS[k]}"
    for k in ["batter_total_bases", "batter_home_runs", "batter_hits", "batter_rbis",
              "batter_walks", "batter_strikeouts", "batter_runs_scored",
              "batter_hits_runs_rbis", "batter_singles", "batter_doubles", "batter_triples",
              "pitcher_hits_allowed", "pitcher_walks", "pitcher_earned_runs",
              "pitcher_strikeouts", "pitcher_outs"]
}

HOCKEY_PROPS = {
    "player_points": "Points",
    "player_power_play_points": "Power Play Points",
    "player_assists": "Assists",
    "player_blocked_shots": "Blocked Shots",
    "player_shots_on_goal": "Shots on Goal",
    "player_goals": "Goals",
    "player_total_saves": "Total Saves",
    "player_goal_scorer_first": "First Goal Scorer",
    "player_goal_scorer_last": "Last Goal Scorer",
    "player_goal_scorer_anytime": "Anytime Goal Scorer",
}
HOCKEY_ALTS = {
    f"{k}_alternate": f"Alt {HOCKEY_PROPS[k]}"
    for k in ["player_points", "player_assists", "player_power_play_points", "player_goals",
              "player_shots_on_goal", "player_blocked_shots", "player_total_saves"]
}

AFL_PROPS = {
    "player_disposals": "Disposals",
    "player_disposals_over": "Disposals (Over only)",
    "player_goal_scorer_first": "First Goal Scorer",
    "player_goal_scorer_last": "Last Goal Scorer",
    "player_goal_scorer_anytime": "Anytime Goal Scorer",
    "player_goals_scored_over": "Goals Scored (Over only)",
    "player_marks_over": "Marks (Over only)",
    "player_marks_most": "Most Marks",
    "player_tackles_over": "Tackles (Over only)",
    "player_tackles_most": "Most Tackles",
    "player_afl_fantasy_points": "AFL Fantasy Points",
    "player_afl_fantasy_points_over": "AFL Fantasy Points (Over only)",
    "player_afl_fantasy_points_most": "Most AFL Fantasy Points",
    "player_clearances_over": "Clearances (Over only)",
    "player_kicks_over": "Kicks (Over only)",
    "player_handballs_over": "Handballs (Over only)",
}

NRL_PROPS = {
    "player_try_scorer_first": "First Try Scorer",
    "player_try_scorer_last": "Last Try Scorer",
    "player_try_scorer_anytime": "Anytime Try Scorer",
    "player_try_scorer_over": "Tries Scored (Over only)",
}

SOCCER_PROPS = {
    "player_goal_scorer_anytime": "Anytime Goal Scorer",
    "player_first_goal_scorer": "First Goal Scorer",
    "player_last_goal_scorer": "Last Goal Scorer",
    "player_to_receive_card": "To Receive a Card",
    "player_to_receive_red_card": "To Receive a Red Card",
    "player_shots_on_target": "Shots on Target",
    "player_shots": "Shots",
    "player_assists": "Assists",
}

# alias -> (sport_key, region, props, alternates)
SPORTS = {
    "nfl":        ("americanfootball_nfl", "us", FOOTBALL_PROPS, FOOTBALL_ALTS),
    "ncaaf":      ("americanfootball_ncaaf", "us", FOOTBALL_PROPS, FOOTBALL_ALTS),
    "cfl":        ("americanfootball_cfl", "us", FOOTBALL_PROPS, FOOTBALL_ALTS),
    "nba":        ("basketball_nba", "us", BASKETBALL_PROPS, BASKETBALL_ALTS),
    "ncaab":      ("basketball_ncaab", "us", BASKETBALL_PROPS, BASKETBALL_ALTS),
    "wnba":       ("basketball_wnba", "us", BASKETBALL_PROPS, BASKETBALL_ALTS),
    "mlb":        ("baseball_mlb", "us", BASEBALL_PROPS, BASEBALL_ALTS),
    "nhl":        ("icehockey_nhl", "us", HOCKEY_PROPS, HOCKEY_ALTS),
    "afl":        ("aussierules_afl", "au", AFL_PROPS, {}),
    "nrl":        ("rugbyleague_nrl", "au", NRL_PROPS, {}),
    "epl":        ("soccer_epl", "us", SOCCER_PROPS, {}),
    "laliga":     ("soccer_spain_la_liga", "us", SOCCER_PROPS, {}),
    "bundesliga": ("soccer_germany_bundesliga", "us", SOCCER_PROPS, {}),
    "seriea":     ("soccer_italy_serie_a", "us", SOCCER_PROPS, {}),
    "ligue1":     ("soccer_france_ligue_one", "us", SOCCER_PROPS, {}),
    "mls":        ("soccer_usa_mls", "us", SOCCER_PROPS, {}),
}
DEFAULT_SPORTS = "nfl,ncaaf,mlb,epl"


# ---------------------------------------------------------------------------
# API client with credit tracking
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def _fetch(url):
    """Network call, isolated so it can be mocked in tests. Returns (status, headers, body)."""
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "props-dashboard-demo"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()


class OddsApi:
    def __init__(self, api_key):
        self.api_key = api_key
        self.credits_this_run = 0
        self.remaining = None
        self.used_total = None
        self.call_count = 0

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        params["apiKey"] = self.api_key
        url = f"{BASE_URL}{path}?{urlencode(params)}"
        for attempt in range(5):
            try:
                status, headers, body = _fetch(url)
            except URLError as e:
                if attempt == 4:
                    raise ApiError("network", str(e.reason))
                time.sleep(2 ** attempt)
                continue
            if status == 429:  # rate limited, back off and retry
                time.sleep(2 ** attempt)
                continue
            if status != 200:
                text = body.decode("utf-8", errors="replace")
                try:
                    text = json.loads(text).get("message", text)
                except (ValueError, AttributeError):
                    pass
                raise ApiError(status, text)
            self.call_count += 1
            self.credits_this_run += int(headers.get("x-requests-last", 0) or 0)
            if headers.get("x-requests-remaining") is not None:
                self.remaining = int(float(headers["x-requests-remaining"]))
            if headers.get("x-requests-used") is not None:
                self.used_total = int(float(headers["x-requests-used"]))
            return json.loads(body)
        raise ApiError(429, "still rate limited after retries")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def implied_prob(american):
    if american is None:
        return None
    american = float(american)
    if american > 0:
        return round(100 / (american + 100), 4)
    return round(-american / (-american + 100), 4)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def parse_time(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def mask(key):
    """Show enough of the key to verify it without exposing it."""
    if len(key) <= 8:
        return f"'{'*' * len(key)}' ({len(key)} chars)"
    return f"'{key[:4]}...{key[-4:]}' ({len(key)} chars)"


def log(msg=""):
    print(msg, flush=True)


def load_dotenv():
    """Load KEY=VALUE pairs from the project's .env (path from create_path).
    Doesn't override variables already set in the environment."""
    if not ENV_FILE.is_file():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)
    return ENV_FILE


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Pull a one-time odds snapshot for the props dashboard demo.")
    ap.add_argument("--sports", default=DEFAULT_SPORTS,
                    help=f"comma list from: {', '.join(SPORTS)} (default: {DEFAULT_SPORTS})")
    ap.add_argument("--max-events", type=int, default=3, help="games per sport (default 3)")
    ap.add_argument("--days-ahead", type=int, default=7, help="only games starting within N days (default 7)")
    ap.add_argument("--include-alternates", action="store_true", help="also pull alternate prop lines")
    ap.add_argument("--budget", type=int, default=1000, help="abort if estimated credits exceed this (default 1000)")
    ap.add_argument("--dry-run", action="store_true", help="discover markets + show cost, pull no odds")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--out", default=None,
                    help="output folder (default: ODDS_API_DATA_DIR from create_path)")
    ap.add_argument("--api-key", default=None, help="overrides ODDS_API_KEY from env / .env")
    args = ap.parse_args()

    already_in_env = bool(os.environ.get("ODDS_API_KEY"))
    env_file = load_dotenv()
    if args.api_key:
        key_source = "--api-key flag"
    elif already_in_env:
        key_source = "environment variable (this takes priority over .env)"
    else:
        key_source = rel(ENV_FILE)
    args.api_key = (args.api_key or os.environ.get("ODDS_API_KEY") or "").strip()
    if not args.api_key:
        where = (f" ({rel(ENV_FILE)} has no ODDS_API_KEY)" if env_file
                 else f" (no .env at {ENV_FILE})")
        sys.exit(f"No API key{where}. Add ODDS_API_KEY=... to .env or pass --api-key.")
    if env_file:
        log(f"Loaded settings from {rel(env_file)}")
    log(f"Using API key {mask(args.api_key)} from {key_source}")
    if any(c in args.api_key for c in " \"'<>"):
        log("  ! The key contains spaces, quotes or <> characters. It probably wasn't pasted cleanly.")

    aliases = [s.strip().lower() for s in args.sports.split(",") if s.strip()]
    unknown = [a for a in aliases if a not in SPORTS]
    if unknown:
        sys.exit(f"Unknown sport(s): {unknown}. Choose from: {', '.join(SPORTS)}")

    api = OddsApi(args.api_key)
    out = ensure_dir(Path(args.out) if args.out else ODDS_API_DATA_DIR)
    raw = out / "raw"
    now = datetime.now(timezone.utc)

    # 1. Which sports are in season (free)
    log("Checking which sports are in season...")
    active = {s["key"] for s in api.get("/sports")}
    selected = []
    for a in aliases:
        sport_key = SPORTS[a][0]
        if sport_key in active:
            selected.append(a)
        else:
            log(f"  - {a} ({sport_key}) is not in season, skipping")
    if not selected:
        sys.exit("None of the selected sports are in season.")

    # 2 + 3. Upcoming events (free) and their available markets (1 credit each)
    plan = []  # list of dicts: alias, sport_key, region, event, markets_to_pull
    for a in selected:
        sport_key, region, props, alts = SPORTS[a]
        wanted = dict(props)
        if args.include_alternates:
            wanted.update(alts)

        events = api.get(f"/sports/{sport_key}/events", dateFormat="iso")
        upcoming = sorted(
            (e for e in events
             if now < parse_time(e["commence_time"]) and
             (parse_time(e["commence_time"]) - now).days < args.days_ahead),
            key=lambda e: e["commence_time"],
        )[: args.max_events]
        log(f"\n{a.upper()}: {len(upcoming)} upcoming game(s) selected")

        for ev in upcoming:
            try:
                mk = api.get(f"/sports/{sport_key}/events/{ev['id']}/markets", regions=region)
            except ApiError as e:
                log(f"  ! markets lookup failed for {ev['away_team']} @ {ev['home_team']}: {e}")
                continue
            save_json(raw / a / f"{ev['id']}_markets.json", mk)
            offered = {m["key"] for b in mk.get("bookmakers", []) for m in b.get("markets", [])}
            to_pull = sorted(k for k in offered if k in wanted)
            books = len(mk.get("bookmakers", []))
            log(f"  {ev['away_team']} @ {ev['home_team']}  "
                f"({ev['commence_time'][:16].replace('T', ' ')} UTC): "
                f"{len(to_pull)} prop markets across {books} book(s)")
            plan.append({"alias": a, "sport_key": sport_key, "region": region,
                         "event": ev, "markets": to_pull, "wanted": wanted})

    # 4. Cost preview
    prop_cost = sum(len(p["markets"]) for p in plan)  # 1 region per sport
    line_cost = len(GAME_LINE_MARKETS) * len(selected)
    estimate = prop_cost + line_cost
    log("\n" + "-" * 60)
    log(f"Market discovery spent: {api.credits_this_run} credits")
    log(f"Estimated cost to pull odds: ~{estimate} credits "
        f"({prop_cost} props + {line_cost} game lines). Upper bound; unreturned markets aren't charged.")
    if api.remaining is not None:
        log(f"Credits remaining on your plan: {api.remaining:,}")
    log("-" * 60)

    if args.dry_run:
        log("Dry run: stopping before pulling odds.")
        return
    if estimate > args.budget:
        sys.exit(f"Estimate {estimate} exceeds --budget {args.budget}. "
                 f"Lower --max-events or raise --budget.")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        log("Cancelled.")
        return

    # 5. Game lines, one call per sport (h2h/spreads/totals for the selected events)
    lines_by_event = {}
    for a in selected:
        sport_key, region = SPORTS[a][0], SPORTS[a][1]
        ids = [p["event"]["id"] for p in plan if p["alias"] == a]
        if not ids:
            continue
        try:
            data = api.get(f"/sports/{sport_key}/odds", regions=region,
                           markets=",".join(GAME_LINE_MARKETS), oddsFormat="american",
                           dateFormat="iso", eventIds=",".join(ids))
            save_json(raw / a / "game_lines.json", data)
            for ev in data:
                lines_by_event[ev["id"]] = ev.get("bookmakers", [])
        except ApiError as e:
            log(f"  ! game lines failed for {a}: {e}")

    # 6. Player props per event
    rows, events_out = [], []
    for p in plan:
        ev, a = p["event"], p["alias"]
        odds = {"bookmakers": []}
        if p["markets"]:
            try:
                odds = api.get(f"/sports/{p['sport_key']}/events/{ev['id']}/odds",
                               regions=p["region"], markets=",".join(p["markets"]),
                               oddsFormat="american", dateFormat="iso")
                save_json(raw / a / f"{ev['id']}_odds.json", odds)
            except ApiError as e:
                log(f"  ! odds failed for {ev['away_team']} @ {ev['home_team']}: {e}")

        markets_found = set()
        for bm in odds.get("bookmakers", []):
            for m in bm.get("markets", []):
                markets_found.add(m["key"])
                for o in m.get("outcomes", []):
                    rows.append({
                        "sport": a,
                        "sport_key": p["sport_key"],
                        "event_id": ev["id"],
                        "commence_time": ev["commence_time"],
                        "home_team": ev["home_team"],
                        "away_team": ev["away_team"],
                        "bookmaker": bm["key"],
                        "bookmaker_title": bm["title"],
                        "market_key": m["key"],
                        "market_name": p["wanted"].get(m["key"], m["key"]),
                        "is_alternate": m["key"].endswith("_alternate"),
                        "player": o.get("description"),
                        "side": o.get("name"),
                        "line": o.get("point"),
                        "price": o.get("price"),
                        "implied_prob": implied_prob(o.get("price")),
                        "last_update": m.get("last_update") or bm.get("last_update"),
                    })

        game_lines = []
        for bm in lines_by_event.get(ev["id"], []):
            for m in bm.get("markets", []):
                for o in m.get("outcomes", []):
                    game_lines.append({"bookmaker": bm["key"], "market": m["key"],
                                       "side": o.get("name"), "line": o.get("point"),
                                       "price": o.get("price")})

        events_out.append({
            "event_id": ev["id"], "sport": a, "sport_key": p["sport_key"],
            "commence_time": ev["commence_time"],
            "home_team": ev["home_team"], "away_team": ev["away_team"],
            "prop_markets": sorted(markets_found),
            "game_lines": game_lines,
        })
        log(f"  {a.upper()} {ev['away_team']} @ {ev['home_team']}: "
            f"{len(markets_found)} markets, {sum(r['event_id'] == ev['id'] for r in rows)} prices")

    # 7. Write outputs
    save_json(out / "events.json", events_out)
    save_json(out / "props.json", rows)
    if rows:
        with open(out / "props.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    market_catalog = {}
    for a in selected:
        market_catalog.update(SPORTS[a][2])
        market_catalog.update(SPORTS[a][3])
    meta = {
        "pulled_at": now.isoformat(),
        "source": "api",
        "sports": selected,
        "events": len(events_out),
        "prop_rows": len(rows),
        "bookmakers": sorted({r["bookmaker"] for r in rows}),
        "credits_used_this_run": api.credits_this_run,
        "credits_remaining": api.remaining,
        "api_calls": api.call_count,
        "market_names": market_catalog,
    }
    save_json(out / "meta.json", meta)

    log("\n" + "=" * 60)
    log(f"Done. {len(events_out)} games, {len(rows):,} prop prices from "
        f"{len(meta['bookmakers'])} books.")
    log(f"Credits used this run: {api.credits_this_run}  |  remaining: {api.remaining}")
    log(f"Saved to: {rel(out)}")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        if e.status == 401:
            sys.exit(
                f"The Odds API rejected the request (401).\n"
                f"  Message from the API: {e.message}\n\n"
                f"  Common causes:\n"
                f"  - The key in .env doesn't match the one on your Odds API account page (compare the\n"
                f"    first/last 4 characters above)\n"
                f"  - The monthly usage quota has run out, or the subscription isn't active yet\n"
                f"  - An old ODDS_API_KEY is set in your shell or IDE run configuration, which overrides .env"
            )
        sys.exit(f"API error: {e}")
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
