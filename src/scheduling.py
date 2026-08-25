"""DST-safe scheduling guard for jobs that need to run at a specific NY-local
clock time (daily_high_forecast, corrected_high_update, actual_high_update).
Render Cron Jobs schedules are UTC-only with no IANA timezone option, unlike
n8n's DST-aware `America/New_York` workflow setting -- so a fixed UTC cron
expression would silently fire an hour off from the intended ET time for the
~4 months a year NY is on EST instead of EDT.

Fix: schedule the job to fire at BOTH possible UTC times every day (comma-
separated hours in the cron expression, e.g. "45 13,14 * * *"), and have the
job itself check whether it's actually the intended NY-local time before
doing any work. Only one of the two daily ticks will ever be in-window; the
other no-ops harmlessly. No twice-yearly manual cron updates needed."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def in_ny_time_window(target_hour: int, target_minute: int, *, tolerance_minutes: int = 10, now=None) -> bool:
    """True if `now` (NY-local) is within `tolerance_minutes` of target_hour:
    target_minute. Checks yesterday/today/tomorrow's occurrence of that clock
    time, not just today's -- actual_high_update_job's 23:55 target is close
    enough to midnight that a few minutes of execution delay could push the
    check past midnight, where a naive "today's 23:55" comparison would be
    off by nearly a full day instead of the few real minutes involved."""
    now_ny = (now or datetime.now(NY)).astimezone(NY)
    same_day_target = now_ny.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    candidates = [same_day_target, same_day_target - timedelta(days=1), same_day_target + timedelta(days=1)]
    closest_diff = min(abs((now_ny - c).total_seconds()) for c in candidates)
    return closest_diff <= tolerance_minutes * 60
