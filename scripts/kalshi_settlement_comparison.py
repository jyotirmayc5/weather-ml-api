"""Quantifies the gap between our own NWS/KNYC-based actual_high_f and
Kalshi's real KXHIGHNY settlement values, across all overlapping days.
Surfaced by a 6-day spot check (WEATHER_KALSHI_TECHNICAL_PLAN.md) that found
two of six days differed by a full 1F, not explained by simple rounding, in
BOTH the pre- and post-Aug-14-2026 settlement-source regimes (NWS vs The
Weather Company) -- meaning this isn't purely about that source transition;
there may also be a station-identity mismatch between our KNYC feed and
Kalshi's "CLINYC" reference site.

This matters because our own actual_high_f is currently used as ground truth
in src/backtest/daily_high_backtest.py -- if it disagrees with what Kalshi
actually pays out on, that backtest is scoring against the wrong outcome for
the real question ("would this have made money"), even if internally
self-consistent.
"""
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

from src.kalshi.client import fetch_settled_events, get_settlement_value


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def load_our_actual_highs(min_date):
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
        SELECT target_date, actual_high_f
        FROM weather_daily_high_predictions
        WHERE station = 'KNYC' AND actual_high_f IS NOT NULL AND target_date >= %s
        ORDER BY target_date;
        """,
        (min_date,),
    )
    result = {row[0]: float(row[1]) for row in cur.fetchall()}
    conn.close()
    return result


def main():
    from datetime import date

    earliest = date(2026, 5, 25)  # our earliest labeled KNYC day
    print(f"Pulling all settled KXHIGHNY events back to {earliest}...")
    events = fetch_settled_events("KXHIGHNY", min_date=earliest)
    print(f"Found {len(events)} settled events.\n")

    our_highs = load_our_actual_highs(earliest)

    rows = []
    for event in events:
        from src.kalshi.client import event_ticker_to_date

        event_date = event_ticker_to_date(event["event_ticker"])
        our_value = our_highs.get(event_date)
        if our_value is None:
            continue
        kalshi_value = get_settlement_value(event["event_ticker"])
        if kalshi_value is None:
            continue
        rows.append((event_date, our_value, kalshi_value, kalshi_value - our_value))

    rows.sort()

    print(f"{'date':<12}{'ours':>8}{'kalshi':>8}{'diff':>8}")
    for event_date, ours, kalshi, diff in rows:
        flag = "  <-- >=1F" if abs(diff) >= 1.0 else ""
        print(f"{str(event_date):<12}{ours:>8.1f}{kalshi:>8.1f}{diff:>8.1f}{flag}")

    if not rows:
        print("No overlapping days found.")
        return

    diffs = [d for _, _, _, d in rows]
    abs_diffs = [abs(d) for d in diffs]
    n = len(diffs)
    mean_signed = sum(diffs) / n
    mean_abs = sum(abs_diffs) / n
    max_abs = max(abs_diffs)
    n_at_least_1f = sum(1 for d in abs_diffs if d >= 1.0)

    print(f"\n=== Summary over {n} overlapping days ===")
    print(f"  mean signed diff (kalshi - ours): {mean_signed:+.3f} F")
    print(f"  mean absolute diff:               {mean_abs:.3f} F")
    print(f"  max absolute diff:                {max_abs:.1f} F")
    print(f"  days with >=1F difference:        {n_at_least_1f} ({100*n_at_least_1f/n:.1f}%)")


if __name__ == "__main__":
    sys.exit(main())
