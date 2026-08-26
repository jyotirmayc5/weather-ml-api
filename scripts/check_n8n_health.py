"""Checks staleness of the REAL n8n-driven tables (weather_predictions,
weather_observations, weather_daily_high_predictions) -- these are written
by n8n directly, not by anything we control, so job_runs / check_job_health.py
have zero visibility into whether n8n itself is still running. Built after
discovering, via a shadow-mode comparison, that n8n had silently stopped
updating all 3 tables simultaneously for ~28-44 hours -- nothing in our own
monitoring caught it; it was found by accident. This is the fix for that
blind spot specifically.

Unlike check_job_health.py, there's no "status" column to check here (n8n
doesn't write to job_runs) -- staleness of the data itself is the only
signal available.
"""
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def main():
    import psycopg2

    parts = urlsplit(load_dsn())
    conn = psycopg2.connect(
        host=parts.hostname,
        port=parts.port,
        user=unquote(parts.username),
        password=unquote(parts.password),
        dbname=parts.path.lstrip("/"),
    )
    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    problems = []

    # 1. weather_observations -- n8n's 15-min flow
    cur.execute("SELECT MAX(created_at) FROM weather_observations;")
    (last_obs,) = cur.fetchone()
    age = now - last_obs
    tolerance = timedelta(minutes=30)
    if age > tolerance:
        problems.append(f"weather_observations: STALE -- last row {last_obs} ({age} ago, expected within {tolerance})")
    else:
        print(f"OK  weather_observations: last row {last_obs} ({age} ago)")

    # 2. weather_predictions -- n8n's hourly flow
    cur.execute("SELECT MAX(forecast_created_at) FROM weather_predictions;")
    (last_pred,) = cur.fetchone()
    age = now - last_pred
    tolerance = timedelta(minutes=90)
    if age > tolerance:
        problems.append(f"weather_predictions: STALE -- last update {last_pred} ({age} ago, expected within {tolerance})")
    else:
        print(f"OK  weather_predictions: last update {last_pred} ({age} ago)")

    # 3. weather_daily_high_predictions -- n8n's 9:45am ET daily flow
    cur.execute("SELECT MAX(prediction_created_at), MAX(target_date) FROM weather_daily_high_predictions;")
    last_daily, last_target_date = cur.fetchone()
    age = now - last_daily
    tolerance = timedelta(hours=26)
    if age > tolerance:
        problems.append(
            f"weather_daily_high_predictions: STALE -- last prediction {last_daily} "
            f"({age} ago, expected within {tolerance}), latest target_date={last_target_date}"
        )
    else:
        print(f"OK  weather_daily_high_predictions: last prediction {last_daily} ({age} ago), latest target_date={last_target_date}")

    # 4. EOD actuals flow (11:55pm ET) -- no dedicated timestamp column for
    # this UPDATE, so proxy-check whether yesterday's KNYC row got its
    # actual_high_f filled in at all.
    yesterday_ny = (now.astimezone(NY) - timedelta(days=1)).date()
    cur.execute(
        "SELECT actual_high_f FROM weather_daily_high_predictions "
        "WHERE station = 'KNYC' AND target_date = %s;",
        (yesterday_ny,),
    )
    row = cur.fetchone()
    if row is None:
        problems.append(f"EOD actuals: no row at all for KNYC on {yesterday_ny} (yesterday, NY-approx)")
    elif row[0] is None:
        problems.append(f"EOD actuals: KNYC row for {yesterday_ny} exists but actual_high_f is still NULL")
    else:
        print(f"OK  EOD actuals: KNYC actual_high_f for {yesterday_ny} = {row[0]}")

    conn.close()

    if problems:
        print("\n=== PROBLEMS (n8n may be down or a flow has stopped) ===")
        for p in problems:
            print(f"  {p}")
        return 1

    print("\nAll real n8n-driven tables healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
