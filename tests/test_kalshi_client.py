from datetime import date
from unittest.mock import patch

import pytest

from src.kalshi.client import event_ticker_to_date, get_settlement_value


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


def test_get_settlement_value_treats_empty_string_as_none():
    # regression: Kalshi's API can return expiration_value="" (empty string,
    # not null) for some events -- float("") raises ValueError, which crashed
    # the full historical comparison run before this was caught.
    fake_response = {"event": {"markets": [{"expiration_value": ""}]}}
    with patch("src.kalshi.client._get", return_value=fake_response):
        assert get_settlement_value("KXHIGHNY-26AUG01") is None


def test_get_settlement_value_parses_real_value():
    fake_response = {"event": {"markets": [{"expiration_value": "84.00"}]}}
    with patch("src.kalshi.client._get", return_value=fake_response):
        assert get_settlement_value("KXHIGHNY-26AUG20") == 84.0
