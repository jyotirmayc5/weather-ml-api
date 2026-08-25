"""Python port of the n8n 'Forecast HIGH' Code node (and its 3 near-duplicate
copies, Forecast HIGH1/2/3, which differ only in the station/source labels --
parameterized here instead of duplicated, per WEATHER_KALSHI_TECHNICAL_PLAN.md).

This is a faithful port, including the lead_hours double-timezone-conversion
bug described in the plan's Sec 4 Step 2 -- do NOT "fix" it here without
updating the plan and the golden fixtures deliberately. Golden fixtures for
this module live in tests/fixtures/n8n_js/.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

_DAY_RE = re.compile(r"P(\d+)D")
_HOUR_RE = re.compile(r"T(\d+)H")


def _duration_to_hours(duration: str | None) -> int:
    """Matches Forecast HIGH's durationToHours -- note: unlike Normalize NWS
    Data's version, this one has NO minute-duration handling."""
    if not duration:
        return 1
    hours = 0
    day_match = _DAY_RE.search(duration)
    hour_match = _HOUR_RE.search(duration)
    if day_match:
        hours += int(day_match.group(1)) * 24
    if hour_match:
        hours += int(hour_match.group(1))
    return hours or 1


def _c_to_f(c):
    return round((c * 9 / 5) + 32, 1)


def _js_sum(values: list[float]) -> float:
    """Matches JS's Array.prototype.reduce((a,b)=>a+b,0): naive left-to-right
    float accumulation, deliberately NOT Python's built-in sum() -- see the
    identical helper (and its rationale) in src/features/actuals.py."""
    total = 0.0
    for v in values:
        total += v
    return total


def _avg(values: list[float]):
    return round(_js_sum(values) / len(values), 1) if values else None


def _max(values: list[float]):
    return max(values) if values else None


def _ny_calendar_date(dt: datetime) -> str:
    """Matches the en-CA Intl formatting used throughout for YYYY-MM-DD in NY."""
    return dt.astimezone(NY).strftime("%Y-%m-%d")


def _target_end_utc_buggy(ny_date_str: str) -> datetime:
    """Faithfully reproduces the double-timezone-conversion bug in the real
    JS: `new Date(new Date(`${nyDate}T23:59:59`).toLocaleString("en-US", {
    timeZone: "America/New_York" }))`.

    Under the TZ=UTC assumption documented in the plan (matching Render's
    presumed container timezone, and matching tests/fixtures/n8n_js/harness.js):
    1. The date-only string is parsed as if it were already UTC (JS parses a
       timezone-less datetime string as system-local time; harness pins that
       to UTC).
    2. That instant is correctly converted to its NY wall-clock numbers.
    3. Those NY wall-clock numbers are then WRONGLY reinterpreted as UTC again
       (the second `new Date(string)` parse, again resolved as system-local =
       UTC), rather than converted back to a UTC instant. This silently
       shifts the result by the current NY UTC offset a second time.
    """
    year, month, day = (int(p) for p in ny_date_str.split("-"))
    naive_target = datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc)
    ny_wallclock_naive = naive_target.astimezone(NY).replace(tzinfo=None)
    return ny_wallclock_naive.replace(tzinfo=timezone.utc)


_FIELDS = {
    "temperature": "temperature_c",
    "dewpoint": "dewpoint_c",
    "relativeHumidity": "humidity_pct",
    "skyCover": "sky_cover_pct",
    "probabilityOfPrecipitation": "precip_probability_pct",
    "windSpeed": "wind_speed",
    "windDirection": "wind_direction",
}


def forecast_high(
    raw_input: dict,
    now: datetime,
    *,
    station: str = "KNYC",
    source: str = "NWS OKX/33,37",
) -> dict:
    """Port of the 'Forecast HIGH' Code node. `now` must be tz-aware UTC."""
    payload = raw_input.get("data") or raw_input.get("body") or raw_input
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    props = payload.get("properties", {})

    ny_date = _ny_calendar_date(now + timedelta(hours=24))

    rows: dict[str, dict[str, Any]] = {}

    for source_field, output_field in _FIELDS.items():
        series = (props.get(source_field) or {}).get("values") or []
        for item in series:
            valid_time = item["validTime"]
            parts = valid_time.split("/")
            start_raw = parts[0]
            duration_raw = parts[1] if len(parts) > 1 else "PT1H"
            start = datetime.fromisoformat(start_raw)
            hours = _duration_to_hours(duration_raw)
            for h in range(hours):
                ts_dt = start + timedelta(hours=h)
                if _ny_calendar_date(ts_dt) != ny_date:
                    continue
                key = ts_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
                    f"{ts_dt.microsecond // 1000:03d}Z"
                )
                if key not in rows:
                    rows[key] = {"timestamp": key}
                rows[key][output_field] = item.get("value")

    day_rows = list(rows.values())

    def vals(field):
        return [r[field] for r in day_rows if r.get(field) is not None]

    temps_f = [_c_to_f(v) for v in vals("temperature_c")]

    def hour_ny(ts_iso: str) -> int:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(NY).hour

    peak_rows = [r for r in day_rows if 12 <= hour_ny(r["timestamp"]) <= 16]

    def peak_vals(field):
        return [r[field] for r in peak_rows if r.get(field) is not None]

    import math

    wind_dirs = vals("wind_direction")
    avg_wind_sin = _avg([math.sin(math.radians(d)) for d in wind_dirs])
    avg_wind_cos = _avg([math.cos(math.radians(d)) for d in wind_dirs])

    target_end_utc = _target_end_utc_buggy(ny_date)
    lead_hours = round((target_end_utc - now).total_seconds() / 3600, 1)

    # Matches `nowNY = new Date()` (misleadingly named -- it's really just `now`
    # again) and the month/day_of_year computed from it.
    month = now.astimezone(NY).month
    day_of_year = now.astimezone(timezone.utc).timetuple().tm_yday

    peak_temps_f = [_c_to_f(v) for v in peak_vals("temperature_c")]

    return {
        "prediction_created_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{now.microsecond // 1000:03d}Z",
        "target_date": ny_date,
        "station": station,
        "forecast_high_f": _max(temps_f),
        "forecast_low_f": min(temps_f) if temps_f else None,
        "corrected_high_f": None,
        "avg_humidity_pct": _avg(vals("humidity_pct")),
        "avg_dewpoint_f": _avg([_c_to_f(v) for v in vals("dewpoint_c")]),
        "avg_sky_cover_pct": _avg(vals("sky_cover_pct")),
        "max_precip_probability_pct": _max(vals("precip_probability_pct")),
        "peak_heating_cloud_pct": _avg(peak_vals("sky_cover_pct")),
        "peak_heating_temp_f": _max(peak_temps_f),
        "avg_wind_speed": _avg(vals("wind_speed")),
        "avg_wind_sin": avg_wind_sin,
        "avg_wind_cos": avg_wind_cos,
        "lead_hours": lead_hours,
        "month": month,
        "day_of_year": day_of_year,
        "source": source,
    }
