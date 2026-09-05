"""EXPLORATORY: tests whether inter-station morning temperature spread
(KEWR-KJFK, KNYC-KJFK, KLGA-KJFK) correlates with KNYC daily-high forecast
error -- a specific, testable hypothesis (marine layer / sea breeze
penetration / urban heat island signal) suggested for this project, distinct
from and untested by the ensemble-spread/climatology check already run
(scripts/feature_exploration_ensemble_climatology.py, both weak: r=+0.110
and r=-0.062).

Uses each station's REAL observation closest to the ~9:45am ET forecast
moment for our 92 real production days -- deliberately a morning-only
snapshot, not a full-day statistic, to avoid leaking same-day afternoon
information into what's supposed to be a forecast-time feature (same
look-ahead discipline as daily_high_backtest.py's forecast_high_f/raw_error_f
choice). Correlation only, same reasoning as the other feature-exploration
scripts: with 92 days, jumping straight to a conditioned model before there's
evidence of a real signal risks fitting noise.
"""
import statistics
import sys
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import psycopg2

NY = ZoneInfo("America/New_York")
STATIONS = ["KEWR", "KJFK", "KLGA", "KNYC", "KTEB"]


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
    """(target_date, forecast_high_f, residual) for our 92 real production days."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.target_date, d.forecast_high_f, k.settled_value_f - d.forecast_high_f
        FROM weather_daily_high_predictions d
        JOIN kalshi_settlements k ON k.target_date = d.target_date
        WHERE d.station = 'KNYC' AND d.forecast_high_f IS NOT NULL
        ORDER BY d.target_date;
        """
    )
    return [(target_date, float(forecast), float(residual)) for target_date, forecast, residual in cur.fetchall()]


def nearest_temp_near_945am(conn, station: str, target_date) -> float | None:
    """The station's actual_temperature_f closest to 9:45am ET on target_date,
    searched within a +/-2 hour window (observations arrive roughly hourly,
    so this comfortably covers the nearest real reading without pulling in
    a whole day's worth of irrelevant rows)."""
    target_ts = datetime(target_date.year, target_date.month, target_date.day, 9, 45, tzinfo=NY)
    window_start = target_ts - timedelta(hours=2)
    window_end = target_ts + timedelta(hours=2)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT actual_temperature_f, observed_time
        FROM weather_observations
        WHERE station = %s
          AND observed_time BETWEEN %s AND %s
          AND actual_temperature_f IS NOT NULL
        ORDER BY observed_time;
        """,
        (station, window_start, window_end),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    closest = min(rows, key=lambda r: abs((r[1].astimezone(NY) - target_ts).total_seconds()))
    return float(closest[0])


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
    days = load_days(conn)
    print(f"Loaded {len(days)} real production days: {days[0][0]} to {days[-1][0]}\n")

    station_temps = {station: [] for station in STATIONS}
    residuals = []
    dates_used = []

    for target_date, _forecast, residual in days:
        temps = {station: nearest_temp_near_945am(conn, station, target_date) for station in STATIONS}
        if any(t is None for t in temps.values()):
            continue
        for station in STATIONS:
            station_temps[station].append(temps[station])
        residuals.append(residual)
        dates_used.append(target_date)

    conn.close()
    print(f"Days with all 5 stations' morning readings available: {len(dates_used)} of {len(days)}\n")

    pairs = [
        ("KEWR - KJFK", "KEWR", "KJFK"),
        ("KNYC - KJFK", "KNYC", "KJFK"),
        ("KLGA - KJFK", "KLGA", "KJFK"),
    ]

    print("=== Morning (~9:45am ET) inter-station spread vs forecast error ===")
    for label, a, b in pairs:
        spread = [ta - tb for ta, tb in zip(station_temps[a], station_temps[b])]
        r = pearson_r(spread, residuals)
        print(f"  {label:15s} r={r:+.3f}  mean={statistics.mean(spread):+.2f}  stdev={statistics.stdev(spread):.2f}  (n={len(spread)})")

    print(
        "\nInterpretation guide (same as the other feature-exploration scripts): |r| < ~0.2 is weak/"
        "noisy at this sample size, ~0.2-0.4 is a real but modest relationship worth watching, "
        "> ~0.4 would be strong enough to consider wiring in now."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
