from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from ..system.service import collect_and_store, clean_system_metric

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
