"""Python port of the n8n 'Code in JavaScript' EOD actuals + pressure-feature
Code node. Golden fixtures for this module live in tests/fixtures/n8n_js/.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _c_to_f(c):
    return round((c * 9 / 5) + 32, 1)


def _js_sum(values: list[float]) -> float:
    """Matches JS's Array.prototype.reduce((a,b)=>a+b,0): naive left-to-right
    float accumulation. Deliberately NOT Python's built-in sum() -- as of
    Python 3.12+, sum() uses a more numerically precise summation algorithm
    (Neumaier summation) for floats, which can disagree with JS's naive
    accumulation right at a rounding boundary. Confirmed empirically: for
    this module's EOD pressure averaging, Python's sum() and JS's reduce()
    diverge by exactly the accumulated float error, enough to flip the final
    .toFixed(1)/round(x,1) to a different digit. See
    WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 3a."""
    total = 0.0
    for v in values:
        total += v
    return total


def _avg(values: list[float]):
    return round(_js_sum(values) / len(values), 1) if values else None


def eod_actuals_and_pressure(raw_input: dict, now: datetime) -> dict:
    """Port of the EOD 'Code in JavaScript' node. `now` must be tz-aware UTC."""
    features = raw_input.get("features") or []

    target_date = now.astimezone(NY).strftime("%Y-%m-%d")

    station = "UNKNOWN"
    if features:
        station_url = (features[0].get("properties") or {}).get("station")
        if station_url:
            station = station_url.rstrip("/").split("/")[-1]

    temps = []
    for f in features:
        value = (f.get("properties") or {}).get("temperature", {}).get("value")
        if value is not None:
            temps.append(_c_to_f(value))

    if not temps:
        raise ValueError(f"No observations found for {target_date}")

    max_temp = max(temps)

    pressure_obs = []
    for f in features:
        props = f.get("properties") or {}
        pressure_pa = (props.get("barometricPressure") or {}).get("value")
        timestamp = props.get("timestamp")
        if pressure_pa is None or not timestamp:
            continue
        hour_ny = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(NY).hour
        pressure_obs.append({"pressure_hpa": pressure_pa / 100, "hourNY": hour_ny})

    avg_pressure_hpa = _avg([o["pressure_hpa"] for o in pressure_obs])

    def nearest_pressure(target_hour: int):
        if not pressure_obs:
            return None
        closest = min(pressure_obs, key=lambda o: abs(o["hourNY"] - target_hour))
        return round(closest["pressure_hpa"], 1)

    pressure_6am_hpa = nearest_pressure(6)
    pressure_12pm_hpa = nearest_pressure(12)
    pressure_6pm_hpa = nearest_pressure(18)

    morning_pressures = [o["pressure_hpa"] for o in pressure_obs if 6 <= o["hourNY"] < 12]
    afternoon_pressures = [o["pressure_hpa"] for o in pressure_obs if 12 <= o["hourNY"] < 18]
    morning_pressure_hpa = _avg(morning_pressures)
    afternoon_pressure_hpa = _avg(afternoon_pressures)

    pressure_change_hpa = (
        round(afternoon_pressure_hpa - morning_pressure_hpa, 1)
        if morning_pressure_hpa is not None and afternoon_pressure_hpa is not None
        else None
    )

    return {
        "target_date": target_date,
        "station": station,
        "actual_high_f": round(max_temp, 1),
        "morning_pressure_hpa": morning_pressure_hpa,
        "afternoon_pressure_hpa": afternoon_pressure_hpa,
        "pressure_change_hpa": pressure_change_hpa,
        "avg_pressure_hpa": avg_pressure_hpa,
        "pressure_6am_hpa": pressure_6am_hpa,
        "pressure_12pm_hpa": pressure_12pm_hpa,
        "pressure_6pm_hpa": pressure_6pm_hpa,
        "observations_used": len(temps),
    }
