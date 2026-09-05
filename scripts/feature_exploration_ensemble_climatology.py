"""EXPLORATORY: tests whether the two cheap, already-available features named
in WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 2 (ensemble spread across the
4 NWS gridpoints, simple climatology) actually carry information worth
wiring into the deployed model, before spending effort doing so. Both are
"free" in the sense that no new data needs to be sourced -- ensemble spread
is already in weather_daily_high_predictions (4 stations per date), and
climatology is computable from open_meteo_historical_daily's real observed
actual_high_f history (2021-03-23 to 2026-05-24, ~5 years) -- deliberately
using ACTUALS from that table, not its forecast_high_f, since observed
climatology is a real physical fact independent of forecast model, unlike
forecasts which are kept separate per the Sec 4a/5d source-mismatch policy.

This is a diagnostic/correlation check, not a full walk-forward-conditioned
backtest -- with only 92 real days, splitting into tiers and re-running
walk-forward on each tier would shrink the effective sample further before
there's even evidence the feature carries a real signal. Simple correlation
against |residual| is the right first question: does this feature relate to
forecast uncertainty/bias at all, honestly reported either way.
"""
import statistics
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


def load_days_with_ensemble_spread(conn):
    """(target_date, knyc_forecast, residual, ensemble_spread) for our 92 real
    production days -- ensemble_spread is the stdev of forecast_high_f across
    all 4 stations for that date (0.0 if the non-KNYC rows are missing)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.target_date, d.forecast_high_f, k.settled_value_f - d.forecast_high_f AS residual,
               (SELECT STDDEV(d2.forecast_high_f) FROM weather_daily_high_predictions d2
                WHERE d2.target_date = d.target_date AND d2.forecast_high_f IS NOT NULL) AS spread
        FROM weather_daily_high_predictions d
        JOIN kalshi_settlements k ON k.target_date = d.target_date
        WHERE d.station = 'KNYC' AND d.forecast_high_f IS NOT NULL
        ORDER BY d.target_date;
        """
    )
    return [
        (target_date, float(forecast), float(residual), float(spread) if spread is not None else 0.0)
        for target_date, forecast, residual, spread in cur.fetchall()
    ]


def load_climatology_lookup(conn):
    """day_of_year -> mean actual_high_f across ALL years in
    open_meteo_historical_daily, +/- 7 calendar days (real observed values,
    not forecasts -- see module docstring for why that distinction matters
    here)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT EXTRACT(DOY FROM target_date)::int AS doy, actual_high_f
        FROM open_meteo_historical_daily
        WHERE actual_high_f IS NOT NULL;
        """
    )
    by_doy: dict[int, list[float]] = {}
    for doy, actual in cur.fetchall():
        by_doy.setdefault(doy, []).append(float(actual))

    lookup = {}
    for center_doy in range(1, 367):
        window_values = []
        for offset in range(-7, 8):
            doy = ((center_doy - 1 + offset) % 366) + 1
            window_values.extend(by_doy.get(doy, []))
        if window_values:
            lookup[center_doy] = statistics.mean(window_values)
    return lookup


def pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x**0.5 * var_y**0.5)


def main():
    conn = connect()
    days = load_days_with_ensemble_spread(conn)
    climatology = load_climatology_lookup(conn)
    conn.close()

    print(f"Loaded {len(days)} real production days: {days[0][0]} to {days[-1][0]}\n")

    print("=== Ensemble spread vs |forecast error| ===")
    spreads = [spread for _, _, _, spread in days]
    abs_residuals = [abs(residual) for _, _, residual, _ in days]
    r = pearson_r(spreads, abs_residuals)
    print(f"  Pearson r(ensemble_spread, |residual|) = {r:+.3f}  (n={len(days)})")
    print(f"  Mean spread: {statistics.mean(spreads):.2f}, stdev: {statistics.stdev(spreads):.2f}")
    print(f"  Mean |residual|: {statistics.mean(abs_residuals):.2f}")

    print("\n=== KNYC forecast vs climatology ===")
    climatology_diffs = []
    residuals = []
    missing_climatology = 0
    for target_date, forecast, residual, _spread in days:
        doy = target_date.timetuple().tm_yday
        clim = climatology.get(doy)
        if clim is None:
            missing_climatology += 1
            continue
        climatology_diffs.append(forecast - clim)
        residuals.append(residual)

    if climatology_diffs:
        r2 = pearson_r(climatology_diffs, residuals)
        print(f"  Pearson r(forecast - climatology, residual) = {r2:+.3f}  (n={len(climatology_diffs)})")
        print(f"  Mean (forecast - climatology): {statistics.mean(climatology_diffs):+.2f}")
    print(f"  Days with no climatology match: {missing_climatology}")

    print(
        "\nInterpretation guide (not a conclusion the script draws for you): |r| < ~0.2 is weak/"
        "noisy at this sample size, ~0.2-0.4 is a real but modest relationship worth watching as "
        "more data accumulates, > ~0.4 would be a strong enough signal to consider wiring into the "
        "live model now rather than waiting for the quantile-regression checkpoint."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
