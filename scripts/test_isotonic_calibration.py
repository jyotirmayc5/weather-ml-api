"""Tests whether isotonic calibration (the one immediately-testable idea from
real research that doesn't need more data first -- WEATHER_KALSHI_TECHNICAL_PLAN.md
Sec 5h) narrows the gap with the market, layered on top of the currently-
deployed 6-model ensemble (Sec 5g).

Walk-forward, no future leakage: for each day, the calibrator is fit only on
PRIOR days' (raw_predicted_prob, outcome) pairs -- never the day's own, same
discipline as walk_forward_backtest and every other test this session. This
is close in spirit to what the research search surfaced as "conformal
calibration" -- a distribution-free recalibration of predicted probabilities
against realized outcomes, using only exchangeable prior data, not a fitted
parametric model that could overfit on ~110 days.
"""
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import brier_score, isotonic_calibrate, predicted_prob_bucket

INDEPENDENT_MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "ukmo_seamless"]


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


def ensemble_forecast(nws_forecast: float, model_values: dict) -> float:
    values = [nws_forecast] + list(model_values.values())
    return sum(values) / len(values)


def main():
    conn = connect()
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
    real_days_raw = cur.fetchall()

    cur.execute("SELECT target_date, model, forecast_high_f FROM multi_model_forecasts WHERE forecast_high_f IS NOT NULL;")
    by_date: dict = {}
    for target_date, model, forecast in cur.fetchall():
        by_date.setdefault(target_date, {})[model] = float(forecast)

    cur.execute(
        """
        SELECT target_date, market_ticker, strike_type, floor_strike, cap_strike, market_prob_at_forecast_time
        FROM kalshi_market_prices WHERE market_prob_at_forecast_time IS NOT NULL;
        """
    )
    market_by_date: dict = {}
    for target_date, ticker, strike_type, floor_strike, cap_strike, market_prob in cur.fetchall():
        market_by_date.setdefault(target_date, []).append(
            (strike_type, float(floor_strike) if floor_strike is not None else None,
             float(cap_strike) if cap_strike is not None else None, float(market_prob))
        )
    conn.close()

    days = []
    for target_date, nws_forecast, settled in real_days_raw:
        mf = by_date.get(target_date, {})
        if all(m in mf for m in INDEPENDENT_MODELS):
            days.append((target_date, float(nws_forecast), float(settled), mf))

    min_history = 20
    min_calibration_history = 30
    raw_pairs, calibrated_pairs, market_pairs = [], [], []
    calibration_pool: list[tuple[float, int]] = []  # grows chronologically, walk-forward
    scored_dates = set()

    for i in range(min_history, len(days)):
        target_date, nws, settled, mf = days[i]

        prior_residuals = [
            s_j - ensemble_forecast(n_j, mf_j) for _, n_j, s_j, mf_j in days[:i]
        ]
        ens = ensemble_forecast(nws, mf)

        if target_date in market_by_date:
            for strike_type, floor_strike, cap_strike, market_prob in market_by_date[target_date]:
                outcome = outcome_for_bucket(settled, strike_type, floor_strike, cap_strike)
                raw_prob = predicted_prob_bucket(ens, prior_residuals, strike_type, floor_strike, cap_strike)
                calibrated_prob = isotonic_calibrate(calibration_pool, raw_prob, min_history=min_calibration_history)

                raw_pairs.append((raw_prob, outcome))
                calibrated_pairs.append((calibrated_prob, outcome))
                market_pairs.append((market_prob, outcome))
            scored_dates.add(target_date)

        # Add today's (raw_prob, outcome) pairs to the calibration pool AFTER
        # scoring, for future days -- even days without a market price still
        # contribute real (prob, outcome) pairs to calibrate against.
        for offset in (-2, -1, 0, 1, 2):
            strike = round(nws) + offset
            outcome = 1 if settled >= strike else 0
            raw_prob = predicted_prob_bucket(ens, prior_residuals, "greater", strike - 1, None)
            calibration_pool.append((raw_prob, outcome))

    if not raw_pairs:
        print("No overlapping days with both multi-model data and real market prices -- nothing to score.")
        return 0

    print(f"Scored {len(raw_pairs)} (day, bucket) pairs across {len(scored_dates)} real days "
          f"({min(scored_dates)} to {max(scored_dates)}).\n")

    raw_brier = brier_score(raw_pairs)
    cal_brier = brier_score(calibrated_pairs)
    mkt_brier = brier_score(market_pairs)

    print(f"  Raw ensemble Brier (currently live):    {raw_brier:.4f}")
    print(f"  Isotonic-calibrated ensemble Brier:     {cal_brier:.4f}")
    print(f"  Market Brier:                           {mkt_brier:.4f}")

    if cal_brier < raw_brier:
        gap_before = raw_brier - mkt_brier
        gap_after = cal_brier - mkt_brier
        print(f"\nCalibration HELPS: {raw_brier - cal_brier:.4f} improvement over raw.")
        print(f"Gap to market: {gap_before:.4f} -> {gap_after:.4f} "
              f"({'narrowed' if gap_after < gap_before else 'still not narrowed'}).")
    else:
        print(f"\nCalibration does NOT help ({cal_brier:.4f} vs {raw_brier:.4f} raw).")

    print(
        "\nHonest caveat: small calibration pool (built from a synthetic 5-offset scan per day, not just "
        "real market buckets, to have enough (prob, outcome) pairs to calibrate against at all -- a "
        "genuine limitation of testing this on ~110 days). A negative result doesn't rule out calibration "
        "helping with more data; a positive one is real but still small-sample."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
