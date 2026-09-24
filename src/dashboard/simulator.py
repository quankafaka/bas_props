"""
Simulated bettors, so the demo shows live action on the props a trader offers.

Only for the demo: in production these would be real bets from the sportsbook.

How the fake bettors behave:
  - Bets only land on props that are live (not off or suspended).
  - Each prop gets a hidden "public lean". Most are fairly balanced; about a third
    attract lopsided money on one side, so the traders have something to react to.
  - Bettors respond to price: shading a side (making it worse for them) slows the
    money on that side, the way it would with real customers.
  - Most stakes are small; occasionally a big bet lands. Every bet is capped at a $2,500 win.
"""

import math
import random
import threading
import time

from pricing import american_to_decimal, american_to_prob

PRICE_SENSITIVITY = 30   # how strongly bettors react to price vs. fair value
MAX_WIN = 2500           # per-bet max win, like a real book's prop limits
TICK_SECONDS = 1.0


class BetSimulator(threading.Thread):
    def __init__(self, desk, seed=None):
        super().__init__(daemon=True)
        self.desk = desk
        self.rng = random.Random(seed)
        self.profiles = {}

    def profile(self, pid):
        if pid not in self.profiles:
            r = self.rng
            if r.random() < 0.35:
                lean = r.uniform(0.74, 0.88) if r.random() < 0.6 else r.uniform(0.12, 0.26)
            else:
                lean = r.uniform(0.42, 0.62)
            self.profiles[pid] = {"popularity": r.lognormvariate(0, 0.8), "lean": lean}
        return self.profiles[pid]

    def poisson(self, lam):
        threshold, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= threshold:
                return k
            k += 1

    def stake(self):
        r = self.rng
        if r.random() < 0.04:
            return int(r.uniform(250, 1500) // 25 * 25), True
        return max(5, min(500, int(r.lognormvariate(math.log(35), 0.85) // 5 * 5))), False

    def run(self):
        while True:
            time.sleep(TICK_SECONDS)
            try:
                self.tick()
            except Exception as e:  # keep the demo running whatever happens
                print(f"[simulator] {e}")

    def tick(self):
        desk = self.desk
        with desk.lock:
            if not desk.state["settings"].get("simulator"):
                return
            live = desk.live_prop_ids()
            if not live:
                return
            n = self.poisson(min(0.6 + 0.04 * len(live), 3.5))
            weights = [self.profile(pid)["popularity"] for pid in live]
            for _ in range(n):
                pid = self.rng.choices(live, weights=weights)[0]
                prop = desk.props[pid]
                prices = desk.effective(pid)["prices"]
                lean = self.profile(pid)["lean"]
                edges = [prop["fair"][i] - american_to_prob(prices[i]) for i in range(len(prices))]

                if prop["two_way"]:
                    w = [lean * math.exp(PRICE_SENSITIVITY * edges[0]),
                         (1 - lean) * math.exp(PRICE_SENSITIVITY * edges[1])]
                    side = 0 if self.rng.random() < w[0] / sum(w) else 1
                    # Overall appetite also drops when the chosen side is badly priced
                    if self.rng.random() > min(1.0, math.exp(PRICE_SENSITIVITY * (edges[side] + 0.03))):
                        continue
                else:
                    side = 0
                    if self.rng.random() > min(1.0, math.exp(PRICE_SENSITIVITY * (edges[0] + 0.04))):
                        continue

                stake, big = self.stake()
                max_stake = MAX_WIN / (american_to_decimal(prices[side]) - 1)
                stake = max(5, min(stake, int(max_stake // 5 * 5)))
                desk.record_bet(pid, side, stake, big=big)
