from datetime import date
from unittest.mock import patch

import httpx
import pytest

from src.kalshi.client import (
    _first_nonempty_expiration_value,
    date_to_ticker_suffix,
    event_ticker_to_date,
    fetch_candlesticks,
    fetch_event_markets,
    fetch_open_event,
    get_settlement_value,
    market_prob_near_time,
)


def test_parses_standard_ticker():
    assert event_ticker_to_date("KXHIGHNY-26AUG20") == date(2026, 8, 20)


def test_parses_single_digit_day_with_leading_zero():
    assert event_ticker_to_date("KXHIGHNY-26AUG05") == date(2026, 8, 5)


def test_parses_different_month():
    assert event_ticker_to_date("KXHIGHNY-26MAY25") == date(2026, 5, 25)


def test_raises_on_unparseable_ticker():
    with pytest.raises(ValueError):
        event_ticker_to_date("KXHIGHNY-INVALID")


def test_raises_on_bad_month_abbreviation():
    with pytest.raises(ValueError):
        event_ticker_to_date("KXHIGHNY-26XXX20")


def test_first_nonempty_expiration_value_skips_empty_strings():
    # Kalshi can return expiration_value="" (empty string, not null) --
    # float("") raises ValueError, which crashed a full historical comparison
    # run before this was caught.
    markets = [{"expiration_value": ""}, {"expiration_value": ""}, {"expiration_value": "77.00"}]
    assert _first_nonempty_expiration_value(markets) == 77.0


def test_first_nonempty_expiration_value_all_empty_returns_none():
    assert _first_nonempty_expiration_value([{"expiration_value": ""}, {"expiration_value": ""}]) is None


def test_get_settlement_value_treats_empty_string_as_none():
    fake_response = {"event": {"markets": [{"expiration_value": ""}]}}
    with patch("src.kalshi.client._get", return_value=fake_response):
        assert get_settlement_value("KXHIGHNY-26AUG01") is None


def test_get_settlement_value_parses_real_value():
    fake_response = {"event": {"markets": [{"expiration_value": "84.00"}]}}
    with patch("src.kalshi.client._get", return_value=fake_response):
        assert get_settlement_value("KXHIGHNY-26AUG20") == 84.0


def test_get_settlement_value_only_the_winning_market_has_a_value():
    # Regression for the real bug: only 67 of 1841 known settled events were
    # recovered on the first full backfill attempt, because only the market
    # that actually resolved "yes" carries expiration_value on live-tier
    # events with multiple bucket markets -- the old code only checked
    # markets[0], which is frequently a losing bucket with an empty value.
    fake_response = {
        "event": {
            "markets": [
                {"expiration_value": "", "result": "no"},
                {"expiration_value": "", "result": "no"},
                {"expiration_value": "53.00", "result": "yes"},
                {"expiration_value": "", "result": "no"},
            ]
        }
    }
    with patch("src.kalshi.client._get", return_value=fake_response):
        assert get_settlement_value("KXHIGHNY-24DEC31") == 53.0


def test_get_settlement_value_falls_back_to_historical_endpoint():
    # Historical-tier events (older than Kalshi's live/historical cutoff)
    # return an empty markets list from the regular /events endpoint --
    # confirmed against the real API for a 2024 event. Must fall back to
    # /historical/markets?event_ticker=... instead of returning None outright.
    live_response = {"event": {"markets": []}}
    historical_response = {
        "cursor": "",
        "markets": [{"expiration_value": "", "result": "no"}, {"expiration_value": "53.00", "result": "yes"}],
    }
    with patch("src.kalshi.client._get", side_effect=[live_response, historical_response]) as mock_get:
        assert get_settlement_value("KXHIGHNY-24DEC31") == 53.0
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[1].kwargs["params"] == {"event_ticker": "KXHIGHNY-24DEC31"}


def test_get_settlement_value_no_value_in_either_tier_returns_none():
    live_response = {"event": {"markets": []}}
    historical_response = {"cursor": "", "markets": []}
    with patch("src.kalshi.client._get", side_effect=[live_response, historical_response]):
        assert get_settlement_value("KXHIGHNY-24DEC31") is None


def test_date_to_ticker_suffix_is_the_inverse_of_event_ticker_to_date():
    d = date(2026, 9, 5)
    suffix = date_to_ticker_suffix(d)
    assert suffix == "26SEP05"
    assert event_ticker_to_date(f"KXHIGHNY-{suffix}") == d


def test_date_to_ticker_suffix_pads_single_digit_day():
    assert date_to_ticker_suffix(date(2026, 8, 5)) == "26AUG05"


def test_fetch_event_markets_uses_live_tier_when_available():
    fake_response = {"event": {"markets": [{"ticker": "KXHIGHNY-26AUG20-T78"}]}}
    with patch("src.kalshi.client._get", return_value=fake_response) as mock_get:
        markets = fetch_event_markets("KXHIGHNY", date(2026, 8, 20))
    assert markets == [{"ticker": "KXHIGHNY-26AUG20-T78"}]
    mock_get.assert_called_once_with(
        "/events/KXHIGHNY-26AUG20", params={"with_nested_markets": "true"}, client=None
    )


def test_fetch_event_markets_falls_back_to_historical_tier():
    live_response = {"event": {"markets": []}}
    historical_response = {"cursor": "", "markets": [{"ticker": "KXHIGHNY-24DEC31-T57"}]}
    with patch("src.kalshi.client._get", side_effect=[live_response, historical_response]) as mock_get:
        markets = fetch_event_markets("KXHIGHNY", date(2024, 12, 31))
    assert markets == [{"ticker": "KXHIGHNY-24DEC31-T57"}]
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[1].kwargs["params"] == {"event_ticker": "KXHIGHNY-24DEC31"}


def test_fetch_candlesticks_builds_correct_url_and_params():
    fake_response = {"candlesticks": [{"end_period_ts": 100}]}
    with patch("src.kalshi.client._get", return_value=fake_response) as mock_get:
        candles = fetch_candlesticks("KXHIGHNY", "KXHIGHNY-26AUG20-T78", 100, 200, period_interval=1)
    assert candles == [{"end_period_ts": 100}]
    mock_get.assert_called_once_with(
        "/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-T78/candlesticks",
        params={"start_ts": 100, "end_ts": 200, "period_interval": 1},
        client=None,
    )


def test_market_prob_near_time_picks_closest_candle_and_computes_mid():
    candles = [
        {"end_period_ts": 100, "yes_bid": {"close_dollars": "0.10"}, "yes_ask": {"close_dollars": "0.20"}},
        {"end_period_ts": 500, "yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.60"}},
    ]
    # target=550 is closer to the second candle (500) than the first (100)
    assert market_prob_near_time(candles, target_ts=550) == pytest.approx(0.5)


def test_market_prob_near_time_empty_candles_returns_none():
    assert market_prob_near_time([], target_ts=100) is None


def test_fetch_candlesticks_404_returns_empty_list_not_an_error():
    # Real behavior confirmed live: candlestick retention is its own shorter
    # window than the settlement live/historical-tier split -- a market old
    # enough that fetch_event_markets still finds it fine can still 404 here.
    # That's a legitimate "no data retained", not something callers should
    # crash on.
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(404, request=request, json={"error": {"message": "not found"}})
    with patch(
        "src.kalshi.client._get",
        side_effect=httpx.HTTPStatusError("404", request=request, response=response),
    ):
        assert fetch_candlesticks("KXHIGHNY", "KXHIGHNY-26MAY25-T79", 100, 200) == []


def test_fetch_candlesticks_reraises_non_404_errors():
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(500, request=request)
    with patch(
        "src.kalshi.client._get",
        side_effect=httpx.HTTPStatusError("500", request=request, response=response),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            fetch_candlesticks("KXHIGHNY", "KXHIGHNY-26MAY25-T79", 100, 200)


def test_fetch_open_event_builds_correct_ticker_and_returns_event():
    fake_response = {"event": {"event_ticker": "KXHIGHNY-26SEP05", "markets": [{"ticker": "x"}]}}
    with patch("src.kalshi.client._get", return_value=fake_response) as mock_get:
        event = fetch_open_event("KXHIGHNY", date(2026, 9, 5))
    assert event == fake_response["event"]
    mock_get.assert_called_once_with(
        "/events/KXHIGHNY-26SEP05", params={"with_nested_markets": "true"}, client=None
    )
