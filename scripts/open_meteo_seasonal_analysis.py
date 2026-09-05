"""EXPLORATORY ONLY -- WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5d.

Uses the large Open-Meteo x Kalshi joined dataset (1,661 days, ~5 years) to
study whether/how forecast-error characteristics vary by season. Open-Meteo's
forecast comes from a different underlying model (GFS) than NWS's own
blended forecast that daily_high_forecast_job.py actually calls in
production, so this script's numbers must NEVER be used to set the deployed
model's bias/residual distribution directly -- that would repeat the exact
source-mismatch trap already documented in Sec 4a for the pressure bias.
This is purely informational: does the shape/spread of forecast error change
across the year, which would inform whether the deployed (small, NWS-only)
model eventually needs season-conditioned residuals once it has enough of
its own multi-season data to do that safely.

The walk-forward backtest run here (src/backtest/daily_high_backtest.py) is
the same one used for the real Kalshi-settlement run against our own
forecasts (scripts/kalshi_ground_truth_backtest.py) -- reused here only to
sanity-check the walk-forward methodology itself at a much larger sample
size, not to produce numbers for deployment.
"""
import sys
from collections import defaultdict
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import brier_score, log_loss, walk_forward_backtest

MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


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


def load_days(conn):
    """(target_date, forecast_high_f, residual, kalshi_settled_value_f) for
    every day with both an Open-Meteo forecast and a real Kalshi settlement."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT o.target_date, o.forecast_high_f, k.settled_value_f
        FROM open_meteo_historical_daily o
        JOIN kalshi_settlements k ON k.target_date = o.target_date
        WHERE o.forecast_high_f IS NOT NULL
        ORDER BY o.target_date;
        """
    )
    return [
        (target_date, float(forecast), float(settled) - float(forecast), float(settled))
        for target_date, forecast, settled in cur.fetchall()
    ]


def seasonal_residual_stats(days):
    by_month = defaultdict(list)
    for target_date, _forecast, residual, _actual in days:
        by_month[target_date.month].append(residual)

    rows = []
    for month in range(1, 13):
        residuals = by_month.get(month, [])
        if not residuals:
            rows.append((month, 0, None, None))
            continue
        mean = sum(residuals) / len(residuals)
        variance = sum((r - mean) ** 2 for r in residuals) / len(residuals)
        rows.append((month, len(residuals), mean, variance**0.5))
    return rows


def main():
    conn = connect()
    days = load_days(conn)
    conn.close()

    print(f"Loaded {len(days)} days with both an Open-Meteo forecast and a real Kalshi settlement.")
    print(f"Range: {days[0][0]} to {days[-1][0]}")
    print("EXPLORATORY ONLY -- see module docstring. Not used to set production bias numbers.\n")

    print("=== Forecast error (Kalshi settled - Open-Meteo forecast) by month ===")
    print("  month   count   mean residual   stdev")
    for month, count, mean, stdev in seasonal_residual_stats(days):
        name = MONTH_NAMES[month - 1]
        if count:
            print(f"  {name}     {count:4d}    {mean:+.2f}          {stdev:.2f}")
        else:
            print(f"  {name}     {count:4d}    --              --")

    strike_offsets = [-4, -2, 0, 2, 4]
    min_history = 60
    model_pairs, naive_pairs = walk_forward_backtest(days, strike_offsets, min_history=min_history)
    print(
        f"\n=== Walk-forward backtest sanity check, {len(days) - min_history} of {len(days)} days scored ==="
    )
    print("(large-sample check on the walk-forward METHOD itself, not a production number)")
    print(f"  Model Brier: {brier_score(model_pairs):.4f}   Naive Brier: {brier_score(naive_pairs):.4f}")
    print(f"  Model log loss: {log_loss(model_pairs):.4f}   Naive log loss: {log_loss(naive_pairs):.4f}")


if __name__ == "__main__":
    sys.exit(main())
