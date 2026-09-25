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
        # 429 confirmed real and hit live in production (WEATHER_KALSHI_TECHNICAL_PLAN.md
        # Sec 5g): jobs/daily_prediction_job.py's first real run called this 5 times in
        # under a second (one per independent model, no spacing), and every single one
        # hit Open-Meteo's rate limit -- previously not retried at all since only >=500
        # counted, silently degrading the live ensemble to NWS-only with no error raised.
        # Same fix already applied to src/kalshi/client.py for the same failure class.
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
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


@_retry
def fetch_live_previous_day1_high(model: str, target_date: date, client: httpx.Client | None = None) -> float | None:
    """The model's ~24h-ahead forecast high for target_date -- what
    jobs/daily_prediction_job.py uses each morning to match the exact
    methodology validated in scripts/test_multi_model_ensemble_adjustment.py
    (yesterday's forecast for today), rather than today's freshest/current
    forecast.

    Deliberately queries the HISTORICAL endpoint (BASE_URL), not the live one
    (LIVE_BASE_URL) -- confirmed live, not assumed (WEATHER_KALSHI_TECHNICAL_PLAN.md
    Sec 5g): the live endpoint's temperature_2m_previous_day1 returned all
    None for both today and tomorrow when actually queried, while the
    historical endpoint had complete, real data for the SAME current day
    when queried with start_date=end_date=today. The "historical" endpoint
    is, in practice, the reliable way to get this feature even for today's
    date -- not just genuinely old dates."""
    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "hourly": "temperature_2m_previous_day1",
        "models": model,
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
    }
    if client is not None:
        resp = client.get(BASE_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
    else:
        with httpx.Client(timeout=60) as owned_client:
            resp = owned_client.get(BASE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

    hourly = payload.get("hourly", {})
    forecasts = hourly.get("temperature_2m_previous_day1", [])
    day_values = [f for f in forecasts if f is not None]
    return max(day_values) if day_values else None


@_retry
def fetch_live_previous_day1_high_multi(
    models: list[str], target_date: date, client: httpx.Client | None = None
) -> dict[str, float]:
    """Same as fetch_live_previous_day1_high, for several models in ONE
    request instead of one request per model -- confirmed live that
    Open-Meteo accepts a comma-separated `models=` list and returns
    per-model fields (temperature_2m_previous_day1_<model>).

    Built after a real production incident (WEATHER_KALSHI_TECHNICAL_PLAN.md
    Sec 5g/5k): even with 429 marked retryable and a 1-second delay between
    calls, jobs/daily_prediction_job.py's original one-request-per-model
    approach still hit 429 on every single request on a live run, degrading
    the whole ensemble to NWS-only. Open-Meteo's own documented limit
    (600 req/min) is nowhere near what 5 sequential calls would trigger --
    the real fix is sending fewer requests in the first place, not retrying
    harder around a limit that isn't actually the documented one (likely a
    Render-IP-specific or shared free-tier throttle, not fully diagnosable
    from here). Returns only the models that had real (non-null) data --
    a model missing from the result dict means no data was available for
    it, not that the whole request failed."""
    params = {
        "latitude": NYC_LATITUDE,
        "longitude": NYC_LONGITUDE,
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "hourly": "temperature_2m_previous_day1",
        "models": ",".join(models),
        "temperature_unit": "fahrenheit",
        "timezone": "America/New_York",
    }
    if client is not None:
        resp = client.get(BASE_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
    else:
        with httpx.Client(timeout=60) as owned_client:
            resp = owned_client.get(BASE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

    hourly = payload.get("hourly", {})
    results: dict[str, float] = {}
    for model in models:
        forecasts = hourly.get(f"temperature_2m_previous_day1_{model}", [])
        day_values = [f for f in forecasts if f is not None]
        if day_values:
            results[model] = max(day_values)
    return results


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
