"""Thin client for api.weather.gov. Returns raw parsed JSON exactly as NWS
sends it -- no reshaping here, that's what src/ingestion/normalize.py and
src/features/*.py are for (and they're already tested against real captured
shapes; feeding them this client's output keeps that fidelity).

Deliberate deviation from a strict 1:1 port, flagged per
WEATHER_KALSHI_TECHNICAL_PLAN.md's "reproduce behavior, including bugs"
discipline: the real n8n HTTP Request nodes for these GET calls have no
retry configured at all (only the /predict POST node does, per the n8n
export). Adding retries here is a deliberate resilience improvement, not a
silent behavior change -- it only affects what happens on a transient
network/5xx failure, never the data values returned on success.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

NY = ZoneInfo("America/New_York")
BASE_URL = "https://api.weather.gov"
USER_AGENT = "weather-ml-api (https://github.com/jyotirmayc5/weather-ml-api)"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # 403 confirmed real and transient, not a block: latest_observations_job
        # hit it twice in one week (2026-10-02, 2026-10-05), both isolated
        # single-tick failures with the ticks immediately before/after
        # succeeding normally, and the same request (same User-Agent, same
        # station) succeeded immediately when retested by hand. Other 4xx
        # (404, etc.) are left alone -- those mean a real client error, not an
        # NWS-side blip.
        return exc.response.status_code >= 500 or exc.response.status_code == 403
    return False


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    # Widened 2026-10-08: the original 3-attempt/~10s-total window (added for
    # the 403 fix above) didn't actually stop the failures -- WEATHER_KALSHI_
    # TECHNICAL_PLAN.md logs 8 more isolated 403s in the 2 days right after
    # that fix shipped, each one still a single-tick blip surrounded by
    # successes, meaning NWS's own blip outlasts ~10s of retrying. 6
    # attempts with a longer backoff (~50s total before giving up) stays
    # well under the 15-minute cron interval while giving a real blip more
    # room to clear before this job gives up and waits for its next tick.
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)


@_retry
def _get(path: str, params: dict | None = None) -> dict:
    with httpx.Client(base_url=BASE_URL, headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


def fetch_gridpoint_forecast(office: str, grid_x: int, grid_y: int) -> dict:
    """GET /gridpoints/{office}/{gridX},{gridY} -- the raw forecast payload
    consumed directly by normalize_nws_data() and forecast_high()."""
    return _get(f"/gridpoints/{office}/{grid_x},{grid_y}")


def fetch_latest_observation(station: str) -> dict:
    """GET /stations/{station}/observations/latest -- a single Feature."""
    return _get(f"/stations/{station}/observations/latest")


def fetch_day_observations(station: str, now: datetime) -> dict:
    """GET /stations/{station}/observations?start=...&end=..., bounded to the
    NY-local calendar day containing `now`. Matches the real n8n HTTP node's
    Luxon-based `$now.setZone('America/New_York').startOf('day').toUTC()...`
    expression -- that part of the workflow uses n8n's built-in date
    expressions, not hand-rolled JS Date math, so unlike Forecast HIGH's
    lead_hours it's already DST-safe and doesn't need a "faithful bug" port,
    just a correct one."""
    ny_now = now.astimezone(NY)
    start_ny = datetime(ny_now.year, ny_now.month, ny_now.day, 0, 0, 0, tzinfo=NY)
    end_ny = datetime(ny_now.year, ny_now.month, ny_now.day, 23, 59, 59, tzinfo=NY)
    start_utc = start_ny.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end_ny.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _get(f"/stations/{station}/observations", params={"start": start_utc, "end": end_utc})
