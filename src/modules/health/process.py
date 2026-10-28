import psutil, platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import subprocess


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
    memory_avalilable: int
    memory_total: int
    memory_percent: float

    swap_used: int
    swap_total: int
    swap_percent: float

    disk_used: int
    disk_total: int
    disk_percent: float

    cpu_temp: float | None


class SystemCollector:
    def __init__(self):
        self.info: SystemInfo | None
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    def get_info(self):
        info = SystemInfo(
            hostname=platform.node(),
            os_name=platform.system(),
            os_release=platform.release(),
            cpu_model=self._read_cpu(),
            cpu_cores=psutil.cpu_count(logical=False),
            cpu_threads=psutil.cpu_count(logical=True),
            memory_total=psutil.virtual_memory().total,
            boot_time=datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc),
        )
        print(info.cpu_model)

    def get_current(self):
        pass

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
        pass

    def _read_network():
        pass


test = SystemCollector()
test.get_info()
