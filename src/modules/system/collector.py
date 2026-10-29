import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import psutil


@dataclass(frozen=True)
class SystemInfo:
    hostname: str
    os_name: str
    os_release: str
    cpu_model: str | None
    cpu_cores: int | None
    cpu_threads: int | None
    memory_total: int
    boot_time: datetime


@dataclass()
class SystemSnapshot:
    timestamp: datetime
    uptime_seconds: int

    cpu_percent: float
    cpu_percent_per_core: list[float]

    memory_used: int
    memory_available: int
    memory_total: int
    memory_percent: float

    swap_used: int
    swap_total: int
    swap_percent: float

    disk_used: int
    disk_total: int
    disk_percent: float

    cpu_temp: float | None

    net_recv_bytes_per_sec: float | None
    net_sent_bytes_per_sec: float | None


class SystemCollector:
    def __init__(self):
        self.info: SystemInfo | None = None
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._last_net = psutil.net_io_counters()
        self._last_net_time = time.monotonic()
        self._has_net_baseline = False

    def get_info(self):
        if self.info is None:
            self.info = SystemInfo(
                hostname=platform.node(),
                os_name=platform.system(),
                os_release=platform.release(),
                cpu_model=self._read_cpu(),
                cpu_cores=psutil.cpu_count(logical=False),
                cpu_threads=psutil.cpu_count(logical=True),
                memory_total=psutil.virtual_memory().total,
                boot_time=datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc),
            )
        return self.info

    def get_current(self):
        recv_rate, sent_rate = self._read_network_rate()
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        return SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            uptime_seconds=int(time.time() - psutil.boot_time()),
            cpu_percent=psutil.cpu_percent(interval=None),
            cpu_percent_per_core=psutil.cpu_percent(interval=None, percpu=True),
            memory_used=mem.used,
            memory_available=mem.available,
            memory_total=mem.total,
            memory_percent=mem.percent,
            swap_used=swap.used,
            swap_total=swap.total,
            swap_percent=swap.percent,
            disk_used=disk.used,
            disk_total=disk.total,
            disk_percent=disk.percent,
            cpu_temp=self._read_cpu_temp(),
            net_recv_bytes_per_sec=recv_rate,
            net_sent_bytes_per_sec=sent_rate,
        )

    @staticmethod
    def _read_cpu():
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        cpu_model = line.split(":", 1)[1].strip()
                        return cpu_model
        except OSError:
            pass
        return platform.processor() or None

    @staticmethod
    def _read_cpu_temp():
        if not hasattr(psutil, "sensors_temperatures"):
            return None
        try:
            sensors = psutil.sensors_temperatures()
        except (OSError, RuntimeError):
            return None
        if not sensors:
            return None

        for name in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            entries = sensors.get(name)
            if not entries:
                continue

            for entry in entries:
                if entry.label in ("Package id 0", "Tctl", "Tdie"):
                    return round(entry.current, 1)
            return round(max(e.current for e in entries), 1)
        return None

    def _read_network_rate(self):
        now = time.monotonic()
        current = psutil.net_io_counters()
        elapsed = now - self._last_net_time

        prev = self._last_net
        self._last_net = current
        self._last_net_time = now

        if not self._has_net_baseline or elapsed <= 0:
            self._has_net_baseline = True
            return None, None

        recv = max(current.bytes_recv - prev.bytes_recv, 0) / elapsed
        sent = max(current.bytes_sent - prev.bytes_sent, 0) / elapsed
        return round(recv, 1), round(sent, 1)


system_collector = SystemCollector()
