"""
The trading desk: what the traders control, and what the bets are doing.

Trader controls (saved to dashboard/data/trader_state.json, so they survive restarts):
  - status per prop: off (not offered) / live / suspended
  - manual line and price overrides
  - vig, set at four levels; the most specific one wins:
        default (3.65%) -> game -> market in a game -> single prop
    Setting a level clears the overrides below it (setting a game's vig resets its
    markets and props to that vig). The default is a fallback: changing it only
    moves props that have no override.

Exposure (in memory; resets when the server restarts):
  - money and bet count per side, and our profit/loss if each side wins
  - alerts when money is lopsided or a loss would be large
"""

import json
import threading
import time
from collections import defaultdict

from general_utility.create_path import ensure_dir
from pricing import (actual_vig, american_to_decimal, american_to_prob, is_valid_american, offered_prices,
                     prob_to_american, rescale_prices, round_american, shade)

DEFAULT_SETTINGS = {"default_vig": 0.0365, "simulator": True}
MAX_VIG = 0.25

# Alert thresholds
LOPSIDED_SHARE = 0.68        # share of money on one side
LOPSIDED_MIN_HANDLE = 400    # ignore small samples...
LOPSIDED_MIN_BETS = 3        # ...and one-off bets
LIABILITY_ALERT = -1500      # alert if we'd lose more than this if one side wins
REALERT_GROWTH = 1.4         # after a trader acts, re-alert only once handle grows 40%
REALERT_MIN_NEW = 250        # ...and by at least $250


def fmt_price(a):
    return f"+{a}" if a > 0 else str(a)


def fmt_line(line):
    return "" if line is None else f"{line:g}"


class Desk:
    def __init__(self, meta, games, props, state_file):
        self.lock = threading.RLock()
        self.meta, self.games, self.props = meta, games, props
        self.games_by_id = {g["event_id"]: g for g in games}
        self.state_file = state_file
        self.state = self._load_state()
        self.bets = []
        self.exposure = defaultdict(lambda: {"handle": [0.0, 0.0], "payout": [0.0, 0.0], "count": [0, 0]})
        self.ack = {}  # prop id -> handle at the moment a trader last acted on it
        self._next_bet_id = 1

    # ------------------------------------------------------------------ state

    def _load_state(self):
        snapshot = self.meta.get("pulled_at")
        fresh = {"snapshot": snapshot, "settings": dict(DEFAULT_SETTINGS), "games": {}, "markets": {},
                 "props": {}, "log": []}
        try:
            saved = json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError):
            return fresh
        if saved.get("snapshot") != snapshot:
            # New odds snapshot: keep settings, start the board clean
            fresh["settings"].update({k: v for k, v in saved.get("settings", {}).items() if k in DEFAULT_SETTINGS})
            fresh["log"].append({"ts": time.time(), "text": "New odds snapshot loaded. All props start off."})
            return fresh
        saved["settings"] = {**DEFAULT_SETTINGS, **{k: v for k, v in saved.get("settings", {}).items()
                                                    if k in DEFAULT_SETTINGS}}
        saved.setdefault("games", {})
        saved.setdefault("markets", {})
        return saved

    def _save(self):
        ensure_dir(self.state_file.parent)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.state_file)

    def _log(self, text):
        self.state["log"].append({"ts": time.time(), "text": text})
        self.state["log"] = self.state["log"][-300:]

    def _pst(self, pid):
        return self.state["props"].setdefault(
            pid, {"offered": False, "suspended": False, "line": None, "prices": None})

    def _label(self, pid):
        p = self.props[pid]
        return f"{p['player']} {p['market_name']}"

    # ------------------------------------------------------------ effective

    @staticmethod
    def market_key(event_id, market_key):
        return f"{event_id}|{market_key}"

    def game_vig(self, event_id):
        custom = self.state["games"].get(event_id, {}).get("vig")
        return custom if custom is not None else self.state["settings"]["default_vig"]

    def market_vig(self, event_id, market_key):
        custom = self.state["markets"].get(self.market_key(event_id, market_key), {}).get("vig")
        return custom if custom is not None else self.game_vig(event_id)

    def vig_for(self, pid):
        """(vig, where it comes from) for one prop: prop, market, game or default."""
        p = self.props[pid]
        own = self.state["props"].get(pid, {}).get("vig")
        if own is not None:
            return own, "prop"
        mk = self.state["markets"].get(self.market_key(p["event_id"], p["market_key"]), {}).get("vig")
        if mk is not None:
            return mk, "market"
        g = self.state["games"].get(p["event_id"], {}).get("vig")
        if g is not None:
            return g, "game"
        return self.state["settings"]["default_vig"], "default"

    def effective(self, pid):
        p = self.props[pid]
        st = self.state["props"].get(pid, {})
        vig_target, vig_source = self.vig_for(pid)
        auto = offered_prices(p["fair"], vig_target, p["two_way"])
        line = st.get("line") if st.get("line") is not None else p["consensus_line"]
        prices = st.get("prices") or auto
        if st.get("suspended"):
            status = "suspended"
        elif st.get("offered"):
            status = "live"
        else:
            status = "off"
        return {
            "id": pid, "line": line, "prices": prices, "auto_prices": auto, "status": status,
            "vig": None if actual_vig(prices) is None else round(actual_vig(prices), 4),
            "vig_target": vig_target, "vig_source": vig_source,
            "manual": st.get("prices") is not None or st.get("line") is not None,
        }

    def board(self):
        with self.lock:
            props = [{**p, **self.effective(pid)} for pid, p in self.props.items()]
            for p in props:
                p.pop("book_quotes", None)
            games = [{**g, "vig": self.game_vig(g["event_id"]),
                      "vig_custom": self.state["games"].get(g["event_id"], {}).get("vig") is not None}
                     for g in self.games]
            market_vigs = {k: v["vig"] for k, v in self.state["markets"].items() if v.get("vig") is not None}
            return {"meta": self.meta_summary(), "settings": self.state["settings"],
                    "games": games, "market_vigs": market_vigs, "props": props}

    def meta_summary(self):
        m = self.meta
        return {"pulled_at": m.get("pulled_at"), "source": m.get("source", "api"),
                "sports": m.get("sports", []), "bookmakers": m.get("bookmakers", [])}

    def book_quotes(self, pid):
        return self.props[pid]["book_quotes"]

    # -------------------------------------------------------------- actions

    def _touch(self, pid):
        """Trader acted on this prop: reset its alert baseline."""
        self.ack[pid] = sum(self.exposure[pid]["handle"]) if pid in self.exposure else 0.0

    def set_status(self, pid, status, log=True):
        st = self._pst(pid)
        before = self.effective(pid)["status"]
        st["offered"] = status in ("live", "suspended")
        st["suspended"] = status == "suspended"
        if log and before != status:
            verb = {"live": "Offered", "off": "Pulled", "suspended": "Suspended"}[status]
            self._log(f"{verb} {self._label(pid)}")
        self._touch(pid)

    def set_price(self, pid, side_idx, price):
        if not is_valid_american(price):
            raise ValueError("American odds must be +100 or higher, or -100 or lower.")
        price = round_american(float(price), step=1)
        eff = self.effective(pid)
        new = list(eff["prices"])
        old = new[side_idx]
        new[side_idx] = price
        self._pst(pid)["prices"] = new
        side = self.props[pid]["sides"][side_idx]
        self._log(f"{self._label(pid)}: {side} {fmt_price(old)} to {fmt_price(price)}")
        self._touch(pid)

    def set_line(self, pid, line):
        p = self.props[pid]
        if p["consensus_line"] is None:
            raise ValueError("This market has no line.")
        line = round(float(line) * 2) / 2
        eff = self.effective(pid)
        st = self._pst(pid)
        if st.get("prices") is None:
            st["prices"] = list(eff["prices"])  # freeze prices; the trader prices the new line
        st["line"] = None if line == p["consensus_line"] else line
        self._log(f"{self._label(pid)}: line {fmt_line(eff['line'])} to {fmt_line(line)}")
        self._touch(pid)

    def move(self, pid, side_idx, cents, verb="moved"):
        """Move one side's price by `cents` (positive = longer odds / better for bettors).
        On two-way props the other side is repriced so the prop keeps its vig:
        both implied probabilities still add up to 100% + vig."""
        p = self.props[pid]
        eff = self.effective(pid)
        new = list(eff["prices"])
        new[side_idx] = shade(new[side_idx], cents)
        if p["two_way"]:
            other_implied = (1 + eff["vig_target"]) - american_to_prob(new[side_idx])
            if not 0.02 <= other_implied <= 0.98:
                raise ValueError("That's as far as this price can move.")
            new[1 - side_idx] = round_american(prob_to_american(other_implied))
        self._pst(pid)["prices"] = new
        self._log_move(pid, eff["prices"], new, verb)
        self._touch(pid)

    def _log_move(self, pid, old, new, verb):
        """Several clicks on the same prop in a row become one log entry."""
        now = time.time()
        last = getattr(self, "_last_move", None)
        if last and last["pid"] == pid and last["verb"] == verb and now - last["ts"] < 15 and self.state["log"] and \
                self.state["log"][-1] is last["entry"]:
            old = last["from"]
            self.state["log"].pop()
        sides = self.props[pid]["sides"]
        parts = [f"{sides[i]} {fmt_price(old[i])} to {fmt_price(new[i])}" for i in range(len(new)) if old[i] != new[i]]
        self._log(f"{self._label(pid)}: {verb} prices, {', '.join(parts) or 'no change'}")
        self._last_move = {"pid": pid, "ts": now, "from": old, "verb": verb, "entry": self.state["log"][-1]}

    def shade(self, pid, side_idx, cents):
        """Make one side `cents` worse for bettors (used by the alert buttons)."""
        self.move(pid, side_idx, -cents, verb="shaded")

    def reset(self, pid):
        """Back to consensus: clears hand-set prices, line, and this prop's own vig."""
        st = self._pst(pid)
        st["line"], st["prices"] = None, None
        st.pop("vig", None)
        self._log(f"{self._label(pid)}: reset to consensus")
        self._touch(pid)

    def prop_action(self, pid, action, body):
        with self.lock:
            if pid not in self.props:
                raise KeyError(pid)
            if action in ("live", "off", "suspended"):
                self.set_status(pid, action)
            elif action == "set_price":
                self.set_price(pid, int(body["side"]), body["price"])
            elif action == "set_line":
                self.set_line(pid, body["line"])
            elif action == "move":
                self.move(pid, int(body["side"]), int(body.get("cents", 5)))
            elif action == "shade":
                self.shade(pid, int(body["side"]), int(body.get("cents", 10)))
            elif action == "reset":
                self.reset(pid)
            else:
                raise ValueError(f"Unknown action: {action}")
            self._save()
            return {**self.props[pid], **self.effective(pid), "book_quotes": None}

    def bulk(self, event_id, market_key, status):
        with self.lock:
            ids = [pid for pid, p in self.props.items()
                   if p["event_id"] == event_id and (market_key is None or p["market_key"] == market_key)]
            if status == "suspended":  # suspending only applies to props that are currently offered
                ids = [pid for pid in ids if self.effective(pid)["status"] == "live"]
            changed = 0
            for pid in ids:
                if self.effective(pid)["status"] != status:
                    self.set_status(pid, status, log=False)
                    changed += 1
            g = self.games_by_id.get(event_id, {})
            scope = (self.props[ids[0]]["market_name"] + " in " if market_key and ids else "all props in ")
            verb = {"live": "Offered", "off": "Pulled", "suspended": "Suspended"}[status]
            self._log(f"{verb} {scope}{g.get('away_team', '')} at {g.get('home_team', '')} ({changed} props)")
            self._save()
            return {pid: self.effective(pid) for pid in ids}

    def update_settings(self, body):
        with self.lock:
            s = self.state["settings"]
            if "default_vig" in body:
                self.set_vig("default", body["default_vig"])
            if "simulator" in body:
                s["simulator"] = bool(body["simulator"])
                self._log("Bet simulator " + ("started" if s["simulator"] else "paused"))
            self._save()
            return s

    @staticmethod
    def _check_vig(v):
        v = round(float(v), 6)
        if not 0 <= v <= MAX_VIG:
            raise ValueError(f"Vig must be between 0% and {MAX_VIG * 100:.0f}%.")
        return v

    def set_vig(self, scope, vig, event_id=None, market_key=None, pid=None):
        """Set the vig at one level. vig=None removes that level's override.
        Returns how many props were repriced and their old/new prices."""
        with self.lock:
            vig = None if vig is None else self._check_vig(vig)
            s = self.state
            if scope == "default":
                if vig is None:
                    raise ValueError("The default vig needs a value.")
                ids = list(self.props)
            elif scope == "game":
                if event_id not in self.games_by_id:
                    raise KeyError(event_id)
                ids = [i for i, p in self.props.items() if p["event_id"] == event_id]
            elif scope == "market":
                ids = [i for i, p in self.props.items()
                       if p["event_id"] == event_id and p["market_key"] == market_key]
                if not ids:
                    raise KeyError(market_key)
            elif scope == "prop":
                if pid not in self.props:
                    raise KeyError(pid)
                ids = [pid]
            else:
                raise ValueError(f"Unknown vig level: {scope}")

            before_vig = {i: self.vig_for(i)[0] for i in ids}
            before_prices = {i: list(self.effective(i)["prices"]) for i in ids}

            # Apply, clearing the overrides below this level
            if scope == "default":
                old = s["settings"]["default_vig"]
                s["settings"]["default_vig"] = vig
            elif scope == "game":
                old = self.game_vig(event_id)
                if vig is None:
                    s["games"].pop(event_id, None)
                else:
                    s["games"][event_id] = {"vig": vig}
                for k in [k for k in s["markets"] if k.startswith(event_id + "|")]:
                    del s["markets"][k]
                for i in ids:
                    s["props"].get(i, {}).pop("vig", None)
            elif scope == "market":
                old = self.market_vig(event_id, market_key)
                key = self.market_key(event_id, market_key)
                if vig is None:
                    s["markets"].pop(key, None)
                else:
                    s["markets"][key] = {"vig": vig}
                for i in ids:
                    s["props"].get(i, {}).pop("vig", None)
            else:
                old = before_vig[pid]
                st = self._pst(pid)
                st["vig"] = vig
                if vig is None:
                    st.pop("vig")

            # Hand-set prices move to the new vig too, keeping the trader's lean
            moved = {}
            for i in ids:
                new_vig = self.vig_for(i)[0]
                st = s["props"].get(i)
                if new_vig != before_vig[i] and st and st.get("prices"):
                    st["prices"] = rescale_prices(st["prices"], before_vig[i], new_vig, self.props[i]["two_way"])
                after = self.effective(i)["prices"]
                if after != before_prices[i]:
                    moved[i] = {"old": before_prices[i], "new": after}
                    self._touch(i)

            new = vig if vig is not None else (self.game_vig(event_id) if scope == "game"
                                               else self.market_vig(event_id, market_key) if scope == "market"
                                               else self.vig_for(pid)[0])
            if scope == "default":
                what = "Default vig"
            elif scope == "game":
                what = f"{self._game_label(event_id)}: vig"
            elif scope == "market":
                what = f"{self.props[ids[0]]['market_name']} in {self._game_label(event_id)}: vig"
            else:
                what = f"{self._label(pid)}: vig"
            reset = " (back to the level above)" if vig is None else ""
            self._log(f"{what} {old * 100:.2f}% to {new * 100:.2f}%{reset}, repriced {len(moved)} prop{'' if len(moved) == 1 else 's'}")
            self._save()
            return {"repriced": len(moved), "moved": moved}

    def _game_label(self, event_id):
        g = self.games_by_id[event_id]
        return f"{g['away_team']} at {g['home_team']}"

    # ------------------------------------------------------------- exposure

    def live_prop_ids(self):
        return [pid for pid in self.props if self.effective(pid)["status"] == "live"]

    def record_bet(self, pid, side_idx, stake, big=False):
        with self.lock:
            eff = self.effective(pid)
            if eff["status"] != "live":
                return None
            p = self.props[pid]
            price = eff["prices"][side_idx]
            ex = self.exposure[pid]
            ex["handle"][side_idx] += stake
            ex["payout"][side_idx] += stake * american_to_decimal(price)
            ex["count"][side_idx] += 1
            g = self.games_by_id[p["event_id"]]
            bet = {
                "id": self._next_bet_id, "ts": time.time(), "prop_id": pid, "event_id": p["event_id"],
                "side_idx": side_idx, "side": p["sides"][side_idx], "stake": stake, "price": price,
                "line": eff["line"], "player": p["player"], "market_name": p["market_name"],
                "game": f"{g['away_team']} at {g['home_team']}", "big": big,
            }
            self._next_bet_id += 1
            self.bets.append(bet)
            self.bets = self.bets[-2000:]
            return bet

    def clear_bets(self):
        with self.lock:
            self.bets.clear()
            self.exposure.clear()
            self.ack.clear()
            self._log("Cleared all bets and exposure")
            self._save()

    def prop_exposure(self, pid):
        ex = self.exposure.get(pid)
        if not ex:
            return None
        p = self.props[pid]
        n = 2 if p["two_way"] else 1
        total = sum(ex["handle"][:n])
        pnl = [round(total - ex["payout"][i], 2) for i in range(n)]
        if not p["two_way"]:
            pnl.append(round(total, 2))  # the "No" outcome of a Yes-only prop: we keep everything
        return {
            "handle": [round(h, 2) for h in ex["handle"][:n]],
            "count": ex["count"][:n],
            "total": round(total, 2),
            "bets": sum(ex["count"][:n]),
            "pnl_if": pnl[:n],
            "worst": min(pnl),
        }

    def alert_for(self, pid, exp):
        if not exp:
            return None
        base = self.ack.get(pid)
        if base is not None and exp["total"] < max(base * REALERT_GROWTH, base + REALERT_MIN_NEW):
            return None
        p = self.props[pid]
        reasons, heavy = [], None
        if p["two_way"] and exp["total"] >= LOPSIDED_MIN_HANDLE and exp["bets"] >= LOPSIDED_MIN_BETS:
            share = max(exp["handle"]) / exp["total"]
            if share >= LOPSIDED_SHARE:
                heavy = exp["handle"].index(max(exp["handle"]))
                reasons.append(f"{share:.0%} of money on {p['sides'][heavy]}")
        if exp["worst"] <= LIABILITY_ALERT:
            if heavy is None:
                heavy = exp["pnl_if"].index(min(exp["pnl_if"]))
            reasons.append(f"would lose ${-exp['worst']:,.0f} if {p['sides'][heavy]} wins")
        if not reasons:
            return None
        return {
            "prop_id": pid, "event_id": p["event_id"], "market_key": p["market_key"],
            "player": p["player"], "market_name": p["market_name"],
            "reason": "; ".join(reasons), "side_idx": heavy, "side": p["sides"][heavy],
            "suggest_cents": 10 if p["two_way"] else 20,
            "severity": "high" if exp["worst"] <= 2 * LIABILITY_ALERT else "normal",
            "worst": exp["worst"],
        }

    def live_view(self, since_bet_id=0):
        with self.lock:
            exposure, alerts = {}, []
            for pid in list(self.exposure):
                exp = self.prop_exposure(pid)
                if exp:
                    exposure[pid] = exp
                    a = self.alert_for(pid, exp)
                    if a:
                        alerts.append(a)
            alerts.sort(key=lambda a: a["worst"])
            live_ids = self.live_prop_ids()
            return {
                "settings": self.state["settings"],
                "totals": {
                    "live": len(live_ids),
                    "bets": sum(e["bets"] for e in exposure.values()),
                    "handle": round(sum(e["total"] for e in exposure.values()), 2),
                    "worst": round(sum(min(e["worst"], 0) for e in exposure.values()), 2),
                },
                "status": {pid: self.effective(pid)["status"] for pid in self.state["props"]
                           if pid in self.props},
                "exposure": exposure,
                "alerts": alerts,
                "bets": [b for b in self.bets[-60:] if b["id"] > since_bet_id][::-1],
                "log": self.state["log"][-60:][::-1],
            }
