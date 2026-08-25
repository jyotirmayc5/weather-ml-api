"""Read-only Kalshi market data client. No API key/account needed -- verified
by hand (WEATHER_KALSHI_TECHNICAL_PLAN.md): reading markets/events/candlesticks
is public, only trading (orders/positions/balance) needs the signed-key auth.
Built ahead of Phase 3 for Phase 2 backtest validation specifically -- this is
NOT a trading client and does not place orders.

Base URL confirmed working by direct request, not assumed -- of several URLs
mentioned across third-party docs, only https://api.elections.kalshi.com and
https://external-api.kalshi.com actually responded; trading-api.kalshi.com
returned 401. Using the elections one.
"""
import re
from datetime import date

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_TICKER_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})$")


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
def _get(path: str, params: dict | None = None) -> dict:
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


def event_ticker_to_date(event_ticker: str) -> date:
    """'KXHIGHNY-26AUG20' -> date(2026, 8, 20). Raises ValueError if the
    ticker doesn't end in the expected YYMMMDD date suffix."""
    match = _TICKER_DATE_RE.search(event_ticker)
    if not match:
        raise ValueError(f"could not parse a date suffix from {event_ticker!r}")
    yy, mon, dd = match.groups()
    if mon not in _MONTHS:
        raise ValueError(f"unrecognized month abbreviation {mon!r} in {event_ticker!r}")
    return date(2000 + int(yy), _MONTHS[mon], int(dd))


def fetch_settled_events(series_ticker: str, min_date: date | None = None) -> list[dict]:
    """Paginates through ALL settled events for a series (cursor-based),
    optionally stopping once events are older than min_date (events come back
    newest-first, so this can stop early rather than paginating the entire
    series history)."""
    events = []
    cursor = None
    while True:
        params = {"series_ticker": series_ticker, "status": "settled", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = _get("/events", params=params)
        page_events = page.get("events", [])
        if not page_events:
            break
        for event in page_events:
            try:
                event_date = event_ticker_to_date(event["event_ticker"])
            except ValueError:
                continue
            if min_date and event_date < min_date:
                return events
            events.append(event)
        cursor = page.get("cursor")
        if not cursor:
            break
    return events


def get_settlement_value(event_ticker: str) -> float | None:
    """The actual settled temperature for a daily-high/low event, taken from
    the first market's expiration_value (all bucket markets within one event
    share the same expiration_value -- only the strike differs)."""
    data = _get(f"/events/{event_ticker}", params={"with_nested_markets": "true"})
    markets = data.get("event", {}).get("markets", [])
    if not markets:
        return None
    value = markets[0].get("expiration_value")
    return float(value) if value else None
