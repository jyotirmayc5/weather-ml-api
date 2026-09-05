"""The actual bar WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 4 has been
pointing at since the backtest work started: does the model beat the market's
own implied probability, not just a naive deterministic baseline? Everything
before this script (daily_high_backtest.py, kalshi_ground_truth_backtest.py)
could only answer "is there some signal at all" -- this is the first script
that can actually answer the real question, now that
scripts/backfill_kalshi_market_prices.py has real historical market prices
to compare against (limited to 2026-07-02 onward -- Kalshi's candlestick
retention window is shorter than our full 92-day forecast history, see that
script's docstring).

Uses walk_forward_backtest's same chronological, no-future-leakage design,
applied per REAL bucket market rather than synthetic strike offsets: for each
(target_date, market_ticker) with a real forecast, a real market price near
forecast time, and a real settled outcome, scores the model's
predicted_prob_bucket() against the market's own market_prob_at_forecast_time,
both against the same real 0/1 outcome. Model residual history is built from
ALL days strictly before target_date across the FULL forecast history (not
just days that happen to have market-price data), matching how the model
would really be used live -- jobs/daily_prediction_job.py does the same.
"""
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import brier_score, log_loss, predicted_prob_bucket


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


def load_forecast_history(conn):
    """(target_date, forecast_high_f, residual) for ALL real production days,
    chronological -- this is the full residual pool walk-forward draws from,
    same as kalshi_ground_truth_backtest.py."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.target_date, d.forecast_high_f, k.settled_value_f - d.forecast_high_f, k.settled_value_f
        FROM weather_daily_high_predictions d
        JOIN kalshi_settlements k ON k.target_date = d.target_date
        WHERE d.station = 'KNYC' AND d.forecast_high_f IS NOT NULL
        ORDER BY d.target_date;
        """
    )
    return [
        (target_date, float(forecast), float(residual), float(settled))
        for target_date, forecast, residual, settled in cur.fetchall()
    ]


def load_market_prices(conn):
    """(target_date, market_ticker, strike_type, floor_strike, cap_strike,
    market_prob) for every real backfilled market price."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT target_date, market_ticker, strike_type, floor_strike, cap_strike,
               market_prob_at_forecast_time
        FROM kalshi_market_prices
        WHERE market_prob_at_forecast_time IS NOT NULL;
        """
    )
    return [
        (
            target_date,
            ticker,
            strike_type,
            float(floor_strike) if floor_strike is not None else None,
            float(cap_strike) if cap_strike is not None else None,
            float(market_prob),
        )
        for target_date, ticker, strike_type, floor_strike, cap_strike, market_prob in cur.fetchall()
    ]


def outcome_for_bucket(settled_value: float, strike_type: str, floor_strike, cap_strike) -> int:
    if strike_type == "less":
        return 1 if settled_value < cap_strike else 0
    if strike_type == "between":
        return 1 if floor_strike <= settled_value <= cap_strike else 0
    if strike_type == "greater":
        return 1 if settled_value > floor_strike else 0
    raise ValueError(f"unrecognized strike_type {strike_type!r}")


def main():
    conn = connect()
    history = load_forecast_history(conn)
    market_prices = load_market_prices(conn)
    conn.close()

    residuals_by_date = {}
    date_to_index = {}
    all_residuals_chronological = []
    for i, (target_date, _forecast, residual, _settled) in enumerate(history):
        date_to_index[target_date] = i
        all_residuals_chronological.append(residual)
        residuals_by_date[target_date] = list(all_residuals_chronological[:i])  # strictly earlier

    forecast_by_date = {d: f for d, f, _r, _s in history}
    settled_by_date = {d: s for d, _f, _r, s in history}

    min_history = 20
    model_pairs, market_pairs = [], []
    scored_dates = set()
    skipped_too_early, skipped_no_forecast = 0, 0

    for target_date, ticker, strike_type, floor_strike, cap_strike, market_prob in market_prices:
        if target_date not in forecast_by_date:
            skipped_no_forecast += 1
            continue
        idx = date_to_index[target_date]
        if idx < min_history:
            skipped_too_early += 1
            continue

        forecast_high_f = forecast_by_date[target_date]
        prior_residuals = residuals_by_date[target_date]
        settled_value = settled_by_date[target_date]

        model_prob = predicted_prob_bucket(forecast_high_f, prior_residuals, strike_type, floor_strike, cap_strike)
        outcome = outcome_for_bucket(settled_value, strike_type, floor_strike, cap_strike)

        model_pairs.append((model_prob, outcome))
        market_pairs.append((market_prob, outcome))
        scored_dates.add(target_date)

    print(f"Scored {len(model_pairs)} (day, market) pairs across {len(scored_dates)} real days "
          f"({min(scored_dates)} to {max(scored_dates)}).")
    print(f"Skipped: {skipped_too_early} too early (< {min_history}-day min history), "
          f"{skipped_no_forecast} with no matching forecast.\n")

    print("=== Model (walk-forward residual distribution) ===")
    print(f"  Brier score: {brier_score(model_pairs):.4f}")
    print(f"  Log loss:    {log_loss(model_pairs):.4f}")

    print("\n=== Real Kalshi market price, at forecast time ===")
    print(f"  Brier score: {brier_score(market_pairs):.4f}")
    print(f"  Log loss:    {log_loss(market_pairs):.4f}")

    model_brier, market_brier = brier_score(model_pairs), brier_score(market_pairs)
    print(f"\n{'Model beats market' if model_brier < market_brier else 'Market beats model'} "
          f"on Brier score ({model_brier:.4f} vs {market_brier:.4f}).")
    print(
        "NOTE: this is the real Sec 5 Step 4 bar, not a naive baseline -- but still a small, "
        "single-season sample. Don't treat one comparison like this as proof of persistent edge; "
        "that's exactly what Step 6's live logging (jobs/daily_prediction_job.py) exists to check "
        "over time, not a single retrospective number."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
