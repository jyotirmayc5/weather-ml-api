"""Tests for src/ingestion/nws_client.py.

Two kinds: live tests against the real api.weather.gov (network required --
these double as end-to-end checks that the client's raw output is actually
compatible with the already-tested transform functions), and mocked tests for
retry/backoff and URL-building logic that shouldn't depend on the network or
NWS's current data.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from src.features.actuals import eod_actuals_and_pressure
from src.features.daily_features import forecast_high
from src.ingestion.normalize import normalize_nws_data, return_observations
from src.ingestion.nws_client import (
    fetch_day_observations,
    fetch_gridpoint_forecast,
    fetch_latest_observation,
)


# ---- live tests: real network, real NWS data --------------------------------


def test_fetch_gridpoint_forecast_shape_is_compatible_with_normalize():
    payload = fetch_gridpoint_forecast("OKX", 33, 37)
    assert "properties" in payload
    assert "values" in payload["properties"]["temperature"]
    # doesn't need to produce non-empty output right now (depends on the
    # actual time of day this test runs) -- just needs to not raise.
    normalize_nws_data(payload, datetime.now(timezone.utc))


def test_fetch_gridpoint_forecast_shape_is_compatible_with_forecast_high():
    payload = fetch_gridpoint_forecast("OKX", 33, 37)
    result = forecast_high(payload, datetime.now(timezone.utc))
    assert result["station"] == "KNYC"
    assert result["source"] == "NWS OKX/33,37"


def test_fetch_latest_observation_returns_a_single_feature():
    payload = fetch_latest_observation("KNYC")
    assert "properties" in payload
    assert "temperature" in payload["properties"]


def test_fetch_latest_observation_across_all_5_stations_feeds_return_observations():
    items = [fetch_latest_observation(s) for s in ["KNYC", "KLGA", "KJFK", "KEWR", "KTEB"]]
    rows = return_observations(items)
    assert [r["station"] for r in rows] == ["KNYC", "KLGA", "KJFK", "KEWR", "KTEB"]
    for row in rows:
        assert row["observed_time"] is not None


def test_fetch_day_observations_shape_is_compatible_with_eod_actuals():
    now = datetime.now(timezone.utc)
    payload = fetch_day_observations("KNYC", now)
    assert "features" in payload
    if payload["features"]:
        # only meaningful once at least one observation exists for today
        eod_actuals_and_pressure(payload, now)


# ---- mocked tests: URL-building and retry behavior, no network needed -------


def test_fetch_day_observations_uses_ny_calendar_day_bounds():
    captured = {}

    def fake_get(self, path, params=None):
        captured["path"] = path
        captured["params"] = params
        return httpx.Response(200, json={"features": []}, request=httpx.Request("GET", "http://x"))

    with patch.object(httpx.Client, "get", fake_get):
        # 2026-06-16T03:00:00Z is 2026-06-15 23:00 EDT -- still June 15 in NY
        fetch_day_observations("KNYC", datetime(2026, 6, 16, 3, 0, 0, tzinfo=timezone.utc))

    assert captured["path"] == "/stations/KNYC/observations"
    # NY midnight June 15 EDT (UTC-4) -> 04:00 UTC; NY 23:59:59 EDT -> 03:59:59 UTC June 16
    assert captured["params"]["start"] == "2026-06-15T04:00:00Z"
    assert captured["params"]["end"] == "2026-06-16T03:59:59Z"


def test_fetch_day_observations_across_dst_boundary():
    # 2026-03-08 is the US spring-forward day; NY is EST (-5) at local midnight
    # but EDT (-4) by 11:59pm the same day.
    captured = {}

    def fake_get(self, path, params=None):
        captured["params"] = params
        return httpx.Response(200, json={"features": []}, request=httpx.Request("GET", "http://x"))

    with patch.object(httpx.Client, "get", fake_get):
        fetch_day_observations("KNYC", datetime(2026, 3, 8, 15, 0, 0, tzinfo=timezone.utc))

    assert captured["params"]["start"] == "2026-03-08T05:00:00Z"  # midnight EST -> +5h
    assert captured["params"]["end"] == "2026-03-09T03:59:59Z"  # 23:59:59 EDT -> +4h


def test_retries_on_5xx_then_succeeds():
    responses = [
        httpx.Response(503, request=httpx.Request("GET", "http://x")),
        httpx.Response(503, request=httpx.Request("GET", "http://x")),
        httpx.Response(200, json={"properties": {}}, request=httpx.Request("GET", "http://x")),
    ]
    calls = {"n": 0}

    def fake_get(self, path, params=None):
        resp = responses[calls["n"]]
        calls["n"] += 1
        return resp

    with patch.object(httpx.Client, "get", fake_get), patch("time.sleep", lambda s: None):
        result = fetch_gridpoint_forecast("OKX", 33, 37)

    assert calls["n"] == 3
    assert result == {"properties": {}}


def test_does_not_retry_on_4xx():
    calls = {"n": 0}

    def fake_get(self, path, params=None):
        calls["n"] += 1
        return httpx.Response(404, request=httpx.Request("GET", "http://x"))

    with patch.object(httpx.Client, "get", fake_get):
        with pytest.raises(httpx.HTTPStatusError):
            fetch_gridpoint_forecast("OKX", 33, 37)

    assert calls["n"] == 1
