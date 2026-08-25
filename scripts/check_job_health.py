"""Health check for all 6 cron jobs -- catches two failure modes job_runs
alone can't distinguish from "healthy": (1) an explicit failed status, and
(2) staleness, i.e. a job that hasn't logged ANY run (success or failure)
recently enough. (2) exists specifically because a job that crashes before
track_job_run() ever executes (e.g. a missing DATABASE_URL, as happened to
kalshi_settlement_job on its first real Render-scheduled run) writes NOTHING
to job_runs at all -- "zero failed rows" in that case means "zero failures we
could see", not "zero failures". Run this periodically instead of just
checking for failed rows.
"""
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

import psycopg2

# job_name -> max acceptable gap since its last logged run (any status)
EXPECTED_INTERVALS = {
    "latest_observations_job_shadow": timedelta(minutes=25),
    "hourly_forecast_job_shadow": timedelta(minutes=90),
    "daily_high_forecast_job_shadow": timedelta(hours=26),
    "corrected_high_update_job_shadow": timedelta(hours=26),
    "actual_high_update_job_shadow": timedelta(hours=26),
    "kalshi_settlement_job": timedelta(hours=26),
}


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def main():
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

    for job_name, max_gap in EXPECTED_INTERVALS.items():
        cur.execute(
            "SELECT started_at, status, error_message FROM job_runs "
            "WHERE job_name = %s ORDER BY started_at DESC LIMIT 1;",
            (job_name,),
        )
        row = cur.fetchone()

        if row is None:
            problems.append(f"{job_name}: NO RUNS EVER LOGGED")
            continue

        started_at, status, error_message = row
        age = now - started_at

        if status == "failed":
            problems.append(f"{job_name}: last run FAILED at {started_at} -- {error_message}")
        elif age > max_gap:
            problems.append(
                f"{job_name}: STALE -- last run {started_at} ({age} ago, expected within {max_gap})"
            )
        else:
            print(f"OK  {job_name}: last run {started_at} ({status}), {age} ago")

    conn.close()

    if problems:
        print("\n=== PROBLEMS ===")
        for p in problems:
            print(f"  {p}")
        return 1

    print("\nAll jobs healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
