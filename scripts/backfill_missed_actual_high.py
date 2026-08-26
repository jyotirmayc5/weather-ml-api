"""One-off utility: backfills actual_high_f + pressure features + errors for
a specific missed day directly into the REAL (not shadow) weather_daily_high_predictions
table -- for when n8n misses its 11:55pm ET EOD run (as happened during the
Postgres-credential outage found and fixed in this session, 2026-08-25) and
the gap needs manual filling.

Only legitimate for the ACTUALS side -- NWS's observation archive covers past
dates, unlike its forecast endpoints (current/future only, no archive, see
WEATHER_KALSHI_TECHNICAL_PLAN.md). There is no equivalent way to backfill a
MISSED forecast day; don't try to fabricate one.

Reuses the exact same functions as jobs/actual_high_update_job.py (fetch_day_observations,
eod_actuals_and_pressure) and the same real-table UPDATE shape, including the
faithfully-preserved quirk of filtering on station = 'KNYC' literally rather
than the computed station value.

Usage: venv/Scripts/python.exe -m scripts.backfill_missed_actual_high 2026-08-25
"""
import sys
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlsplit

from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from src.db.models import WeatherDailyHighPrediction
from src.features.actuals import eod_actuals_and_pressure
from src.ingestion.nws_client import fetch_day_observations


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def main(target_date_str: str):
    target_date = date.fromisoformat(target_date_str)
    # any moment during the target NY-local day works -- use noon UTC, safely
    # inside it regardless of EDT/EST
    now = datetime(target_date.year, target_date.month, target_date.day, 12, tzinfo=timezone.utc)

    payload = fetch_day_observations("KNYC", now)
    result = eod_actuals_and_pressure(payload, now)
    print(f"Computed for {result['target_date']}: actual_high_f={result['actual_high_f']}")

    if result["target_date"] != target_date_str:
        print(f"WARNING: computed target_date {result['target_date']} != requested {target_date_str}, aborting")
        return 1

    engine = create_engine(load_dsn())
    session = Session(bind=engine)

    stmt = (
        update(WeatherDailyHighPrediction)
        .where(
            WeatherDailyHighPrediction.target_date == result["target_date"],
            WeatherDailyHighPrediction.station == "KNYC",
        )
        .values(
            actual_high_f=result["actual_high_f"],
            morning_pressure_hpa=result["morning_pressure_hpa"],
            afternoon_pressure_hpa=result["afternoon_pressure_hpa"],
            pressure_change_hpa=result["pressure_change_hpa"],
            avg_pressure_hpa=result["avg_pressure_hpa"],
            pressure_6am_hpa=result["pressure_6am_hpa"],
            pressure_12pm_hpa=result["pressure_12pm_hpa"],
            pressure_6pm_hpa=result["pressure_6pm_hpa"],
            raw_error_f=result["actual_high_f"] - WeatherDailyHighPrediction.forecast_high_f,
            corrected_error_f=result["actual_high_f"] - WeatherDailyHighPrediction.corrected_high_f,
        )
    )
    exec_result = session.execute(stmt)
    session.commit()
    print(f"Rows updated: {exec_result.rowcount}")
    session.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m scripts.backfill_missed_actual_high YYYY-MM-DD")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
