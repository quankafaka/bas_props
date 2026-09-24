"""
Odds math used by the dashboard.

How "our price" is set for each prop:
  1. Collect every book's price for the prop at the consensus line.
  2. Devig. Two-way markets (Over/Under, Yes/No): remove each book's vig so its
     two sides add up to 100%, then average across books -> fair probability.
     One-way markets (e.g. Anytime TD, Yes only) have no other side to devig
     against, so we average the books' implied probability and remove an assumed
     vig (ONE_WAY_BOOK_HOLD) -> fair probability.
  3. Add our vig back: the game's vig if a trader set one, else the default (3.65%).
     Both sides are multiplied by (1 + vig), so they add up to 100% + vig.
     Prices are rounded to the nearest whole cent, not to 5s, so the vig we offer
     matches the vig the trader set: at 3.65%, a coin-flip prop is -108 / -108.
Traders can then override any price by hand.
"""

from statistics import median

ONE_WAY_BOOK_HOLD = 0.10   # assumed vig books build into Yes-only props
ONE_WAY_MARGIN_MULT = 2.0  # Yes-only props carry twice the game vig


def american_to_prob(american):
    a = float(american)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def american_to_decimal(american):
    a = float(american)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def prob_to_american(p):
    p = min(max(p, 0.01), 0.99)
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def round_american(a, step=1):
    """Round to the nearest `step` cents. Values between -100 and +100 don't exist in American odds."""
    r = int(round(a / step) * step)
    if -100 < r < 100:
        r = 100 if a >= 0 else -100
    return 100 if r == -100 else r


def is_valid_american(a):
    try:
        a = float(a)
    except (TypeError, ValueError):
        return False
    return a >= 100 or a <= -100


# "Cents" ladder: -110 -> -10, -100/+100 -> 0, +110 -> +10.
# Lower = worse for the bettor. Makes shading prices across the +/-100 gap simple.
def to_cents(a):
    return a - 100 if a >= 100 else a + 100


def from_cents(c):
    return c + 100 if c >= 0 else c - 100


def shade(american, cents):
    """Negative cents = worse for the bettor (e.g. -110 shaded -10 -> -120)."""
    return round_american(from_cents(to_cents(american) + cents))


def offered_prices(fair_probs, vig, two_way):
    """Fair probabilities -> our American prices, with our vig added back proportionally."""
    m = vig if two_way else vig * ONE_WAY_MARGIN_MULT
    return [round_american(prob_to_american(min(p * (1 + m), 0.97))) for p in fair_probs]


def rescale_prices(prices, old_vig, new_vig, two_way):
    """Move hand-set prices to a new vig while keeping the trader's view of the odds.
    Two-way: take our current prices' own no-vig split, then add the new vig.
    One-way: strip the old vig, add the new one (both at the one-way multiplier)."""
    implied = [american_to_prob(a) for a in prices]
    if two_way and len(implied) == 2:
        total = sum(implied)
        fair = [x / total for x in implied]
        return [round_american(prob_to_american(min(f * (1 + new_vig), 0.97))) for f in fair]
    fair = implied[0] / (1 + old_vig * ONE_WAY_MARGIN_MULT)
    return [round_american(prob_to_american(min(fair * (1 + new_vig * ONE_WAY_MARGIN_MULT), 0.97)))]


def actual_vig(prices):
    """Overround of a two-way price pair, e.g. -110/-110 -> 0.0476."""
    if len(prices) != 2:
        return None
    return sum(american_to_prob(a) for a in prices) - 1


def consensus_line(book_lines):
    """Most common line across books; ties go to the one closest to the median."""
    lines = [ln for ln in book_lines if ln is not None]
    if not lines:
        return None
    counts = {}
    for ln in lines:
        counts[ln] = counts.get(ln, 0) + 1
    top = max(counts.values())
    candidates = [ln for ln, c in counts.items() if c == top]
    mid = median(lines)
    return min(candidates, key=lambda ln: (abs(ln - mid), ln))


def fair_probabilities(quotes, sides):
    """
    quotes: list of {side: american_price} dicts, one per book, all at the same line.
    Returns (fair probability per side, number of books used).
    """
    if len(sides) == 2:
        per_book = []
        for q in quotes:
            if all(s in q for s in sides):
                p = [american_to_prob(q[s]) for s in sides]
                total = sum(p)
                per_book.append([x / total for x in p])
        if per_book:
            n = len(per_book)
            return [sum(b[i] for b in per_book) / n for i in range(2)], n

    # One-way, or two-way where no book quoted both sides
    side = sides[0]
    implied = [american_to_prob(q[side]) for q in quotes if side in q]
    if not implied:
        return None, 0
    fair = (sum(implied) / len(implied)) / (1 + ONE_WAY_BOOK_HOLD)
    return [fair], len(implied)
