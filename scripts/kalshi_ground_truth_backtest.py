"""Re-runs the daily-high backtest using Kalshi's REAL settled values as
ground truth instead of our own NWS/KNYC actual_high_f -- see
scripts/kalshi_settlement_comparison.py and WEATHER_KALSHI_TECHNICAL_PLAN.md
for why: our own actual_high_f is systematically ~0.64F lower than what
Kalshi actually settles on, across 53 real overlapping days, with 41.5% of
days differing by >=1F. The earlier backtest (daily_high_backtest.py) scored
against the wrong target. This reuses its exact same, already-tested scoring
functions (leave_one_out_backtest, brier_score, log_loss, reliability_table)
against the correct one.

This is Phase 2's "small, correct" dataset (WEATHER_KALSHI_TECHNICAL_PLAN.md
Sec 5): trains/scores only on OUR OWN NWS-sourced forecast_high_f, the exact
same forecast source live in production (daily_high_forecast_job.py). Kept
deliberately separate from the much larger Open-Meteo-based exploratory
analysis (scripts/open_meteo_seasonal_analysis.py) -- Open-Meteo uses a
different underlying model (GFS), so its forecast errors have their own bias
characteristics; fitting this model's numbers on Open-Meteo data and applying
them to NWS-sourced forecasts would risk the same source-mismatch trap as the
Sec 4a pressure-bias finding.

Reads from the now-backfilled kalshi_settlements table directly (scripts/
backfill_kalshi_settlements.py) rather than re-hitting the live Kalshi API
per event -- that backfill is exactly what this table is for.
"""
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import (
    brier_score,
    log_loss,
    reliability_table,
    walk_forward_backtest,
)


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
    every day with both a real KNYC forecast and a real Kalshi settlement."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.target_date, d.forecast_high_f, k.settled_value_f
        FROM weather_daily_high_predictions d
        JOIN kalshi_settlements k ON k.target_date = d.target_date
        WHERE d.station = 'KNYC' AND d.forecast_high_f IS NOT NULL
        ORDER BY d.target_date;
        """
    )
    return [
        (target_date, float(forecast), float(settled) - float(forecast), float(settled))
        for target_date, forecast, settled in cur.fetchall()
    ]


def main():
    conn = connect()
    days = load_days(conn)
    conn.close()

    print(f"Loaded {len(days)} days with both a real KNYC forecast and a real Kalshi settlement.")
    print(f"Range: {days[0][0]} to {days[-1][0]}\n")

    strike_offsets = [-4, -2, 0, 2, 4]
    min_history = 20
    model_pairs, naive_pairs = walk_forward_backtest(days, strike_offsets, min_history=min_history)
    print(
        f"Walk-forward: scoring days[{min_history}:] using only strictly earlier days' "
        f"residuals ({len(days) - min_history} of {len(days)} days scored, chronological, "
        "no future leakage -- see src/backtest/daily_high_backtest.py docstring for why "
        "this replaced leave-one-out).\n"
    )

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
