from core.database import SessionLocal

from .collector import SystemSnapshot, system_collector
from .models import SystemMetric


def get_info():
    return system_collector.get_info()

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

