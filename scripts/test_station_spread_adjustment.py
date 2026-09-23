"""Tests whether actually USING the KEWR-KJFK morning-spread lead (r=+0.269
at n=107, scripts/feature_exploration_station_spread.py) narrows the gap
with the market, rather than just confirming it correlates. This is the
natural next step after a correlation check holds up under more data: does
acting on it actually help, walk-forward, against real settled outcomes?

Adjustment: for each day, fits a simple linear coefficient (residual ~ beta *
spread) using ONLY strictly earlier days (same no-future-leakage discipline
as walk_forward_backtest), then adds beta * today's spread to today's point
forecast before building the residual distribution around it. Compares three
walk-forward Brier scores on the same real days: the original model, the
spread-adjusted model, and the real market price.

Honest framing: this is one specific, simple way to use the lead (a linear
shift). If it doesn't help, that doesn't disprove the correlation -- it may
need a different functional form, more data, or combination with other
features. If it does help, that's real, testable evidence, not proof of
tradeable edge on its own (see Sec 5 Step 6 for what's actually required
before that claim).
"""
import statistics
import sys
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import psycopg2

from src.backtest.daily_high_backtest import brier_score, predicted_prob_bucket

NY = ZoneInfo("America/New_York")
STATIONS = ("KEWR", "KJFK")


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


def nearest_temp_near_945am(conn, station: str, target_date) -> float | None:
    target_ts = datetime(target_date.year, target_date.month, target_date.day, 9, 45, tzinfo=NY)
    window_start = target_ts - timedelta(hours=2)
    window_end = target_ts + timedelta(hours=2)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT actual_temperature_f, observed_time
        FROM weather_observations
        WHERE station = %s AND observed_time BETWEEN %s AND %s AND actual_temperature_f IS NOT NULL
        ORDER BY observed_time;
        """,
        (station, window_start, window_end),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    closest = min(rows, key=lambda r: abs((r[1].astimezone(NY) - target_ts).total_seconds()))
    return float(closest[0])


def outcome_for_bucket(settled_value: float, strike_type: str, floor_strike, cap_strike) -> int:
    if strike_type == "less":
        return 1 if settled_value < cap_strike else 0
    if strike_type == "between":
        return 1 if floor_strike <= settled_value <= cap_strike else 0
    if strike_type == "greater":
        return 1 if settled_value > floor_strike else 0
    raise ValueError(f"unrecognized strike_type {strike_type!r}")


def fit_beta(spreads: list[float], residuals: list[float]) -> float:
    """OLS slope of residual ~ spread through the origin-adjusted means
    (simple linear regression coefficient), or 0.0 if too little data/no
    variance to fit safely."""
    if len(spreads) < 10:
        return 0.0
    mean_s, mean_r = statistics.mean(spreads), statistics.mean(residuals)
    cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(spreads, residuals))
    var_s = sum((s - mean_s) ** 2 for s in spreads)
    if var_s == 0:
        return 0.0
    return cov / var_s


def main():
    conn = connect()
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
    history = cur.fetchall()

    days = []
    for target_date, forecast, residual, settled in history:
        spread_a = nearest_temp_near_945am(conn, STATIONS[0], target_date)
        spread_b = nearest_temp_near_945am(conn, STATIONS[1], target_date)
        if spread_a is None or spread_b is None:
            continue
        days.append((target_date, float(forecast), float(residual), float(settled), spread_a - spread_b))

    cur.execute(
        """
        SELECT target_date, market_ticker, strike_type, floor_strike, cap_strike, market_prob_at_forecast_time
        FROM kalshi_market_prices
        WHERE market_prob_at_forecast_time IS NOT NULL;
        """
    )
    market_by_date = {}
    for target_date, ticker, strike_type, floor_strike, cap_strike, market_prob in cur.fetchall():
        market_by_date.setdefault(target_date, []).append(
            (strike_type, float(floor_strike) if floor_strike is not None else None,
             float(cap_strike) if cap_strike is not None else None, float(market_prob))
        )
    conn.close()

    print(f"{len(days)} real days with station data. {len(market_by_date)} days with real market prices.\n")

    min_history = 20
    original_pairs, adjusted_pairs, market_pairs = [], [], []
    scored_dates = set()

    for i in range(min_history, len(days)):
        target_date, forecast, _residual, settled, spread = days[i]
        if target_date not in market_by_date:
            continue

        prior_residuals = [r for _, _, r, _, _ in days[:i]]
        prior_spreads = [s for _, _, _, _, s in days[:i]]
        beta = fit_beta(prior_spreads, prior_residuals)
        adjusted_forecast = forecast + beta * spread

        for strike_type, floor_strike, cap_strike, market_prob in market_by_date[target_date]:
            outcome = outcome_for_bucket(settled, strike_type, floor_strike, cap_strike)

            original_prob = predicted_prob_bucket(forecast, prior_residuals, strike_type, floor_strike, cap_strike)
            adjusted_prob = predicted_prob_bucket(adjusted_forecast, prior_residuals, strike_type, floor_strike, cap_strike)

            original_pairs.append((original_prob, outcome))
            adjusted_pairs.append((adjusted_prob, outcome))
            market_pairs.append((market_prob, outcome))
        scored_dates.add(target_date)

    if not original_pairs:
        print("No overlapping days with both station data and real market prices -- nothing to score.")
        return 0

    print(f"Scored {len(original_pairs)} (day, bucket) pairs across {len(scored_dates)} real days "
          f"({min(scored_dates)} to {max(scored_dates)}).\n")

    orig_brier = brier_score(original_pairs)
    adj_brier = brier_score(adjusted_pairs)
    mkt_brier = brier_score(market_pairs)

    print(f"  Original model Brier:          {orig_brier:.4f}")
    print(f"  Spread-adjusted model Brier:   {adj_brier:.4f}")
    print(f"  Market Brier:                  {mkt_brier:.4f}")

    if adj_brier < orig_brier:
        improvement = orig_brier - adj_brier
        print(f"\nAdjustment HELPS: {improvement:.4f} improvement over the original model.")
        gap_before = orig_brier - mkt_brier
        gap_after = adj_brier - mkt_brier
        print(f"Gap to market: {gap_before:.4f} -> {gap_after:.4f} "
              f"({'narrowed' if gap_after < gap_before else 'still wider, not narrowed'}).")
    else:
        print(f"\nAdjustment does NOT help ({adj_brier:.4f} vs {orig_brier:.4f} unadjusted) -- "
              "a simple linear shift on this lead doesn't translate into better predictions here.")

    print(
        "\nHonest caveat: small sample, one specific (linear) way of using the lead. A negative result "
        "here doesn't rule out the lead being real -- it might need a different form or more data. "
        "A positive result here is a real finding worth re-checking as more days accumulate, not "
        "yet proof of tradeable edge (Sec 5 Step 6 still applies)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
