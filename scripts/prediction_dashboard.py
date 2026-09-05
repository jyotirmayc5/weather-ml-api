"""Human-readable view of daily_prediction_job's logged predictions
side-by-side with the real Kalshi market price, and the actual outcome once
a day has settled. WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 6 exists to
watch these two numbers converge or diverge over time -- this makes that
visible without needing to run a one-off SQL query or wait for a scripted
backtest. Read-only, no trading logic here.

Usage: venv/Scripts/python.exe -m scripts.prediction_dashboard [--days N]
Defaults to the last 7 days of logged predictions.
"""
import argparse
import sys
from urllib.parse import unquote, urlsplit

import psycopg2


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


def bucket_label(strike_type: str, floor_strike, cap_strike) -> str:
    if strike_type == "less":
        return f"< {cap_strike:g}"
    if strike_type == "between":
        return f"{floor_strike:g}-{cap_strike:g}"
    if strike_type == "greater":
        return f"> {floor_strike:g}"
    return f"{strike_type} ({floor_strike}, {cap_strike})"


def outcome_for_bucket(settled_value: float, strike_type: str, floor_strike, cap_strike) -> int:
    if strike_type == "less":
        return 1 if settled_value < cap_strike else 0
    if strike_type == "between":
        return 1 if floor_strike <= settled_value <= cap_strike else 0
    if strike_type == "greater":
        return 1 if settled_value > floor_strike else 0
    raise ValueError(f"unrecognized strike_type {strike_type!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="how many most-recent target_dates to show")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT target_date FROM kalshi_predictions
        ORDER BY target_date DESC LIMIT %s;
        """,
        (args.days,),
    )
    target_dates = sorted(row[0] for row in cur.fetchall())

    if not target_dates:
        print("No predictions logged yet -- daily_prediction_job hasn't had a real run.")
        return 0

    for target_date in target_dates:
        cur.execute(
            """
            SELECT market_ticker, strike_type, floor_strike, cap_strike,
                   model_probability, market_yes_bid, market_yes_ask, forecast_high_f
            FROM kalshi_predictions
            WHERE target_date = %s
            ORDER BY floor_strike NULLS FIRST, cap_strike NULLS LAST;
            """,
            (target_date,),
        )
        rows = cur.fetchall()
        if not rows:
            continue

        cur.execute("SELECT settled_value_f FROM kalshi_settlements WHERE target_date = %s;", (target_date,))
        settled_row = cur.fetchone()
        settled_value = float(settled_row[0]) if settled_row else None

        forecast_high_f = float(rows[0][7])
        print(f"\n=== {target_date}  (forecast_high_f={forecast_high_f:g}"
              + (f", settled at {settled_value:g}°F)" if settled_value is not None else ", not yet settled)"))
        print(f"  {'bucket':10s} {'model':>7s} {'market mid':>11s} {'spread':>7s} {'diff':>7s} {'outcome':>8s}")

        for ticker, strike_type, floor_strike, cap_strike, model_prob, yes_bid, yes_ask, _ in rows:
            floor_strike = float(floor_strike) if floor_strike is not None else None
            cap_strike = float(cap_strike) if cap_strike is not None else None
            model_prob = float(model_prob)
            market_mid = (float(yes_bid) + float(yes_ask)) / 2
            spread = float(yes_ask) - float(yes_bid)
            diff = model_prob - market_mid

            outcome_str = "--"
            if settled_value is not None:
                outcome = outcome_for_bucket(settled_value, strike_type, floor_strike, cap_strike)
                outcome_str = "YES" if outcome else "no"

            print(
                f"  {bucket_label(strike_type, floor_strike, cap_strike):10s} "
                f"{model_prob:6.1%} {market_mid:10.1%} {spread:6.1%} {diff:+6.1%} {outcome_str:>8s}"
            )

    conn.close()
    print(
        "\n'diff' = model - market midpoint. Consistently positive/negative on days that later settle "
        "YES/no respectively would be a real signal; scattered signs are not. This is a viewer, not a "
        "trading signal on its own -- see scripts/kalshi_market_vs_model_backtest.py for the actual "
        "scored comparison."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
