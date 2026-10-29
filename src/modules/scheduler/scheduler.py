from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..system.service import collect_and_store

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
