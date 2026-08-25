"""job_runs tracking, per WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 3 -- every job
writes a row on start and on finish/failure. Only visibility into cron health
once n8n's execution log is gone."""
from contextlib import contextmanager
from datetime import datetime, timezone

from src.db.models import JobRun


@contextmanager
def track_job_run(session, job_name: str):
    run = JobRun(job_name=job_name, started_at=datetime.now(timezone.utc), status="running")
    session.add(run)
    session.commit()
    try:
        yield run
        run.status = "success"
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
