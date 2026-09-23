"""EXPLORATORY: tests the multi-model ensemble idea for real, using 5
genuinely independent global models (ECMWF, GFS, ICON, GEM, UKMO via
scripts/backfill_multi_model_forecasts.py) -- distinct from the
within-NWS-model ensemble spread already tested weak (r=+0.110), since these
are actually different models built by different national weather services,
not spatial variation of one model.

Three questions, each answerable directly from real data:
1. Does model DISAGREEMENT (spread) predict our own forecast error? (same
   diagnostic as the NWS-gridpoint spread check)
2. Is any single model, on its own, more accurate than our current NWS
   forecast against Kalshi's real settled value? (a genuinely different
   question from #1 -- could replace or supplement NWS directly)
3. Does a simple average across all models (including NWS) reduce error
   versus NWS alone? (the classic "ensembles reduce single-model bias"
   claim, tested directly rather than assumed)
"""
import statistics
import sys
from urllib.parse import unquote, urlsplit

import psycopg2

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
    conn.close()

    rows = []
    for target_date, (nws_forecast, settled) in real_days.items():
        model_forecasts = by_date.get(target_date, {})
        if len(model_forecasts) < len(MODELS):
            continue
        rows.append((target_date, nws_forecast, settled, model_forecasts))

    print(f"{len(rows)} real days with NWS forecast, Kalshi settlement, and all 5 independent models.\n")
    if not rows:
        return 0

    print("=== Question 1: does multi-model spread predict our own forecast error? ===")
    spreads = [max(mf.values()) - min(mf.values()) for _, _, _, mf in rows]
    abs_residuals = [abs(settled - nws) for _, nws, settled, _ in rows]
    r = pearson_r(spreads, abs_residuals)
    print(f"  Pearson r(multi_model_spread, |NWS residual|) = {r:+.3f}  (n={len(rows)})")
    print(f"  Mean spread: {statistics.mean(spreads):.2f}F, stdev: {statistics.stdev(spreads):.2f}F\n")

    print("=== Question 2: is any single model more accurate than NWS alone? ===")
    nws_errors = [settled - nws for _, nws, settled, _ in rows]
    print(f"  {'Model':15s} {'MAE':>6s}  {'Mean signed error':>18s}")
    print(f"  {'NWS (current)':15s} {statistics.mean([abs(e) for e in nws_errors]):6.2f}  {statistics.mean(nws_errors):+18.2f}")
    for model in MODELS:
        errors = [settled - mf[model] for _, _, settled, mf in rows]
        mae = statistics.mean([abs(e) for e in errors])
        bias = statistics.mean(errors)
        print(f"  {model:15s} {mae:6.2f}  {bias:+18.2f}")

    print("\n=== Question 3: does simple averaging (NWS + all 5 models) reduce error? ===")
    ensemble_errors = []
    for _, nws, settled, mf in rows:
        ensemble_mean = statistics.mean([nws] + list(mf.values()))
        ensemble_errors.append(settled - ensemble_mean)
    print(f"  NWS alone MAE:        {statistics.mean([abs(e) for e in nws_errors]):.2f}F")
    print(f"  6-model average MAE:  {statistics.mean([abs(e) for e in ensemble_errors]):.2f}F")

    print(
        "\nInterpretation: for Q1, same |r| thresholds as other feature-exploration scripts. For Q2/Q3, "
        "a lower MAE than NWS's own is the real signal to look for -- not just 'different', but "
        "measurably closer to what Kalshi actually settled on."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
