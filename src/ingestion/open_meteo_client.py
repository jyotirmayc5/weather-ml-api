"""Open-Meteo Historical Forecast API client -- used only for backfilling
past seasons we have no NWS-based data for (WEATHER_KALSHI_TECHNICAL_PLAN.md).
Deliberately NOT used for live/ongoing collection -- that stays on the real
NWS gridpoint data this whole pipeline is built around; this is a
methodologically distinct, clearly-separated supplementary source for
history that predates our own collection (which started 2026-05-25).

Uses the Previous Runs feature (the `_previous_day1` variable suffix), not
the plain historical-forecast endpoint -- the plain one stitches each run's
freshest hours into a continuous series (effectively near-nowcast quality),
which would badly overstate real day-ahead forecast accuracy if used to
train/backtest a bias-correction model. `_previous_day1` gives the fixed
~24h-ahead forecast for each hour instead, which is what actually matches
this project's real "predict tomorrow's high" pattern.

No API key needed for non-commercial use (verified against the real API, not
assumed). GFS 2m temperature history goes back to March 2021.
"""
from datetime import date

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

BASE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Approximate Central Park / KNYC coordinates -- Open-Meteo snaps to its
# nearest model grid point regardless (returned ~40.7886, -73.9661 for this).
NYC_LATITUDE = 40.7812
NYC_LONGITUDE = -73.9665


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


@_retry
def fetch_historical_hourly(
    start_date: date,
    end_date: date,
    *,
    model: str = "gfs_seamless",
    client: httpx.Client | None = None,
) -> dict:
    """Hourly actual (temperature_2m) and ~24h-ahead forecast
    (temperature_2m_previous_day1) temperatures, in NY-local time, for the
    given [start_date, end_date] inclusive range."""
    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": "temperature_2m,temperature_2m_previous_day1",
        "models": model,
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
    }
    if client is not None:
        resp = client.get(BASE_URL, params=params)
        resp.raise_for_status()
        return resp.json()
    with httpx.Client(timeout=60) as owned_client:
        resp = owned_client.get(BASE_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def daily_highs_from_hourly(payload: dict) -> dict[date, dict]:
    """Groups the hourly response into per-NY-calendar-day max of both the
    actual and the ~24h-ahead-forecast series. The API's `time` values are
    already NY-local (via the timezone= param), so grouping by the date
    portion of each timestamp directly is correct -- no further conversion
    needed. Returns {date: {"forecast_high_f": ..., "actual_high_f": ...}},
    skipping days where either series has no data at all that day."""
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    actuals = hourly.get("temperature_2m", [])
    forecasts = hourly.get("temperature_2m_previous_day1", [])

    by_date: dict[date, dict[str, list[float]]] = {}
    for t, actual, forecast in zip(times, actuals, forecasts):
        day = date.fromisoformat(t[:10])
        bucket = by_date.setdefault(day, {"actual": [], "forecast": []})
        if actual is not None:
            bucket["actual"].append(actual)
        if forecast is not None:
            bucket["forecast"].append(forecast)

    result = {}
    for day, bucket in by_date.items():
        if not bucket["actual"] or not bucket["forecast"]:
            continue
        result[day] = {
            "actual_high_f": max(bucket["actual"]),
            "forecast_high_f": max(bucket["forecast"]),
        }
    return result
