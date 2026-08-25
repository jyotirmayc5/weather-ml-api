"""Builds realistic-but-synthetic NWS-API-shaped input fixtures for the harness.
Hand-constructed (not pulled live) so we can deliberately hit edge cases:
missing fields, the peak-heating window, and a real DST spring-forward boundary.
"""
import json
from datetime import datetime, timedelta, timezone

out = __import__("pathlib").Path(__file__).resolve().parent / "inputs"
out.mkdir(exist_ok=True)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def gridpoint_payload(start_utc, hours, *, gap_hours=()):
    """Builds a NWS gridpoints-style properties payload with `hours` hourly
    entries starting at start_utc. Hours listed in gap_hours (offsets from
    start) omit dewpoint/humidity/wind/precip/sky fields to exercise the
    Normalize NWS Data script's `?? <default>` fallbacks. temperature is
    always present (the script throws without it)."""
    fields = {
        "temperature": [],
        "dewpoint": [],
        "relativeHumidity": [],
        "windSpeed": [],
        "windDirection": [],
        "probabilityOfPrecipitation": [],
        "skyCover": [],
    }
    for h in range(hours):
        t = start_utc + timedelta(hours=h)
        vt = f"{iso(t)}/PT1H"
        # a gentle diurnal temperature curve, peaking mid-afternoon NY time
        temp_c = 15 + 8 * max(0, 1 - abs((h % 24) - 15) / 8)
        fields["temperature"].append({"validTime": vt, "value": round(temp_c, 1)})
        if h in gap_hours:
            continue
        fields["dewpoint"].append({"validTime": vt, "value": round(temp_c - 5, 1)})
        fields["relativeHumidity"].append({"validTime": vt, "value": 55 + (h % 10)})
        fields["windSpeed"].append({"validTime": vt, "value": 10 + (h % 5)})
        fields["windDirection"].append({"validTime": vt, "value": (h * 15) % 360})
        fields["probabilityOfPrecipitation"].append({"validTime": vt, "value": (h * 3) % 40})
        fields["skyCover"].append({"validTime": vt, "value": (h * 7) % 100})

    return {
        "properties": {
            "gridId": "OKX",
            "gridX": 33,
            "gridY": 37,
            **{k: {"values": v} for k, v in fields.items()},
        }
    }


# ---- normalize_normal: hourly ingestion, run at 15:02 UTC (11:02am EDT) ----
normalize_start = datetime(2026, 6, 16, 12, tzinfo=timezone.utc)
normalize_payload = gridpoint_payload(normalize_start, 36, gap_hours={5, 20})
(out / "normalize_normal.json").write_text(json.dumps(normalize_payload, indent=2))

# ---- forecast_high_normal: 9:45am EDT run, predicting tomorrow (no DST) ----
fh_start = datetime(2026, 6, 16, 4, tzinfo=timezone.utc)  # covers June 17 NY fully
fh_payload = gridpoint_payload(fh_start, 48)
(out / "forecast_high_normal.json").write_text(json.dumps(fh_payload, indent=2))

# ---- forecast_high_dst: predicting March 8 2026, the US spring-forward day ----
dst_start = datetime(2026, 3, 7, 4, tzinfo=timezone.utc)  # covers March 8 NY fully
dst_payload = gridpoint_payload(dst_start, 48)
(out / "forecast_high_dst.json").write_text(json.dumps(dst_payload, indent=2))


# ---- eod_actuals_normal: KNYC station observations for June 15 2026 (NY) ----
def obs_feature(t_utc, temp_c, pressure_pa):
    return {
        "properties": {
            "station": "https://api.weather.gov/stations/KNYC",
            "timestamp": iso(t_utc),
            "temperature": {"value": temp_c},
            "barometricPressure": {"value": pressure_pa},
        }
    }


obs_start = datetime(2026, 6, 15, 4, tzinfo=timezone.utc)  # ~midnight EDT June 15
features = []
for h in range(24):
    t = obs_start + timedelta(hours=h)
    temp_c = 15 + 8 * max(0, 1 - abs((h % 24) - 15) / 8)
    # pressure falling through the day: 1015 hPa in the morning down to 1008 hPa evening
    pressure_hpa = 1015 - (h * 0.3)
    features.append(obs_feature(t, round(temp_c, 1), round(pressure_hpa * 100)))
(out / "eod_actuals_normal.json").write_text(json.dumps({"features": features}, indent=2))


# ---- convert_to_f_normal: items shaped like real main.py /predict responses ----
convert_items = [
    {
        "timestamp": "2026-06-16T15:00:00+00:00",
        "forecast_temperature_c": 20.0,
        "corrected_temperature_c": 21.35,
        "bias_c": 1.35,
        "bias_f": 2.43,
        "dewpoint_c": 14.0,
        "humidity_pct": 60,
        "wind_speed": 12,
        "wind_direction": 200,
        "precip_probability_pct": 10,
        "sky_cover_pct": 15,
    },
    {
        "timestamp": "2026-06-16T16:00:00+00:00",
        "forecast_temperature_c": 21.0,
        "corrected_temperature_c": 22.0,
        "bias_c": 1.0,
        "bias_f": 1.8,
        "dewpoint_c": None,
        "humidity_pct": None,
        "wind_speed": None,
        "wind_direction": None,
        "precip_probability_pct": None,
        "sky_cover_pct": None,
    },
]
(out / "convert_to_f_normal.json").write_text(json.dumps(convert_items, indent=2))

# ---- return_observations_normal: 5-station "latest observation" merge ----
# Deliberately covers: missing pressure (KLGA), missing wind (KJFK), missing
# dewpoint (KTEB), and a normal case with everything present (KNYC, KEWR).
def latest_obs_feature(
    station_url, *, temp_c=18.0, dewpoint_c=12.0, humidity=60, pressure_pa=101500,
    wind_speed=8.0, wind_direction=200, visibility_m=16000, text="Partly Cloudy",
    timestamp="2026-06-16T15:51:00+00:00",
):
    return {
        "properties": {
            "station": station_url,
            "timestamp": timestamp,
            "temperature": {"value": temp_c},
            "dewpoint": {"value": dewpoint_c} if dewpoint_c is not None else {"value": None},
            "relativeHumidity": {"value": humidity},
            "barometricPressure": {"value": pressure_pa},
            "windSpeed": {"value": wind_speed},
            "windDirection": {"value": wind_direction},
            "visibility": {"value": visibility_m},
            "textDescription": text,
        }
    }


return_obs_items = [
    latest_obs_feature("https://api.weather.gov/stations/KNYC"),
    latest_obs_feature("https://api.weather.gov/stations/KLGA", pressure_pa=None),
    latest_obs_feature("https://api.weather.gov/stations/KJFK", wind_speed=None, wind_direction=None),
    latest_obs_feature("https://api.weather.gov/stations/KEWR"),
    latest_obs_feature("https://api.weather.gov/stations/KTEB", dewpoint_c=None),
]
(out / "return_observations_normal.json").write_text(json.dumps(return_obs_items, indent=2))

print("wrote fixtures to", out)
