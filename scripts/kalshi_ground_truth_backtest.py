"""Re-runs the daily-high backtest using Kalshi's REAL settled values as
ground truth instead of our own NWS/KNYC actual_high_f -- see
scripts/kalshi_settlement_comparison.py and WEATHER_KALSHI_TECHNICAL_PLAN.md
for why: our own actual_high_f is systematically ~0.64F lower than what
Kalshi actually settles on, across 53 real overlapping days, with 41.5% of
days differing by >=1F. The earlier backtest (daily_high_backtest.py) scored
against the wrong target. This reuses its exact same, already-tested scoring
functions (leave_one_out_backtest, brier_score, log_loss, reliability_table)
against the correct one.
"""
import sys
from datetime import date
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import (
    brier_score,
    leave_one_out_backtest,
    log_loss,
    reliability_table,
)
from src.kalshi.client import event_ticker_to_date, fetch_settled_events, get_settlement_value


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def load_forecasts(min_date):
    parts = urlsplit(load_dsn())
    conn = psycopg2.connect(
        host=parts.hostname,
        port=parts.port,
        user=unquote(parts.username),
        password=unquote(parts.password),
        dbname=parts.path.lstrip("/"),
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT target_date, forecast_high_f
        FROM weather_daily_high_predictions
        WHERE station = 'KNYC' AND forecast_high_f IS NOT NULL AND target_date >= %s
        ORDER BY target_date;
        """,
        (min_date,),
    )
    result = {row[0]: float(row[1]) for row in cur.fetchall()}
    conn.close()
    return result


def main():
    earliest = date(2026, 5, 25)
    forecasts = load_forecasts(earliest)

    print(f"Pulling Kalshi settlements back to {earliest}...")
    events = fetch_settled_events("KXHIGHNY", min_date=earliest)

    days = []  # (date, forecast_high_f, residual, kalshi_actual)
    for event in events:
        event_date = event_ticker_to_date(event["event_ticker"])
        forecast = forecasts.get(event_date)
        if forecast is None:
            continue
        kalshi_value = get_settlement_value(event["event_ticker"])
        if kalshi_value is None:
            continue
        days.append((event_date, forecast, kalshi_value - forecast, kalshi_value))

    days.sort()
    print(f"Scoring against {len(days)} days with both a forecast and a real Kalshi settlement.\n")

    strike_offsets = [-4, -2, 0, 2, 4]
    model_pairs, naive_pairs = leave_one_out_backtest(days, strike_offsets)

    print("=== Residual-distribution model, scored against REAL Kalshi settlements ===")
    print(f"  Brier score: {brier_score(model_pairs):.4f}")
    print(f"  Log loss:    {log_loss(model_pairs):.4f}")

    print("\n=== Naive baseline, scored against REAL Kalshi settlements ===")
    print(f"  Brier score: {brier_score(naive_pairs):.4f}")
    print(f"  Log loss:    {log_loss(naive_pairs):.4f}")

    print("\n=== Calibration (model, vs real Kalshi settlements) ===")
    print("  range        count   avg predicted   realized freq")
    for lo, hi, count, avg_pred, realized in reliability_table(model_pairs):
        if count:
            print(f"  {lo:.1f}-{hi:.1f}     {count:4d}   {avg_pred:.3f}          {realized:.3f}")
        else:
            print(f"  {lo:.1f}-{hi:.1f}     {count:4d}   --              --")


if __name__ == "__main__":
    sys.exit(main())
