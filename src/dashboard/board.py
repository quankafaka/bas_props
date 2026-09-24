"""
Turns the raw odds snapshot (odds-api/data) into the board the traders work with:
games -> markets -> props, each prop with a consensus line and fair probabilities.
"""

import json
from collections import defaultdict
from statistics import median

from general_utility.create_path import ODDS_API_DATA_DIR, rel
from pricing import consensus_line, fair_probabilities

SIDE_ORDER = {"Over": 0, "Yes": 0, "Under": 1, "No": 1}


class SnapshotMissing(Exception):
    pass


def prop_id(event_id, market_key, player, line, is_alternate):
    """Stable id for a prop. Main markets are one prop per player (the line can move);
    alternate markets are one prop per player per line."""
    parts = [event_id, market_key, player or "-"]
    if is_alternate:
        parts.append(str(line))
    return "|".join(parts)


def load_snapshot(data_dir=ODDS_API_DATA_DIR):
    try:
        meta = json.loads((data_dir / "meta.json").read_text())
        events = json.loads((data_dir / "events.json").read_text())
        rows = json.loads((data_dir / "props.json").read_text())
    except FileNotFoundError as e:
        raise SnapshotMissing(
            f"No odds snapshot found in {rel(data_dir)}. Run odds-api/pull_snapshot.py "
            f"(real odds) or odds-api/make_sample_data.py (sample odds) first."
        ) from e
    return meta, events, rows


def game_line_summary(event):
    """Short consensus summary of the main game lines, for context next to the props."""
    lines = event.get("game_lines", [])
    home, away = event["home_team"], event["away_team"]
    summary = {}

    spreads = [g["line"] for g in lines if g["market"] == "spreads" and g["side"] == home and g["line"] is not None]
    if spreads:
        summary["home_spread"] = consensus_line(spreads)
    totals = [g["line"] for g in lines if g["market"] == "totals" and g["side"] == "Over" and g["line"] is not None]
    if totals:
        summary["total"] = consensus_line(totals)
    for team, key in ((home, "home_ml"), (away, "away_ml")):
        prices = [g["price"] for g in lines if g["market"] == "h2h" and g["side"] == team]
        if prices:
            summary[key] = int(median(prices))
    return summary


def build_board(meta, events, rows):
    """Returns (games, props). props is a dict id -> prop."""
    market_names = meta.get("market_names", {})
    market_order = {k: i for i, k in enumerate(market_names)}

    # Group every book quote: prop key -> book -> line -> {side: price}
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    info = {}
    for r in rows:
        alt = bool(r.get("is_alternate")) or r["market_key"].endswith("_alternate")
        pid = prop_id(r["event_id"], r["market_key"], r.get("player"), r.get("line"), alt)
        grouped[pid][r["bookmaker"]][r.get("line")][r["side"]] = r["price"]
        if pid not in info:
            info[pid] = {
                "event_id": r["event_id"],
                "market_key": r["market_key"],
                "market_name": r.get("market_name") or market_names.get(r["market_key"], r["market_key"]),
                "player": r.get("player") or r["side"],
                "is_alternate": alt,
            }

    props = {}
    for pid, books in grouped.items():
        all_lines = [ln for by_line in books.values() for ln in by_line]
        line = consensus_line(all_lines)
        quotes = []
        for bkey, by_line in books.items():
            if line in by_line:
                quotes.append({"book": bkey, **by_line[line]})
        sides = sorted({s for q in quotes for s in q if s != "book"}, key=lambda s: (SIDE_ORDER.get(s, 9), s))
        if not sides or len(sides) > 2:
            continue  # multi-outcome markets (e.g. method of first basket) aren't handled in the demo
        fair, n_books = fair_probabilities(quotes, sides)
        if fair is None:
            continue
        two_way = len(fair) == 2
        props[pid] = {
            "id": pid,
            **info[pid],
            "consensus_line": line,
            "sides": sides if two_way else sides[:1],
            "two_way": two_way,
            "fair": [round(p, 4) for p in fair],
            "books": n_books,
            "book_quotes": quotes,
            "lines_seen": sorted({ln for ln in all_lines if ln is not None}),
        }

    by_event = defaultdict(list)
    for p in props.values():
        by_event[p["event_id"]].append(p)

    games = []
    for ev in sorted(events, key=lambda e: (e["commence_time"], e["sport"])):
        ev_props = by_event.get(ev["event_id"], [])
        markets = sorted({p["market_key"] for p in ev_props}, key=lambda k: (market_order.get(k, 999), k))
        games.append({
            "event_id": ev["event_id"],
            "sport": ev["sport"],
            "commence_time": ev["commence_time"],
            "home_team": ev["home_team"],
            "away_team": ev["away_team"],
            "lines": game_line_summary(ev),
            "markets": [{"key": k, "name": market_names.get(k, k)} for k in markets],
        })
    return games, props
