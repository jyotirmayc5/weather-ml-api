"""EXPLORATORY, cheap first check before any pipeline changes -- does today's
own observed temperature trajectory add predictive power for the FINAL
settled high, beyond what the morning 6-model ensemble forecast already
captures? WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5i.

Deliberately does NOT yet test whether this would beat the market's own
same-time price (that's the harder, more expensive question -- our market-
price backfill only has one snapshot per day near 9:45am, not intraday
history). This only checks whether the signal exists at all: does knowing
"how much the day is already running hot/cold vs. the morning forecast, as
of checkpoint hour X" correlate with the eventual settlement error, using
data we already have (weather_observations, no new collection needed).

Signal tested: (max observed KNYC temp from midnight to checkpoint hour X on
target_date) - (morning ensemble forecast), correlated against
(settled - morning ensemble forecast) -- i.e. does "running hot/cold by hour
X" predict "will end up hot/cold at settlement", beyond zero information.
"""
import statistics
import sys
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import psycopg2

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
INDEPENDENT_MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "ukmo_seamless"]
CHECKPOINT_HOURS = [10, 12, 14, 16]


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


def max_observed_temp_by_hour(conn, target_date, checkpoint_hour: int) -> float | None:
    """Max KNYC actual_temperature_f from midnight NY-local up to
    checkpoint_hour NY-local on target_date."""
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=NY)
    end = datetime(target_date.year, target_date.month, target_date.day, checkpoint_hour, 0, tzinfo=NY)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(actual_temperature_f) FROM weather_observations
        WHERE station = 'KNYC' AND observed_time BETWEEN %s AND %s AND actual_temperature_f IS NOT NULL;
        """,
        (start.astimezone(UTC), end.astimezone(UTC)),
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


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
    real_days_raw = cur.fetchall()

    cur.execute("SELECT target_date, model, forecast_high_f FROM multi_model_forecasts WHERE forecast_high_f IS NOT NULL;")
    by_date: dict = {}
    for target_date, model, forecast in cur.fetchall():
        by_date.setdefault(target_date, {})[model] = float(forecast)

    days = []
    for target_date, nws_forecast, settled in real_days_raw:
        mf = by_date.get(target_date, {})
        if all(m in mf for m in INDEPENDENT_MODELS):
            ensemble = statistics.mean([float(nws_forecast)] + list(mf.values()))
            days.append((target_date, ensemble, float(settled)))

    print(f"{len(days)} real days with a 6-model ensemble forecast and Kalshi settlement.\n")

    final_residuals = [settled - ensemble for _, ensemble, settled in days]

    for hour in CHECKPOINT_HOURS:
        signals, residuals = [], []
        for target_date, ensemble, settled in days:
            running_max = max_observed_temp_by_hour(conn, target_date, hour)
            if running_max is None:
                continue
            signals.append(running_max - ensemble)
            residuals.append(settled - ensemble)

        if len(signals) < 10:
            print(f"{hour}:00 ET: too few days with data ({len(signals)}), skipping.")
            continue
        r = pearson_r(signals, residuals)
        print(f"{hour}:00 ET checkpoint: r(running_max - ensemble, settled - ensemble) = {r:+.3f}  (n={len(signals)})")

    conn.close()
    print(
        "\nInterpretation: same thresholds as other feature-exploration scripts. A real signal here only "
        "answers 'does this information exist at all' -- NOT whether it would beat the market's own "
        "same-time price, which is the harder question this deliberately doesn't test yet."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
