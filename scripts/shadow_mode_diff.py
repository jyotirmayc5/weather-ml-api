"""Formal shadow-mode comparison: production tables vs their _v2 staging
counterparts, per WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 4 Step 5's rule that
shadow mode needs 7-14 days of matching output before any n8n trigger gets
cut over.

Not a pytest suite -- this is a report you read, run against real production
data, same spirit as src/backtest/ later. Run with:
    venv/Scripts/python.exe scripts/shadow_mode_diff.py

IMPORTANT ON INTERPRETING THE OUTPUT: for weather_observations and
weather_predictions in particular, rows that exist in only one side are
EXPECTED and not by themselves a problem -- shadow and real jobs poll on the
same frequency but not the same exact clock tick, so they often catch
different NWS publish moments (see the manual spot-check in the plan's
checklist: KLGA/KJFK/KEWR/KTEB all showed this pattern on the very first
run). The signal that actually matters is the "key matched, values differ"
count -- that means both sides saw the SAME real observation/forecast and
computed a DIFFERENT result, which is a real discrepancy worth investigating,
not polling asynchrony.
"""
import sys
from decimal import Decimal
from urllib.parse import unquote, urlsplit

import psycopg2
import psycopg2.extras


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


def values_close(a, b, tolerance=0.05):
    if a is None or b is None:
        return a == b
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return abs(float(a) - float(b)) <= tolerance
    return a == b


def compare_tables(conn, real_table, shadow_table, key_cols, compare_cols):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cols_sql = ", ".join(key_cols + compare_cols)

    cur.execute(f"SELECT {cols_sql} FROM {real_table};")
    real_rows = {tuple(row[k] for k in key_cols): row for row in cur.fetchall()}

    cur.execute(f"SELECT {cols_sql} FROM {shadow_table};")
    shadow_rows = {tuple(row[k] for k in key_cols): row for row in cur.fetchall()}

    real_only = set(real_rows) - set(shadow_rows)
    shadow_only = set(shadow_rows) - set(real_rows)
    matched_keys = set(real_rows) & set(shadow_rows)

    exact_matches = 0
    mismatches = []
    for key in matched_keys:
        real_row, shadow_row = real_rows[key], shadow_rows[key]
        diffs = {
            col: (real_row[col], shadow_row[col])
            for col in compare_cols
            if not values_close(real_row[col], shadow_row[col])
        }
        if diffs:
            mismatches.append((key, diffs))
        else:
            exact_matches += 1

    print(f"\n=== {real_table}  vs  {shadow_table} ===")
    print(f"  rows only in {real_table} (real, not yet seen in shadow): {len(real_only)}")
    print(f"  rows only in {shadow_table} (shadow, not yet seen in real): {len(shadow_only)}")
    print(f"  keys present in both: {len(matched_keys)}")
    print(f"    -> exact match on all compared fields: {exact_matches}")
    print(f"    -> SAME KEY BUT DIFFERENT VALUES (real discrepancy): {len(mismatches)}")
    for key, diffs in mismatches[:10]:
        print(f"       key={key}")
        for col, (real_val, shadow_val) in diffs.items():
            print(f"         {col}: real={real_val!r}  shadow={shadow_val!r}")
    if len(mismatches) > 10:
        print(f"       ... and {len(mismatches) - 10} more")


def main():
    conn = connect()

    compare_tables(
        conn,
        "weather_observations",
        "weather_observations_v2",
        key_cols=["station", "observed_time"],
        compare_cols=[
            "actual_temperature_f",
            "actual_dewpoint_f",
            "actual_humidity_pct",
            "actual_pressure_hpa",
            "actual_wind_speed",
            "actual_wind_direction",
            "wind_u",
            "wind_v",
            "visibility_m",
        ],
    )

    compare_tables(
        conn,
        "weather_predictions",
        "weather_predictions_v2",
        key_cols=["forecast_time", "source"],
        compare_cols=[
            "forecast_temperature_f",
            "corrected_temperature_f",
            "humidity_pct",
            "wind_speed",
            "wind_direction",
            "sky_cover_pct",
            "precip_probability_pct",
            "dewpoint_f",
        ],
    )

    compare_tables(
        conn,
        "weather_daily_high_predictions",
        "weather_daily_high_predictions_v2",
        key_cols=["target_date", "station"],
        compare_cols=[
            "forecast_high_f",
            "corrected_high_f",
            "actual_high_f",
            "raw_error_f",
            "corrected_error_f",
            "avg_humidity_pct",
            "avg_dewpoint_f",
            "avg_sky_cover_pct",
            "max_precip_probability_pct",
            "avg_wind_speed",
            "avg_wind_sin",
            "avg_wind_cos",
            "peak_heating_cloud_pct",
            "peak_heating_temp_f",
            "lead_hours",
            "month",
            "day_of_year",
        ],
    )

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
