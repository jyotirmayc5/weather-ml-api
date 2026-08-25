"""Python port of the n8n 'Normalize NWS Data', 'Convert to F', and
'Return Observations' Code nodes.

Ported to match the exact behavior of archive/n8n_export.json verbatim,
including its quirks -- see WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 3a/Sec 4a.
Golden fixtures for this module live in tests/fixtures/n8n_js/.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any


def to_js_iso(dt: datetime) -> str:
    """Matches JS Date.prototype.toISOString(): always UTC, always .sss, always Z."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


_DAY_RE = re.compile(r"P(\d+)D")
_HOUR_RE = re.compile(r"T(\d+)H")
_MIN_RE = re.compile(r"T(\d+)M")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _duration_to_hours(duration: str | None) -> int:
    """Matches Normalize NWS Data's durationToHours (day+hour+minute variant)."""
    if not duration:
        return 1
    hours = 0
    day_match = _DAY_RE.search(duration)
    hour_match = _HOUR_RE.search(duration)
    min_match = _MIN_RE.search(duration)
    if day_match:
        hours += int(day_match.group(1)) * 24
    if hour_match:
        hours += int(hour_match.group(1))
    if min_match:
        import math

        hours += math.ceil(int(min_match.group(1)) / 60)
    return hours or 1


def _to_number(value: Any, fallback=None):
    """Matches Normalize NWS Data's toNumber: numeric passthrough, regex-extract
    from strings, fallback (None) for anything else including None/missing."""
    if value is None:
        return fallback
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        match = _NUMBER_RE.search(value)
        return float(match.group(0)) if match else fallback
    return fallback


_NORMALIZE_FIELDS = {
    "temperature": "temperature_c",
    "dewpoint": "dewpoint_c",
    "relativeHumidity": "humidity_pct",
    "apparentTemperature": "feels_like_c",
    "windSpeed": "wind_speed",
    "windDirection": "wind_direction",
    "probabilityOfPrecipitation": "precip_probability_pct",
    "quantitativePrecipitation": "precip_mm",
    "snowfallAmount": "snowfall_mm",
    "iceAccumulation": "ice_mm",
    "skyCover": "sky_cover_pct",
}


def normalize_nws_data(raw_input: dict, now: datetime) -> list[dict]:
    """Port of the 'Normalize NWS Data' Code node. `now` must be tz-aware UTC."""
    payload = raw_input.get("data") or raw_input.get("body") or raw_input
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)

    props = payload.get("properties")
    if not props:
        raise ValueError("No properties found. Check HTTP Request response format.")
    if not isinstance((props.get("temperature") or {}).get("values"), list):
        raise ValueError(
            "No temperature.values found. Use https://api.weather.gov/gridpoints/OKX/33,37 "
            "and set HTTP Response Format to JSON."
        )

    rows: dict[str, dict] = {}

    for source_field, output_field in _NORMALIZE_FIELDS.items():
        series = (props.get(source_field) or {}).get("values")
        if not isinstance(series, list):
            continue
        for item in series:
            valid_time = item.get("validTime")
            if not valid_time:
                continue
            parts = valid_time.split("/")
            start_raw = parts[0]
            duration_raw = parts[1] if len(parts) > 1 else "PT1H"
            start = datetime.fromisoformat(start_raw)
            hours = _duration_to_hours(duration_raw)
            for h in range(hours):
                ts = to_js_iso(start + timedelta(hours=h))
                if ts not in rows:
                    rows[ts] = {
                        "timestamp": ts,
                        "forecast_created_at": to_js_iso(now),
                        "office": props.get("gridId") or None,
                        "gridX": props.get("gridX") or None,
                        "gridY": props.get("gridY") or None,
                        "source": "NWS",
                    }
                rows[ts][output_field] = _to_number(item.get("value"), None)

    next_24h = now + timedelta(hours=24)

    def in_window(row):
        ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        return now < ts <= next_24h

    filtered = sorted((r for r in rows.values() if in_window(r)), key=lambda r: r["timestamp"])

    def default(row, field, fallback):
        value = row.get(field)
        return fallback if value is None else value

    output = []
    for row in filtered:
        output.append(
            {
                "timestamp": row["timestamp"],
                "forecast_created_at": row["forecast_created_at"],
                "office": row["office"],
                "gridX": row["gridX"],
                "gridY": row["gridY"],
                "source": row["source"],
                "temperature_c": default(row, "temperature_c", 0),
                "dewpoint_c": default(row, "dewpoint_c", 0),
                "humidity_pct": default(row, "humidity_pct", 50),
                "feels_like_c": (
                    row["feels_like_c"]
                    if row.get("feels_like_c") is not None
                    else default(row, "temperature_c", 0)
                ),
                "wind_speed": default(row, "wind_speed", 5),
                "wind_direction": default(row, "wind_direction", 180),
                "precip_probability_pct": default(row, "precip_probability_pct", 0),
                "precip_mm": default(row, "precip_mm", 0),
                "snowfall_mm": default(row, "snowfall_mm", 0),
                "ice_mm": default(row, "ice_mm", 0),
                "sky_cover_pct": default(row, "sky_cover_pct", 50),
            }
        )
    return output


def _c_to_f(c):
    if c is None:
        return None
    return round((c * 9 / 5) + 32, 1)


def _first_not_none(*values):
    for v in values:
        if v is not None:
            return v
    return None


def return_observations(items: list[dict]) -> list[dict]:
    """Port of the 'Return Observations' Code node -- the 5-station
    latest-observation merge feeding the weather_observations upsert. Each
    item is one station's raw NWS Feature (the shape fetch_latest_observation
    returns directly)."""
    output = []
    for item in items:
        p = item.get("properties", {})

        pressure_pa = (p.get("barometricPressure") or {}).get("value")
        wind_speed = (p.get("windSpeed") or {}).get("value")
        wind_direction = (p.get("windDirection") or {}).get("value")

        station_url = p.get("station")
        station = station_url.rstrip("/").split("/")[-1] if station_url else None

        if wind_speed is None or wind_direction is None:
            wind_u = wind_v = None
        else:
            wind_u = round(wind_speed * math.sin(math.radians(wind_direction)), 2)
            wind_v = round(wind_speed * math.cos(math.radians(wind_direction)), 2)

        output.append(
            {
                "observed_time": p.get("timestamp"),
                "station": station,
                "actual_temperature_f": _c_to_f((p.get("temperature") or {}).get("value")),
                "actual_dewpoint_f": _c_to_f((p.get("dewpoint") or {}).get("value")),
                "actual_humidity_pct": (p.get("relativeHumidity") or {}).get("value"),
                "actual_pressure_pa": pressure_pa,
                "actual_pressure_hpa": None if pressure_pa is None else round(pressure_pa / 100, 1),
                "actual_wind_speed": wind_speed,
                "actual_wind_direction": wind_direction,
                "wind_u": wind_u,
                "wind_v": wind_v,
                "visibility_m": (p.get("visibility") or {}).get("value"),
                "text_description": p.get("textDescription"),
            }
        )
    return output


def convert_to_f(items: list[dict], now: datetime) -> list[dict]:
    """Port of the 'Convert to F' Code node. `now` must be tz-aware UTC.
    Each item is the .json payload as it would arrive from the /predict call."""
    output = []
    for item in items:
        input_received = item.get("input_received") or {}

        forecast_c = _first_not_none(
            item.get("forecast_temperature_c"),
            input_received.get("temperature_c"),
            item.get("temperature_c"),
            0,
        )
        corrected_c = _first_not_none(item.get("corrected_temperature_c"), forecast_c)
        forecast_time = _first_not_none(item.get("timestamp"), input_received.get("timestamp"), None)
        dewpoint_c = _first_not_none(item.get("dewpoint_c"), input_received.get("dewpoint_c"), None)

        output.append(
            {
                "forecast_time": forecast_time,
                "forecast_created_at": to_js_iso(now),
                "forecast_temperature_c": forecast_c,
                "corrected_temperature_c": corrected_c,
                "forecast_temperature_f": _c_to_f(forecast_c),
                "corrected_temperature_f": _c_to_f(corrected_c),
                "predicted_error_c": _first_not_none(item.get("predicted_error_c"), None),
                "predicted_error_f": _c_to_f(item.get("predicted_error_c")),
                "dewpoint_c": dewpoint_c,
                "dewpoint_f": _c_to_f(dewpoint_c),
                "humidity_pct": _first_not_none(
                    item.get("humidity_pct"), input_received.get("humidity_pct"), None
                ),
                "wind_speed": _first_not_none(
                    item.get("wind_speed"), input_received.get("wind_speed"), None
                ),
                "wind_direction": _first_not_none(
                    item.get("wind_direction"), input_received.get("wind_direction"), None
                ),
                "sky_cover_pct": _first_not_none(
                    item.get("sky_cover_pct"), input_received.get("sky_cover_pct"), None
                ),
                "precip_probability_pct": _first_not_none(
                    item.get("precip_probability_pct"),
                    input_received.get("precip_probability_pct"),
                    None,
                ),
                "source": _first_not_none(item.get("source"), input_received.get("source"), "NWS"),
            }
        )
    return output
