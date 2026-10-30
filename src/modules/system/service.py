from datetime import datetime, timedelta, timezone

from core.database import SessionLocal

from .collector import SystemSnapshot, system_collector
from .models import SystemMetric
from sqlalchemy import delete, select


def read_info():
    return system_collector.read_info()


def _map_snapshot(snapshot: SystemSnapshot):
    return SystemMetric(
        timestamp=snapshot.timestamp,
        cpu_percent=snapshot.cpu_percent,
        memory_percent=snapshot.memory_percent,
        swap_percent=snapshot.swap_percent,
        disk_percent=snapshot.disk_percent,
        cpu_temp=snapshot.cpu_temp,
        net_recv_bytes_per_sec=snapshot.net_recv_bytes_per_sec,
        net_sent_bytes_per_sec=snapshot.net_sent_bytes_per_sec,
    )


async def collect_and_store():
    snapshot = system_collector.get_current()
    async with SessionLocal() as session:
        session.add(_map_snapshot(snapshot))
        await session.commit()
    return snapshot


async def read_latest():
    async with SessionLocal() as session:
        return await session.scalar(
            select(SystemMetric).order_by(SystemMetric.timestamp.desc()).limit(1)
        )


async def read_history(hours: int):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with SessionLocal() as session:
        result = await session.scalars(
            select(SystemMetric)
            .where(SystemMetric.timestamp >= cutoff)
            .order_by(SystemMetric.timestamp.asc())
        )
        return result.all()


async def clean_system_metric(retention_days: int = 7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with SessionLocal() as session:
        result = await session.execute(
            delete(SystemMetric).where(SystemMetric.timestamp < cutoff)
        )
        await session.commit()
    return result.rowcount