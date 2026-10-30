import docker
from docker.errors import DockerException


def read_containers():
    client = docker.from_env()
    try:
        return [
            {
                "id": c.short_id,
                "name": c.name,
                "image": c.attrs["Config"]["Image"],
                "status": c.status,
                "health": c.attrs["State"].get("Health", {}).get("Status"),
                "started_at": c.attrs["State"]["StartedAt"],
                "ports": c.ports,
            }
            for c in client.containers.list()
        ]
    finally:
        client.close()
