from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings


def make_scheduler(settings: Settings, job: Callable[[], None]) -> BackgroundScheduler:
    """Build a BackgroundScheduler that fires ``job`` on the configured cron.

    The scheduler is returned *not started*; the caller decides when to
    ``start()`` it (the FastAPI app starts it on the ``startup`` event unless
    running under tests). The cron expression and timezone come straight from
    ``Settings`` so dates align with the NYT rollover in ``schedule_tz``.
    """
    scheduler = BackgroundScheduler(timezone=settings.schedule_tz)
    trigger = CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.schedule_tz)
    scheduler.add_job(job, trigger=trigger, id="daily-cycle", replace_existing=True)
    return scheduler
