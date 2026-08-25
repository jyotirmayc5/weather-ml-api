from datetime import date
from unittest.mock import patch

from sqlalchemy import select

import jobs.kalshi_settlement_job as kalshi_settlement_job
from src.db.models import JobRun, KalshiSettlement


def _fake_event(day: str) -> dict:
    return {"event_ticker": f"KXHIGHNY-99{day}"}


def test_stores_settlement_values_for_settled_events(plain_session):
    events = [_fake_event("JAN01"), _fake_event("JAN02")]

    def fake_settlement(event_ticker):
        return {"KXHIGHNY-99JAN01": 71.0, "KXHIGHNY-99JAN02": 68.0}[event_ticker]

    with (
        patch.object(kalshi_settlement_job, "get_session", lambda: plain_session),
        patch.object(kalshi_settlement_job, "fetch_settled_events", return_value=events),
        patch.object(kalshi_settlement_job, "get_settlement_value", side_effect=fake_settlement),
    ):
        stored = kalshi_settlement_job.run()

    assert stored == 2
    rows = plain_session.scalars(
        select(KalshiSettlement).where(KalshiSettlement.target_date >= date(2099, 1, 1))
    ).all()
    assert {(r.target_date, float(r.settled_value_f)) for r in rows} == {
        (date(2099, 1, 1), 71.0),
        (date(2099, 1, 2), 68.0),
    }

    job_run = plain_session.scalars(
        select(JobRun).where(JobRun.job_name == "kalshi_settlement_job")
    ).first()
    assert job_run.status == "success"


def test_rerunning_does_not_duplicate_rows(plain_session):
    events = [_fake_event("FEB01")]

    with (
        patch.object(kalshi_settlement_job, "get_session", lambda: plain_session),
        patch.object(kalshi_settlement_job, "fetch_settled_events", return_value=events),
        patch.object(kalshi_settlement_job, "get_settlement_value", return_value=65.0),
    ):
        kalshi_settlement_job.run()
        kalshi_settlement_job.run()

    rows = plain_session.scalars(
        select(KalshiSettlement).where(KalshiSettlement.target_date == date(2099, 2, 1))
    ).all()
    assert len(rows) == 1


def test_skips_events_with_no_settlement_value_yet(plain_session):
    events = [_fake_event("MAR01")]

    with (
        patch.object(kalshi_settlement_job, "get_session", lambda: plain_session),
        patch.object(kalshi_settlement_job, "fetch_settled_events", return_value=events),
        patch.object(kalshi_settlement_job, "get_settlement_value", return_value=None),
    ):
        stored = kalshi_settlement_job.run()

    assert stored == 0
