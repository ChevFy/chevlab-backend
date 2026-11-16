from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import settings

from ..planner.reminders.job import scan_due_reminders
from ..planner.service import clean_fired_reminders
from ..system.service import clean_system_metric, collect_and_store

# AsyncIOScheduler awaits coroutine jobs on the app's event loop
scheduler = AsyncIOScheduler(timezone="Asia/Bangkok")


def scheduler_store_system_metric():
    scheduler.add_job(
        collect_and_store,
        "interval",
        seconds=5,
        id="store_system_metric",
        replace_existing=True,
    )


def scheduler_clean_system_metric():
    scheduler.add_job(
        clean_system_metric,
        "interval",
        days=1,
        next_run_time=datetime.now(scheduler.timezone),
        id="clean_system_metric",
        replace_existing=True,
    )


def scheduler_scan_reminders():
    # Fires due reminders; also picks up missed ones on the first run after a
    # restart (durable + catch-up, FR-33/34). Runs on the shared event loop.
    scheduler.add_job(
        scan_due_reminders,
        "interval",
        seconds=settings.REMINDER_SCAN_INTERVAL_S,
        next_run_time=datetime.now(scheduler.timezone),
        id="scan_reminders",
        replace_existing=True,
    )


def scheduler_clean_reminders():
    scheduler.add_job(
        clean_fired_reminders,
        "interval",
        days=1,
        next_run_time=datetime.now(scheduler.timezone),
        id="clean_reminders",
        replace_existing=True,
    )
