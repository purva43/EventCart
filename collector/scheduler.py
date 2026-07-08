from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from .collector_logic import CollectorConfig, collector_job, set_config

_scheduler = None


def start_or_update_scheduler(config: CollectorConfig):
    global _scheduler
    set_config(config)
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            collector_job,
            "interval",
            minutes=config.interval_minutes,
            next_run_time=datetime.now(timezone.utc),
            id="collector_job",
            replace_existing=True,
        )
        _scheduler.start()
    else:
        _scheduler.reschedule_job("collector_job", trigger="interval", minutes=config.interval_minutes)
    return _scheduler


def is_scheduler_running():
    return _scheduler is not None and _scheduler.running


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
