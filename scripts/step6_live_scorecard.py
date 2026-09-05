"""The actual tool for WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 6: scores
every prediction jobs/daily_prediction_job.py has logged (kalshi_predictions)
against real settled outcomes (kalshi_settlements) as they come in, and
reports whether the model is beating the market -- growing day by day, not a
one-time snapshot.

Distinct from scripts/kalshi_market_vs_model_backtest.py, which is a
retrospective analysis of the backfilled historical range
(kalshi_market_prices, frozen at whenever that backfill ran). This script is
the ongoing, live version: run it periodically (weekly is reasonable) to see
whether the model's Brier score is closing the gap on the market's, using
only real days daily_prediction_job has actually logged and that have since
settled.

Days with no settlement yet are excluded (can't score an unknown outcome).
The scored-day count starts small and only grows as time passes -- don't
expect a meaningful comparison from a handful of days.
"""
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import brier_score, log_loss


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def connect():
    parts = urlsplit(load_dsn())
    return psycopg2.connect(
        host=parts.hostname,
        port=parts.port,
        user=unquote(parts.username),
        password=unquote(parts.password),
        dbname=parts.path.lstrip("/"),
    )


def outcome_for_bucket(settled_value: float, strike_type: str, floor_strike, cap_strike) -> int:
    if strike_type == "less":
        return 1 if settled_value < cap_strike else 0
    if strike_type == "between":
        return 1 if floor_strike <= settled_value <= cap_strike else 0
    if strike_type == "greater":
        return 1 if settled_value > floor_strike else 0
    raise ValueError(f"unrecognized strike_type {strike_type!r}")


def build_report() -> str:
    """Returns the full report as a plain-text string, instead of printing it
    directly -- lets other scripts (e.g. scripts/send_weekly_scorecard_email.py)
    reuse the exact same logic as the report body, rather than parsing stdout
    from a subprocess or duplicating the query/scoring code."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.target_date, p.strike_type, p.floor_strike, p.cap_strike,
               p.model_probability, p.market_yes_bid, p.market_yes_ask, k.settled_value_f
        FROM kalshi_predictions p
        JOIN kalshi_settlements k ON k.target_date = p.target_date
        ORDER BY p.target_date;
        """
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return (
            "No scored days yet -- either daily_prediction_job hasn't logged any days that have "
            "since settled, or kalshi_settlement_job hasn't pulled that settlement in yet. "
            "This is expected early on; check back after a few real days have passed."
        )

    model_pairs, market_pairs = [], []
    dates_scored = set()
    for target_date, strike_type, floor_strike, cap_strike, model_prob, yes_bid, yes_ask, settled in rows:
        floor_strike = float(floor_strike) if floor_strike is not None else None
        cap_strike = float(cap_strike) if cap_strike is not None else None
        outcome = outcome_for_bucket(float(settled), strike_type, floor_strike, cap_strike)
        market_mid = (float(yes_bid) + float(yes_ask)) / 2

        model_pairs.append((float(model_prob), outcome))
        market_pairs.append((market_mid, outcome))
        dates_scored.add(target_date)

    model_brier, market_brier = brier_score(model_pairs), brier_score(market_pairs)
    model_ll, market_ll = log_loss(model_pairs), log_loss(market_pairs)

    lines = [
        f"Scored {len(model_pairs)} (day, bucket) pairs across {len(dates_scored)} real settled days "
        f"({min(dates_scored)} to {max(dates_scored)}).",
        "",
        "=== Model (daily_prediction_job's live predictions) ===",
        f"  Brier score: {model_brier:.4f}",
        f"  Log loss:    {model_ll:.4f}",
        "",
        "=== Real market price, at the time predictions were logged ===",
        f"  Brier score: {market_brier:.4f}",
        f"  Log loss:    {market_ll:.4f}",
        "",
        f"{'Model beats market' if model_brier < market_brier else 'Market beats model'} "
        f"on Brier score ({model_brier:.4f} vs {market_brier:.4f}).",
        "",
        "This is what to check periodically, not any single day's diff on the dashboard. "
        "A few days of one side winning proves nothing -- watch this number over WEEKS "
        "(Sec 5 Step 6's own stated bar) before treating any result here as real edge.",
    ]
    return "\n".join(lines)


def main():
    print(build_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
