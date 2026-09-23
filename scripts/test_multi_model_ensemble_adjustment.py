"""Tests whether the 6-model ensemble average (NWS + ECMWF/GFS/ICON/GEM/UKMO,
scripts/feature_exploration_multi_model.py found a modest MAE improvement:
2.08F vs NWS's own 2.25F) actually narrows the gap with the real market,
walk-forward -- same rigor as test_station_spread_adjustment.py. A lower
point-forecast MAE doesn't automatically mean better probability calibration;
this checks directly rather than assuming.

Replaces forecast_high_f with the 6-model average as the center of the
residual distribution (residual history itself still built walk-forward,
prior days only, against the ensemble-centered residuals -- an apples-to-
apples comparison, not mixing forecast sources mid-stream).
"""
import statistics
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

from src.backtest.daily_high_backtest import brier_score, predicted_prob_bucket

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "ukmo_seamless"]


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
    real_days = {row[0]: (float(row[1]), float(row[2])) for row in cur.fetchall()}

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
    market_by_date = {}
    for target_date, ticker, strike_type, floor_strike, cap_strike, market_prob in cur.fetchall():
        market_by_date.setdefault(target_date, []).append(
            (strike_type, float(floor_strike) if floor_strike is not None else None,
             float(cap_strike) if cap_strike is not None else None, float(market_prob))
        )
    conn.close()

    days = []
    for target_date in sorted(real_days):
        nws_forecast, settled = real_days[target_date]
        model_forecasts = by_date.get(target_date, {})
        if len(model_forecasts) < len(MODELS):
            continue
        ensemble_forecast = statistics.mean([nws_forecast] + list(model_forecasts.values()))
        days.append((target_date, nws_forecast, ensemble_forecast, settled))

    print(f"{len(days)} real days with NWS forecast, all 5 independent models, and Kalshi settlement.\n")

    min_history = 20
    original_pairs, ensemble_pairs, market_pairs = [], [], []
    scored_dates = set()

    for i in range(min_history, len(days)):
        target_date, nws_forecast, ensemble_forecast, settled = days[i]
        if target_date not in market_by_date:
            continue

        prior_nws_residuals = [settled_j - nws_j for _, nws_j, _, settled_j in days[:i]]
        prior_ensemble_residuals = [settled_j - ens_j for _, _, ens_j, settled_j in days[:i]]

        for strike_type, floor_strike, cap_strike, market_prob in market_by_date[target_date]:
            outcome = outcome_for_bucket(settled, strike_type, floor_strike, cap_strike)

            original_prob = predicted_prob_bucket(nws_forecast, prior_nws_residuals, strike_type, floor_strike, cap_strike)
            ensemble_prob = predicted_prob_bucket(ensemble_forecast, prior_ensemble_residuals, strike_type, floor_strike, cap_strike)

            original_pairs.append((original_prob, outcome))
            ensemble_pairs.append((ensemble_prob, outcome))
            market_pairs.append((market_prob, outcome))
        scored_dates.add(target_date)

    if not original_pairs:
        print("No overlapping days with both multi-model data and real market prices -- nothing to score.")
        return 0

    print(f"Scored {len(original_pairs)} (day, bucket) pairs across {len(scored_dates)} real days "
          f"({min(scored_dates)} to {max(scored_dates)}).\n")

    orig_brier = brier_score(original_pairs)
    ens_brier = brier_score(ensemble_pairs)
    mkt_brier = brier_score(market_pairs)

    print(f"  NWS-only model Brier:       {orig_brier:.4f}")
    print(f"  6-model ensemble Brier:     {ens_brier:.4f}")
    print(f"  Market Brier:               {mkt_brier:.4f}")

    if ens_brier < orig_brier:
        gap_before = orig_brier - mkt_brier
        gap_after = ens_brier - mkt_brier
        print(f"\nEnsemble HELPS: {orig_brier - ens_brier:.4f} improvement over NWS-only.")
        print(f"Gap to market: {gap_before:.4f} -> {gap_after:.4f} "
              f"({'narrowed' if gap_after < gap_before else 'still not narrowed'}).")
    else:
        print(f"\nEnsemble does NOT help ({ens_brier:.4f} vs {orig_brier:.4f} NWS-only) despite the lower raw MAE -- "
              "a better point forecast didn't translate into better probability calibration here.")

    print(
        "\nHonest caveat: small sample (~59-75 days with real market prices), one specific way of combining "
        "models (simple average). Doesn't rule out a smarter combination helping; this is what a plain, "
        "unweighted ensemble actually does on real data, not assumed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
