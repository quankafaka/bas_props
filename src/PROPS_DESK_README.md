# Props trading desk (demo)

A local tool for sportsbook traders to choose which player props are offered, set their prices, and react to where the money is going. It runs on one laptop with a one-time odds snapshot and simulated bets. No database and no internet connection are needed during the demo.

## Run it

Python 3.8+, standard library only (nothing to `pip install`).

```bash
# 1. Get odds (pick one)
python src/odds-api/pull_snapshot.py       # real odds from The Odds API (needs ODDS_API_KEY in .env)
python src/odds-api/make_sample_data.py    # sample odds, no key needed

# 2. Start the dashboard (opens http://localhost:8050)
python src/dashboard/server.py
```

Restart the server after pulling a new snapshot. A new snapshot starts the board clean, with all props off and no game vigs set.

## Project layout

```
bas_props/
├── .env                          ODDS_API_KEY (keep out of git)
└── src/
    ├── general_utility/
    │   └── create_path.py        every path in the project, portable across machines
    ├── odds-api/
    │   ├── pull_snapshot.py      one-time pull from The Odds API
    │   ├── make_sample_data.py   sample snapshot in the same format
    │   └── data/                 the snapshot the dashboard reads
    ├── dashboard/
    │   ├── server.py             local web server + API
    │   ├── board.py              snapshot -> games -> markets -> props
    │   ├── pricing.py            consensus and our price (odds math)
    │   ├── desk.py               trader controls, exposure, alerts
    │   ├── simulator.py          fake bettors for the demo
    │   ├── static/               the page (HTML, CSS, JS)
    │   └── data/trader_state.json   saved statuses, prices, change log
    └── live_feed/                credit-usage tests for a live odds feed (see its README.md);
                                  `server.py --live-run latest` shows a running test's odds on the dashboard
```

## How prices are set

1. For each prop, find the line most books agree on (the consensus line).
2. **Devig:** remove each book's vig from its Over/Under pair, so the two sides add up to 100%. Average across books to get the fair probability.
3. **Add our vig back:** multiply both sides by (1 + vig), so our two implied probabilities add up to 100% + vig. Round to the nearest whole cent, so the vig we offer matches the number on the dashboard.

Vig is set at two levels:
- **Default vig** (top of the page, 3.65%) applies to every game.
- **Game vig** (inside an open game) overrides the default for that game. Games with their own vig show it in amber in the game row. "Use default" removes the override.

Changing either one reprices every prop that hasn't been edited by hand. Hand-edited props keep their prices, marked "edited", until they're reset.

For reference, at 3.65% a coin-flip prop is priced -108 / -108. The industry-standard -110 / -110 is about 4.76%.

Yes-only props (like Anytime TD) have no opposite side to devig against, so the desk assumes the books hold about 10% on them, and prices ours at twice the game vig. Both numbers are constants in `src/dashboard/pricing.py`.

## Demo talk track (about 5 minutes)

1. **The board.** Every game in the snapshot, with the main lines for context. Nothing is offered yet: every prop starts off.
2. **Open a game.** Click it. Props are grouped by market in tabs. Each row shows our price, the fair price, and how many books we're pricing off. Click "5 books" to show every book's price side by side.
3. **Choose what to offer.** Offer a whole market ("Offer all Pass Yards"), a whole game, or single players with Off / Live / Suspend.
4. **Bets arrive.** The simulator places bets on live props only. Show the bet feed on the right, and the blue/amber bar on each prop showing money on each side.
5. **React.** When money gets lopsided or a result would cost more than $1,500, the prop shows up under Needs attention. Click "Shade Over 10¢": that side gets worse for bettors, the other side gets better, and the flow slows down.
6. **Guardrails.** Type an obviously wrong price, like +2000 on a +145 prop. The desk asks before offering something that generous.
7. **Set the vig.** Change a game's vig from 3.65% to 5% and every non-edited prop in that game reprices. Click a "books" link to show the vig we're actually offering on that prop.
8. **Accountability.** The Changes tab logs every trader action with a time.

## What's simulated vs. real

| Part | Demo | Production would need |
|---|---|---|
| Odds | One snapshot, refreshed by hand | Live odds feed (push, not polling) |
| Bets | Simulated bettors | Real bets from the sportsbook platform |
| Offered props | Saved to a local JSON file | Published to the sportsbook in real time |
| Users | One trader, no login | Logins, roles, per-trader audit trail |
| Settlement | None | Grading props from official stats |
